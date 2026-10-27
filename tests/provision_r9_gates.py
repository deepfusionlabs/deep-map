#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j, no-kill gates for the ROUND-9 provisioner fixes (build/provision.py, owner W2):

  K1 `git-hook` on git < 2.31 (Debian 11 ships 2.30, Ubuntu 20.04 2.25): old git does not know
     `rev-parse --path-format=absolute` — it ECHOES the unknown option as its first output line and exits 0, so every
     field shifted by one (top='--path-format=absolute', hook=<top>/.git). --status then said 'not installed' for a
     destructive legacy line, --install refused with a false 'outside this repository', --uninstall removed nothing.
     Simulated faithfully: every provision git probe runs through a wrapper that behaves like old git (echo + rc 0,
     relative answers) around the real git — status / install / uninstall, a REAL `git commit` running the block, and
     the setup tip all work; a rev-parse answer that is not an existing absolute top is rejected (no hook written).
  K2 setup's git-hook tip names THIS project's sync root (`git-hook --install "<sync root>"`), so following it from the
     repo top can never re-open J1a; a bare `--install` at a work-tree directory with NO graph of its own while another
     directory of the same work tree was set up (one work tree = one project key = one Neo4j instance) is REFUSED (rc
     2, naming the command for the set-up project) instead of writing the top's whole tree into that graph.
  K3 the REAL upgrade (v0.27.4 -> this): v0.27.4 wrote no owner sidecar. heal / mcp-start restart such a server when it
     is proven this project's by evidence — the registry gives its port to THIS project, its pid file names a live pid
     that is older than the pid file, its command line runs this server's script from ANOTHER code root, and nothing
     says it is another port's / project's — and that code is older (K4). Each missing piece of evidence -> left alone.
     The restart lands in heal's status (mcp_started + mcp_code_restarted: the reconnect note's evidence).
  K4 never DOWNGRADE: restart onto the code running now only when it is NEWER than the running server's code; equal
     version from another directory -> only when the running code's directory is gone (ROUND-10 L4 dropped the
     installed-newest tie-break: two installed copies of one version ping-ponged); the same directory with another
     version (an in-place update) -> restart. An older window's heal never restarts a newer window's servers.
  ROUND-10 reconciliation (tests/provision_r10_gates.py gates the new rules): an owner file naming another pid is no
  owner record (L1); a K3 restart needs a working Neo4j config (L2 — these worlds model one); a server "running THIS
  code" started after that code was written (L3 — one started before an in-place update is restarted).
  K5 `git-hook --install` REFUSES an in-repo core.hooksPath directory the team commits (git tracks files in it), even
     when post-commit does not exist yet, and never CREATES a new hook that git would show as an untracked file.

Reuses the round-4/6/8 hermetic harnesses (same owner): DEEPMAP_HOME -> a throwaway dir; port probes, pid liveness /
command line / creation time / environment, spawns and kills are stubbed. The git-hook gates use REAL git in throwaway
repos (isolated global/system config) and a stub interpreter. CLAUDE_CONFIG_DIR -> a throwaway dir (the real
installed_plugins.json is never read). No Neo4j, no network. Exit 0 = GREEN.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
from provision_round4_gates import (P, PR, _Patch, _Sandbox, _mk_project, _captured, _quiet,  # noqa: E402
                                    _clear_port_env)
from provision_round6_gates import _World, _mcp_env, _seed_owned, _seed_pidfile_only  # noqa: E402
from provision_r8_gates import (_GitBox, _CodeWorld, _rewrite_owner, _script_cmd, _same_dir,  # noqa: E402
                                _targets, _read_json, _rmtree)

_PASS = 0
_FAIL = 0
SEARCH, EMBED = "deepmap-search", "deepmap-embed"


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
        tb = traceback.format_exc().strip().splitlines()
        check(f"{fn.__name__} ran without raising", False, f"{type(e).__name__}: {e} " + " | ".join(tb[-4:]))


def _api(name):
    fn = getattr(P, name, None)
    check(f"provision.{name} exists", callable(fn))
    return fn


class _ClaudeDir:
    """CLAUDE_CONFIG_DIR -> a throwaway dir (optionally with an installed_plugins.json naming `installed`
    [(installPath, version)]): the gates never read the developer's real plugin records."""

    def __init__(self, installed=None):
        self.installed = installed

    def __enter__(self):
        self.base = Path(tempfile.mkdtemp(prefix="dmr9c")).resolve()
        self.saved = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.base)
        if self.installed is not None:
            self.write(self.installed)
        return self

    def write(self, installed) -> None:
        (self.base / "plugins").mkdir(parents=True, exist_ok=True)
        recs = [{"scope": "user", "installPath": str(p), "version": v} for p, v in installed]
        data = {"version": 2, "plugins": {"deep-map@deepfusionlabs": recs,
                                          "other-plugin@x": [{"installPath": "/elsewhere", "version": "99.0.0"}]}}
        (self.base / "plugins" / "installed_plugins.json").write_text(json.dumps(data), encoding="utf-8")

    def __exit__(self, *a):
        if self.saved is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = self.saved
        _rmtree(self.base)


