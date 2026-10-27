#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Round-11 engine gate (WE, L2): from a Windows venv, a killed or timed-out sync no longer leaks graphify's
ProcessPoolExecutor workers.

The defect: _run_engine starts the engine with `sys.executable` and puts it in a KILL_ON_JOB_CLOSE job. In a
Windows venv `sys.executable` is the venv's LAUNCHER (Scripts\\python.exe): it starts the real interpreter in a
job of its own that lets that interpreter's children break away — so the engine's pool workers were outside
DeepMap's job and outlived a killed sync forever (measured with the real engine: 6 of 14 engine processes alive
10 s after the kill; 0 with a base interpreter). DeepMap runs from a venv whenever DEEPMAP_PYTHON (F4) or Claude
Code's own interpreter is one.

The fix (graphify_extract._venv_launcher_bypass, applied inside _run_engine after the test seam): when the
command starts THIS interpreter and it is a Windows venv launcher (sys._base_executable differs), start the base
interpreter directly with __PYVENV_LAUNCHER__ naming the launcher — what multiprocessing itself does for its
workers since bpo-35797 — so the child still resolves the venv but nothing sits between DeepMap's job and the
engine. POSIX, a base interpreter, another program or a missing base: unchanged.

Sections:
  A  _venv_launcher_bypass truth table (hermetic: a stand-in `sys` / `os`)
  B  _run_engine starts the bypassed command; the fake-engine seam and the returned args are unchanged
  C  REAL, Windows only (a throwaway --without-pip venv in a temp dir; skipped elsewhere or if it cannot be made):
     C1 the engine child started from the venv still resolves the venv (sys.prefix)
     C2 killing a venv-run driver kills the engine child AND every ProcessPoolExecutor worker
     C3 a timeout does the same
Only processes this gate caused to exist are ever signalled, by their recorded pids.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build"
sys.path.insert(0, str(BUILD))

import graphify_extract as GX  # noqa: E402

_PASS = _FAIL = 0
_TMP = Path(tempfile.mkdtemp(prefix="dm_r11v_"))
_BORN: dict = {}             # pid -> its creation time, taken when the gate learned the pid (0.28.2)


def _ctime(pid):
    """The creation time of process `pid` (an opaque comparable value), or None when it cannot be read (gone)."""
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.OpenProcess.restype = wintypes.HANDLE
            h = k32.OpenProcess(0x1000, False, int(pid))      # PROCESS_QUERY_LIMITED_INFORMATION
            if not h:
                return None
            try:
                t = [wintypes.FILETIME() for _ in range(4)]
                ok = k32.GetProcessTimes(wintypes.HANDLE(h), *[ctypes.byref(x) for x in t])
                return (t[0].dwHighDateTime << 32 | t[0].dwLowDateTime) if ok else None
            finally:
                k32.CloseHandle(wintypes.HANDLE(h))
        with open(f"/proc/{int(pid)}/stat", encoding="utf-8") as fh:
            return int(fh.read().rsplit(")", 1)[-1].split()[19])   # field 22: starttime
    except Exception:  # noqa: BLE001
        pass
    try:
        out = subprocess.run(["ps", "-o", "lstart=", "-p", str(int(pid))], capture_output=True, text=True,
                             timeout=10).stdout.strip()
        return out or None
    except Exception:  # noqa: BLE001
        return None


class _Owned(list):
    """Every pid this gate caused to exist (the cleanup safety net), each with its creation time recorded the moment
    the gate learns it — while it is known to be the gate's own process. kill_pid acts only on a pid that still
    carries that creation time: Windows reuses pids within seconds, and a kill by a recorded pid alone could hit an
    unrelated process of the user's (0.28.2)."""

    def append(self, pid) -> None:
        _BORN.setdefault(int(pid), _ctime(pid))
        super().append(pid)

    def extend(self, pids) -> None:
        for p in pids:
            self.append(p)


_STARTED: list = _Owned()


