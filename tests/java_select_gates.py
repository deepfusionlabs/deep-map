#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Off macOS, the Java preflight checks the java Neo4j will run, and finds an installed JDK 17 / 21 by itself.

Before (a field report): a Windows user had Java 19 first on PATH. /deep-map:setup stopped with "Java 19 found ...,
but Neo4j 5.26 needs 17 or 21. Install Temurin 21, or point JAVA_HOME at a 17/21 JDK, then reopen the shell." Off
macOS the check read ONLY the PATH java: a JAVA_HOME the user pointed at a 21 was ignored (Neo4j's launcher runs
JAVA_HOME's java first, on Windows and POSIX), and a Temurin 21 installed beside the 19 was never looked for, so
neither remedy could pass.

Now (every OS but macOS; the macOS rule is unchanged):
  J  JAVA_HOME set: <JAVA_HOME>/bin/java.exe (Windows) / bin/java is what is checked first, what Neo4j's launcher
     runs, built from the value as set (a pasted trailing space is named, as Neo4j's launcher keeps it); a 17 / 21 there
     passes although PATH has a 19, nothing pinned, never touched; one whose -version only timed out: the WARNING, as
     before, nothing pinned. Its java missing, unsupported or broken: the PASS-OVER (0.6.2 passed such a machine when
     PATH had a 17 / 21; refusing it would leave every SessionStart heal refusing): the PATH java's JDK when its own
     -version says 17 / 21 (its home: <home>/bin/java(.exe) once links are resolved, asked plain -version; else, for a
     launcher or shim, the java.home its JVM names in the same run, -XshowSettings:properties) or, at its own home,
     when that -version only times out and its `release` says 17 / 21 / nothing; never a 32-bit JDK; else the first
     installed 21 / 17 the search confirms (JAVA_HOME's java and the PATH java never run twice; a candidate that only
     times out is pinned, as in F), is pinned; the preflight passes and prints ONE NOTE line, once per process (what
     JAVA_HOME names, which Neo4j "does not support" when it runs and "cannot use" when it is missing or broken, the JDK
     Neo4j starts with instead, JAVA_HOME itself unchanged, how to make it permanent: "remove that space" only when the
     JDK without the space is a 17 / 21); `neo4j console` and neo4j-admin get JAVA_HOME replaced only
     where their environment holds EXACTLY the value passed over (another value is never touched). Nothing found: the
     refusal exactly as before, naming JAVA_HOME (point it at a 17 / 21 or unset it; none installed: the install
     command first), and saying the change must be made where JAVA_HOME is set, not for one command, and Claude Code
     restarted (a running session and its heal keep the old value).
  P  JAVA_HOME unset, the PATH java a 17 / 21 (or one whose -version only timed out): exactly the old result, one
     `java -version`, no search, nothing pinned, Neo4j's environment unchanged...
  G  ...except on Windows when the JavaSoft registry entry Neo4j's Get-Java takes BEFORE PATH (CurrentVersion ->
     JavaHome, JDK then JRE, 64- then 32-bit view; read with winreg) names no java or not a 17 / 21: then the PATH
     java's own JDK home (no process), else an installed 21 / 17, is handed to Neo4j as JAVA_HOME. A pick that is a
     17 / 21, or the PATH java itself, or none: nothing changes.
  F  the PATH java missing, unsupported or broken: an installed JDK 21, then 17, is looked for (Windows: every
     HKLM JavaSoft\\JDK / \\JRE version subkey's JavaHome in both registry views, the vendor folders under
     %ProgramFiles% / %ProgramW6432%, %LOCALAPPDATA%\\Programs\\Eclipse Adoptium; Linux: /usr/lib/jvm, /usr/lib64/jvm,
     SDKMAN; everywhere <DEEPMAP_HOME>/jdk*), cheapest first (the JDK's `release` file, else its folder name, before any
     process), confirmed by its own -version; the first usable one is pinned (_NEO4J_JAVA_HOME) and handed to
     `neo4j console` and neo4j-admin as JAVA_HOME. A candidate that only timed out ends the search (as on macOS); a
     registry JavaHome whose folder is gone is skipped; at most _JAVA_FIND_MAX_PROBES candidates are run.
  M  messages: the old short forms ("no Java on PATH", "Java N unsupported (needs 17/21)", "java -version failed");
     nothing found: the java found and its path, "no JDK 17 or 21 was found in the usual install folders", the install
     command (Windows winget, run unattended; Debian / Ubuntu apt; Fedora / RHEL dnf; an archive unpacked into
     <DEEPMAP_HOME>, where the search looks) and "re-run /deep-map:setup ... no PATH or JAVA_HOME change is needed";
     never the dead-end "point JAVA_HOME at a 17/21 JDK" when JAVA_HOME is not set; no em dash in the new text.
  E  _neo4j_java_env hands Neo4j the preflight's JDK; macOS runs the rule first in a fresh process, every other OS does
     not (no java runs), because every Neo4j start runs the preflight first in the same process (locked on the
     source: _ensure_neo4j, _upgrade_neo4j -> _upgrade_locked; recover never starts Neo4j); a user's JAVA_HOME is
     kept unless it is exactly the value the preflight passed over; the preflight forgets an earlier pass-over before
     it judges; never raises.
  D  deps_bootstrap's Java check is preflight's own function (one rule): a pass-over there is no problem either.
  X  macOS: the macOS gate's own sections (A B C D K T V) pass unchanged, and neither the new search nor the
     pass-over is ever used there.

Hermetic: temp dirs only (a fake Program Files, LOCALAPPDATA, /usr/lib/jvm, DEEPMAP_HOME), a fake registry and a fake
winreg module, fake `java -version` answers; no JVM, no Neo4j, no real registry, no network.
Usage: python tests/java_select_gates.py
"""
from __future__ import annotations

import importlib.util
import io
import os
import shutil
import subprocess
import sys
import tempfile
import types
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "build"))
import provision as P  # noqa: E402

PASSES: list = []
FAILS: list = []
EM_DASH = "\u2014"
WINGET = ("winget install -e --id EclipseAdoptium.Temurin.21.JDK --source winget --accept-source-agreements "
          "--accept-package-agreements")
APT = "sudo apt install openjdk-21-jre-headless"
DNF = "sudo dnf install java-21-openjdk-headless"
NOT_FOUND = "no JDK 17 or 21 was found in the usual install folders"
NO_CHANGE = "no PATH or JAVA_HOME change is needed"
DEAD_END = "point JAVA_HOME at a 17/21 JDK"
ADOPTIUM = "https://adoptium.net/temurin/releases/?version=21"
RESTART = "restart Claude Code"
WIN_ENV_DIALOG = "Settings > System > About > Advanced system settings > Environment Variables"
NOTE_HEAD = "[provision] NOTE: JAVA_HOME names"
LAST: dict = {}   # the last _preflight's state beside its result ("passed": the JAVA_HOME value passed over)


def _note(what: str, major, home) -> str:
    """The pass-over's NOTE line: JAVA_HOME names `what`; Neo4j is started with the JDK `major` (None: a JDK whose
    -version only timed out) at `home` instead. A Java that runs but is not a 17 / 21 is one Neo4j 5.26 "does not
    support" (it may still start on it, with a warning); a missing or broken java is one it "cannot use"."""
    jdk = f"the JDK {major}" if major else "the JDK"
    verdict = "does not support" if what.startswith("Java ") else "cannot use"
    return (f"{NOTE_HEAD} {what}, which Neo4j 5.26 {verdict}; DeepMap starts Neo4j with {jdk} at {home} instead (only "
            "in Neo4j's own environment; your JAVA_HOME is unchanged). To make this permanent, point JAVA_HOME at a "
            "JDK 17 or 21, or unset it.")


def _props_text(home) -> str:
    """What `java -XshowSettings:properties -version` prints before its version lines (stderr): an indented
    `key = value` block; java.home only when `home` is given."""
    return ("Property settings:\n    file.encoding = UTF-8\n    java.class.version = 65.0\n"
            + (f"    java.home = {home}\n" if home else "")
            + "    java.specification.version = 21\n    os.name = Windows 11\n\n")


def _install_win(home) -> str:
    """The Windows install remedy, <DEEPMAP_HOME> filled in (where an unpacked .zip is looked for)."""
    return (f"install Temurin 21 (`{WINGET}`, or the .msi from {ADOPTIUM}; a .zip from there works too, unpacked so "
            f"its jdk-21 folder sits directly in {home})")


def _install_posix(home) -> str:
    return ("install Java 21 (Debian / Ubuntu: `sudo apt install openjdk-21-jre-headless`, on Debian 12 "
            "`openjdk-17-jre-headless`; Fedora / RHEL: `sudo dnf install java-21-openjdk-headless`; or Temurin 21's "
            f".tar.gz from {ADOPTIUM}, unpacked so its jdk-21 folder sits directly in {home})")


def check(name: str, ok: bool, detail="") -> None:
    (PASSES if ok else FAILS).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok or detail == "" else f" -- {str(detail)[:900]}"))


_ABSENT = object()


class _Patch:
    """Set module attributes for a block; a seam an older tree lacks is set and removed again (a failed check, not a
    crash, on the pre-fix tree)."""

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


class _Env:
    """Set / unset os.environ keys for a block (JAVA_HOME above all: CI runners set it)."""

    def __init__(self, **kv):
        self.kv, self.saved = kv, {}

    def __enter__(self):
        for k, v in self.kv.items():
            self.saved[k] = os.environ.get(k)
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return self

    def __exit__(self, *exc):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return False


def _quiet(fn, *a, **k):
    out, err = io.StringIO(), io.StringIO()
    res, exc = None, None
    with redirect_stdout(out), redirect_stderr(err):
        try:
            res = fn(*a, **k)
        except BaseException as e:  # noqa: BLE001 — a gate reports, never dies
            exc = e
    return res, out.getvalue(), err.getvalue(), exc


def _delegate(mod, **over):
    """A namespace that is `mod` except for `over` (patching the module object itself would leak into this process)."""
    ns = types.SimpleNamespace(**{k: getattr(mod, k) for k in dir(mod) if not k.startswith("__")})
    for k, v in over.items():
        setattr(ns, k, v)
    return ns


def _norm(path) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(str(path))))


def _same(a, b) -> bool:
    return bool(a) and bool(b) and _norm(a) == _norm(b)


def _version_text(major: int) -> str:
    v = "1.8.0_402" if major == 8 else f"{major}.0.4"
    return (f'openjdk version "{v}" 2024-07-16 LTS\nOpenJDK Runtime Environment Temurin-{v}+7 (build {v}+7-LTS)\n'
            f"OpenJDK 64-Bit Server VM Temurin-{v}+7 (build {v}+7-LTS, mixed mode)\n")


def _line(major: int) -> str:
    return _version_text(major).splitlines()[0]


