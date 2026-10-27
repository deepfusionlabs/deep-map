#!/usr/bin/env python3
"""ISS-build:013 (D7) — after a reboot the SessionStart heal runs under the Python setup ran under, not a random one —
and nothing in a repository can choose that program.

Finding: hooks.json runs the heal hook as `python … ; python3 …`; whichever interpreter reads the payload spawned
`provision.py heal` with ITS sys.executable, and provision starts the MCP servers with heal's sys.executable. Nothing
recorded the interpreter setup (and the installers: `py -3` first in install.ps1, python3 in install.sh) put DeepMap's
dependencies into. On a host where PATH `python` is another Python 3 without them, every revive after every reboot
failed (the neo4j driver import error read as "Neo4j down": heal started the JVM, timed out, never started the MCP
servers, and the next heal's preflight found the port taken).

The fix: a setup that ends with working MCP servers (and this project's .mcp.json naming them) records its interpreter
in DeepMap's MACHINE state — <DEEPMAP_HOME>/run/setup-python-<root id>.json, <root id> = the first 16 hex digits of the
SHA-256 of the sync root's normalised path (port_registry.write_setup_python) — and the heal hook starts heal (and the
opt-in hibernate sweep) with, in order: DEEPMAP_PYTHON when it names a startable interpreter (the rule
provision._hook_python applies to the post-commit hook; a command line such as `py -3` is started as its words), else
the recorded interpreter while it still exists, else its own sys.executable (the old behaviour). The record is read
with no git call and no subprocess — nothing starts before the hook's one heal spawn. An interpreter that can't be
started falls back to the hook's own Python — nothing is started twice. The hook contract holds: exit 0, nothing
printed, hidden-console creation flags on Windows. instance-rm --purge removes the project's record.

Review finding (repair round 1): the first cut kept the record INSIDE the project (<root>/.deepmap/setup_python.json)
and started whatever absolute path it named, so a repository could commit that file and have the default-on
SessionStart hook run a program of its choosing. Now nothing in the project's files picks the program: the record is
machine state, and a record that names another root, names a program not named like a Python interpreter, or lies
inside the project's own tree (a DEEPMAP_HOME inside the project) is ignored — by the writer, by port_registry's reader
and by the hook's import-free reader alike. Section F proves it in process; section E2 with the REAL hook, a repository
that ships such a file, and a stand-in hostile script that only writes a marker file. The same class, found while
repairing: the first cut also resolved DEEPMAP_PYTHON with os.path.isfile(<relative>) / shutil.which, both of which
consult the working directory — the project the hook runs in (shutil.which searches it FIRST on Windows unless
NoDefaultCurrentDirectoryInExePath is set) — so `DEEPMAP_PYTHON=py -3` could start a `py.bat` the repository ships.
Sections F2 (in process) and E3 (real hook + marker script) prove a DEEPMAP_PYTHON now names only an absolute file or a
command in an absolute PATH directory. Section H locks that the new helpers shadow nothing: an intermediate cut of
this repair defined a second port_registry._norm_path, silently replacing the lexical one the owner / identity checks
compare in (tests/key_migration_gates.py caught it on Windows).

Hermetic: temp DEEPMAP_HOME / plugin root / projects; the in-process hook runs with a fake Popen; the real-process
sections run the REAL hook against a stub provision.py and a throwaway venv interpreter. No Neo4j, no network.
Usage: python tests/o1_heal_interpreter_r283_gates.py [--root <tree>]
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import types
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

# Auto-sync (DeepMap's background refresh at session start) is on by default in an attended session; this gate
# locks the hooks WITHOUT it, so it is pinned off for this process and every hook process the gate starts
# (tests/auto_sync_hooks_gates.py locks the hooks with it on).
os.environ["DEEPMAP_AUTO_SYNC"] = "0"
REPO = Path(__file__).resolve().parent.parent
PASSES: list[str] = []
FAILS: list[str] = []
_WIN = os.name == "nt"
_SCRUB = ("PYTHONUTF8", "PYTHONIOENCODING", "PYTHONHOME", "PYTHONPATH", "DEEPMAP_HOOKS", "DEEPMAP_HEAL",
          "DEEPMAP_HIBERNATE", "CLAUDE_PROJECT_DIR", "DEEPMAP_PROJECT_ROOT", "NEO4J_URI", "CLAUDE_PLUGIN_ROOT",
          "DEEPMAP_PYTHON", "DEEPMAP_HOME", "__PYVENV_LAUNCHER__", "DEEPMAP_SEARCH_PORT", "DEEPMAP_EMBED_PORT",
          "O1_PWNED", "O1_HEAL_REC",
          # Windows: when set (Claude Code's own shell sets it), shutil.which skips the current directory — a hook
          # started by another host may not have it, so every hook run here goes without it (the worst case)
          "NoDefaultCurrentDirectoryInExePath")
IN_REPO_RECORD = ".deepmap/setup_python.json"   # the first cut's location — a file a repository can commit


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSES if ok else FAILS).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok or not detail else f" — {str(detail)[:700]}"))


def _load(root: Path):
    for d in (root / "hooks", root / "build"):
        sys.path.insert(0, str(d))
    import provision as P  # noqa: E402
    import port_registry as PR  # noqa: E402
    import deepmap_hook_state as HS  # noqa: E402
    import deepmap_sessionstart_heal as HK  # noqa: E402
    return P, PR, HS, HK


def _project(parent: Path, name: str, *, git: bool = False, manifest: bool = True) -> Path:
    """A synced project (a READY manifest). `git`: its root is a work tree's top (a `.git` dir), so a subdirectory
    belongs to it — outside git a subdirectory is a project of its own (sync._sync_root / project_root)."""
    root = parent / name
    (root / ".deepmap").mkdir(parents=True)
    if git:
        (root / ".git").mkdir()
    if manifest:
        (root / ".deepmap" / "graph_build.json").write_text(json.dumps({"nodes": 42, "verdict": "READY"}),
                                                            encoding="utf-8")
    (root / "app.py").write_text("def main():\n    return 1\n", encoding="utf-8")
    return root.resolve()


def _fake_file(path: Path, text: str = "# a stand-in (never executed by the in-process sections)\n") -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if not _WIN:
        os.chmod(path, 0o755)
    return str(path)


def _fake_python(parent: Path, name: str) -> str:
    return _fake_file(parent / f"py {name}" / ("python.exe" if _WIN else "python3"))


def _script_name() -> str:
    return "x.cmd" if _WIN else "x.sh"


def _root_id(root) -> str:
    """The record's <root id>, stated here independently of the code under test (the documented format)."""
    norm = os.path.normcase(os.path.abspath(str(root)))
    return hashlib.sha256(norm.encode("utf-8", "surrogatepass")).hexdigest()[:16]