def check(label: str, cond: bool, detail="") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {label}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}{(' — ' + str(detail)[:600]) if detail != '' else ''}")


def alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        h = k32.OpenProcess(0x1000, False, int(pid))      # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = wintypes.DWORD()
        ok = k32.GetExitCodeProcess(wintypes.HANDLE(h), ctypes.byref(code))
        k32.CloseHandle(wintypes.HANDLE(h))
        return bool(ok) and code.value == 259               # STILL_ACTIVE
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def kill_pid(pid: int) -> None:
    """Hard-kill a process THIS gate started (by its recorded pid) — only while that pid still names the same process
    (the creation time recorded when the gate learned it); a gone, reused or never-recorded pid is left alone.
    Never raises."""
    born = _BORN.get(int(pid))
    if born is None or _ctime(pid) != born:
        return
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/PID", str(int(pid))], capture_output=True, timeout=30)
        else:
            import signal
            os.kill(int(pid), signal.SIGKILL)
    except Exception:  # noqa: BLE001
        pass


def gate_k_kill_hygiene() -> None:
    print("\n-- K: the cleanup kills a recorded pid only while it is still the same process (never a reused pid) --")
    g = globals()
    me = _ctime(os.getpid())
    check("the creation time of a live process can be read, and is stable", me is not None and _ctime(os.getpid()) == me,
          me)
    probe = _Owned()
    probe.append(os.getpid())              # a private list: the cleanup net (_STARTED) never holds this process
    check("a learned pid gets its creation time recorded", _BORN.get(os.getpid()) == me, _BORN.get(os.getpid()))
    _BORN.pop(os.getpid(), None)
    calls: list = []
    real = (subprocess.run, os.kill, g["_ctime"])
    fake = 999_991
    try:
        subprocess.run = lambda argv, **kw: calls.append(list(argv))
        os.kill = lambda pid, sig: calls.append(["kill", pid, sig])
        _BORN[fake] = 111
        g["_ctime"] = lambda pid: 222      # the pid now names a process born later: reused
        kill_pid(fake)
        check("a recorded pid now held by another process (a different creation time) is NOT killed", calls == [], calls)
        g["_ctime"] = lambda pid: None     # gone
        kill_pid(fake)
        check("a recorded pid that is gone is NOT killed", calls == [], calls)
        kill_pid(fake + 2)
        check("a pid the gate never recorded is NOT killed", calls == [], calls)
        g["_ctime"] = lambda pid: 111      # still the same process
        kill_pid(fake)
        check("a recorded pid that is still the same process IS killed", len(calls) == 1, calls)
    finally:
        subprocess.run, os.kill, g["_ctime"] = real
        _BORN.pop(fake, None)


def wait_for(pred, timeout: float) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.2)
    return pred()


# ---- A ---------------------------------------------------------------------------------------------------
def _bypass_with(*, name: str, exe, base, cmd, env, isfile=None):
    """_venv_launcher_bypass(cmd, env) under a stand-in `sys` (executable / _base_executable) and `os.name`."""
    fake_sys = types.SimpleNamespace(executable=exe)
    if base is not None:
        fake_sys._base_executable = base
    fake_path = types.SimpleNamespace(normcase=os.path.normcase, abspath=os.path.abspath,
                                      isfile=isfile or os.path.isfile)
    fake_os = types.SimpleNamespace(name=name, path=fake_path, environ={"FROM_OS": "1"})
    real_sys, real_os = GX.sys, GX.os
    GX.sys, GX.os = fake_sys, fake_os
    try:
        return GX._venv_launcher_bypass(cmd, env)
    finally:
        GX.sys, GX.os = real_sys, real_os


