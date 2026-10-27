#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ROUND-11 W1 gates (R3): every hooks.json command parses AND runs, exactly as spelled, under every shell Claude Code
may hand it to, and each hook does its work EXACTLY ONCE per event whichever interpreter names the machine has.

Claude Code runs a hook command with bash (POSIX; Git Bash on Windows when it finds it) or, on Windows without Git
Bash, PowerShell (pwsh, else the stock Windows PowerShell 5.1). Round 10's `python … || python3 …` does not parse in
5.1 (`||` is PowerShell 7 syntax): every DeepMap hook failed there before running — no self-heal after a reboot, a hook
error on every Grep / Glob / Edit. Each command is now

    python -X utf8 "${CLAUDE_PLUGIN_ROOT}/hooks/<hook>.py" ; python3 -X utf8 "${CLAUDE_PLUGIN_ROOT}/hooks/<hook>.py" ; exit 0

`;` separates statements in sh, bash, dash, Windows PowerShell 5.1 and PowerShell 7; `exit 0` means a missing
interpreter name is only a line on stderr, never a hook error; cmd.exe (never Claude Code's hook shell) hands the rest
of the line to `python` as ignored arguments. Exactly one interpreter does the work: the two share the shell's stdin,
so the first that runs the hook reads the whole payload and the second finds it drained and exits at once — before
any import or side effect. A `python` that can't run the hook (missing, Python 2, too old) never reads stdin, so
python3 gets the payload.

  A  shape: every command is exactly that form for ONE hook; the hooks stay on their events / matchers /
     timeouts; nothing any of the shells would read differently (||, &&, |, redirection, $(), backticks).
  B  parse: each command parses under the REAL Windows PowerShell 5.1 and PowerShell 7 parsers and under sh / bash /
     dash -n. Control: round 10's `||` form is rejected by the 5.1 parser (the gate sees that defect).
  C  run, for real: every shell present (sh, bash, dash, cmd.exe, powershell.exe 5.1, pwsh) x launchers on a controlled
     PATH {python and python3, python only, python3 only, a `python` that exits without reading stdin (Python 2 /
     unknown -X) plus python3, a python plus a `python3` that fails likewise (the Microsoft Store alias stub)}: each
     of the REAL hooks (in a temp plugin root whose path holds a space, with a stub provision.py) does its work
     exactly once — heal launches provision ONCE for the payload's project via the
     expected interpreter; the nudge and sync-flag session counters read 1; stdout is at most ONE JSON document — and
     the shell exits 0. cmd.exe tries only `python` (it has no `;` separator): python3-only / a broken python run
     nothing there, and say nothing.
  D  the second interpreter: each hook run as hooks.json runs it but with an EMPTY or whitespace-only stdin prints
     nothing, starts nothing, writes no state and exits 0 — the heal hook used to fall back to the working directory
     and start a SECOND heal — and it exits before loading any hook code.
  E  the host's own interpreters (no shims): under every shell present each hook still runs exactly once (on a
     python3-only Linux box this is the real python3-only proof); on Windows also a real venv python.exe alone under
     Windows PowerShell 5.1 and sh.

Hermetic: temp plugin root / projects / DEEPMAP_HOME per run, stub provision; no Neo4j, no sockets opened by the gate,
no network; never touches ~/.deepmap, ~/.claude or the committed .mcp.json. Exit 0 = GREEN.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# Auto-sync (DeepMap's background refresh at session start) is on by default in an attended session; this gate
# locks the hooks WITHOUT it, so it is pinned off for this process and every hook process the gate starts
# (tests/auto_sync_hooks_gates.py locks the hooks with it on).
os.environ["DEEPMAP_AUTO_SYNC"] = "0"
REPO = Path(os.environ.get("DMGATE_ROOT") or Path(__file__).resolve().parent.parent)
HOOKS = REPO / "hooks"
BUILD = REPO / "build"
PY = sys.executable
_WIN = os.name == "nt"

