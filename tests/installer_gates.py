#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j gates for the installers (install.sh / install.ps1).

The build/ bug-hunts targeted the Python product + the benchmark; the installers — the user's first
touchpoint — were never covered. This locks their load-bearing wiring + go-public accuracy:

  * ROUND-9 K8: the installers write NO git hook themselves — both DELEGATE to the one implementation,
    `build/provision.py git-hook --install` (ROUND-8 J1: scoped block after the shebang, the project's
    sync root, refusals for non-sh / tracked / shared / husky hooks). Their own appended block broke
    Python hooks, never ran after an `exec`, synced the wrong root and clobbered .deepmap/instance.json.
    The behavior is locked end-to-end in tests/installer_hook_r9_gates.py; here the static wiring.
  * The hook they delegate to MUST call the REAL CLI sync entrypoint (`build/deepmap_cli.py sync
    <root>`), never a phantom `.deepmap/sync.py` the provisioner never creates. The phantom made the hook
    a silent no-op, contradicting the README's "a git-hook sync rebuilds the graph on commit" claim.
  * That hook must be COMMIT-SAFE (`|| true`, so a sync failure never blocks the user's commit),
    carry the `# deepmap` marker + excision sentinels, and run the sync DETACHED (`nohup … &` + a
    redirect to a DeepMap log) so `git commit` returns immediately — a big-repo graphify re-parse must
    never block the commit; never with a destructive flag (F1); its interpreter is DEEPMAP_PYTHON, else
    a concrete, absolute Python (F4).
  * The real entrypoint must actually exist (`build/deepmap_cli.py` with a `sync` subcommand).
  * Neither installer ships the obsolete "deployed product / not the shipped one" disclaimer
    (settled — this repo IS the product; Scenario A).

Pure: reads the installer text + builds provision's hook block in-process (no git, no files). Hermetic
self-checks prove the detectors are load-bearing (they fire on a synthetic OLD-style string and stay
quiet on the fixed one).
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SH = ROOT / "install.sh"
PS1 = ROOT / "install.ps1"
CLI = ROOT / "build" / "deepmap_cli.py"

PHANTOM = ".deepmap/sync.py"
OBSOLETE = re.compile(r"RECONSTRUCTED FROM SPEC|deployed product|not the shipped|when it is available",
                      re.IGNORECASE)

_PASS = 0
_FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {label}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}{(' — ' + detail) if detail else ''}")


# Detectors (self-checked below so the gates can't rot into tautologies) ------- #
def references_phantom_hook(text: str) -> bool:
    return PHANTOM in text


def references_real_sync(text: str) -> bool:
    """The hook calls the real CLI sync entrypoint: build/deepmap_cli.py ... sync."""
    return "deepmap_cli.py" in text and re.search(r"deepmap_cli\.py[\"']?\s+sync", text) is not None


def hook_is_commit_safe(text: str) -> bool:
    return "|| true" in text


def has_deepmap_marker(text: str) -> bool:
    return "# deepmap" in text


def hook_backgrounds_sync(text: str) -> bool:
    """The hook runs the sync DETACHED so `git commit` returns immediately: a subshell that backgrounds
    a nohup'd sync — `( [mkdir …;] [VAR=v …] nohup … & )`. A foreground `python … sync …` line does NOT
    match."""
    return re.search(r"\(\s*(?:[^;()]*;\s*)?(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*nohup\b[^\n]*&\s*\)", text) is not None


def hook_redirects_to_log(text: str) -> bool:
    """The backgrounded sync's output goes to a DeepMap log file (not git's terminal), which is also what
    releases git's stdout/stderr pipe so the commit returns: `>'<DEEPMAP_HOME>/sync.log' 2>&1` (an old
    installer: `>"$HOME/.deepmap/sync.log" 2>&1`)."""
    return re.search(r">\s*[\"']?[^\"'\s>]+\.log[\"']?\s+2>&1", text) is not None


def _code_lines(text: str) -> "list[str]":
    """The installer's code lines: PowerShell's `<# … #>` help block and `#` comments stripped."""
    text = re.sub(r"<#.*?#>", "", text, flags=re.DOTALL)
    return [ln.split("#", 1)[0] for ln in text.splitlines()]


