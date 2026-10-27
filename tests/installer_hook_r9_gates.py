#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ROUND-9 K8 — the installers' git hook follows the ROUND-8 J1 rules (+ the T7 lock the re-run path needs).

install.sh / install.ps1 used to write their OWN sh block into .git/hooks/post-commit AND post-checkout:
  * appended to ANY existing hook — a `#!/usr/bin/env python3` hook then failed every commit with a SyntaxError, and a
    hook ending in `exec …` / `exit` never reached the block;
  * synced the directory the installer ran in (never the project's SYNC ROOT; a sub-project got no hook at all, since
    `<subdir>/.git/hooks` does not exist) with no work-tree scoping;
  * wrote into `.git/hooks` even where git runs `core.hooksPath` (a dead hook), and skipped a linked worktree;
  * overwrote `.deepmap/instance.json` — RM-1's learned-fusion state — with a hook-ownership map;
  * on a re-run found its own old block ("already-chained") and never upgraded it.
ROUND-8 J1 fixed all of that in ONE place — `provision.py git-hook --install`. K8: the installers now DELEGATE to it
(post-commit only), skip DeepMap's own install directory, and have an opt-out.

T7 (the installer re-run path): the provisioning lock `.deepmap/provision.lock` was never released on SUCCESS (so every
re-run of the installer died "lock already present"), and install.sh's RETURN-trap cleanup never fires under
`set -e` (errexit leaves the shell without running it), so it leaked on exactly the failure it was meant to clean.

