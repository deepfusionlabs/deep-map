#!/usr/bin/env python3
"""0.28.1/0.28.2 — `/deep-map:plugin-update` must DO the update, not describe it — and do it right.

0.28.1 (field report, the operator in another project, VS Code extension): the command only printed instructions.
Its text (a) said the IDE extension's chat cannot run the `claude` CLI — false: its Bash tool runs it; (b) named the
plugin `deepmap@deepfusionlabs` — it is `deep-map@deepfusionlabs`; (c) demanded a second "yes".
0.28.2 (two reviews of the 0.28.1 text, every claim read from the 2.1.284 / 2.1.129 binaries on the real host):
(P1) without `disable-model-invocation: true` the MODEL could invoke the command and update the plugin unasked;
(P2) in the VS Code extension a NEW CHAT loads the new version — a full IDE restart is not needed and ends every
running session; an update made in the extension's Manage Plugins dialog reloads open chats in place;
(P3) CLI discovery: `$CLAUDE_CODE_EXECPATH` names the running binary but only the Bash tool exports it; PATH can hold
an older CLI (npm 2.1.129 vs the host's 2.1.284), so the highest version wins; PowerShell's plain `where` is
Where-Object and a quoted path needs `&`; `--help` of an unknown subcommand exits 0, so only the Usage line proves a
CLI; (P4) `claude plugin list --json` resolves the install, one per scope, and a current CLI's `plugin update` without
`--scope` picks whichever install applies to the current directory — so the scope is always passed; (P5) `--check`
refreshes the catalog, installs nothing, and stops when no CLI works; (P7) no "reversible" claim: orphaned versions are
deleted after 14 days (7 on older releases); `claude plugin update` does refresh a git marketplace itself (the
separate refresh makes a failed refresh visible); #38271 was auto-closed as stale; #37862 was fixed. And
commands/version.md compares LOADED with the INSTALLED pin a new session loads — not the marketplace catalog or a
working copy (a newer catalog means "update", not "reload").

  A  the command text (and commands/version.md): the frontmatter parsed as YAML, the step-1 bullet order inside
     step 1, and the corrected claims.
  B  against the real host when present (skips visibly otherwise): the documented `claude plugin` syntax, from the
     session's own binary ($CLAUDE_CODE_EXECPATH) when it really is Claude Code, else PATH; the bundled-binary location
     where an extension ships one; the registry shape when DeepMap is installed.

Usage: python tests/plugin_update_cmd_gates.py [--root <tree>]   (default: this repository; section A reads
<tree>/commands/)
"""
from __future__ import annotations

import glob
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PASSES: list[str] = []
FAILS: list[str] = []

# A regression back to "new window" / "quit the IDE" advice (P2), in either file.
_WINDOW_ADVICE = re.compile(r"(same|new) (vs code )?window|quit .{0,20}(ide|vs ?code)")


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSES if ok else FAILS).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok or not detail else f" — {detail}"))


def _flat(text: str) -> str:
    return " ".join(text.split())


def _frontmatter(raw: str) -> str:
    parts = raw.split("---")
    return parts[1] if raw.startswith("---") and len(parts) > 2 else ""


def _parse_frontmatter(fm: str):
    """(dict | None, how). Claude Code drops EVERY frontmatter field when the block does not parse, so an
    unparseable block would silently lose `disable-model-invocation`. PyYAML when installed; else a strict
    stdlib reading of flat `key: value` lines (a quoted value must close; an unquoted one must not hold ': ',
    ' #' or start with a YAML indicator)."""
    try:
        import yaml  # type: ignore
    except ImportError:
        yaml = None
    if yaml is not None:
        try:
            d = yaml.safe_load(fm)
        except Exception as e:  # noqa: BLE001
            return None, f"yaml: {e}"
        return (d if isinstance(d, dict) else None), "yaml"
    out: dict = {}
    for line in fm.splitlines():
        if not line.strip():
            continue
        m = re.match(r"^([A-Za-z][\w-]*):\s*(.*?)\s*$", line)
        if not m:
            return None, f"stdlib: not a key line: {line!r}"
        key, val = m.group(1), m.group(2)
        if val[:1] in ('"', "'"):
            if len(val) < 2 or val[-1] != val[0]:
                return None, f"stdlib: unclosed quote: {line!r}"
            val = val[1:-1]
        elif ": " in val or " #" in val or val[:1] in set("[]{}&*!|>%@`,?:-#"):
            return None, f"stdlib: not a plain scalar: {line!r}"
        out[key] = {"true": True, "false": False}.get(val, val)
    return out, "stdlib"