def delegates_hook_install(text: str) -> bool:
    """The installer's hook step is a real CALL of `build/provision.py git-hook --install` (not a message
    that merely names the command): a code line running git-hook --install through the provision path."""
    code = _code_lines(text)
    calls = [ln for ln in code if "git-hook" in ln and "--install" in ln and "provision" in ln
             and not re.search(r"\b(echo|printf|Write-Host|Write-Warning|Write-Output)\b", ln)]
    return bool(calls) and any(re.search(r"build/provision\.py", ln) for ln in code)


def writes_hook_itself(text: str) -> bool:
    """The installer writes a hook file with its own code: an sh append / create onto a hook path, or a
    PowerShell Set-Content / Add-Content onto one (what the pre-K8 installers did)."""
    code = "\n".join(_code_lines(text))
    return re.search(r">>?\s*\"\$path\"|(Set|Add)-Content\s+(-Path\s+|-LiteralPath\s+)?\$path\b", code) is not None


def has_obsolete_disclaimer(text: str) -> bool:
    return OBSOLETE.search(text) is not None


def _is_install_line(line: str) -> bool:
    """A line that installs packages: a `pip install` (sh / ps1 arg list), or install.ps1's Install-PipSpecs
    helper, which pip-installs VERSION-PINNED specs through a temporary requirements file (E3)."""
    return ("pip" in line and "install" in line) or "Install-PipSpecs" in line


def non_ascii_chars(text: str) -> "list[str]":
    """The distinct non-ASCII characters in `text` (sorted)."""
    return sorted({ch for ch in text if ord(ch) > 127})


def ps1_readable_by_windows_powershell(raw: bytes) -> bool:
    """Windows PowerShell 5.1 — the powershell.exe every Windows box ships — reads a script WITHOUT a
    byte-order mark in the ANSI code page. A UTF-8 em dash (E2 80 94) then decodes (cp1252) to `â€"`,
    whose last character is a right double quotation mark, which PowerShell accepts as a STRING
    DELIMITER: `Write-Host "(bare — no LLM extras)"` stops parsing and the whole installer fails with
    MissingEndCurlyBrace before running a single line. Safe: pure ASCII, or a UTF-8 BOM."""
    return raw.startswith(b"\xef\xbb\xbf") or all(b < 0x80 for b in raw)


def pinned_spec_passed_on_command_line(text: str) -> "list[str]":
    """install.ps1 lines that hand a version-range spec (`<` / `>`) straight to pip as an ARGUMENT —
    cmd.exe (which runs a .bat Python shim such as pyenv-win's) would read it as a redirection and pip
    would install the package UNPINNED. Ranges must go through Install-PipSpecs (a requirements file)."""
    bad = []
    for ln in text.splitlines():
        code = ln.split("#", 1)[0]
        if '"pip"' in code and '"install"' in code and re.search(r'"[A-Za-z0-9_.\-]+\s*[<>]', code):
            bad.append(ln.strip())
    return bad


def gate_installer(name: str, path: Path) -> None:
    print(f"\n-- {name} --")
    check(f"{name} exists", path.exists())
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    check(f"{name}: git hook does NOT call the phantom {PHANTOM}", not references_phantom_hook(text),
          "the provisioner never creates it — the hook would be a silent no-op")
    check(f"{name}: the git hook is written by `build/provision.py git-hook --install` (delegated, K8)",
          delegates_hook_install(text))
    check(f"{name}: the installer writes no hook file with its own code (no blind append — K8)",
          not writes_hook_itself(text))
    check(f"{name}: no obsolete 'deployed product / not the shipped' disclaimer",
          not has_obsolete_disclaimer(text))


