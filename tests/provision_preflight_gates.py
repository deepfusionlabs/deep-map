#!/usr/bin/env python3
"""Gates for provision.py preflight (P3) — fail fast on a wrong/missing Java, an occupied bolt/http
port, or low disk, BEFORE a 180MB download + a 120s wait_for_bolt timeout. Hermetic: no Neo4j, no
real `java`, no network, no download — every dependency is stubbed."""
from __future__ import annotations

import io
import os
import sys
import types
from contextlib import redirect_stderr
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "build"))
import provision as P  # noqa: E402

_PASS = _FAIL = 0


def check(desc: str, cond: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    ok = bool(cond)
    _PASS += ok
    _FAIL += (not ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {desc}" + ("" if ok else f"  -- {detail}"))


def _fake_run(stderr: str):
    def run(*a, **k):
        return types.SimpleNamespace(stdout="", stderr=stderr, returncode=0)
    return run


def gate_java_version() -> None:
    print("\n-- _java_version(): parse every java -version format --")
    saved_which, saved_sub = P.shutil.which, P.subprocess
    # the PATH `java -version` rule of every OS but macOS (a Mac has its own rule: macos_java_intel_gates), so the
    # host's platform is pinned — on a Mac _java_version would otherwise ask /usr/libexec/java_home
    saved_mac = getattr(P, "_is_macos", None)
    # off macOS a PATH java that is missing / unsupported / broken makes the rule look for an installed 17 / 21
    # (provision._java_install_candidates): never this machine's own registry, Program Files or /usr/lib/jvm
    saved_find = (P._java_install_candidates, P._java_registry_pick)
    P._java_install_candidates = lambda: []
    P._java_registry_pick = lambda: None   # nor its JavaSoft registry (Windows, a PATH java that is a 17 / 21)
    # the PATH rule is what is parsed here, so JAVA_HOME is unset for the gate: off macOS a JAVA_HOME the host (a CI
    # runner, a stale user value) sets is judged first, and would make this gate depend on the machine
    saved_jh = os.environ.pop("JAVA_HOME", None)

    def stub(which_ret, stderr):
        P.shutil.which = lambda name: which_ret
        P.subprocess = types.SimpleNamespace(run=_fake_run(stderr),
                                             TimeoutExpired=saved_sub.TimeoutExpired)
    P._is_macos = lambda: False
    try:
        stub("/usr/bin/java", 'openjdk version "17.0.9" 2023-10-17\nOpenJDK Runtime Environment')
        check("openjdk 17.0.9 -> 17", (P._java_version() or (None,))[0] == 17)
        stub("/usr/bin/java", 'openjdk version "21" 2023-09-19')
        check('"21" -> 21', (P._java_version() or (None,))[0] == 21)
        stub("/usr/bin/java", 'java version "1.8.0_392"')
        check("legacy 1.8.0 -> 8", (P._java_version() or (None,))[0] == 8)
        stub(None, "")
        check("no java on PATH -> None", P._java_version() is None)
        stub("/usr/bin/java", "garbage; no version token")
        check("unparseable -> None", P._java_version() is None)
    finally:
        P.shutil.which, P.subprocess = saved_which, saved_sub
        P._java_install_candidates, P._java_registry_pick = saved_find
        if saved_jh is not None:
            os.environ["JAVA_HOME"] = saved_jh
        if saved_mac is None:
            del P._is_macos
        else:
            P._is_macos = saved_mac


def gate_preflight() -> None:
    print("\n-- preflight(): fail fast + actionable --")
    saved = (P._java_version, P._port_in_use, P._disk_free_gb)

    def env(java, *, port_used=False, free=100.0):
        P._java_version = lambda: java
        P._port_in_use = lambda h, p: port_used
        P._disk_free_gb = lambda path: free
    try:
        with redirect_stderr(io.StringIO()):
            env((21, "openjdk 21"))
            check("java21 + free ports + disk -> True (proceed)", P.preflight("5.26.0") is True)
            env((17, "openjdk 17"))
            check("java17 -> True", P.preflight("5.26.0") is True)
            env(None)
            check("no java -> False", P.preflight("5.26.0") is False)
            env((19, "openjdk 19"))
            check("java19 (unsupported) -> False", P.preflight("5.26.0") is False)
            env((11, "openjdk 11"))
            check("java11 (unsupported) -> False", P.preflight("5.26.0") is False)
            env((21, "j21"), port_used=True)
            check("occupied bolt/http port -> False", P.preflight("5.26.0") is False)
            env((21, "j21"), free=0.5)
            check("low disk (0.5 GB) -> False", P.preflight("5.26.0") is False)

        buf = io.StringIO()
        with redirect_stderr(buf):
            env(None)
            P.preflight("5.26.0")
        msg = buf.getvalue()
        check("no-java message is actionable (Temurin + 17/21)",
              "Temurin" in msg and "17" in msg and "21" in msg, msg)
    finally:
        P._java_version, P._port_in_use, P._disk_free_gb = saved


def gate_selfcheck() -> None:
    print("\n-- self-check: preflight is load-bearing (not a tautology) --")
    saved = (P._java_version, P._port_in_use, P._disk_free_gb)
    try:
        P._port_in_use = lambda h, p: False
        P._disk_free_gb = lambda x: 100.0
        with redirect_stderr(io.StringIO()):
            P._java_version = lambda: (21, "j21")
            base = P.preflight("5.26.0")
            P._java_version = lambda: (11, "j11")
            flipped = P.preflight("5.26.0")
        check("all-good True, flip Java to 11 -> False", base is True and flipped is False)
    finally:
        P._java_version, P._port_in_use, P._disk_free_gb = saved


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 64)
    print("DeepMap — provision preflight gates (P3, no Neo4j/java/network)")
    print("=" * 64)
    gate_java_version()
    gate_preflight()
    gate_selfcheck()
    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