_PASS = 0
_FAIL = 0

# Knobs a runner might have set; every run sets exactly what it needs.
_SCRUB = ("PYTHONUTF8", "PYTHONIOENCODING", "PYTHONHOME", "PYTHONPATH", "DEEPMAP_HOOKS", "DEEPMAP_HEAL",
          "DEEPMAP_NUDGE", "DEEPMAP_SYNC_FLAG", "DEEPMAP_SYNC_FLAG_EVERY", "DEEPMAP_ANNOUNCE", "DEEPMAP_ONBOARD",
          "DEEPMAP_HIBERNATE", "DEEPMAP_RETRIEVAL_LOG", "CLAUDE_PROJECT_DIR", "DEEPMAP_PROJECT_ROOT", "NEO4J_URI",
          "DEEPMAP_LEARNING", "CLAUDE_PLUGIN_ROOT", "DEEPMAP_NOTIFY_HEAL_WAIT", "DEEPMAP_PYTHON", "DEEPMAP_HOME")

HOOK_NAMES = ("deepmap_sessionstart_heal", "deepmap_sessionstart_notify", "deepmap_presearch_nudge",
              "deepmap_postedit_syncflag", "deepmap_toolresult_freshness")
_WANT_EVENTS = {("SessionStart", None): {"deepmap_sessionstart_heal", "deepmap_sessionstart_notify"},
                ("PreToolUse", "Grep|Glob"): {"deepmap_presearch_nudge"},
                ("PostToolUse", "Edit|Write|MultiEdit|NotebookEdit"): {"deepmap_postedit_syncflag"},
                # auto-sync: the follow-up notes after a deepmap tool call (search / impact / cypher)
                ("PostToolUse", "mcp__.*deepmap.*__deepmap_(search|impact|cypher)"): {"deepmap_toolresult_freshness"}}
_COUNT_FILES = {"deepmap_presearch_nudge": "presearch_nudge_state.json",
                "deepmap_postedit_syncflag": "postedit_sync_state.json"}
_R10_FORM = ('python -X utf8 "${CLAUDE_PLUGIN_ROOT}/hooks/deepmap_presearch_nudge.py" || '
             'python3 -X utf8 "${CLAUDE_PLUGIN_ROOT}/hooks/deepmap_presearch_nudge.py"')
_SHAPE = re.compile(r'^python -X utf8 "\$\{CLAUDE_PLUGIN_ROOT\}/hooks/([a-z_]+)\.py" ; '
                    r'python3 -X utf8 "\$\{CLAUDE_PLUGIN_ROOT\}/hooks/([a-z_]+)\.py" ; exit 0$')
_HEAL_WAIT_S = 45.0     # a detached stub on a loaded Windows box can take several seconds to start
_HEAL_GRACE_S = 2.5     # after the first heal line: long enough for a SECOND launch to show up


def check(label: str, cond: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {label}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}{(' — ' + str(detail)[:900]) if detail else ''}")


def _hooks_json() -> dict:
    return json.loads((HOOKS / "hooks.json").read_text(encoding="utf-8"))


def _entries() -> list:
    """(event, matcher, command, timeout) for every registered hook command."""
    out = []
    for event, groups in (_hooks_json().get("hooks") or {}).items():
        for g in groups or []:
            for h in (g or {}).get("hooks") or []:
                out.append((event, g.get("matcher"), h.get("command", ""), h.get("timeout")))
    return out


def _hook_of(cmd: str):
    m = _SHAPE.match(cmd)
    return m.group(1) if m and m.group(1) == m.group(2) else None


def _hook_in(cmd: str):
    """The hook a command runs, whatever its shape (so a RED shape still goes through the run sections)."""
    m = re.search(r"/hooks/([a-z_]+)\.py", cmd)
    return m.group(1) if m else None


# =========================================================================== shells
def _ps51():
    if not _WIN:
        return None
    p = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    return str(p) if p.is_file() else None