def _record_path(home: Path, proj) -> Path:
    return Path(home) / "run" / f"setup-python-{_root_id(proj)}.json"


def _record(home: Path, proj: Path, python: str, **extra) -> Path:
    """The record exactly as setup writes it (the documented path + shape) — written by hand, so the gate also judges a
    tree that has no writer."""
    path = _record_path(home, proj)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"python": python, "root": str(proj), **extra}), encoding="utf-8")
    return path


def _in_repo_record(proj: Path, python: str) -> Path:
    """What a REPOSITORY can supply: a committed <root>/.deepmap/setup_python.json naming a program."""
    path = proj / IN_REPO_RECORD
    path.write_text(json.dumps({"python": python, "root": str(proj)}), encoding="utf-8")
    return path


def _inside(path, top) -> bool:
    p, t = os.path.normcase(os.path.abspath(str(path))), os.path.normcase(os.path.abspath(str(top)))
    try:
        return os.path.commonpath([p, t]) == t
    except ValueError:
        return False


def _same_file(a, b) -> bool:
    return a is not None and b is not None and \
        os.path.normcase(os.path.abspath(str(a))) == os.path.normcase(os.path.abspath(str(b)))


class _Env:
    def __init__(self, **values):
        self.values, self.saved = values, {}

    def __enter__(self):
        for k in set(_SCRUB) | set(self.values):
            self.saved[k] = os.environ.get(k)
            os.environ.pop(k, None)
        for k, v in self.values.items():
            if v is not None:
                os.environ[k] = v
        return self

    def __exit__(self, *exc):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _run_hook(HK, cwd: Path, *, fail_for=()) -> tuple:
    """(rc, [(argv, kw)] of EVERY Popen the process makes, stdout) of the REAL hook main() with a fake Popen (it
    replaces subprocess.Popen itself, so a git call or any other child would show up here too); argv[0] in `fail_for`
    raises FileNotFoundError (an interpreter that can't be started)."""
    calls: list = []

    class FakePopen:
        def __init__(self, argv, **kw):
            calls.append((list(argv), kw))
            if argv and argv[0] in fail_for:
                raise FileNotFoundError(2, "No such file or directory", argv[0])

    saved = (HK.subprocess.Popen, HK.sys.stdin)
    out = io.StringIO()
    try:
        HK.subprocess.Popen = FakePopen
        HK.sys.stdin = io.StringIO(json.dumps({"cwd": str(cwd), "session_id": "o1"}))
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            rc = HK.main()
    finally:
        HK.subprocess.Popen, HK.sys.stdin = saved
    return rc, calls, out.getvalue()