def gate_delegated_block() -> None:
    """The block `provision.py git-hook --install` writes — what both installers now install — keeps every
    property the installers' own hook line was gated on (real entrypoint, commit-safe, marker, detached,
    logged) plus F1 (no destructive flag, excisable sentinels) and F4 (the interpreter lever)."""
    print("\n-- the hook block the installers delegate to (build/provision.py _git_hook_block) --")
    sys.path.insert(0, str(ROOT / "build"))
    try:
        import provision as P   # type: ignore
    except Exception as exc:   # noqa: BLE001
        check("build/provision.py imports", False, repr(exc))
        return
    fn = getattr(P, "_git_hook_block", None)
    check("provision exposes _git_hook_block (the one hook writer)", callable(fn))
    if not callable(fn):
        return
    saved = {k: os.environ.get(k) for k in ("DEEPMAP_HOME", "DEEPMAP_PYTHON")}
    home = ROOT / "no-such-dir-dm-installer-gates" / "home"   # only formatted into text, never touched
    root = ROOT / "no-such-dir-dm-installer-gates" / "proj"
    try:
        os.environ["DEEPMAP_HOME"] = str(home)
        os.environ.pop("DEEPMAP_PYTHON", None)
        block = fn(str(root), str(root))
        check("the block calls the real entrypoint (build/deepmap_cli.py sync)", references_real_sync(block), block)
        check("... and never the phantom .deepmap/sync.py", not references_phantom_hook(block), block)
        check("the block is commit-safe (|| true)", hook_is_commit_safe(block), block)
        check("the block carries the '# deepmap' marker", has_deepmap_marker(block), block)
        check("the block is wrapped in the excision sentinels (# >>> deepmap … # <<< deepmap)",
              block.startswith("# >>> deepmap") and "# <<< deepmap" in block, block)
        check("the block backgrounds the sync (detached — git commit returns immediately)",
              hook_backgrounds_sync(block), block)
        check("the block redirects the sync's output to DEEPMAP_HOME's sync.log (not the terminal)",
              hook_redirects_to_log(block) and (home / "sync.log").as_posix() in block, block)
        check("the block never passes a destructive flag (--stale-cleanup / --switch-project / --force-prune, F1)",
              not re.search(r"--(stale-cleanup|switch-project|force-prune)\b", block), block)
        check("F4: with no DEEPMAP_PYTHON the interpreter is this concrete, absolute Python (never a bare python3)",
              Path(sys.executable).as_posix() in block and not re.search(r"nohup\s+'?python3?'?\s", block), block)
        os.environ["DEEPMAP_PYTHON"] = "/opt/dm-venv/bin/python"
        check("F4: DEEPMAP_PYTHON is honoured as the hook's interpreter",
              "nohup '/opt/dm-venv/bin/python'" in fn(str(root), str(root)))
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def gate_runtime_deps() -> None:
    """Both installers + the self-check + requirements MUST cover the import-time runtime deps
    (neo4j, mcp) the provisioner and MCP servers load at module top — else a fresh install
    'succeeds' then crashes at `provision.py setup` (bug-hunt #3 HIGH)."""
    print("\n-- runtime deps (neo4j, mcp) are installed + self-checked --")
    import re as _re
    for name, path in (("install.sh", SH), ("install.ps1", PS1)):
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        # a single pip-install line (or two) that mentions BOTH neo4j and mcp
        pip_lines = [ln for ln in text.splitlines() if _is_install_line(ln)]
        joined = "\n".join(pip_lines)
        check(f"{name}: pip-installs neo4j", _re.search(r"\bneo4j\b", joined) is not None)
        check(f"{name}: pip-installs mcp", _re.search(r"\bmcp\b", joined) is not None)
    selfcheck = ROOT / "scripts" / "ml_stack_selfcheck.py"
    if selfcheck.exists():
        src = selfcheck.read_text(encoding="utf-8")
        m = _re.search(r"REQUIRED\s*=\s*\[(.*?)\]", src, _re.DOTALL)
        req = m.group(1) if m else ""
        check("ml_stack_selfcheck REQUIRED includes neo4j", '"neo4j"' in req or "'neo4j'" in req)
        check("ml_stack_selfcheck REQUIRED includes mcp", '"mcp"' in req or "'mcp'" in req)
    reqs = ROOT / "requirements.txt"
    if reqs.exists():
        rt = reqs.read_text(encoding="utf-8")
        check("requirements.txt lists neo4j", _re.search(r"(?m)^\s*neo4j\b", rt) is not None)
        check("requirements.txt lists mcp", _re.search(r"(?m)^\s*mcp\b", rt) is not None)