def gate_a_truth_table() -> None:
    print("\n-- A: _venv_launcher_bypass — only this interpreter, only a Windows venv launcher --")
    fn = getattr(GX, "_venv_launcher_bypass", None)
    check("graphify_extract._venv_launcher_bypass exists", callable(fn))
    if not callable(fn):
        return
    base = _TMP / "base_python.exe"
    base.write_bytes(b"")
    launcher = str(_TMP / "venv" / "Scripts" / "python.exe")
    cmd = [launcher, "-m", "graphify", "update", "C:/p", "--no-cluster"]
    env = {"GRAPHIFY_OUT": "C:/o"}
    got_cmd, got_env = _bypass_with(name="nt", exe=launcher, base=str(base), cmd=cmd, env=env)
    check("Windows venv: the engine starts the BASE interpreter, every other argument unchanged",
          got_cmd == [str(base), *cmd[1:]], got_cmd)
    check("...with __PYVENV_LAUNCHER__ naming the venv's launcher (the child still resolves the venv)",
          got_env.get("__PYVENV_LAUNCHER__") == launcher and got_env.get("GRAPHIFY_OUT") == "C:/o", got_env)
    check("...and the caller's env / command are not mutated", env == {"GRAPHIFY_OUT": "C:/o"}
          and cmd[0] == launcher)
    _c, e2 = _bypass_with(name="nt", exe=launcher, base=str(base), cmd=cmd, env=None)
    check("env None -> built from os.environ (as Popen would inherit it)",
          e2.get("FROM_OS") == "1" and e2.get("__PYVENV_LAUNCHER__") == launcher, e2)
    same = [(label, _bypass_with(**kw)) for label, kw in (
        ("a base interpreter (base == executable)", dict(name="nt", exe=str(base), base=str(base),
                                                         cmd=[str(base), "-m", "graphify"], env=env)),
        ("POSIX (a venv python is a symlink, not a launcher)", dict(name="posix", exe=launcher, base=str(base),
                                                                   cmd=cmd, env=env)),
        ("another program (cmd[0] is not this interpreter)", dict(name="nt", exe=launcher, base=str(base),
                                                                  cmd=["git", "status"], env=env)),
        ("a base path that does not exist", dict(name="nt", exe=launcher, base=str(_TMP / "missing.exe"),
                                                 cmd=cmd, env=env)),
        ("no sys._base_executable at all", dict(name="nt", exe=launcher, base=None, cmd=cmd, env=env)),
        ("an empty command", dict(name="nt", exe=launcher, base=str(base), cmd=[], env=env)),
    )]
    for label, (c, e) in same:
        kw_cmd = {"a base interpreter (base == executable)": [str(base), "-m", "graphify"],
                  "another program (cmd[0] is not this interpreter)": ["git", "status"], "an empty command": []}
        want = kw_cmd.get(label, cmd)
        check(f"unchanged for {label}", c == want and e is env, (c, e))
    boom = _bypass_with(name="nt", exe=launcher, base=str(base), cmd=cmd, env=env,
                        isfile=lambda p: (_ for _ in ()).throw(OSError("x")))
    check("never raises (an OSError inside -> the launch as it always was)", boom == (cmd, env), boom)
    if os.name == "nt":
        c3, _e3 = _bypass_with(name="nt", exe=launcher, base=str(base), cmd=[launcher.upper(), "-m", "graphify"],
                               env=env)
        check("Windows: the interpreter path is compared case-insensitively", c3[0] == str(base), c3)


