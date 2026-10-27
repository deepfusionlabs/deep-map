#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j gates for the installer git-hook hardening (F1) + the DEEPMAP_PYTHON interpreter lever (F4).

The 2026-07 field audit raised two installer/interpreter defects:

  F1 (git-hook safety):
    * The auto-generated post-commit/post-checkout hook passed `--stale-cleanup` to `deepmap_cli.py
      sync`. A background hook must NEVER carry a destructive flag: a routine commit/checkout should
      rebuild the graph, not authorize any cleanup path. Drop it from the written hook line.
    * The CREATE branch (no pre-existing hook) wrote a BARE `#!/bin/sh\\n<line>` with no excision
      sentinels, while the CHAIN branch wrapped its addition in `# >>> deepmap … # <<< deepmap`. So an
      uninstall could cleanly excise a CHAINED block but not a CREATED one. Wrap BOTH in the sentinels.

  F4 (interpreter lever):
    * The hook line + the printed 'run this' hints + the CLI's printed access-hook snippet hardcoded a
      bare `python` / `python3`. On a machine whose interpreter is `py`, a venv, or only `python3`
      (or Windows, which has NO python3.exe), those commands don't resolve. Honor a DEEPMAP_PYTHON
      override; in the CLI, default to sys.executable (the interpreter already running — it provably
      exists). NEVER blindly rewrite python -> python3 (breaks Windows).

ROUND-9 K8: the installers now DELEGATE the hook to `build/provision.py git-hook --install`, so F1/F4 are
checked on provision's block (the installer-line detectors below stay, self-checked, for the history).
Pure/static for the installers (reads the text, self-checks the detectors); behavioral for the CLI
helper. No Neo4j, no network, no spend.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SH = ROOT / "install.sh"
PS1 = ROOT / "install.ps1"
sys.path.insert(0, str(ROOT / "build"))

_PASS = _FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {label}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}{(' — ' + detail) if detail else ''}")


# ---- detectors (self-checked below) ---------------------------------------- #
def hook_sync_line(text: str) -> str:
    """The installer's git-hook line assignment — the string WRITTEN into the hook (contains the
    `deepmap_cli.py … sync` call). Returns '' if not found."""
    for ln in text.splitlines():
        s = ln.strip()
        if ("deepmap_line" in s or "deepmapLine" in s) and "deepmap_cli.py" in s and "sync" in s \
                and "=" in s:
            return s
    return ""


def hook_uses_stale_cleanup(text: str) -> bool:
    return "--stale-cleanup" in hook_sync_line(text)


def hook_interpreter_is_levered(text: str) -> bool:
    """The hook line invokes the interpreter via a shell/PowerShell VARIABLE (the DEEPMAP_PYTHON lever),
    not a hardcoded `python`/`python3` literal immediately before deepmap_cli.py."""
    line = hook_sync_line(text)
    if not line:
        return False
    # a `python`/`python3` token directly invoking the cli path (any quoting/backtick run, no space) ==
    # NOT levered; a shell/PS variable ($hook_py / $hookPy) in that slot == levered.
    return re.search(r"\bpython3?\s+\S*deepmap_cli\.py", line) is None


def honors_deepmap_python(text: str) -> bool:
    return "DEEPMAP_PYTHON" in text


def create_branch_uses_block(text: str, kind: str) -> bool:
    """The CREATE (no pre-existing hook) branch writes a sentinel-wrapped block variable, not the bare
    hook line — so both create and chain produce an excisable block."""
    if kind == "sh":
        return re.search(r'printf[^\n]*"\$deepmap_block"', text) is not None
    return re.search(r'Set-Content[^\n]*\$deepmapBlock', text) is not None


def block_var_has_sentinels(text: str, kind: str) -> bool:
    """The block variable is defined adjacent to the deepmap OPEN sentinel (and a CLOSE sentinel
    exists) — proving the created hook body is excisable."""
    var = "deepmap_block=" if kind == "sh" else r"\$deepmapBlock\s*="
    open_near = re.search(var + r".{0,120}?>>> deepmap", text, re.DOTALL) is not None
    close_exists = re.search(r">>> deepmap.{0,600}?<<< deepmap", text, re.DOTALL) is not None
    return open_near and close_exists


# ---- installer gates -------------------------------------------------------- #
def gate_installer(name: str, path: Path, kind: str) -> None:
    """ROUND-9 K8: the installers no longer write a hook line of their own — both delegate to
    `build/provision.py git-hook --install` (the ONE hook writer, ROUND-8 J1). F1/F4 therefore hold for the block
    provision writes (gate_delegated_block below); tests/installer_hook_r9_gates.py runs each installer's hook step
    end-to-end and tests/installer_gates.py locks the delegation."""
    print(f"\n-- {name}: F1 hook-safety + F4 interpreter lever (the hook is provision's) --")
    check(f"{name} exists", path.exists())
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    check(f"{name}: writes NO hook line of its own (it delegates to provision.py git-hook --install)",
          not hook_sync_line(text) and "git-hook" in text and "--install" in text)
    # F1a — no destructive flag anywhere near the installer's hook step
    code = "\n".join(ln.split("#", 1)[0] for ln in re.sub(r"<#.*?#>", "", text, flags=re.DOTALL).splitlines())
    check(f"{name}: the installer's code never passes --stale-cleanup to a hook", "--stale-cleanup" not in code)
    # F4 — interpreter lever (the hook block honours DEEPMAP_PYTHON; the installer documents / honours it)
    check(f"{name}: honors the DEEPMAP_PYTHON override", honors_deepmap_python(text))


