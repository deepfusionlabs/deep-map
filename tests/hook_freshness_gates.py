#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j, no-network gates for the ROUND-2 hook fixes (hooks/*.py, commands/*.md):

  R1  Project root: every hook resolves the manifest through the ancestors of the session cwd, up to
      the git work tree's top, preferring the OUTERMOST one (the rule sync._sync_root uses). A session
      opened in a subdirectory of a repo synced at its root used to find no manifest there, so heal,
      notify, nudge and the sync flag were all silent; a stale manifest an old subdirectory sync left
      behind must not shadow the root's. Outside a git work tree only the cwd itself counts, and
      DEEPMAP_HOME's own .deepmap is never a project.
  R2  The opt-in access hook logs to retrieval_log.project_root(cwd) — the same place the search side
      logs to — not to a stray <subdir>/.deepmap.
  R3  A heal failure on record is RE-CHECKED live before it steers a session away from DeepMap
      (review: "notify acts on the previous session's heal status"). The SessionStart hooks read the
      heal file while this session's heal is still running, so one transient failure (a JVM slower than
      heal's wait, a server that came up late) told a whole healthy session DeepMap was down. Dropped
      when this project's MCP ports (+ its OWN bolt port, for a database failure) listen now; never for
      a credentials rejection or an unverifiable legacy 7687; the line says a new check is running when
      heal is enabled. The same re-check un-silences the pre-search nudge.
  R4  Freshness (ROUND-2 D4): a dirty-tree manifest (built_at_dirty / dirty, possibly only
      built_at_base_sha) still gets the "HEAD moved -> may be stale" note, and a build from uncommitted
      changes on an unmoved HEAD gets a softer note; an abbreviated SHA equal to HEAD is not "stale".
  R5  heal's reason is not cut mid-command (the old hard 240-char cut).
  R6  D6: every remediation the hooks and command docs give is runnable as written (no `deepmap`
      executable, no instance-rm on the base store), and the command docs describe what the CLI now
      prints (impact notes / symbol forms, refresh queue / exit / root notices, status exit 3 +
      "(no instance)", setup's cold-start verify warning, doctor's freshness line).

Hermetic: temp git repos + a temp DEEPMAP_HOME; heal's provision.py is a recording stub; the port
probe is stubbed in-process. No Neo4j, no sockets opened, no network. DMGATE_ROOT runs these gates
against another copy of the repo (how the RED evidence was taken). Exit 0 = green.
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
REPO = Path(os.environ.get("DMGATE_ROOT") or Path(__file__).resolve().parent.parent)
HOOKS = REPO / "hooks"
BUILD = REPO / "build"
COMMANDS = Path(__file__).resolve().parent.parent / "commands"   # docs are always the live tree's
if os.environ.get("DMGATE_ROOT") and (REPO / "commands").is_dir():
    COMMANDS = REPO / "commands"
PY = sys.executable
sys.path.insert(0, str(HOOKS))
sys.path.insert(0, str(BUILD))
import port_registry as PR  # noqa: E402

_PASS = 0
_FAIL = 0
_SCRUB = ("PYTHONUTF8", "PYTHONIOENCODING", "DEEPMAP_HOOKS", "DEEPMAP_HEAL", "DEEPMAP_NUDGE",
          "DEEPMAP_SYNC_FLAG", "DEEPMAP_SYNC_FLAG_EVERY", "DEEPMAP_ANNOUNCE", "DEEPMAP_ONBOARD",
          "DEEPMAP_HIBERNATE", "DEEPMAP_RETRIEVAL_LOG", "CLAUDE_PROJECT_DIR", "DEEPMAP_PROJECT_ROOT",
          "NEO4J_URI", "DEEPMAP_LEARNING", "CLAUDE_PLUGIN_ROOT")