def gate_a_text(root: Path) -> None:
    print("\n-- A: the command performs the update, correctly --")
    raw = (root / "commands" / "plugin-update.md").read_text(encoding="utf-8")
    fm_raw = _frontmatter(raw)
    fm, how = _parse_frontmatter(fm_raw)
    t = _flat(raw)
    low = t.lower()
    vraw = (root / "commands" / "version.md").read_text(encoding="utf-8")
    vt = _flat(vraw)
    vlow = vt.lower()

    # --- who may run it (frontmatter, parsed the way Claude Code needs it to parse)
    check(f"frontmatter parses ({how})", isinstance(fm, dict), how if not isinstance(fm, dict) else "")
    fm = fm if isinstance(fm, dict) else {}
    check("frontmatter: disable-model-invocation is true (only the user can start an update)",
          fm.get("disable-model-invocation") is True, repr(fm.get("disable-model-invocation")))
    check("frontmatter: an argument-hint offers --check", "--check" in str(fm.get("argument-hint", "")))
    desc = str(fm.get("description", ""))
    check("the description says it updates for you, and that --check refreshes the catalog and installs nothing",
          "updates the plugin for you" in desc and "--check" in desc and "installs nothing" in desc
          and "refreshes the marketplace catalog" in desc and "only reports" not in desc, desc)
    vfm, vhow = _parse_frontmatter(_frontmatter(vraw))
    check(f"commands/version.md frontmatter parses ({vhow})", isinstance(vfm, dict), vhow)
    check("no second confirmation: the user's invocation IS the request",
          "do not stop to ask for a second yes" in low and "wait for an explicit yes" not in low
          and "confirm before changing anything" not in low)

    # --- identity + resolution
    check("names the plugin by its real registry key deep-map@<marketplace>", "deep-map@" in t)
    check("never names the plugin `deepmap@` / `deepmap` (the name has a hyphen)",
          "deepmap@" not in t and not re.search(r"`deepmap`", t), re.findall(r".{30}deepmap@.{20}", t))
    check("resolves the install with `claude plugin list --json` (honours CLAUDE_CONFIG_DIR)",
          "plugin list --json" in t and "CLAUDE_CONFIG_DIR" in t)
    check("one install per scope: local, then this project's project entry, else user (what Claude Code applies)",
          "array" in low and "a `local`, then a `project` entry whose `projectPath` is this project" in t
          and "else the `user` entry" in t)

    # --- finding the CLI (step 1)
    check("runs the CLI through the shell tool in every host (extensions included)",
          "every host" in low and "vs code" in low and "not on its path" not in low
          and "does **not** shell out" not in t)
    s1 = t[t.find("1. **Find"):t.find("2. **Resolve")]
    i_exec = s1.find('- `"$CLAUDE_CODE_EXECPATH"`')
    i_path = s1.find("- every `claude` on PATH")
    i_bund = s1.find("- the CLI bundled")
    check("step 1 bullets, in order: $CLAUDE_CODE_EXECPATH, then every claude on PATH, then the bundled CLIs",
          0 <= i_exec < i_path < i_bund, f"positions {i_exec}, {i_path}, {i_bund} in step 1")
    check("prefers the Bash tool: only it exports $CLAUDE_CODE_EXECPATH (the PowerShell tool does not)",
          "prefer the Bash tool, because only it exports `$CLAUDE_CODE_EXECPATH`" in s1)
    check("a candidate is usable only if `plugin update --help` prints its Usage line (exit 0 proves nothing)",
          "prints `Usage: claude plugin update`" in s1 and "exit code proves nothing" in s1.lower())
    check("PowerShell calls a path with `&`", '`& "<path>" plugin update --help`' in s1)
    check("lists every PATH hit in bash and PowerShell (type -ap / Get-Command -All / where.exe; plain `where` "
          "is Where-Object)", "type -ap claude" in s1 and "Get-Command claude -All" in s1
          and "where.exe claude" in s1 and "`Where-Object`" in s1)
    check("knows the IDE extensions' and the desktop app's bundled CLI",
          "anthropic.claude-code-*/resources/native-binary/claude" in s1 and "claude-code\\<version>\\claude.exe" in s1)
    check("without a usable $CLAUDE_CODE_EXECPATH, the highest --version across PATH AND bundled candidates wins",
          "run `<claude> --version` on every usable path and bundled candidate" in s1.lower()
          and "highest version number" in s1.lower() and "numerically" in s1.lower()
          and "never a path hit just because it comes first" in s1.lower())
    check("tries the next-highest candidate when a later step fails", "try the next-highest" in s1.lower())
    check("--check with no working CLI reports from the files and stops (never sends the user to Update)",
          "with `--check`, say no `claude` CLI is reachable" in s1 and "and stop" in s1)

    # --- the update
    m = t.find("plugin marketplace update")
    u = t.find("plugin update deep-map@")
    check("refreshes the marketplace BEFORE updating the plugin", 0 <= m < u, f"marketplace at {m}, update at {u}")
    check("says why: `plugin update` refreshes the catalog itself but only warns when that fails",
          "refreshes the catalog itself" in low and "reports the installed version as the newest with exit 0" in low)
    check("a failed refresh is shown and stops the run", "if the refresh fails, show its output and stop" in low)
    check("the feature request is described as auto-closed as stale, not as a product decision",
          "auto-closed as stale" in low and "closed as not-planned" not in low and "no first-party" not in low)
    check("always passes the resolved scope (a bare update can hit another scope's install)",
          "plugin update deep-map@<marketplace> --scope <scope>" in t and "always passing the scope" in low)
    check("verifies the new version landed (plugin list --json again; install path holds that version)",
          "must now show the newest version" in low and "install path" in low)
    check("does not verify by the recorded git commit (it can stay stale)", "recorded git commit" in low)
    check("retries once, then shows the CLI output and falls back to the /plugin manager",
          "once more" in low and "fall back to the `/plugin` manager" in t)
    # The two hosts' /plugin screens differ (read from the 2.1.284 binaries): the terminal has Discover / Installed /
    # Marketplaces with "Update marketplace" / "Update now"; the VS Code extension's "Manage Plugins" has Plugins /
    # Marketplaces with "Refresh marketplace" / "Update plugin".
    check("the /plugin fallback: Marketplaces first, then the installed `deep-map` plugin, with both hosts' labels",
          "`/plugin`" in t and "**Marketplaces**" in t and "**`deep-map`**" in t
          and t.find("**Marketplaces**") < t.find("**`deep-map`** plugin")
          and "**Refresh marketplace**" in t and "**Update marketplace**" in t
          and "**Plugins** tab in the VS Code extension" in t and "**Installed** in the terminal" in t
          and "**Update plugin**" in t and "**Update now**" in t)
    check("reports old -> new", "old → new" in t)

    # --- honesty about state
    check("--check: steps 1-3 only, installs nothing, and says the catalog refresh does change the local catalog",
          "`--check`" in t and "steps 1–3" in t and "installs nothing" in low and "local copy of the marketplace catalog" in low)
    check("no 'reversible' claim; no one-command rollback; the old version is kept 14 days (7 on older releases)",
          not re.search(r"(?<!not )\breversible\b", low) and "no one-command rollback" in low
          and re.search(r"\b14 days\b", low) is not None and re.search(r"\b7 on older\b", low) is not None)

    # --- activation
    check("IDE extensions: a NEW CHAT loads the new version; a full restart ends every running session",
          "open a **new chat**" in t and "ends every running claude session" in low
          and not _WINDOW_ADVICE.search(low), str(_WINDOW_ADVICE.findall(low)))
    check("says chats already open keep their version after a CLI update, but the Manage Plugins dialog reloads them",
          "chats already open keep their version after a cli update" in low and "reloads open chats in place" in low)
    check("terminal CLI: a new session, or /reload-plugins (only OLDER releases skipped the command index)",
          "/reload-plugins" in t and "new session" in low
          and "older releases did not rebuild the slash-command index" in low
          and "does not rebuild the slash-command index" not in low)
    check("desktop app / web: a new session, or restart the app", "start a new session, or restart the app" in low)
    check("keeps the post-update server guidance (heal on next session start; /deep-map:setup; /mcp)",
          "next session start" in t and "DEEPMAP_HEAL=0" in t and "/deep-map:setup" in t and "/mcp" in t)

    # --- commands/version.md
    check("version.md: the extension step is a new chat (no new-window / quit-the-IDE advice)",
          "open a **new chat**" in vt and not _WINDOW_ADVICE.search(vlow), str(_WINDOW_ADVICE.findall(vlow)))
    check("version.md compares LOADED with the INSTALLED pin (installed_plugins.json), not the catalog or a clone",
          "installed_plugins.json" in vt and "do **not** use the marketplace catalog" in vlow
          and "the newest `plugin.json` under `~/.claude/plugins/`" not in vt)
    check("version.md: a newer catalog / working copy means run /deep-map:plugin-update, not reload",
          "only the catalog or a working copy is newer" in vlow and "run `/deep-map:plugin-update`" in vt)
    check("version.md: terminal and desktop advice match the command",
          "start a new session, or restart the app" in vlow and "window reload alone is not enough" not in vlow
          and "older releases did not rebuild the slash-command index" in vlow
          and "doesn't rebuild the slash-command index" not in vlow)