def gate_a_registry_helpers(PR, HS, work: Path, home: Path) -> None:
    print("\n-- A: port_registry records / reads the interpreter setup ran under — machine state, never the project --")
    names = ("setup_python_root_id", "setup_python_path", "write_setup_python", "read_setup_python",
             "clear_setup_python")
    for name in names:
        check(f"port_registry.{name} exists", callable(getattr(PR, name, None)))
    hs_read, hs_id = getattr(HS, "setup_python", None), getattr(HS, "setup_python_root_id", None)
    check("deepmap_hook_state.setup_python + setup_python_root_id exist (the hook's import-free reader)",
          callable(hs_read) and callable(hs_id))
    check("... and name the same record file prefix as port_registry (one format)",
          getattr(HS, "SETUP_PYTHON_PREFIX", None) == getattr(PR, "SETUP_PYTHON_PREFIX", None) == "setup-python-",
          repr((getattr(HS, "SETUP_PYTHON_PREFIX", None), getattr(PR, "SETUP_PYTHON_PREFIX", None))))
    if not all(callable(getattr(PR, n, None)) for n in names) or not callable(hs_read) or not callable(hs_id):
        return
    proj = _project(work, "a-proj", git=True)
    odd = _project(work, "a prój ü (x)")
    for label, base in (("a plain root", proj), ("a root with spaces, non-ASCII and parentheses", odd)):
        spellings = [base, str(base), str(base) + os.sep, os.path.join(str(base), "sub", "..")]
        if _WIN:
            spellings.append(str(base).upper())
        ids = {(repr(s), PR.setup_python_root_id(s), hs_id(s)) for s in spellings}
        check(f"{label}: the root id is the documented digest for every spelling — port_registry == the hook",
              all(a == b == _root_id(base) for _, a, b in ids), repr(sorted(ids))[:500])

    path = PR.setup_python_path(proj)
    check("the record is machine state: <DEEPMAP_HOME>/run/setup-python-<root id>.json",
          path == _record_path(home, proj), str(path))
    check("... never a file inside the project", not _inside(path, proj), str(path))
    before = sorted(p.name for p in (proj / ".deepmap").iterdir())
    check("write_setup_python(root) records sys.executable", PR.write_setup_python(proj) is True
          and PR.read_setup_python(proj) == sys.executable, repr(PR.read_setup_python(proj)))
    rec = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    check("... as JSON naming the interpreter, its version, the root and the project key",
          rec.get("python") == sys.executable and rec.get("version") == sys.version.split()[0]
          and rec.get("root") == str(proj) and rec.get("project_key") == PR.project_key(proj, migrate=False), repr(rec))
    check("... and nothing was written into the project",
          sorted(p.name for p in (proj / ".deepmap").iterdir()) == before and not (proj / IN_REPO_RECORD).exists())
    gone = str(work / "no such dir" / ("python.exe" if _WIN else "python3"))
    check("an interpreter that does not exist is not recorded (the old record stays)",
          PR.write_setup_python(proj, gone) is False and PR.read_setup_python(proj) == sys.executable)
    script = _fake_file(work / "not a python" / _script_name())
    check("a program not named like a Python interpreter (a script) is not recorded (the old record stays)",
          PR.write_setup_python(proj, script) is False and PR.read_setup_python(proj) == sys.executable)
    bare = work / "a-never-synced"
    bare.mkdir()
    check("a never-synced directory: the record goes to the home — nothing is created in the directory",
          PR.write_setup_python(bare) is True and list(bare.iterdir()) == [])

    other = work / "a-other-root"
    bad = (("corrupt JSON", "{nope"), ("not an object", "[1]"),
           ("a relative path", json.dumps({"python": "python.exe", "root": str(proj)})),
           ("no python field", json.dumps({"version": "3.12.1", "root": str(proj)})),
           ("a blank path", json.dumps({"python": "  ", "root": str(proj)})),
           ("a non-string path", json.dumps({"python": 3, "root": str(proj)})),
           ("no root field", json.dumps({"python": sys.executable})),
           ("ANOTHER project's root (a copied / colliding record)",
            json.dumps({"python": sys.executable, "root": str(other)})),
           ("a program not named like a Python interpreter", json.dumps({"python": script, "root": str(proj)})))
    for label, text in bad:
        path.write_text(text, encoding="utf-8")
        check(f"a record with {label} reads as None — port_registry and the hook agree",
              PR.read_setup_python(proj) is None and hs_read(proj) is None,
              repr((PR.read_setup_python(proj), hs_read(proj))))
    path.write_text(json.dumps({"python": sys.executable, "root": str(proj)}), encoding="utf-8")
    check("a valid record: both readers return the interpreter",
          hs_read(proj) == PR.read_setup_python(proj) == sys.executable, repr((hs_read(proj), PR.read_setup_python(proj))))
    if _WIN:
        path.write_text(json.dumps({"python": sys.executable, "root": str(proj).upper()}), encoding="utf-8")
        check("... also when the record spells the root in another case (Windows paths fold case)",
              hs_read(proj) == PR.read_setup_python(proj) == sys.executable)
    path.unlink()
    check("no record -> None (both readers)", PR.read_setup_python(proj) is None and hs_read(proj) is None)

    print("\n-- A2: a DEEPMAP_HOME inside the project's own tree is never used for the record --")
    inner = proj / "dm-home"
    env_in = {"DEEPMAP_HOME": str(inner)}
    check("the writer refuses it — nothing is written into the project",
          PR.write_setup_python(proj, env=env_in) is False and not inner.exists())
    _record(inner, proj, sys.executable)
    check("a record found there is ignored by port_registry's reader", PR.read_setup_python(proj, env=env_in) is None)
    with _Env(DEEPMAP_HOME=str(inner)):
        got = hs_read(proj)
    check("... and by the hook's reader", got is None, repr(got))
    tree = _project(work, "a-tree", git=True, manifest=False)
    pkg = tree / "pkg"
    (pkg / ".deepmap").mkdir(parents=True)
    (pkg / ".deepmap" / "graph_build.json").write_text("{}", encoding="utf-8")
    top_home = tree / "dm-home"   # outside the sync root pkg/, but inside the git work tree
    check("the work tree counts, not just the sync root: a home at the repository's top is refused too",
          PR.write_setup_python(pkg, env={"DEEPMAP_HOME": str(top_home)}) is False and not top_home.exists())
    _record(top_home, pkg, sys.executable)
    with _Env(DEEPMAP_HOME=str(top_home)):
        got = hs_read(pkg)
    check("... and a record there is ignored by both readers",
          PR.read_setup_python(pkg, env={"DEEPMAP_HOME": str(top_home)}) is None and got is None, repr(got))

    print("\n-- A3: clear_setup_python (instance-rm --purge) removes a project's records and only those --")
    mine, theirs, keyless = (_project(work, n) for n in ("a3-mine", "a3-theirs", "a3-keyless"))
    _record(home, mine, sys.executable, project_key="a3key-mine")
    _record(home, theirs, sys.executable, project_key="a3key-theirs")
    _record(home, keyless, sys.executable)
    check("an empty key list removes nothing", PR.clear_setup_python([]) == 0 and _record_path(home, mine).exists())
    n = PR.clear_setup_python(["a3key-mine", "a3key-alias"])
    check("the project's record is removed, another project's and a keyless one are kept",
          n == 1 and not _record_path(home, mine).exists() and _record_path(home, theirs).exists()
          and _record_path(home, keyless).exists(), f"n={n}")


