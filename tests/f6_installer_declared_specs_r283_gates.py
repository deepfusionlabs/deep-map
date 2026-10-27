#!/usr/bin/env python3
"""0.28.3 (F6) — both installers install every requirement requirements.txt declares WITH a version constraint, with
that same constraint; a directly imported package that arrives through a pinned one (anyio, through mcp) is named in
that package's own install command.

Finding (round-1 O12, routed): requirements.txt declares `anyio>=4.5` because DeepMap imports anyio directly
(build/mcp_bounds.py runs each MCP tool body in a worker thread under a deadline; the search server bounds its roots
request with it). The installers — the path users actually install from — pin their packages separately from
requirements.txt and never named anyio, so the declared floor held on that path only through mcp's own metadata
(`Requires-Dist: anyio>=4.5` today). A direct import is now a direct install there too, in the SAME pip command as
mcp: pip then resolves anyio against mcp's own dependency tree, so `--upgrade` lifts it only as far as mcp's
dependencies allow (a separate `pip install --upgrade anyio` resolves anyio on its own; pip does not re-check every
installed package's requirements).

Locks (reads the installers' code lines — comments never count; no pip, no network):
  A  requirements.txt yields its constrained requirements (not vacuous: neo4j, mcp, graphifyy, anyio);
  B  per installer and per constrained requirement: installed, always with requirements.txt's exact specifier, always
     with --upgrade (install.ps1: through Install-PipSpecs — a `<`/`>` on pip's command line is a cmd.exe redirection
     under a .bat Python shim); a package that arrives through a pinned one (tests/compliance_gates.py
     DIRECT_VIA_PINNED) shares that package's install command;
  C  teeth: the pre-fix install lines, a separate anyio command, a divergent floor, a missing --upgrade, a raw ps1 pip
     argument and a comment-only mention are each flagged; an unconstrained name is out of scope.
Usage: python tests/f6_installer_declared_specs_r283_gates.py [--root <tree>]
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PASSES: list[str] = []
FAILS: list[str] = []
DEFAULT_VIA = {"anyio": ("mcp", "MIT")}   # used only when no tests/compliance_gates.py DIRECT_VIA_PINNED is readable

PRE_FIX_SH = ('  py -m pip install --upgrade "neo4j>=6.0.1,<7" "mcp>=1.24,<2" pyyaml\n'
              '  py -m pip install --upgrade "graphifyy>=0.8.39,<0.8.52"\n')
PRE_FIX_PS1 = ('function Install-PipSpecs([string]$PyCmd, [string[]]$Specs) {\n'
               '    Invoke-Py $PyCmd @("-m", "pip", "install", "--upgrade", "-r", $req)\n}\n'
               '  Install-PipSpecs $PyCmd @("neo4j>=6.0.1,<7", "mcp>=1.24,<2", "pyyaml")\n'
               '  Install-PipSpecs $PyCmd @("graphifyy>=0.8.39,<0.8.52")\n')
REQS = "neo4j>=6.0.1,<7\nmcp>=1.24,<2\nanyio>=4.5   # direct import\ntorch\npyyaml\ngraphifyy>=0.8.39,<0.8.52\n"


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSES if ok else FAILS).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok or not detail else f" — {detail}"))


def _name(n: str) -> str:
    """PEP 503 name normalization (`huggingface_hub` == `huggingface-hub`, `PyYAML` == `pyyaml`)."""
    return re.sub(r"[-_.]+", "-", n.strip()).lower()


def _spec(s: str) -> str:
    """A specifier set's canonical form: whitespace dropped, floors before ceilings (`<2,>=1.2` == `>=1.2, <2`)."""
    parts = [p for p in re.sub(r"\s+", "", s or "").split(",") if p]
    return ",".join(sorted(parts, key=lambda p: (0 if p[:1] in ">=~" else 1 if p[:1] == "<" else 2, p)))


