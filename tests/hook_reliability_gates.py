#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j, no-network gates for the activation-reliability hooks + slash-command entry points, from
the 2026-09 live validation (hooks/*.py, hooks/hooks.json, commands/*.md):

  A2  every hook decodes its stdin payload as UTF-8 whatever the locale. Claude Code writes raw UTF-8
      JSON; a bare sys.stdin.read() decoded it in the Windows ANSI code page, so a project under a
      non-ASCII path resolved to a garbled cwd and heal / notify / nudge / syncflag all went silent
      (the access hook logged into a mojibake SIBLING dir). Locked with REAL hook subprocesses in
      UTF-8 mode OFF: a deterministic cp1252 stdin on every platform, and the box's own locale.
  H1  the master DEEPMAP_HOOKS=0 (and a per-hook DEEPMAP_HEAL=0) disables the SessionStart heal —
      and the opt-in hibernate sweep it would also spawn.
  +   heal no longer revives a project whose last sync verdict is FAILED / 0 nodes (setup refused to
      wire it; heal re-did the wiring every session for an empty graph).
  H2  DEEPMAP_SYNC_FLAG_EVERY=1 reminds after EVERY edit (it silenced the flag entirely).
  +   the sync flag claims "a commit also re-syncs via the git hook" ONLY when this repo's post-commit
      hook carries DeepMap's `# deepmap` marker (/deep-map:setup never installs one).
  H3  the pre-search nudge is verdict-gated like notify (READY; a coverage-aware variant for DEGRADED
      with a LOW-COVERAGE reason; silent otherwise) and stays quiet while the last heal reported the
      database or servers down (a heal status older than the manifest is superseded).
  H4  notify turns a failed last heal into ONE "database not reachable — run /deep-map:setup" line
      instead of the READY announce, and when this project's MCP servers are down at session start
      (heal is reviving them — the ECONNREFUSED-at-startup race) tells the agent to reconnect via /mcp.
  H5  hooks.json runs every hook in Python UTF-8 mode, the heal child inherits PYTHONUTF8=1, and every
      commands/*.md python entry point sets PYTHONUTF8=1 without breaking its allowed-tools prefix.
  +   commands/*.md doc drift: impact.md's --edges example keeps the default runtime edges and its
      empty-result wording is honest; status.md names no uninstalled `deepmap` executable.

Hermetic: temp project dirs + a temp DEEPMAP_HOME; heal's provision.py is a recording STUB (via
CLAUDE_PLUGIN_ROOT) or a fake Popen; the MCP port probe is stubbed in-process. No Neo4j, no sockets
opened by the tests, no network. Exit 0 = green.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# Auto-sync (DeepMap's background refresh at session start) is on by default in an attended session; this gate
# locks the hooks WITHOUT it, so it is pinned off for this process and every hook process the gate starts
# (tests/auto_sync_hooks_gates.py locks the hooks with it on).
os.environ["DEEPMAP_AUTO_SYNC"] = "0"
REPO = Path(__file__).resolve().parent.parent
HOOKS = REPO / "hooks"
BUILD = REPO / "build"
COMMANDS = REPO / "commands"
PY = sys.executable

sys.path.insert(0, str(BUILD))
import port_registry as PR  # noqa: E402

_PASS = 0
_FAIL = 0

# Opt-outs / routing knobs a runner might have set; every case sets exactly what it needs.
_SCRUB = ("PYTHONUTF8", "PYTHONIOENCODING", "DEEPMAP_HOOKS", "DEEPMAP_HEAL", "DEEPMAP_NUDGE",
          "DEEPMAP_SYNC_FLAG", "DEEPMAP_SYNC_FLAG_EVERY", "DEEPMAP_ANNOUNCE", "DEEPMAP_ONBOARD",
          "DEEPMAP_HIBERNATE", "DEEPMAP_RETRIEVAL_LOG", "CLAUDE_PROJECT_DIR", "DEEPMAP_PROJECT_ROOT",
          "NEO4J_URI", "DEEPMAP_LEARNING")

_PASCAL = ("LOW COVERAGE for Pascal/Delphi — 412 files on disk but only 0 extracted node(s); "
           "Pascal/Delphi code is effectively invisible to search/impact.")


def check(label: str, cond: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {label}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}{(' — ' + detail) if detail else ''}")


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"dmhr_{name}", HOOKS / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _manifest(root: Path, verdict="READY", nodes=10, reasons=None, raw=None) -> Path:
    d = root / ".deepmap"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "graph_build.json"
    if raw is not None:
        p.write_text(raw, encoding="utf-8")
        return p
    m = {"nodes": nodes, "extractor": "graphify"}
    if verdict is not None:
        m["verdict"] = verdict
    if reasons is not None:
        m["reasons"] = list(reasons)
    p.write_text(json.dumps(m), encoding="utf-8")
    return p


def _heal_status(home: Path, root: Path, *, neo4j_ok=True, mcp_ok=True, reason="", age_vs_manifest=5):
    """Write <home>/run/heal-<key>.json (the contract provision.heal writes). `age_vs_manifest` > 0
    makes it NEWER than the manifest (a current failure), < 0 OLDER (superseded by a later sync)."""
    key = PR.project_key(root)
    p = home / "run" / f"heal-{key}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"ts": time.time(), "neo4j_ok": neo4j_ok, "mcp_ok": mcp_ok,
                             "reason": reason}), encoding="utf-8")
    man = root / ".deepmap" / "graph_build.json"
    base = man.stat().st_mtime if man.exists() else time.time()
    t = base + age_vs_manifest
    os.utime(p, (t, t))
    return p


def _env(home: Path, extra=None) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in _SCRUB}
    env["DEEPMAP_HOME"] = str(home)
    if extra:
        env.update(extra)
    return env


def _run(hook: str, payload: dict, home: Path, *, extra=None, utf8_off=True, cwd=None):
    """Run a REAL hook subprocess with the payload serialised like Node's JSON.stringify (raw UTF-8,
    no \\u escapes), UTF-8 mode OFF by default (the state Claude Code launches hooks in)."""
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    argv = [PY] + (["-X", "utf8=0"] if utf8_off else []) + [str(HOOKS / f"{hook}.py")]
    p = subprocess.run(argv, input=data, capture_output=True, env=_env(home, extra),
                       cwd=str(cwd or home), timeout=120)
    return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")


def _ctx(out: str) -> str:
    try:
        return json.loads(out.strip().splitlines()[-1])["hookSpecificOutput"]["additionalContext"]
    except Exception:
        return ""


def _stub_plugin_root(parent: Path, record: Path) -> Path:
    """A fake CLAUDE_PLUGIN_ROOT whose build/provision.py only records argv + PYTHONUTF8 + utf8_mode."""
    pr = parent / "plugin"
    (pr / "build").mkdir(parents=True, exist_ok=True)
    (pr / "build" / "provision.py").write_text(
        "import json, os, sys\n"
        f"rec = {str(record)!r}\n"
        "with open(rec, 'a', encoding='utf-8') as f:\n"
        "    f.write(json.dumps({'argv': sys.argv[1:], 'PYTHONUTF8': os.environ.get('PYTHONUTF8'),\n"
        "                        'utf8_mode': sys.flags.utf8_mode}, ensure_ascii=True) + '\\n')\n",
        encoding="utf-8")
    return pr


def _wait_records(record: Path, n: int, budget: float = 20.0) -> list:
    deadline = time.time() + budget
    while time.time() < deadline:
        try:
            lines = [json.loads(x) for x in record.read_text(encoding="utf-8").splitlines() if x.strip()]
            if len(lines) >= n:
                return lines
        except Exception:
            pass
        time.sleep(0.1)
    try:
        return [json.loads(x) for x in record.read_text(encoding="utf-8").splitlines() if x.strip()]
    except Exception:
        return []


# --------------------------------------------------------------------------- A2
def gate_a2_non_ascii_payload() -> None:
    print("\n-- A2: a project under a NON-ASCII path -> every hook still fires (UTF-8 mode off) --")
    variants = [("cp1252 stdin (deterministic)", {"PYTHONIOENCODING": "cp1252"}),
                ("the box's own locale", {})]
    for label, extra in variants:
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as th:
            home = Path(th)
            try:
                root = (Path(td) / "ready_café_פרויקט")
                root.mkdir()
                root = root.resolve()
            except (OSError, UnicodeError) as exc:
                check(f"({label}: non-ASCII dir creatable)", True, f"skipped — {exc}")
                continue
            _manifest(root)
            sid = "s-" + str(abs(hash(label)))
            before = sorted(x.name for x in Path(td).iterdir())

            rc, out, err = _run("deepmap_presearch_nudge", {"cwd": str(root), "session_id": sid,
                                "tool_name": "Grep", "tool_input": {"pattern": "x"}}, home, extra=extra)
            check(f"{label}: nudge fires for the non-ASCII project", rc == 0 and "deepmap_search" in _ctx(out),
                  f"rc={rc} out={out[:120]!r} err={err[-160:]!r}")

            rc, out, err = _run("deepmap_postedit_syncflag", {"cwd": str(root), "session_id": sid,
                                "tool_name": "Edit", "tool_input": {"file_path": str(root / "a.py")}},
                                home, extra=extra)
            check(f"{label}: sync flag fires", rc == 0 and "/deep-map:refresh" in _ctx(out),
                  f"rc={rc} out={out[:120]!r} err={err[-160:]!r}")

            rc, out, err = _run("deepmap_sessionstart_notify", {"cwd": str(root), "session_id": sid},
                                home, extra=extra)
            check(f"{label}: notify announces the READY graph", rc == 0 and "deepmap_search" in _ctx(out),
                  f"rc={rc} out={out[:120]!r} err={err[-160:]!r}")

            record = Path(th) / "heal_record.jsonl"
            plugin = _stub_plugin_root(Path(th), record)
            rc, out, err = _run("deepmap_sessionstart_heal", {"cwd": str(root), "session_id": sid}, home,
                                extra=dict(extra, CLAUDE_PLUGIN_ROOT=str(plugin)))
            recs = _wait_records(record, 1)
            argv = recs[0]["argv"] if recs else []
            check(f"{label}: heal spawns `provision.py heal <the REAL root>`",
                  rc == 0 and argv[:1] == ["heal"] and len(argv) >= 2 and Path(argv[1]) == root,
                  f"rc={rc} recs={recs} err={err[-160:]!r}")

            rc, out, err = _run("deepmap_access_hook", {"cwd": str(root), "session_id": sid,
                                "tool_name": "Read", "tool_input": {"file_path": str(root / "a.py")}},
                                home, extra=dict(extra, DEEPMAP_RETRIEVAL_LOG="1"))
            log = root / ".deepmap" / "retrieval_log.jsonl"
            check(f"{label}: access hook logs into the REAL project dir", rc == 0 and log.is_file(),
                  f"rc={rc} err={err[-200:]!r}")
            after = sorted(x.name for x in Path(td).iterdir())
            check(f"{label}: no mojibake sibling directory was created", after == before, f"{after} vs {before}")


# --------------------------------------------------------------------------- H1 (+ FAILED heal)
class _FakePopen:
    calls: list = []

    def __init__(self, argv, **kw):
        _FakePopen.calls.append((list(argv), kw))


def _heal_inproc(HK, root: Path, env: dict) -> list:
    saved_env = {k: os.environ.get(k) for k in set(env) | set(_SCRUB) | {"CLAUDE_PLUGIN_ROOT"}}
    saved_popen, saved_stdin = HK.subprocess.Popen, sys.stdin
    _FakePopen.calls = []
    try:
        for k in _SCRUB:
            os.environ.pop(k, None)
        os.environ["CLAUDE_PLUGIN_ROOT"] = str(REPO)
        os.environ.update(env)
        HK.subprocess.Popen = _FakePopen
        sys.stdin = io.StringIO(json.dumps({"cwd": str(root)}))
        rc = HK.main()
        return [rc] + list(_FakePopen.calls)
    finally:
        HK.subprocess.Popen, sys.stdin = saved_popen, saved_stdin
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def gate_h1_heal_optout_and_failed() -> None:
    print("\n-- H1: DEEPMAP_HOOKS=0 / DEEPMAP_HEAL=0 disable heal; a FAILED graph is never revived --")
    HK = _load("deepmap_sessionstart_heal")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td).resolve()
        _manifest(root)
        res = _heal_inproc(HK, root, {})
        check("READY + no opt-out -> exactly one spawn (heal)", res[0] == 0 and len(res) == 2
              and res[1][0][2] == "heal", str(res))
        spawn_env = res[1][1].get("env") if len(res) > 1 else None
        check("H5: the heal child runs in UTF-8 mode (env PYTHONUTF8=1)",
              isinstance(spawn_env, dict) and spawn_env.get("PYTHONUTF8") == "1", str(spawn_env and
                                                                                     spawn_env.get("PYTHONUTF8")))
        for env in ({"DEEPMAP_HOOKS": "0"}, {"DEEPMAP_HOOKS": "off"}, {"DEEPMAP_HEAL": "0"},
                    {"DEEPMAP_HEAL": "false"}, {"DEEPMAP_HOOKS": "0", "DEEPMAP_HIBERNATE": "1"}):
            res = _heal_inproc(HK, root, env)
            check(f"{env} -> exit 0, NOTHING spawned", res[0] == 0 and len(res) == 1, str(res))
        res = _heal_inproc(HK, root, {"DEEPMAP_HIBERNATE": "1"})
        check("DEEPMAP_HIBERNATE=1 (no opt-out) -> heal + hibernate still spawned", len(res) == 3, str(res))

    for label, kw in (("verdict FAILED", {"verdict": "FAILED", "nodes": 0}),
                      ("0 nodes (DEGRADED)", {"verdict": "DEGRADED", "nodes": 0}),
                      ("0 nodes, no verdict", {"verdict": None, "nodes": 0})):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            _manifest(root, **kw)
            res = _heal_inproc(HK, root, {})
            check(f"{label} -> heal NOT spawned (setup never wired this project)",
                  res[0] == 0 and len(res) == 1, str(res))
    for label, raw in (("legacy {} manifest", "{}"), ("corrupt manifest", "{not json")):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            _manifest(root, raw=raw)
            res = _heal_inproc(HK, root, {})
            check(f"{label} -> still revived (unknown health is not a proven failure)",
                  res[0] == 0 and len(res) == 2, str(res))


# --------------------------------------------------------------------------- H2 (+ git-hook claim)
def gate_h2_syncflag_every() -> None:
    print("\n-- H2: DEEPMAP_SYNC_FLAG_EVERY=N schedule (N=1 means EVERY edit) --")
    SF = _load("deepmap_postedit_syncflag")
    saved = os.environ.get("DEEPMAP_SYNC_FLAG_EVERY")
    try:
        for every, want in (("1", [True] * 7), ("2", [True, False] * 3 + [True]),
                            ("3", [True, False, False, True, False, False, True]),
                            ("", [True] + [False] * 6)):
            if every:
                os.environ["DEEPMAP_SYNC_FLAG_EVERY"] = every
            else:
                os.environ.pop("DEEPMAP_SYNC_FLAG_EVERY", None)
            got = [SF._should_fire(c) for c in range(1, 8)]
            check(f"EVERY={every or 'unset'} -> {want}", got == want, str(got))
        os.environ["DEEPMAP_SYNC_FLAG_EVERY"] = "1"
        check("an unreadable-state sentinel (-1) never fires, even for EVERY=1", SF._should_fire(-1) is False)
    finally:
        if saved is None:
            os.environ.pop("DEEPMAP_SYNC_FLAG_EVERY", None)
        else:
            os.environ["DEEPMAP_SYNC_FLAG_EVERY"] = saved
    with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as th:
        root = Path(td).resolve()
        _manifest(root)
        fired = []
        for _ in range(3):
            rc, out, _e = _run("deepmap_postedit_syncflag", {"cwd": str(root), "session_id": "e1",
                               "tool_name": "Edit", "tool_input": {"file_path": "a.py"}}, Path(th),
                               extra={"DEEPMAP_SYNC_FLAG_EVERY": "1"})
            fired.append(bool(_ctx(out)))
        check("real hook, EVERY=1: reminds after each of 3 edits", fired == [True, True, True], str(fired))


def gate_syncflag_git_hook_claim() -> None:
    print("\n-- sync flag: the 'a commit re-syncs' claim appears ONLY with DeepMap's git hook installed --")
    git = shutil.which("git")
    with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as th:
        root = Path(td).resolve()
        _manifest(root)
        rc, out, _e = _run("deepmap_postedit_syncflag", {"cwd": str(root), "session_id": "g1",
                           "tool_name": "Edit", "tool_input": {}}, Path(th))
        ctx = _ctx(out)
        check("no git repo -> reminder fires WITHOUT a git-hook auto-sync claim",
              "/deep-map:refresh" in ctx and "git hook" not in ctx.lower(), ctx)
    if not git:
        check("(git on PATH for the git-hook claim)", True, "skipped — git absent")
        return
    with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as th:
        root = Path(td).resolve()
        subprocess.run([git, "init", "-q"], cwd=str(root), capture_output=True, timeout=60)
        _manifest(root)
        hook = root / ".git" / "hooks" / "post-commit"
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text("#!/bin/sh\necho unrelated\n", encoding="utf-8")
        rc, out, _e = _run("deepmap_postedit_syncflag", {"cwd": str(root), "session_id": "g2",
                           "tool_name": "Edit", "tool_input": {}}, Path(th))
        check("a FOREIGN post-commit hook -> no auto-sync claim", "git hook" not in _ctx(out).lower(), _ctx(out))
        # ROUND-8 J1: the marker alone is no proof — the claim needs a DeepMap line that syncs THIS project
        hook.write_text("#!/bin/sh\n# >>> deepmap >>>\n( nohup python x sync y & ) || true   # deepmap\n",
                        encoding="utf-8")
        rc, out, _e = _run("deepmap_postedit_syncflag", {"cwd": str(root), "session_id": "g3",
                           "tool_name": "Edit", "tool_input": {}}, Path(th))
        check("a marker line that syncs nothing of this project -> no auto-sync claim (ROUND-8 J1)",
              "git hook" not in _ctx(out).lower(), _ctx(out))
        cli = (BUILD / "deepmap_cli.py").as_posix()
        # ROUND-11 L3: the block names the RUNNING interpreter (single-quoted, forward slashes — as
        # provision._git_hook_block writes it), not a bare `python`: on a python3-only host (stock Debian / Ubuntu /
        # macOS) the syncflag rightly finds that `python` can never start and makes no claim.
        py = "'" + Path(sys.executable).as_posix().replace("'", "'\\''") + "'"
        hook.write_text("#!/bin/sh\n# >>> deepmap (managed; existing hook preserved on chain) >>>\n"
                        f"( PYTHONUTF8=1 nohup {py} '{cli}' sync '{root.as_posix()}' >/dev/null 2>&1 & ) "
                        "|| true   # deepmap\n# <<< deepmap <<<\n", encoding="utf-8")
        if os.name != "nt":
            os.chmod(hook, 0o755)
        rc, out, _e = _run("deepmap_postedit_syncflag", {"cwd": str(root), "session_id": "g4",
                           "tool_name": "Edit", "tool_input": {}}, Path(th))
        check("DeepMap's post-commit hook installed (syncs this project) -> the claim is made",
              "git hook" in _ctx(out).lower(), _ctx(out))


# --------------------------------------------------------------------------- H3
def gate_h3_nudge_health() -> None:
    print("\n-- H3: the pre-search nudge never steers toward a failed/degraded/unreachable graph --")
    grep = {"tool_name": "Grep", "tool_input": {"pattern": "x"}}

    def nudge(root, home, sid):
        rc, out, err = _run("deepmap_presearch_nudge", dict(grep, cwd=str(root), session_id=sid), home)
        return rc, _ctx(out), err

    cases = [("READY", dict(verdict="READY"), True),
             ("FAILED", dict(verdict="FAILED", nodes=0), False),
             ("legacy (no verdict)", dict(verdict=None), False),
             ("DEGRADED without a coverage reason", dict(verdict="DEGRADED",
                                                         reasons=["VECTOR SEARCH DISABLED"]), False)]
    for label, kw, want in cases:
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as th:
            root = Path(td).resolve()
            _manifest(root, **kw)
            rc, ctx, err = nudge(root, Path(th), "n1")
            check(f"{label} -> {'nudges' if want else 'silent'}", rc == 0 and (bool(ctx) == want), f"{ctx!r} {err[-120:]}")
    with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as th:
        root = Path(td).resolve()
        _manifest(root, raw="{not json")
        rc, ctx, err = nudge(root, Path(th), "n1")
        check("corrupt manifest -> silent", rc == 0 and ctx == "", ctx)
    with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as th:
        root = Path(td).resolve()
        _manifest(root, verdict="DEGRADED", reasons=[_PASCAL])
        rc, ctx, err = nudge(root, Path(th), "n1")
        check("DEGRADED + LOW COVERAGE -> a coverage-aware nudge naming the gap",
              rc == 0 and "Pascal/Delphi" in ctx, ctx)
        check("...which routes that layer to Grep (not indexed != absent)",
              "grep" in ctx.lower() and "not indexed" in ctx.lower(), ctx)

    for label, kw, age, want in (("heal: database unreachable (current)", dict(neo4j_ok=False), 5, False),
                                 ("heal: MCP servers failed (current)", dict(mcp_ok=False), 5, False),
                                 ("heal: failure OLDER than the last sync (superseded)",
                                  dict(neo4j_ok=False), -60, True),
                                 ("heal: healthy", {}, 5, True)):
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as th:
            root = Path(td).resolve()
            _manifest(root)
            _heal_status(Path(th), root, reason="PREFLIGHT FAILED: port 7813 in use",
                         age_vs_manifest=age, **kw)
            rc, ctx, err = nudge(root, Path(th), "n2")
            check(f"READY + {label} -> {'nudges' if want else 'silent'}", rc == 0 and bool(ctx) == want,
                  f"{ctx!r} {err[-160:]}")


# --------------------------------------------------------------------------- H4
def _notify_inproc(NT, payload: dict, home: Path, *, listening=None, env=None) -> str:
    """Run notify in-process with faked stdin/stdout; `listening` stubs the MCP port probe."""
    saved = {k: os.environ.get(k) for k in set(_SCRUB) | {"DEEPMAP_HOME"} | set(env or {})}
    saved_io = (sys.stdin, sys.stdout)
    hs = NT._hs
    saved_probe = getattr(hs, "port_listening", None)
    try:
        for k in _SCRUB:
            os.environ.pop(k, None)
        os.environ["DEEPMAP_HOME"] = str(home)
        os.environ.update(env or {})
        if listening is not None and saved_probe is not None:
            hs.port_listening = lambda port, *a, **k: listening
        sys.stdin = io.StringIO(json.dumps(payload))
        sys.stdout = io.StringIO()
        NT.main()
        return sys.stdout.getvalue()
    finally:
        sys.stdin, sys.stdout = saved_io
        if saved_probe is not None:
            hs.port_listening = saved_probe
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _mcp_json(root: Path, s=18911, e=18912) -> None:
    (root / ".mcp.json").write_text(json.dumps({"mcpServers": {
        "deepmap-search": {"type": "http", "url": f"http://127.0.0.1:{s}/mcp"},
        "deepmap-embed": {"type": "http", "url": f"http://127.0.0.1:{e}/mcp"}}}), encoding="utf-8")


def gate_h4_notify_heal_status() -> None:
    print("\n-- H4: notify surfaces a failed last heal + the reconnect-via-/mcp race --")
    NT = _load("deepmap_sessionstart_notify")
    check("hook_state exposes a stubbable port_listening probe", hasattr(NT._hs, "port_listening"))
    reason = "PREFLIGHT FAILED: port 7813 (Bolt) is already in use"
    with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as th:
        root, home = Path(td).resolve(), Path(th)
        _manifest(root)
        _heal_status(home, root, neo4j_ok=False, mcp_ok=True, reason=reason)
        ctx = _ctx(_notify_inproc(NT, {"cwd": str(root), "session_id": "h1"}, home, listening=True))
        check("READY + current heal failure -> ONE line: database NOT reachable",
              "not reachable" in ctx.lower(), ctx)
        check("...tells the agent to run /deep-map:setup", "/deep-map:setup" in ctx, ctx)
        check("...carries heal's reason", "7813" in ctx, ctx)
        check("...and does NOT announce the graph as ACTIVE / preferred",
              "is ACTIVE" not in ctx and "prefer" not in ctx.lower(), ctx)
        again = _notify_inproc(NT, {"cwd": str(root), "session_id": "h1"}, home, listening=True)
        check("same session -> silent (once per session)", again.strip() == "", again)
    with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as th:
        root, home = Path(td).resolve(), Path(th)
        _manifest(root)
        _heal_status(home, root, neo4j_ok=True, mcp_ok=False, reason="deepmap-search failed to start")
        ctx = _ctx(_notify_inproc(NT, {"cwd": str(root), "session_id": "h2"}, home, listening=True))
        check("READY + MCP servers failed at the last heal -> says so + /deep-map:setup",
              "/deep-map:setup" in ctx and "is ACTIVE" not in ctx, ctx)
    with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as th:
        root, home = Path(td).resolve(), Path(th)
        _manifest(root)
        _heal_status(home, root, neo4j_ok=False, reason=reason, age_vs_manifest=-60)
        ctx = _ctx(_notify_inproc(NT, {"cwd": str(root), "session_id": "h3"}, home, listening=True))
        check("a heal failure OLDER than the last sync is superseded -> the normal READY announce",
              "is ACTIVE" in ctx and "not reachable" not in ctx.lower(), ctx)

    # The ECONNREFUSED-at-startup race: servers down at SessionStart, heal reviving them in background.
    with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as th:
        root, home = Path(td).resolve(), Path(th)
        _manifest(root)
        _mcp_json(root)
        ctx = _ctx(_notify_inproc(NT, {"cwd": str(root), "session_id": "r1"}, home, listening=False))
        check("servers DOWN at session start -> announce + reconnect via /mcp",
              "is ACTIVE" in ctx and "/mcp" in ctx and "reconnect" in ctx.lower(), ctx)
        ctx = _ctx(_notify_inproc(NT, {"cwd": str(root), "session_id": "r2"}, home, listening=True))
        check("servers UP -> no reconnect note", "is ACTIVE" in ctx and "/mcp" not in ctx, ctx)
        ctx = _ctx(_notify_inproc(NT, {"cwd": str(root), "session_id": "r3"}, home, listening=False,
                                  env={"DEEPMAP_HEAL": "0"}))
        check("servers DOWN + heal disabled -> 'not running', /deep-map:setup (no false 'being revived')",
              "/deep-map:setup" in ctx and "revived" not in ctx.lower(), ctx)
    with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as th:
        root, home = Path(td).resolve(), Path(th)
        _manifest(root, verdict="DEGRADED", reasons=[_PASCAL])
        _mcp_json(root)
        ctx = _ctx(_notify_inproc(NT, {"cwd": str(root), "session_id": "r4"}, home, listening=False))
        check("DEGRADED coverage warning also carries the reconnect note", "Pascal/Delphi" in ctx
              and "/mcp" in ctx, ctx)
    with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as th:
        root, home = Path(td).resolve(), Path(th)
        _manifest(root)   # no .mcp.json, no registry entry -> ports unknown
        ctx = _ctx(_notify_inproc(NT, {"cwd": str(root), "session_id": "r5"}, home, listening=False))
        check("ports unknown -> the plain announce (no guessed reconnect note)",
              "is ACTIVE" in ctx and "/mcp" not in ctx, ctx)


def gate_h4_writer_contract() -> None:
    print("\n-- H4 contract: the file provision.heal REALLY writes is the one the hooks read --")
    spec = importlib.util.spec_from_file_location("dmhr_provision", BUILD / "provision.py")
    P = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(P)
    writer = getattr(P, "write_heal_status", None)
    check("provision exports write_heal_status", callable(writer))
    if not callable(writer):
        return
    NT = _load("deepmap_sessionstart_notify")
    saved_home = os.environ.get("DEEPMAP_HOME")
    with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as th:
        root, home = Path(td).resolve(), Path(th)
        _manifest(root)
        time.sleep(0.05)
        os.environ["DEEPMAP_HOME"] = str(home)
        try:
            writer(str(root), {"neo4j_ok": False, "mcp_ok": False,
                               "reason": "this project's Neo4j (bolt://localhost:7813) is not reachable — "
                                         "run /deep-map:setup"})
            sh = _load("deepmap_hook_state")
            prob = sh.heal_problem(root)
        finally:
            if saved_home is None:
                os.environ.pop("DEEPMAP_HOME", None)
            else:
                os.environ["DEEPMAP_HOME"] = saved_home
        check("hook_state.heal_problem reads provision's real heal file -> ('neo4j', reason)",
              isinstance(prob, tuple) and prob[0] == "neo4j" and "7813" in prob[1], str(prob))
        ctx = _ctx(_notify_inproc(NT, {"cwd": str(root), "session_id": "c1"}, home, listening=True))
        check("...and notify turns it into the not-reachable line", "not reachable" in ctx.lower()
              and "7813" in ctx, ctx)
        os.environ["DEEPMAP_HOME"] = str(home)
        try:
            writer(str(root), {"neo4j_ok": True, "mcp_ok": True, "reason": None,
                               "mcp_started": ["deepmap-search"]})
            prob = _load("deepmap_hook_state").heal_problem(root)
        finally:
            if saved_home is None:
                os.environ.pop("DEEPMAP_HOME", None)
            else:
                os.environ["DEEPMAP_HOME"] = saved_home
        check("a healthy heal file -> no problem", prob is None, str(prob))


# --------------------------------------------------------------------------- H5 + command docs
_PY_ENTRY = re.compile(r'python\s+"?\$\{CLAUDE_PLUGIN_ROOT\}/build/')


def _commands_of(md: str) -> list:
    """Every shell command in a command .md: fenced ```bash lines (with `\\` continuations joined)
    and inline `code` spans (which may wrap across lines)."""
    cmds = []
    fenced = re.findall(r"```(?:bash|sh)?\n(.*?)```", md, flags=re.S)
    for block in fenced:
        for line in block.replace("\\\n", " ").splitlines():
            if line.strip():
                cmds.append(" ".join(line.split()))
    rest = re.sub(r"```.*?```", "", md, flags=re.S)
    for span in re.findall(r"`([^`]+)`", rest):
        cmds.append(" ".join(span.split()))
    return cmds


def _allowed_prefixes(md: str) -> list:
    m = re.search(r"^allowed-tools:\s*(.*)$", md, flags=re.M)
    if not m:
        return []
    out = []
    for pat in re.findall(r"Bash\(([^)]*)\)", m.group(1)):
        pat = pat[:-2] if pat.endswith(":*") else (pat[:-1] if pat.endswith("*") else pat)
        out.append(pat.replace('"', ""))
    return out


def gate_h5_utf8_entry_points() -> None:
    print("\n-- H5: hooks.json + commands/*.md run every python entry point in UTF-8 mode --")
    data = json.loads((HOOKS / "hooks.json").read_text(encoding="utf-8"))
    cmds = [h.get("command", "") for groups in data.get("hooks", {}).values() for g in groups
            for h in g.get("hooks", [])]
    check("hooks.json registers hooks", len(cmds) >= 4, str(cmds))
    bad = [c for c in cmds if not ("-X utf8" in c or "PYTHONUTF8=1" in c)]
    check("every hooks.json command runs Python in UTF-8 mode", bad == [], str(bad))
    check("...without a shell-specific env-assignment prefix (works under any hook shell)",
          all(c.lstrip().startswith("python") for c in cmds), str(cmds))

    for md_path in sorted(COMMANDS.glob("*.md")):
        md = md_path.read_text(encoding="utf-8")
        prefixes = _allowed_prefixes(md)
        entries = [c for c in _commands_of(md) if _PY_ENTRY.search(c)]
        if not entries:
            continue
        missing = [c for c in entries if "PYTHONUTF8=1" not in c.split("python")[0]]
        check(f"{md_path.name}: every python entry point sets PYTHONUTF8=1", missing == [], str(missing))
        unmatched = [c for c in entries if not any(c.replace('"', "").startswith(p) for p in prefixes)]
        check(f"{md_path.name}: each entry point still matches an allowed-tools prefix", unmatched == [],
              f"{unmatched} vs {prefixes}")


def gate_command_doc_drift() -> None:
    print("\n-- commands/*.md: honest impact + status guidance (2026-09 doc drift) --")
    impact = (COMMANDS / "impact.md").read_text(encoding="utf-8")
    check("impact.md no longer quotes a 'No callers found' string the CLI never prints",
          "No callers found" not in impact)
    check("impact.md: an empty result is NOT presented as proof nothing depends on it",
          "not prove" in impact.lower() or "does not mean" in impact.lower(), impact[-600:])
    ex = re.findall(r"--edges\s+([A-Z_,]+)", impact)
    narrowing = [e for e in ex if not {"ROUTES_TO", "CASCADES", "RENDERS"} <= set(e.split(","))]
    check("impact.md --edges examples keep the default runtime edges (ROUTES_TO/CASCADES/RENDERS...)",
          ex and narrowing == [], str(ex))
    check("impact.md says --edges REPLACES the default set (and recommends the rails profile)",
          "replace" in impact.lower() and "DEEPMAP_IMPACT_PROFILE=rails" in impact, impact[-800:])
    status = (COMMANDS / "status.md").read_text(encoding="utf-8")
    bare = re.findall(r"`deepmap [a-z-]+", status)
    check("status.md names no bare `deepmap <verb>` executable (none is installed)", bare == [], str(bare))
    check("status.md's lifecycle hints are runnable provision.py commands",
          "provision.py\" hibernate" in status and "provision.py\" instance-rm" in status, status[-500:])


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 68)
    print("DeepMap — hook + command entry-point reliability (A2/H1-H5, no Neo4j)")
    print("=" * 68)
    gate_a2_non_ascii_payload()
    gate_h1_heal_optout_and_failed()
    gate_h2_syncflag_every()
    gate_syncflag_git_hook_claim()
    gate_h3_nudge_health()
    gate_h4_notify_heal_status()
    gate_h4_writer_contract()
    gate_h5_utf8_entry_points()
    gate_command_doc_drift()
    print("\n" + "=" * 68)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