_GIT_ENV = {"GIT_AUTHOR_NAME": "g", "GIT_AUTHOR_EMAIL": "g@x", "GIT_COMMITTER_NAME": "g",
            "GIT_COMMITTER_EMAIL": "g@x"}
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
    spec = importlib.util.spec_from_file_location(f"dmhf_{name}", HOOKS / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _tmpdir():
    """A temp dir whose cleanup tolerates a straggler: the heal hook spawns a DETACHED (stub) provision
    that can still hold the dir for a moment under load (Windows WinError 32)."""
    return tempfile.TemporaryDirectory(ignore_cleanup_errors=True)


def _load_retrieval():
    """build/retrieval.py (its module import is stdlib-only; the neo4j driver is imported lazily), or
    None if it can't be loaded."""
    try:
        spec = importlib.util.spec_from_file_location("dmhf_retrieval", BUILD / "retrieval.py")
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m
    except Exception:  # noqa: BLE001
        return None


def _git(d: Path, *args) -> str:
    p = subprocess.run(["git", "-C", str(d), *args], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=60, env=dict(os.environ, **_GIT_ENV))
    return (p.stdout or "").strip()


def _repo(parent: Path, name: str) -> Path:
    d = parent / name
    d.mkdir(parents=True, exist_ok=True)
    _git(d, "init", "-q")
    _git(d, "commit", "-q", "--allow-empty", "-m", "c1")
    return d.resolve()


def _manifest(root: Path, verdict="READY", nodes=10, reasons=None, **extra) -> Path:
    d = root / ".deepmap"
    d.mkdir(parents=True, exist_ok=True)
    m = {"nodes": nodes, "extractor": "graphify"}
    if verdict is not None:
        m["verdict"] = verdict
    if reasons is not None:
        m["reasons"] = list(reasons)
    m.update(extra)
    p = d / "graph_build.json"
    p.write_text(json.dumps(m), encoding="utf-8")
    return p


def _heal_status(home: Path, root: Path, *, neo4j_ok=True, mcp_ok=True, reason="", ahead=5) -> Path:
    key = PR.project_key(root)
    p = home / "run" / f"heal-{key}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"ts": "2026-09-25T00:00:00Z", "ts_epoch": time.time(), "neo4j_ok": neo4j_ok,
                             "mcp_ok": mcp_ok, "reason": reason}), encoding="utf-8")
    man = root / ".deepmap" / "graph_build.json"
    t = (man.stat().st_mtime if man.exists() else time.time()) + ahead
    os.utime(p, (t, t))
    return p


def _register(home: Path, root: Path, *, bolt=7811, http=7598, search=18911, embed=18912) -> None:
    reg = {"version": 1, "kind": "deepmap_port_registry", "projects": {
        PR.project_key(root): {"root": str(root).replace("\\", "/"), "bolt_port": bolt, "http_port": http,
                               "search_port": search, "embed_port": embed}}}
    (home / "registry.json").write_text(json.dumps(reg), encoding="utf-8")


def _mcp_json(root: Path, s=18911, e=18912) -> None:
    (root / ".mcp.json").write_text(json.dumps({"mcpServers": {
        "deepmap-search": {"type": "http", "url": f"http://127.0.0.1:{s}/mcp"},
        "deepmap-embed": {"type": "http", "url": f"http://127.0.0.1:{e}/mcp"}}}), encoding="utf-8")


def _env(home: Path, extra=None) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in _SCRUB}
    env["DEEPMAP_HOME"] = str(home)
    env.update(extra or {})
    return env


def _run(hook: str, payload: dict, home: Path, *, extra=None):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    p = subprocess.run([PY, str(HOOKS / f"{hook}.py")], input=data, capture_output=True,
                       env=_env(home, extra), cwd=str(home), timeout=120)
    return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")


def _ctx(out: str) -> str:
    try:
        return json.loads(out.strip().splitlines()[-1])["hookSpecificOutput"]["additionalContext"]
    except Exception:
        return ""


