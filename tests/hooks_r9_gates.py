#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ROUND-9 W1 gates: the hook side of the REAL plugin upgrade (K3), restarts that only move FORWARD (K4, port_registry
side), the sync flag recognising an install.ps1-written hook (K6), and hooks_r7_gates B no longer racing the wall
clock (K7).

  A  K4 — port_registry.server_code_stale is ORDERED. It used to answer True for ANY other code, so a Claude Code
     window still on the previous plugin version restarted the servers a newer window had just started (SessionStart
     fires on startup, resume, /clear and /compact) and two windows ping-ponged them. Now: a newer version replaces
     an older one; an older one never replaces a newer one; the same version from another directory is replaced
     only when that directory's code is gone (ROUND-10 L4 — round 9 also replaced it when the current code was the
     copy Claude Code lists as installed, and two installed copies of one version ping-ponged); code that is no
     longer on disk is always replaced (nothing else can serve it). compare_versions orders dotted versions +
     pre-release tags.
  B  K3 (hook side) — v0.27.4, the release real users run, wrote NO owner sidecar (only run/<name>-<port>.pid). So
     after the upgrade mcp_code_stale returned None and the SessionStart notify said nothing while heal restarted
     the servers under the session's feet. It now judges such a server like heal does: the port is registered to
     THIS project, the pid file names a live process, and that process's command line runs this server's script
     from another DeepMap root (port_registry.pid_cmdline / running_code_root — a sub-millisecond read, no
     PowerShell). A REAL child process whose command line runs an old plugin's server script proves the reader.
  C  K3 (belt and braces) — whatever made the heal restart servers that were UP when the session started (old code,
     a stale config, an unreadable command line), the pre-search nudge's one follow-up now tells the agent to
     reconnect via /mcp — unless the SessionStart notify already said so.
  D  K6 — install.ps1 writes `sync "C:\\Src\\proj"` (a backslash Windows path in double quotes; sh keeps `\\S`
     literally), but the sync flag unescaped EVERY backslash (-> `C:Srcproj`) and never made its true claim. Only
     sh's double-quote escapes (\\" \\\\ \\$ \\`) are undone now.
  E  K7 — hooks_r7_gates B1/B3 measured the WHOLE notify call against tight wall-clock bounds and went RED on a
     loaded box (review: 4.79 s vs the 4.5 s bound under CPU load). Run under an injected +delay on every git call
     the notify makes (the load the review measured), section B must stay GREEN: it now checks the poller by event
     ordering, poll counts and requested-sleep accounting instead.
  F  Docs: plugin-update.md says the next session start moves a project's servers onto the new version (and an older
     window never moves them back); SKILL.md says the reconnect note may come at the first search after the restart.

Hermetic: temp project dirs, a temp DEEPMAP_HOME and CLAUDE_CONFIG_DIR, temp git repos; the MCP port probe is stubbed
in-process; the only processes started are this gate's own sleeping stub "servers" (killed by their Popen handles)
and python subprocesses. No Neo4j, no sockets, no network; never touches ~/.deepmap, ~/.claude or the committed
.mcp.json. Exit 0 = GREEN.
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
REAL_REPO = Path(__file__).resolve().parent.parent
HOOKS = REPO / "hooks"
BUILD = REPO / "build"
TESTS = REPO / "tests"
PY = sys.executable
sys.path.insert(0, str(BUILD))
import port_registry as PR  # noqa: E402

_PASS = 0
_FAIL = 0
_SCRUB = ("PYTHONUTF8", "PYTHONIOENCODING", "DEEPMAP_HOOKS", "DEEPMAP_HEAL", "DEEPMAP_NUDGE",
          "DEEPMAP_SYNC_FLAG", "DEEPMAP_SYNC_FLAG_EVERY", "DEEPMAP_ANNOUNCE", "DEEPMAP_ONBOARD",
          "DEEPMAP_HIBERNATE", "DEEPMAP_RETRIEVAL_LOG", "CLAUDE_PROJECT_DIR", "DEEPMAP_PROJECT_ROOT",
          "NEO4J_URI", "DEEPMAP_LEARNING", "CLAUDE_PLUGIN_ROOT", "DEEPMAP_NOTIFY_HEAL_WAIT", "DEEPMAP_PYTHON")
_GIT_ENV = {"GIT_AUTHOR_NAME": "g", "GIT_AUTHOR_EMAIL": "g@x", "GIT_COMMITTER_NAME": "g",
            "GIT_COMMITTER_EMAIL": "g@x", "GIT_CONFIG_NOSYSTEM": "1"}
_WIN = os.name == "nt"
_LONG = ["-c", "core.longpaths=true"] if _WIN else []
SEARCH, EMBED = "deepmap-search", "deepmap-embed"
_SCRIPTS = {SEARCH: "deepmap_search_server.py", EMBED: "deepmap_embed_server.py"}
_S, _E = 18931, 18932


def check(label: str, cond: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {label}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}{(' — ' + str(detail)[:700]) if detail else ''}")


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"dmr9_{name}_{time.monotonic_ns()}", HOOKS / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _tmpdir(prefix="dmr9w1"):
    return tempfile.TemporaryDirectory(prefix=prefix, ignore_cleanup_errors=True)