def gate_delegated_block() -> None:
    print("\n-- F1/F4 on the block the installers delegate to (build/provision.py _git_hook_block) --")
    try:
        import provision as P   # type: ignore
    except Exception as e:  # noqa: BLE001
        check("build/provision.py imports", False, repr(e))
        return
    saved = os.environ.get("DEEPMAP_PYTHON")
    try:
        os.environ.pop("DEEPMAP_PYTHON", None)
        block = P._git_hook_block("/r", "/r")
        check("F1: the hook block never passes --stale-cleanup", "--stale-cleanup" not in block, block)
        check("F1: the block is wrapped in the excision sentinels (created or chained alike)",
              block.startswith("# >>> deepmap") and "# <<< deepmap" in block, block)
        check("F4: with no override the interpreter is this concrete Python, never a hardcoded python/python3",
              re.search(r"nohup\s+'?python3?'?\s", block) is None and Path(sys.executable).as_posix() in block, block)
        os.environ["DEEPMAP_PYTHON"] = "/opt/venv/bin/python"
        check("F4: DEEPMAP_PYTHON is honoured as the hook's interpreter",
              "nohup '/opt/venv/bin/python'" in P._git_hook_block("/r", "/r"))
    finally:
        if saved is None:
            os.environ.pop("DEEPMAP_PYTHON", None)
        else:
            os.environ["DEEPMAP_PYTHON"] = saved


def gate_self_checks() -> None:
    print("\n-- self-check: the detectors are load-bearing --")
    old_sh = ('local deepmap_line="mkdir -p x; ( nohup python3 \"$DIR/build/deepmap_cli.py\" sync '
              '\"$REPO\" --stale-cleanup >log 2>&1 & ) || true   # deepmap"')
    new_sh = ('local deepmap_line="mkdir -p x; ( nohup $hook_py \"$DIR/build/deepmap_cli.py\" sync '
              '\"$REPO\" >log 2>&1 & ) || true   # deepmap"')
    check("stale-cleanup detector fires on OLD sh line", hook_uses_stale_cleanup(old_sh) is True)
    check("stale-cleanup detector quiet on NEW sh line", hook_uses_stale_cleanup(new_sh) is False)
    check("interpreter-levered detector fires (True) on NEW sh line (variable interp)",
          hook_interpreter_is_levered(new_sh) is True)
    check("interpreter-levered detector quiet (False) on OLD sh line (hardcoded python3)",
          hook_interpreter_is_levered(old_sh) is False)
    old_ps = ('$deepmapLine = "mkdir -p x; ( nohup python `"$Dir/build/deepmap_cli.py`" sync `"$R`" '
              '--stale-cleanup >log 2>&1 & ) || true   # deepmap"')
    new_ps = ('$deepmapLine = "mkdir -p x; ( nohup $hookPy `"$Dir/build/deepmap_cli.py`" sync `"$R`" '
              '>log 2>&1 & ) || true   # deepmap"')
    check("stale-cleanup detector fires on OLD ps1 line", hook_uses_stale_cleanup(old_ps) is True)
    check("stale-cleanup detector quiet on NEW ps1 line", hook_uses_stale_cleanup(new_ps) is False)
    check("interpreter-levered detector quiet (False) on OLD ps1 line (hardcoded python)",
          hook_interpreter_is_levered(old_ps) is False)
    check("interpreter-levered detector fires (True) on NEW ps1 line (variable interp)",
          hook_interpreter_is_levered(new_ps) is True)


# ---- CLI interpreter-lever gates ------------------------------------------- #
def gate_cli_helper() -> None:
    print("\n-- F4: deepmap_cli._python_interpreter() resolution --")
    try:
        import deepmap_cli as CLI
    except Exception as e:  # noqa: BLE001
        check("deepmap_cli imports for the helper test", False, repr(e))
        return
    fn = getattr(CLI, "_python_interpreter", None)
    check("deepmap_cli defines _python_interpreter()", callable(fn))
    if not callable(fn):
        return
    saved = os.environ.get("DEEPMAP_PYTHON")
    try:
        os.environ["DEEPMAP_PYTHON"] = "py -3"
        check("DEEPMAP_PYTHON override is honored", fn() == "py -3", repr(fn()))
        os.environ.pop("DEEPMAP_PYTHON", None)
        val = fn()
        check("with no override, resolves to sys.executable (a concrete, existing interpreter)",
              val == sys.executable and bool(val), repr(val))
        check("the resolved interpreter is never blindly 'python3'", val != "python3", repr(val))
    finally:
        if saved is None:
            os.environ.pop("DEEPMAP_PYTHON", None)
        else:
            os.environ["DEEPMAP_PYTHON"] = saved


def gate_cli_snippet_uses_helper() -> None:
    print("\n-- F4: the printed access-hook snippet is built from the interpreter lever --")
    src = (ROOT / "build" / "deepmap_cli.py").read_text(encoding="utf-8")
    # The snippet's command must be built from _python_interpreter(), not a hardcoded f'python "…"'.
    check("the access-hook snippet does not hardcode a bare 'python \"' command",
          re.search(r'''command["']?\s*:\s*f?['"]python\s+"''', src) is None,
          "found a hardcoded python command in the snippet")
    check("_python_interpreter() is referenced when emitting the snippet",
          "_python_interpreter(" in src)


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 64)
    print("DeepMap — installer hardening + DEEPMAP_PYTHON lever gates (F1/F4, no Neo4j)")
    print("=" * 64)
    gate_installer("install.sh", SH, "sh")
    gate_installer("install.ps1", PS1, "ps1")
    gate_delegated_block()
    gate_self_checks()
    gate_cli_helper()
    gate_cli_snippet_uses_helper()
    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
