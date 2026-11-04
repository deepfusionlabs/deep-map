#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Install sanity: DeepMap from nothing, the way a user gets it, then proof that it works (a GitHub-hosted runner).

Every check runs what is INSTALLED, never a copy: the plugin comes from `claude plugin marketplace add
deepfusionlabs/deep-map#<ref>` + `plugin install`, and the harness then runs the installed commands/setup.md and
commands/doctor.md bash blocks (read as bytes, ${CLAUDE_PLUGIN_ROOT} and $ARGUMENTS substituted as Claude Code does),
the installed hooks.json command lines and the installed tests/mcp_http_smoketest.py. Every Claude Code session is an
UNAUTHENTICATED `claude -p ... --output-format stream-json --include-hook-events`: it loads the plugin, runs the
SessionStart hooks and connects the project's .mcp.json servers, then ends "Not logged in" at $0. Each one must show
cost 0 and no API key, so a leaked key fails the run instead of billing it.

Phases (round 1: linux and windows-field, project A only):
  0 facts      the machine; on windows-field the field profile is verified (LongPathsEnabled=0, Defender real-time
               scanning on without the C:\\ / D:\\ exclusions, no Java on PATH, JAVA_HOME at a Java 8, a JDK 21 in the
               per-user Temurin folder, a project path with a space and a non-ASCII letter) and anything not in place
               is reported NOT COVERED
  1 install    claude --version; marketplace add (#ref); plugin validate; plugin install; installed version and sha;
               no root .mcp.json, no .env*, every *.sh LF
  2 before     project A generated; a $0 session: the plugin listed, no plugin_errors, the onboarding note
  3 setup      the installed setup.md block. One retry, only for a transport error naming a pypi / pytorch /
               huggingface / neo4j URL (then PASS AFTER RETRY, which still blocks a release); exit 4 is a FAIL;
               the verdict READY, graph_engines == ["graphify"] (a silent ast fallback fails)
  4 after      a $0 session: both servers connected, the four tools registered, the READY announce
  5 tools      the installed smoketest, then exact answers from search / impact / cypher / embed over MCP, the CLI,
               and the installed doctor.md block reporting READY
  6 hooks      every hooks.json command under bash (and powershell.exe 5.1 on Windows), each with a POSITIVE signal
               (exit 0 counts for nothing): a heal.log header naming setup's interpreter, the READY announce, the
               Grep nudge, the edit note and its record, and a freshness note in a deliberately stale case
  7 lifecycle  mcp-stop + stop (all three ports closed, as after a reboot) -> a $0 session's SessionStart heal
               revives Neo4j and both servers. Whether a heal a headless session started outlives that session is a
               first-run question: when no heal finished and none runs, that row is NOT COVERED and the hooks.json
               heal run directly decides; a heal that finished (or still runs) with a port closed is a FAIL
  8 uninstall  mcp-stop, stop, plugin uninstall; no recorded DeepMap pid alive (always runs)

OPT-IN, CI ONLY. It uses the default ~/.deepmap and ~/.claude and starts Neo4j and the MCP servers on the base
ports, so it refuses (exit 2) unless CI=true AND GITHUB_ACTIONS=true AND RUNNER_ENVIRONMENT=github-hosted AND
DEEPMAP_LIVE_TEST=1 (never a self-hosted runner), or inside a container proven by /.dockerenv plus the token its
launcher wrote. `--dry-run` runs only the pure parts (fixture, command text, hook spellings, recorded sessions, the
report) and touches no DeepMap state; it refuses (exit 2) a --project-dir that already holds anything.

Exit: 0 every check held (NOT COVERED and SKIP rows allowed), 1 a FAIL or a PASS AFTER RETRY, 2 refused.
Usage (the workflow):
  python tests/live_e2e.py --claude "$CLAUDE_BIN" --ref "$REF" --profile windows-field --report "$RUNNER_TEMP/sanity"
  python tests/live_e2e.py --only cleanup --resume "$RUNNER_TEMP/sanity"      (if the main step was cut off)
Standard library only; run it with a Python that has no DeepMap packages (setup builds DeepMap's own environment).
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import socket
import sys
import tempfile
import textwrap
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import live_claude as LC  # noqa: E402
import live_e2e_fixture as FX  # noqa: E402

WINDOWS = os.name == "nt"
PLUGIN = "deep-map"
DEFAULT_REPO = "deepfusionlabs/deep-map"
DEFAULT_MARKETPLACE = "deepfusionlabs-deep-map"
CONTAINER_TOKEN_ENV = "DEEPMAP_SANITY_TOKEN"
CONTAINER_TOKEN_FILE = "/dmsan/token"
SESSION_TIMEOUT_S = 300
HOOK_BUDGET_S = 10.0
REVIVE_WAIT_S = 420
STALE_WAIT_S = 130          # deepmap_hook_state.NOT_STARTED_AFTER_S (120 s) plus a margin
SERVERS = ("deepmap-search", "deepmap-embed")
TOOLS = ("mcp__deepmap-search__deepmap_search", "mcp__deepmap-search__deepmap_impact",
         "mcp__deepmap-search__deepmap_cypher", "mcp__deepmap-embed__deepmap_embed")
SLASH = ("deep-map:setup", "deep-map:doctor", "deep-map:refresh", "deep-map:status", "deep-map:impact",
         "deep-map:version")
ONBOARD_NEEDLE = "installed but NOT set up"
READY_NEEDLE = "DeepMap is ACTIVE for this project"
NUDGE_NEEDLE = "DeepMap is set up for this project"
EDIT_NEEDLE = "Source files changed since DeepMap's last graph build"
JAVA_NOTE_NEEDLE = "NOTE: JAVA_HOME names"
UNSETTLED_HEAL = "first-run question: does a heal started by a headless `claude -p` outlive the session?"
NOT_PROVEN = (
    "the MCP approval prompt of an interactive session (`-p` connects project servers without asking)",
    "VS Code's Manage plugins UI",
    "the attended auto-sync default (`-p` sets CLAUDE_CODE_SESSION_ATTENDED=0)",
    "a non-admin Windows user, Windows 10, ARM64 Windows",
    "Microsoft Store Python",
    "non-English locales",
    "Intel Macs (unsupported)",
)


# ======================================================================================================== recording
class Recorder:
    """Check rows: {id, phase, status, detail, predicted}. status: pass | fail | skip | not-covered |
    pass-after-retry | info."""

    def __init__(self, quiet: bool = False):
        self.results: list = []
        self.cur = "0 facts"
        self.on_phase = None
        self.quiet = quiet

    def phase(self, name: str) -> None:
        self.cur = name
        if not self.quiet:
            print(f"\n=== {name} ===", flush=True)
        if self.on_phase:
            try:
                self.on_phase()
            except Exception:  # noqa: BLE001
                pass

    def _add(self, cid, status, detail="", predicted=None) -> None:
        self.results.append({"id": str(cid), "phase": self.cur, "status": status, "detail": _short(detail, 1500),
                             "predicted": predicted})
        tag = {"pass": "PASS", "fail": "FAIL", "skip": "SKIP", "not-covered": "NOT COVERED", "info": "INFO",
               "pass-after-retry": "PASS AFTER RETRY"}[status]
        show = "" if status == "pass" or not detail else f" - {_short(detail, 300)}"
        if self.quiet:
            return
        print(f"  [{tag}] {cid}{show}" + (f" ({predicted})" if predicted and status == "fail" else ""), flush=True)

    def check(self, cid, ok, detail="", predicted=None) -> bool:
        self._add(cid, "pass" if ok else "fail", "" if ok and not detail else detail, predicted)
        return bool(ok)

    def skip(self, cid, why) -> None:
        self._add(cid, "skip", why)

    def info(self, cid, value) -> None:
        self._add(cid, "info", value if isinstance(value, str) else json.dumps(value, default=str))

    def not_covered(self, cid, why) -> None:
        self._add(cid, "not-covered", why)

    def retry(self, cid, detail) -> None:
        self._add(cid, "pass-after-retry", detail)

    def record(self, cid, status, detail="") -> None:
        """A row whose status a verdict helper decided (pass / fail / not-covered / skip / info)."""
        self._add(cid, status, detail)


REC = Recorder()


def _short(text, n=600) -> str:
    text = "" if text is None else str(text)
    text = text.strip()
    return text if len(text) <= n else text[: n // 2] + " ... " + text[-n // 2:]


# ========================================================================================================== report
_SECTIONS = (("fail", "FAIL"), ("pass-after-retry", "PASS AFTER RETRY"), ("not-covered", "NOT COVERED"),
             ("skip", "SKIP"), ("pass", "PASS"))


def merge_results(old: list, new: list) -> list:
    """old with each (phase, id) that `new` also has replaced in place, then new's other rows appended."""
    out = [dict(r) for r in (old or [])]
    where = {(r.get("phase"), r.get("id")): i for i, r in enumerate(out)}
    for r in new or []:
        k = (r.get("phase"), r.get("id"))
        if k in where:
            out[where[k]] = dict(r)
        else:
            where[k] = len(out)
            out.append(dict(r))
    return out


def render_report(results: list, meta: dict) -> tuple:
    """(report.json text, report.md text, exit code). Exit 1 on any FAIL or PASS AFTER RETRY."""
    counts = {s: sum(1 for r in results if r.get("status") == s) for s in
              ("pass", "fail", "skip", "not-covered", "pass-after-retry", "info")}
    rc = 1 if counts["fail"] or counts["pass-after-retry"] else 0
    js = json.dumps({"meta": meta, "counts": counts, "exit_code": rc, "results": results}, indent=1, default=str)

    def esc(s):
        return str(s if s is not None else "").replace("|", "\\|").replace("\r", " ").replace("\n", " ")

    title = " ".join(str(meta.get(k)) for k in ("job", "os") if meta.get(k)) or platform.platform()
    md = [f"## DeepMap install sanity: {esc(title)}", ""]
    facts = [f"{k} {esc(meta[k])}" for k in ("mode", "repo", "ref", "claude_version", "plugin_version", "python",
                                             "minutes") if meta.get(k) not in (None, "")]
    if facts:
        md += ["; ".join(facts), ""]
    md += [f"{counts['pass']} passed, {counts['fail']} failed, {counts['pass-after-retry']} passed after a retry, "
           f"{counts['not-covered']} not covered, {counts['skip']} skipped.", ""]
    for status, label in _SECTIONS:
        rows = [r for r in results if r.get("status") == status]
        if not rows:
            continue
        md += [f"### {label}", ""]
        if status == "pass":
            md += [f"- {esc(r['phase'])}: {esc(r['id'])}" for r in rows]
        else:
            md += ["| Phase | Check | Detail |", "|---|---|---|"]
            for r in rows:
                pred = (str(r.get("predicted")) + ": ") if r.get("predicted") else ""
                md.append(f"| {esc(r['phase'])} | {esc(r['id'])} | {esc(pred)}{esc(str(r.get('detail'))[:400])} |")
        md.append("")
    infos = [r for r in results if r.get("status") == "info"]
    if infos:
        md += ["<details><summary>Recorded values</summary>", ""]
        md += [f"- **{esc(r['id'])}**: {esc(str(r.get('detail'))[:600])}" for r in infos] + ["", "</details>", ""]
    md += ["### Not proven by this run", ""] + [f"- {x}" for x in NOT_PROVEN] + [""]
    return js, "\n".join(md), rc


def write_report(report_dir, results: list, meta: dict, *, merge: bool = False, summary: bool = False) -> int:
    report_dir = Path(report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    jp = report_dir / "report.json"
    if merge and jp.is_file():
        try:
            old = json.loads(jp.read_text(encoding="utf-8"))
            results = merge_results(old.get("results") or [], results)
            meta = dict(old.get("meta") or {}, **meta)
        except (OSError, ValueError):
            pass
    js, md, rc = render_report(results, meta)
    jp.write_text(js, encoding="utf-8", newline="\n")
    (report_dir / "report.md").write_text(md, encoding="utf-8", newline="\n")
    target = os.environ.get("GITHUB_STEP_SUMMARY") if summary else None
    if target:
        try:
            with open(target, "a", encoding="utf-8") as fh:
                fh.write(md + "\n")
        except OSError:
            pass
    return rc


# ========================================================================================================= opt-in
def _read_text(p) -> str:
    with open(p, encoding="utf-8") as fh:
        return fh.read()


def opt_in(env, *, exists=os.path.exists, read_text=None) -> tuple:
    """(allowed, why). Allowed only on a GitHub-HOSTED Actions runner (CI=true, GITHUB_ACTIONS=true,
    RUNNER_ENVIRONMENT=github-hosted, DEEPMAP_LIVE_TEST=1: a self-hosted runner, a developer box registered as one
    say, sets the first two as well, and its owner's ~/.claude, ~/.deepmap and base ports must never be touched) or
    in a container proven by /.dockerenv plus the launcher's token (env DEEPMAP_SANITY_TOKEN == /dmsan/token, 16+
    characters) with DEEPMAP_LIVE_TEST=1. Never on a switch alone: this run writes ~/.deepmap and starts servers."""
    if str(env.get("DEEPMAP_LIVE_TEST", "")) != "1":
        return False, "DEEPMAP_LIVE_TEST is not 1"
    github = (str(env.get("CI", "")).strip().lower() == "true"
              and str(env.get("GITHUB_ACTIONS", "")).strip().lower() == "true")
    runner_env = str(env.get("RUNNER_ENVIRONMENT", "")).strip()
    if github and runner_env == "github-hosted":
        return True, "a GitHub-hosted Actions runner"
    tok = str(env.get(CONTAINER_TOKEN_ENV) or "").strip()
    try:
        docker = bool(exists("/.dockerenv"))
    except Exception:  # noqa: BLE001
        docker = False
    if not docker:
        if github:
            return False, (f"a GitHub Actions runner whose RUNNER_ENVIRONMENT is {runner_env or '(unset)'!s}, not "
                           "github-hosted (a self-hosted runner is someone's machine)")
        return False, ("not a GitHub-hosted Actions runner (CI=true, GITHUB_ACTIONS=true and "
                       "RUNNER_ENVIRONMENT=github-hosted) and no /.dockerenv")
    if len(tok) < 16:
        return False, f"/.dockerenv but no launcher token in {CONTAINER_TOKEN_ENV} (16+ characters)"
    try:
        want = str((read_text or _read_text)(CONTAINER_TOKEN_FILE) or "").strip()
    except Exception:  # noqa: BLE001
        want = ""
    if want and want == tok:
        return True, "a container its launcher marked"
    return False, f"the launcher token does not match {CONTAINER_TOKEN_FILE}"


# ================================================================================================== command text
_FENCE = re.compile(r"^```bash[ \t]*\n(.*?)^```[ \t]*$", re.S | re.M)


def command_block(md: bytes, index: int = 0) -> tuple:
    """(the index-th ```bash block of a command .md, LF line endings; the CRLF count of the file). Read as BYTES:
    a CRLF checkout (Windows, core.autocrlf) is recorded, and the CRs never reach bash (the model types the block,
    it does not paste CRs). (None, n) when there is no such block."""
    data = md if isinstance(md, (bytes, bytearray)) else str(md).encode("utf-8")
    crlf = data.count(b"\r\n")
    text = data.decode("utf-8", "replace").replace("\r\n", "\n")
    blocks = _FENCE.findall(text)
    return (blocks[index] if len(blocks) > index else None), crlf


def substitute(block: str, plugin_root, arguments: str = "") -> str:
    """Claude Code's textual substitution: ${CLAUDE_PLUGIN_ROOT} -> the install path (forward slashes, which bash
    and Git Bash both take), $ARGUMENTS -> the command's arguments."""
    root = str(plugin_root).replace("\\", "/")
    return str(block).replace("${CLAUDE_PLUGIN_ROOT}", root).replace("$ARGUMENTS", arguments)


_PY_WORD = re.compile(r"(?<![\w./-])python(?=\s)")


def python_fallback(text: str, have_python: bool) -> tuple:
    """setup.md / doctor.md: "Use python3 where python is absent; the command is otherwise the same."
    (text, applied)."""
    if have_python:
        return text, False
    new = _PY_WORD.sub("python3", text)
    return new, new != text


def hook_commands(hooks_json: dict) -> list:
    """[(event, matcher, command, timeout)] as hooks.json lists them."""
    out = []
    for event, groups in ((hooks_json or {}).get("hooks") or {}).items():
        for g in groups or []:
            for h in g.get("hooks") or []:
                if h.get("type", "command") == "command" and h.get("command"):
                    out.append((event, g.get("matcher") or "", h["command"], h.get("timeout")))
    return out


def hook_line(cmd: str, plugin_root, kind: str) -> str:
    """The command as Claude Code hands it to a shell: native separators for PowerShell, forward slashes for bash."""
    root = str(plugin_root) if kind == "ps" else str(plugin_root).replace("\\", "/")
    return cmd.replace("${CLAUDE_PLUGIN_ROOT}", root)


def hook_argv(kind: str, exe: str, line: str) -> list:
    if kind == "ps":
        return [exe, "-NoProfile", "-NonInteractive", "-Command", line]
    return [exe, "-c", line]


_KINDS = {"deepmap_sessionstart_heal": "heal", "deepmap_sessionstart_notify": "notify",
          "deepmap_presearch_nudge": "nudge", "deepmap_postedit_syncflag": "edit",
          "deepmap_toolresult_freshness": "freshness"}


def hook_kind(name_or_cmd: str):
    m = re.search(r"(deepmap_[a-z_]+?)(?:\.py)?(?:\"|$|\s)", str(name_or_cmd) + " ")
    return _KINDS.get(m.group(1)) if m else None


# ================================================================================================= transport retry
_HOSTS = re.compile(r"\b(files\.pythonhosted\.org|pypi\.org|download\.pytorch\.org|(?:[\w-]+\.)*huggingface\.co|"
                    r"(?:[\w-]+\.)*hf\.co|(?:[\w-]+\.)*neo4j\.(?:com|org))\b", re.I)
_TRANSPORT = re.compile(
    r"ConnectionResetError|ConnectionRefusedError|ConnectionAbortedError|NewConnectionError|ConnectionError|"
    r"Connection (?:aborted|reset|refused)|timed out|TimeoutError|ReadTimeout|ConnectTimeout|"
    r"Temporary failure in name resolution|Name or service not known|getaddrinfo failed|nodename nor servname|"
    r"Max retries exceeded|RemoteDisconnected|IncompleteRead|ProtocolError|SSLError|SSL: |urlopen error|"
    r"HTTP error\W{0,3}(?:429|5\d\d)\b|\b(?:429|50[0-4]) (?:Client |Server )?Error|Too Many Requests|"
    r"Service Unavailable|Bad Gateway|Gateway Time-?out", re.I)


def transport_error(text: str):
    """'<host>: <line>' when the output shows a network failure talking to a host DeepMap downloads from (PyPI,
    PyTorch's index, Hugging Face, Neo4j), within three lines of that host; else None. A checksum or integrity
    failure, a 404, or an error naming no such host is never a transport error: those are defects."""
    lines = (text or "").splitlines()
    for i, ln in enumerate(lines):
        m = _HOSTS.search(ln)
        if not m:
            continue
        near = lines[max(0, i - 3): i + 4]
        if re.search(r"integrity|checksum|sha256 mismatch", "\n".join(near), re.I):
            continue
        err = next((x for x in near if _TRANSPORT.search(x)), None)
        if err is not None:
            return f"{m.group(1)}: {err.strip()[:200]}"
    return None


# =================================================================================================== processes
def process_table_argv(windows: bool = WINDOWS) -> list:
    if windows:
        ps = os.environ.get("SystemRoot", r"C:\Windows") + r"\System32\WindowsPowerShell\v1.0\powershell.exe"
        script = ("$ErrorActionPreference='SilentlyContinue'; [Console]::OutputEncoding=[Text.Encoding]::UTF8; "
                  "Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,ExecutablePath,CommandLine "
                  "| ConvertTo-Json -Compress -Depth 2")
        return [ps, "-NoProfile", "-NonInteractive", "-Command", script]
    return ["ps", "-A", "-ww", "-o", "pid=,command="]


def parse_ps(text: str) -> list:
    rows = []
    for ln in (text or "").splitlines():
        parts = ln.strip().split(None, 1)
        if len(parts) == 2 and parts[0].isdigit():
            rows.append((int(parts[0]), parts[1]))
    return rows


def parse_cim(text: str) -> list:
    try:
        data = json.loads(text) if (text or "").strip() else []
    except ValueError:
        return []
    if isinstance(data, dict):
        data = [data]
    out = []
    for d in data if isinstance(data, list) else []:
        if isinstance(d, dict) and isinstance(d.get("ProcessId"), int):
            out.append({"pid": d["ProcessId"], "ppid": d.get("ParentProcessId"), "exe": d.get("ExecutablePath") or "",
                        "cmd": d.get("CommandLine") or ""})
    return out


def process_table() -> list:
    """[{pid, ppid, exe, cmd}] of every process: ps on POSIX, Win32_Process through powershell.exe on Windows."""
    r = LC.run(process_table_argv(), timeout=90)
    if WINDOWS:
        return parse_cim(r.out)
    return [{"pid": p, "ppid": None, "exe": "", "cmd": c} for p, c in parse_ps(r.out)]


def wait_until(pred, timeout: float, step: float = 3.0) -> bool:
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


def port_open(port, host: str = "127.0.0.1") -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=2):
            return True
    except (OSError, ValueError, TypeError):
        return False


# ======================================================================================================= secrets
def scan_and_redact(root, secrets=()) -> list:
    """Every file under root holding an sk-ant- key or one of `secrets` is redacted in place. [(path, kinds)]."""
    hits = []
    for p in sorted(Path(root).rglob("*")):
        if not p.is_file():
            continue
        try:
            data = p.read_bytes()
        except OSError:
            continue
        text = data.decode("utf-8", "surrogateescape")
        kinds = LC.find_secrets(text, secrets)
        if kinds:
            try:
                p.write_bytes(LC.redact(text, secrets).encode("utf-8", "surrogateescape"))
            except OSError:
                pass
            hits.append((str(p), ", ".join(kinds)))
    return hits


# ===================================================================================================== the run
class Ctx:
    def __init__(self, args):
        self.args = args
        self.claude = args.claude or ""
        self.config_dir = Path(args.config_dir or (Path.home() / ".claude"))
        self.repo = args.repo
        self.ref = args.ref or ""
        self.project = Path(args.project_dir or (Path.home() / "dm proj \u05d0" / "polyshop"))
        self.report = Path(args.report or tempfile.mkdtemp(prefix="deepmap-sanity-"))
        self.logs = self.report / "logs"
        self.home = Path.home() / ".deepmap"     # the product's default; the child env never carries DEEPMAP_HOME
        self.marketplace = DEFAULT_MARKETPLACE
        self.install_path: Path | None = None
        self.mkt_path: Path | None = None
        self.version = ""
        self.cli_version = ""
        self.setup_ok = False
        self.uninstalled = False
        self.field: dict = {}          # windows-field preconditions phase 0 saw hold (java_checks_due reads them)
        self.bash = find_bash()
        self.ps51 = ps51() if WINDOWS else None
        self.started = time.time()

    @property
    def plugin_id(self) -> str:
        return f"{PLUGIN}@{self.marketplace}"

    def user_env(self, extra=None) -> dict:
        return LC.child_env(os.environ, config_dir=str(self.config_dir), extra=extra)

    def venv_python(self) -> Path:
        return self.home / "venv" / ("Scripts/python.exe" if WINDOWS else "bin/python")

    def meta(self) -> dict:
        return {"mode": "live", "job": self.args.job or self.args.profile, "profile": self.args.profile,
                "os": platform.platform(), "python": sys.version.split()[0], "repo": self.repo, "ref": self.ref,
                "claude_version": self.cli_version, "plugin_version": self.version,
                "minutes": round((time.time() - self.started) / 60, 1)}

    def state(self) -> dict:
        return {"claude": self.claude, "config_dir": str(self.config_dir), "project": str(self.project),
                "marketplace": self.marketplace, "install_path": str(self.install_path or ""),
                "setup_ok": self.setup_ok, "uninstalled": self.uninstalled, "repo": self.repo, "ref": self.ref,
                "profile": self.args.profile}


def find_bash():
    """The bash Claude Code's Bash tool uses: Git Bash on Windows (never System32's WSL bash), else bash."""
    if not WINDOWS:
        return shutil.which("bash") or ("/bin/bash" if os.path.exists("/bin/bash") else None)
    cands = [os.environ.get("CLAUDE_CODE_GIT_BASH_PATH")]
    for var in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
        if os.environ.get(var):
            cands.append(os.path.join(os.environ[var], "Git", "bin", "bash.exe"))
    if os.environ.get("LOCALAPPDATA"):
        cands.append(os.path.join(os.environ["LOCALAPPDATA"], "Programs", "Git", "bin", "bash.exe"))
    git = shutil.which("git")
    if git:
        cands.append(str(Path(git).resolve().parent.parent / "bin" / "bash.exe"))
    return next((c for c in cands if c and os.path.isfile(c)), None)


def ps51():
    p = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "WindowsPowerShell", "v1.0",
                     "powershell.exe")
    return p if os.path.isfile(p) else None


def log_run(ctx: Ctx, name: str, argv, res, *, cwd=None) -> None:
    try:
        ctx.logs.mkdir(parents=True, exist_ok=True)
        with open(ctx.logs / name, "a", encoding="utf-8") as fh:
            fh.write(f"$ {' '.join(str(a) for a in argv)}\n# cwd={cwd} rc={res.rc} {res.secs}s\n{res.out}\n"
                     f"--- stderr ---\n{res.err}\n\n")
    except OSError:
        pass


def sh(ctx: Ctx, name: str, argv, *, cwd=None, env=None, timeout=600, stdin=None):
    res = LC.run(argv, cwd=cwd, env=env if env is not None else ctx.user_env(), timeout=timeout, stdin=stdin)
    log_run(ctx, name, argv, res, cwd=cwd)
    return res


def read_json(p):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def cli_env(ctx: Ctx, extra=None) -> dict:
    """What the /deep-map:* command blocks set before their python."""
    return ctx.user_env(dict({"NEO4J_PASSWORD": "deepmap", "PYTHONUTF8": "1"}, **(extra or {})))


def installed_build(ctx: Ctx) -> Path:
    return Path(ctx.install_path or ".") / "build"


PRELUDE = "import sys, os, json\nsys.path.insert(0, {build!r})\n"


def probe(ctx: Ctx, py, body: str, *, env=None, timeout=180):
    """Run `body` under `py` with the INSTALLED build/ importable; its last stdout line must be JSON."""
    code = PRELUDE.format(build=str(installed_build(ctx))) + textwrap.dedent(body)
    res = sh(ctx, "probes.log", [py, "-X", "utf8", "-c", code], env=env or cli_env(ctx), timeout=timeout)
    lines = [ln for ln in res.out.strip().splitlines() if ln.strip()]
    try:
        return json.loads(lines[-1]), res.out + "\n" + res.err
    except (IndexError, ValueError):
        return None, f"rc={res.rc}\n{res.out}\n{res.err}"


# ----------------------------------------------------------------------------------------------------- sessions
def run_session(ctx: Ctx, label: str, *, cwd=None, extra=None):
    """A $0 unauthenticated session: (parsed Session, CliResult). Records cost, key source and CLI version checks."""
    env = ctx.user_env(extra)
    REC.check(f"{label}: the session's env holds no API key or token", not LC.key_env_names(env),
              LC.key_env_names(env))
    argv = LC.p_argv(ctx.claude)
    res = LC.run(argv, cwd=str(cwd or ctx.project), env=env, timeout=SESSION_TIMEOUT_S)
    sdir = ctx.logs / "sessions"
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / f"{label}.jsonl").write_bytes(res.raw)
    (sdir / f"{label}.err").write_text(res.err, encoding="utf-8")
    s = LC.parse_stream(res.raw)
    REC.info(f"{label}: exit, seconds, events, unreadable lines", [res.rc, res.secs, len(s.events), s.bad_lines])
    REC.check(f"{label}: system/init was read", s.init is not None, f"rc={res.rc} {res.err[-600:]}")
    REC.check(f"{label}: total_cost_usd is exactly 0", s.cost() == 0, f"cost={s.cost()!r}")
    REC.check(f"{label}: apiKeySource is none", s.api_key_source() == "none", s.api_key_source())
    if ctx.args.claude_version:
        REC.check(f"{label}: init.claude_code_version is the pin {ctx.args.claude_version}",
                  s.version() == ctx.args.claude_version, s.version())
    REC.check(f"{label}: no key in the session output", not LC.find_secrets(res.out + res.err))
    for h in s.hook_responses():
        v = LC.stderr_verdict(h.get("stderr") or "")
        if v["fatal"] or v["missing"]:
            REC.info(f"{label}: hook {h.get('hook_name')} stderr",
                     {"fatal": v["fatal"], "missing": sorted(v["missing"]), "stderr": _short(h.get("stderr"), 400)})
    return s, res


def hook_stderr_ok(s) -> tuple:
    bad = []
    for h in s.hook_responses():
        v = LC.stderr_verdict(h.get("stderr") or "")
        if v["fatal"]:
            bad.append((h.get("hook_name"), v["fatal"]))
    return not bad, bad


# ========================================================================================================= phases
def phase_facts(ctx: Ctx) -> None:
    REC.phase("0 facts")
    f = {"platform": platform.platform(), "machine": platform.machine(), "python": sys.version.split()[0],
         "cpus": os.cpu_count(), "home": str(Path.home()), "config_dir": str(ctx.config_dir),
         "config_dir_len": len(str(ctx.config_dir)), "project": str(ctx.project), "bash": ctx.bash,
         "powershell_5_1": ctx.ps51, "JAVA_HOME": os.environ.get("JAVA_HOME", ""),
         "JAVA_HOME_vars": sorted(k for k in os.environ if k.upper().startswith("JAVA_HOME_")),
         "java_on_PATH": shutil.which("java", path=ctx.user_env().get("PATH")),
         "python_on_PATH": shutil.which("python", path=ctx.user_env().get("PATH")),
         "python3_on_PATH": shutil.which("python3", path=ctx.user_env().get("PATH"))}
    try:
        import locale
        f["preferred_encoding"] = locale.getpreferredencoding(False)
    except Exception:  # noqa: BLE001
        pass
    for name, argv in (("git", ["git", "--version"]), ("java -version", ["java", "-version"])):
        r = LC.run(argv, env=ctx.user_env(), timeout=60)
        f[name] = _short((r.out + " " + r.err).strip(), 300) if r.rc is not None else None
    if WINDOWS:
        r = LC.run(["cmd", "/c", "chcp"], timeout=30)
        f["code_page"] = r.out.strip()
    for k, v in f.items():
        REC.info(k, v)
    REC.check("the job env holds no API key or token (the free tier never needs one)",
              not LC.key_env_names(os.environ), LC.key_env_names(os.environ))
    REC.check("Git Bash / bash found (Claude Code's Bash tool and hook shell)", bool(ctx.bash), ctx.bash)
    p = str(ctx.project)
    space_nonascii = " " in p and any(ord(c) > 127 and c.isalpha() for c in p)
    if ctx.args.profile == "windows-field":
        REC.check("windows-field: the project path holds a space and a non-ASCII letter", space_nonascii, p)
        field_profile(ctx)
    else:
        REC.info("project path holds a space and a non-ASCII letter", space_nonascii)


def _java_major(text: str):
    m = re.search(r'version "(\d+)(?:\.(\d+))?', text or "")
    if not m:
        return None
    major = int(m.group(1))
    return int(m.group(2)) if major == 1 and m.group(2) else major


def field_profile(ctx: Ctx) -> None:
    """Verify the windows-field machine state the workflow set up; NOT COVERED where it did not take."""
    r = LC.run(["reg", "query", r"HKLM\SYSTEM\CurrentControlSet\Control\FileSystem", "/v", "LongPathsEnabled"],
               timeout=30)
    m = re.search(r"LongPathsEnabled\s+REG_DWORD\s+0x([0-9a-fA-F]+)", r.out)
    lp = int(m.group(1), 16) if m else None
    if lp == 0:
        REC.check("windows-field: LongPathsEnabled is 0 (the MAX_PATH case is live)", True)
    else:
        REC.not_covered("MAX_PATH", f"LongPathsEnabled reads {lp!r}, not 0: {r.out.strip()[:200]}")
    g = LC.run(["git", "config", "--get", "core.longpaths"], env=ctx.user_env(), timeout=30)
    if g.out.strip().lower() == "true":
        REC.not_covered("MAX_PATH (git)", "git core.longpaths is true, so a long clone path cannot fail")
    else:
        REC.info("git core.longpaths", g.out.strip() or "(unset)")
    d = LC.run([ctx.ps51 or "powershell", "-NoProfile", "-NonInteractive", "-Command",
                "$s=Get-MpComputerStatus; $p=Get-MpPreference; @{rtp=$s.RealTimeProtectionEnabled; "
                "excl=@($p.ExclusionPath)} | ConvertTo-Json -Compress"], timeout=120)
    try:
        dj = json.loads(d.out.strip() or "{}")
    except ValueError:
        dj = {}
    excl = [str(x) for x in (dj.get("excl") or []) if x]
    broad = [x for x in excl if x.rstrip("\\/").upper() in ("C:", "D:")]
    if dj.get("rtp") is True and not broad:
        REC.check("windows-field: Defender real-time scanning is on, without C:\\ / D:\\ exclusions", True,
                  f"exclusions: {excl}")
    else:
        REC.not_covered("Defender", f"real-time={dj.get('rtp')!r} broad exclusions={broad} ({d.err.strip()[:200]})")
    jh = os.environ.get("JAVA_HOME", "")
    jexe = os.path.join(jh, "bin", "java.exe") if jh else ""
    major = None
    if jexe and os.path.isfile(jexe):
        jr = LC.run([jexe, "-version"], timeout=60)
        major = _java_major(jr.out + jr.err)
    ctx.field["decoy"] = major is not None and major not in (17, 21)
    ctx.field["java_home_usable"] = major in (17, 21)
    if ctx.field["decoy"]:
        REC.check(f"windows-field: JAVA_HOME names a Java {major} (a version Neo4j cannot use)", True, jh)
    else:
        REC.not_covered("wrong JAVA_HOME", f"JAVA_HOME={jh!r} java major={major!r}")
    on_path = shutil.which("java", path=ctx.user_env().get("PATH"))
    ctx.field["no_java_on_path"] = not on_path
    if on_path:
        REC.not_covered("no Java on PATH", f"java is on PATH: {on_path}")
    else:
        REC.check("windows-field: no java on PATH", True)
    base = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Eclipse Adoptium"
    jdk21 = [j for j in (sorted(base.glob("jdk-21*")) if base.is_dir() else []) if (j / "bin" / "java.exe").is_file()]
    ctx.field["jdk21"] = bool(jdk21)
    if jdk21:
        REC.check("windows-field: a JDK 21 sits in the per-user Temurin folder", True, str(jdk21[0]))
    else:
        REC.not_covered("per-user JDK 21", f"nothing under {base}")


def java_checks_due(field: dict) -> dict:
    """Which windows-field Java checks of phase 3 can mean anything, from the preconditions phase 0 saw hold:
    {'S8': (run, why not), 'S9': (run, why not)}. S8 (setup passed over JAVA_HOME and printed its NOTE) needs a
    JAVA_HOME naming a Java Neo4j cannot use. S9 (Neo4j runs the per-user Temurin JDK 21) needs that JDK there, no
    java on PATH (setup would hand Neo4j the PATH java's home) and no usable JAVA_HOME (Neo4j would run that). A
    precondition phase 0 did not see hold (or never checked) is reported NOT COVERED, never FAIL."""
    f = field or {}
    s8 = (True, "") if f.get("decoy") else (False, "JAVA_HOME does not name a Java 8 / 11 / 25 here (phase 0: NOT "
                                                   "COVERED wrong JAVA_HOME), so there is nothing to pass over")
    missing = [why for ok, why in ((f.get("jdk21"), "no JDK 21 in the per-user Temurin folder"),
                                   (f.get("no_java_on_path"), "a java on PATH"),
                                   (f.get("java_home_usable") is False, "JAVA_HOME names a usable 17 / 21 or was "
                                                                        "not checked")) if not ok]
    s9 = (True, "") if not missing else (False, "phase 0 saw " + "; ".join(missing))
    return {"S8": s8, "S9": s9}


def phase_install(ctx: Ctx) -> bool:
    REC.phase("1 install")
    r = sh(ctx, "install.log", [ctx.claude, "--version"], timeout=120)
    ctx.cli_version = LC.version_of(r.out) or ""
    REC.check("I1 claude --version answers", r.rc == 0 and bool(ctx.cli_version), r.out + r.err)
    if ctx.args.claude_version:
        REC.check(f"I1 the CLI is the pinned {ctx.args.claude_version}", ctx.cli_version == ctx.args.claude_version,
                  ctx.cli_version)
    src = f"{ctx.repo}#{ctx.ref}" if ctx.ref else ctx.repo
    first_err, ok, tries = None, False, 0
    for tries in (1, 2, 3):
        r = sh(ctx, "install.log", [ctx.claude, "plugin", "marketplace", "add", src, "--json"], timeout=600)
        if r.rc == 0:
            ok = True
            break
        first_err = first_err or (r.out + r.err)
        time.sleep(20)
    net = bool(first_err) and re.search(r"Could not resolve host|unable to access|Connection|timed out|RPC failed|"
                                        r"early EOF|returned error: 5\d\d|TLS|SSL", first_err or "", re.I)
    cid = f"I2 claude plugin marketplace add {src}"
    if ok and tries == 1:
        REC.check(cid, True)
    elif ok and net:
        REC.check(cid, True, f"after {tries} tries; the first failed on the network: {_short(first_err, 300)}")
    elif ok:
        REC.retry(cid, f"after {tries} tries; the first failure was not the network: {_short(first_err, 400)}")
    else:
        REC.check(cid, False, first_err)
        return False
    km = read_json(ctx.config_dir / "plugins" / "known_marketplaces.json") or {}
    entry_name, entry = next(((k, v) for k, v in km.items() if isinstance(v, dict) and str(
        (v.get("source") or {}).get("repo", "")).lower() == ctx.repo.lower()), (None, None))
    if entry_name:
        ctx.marketplace = entry_name
        ctx.mkt_path = Path(entry.get("installLocation") or ctx.config_dir / "plugins" / "marketplaces" / entry_name)
    REC.check("I2 known_marketplaces.json records the GitHub source" + (f" at ref {ctx.ref}" if ctx.ref else ""),
              bool(entry) and (entry.get("source") or {}).get("source") == "github"
              and (not ctx.ref or (entry.get("source") or {}).get("ref") == ctx.ref), json.dumps(km)[:600])
    if not ctx.mkt_path:
        ctx.mkt_path = ctx.config_dir / "plugins" / "marketplaces" / ctx.marketplace
    r = sh(ctx, "install.log", [ctx.claude, "plugin", "validate", str(ctx.mkt_path), "--json"], timeout=300)
    try:
        vj = json.loads(r.out[r.out.index("{"):]) if "{" in r.out else {}
    except ValueError:
        vj = {}
    REC.check("I3 claude plugin validate passes on the marketplace clone", r.rc == 0 and vj.get("success", True)
              is not False, r.out[-800:] + r.err[-400:])
    REC.info("I3 validate output", _short(r.out, 800))
    r = sh(ctx, "install.log", [ctx.claude, "plugin", "install", ctx.plugin_id, "--scope", "user", "--json"],
           timeout=600)
    if not REC.check(f"I4 claude plugin install {ctx.plugin_id} --scope user", r.rc == 0, r.out + r.err):
        return False
    ip = read_json(ctx.config_dir / "plugins" / "installed_plugins.json") or {}
    recs = (ip.get("plugins") or {}).get(ctx.plugin_id) or []
    rec = recs[0] if recs and isinstance(recs[0], dict) else {}
    ctx.install_path = Path(rec["installPath"]) if rec.get("installPath") else None
    ctx.version = str(rec.get("version") or "")
    REC.info("installed_plugins.json entry", rec)
    if not REC.check("I5 installed_plugins.json names the install path", bool(ctx.install_path)
                     and ctx.install_path.is_dir(), rec):
        return False
    pj = read_json(ctx.mkt_path / ".claude-plugin" / "plugin.json") or {}
    REC.check("I5 the installed version is the marketplace clone's plugin.json version",
              bool(ctx.version) and ctx.version == str(pj.get("version")),
              f"installed {ctx.version} clone {pj.get('version')}")
    if ctx.args.expect_version:
        REC.check(f"I5 the installed version is {ctx.args.expect_version}", ctx.version == ctx.args.expect_version,
                  ctx.version)
    head = LC.run(["git", "-C", str(ctx.mkt_path), "rev-parse", "HEAD"], env=ctx.user_env(), timeout=60).out.strip()
    sha = str(rec.get("gitCommitSha") or "")
    REC.check("I5 the installed gitCommitSha is the marketplace clone's HEAD", bool(sha) and bool(head)
              and head.startswith(sha), f"installed {sha} clone {head}")
    if ctx.args.expect_sha:
        want = ctx.args.expect_sha.strip().lower()
        REC.check(f"I5 the clone is at the expected commit {want[:12]}", len(want) >= 7 and bool(head)
                  and (head.lower().startswith(want) or want.startswith(head.lower())), head)
    root = ctx.install_path
    need = ["hooks/hooks.json", "commands/setup.md", "commands/doctor.md", "build/provision.py",
            "tests/mcp_http_smoketest.py"]
    REC.check("I6 the install holds hooks.json, the commands, build/ and the smoketest",
              all((root / n).is_file() for n in need), [n for n in need if not (root / n).is_file()])
    REC.check("I7 no .mcp.json at the plugin root (setup writes the project's own)", not (root / ".mcp.json").exists())
    envs = [str(p.relative_to(root)) for p in root.rglob(".env*") if p.is_file()]
    REC.check("I7 no .env file anywhere in the install", not envs, envs)
    crlf_sh = [str(p.relative_to(root)) for p in root.rglob("*.sh") if b"\r" in p.read_bytes()]
    REC.check("I8 every *.sh in the install has LF line endings", not crlf_sh, crlf_sh)
    md = (root / "commands" / "setup.md").read_bytes()
    REC.info("installed commands/setup.md CRLF line endings", md.count(b"\r\n"))
    write_state(ctx)
    return True


def write_state(ctx: Ctx) -> None:
    try:
        (ctx.report / "state.json").write_text(json.dumps(ctx.state(), indent=1), encoding="utf-8")
    except OSError:
        pass


def phase_before(ctx: Ctx) -> bool:
    REC.phase("2 project A + a $0 session before setup")
    if ctx.project.exists():
        REC.check("project A's directory did not exist before this run", False, str(ctx.project))
        return False
    made = FX.make_project(ctx.project)
    REC.info("project A", made)
    if not REC.check("project A generated and committed", made.get("head") and not made.get("error"), made):
        return False
    s, _ = run_session(ctx, "P before-setup")
    pe = s.plugin(ctx.plugin_id)
    REC.check("P1 the plugin is listed at the installed version", bool(pe) and str(pe.get("version")) == ctx.version,
              [p.get("source") for p in s.plugins()])
    REC.check("P2 init reports no plugin_errors", not s.plugin_errors(), s.plugin_errors())
    REC.check("P3 the deep-map slash commands are listed", set(SLASH) <= set(s.slash_commands()),
              sorted(set(SLASH) - set(s.slash_commands())))
    hr = s.hook_responses("SessionStart")
    REC.check("P4 both SessionStart hooks ran", len(hr) >= 2, [h.get("hook_name") for h in hr])
    ctxs = s.hook_contexts("SessionStart")
    REC.check("P5 the onboarding note is in the session (the notify hook ran)", any(ONBOARD_NEEDLE in c
                                                                                    for c in ctxs), ctxs)
    ok, bad = hook_stderr_ok(s)
    REC.check("P6 no SessionStart hook stderr shows a failure", ok, bad)
    REC.info("init.powershell_path", (s.init or {}).get("powershell_path"))
    return True


def _progress_complete(text: str) -> tuple:
    """deps_bootstrap's `[provision] [k/N] <step> ...` lines: every k of 1..N seen (N may grow while it runs)."""
    steps = re.findall(r"^\[provision\] \[(\d+)/(\d+)\] ", text or "", re.M)
    if not steps:
        return False, "no numbered progress lines"
    n = max(int(t) for _, t in steps)
    seen = {int(k) for k, _ in steps}
    return set(range(1, n + 1)) <= seen, f"steps seen {sorted(seen)} of {n}"


def run_block(ctx: Ctx, command: str, log: str, *, args: str = "", timeout: float = 7200):
    """Run the first bash block of an INSTALLED command file as /deep-map:<command> would. (CliResult, text)."""
    md = (Path(ctx.install_path) / "commands" / f"{command}.md").read_bytes()
    block, crlf = command_block(md)
    if not block:
        return None, None
    text = substitute(block, ctx.install_path, args)
    have_py = bool(shutil.which("python", path=ctx.user_env().get("PATH")))
    text, fell_back = python_fallback(text, have_py)
    if fell_back:
        REC.info(f"{command}.md: python is absent, setup.md's python3 substitution applied", True)
    (ctx.logs / f"{command}-command.txt").write_text(text, encoding="utf-8")
    res = sh(ctx, log, [ctx.bash, "-c", text], cwd=str(ctx.project), env=ctx.user_env(), timeout=timeout)
    return res, text


def phase_setup(ctx: Ctx) -> bool:
    REC.phase("3 setup (the installed commands/setup.md block)")
    if not ctx.bash:
        REC.check("S1 a bash to run the block", False, "no bash / Git Bash found")
        return False
    res, text = run_block(ctx, "setup", "setup.log", timeout=ctx.args.setup_timeout)
    if res is None:
        REC.check("S0 setup.md has a bash block", False)
        return False
    out = res.out + "\n" + res.err
    REC.info("setup exit code and wall time (s)", [res.rc, res.secs])
    if res.rc == 4:
        REC.check("S1 setup exits 0", False, "exit 4 (the graph was NOT rebuilt) on project A is a FAIL: "
                  + _short(out[-1200:], 1200))
        return False
    if res.rc != 0:
        host = transport_error(out)
        if not host:
            REC.check("S1 setup exits 0", False, f"exit {res.rc}: {_short(out[-1500:], 1500)}")
            return False
        REC.info("setup first attempt failed on the network", host)
        res2 = sh(ctx, "setup.retry.log", [ctx.bash, "-c", text], cwd=str(ctx.project), env=ctx.user_env(),
                  timeout=ctx.args.setup_timeout)
        out = res2.out + "\n" + res2.err
        if res2.rc != 0:
            REC.check("S1 setup exits 0", False, f"exit {res.rc}, then {res2.rc} on its one retry: {_short(out, 1200)}")
            return False
        REC.retry("S1 setup exits 0", f"first attempt exit {res.rc}: transport error ({host}); the retry passed")
    else:
        REC.check("S1 setup exits 0", True)
    man = read_json(ctx.project / ".deepmap" / "graph_build.json") or {}
    REC.check("S2 the persisted verdict is READY (no DEGRADED, .sql included)", man.get("verdict") == "READY",
              {k: man.get(k) for k in ("verdict", "verdict_reasons", "reasons", "coverage") if k in man})
    REC.check("S3 graph_engines == ['graphify'] (no silent ast fallback, e.g. past MAX_PATH)",
              man.get("graph_engines") == ["graphify"], man.get("graph_engines"))
    mark = read_json(ctx.home / "venv" / "deepmap-venv.json") or {}
    REC.check("S4 DeepMap's own environment is complete (venv/deepmap-venv.json)", mark.get("complete") is True, mark)
    ok, why = _progress_complete(out)
    REC.check("S5 setup printed its numbered progress [1/N] .. [N/N]", ok, why)
    mcp = (read_json(ctx.project / ".mcp.json") or {}).get("mcpServers") or {}
    REC.check("S6 the project's .mcp.json names both servers as http on 127.0.0.1",
              all((mcp.get(n) or {}).get("type") == "http" and "127.0.0.1" in str((mcp.get(n) or {}).get("url"))
                  for n in SERVERS), mcp)
    owners = owner_records(ctx)
    alive = {p["pid"] for p in process_table()}
    REC.check("S7 both MCP servers have an owner record and the recorded pid is alive",
              all(any(n.startswith(s + "-") and int(r.get("pid") or -1) in alive for n, r in owners) for s in SERVERS),
              [(n, r.get("pid")) for n, r in owners])
    REC.info("setup-python records", [p.name for p in (ctx.home / "run").glob("setup-python-*.json")])
    if ctx.args.profile == "windows-field":
        due = java_checks_due(ctx.field)
        s8 = "S8 windows-field: setup passed over the wrong JAVA_HOME and printed its NOTE line"
        if due["S8"][0]:
            note = [ln.strip() for ln in out.splitlines() if JAVA_NOTE_NEEDLE in ln]
            REC.check(s8, bool(note), "no NOTE line" if not note else note[0])
        else:
            REC.not_covered(s8, due["S8"][1])
        jvm = [p for p in process_table() if p["exe"].lower().endswith("java.exe") and "neo4j" in p["cmd"].lower()]
        s9 = "S9 windows-field: Neo4j runs the per-user Temurin JDK 21"
        if due["S9"][0]:
            REC.check(s9, bool(jvm) and all("eclipse adoptium" in p["exe"].lower() and "jdk-21" in p["exe"].lower()
                                            for p in jvm), [p["exe"] for p in jvm])
        else:
            REC.not_covered(s9, f"{due['S9'][1]} (Neo4j runs {[p['exe'] for p in jvm]})")
    else:
        jvm = [p["cmd"][:200] for p in process_table() if "neo4j" in p["cmd"].lower() and "java" in p["cmd"].lower()]
        REC.info("Neo4j JVM command lines", jvm[:2])
    ctx.setup_ok = True
    write_state(ctx)
    return True


def owner_records(ctx: Ctx) -> list:
    out = []
    for f in sorted((ctx.home / "run").glob("*.owner")):
        rec = read_json(f)
        if isinstance(rec, dict) and rec.get("pid"):
            out.append((f.name, rec))
    return out


def heals_running() -> list:
    return [p["pid"] for p in process_table() if "provision.py" in p["cmd"] and " heal" in p["cmd"]]


def phase_after(ctx: Ctx) -> None:
    REC.phase("4 a $0 session after setup")
    s, _ = run_session(ctx, "C after-setup")
    st = s.mcp_servers()
    REC.check("C1 both deepmap servers connected (source: the project's .mcp.json)",
              all(st.get(n) == "connected" for n in SERVERS), st)
    REC.check("C2 the four tools are registered", set(TOOLS) <= set(s.tools()),
              sorted(set(TOOLS) - set(s.tools())))
    REC.check("C3 no plugin_errors and no mcp_server_errors", not s.plugin_errors() and not s.mcp_server_errors(),
              [s.plugin_errors(), s.mcp_server_errors()])
    ctxs = s.hook_contexts("SessionStart")
    REC.check("C4 the notify hook announces the READY graph", any(READY_NEEDLE in c for c in ctxs), ctxs)
    REC.check("C5 no 'NOT set up' or 'must NOT be used' note", not any(ONBOARD_NEEDLE in c or "must NOT be used" in c
                                                                      for c in ctxs), ctxs)
    ok, bad = hook_stderr_ok(s)
    REC.check("C6 no SessionStart hook stderr shows a failure", ok, bad)
    wait_until(lambda: not heals_running(), 300)


PROBE_SCRIPT = r'''
import asyncio, importlib.util, json, sys
spec = importlib.util.spec_from_file_location("smoke", sys.argv[1])
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)
url, tool, query, top_k = sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5])
try:
    res = asyncio.run(asyncio.wait_for(smoke._probe(url, tool, query, top_k), timeout=150))
    payload = res.get("payload")
    rows = payload.get("result") if isinstance(payload, dict) else payload
    print(json.dumps({"tools": res.get("tools"), "rows": rows}, default=str))
except BaseException as exc:
    print(json.dumps({"error": smoke._leaf_errors(exc) if hasattr(smoke, "_leaf_errors") else repr(exc)}))
'''


def mcp_call(ctx: Ctx, url: str, tool: str, query: str, top_k: int) -> dict:
    script = ctx.report / "bin" / "mcp_probe.py"
    script.parent.mkdir(parents=True, exist_ok=True)
    if not script.is_file():
        script.write_text(PROBE_SCRIPT, encoding="utf-8")
    smoke = Path(ctx.install_path) / "tests" / "mcp_http_smoketest.py"
    res = sh(ctx, "mcp.log", [str(ctx.venv_python()), "-X", "utf8", str(script), str(smoke), url, tool, query,
                              str(top_k)], env=cli_env(ctx), timeout=240)
    lines = [ln for ln in res.out.strip().splitlines() if ln.strip().startswith("{")]
    try:
        return json.loads(lines[-1])
    except (IndexError, ValueError):
        return {"error": f"rc={res.rc} {_short(res.out + res.err, 600)}"}


def phase_tools(ctx: Ctx) -> None:
    REC.phase("5 tools: the installed smoketest, exact answers over MCP, the CLI, doctor")
    mcp = (read_json(ctx.project / ".mcp.json") or {}).get("mcpServers") or {}
    surl = str((mcp.get("deepmap-search") or {}).get("url") or "")
    eurl = str((mcp.get("deepmap-embed") or {}).get("url") or "")
    vpy = str(ctx.venv_python())
    smoke = str(Path(ctx.install_path) / "tests" / "mcp_http_smoketest.py")
    for tool, url, q in (("deepmap_search", surl, "email the customer their order confirmation"),
                         ("deepmap_impact", surl, "validate_order"),
                         ("deepmap_cypher", surl, "MATCH (f:Function) RETURN count(f) AS n"),
                         ("deepmap_embed", eurl, "order confirmation email")):
        r = sh(ctx, "smoketest.log", [vpy, "-X", "utf8", smoke, "--url", url, "--tool", tool, "--query", q,
                                      "--top-k", "30"], env=cli_env(ctx), timeout=240)
        REC.check(f"T0 the installed mcp_http_smoketest passes for {tool}", r.rc == 0 and "PASS" in r.out,
                  r.out[-500:] + r.err[-500:])
    first = mcp_call(ctx, surl, "deepmap_search", FX.SEARCH_EXPECT[0]["query"], 30)
    REC.check("T1 the search server lists deepmap_search, deepmap_impact and deepmap_cypher",
              {"deepmap_search", "deepmap_impact", "deepmap_cypher"} <= set(first.get("tools") or []), first)
    for exp in FX.SEARCH_EXPECT:
        got = first if exp is FX.SEARCH_EXPECT[0] else mcp_call(ctx, surl, "deepmap_search", exp["query"], 30)
        ok, detail = FX.check_search(got.get("rows"), exp) if "rows" in got else (False, got.get("error"))
        REC.check(f"T2 search '{exp['query']}' finds {exp['name']} ({exp['file']}) in the top {FX.SEARCH_TOP}",
                  ok, detail)
    for exp in FX.IMPACT_EXPECT:
        got = mcp_call(ctx, surl, "deepmap_impact", exp["symbol"], FX.IMPACT_LIMIT)
        ok, detail = FX.check_impact(got.get("rows"), exp) if "rows" in got else (False, got.get("error"))
        what = f"the honest {exp['note']} note" if exp.get("note") else ", ".join(exp["callers"][:3] + ["..."])
        REC.check(f"T3 impact {exp['symbol']}: {what}", ok, detail)
    for exp in FX.CYPHER_EXPECT:
        got = mcp_call(ctx, surl, "deepmap_cypher", exp["query"], 100)
        if exp.get("rejected"):   # a rejection, not any failure (a dead server is an error too)
            REC.check("T4 cypher: a write is rejected as a tool error (isError)", "rows" not in got
                      and "isError" in str(got.get("error")), got)
            continue
        ok, detail = FX.check_cypher(got.get("rows"), exp) if "rows" in got else (False, got.get("error"))
        REC.check(f"T4 cypher {exp['id']}: {exp['query'][:70]}", ok, detail)
    got = mcp_call(ctx, eurl, "deepmap_embed", "order confirmation email", 1)
    REC.check("T5 the embed server lists deepmap_embed", "deepmap_embed" in (got.get("tools") or []), got.get("tools"))
    vec = got.get("rows")
    REC.check(f"T5 deepmap_embed returns a {FX.EMBED_DIM}-dimension vector", isinstance(vec, list)
              and len(vec) == FX.EMBED_DIM, f"{type(vec).__name__} {len(vec) if isinstance(vec, list) else ''}")
    cli = [sys.executable, "-X", "utf8", str(installed_build(ctx) / "deepmap_cli.py")]
    for args, want_rc, needle, label in (
            (["status"], 0, None, "status exits 0"),
            (["search", FX.SEARCH_EXPECT[0]["query"]], 0, FX.SEARCH_EXPECT[0]["name"], "search finds it"),
            (["impact", "validate_order"], 0, "checkout", "impact names checkout"),
            (["cypher", "CREATE (x:SanityProbe) RETURN x"], 2, None, "cypher rejects a write with exit 2")):
        r = sh(ctx, "cli.log", cli + args, cwd=str(ctx.project), env=cli_env(ctx), timeout=600)
        REC.check(f"T6 CLI {label}", r.rc == want_rc and (needle is None or needle in r.out),
                  f"rc={r.rc} {r.out[-600:]} {r.err[-300:]}")
    res, _ = run_block(ctx, "doctor", "doctor.log", timeout=900)
    if res is None:
        REC.check("D1 doctor.md has a bash block", False)
        return
    REC.check("D1 the installed doctor.md block exits 0 with verdict READY",
              res.rc == 0 and "[deepmap] verdict: READY" in res.out, res.out[-1500:] + res.err[-300:])
    REC.info("doctor names the .sql files", ".sql" in res.out)


# ------------------------------------------------------------------------------------------------------------ hooks
def hook_payload(kind: str, proj: Path, session: str) -> bytes:
    base = {"session_id": session, "transcript_path": str(proj / ".deepmap" / "sanity-transcript.jsonl"),
            "cwd": str(proj)}
    if kind in ("heal", "notify"):
        base.update(hook_event_name="SessionStart", source="startup")
    elif kind == "nudge":
        base.update(hook_event_name="PreToolUse", tool_name="Grep", tool_input={"pattern": "checkout"})
    elif kind == "edit":
        f = str(proj / FX.STALE_EDIT[0])
        base.update(hook_event_name="PostToolUse", tool_name="Edit",
                    tool_input={"file_path": f, "old_string": "a", "new_string": "b"},
                    tool_response={"filePath": f, "success": True})
    else:
        base.update(hook_event_name="PostToolUse", tool_name="mcp__deepmap-search__deepmap_search",
                    tool_input={"query": "checkout"}, tool_response=[{"type": "text", "text": "[]"}])
    return json.dumps(base).encode("utf-8")


def heal_headers(proj: Path) -> list:
    out = []
    for name in ("heal.log.1", "heal.log"):
        try:
            text = (proj / ".deepmap" / name).read_text(encoding="utf-8", errors="replace")
            out += [ln for ln in text.splitlines() if ln.startswith("--- ") and "DeepMap SessionStart heal:" in ln]
        except OSError:
            pass
    return out


def shells(ctx: Ctx) -> list:
    out = [("bash", "posix", ctx.bash)] if ctx.bash else []
    if WINDOWS and ctx.ps51:
        out.append(("powershell.exe 5.1", "ps", ctx.ps51))
    return out


def fire_hook(ctx: Ctx, kind: str, shell: tuple, session: str, *, auto: str = "0"):
    """Run the hooks.json command for `kind` under `shell` with a real payload. (CliResult, contexts)."""
    hj = json.loads((Path(ctx.install_path) / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    cmd = next(c for _, _, c, _ in hook_commands(hj) if hook_kind(c) == kind)
    label, skind, exe = shell
    argv = hook_argv(skind, exe, hook_line(cmd, ctx.install_path, skind))
    env = ctx.user_env({"CLAUDE_PLUGIN_ROOT": str(ctx.install_path), "CLAUDE_PROJECT_DIR": str(ctx.project),
                        "DEEPMAP_AUTO_SYNC": auto})
    res = sh(ctx, "hooks.log", argv, cwd=str(ctx.project), env=env, timeout=60,
             stdin=hook_payload(kind, ctx.project, session))
    return res, LC.additional_contexts(res.out)


def judge_hook(ctx: Ctx, cid: str, kind: str, shell: tuple, session: str, res, positive: bool, detail) -> None:
    v = LC.stderr_verdict(res.err)
    REC.check(cid, positive and not v["fatal"] and res.rc is not None,
              f"positive={positive} fatal={v['fatal']} rc={res.rc} {res.secs}s {detail} err={_short(res.err, 300)}")
    if res.secs <= HOOK_BUDGET_S:
        REC.check(f"{cid}: within its {HOOK_BUDGET_S:.0f} s budget", True)
        return
    again, _ = fire_hook(ctx, kind, shell, session + "-timing")      # a cold first run (Defender): once more
    REC.info(f"{cid}: cold run (s)", res.secs)
    REC.check(f"{cid}: within its {HOOK_BUDGET_S:.0f} s budget (second run)", again.secs <= HOOK_BUDGET_S,
              f"{res.secs}s then {again.secs}s")


def phase_hooks(ctx: Ctx) -> None:
    REC.phase("6 hooks as hooks.json spells them (positive signals only)")
    for shell in shells(ctx):
        lab = shell[0]
        tag = re.sub(r"\W+", "-", lab)
        before = set(heal_headers(ctx.project))
        res, ctxs = fire_hook(ctx, "heal", shell, f"sanity-{tag}-heal")
        new = [h for h in heal_headers(ctx.project) if h not in before]
        pos = len(new) == 1 and ("[setup]" in new[0] or "[DEEPMAP_PYTHON]" in new[0]) and not res.out.strip()
        judge_hook(ctx, f"H1 {lab}: the heal hook spawns one heal under setup's interpreter", "heal", shell,
                   f"sanity-{tag}-heal", res, pos, f"new headers {new}")
        wait_until(lambda: not heals_running(), 300)
        for kind, needle, cid in (("notify", READY_NEEDLE, "H2 {}: the notify hook announces the READY graph"),
                                  ("nudge", NUDGE_NEEDLE, "H3 {}: the Grep nudge speaks"),
                                  ("edit", EDIT_NEEDLE, "H4 {}: the edit hook says the graph may be stale")):
            res, ctxs = fire_hook(ctx, kind, shell, f"sanity-{tag}-{kind}")
            judge_hook(ctx, cid.format(lab), kind, shell, f"sanity-{tag}-{kind}", res,
                       any(needle in c for c in ctxs), f"contexts {ctxs}")
    stale_case(ctx)
    wait_until(lambda: not heals_running(), 300)


def stale_case(ctx: Ctx) -> None:
    """The tool-result freshness hook must SPEAK: auto-sync on, a source file edited, the edit hook records it, the
    notify hook announces a background refresh for the session, none starts; after deepmap_hook_state's 120 s the
    hook reports that the announced refresh did not start."""
    rel, extra = FX.STALE_EDIT
    path = ctx.project / rel
    original = path.read_bytes()
    path.write_bytes(original + extra.encode("utf-8"))
    record = ctx.project / ".deepmap" / "last_source_edit.json"
    told = {}
    try:
        for shell in shells(ctx):
            lab, tag = shell[0], re.sub(r"\W+", "-", shell[0])
            sid = f"sanity-{tag}-stale"
            t0 = time.time()
            res, ctxs = fire_hook(ctx, "edit", shell, sid, auto="1")
            rec_ok = record.is_file() and record.stat().st_mtime >= t0 - 2
            judge_hook(ctx, f"H5 {lab}: with auto-sync on, the edit hook records the edit "
                            "(.deepmap/last_source_edit.json)", "edit", shell, sid, res,
                       rec_ok and any(EDIT_NEEDLE in c for c in ctxs), f"record={rec_ok} contexts {ctxs}")
            res, ctxs = fire_hook(ctx, "notify", shell, sid, auto="1")
            judge_hook(ctx, f"H6 {lab}: with auto-sync on and an edited file, notify announces a background refresh",
                       "notify", shell, sid, res, any("in the background" in c for c in ctxs), f"contexts {ctxs}")
            told[sid] = (shell, time.time())
        if told:
            wait_s = max(t for _, t in told.values()) + STALE_WAIT_S - time.time()
            if wait_s > 0:
                time.sleep(wait_s)
        for sid, (shell, _) in told.items():
            res, ctxs = fire_hook(ctx, "freshness", shell, sid, auto="1")
            judge_hook(ctx, f"H7 {shell[0]}: the tool-result freshness hook speaks in the stale case", "freshness",
                       shell, sid + "-t", res, any(c.startswith("DeepMap") for c in ctxs), f"contexts {ctxs}")
    finally:
        path.write_bytes(original)


# -------------------------------------------------------------------------------------------------------- lifecycle
def node_count(ctx: Ctx):
    r = sh(ctx, "cli.log", [sys.executable, "-X", "utf8", str(installed_build(ctx) / "deepmap_cli.py"), "cypher",
                            "--json", "MATCH (n) WHERE NOT n:_DeepMapMeta RETURN count(n) AS nodes"],
           cwd=str(ctx.project), env=cli_env(ctx), timeout=300)
    try:
        return int(json.loads(r.out[r.out.index("{"):])["rows"][0]["nodes"]) if r.rc == 0 else None
    except (ValueError, KeyError, IndexError, TypeError):
        return None


def ports(ctx: Ctx) -> dict:
    mcp = (read_json(ctx.project / ".mcp.json") or {}).get("mcpServers") or {}
    out = {}
    for n in SERVERS:
        m = re.search(r":(\d+)/", str((mcp.get(n) or {}).get("url") or ""))
        out[n] = int(m.group(1)) if m else None
    r, o = probe(ctx, str(ctx.venv_python()), f'''
    import port_registry as PR
    rt = PR.resolve_neo4j_route({str(ctx.project)!r})
    print(json.dumps({{"bolt": rt.bolt, "uri": rt.uri}}))
    ''')
    out["bolt"] = (r or {}).get("bolt")
    return out


def all_up(p: dict) -> bool:
    return all(port_open(v) for v in p.values() if v) and all(p.values())


def all_down(p: dict) -> bool:
    return all(v and not port_open(v) for v in p.values())


# what the lifecycle stops before a session must heal: the MCP servers too (`stop` stops Neo4j only), so the heal's
# server start runs as it does after a reboot, and is not bypassed by servers that never went away
LIFECYCLE_STOP = ("mcp-stop", "stop")


def _same_path(a, b) -> bool:
    def norm(x):
        return os.path.normcase(os.path.normpath(str(x).replace("\\", "/")))
    return norm(a) == norm(b)


def heal_finished_since(home, t0: float, project):
    """The newest heal status (<home>/run/heal-<key>.json, which provision.py heal writes as it ENDS) written at or
    after t0 for this project (its `root`), else None. A heal that was ended before it finished writes none."""
    best = None
    run = Path(home) / "run"
    for f in sorted(run.glob("heal-*.json")) if run.is_dir() else []:
        rec = read_json(f)
        if not isinstance(rec, dict):
            continue
        try:
            ts = float(rec.get("ts_epoch"))
        except (TypeError, ValueError):
            continue
        if ts < t0 or (rec.get("root") and not _same_path(rec["root"], project)):
            continue
        if best is None or ts > float(best["ts_epoch"]):
            best = rec
    return best


def revive_verdict(cli_up: bool, finished, heal_alive: bool) -> tuple:
    """L4, the heal a $0 session's SessionStart hook started: (status, detail).
    pass         Neo4j and both servers came back.
    fail         they did not, and that heal FINISHED (it wrote its heal status after the session started) or still
                 runs after the wait: a product failure, never the open question.
    not-covered  they did not, no heal finished and none runs: the CLI's teardown may have ended the heal it started
                 (UNSETTLED_HEAL, settled on the first runs per OS). L5, the hooks.json heal run directly, then
                 gates."""
    if cli_up:
        return "pass", ""
    if isinstance(finished, dict):
        return "fail", (f"the heal the session started finished and left a port closed: neo4j_ok="
                        f"{finished.get('neo4j_ok')!r} mcp_ok={finished.get('mcp_ok')!r} "
                        f"reason={finished.get('reason')!r}")
    if heal_alive:
        return "fail", f"the heal the session started still runs after {REVIVE_WAIT_S} s with a port closed"
    return "not-covered", (f"{UNSETTLED_HEAL} No heal finished and none runs after {REVIVE_WAIT_S} s, so the "
                           "session's teardown may have ended it; L5 (the hook run directly) decides.")


def phase_lifecycle(ctx: Ctx) -> None:
    REC.phase("7 lifecycle: mcp-stop + stop, then a $0 session heals")
    p = ports(ctx)
    REC.info("ports", p)
    if not REC.check("L0 this project's bolt and MCP ports are known", all(p.values()), p):
        return
    before = node_count(ctx)
    prov = [sys.executable, "-X", "utf8", str(installed_build(ctx) / "provision.py")]
    for verb in LIFECYCLE_STOP:
        r = sh(ctx, "lifecycle.log", prov + [verb, str(ctx.project)], cwd=str(ctx.project), env=cli_env(ctx),
               timeout=300)
        REC.check(f"L1 provision.py {verb} exits 0", r.rc == 0, r.out[-600:] + r.err[-300:])
    down = wait_until(lambda: all_down(p), 90)
    if not REC.check("L1 the bolt port and both MCP ports are closed (as after a reboot)", down,
                     {k: port_open(v) for k, v in p.items()}):
        return      # a heal could not prove anything: something still answers on this project's ports
    r = sh(ctx, "cli.log", [sys.executable, "-X", "utf8", str(installed_build(ctx) / "deepmap_cli.py"), "status"],
           cwd=str(ctx.project), env=cli_env(ctx), timeout=300)
    REC.check("L2 status exits 3 while Neo4j is down", r.rc == 3, f"rc={r.rc} {r.out[-300:]}")
    hb = set(heal_headers(ctx.project))
    t0 = time.time()
    s, _ = run_session(ctx, "L heal-session")
    new = [h for h in heal_headers(ctx.project) if h not in hb]
    REC.check("L3 the session's SessionStart heal hook ran (one new heal.log header)", len(new) == 1, new)
    up = wait_until(lambda: all_up(p), REVIVE_WAIT_S, step=10)
    finished = heal_finished_since(ctx.home, t0, ctx.project)
    status, why = revive_verdict(up, finished, bool(heals_running()))
    REC.info("L4 the heal status written after the session started", finished)
    REC.record("L4 the heal started by the $0 session revived Neo4j and both servers", status,
               why or {k: port_open(v) for k, v in p.items()})
    if not up:
        wait_until(lambda: not heals_running(), 300)
        res, _ = fire_hook(ctx, "heal", ("bash", "posix", ctx.bash), "sanity-direct-revive")
        up = wait_until(lambda: all_up(p), REVIVE_WAIT_S, step=10)
        REC.check("L5 the heal hook run directly (as hooks.json spells it) revives Neo4j and both servers", up,
                  {k: port_open(v) for k, v in p.items()})
    after = node_count(ctx)
    REC.check("L6 the graph survived stop + heal (same node count)", before is not None and before == after,
              f"before={before} after={after}")
    wait_until(lambda: not heals_running(), 300)
    s, _ = run_session(ctx, "L after-heal")
    REC.check("L7 a new session connects both servers again", all(s.mcp_servers().get(n) == "connected"
                                                                  for n in SERVERS), s.mcp_servers())
    wait_until(lambda: not heals_running(), 300)


# -------------------------------------------------------------------------------------------------------- uninstall
def recorded_pids(home: Path) -> dict:
    """{pid: where} from DeepMap's process-identity records only: the MCP servers' *.owner and *.pid files, Neo4j's
    pid file, its neo4j.pid.identity and neo4j.launcher.json (status files such as heal-<key>.json are not identity
    records: a pid they name is long gone and may be reused)."""
    out: dict = {}

    def add(pid, where):
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            return
        if pid > 0:
            out.setdefault(pid, where)

    runs = [home / "run"] + sorted(home.glob("neo4j/neo4j-community-*/run")) + sorted(home.glob("instances/*/run"))
    for d in runs:
        for f in sorted(d.glob("*")) if d.is_dir() else []:
            if f.suffix == ".pid":
                try:
                    add(f.read_text(encoding="utf-8").strip(), f.name)
                except OSError:
                    pass
            elif f.suffix in (".owner", ".identity") or f.name == "neo4j.launcher.json":
                rec = read_json(f)
                if isinstance(rec, dict):
                    add(rec.get("pid"), f.name)
                    add(rec.get("launcher_pid"), f.name)
    return out


_DEEPMAP_CMD = re.compile(r"deepmap|neo4j|provision\.py|graphify", re.I)


def live_recorded(pids: dict, table: list) -> dict:
    """The recorded pids that are alive AND still look like DeepMap's (a reused pid running something else is not a
    leftover). {pid: (where, command)}."""
    alive = {p["pid"]: p for p in table}
    return {pid: (where, alive[pid]["cmd"][:160]) for pid, where in pids.items()
            if pid in alive and _DEEPMAP_CMD.search(alive[pid]["cmd"] + " " + alive[pid]["exe"])}


def phase_uninstall(ctx: Ctx) -> None:
    REC.phase("8 uninstall")
    if not ctx.install_path:
        REC.skip("uninstall", "the plugin was never installed")
        return
    # a heal a session started could revive what stop is about to stop
    REC.check("U0 no SessionStart heal is still running before the stop", wait_until(lambda: not heals_running(), 300),
              heals_running())
    pids = recorded_pids(ctx.home)
    REC.info("recorded DeepMap pids", pids)
    prov = [sys.executable, "-X", "utf8", str(installed_build(ctx) / "provision.py")]
    for verb in ("mcp-stop", "stop"):
        r = sh(ctx, "uninstall.log", prov + [verb, str(ctx.project)], cwd=str(ctx.project), env=cli_env(ctx),
               timeout=300)
        if ctx.setup_ok:
            REC.check(f"U1 provision.py {verb} exits 0", r.rc == 0, r.out[-500:] + r.err[-300:])
        else:
            REC.info(f"{verb} after a setup that did not finish (rc)", [r.rc, _short(r.out + r.err, 300)])
    listed = (read_json(ctx.config_dir / "plugins" / "installed_plugins.json") or {}).get("plugins") or {}
    if ctx.plugin_id in listed:
        r = sh(ctx, "uninstall.log", [ctx.claude, "plugin", "uninstall", ctx.plugin_id, "--scope", "user"],
               timeout=300)
        if r.rc != 0 and re.search(r"unknown option|unexpected argument", r.out + r.err, re.I):
            r = sh(ctx, "uninstall.log", [ctx.claude, "plugin", "uninstall", ctx.plugin_id], timeout=300)
        REC.check(f"U2 claude plugin uninstall {ctx.plugin_id}", r.rc == 0, r.out[-500:] + r.err[-300:])
    else:
        REC.info("U2 the plugin was already uninstalled", ctx.plugin_id)
    ip = read_json(ctx.config_dir / "plugins" / "installed_plugins.json") or {}
    REC.check("U2 installed_plugins.json no longer lists the plugin", not (ip.get("plugins") or {}).get(ctx.plugin_id),
              (ip.get("plugins") or {}).get(ctx.plugin_id))
    me = os.getpid()
    markers = [str(ctx.install_path), str(ctx.home / "neo4j"), str(ctx.home / "venv"), str(ctx.home / "instances")]

    def leftovers():
        table = process_table()
        stray = [(p["pid"], p["cmd"][:160]) for p in table if p["pid"] != me and any(
            m.lower() in (p["cmd"] + " " + p["exe"]).lower() for m in markers)]
        return live_recorded(pids, table), stray

    wait_until(lambda: leftovers() == ({}, []), 120, step=5)
    rec_alive, stray = leftovers()
    REC.check("U3 no recorded DeepMap pid is still alive", not rec_alive, rec_alive)
    REC.check("U3 no process runs from the plugin, DeepMap's venv or its Neo4j", not stray, stray)
    s, _ = run_session(ctx, "U after-uninstall")
    REC.check("U4 a new session lists no deep-map plugin", not s.plugin(ctx.plugin_id),
              [p.get("source") for p in s.plugins()])
    REC.check("U4 ... and no hook output from DeepMap", not any("deepmap" in str(h.get("stdout") or "").lower()
                                                               or "deepmap" in str(h.get("stderr") or "").lower()
                                                               for h in s.hook_responses()))
    ctx.uninstalled = True
    write_state(ctx)


# --------------------------------------------------------------------------------------------------------- artifacts
_IGNORE = shutil.ignore_patterns("*.lock", "*token*", "*secret*", "*auth*", "*.key", "*.jks", "*.pem")


def copy_artifacts(ctx: Ctx) -> None:
    dst = ctx.report / "artifacts"
    for src, name in ((ctx.project / ".deepmap", "project-deepmap"), (ctx.home / "run", "deepmap-home-run")):
        if src.is_dir():
            try:
                shutil.copytree(src, dst / name, dirs_exist_ok=True, ignore=_IGNORE)
            except (shutil.Error, OSError):
                pass
    for f in [ctx.project / ".mcp.json", ctx.config_dir / "plugins" / "installed_plugins.json",
              ctx.config_dir / "plugins" / "known_marketplaces.json", ctx.home / "registry.json"]:
        try:
            if f.is_file():
                (dst / "files").mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dst / "files" / f.name)
        except OSError:
            pass
    for f in list(ctx.home.glob("*.log")) + list(ctx.home.glob("instances/*/*.log")) + \
            list(ctx.home.glob("neo4j/neo4j-community-*/logs/*.log")):
        try:
            (dst / "logs").mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dst / "logs" / f"{f.parent.name}-{f.name}")
        except OSError:
            pass


def finish(ctx: Ctx, prior=None) -> int:
    REC.phase("9 artifacts")
    try:
        copy_artifacts(ctx)
    except Exception:  # noqa: BLE001
        REC.info("copying the artifacts failed", traceback.format_exc()[-600:])
    secrets = [os.environ.get(k) for k in LC.KEY_ENV if os.environ.get(k)]
    hits = scan_and_redact(ctx.report, secrets)
    REC.check("R1 no key or secret in any report file (redacted where found)", not hits, hits)
    return write_report(ctx.report, merge_results(prior, REC.results), ctx.meta(), summary=True)


def guarded(label: str, fn, *args):
    try:
        return fn(*args)
    except Exception:  # noqa: BLE001
        REC.check(f"{label}: the harness ran without an error", False, traceback.format_exc())
        return None


def live(args) -> int:
    ctx = Ctx(args)
    ctx.logs.mkdir(parents=True, exist_ok=True)
    # a cleanup run after a cut-off main run keeps that run's rows: every snapshot and the final report merge onto them
    prior = (read_json(ctx.report / "report.json") or {}).get("results") if args.only == "cleanup" else None
    REC.on_phase = lambda: write_report(ctx.report, merge_results(prior, REC.results), ctx.meta())
    if args.only == "cleanup":
        st = read_json(Path(args.resume or ctx.report) / "state.json") or {}
        if not st.get("install_path") or st.get("uninstalled"):   # idempotent: never a second uninstall
            print("[cleanup] nothing to clean: " + ("the main run already uninstalled the plugin"
                                                    if st.get("uninstalled") else "the main run installed nothing"))
            return 0
        ctx.claude = ctx.claude or st.get("claude") or ""
        ctx.config_dir = Path(st.get("config_dir") or ctx.config_dir)
        ctx.project = Path(st.get("project") or ctx.project)
        ctx.marketplace = st.get("marketplace") or ctx.marketplace
        ctx.install_path = Path(st["install_path"]) if st.get("install_path") else None
        ctx.setup_ok = bool(st.get("setup_ok"))
        if not ctx.claude:
            REC.check("cleanup: the state of the main run was found (state.json)", False, str(ctx.report))
        else:
            guarded("8 uninstall", phase_uninstall, ctx)
        return finish(ctx, prior)
    if not ctx.claude:
        print("[REFUSED] --claude <path to the installed Claude Code CLI> is required for a live run")
        return 2
    try:
        guarded("0 facts", phase_facts, ctx)
        if guarded("1 install", phase_install, ctx) and guarded("2 before", phase_before, ctx):
            if guarded("3 setup", phase_setup, ctx):
                guarded("4 after", phase_after, ctx)
                guarded("5 tools", phase_tools, ctx)
                guarded("6 hooks", phase_hooks, ctx)
                guarded("7 lifecycle", phase_lifecycle, ctx)
            else:
                REC.skip("phases 4-7", "setup did not pass (logs/setup.log)")
    finally:
        guarded("8 uninstall", phase_uninstall, ctx)
    return finish(ctx)


# ============================================================================================================ dry run
def dry_run(args) -> int:
    """The pure parts only: no claude, no DeepMap code, no ~/.deepmap. For a developer machine and the gate. Exit 2,
    before anything is written, when --project-dir already holds anything (project A overwrites files there, sets
    core.autocrlf and commits everything: never a developer's checkout)."""
    if args.project_dir:
        refused = FX.occupied(args.project_dir)
        if refused:
            print(f"[REFUSED] --dry-run writes project A into --project-dir and commits it there, so it must be a path "
                  f"that does not exist yet (or an empty directory): {refused}.")
            return 2
    report = Path(args.report or tempfile.mkdtemp(prefix="deepmap-sanity-dry-"))
    logs = report / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    root = Path(args.plugin_root or REPO)
    REC.phase("D1 fixture")
    proj = Path(args.project_dir) if args.project_dir else Path(tempfile.mkdtemp(prefix="dm-dry-")) / "polyshop"
    if shutil.which("git"):
        made = FX.make_project(proj)
        REC.check("D1 project A generated and committed", bool(made.get("head")) and not made.get("error"), made)
    else:
        FX.write_files(proj)
        REC.skip("D1 project A committed", "git is not on PATH")
    REC.check("D1 project A's bytes match the fixture digest", FX.digest(proj) == FX.digest())
    REC.phase("D2 command text")
    for name in ("setup", "doctor"):
        block, crlf = command_block((root / "commands" / f"{name}.md").read_bytes())
        text = substitute(block or "", root, "")
        (logs / f"{name}-command.txt").write_text(text, encoding="utf-8")
        REC.check(f"D2 commands/{name}.md: its first bash block substitutes cleanly", bool(block)
                  and "${CLAUDE_PLUGIN_ROOT}" not in text and "$ARGUMENTS" not in text, text)
        REC.info(f"commands/{name}.md CRLF line endings", crlf)
    REC.phase("D3 hooks")
    hj = json.loads((root / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    cmds = hook_commands(hj)
    lines = []
    for event, matcher, cmd, _t in cmds:
        for kind in ("posix", "ps"):
            lines.append(f"{event} [{matcher}] {kind}: {hook_argv(kind, kind, hook_line(cmd, root, kind))}")
    (logs / "hook-commands.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    REC.check("D3 every hooks.json command maps to a hook kind with a positive signal",
              len(cmds) == 5 and all(hook_kind(c) for _, _, c, _ in cmds), [c for _, _, c, _ in cmds])
    REC.phase("D4 recorded sessions")
    for f in sorted((HERE / "fixtures" / "live_e2e").glob("*.jsonl")):
        s = LC.parse_stream(f.read_bytes())
        REC.info(f.name, {"version": s.version(), "mcp": s.mcp_servers(), "cost": s.cost(),
                          "contexts": len(s.hook_contexts())})
    REC.info("opt-in in this environment", list(opt_in(os.environ)))
    return write_report(report, REC.results, {"mode": "dry-run", "job": "dry-run", "os": platform.platform(),
                                              "python": sys.version.split()[0]})


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--claude", default=None, help="path to the installed Claude Code CLI (the workflow installs it)")
    ap.add_argument("--claude-version", default=None, help="the pinned CLI version every session must report")
    ap.add_argument("--config-dir", default=None, help="CLAUDE_CONFIG_DIR (default ~/.claude; keep it short)")
    ap.add_argument("--repo", default=DEFAULT_REPO, help="the GitHub marketplace repository")
    ap.add_argument("--ref", default="", help="the ref to install (`marketplace add <repo>#<ref>`)")
    ap.add_argument("--expect-version", default=None, help="the plugin.json version the ref must install")
    ap.add_argument("--expect-sha", default=None, help="the commit the ref must resolve to")
    ap.add_argument("--profile", default="linux", choices=("linux", "windows-field"))
    ap.add_argument("--job", default=None, help="the job label in the report")
    ap.add_argument("--project-dir", default=None, help="where project A is generated (must not exist; --dry-run also "
                                                         "takes an empty directory)")
    ap.add_argument("--report", default=None, help="directory for report.json, report.md, logs/ and artifacts/")
    ap.add_argument("--setup-timeout", type=float, default=7200.0, help="seconds (setup.md's background limit)")
    ap.add_argument("--only", default="all", choices=("all", "cleanup"))
    ap.add_argument("--resume", default=None, help="a report directory to merge into (with --only cleanup)")
    ap.add_argument("--dry-run", action="store_true", help="the pure parts only; no claude, no DeepMap state")
    ap.add_argument("--plugin-root", default=None, help="--dry-run: read commands/ and hooks/ from here")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
    args = parse_args(argv)
    if args.dry_run:
        return dry_run(args)
    ok, why = opt_in(os.environ)
    if not ok:
        print(f"[REFUSED] the live install sanity check runs only on a throwaway GitHub-hosted runner or a proven "
              f"container ({why}). It uses the default ~/.deepmap and ~/.claude and starts Neo4j and the MCP servers "
              f"on the base ports. Use --dry-run for the pure parts.")
        return 2
    if args.only == "cleanup" and not args.report:
        args.report = args.resume
    return live(args)


if __name__ == "__main__":
    sys.exit(main())