class _PythonOnPath:
    """ROUND-11 L3: a fixture that spells the interpreter as a bare `python` — the literal line an installer wrote — needs
    that word to resolve: the sync flag's _interp_runs (ROUND-10 L5) rightly makes no claim for a `python` that is not
    on PATH, which is every python3-only host (stock Debian / Ubuntu / macOS). When PATH lacks `python`, a temp dir
    holding a `python` launcher for the running interpreter goes first on PATH for the duration; a host that has
    `python` is left untouched."""

    def __enter__(self):
        self.saved = os.environ.get("PATH")
        self.tmp = None
        if shutil.which("python"):
            return self
        self.tmp = tempfile.TemporaryDirectory(prefix="dmr11py", ignore_cleanup_errors=True)
        d = Path(self.tmp.name)
        if _WIN:
            (d / "python.cmd").write_text(f'@"{sys.executable}" %*\r\n', encoding="utf-8")
        else:
            f = d / "python"
            f.write_text("#!/bin/sh\nexec '" + sys.executable.replace("'", "'\\''") + "' \"$@\"\n", encoding="utf-8")
            os.chmod(f, 0o755)
        os.environ["PATH"] = str(d) + os.pathsep + (self.saved or "")
        return self

    def __exit__(self, *exc):
        if self.saved is None:
            os.environ.pop("PATH", None)
        else:
            os.environ["PATH"] = self.saved
        if self.tmp is not None:
            self.tmp.cleanup()
        return False


def _git(d: Path, *args) -> str:
    p = subprocess.run(["git", *_LONG, "-C", str(d), *args], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=60, env=dict(os.environ, **_GIT_ENV))
    return (p.stdout or "").strip()


def _repo(parent: Path, name: str = "proj") -> Path:
    d = parent / name
    d.mkdir(parents=True, exist_ok=True)
    _git(d, "init", "-q")
    _git(d, "commit", "-q", "--allow-empty", "-m", "c1")
    return d.resolve()


def _manifest(root: Path, verdict="READY", nodes=10) -> Path:
    d = root / ".deepmap"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "graph_build.json"
    p.write_text(json.dumps({"nodes": nodes, "extractor": "graphify", "verdict": verdict}), encoding="utf-8")
    return p


def _register(home: Path, root: Path, *, search=_S, embed=_E, bolt=17791, http=17592) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "registry.json").write_text(json.dumps({"version": 1, "kind": "deepmap_port_registry", "projects": {
        PR.project_key(root): {"root": str(root).replace("\\", "/"), "bolt_port": bolt, "http_port": http,
                               "search_port": search, "embed_port": embed}}}), encoding="utf-8")


def _mcp_json(root: Path, s=_S, e=_E) -> None:
    (root / ".mcp.json").write_text(json.dumps({"mcpServers": {
        SEARCH: {"type": "http", "url": f"http://127.0.0.1:{s}/mcp"},
        EMBED: {"type": "http", "url": f"http://127.0.0.1:{e}/mcp"}}}), encoding="utf-8")


def _plugin(parent: Path, ver: str, label: str = "deep-map", *, scripts=False) -> Path:
    """A DeepMap plugin root on disk: <parent>/<label>/<ver>/{.claude-plugin/plugin.json, build/}."""
    r = parent / label / ver
    (r / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (r / "build").mkdir(parents=True, exist_ok=True)
    (r / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "deep-map", "version": ver}),
                                                      encoding="utf-8")
    if scripts:
        for s in _SCRIPTS.values():
            (r / "build" / s).write_text("import time\ntime.sleep(180)\n", encoding="utf-8")
    return r


def _installed(cfg: Path, *roots) -> None:
    """A Claude Code installed_plugins.json (format version 2) naming `roots` as the installed deep-map copy."""
    (cfg / "plugins").mkdir(parents=True, exist_ok=True)
    (cfg / "plugins" / "installed_plugins.json").write_text(json.dumps({"version": 2, "plugins": {
        "deep-map@deepfusionlabs": [{"scope": "user", "installPath": str(r), "version": "x"} for r in roots],
        "other@elsewhere": [{"scope": "user", "installPath": str(cfg / "nope")}]}}), encoding="utf-8")


class _Env:
    """Scrub the knobs, point DEEPMAP_HOME at `home` (and CLAUDE_CONFIG_DIR at `cfg` — never the real ~/.claude),
    apply `extra`; restore on exit."""

    def __init__(self, home: Path, cfg: Path, extra=None):
        self.home, self.cfg, self.extra = home, cfg, dict(extra or {})

    def __enter__(self):
        keys = set(_SCRUB) | {"DEEPMAP_HOME", "CLAUDE_CONFIG_DIR"} | set(self.extra)
        self.saved = {k: os.environ.get(k) for k in keys}
        for k in _SCRUB:
            os.environ.pop(k, None)
        os.environ["DEEPMAP_HOME"] = str(self.home)
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.cfg)
        os.environ.update(self.extra)
        return self

    def __exit__(self, *exc):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class _Patch:
    """Set attributes on an object for the duration of a with-block (restored on exit, even if missing before)."""

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


def _inproc(mod, payload: dict, home: Path, cfg: Path, *, up=(), env=None) -> str:
    """Run a hook's main() in-process: faked stdin/stdout, `up` = the ports the (stubbed) probe sees listening."""
    hs = mod._hs
    saved_io, saved_probe = (sys.stdin, sys.stdout), hs.port_listening
    with _Env(home, cfg, env):
        try:
            ups = {int(p) for p in up}
            hs.port_listening = lambda port, host="127.0.0.1", timeout=None: int(port) in ups
            sys.stdin = io.StringIO(json.dumps(payload))
            sys.stdout = io.StringIO()
            mod.main()
            return sys.stdout.getvalue()
        finally:
            sys.stdin, sys.stdout = saved_io
            hs.port_listening = saved_probe


def _ctx(out: str) -> str:
    try:
        return json.loads(out.strip().splitlines()[-1])["hookSpecificOutput"]["additionalContext"]
    except Exception:
        return ""


