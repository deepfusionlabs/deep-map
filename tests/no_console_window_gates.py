#!/usr/bin/env python3
"""0.28.1 — DeepMap's background processes must never flash console windows on Windows.

Field report (2026-09-29, the operator's machine): Windows Terminal windows titled
`C:\\WINDOWS\\system32\\tasklist.exe` kept flashing up. A 90-second process capture traced them to
DeepMap's SessionStart heal: the hook started `provision.py heal` with DETACHED_PROCESS, and a detached
process has NO console, so every console program it then runs (git, tasklist, powershell) gets a brand
new VISIBLE console window. The same held for Neo4j (`cmd.exe /c neo4j.bat console` -> java) and the
MCP servers, which provision started DETACHED as well. CREATE_NO_WINDOW gives the process a HIDDEN
console that its children inherit, so nothing is shown; it outlives its parent just the same.

  A  static (every OS): no DETACHED_PROCESS creation flag anywhere in build/ or hooks/; provision's
     WINDOWS_BACKGROUND_FLAGS == CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP; every Windows Popen with
     creationflags in provision uses it; the heal hook's creationflags name CREATE_NO_WINDOW.
  B  behavioural (Windows only): a background process started with provision's flags, and one started
     with the flags the heal hook really passes (captured through a fake Popen), each run a child the
     plain way provision runs its probes; the child must SHARE its parent's (hidden) console - no new
     console, so no window. `DEEPMAP_GATE_WINDOW_TEETH=1` adds the control: DETACHED_PROCESS must give
     the child a NEW console (that run flashes one window, which is why it is opt-in).
  Auto-sync (the heal hook also starts DeepMap's background refresh, `deepmap_cli.py sync <root>
     --unattended=session-start`): its flags are the heal's plus BELOW_NORMAL_PRIORITY_CLASS (0x08004200), never
     the 0x8 bit, built in a dict of their own (`rkw`) so `kw['creationflags']` is still assigned once; captured
     from the REAL hook (a temp plugin whose build/ answers the precheck "yes") and probed like the heal's.

Usage: python tests/no_console_window_gates.py [--root <tree>]   (default: this repository)
"""
from __future__ import annotations

import ast
import importlib.util
import io
import json
import os
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
PASSES: list[str] = []
FAILS: list[str] = []

CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200
DETACHED_PROCESS = 0x00000008
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSES if ok else FAILS).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok or not detail else f" — {detail}"))


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(mod)
    finally:
        try:
            sys.path.remove(str(path.parent))
        except ValueError:
            pass
    return mod


def _code_lines(path: Path) -> list[tuple[int, str]]:
    out = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        code = line.split("#", 1)[0]
        if code.strip():
            out.append((i, code))
    return out