def _split_req(tok: str) -> "tuple[str, str] | None":
    """`mcp>=1.24,<2` -> ('mcp', '<2,>=1.24'); `pyyaml` -> ('pyyaml', ''); a non-requirement token -> None."""
    m = re.match(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*((?:[<>=!~]=?|===)[^;#\s]*(?:\s*,\s*"
                 r"(?:[<>=!~]=?|===)[^;#\s,]*)*)?\s*$", tok)
    if not m:
        return None
    return _name(m.group(1)), _spec(m.group(2) or "")


def constrained_requirements(text: str) -> dict:
    """{normalized name: canonical specifier} for every requirements.txt line WITH a version constraint."""
    out: dict = {}
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        got = _split_req(line.split(";", 1)[0]) if line else None
        if got and got[1]:
            out[got[0]] = got[1]
    return out


def _strip_comment(line: str) -> str:
    """Drop a trailing `#` comment (sh and PowerShell) — never a `#` inside quotes."""
    out, q = [], None
    for ch in line:
        if q:
            out.append(ch)
            q = None if ch == q else q
            continue
        if ch in "\"'":
            q = ch
        elif ch == "#":
            break
        out.append(ch)
    return "".join(out)


def _code(text: str) -> str:
    """The script without comments (sh `#` lines/tails, PowerShell `#` tails and `<# ... #>` blocks)."""
    text = re.sub(r"<#.*?#>", "", text, flags=re.DOTALL)
    return "\n".join(_strip_comment(ln) for ln in text.splitlines())


def install_commands(text: str, kind: str) -> "list[dict]":
    """Every package-installing command in an installer: {'reqs': {name: spec}, 'upgrade': bool, 'via': str}.
    sh: each `pip install` line (tokens after `install`). ps1: each `Install-PipSpecs $PyCmd @(...)` call (a spec
    list pip installs from a temporary requirements file — `upgrade` = the helper's own pip call has --upgrade) and
    each raw `Invoke-Py $PyCmd @("-m", "pip", "install", ...)` array (via 'pip-arg')."""
    code = _code(text)
    cmds: list = []
    if kind == "sh":
        for line in code.splitlines():
            m = re.search(r"\bpip\b[\"'\s]+install\b(.*)", line)
            if not m:
                continue
            tail = m.group(1)
            toks = [a or b for a, b in re.findall(r"\"([^\"]*)\"|'([^']*)'", tail)]
            toks += re.sub(r"\"[^\"]*\"|'[^']*'", " ", tail).split()
            reqs = {}
            for tok in toks:
                if tok.startswith(("-", "$")) or "://" in tok:
                    continue
                got = _split_req(tok)
                if got:
                    reqs[got[0]] = got[1]
            cmds.append({"reqs": reqs, "upgrade": bool(re.search(r"(?<![\w-])(?:--upgrade|-U)(?![\w-])", tail)),
                         "via": "pip"})
        return cmds
    body = re.search(r"^function\s+Install-PipSpecs\b[^{]*\{(.*?)^\}", code, re.MULTILINE | re.DOTALL | re.IGNORECASE)
    helper_upgrade = bool(body and re.search(r"[\"']--upgrade[\"']|[\"']-U[\"']", body.group(1)))
    for m in re.finditer(r"^\s*(Install-PipSpecs|Invoke-Py)\s+\$PyCmd\s+@\((.*?)\)", code, re.MULTILINE | re.DOTALL):
        args = re.findall(r"\"([^\"]*)\"|'([^']*)'", m.group(2))
        vals = [a or b for a, b in args]
        if m.group(1).lower() == "invoke-py":
            if not ("pip" in vals and "install" in vals):
                continue
            specs = vals[vals.index("install") + 1:]
            upgrade = "--upgrade" in specs or "-U" in specs
            via = "pip-arg"
        else:
            specs, upgrade, via = vals, helper_upgrade, "Install-PipSpecs"
        reqs = {}
        for tok in specs:
            if tok.startswith("-") or "://" in tok:
                continue
            got = _split_req(tok)
            if got:
                reqs[got[0]] = got[1]
        cmds.append({"reqs": reqs, "upgrade": upgrade, "via": via})
    return cmds


def spec_problems(requirements: str, text: str, kind: str, via_map: dict) -> "list[str]":
    """Pure: what in one installer contradicts requirements.txt's constrained requirements ([] = consistent)."""
    probs: list = []
    cmds = install_commands(text, kind)
    for pkg, spec in sorted(constrained_requirements(requirements).items()):
        mine = [c for c in cmds if pkg in c["reqs"]]
        if not mine:
            probs.append(f"{pkg}: not installed (requirements.txt declares {pkg}{spec})")
            continue
        for c in mine:
            if c["reqs"][pkg] != spec:
                probs.append(f"{pkg}: installed as {pkg}{c['reqs'][pkg] or ' (bare)'}, requirements.txt says {pkg}{spec}")
            if not c["upgrade"]:
                probs.append(f"{pkg}: installed without --upgrade (an older copy would stay 'already satisfied')")
            if kind == "ps1" and c["via"] != "Install-PipSpecs":
                probs.append(f"{pkg}: its specifier is a pip command-line argument, not an Install-PipSpecs entry")
        parent = (via_map.get(pkg) or (None,))[0]
        if parent and not any(_name(parent) in c["reqs"] for c in mine):
            probs.append(f"{pkg}: not in the same install command as {parent}, the pinned package it arrives through")
    return probs


def direct_via_pinned(root: Path) -> "tuple[dict, str]":
    """tests/compliance_gates.py's DIRECT_VIA_PINNED (the tree's own, else this repo's), else the built-in default."""
    for base in (root, REPO):
        path = base / "tests" / "compliance_gates.py"
        try:
            for node in ast.parse(path.read_text(encoding="utf-8")).body:
                if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "DIRECT_VIA_PINNED"
                                                        for t in node.targets):
                    return ast.literal_eval(node.value), str(path)
        except (OSError, SyntaxError, ValueError):
            continue
    return dict(DEFAULT_VIA), "built-in default"


def gate_real(root: Path) -> None:
    req = (root / "requirements.txt").read_text(encoding="utf-8") if (root / "requirements.txt").is_file() else ""
    via_map, src = direct_via_pinned(root)
    print("\n-- A: requirements.txt's constrained requirements --")
    cons = constrained_requirements(req)
    check("requirements.txt declares the constrained set this gate enforces (neo4j, mcp, graphifyy, anyio)",
          {"neo4j", "mcp", "graphifyy", "anyio"} <= set(cons), str(cons))
    check(f"DIRECT_VIA_PINNED read ({src}) maps anyio to mcp", (via_map.get("anyio") or (None,))[0] == "mcp",
          str(via_map))
    for rel, kind in (("install.sh", "sh"), ("install.ps1", "ps1")):
        print(f"\n-- B: {rel} installs each constrained requirement with its declared specifier --")
        path = root / rel
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        check(f"{rel} exists and has install commands", bool(install_commands(text, kind)))
        probs = spec_problems(req, text, kind, via_map)
        for pkg, spec in sorted(cons.items()):
            mine = [p for p in probs if p.startswith(pkg + ":")]
            label = f"{rel}: {pkg}{spec} installed as declared, with --upgrade"
            if pkg in via_map:
                label += f", in {via_map[pkg][0]}'s own install command"
            check(label, not mine, "; ".join(mine))


def gate_teeth() -> None:
    print("\n-- C: the detector has teeth --")
    via = dict(DEFAULT_VIA)
    got = spec_problems(REQS, PRE_FIX_SH, "sh", via)
    check("pre-fix install.sh (anyio never named) -> flagged 'not installed', nothing else",
          got == ["anyio: not installed (requirements.txt declares anyio>=4.5)"], str(got))
    got = spec_problems(REQS, PRE_FIX_PS1, "ps1", via)
    check("pre-fix install.ps1 -> flagged 'not installed', nothing else",
          got == ["anyio: not installed (requirements.txt declares anyio>=4.5)"], str(got))
    fixed_sh = PRE_FIX_SH.replace('"mcp>=1.24,<2" pyyaml', '"mcp>=1.24,<2" "anyio>=4.5" pyyaml')
    check("the fixed install.sh line -> consistent", spec_problems(REQS, fixed_sh, "sh", via) == [],
          str(spec_problems(REQS, fixed_sh, "sh", via)))
    fixed_ps1 = PRE_FIX_PS1.replace('"mcp>=1.24,<2", "pyyaml"', '"mcp>=1.24,<2", "anyio>=4.5", "pyyaml"')
    check("the fixed install.ps1 spec list -> consistent", spec_problems(REQS, fixed_ps1, "ps1", via) == [],
          str(spec_problems(REQS, fixed_ps1, "ps1", via)))
    reordered = fixed_sh.replace('"mcp>=1.24,<2"', '"mcp<2, >=1.24"')
    check("clause order / spacing is not a difference (mcp<2, >=1.24 == mcp>=1.24,<2)",
          spec_problems(REQS, reordered, "sh", via) == [], str(spec_problems(REQS, reordered, "sh", via)))
    separate = PRE_FIX_SH + '  py -m pip install --upgrade "anyio>=4.5"\n'
    check("anyio in its own command (resolved apart from mcp's dependencies) -> flagged",
          any("same install command as mcp" in p for p in spec_problems(REQS, separate, "sh", via)))
    check("a divergent floor (anyio>=4.0) -> flagged",
          any("installed as anyio>=4.0" in p for p in
              spec_problems(REQS, fixed_sh.replace("anyio>=4.5", "anyio>=4.0"), "sh", via)))
    check("no --upgrade on the command -> flagged",
          any("without --upgrade" in p for p in
              spec_problems(REQS, fixed_sh.replace("install --upgrade \"neo4j", "install \"neo4j"), "sh", via)))
    helper_no_upgrade = fixed_ps1.replace('"install", "--upgrade", "-r"', '"install", "-r"')
    check("an Install-PipSpecs helper without --upgrade -> flagged",
          any("without --upgrade" in p for p in spec_problems(REQS, helper_no_upgrade, "ps1", via)))
    raw_arg = PRE_FIX_PS1 + '  Invoke-Py $PyCmd @("-m", "pip", "install", "--upgrade", "anyio>=4.5", "mcp>=1.24,<2")\n'
    check("install.ps1 handing anyio>=4.5 to pip as a raw argument (a cmd.exe redirection) -> flagged",
          any("not an Install-PipSpecs entry" in p for p in spec_problems(REQS, raw_arg, "ps1", via)))
    comment_only = PRE_FIX_SH + '  # py -m pip install --upgrade "mcp>=1.24,<2" "anyio>=4.5"\n'
    check("a comment that names anyio does not count as installing it",
          any(p.startswith("anyio: not installed") for p in spec_problems(REQS, comment_only, "sh", via)))
    check("unconstrained names (torch, pyyaml) are out of scope; a bare constrained one is flagged",
          constrained_requirements(REQS) == {"neo4j": ">=6.0.1,<7", "mcp": ">=1.24,<2", "anyio": ">=4.5",
                                             "graphifyy": ">=0.8.39,<0.8.52"}
          and any("(bare)" in p for p in spec_problems(REQS, fixed_sh.replace('"anyio>=4.5"', "anyio"), "sh", via)))


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
    print(f"DeepMap — F6: the installers install every constrained requirement as declared (root: {root})")
    print("=" * 72)
    gate_real(root)
    gate_teeth()
    total = len(PASSES) + len(FAILS)
    print("\n" + "=" * 72)
    if FAILS:
        print(f"RESULT: RED — {len(PASSES)}/{total} gates passed.")
        return 1
    print(f"RESULT: GREEN — {len(PASSES)}/{total} gates passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