def gate_h_no_shadowing(root: Path, PR) -> None:
    print("\n-- H: the new helpers shadow nothing (a second top-level def silently replaces the first, module-wide) --")
    import ast
    import collections
    for rel in ("build/port_registry.py", "hooks/deepmap_hook_state.py", "build/provision.py",
                "hooks/deepmap_sessionstart_heal.py"):
        tree = ast.parse((root / rel).read_text(encoding="utf-8"))
        names = collections.Counter(n.name for n in tree.body
                                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)))
        dups = sorted(k for k, v in names.items() if v > 1)
        check(f"{rel} defines no top-level function twice", not dups, repr(dups))
    norm = getattr(PR, "_norm_path", None)
    if callable(norm):   # the owner / identity checks' lexical form (an earlier cut of this fix redefined it)
        sample = "Proj" + os.sep + "Sub" + os.sep
        want = os.path.normcase(sample.replace("\\", "/").rstrip("/")).replace("\\", "/")
        check("port_registry._norm_path is still the lexical forward-slash form the owner checks compare in",
              norm(sample) == want, repr((norm(sample), want)))


def gate_b_setup_records(P, work: Path, home: Path) -> None:
    print("\n-- B: setup records its interpreter (machine state) only when it ends with working servers --")
    proj = _project(work, "b-proj", git=True)
    stubs = dict(_source_census_problem=lambda t: None, apply_ports_for=lambda *a, **k: (8801, 8802),
                 resolved_instance=lambda t: ("0123456789ab", 7689, 7476), ensure_neo4j=lambda **k: True,
                 _load=lambda name: types.SimpleNamespace(sync=lambda *a, **k: True, last_verdict="READY",
                                                          last_not_pushed=None),
                 verify_search=lambda *a, **k: True, start_mcp_servers=lambda **k: True,
                 verify_mcp=lambda name, **k: True, write_mcp_json=lambda *a, **k: True,
                 _print_mcp_hint=lambda *a, **k: None, _print_git_hook_hint=lambda *a, **k: None)
    rec_path = _record_path(home, proj)

    def run_setup(target, **over):
        s = dict(stubs, **over)
        saved = {n: getattr(P, n) for n in s}
        out = io.StringIO()
        try:
            for n, f in s.items():
                setattr(P, n, f)
            with redirect_stdout(out), redirect_stderr(out):
                return P.setup(str(target), password="deepmap"), out.getvalue()
        finally:
            for n, f in saved.items():
                setattr(P, n, f)

    ok, out = run_setup(proj)
    rec = json.loads(rec_path.read_text(encoding="utf-8")) if rec_path.is_file() else {}
    check("a READY setup records sys.executable in the DeepMap home, keyed by the project's sync root",
          ok is True and rec.get("python") == sys.executable and rec.get("root") == str(proj),
          f"ok={ok} rec={rec} {out[-300:]}")
    check("... and writes nothing into the project (no .deepmap/setup_python.json)", not (proj / IN_REPO_RECORD).exists())
    sub = proj / "pkg" / "sub"
    sub.mkdir(parents=True)
    rec_path.unlink(missing_ok=True)
    ok, out = run_setup(sub)
    check("setup run from a SUBDIRECTORY records under the sync root's id (where the heal hook looks), not the subdir's",
          ok is True and rec_path.is_file() and not _record_path(home, sub).exists() and not (sub / ".deepmap").exists(),
          f"ok={ok} {out[-300:]}")
    other = _fake_python(work, "other")
    _record(home, proj, other)
    ok, out = run_setup(proj, ensure_neo4j=lambda **k: False)
    rec = json.loads(rec_path.read_text(encoding="utf-8"))
    check("a FAILED setup (Neo4j) never overwrites the record", ok is False and rec.get("python") == other,
          f"ok={ok} rec={rec}")
    ok, out = run_setup(proj, start_mcp_servers=lambda **k: False)
    rec = json.loads(rec_path.read_text(encoding="utf-8"))
    check("a FAILED setup (MCP servers did not start) never overwrites the record", ok is False
          and rec.get("python") == other, f"ok={ok} rec={rec}")
    rec_path.unlink()
    ok, out = run_setup(proj, write_mcp_json=lambda *a, **k: None)
    check("a setup that could not confirm this project's .mcp.json names its servers records nothing",
          not rec_path.exists(), f"ok={ok} {out[-300:]}")