# =========================================================================== A: static
def gate_a_static(root: Path) -> None:
    print("\n-- A: no DETACHED_PROCESS; one hidden-console flag set for every background process --")
    files = sorted((root / "build").glob("*.py")) + sorted((root / "hooks").glob("*.py"))
    hits = [f"{p.relative_to(root).as_posix()}:{i}" for p in files for i, code in _code_lines(p)
            if "DETACHED_PROCESS" in code or "0x00000008" in code.lower()]
    check("no DETACHED_PROCESS creation flag in build/ or hooks/ (it makes every child console "
          "program open a visible window)", not hits, ", ".join(hits))

    prov_path = root / "build" / "provision.py"
    try:
        P = _load(prov_path, "_prov_under_test")
    except Exception as e:  # noqa: BLE001
        check("build/provision.py imports", False, repr(e))
        return
    flags = getattr(P, "WINDOWS_BACKGROUND_FLAGS", None)
    check("provision.WINDOWS_BACKGROUND_FLAGS == CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP",
          flags == CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP, repr(flags))

    tree = ast.parse(prov_path.read_text(encoding="utf-8"))
    popens = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "Popen":
            for kw in node.keywords:
                if kw.arg == "creationflags":
                    popens.append((node.lineno, ast.unparse(kw.value)))
    check("provision starts at least two Windows background processes (Neo4j, MCP servers) with "
          "explicit creationflags", len(popens) >= 2, repr(popens))
    bad = [f"line {ln}: {src}" for ln, src in popens if src != "WINDOWS_BACKGROUND_FLAGS"]
    check("every provision Popen with creationflags uses WINDOWS_BACKGROUND_FLAGS", not bad, "; ".join(bad))

    hook = root / "hooks" / "deepmap_sessionstart_heal.py"
    src = hook.read_text(encoding="utf-8")
    cf = [ast.unparse(n.value) for n in ast.walk(ast.parse(src))
          if isinstance(n, ast.Assign) and any(isinstance(t, ast.Subscript) and
                                               ast.unparse(t) == "kw['creationflags']" for t in n.targets)]
    check("the heal hook sets creationflags once", len(cf) == 1, repr(cf))
    check("the heal hook's creationflags name CREATE_NO_WINDOW (and CREATE_NEW_PROCESS_GROUP), not "
          "DETACHED_PROCESS", bool(cf) and "CREATE_NO_WINDOW" in cf[0]
          and "CREATE_NEW_PROCESS_GROUP" in cf[0] and "DETACHED" not in cf[0], repr(cf))
    # auto-sync: the background refresh's flags — the heal's, plus BELOW_NORMAL, in a dict of their own
    rcf = [ast.unparse(n.value) for n in ast.walk(ast.parse(src))
           if isinstance(n, ast.Assign) and any(isinstance(t, ast.Subscript) and
                                                ast.unparse(t) == "rkw['creationflags']" for t in n.targets)]
    check("the background refresh's creationflags are set once, from the heal's: "
          "rkw['creationflags'] = kw['creationflags'] | BELOW_NORMAL_PRIORITY_CLASS",
          rcf == ["kw['creationflags'] | BELOW_NORMAL_PRIORITY_CLASS"], repr(rcf))
    try:
        HK = _load(hook, "_heal_hook_consts")
        bn = getattr(HK, "BELOW_NORMAL_PRIORITY_CLASS", None)
    except Exception as e:  # noqa: BLE001
        bn = repr(e)
    check("the heal hook's BELOW_NORMAL_PRIORITY_CLASS == 0x00004000", bn == BELOW_NORMAL_PRIORITY_CLASS, repr(bn))


# =========================================================================== B: behavioural (Windows)
_GRANDCHILD = (
    "import ctypes, os\n"
    "k = ctypes.windll.kernel32\n"
    "arr = (ctypes.c_uint32 * 64)()\n"
    "n = k.GetConsoleProcessList(arr, 64)\n"
    "print(os.getppid(), n, ' '.join(str(p) for p in arr[:n]))\n"
)
_CHILD = (
    "import subprocess, sys, json\n"
    "out, code = sys.argv[1], sys.argv[2]\n"
    "r = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=60)\n"
    "open(out, 'w', encoding='utf-8').write(r.stdout)\n"
)


def _probe(flags: int, td: Path, tag: str) -> tuple[bool, str]:
    """Start a background process with `flags` (as provision/the hook do: stdio to files/DEVNULL); it runs
    a child the plain way (subprocess.run, no flags). True iff the child shares its parent's console."""
    out = td / f"{tag}.txt"
    log = open(td / f"{tag}.log", "wb")
    try:
        proc = subprocess.Popen([sys.executable, "-c", _CHILD, str(out), _GRANDCHILD],
                                stdout=log, stderr=log, stdin=subprocess.DEVNULL, creationflags=flags,
                                close_fds=True)
    finally:
        log.close()
    # Reap the child before returning (0.28.2): it still holds <tag>.log for a moment after writing its output, and
    # the caller's TemporaryDirectory cleanup then failed with WinError 32 (~1 run in 25 measured). Through the Popen
    # handle only — never a kill by a recorded pid (PID reuse).
    try:
        proc.wait(timeout=60)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)
    try:
        ppid, n, pids = out.read_text(encoding="utf-8").split(" ", 2)
        shared = int(n) >= 2 and ppid in pids.split()
        return shared, f"child's parent {ppid}; child's console holds {n} process(es): {pids.strip()}"
    except Exception as e:  # noqa: BLE001
        return False, f"no probe output ({e!r}); log: {(td / f'{tag}.log').read_text(errors='replace')[-300:]}"


