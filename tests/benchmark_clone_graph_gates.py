#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j gates for the benchmark's PER-CLONE graph build under the D1 routing rule (ROUND-3 W8).

The documented G3 / Scenario A/B flow built every clone's graph into ONE shared Neo4j: `provision.py
setup` in the dev repo (which registers the dev repo on bolt 7687), then `deepmap_cli.py sync <clone>
--stale-cleanup` per repo, with the ON arm reading the dev repo's MCP servers on 8787/8788. Under D1 a
clone that is not its own DeepMap project has no instance: with the dev repo owning 7687 its sync
REFUSES, `--switch-project` no longer overrides that, and the `set -euo pipefail` driver aborted on the
first repo (review finding, reproduced hermetically — section A below).

The fix: each clone is built as its OWN project (`benchmark/clone_graph.py build` -> `provision.py setup
<clone>`: own Neo4j instance, own graph, own MCP servers) and the ON arm is pointed at THAT pair, per
command; `run_benchmark.do_run` verifies before spending that the ON-arm pair is the measured clone's own.

  A  the OLD driver step (`sync <clone>`, even with --switch-project) is refused under D1 — why it changed
  B  the NEW build step (provision's own routing seam) gives the clone its OWN Neo4j + MCP pairs while
     another project owns 7687 / 8787 — the real apply_ports_for, only sockets stubbed
  C  clone_graph.on_arm_problem: own pair -> ok; another project's pair -> FAIL CLOSED naming it; an
     unregistered clone -> strict FAIL / lenient warning; a garbage registry -> FAIL CLOSED
  D  run_benchmark.do_run fails closed BEFORE any spend when the ON arm points at another project's servers
     (and the check is load-bearing: without it the same setup spends)
  E  clone_graph CLI: build strips the routing env (a leftover DEEPMAP_*_PORT would hijack the next clone's
     setup), ports/stop/rm run the right commands
  F  the drivers + runbooks: no shared-graph sync, no global port export, per-clone build/ports/stop,
     strict ON-arm verification; the PRE-fix driver text is flagged (detectors have teeth). That text is a
     committed fixture (tests/fixtures/benchmark_pre_d1_drivers/), never `git show HEAD:`, because HEAD holds
     the FIXED drivers once the fix is committed (ROUND-11c C4)
  G  a CLEAN machine (no dev-repo setup, the runbooks' default): build claims a NON-base Neo4j pair for
     the clone before setup (the real provision seam keeps it), so `rm` -> instance-rm can remove it; a
     clone that already holds the base pair is released by `rm` (never the base store deleted) (R3 #4)
  H  `rm` / `stop` treat "nothing to stop" as success: `provision.py stop` exits 1 when it signalled nothing
     (no / stale pid file), which the runbooks' build -> stop -> rm flow always hits, so the LIVE state after
     the stop commands decides (a verified live DeepMap pid, the clone's ports listening), not exit codes;
     another project's store (F2) / server (owner sidecar) and an unmanaged 7687 listener never wedge rm;
     a failing probe keeps the claim (R4 review)
  I  that live verdict SETTLES: a just-signalled server can hold its port for a moment (Windows
     TerminateProcess is asynchronous), so while something still looks up it is re-checked for a BOUNDED
     grace (DEEPMAP_BENCH_STOP_GRACE_S) — no wait when nothing is up, never unbounded (R5 W8)

Hermetic: temp DEEPMAP_HOME + temp git repos, a socket tripwire (no real connection is possible), no
Neo4j, no claude, no spend; never touches ~/.deepmap or the committed .mcp.json.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build"
BENCH = REPO / "benchmark"
sys.path.insert(0, str(BUILD))

_PASS = 0
_FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {label}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}{(' — ' + detail) if detail else ''}")


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@contextlib.contextmanager
def hermetic_home():
    """A temp DEEPMAP_HOME, no NEO4J_URI / DEEPMAP_*_PORT / verify flag, and a SOCKET TRIPWIRE: every
    connect is refused, so every port probe answers "free" / "down" and nothing real is ever reached."""
    saved_env = dict(os.environ)
    saved = (socket.socket.connect, socket.socket.connect_ex, socket.create_connection)

    def _refuse(*a, **k):
        raise ConnectionRefusedError("benchmark_clone_graph_gates tripwire")

    td = tempfile.mkdtemp(prefix="dmbcg_")
    try:
        for k in ("NEO4J_URI", "DEEPMAP_SEARCH_PORT", "DEEPMAP_EMBED_PORT", "DEEPMAP_BENCH_VERIFY_ON_ARM",
                  "DEEPMAP_MCP_HOST"):
            os.environ.pop(k, None)
        os.environ["DEEPMAP_HOME"] = str(Path(td) / "home")
        # no settle wait by default: a "still running" verdict is instant (section I drives the wait itself)
        os.environ["DEEPMAP_BENCH_STOP_GRACE_S"] = "0"
        socket.socket.connect = _refuse
        socket.socket.connect_ex = lambda *a, **k: 111
        socket.create_connection = _refuse
        yield Path(td)
    finally:
        socket.socket.connect, socket.socket.connect_ex, socket.create_connection = saved
        os.environ.clear()
        os.environ.update(saved_env)
        shutil.rmtree(td, ignore_errors=True)


def _git_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(path)], capture_output=True, timeout=60)
    return path


def _register_dev(PR, dev: Path):
    """The dev repo set up first on a clean machine: bolt 7687/7474 + MCP 8787/8788."""
    mcp = PR.allocate_ports(dev)
    neo = PR.allocate_neo4j_ports(dev, base_adopt_ok=lambda: True)
    return mcp, neo


# ------------------------------------------------------------------------------------------------ A
def gate_old_path_refused() -> None:
    print("\n-- A: the OLD driver step (`sync <clone> --stale-cleanup`) is refused under D1 --")
    with hermetic_home() as td:
        import port_registry as PR
        dev, clone = _git_repo(td / "dev"), _git_repo(td / "clone")
        mcp, neo = _register_dev(PR, dev)
        check("setup: the dev repo holds the base pairs (7687/7474 + 8787/8788)",
              tuple(neo) == (7687, 7474) and tuple(mcp) == (8787, 8788), f"{mcp} {neo}")
        route = PR.resolve_neo4j_route(clone)
        check("the clone has NO route of its own while the dev repo owns 7687 (source 'none')",
              route.source == "none" and route.uri is None, repr(route))
        import sync as S
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            ok1 = S.sync(clone, cleanup_stale=True)
            ok2 = S.sync(clone, cleanup_stale=True, switch_project=True)
        check("`sync <clone> --stale-cleanup` returns False (the driver's set -e then aborted)", ok1 is False)
        check("...and --switch-project no longer overrides it (the old manual workaround is gone)", ok2 is False)
        check("the refusal names the missing instance", "no DeepMap Neo4j instance of its own" in err.getvalue(),
              err.getvalue()[-300:])


# ------------------------------------------------------------------------------------------------ B
def gate_new_build_routes_clone_to_its_own_instance() -> None:
    print("\n-- B: the NEW build step (provision's routing seam) gives the clone its OWN Neo4j + MCP pairs --")
    with hermetic_home() as td:
        import port_registry as PR
        import provision as PV
        dev, clone = _git_repo(td / "dev"), _git_repo(td / "clone")
        _register_dev(PR, dev)
        _pair, neo_problem, mcp_problem = PV._resolve_ports(str(clone), allocate=True)
        own_neo, own_mcp = PR.lookup_neo4j_ports(clone), PR.lookup_ports(clone)
        check("provision's setup seam routes the clone with NO problem", not neo_problem and not mcp_problem,
              f"{neo_problem} | {mcp_problem}")
        check("the clone gets its OWN Neo4j pair (not the dev repo's 7687/7474)",
              bool(own_neo) and tuple(own_neo) != (7687, 7474), str(own_neo))
        check("the clone gets its OWN MCP pair (not the dev repo's 8787/8788)",
              bool(own_mcp) and tuple(own_mcp) != (8787, 8788), str(own_mcp))
        check("...and injects THAT instance's bolt URI for the setup's sync + servers",
              os.environ.get("NEO4J_URI") == f"bolt://127.0.0.1:{(own_neo or (0,))[0]}", os.environ.get("NEO4J_URI"))  # ROUND-8 J4: provision generates bolt://127.0.0.1:<port>
        os.environ.pop("NEO4J_URI", None)   # a later CLI/sync process starts without setup's injection
        route = PR.resolve_neo4j_route(clone)
        check("afterwards the clone's own sync/CLI route (fresh process) is its own registered instance",
              route.source == "registry" and route.bolt == (own_neo or (None,))[0], repr(route))
        check("the dev repo keeps its own pairs untouched",
              PR.lookup_neo4j_ports(dev) == (7687, 7474) and PR.lookup_ports(dev) == (8787, 8788))


# ------------------------------------------------------------------------------------------------ C
def gate_on_arm_problem(CG) -> None:
    print("\n-- C: clone_graph.on_arm_problem — the ON arm must query the clone's OWN servers --")
    with hermetic_home() as td:
        import port_registry as PR
        dev, clone, lone = _git_repo(td / "dev"), _git_repo(td / "clone"), _git_repo(td / "lone")
        _register_dev(PR, dev)
        own = PR.allocate_ports(clone)
        env_own = {"DEEPMAP_SEARCH_PORT": str(own[0]), "DEEPMAP_EMBED_PORT": str(own[1])}
        check("ON arm on the clone's own pair -> ok", CG.on_arm_problem(clone, env=env_own) == (None, None))
        prob, _w = CG.on_arm_problem(clone, env={})
        check("ON arm on the default 8787/8788 (the dev repo's) -> FAIL CLOSED naming that project",
              bool(prob) and str(dev.resolve()).replace("\\", "/").lower()[-12:] in prob.replace("\\", "/").lower()
              and "FAIL CLOSED" in prob, str(prob))
        check("...and the fix is runnable as written (the clone's own ports to pass)",
              f"DEEPMAP_SEARCH_PORT={own[0]}" in (prob or "") and f"DEEPMAP_EMBED_PORT={own[1]}" in (prob or ""))
        prob, warn = CG.on_arm_problem(lone, env={}, strict=True)
        check("an UNREGISTERED clone under the drivers' strict flag -> FAIL CLOSED ('build it first')",
              bool(prob) and "clone_graph.py build" in prob, str(prob))
        prob, warn = CG.on_arm_problem(lone, env={"DEEPMAP_BENCH_VERIFY_ON_ARM": "1"})
        check("the strict flag is read from DEEPMAP_BENCH_VERIFY_ON_ARM=1", bool(prob))
        prob, warn = CG.on_arm_problem(lone, env={})
        check("an unregistered clone WITHOUT the flag -> a warning only (operator-managed layout)",
              prob is None and bool(warn) and "unverified" in warn, f"{prob} | {warn}")
        PR.registry_path().write_text(json.dumps({"kind": "something-else", "version": 99}), encoding="utf-8")
        prob, _w = CG.on_arm_problem(clone, env=env_own)
        check("a present-but-unrecognised registry -> FAIL CLOSED (D1: whose pair is whose is unknowable)",
              bool(prob) and "registry" in prob and "FAIL CLOSED" in prob, str(prob))


# ------------------------------------------------------------------------------------------------ D
def _stub_benchmark(B, calls):
    repos = [{"name": "o/clone", "url": "u", "pinned_sha": "a" * 40, "license": "MIT"}]
    questions = [{"id": "q1", "repo": "o/clone", "prompt": "p", "grep_can_do": False, "family": "f",
                  "answer_key": {"ground_truth_set": ["x"]}}]
    B.load_config = lambda: (repos, questions)
    B.validate_config = lambda r, q: []
    B._which = lambda name: "claude"
    B._server_files_present = lambda: True
    B._unreachable_mcp_servers = lambda: []
    B.write_on_mcp_config = lambda p: True
    B.assert_pinned_clean = lambda d, s: None
    B.assert_graph_at_pinned_sha = lambda d, s: None
    B.reset_pristine = lambda d, s: None

    def fake_run_one(arm, q, repo_dir, cfg, run_idx, out_dir, **kw):
        calls.append((arm, run_idx))
        return {"repo": q["repo"], "question_id": q["id"], "arm": arm, "run": run_idx, "exit_code": 0,
                "metrics": {k: None for k in B.METRIC_KEYS}, "scored": False}

    B.run_one = fake_run_one


def gate_do_run_fails_closed(Bpath: Path) -> None:
    print("\n-- D: run_benchmark.do_run refuses to spend when the ON arm is not the clone's own servers --")
    with hermetic_home() as td:
        import port_registry as PR
        dev = _git_repo(td / "dev")
        _register_dev(PR, dev)
        clone_root = td / "clones"
        clone = _git_repo(clone_root / "o__clone")
        own = PR.allocate_ports(clone)
        results = td / "results"
        results.mkdir()
        cases = (("ON arm on the dev repo's 8787/8788", {}, 2, False),
                 ("ON arm on the clone's own pair", {"DEEPMAP_SEARCH_PORT": str(own[0]),
                                                    "DEEPMAP_EMBED_PORT": str(own[1])}, 0, True))
        for label, env, want_rc, want_spend in cases:
            B = _load(Bpath, f"dm_bench_d_{want_rc}")
            B.RESULTS_ROOT = results
            calls: list = []
            _stub_benchmark(B, calls)
            os.environ.update(env)
            os.environ[B.ENGINE_ENV] = "1"
            err = io.StringIO()
            try:
                with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                    rc = B.do_run(SimpleNamespace(repo=None, clone_root=str(clone_root), max_budget_usd=1.0,
                                                  timeout_s=60, runs_per_arm=1), f"t_{want_rc}")
            finally:
                for k in env:
                    os.environ.pop(k, None)
            if want_spend:
                check(f"{label} -> the capture proceeds", rc == 0 and len(calls) > 0, f"rc={rc} calls={calls}")
            else:
                check(f"{label} -> rc 2 and ZERO runs (no tokens spent)", rc == 2 and not calls,
                      f"rc={rc} calls={calls}")
                check("...the refusal says whose servers they are and which pair to use",
                      "FAIL CLOSED" in err.getvalue() and f"DEEPMAP_SEARCH_PORT={own[0]}" in err.getvalue(),
                      err.getvalue()[-300:])
        # teeth: the SAME wrong-pair setup spends when the ownership check is removed
        B = _load(Bpath, "dm_bench_d_teeth")
        B.RESULTS_ROOT = results
        calls = []
        _stub_benchmark(B, calls)
        B.assert_on_arm_serves_repo = lambda d: None
        os.environ[B.ENGINE_ENV] = "1"
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            B.do_run(SimpleNamespace(repo=None, clone_root=str(clone_root), max_budget_usd=1.0, timeout_s=60,
                                     runs_per_arm=1), "t_teeth")
        check("teeth: without assert_on_arm_serves_repo the wrong-servers setup WOULD spend", len(calls) > 0)
        # strict: an unregistered clone is refused under the drivers' flag
        lone_root = td / "lone_clones"
        _git_repo(lone_root / "o__clone")
        B = _load(Bpath, "dm_bench_d_strict")
        B.RESULTS_ROOT = results
        calls = []
        _stub_benchmark(B, calls)
        os.environ["DEEPMAP_BENCH_VERIFY_ON_ARM"] = "1"
        try:
            with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
                rc = B.do_run(SimpleNamespace(repo=None, clone_root=str(lone_root), max_budget_usd=1.0,
                                              timeout_s=60, runs_per_arm=1), "t_strict")
        finally:
            os.environ.pop("DEEPMAP_BENCH_VERIFY_ON_ARM", None)
        check("DEEPMAP_BENCH_VERIFY_ON_ARM=1 + a clone never built as its own project -> rc 2, zero runs",
              rc == 2 and not calls, f"rc={rc} calls={calls}")


# ------------------------------------------------------------------------------------------------ E
def gate_cli(CG) -> None:
    print("\n-- E: clone_graph CLI — build strips the routing env; ports / stop / rm run the right commands --")
    with hermetic_home() as td:
        import port_registry as PR
        clone = _git_repo(td / "clone")
        seen = []

        def runner(argv, env):
            seen.append((list(argv), dict(env)))
            if argv[2:3] == ["setup"]:
                PR.allocate_ports(clone)          # what a successful setup leaves behind
            return 0

        os.environ.update({"NEO4J_URI": "bolt://localhost:7687", "DEEPMAP_SEARCH_PORT": "8787",
                           "DEEPMAP_EMBED_PORT": "8788", "DEEPMAP_PRECISE_CALLS": "1"})
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            rc = CG.main(["build", str(clone)], runner=runner)
        argv, env = seen[0]
        check("build runs `provision.py setup <clone>`", rc == 0 and argv[1].endswith("provision.py")
              and argv[2:] == ["setup", str(clone.resolve())], str(argv))
        check("build drops NEO4J_URI / DEEPMAP_SEARCH_PORT / DEEPMAP_EMBED_PORT for the setup child",
              not any(k in env for k in ("NEO4J_URI", "DEEPMAP_SEARCH_PORT", "DEEPMAP_EMBED_PORT")), str(sorted(env))[:200])
        check("...but keeps an operator's experiment knob (DEEPMAP_PRECISE_CALLS)", env.get("DEEPMAP_PRECISE_CALLS") == "1")
        for k in ("NEO4J_URI", "DEEPMAP_SEARCH_PORT", "DEEPMAP_EMBED_PORT", "DEEPMAP_PRECISE_CALLS"):
            os.environ.pop(k, None)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = CG.main(["ports", str(clone)])
        pair = PR.lookup_ports(clone)
        check("ports prints the clone's own pair", rc == 0 and out.getvalue().split() == [str(pair[0]), str(pair[1])],
              out.getvalue())
        other = _git_repo(td / "other")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            rc = CG.main(["ports", str(other)])
        check("ports on a clone never built -> exit 2 (the driver dies instead of guessing 8787)", rc == 2)
        seen.clear()
        with contextlib.redirect_stdout(io.StringIO()):
            CG.main(["stop", str(clone)], runner=runner)
        check("stop runs mcp-stop then stop for THAT clone",
              [a[2:] for a, _ in seen] == [["mcp-stop", str(clone.resolve())], ["stop", str(clone.resolve())]],
              str([a[2:] for a, _ in seen]))
        seen.clear()
        with contextlib.redirect_stdout(io.StringIO()):
            CG.main(["rm", str(clone)], runner=runner)
        check("rm ends with instance-rm --key <the clone's key> --purge --yes",
              seen and seen[-1][0][2:] == ["instance-rm", "--key", PR.project_key(clone), "--purge", "--yes"],
              str(seen[-1][0] if seen else None))
        seen.clear()
        failing = lambda argv, env: 1   # noqa: E731
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            rc = CG.main(["build", str(other)], runner=failing)
        check("a failed setup -> non-zero (the driver dies: never measure a clone without its graph)", rc != 0)


# ------------------------------------------------------------------------------------------------ F
def _code_lines(text: str):
    return [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]


def driver_problems(text: str) -> "list[str]":
    """What a benchmark driver must never do under D1 (pure — applied to the fixed AND the pre-fix text)."""
    code = "\n".join(_code_lines(text))
    probs = []
    if re.search(r"deepmap_cli\.py\s+sync\b", code):
        probs.append("syncs a clone into a shared graph (deepmap_cli.py sync)")
    if re.search(r"provision\.py\s+mcp-start\b", code):
        probs.append("starts the dev repo's servers for the ON arm (provision.py mcp-start)")
    if re.search(r"^\s*export\s+[^\n]*DEEPMAP_(SEARCH|EMBED)_PORT", code, re.MULTILINE):
        probs.append("exports DEEPMAP_*_PORT (leaks into the next clone's setup)")
    if "clone_graph.py build" not in code:
        probs.append("never builds the clone as its own project (clone_graph.py build)")
    if "clone_graph.py ports" not in code:
        probs.append("never reads the clone's own MCP pair (clone_graph.py ports)")
    capture = re.search(r"DEEPMAP_SEARCH_PORT=\"\$SP\"\s+DEEPMAP_EMBED_PORT=\"\$EP\"\s+DEEPMAP_BENCH_VERIFY_ON_ARM=1"
                        r"\s*\\?\s*\n?\s*python benchmark/run_benchmark\.py --repo", code)
    if not capture:
        probs.append("the capture is not pointed at the clone's pair with strict ON-arm verification")
    if "clone_graph.py stop" not in code:
        probs.append("never stops the clone's servers + Neo4j after its capture")
    return probs


def runbook_problems(text: str) -> "list[str]":
    fences = "\n".join(re.findall(r"```bash\n(.*?)```", text, re.DOTALL))
    code = "\n".join(_code_lines(fences))
    probs = []
    if re.search(r"deepmap_cli\.py\s+sync\s+\"\$(D|CLONES)", code):
        probs.append("a code block syncs a clone into the shared graph")
    if re.search(r"provision\.py\s+mcp-start", code):
        probs.append("a code block starts the dev repo's servers for the ON arm")
    if "clone_graph.py build" not in code:
        probs.append("no code block builds the clone as its own project")
    return probs


# The PRE-fix (v0.27.4, commit 9c8d50bce) drivers, verbatim, as committed FIXTURES: the teeth below must not read
# the git state. They used `git show HEAD:<driver>`, which is the pre-fix text only while HEAD is v0.27.4. Once
# this batch is committed, HEAD holds the FIXED driver (its comments still mention the old `deepmap_cli.py sync`
# step), so the teeth read the fixed text and went RED 92/94 on every committed checkout, CI's depth-1 checkout
# included. Without git (a tarball) they silently vanished. ROUND-11c C4; the fixtures' provenance (their
# git blob ids == 9c8d50bce:<driver>) is locked by tests/benchmark_clone_graph_r11c_gates.py.
PRE_D1_DRIVER_DIR = REPO / "tests" / "fixtures" / "benchmark_pre_d1_drivers"
PRE_D1_DRIVERS = {"benchmark/run_g3_n4.sh": "run_g3_n4.sh.txt", "benchmark/run_breadth.sh": "run_breadth.sh.txt"}


def gate_drivers_and_runbooks() -> None:
    print("\n-- F: drivers + runbooks: per-clone build/ports/stop, no shared-graph sync, no global export --")
    for rel in ("benchmark/run_g3_n4.sh", "benchmark/run_breadth.sh"):
        text = (REPO / rel).read_text(encoding="utf-8")
        probs = driver_problems(text)
        check(f"{rel}: builds each clone as its own project and measures it on ITS servers", not probs, "; ".join(probs))
        bash = shutil.which("bash")
        if bash:
            p = subprocess.run([bash, "-n", str(REPO / rel)], capture_output=True, text=True, timeout=60)
            check(f"{rel}: bash -n (syntax)", p.returncode == 0, p.stderr.strip()[:200])
        else:
            print(f"  [skip] {rel}: bash not available for a syntax check")
        # teeth: the PRE-fix driver (the committed fixture, whatever HEAD is) is flagged. A missing fixture is a
        # FAIL, never a skip: it ships with this gate
        fx = PRE_D1_DRIVER_DIR / PRE_D1_DRIVERS[rel]
        try:
            pre = fx.read_text(encoding="utf-8")   # universal newlines: an autocrlf (CRLF) checkout reads the same
            hp = driver_problems(pre)
            detail = str(hp)
        except OSError as exc:
            hp = []
            detail = f"pre-fix fixture unreadable: {fx.relative_to(REPO).as_posix()} ({type(exc).__name__})"
        check(f"teeth: the pre-fix {rel} (shared-graph sync) is flagged", any("deepmap_cli.py sync" in x for x in hp),
              detail)
    for rel in ("benchmark/RUN_SCENARIO_A.md", "benchmark/RUN_SCENARIO_B.md"):
        probs = runbook_problems((REPO / rel).read_text(encoding="utf-8"))
        check(f"{rel}: its commands build each clone as its own project (no shared-graph sync / mcp-start)",
              not probs, "; ".join(probs))
    old = 'for N in a b; do\n  python build/deepmap_cli.py sync "$D" --stale-cleanup\ndone\n'
    check("teeth: a runbook block with the old shared-graph sync is flagged",
          bool(runbook_problems("```bash\n" + old + "```\n")))
    leaked = 'export DEEPMAP_SEARCH_PORT="$SP"\n'
    check("teeth: a global port export is flagged", any("exports" in p for p in driver_problems(leaked)))


# ------------------------------------------------------------------------------------------------ G
def _drop_seam_env() -> None:
    """What provision's routing seam injects into ITS process (the project's bolt URI + MCP pair). Each
    real `provision.py setup` is its own process (and clone_graph.child_env strips these for the next
    one), so an in-process simulation must drop them — a leftover DEEPMAP_*_PORT reads as an operator
    override and the next setup would not allocate the clone a pair of its own."""
    for k in ("NEO4J_URI", "DEEPMAP_SEARCH_PORT", "DEEPMAP_EMBED_PORT"):
        os.environ.pop(k, None)


def gate_clean_machine_base_pair(CG) -> None:
    """ROUND-3 review #4: on a benchmark box where DeepMap was never set up (the runbooks' default — no
    dev-repo setup any more), the FIRST clone's `provision.py setup` adopts the base Neo4j pair 7687/7474,
    and `instance-rm` refuses the base store unconditionally — so `clone_graph.py rm` (the documented
    cleanup) returned 1 and left the clone claiming 7687 + 8787/8788 forever (D6: a remedy that cannot run).
    Fix: `build` claims a NON-base Neo4j pair for the clone BEFORE setup runs (provision honours an existing
    registry pair), and `rm` releases a clone that already holds the base pair (built before the fix)."""
    print("\n-- G: clean machine (no dev-repo setup) — a clone never takes the base pair; rm can clean one up --")
    with hermetic_home() as td:
        import port_registry as PR
        import provision as PV
        import graphify_extract as GX
        base = (PR.BASE_BOLT, PR.BASE_HTTP)
        # the premise: with NO other project registered, provision's real setup seam adopts the base pair
        first = _git_repo(td / "first")
        _pair, neo_problem, _mcp_problem = PV._resolve_ports(str(first), allocate=True)
        check("premise: on a clean machine provision's setup seam gives the first project the BASE pair",
              not neo_problem and PR.lookup_neo4j_ports(first) == base, f"{PR.lookup_neo4j_ports(first)} {neo_problem}")
        PR.remove_entry(first, drop_neo4j_only=False)
        _drop_seam_env()
        check("(the premise project is gone again: a clean registry for the clone)",
              PR.lookup_neo4j_ports(first) is None and PR.lookup_ports(first) is None)

        clone = _git_repo(td / "clone")
        injected = {}

        def setup_runner(argv, env):
            # what `provision.py setup <clone>` does to routing: the REAL seam, in-process
            if argv[2:3] == ["setup"]:
                _p, np_, mp_ = PV._resolve_ports(argv[3], allocate=True)
                injected["uri"] = os.environ.get("NEO4J_URI")
                _drop_seam_env()   # a real setup is its own process: nothing it injects outlives it
                return 0 if not np_ and not mp_ else 1
            return 0

        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            rc = CG.main(["build", str(clone)], runner=setup_runner)
        own = PR.lookup_neo4j_ports(clone)
        check("build on a clean machine succeeds", rc == 0, out.getvalue()[-400:])
        check("the clone's Neo4j pair is NOT the base 7687/7474 (so instance-rm can remove it later)",
              bool(own) and tuple(own) != base, str(own))
        check("provision's setup seam KEPT that pre-claimed pair (its sync + servers use it)",
              bool(own) and injected.get("uri") == f"bolt://127.0.0.1:{own[0]}", f"{injected} {own}")  # ROUND-8 J4: provision generates bolt://127.0.0.1:<port>
        check("the base pair stays unclaimed — free for a real project set up later",
              base[0] not in [e.get("bolt_port") for e in (PR.load_registry() or {}).get("projects", {}).values()
                              if isinstance(e, dict)])
        seen = []
        with contextlib.redirect_stdout(io.StringIO()):
            CG.main(["rm", str(clone)], runner=lambda a, e: seen.append(list(a)) or 0)
        check("rm of that clone ends with instance-rm --purge (its own instance, removable)",
              bool(seen) and seen[-1][2:] == ["instance-rm", "--key", PR.project_key(clone), "--purge", "--yes"],
              str(seen[-1] if seen else None))

        # a clone that ALREADY holds the base pair (built by the pre-fix clone_graph): rm must release it
        old = _git_repo(td / "old")
        PR.allocate_ports(old)
        PR.allocate_neo4j_ports(old, base_adopt_ok=lambda: True)
        check("(fixture) the old clone holds the base Neo4j pair", PR.lookup_neo4j_ports(old) == base)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            CG.main(["build", str(old)], runner=lambda a, e: 0)
        check("re-running build on that clone says it holds the BASE pair and how to fix it (rm, then build)",
              "never the base store" not in out.getvalue() and "base store" in out.getvalue().lower()
              and "clone_graph.py rm" in out.getvalue(), out.getvalue()[-400:])
        gdir = Path(os.environ["DEEPMAP_HOME"]) / "gout" / "abc123old"
        gdir.mkdir(parents=True)
        (gdir / GX.OWNER_MARKER).write_text(json.dumps({"project_key": PR.project_key(old)}), encoding="utf-8")
        calls = []

        def rm_runner(argv, env):
            calls.append(list(argv))
            return 2 if argv[2:3] == ["instance-rm"] else 0   # the real instance-rm refuses the base store

        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            rc = CG.main(["rm", str(old)], runner=rm_runner)
        check("rm of a base-pair clone succeeds (exit 0) instead of dead-ending on instance-rm's refusal",
              rc == 0, out.getvalue()[-500:])
        check("...it stopped that clone's MCP servers and Neo4j first",
              [c[2:3] for c in calls[:2]] == [["mcp-stop"], ["stop"]], str([c[2:3] for c in calls]))
        check("...and never asked instance-rm to delete the base store",
              not any(c[2:3] == ["instance-rm"] for c in calls), str([c[2:3] for c in calls]))
        check("...the clone no longer claims 7687/7474 or its MCP pair in the registry",
              PR.lookup_neo4j_ports(old) is None and PR.lookup_ports(old) is None,
              f"{PR.lookup_neo4j_ports(old)} {PR.lookup_ports(old)}")
        check("...its graphify output dir is purged", not gdir.exists())
        check("...and it says the base store (still holding that graph) was left in place, and where",
              "base" in out.getvalue() and "left" in out.getvalue(), out.getvalue()[-500:])

        # a failed stop must NOT release the claim (the servers would keep running unclaimed). "Could not be
        # stopped" is a LIVE state — DeepMap's base instance still running after `provision.py stop` — not a
        # non-zero exit code (which also means "nothing to stop": section H).
        old2 = _git_repo(td / "old2")
        PR.allocate_ports(old2)
        PR.allocate_neo4j_ports(old2, base_adopt_ok=lambda: True)
        with patched(PV, _legacy_base_instance_alive=lambda: True), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            rc = CG.main(["rm", str(old2)], runner=lambda a, e: 1 if a[2:3] == ["stop"] else 0)
        check("a base-pair clone whose Neo4j could not be stopped keeps its claim (rm exits 1)",
              rc == 1 and PR.lookup_neo4j_ports(old2) == base, f"rc={rc} {PR.lookup_neo4j_ports(old2)}")


# ------------------------------------------------------------------------------------------------ H
@contextlib.contextmanager
def patched(mod, **attrs):
    """Temporarily replace module attributes (a provision probe), restoring them afterwards."""
    saved = {k: getattr(mod, k) for k in attrs}
    try:
        for k, v in attrs.items():
            setattr(mod, k, v)
        yield
    finally:
        for k, v in saved.items():
            setattr(mod, k, v)


def _base_clone(PR, td: Path, name: str) -> Path:
    """A clone that holds the BASE Neo4j pair (+ an MCP pair) — what the pre-fix build left behind."""
    c = _git_repo(td / name)
    PR.allocate_ports(c)
    PR.allocate_neo4j_ports(c, base_adopt_ok=lambda: True)
    return c


def _rm(CG, clone, stop_rc=1):
    """`clone_graph.py rm <clone>` with provision's stop answering `stop_rc` ("no pid file / stale pid file ->
    nothing signalled" is rc 1) and instance-rm refusing the base store (rc 2) — (rc, output, calls)."""
    calls: list = []

    def runner(argv, env):
        calls.append(list(argv))
        return {"stop": stop_rc, "instance-rm": 2}.get(argv[2], 0)

    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        rc = CG.main(["rm", str(clone)], runner=runner)
    return rc, out.getvalue(), calls


def gate_nothing_to_stop_is_success(CG) -> None:
    """ROUND-4 review: `rm` of a base-pair clone required rc 0 from `provision.py stop`, which exits 1
    whenever nothing was signalled (no pid file; a stale one after a reboot). The runbooks' own flow — build,
    measure, `clone_graph.py stop`, later `rm` — therefore could NEVER release it: re-running rm failed the
    same way forever. The decision now comes from the LIVE state after the stop commands (no live verified
    DeepMap pid, the clone's ports not listening), not from their exit codes."""
    print("\n-- H: rm / stop — nothing to stop is success; the LIVE state (pids, ports) decides, not exit codes --")
    import provision as PV
    import graphify_extract as GX
    with hermetic_home() as td:
        import port_registry as PR
        base = (PR.BASE_BOLT, PR.BASE_HTTP)
        c = _base_clone(PR, td, "h1")
        check("(fixture) the clone holds the base Neo4j pair and an MCP pair",
              PR.lookup_neo4j_ports(c) == base and bool(PR.lookup_ports(c)))
        gdir = Path(os.environ["DEEPMAP_HOME"]) / "gout" / "h1gout"
        gdir.mkdir(parents=True)
        (gdir / GX.OWNER_MARKER).write_text(json.dumps({"project_key": PR.project_key(c)}), encoding="utf-8")
        rc, out, calls = _rm(CG, c, stop_rc=1)
        check("rm of an ALREADY-STOPPED base-pair clone (provision's stop: rc 1, nothing to stop) exits 0",
              rc == 0, out[-500:])
        check("...its registry claims (base Neo4j pair + MCP pair) are released",
              PR.lookup_neo4j_ports(c) is None and PR.lookup_ports(c) is None,
              f"{PR.lookup_neo4j_ports(c)} {PR.lookup_ports(c)}")
        check("...its graphify output dir is purged", not gdir.exists())
        check("...it still ran the stop commands first and never asked instance-rm for the base store",
              [a[2] for a in calls] == ["mcp-stop", "stop"], str([a[2] for a in calls]))
        check("...and says the non-zero stop exit code meant there was nothing left to stop",
              "nothing" in out.lower() and "left running" in out.lower(), out[-500:])
        rc2, out2, _ = _rm(CG, c, stop_rc=1)
        check("re-running rm afterwards does not dead-end on the base store (the clone no longer claims it)",
              "base pair is kept" not in out2, out2[-300:])

    with hermetic_home() as td:
        import port_registry as PR
        base = (PR.BASE_BOLT, PR.BASE_HTTP)
        c = _base_clone(PR, td, "h2")
        with patched(PV, _legacy_base_instance_alive=lambda: True):
            rc, out, _ = _rm(CG, c, stop_rc=1)
        check("DeepMap's base instance still running after stop (a verified live pid) -> rm exits 1, claim kept",
              rc == 1 and PR.lookup_neo4j_ports(c) == base, f"rc={rc} {PR.lookup_neo4j_ports(c)} {out[-300:]}")
        check("...naming what still runs and a runnable remedy (clone_graph.py stop, then rm again)",
              "neo4j" in out.lower() and "still running" in out.lower() and "clone_graph.py stop" in out, out[-500:])

    with hermetic_home() as td:
        import port_registry as PR
        base = (PR.BASE_BOLT, PR.BASE_HTTP)
        c = _base_clone(PR, td, "h3")
        sp = PR.lookup_ports(c)[0]
        with patched(PV, is_http_up=lambda host, port, timeout=2.0: int(port) == int(sp)):
            rc, out, _ = _rm(CG, c, stop_rc=0)
        check("both stops exit 0 but the clone's search port is still LISTENING -> rm exits 1, claim kept",
              rc == 1 and PR.lookup_neo4j_ports(c) == base and PR.lookup_ports(c) is not None,
              f"rc={rc} {out[-300:]}")
        check("...naming the port and how to find what holds it",
              str(sp) in out and "listening" in out.lower() and ("netstat" in out or "lsof" in out), out[-500:])
        rc, out, _ = _rm(CG, c, stop_rc=0)
        check("...and once the port is free, the same rm releases it", rc == 0 and PR.lookup_ports(c) is None,
              f"rc={rc} {out[-300:]}")

    with hermetic_home() as td:
        import port_registry as PR
        c = _base_clone(PR, td, "h4")
        sp = PR.lookup_ports(c)[0]
        with patched(PV, is_http_up=lambda host, port, timeout=2.0: int(port) == int(sp),
                     _mcp_foreign_owner=lambda name, port, target: "C:/elsewhere/other" if int(port) == int(sp) else None):
            rc, out, _ = _rm(CG, c, stop_rc=1)
        check("a listener on the clone's port that is ANOTHER project's server (owner sidecar) is not the clone's "
              "-> released", rc == 0 and PR.lookup_ports(c) is None, f"rc={rc} {out[-300:]}")
        check("...and that server is named as left running", "other" in out and "left running" in out.lower(),
              out[-400:])

    with hermetic_home() as td:
        import port_registry as PR
        c = _base_clone(PR, td, "h5")
        other = _git_repo(td / "realowner")
        PV.paths(None)["data"].mkdir(parents=True, exist_ok=True)
        check("(fixture) the base store's owner record names ANOTHER project (the <=v0.27.4 mis-adoption, F2)",
              PV._write_base_owner_record(PR.project_identity(other)) and bool(PV.foreign_base_claim_problem(c)))
        with patched(PV, _legacy_base_instance_alive=lambda: True):
            rc, out, _ = _rm(CG, c, stop_rc=1)
        check("F2: the running base instance is the OTHER project's (provision's stop refused it) -> the clone's "
              "mis-adopted claim is released anyway", rc == 0 and PR.lookup_neo4j_ports(c) is None,
              f"rc={rc} {out[-400:]}")
        check("...and it says the store belongs to that project (never 'still holds that clone's graph')",
              "realowner" in out and "still holds that clone's graph" not in out, out[-500:])

    with hermetic_home() as td:
        import port_registry as PR
        c = _base_clone(PR, td, "h6")
        with patched(PV, is_http_up=lambda host, port, timeout=2.0: int(port) == PR.BASE_BOLT):
            rc, out, _ = _rm(CG, c, stop_rc=1)
        check("bolt 7687 held by something with NO live DeepMap Neo4j pid (e.g. a Docker Neo4j) -> released, "
              "not wedged", rc == 0 and PR.lookup_neo4j_ports(c) is None, f"rc={rc} {out[-300:]}")
        check("...with a note that 7687 is held by a process DeepMap does not manage (left alone)",
              str(PR.BASE_BOLT) in out and "does not manage" in out, out[-400:])

    with hermetic_home() as td:
        import port_registry as PR
        base = (PR.BASE_BOLT, PR.BASE_HTTP)
        c = _base_clone(PR, td, "h7")

        def boom():
            raise RuntimeError("probe failed")

        with patched(PV, _legacy_base_instance_alive=boom):
            rc, out, _ = _rm(CG, c, stop_rc=0)
        check("a liveness probe that fails -> the claim is KEPT (rm exits 1: cannot show it is stopped)",
              rc == 1 and PR.lookup_neo4j_ports(c) == base, f"rc={rc} {out[-300:]}")
        check("...saying it could not verify", "could not verify" in out, out[-300:])

    with hermetic_home() as td:
        import port_registry as PR
        base = (PR.BASE_BOLT, PR.BASE_HTTP)
        c = _base_clone(PR, td, "h8")
        sp = PR.lookup_ports(c)[0]
        pf = PV._mcp_pidfile("deepmap-search", sp)
        pf.parent.mkdir(parents=True, exist_ok=True)
        pf.write_text(str(os.getpid()), encoding="utf-8")
        with patched(PV, _pid_cmdline=lambda pid: "python build/deepmap_search_server.py" if pid == os.getpid() else None):
            rc, out, _ = _rm(CG, c, stop_rc=0)
        check("the clone's search pid file names a LIVE server process (not yet listening) -> claim kept",
              rc == 1 and PR.lookup_neo4j_ports(c) == base, f"rc={rc} {out[-300:]}")
        with patched(PV, _pid_cmdline=lambda pid: "C:/Windows/explorer.exe"):
            rc, out, _ = _rm(CG, c, stop_rc=0)
        check("...but a pid file whose live pid now runs something else (recycled) does not block the release",
              rc == 0 and PR.lookup_neo4j_ports(c) is None, f"rc={rc} {out[-300:]}")

    # the `stop` verb: a clone that is already stopped is a success (the drivers log a WARN on non-zero)
    with hermetic_home() as td:
        import port_registry as PR
        own = _git_repo(td / "own")
        PR.allocate_ports(own)
        pair = PR.allocate_neo4j_ports(own, base_adopt_ok=lambda: False)
        check("(fixture) a clone with its own non-base instance", bool(pair) and tuple(pair) != (7687, 7474), str(pair))
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            rc = CG.main(["stop", str(own)], runner=lambda a, e: 1 if a[2] == "stop" else 0)
        check("`stop` of an already-stopped clone (provision's stop: rc 1, nothing to stop) exits 0", rc == 0)
        err = io.StringIO()
        with patched(PV, _live_instance_pid=lambda key: 4242, _is_deepmap_neo4j_pid=lambda pid: pid == 4242), \
                contextlib.redirect_stdout(err), contextlib.redirect_stderr(err):
            rc = CG.main(["stop", str(own)], runner=lambda a, e: 1 if a[2] == "stop" else 0)
        check("...but `stop` of a clone whose own Neo4j is still running (live verified pid) exits 1, naming it",
              rc == 1 and "4242" in err.getvalue(), f"rc={rc} {err.getvalue()[-300:]}")
        bc = _base_clone(PR, td, "stopbase")
        check("(fixture) a base-pair clone next to it", PR.lookup_neo4j_ports(bc) == (7687, 7474))
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            rc = CG.main(["stop", str(bc)], runner=lambda a, e: 1 if a[2] == "stop" else 0)
        check("`stop` of an already-stopped base-pair clone exits 0 too", rc == 0)


# ------------------------------------------------------------------------------------------------ I
class _FakeClock:
    """A monotonic clock that only moves when the code under test sleeps (records each sleep)."""

    def __init__(self):
        self.t = 0.0
        self.sleeps: list = []

    def now(self) -> float:
        return self.t

    def sleep(self, s) -> None:
        self.sleeps.append(float(s))
        self.t += float(s)


def gate_stop_settles(CG) -> None:
    """A signalled process is not gone the instant its stop returns: Windows TerminateProcess (taskkill /F) is
    asynchronous, so a just-killed MCP server / Neo4j can still hold its port for a moment. Judging the live
    state ONCE, right after the stop commands, turned that moment into a spurious "not fully stopped" (rm
    exit 1, claim kept; `stop` exit 1 -> the drivers' WARN). The verdict is re-checked for a BOUNDED grace
    (DEEPMAP_BENCH_STOP_GRACE_S, default a few seconds) while something still looks up — never when nothing
    does, never unbounded."""
    print("\n-- I: the live-state verdict settles for a bounded grace (a just-killed server can hold its port) --")
    import provision as PV
    have = all(hasattr(CG, a) for a in ("_sleep", "_clock", "stop_grace_s", "DEFAULT_STOP_GRACE_S",
                                        "MAX_STOP_GRACE_S"))
    check("clone_graph exposes the settle seam (_sleep, _clock, stop_grace_s, DEFAULT/MAX_STOP_GRACE_S)", have)

    def clock_of(clk):   # without the seam (the pre-fix module) the behavioural checks still run — and fail
        return patched(CG, _sleep=clk.sleep, _clock=clk.now) if have else contextlib.nullcontext()
    with hermetic_home() as td:
        import port_registry as PR
        os.environ["DEEPMAP_BENCH_STOP_GRACE_S"] = "5"
        c = _base_clone(PR, td, "i1")
        sp = int(PR.lookup_ports(c)[0])
        probes = {"n": 0}

        def exiting(host, port, timeout=2.0):   # the server is still exiting for the first two probes
            if int(port) != sp:
                return False
            probes["n"] += 1
            return probes["n"] <= 2

        clk = _FakeClock()
        with patched(PV, is_http_up=exiting), clock_of(clk):
            rc, out, _ = _rm(CG, c, stop_rc=0)
        check("a search port that frees a moment after the stop -> rm waits, then releases the claim (exit 0)",
              rc == 0 and PR.lookup_neo4j_ports(c) is None and PR.lookup_ports(c) is None, f"rc={rc} {out[-300:]}")
        check("...it re-checked (slept) while the port was still held, within the grace",
              1 <= len(clk.sleeps) and sum(clk.sleeps) <= 5.0 + 1e-9, str(clk.sleeps))

    with hermetic_home() as td:
        import port_registry as PR
        c = _base_clone(PR, td, "i2")   # grace 0 (hermetic default): the same moment -> not settled
        sp = int(PR.lookup_ports(c)[0])
        probes = {"n": 0}

        def exiting(host, port, timeout=2.0):
            if int(port) != sp:
                return False
            probes["n"] += 1
            return probes["n"] <= 2

        clk = _FakeClock()
        with patched(PV, is_http_up=exiting), clock_of(clk):
            rc, out, _ = _rm(CG, c, stop_rc=0)
        check("teeth: with no grace (DEEPMAP_BENCH_STOP_GRACE_S=0) that moment keeps the claim (rm exits 1, "
              "no sleep)", rc == 1 and PR.lookup_neo4j_ports(c) is not None and not clk.sleeps,
              f"rc={rc} sleeps={clk.sleeps}")

    with hermetic_home() as td:
        import port_registry as PR
        os.environ["DEEPMAP_BENCH_STOP_GRACE_S"] = "3"
        c = _base_clone(PR, td, "i3")
        sp = int(PR.lookup_ports(c)[0])
        clk = _FakeClock()
        with patched(PV, is_http_up=lambda host, port, timeout=2.0: int(port) == sp), \
                clock_of(clk):
            rc, out, _ = _rm(CG, c, stop_rc=0)
        check("a port that STAYS held -> rm gives up after the grace (bounded wait), claim kept, exit 1",
              rc == 1 and PR.lookup_neo4j_ports(c) is not None and 3.0 <= sum(clk.sleeps) <= 3.0 + 0.5 + 1e-9,
              f"rc={rc} waited={sum(clk.sleeps)}")
        check("...and still names the held port", str(sp) in out and "listening" in out.lower(), out[-300:])

    with hermetic_home() as td:
        import port_registry as PR
        os.environ["DEEPMAP_BENCH_STOP_GRACE_S"] = "5"
        c = _base_clone(PR, td, "i4")
        clk = _FakeClock()
        with clock_of(clk):
            rc, out, _ = _rm(CG, c, stop_rc=1)
        check("nothing left running -> released at once, with NO wait (the grace only applies while something "
              "is still up)", rc == 0 and not clk.sleeps, f"rc={rc} sleeps={clk.sleeps}")

    with hermetic_home() as td:
        import port_registry as PR
        os.environ["DEEPMAP_BENCH_STOP_GRACE_S"] = "5"
        own = _git_repo(td / "i5")
        PR.allocate_ports(own)
        pair = PR.allocate_neo4j_ports(own, base_adopt_ok=lambda: False)
        probes = {"n": 0}

        def jvm_exiting(host, port, timeout=2.0):   # its own bolt port: still held for the first probe
            if int(port) != int(pair[0]):
                return False
            probes["n"] += 1
            return probes["n"] <= 1

        clk = _FakeClock()
        with patched(PV, is_http_up=jvm_exiting), clock_of(clk), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            rc = CG.main(["stop", str(own)], runner=lambda a, e: 0)
        check("`stop` too: its own Neo4j bolt port freeing a moment after the stop -> exit 0 (no spurious WARN)",
              rc == 0 and len(clk.sleeps) >= 1, f"rc={rc} sleeps={clk.sleeps}")

    if not have:
        check("stop_grace_s: unset/garbage/nan -> the default, negative -> 0, huge/inf -> capped", False,
              "no stop_grace_s")
        return
    env_cases = {"": CG.DEFAULT_STOP_GRACE_S, "abc": CG.DEFAULT_STOP_GRACE_S, "-4": 0.0, "0": 0.0, "2.5": 2.5,
                 "nan": CG.DEFAULT_STOP_GRACE_S, "inf": CG.MAX_STOP_GRACE_S, "100000": CG.MAX_STOP_GRACE_S}
    got = {k: CG.stop_grace_s({"DEEPMAP_BENCH_STOP_GRACE_S": k}) for k in env_cases}
    check("stop_grace_s: unset/garbage/nan -> the default, negative -> 0, huge/inf -> capped",
          got == env_cases and CG.stop_grace_s({}) == CG.DEFAULT_STOP_GRACE_S
          and 0 < CG.DEFAULT_STOP_GRACE_S <= CG.MAX_STOP_GRACE_S <= 120, str(got))


def gate_child_env_auto_sync_off(CG) -> None:
    print("\n-- M12: every clone build runs with DeepMap's background refresh off --")
    for base, label in (({"DEEPMAP_AUTO_SYNC": "1"}, "an operator's DEEPMAP_AUTO_SYNC=1"), ({}, "an empty env")):
        env = CG.child_env(dict(base, NEO4J_URI="bolt://127.0.0.1:9"))
        check(f"child_env sets DEEPMAP_AUTO_SYNC=0 ({label}) and still strips NEO4J_URI",
              env.get("DEEPMAP_AUTO_SYNC") == "0" and "NEO4J_URI" not in env, str(env))


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 72)
    print("DeepMap — benchmark per-clone graph build under D1 (no Neo4j, no claude, no spend)")
    print("=" * 72)
    if not shutil.which("git"):
        print("  [skip] git not on PATH — these gates need temp git repos")
        print("RESULT: GREEN — 0/0 gates passed.")
        return 0
    CG = _load(BENCH / "clone_graph.py", "dm_clone_graph_gate")
    gate_old_path_refused()
    gate_new_build_routes_clone_to_its_own_instance()
    gate_on_arm_problem(CG)
    gate_do_run_fails_closed(BENCH / "run_benchmark.py")
    gate_cli(CG)
    gate_child_env_auto_sync_off(CG)
    gate_clean_machine_base_pair(CG)
    gate_nothing_to_stop_is_success(CG)
    gate_stop_settles(CG)
    gate_drivers_and_runbooks()
    print("\n" + "=" * 72)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