def _shells() -> list:
    """(label, kind, exe) for every hook-capable shell on this machine."""
    out = []
    for name in ("sh", "bash", "dash"):
        exe = shutil.which(name) or (f"/bin/{name}" if Path(f"/bin/{name}").exists() else None)
        if exe:
            out.append((name, "posix", exe))
    if _WIN and os.environ.get("COMSPEC"):
        out.append(("cmd.exe", "cmd", os.environ["COMSPEC"]))
    if _ps51():
        out.append(("powershell.exe (5.1)", "ps", _ps51()))
    pw = shutil.which("pwsh")
    if pw:
        out.append(("pwsh (7)", "ps", pw))
    return out


def _argv(kind: str, exe: str, line: str):
    if kind == "posix":
        return [exe, "-c", line]
    if kind == "cmd":   # cmd /s /c "<line>": the outer quotes go, the line runs verbatim (a list would re-quote it)
        return f'"{exe}" /d /s /c "{line}"'
    return [exe, "-NoProfile", "-NonInteractive", "-Command", line]


def _line(cmd: str, plugin: Path, kind: str) -> str:
    """The command as Claude Code hands it to the shell: ${CLAUDE_PLUGIN_ROOT} substituted (native separators for the
    Windows shells, forward slashes for sh)."""
    root = str(plugin) if kind in ("cmd", "ps") else plugin.as_posix()
    return cmd.replace("${CLAUDE_PLUGIN_ROOT}", root)


# =========================================================================== fixtures
_STUB_PROVISION = r'''import json, os, sys
with open(os.environ["DMR11_HEAL_LOG"], "a", encoding="utf-8") as f:
    f.write(json.dumps({"argv": sys.argv[1:], "via": os.environ.get("DMR11_VIA")}) + "\n")
'''


def _plugin(parent: Path) -> Path:
    """A temp plugin root (its path holds a SPACE, like C:\\Users\\John Doe\\.claude\\plugins\\...) with the REAL hooks,
    the real port_registry + plugin.json, and a provision.py stub that records each heal launch."""
    pr = parent / "plug in" / "deep-map"
    (pr / "hooks").mkdir(parents=True)
    (pr / "build").mkdir()
    (pr / ".claude-plugin").mkdir()
    for f in HOOKS.glob("*.py"):
        shutil.copyfile(f, pr / "hooks" / f.name)
    shutil.copyfile(HOOKS / "hooks.json", pr / "hooks" / "hooks.json")
    shutil.copyfile(BUILD / "port_registry.py", pr / "build" / "port_registry.py")
    pj = REPO / ".claude-plugin" / "plugin.json"
    if pj.is_file():
        shutil.copyfile(pj, pr / ".claude-plugin" / "plugin.json")
    (pr / "build" / "provision.py").write_text(_STUB_PROVISION, encoding="utf-8")
    return pr


def _project(parent: Path, name: str) -> Path:
    root = parent / name
    (root / ".deepmap").mkdir(parents=True)
    (root / ".deepmap" / "graph_build.json").write_text(
        json.dumps({"nodes": 42, "extractor": "graphify", "verdict": "READY"}), encoding="utf-8")
    (root / "app.py").write_text("def main():\n    return 1\n", encoding="utf-8")
    return root.resolve()