BEHAVIORAL: each installer's own function text is extracted and run for real (bash / Windows PowerShell or pwsh) against
throwaway git repos, with the REAL build/provision.py doing the hook work. Hermetic: temp DEEPMAP_HOME, an isolated git
config (empty global, no system — a developer's own core.hooksPath must not leak in), a sh stub as the hook's Python
(records the sync target on a commit), no Neo4j, no network. A shell that is not available is a [skip], never a verdict.
"""
from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SH = ROOT / "install.sh"
PS1 = ROOT / "install.ps1"
sys.path.insert(0, str(ROOT / "build"))

_PASS = 0
_FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {label}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}{(' — ' + str(detail)[:600]) if detail else ''}")


def _run_gate(fn) -> None:
    """An unexpected raise counts as ONE failed check, never a crash."""
    try:
        fn()
    except Exception as e:   # noqa: BLE001
        import traceback
        tb = traceback.format_exc().strip().splitlines()
        check(f"{fn.__name__} ran without raising", False, f"{type(e).__name__}: {e} " + " | ".join(tb[-4:]))


def _rmtree(path) -> None:
    """Delete a dir THIS gate created (exact path) — git's read-only object files included."""
    def onerr(fn, p, _exc):
        try:
            os.chmod(p, stat.S_IWRITE)
            fn(p)
        except Exception:
            pass
    if path and Path(path).exists():
        shutil.rmtree(str(path), onerror=onerr)


# ---- the shells ------------------------------------------------------------------------------------------------ #
def _bash() -> str | None:
    """A bash that sees this filesystem: on Windows Git's bash (never WSL's System32\\bash.exe)."""
    cand = shutil.which("bash")
    if os.name != "nt":
        return cand
    out = []
    if cand and "system32" not in cand.lower():
        out.append(cand)
    git = shutil.which("git")
    if git:
        g = Path(git).resolve()
        for up in g.parents:
            for rel in ("bin/bash.exe", "usr/bin/bash.exe"):
                if (up / rel).is_file():
                    out.append(str(up / rel))
    return out[0] if out else None


def _powershell() -> str | None:
    """Windows PowerShell 5.1 first (the powershell.exe every Windows box ships — the installer's real target), else
    PowerShell 7."""
    return (shutil.which("powershell") if os.name == "nt" else None) or shutil.which("pwsh")


# ---- extracting the installers' functions (works on the old and the new text alike) ----------------------------- #
def sh_functions(text: str) -> str:
    """Every top-level `name() {` … `}` function of install.sh, in file order."""
    out, cur = [], None
    for ln in text.splitlines():
        if cur is None and re.match(r"^[A-Za-z_][A-Za-z0-9_]*\(\)\s*\{\s*$", ln):
            cur = [ln]
        elif cur is not None:
            cur.append(ln)
            if ln.rstrip() == "}":
                out.append("\n".join(cur))
                cur = None
    return "\n\n".join(out)


def ps_functions(text: str) -> str:
    """Every top-level `function Name … {` … `}` of install.ps1, in file order."""
    out, cur = [], None
    for ln in text.splitlines():
        if cur is None and re.match(r"^function\s+[A-Za-z][\w-]*", ln):
            cur = [ln]
        elif cur is not None:
            cur.append(ln)
            if ln.rstrip() == "}":
                out.append("\n".join(cur))
                cur = None
    return "\n\n".join(out)


def _sq(s) -> str:
    """sh single-quote."""
    return "'" + str(s).replace("'", "'\\''") + "'"


def _pq(s) -> str:
    """PowerShell single-quote."""
    return "'" + str(s).replace("'", "''") + "'"


def _fwd(p) -> str:
    return str(p).replace("\\", "/")


class _Box:
    """A throwaway work dir + DEEPMAP_HOME + an isolated git config + a sh stub as the hook's Python (records the
    target of `<cli> sync <target>`). Sets the same env in-process (for provision's own report) and restores it."""

    KEYS = ("DEEPMAP_HOME", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM", "DEEPMAP_PYTHON", "GIT_DIR", "GIT_WORK_TREE",
            "SKIP_GIT_HOOK", "NEO4J_URI", "NEO4J_PASSWORD")

    def __enter__(self):
        self.base = Path(tempfile.mkdtemp(prefix="dmk8g")).resolve()
        self.saved = {k: os.environ.get(k) for k in self.KEYS}
        for k in ("GIT_DIR", "GIT_WORK_TREE", "SKIP_GIT_HOOK"):
            os.environ.pop(k, None)
        self.home = self.base / "home"
        self.home.mkdir()
        self.gcfg = self.base / "gitconfig"
        self.gcfg.write_text("[user]\n\tname = t\n\temail = t@t\n[init]\n\tdefaultBranch = main\n", encoding="utf-8")
        self.log = self.base / "synced.log"
        self.stub = self.base / "stub-python.sh"
        self.stub.write_text("#!/bin/sh\n# records: <cli> sync <target>\n"
                             f"printf '%s\\n' \"$3\" >> '{self.log.as_posix()}'\n", encoding="utf-8", newline="\n")
        os.chmod(self.stub, 0o755)
        # NEO4J_URI -> a port nothing listens on + a bogus password: even a hook that bypassed the stub (an OLD
        # installer's hard-coded interpreter) could never reach a real Neo4j (the operator's 7687 above all).
        os.environ.update({"DEEPMAP_HOME": str(self.home), "GIT_CONFIG_GLOBAL": str(self.gcfg),
                           "GIT_CONFIG_NOSYSTEM": "1", "DEEPMAP_PYTHON": self.stub.as_posix(),
                           "NEO4J_URI": "bolt://127.0.0.1:9", "NEO4J_PASSWORD": "dm-k8-gate-not-a-password"})
        self.work = self.base / "w"
        self.work.mkdir()
        self.n = 0
        return self

    def __exit__(self, *a):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _rmtree(self.base)

    def env(self, **extra) -> dict:
        e = dict(os.environ)
        e["PATH"] = str(Path(sys.executable).parent) + os.pathsep + e.get("PATH", "")
        e["PYTHONUTF8"] = "1"
        for k, v in extra.items():
            if v is None:
                e.pop(k, None)
            else:
                e[k] = v
        return e

    def git(self, cwd, *args, check=True) -> str:
        r = subprocess.run([shutil.which("git"), "-C", str(cwd), *args], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=120, env=self.env(), stdin=subprocess.DEVNULL)
        if check and r.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()}")
        return r.stdout.strip()

    def repo(self, name) -> Path:
        d = self.work / name
        d.mkdir(parents=True)
        self.git(d, "init", "-q")
        (d / "a.txt").write_text("a\n", encoding="utf-8")
        self.git(d, "add", "a.txt")
        self.git(d, "commit", "-qm", "init")
        return d.resolve()

    @staticmethod
    def manifest(d: Path) -> None:
        (d / ".deepmap").mkdir(parents=True, exist_ok=True)
        (d / ".deepmap" / "graph_build.json").write_text('{"verdict": "READY", "nodes": 5}', encoding="utf-8")

    def hooks_dir(self, repo) -> Path:
        rel = self.git(repo, "rev-parse", "--git-path", "hooks")
        p = Path(rel)
        return p if p.is_absolute() else (Path(repo) / p)

    def synced(self, wait_s: float = 12.0) -> list:
        import time
        deadline = time.time() + wait_s
        while time.time() < deadline and not (self.log.exists() and self.log.read_text(encoding="utf-8").strip()):
            time.sleep(0.2)
        time.sleep(0.3)
        return [ln.strip() for ln in self.log.read_text(encoding="utf-8").splitlines()] if self.log.exists() else []

    def commit(self, repo, msg="c") -> int:
        f = Path(repo) / "a.txt"
        f.write_text(f.read_text(encoding="utf-8") + msg + "\n", encoding="utf-8")
        r = subprocess.run([shutil.which("git"), "commit", "-qam", msg], cwd=str(repo), capture_output=True,
                           text=True, timeout=120, env=self.env(), stdin=subprocess.DEVNULL)
        return r.returncode

    # ---- running an installer's hook step for real
    def script(self, name: str, body: str, *, newline="\n") -> Path:
        self.n += 1
        p = self.base / f"{self.n:02d}-{name}"
        p.write_text(body, encoding="utf-8", newline=newline)
        return p

    def _run(self, argv, *, env_extra=None, timeout=300):
        """Run one harness script (stdin closed). Every step it runs is idempotent, so ONE retry after a timeout —
        a shell that stalls starting under heavy machine load — is safe; it is printed, never silent."""
        for attempt in (1, 2):
            try:
                r = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                   timeout=timeout, env=self.env(**(env_extra or {})), stdin=subprocess.DEVNULL)
                return r.returncode, (r.stdout or "") + (r.stderr or "")
            except subprocess.TimeoutExpired:
                if attempt == 2:
                    raise
                print(f"  [note] {Path(argv[-1]).name} timed out after {timeout}s — retrying once (machine load)")
        raise AssertionError("unreachable")

    def run_sh(self, body: str, *, env_extra=None, timeout=300):
        p = self.script("run.sh", body)
        return self._run([_bash(), str(p)], env_extra=env_extra, timeout=timeout)

    def run_ps(self, body: str, *, env_extra=None, timeout=300):
        p = self.script("run.ps1", body, newline="\r\n")
        return self._run([_powershell(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(p)],
                         env_extra=env_extra, timeout=timeout)

    def install_hook(self, kind: str, repo, *, script_dir=ROOT, skip=False, env_extra=None):
        """Run the installer's OWN git-hook step (its extracted functions) for `repo`, as its main does."""
        if kind == "sh":
            body = ("set -euo pipefail\n"
                    f"SCRIPT_DIR={_sq(_fwd(script_dir))}\n"
                    f"REPO_ROOT={_sq(_fwd(repo))}\n"
                    + sh_functions(SH.read_text(encoding="utf-8")) + "\n"
                    f"py() {{ {_sq(_fwd(sys.executable))} \"$@\"; }}\n"
                    f"python3() {{ {_sq(_fwd(sys.executable))} \"$@\"; }}\n"
                    "install_git_hooks\n")
            extra = dict(env_extra or {})
            if skip:
                extra["SKIP_GIT_HOOK"] = "1"
            return self.run_sh(body, env_extra=extra)
        body = ("$ErrorActionPreference = 'Stop'\n"
                "Set-StrictMode -Version Latest\n"
                f"$ScriptDir = {_pq(script_dir)}\n"
                f"$DeepMapPy = {_pq(self.stub.as_posix())}\n"   # an old-style hook line's interpreter -> the stub
                "$py = 'python'\n"
                f"$SkipGitHook = {'$true' if skip else '$false'}\n"
                f"$RepoRoot = {_pq(repo)}\n"
                + ps_functions(PS1.read_text(encoding="utf-8")) + "\n"
                f"Install-DeepMapGitHooks {_pq(repo)} 'python'\n"
                "exit 0\n")
        return self.run_ps(body, env_extra=env_extra)


def _provision():
    try:
        import provision as P   # type: ignore
        return P
    except Exception as e:   # noqa: BLE001
        check("build/provision.py imports", False, repr(e))
        return None


def _kinds() -> list:
    ks = []
    if SH.exists() and _bash():
        ks.append("sh")
    else:
        print("  [skip] install.sh: no bash here")
    if PS1.exists() and _powershell():
        ks.append("ps1")
    else:
        print("  [skip] install.ps1: no PowerShell here")
    return ks


def _targets(text: str) -> list:
    return [m.group(1) for m in re.finditer(r"deepmap_cli\.py'?\"?\s+sync\s+['\"]([^'\"]*)['\"]", text or "")]


def _same_dir(a, b) -> bool:
    try:
        return os.path.normcase(str(Path(a).resolve())) == os.path.normcase(str(Path(b).resolve()))
    except Exception:
        return False


# ---- K8 gates ---------------------------------------------------------------------------------------------------- #
def gate_fresh_repo() -> None:
    print("\n-- K8: a fresh repo gets provision's CURRENT scoped post-commit block; nothing else is written --")
    P = _provision()
    if not shutil.which("git") or P is None:
        return
    for kind in _kinds():
        with _Box() as gb:
            a = gb.repo("a")
            (a / ".deepmap").mkdir()
            state = '{"learned_fusion": {"learning_enabled": true, "weights": [1, 1, 1]}}\n'
            (a / ".deepmap" / "instance.json").write_text(state, encoding="utf-8")
            hooks = gb.hooks_dir(a)
            rc, out = gb.install_hook(kind, a)
            pc_path = hooks / "post-commit"
            text = pc_path.read_text(encoding="utf-8") if pc_path.exists() else ""
            check(f"{kind}: the hook step exits 0", rc == 0, out[-500:])
            rep = P._git_hook_report(str(a))
            check(f"{kind}: provision itself calls the installed hook CURRENT (its scoped block, not an installer copy)",
                  rep.get("state") == "current", f"state={rep.get('state')} reasons={rep.get('reasons')} | {text}")
            check(f"{kind}: exactly ONE DeepMap block, scoped to this work tree (-ef guard), right after #!/bin/sh",
                  text.count("# >>> deepmap") == 1 and " -ef " in text and text.startswith("#!/bin/sh\n# >>> deepmap"),
                  text)
            check(f"{kind}: no post-checkout hook is written (DeepMap's auto-sync is post-commit only)",
                  not (hooks / "post-checkout").exists(),
                  (hooks / "post-checkout").read_text(encoding="utf-8") if (hooks / "post-checkout").exists() else "")
            check(f"{kind}: .deepmap/instance.json (RM-1 learned-fusion state) is left byte-identical",
                  (a / ".deepmap" / "instance.json").read_text(encoding="utf-8") == state,
                  (a / ".deepmap" / "instance.json").read_text(encoding="utf-8"))
            check(f"{kind}: the sync target has no backslash (git's sh would eat it) and never --stale-cleanup",
                  bool(_targets(text)) and not any("\\" in t for t in _targets(text)) and "--stale-cleanup" not in text,
                  str(_targets(text)))
            rc2, out2 = gb.install_hook(kind, a)
            check(f"{kind}: a re-run is idempotent (rc 0, hook byte-identical)",
                  rc2 == 0 and pc_path.exists() and pc_path.read_text(encoding="utf-8") == text, out2[-300:])
            gb.manifest(a)   # a built graph, so the commit's sync has somewhere to go
            crc = gb.commit(a)
            got = gb.synced()
            check(f"{kind}: END-TO-END — a real `git commit` runs the installed hook, which syncs this project",
                  crc == 0 and len(got) == 1 and _same_dir(got[0], a), f"rc={crc} synced={got}")


def gate_refuses_non_sh_and_tracked() -> None:
    print("\n-- K8: never append sh to a Python hook; never write a tracked / core.hooksPath hook or a dead .git/hooks --")
    if not shutil.which("git"):
        return
    for kind in _kinds():
        with _Box() as gb:
            a = gb.repo("a")
            hp = gb.hooks_dir(a) / "post-commit"
            hp.parent.mkdir(parents=True, exist_ok=True)
            py_hook = "#!/usr/bin/env python3\nimport sys\nprint('user python hook')\nsys.exit(0)\n"
            hp.write_text(py_hook, encoding="utf-8", newline="\n")
            os.chmod(hp, 0o755)
            before = hp.read_bytes()
            rc, out = gb.install_hook(kind, a)
            check(f"{kind}: a Python post-commit is left BYTE-IDENTICAL (sh appended to it broke every commit)",
                  hp.read_bytes() == before, hp.read_text(encoding="utf-8"))
            check(f"{kind}: ... the install is not failed by the refusal (rc 0) and says no hook was written",
                  rc == 0 and ("not installed" in out.lower() or "no git hook" in out.lower()), out[-500:])
            check(f"{kind}: ... and no post-checkout was created either", not (hp.parent / "post-checkout").exists())
        with _Box() as gb:
            a = gb.repo("a")
            gh = a / ".githooks"
            gh.mkdir()
            team = "#!/bin/sh\necho team-hook\n"
            (gh / "post-commit").write_text(team, encoding="utf-8", newline="\n")
            os.chmod(gh / "post-commit", 0o755)
            gb.git(a, "add", ".githooks/post-commit")
            gb.git(a, "config", "core.hooksPath", ".githooks")
            before = (gh / "post-commit").read_bytes()
            rc, out = gb.install_hook(kind, a)
            check(f"{kind}: a git-TRACKED core.hooksPath hook (.githooks/post-commit) is left byte-identical",
                  (gh / "post-commit").read_bytes() == before, (gh / "post-commit").read_text(encoding="utf-8"))
            dead = [p.name for p in ((a / ".git" / "hooks" / "post-commit"), (a / ".git" / "hooks" / "post-checkout"))
                    if p.exists()]
            check(f"{kind}: ... and no DEAD hook is written into .git/hooks (git runs core.hooksPath, never these)",
                  not dead, str(dead))
            check(f"{kind}: ... rc 0 (a refusal never fails the install)", rc == 0, out[-400:])


def gate_exec_terminated_hook() -> None:
    print("\n-- K8: a hook ending in `exec …` gets the block right AFTER its shebang (appended, git never reached it) --")
    if not shutil.which("git"):
        return
    for kind in _kinds():
        with _Box() as gb:
            a = gb.repo("a")
            hp = gb.hooks_dir(a) / "post-commit"
            hp.parent.mkdir(parents=True, exist_ok=True)
            hp.write_text("#!/bin/sh\necho framework-hook\nexec true \"$@\"\n", encoding="utf-8", newline="\n")
            os.chmod(hp, 0o755)
            rc, out = gb.install_hook(kind, a)
            lines = hp.read_text(encoding="utf-8").splitlines()
            blk = next((i for i, ln in enumerate(lines) if ln.startswith("# >>> deepmap")), -1)
            ex = next((i for i, ln in enumerate(lines) if ln.startswith("exec true")), -1)
            check(f"{kind}: the DeepMap block sits right after the shebang, BEFORE the exec (so git reaches it)",
                  rc == 0 and blk == 1 and 0 <= blk < ex, "\n".join(lines) + " | " + out[-300:])
            check(f"{kind}: ... the user's own lines are kept, in order",
                  "echo framework-hook" in lines and lines[0] == "#!/bin/sh" and lines[-1] == 'exec true "$@"',
                  "\n".join(lines))


def gate_sync_root_subproject() -> None:
    print("\n-- K8: a sub-project set up on its own is synced at ITS sync root (the old installer wrote no hook) --")
    if not shutil.which("git"):
        return
    for kind in _kinds():
        with _Box() as gb:
            mono = gb.repo("mono")
            be = mono / "backend"
            be.mkdir()
            gb.manifest(be)
            rc, out = gb.install_hook(kind, be)
            hp = gb.hooks_dir(mono) / "post-commit"
            text = hp.read_text(encoding="utf-8") if hp.exists() else ""
            t = _targets(text)
            check(f"{kind}: the repo's post-commit syncs mono/backend (the sync root), never the toplevel",
                  rc == 0 and len(t) == 1 and _same_dir(t[0].replace("/", os.sep) if os.name == "nt" else t[0], be),
                  f"targets={t} | {out[-300:]}")


def gate_upgrades_old_installer_hook() -> None:
    print("\n-- K8: a re-run replaces an OLD installer's block in place (it used to say 'already-chained' and stop) --")
    P = _provision()
    if not shutil.which("git") or P is None:
        return
    for kind in _kinds():
        with _Box() as gb:
            a = gb.repo("a")
            hooks = gb.hooks_dir(a)
            hooks.mkdir(parents=True, exist_ok=True)
            cli = (ROOT / "build" / "deepmap_cli.py").as_posix()
            v0274 = ('mkdir -p "$HOME/.deepmap" 2>/dev/null; ( nohup python3 "' + cli + '" sync "' + a.as_posix()
                     + '" >"$HOME/.deepmap/sync.log" 2>&1 & ) || true   # deepmap')
            (hooks / "post-commit").write_text("#!/bin/sh\necho user\n\n# >>> deepmap (managed; existing hook preserved "
                                               "on chain) >>>\n" + v0274 + "\n# <<< deepmap <<<\n",
                                               encoding="utf-8", newline="\n")
            pre_f1 = ('mkdir -p "$HOME/.deepmap" 2>/dev/null; ( nohup python3 "' + cli + '" sync "' + a.as_posix()
                      + '" --stale-cleanup >"$HOME/.deepmap/sync.log" 2>&1 & ) || true   # deepmap')
            (hooks / "post-checkout").write_text("#!/bin/sh\n" + pre_f1 + "\n", encoding="utf-8", newline="\n")
            for n in ("post-commit", "post-checkout"):
                os.chmod(hooks / n, 0o755)
            rc, out = gb.install_hook(kind, a)
            text = (hooks / "post-commit").read_text(encoding="utf-8")
            rep = P._git_hook_report(str(a))
            check(f"{kind}: the old unscoped block is REPLACED — one current, scoped block (provision agrees)",
                  rc == 0 and text.count("# >>> deepmap") == 1 and " -ef " in text and rep.get("state") == "current",
                  f"state={rep.get('state')} reasons={rep.get('reasons')} | {text}")
            check(f"{kind}: ... the user's own line is kept", "echo user" in text)
            pc = (hooks / "post-checkout")
            pct = pc.read_text(encoding="utf-8") if pc.exists() else ""
            check(f"{kind}: ... and the old installer's DESTRUCTIVE post-checkout line (--stale-cleanup) is gone",
                  "--stale-cleanup" not in pct, pct)
    if os.name != "nt" or "ps1" not in _kinds():
        return
    with _Box() as gb:   # the Windows upgrade: a v0.27.x install.ps1 block, BACKSLASH paths (git's sh keeps them)
        a = gb.repo("a")
        hooks = gb.hooks_dir(a)
        hooks.mkdir(parents=True, exist_ok=True)
        cli = str(ROOT) + "/build/deepmap_cli.py"
        ps1_line = ('mkdir -p "$HOME/.deepmap" 2>/dev/null; ( nohup python "' + cli + '" sync "' + str(a)
                    + '" >"$HOME/.deepmap/sync.log" 2>&1 & ) || true   # deepmap')
        (hooks / "post-commit").write_text("#!/bin/sh\n# >>> deepmap (managed; existing hook preserved on chain) >>>\n"
                                           + ps1_line + "\n# <<< deepmap <<<\n", encoding="utf-8", newline="\n")
        rc, out = gb.install_hook("ps1", a)
        text = (hooks / "post-commit").read_text(encoding="utf-8")
        rep = P._git_hook_report(str(a))
        check("ps1: an old install.ps1 block (backslash Windows paths) is recognised as this project's and REPLACED "
              "in place (one current block, forward slashes)",
              rc == 0 and text.count("# >>> deepmap") == 1 and rep.get("state") == "current" and "\\" not in text,
              f"state={rep.get('state')} reasons={rep.get('reasons')} | {text}")


def _hook_interp(text: str) -> str:
    m = re.search(r"nohup\s+'([^']*)'", text or "")
    return m.group(1) if m else ""


def gate_multiword_python() -> None:
    print("\n-- F4 under K8: a command-line DEEPMAP_PYTHON (`py -3`) still yields a hook that RUNS --")
    if not shutil.which("git"):
        return
    exe = _fwd(sys.executable)
    if " " in exe:
        print("  [skip] this Python's path has a space — the command-line form can't be built from it")
        return
    for kind in _kinds():
        with _Box() as gb:
            a = gb.repo("a")
            rc, out = gb.install_hook(kind, a, env_extra={"DEEPMAP_PYTHON": f"{exe} -I"})
            hp = gb.hooks_dir(a) / "post-commit"
            text = hp.read_text(encoding="utf-8") if hp.exists() else ""
            interp = _hook_interp(text)
            check(f"{kind}: DEEPMAP_PYTHON='<python> -I' -> the hook runs that CONCRETE interpreter (one existing file), "
                  "never the command line quoted as one word (a command that does not exist)",
                  rc == 0 and bool(interp) and " -I" not in interp and Path(interp).is_file(),
                  f"interp={interp!r} | {out[-300:]}")
        with _Box() as gb:
            a = gb.repo("a")
            rc, out = gb.install_hook(kind, a, env_extra={"DEEPMAP_PYTHON": "dm-no-such-python-k8 -3"})
            hp = gb.hooks_dir(a) / "post-commit"
            text = hp.read_text(encoding="utf-8") if hp.exists() else ""
            interp = _hook_interp(text)
            check(f"{kind}: a DEEPMAP_PYTHON command that does not run -> a warning, and the hook uses a real Python",
                  rc == 0 and "DEEPMAP_PYTHON" in out and bool(interp) and Path(interp).is_file(),
                  f"interp={interp!r} | {out[-300:]}")


def gate_skip_self_and_opt_out() -> None:
    print("\n-- K8: run from DeepMap's OWN install dir (the documented deps-only run) no hook is written; opt-out --")
    if not shutil.which("git"):
        return
    for kind in _kinds():
        with _Box() as gb:
            plug = gb.repo("plug")   # a git checkout of the plugin (the marketplace clone is one)
            (plug / "build").mkdir()
            called = gb.base / "provision-called.txt"
            (plug / "build" / "provision.py").write_text(
                "import sys\nopen(%r, 'a').write(' '.join(sys.argv[1:]) + '\\n')\n" % str(called), encoding="utf-8")
            rc, out = gb.install_hook(kind, plug, script_dir=plug)
            hooks = gb.hooks_dir(plug)
            written = [n for n in ("post-commit", "post-checkout") if (hooks / n).exists()]
            check(f"{kind}: REPO_ROOT == the installer's own directory -> no hook written into the plugin checkout",
                  rc == 0 and not written and not called.exists(), f"written={written} called={called.exists()} "
                                                                   f"| {out[-300:]}")
        with _Box() as gb:
            a = gb.repo("a")
            rc, out = gb.install_hook(kind, a, skip=True)
            hooks = gb.hooks_dir(a)
            written = [n for n in ("post-commit", "post-checkout") if (hooks / n).exists()]
            flag = "SKIP_GIT_HOOK=1" if kind == "sh" else "-SkipGitHook"
            check(f"{kind}: {flag} -> no hook written, rc 0", rc == 0 and not written, f"written={written} {out[-300:]}")


def gate_static_wiring() -> None:
    print("\n-- K8 (static): delegation to provision, hook step AFTER setup, no instance.json / post-checkout writes --")
    for name, path in (("install.sh", SH), ("install.ps1", PS1)):
        text = path.read_text(encoding="utf-8")
        text_nohelp = re.sub(r"<#.*?#>", "", text, flags=re.DOTALL)   # PowerShell's comment-based help block
        code = "\n".join(ln.split("#", 1)[0] for ln in text_nohelp.splitlines())
        calls = [ln for ln in code.splitlines() if "git-hook" in ln and "--install" in ln and "provision" in ln
                 and not re.search(r"\b(echo|Write-Host|Write-Warning)\b", ln)]
        check(f"{name}: the hook step delegates to `build/provision.py git-hook --install` (a real call, not a message)",
              bool(calls) and re.search(r"provision\s*=\s*(Join-Path\s+\$ScriptDir\s+)?\"(\$SCRIPT_DIR/)?build/provision\.py\"",
                                        code) is not None, str(calls))
        check(f"{name}: the installer writes no hook file itself (no append / Set-Content onto a hook path)",
              not re.search(r">>\s*\"\$path\"|Set-Content\s+-Path\s+\$path", code))
        check(f"{name}: the installer never writes .deepmap/instance.json (RM-1's state file)",
              "instance.json" not in code and "instanceJson" not in code)
        check(f"{name}: no post-checkout hook is written", "post-checkout" not in code)
        if name == "install.sh":
            m = text[text.find("\nmain() {"):]
            ok = 0 <= m.find("provision.py\" setup") < m.find("\n  install_git_hooks")
        else:
            m = text[text.find("# main"):]
            ok = 0 <= m.find('"setup"') < m.find("Install-DeepMapGitHooks $RepoRoot")
        check(f"{name}: main runs the hook step AFTER provisioning (the hook syncs the root setup just built)", ok)


# ---- T7: the provisioning lock (the installer re-run path) ------------------------------------------------------- #
def gate_t7_lock_sh() -> None:
    print("\n-- T7 (install.sh): the provisioning lock is released on success AND on failure under set -e --")
    if not (SH.exists() and _bash()):
        print("  [skip] no bash here")
        return
    funcs = sh_functions(SH.read_text(encoding="utf-8"))
    with _Box() as gb:
        lock = gb.base / "provision.lock"
        L = _sq(_fwd(lock))
        rc, out = gb.run_sh(f"set -euo pipefail\n{funcs}\nspawn_instance {L} false\necho reached\n")
        check("a FAILED spawn under `set -e` fails the installer (non-zero, stops) and leaves NO lock behind",
              rc != 0 and "reached" not in out and not lock.exists(), f"rc={rc} lock={lock.exists()} {out[-300:]}")
        lock.unlink(missing_ok=True)
        rc, out = gb.run_sh(f"set -euo pipefail\n{funcs}\nspawn_instance {L} sh -c 'exit 3'\n")
        check("... its exit code is the provisioner's (3)", rc == 3 and not lock.exists(), f"rc={rc} {out[-300:]}")
        lock.unlink(missing_ok=True)
        rc, out = gb.run_sh(f"set -euo pipefail\n{funcs}\nspawn_instance {L} true\nspawn_instance {L} true\necho ok\n")
        check("a SUCCESSFUL spawn releases the lock, so a RE-RUN of the installer works (it died 'lock already present')",
              rc == 0 and "ok" in out and not lock.exists(), f"rc={rc} lock={lock.exists()} {out[-300:]}")
        lock.write_text("", encoding="utf-8")   # what every successful OLD installer run left behind
        rc, out = gb.run_sh(f"set -euo pipefail\n{funcs}\nspawn_instance {L} true\necho ok\n")
        check("an empty lock an OLD installer left behind is stale -> reclaimed (the upgrade path)",
              rc == 0 and "ok" in out and not lock.exists(), f"rc={rc} {out[-300:]}")
        marker = gb.base / "ran.txt"
        rc, out = gb.run_sh(f"set -euo pipefail\n{funcs}\n( exit 0 ) & dead=$!\nwait \"$dead\" || true\n"
                            f"echo \"$dead\" > {L}\nspawn_instance {L} touch {_sq(_fwd(marker))}\necho ok\n")
        check("a lock naming a pid that is gone (an installer killed mid-provision) is reclaimed",
              rc == 0 and marker.exists() and not lock.exists(), f"rc={rc} {out[-300:]}")
        marker.unlink(missing_ok=True)
        lock.unlink(missing_ok=True)
        rc, out = gb.run_sh(f"set -euo pipefail\n{funcs}\necho $$ > {L}\nspawn_instance {L} touch {_sq(_fwd(marker))}\n")
        check("a lock held by a LIVE installer is honoured (refused, nothing run, its lock kept)",
              rc != 0 and not marker.exists() and lock.exists(), f"rc={rc} {out[-300:]}")
        lock.unlink(missing_ok=True)


def gate_t7_lock_ps1() -> None:
    print("\n-- T7 (install.ps1): the provisioning lock is released on success too (a re-run died 'lock already present') --")
    if not (PS1.exists() and _powershell()):
        print("  [skip] no PowerShell here")
        return
    funcs = ps_functions(PS1.read_text(encoding="utf-8"))
    pre = "$ErrorActionPreference = 'Stop'\nSet-StrictMode -Version Latest\n" + funcs + "\n"
    with _Box() as gb:
        lock = gb.base / "provision.lock"
        L = _pq(lock)
        rc, out = gb.run_ps(pre + f"Invoke-InstanceSpawn {{ throw 'spawn boom' }} {L}\nWrite-Output 'reached'\n")
        check("a FAILED spawn fails the installer and leaves NO lock", rc != 0 and "reached" not in out
              and not lock.exists(), f"rc={rc} {out[-300:]}")
        lock.unlink(missing_ok=True)
        rc, out = gb.run_ps(pre + f"Invoke-InstanceSpawn {{ $null = 1 }} {L}\nInvoke-InstanceSpawn {{ $null = 1 }} {L}\n"
                            "Write-Output 'ok'\n")
        check("a SUCCESSFUL spawn releases the lock, so a RE-RUN of the installer works",
              rc == 0 and "ok" in out and not lock.exists(), f"rc={rc} lock={lock.exists()} {out[-400:]}")
        lock.write_text("", encoding="utf-8")
        rc, out = gb.run_ps(pre + f"Invoke-InstanceSpawn {{ $null = 1 }} {L}\nWrite-Output 'ok'\n")
        check("an empty lock an OLD installer left behind is stale -> reclaimed", rc == 0 and "ok" in out
              and not lock.exists(), f"rc={rc} {out[-300:]}")
        marker = gb.base / "ran.txt"
        rc, out = gb.run_ps(pre + f"Set-Content -LiteralPath {L} -Value $PID\n"
                            f"Invoke-InstanceSpawn {{ Set-Content -LiteralPath {_pq(marker)} -Value x }} {L}\n")
        check("a lock held by a LIVE installer is honoured (refused, nothing run, its lock kept)",
              rc != 0 and not marker.exists() and lock.exists(), f"rc={rc} {out[-300:]}")
        lock.unlink(missing_ok=True)


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 72)
    print("DeepMap — ROUND-9 K8 installer git hook (J1 rules via provision) + T7 lock (behavioral, no Neo4j)")
    print("=" * 72)
    for g in (gate_static_wiring, gate_fresh_repo, gate_refuses_non_sh_and_tracked, gate_exec_terminated_hook,
              gate_sync_root_subproject, gate_upgrades_old_installer_hook, gate_multiword_python,
              gate_skip_self_and_opt_out,
              gate_t7_lock_sh, gate_t7_lock_ps1):
        _run_gate(g)
    print("\n" + "=" * 72)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} checks passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