class _World:
    """A fake machine that is not a Mac: JDK homes under a temp dir (each <home>/bin/java.exe on Windows, bin/java
    elsewhere, an empty file, with an optional `release` file), the PATH java, the JavaSoft registry's JavaHome values,
    and every `java -version` answered from that table (an unknown java: FileNotFoundError, as for a missing file).
    Records every subprocess.run argv + timeout + env, every Popen env, and how often the search ran."""

    def __init__(self, td: Path, *, win: bool = True):
        self.td = Path(td)
        self.win = win
        self.majors: dict = {}      # _norm(exe) -> major
        self.slow: set = set()      # _norm(exe) whose -version times out
        self.broken: set = set()    # _norm(exe) whose -version exits 1
        self.props: dict = {}       # _norm(exe) -> the java.home its JVM prints for -XshowSettings:properties
        self.reg: list = []         # [(version key name, JavaHome)] as _java_registry_homes returns them
        self.pick = None            # (CurrentVersion, JavaHome) Neo4j's Get-Java takes from the registry (None: none)
        self.which_ret = None
        self.calls: list = []
        self.popen: list = []
        self.searched = 0
        self.picked = 0
        self.pf = self.td / "Program Files"
        self.la = self.td / "AppData-Local"
        self.home = self.td / "dmhome"
        self.jvm = self.td / "usr-lib-jvm"
        self.jvm64 = self.td / "usr-lib64-jvm"
        self.sdk = self.td / "sdkman-candidates-java"
        for d in (self.pf, self.la, self.home, self.jvm, self.jvm64, self.sdk):
            d.mkdir(parents=True, exist_ok=True)

    def exe_name(self) -> str:
        return "java.exe" if self.win else "java"

    def add_jdk(self, home, major, *, release=True, version=None, arch="x86_64", slow=False, broken=False) -> str:
        home = Path(home)
        (home / "bin").mkdir(parents=True, exist_ok=True)
        exe = home / "bin" / self.exe_name()
        exe.write_bytes(b"")
        if release:
            v = version or ("1.8.0_402" if major == 8 else f"{major}.0.4")
            (home / "release").write_text(f'IMPLEMENTOR="Eclipse Adoptium"\nJAVA_VERSION="{v}"\nOS_ARCH="{arch}"\n'
                                          'JAVA_VERSION_DATE="2024-07-16"\n', encoding="utf-8")
        self._answer(exe, major, slow=slow, broken=broken)
        return str(home)

    def pf_jdk(self, vendor: str, name: str, major: int, **kw) -> str:
        return self.add_jdk(self.pf / vendor / name, major, **kw)

    def set_path_java(self, major, *, slow=False, broken=False, exe=None, java_home=None) -> str:
        """The java first on PATH: by default a launcher whose folder is not a JDK home (Oracle's javapath).
        java_home: the home its JVM names for -XshowSettings:properties (None: it names none)."""
        if exe is None:
            d = self.td / "javapath"
            d.mkdir(parents=True, exist_ok=True)
            exe = d / self.exe_name()
            exe.write_bytes(b"")
        self._answer(exe, major, slow=slow, broken=broken)
        if java_home:
            self.props[_norm(exe)] = str(java_home)
        self.which_ret = str(exe)
        return str(exe)

    def _answer(self, exe, major, *, slow=False, broken=False) -> None:
        key = _norm(exe)
        self.majors[key] = major
        (self.slow.add if slow else self.slow.discard)(key)
        (self.broken.add if broken else self.broken.discard)(key)

    def run(self, argv, *a, **k):
        argv = [str(x) for x in (argv if isinstance(argv, (list, tuple)) else [argv])]
        self.calls.append({"argv": argv, "timeout": k.get("timeout"), "env": k.get("env")})
        if "neo4j-admin" in " ".join(argv):
            return types.SimpleNamespace(returncode=0, stdout="Changed password for user 'neo4j'.", stderr="")
        key = _norm(argv[0])
        if key not in self.majors:
            raise FileNotFoundError(2, "No such file or directory", argv[0])
        if key in self.slow:
            raise subprocess.TimeoutExpired(argv, k.get("timeout") or 0)
        if key in self.broken:
            return types.SimpleNamespace(returncode=1, stdout="",
                                         stderr="Error: Could not create the Java Virtual Machine.\n")
        props = _props_text(self.props.get(key)) if "-XshowSettings:properties" in argv else ""
        return types.SimpleNamespace(returncode=0, stdout="", stderr=props + _version_text(self.majors[key]))

    def Popen(self, argv, *a, **k):   # noqa: N802 — subprocess.Popen's name
        self.popen.append({"argv": [str(x) for x in argv], "env": k.get("env")})
        return types.SimpleNamespace(pid=434343, poll=lambda: None)

    def which(self, name, *a, **k):
        return self.which_ret if name == "java" else None

    def java_runs(self) -> list:
        return [c["argv"][0] for c in self.calls if "neo4j-admin" not in " ".join(c["argv"])]

    def ran(self, home) -> bool:
        exe = os.path.join(str(home), "bin", self.exe_name())
        return any(_same(e, exe) for e in self.java_runs())

    def patches(self, **over) -> dict:
        real = getattr(P, "_java_install_candidates", None)

        def counted():
            self.searched += 1
            return real() if callable(real) else []

        def pick():
            self.picked += 1
            return self.pick
        d = dict(subprocess=_delegate(subprocess, run=self.run, Popen=self.Popen),
                 shutil=_delegate(shutil, which=self.which),
                 _is_macos=lambda: False, is_windows=lambda: self.win,
                 _java_registry_homes=lambda: list(self.reg),
                 _java_registry_pick=pick,
                 _java_install_candidates=counted,
                 _POSIX_JVM_DIRS=(str(self.jvm), str(self.jvm64), str(self.sdk)))
        d.update(over)
        return d

    def env(self, **kv) -> dict:
        e = dict(ProgramFiles=str(self.pf), ProgramW6432=str(self.pf), LOCALAPPDATA=str(self.la),
                 DEEPMAP_HOME=str(self.home), JAVA_HOME=None)
        e.update(kv)
        return e


def _preflight(w: _World, *, patches=None, **env):
    with _Env(**w.env(**env)), _Patch(P, **w.patches(**(patches or {}))):
        res, out, err, exc = _quiet(P._java_preflight_problems)
        chosen = getattr(P, "_NEO4J_JAVA_HOME", None)
        LAST["passed"] = getattr(P, "_JAVA_PASSED_OVER", None)
    return res or [], out + err, exc, chosen


def _text(res) -> str:
    return " | ".join(f"{m} [{s}]" for m, s in (res or []))


def _launch_envs(w: _World, *, preflight=True, extra=None, **env):
    """(start_neo4j's Popen env, set_initial_password's run env, exceptions) after a preflight in the same process
    (preflight=False: a fresh process that starts Neo4j with no preflight first)."""
    stubs = dict(_pid_create_time=lambda pid: None, _instances_running=lambda *a, **k: 0,
                 _clear_blocking_neo4j_pidfile=lambda p: None, _is_initialized=lambda *a, **k: False)
    if not preflight:
        stubs.update(_JAVA_CHECKED=False, _NEO4J_JAVA_HOME=None, _JAVA_PASSED_OVER=None)
    stubs.update(extra or {})
    with _Env(**w.env(**env)), _Patch(P, **w.patches(), **stubs):
        r1 = _quiet(P._java_preflight_problems) if preflight else None
        w.popen.clear()
        n0 = len(w.calls)
        r2 = _quiet(P.start_neo4j, "5.26.32", "j" * 16)
        r3 = _quiet(P.set_initial_password, "5.26.32", "deepmap", "j" * 16)
    start_env = w.popen[-1]["env"] if w.popen else None
    admin = [c for c in w.calls[n0:] if "neo4j-admin" in " ".join(c["argv"])]
    admin_env = admin[-1]["env"] if admin else None
    return start_env, admin_env, (r1, r2, r3)


def _jh(env):
    return None if env is None else env.get("JAVA_HOME")