def _stub_plugin_root(parent: Path, record: Path) -> Path:
    pr = parent / "plugin"
    (pr / "build").mkdir(parents=True, exist_ok=True)
    (pr / "build" / "provision.py").write_text(
        "import json, sys\n"
        f"rec = {str(record)!r}\n"
        "with open(rec, 'a', encoding='utf-8') as f:\n"
        "    f.write(json.dumps({'argv': sys.argv[1:]}) + '\\n')\n", encoding="utf-8")
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
    return []


class _InProc:
    """Run a hook's main() in-process with a temp DEEPMAP_HOME, a faked stdin/stdout and a per-port
    probe stub (`up` = the set of ports that "listen")."""

    def __init__(self, mod, home: Path, up=(), env=None):
        self.mod, self.home, self.up, self.env = mod, home, set(up), dict(env or {})

    def __call__(self, payload: dict) -> str:
        hs = self.mod._hs
        saved = {k: os.environ.get(k) for k in set(_SCRUB) | {"DEEPMAP_HOME"} | set(self.env)}
        saved_io = (sys.stdin, sys.stdout)
        saved_probe = hs.port_listening
        try:
            for k in _SCRUB:
                os.environ.pop(k, None)
            os.environ["DEEPMAP_HOME"] = str(self.home)
            os.environ.update(self.env)
            up = self.up
            hs.port_listening = lambda port, host="127.0.0.1", timeout=None: int(port) in up
            sys.stdin = io.StringIO(json.dumps(payload))
            sys.stdout = io.StringIO()
            self.mod.main()
            return sys.stdout.getvalue()
        finally:
            sys.stdin, sys.stdout = saved_io
            hs.port_listening = saved_probe
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


# --------------------------------------------------------------------------- R1
def gate_r1_project_root_unit() -> None:
    print("\n-- R1: project_root — outermost manifest up to the git work tree's top --")
    hs = _load("deepmap_hook_state")
    fn = getattr(hs, "project_root", None)
    check("deepmap_hook_state.project_root exists", callable(fn))
    if not callable(fn):
        return
    with _tmpdir() as tw, _tmpdir() as th:
        saved = os.environ.get("DEEPMAP_HOME")
        os.environ["DEEPMAP_HOME"] = str(Path(th))
        try:
            w = Path(tw).resolve()
            r = _repo(w, "repo")
            deep = r / "a" / "b"
            deep.mkdir(parents=True)
            check("no manifest anywhere -> the start dir itself", fn(deep) == deep, str(fn(deep)))
            _manifest(r / "a")
            check("manifest only at repo/a -> repo/a from repo/a/b", fn(deep) == r / "a", str(fn(deep)))
            _manifest(r)
            check("manifests at repo AND repo/a -> the OUTERMOST (repo)", fn(deep) == r, str(fn(deep)))
            check("... from repo/a itself too", fn(r / "a") == r, str(fn(r / "a")))
            check("... and from the root", fn(r) == r, str(fn(r)))
            p = w / "plain"
            (p / "c").mkdir(parents=True)
            _manifest(p)
            check("NOT a git work tree: only the cwd counts (plain/c -> plain/c)", fn(p / "c") == p / "c",
                  str(fn(p / "c")))
            nested = _repo(p, "inner")
            (nested / "x").mkdir()
            check("a git repo nested in a synced non-git dir is bounded at its own top",
                  fn(nested / "x") == nested / "x", str(fn(nested / "x")))
            wt = w / "wt"
            (wt / "s").mkdir(parents=True)
            (wt / ".git").write_text("gitdir: /elsewhere\n", encoding="utf-8")   # a worktree's .git FILE
            _manifest(wt)
            check("a `.git` FILE (worktree) also marks the top", fn(wt / "s") == wt, str(fn(wt / "s")))
            hr = _repo(w, "homerepo")
            (hr / "sub").mkdir()
            os.environ["DEEPMAP_HOME"] = str(hr / ".deepmap")
            _manifest(hr)   # DEEPMAP_HOME's own .deepmap holds a graph_build.json
            check("DEEPMAP_HOME's .deepmap is never taken as a project", fn(hr / "sub") == hr / "sub",
                  str(fn(hr / "sub")))
        finally:
            if saved is None:
                os.environ.pop("DEEPMAP_HOME", None)
            else:
                os.environ["DEEPMAP_HOME"] = saved


