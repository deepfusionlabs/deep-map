#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Hermetic lock on the install sanity harness (tests/live_e2e.py, tests/live_claude.py, tests/live_e2e_fixture.py).

The harness itself runs only on a throwaway CI runner (it installs the plugin, starts Neo4j on the base ports and uses
the default ~/.deepmap). Its pure parts decide every verdict, so they are checked here, with no network, no Neo4j and
no Claude Code CLI:

  A  the stream-json parser on recorded, scrubbed sessions (tests/fixtures/live_e2e/): init (plugins, plugin_errors,
     slash commands, MCP servers and tools, apiKeySource, CLI version), SessionStart hook pairs and their
     additionalContext, the result's cost; blank lines, CRLF, a BOM, garbage and a cut last line are tolerated, an
     absent result is never read as $0
  B  the INSTALLED command text: the first ```bash block of commands/setup.md read as BYTES, identical from an LF and a
     CRLF copy (the CR count is recorded, never left in the text bash runs), ${CLAUDE_PLUGIN_ROOT} and $ARGUMENTS
     substituted as Claude Code does, setup.md's own python -> python3 rule; hooks.json's commands and their per-shell
     spelling (forward slashes for bash, native for PowerShell)
  C  the opt-in guard: CI=true AND GITHUB_ACTIONS=true AND RUNNER_ENVIRONMENT=github-hosted AND DEEPMAP_LIVE_TEST=1,
     or a container proven by /.dockerenv plus the launcher's token; anything else (a self-hosted runner included)
     refuses (exit 2, nothing created: the child runs with a temp home, so a ~/.deepmap or ~/.claude would be seen)
  D  the secret scan (an sk-ant- key, the value of a key in the job env) with redaction, and the allowlisted child env
     (no API key, no CI markers, CLAUDE_CONFIG_DIR and DISABLE_AUTOUPDATER set)
  E  report rendering and merging: PASS / FAIL / NOT COVERED / SKIP / PASS AFTER RETRY, exit codes, a resumed run
  F  the synthetic project A: deterministic bytes (and commit), the shapes it must hold, the expected-answer
     evaluators on synthetic tool payloads (each one also seen to FAIL), and the refusal to write into an occupied
     directory
  G  the one setup retry: only a transport error naming a pypi / pytorch / huggingface / neo4j URL, never a checksum
  H  hook stderr: a missing python OR python3 is tolerated beside a positive signal; a traceback, a PowerShell parse
     error, an unopenable hook file or both interpreters missing is fatal
  I  process tables (ps text, Win32_Process JSON) parsed without a shell
  J  hygiene of the new files: compile, stdlib-only imports, LF, lines <= 120, no em dash, no key-shaped literal (the
     fixture README and .github/workflows/sanity.yml too: LF, lines, em dash, key)
  K  the driver's --dry-run end to end (fixture, command text, hook spellings, report) with a temp home, touching no
     ~/.deepmap, ~/.claude or DEEPMAP_HOME; an existing non-empty --project-dir is refused (here and by the fixture's
     own entry point) with its bytes unchanged
  L  the verdict helpers of the live phases: setup's numbered progress, heal.log headers, the heal status a session's
     heal wrote, the lifecycle revive verdict (the open first-run question is NOT COVERED, the direct heal gates),
     the lifecycle stopping the MCP servers too, and which windows-field Java checks their preconditions allow

Usage: python tests/live_e2e_gates.py
"""
from __future__ import annotations

import ast
import importlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TESTS = REPO / "tests"
SAMPLES = TESTS / "fixtures" / "live_e2e"
sys.path.insert(0, str(TESTS))

PASSES: list = []
FAILS: list = []
NEW_FILES = ("tests/live_e2e.py", "tests/live_claude.py", "tests/live_e2e_fixture.py", "tests/live_e2e_gates.py")
TEXT_FILES = ("tests/fixtures/live_e2e/README.md", ".github/workflows/sanity.yml")
FAKE_KEY = "sk-" + "ant-" + "api03-" + "Zq" * 24          # built at run time: no key-shaped literal in this file


def check(name: str, ok: bool, detail="") -> bool:
    (PASSES if ok else FAILS).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok or detail == "" else f" -- {str(detail)[:900]}"))
    return ok


def _imp(name):
    try:
        return importlib.import_module(name)
    except Exception as exc:  # noqa: BLE001 - a missing module is a failed check, not a crash (RED first)
        check(f"tests/{name}.py imports", False, repr(exc))
        return None


def _sample(name: str) -> bytes:
    return (SAMPLES / name).read_bytes()


_SCRUB = ("CI", "GITHUB_ACTIONS", "DEEPMAP_LIVE_TEST", "RUNNER_ENVIRONMENT", "DEEPMAP_SANITY_TOKEN")
_HOME_VARS = ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "HOMEDRIVE", "HOMEPATH", "XDG_CONFIG_HOME",
              "XDG_CACHE_HOME", "XDG_DATA_HOME", "CLAUDE_CONFIG_DIR")
_HOME_DIRS = (".deepmap", ".claude")


def _isolated_env(home: Path) -> tuple:
    """(env, snapshot of the real home) for a child of the driver: no opt-in marker, and a user home of its own, so
    Path.home() / '.deepmap' and '.claude' (what the live run uses, whatever DEEPMAP_HOME says) resolve under `home`
    and a regression that writes there is seen."""
    home.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k.upper() not in _SCRUB + _HOME_VARS}
    env.update(HOME=str(home), USERPROFILE=str(home), APPDATA=str(home / "AppData" / "Roaming"),
               LOCALAPPDATA=str(home / "AppData" / "Local"), PYTHONIOENCODING="utf-8", DEEPMAP_AUTO_SYNC="0")
    real = Path.home()
    return env, {n: (real / n).exists() for n in _HOME_DIRS}


def _home_leftovers(home: Path, real_before: dict) -> list:
    """What a child must not leave: <home>/.deepmap or <home>/.claude, or a ~/.deepmap / ~/.claude in the REAL home
    that did not exist before it ran."""
    real = Path.home()
    return ([str(home / n) for n in _HOME_DIRS if (home / n).exists()]
            + [str(real / n) for n, was in real_before.items() if not was and (real / n).exists()])


def _child_home(env: dict) -> str:
    """Path.home() as a child with `env` sees it (proves the redirect works on this OS, so the checks can fail)."""
    p = subprocess.run([sys.executable, "-c", "from pathlib import Path; print(Path.home())"], env=env,
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    return p.stdout.strip()


def _tree_bytes(root: Path) -> dict:
    """{relative path: bytes} of every file under root, .git included (a dry run must change nothing there)."""
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


# ===================================================================================================== A parser
def gate_a_parser(LC) -> None:
    print("\n-- A: stream-json parser on recorded sessions --")
    s = LC.parse_stream(_sample("claude_p_noauth_onboarding.jsonl"))
    check("A1 init found, CLI version read", s.init is not None and s.version() == "2.1.314", s.version())
    pe = s.plugin("deep-map@deepfusionlabs-deep-map")
    check("A2 the deep-map plugin entry and its version", bool(pe) and pe.get("version") == "0.6.3", pe)
    check("A3 an absent plugin_errors key reads as no errors", s.plugin_errors() == [], s.plugin_errors())
    check("A4 apiKeySource none, cost exactly 0", s.api_key_source() == "none" and s.cost() == 0,
          (s.api_key_source(), s.cost()))
    check("A5 the deep-map slash commands are listed",
          {"deep-map:setup", "deep-map:doctor", "deep-map:refresh"} <= set(s.slash_commands()), s.slash_commands()[:12])
    hr = s.hook_responses("SessionStart")
    check("A6 two SessionStart hook responses, paired with their starts, exit_code 0",
          len(hr) == 2 and all(h.get("exit_code") == 0 and h.get("started") for h in hr), hr)
    ctx = s.hook_contexts("SessionStart")
    check("A7 the onboarding additionalContext is extracted (its stdout ends in CRLF)",
          len(ctx) == 1 and "installed but NOT set up" in ctx[0], ctx)
    check("A8 no MCP servers, no mcp tools", s.mcp_servers() == {} and not [t for t in s.tools() if
                                                                               t.startswith("mcp__")], s.mcp_servers())
    check("A9 the result is the not-logged-in ending",
          bool(s.result) and "Not logged in" in str(s.result.get("result")), s.result)

    c = LC.parse_stream(_sample("claude_p_mcp_connected.jsonl"))
    check("A10 a project .mcp.json server reads connected and its tool is registered",
          c.mcp_servers() == {"probe-live": "connected"} and "mcp__probe-live__probe_echo" in c.tools(),
          (c.mcp_servers(), [t for t in c.tools() if t.startswith("mcp__")]))
    f = LC.parse_stream(_sample("claude_p_mcp_failed.jsonl"))
    check("A11 a dead port reads failed and registers no tool",
          f.mcp_servers() == {"probe-dead": "failed"} and not [t for t in f.tools() if t.startswith("mcp__")],
          f.mcp_servers())
    d = LC.parse_stream(_sample("claude_p_no_project_mcp.jsonl"))
    check("A12 an inline plugin is found by name too", bool(d.plugin("deep-map@inline")), d.plugins())

    raw = _sample("claude_p_noauth_onboarding.jsonl")
    crlf = b"\xef\xbb\xbf" + raw.replace(b"\n", b"\r\n")
    noisy = b"\n\n[warn] something odd\n" + crlf + b'{"type":"system","subtype":"brand_new_event","x":1}\n{"type": "res'
    n = LC.parse_stream(noisy)
    check("A13 BOM + CRLF + blank + garbage + an unknown event + a cut last line: same init, hooks and cost",
          n.version() == "2.1.314" and len(n.hook_responses("SessionStart")) == 2 and n.cost() == 0
          and n.bad_lines == 2, (n.version(), n.bad_lines, n.cost()))
    lines = [ln for ln in raw.decode("utf-8").splitlines() if '"type": "result"' not in ln]
    nr = LC.parse_stream("\n".join(lines))
    check("A14 a session with no result event never reads as $0", nr.result is None and nr.cost() is None, nr.cost())

    init = json.loads([ln for ln in raw.decode("utf-8").splitlines() if '"subtype": "init"' in ln][0])
    init["plugin_errors"] = [{"plugin": "deep-map", "error": "manifest invalid"}]
    init["mcp_server_errors"] = [{"name": "deepmap-search", "error": "x"}]
    e = LC.parse_stream(json.dumps(init))
    check("A15 non-empty plugin_errors and mcp_server_errors are surfaced",
          len(e.plugin_errors()) == 1 and len(e.mcp_server_errors()) == 1, (e.plugin_errors(), e.mcp_server_errors()))
    check("A16 version_of reads `claude --version`", LC.version_of("2.1.317 (Claude Code)\n") == "2.1.317"
          and LC.version_of("garbage") is None)
    two = '{"hookSpecificOutput": {"additionalContext": "one"}}\r\n{"hookSpecificOutput": {"additionalContext": "two"}}'
    check("A17 additional_contexts reads one or several JSON objects, ignores non-JSON",
          LC.additional_contexts(two) == ["one", "two"] and LC.additional_contexts("not json") == []
          and LC.additional_contexts("") == [], LC.additional_contexts(two))
    # a result whose cost is absent, null or not a number is never read as $0 (every "$0 session" check rests on it)
    res = {"type": "result", "subtype": "success", "is_error": True, "result": "Not logged in"}
    odd = {"absent": None, "null": {"total_cost_usd": None}, "the string 0": {"total_cost_usd": "0"},
           "true": {"total_cost_usd": True}, "false": {"total_cost_usd": False}, "a list": {"total_cost_usd": [0]}}
    got = {k: LC.parse_stream(json.dumps(dict(res, **(v or {})))).cost() for k, v in odd.items()}
    check("A18 a result with total_cost_usd absent, null, \"0\", true, false or a list has no cost (never $0)",
          all(v is None for v in got.values()), got)
    zero = [LC.parse_stream(json.dumps(dict(res, total_cost_usd=z))).cost() for z in (0, 0.0)]
    paid = LC.parse_stream(json.dumps(dict(res, total_cost_usd=0.0123))).cost()
    check("A19 0 and 0.0 read as exactly 0, a real cost as itself", zero == [0, 0] and paid == 0.0123, (zero, paid))


# ===================================================================================================== B command text
def gate_b_command_text(E) -> None:
    print("\n-- B: the installed command text, LF and CRLF --")
    md = (REPO / "commands" / "setup.md").read_bytes()
    lf = md.replace(b"\r\n", b"\n")
    cr = lf.replace(b"\n", b"\r\n")
    b_lf, n_lf = E.command_block(lf)
    b_cr, n_cr = E.command_block(cr)
    check("B1 the first bash block is found in setup.md", bool(b_lf) and "provision.py" in b_lf and " setup " in b_lf,
          b_lf)
    check("B2 a CRLF copy gives the byte-identical block (no CR left), and its CR count is recorded",
          b_cr == b_lf and "\r" not in (b_cr or "") and n_lf == 0 and n_cr == cr.count(b"\r\n"), (n_lf, n_cr))
    root_win = r"C:\Users\runneradmin\.claude\plugins\cache\deepfusionlabs-deep-map\deep-map\0.7.0"
    text = E.substitute(b_cr, root_win, "")
    want = "C:/Users/runneradmin/.claude/plugins/cache/deepfusionlabs-deep-map/deep-map/0.7.0/build/provision.py"
    check("B3 ${CLAUDE_PLUGIN_ROOT} becomes the install path with forward slashes, $ARGUMENTS the arguments",
          want in text and "${CLAUDE_PLUGIN_ROOT}" not in text and 'TARGET=""' in text, text)
    check("B4 with arguments, $ARGUMENTS is replaced verbatim",
          'TARGET="sub dir"' in E.substitute(b_lf, "/p", "sub dir"))
    second, _ = E.command_block(cr, index=1)
    check("B5 the second block (setup.md's exit-4 sync) is a distinct block", bool(second) and "sync" in second
          and second != b_lf, second)
    check("B6 a markdown file with no bash block gives None", E.command_block(b"# title\n```sh\nx\n```\n")[0] is None)
    t2, used = E.python_fallback(text, have_python=False)
    t3, used3 = E.python_fallback(text, have_python=True)
    check("B7 setup.md's own rule: python -> python3 only where python is absent, and it is recorded",
          used and 'python3 "C:/' in t2 and not used3 and t3 == text, t2)
    hj = json.loads((REPO / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    cmds = E.hook_commands(hj)
    check("B8 every hooks.json command is listed with its event, matcher and timeout",
          len(cmds) == 5 and {c[0] for c in cmds} == {"SessionStart", "PreToolUse", "PostToolUse"}
          and all(c[3] == 10 for c in cmds), cmds)
    heal = next(c[2] for c in cmds if "sessionstart_heal" in c[2])
    lb = E.hook_line(heal, root_win, "posix")
    lp = E.hook_line(heal, root_win, "ps")
    check("B9 bash gets forward slashes, PowerShell native separators; nothing else changes",
          "C:/Users/runneradmin/.claude/plugins" in lb and root_win + "\\hooks\\" not in lb
          and root_win + "/hooks/deepmap_sessionstart_heal.py" in lp and lb.endswith("; exit 0"), (lb, lp))
    check("B10 argv per shell kind", E.hook_argv("posix", "/bin/bash", "x") == ["/bin/bash", "-c", "x"]
          and E.hook_argv("ps", "powershell.exe", "x") == ["powershell.exe", "-NoProfile", "-NonInteractive",
                                                           "-Command", "x"])
    check("B11 the hook kinds a name maps to", E.hook_kind("deepmap_sessionstart_heal") == "heal"
          and E.hook_kind("deepmap_toolresult_freshness") == "freshness" and E.hook_kind("x") is None)


# ===================================================================================================== C opt-in
def gate_c_opt_in(E, tmp: Path) -> None:
    print("\n-- C: the opt-in guard --")
    tok = "t0k3n-" + "9" * 20

    def g(env, docker=False, token_file=None):
        files = {"/.dockerenv": "" if docker else None, E.CONTAINER_TOKEN_FILE: token_file}
        return E.opt_in(env, exists=lambda p: files.get(str(p)) is not None,
                        read_text=lambda p: files.get(str(p)) or "")[0]

    ci = {"CI": "true", "GITHUB_ACTIONS": "true", "DEEPMAP_LIVE_TEST": "1", "RUNNER_ENVIRONMENT": "github-hosted"}
    check("C1 CI=true + GITHUB_ACTIONS=true + RUNNER_ENVIRONMENT=github-hosted + DEEPMAP_LIVE_TEST=1 opts in", g(ci))
    for k in ci:
        env = dict(ci)
        env.pop(k)
        check(f"C2 without {k} it refuses", not g(env))
    check("C3 DEEPMAP_LIVE_TEST must be exactly 1", not g(dict(ci, DEEPMAP_LIVE_TEST="yes")))
    check("C4 CI=false refuses", not g(dict(ci, CI="false")))
    check("C14 a self-hosted runner refuses (a developer box registered as a runner)",
          not g(dict(ci, RUNNER_ENVIRONMENT="self-hosted")))
    check("C15 RUNNER_ENVIRONMENT empty or another value refuses",
          not g(dict(ci, RUNNER_ENVIRONMENT="")) and not g(dict(ci, RUNNER_ENVIRONMENT="github")))
    why = E.opt_in(dict(ci, RUNNER_ENVIRONMENT="self-hosted"), exists=lambda p: False, read_text=lambda p: "")[1]
    check("C16 the refusal names RUNNER_ENVIRONMENT", "RUNNER_ENVIRONMENT" in why, why)
    check("C5 nothing set refuses (a developer machine)", not g({}))
    check("C6 a DMSAN=1-style switch alone refuses", not g({"DMSAN": "1", "DEEPMAP_LIVE_TEST": "1"}))
    cenv = {E.CONTAINER_TOKEN_ENV: tok, "DEEPMAP_LIVE_TEST": "1"}
    check("C7 /.dockerenv + the launcher's token in env and file opts in", g(cenv, docker=True, token_file=tok + "\n"))
    check("C8 a token without /.dockerenv refuses", not g(cenv, docker=False, token_file=tok))
    check("C9 /.dockerenv without a token file refuses", not g(cenv, docker=True, token_file=None))
    check("C10 a token that does not match the file refuses", not g(cenv, docker=True, token_file=tok + "x"))
    check("C11 a short token refuses", not g({E.CONTAINER_TOKEN_ENV: "abc", "DEEPMAP_LIVE_TEST": "1"},
                                             docker=True, token_file="abc"))
    check("C12 the container path still needs DEEPMAP_LIVE_TEST=1",
          not g({E.CONTAINER_TOKEN_ENV: tok}, docker=True, token_file=tok))
    # the real entry point, in a child with a scrubbed env and its own home: exit 2 and nothing created
    rep = tmp / "refused-report"
    home = tmp / "refused-user-home"
    env, real = _isolated_env(home)
    env["DEEPMAP_HOME"] = str(tmp / "refused-home")
    p = subprocess.run([sys.executable, str(TESTS / "live_e2e.py"), "--claude", "claude-does-not-exist",
                        "--report", str(rep)], capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env=env, cwd=str(tmp), timeout=120)
    left = _home_leftovers(home, real)
    seen = _child_home(env)
    check("C17 the refused child's Path.home() is its own temp home (so C13's home clause can fail)",
          os.path.normcase(seen) == os.path.normcase(str(home)), (seen, str(home)))
    check("C13 the driver refuses outside CI: exit 2, says why, creates no report dir, no ~/.deepmap, no ~/.claude",
          p.returncode == 2 and "refus" in (p.stdout + p.stderr).lower() and not rep.exists()
          and not (tmp / "refused-home").exists() and not left,
          f"rc={p.returncode} leftovers={left} {p.stdout[-400:]} {p.stderr[-400:]}")


# ===================================================================================================== D secrets + env
def gate_d_secrets(LC, E, tmp: Path) -> None:
    print("\n-- D: secret scan and the child env --")
    d = tmp / "scan"
    (d / "logs").mkdir(parents=True)
    secret_val = "oauth-" + "Q" * 30
    (d / "logs" / "a.log").write_text(f"before {FAKE_KEY} after\n", encoding="utf-8")
    (d / "logs" / "b.jsonl").write_text(json.dumps({"env": {"TOKEN": secret_val}}) + "\n", encoding="utf-8")
    (d / "clean.md").write_text("all good, sk- alone is fine, ant- too\n", encoding="utf-8")
    (d / "bin.dat").write_bytes(b"\x00\x01" + FAKE_KEY.encode() + b"\xff")
    hits = E.scan_and_redact(d, [secret_val])
    hit_files = sorted(Path(h[0]).name for h in hits)
    check("D1 an sk-ant- key, an env secret's value and a key inside a binary file are all found",
          hit_files == ["a.log", "b.jsonl", "bin.dat"], hits)
    check("D2 a hit names its kind, never the secret itself", all(secret_val not in str(h) and FAKE_KEY not in str(h)
                                                                   for h in hits), hits)
    after = "".join(p.read_text(encoding="utf-8", errors="replace") for p in d.rglob("*") if p.is_file())
    check("D3 the files are redacted in place", FAKE_KEY not in after and secret_val not in after
          and "[REDACTED" in after, after[:300])
    check("D4 a clean tree has no hits", E.scan_and_redact(d, [secret_val]) == [])
    check("D5 find_secrets on text", LC.find_secrets(f"x {FAKE_KEY} y") == ["sk-ant- key"]
          and LC.find_secrets("x", ["abcdefghijkl"]) == [] and LC.find_secrets("abcdefghijkl", ["abcdefghijkl"])
          == ["a secret's value"], LC.find_secrets(f"x {FAKE_KEY} y"))
    check("D6 a short env value is never treated as a secret (no false hits on 'true' or '1')",
          LC.find_secrets("true 1 yes", ["1", "true", ""]) == [])
    base = {"PATH": "/usr/bin", "HOME": "/home/u", "USERPROFILE": r"C:\Users\u", "JAVA_HOME": "/opt/jdk8",
            "JAVA_HOME_21_X64": "/opt/hostedtoolcache/jdk21", "ANTHROPIC_API_KEY": FAKE_KEY,
            "CLAUDE_CODE_OAUTH_TOKEN": "x" * 40, "ANTHROPIC_AUTH_TOKEN": "y" * 40, "CI": "true",
            "GITHUB_TOKEN": "z" * 40, "GITHUB_ACTIONS": "true", "RUNNER_TEMP": "/tmp/r", "DEEPMAP_LIVE_TEST": "1",
            "LC_ALL": "C.UTF-8", "LANG": "C.UTF-8", "SystemRoot": r"C:\Windows", "NEO4J_PASSWORD": "deepmap",
            "pythonLocation": "/opt/py", "HTTPS_PROXY": "http://proxy:3128"}
    env = LC.child_env(base, config_dir="/home/u/.claude")
    upper = {k.upper() for k in env}
    check("D7 the child env drops every key / token and every CI marker",
          not upper & {"ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_AUTH_TOKEN", "CI", "GITHUB_TOKEN",
                       "GITHUB_ACTIONS", "RUNNER_TEMP", "DEEPMAP_LIVE_TEST", "JAVA_HOME_21_X64", "NEO4J_PASSWORD",
                       "PYTHONLOCATION"}, sorted(upper))
    check("D8 ... and keeps what a user's shell has (PATH, HOME, USERPROFILE, JAVA_HOME, locale, SystemRoot, proxy)",
          {"PATH", "HOME", "USERPROFILE", "JAVA_HOME", "LC_ALL", "LANG", "SYSTEMROOT", "HTTPS_PROXY"} <= upper,
          sorted(upper))
    check("D9 CLAUDE_CONFIG_DIR and DISABLE_AUTOUPDATER=1 are set", env.get("CLAUDE_CONFIG_DIR") == "/home/u/.claude"
          and env.get("DISABLE_AUTOUPDATER") == "1", env)
    env2 = LC.child_env(base, config_dir=None, extra={"DEEPMAP_AUTO_SYNC": "1", "ANTHROPIC_API_KEY": "nope"})
    check("D10 extra adds settings but can never add a key back", env2.get("DEEPMAP_AUTO_SYNC") == "1"
          and "ANTHROPIC_API_KEY" not in env2 and "CLAUDE_CONFIG_DIR" not in env2, env2)
    check("D11 key_env_names reports which key variables a parent env holds (names only)",
          LC.key_env_names(base) == ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"]
          and LC.key_env_names({"ANTHROPIC_API_KEY": ""}) == [], LC.key_env_names(base))


# ===================================================================================================== E report
def gate_e_report(E, tmp: Path) -> None:
    print("\n-- E: report rendering and merging --")
    R = E.Recorder(quiet=True)
    R.phase("1 install")
    R.check("I1 a passing check", True)
    R.check("I2 a failing | piped check", False, "line one\nline two | piped")
    R.phase("2 setup")
    R.not_covered("MAX_PATH", "LongPathsEnabled could not be set back to 0")
    R.skip("S9 a skipped check", "no precondition")
    R.info("setup wall time (s)", 612.5)
    rows = R.results
    statuses = [r["status"] for r in rows]
    check("E1 the recorder keeps phase, id, status and detail", statuses == ["pass", "fail", "not-covered", "skip",
                                                                            "info"]
          and rows[1]["phase"] == "1 install" and rows[2]["phase"] == "2 setup", rows)
    js, md, rc = E.render_report(rows, {"job": "windows-field", "ref": "rc/v0.7.1"})
    check("E2 a FAIL makes the exit code 1", rc == 1)
    check("E3 report.md lists PASS, FAIL, NOT COVERED and SKIP sections and the job meta",
          all(s in md for s in ("### FAIL", "### NOT COVERED", "### SKIP", "### PASS", "windows-field",
                                "rc/v0.7.1")), md)
    check("E4 a pipe in a detail is escaped, a newline flattened", "piped check" in md and "\\|" in md
          and "line one line two" in md, md)
    data = json.loads(js)
    check("E5 report.json carries meta, counts and every row", data["meta"]["job"] == "windows-field"
          and data["counts"]["fail"] == 1 and len(data["results"]) == 5, data.get("counts"))
    ok_rows = [r for r in rows if r["status"] != "fail"]
    check("E6 NOT COVERED, SKIP and INFO alone exit 0", E.render_report(ok_rows, {})[2] == 0)
    R2 = E.Recorder(quiet=True)
    R2.phase("3 setup")
    R2.retry("S1 setup exits 0", "first attempt: transport error naming files.pythonhosted.org")
    js2, md2, rc2 = E.render_report(R2.results, {})
    check("E7 PASS AFTER RETRY is shown apart and blocks (exit 1)", rc2 == 1 and "### PASS AFTER RETRY" in md2, md2)
    old = [{"id": "a", "phase": "1", "status": "pass", "detail": ""},
           {"id": "b", "phase": "9 uninstall", "status": "skip", "detail": "main run timed out"}]
    new = [{"id": "b", "phase": "9 uninstall", "status": "pass", "detail": ""},
           {"id": "c", "phase": "9 uninstall", "status": "pass", "detail": ""}]
    merged = E.merge_results(old, new)
    check("E8 a resumed run replaces the same (phase, id) in place and appends the rest",
          [(r["id"], r["status"]) for r in merged] == [("a", "pass"), ("b", "pass"), ("c", "pass")], merged)
    d = tmp / "report"
    d.mkdir()
    E.write_report(d, rows, {"job": "linux"})
    E.write_report(d, new, {"job": "linux"}, merge=True)
    again = json.loads((d / "report.json").read_text(encoding="utf-8"))
    check("E9 write_report(merge=True) merges with the report.json already in the directory",
          len(again["results"]) == 7 and (d / "report.md").is_file(), len(again["results"]))
    for i, (label, state) in enumerate((("the main run installed nothing (no state.json)", None),
                                        ("the main run already uninstalled", {"install_path": "/x", "claude": "c",
                                                                              "uninstalled": True}))):
        cd = tmp / f"cleanup-{i}"
        cd.mkdir()
        if state is not None:
            (cd / "state.json").write_text(json.dumps(state), encoding="utf-8")
        rc = E.live(E.parse_args(["--only", "cleanup", "--resume", str(cd), "--report", str(cd)]))
        check(f"E11 the always-run cleanup is a no-op when {label}: exit 0, no report written",
              rc == 0 and not (cd / "report.json").exists(), rc)
    E.REC.on_phase = None
    check("E10 report.md has no em dash and no line over 120 characters in its fixed text",
          "\u2014" not in md and all(len(ln) <= 120 for ln in md.splitlines() if not ln.startswith("|")))


# ===================================================================================================== F fixture
def gate_f_fixture(FX, tmp: Path) -> None:
    print("\n-- F: synthetic project A --")
    a, b = tmp / "fa", tmp / "fb"
    FX.write_files(a)
    FX.write_files(b)
    check("F1 two writes give the same bytes (digest)", FX.digest(a) == FX.digest(b) == FX.digest())
    files = sorted(p.relative_to(a).as_posix() for p in a.rglob("*") if p.is_file())
    check("F2 about 40 files", 36 <= len(files) <= 48, len(files))
    exts = {Path(f).suffix for f in files}
    check("F3 Python, TypeScript, C#, SQL and an upper-case .PY", {".py", ".ts", ".cs", ".sql", ".PY"} <= exts, exts)
    py_like = [f for f in files if f.lower().endswith(".py")]
    check("F4 more than 20 Python files, so graphify starts its worker pool", len(py_like) > 20
          and len([f for f in files if "audit_" in f]) == FX.AUDIT_RULES == 21, len(py_like))
    raw = b"".join((a / f).read_bytes() for f in files)
    check("F5 LF only, valid UTF-8", b"\r" not in raw and raw.decode("utf-8") is not None)
    src = {f: (a / f).read_text(encoding="utf-8") for f in files}
    cs = "".join(t for f, t in src.items() if f.endswith(".cs"))
    check("F6 C#: a computed property and a call through an injected field (the 0.6.0 report shape)",
          re.search(r"decimal ItemsCalculatedPrice\s*=>", cs) is not None and "_carts.CreateShoppingCartAsync(" in cs
          and "private readonly ICartService _carts" in cs, cs[:200])
    defs = FX.definitions(src)
    for exp in FX.SEARCH_EXPECT:
        check(f"F7 search target {exp['name']} is defined in {exp['file']}", defs.get(exp["name"]) == exp["file"],
              defs.get(exp["name"]))
    for exp in FX.IMPACT_EXPECT:
        for caller in exp.get("callers", ()):
            body = FX.body_of(src, caller)
            target = exp["symbol"].split(".")[-1]
            check(f"F8 {caller} really calls {target}", bool(body) and f"{target}(" in body, body[:120])
    check("F9 the SQL file declares a table and a procedure", "CREATE TABLE dbo.Orders" in src["db/schema.sql"]
          and "CREATE PROCEDURE dbo.usp_ReserveStock" in src["db/schema.sql"])
    # evaluators on synthetic payloads
    exp = next(e for e in FX.SEARCH_EXPECT if e["name"] == "ItemsCalculatedPrice")
    rows = [{"name": f"filler_{i}", "file": "x.py"} for i in range(12)]
    hit = {"name": "ShoppingCartDto.ItemsCalculatedPrice", "file": exp["file"], "line": 9}
    ok3, _ = FX.check_search(rows[:2] + [hit] + rows[2:] + [{"note": "keyword-only"}], exp)
    ok12, d12 = FX.check_search(rows + [hit], exp)
    check("F10 search: a qualified name at rank 3 passes, at rank 13 fails (top 10), notes ignored",
          ok3 and not ok12, d12)
    ok_wf, _ = FX.check_search([{"name": "ItemsCalculatedPrice", "file": "other/File.cs"}], exp)
    check("F11 search: the right name in the wrong file fails", not ok_wf)
    vexp = next(e for e in FX.IMPACT_EXPECT if e["symbol"] == "validate_order")
    good = [{"caller": "checkout", "hop": 1, "confidence": "EXTRACTED"}] + \
        [{"caller": f"audit_rule_{i:02d}", "hop": 1, "confidence": "INFERRED"} for i in range(21)] + \
        [{"caller": "post_checkout", "hop": 2, "confidence": "INFERRED"}]
    okv, dv = FX.check_impact(good, vexp)
    okm, dm = FX.check_impact(good[:5] + good[6:], vexp)
    check("F12 impact validate_order: every audit rule, checkout and post_checkout pass; one missing rule fails",
          okv and not okm and "audit_rule_04" in dm, (dv, dm))
    check("F13 impact: a row without a confidence fails", not FX.check_impact([dict(r, confidence=None)
                                                                                  for r in good], vexp)[0])
    cexp = next(e for e in FX.IMPACT_EXPECT if e["symbol"] == "CreateShoppingCartAsync")
    okc, _ = FX.check_impact([{"note": "No dependents found. That is NOT proof nothing uses it: for C#, ..."}], cexp)
    okn, _ = FX.check_impact([], cexp)
    check("F14 impact on the C# method needs the honest C# note; a bare empty answer fails", okc and not okn)
    names_exp = next(e for e in FX.CYPHER_EXPECT if e["id"] == "names")
    okn2, _ = FX.check_cypher([{"name": "checkout"}, {"name": "formatPrice"}, {"name": "export_ledger"}], names_exp)
    okn3, _ = FX.check_cypher([{"name": "checkout"}, {"note": "x"}], names_exp)
    check("F15 cypher: the exact name set passes, a short one fails", okn2 and not okn3)
    w = next(e for e in FX.CYPHER_EXPECT if e["id"] == "write")
    check("F16 cypher: the write probe expects a rejection", w.get("rejected") is True and "CREATE" in w["query"])
    no_hop2 = [r for r in good if r["caller"] != "post_checkout"]
    okp, dp = FX.check_impact(no_hop2, vexp)
    check("F20 impact validate_order: every hop-1 caller but no post_checkout (the hop-2 route) fails",
          not okp and "post_checkout" in dp, dp)
    fexp = next(e for e in FX.IMPACT_EXPECT if e["symbol"] == "formatPrice")
    fgood = [{"caller": "renderReceipt", "confidence": "INFERRED"},
             {"caller": "renderInvoice", "confidence": "INFERRED"},
             {"caller": "receiptHandler", "hop": 2, "confidence": "INFERRED"}]
    okf, _ = FX.check_impact(fgood, fexp)
    okf2, df2 = FX.check_impact(fgood[:2], fexp)
    check("F21 impact formatPrice: both renderers and receiptHandler pass; no receiptHandler (hop 2) fails",
          okf and not okf2 and "receiptHandler" in df2, df2)
    rexp = next(e for e in FX.CYPHER_EXPECT if e["id"] == "routes")
    okr, _ = FX.check_cypher([{"name": "post_checkout"}, {"name": "receiptHandler"}, {"name": "router"}], rexp)
    okr2, dr2 = FX.check_cypher([{"name": "post_checkout"}, {"name": "router"}], rexp)
    check("F22 cypher routes: both handlers pass, one handler missing fails", okr and not okr2
          and "receiptHandler" in dr2, dr2)
    nexp = next(e for e in FX.CYPHER_EXPECT if e["id"] == "functions")
    low = [FX.check_cypher(rows_, nexp)[0] for rows_ in ([{"n": nexp["min_n"] - 1}], [{"n": True}], [{"n": "99"}],
                                                         [], [{"note": "x"}])]
    check("F23 cypher functions: n at min_n passes; below it, a bool, a string or no row fails",
          FX.check_cypher([{"n": nexp["min_n"]}], nexp)[0] and not any(low), low)
    occ = tmp / "occupied"
    (occ / "sub").mkdir(parents=True)
    (occ / "README.md").write_bytes(b"# mine\n")
    (occ / "package.json").write_bytes(b'{"name": "mine"}\n')
    (occ / "notes.txt").write_bytes(b"untracked notes\n")
    afile = tmp / "a-file"
    afile.write_bytes(b"x")
    empty = tmp / "empty-dir"
    empty.mkdir()
    check("F24 occupied(): a non-empty directory or a file is refused; a missing path or an empty directory is not",
          bool(FX.occupied(occ)) and bool(FX.occupied(afile)) and FX.occupied(tmp / "absent") is None
          and FX.occupied(empty) is None, (FX.occupied(occ), FX.occupied(afile)))
    before = _tree_bytes(occ)
    made = FX.make_project(occ)
    raised = None
    try:
        FX.write_files(occ)
    except FileExistsError as exc:
        raised = exc
    check("F25 make_project and write_files refuse an occupied directory and change none of its bytes",
          made.get("head") is None and "refus" in str(made.get("error") or "").lower() and raised is not None
          and _tree_bytes(occ) == before and not (occ / ".git").exists(), (made, raised))
    if shutil.which("git"):
        h1 = FX.make_project(tmp / "ga")
        h2 = FX.make_project(tmp / "gb")
        check("F17 make_project commits the same tree to the same commit (isolated git config, fixed dates)",
              bool(h1.get("head")) and h1.get("head") == h2.get("head"), (h1, h2))
        st = subprocess.run(["git", "-C", str(tmp / "ga"), "status", "--porcelain"], capture_output=True, text=True)
        check("F18 the work tree is clean after make_project", st.returncode == 0 and st.stdout.strip() == "",
              st.stdout)
    else:
        check("F17 git is not on PATH here: the commit determinism check is skipped (counted as pass)", True)
    st_file, st_text = FX.STALE_EDIT
    check("F19 the stale-case edit targets a fixture source file and defines a new function",
          st_file in src and "def " in st_text and st_text not in src[st_file])


# ===================================================================================================== G retry rule
def gate_g_transport(E) -> None:
    print("\n-- G: the one setup retry --")
    yes = [
        ("pip, connection reset", "ERROR: Could not install packages due to an OSError: "
         "HTTPSConnectionPool(host='files.pythonhosted.org', port=443): Max retries exceeded with url: /packages/x.whl "
         "(Caused by ProtocolError('Connection aborted.', ConnectionResetError(10054)))", "files.pythonhosted.org"),
        ("torch index 503", "Looking in indexes: https://download.pytorch.org/whl/cpu\n"
         "ERROR: HTTP error 503 while getting https://download.pytorch.org/whl/cpu/torch-2.9.0.whl",
         "download.pytorch.org"),
        ("hf 429", "huggingface_hub.errors.HfHubHTTPError: 429 Client Error: Too Many Requests for url: "
         "https://huggingface.co/api/models/nomic-ai/x", "huggingface.co"),
        ("neo4j download timeout", "[provision] downloading https://dist.neo4j.org/neo4j-community-5.26.32-windows.zip"
         "\n[provision] ERROR: download failed: <urlopen error timed out>", "dist.neo4j.org"),
        ("dns", "pip._vendor.urllib3.exceptions.NewConnectionError: Failed to establish a new connection: "
         "[Errno -3] Temporary failure in name resolution (pypi.org)", "pypi.org"),
    ]
    for label, text, host in yes:
        got = E.transport_error(text)
        check(f"G1 retry allowed: {label} (names {host})", bool(got) and host in got, got)
    no = [
        ("a census FAILED", "[provision] setup verdict: FAILED - no source code DeepMap can index"),
        ("a URL with no error", "Downloading https://download.pytorch.org/whl/cpu/torch.whl (200 MB)\n done"),
        ("an error with no URL", "ConnectionResetError: [WinError 10054] An existing connection was forcibly closed"),
        ("a checksum mismatch on the Neo4j download (a product defect, KL-mistake:007)",
         "[provision] ERROR: https://dist.neo4j.org/neo4j-community-5.26.32-windows.zip failed its integrity "
         "check: sha256 mismatch"),
        ("a checksum mismatch next to a network word (still a defect: never retried)",
         "[provision] downloading https://dist.neo4j.org/neo4j-community-5.26.32-windows.zip (read timed out once, "
         "resumed)\n[provision] ERROR: the archive failed its integrity check: sha256 mismatch"),
        ("a 404 (a wrong URL is a defect, not the network)",
         "ERROR: HTTP error 404 while getting https://download.pytorch.org/whl/cpu/torch-9.9.9.whl"),
        ("a host DeepMap does not use", "ConnectionResetError talking to https://example.com/x"),
    ]
    for label, text in no:
        check(f"G2 no retry: {label}", E.transport_error(text) is None, E.transport_error(text))


# ===================================================================================================== H hook stderr
def gate_h_hook_stderr(LC) -> None:
    print("\n-- H: hook stderr verdicts --")
    v = LC.stderr_verdict("/usr/bin/bash: line 1: python3: command not found\n")
    check("H1 bash: python3 not found (python ran the hook) is tolerated", not v["fatal"] and v["missing"] ==
          {"python3"}, v)
    v = LC.stderr_verdict("/usr/bin/bash: line 1: python: command not found\n"
                          "/usr/bin/bash: line 1: python3: command not found\n")
    check("H2 bash: both interpreters missing is fatal", bool(v["fatal"]), v)
    ps = ("python3 : The term 'python3' is not recognized as the name of a cmdlet, function, script file, or "
          "operable program. Check the spelling of the name, or if a path was included, verify that the path is "
          "correct and try again.\nAt line:1 char:70\n+ ... utf8 \"C:\\p\\hooks\\x.py\" ; python3 -X utf8 "
          "\"C:\\p\\hooks\\x.py\" ; exit 0\n+                                                  ~~~~~~~\n"
          "    + CategoryInfo          : "
          "ObjectNotFound: (python3:String) [], CommandNotFoundException\n    + FullyQualifiedErrorId : "
          "CommandNotFoundException\n")
    v = LC.stderr_verdict(ps)
    check("H3 PowerShell: the python3 not-recognized block is tolerated as a whole", not v["fatal"]
          and v["missing"] == {"python3"}, v)
    v = LC.stderr_verdict("Python was not found; run without arguments to install from the Microsoft Store, or "
                          "disable this shortcut from Settings > Apps > Advanced app settings > App execution "
                          "aliases.\n")
    check("H4 the Windows python3 alias stub is tolerated", not v["fatal"], v)
    for label, text in (("a traceback", "Traceback (most recent call last):\n  File \"x\", line 1\nValueError: x\n"),
                        ("an unopenable hook file", "python: can't open file '/p/hooks/x.py': [Errno 2] No such file"),
                        ("PowerShell 5.1 rejecting &&", "At line:1 char:10\n+ a && b\nThe token '&&' is not a valid "
                         "statement separator in this version.\n    + FullyQualifiedErrorId : InvalidEndOfLine"),
                        ("another command missing", "/usr/bin/bash: line 1: deepmap: command not found"),
                        ("PowerShell missing another command", "The term 'claude' is not recognized as the name of a "
                         "cmdlet")):
        check(f"H5 fatal: {label}", bool(LC.stderr_verdict(text)["fatal"]), LC.stderr_verdict(text))
    check("H6 empty stderr is clean", LC.stderr_verdict("") == {"fatal": [], "missing": set(), "tolerated": []})


# ===================================================================================================== I process table
def gate_i_process_table(E, tmp: Path) -> None:
    print("\n-- I: process tables --")
    rows = E.parse_ps("  PID COMMAND\n    1 /sbin/init splash\n  412 /home/u/.deepmap/venv/bin/python "
                      "deepmap_search_server.py --port 8787\nbad line\n")
    check("I1 ps text: pid + full command, header and junk skipped",
          rows == [(1, "/sbin/init splash"), (412, "/home/u/.deepmap/venv/bin/python deepmap_search_server.py "
                                                   "--port 8787")], rows)
    cim = json.dumps([{"ProcessId": 4, "ParentProcessId": 0, "ExecutablePath": None, "CommandLine": None},
                      {"ProcessId": 4012, "ParentProcessId": 4, "ExecutablePath": "C:\\x\\java.exe",
                       "CommandLine": "\"C:\\x\\java.exe\" -cp neo4j"}])
    t = E.parse_cim(cim)
    check("I2 Win32_Process JSON: pid, parent, executable and command line", len(t) == 2 and t[1]["pid"] == 4012
          and t[1]["exe"].endswith("java.exe") and "neo4j" in t[1]["cmd"] and t[0]["cmd"] == "", t)
    one = E.parse_cim(json.dumps({"ProcessId": 7, "CommandLine": "a"}))
    check("I3 a single object (one process) and garbage", one == [{"pid": 7, "ppid": None, "exe": "", "cmd": "a"}]
          and E.parse_cim("not json") == [] and E.parse_cim("") == [], one)
    home = tmp / "home"
    for rel, text in (("run/deepmap-search-8787.owner", json.dumps({"pid": 101, "created": 1.0})),
                      ("run/deepmap-search-8787.pid", "101"), ("run/heal-abc.json", json.dumps({"pid": 999})),
                      ("run/setup-python-x.json", json.dumps({"pid": 998})),
                      ("neo4j/neo4j-community-5.26.32/run/neo4j.pid", "202"),
                      ("neo4j/neo4j-community-5.26.32/run/neo4j.launcher.json", json.dumps({"launcher_pid": 303})),
                      ("instances/k1/run/neo4j.pid.identity", json.dumps({"pid": 404}))):
        (home / rel).parent.mkdir(parents=True, exist_ok=True)
        (home / rel).write_text(text, encoding="utf-8")
    pids = E.recorded_pids(home)
    check("I5 recorded pids come from identity records only (owner, pid, launcher, identity; not status files)",
          set(pids) == {101, 202, 303, 404}, pids)
    table = [{"pid": 101, "ppid": 1, "exe": "", "cmd": "/h/.deepmap/venv/bin/python deepmap_search_server.py"},
             {"pid": 202, "ppid": 1, "exe": "C:\\Windows\\notepad.exe", "cmd": "notepad.exe"}]
    lr = E.live_recorded(pids, table)
    check("I6 a live recorded pid counts only while it still runs DeepMap's code (a reused pid does not)",
          set(lr) == {101}, lr)
    wargv = E.process_table_argv(windows=True)
    check("I4 Windows: Win32_Process through powershell.exe 5.1, never ps or /proc; POSIX: ps",
          wargv[0].lower().endswith("\\windowspowershell\\v1.0\\powershell.exe") and "Win32_Process" in wargv[-1]
          and not any(a == "ps" or "/proc" in a for a in wargv) and E.process_table_argv(windows=False)[0] == "ps",
          wargv)


# ===================================================================================================== J hygiene
def gate_j_hygiene() -> None:
    print("\n-- J: hygiene of the new files --")
    stdlib = set(getattr(sys, "stdlib_module_names", ())) | {"live_claude", "live_e2e", "live_e2e_fixture",
                                                              "__future__"}
    for rel in NEW_FILES:
        p = REPO / rel
        if not p.is_file():
            check(f"J0 {rel} exists", False)
            continue
        raw = p.read_bytes()
        text = raw.decode("utf-8")
        try:
            tree = ast.parse(text)
            compiled = True
        except SyntaxError as exc:
            compiled, tree = False, None
            check(f"J1 {rel} parses", False, exc)
        if compiled:
            check(f"J1 {rel} parses", True)
        if tree is not None and stdlib:
            mods = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    mods |= {a.name.split(".")[0] for a in node.names}
                elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                    mods.add(node.module.split(".")[0])
            check(f"J2 {rel} imports the standard library only", mods <= stdlib, sorted(mods - stdlib))
        # LF in git; a Windows checkout (core.autocrlf) turns it into CRLF: only a CR outside a CRLF pair fails
        check(f"J3 {rel} has no stray CR", b"\r" not in raw.replace(b"\r\n", b""))
        long_lines = [i + 1 for i, ln in enumerate(text.splitlines()) if len(ln) > 120]
        check(f"J4 {rel} keeps lines <= 120", not long_lines, long_lines[:5])
        check(f"J5 {rel} has no em dash", "\u2014" not in text)
        check(f"J6 {rel} holds no key-shaped literal", re.search(r"sk-ant-[A-Za-z0-9_-]{20,}", text) is None)
    for rel in TEXT_FILES:   # shipped beside the harness through the public mirror: the same text rules
        p = REPO / rel
        if not p.is_file():
            check(f"J0 {rel} exists", False)
            continue
        raw = p.read_bytes()
        text = raw.decode("utf-8")
        check(f"J3 {rel} has no stray CR", b"\r" not in raw.replace(b"\r\n", b""))
        long_lines = [i + 1 for i, ln in enumerate(text.splitlines()) if len(ln) > 120]
        check(f"J4 {rel} keeps lines <= 120", not long_lines, long_lines[:5])
        check(f"J5 {rel} has no em dash", "\u2014" not in text)
        check(f"J6 {rel} holds no key-shaped literal", re.search(r"sk-ant-[A-Za-z0-9_-]{20,}", text) is None)
    samples = sorted(SAMPLES.glob("*.jsonl"))
    leak = re.compile(r"[Uu]sers[_/\\]+[Oo]wner(?![A-Za-z])|AppData[/\\]+Local[/\\]+Temp|dmsan")
    check("J7 the recorded samples carry no local path", len(samples) == 4 and not any(
        leak.search(p.read_text(encoding="utf-8")) for p in samples), [p.name for p in samples])


# ===================================================================================================== L phase verdicts
def gate_l_phase_verdicts(E, tmp: Path) -> None:
    print("\n-- L: the verdict helpers of the live phases --")
    full = "".join(f"[provision] [{k}/7] step {k} ...\n" for k in range(1, 8))
    gap = "".join(f"[provision] [{k}/7] step {k} ...\n" for k in (1, 2, 3, 5, 6, 7))
    grow = "[provision] [1/5] a\n[provision] [2/5] b\n[provision] [3/5] c\n[provision] [4/5] d\n[provision] [5/5] e\n" \
           "[provision] [6/7] f\n[provision] [7/7] g\n"
    indented = "".join(f"  [provision] [{k}/2] x\n" for k in (1, 2))
    got = [E._progress_complete(t)[0] for t in (full, gap, "", "no progress here\n", grow, indented)]
    check("L1 setup progress: [1/7]..[7/7] complete; a gap, no lines, or lines not at a line start are not; a total "
          "that grows while it runs is complete once every step is seen", got == [True, False, False, False, True,
                                                                                  False], got)
    proj = tmp / "proj"
    (proj / ".deepmap").mkdir(parents=True)
    head = "DeepMap SessionStart heal: provision.py heal /p (hook pid {}; python /v/py [{}]) ---"
    h1 = "--- 2026-11-03T10:00:00Z " + head.format(11, "setup")
    h2 = "--- 2026-11-03T10:05:00Z " + head.format(12, "hook")
    (proj / ".deepmap" / "heal.log.1").write_text(f"\n{h1}\n[provision] heal: Neo4j is up\n", encoding="utf-8")
    (proj / ".deepmap" / "heal.log").write_text(
        f"\n--- not a header: provision.py output ---\n[provision] heal: --- DeepMap SessionStart heal: quoted ---\n"
        f"\n{h2}\n--- \n", encoding="utf-8")
    hh = E.heal_headers(proj)
    check("L2 heal_headers: only the hook's header lines, heal.log.1 first, never another '--- ' line", hh == [h1, h2],
          hh)
    check("L3 heal_headers on a project with no heal.log is empty", E.heal_headers(tmp / "nowhere") == [])
    home = tmp / "home"
    (home / "run").mkdir(parents=True)
    pdir = tmp / "dm proj \u05d0" / "polyshop"
    recs = {"heal-old.json": {"ts_epoch": 100.0, "neo4j_ok": True, "mcp_ok": True, "root": str(pdir)},
            "heal-new.json": {"ts_epoch": 300.0, "neo4j_ok": False, "mcp_ok": False, "reason": "Neo4j did not start",
                              "root": str(pdir).replace("\\", "/")},
            "heal-other.json": {"ts_epoch": 400.0, "neo4j_ok": True, "mcp_ok": True, "root": str(tmp / "other")},
            "heal-junk.json": "not json"}
    for name, rec in recs.items():
        (home / "run" / name).write_text(rec if isinstance(rec, str) else json.dumps(rec), encoding="utf-8")
    (home / "run" / "heal-new.lock").write_text("{}", encoding="utf-8")
    fin = E.heal_finished_since(home, 200.0, pdir)
    check("L4 heal_finished_since: this project's newest heal status written after t0 (another project's, an older "
          "one, junk and the lock are ignored)", isinstance(fin, dict) and fin.get("ts_epoch") == 300.0, fin)
    check("L5 heal_finished_since: none written after t0 is None", E.heal_finished_since(home, 350.0, pdir) is None
          and E.heal_finished_since(tmp / "no-home", 0.0, pdir) is None)
    rv = {"up": E.revive_verdict(True, None, False), "finished": E.revive_verdict(False, fin, False),
          "running": E.revive_verdict(False, None, True), "gone": E.revive_verdict(False, None, False)}
    check("L6 revive: the CLI-started heal reviving everything passes", rv["up"][0] == "pass", rv["up"])
    check("L7 revive: a heal that FINISHED with a port still closed is a FAIL (never the open question)",
          rv["finished"][0] == "fail" and "Neo4j did not start" in rv["finished"][1], rv["finished"])
    check("L8 revive: a heal still running after the wait is a FAIL", rv["running"][0] == "fail", rv["running"])
    check("L9 revive: no heal finished and none runs (the teardown may have ended it) is NOT COVERED, naming the "
          "first-run question", rv["gone"][0] == "not-covered" and E.UNSETTLED_HEAL in rv["gone"][1], rv["gone"])
    R = E.Recorder(quiet=True)
    R.phase("7 lifecycle")
    R.record("L4 the CLI-started heal revived everything", *rv["gone"])
    R.check("L5 the hooks.json heal run directly revives everything", True)
    ok_rc = E.render_report(R.results, {})[2]
    R.check("L5 the hooks.json heal run directly revives everything (again)", False, "ports closed")
    bad_rc = E.render_report(R.results, {})[2]
    check("L10 the open question alone never fails the leg; the direct heal (L5) gates it", ok_rc == 0 and bad_rc == 1,
          (ok_rc, bad_rc))
    check("L11 the lifecycle stops the MCP servers too (mcp-stop, then stop), so the heal must start them",
          tuple(E.LIFECYCLE_STOP) == ("mcp-stop", "stop"), E.LIFECYCLE_STOP)
    held = {"decoy": True, "java_home_usable": False, "no_java_on_path": True, "jdk21": True}
    due = E.java_checks_due(held)
    check("L12 Java checks: every precondition held -> S8 and S9 both run", due["S8"][0] and due["S9"][0], due)
    cases = {"no decoy": (dict(held, decoy=False), (False, True)),
             "no per-user JDK 21": (dict(held, jdk21=False), (True, False)),
             "java on PATH": (dict(held, no_java_on_path=False), (True, False)),
             "JAVA_HOME a usable 17 / 21": (dict(held, decoy=False, java_home_usable=True), (False, False)),
             "phase 0 never ran": ({}, (False, False))}
    for label, (field, want) in cases.items():
        d = E.java_checks_due(field)
        check(f"L13 Java checks, {label}: S8 runs={want[0]}, S9 runs={want[1]}, a skipped one says why",
              (d["S8"][0], d["S9"][0]) == want and all(d[k][1] for k in ("S8", "S9") if not d[k][0]), d)


# ===================================================================================================== K dry run
def gate_k_dry_run(tmp: Path) -> None:
    print("\n-- K: the driver's --dry-run --")
    rep = tmp / "dry-report"
    home = tmp / "dry-home"
    uhome = tmp / "dry-user-home"
    env, real = _isolated_env(uhome)
    env["DEEPMAP_HOME"] = str(home)
    seen = _child_home(env)
    check("K0 the dry run's Path.home() is its own temp home (so K6 can fail)",
          os.path.normcase(seen) == os.path.normcase(str(uhome)), (seen, str(uhome)))
    p = subprocess.run([sys.executable, str(TESTS / "live_e2e.py"), "--dry-run", "--report", str(rep),
                        "--project-dir", str(tmp / "dry proj \u05e9" / "polyshop")], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env, cwd=str(tmp), timeout=300)
    check("K1 --dry-run exits 0 without opting in", p.returncode == 0, f"rc={p.returncode} {p.stdout[-800:]} "
                                                                       f"{p.stderr[-800:]}")
    data = {}
    try:
        data = json.loads((rep / "report.json").read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        check("K2 report.json written", False, repr(exc))
    if data:
        ids = [r["id"] for r in data.get("results", [])]
        check("K2 report.json written, dry-run rows recorded", data.get("meta", {}).get("mode") == "dry-run"
              and any("setup.md" in i for i in ids) and any("hooks.json" in i for i in ids), ids[:20])
        fails = [r for r in data.get("results", []) if r["status"] == "fail"]
        check("K3 the dry run's own checks pass", not fails, fails)
    cmd_log = rep / "logs" / "setup-command.txt"
    check("K4 the substituted setup command is logged", cmd_log.is_file() and "provision.py" in
          cmd_log.read_text(encoding="utf-8"))
    check("K5 the project was generated at the given path (a space and a non-ASCII letter)",
          (tmp / "dry proj \u05e9" / "polyshop" / "shop" / "orders.py").is_file())
    left = _home_leftovers(uhome, real)
    check("K6 no DEEPMAP_HOME, no ~/.deepmap and no ~/.claude was created (the temp home and the real one)",
          not home.exists() and not left, left)
    # an existing, non-empty --project-dir (a developer's checkout, say) is refused before anything is written
    occ = tmp / "my checkout \u05d1"
    occ.mkdir()
    (occ / "README.md").write_bytes(b"# my project\n")
    (occ / "package.json").write_bytes(b'{"name": "mine"}\n')
    (occ / "notes.txt").write_bytes(b"untracked notes\n")
    if shutil.which("git"):
        subprocess.run(["git", "-C", str(occ), "init", "-q"], env=env, capture_output=True, timeout=60)
    before = _tree_bytes(occ)
    rep2 = tmp / "dry-report-refused"
    p2 = subprocess.run([sys.executable, str(TESTS / "live_e2e.py"), "--dry-run", "--report", str(rep2),
                         "--project-dir", str(occ)], capture_output=True, text=True, encoding="utf-8",
                        errors="replace", env=env, cwd=str(tmp), timeout=300)
    check("K7 --dry-run with an existing non-empty --project-dir refuses: exit 2, says why, no report, its bytes "
          "(.git included) unchanged", p2.returncode == 2 and "refus" in (p2.stdout + p2.stderr).lower()
          and not rep2.exists() and _tree_bytes(occ) == before,
          f"rc={p2.returncode} {p2.stdout[-400:]} {p2.stderr[-400:]}")
    p3 = subprocess.run([sys.executable, str(TESTS / "live_e2e_fixture.py"), str(occ)], capture_output=True,
                        text=True, encoding="utf-8", errors="replace", env=env, cwd=str(tmp), timeout=120)
    check("K8 `python tests/live_e2e_fixture.py <dir>` refuses the same directory: exit 2, bytes unchanged",
          p3.returncode == 2 and "refus" in (p3.stdout + p3.stderr).lower() and _tree_bytes(occ) == before,
          f"rc={p3.returncode} {p3.stdout[-400:]} {p3.stderr[-400:]}")
    check("K9 ... and neither child left a ~/.deepmap or ~/.claude", not _home_leftovers(uhome, real),
          _home_leftovers(uhome, real))


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
    print("=" * 72)
    print("DeepMap install sanity harness: hermetic lock (no network, no Neo4j, no Claude Code CLI)")
    print("=" * 72)
    LC = _imp("live_claude")
    E = _imp("live_e2e")
    FX = _imp("live_e2e_fixture")
    with tempfile.TemporaryDirectory(prefix="dm_live_e2e_gates_") as d:
        td = Path(d)
        sections = [("A", gate_a_parser, (LC,), LC), ("B", gate_b_command_text, (E,), E),
                    ("C", gate_c_opt_in, (E, td / "c"), E), ("D", gate_d_secrets, (LC, E, td / "d"), LC and E),
                    ("E", gate_e_report, (E, td / "e"), E), ("F", gate_f_fixture, (FX, td / "f"), FX),
                    ("G", gate_g_transport, (E,), E), ("H", gate_h_hook_stderr, (LC,), LC),
                    ("I", gate_i_process_table, (E, td / "i"), E), ("J", gate_j_hygiene, (), True),
                    ("K", gate_k_dry_run, (td / "k",), E and FX), ("L", gate_l_phase_verdicts, (E, td / "l"), E)]
        for label, fn, args, ready in sections:
            for a in args:
                if isinstance(a, Path):
                    a.mkdir(parents=True, exist_ok=True)
            if not ready:
                check(f"section {label} ran (its module imports)", False)
                continue
            try:
                fn(*args)
            except Exception as exc:  # noqa: BLE001 - one section's crash is a failure, not the end
                import traceback
                traceback.print_exc()
                check(f"section {label} ran without an exception", False, repr(exc))
    print("\n" + "=" * 72)
    total = len(PASSES) + len(FAILS)
    print(f"RESULT: {'GREEN' if not FAILS else 'RED'} -- {len(PASSES)}/{total} checks passed.")
    for f in FAILS:
        print(f"  FAILED: {f}")
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