def gate_extractor_engine() -> None:
    """The multi-language extraction engine (import graphify / pip graphifyy) MUST ship with the
    product: both installers pip-install it, requirements.txt lists it, and the self-check verifies
    it. Without this, DEEPMAP_EXTRACTOR=graphify silently falls back to ast (Python-only) on a fresh
    install — the exact 'multi-language claimed but not real' bug this P0 build closes. And it must be
    installed BARE (no `graphifyy[all]`), else optional LLM SDKs would break zero-egress-by-default."""
    import re as _re
    print("\n-- multi-language extractor engine (graphify) is installed + self-checked --")
    for name, path in (("install.sh", SH), ("install.ps1", PS1)):
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        # Strip shell/PowerShell `#` comments so a "NEVER install graphifyy[all]" warning in the
        # installer's own prose can't false-trip the extras check; keep only real command text.
        code_lines = [ln.split("#", 1)[0] for ln in text.splitlines()]
        pip_lines = [ln for ln in code_lines if _is_install_line(ln)]
        joined = "\n".join(pip_lines)
        check(f"{name}: pip-installs graphifyy (the multi-language engine)",
              _re.search(r"\bgraphifyy\b", joined) is not None,
              "fresh install falls back to ast/Python-only without it")
        check(f"{name}: does NOT install graphifyy[all] (would add LLM SDKs; breaks zero-egress)",
              _re.search(r"graphifyy\[", joined) is None)
    selfcheck = ROOT / "scripts" / "ml_stack_selfcheck.py"
    if selfcheck.exists():
        src = selfcheck.read_text(encoding="utf-8")
        check("ml_stack_selfcheck verifies the graphify engine (check_extractor)",
              "def check_extractor" in src and "find_spec(\"graphify\")" in src)
        check("ml_stack_selfcheck runnable-smoke uses --no-cluster (NO LLM / NO network)",
              "--no-cluster" in src)
    reqs = ROOT / "requirements.txt"
    if reqs.exists():
        rt = reqs.read_text(encoding="utf-8")
        check("requirements.txt lists graphifyy",
              _re.search(r"(?m)^\s*graphifyy\b", rt) is not None)
        # Only real requirement specs count — strip comments so a "NEVER install graphifyy[all]"
        # warning in the file's own prose doesn't false-trip the extras check.
        req_specs = "\n".join(ln.split("#", 1)[0] for ln in rt.splitlines())
        check("requirements.txt does NOT pin graphifyy[all]",
              _re.search(r"graphifyy\[", req_specs) is None)
    cli = ROOT / "build" / "deepmap_cli.py"
    if cli.exists():
        src = cli.read_text(encoding="utf-8")
        check("deepmap doctor probes the extractor engine",
              '"extractor"' in src and "DEEPMAP_EXTRACTOR" in src)
    # The default extractor is `auto` (graphify-preferred, ast fallback) — ADR-015 (amended). Lock it
    # so the multi-language default can't silently revert to the Python-only `ast` path.
    sync = ROOT / "build" / "sync.py"
    if sync.exists():
        src = sync.read_text(encoding="utf-8")
        defaults = _re.findall(r'os\.environ\.get\(\s*["\']DEEPMAP_EXTRACTOR["\']\s*,\s*["\'](\w+)["\']', src)
        check("build/sync.py defaults DEEPMAP_EXTRACTOR to 'auto' (multi-language by default)",
              bool(defaults) and all(d == "auto" for d in defaults),
              f"found defaults: {defaults}")