# --------------------------------------------------------------------------------------------------- W (Windows)
def gate_w_windows(td: Path) -> None:
    print("\n-- W: Windows: the field case, JAVA_HOME, the registry, <DEEPMAP_HOME>/jdk* --")
    w = _World(td / "w1")
    w.set_path_java(19)
    t21 = w.pf_jdk("Eclipse Adoptium", "jdk-21.0.5.11-hotspot", 21)
    res, out, exc, chosen = _preflight(w)
    check("W1 the field case: PATH java 19, JAVA_HOME unset, a Temurin 21 under Program Files -> passes",
          res == [] and exc is None, (_text(res), exc))
    check("W1b ...that Temurin 21's home is pinned for Neo4j", _same(chosen, t21), chosen)
    w.calls.clear()
    start_env, admin_env, rr = _launch_envs(w)
    check("W1c ...`neo4j console` and neo4j-admin both get JAVA_HOME = that home (Neo4j runs the JDK the check passed)",
          _same(_jh(start_env), t21) and _same(_jh(admin_env), t21), (_jh(start_env), _jh(admin_env), rr[1][3]))

    w = _World(td / "w2")
    w.set_path_java(19)
    oracle = w.add_jdk(td / "w2" / "oracle" / "jdk-21", 21)
    w.reg = [("19", str(td / "w2" / "oracle" / "jdk-19-not-there")), ("21.0.1", oracle)]
    res, out, exc, chosen = _preflight(w)
    check("W2 PATH 19 + a registry JavaSoft\\JDK\\21.0.1 JavaHome -> passes and pins that JavaHome",
          res == [] and exc is None and _same(chosen, oracle), (_text(res), chosen, exc))

    w = _World(td / "w3")
    path19 = w.set_path_java(19)
    res, out, exc, chosen = _preflight(w)
    t = _text(res)
    msg = res[0][0] if res else ""
    check("W3 PATH 19 + no JDK 17 / 21 anywhere -> refused with the old short form",
          len(res) == 1 and res[0][1] == "Java 19 unsupported (needs 17/21)" and chosen is None, (t, chosen))
    check("W3b ...naming the java it found and its path", "Java 19 found" in msg and path19 in msg
          and _line(19) in msg, msg)
    check("W3c ...saying no JDK 17 or 21 was found in the usual install folders, with the winget command",
          NOT_FOUND in msg and WINGET in msg, msg)
    check("W3d ...then 're-run /deep-map:setup' (DeepMap finds it there itself; no PATH or JAVA_HOME change)",
          "re-run /deep-map:setup" in msg and "finds it there itself" in msg and NO_CHANGE in msg, msg)
    check("W3e ...never the dead-end 'point JAVA_HOME at a 17/21 JDK' / 'reopen the shell'; no em dash",
          DEAD_END not in msg and "reopen the shell" not in msg and EM_DASH not in msg, msg)
    inst = _install_win(w.home)
    check("W3f ...exactly", msg == (f"Java 19 found at {path19} ('{_line(19)}'), but Neo4j 5.26 needs 17 or 21, and "
                                    "no JDK 17 or 21 was found in the usual install folders. "
                                    f"{inst[:1].upper()}{inst[1:]}, then re-run /deep-map:setup (DeepMap finds it "
                                    "there itself; no PATH or JAVA_HOME change is needed)."), msg)
    check("W3g ...an unpacked .zip is told where the search looks (<DEEPMAP_HOME>), and winget runs unattended "
          "(exact id, the winget source, agreements accepted: no prompt in Claude Code's shell)",
          f"sits directly in {w.home}" in msg and "--source winget" in msg and "--accept-source-agreements" in msg
          and "-e --id EclipseAdoptium.Temurin.21.JDK" in msg, msg)

    w = _World(td / "w4")
    w.set_path_java(19)
    j21 = w.add_jdk(td / "w4" / "user-jdk-21", 21)
    w.pf_jdk("Eclipse Adoptium", "jdk-17.0.9.9-hotspot", 17)
    res, out, exc, chosen = _preflight(w, JAVA_HOME=j21)
    check("W4 JAVA_HOME = a JDK 21, PATH java 19 -> passes (was a false refusal); nothing pinned",
          res == [] and exc is None and chosen is None, (_text(res), chosen, exc))
    check("W4b ...JAVA_HOME's bin\\java.exe was checked, the PATH java never ran, no search",
          w.ran(j21) and not any(_same(e, w.which_ret) for e in w.java_runs()) and w.searched == 0,
          (w.java_runs(), w.searched))
    start_env, admin_env, _rr = _launch_envs(w, JAVA_HOME=j21)
    check("W4c ...Neo4j keeps the user's JAVA_HOME", _same(_jh(start_env), j21) and _same(_jh(admin_env), j21),
          (_jh(start_env), _jh(admin_env)))

    # ---- the pass-over: JAVA_HOME's java missing, unsupported or broken, a usable 17 / 21 found -> pinned, a NOTE
    w = _World(td / "w5")
    w.set_path_java(21)                     # a launcher outside any JDK home (Oracle's javapath)
    j19 = w.add_jdk(td / "w5" / "user-jdk-19", 19)
    t21 = w.pf_jdk("Eclipse Adoptium", "jdk-21.0.5.11-hotspot", 21)
    res, out, exc, chosen = _preflight(w, JAVA_HOME=j19)
    j19_exe = os.path.join(j19, "bin", "java.exe")
    check("W5 JAVA_HOME = a JDK 19, an installed Temurin 21: passes (0.6.2 passed this machine), and that 21 is "
          "pinned for Neo4j", res == [] and exc is None and _same(chosen, t21), (_text(res), chosen, exc))
    check("W5b ...the JAVA_HOME value passed over is remembered exactly as set", LAST.get("passed") == j19,
          LAST.get("passed"))
    note = _note(f"Java 19 at {j19_exe}", 21, chosen)
    check("W5c ...ONE NOTE line, exactly: what JAVA_HOME names, the JDK Neo4j starts with, JAVA_HOME itself unchanged, "
          "how to make it permanent; no em dash", out.count(NOTE_HEAD) == 1 and note in out and EM_DASH not in out
          and "PREFLIGHT FAILED" not in out, (note, out[-700:]))
    check("W5d ...JAVA_HOME's java ran once, the PATH java once (not a JDK home), the 21 once; no registry pick asked",
          [_norm(e) for e in w.java_runs()] == [_norm(j19_exe), _norm(w.which_ret),
                                                _norm(os.path.join(t21, "bin", "java.exe"))] and w.picked == 0,
          (w.java_runs(), w.picked))
    start_env, admin_env, _rr = _launch_envs(w, JAVA_HOME=j19)
    check("W5e ...`neo4j console` and neo4j-admin get JAVA_HOME = that 21 (the passed-over value replaced)",
          _same(_jh(start_env), t21) and _same(_jh(admin_env), t21), (_jh(start_env), _jh(admin_env)))
    with _Env(**w.env(JAVA_HOME=j19)), _Patch(P, **w.patches()):
        _quiet(P._java_preflight_problems)
        other = P._neo4j_java_env({"JAVA_HOME": str(td / "some-other-jdk")})
        spaced = P._neo4j_java_env({"JAVA_HOME": j19 + " "})
        same = P._neo4j_java_env({"JAVA_HOME": j19, "PATH": "x"})
        unset = P._neo4j_java_env({"PATH": "x"})
        own = os.environ.get("JAVA_HOME")
    check("W5f ...an environment whose JAVA_HOME differs from the passed-over value (even by a space) is never touched",
          other.get("JAVA_HOME") == str(td / "some-other-jdk") and spaced.get("JAVA_HOME") == j19 + " ",
          (other, spaced))
    check("W5g ...exactly that value is replaced; an environment without JAVA_HOME gets the chosen JDK too; this "
          "process's own JAVA_HOME is unchanged", _same(same.get("JAVA_HOME"), t21) and same.get("PATH") == "x"
          and _same(unset.get("JAVA_HOME"), t21) and own == j19, (same, unset, own))
    res2, out2, exc2, chosen2 = _preflight(w, JAVA_HOME=j19)
    check("W5x the same process judges again (setup's start, then its upgrade check): the same pass-over, but the "
          "identical NOTE is printed no second time", res2 == [] and exc2 is None and _same(chosen2, t21)
          and LAST.get("passed") == j19 and NOTE_HEAD not in out2, (_text(res2), chosen2, out2[-500:]))

    w = _World(td / "w5h")
    t21 = w.pf_jdk("Eclipse Adoptium", "jdk-21.0.5.11-hotspot", 21)
    w.set_path_java(21, exe=os.path.join(t21, "bin", "java.exe"))   # the Temurin .msi put its bin first on PATH
    j19 = w.add_jdk(td / "w5h" / "user-jdk-19", 19)
    res, out, exc, chosen = _preflight(w, JAVA_HOME=j19)
    check("W5h JAVA_HOME = a 19, the PATH java a 21 at <home>\\bin\\java.exe: that home is pinned, no search",
          res == [] and exc is None and _same(chosen, t21) and w.searched == 0 and LAST.get("passed") == j19,
          (_text(res), chosen, w.searched))
    check("W5i ...the NOTE names the PATH JDK", _note(f"Java 19 at {os.path.join(j19, 'bin', 'java.exe')}", 21,
                                                      chosen) in out, out[-600:])
    asked = [c["argv"][1:] for c in w.calls if _same(c["argv"][0], w.which_ret)]
    check("W5i2 ...a PATH java at its own JDK home is asked plain -version (no -XshowSettings), once",
          asked == [["-version"]], asked)

    w = _World(td / "w5j")
    o19 = w.pf_jdk("Java", "jdk-19", 19)
    w.set_path_java(19, exe=os.path.join(o19, "bin", "java.exe"))   # a JDK 19's own bin first on PATH
    j25 = w.add_jdk(td / "w5j" / "user-jdk-25", 25)
    t21 = w.pf_jdk("Eclipse Adoptium", "jdk-21.0.5.11-hotspot", 21)
    res, out, exc, chosen = _preflight(w, JAVA_HOME=j25)
    check("W5j JAVA_HOME = a 25, the PATH java a JDK 19 (its own home), a Temurin 21 installed: the Temurin 21 is "
          "pinned, never the PATH 19", res == [] and _same(chosen, t21) and LAST.get("passed") == j25,
          (_text(res), chosen))

    w = _World(td / "w5w")
    b21 = w.pf_jdk("Eclipse Adoptium", "jdk-21.0.5.11-hotspot", 21, broken=True)
    w.set_path_java(21, broken=True, exe=os.path.join(b21, "bin", "java.exe"))
    j19 = w.add_jdk(td / "w5w" / "user-jdk-19", 19)
    o17 = w.pf_jdk("Java", "jdk-17", 17)
    res, out, exc, chosen = _preflight(w, JAVA_HOME=j19)
    nb = sum(1 for e in w.java_runs() if _same(e, os.path.join(b21, "bin", "java.exe")))
    check("W5w the PATH java is a broken 21 that the search would list too: it runs once, never twice; the 17 is "
          "pinned", res == [] and _same(chosen, o17) and nb == 1, (_text(res), chosen, w.java_runs()))

    w = _World(td / "w5k")
    j19 = w.add_jdk(td / "w5k" / "user-jdk-19", 19)
    w.set_path_java(19, exe=os.path.join(j19, "bin", "java.exe"))   # PATH holds %JAVA_HOME%\bin
    t21 = w.pf_jdk("Eclipse Adoptium", "jdk-21.0.5.11-hotspot", 21)
    res, out, exc, chosen = _preflight(w, JAVA_HOME=j19)
    n19 = sum(1 for e in w.java_runs() if _same(e, os.path.join(j19, "bin", "java.exe")))
    check("W5k the PATH java IS JAVA_HOME's java: it runs once, never twice; the installed 21 is pinned",
          res == [] and _same(chosen, t21) and n19 == 1, (_text(res), chosen, w.java_runs()))

    w = _World(td / "w5l")
    w.set_path_java(21)
    j19 = w.add_jdk(td / "w5l" / "user-jdk-19", 19)
    a17 = w.pf_jdk("Eclipse Adoptium", "jdk-17.0.9.9-hotspot", 17)   # listed before the 21 in search order
    z21 = w.pf_jdk("Zulu", "zulu-21", 21)
    res, out, exc, chosen = _preflight(w, JAVA_HOME=j19)
    check("W5l a 17 and a 21 installed: the 21 is pinned, the 17 never ran", res == [] and _same(chosen, z21)
          and not w.ran(a17), (_text(res), chosen, w.java_runs()))

    w = _World(td / "w5m")
    w.set_path_java(21)
    b21 = w.pf_jdk("Eclipse Adoptium", "jdk-21.0.5.11-hotspot", 21, broken=True)
    o17 = w.pf_jdk("Java", "jdk-17", 17)
    res, out, exc, chosen = _preflight(w, JAVA_HOME=b21)
    b21_exe = os.path.join(b21, "bin", "java.exe")
    n21 = sum(1 for e in w.java_runs() if _same(e, b21_exe))
    check("W5m JAVA_HOME = a broken 21 (its -version FAILS), an installed 17: the 17 is pinned; the broken one ran "
          "once (the search never runs it again)", res == [] and _same(chosen, o17) and n21 == 1
          and LAST.get("passed") == b21, (_text(res), chosen, w.java_runs()))
    check("W5n ...the NOTE says JAVA_HOME names a java that does not run, and names the 17",
          _note(f"a java that does not run ({b21_exe})", 17, chosen) in out, out[-600:])

    w = _World(td / "w5o")
    w.set_path_java(21)
    j21 = w.add_jdk(td / "w5o" / "user-jdk-21", 21, slow=True)
    t21 = w.pf_jdk("Eclipse Adoptium", "jdk-21.0.5.11-hotspot", 21)
    res, out, exc, chosen = _preflight(w, JAVA_HOME=j21)
    check("W5o JAVA_HOME's java only TIMES OUT: [] + the WARNING (Java IS installed), as before; nothing pinned, "
          "nothing passed over, no NOTE, no other java ran, no search",
          res == [] and "Java IS installed" in out and chosen is None and LAST.get("passed") is None
          and NOTE_HEAD not in out and not any(_same(e, w.which_ret) for e in w.java_runs()) and w.searched == 0,
          (_text(res), chosen, LAST.get("passed"), w.java_runs(), w.searched))
    start_env, admin_env, _rr = _launch_envs(w, JAVA_HOME=j21)
    check("W5p ...Neo4j keeps the user's JAVA_HOME", _same(_jh(start_env), j21) and _same(_jh(admin_env), j21),
          (_jh(start_env), _jh(admin_env)))

    w = _World(td / "w5q")
    j19 = w.add_jdk(td / "w5q" / "user-jdk-19", 19)
    s21 = w.pf_jdk("Eclipse Adoptium", "jdk-21.0.5.11-hotspot", 21, slow=True)
    o17 = w.pf_jdk("Java", "jdk-17", 17)
    res, out, exc, chosen = _preflight(w, JAVA_HOME=j19)
    check("W5q the installed 21 found for a JAVA_HOME 19 only TIMES OUT: pinned as _find_installed_jdk's rule says "
          "(the search ends there, the 17 never runs), the WARNING says Java IS installed, and the NOTE names its home",
          res == [] and _same(chosen, s21) and not w.ran(o17) and "Java IS installed" in out
          and _note(f"Java 19 at {os.path.join(j19, 'bin', 'java.exe')}", None, chosen) in out,
          (_text(res), chosen, out[-700:]))

    # ---- nothing usable found: the refusal exactly as before (naming JAVA_HOME), nothing pinned, no NOTE
    w = _World(td / "w5r")
    path21 = w.set_path_java(21)
    j19 = w.add_jdk(td / "w5r" / "user-jdk-19", 19)
    res, out, exc, chosen = _preflight(w, JAVA_HOME=j19)
    msg = res[0][0] if res else ""
    j19_exe = os.path.join(j19, "bin", "java.exe")
    check("W5r JAVA_HOME = a 19, the PATH java a 21 launcher outside any JDK home whose JVM names no java.home, no "
          "17 / 21 installed: refused, short form kept, nothing pinned, nothing passed over, no NOTE",
          len(res) == 1 and res[0][1] == "Java 19 unsupported (needs 17/21)" and chosen is None
          and LAST.get("passed") is None and NOTE_HEAD not in out, (_text(res), chosen, LAST.get("passed")))
    remedy = ("Point JAVA_HOME at a JDK 17 or 21, or unset it (DeepMap then finds an installed 17 / 21 itself); "
              f"{NOT_FOUND}, so first {_install_win(w.home)}. Change it where it is set ({WIN_ENV_DIALOG}), not for "
              "one command only: a running Claude Code, and the SessionStart heal it starts, keep the JAVA_HOME they "
              "started with, so restart Claude Code (and VS Code, when it runs there), then re-run /deep-map:setup.")
    check("W5s ...exactly the message of before: names JAVA_HOME and its java, none found, install first, where to "
          "change it, the restart", msg == (f"Java 19 found at {j19_exe} ('{_line(19)}'), the java JAVA_HOME names, "
                                            f"which Neo4j runs; Neo4j 5.26 needs 17 or 21. {remedy}"), msg)
    check("W5t ...JAVA_HOME's java and the PATH java each ran once", [_norm(e) for e in w.java_runs()]
          == [_norm(j19_exe), _norm(path21)], w.java_runs())
    start_env, admin_env, _rr = _launch_envs(w, JAVA_HOME=j19)
    check("W5u ...Neo4j keeps the user's JAVA_HOME (nothing to hand over)", _same(_jh(start_env), j19)
          and _same(_jh(admin_env), j19), (_jh(start_env), _jh(admin_env)))

    w = _World(td / "w5v")
    w.set_path_java(21)
    b21 = w.pf_jdk("Eclipse Adoptium", "jdk-21.0.5.11-hotspot", 21, broken=True)
    res, out, exc, chosen = _preflight(w, JAVA_HOME=b21)
    msg = res[0][0] if res else ""
    check("W5v JAVA_HOME = a broken 21 and nothing else usable: refused 'java -version failed' naming JAVA_HOME, "
          "the hint never names that same JDK, the restart named", len(res) == 1
          and res[0][1] == "java -version failed" and "That is the java JAVA_HOME names" in msg
          and f"installed at {b21}" not in msg and RESTART in msg and chosen is None, (msg, chosen))

    # ---- JAVA_HOME with no java there
    w = _World(td / "w6")
    w.set_path_java(21)
    bad = td / "w6" / "jdk-unpacked-wrong"
    (bad / "bin").mkdir(parents=True)
    (bad / "bin" / "java").write_bytes(b"")   # a POSIX-only name: not what Neo4j's launcher runs on Windows
    res, out, exc, chosen = _preflight(w, JAVA_HOME=str(bad))
    t = _text(res)
    check("W6 JAVA_HOME without bin\\java.exe and nothing usable found -> refused naming JAVA_HOME and that folder "
          "('javahome')",
          len(res) == 1 and res[0][1] == "JAVA_HOME has no bin/java" and "JAVA_HOME" in res[0][0]
          and str(bad) in res[0][0] and not res[0][0].startswith("Java was found") and EM_DASH not in res[0][0]
          and chosen is None, t)
    check("W6b ...JAVA_HOME's java was never run (it is not there); only the PATH java's -version ran",
          [_norm(e) for e in w.java_runs()] == [_norm(w.which_ret)], w.java_runs())
    check("W6c ...the remedy says to restart Claude Code after changing JAVA_HOME", RESTART in t, t)

    w = _World(td / "w6g")
    w.set_path_java(21)
    gone = td / "w6g" / "jdk-uninstalled"
    t21 = w.pf_jdk("Eclipse Adoptium", "jdk-21.0.5.11-hotspot", 21)
    res, out, exc, chosen = _preflight(w, JAVA_HOME=str(gone))
    gone_exe = os.path.join(str(gone), "bin", "java.exe")
    check("W6g JAVA_HOME names a JDK that is gone, a Temurin 21 installed: pinned, and the NOTE says JAVA_HOME names "
          "no java", res == [] and _same(chosen, t21) and LAST.get("passed") == str(gone)
          and _note(f"no java ({gone_exe} does not exist)", 21, chosen) in out, (_text(res), chosen, out[-600:]))

    w = _World(td / "w6d")
    w.set_path_java(21)
    j21 = w.add_jdk(td / "w6d" / "jdk-21", 21)
    res, out, exc, chosen = _preflight(w, JAVA_HOME=j21 + " ")
    t = _text(res)
    check("W6d JAVA_HOME with a trailing space (Neo4j's launcher keeps it, so its java is not found), the JDK neither "
          "on PATH nor where the search looks: refused 'javahome', the space named, JAVA_HOME's java never ran",
          len(res) == 1 and res[0][1] == "JAVA_HOME has no bin/java" and "space" in t and chosen is None
          and not w.ran(j21), (t, w.java_runs()))
    check("W6e ...its remedy is to remove the space (the JDK is there: no 'install', no 'none found'), then restart",
          "Remove that space from JAVA_HOME." in t and NOT_FOUND not in t and "winget" not in t and RESTART in t, t)
    w = _World(td / "w6f")
    w.set_path_java(21)
    j21 = w.add_jdk(td / "w6f" / "jdk-21", 21)
    res, out, exc, chosen = _preflight(w, JAVA_HOME=" " + j21 + "\t")
    check("W6f a leading space and a trailing tab around a JDK 21: 'javahome' too, the space named, it never ran",
          len(res) == 1 and res[0][1] == "JAVA_HOME has no bin/java" and "space" in res[0][0]
          and not w.ran(j21), (_text(res), w.java_runs()))

    w = _World(td / "w6h")
    w.set_path_java(21)
    t21 = w.pf_jdk("Eclipse Adoptium", "jdk-21.0.5.11-hotspot", 21)
    raw = t21 + " "
    res, out, exc, chosen = _preflight(w, JAVA_HOME=raw)
    raw_exe = os.path.join(raw, "bin", "java.exe")
    check("W6h JAVA_HOME = a Temurin 21 with a trailing space, that JDK installed where the search looks: it is "
          "pinned (the value with the space is what was passed over)", res == [] and _same(chosen, t21)
          and LAST.get("passed") == raw, (_text(res), chosen, LAST.get("passed")))
    check("W6i ...the NOTE names the space and says to remove it",
          f'{NOTE_HEAD} no java ("{raw_exe}" does not exist: the value has a space at its start or end)' in out
          and "To make this permanent, remove that space from JAVA_HOME." in out, out[-700:])
    start_env, admin_env, _rr = _launch_envs(w, JAVA_HOME=raw)
    check("W6j ...the environment value with the space is replaced for Neo4j (start and neo4j-admin)",
          _same(_jh(start_env), t21) and _jh(start_env) != raw and _same(_jh(admin_env), t21),
          (_jh(start_env), _jh(admin_env)))

    w = _World(td / "w6k")
    j21 = w.add_jdk(td / "w6k" / "jdk-21", 21)        # not where the search looks...
    w.set_path_java(21, exe=os.path.join(j21, "bin", "java.exe"))   # ...but PATH holds its bin
    res, out, exc, chosen = _preflight(w, JAVA_HOME=j21 + " ")
    check("W6k JAVA_HOME = a 21 with a trailing space, its bin on PATH: the PATH java's home is pinned, no search",
          res == [] and _same(chosen, j21) and w.searched == 0, (_text(res), chosen, w.searched))

    # ---- fix round: a launcher / shim on PATH, a 32-bit PATH JDK, a PATH JDK that only times out, a space before a 19
    w = _World(td / "w14")
    j22 = w.add_jdk(td / "w14" / "user-jdk-22", 22)
    tools21 = w.add_jdk(td / "w14" / "tools" / "temurin-21", 21)          # not where the search looks
    shim = w.set_path_java(21, java_home=tools21)                         # a launcher; its JVM names its own home
    res, out, exc, chosen = _preflight(w, JAVA_HOME=j22)
    j22_exe = os.path.join(j22, "bin", "java.exe")
    check("W14 JAVA_HOME = a 22, the PATH java a 21 launcher outside any JDK home whose JVM names its home "
          "(java.home), no 17 / 21 where the search looks: that home is pinned, no search (was refused, 'install "
          "Temurin first')", res == [] and exc is None and _same(chosen, tools21) and w.searched == 0
          and LAST.get("passed") == j22, (_text(res), chosen, w.searched, exc))
    asked = [c["argv"][1:] for c in w.calls if _same(c["argv"][0], shim)]
    check("W14b ...the launcher ran ONCE, as `-XshowSettings:properties -version` (its home read in the same process)",
          asked == [["-XshowSettings:properties", "-version"]], asked)
    check("W14c ...the NOTE names the 22 (a Java Neo4j 5.26 does not support) and that 21's home",
          _note(f"Java 22 at {j22_exe}", 21, chosen) in out, out[-700:])
    start_env, admin_env, _rr = _launch_envs(w, JAVA_HOME=j22)
    check("W14d ...`neo4j console` and neo4j-admin get JAVA_HOME = that 21", _same(_jh(start_env), tools21)
          and _same(_jh(admin_env), tools21), (_jh(start_env), _jh(admin_env)))
    w = _World(td / "w14e")
    j22 = w.add_jdk(td / "w14e" / "user-jdk-22", 22)
    posix = td / "w14e" / "tools" / "jdk-21-posix"
    (posix / "bin").mkdir(parents=True)
    (posix / "bin" / "java").write_bytes(b"")                            # no bin\java.exe: not what Neo4j would run
    w.set_path_java(21, java_home=posix)
    res, out, exc, chosen = _preflight(w, JAVA_HOME=j22)
    check("W14e ...but a java.home that holds no bin\\java.exe on Windows is never handed over (nothing else: refused)",
          len(res) == 1 and res[0][1] == "Java 22 unsupported (needs 17/21)" and chosen is None, (_text(res), chosen))

    w = _World(td / "w15")
    j19 = w.add_jdk(td / "w15" / "user-jdk-19", 19)
    x17 = w.add_jdk(td / "w15" / "jdk-17-x86", 17, arch="x86")
    w.set_path_java(17, exe=os.path.join(x17, "bin", "java.exe"))       # a 32-bit JDK 17's own bin first on PATH
    t21 = w.pf_jdk("Eclipse Adoptium", "jdk-21.0.5.11-hotspot", 21)
    res, out, exc, chosen = _preflight(w, JAVA_HOME=j19)
    check("W15 JAVA_HOME = a 19, the PATH java a 32-bit JDK 17 at its own home (`release` OS_ARCH x86: Neo4j's heap "
          "does not fit, the search leaves it out too), a 64-bit Temurin 21 installed: the 21 is pinned, never the "
          "32-bit 17", res == [] and exc is None and _same(chosen, t21), (_text(res), chosen, exc))
    w = _World(td / "w15b")
    j19 = w.add_jdk(td / "w15b" / "user-jdk-19", 19)
    x21 = w.add_jdk(td / "w15b" / "tools" / "jdk-21-x86", 21, arch="i686")
    w.set_path_java(21, java_home=x21)                                    # a launcher whose JVM is a 32-bit 21
    res, out, exc, chosen = _preflight(w, JAVA_HOME=j19)
    check("W15b ...a launcher whose JVM names a 32-bit JDK home is not handed over either (nothing else: refused)",
          len(res) == 1 and res[0][1] == "Java 19 unsupported (needs 17/21)" and chosen is None
          and LAST.get("passed") is None, (_text(res), chosen))

    w = _World(td / "w16")
    j19 = w.add_jdk(td / "w16" / "user-jdk-19", 19)
    s21 = w.pf_jdk("Eclipse Adoptium", "jdk-21.0.5.11-hotspot", 21)
    s21_exe = os.path.join(s21, "bin", "java.exe")
    w.set_path_java(21, slow=True, exe=s21_exe)                           # that Temurin first on PATH, and slow
    res, out, exc, chosen = _preflight(w, JAVA_HOME=j19)
    check("W16 JAVA_HOME = a 19, the PATH java a Temurin 21 at its own home whose -version only TIMES OUT: it is "
          "pinned, as the search would pin it (was refused, naming that same 21); the WARNING says Java IS installed",
          res == [] and exc is None and _same(chosen, s21) and "Java IS installed" in out
          and LAST.get("passed") == j19, (_text(res), chosen, out[-600:]))
    runs = [c["timeout"] for c in w.calls if _same(c["argv"][0], s21_exe)]
    check("W16b ...it ran once (15 s, then the 60 s retry), never again by the search; the NOTE names its home",
          runs == [15, 60] and _note(f"Java 19 at {os.path.join(j19, 'bin', 'java.exe')}", None, chosen) in out,
          (runs, out[-600:]))
    w = _World(td / "w16c")
    j25 = w.add_jdk(td / "w16c" / "user-jdk-25", 25)
    s19 = w.add_jdk(td / "w16c" / "jdk-19", 19)
    w.set_path_java(19, slow=True, exe=os.path.join(s19, "bin", "java.exe"))
    o17 = w.pf_jdk("Java", "jdk-17", 17)
    res, out, exc, chosen = _preflight(w, JAVA_HOME=j25)
    check("W16c ...but a slow PATH JDK whose `release` says 19 is never taken: the installed 17 is pinned, no WARNING",
          res == [] and _same(chosen, o17) and "WARNING" not in out, (_text(res), chosen, out[-400:]))

    w = _World(td / "w17")
    w.set_path_java(21)
    s19 = w.add_jdk(td / "w17" / "jdk-19", 19)
    t21 = w.pf_jdk("Eclipse Adoptium", "jdk-21.0.5.11-hotspot", 21)
    res, out, exc, chosen = _preflight(w, JAVA_HOME=s19 + " ")
    check("W17 JAVA_HOME = a JDK 19 with a trailing space, a Temurin 21 installed: the 21 is pinned; the NOTE names "
          "the space but never says removing it is enough (the JDK there is a 19): point JAVA_HOME at a 17 / 21",
          res == [] and _same(chosen, t21) and "the value has a space at its start or end" in out
          and "remove that space" not in out
          and "To make this permanent, point JAVA_HOME at a JDK 17 or 21, or unset it." in out,
          (_text(res), chosen, out[-700:]))
    w = _World(td / "w17b")
    w.set_path_java(21)
    s19 = w.add_jdk(td / "w17b" / "jdk-19", 19)
    res, out, exc, chosen = _preflight(w, JAVA_HOME=s19 + " ")
    t = _text(res)
    check("W17b ...and with nothing usable found: refused 'JAVA_HOME has no bin/java', the space named, the remedy "
          "is to point JAVA_HOME at a 17 / 21 (never 'Remove that space': that leaves a 19), then restart",
          len(res) == 1 and res[0][1] == "JAVA_HOME has no bin/java" and "space" in t
          and "Remove that space" not in t and "Point JAVA_HOME at a JDK 17 or 21" in t and RESTART in t
          and chosen is None, t)

    w = _World(td / "w7")
    w.set_path_java(21)
    w.pf_jdk("Eclipse Adoptium", "jdk-21.0.5.11-hotspot", 21)
    res, out, exc, chosen = _preflight(w)
    check("W7 PATH java 21 -> passes with ONE `java -version`, no search, nothing pinned",
          res == [] and len(w.java_runs()) == 1 and w.searched == 0 and chosen is None,
          (_text(res), w.java_runs(), w.searched, chosen))
    check("W7b ...the PATH java was asked plain -version", [c["argv"][1:] for c in w.calls] == [["-version"]],
          [c["argv"] for c in w.calls])
    start_env, admin_env, _rr = _launch_envs(w)
    check("W7c ...Neo4j's environment unchanged (no JAVA_HOME added)", start_env is not None
          and "JAVA_HOME" not in start_env and admin_env is not None and "JAVA_HOME" not in admin_env,
          (_jh(start_env), _jh(admin_env)))

    w = _World(td / "w8")
    unpacked = w.add_jdk(w.home / "jdk-21", 21, release=False)   # a hand-unpacked JDK (folder name only)
    res, out, exc, chosen = _preflight(w)
    check("W8 no java on PATH + <DEEPMAP_HOME>/jdk-21 -> passes and pins it", res == [] and _same(chosen, unpacked),
          (_text(res), chosen))

    w = _World(td / "w8b")
    res, out, exc, chosen = _preflight(w)
    msg = res[0][0] if res else ""
    check("W8b no java on PATH and none installed -> the old short form 'no Java on PATH'",
          len(res) == 1 and res[0][1] == "no Java on PATH" and chosen is None, _text(res))
    check("W8c ...Temurin 21 with the winget command, no JDK 17 / 21 found, re-run setup (it is found itself)",
          "Temurin 21" in msg and WINGET in msg and NOT_FOUND in msg and NO_CHANGE in msg
          and "re-run /deep-map:setup" in msg and "reopen the shell" not in msg and EM_DASH not in msg, msg)
    check("W8d ...exactly", msg == ("no Java on PATH, and no JDK 17 or 21 was found in the usual install folders. "
                                    f"Neo4j 5.26 needs a JDK/JRE 17 or 21: {_install_win(w.home)}, then re-run "
                                    "/deep-map:setup (DeepMap finds it there itself; no PATH or JAVA_HOME change is "
                                    "needed). The Neo4j Community download does not bundle a JRE."), msg)
    w = _World(td / "w8e")
    zipped = w.add_jdk(w.home / "jdk-21.0.5+11", 21)   # the Temurin .zip unpacked as the message says
    res, out, exc, chosen = _preflight(w)
    check("W8e ...and a .zip unpacked where it says (its jdk-21.0.5+11 folder in <DEEPMAP_HOME>) is found",
          res == [] and _same(chosen, zipped), (_text(res), chosen))

    w = _World(td / "w9")
    w.set_path_java(19)
    s21 = w.pf_jdk("Eclipse Adoptium", "jdk-21.0.5.11-hotspot", 21, slow=True)
    o17 = w.pf_jdk("Java", "jdk-17", 17)
    res, out, exc, chosen = _preflight(w)
    check("W9 an installed 21 whose -version only times out: [] + a WARNING (as everywhere), and it is pinned",
          res == [] and exc is None and "WARNING" in out and _same(chosen, s21), (_text(res), chosen, out[-400:]))
    check("W9b ...the WARNING says Java IS installed (not 'on PATH'); the search stopped there (the 17 never ran)",
          "Java IS installed" in out and "Java IS on PATH" not in out and not w.ran(o17), (out[-400:], w.java_runs()))
    runs = [c for c in w.calls if _same(c["argv"][0], os.path.join(s21, "bin", "java.exe"))]
    check("W9c ...retried once with a longer budget (15 s, then 60 s), never more",
          [c["timeout"] for c in runs] == [15, 60], [c["timeout"] for c in runs])
    check("W9d ...no em dash in the WARNING", EM_DASH not in out, out[-400:])

    w = _World(td / "w10")
    w.set_path_java(19)
    g17 = w.add_jdk(td / "w10" / "corretto" / "jdk17.0.13_0", 17)
    w.reg = [("21.0.1", str(td / "w10" / "uninstalled" / "jdk-21")), ("17.0.13", g17), ("21", "")]
    res, out, exc, chosen = _preflight(w)
    check("W10 a registry JavaHome whose folder is gone (and an empty one) is skipped, never a crash: the 17 is used",
          res == [] and exc is None and _same(chosen, g17), (_text(res), chosen, exc))

    w = _World(td / "w11")
    w.set_path_java(19)
    la21 = w.add_jdk(w.la / "Programs" / "Eclipse Adoptium" / "jdk-21.0.5.11-hotspot", 21)
    res, out, exc, chosen = _preflight(w)
    check("W11 a per-user Temurin 21 under %LOCALAPPDATA%\\Programs\\Eclipse Adoptium is found",
          res == [] and _same(chosen, la21), (_text(res), chosen))

    w = _World(td / "w12")
    w.set_path_java(19)
    for vendor, name in (("Microsoft", "jdk-21.0.5.11-hotspot"), ("Zulu", "zulu-21"),
                         ("Amazon Corretto", "jdk21.0.5_11"), ("BellSoft", "LibericaJDK-21"),
                         ("Semeru", "jdk-21.0.5.11-openj9"), ("OpenJDK", "jdk-21.0.2")):
        sub = _World(td / "w12" / vendor.replace(" ", "_"))
        sub.set_path_java(19)
        home = sub.pf_jdk(vendor, name, 21)
        res, out, exc, chosen = _preflight(sub)
        check(f"W12 a JDK 21 under %ProgramFiles%\\{vendor} is found", res == [] and _same(chosen, home),
              (_text(res), chosen))


