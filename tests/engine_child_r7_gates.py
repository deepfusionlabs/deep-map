#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Round-7 W5 gate (T10d-10): the graphify engine child can never outlive the sync that started it.

The defect: extract_with_report ran the engine with a plain subprocess.run(..., timeout=600). Killing the
sync (kill -9, TerminateProcess, a closed terminal) left the engine — and its ProcessPoolExecutor workers —
running, still writing into the <DEEPMAP_HOME>/gout/<key> dir the next sync clears and re-reads; and a
TIMEOUT killed only the direct child, orphaning its workers.

The fix (graphify_extract._run_engine): Windows puts the child in a KILL_ON_JOB_CLOSE job object (every
process the child starts joins the job; the OS closes our handle when we die, which kills them all); POSIX
starts the child in its own session and hands a tiny watchdog the read end of a pipe only we write to —
when we die the pipe reads EOF and the watchdog kills the child's whole process group. A timeout kills the
tree, not just the child.

Hermetic: the "engine" is a small Python child that starts a grandchild (a stand-in worker) and sleeps.
Only processes this gate started are signalled, by their recorded pids.
  A  the normal path returns stdout/stderr/returncode like subprocess.run
  B  CONTROL: with plain subprocess.run, killing the parent leaves the child + worker running (the defect
     exists on this OS — proves C can see it); they are then killed by pid
  C  with _run_engine, killing the parent kills the child + worker
  D  a timeout kills the child AND its worker, and raises subprocess.TimeoutExpired
  E  extract_with_report runs the engine through _run_engine
