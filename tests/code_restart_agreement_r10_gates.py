#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ROUND-10 W1 gates: the SessionStart reconnect note and heal's code restart apply ONE rule (L1, L4, L6), and the
hooks start on a python3-only machine (L7).

  A  L1 — the PAIRED gate. The notify tells the agent "the SessionStart heal restarts them" whenever
     deepmap_hook_state.mcp_code_stale says a server runs superseded code; heal restarts a server only when
     provision._own_server_code_stale says so. Round 9 left them on different rules: an owner record naming an
     EARLIER pid than the pid file made the hook fall through to the command-line evidence while heal refused on the
     file's mere existence — the note promised a restart forever. Real child processes stand in for the servers
     (their argv runs <plugin root>/build/<server script>, the pid file written after they started) and every
     owner-record state (none; an earlier process's — dead, alive, another project's; unreadable; matching with and
     without the code fields; another project's; a recycled pid) is combined with the code the process runs (older,
     newer, the same version from another existing directory, a removed directory, the current directory — also
     updated IN PLACE after the process started, L3) and the pid-file / registry facts heal checks (a pid file older
     than its process, another port's record naming the pid, an unregistered port, a dead pid). For EVERY state: the
     hook notes it  <=>  heal restarts it; plus the expected verdict of the states the review named. The shared rule
     (port_registry.own_server_code_stale) must say the same.
  B  L6 — a removed plugin root whose path holds a SPACE, on POSIX: provision joined /proc argv with spaces and
     never found the script (heal kept the old servers) while the hook promised a restart. Linux is simulated through
     both modules' /proc seams (the same argv, pid stat, boot id, environment); macOS reads the kernel's argv
     (KERN_PROCARGS2 — its parser and the reader seam); the Windows command line is split by CommandLineToArgvW's
     rules (checked against the real API on Windows).
  C  L4 — two INSTALLED copies of the SAME version (two CLAUDE_CONFIG_DIR accounts, or two marketplaces in one):
     each window used to see its own copy as "the installed one" and restart the servers onto it, then the other
     window back. Equal version from a directory that still holds it is never replaced now — on both sides — and a
     newer version still wins exactly once.
  D  L7 — hooks.json runs each hook with `python`, and with `python3` when `python` is missing (stock Debian / Ubuntu /
     macOS ship only python3, so every hook silently never ran there). ROUND-11 R3 replaced round 10's
     `python … || python3 …` (Windows PowerShell 5.1 — Claude Code's hook shell on Windows without Git Bash — can't
     parse `||`) with `python … ; python3 … ; exit 0`: the two share the shell's stdin, so the first to run the hook
     reads the payload and the second finds it drained and exits at once. Exercised under sh and (Windows) cmd.exe with
     shim launchers on a controlled PATH — python-only, python3-only, both (python does the work, python3 exits
     silently) — the payload reaching python3 when it is the one that runs. cmd.exe tries only `python` (no `;`
     separator; never Claude Code's hook shell). PowerShell 5.1 / 7 and the REAL hooks: tests/hooks_shell_r11_gates.py.
  E  L7 docs + doc drift: every commands/*.md that runs a python entry point names the python3 fallback and allows
     it; the sync-flag hook's marker comment names provision's git-hook writer (the installers delegate to it).
  F  L5, hook side: the post-edit reminder's "a commit also re-syncs it" is never made for a block whose Python can
     never start — the one-word `'py -3'` the review found (a command-line DEEPMAP_PYTHON written as ONE sh word), a
     removed interpreter, a directory, a bare command not on PATH — and still is for provision's real block.

Hermetic: temp project dirs, a temp DEEPMAP_HOME, CLAUDE_CONFIG_DIR and GIT_CONFIG_GLOBAL; the only processes started
are this gate's own sleeping stub "servers" (killed through their Popen handles) and shell / python children. No
Neo4j, no sockets, no network; never touches ~/.deepmap, ~/.claude or the committed .mcp.json. Exit 0 = GREEN.
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

REPO = Path(os.environ.get("DMGATE_ROOT") or Path(__file__).resolve().parent.parent)
HOOKS = REPO / "hooks"
BUILD = REPO / "build"
COMMANDS = REPO / "commands"
PY = sys.executable
sys.path.insert(0, str(BUILD))
import port_registry as PR  # noqa: E402
import provision as P  # noqa: E402

_PASS = 0
_FAIL = 0
_WIN = os.name == "nt"
SEARCH, EMBED = "deepmap-search", "deepmap-embed"
_SCRIPTS = {SEARCH: "deepmap_search_server.py", EMBED: "deepmap_embed_server.py"}
_PORT_ENV = {SEARCH: "DEEPMAP_SEARCH_PORT", EMBED: "DEEPMAP_EMBED_PORT"}
_S, _E = 18961, 18962          # this project's registered MCP pair (nothing listens: no probe is made)
_S2, _E2 = 18971, 18972
_CUR_VER = "0.28.0"
_SCRUB = ("DEEPMAP_SEARCH_PORT", "DEEPMAP_EMBED_PORT", "NEO4J_URI", "DEEPMAP_PROJECT_ROOT", "DEEPMAP_PROJECT_KEY",
          "CLAUDE_PROJECT_DIR", "CLAUDE_PLUGIN_ROOT", "DEEPMAP_HOOKS", "DEEPMAP_HEAL", "PYTHONUTF8")
_GIT_ENV = {"GIT_AUTHOR_NAME": "g", "GIT_AUTHOR_EMAIL": "g@x", "GIT_COMMITTER_NAME": "g",
            "GIT_COMMITTER_EMAIL": "g@x", "GIT_CONFIG_NOSYSTEM": "1"}


def check(label: str, cond: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {label}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}{(' — ' + str(detail)[:900]) if detail else ''}")


def _load_hook(name: str):
    spec = importlib.util.spec_from_file_location(f"dmr10_{name}_{time.monotonic_ns()}", HOOKS / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _rmtree(p) -> None:
    """Remove a tree this gate created — including git's read-only object files on Windows. Never raises."""
    def _retry(func, path, _exc):
        try:
            os.chmod(path, 0o700)
            func(path)
        except Exception:
            pass
    try:
        if sys.version_info >= (3, 12):
            shutil.rmtree(str(p), onexc=_retry)
        else:
            shutil.rmtree(str(p), onerror=_retry)
    except Exception:
        pass


class _Patch:
    """Set attributes on an object for a with-block (restored on exit, also when they did not exist before)."""

    _MISSING = object()

    def __init__(self, obj, **attrs):
        self.obj, self.attrs = obj, attrs

    def __enter__(self):
        self.saved = {k: getattr(self.obj, k, self._MISSING) for k in self.attrs}
        for k, v in self.attrs.items():
            setattr(self.obj, k, v)
        return self

    def __exit__(self, *exc):
        for k, v in self.saved.items():
            if v is self._MISSING:
                try:
                    delattr(self.obj, k)
                except Exception:
                    pass
            else:
                setattr(self.obj, k, v)


class _Env:
    """DEEPMAP_HOME / CLAUDE_CONFIG_DIR / GIT_CONFIG_GLOBAL -> temp; the port / project knobs scrubbed; restored on
    exit (sys.path too — the hook module may insert its build dir)."""

    def __init__(self, home: Path, cfg: Path, gitcfg: Path, extra=None):
        self.home, self.cfg, self.gitcfg, self.extra = home, cfg, gitcfg, dict(extra or {})

    def __enter__(self):
        keys = set(_SCRUB) | {"DEEPMAP_HOME", "CLAUDE_CONFIG_DIR", "GIT_CONFIG_GLOBAL"} | set(self.extra)
        self.saved = {k: os.environ.get(k) for k in keys}
        self.path = list(sys.path)
        for k in _SCRUB:
            os.environ.pop(k, None)
        os.environ["DEEPMAP_HOME"] = str(self.home)
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.cfg)
        os.environ["GIT_CONFIG_GLOBAL"] = str(self.gitcfg)
        os.environ.update(self.extra)
        return self

    def __exit__(self, *exc):
        sys.path[:] = self.path
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _git(d: Path, *args) -> str:
    p = subprocess.run(["git", *(["-c", "core.longpaths=true"] if _WIN else []), "-C", str(d), *args],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
                       env=dict(os.environ, **_GIT_ENV))
    return (p.stdout or "").strip()


# a stub server: says it is running (its script was read — a caller may now remove the plugin dir), then sleeps
_STUB = ("import os, time\nr = os.environ.get('DMR10_READY')\nif r:\n    open(r, 'w').close()\n"
         "time.sleep(900)\n")


def _plugin(parent: Path, ver: str, *, label: str = "deep-map") -> Path:
    """A DeepMap plugin root: <parent>/<label>/<ver>/{.claude-plugin/plugin.json, build/<server scripts>} — the
    scripts only sleep (they stand in for the real servers; nothing listens)."""
    r = parent / label / ver
    (r / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (r / "build").mkdir(parents=True, exist_ok=True)
    (r / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "deep-map", "version": ver}),
                                                      encoding="utf-8")
    for s in _SCRIPTS.values():
        (r / "build" / s).write_text(_STUB, encoding="utf-8")
    return r.resolve()


def _installed(cfg: Path, *recs) -> None:
    """<cfg>/plugins/installed_plugins.json naming `recs` [(installPath, version)] as installed deep-map copies."""
    (cfg / "plugins").mkdir(parents=True, exist_ok=True)
    (cfg / "plugins" / "installed_plugins.json").write_text(json.dumps({"version": 2, "plugins": {
        "deep-map@deepfusionlabs": [{"scope": "user", "installPath": str(p), "version": v} for p, v in recs],
        "other@elsewhere": [{"scope": "user", "installPath": str(cfg / "nope"), "version": "99.0.0"}]}}),
        encoding="utf-8")


class _World:
    """One project (a git repo registered on (_S, _E)), a temp DEEPMAP_HOME / config dir, the plugin roots the stub
    servers run from, and the "current" plugin both sides treat as the code running now."""

    def __init__(self):
        self.base = Path(tempfile.mkdtemp(prefix="dmr10a")).resolve()
        self.home, self.cfg = self.base / "home", self.base / "claude"
        self.home.mkdir()
        self.cfg.mkdir()
        (self.home / "run").mkdir()
        self.gitcfg = self.base / "gitconfig"
        self.gitcfg.write_text("", encoding="utf-8")
        self.root = self.base / "proj"
        self.root.mkdir()
        with self.env():
            _git(self.root, "init", "-q")
            _git(self.root, "commit", "-q", "--allow-empty", "-m", "c1")
        self.root = self.root.resolve()
        (self.root / ".deepmap").mkdir()
        (self.root / ".deepmap" / "graph_build.json").write_text(json.dumps({"nodes": 5, "verdict": "READY"}),
                                                                   encoding="utf-8")
        plugins = self.base / "plugins"
        self.cur = _plugin(plugins / "cur", _CUR_VER)
        self.old = _plugin(plugins / "cache", "0.27.4")
        self.newer = _plugin(plugins / "cache", "99.0.0")
        self.twin = _plugin(plugins / "cache", _CUR_VER)              # the SAME version, another directory
        self.gone = _plugin(plugins / "removed", "0.27.3")            # removed once its servers run
        self.gone_sp = _plugin(plugins / "plug ins", "0.27.2")        # ... and one whose path holds a SPACE (L6)
        self.procs: list = []
        with self.env():
            self.register()
            self.key = PR.project_key(self.root)

    def env(self, extra=None):
        return _Env(self.home, self.cfg, self.gitcfg, extra)

    def register(self, search=_S, embed=_E) -> None:
        (self.home / "registry.json").write_text(json.dumps({"version": 1, "kind": "deepmap_port_registry",
                                                             "projects": {PR.project_key(self.root): {
                                                                 "root": self.root.as_posix(), "bolt_port": 17991,
                                                                 "http_port": 17792, "search_port": search,
                                                                 "embed_port": embed}}}), encoding="utf-8")

    def spawn(self, root: Path, name: str, port: int):
        """A stub server: `<python> <root>/build/<script>` (cwd outside every plugin root), started with the per-port
        env var provision sets. Returns its Popen once its argv is readable."""
        env = dict(os.environ)
        env[_PORT_ENV[name]] = str(port)
        ready = self.base / f"ready-{len(self.procs)}"
        env["DMR10_READY"] = str(ready)
        p = subprocess.Popen([PY, str(root / "build" / _SCRIPTS[name])], cwd=str(self.base), env=env,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.procs.append(p)
        deadline = time.time() + 60
        while time.time() < deadline and not ready.exists() and p.poll() is None:
            time.sleep(0.05)
        return p

    def clear_run(self) -> None:
        run = self.home / "run"
        for f in list(run.iterdir()):
            if f.is_file():
                f.unlink()

    def pidfile(self, name: str, port: int, pid: int, *, age: float | None = None) -> Path:
        pf = self.home / "run" / f"{name}-{port}.pid"
        pf.write_text(str(pid), encoding="utf-8")
        if age is not None:
            t = time.time() - age
            os.utime(pf, (t, t))
        return pf

    def owner(self, name: str, port: int, rec) -> Path:
        of = self.home / "run" / f"{name}-{port}.owner"
        of.write_text(rec if isinstance(rec, str) else json.dumps(rec), encoding="utf-8")
        return of

    def close(self) -> None:
        for p in self.procs:
            try:
                p.kill()
                p.wait(timeout=20)
            except Exception:
                pass
        _rmtree(self.base)


def _current(w: _World) -> dict:
    return PR.code_identity(w.cur / "build")


def _decide(w: _World, hs, *, ports=(_S, _E)):
    """(hook note, heal restarts {name: desc}, shared rule {name: verdict}) for the world as it stands. Both modules are
    pointed at the world's "current" plugin — the code the hook runs from / heal would restart onto."""
    cur = _current(w)
    with w.env(), _Patch(hs, _BUILD=w.cur / "build", _CODE_BUILD=w.cur / "build"), \
            _Patch(P, _code_identity=lambda: dict(cur)):
        note = hs.mcp_code_stale(w.root)
        heal = {n: P._own_server_code_stale(n, port, w.root) for n, port in zip((SEARCH, EMBED), ports)}
        shared_fn = getattr(PR, "own_server_code_stale", None)
        shared = {n: (shared_fn(w.root, n, port, cur) if shared_fn else "n/a") for n, port in zip((SEARCH, EMBED),
                                                                                                    ports)}
    return note, heal, shared


def _agree(label: str, w: _World, hs, *, expect=None, ports=(_S, _E)) -> None:
    note, heal, shared = _decide(w, hs, ports=ports)
    restarts = any(v is not None for v in heal.values())
    check(f"{label}: the note ({'promises a restart' if note else 'silent'}) == heal's decision "
          f"({'restarts' if restarts else 'leaves it'})", bool(note) == restarts,
          f"note={note} heal={heal} shared={shared}")
    if expect is not None:
        check(f"{label}: expected {'a restart' if expect else 'no restart'}", restarts is expect and bool(note) is expect,
              f"note={note} heal={heal}")
    if not isinstance(shared.get(SEARCH), str):
        got = any(bool(v) for v in shared.values())
        check(f"{label}: port_registry.own_server_code_stale (the shared rule) says the same", got == restarts,
              f"shared={shared} heal={heal}")


# =========================================================================== A: L1 — the paired gate
def gate_a_paired() -> None:
    print("\n-- A (L1): the hook's reconnect note and heal's code restart agree on every owner-record state --")
    hs = _load_hook("deepmap_hook_state")
    w = _World()
    try:
        with w.env():
            old = w.spawn(w.old, SEARCH, _S)
            old_e = w.spawn(w.old, EMBED, _E)
            newer = w.spawn(w.newer, SEARCH, _S)
            twin = w.spawn(w.twin, SEARCH, _S)
            gone = w.spawn(w.gone, SEARCH, _S)
            gone_sp = w.spawn(w.gone_sp, SEARCH, _S)
            cur = w.spawn(w.cur, SEARCH, _S)
        _rmtree(w.gone)                                   # a plugin update removed that version's directory
        _rmtree(w.gone_sp)
        created = {p.pid: P._pid_create_time(p.pid) for p in w.procs}   # provision's stamp: what records hold
        k = w.key
        cur_fields = {"code_root": w.cur.as_posix(), "code_version": _CUR_VER}
        old_fields = {"code_root": w.old.as_posix(), "code_version": "0.27.4"}
        dead = 4000000 + (os.getpid() % 90000)            # a pid that is not running

        def state(pid, owner=None, **kw):
            w.clear_run()
            w.pidfile(SEARCH, _S, pid, **kw)
            if owner is not None:
                w.owner(SEARCH, _S, owner)

        # ---- the review's L1 case and its siblings: an owner record of an EARLIER process on the port
        state(old.pid)
        _agree("no owner record, v0.27.4 server (the K3 upgrade)", w, hs, expect=True)
        state(old.pid, dict(cur_fields, pid=dead, project_key=k, created=1.0))
        _agree("L1: an owner record naming an EARLIER (dead) pid, the pid file's process runs 0.27.4", w, hs,
               expect=True)
        state(old.pid, dict(cur_fields, pid=old_e.pid, project_key=k))
        _agree("L1: an owner record naming an earlier pid that is ALIVE (another process now)", w, hs, expect=True)
        state(old.pid, {"pid": dead, "project_key": "0123456789abcdef", "root": "/elsewhere"})
        _agree("L1: an earlier process's record naming ANOTHER project (the port is registered to this one)", w, hs,
               expect=True)
        state(newer.pid, dict(cur_fields, pid=dead, project_key=k))
        _agree("an earlier pid's record + the process runs a NEWER version (K4)", w, hs, expect=False)
        state(old.pid, "{not json")
        _agree("an owner file that can't be read as a record (half written / garbage) = no record: the K3 evidence "
               "decides", w, hs, expect=True)
        state(old.pid, json.dumps([1, 2]))
        _agree("an owner file that is JSON but not a record = no record: the K3 evidence decides", w, hs, expect=True)
        state(newer.pid, "{not json")
        _agree("an unreadable owner file + the process runs a NEWER version (never a downgrade)", w, hs, expect=False)
        # ---- a record naming the pid-file pid
        state(old.pid, dict(old_fields, pid=old.pid, project_key=k, created=created[old.pid]))
        _agree("a matching record of this project, code 0.27.4 (J5)", w, hs, expect=True)
        state(old.pid, {"pid": old.pid, "project_key": k, "root": w.root.as_posix(), "created": created[old.pid]})
        _agree("a matching pre-J5 record (no code fields): the command line decides", w, hs, expect=True)
        state(old.pid, dict(old_fields, pid=old.pid, project_key="0123456789abcdef", created=created[old.pid]))
        _agree("a matching record naming ANOTHER project", w, hs, expect=False)
        state(old.pid, dict(cur_fields, pid=old.pid, project_key=k, created=created[old.pid]))
        _agree("a matching record that says the current code", w, hs, expect=False)
        state(old.pid, dict(old_fields, pid=old.pid, project_key=k, created=float(created[old.pid] or 0) - 5000
                            if not isinstance(created[old.pid], str) else "linux:-:1"))
        _agree("a matching record whose start stamp is another process's (a recycled pid)", w, hs, expect=False)
        state(old.pid, dict(old_fields, pid=old.pid, project_key=k))
        _agree("a matching record without a start stamp (the pid file's age decides)", w, hs)
        # ---- no record: the code the process runs (K3 / K4 / L4)
        state(newer.pid)
        _agree("no record, the process runs a NEWER version (never a downgrade)", w, hs, expect=False)
        _installed(w.cfg, (w.cur, _CUR_VER))
        state(twin.pid)
        _agree("L4: no record, the SAME version from another existing directory, current = the installed copy",
               w, hs, expect=False)
        _installed(w.cfg)
        state(gone.pid)
        _agree("no record, the process's plugin directory was removed", w, hs, expect=True)
        state(gone_sp.pid)
        _agree("L6: no record, a REMOVED plugin directory whose path holds a space (real argv)", w, hs, expect=True)
        state(gone_sp.pid, dict(cur_fields, pid=dead, project_key=k))
        _agree("L6 + L1: the same, behind an earlier process's owner record", w, hs, expect=True)
        # ---- L3: the process runs the CURRENT directory — restarted only when that directory was updated IN PLACE
        #      after the process started (a --plugin-dir / git-clone install): plugin.json or its own script rewritten
        state(cur.pid)
        _agree("no record, the process runs the CURRENT directory, nothing rewritten since it started", w, hs,
               expect=False)
        later = time.time() + 30
        pj, sc = w.cur / ".claude-plugin" / "plugin.json", w.cur / "build" / _SCRIPTS[SEARCH]
        before = {f: f.stat().st_mtime for f in (pj, sc)}
        readme = w.cur / "README.md"
        readme.write_text("docs\n", encoding="utf-8")
        os.utime(readme, (later, later))
        _agree("L3: only an unrelated file of that directory changed after the process started", w, hs, expect=False)
        os.utime(pj, (later, later))
        _agree("L3: that directory's plugin.json was rewritten after the process started (updated in place)", w, hs,
               expect=True)
        note = _decide(w, hs)[0]
        check("L3: the note names the in-place update (not 'a different version 0.28.0 ... than 0.28.0')",
              isinstance(note, dict) and "updated in place" in str(note.get("old")), note)
        state(cur.pid, {"pid": cur.pid, "project_key": k, "root": w.root.as_posix(), "created": created[cur.pid]})
        _agree("L3: a matching pre-J5 record (no code fields) on that updated directory", w, hs, expect=True)
        state(cur.pid, dict(cur_fields, pid=cur.pid, project_key=k, created=created[cur.pid]))
        _agree("a matching record naming that directory's current version: the version decides, not a file time",
               w, hs, expect=False)
        os.utime(pj, (before[pj], before[pj]))
        os.utime(sc, (later, later))
        state(cur.pid)
        _agree("L3: the server's own script was rewritten after the process started", w, hs, expect=True)
        os.utime(sc, (before[sc], before[sc]))
        # ---- no record: the pid-file / registry facts heal checks before it signals anything
        state(old.pid, age=3600)
        _agree("no record, the pid file is OLDER than its process (a recycled pid)", w, hs,
               expect=None if Path("/proc").is_dir() else False)
        state(old.pid)
        (w.home / "run" / f"{SEARCH}-{_S2}.pid").write_text(str(old.pid), encoding="utf-8")
        w.owner(SEARCH, _S2, {"pid": old.pid, "project_key": "0123456789abcdef", "created": created[old.pid]})
        _agree("no record, ANOTHER port's pid file + owner record name this very process", w, hs, expect=False)
        state(dead)
        _agree("the pid file names a dead pid", w, hs, expect=False)
        w.clear_run()
        _agree("no pid file at all", w, hs, expect=False)
        # the port is not registered to this project: .mcp.json names it, the registry gives the project another pair
        state(old.pid)
        with w.env():
            w.register(_S2, _E2)
        (w.root / ".mcp.json").write_text(json.dumps({"mcpServers": {
            SEARCH: {"type": "http", "url": f"http://127.0.0.1:{_S}/mcp"},
            EMBED: {"type": "http", "url": f"http://127.0.0.1:{_E}/mcp"}}}), encoding="utf-8")
        _agree("no record, the port is NOT registered to this project (only .mcp.json names it)", w, hs,
               expect=False)
        (w.root / ".mcp.json").unlink()
        with w.env():
            w.register()
        # both servers: the note fires if heal restarts EITHER
        w.clear_run()
        w.pidfile(SEARCH, _S, cur.pid)
        w.pidfile(EMBED, _E, old_e.pid)
        _agree("search on the current code, embed on 0.27.4 (no records): one restart -> the note", w, hs,
               expect=True)
    finally:
        w.close()


# =========================================================================== B: L6 — exact argv (POSIX)
def _fake_proc(base: Path, pid: int, argv: list, env: dict, *, start_ago: float) -> Path:
    """A Linux /proc for one process: <base>/<pid>/{cmdline, stat, environ}, /stat (btime), the boot id."""
    btime = int(time.time()) - 100000
    (base / "sys" / "kernel" / "random").mkdir(parents=True, exist_ok=True)
    (base / "sys" / "kernel" / "random" / "boot_id").write_text("dmr10-boot\n", encoding="utf-8")
    (base / "stat").write_text(f"cpu 1 2 3 4\nbtime {btime}\n", encoding="utf-8")
    d = base / str(pid)
    d.mkdir(parents=True, exist_ok=True)
    (d / "cmdline").write_bytes(b"\0".join(a.encode("utf-8") for a in argv) + b"\0")
    ticks = int((time.time() - start_ago - btime) * 100)
    (d / "stat").write_text(f"{pid} (python3) S " + " ".join(["0"] * 18) + f" {ticks} 0 0 0\n", encoding="utf-8")
    (d / "environ").write_bytes(b"\0".join(f"{k}={v}".encode("utf-8") for k, v in env.items()) + b"\0")
    return base


def gate_b_exact_argv() -> None:
    print("\n-- B (L6): a removed plugin root whose path holds a SPACE is read exactly on POSIX --")
    for fn in ("pid_argv", "cmdline_argv", "script_from_argv"):
        check(f"port_registry exposes {fn}() (the exact-argv reader heal and the hooks share)",
              callable(getattr(PR, fn, None)))
    removed = "/nonexistent-dmr10/plug ins/deep-map/0.27.4"
    script = f"{removed}/build/{_SCRIPTS[SEARCH]}"
    argv = ["/usr/bin/python3", script]
    # B1 — Linux, simulated through BOTH modules' seams: the same /proc, pid 4242 alive, started before its pid file
    hs = _load_hook("deepmap_hook_state")
    w = _World()
    try:
        fp = w.base / "fakeproc"
        _fake_proc(fp, 4242, argv, {"DEEPMAP_SEARCH_PORT": str(_S), "PATH": "/usr/bin"}, start_ago=120)
        w.clear_run()
        w.pidfile(SEARCH, _S, 4242)
        with _Patch(PR, _PROC=str(fp), _windows=lambda: False, _darwin=lambda: False), \
                _Patch(P, _PROC=str(fp), is_windows=lambda: False):
            got = PR.running_code_root(4242, SEARCH) if callable(getattr(PR, "running_code_root", None)) else None
            check("Linux: running_code_root reads the NUL-separated argv -> the removed root, space kept",
                  got == removed, got)
            if callable(getattr(PR, "pid_argv", None)):
                check("Linux: pid_argv == the process's argv, word for word", PR.pid_argv(4242) == argv,
                      PR.pid_argv(4242))
            _agree("Linux: a removed root with a space (no owner record, v0.27.4 server)", w, hs, expect=True)
    finally:
        w.close()
    # B2 — macOS: the kernel's argv (KERN_PROCARGS2) — its parser, then the reader seam
    parse = getattr(PR, "_parse_procargs2", None)
    check("port_registry has the KERN_PROCARGS2 parser", callable(parse))
    if callable(parse):
        body = b"/usr/bin/python3\0\0\0\0" + b"\0".join(a.encode() for a in argv) + b"\0" + b"HOME=/Users/x\0"
        raw = (2).to_bytes(4, sys.byteorder) + body
        check("KERN_PROCARGS2: argc + exec path + padding + argv (+ env) -> argv exactly", parse(raw) == argv,
              parse(raw))
        check("KERN_PROCARGS2: a truncated buffer -> None (never a partial argv)", parse(raw[:30]) is None)
        check("KERN_PROCARGS2: garbage -> None", parse(b"\xff\xff") is None and parse(b"") is None)
    if callable(getattr(PR, "_darwin_argv", None)):
        with tempfile.TemporaryDirectory(prefix="dmr10m") as td, \
                _Patch(PR, _PROC=td, _windows=lambda: False, _darwin=lambda: True,
                       _darwin_argv=lambda pid: list(argv) if int(pid) == 4343 else None):
            got = PR.running_code_root(4343, SEARCH)
            check("macOS: the reader keeps the argv exact (NUL-joined) -> the removed root with its space",
                  got == removed, got)
        check("macOS reader: not on macOS -> None (never a Linux / Windows sysctl)",
              sys.platform == "darwin" or PR._darwin_argv(os.getpid()) is None)
    # B3 — a lossy space-joined `ps` line (the last resort): the interpreter that exists anchors the script path
    if callable(getattr(PR, "script_from_cmdline", None)):
        with _Patch(PR, _windows=lambda: False):
            lossy = f"{PY} {script}"
            if lossy:
                check("a lossy `ps` line: an existing interpreter anchors a removed script path with a space",
                      PR.script_from_cmdline(lossy, _SCRIPTS[SEARCH]) == script,
                      PR.script_from_cmdline(lossy, _SCRIPTS[SEARCH]))
    # B4 — Windows: the command line split by CommandLineToArgvW's rules
    split = getattr(PR, "_split_windows_cmdline", None)
    check("port_registry splits a Windows command line by the argv rules", callable(split))
    if callable(split):
        cases = [r'"C:\Program Files\Python\python.exe" "C:\plug ins\deep-map\0.27.4\build\deepmap_search_server.py"',
                 r'C:\py\python.exe C:\x\build\deepmap_search_server.py', r'python.exe "C:\a b\c\\" next',
                 r'python.exe a\\\"b c', r'python.exe "a\"b" c', r'"C:\a b\py.exe" -X utf8 "C:\p q\b\x.py"',
                 r'py.exe "" x', r'py.exe "a b"c d', "  py.exe\targ  "]
        if _WIN:
            import ctypes
            from ctypes import wintypes
            shell32, k32 = ctypes.WinDLL("shell32"), ctypes.WinDLL("kernel32")
            shell32.CommandLineToArgvW.restype = ctypes.POINTER(ctypes.c_wchar_p)
            shell32.CommandLineToArgvW.argtypes = (wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int))
            k32.LocalFree.argtypes = (ctypes.c_void_p,)

            def real(s):
                n = ctypes.c_int(0)
                arr = shell32.CommandLineToArgvW(s, ctypes.byref(n))
                try:
                    return [arr[i] for i in range(n.value)]
                finally:
                    k32.LocalFree(arr)
            bad = [(c, split(c), real(c)) for c in cases if split(c) != real(c)]
            check("Windows: the split equals CommandLineToArgvW on every case", bad == [], bad)
        lc = subprocess.list2cmdline([r"C:\a b\python.exe", r"C:\plug ins\x\build\deepmap_search_server.py", "t\\"])
        check("Windows: a subprocess.list2cmdline line splits back to its argv",
              split(lc) == [r"C:\a b\python.exe", r"C:\plug ins\x\build\deepmap_search_server.py", "t\\"], split(lc))


# =========================================================================== C: L4 — two installed copies, one version
def gate_c_equal_version() -> None:
    print("\n-- C (L4): two installed copies of the SAME version never ping-pong the servers --")
    base = Path(tempfile.mkdtemp(prefix="dmr10c")).resolve()
    try:
        a = _plugin(base / "acct-a" / "plugins" / "cache", "0.28.0")
        b = _plugin(base / "acct-b" / "plugins" / "cache", "0.28.0")
        b1 = _plugin(base / "acct-b" / "plugins" / "cache", "0.28.1")
        cfg_a, cfg_b, cfg_ab = base / "cfg-a", base / "cfg-b", base / "cfg-ab"
        _installed(cfg_a, (a, "0.28.0"))
        _installed(cfg_b, (b, "0.28.0"))
        _installed(cfg_ab, (a, "0.28.0"), (b, "0.28.0"))
        ia, ib, ib1 = (PR.code_identity(r / "build") for r in (a, b, b1))
        prv = getattr(P, "_code_restart_verdict", None)
        shared = getattr(PR, "code_restart_verdict", None)

        def verdicts(rec: Path, rec_ver, cur: dict, cfg: Path):
            saved = os.environ.get("CLAUDE_CONFIG_DIR")
            os.environ["CLAUDE_CONFIG_DIR"] = str(cfg)
            try:
                hook = PR.server_code_stale({"code_root": rec.as_posix(), "code_version": rec_ver}, cur)
                heal = prv(rec.as_posix(), rec_ver, cur) if prv else None
                sh = shared(rec.as_posix(), rec_ver, cur) if shared else None
                heal_pre = prv(rec.as_posix(), None, cur) if prv else None     # a v0.27.4-style server (no record)
                hook_pre = PR.server_code_stale({"code_root": rec.as_posix()}, cur)
                return hook, heal, sh, heal_pre, hook_pre
            finally:
                if saved is None:
                    os.environ.pop("CLAUDE_CONFIG_DIR", None)
                else:
                    os.environ["CLAUDE_CONFIG_DIR"] = saved

        for label, (rec, cur, cfg) in {
                "two accounts: window A (A installed) judges servers on B": (b, ia, cfg_a),
                "two accounts: window B (B installed) judges servers on A": (a, ib, cfg_b),
                "two marketplaces in one config: A judges B": (b, ia, cfg_ab),
                "two marketplaces in one config: B judges A": (a, ib, cfg_ab)}.items():
            got = verdicts(rec, "0.28.0", cur, cfg)
            check(f"{label} -> never restarted (hook, heal, shared rule; with and without a record)",
                  not any(v is True for v in got), got)

        def alternate(cfgs, curs, start: Path, rounds=6) -> int:
            """Alternating SessionStart heals of two windows; how many times the servers move."""
            on, moves = start, 0
            for i in range(rounds):
                cur, cfg = curs[i % 2], cfgs[i % 2]
                ver = PR._plugin_version(on)
                v_heal = verdicts(on, ver, cur, cfg)[1] if prv else verdicts(on, ver, cur, cfg)[0]
                if v_heal is True:
                    on = Path(cur["code_root"])
                    moves += 1
            return moves
        check("two accounts at 0.28.0, alternating heals: the servers never move", alternate(
            (cfg_a, cfg_b), (ia, ib), b) == 0)
        _installed(cfg_b, (b1, "0.28.1"))
        n = alternate((cfg_a, cfg_b), (ia, ib1), a)
        check("control: account B updated to 0.28.1 -> the servers move forward exactly once, never back", n == 1, n)
        got = verdicts(b1, "0.28.1", ia, cfg_a)
        check("control: window A (0.28.0) never downgrades 0.28.1 servers (hook + heal)",
              got[0] is False and got[1] is not True, got)
        nightly = _plugin(base / "dev", "nightly")
        got = verdicts(nightly, "nightly", ia, cfg_ab)
        check("versions that can't be ordered ('nightly' vs 0.28.0, another existing dir) -> never restarted, whatever "
              "installed_plugins.json lists", not any(v is True for v in got), got)
    finally:
        _rmtree(base)


# =========================================================================== D: L7 — hooks.json python -> python3
def _hook_commands() -> list:
    data = json.loads((HOOKS / "hooks.json").read_text(encoding="utf-8"))
    return [h.get("command", "") for groups in data.get("hooks", {}).values() for g in groups
            for h in g.get("hooks", [])]


_FAKE_HOOK = r'''import os, sys
data = sys.stdin.read()
if not data.strip():
    raise SystemExit(0)   # the real hooks' ROUND-11 R3 contract: a drained stdin = another interpreter did the work
with open(os.environ["DMR10_LOG"], "a", encoding="utf-8") as f:
    f.write("ran:" + os.path.basename(__file__) + ":" + data.strip() + "\n")
'''


def _shim_dir(d: Path, names) -> Path:
    """Launcher shims that log which name ran, then run the real interpreter with the same arguments."""
    d.mkdir(parents=True, exist_ok=True)
    py = PY.replace("\\", "/")
    for n in names:
        (d / n).write_text(f'#!/bin/sh\necho "{n}" >> "$DMR10_LOG"\nexec "{py}" "$@"\n', encoding="utf-8",
                           newline="\n")
        os.chmod(d / n, 0o755)
        if _WIN:
            (d / f"{n}.cmd").write_text(f'@echo {n}>>"%DMR10_LOG%"\r\n@"{PY}" %*\r\n', encoding="utf-8")
    return d


def gate_d_hooks_json_fallback() -> None:
    print("\n-- D (L7 + ROUND-11 R3): hooks.json runs python, else python3, each hook once (sh and cmd.exe) --")
    cmds = _hook_commands()
    check("(hooks.json registers the four hooks)", len(cmds) >= 4, cmds)
    pat = re.compile(r'^python -X utf8 "\$\{CLAUDE_PLUGIN_ROOT\}/hooks/([a-z_]+\.py)" ; '
                     r'python3 -X utf8 "\$\{CLAUDE_PLUGIN_ROOT\}/hooks/([a-z_]+\.py)" ; exit 0$')
    shape = [c for c in cmds if not (pat.match(c) and pat.match(c).group(1) == pat.match(c).group(2))]
    check("every command: `python -X utf8 <hook> ; python3 -X utf8 <the same hook> ; exit 0`", shape == [], shape)
    redirect = [c for c in cmds if re.search(r"[<>]|2>|&>|&&|\|\||\$\(|`|command -v|where ", c)]
    check("no shell-specific redirection / chaining — no `||` (Windows PowerShell 5.1 can't parse it), `&&`, "
          "redirection, `$()`", redirect == [], redirect)
    if shape:
        return
    base = Path(tempfile.mkdtemp(prefix="dmr10d")).resolve()
    try:
        plugin = base / "plugin"
        (plugin / "hooks").mkdir(parents=True)
        for c in cmds:
            (plugin / "hooks" / pat.match(c).group(1)).write_text(_FAKE_HOOK, encoding="utf-8")
        log = base / "log.txt"
        proot = plugin.as_posix()
        sh = shutil.which("sh") or ("/bin/sh" if Path("/bin/sh").exists() else None)
        runs = {"python only": ("python",), "python3 only": ("python3",), "both": ("python", "python3")}
        for shell in (("sh", sh), ("cmd.exe", os.environ.get("COMSPEC") if _WIN else None)):
            if not shell[1]:
                check(f"({shell[0]} available)", True, "skipped")
                continue
            for label, names in runs.items():
                bindir = _shim_dir(base / f"bin-{shell[0]}-{label.replace(' ', '')}", names)
                ok = []
                for c in cmds:
                    hook = pat.match(c).group(1)
                    log.write_text("", encoding="utf-8")
                    line = c.replace("${CLAUDE_PLUGIN_ROOT}", proot)   # Claude Code substitutes it
                    env = {k: v for k, v in os.environ.items() if k.upper() not in ("PATH", "PYTHONHOME")}
                    env["DMR10_LOG"] = str(log)
                    if shell[0] == "sh":
                        env["PATH"] = str(bindir)
                        argv = [shell[1], "-c", line]
                    else:   # cmd /s /c "<line>": the outer quotes go, the line runs verbatim (a list would re-quote it)
                        env["PATH"] = str(bindir) + ";" + str(Path(os.environ.get("SystemRoot", r"C:\Windows"))
                                                              / "System32")
                        argv = f'"{shell[1]}" /d /s /c "{line}"'
                    p = subprocess.run(argv, input='{"session_id": "d1"}', capture_output=True, text=True,
                                       encoding="utf-8", errors="replace", env=env, timeout=120)
                    got = [ln for ln in log.read_text(encoding="utf-8").splitlines() if ln.strip()]
                    worker = "python" if "python" in names else "python3"
                    ran = f'ran:{hook}:{{"session_id": "d1"}}'
                    if shell[0] == "cmd.exe":   # no `;` separator: only `python` is tried (the rest are its args)
                        want = [worker, ran] if worker == "python" else []
                        rc_ok = (p.returncode == 0) if worker == "python" else True
                    else:                       # python3 still STARTS after python, finds stdin drained, exits
                        want = [worker, ran] + (["python3"] if worker == "python" and "python3" in names else [])
                        rc_ok = p.returncode == 0
                    ok.append((hook, p.returncode, got) if not (rc_ok and got == want) else None)
                bad = [x for x in ok if x]
                if shell[0] == "cmd.exe" and "python" not in names:
                    what = "runs nothing (cmd.exe tries only `python`), quietly"
                else:
                    what = f"runs ONCE via {'python' if 'python' in names else 'python3'} with the payload on stdin"
                check(f"{shell[0]}, {label} on PATH: each hook {what}, exit 0", bad == [], bad)
    finally:
        _rmtree(base)


# =========================================================================== E: docs
_PY_ENTRY = re.compile(r'python\s+"?\$\{CLAUDE_PLUGIN_ROOT\}/build/')


def gate_e_docs() -> None:
    print("\n-- E (L7 docs + doc drift): commands name the python3 fallback; the sync-flag names its writer --")
    for md_path in sorted(COMMANDS.glob("*.md")):
        md = md_path.read_text(encoding="utf-8")
        if not _PY_ENTRY.search(md):
            continue
        flat = " ".join(md.split())
        check(f"{md_path.name}: says to use python3 where python is absent",
              re.search(r"python3.{0,40}where\s+`?python`?\s+is\s+(absent|missing|not found)", flat, re.I)
              is not None, flat[:300])
        fm = re.search(r"^allowed-tools:\s*(.*)$", md, flags=re.M)
        allowed = fm.group(1) if fm else ""
        py = set(re.findall(r"Bash\((?:[A-Z0-9_]+=\S+ )*python (\$\{CLAUDE_PLUGIN_ROOT\}/build/[a-z_]+\.py)", allowed))
        py3 = set(re.findall(r"Bash\((?:[A-Z0-9_]+=\S+ )*python3 (\$\{CLAUDE_PLUGIN_ROOT\}/build/[a-z_]+\.py)",
                             allowed))
        check(f"{md_path.name}: allowed-tools lets the python3 form run the same entry points", py and py <= py3,
              f"python={sorted(py)} python3={sorted(py3)}")
    sf = (HOOKS / "deepmap_postedit_syncflag.py").read_text(encoding="utf-8")
    m = re.search(r'^_GIT_HOOK_MARKER\s*=.*$', sf, flags=re.M)
    line = m.group(0) if m else ""
    check("syncflag: the marker comment names provision.py git-hook --install as the writer (not the installers)",
          "git-hook" in line and "install.sh / install.ps1 write" not in line, line)


# =========================================================================== F: L5 (hook side) — the sync-flag claim
def _sq(s: str) -> str:
    return "'" + str(s).replace("'", "'\\''") + "'"


def gate_f_syncflag_interpreter() -> None:
    print("\n-- F (L5, hook side): no 'a commit re-syncs it' claim for a block whose Python can never start --")
    sf = _load_hook("deepmap_postedit_syncflag")
    check("syncflag has the interpreter check (_interp_runs)", callable(getattr(sf, "_interp_runs", None)))
    base = Path(tempfile.mkdtemp(prefix="dmr10f")).resolve()
    try:
        gitcfg = base / "gitconfig"
        gitcfg.write_text("", encoding="utf-8")
        root = base / "proj"
        root.mkdir()
        saved = {k: os.environ.get(k) for k in ("GIT_CONFIG_GLOBAL", "DEEPMAP_HOME")}
        os.environ["GIT_CONFIG_GLOBAL"] = str(gitcfg)
        os.environ["DEEPMAP_HOME"] = str(base / "home")
        try:
            _git(root, "init", "-q")
            _git(root, "commit", "-q", "--allow-empty", "-m", "c1")
            root = root.resolve()
            top = _git(root, "rev-parse", "--show-toplevel")
            cli = (REPO / "build" / "deepmap_cli.py").resolve().as_posix()
            py = Path(PY).resolve().as_posix()

            def line(interp: str) -> str:
                return (f"( PYTHONUTF8=1 nohup {interp} {_sq(cli)} sync {_sq(root.as_posix())} >/dev/null 2>&1 & ) "
                        "|| true   # deepmap")
            cases = (
                ("an existing interpreter path", _sq(py), True),
                ("`'py -3'` — a command line written as ONE sh word (the review's dead hook)", _sq("py -3"), False),
                ("`'/usr/bin/env python3'` as one word", _sq("/usr/bin/env python3"), False),
                ("a rooted interpreter that does not exist", _sq((base / "gone venv" / "python").as_posix()), False),
                ("a directory, not an interpreter", _sq(base.as_posix()), False),
                ("a bare command that is not on PATH", _sq("dmr10-no-such-python"), False),
                ("an interpreter the shell computes ($PY) — can't be judged here", '"$PY"', True),
            )
            for label, interp, want in cases:
                got = sf._line_syncs(line(interp), root, top)
                check(f"_line_syncs, {label} -> {'claim' if want else 'no claim'}", got is want, line(interp))
            hook = Path(_git(root, "rev-parse", "--git-path", "hooks/post-commit"))
            hook = hook if hook.is_absolute() else root / hook
            hook.parent.mkdir(parents=True, exist_ok=True)
            for label, interp, want in (("the dead `'py -3'` block", _sq("py -3"), False),
                                        ("control: the same block with a real interpreter", _sq(py), True)):
                hook.write_text("#!/bin/sh\n# >>> deepmap (managed; existing hook preserved on chain) >>>\n"
                                + line(interp) + "\n# <<< deepmap <<<\n", encoding="utf-8", newline="\n")
                if not _WIN:
                    os.chmod(hook, 0o755)
                check(f"_git_hook_installed: {label} -> {'claim' if want else 'no claim'}",
                      sf._git_hook_installed(root) is want)
            blk = getattr(P, "_git_hook_block", None)
            if callable(blk):
                saved_py = os.environ.pop("DEEPMAP_PYTHON", None)
                try:
                    text = blk(root, top)
                finally:
                    if saved_py is not None:
                        os.environ["DEEPMAP_PYTHON"] = saved_py
                ok = [ln for ln in text.splitlines() if sf._line_syncs(ln, root, top)]
                check("drift lock: provision's REAL post-commit block still gets the claim", bool(ok), text)
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
    finally:
        _rmtree(base)


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 72)
    print("DeepMap — round-10 W1: one code-restart rule for the hook note + heal (L1/L4/L6), python3 fallback (L7)")
    print(f"(repo under test: {REPO})")
    print("=" * 72)
    if not shutil.which("git"):
        print("RESULT: GREEN — 0/0 gates passed. (git not on PATH: skipped)")
        return 0
    for g in (gate_a_paired, gate_b_exact_argv, gate_c_equal_version, gate_d_hooks_json_fallback, gate_e_docs,
              gate_f_syncflag_interpreter):
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