# ---- B ---------------------------------------------------------------------------------------------------
def gate_b_wiring() -> None:
    print("\n-- B: _run_engine starts the bypassed command (after the fake-engine seam) --")
    if not callable(getattr(GX, "_venv_launcher_bypass", None)):
        check("_venv_launcher_bypass exists", False)
        return
    seen: dict = {}

    class _FakeProc:
        pid = 999999999
        returncode = 0

        def communicate(self, timeout=None):
            return "o", "e"

        def kill(self):
            pass

    def fake_popen(args, **kw):
        seen.update(args=list(args), env=kw.get("env"))
        return _FakeProc()

    real_bypass, real_popen, real_wd = GX._venv_launcher_bypass, GX.subprocess.Popen, GX._posix_watchdog
    GX._venv_launcher_bypass = lambda cmd, env: (["BASE", *cmd[1:]], dict(env or {}, MARK="1"))
    GX.subprocess.Popen = fake_popen
    GX._posix_watchdog = lambda pgid: None          # never guard a fake pid
    try:
        res = GX._run_engine(["EXE", "-m", "graphify"], cwd=str(_TMP), env={"A": "1"}, timeout=5)
    finally:
        GX._venv_launcher_bypass, GX.subprocess.Popen, GX._posix_watchdog = real_bypass, real_popen, real_wd
    check("Popen gets the bypassed command and env", seen.get("args") == ["BASE", "-m", "graphify"]
          and (seen.get("env") or {}).get("MARK") == "1", seen)
    check("the result's args are the command as given (messages unchanged)",
          getattr(res, "args", None) == ["EXE", "-m", "graphify"] and res.stdout == "o", getattr(res, "args", None))
    got = {}

    def fake_run(cmd, **kw):
        got.update(cmd=list(cmd))
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    real_run = subprocess.run
    GX._venv_launcher_bypass = lambda cmd, env: (["BASE"], env)
    subprocess.run = fake_run
    try:
        GX._run_engine(["EXE", "x"], cwd="c", env={}, timeout=1)
    finally:
        subprocess.run = real_run
        GX._venv_launcher_bypass = real_bypass
    check("the hermetic gates' seam (a replaced subprocess.run) still sees the command exactly as given",
          got.get("cmd") == ["EXE", "x"], got)


# ---- C ---------------------------------------------------------------------------------------------------
POOL_CHILD = r'''
import os, sys, time
from concurrent.futures import ProcessPoolExecutor
if __name__ == "__main__":
    pidfile = sys.argv[1]
    ex = ProcessPoolExecutor(6)
    list(ex.map(abs, range(60)))
    pids = [os.getpid()] + list(ex._processes.keys())
    with open(pidfile + ".tmp", "w") as fh:
        fh.write(" ".join(str(p) for p in pids))
    os.replace(pidfile + ".tmp", pidfile)
    time.sleep(120)
'''

DRIVER = r'''
import os, subprocess, sys
sys.path.insert(0, sys.argv[1])
import graphify_extract as gx
mode, child, pidfile = sys.argv[2], sys.argv[3], sys.argv[4]
if mode == "prefix":
    r = gx._run_engine([sys.executable, "-c", "import sys; print(sys.prefix)"], cwd=os.getcwd(), env=dict(os.environ),
                       timeout=120)
    print(r.stdout.strip())
elif mode == "timeout":
    try:
        gx._run_engine([sys.executable, child, pidfile], cwd=os.getcwd(), env=dict(os.environ), timeout=int(sys.argv[5]))
    except subprocess.TimeoutExpired:
        print("TIMEOUT")
else:
    gx._run_engine([sys.executable, child, pidfile], cwd=os.getcwd(), env=dict(os.environ), timeout=120)
'''


def _pids(pidfile: Path, timeout: float = 90) -> list:
    ok = wait_for(lambda: pidfile.is_file() and len(pidfile.read_text().split()) >= 2, timeout)
    pids = [int(x) for x in pidfile.read_text().split()] if ok else []
    _STARTED.extend(pids)
    return pids


