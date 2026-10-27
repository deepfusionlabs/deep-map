#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j, no-network, no-spend gates for the CLI's round-4 fixes (2026-09 live validation, owner W3).

Locks:
  PIN  Every "install graphify" hint the CLI prints is THE runnable command graphify_extract builds
       (graphify_install_command: DeepMap's own interpreter, double-quoted with forward slashes, `-m pip
       install --upgrade "<SUPPORTED_GRAPHIFY_SPEC>"` — the F3 validated range, `--upgrade` so an old
       in-range 0.8.39 is lifted and an out-of-range copy replaced). The CLI's fallback literal (used only
       if graphify_extract cannot be imported) equals the constant, formats byte-identically, and no W3
       source file carries an unpinned `graphifyy`. A REAL Git Bash keeps the command's tokens intact and
       runs its interpreter.
  RNG  doctor shows the graphify version the last sync ran AND whether it is inside DeepMap's supported
       (validated) range: inside -> an OK fact line naming the range; outside -> the extractor line WARNs
       and names the fix ONCE (a /deep-map:refresh when a supported graphify is installed now, else the
       pinned pip command) — never the same pip command two or three times in one block. Informational:
       the verdict is the manifest's. An older manifest (no version) prints no range line.
  NOM  With no sync manifest yet, doctor names the INSTALLED graphify version and whether it is inside
       the supported range (outside -> WARN + the pinned command, before the first sync runs on it).
  FB   An automatic auto -> ast fallback while an UNSUPPORTED graphify is installed names that graphify
       and the pinned command (the actual fix), without repeating a command the recorded reason already
       carries.
  F1   The CLI's owner check (status / doctor "NOT this project") is port_registry.owner_is_this_project —
       THE owner predicate — on every shape: the same repo, a subdirectory, a NESTED repository (a project
       of its own, both directions), a vanished narrower stamp, and nested non-git folders.

Hermetic: temp DEEPMAP_HOME, fake Neo4j drivers (neo4j.GraphDatabase.driver patched), temp project dirs,
the installed graphify stubbed (the result is the same whatever this box has installed); never touches
~/.deepmap or the committed .mcp.json. The only subprocesses are git (temp repos) and, when a real bash is
on PATH, `bash -c` tokenizing the command and running `<python> -m pip --version` (local, no network).
Helpers are shared with tests/cli_route_impact_gates.py (the round-2 CLI gate).
"""
from __future__ import annotations

import contextlib
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build"

_PASS = 0
_FAIL = 0

# The files W3 owns: none may carry an unpinned graphify install hint.
_W3_FILES = ("deepmap_cli.py", "enrich.py", "insights.py", "learning_loop.py", "instance_state.py",
             "retrieval_log.py")


def check(label: str, cond: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {label}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}{(' — ' + detail) if detail else ''}")


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


H = _load_module(REPO / "tests" / "cli_route_impact_gates.py", f"dm_cli_r2_helpers4_{time.time_ns()}")


class _GXStub:
    """The real graphify_extract, but reporting `(supported, version)` as the graphify installed NOW."""

    def __init__(self, real, supported, version, *, drop=()):
        self._real, self._sv, self._drop = real, (supported, version), set(drop)

    def graphify_support(self):
        return self._sv

    def __getattr__(self, name):
        if name in self._drop:
            raise AttributeError(name)
        return getattr(self._real, name)


@contextlib.contextmanager
def _installed(C, GX, supported, version, *, importable=True, drop=()):
    """doctor sees graphify `version` (supported or not) installed NOW, importable or not."""
    import importlib.util as IU
    real_find = IU.find_spec
    saved = C._graphify_engine
    C._graphify_engine = lambda: _GXStub(GX, supported, version, drop=drop)
    IU.find_spec = lambda name, *a, **k: ((object() if importable else None) if name == "graphify"
                                          else real_find(name, *a, **k))
    try:
        yield
    finally:
        C._graphify_engine = saved
        IU.find_spec = real_find


def _doctor(C, PR, proj: Path, manifest):
    """doctor output for `proj` (a healthy fake graph it owns); `manifest` None -> no manifest at all."""
    if manifest is not None:
        H._write_manifest(proj, **manifest)
    fac = H._Factory(lambda: H._GraphDriver(owner=PR.project_identity(proj), nodes=7))
    with H._env(NEO4J_URI="bolt://localhost:7999"), H._patched_graphdb(fac):
        return H._run_doctor(C, cwd=proj, provision=H._fake_provision({8787, 8788}, []))


def _extractor_block(out: str) -> list:
    lines = out.splitlines()
    for i, ln in enumerate(lines):
        if "] extractor" in ln:
            block = [ln]
            for nxt in lines[i + 1:]:
                if nxt.startswith("  [") or not nxt.strip() or nxt.startswith("["):
                    break
                block.append(nxt)
            return block
    return []


def _real_bash():
    """A real POSIX bash (Git Bash on Windows), never the WSL launcher in System32. Or None."""
    b = shutil.which("bash")
    if not b or "system32" in b.lower() or "windowsapps" in b.lower():
        return None
    return b


def _bash(script: str, cmd: str):
    b = _real_bash()
    if not b:
        return None
    try:
        return subprocess.run([b, "-c", script], capture_output=True, timeout=120,
                              env=dict(os.environ, DM_CMD=cmd), encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return None


# ---- PIN: one pinned, runnable install command ----------------------------------------------------------
def gate_pin(C, GX, PR, work: Path) -> None:
    print("\n-- PIN: every graphify install hint is graphify_extract's pinned, quoted, --upgrade command --")
    spec = GX.SUPPORTED_GRAPHIFY_SPEC
    posix = Path(sys.executable).as_posix()
    want = f'"{posix}" -m pip install --upgrade "{spec}"'
    check("graphify_extract.graphify_install_command() is the reference form (quoted python, --upgrade, spec)",
          GX.graphify_install_command() == want, GX.graphify_install_command())
    check("CLI _graphify_spec() == graphify_extract.SUPPORTED_GRAPHIFY_SPEC", C._graphify_spec() == spec,
          C._graphify_spec())
    check("CLI _graphify_install_cmd() == graphify_extract.graphify_install_command() (one source)",
          C._graphify_install_cmd() == GX.graphify_install_command(), C._graphify_install_cmd())

    src = (BUILD / "deepmap_cli.py").read_text(encoding="utf-8")
    lits = re.findall(r'"(graphifyy[^"]*)"', src)
    check("the CLI's fallback spec literal(s) equal SUPPORTED_GRAPHIFY_SPEC (drift lock)",
          bool(lits) and all(x == spec for x in lits), repr(lits))

    saved = C._graphify_engine
    try:
        C._graphify_engine = lambda: None                       # graphify_extract not importable
        check("graphify_extract NOT importable: the fallback is still the pinned, quoted --upgrade command",
              C._graphify_install_cmd() == want, C._graphify_install_cmd())
        C._graphify_engine = lambda: _GXStub(GX, True, "0.8.51", drop=("graphify_install_command",
                                                                         "hint_python"))
        check("an older graphify_extract (no graphify_install_command): formats byte-identically",
              C._graphify_install_cmd() == want, C._graphify_install_cmd())
    finally:
        C._graphify_engine = saved

    bad = []
    for name in _W3_FILES:
        text = (BUILD / name).read_text(encoding="utf-8")
        for m in re.finditer(r"graphifyy(\S{0,3})", text):
            if not m.group(1).startswith(">="):
                line = text.count("\n", 0, m.start()) + 1
                bad.append(f"{name}:{line}: {text[max(0, m.start() - 30):m.end() + 5]!r}")
    check("no W3 source file names an UNPINNED graphifyy (every mention is the spec)", not bad, "; ".join(bad))

    # A real Git Bash (the shell Claude Code runs on Windows): the tokens survive, the interpreter runs.
    cmd = C._graphify_install_cmd()
    p = _bash('eval "set -- $DM_CMD"; printf "%s\\n" "$@"', cmd)
    if p is None:
        print("     (no real bash on PATH — the live-shell half is skipped)")
    else:
        argv = p.stdout.splitlines() if p.returncode == 0 else None
        check("a REAL bash tokenizes the hint to [python, -m, pip, install, --upgrade, spec]",
              argv == [posix, "-m", "pip", "install", "--upgrade", spec], repr(argv))
        runnable = cmd.split(" -m pip install", 1)[0] + " -m pip --version"
        p = _bash('eval "$DM_CMD"', runnable)
        check("... and runs DeepMap's interpreter as written (`<python> -m pip --version` exits 0)",
              p is not None and p.returncode == 0 and "pip" in (p.stdout or ""),
              (p.stdout + p.stderr)[-300:] if p is not None else "no bash")

    # Every doctor path that tells the user to install graphify prints exactly that command.
    base = {"verdict": "READY", "reasons": []}
    cases = (
        ("auto, graphify not installed, no manifest", None, None, False),
        ("graphify forced, not installed", None, "graphify", False),
        ("ast forced, graphify not installed", None, "ast", False),
        ("last sync used graphify, not importable now", dict(base, extractor="graphify",
                                                            extractor_requested="auto",
                                                            graphify_version="0.8.51"), None, False),
        ("auto fell back to ast, graphify not installed", dict(base, extractor="ast", extractor_requested="auto"),
         None, False),
    )
    for i, (label, manifest, mode, importable) in enumerate(cases):
        proj = work / f"pin{i}"
        proj.mkdir()
        with _installed(C, GX, None, None, importable=importable), H._env(DEEPMAP_EXTRACTOR=mode):
            rc, out = _doctor(C, PR, proj, manifest)
        block = "\n".join(_extractor_block(out))
        check(f"doctor, {label}: the hint is the pinned --upgrade command",
              want in block and not re.search(r"pip install\s+(--upgrade\s+)?\"?graphifyy(?!>=)", block),
              block[-400:])


# ---- RNG: the version the last sync ran vs the supported range -----------------------------------------
def gate_range(C, GX, PR, work: Path) -> None:
    print("\n-- RNG: doctor names the graphify version the last sync ran and whether it is in the range --")
    spec = GX.SUPPORTED_GRAPHIFY_SPEC
    cmd = GX.graphify_install_command()
    base = {"extractor": "graphify", "extractor_requested": "auto", "verdict": "READY", "reasons": []}

    def run(tag, built, installed, **extra):
        proj = work / f"rng_{tag}"
        proj.mkdir()
        m = dict(base, **extra)
        if built is not None:
            m["graphify_version"] = built
        with _installed(C, GX, *installed):
            rc, out = _doctor(C, PR, proj, m)
        return _extractor_block(out), out

    for built in ("0.8.39", "0.8.51", "0.8.48"):
        block, out = run(f"in{built.replace('.', '')}", built, (True, built))
        text = "\n".join(block)
        check(f"built {built}: an OK extractor line + a fact line 'inside DeepMap's supported range ({spec})'",
              bool(block) and block[0].strip().startswith("[OK") and f"graphify {built}" in text
              and "inside" in text and spec in text, text)
        check(f"built {built}: verdict READY", H._verdict_of(out) == "READY", H._verdict_of(out))

    for built in ("0.8.38", "0.8.52", "0.9.1"):
        block, out = run(f"out{built.replace('.', '')}", built, (False, built))
        text = "\n".join(block)
        check(f"built {built} (still installed): the extractor line WARNs, naming it OUTSIDE {spec}",
              bool(block) and block[0].strip().startswith("[WARN") and f"graphify {built}" in text
              and "OUTSIDE" in text and spec in text, text)
        check(f"built {built}: the pinned command appears exactly ONCE in the block (no repeat)",
              text.count(cmd) == 1, f"{text.count(cmd)}x: {text}")
        check(f"built {built}: informational — the verdict stays the manifest's (READY)",
              H._verdict_of(out) == "READY", H._verdict_of(out))

    block, out = run("fixednow", "0.9.1", (True, "0.8.51"))
    text = "\n".join(block)
    check("built 0.9.1, a supported 0.8.51 installed NOW: WARN, OUTSIDE, and the fix is a /deep-map:refresh",
          bool(block) and block[0].strip().startswith("[WARN") and "OUTSIDE" in text and "0.8.51" in text
          and "/deep-map:refresh" in text, text)
    check("... with no pip command at all (nothing to install)", "-m pip install" not in text, text)

    block, out = run("blocked", "0.9.1", (False, "0.9.1"), verdict="DEGRADED",
                     reasons=["LOW COVERAGE in build/ — 4 git-tracked source file(s) but only 0 extracted "
                              "node(s)"],
                     graphify_rescue_blocked=["build", "out"], graphify_self_call_rebind=True)
    text = "\n".join(block)
    check("blocked dirs + an unsupported graphify: the dirs are still named NOT re-included",
          "NOT re-included" in text and "build/" in text and "out/" in text, text)
    check("... and the pinned command appears exactly ONCE in the block (it was printed 2-3x)",
          text.count(cmd) == 1, f"{text.count(cmd)}x: {text}")

    block, out = run("legacy", None, (True, "0.8.51"))
    text = "\n".join(block)
    check("an older manifest (no graphify_version): no range line, as before",
          "supported range" not in text and len(block) == 1, text)


# ---- NOM: no manifest yet ---------------------------------------------------------------------------
def gate_no_manifest(C, GX, PR, work: Path) -> None:
    print("\n-- NOM: with no sync manifest, doctor names the installed graphify + the range --")
    spec = GX.SUPPORTED_GRAPHIFY_SPEC
    cmd = GX.graphify_install_command()
    proj = work / "nomOk"
    proj.mkdir()
    with _installed(C, GX, True, "0.8.51"):
        rc, out = _doctor(C, PR, proj, None)
    ext = H._line_with(out, "] extractor")
    check("supported 0.8.51 installed, no manifest: OK, names 'graphify 0.8.51' and the supported range",
          ext.strip().startswith("[OK") and "graphify 0.8.51" in ext and "inside" in ext and spec in ext, ext)
    proj = work / "nomBad"
    proj.mkdir()
    with _installed(C, GX, False, "0.9.3"):
        rc, out = _doctor(C, PR, proj, None)
    ext = H._line_with(out, "] extractor")
    check("unsupported 0.9.3 installed, no manifest: WARN, names it OUTSIDE the range + the pinned command",
          ext.strip().startswith("[WARN") and "0.9.3" in ext and "OUTSIDE" in ext and cmd in ext, ext)


# ---- FB: the ast fallback names the unsupported installed graphify -----------------------------------
def gate_fallback(C, GX, PR, work: Path) -> None:
    print("\n-- FB: an auto -> ast fallback names an unsupported installed graphify + the fix --")
    cmd = GX.graphify_install_command()
    fb = {"extractor": "ast", "extractor_requested": "auto", "verdict": "DEGRADED", "reasons": []}
    proj = work / "fbBad"
    proj.mkdir()
    with _installed(C, GX, False, "0.9.3"):
        rc, out = _doctor(C, PR, proj, dict(fb, extractor_fallback_reason="graphify exited 1: ImportError"))
    ext = "\n".join(_extractor_block(out))
    check("fell back while graphify 0.9.3 (unsupported) is installed: names it + the pinned command",
          "fell back automatically" in ext and "0.9.3" in ext and cmd in ext, ext)
    proj = work / "fbHas"
    proj.mkdir()
    short_cmd = f'"py" -m pip install --upgrade "{GX.SUPPORTED_GRAPHIFY_SPEC}"'   # fits the clipped line
    with _installed(C, GX, False, "0.9.3"):
        rc, out = _doctor(C, PR, proj, dict(fb, extractor_fallback_reason=f"graphify 0.9.3 is unsupported — "
                                                                           f"reinstall: {short_cmd}"))
    ext = "\n".join(_extractor_block(out))
    check("... a recorded reason that already carries a pip command: no second one is added",
          short_cmd in ext and ext.count("-m pip install") == 1, f"{ext.count('-m pip install')}x: {ext}")
    proj = work / "fbHasLong"
    proj.mkdir()
    with _installed(C, GX, False, "0.9.3"):
        rc, out = _doctor(C, PR, proj, dict(fb, extractor_fallback_reason=GX.unsupported_engine_hint("0.9.3")))
    ext = "\n".join(_extractor_block(out))
    check("... a recorded reason whose command is too long for the line: shown exactly once, whole",
          ext.count(cmd) == 1 and ext.count("-m pip install") == 1, f"{ext.count(cmd)}x: {ext}")
    proj = work / "fbOk"
    proj.mkdir()
    with _installed(C, GX, True, "0.8.51"):
        rc, out = _doctor(C, PR, proj, dict(fb, extractor_fallback_reason="graphify returned nothing"))
    ext = "\n".join(_extractor_block(out))
    check("... a SUPPORTED graphify installed: no pip command is added to the fallback line",
          "-m pip install" not in ext and "fell back automatically" in ext, ext)

    # A long recorded reason is clipped for the doctor line — never through the middle of a command
    # (the old [:200] cut printed '"C:/Users/.../PythonSoftwareFoundati' as the "command").
    for pad in (120, 150, 175, 199):
        reason = "graphify crashed: " + "x" * pad + " — install it: " + cmd + ", then re-run /deep-map:refresh"
        got = C._manifest_fallback_reason({"extractor_fallback_reason": reason}) or ""
        whole = cmd in got
        none = " -m pip" not in got and got.count('"') == 0
        check(f"clipped reason (pad {pad}): the command is whole or absent, never cut in half",
              (whole or none) and len(got) <= 205 and got.endswith("…"), repr(got[-160:]))
    short = "graphify returned nothing"
    check("a short reason is shown verbatim", C._manifest_fallback_reason({"extractor_fallback_reason": short})
          == short)


# ---- F1: the CLI's owner check is THE owner predicate ------------------------------------------------
def gate_owner_predicate(C, PR, work: Path) -> None:
    print("\n-- F1: status/doctor's 'NOT this project' check == port_registry.owner_is_this_project --")
    if not shutil.which("git"):
        check("(git on PATH for the owner cases)", True, "skipped — git absent")
        return
    P = work / "ownParent"
    H._git_repo(P)
    N = P / "vendor" / "nested"
    H._git_repo(N)                                  # a repository of its own INSIDE the parent
    S = P / "svc"
    S.mkdir()
    A = work / "ownPlain"
    B = A / "inner"
    B.mkdir(parents=True)
    pairs = (("the same repo", P, P), ("a subdirectory of the repo", S, P), ("the repo, stamp = its subdir", P, S),
             ("nested repo, parent's stamp", N, P), ("parent, nested repo's stamp", P, N),
             ("a vanished narrower stamp", P, P / "gone" / "old"),
             ("non-git folder, inner folder's stamp", A, B), ("inner non-git folder, outer's stamp", B, A))
    for label, root, owner in pairs:
        want = PR.owner_is_this_project(root, str(owner))
        got = C._foreign_owner(root, str(owner)) is None
        check(f"{label}: CLI == owner_is_this_project ({want})", got == want, f"cli={got} predicate={want}")
    check("F1: a NESTED repository's graph is NOT the parent's (and vice versa)",
          C._foreign_owner(N, str(P)) is not None and C._foreign_owner(P, str(N)) is not None)
    check("F1: a subdirectory of the same repo IS the repo's project", C._foreign_owner(S, str(P)) is None)

    fac = H._Factory(lambda: H._GraphDriver(owner=PR.project_identity(P), nodes=7))
    with H._env(NEO4J_URI="bolt://localhost:7999"), H._patched_graphdb(fac):
        rc, out = H._run_doctor(C, cwd=N, provision=H._fake_provision({8787, 8788}, []))
    own = H._line_with(out, "] owner")
    check("doctor inside the nested repo, graph stamped by the parent: owner WARN 'NOT this project'",
          own.strip().startswith("[WARN") and "NOT this project" in own, own)


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 72)
    print("DeepMap — CLI round-4 gates (W3: pinned graphify hints, doctor version/range, owner predicate)")
    print("=" * 72)
    saved_env = {k: os.environ.get(k) for k in H._SCRUB + ("DEEPMAP_HOME", "DEEPMAP_SELF_CALL_REBIND",
                                                          "DEEPMAP_RESCUE_SKIPPED_DIRS")}
    for k in saved_env:
        if k != "DEEPMAP_HOME":
            os.environ.pop(k, None)
    with tempfile.TemporaryDirectory() as td:
        base = Path(td).resolve()
        home = base / "home"
        home.mkdir()
        work = base / "work"
        work.mkdir()
        os.environ["DEEPMAP_HOME"] = str(home)
        try:
            if str(BUILD) not in sys.path:
                sys.path.insert(0, str(BUILD))
            import graphify_extract as GX
            C = H._load_cli()
            PR = _load_module(BUILD / "port_registry.py", f"dm_pr_r4_{time.time_ns()}")
            for gate in (lambda: gate_pin(C, GX, PR, work),
                         lambda: gate_range(C, GX, PR, work),
                         lambda: gate_no_manifest(C, GX, PR, work),
                         lambda: gate_fallback(C, GX, PR, work),
                         lambda: gate_owner_predicate(C, PR, work)):
                try:
                    gate()
                except Exception as exc:  # noqa: BLE001 — a crashing gate is a RED gate, not a crash
                    import traceback
                    traceback.print_exc()
                    check(f"gate ran without crashing ({type(exc).__name__}: {exc})", False)
                finally:
                    H._clear_registry(PR)
        finally:
            for k, v in saved_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
    print("\n" + "=" * 72)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