def _hook_flags(root: Path) -> int | None:
    """The creationflags the REAL heal hook passes (its Popen replaced by a recorder)."""
    HK = _load(root / "hooks" / "deepmap_sessionstart_heal.py", "_heal_hook_under_test")
    spawned: list = []

    class FakePopen:
        def __init__(self, argv, **k):
            spawned.append((argv, k))

    saved_popen, saved_stdin = HK.subprocess.Popen, HK.sys.stdin
    saved_root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    saved_off = {k: os.environ.pop(k, None) for k in ("DEEPMAP_HOOKS", "DEEPMAP_HEAL")}   # an inherited opt-out
    try:
        HK.subprocess.Popen = FakePopen
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            dm = Path(td) / ".deepmap"
            dm.mkdir()
            (dm / "graph_build.json").write_text(json.dumps({"nodes": 5, "verdict": "READY"}), encoding="utf-8")
            os.environ["CLAUDE_PLUGIN_ROOT"] = str(root)
            HK.sys.stdin = io.StringIO(json.dumps({"cwd": td, "session_id": "no-console-gate"}))
            HK.main()
    finally:
        HK.subprocess.Popen, HK.sys.stdin = saved_popen, saved_stdin
        if saved_root is None:
            os.environ.pop("CLAUDE_PLUGIN_ROOT", None)
        else:
            os.environ["CLAUDE_PLUGIN_ROOT"] = saved_root
        for k, v in saved_off.items():
            if v is not None:
                os.environ[k] = v
    return spawned[0][1].get("creationflags") if spawned else None


_STUB_AUTO_SYNC = ("# no_console_window_gates stub: auto-sync is on and its precheck passes (this gate tests flags only)\n"
                   "def enabled(root, trigger='session-start', env=None):\n    return (True, 'env')\n\n\n"
                   "def precheck(root, **kw):\n"
                   "    return {'ok': True, 'code': None, 'took_s': None, 'enabled_source': 'env'}\n")
# The stub CLI never runs (Popen is a recorder) but carries the sync CLI contract the heal hook reads before it starts the
# background refresh (deepmap_hook_state.cli_takes_unattended: `sync` takes --unattended / --session -> auto_sync.run).
_STUB_CLI = ("raise SystemExit(0)\n\n\n"
             "def _contract(p, _load, args):   # no_console_window_gates stub: the --unattended contract, as text\n"
             "    p.add_argument('--unattended', choices=('session-start',), default=None)\n"
             "    p.add_argument('--session', default=None)\n"
             "    return _load('auto_sync').run(args.target, trigger=args.unattended, session=args.session)\n")


def _hook_runner_flags(root: Path):
    """(heal flags, background-refresh flags) the REAL heal hook passes when it starts both — its hooks/ copied into a
    temp plugin whose build/ holds a stub auto_sync (precheck: yes), stub provision.py / deepmap_cli.py (the CLI stub
    carries the --unattended contract text) and the real port_registry; Popen replaced by a recorder (nothing starts).
    None for a process it did not start."""
    names = ("deepmap_hook_state", "auto_sync", "sync_status", "port_registry", "_heal_hook_runner")
    keys = ("CLAUDE_PLUGIN_ROOT", "DEEPMAP_AUTO_SYNC", "DEEPMAP_HOME", "CLAUDE_CODE_SESSION_ATTENDED", "DEEPMAP_HEAL",
            "DEEPMAP_HOOKS")
    saved_mods = {n: sys.modules.get(n) for n in names}
    saved_env = {k: os.environ.get(k) for k in keys}
    saved_path = list(sys.path)
    saved_popen, saved_stdin = subprocess.Popen, sys.stdin
    spawned: list = []

    class FakePopen:
        def __init__(self, argv, **k):
            spawned.append((list(argv), k))
            self.args, self.pid = argv, os.getpid()

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        plugin = Path(td) / "plugin"
        (plugin / "hooks").mkdir(parents=True)
        (plugin / "build").mkdir()
        for f in (root / "hooks").glob("*.py"):
            shutil.copyfile(f, plugin / "hooks" / f.name)
        if (root / "build" / "port_registry.py").is_file():
            shutil.copyfile(root / "build" / "port_registry.py", plugin / "build" / "port_registry.py")
        (plugin / "build" / "auto_sync.py").write_text(_STUB_AUTO_SYNC, encoding="utf-8")
        (plugin / "build" / "provision.py").write_text("raise SystemExit(0)\n", encoding="utf-8")
        (plugin / "build" / "deepmap_cli.py").write_text(_STUB_CLI, encoding="utf-8")
        proj = Path(td) / "proj"
        (proj / ".deepmap").mkdir(parents=True)
        (proj / ".deepmap" / "graph_build.json").write_text(json.dumps({"nodes": 5, "verdict": "READY"}),
                                                            encoding="utf-8")
        try:
            for n in names:
                sys.modules.pop(n, None)
            for k in ("DEEPMAP_HEAL", "DEEPMAP_HOOKS"):
                os.environ.pop(k, None)
            os.environ.update({"CLAUDE_PLUGIN_ROOT": str(plugin), "DEEPMAP_AUTO_SYNC": "1",
                               "DEEPMAP_HOME": str(Path(td) / "home"), "CLAUDE_CODE_SESSION_ATTENDED": "1"})
            HK = _load(plugin / "hooks" / "deepmap_sessionstart_heal.py", "_heal_hook_runner")
            subprocess.Popen = FakePopen
            sys.stdin = io.StringIO(json.dumps({"cwd": str(proj), "session_id": "no-console-runner",
                                                "source": "startup"}))
            HK.main()
        finally:
            subprocess.Popen, sys.stdin = saved_popen, saved_stdin
            sys.path[:] = saved_path
            for n, m in saved_mods.items():
                if m is None:
                    sys.modules.pop(n, None)
                else:
                    sys.modules[n] = m
            for k, v in saved_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
    heal = next((k.get("creationflags", k.get("start_new_session")) for a, k in spawned
                 if len(a) > 2 and a[2] == "heal"), None)
    runner = next((k.get("creationflags", k.get("start_new_session")) for a, k in spawned
                   if "--unattended=session-start" in a), None)
    return heal, runner