# --------------------------------------------------------------------------------------------------- G (Get-Java)
def gate_g_registry_pick(td: Path) -> None:
    print("\n-- G: Windows, JAVA_HOME unset, PATH java a 17 / 21: Neo4j's Get-Java takes the JavaSoft registry's pick "
          "first; a pick that is missing or not 17 / 21 makes DeepMap hand Neo4j the checked JDK --")
    w = _World(td / "g1")
    t21 = w.pf_jdk("Eclipse Adoptium", "jdk-21.0.5.11-hotspot", 21)
    w.set_path_java(21, exe=os.path.join(t21, "bin", "java.exe"))   # the Temurin .msi put its bin first on PATH
    o19 = w.pf_jdk("Java", "jdk-19", 19)
    w.pick = ("19", o19)                                             # an Oracle JDK 19 left registered
    res, out, exc, chosen = _preflight(w)
    check("G1 the field machine after a restart (PATH Temurin 21, registry CurrentVersion 19): passes, and the PATH "
          "java's own JDK home is pinned (Neo4j would otherwise run the registry's 19)",
          res == [] and exc is None and _same(chosen, t21), (_text(res), chosen, exc))
    check("G1b ...one `java -version` (the PATH one), no search, the registry pick read once (no process)",
          len(w.java_runs()) == 1 and w.searched == 0 and w.picked == 1, (w.java_runs(), w.searched, w.picked))
    start_env, admin_env, _rr = _launch_envs(w)
    check("G1c ...`neo4j console` and neo4j-admin get JAVA_HOME = that Temurin 21",
          _same(_jh(start_env), t21) and _same(_jh(admin_env), t21), (_jh(start_env), _jh(admin_env)))

    w = _World(td / "g2")
    t21 = w.pf_jdk("Eclipse Adoptium", "jdk-21.0.5.11-hotspot", 21)
    w.set_path_java(21, exe=os.path.join(t21, "bin", "java.exe"))
    w.pick = ("21.0.1", str(td / "g2" / "uninstalled" / "jdk-21"))   # a JavaHome whose JDK was uninstalled
    res, out, exc, chosen = _preflight(w)
    check("G2 a registry pick whose folder is gone (Neo4j would stop with 'Could not find java'): the PATH java's "
          "home is pinned", res == [] and _same(chosen, t21), (_text(res), chosen))

    w = _World(td / "g3")
    t21 = w.pf_jdk("Eclipse Adoptium", "jdk-21.0.5.11-hotspot", 21)
    w.set_path_java(21, exe=os.path.join(t21, "bin", "java.exe"))
    o17 = w.pf_jdk("Java", "jdk-17", 17)
    w.pick = ("17", o17)
    res, out, exc, chosen = _preflight(w)
    check("G3 a registry pick that is a 17 / 21 (Neo4j runs a supported Java): nothing pinned", res == []
          and chosen is None and not w.ran(o17), (_text(res), chosen, w.java_runs()))
    start_env, admin_env, _rr = _launch_envs(w)
    check("G3b ...Neo4j's environment unchanged", start_env is not None and "JAVA_HOME" not in start_env
          and admin_env is not None and "JAVA_HOME" not in admin_env, (_jh(start_env), _jh(admin_env)))

    w = _World(td / "g4")
    o21 = w.pf_jdk("Java", "jdk-21", 21)
    w.set_path_java(21, exe=os.path.join(o21, "bin", "java.exe"))
    w.pick = ("21.0.1", o21)
    res, out, exc, chosen = _preflight(w)
    check("G4 the registry pick IS the PATH java (this dev box's shape): nothing pinned", res == [] and chosen is None,
          (_text(res), chosen))

    w = _World(td / "g5")
    w.set_path_java(21)
    res, out, exc, chosen = _preflight(w)
    check("G5 no registry pick at all: nothing pinned, one run, no search", res == [] and chosen is None
          and len(w.java_runs()) == 1 and w.searched == 0, (_text(res), chosen, w.java_runs()))

    w = _World(td / "g6")
    w.set_path_java(21)                           # a launcher outside any JDK home (a javapath link, a scoop shim)
    o19 = w.pf_jdk("Java", "jdk-19", 19)
    w.pick = ("19", o19)
    t21 = w.pf_jdk("Eclipse Adoptium", "jdk-21.0.5.11-hotspot", 21)
    res, out, exc, chosen = _preflight(w)
    check("G6 a bad registry pick and a PATH java that is not <home>\\bin\\java.exe: an installed 21 is pinned "
          "(confirmed by its own -version), the 19 never ran", res == [] and _same(chosen, t21) and not w.ran(o19),
          (_text(res), chosen, w.java_runs()))

    w = _World(td / "g7")
    w.set_path_java(21)
    o19 = w.pf_jdk("Java", "jdk-19", 19)
    w.pick = ("19", o19)
    res, out, exc, chosen = _preflight(w)
    check("G7 ...and nothing installed to hand over: the PATH verdict as before (passes), nothing pinned, no crash",
          res == [] and exc is None and chosen is None, (_text(res), chosen, exc))

    w = _World(td / "g7b")
    w.set_path_java(21)
    o19 = w.pf_jdk("Java", "jdk-19", 19)
    w.pick = ("19", o19)
    w.pf_jdk("Eclipse Adoptium", "jdk-21.0.5.11-hotspot", 21, slow=True)
    res, out, exc, chosen = _preflight(w)
    check("G7b ...an installed 21 whose -version only timed out is not handed over (the PATH java answered 21): "
          "the PATH verdict, no WARNING, nothing pinned", res == [] and chosen is None and "WARNING" not in out,
          (_text(res), chosen, out[-300:]))

    w = _World(td / "g8")
    t21 = w.pf_jdk("Eclipse Adoptium", "jdk-21.0.5.11-hotspot", 21)
    w.set_path_java(21, exe=os.path.join(t21, "bin", "java.exe"))
    jre8 = w.add_jdk(td / "g8" / "Java" / "jre1.8.0_402", 8, release=False)
    w.pick = ("1.8", jre8)                        # a Java 8 JRE: no release file, its key name says 1.8
    res, out, exc, chosen = _preflight(w)
    check("G8 a registry pick that is a JRE 8 (key name only): the PATH java's home is pinned (Neo4j would stop with "
          "UnsupportedClassVersionError)", res == [] and _same(chosen, t21), (_text(res), chosen))

    w = _World(td / "g9")
    w.set_path_java(21, slow=True)
    w.pick = ("19", str(td / "g9" / "gone"))
    res, out, exc, chosen = _preflight(w)
    check("G9 a PATH java whose -version only timed out: the old result exactly, the registry is not read",
          res == [] and "Java IS on PATH" in out and w.picked == 0 and chosen is None, (out[-300:], w.picked))

    w = _World(td / "g10", win=False)
    t21 = w.add_jdk(w.jvm / "java-21-openjdk-amd64", 21)
    w.set_path_java(21, exe=os.path.join(t21, "bin", "java"))
    w.pick = ("19", str(td / "g10" / "gone"))
    res, out, exc, chosen = _preflight(w)
    check("G10 Linux: no registry to read (asked never), nothing pinned",
          res == [] and w.picked == 0 and chosen is None, (_text(res), w.picked, chosen))

    def boom():
        raise RuntimeError("registry exploded")
    w = _World(td / "g11")
    w.set_path_java(21)
    res, out, exc, chosen = _preflight(w, patches=dict(_java_registry_pick=boom))
    check("G11 a registry read that raises: never a crash, the PATH verdict, nothing pinned",
          res == [] and exc is None and chosen is None, (_text(res), exc, chosen))


