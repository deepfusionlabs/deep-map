#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j gates: benchmark_clone_graph_gates' driver TEETH must not depend on the git state (ROUND-11c C4).

Section F of tests/benchmark_clone_graph_gates.py proves its driver detectors have teeth by running them over
the PRE-fix driver text. Up to round 11 that text came from `git show HEAD:benchmark/<driver>`. It was only the
pre-fix text while HEAD was v0.27.4 (9c8d50bce). After the operator commits this batch, HEAD holds the FIXED
driver. Its comments still mention the old `deepmap_cli.py sync` step, so the guard passed, but the comment-
stripping detector found nothing. The gate went RED 92/94 on every committed checkout: the round-11 Linux suite
on a committed export, a simulated commit in a scratch clone, and CI's depth-1 `actions/checkout`. With no git
at all (a source tarball) the teeth silently vanished instead.

The fix keeps the teeth and makes them independent of the git state. The pre-fix driver text is a committed
FIXTURE (tests/fixtures/benchmark_pre_d1_drivers/), pinned to the exact git blob ids those drivers had in
9c8d50bce.

  A  the fixtures exist and are the genuine v0.27.4 drivers: their git blob ids (LF-normalised, so an autocrlf
     checkout still matches) equal the pinned ids, which equal `9c8d50bce:<driver>` whenever that commit is
     reachable (a visible SKIP in a depth-1 checkout); each holds a real CODE line running the shared-graph sync
  B  teeth per detector: every detector that the v0.27.4 drivers trip fires on the fixture, and none fires
     on the current fixed drivers
  C  section F reads no git state: no `git show` / `HEAD:` in gate_drivers_and_runbooks; it reads the fixture
  D  BEHAVIOURAL, the real section F run from a copy of the gate in throwaway repos, in every git state:
       D1 HEAD = the v0.27.4 drivers, the batch uncommitted on top (today's tree)
       D2 HEAD = the batch COMMITTED (the operator's commit — the state that went RED 92/94)
       D3 a depth-1 clone of D2 (CI's actions/checkout default)
       D4 no git at all (a source tarball / export)
       D5 the fixtures checked out with CRLF line endings (core.autocrlf on Windows)
     In each state section F must be fully GREEN, with both driver-teeth checks RUN and PASSED.

Hermetic: temp dirs only (created and removed by this gate), no Neo4j, no network, no claude, no spend. git is
used only inside those temp repos. The gate never writes to this repo or its git state.
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GATE = REPO / "tests" / "benchmark_clone_graph_gates.py"
FIXTURE_DIR = REPO / "tests" / "fixtures" / "benchmark_pre_d1_drivers"
PRE_D1_COMMIT = "9c8d50bce"   # v0.27.4: the last commit whose benchmark drivers synced clones into ONE shared graph
# driver -> (fixture file name, git blob id of that driver in 9c8d50bce: `git rev-parse 9c8d50bce:<driver>`)
PINNED = {
    "benchmark/run_g3_n4.sh": ("run_g3_n4.sh.txt", "cbeb586fbff7c64638f59fc43cfc1f92e8d81832"),
    "benchmark/run_breadth.sh": ("run_breadth.sh.txt", "c42a4f658f9a43c8be17fa552b815ccc4e7cd236"),
}
# what section F reads from the working tree (besides the gate + the fixtures)
F_INPUTS = ("benchmark/run_g3_n4.sh", "benchmark/run_breadth.sh", "benchmark/RUN_SCENARIO_A.md",
            "benchmark/RUN_SCENARIO_B.md")
TEETH_LABEL = "teeth: the pre-fix {rel} (shared-graph sync) is flagged"

_PASS = 0
_FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {label}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}{(' — ' + str(detail)) if detail else ''}")


def _load_gate():
    spec = importlib.util.spec_from_file_location("dm_bcg_r11c_subject", GATE)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _lf(b: bytes) -> bytes:
    return b.replace(b"\r\n", b"\n")


def git_blob_id(content: bytes) -> str:
    """The id `git hash-object` gives this content (LF-normalised, as git stores a text file under autocrlf)."""
    body = _lf(content)
    return hashlib.sha1(b"blob %d\0" % len(body) + body).hexdigest()


def _git_env() -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}   # never inherit a hook's GIT_DIR
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def _git(*args, cwd=None, check_rc=True, hooks: "Path | None" = None):
    cmd = ["git"]
    if hooks is not None:   # an EMPTY hooks dir: no user/global hook ever runs on these throwaway commits
        cmd += ["-c", f"core.hooksPath={hooks}"]
    cmd += ["-c", "user.name=r11c-gate", "-c", "user.email=r11c-gate@example.invalid", "-c", "commit.gpgsign=false",
            "-c", "init.defaultBranch=main", "-c", "core.autocrlf=false", *args]
    p = subprocess.run(cmd, cwd=None if cwd is None else str(cwd), capture_output=True, timeout=120, env=_git_env())
    if check_rc and p.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} -> rc {p.returncode}: {p.stderr.decode('utf-8', 'replace')[-300:]}")
    return p