def gate_r1_hooks_from_subdir() -> None:
    print("\n-- R1: the REAL hooks, run from a subdirectory of a repo synced at its root --")
    with _tmpdir() as tw, _tmpdir() as th:
        home = Path(th)
        r = _repo(Path(tw).resolve(), "proj")
        sub = r / "src" / "pkg"
        sub.mkdir(parents=True)
        _manifest(r)
        _manifest(r / "src", verdict="FAILED", nodes=0)   # a stale leftover of an old subdir sync
        rc, out, err = _run("deepmap_presearch_nudge", {"cwd": str(sub), "session_id": "s1",
                            "tool_name": "Grep", "tool_input": {"pattern": "x"}}, home)
        check("nudge from src/pkg fires on the ROOT's READY manifest (not the stale FAILED src/ one)",
              rc == 0 and "deepmap_search" in _ctx(out), f"rc={rc} out={out[:160]!r} err={err[-200:]!r}")
        check("... its throttle state lives under the ROOT's .deepmap",
              (r / ".deepmap" / "presearch_nudge_state.json").is_file()
              and not (sub / ".deepmap").exists())
        rc, out, err = _run("deepmap_postedit_syncflag", {"cwd": str(sub), "session_id": "s1",
                            "tool_name": "Edit", "tool_input": {"file_path": str(sub / "a.py")}}, home)
        check("sync flag from a subdirectory fires", rc == 0 and "/deep-map:refresh" in _ctx(out),
              f"rc={rc} out={out[:160]!r} err={err[-200:]!r}")
        rc, out, err = _run("deepmap_sessionstart_notify", {"cwd": str(sub), "session_id": "s1"}, home)
        check("notify from a subdirectory announces the READY graph",
              rc == 0 and "is ACTIVE" in _ctx(out), f"rc={rc} out={out[:160]!r} err={err[-200:]!r}")
        record = home / "heal_record.jsonl"
        plugin = _stub_plugin_root(home, record)
        rc, out, err = _run("deepmap_sessionstart_heal", {"cwd": str(sub)}, home,
                            extra={"CLAUDE_PLUGIN_ROOT": str(plugin)})
        recs = _wait_records(record, 1)
        argv = recs[0]["argv"] if recs else []
        check("heal from a subdirectory spawns `provision.py heal <the synced ROOT>`",
              rc == 0 and argv[:1] == ["heal"] and len(argv) >= 2 and Path(argv[1]) == r,
              f"rc={rc} recs={recs} err={err[-200:]!r}")
    with _tmpdir() as tw, _tmpdir() as th:
        home = Path(th)
        plain = Path(tw).resolve() / "plain"
        (plain / "sub").mkdir(parents=True)
        _manifest(plain)
        rc, out, _err = _run("deepmap_presearch_nudge", {"cwd": str(plain / "sub"), "session_id": "s2",
                             "tool_name": "Grep", "tool_input": {"pattern": "x"}}, home)
        check("outside git, a parent's manifest is NOT borrowed (sync syncs a non-git dir as given)",
              rc == 0 and _ctx(out) == "", out[:160])


# --------------------------------------------------------------------------- R2
def gate_r2_access_hook_root() -> None:
    print("\n-- R2: the access hook logs to the project's ONE log from a subdirectory --")
    with _tmpdir() as tw, _tmpdir() as th:
        home = Path(th)
        r = _repo(Path(tw).resolve(), "proj")
        sub = r / "lib"
        sub.mkdir()
        _manifest(r)
        rc, _out, err = _run("deepmap_access_hook", {"cwd": str(sub), "session_id": "a1", "tool_name": "Read",
                             "tool_input": {"file_path": str(sub / "m.py")}}, home,
                             extra={"DEEPMAP_RETRIEVAL_LOG": "1"})
        check("access event lands in <root>/.deepmap/retrieval_log.jsonl",
              rc == 0 and (r / ".deepmap" / "retrieval_log.jsonl").is_file(), err[-200:])
        check("... and no stray <subdir>/.deepmap is created", not (sub / ".deepmap").exists())