def _fake_plugin(parent: Path, version: str, name: str | None = None) -> Path:
    """A plugin version dir as Claude Code's cache lays it out: <parent>/<version>/{.claude-plugin/plugin.json,
    build/<server scripts>}."""
    d = parent / (name or version)
    (d / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (d / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "deep-map", "version": version}),
                                                     encoding="utf-8")
    (d / "build").mkdir(exist_ok=True)
    for cfg in P.MCP_SERVERS.values():
        (d / "build" / cfg["script"]).write_text("# a server\n", encoding="utf-8")
    return d.resolve()


def _older_than(ver) -> str:
    """A release version strictly OLDER than `ver`: 0.5.0 -> 0.4.9, 1.2.3 -> 1.2.2, 2.0.0 -> 1.99.9. The K3 stand-in
    for "an older build" (pid file only, no owner sidecar) is derived from the REAL plugin version under test, so it
    stays older than the code running now whatever the release line is (the version line restarted at 0.5.0 for the
    public release, below the synthetic 0.27.4 these gates used). "0.0.1" when `ver` can't be read."""
    try:
        nums = [int(x) for x in str(ver).strip().lstrip("vV").split("+")[0].split("-")[0].split(".")]
    except Exception:   # noqa: BLE001
        return "0.0.1"
    nums = (nums + [0, 0, 0])[:3]
    if nums[2] > 0:
        return f"{nums[0]}.{nums[1]}.{nums[2] - 1}"
    if nums[1] > 0:
        return f"{nums[0]}.{nums[1] - 1}.9"
    if nums[0] > 0:
        return f"{nums[0] - 1}.99.9"
    return "0.0.0-rc.1"


# what an older (pre-owner-record) build's plugin.json says: always below the code under test (provision_r10 too)
OLD_VER = _older_than(P._code_identity().get(PR.CODE_VERSION_FIELD))


def _ident(root: Path, version: str) -> dict:
    return {PR.CODE_ROOT_FIELD: Path(root).as_posix(), PR.CODE_VERSION_FIELD: version}


# =========================================================================== K1 — git < 2.31

_OLD_GIT = r'''
import subprocess, sys
git, rest = sys.argv[1], sys.argv[2:]
i, pre = 0, []
while i + 1 < len(rest) and rest[i] in ("-c", "-C"):
    pre += rest[i:i + 2]
    i += 2
cmd = rest[i:]
echo = b""
if cmd[:1] == ["rev-parse"]:
    # git < 2.31 does not know --path-format: rev-parse passes the unknown option through to its output (first) and
    # exits 0; the answers that follow are the flagless ones (--git-common-dir / --git-path relative to the cwd)
    echo = b"".join(a.encode() + b"\n" for a in cmd[1:] if a.startswith("--path-format"))
    cmd = [a for a in cmd if not a.startswith("--path-format")]
r = subprocess.run([git, *pre, *cmd], capture_output=True)
sys.stdout.buffer.write(echo + r.stdout)
sys.stdout.flush()
sys.stderr.buffer.write(r.stderr)
sys.exit(r.returncode)
'''


class _OldGit:
    """provision's git probes (_git_argv — _git_text, _git_tracked and every git-hook probe) run through a wrapper that
    behaves like git < 2.31 around the REAL git."""

    def __init__(self, base: Path):
        self.path = Path(base) / "old_git.py"
        self.path.write_text(_OLD_GIT, encoding="utf-8")

    def argv(self, git, cwd, *args):
        return [sys.executable, str(self.path), git, "-C", str(cwd), *args]


