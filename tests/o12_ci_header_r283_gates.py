#!/usr/bin/env python3
"""Observation D20 — the CI workflow's comments say what the job really runs.

.github/workflows/validate.yml's header said the job runs "py_compile + the STAGE-0 in-session fusion gates + the
copy-honesty scanner selftest + the benchmark harness dry-run". Its only test step is `python tests/run_all.py` — the
whole no-Neo4j suite: every registered tests/*_gates.py suite, the installer syntax checks, the real-copy honesty scan.
A step comment also called the install.ps1 PowerShell parse check "Windows-only", but run_all runs it wherever `pwsh` /
`powershell` is on PATH, and GitHub's ubuntu-latest image ships pwsh, so it runs in CI. The fix is comments only; the
workflow's steps are unchanged.

Each claim is checked against the file it describes, so a later change to either side re-opens the gap loudly:
  * the job's test step runs `python tests/run_all.py`; the header names it and says every registered
    tests/*_gates.py suite runs, and gives no suite COUNT (a count re-drifts; run_all's docstring is count-free);
  * run_all gates the PowerShell parse check on `pwsh` / `powershell` being on PATH -> the comments name pwsh and no
    comment sentence about install.ps1 / PowerShell calls it Windows-only;
  * run_all gates `claude plugin validate` on the claude CLI -> the comments say those checks skip without it;
  * run_all never runs the live-Neo4j operator harness the header says is not run, and when it registers the opt-in
    live isolation suite the header says that suite skips without DEEPMAP_LIVE_TEST=1.

Pure text checks: no network, no Neo4j, nothing executed.
Usage: python tests/o12_ci_header_r283_gates.py [--root <tree>]
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WORKFLOW = Path(".github") / "workflows" / "validate.yml"
RUN_ALL = Path("tests") / "run_all.py"
PASSES: list[str] = []
FAILS: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSES if ok else FAILS).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok or not detail else f" — {str(detail)[:500]}"))


def comments(yml: str, header_only: bool = False) -> str:
    """The workflow's comment text, joined into one line of prose. header_only: only the comments above `on:`."""
    out = []
    for line in yml.splitlines():
        if header_only and re.match(r"on:\s*$", line):
            break
        stripped = line.strip()
        if stripped.startswith("#"):
            out.append(stripped.lstrip("#").strip())
    return " ".join(out)


def sentences(text: str) -> "list[str]":
    return [s for s in re.split(r"(?<=[.;])\s+", text) if s.strip()]


def run_steps(yml: str) -> "list[str]":
    return [m.group(1) for m in re.finditer(r"(?m)^\s*(?:-\s*)?run:\s*(.+?)\s*$", yml)]


def problems(yml: str, run_all: str) -> "list[str]":
    """Pure: every claim in the workflow's comments that the workflow's steps or tests/run_all.py contradict."""
    probs: list = []
    header = comments(yml, header_only=True)
    every = comments(yml)
    if not any(re.search(r"\bpython3?\s+tests/run_all\.py\b", cmd) for cmd in run_steps(yml)):
        probs.append("steps: no step runs `python tests/run_all.py` (the checks below assume it)")
    if "tests/run_all.py" not in header:
        probs.append("header: does not name tests/run_all.py, the one test step the job runs")
    if "_gates.py" not in header:
        probs.append("header: does not say the registered tests/*_gates.py suites run (it understates the scope)")
    count = re.search(r"(?<![\w.])~?\d{2,4}\+?\s+(?:\S+\s+){0,3}?(?:suites?|gates?|checks?)\b", header, re.I)
    if count:
        probs.append(f"header: states a suite count ({count.group(0)!r}) that re-drifts; keep it count-free")
    ps_on_path = re.search(r"which\(\s*[\"']pwsh[\"']\s*\)", run_all) is not None
    if ps_on_path:
        bad = [s for s in sentences(every)
               if re.search(r"install\.ps1|powershell", s, re.I) and re.search(r"windows[- ]only", s, re.I)]
        if bad:
            probs.append(f"comments: call the install.ps1 PowerShell parse check Windows-only, but run_all runs it "
                         f"wherever pwsh / powershell is on PATH: {bad[0][:200]!r}")
        if not re.search(r"\bpwsh\b", every):
            probs.append("comments: never say the PowerShell parse check runs where pwsh is on PATH (it runs in CI)")
    if re.search(r"which\(\s*[\"']claude[\"']\s*\)", run_all) and "claude plugin validate" in every:
        if not any("claude plugin validate" in s and re.search(r"\bskip\w*\b|\bonly where\b", s, re.I)
                   for s in sentences(every)):
            probs.append("comments: mention `claude plugin validate` without saying it skips where the claude CLI "
                         "is absent (as in CI)")
    for script in ("stage0_capture_oracle", "stage0_regression_check"):
        if re.search(rf"[\"']tests/{script}\.py[\"']", run_all):
            probs.append(f"run_all: runs tests/{script}.py, which the header says CI does not run")
    if "live_per_project_isolation" in run_all and "DEEPMAP_LIVE_TEST" not in header:
        probs.append("header: run_all registers the opt-in live isolation suite; the header does not say it skips "
                     "without DEEPMAP_LIVE_TEST=1")
    return probs