# --------------------------------------------------------------------------- R3
def _heal_fixture(tw: Path, home: Path, *, registered=True, mcp_json=True, verdict="READY"):
    r = _repo(tw, "proj")
    _manifest(r, verdict=verdict, reasons=[_PASCAL] if verdict == "DEGRADED" else None)
    if registered:
        _register(home, r)
    if mcp_json:
        _mcp_json(r)
    return r


def gate_r3_stale_heal_recheck() -> None:
    print("\n-- R3: a recorded heal failure is re-checked live before it steers the session --")
    NT = _load("deepmap_sessionstart_notify")
    NU = _load("deepmap_presearch_nudge")
    hs = NT._hs
    check("hook_state exposes heal_problem_superseded + bolt_target",
          callable(getattr(hs, "heal_problem_superseded", None)) and callable(getattr(hs, "bolt_target", None)))
    MCP = {18911, 18912}
    cases = [
        # label, status kwargs, listening ports, registered, env, want_down, want_retry_note
        ("mcp failure on record, both MCP ports listen NOW -> normal announce",
         dict(mcp_ok=False, reason="deepmap-search did not come up"), MCP, True, {}, False, False),
        ("mcp failure, ports still down -> NOT reachable + 'new check running'",
         dict(mcp_ok=False, reason="deepmap-search did not come up"), set(), True, {}, True, True),
        ("mcp failure, ports down, heal disabled -> NOT reachable, no 'new check' promise",
         dict(mcp_ok=False, reason="deepmap-search did not come up"), set(), True, {"DEEPMAP_HEAL": "0"},
         True, False),
        ("neo4j failure, own bolt 7811 + MCP listen NOW -> normal announce",
         dict(neo4j_ok=False, reason="bolt://localhost:7811 not reachable after 120s"), MCP | {7811}, True, {},
         False, False),
        ("neo4j failure, MCP up but own bolt down -> NOT reachable",
         dict(neo4j_ok=False, reason="bolt://localhost:7811 not reachable after 120s"), MCP, True, {},
         True, True),
        ("neo4j failure = credentials REJECTED, everything listens -> still NOT reachable",
         dict(neo4j_ok=False, reason="Neo4j at bolt://localhost:7811 is running but REJECTED the "
                                     "credentials (AuthError) — run /deep-map:setup"), MCP | {7811}, True, {},
         True, True),
        ("neo4j failure on an UNREGISTERED project (legacy 7687), everything listens -> unverifiable, "
         "stays", dict(neo4j_ok=False, reason="bolt 7687 held by another program"), MCP | {7687}, False, {},
         True, True),
    ]
    for i, (label, st, up, registered, env, want_down, want_retry) in enumerate(cases):
        with _tmpdir() as tw, _tmpdir() as th:
            home = Path(th)
            r = _heal_fixture(Path(tw).resolve(), home, registered=registered)
            _heal_status(home, r, **st)
            ctx = _ctx(_InProc(NT, home, up, env)({"cwd": str(r), "session_id": f"n{i}"}))
            down = "not reachable" in ctx.lower()
            retry = "new start-up check" in ctx.lower()
            ok = (down == want_down) and (retry == want_retry) and (want_down or "is ACTIVE" in ctx)
            check(f"notify: {label}", ok, ctx[:220])
    for i, (label, st, up, want) in enumerate([
            ("nudge: mcp failure on record but ports listen NOW -> nudges",
             dict(mcp_ok=False, reason="x did not come up"), MCP, True),
            ("nudge: neo4j failure, nothing listens -> silent",
             dict(neo4j_ok=False, reason="bolt://localhost:7811 not reachable"), set(), False)]):
        with _tmpdir() as tw, _tmpdir() as th:
            home = Path(th)
            r = _heal_fixture(Path(tw).resolve(), home)
            _heal_status(home, r, **st)
            ctx = _ctx(_InProc(NU, home, up)({"cwd": str(r), "session_id": f"u{i}", "tool_name": "Grep",
                                                "tool_input": {"pattern": "x"}}))
            check(label, bool(ctx) == want, ctx[:160])
    bt = getattr(hs, "bolt_target", None)
    if callable(bt):
        with _tmpdir() as tw, _tmpdir() as th:
            home = Path(th)
            saved = {k: os.environ.get(k) for k in ("DEEPMAP_HOME", "NEO4J_URI")}
            try:
                os.environ["DEEPMAP_HOME"] = str(home)
                os.environ.pop("NEO4J_URI", None)
                r = _repo(Path(tw).resolve(), "proj")
                check("bolt_target: unregistered (legacy 7687) -> None (a probe proves nothing)", bt(r) is None,
                      str(bt(r)))
                _register(home, r)
                check("bolt_target: registered -> ('127.0.0.1', 7811)", bt(r) == ("127.0.0.1", 7811), str(bt(r)))
                os.environ["NEO4J_URI"] = "bolt://127.0.0.1:7999"
                check("bolt_target: operator NEO4J_URI -> ('127.0.0.1', 7999)", bt(r) == ("127.0.0.1", 7999),
                      str(bt(r)))
                os.environ["NEO4J_URI"] = "neo4j+s://abcd1234.databases.neo4j.io"
                check("bolt_target: a REMOTE operator endpoint -> None (a hook never connects off the box)",
                      bt(r) is None, str(bt(r)))
            finally:
                for k, v in saved.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v