def gate_k1_old_git() -> None:
    print("\n-- K1: git-hook on git < 2.31 (rev-parse echoes the unknown --path-format option, rc 0) --")
    if not shutil.which("git"):
        check("git is available for the git-hook gates", False)
        return
    with _GitBox() as gb:
        old = _OldGit(gb.base)
        a = gb.repo("a")
        (a / "sub").mkdir()
        with _Patch(P, _git_argv=old.argv):
            raw = P._git_text(str(a), "rev-parse", "--path-format=absolute", "--show-toplevel")
        check("control: the simulated old git echoes the flag as its first line (rc 0)",
              (raw or "").splitlines()[:1] == ["--path-format=absolute"], repr(raw))
        hp = gb.hook(a)
        with _Patch(P, _git_argv=old.argv):
            env = P._hook_env(str(a))
            env_sub = P._hook_env(str(a / "sub"))
        for label, e in (("from the top", env), ("from a sub-directory", env_sub)):
            check(f"_hook_env {label}: top is the real toplevel (not the echoed flag)",
                  e is not None and _same_dir(e["top"], a), str(e and e.get("top")))
            check(f"_hook_env {label}: the hook is <top>/.git/hooks/post-commit, common dir <top>/.git",
                  e is not None and _same_dir(e["hook"], hp) and _same_dir(e["common_dir"], a / ".git"),
                  str(e and (e.get("hook"), e.get("common_dir"))))
        # a v0.27.2 installer's destructive line: status must say STALE, install must upgrade it, uninstall remove it
        hp.parent.mkdir(parents=True, exist_ok=True)
        old_cli = "/home/u/.claude/plugins/cache/deep-map/0.27.2/build/deepmap_cli.py"
        legacy = (f'mkdir -p "$HOME/.deepmap" 2>/dev/null; ( nohup python3 "{old_cli}" sync "{a.as_posix()}" '
                  '--stale-cleanup >"$HOME/.deepmap/sync.log" 2>&1 & ) || true   # deepmap')
        hp.write_text("#!/bin/sh\necho user-hook\n" + legacy + "\n", encoding="utf-8", newline="\n")
        os.chmod(hp, 0o755)
        with _Patch(P, _git_argv=old.argv):
            rep = P._git_hook_report(str(a))
            rc_s, out_s = _captured(P.git_hook, str(a), "status")
        check("old git: --status of a legacy --stale-cleanup line says STALE (was: 'not installed')",
              rep.get("state") == "stale" and rc_s == 0 and "stale" in out_s.lower(), f"{rep.get('state')} {out_s[-300:]}")
        with _Patch(P, _git_argv=old.argv):
            rc, out = _captured(P.git_hook, str(a), "install")
        text = hp.read_text(encoding="utf-8")
        check("old git: --install -> rc 0 (was: rc 2 'outside this repository')", rc == 0, f"rc={rc} {out[-300:]}")
        check("... the block's guard names the real toplevel, never the echoed flag",
              "--path-format" not in text and re.search(r"-ef '([^']+)'", text) is not None
              and _same_dir(re.search(r"-ef '([^']+)'", text).group(1), a), text)
        check("... the destructive legacy line is gone, one current block, the user's line kept",
              "--stale-cleanup" not in text and text.count("# >>> deepmap") == 1 and "echo user-hook" in text, text)
        gb.reset_log()
        check("... a REAL `git commit` runs it: the stub records a sync of the project",
              gb.commit(a) == 0 and [t for t in gb.synced() if _same_dir(t, a)], str(gb.synced(0.1)))
        with _Patch(P, _git_argv=old.argv):
            st = P._git_hook_report(str(a)).get("state")
            rc_u = _quiet(P.git_hook, str(a), "uninstall")
            after = P._git_hook_report(str(a)).get("state")
        check("old git: status after install is 'current'", st == "current", str(st))
        check("old git: --uninstall removes this work tree's block (was: found nothing)",
              rc_u == 0 and after == "absent" and "deepmap" not in (hp.read_text(encoding="utf-8")
                                                                     if hp.exists() else ""), f"rc={rc_u} {after}")
        with _Patch(P, _git_argv=old.argv):
            tip = _captured(P._print_git_hook_hint, str(a))[1]
        check("old git: setup's hint offers the opt-in tip again (was: silent)", "git-hook --install" in tip, tip)
    # a rev-parse answer that is not an existing absolute toplevel is rejected — nothing is ever written with it
    with _GitBox() as gb:
        a = gb.repo("a")
        for label, answer in (("a relative top", "relative/top\n.git\n.git\n.git/hooks/post-commit"),
                              ("the echoed flag as top", "--path-format=absolute\n" + str(a) + "\n.git\n.git"),
                              ("a hook path that is a directory", f"{a}\n{a / '.git'}\n{a / '.git'}\n{a / '.git'}")):
            with _Patch(P, _git_text=lambda t, *args, _ans=answer: (_ans if args[:1] == ("rev-parse",) else None)):
                e = P._hook_env(str(a))
            check(f"_hook_env rejects {label} (None -> 'not a git work tree', no hook written)", e is None, str(e))


# =========================================================================== K2 — setup's tip names the sync root

def _tip_dir(out: str):
    m = re.search(r'git-hook --install "([^"]+)"', out or "")
    return m.group(1) if m else None