def _payload(hook: str, root: Path, sid: str) -> dict:
    base = {"session_id": sid, "transcript_path": str(root / "t.jsonl"), "cwd": str(root)}
    if hook in ("deepmap_sessionstart_heal", "deepmap_sessionstart_notify"):
        return dict(base, hook_event_name="SessionStart", source="startup")
    if hook == "deepmap_presearch_nudge":
        return dict(base, permission_mode="default", hook_event_name="PreToolUse", tool_name="Grep",
                    tool_input={"pattern": "def main", "path": str(root)}, tool_use_id=f"toolu_{sid}")
    if hook == "deepmap_toolresult_freshness":
        return dict(base, permission_mode="default", hook_event_name="PostToolUse",
                    tool_name="mcp__deepmap-search__deepmap_search", tool_input={"query": "where is main"},
                    tool_response=[{"type": "text", "text": "[]"}], tool_use_id=f"toolu_{sid}")
    return dict(base, permission_mode="default", hook_event_name="PostToolUse", tool_name="Edit",
                tool_input={"file_path": str(root / "app.py"), "old_string": "1", "new_string": "2"},
                tool_response={"filePath": str(root / "app.py"), "success": True}, tool_use_id=f"toolu_{sid}")


def _shim_dir(d: Path, variant: str) -> Path:
    """Launchers named python / python3 that log their name, export DMR11_VIA=<name> and run the real interpreter —
    or, for `broken`, a `python` that fails WITHOUT reading stdin (Python 2's "Unknown option: -X")."""
    d.mkdir(parents=True, exist_ok=True)
    names = {"both": ("python", "python3"), "python only": ("python",), "python3 only": ("python3",),
             "broken python + python3": ("python!", "python3"),
             "python + broken python3": ("python", "python3!")}[variant]
    pyf = PY.replace("\\", "/")
    for n in names:
        broken = n.endswith("!")
        n = n.rstrip("!")
        tag = n + ("-broken" if broken else "")
        body = (f'#!/bin/sh\necho "{tag}" >> "$DMR11_SHIM_LOG"\n'
                + ('echo "Unknown option: -X" >&2\nexit 2\n' if broken else
                   f'DMR11_VIA={n}; export DMR11_VIA\nexec "{pyf}" "$@"\n'))
        (d / n).write_text(body, encoding="utf-8", newline="\n")
        os.chmod(d / n, 0o755)
        if _WIN:
            (d / f"{n}.cmd").write_text(
                f'@echo {tag}>>"%DMR11_SHIM_LOG%"\r\n'
                + ('@echo Unknown option: -X 1>&2\r\n@exit /b 2\r\n' if broken else
                   f'@set DMR11_VIA={n}\r\n@"{PY}" %*\r\n'), encoding="utf-8")
    return d


def _env(home: Path, plugin: Path, extra: dict) -> dict:
    env = {k: v for k, v in os.environ.items() if k.upper() not in _SCRUB}
    env["DEEPMAP_AUTO_SYNC"] = "0"   # this gate locks how each hook RUNS, never DeepMap's background refresh
    env["DEEPMAP_HOME"] = str(home)
    env["CLAUDE_PLUGIN_ROOT"] = str(plugin)
    env.update(extra)
    return env


def _json_docs(out: str):
    """The JSON documents on a hook's stdout: [] when empty, None when it isn't JSON (e.g. two documents glued)."""
    s = out.strip()
    if not s:
        return []
    try:
        return [json.loads(s)]
    except Exception:
        return None