# --------------------------------------------------------------------------- R4
def gate_r4_dirty_staleness() -> None:
    print("\n-- R4: freshness notes for dirty-tree manifests (D4) --")
    NT = _load("deepmap_sessionstart_notify")
    with _tmpdir() as tw, _tmpdir() as th:
        home = Path(th)
        r = _repo(Path(tw).resolve(), "proj")
        c1 = _git(r, "rev-parse", "HEAD")
        _git(r, "commit", "-q", "--allow-empty", "-m", "c2")
        c2 = _git(r, "rev-parse", "HEAD")
        check("(fixture: two commits)", len(c1) == 40 and len(c2) == 40 and c1 != c2)
        cases = [
            ("dirty manifest with ONLY built_at_base_sha, HEAD moved -> stale note",
             dict(built_at_base_sha=c1, built_at_dirty=True), "stale", c1[:7]),
            ("built_at_sha == HEAD + built_at_dirty -> 'uncommitted changes' note",
             dict(built_at_sha=c2, built_at_dirty=True), "uncommitted", c2[:7]),
            ("built_at_sha == HEAD + dirty (the D4 key) -> 'uncommitted changes' note",
             dict(built_at_sha=c2, dirty=True, dirty_paths_sample=["a.py"]), "uncommitted", c2[:7]),
            ("built_at_sha == HEAD, clean -> no note", dict(built_at_sha=c2, built_at_dirty=False), None, ""),
            ("built_at_sha != HEAD (clean) -> stale note (unchanged)", dict(built_at_sha=c1), "stale", c2[:7]),
            ("abbreviated built_at_sha == HEAD -> no false 'stale'", dict(built_at_sha=c2[:7]), None, ""),
        ]
        for i, (label, extra, want, sha) in enumerate(cases):
            _manifest(r, **extra)
            ctx = _ctx(_InProc(NT, home)({"cwd": str(r), "session_id": f"d{i}"}))
            has_stale = "may be stale" in ctx
            has_dirty = "uncommitted changes" in ctx
            if want == "stale":
                ok = has_stale and sha in ctx and not has_dirty
            elif want == "uncommitted":
                ok = has_dirty and sha in ctx and not has_stale
            else:
                ok = "is ACTIVE" in ctx and not has_stale and not has_dirty
            check(label, ok, ctx[-260:])
        _manifest(r, verdict="DEGRADED", reasons=[_PASCAL], built_at_sha=c1)
        ctx = _ctx(_InProc(NT, home)({"cwd": str(r), "session_id": "dg"}))
        check("the DEGRADED coverage warning carries the stale note too",
              "Pascal/Delphi" in ctx and "may be stale" in ctx, ctx[-200:])