def gate_ps1_windows_powershell() -> None:
    """install.ps1 must PARSE under Windows PowerShell 5.1 (the default powershell.exe) — run_all's own
    parse check prefers pwsh 7, which reads BOM-less files as UTF-8, so it never saw this — and its pinned
    specs must reach pip without passing through cmd.exe redirection parsing."""
    print("\n-- install.ps1 runs under Windows PowerShell 5.1 + pinned specs survive a .bat Python shim --")
    if not PS1.exists():
        check("install.ps1 exists", False)
        return
    raw = PS1.read_bytes()
    check("install.ps1 is readable by Windows PowerShell 5.1 (pure ASCII or a UTF-8 BOM)",
          ps1_readable_by_windows_powershell(raw),
          f"non-ASCII characters without a BOM: {non_ascii_chars(raw.decode('utf-8', 'replace'))}")
    import shutil as _sh
    import subprocess as _sp
    win_ps = _sh.which("powershell") if os.name == "nt" else None
    if win_ps:
        esc = str(PS1).replace("'", "''")
        script = ("$t=$null; $e=$null; [void][System.Management.Automation.Language.Parser]::ParseFile("
                  f"'{esc}',[ref]$t,[ref]$e); if ($e.Count -gt 0) {{ $e | ForEach-Object {{ "
                  "Write-Output ($_.Extent.StartLineNumber.ToString() + ': ' + $_.Message) }; exit 1 } "
                  "else { exit 0 }")
        try:
            p = _sp.run([win_ps, "-NoProfile", "-NonInteractive", "-Command", script],
                        capture_output=True, text=True, timeout=120)
            check("Windows PowerShell 5.1 (powershell.exe) parses install.ps1 with 0 errors",
                  p.returncode == 0, (p.stdout or p.stderr).strip()[:400])
        except Exception as exc:  # noqa: BLE001 — an unusable powershell.exe is a skip, not a verdict
            print(f"  [skip] powershell.exe parse: {exc}")
    else:
        print("  [skip] powershell.exe (Windows PowerShell 5.1) not available here — the ASCII check covers it")
    text = raw.decode("utf-8", "replace")
    check("no version-range spec is passed to pip as a command-line argument (cmd.exe redirection)",
          not pinned_spec_passed_on_command_line(text), str(pinned_spec_passed_on_command_line(text)))
    m = re.search(r"function Install-PipSpecs\b.*?\n}", text, re.DOTALL)
    body = m.group(0) if m else ""
    check("Install-PipSpecs installs from a temporary requirements file (-r) and removes it",
          bool(body) and '"-r"' in body and "WriteAllLines" in body and "Remove-Item" in body, body[:300])
    check("the pinned installs go through Install-PipSpecs",
          len(re.findall(r"^\s*Install-PipSpecs\s+\$PyCmd\s+@\(", text, re.MULTILINE)) >= 2)


def gate_entrypoint_exists() -> None:
    print("\n-- the real sync entrypoint exists --")
    check("build/deepmap_cli.py exists", CLI.exists())
    if CLI.exists():
        src = CLI.read_text(encoding="utf-8")
        check("deepmap_cli.py defines a 'sync' subcommand", 'add_parser("sync")' in src)