def _is_claude(path: str) -> bool:
    try:
        out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=90,
                             encoding="utf-8", errors="replace").stdout
    except Exception:  # noqa: BLE001
        return False
    return "Claude Code" in (out or "")


def _host_claude() -> tuple[str | None, str]:
    execpath = os.environ.get("CLAUDE_CODE_EXECPATH")
    if execpath and Path(execpath).is_file() and _is_claude(execpath):
        return execpath, "CLAUDE_CODE_EXECPATH"
    found = shutil.which("claude")
    if found and _is_claude(found):
        return found, "PATH"
    return None, ""


def gate_b_host() -> None:
    print("\n-- B: the documented commands match the real host (skipped where absent) --")
    claude, source = _host_claude()
    if claude:
        def help_of(*args: str) -> str:
            try:
                return subprocess.run([claude, *args, "--help"], capture_output=True, text=True,
                                      timeout=90, encoding="utf-8", errors="replace").stdout
            except Exception as e:  # noqa: BLE001
                return f"<error {e!r}>"
        up = help_of("plugin", "update")
        check(f"`claude plugin update <plugin>` prints its Usage line and has --scope ({source} CLI)",
              "Usage: claude plugin update" in up and "update [options] <plugin>" in up and "--scope" in up, up[:200])
        mk = help_of("plugin", "marketplace")
        check(f"`claude plugin marketplace update [name]` exists ({source} CLI)", "update [options] [name]" in mk,
              mk[:300])
        ls = help_of("plugin", "list")
        check(f"`claude plugin list --json` exists ({source} CLI)", "--json" in ls, ls[:300])
    else:
        check("no Claude Code CLI here ($CLAUDE_CODE_EXECPATH / PATH absent or not Claude Code) - syntax check "
              "skipped", True)
    home = Path.home()
    exts = sorted(glob.glob(str(home / ".vscode" / "extensions" / "anthropic.claude-code-*")))
    with_bin = [e for e in exts if (Path(e) / "resources" / "native-binary").is_dir()]
    if with_bin:
        bins = [b for e in with_bin for b in glob.glob(str(Path(e) / "resources" / "native-binary" / "claude*"))]
        check("an installed VS Code extension ships its CLI where the command says", bool(bins), str(with_bin[-1]))
    else:
        check("no VS Code Claude extension with a bundled CLI here - bundled-binary path check skipped", True)
    cfg = Path(os.environ.get("CLAUDE_CONFIG_DIR") or (home / ".claude"))
    ip = cfg / "plugins" / "installed_plugins.json"
    plugins: dict = {}
    if ip.exists():
        try:
            plugins = json.loads(ip.read_text(encoding="utf-8")).get("plugins") or {}
        except Exception:  # noqa: BLE001
            plugins = {}
    mine = [k for k in plugins if k.split("@", 1)[0] == "deep-map"]
    legacy = [k for k in plugins if k.split("@", 1)[0] == "deepmap"]
    if mine:
        bad = [k for k in mine if not (isinstance(plugins[k], list) and plugins[k] and all(
            isinstance(e, dict) and {"scope", "version", "installPath"} <= set(e) for e in plugins[k]))]
        check("each installed `deep-map@` key maps to an array of installs with scope, version and installPath "
              "(what the command's fallback reads)", not bad, str(bad))
    elif legacy:
        check(f"only a pre-rename `deepmap@` install here ({legacy}) - registry check skipped", True)
    else:
        check("DeepMap not installed from a marketplace here - registry check skipped", True)


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
    print(f"DeepMap — /deep-map:plugin-update performs the update (root: {root})")
    print("=" * 72)
    gate_a_text(root)
    gate_b_host()
    total = len(PASSES) + len(FAILS)
    print("\n" + "=" * 72)
    if FAILS:
        print(f"RESULT: RED — {len(PASSES)}/{total} gates passed.")
        return 1
    print(f"RESULT: GREEN — {len(PASSES)}/{total} gates passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