# --------------------------------------------------------------------------- R5
def gate_r5_reason_not_cut() -> None:
    print("\n-- R5: heal's reason reaches the agent whole (no mid-command cut) --")
    NT = _load("deepmap_sessionstart_notify")
    tail = 'then run `"C:/Python/python.exe" "C:/plugin/build/provision.py" status` and /deep-map:setup'
    reason = ("Neo4j at bolt://localhost:7811 did not answer within 120 s after start — the instance log "
              "is C:/Users/someone/.deepmap/instances/0123456789ab/neo4j-console.log; a JVM that is still "
              "starting on a loaded machine can take longer than that, and a port held by another "
              "program keeps it from binding at all — " + tail)
    with _tmpdir() as tw, _tmpdir() as th:
        home = Path(th)
        r = _heal_fixture(Path(tw).resolve(), home, registered=True, mcp_json=False)
        _heal_status(home, r, neo4j_ok=False, reason=reason)
        ctx = _ctx(_InProc(NT, home)({"cwd": str(r), "session_id": "rs"}))
        check(f"a {len(reason)}-char reason is shown to its end (the command is intact)",
              "not reachable" in ctx.lower() and tail in ctx, ctx[-240:])


# --------------------------------------------------------------------------- R6
def gate_r6_runnable_remediations_and_docs() -> None:
    print("\n-- R6: runnable remediations (D6) + command docs match the CLI --")
    bad = re.compile(r"`deepmap (doctor|status|sync|search|impact)\b|python build/|instance-rm --yes")
    for f in sorted(HOOKS.glob("*.py")):
        hits = bad.findall(f.read_text(encoding="utf-8"))
        check(f"hooks/{f.name}: no un-runnable remediation (`deepmap ...` / python build/ / instance-rm --yes)",
              not hits, str(hits))
    NT = _load("deepmap_sessionstart_notify")
    check("the coverage warning points at /deep-map:doctor", "/deep-map:doctor" in NT.COVERAGE_TEXT)
    docs = {p.name: p.read_text(encoding="utf-8") for p in COMMANDS.glob("*.md")}
    for name, text in sorted(docs.items()):
        check(f"commands/{name}: never advises `instance-rm --yes` / a bare `deepmap` executable",
              "instance-rm --yes" not in text and not re.search(r"`deepmap (doctor|status|sync|search|impact)\b",
                                                                  text))
    imp = docs.get("impact.md", "")
    check("impact.md: the owner-qualified / hash symbol forms", "Class#method" in imp and "#method" in imp
          and "Owner::method" in imp)
    check("impact.md: relay the notes (limit cut, reduced depth / 1-hop, in-graph vs not)",
          "--limit" in imp and "one hop" in imp and "NOT in the graph" in imp and "DISTINCT" in imp)
    # 'too expensive' (ImpactTooExpensive) means the 1-hop walk ITSELF ran out of transaction memory, so
    # `--max-hops 1` would fail the same way (round-2 review, D6): the advice must be the CLI's own remedy.
    te = next((s for s in re.split(r"(?<=[.!?])\s+", " ".join(imp.split())) if "too expensive" in s), "")
    check("impact.md: the too-expensive advice is the CLI's remedy (qualified Class.method or a larger "
          "DEEPMAP_NEO4J_HEAP_MAX), never `--max-hops 1`",
          bool(te) and "Class.method" in te and "--max-hops 1" not in te and "DEEPMAP_NEO4J_HEAP_MAX" in imp,
          te)
    ret = getattr(_load_retrieval(), "_too_expensive", None)
    cli_txt = ret("get", 3) if callable(ret) else ""
    check("...and the CLI's own too-expensive message names the same two remedies",
          "Class.method" in cli_txt and "DEEPMAP_NEO4J_HEAP_MAX" in cli_txt, cli_txt)
    check("impact.md: edge_types / --edges REPLACE the default walk (full list given)",
          "REPLACES" in imp and "CALLS,ROUTES_TO,DEPENDS_ON,TRIGGERS,SCHEDULED_AS,HOOKS,INHERITS,CASCADES,"
                                "RENDERS,HAS_MANY,REFERENCES" in imp)
    ref = docs.get("refresh.md", "")
    check("refresh.md: 'queued a re-run', non-zero exit on FAILED, --force-prune, synced-root notice",
          "queued a re-run" in ref and "non-zero exit" in ref.lower() and "--force-prune" in ref
          and "already-synced" in ref)
    st = docs.get("status.md", "")
    check("status.md: exit 3 + '(no instance)' + instance-rm refuses the base store",
          "(3)" in st and "(no instance)" in st and "7687" in st and "refuses" in st)
    su = docs.get("setup.md", "")
    check("setup.md: a verify WARNING usually means still starting up (cold model load)",
          "still starting up" in su and "setup verdict" in su)
    dr = docs.get("doctor.md", "")
    check("doctor.md: freshness incl. uncommitted builds + this project's own servers",
          "uncommitted" in dr and "THIS project's" in dr)
    # round-2 W3 hand-off: what doctor/status/impact now print
    check("doctor.md: the unregistered-MCP-pair WARN and the 'NOT this project' owner WARN are explained",
          "no MCP port pair is registered" in dr and "NOT this project" in dr, dr[-300:])
    check("status.md: a foreign owner is marked '(NOT this project' and must not be read as this one's",
          "NOT this project" in st)
    check("impact.md: exit codes (3 = database error or too expensive)", "exit" in imp and "3" in imp
          and re.search(r"exits?\s*(code\s*)?`?3`?", imp) is not None)
    # ROUND-4 F1/F2: a sync REFUSING over another project's graph on a per-project route is fixed by
    # /deep-map:setup (it re-verifies an old base claim and gives this project its own instance); the
    # printed --switch-project deletes that project's graph, so it is only for an explicit request.
    para = " ".join(next((b for b in re.split(r"\n(?=- )", ref) if "this Neo4j holds project" in b), "").split())
    check("refresh.md: 'REFUSING: this Neo4j holds project X' -> /deep-map:setup; --switch-project deletes "
          "X's graph (explicit request only)",
          "/deep-map:setup" in para and "--switch-project" in para and "deletes" in para
          and "explicit request" in para, para)
    check("refresh.md + doctor.md: a nested repository (or the folder around one) is a project of its own (F1)",
          all("nested" in docs.get(n, "") and "project of its own" in " ".join(docs.get(n, "").split())
              for n in ("refresh.md", "doctor.md")))
    # ROUND-3 E1: the one-time registry-key migration notice is expected, not an error
    for name in ("doctor.md", "status.md"):
        t = docs.get(name, "")
        check(f"{name}: the one-time 'registry key migrated' / DEFERRED notice is explained as expected",
              "registry key migrated" in t and "DEFERRED" in t)


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 68)
    print("DeepMap — round-2 hook fixes: project root, live heal re-check, freshness, docs")
    print(f"(hooks under test: {HOOKS})")
    print("=" * 68)
    if not shutil.which("git"):
        print("RESULT: GREEN — 0/0 gates passed. (git not on PATH: skipped)")
        return 0
    gate_r1_project_root_unit()
    gate_r1_hooks_from_subdir()
    gate_r2_access_hook_root()
    gate_r3_stale_heal_recheck()
    gate_r4_dirty_staleness()
    gate_r5_reason_not_cut()
    gate_r6_runnable_remediations_and_docs()
    print("\n" + "=" * 68)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