def gate_selfcheck() -> None:
    print("\n-- self-check: detectors are load-bearing --")
    old = 'python3 ".deepmap/sync.py" || true   # deepmap\n[RECONSTRUCTED FROM SPEC] not the shipped one'
    new = 'python3 "$DIR/build/deepmap_cli.py" sync "$REPO" || true   # deepmap'
    check("phantom detector fires on OLD installer", references_phantom_hook(old) is True)
    check("phantom detector quiet on NEW installer", references_phantom_hook(new) is False)
    check("real-sync detector fires on NEW installer", references_real_sync(new) is True)
    check("real-sync detector quiet on OLD installer", references_real_sync(old) is False)
    check("obsolete-disclaimer detector fires on OLD installer", has_obsolete_disclaimer(old) is True)
    check("obsolete-disclaimer detector quiet on NEW installer", has_obsolete_disclaimer(new) is False)
    # Backgrounding: prove the detectors fire on the NEW detached line and stay quiet on the OLD
    # foreground one (the exact change this build makes).
    fg = 'python3 "$DIR/build/deepmap_cli.py" sync "$REPO" --stale-cleanup || true   # deepmap'
    bg = ('mkdir -p "$HOME/.deepmap" 2>/dev/null; ( nohup python3 "$DIR/build/deepmap_cli.py" sync '
          '"$REPO" --stale-cleanup >"$HOME/.deepmap/sync.log" 2>&1 & ) || true   # deepmap')
    prov = ("if [ \"$(git rev-parse --show-toplevel 2>/dev/null)\" -ef '/r' ]; then ( mkdir -p '/h' 2>/dev/null; "
            "PYTHONUTF8=1 nohup '/usr/bin/python3' '/p/build/deepmap_cli.py' sync '/r' >'/h/sync.log' 2>&1 & ) "
            "|| true; fi   # deepmap")
    check("backgrounding detector fires on the old installer's detached line", hook_backgrounds_sync(bg) is True)
    check("backgrounding detector fires on provision's block line (mkdir; VAR=v nohup … &)",
          hook_backgrounds_sync(prov) is True)
    check("backgrounding detector quiet on the OLD foreground line", hook_backgrounds_sync(fg) is False)
    check("log detector fires on both detached lines", hook_redirects_to_log(bg) and hook_redirects_to_log(prov))
    check("log detector quiet on the OLD foreground line", hook_redirects_to_log(fg) is False)
    check("real-sync detector fires on provision's single-quoted form", references_real_sync(prov) is True)
    # K8: delegation vs an installer that writes the hook itself
    old_sh = ('for hook in post-commit post-checkout; do\n  local path="$hooks_dir/$hook"\n'
              '  { echo ""; echo "$deepmap_block"; } >> "$path"\ndone')
    old_ps = ('$chained = $existing.TrimEnd() + "`n`n" + $deepmapBlock + "`n"\n'
              'Set-Content -Path $path -Value $chained -NoNewline')
    new_sh = ('local provision="$SCRIPT_DIR/build/provision.py"\n'
              'echo "opt in later: python3 \\"$provision\\" git-hook --install"\n'
              'py "$provision" git-hook --install "$REPO_ROOT" || rc=$?')
    new_ps = ('$provision = Join-Path $ScriptDir "build/provision.py"\n'
              '$hookArgs = @($provision, "git-hook", "--install", $Repo)')
    msg_only = 'local provision="$SCRIPT_DIR/build/provision.py"\necho "run: python3 \\"$provision\\" git-hook --install"'
    check("self-writer detector fires on the OLD sh / ps1 hook writers",
          writes_hook_itself(old_sh) and writes_hook_itself(old_ps))
    check("self-writer detector quiet on the delegating installers",
          not writes_hook_itself(new_sh) and not writes_hook_itself(new_ps))
    check("delegation detector fires on the delegating sh / ps1 steps",
          delegates_hook_install(new_sh) and delegates_hook_install(new_ps))
    check("delegation detector quiet on the OLD writers and on a mere message naming the command",
          not delegates_hook_install(old_sh) and not delegates_hook_install(old_ps)
          and not delegates_hook_install(msg_only))
    # Windows PowerShell 5.1 readability + the cmd.exe redirection trap (E3 pins).
    check("ps1-readable detector rejects a BOM-less UTF-8 em dash",
          ps1_readable_by_windows_powershell('Write-Host "a — b"'.encode("utf-8")) is False)
    check("ps1-readable detector accepts pure ASCII and a BOM-prefixed file",
          ps1_readable_by_windows_powershell(b'Write-Host "a - b"')
          and ps1_readable_by_windows_powershell(b"\xef\xbb\xbf" + 'x —'.encode("utf-8")))
    check("command-line-spec detector fires on a direct pip arg with a range",
          pinned_spec_passed_on_command_line('Invoke-Py $PyCmd @("-m", "pip", "install", "mcp>=1.2,<2")') != [])
    check("command-line-spec detector quiet on the Install-PipSpecs call and a bare install",
          pinned_spec_passed_on_command_line('Install-PipSpecs $PyCmd @("mcp>=1.2,<2")\n'
                                             'Invoke-Py $PyCmd @("-m", "pip", "install", "torch")') == [])