def _cur_version():
    try:
        return json.loads((REPO / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"]
    except Exception:
        return None


def _older_than(ver) -> str:
    """A release version strictly OLDER than `ver`: 0.5.0 -> 0.4.9, 1.2.3 -> 1.2.2, 2.0.0 -> 1.99.9. The stand-ins
    for "an older build" (pid files only, no owner sidecar) are derived from the REAL plugin version under test, so
    they stay older than it whatever the release line is (the version line restarted at 0.5.0 for the public
    release, below the synthetic 0.27.4 these sections used). "0.0.1" when `ver` can't be read."""
    try:
        nums = [int(x) for x in str(ver).strip().lstrip("vV").split("+")[0].split("-")[0].split(".")]
    except Exception:
        return "0.0.1"
    nums = (nums + [0, 0, 0])[:3]
    if nums[2] > 0:
        return f"{nums[0]}.{nums[1]}.{nums[2] - 1}"
    if nums[1] > 0:
        return f"{nums[0]}.{nums[1] - 1}.9"
    if nums[0] > 0:
        return f"{nums[0] - 1}.99.9"
    return "0.0.0-rc.1"


OLD_VER = _older_than(_cur_version())     # what an older (pre-owner-record) build's plugin.json says
GONE_VER = _older_than(OLD_VER)           # an even older build whose plugin dir the update removed


# =========================================================================== A: K4 ordered restarts
def gate_a_ordered() -> None:
    print("\n-- A (K4): server_code_stale only ever moves a server FORWARD --")
    # (ROUND-10 L4 retired installed_plugin_roots / is_installed_plugin_root: an equal version never restarts, so the
    # rule no longer asks which copy Claude Code lists as installed — see code_restart_agreement_r10_gates C)
    for fn in ("compare_versions", "code_present", "server_code_stale", "code_identity"):
        check(f"port_registry exposes {fn}()", callable(getattr(PR, fn, None)))
    cv = getattr(PR, "compare_versions", None)
    if callable(cv):
        for a, b, want in (("0.28.0", "0.27.4", 1), ("0.27.4", "0.28.0", -1), ("0.27.10", "0.27.9", 1),
                           ("0.28", "0.28.0", 0), ("v0.28.0", "0.28.0", 0), ("0.28.0-rc.1", "0.28.0", -1),
                           ("0.28.0", "0.28.0-rc.2", 1), ("0.28.0-rc.2", "0.28.0-rc.10", -1),
                           ("0.28.0+build7", "0.28.0", 0), ("1.0.0", "0.99.99", 1),
                           (None, "0.28.0", None), ("0.28.0", "", None), ("garbage", "0.28.0", None),
                           (True, "1", None)):
            try:
                got = cv(a, b)
            except Exception as exc:  # noqa: BLE001
                got = f"raised {exc!r}"
            check(f"compare_versions({a!r}, {b!r}) == {want!r}", got == want, got)
    st = PR.server_code_stale
    with _tmpdir() as tw, _tmpdir() as tc, _tmpdir() as th:
        base, cfg, home = Path(tw), Path(tc), Path(th)
        old = _plugin(base / "cache", "0.27.4")
        a = _plugin(base / "cache", "0.28.0")
        b = _plugin(base / "cache", "0.28.1")
        dev = _plugin(base / "dev", "0.28.1", "deep-map-dev")
        ia, ib, idev = (PR.code_identity(r / "build") for r in (a, b, dev))
        gone = (base / "deleted" / "deep-map" / "0.29.0").as_posix()
        with _Env(home, cfg):
            _installed(cfg, b)
            check("code_present: a root with build/ yes; a deleted root no", PR.code_present(str(a))
                  and not PR.code_present(gone))
            # the multi-window ping-pong (review K4): A still on 0.28.0, B updated to 0.28.1 (installed)
            check("B (0.28.1) sees servers A started on 0.28.0 -> stale (restart forward)", st(ia, ib) is True)
            check("A (0.28.0) sees servers B started on 0.28.1 -> NOT stale: an older window never downgrades",
                  st(ib, ia) is False, st(ib, ia))
            check("...not even when A's copy is the one Claude Code lists as installed (version order wins)",
                  (_installed(cfg, a), st(ib, ia))[1] is False)
            _installed(cfg, b)
            check("the old record without a version, judged by the version its root holds on disk (0.27.4 < 0.28.1)",
                  st({"code_root": old.as_posix()}, ib) is True)
            check("...and a root holding a NEWER version is left alone (0.28.1 running, 0.28.0 current)",
                  st({"code_root": b.as_posix()}, ia) is False)
            check("recorded code that is no longer on disk -> stale whatever its version (nothing else serves it)",
                  st({"code_root": gone, "code_version": "0.29.0"}, ia) is True)
            # the same version from two directories: a dev tree next to the installed plugin — ROUND-10 L4: never
            # restarted either way while both directories exist (two accounts / two marketplaces each "install"
            # their own copy, and a tie-break by installed copy ping-ponged the servers)
            check("same version, another existing root, current is the INSTALLED copy -> NOT stale (L4: no ping-pong)",
                  st(idev, ib) is False, st(idev, ib))
            check("same version, another existing root, current is NOT the installed copy -> left alone (no ping-pong)",
                  st(ib, idev) is False, st(ib, idev))
            _installed(cfg)   # nothing installed (a dev-only machine)
            check("same version, two existing roots, neither installed -> left alone both ways",
                  st(ib, idev) is False and st(idev, ib) is False)
            _installed(cfg, b)
            # the same directory updated in place
            check("same root, another recorded version (in-place update) -> stale, whichever way",
                  st(dict(ib, code_version="0.27.0"), ib) is True and st(dict(ib, code_version="9.0.0"), ib) is True)
            check("same root + same version -> not stale", st(dict(ib), ib) is False)
            # records that carry only a version
            check("version-only record, older -> stale", st({"code_version": "0.1.0"}, ib) is True)
            check("version-only record, NEWER -> not stale", st({"code_version": "9.9.9"}, ib) is False)
            check("version-only record whose order can't be told -> unknown (None), never guessed",
                  st({"code_version": "nightly"}, ib) is None)
            check("a record without the code fields -> None (unchanged J5 contract)",
                  st({"pid": 1, "project_key": "k"}, ib) is None)
            (cfg / "plugins" / "installed_plugins.json").write_text("{corrupt", encoding="utf-8")
            check("a corrupt installed_plugins.json changes nothing: same-version roots are left alone",
                  st(idev, ib) is False and st(ib, idev) is False)


# =========================================================================== B: K3 hook side (pre-J5 servers)
class _World:
    """This project's run dir as an older build (before owner records) leaves it: per-port pid files, NO owner
    sidecar (unless given)."""

    def __init__(self, home: Path, pids: dict, owners=None):
        run = home / "run"
        run.mkdir(parents=True, exist_ok=True)
        for (name, port), pid in pids.items():
            (run / f"{name}-{port}.pid").write_text(str(pid), encoding="utf-8")
            (run / f"{name}-{port}.owner").unlink(missing_ok=True)
        for (name, port), rec in (owners or {}).items():
            (run / f"{name}-{port}.owner").write_text(json.dumps(rec), encoding="utf-8")


class _started_with_code:
    """ROUND-11 R4: the stand-in servers start the moment `root`'s code (plugin.json + both server scripts — the files
    port_registry.code_replaced_since reads) was last written, and this project's pid files are stamped at that same
    moment (heal's rule: a pid-file process started no later than its pid file). Nothing then depends on how old the
    checkout is. The pid files' own stamps are restored on exit."""

    def __init__(self, home: Path, started: dict, root: Path):
        self.run = home / "run"
        self.started, self.root = started, root
        self.saved: dict = {}

    def __enter__(self):
        stamps = []
        for f in [self.root / ".claude-plugin" / "plugin.json"] + [self.root / "build" / s for s in _SCRIPTS.values()]:
            try:
                stamps.append(f.stat().st_mtime)
            except OSError:
                pass
        t0 = max(stamps) if stamps else time.time() - 3600
        self.started["t"] = t0
        for pf in sorted(self.run.glob("*.pid")):
            st = pf.stat()
            self.saved[pf] = (st.st_atime, st.st_mtime)
            os.utime(pf, (t0, t0))
        return self

    def __exit__(self, *exc):
        self.started.pop("t", None)
        for pf, times in self.saved.items():
            try:
                os.utime(pf, times)
            except OSError:
                pass
        return False


def _cmd(root: Path, name: str) -> str:
    script = root / "build" / _SCRIPTS[name]
    return f'"{PY}" "{script}"' if _WIN else f"{PY}\0{script}"


def gate_b_pre_j5_servers() -> None:
    print(f"\n-- B (K3, hook side): a server an older build (v{OLD_VER}) started, no owner sidecar, is judged by "
          "its command line --")
    for fn in ("pid_cmdline", "running_code_root"):
        check(f"port_registry exposes {fn}()", callable(getattr(PR, fn, None)))
    hs = _load("deepmap_hook_state")
    notify = _load("deepmap_sessionstart_notify")
    cur = _cur_version()
    with _tmpdir() as tw, _tmpdir() as tc, _tmpdir() as th, _tmpdir() as tp:
        cfg, home, plugins = Path(tc), Path(th), Path(tp)
        root = _repo(Path(tw))
        _manifest(root)
        _mcp_json(root)
        old = _plugin(plugins / "cache", OLD_VER)
        newer = _plugin(plugins / "cache", "99.0.0")
        gone = plugins / "cache" / "deep-map" / GONE_VER
        cmds: dict = {}
        alive: set = set()
        fake_cmd = lambda pid: cmds.get(int(pid))                   # noqa: E731
        fake_alive = lambda pid: int(pid) in alive                  # noqa: E731
        # ROUND-10 L1: heal (and so the note) signals a pid-file process only when it started BEFORE its pid file
        # was written — these stand-in pids "started" an hour ago (unless a check sets started["t"])
        started: dict = {}
        fake_ct = lambda pid: (started.get("t", time.time() - 3600) if int(pid) in alive   # noqa: E731
                               else None)

        def run(**w):
            with _Env(home, cfg), _Patch(PR, pid_cmdline=fake_cmd, _pid_alive=fake_alive, pid_create_time=fake_ct,
                                         proc_environ=lambda pid: None):
                return hs.mcp_code_stale(root)

        with _Env(home, cfg):
            _register(home, root)
            key = PR.project_key(root)
        # 1. the real upgrade: an older build's servers on this project's registered ports, no sidecar
        _World(home, {(SEARCH, _S): 7101, (EMBED, _E): 7102})
        cmds.update({7101: _cmd(old, SEARCH), 7102: _cmd(old, EMBED)})
        alive.update({7101, 7102})
        got = run()
        check(f"v{OLD_VER} servers (pid files only) running an older plugin root -> "
              f"{{'old': '{OLD_VER}', 'new': <current {cur}>}}",
              isinstance(got, dict) and got.get("old") == OLD_VER and got.get("new") == cur, got)
        # 2. that older root was deleted by the update: still stale, labelled by its path
        cmds.update({7101: _cmd(gone, SEARCH), 7102: _cmd(gone, EMBED)})
        got = run()
        check("...an old root that no longer exists -> stale, labelled by that root",
              isinstance(got, dict) and GONE_VER in str(got.get("old")), got)
        # 3. K4: a NEWER plugin's servers (a window still on the previous plugin must not warn/restart)
        cmds.update({7101: _cmd(newer, SEARCH), 7102: _cmd(newer, EMBED)})
        check("K4: servers running a NEWER version (99.0.0) -> None (never 'stale' toward an older plugin)",
              run() is None, run())
        # 4. the current code itself. ROUND-11 R4: with the stand-ins "started" an hour ago, L3 (code_replaced_since)
        # read THIS checkout's plugin.json / server scripts as rewritten in place after they started whenever the
        # checkout was younger than that — every CI run and fresh clone went RED. Here they start the moment this code
        # was last written and their pid files are stamped then (restored after), so the checkout's age is irrelevant.
        cmds.update({7101: _cmd(REPO, SEARCH), 7102: _cmd(REPO, EMBED)})
        with _started_with_code(home, started, REPO):
            got4 = run()
        check("servers running THIS code -> None (whatever the checkout's age)", got4 is None, got4)
        # 5. unreadable command line / another script / a dead pid
        cmds.clear()
        check("command line unreadable -> None (unknown, never guessed)", run() is None)
        cmds.update({7101: _cmd(old, EMBED), 7102: f'"{PY}" -c pass'})
        check("the search pid runs the EMBED script, the embed pid runs something else -> None", run() is None)
        cmds.update({7101: _cmd(old, SEARCH), 7102: _cmd(old, EMBED)})
        alive.clear()
        check("dead pids -> None", run() is None)
        alive.update({7101, 7102})
        # 6. the port must be registered to THIS project (a .mcp.json pair alone is no proof)
        with _Env(home, cfg):
            _register(home, root, search=18941, embed=18942)
        check("ports in .mcp.json but registered elsewhere -> None", run() is None)
        with _Env(home, cfg):
            _register(home, root)
        # 7. an owner record naming ANOTHER project for this very pid -> never this project's news
        _World(home, {(SEARCH, _S): 7101, (EMBED, _E): 7102},
               owners={(SEARCH, _S): {"pid": 7101, "project_key": "someoneelse1"},
                       (EMBED, _E): {"pid": 7102, "project_key": "someoneelse1"}})
        check("an owner record naming another project for the same pid -> None", run() is None)
        # 8. a stale owner record of an EARLIER process (another pid): the pid file's process is judged
        _World(home, {(SEARCH, _S): 7101, (EMBED, _E): 7102},
               owners={(SEARCH, _S): {"pid": 1234, "project_key": key, "code_root": REPO.as_posix()}})
        got = run()
        check("an owner record for another pid says nothing about this process -> command line decides (stale)",
              isinstance(got, dict) and got.get("old") == OLD_VER, got)
        # 9. an own pre-J5 sidecar (project fields, no code fields) -> command line decides
        _World(home, {(SEARCH, _S): 7101, (EMBED, _E): 7102},
               owners={(SEARCH, _S): {"pid": 7101, "project_key": key, "root": root.as_posix()},
                       (EMBED, _E): {"pid": 7102, "project_key": key, "root": root.as_posix()}})
        got = run()
        check("an own pre-J5 sidecar (no code fields) -> command line decides (stale)",
              isinstance(got, dict) and got.get("old") == OLD_VER, got)
        # 9b. K4 through a full round-8 sidecar: a NEWER recorded version from an existing root -> never stale
        _World(home, {(SEARCH, _S): 7101, (EMBED, _E): 7102},
               owners={(SEARCH, _S): {"pid": 7101, "project_key": key, "code_root": newer.as_posix(),
                                      "code_version": "99.0.0"},
                       (EMBED, _E): {"pid": 7102, "project_key": key, "code_root": newer.as_posix(),
                                     "code_version": "99.0.0"}})
        check("K4: a round-8 sidecar recording a NEWER version (99.0.0, root on disk) -> None", run() is None, run())
        # 10. end to end through the SessionStart notify: READY, ports UP, an older build's servers
        _World(home, {(SEARCH, _S): 7101, (EMBED, _E): 7102})
        with _Patch(PR, pid_cmdline=fake_cmd, _pid_alive=fake_alive, pid_create_time=fake_ct,
                    proc_environ=lambda pid: None):
            out = _ctx(_inproc(notify, {"cwd": str(root), "session_id": "k3a"}, home, cfg, up=(_S, _E)))
            out_off = _ctx(_inproc(notify, {"cwd": str(root), "session_id": "k3b"}, home, cfg, up=(_S, _E),
                                   env={"DEEPMAP_HEAL": "0"}))
            cmds.update({7101: _cmd(newer, SEARCH), 7102: _cmd(newer, EMBED)})
            out_new = _ctx(_inproc(notify, {"cwd": str(root), "session_id": "k3c"}, home, cfg, up=(_S, _E)))
        check(f"notify (first session after the v{OLD_VER} -> current upgrade): the reconnect-via-/mcp note",
              "DeepMap is ACTIVE" in out and f"different DeepMap version ({OLD_VER})" in out and "/mcp" in out
              and "heal restarts them" in out, out)
        check("...heal disabled: /deep-map:setup restarts them", f"different DeepMap version ({OLD_VER})" in out_off
              and "/deep-map:setup" in out_off, out_off)
        check("...K4: servers on a NEWER version -> the plain announce, no version note",
              "DeepMap is ACTIVE" in out_new and "different DeepMap version" not in out_new, out_new)

    # the REAL reader: a live child process whose command line runs an old plugin's server script (path with a space)
    with _tmpdir() as tw, _tmpdir() as tc, _tmpdir() as th, _tmpdir() as tp:
        cfg, home = Path(tc), Path(th)
        root = _repo(Path(tw))
        _manifest(root)
        _mcp_json(root)
        old = _plugin(Path(tp) / "old plugins", OLD_VER, scripts=True)
        procs = []
        try:
            for name in (SEARCH, EMBED):
                procs.append(subprocess.Popen([PY, str(old / "build" / _SCRIPTS[name])], stdin=subprocess.DEVNULL,
                                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            spid, epid = procs[0].pid, procs[1].pid
            deadline = time.time() + 20
            cmd = None
            while time.time() < deadline:
                cmd = PR.pid_cmdline(spid) if callable(getattr(PR, "pid_cmdline", None)) else None
                if cmd and _SCRIPTS[SEARCH] in cmd:
                    break
                time.sleep(0.1)
            check("pid_cmdline(<live child>) names the server script it runs", bool(cmd) and _SCRIPTS[SEARCH] in cmd,
                  repr(cmd))
            spawned: list = []
            real_run, real_popen = subprocess.run, subprocess.Popen

            def _no_run(*a, **k):
                spawned.append(a[0] if a else k.get("args"))
                return real_run(*a, **k)

            class _NoPopen(real_popen):   # type: ignore[misc, valid-type]
                def __init__(self, *a, **k):
                    spawned.append(a[0] if a else k.get("args"))
                    super().__init__(*a, **k)

            subprocess.run, subprocess.Popen = _no_run, _NoPopen
            try:
                rr = PR.running_code_root(spid, SEARCH) if callable(getattr(PR, "running_code_root", None)) else None
            finally:
                subprocess.run, subprocess.Popen = real_run, real_popen
            check("running_code_root(<live child>, 'deepmap-search') == the old plugin root (a path with a space)",
                  rr is not None and Path(rr).resolve() == old.resolve(), rr)
            check("...read in-process: no subprocess (no PowerShell / ps) is spawned on Windows or Linux "
                  "(the hook's 10 s budget)", not spawned if (_WIN or Path("/proc").is_dir()) else True, spawned)
            check("running_code_root: the wrong script name for that pid -> None",
                  callable(getattr(PR, "running_code_root", None)) and PR.running_code_root(spid, EMBED) is None)
            with _Env(home, cfg):
                _register(home, root)
            _World(home, {(SEARCH, _S): spid, (EMBED, _E): epid})
            with _Env(home, cfg):
                got = hs.mcp_code_stale(root)
            check(f"mcp_code_stale with REAL older-build servers (no stubs) -> {{'old': '{OLD_VER}'}}",
                  isinstance(got, dict) and got.get("old") == OLD_VER, got)
        finally:
            for p in procs:
                try:
                    p.kill()
                    p.wait(timeout=20)
                except Exception:
                    pass
        check("(the stub server processes this gate started are gone)", all(p.poll() is not None for p in procs))


# =========================================================================== C: K3 nudge follow-up
def _heal_status(home: Path, root: Path, *, mtime=None, **fields) -> Path:
    p = home / "run" / f"heal-{PR.project_key(root)}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    rec = {"ts": "2026-09-27T00:00:00Z", "ts_epoch": time.time(), "neo4j_ok": True, "mcp_ok": True, "reason": None,
           "mcp_started": [], "mcp_started_epoch": None, "refused": False, "refusal_kind": None, "mcp_stopped": [],
           "mcp_unverified": []}
    rec.update(fields)
    p.write_text(json.dumps(rec), encoding="utf-8")
    m = time.time() + 1 if mtime is None else mtime
    os.utime(p, (m, m))
    return p


def gate_c_nudge_restart_followup() -> None:
    print("\n-- C (K3): servers UP at session start, then this session's heal restarted them -> reconnect, once --")
    notify = _load("deepmap_sessionstart_notify")
    nudge = _load("deepmap_presearch_nudge")
    grep = {"tool_name": "Grep", "tool_input": {"pattern": "x"}}

    def world():
        tw, tc, th = _tmpdir(), _tmpdir(), _tmpdir()
        root = _repo(Path(tw.name))
        home, cfg = Path(th.name), Path(tc.name)
        _manifest(root)
        _mcp_json(root)
        with _Env(home, cfg):
            _register(home, root)
        return (tw, tc, th), root, home, cfg

    keep, root, home, cfg = world()
    try:
        ctx = _ctx(_inproc(notify, {"cwd": str(root), "session_id": "c1"}, home, cfg, up=(_S, _E)))
        check("(fixture: READY announce, ports up, no version note)", "DeepMap is ACTIVE" in ctx
              and "different DeepMap version" not in ctx and "/mcp" not in ctx, ctx)
        first = _ctx(_inproc(nudge, dict(grep, cwd=str(root), session_id="c1"), home, cfg, up=(_S, _E)))
        check("(fixture: the first Grep gets the normal nudge)", "deepmap_search" in first, first)
        time.sleep(0.05)
        _heal_status(home, root, mcp_started=[SEARCH, EMBED], mcp_started_epoch=time.time())
        ctx = _ctx(_inproc(nudge, dict(grep, cwd=str(root), session_id="c1"), home, cfg, up=(_S, _E)))
        check("this session's heal then RESTARTED the servers the session had connected to -> reconnect via /mcp",
              "/mcp" in ctx and "restarted" in ctx.lower() and "deepmap-search" in ctx, ctx)
        again = _ctx(_inproc(nudge, dict(grep, cwd=str(root), session_id="c1"), home, cfg, up=(_S, _E)))
        check("...once", again == "", again)
    finally:
        for k in keep:
            k.cleanup()

    keep, root, home, cfg = world()
    try:
        _inproc(notify, {"cwd": str(root), "session_id": "c2"}, home, cfg, up=(_S, _E))
        _inproc(nudge, dict(grep, cwd=str(root), session_id="c2"), home, cfg, up=(_S, _E))
        _heal_status(home, root, mcp_started=[SEARCH, EMBED], mcp_started_epoch=time.time() - 3600)
        ctx = _ctx(_inproc(nudge, dict(grep, cwd=str(root), session_id="c2"), home, cfg, up=(_S, _E)))
        check("control: a heal that only CARRIED FORWARD an earlier start (epoch before the session) -> silent",
              ctx == "", ctx)
    finally:
        for k in keep:
            k.cleanup()

    keep, root, home, cfg = world()
    try:
        run = home / "run"
        run.mkdir(parents=True, exist_ok=True)
        with _Env(home, cfg):
            key = PR.project_key(root)
        pid = os.getpid()
        for name, port in ((SEARCH, _S), (EMBED, _E)):
            (run / f"{name}-{port}.pid").write_text(str(pid), encoding="utf-8")
            (run / f"{name}-{port}.owner").write_text(json.dumps({
                "pid": pid, "project_key": key, "code_root": (home / "gone-plugin").as_posix(),
                "code_version": "0.0.1"}), encoding="utf-8")
        # ROUND-10 L1: heal restarts a recorded server only when the pid IS that server (its command line runs the
        # server script) — this gate's own pid stands in for it
        srv = _cmd(home / "gone-plugin", SEARCH)
        real_cmd = PR.pid_cmdline
        with _Patch(PR, pid_cmdline=lambda p: srv if int(p) == pid else real_cmd(p)):
            ctx = _ctx(_inproc(notify, {"cwd": str(root), "session_id": "c3"}, home, cfg, up=(_S, _E)))
        check("(fixture: notify already gave the post-update reconnect note)", "different DeepMap version" in ctx, ctx)
        _inproc(nudge, dict(grep, cwd=str(root), session_id="c3"), home, cfg, up=(_S, _E))
        _heal_status(home, root, mcp_started=[SEARCH, EMBED], mcp_started_epoch=time.time())
        ctx = _ctx(_inproc(nudge, dict(grep, cwd=str(root), session_id="c3"), home, cfg, up=(_S, _E)))
        check("control: the session was already told to reconnect -> the nudge does not repeat it", ctx == "", ctx)
    finally:
        for k in keep:
            k.cleanup()

    keep, root, home, cfg = world()
    try:
        _inproc(notify, {"cwd": str(root), "session_id": "c4"}, home, cfg, up=(), env={"DEEPMAP_NOTIFY_HEAL_WAIT": "0"})
        _inproc(nudge, dict(grep, cwd=str(root), session_id="c4"), home, cfg, up=())
        _heal_status(home, root, mcp_started=[SEARCH, EMBED], mcp_started_epoch=time.time())
        ctx = _ctx(_inproc(nudge, dict(grep, cwd=str(root), session_id="c4"), home, cfg, up=(_S, _E)))
        check("control: servers DOWN at start -> the existing 'up now' follow-up is unchanged",
              "up now" in ctx.lower() and "/mcp" in ctx, ctx)
    finally:
        for k in keep:
            k.cleanup()


# =========================================================================== D: K6 install.ps1 hook form
def _hook_file(repo: Path) -> Path:
    rel = _git(repo, "rev-parse", "--git-path", "hooks/post-commit")
    p = Path(rel) if Path(rel).is_absolute() else repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _write_hook(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")
    if not _WIN:
        os.chmod(path, 0o755)


def _ps1_line(script_dir: str, repo: str) -> str:
    """The exact line install.ps1 (v0.27.x) writes: `$ScriptDir/build/deepmap_cli.py` and `$Repo` interpolated
    verbatim (Windows backslash paths) inside double quotes."""
    return (f'mkdir -p "$HOME/.deepmap" 2>/dev/null; ( nohup python "{script_dir}/build/deepmap_cli.py" sync "{repo}" '
            '>"$HOME/.deepmap/sync.log" 2>&1 & ) || true   # deepmap')


def gate_d_ps1_hook() -> None:
    print("\n-- D (K6): the sync flag recognises the hook install.ps1 writes (backslash Windows paths) --")
    sf = _load("deepmap_postedit_syncflag")
    # ROUND-11 L3: _ps1_line is install.ps1's literal line, whose interpreter is a bare `python` — on a python3-only
    # host that word must resolve for the claim to be judged at all (_PythonOnPath: a no-op where `python` exists)
    with _tmpdir() as tw, _tmpdir() as tc, _tmpdir() as th, _PythonOnPath():
        home, cfg = Path(th), Path(tc)
        with _Env(home, cfg):
            check("(fixture) the bare `python` install.ps1 writes resolves on PATH", shutil.which("python") is not None)
            if _WIN:
                root = _repo(Path(tw), "PsProj")
                line = _ps1_line(str(REAL_REPO), str(root))
                check("(fixture: the PS1 line carries single backslashes, e.g. 'sync \"C:\\...\\PsProj\"')",
                      "\\" in line.split(" sync ", 1)[1].split('"')[1], line)
            else:
                root = _repo(Path(tw), "we\\ird proj")   # a directory name with a literal backslash (legal on POSIX)
                line = _ps1_line(str(REAL_REPO), str(root))
            top = _git(root, "rev-parse", "--show-toplevel")
            check("_line_syncs: the install.ps1 line syncs THIS root (sh keeps a backslash before an ordinary char)",
                  sf._line_syncs(line, root, top) is True, line)
            dq = str(root).replace("\\", "\\\\")
            check("control: the same path with sh-escaped backslashes (\\\\) still matches",
                  sf._line_syncs(line.replace(f'sync "{root}"', f'sync "{dq}"'), root, top) is True)
            check("control: another root is still refused",
                  sf._line_syncs(_ps1_line(str(REAL_REPO), str(root) + "X"), root, top) is False)
            gone_cli = str(Path(tw) / "deleted plugin")
            check("control: a deleted plugin's CLI in the PS1 form syncs nothing",
                  sf._line_syncs(_ps1_line(gone_cli, str(root)), root, top) is False)
            _manifest(root)
            hook = _hook_file(root)
            _write_hook(hook, "#!/bin/sh\n# >>> deepmap (managed; existing hook preserved on chain) >>>\n"
                              + line + "\n# <<< deepmap <<<\n")
            check("_git_hook_installed: the hook install.ps1 created -> the claim is made",
                  sf._git_hook_installed(root) is True, hook.read_text(encoding="utf-8"))
            _write_hook(hook, "#!/bin/sh\necho mine\n\n# >>> deepmap (managed; existing hook preserved on chain) >>>\n"
                              + line + "\n# <<< deepmap <<<\n")
            check("_git_hook_installed: install.ps1 chained onto an existing sh hook -> the claim is made",
                  sf._git_hook_installed(root) is True)
            saved = (sys.stdin, sys.stdout)
            try:
                sys.stdin = io.StringIO(json.dumps({"cwd": str(root), "session_id": "d1", "tool_name": "Edit",
                                                    "tool_input": {}}))
                sys.stdout = io.StringIO()
                sf.main()
                out = sys.stdout.getvalue()
            finally:
                sys.stdin, sys.stdout = saved
            check("end to end: the post-edit reminder says a commit re-syncs it", "git hook" in _ctx(out).lower(),
                  _ctx(out))


# =========================================================================== E: K7 hooks_r7 B under load
_SITECUSTOMIZE = r'''
import os, subprocess, sys, time
INJECTED = [0]
_DELAY = float(os.environ.get("DMR9_SLOW_NOTIFY_S") or 0)
_orig_run = subprocess.run


def _slow_run(*a, **k):
    if _DELAY > 0:
        f = sys._getframe(1)
        while f is not None:
            if f.f_code.co_filename.replace("\\", "/").endswith("/hooks/deepmap_sessionstart_notify.py"):
                INJECTED[0] += 1
                time.sleep(_DELAY)
                break
            f = f.f_back
    return _orig_run(*a, **k)


subprocess.run = _slow_run
'''

_RUNNER = r'''
import importlib.util, sys
for s in (sys.stdout, sys.stderr):
    try:
        s.reconfigure(encoding="utf-8")
    except Exception:
        pass
spec = importlib.util.spec_from_file_location("dmr9_r7", sys.argv[1])
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)
try:
    g.gate_b_same_session_window()
except Exception as exc:
    g.check("gate_b ran without raising", False, repr(exc))
import sitecustomize
print("DMR9_INJECTED", sitecustomize.INJECTED[0])
print("DMR9_RESULT", g._PASS, g._FAIL)
sys.exit(1 if g._FAIL else 0)
'''


def gate_e_r7_under_load() -> None:
    print("\n-- E (K7): hooks_r7_gates section B stays GREEN when every git call the notify makes is slow --")
    r7 = TESTS / "hooks_r7_gates.py"
    if not r7.is_file() or not shutil.which("git"):
        check("(hooks_r7_gates.py + git available)", True, "skipped")
        return
    with _tmpdir("dmr9k7") as td:
        d = Path(td)
        (d / "sitecustomize.py").write_text(_SITECUSTOMIZE, encoding="utf-8")
        (d / "runner.py").write_text(_RUNNER, encoding="utf-8")
        env = dict(os.environ)
        env["PYTHONPATH"] = str(d) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        env["DMR9_SLOW_NOTIFY_S"] = "0.9"
        env["PYTHONUTF8"] = "1"
        p = subprocess.run([PY, str(d / "runner.py"), str(r7)], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", env=env, cwd=str(d), timeout=600)
        out = (p.stdout or "") + (p.stderr or "")
        inj = re.search(r"DMR9_INJECTED (\d+)", out)
        res = re.search(r"DMR9_RESULT (\d+) (\d+)", out)
        n = int(inj.group(1)) if inj else 0
        check(f"(the injected slow-down really fired inside the notify: {n} slowed git call(s))", n >= 5, out[-600:])
        fails = [ln.strip() for ln in out.splitlines() if "[FAIL]" in ln]
        check("hooks_r7_gates B under the load: GREEN (no wall-clock race)",
              p.returncode == 0 and res is not None and int(res.group(2)) == 0 and not fails,
              f"rc={p.returncode} {res.group(0) if res else ''} {fails[:6]}")


# =========================================================================== F: docs
def gate_f_docs() -> None:
    print("\n-- F: W1 docs say what the hooks now do after an update --")
    pu = " ".join((REPO / "commands" / "plugin-update.md").read_text(encoding="utf-8").split())
    sk = " ".join((REPO / "skills" / "deep-map" / "SKILL.md").read_text(encoding="utf-8").split())
    check("plugin-update.md: the next session start restarts the project's servers on the new version (the heal)",
          "next session start" in pu and "heal" in pu.lower() and "DEEPMAP_HEAL=0" in pu, pu[-900:])
    check("plugin-update.md: a window still on the previous version never moves them back (K4)",
          "never moves them back" in pu, pu[-900:])
    check("plugin-update.md: /deep-map:setup still restarts them at once, and /mcp reconnects the client",
          "/deep-map:setup" in pu and "/mcp" in pu and "reconnect" in pu.lower())
    check("SKILL.md: the post-update note may arrive at the first search after the restart (K3 follow-up)",
          "first search after the restart" in sk and "/mcp" in sk)


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 72)
    print("DeepMap — round-9 W1: ordered restarts (K4), pre-J5 servers (K3), install.ps1 hook claim (K6), r7 flake (K7)")
    print(f"(hooks under test: {HOOKS}; build: {BUILD})")
    print("=" * 72)
    if not shutil.which("git"):
        print("RESULT: GREEN — 0/0 gates passed. (git not on PATH: skipped)")
        return 0
    for g in (gate_a_ordered, gate_b_pre_j5_servers, gate_c_nudge_restart_followup, gate_d_ps1_hook,
              gate_e_r7_under_load, gate_f_docs):
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