# --------------------------------------------------------------------------------------------------- O (order)
def gate_o_order(td: Path) -> None:
    print("\n-- O: 21 before 17; the release file before the folder name; no unsupported / 32-bit JDK is run --")
    w = _World(td / "o1")
    w.set_path_java(19)
    j17 = w.pf_jdk("Java", "jdk-17", 17)
    t21 = w.pf_jdk("Eclipse Adoptium", "jdk-21.0.5.11-hotspot", 21)
    l17 = w.add_jdk(w.la / "Programs" / "Eclipse Adoptium" / "jdk-17.0.9.9-hotspot", 17)
    w.reg = [("17", j17)]
    res, out, exc, chosen = _preflight(w)
    check("O1 a 17 (registry, Program Files, LOCALAPPDATA) and a 21: the 21 is used", res == [] and _same(chosen, t21),
          (_text(res), chosen))
    check("O1b ...no 17 ever ran (the release file said 17: no process needed to rank it)",
          not w.ran(j17) and not w.ran(l17) and len(w.java_runs()) == 2, w.java_runs())

    w = _World(td / "o2")
    w.set_path_java(19)
    liar = w.pf_jdk("Zulu", "zulu-21", 11, version="11.0.25")      # the folder says 21, its release file says 11
    ms17 = w.pf_jdk("Microsoft", "jdk-hotspot", 17, version="17.0.13")  # no number in the name; release says 17
    res, out, exc, chosen = _preflight(w)
    check("O2 the release file is believed over the folder name: zulu-21 holding an 11 never runs; the 17 is used",
          res == [] and _same(chosen, ms17) and not w.ran(liar), (_text(res), chosen, w.java_runs()))

    w = _World(td / "o3")
    w.set_path_java(19)
    j25 = w.pf_jdk("Java", "jdk-25", 25)
    j11 = w.pf_jdk("Java", "jdk-11", 11)
    res, out, exc, chosen = _preflight(w)
    check("O3 only a 25 and an 11 installed: neither runs; the PATH 19 verdict (refused)",
          len(res) == 1 and res[0][1] == "Java 19 unsupported (needs 17/21)" and not w.ran(j25) and not w.ran(j11)
          and chosen is None, (_text(res), w.java_runs()))

    w = _World(td / "o4")
    w.set_path_java(19)
    x86 = w.pf_jdk("BellSoft", "LibericaJDK-21", 21, arch="x86")
    res, out, exc, chosen = _preflight(w)
    check("O4 a 32-bit JDK 21 (release OS_ARCH x86) is never used (Neo4j's heap does not fit it)",
          len(res) == 1 and chosen is None and not w.ran(x86), (_text(res), chosen, w.java_runs()))