def gate_k2_tip_and_subproject_refusal() -> None:
    print("\n-- K2: setup's git-hook tip names THIS project's sync root; a bare --install at a graph-less top is refused --")
    if not shutil.which("git"):
        return
    with _GitBox() as gb:
        top = gb.repo("mono", manifest_at="backend")
        backend = top / "backend"
        hp = gb.hook(top)
        out = _captured(P._print_git_hook_hint, str(backend))[1]
        d = _tip_dir(out)
        check("setup's tip carries the directory: git-hook --install \"<backend>\"", d is not None and _same_dir(d, backend),
              out.strip())
        cwd = os.getcwd()
        try:
            os.chdir(str(top))   # following the tip from a terminal at the repo top
            rc = _quiet(P.main, ["git-hook", "--install", d or "."])
        finally:
            os.chdir(cwd)
        tg = _targets(hp.read_text(encoding="utf-8") if hp.exists() else "")
        check("following the tip from the repo top installs a block that syncs backend (the sync root), not the top",
              rc == 0 and len(tg) == 1 and _same_dir(tg[0], backend), f"rc={rc} {tg}")
        _quiet(P.git_hook, str(backend), "uninstall")
        # the bare command at the top: the top has no graph, backend (same work tree = same key) has one -> refused
        cwd = os.getcwd()
        try:
            os.chdir(str(top))
            rc, out = _captured(P.main, ["git-hook", "--install"])
        finally:
            os.chdir(cwd)
        text = hp.read_text(encoding="utf-8") if hp.exists() else ""
        check("a bare `git-hook --install` at the graph-less top -> refused rc 2, no DeepMap block written",
              rc == 2 and "deepmap" not in text, f"rc={rc} {text!r} {out[-300:]}")
        check("... the refusal names backend's exact command", _tip_dir(out) is not None and _same_dir(_tip_dir(out), backend),
              out.strip()[-500:])
        check("... and never the misleading 'no DeepMap graph has been built … no instance' NOTE",
              "no instance to write to" not in out, out.strip()[-300:])
        rc, out = _captured(P.git_hook, str(top), "status")
        check("--status at the top: rc 0, and it says --install is not available there, naming backend",
              rc == 0 and _tip_dir(out) is not None and _same_dir(_tip_dir(out), backend), out.strip()[-500:])
        out = _captured(P._print_git_hook_hint, str(top))[1]
        check("no 'run --install' tip for the top (it would be refused)", _tip_dir(out) is None or
              _same_dir(_tip_dir(out), backend), out)
        # a stale block of backend: the 'out of date' tip also names the directory
        cli = "/home/u/.claude/plugins/cache/deep-map/0.27.2/build/deepmap_cli.py"
        hp.parent.mkdir(parents=True, exist_ok=True)
        hp.write_text("#!/bin/sh\n" f'( nohup python3 "{cli}" sync "{backend.as_posix()}" --stale-cleanup & ) || true'
                      "   # deepmap\n", encoding="utf-8", newline="\n")
        os.chmod(hp, 0o755)
        out = _captured(P._print_git_hook_hint, str(backend))[1]
        check("a stale block: the 'out of date' tip names backend too", "out of date" in out and _tip_dir(out) is not None
              and _same_dir(_tip_dir(out), backend), out.strip())
        rc, out = _captured(P.git_hook, str(backend), "status")
        check("--status's 'Run … --install' line names the directory", _tip_dir(out) is not None
              and _same_dir(_tip_dir(out), backend), out.strip()[-400:])
    # controls
    with _GitBox() as gb:
        a = gb.repo("a")   # the top has its own graph
        (a / "lib").mkdir()
        gb.manifest(a / "lib")   # (a nested manifest under a top that has one: the outermost wins — sync root = top)
        rc = _quiet(P.git_hook, str(a), "install")
        tg = _targets(gb.hook(a).read_text(encoding="utf-8") if gb.hook(a).exists() else "")
        check("control: a top WITH its own graph installs as before (rc 0, syncs the top)",
              rc == 0 and len(tg) == 1 and _same_dir(tg[0], a), f"rc={rc} {tg}")
    with _GitBox() as gb:
        a = gb.repo("a", manifest_at=None)   # nothing set up anywhere yet
        rc, out = _captured(P.git_hook, str(a), "install")
        check("control: a work tree with no graph anywhere installs (rc 0) and says to run /deep-map:setup there",
              rc == 0 and gb.hook(a).exists() and "/deep-map:setup" in out, f"rc={rc} {out[-300:]}")
    with _GitBox() as gb:
        m = gb.repo("M")   # a linked worktree's graph belongs to ANOTHER work tree: never a reason to refuse M
        wt = gb.work / "M" / "wt"
        gb.git(m, "worktree", "add", "-q", str(wt))
        gb.manifest(wt)
        (m / ".deepmap" / "graph_build.json").unlink()
        rc = _quiet(P.git_hook, str(m), "install")
        check("control: a manifest inside a NESTED work tree (a linked worktree dir) is not this work tree's -> rc 0",
              rc == 0, f"rc={rc}")
        gb.git(m, "worktree", "remove", "--force", str(wt), check=False)


# =========================================================================== K3 — the v0.27.4 upgrade (no sidecar)

def _preowner_world(a, s, pid, cmd_root, **kw):
    """A server a v0.27.4 build started for `a` on its registered port `s`: a pid file only (no .owner), a live pid
    whose command line runs `cmd_root`/build/<script> (None = unreadable)."""
    _seed_pidfile_only(SEARCH, s, pid)
    w = _CodeWorld({pid: (SEARCH, s)}, **kw)
    w.cmd[pid] = None if cmd_root is None else _script_cmd(Path(cmd_root).as_posix(), SEARCH)
    return w


