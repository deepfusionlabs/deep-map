#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fix round r283, owner F5 — test hygiene: the gates that drive provision.setup() never touch the machine's real
DeepMap state or this repo, and two gate texts say what is true.

A  Sandboxed setup gates (O1's round-1 note). tests/audit_fix_gates.py (gate_setup_verify_guard: setup("x")) and
   tests/provision_robustness_gates.py (gate_setup_fails_loud_on_mcp_failure: setup(".")) drove provision.setup()
   against the REAL DEEPMAP_HOME with targets inside this repo. With apply_ports_for / write_mcp_json / ensure_neo4j /
   the sync / the MCP start stubbed, setup() still clears the target's setup blocker (_clear_setup_blocker ->
   os.remove <DEEPMAP_HOME>/run/setup-blocked-<key>.json — for "." this repo's REAL blocker, the record heal and the
   SessionStart hooks honour), runs git on the target, and for a local base URI records the base store's owner
   (_record_base_owner: a probe of the real 7687/7474, a JVM listing, possibly a rewrite of the base store's
   run/neo4j.pid, then <home>/neo4j/neo4j-community-*/data/deepmap_base_owner.json). Measured on the dev box: that
   real record reads {"root": ".../<this repo>/x", "source": "setup-sync"} (2026-09-25) — audit_fix_gates'
   setup("x"); round-0 code also wrote <repo>/.deepmap/setup_python.json and ~/.deepmap/run/setup-python-<key>.json
   through these two gates.
   Each file runs here in a child Python under an audit hook (sys.addaudithook) that records every filesystem
   mutation (open for writing, remove, rename/replace, mkdir, rmdir, rmtree, utime, chmod, truncate, link, copy),
   socket connect, signal and process start. The child's HOME / USERPROFILE / DEEPMAP_HOME point at a throwaway
   MACHINE HOME seeded with canary DeepMap state (a registry, this project's setup blocker, a base-store owner
   record) and its TMPDIR / TEMP / TMP at a private temp dir; bytecode writes are off (-B). Asserted per file: it is
   GREEN there; every mutation lands in that private temp dir — none in the machine home (event level, and its
   listing + mtimes are unchanged), in the tree (.deepmap/, .mcp.json or anywhere), in the caller's own DeepMap
   home, or anywhere else; no socket is connected; no process is signalled; the only processes started are git
   probes of a temp dir, never of the tree (plus the stdlib's own read-only platform probes, e.g. Windows `cmd /c
   ver` when platform's WMI query times out). A name POSIX shutil.rmtree removes relative to a directory fd is
   resolved in the child, never read as a tree path. A self-test first proves the harness goes RED on each
   violation kind.
B  tests/sync_r10_gates.py gate H compares the working tree with `git show HEAD:` — tautological once a change is
   committed (and always in CI's fresh checkout). Its docstrings must say so and name the durable locks, which must
   exist: tests/o12_learned_fusion_lock_r283_gates.py (learned_fusion.py) and tests/fts_r7_gates.py (_FROZEN).
C  tests/delphi_pascal_gates.py: no check label claims a `--strict` flag (there is none — build/verdict.py
   exit_code() is an unwired helper, ISS-build:026), and the DEGRADED -> 0 / strict=True -> 3 assertion stays.

Hermetic: temp dirs only (the children's too); no Neo4j, no network, no real ~/.deepmap. Section A starts three child
Pythons (a few seconds each).
Usage: python tests/f5_test_hygiene_r283_gates.py [--root <tree>]
"""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import re
import site
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PASSES: list[str] = []
FAILS: list[str] = []

SETUP_GATES = ("tests/audit_fix_gates.py", "tests/provision_robustness_gates.py")


def check(name: str, ok: bool, detail="") -> None:
    (PASSES if ok else FAILS).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok or detail == "" else f" — {str(detail)[:900]}"))


# ---- A: the audit-hooked child ----------------------------------------------------------------------------------------
# Runs <gate> as __main__ (sys.argv / sys.path[0] as `python <gate>` sets them) and dumps what it did to <out>. The hook
# only appends to a list; the dump happens with recording off, outside the private temp dir.
_CHILD = r'''
import json, os, runpy, sys, threading
gate, out = sys.argv[1], sys.argv[2]
_W = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
_ev, _on, _tl = [], [True], threading.local()   # _on: recording enabled; _tl.busy: this thread is inside the hook
# event -> index of its dir_fd argument (None: the event carries none). POSIX shutil.rmtree removes entries by NAME
# relative to a directory fd, so a bare name must be joined to that fd's directory, never to the working directory.
_PATH1 = {"os.remove": 1, "os.rmdir": 1, "os.mkdir": 2, "shutil.rmtree": 1, "os.truncate": None, "os.utime": 3,
          "os.chmod": 2, "os.chown": 3, "os.chflags": None, "os.lchflags": None, "os.setxattr": None,
          "os.removexattr": None}
_PATH2 = {"os.rename": (2, 3), "os.link": (2, 3), "shutil.copyfile": (None, None), "shutil.copymode": (None, None),
          "shutil.copystat": (None, None), "shutil.copytree": (None, None), "shutil.move": (None, None)}


def _p(a):
    try:
        return os.fsdecode(os.fspath(a))
    except Exception:
        return None


def _fd_dir(fd):
    """The directory an open fd names (Linux /proc, macOS F_GETPATH), or None."""
    try:
        if sys.platform.startswith("linux"):
            return os.readlink("/proc/self/fd/%d" % fd)
        if sys.platform == "darwin":
            import fcntl
            return os.fsdecode(fcntl.fcntl(fd, fcntl.F_GETPATH, bytes(1024)).split(b"\0", 1)[0])
    except Exception:
        pass
    return None


def _at(path, args, i):
    """`path` resolved against the event's dir_fd (args[i]) when it has one; a relative name whose fd cannot be
    resolved is marked so the parent never mistakes it for a working-directory path."""
    p = _p(path)
    fd = args[i] if i is not None and i < len(args) else None
    if p is None or os.path.isabs(p) or not isinstance(fd, int) or isinstance(fd, bool) or fd < 0:
        return p
    d = _fd_dir(fd)
    return os.path.join(d, p) if d else "<dir_fd %d>/%s" % (fd, p)


def _argv(a):
    a = [a] if isinstance(a, (str, bytes)) or not hasattr(a, "__iter__") else list(a)
    return [_p(x) or repr(x) for x in a]


def _hook(name, args):
    if not _on[0] or getattr(_tl, "busy", False):
        return
    _tl.busy = True   # nothing the hook itself does is recorded (e.g. resolving a dir_fd); other threads still are
    try:
        if name == "open":
            path, mode, flags = args[0], args[1], args[2]
            if not isinstance(path, int) and ((isinstance(mode, str) and any(c in mode for c in "wax+"))
                                               or (isinstance(flags, int) and flags & _W)):
                _ev.append(["write", _p(path)])
        elif name in _PATH1:
            if not isinstance(args[0], int):
                _ev.append([name, _at(args[0], args, _PATH1[name])])
        elif name in _PATH2:
            i, j = _PATH2[name]
            _ev.append([name, _at(args[0], args, i), _at(args[1], args, j)])
        elif name == "os.symlink":   # (target text, link path, dir_fd): the link is what gets created
            _ev.append([name, _at(args[1], args, 2)])
        elif name == "subprocess.Popen":
            _ev.append(["spawn", _argv(args[1]), _p(args[2]) if args[2] is not None else None])
        elif name in ("os.posix_spawn", "os.exec"):
            _ev.append(["spawn", _argv(args[1]), None])
        elif name == "os.spawn":
            _ev.append(["spawn", _argv(args[2]), None])
        elif name in ("os.system", "os.startfile", "os.fork", "os.forkpty"):
            _ev.append(["spawn-other", name, repr(args)[:300]])
        elif name == "socket.connect":
            _ev.append(["connect", repr(args[1])])
        elif name in ("os.kill", "os.killpg"):
            _ev.append(["signal", name, repr(args)])
    except Exception as exc:
        _ev.append(["hook-error", name, repr(exc)])
    finally:
        _tl.busy = False


sys.addaudithook(_hook)
sys.argv = [gate]
sys.path[0] = os.path.dirname(os.path.abspath(gate))
rc = 1
try:
    runpy.run_path(gate, run_name="__main__")
    rc = 0
except SystemExit as exc:
    rc = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
finally:
    _on[0] = False
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"rc": rc, "events": _ev}, fh)
sys.exit(rc)
'''

# A synthetic gate for the harness self-test: one violation of every kind. Everything real it does stays inside the
# box (the canary it removes is the box's); the tree write, the connect, the git probe and the signal are raised as
# audit events only (sys.audit) — nothing is written to a tree, nothing connects, nothing is spawned or signalled.
_BAD_GATE = r'''
import os, shutil, sys, tempfile
from pathlib import Path
home = Path(os.environ["DEEPMAP_HOME"])
for p in sorted((home / "run").glob("setup-blocked-*.json")):
    p.unlink()                                            # the O1 defect: the machine's setup blocker removed
scratch = Path(tempfile.mkdtemp(prefix="f5_rmtree_"))     # legitimate: the private temp dir
(scratch / "sub").mkdir()
(scratch / "sub" / "f5_leaf.txt").write_text("x", encoding="utf-8")
shutil.rmtree(scratch)                                    # POSIX: removes entries by dir_fd-relative NAME
tree = Path.cwd()
sys.audit("open", str(tree / ".deepmap" / "setup_python.json"), "w", 0)
sys.audit("socket.connect", None, ("127.0.0.1", 7687))
sys.audit("subprocess.Popen", None, ["git", "-C", str(tree), "rev-parse", "--show-toplevel"], None, None)
sys.audit("os.kill", 4242, 15)
print("RESULT: GREEN - 1/1 gates passed.")
'''


def _norm(p) -> str:
    return os.path.normcase(os.path.realpath(p))


def _under(p, root) -> bool:
    p, root = _norm(p), _norm(root)
    return p == root or p.startswith(root.rstrip("\\/") + os.sep)


def _abs(p, cwd: Path) -> str:
    return p if os.path.isabs(p) else os.path.join(str(cwd), p)


def _split_cmdline(argv: list) -> list:
    """A Windows command line arrives as ONE string (subprocess joins the list before its audit event): split it,
    honouring double quotes. A POSIX argv is already a list."""
    if len(argv) != 1 or not isinstance(argv[0], str) or " " not in argv[0]:
        return list(argv)
    return [a if a else b for a, b in re.findall(r'"((?:[^"\\]|\\.)*)"|(\S+)', argv[0])]


def _prog(tok) -> str:
    """A program token's lower-cased file name — split on both separators, so a Windows path reads right anywhere."""
    return re.split(r"[\\/]", str(tok).strip('"'))[-1].lower()


def _is_git(tok) -> bool:
    return _prog(tok) in ("git", "git.exe", "git.cmd", "git.bat")   # Windows PATH may resolve git to a .cmd shim


def _stdlib_probe(argv: list) -> bool:
    """A read-only probe Python's own `platform` module may start (not DeepMap's doing, and not deterministic):
    Windows `cmd /c ver` — platform._syscmd_ver, used when platform's WMI query times out (seen under load) — and POSIX
    `uname` / `file` (platform.processor / platform.architecture)."""
    if not argv:
        return False
    name = _prog(argv[0])
    rest = [str(a).strip('"').lower() for a in argv[1:]]
    return (name in ("cmd", "cmd.exe") and rest == ["/c", "ver"]) or name in ("uname", "file")


_DEVNULL = os.path.normcase(os.devnull)   # subprocess / platform open it for writing: never a file anywhere


def _snapshot(root: Path) -> dict:
    """{relpath: (is_dir, size, mtime_ns)} of everything under `root` — the listing + mtimes."""
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for n in dirnames + filenames:
            p = Path(dirpath) / n
            try:
                st = p.stat()
            except OSError:
                continue
            out[p.relative_to(root).as_posix()] = (p.is_dir(), 0 if p.is_dir() else st.st_size, st.st_mtime_ns)
    return out


def _tree_project_key(tree: Path, dm_home: Path):
    """The key the tree's own port_registry gives the project the gate runs in (cwd = the tree) — the key whose setup
    blocker an unsandboxed setup(".") clears. None when the tree's registry module can't say."""
    try:
        spec = importlib.util.spec_from_file_location("_f5_port_registry", tree / "build" / "port_registry.py")
        PR = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(PR)
        try:
            return PR.project_key(str(tree), env={"DEEPMAP_HOME": str(dm_home)}, migrate=False)
        except TypeError:
            return PR.project_key(str(tree))
    except Exception:
        return None


def _default_neo4j_version(tree: Path):
    try:
        m = re.search(r'^DEFAULT_VERSION\s*=\s*"([^"]+)"', (tree / "build" / "provision.py").read_text(encoding="utf-8"),
                      re.M)
        return m.group(1) if m else None
    except OSError:
        return None


def _seed_machine_home(mh: Path, tree: Path) -> list:
    """Canary DeepMap state an unsandboxed setup could touch; returns what was seeded (relpaths)."""
    dm = mh / ".deepmap"
    (dm / "run").mkdir(parents=True)
    seeded = []

    def put(rel: str, rec: dict) -> None:
        p = dm / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(rec), encoding="utf-8", newline="\n")
        seeded.append(".deepmap/" + rel)

    put("registry.json", {"kind": "deepmap_port_registry", "version": 1, "projects": {},
                          "updated_utc": "2026-09-30T00:00:00Z"})
    key = _tree_project_key(tree, dm)
    if key:
        put(f"run/setup-blocked-{key}.json", {"kind": "mcp-blocked", "reason": "F5 canary: a held MCP port",
                                               "servers": ["deepmap-search"], "ports": [8787], "pids": [],
                                               "ts_epoch": 1.0, "project_key": key})
    ver = _default_neo4j_version(tree)
    if ver:
        put(f"neo4j/neo4j-community-{ver}/data/deepmap_base_owner.json",
            {"root": str(mh / "another-project").replace("\\", "/"), "source": "setup-sync",
             "ts": "2026-09-30T00:00:00Z"})
    return seeded


def _run_in_harness(gate: Path, cwd: Path, box: Path, name: str) -> dict:
    """Run `gate` in the audit-hooked child (cwd = `cwd`) against the box's machine home + private temp dir.
    Returns {rc, events, out, before, after, mh, tmp, error}."""
    mh, ctmp = box / "machine_home", box / "tmp"
    ctmp.mkdir(exist_ok=True)
    child = box / "child.py"
    if not child.exists():
        child.write_text(_CHILD, encoding="utf-8")
    out = box / f"events_{name}.json"
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("DEEPMAP_", "NEO4J_")) and k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR",
                                                                     "GIT_INDEX_FILE", "GIT_CEILING_DIRECTORIES")}
    env.update(HOME=str(mh), USERPROFILE=str(mh), DEEPMAP_HOME=str(mh / ".deepmap"),
               TMPDIR=str(ctmp), TEMP=str(ctmp), TMP=str(ctmp),
               PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
    try:
        env["PYTHONUSERBASE"] = site.getuserbase()   # user-site packages stay importable under the moved HOME
    except Exception:
        pass
    before = _snapshot(mh)
    res = {"rc": None, "events": [], "out": "", "before": before, "after": before, "mh": mh, "tmp": ctmp,
           "error": None}
    try:
        p = subprocess.run([sys.executable, "-B", str(child), str(gate), str(out)], cwd=str(cwd), env=env,
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
        res["out"] = (p.stdout or "") + (p.stderr or "")
        data = json.loads(out.read_text(encoding="utf-8"))
        res["rc"], res["events"] = data.get("rc"), data.get("events") or []
    except Exception as exc:  # noqa: BLE001 — reported as a failed check, not a crash
        res["error"] = f"{type(exc).__name__}: {exc}"
    res["after"] = _snapshot(mh)
    return res


def _violations(res: dict, tree: Path, cwd: Path, real_home: Path) -> dict:
    """Classify the child's events: {kind: [event, ...]}."""
    mh, ctmp = res["mh"], res["tmp"]
    v = {"machine_home": [], "tree": [], "real_home": [], "elsewhere": [], "connect": [], "signal": [],
         "spawn": [], "hook_error": [], "in_tmp": []}
    for ev in res["events"]:
        kind = ev[0]
        if kind == "connect":
            v["connect"].append(ev)
        elif kind == "signal":
            v["signal"].append(ev)
        elif kind == "spawn-other":
            v["spawn"].append(ev)
        elif kind == "hook-error":
            v["hook_error"].append(ev)
        elif kind == "spawn":
            argv = _split_cmdline(ev[1])
            if _stdlib_probe(argv):
                continue
            where = None
            if argv and _is_git(argv[0]):
                if "-C" in argv[:-1]:
                    where = _abs(argv[argv.index("-C") + 1].strip('"'), cwd)
                else:
                    where = _abs(ev[2], cwd) if ev[2] else str(cwd)
            if where is None or not _under(where, ctmp):
                v["spawn"].append(ev)
        else:
            for p in ev[1:]:
                if not p or os.path.normcase(p) == _DEVNULL:
                    continue
                if p.startswith("<dir_fd "):   # a name under a directory fd the child could not resolve
                    v["elsewhere"].append(ev)
                    break
                p = _abs(p, cwd)
                if _under(p, mh):
                    v["machine_home"].append(ev)
                elif _under(p, ctmp):
                    v["in_tmp"].append(ev)
                    continue
                elif _under(p, tree):
                    v["tree"].append(ev)
                elif _under(p, real_home):
                    v["real_home"].append(ev)
                else:
                    v["elsewhere"].append(ev)
                break
    return v


def _diff(before: dict, after: dict) -> list:
    keys = sorted(set(before) | set(after))
    return [(k, before.get(k), after.get(k)) for k in keys if before.get(k) != after.get(k)]


def _real_deepmap_home() -> Path:
    return Path(os.environ.get("DEEPMAP_HOME") or (Path.home() / ".deepmap"))


def gate_a_selftest() -> None:
    print("\n-- A (self-test): the harness goes RED on every violation kind --")
    real_home = _real_deepmap_home()
    with tempfile.TemporaryDirectory(prefix="f5_harness_selftest_") as td:
        box = Path(td)
        tree = box / "faketree"
        (tree / "tests").mkdir(parents=True)
        (tree / ".deepmap").mkdir()
        gate = tree / "tests" / "bad_gate.py"
        gate.write_text(_BAD_GATE, encoding="utf-8")
        (box / "machine_home" / ".deepmap" / "run").mkdir(parents=True)
        (box / "machine_home" / ".deepmap" / "run" / "setup-blocked-selftest.json").write_text("{}", encoding="utf-8")
        res = _run_in_harness(gate, tree, box, "selftest")
        v = _violations(res, tree, tree, real_home)
        check("A (self-test) the synthetic gate ran in the harness (rc 0, events dumped)",
              res["error"] is None and res["rc"] == 0 and res["events"], res["error"] or res["out"][-600:])
        check("A (self-test) a removed machine-home setup blocker is caught (event + listing)",
              bool(v["machine_home"]) and bool(_diff(res["before"], res["after"])), v["machine_home"])
        check("A (self-test) a write into the tree's .deepmap/ is caught", bool(v["tree"]), v)
        check("A (self-test) a temp-dir rmtree stays in the temp dir (POSIX dir_fd-relative names resolved, never "
              "read as tree paths)",
              any("f5_leaf.txt" in str(e) for e in v["in_tmp"])
              and all(".deepmap" in str(e) for e in v["tree"] + v["elsewhere"]),
              {"tree": v["tree"], "elsewhere": v["elsewhere"]})
        check("A (self-test) a socket connect is caught", bool(v["connect"]), v)
        check("A (self-test) a git probe of the tree (not a temp dir) is caught", bool(v["spawn"]), v)
        check("A (self-test) a signal is caught", bool(v["signal"]), v)
    check("A (self-test) only the stdlib's read-only platform probes are exempt: `cmd /c ver`, not `cmd /c <other>`",
          _stdlib_probe(_split_cmdline(['C:\\WINDOWS\\system32\\cmd.exe /c "ver"']))
          and not _stdlib_probe(_split_cmdline(['C:\\WINDOWS\\system32\\cmd.exe /c "del x"']))
          and not _stdlib_probe(["git", "-C", "x", "status"]) and not _stdlib_probe([]))
    check("A (self-test) Windows command lines split on quotes (a quoted git path, a quoted -C dir with a space)",
          _split_cmdline(['"C:\\Program Files\\Git\\bin\\git.EXE" -c core.longpaths=true -C "C:\\a b\\proj" status'])
          == ["C:\\Program Files\\Git\\bin\\git.EXE", "-c", "core.longpaths=true", "-C", "C:\\a b\\proj", "status"])


def gate_a_setup_gates(root: Path) -> None:
    print("\n-- A: the setup-driving gates run sealed: nothing lands in the machine home, the repo, or anywhere else --")
    real_home = _real_deepmap_home()
    for rel in SETUP_GATES:
        gate = root / rel
        name = Path(rel).stem
        if not gate.is_file():
            check(f"A {rel} exists", False, str(gate))
            continue
        with tempfile.TemporaryDirectory(prefix=f"f5_{name[:12]}_") as td:
            box = Path(td)
            seeded = _seed_machine_home(box / "machine_home", root)
            res = _run_in_harness(gate, root, box, name)
            v = _violations(res, root, root, real_home)
            tail = "\n".join(res["out"].splitlines()[-8:])
            check(f"A {rel}: GREEN inside the sealed harness (rc 0)",
                  res["error"] is None and res["rc"] == 0, res["error"] or tail)
            check(f"A {rel}: the audit hook is live (it recorded the gate's own temp-dir work)",
                  len(v["in_tmp"]) > 0, f"{len(res['events'])} events")
            check(f"A {rel}: no write / delete / rename / mkdir in the machine's DeepMap home (event level)",
                  not v["machine_home"], v["machine_home"][:6])
            changed = _diff(res["before"], res["after"])
            check(f"A {rel}: the machine home's listing + mtimes are unchanged (canaries: {', '.join(seeded)})",
                  not changed, changed[:6])
            check(f"A {rel}: nothing written, deleted or renamed in the tree (.deepmap/, .mcp.json or anywhere)",
                  not v["tree"], v["tree"][:6])
            check(f"A {rel}: nothing touched in the caller's own DeepMap home ({real_home})",
                  not v["real_home"], v["real_home"][:6])
            check(f"A {rel}: every filesystem mutation lands in the child's private temp dir",
                  not v["elsewhere"], v["elsewhere"][:6])
            check(f"A {rel}: no socket connected (no Neo4j / MCP / port probe)", not v["connect"], v["connect"][:6])
            check(f"A {rel}: no process signalled", not v["signal"], v["signal"][:6])
            check(f"A {rel}: the only processes started are git probes of a temp dir (never the tree)",
                  not v["spawn"], v["spawn"][:6])
            check(f"A {rel}: the audit hook raised no error", not v["hook_error"], v["hook_error"][:3])


# ---- B: tests/sync_r10_gates.py gate H names its limits and the durable locks ------------------------------------------
def _module_row(doc: str, letter: str) -> str:
    """The `  H  ...` row of a module docstring's section table, continuation lines included."""
    lines, out, on = doc.splitlines(), [], False
    for ln in lines:
        if re.match(rf"^\s{{1,3}}{letter}\s{{2}}\S", ln):
            on = True
            out.append(ln.strip())
            continue
        if on:
            if ln.startswith("     ") and ln.strip():
                out.append(ln.strip())
            else:
                break
    return " ".join(out)


def _says_tautological(text: str) -> bool:
    t = " ".join(text.split())
    return ("HEAD" in t and re.search(r"\bcommitted\b", t) is not None
            and re.search(r"tautolog|whatever the file says", t, re.I) is not None)


def gate_b_sync_r10_h(root: Path) -> None:
    print("\n-- B: sync_r10 gate H says its `git show HEAD:` check is tautological once committed + names the locks --")
    path = root / "tests" / "sync_r10_gates.py"
    try:
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src)
    except (OSError, SyntaxError) as exc:
        check("B tests/sync_r10_gates.py is readable", False, exc)
        return
    row = _module_row(ast.get_docstring(tree) or "", "H")
    fn = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "gate_h_frozen"), None)
    fdoc = (ast.get_docstring(fn) or "") if fn else ""
    check("B the module docstring's H row still describes the HEAD comparison", "HEAD" in row, row)
    check("B ... and says it is tautological once a change is committed (a guard for UNCOMMITTED edits only)",
          _says_tautological(row), row)
    check("B gate_h_frozen() has a docstring that says the same", bool(fn) and _says_tautological(fdoc), fdoc or fn)
    both = row + " " + fdoc
    check("B the durable learned_fusion.py byte lock is named: tests/o12_learned_fusion_lock_r283_gates.py",
          "tests/o12_learned_fusion_lock_r283_gates.py" in row and "tests/o12_learned_fusion_lock_r283_gates.py" in fdoc,
          both)
    check("B the ranking-symbol hash lock is named: tests/fts_r7_gates.py (_FROZEN)",
          "tests/fts_r7_gates.py" in both and "_FROZEN" in both, both)
    named = sorted(set(re.findall(r"tests/[\w.]+\.py", both)))
    missing = [n for n in named if not (root / n).is_file()]
    check(f"B every gate it names exists ({', '.join(named) or 'none'})", bool(named) and not missing, missing)
    body = ast.get_source_segment(src, fn) if fn else ""
    check("B gate H's checks are unchanged (still the HEAD comparison of both frozen files)",
          'f"HEAD:{path}"' in (body or "") and "learned_fusion.py is byte-identical to HEAD" in (body or ""),
          (body or "")[:300])


# ---- C: tests/delphi_pascal_gates.py promises no --strict flag --------------------------------------------------------
# The same negation-aware rule as tests/o6_verdict_contract_r283_gates.py (a mention may only say there is no flag).
_STRICT = re.compile(r"--strict(?![\w-])")
_NEGATED = re.compile(r"\b(?:no|not|never|without|nor)\b[^.;:]{0,30}$", re.I)
_NEGATED_AFTER = re.compile(r"^[^.;]{0,60}?\b(?:unwired|not wired|never wired|is not|isn't|does not exist|"
                            r"doesn't exist|was removed|no longer)\b", re.I)


def _promises_strict(text: str) -> list:
    out = []
    for m in _STRICT.finditer(text):
        before, after = text[max(0, m.start() - 40):m.start()], text[m.end():m.end() + 80]
        if not (_NEGATED.search(before) or _NEGATED_AFTER.search(after)):
            out.append(text[max(0, m.start() - 60):m.end() + 20])
    return out


def _check_labels(tree: ast.AST) -> list:
    """The first argument of every check(...) call: a string literal, or an f-string's literal parts."""
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "check" and n.args:
            a = n.args[0]
            if isinstance(a, ast.Constant) and isinstance(a.value, str):
                out.append(a.value)
            elif isinstance(a, ast.JoinedStr):
                out.append("".join(v.value for v in a.values if isinstance(v, ast.Constant) and isinstance(v.value, str)))
    return out


def gate_c_delphi_label(root: Path) -> None:
    print("\n-- C: delphi_pascal_gates' check labels claim no --strict flag (ISS-build:026); the assertion stays --")
    path = root / "tests" / "delphi_pascal_gates.py"
    try:
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src)
    except (OSError, SyntaxError) as exc:
        check("C tests/delphi_pascal_gates.py is readable", False, exc)
        return
    labels = _check_labels(tree)
    promises = [p for lab in labels for p in _promises_strict(lab)]
    check(f"C no check label promises a --strict flag ({len(labels)} labels)", bool(labels) and not promises, promises)
    check("C (self-test) the rule flags the old label and passes a negation",
          bool(_promises_strict("DEGRADED is honest about exit status under --strict"))
          and not _promises_strict("there is no --strict flag"))
    flat = re.sub(r"\s+", "", src)
    check("C the assertion is unchanged: exit_code(DEGRADED) == 0 and the helper's strict=True mapping == 3",
          "V.exit_code(v,strict=True)==3andV.exit_code(v)==0" in flat)


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
    print(f"F5 (r283) test hygiene — sealed setup gates + truthful gate texts (root: {root})")
    print("=" * 72)
    for gate in (gate_a_selftest, lambda: gate_a_setup_gates(root), lambda: gate_b_sync_r10_h(root),
                 lambda: gate_c_delphi_label(root)):
        try:
            gate()
        except Exception as exc:  # noqa: BLE001 — a crashing section is a failure, not a skipped one
            import traceback
            check("a section ran without crashing", False, f"{type(exc).__name__}: {exc}\n"
                                                          + traceback.format_exc()[-1200:])
    total = len(PASSES) + len(FAILS)
    print("\n" + "=" * 72)
    if FAILS:
        print(f"RESULT: RED — {len(PASSES)}/{total} gates passed.")
        return 1
    print(f"RESULT: GREEN — {len(PASSES)}/{total} gates passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