def gate_c_real_venv() -> None:
    print("\n-- C: REAL — a Windows venv: the engine's pool workers die with the sync --")
    if os.name != "nt":
        print("  [SKIP] POSIX: a venv python is not a launcher (the process-group watchdog covers it; engine_child_r7)")
        return
    venv = _TMP / "venv"
    try:
        r = subprocess.run([sys.executable, "-m", "venv", "--without-pip", "--system-site-packages", str(venv)],
                           capture_output=True, text=True, timeout=300)
    except Exception as e:  # noqa: BLE001
        print(f"  [SKIP] a throwaway venv could not be created ({type(e).__name__}: {e})")
        return
    vpy = venv / "Scripts" / "python.exe"
    if r.returncode != 0 or not vpy.is_file():
        print(f"  [SKIP] a throwaway venv could not be created (rc={r.returncode}): {(r.stderr or '')[-300:]}")
        return
    probe = subprocess.run([str(vpy), "-c", "import sys; print(sys._base_executable != sys.executable)"],
                           capture_output=True, text=True, timeout=120)
    if probe.stdout.strip() != "True":
        print("  [SKIP] this Python's venv interpreter is not a launcher — nothing to bypass here")
        return
    (_TMP / "pool_child.py").write_text(POOL_CHILD, encoding="utf-8")
    (_TMP / "driver.py").write_text(DRIVER, encoding="utf-8")
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    # C1: the child still resolves the venv
    r1 = subprocess.run([str(vpy), str(_TMP / "driver.py"), str(BUILD), "prefix", "-", "-"], cwd=str(_TMP),
                        capture_output=True, text=True, timeout=180, env=env)
    check("C1: the engine child started from the venv still resolves the venv (sys.prefix)",
          os.path.normcase(r1.stdout.strip()) == os.path.normcase(str(venv)), (r1.stdout, r1.stderr[-300:]))
    # C2: kill the venv-run driver while the pool is up
    pidfile = _TMP / "pids_kill.txt"
    drv = subprocess.Popen([str(vpy), str(_TMP / "driver.py"), str(BUILD), "kill", str(_TMP / "pool_child.py"),
                            str(pidfile)], cwd=str(_TMP), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, env=env)
    _STARTED.append(drv.pid)
    pids = _pids(pidfile)
    check("C2: the engine child and its pool workers started", len(pids) >= 3 and all(alive(p) for p in pids), pids)
    drv.kill()
    drv.wait(timeout=30)
    gone = wait_for(lambda: not any(alive(p) for p in pids), 15)
    check("C2: after the venv-run sync was killed the engine child AND every pool worker are gone", gone,
          [p for p in pids if alive(p)])
    # C3: the timeout path
    pidfile3 = _TMP / "pids_timeout.txt"
    t0 = time.time()
    drv3 = subprocess.Popen([str(vpy), str(_TMP / "driver.py"), str(BUILD), "timeout", str(_TMP / "pool_child.py"),
                             str(pidfile3), "20"], cwd=str(_TMP), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, env=env, text=True)
    _STARTED.append(drv3.pid)
    pids3 = _pids(pidfile3)
    out3, _e = drv3.communicate(timeout=180)
    check("C3: the engine is stopped by its time budget (TimeoutExpired)", "TIMEOUT" in (out3 or ""), out3)
    check("C3: ...and the engine child AND every pool worker are gone",
          len(pids3) >= 3 and wait_for(lambda: not any(alive(p) for p in pids3), 15),
          (pids3, [p for p in pids3 if alive(p)], round(time.time() - t0, 1)))


def main() -> int:
    print("engine_venv_r11_gates — a Windows venv's launcher never sits between the sync and the engine (L2)")
    try:
        for g in (gate_k_kill_hygiene, gate_a_truth_table, gate_b_wiring, gate_c_real_venv):
            try:
                g()
            except Exception as e:  # noqa: BLE001
                import traceback
                traceback.print_exc()
                check(f"{g.__name__} ran without raising", False, f"{type(e).__name__}: {e}")
    finally:
        for p in _STARTED:                 # safety net: only pids this gate caused to exist
            if alive(p):
                kill_pid(p)
        shutil.rmtree(_TMP, ignore_errors=True)
    total = _PASS + _FAIL
    if _FAIL:
        print(f"\nRESULT: RED — {_FAIL} of {total} checks failed.")
        return 1
    print(f"\nRESULT: GREEN — {total}/{total} checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