def gate_k3_preowner_upgrade_restart() -> None:
    print("\n-- K3: heal / mcp-start restart an older build's server (no owner sidecar) proven ours by evidence --")
    with _Sandbox(bolt="up") as (home, work), _ClaudeDir():   # ROUND-10 L2: the current config connects
        a = _mk_project(work, "a", marker=True)
        s, e = PR.allocate_ports(a)
        _mcp_env((s, e))
        v274 = _fake_plugin(work / "plugins" / "deep-map", OLD_VER)
        w = _preowner_world(a, s, 6801, v274)
        del P._MCP_STARTED[:]
        with _Patch(P, **w.stubs()):
            ok, out = _captured(P.start_mcp_server, SEARCH, password="deepmap", target=a)
        check("an older build's server (pid file, NO sidecar) on this project's registered port, older code -> restarted",
              ok is True and (6801, True) in w.killed and (SEARCH, s) in w.spawned, f"ok={ok} {w.killed} {w.spawned} "
              f"{out.strip()[-400:]}")
        check("... the message names the old code (the older build's version) and the restart lands in _MCP_STARTED",
              OLD_VER in out and SEARCH in P._MCP_STARTED, out.strip()[-400:])
        rec = _read_json(P._mcp_ownerfile(SEARCH, s))
        check("... the respawned server now has an owner sidecar naming this project + the current code",
              rec.get("project_key") == PR.project_key(a) and rec.get(PR.CODE_ROOT_FIELD) == P._code_identity().get(
                  PR.CODE_ROOT_FIELD), str(rec))
    with _Sandbox(bolt="up") as (home, work), _ClaudeDir():
        a = _mk_project(work, "a", marker=True)
        s, e = PR.allocate_ports(a)
        _mcp_env((s, e))
        w = _preowner_world(a, s, 6802, work / "plugins" / "deep-map" / "0.27.3")   # that version dir was removed
        with _Patch(P, **w.stubs()):
            ok = _quiet(P.start_mcp_server, SEARCH, password="deepmap", target=a)
        check("... its code dir no longer exists (a removed plugin version) -> restarted", ok is True and w.spawned,
              f"{w.killed} {w.spawned}")
    cases = (
        ("its command line runs THIS code (started after it was written — ROUND-10 L3) -> left alone", "self",
         {"created": time.time()}, {}),
        ("its command line is unreadable -> unknown, left alone", "unreadable", {}, {}),
        ("its command line runs another program -> left alone", "other-program", {}, {}),
        ("it started AFTER its pid file was written (not provably the recorded process) -> left alone", "old",
         {"created": time.time() + 10_000}, {}),
        ("its environment names ANOTHER project -> left alone", "old", {}, {"DEEPMAP_PROJECT_KEY": "f" * 12}),
        ("its environment serves ANOTHER port -> left alone", "old", {}, {"DEEPMAP_SEARCH_PORT": "1"}),
        ("it runs NEWER code (0.99.0) -> never downgraded (K4)", "newer", {}, {}),
    )
    for label, which, world_kw, environ in cases:
        with _Sandbox(bolt="up") as (home, work), _ClaudeDir():
            a = _mk_project(work, "a", marker=True)
            s, e = PR.allocate_ports(a)
            _mcp_env((s, e))
            root = {"self": P.HERE.parent, "unreadable": None, "other-program": None,
                    "old": _fake_plugin(work / "plugins" / "deep-map", OLD_VER),
                    "newer": _fake_plugin(work / "plugins" / "deep-map", "0.99.0")}[which]
            w = _preowner_world(a, s, 6803, root, **world_kw)
            if which == "other-program":
                w.cmd[6803] = "/usr/bin/python3 /opt/something_else.py"
            if environ:
                w.environ[6803] = environ
            with _Patch(P, **w.stubs()):
                ok, out = _captured(P.start_mcp_server, SEARCH, password="deepmap", target=a)
            check(f"K3: {label}", ok is True and not w.killed and not w.spawned,
                  f"ok={ok} killed={w.killed} spawned={w.spawned} {out.strip()[-200:]}")
    # the port is NOT registered to this project (a pair from a committed / copied .mcp.json)
    with _Sandbox(bolt="up") as (home, work), _ClaudeDir():
        a = _mk_project(work, "a", marker=True)
        s, e = 8991, 8992
        _mcp_env((s, e))
        w = _preowner_world(a, s, 6804, _fake_plugin(work / "plugins" / "deep-map", OLD_VER))
        with _Patch(P, **w.stubs()):
            ok = _quiet(P.start_mcp_server, SEARCH, password="deepmap", target=a)
        check("K3: a port the registry does NOT give this project -> never signalled", not w.killed and not w.spawned,
              f"{w.killed} {w.spawned}")
    # an owner FILE that names another pid (not the pid file's): ROUND-10 L1 — the record of an EARLIER process is no
    # owner record (the hook's rule): the K3 evidence decides -> restarted (was: "ambiguous -> left alone")
    with _Sandbox(bolt="up") as (home, work), _ClaudeDir():
        a = _mk_project(work, "a", marker=True)
        s, e = PR.allocate_ports(a)
        _mcp_env((s, e))
        w = _preowner_world(a, s, 6805, _fake_plugin(work / "plugins" / "deep-map", OLD_VER))
        P._mcp_ownerfile(SEARCH, s).write_text(json.dumps({"pid": 1234, "project_key": PR.project_key(a)}),
                                               encoding="utf-8")
        with _Patch(P, **w.stubs()):
            ok = _quiet(P.start_mcp_server, SEARCH, password="deepmap", target=a)
        check("K3/L1: an owner file naming ANOTHER pid than the pid file = no owner record -> restarted on the evidence",
              bool(w.killed) and bool(w.spawned), f"{w.killed} {w.spawned}")
    # setup's own path (restart_own) is unchanged: it restarts a pre-upgrade server anyway (ROUND-6 H1)
    # heal end to end: both v0.27.4 servers restarted; the heal status says so (the reconnect note's evidence)
    with _Sandbox(bolt="up") as (home, work), _ClaudeDir():
        a = _mk_project(work, "a", marker=True)
        s, e = PR.allocate_ports(a)
        PR.allocate_neo4j_ports(a, base_adopt_ok=lambda: False)
        v274 = _fake_plugin(work / "plugins" / "deep-map", OLD_VER)
        _seed_pidfile_only(SEARCH, s, 6811)
        _seed_pidfile_only(EMBED, e, 6812)
        w = _CodeWorld({6811: (SEARCH, s), 6812: (EMBED, e)})
        w.cmd[6811] = _script_cmd(v274.as_posix(), SEARCH)
        w.cmd[6812] = _script_cmd(v274.as_posix(), EMBED)
        _clear_port_env()
        with _Patch(P, **w.stubs()), _Patch(P, ensure_neo4j=lambda **k: True, _ensure_mcp_json=lambda t: None):
            rc, out = _captured(P.main, ["heal", str(a)])
        st = _read_json(home / "run" / f"heal-{PR.project_key(a)}.json")
        check("heal (the first session after the older build -> this upgrade): rc 0, both servers restarted",
              rc == 0 and sorted(w.spawned) == sorted([(SEARCH, s), (EMBED, e)]), f"rc={rc} {w.spawned} {out[-300:]}")
        check("... heal status: mcp_started names both",
              sorted(st.get("mcp_started") or []) == sorted([SEARCH, EMBED]), str(st))
        mcr = st.get("mcp_code_restarted")
        check("... and mcp_code_restarted names both with the old code (the older build's version)",
              isinstance(mcr, dict) and sorted(mcr) == sorted([SEARCH, EMBED])
              and all(OLD_VER in str(v) for v in mcr.values()), str(mcr))
        # a second heal: nothing to restart; the evidence is carried forward with mcp_started
        w.spawned.clear()
        with _Patch(P, **w.stubs()), _Patch(P, ensure_neo4j=lambda **k: True, _ensure_mcp_json=lambda t: None):
            rc = _quiet(P.main, ["heal", str(a)])
        st2 = _read_json(home / "run" / f"heal-{PR.project_key(a)}.json")
        check("a second heal restarts nothing, and carries mcp_code_restarted forward with mcp_started",
              rc == 0 and not w.spawned and st2.get("mcp_code_restarted") == mcr, f"{w.spawned} {st2}")


