#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ROUND-7 W1 gates: the hooks honour a persisted setup blocker, the same-session heal window, staleness for
verdict-less manifests, the .mcp.json ownership rule, and the W1 doc/hint drift (hooks/*, port_registry, commands,
skills/deep-map/SKILL.md).

  A  I4 — setup's MCP BLOCKER is durable and the hooks honour it. setup could not restart a DeepMap server that holds
     this project's MCP port (it may answer from ANOTHER graph) and says FAILED, but the next SessionStart heal found
     that listener "already listening" and wrote mcp_ok:true, so notify announced READY over it (round-6 review).
     port_registry now persists the blocker (<DEEPMAP_HOME>/run/setup-blocked-<key>.json:
     write/read/clear_setup_blocker); the hooks treat it — and a heal status saying the same thing (refusal_kind
     "mcp-blocked", or mcp_ok:false with mcp_unverified ports) — as FINAL: never superseded by a port probe (the held
     port listens by definition) or by a newer manifest (a refresh does not free the port). Only setup clearing it, or
     a newer heal that started the blocked servers ITSELF, lifts it. notify says "must NOT be used"; the nudge and the
     sync flag stay silent. The E1 key migration carries the file along.
  B  T9#1 — the same-session heal window. notify runs WHILE this session's heal runs, so it read the PREVIOUS heal's
     status and could announce READY + "do not conclude DeepMap is broken" in the very session whose heal then failed.
     Now: (1) with this project's MCP ports down and a heal coming, notify waits a short, bounded time
     (DEEPMAP_NOTIFY_HEAL_WAIT, default 3 s, max 6) for this session's heal and reports ITS outcome; (2) it records
     what it told the session (<root>/.deepmap/session_health.json) and the pre-search nudge surfaces a later outcome
     that contradicts it — ONCE: a failure after a READY announce ("could not start ... /deep-map:setup"), a recovery
     after a "not reachable" line, servers now up after a reconnect note; (3) the reconnect note no longer says
     "do not conclude DeepMap is broken" unconditionally; (4) the heal hook logs its child's output to
     <root>/.deepmap/heal.log (bounded, rotated) instead of DEVNULL.
  C  S-4 / T9#6 — staleness beyond READY. A verdict-less (legacy) or non-coverage DEGRADED manifest whose HEAD moved
     now gets ONE caution line (never a steer toward the graph), and every stale note carries the commit distance.
     The READY nudge carries the stale note too.
  D  I4 (hook side of the doctor finding) — a .mcp.json pair the registry gives to ANOTHER project is not this
     project's: the hooks never probe it as theirs (port_registry.mcp_pair_claimed_by_other), and the agent
     is told the session's deepmap tools answer from THAT project's graph (notify: "must NOT be used"; the
     nudge and the sync flag stay silent). A BOM-prefixed .mcp.json is still read (T10d-11).
  E  Doc/hint drift in W1's files: SKILL.md (/deep-map:doctor not a bare `deepmap doctor`, empty/partial impact is not
     proof, /mcp reconnect, top_k widening, INFERRED is name-based, deepmap_impact's edge_types), impact.md (--limit
     counts rows, INFERRED wording), status.md (also runs the CLI status: owner/coverage/freshness), setup.md (the
     blocker is remembered until setup succeeds).

Hermetic: temp project dirs + a temp DEEPMAP_HOME + temp git repos; the MCP port probe is stubbed in-process; the heal
hook's provision is a recording stub (via CLAUDE_PLUGIN_ROOT) or a fake Popen. No Neo4j, no sockets opened by the
checks, no network, never touches ~/.deepmap or the committed .mcp.json. Exit 0 = GREEN.
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
import threading
import time
from pathlib import Path

# Auto-sync (DeepMap's background refresh at session start) is on by default in an attended session; this gate
# locks the hooks WITHOUT it, so it is pinned off for this process and every hook process the gate starts
# (tests/auto_sync_hooks_gates.py locks the hooks with it on).
os.environ["DEEPMAP_AUTO_SYNC"] = "0"
REPO = Path(os.environ.get("DMGATE_ROOT") or Path(__file__).resolve().parent.parent)
HOOKS = REPO / "hooks"
BUILD = REPO / "build"
COMMANDS = REPO / "commands"
SKILL = REPO / "skills" / "deep-map" / "SKILL.md"
PY = sys.executable
sys.path.insert(0, str(BUILD))
import port_registry as PR  # noqa: E402

_PASS = 0
_FAIL = 0
_SCRUB = ("PYTHONUTF8", "PYTHONIOENCODING", "DEEPMAP_HOOKS", "DEEPMAP_HEAL", "DEEPMAP_NUDGE",
          "DEEPMAP_SYNC_FLAG", "DEEPMAP_SYNC_FLAG_EVERY", "DEEPMAP_ANNOUNCE", "DEEPMAP_ONBOARD",
          "DEEPMAP_HIBERNATE", "DEEPMAP_RETRIEVAL_LOG", "CLAUDE_PROJECT_DIR", "DEEPMAP_PROJECT_ROOT",
          "NEO4J_URI", "DEEPMAP_LEARNING", "CLAUDE_PLUGIN_ROOT", "DEEPMAP_NOTIFY_HEAL_WAIT")
_GIT_ENV = {"GIT_AUTHOR_NAME": "g", "GIT_AUTHOR_EMAIL": "g@x", "GIT_COMMITTER_NAME": "g",
            "GIT_COMMITTER_EMAIL": "g@x"}
_PASCAL = ("LOW COVERAGE for Pascal/Delphi — 412 files on disk but only 0 extracted node(s); "
           "Pascal/Delphi code is effectively invisible to search/impact.")
_BLOCK_REASON = ("deepmap-search on 127.0.0.1:18911 is still served by a DeepMap MCP server (pid 4242) that setup "
                 "could not restart — an older DeepMap build started it (no owner record)")


def check(label: str, cond: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {label}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}{(' — ' + str(detail)[:600]) if detail else ''}")


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"dmr7_{name}_{time.monotonic_ns()}", HOOKS / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _tmpdir():
    return tempfile.TemporaryDirectory(ignore_cleanup_errors=True)


def _git(d: Path, *args) -> str:
    p = subprocess.run(["git", "-C", str(d), *args], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=60, env=dict(os.environ, **_GIT_ENV))
    return (p.stdout or "").strip()


def _repo(parent: Path, name: str = "proj") -> Path:
    d = parent / name
    d.mkdir(parents=True, exist_ok=True)
    _git(d, "init", "-q")
    _git(d, "commit", "-q", "--allow-empty", "-m", "c1")
    return d.resolve()


def _manifest(root: Path, verdict="READY", nodes=10, reasons=None, raw=None, **extra) -> Path:
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
    m.update(extra)
    p.write_text(json.dumps(m), encoding="utf-8")
    return p


def _heal_path(home: Path, root: Path) -> Path:
    return home / "run" / f"heal-{PR.project_key(root)}.json"


def _heal_status(home: Path, root: Path, *, ahead=5.0, mtime=None, **fields) -> Path:
    """Write the heal status provision.heal writes. `ahead` s newer than the manifest (a current status), or an
    explicit `mtime`."""
    p = _heal_path(home, root)
    p.parent.mkdir(parents=True, exist_ok=True)
    rec = {"ts": "2026-09-26T00:00:00Z", "ts_epoch": time.time(), "neo4j_ok": True, "mcp_ok": True,
           "reason": None, "mcp_started": [], "mcp_started_epoch": None, "refused": False, "refusal_kind": None,
           "mcp_stopped": [], "mcp_unverified": []}
    rec.update(fields)
    p.write_text(json.dumps(rec), encoding="utf-8")
    if mtime is None:
        man = root / ".deepmap" / "graph_build.json"
        mtime = (man.stat().st_mtime if man.exists() else time.time()) + ahead
    os.utime(p, (mtime, mtime))
    return p


def _register(home: Path, root: Path, *, search=18911, embed=18912, bolt=7811, http=7598, others=()) -> None:
    projects = {PR.project_key(root): {"root": str(root).replace("\\", "/"), "bolt_port": bolt, "http_port": http,
                                       "search_port": search, "embed_port": embed}}
    for key, ent in others:
        projects[key] = ent
    home.mkdir(parents=True, exist_ok=True)
    (home / "registry.json").write_text(json.dumps({"version": 1, "kind": "deepmap_port_registry",
                                                    "projects": projects}), encoding="utf-8")


def _mcp_json(root: Path, s=18911, e=18912) -> None:
    (root / ".mcp.json").write_text(json.dumps({"mcpServers": {
        "deepmap-search": {"type": "http", "url": f"http://127.0.0.1:{s}/mcp"},
        "deepmap-embed": {"type": "http", "url": f"http://127.0.0.1:{e}/mcp"}}}), encoding="utf-8")


def _ctx(out: str) -> str:
    try:
        return json.loads(out.strip().splitlines()[-1])["hookSpecificOutput"]["additionalContext"]
    except Exception:
        return ""


class _Env:
    """Scrub the knobs, point DEEPMAP_HOME at `home`, apply `extra`; restore on exit."""

    def __init__(self, home: Path, extra=None):
        self.home, self.extra = home, dict(extra or {})

    def __enter__(self):
        self.saved = {k: os.environ.get(k) for k in set(_SCRUB) | {"DEEPMAP_HOME"} | set(self.extra)}
        for k in _SCRUB:
            os.environ.pop(k, None)
        os.environ["DEEPMAP_HOME"] = str(self.home)
        os.environ.update(self.extra)
        return self

    def __exit__(self, *exc):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _inproc(mod, payload: dict, home: Path, *, up=(), env=None) -> str:
    """Run a hook's main() in-process: faked stdin/stdout, `up` = the ports the (stubbed) probe sees listening."""
    hs = getattr(mod, "_hs", None)
    saved_io = (sys.stdin, sys.stdout)
    saved_probe = getattr(hs, "port_listening", None)
    with _Env(home, env):
        try:
            if hs is not None and saved_probe is not None:
                ups = {int(p) for p in up}
                hs.port_listening = lambda port, host="127.0.0.1", timeout=None: int(port) in ups
            sys.stdin = io.StringIO(json.dumps(payload))
            sys.stdout = io.StringIO()
            mod.main()
            return sys.stdout.getvalue()
        finally:
            sys.stdin, sys.stdout = saved_io
            if hs is not None and saved_probe is not None:
                hs.port_listening = saved_probe


def _hs_call(fn, home: Path, *args, up=(), env=None, **kw):
    """Call a deepmap_hook_state function with the probe stubbed and DEEPMAP_HOME = home."""
    hs = _load("deepmap_hook_state")
    ups = {int(p) for p in up}
    hs.port_listening = lambda port, host="127.0.0.1", timeout=None: int(port) in ups
    with _Env(home, env):
        return getattr(hs, fn)(*args, **kw), hs


# =========================================================================== A: I4 setup blocker
def gate_a_setup_blocker() -> None:
    print("\n-- A (I4): setup's MCP blocker is persisted and FINAL for the hooks --")
    for fn in ("setup_blocker_path", "write_setup_blocker", "read_setup_blocker", "clear_setup_blocker",
               "mcp_pair_claimed_by_other"):
        check(f"port_registry exposes {fn}()", callable(getattr(PR, fn, None)))
    if not callable(getattr(PR, "write_setup_blocker", None)):
        return
    with _tmpdir() as tw, _tmpdir() as th:
        home, root = Path(th), Path(tw).resolve()
        _manifest(root)
        env = {"DEEPMAP_HOME": str(home)}
        path = PR.setup_blocker_path(root, env=env)
        check("the blocker lives at <DEEPMAP_HOME>/run/setup-blocked-<project_key>.json",
              path == home / "run" / f"setup-blocked-{PR.project_key(root)}.json", str(path))
        ok = PR.write_setup_blocker(root, reason=_BLOCK_REASON, servers=["deepmap-search"], ports=[18911],
                                    pids=[4242], env=env)
        rec = PR.read_setup_blocker(root, env=env)
        check("write_setup_blocker -> True and the record reads back (reason, servers, ports, ts_epoch)",
              ok is True and isinstance(rec, dict) and rec.get("reason") == _BLOCK_REASON
              and rec.get("servers") == ["deepmap-search"] and rec.get("ports") == [18911]
              and isinstance(rec.get("ts_epoch"), (int, float)), str(rec))
        check("...written as UTF-8 JSON with LF (no CR)", b"\r" not in path.read_bytes())

        # heal ran AFTER setup's blocker and found the listener "already listening": mcp_ok true, nothing started
        _heal_status(home, root, mtime=time.time() + 30, neo4j_ok=True, mcp_ok=True, mcp_started=[])
        _mcp_json(root)
        prob, _hs = _hs_call("heal_problem", home, root, up=(18911, 18912))
        check("a healthy NEWER heal status + listening ports do NOT lift the blocker -> ('blocked', reason)",
              isinstance(prob, tuple) and prob[0] == "blocked" and "18911" in prob[1], str(prob))
        # a refresh since does not free the port either
        _manifest(root)
        os.utime(root / ".deepmap" / "graph_build.json", (time.time() + 60, time.time() + 60))
        prob, _hs = _hs_call("heal_problem", home, root, up=(18911, 18912))
        check("a NEWER manifest (a refresh) does not lift it either", isinstance(prob, tuple)
              and prob[0] == "blocked", str(prob))
        check("heal_problem_superseded never supersedes 'blocked'",
              _hs_call("heal_problem_superseded", home, root, ("blocked", "x"), up=(18911, 18912))[0] is False)

        NT = _load("deepmap_sessionstart_notify")
        ctx = _ctx(_inproc(NT, {"cwd": str(root), "session_id": "b1"}, home, up=(18911, 18912)))
        check("notify: READY + blocker -> the tools must NOT be used, names the held port, /deep-map:setup",
              "must not be used" in ctx.lower() and "18911" in ctx and "/deep-map:setup" in ctx, ctx)
        check("...and never the READY announce / a preference steer", "is ACTIVE" not in ctx
              and "prefer" not in ctx.lower(), ctx)
        check("...and says their answers may come from ANOTHER graph", "another" in ctx.lower(), ctx)
        NU = _load("deepmap_presearch_nudge")
        out = _inproc(NU, {"cwd": str(root), "session_id": "b2", "tool_name": "Grep",
                           "tool_input": {"pattern": "x"}}, home, up=(18911, 18912))
        check("nudge: blocked -> silent (never steer toward that server)", _ctx(out) == "", out)
        SF = _load("deepmap_postedit_syncflag")
        out = _inproc(SF, {"cwd": str(root), "session_id": "b3", "tool_name": "Edit",
                           "tool_input": {"file_path": str(root / "a.py")}}, home, up=(18911, 18912))
        check("sync flag: blocked -> silent ('run /deep-map:refresh' would steer toward that server)",
              _ctx(out) == "", out)

        # the ONE hook-side lift: a newer heal that started the blocked servers ITSELF (the port was free again)
        _heal_status(home, root, mtime=time.time() + 90, neo4j_ok=True, mcp_ok=True,
                     mcp_started=["deepmap-search", "deepmap-embed"], mcp_started_epoch=time.time() + 5)
        prob, _hs = _hs_call("heal_problem", home, root, up=(18911, 18912))
        check("a newer heal that STARTED the blocked server itself (mcp_started_epoch > blocker) lifts it", prob is None,
              str(prob))
        _heal_status(home, root, mtime=time.time() + 120, neo4j_ok=True, mcp_ok=True,
                     mcp_started=["deepmap-search"], mcp_started_epoch=float(rec["ts_epoch"]) - 100)
        prob, _hs = _hs_call("heal_problem", home, root, up=(18911, 18912))
        check("...but a start carried forward from BEFORE the blocker does not", isinstance(prob, tuple)
              and prob[0] == "blocked", str(prob))
        check("clear_setup_blocker -> True, file gone, read -> None",
              PR.clear_setup_blocker(root, env=env) is True and not path.exists()
              and PR.read_setup_blocker(root, env=env) is None)
        check("clear_setup_blocker with nothing to clear -> True (idempotent)",
              PR.clear_setup_blocker(root, env=env) is True)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")
        rec = PR.read_setup_blocker(root, env=env)
        check("a corrupt blocker file still reads as a blocker (fail-safe: never steer on a doubt)",
              isinstance(rec, dict), str(rec))
        path.unlink()

    # the heal-status forms of the same fact (W2 may record it there instead / as well)
    for label, fields in (
            ("refusal_kind 'mcp-blocked' (refused:true)", dict(neo4j_ok=True, mcp_ok=False, refused=True,
                                                             refusal_kind="mcp-blocked", reason=_BLOCK_REASON)),
            ("refusal_kind 'mcp-blocked' with refused:false", dict(neo4j_ok=True, mcp_ok=False, refused=False,
                                                                 refusal_kind="mcp-blocked", reason=_BLOCK_REASON)),
            ("mcp_ok:false + mcp_unverified ports (not a refusal)", dict(neo4j_ok=True, mcp_ok=False, refused=False,
                                                                      mcp_unverified=[18911],
                                                                      reason=_BLOCK_REASON))):
        with _tmpdir() as tw, _tmpdir() as th:
            home, root = Path(th), Path(tw).resolve()
            _manifest(root)
            _mcp_json(root)
            _heal_status(home, root, **fields)
            prob, _hs = _hs_call("heal_problem", home, root, up=(18911, 18912))
            check(f"heal status {label} + ports LISTENING -> ('blocked', …), never probe-superseded",
                  isinstance(prob, tuple) and prob[0] == "blocked", str(prob))
            NT = _load("deepmap_sessionstart_notify")
            ctx = _ctx(_inproc(NT, {"cwd": str(root), "session_id": "hs"}, home, up=(18911, 18912)))
            check(f"...notify: {label} -> 'must NOT be used' (not the 'another project's graph' refusal text)",
                  "must not be used" in ctx.lower() and "is ACTIVE" not in ctx and "18911" in ctx, ctx)

    # E1: the key migration re-keys the blocker with the other per-key side files
    with _tmpdir() as th:
        home = Path(th)
        run = home / "run"
        run.mkdir(parents=True)
        (run / "setup-blocked-oldkey000000.json").write_text(json.dumps(
            {"kind": "mcp-blocked", "reason": "r", "project_key": "oldkey000000", "root": "C:/x"}), encoding="utf-8")
        PR._rekey_side_files({"DEEPMAP_HOME": str(home)}, "oldkey000000", "newkey111111", "C:/y")
        new = run / "setup-blocked-newkey111111.json"
        moved = json.loads(new.read_text(encoding="utf-8")) if new.is_file() else {}
        check("E1 key migration moves setup-blocked-<old>.json to the new key (project_key/root rewritten)",
              moved.get("project_key") == "newkey111111" and moved.get("root") == "C:/y"
              and not (run / "setup-blocked-oldkey000000.json").exists(), str(moved))

    # the review's chain with provision's REAL heal-status writer: blocker, then a healthy heal -> still blocked
    try:
        spec = importlib.util.spec_from_file_location("dmr7_provision", BUILD / "provision.py")
        P = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(P)
        writer = getattr(P, "write_heal_status", None)
    except Exception as exc:  # noqa: BLE001
        writer = None
        check("provision importable for the writer-contract check", False, repr(exc))
    if callable(writer):
        with _tmpdir() as tw, _tmpdir() as th:
            home, root = Path(th), Path(tw).resolve()
            _manifest(root)
            _mcp_json(root)
            with _Env(home):
                PR.write_setup_blocker(root, reason=_BLOCK_REASON, servers=["deepmap-search"], ports=[18911])
                time.sleep(0.05)
                writer(str(root), {"neo4j_ok": True, "mcp_ok": True, "reason": None, "mcp_started": []})
            prob, _hs = _hs_call("heal_problem", home, root, up=(18911, 18912))
            check("review chain: setup blocker, then provision's REAL write_heal_status(mcp_ok) -> still 'blocked'",
                  isinstance(prob, tuple) and prob[0] == "blocked", str(prob))


# =========================================================================== B: T9#1 same-session heal window
class _TimeRec:
    """Stands in for the notify module's `time`: every call is the real one, but each sleep it REQUESTS is logged —
    so how long the poller chose to wait is measured without the wall clock (ROUND-9 K7)."""

    def __init__(self, log: list):
        self._log = log

    def sleep(self, s):
        self._log.append(float(s))
        return time.sleep(s)

    def __getattr__(self, name):
        return getattr(time, name)


class _WaitSpy:
    """Wraps the notify's heal-status poller (_wait_for_heal) for one with-block. Records each budget notify hands it,
    what it returned, the sleeps it requested (_TimeRec) and how many status polls it made. With `write`, THIS
    session's heal outcome is written by a thread only AFTER the poller has started (event ordering — the old fixture
    wrote at a fixed wall-clock offset, so on a loaded box notify's own slow start-up could see the file before its
    wait began and the check measured the machine, not the poller). ROUND-9 K7: every property below is load-proof."""

    def __init__(self, NT, write=None, delay: float = 0.3):
        self.NT, self.write, self.delay = NT, write, delay
        self.budgets: list = []
        self.results: list = []
        self.slept: list = []
        self.polls = 0
        self.polls_at_write = None
        self.wait_s: list = []

    def __enter__(self):
        NT = self.NT
        self._saved = (NT._wait_for_heal, NT._hs.file_sig, NT.time)
        orig_wait, orig_sig = self._saved[0], self._saved[1]
        self.in_wait = False

        def sig(path):
            if self.in_wait:
                self.polls += 1
            return orig_sig(path)

        def wait(path, seen, budget):
            self.budgets.append(budget)
            writer = None
            if self.write is not None:
                def _w():
                    time.sleep(self.delay)
                    self.write()
                    self.polls_at_write = self.polls   # snapshot once the outcome is fully on disk
                writer = threading.Thread(target=_w, daemon=True)
            self.in_wait = True
            t0 = time.monotonic()
            try:
                if writer is not None:
                    writer.start()
                r = orig_wait(path, seen, budget)
            finally:
                self.in_wait = False
                self.wait_s.append(time.monotonic() - t0)
            if writer is not None:
                writer.join(timeout=60)
            self.results.append(r)
            return r

        NT._wait_for_heal = wait
        NT._hs.file_sig = sig
        NT.time = _TimeRec(self.slept)
        return self

    def __exit__(self, *exc):
        self.NT._wait_for_heal, self.NT._hs.file_sig, self.NT.time = self._saved

    def polls_after_write(self):
        return None if self.polls_at_write is None else self.polls - self.polls_at_write


def gate_b_same_session_window() -> None:
    print("\n-- B (T9#1): this session's heal outcome reaches the agent; the heal child's output is logged --")
    NT = _load("deepmap_sessionstart_notify")
    reason = "this project's Neo4j (bolt://localhost:7811) is not reachable — run /deep-map:setup"
    poll = float(getattr(NT, "_HEAL_POLL_S", 0.1))

    # B1 ports down, heal coming, this session's heal FAILS while notify waits -> notify reports that, not READY
    with _tmpdir() as tw, _tmpdir() as th:
        home, root = Path(th), Path(tw).resolve()
        _manifest(root)
        _mcp_json(root)
        spy = _WaitSpy(NT, write=lambda: _heal_status(home, root, mtime=time.time(), neo4j_ok=False, mcp_ok=False,
                                                      reason=reason))
        with spy:
            ctx = _ctx(_inproc(NT, {"cwd": str(root), "session_id": "w1"}, home, up=(),
                               env={"DEEPMAP_NOTIFY_HEAL_WAIT": "6"}))
        check("B1: ports down + heal coming + this session's heal fails in the wait -> 'NOT reachable' + reason",
              "not reachable" in ctx.lower() and "7811" in ctx and "/deep-map:setup" in ctx, ctx)
        check("...no READY announce, no 'do not conclude DeepMap is broken'", "is ACTIVE" not in ctx
              and "do not conclude deepmap is broken" not in ctx.lower(), ctx)
        after = spy.polls_after_write()
        check("...it waited for THIS session's outcome (budget 6 s) and stopped as soon as it arrived: the poller "
              "returned the outcome within a few polls of it landing (event-ordered, no wall-clock bound)",
              spy.budgets == [6.0] and len(spy.results) == 1 and isinstance(spy.results[0], dict)
              and after is not None and after <= 3,
              f"budgets={spy.budgets} result={spy.results} polls_after_write={after}")
        check("...and never requested more sleep than its budget allows",
              sum(spy.slept) <= 6.0 + poll + 1e-6, f"slept={sum(spy.slept):.2f}s")
        sh = root / ".deepmap" / "session_health.json"
        st = json.loads(sh.read_text(encoding="utf-8")) if sh.is_file() else {}
        ent = (st.get("sessions") or {}).get("w1") or {}
        check("notify records what it told the session in <root>/.deepmap/session_health.json",
              ent.get("told") == "down" and isinstance(ent.get("start"), (int, float)), str(st))

    # B2 this session's heal SUCCEEDS in the wait and started the servers -> READY + a 'just started' reconnect note
    with _tmpdir() as tw, _tmpdir() as th:
        home, root = Path(th), Path(tw).resolve()
        _manifest(root)
        _mcp_json(root)
        spy = _WaitSpy(NT, write=lambda: _heal_status(home, root, mtime=time.time(), neo4j_ok=True, mcp_ok=True,
                                                      mcp_started=["deepmap-search", "deepmap-embed"],
                                                      mcp_started_epoch=time.time()))
        with spy:
            ctx = _ctx(_inproc(NT, {"cwd": str(root), "session_id": "w2"}, home, up=(),
                               env={"DEEPMAP_NOTIFY_HEAL_WAIT": "4"}))
        check("B2: this session's heal succeeds in the wait -> READY announce + /mcp reconnect note",
              "is ACTIVE" in ctx and "/mcp" in ctx and "reconnect" in ctx.lower(), ctx)
        check("...the note says the servers have just been started (not 'being revived')",
              "just" in ctx.lower() and "started" in ctx.lower(), ctx)
        check("...(the poller received that outcome)", len(spy.results) == 1 and isinstance(spy.results[0], dict),
              str(spy.results))

    # B3 the wait is bounded and can be switched off. Measured by what the poller was ASKED to do (its budget) and
    # the sleeps it REQUESTED — never by the wall-clock time of the whole notify call, which a loaded box stretches
    # (ROUND-9 K7: 4.79 s against a 4.5 s bound). The one wall-clock check left is a LOWER bound (a full wait can't be
    # shorter than its budget), which load can only make safer.
    with _tmpdir() as tw, _tmpdir() as th:
        home, root = Path(th), Path(tw).resolve()
        _manifest(root)
        _mcp_json(root)
        spy = _WaitSpy(NT)
        with spy:
            ctx = _ctx(_inproc(NT, {"cwd": str(root), "session_id": "w3"}, home, up=(),
                               env={"DEEPMAP_NOTIFY_HEAL_WAIT": "0"}))
            check("B3: DEEPMAP_NOTIFY_HEAL_WAIT=0 -> no wait (announce + reconnect note at once)",
                  all(b == 0 for b in spy.budgets) and not spy.slept and "is ACTIVE" in ctx and "/mcp" in ctx,
                  f"budgets={spy.budgets} slept={spy.slept} {ctx}")
            spy.budgets.clear(), spy.slept.clear(), spy.wait_s.clear()
            _inproc(NT, {"cwd": str(root), "session_id": "w4"}, home, up=(), env={"DEEPMAP_NOTIFY_HEAL_WAIT": "99"})
            check("...an oversized value is capped at 6 s (the hook's budget is 10 s): the poller got 6.0, requested "
                  "no more sleep than that, and did wait it out (no outcome came)",
                  spy.budgets == [6.0] and sum(spy.slept) <= 6.0 + poll + 1e-6 and spy.wait_s
                  and spy.wait_s[0] >= 6.0 - 1e-3,
                  f"budgets={spy.budgets} slept={sum(spy.slept):.2f}s waited={spy.wait_s}")
            spy.budgets.clear(), spy.slept.clear()
            _inproc(NT, {"cwd": str(root), "session_id": "w4b"}, home, up=())
            check("...unset -> the 3 s default", spy.budgets == [3.0], str(spy.budgets))
            spy.budgets.clear(), spy.slept.clear()
            _inproc(NT, {"cwd": str(root), "session_id": "w5"}, home, up=(18911, 18912),
                    env={"DEEPMAP_NOTIFY_HEAL_WAIT": "5"})
            check("...ports UP at session start -> no wait", spy.budgets == [] and not spy.slept,
                  f"budgets={spy.budgets} slept={spy.slept}")
            _inproc(NT, {"cwd": str(root), "session_id": "w6"}, home, up=(),
                    env={"DEEPMAP_NOTIFY_HEAL_WAIT": "5", "DEEPMAP_HEAL": "0"})
            check("...heal disabled -> no wait", spy.budgets == [] and not spy.slept,
                  f"budgets={spy.budgets} slept={spy.slept}")
            _inproc(NT, {"cwd": str(root), "session_id": "w6"}, home, up=(),
                    env={"DEEPMAP_NOTIFY_HEAL_WAIT": "5"})
            check("...a session already announced (SessionStart fires again on resume/compact) -> no wait",
                  spy.budgets == [] and not spy.slept, str(spy.budgets))
    check("B8: RECONNECT_NOTE no longer tells the agent 'do not conclude DeepMap is broken' unconditionally",
          "do not conclude deepmap is broken" not in NT.RECONNECT_NOTE.lower()
          and "/deep-map:setup" in NT.RECONNECT_NOTE, NT.RECONNECT_NOTE)

    # B5 the nudge surfaces a LATER failure of this session's heal after notify announced READY — once
    NU = _load("deepmap_presearch_nudge")
    grep = {"tool_name": "Grep", "tool_input": {"pattern": "x"}}
    with _tmpdir() as tw, _tmpdir() as th:
        home, root = Path(th), Path(tw).resolve()
        _manifest(root)
        _mcp_json(root)
        ctx = _ctx(_inproc(NT, {"cwd": str(root), "session_id": "s5"}, home, up=(),
                           env={"DEEPMAP_NOTIFY_HEAL_WAIT": "0"}))
        check("(B5 fixture: notify announced READY + reconnect)", "is ACTIVE" in ctx, ctx)
        first = _ctx(_inproc(NU, dict(grep, cwd=str(root), session_id="s5"), home, up=()))
        check("(B5: heal still running at the first Grep -> the normal nudge)", "deepmap_search" in first, first)
        _heal_status(home, root, mtime=time.time() + 1, neo4j_ok=False, mcp_ok=False, reason=reason)
        ctx = _ctx(_inproc(NU, dict(grep, cwd=str(root), session_id="s5"), home, up=()))
        check("B5: the session's heal then FAILED -> the next Grep says DeepMap could not start + reason + setup",
              "could not start" in ctx.lower() and "7811" in ctx and "/deep-map:setup" in ctx
              and "grep" in ctx.lower(), ctx)
        again = _ctx(_inproc(NU, dict(grep, cwd=str(root), session_id="s5"), home, up=()))
        check("...ONCE (the following Grep is silent)", again == "", again)
        other = _ctx(_inproc(NU, dict(grep, cwd=str(root), session_id="other"), home, up=()))
        check("...and a session notify never recorded keeps the old behaviour (silent on a current failure)",
              other == "", other)

    # B6 notify said 'not reachable' (previous heal) and this session's heal then succeeded -> ONE recovery notice
    with _tmpdir() as tw, _tmpdir() as th:
        home, root = Path(th), Path(tw).resolve()
        _manifest(root)
        _mcp_json(root)
        _heal_status(home, root, neo4j_ok=False, mcp_ok=False, reason=reason, ahead=2)
        ctx = _ctx(_inproc(NT, {"cwd": str(root), "session_id": "s6"}, home, up=(),
                           env={"DEEPMAP_NOTIFY_HEAL_WAIT": "0"}))
        check("(B6 fixture: notify said NOT reachable)", "not reachable" in ctx.lower(), ctx)
        _heal_status(home, root, mtime=time.time() + 1, neo4j_ok=True, mcp_ok=True,
                     mcp_started=["deepmap-search", "deepmap-embed"], mcp_started_epoch=time.time())
        ctx = _ctx(_inproc(NU, dict(grep, cwd=str(root), session_id="s6"), home, up=(18911, 18912)))
        check("B6: this session's heal then succeeded -> the Grep says DeepMap is available again (+ /mcp)",
              "available again" in ctx.lower() and "/mcp" in ctx and "deepmap_search" in ctx, ctx)
        again = _ctx(_inproc(NU, dict(grep, cwd=str(root), session_id="s6"), home, up=(18911, 18912)))
        check("...once", again == "", again)

    # B6b a reconnect note at session start, then this session's heal started the servers -> 'up now' once
    with _tmpdir() as tw, _tmpdir() as th:
        home, root = Path(th), Path(tw).resolve()
        _manifest(root)
        _mcp_json(root)
        _inproc(NT, {"cwd": str(root), "session_id": "s7"}, home, up=(), env={"DEEPMAP_NOTIFY_HEAL_WAIT": "0"})
        _inproc(NU, dict(grep, cwd=str(root), session_id="s7"), home, up=())      # the first-Grep nudge
        _heal_status(home, root, mtime=time.time() + 1, neo4j_ok=True, mcp_ok=True,
                     mcp_started=["deepmap-search", "deepmap-embed"], mcp_started_epoch=time.time())
        ctx = _ctx(_inproc(NU, dict(grep, cwd=str(root), session_id="s7"), home, up=(18911, 18912)))
        check("B6b: servers were down at start, this session's heal started them -> 'up now' + /mcp reconnect",
              "up now" in ctx.lower() and "/mcp" in ctx, ctx)

    # B7 a status OLDER than the session's start is not this session's outcome
    with _tmpdir() as tw, _tmpdir() as th:
        home, root = Path(th), Path(tw).resolve()
        _manifest(root)
        _mcp_json(root)
        _inproc(NT, {"cwd": str(root), "session_id": "s8"}, home, up=(18911, 18912),
                env={"DEEPMAP_NOTIFY_HEAL_WAIT": "0"})
        _inproc(NU, dict(grep, cwd=str(root), session_id="s8"), home, up=(18911, 18912))
        _heal_status(home, root, mtime=time.time() - 3600, neo4j_ok=False, reason=reason)
        ctx = _ctx(_inproc(NU, dict(grep, cwd=str(root), session_id="s8"), home, up=(18911, 18912)))
        check("B7: a heal status written BEFORE the session started is not reported as this session's outcome",
              ctx == "", ctx)

    # B9 the heal hook logs its child's output instead of discarding it
    HK = _load("deepmap_sessionstart_heal")
    calls = []

    class _FakePopen:
        def __init__(self, argv, **kw):
            calls.append((list(argv), kw))

    with _tmpdir() as tw, _tmpdir() as th:
        home, root = Path(th), Path(tw).resolve()
        _manifest(root)
        log = root / ".deepmap" / "heal.log"
        log.write_bytes(b"x" * (300 * 1024))
        saved = (HK.subprocess.Popen, sys.stdin)
        try:
            HK.subprocess.Popen = _FakePopen
            sys.stdin = io.StringIO(json.dumps({"cwd": str(root), "session_id": "hk"}))
            with _Env(home, {"CLAUDE_PLUGIN_ROOT": str(REPO)}):
                rc = HK.main()
        finally:
            HK.subprocess.Popen, sys.stdin = saved
        kw = calls[0][1] if calls else {}
        out = kw.get("stdout")
        name = getattr(out, "name", "") or ""
        check("B9: the heal child's stdout goes to <root>/.deepmap/heal.log (not DEVNULL)",
              rc == 0 and bool(calls) and str(name).replace("\\", "/").endswith(".deepmap/heal.log"), str(kw))
        check("...stderr too", kw.get("stderr") is out or kw.get("stderr") == subprocess.STDOUT, str(kw))
        check("...the parent closed its copy of the log handle", getattr(out, "closed", True) is True)
        check("...an oversized log was rotated to heal.log.1 (bounded)",
              (root / ".deepmap" / "heal.log.1").is_file() and log.stat().st_size < 64 * 1024,
              f"{log.stat().st_size if log.exists() else None}")
        check("...with a header naming the run", "heal" in log.read_text(encoding="utf-8", errors="replace"))

    with _tmpdir() as tw, _tmpdir() as th:
        home, root = Path(th), Path(tw).resolve()
        _manifest(root)
        plugin = Path(th) / "plugin"
        (plugin / "build").mkdir(parents=True)
        (plugin / "build" / "provision.py").write_text(
            "import sys\nprint('HEAL-CHILD-SAYS-HELLO', sys.argv[1:])\nprint('HEAL-CHILD-ERR', file=sys.stderr)\n",
            encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if k not in _SCRUB}
        env.update({"DEEPMAP_HOME": str(home), "CLAUDE_PLUGIN_ROOT": str(plugin)})
        p = subprocess.run([PY, str(HOOKS / "deepmap_sessionstart_heal.py")],
                           input=json.dumps({"cwd": str(root)}).encode("utf-8"), capture_output=True, env=env,
                           cwd=str(home), timeout=60)
        log = root / ".deepmap" / "heal.log"
        deadline, txt = time.time() + 20, ""
        while time.time() < deadline:
            txt = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else ""
            if "HEAL-CHILD-SAYS-HELLO" in txt and "HEAL-CHILD-ERR" in txt:
                break
            time.sleep(0.1)
        check("B9 (real subprocess): the detached child's stdout AND stderr land in heal.log; the hook prints nothing",
              p.returncode == 0 and p.stdout == b"" and "HEAL-CHILD-SAYS-HELLO" in txt and "HEAL-CHILD-ERR" in txt,
              f"rc={p.returncode} out={p.stdout[:80]!r} log={txt[-200:]!r}")


# =========================================================================== C: S-4 staleness beyond READY
def gate_c_staleness() -> None:
    print("\n-- C (S-4/T9#6): verdict-less / non-coverage DEGRADED manifests get a stale caution; distance shown --")
    if not shutil.which("git"):
        check("(git on PATH)", True, "skipped")
        return
    NT = _load("deepmap_sessionstart_notify")
    NU = _load("deepmap_presearch_nudge")
    with _tmpdir() as tw, _tmpdir() as th:
        home = Path(th)
        r = _repo(Path(tw).resolve())
        c1 = _git(r, "rev-parse", "HEAD")
        for i in range(3):
            _git(r, "commit", "-q", "--allow-empty", "-m", f"c{i + 2}")
        c4 = _git(r, "rev-parse", "HEAD")
        cases = (("legacy manifest (no verdict)", dict(verdict=None, built_at_sha=c1)),
                 ("DEGRADED without a coverage reason", dict(verdict="DEGRADED", built_at_sha=c1,
                                                             reasons=["VECTOR SEARCH DISABLED — no embeddings"])))
        for i, (label, kw) in enumerate(cases):
            _manifest(r, **kw)
            ctx = _ctx(_inproc(NT, {"cwd": str(r), "session_id": f"c{i}"}, home))
            check(f"{label}, HEAD moved -> ONE stale caution naming both SHAs + /deep-map:refresh",
                  "may be stale" in ctx.lower() and c1[:7] in ctx and c4[:7] in ctx and "/deep-map:refresh" in ctx,
                  ctx)
            check("...with the commit distance (3 commits)", "3 commits" in ctx, ctx)
            check("...never a steer toward the graph (no ACTIVE / prefer)",
                  "is ACTIVE" not in ctx and "prefer" not in ctx.lower(), ctx)
            again = _ctx(_inproc(NT, {"cwd": str(r), "session_id": f"c{i}"}, home))
            check("...once per session", again == "", again)
        _manifest(r, verdict=None, built_at_sha=c4)
        ctx = _ctx(_inproc(NT, {"cwd": str(r), "session_id": "c9"}, home))
        check("legacy manifest, HEAD unchanged -> still silent (unknown health, nothing actionable)", ctx == "", ctx)
        _manifest(r, verdict="FAILED", nodes=0, built_at_sha=c1)
        ctx = _ctx(_inproc(NT, {"cwd": str(r), "session_id": "c10"}, home))
        check("FAILED + HEAD moved -> silent (setup is broken; nothing to refresh toward)", ctx == "", ctx)
        _manifest(r, built_at_sha=c1)
        ctx = _ctx(_inproc(NT, {"cwd": str(r), "session_id": "c11"}, home))
        check("READY + HEAD moved -> the announce's stale note carries the distance too",
              "is ACTIVE" in ctx and "may be stale" in ctx and "3 commits" in ctx, ctx)
        ctx = _ctx(_inproc(NU, {"cwd": str(r), "session_id": "c12", "tool_name": "Grep",
                               "tool_input": {"pattern": "x"}}, home))
        check("READY + HEAD moved -> the pre-search nudge carries the stale note",
              "deepmap_search" in ctx and "may be stale" in ctx and "/deep-map:refresh" in ctx, ctx)
        _manifest(r, built_at_sha=c4)
        ctx = _ctx(_inproc(NU, {"cwd": str(r), "session_id": "c13", "tool_name": "Grep",
                               "tool_input": {"pattern": "x"}}, home))
        check("READY + fresh -> the plain nudge (no stale note)", "deepmap_search" in ctx
              and "stale" not in ctx.lower(), ctx)


# =========================================================================== D: .mcp.json ownership (hook side)
def gate_d_mcp_json_owner() -> None:
    print("\n-- D (I4 hook side): a .mcp.json pair registered to ANOTHER project is never probed as this one's --")
    if not callable(getattr(PR, "mcp_pair_claimed_by_other", None)):
        check("port_registry.mcp_pair_claimed_by_other exists", False)
        return
    with _tmpdir() as tw, _tmpdir() as th:
        home = Path(th)
        main_root = (Path(tw) / "main").resolve()
        wt = (Path(tw) / "worktree").resolve()
        main_root.mkdir()
        wt.mkdir()
        other_key = PR.project_key(main_root)
        _register(home, wt, search=18950, embed=18951, others=())
        reg = json.loads((home / "registry.json").read_text(encoding="utf-8"))
        reg["projects"] = {other_key: {"root": str(main_root).replace("\\", "/"), "search_port": 18911,
                                       "embed_port": 18912, "bolt_port": 7811, "http_port": 7598}}
        (home / "registry.json").write_text(json.dumps(reg), encoding="utf-8")
        env = {"DEEPMAP_HOME": str(home)}
        who = PR.mcp_pair_claimed_by_other(wt, (18911, 18912), env=env)
        check("mcp_pair_claimed_by_other(worktree, main's pair) -> main's root",
              who is not None and Path(who).name == "main", str(who))
        check("...and None for the project's OWN pair",
              PR.mcp_pair_claimed_by_other(main_root, (18911, 18912), env=env) is None)
        _manifest(wt)
        _mcp_json(wt, 18911, 18912)      # a copied checkout inherited main's committed .mcp.json
        ports, _hs = _hs_call("mcp_ports", home, wt)
        check("hook mcp_ports: a .mcp.json pair registered to ANOTHER project -> None (unknown, never probed)",
              ports is None, str(ports))
        _heal_status(home, wt, neo4j_ok=True, mcp_ok=False, reason="deepmap-search failed to start")
        prob, _hs = _hs_call("heal_problem", home, wt, up=(18911, 18912))
        check("...so main's listening servers can't 'supersede' this project's MCP failure",
              isinstance(prob, tuple) and prob[0] == "mcp", str(prob))
        ports, _hs = _hs_call("mcp_ports", home, main_root)
        _mcp_json(main_root, 18911, 18912)
        ports, _hs = _hs_call("mcp_ports", home, main_root)
        check("...while the owner's own .mcp.json pair is still its own", ports == (18911, 18912), str(ports))

        # the agent is TOLD: the tools this session connects to are the other project's servers
        (home / "run" / f"heal-{PR.project_key(wt)}.json").unlink()
        NT = _load("deepmap_sessionstart_notify")
        ctx = _ctx(_inproc(NT, {"cwd": str(wt), "session_id": "d1"}, home, up=(18911, 18912)))
        check("notify (READY copy whose .mcp.json names main's pair): 'must NOT be used', names the other "
              "project, /deep-map:setup", "must not be used" in ctx.lower() and "main" in ctx
              and "/deep-map:setup" in ctx and "another project" in ctx.lower(), ctx)
        check("...never the READY announce / a preference steer", "is ACTIVE" not in ctx
              and "prefer" not in ctx.lower(), ctx)
        NU = _load("deepmap_presearch_nudge")
        out = _inproc(NU, {"cwd": str(wt), "session_id": "d2", "tool_name": "Grep",
                           "tool_input": {"pattern": "x"}}, home, up=(18911, 18912))
        check("nudge: a foreign .mcp.json pair -> silent (never steer toward another project's servers)",
              _ctx(out) == "", out)
        SF = _load("deepmap_postedit_syncflag")
        out = _inproc(SF, {"cwd": str(wt), "session_id": "d3", "tool_name": "Edit",
                           "tool_input": {"file_path": str(wt / "a.py")}}, home, up=(18911, 18912))
        check("sync flag: a foreign .mcp.json pair -> silent", _ctx(out) == "", out)
        _manifest(main_root)
        ctx = _ctx(_inproc(NT, {"cwd": str(main_root), "session_id": "d4"}, home, up=(18911, 18912)))
        check("control: the OWNER (its own pair) still gets the READY announce", "is ACTIVE" in ctx, ctx)
        _mcp_json(main_root, 18913, 18914)   # differs from the registry pair: only the FILE can answer this
        (main_root / ".mcp.json").write_bytes(b"\xef\xbb\xbf" + (main_root / ".mcp.json").read_bytes())
        ports, _hs = _hs_call("mcp_ports", home, main_root)
        check("a BOM-prefixed .mcp.json is still read (its pair, not the registry fallback — T10d-11)",
              ports == (18913, 18914), str(ports))


# =========================================================================== E: docs
def _sentences(text: str) -> list:
    return re.split(r"(?<=[.!?])\s+", " ".join(text.split()))


def gate_e_docs() -> None:
    print("\n-- E: W1 doc/hint drift (SKILL.md, impact.md, status.md, setup.md) --")
    skill = SKILL.read_text(encoding="utf-8")
    flat = " ".join(skill.split())
    check("SKILL.md names no bare `deepmap doctor` / `deepmap status` executable (none is installed)",
          not re.search(r"`deepmap (doctor|status|sync|search|impact)\b", skill),
          str(re.findall(r"`deepmap [a-z-]+", skill)))
    s = next((x for x in _sentences(skill) if "coverage table" in x or "N files, M nodes" in x), "")
    check("SKILL.md: the coverage check is /deep-map:doctor (a runnable slash command)",
          "/deep-map:doctor" in s, s)
    s = " ".join(x for x in _sentences(skill) if "deepmap_impact" in x and ("empty" in x.lower()
                                                                           or "partial" in x.lower()))
    check("SKILL.md: an empty or partial deepmap_impact answer is not proof — grep before calling it a leaf",
          ("not proof" in s.lower() or "does not prove" in s.lower()) and "grep" in s.lower(), s)
    check("SKILL.md: tools missing / connection error at session start -> ask the user to run /mcp and reconnect",
          "/mcp" in skill and "reconnect" in skill.lower())
    s = next((x for x in _sentences(skill) if "top_k" in x and "widen" in x.lower()), "")
    check("SKILL.md: widening top_k keeps the earlier top hits (the ranking shifts with top_k)",
          "keep" in s.lower() and "only if the top hits miss" not in s, s)
    for name, text in (("SKILL.md", skill), ("impact.md", (COMMANDS / "impact.md").read_text(encoding="utf-8"))):
        t = " ".join(text.split())
        check(f"{name}: INFERRED is not presented as 'trust higher' (it is name-based)",
              "trust higher" not in t and re.search(r"INFERRED[^.]{0,260}name", t) is not None,
              re.findall(r"INFERRED[^.]{0,200}", t)[:2])
    check("SKILL.md: deepmap_impact's signature includes edge_types (it REPLACES the default walk)",
          re.search(r"deepmap_impact\(symbol: str, limit: int = 30,\s*edge_types", flat) is not None
          and "REPLACES" in flat, re.findall(r"deepmap_impact\([^)]*\)", flat)[:1])
    imp = " ".join((COMMANDS / "impact.md").read_text(encoding="utf-8").split())
    check("impact.md: --limit counts result ROWS (one per dependent-callee pair), not distinct dependents",
          "DISTINCT dependents" not in imp and "DISTINCT" in imp and "callee" in imp
          and re.search(r"--limit[^.]*row", imp) is not None, re.findall(r"--limit[^.]*\.", imp)[:2])
    st_raw = (COMMANDS / "status.md").read_text(encoding="utf-8")
    st = " ".join(st_raw.split())
    check("status.md also runs the CLI status (owner / coverage / freshness / learning lines)",
          "build/deepmap_cli.py\" status" in st and "owner" in st and "coverage" in st and "freshness" in st, st[:400])
    check("status.md: allowed-tools covers the CLI entry point",
          re.search(r"^allowed-tools:.*deepmap_cli\.py", st_raw, flags=re.M) is not None)
    su = " ".join((COMMANDS / "setup.md").read_text(encoding="utf-8").split())
    s = next((x for x in _sentences(su) if "remember" in x.lower() and "session" in x.lower()), "")
    check("setup.md: after that FAILED line new sessions are told not to use the tools until setup succeeds",
          bool(s) and "/deep-map:setup" in su, s)


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 72)
    print("DeepMap — round-7 W1: setup blocker (I4), same-session heal window (T9#1), staleness (S-4), docs")
    print(f"(hooks under test: {HOOKS}; build: {BUILD})")
    print("=" * 72)
    if not shutil.which("git"):
        print("RESULT: GREEN — 0/0 gates passed. (git not on PATH: skipped)")
        return 0
    for g in (gate_a_setup_blocker, gate_b_same_session_window, gate_c_staleness, gate_d_mcp_json_owner,
              gate_e_docs):
        try:
            g()
        except Exception as exc:  # noqa: BLE001 — a crashing section is a RED check, never a crash of the suite
            import traceback
            check(f"{g.__name__} ran without raising", False, "".join(traceback.format_exception(exc))[-900:])
    print("\n" + "=" * 72)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