# --------------------------------------------------------------------------------------------------- C (bounds)
def gate_c_bounds(td: Path) -> None:
    print("\n-- C: the search is bounded; a broken / slow PATH java; never raises --")
    cap = getattr(P, "_JAVA_FIND_MAX_PROBES", None)
    check("C0 the probe cap is a small named constant and the search's docstring says so",
          isinstance(cap, int) and 1 <= cap <= 8 and any("_JAVA_FIND_MAX_PROBES" in (getattr(P, f, None).__doc__ or "")
                                                        for f in ("_find_installed_jdk", "_java_version")
                                                        if callable(getattr(P, f, None))), cap)
    w = _World(td / "c1")
    w.set_path_java(19)
    homes = [w.pf_jdk("Java", f"jdk-21.0.{i}", 21, broken=True) for i in range(1, 7)]
    res, out, exc, chosen = _preflight(w)
    tried = [h for h in homes if w.ran(h)]
    check("C1 six broken JDK 21s: at most _JAVA_FIND_MAX_PROBES of them run; the PATH 19 verdict stands",
          isinstance(cap, int) and 1 <= len(tried) <= cap and len(res) == 1
          and res[0][1] == "Java 19 unsupported (needs 17/21)" and chosen is None, (len(tried), cap, _text(res)))
    order = [e for e in w.java_runs() if not _same(e, w.which_ret)]
    check("C1b ...the newest folder first (jdk-21.0.6, then jdk-21.0.5, ...)",
          bool(order) and _same(order[0], os.path.join(homes[-1], "bin", "java.exe"))
          and (len(order) < 2 or _same(order[1], os.path.join(homes[-2], "bin", "java.exe"))), order[:2])

    w = _World(td / "c2")
    j21 = w.pf_jdk("Java", "jdk-21", 21, broken=True)
    w.set_path_java(21, broken=True, exe=os.path.join(j21, "bin", "java.exe"))
    t17 = w.pf_jdk("Eclipse Adoptium", "jdk-17.0.9.9-hotspot", 17)
    res, out, exc, chosen = _preflight(w)
    n = sum(1 for e in w.java_runs() if _same(e, os.path.join(j21, "bin", "java.exe")))
    check("C2 the PATH java does not run (a broken 21): an installed 17 is used; the broken one is not run twice",
          res == [] and _same(chosen, t17) and n == 1, (_text(res), chosen, w.java_runs()))

    w = _World(td / "c3")
    w.set_path_java(21, broken=True)
    res, out, exc, chosen = _preflight(w)
    msg = res[0][0] if res else ""
    check("C3 a broken PATH java and nothing installed: the old short form, 'Java was found, but ...' kept",
          len(res) == 1 and res[0][1] == "java -version failed" and msg.startswith("Java was found, but `"), msg)
    check("C3b ...the remedy is one that works (install, then re-run setup; nothing found), no em dash in it",
          NOT_FOUND in msg and WINGET in msg and NO_CHANGE in msg and "PATH at a Temurin" not in msg
          and EM_DASH not in msg, msg)

    w = _World(td / "c4")
    w.set_path_java(21, slow=True)
    w.pf_jdk("Eclipse Adoptium", "jdk-21.0.5.11-hotspot", 21)
    res, out, exc, chosen = _preflight(w)
    check("C4 a PATH java whose -version only timed out: the old result exactly (WARNING, Java IS on PATH), no search",
          res == [] and "Java IS on PATH" in out and w.searched == 0 and chosen is None, (out[-300:], w.searched))

    def boom():
        raise RuntimeError("search exploded")
    w = _World(td / "c5")
    w.set_path_java(19)
    res, out, exc, chosen = _preflight(w, patches=dict(_java_install_candidates=boom))
    check("C5 a search that raises: never a crash, the PATH verdict", exc is None and len(res) == 1
          and res[0][1] == "Java 19 unsupported (needs 17/21)", (_text(res), exc))

    w = _World(td / "c6")
    w.set_path_java(19)
    (w.pf / "Java").write_bytes(b"")           # a FILE where a vendor folder is expected
    (w.pf / "Zulu").mkdir()
    (w.pf / "Zulu" / "notes.txt").write_text("not a jdk", encoding="utf-8")
    (w.home / "jdk-broken").mkdir()            # no bin
    z = w.pf_jdk("Eclipse Adoptium", "jdk-21.0.5.11-hotspot", 21)
    res, out, exc, chosen = _preflight(w)
    check("C6 odd folders (a file named Java, a folder with no JDK, a jdk* without bin) are skipped",
          res == [] and exc is None and _same(chosen, z), (_text(res), chosen, exc))

    w = _World(td / "c7")
    w.set_path_java(19)
    (w.pf / "Eclipse Adoptium" / "a1²2").mkdir(parents=True)   # a digit str.isdigit() takes but int() refuses
    late = w.add_jdk(w.home / "jdk-21", 21)
    res, out, exc, chosen = _preflight(w)
    check("C7 an odd folder name in the FIRST scanned root ('a1²2') never ends the search: a later root's JDK "
          "(<DEEPMAP_HOME>/jdk-21) is still found", res == [] and exc is None and _same(chosen, late),
          (_text(res), chosen, exc))
    fn = getattr(P, "_jdk_dirs_in", None)
    got, _o, _e, exc = _quiet(fn, str(w.pf / "Eclipse Adoptium")) if callable(fn) else (None, "", "", "missing")
    check("C7b ..._jdk_dirs_in itself never raises on it", exc is None and isinstance(got, list), (got, exc))
    for n in ("jdk-21.0.5", "jdk-21.0.13"):
        (w.pf / "Eclipse Adoptium" / n).mkdir()
    got, _o, _e, exc = _quiet(fn, str(w.pf / "Eclipse Adoptium")) if callable(fn) else (None, "", "", "missing")
    names = [os.path.basename(p) for p in (got or [])]
    check("C7c ...and still sorts the rest newest first (jdk-21.0.13 before jdk-21.0.5, not a plain text sort)",
          exc is None and "jdk-21.0.13" in names and "jdk-21.0.5" in names
          and names.index("jdk-21.0.13") < names.index("jdk-21.0.5"), names)


# --------------------------------------------------------------------------------------------------- R (registry)
class _FakeKey:
    def __init__(self, path, view):
        self.path, self.view = path, view

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeWinreg(types.ModuleType):
    """winreg's surface the registry reader uses, over {(view, key path): {subkey: {value: data}}}."""
    HKEY_LOCAL_MACHINE = "HKLM"
    KEY_READ = 0x20019
    KEY_WOW64_64KEY = 0x0100
    KEY_WOW64_32KEY = 0x0200

    def __init__(self, tree, *, deny=False, values=None):
        super().__init__("winreg")
        self.tree, self.deny, self.opened = tree, deny, []
        self.values = values or {}   # {(view, key path): {value: data}}: a top key's own values (CurrentVersion)

    def OpenKey(self, key, sub, reserved=0, access=0x20019):   # noqa: N802 — winreg's name
        if self.deny:
            raise PermissionError(5, "Access is denied")
        if isinstance(key, _FakeKey):
            view = key.view
            parent = self.tree.get((view, key.path))
            if parent is None or sub not in parent:
                raise FileNotFoundError(2, "not found", sub)
            k = _FakeKey(key.path + "\\" + sub, view)
            k.values = parent[sub]
            return k
        view = access & (self.KEY_WOW64_64KEY | self.KEY_WOW64_32KEY)
        self.opened.append((view, sub))
        if (view, sub) not in self.tree:
            raise FileNotFoundError(2, "not found", sub)
        k = _FakeKey(sub, view)
        k.values = self.values.get((view, sub), {})
        return k

    def EnumKey(self, key, i):   # noqa: N802
        names = sorted(self.tree.get((key.view, key.path), {}))
        if i >= len(names):
            raise OSError(259, "No more data is available")
        return names[i]

    def QueryValueEx(self, key, name):   # noqa: N802
        vals = getattr(key, "values", {})
        if name not in vals:
            raise FileNotFoundError(2, "not found", name)
        return vals[name], 1

    def CloseKey(self, key):   # noqa: N802
        return None


