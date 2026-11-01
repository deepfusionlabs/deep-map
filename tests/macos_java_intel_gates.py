#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""macOS prerequisites: the Java preflight on a Mac, and an Intel / Rosetta Python refused before setup downloads.

Before: (1) provision._java_version ran shutil.which("java"), which on every Mac is /usr/bin/java, Apple's stub. With
no JDK the stub exits 1 printing "... Unable to locate a Java Runtime." (older: "No Java runtime present, requesting
install."), so the preflight said "Java was found, but ..." and told the user to fix that Java; and with JAVA_HOME
unset the stub runs the NEWEST registered JDK, so a Mac with 25 and 21 installed was refused although 21 is there.
(2) /deep-map:setup on an Intel Mac (or an x86_64 Python under Rosetta 2 on Apple silicon) downloaded about 1 GB, then
failed at the torch step or the self-check: PyTorch publishes no current macOS x86_64 build.

Now:
  A  macOS, no JDK: "no JDK 17 or 21 that DeepMap or Neo4j can find" (where it looked) with the Temurin 21 remedy
     (`brew install --cask temurin@21`); never "Java was found", never "no Java on PATH", never "no Java is installed";
     the stub is never run (with no JDK it can offer an install dialog), no call asks macOS to install one, and every
     call has a timeout. On a macOS without /usr/libexec/java_home the stub's own "no runtime" words (both wordings)
     read the same way, and Homebrew's kegs are still looked for.
  B  macOS, JAVA_HOME unset: the first JDK 17 / 21 of a PATH java that is not the stub, the registered 21, the
     registered 17 (`/usr/libexec/java_home -v 21` / `-v 17`, each answer verified by that JDK's own -version: the
     tool may fall back to another version) is used, and Neo4j is started with exactly that JAVA_HOME (start_neo4j's
     `neo4j console` and set_initial_password's neo4j-admin): the preflight verdict and the JDK Neo4j runs can never
     differ. A JDK that only timed out is still the one handed to Neo4j. The common case costs two short calls.
  C  macOS, JAVA_HOME set: its bin/java is what is checked (what the stub and Neo4j run), never overridden; an
     unsupported one is refused naming `export JAVA_HOME=$(/usr/libexec/java_home -v 21)`; a JAVA_HOME without
     bin/java is named as such.
  D  macOS, nothing usable registered: the newest registered JDK's version is named, with the Temurin 21 remedy; a
     version manager's shim on PATH (no JDK home to hand Neo4j) keeps the old rule.
  L  locked shapes (KL-mistake:008): Windows / Linux run plain `java -version` only, never ask /usr/libexec/java_home
     or look for Homebrew kegs, keep the timeout WARNING exactly, and add no JAVA_HOME to Neo4j's environment when no
     installed 17 / 21 was found; their exact words when nothing is found (java_select_gates owns the off-macOS rule,
     its search for an installed JDK included); the gates' stub of _java_version still drives
     _java_preflight_problems.
  T  a slow /usr/libexec/java_home: retried with a longer budget (10 s, then 60 s, like `java -version`); still no
     answer: a WARNING that the installed JDKs could not be listed and setup continues (#81/#107: a slow probe is never
     read as missing) — never "no JDK ... can find", never "no 17 / 21 registered"; it is not asked again; a PATH or
     Homebrew JDK 17 / 21 is still found and pinned.
  K  Homebrew's keg-only openjdk@21 / openjdk@17 / openjdk (17 or 21 by its own -version), under /opt/homebrew then
     /usr/local, found with os.path.isfile only (no process unless a candidate exists), after the registered 21 / 17;
     the first one confirmed is pinned for Neo4j (its opt/ path).
  V  a version manager's shim on PATH (asdf / mise / jenv: no JDK home in its path): ONE `-XshowSettings:properties
     -version` reads its JDK's own java.home, which is pinned for Neo4j when it is a 17 / 21; the version is read from
     the version lines, never the properties block; JDKs whose home is known are asked plain -version only.
  S  the gates that stub Java (provision_preflight_gates, setup_bootstrap_gates section E, set_initial_password_gates)
     pin the platform where they assert non-Mac wording or call counts, so each passes on a Mac — simulated in-process
     (_is_macos, /usr/libexec/java_home, /usr/bin/java on PATH, a fresh process's Java state, darwin arm64 for
     deps_bootstrap), with JAVA_HOME unset and set — as off macOS.
  I  deps_bootstrap: darwin + x86_64 refuses before anything is installed or downloaded (and before the Java check):
     an Intel Mac is not supported (PyTorch has no current build for it); under Rosetta 2 the verdict says to use a
     native arm64 Python, naming the interpreter /deep-map:setup runs (`python` on Claude Code's PATH, python3 where
     there is none) and the check `python -c "import platform; print(platform.machine())"` (no setting picks that
     interpreter: no DEEPMAP_PYTHON claim). Rosetta is read through sysctlbyname (ctypes), else `sysctl -n` with a
     timeout, never on another OS. An Apple-silicon arm64 Python, Linux and Windows are unaffected; a complete
     environment is reused and a model-only finish is not refused (nothing to install for torch); a darwin simulated
     through _platform alone (as setup_bootstrap_gates does) never takes a non-Mac host's architecture.
  R  the environment probe reports its interpreter's machine and Rosetta; on macOS an existing environment that needs
     work and whose Python runs as x86_64 (or as another architecture than the Python running setup) is moved aside
     and made afresh under the existing safe rules (never while a process runs from it), never repaired in place —
     or, when the Python running setup cannot serve either, refused with the platform message, nothing moved. A
     complete one is reused as is; Linux / Windows and a probe naming no architecture are unchanged.

Hermetic: temp dirs only, fake `java` / `java_home` answers, no JVM, no Neo4j, no network.
Usage: python tests/macos_java_intel_gates.py
"""
from __future__ import annotations

import io
import json
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
import deps_bootstrap as B  # noqa: E402

PASSES: list = []
FAILS: list = []
_WIN = os.name == "nt"
STUB = "/usr/bin/java"
TOOL = "/usr/libexec/java_home"


def check(name: str, ok: bool, detail="") -> None:
    (PASSES if ok else FAILS).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok or detail == "" else f" — {str(detail)[:900]}"))


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


def _version_text(major: int) -> str:
    v = "1.8.0_402" if major == 8 else f"{major}.0.4"
    return (f'openjdk version "{v}" 2024-07-16 LTS\nOpenJDK Runtime Environment Temurin-{v}+7 (build {v}+7-LTS)\n'
            f"OpenJDK 64-Bit Server VM Temurin-{v}+7 (build {v}+7-LTS, mixed mode)\n")


NO_RUNTIME_NEW = ("The operation couldn't be completed. Unable to locate a Java Runtime.\n"
                  "Please visit http://www.java.com for information on installing Java.\n")
NO_RUNTIME_OLD = "No Java runtime present, requesting install.\n"


def _props_text(home) -> str:
    """What `java -XshowSettings:properties -version` prints before its version lines (stderr): an indented
    `key = value` block, a multi-line value included; java.home only when `home` is given."""
    return ("Property settings:\n    file.encoding = UTF-8\n    java.class.version = 65.0\n"
            + (f"    java.home = {home}\n" if home else "")
            + "    java.library.path = /Users/me/Library/Java/Extensions\n        /Library/Java/Extensions\n"
              "    java.specification.version = 21\n    java.vendor.version = Temurin-21.0.4+7\n"
              "    java.vm.specification.version = 21\n    os.version = 15.0\n\n")


class _Mac:
    """A fake Mac: JDK homes registered (as /Library/Java/JavaVirtualMachines would hold them) under a temp dir, each
    <home>/bin/java an empty file; /usr/libexec/java_home and every `java -version` answered from that registry, the
    stub included (JAVA_HOME's JDK, else the newest). Homebrew's prefixes are temp dirs too (add_keg). Records every
    subprocess.run argv + timeout + env."""

    def __init__(self, td: Path, majors=(), *, fallback=True, which=STUB, slow=(), broken=(), tool_timeouts=0):
        self.td = Path(td)
        self.calls: list = []
        self.popen: list = []
        self.fallback = fallback        # java_home -v N with no match: print the newest home (rc 0), like old macOS
        self.which_ret = which
        self.slow = set(slow)           # majors whose -version times out
        self.broken = set(broken)       # majors whose -version exits 134 with a crash line
        self.tool_timeouts = tool_timeouts   # java_home calls that time out before it answers (a loaded machine)
        self.shim_home = None           # the JDK home a version manager's shim runs (its java.home); None: not printed
        self.extra: list = []           # [(major, home)] of JDKs that are not registered (a keg, a shim's target)
        self.jdks = []                  # [(major, home)] newest first
        for m in sorted(majors, reverse=True):
            self.jdks.append((m, self.add_home(f"temurin-{m}.jdk")))

    def add_home(self, name: str, *, under: str = "JavaVirtualMachines") -> str:
        home = self.td / under / name / "Contents" / "Home"
        (home / "bin").mkdir(parents=True, exist_ok=True)
        (home / "bin" / "java").write_bytes(b"")
        return str(home)

    def brew_prefixes(self) -> tuple:
        return (str(self.td / "opt-homebrew"), str(self.td / "usr-local"))

    def add_keg(self, prefix: str, keg: str, major: int) -> str:
        """A Homebrew keg-only JDK (not linked onto PATH, not registered): <prefix>/opt/<keg>/libexec/openjdk.jdk/..."""
        home = self.td / prefix / "opt" / keg / "libexec" / "openjdk.jdk" / "Contents" / "Home"
        (home / "bin").mkdir(parents=True, exist_ok=True)
        (home / "bin" / "java").write_bytes(b"")
        self.extra.append((major, str(home)))
        return str(home)

    def major_of(self, exe: str):
        for m, h in self.jdks + self.extra:
            if _norm(exe) == _norm(os.path.join(h, "bin", "java")):
                return m
        return None

    def run(self, argv, *a, **k):
        argv = [str(x) for x in (argv if isinstance(argv, (list, tuple)) else [argv])]
        self.calls.append({"argv": argv, "timeout": k.get("timeout"), "env": k.get("env")})
        if "neo4j-admin" in " ".join(argv):
            return types.SimpleNamespace(returncode=0, stdout="Changed password for user 'neo4j'.", stderr="")
        exe = argv[0]
        if exe == TOOL:
            if self.tool_timeouts > 0:
                self.tool_timeouts -= 1
                raise subprocess.TimeoutExpired(argv, k.get("timeout") or 0)
            want = argv[argv.index("-v") + 1] if "-v" in argv else None
            if not self.jdks:
                return types.SimpleNamespace(returncode=1, stdout="", stderr="Unable to locate a Java Runtime.\n")
            hits = [h for m, h in self.jdks if want is None or str(m) == want]
            if hits:
                return types.SimpleNamespace(returncode=0, stdout=hits[0] + "\n", stderr="")
            if self.fallback and "-F" not in argv and "--failfast" not in argv:
                return types.SimpleNamespace(returncode=0, stdout=self.jdks[0][1] + "\n",
                                             stderr=f'Unable to find any JVMs matching version "{want}".\n')
            return types.SimpleNamespace(returncode=1, stdout="",
                                         stderr=f'Unable to find any JVMs matching version "{want}".\n')
        if exe == STUB:
            jh = (os.environ.get("JAVA_HOME") or "").strip()
            target = os.path.join(jh, "bin", "java") if jh else (os.path.join(self.jdks[0][1], "bin", "java")
                                                                 if self.jdks else None)
            if target is None:
                return types.SimpleNamespace(returncode=1, stdout="", stderr=getattr(self, "stub_text", NO_RUNTIME_NEW))
            exe = target
        m = self.major_of(exe)
        home = os.path.dirname(os.path.dirname(exe))
        if m is None:
            if os.path.basename(os.path.dirname(exe)) == "shims" and getattr(self, "shim_major", None):
                m, home = self.shim_major, self.shim_home
            else:
                raise FileNotFoundError(2, "No such file or directory", exe)
        if m in self.slow:
            raise subprocess.TimeoutExpired(argv, k.get("timeout") or 0)
        if m in self.broken:
            return types.SimpleNamespace(returncode=134, stdout="",
                                         stderr="Error occurred during initialization of VM\nbroken\n")
        props = _props_text(home) if "-XshowSettings:properties" in argv else ""
        return types.SimpleNamespace(returncode=0, stdout="", stderr=props + _version_text(m))

    def Popen(self, argv, *a, **k):   # noqa: N802 — subprocess.Popen's name
        self.popen.append({"argv": [str(x) for x in argv], "env": k.get("env")})
        return types.SimpleNamespace(pid=424242, poll=lambda: None)

    def which(self, name, *a, **k):
        return self.which_ret if name == "java" else None

    def patches(self, *, mac=True, tool=TOOL) -> dict:
        return dict(subprocess=_delegate(subprocess, run=self.run, Popen=self.Popen),
                    shutil=_delegate(P.shutil, which=self.which),
                    _is_macos=lambda: mac, _MACOS_JAVA_HOME_TOOL=tool,
                    _macos_has_java_home_tool=lambda: tool == TOOL,
                    _MACOS_BREW_PREFIXES=self.brew_prefixes(),   # never this machine's own Homebrew
                    _java_install_candidates=lambda: [],   # nor its installed JDKs (the off-macOS search)
                    _java_registry_pick=lambda: None)      # nor its JavaSoft registry (Windows)

    def exes(self) -> list:
        return [c["argv"][0] for c in self.calls]


def _preflight(mac: _Mac, *, is_mac=True, tool=TOOL, **env):
    with _Env(**env), _Patch(P, **mac.patches(mac=is_mac, tool=tool)):
        res, out, err, exc = _quiet(P._java_preflight_problems)
        chosen = getattr(P, "_NEO4J_JAVA_HOME", None)
    return res or [], out + err, exc, chosen


def _text(res) -> str:
    return " | ".join(f"{m} [{s}]" for m, s in (res or []))


def _launch_envs(mac: _Mac, td: Path, *, is_mac=True, **env):
    """(start_neo4j's Popen env, set_initial_password's run env) after a preflight in the same process."""
    home = td / "dmhome"
    extra = dict(_pid_create_time=lambda pid: None, _instances_running=lambda *a, **k: 0,
                 _clear_blocking_neo4j_pidfile=lambda p: None, _is_initialized=lambda *a, **k: False)
    with _Env(DEEPMAP_HOME=str(home), **env), _Patch(P, **mac.patches(mac=is_mac), **extra):
        r1 = _quiet(P._java_preflight_problems)
        mac.popen.clear()
        n0 = len(mac.calls)
        r2 = _quiet(P.start_neo4j, "5.26.32", "k" * 16)
        r3 = _quiet(P.set_initial_password, "5.26.32", "deepmap", "k" * 16)
    start_env = mac.popen[-1]["env"] if mac.popen else None
    admin = [c for c in mac.calls[n0:] if "neo4j-admin" in " ".join(c["argv"])]
    admin_env = admin[-1]["env"] if admin else None
    return start_env, admin_env, (r1, r2, r3)


def _jh(env):
    return None if env is None else env.get("JAVA_HOME")


def _norm(path) -> str:
    """A path compared as the OS sees it (an 8.3 short temp dir, as on CI's Windows runners, is the long one)."""
    return os.path.normcase(os.path.realpath(os.path.abspath(str(path))))


def _same(a, b) -> bool:
    return bool(a) and bool(b) and _norm(a) == _norm(b)


# --------------------------------------------------------------------------------------------------- A
_NONE_FOUND = "no JDK 17 or 21 that DeepMap or Neo4j can find"


def gate_a_no_jdk(td: Path) -> None:
    print("\n-- A: macOS with no JDK: 'no JDK 17 or 21 ... can find' + the Temurin 21 remedy; the stub is never run --")
    mac = _Mac(td / "a1")
    res, out, exc, chosen = _preflight(mac, JAVA_HOME=None)
    t = _text(res)
    check("A1 no JDK: refused, never 'Java was found' (the stub's exit 1 is not a broken Java)",
          bool(res) and exc is None and "was found" not in t, t or exc)
    check("A2 ...says no JDK 17 or 21 that DeepMap or Neo4j can find is installed (where it looked: java_home, PATH, "
          "Homebrew's kegs), names Temurin 21 and `brew install --cask temurin@21`; never 'no Java is installed'",
          _NONE_FOUND in t and "/usr/libexec/java_home" in t and "openjdk@21" in t and "Temurin 21" in t
          and "brew install --cask temurin@21" in t and "no Java is installed" not in t, t)
    check("A3 ...never 'no Java on PATH' (/usr/bin/java IS on a Mac's PATH); short form 'no JDK 17 or 21 found'",
          "no Java on PATH" not in t and bool(res) and res[0][1] == "no JDK 17 or 21 found", t)
    check("A4 the stub /usr/bin/java was never run (with no JDK it can offer an install dialog)",
          STUB not in mac.exes(), mac.exes())
    check("A5 no call asks macOS to install a Java (no --request / -R), and every call has a timeout",
          mac.calls and all("--request" not in c["argv"] and "-R" not in c["argv"] for c in mac.calls)
          and all(c["timeout"] and c["timeout"] <= 60 for c in mac.calls), mac.calls)
    check("A6 nothing chosen for Neo4j", chosen is None, chosen)

    for label, text in (("macOS 11+ wording", NO_RUNTIME_NEW), ("older wording", NO_RUNTIME_OLD)):
        mac = _Mac(td / f"a7-{len(label)}")
        mac.stub_text = text
        res, out, exc, chosen = _preflight(mac, tool="/nonexistent/java_home", JAVA_HOME=None)
        t = _text(res)
        check(f"A7 no /usr/libexec/java_home: the stub's own 'no runtime' ({label}) reads as no JDK found",
              bool(res) and "was found" not in t and _NONE_FOUND in t and exc is None, t or exc)
    mac = _Mac(td / "a8")
    mac.stub_text = NO_RUNTIME_NEW
    keg = mac.add_keg("opt-homebrew", "openjdk@21", 21)
    res, out, exc, chosen = _preflight(mac, tool="/nonexistent/java_home", JAVA_HOME=None)
    check("A8 no /usr/libexec/java_home and only the stub on PATH, but Homebrew's keg-only openjdk@21: used, pinned",
          res == [] and _same(chosen, keg) and exc is None, (res, chosen, exc))


# --------------------------------------------------------------------------------------------------- B
def gate_b_registered(td: Path) -> None:
    print("\n-- B: JAVA_HOME unset: a registered 21 / 17 is used, and Neo4j is started with that very JAVA_HOME --")
    for fb in (True, False):
        tag = "java_home falls back" if fb else "java_home fails on no match"
        mac = _Mac(td / f"b1-{fb}", (25, 21), fallback=fb)
        res, out, exc, chosen = _preflight(mac, JAVA_HOME=None)
        j21 = dict((m, h) for m, h in mac.jdks)[21]
        check(f"B1 25 + 21 installed (the stub runs 25), {tag}: no problem — 21 is used",
              res == [] and exc is None and _same(chosen, j21), (res, chosen, exc))
        check(f"B2 ...the stub was never run; java_home asked for 21 first", STUB not in mac.exes()
              and any(c["argv"][:1] == [TOOL] and "21" in c["argv"] for c in mac.calls), mac.calls)

    mac = _Mac(td / "b3", (25, 17))
    res, out, exc, chosen = _preflight(mac, JAVA_HOME=None)
    check("B3 25 + 17 installed: 17 is used", res == [] and _same(chosen, dict(mac.jdks)[17]), (res, chosen))

    mac = _Mac(td / "b4", (21, 17))
    res, out, exc, chosen = _preflight(mac, JAVA_HOME=None)
    check("B4 21 + 17 installed: 21 is preferred", res == [] and _same(chosen, dict(mac.jdks)[21]), (res, chosen))

    mac = _Mac(td / "b5", (21,))
    res, out, exc, chosen = _preflight(mac, JAVA_HOME=None)
    jv = [c for c in mac.calls if c["argv"][0] != TOOL]
    check("B5 only 21 installed: used; the common case is two short calls (java_home + one -version)",
          res == [] and _same(chosen, dict(mac.jdks)[21]) and len(mac.calls) <= 2 and len(jv) == 1, mac.calls)

    mac = _Mac(td / "b6", (25, 21))
    start_env, admin_env, rr = _launch_envs(mac, td / "b6", JAVA_HOME=None)
    j21 = dict(mac.jdks)[21]
    check("B6 start_neo4j hands Neo4j the JAVA_HOME the preflight passed (the registered 21, not the stub's 25)",
          _same(_jh(start_env), j21), (_jh(start_env), j21, rr[1][3]))
    check("B7 set_initial_password's neo4j-admin gets the same JAVA_HOME", _same(_jh(admin_env), j21),
          (_jh(admin_env), rr[2][3]))
    check("B8 ...and the JDK that JAVA_HOME names is the one whose -version the preflight read",
          bool(start_env) and mac.major_of(os.path.join(_jh(start_env) or "", "bin", "java")) == 21, _jh(start_env))

    mac = _Mac(td / "b9", (21,), slow={21})
    res, out, exc, chosen = _preflight(mac, JAVA_HOME=None)
    check("B9 the registered 21's -version only TIMED OUT: proceed with a WARNING (as on every OS) ...",
          res == [] and "WARNING" in out and exc is None, (res, out[-300:]))
    check("B10 ...and that same JDK is the one handed to Neo4j", _same(chosen, dict(mac.jdks)[21]), chosen)

    mac = _Mac(td / "b11", (21, 17), broken={21})
    res, out, exc, chosen = _preflight(mac, JAVA_HOME=None)
    check("B11 a registered 21 that does not run: the registered 17 is used", res == []
          and _same(chosen, dict(mac.jdks)[17]), (res, chosen))

    # a fresh process: start_neo4j without a preflight first resolves by the same rule (never a launch on a guess)
    mac = _Mac(td / "b12", (25, 21))
    home = td / "b12" / "dmhome"
    extra = dict(_pid_create_time=lambda pid: None, _instances_running=lambda *a, **k: 0,
                 _clear_blocking_neo4j_pidfile=lambda p: None, _JAVA_CHECKED=False, _NEO4J_JAVA_HOME=None)
    with _Env(DEEPMAP_HOME=str(home), JAVA_HOME=None), _Patch(P, **mac.patches(), **extra):
        _quiet(P.start_neo4j, "5.26.32", "k" * 16)
    env = mac.popen[-1]["env"] if mac.popen else None
    check("B12 start_neo4j with no preflight in this process resolves the JDK by the same rule",
          _same(_jh(env), dict(mac.jdks)[21]), _jh(env))


# --------------------------------------------------------------------------------------------------- C
def gate_c_java_home_set(td: Path) -> None:
    print("\n-- C: JAVA_HOME set: its bin/java is checked (what Neo4j runs), never overridden --")
    mac = _Mac(td / "c1", (25, 21))
    j25, j21 = dict(mac.jdks)[25], dict(mac.jdks)[21]
    res, out, exc, chosen = _preflight(mac, JAVA_HOME=j25)
    t = _text(res)
    check("C1 JAVA_HOME = a JDK 25 (21 also installed): refused — the user's choice is not overridden",
          bool(res) and "Java 25" in t and res[0][1] == "Java 25 unsupported (needs 17/21)", t)
    check("C2 ...naming the exact remedy `export JAVA_HOME=$(/usr/libexec/java_home -v 21)`",
          "export JAVA_HOME=$(/usr/libexec/java_home -v 21)" in t, t)
    start_env, admin_env, _rr = _launch_envs(mac, td / "c1", JAVA_HOME=j25)
    check("C3 Neo4j keeps the user's JAVA_HOME (nothing pinned over it)", _same(_jh(start_env), j25)
          and _same(_jh(admin_env), j25), (_jh(start_env), _jh(admin_env)))

    mac = _Mac(td / "c4", (25, 21), which=None)
    j21 = dict(mac.jdks)[21]
    res, out, exc, chosen = _preflight(mac, JAVA_HOME=j21)
    check("C4 JAVA_HOME = a JDK 21: no problem; its own bin/java was checked (not the stub)",
          res == [] and STUB not in mac.exes() and any(_same(e, os.path.join(j21, "bin", "java")) for e in mac.exes()),
          (res, mac.exes()))

    mac = _Mac(td / "c5", (21,))
    bad = td / "c5" / "no-jdk-here"
    bad.mkdir(parents=True)
    res, out, exc, chosen = _preflight(mac, JAVA_HOME=str(bad))
    t = _text(res)
    check("C5 JAVA_HOME without bin/java: named as such (not 'no Java installed', not 'Java was found')",
          bool(res) and "JAVA_HOME" in t and str(bad) in t and "no Java is installed" not in t
          and "was found" not in t and exc is None, t or exc)


# --------------------------------------------------------------------------------------------------- D
def gate_d_unsupported(td: Path) -> None:
    print("\n-- D: nothing usable registered: the newest JDK's version is named, with the Temurin 21 remedy --")
    for fb in (True, False):
        mac = _Mac(td / f"d1-{fb}", (25, 11), fallback=fb)
        res, out, exc, chosen = _preflight(mac, JAVA_HOME=None)
        t = _text(res)
        check(f"D1 25 + 11 installed (java_home {'falls back' if fb else 'fails'} on no match): refused naming "
              "Java 25 (what `java -version` shows), the Temurin 21 cask, 17 / 21",
              bool(res) and "Java 25" in t and res[0][1] == "Java 25 unsupported (needs 17/21)"
              and "brew install --cask temurin@21" in t and chosen is None, (t, chosen))
        check("D2 ...still never ran the stub", STUB not in mac.exes(), mac.exes())

    mac = _Mac(td / "d3")
    keg = mac.add_home("21.0.4/libexec/openjdk.jdk", under="Cellar/openjdk@21")
    mac.extra = [(21, keg)]
    mac.which_ret = os.path.join(keg, "bin", "java")
    res, out, exc, chosen = _preflight(mac, JAVA_HOME=None)
    check("D3 a Homebrew keg's java first on PATH (not registered): used, and its home is handed to Neo4j",
          res == [] and _same(chosen, keg), (res, chosen, mac.calls))

    mac = _Mac(td / "d4", (21,))
    keg = mac.add_home("25/libexec/openjdk.jdk", under="Cellar/openjdk")
    mac.extra = [(25, keg)]
    mac.which_ret = os.path.join(keg, "bin", "java")
    res, out, exc, chosen = _preflight(mac, JAVA_HOME=None)
    check("D4 a PATH keg of 25 + a registered 21: the registered 21 is used", res == []
          and _same(chosen, dict(mac.jdks)[21]), (res, chosen))

    mac = _Mac(td / "d5")
    shim = td / "d5" / ".jenv" / "shims" / "java"
    shim.parent.mkdir(parents=True)
    shim.write_bytes(b"")
    mac.which_ret, mac.shim_major = str(shim), 21
    res, out, exc, chosen = _preflight(mac, JAVA_HOME=None)
    check("D5 a version manager's shim on PATH (no JDK home to hand Neo4j), nothing registered: the old rule — "
          "its version is read, no refusal", res == [] and exc is None, (res, exc))


# --------------------------------------------------------------------------------------------------- L
_WINGET = ("winget install -e --id EclipseAdoptium.Temurin.21.JDK --source winget --accept-source-agreements "
           "--accept-package-agreements")
_ADOPTIUM = "https://adoptium.net/temurin/releases/?version=21"


def _install(win: bool) -> str:
    """The off-macOS install remedy (java_select_gates owns its exact words); <DEEPMAP_HOME> is where an unpacked
    archive is looked for."""
    where = f"unpacked so its jdk-21 folder sits directly in {P.deepmap_home()}"
    if win:
        return (f"install Temurin 21 (`{_WINGET}`, or the .msi from {_ADOPTIUM}; a .zip from there works too, "
                f"{where})")
    return ("install Java 21 (Debian / Ubuntu: `sudo apt install openjdk-21-jre-headless`, on Debian 12 "
            "`openjdk-17-jre-headless`; Fedora / RHEL: `sudo dnf install java-21-openjdk-headless`; or Temurin 21's "
            f".tar.gz from {_ADOPTIUM}, {where})")


_FINDS_IT = "then re-run /deep-map:setup (DeepMap finds it there itself; no PATH or JAVA_HOME change is needed)"


def _missing(win: bool) -> tuple:
    """The off-macOS words for no java on PATH and no JDK 17 / 21 installed (Windows: winget; else apt / dnf)."""
    return (f"no Java on PATH, and no JDK 17 or 21 was found in the usual install folders. Neo4j 5.26 needs a JDK/JRE "
            f"17 or 21: {_install(win)}, {_FINDS_IT}. The Neo4j Community download does not bundle a JRE.",
            "no Java on PATH")


def gate_l_locked(td: Path) -> None:
    print("\n-- L: Windows / Linux: the PATH rule's exact messages, and Neo4j's environment --")
    check("L0 _is_macos() is sys.platform == 'darwin' on this machine",
          callable(getattr(P, "_is_macos", None)) and P._is_macos() == (sys.platform == "darwin"))
    for plat_label in ("Linux", "Windows"):
        mac = _Mac(td / f"l1-{plat_label}", (25, 21))
        win = plat_label == "Windows"
        with _Patch(P, is_windows=lambda w=win: w):
            res, out, exc, chosen = _preflight(mac, is_mac=False, JAVA_HOME=None)
        install = _install(win)[:1].upper() + _install(win)[1:]
        check(f"L1 {plat_label}: `java` on PATH is checked as before: Java 25 (no 17 / 21 installed) refused with "
              "the exact text: where it was found, nothing installed, the install command, re-run setup",
              res == [(f"Java 25 found at {STUB} ('openjdk version \"25.0.4\" 2024-07-16 LTS'), but Neo4j 5.26 needs "
                       "17 or 21, and no JDK 17 or 21 was found in the usual install folders. "
                       f"{install}, {_FINDS_IT}.", "Java 25 unsupported (needs 17/21)")], res)
        check(f"L2 {plat_label}: /usr/libexec/java_home is never asked", TOOL not in mac.exes(), mac.exes())
    mac = _Mac(td / "l3", ())
    mac.which_ret = None
    res, out, exc, chosen = _preflight(mac, is_mac=False, JAVA_HOME=None)
    check("L3 not macOS, no java on PATH, none installed: the 'no Java on PATH' text, exactly",
          res == [_missing(P.is_windows())], res)
    mac = _Mac(td / "l4", (21,), broken={21})
    res, out, exc, chosen = _preflight(mac, is_mac=False, JAVA_HOME=None)
    t = _text(res)
    check("L4 not macOS, a java that fails: the old 'Java was found, but ...' text and short form",
          bool(res) and t.startswith("Java was found, but `/usr/bin/java -version` (rc=134) printed: Error occurred")
          and res[0][1] == "java -version failed", t)
    mac = _Mac(td / "l5", (21,), slow={21})
    res, out, exc, chosen = _preflight(mac, is_mac=False, JAVA_HOME=None)
    check("L5 not macOS, a java that only timed out: [] + the WARNING, as before", res == [] and "WARNING" in out, out)
    check("L5b ...its exact text", "[provision] WARNING: `/usr/bin/java -version` timed out (no answer within 15s, nor on "
          "a 60s retry). Java IS on PATH, the machine is probably under heavy load; continuing. If Neo4j then fails "
          "to start, check `java -version`." in out, out)
    calls = [c["argv"] for c in mac.calls]
    check("L5c ...and `java -version` is all that ran (no -XshowSettings, no java_home, no Homebrew look-up)",
          calls and all(a[1:] == ["-version"] for a in calls), calls)
    mac = _Mac(td / "l5d", (21,))
    mac.add_keg("opt-homebrew", "openjdk@21", 21)
    mac.which_ret = None
    res, out, exc, chosen = _preflight(mac, is_mac=False, JAVA_HOME=None)
    check("L5d not macOS, no java on PATH but a Homebrew-shaped keg: still 'no Java on PATH', nothing run",
          res == [_missing(P.is_windows())] and mac.calls == [] and chosen is None, (res, mac.calls))

    mac = _Mac(td / "l6", (25, 21))
    j25 = dict(mac.jdks)[25]
    start_env, admin_env, _rr = _launch_envs(mac, td / "l6", is_mac=False, JAVA_HOME=None)
    check("L6 not macOS, no 17 / 21 installed to hand over: no JAVA_HOME is added to Neo4j's environment (start "
          "and neo4j-admin)",
          start_env is not None and "JAVA_HOME" not in start_env and admin_env is not None
          and "JAVA_HOME" not in admin_env, (_jh(start_env), _jh(admin_env)))
    start_env, admin_env, _rr = _launch_envs(mac, td / "l7", is_mac=False, JAVA_HOME=j25)
    check("L7 not macOS, JAVA_HOME set (a 25) and no 17 / 21 to use instead: passed through to Neo4j unchanged "
          "(java_select_gates owns the pass-over that replaces it when one is found)", _same(_jh(start_env), j25)
          and _same(_jh(admin_env), j25), (_jh(start_env), _jh(admin_env)))

    with _Patch(P, _java_version=lambda: (21, 'openjdk version "21.0.1"'), _is_macos=lambda: True):
        r1, *_ = _quiet(P._java_preflight_problems)

    def fake_missing():
        P._JAVA_DIAG = "missing"
        return None
    with _Patch(P, _java_version=fake_missing, _is_macos=lambda: False):
        r2, *_ = _quiet(P._java_preflight_problems)
    check("L8 a stubbed _java_version still drives the rule (21 -> []; missing off macOS -> its text)",
          r1 == [] and r2 == [_missing(P.is_windows())], (r1, r2))


# --------------------------------------------------------------------------------------------------- T
def gate_t_slow_java_home(td: Path) -> None:
    print("\n-- T: a slow /usr/libexec/java_home is retried, and never read as 'nothing is installed' --")
    mac = _Mac(td / "t1", (21,), tool_timeouts=1)
    res, out, exc, chosen = _preflight(mac, JAVA_HOME=None)
    tool = [c for c in mac.calls if c["argv"][0] == TOOL]
    check("T1 java_home's first try timed out, its retry answered: the registered 21 is used and pinned",
          res == [] and exc is None and _same(chosen, dict(mac.jdks)[21]), (res, chosen, exc))
    check("T2 ...the retry asked the same question with a longer budget (at least 60 s, as for `java -version`)",
          len(tool) >= 2 and tool[0]["argv"] == tool[1]["argv"] and (tool[1]["timeout"] or 0) >= 60
          and (tool[1]["timeout"] or 0) > (tool[0]["timeout"] or 0), tool[:2])

    mac = _Mac(td / "t3", (21,), tool_timeouts=99)
    res, out, exc, chosen = _preflight(mac, JAVA_HOME=None)
    t = _text(res) + " " + out
    tool = [c for c in mac.calls if c["argv"][0] == TOOL]
    check("T3 java_home never answered (nor on the retry): no refusal — a WARNING that the installed JDKs could not "
          "be listed, and setup continues (a slow probe is never read as missing, #81/#107)",
          res == [] and exc is None and "WARNING" in out and "could not be listed" in out, (res, out[-500:], exc))
    check("T4 ...never 'no Java is installed', never 'no JDK ... can find', never 'Java IS installed' (unknown)",
          "no Java is installed" not in t and _NONE_FOUND not in t and "Java IS installed" not in t, t[-500:])
    check("T5 ...java_home was not asked again once it ran out of time (2 tries), the stub never ran, nothing pinned",
          len(tool) == 2 and STUB not in mac.exes() and chosen is None, (mac.exes(), chosen))

    mac = _Mac(td / "t6", (), tool_timeouts=99)
    keg = mac.add_home("25/libexec/openjdk.jdk", under="Cellar/openjdk")
    mac.extra = [(25, keg)]
    mac.which_ret = os.path.join(keg, "bin", "java")
    res, out, exc, chosen = _preflight(mac, JAVA_HOME=None)
    t = _text(res)
    check("T6 a PATH JDK 25 and a java_home that never answers: a WARNING and continue — never 'Java 25 found ... no "
          "JDK 17 or 21 is registered' (what is registered is unknown)",
          res == [] and "WARNING" in out and "registered on this Mac" not in t, (t, out[-300:]))

    mac = _Mac(td / "t7", (), tool_timeouts=99)
    keg = mac.add_keg("opt-homebrew", "openjdk@21", 21)
    res, out, exc, chosen = _preflight(mac, JAVA_HOME=None)
    check("T7 java_home never answers, but Homebrew's keg-only openjdk@21 is there: it is used and pinned (no warning)",
          res == [] and _same(chosen, keg) and "WARNING" not in out, (res, chosen, out[-300:]))


# --------------------------------------------------------------------------------------------------- K
def gate_k_brew_kegs(td: Path) -> None:
    print("\n-- K: Homebrew's keg-only openjdk@21 / @17 / openjdk (not on PATH, not registered) is found and pinned --")
    check("K0 Homebrew is looked for at /opt/homebrew, then /usr/local; kegs openjdk@21, openjdk@17, openjdk",
          getattr(P, "_MACOS_BREW_PREFIXES", None) == ("/opt/homebrew", "/usr/local")
          and getattr(P, "_MACOS_BREW_KEGS", None) == ("openjdk@21", "openjdk@17", "openjdk"),
          (getattr(P, "_MACOS_BREW_PREFIXES", None), getattr(P, "_MACOS_BREW_KEGS", None)))
    mac = _Mac(td / "k1")
    keg = mac.add_keg("opt-homebrew", "openjdk@21", 21)
    res, out, exc, chosen = _preflight(mac, JAVA_HOME=None)
    check("K1 nothing registered, only the stub on PATH, Homebrew's keg-only openjdk@21: used, its opt/ home pinned",
          res == [] and exc is None and _same(chosen, keg) and STUB not in mac.exes(), (res, chosen, mac.calls))
    start_env, admin_env, _rr = _launch_envs(mac, td / "k1", JAVA_HOME=None)
    check("K2 ...and Neo4j (start and neo4j-admin) runs with JAVA_HOME = that keg", _same(_jh(start_env), keg)
          and _same(_jh(admin_env), keg), (_jh(start_env), _jh(admin_env)))

    mac = _Mac(td / "k3")
    keg = mac.add_keg("usr-local", "openjdk@17", 17)
    res, out, exc, chosen = _preflight(mac, JAVA_HOME=None)
    check("K3 an Intel Homebrew's /usr/local/opt/openjdk@17: used", res == [] and _same(chosen, keg), (res, chosen))

    mac = _Mac(td / "k4")
    keg = mac.add_keg("opt-homebrew", "openjdk", 21)
    res, out, exc, chosen = _preflight(mac, JAVA_HOME=None)
    check("K4 plain openjdk whose -version is 21: used", res == [] and _same(chosen, keg), (res, chosen))

    mac = _Mac(td / "k5")
    mac.add_keg("opt-homebrew", "openjdk", 25)
    res, out, exc, chosen = _preflight(mac, JAVA_HOME=None)
    t = _text(res)
    check("K5 only plain openjdk at 25: refused naming Java 25 (its own -version), Homebrew's kegs and the Temurin 21 "
          "remedy; nothing pinned", bool(res) and "Java 25" in t and res[0][1] == "Java 25 unsupported (needs 17/21)"
          and "openjdk@21" in t and "brew install --cask temurin@21" in t and chosen is None, (t, chosen))

    mac = _Mac(td / "k6", (21,))
    k17 = mac.add_keg("opt-homebrew", "openjdk@17", 17)
    res, out, exc, chosen = _preflight(mac, JAVA_HOME=None)
    check("K6 a registered 21 and a keg 17: the registered 21 is used, the keg's java never runs",
          res == [] and _same(chosen, dict(mac.jdks)[21])
          and not any(_same(e, os.path.join(k17, "bin", "java")) for e in mac.exes()), (chosen, mac.exes()))

    mac = _Mac(td / "k7", broken={21})
    mac.add_keg("opt-homebrew", "openjdk@21", 21)
    k17 = mac.add_keg("opt-homebrew", "openjdk@17", 17)
    res, out, exc, chosen = _preflight(mac, JAVA_HOME=None)
    check("K7 a keg openjdk@21 that does not run: the keg openjdk@17 is used", res == [] and _same(chosen, k17),
          (res, chosen))

    mac = _Mac(td / "k8")
    a = mac.add_keg("opt-homebrew", "openjdk@21", 21)
    mac.add_keg("usr-local", "openjdk@21", 21)
    res, out, exc, chosen = _preflight(mac, JAVA_HOME=None)
    check("K8 openjdk@21 under both /opt/homebrew and /usr/local: Apple silicon's /opt/homebrew one",
          res == [] and _same(chosen, a), chosen)

    mac = _Mac(td / "k9")
    for pre in ("opt-homebrew", "usr-local"):   # keg directories without a java (a half-removed keg)
        (mac.td / pre / "opt" / "openjdk@21" / "libexec").mkdir(parents=True)
    res, out, exc, chosen = _preflight(mac, JAVA_HOME=None)
    brew_runs = [e for e in mac.exes() if "opt-homebrew" in e or "usr-local" in e]
    check("K9 keg directories without a bin/java: os.path.isfile only — no process; refused as no JDK found",
          bool(res) and brew_runs == [] and _NONE_FOUND in _text(res), (brew_runs, _text(res)))


# --------------------------------------------------------------------------------------------------- V
def gate_v_shim(td: Path) -> None:
    print("\n-- V: a version manager's shim on PATH: its JDK's own java.home is read once and handed to Neo4j --")

    def shim_mac(name, majors, shim_major, *, home=True):
        mac = _Mac(td / name, majors)
        shim = td / name / ".asdf" / "shims" / "java"
        shim.parent.mkdir(parents=True)
        shim.write_bytes(b"")
        target = mac.add_home(f"temurin-{shim_major}.0.4+7", under="asdf-installs")
        mac.extra = [(shim_major, target)]
        mac.which_ret, mac.shim_major, mac.shim_home = str(shim), shim_major, (target if home else None)
        return mac, str(shim), target

    mac, shim, target = shim_mac("v1", (25,), 21)
    res, out, exc, chosen = _preflight(mac, JAVA_HOME=None)
    check("V1 an asdf / mise shim resolving to 21, only a 25 registered: passes, and the java.home the shim's JDK "
          "reports is pinned (Neo4j would otherwise run the registered 25)",
          res == [] and exc is None and _same(chosen, target), (res, chosen, exc))
    runs = [c for c in mac.calls if _same(c["argv"][0], shim)]
    check("V2 ...the shim ran ONCE, as `-XshowSettings:properties -version` (not also a plain -version), with a timeout",
          len(runs) == 1 and runs[0]["argv"][1:] == ["-XshowSettings:properties", "-version"]
          and runs[0]["timeout"] and runs[0]["timeout"] <= 60, [c["argv"] for c in mac.calls])
    start_env, admin_env, _rr = _launch_envs(mac, td / "v1", JAVA_HOME=None)
    check("V3 ...start_neo4j and neo4j-admin get JAVA_HOME = that java.home", _same(_jh(start_env), target)
          and _same(_jh(admin_env), target), (_jh(start_env), _jh(admin_env)))

    mac, shim, target = shim_mac("v4", (21,), 25)
    res, out, exc, chosen = _preflight(mac, JAVA_HOME=None)
    check("V4 a shim resolving to 25 and a registered 21: the registered 21 is used",
          res == [] and _same(chosen, dict(mac.jdks)[21]), (res, chosen))

    mac, shim, target = shim_mac("v5", (21,), 21, home=False)
    res, out, exc, chosen = _preflight(mac, JAVA_HOME=None)
    check("V5 a shim resolving to 21 that names no java.home, a registered 21: the registered one is pinned",
          res == [] and _same(chosen, dict(mac.jdks)[21]), (res, chosen))

    mac, shim, target = shim_mac("v6", (25,), 21, home=False)
    res, out, exc, chosen = _preflight(mac, JAVA_HOME=None)
    check("V6 a shim resolving to 21 that names no java.home, only a 25 registered: the old PATH rule (passes, nothing "
          "pinned)", res == [] and chosen is None, (res, chosen))

    mac, shim, target = shim_mac("v7", (), 25)
    res, out, exc, chosen = _preflight(mac, JAVA_HOME=None)
    t = _text(res)
    check("V7 a shim resolving to 25 and nothing else: refused naming Java 25 and its version line (not the "
          "properties block)", bool(res) and "Java 25 found ('openjdk version \"25.0.4\" 2024-07-16 LTS')" in t
          and "Property settings" not in t, t)

    mac = _Mac(td / "v8", (25, 21))
    _preflight(mac, JAVA_HOME=None)
    check("V8 a JDK whose home is known (registered, keg) is asked plain `-version` only — never -XshowSettings",
          all("-XshowSettings:properties" not in c["argv"] for c in mac.calls), [c["argv"] for c in mac.calls])
    mac = _Mac(td / "v9", (21,))
    res, out, exc, chosen = _preflight(mac, is_mac=False, JAVA_HOME=None)
    check("V9 off macOS the PATH java is asked plain `-version` (no -XshowSettings), as before",
          res == [] and all(c["argv"][1:] == ["-version"] for c in mac.calls), [c["argv"] for c in mac.calls])


# --------------------------------------------------------------------------------------------------- S
def _load_gate(name: str):
    import importlib.util
    spec = importlib.util.spec_from_file_location(f"dmsib_{name}", REPO / "tests" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fail_lines(text: str) -> list:
    return [ln.strip() for ln in text.splitlines() if "[FAIL]" in ln]


def gate_s_siblings_on_a_mac(td: Path) -> None:
    print("\n-- S: the gates that stub Java pass on a Mac as anywhere else (a Mac simulated in-process) --")
    pp = _load_gate("provision_preflight_gates")
    sb = _load_gate("setup_bootstrap_gates")
    sp = _load_gate("set_initial_password_gates")
    runner_jdk = td / "runner-jdk"   # CI's macOS runners set JAVA_HOME; a Mac user often does not
    (runner_jdk / "bin").mkdir(parents=True)
    (runner_jdk / "bin" / "java").write_bytes(b"")
    (runner_jdk / "bin" / "java.exe").write_bytes(b"")   # JAVA_HOME's java on a Windows host
    no_brew = (str(td / "no-opt-homebrew"), str(td / "no-usr-local"))
    real_which = shutil.which
    n = 0
    for on_mac in (True, False):
        for jh in (None, str(runner_jdk)):
            n += 1
            label = (f"{'on a Mac' if on_mac else 'off macOS'}, JAVA_HOME {'set' if jh else 'unset'}")
            # what a real Mac answers: /usr/bin/java (Apple's stub) on PATH, /usr/libexec/java_home present; for
            # deps_bootstrap darwin on an arm64 Python. Each run starts as a fresh process would (_JAVA_CHECKED).
            sim = dict(_is_macos=lambda m=on_mac: m, _macos_has_java_home_tool=lambda m=on_mac: m, _JAVA_CHECKED=False,
                       _NEO4J_JAVA_HOME=None, _MACOS_BREW_PREFIXES=no_brew,
                       shutil=_delegate(shutil, which=lambda name, *a, m=on_mac, **k: (
                           STUB if m and name == "java" else real_which(name, *a, **k))))
            bsim = dict(_platform=lambda: "darwin", _mac_machine=lambda: "arm64", _sysctl_int=lambda name: 0) \
                if on_mac else {}
            # one sibling per block: each gate file runs in its own process, so none inherits another's Java check
            with _Env(JAVA_HOME=jh), _Patch(P, **sim):
                f0 = pp._FAIL
                _r, o, e, x = _quiet(lambda: (pp.gate_java_version(), pp.gate_preflight(), pp.gate_selfcheck()))
                check(f"S1 provision_preflight_gates' Java sections pass {label}", pp._FAIL == f0 and x is None,
                      (_fail_lines(o + e), x))
            with _Env(JAVA_HOME=jh), _Patch(P, **sim), _Patch(B, **bsim):
                f0 = len(sb.FAILS)
                (td / f"sb{n}").mkdir()
                _r, o, e, x = _quiet(sb.gate_e_prereqs, B, P, td / f"sb{n}")
                check(f"S2 setup_bootstrap_gates' section E (Java before any download) passes {label}",
                      len(sb.FAILS) == f0 and x is None, (sb.FAILS[f0:], x))
            with _Env(JAVA_HOME=jh), _Patch(P, **sim):
                f0 = sp._FAIL
                _r, o, e, x = _quiet(sp.gate_windows_refuses_unsafe, P)
                check(f"S3 set_initial_password_gates' neo4j-admin call count holds {label}",
                      sp._FAIL == f0 and x is None, (_fail_lines(o + e), x))


# --------------------------------------------------------------------------------------------------- I
class _Boot:
    """Fakes for deps_bootstrap's process seams: records steps / captures and answers the probe and model checks.
    probe: the environment's answers in order (the last one repeats); vdir: where the "venv" step lays a venv out."""

    def __init__(self, probe=None, vdir=None):
        self.events: list = []
        self.probe = list(probe or [{"missing": [], "version": "3.12.7", "pip": True}])
        self.vdir = vdir

    def run_step(self, key, argv, *, env, log, what, timeout_s):
        self.events.append(("step", key))
        if key == "venv" and self.vdir is not None:
            _venv_layout(self.vdir)
        return 0, 0.01

    def capture(self, key, argv, *, env, timeout_s):
        self.events.append(("capture", key))
        if key == "probe":
            r = self.probe.pop(0) if len(self.probe) > 1 else self.probe[0]
            return 0, json.dumps(r) + "\n"
        if key == "model-cached":
            return 0, json.dumps({"cached": getattr(self, "cached", True)}) + "\n"
        return 1, ""

    def steps(self) -> list:
        return [e[1] for e in self.events if e[0] == "step"]


def _checks(world: _Boot):
    def java_problems():
        world.events.append(("java",))
        return []
    return B.SetupChecks(census=lambda target: None, java_problems=java_problems,
                         neo4j_download_needed=lambda: False, long_paths_enabled=lambda: True)


def _ensure(world: _Boot, home: Path, *, plat, machine, translated=None, users=()):
    patches = dict(_run_step=world.run_step, _capture=world.capture, _model_cached_guess=lambda e: False,
                   _disk_free_bytes=lambda p: 500 * 1024 ** 3, _processes_using=lambda vdir: list(users),
                   _platform=lambda: plat, _mac_machine=lambda: machine,
                   _sysctl_int=lambda name: translated)
    env = {k: v for k, v in os.environ.items() if k not in ("DEEPMAP_REEXEC_GUARD", "DEEPMAP_MANAGED_VENV",
                                                            "PYTHONPATH", "PYTHONHOME")}
    env["DEEPMAP_HOME"] = str(home)
    with _Patch(B, **patches):
        return _quiet(B.ensure_env, _checks(world), target=".", env=env)


def _venv_layout(vdir: Path) -> None:
    vdir.mkdir(parents=True, exist_ok=True)
    (vdir / "pyvenv.cfg").write_text("home = /base\n", encoding="utf-8")
    py = vdir / ("Scripts" if _WIN else "bin") / ("python.exe" if _WIN else "python")
    py.parent.mkdir(parents=True, exist_ok=True)
    py.write_bytes(b"")


def _incomplete_venv(vdir: Path) -> None:
    """What a setup that died at its torch step leaves: DeepMap's marker, complete false, no python_version."""
    _venv_layout(vdir)
    (vdir / "deepmap-venv.json").write_text(json.dumps({"complete": False, "repair_pending": False,
                                                        "python_version": None}), encoding="utf-8")


def _complete_venv(vdir: Path) -> None:
    _venv_layout(vdir)
    (vdir / "deepmap-venv.json").write_text(json.dumps({"complete": True, "python_version": "3.12.7"}),
                                            encoding="utf-8")


def gate_i_intel(td: Path) -> None:
    print("\n-- I: an Intel Mac / Rosetta Python is refused before anything is installed or downloaded --")
    world = _Boot()
    home = td / "i1"
    py, out, err, exc = _ensure(world, home, plat="darwin", machine="x86_64", translated=None)
    check("I1 Intel Mac (x86_64, not translated): FAILED before any step; nothing created",
          py is None and exc is None and world.steps() == [] and not (home / "venv").exists(),
          (world.steps(), exc, err[-400:]))
    check("I2 ...the verdict names the platform: Intel Macs are not supported, PyTorch has no current build",
          "PREFLIGHT FAILED" in err and "Intel" in err and "not supported" in err and "PyTorch" in err
          and "setup verdict: FAILED —" in err and "Nothing was downloaded" in err, err[-900:])
    check("I3 ...checked before Java (no point installing a JDK for a platform setup cannot serve)",
          ("java",) not in world.events, world.events)

    world = _Boot()
    py, out, err, exc = _ensure(world, td / "i4", plat="darwin", machine="x86_64", translated=0)
    check("I4 sysctl.proc_translated = 0 (an Intel Mac that has the key): the same Intel verdict",
          py is None and world.steps() == [] and "Intel" in err and "Rosetta" not in err, err[-600:])

    world = _Boot()
    py, out, err, exc = _ensure(world, td / "i5", plat="darwin", machine="x86_64", translated=1)
    check("I5 Rosetta 2 (an x86_64 Python on Apple silicon): FAILED before any step, saying to use a native arm64 "
          "Python", py is None and world.steps() == [] and "Rosetta" in err and "arm64" in err
          and "setup verdict: FAILED —" in err and "Intel Macs are not supported" not in err, err[-900:])
    check("I5b ...naming the interpreter /deep-map:setup runs (the `python` on Claude Code's PATH, or python3 where "
          "there is none) and the check `python -c \"import platform; print(platform.machine())\"`, never python3 -c",
          "the `python` on the PATH Claude Code runs commands with, or python3 where there is none" in err
          and 'python -c "import platform; print(platform.machine())"' in err and "python3 -c" not in err, err[-1200:])
    src = Path(B.__file__).read_text(encoding="utf-8")
    check("I5c ...and no DEEPMAP_PYTHON claim: the environment decision never reads it (deps_bootstrap.py names it "
          "nowhere)", "DEEPMAP_PYTHON" not in err and "DEEPMAP_PYTHON" not in src, err[-600:])

    for plat, machine, label in (("darwin", "arm64", "Apple-silicon arm64 Python"), ("linux", "x86_64", "Linux x86_64"),
                                 ("linux", "aarch64", "Linux aarch64"), ("win32", "AMD64", "Windows AMD64")):
        world = _Boot()
        py, out, err, exc = _ensure(world, td / f"i6-{plat}-{machine}", plat=plat, machine=machine, translated=None)
        check(f"I6 {label}: not refused — the environment is built (venv, pip, torch, ..., self-check)",
              py and exc is None and "venv" in world.steps() and "torch" in world.steps()
              and "selfcheck" in world.steps() and "Intel" not in err and "Rosetta" not in err,
              (world.steps(), err[-400:]))

    world = _Boot()
    home = td / "i7"
    _complete_venv(home / "venv")
    py, out, err, exc = _ensure(world, home, plat="darwin", machine="x86_64", translated=None)
    check("I7 Intel Mac with a COMPLETE DeepMap environment: reused as is (nothing to install, nothing refused)",
          py and world.steps() == [] and "Intel" not in err, (world.steps(), err[-400:]))

    world = _Boot()
    world.cached = False
    home = td / "i8"
    _complete_venv(home / "venv")
    py, out, err, exc = _ensure(world, home, plat="darwin", machine="x86_64", translated=None)
    check("I8 Intel Mac, environment installed but the model missing: the model is fetched, nothing refused "
          "(no torch to install)", py and "model" in world.steps() and "Intel" not in err, (world.steps(), err[-400:]))

    pp = getattr(B, "_platform_problem", None)
    check("I9 _platform_problem: darwin x86_64 -> Intel; translated -> Rosetta; arm64 / Linux / Windows -> None",
          callable(pp) and "Intel" in (pp("darwin", "x86_64", False) or ("",))[0]
          and "Rosetta" in (pp("darwin", "x86_64", True) or ("",))[0] and pp("darwin", "arm64", False) is None
          and pp("linux", "x86_64", False) is None and pp("win32", "AMD64", False) is None
          and pp("linux", "aarch64", True) is None, pp)

    if sys.platform != "darwin":
        with _Patch(B, _sysctl_int=lambda name: None):
            leak = B._platform_problem("darwin") if callable(pp) else "missing"
        check("I9b a darwin simulated through _platform alone (other gates do) on a non-Mac host is not refused: the "
              "host's own x86_64 / AMD64 never reads as an Intel Mac", leak is None and B._mac_machine() == "", leak)

    # the Rosetta probe: never a process on another OS; on darwin ctypes first, else `sysctl -n` with a timeout
    runs: list = []

    def rec(argv, *a, **k):
        runs.append({"argv": [str(x) for x in argv], "timeout": k.get("timeout")})
        return types.SimpleNamespace(returncode=0, stdout="1\n", stderr="")
    si = getattr(B, "_sysctl_int", None)
    with _Patch(B, subprocess=_delegate(subprocess, run=rec), _platform=lambda: "linux"):
        v = si("sysctl.proc_translated") if callable(si) else "missing"
    check("I10 _sysctl_int off macOS: None, and no process is started", v is None and runs == [], (v, runs))
    if sys.platform != "darwin":
        runs.clear()
        with _Patch(B, subprocess=_delegate(subprocess, run=rec), _platform=lambda: "darwin"):
            v = si("sysctl.proc_translated") if callable(si) else "missing"
        check("I11 darwin without a usable sysctlbyname (ctypes): `sysctl -n <name>` with a short timeout is read",
              v == 1 and len(runs) == 1 and runs[0]["argv"][-2:] == ["-n", "sysctl.proc_translated"]
              and runs[0]["timeout"] and runs[0]["timeout"] <= 10, (v, runs))
    else:
        v = si("sysctl.proc_translated") if callable(si) else "missing"
        check("I11 real macOS: _sysctl_int answers 0 / 1 / None without raising", v in (0, 1, None), v)

    def boom(*a, **k):
        raise OSError("no sysctl")
    with _Patch(B, subprocess=_delegate(subprocess, run=boom), _platform=lambda: "darwin",
                _sysctl_ctypes=lambda name: None):
        v, o, e, x = _quiet(si, "sysctl.proc_translated") if callable(si) else ("missing", "", "", None)
    check("I12 both probes failing: None, never raises (read as 'not translated')", v is None and x is None, (v, x))


# --------------------------------------------------------------------------------------------------- R
_TORCH_MISSING = [{"name": "torch", "spec": ">=2.4", "found": None}]


def _env_probe(machine, translated=None, missing=_TORCH_MISSING) -> dict:
    return {"missing": list(missing), "version": "3.12.7", "pip": True, "machine": machine, "translated": translated}


def _leftovers(home: Path) -> list:
    return sorted(p.name for p in home.iterdir() if p.name.startswith("venv.old")) if home.is_dir() else []


def gate_r_env_arch(td: Path) -> None:
    print("\n-- R: macOS, an existing environment whose Python runs as x86_64 is re-created, never repaired in place --")
    import platform
    res, out, err, exc = _quiet(B._probe_main)
    data = B._last_json(out) or {}
    check("R1 the environment probe reports its interpreter's machine and Rosetta (sysctl.proc_translated; None off "
          "macOS, nothing run)", data.get("machine") == platform.machine() and "translated" in data
          and (sys.platform == "darwin" or data.get("translated") is None) and res == 0, out[-300:])

    home = td / "r2"
    _incomplete_venv(home / "venv")
    world = _Boot(probe=[_env_probe("x86_64", 1), _env_probe("arm64", 0, missing=[])], vdir=home / "venv")
    py, out, err, exc = _ensure(world, home, plat="darwin", machine="arm64", translated=0)
    steps = world.steps()
    check("R2 an incomplete environment an x86_64 (Rosetta) Python left, setup now on a native arm64 Python: "
          "re-created (moved aside and made afresh), then torch installed into the new one",
          py and exc is None and steps[:1] == ["venv"] and "torch" in steps and "selfcheck" in steps
          and _leftovers(home) == [], (steps, _leftovers(home), err[-500:], exc))
    check("R3 ...saying why: its Python runs as x86_64 under Rosetta 2, and PyTorch has no current x86_64 macOS build",
          "must be re-created" in out and "x86_64 under Rosetta 2" in out and "PyTorch" in out, out[-600:])

    for mine, tr, label in (("x86_64", 1, "an x86_64 Python under Rosetta 2"), ("x86_64", 0, "an Intel Mac")):
        home = td / f"r4-{tr}"
        _incomplete_venv(home / "venv")
        before = (home / "venv" / "deepmap-venv.json").read_bytes()
        world = _Boot(probe=[_env_probe("x86_64", tr)], vdir=home / "venv")
        py, out, err, exc = _ensure(world, home, plat="darwin", machine=mine, translated=tr)
        check(f"R4 the same environment, setup still on {label}: refused with the platform message before any step; "
              "the environment is left exactly as it was", py is None and world.steps() == []
              and ("Rosetta" in err if tr else "Intel Macs are not supported" in err) and "setup verdict: FAILED —" in err
              and (home / "venv" / "deepmap-venv.json").read_bytes() == before and _leftovers(home) == [],
              (world.steps(), err[-500:]))

    home = td / "r5"
    _incomplete_venv(home / "venv")
    world = _Boot(probe=[_env_probe("arm64", 0)], vdir=home / "venv")
    py, out, err, exc = _ensure(world, home, plat="darwin", machine="x86_64", translated=1)
    check("R5 an arm64 environment and an x86_64 (Rosetta) Python running setup: refused with the Rosetta message, "
          "not repaired", py is None and world.steps() == [] and "Rosetta" in err, (world.steps(), err[-400:]))

    home = td / "r6"
    _incomplete_venv(home / "venv")
    world = _Boot(probe=[_env_probe("arm64", 0), _env_probe("arm64", 0, missing=[])], vdir=home / "venv")
    py, out, err, exc = _ensure(world, home, plat="darwin", machine="arm64", translated=0)
    check("R6 macOS, environment and setup both arm64: repaired in place as before (no venv step)",
          py and "venv" not in world.steps() and "torch" in world.steps(), (world.steps(), err[-400:]))

    for plat, machine in (("linux", "x86_64"), ("win32", "AMD64")):
        home = td / f"r7-{plat}"
        _incomplete_venv(home / "venv")
        world = _Boot(probe=[_env_probe(machine), _env_probe(machine, missing=[])], vdir=home / "venv")
        py, out, err, exc = _ensure(world, home, plat=plat, machine="", translated=None)
        check(f"R7 {plat}: an environment reporting {machine} (as every one there does) is repaired in place, unchanged",
              py and "venv" not in world.steps() and "torch" in world.steps() and "re-created" not in out,
              (world.steps(), out[-300:]))

    home = td / "r8"
    _complete_venv(home / "venv")
    world = _Boot(probe=[_env_probe("x86_64", 1, missing=[])], vdir=home / "venv")
    py, out, err, exc = _ensure(world, home, plat="darwin", machine="arm64", translated=0)
    check("R8 macOS, a COMPLETE x86_64 environment with nothing missing (it works, under Rosetta): reused as is",
          py and world.steps() == [], (world.steps(), err[-400:]))

    home = td / "r9"
    _incomplete_venv(home / "venv")
    before = (home / "venv" / "deepmap-venv.json").read_bytes()
    world = _Boot(probe=[_env_probe("x86_64", 1)], vdir=home / "venv")
    py, out, err, exc = _ensure(world, home, plat="darwin", machine="arm64", translated=0,
                                users=[(4242, f"{home / 'venv' / 'bin' / 'python'} deepmap_search_server.py")])
    check("R9 ...while a process runs from it: refused by the move-aside rule (names it), nothing moved or changed",
          py is None and world.steps() == [] and "4242" in err and (home / "venv" / "deepmap-venv.json").read_bytes()
          == before and _leftovers(home) == [], (world.steps(), err[-600:]))

    home = td / "r10"
    _incomplete_venv(home / "venv")
    world = _Boot(probe=[{"missing": _TORCH_MISSING, "version": "3.12.7", "pip": True},
                         {"missing": [], "version": "3.12.7", "pip": True}], vdir=home / "venv")
    py, out, err, exc = _ensure(world, home, plat="darwin", machine="arm64", translated=0)
    check("R10 macOS, a probe that names no machine: repaired in place as before", py and "venv" not in world.steps(),
          (world.steps(), err[-400:]))


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 72)
    print("DeepMap — macOS prerequisites: the Java preflight on a Mac, Intel / Rosetta refused up front")
    print("=" * 72)
    with tempfile.TemporaryDirectory(prefix="dmmacjava") as tds:
        td = Path(tds).resolve()
        with _Env(DEEPMAP_HOME=str(td / "machine-home")):
            for g in (gate_a_no_jdk, gate_b_registered, gate_c_java_home_set, gate_d_unsupported, gate_l_locked,
                      gate_t_slow_java_home, gate_k_brew_kegs, gate_v_shim, gate_s_siblings_on_a_mac, gate_i_intel,
                      gate_r_env_arch):
                try:
                    g(td / g.__name__)
                except Exception as exc:  # noqa: BLE001 — a section that dies is a failed check, not a crash
                    import traceback
                    traceback.print_exc()
                    check(f"{g.__name__} ran to the end", False, f"{type(exc).__name__}: {exc}")
    print("\n" + "=" * 72)
    total = len(PASSES) + len(FAILS)
    print(f"RESULT: {'GREEN' if not FAILS else 'RED'} — {len(PASSES)}/{total} checks passed.")
    if FAILS:
        print("FAILED: " + "; ".join(FAILS))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    raise SystemExit(main())