# =========================================================================== K4 — never downgrade

def gate_k4_version_order_unit() -> None:
    print("\n-- K4: version ordering + the restart verdict (the contract W1's hook side mirrors) --")
    cmp_ = _api("_version_cmp")
    if callable(cmp_):
        for a, b, want in (("0.27.4", "0.28.0", -1), ("0.28.1", "0.28.0", 1), ("0.28.0", "0.28.0", 0),
                           ("v0.28.0", "0.28.0", 0), ("0.28.0+build.7", "0.28.0", 0), ("0.10.0", "0.9.9", 1),
                           ("1.0", "1.0.0", 0), ("0.28.0-rc.1", "0.28.0", -1), ("0.28.0-rc.2", "0.28.0-rc.10", -1),
                           ("0.28.0-alpha", "0.28.0-1", 1), ("garbage", "0.28.0", None), (None, "0.28.0", None),
                           ("0.28.0-w2live", "0.27.5", 1)):
            check(f"_version_cmp({a!r}, {b!r}) == {want}", cmp_(a, b) == want, repr(cmp_(a, b)))
    verdict = _api("_code_restart_verdict")
    if not callable(verdict):
        return
    with tempfile.TemporaryDirectory(prefix="dmr9v") as t:
        t = Path(t)
        p280, p281 = _fake_plugin(t / "cache", "0.28.0"), _fake_plugin(t / "cache", "0.28.1")
        dev = _fake_plugin(t, "0.28.0", name="dev-tree")
        cur281, cur280, cur_dev = _ident(p281, "0.28.1"), _ident(p280, "0.28.0"), _ident(dev, "0.28.0")
        gone = (t / "cache" / "0.27.9").as_posix()
        table = (
            ("recorded 0.28.0 (existing dir) vs current 0.28.1 -> restart (upgrade)", p280, "0.28.0", cur281, None, True),
            ("recorded 0.28.1 (existing dir) vs current 0.28.0 -> NEVER (a downgrade)", p281, "0.28.1", cur280, None,
             False),
            ("recorded dir gone (even a newer recorded version) -> restart (nothing to protect)", gone, "0.29.0", cur280,
             None, True),
            ("same dir, another recorded version (an in-place update) -> restart", p280, "0.27.0", cur280, None, True),
            ("same dir, same version -> not stale", p280, "0.28.0", cur280, None, False),
            ("same dir, recorded version unknown -> not stale", p280, None, cur280, None, False),
            ("equal version, another existing dir, no installed record -> no restart", dev, "0.28.0", cur280, None,
             False),
            ("equal version, the CURRENT dir is the installed newest -> NO restart (ROUND-10 L4: no tie-break)", dev,
             "0.28.0", cur280, [(p280, "0.28.0")], False),
            ("equal version, the RUNNING dir is the installed one -> no restart", p280, "0.28.0", cur_dev,
             [(p280, "0.28.0")], False),
            ("installed records 0.28.0 + 0.28.1: the current 0.28.0 is not the newest -> no restart", dev, "0.28.0",
             cur280, [(p280, "0.28.0"), (p281, "0.28.1")], False),
            ("a pre-J5 record (root only): the running dir's plugin.json 0.28.0 < current 0.28.1 -> restart", p280,
             None, cur281, None, True),
            ("a pre-J5 record (root only): the running dir's plugin.json 0.28.1 > current 0.28.0 -> never", p281,
             None, cur280, None, False),
            ("unparseable versions from two existing dirs, not installed -> not a restart", dev, "banana", _ident(
                p280, "cherry"), None, None),
            ("no code fields at all -> unknown", None, None, cur280, None, None),
        )
        for label, rroot, rver, cur, installed, want in table:
            with _ClaudeDir(installed):
                got = verdict(None if rroot is None else (rroot if isinstance(rroot, str) else rroot.as_posix()),
                              rver, cur)
            check(f"verdict: {label}", got is want, f"got {got!r}")


