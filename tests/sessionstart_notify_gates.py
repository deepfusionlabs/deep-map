#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gates for the SessionStart notify hook (WS-A1 onboarding) + the mcp-stop nothing-to-stop fix.

Field report (2026-07): a plugin-install-only project got ZERO signal that DeepMap wasn't
provisioned (every hook is marker-gated => silent exactly when the tools are absent), and a stale
pidfile made `mcp-stop` report failure with nothing to stop. These gates lock:

  notify hook (hooks/deepmap_sessionstart_notify.py):
  - marker ABSENT + code-shaped root -> emits ONE SessionStart additionalContext onboarding line
    (mentions /deep-map:setup), with NO permission decision,
  - fires ONCE EVER per project root (persisted shown-flag), and the flag lives in the USER-level
    DEEPMAP_HOME — NOTHING is ever created under the project root (non-DeepMap repos stay pristine),
  - marker PRESENT -> silent (the availability announce is a later, coverage-gated phase),
  - non-code root -> silent; opt-outs DEEPMAP_ONBOARD=0 / DEEPMAP_HOOKS=0 -> silent,
  - malformed/empty stdin -> silent exit 0 (never disrupts session start),
  - registered in hooks/hooks.json on SessionStart.

  provision.stop_mcp_server:
  - no pidfile -> True ("nothing to stop" is success — stop_mcp_servers is all(...)),
  - stale pidfile (dead pid) -> clears the file and returns True,
  - unreadable pidfile -> clears the file and returns True,
  - live pid + failing kill -> still False (a REAL stop failure stays loud).

Hermetic: temp dirs, faked stdin, DEEPMAP_HOME pointed at a temp dir. No Neo4j, no network.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import tempfile
from pathlib import Path

# Auto-sync (DeepMap's background refresh at session start) is on by default in an attended session; this gate
# locks the hooks WITHOUT it, so it is pinned off for this process and every hook process the gate starts
# (tests/auto_sync_hooks_gates.py locks the hooks with it on).
os.environ["DEEPMAP_AUTO_SYNC"] = "0"
REPO = Path(__file__).resolve().parent.parent
HOOKS = REPO / "hooks"
BUILD = REPO / "build"

_PASS = _FAIL = 0


