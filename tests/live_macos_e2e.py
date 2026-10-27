#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LIVE end-to-end check of DeepMap on macOS (a GitHub-hosted macOS runner; .github/workflows/macos.yml).

DeepMap had never run on a real Mac: its darwin code paths (`ps -o lstart=` start stamps, `ps -o stat=` zombies,
KERN_PROCARGS2 argv, no /proc, APFS, the `spawn` start method, the /usr/bin/java stub, /var -> /private/var) were
exercised only by hermetic gates with simulated inputs. This script runs the real first-time user path once and
checks what a Mac user depends on, one check per line, and keeps going after a failure so one run reports everything:

  0 facts      the machine (macOS, CPU, limits, interpreters, Java, file-system case) - recorded, never judged
  1 pre-setup  the darwin process-identity readers on live children (start stamp in three time zones, argv of a
               long non-ASCII command line, zombie, the venv-in-use guard), the Java preflight without a JDK on PATH,
               the Xcode CLT python3 3.9 refusal, Homebrew python3 under install.sh, the memory reading
  2 setup      `python build/provision.py setup <project>` from a BARE interpreter, so setup builds DeepMap's own
               environment (<DEEPMAP_HOME>/venv), downloads Neo4j and the model, builds the graph and starts the MCP
               servers, as /deep-map:setup does on a fresh Mac; the project has 28 files, enough for graphify's
               worker pool (spawn on macOS)
  3 tools      version, status, search, impact, cypher, doctor; the MCP servers' owner records; heal leaves them
  4 spellings  the project under $TMPDIR synced through its /var and its /private/var spelling and in another
               letter case (one registry entry); a non-git folder in two letter cases; AppleDouble ._x.py twins
  5 lifecycle  stop -> status exits 3 -> the SessionStart heal hook (run as hooks.json spells it) revives Neo4j
  6 hooks      every hooks.json command under sh / bash / zsh, and under the PATH a Dock-launched app gets
  7 auto-sync  the session-start background refresh (exit code, outcome, its CPU priority while it runs)
  8 cleanup    mcp-stop + stop through DeepMap's own verbs (always)

A check is a CONTRACT: a failure is a DeepMap defect on this machine. Checks the code audit of 2026-10-25 predicted to
fail on macOS name that finding (`predicted`), so the summary separates predicted failures from surprises. Skipped =
its precondition is absent here (e.g. no case-insensitive file system on Linux).

OPT-IN and CI-ONLY: runs only with DEEPMAP_LIVE_TEST=1 AND CI=true, because it uses the machine's DEFAULT DEEPMAP_HOME
(~/.deepmap) to exercise the true first-time path and starts Neo4j on the base ports - right for a throwaway runner,
wrong on a machine someone uses. It also runs on Linux (the darwin-only checks skip), which is how it was dry-run in
a python:3.12 container before any macOS minutes were spent. Exit 0 = every contract held, 1 = one or more failed,
0 with a SKIP line = not opted in.