def gate_r_registry(td: Path) -> None:
    print("\n-- R: the JavaSoft registry is read with winreg (no process): every version subkey, both views --")
    fn = getattr(P, "_java_registry_homes", None)
    check("R0 provision._java_registry_homes exists", callable(fn))
    if not callable(fn):
        return
    v64, v32 = _FakeWinreg.KEY_WOW64_64KEY, _FakeWinreg.KEY_WOW64_32KEY
    tree = {
        (v64, r"SOFTWARE\JavaSoft\JDK"): {"19": {"JavaHome": r"C:\Program Files\Java\jdk-19"},
                                          "21.0.1": {"JavaHome": r"C:\Program Files\Java\jdk-21"},
                                          "21": {"JavaHome":
                                                 r"C:\Program Files\Eclipse Adoptium\jdk-21.0.5.11-hotspot"},
                                          "MSI-only": {"INSTALLDIR": r"C:\x"}, "bad-type": {"JavaHome": 7}},
        (v64, r"SOFTWARE\JavaSoft\JRE"): {"17": {"JavaHome": r"C:\jre-17"}},
        (v32, r"SOFTWARE\JavaSoft\JDK"): {"17.0.2": {"JavaHome": r"C:\Program Files (x86)\Java\jdk-17"}},
    }
    fake = _FakeWinreg(tree)
    saved = sys.modules.get("winreg", _ABSENT)
    sys.modules["winreg"] = fake
    try:
        got, _o, _e, exc = _quiet(fn)
        got = got or []
    finally:
        if saved is _ABSENT:
            sys.modules.pop("winreg", None)
        else:
            sys.modules["winreg"] = saved
    names = {(n, h) for n, h in got}
    check("R1 every version subkey of JavaSoft\\JDK is read (not only CurrentVersion), JRE too",
          exc is None and {("19", r"C:\Program Files\Java\jdk-19"), ("21.0.1", r"C:\Program Files\Java\jdk-21"),
                           ("21", r"C:\Program Files\Eclipse Adoptium\jdk-21.0.5.11-hotspot"),
                           ("17", r"C:\jre-17")} <= names, (got, exc))
    check("R2 ...the 32-bit view too", ("17.0.2", r"C:\Program Files (x86)\Java\jdk-17") in names, got)
    check("R3 ...a subkey without JavaHome, or with a non-text JavaHome, is skipped",
          all(isinstance(h, str) and h for _n, h in got) and len(got) == 5, got)
    check("R4 ...both registry views were opened", {v for v, _p in fake.opened} == {v64, v32}, fake.opened)

    for label, mod in (("winreg cannot be imported (another OS)", None),
                       ("the keys cannot be opened (access denied)", _FakeWinreg(tree, deny=True)),
                       ("no JavaSoft key at all", _FakeWinreg({}))):
        saved = sys.modules.get("winreg", _ABSENT)
        sys.modules["winreg"] = mod
        try:
            got, _o, _e, exc = _quiet(fn)
        finally:
            if saved is _ABSENT:
                sys.modules.pop("winreg", None)
            else:
                sys.modules["winreg"] = saved
        check(f"R5 {label}: [] and never raises", got == [] and exc is None, (got, exc))

    pick = getattr(P, "_java_registry_pick", None)
    check("R6 provision._java_registry_pick exists (Get-Java's own registry choice)", callable(pick))
    if not callable(pick):
        return
    jdk, jre = r"SOFTWARE\JavaSoft\JDK", r"SOFTWARE\JavaSoft\JRE"

    def run_pick(mod):
        saved = sys.modules.get("winreg", _ABSENT)
        sys.modules["winreg"] = mod
        try:
            got, _o, _e, exc = _quiet(pick)
        finally:
            if saved is _ABSENT:
                sys.modules.pop("winreg", None)
            else:
                sys.modules["winreg"] = saved
        return got, exc

    cases = (
        ("R7 JDK CurrentVersion -> <CurrentVersion>\\JavaHome (Oracle's '19')",
         _FakeWinreg({(v64, jdk): {"19": {"JavaHome": r"C:\Program Files\Java\jdk-19"},
                                   "21.0.1": {"JavaHome": r"C:\Program Files\Java\jdk-21"}}},
                     values={(v64, jdk): {"CurrentVersion": "19"}}),
         ("19", r"C:\Program Files\Java\jdk-19")),
        ("R8 a CurrentVersion whose subkey is missing: on to JRE (as Get-Java: an error is '' and it goes on)",
         _FakeWinreg({(v64, jdk): {"21": {"JavaHome": r"C:\jdk-21"}},
                      (v64, jre): {"1.8": {"JavaHome": r"C:\Program Files\Java\jre1.8.0_402"}}},
                     values={(v64, jdk): {"CurrentVersion": "19"}, (v64, jre): {"CurrentVersion": "1.8"}}),
         ("1.8", r"C:\Program Files\Java\jre1.8.0_402")),
        ("R9 an empty JavaHome and no CurrentVersion are skipped; the 32-bit view comes after the 64-bit one",
         _FakeWinreg({(v64, jdk): {"21": {"JavaHome": ""}}, (v64, jre): {"17": {"JavaHome": r"C:\jre-17"}},
                      (v32, jdk): {"17.0.2": {"JavaHome": r"C:\Program Files (x86)\Java\jdk-17"}}},
                     values={(v64, jdk): {"CurrentVersion": "21"}, (v32, jdk): {"CurrentVersion": "17.0.2"}}),
         ("17.0.2", r"C:\Program Files (x86)\Java\jdk-17")),
        ("R10 subkeys but no CurrentVersion anywhere: no pick (Get-Java then takes the PATH java)",
         _FakeWinreg(tree), None),
        ("R11 access denied: no pick, never raises", _FakeWinreg(tree, deny=True), None),
        ("R12 winreg cannot be imported (another OS): no pick, never raises", None, None),
    )
    for label, mod, want in cases:
        got, exc = run_pick(mod)
        check(label, exc is None and got == want, (got, exc))


# --------------------------------------------------------------------------------------------------- L (Linux)
def gate_l_linux(td: Path) -> None:
    print("\n-- L: Linux: /usr/lib/jvm, /usr/lib64/jvm, SDKMAN; JAVA_HOME's bin/java; the apt / dnf remedy --")
    w = _World(td / "l1", win=False)
    w.set_path_java(25)
    d17 = w.add_jdk(w.jvm / "java-17-openjdk-amd64", 17)
    d21 = w.add_jdk(w.jvm / "java-21-openjdk-amd64", 21)
    res, out, exc, chosen = _preflight(w)
    check("L1 PATH java 25 (Debian's alternatives pick the highest), /usr/lib/jvm/java-21-openjdk-amd64 -> used",
          res == [] and exc is None and _same(chosen, d21) and not w.ran(d17), (_text(res), chosen, w.java_runs()))
    start_env, admin_env, _rr = _launch_envs(w)
    check("L1b ...and Neo4j (start and neo4j-admin) runs with JAVA_HOME = that JDK", _same(_jh(start_env), d21)
          and _same(_jh(admin_env), d21), (_jh(start_env), _jh(admin_env)))

    w = _World(td / "l2", win=False)
    f17 = w.add_jdk(w.jvm64 / "java-17-openjdk", 17)
    res, out, exc, chosen = _preflight(w)
    check("L2 no java on PATH, Fedora's /usr/lib64/jvm/java-17-openjdk -> used", res == [] and _same(chosen, f17),
          (_text(res), chosen))

    w = _World(td / "l3", win=False)
    w.set_path_java(11)
    s21 = w.add_jdk(w.sdk / "21.0.4-tem", 21)
    res, out, exc, chosen = _preflight(w)
    check("L3 PATH java 11, an SDKMAN 21.0.4-tem -> used", res == [] and _same(chosen, s21), (_text(res), chosen))

    w = _World(td / "l4", win=False)
    p25 = w.set_path_java(25)
    res, out, exc, chosen = _preflight(w)
    msg = res[0][0] if res else ""
    check("L4 PATH java 25 and nothing installed: refused, old short form", len(res) == 1
          and res[0][1] == "Java 25 unsupported (needs 17/21)" and p25 in msg, _text(res))
    check("L4b ...the Linux install commands (apt / dnf), never winget; re-run setup, no PATH / JAVA_HOME change",
          APT in msg and DNF in msg and "winget" not in msg and NOT_FOUND in msg and NO_CHANGE in msg
          and DEAD_END not in msg and EM_DASH not in msg, msg)
    inst = _install_posix(w.home)
    check("L4c ...exactly, the .tar.gz told to go where the search looks (<DEEPMAP_HOME>)",
          msg == (f"Java 25 found at {p25} ('{_line(25)}'), but Neo4j 5.26 needs 17 or 21, and {NOT_FOUND}. "
                  f"{inst[:1].upper()}{inst[1:]}, then re-run /deep-map:setup (DeepMap finds it there itself; no "
                  "PATH or JAVA_HOME change is needed)."), msg)
    w = _World(td / "l4d", win=False)
    w.set_path_java(25)
    tgz = w.add_jdk(w.home / "jdk-21.0.5+11", 21)
    res, out, exc, chosen = _preflight(w)
    check("L4d ...and a .tar.gz unpacked there is found", res == [] and _same(chosen, tgz), (_text(res), chosen))

    w = _World(td / "l5", win=False)
    w.set_path_java(25)
    jh = w.add_jdk(td / "l5" / "opt-java-openjdk", 21)
    res, out, exc, chosen = _preflight(w, JAVA_HOME=jh)
    check("L5 JAVA_HOME = a 21 (bin/java), PATH 25: passes, nothing pinned, no search",
          res == [] and chosen is None and w.searched == 0 and w.ran(jh), (_text(res), chosen, w.java_runs()))

    w = _World(td / "l6", win=False)
    w.set_path_java(21)
    nobin = td / "l6" / "not-a-jdk"
    nobin.mkdir(parents=True)
    res, out, exc, chosen = _preflight(w, JAVA_HOME=str(nobin))
    check("L6 JAVA_HOME without bin/java, nothing usable found: 'javahome', named; only the PATH java ran (a "
          "launcher outside any JDK home)", len(res) == 1 and res[0][1] == "JAVA_HOME has no bin/java"
          and str(nobin) in res[0][0] and [_norm(e) for e in w.java_runs()] == [_norm(w.which_ret)],
          (_text(res), w.java_runs()))

    w = _World(td / "l7", win=False)
    res, out, exc, chosen = _preflight(w)
    msg = res[0][0] if res else ""
    check("L7 no Java at all on Linux: 'no Java on PATH' with the apt / dnf commands and Temurin 21",
          len(res) == 1 and res[0][1] == "no Java on PATH" and APT in msg and DNF in msg and "Temurin 21" in msg, msg)

    w = _World(td / "l8", win=False)
    w.set_path_java(21)
    j19 = w.add_jdk(td / "l8" / "opt-jdk-19", 19)
    d21 = w.add_jdk(w.jvm / "java-21-openjdk-amd64", 21)
    res, out, exc, chosen = _preflight(w, JAVA_HOME=j19)
    check("L8 Linux, JAVA_HOME = a 19, /usr/lib/jvm/java-21-openjdk-amd64 installed: passes, that 21 pinned, the NOTE "
          "printed", res == [] and _same(chosen, d21) and LAST.get("passed") == j19
          and _note(f"Java 19 at {os.path.join(j19, 'bin', 'java')}", 21, chosen) in out,
          (_text(res), chosen, out[-600:]))
    start_env, admin_env, _rr = _launch_envs(w, JAVA_HOME=j19)
    check("L8b ...Neo4j (start and neo4j-admin) runs with JAVA_HOME = that 21, not the 19",
          _same(_jh(start_env), d21) and _same(_jh(admin_env), d21), (_jh(start_env), _jh(admin_env)))

    w = _World(td / "l9", win=False)
    w.set_path_java(21)
    j19 = w.add_jdk(td / "l9" / "opt-jdk-19", 19)
    res, out, exc, chosen = _preflight(w, JAVA_HOME=j19)
    msg = res[0][0] if res else ""
    check("L9 Linux, JAVA_HOME = a 19 and no 17 / 21 to use instead: refused naming JAVA_HOME, the install command "
          "first, the shell profile and a restart of Claude Code named (a running session keeps its JAVA_HOME)",
          len(res) == 1 and res[0][1] == "Java 19 unsupported (needs 17/21)" and "JAVA_HOME" in msg and APT in msg
          and "shell profile" in msg and RESTART in msg and "winget" not in msg and WIN_ENV_DIALOG not in msg
          and chosen is None and NOTE_HEAD not in out, (msg, chosen))


# --------------------------------------------------------------------------------------------------- E (env)
def _calls_in(tree, names) -> list:
    """[(top-level function name, callee name, line)] of every call to one of `names` in the module AST (a call in a
    nested helper counts for the module-level function holding it)."""
    import ast
    out = []
    for fn in tree.body:
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(fn):
            if isinstance(node, ast.Call):
                f = node.func
                name = f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else None)
                if name in names:
                    out.append((fn.name, name, node.lineno))
    return out