"""
from __future__ import annotations

import inspect
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build"
sys.path.insert(0, str(BUILD))

import graphify_extract as GX  # noqa: E402

_PASS = _FAIL = 0
_TMP = Path(tempfile.mkdtemp(prefix="dm_r7c_"))
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

# The stand-in worker starts the way graphify's ProcessPoolExecutor workers do (multiprocessing's rule): with
# THIS interpreter — or, in a Windows venv, the base interpreter with __PYVENV_LAUNCHER__ (bpo-35797), never the
# venv's launcher. R11/L2: under a venv the old `Popen([sys.executable])` stand-in went through the launcher,
# which this box's packaged (Store) Python lets break out of any job — a worker the real engine never starts —
# so the gate was RED under the PLAYBOOK throwaway-venv recipe; with a base interpreter nothing changes.
CHILD = r'''
import os, subprocess, sys, time
pidfile = sys.argv[1]
exe, env = sys.executable, None
base = getattr(sys, "_base_executable", None)
if os.name == "nt" and base and os.path.normcase(os.path.abspath(base)) != os.path.normcase(os.path.abspath(exe)):
    exe, env = base, dict(os.environ, __PYVENV_LAUNCHER__=sys.executable)
worker = subprocess.Popen([exe, "-c", "import time; time.sleep(120)"], env=env)
with open(pidfile, "w") as fh:
    fh.write(f"{os.getpid()} {worker.pid}\n")
time.sleep(120)
'''

DRIVER = r'''
import os, subprocess, sys
sys.path.insert(0, sys.argv[1])
mode, child, pidfile = sys.argv[2], sys.argv[3], sys.argv[4]
cmd = [sys.executable, child, pidfile]
if mode == "plain":
    subprocess.run(cmd, capture_output=True, text=True, timeout=120)
else:
    import graphify_extract as gx
    gx._run_engine(cmd, cwd=os.getcwd(), env=dict(os.environ), timeout=120)
'''


def check(label: str, cond: bool, detail="") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {label}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}{(' — ' + str(detail)[:400]) if detail != '' else ''}")


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
    try:
        with open(f"/proc/{int(pid)}/stat", encoding="utf-8") as fh:
            return fh.read().rsplit(")", 1)[-1].split()[0] != "Z"
    except OSError:
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


def run_driver(mode: str):
    """Start the driver (which starts the child, which starts the worker); return (driver, [child, worker])."""
    child = _TMP / "child.py"
    child.write_text(CHILD, encoding="utf-8")
    driver = _TMP / "driver.py"
    driver.write_text(DRIVER, encoding="utf-8")
    pidfile = _TMP / f"pids_{mode}.txt"
    proc = subprocess.Popen([sys.executable, str(driver), str(BUILD), mode, str(child), str(pidfile)],
                            cwd=str(_TMP), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    _STARTED.append(proc.pid)
    ok = wait_for(lambda: pidfile.is_file() and len(pidfile.read_text().split()) == 2, 60)
    pids = [int(x) for x in pidfile.read_text().split()] if ok else []
    _STARTED.extend(pids)
    return proc, pids


def gate_a_normal() -> None:
    print("\n-- A: the normal path behaves like subprocess.run --")
    fn = getattr(GX, "_run_engine", None)
    check("graphify_extract._run_engine exists", callable(fn))
    if not callable(fn):
        return
    res = fn([sys.executable, "-c", "import sys; print('hé out'); print('err line', file=sys.stderr); sys.exit(3)"],
             cwd=str(_TMP), env=dict(os.environ, PYTHONUTF8="1"), timeout=60)
    check("returncode", getattr(res, "returncode", None) == 3, getattr(res, "returncode", None))
    check("stdout (UTF-8 decoded)", "hé out" in (getattr(res, "stdout", "") or ""), getattr(res, "stdout", None))
    check("stderr", "err line" in (getattr(res, "stderr", "") or ""), getattr(res, "stderr", None))


def gate_b_control() -> None:
    print("\n-- B: CONTROL — plain subprocess.run leaves an orphan when the parent is killed --")
    proc, pids = run_driver("plain")
    check("the child and its worker started", len(pids) == 2 and all(alive(p) for p in pids), pids)
    proc.kill()
    proc.wait(timeout=30)
    time.sleep(3)
    survived = [p for p in pids if alive(p)]
    check("CONTROL: after the parent was killed the child/worker are STILL running (the defect)",
          len(survived) == 2, survived)
    for p in pids:
        kill_pid(p)
    check("control processes cleaned up", wait_for(lambda: not any(alive(p) for p in pids), 20),
          [p for p in pids if alive(p)])


def gate_c_fixed() -> None:
    print("\n-- C: _run_engine — killing the parent kills the engine child and its worker --")
    if not callable(getattr(GX, "_run_engine", None)):
        check("_run_engine exists", False)
        return
    proc, pids = run_driver("fixed")
    check("the child and its worker started", len(pids) == 2 and all(alive(p) for p in pids), pids)
    proc.kill()
    proc.wait(timeout=30)
    gone = wait_for(lambda: not any(alive(p) for p in pids), 20)
    check("after the parent was killed the child AND its worker are gone", gone, [p for p in pids if alive(p)])
    for p in pids:
        kill_pid(p)


def gate_d_timeout() -> None:
    print("\n-- D: a timeout kills the whole engine tree --")
    fn = getattr(GX, "_run_engine", None)
    if not callable(fn):
        check("_run_engine exists", False)
        return
    child = _TMP / "child.py"
    child.write_text(CHILD, encoding="utf-8")
    pidfile = _TMP / "pids_timeout.txt"
    raised = False
    t0 = time.time()
    try:
        fn([sys.executable, str(child), str(pidfile)], cwd=str(_TMP), env=dict(os.environ), timeout=6)
    except subprocess.TimeoutExpired:
        raised = True
    pids = [int(x) for x in pidfile.read_text().split()] if pidfile.is_file() else []
    _STARTED.extend(pids)
    check("subprocess.TimeoutExpired is raised", raised)
    check("it returns promptly after the timeout", time.time() - t0 < 40, round(time.time() - t0, 1))
    check("the child and its worker were both killed",
          len(pids) == 2 and wait_for(lambda: not any(alive(p) for p in pids), 20), [p for p in pids if alive(p)])
    for p in pids:
        kill_pid(p)


def gate_e_wired() -> None:
    print("\n-- E: extract_with_report runs the engine through _run_engine --")
    src = inspect.getsource(GX.extract_with_report)
    check("extract_with_report calls _run_engine", "_run_engine(" in src)
    check("...and no longer calls subprocess.run for the engine", "subprocess.run(cmd" not in src)
    # the hermetic gates fake the engine by replacing subprocess.run — that seam keeps working, with the
    # exact arguments the engine was always run with
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(kw, cmd=cmd)
        return type("P", (), {"returncode": 0, "stdout": "o", "stderr": "e"})()

    real = subprocess.run
    subprocess.run = fake_run
    try:
        res = GX._run_engine(["x"], cwd="c", env={"A": "1"}, timeout=7)
    finally:
        subprocess.run = real
    check("a replaced subprocess.run (the gates' fake engine) is honoured with the historical arguments",
          seen.get("cmd") == ["x"] and seen.get("capture_output") is True and seen.get("timeout") == 7
          and seen.get("encoding") == "utf-8" and seen.get("errors") == "replace" and seen.get("cwd") == "c"
          and getattr(res, "stdout", None) == "o", seen)
    check("with the stdlib subprocess.run in place, the job-object/watchdog path is used",
          subprocess.run is GX._STDLIB_SUBPROCESS_RUN)


def main() -> int:
    print("engine_child_r7_gates — the engine child never outlives the sync (T10d-10)")
    try:
        for g in (gate_k_kill_hygiene, gate_a_normal, gate_b_control, gate_c_fixed, gate_d_timeout,
                  gate_e_wired):
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