def _rmtree(path: Path) -> None:
    """Remove a temp dir THIS gate created (git packs are read-only on Windows: clear the bit and retry)."""
    def _retry(func, p, _exc):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except Exception:
            pass
    try:
        if sys.version_info >= (3, 12):
            shutil.rmtree(path, onexc=_retry)    # `onerror` is deprecated from 3.12
        else:
            shutil.rmtree(path, onerror=_retry)
    except OSError:
        pass
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)


# ------------------------------------------------------------------------------------------------ A
def pre_fix_texts() -> "dict[str, str]":
    """The pinned v0.27.4 driver texts: from the fixture when it is the genuine one, else (the RED phase, before
    the fixture existed) from the pinned git blob when this checkout still holds it. {} when neither is there."""
    out = {}
    for rel, (name, blob) in PINNED.items():
        fx = FIXTURE_DIR / name
        if fx.is_file() and git_blob_id(fx.read_bytes()) == blob:
            out[rel] = _lf(fx.read_bytes()).decode("utf-8")
            continue
        if shutil.which("git"):
            p = _git("-C", str(REPO), "cat-file", "blob", blob, check_rc=False)
            if p.returncode == 0:
                out[rel] = _lf(p.stdout).decode("utf-8")
    return out


def gate_fixture_provenance() -> None:
    print("\n-- A: the fixtures are the genuine v0.27.4 drivers (pinned git blob ids) --")
    for rel, (name, blob) in PINNED.items():
        fx = FIXTURE_DIR / name
        check(f"fixture {fx.relative_to(REPO).as_posix()} exists", fx.is_file())
        if not fx.is_file():
            continue
        got = git_blob_id(fx.read_bytes())
        check(f"...its git blob id is the pinned {PRE_D1_COMMIT}:{rel} id ({blob[:12]})", got == blob, got)
        code = [ln for ln in _lf(fx.read_bytes()).decode("utf-8").splitlines()
                if ln.strip() and not ln.lstrip().startswith("#")]
        check("...and a real CODE line (not a comment) runs the shared-graph `deepmap_cli.py sync <clone>`",
              any(re.search(r"deepmap_cli\.py\s+sync\s+\"\$", ln) for ln in code))
        if not shutil.which("git"):
            print(f"  [SKIP] git not on PATH: cannot cross-check the pin against {PRE_D1_COMMIT}:{rel}")
            continue
        p = _git("-C", str(REPO), "rev-parse", "--verify", "-q", f"{PRE_D1_COMMIT}:{rel}", check_rc=False)
        if p.returncode != 0:
            print(f"  [SKIP] {PRE_D1_COMMIT} is not reachable in this checkout (a depth-1 clone / an export): "
                  f"the pinned id stands on its own")
            continue
        check(f"...and the pin IS `git rev-parse {PRE_D1_COMMIT}:{rel}` in this checkout",
              p.stdout.decode().strip() == blob, p.stdout.decode().strip())


# ------------------------------------------------------------------------------------------------ B
def gate_teeth_per_detector(G) -> None:
    print("\n-- B: every detector the v0.27.4 drivers trip fires on the fixture; none on the fixed drivers --")
    expected = ("syncs a clone into a shared graph", "starts the dev repo's servers for the ON arm",
                "never builds the clone as its own project", "never reads the clone's own MCP pair",
                "the capture is not pointed at the clone's pair", "never stops the clone's servers")
    texts = pre_fix_texts()
    for rel in PINNED:
        pre = texts.get(rel)
        if pre is None:
            check(f"{rel}: the pre-fix text is available (fixture or pinned blob)", False, "neither is present")
            continue
        probs = G.driver_problems(pre)
        for want in expected:
            check(f"{rel} (v0.27.4): flagged — {want}", any(p.startswith(want) for p in probs), probs)
        now = G.driver_problems((REPO / rel).read_text(encoding="utf-8"))
        check(f"{rel} (current): no detector fires", not now, now)


# ------------------------------------------------------------------------------------------------ C
def gate_section_f_reads_no_git() -> None:
    print("\n-- C: section F reads no git state (a fixture, never `git show HEAD:`) --")
    src = GATE.read_text(encoding="utf-8")
    fn = next((n for n in ast.walk(ast.parse(src))
               if isinstance(n, ast.FunctionDef) and n.name == "gate_drivers_and_runbooks"), None)
    body = ast.get_source_segment(src, fn) if fn else ""
    check("gate_drivers_and_runbooks exists", bool(body))
    check("...runs no `git show` and names no `HEAD:` revision",
          '"show"' not in body and "'show'" not in body and "HEAD:" not in body, "a git read is back")
    check("...and takes the pre-fix text from the committed fixture dir",
          "benchmark_pre_d1_drivers" in src and ("PRE_D1" in body or "benchmark_pre_d1_drivers" in body))