def gate_c_hook(HK, root: Path, home: Path, work: Path) -> None:
    print("\n-- C: the heal hook starts heal with DEEPMAP_PYTHON > the recorded interpreter > its own Python --")
    plugin = str(root)
    prov = str(root / "build" / "provision.py")
    rec_py = _fake_python(work, "recorded")
    env_py = _fake_python(work, "from env")

    def argv_of(calls, i=0):
        return calls[i][0] if len(calls) > i else []

    proj = _project(work, "c-proj", git=True)
    with _Env(DEEPMAP_HOME=str(home), CLAUDE_PLUGIN_ROOT=plugin):
        rc, calls, out = _run_hook(HK, proj)
    check("no record: heal runs under the hook's own Python (unchanged), and it is the ONLY process started",
          rc == 0 and len(calls) == 1 and argv_of(calls)[:1] == [sys.executable]
          and argv_of(calls)[1:3] == [prov, "heal"], repr([c[0][:3] for c in calls]))

    _record(home, proj, rec_py)
    with _Env(DEEPMAP_HOME=str(home), CLAUDE_PLUGIN_ROOT=plugin, __PYVENV_LAUNCHER__=r"C:\elsewhere\venv\python.exe"):
        rc, calls, out = _run_hook(HK, proj)
    argv = argv_of(calls)
    check("a record: heal is started with the RECORDED interpreter — the ONLY process started (no git, no probe)",
          rc == 0 and len(calls) == 1 and argv == [rec_py, prov, "heal", str(proj)], repr([c[0] for c in calls]))
    kw = calls[0][1] if calls else {}
    env = kw.get("env") or {}
    check("... in UTF-8 mode (PYTHONUTF8=1), without a venv redirector's __PYVENV_LAUNCHER__",
          env.get("PYTHONUTF8") == "1" and "__PYVENV_LAUNCHER__" not in env, repr({k: env.get(k) for k in
                                                                                   ("PYTHONUTF8", "__PYVENV_LAUNCHER__")}))
    if _WIN:
        check("... still a hidden console of its own (CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP)",
              kw.get("creationflags") == 0x08000000 | 0x00000200, repr(kw.get("creationflags")))
    else:
        check("... still its own session (start_new_session)", kw.get("start_new_session") is True, repr(kw))
    check("... and the hook printed nothing", out == "", repr(out))
    logf = proj / ".deepmap" / "heal.log"
    log = logf.read_text(encoding="utf-8", errors="replace") if logf.is_file() else ""
    heads = [ln for ln in log.splitlines() if ln.startswith("--- ")]
    check("... the heal.log header names the interpreter and where it came from",
          bool(heads) and rec_py in heads[-1] and "setup" in heads[-1], heads[-1:] or log[-300:])

    sub = proj / "deep" / "er"
    sub.mkdir(parents=True)
    with _Env(DEEPMAP_HOME=str(home), CLAUDE_PLUGIN_ROOT=plugin):
        rc, calls, out = _run_hook(HK, sub)
    check("a session opened in a SUBDIRECTORY heals the project root under the recorded interpreter",
          rc == 0 and argv_of(calls) == [rec_py, prov, "heal", str(proj)], repr(argv_of(calls)))

    with _Env(DEEPMAP_HOME=str(home), CLAUDE_PLUGIN_ROOT=plugin, DEEPMAP_HIBERNATE="1"):
        rc, calls, out = _run_hook(HK, proj)
    check("DEEPMAP_HIBERNATE=1: heal AND the hibernate sweep both use the recorded interpreter",
          len(calls) == 2 and [c[0][0] for c in calls] == [rec_py, rec_py] and calls[1][0][2] == "hibernate",
          repr([c[0][:3] for c in calls]))

    with _Env(DEEPMAP_HOME=str(home), CLAUDE_PLUGIN_ROOT=plugin):
        rc, calls, out = _run_hook(HK, proj, fail_for=(rec_py,))
    check("the recorded interpreter can't be started (spawn OSError): heal falls back to the hook's own Python, once",
          rc == 0 and [c[0][0] for c in calls] == [rec_py, sys.executable] and calls[1][0][1:3] == [prov, "heal"],
          repr([c[0][:3] for c in calls]))

    Path(rec_py).unlink()
    with _Env(DEEPMAP_HOME=str(home), CLAUDE_PLUGIN_ROOT=plugin):
        rc, calls, out = _run_hook(HK, proj)
    check("the recorded interpreter no longer exists: the hook's own Python (no failed spawn first)",
          rc == 0 and [c[0][0] for c in calls] == [sys.executable], repr([c[0][:1] for c in calls]))
    rec_py = _fake_python(work, "recorded")   # recreate it for the precedence checks

    print("\n-- C2: DEEPMAP_PYTHON (the operator's explicit interpreter — provision._hook_python's rule) --")
    with _Env(DEEPMAP_HOME=str(home), CLAUDE_PLUGIN_ROOT=plugin, DEEPMAP_PYTHON=env_py):
        rc, calls, out = _run_hook(HK, proj)
    check("an existing interpreter file wins over the record", [c[0][0] for c in calls] == [env_py],
          repr([c[0][:1] for c in calls]))
    cmdline = (f'"{env_py}" -X utf8' if _WIN else f"'{env_py}' -X utf8")
    with _Env(DEEPMAP_HOME=str(home), CLAUDE_PLUGIN_ROOT=plugin, DEEPMAP_PYTHON=cmdline):
        rc, calls, out = _run_hook(HK, proj)
    check("a command line ('<python> -X utf8', like 'py -3') is started as its words",
          argv_of(calls)[:5] == [env_py, "-X", "utf8", prov, "heal"], repr(argv_of(calls)))
    for label, val in (("a command that is not on PATH", "dm-no-such-python-o1"),
                       ("a command line whose program is not on PATH", "dm-no-such-python-o1 -3"),
                       ("a directory", str(work)), ("unbalanced quotes", '"' + env_py + " -X")):
        with _Env(DEEPMAP_HOME=str(home), CLAUDE_PLUGIN_ROOT=plugin, DEEPMAP_PYTHON=val):
            rc, calls, out = _run_hook(HK, proj)
        check(f"DEEPMAP_PYTHON = {label}: falls back to the recorded interpreter",
              rc == 0 and [c[0][0] for c in calls] == [rec_py], repr([c[0][:2] for c in calls]))
    _record_path(home, proj).unlink()