Usage (the workflow): DEEPMAP_LIVE_TEST=1 CI=true NEO4J_PASSWORD=deepmap python tests/live_macos_e2e.py --report DIR
Run it with a Python that has NO DeepMap packages (setup builds DeepMap's own environment); stdlib only here.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
HOOKS_JSON = ROOT / "hooks" / "hooks.json"
DARWIN = sys.platform == "darwin"

RESULTS: list = []          # {"id", "phase", "status": pass|fail|skip|info, "detail", "predicted"}
REPORT_DIR = Path(".")
LOG_DIR = Path(".")
PHASE = "0 facts"


# ------------------------------------------------------------------------------------------------------------------- #
# recording
# ------------------------------------------------------------------------------------------------------------------- #
def _short(text, n=600) -> str:
    text = "" if text is None else str(text)
    text = text.strip()
    return text if len(text) <= n else text[: n // 2] + " ... " + text[-n // 2:]


def check(cid: str, ok: bool, detail="", predicted: str | None = None) -> bool:
    RESULTS.append({"id": cid, "phase": PHASE, "status": "pass" if ok else "fail", "detail": _short(detail),
                    "predicted": predicted})
    print(f"  [{'PASS' if ok else 'FAIL'}] {cid}{(' - ' + _short(detail, 300)) if detail and not ok else ''}"
          f"{(' (predicted: ' + predicted + ')') if predicted and not ok else ''}", flush=True)
    return ok


def skip(cid: str, why: str, predicted: str | None = None) -> None:
    RESULTS.append({"id": cid, "phase": PHASE, "status": "skip", "detail": why, "predicted": predicted})
    print(f"  [SKIP] {cid} - {why}", flush=True)


def info(cid: str, detail) -> None:
    RESULTS.append({"id": cid, "phase": PHASE, "status": "info", "detail": _short(detail, 1500), "predicted": None})
    print(f"  [INFO] {cid}: {_short(detail, 300)}", flush=True)


def phase(name: str) -> None:
    global PHASE
    PHASE = name
    print(f"\n=== {name} ===", flush=True)
    try:   # a snapshot per phase, so a job timeout or a cancel still leaves the results so far
        (REPORT_DIR / "report.json").write_text(json.dumps(RESULTS, indent=1, default=str), encoding="utf-8")
    except OSError:
        pass


def guarded(label: str, fn, *args):
    """Run one phase; an exception in this script is recorded as a failed check and the run carries on."""
    try:
        return fn(*args)
    except Exception:  # noqa: BLE001
        import traceback
        check(f"{label}: the check script ran without an error", False, traceback.format_exc())
        return None


# ------------------------------------------------------------------------------------------------------------------- #
# processes
# ------------------------------------------------------------------------------------------------------------------- #
def _text(b) -> str:
    if b is None:
        return ""
    return b.decode("utf-8", "replace") if isinstance(b, bytes) else str(b)


def run(argv, *, cwd=None, env=None, timeout=600, log: str | None = None, stdin: str | None = None):
    """(rc, stdout, stderr, seconds). rc None = timed out or could not start. Never raises."""
    t0 = time.time()
    try:
        p = subprocess.run([str(a) for a in argv], cwd=None if cwd is None else str(cwd), env=env,
                           input=stdin if stdin is not None else None,
                           stdin=None if stdin is not None else subprocess.DEVNULL,
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
        rc, out, err = p.returncode, p.stdout or "", p.stderr or ""
    except subprocess.TimeoutExpired as exc:
        rc, out, err = None, _text(exc.stdout), _text(exc.stderr) + f"\n[TIMEOUT after {timeout} s]"
    except OSError as exc:
        rc, out, err = None, "", f"[could not start: {exc}]"
    secs = round(time.time() - t0, 1)
    if log:
        try:
            with open(LOG_DIR / log, "a", encoding="utf-8") as fh:
                fh.write(f"$ {' '.join(str(a) for a in argv)}\n# cwd={cwd} rc={rc} {secs}s\n{out}\n--- stderr ---\n"
                         f"{err}\n\n")
        except OSError:
            pass
    return rc, out, err, secs


PRELUDE = "import sys, os, json\nsys.path.insert(0, {build!r})\n"


def probe(py, body: str, *, env=None, timeout=180, log="probes.log"):
    """Run `body` in a child `py` with build/ importable; its LAST stdout line must be JSON. (obj | None, out+err)."""
    code = PRELUDE.format(build=str(BUILD)) + textwrap.dedent(body)
    rc, out, err, _ = run([py, "-X", "utf8", "-c", code], env=env, timeout=timeout, log=log)
    lines = [ln for ln in (out or "").strip().splitlines() if ln.strip()]
    try:
        return json.loads(lines[-1]), (out + "\n" + err)
    except (IndexError, ValueError):
        return None, f"rc={rc}\n{out}\n{err}"


def ps_table() -> list:
    """[(pid, command)] of every process (BSD and procps both take -A -ww -o)."""
    rc, out, _, _ = run(["ps", "-A", "-ww", "-o", "pid=,command="], timeout=30)
    rows = []
    for ln in (out or "").splitlines():
        parts = ln.strip().split(None, 1)
        if len(parts) == 2 and parts[0].isdigit():
            rows.append((int(parts[0]), parts[1]))
    return rows


def wait_until(pred, timeout: float, step: float = 2.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            if pred():
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(step)
    try:
        return bool(pred())
    except Exception:  # noqa: BLE001
        return False


def port_open(port: int, host: str = "127.0.0.1") -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=2):
            return True
    except OSError:
        return False


# ------------------------------------------------------------------------------------------------------------------- #
# the project under test
# ------------------------------------------------------------------------------------------------------------------- #
CORE = {
    "shop/__init__.py": '"""A small shop: orders, payments, notifications."""\n',
    "shop/orders.py": '''"""Order handling."""
from shop.payments import charge_card
from shop.notify import send_confirmation_email


def validate_order(order):
    """Reject an order with no lines or a negative total."""
    if not order.get("lines"):
        raise ValueError("empty order")
    if order.get("total", 0) < 0:
        raise ValueError("negative total")
    return True


def checkout(order):
    """Validate the order, charge the customer's card and email the confirmation."""
    validate_order(order)
    charge_card(order["card"], order["total"])
    send_confirmation_email(order["email"], order["id"])
    return order["id"]
''',
    "shop/payments.py": '''"""Card payments through the payment gateway."""


def charge_card(card, amount):
    """Charge the customer's card for the order total."""
    if amount <= 0:
        return None
    return {"card": card[-4:], "amount": amount}
''',
    "shop/notify.py": '''"""Customer notifications."""


def send_confirmation_email(address, order_id):
    """Email the order confirmation to the customer."""
    return f"to={address} subject=Order {order_id} confirmed"
''',
    "shop/api.py": '''"""HTTP entry points."""
from shop.orders import checkout


def post_checkout(request):
    """Handle POST /checkout: place the order in the request body."""
    return {"order_id": checkout(request["json"])}
''',
    "web/client.ts": '''export interface Order { id: string; total: number; email: string }

export function submitOrder(order: Order): Promise<Response> {
  return fetch("/checkout", { method: "POST", body: JSON.stringify(order) });
}
''',
    "web/format.ts": '''export function formatTotal(total: number): string {
  return `$${total.toFixed(2)}`;
}
''',
}


def make_project(root: Path, n_util: int = 21) -> None:
    """28 files (26 .py + 2 .ts): above graphify's 20-file threshold, so its worker pool starts (spawn on macOS)."""
    for rel, text in CORE.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")
    for i in range(n_util):
        (root / "shop" / f"audit_{i:02d}.py").write_text(
            f'"""Audit rule {i}."""\nfrom shop.orders import validate_order\n\n\n'
            f'def audit_rule_{i:02d}(order):\n    """Re-check order {i} before it ships."""\n'
            f'    return validate_order(order) and _note_{i:02d}(order)\n\n\n'
            f'def _note_{i:02d}(order):\n    return bool(order)\n', encoding="utf-8", newline="\n")
    git(root, "init", "-q")
    git(root, "config", "user.email", "ci@example.invalid")
    git(root, "config", "user.name", "DeepMap CI")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "initial")


def git(root: Path, *args):
    return run(["git", "-C", str(root), *args], timeout=60, log="git.log")


# ------------------------------------------------------------------------------------------------------------------- #
# phases
# ------------------------------------------------------------------------------------------------------------------- #
def facts(py: str) -> dict:
    phase("0 facts")
    f: dict = {"python": sys.executable, "version": sys.version.split()[0], "platform": sys.platform,
               "machine": platform.machine(), "tmp": tempfile.gettempdir(),
               "tmp_real": os.path.realpath(tempfile.gettempdir())}
    try:
        import multiprocessing
        import sysconfig
        f["start_method"] = multiprocessing.get_start_method()
        f["framework"] = sysconfig.get_config_var("PYTHONFRAMEWORK") or ""
    except Exception as exc:  # noqa: BLE001
        f["start_method"] = f"? {exc}"
    try:
        import resource
        f["nofile"] = resource.getrlimit(resource.RLIMIT_NOFILE)
    except Exception:  # noqa: BLE001
        pass
    for name, argv in (("sw_vers", ["sw_vers"]), ("uname", ["uname", "-a"]),
                       ("ncpu", ["sysctl", "-n", "hw.ncpu"] if DARWIN else ["nproc"]),
                       ("memsize", ["sysctl", "-n", "hw.memsize"] if DARWIN else ["free", "-b"]),
                       ("maxfiles", ["launchctl", "limit", "maxfiles"]),
                       ("java", ["java", "-version"]), ("java_home", ["/usr/libexec/java_home", "-V"]),
                       ("usr_bin_python3", ["/usr/bin/python3", "--version"]), ("git", ["git", "--version"]),
                       ("precompose", ["git", "config", "--global", "--get", "core.precomposeunicode"])):
        rc, out, err, _ = run(argv, timeout=30)
        if rc is not None:
            f[name] = _short((out + " " + err).strip(), 400)
    f["JAVA_HOME"] = os.environ.get("JAVA_HOME", "")
    f["locale_env"] = {k: os.environ.get(k) for k in ("LANG", "LC_ALL", "LC_CTYPE")}
    f["shells"] = {s: shutil.which(s) for s in ("sh", "bash", "zsh", "dash", "pwsh")}
    f["brew_python3"] = brew_python3()
    # file-system case sensitivity where the project will live
    d = Path(tempfile.mkdtemp(prefix="dmcase-"))
    (d / "CaseProbe").write_text("x", encoding="utf-8")
    f["fs_case_insensitive"] = (d / "caseprobe").exists()
    shutil.rmtree(d, ignore_errors=True)
    for k, v in f.items():
        info(k, v)
    return f


def brew_python3() -> str | None:
    for cand in ("/opt/homebrew/bin/python3", "/usr/local/bin/python3"):
        if DARWIN and os.path.exists(cand):
            return cand
    return None


CHILD = "import time, sys; time.sleep(float(sys.argv[1]))"
LONG_ARG = "x" * 300
WIDE_ARG = "José-אבג-資料"


def identity_probes(py: str, env: dict) -> None:
    phase("1 pre-setup: process identity")
    body = f'''
    import subprocess, time, signal
    import provision as P, port_registry as PR
    argv = [sys.executable, "-c", {CHILD!r}, "60", "a b", {WIDE_ARG!r}, {LONG_ARG!r}]
    t0 = time.time()
    c = subprocess.Popen(argv)
    time.sleep(1.5)
    pid = c.pid
    r = {{"pid": pid, "spawn": t0,
          "p_ct": P._pid_create_time(pid), "r_ct": PR.pid_create_time(pid),
          "p_argv": P._darwin_argv(pid), "r_argv": PR._darwin_argv(pid),
          "p_cmd": P._pid_cmdline(pid), "r_cmd": PR.pid_cmdline(pid),
          "p_alive": P._pid_alive(pid), "r_alive": PR._pid_alive(pid), "argv_tail": argv[1:]}}
    c.send_signal(signal.SIGTERM)
    zombie = None
    for _ in range(50):          # not reaped: the child is a zombie until we wait() for it
        st = (subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout or "").strip()
        if st.startswith("Z"):
            zombie = st
            break
        time.sleep(0.1)
    r["zombie_state"] = zombie
    r["p_alive_zombie"] = P._pid_alive(pid)
    r["r_alive_zombie"] = PR._pid_alive(pid)
    c.wait()
    r["p_alive_reaped"] = P._pid_alive(pid)
    r["r_alive_reaped"] = PR._pid_alive(pid)
    print(json.dumps(r, default=str))
    '''
    r, out = probe(py, body, env=env)
    if r is None:
        check("identity probe ran", False, out)
        return
    tail = r["argv_tail"]
    numeric = isinstance(r["p_ct"], (int, float))
    check("start stamp readable in provision and port_registry, and equal", r["p_ct"] is not None
          and r["p_ct"] == r["r_ct"], f"provision={r['p_ct']!r} port_registry={r['r_ct']!r}")
    if numeric:
        check("start stamp within 2 s of the spawn (float epoch, 1 s resolution on macOS)",
              abs(float(r["p_ct"]) - float(r["spawn"])) <= 2.5, f"stamp={r['p_ct']} spawn={r['spawn']:.2f}")
    if DARWIN:
        for mod in ("p", "r"):
            got = r[f"{mod}_argv"]
            check(f"{'provision' if mod == 'p' else 'port_registry'}._darwin_argv returns the exact arguments",
                  isinstance(got, list) and got[1:] == tail, f"got={got!r}")
        info("darwin argv[0] (a framework Python re-execs Python.app)", (r.get("r_argv") or ["?"])[0])
    else:
        skip("_darwin_argv returns the exact arguments", "not macOS")
    for mod, name in (("p", "provision._pid_cmdline"), ("r", "port_registry.pid_cmdline")):
        cmd = r[f"{mod}_cmd"] or ""
        check(f"{name} holds the whole of a 300-character argument", LONG_ARG in cmd,
              f"len={len(cmd)} tail={cmd[-80:]!r}", predicted="process-ps-width" if mod == "p" and DARWIN else None)
        check(f"{name} keeps a non-ASCII argument intact ({WIDE_ARG})", WIDE_ARG in cmd,
              f"len={len(cmd)} cmd={cmd[:200]!r}")
    check("a live child reads alive in both modules", r["p_alive"] is True and r["r_alive"] is True,
          f"provision={r['p_alive']} port_registry={r['r_alive']}")
    if r.get("zombie_state"):
        check("a zombie (exited, not reaped) reads NOT alive in both modules",
              r["p_alive_zombie"] is False and r["r_alive_zombie"] is False,
              f"ps stat={r['zombie_state']} provision={r['p_alive_zombie']} port_registry={r['r_alive_zombie']}")
    else:
        skip("a zombie reads NOT alive", "the child was never seen in state Z")
    check("a reaped child reads NOT alive in both modules",
          r["p_alive_reaped"] is False and r["r_alive_reaped"] is False,
          f"provision={r['p_alive_reaped']} port_registry={r['r_alive_reaped']}")

    # the same stamp read under other time zones (a runner runs in UTC, which would hide a TZ bug)
    body_tz = f'''
    import subprocess, time
    import provision as P, port_registry as PR
    c = subprocess.Popen([sys.executable, "-c", {CHILD!r}, "30"])
    time.sleep(1.5)
    out = {{"utc": P._pid_create_time(c.pid)}}
    for tz in ("America/Los_Angeles", "Asia/Tokyo", "Australia/Adelaide"):
        code = "import sys, json; sys.path.insert(0, %r); import provision as P, port_registry as PR; " \\
               "print(json.dumps([P._pid_create_time(%d), PR.pid_create_time(%d)]))" % ({str(BUILD)!r}, c.pid, c.pid)
        env = dict(os.environ, TZ=tz)
        res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
        try:
            out[tz] = json.loads(res.stdout.strip().splitlines()[-1])
        except Exception:
            out[tz] = res.stderr[-300:]
    c.kill(); c.wait()
    print(json.dumps(out, default=str))
    '''
    r, out = probe(py, body_tz, env=env)
    if r is None:
        check("time-zone probe ran", False, out)
    else:
        bad = {tz: v for tz, v in r.items() if tz != "utc" and v != [r["utc"], r["utc"]]}
        check("the start stamp is the same whatever TZ the reading process has", not bad and r["utc"] is not None,
              json.dumps(r))


def venv_guard_probe(py: str, env: dict, scratch: Path) -> None:
    """deps_bootstrap._processes_using must see a process running from a venv (the guard before a venv rebuild)."""
    vdir = scratch / "probe-venv"
    rc, out, err, _ = run([py, "-m", "venv", "--without-pip", str(vdir)], timeout=180, log="probes.log")
    vpy = vdir / "bin" / "python"
    if rc != 0 or not vpy.exists():
        skip("the venv-in-use guard sees a process started from a venv", f"could not make a venv: {_short(err)}")
        return
    body = f'''
    import subprocess, time
    import deps_bootstrap as D
    c = subprocess.Popen([{str(vpy)!r}, "-c", {CHILD!r}, "30"])
    time.sleep(1.5)
    users = D._processes_using({str(vdir)!r})
    c.kill(); c.wait()
    print(json.dumps({{"pid": c.pid, "users": users}}, default=str))
    '''
    r, out = probe(py, body, env=env)
    if r is None:
        check("venv guard probe ran", False, out)
        return
    pids = [u[0] for u in (r["users"] or [])]
    check("the venv-in-use guard sees a process started from a venv (deps_bootstrap._processes_using)",
          r["pid"] in pids, f"pid={r['pid']} users={r['users']}",
          predicted="process-venv-guard-framework-argv" if DARWIN else None)


JVM_DIR = "/Library/Java/JavaVirtualMachines"


def _java_preflight(py: str, env: dict):
    return probe(py, '''
    import provision as P
    probs = P._java_preflight_problems()
    print(json.dumps({"problems": probs, "diag": str(getattr(P, "_JAVA_DIAG", ""))}))
    ''', env=env)


def _java_major(text: str):
    import re
    m = re.search(r'version "(\d+)(?:\.(\d+))?', text or "")
    if not m:
        return None
    major = int(m.group(1))
    return int(m.group(2)) if major == 1 and m.group(2) else major


def _brew_kegs(env: dict) -> dict:
    """{home: major} of Homebrew's keg-only JDK 17 / 21 (not linked onto PATH, not registered with java_home), which
    the preflight also finds and hands to Neo4j."""
    out = {}
    for prefix in ("/opt/homebrew", "/usr/local"):
        for keg in ("openjdk@21", "openjdk@17", "openjdk"):
            home = f"{prefix}/opt/{keg}/libexec/openjdk.jdk/Contents/Home"
            java = os.path.join(home, "bin", "java")
            if os.path.isfile(java):
                rc, o, e, _ = run([java, "-version"], env=env, timeout=60, log="java.log")
                major = _java_major(o + e)
                if major in (17, 21):
                    out[home] = major
    return out


def java_probes(py: str, env: dict) -> None:
    """The Java preflight as a Mac user meets it: JAVA_HOME unset and the PATH a Dock-launched app gets, so `java` is
    /usr/bin/java, Apple's stub, which runs the newest JDK registered under /Library/Java/JavaVirtualMachines."""
    env_nojdk = {k: v for k, v in env.items() if not k.startswith("JAVA_HOME")}
    env_nojdk["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
    kegs = _brew_kegs(env_nojdk)
    info("Homebrew keg-only JDK 17 / 21", kegs)
    rc, out, err, _ = run(["/usr/bin/java", "-version"], env=env_nojdk, timeout=60, log="java.log")
    info("/usr/bin/java -version with JAVA_HOME unset", f"rc={rc} {(out + err).strip()[:300]}")
    if rc == 0:   # a registered JDK: the preflight passes iff a 17 or 21 is registered, and Neo4j gets that one
        major = _java_major(out + err)
        supported = {}
        for want in (21, 17):
            rc2, o2, e2, _ = run(["/usr/libexec/java_home", "-v", str(want)], env=env_nojdk, timeout=60, log="java.log")
            home = o2.strip() if rc2 == 0 else ""
            if home:
                rc3, o3, e3, _ = run([os.path.join(home, "bin", "java"), "-version"], env=env_nojdk, timeout=60)
                if _java_major(o3 + e3) == want:
                    supported[want] = home
        info("registered JDK homes for 21 / 17", supported)
        r, o = probe(py, '''
        import provision as P
        probs = P._java_preflight_problems()
        print(json.dumps({"problems": probs, "neo4j_java_home": P._NEO4J_JAVA_HOME,
                          "diag": str(getattr(P, "_JAVA_DIAG", ""))}))
        ''', env=env_nojdk)
        probs = " ".join(map(str, (r or {}).get("problems") or []))
        if major in (17, 21) or supported or kegs:
            home = (r or {}).get("neo4j_java_home") or ""
            hv = _java_major("".join(run([os.path.join(home, "bin", "java"), "-version"], env=env_nojdk,
                                         timeout=60)[1:3])) if home else major
            check("with a 17 or 21 registered and JAVA_HOME unset, the Java preflight passes and Neo4j gets a 17 or 21",
                  bool(r) and not r["problems"] and hv in (17, 21), json.dumps(r) if r else o)
        else:
            check(f"with only Java {major} registered, the Java preflight refuses naming it",
                  bool(r) and f"Java {major}" in probs and "no Java on PATH" not in probs, json.dumps(r) if r else o)
    # The real no-JDK path: move the registered JDKs aside for this one probe (CI=true only: a throwaway runner).
    moved = None
    if os.path.isdir(JVM_DIR) and os.listdir(JVM_DIR) and run(["sudo", "-n", "true"], timeout=30)[0] == 0:
        aside = JVM_DIR + ".deepmap-ci-aside"
        if run(["sudo", "-n", "mv", JVM_DIR, aside], timeout=60, log="java.log")[0] == 0:
            moved = aside
    try:
        rc, out, err, _ = run(["/usr/bin/java", "-version"], env=env_nojdk, timeout=60, log="java.log")
        cid = "with no JDK installed, the Java preflight refuses and does not say 'Java was found'"
        if rc == 0:
            skip(cid, f"a JDK is still reachable through /usr/bin/java: {(out + err).strip()[:200]}")
            return
        r, o = probe(py, '''
        import provision as P
        probs = P._java_preflight_problems()
        print(json.dumps({"problems": probs, "neo4j_java_home": P._NEO4J_JAVA_HOME}))
        ''', env=env_nojdk)
        probs = " ".join(map(str, (r or {}).get("problems") or []))
        if kegs:   # nothing registered, but a Homebrew keg 17 / 21 is installed: the preflight uses it
            check("with no JDK registered but a Homebrew keg JDK 17 / 21, the preflight passes and Neo4j gets the keg",
                  bool(r) and not r["problems"] and (r.get("neo4j_java_home") or "").startswith(
                      ("/opt/homebrew/opt/", "/usr/local/opt/")), json.dumps(r) if r else o)
        else:
            check(cid, bool(r) and bool(r["problems"]) and "was found" not in probs, json.dumps(r) if r else o)
    finally:
        if moved and run(["sudo", "-n", "mv", moved, JVM_DIR], timeout=60, log="java.log")[0] != 0:
            check("the registered JDKs were put back", False, f"{moved} -> {JVM_DIR}")


def pre_setup(py: str, env: dict, scratch: Path) -> None:
    identity_probes(py, env)
    phase("1 pre-setup: environment")
    venv_guard_probe(py, env, scratch)

    r, out = probe(py, '''
    import auto_sync as A
    print(json.dumps(A._memory()))
    ''', env=env)
    check("auto_sync knows the available memory (its low-memory floor depends on it)",
          bool(r) and r[0] is not None, f"(available, total) = {r}")

    if DARWIN:
        java_probes(py, env)
    else:
        skip("Java preflight without a JDK", "the /usr/bin/java stub exists only on macOS")

    clt = "/usr/bin/python3"
    rc, out, err, _ = run([clt, "-c", "import sys; print(sys.version_info[:2] < (3, 10))"], timeout=60)
    if DARWIN and rc == 0 and out.strip() == "True":
        tiny = scratch / "py39proj"
        tiny.mkdir(exist_ok=True)
        (tiny / "app.py").write_text("def main():\n    return 1\n", encoding="utf-8")
        env39 = dict(env, DEEPMAP_HOME=str(scratch / "dm39"), NEO4J_URI="bolt://127.0.0.1:1")
        rc, out, err, secs = run([clt, "-X", "utf8", str(BUILD / "provision.py"), "setup", str(tiny)], env=env39,
                                 timeout=600, log="py39.log")
        text = out + err
        check("setup under the Xcode CLT python3 (3.9) refuses cleanly: exit 1, names Python 3.10, builds nothing",
              rc == 1 and "3.10" in text and not (scratch / "dm39" / "venv").exists(), f"rc={rc} {secs}s {text}")
    else:
        skip("setup under python3 3.9 refuses cleanly", "no /usr/bin/python3 older than 3.10 here")

    bp = brew_python3()
    if bp:
        rc, out, err, _ = run([bp, "-m", "pip", "install", "--dry-run", "--quiet", "pyyaml"], timeout=180,
                              log="probes.log")
        check("install.sh's first step (pip install into the python3 on PATH) can run on Homebrew's python3",
              rc == 0, f"{bp}: rc={rc} {err}", predicted="environment-install-sh-pep668-and-py39")
    else:
        skip("install.sh on Homebrew python3", "no Homebrew python3 here")


def read_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def dm_home(env) -> Path:
    return Path(env.get("DEEPMAP_HOME") or Path.home() / ".deepmap")


def setup_phase(py: str, env: dict, proj: Path) -> bool:
    phase("2 setup (bare interpreter: setup builds DeepMap's own environment)")
    rc, out, err, secs = run([py, "-X", "utf8", str(BUILD / "provision.py"), "setup", str(proj)], cwd=proj, env=env,
                             timeout=3600, log="setup.log")
    info("setup wall time (s)", secs)
    verdict = next((ln.strip() for ln in (out + err).splitlines() if "READY" in ln or "DEGRADED" in ln
                    or "FAILED" in ln), "")
    info("setup verdict line", verdict)
    ok = check("provision.py setup exits 0 (READY or DEGRADED)", rc == 0, f"rc={rc} {secs}s {(out + err)[-1500:]}")
    home = dm_home(env)
    mark = read_json(home / "venv" / "deepmap-venv.json")
    check("DeepMap's own environment is complete (venv/deepmap-venv.json complete:true)",
          bool(mark) and mark.get("complete") is True, json.dumps(mark))
    man = read_json(proj / ".deepmap" / "graph_build.json") or {}
    check("the graph was built by graphify, not the ast fallback (graph_engines)", man.get("graph_engines") == ["graphify"],
          f"graph_engines={man.get('graph_engines')!r}")
    vpy = home / "venv" / "bin" / "python"
    if vpy.exists():
        r, o = probe(str(vpy), '''
        import importlib.metadata as M, sysconfig, platform
        out = {"python": sys.version.split()[0], "framework": sysconfig.get_config_var("PYTHONFRAMEWORK") or ""}
        for d in ("torch", "sentence-transformers", "transformers", "huggingface_hub", "neo4j", "mcp", "graphifyy",
                  "numpy"):
            try:
                out[d] = M.version(d)
            except Exception:
                out[d] = None
        try:
            import torch
            out["mps_available"] = bool(getattr(torch.backends, "mps", None) and torch.backends.mps.is_available())
        except Exception as exc:
            out["torch_import"] = repr(exc)
        print(json.dumps(out))
        ''', env=env, timeout=300)
        info("DeepMap environment", r if r else o)
        run([str(vpy), "-m", "pip", "list", "--format=freeze"], timeout=120, log="venv-freeze.log")
    for p in sorted(home.glob("neo4j/neo4j-community-*/run/*")) + sorted(home.glob("instances/*/run/*")):
        info(f"neo4j run file {p.name}", p.read_text(encoding="utf-8", errors="replace")[:200] if p.is_file() else "dir")
    for logf in list(home.glob("*.log")) + list(home.glob("neo4j/neo4j-community-*/logs/*.log")):
        try:
            hits = [ln for ln in logf.read_text(encoding="utf-8", errors="replace").splitlines()
                    if "open files" in ln.lower() or "maxfd" in ln.lower()]
            if hits:
                info(f"open-files lines in {logf.name}", "\n".join(hits[:5]))
        except OSError:
            pass
    return ok


def cli(py, env, proj, *args, timeout=600, log="cli.log"):
    return run([py, "-X", "utf8", str(BUILD / "deepmap_cli.py"), *args], cwd=proj, env=env, timeout=timeout, log=log)


def owners(env) -> list:
    out = []
    for f in sorted((dm_home(env) / "run").glob("*.owner")):
        rec = read_json(f)
        if isinstance(rec, dict) and rec.get("pid"):
            out.append((f.name, rec))
    return out


def node_count(py, env, proj):
    rc, out, err, _ = cli(py, env, proj, "cypher", "--json",
                          "MATCH (n) WHERE NOT n:_DeepMapMeta RETURN count(n) AS nodes")
    try:
        rows = json.loads(out[out.index("{"):])["rows"]
        return (int(rows[0]["nodes"]) if rc == 0 else None), out + err
    except (ValueError, KeyError, IndexError, TypeError):
        return None, f"rc={rc} {out} {err}"


def tools_phase(py: str, env: dict, proj: Path) -> None:
    phase("3 tools")
    rc, out, err, _ = cli(py, env, proj, "version")
    check("deepmap_cli.py version exits 0 (re-runs itself in DeepMap's environment)", rc == 0, out + err)
    rc, out, err, _ = cli(py, env, proj, "status")
    check("status exits 0", rc == 0, out + err)
    rc, out, err, _ = cli(py, env, proj, "search", "email the customer their order confirmation")
    check("search exits 0 and finds send_confirmation_email", rc == 0 and "send_confirmation_email" in out, out + err)
    rc, out, err, _ = cli(py, env, proj, "impact", "validate_order")
    check("impact exits 0 and names checkout and an audit rule as callers",
          rc == 0 and "checkout" in out and "audit_rule_" in out, out + err)
    rc, out, err, _ = cli(py, env, proj, "cypher", "MATCH (n) WHERE n.name = 'checkout' RETURN n.name AS name")
    check("cypher exits 0 and returns checkout", rc == 0 and "checkout" in out, out + err)
    rc, out, err, _ = cli(py, env, proj, "doctor", timeout=900)
    check("doctor exits 0 (Neo4j and a populated graph healthy)", rc == 0, out + err)
    info("doctor output", out[-2500:])

    vpy = str(dm_home(env) / "venv" / "bin" / "python")
    recs = owners(env)
    info("MCP owner records", [(n, r.get("pid")) for n, r in recs])
    check("both MCP servers have an owner record (run/deepmap-search-<port>.owner, run/deepmap-embed-<port>.owner)",
          any(n.startswith("deepmap-search-") for n, _ in recs) and any(n.startswith("deepmap-embed-") for n, _ in recs),
          json.dumps(recs, default=str))
    for name, rec in recs:
        r, o = probe(vpy, f'''
        import provision as P
        pid = {int(rec["pid"])}
        now = P._pid_create_time(pid)
        print(json.dumps({{"alive": P._pid_alive(pid), "now": now, "same": P._same_start({rec.get("created")!r}, now),
                           "cmd": P._pid_cmdline(pid)}}, default=str))
        ''', env=env)
        check(f"{name}: the recorded server is alive and its start stamp matches the record",
              bool(r) and r["alive"] is True and r["same"] is True, json.dumps(r) if r else o)
    pids = sorted(int(r["pid"]) for _, r in recs)
    r, o = probe(vpy, '''
    import deps_bootstrap as D
    print(json.dumps(D._processes_using(D.venv_dir()), default=str))
    ''', env=env)
    seen = sorted(int(u[0]) for u in (r or []))
    check("the venv-in-use guard sees the MCP servers running from DeepMap's environment",
          bool(pids) and set(pids) <= set(seen), f"servers={pids} guard sees={seen}",
          predicted="process-venv-guard-framework-argv" if DARWIN else None)

    rc, out, err, _ = run([py, "-X", "utf8", str(BUILD / "provision.py"), "heal", str(proj)], cwd=proj, env=env,
                          timeout=600, log="heal.log")
    after = sorted(int(r["pid"]) for _, r in owners(env))
    check("heal on a healthy project exits 0 and leaves the MCP servers running (same pids)",
          rc == 0 and after == pids, f"rc={rc} before={pids} after={after} {(out + err)[-800:]}")


def swap_case(path: Path) -> Path:
    """The same path with the letter case of its last ASCII-lettered component swapped."""
    parts = list(path.parts)
    for i in range(len(parts) - 1, 0, -1):
        if any(c.isalpha() for c in parts[i]):
            parts[i] = parts[i].swapcase()
            return Path(*parts)
    return path


def graph_shape(py, env, proj):
    """(nodes, distinct keys) of the project's graph, or None."""
    rc, out, err, _ = cli(py, env, proj, "cypher", "--json",
                          "MATCH (n) WHERE NOT n:_DeepMapMeta RETURN count(n) AS nodes, count(DISTINCT n.key) AS keys")
    try:
        row = json.loads(out[out.index("{"):])["rows"][0]
        return (int(row["nodes"]), int(row["keys"])) if rc == 0 else None
    except (ValueError, KeyError, IndexError, TypeError):
        return None


def spellings_phase(py: str, env: dict, proj: Path, fs_ci: bool, scratch: Path) -> None:
    phase("4 path spellings")
    vpy = str(dm_home(env) / "venv" / "bin" / "python")
    real = Path(os.path.realpath(str(proj)))
    spellings = []
    if str(real) != str(proj):
        spellings.append(("the realpath spelling (/private/...)", real))
    else:
        skip("sync through /var and /private/var spellings", f"{proj} is not behind a symlink here")
    if fs_ci:
        spellings.append(("another letter case", swap_case(proj)))
    else:
        skip("letter-case spellings", "the file system here is case-sensitive")
    if spellings:
        every = [str(proj)] + [str(s) for _, s in spellings]
        r, o = probe(vpy, f'''
        import port_registry as PR
        print(json.dumps([[PR.project_identity(s), PR.project_key(s, migrate=False)] for s in {every!r}]))
        ''', env=env)
        check("every spelling of the git project resolves to one project identity and key",
              bool(r) and all(x == r[0] for x in r), json.dumps(dict(zip(every, r or []))) if r else o)
        base = graph_shape(py, env, proj)
        info("graph shape before the spelling syncs (nodes, keys)", base)
        for label, spelled in spellings:
            rc, out, err, _ = cli(py, env, spelled, "sync", str(spelled), timeout=1800, log="sync.log")
            check(f"sync through {label} ({spelled}) exits 0", rc == 0, out[-800:] + err[-800:])
            now = graph_shape(py, env, proj)
            check(f"sync through {label} leaves the graph unchanged (same nodes and keys)",
                  base is not None and now == base, f"before={base} after={now}")
    if fs_ci:
        ng = scratch / "NonGitProj"
        ng.mkdir(exist_ok=True)
        r, o = probe(py, f'''
        import port_registry as PR
        a = PR.project_key({str(ng)!r}, migrate=False)
        b = PR.project_key({str(swap_case(ng))!r}, migrate=False)
        print(json.dumps({{"a": a, "b": b}}))
        ''', env=env)
        check("a non-git folder gets one project key whatever letter case its path is typed in",
              bool(r) and r["a"] == r["b"], json.dumps(r) if r else o, predicted="filesystem-case-identity-nongit")
    # AppleDouble twins (exFAT / SMB volumes, macOS-made archives). A twin gets its original's engine id, so a name
    # that sorts before "._" (SvelteKit's +page.ts) is where a twin would replace the real node; __MACOSX/ mirrors too.
    ad = scratch / "appledouble"
    twin = b"\x00\x05\x16\x07\x00\x02\x00\x00Mac OS X        \x00\x00"
    for rel, data in (("x.py", b"def f():\n    return 1\n"), ("._x.py", twin),
                      ("routes/+page.ts", b"export function load() { return 1 }\n"), ("routes/._+page.ts", twin),
                      ("__MACOSX/._x.py", twin), ("__MACOSX/stray.py", b"def stray():\n    return 2\n")):
        (ad / rel).parent.mkdir(parents=True, exist_ok=True)
        (ad / rel).write_bytes(data)
    r, o = probe(vpy, f'''
    from pathlib import Path
    import sync as S
    print(json.dumps(sorted(rel for _py, rel in S._iter_ast_files(Path({str(ad)!r})))))
    ''', env=env)
    check("the ast fallback's walker skips AppleDouble twins and __MACOSX/", r == ["x.py"],
          json.dumps(r) if r is not None else o)
    r, o = probe(vpy, f'''
    from pathlib import Path
    import graphify_extract as G
    res = G.extract(Path({str(ad)!r}))
    nodes = [] if res is None else [n for n in res[0] if isinstance(n, dict)]
    sf = lambda n: str(n.get("source_file") or "").replace(chr(92), "/")
    print(json.dumps({{"mapped": res is not None,
                       "files": sorted({{sf(n) for n in nodes}}),
                       "page_module": [[n.get("name"), sf(n)] for n in nodes
                                       if n.get("label") == "Module" and "page" in str(n.get("name"))]}}))
    ''', env=env, timeout=600)
    files = (r or {}).get("files") or []
    check("graphify extraction (the default engine) takes no AppleDouble twin or __MACOSX/ file into the graph",
          bool(r) and r["mapped"] and sorted(files) == ["routes/+page.ts", "x.py"], json.dumps(r) if r else o)
    check("routes/+page.ts keeps its own Module (a ._+page.ts twin does not replace it)",
          bool(r) and ["+page", "routes/+page.ts"] in (r.get("page_module") or []), json.dumps(r) if r else o)


def hook_commands() -> list:
    """[(event, matcher, command)] from hooks.json, ${CLAUDE_PLUGIN_ROOT} substituted."""
    data = read_json(HOOKS_JSON) or {}
    out = []
    for event, groups in (data.get("hooks") or {}).items():
        for g in groups:
            for h in g.get("hooks") or []:
                out.append((event, g.get("matcher") or "",
                            h["command"].replace("${CLAUDE_PLUGIN_ROOT}", str(ROOT))))
    return out


def payload(event: str, matcher: str, proj: Path, session: str = "macos-ci") -> str:
    base = {"session_id": session, "transcript_path": str(proj / ".deepmap" / "ci-transcript.jsonl"),
            "cwd": str(proj), "hook_event_name": event}
    if event == "SessionStart":
        base["source"] = "startup"
    elif "Grep" in matcher:
        base.update(tool_name="Grep", tool_input={"pattern": "checkout"})
    elif "Edit" in matcher:
        base.update(tool_name="Edit", tool_input={"file_path": str(proj / "shop" / "orders.py"),
                                                  "old_string": "a", "new_string": "b"},
                    tool_response={"filePath": str(proj / "shop" / "orders.py"), "success": True})
    else:
        base.update(tool_name="mcp__deepmap-search__deepmap_search", tool_input={"query": "checkout"},
                    tool_response=[{"type": "text", "text": "{}"}])
    return json.dumps(base)


def run_hook(shell: str, cmd: str, stdin: str, env: dict):
    return run([shell, "-c", cmd], env=env, stdin=stdin, timeout=30, log="hooks.log")


def single_json_or_empty(out: str) -> bool:
    s = (out or "").strip()
    if not s:
        return True
    try:
        return isinstance(json.loads(s), dict)
    except ValueError:
        return False


def heals_running() -> list:
    return [pid for pid, cmd in ps_table() if "provision.py" in cmd and " heal" in cmd]


def heal_headers(proj: Path) -> list:
    """The '--- <stamp> DeepMap SessionStart heal: ... (hook pid N; python ... [source]) ---' lines the heal hook
    writes, one per spawn, across heal.log and its rotation heal.log.1 (unique by stamp + hook pid)."""
    out = []
    for name in ("heal.log.1", "heal.log"):
        p = proj / ".deepmap" / name
        try:
            out += [ln for ln in p.read_text(encoding="utf-8", errors="replace").splitlines()
                    if ln.startswith("--- ") and "DeepMap SessionStart heal:" in ln]
        except OSError:
            pass
    return out


def hook_check(label: str, shell: str, event: str, matcher: str, cmd: str, env: dict, proj: Path, session: str,
               *, dock: bool = False) -> None:
    """One hooks.json command run as Claude Code runs it. Every command ends in `; exit 0`, so rc alone proves
    nothing: no Python traceback / xcrun error on stderr, stdout empty or one JSON object, and for the heal hook one
    new heal.log header (it reached its spawn) naming the interpreter setup recorded, never the hook's own python3."""
    is_heal = event == "SessionStart" and "heal" in cmd
    before = set(heal_headers(proj)) if is_heal else set()
    rc, out, err, secs = run_hook(shell, cmd, payload(event, matcher, proj, session), env)
    clean = "Traceback" not in err and "xcrun: error" not in err
    ok = rc == 0 and secs <= 10 and single_json_or_empty(out) and clean
    detail = f"rc={rc} {secs}s out={out[:300]!r} err={err[-400:]!r}"
    if is_heal:
        new = [h for h in heal_headers(proj) if h not in before]
        ok = ok and not out.strip() and len(new) == 1 and (not dock or "[setup]" in new[0]
                                                           or "[DEEPMAP_PYTHON]" in new[0])
        detail += f" new heal.log headers={new}"
    check(label, ok, detail)
    if event == "SessionStart" and "notify" in cmd:
        info(f"{label}: SessionStart note printed", bool(out.strip()))


def lifecycle_phase(py: str, env: dict, proj: Path) -> None:
    phase("5 lifecycle: stop -> status 3 -> SessionStart heal hook revives")
    before, _ = node_count(py, env, proj)
    r, o = probe(str(dm_home(env) / "venv" / "bin" / "python"), f'''
    import port_registry as PR
    rt = PR.resolve_neo4j_route({str(proj)!r})
    print(json.dumps({{"uri": rt.uri, "source": rt.source, "bolt": rt.bolt, "problem": rt.problem}}))
    ''', env=env)
    bolt = (r or {}).get("bolt")
    check("this project's Neo4j route names a bolt port (port_registry.resolve_neo4j_route)", bool(bolt),
          json.dumps(r) if r else o)
    rc, out, err, secs = run([py, "-X", "utf8", str(BUILD / "provision.py"), "stop", str(proj)], cwd=proj, env=env,
                             timeout=300, log="lifecycle.log")
    check("provision.py stop exits 0", rc == 0, f"rc={rc} {secs}s {out}{err}")
    if bolt:
        check("the Bolt port is closed after stop", wait_until(lambda: not port_open(bolt), 60), f"port {bolt}")
    rc, out, err, _ = cli(py, env, proj, "status")
    check("status exits 3 while Neo4j is down", rc == 3, f"rc={rc} {out[-400:]} {err[-400:]}")
    heal_cmd = next((c for e, _, c in hook_commands() if e == "SessionStart" and "heal" in c), None)
    if not heal_cmd:
        check("hooks.json has the SessionStart heal hook", False, str(HOOKS_JSON))
        return
    hook_check("the heal hook (/bin/sh): exit 0 within its 10 s budget, prints nothing, spawns one heal", "/bin/sh",
               "SessionStart", "", heal_cmd, env, proj, "macos-ci-lifecycle")
    up = wait_until(lambda: cli(py, env, proj, "status", timeout=120)[0] == 0, 420, step=10)
    check("the heal hook revived Neo4j (status exits 0 again within 7 min)", up,
          (proj / ".deepmap" / "heal.log").read_text(encoding="utf-8", errors="replace")[-1500:]
          if (proj / ".deepmap" / "heal.log").exists() else "no .deepmap/heal.log")
    after, o = node_count(py, env, proj)
    check("the graph survived stop + heal (same node count)", before is not None and before == after,
          f"before={before} after={after} {o[-300:]}")
    wait_until(lambda: not heals_running(), 300, step=3)


def hooks_phase(env: dict, proj: Path) -> None:
    phase("6 hooks as hooks.json spells them")
    cmds = hook_commands()
    henv = dict(env, DEEPMAP_AUTO_SYNC="0")
    for shell in ("/bin/sh", "/bin/bash", "/bin/zsh"):
        if not os.path.exists(shell):
            skip(f"hooks under {shell}", "shell not installed")
            continue
        for i, (event, matcher, cmd) in enumerate(cmds):
            name = cmd.split("hooks/")[-1].split('"')[0]
            hook_check(f"{shell} {name}: exit 0, under 10 s, no traceback, stdout empty or one JSON object", shell,
                       event, matcher, cmd, henv, proj, f"macos-ci-{Path(shell).name}-{i}")
    if DARWIN:   # a Claude Code started from the Dock: no `python`, python3 = Xcode CLT 3.9
        denv = dict(henv, PATH="/usr/bin:/bin:/usr/sbin:/sbin")
        for i, (event, matcher, cmd) in enumerate(cmds):
            name = cmd.split("hooks/")[-1].split('"')[0]
            hook_check(f"Dock PATH (python3 = /usr/bin/python3) {name}: exit 0, under 10 s, no traceback, stdout empty "
                       "or one JSON object", "/bin/sh", event, matcher, cmd, denv, proj, f"macos-ci-dock-{i}", dock=True)
    else:
        skip("hooks under a Dock-launched PATH", "macOS only")
    wait_until(lambda: not heals_running(), 300, step=3)


def autosync_phase(py: str, env: dict, proj: Path) -> None:
    phase("7 auto-sync (session-start background refresh)")
    (proj / "shop" / "notify.py").write_text(CORE["shop/notify.py"] + '''

def send_shipping_notice(address, order_id):
    """Tell the customer their order has shipped."""
    return f"to={address} subject=Order {order_id} shipped"
''', encoding="utf-8", newline="\n")
    git(proj, "commit", "-q", "-am", "add shipping notice")
    aenv = dict(env, DEEPMAP_AUTO_SYNC="1")
    argv = [py, "-X", "utf8", str(BUILD / "deepmap_cli.py"), "sync", str(proj), "--unattended=session-start",
            "--session", "macos-ci"]
    base_nice = os.nice(0)   # what the child inherits; the runner adds 10 to it
    t0 = time.time()
    with open(LOG_DIR / "autosync.log", "w", encoding="utf-8") as fh:
        p = subprocess.Popen(argv, cwd=str(proj), env=aenv, stdout=fh, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL)
        nices = []
        while p.poll() is None and time.time() - t0 < 1800:
            if time.time() - t0 > 2:
                rc, out, _, _ = run(["ps", "-o", "ni=", "-p", str(p.pid)], timeout=15)
                if rc == 0 and out.strip().lstrip("-").isdigit():
                    nices.append(int(out.strip()))
            time.sleep(0.5)
        if p.poll() is None:
            p.kill()
        rc = p.wait()
    secs = round(time.time() - t0, 1)
    st = read_json(proj / ".deepmap" / "autosync.json") or {}
    lr = st.get("last_run") or {}
    info("autosync.json last_run", lr)
    try:
        product_log = (proj / ".deepmap" / "autosync.log").read_text(encoding="utf-8", errors="replace")[-1200:]
    except OSError:
        product_log = "(no .deepmap/autosync.log)"
    detail = (f"rc={rc} {secs}s outcome={lr.get('outcome')!r} why_code={lr.get('why_code')!r} "
              f"reason={lr.get('reason')!r} verdict={lr.get('verdict')!r} log: {product_log}")
    want = min(19, base_nice + 10)
    info("auto-sync niceness samples (baseline, samples)", (base_nice, nices[:20]))
    if len(nices) >= 3:
        check(f"the refresh runs at lowered CPU priority (nice {want}, baseline {base_nice}) while it works",
              max(nices) >= want, str(nices[:20]))
    else:
        skip("the refresh runs at lowered CPU priority", f"it finished in {secs} s, too fast to sample")
    if rc == 4 and lr.get("why_code"):   # a recorded, deliberate skip (low memory, a busy machine): not a defect
        skip("the session-start refresh synced the new commit", f"skipped by design: {detail}")
        skip("after the refresh, search finds the new function", "the refresh was skipped (see above)")
        info("available memory and the floor", probe(str(dm_home(env) / "venv" / "bin" / "python"), '''
        import auto_sync as A
        mem = A._memory()
        print(json.dumps({"memory": mem, "floor": A._mem_floor(mem[1])}, default=str))
        ''', env=env)[0])
        if DARWIN:
            info("vm_stat", run(["vm_stat"], timeout=30)[1][:800])
        return
    check("the session-start refresh synced the new commit (exit 0, last_run.outcome 'synced')",
          rc == 0 and lr.get("outcome") == "synced", detail)
    rc, out, err, _ = cli(py, env, proj, "search", "tell the customer the order shipped")
    check("after the refresh, search finds the new function", rc == 0 and "send_shipping_notice" in out,
          out[-800:] + err[-400:])


def cleanup(py: str, env: dict, proj: Path, ready: bool) -> None:
    phase("8 cleanup (DeepMap's own verbs)")
    if not ready:   # setup never brought an instance up: stop has nothing of this project's to stop
        for verb in ("mcp-stop", "stop"):
            rc, out, err, _ = run([py, "-X", "utf8", str(BUILD / "provision.py"), verb, str(proj)], cwd=proj, env=env,
                                  timeout=300, log="cleanup.log")
            info(f"{verb} after a failed setup (rc)", f"rc={rc} {out[-300:]} {err[-300:]}")
        return
    pids = [int(r["pid"]) for _, r in owners(env)]
    rc, out, err, _ = run([py, "-X", "utf8", str(BUILD / "provision.py"), "mcp-stop", str(proj)], cwd=proj, env=env,
                          timeout=300, log="cleanup.log")
    check("mcp-stop exits 0", rc == 0, out + err)
    running = {p for p, _ in ps_table()}
    alive = [pid for pid in pids if pid in running]
    check("the MCP servers are gone after mcp-stop", not alive, f"still running: {alive}")
    rc, out, err, _ = run([py, "-X", "utf8", str(BUILD / "provision.py"), "stop", str(proj)], cwd=proj, env=env,
                          timeout=300, log="cleanup.log")
    check("stop exits 0", rc == 0, out + err)


# ------------------------------------------------------------------------------------------------------------------- #
def write_report(started: float) -> int:
    fails = [r for r in RESULTS if r["status"] == "fail"]
    predicted = [r for r in fails if r["predicted"]]
    surprises = [r for r in fails if not r["predicted"]]
    passes = [r for r in RESULTS if r["status"] == "pass"]
    skips = [r for r in RESULTS if r["status"] == "skip"]
    infos = [r for r in RESULTS if r["status"] == "info"]
    (REPORT_DIR / "report.json").write_text(json.dumps(RESULTS, indent=1, default=str), encoding="utf-8")

    def esc(s):
        return str(s).replace("|", "\\|").replace("\n", " ")
    md = [f"## DeepMap live check on {platform.platform()} ({platform.machine()}), Python {sys.version.split()[0]}",
          "", f"{len(passes)} passed, {len(fails)} failed ({len(predicted)} predicted by the code audit, "
              f"{len(surprises)} not predicted), {len(skips)} skipped; {round((time.time() - started) / 60, 1)} min.",
          ""]
    for title, rows in (("Failed, not predicted", surprises), ("Failed, predicted by the code audit", predicted)):
        if rows:
            md += [f"### {title}", "", "| Phase | Check | Finding | Detail |", "|---|---|---|---|"]
            md += [f"| {esc(r['phase'])} | {esc(r['id'])} | {esc(r['predicted'] or '')} | {esc(r['detail'][:400])} |"
                   for r in rows]
            md.append("")
    md += ["### Passed", ""] + [f"- {esc(r['phase'])}: {esc(r['id'])}" for r in passes] + [""]
    if skips:
        md += ["### Skipped", ""] + [f"- {esc(r['id'])}: {esc(r['detail'])}" for r in skips] + [""]
    md += ["<details><summary>Machine facts and recorded values</summary>", ""]
    md += [f"- **{esc(r['id'])}**: {esc(r['detail'][:600])}" for r in infos] + ["", "</details>", ""]
    text = "\n".join(md)
    (REPORT_DIR / "report.md").write_text(text, encoding="utf-8")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        try:
            with open(summary, "a", encoding="utf-8") as fh:
                fh.write(text + "\n")
        except OSError:
            pass
    print("\n" + text)
    return 1 if fails else 0


def main(argv=None) -> int:
    global REPORT_DIR, LOG_DIR
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--report", default=None, help="directory for report.md, report.json and logs/")
    args = ap.parse_args(argv)
    if os.environ.get("DEEPMAP_LIVE_TEST") != "1" or os.environ.get("CI", "").lower() != "true":
        print("[SKIP] live macOS end-to-end check: needs DEEPMAP_LIVE_TEST=1 and CI=true (it uses the machine's default "
              "DEEPMAP_HOME and the base Neo4j ports; run it on a throwaway runner only)")
        return 0
    REPORT_DIR = Path(args.report or tempfile.mkdtemp(prefix="deepmap-live-report-"))
    LOG_DIR = REPORT_DIR / "logs"
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    started = time.time()
    py = sys.executable
    env = dict(os.environ)
    env.setdefault("NEO4J_PASSWORD", "deepmap")
    env.setdefault("DEEPMAP_EXTRACTOR", "graphify")     # never let `auto` fall back to ast silently
    env["DEEPMAP_AUTO_SYNC"] = "0"                       # only phase 7 turns it on
    env["PYTHONUTF8"] = "1"
    scratch = Path(tempfile.mkdtemp(prefix="dmlive-"))   # under $TMPDIR: /var/folders/... on macOS
    proj = scratch / "ShopProject"
    proj.mkdir()
    env["CLAUDE_PLUGIN_ROOT"] = str(ROOT)                # what Claude Code sets for the hooks and commands
    env["CLAUDE_PROJECT_DIR"] = str(proj)

    ready = False
    try:
        f = guarded("0 facts", facts, py) or {}
        guarded("1 pre-setup", pre_setup, py, env, scratch)
        guarded("the test project", make_project, proj)
        try:
            ready = bool(guarded("2 setup", setup_phase, py, env, proj))
            if ready:
                guarded("3 tools", tools_phase, py, env, proj)
                guarded("4 path spellings", spellings_phase, py, env, proj, bool(f.get("fs_case_insensitive")),
                        scratch)
                guarded("5 lifecycle", lifecycle_phase, py, env, proj)
                guarded("6 hooks", hooks_phase, env, proj)
                guarded("7 auto-sync", autosync_phase, py, env, proj)
            else:
                phase("3-7 skipped")
                skip("tools, spellings, lifecycle, hooks, auto-sync", "setup did not exit 0 (see logs/setup.log)")
        finally:
            guarded("8 cleanup", cleanup, py, env, proj, ready)
            guarded("artifacts", copy_artifacts, env, proj)
    finally:
        rc = write_report(started)
    return rc


def copy_artifacts(env: dict, proj: Path) -> None:
    """The project's .deepmap/ state, DeepMap's run/ records and its logs into the report (no lock, token, key files)."""
    for src in (proj / ".deepmap", dm_home(env) / "run"):
        if src.is_dir():
            dst = REPORT_DIR / ("project-deepmap" if src.name == ".deepmap" else "deepmap-home-run")
            try:
                shutil.copytree(src, dst, dirs_exist_ok=True,
                                ignore=shutil.ignore_patterns("*.lock", "*token*", "*secret*", "*auth*", "*.key"))
            except (shutil.Error, OSError):
                pass
    home = dm_home(env)   # venv-setup.log, neo4j-console.log, Neo4j's own logs
    for logf in list(home.glob("*.log")) + list(home.glob("instances/*/*.log")) + \
            list(home.glob("neo4j/neo4j-community-*/logs/*.log")):
        try:
            shutil.copy2(logf, LOG_DIR / f"home-{logf.parent.name}-{logf.name}")
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