def gate_k4_no_ping_pong() -> None:
    print("\n-- K4: two windows on two plugin versions never ping-pong the servers; equal versions converge --")
    with _Sandbox() as (home, work), _ClaudeDir():
        a = _mk_project(work, "a", marker=True)
        s, e = PR.allocate_ports(a)
        _mcp_env((s, e))
        dir_a = _fake_plugin(work / "cache", "0.28.0")
        dir_b = _fake_plugin(work / "cache", "0.28.1")
        id_a, id_b = _ident(dir_a, "0.28.0"), _ident(dir_b, "0.28.1")
        w = _CodeWorld({6901: (SEARCH, s)})
        with _Patch(P, **w.stubs()), _Patch(P, _code_identity=lambda: dict(id_a)):
            _seed_owned(SEARCH, s, 6901, a)          # window A (0.28.0) started it
        with _Patch(P, **w.stubs()), _Patch(P, _code_identity=lambda: dict(id_b)):
            ok_b = _quiet(P.start_mcp_server, SEARCH, password="deepmap", target=a)   # window B's heal (newer)
        check("window B (0.28.1) restarts A's 0.28.0 server", ok_b is True and len(w.spawned) == 1, str(w.spawned))
        rec = _read_json(P._mcp_ownerfile(SEARCH, s))
        check("... the new sidecar records 0.28.1", rec.get(PR.CODE_VERSION_FIELD) == "0.28.1", str(rec))
        for i in range(3):
            with _Patch(P, **w.stubs()), _Patch(P, _code_identity=lambda: dict(id_a)):
                ok_a, out_a = _captured(P.start_mcp_server, SEARCH, password="deepmap", target=a)   # A's /clear heal
            with _Patch(P, **w.stubs()), _Patch(P, _code_identity=lambda: dict(id_b)):
                _quiet(P.start_mcp_server, SEARCH, password="deepmap", target=a)
        check("window A (0.28.0) never downgrades B's servers; three rounds of alternating heals: still ONE restart",
              ok_a is True and len(w.spawned) == 1 and len(w.killed) == 1, f"{w.killed} {w.spawned}")
        check("... A's heal reports them as already listening", "already listening" in out_a, out_a.strip()[-200:])
    with _Sandbox() as (home, work), _ClaudeDir() as cd:
        a = _mk_project(work, "a", marker=True)
        s, e = PR.allocate_ports(a)
        _mcp_env((s, e))
        dev = _fake_plugin(work, "0.28.0", name="dev-tree")
        plug = _fake_plugin(work / "cache", "0.28.0")
        cd.write([(plug, "0.28.0")])
        id_dev, id_plug = _ident(dev, "0.28.0"), _ident(plug, "0.28.0")
        w = _CodeWorld({6911: (SEARCH, s)})
        with _Patch(P, **w.stubs()), _Patch(P, _code_identity=lambda: dict(id_dev)):
            _seed_owned(SEARCH, s, 6911, a)          # a dev source tree started it
        with _Patch(P, **w.stubs()), _Patch(P, _code_identity=lambda: dict(id_plug)):
            _quiet(P.start_mcp_server, SEARCH, password="deepmap", target=a)   # the installed plugin's heal
        n = len(w.spawned)
        for _ in range(2):
            with _Patch(P, **w.stubs()), _Patch(P, _code_identity=lambda: dict(id_dev)):
                _quiet(P.start_mcp_server, SEARCH, password="deepmap", target=a)
            with _Patch(P, **w.stubs()), _Patch(P, _code_identity=lambda: dict(id_plug)):
                _quiet(P.start_mcp_server, SEARCH, password="deepmap", target=a)
        check("equal versions (dev tree + installed plugin): never restarted by either heal (ROUND-10 L4 — setup "
              "restarts its own)", n == 0 and len(w.spawned) == 0, f"first={n} total={w.spawned}")