def _load_selfcheck():
    import importlib.util as _iu
    spec = _iu.spec_from_file_location("dm_ml_stack_selfcheck_gate", ROOT / "scripts" / "ml_stack_selfcheck.py")
    mod = _iu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def gate_ml_selfcheck_pins() -> None:
    """ROUND-4 F3: the install-time self-check (both installers run it last) must (a) print the PINNED,
    quoted, --upgrade graphify install command — a bare `pip install graphifyy` resolves to 0.9.x, outside
    the validated range — and (b) FAIL LOUD when an installed neo4j / mcp / graphifyy sits outside the
    range requirements.txt pins (an old copy pip kept as "already satisfied", or a later drift)."""
    import importlib.util as _iu
    print("\n-- ml_stack_selfcheck: pinned graphify hint + installed versions inside the pinned ranges --")
    if not (ROOT / "scripts" / "ml_stack_selfcheck.py").exists():
        check("scripts/ml_stack_selfcheck.py exists", False)
        return
    M = _load_selfcheck()
    sys.path.insert(0, str(ROOT / "build"))
    import graphify_extract as GX
    real_find = _iu.find_spec
    _iu.find_spec = lambda name, *a, **k: None if name == "graphify" else real_find(name, *a, **k)
    try:
        msg = M.check_extractor(runnable=False) or ""
    finally:
        _iu.find_spec = real_find
    check("graphify missing -> the remedy is graphify_extract's pinned, quoted --upgrade command",
          GX.graphify_install_command() in msg, msg)
    check("...never a bare `pip install graphifyy` (resolves to an unvalidated 0.9.x)",
          not re.search(r"pip install graphifyy(?![>=<\[])", msg), msg)
    specs = M.pinned_specs() if hasattr(M, "pinned_specs") else {}
    check("pinned_specs() reads neo4j / mcp / graphifyy ranges from requirements.txt",
          set(specs) == {"neo4j", "mcp", "graphifyy"} and all(specs.values()), str(specs))
    check("...and its graphifyy range is graphify_extract.SUPPORTED_GRAPHIFY_SPEC's",
          f"graphifyy{specs.get('graphifyy', '')}".replace(" ", "") == GX.SUPPORTED_GRAPHIFY_SPEC.replace(" ", "")
          or sorted(specs.get("graphifyy", "").split(",")) == sorted(GX.SUPPORTED_GRAPHIFY_SPEC[len("graphifyy"):].split(",")),
          f"{specs.get('graphifyy')} vs {GX.SUPPORTED_GRAPHIFY_SPEC}")
    if not hasattr(M, "check_pins"):
        check("ml_stack_selfcheck has check_pins()", False)
        return
    import contextlib as _cl, io as _io

    def pins(versions):
        with _cl.redirect_stdout(_io.StringIO()), _cl.redirect_stderr(_io.StringIO()):
            return M.check_pins(version_of=versions)

    good = {"neo4j": "6.3.1", "mcp": "1.30.0", "graphifyy": "0.8.51"}
    check("all installed inside the ranges -> no pin failure", pins(good.get) == [])
    old_mcp = pins({**good, "mcp": "1.13.1"}.get)
    check("an old mcp 1.13.1 (kept by pip as 'already satisfied') -> ONE failure naming it",
          len(old_mcp) == 1 and "mcp" in old_mcp[0] and "1.13.1" in old_mcp[0], str(old_mcp))
    check("...whose remedy is the quoted, --upgrade, pinned command for THAT package",
          bool(old_mcp) and f'-m pip install --upgrade "mcp{specs.get("mcp", "")}"' in old_mcp[0]
          and old_mcp[0].count('"') >= 4, str(old_mcp))
    check("a neo4j 5.x driver and a graphifyy 0.9 are flagged too",
          len(pins({**good, "neo4j": "5.28.6", "graphifyy": "0.9.3"}.get)) == 2)
    check("a package that is not installed is not a PIN failure (the import checks report it)",
          pins(lambda p: None if p == "graphifyy" else good[p]) == [])
    saved = (M.check_imports, M.check_extractor, M.check_pins, sys.argv)
    try:
        M.check_imports = lambda: []
        M.check_extractor = lambda runnable=True: None
        M.check_pins = lambda **k: ["mcp 1.13.1 is outside the validated range"]
        sys.argv = ["ml_stack_selfcheck.py", "--imports-only"]
        with _cl.redirect_stdout(_io.StringIO()), _cl.redirect_stderr(_io.StringIO()):
            rc = M.main()
    finally:
        M.check_imports, M.check_extractor, M.check_pins, sys.argv = saved
    check("main() FAILS (exit 1) when a pinned package is out of range — the installer then stops", rc == 1)


def gate_model_ignore_mirror() -> None:
    """2026-10-22: both installers pre-downloaded the WHOLE model repository (about 2.5 GB, ~1.9 GB of it ONNX /
    OpenVINO exports sentence-transformers never loads) while saying ~300 MB. They now skip the same patterns as
    /deep-map:setup's own environment step (build/deps_bootstrap.py MODEL_IGNORE): one skip list, three copies,
    kept equal here."""
    print("\n-- installers skip the same model exports as setup (deps_bootstrap.MODEL_IGNORE) --")
    sys.path.insert(0, str(ROOT / "build"))
    import deps_bootstrap as DB
    want = tuple(DB.MODEL_IGNORE)
    check("deps_bootstrap.MODEL_IGNORE is non-empty (the mirror below is not vacuous)", bool(want), repr(want))
    for name, path in (("install.sh", SH), ("install.ps1", PS1)):
        text = path.read_text(encoding="utf-8")
        m = re.search(r"snapshot_download\('\$(?:MODEL|Model)',\s*ignore_patterns=\[([^\]]*)\]\)", text)
        got = tuple(re.findall(r"'([^']+)'", m.group(1))) if m else ()
        check(f"{name}: the model pre-download passes ignore_patterns equal to MODEL_IGNORE", got == want,
              f"installer={got!r} setup={want!r}")
        check(f"{name}: no stale '~300MB' model size", "300MB" not in text and "300 MB" not in text)