def gate_f_foreign_records(HK, root: Path, home: Path, work: Path) -> None:
    print("\n-- F: nothing a repository ships can pick the program the heal hook starts --")
    plugin = str(root)
    proj = _project(work, "f-proj", git=True)
    named_py = _fake_file(proj / ".deepmap" / ("python.exe" if _WIN else "python3"))   # python-NAMED, in the repo
    script = _fake_file(proj / ".deepmap" / _script_name())
    outside = _fake_python(work, "outside the repo")

    def hook(**env):
        with _Env(DEEPMAP_HOME=str(home), CLAUDE_PLUGIN_ROOT=plugin, **env):
            return _run_hook(HK, proj)

    for label, target in (("a python-named program inside the repo", named_py),
                          ("a script inside the repo", script),
                          ("an interpreter-named program outside the repo", outside)):
        _in_repo_record(proj, target)
        rc, calls, out = hook()
        argvs = [c[0] for c in calls]
        check(f"a committed {IN_REPO_RECORD} naming {label}: heal runs under the hook's own Python, once",
              rc == 0 and len(calls) == 1 and argvs[0][:1] == [sys.executable]
              and not any(target in a for a in argvs), repr([a[:2] for a in argvs]))
    legit = _fake_python(work, "legit")
    _record(home, proj, legit)
    _in_repo_record(proj, named_py)
    rc, calls, out = hook()
    check(f"... and beside a real record the {IN_REPO_RECORD} file changes nothing (the record is used)",
          rc == 0 and [c[0][0] for c in calls] == [legit], repr([c[0][:1] for c in calls]))
    (proj / IN_REPO_RECORD).unlink()

    other = _project(work, "f-other", git=True)
    _record_path(home, proj).write_text(json.dumps({"python": legit, "root": str(other)}), encoding="utf-8")
    rc, calls, out = hook()
    check("a record at this root's id that names ANOTHER root is not used (the hook's own Python)",
          rc == 0 and [c[0][0] for c in calls] == [sys.executable], repr([c[0][:1] for c in calls]))
    outside_script = _fake_file(work / "scripts" / _script_name())
    _record(home, proj, outside_script)
    rc, calls, out = hook()
    check("a record naming a program that is not named like a Python interpreter is not used",
          rc == 0 and [c[0][0] for c in calls] == [sys.executable], repr([c[0][:1] for c in calls]))
    _record_path(home, proj).unlink()

    inner = proj / "dm-home"
    _record(inner, proj, legit)
    with _Env(DEEPMAP_HOME=str(inner), CLAUDE_PLUGIN_ROOT=plugin):
        rc, calls, out = _run_hook(HK, proj)
    check("a DEEPMAP_HOME inside the project: a record there is not used (the project's files could supply it)",
          rc == 0 and [c[0][0] for c in calls] == [sys.executable], repr([c[0][:1] for c in calls]))

    venv_py = _fake_file(proj / ".venv" / ("Scripts/python.exe" if _WIN else "bin/python"))
    _record(home, proj, venv_py)
    rc, calls, out = hook()
    check("the INTERPRETER may live in the project (a project venv the operator ran setup under) — only the record "
          "must be DeepMap's", rc == 0 and [c[0][0] for c in calls] == [venv_py], repr([c[0][:1] for c in calls]))
    _record_path(home, proj).unlink()


def gate_f2_env_never_resolves_in_project(HK, root: Path, home: Path, work: Path) -> None:
    print("\n-- F2: DEEPMAP_PYTHON is never resolved against the hook's working directory (the project) --")
    plugin = str(root)
    proj = _project(work, "f2-proj", git=True)
    rec_py = _fake_python(work, "f2 recorded")
    _record(home, proj, rec_py)
    ext = ".cmd" if _WIN else ""
    planted = _fake_file(proj / f"dm-o1-cwd-py{ext}")            # a program the repository ships in its root
    _fake_file(proj / "tools" / f"dm-o1-cwd-py{ext}")
    bindir = work / "f2 bin"
    on_path = _fake_file(bindir / f"dm-o1-path-py{ext}")
    _fake_file(proj / f"dm-o1-path-py{ext}")                      # the same name, planted in the project too
    path0 = os.environ.get("PATH", "")
    rel_tools = os.path.join("tools", f"dm-o1-cwd-py{ext}")
    cases = [("a bare name only the project's root provides", "dm-o1-cwd-py", None),
             ("a command line whose program only the project's root provides", "dm-o1-cwd-py -3", None),
             ("a relative path into the project", rel_tools, None),
             ("a ./-relative path into the project", "." + os.sep + f"dm-o1-cwd-py{ext}", None),
             ("a bare name on an absolute PATH dir, with '.' first on PATH and a copy in the project",
              "dm-o1-path-py", "." + os.pathsep + str(bindir) + os.pathsep + path0)]
    saved_cwd = os.getcwd()
    try:
        for label, val, path in cases:
            env = dict(DEEPMAP_HOME=str(home), CLAUDE_PLUGIN_ROOT=plugin, DEEPMAP_PYTHON=val)
            saved_path = os.environ.get("PATH")
            try:
                if path is not None:
                    os.environ["PATH"] = path
                os.chdir(proj)   # hooks run in the session's directory — the project
                with _Env(**env):
                    rc, calls, out = _run_hook(HK, proj)
            finally:
                os.chdir(saved_cwd)
                if saved_path is not None:
                    os.environ["PATH"] = saved_path
            first = [c[0][0] for c in calls]
            want = on_path if path is not None else rec_py
            check(f"DEEPMAP_PYTHON = {label}: never the project's file "
                  f"({'the PATH one' if path is not None else 'falls back to the recorded interpreter'})",
                  rc == 0 and len(first) == 1 and _same_file(first[0], want)
                  and not any(_inside(a, proj) for a in first if os.path.isabs(str(a)))
                  and not any(not os.path.isabs(str(a)) for a in first), repr(first))
    finally:
        os.chdir(saved_cwd)
    check("(the planted files were real, so a cwd lookup would have found them)", os.path.isfile(planted))
    _record_path(home, proj).unlink()


