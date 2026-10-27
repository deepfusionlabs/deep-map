#!/usr/bin/env python3
"""0.28.2 — a Windows `stop` whose Neo4j tree is gone must not report "stop FAILED"; a survivor still must.

Review finding (0.28.1 review, measured ~8% of stops): `_terminate_pid(<cmd.exe launcher of Neo4j>, tree=True)` runs
`taskkill /F /T /PID <cmd>`. taskkill kills the children first; `cmd.exe /c neo4j.bat console` then ends on its own, so
the final kill of a process that has just exited fails and taskkill exits non-zero (255 for one error, 128 for
several) although the tree is gone. The reason text depends on whether anything still holds the exited process:
"There is no running instance of the task." (something does) or "The operation attempted is not supported." (nothing
does) — both captured live (a skeptic's 1,403 kills; ERROR/Reason lines always on stderr, SUCCESS lines on stdout).
The old code returned `rc == 0` -> "stop FAILED", the pid file kept, instance-rm skipping its wait, hibernate not
counting the instance, `mcp-stop` exiting non-zero. The rule now is LIVENESS, not reason text: stopped iff every ERROR
line is "could not be terminated" and every process it names, and the pid, is gone. A survivor (Access denied, ...),
"not found" (the pid was already gone — its children may live on), an unreadable report (a non-English Windows) and a
silent non-zero exit all stay failures; taskkill is never re-run on the pid (PID reuse).

Hermetic on every OS: provision's is_windows / subprocess.run / _pid_alive are replaced by fakes; the liveness fake
answers PER PID, so checking the wrong pid is caught.
Usage: python tests/stop_windows_r282_gates.py [--root <tree>]
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PASSES: list[str] = []
FAILS: list[str] = []

ROOT_PID, CHILD, OTHER, PARENT = 4100, 68912, 4300, 900
KILLED_KIDS = ("SUCCESS: The process with PID 4242 (child process of PID 68912) has been terminated.\r\n"
               "SUCCESS: The process with PID 4243 (child process of PID 4100) has been terminated.\r\n")


def _err(pid: int, parent: int, reason: str) -> str:
    return (f"ERROR: The process with PID {pid} (child process of PID {parent}) could not be terminated.\r\n"
            f"Reason: {reason}\r\n")


NRI = "There is no running instance of the task."
NS = "The operation attempted is not supported."
DENIED = "Access is denied."
STILL_RUNNING = "One or more child processes of this process were still running."


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSES if ok else FAILS).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok or not detail else f" — {detail}"))


def _load(root: Path):
    path = root / "build" / "provision.py"
    spec = importlib.util.spec_from_file_location("_prov_stop_r282", path)
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path.remove(str(path.parent))
    return mod


def _run(P, rc: int, *, stdout: str = "", stderr: str = "", alive=()) -> tuple[bool, list, list]:
    """Run the Windows branch with taskkill answering (rc, stdout, stderr) and exactly the pids in `alive` alive."""
    calls: list = []
    probed: list = []

    def fake_run(argv, **kw):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, rc, stdout, stderr)

    def fake_alive(pid):
        probed.append(pid)
        return pid in alive

    saved = (P.is_windows, P.subprocess.run, P._pid_alive)
    try:
        P.is_windows = lambda: True
        P.subprocess.run = fake_run
        P._pid_alive = fake_alive
        return P._terminate_pid(ROOT_PID, tree=True), calls, probed
    finally:
        P.is_windows, P.subprocess.run, P._pid_alive = saved


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
    print(f"DeepMap — Windows stop: a tree that ended on its own is stopped; a survivor is not (root: {root})")
    print("=" * 72)
    P = _load(root)

    print("\n-- success paths --")
    ok, calls, _ = _run(P, 0, stdout="SUCCESS: The process with PID 4100 (child process of PID 900) has been terminated.\r\n")
    check("rc 0 -> stopped", ok is True)
    check("a tree stop runs exactly one `taskkill /F /T /PID <pid>`",
          calls == [["taskkill", "/F", "/T", "/PID", str(ROOT_PID)]], repr(calls))

    ok, calls, probed = _run(P, 255, stdout=KILLED_KIDS, stderr=_err(ROOT_PID, PARENT, NRI))
    check("the race, reason 'no running instance of the task', everything gone -> stopped (the false FAILED)", ok is True)
    check("... taskkill is not re-run on the pid (it may name another process by then)", len(calls) == 1, repr(calls))
    check("... the pid itself was probed", ROOT_PID in probed, repr(probed))

    ok, _, _ = _run(P, 255, stdout=KILLED_KIDS, stderr=_err(ROOT_PID, PARENT, NS))
    check("the race, reason 'The operation attempted is not supported.' (nothing held the exited process), "
          "everything gone -> stopped", ok is True)

    ok, _, probed = _run(P, 128, stdout=KILLED_KIDS, stderr=_err(CHILD, ROOT_PID, NS) + _err(ROOT_PID, PARENT, NRI))
    check("rc 128, two ERROR lines with mixed reasons, every named process and the pid gone -> stopped", ok is True)
    check("... every process an ERROR line names was probed", {CHILD, ROOT_PID} <= set(probed), repr(probed))

    ok, _, _ = _run(P, 255, stdout=KILLED_KIDS, stderr=_err(CHILD, ROOT_PID, DENIED))
    check("'Access is denied' for a child that has exited by the time it is checked -> stopped (nothing survived)",
          ok is True)

    print("\n-- failure paths --")
    ok, _, _ = _run(P, 255, stdout=KILLED_KIDS, stderr=_err(ROOT_PID, PARENT, NRI), alive={ROOT_PID})
    check("the same race output but the pid still alive -> NOT stopped", ok is False)
    ok, _, _ = _run(P, 255, stdout=KILLED_KIDS, stderr=_err(CHILD, ROOT_PID, NS), alive={ROOT_PID})
    check("a child's race line, but the pid itself (named in no ERROR line) still alive -> NOT stopped", ok is False)
    ok, _, _ = _run(P, 255, stdout=KILLED_KIDS, stderr=_err(CHILD, ROOT_PID, DENIED), alive={CHILD})
    check("'Access is denied' and that child still alive -> NOT stopped (a survivor)", ok is False)
    ok, _, _ = _run(P, 128, stderr=_err(OTHER, ROOT_PID, DENIED) + _err(ROOT_PID, PARENT, NRI), alive={OTHER})
    check("mixed: the pid gone but another named process alive -> NOT stopped", ok is False)
    ok, _, _ = _run(P, 255, stderr=_err(ROOT_PID, PARENT, STILL_RUNNING), alive={ROOT_PID})
    check("'One or more child processes ... were still running' with the pid alive -> NOT stopped", ok is False)
    ok, _, _ = _run(P, 128, stderr='ERROR: The process "4100" not found.\r\n')
    check("'not found' (the pid was already gone; its children may live on, orphaned) -> NOT stopped", ok is False)
    ok, _, _ = _run(P, 128, stderr=_err(CHILD, ROOT_PID, NS) + 'ERROR: The process "4100" not found.\r\n')
    check("a readable ERROR line next to a 'not found' one -> NOT stopped (every ERROR line must be readable)",
          ok is False)
    ok, _, _ = _run(P, 255, stderr="FEHLER: Der Prozess mit PID 4100 (untergeordneter Prozess von PID 900) konnte "
                                   "nicht beendet werden.\r\nUrsache: Es ist keine Instanz der Aufgabe aktiv.\r\n")
    check("output it cannot read (a non-English Windows) -> NOT stopped (the old, safe answer)", ok is False)
    ok, _, _ = _run(P, 1)
    check("a non-zero exit with no output -> NOT stopped", ok is False)

    total = len(PASSES) + len(FAILS)
    print("\n" + "=" * 72)
    if FAILS:
        print(f"RESULT: RED — {len(PASSES)}/{total} gates passed.")
        return 1
    print(f"RESULT: GREEN — {len(PASSES)}/{total} gates passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