def gate_current(root: Path) -> None:
    print("\n-- the workflow's comments match its steps and tests/run_all.py --")
    wf, ra = root / WORKFLOW, root / RUN_ALL
    yml = wf.read_text(encoding="utf-8") if wf.is_file() else ""
    run_all = ra.read_text(encoding="utf-8") if ra.is_file() else ""
    check("precondition: the workflow and tests/run_all.py are readable", bool(yml and run_all), f"{wf} / {ra}")
    if not (yml and run_all):
        return
    probs = problems(yml, run_all)
    for tag, label in (("steps:", "the job's test step runs `python tests/run_all.py`"),
                       ("header: does not name", "the header names tests/run_all.py as what the job runs"),
                       ("header: does not say the registered",
                        "the header says every registered tests/*_gates.py suite runs (the scope is not understated)"),
                       ("header: states a suite count", "the header gives no suite count (count-free, like run_all)"),
                       ("comments: call the install.ps1",
                        "no comment calls the install.ps1 PowerShell parse check Windows-only (run_all: pwsh on PATH)"),
                       ("comments: never say", "the comments say the PowerShell parse check runs where pwsh is on PATH"),
                       ("comments: mention `claude plugin validate`",
                        "the comments say `claude plugin validate` skips where the claude CLI is absent"),
                       ("run_all: runs", "run_all does not run the live-Neo4j operator harness the header excludes"),
                       ("header: run_all registers",
                        "the header says the opt-in live isolation suite skips without DEEPMAP_LIVE_TEST=1")):
        mine = [p for p in probs if p.startswith(tag)]
        check(label, not mine, "; ".join(mine))


def gate_teeth() -> None:
    print("\n-- teeth: the checks catch the old header, a re-inserted 'Windows-only' and a suite count --")
    run_all = ("ps_exe = shutil.which('pwsh') or shutil.which('powershell')\nclaude_exe = shutil.which(\"claude\")\n"
               'run("live", ["tests/live_per_project_isolation.py"])\n')
    old = ("name: validate\n\n"
           "# Runs the no-Neo4j validation gate (the local analog of /oss-kit:oss-validate)\n"
           "# on every push / PR: py_compile + the STAGE-0 in-session fusion gates + the\n"
           "# copy-honesty scanner selftest + the benchmark harness dry-run. The live-Neo4j\n"
           "# operator harness (stage0_capture_oracle / stage0_regression_check) is NOT run\n"
           "# here — it needs a real instance (see docs/deepmap-test-plan.md).\n\n"
           "on:\n  push:\njobs:\n  j:\n    steps:\n"
           "      # The install.ps1 PowerShell parse-check + the two `claude plugin validate`\n"
           "      # checks skip where those tools are absent (Windows-only / CLI-only).\n"
           "      - name: Run\n        run: python tests/run_all.py\n")
    got = problems(old, run_all)
    check("the pre-fix header and step comment are flagged (no run_all.py, no *_gates.py, Windows-only, no pwsh, "
          "no DEEPMAP_LIVE_TEST)",
          all(any(p.startswith(t) for p in got) for t in ("header: does not name", "header: does not say the registered",
                                                          "comments: call the install.ps1", "comments: never say",
                                                          "header: run_all registers")), "; ".join(got))
    good_header = ("# Runs the WHOLE suite, `python tests/run_all.py`: EVERY registered tests/*_gates.py suite; the\n"
                   "# install.ps1 PowerShell parse check runs wherever pwsh is on PATH; `claude plugin validate`\n"
                   "# skips where the claude CLI is absent. The live suite skips without DEEPMAP_LIVE_TEST=1.\n")
    good = old.replace(old[old.index("# Runs"):old.index("\non:")], good_header.rstrip("\n")).replace(
        "(Windows-only / CLI-only)", "(pwsh / claude CLI on PATH)")
    check("a corrected header and step comment yield no problem (the check is not vacuous both ways)",
          problems(good, run_all) == [], "; ".join(problems(good, run_all)))
    back = good.replace("runs wherever pwsh is on PATH", "is Windows-only")
    check("re-inserting 'Windows-only' into the PowerShell sentence is flagged",
          any(p.startswith("comments: call the install.ps1") for p in problems(back, run_all)))
    counted = good.replace("EVERY registered tests/*_gates.py suite", "the ~200 registered tests/*_gates.py suites")
    check("a suite count in the header is flagged", any(p.startswith("header: states a suite count")
                                                        for p in problems(counted, run_all)))
    check("a workflow whose step no longer runs tests/run_all.py is flagged",
          any(p.startswith("steps:") for p in problems(good.replace("run: python tests/run_all.py",
                                                                    "run: python tests/other.py"), run_all)))
    check("a run_all that starts running the live-Neo4j harness is flagged",
          any(p.startswith("run_all: runs") for p in problems(good, run_all + 'run("x", ["tests/stage0_regression_check.py"])\n')))


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
    print(f"DeepMap — CI workflow comments vs what the job runs (tests/run_all.py) (root: {root})")
    print("=" * 72)
    gate_current(root)
    gate_teeth()
    total = len(PASSES) + len(FAILS)
    print("\n" + "=" * 72)
    if FAILS:
        print(f"RESULT: RED — {len(PASSES)}/{total} gates passed.")
        return 1
    print(f"RESULT: GREEN — {len(PASSES)}/{total} gates passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