def _heal_lines(log: Path, *, wait: bool) -> list:
    deadline = time.monotonic() + (_HEAL_WAIT_S if wait else 0)
    while True:
        lines = [ln for ln in (log.read_text(encoding="utf-8").splitlines() if log.exists() else []) if ln.strip()]
        if lines or time.monotonic() >= deadline:
            break
        time.sleep(0.2)
    if lines:
        time.sleep(_HEAL_GRACE_S)
        lines = [ln for ln in log.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return [json.loads(ln) for ln in lines]


def _count(root: Path, hook: str, sid: str):
    fn = _COUNT_FILES.get(hook)
    if not fn:
        return None
    try:
        return json.loads((root / ".deepmap" / fn).read_text(encoding="utf-8"))["counts"].get(sid, 0)
    except Exception:
        return 0


# =========================================================================== A: shape
def gate_a_shape() -> None:
    print("\n-- A: every hooks.json command is `python … ; python3 … ; exit 0` for ONE hook --")
    ents = _entries()
    cmds = [e[2] for e in ents]
    bad = [c for c in cmds if _hook_of(c) is None]
    check("every command: python -X utf8 <hook> ; python3 -X utf8 <the same hook> ; exit 0", cmds and bad == [], bad)
    got = {}
    for event, matcher, cmd, _t in ents:
        got.setdefault((event, matcher), set()).add(_hook_of(cmd) or cmd)
    check("every hook keeps its event and matcher", got == _WANT_EVENTS, got)
    check("each hook is registered exactly once (one command per event, not one per interpreter)",
          sorted(_hook_of(c) for c in cmds if _hook_of(c)) == sorted(HOOK_NAMES), cmds)
    check("every hook keeps its 10 s timeout", all(e[3] == 10 for e in ents), [e[3] for e in ents])
    odd = [c for c in cmds if re.search(r"\|\||&&|\||[<>]|\$\(|`", c)]
    check("nothing a shell reads differently: no ||, &&, pipes, redirection, $(), backticks", odd == [], odd)
    for h in HOOK_NAMES:
        check(f"hooks/{h}.py exists", (HOOKS / f"{h}.py").is_file())


# =========================================================================== B: parse
_PS_PARSE = ("$e = $null; $t = $null; "
             "[void][System.Management.Automation.Language.Parser]::ParseInput($env:DMR11_CMD, [ref]$t, [ref]$e); "
             "Write-Output ('ERRORS=' + $e.Count); foreach ($x in $e) { Write-Output $x.Message }")


def _ps_errors(exe: str, text: str):
    p = subprocess.run([exe, "-NoProfile", "-NonInteractive", "-Command", _PS_PARSE], capture_output=True,
                       env=dict(os.environ, DMR11_CMD=text), timeout=180)
    out = p.stdout.decode("utf-8", "replace")
    m = re.search(r"ERRORS=(\d+)", out)
    return (int(m.group(1)) if m else None), out.strip()[-400:]


def gate_b_parse() -> None:
    print("\n-- B: each command parses under the real PowerShell 5.1 / 7 parsers and sh / bash / dash -n --")
    cmds = [e[2] for e in _entries()]
    fake = Path(tempfile.gettempdir()) / "plug in" / "deep-map"
    tmp = Path(tempfile.mkdtemp(prefix="dmr11b")).resolve()
    try:
        for label, kind, exe in _shells():
            if kind == "cmd":
                continue   # cmd.exe has no parse-only mode: section C runs every command under it
            if kind == "posix":
                # `-n` from a FILE: dash ignores -n together with -c (it runs the command)
                bad = tmp / "bad.sh"
                bad.write_text("echo x ; if then\n", encoding="utf-8", newline="\n")
                p = subprocess.run([exe, "-n", str(bad)], capture_output=True, timeout=60)
                check(f"(control: {label} -n rejects a syntax error)", p.returncode != 0, p.returncode)
            for i, c in enumerate(cmds):
                line = _line(c, fake, kind)
                if kind == "ps":
                    n, detail = _ps_errors(exe, line)
                    check(f"{label} parses `{_hook_in(c) or c[:40]}` with 0 errors", n == 0, detail)
                else:
                    f = tmp / f"cmd{i}.sh"
                    f.write_text(line + "\n", encoding="utf-8", newline="\n")
                    p = subprocess.run([exe, "-n", str(f)], capture_output=True, timeout=60)
                    check(f"{label} -n accepts `{_hook_in(c) or c[:40]}`", p.returncode == 0,
                          p.stderr.decode("utf-8", "replace")[-300:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    ps = _ps51()
    if ps:
        n, detail = _ps_errors(ps, _line(_R10_FORM, fake, "ps"))
        check("control: Windows PowerShell 5.1 REJECTS round 10's `python … || python3 …` (the defect is visible)",
              n is not None and n > 0, detail)
    else:
        check("(Windows PowerShell 5.1 present)", True, "skipped — not Windows")


# =========================================================================== C: run matrix
# "python + broken python3" is the commonest Windows layout: a python.org `python` and the Microsoft Store's `python3`
# alias stub, which prints "Python was not found" and exits 9009 without reading stdin.
_VARIANTS = ("both", "python only", "python3 only", "broken python + python3", "python + broken python3")


def _want(kind: str, variant: str):
    """(launchers that start, in order; the interpreter that does the work or None)."""
    if kind == "cmd":   # cmd.exe: `;` is no separator — only `python` is tried, with the rest as its arguments
        return {"both": (["python"], "python"), "python only": (["python"], "python"),
                "python3 only": ([], None), "broken python + python3": (["python-broken"], None),
                "python + broken python3": (["python"], "python")}[variant]
    return {"both": (["python", "python3"], "python"), "python only": (["python"], "python"),
            "python3 only": (["python3"], "python3"),
            "broken python + python3": (["python-broken", "python3"], "python3"),
            "python + broken python3": (["python", "python3-broken"], "python")}[variant]


def _run_one(base: Path, plugin: Path, cmd: str, hook: str, shell, variant, bindir, tag: str, *, path=None,
             via_expected="?", launchers_expected=None):
    label, kind, exe = shell
    work = base / tag
    work.mkdir(parents=True)
    root = _project(work, "proj")
    home = work / "home"
    home.mkdir()
    heal_log, shim_log = work / "heal.log", work / "shim.log"
    shim_log.write_text("", encoding="utf-8")
    sid = f"s-{tag}"
    if path is None:
        path = str(bindir) if kind == "posix" else (
            str(bindir) + os.pathsep + str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"))
    env = _env(home, plugin, {"PATH": path, "DMR11_HEAL_LOG": str(heal_log), "DMR11_SHIM_LOG": str(shim_log)})
    data = json.dumps(_payload(hook, root, sid), ensure_ascii=False).encode("utf-8")
    try:
        p = subprocess.run(_argv(kind, exe, _line(cmd, plugin, kind)), input=data, capture_output=True, env=env,
                           cwd=str(root), timeout=240)
        rc, out, err = p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        rc, out, err = "timeout", "", ""
    launched = [ln.strip() for ln in shim_log.read_text(encoding="utf-8", errors="replace").splitlines()
                if ln.strip()]
    res = {"rc": rc, "stdout": out.strip()[-300:], "stderr": err.strip()[-300:], "launched": launched}
    if hook == "deepmap_sessionstart_heal":
        lines = _heal_lines(heal_log, wait=via_expected is not None)
        res["heal"] = lines
    else:
        res["count"] = _count(root, hook, sid)
        res["docs"] = _json_docs(out)
    res["root"] = str(root)
    return res


def _judge(hook: str, res: dict, via, launchers, kind: str) -> list:
    """Why a run broke the contract ([] = it held)."""
    why = []
    if kind != "cmd" or via is not None:
        if res["rc"] != 0:
            why.append(f"exit {res['rc']}")
    if launchers is not None and res["launched"] != launchers:
        why.append(f"launchers {res['launched']} != {launchers}")
    if hook == "deepmap_sessionstart_heal":
        heal = res.get("heal") or []
        if via is None:
            if heal:
                why.append(f"heal launched {len(heal)}x where nothing should run")
        elif len(heal) != 1:
            why.append(f"heal launched {len(heal)}x (want exactly 1)")
        else:
            h = heal[0]
            if h.get("argv", [None])[0] != "heal" or Path(h["argv"][1]).resolve() != Path(res["root"]):
                why.append(f"heal argv {h.get('argv')} (want heal {res['root']})")
            if via != "?" and h.get("via") != via:
                why.append(f"heal ran via {h.get('via')} (want {via})")
    else:
        if res.get("docs") is None:
            why.append(f"stdout is not ONE JSON document: {res['stdout']!r}")
        elif len(res["docs"]) > 1:
            why.append("more than one JSON document")
        if hook in _COUNT_FILES:
            want = 0 if via is None else 1
            if res.get("count") != want:
                why.append(f"session counter {res.get('count')} (want {want})")
        if hook == "deepmap_sessionstart_notify" and via is not None and res.get("docs") == []:
            why.append("notify printed nothing on a READY project")
        if via is None and res.get("docs"):
            why.append("output where nothing should run")
    return why


def gate_c_matrix() -> None:
    print("\n-- C: every shell x launcher set x REAL hook: the work is done exactly once, exit 0 --")
    cmds = {(_hook_in(c) or ""): c for _e, _m, c, _t in _entries()}
    if not all(h in cmds for h in HOOK_NAMES):
        check("(hooks.json registers every hook, so the matrix can run)", False, list(cmds))
        return
    base = Path(tempfile.mkdtemp(prefix="dmr11c")).resolve()
    try:
        plugin = _plugin(base)
        jobs = []
        n = 0
        for shell in _shells():
            for variant in _VARIANTS:
                bindir = _shim_dir(base / "bin" / f"{shell[0].split()[0]}-{variant.replace(' ', '_')}", variant)
                launchers, via = _want(shell[1], variant)
                for hook in HOOK_NAMES:
                    n += 1
                    jobs.append((shell, variant, hook, launchers, via, bindir, f"r{n}"))
        with ThreadPoolExecutor(max_workers=6) as ex:
            futs = [(j, ex.submit(_run_one, base, plugin, cmds[j[2]], j[2], j[0], j[1], j[5], j[6],
                                  via_expected=j[4])) for j in jobs]
            results = [(j, f.result()) for j, f in futs]
        for (shell, variant, hook, launchers, via, _b, _t), res in results:
            why = _judge(hook, res, via, launchers, shell[1])
            what = (f"runs ONCE via {via}" if via else "runs nothing, quietly")
            check(f"{shell[0]}, {variant}: {hook.replace('deepmap_', '')} {what}", why == [],
                  f"{why} | {res}")
    finally:
        shutil.rmtree(base, ignore_errors=True)


# =========================================================================== D: the second interpreter
def gate_d_empty_stdin() -> None:
    print("\n-- D: an EMPTY stdin (the second interpreter of the chain) does nothing and loads no hook code --")
    base = Path(tempfile.mkdtemp(prefix="dmr11d")).resolve()
    try:
        plugin = _plugin(base)
        for hook in HOOK_NAMES:
            for label, data in (("empty", b""), ("whitespace-only", b"  \r\n")):
                work = base / f"{hook}-{label}"
                work.mkdir()
                root = _project(work, "proj")
                home = work / "home"
                home.mkdir()
                heal_log = work / "heal.log"
                env = _env(home, plugin, {"DMR11_HEAL_LOG": str(heal_log), "CLAUDE_PROJECT_DIR": str(root)})
                p = subprocess.run([PY, "-X", "utf8", str(plugin / "hooks" / f"{hook}.py")], input=data,
                                   capture_output=True, env=env, cwd=str(root), timeout=120)
                out = p.stdout.decode("utf-8", "replace").strip()
                state = sorted(f.name for f in (root / ".deepmap").iterdir() if f.name != "graph_build.json")
                heal = _heal_lines(heal_log, wait=False)
                if hook == "deepmap_sessionstart_heal":
                    time.sleep(_HEAL_GRACE_S)   # a launch, if any, has had time to write
                    heal = _heal_lines(heal_log, wait=False)
                check(f"{hook.replace('deepmap_', '')}, {label} stdin: exit 0, no output, no heal launch, no state",
                      p.returncode == 0 and out == "" and heal == [] and state == [],
                      f"rc={p.returncode} out={out!r} heal={heal} state={state} "
                      f"err={p.stderr.decode('utf-8', 'replace')[-300:]!r}")
            p = subprocess.run([PY, "-X", "utf8", "-X", "importtime", str(plugin / "hooks" / f"{hook}.py")],
                               input=b"", capture_output=True, env=_env(base, plugin, {}), timeout=120)
            err = p.stderr.decode("utf-8", "replace")
            check(f"{hook.replace('deepmap_', '')}, empty stdin: exits before loading deepmap_hook_state",
                  p.returncode == 0 and "deepmap_hook_state" not in err, err[-400:])
    finally:
        shutil.rmtree(base, ignore_errors=True)


# =========================================================================== E: real interpreters
def gate_e_real_interpreters() -> None:
    print("\n-- E: the host's OWN interpreters (no shims) — each hook still runs exactly once --")
    cmds = {(_hook_in(c) or ""): c for _e, _m, c, _t in _entries()}
    if not all(h in cmds for h in HOOK_NAMES):
        check("(hooks.json registers every hook, so the real runs can happen)", False, list(cmds))
        return
    has = [n for n in ("python", "python3") if shutil.which(n)]
    check("(this host has a python or python3 on PATH)", bool(has), os.environ.get("PATH", "")[:200])
    base = Path(tempfile.mkdtemp(prefix="dmr11e")).resolve()
    try:
        plugin = _plugin(base)
        rows = []
        for shell in _shells():
            if shell[1] == "cmd" and "python" not in has:
                continue   # cmd.exe tries only `python`
            rows.append((shell, "the host's PATH", os.environ.get("PATH", "")))
        venv_scripts = None
        if _WIN:
            v = base / "venv"
            p = subprocess.run([PY, "-m", "venv", "--without-pip", str(v)], capture_output=True, timeout=300)
            if p.returncode == 0 and (v / "Scripts" / "python.exe").is_file():
                venv_scripts = v / "Scripts"
            check("(a real venv python.exe for the python-only Windows row)", venv_scripts is not None,
                  p.stderr.decode("utf-8", "replace")[-300:])
        if venv_scripts is not None:
            sysdir = str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32")
            for shell in _shells():
                if shell[0] in ("powershell.exe (5.1)", "sh"):
                    rows.append((shell, "a real venv python.exe only", str(venv_scripts) + os.pathsep + sysdir))
        jobs = []
        for i, (shell, label, path) in enumerate(rows):
            for j, hook in enumerate(HOOK_NAMES):
                jobs.append((shell, label, path, hook, f"e{i}-{j}"))
        with ThreadPoolExecutor(max_workers=4) as ex:
            futs = [(j, ex.submit(_run_one, base, plugin, cmds[j[3]], j[3], j[0], j[1], None, j[4], path=j[2],
                                  via_expected="?")) for j in jobs]
            results = [(j, f.result()) for j, f in futs]
        for (shell, label, _p, hook, _t), res in results:
            why = _judge(hook, res, "?", None, shell[1])
            check(f"{shell[0]}, {label}: {hook.replace('deepmap_', '')} runs ONCE, exit 0", why == [],
                  f"{why} | {res}")
    finally:
        shutil.rmtree(base, ignore_errors=True)


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 72)
    print("DeepMap — round-11 W1 (R3): hooks.json parses + runs under sh / bash / dash / cmd / PowerShell 5.1 + 7,")
    print("each hook exactly once, python-only / python3-only / both")
    print(f"(repo under test: {REPO}; shells: {', '.join(s[0] for s in _shells())})")
    print("=" * 72)
    for g in (gate_a_shape, gate_b_parse, gate_c_matrix, gate_d_empty_stdin, gate_e_real_interpreters):
        try:
            g()
        except Exception as exc:  # noqa: BLE001 — a crashing section is a RED check, never a crash of the suite
            import traceback
            check(f"{g.__name__} ran without raising", False, "".join(traceback.format_exception(exc))[-900:])
    print("\n" + "=" * 72)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