def gate_e_env(td: Path) -> None:
    print("\n-- E: _neo4j_java_env: the preflight's JDK reaches Neo4j; off macOS the preflight always ran first in the "
          "same process (locked), so a fresh process runs no java; a user's JAVA_HOME is kept --")
    w = _World(td / "e1")
    w.set_path_java(19)
    t21 = w.pf_jdk("Eclipse Adoptium", "jdk-21.0.5.11-hotspot", 21)
    start_env, admin_env, rr = _launch_envs(w, preflight=False)
    check("E1 off macOS, a fresh process starting Neo4j with no preflight first: no java runs, no search, the "
          "environment unchanged (as before this change; gates that start Neo4j stay hermetic)",
          start_env is not None and "JAVA_HOME" not in start_env and admin_env is not None
          and "JAVA_HOME" not in admin_env and w.java_runs() == [] and w.searched == 0,
          (_jh(start_env), _jh(admin_env), w.java_runs(), w.searched))

    mac_home = str(td / "e1-mac-jdk-21")

    def mac_rule():
        P._NEO4J_JAVA_HOME = mac_home
        return 21, 'openjdk version "21.0.4"'
    with _Patch(P, _is_macos=lambda: True, _macos_java_version=mac_rule, _JAVA_CHECKED=False, _NEO4J_JAVA_HOME=None):
        env = P._neo4j_java_env({"PATH": "x"})
    check("E1b macOS keeps its lazy run: a fresh process resolves the JDK first and hands it over",
          env.get("JAVA_HOME") == mac_home, env)

    import ast
    tree = ast.parse((REPO / "build" / "provision.py").read_text(encoding="utf-8"))
    starts = _calls_in(tree, {"start_neo4j", "set_initial_password"})
    check("E5 every Neo4j launch (start_neo4j / set_initial_password) sits in _ensure_neo4j or _start_version",
          bool(starts) and {f for f, _n, _l in starts} <= {"_ensure_neo4j", "_start_version"}, starts)
    ens = [ln for f, _n, ln in starts if f == "_ensure_neo4j"]
    pre = [ln for f, _n, ln in _calls_in(tree, {"preflight"}) if f == "_ensure_neo4j"]
    check("E5b ..._ensure_neo4j runs preflight() before its first launch", bool(ens) and bool(pre)
          and min(pre) < min(ens), (pre, ens))
    allowed = {"_start_version": {"_upgrade_locked", "_rollback_upgrade"}, "_rollback_upgrade": {"_upgrade_locked"},
               "_upgrade_locked": {"_upgrade_neo4j"}}
    sv = _calls_in(tree, set(allowed))
    check("E5c ..._start_version is reached only through _upgrade_neo4j -> _upgrade_locked (-> _rollback_upgrade); "
          "recover_neo4j_upgrade never starts Neo4j",
          bool(sv) and all(f in allowed[n] for f, n, _l in sv), sv)
    up = [ln for f, n, ln in sv if f == "_upgrade_neo4j"]
    jp = [ln for f, _n, ln in _calls_in(tree, {"_java_preflight_problems"}) if f == "_upgrade_neo4j"]
    check("E5d ..._upgrade_neo4j runs the Java preflight before it hands over to _upgrade_locked", bool(up)
          and bool(jp) and min(jp) < min(up), (jp, up))

    with _Patch(P, _is_macos=lambda: False, _NEO4J_JAVA_HOME=str(t21), _JAVA_CHECKED=True, _JAVA_PASSED_OVER=None):
        env = P._neo4j_java_env({"JAVA_HOME": r"C:\Users\me\jdk-21"})
    check("E2 a JAVA_HOME already in the environment, nothing passed over: never overridden",
          env.get("JAVA_HOME") == r"C:\Users\me\jdk-21", env)
    passed, chose = r"C:\Users\me\jdk-19", str(t21)
    with _Patch(P, _is_macos=lambda: False, _NEO4J_JAVA_HOME=chose, _JAVA_CHECKED=True, _JAVA_PASSED_OVER=passed):
        e6 = P._neo4j_java_env({"JAVA_HOME": passed, "NEO4J_CONF": "c"})
        e7 = P._neo4j_java_env({"JAVA_HOME": r"C:\Users\me\jdk-21"})
        e7b = P._neo4j_java_env({"JAVA_HOME": passed.lower()})
    check("E6 the JAVA_HOME value the preflight passed over is replaced with the JDK it chose (nothing else changes)",
          e6 == {"JAVA_HOME": chose, "NEO4J_CONF": "c"}, e6)
    check("E7 ...a different JAVA_HOME value (even one that differs only in letter case) is never touched",
          e7 == {"JAVA_HOME": r"C:\Users\me\jdk-21"} and e7b == {"JAVA_HOME": passed.lower()}, (e7, e7b))
    with _Patch(P, _is_macos=lambda: False, _NEO4J_JAVA_HOME=None, _JAVA_CHECKED=True, _JAVA_PASSED_OVER=passed):
        e8 = P._neo4j_java_env({"JAVA_HOME": passed})
    check("E8 ...and with no JDK chosen, nothing is replaced", e8 == {"JAVA_HOME": passed}, e8)

    def stale_ok():
        return 21, 'openjdk version "21.0.1"'
    with _Patch(P, _is_macos=lambda: False, _java_version=stale_ok, _JAVA_PASSED_OVER=passed,
                _JAVA_PASSED_NOTE="[provision] NOTE: JAVA_HOME names x", _NEO4J_JAVA_HOME=chose):
        r9, o9, e9, x9 = _quiet(P._java_preflight_problems)
        left = getattr(P, "_JAVA_PASSED_OVER", "absent")
    check("E9 the preflight forgets an earlier pass-over before it judges (a stubbed _java_version prints no NOTE)",
          r9 == [] and NOTE_HEAD not in o9 + e9 and left is None and x9 is None, (r9, e9, left))

    w = _World(td / "e10")
    w.set_path_java(21)
    j19 = w.add_jdk(td / "e10" / "user-jdk-19", 19)
    j21 = w.add_jdk(td / "e10" / "user-jdk-21", 21)
    w.pf_jdk("Eclipse Adoptium", "jdk-21.0.5.11-hotspot", 21)
    with _Patch(P, **w.patches()):
        with _Env(**w.env(JAVA_HOME=j19)):
            _quiet(P._java_version)
            first = (getattr(P, "_JAVA_PASSED_OVER", None), getattr(P, "_JAVA_PASSED_NOTE", None))
        with _Env(**w.env(JAVA_HOME=j21)):
            _quiet(P._java_version)
            second = (getattr(P, "_JAVA_PASSED_OVER", "absent"), getattr(P, "_JAVA_PASSED_NOTE", "absent"),
                      getattr(P, "_NEO4J_JAVA_HOME", "absent"))
    check("E10 _java_version itself forgets an earlier pass-over: a 19 passed over, then JAVA_HOME a usable 21 -> "
          "nothing passed over, no NOTE, nothing pinned", first[0] == j19 and bool(first[1])
          and second == (None, None, None), (first, second))

    def boom():
        raise RuntimeError("rule exploded")
    with _Patch(P, _is_macos=lambda: False, _java_version=boom, _JAVA_CHECKED=False, _NEO4J_JAVA_HOME=None):
        res, _o, _e, exc = _quiet(P._neo4j_java_env, {"PATH": "x"})
    check("E3 never raises: a rule that explodes leaves the environment unchanged",
          exc is None and res == {"PATH": "x"}, (res, exc))

    with _Patch(P, _is_macos=lambda: False, _NEO4J_JAVA_HOME=None, _JAVA_CHECKED=True):
        env = P._neo4j_java_env({"PATH": "x"})
    check("E4 nothing chosen (the PATH java is a 17 / 21): the environment is unchanged", env == {"PATH": "x"}, env)


# --------------------------------------------------------------------------------------------------- D (one rule)
def gate_d_one_rule() -> None:
    print("\n-- D: deps_bootstrap's Java check is preflight's own rule --")
    with _Patch(P, _USER_SET_NEO4J_URI=False):
        ck = P._bootstrap_checks()
    check("D1 _bootstrap_checks().java_problems is _java_preflight_problems itself (no copy)",
          ck.java_problems is P._java_preflight_problems, ck.java_problems)


def gate_d_bootstrap_pass_over(td: Path) -> None:
    print("\n-- D: deps_bootstrap's Java check passes a machine the pass-over serves --")
    import deps_bootstrap as B
    w = _World(td / "d2")
    w.set_path_java(21)
    j19 = w.add_jdk(td / "d2" / "user-jdk-19", 19)
    t21 = w.pf_jdk("Eclipse Adoptium", "jdk-21.0.5.11-hotspot", 21)
    with _Env(**w.env(JAVA_HOME=j19)), _Patch(P, **w.patches(), _USER_SET_NEO4J_URI=False):
        ck = P._bootstrap_checks()
        res, out, err, exc = _quiet(ck.java_problems)
        chosen = getattr(P, "_NEO4J_JAVA_HOME", None)
        vdir = td / "d2" / "venv"
        r2, o2, e2, x2 = _quiet(B._prereqs, ck, {}, vdir, install=False, model=False)
    check("D2 JAVA_HOME = a 19, a Temurin 21 installed: the bootstrap's Java check returns no problem, pins the 21 and "
          "prints the NOTE", res == [] and exc is None and _same(chosen, t21) and NOTE_HEAD in err, (res, exc, chosen))
    check("D3 ...and deps_bootstrap's own prerequisite step does not stop on Java", x2 is None
          and "PREFLIGHT FAILED" not in o2 + e2, (x2, (o2 + e2)[-400:]))


# --------------------------------------------------------------------------------------------------- X (macOS)
def gate_x_macos(td: Path) -> None:
    print("\n-- X: macOS: the macOS gate's own sections pass unchanged; the new search is never consulted there --")
    spec = importlib.util.spec_from_file_location("dm_macos_java_intel_gates",
                                                  REPO / "tests" / "macos_java_intel_gates.py")
    mg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mg)
    check("X0 the macOS gate drives this same provision module", mg.P is P, (mg.P, P))
    asked = {"search": 0, "registry": 0}
    lure = td / "x-lure" / "jdk-21"
    (lure / "bin").mkdir(parents=True)
    for n in ("java", "java.exe"):
        (lure / "bin" / n).write_bytes(b"")

    def search():
        asked["search"] += 1
        return [(21, str(lure))]

    def registry():
        asked["registry"] += 1
        return [("21", str(lure))]

    def pick():   # Windows case 2 only: V9 (off macOS, PATH 21) may ask it on a Windows host, never on a Mac
        if P._is_macos():
            asked["pick-on-a-mac"] = asked.get("pick-on-a-mac", 0) + 1
        return None
    real_pass = getattr(P, "_java_pass_over", None)

    def pass_over(*a, **k):   # the off-macOS pass-over: C (JAVA_HOME set on a Mac) must never reach it
        if P._is_macos():
            asked["pass-over-on-a-mac"] = asked.get("pass-over-on-a-mac", 0) + 1
        return real_pass(*a, **k)
    sections =(("A", mg.gate_a_no_jdk), ("B", mg.gate_b_registered), ("C", mg.gate_c_java_home_set),
                ("D", mg.gate_d_unsupported), ("K", mg.gate_k_brew_kegs), ("T", mg.gate_t_slow_java_home),
                ("V", mg.gate_v_shim))
    for name, fn in sections:
        f0 = len(mg.FAILS)
        sub = td / f"x-{name}"
        sub.mkdir()
        over = dict(_java_pass_over=pass_over) if callable(real_pass) else {}
        with _Patch(P, _java_install_candidates=search, _java_registry_homes=registry, _java_registry_pick=pick,
                    **over):
            _r, o, e, exc = _quiet(fn, sub)
            if name == "C" and getattr(P, "_JAVA_PASSED_OVER", None) is not None:
                asked["passed-over-on-a-mac"] = P._JAVA_PASSED_OVER
        check(f"X1 macOS section {name} passes as before", len(mg.FAILS) == f0 and exc is None,
              (mg.FAILS[f0:], exc))
    check("X2 ...and the new search / registry readers and the pass-over were never used on a Mac (V9's off-macOS "
          "PATH 21 included)", asked == {"search": 0, "registry": 0} and callable(real_pass), (asked, real_pass))


_STATE = ("_JAVA_CHECKED", "_NEO4J_JAVA_HOME", "_JAVA_PASSED_OVER", "_JAVA_PASSED_NOTE")


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 72)
    print("DeepMap: off macOS the Java preflight checks what Neo4j runs and finds an installed 17 / 21 (no Neo4j)")
    print("=" * 72)
    with tempfile.TemporaryDirectory(prefix="dm_java_select_") as d:
        td = Path(d)
        saved = tuple(getattr(P, k, None) for k in _STATE)
        try:
            for fn in (gate_w_windows, gate_g_registry_pick, gate_o_order, gate_c_bounds, gate_r_registry, gate_l_linux,
                       gate_e_env):
                sub = td / fn.__name__
                sub.mkdir()
                try:
                    fn(sub)
                except Exception as exc:  # noqa: BLE001 — one section's crash is a failure, not the end
                    import traceback
                    traceback.print_exc()
                    check(f"{fn.__name__} ran without an exception", False, repr(exc))
            try:
                gate_d_one_rule()
            except Exception as exc:  # noqa: BLE001
                check("gate_d_one_rule ran without an exception", False, repr(exc))
            dd = td / "d"
            dd.mkdir()
            try:
                gate_d_bootstrap_pass_over(dd)
            except Exception as exc:  # noqa: BLE001
                import traceback
                traceback.print_exc()
                check("gate_d_bootstrap_pass_over ran without an exception", False, repr(exc))
            xd = td / "x"
            xd.mkdir()
            try:
                gate_x_macos(xd)
            except Exception as exc:  # noqa: BLE001
                import traceback
                traceback.print_exc()
                check("gate_x_macos ran without an exception", False, repr(exc))
        finally:
            for k, v in zip(_STATE, saved):
                if hasattr(P, k):
                    setattr(P, k, v)
    print("\n" + "=" * 72)
    total = len(PASSES) + len(FAILS)
    print(f"RESULT: {'GREEN' if not FAILS else 'RED'} -- {len(PASSES)}/{total} checks passed.")
    for f in FAILS:
        print(f"  FAILED: {f}")
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