# ------------------------------------------------------------------------------------------------ D
_RUN_F = r'''
import importlib.util, sys
spec = importlib.util.spec_from_file_location("dm_bcg_r11c_probe", sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
m.gate_drivers_and_runbooks()
print("R11C_COUNTS %d %d" % (m._PASS, m._FAIL))
'''


def run_section_f(root: Path) -> "tuple[int, int, str]":
    """Section F of the gate COPY under <root> (its REPO resolves to <root>) — (passed, failed, output)."""
    env = dict(_git_env(), PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    p = subprocess.run([sys.executable, "-c", _RUN_F, str(root / "tests" / "benchmark_clone_graph_gates.py")],
                       cwd=str(root), capture_output=True, timeout=300, env=env)
    out = p.stdout.decode("utf-8", "replace") + p.stderr.decode("utf-8", "replace")
    m = re.search(r"R11C_COUNTS (\d+) (\d+)", out)
    if not m:
        return 0, 1, out
    return int(m.group(1)), int(m.group(2)), out


def _write(root: Path, rel: str, data: bytes) -> None:
    dst = root / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(data)


def _current_tree(root: Path, crlf_fixtures: bool = False) -> None:
    """Today's working tree, as section F sees it: the gate, the fixtures, the fixed drivers + runbooks."""
    _write(root, "tests/benchmark_clone_graph_gates.py", GATE.read_bytes())
    for rel in F_INPUTS:
        _write(root, rel, (REPO / rel).read_bytes())
    if FIXTURE_DIR.is_dir():
        for fx in sorted(FIXTURE_DIR.iterdir()):
            if fx.is_file():
                data = _lf(fx.read_bytes())
                _write(root, f"tests/fixtures/benchmark_pre_d1_drivers/{fx.name}",
                       data.replace(b"\n", b"\r\n") if crlf_fixtures else data)


def judge(state: str, root: Path) -> None:
    passed, failed, out = run_section_f(root)
    ran = {rel: bool(re.search(r"\[PASS\] " + re.escape(TEETH_LABEL.format(rel=rel)), out)) for rel in PINNED}
    fails = [ln.strip() for ln in out.splitlines() if "[FAIL]" in ln]
    check(f"{state}: section F is GREEN ({passed}/{passed + failed})", failed == 0 and passed > 0,
          "; ".join(fails)[:400] or out[-400:])
    check(f"{state}: both driver-teeth checks RAN and PASSED (not skipped, not failed)", all(ran.values()),
          {k: v for k, v in ran.items() if not v})


def gate_git_states() -> None:
    print("\n-- D: the real section F, from a copy of the gate, GREEN in every git state --")
    if not shutil.which("git"):
        print("  [SKIP] git not on PATH — the git-state matrix needs throwaway git repos")
        return
    pre = pre_fix_texts()
    td = Path(tempfile.mkdtemp(prefix="dmbcg11c_"))
    try:
        hooks = td / "nohooks"
        hooks.mkdir()
        d1 = td / "d1"
        d1.mkdir()
        _git("init", "-q", str(d1), hooks=hooks)
        if len(pre) == len(PINNED):
            for rel, text in pre.items():
                _write(d1, rel, text.encode("utf-8"))
            _git("add", "-A", cwd=d1, hooks=hooks)
            _git("commit", "-q", "--no-verify", "-m", "v0.27.4 drivers (shared-graph sync)", cwd=d1, hooks=hooks)
            _current_tree(d1)
            judge("D1 HEAD = the v0.27.4 drivers, the batch uncommitted", d1)
        else:
            print("  [SKIP] D1: the v0.27.4 driver text is not available (no fixture, no pinned blob)")
            _current_tree(d1)
        _git("add", "-A", cwd=d1, hooks=hooks)
        _git("commit", "-q", "--no-verify", "--allow-empty", "-m", "the batch, committed", cwd=d1, hooks=hooks)
        judge("D2 HEAD = the batch COMMITTED (the operator's commit)", d1)
        d3 = td / "d3"
        _git("clone", "-q", "--depth", "1", d1.resolve().as_uri(), str(d3), hooks=hooks)
        judge("D3 a depth-1 clone of the committed batch (CI's actions/checkout)", d3)
        d4 = td / "d4"
        d4.mkdir()
        _current_tree(d4)
        judge("D4 no git at all (a source tarball / export)", d4)
        d5 = td / "d5"
        d5.mkdir()
        _current_tree(d5, crlf_fixtures=True)
        judge("D5 the fixtures with CRLF line endings (core.autocrlf checkout)", d5)
    finally:
        _rmtree(td)


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 72)
    print("DeepMap — benchmark_clone_graph driver teeth are independent of the git state (ROUND-11c C4)")
    print("=" * 72)
    for gate in (gate_fixture_provenance, lambda: gate_teeth_per_detector(_load_gate()),
                 gate_section_f_reads_no_git, gate_git_states):
        try:
            gate()
        except Exception as exc:  # noqa: BLE001 — a crashing section is a failure, never a skip
            import traceback
            check(f"{getattr(gate, '__name__', 'gate')} ran without crashing", False,
                  f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[-800:]}")
    print("\n" + "=" * 72)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