def check(desc: str, cond: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    ok = bool(cond)
    _PASS += ok
    _FAIL += (not ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {desc}" + ("" if ok else f"  -- {detail}"))


def _load(name: str, base: Path):
    spec = importlib.util.spec_from_file_location(f"dmnotify_{name}", base / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


NT = _load("deepmap_sessionstart_notify", HOOKS)


def _run_hook(payload, home: Path) -> tuple:
    """Run the hook in-process with faked stdin + captured stdout; returns (rc, stdout)."""
    saved_stdin, saved_stdout = sys.stdin, sys.stdout
    saved_home = os.environ.get("DEEPMAP_HOME")
    try:
        os.environ["DEEPMAP_HOME"] = str(home)
        sys.stdin = io.StringIO(payload if isinstance(payload, str) else json.dumps(payload))
        sys.stdout = io.StringIO()
        rc = NT.main()
        return rc, sys.stdout.getvalue()
    finally:
        sys.stdin, sys.stdout = saved_stdin, saved_stdout
        if saved_home is None:
            os.environ.pop("DEEPMAP_HOME", None)
        else:
            os.environ["DEEPMAP_HOME"] = saved_home


def _tree_size(root: Path) -> int:
    return sum(1 for _ in root.rglob("*"))


def gate_onboarding_fires() -> None:
    print("\n-- marker absent + code root -> ONE onboarding line, additionalContext-only --")
    with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as th:
        root, home = Path(td), Path(th)
        (root / "app.py").write_text("x=1", encoding="utf-8")
        before = _tree_size(root)
        rc, out = _run_hook({"cwd": td, "session_id": "s1"}, home)
        check("exit 0", rc == 0)
        check("emits output", bool(out.strip()), repr(out))
        try:
            env = json.loads(out)
        except Exception:
            env = {}
        hso = env.get("hookSpecificOutput", {})
        check("SessionStart envelope", hso.get("hookEventName") == "SessionStart", out[:120])
        ctx = hso.get("additionalContext", "")
        check("mentions /deep-map:setup", "/deep-map:setup" in ctx, ctx[:120])
        check("names the tools", "deepmap_search" in ctx and "deepmap_impact" in ctx, ctx[:160])
        check("NO permission decision", "permissionDecision" not in out, out[:200])
        check("NOTHING created under the project root (stays pristine)",
              _tree_size(root) == before, f"{_tree_size(root)} vs {before}")
        check("shown-flag lives under DEEPMAP_HOME", (home / "state" / "onboarded.json").is_file())

        # Once EVER: a second session in the same root stays silent.
        rc2, out2 = _run_hook({"cwd": td, "session_id": "s2"}, home)
        check("second session -> silent (once ever per root)", rc2 == 0 and out2 == "", repr(out2))

        # A DIFFERENT project root still gets its own one-time line.
        with tempfile.TemporaryDirectory() as td2:
            (Path(td2) / "main.go").write_text("package main", encoding="utf-8")
            rc3, out3 = _run_hook({"cwd": td2, "session_id": "s3"}, home)
            check("a different root gets its own line", rc3 == 0 and "/deep-map:setup" in out3,
                  repr(out3[:120]))


def gate_silent_paths() -> None:
    print("\n-- silent paths: unhealthy-manifest / non-code / opt-out / malformed stdin --")
    with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as th:
        root, home = Path(td), Path(th)
        (root / "app.py").write_text("x=1", encoding="utf-8")
        dm = root / ".deepmap"
        dm.mkdir()
        (dm / "graph_build.json").write_text("{}", encoding="utf-8")
        rc, out = _run_hook({"cwd": td, "session_id": "s1"}, home)
        check("marker present but NO verdict field -> silent (never steer on unknown health)",
              rc == 0 and out == "", repr(out))

    with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as th:
        root, home = Path(td), Path(th)
        (root / "notes.txt").write_text("hi", encoding="utf-8")
        rc, out = _run_hook({"cwd": td, "session_id": "s1"}, home)
        check("non-code root -> silent", rc == 0 and out == "", repr(out))

    for env_name in ("DEEPMAP_ONBOARD", "DEEPMAP_HOOKS"):
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as th:
            (Path(td) / "app.py").write_text("x=1", encoding="utf-8")
            os.environ[env_name] = "0"
            try:
                rc, out = _run_hook({"cwd": td, "session_id": "s1"}, Path(th))
            finally:
                os.environ.pop(env_name, None)
            check(f"{env_name}=0 -> silent", rc == 0 and out == "", repr(out))

    with tempfile.TemporaryDirectory() as th:
        rc, out = _run_hook("", Path(th))
        check("empty stdin -> silent exit 0", rc == 0 and out == "")
        rc, out = _run_hook("{not json", Path(th))
        check("malformed stdin -> silent exit 0", rc == 0 and out == "")
        rc, out = _run_hook('"just a string"', Path(th))
        check("non-dict payload -> silent exit 0", rc == 0 and out == "")


def _provisioned_root(td: str, verdict="READY", built_at_sha=None, reasons=None) -> Path:
    root = Path(td)
    dm = root / ".deepmap"
    dm.mkdir(parents=True, exist_ok=True)
    manifest = {"nodes": 10, "extractor": "graphify"}
    if verdict is not None:
        manifest["verdict"] = verdict
    if reasons is not None:
        manifest["reasons"] = list(reasons)
    if built_at_sha is not None:
        manifest["built_at_sha"] = built_at_sha
    (dm / "graph_build.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


def gate_announce() -> None:
    print("\n-- marker present + verdict READY -> once-per-session availability announce --")
    with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as th:
        _provisioned_root(td)
        rc, out = _run_hook({"cwd": td, "session_id": "a1"}, Path(th))
        check("READY manifest -> announce emitted", rc == 0 and bool(out.strip()), repr(out[:80]))
        try:
            ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        except Exception:
            ctx = ""
        check("announce prefers deepmap_search + deepmap_impact",
              "deepmap_search" in ctx and "deepmap_impact" in ctx, ctx[:160])
        check("announce carries the DELEGATION line (subagents must be told)",
              "subagent" in ctx.lower() and "Task" in ctx, ctx[:300])
        check("announce mentions the reload fallback", "reload" in ctx.lower(), ctx[:300])
        check("NO permission decision", "permissionDecision" not in out)
        rc2, out2 = _run_hook({"cwd": td, "session_id": "a1"}, Path(th))
        check("same session -> silent (once per session)", rc2 == 0 and out2 == "", repr(out2))
        rc3, out3 = _run_hook({"cwd": td, "session_id": "a2"}, Path(th))
        check("new session -> announces again", rc3 == 0 and "deepmap_search" in out3)
        check("announce throttle state lives under <root>/.deepmap (provisioned project)",
              (Path(td) / ".deepmap" / "announce_state.json").is_file())

    # A non-READY verdict must NEVER produce a preference steer toward the graph. Silence remains the
    # default for it — with ONE deliberate exception below.
    for verdict in ("DEGRADED", "FAILED"):
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as th:
            _provisioned_root(td, verdict=verdict)
            rc, out = _run_hook({"cwd": td, "session_id": "a1"}, Path(th))
            check(f"verdict {verdict} (no coverage reason) -> silent", rc == 0 and out == "",
                  repr(out))

    # THE EXCEPTION (2026-08 Delphi field report): DEGRADED *with a persisted LOW-COVERAGE reason* is
    # the one state where silence actively harms — the agent cannot distinguish "not indexed" from
    # "does not exist" and answers confidently from an empty result. The hook warns AWAY from the
    # graph for the named layer; it must still never steer TOWARD it.
    _pascal = ("LOW COVERAGE for Pascal/Delphi — 412 files on disk but only 0 extracted node(s); "
               "Pascal/Delphi code is effectively invisible to search/impact (check the `doctor` "
               "extractor line — is the graphify engine installed?).")
    with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as th:
        _provisioned_root(td, verdict="DEGRADED", reasons=[_pascal])
        rc, out = _run_hook({"cwd": td, "session_id": "a1"}, Path(th))
        check("DEGRADED + LOW COVERAGE -> the agent IS told", rc == 0 and out != "", repr(out))
        ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"] if out else ""
        check("...names the uncovered language", "Pascal/Delphi" in ctx, ctx[:300])
        check("...carries the file/node counts", "412 files" in ctx and "0 extracted" in ctx, ctx[:300])
        check("...teaches empty != absent",
              "not indexed" in ctx.lower() and "does not exist" in ctx.lower(), ctx[:300])
        check("...routes the agent to grep for that layer",
              "grep" in ctx.lower(), ctx[:300])
        check("...names the remediation command", "/deep-map:refresh" in ctx, ctx[:300])
        check("...does NOT steer toward the degraded graph (no 'prefer deepmap_search')",
              "prefer deepmap_search" not in ctx.lower(), ctx[:300])
        check("...drops the long explanatory tail (one short line per gap)",
              "graphify engine installed" not in ctx, ctx[:300])
        check("NO permission decision", "permissionDecision" not in out)
        rc2, out2 = _run_hook({"cwd": td, "session_id": "a1"}, Path(th))
        check("same session -> silent (once per session, shares the announce throttle)",
              rc2 == 0 and out2 == "", repr(out2))

    with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as th:
        _provisioned_root(td, verdict="FAILED", reasons=[_pascal])
        rc, out = _run_hook({"cwd": td, "session_id": "a1"}, Path(th))
        check("FAILED stays silent even with a coverage reason (setup is broken, not partial)",
              rc == 0 and out == "", repr(out))

    with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as th:
        _provisioned_root(td, verdict="DEGRADED",
                          reasons=["VECTOR SEARCH DISABLED — no embeddings were generated."])
        rc, out = _run_hook({"cwd": td, "session_id": "a1"}, Path(th))
        check("a non-coverage DEGRADED reason still stays silent", rc == 0 and out == "", repr(out))

    with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as th:
        root = Path(td)
        dm = root / ".deepmap"
        dm.mkdir()
        (dm / "graph_build.json").write_text("{not json", encoding="utf-8")
        rc, out = _run_hook({"cwd": td, "session_id": "a1"}, Path(th))
        check("malformed manifest -> silent exit 0", rc == 0 and out == "")

    for env_name in ("DEEPMAP_ANNOUNCE", "DEEPMAP_HOOKS"):
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as th:
            _provisioned_root(td)
            os.environ[env_name] = "0"
            try:
                rc, out = _run_hook({"cwd": td, "session_id": "a1"}, Path(th))
            finally:
                os.environ.pop(env_name, None)
            check(f"{env_name}=0 -> announce silenced", rc == 0 and out == "", repr(out))

    # DEEPMAP_ONBOARD=0 silences ONLY onboarding — the announce still fires.
    with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as th:
        _provisioned_root(td)
        os.environ["DEEPMAP_ONBOARD"] = "0"
        try:
            rc, out = _run_hook({"cwd": td, "session_id": "a1"}, Path(th))
        finally:
            os.environ.pop("DEEPMAP_ONBOARD", None)
        check("DEEPMAP_ONBOARD=0 does NOT silence the announce (separate opt-outs)",
              rc == 0 and "deepmap_search" in out, repr(out[:80]))


def _git(*args, cwd):
    import subprocess
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=60)


def _make_repo(td: Path) -> str:
    _git("init", "-q", cwd=td)
    _git("config", "user.email", "t@t.t", cwd=td)
    _git("config", "user.name", "t", cwd=td)
    _git("config", "commit.gpgsign", "false", cwd=td)
    (td / "a.rb").write_text("class A\nend\n", encoding="utf-8")
    _git("add", "-A", cwd=td)
    _git("commit", "-q", "-m", "init", cwd=td)
    return _git("rev-parse", "HEAD", cwd=td).stdout.strip().lower()


def gate_staleness() -> None:
    print("\n-- staleness: announce + doctor/status flag a graph built before HEAD --")
    import shutil
    if not shutil.which("git"):
        check("(git on PATH for staleness checks)", True, "skipped — git absent")
        return
    with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as th:
        root = Path(td)
        sha1 = _make_repo(root)
        _provisioned_root(td, built_at_sha=sha1)
        rc, out = _run_hook({"cwd": td, "session_id": "s1"}, Path(th))
        check("built_at_sha == HEAD -> announce WITHOUT a stale note",
              "deepmap_search" in out and "refresh" not in out.lower(), repr(out[-160:]))
        # Advance HEAD -> the graph is now stale.
        (root / "b.rb").write_text("class B\nend\n", encoding="utf-8")
        _git("add", "-A", cwd=root)
        _git("commit", "-q", "-m", "more", cwd=root)
        rc, out = _run_hook({"cwd": td, "session_id": "s2"}, Path(th))
        check("HEAD moved -> announce appends the /deep-map:refresh stale note",
              "deepmap_search" in out and "/deep-map:refresh" in out, repr(out[-200:]))

        # The CLI helper: doctor/status freshness line.
        import importlib.util
        spec = importlib.util.spec_from_file_location("dmnotify_cli", BUILD / "deepmap_cli.py")
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        line = cli._staleness_line(root)
        check("_staleness_line flags STALE with both SHAs",
              line and "STALE" in line and sha1[:7] in line, str(line))
        # Fresh again after a "re-sync" (manifest updated to the new HEAD).
        head2 = _git("rev-parse", "HEAD", cwd=root).stdout.strip().lower()
        _provisioned_root(td, built_at_sha=head2)
        line2 = cli._staleness_line(root)
        check("_staleness_line reports fresh when built == HEAD",
              line2 and "STALE" not in line2 and "fresh" in line2, str(line2))
        check("_staleness_line is None outside a repo / without a manifest",
              cli._staleness_line(Path(th)) is None)


def gate_registered() -> None:
    print("\n-- hooks.json registers the notify hook on SessionStart --")
    try:
        data = json.loads((HOOKS / "hooks.json").read_text(encoding="utf-8"))
        cmds = [h.get("command", "") for grp in data.get("hooks", {}).get("SessionStart", [])
                for h in grp.get("hooks", [])]
    except Exception as exc:
        cmds = []
        print(f"    (hooks.json unreadable: {exc})")
    check("notify registered on SessionStart",
          any("deepmap_sessionstart_notify.py" in c for c in cmds), str(cmds))
    check("heal still registered (additive, not replaced)",
          any("deepmap_sessionstart_heal.py" in c for c in cmds), str(cmds))


def gate_stop_nothing_to_stop() -> None:
    print("\n-- provision.stop_mcp_server: 'nothing to stop' is SUCCESS --")
    P = _load("provision", BUILD)
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        saved_pidfile = P._mcp_pidfile
        saved_alive, saved_term = P._pid_alive, P._terminate_pid
        try:
            # Per-PORT pidfile now (side-by-side projects): stop_mcp_server reads _mcp_pidfile(name,
            # mcp_port(name)). With no env override mcp_port -> the default 8787/8788.
            P._mcp_pidfile = lambda name, port: run_dir / f"{name}-{port}.pid"
            _sport = P.mcp_port("deepmap-search")

            # (1) no pidfile at all -> True.
            check("no pidfile -> True", P.stop_mcp_server("deepmap-search") is True)

            # (2) stale pidfile (dead pid) -> cleared + True.
            pf = run_dir / f"deepmap-search-{_sport}.pid"
            pf.write_text("99999999", encoding="utf-8")
            P._pid_alive = lambda pid: False
            check("stale pidfile -> True", P.stop_mcp_server("deepmap-search") is True)
            check("stale pidfile cleared", not pf.exists())

            # (3) unreadable pidfile -> cleared + True.
            pf.write_text("not-a-pid", encoding="utf-8")
            check("unreadable pidfile -> True", P.stop_mcp_server("deepmap-search") is True)
            check("unreadable pidfile cleared", not pf.exists())

            # (4) a REAL stop failure stays loud: live pid, kill fails -> False.
            pf.write_text("12345", encoding="utf-8")
            P._pid_alive = lambda pid: True
            P._terminate_pid = lambda pid, tree=False: False
            import contextlib
            with contextlib.redirect_stderr(io.StringIO()):
                check("live pid + failing kill -> False (real failure stays loud)",
                      P.stop_mcp_server("deepmap-search") is False)

            # (5) the aggregate: both servers nothing-to-stop -> True exit for mcp-stop.
            pf.unlink(missing_ok=True)
            P._pid_alive = lambda pid: False
            check("stop_mcp_servers() True when nothing to stop (mcp-stop && mcp-start unwedged)",
                  P.stop_mcp_servers() is True)
        finally:
            P._mcp_pidfile = saved_pidfile
            P._pid_alive, P._terminate_pid = saved_alive, saved_term


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 64)
    print("DeepMap — SessionStart notify + mcp-stop gates (WS-A1, no Neo4j)")
    print("=" * 64)
    gate_onboarding_fires()
    gate_silent_paths()
    gate_announce()
    gate_staleness()
    gate_registered()
    gate_stop_nothing_to_stop()
    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