# =========================================================================== K5 — a team-committed hooks dir

def gate_k5_team_hooks_dir() -> None:
    print("\n-- K5: an in-repo core.hooksPath dir the team commits: --install refuses (even with no post-commit yet) --")
    if not shutil.which("git"):
        return
    with _GitBox() as gb:
        a = gb.repo("a")
        gb.git(a, "config", "core.hooksPath", ".githooks")
        (a / ".githooks").mkdir()
        (a / ".githooks" / "pre-commit").write_text("#!/bin/sh\necho team pre-commit\n", encoding="utf-8", newline="\n")
        gb.git(a, "add", ".githooks/pre-commit")
        gb.git(a, "commit", "-qm", "team hooks")
        st0 = gb.git(a, "status", "--porcelain")
        rc, out = _captured(P.git_hook, str(a), "install")
        check("a committed .githooks (only pre-commit tracked), no post-commit yet -> refused rc 2, nothing created",
              rc == 2 and not (a / ".githooks" / "post-commit").exists(), f"rc={rc} {out[-300:]}")
        check("... the message says the directory is committed/tracked and prints the line to add by hand",
              ("commit" in out.lower() or "tracked" in out.lower()) and "deepmap_cli.py" in out, out.strip()[-400:])
        check("... `git status` shows nothing new", gb.git(a, "status", "--porcelain") == st0,
              gb.git(a, "status", "--porcelain"))
        rc, out = _captured(P.git_hook, str(a), "status")
        check("--status: rc 0, --install not available here", rc == 0 and "not available" in out, out.strip()[-300:])
        check("setup's hint: no --install tip", "git-hook --install" not in _captured(P._print_git_hook_hint, str(a))[1])
        # an untracked, local post-commit in that committed dir: still refused (one `git add .` from being shipped)
        (a / ".githooks" / "post-commit").write_text("#!/bin/sh\necho mine\n", encoding="utf-8", newline="\n")
        os.chmod(a / ".githooks" / "post-commit", 0o755)
        before = (a / ".githooks" / "post-commit").read_bytes()
        rc = _quiet(P.git_hook, str(a), "install")
        check("... an existing UNTRACKED post-commit inside that committed dir -> refused too, untouched",
              rc == 2 and (a / ".githooks" / "post-commit").read_bytes() == before, f"rc={rc}")
    with _GitBox() as gb:
        a = gb.repo("a")
        gb.git(a, "config", "core.hooksPath", ".localhooks")
        (a / ".localhooks").mkdir()
        st0 = gb.git(a, "status", "--porcelain")
        rc, out = _captured(P.git_hook, str(a), "install")
        check("an untracked in-repo hooks dir git does NOT ignore: a NEW hook would be an untracked file -> refused",
              rc == 2 and not (a / ".localhooks" / "post-commit").exists(), f"rc={rc} {out[-300:]}")
        (a / ".git" / "info").mkdir(parents=True, exist_ok=True)
        with open(a / ".git" / "info" / "exclude", "a", encoding="utf-8") as f:
            f.write("\n.localhooks/\n")
        rc = _quiet(P.git_hook, str(a), "install")
        check("control: the same dir, ignored by git (.git/info/exclude) -> installed (created), git status clean",
              rc == 0 and (a / ".localhooks" / "post-commit").exists() and gb.git(a, "status", "--porcelain") == st0,
              f"rc={rc} {gb.git(a, 'status', '--porcelain')!r}")
    with _GitBox() as gb:
        a = gb.repo("a")
        rc = _quiet(P.git_hook, str(a), "install")
        check("control: the default .git/hooks -> installed as before", rc == 0 and gb.hook(a).exists(), f"rc={rc}")


# =========================================================================== main

def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 68)
    print("DeepMap — provisioner round-9 gates (hermetic, W2): K1 old git, K2 sync-root tip, K3 v0.27.4 upgrade")
    print("          restart, K4 never downgrade, K5 team hooks dir")
    print("=" * 68)
    for g in (gate_k1_old_git, gate_k2_tip_and_subproject_refusal, gate_k3_preowner_upgrade_restart,
              gate_k4_version_order_unit, gate_k4_no_ping_pong, gate_k5_team_hooks_dir):
        _run_gate(g)
    print("\n" + "=" * 68)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
