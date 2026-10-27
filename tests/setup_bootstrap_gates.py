#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""/deep-map:setup installs DeepMap's own dependencies — no terminal step (operator decision, 2026-10-04).

Before: only install.sh / install.ps1 pip-installed DeepMap's packages. /deep-map:setup runs bare `python`; on a Python
without them, `provision.py setup` read the missing neo4j driver as "Neo4j is down", downloaded and started Neo4j,
waited 120 s and ended "Neo4j did not become reachable" (exit 1), leaving the JVM running.

Now (build/deps_bootstrap.py): when the Python running setup lacks a requirement, or holds one outside
requirements.txt's range, setup never pip-installs into it. It checks Java and free disk, creates or repairs DeepMap's
own environment at <DEEPMAP_HOME>/venv, installs requirements.txt there (torch from the PyTorch CPU index), downloads
the embedding model (the one step run with HF_HUB_OFFLINE lifted), runs scripts/ml_stack_selfcheck.py with it, then
re-runs setup under it and passes its output and exit code through. Every other entry point a slash command runs
(provision.py's verbs, deepmap_cli.py) re-runs itself under that interpreter once the environment is complete. A Python
that already has everything behaves exactly as before.

  A  detection: requirements() is requirements.txt; missing() reads *.dist-info / *.egg-info names (it imports neither
     importlib.metadata nor packaging; a version-less legacy egg-info costs one metadata read); its comparator agrees
     with packaging and the self-check; on this interpreter it agrees with importlib.metadata. A2: requirements.txt is
     read the way pip reads it (a BOM, extras, markers).
  B  paths: <DEEPMAP_HOME>/venv by provision's own DEEPMAP_HOME rule; Scripts/python.exe on Windows, bin/python else.
  C  specs: the pip commands install exactly requirements.txt (version-ranged specs with --upgrade, torch from the CPU
     index); deps_bootstrap.py holds no copy of a range.
  D  offline: only the model download runs without HF_HUB_OFFLINE; the probe, the cache check and the self-check run
     with it on; the caller's environment is never changed; nothing points pip at another Python or prefix.
  E  setup's census, Java, Windows MAX_PATH and free disk are checked before anything is created or downloaded.
  F  re-exec: REAL child processes — exit codes 0/1/3/4 and stdout/stderr pass through; the child gets the loop guard
     and no PYTHONPATH / PYTHONHOME / __PYVENV_LAUNCHER__; setup's child exit code is returned unchanged.
  G  loop guard + opt-out: DEEPMAP_REEXEC_GUARD=1 / DEEPMAP_MANAGED_VENV=0 never bootstrap or re-exec; nor does a
     Python that has everything, or an incomplete environment (recorders, so a dropped check reaches the re-run).
  P  a complete environment whose interpreter cannot start is never handed a command (it runs where it was started),
     and one that starts always is. Windows: as the venv launcher reads pyvenv.cfg (a `home` key, case-sensitive, a
     BOM allowed; python.exe in that directory, or the free-threaded python3.Xt.exe). POSIX: no pyvenv.cfg or a dangling
     bin/python; `home` plays no part (site.py activates the venv without it, measured on CPython 3.10-3.14).
  N  an environment finished against another requirements.txt that lacks one of today's: one stderr note per command,
     kept after a repair that failed and left it in use.
  H  idempotency / repair: a complete environment is reused as is; an out-of-range package is repaired (a failed
     repair keeps it in use when it is no worse); a broken one DeepMap made (its Python does not run, it has no pip,
     or its Python moved to another minor version under it) is moved aside and re-created, never cleared in place,
     never while a process runs from it and never when the process table cannot be read (the stop command named then
     carries DEEPMAP_MANAGED_VENV=0 when a bare python would still hand it to that environment); such a refusal
     leaves the last build log alone; a directory DeepMap did not make (a working venv included, or what a delete
     left while servers ran) is refused and left alone, naming what still runs from it (a delete is advised only when
     DeepMap's own processes are among it: an owner record, or a script in a DeepMap build directory).
  M  a partly cached model is completed before the self-check; a cached copy that cannot load names its directory.
  I  failure messages: Debian/Ubuntu without python3-venv (only there), pip / network, no build for this platform (or
     an index that refused pip: pip logs both the same way), pip set to use no index, a file a running server holds,
     the model, the self-check, a timeout, Python < 3.10 —
     each names the fix and ends "setup verdict: FAILED — ..."
     (exit 1). With no usable neo4j driver, setup() / ensure_neo4j() say the packages are missing — never "Neo4j did
     not become reachable" — and start no JVM.
  L  every remedy for a package this Python lacks is /deep-map:setup (doctor, the sync verdict, graphify's command),
     never pip into that Python; so is every remedy inside DeepMap's environment once its Python moved to another
     minor version; DEEPMAP_MANAGED_VENV=0 keeps the pip commands.
  Q  the SessionStart heal starts DeepMap's environment only when the commands would use it
     (deps_bootstrap.managed_python, imported by the hook only for that interpreter); else the hook's own Python.
  K  a fresh Neo4j zip passes the real archive check under its .part download name.
  O  the command docs the agent acts on: setup.md asks for the Bash tool's longest background timeout; doctor.md sends
     a neo4j package the Python running DeepMap lacks to /deep-map:setup.
  J  wiring: provision.py / deepmap_cli.py call the bootstrap from __main__ before main(); the module is stdlib-only and
     parses on Python 3.8; END TO END with real interpreters (two throwaway venvs and a sitecustomize tracer): a bare
     Python re-runs deepmap_cli.py / provision.py under the "managed" one exactly once, output and exit code intact,
     and runs them itself (doctor naming /deep-map:setup) when that one's interpreter cannot start; the no-start rule
     agrees with this OS's real venv for every pyvenv.cfg shape tried; a process running from a venv is seen by the
     check that guards its re-creation.

Hermetic: temp DEEPMAP_HOME; venv creation, pip, the model download and the self-check are fakes in C-I (no network,
no Neo4j, no JVM). Section J creates two throwaway venvs (--without-pip, local only) and removes them.
Usage: python tests/setup_bootstrap_gates.py [--root <tree>]
"""
from __future__ import annotations

import ast
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PASSES: list = []
FAILS: list = []
_WIN = os.name == "nt"
_SCRUB = ("DEEPMAP_REEXEC_GUARD", "DEEPMAP_MANAGED_VENV", "PYTHONPATH", "PYTHONHOME", "__PYVENV_LAUNCHER__",
          "DEEPMAP_HOME", "NEO4J_URI", "DEEPMAP_PYTHON", "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "DMSB_TRACE",
          "PIP_NO_INDEX", "PIP_PYTHON")


def check(name: str, ok: bool, detail="") -> None:
    (PASSES if ok else FAILS).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok or detail == "" else f" — {str(detail)[:900]}"))


_ABSENT = object()


class _Patch:
    """Set module attributes for a block. A seam an older tree lacks is set and removed again, so a gate run
    against that tree reports a failed check instead of dying."""

    def __init__(self, mod, **attrs):
        self.mod, self.attrs, self.saved = mod, attrs, {}

    def __enter__(self):
        for k, v in self.attrs.items():
            self.saved[k] = getattr(self.mod, k, _ABSENT)
            setattr(self.mod, k, v)
        return self

    def __exit__(self, *exc):
        for k, v in self.saved.items():
            if v is _ABSENT:
                delattr(self.mod, k)
            else:
                setattr(self.mod, k, v)
        return False


def _quiet(fn, *a, **k):
    """(result, stdout, stderr, exception) of fn(*a, **k) with both streams captured."""
    out, err = io.StringIO(), io.StringIO()
    res, exc = None, None
    with redirect_stdout(out), redirect_stderr(err):
        try:
            res = fn(*a, **k)
        except BaseException as e:  # noqa: BLE001 — a gate reports, never dies
            exc = e
    return res, out.getvalue(), err.getvalue(), exc


def _req_specs(text: str) -> list:
    """requirements.txt read independently of the module: [(name, spec without whitespace)] in file order."""
    out = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        name = re.split(r"[<>=!~;\[\s]", line, maxsplit=1)[0].strip()
        out.append((name, re.sub(r"\s+", "", line[len(name):])))
    return out


def _clean_env(**extra) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in _SCRUB}
    env.update(extra)
    return env


# --------------------------------------------------------------------------------------------------- fake world
class _World:
    """Fakes for the bootstrap's process seams (_run_step / _capture): records every step with its argv + env, writes a
    venv layout when the "venv" step succeeds, and answers the probe / model-cache questions from scripted results."""

    def __init__(self, vdir: Path, *, probe=None, cached=None, rc=None, logtext=None, base_home=None, pipconf=None):
        self.vdir = Path(vdir)
        self.events: list = []
        self.probe = list(probe if probe is not None else [{"missing": []}])
        self.cached = list(cached if cached is not None else [{"cached": True}])
        self.rc = dict(rc or {})
        self.logtext = dict(logtext or {})
        self.base_home = str(base_home or self.vdir.parent / "base-python")
        self.pipconf = pipconf   # what `pip config list` prints (None: it could not run)
        self.marker_at: dict = {}

    def _venv_layout(self) -> None:
        """A venv as `-m venv` leaves it, made from an installed base Python (Windows: <home>\\python.exe, which the
        venv's launcher runs)."""
        self.vdir.mkdir(parents=True, exist_ok=True)
        base = Path(self.base_home)
        base.mkdir(parents=True, exist_ok=True)
        if _WIN:
            (base / "python.exe").write_bytes(b"")
        (self.vdir / "pyvenv.cfg").write_text(f"home = {self.base_home}\n", encoding="utf-8")
        py = self.vdir / ("Scripts" if _WIN else "bin") / ("python.exe" if _WIN else "python")
        py.parent.mkdir(parents=True, exist_ok=True)
        py.write_bytes(b"")

    def run_step(self, key, argv, *, env, log, what, timeout_s):
        self.events.append(("step", key, [str(a) for a in argv], dict(env)))
        try:
            self.marker_at[key] = json.loads((self.vdir / "deepmap-venv.json").read_text(encoding="utf-8"))
        except Exception:
            self.marker_at[key] = None
        if key in self.logtext:
            log.write(self.logtext[key].encode("utf-8"))
            log.flush()
        rc = self.rc.get(key, 0)
        if key == "venv" and (rc == 0 or getattr(self, "half_made", False)):
            self._venv_layout()   # half_made: Debian's `-m venv` without ensurepip leaves a venv without pip
        return rc, 0.01

    def capture(self, key, argv, *, env, timeout_s):
        self.events.append(("capture", key, [str(a) for a in argv], dict(env)))
        if key == "pip-config":
            return (0, self.pipconf) if self.pipconf is not None else (1, "")
        seq = {"probe": self.probe, "model-cached": self.cached}.get(key)
        if seq is None:
            return 1, ""
        r = seq.pop(0) if len(seq) > 1 else seq[0]
        if r == "TIMEOUT":
            return -1, ""   # _capture's answer for a helper that ran past its limit
        if r is None:
            return 1, "Traceback (most recent call last):\n  boom\n"
        return 0, "noise line\n" + json.dumps(r) + "\n"

    def steps(self) -> list:
        return [e[1] for e in self.events if e[0] == "step"]

    def step(self, key):
        return next((e for e in self.events if e[0] == "step" and e[1] == key), None)


def _checks(B, world: _World, *, java=None, neo4j=False, census=None, long_paths=True):
    def java_problems():
        world.events.append(("java",))
        return list(java or [])

    def neo():
        world.events.append(("neo4j?",))
        return neo4j

    def cen(target):
        world.events.append(("census", str(target)))
        return census

    return B.SetupChecks(census=cen, java_problems=java_problems, neo4j_download_needed=neo,
                         long_paths_enabled=lambda: long_paths)


def _disk(world: _World, free_gb: float = 500.0):
    def f(path):
        world.events.append(("disk", str(path)))
        return int(free_gb * (1024 ** 3))
    return f


def _ensure(B, world: _World, checks, env: dict, *, guess=False, pyver=None, target=".", extra=None):
    patches = dict(_run_step=world.run_step, _capture=world.capture, _model_cached_guess=lambda e: guess,
                   _disk_free_bytes=getattr(world, "disk", None) or _disk(world),
                   _processes_using=lambda vdir: [])   # the real look at this machine's processes: section J
    if pyver is not None:
        patches["_python_version"] = lambda: pyver
    patches.update(extra or {})
    with _Patch(B, **patches):
        return _quiet(B.ensure_env, checks, target=target, env=env)


def _complete_env(vdir: Path, world: _World) -> None:
    """A working environment DeepMap made and finished: venv layout + marker complete."""
    world._venv_layout()
    _write_marker(vdir, True)


def _leftovers(home: Path) -> list:
    """Anything a re-creation left next to <home>/venv (a moved-aside copy that was not removed)."""
    return sorted(p.name for p in home.iterdir() if p.name.startswith("venv.")) if home.exists() else []


def _home_env(home: Path, **extra) -> dict:
    return _clean_env(DEEPMAP_HOME=str(home), **extra)


def _write_marker(vdir: Path, complete: bool, **extra) -> None:
    vdir.mkdir(parents=True, exist_ok=True)
    (vdir / "deepmap-venv.json").write_text(json.dumps({"complete": complete, **extra}), encoding="utf-8")


def _seed_log(home: Path) -> bytes:
    """A previous build's log at <home>/venv-setup.log (a refusal must leave it as it is)."""
    data = b"===== 2026-10-05 12:00:00 installing the version-pinned packages\nthe previous build's log\n"
    home.mkdir(parents=True, exist_ok=True)
    (home / "venv-setup.log").write_bytes(data)
    return data


def _log_kept(home: Path, data: bytes, out: str) -> bool:
    """The seeded log is untouched and no 'details: <log>' line pointed at it."""
    p = home / "venv-setup.log"
    return p.is_file() and p.read_bytes() == data and "details:" not in out


# --------------------------------------------------------------------------------------------------- A
def gate_a_detection(B, root: Path, td: Path) -> None:
    print("\n-- A: requirements() is requirements.txt; missing() is a fast *.dist-info scan agreeing with pip --")
    want = _req_specs((root / "requirements.txt").read_text(encoding="utf-8"))
    got = B.requirements()
    check("requirements() == requirements.txt, line for line (the one copy of the ranges)", got == want,
          f"{got} vs {want}")
    pins = dict(want)
    check("...it carries the pinned ranges (neo4j / mcp / graphifyy) and the unpinned torch",
          all(pins.get(p) for p in ("neo4j", "mcp", "graphifyy")) and pins.get("torch") == "", pins)

    site1, site2, site3 = td / "site1", td / "site2", td / "site3"
    for d in (site1, site2, site3):
        d.mkdir()
    good = {"neo4j": "6.2.0", "mcp": "1.28.1", "anyio": "4.9.0", "torch": "2.12.1+cpu",
            "sentence_transformers": "5.6.0", "transformers": "5.12.1", "huggingface_hub": "1.21.0",
            "PyYAML": "6.0.2", "graphifyy": "0.8.51"}
    for n, v in good.items():
        (site1 / f"{n}-{v}.dist-info").mkdir()
    (site1 / "notes.txt").write_text("x", encoding="utf-8")
    reqs = B.requirements()
    check("a site with every requirement inside its range -> nothing missing (wheel-escaped names: "
          "sentence_transformers, huggingface_hub, PyYAML; a +cpu local version)",
          B.missing(reqs, paths=[str(site1)]) == [], B.missing(reqs, paths=[str(site1)]))
    (site2 / "mcp-2.2.0.dist-info").mkdir()
    m = B.missing(reqs, paths=[str(site2), str(site1)])
    check("the FIRST copy on the path wins: an mcp 2.2.0 ahead of 1.28.1 is reported out of range, with its version",
          [(x["name"], x["found"]) for x in m] == [("mcp", "2.2.0")] and m[0]["spec"] == pins.get("mcp"), m)
    check("...and behind it, the in-range copy wins", B.missing(reqs, paths=[str(site1), str(site2)]) == [])
    for n, v in good.items():
        if n != "torch":
            (site3 / f"{n}-{v}.dist-info").mkdir()
    m = B.missing(reqs, paths=[str(site3), str(td / "no-such-dir"), str(td / "x.zip")])
    check("an absent package is reported with found=None (missing dirs / zip entries on the path are skipped)",
          [(x["name"], x["found"]) for x in m] == [("torch", None)], m)
    for bad_name, bad_ver in (("neo4j", "5.28.6"), ("neo4j", "6.0.0"), ("graphifyy", "0.8.52"), ("mcp", "1.23.3")):
        d = td / f"bad-{bad_name}-{bad_ver}"
        d.mkdir()
        (d / f"{bad_name}-{bad_ver}.dist-info").mkdir()
        m = B.missing(reqs, paths=[str(d), str(site1)])
        check(f"{bad_name} {bad_ver} (outside the validated range) is reported missing",
              [(x["name"], x["found"]) for x in m] == [(bad_name, bad_ver)], m)
    egg = td / "egg"
    egg.mkdir()
    (egg / "PyYAML-6.0.2-py3.11.egg-info").mkdir()
    check("a legacy *.egg-info counts (name + version read from the directory name)",
          B.installed_versions([str(egg)]).get("pyyaml") == "6.0.2", B.installed_versions([str(egg)]))
    # a legacy editable install (`setup.py develop`) leaves <name>.egg-info with no version in its name, in a source
    # directory on sys.path; distutils wrote a single FILE of that name
    src = td / "editable-src"
    src.mkdir()
    (src / "graphifyy.egg-info").mkdir()
    (src / "graphifyy.egg-info" / "PKG-INFO").write_text(
        "Metadata-Version: 2.1\nName: graphifyy\nVersion: 0.8.45\n\nVersion: 9.9 (in the description)\n",
        encoding="utf-8")
    (src / "PyYAML.egg-info").write_text("Metadata-Version: 1.1\nName: PyYAML\nVersion: 6.0.2\n", encoding="utf-8")
    got = B.installed_versions([str(src)])
    try:
        from importlib import metadata as md
        theirs = {B._canon(d.metadata["Name"]): d.version for d in md.distributions(path=[str(src)])}
    except Exception as exc:  # noqa: BLE001
        theirs = {"error": repr(exc)}
    check("a version-less <name>.egg-info (a legacy editable install; distutils' single-file form too) counts, its "
          "version read from its metadata headers — importlib.metadata's answer",
          got.get("graphifyy") == "0.8.45" and got.get("pyyaml") == "6.0.2"
          and {k: got.get(k) for k in theirs} == theirs and len(theirs) == 2, (got, theirs))
    m = B.missing([("graphifyy", ">=0.8.39,<0.8.52"), ("PyYAML", "")], paths=[str(src)])
    check("...so a Python that imports graphifyy from such an install is not sent to build DeepMap's environment",
          m == [], m)

    # the comparator: packaging's answer (when installed) and the self-check's, on the clauses requirements.txt uses
    cases = [(">=6.0.1,<7", v) for v in ("6.0.1", "6.0.0", "5.28.6", "6.3.1", "7.0.0", "6.2.0", "6.0.1.post1")]
    cases += [(">=1.24,<2", v) for v in ("1.24.0", "1.24", "1.23.3", "2.2.0", "1.30.0", "2.0.0rc1", "2.0")]
    cases += [(">=0.8.39,<0.8.52", v) for v in ("0.8.39", "0.8.51", "0.8.52", "0.9.68", "0.8.38", "0.8.40")]
    cases += [(">=4.5", v) for v in ("4.5", "4.9.0", "4.4.0", "4.15.1")]
    cases += [("==2.12.1", "2.12.1+cpu"), ("~=1.4.2", "1.4.5"), ("~=1.4.2", "1.5.0"), ("==1.4.*", "1.4.9"),
              ("==1.4.*", "1.5.0"), ("!=6.0.0", "6.0.0"), ("!=6.0.0", "6.0.1"), ("<=6.2", "6.2.0"), (">6.2", "6.2.0")]
    try:
        from packaging.specifiers import SpecifierSet
        from packaging.version import Version

        def pk(spec, v):
            return Version(v) in SpecifierSet(spec, prereleases=True)
    except Exception:
        pk = None
    sc = _load_selfcheck(root)
    disagree = []
    for spec, v in cases:
        mine = B.version_ok(spec, v)
        if pk is not None and mine != pk(spec, v):
            disagree.append(("packaging", spec, v, mine))
        if sc is not None and spec and not re.search(r"~=|\.\*", spec) and mine != sc.spec_contains(spec, v):
            disagree.append(("selfcheck", spec, v, mine))
    check(f"version_ok agrees with packaging{'' if pk else ' (not installed: skipped)'} and the self-check on "
          f"{len(cases)} cases (incl. a pre-release at a ceiling, a +local build, ~= and ==X.*)", not disagree,
          disagree)
    check("an unpinned requirement accepts any version, a junk version satisfies no range",
          B.version_ok("", "2.12.1+cpu") and not B.version_ok(">=4.5", "dev") and B.version_ok("", "dev"))

    # this very interpreter: missing() == what importlib.metadata (+ packaging) says
    try:
        from importlib import metadata as md
        wrong = []
        mine = {x["name"] for x in B.missing()}
        for name, spec in B.requirements():
            try:
                ver = md.version(name)
            except Exception:
                ver = None
            expect = ver is None or bool(spec and not (pk(spec, ver) if pk else B.version_ok(spec, ver)))
            if (name in mine) != expect:
                wrong.append((name, ver, spec, name in mine))
        check(f"on this interpreter missing() == importlib.metadata's verdict for all {len(B.requirements())} "
              "requirements", not wrong, wrong)
    except Exception as exc:  # noqa: BLE001
        check("importlib.metadata comparison ran", False, repr(exc))

    # cheap: no importlib.metadata, no packaging, well under a second
    code = ("import json, sys, time; sys.path.insert(0, sys.argv[1]); t = time.perf_counter(); import deps_bootstrap "
            "as B; B.missing(); dt = time.perf_counter() - t; print(json.dumps({'ms': dt * 1000, "
            "'meta': 'importlib.metadata' in sys.modules, 'pk': 'packaging' in sys.modules}))")
    r = subprocess.run([sys.executable, "-c", code, str(root / "build")], capture_output=True, text=True,
                       timeout=60, env=_clean_env())
    try:
        info = json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        info = {}
    check("import + missing() load neither importlib.metadata nor packaging (fast on every command)",
          info.get("meta") is False and info.get("pk") is False, r.stdout + r.stderr)
    check(f"...and take well under a second ({info.get('ms', -1):.0f} ms here)", 0 <= info.get("ms", -1) < 1000,
          info)


_PEP440_SPECS = (">=6.0.1,<7", ">=1.24,<2", ">=0.8.39,<0.8.52", ">=4.5", "==2.12.1", "~=1.4.2", "~=2.2", "==1.4.*",
                 "!=1.4.*", "!=6.0.0", "<=6.2", ">6.2", "<7", ">6.0.1", "==1.0+cpu", "===1.0", ">=1!1.0",
                 "~=1.4.2rc1", "<2.0rc1", ">=6.0.1rc2")
_PEP440_VERSIONS = ("6.0.1", "6.0.1rc1", "6.0.1rc2", "6.0.1.dev3", "6.0.1a1", "6.0.1.post1", "6.0.0", "6.0.0.post2",
                    "6.2.0", "7.0.0", "7.0.0rc1", "7.0.0.dev1", "6.99", "1.24", "1.24.0rc2", "2.0", "2.0.0rc1",
                    "2.0rc1", "2.0b1", "1.30.0", "0.8.39", "0.8.52", "0.8.52rc1", "0.8.51.post1", "4.5", "4.5.0a1",
                    "4.15.1", "2.12.1+cpu", "2.12.1", "2.12.1rc1", "1.4.2", "1.4.5", "1.4.0rc1", "1.4.2rc1",
                    "1.5.0", "1.4", "2.2", "2.3", "3.0", "6.2", "6.2.0.post1", "6.2.1", "6.2+local", "1.0+cpu",
                    "1.0+CPU", "1.0", "1!1.0", "1!0.5", "0.5", "dev", "1.0.0-1", "1.0.post", "v1.0", "1.0-rc.1",
                    "1.0_a1", "6.0.1-dev2", "6.2.0+cpu.2")


def gate_a_requirement_lines(B, root: Path, td: Path) -> None:
    print("\n-- A2: requirements.txt is read the way pip reads it (BOM, extras, markers), compared by PEP 440 --")
    text = (root / "requirements.txt").read_text(encoding="utf-8")
    bom = td / "req-bom.txt"
    bom.write_bytes(b"\xef\xbb\xbf" + text.encode("utf-8"))
    got = B.requirements(bom)
    # the shipped file opens with a comment, which hides a BOM read as text: a requirement first must survive it too
    body = "\n".join(ln for ln in text.splitlines() if ln.split("#", 1)[0].strip()) + "\n"
    bom2 = td / "req-bom-first.txt"
    bom2.write_bytes(b"\xef\xbb\xbf" + body.encode("utf-8"))
    got2 = B.requirements(bom2)
    check("a requirements.txt saved with a UTF-8 BOM (Windows PowerShell 5.1's -Encoding UTF8) reads exactly like the "
          "plain file — its first requirement included when no comment comes first",
          got == B.requirements() and got2 == B.requirements() and not any("﻿" in n for n, _ in got + got2),
          (got2[:2], B.requirements()[:2]))
    mixed = td / "req-mixed.txt"
    mixed.write_text("\n".join([
        "uvicorn[standard] >= 0.30   # extras",
        'pywin32>=306; sys_platform == "win32"',
        'pkgx>=1 ; sys_platform == "no-such-os"',
        'pkgy ; python_version >= "3.0" and (os_name != "no-such-os" or platform_system == "x")',
        "pkgz @ https://example.invalid/pkgz-1.0-py3-none-any.whl",
        'pkgq>=1; extra == "test"',
        "-r other-requirements.txt",
        "",
    ]) + "\n", encoding="utf-8")
    got = dict(B.requirements(mixed))
    want = {"uvicorn": "[standard]>=0.30", "pkgy": ""}
    if sys.platform == "win32":
        want["pywin32"] = ">=306"
    check("extras are kept for pip; a marker false here drops its line (pip skips it too), a true one leaves the "
          "clauses; a URL, an unknown marker or an option line is left out", got == want, got)
    have = td / "req-have"
    have.mkdir()
    for d in ("uvicorn-0.31.0.dist-info", "pkgy-2.0.dist-info", "pywin32-306.dist-info"):
        (have / d).mkdir()
    m = B.missing(B.requirements(mixed), paths=[str(have)])
    check("...so a Python holding every package that applies, in range, lacks nothing (no line nothing satisfies)",
          m == [], m)
    check("version_ok reads past an extras prefix ([standard]>=0.30 accepts 999.0, rejects 0.29)",
          B.version_ok("[standard]>=0.30", "999.0") and not B.version_ok("[standard]>=0.30", "0.29"))
    cmds = dict(B.install_commands("PY", B.requirements(mixed)))
    check("the extras reach pip, in the --upgrade command of the ranged specs",
          "uvicorn[standard]>=0.30" in cmds.get("pinned", []) and "pkgy" in cmds.get("packages", []), cmds)
    parse = getattr(B, "parse_requirement", None)
    bad = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if line and (line.startswith("-") or not callable(parse) or parse(line) is None):
            bad.append(line)
    check("every line of the shipped requirements.txt is inside that subset (name[extras] clauses ; marker — no "
          "option, -r include or URL), so an edit outside it fails here, not in every user's setup", not bad, bad)

    try:
        from packaging.specifiers import SpecifierSet
        from packaging.version import InvalidVersion, Version
    except Exception:  # noqa: BLE001
        print("  (packaging is not installed here: the PEP 440 comparison is skipped)")
        return
    dis, n = [], 0
    for spec in _PEP440_SPECS:
        for v in _PEP440_VERSIONS:
            n += 1
            try:
                want = Version(v) in SpecifierSet(spec, prereleases=True)
            except InvalidVersion:
                want = False
            got = B.version_ok(spec, v)
            if got != want:
                dis.append((spec, v, got, want))
    check(f"version_ok == packaging on {n} cases (pip's view of an INSTALLED version: pre-releases allowed) — "
          "6.0.1rc1 is not >=6.0.1, a post-release of 6.0.1 is not >6.0.1, locals, epochs, ~=, ==X.*, ===",
          not dis, dis[:12])


def _load_selfcheck(root: Path):
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("dmsb_selfcheck", root / "scripts" / "ml_stack_selfcheck.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


# --------------------------------------------------------------------------------------------------- B
def gate_b_paths(B, P, PR, td: Path) -> None:
    print("\n-- B: the managed environment lives at <DEEPMAP_HOME>/venv (provision's own DEEPMAP_HOME rule) --")
    saved = os.environ.get("DEEPMAP_HOME")
    try:
        for label, val in (("DEEPMAP_HOME set", str(td / "dmhome")), ("DEEPMAP_HOME unset", None)):
            if val is None:
                os.environ.pop("DEEPMAP_HOME", None)
            else:
                os.environ["DEEPMAP_HOME"] = val
            want = Path(os.path.abspath(P.deepmap_home() / "venv"))
            check(f"{label}: venv_dir() == provision.deepmap_home()/venv == port_registry.home_dir()/venv",
                  B.venv_dir() == want and Path(os.path.abspath(PR.home_dir() / "venv")) == want,
                  f"{B.venv_dir()} vs {want}")
            check(f"{label}: an explicit env mapping is read the same way",
                  B.venv_dir(dict(os.environ)) == want)
    finally:
        if saved is None:
            os.environ.pop("DEEPMAP_HOME", None)
        else:
            os.environ["DEEPMAP_HOME"] = saved
    v = td / "h" / "venv"
    check("Windows: <venv>/Scripts/python.exe", B.venv_python(v, windows=True) == v / "Scripts" / "python.exe")
    check("macOS / Linux: <venv>/bin/python", B.venv_python(v, windows=False) == v / "bin" / "python")
    check("default: this OS's layout", B.venv_python(v) == B.venv_python(v, windows=_WIN))
    check("the setup log sits next to the other DeepMap logs: <DEEPMAP_HOME>/venv-setup.log",
          B.log_path({"DEEPMAP_HOME": str(td / "h")}) == Path(os.path.abspath(td / "h" / "venv-setup.log")))


# --------------------------------------------------------------------------------------------------- C
def gate_c_specs(B, root: Path) -> None:
    print("\n-- C: the pip commands install exactly requirements.txt (ranges --upgrade; torch from the CPU index) --")
    reqs = B.requirements()
    cmds = B.install_commands("PY", reqs)
    keys = [k for k, _ in cmds]
    check("four pip commands, in the installers' order: pip, torch, the unpinned packages, the ranged ones",
          keys == ["pip", "torch", "packages", "pinned"], keys)
    argv = dict(cmds)
    pip_head = ["PY", "-m", "pip", "install"]
    check("every command is `<venv python> -m pip install ...` (never the user's Python, never a shell)",
          all(a[:4] == pip_head for a in argv.values()), argv)
    check("pip upgrades itself first (as the installers do)", argv["pip"][-2:] == ["--upgrade", "pip"], argv["pip"])
    t = argv["torch"]
    check("torch comes from https://download.pytorch.org/whl/cpu, alone in its command (the CPU build)",
          "--index-url" in t and t[t.index("--index-url") + 1] == "https://download.pytorch.org/whl/cpu"
          and [a for a in t[4:] if not a.startswith("-") and "://" not in a and a not in ("off",)] == ["torch"], t)
    ranged = [f"{n}{s}" for n, s in reqs if s and n != "torch"]
    bare = [n for n, s in reqs if not s and n != "torch"]
    specs = lambda a: [x for x in a[4:] if not x.startswith("-") and x != "off" and "://" not in x]  # noqa: E731
    check("the version-ranged specs, exactly as requirements.txt writes them, go in ONE --upgrade command",
          specs(argv["pinned"]) == ranged and "--upgrade" in argv["pinned"], argv["pinned"])
    check("the unpinned packages go without --upgrade (the installers keep an existing copy too)",
          specs(argv["packages"]) == bare and "--upgrade" not in argv["packages"], argv["packages"])
    flat = specs(argv["torch"]) + specs(argv["packages"]) + specs(argv["pinned"])
    check("each requirement is installed exactly once, and nothing else is",
          sorted(flat) == sorted([f"{n}{s}" for n, s in reqs]), flat)
    check("a repair with torch already present skips the torch command",
          [k for k, _ in B.install_commands("PY", reqs, torch=False)] == ["pip", "packages", "pinned"])
    src = (root / "build" / "deps_bootstrap.py").read_text(encoding="utf-8")
    copies = [s for n, s in reqs if s and s in src]
    check("build/deps_bootstrap.py holds no copy of a range (it reads requirements.txt)", not copies, copies)


# --------------------------------------------------------------------------------------------------- D
def gate_d_offline(B, td: Path) -> None:
    print("\n-- D: HF_HUB_OFFLINE is lifted for the model download only --")
    for label, parent in (("command env with HF_HUB_OFFLINE=1", {"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}),
                          ("terminal env without it", {})):
        home = td / f"d-{len(PASSES) + len(FAILS)}"
        world = _World(home / "venv", probe=[{"missing": []}], cached=[{"cached": False}])
        env = _home_env(home, NEO4J_PASSWORD="deepmap", **parent)
        before = dict(os.environ)
        py, out, err, exc = _ensure(B, world, _checks(B, world), env)
        check(f"{label}: a fresh environment is built (venv, pip x4, model, self-check) and its python returned",
              exc is None and py == str(B.venv_python(home / "venv"))
              and world.steps() == ["venv", "pip", "torch", "packages", "pinned", "model", "selfcheck"],
              f"{world.steps()} py={py} exc={exc!r} err={err[-600:]}")
        dl = world.step("model")
        denv = dl[3] if dl else {}
        check(f"{label}: the model download runs with HF_HUB_OFFLINE and TRANSFORMERS_OFFLINE removed, telemetry off",
              dl is not None and "HF_HUB_OFFLINE" not in denv and "TRANSFORMERS_OFFLINE" not in denv
              and denv.get("HF_HUB_DISABLE_TELEMETRY") == "1", {k: denv.get(k) for k in (
                  "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_HUB_DISABLE_TELEMETRY")})
        others = [(e[0], e[1], e[3].get("HF_HUB_OFFLINE")) for e in world.events
                  if e[0] in ("step", "capture") and e[1] != "model"]
        if parent:
            bad = [o for o in others if o[2] != "1"]
            check(f"{label}: every other step keeps HF_HUB_OFFLINE=1 (pip, probe, cache check, self-check)", not bad,
                  bad)
        forced = [(e[0], e[1], e[3].get("HF_HUB_OFFLINE")) for e in world.events
                  if e[0] in ("step", "capture") and e[1] in ("probe", "model-cached", "selfcheck")]
        check(f"{label}: the probe, the model-cache check and the self-check run offline (HF_HUB_OFFLINE=1, forced)",
              forced and all(f[2] == "1" for f in forced), forced)
        mod_argv = " ".join(dl[2]) if dl else ""
        try:
            import embeddings
            model = embeddings.MODEL_NAME
        except Exception:
            model = "?"
        check(f"{label}: the download names embeddings.MODEL_NAME and skips the ONNX / OpenVINO exports",
              model in mod_argv and "onnx/*" in mod_argv and "openvino/*" in mod_argv, mod_argv[:400])
        check(f"{label}: the caller's os.environ is unchanged", dict(os.environ) == before)
        check(f"{label}: the parent env passed in is not mutated", env.get("HF_HUB_OFFLINE") == parent.get(
            "HF_HUB_OFFLINE") and "DEEPMAP_REEXEC_GUARD" not in env)
    # pip reads PIP_<OPTION> from the environment: PIP_PYTHON (--python) re-runs pip under ANOTHER interpreter, which
    # would install into the user's Python; --target / --prefix / --root / --user would install beside the venv
    redirect = {"PIP_PYTHON": "C:/their/python.exe", "PIP_TARGET": "t", "PIP_PREFIX": "p", "PIP_ROOT": "r",
                "PIP_USER": "1", "PYTHONPATH": "x", "PYTHONHOME": "y", "__PYVENV_LAUNCHER__": "z"}
    e = B._tool_env(dict(redirect, PIP_INDEX_URL="https://mirror.example/simple", HTTPS_PROXY="http://proxy:3128"))
    check("every pip step drops what would point pip at another Python or prefix (PIP_PYTHON, PIP_TARGET, PIP_PREFIX, "
          "PIP_ROOT; PIP_USER=0) and keeps the user's index and proxy",
          not ({"PIP_PYTHON", "PIP_TARGET", "PIP_PREFIX", "PIP_ROOT", "PYTHONPATH", "PYTHONHOME",
                "__PYVENV_LAUNCHER__"} & set(e)) and e.get("PIP_USER") == "0"
          and e.get("PIP_INDEX_URL") == "https://mirror.example/simple" and e.get("HTTPS_PROXY") == "http://proxy:3128",
          {k: e.get(k) for k in (*redirect, "PIP_INDEX_URL", "HTTPS_PROXY")})
    home = td / "d-pip"
    world = _World(home / "venv", probe=[{"missing": []}], cached=[{"cached": True}])
    _ensure(B, world, _checks(B, world), _home_env(home, PIP_PYTHON="C:/their/python.exe", PIP_TARGET="t"))
    leaked = [(ev[1], k) for ev in world.events if ev[0] in ("step", "capture") for k in ("PIP_PYTHON", "PIP_TARGET")
              if k in ev[3]]
    check("...in a real build too: no step sees the caller's PIP_PYTHON / PIP_TARGET",
          "pinned" in world.steps() and not leaked, (world.steps(), leaked))


# --------------------------------------------------------------------------------------------------- E
def gate_e_prereqs(B, P, td: Path) -> None:
    print("\n-- E: census, Java, MAX_PATH and free disk are checked before anything is created or downloaded --")
    home = td / "e1"
    world = _World(home / "venv")
    jmsg = ("no Java on PATH. Neo4j 5.26 needs a JDK/JRE 17 or 21 — install Temurin 21 "
            "(https://adoptium.net/temurin/releases/?version=21) and reopen the shell.", "no Java on PATH")
    py, out, err, exc = _ensure(B, world, _checks(B, world, java=[jmsg]), _home_env(home))
    check("no Java: FAILED in seconds — nothing created, nothing downloaded (no step ran)",
          py is None and exc is None and world.steps() == [] and not (home / "venv").exists(),
          f"{world.steps()} exc={exc!r}")
    check("...printed in preflight's style + a FAILED verdict naming it",
          "PREFLIGHT FAILED: no Java on PATH" in err and "setup verdict: FAILED —" in err and "Java" in
          err.split("setup verdict: FAILED —", 1)[-1], err[-700:])

    home = td / "e2"
    world = _World(home / "venv")
    world.disk = _disk(world, free_gb=0.5)
    py, out, err, exc = _ensure(B, world, _checks(B, world, neo4j=True), _home_env(home))
    check("too little disk: FAILED before any step", py is None and world.steps() == [], world.steps())
    check("...the message says how much is free, how much is needed (packages / model / Neo4j) and the fix",
          re.search(r"only \d+(\.\d+)? GB free", err) and "needs about" in err and "Neo4j" in err
          and "DEEPMAP_HOME" in err and "setup verdict: FAILED —" in err, err[-900:])

    home = td / "e3"
    home.mkdir()
    world = _World(home / "venv", cached=[{"cached": False}])
    py, out, err, exc = _ensure(B, world, _checks(B, world, neo4j=True), _home_env(home))
    order = [e[0] if e[0] != "step" else "step:" + e[1] for e in world.events if e[0] in ("java", "disk", "step")]
    first_step = next((i for i, o in enumerate(order) if o.startswith("step:")), len(order))
    check("ordering: the Java check and the disk probe come before the first step",
          "java" in order[:first_step] and "disk" in order[:first_step], order)
    disk_paths = [e[1] for e in world.events if e[0] == "disk"]
    check("...the disk probe covers DeepMap's home (packages, Neo4j) and the Hugging Face cache (the model)",
          any(str(home) in p for p in disk_paths) and len(disk_paths) >= 1, disk_paths)

    home = td / "e4"
    world = _World(home / "venv")
    cen = ("setup verdict: FAILED — no source code DeepMap can index was found under X; nothing was started (no ports "
           "were allocated, no Neo4j was started). Run /deep-map:setup in a project directory that holds source code.")
    py, out, err, exc = _ensure(B, world, _checks(B, world, census=cen), _home_env(home))
    check("nothing to index: setup's own census verdict, before Java / disk / any step",
          py is None and world.steps() == [] and cen in err
          and not [e for e in world.events if e[0] in ("java", "disk")], (world.events, err[-400:]))

    home = td / ("e5-" + "x" * 150)
    world = _World(home / "venv")
    prob = B.venv_path_problem(home / "venv", windows=True, long_paths=False)
    check("Windows without long-path support: a DEEPMAP_HOME too long for torch's files is named (and the fix)",
          bool(prob) and "MAX_PATH" in prob and "DEEPMAP_HOME" in prob, prob)
    check("...not when long paths are on, nor off Windows, nor for a short home",
          B.venv_path_problem(home / "venv", windows=True, long_paths=True) is None
          and B.venv_path_problem(home / "venv", windows=False, long_paths=False) is None
          and B.venv_path_problem(td / "s" / "venv", windows=True, long_paths=False) is None)

    home = td / "e6"
    vdir = home / "venv"
    world = _World(vdir, probe=[{"missing": []}], cached=[{"cached": True}])
    world._venv_layout()
    _write_marker(vdir, True)
    py, out, err, exc = _ensure(B, world, _checks(B, world, java=[jmsg], neo4j=True), _home_env(home))
    check("a complete environment needs no download: no Java / disk check, no step (reused as is)",
          py == str(B.venv_python(vdir)) and world.steps() == []
          and not [e for e in world.events if e[0] in ("java", "disk")], (world.events, err[-400:]))

    # provision's real checks: Java only when it provisions a local Neo4j
    calls: list = []

    def fake_jv():
        calls.append(1)
        P._JAVA_DIAG = "missing"
        return None
    with _Patch(P, _java_version=fake_jv, _USER_SET_NEO4J_URI=True):
        ck = P._bootstrap_checks()
        r1, o1, e1, x1 = _quiet(ck.java_problems)
    check("operator NEO4J_URI (bring your own Neo4j): no Java check", r1 == [] and calls == [] and x1 is None,
          (r1, calls, x1))
    # the PATH wording ("no Java on PATH") of every OS but macOS, whatever the host (a Mac words a missing JDK its own
    # way: macos_java_intel_gates)
    with _Patch(P, _java_version=fake_jv, _USER_SET_NEO4J_URI=False, _is_macos=lambda: False):
        ck = P._bootstrap_checks()
        r2, o2, e2, x2 = _quiet(ck.java_problems)
    check("a local Neo4j: preflight's own Java rule and wording (one rule, two callers)",
          calls == [1] and r2 and r2[0][1] == "no Java on PATH" and "Temurin 21" in r2[0][0], (r2, calls))
    with _Patch(P, _java_version=lambda: (11, 'openjdk version "11.0.2"')):
        r3, *_ = _quiet(P._java_preflight_problems)
    with _Patch(P, _java_version=lambda: (21, 'openjdk version "21.0.1"')):
        r4, *_ = _quiet(P._java_preflight_problems)
    check("Java 11 -> unsupported (needs 17/21); Java 21 -> no problem",
          r3 and "needs 17/21" in r3[0][1] and r4 == [], (r3, r4))
    empty = td / "e-empty"
    empty.mkdir()
    (empty / "README.md").write_text("# docs only\n", encoding="utf-8")
    verdict = P._census_verdict(str(empty))
    with _Patch(P, apply_ports_for=lambda *a, **k: None, write_mcp_json=lambda *a, **k: None):
        res, so, se, ex = _quiet(P.setup, str(empty))
    check("provision._census_verdict is exactly the verdict setup() prints for a directory with no source",
          bool(verdict) and verdict in se and res is False, (verdict, se[-400:]))


# --------------------------------------------------------------------------------------------------- F
_CHILD = r'''import os, sys
code = int(sys.argv[1])
print("child-out", "|".join(sys.argv[2:]), flush=True)
print("child-err", os.environ.get("DEEPMAP_REEXEC_GUARD"), os.environ.get("PYTHONPATH", "<none>"),
      os.environ.get("PYTHONHOME", "<none>"), file=sys.stderr, flush=True)
sys.exit(code)
'''
_WRAP = r'''import sys
sys.path.insert(0, sys.argv[1])
import deps_bootstrap as B
print("wrapper-before", flush=True)
rc = B.reexec(sys.executable, sys.argv[2], sys.argv[3:])
sys.exit(97 if rc is None else rc)
'''


def gate_f_reexec(B, root: Path, td: Path) -> None:
    print("\n-- F: the re-run passes stdout, stderr and the exit code through (real processes) --")
    child = td / "child.py"
    child.write_text(_CHILD, encoding="utf-8")
    for code in (0, 1, 3, 4):
        env = _clean_env(PYTHONIOENCODING="utf-8", PYTHONPATH=str(td / "no-such-pythonpath"))
        r = subprocess.run([sys.executable, "-c", _WRAP, str(root / "build"), str(child), str(code), "a b", "ünï"],
                           capture_output=True, timeout=120, env=env)
        out = r.stdout.decode("utf-8", "replace")
        err = r.stderr.decode("utf-8", "replace")
        check(f"exit {code} passes through unchanged", r.returncode == code, f"rc={r.returncode} {out} {err}")
        if code == 4:
            check("stdout passes through, after the parent's own line, argv intact (spaces, non-ASCII)",
                  "wrapper-before" in out and "child-out a b|ünï" in out
                  and out.index("wrapper-before") < out.index("child-out"), out)
            check("stderr passes through; the child carries the loop guard and no PYTHONPATH / PYTHONHOME",
                  "child-err 1 <none> <none>" in err, err)
    e = B.child_env({"PYTHONPATH": "x", "PYTHONHOME": "y", "__PYVENV_LAUNCHER__": "z", "NEO4J_PASSWORD": "deepmap",
                     "HF_HUB_OFFLINE": "1", "DEEPMAP_HOME": "h"})
    check("child_env: guard set, the three interpreter-redirecting variables dropped, everything else kept",
          e.get("DEEPMAP_REEXEC_GUARD") == "1" and not ({"PYTHONPATH", "PYTHONHOME", "__PYVENV_LAUNCHER__"} & set(e))
          and e.get("NEO4J_PASSWORD") == "deepmap" and e.get("HF_HUB_OFFLINE") == "1" and e.get("DEEPMAP_HOME") == "h",
          e)
    seen: list = []

    def spawn(argv, env):
        seen.append((list(argv), dict(env)))
        return 4
    script = root / "build" / "deepmap_cli.py"
    with _Patch(B, missing=lambda *a, **k: [{"name": "neo4j", "spec": "", "found": None}],
                managed_python=lambda env=None: "/dm/venv/bin/python", _spawn_child=spawn):
        rc = B.reexec_entry(script, ["sync", ".", "--stale-cleanup"], env={"X": "1", "DEEPMAP_HOME": str(td / "f-h")})
    check("reexec_entry: argv = [managed python, (-X utf8), script, *argv]; sync's exit 4 returned unchanged",
          rc == 4 and seen and seen[0][0][0] == "/dm/venv/bin/python"
          and seen[0][0][-4:] == [str(script), "sync", ".", "--stale-cleanup"]
          and seen[0][1].get("DEEPMAP_REEXEC_GUARD") == "1", (rc, seen[:1]))
    for child_rc in (0, 1, 4):
        seen.clear()

        def spawn2(argv, env, _rc=child_rc):
            seen.append(list(argv))
            return _rc
        with _Patch(B, missing=lambda *a, **k: [{"name": "mcp", "spec": "", "found": None}],
                    ensure_env=lambda *a, **k: "/dm/venv/bin/python", _spawn_child=spawn2):
            rc, out, err, exc = _quiet(B.setup_entry, script, ["setup", "proj"], checks=B.SetupChecks(), env={})
        check(f"setup_entry: setup's exit {child_rc} under the managed interpreter is returned unchanged",
              rc == child_rc and seen and seen[0][-2:] == ["setup", "proj"], (rc, seen, exc))
    with _Patch(B, missing=lambda *a, **k: [{"name": "mcp", "spec": "", "found": None}],
                ensure_env=lambda *a, **k: "/dm/venv/bin/python", _spawn_child=lambda argv, env: None):
        rc, out, err, exc = _quiet(B.setup_entry, script, ["setup"], checks=B.SetupChecks(), env={})
    check("setup_entry: an environment that cannot be started -> FAILED, exit 1",
          rc == 1 and "setup verdict: FAILED —" in err, (rc, err[-300:]))

    def explode(*a, **k):
        raise PermissionError(13, "Access is denied", "/dm/venv-setup.log")
    with _Patch(B, missing=lambda *a, **k: [{"name": "mcp", "spec": "", "found": None}], ensure_env=explode,
                _spawn_child=lambda argv, env: 0):
        rc, out, err, exc = _quiet(B.setup_entry, script, ["setup"], checks=B.SetupChecks(), env={})
    check("setup_entry: an unexpected error (an unwritable DEEPMAP_HOME) -> a FAILED verdict naming it, exit 1, no "
          "traceback", rc == 1 and exc is None and "setup verdict: FAILED —" in err and "PermissionError" in err,
          (rc, exc, err[-300:]))


# --------------------------------------------------------------------------------------------------- G
def gate_g_guard(B, root: Path, td: Path) -> None:
    print("\n-- G: loop guard, opt-out, a complete Python, an incomplete environment: no bootstrap, no re-run --")
    # Every seam is a RECORDER that returns normally: an exception would be swallowed by the entry points' own
    # `except Exception: return None` and read as "not called". Each case leaves exactly one thing between the call
    # and a re-run (the guard, the opt-out, the missing() check, the same-interpreter check), so dropping that one
    # thing reaches _spawn_child and shows up here.
    calls: list = []

    def rec(name, ret=None):
        def f(*a, **k):
            calls.append(name)
            return ret
        return f
    lacks = [{"name": "neo4j", "spec": ">=6.0.1,<7", "found": None}]
    other_py = str(td / "g-managed" / ("python.exe" if _WIN else "python"))   # complete, and not this interpreter
    script = root / "build" / "provision.py"

    def run(fn, *a, miss=lacks, managed=other_py, **k):
        calls.clear()
        k["env"] = dict(k.get("env") or {}, DEEPMAP_HOME=str(td / "g-home"))   # never the machine's ~/.deepmap
        with _Patch(B, missing=rec("missing", miss), managed_python=rec("managed_python", managed),
                    ensure_env=rec("ensure_env", other_py), _spawn_child=rec("spawn", 0)):
            res = _quiet(fn, *a, **k)
        return res, list(calls)

    (r1, c1) = run(B.reexec_entry, script, ["status"], env={"DEEPMAP_REEXEC_GUARD": "1"})
    (r2, c2) = run(B.setup_entry, script, ["setup"], checks=B.SetupChecks(), env={"DEEPMAP_REEXEC_GUARD": "1"})
    check("DEEPMAP_REEXEC_GUARD=1 (the re-run child): no scan, no bootstrap, no re-run (None = run here)",
          r1[0] is None and r2[0] is None and c1 == [] and c2 == [], (r1[0], c1, r2[0], c2))
    (r3, c3) = run(B.reexec_entry, script, ["status"], env={"DEEPMAP_MANAGED_VENV": "0"})
    (r4, c4) = run(B.setup_entry, script, ["setup"], checks=B.SetupChecks(), env={"DEEPMAP_MANAGED_VENV": "0"})
    check("DEEPMAP_MANAGED_VENV=0: never creates or uses the environment (no scan, no build, no re-run)",
          r3[0] is None and r4[0] is None and c3 == [] and c4 == [], (r3[0], c3, r4[0], c4))
    (r5, c5) = run(B.setup_entry, script, ["setup"], checks=B.SetupChecks(), env={}, miss=[])
    (r6, c6) = run(B.reexec_entry, script, ["status"], env={}, miss=[])
    (r6b, c6b) = run(B.entry, script, [], checks=lambda: B.SetupChecks(), env={}, miss=[])
    check("a Python that has every requirement in range (an installer user), next to a COMPLETE environment: setup "
          "and every verb run here, exactly as before — no build, no re-run, no line printed",
          r5[0] is None and r6[0] is None and r6b[0] is None
          and not {"ensure_env", "spawn"} & set(c5 + c6 + c6b) and not (r5[1] + r5[2] + r6[1] + r6[2]),
          (c5, c6, c6b))
    (r7, c7) = run(B.reexec_entry, script, ["status"], env={}, managed=None)
    check("no complete managed environment: the verb runs here, silently (no stray output for CI / gates)",
          r7[0] is None and "spawn" not in c7 and not (r7[1] + r7[2]), (r7, c7))
    (r8, c8) = run(B.reexec_entry, script, ["status"], env={}, managed=sys.executable)
    check("the managed interpreter IS this one: no re-run (no self-loop)", r8[0] is None and "spawn" not in c8,
          (r8[0], c8))
    (r9, c9) = run(B.reexec_entry, script, ["status"], env={})
    (r10, c10) = run(B.setup_entry, script, ["setup"], checks=B.SetupChecks(), env={})
    check("controls: with nothing in the way the recorders DO see the re-run (verb) and the build + re-run (setup)",
          r9[0] == 0 and c9[-1:] == ["spawn"] and r10[0] == 0 and "ensure_env" in c10 and c10[-1:] == ["spawn"],
          (r9[0], c9, r10[0], c10))


# --------------------------------------------------------------------------------------------------- P
def gate_p_unstartable(B, root: Path, td: Path) -> None:
    print("\n-- P: a complete environment whose interpreter cannot start is never handed a command; one that starts "
          "always is --")

    def make(name, *, cfg=None, base_files=None, bom=False):
        """A COMPLETE environment (marker) made from <home>/base-python; cfg: pyvenv.cfg's lines ('{base}' is that
        directory; None: as `-m venv` writes it, False: no pyvenv.cfg); base_files: what that directory still holds
        (None: as installed); bom: pyvenv.cfg saved with a UTF-8 BOM."""
        home = td / f"p-{name}"
        vdir, base = home / "venv", home / "base-python"
        _World(vdir, base_home=base)._venv_layout()
        if base_files is not None:
            for f in base.iterdir():
                f.unlink()
            for f in base_files:
                (base / f).write_bytes(b"")
        if cfg is False:
            (vdir / "pyvenv.cfg").unlink()
        elif cfg is not None:
            (vdir / "pyvenv.cfg").write_text("".join(ln.format(base=base) + "\n" for ln in cfg), encoding="utf-8")
        if bom:
            (vdir / "pyvenv.cfg").write_bytes(b"\xef\xbb\xbf" + (vdir / "pyvenv.cfg").read_bytes())
        _write_marker(vdir, True)
        return {"DEEPMAP_HOME": str(home)}, vdir

    ok_env, ok_vdir = make("ok")
    check("control: a complete environment made from a Python that is still installed is used",
          B.managed_python(ok_env) == str(B.venv_python(ok_vdir)), B.managed_python(ok_env))
    # What each OS's real venv does with these shapes (section J checks the rule against a real one): Windows' launcher
    # finds `home` case-sensitively anywhere a BOM may precede it, and runs <home>\python.exe (the free-threaded
    # launcher: python3.Xt.exe); a POSIX venv runs whatever bin/python links to, and site.py activates the venv from
    # pyvenv.cfg whatever its `home` says (CPython 3.10, 3.11, 3.13, 3.14 measured: missing, empty, relative or gone).
    unused = [("no pyvenv.cfg (Windows' venv launcher exits 106 'failed to locate pyvenv.cfg'; on Linux / macOS its "
               "python starts as the base Python, outside the environment)", make("nocfg", cfg=False))]
    used = [("a pyvenv.cfg saved with a UTF-8 BOM (PowerShell 5.1's Set-Content -Encoding UTF8, older Notepad): the "
             "launcher and the interpreter read it all the same", make("bom", bom=True))]
    if _WIN:
        unused += [("a pyvenv.cfg naming no home (the launcher exits 107)",
                    make("nohome", cfg=["include-system-site-packages = false", "version = 3.13.14"])),
                   ("an empty `home =` (the launcher reads nothing there)",
                    make("emptyhome", cfg=["home =", "version = 3.13.14"])),
                   ("its key spelled `Home` (the launcher looks for `home`, case-sensitive: exit 107)",
                    make("Home", cfg=["Home = {base}", "version = 3.13.14"])),
                   ("its base Python uninstalled with the directory left behind (`home` exists without python.exe: "
                    "the launcher exits 103 'did not find executable')", make("gone", base_files=["notes.txt"])),
                   ("a free-threaded venv whose base kept a python.exe but lost the python3.13t.exe its launcher runs",
                    make("ftgone", cfg=["home = {base}", "executable = {base}\\python3.13t.exe"],
                         base_files=["python.exe"]))]
        used += [("a free-threaded venv, whose launcher runs the python3.13t.exe its pyvenv.cfg names",
                  make("ft", cfg=["home = {base}", "executable = {base}\\python3.13t.exe"],
                       base_files=["python3.13t.exe"]))]
    else:
        env, vdir = make("dangling")
        link = B.venv_python(vdir)
        link.unlink()
        os.symlink(str(vdir.parent / "base-python" / "python3.no-such"), str(link))
        unused.append(("its bin/python a link to a base Python that is gone", (env, vdir)))
        used += [("a pyvenv.cfg naming no home", make("nohome", cfg=["include-system-site-packages = false",
                                                                      "version = 3.11.16"])),
                 ("an empty `home =`", make("emptyhome", cfg=["home =", "version = 3.11.16"])),
                 ("a `home` that no longer exists while bin/python still resolves",
                  make("homegone", cfg=["home = /opt/dm-no-such-python/bin", "version = 3.11.16"])),
                 ("a relative `home` (read the same from any working directory)",
                  make("relhome", cfg=["home = usr/local/bin", "version = 3.11.16"]))]
    for label, (env, vdir) in unused:
        got = B.managed_python(env)
        check(f"marker complete, but {label}: not used", got is None, got)
    for label, (env, vdir) in used:
        got = B.managed_python(env)
        check(f"marker complete and {label}: used (never stricter than the real venv — a bare python running its "
              "commands in place while setup reuses the environment would loop)", got == str(B.venv_python(vdir)), got)

    spawned: list = []
    lacks = [{"name": "neo4j", "spec": ">=6.0.1,<7", "found": None}]
    script = root / "build" / "deepmap_cli.py"
    runs = []
    for env, _vdir in [(ok_env, ok_vdir)] + [c[1] for c in used] + [c[1] for c in unused]:
        spawned.clear()
        with _Patch(B, missing=lambda *a, **k: lacks, _spawn_child=lambda argv, e: spawned.append(argv) or 0):
            res = _quiet(B.reexec_entry, script, ["doctor"], env=dict(env))
        runs.append((res[0], len(spawned), res[1] + res[2]))
    n_used = 1 + len(used)
    check("a bare Python's command runs HERE (no re-run, nothing printed) in every case not used — so doctor names "
          "/deep-map:setup and the `mcp-stop` a setup refusal names works — and is re-run in every case used",
          all(r[:2] == (0, 1) for r in runs[:n_used]) and all(r == (None, 0, "") for r in runs[n_used:]), runs)


# --------------------------------------------------------------------------------------------------- Q
_Q_IMPORTS = r'''import json, sys
sys.path.insert(0, sys.argv[1])
import deepmap_hook_state as HS
words, source = HS.heal_interpreter(sys.argv[2])
print(json.dumps({"source": source, "imported": "deps_bootstrap" in sys.modules}))
'''


def gate_q_heal(B, root: Path, td: Path) -> None:
    print("\n-- Q: the SessionStart heal starts DeepMap's environment only when the commands would use it --")
    import importlib
    if str(root / "hooks") not in sys.path:
        sys.path.insert(0, str(root / "hooks"))
    try:
        HS = importlib.import_module("deepmap_hook_state")
    except Exception as exc:  # noqa: BLE001
        check("hooks/deepmap_hook_state imports", False, repr(exc))
        return
    home = td / "q-home"
    vdir = home / "venv"
    _World(vdir, base_home=home / "base-python")._venv_layout()
    _write_marker(vdir, True)
    py = B.venv_python(vdir)
    if not _WIN:
        os.chmod(py, 0o755)   # the hook starts only an executable file
    proj = td / "q-proj"
    (proj / ".git").mkdir(parents=True)   # the record must lie outside the project's own tree
    (proj / ".deepmap").mkdir()
    rec = home / "run" / f"setup-python-{HS.setup_python_root_id(proj)}.json"
    rec.parent.mkdir(parents=True, exist_ok=True)
    rec.write_text(json.dumps({"python": str(py), "root": str(proj)}), encoding="utf-8")
    saved = {k: os.environ.get(k) for k in ("DEEPMAP_HOME", "DEEPMAP_PYTHON")}

    def pick(**env):
        os.environ["DEEPMAP_HOME"] = str(home)
        os.environ.pop("DEEPMAP_PYTHON", None)
        os.environ.update(env)
        return HS.heal_interpreter(proj)
    try:
        got = pick()
        check("control: setup's recorded interpreter, DeepMap's complete environment, is what heal starts",
              got == ([str(py)], "setup"), got)
        cfg = vdir / "pyvenv.cfg"
        cfg_bytes = cfg.read_bytes()
        cfg.unlink()
        got, got_env = pick(), pick(DEEPMAP_PYTHON=str(py))
        cfg.write_bytes(cfg_bytes)
        check("its interpreter cannot start (pyvenv.cfg gone): heal runs under the hook's own Python — whose heal "
              "records 'run /deep-map:setup' — never the venv launcher, which exits at once and records nothing",
              got == ([sys.executable], "hook"), got)
        check("...also when DEEPMAP_PYTHON names that interpreter (one rule: the commands' own)",
              got_env == ([sys.executable], "hook"), got_env)
        _write_marker(vdir, False)
        got = pick()
        _write_marker(vdir, True)
        check("an environment setup has not finished (marker incomplete): the hook's own Python, as for every command",
              got == ([sys.executable], "hook"), got)
        other = td / "q-other" / ("python.exe" if _WIN else "python3")
        other.parent.mkdir(parents=True)
        other.write_bytes(b"")
        if not _WIN:
            os.chmod(other, 0o755)
        rec.write_text(json.dumps({"python": str(other), "root": str(proj)}), encoding="utf-8")
        got = pick()
        check("another recorded interpreter (an installer's Python) is untouched by this rule",
              got == ([str(other)], "setup"), got)
        env = _clean_env(DEEPMAP_HOME=str(home), PYTHONIOENCODING="utf-8")
        r = subprocess.run([sys.executable, "-c", _Q_IMPORTS, str(root / "hooks"), str(proj)], capture_output=True,
                           text=True, timeout=120, env=env)
        info = B._last_json(r.stdout) or {}
        rec.write_text(json.dumps({"python": str(py), "root": str(proj)}), encoding="utf-8")
        r2 = subprocess.run([sys.executable, "-c", _Q_IMPORTS, str(root / "hooks"), str(proj)], capture_output=True,
                            text=True, timeout=120, env=env)
        info2 = B._last_json(r2.stdout) or {}
        check("...and the hook imports deps_bootstrap only for DeepMap's own interpreter (it runs on the session-start "
              "clock)", info.get("source") == "setup" and info.get("imported") is False
              and info2.get("source") == "setup" and info2.get("imported") is True,
              (info, info2, r.stderr[-300:], r2.stderr[-300:]))
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# --------------------------------------------------------------------------------------------------- N
def gate_n_stale(B, td: Path) -> None:
    print("\n-- N: an environment finished against another requirements.txt that lacks today's: one note per "
          "command --")
    home = td / "n1"
    vdir = home / "venv"
    site = (vdir / "Lib" / "site-packages" if _WIN
            else vdir / "lib" / f"python{sys.version_info[0]}.{sys.version_info[1]}" / "site-packages")
    site.mkdir(parents=True)
    for name, spec in B.requirements():
        cands = ["1.0", "99.0"] + re.findall(r"\d+(?:\.\d+)*", spec)
        ver = next((c for c in cands if B.version_ok(spec, c)), "1.0")
        if name == "mcp":
            ver = "1.0.0"   # what an older plugin's environment holds, below today's floor
        (site / f"{re.sub(r'[-.]+', '_', name)}-{ver}.dist-info").mkdir()
    marker = vdir / "deepmap-venv.json"
    env = {"DEEPMAP_HOME": str(home)}
    fn = getattr(B, "stale_note", None)

    def note(sha):
        marker.write_text(json.dumps({"complete": True, "requirements_sha256": sha}), encoding="utf-8")
        return fn(env) if callable(fn) else None
    import hashlib
    today = hashlib.sha256((B.ROOT / "requirements.txt").read_bytes()).hexdigest()
    n_old, n_now = note("0" * 64), note(today)
    check("finished against another requirements.txt and below today's mcp floor: one line naming mcp, its version "
          "and /deep-map:setup", isinstance(n_old, str) and "mcp 1.0.0" in n_old and "/deep-map:setup" in n_old
          and "\n" not in n_old, n_old)
    check("...nothing when the environment was finished against this very requirements.txt", n_now is None, n_now)
    (site / "mcp-1.0.0.dist-info").rename(site / "mcp-1.30.0.dist-info")
    n_fits = note("0" * 64)
    check("...nor when the requirements changed but it still holds every one in range (a comment-only change)",
          n_fits is None, n_fits)
    (site / "mcp-1.30.0.dist-info").rename(site / "mcp-1.0.0.dist-info")
    note("0" * 64)
    spawned: list = []
    lacks = [{"name": "neo4j", "spec": ">=6.0.1,<7", "found": None}]
    real_missing = B.missing

    def miss(reqs=None, paths=None):   # this Python lacks neo4j; the environment's own directories, as they are
        return real_missing(reqs, paths) if paths is not None else lacks
    with _Patch(B, missing=miss, managed_python=lambda e=None: str(B.venv_python(vdir)),
                _spawn_child=lambda argv, e: spawned.append(argv) or 0):
        rc, out, err, exc = _quiet(B.reexec_entry, B.ROOT / "build" / "deepmap_cli.py", ["status"], env=env)
    check("a command a bare Python hands to that environment prints the note on stderr, once, and still runs there "
          "(exit code unchanged, stdout untouched)", rc == 0 and len(spawned) == 1 and err.count("/deep-map:setup") == 1
          and "mcp 1.0.0" in err and out == "", (rc, spawned, out, err))
    # /deep-map:setup then repairs it and a step fails, leaving it no worse: it stays in use (section H), and it still
    # predates today's requirements, so the note must keep naming mcp until a setup finishes the repair
    oor = [{"name": "mcp", "spec": dict(B.requirements()).get("mcp", ""), "found": "1.0.0"}]
    w = _World(vdir, probe=[{"missing": oor, "pip": True}, {"missing": oor, "pip": True}], cached=[{"cached": True}],
               rc={"pinned": 1}, logtext={"pinned": "HTTPSConnectionPool(host='pypi.org'): Read timed out.\n"})
    w._venv_layout()
    note("0" * 64)
    py, out, err, exc = _ensure(B, w, _checks(B, w), dict(env))
    n_kept = fn(env) if callable(fn) else None
    check("...and after a repair that failed and left it no worse (still in use, repair pending), the note still "
          "names mcp — that setup did not bring it up to today's requirements",
          py is None and "pinned" in w.steps() and B.managed_python(env) == str(B.venv_python(vdir))
          and isinstance(n_kept, str) and "mcp 1.0.0" in n_kept, (n_kept, B._read_marker(vdir), err[-300:]))


# --------------------------------------------------------------------------------------------------- H
def gate_h_repair(B, td: Path) -> None:
    print("\n-- H: idempotent — a complete environment is reused; a broken or out-of-range one is repaired --")
    home = td / "h1"
    world = _World(home / "venv", probe=[{"missing": []}], cached=[{"cached": False}])
    env = _home_env(home, HF_HUB_OFFLINE="1")
    py1, *_ = _ensure(B, world, _checks(B, world), env)
    check("first setup: built; the marker says complete", py1 and B.managed_python(env) == py1,
          (py1, B.managed_python(env)))
    world2 = _World(home / "venv", probe=[{"missing": []}], cached=[{"cached": True}])
    py2, out, err, exc = _ensure(B, world2, _checks(B, world2), env)
    check("second setup: reused as is — no venv / pip / download / self-check, only the probe + the cache check",
          py2 == py1 and world2.steps() == [] and [e[1] for e in world2.events if e[0] == "capture"] == [
              "probe", "model-cached"], world2.events)

    home = td / "h2"
    vdir = home / "venv"
    w = _World(vdir, probe=[{"missing": [{"name": "mcp", "spec": ">=1.24,<2", "found": "2.2.0"}]}, {"missing": []}],
               cached=[{"cached": True}])
    w._venv_layout()
    _write_marker(vdir, True)
    py, out, err, exc = _ensure(B, w, _checks(B, w), _home_env(home))
    check("an out-of-range mcp in the environment is repaired: pip + packages + ranged (--upgrade), the model "
          "check, the self-check; torch is not re-installed",
          py and w.steps() == ["pip", "packages", "pinned", "model", "selfcheck"], w.steps())
    during = w.marker_at.get("pip") or {}
    check("...while it repairs, the commands keep using it (complete, repair pending); complete afterwards",
          during.get("complete") is True and during.get("repair_pending") is True
          and B.managed_python(_home_env(home)) == py and not (B._read_marker(vdir) or {}).get("repair_pending"),
          (w.marker_at, B._read_marker(vdir)))
    check("...and the repair is announced with what was wrong", "mcp" in out + err and "2.2.0" in out + err,
          (out + err)[-500:])

    home = td / "h3"
    vdir = home / "venv"
    w = _World(vdir, probe=[None, {"missing": []}], cached=[{"cached": True}])
    _complete_env(vdir, w)
    (vdir / "stale.txt").write_text("old", encoding="utf-8")
    py, out, err, exc = _ensure(B, w, _checks(B, w), _home_env(home))
    st = w.step("venv")
    check("a broken environment DeepMap made (its python no longer runs) is moved aside and re-created, then filled "
          "— never `--clear`ed in place (on Windows that half-deletes an environment a running server holds)",
          py and st is not None and "--clear" not in st[2] and w.steps()[:3] == ["venv", "pip", "torch"]
          and not (vdir / "stale.txt").exists() and _leftovers(home) == [], (w.steps(), _leftovers(home)))

    home = td / "h4"
    vdir = home / "venv"
    w = _World(vdir, probe=[None])
    w._venv_layout()   # a venv with no DeepMap marker
    py, out, err, exc = _ensure(B, w, _checks(B, w), _home_env(home))
    check("a broken venv DeepMap did NOT make (no marker): refused, never cleared", py is None and w.steps() == []
          and (vdir / "pyvenv.cfg").exists() and "setup verdict: FAILED —" in err, (w.steps(), err[-400:]))

    home = td / "h12"
    vdir = home / "venv"
    w = _World(vdir, probe=[{"missing": [{"name": "neo4j", "spec": ">=6.0.1,<7", "found": None}], "pip": True}],
               cached=[{"cached": True}])
    w._venv_layout()   # someone's own WORKING venv, e.g. <project>/venv under an empty DEEPMAP_HOME
    py, out, err, exc = _ensure(B, w, _checks(B, w), _home_env(home))
    check("a WORKING virtual environment DeepMap did not make (no marker) is refused too: nothing is installed into "
          "it and no marker is written there", py is None and w.steps() == []
          and not (vdir / "deepmap-venv.json").exists() and "did not make" in err, (w.steps(), err[-400:]))
    check("...the verdict says to move it aside or point DEEPMAP_HOME elsewhere",
          "DEEPMAP_HOME" in err and "setup verdict: FAILED —" in err, err[-400:])

    home = td / "h13"
    w = _World(home / "venv")
    py, out, err, exc = _ensure(B, w, _checks(B, w), _home_env(home))
    mk = w.marker_at.get("venv")
    check("the incomplete marker is written BEFORE `-m venv` runs: a creation killed half-way is still DeepMap's",
          py and isinstance(mk, dict) and mk.get("complete") is False, w.marker_at)

    for label, value in (("empty (export DEEPMAP_HOME=$UNSET)", ""), ("blank", "  ")):
        cwd = td / f"h14-cwd-{len(value)}"
        cwd.mkdir()
        w = _World(cwd / "venv")
        saved = os.getcwd()
        os.chdir(cwd)
        try:
            py, out, err, exc = _ensure(B, w, _checks(B, w), _clean_env(DEEPMAP_HOME=value))
        finally:
            os.chdir(saved)
        check(f"DEEPMAP_HOME set but {label}: FAILED naming it — no environment, lock or log in the current "
              "directory (it would have been the project's own venv/)", py is None and w.steps() == []
              and not list(cwd.iterdir()) and "DEEPMAP_HOME" in err and "setup verdict: FAILED —" in err,
              (w.steps(), sorted(p.name for p in cwd.iterdir()), err[-400:]))

    home = td / "h15"
    vdir = home / "venv"
    w = _World(vdir, probe=["TIMEOUT"], cached=[{"cached": True}])
    _complete_env(vdir, w)
    py, out, err, exc = _ensure(B, w, _checks(B, w), _home_env(home))
    check("a probe that does not answer in time is a busy machine, not a broken environment: FAILED asking for a "
          "re-run, nothing re-created, still complete", py is None and w.steps() == []
          and (vdir / "pyvenv.cfg").exists() and (B._read_marker(vdir) or {}).get("complete") is True
          and "setup verdict: FAILED —" in err and "/deep-map:setup" in err, (w.steps(), err[-400:]))

    home = td / "h16"
    vdir = home / "venv"
    w = _World(vdir, probe=[None])
    _complete_env(vdir, w)
    log16 = _seed_log(home)

    def held(path):
        raise PermissionError(13, "Access is denied", str(path))
    py, out, err, exc = _ensure(B, w, _checks(B, w), _home_env(home), extra={"_move_aside": held})
    check("an environment that cannot be moved aside (permissions, security software, a file held open) is left as "
          "it is: FAILED naming `mcp-stop`", py is None and w.steps() == [] and (vdir / "pyvenv.cfg").exists()
          and "mcp-stop" in err and "close the Claude Code" not in err and "close those Claude Code" not in err,
          (w.steps(), err[-500:]))
    check("...and 'Nothing was changed' holds for the last build's log too: venv-setup.log is left as it was, and no "
          "'details:' line points at it", _log_kept(home, log16, out + err), (out[-300:], (home / "venv-setup.log")
                                                                               .read_bytes()[:80]))

    # Every project's MCP servers run from this one environment, and nothing stops a move (or a clear) from under them:
    # measured 2026-10-05 on Windows 11, a venv directory renames while a process runs from it with a .pyd loaded, and
    # deleting the moved copy then removes all but the mapped files. So the process table is asked, and an answer it
    # cannot give is a refusal, never "nobody".
    home = td / "h20"
    vdir = home / "venv"
    w = _World(vdir, probe=[None])
    _complete_env(vdir, w)
    (vdir / "stale.txt").write_text("old", encoding="utf-8")
    log20 = _seed_log(home)
    moved = []
    py, out, err, exc = _ensure(B, w, _checks(B, w), _home_env(home),
                                extra={"_processes_using": lambda v: None,
                                       "_move_aside": lambda v: moved.append(v) or home / "venv.not-moved"})
    verdict = err.split("setup verdict: FAILED —", 1)[-1]
    check("the process table cannot be read (no PowerShell, no /proc, no ps): the broken environment is NOT moved or "
          "rebuilt — FAILED naming mcp-stop and the directory to delete by hand once nothing runs from it",
          py is None and not moved and w.steps() == [] and (vdir / "stale.txt").exists() and _leftovers(home) == []
          and "mcp-stop" in verdict and str(vdir) in verdict and "Nothing was changed" in verdict, verdict[-700:])
    check("...the last build's log left as it was (no 'details:' line)", _log_kept(home, log20, out + err),
          out[-300:])
    table = getattr(B, "_process_table", None)
    if callable(table):
        with _Patch(B, _process_table=lambda: None):
            r1 = B._processes_using(vdir)
        with _Patch(B, _process_table=lambda: [("4242", f'"{B.venv_python(vdir)}" -X utf8 x.py'), ("7", "other")]):
            r2 = B._processes_using(vdir)
        check("_processes_using: None when the OS cannot be asked (not 'nobody'); the matching rows when it can",
              r1 is None and r2 == [(4242, f'"{B.venv_python(vdir)}" -X utf8 x.py')], (r1, r2))
    else:
        check("_processes_using reads the process table through _process_table (None when it cannot be read)", False,
              "no _process_table")
    home = td / "h19"
    vdir = home / "venv"
    w = _World(vdir, probe=[None])
    _complete_env(vdir, w)
    (vdir / "stale.txt").write_text("old", encoding="utf-8")
    (home / "run").mkdir(parents=True)
    (home / "run" / "deepmap-search-8899.owner").write_text(json.dumps({"pid": 4242, "root": "C:/work/proj-a"}),
                                                            encoding="utf-8")
    server = f'"{B.venv_python(vdir)}" "C:/plugin/build/deepmap_search_server.py"'
    seen: list = []
    moved: list = []
    log19 = _seed_log(home)

    def users(v):
        seen.append(str(v))
        return [(4242, server), (5151, f'"{B.venv_python(vdir)}" "C:/plugin/build/deepmap_cli.py" sync .')]
    py, out, err, exc = _ensure(B, w, _checks(B, w), _home_env(home),
                                extra={"_processes_using": users,
                                       "_move_aside": lambda v: moved.append(v) or home / "venv.not-moved"})
    verdict = err.split("setup verdict: FAILED —", 1)[-1]
    check("a broken environment that processes still run from (a project's MCP server, a sync) is NOT moved, cleared "
          "or rebuilt: FAILED, nothing changed", py is None and seen == [str(vdir)] and not moved and w.steps() == []
          and (vdir / "stale.txt").exists() and (vdir / "pyvenv.cfg").exists() and _leftovers(home) == [],
          (seen, moved, w.steps(), err[-400:]))
    check("...nothing at all: the last build's log — the only record of an earlier failure — is left as it was, and no "
          "'details:' line points at an emptied file", _log_kept(home, log19, out + err),
          (out[-300:], (home / "venv-setup.log").read_bytes()[:80]))
    check("...the verdict names each process (pid, script, the server's project from its owner record) and the "
          "`provision.py mcp-stop \"<project>\"` that stops it — never 'close Claude Code' (the servers are detached: "
          "they outlive it)", "4242" in verdict and "deepmap_search_server.py" in verdict and "5151" in verdict
          and 'mcp-stop "C:/work/proj-a"' in verdict and "Nothing was changed" in verdict
          and "close the Claude Code" not in verdict, verdict[-700:])
    # Its Python does not run (the probe), yet its files look intact, so a bare python would still hand that very stop
    # command to it and fail: the command must run with the Python the user has, whatever DeepMap's own check decides.
    optout = f'`DEEPMAP_MANAGED_VENV=0 "{Path(sys.executable).as_posix()}" "'
    check("...and since its Python does not run while its files look intact, that stop command carries "
          "DEEPMAP_MANAGED_VENV=0: it runs with this Python, never handed to the environment that cannot start it",
          optout in verdict and 'mcp-stop "C:/work/proj-a"`' in verdict, verdict[-700:])
    for label, case in (("its pyvenv.cfg is gone (its interpreter visibly cannot start)", "nocfg"),
                        ("it only lacks pip (its Python runs, and so does the stop command under it)", "nopip")):
        home = td / f"h21-{case}"
        vdir = home / "venv"
        w = _World(vdir, probe=[{"missing": [], "pip": False}] if case == "nopip" else [None])
        _complete_env(vdir, w)
        if case == "nocfg":
            (vdir / "pyvenv.cfg").unlink()
        (home / "run").mkdir(parents=True)
        (home / "run" / "deepmap-search-8899.owner").write_text(json.dumps({"pid": 4242, "root": "C:/work/proj-a"}),
                                                                encoding="utf-8")
        held_by = [(4242, f'"{B.venv_python(vdir)}" "C:/plugin/build/deepmap_search_server.py"')]
        py, out, err, exc = _ensure(B, w, _checks(B, w), _home_env(home),
                                    extra={"_processes_using": lambda v, rows=held_by: rows})
        verdict = err.split("setup verdict: FAILED —", 1)[-1]
        check(f"...not when {label}: the plain stop command, as a terminal runs it",
              py is None and 'mcp-stop "C:/work/proj-a"' in verdict and "DEEPMAP_MANAGED_VENV" not in verdict,
              verdict[-600:])

    # A base Python upgraded in place to another minor version (scoop's apps\python\current after `scoop update python`,
    # a conda base, a fixed install directory): on Windows every package still looks installed (Lib\site-packages
    # carries no version), but none of their compiled parts load (pydantic_core, so mcp; torch; numpy). The marker
    # records the version the environment was finished with; the probe reports the one its Python runs now.
    now = {"missing": [], "pip": True, "version": "3.13.14"}
    home = td / "h28"
    vdir = home / "venv"
    w = _World(vdir, probe=[now, now], cached=[{"cached": True}])
    _complete_env(vdir, w)
    _write_marker(vdir, True, python_version="3.12.10")
    (vdir / "stale.txt").write_text("old", encoding="utf-8")
    py, out, err, exc = _ensure(B, w, _checks(B, w), _home_env(home))
    check("its Python moved from 3.12 to 3.13 under it (every package still listed, no compiled part loads): "
          "re-created — moved aside, a fresh venv, every package installed again — never 'reused as is'",
          py and w.steps()[:3] == ["venv", "pip", "torch"] and not (vdir / "stale.txt").exists()
          and _leftovers(home) == [], (w.steps(), (out + err)[-500:]))
    check("...saying why in its own words (the version it was finished with, the one it runs now)",
          "changed from 3.12 to 3.13" in out + err, (out + err)[-600:])
    check("...and the fresh environment records the version it runs now",
          (B._read_marker(vdir) or {}).get("python_version") == "3.13.14", B._read_marker(vdir))
    for label, made in (("a patch release of the same minor version (3.13.2 -> 3.13.14)", "3.13.2"),
                        ("no version recorded (an older marker)", None)):
        home = td / f"h28-{made or 'none'}"
        vdir = home / "venv"
        w = _World(vdir, probe=[now], cached=[{"cached": True}])
        _complete_env(vdir, w)
        if made:
            _write_marker(vdir, True, python_version=made)
        py, out, err, exc = _ensure(B, w, _checks(B, w), _home_env(home))
        check(f"...while {label} is reused as is", py == str(B.venv_python(vdir)) and w.steps() == [],
              (w.steps(), (out + err)[-300:]))
    home = td / "h29"
    vdir = home / "venv"
    w = _World(vdir, probe=[now], cached=[{"cached": True}])
    _complete_env(vdir, w)
    _write_marker(vdir, True, python_version="3.12.10")
    (home / "run").mkdir(parents=True)
    (home / "run" / "deepmap-search-8899.owner").write_text(json.dumps({"pid": 4242, "root": "C:/work/proj-a"}),
                                                            encoding="utf-8")
    log29 = _seed_log(home)
    rows29 = [(4242, f'"{B.venv_python(vdir)}" "C:/plugin/build/deepmap_search_server.py"')]
    moved = []
    py, out, err, exc = _ensure(B, w, _checks(B, w), _home_env(home),
                                extra={"_processes_using": lambda v: rows29,
                                       "_move_aside": lambda v: moved.append(v) or home / "venv.not-moved"})
    verdict = err.split("setup verdict: FAILED —", 1)[-1]
    check("...but not while a server still runs from it: FAILED, nothing changed (its log included), naming why, the "
          "server and its stop command — with DEEPMAP_MANAGED_VENV=0, since a bare python would hand that command to "
          "the environment whose packages no longer load", py is None and not moved and w.steps() == []
          and "changed from 3.12 to 3.13" in verdict and "4242" in verdict and optout in verdict
          and 'mcp-stop "C:/work/proj-a"`' in verdict and "Nothing was changed" in verdict
          and _log_kept(home, log29, out + err), verdict[-800:])

    oor = [{"name": "graphifyy", "spec": ">=0.8.39,<0.8.52", "found": "0.8.38"}]
    home = td / "h17"
    vdir = home / "venv"
    w = _World(vdir, probe=[{"missing": oor, "pip": True}, {"missing": oor, "pip": True}], cached=[{"cached": True}],
               rc={"pinned": 1}, logtext={"pinned": "HTTPSConnectionPool(host='pypi.org'): Read timed out.\n"})
    _complete_env(vdir, w)
    env17 = _home_env(home)
    py, out, err, exc = _ensure(B, w, _checks(B, w), env17)
    check("a failed repair of a COMPLETE environment that left it no worse (pip rolled the upgrade back) keeps it in "
          "use: every project's commands still run under it", py is None and "setup verdict: FAILED —" in err
          and B.managed_python(env17) == str(B.venv_python(vdir)), (B._read_marker(vdir), err[-300:]))
    w2 = _World(vdir, probe=[{"missing": [], "pip": True}], cached=[{"cached": True}])
    py, out, err, exc = _ensure(B, w2, _checks(B, w2), env17)
    check("...and the next setup retries it (model check + self-check), never 'reused as is'",
          py and w2.steps() == ["model", "selfcheck"] and not (B._read_marker(vdir) or {}).get("repair_pending"),
          (w2.steps(), B._read_marker(vdir)))
    home = td / "h18"
    vdir = home / "venv"
    w = _World(vdir, probe=[{"missing": oor, "pip": True},
                            {"missing": [dict(oor[0], found=None)], "pip": True}], cached=[{"cached": True}],
               rc={"pinned": 1})
    _complete_env(vdir, w)
    py, out, err, exc = _ensure(B, w, _checks(B, w), _home_env(home))
    check("...but a failed repair that left it WORSE (graphifyy now absent) marks it incomplete",
          py is None and B.managed_python(_home_env(home)) is None, B._read_marker(vdir))

    home = td / "h5"
    vdir = home / "venv"
    vdir.mkdir(parents=True)
    (vdir / "notes.txt").write_text("mine", encoding="utf-8")
    w = _World(vdir)
    py, out, err, exc = _ensure(B, w, _checks(B, w), _home_env(home))
    check("a foreign, non-empty <DEEPMAP_HOME>/venv is refused and left untouched",
          py is None and w.steps() == [] and (vdir / "notes.txt").read_text(encoding="utf-8") == "mine"
          and "not a DeepMap environment" in err, (w.steps(), err[-400:]))
    py, out, err, exc = _ensure(B, w, _checks(B, w), _home_env(home), extra={"_processes_using": lambda v: None})
    check("...also when the process table cannot be read: the same refusal, nothing touched",
          py is None and exc is None and w.steps() == [] and "not a DeepMap environment" in err
          and (vdir / "notes.txt").read_text(encoding="utf-8") == "mine", (exc, err[-400:]))

    # Deleting the environment while DeepMap's servers run from it (they outlive Claude Code) removes its marker and
    # pyvenv.cfg but not the files those servers hold open (Windows, measured): what is left is not DeepMap's any more,
    # so it is still refused and never touched — but the refusal names what runs from it and how to stop it.
    for label, with_cfg in (("marker and pyvenv.cfg gone", False), ("marker gone, pyvenv.cfg left", True)):
        home = td / f"h24-{int(with_cfg)}"
        vdir = home / "venv"
        held = vdir / "Lib" / "site-packages" / "markupsafe" / "_speedups.cp313-win_amd64.pyd"
        held.parent.mkdir(parents=True)
        held.write_bytes(b"MZ")
        if with_cfg:
            (vdir / "pyvenv.cfg").write_text("home = C:/base\n", encoding="utf-8")
        (home / "run").mkdir(parents=True)
        (home / "run" / "deepmap-search-8899.owner").write_text(json.dumps({"pid": 4242, "root": "C:/work/proj-a"}),
                                                                encoding="utf-8")
        rows = [(4242, f'"{B.venv_python(vdir)}" "C:/plugin/build/deepmap_search_server.py"'),
                (5151, f'"{B.venv_python(vdir)}" "C:/plugin/build/deepmap_cli.py" sync .')]
        asked: list = []
        w = _World(vdir)
        py, out, err, exc = _ensure(B, w, _checks(B, w), _home_env(home),
                                    extra={"_processes_using": lambda v, r=rows: asked.append(str(v)) or r})
        verdict = err.split("setup verdict: FAILED —", 1)[-1]
        check(f"what a delete left while DeepMap's servers ran ({label}): still refused and untouched (no step, no "
              "marker written)", py is None and w.steps() == [] and held.exists()
              and not (vdir / "deepmap-venv.json").exists() and "setup verdict: FAILED —" in err,
              (w.steps(), err[-400:]))
        check("...the verdict names each process running from it (pid, script, the server's project) and the "
              "`mcp-stop` that stops it, then says to delete the directory and re-run setup",
              asked == [str(vdir)] and "4242" in verdict and "deepmap_search_server.py" in verdict and "5151" in verdict
              and 'mcp-stop "C:/work/proj-a"' in verdict and f"delete {vdir}" in verdict
              and "/deep-map:setup" in verdict, verdict[-900:])
    # Only DeepMap's own processes make a directory look like what a delete of its environment left. Someone's working
    # venv that other programs run from is named as in use, and never one to delete.
    home = td / "h25"
    vdir = home / "venv"
    w = _World(vdir)
    w._venv_layout()
    rows = [(777, f'"{B.venv_python(vdir)}" "C:/work/site/manage.py" runserver')]
    py, out, err, exc = _ensure(B, w, _checks(B, w), _home_env(home), extra={"_processes_using": lambda v, r=rows: r})
    verdict = err.split("setup verdict: FAILED —", 1)[-1]
    check("a venv DeepMap did not make that only other programs run from (no owner record, none of DeepMap's scripts): "
          "refused and untouched, naming what runs from it — never told to delete it, nor to run DeepMap's mcp-stop",
          py is None and w.steps() == [] and (vdir / "pyvenv.cfg").exists() and "777" in verdict
          and "manage.py" in verdict and "did not make" in verdict and "DEEPMAP_HOME" in verdict
          and "delete" not in verdict and "mcp-stop" not in verdict, verdict[-700:])
    home = td / "h27"
    vdir = home / "venv"
    w = _World(vdir)
    w._venv_layout()
    rows = [(888, f'"{B.venv_python(vdir)}" "C:/work/site/provision.py" --apply')]
    py, out, err, exc = _ensure(B, w, _checks(B, w), _home_env(home), extra={"_processes_using": lambda v, r=rows: r})
    verdict = err.split("setup verdict: FAILED —", 1)[-1]
    check("...nor is a program that only shares a name with one of DeepMap's scripts (a provision.py outside a build/ "
          "directory: DeepMap runs its scripts from its plugin's build/): named as in use, never told to be deleted",
          py is None and w.steps() == [] and "888" in verdict and "delete" not in verdict and "mcp-stop" not in verdict,
          verdict[-700:])
    # An owner record proves a DeepMap server only while its pid still runs that server's script: only mcp-stop removes
    # a record, so after a reboot or a crash its pid can be another program's (Windows reuses pids).
    for i, (label, rec, cmd, ours) in enumerate((
            ("a stale owner record whose pid now runs someone's program from their venv",
             {"pid": 4343, "root": "C:/work/dead-proj", "script": "C:/plugin/build/deepmap_search_server.py"},
             '"@PY@" "C:/work/site/manage.py" runserver', False),
            ("a stale owner record without a script field, same reused pid",
             {"pid": 4343, "root": "C:/work/dead-proj"}, '"@PY@" "C:/work/site/manage.py" runserver', False),
            ("a live owner record whose pid runs the server script it names",
             {"pid": 4343, "root": "C:/work/live-proj", "script": "C:/plugin/build/deepmap_embed_server.py"},
             '"@PY@" "C:\\plugin\\build\\deepmap_embed_server.py"', True))):
        home = td / f"h31-{i}"
        vdir = home / "venv"
        w = _World(vdir)
        w._venv_layout()
        (home / "run").mkdir(parents=True)
        (home / "run" / "deepmap-search-8899.owner").write_text(json.dumps(rec), encoding="utf-8")
        rows = [(4343, cmd.replace("@PY@", str(B.venv_python(vdir))))]
        py, out, err, exc = _ensure(B, w, _checks(B, w), _home_env(home),
                                    extra={"_processes_using": lambda v, r=rows: r})
        verdict = err.split("setup verdict: FAILED —", 1)[-1]
        if ours:
            check(f"...{label}: still DeepMap's server (its project named, stop then delete)",
                  py is None and w.steps() == [] and "4343" in verdict and 'mcp-stop "C:/work/live-proj"' in verdict
                  and f"delete {vdir}" in verdict, verdict[-700:])
        else:
            check(f"...{label}: not DeepMap's (named as in use; no project, no mcp-stop, never told to delete it)",
                  py is None and w.steps() == [] and "4343" in verdict and "manage.py" in verdict
                  and "dead-proj" not in verdict and "delete" not in verdict and "mcp-stop" not in verdict,
                  verdict[-700:])
    home = td / "h26"
    vdir = home / "venv"
    (vdir / "Lib" / "site-packages").mkdir(parents=True)
    w = _World(vdir)
    rows = [(5151, f'"{B.venv_python(vdir)}" "{(B.HERE / "deepmap_cli.py").as_posix()}" sync .')]
    py, out, err, exc = _ensure(B, w, _checks(B, w), _home_env(home), extra={"_processes_using": lambda v, r=rows: r})
    verdict = err.split("setup verdict: FAILED —", 1)[-1]
    check("...while a DeepMap sync alone (no server, so no owner record) running from such a remnant — the plugin's "
          "own build/deepmap_cli.py — is DeepMap's: stop, then delete it", py is None and w.steps() == []
          and "5151" in verdict and "mcp-stop" in verdict and f"delete {vdir}" in verdict, verdict[-700:])
    # What makes a script DeepMap's: its process is one of DeepMap's servers (an owner record), or the script sits in a
    # DeepMap build directory, beside DeepMap's other entry points — under any path, spaces and non-ASCII included,
    # quoted or not (/proc joins a command line's words with spaces). Another program's build/provision.py is not.
    plug = td / "plüg in" / "deep-map" / "0.28.2" / "build"
    plug.mkdir(parents=True)
    for f in ("deepmap_cli.py", "deepmap_search_server.py", "deepmap_embed_server.py", "provision.py",
              "deps_bootstrap.py"):
        (plug / f).write_text("# a stand-in\n", encoding="utf-8")
    infra = td / "work" / "infra" / "build"
    infra.mkdir(parents=True)
    (infra / "provision.py").write_text("# someone else's\n", encoding="utf-8")
    for i, (label, cmd, ours) in enumerate((
            ("someone's C:/work/infra/build/provision.py (a common name for an infrastructure script)",
             '"@PY@" "C:/work/infra/build/provision.py" --apply', False),
            ("another program's build/provision.py in a directory that exists, with no DeepMap entry point beside it",
             f'"@PY@" "{infra / "provision.py"}" --apply', False),
            ("DeepMap's search server from a plugin path with spaces and non-ASCII, quoted",
             f'"@PY@" "{plug / "deepmap_search_server.py"}"', True),
            ("DeepMap's CLI from that path unquoted (as /proc joins argv), with a .py argument after it",
             f'@PY@ {plug / "deepmap_cli.py"} sync x.py', True))):
        home = td / f"h30-{i}"
        vdir = home / "venv"
        w = _World(vdir)
        w._venv_layout()
        rows = [(900 + i, cmd.replace("@PY@", str(B.venv_python(vdir))))]
        py, out, err, exc = _ensure(B, w, _checks(B, w), _home_env(home),
                                    extra={"_processes_using": lambda v, r=rows: r})
        verdict = err.split("setup verdict: FAILED —", 1)[-1]
        advice = "mcp-stop" in verdict and f"delete {vdir}" in verdict
        check(f"a venv DeepMap did not make, run from by {label}: "
              + ("DeepMap's — stop, then delete it" if ours else
                 "not DeepMap's — named as in use, never told to delete it or to run DeepMap's mcp-stop"),
              py is None and w.steps() == [] and str(900 + i) in verdict
              and (advice if ours else ("delete" not in verdict and "mcp-stop" not in verdict)), verdict[-700:])
        if i == 3:
            check("...naming the script that runs (deepmap_cli.py), not the .py argument after it",
                  "(deepmap_cli.py" in verdict and "x.py" not in verdict, verdict[-500:])

    home = td / "h6"
    (home / "venv").mkdir(parents=True)
    w = _World(home / "venv", probe=[{"missing": []}], cached=[{"cached": True}])
    py, out, err, exc = _ensure(B, w, _checks(B, w), _home_env(home))
    st = w.step("venv")
    check("an EMPTY <DEEPMAP_HOME>/venv is simply used (no --clear)", py and st and "--clear" not in st[2], w.steps())

    home = td / "h7"
    w = _World(home / "venv", probe=[{"missing": []}], cached=[{"cached": True}], rc={"pinned": 1})
    py, out, err, exc = _ensure(B, w, _checks(B, w), _home_env(home))
    mk = B._read_marker(home / "venv")
    check("a failed step leaves the marker incomplete: no command re-runs into a half-built environment",
          py is None and mk is not None and mk.get("complete") is False
          and B.managed_python(_home_env(home)) is None, mk)

    home = td / "h8"
    w = _World(home / "venv", probe=[{"missing": [{"name": "neo4j", "spec": ">=6.0.1,<7", "found": None}]}],
               cached=[{"cached": True}])
    py, out, err, exc = _ensure(B, w, _checks(B, w), _home_env(home))
    check("still missing after the install: FAILED naming the package (never a half-built 'complete')",
          py is None and "neo4j" in err and "setup verdict: FAILED —" in err
          and B.managed_python(_home_env(home)) is None, err[-500:])

    # Debian / Ubuntu without python3-venv: `-m venv` leaves a half-made venv (pyvenv.cfg, a python, no pip) and fails.
    # Once the user installs python3-venv as told, the next setup must re-create it, never run pip inside it.
    home = td / "h9"
    vdir = home / "venv"
    w = _World(vdir, rc={"venv": 1}, logtext={"venv": "Error: ensurepip is not available"})
    w.half_made = True
    py, out, err, exc = _ensure(B, w, _checks(B, w), _home_env(home))
    mk = B._read_marker(vdir)
    check("a venv step that failed half-way (Debian, no ensurepip) still marks the directory as DeepMap's, incomplete",
          py is None and (vdir / "pyvenv.cfg").exists() and mk is not None and mk.get("complete") is False,
          (mk, w.steps(), err[-300:]))
    allmiss = [{"name": n, "spec": s, "found": None} for n, s in B.requirements()]
    w2 = _World(vdir, probe=[{"missing": allmiss, "pip": False}, {"missing": [], "pip": True}],
                cached=[{"cached": True}])
    py, out, err, exc = _ensure(B, w2, _checks(B, w2), _home_env(home))
    st = w2.step("venv")
    check("...after `apt install python3-venv` the next setup re-creates it (moved aside, a fresh venv) instead of "
          "running pip in a venv without pip", py and st is not None and "--clear" not in st[2]
          and w2.steps()[:3] == ["venv", "pip", "torch"] and _leftovers(home) == [], (w2.steps(), err[-300:]))
    home = td / "h10"
    vdir = home / "venv"
    w = _World(vdir, probe=[{"missing": [], "pip": False}, {"missing": [], "pip": True}], cached=[{"cached": True}])
    _complete_env(vdir, w)
    py, out, err, exc = _ensure(B, w, _checks(B, w), _home_env(home))
    st = w.step("venv")
    check("a complete environment whose pip is gone is re-created too",
          py and st is not None and "--clear" not in st[2] and _leftovers(home) == [], (w.steps(), err[-300:]))
    home = td / "h11"
    vdir = home / "venv"
    _write_marker(vdir, False)   # creation died before pyvenv.cfg: only DeepMap's marker is there
    w = _World(vdir, probe=[{"missing": [], "pip": True}], cached=[{"cached": True}])
    py, out, err, exc = _ensure(B, w, _checks(B, w), _home_env(home))
    st = w.step("venv")
    check("a directory holding only DeepMap's marker is DeepMap's: re-created, not refused as foreign",
          py and st is not None and "--clear" not in st[2] and _leftovers(home) == [], (w.steps(), err[-300:]))


# --------------------------------------------------------------------------------------------------- M
_ST_MODULES = [{"idx": 0, "name": "0", "path": "", "type": "sentence_transformers.models.Transformer"},
               {"idx": 1, "name": "1", "path": "1_Pooling", "type": "sentence_transformers.models.Pooling"},
               {"idx": 2, "name": "2", "path": "2_Normalize", "type": "sentence_transformers.models.Normalize"}]
_SNAPSHOT_FILES = ("config.json", "model.safetensors", "tokenizer.json", "tokenizer_config.json",
                   "special_tokens_map.json", "sentence_bert_config.json", "config_sentence_transformers.json")


def gate_m_model(B, td: Path) -> None:
    print("\n-- M: a partly cached model is completed (and named), never re-checked into the same failure --")
    try:
        model = B._model_name()
    except Exception as exc:  # noqa: BLE001
        check("embeddings.MODEL_NAME is readable", False, repr(exc))
        return
    # the real helper against a fake Hugging Face cache: the snapshot lacks one module's file (1_Pooling/config.json)
    cache = td / "m-hf" / "hub"
    repo = cache / ("models--" + model.replace("/", "--"))
    sha = "0123456789abcdef0123456789abcdef01234567"
    snap = repo / "snapshots" / sha
    snap.mkdir(parents=True)
    (repo / "refs").mkdir()
    (repo / "refs" / "main").write_text(sha, encoding="utf-8")
    (snap / "modules.json").write_text(json.dumps(_ST_MODULES), encoding="utf-8")
    for f in _SNAPSHOT_FILES:
        (snap / f).write_text("{}", encoding="utf-8")
    env = _clean_env(HF_HUB_CACHE=str(cache), HF_HUB_OFFLINE="1")
    hub = subprocess.run([sys.executable, "-c", "import huggingface_hub"], capture_output=True, timeout=120,
                         env=_clean_env()).returncode == 0
    if not hub:
        print("  (huggingface_hub is not installed here: the real cache-helper checks are skipped)")
    else:
        got = B._model_cached(sys.executable, env)
        check("a snapshot holding the 4 key files but not 1_Pooling/config.json (a module modules.json lists) is NOT "
              "cached — SentenceTransformer cannot load it", got is False, got)
        cmd = getattr(B, "_model_cmd", None)
        if callable(cmd):
            r = subprocess.run(cmd(sys.executable, "download"), capture_output=True, text=True, timeout=300, env=env)
            info = B._last_json(r.stdout) or {}
            check("the download step re-checks the same files afterwards: still incomplete (offline here, so "
                  "huggingface_hub hands back the cached copy) -> exit 3 naming the file, never a pass",
                  r.returncode == 3 and info.get("missing") == ["1_Pooling/config.json"],
                  (r.returncode, r.stdout[-300:], r.stderr[-300:]))
        else:
            check("the download step re-checks the files it was to fetch (_model_cmd)", False, "no _model_cmd")
        (snap / "1_Pooling").mkdir()
        (snap / "1_Pooling" / "config.json").write_text("{}", encoding="utf-8")
        got = B._model_cached(sys.executable, env)
        check("...with that file the snapshot is cached (2_Normalize carries no file)", got is True, got)
    real = B._hf_hub_cache(dict(os.environ)) / ("models--" + model.replace("/", "--")) / "snapshots"
    snaps = [s for s in real.iterdir() if s.is_dir()] if real.is_dir() else []
    if snaps:
        have = {p.relative_to(snaps[0]).as_posix() for p in snaps[0].rglob("*") if p.is_file()}
        need = set(B.MODEL_KEY_FILES)
        check(f"every file the cache check requires exists in the real {model} snapshot on this machine",
              need <= have, sorted(need - have))

    # the flow (fake process seams)
    home = td / "m1"
    vdir = home / "venv"
    w = _World(vdir, probe=[{"missing": [], "pip": True}], cached=[{"cached": True}])
    w._venv_layout()
    _write_marker(vdir, False)   # the last setup ended in a failed self-check
    py, out, err, exc = _ensure(B, w, _checks(B, w), _home_env(home, HF_HUB_OFFLINE="1"))
    st = w.step("model")
    check("an environment not yet complete runs the model step before its self-check even when the cache looks "
          "complete — the step fetches only what is missing", py and w.steps() == ["model", "selfcheck"], w.steps())
    check("...that step is the one with HF_HUB_OFFLINE lifted", st is not None and "HF_HUB_OFFLINE" not in st[3])
    home = td / "m2"
    vdir = home / "venv"
    w = _World(vdir, probe=[{"missing": [], "pip": True}], cached=[{"cached": True}], rc={"model": 1},
               logtext={"model": "httpx.ProxyError: 407 Proxy Authentication Required\n"})
    w._venv_layout()
    _write_marker(vdir, False)
    py, out, err, exc = _ensure(B, w, _checks(B, w), _home_env(home))
    check("with a complete-looking cached copy, a model step that cannot reach huggingface.co is not fatal: the "
          "offline self-check decides", py and w.steps() == ["model", "selfcheck"], (w.steps(), err[-300:]))
    home = td / "m3"
    w = _World(home / "venv", cached=[{"cached": False}], rc={"model": 3},
               logtext={"model": json.dumps({"cached": False, "missing": ["1_Pooling/config.json"]}) + "\n"})
    py, out, err, exc = _ensure(B, w, _checks(B, w), _home_env(home))
    verdict = err.split("setup verdict: FAILED —", 1)[-1]
    check("a download that leaves the model incomplete (exit 3) is FAILED as such, naming the missing file — "
          "before any self-check", py is None and "selfcheck" not in w.steps() and "incomplete" in verdict
          and "1_Pooling/config.json" in verdict, (w.steps(), verdict[-500:]))
    home = td / "m4"
    hf = td / "m4-hf"
    w = _World(home / "venv", rc={"selfcheck": 1},
               logtext={"selfcheck": f"FAIL: model load/encode failed (loading the local cache copy of {model}, "
                                     "offline): TypeError: Pooling.__init__() missing 1 required positional "
                                     "argument: 'embedding_dimension'\n"})
    py, out, err, exc = _ensure(B, w, _checks(B, w), _home_env(home, HF_HUB_CACHE=str(hf)))
    where = str(hf / ("models--" + model.replace("/", "--")))
    verdict = err.split("setup verdict: FAILED —", 1)[-1]
    check("a self-check that cannot load the cached model names the model's cache directory to delete (a re-run "
          "alone would load the same files)", py is None and where in verdict and "delete" in verdict.lower(),
          verdict[-600:])


# --------------------------------------------------------------------------------------------------- L
def gate_l_remedies(B, td: Path) -> None:
    print("\n-- L: a package missing from this Python is fixed by /deep-map:setup, never by pip into that Python --")
    import importlib
    try:
        C = importlib.import_module("deepmap_cli")
        V = importlib.import_module("verdict")
        GX = importlib.import_module("graphify_extract")
    except Exception as exc:  # noqa: BLE001
        check("deepmap_cli / verdict / graphify_extract import", False, repr(exc))
        return
    fn = getattr(B, "setup_remedy", None)
    if not callable(fn):
        check("deps_bootstrap.setup_remedy exists (the one rule every remedy asks)", False, "missing")
        fn = lambda *a, **k: None  # noqa: E731
    home = td / "l-home"
    venv = str(B.venv_dir({"DEEPMAP_HOME": str(home)}))
    ml_gone = [{"name": n, "spec": "", "found": None} for n in ("torch", "sentence-transformers", "transformers")]
    neo_gone = [{"name": "neo4j", "spec": ">=6.0.1,<7", "found": None}]
    gfx_out = [{"name": "graphifyy", "spec": ">=0.8.39,<0.8.52", "found": "0.9.68"}]
    saved = {k: os.environ.get(k) for k in ("DEEPMAP_HOME", "DEEPMAP_MANAGED_VENV")}

    def env(optout=False):
        os.environ["DEEPMAP_HOME"] = str(home)
        if optout:
            os.environ["DEEPMAP_MANAGED_VENV"] = "0"
        else:
            os.environ.pop("DEEPMAP_MANAGED_VENV", None)
    try:
        env()
        with _Patch(B, missing=lambda *a, **k: neo_gone):
            r1 = fn(("neo4j",))
            r2 = fn(("mcp",))
        check("setup_remedy: a named package missing here -> '/deep-map:setup', naming it and DeepMap's environment",
              isinstance(r1, str) and "/deep-map:setup" in r1 and "neo4j" in r1 and venv in r1
              and "not into this Python" in r1, r1)
        check("...None when none of the named packages is missing here (the caller's own remedy stands)", r2 is None,
              r2)
        with _Patch(B, missing=lambda *a, **k: neo_gone), _Patch(sys, executable=str(B.venv_python(venv))):
            r4 = fn(("neo4j",))
        check("...run by DeepMap's own environment itself (one that predates a requirements change): "
              "'/deep-map:setup', never 'not into this Python' (this Python IS that environment)",
              isinstance(r4, str) and "/deep-map:setup" in r4 and venv in r4 and "not into this Python" not in r4, r4)
        # inside DeepMap's environment after its base Python moved to another minor version: every package still
        # listed (missing() is empty), their compiled parts do not load — pip would only say 'already satisfied'
        _write_marker(Path(venv), True, python_version="3.12.10")
        moved_runs = {}
        for label, ver, exe in (("moved", (3, 13, 14), str(B.venv_python(venv))),
                                ("same minor", (3, 12, 11), str(B.venv_python(venv))),
                                ("another Python", (3, 13, 14), sys.executable)):
            with _Patch(B, missing=lambda *a, **k: [], _python_version=lambda v=ver: v), _Patch(sys, executable=exe):
                moved_runs[label] = (fn(("torch", "sentence-transformers", "transformers")), C._ml_stack_remedy())
        r5, ml5 = moved_runs["moved"]
        check("...run by DeepMap's own environment whose Python moved from 3.12 to 3.13 under it (nothing listed as "
              "missing, no compiled part loads): '/deep-map:setup', which re-creates it, naming both versions — and "
              "doctor's ML-stack remedy says so instead of a pip command",
              isinstance(r5, str) and "/deep-map:setup" in r5 and "3.12" in r5 and "3.13" in r5
              and "/deep-map:setup" in ml5 and "pip install" not in ml5, moved_runs)
        check("...but not for the same minor version, nor in a Python that is not that environment (None: the "
              "caller's remedy stands)",
              moved_runs["same minor"][0] is None and moved_runs["another Python"][0] is None, moved_runs)
        (Path(venv) / "deepmap-venv.json").unlink()
        env(optout=True)
        with _Patch(B, missing=lambda *a, **k: neo_gone):
            r3 = fn(("neo4j",))
        check("...None under DEEPMAP_MANAGED_VENV=0 (the operator manages that Python)", r3 is None, r3)

        env()
        with _Patch(B, missing=lambda *a, **k: ml_gone):
            ml = C._ml_stack_remedy()
            v, reasons = V.classify(neo4j_ok=True, n_nodes=5, n_embedded=0, census={"Python": 5},
                                    extractor="graphify", ml_ok=False)
        vec = next((x for x in reasons if x.startswith("VECTOR SEARCH DISABLED")), "")
        check("doctor's ML-stack remedy, torch missing here: /deep-map:setup, no pip into this Python",
              "/deep-map:setup" in ml and "pip install" not in ml, ml)
        check("the sync verdict's VECTOR SEARCH DISABLED fix: /deep-map:setup, no pip",
              "/deep-map:setup" in vec and "pip install" not in vec, vec)
        with _Patch(B, missing=lambda *a, **k: neo_gone):
            drv = C._driver_remedy(ModuleNotFoundError("No module named 'neo4j'", name="neo4j"))
        check("doctor's driver remedy, neo4j missing here: /deep-map:setup, no pip",
              "/deep-map:setup" in drv and "pip install" not in drv, drv)
        with _Patch(B, missing=lambda *a, **k: [dict(neo_gone[0], found="5.28.6")]), \
                _Patch(C, _installed_neo4j_version=lambda: "5.28.6"):
            drv_old = C._driver_remedy(RuntimeError("Unexpected config keys: notifications_min_severity"))
        check("...and with neo4j 5.28.6 here (outside the range) a client-side driver error: /deep-map:setup, no pip",
              "/deep-map:setup" in drv_old and "5.28.6" in drv_old and "pip install" not in drv_old, drv_old)
        with _Patch(B, missing=lambda *a, **k: gfx_out):
            gcmd, ccmd = GX.graphify_install_command(), C._graphify_install_cmd()
            hint, chint = GX.unsupported_engine_hint("0.9.68"), C._unsupported_graphify_hint("0.9.68")
        check("graphify's range remedy, graphifyy 0.9.68 here: /deep-map:setup (one source: graphify_extract == CLI)",
              "/deep-map:setup" in gcmd and "pip install" not in gcmd and ccmd == gcmd
              and all("/deep-map:setup" in h and "pip install" not in h for h in (hint, chint)),
              (gcmd, ccmd, hint, chint))
        env(optout=True)
        with _Patch(B, missing=lambda *a, **k: ml_gone + neo_gone + gfx_out):
            ml_o = C._ml_stack_remedy()
            drv_o = C._driver_remedy(ModuleNotFoundError("No module named 'neo4j'", name="neo4j"))
            gcmd_o = GX.graphify_install_command()
        py = f'"{Path(sys.executable).as_posix()}" -m pip install'
        check("DEEPMAP_MANAGED_VENV=0: every remedy keeps its pip command for this Python, as before",
              py in ml_o and py in drv_o and py in gcmd_o, (ml_o, drv_o, gcmd_o))
        env()
        with _Patch(B, missing=lambda *a, **k: []):
            ml_i = C._ml_stack_remedy()
        check("installed but not importable (setup would not change it): the pip command stays", py in ml_i, ml_i)
    finally:
        for k, val in saved.items():
            if val is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = val


# --------------------------------------------------------------------------------------------------- I
class _Block:
    """`import <name>` fails the way it does on a Python without that package."""

    def __init__(self, names):
        self.names = tuple(names)

    def _hit(self, name) -> bool:
        return any(name == n or name.startswith(n + ".") for n in self.names)

    def find_spec(self, name, path=None, target=None):
        if self._hit(name):
            raise ModuleNotFoundError(f"No module named {name!r}", name=name)
        return None


@contextmanager
def _blocked(*names):
    blocker = _Block(names)
    saved = {k: v for k, v in sys.modules.items() if blocker._hit(k)}
    for k in saved:
        del sys.modules[k]
    sys.meta_path.insert(0, blocker)
    try:
        yield
    finally:
        sys.meta_path.remove(blocker)
        sys.modules.update(saved)


def gate_i_messages(B, td: Path) -> None:
    print("\n-- I: every failure names its fix and ends 'setup verdict: FAILED — ...' --")
    deb = ("The virtual environment was not created successfully because ensurepip is not\navailable.  On "
           "Debian/Ubuntu systems, you need to install the python3-venv\npackage using the following command.\n\n"
           "    apt install python3.11-venv\n")
    home = td / "i1"
    w = _World(home / "venv", rc={"venv": 1}, logtext={"venv": deb})
    linux = {"_platform": lambda: "linux", "_ensurepip_missing": lambda: False}
    py, out, err, exc = _ensure(B, w, _checks(B, w), _home_env(home), extra=linux)
    minor = f"python3.{sys.version_info[1]}-venv"
    check("Debian/Ubuntu without python3-venv: names `sudo apt install` with this Python's package and python3-venv",
          py is None and "sudo apt install" in err and minor in err and "python3-venv" in err
          and "/deep-map:setup" in err and err.rstrip().splitlines()[-1].startswith("[provision] setup verdict: "
                                                                                     "FAILED —"), err[-900:])
    # CPython's venv prints this for EVERY failure at its ensurepip stage (AppLocker / Defender blocking the new
    # python.exe, a full disk, a locked file) — only Debian's own wording, or a Linux Python without ensurepip, is
    # the missing python3-venv package.
    generic = ("Error: Command '['/home/u/.deepmap/venv/bin/python3', '-m', 'ensurepip', '--upgrade', "
               "'--default-pip']' returned non-zero exit status 1.\n")
    for plat, label in (("win32", "Windows"), ("darwin", "macOS"), ("linux", "Linux whose Python has ensurepip")):
        home = td / f"i7-{plat}"
        w = _World(home / "venv", rc={"venv": 1}, logtext={"venv": generic})
        py, out, err, exc = _ensure(B, w, _checks(B, w), _home_env(home),
                                    extra={"_platform": lambda p=plat: p, "_ensurepip_missing": lambda: False})
        verdict = err.split("setup verdict: FAILED —", 1)[-1]
        check(f"{label}: CPython's generic ensurepip failure is not read as Debian's missing python3-venv (no apt "
              "advice): 'failed at its pip step', the log", py is None and "apt install" not in err
              and "pip step" in verdict and str(B.log_path(_home_env(home))) in verdict, verdict[-600:])
        if plat == "win32":
            check("...on Windows it names security software that may block a new python.exe",
                  "security software" in verdict, verdict[-600:])
    home = td / "i8"
    w = _World(home / "venv", rc={"venv": 1}, logtext={"venv": generic})
    py, out, err, exc = _ensure(B, w, _checks(B, w), _home_env(home),
                                extra={"_platform": lambda: "linux", "_ensurepip_missing": lambda: True})
    check("a Linux Python with no ensurepip module at all: the python3-venv advice, even without Debian's wording",
          py is None and "sudo apt install" in err and minor in err, err[-600:])
    home = td / "i2"
    # what pip prints when it cannot reach the index: its retries, then "no matching distribution"
    pip_log = ("WARNING: Retrying (Retry(total=4, connect=None, read=None, redirect=None, status=None)) after "
               "connection broken by 'NewConnectionError('<pip._vendor.urllib3.connection.HTTPSConnection object at "
               "0x7f2c>: Failed to establish a new connection: [Errno -3] Temporary failure in name resolution')': "
               "/simple/mcp/\n"
               "ERROR: Could not find a version that satisfies the requirement mcp<2,>=1.24 (from versions: none)\n"
               "ERROR: No matching distribution found for mcp<2,>=1.24\n")
    w = _World(home / "venv", rc={"pinned": 1}, logtext={"pinned": pip_log})
    py, out, err, exc = _ensure(B, w, _checks(B, w), _home_env(home))
    logp = str(B.log_path(_home_env(home)))
    check("pip fails (network / index): the log path, the log's last lines, the network hint and the retry",
          py is None and logp in err and "No matching distribution found" in err and "network" in err.lower()
          and "/deep-map:setup" in err and "setup verdict: FAILED —" in err, err[-900:])
    check("...and the retry keeps what is installed (the message says so) — the terminal form names this Python",
          "keeps what is already installed" in err and Path(sys.executable).as_posix() in err, err[-900:])
    # the index answered, but holds no build for this Python / platform (a 32-bit Windows Python, an Intel Mac on a new
    # Python, musl): no retry or connection line — no network problem, so the verdict must not send the user to check
    # one first. pip logs an index that REFUSED the request (401 / 403 / 404, e.g. a proxy's block page) with the very
    # same lines (measured, pip 26), so the verdict names that possibility too, never the platform as a certainty.
    ver = ".".join(map(str, sys.version_info[:3]))
    for key, pkg, log, own in (
            ("torch", "torch", "Looking in indexes: https://download.pytorch.org/whl/cpu\nERROR: Could not find a "
                               "version that satisfies the requirement torch (from versions: none)\nERROR: No matching "
                               "distribution found for torch\n", "found no PyTorch CPU build"),
            ("pinned", "graphifyy", "ERROR: Could not find a version that satisfies the requirement "
                                    "graphifyy<0.8.52,>=0.8.39 (from versions: 0.8.10, 0.8.12)\nERROR: No matching "
                                    "distribution found for graphifyy<0.8.52,>=0.8.39\n", "pip found no graphifyy")):
        home = td / f"i9-{key}"
        w = _World(home / "venv", rc={key: 1}, logtext={key: log})
        py, out, err, exc = _ensure(B, w, _checks(B, w), _home_env(home))
        verdict = err.split("setup verdict: FAILED —", 1)[-1]
        check(f"no {pkg} build for this Python / platform (no network error logged): '{own}', with this Python's "
              f"version and bitness and the log — not 'usually the network', and never the platform as a certainty: "
              f"an index that refused pip's request is named too", py is None and own in verdict
              and ver in verdict and "-bit" in verdict and "usually the network" not in verdict
              and "refused pip's request" in verdict and "publishes no CPU build for this" not in verdict
              and str(B.log_path(_home_env(home))) in verdict, verdict[-700:])
    # a FILE download the index refused (pip: "HTTP error 403 while getting <url>") is index trouble, not the platform
    home = td / "i9-http"
    w = _World(home / "venv", rc={"torch": 1}, logtext={"torch": (
        "ERROR: HTTP error 403 while getting https://download.pytorch.org/whl/cpu/torch-2.12.1%2Bcpu-cp313-cp313-"
        "win_amd64.whl\nERROR: Could not install requirement torch because of HTTP error 403 Client Error: Forbidden "
        "for url\nERROR: No matching distribution found for torch\n")})
    py, out, err, exc = _ensure(B, w, _checks(B, w), _home_env(home))
    verdict = err.split("setup verdict: FAILED —", 1)[-1]
    check("pip's 'HTTP error 403 while getting <url>' is index / network trouble, never 'no build for this platform'",
          py is None and "usually the network" in verdict and "found no PyTorch" not in verdict, verdict[-500:])
    # pip set not to use any package index prints the very same lines (measured: `PIP_NO_INDEX=1 pip install
    # --index-url <torch index> torch` -> "(from versions: none)", no "Looking in indexes", no network line): that is
    # neither this platform nor the network
    nothing = ("ERROR: Could not find a version that satisfies the requirement torch (from versions: none)\n"
               "ERROR: No matching distribution found for torch\n")
    for label, extra, conf in (("PIP_NO_INDEX=1", {"PIP_NO_INDEX": "1"}, None),
                               ("no-index in a pip configuration file", {}, "global.no-index='true'\n")):
        home = td / f"i11-{'env' if extra else 'conf'}"
        w = _World(home / "venv", rc={"torch": 1}, logtext={"torch": nothing}, pipconf=conf)
        py, out, err, exc = _ensure(B, w, _checks(B, w), _home_env(home, **extra))
        verdict = err.split("setup verdict: FAILED —", 1)[-1]
        check(f"pip set not to use any package index ({label}): the verdict says so — not 'PyTorch publishes no CPU "
              "build', not 'usually the network'", py is None and "not to use any package index" in verdict
              and "publishes no CPU build" not in verdict and "usually the network" not in verdict, verdict[-600:])
    # a repair while a project's MCP server runs from the environment: on Windows pip cannot replace an extension
    # module that server has loaded — the step names that, the server and its mcp-stop, never the network
    home = td / "i10"
    vdir = home / "venv"
    oor = [{"name": "mcp", "spec": ">=1.24,<2", "found": "1.23.3"}]
    locked = ("ERROR: Could not install packages due to an OSError: [WinError 5] Access is denied: "
              f"'{vdir}\\Lib\\site-packages\\pydantic_core\\_pydantic_core.cp313-win_amd64.pyd'\n"
              "Consider using the `--user` option or check the permissions.\n")
    w = _World(vdir, probe=[{"missing": oor, "pip": True}], cached=[{"cached": True}], rc={"pinned": 1},
               logtext={"pinned": locked})
    _complete_env(vdir, w)
    (home / "run").mkdir(parents=True)
    (home / "run" / "deepmap-embed-8900.owner").write_text(json.dumps({"pid": 777, "root": "C:/work/proj-b"}),
                                                           encoding="utf-8")
    holder = [(777, f'"{B.venv_python(vdir)}" "C:/plugin/build/deepmap_embed_server.py"')]
    py, out, err, exc = _ensure(B, w, _checks(B, w), _home_env(home), extra={"_processes_using": lambda v: holder})
    verdict = err.split("setup verdict: FAILED —", 1)[-1]
    check("a pip step that cannot replace a file a running server holds (Windows' WinError 5 / 32) says so — naming "
          "the server, its project and the `mcp-stop` that frees it — never 'usually the network'",
          py is None and "held by a running process" in verdict and "777" in verdict
          and 'mcp-stop "C:/work/proj-b"' in verdict and "usually the network" not in verdict, verdict[-800:])
    check("...and the environment the commands were using stays in use (that failed repair left it no worse)",
          B.managed_python(_home_env(home)) == str(B.venv_python(vdir)), B._read_marker(vdir))
    home = td / "i3"
    w = _World(home / "venv", cached=[{"cached": False}], rc={"model": 1},
               logtext={"model": "requests.exceptions.ConnectionError: huggingface.co timed out\n"})
    py, out, err, exc = _ensure(B, w, _checks(B, w), _home_env(home))
    check("the model download fails: huggingface.co, network / proxy, the log and the retry",
          py is None and "huggingface.co" in err and "proxy" in err and str(B.log_path(_home_env(home))) in err
          and "/deep-map:setup" in err, err[-700:])
    home = td / "i4"
    w = _World(home / "venv", rc={"selfcheck": 1},
               logtext={"selfcheck": "FAIL: graphify (the multi-language engine) is not importable\n"})
    py, out, err, exc = _ensure(B, w, _checks(B, w), _home_env(home))
    check("the self-check fails: its own FAIL line is shown, plus the log",
          py is None and "self-check" in err and "FAIL: graphify" in err and "setup verdict: FAILED —" in err,
          err[-700:])
    home = td / "i5"
    w = _World(home / "venv", rc={"torch": -1})
    py, out, err, exc = _ensure(B, w, _checks(B, w), _home_env(home))
    check("a step that runs past its limit: 'did not finish within', the network hint and the retry",
          py is None and "did not finish within" in err and "/deep-map:setup" in err, err[-600:])
    home = td / "i6"
    w = _World(home / "venv")
    py, out, err, exc = _ensure(B, w, _checks(B, w), _home_env(home), pyver=(3, 9, 18))
    check("Python 3.9: FAILED before anything, naming 3.10 and the version found",
          py is None and w.steps() == [] and "3.10" in err and "3.9.18" in err, err[-500:])


def gate_i_driver_missing(P, td: Path) -> None:
    print("\n-- I (R5): no neo4j driver -> 'packages are missing', never 'Neo4j did not become reachable', no JVM --")
    started: list = []
    stubs = dict(preflight=lambda *a, **k: True, _download_extract_shared=lambda *a, **k: None,
                 configure_neo4j=lambda *a, **k: None, set_initial_password=lambda *a, **k: True,
                 start_neo4j=lambda *a, **k: started.append(a) or 123, wait_for_bolt=lambda *a, **k: False,
                 _is_initialized=lambda *a, **k: False, _record_neo4j_server_identity=lambda *a, **k: None)
    diag: dict = {}
    with _Patch(P, **stubs), _blocked("neo4j"):
        ok, out, err, exc = _quiet(P.ensure_neo4j, uri="bolt://127.0.0.1:1", user="neo4j", password="deepmap",
                                   project_key=None, diag=diag)
    reason = diag.get("reason", "")
    check("ensure_neo4j with no neo4j driver: False, the reason names the missing packages (heal status, verdicts)",
          ok is False and exc is None and "neo4j" in reason and "missing" in reason.lower(), (ok, reason, exc))
    check("...nothing provisioned or started, and never 'Neo4j did not become reachable'",
          not started and "did not become reachable" not in out + err + reason, (started, (out + err)[-500:]))
    proj = td / "r5proj"
    proj.mkdir()
    (proj / "app.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    saved_uri = os.environ.get("NEO4J_URI")
    os.environ["NEO4J_URI"] = "bolt://127.0.0.1:1"
    ports: list = []
    try:
        for missing_mod in ("neo4j", "mcp"):
            started.clear()
            ports.clear()
            with _Patch(P, **stubs, _resolve_ports=lambda *a, **k: ports.append(a) or ((8899, 8900), None, None),
                        resolved_instance=lambda *a, **k: None, apply_ports_for=lambda *a, **k: (8899, 8900),
                        write_mcp_json=lambda *a, **k: None), _blocked(missing_mod):
                ok, out, err, exc = _quiet(P.setup, str(proj))
            verdict = err.split("setup verdict: FAILED —", 1)[-1]
            check(f"setup() with no {missing_mod} package: 'setup verdict: FAILED' saying DeepMap's Python packages "
                  f"are missing, naming {missing_mod}", ok is False and exc is None
                  and "setup verdict: FAILED —" in err and "packages are missing" in verdict
                  and missing_mod in verdict, (ok, exc, err[-700:]))
            check(f"...before anything is allocated or started (no ports, no JVM), never 'Neo4j did not become "
                  f"reachable' (no {missing_mod})", "did not become reachable" not in out + err and not started
                  and not ports, (started, ports, (out + err)[-400:]))
    finally:
        if saved_uri is None:
            os.environ.pop("NEO4J_URI", None)
        else:
            os.environ["NEO4J_URI"] = saved_uri


# --------------------------------------------------------------------------------------------------- J
def _main_block_calls(path: Path) -> list:
    """[(dotted call name)] in source order inside the file's `if __name__ == "__main__":` block."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.If) and "__main__" in ast.dump(node.test):
            out = []
            for sub in ast.walk(ast.Module(body=node.body, type_ignores=[])):
                if isinstance(sub, ast.Call):
                    f = sub.func
                    name = (f"{f.value.id}.{f.attr}" if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)
                            else getattr(f, "id", ""))
                    out.append((sub.lineno, sub.col_offset, name))
            return [n for _, _, n in sorted(out)]
    return []


def gate_j_wiring(B, root: Path, td: Path) -> None:
    print("\n-- J: wiring — both entry points call the bootstrap before main(); stdlib-only module --")
    calls = _main_block_calls(root / "build" / "provision.py")
    check("provision.py __main__: deps_bootstrap.entry(...) runs before main()",
          "deps_bootstrap.entry" in calls and "main" in calls
          and calls.index("deps_bootstrap.entry") < calls.index("main"), calls)
    calls = _main_block_calls(root / "build" / "deepmap_cli.py")
    check("deepmap_cli.py __main__: deps_bootstrap.reexec_entry(...) runs before main()",
          "deps_bootstrap.reexec_entry" in calls and "main" in calls
          and calls.index("deps_bootstrap.reexec_entry") < calls.index("main"), calls)
    src = (root / "build" / "deps_bootstrap.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    top = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            top += [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            top.append(node.module.split(".")[0])
    std = set(getattr(sys, "stdlib_module_names", ())) | {"__future__"}
    check("deps_bootstrap.py imports only the standard library at module level", std and set(top) <= std,
          sorted(set(top) - std))
    try:
        ast.parse(src, feature_version=(3, 8))
        old_ok = True
    except SyntaxError as exc:
        old_ok = exc
    check("...and parses on Python 3.8, so an old Python still gets the 'needs 3.10' message", old_ok is True, old_ok)


_TRACER = r'''import os, sys
_p = os.environ.get("DMSB_TRACE")
if _p:
    with open(_p, "a", encoding="utf-8") as _f:
        _f.write(sys.executable + "\t" + os.environ.get("DEEPMAP_REEXEC_GUARD", "") + "\t"
                 + " ".join(sys.argv[1:]) + "\n")
'''


def gate_j_end_to_end(B, root: Path, td: Path) -> None:
    print("\n-- J (end to end): a bare Python re-runs the REAL entry points under the managed one, once --")
    bare, home = td / "bare", td / "e2e-home"
    mvenv = home / "venv"
    for d in (bare, mvenv):
        r = subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(d)], capture_output=True, text=True,
                           timeout=300)
        if r.returncode != 0:
            check(f"throwaway venv {d.name} created", False, r.stdout + r.stderr)
            return
    bpy, mpy = B.venv_python(bare), B.venv_python(mvenv)   # (home is created by `-m venv` for mvenv)
    r = subprocess.run([str(mpy), "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
                       capture_output=True, text=True, timeout=120, env=_clean_env())
    purelib = Path(r.stdout.strip())
    purelib.mkdir(parents=True, exist_ok=True)
    (purelib / "sitecustomize.py").write_text(_TRACER, encoding="utf-8")
    _write_marker(mvenv, True)

    # What guards a re-creation (section H): a REAL process running from a venv is seen by its interpreter's path —
    # on this OS's own process table — and no longer once it has exited.
    look = getattr(B, "_processes_using", None)
    proc = subprocess.Popen([str(mpy), "-c", "import sys; print('up', flush=True); sys.stdin.read()"],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=_clean_env())
    try:
        # wait until it really runs: on Linux Popen can return before the exec has set /proc/<pid>/cmdline
        proc.stdout.readline()
        during = look(mvenv) if callable(look) else None
        other = look(bare) if callable(look) else None
    finally:
        proc.stdin.close()   # it reads stdin to the end, then exits by itself: nothing is killed
        try:
            proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            proc.kill()   # our own child, by its handle
            proc.wait()
        proc.stdout.close()
    after = look(mvenv) if callable(look) else None

    def has(rows):
        return any(pid == proc.pid for pid, _ in rows or [])
    check("a process running from a venv is seen by the check that guards its re-creation (this machine's process "
          "table), not for another venv, and no longer once it exits", has(during) and not has(other)
          and not has(after), (proc.pid, during, other, after))
    trace = td / "trace.txt"
    cli, prov = root / "build" / "deepmap_cli.py", root / "build" / "provision.py"
    notgit = td / "notgit"
    notgit.mkdir()

    def run(py, args, **extra):
        env = _clean_env(DEEPMAP_HOME=str(home), DMSB_TRACE=str(trace), PYTHONIOENCODING="utf-8", **extra)
        before = trace.read_text(encoding="utf-8").splitlines() if trace.exists() else []
        p = subprocess.run([str(py), *map(str, args)], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=180, env=env, cwd=str(td))
        after = trace.read_text(encoding="utf-8").splitlines() if trace.exists() else []
        return p, [ln.split("\t") for ln in after[len(before):]]

    def ran_managed(lines, args):
        return (len(lines) == 1 and os.path.normcase(lines[0][0]) == os.path.normcase(str(mpy))
                and lines[0][1] == "1" and lines[0][2].endswith(" ".join(args)))

    p, lines = run(bpy, [cli, "version"])
    check("bare python deepmap_cli.py version: exit 0, the version printed — run ONCE under the managed python with "
          "the loop guard", p.returncode == 0 and p.stdout.startswith("deepmap ") and ran_managed(lines, ["version"]),
          (p.returncode, p.stdout, p.stderr[-300:], lines))
    p, lines = run(bpy, [cli, "no-such-subcommand"])
    check("argparse's exit 2 and its stderr pass through the re-run unchanged",
          p.returncode == 2 and "invalid choice" in p.stderr and ran_managed(lines, ["no-such-subcommand"]),
          (p.returncode, p.stderr[-300:], lines))
    args = ["git-hook", "--status", str(notgit)]
    direct, _ = run(mpy, [prov, *args], DEEPMAP_REEXEC_GUARD="1")
    p, lines = run(bpy, [prov, *args])
    check("bare python provision.py git-hook --status: same exit code and output as the managed python run directly",
          p.returncode == direct.returncode and p.stdout == direct.stdout and p.stderr == direct.stderr
          and ran_managed(lines, args), (p.returncode, direct.returncode, p.stdout[-200:], lines))
    p, lines = run(bpy, [cli, "version"], DEEPMAP_MANAGED_VENV="0")
    check("DEEPMAP_MANAGED_VENV=0: the bare python runs it itself", p.returncode == 0 and lines == [], lines)
    p, lines = run(bpy, [cli, "version"], DEEPMAP_REEXEC_GUARD="1")
    check("DEEPMAP_REEXEC_GUARD=1 already set: no re-run", p.returncode == 0 and lines == [], lines)
    _write_marker(mvenv, False)
    p, lines = run(bpy, [cli, "version"])
    check("an incomplete environment (marker complete=false): no re-run", p.returncode == 0 and lines == [], lines)
    _write_marker(mvenv, True)

    # A COMPLETE environment whose interpreter cannot start: the bare python runs the command itself. Before, it was
    # handed to the venv launcher, which failed (Windows: exit 106 without pyvenv.cfg, 103 when its base Python is
    # gone).
    cfg = mvenv / "pyvenv.cfg"
    cfg_bytes = cfg.read_bytes()
    broken = [("its pyvenv.cfg gone", None)]
    if _WIN:
        left = td / "e2e-uninstalled-base"
        left.mkdir()
        (left / "notes.txt").write_text("left behind", encoding="utf-8")
        broken.append(("its base Python uninstalled, the directory left behind",
                       re.sub(rb"(?m)^home = [^\r\n]*", lambda m: b"home = " + str(left).encode("utf-8"), cfg_bytes)))
    for label, text in broken:
        if text is None:
            cfg.unlink()
        else:
            cfg.write_bytes(text)
        try:
            p, lines = run(bpy, [cli, "version"])
        finally:
            cfg.write_bytes(cfg_bytes)
        check(f"a complete environment with {label}: a bare python runs deepmap_cli.py version itself — exit 0, the "
              "version printed, nothing run in that environment", p.returncode == 0 and p.stdout.startswith("deepmap ")
              and lines == [], (p.returncode, p.stdout[-200:], p.stderr[-300:], lines))

    # The no-start rule is this OS's own: for each pyvenv.cfg shape, its verdict must match what the REAL venv does —
    # start AS this environment (a module only its site-packages holds imports). Stricter than the venv loops setup
    # (its probe reuses the environment, the commands never run in it); looser hands commands to a launcher that fails.
    present = getattr(B, "_interpreter_present", None)
    only = purelib / "dmsb_only_here.py"
    only.write_text("X = 1\n", encoding="utf-8")
    nowhere = str(td / "no-such-python-home").encode("utf-8")
    shapes = [("as -m venv wrote it", cfg_bytes), ("saved with a UTF-8 BOM", b"\xef\xbb\xbf" + cfg_bytes),
              ("its key spelled Home", re.sub(rb"(?m)^home =", b"Home =", cfg_bytes)),
              ("no home line", re.sub(rb"(?m)^home = [^\r\n]*\r?\n", b"", cfg_bytes)),
              ("an empty home", re.sub(rb"(?m)^home = [^\r\n]*", b"home =", cfg_bytes)),
              ("a home that does not exist", re.sub(rb"(?m)^home = [^\r\n]*", lambda m: b"home = " + nowhere,
                                                    cfg_bytes)),
              ("no pyvenv.cfg", None)]
    table = []
    try:
        for label, data in shapes:
            if data is None:
                cfg.unlink()
            else:
                cfg.write_bytes(data)
            r = subprocess.run([str(mpy), "-c", "import dmsb_only_here"], capture_output=True, timeout=120,
                               env=_clean_env(), cwd=str(td))
            table.append((label, r.returncode == 0, present(mvenv) if callable(present) else None))
    finally:
        cfg.write_bytes(cfg_bytes)
        only.unlink()
    check("the no-start rule agrees with this OS's real venv for every pyvenv.cfg shape tried (as written, a BOM, "
          "`Home`, no home, an empty or missing home, no pyvenv.cfg) — never stricter, never looser",
          all(real is static for _l, real, static in table), table)

    # opt-out on a Python without the packages: setup says so and builds nothing (PIP_NO_INDEX: even a regression
    # that ignored the opt-out could not download anything here)
    proj = td / "e2e-proj"
    proj.mkdir()
    (proj / "app.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    home2 = td / "e2e-optout-home"
    env = _clean_env(DEEPMAP_HOME=str(home2), DMSB_TRACE=str(trace), PYTHONIOENCODING="utf-8",
                     DEEPMAP_MANAGED_VENV="0", NEO4J_URI="bolt://127.0.0.1:1", PIP_NO_INDEX="1", HF_HUB_OFFLINE="1")
    p = subprocess.run([str(bpy), str(prov), "setup", str(proj)], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=600, env=env, cwd=str(td))
    check("DEEPMAP_MANAGED_VENV=0, a Python without the packages: `provision.py setup` exits 1 saying DeepMap's Python "
          "packages are missing, and makes no environment", p.returncode == 1 and "packages are missing" in p.stderr
          and not (home2 / "venv").exists(), (p.returncode, p.stderr[-500:], (home2 / "venv").exists()))

    # doctor on that Python (no complete environment for its home): every remedy is /deep-map:setup, not pip into it
    home3 = td / "e2e-doctor-home"
    home3.mkdir()
    import importlib
    PR = importlib.import_module("port_registry")
    (home3 / PR.REGISTRY_FILENAME).write_text(json.dumps({"version": PR.REGISTRY_VERSION, "kind": PR.REGISTRY_KIND,
                                                          "projects": {}}), encoding="utf-8")   # no port is probed

    def doctor(dm_home=home3, **extra):
        e = _clean_env(DEEPMAP_HOME=str(dm_home), PYTHONIOENCODING="utf-8", NEO4J_URI="bolt://127.0.0.1:1",
                       NEO4J_PASSWORD="deepmap", HF_HUB_OFFLINE="1", **extra)
        r = subprocess.run([str(bpy), str(cli), "doctor"], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=300, env=e, cwd=str(proj))
        out = r.stdout + r.stderr
        neo = next((ln for ln in out.splitlines() if "Neo4j" in ln and "FAIL" in ln), "")
        ml = next((ln for ln in out.splitlines() if "ML stack" in ln), "")
        return r, neo, ml
    r, neo, ml = doctor()
    check("doctor on a Python without the packages: the neo4j driver FAIL and the ML-stack WARN both send the user "
          "to /deep-map:setup, with no pip command into that Python",
          "/deep-map:setup" in neo and "/deep-map:setup" in ml and "-m pip install" not in neo + ml,
          (r.returncode, neo, ml, r.stdout[-600:]))
    r, neo, ml = doctor(DEEPMAP_MANAGED_VENV="0")
    posix = Path(str(bpy)).as_posix()
    check("...with DEEPMAP_MANAGED_VENV=0 they keep the pip commands for that Python (the operator manages it)",
          f'"{posix}" -m pip install' in neo and f'"{posix}" -m pip install' in ml, (neo, ml))
    # the same doctor next to a COMPLETE environment that cannot start (its pyvenv.cfg gone)
    (home / PR.REGISTRY_FILENAME).write_text((home3 / PR.REGISTRY_FILENAME).read_text(encoding="utf-8"),
                                             encoding="utf-8")   # no port is probed
    cfg.unlink()
    try:
        r, neo, ml = doctor(dm_home=home)
    finally:
        cfg.write_bytes(cfg_bytes)
    check("...and next to a complete DeepMap environment whose interpreter cannot start: doctor runs with that Python "
          "all the same and sends the user to /deep-map:setup (before, Windows ended in the venv launcher's error)",
          "/deep-map:setup" in neo and "/deep-map:setup" in ml and "-m pip install" not in neo + ml,
          (r.returncode, neo, ml, (r.stdout + r.stderr)[-500:]))

    # an installer-like Python: every requirement present in range (dist-info only), next to a COMPLETE environment
    r = subprocess.run([str(bpy), "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
                       capture_output=True, text=True, timeout=120, env=_clean_env())
    bare_lib = Path(r.stdout.strip())
    bare_lib.mkdir(parents=True, exist_ok=True)
    picked = {}
    for name, spec in B.requirements():
        cands = ["1.0", "99.0"] + re.findall(r"\d+(?:\.\d+)*", spec)
        ver = next((c for c in cands if B.version_ok(spec, c)), None)
        picked[name] = ver
        if ver:
            (bare_lib / f"{re.sub(r'[-.]+', '_', name)}-{ver}.dist-info").mkdir(exist_ok=True)
    p, lines = run(bpy, [cli, "version"])
    check("a Python holding every requirement in range runs the command itself — no re-run under the complete "
          "environment (the installers' users keep their Python)", all(picked.values()) and p.returncode == 0
          and p.stdout.startswith("deepmap ") and lines == [], (picked, p.returncode, lines))


# --------------------------------------------------------------------------------------------------- K
class _Resp:
    def __init__(self, data: bytes):
        self._buf = io.BytesIO(data)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, n=-1):
        return self._buf.read(n)


def gate_k_neo4j_download(P, td: Path) -> None:
    print("\n-- K: a FRESH Neo4j zip download passes the archive check under its .part temp name (the Windows first "
          "run) --")
    import hashlib
    import tarfile
    import zipfile
    src = td / "k-src.zip"
    with zipfile.ZipFile(src, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("neo4j-community-9.9.9/bin/neo4j.bat", "@echo off\r\n")
        z.writestr("neo4j-community-9.9.9/lib/neo4j.jar", b"\0" * 300_000)
    data = src.read_bytes()
    part = td / "neo4j-community-9.9.9-windows.zip.k3j4h5.part"
    part.write_bytes(data)
    check("_archive_ok accepts a valid zip checked under its `<name>.<random>.part` download name", P._archive_ok(part))
    trunc = td / "neo4j-community-9.9.9-windows.zip.trunc.part"
    trunc.write_bytes(data[: len(data) // 2])
    fake = td / "fake.zip"
    fake.write_bytes(b"not a zip at all\n" * 1000)
    check("...and still refuses a truncated download and a .zip that is not a zip",
          not P._archive_ok(trunc) and not P._archive_ok(fake))
    tgz = td / "k.tar.gz"
    with tarfile.open(tgz, "w:gz") as t:
        t.add(str(src), arcname="neo4j-community-9.9.9/lib/x.zip")
    tpart = td / "neo4j-community-9.9.9-unix.tar.gz.q1w2e3.part"
    tpart.write_bytes(tgz.read_bytes())
    check("...a tar.gz download (POSIX) still passes under its .part name", P._archive_ok(tpart))
    saved = os.environ.get("DEEPMAP_NEO4J_SHA256")
    os.environ["DEEPMAP_NEO4J_SHA256"] = hashlib.sha256(data).hexdigest()
    dest = td / "k-cache" / "neo4j-community-9.9.9-windows.zip"
    try:
        with _Patch(P.urllib.request, urlopen=lambda url, timeout=600: _Resp(data)):
            out, so, se, exc = _quiet(P.download_neo4j, "9.9.9", dest)
    finally:
        if saved is None:
            os.environ.pop("DEEPMAP_NEO4J_SHA256", None)
        else:
            os.environ["DEEPMAP_NEO4J_SHA256"] = saved
    check("download_neo4j, archive check NOT stubbed: a fresh zip is verified (structure + SHA-256) and kept — "
          "before, every fresh Windows download failed 'integrity check'",
          exc is None and out == dest and dest.exists() and dest.read_bytes() == data, (exc, (so + se)[-300:]))
    left = [p.name for p in dest.parent.iterdir() if p.name.endswith(".part")] if dest.parent.exists() else []
    check("...leaving no .part file behind", not left, left)


# --------------------------------------------------------------------------------------------------- O
def gate_o_docs(root: Path) -> None:
    print("\n-- O: the command docs the agent acts on match what setup now does --")
    setup_md = (root / "commands" / "setup.md").read_text(encoding="utf-8")
    check("setup.md: run in the background with the Bash tool's longest background timeout (7200000 ms) — a first "
          "run on a slow network outlasts its 30-minute default, which would stop it mid-install",
          "run_in_background: true" in setup_md and re.search(r"`?timeout`?[^.]{0,80}7200000", setup_md) is not None,
          setup_md[:900])
    doctor_md = (root / "commands" / "doctor.md").read_text(encoding="utf-8")
    para = next((p for p in re.split(r"\r?\n\r?\n", doctor_md) if "driver error talking to" in p), "")
    check("doctor.md: a neo4j package the Python running DeepMap lacks (or holds out of range) is /deep-map:setup's "
          "to install, never 'setup will not fix it'; the pip command only under DEEPMAP_MANAGED_VENV=0 or for a "
          "package that is installed but will not import",
          bool(para) and "will not fix it" not in para and "/deep-map:setup" in para and "own environment" in para
          and "DEEPMAP_MANAGED_VENV=0" in para, para[:900])


# --------------------------------------------------------------------------------------------------- main
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
    print(f"DeepMap — /deep-map:setup installs DeepMap's own environment (root: {root})")
    print("=" * 72)
    for k in _SCRUB:
        os.environ.pop(k, None)
    with tempfile.TemporaryDirectory(prefix="dmsb") as tds:
        td = Path(tds)
        os.environ["DEEPMAP_HOME"] = str(td / "machine-home")
        sys.path.insert(0, str(root / "build"))
        import port_registry as PR  # noqa: E402
        import provision as P  # noqa: E402
        try:
            import deps_bootstrap as B  # noqa: E402
        except Exception as exc:  # noqa: BLE001 — the pre-feature tree: report, then run what still applies
            B = None
            check("build/deps_bootstrap.py exists and imports", False, repr(exc))
        if B is not None:
            gate_a_detection(B, root, td)
            gate_a_requirement_lines(B, root, td)
            gate_b_paths(B, P, PR, td)
            gate_c_specs(B, root)
            gate_d_offline(B, td)
            gate_e_prereqs(B, P, td)
            gate_f_reexec(B, root, td)
            gate_g_guard(B, root, td)
            gate_p_unstartable(B, root, td)
            gate_q_heal(B, root, td)
            gate_n_stale(B, td)
            gate_h_repair(B, td)
            gate_m_model(B, td)
            gate_i_messages(B, td)
            gate_l_remedies(B, td)
        gate_i_driver_missing(P, td)
        gate_k_neo4j_download(P, td)
        gate_o_docs(root)
        if B is not None:
            gate_j_wiring(B, root, td)
            gate_j_end_to_end(B, root, td)
        else:
            calls = _main_block_calls(root / "build" / "provision.py")
            check("provision.py __main__: deps_bootstrap.entry(...) runs before main()",
                  "deps_bootstrap.entry" in calls, calls)
            calls = _main_block_calls(root / "build" / "deepmap_cli.py")
            check("deepmap_cli.py __main__: deps_bootstrap.reexec_entry(...) runs before main()",
                  "deps_bootstrap.reexec_entry" in calls, calls)
        os.environ.pop("DEEPMAP_HOME", None)
    total = len(PASSES) + len(FAILS)
    print("\n" + "=" * 72)
    if FAILS:
        print(f"RESULT: RED — {len(PASSES)}/{total} gates passed.")
        return 1
    print(f"RESULT: GREEN — {len(PASSES)}/{total} gates passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