def gate_selfcheck_offline_first() -> None:
    """ROUND-8 J6 (live L2f-5: one install-time self-check ran ~33 min with the model already cached): both
    installers pre-download the model BEFORE running the self-check, and the self-check verifies a cached
    model OFFLINE (one local_files_only load, no online attempt) with every phase bounded — so the normal
    install path needs no network for the check and can't hang silently. The detailed behaviour is locked
    in tests/selfcheck_r8_gates.py; this pins the installer-facing contract."""
    print("\n-- installers + self-check: pre-download first, then an OFFLINE-first, bounded model check --")
    sh, ps1 = SH.read_text(encoding="utf-8"), PS1.read_text(encoding="utf-8")
    sh_main = sh[sh.rfind("install_ml_stack\n"):] if "install_ml_stack\n" in sh else ""
    check("install.sh runs install_ml_stack (the model pre-download) BEFORE test_ml_stack (the self-check)",
          "snapshot_download" in sh and 0 <= sh_main.find("install_ml_stack") < sh_main.find("test_ml_stack"))
    check("install.ps1 runs Install-MLStack (the model pre-download) BEFORE Test-MLStack (the self-check)",
          "snapshot_download" in ps1
          and 0 <= ps1.rfind("Install-MLStack $py") < ps1.rfind("Test-MLStack $py"))
    M = _load_selfcheck()
    if not (hasattr(M, "check_model") and hasattr(M, "load_model")):
        check("ml_stack_selfcheck has check_model(loader=...) + load_model()", False)
        return
    import contextlib as _cl, io as _io
    calls: list = []

    class _M:
        def encode(self, text):
            return [0.0] * M.EXPECT_DIM

    def loader(local_files_only):
        calls.append(local_files_only)
        return _M()
    with _cl.redirect_stdout(_io.StringIO()), _cl.redirect_stderr(_io.StringIO()):
        err = M.check_model(loader=loader, offline_budget=30, download_budget=30)
    check("a cached model is verified with ONE offline (local_files_only) load and no online attempt",
          err is None and calls == [True], (err, calls))
    doc = M.__doc__ or ""
    check("the self-check documents --offline and its three phase limits (the knobs for a slow install)",
          all(k in doc for k in ("--offline", "DEEPMAP_SELFCHECK_IMPORT_TIMEOUT", "DEEPMAP_SELFCHECK_MODEL_TIMEOUT",
                                 "DEEPMAP_SELFCHECK_DOWNLOAD_TIMEOUT")))
    saved = (M.check_imports, M.check_extractor, M.check_pins, sys.argv)
    try:
        M.check_imports = lambda: []
        M.check_extractor = lambda runnable=True: None
        M.check_pins = lambda **k: []
        sys.argv = ["ml_stack_selfcheck.py", "--offline", "--imports-only"]
        with _cl.redirect_stdout(_io.StringIO()), _cl.redirect_stderr(_io.StringIO()):
            try:
                rc = M.main()
            except SystemExit as exc:      # argparse rejecting the flag
                rc = f"SystemExit {exc.code}"
    finally:
        M.check_imports, M.check_extractor, M.check_pins, sys.argv = saved
    check("`ml_stack_selfcheck.py --offline` is accepted (exit 0 on a healthy stubbed stack)", rc == 0, rc)


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 64)
    print("DeepMap — installer gates (git-hook delegation + hook block + accuracy, no Neo4j)")
    print("=" * 64)
    gate_installer("install.sh", SH)
    gate_installer("install.ps1", PS1)
    gate_delegated_block()
    gate_runtime_deps()
    gate_extractor_engine()
    gate_ps1_windows_powershell()
    gate_entrypoint_exists()
    gate_ml_selfcheck_pins()
    gate_model_ignore_mirror()
    gate_selfcheck_offline_first()
    gate_selfcheck()
    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