def gate_c_auto_sync_runner(root: Path) -> None:
    print("\n-- C: the background refresh the heal hook starts: hidden console + BELOW_NORMAL, never DETACHED --")
    heal, runner = _hook_runner_flags(root)
    if os.name != "nt":
        check("POSIX: the heal hook starts the background refresh in a new session (start_new_session)",
              runner is True, repr(runner))
        return
    check("the heal hook starts the background refresh with creationflags 0x08004200 (CREATE_NO_WINDOW | "
          "CREATE_NEW_PROCESS_GROUP | BELOW_NORMAL_PRIORITY_CLASS)",
          runner == CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP | BELOW_NORMAL_PRIORITY_CLASS, repr(runner))
    check("...never the DETACHED_PROCESS bit (0x8)", isinstance(runner, int) and not runner & DETACHED_PROCESS,
          repr(runner))
    check("...while the heal itself keeps CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP (no BELOW_NORMAL)",
          heal == CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP, repr(heal))
    if isinstance(runner, int):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tds:
            ok, why = _probe(runner, Path(tds), "runner")
            check("the background refresh (the hook's real flags): a program it starts (git, the engine) shares its "
                  "hidden console", ok, why)


def gate_b_behaviour(root: Path) -> None:
    print("\n-- B: a background process's child programs share its hidden console (no new window) --")
    if os.name != "nt":
        check("behavioural console check (Windows only) - skipped on this OS", True)
        return
    try:
        P = _load(root / "build" / "provision.py", "_prov_under_test_b")
        prov_flags = getattr(P, "WINDOWS_BACKGROUND_FLAGS", None)
    except Exception as e:  # noqa: BLE001
        prov_flags = None
        check("build/provision.py imports", False, repr(e))
    hook_flags = _hook_flags(root)
    check("the heal hook passes creationflags on Windows", isinstance(hook_flags, int), repr(hook_flags))
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tds:
        td = Path(tds)
        if isinstance(prov_flags, int):
            ok, why = _probe(prov_flags, td, "provision")
            check("Neo4j / MCP servers (provision.WINDOWS_BACKGROUND_FLAGS): a program they start shares "
                  "their hidden console", ok, why)
        if isinstance(hook_flags, int):
            ok, why = _probe(hook_flags, td, "heal")
            check("the SessionStart heal (the hook's real flags): a program it starts (git / tasklist / "
                  "powershell) shares its hidden console", ok, why)
        if os.environ.get("DEEPMAP_GATE_WINDOW_TEETH") == "1":
            ok, why = _probe(DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP, td, "detached")
            check("control: under DETACHED_PROCESS the child gets a NEW console (the probe can tell)",
                  not ok, why)


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    root = REPO
    if "--root" in sys.argv:
        root = Path(sys.argv[sys.argv.index("--root") + 1]).resolve()
    print("=" * 72)
    print(f"DeepMap — no console windows from background processes (root: {root})")
    print("=" * 72)
    gate_a_static(root)
    gate_b_behaviour(root)
    gate_c_auto_sync_runner(root)
    total = len(PASSES) + len(FAILS)
    print("\n" + "=" * 72)
    if FAILS:
        print(f"RESULT: RED — {len(PASSES)}/{total} gates passed.")
        return 1
    print(f"RESULT: GREEN — {len(PASSES)}/{total} gates passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