def gate_d_one_split_rule(P, PR) -> None:
    print("\n-- D: ONE rule splits a command-line DEEPMAP_PYTHON (the post-commit block and the heal hook agree) --")
    split = getattr(PR, "split_command_line", None)
    check("port_registry.split_command_line exists", callable(split))
    if not callable(split):
        return
    cases = ["py -3", "/usr/bin/env python3", "python3 -X utf8", '"unbalanced -3', ""]
    cases += ([r'"C:\Program Files\Py\python.exe" -X utf8', r"C:\Py\python.exe -3"] if _WIN
              else ["'/opt/my py/bin/python3' -X utf8", r"/opt/a\ b/python3 -3"])
    for c in cases:
        check(f"provision._split_command_line({c!r}) == port_registry.split_command_line (this OS)",
              P._split_command_line(c) == split(c, windows=P.is_windows()), repr((P._split_command_line(c), split(c))))


def gate_g_purge(P, work: Path, home: Path) -> None:
    print("\n-- G: instance-rm --purge removes the project's recorded interpreter (and only its own) --")
    mine, theirs = _project(work, "g-mine"), _project(work, "g-theirs")
    _record(home, mine, sys.executable, project_key="gkey-mine")
    _record(home, theirs, sys.executable, project_key="gkey-theirs")
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        P._remove_instance_locks("gkey-mine", purge=False)
    check("a plain instance-rm keeps it (the project stays registered)", _record_path(home, mine).exists())
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        P._remove_instance_locks("gkey-mine", ["gkey-alias"], purge=True)
    check("--purge removes it", not _record_path(home, mine).exists())
    check("... and never another project's", _record_path(home, theirs).exists())
    _record(home, mine, sys.executable, project_key="gkey-alias")
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        P._remove_instance_locks("gkey-mine", ["gkey-alias"], purge=True)
    check("... including a record under one of the project's migrated keys (aliases)",
          not _record_path(home, mine).exists() and _record_path(home, theirs).exists())


_STUB_PROVISION = r'''import json, os, sys
with open(os.environ["O1_HEAL_REC"], "a", encoding="utf-8") as f:
    f.write(json.dumps({"exe": sys.executable, "argv": sys.argv[1:]}) + "\n")
'''


def _wait_lines(path: Path, n: int = 1, wait_s: float = 60.0) -> list:
    deadline = time.monotonic() + wait_s
    lines: list = []
    while time.monotonic() < deadline:
        lines = [ln for ln in (path.read_text(encoding="utf-8").splitlines() if path.exists() else []) if ln.strip()]
        if len(lines) >= n:
            break
        time.sleep(0.2)
    time.sleep(1.0)
    lines = [ln for ln in (path.read_text(encoding="utf-8").splitlines() if path.exists() else []) if ln.strip()]
    return [json.loads(ln) for ln in lines]


def _real_hook(plugin: Path, home: Path, proj: Path, recf: Path, marker: Path | None = None, extra=None):
    scrub = {s.upper() for s in _SCRUB}
    env = {k: v for k, v in os.environ.items() if k.upper() not in scrub}
    env.update({"DEEPMAP_HOME": str(home), "CLAUDE_PLUGIN_ROOT": str(plugin), "O1_HEAL_REC": str(recf)})
    env.update(extra or {})
    if marker is not None:
        env["O1_PWNED"] = str(marker)
    return subprocess.run([sys.executable, "-X", "utf8", "-X", "importtime",
                           str(plugin / "hooks" / "deepmap_sessionstart_heal.py")],
                          input=json.dumps({"cwd": str(proj), "session_id": "o1e"}).encode("utf-8"),
                          capture_output=True, env=env, cwd=str(proj), timeout=120)


def gate_e_real_process(root: Path, base: Path) -> None:
    print("\n-- E: REAL processes — the real hook starts heal under a DIFFERENT, recorded interpreter --")
    venv = base / "venv o1"
    p = subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv)], capture_output=True, timeout=300)
    vpy = venv / ("Scripts/python.exe" if _WIN else "bin/python")
    if p.returncode != 0 or not vpy.is_file():
        check("(a throwaway venv interpreter could be created — skipped otherwise)", True,
              p.stderr.decode("utf-8", "replace")[-300:])
        return
    plugin = base / "plug in" / "deep-map"
    (plugin / "hooks").mkdir(parents=True)
    (plugin / "build").mkdir()
    for f in (root / "hooks").glob("*.py"):
        shutil.copyfile(f, plugin / "hooks" / f.name)
    shutil.copyfile(root / "build" / "port_registry.py", plugin / "build" / "port_registry.py")
    (plugin / "build" / "provision.py").write_text(_STUB_PROVISION, encoding="utf-8")
    home = base / "home e"
    home.mkdir()
    for label, with_record in (("no record (control)", False), ("a record naming the venv interpreter", True)):
        proj = _project(base, f"proj e{int(with_record)}")
        recf = base / f"heal-{int(with_record)}.jsonl"
        if with_record:
            _record(home, proj, str(vpy))
        r = _real_hook(plugin, home, proj, recf)
        lines = _wait_lines(recf)
        want = str(vpy) if with_record else sys.executable
        got = lines[0].get("exe") if lines else None
        check(f"{label}: exactly one heal, under {'the venv interpreter' if with_record else 'the hook Python'}",
              r.returncode == 0 and r.stdout == b"" and len(lines) == 1 and _same_file(got, want)
              and lines[0].get("argv", [None])[0] == "heal",
              f"rc={r.returncode} out={r.stdout[:80]!r} lines={lines} want={want}")
        check(f"{label}: the hook never imports port_registry on this path (it is on the session-start clock)",
              b"port_registry" not in r.stderr, r.stderr.decode("utf-8", "replace")[-300:])

    print("\n-- E2: REAL processes — a repository that SHIPS .deepmap/setup_python.json picks nothing --")
    proj = _project(base, "proj evil")
    marker = base / "pwned.txt"
    if _WIN:
        script = _fake_file(proj / ".deepmap" / "x.cmd", '@echo pwned> "%O1_PWNED%"\n')
    else:
        script = _fake_file(proj / ".deepmap" / "x.sh", '#!/bin/sh\necho pwned > "$O1_PWNED"\n')
    variants = [("a hostile script in the repo", script)]
    if not _WIN and os.path.isdir("/proc/self/cwd"):
        # the review's proof of concept: an absolute path a repository can commit without knowing where it is cloned
        variants.append(("/proc/self/cwd/.deepmap/x.sh (no clone path needed)", "/proc/self/cwd/.deepmap/x.sh"))
    variants.append(("the venv interpreter", str(vpy)))
    for i, (label, target) in enumerate(variants):
        _in_repo_record(proj, target)
        recf = base / f"heal-evil-{i}.jsonl"
        if marker.exists():
            marker.unlink()
        r = _real_hook(plugin, home, proj, recf, marker)
        lines = _wait_lines(recf, wait_s=30.0)
        got = lines[0].get("exe") if lines else None
        check(f"REAL hook, the repo's file names {label}: the named program never runs — one heal, under the hook's "
              "own Python", r.returncode == 0 and r.stdout == b"" and not marker.exists() and len(lines) == 1
              and _same_file(got, sys.executable),
              f"rc={r.returncode} marker={marker.exists()} lines={lines}")
    (proj / IN_REPO_RECORD).unlink()

    print("\n-- E3: REAL processes — a DEEPMAP_PYTHON that names no absolute program never finds the project's file --")
    body = '@echo pwned> "%O1_PWNED%"\n' if _WIN else '#!/bin/sh\necho pwned > "$O1_PWNED"\n'
    planted = _fake_file(proj / ("dm-o1-cwd-py.cmd" if _WIN else "dm-o1-cwd-py"), body)
    env_cases = [("a ./-relative path to the project's file", "." + os.sep + os.path.basename(planted))]
    if _WIN:   # the one OS whose command search (shutil.which, CreateProcess) looks in the working directory first
        env_cases.append(("a bare command name the project's root provides", "dm-o1-cwd-py"))
    for i, (label, val) in enumerate(env_cases):
        recf = base / f"heal-env-{i}.jsonl"
        if marker.exists():
            marker.unlink()
        r = _real_hook(plugin, home, proj, recf, marker, {"DEEPMAP_PYTHON": val})
        lines = _wait_lines(recf, wait_s=30.0)
        got = lines[0].get("exe") if lines else None
        check(f"REAL hook, DEEPMAP_PYTHON = {label}: the project's program never runs — one heal, under the hook's "
              "own Python", r.returncode == 0 and r.stdout == b"" and not marker.exists() and len(lines) == 1
              and _same_file(got, sys.executable),
              f"rc={r.returncode} marker={marker.exists()} lines={lines}")


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
    print(f"DeepMap — the SessionStart heal runs under setup's interpreter; no repository picks it (root: {root})")
    print("=" * 72)
    for k in _SCRUB:
        os.environ.pop(k, None)
    base = Path(tempfile.mkdtemp(prefix="o1heal")).resolve()
    try:
        home = base / "home"
        home.mkdir()
        os.environ["DEEPMAP_HOME"] = str(home)
        P, PR, HS, HK = _load(root)
        work = base / "work"
        work.mkdir()
        gate_a_registry_helpers(PR, HS, work, home)
        gate_h_no_shadowing(root, PR)
        gate_b_setup_records(P, work, home)
        gate_c_hook(HK, root, home, work)
        gate_f_foreign_records(HK, root, home, work)
        gate_f2_env_never_resolves_in_project(HK, root, home, work)
        gate_d_one_split_rule(P, PR)
        gate_g_purge(P, work, home)
        gate_e_real_process(root, base)
    finally:
        shutil.rmtree(base, ignore_errors=True)
    total = len(PASSES) + len(FAILS)
    print("\n" + "=" * 72)
    if FAILS:
        print(f"RESULT: RED — {len(PASSES)}/{total} gates passed.")
        return 1
    print(f"RESULT: GREEN — {len(PASSES)}/{total} gates passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
