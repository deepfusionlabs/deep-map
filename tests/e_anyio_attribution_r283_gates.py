#!/usr/bin/env python3
"""0.28.3 (round-3 E) — the compliance artifacts say how anyio is really installed: both installers name it, CI not.

Finding (round-3 completeness critics cov-build[2] / cov-bench[0] / docs[1] / safety[0]): round 2 (F6) made both
installers install `anyio>=4.5` by name, in mcp's own pip command (install.sh's pip line, install.ps1's Install-PipSpecs
list), but the sentences round 1 wrote still said the installers get anyio through mcp and do not name it: the
requirements.txt comment, NOTICE, THIRD-PARTY-LICENSES.md (its row, and "arrives with mcp" in the Distribution cell),
sbom.cdx.json (the status property and anyio's deepmap:distribution) and the section-14 comment of
tests/compliance_gates.py. The license artifacts contradicted the installers and the CHANGELOG, and nothing read those
sentences: compliance_gates section 14 checks only anyio's range and license.

Locks (text only; an installer's comments never count; no pip, no network):
  A  the ground truth, read from code lines: which install commands of install.sh / install.ps1 name each package that
     tests/compliance_gates.py lists in DIRECT_VIA_PINNED (anyio -> mcp), whether that is the pinned package's own
     command, and whether CI's pip lines (.github/workflows/validate.yml) name it;
  B  every place the artifacts describe anyio's installation — the comment block above its requirements.txt line and
     that line's own comment, the NOTICE bullet, the THIRD-PARTY-LICENSES.md row, the SBOM component, the segment of
     the SBOM status property that names it, the section-14 comment — makes no claim the code contradicts: a clause
     about the installers says they name it iff both do ("next to mcp" / "in mcp's own command" only if both share
     mcp's command); a clause about CI says it gets it through mcp iff CI does not name it; a distribution field (the
     Distribution cell, the requirement line's comment, the first sentence of the SBOM's deepmap:distribution) does
     not say it merely arrives with mcp while the installers name it; each of the four artifacts says how the
     installers get it; the SBOM stays valid JSON;
  C  teeth, on fixed installer text shaped like F6's (so they never depend on the tree's installer state): the pre-fix
     sentences are flagged, clauses are split per subject, and every direction of drift is caught (an installer that
     stops naming anyio, names it apart from mcp or only in a comment; CI naming it directly or through
     -r requirements.txt; a field that says "arrives with mcp"; an artifact that says nothing); the tree's real
     installers reduced to their comments name nothing.
Usage: python tests/e_anyio_attribution_r283_gates.py [--root <tree>]
"""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PASSES: list[str] = []
FAILS: list[str] = []
DEFAULT_VIA = {"anyio": ("mcp", "MIT")}   # used only when no tests/compliance_gates.py DIRECT_VIA_PINNED is readable
ARTIFACTS = ("requirements.txt", "NOTICE", "THIRD-PARTY-LICENSES.md", "sbom.cdx.json")   # must SAY how installers get it
INSTALLERS = (("install.sh", "sh"), ("install.ps1", "ps1"))
CI_WORKFLOW = ".github/workflows/validate.yml"
GATE_COMMENT = "tests/compliance_gates.py"

# The round-1 sentences F6 left behind (verbatim, one per artifact place) — section C proves each is flagged.
PRE_FIX = {
    "requirements.txt": "no known breaking release. The installers and CI get it through mcp and do not name it.",
    "NOTICE": "(build/deepmap_search_server.py). The installers get it through mcp.",
    "THIRD-PARTY-LICENSES.md": "no ceiling (no known breaking release). The installers get it through mcp.",
    "sbom.cdx.json status": "anyio carries the floor requirements.txt declares for it (mcp's own; it arrives with mcp, "
                            "so the installers and CI do not name it)",
    "sbom.cdx.json component": "bundled (pip); arrives with mcp — the installers and CI get it through mcp and do not "
                               "name it.",
    GATE_COMMENT: "anyio has no known breaking release. The installers and CI get it through mcp and do not name it.",
}
PRE_FIX_FIELDS = ("bundled (pip; arrives with mcp)", "MIT — imported directly (see above); arrives with mcp",
                  "bundled (pip); arrives with mcp — the installers and CI get it through mcp and do not name it")

_SUBJ_INSTALLERS = r"\binstallers?\b|\binstall\.(?:sh|ps1)\b"
_SUBJ_CI = r"\bCI\b|\bvalidate\.yml\b"
_POSITIVE = r"\b(?:install|installs|installed|name|names|named|list|lists|listed|pin|pins|pinned)\b"


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSES if ok else FAILS).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok or not detail else f" — {detail}"))


def _name(n: str) -> str:
    """PEP 503 name normalization (`huggingface_hub` == `huggingface-hub`, `PyYAML` == `pyyaml`)."""
    return re.sub(r"[-_.]+", "-", n.strip()).lower()


def _read(root: Path, rel: str) -> str:
    p = root / rel
    return p.read_text(encoding="utf-8") if p.is_file() else ""


# ------------------------------------------------------------------ A: the ground truth, from code lines only
def _strip_comment(line: str) -> str:
    """Drop a trailing `#` comment (sh, PowerShell, YAML) — never a `#` inside quotes."""
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
    """The script without comments (`#` lines/tails, PowerShell `<# ... #>` blocks)."""
    text = re.sub(r"<#.*?#>", "", text, flags=re.DOTALL)
    return "\n".join(_strip_comment(ln) for ln in text.splitlines())


def _req_name(tok: str) -> str:
    """The package a pip argument / spec-list entry names ('anyio>=4.5' -> 'anyio'); '' for an option or variable."""
    tok = tok.strip()
    if not tok or tok.startswith(("-", "$")) or "://" in tok:
        return ""
    m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*(?:[<>=!~]|$)", tok)
    return _name(m.group(1)) if m else ""


def declared_packages(requirements: str) -> set:
    """Every package requirements.txt declares (what a `pip install -r requirements.txt` names)."""
    out = set()
    for raw in requirements.splitlines():
        name = _req_name(raw.split("#", 1)[0].split(";", 1)[0])
        if name:
            out.add(name)
    return out


def _names_requirements_file(args: "list[str]") -> bool:
    """Does a pip argument list read requirements.txt (`-r requirements.txt`, `--requirement=…/requirements.txt`)?"""
    for i, a in enumerate(args):
        val = a.split("=", 1)[1] if a.startswith("--requirement=") else (
            args[i + 1] if a in ("-r", "--requirement") and i + 1 < len(args) else "")
        if re.search(r"(?:^|[/\\])requirements\.txt$", val.strip("\"'")):
            return True
    return False


def install_commands(text: str, kind: str, declared: set) -> "list[set]":
    """The package names each installing command names. sh / yml: each `pip install` line (a `-r requirements.txt`
    names every package requirements.txt declares); ps1: each `Install-PipSpecs $PyCmd @(...)` spec list and each
    raw `Invoke-Py $PyCmd @("-m", "pip", "install", ...)` array. Comments never count."""
    code = _code(text)
    cmds: list = []
    if kind in ("sh", "yml"):
        for line in code.splitlines():
            m = re.search(r"\bpip3?\b[\"'\s]+install\b(.*)", line)
            if not m:
                continue
            tail = m.group(1)
            toks = [a or b for a, b in re.findall(r"\"([^\"]*)\"|'([^']*)'", tail)]
            toks += re.sub(r"\"[^\"]*\"|'[^']*'", " ", tail).split()
            names = {n for n in map(_req_name, toks) if n}
            if _names_requirements_file(tail.split()):
                names |= declared
            cmds.append(names)
        return cmds
    for m in re.finditer(r"^\s*(Install-PipSpecs|Invoke-Py)\s+\$PyCmd\s+@\((.*?)\)", code, re.MULTILINE | re.DOTALL):
        vals = [a or b for a, b in re.findall(r"\"([^\"]*)\"|'([^']*)'", m.group(2))]
        if m.group(1).lower() == "invoke-py":
            if not ("pip" in vals and "install" in vals):
                continue
            vals = vals[vals.index("install") + 1:]
        names = {n for n in map(_req_name, vals) if n}
        if _names_requirements_file(vals):
            names |= declared
        cmds.append(names)
    return cmds


def ground_truth(texts: dict, pkg: str, parent: str, declared: set) -> dict:
    """{'installers': {rel: {'named', 'with_parent', 'commands'}}, 'ci_named': bool, 'ci_commands': int} from
    `texts` = {'install.sh': …, 'install.ps1': …, CI_WORKFLOW: …}."""
    inst = {}
    for rel, kind in INSTALLERS:
        cmds = install_commands(texts.get(rel, ""), kind, declared)
        inst[rel] = {"named": any(pkg in c for c in cmds), "with_parent": any(pkg in c and parent in c for c in cmds),
                     "commands": len(cmds)}
    ci = install_commands(texts.get(CI_WORKFLOW, ""), "yml", declared)
    return {"installers": inst, "ci_named": any(pkg in c for c in ci), "ci_commands": len(ci)}


# ------------------------------------------------------------------ B: the claims the artifacts make
def _flat(text: str) -> str:
    """Prose without markdown code-span / bold markers and with whitespace collapsed."""
    return re.sub(r"\s+", " ", re.sub(r"[`*]", "", text)).strip()


def clauses(text: str) -> "list[str]":
    """Prose split into clauses: at sentence ends, `;`, a spaced dash, `while` / `whereas` / `but`, and at a comma that
    starts a new subject (`…, CI gets it …`, `…, so the installers …`), so each clause has at most one predicate."""
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z(])|;|\s[—–]\s|\s--\s|,?\s+\b(?:while|whereas|but)\b\s+"
                     r"|,\s+(?=(?:(?:so|and)\s+)?(?:(?:the|both)\s+)?(?:installers?\b|CI\b|install\.(?:sh|ps1)\b))",
                     _flat(text))
    return [p.strip(" ,.()") for p in parts if p and p.strip(" ,.()")]


def _negative(pkg: str, parent: str) -> str:
    """A predicate that says the subject does NOT install `pkg` by name (gets it through `parent` instead)."""
    p, v = re.escape(pkg), re.escape(parent)
    return (r"\b(?:do|does|did)\s+not\s+(?:name|install|list|pin)\b"
            r"|\b(?:don't|doesn't|never)\s+(?:names?|installs?|lists?|pins?)\b|\bnot\s+(?:named|installed|listed)\b"
            rf"|\b(?:gets?|receives?|obtains?|pulls?)\s+(?:it|{p})\s+(?:only\s+)?(?:through|via|from|with)\s+{v}\b"
            rf"|\b(?:through|via)\s+{v}\s+only\b")


def _next_to(parent: str) -> str:
    """A predicate that places the install in `parent`'s own command."""
    v = re.escape(parent)
    return (rf"\bnext\s+to\s+{v}\b|\b{v}'s\s+(?:own\s+)?(?:pip\s+)?(?:install\s+)?(?:command|spec\s+list)\b"
            rf"|\bsame\s+(?:pip\s+)?(?:install\s+)?command\b|\bone\s+command\b")


def claims(text: str, pkg: str, parent: str) -> "list[tuple[str, str, str]]":
    """[(subject, verdict, clause)]: subject 'installers' | 'ci'; verdict 'not' (does not name it / gets it through
    `parent`), 'names' or 'names-next-to' (names it, in `parent`'s own command)."""
    out = []
    subjects = f"{_SUBJ_INSTALLERS}|{_SUBJ_CI}"
    for c in clauses(text):
        pred = re.sub(subjects, " ", c)
        if re.search(_negative(pkg, parent), pred, re.IGNORECASE):
            verdict = "not"
        elif re.search(_POSITIVE, pred, re.IGNORECASE):
            verdict = "names-next-to" if re.search(_next_to(parent), pred, re.IGNORECASE) else "names"
        else:
            continue
        if re.search(_SUBJ_INSTALLERS, c, re.IGNORECASE):
            out.append(("installers", verdict, c))
        if re.search(_SUBJ_CI, c):
            out.append(("ci", verdict, c))
    return out


def _s(subjects: list) -> str:
    """The verb ending for a list of installer names ('install.sh names', 'install.sh and install.ps1 name')."""
    return "s" if len(subjects) == 1 else ""


def claim_problems(where: str, text: str, pkg: str, parent: str, truth: dict) -> "list[str]":
    """Pure: each claim in `text` the ground truth contradicts ([] = consistent)."""
    probs = []
    inst = truth["installers"]
    both_named = all(v["named"] for v in inst.values())
    none_named = not any(v["named"] for v in inst.values())
    both_with_parent = all(v["with_parent"] for v in inst.values())
    for subject, verdict, clause in claims(text, pkg, parent):
        if subject == "installers":
            if verdict == "not" and not none_named:
                named = [r for r, v in inst.items() if v["named"]]
                probs.append(f"{where}: says the installers do not name {pkg} / get it through {parent}, but "
                             f"{' and '.join(named)} install{_s(named)} it by name: {clause!r}")
            elif verdict.startswith("names") and not both_named:
                missing = [r for r, v in inst.items() if not v["named"]]
                probs.append(f"{where}: says the installers install {pkg}, but {' and '.join(missing)} never "
                             f"name{_s(missing)} it: {clause!r}")
            elif verdict == "names-next-to" and not both_with_parent:
                apart = [r for r, v in inst.items() if not v["with_parent"]]
                probs.append(f"{where}: says {pkg} is installed in {parent}'s own command, but "
                             f"{' and '.join(apart)} install{_s(apart)} it apart from {parent}: {clause!r}")
        else:
            if verdict == "not" and truth["ci_named"]:
                probs.append(f"{where}: says CI gets {pkg} through {parent}, but {CI_WORKFLOW} names it: {clause!r}")
            elif verdict.startswith("names") and not truth["ci_named"]:
                probs.append(f"{where}: says CI installs {pkg}, but {CI_WORKFLOW} never names it: {clause!r}")
    return probs


def field_problems(where: str, field: str, pkg: str, parent: str, truth: dict) -> "list[str]":
    """Pure: a distribution field that says `pkg` merely arrives with `parent` while both installers name it."""
    if not all(v["named"] for v in truth["installers"].values()):
        return []
    v = re.escape(parent)
    pat = rf"\b(?:arrives?|comes?|ships?)\s+with\s+{v}\b|\b(?:only\s+)?(?:through|via)\s+{v}\b"
    return [f"{where}: its distribution field says {pkg} arrives with {parent}, but both installers install it by name: "
            f"{c!r}" for c in clauses(field) if re.search(pat, c, re.IGNORECASE) and not re.search(_SUBJ_CI, c)]


def _first_sentence(text: str) -> str:
    return re.split(r"(?<=[.!?])\s+(?=[A-Z(])", _flat(text), maxsplit=1)[0]


def _top_segments(text: str) -> "list[str]":
    """`text` split at the `;` outside parentheses."""
    segs, cur, depth = [], [], 0
    for ch in text:
        depth += {"(": 1, ")": -1}.get(ch, 0)
        depth = max(depth, 0)
        if ch == ";" and depth == 0:
            segs.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    segs.append("".join(cur))
    return segs


def _word(pkg: str) -> str:
    return rf"(?<![\w.\-/]){re.escape(pkg)}(?![\w\-])"


def regions(texts: dict, sbom, pkg: str) -> "tuple[dict, dict, list[str]]":
    """({place: text describing pkg's installation}, {place: distribution field}, [places not found])."""
    reg, fields, missing = {}, {}, []
    lines = texts.get("requirements.txt", "").splitlines()
    idx = next((i for i, ln in enumerate(lines) if _req_name(ln.split("#", 1)[0].split(";", 1)[0]) == pkg), None)
    if idx is None:
        missing.append("requirements.txt (no requirement line)")
    else:
        above = []
        j = idx - 1
        while j >= 0 and lines[j].lstrip().startswith("#"):
            above.insert(0, lines[j].lstrip()[1:].strip())
            j -= 1
        inline = lines[idx].split("#", 1)[1].strip() if "#" in lines[idx] else ""
        reg["requirements.txt"] = "\n".join(above) + ";\n" + inline   # `;` = a clause boundary between the two
        fields["requirements.txt"] = inline
    notice = texts.get("NOTICE", "").splitlines()
    start = next((i for i, ln in enumerate(notice) if re.match(rf"^\*\s+{re.escape(pkg)}\b", ln, re.IGNORECASE)), None)
    if start is None:
        missing.append("NOTICE (no bullet)")
    else:
        end = start + 1
        while end < len(notice) and notice[end].strip() and not re.match(r"^(?:\*\s|-{3,})", notice[end]):
            end += 1
        reg["NOTICE"] = "\n".join(notice[start:end])
    row = None
    for ln in texts.get("THIRD-PARTY-LICENSES.md", "").splitlines():
        cells = [c.strip() for c in re.split(r"(?<!\\)\|", ln.strip())[1:-1]] if ln.lstrip().startswith("|") else []
        if cells and _name(re.sub(r"[`*]", "", cells[0])) == pkg:
            row = cells
            break
    if row is None or len(row) < 5:
        missing.append("THIRD-PARTY-LICENSES.md (no table row)")
    else:
        reg["THIRD-PARTY-LICENSES.md"] = ";\n".join(row)   # one clause boundary per cell
        fields["THIRD-PARTY-LICENSES.md"] = row[2]
    comps = [c for c in (sbom.get("components") or []) if isinstance(c, dict)
             and re.fullmatch(rf"pkg:pypi/{re.escape(pkg)}", str(c.get("purl", "")), re.IGNORECASE)]
    if len(comps) != 1:
        missing.append(f"sbom.cdx.json ({len(comps)} components with purl pkg:pypi/{pkg})")
    else:
        props = {str(p.get("name")): str(p.get("value")) for p in (comps[0].get("properties") or [])
                 if isinstance(p, dict)}
        reg["sbom.cdx.json"] = ";\n".join(props.values())   # one clause boundary per property
        fields["sbom.cdx.json"] = _first_sentence(props.get("deepmap:distribution", ""))
    status = [seg for p in ((sbom.get("metadata") or {}).get("properties") or []) if isinstance(p, dict)
              for seg in _top_segments(str(p.get("value", ""))) if re.search(_word(pkg), seg)]
    if status:
        reg["sbom.cdx.json status"] = ";\n".join(status)
    gate = texts.get(GATE_COMMENT, "").splitlines()
    at = next((i for i, ln in enumerate(gate) if re.match(r"^DIRECT_VIA_PINNED\s*=", ln)), None)
    if at is None:
        missing.append(f"{GATE_COMMENT} (no DIRECT_VIA_PINNED)")
    else:
        block, j = [], at - 1
        while j >= 0 and gate[j].startswith("#"):
            body = gate[j][1:].strip()
            if body.strip("-—# "):   # skip the `# ----- #` rules
                block.insert(0, body)
            j -= 1
        reg[GATE_COMMENT] = "\n".join(block)
    return reg, fields, missing


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


def _load(root: Path) -> "tuple[dict, dict | None, str]":
    rels = (*ARTIFACTS, "install.sh", "install.ps1", CI_WORKFLOW, GATE_COMMENT)
    texts = {rel: _read(root, rel) for rel in rels}
    try:
        return texts, json.loads(texts["sbom.cdx.json"] or "{}"), ""
    except json.JSONDecodeError as e:
        return texts, None, str(e)


def gate_real(root: Path) -> None:
    texts, sbom, err = _load(root)
    via_map, src = direct_via_pinned(root)
    declared = declared_packages(texts["requirements.txt"])
    print("\n-- A: the ground truth, read from the installers' and CI's code lines --")
    check(f"DIRECT_VIA_PINNED read ({src}) is non-empty and maps anyio to mcp",
          (via_map.get("anyio") or (None,))[0] == "mcp", str(via_map))
    check("sbom.cdx.json parses as JSON", sbom is not None, err)
    for pkg, (parent, _lic) in sorted(via_map.items()):
        truth = ground_truth(texts, _name(pkg), _name(parent), declared)
        for rel, t in truth["installers"].items():
            check(f"{rel}: has install commands; names {pkg}: {t['named']}, in {parent}'s own command: "
                  f"{t['with_parent']}", t["commands"] > 0, "no install command found")
        check(f"{CI_WORKFLOW}: has pip install lines; names {pkg}: {truth['ci_named']}", truth["ci_commands"] > 0,
              "no pip install line found")
        print(f"\n-- B: every place the artifacts describe how {pkg} is installed agrees with that --")
        reg, fields, missing = regions(texts, sbom or {}, _name(pkg))
        check(f"every artifact place that describes {pkg} is found", not missing, "; ".join(missing))
        for where, text in sorted(reg.items()):
            probs = claim_problems(where, text, _name(pkg), _name(parent), truth)
            check(f"{where}: its claims about the installers / CI match the code", not probs, "; ".join(probs))
        for where, field in sorted(fields.items()):
            probs = field_problems(where, field, _name(pkg), _name(parent), truth)
            check(f"{where}: its distribution field does not say {pkg} merely arrives with {parent}", not probs,
                  "; ".join(probs))
        for where in ARTIFACTS:
            said = [v for s, v, _ in claims(reg.get(where, ""), _name(pkg), _name(parent)) if s == "installers"]
            check(f"{where}: says how the installers get {pkg}", bool(said), "no clause about the installers")


# ------------------------------------------------------------------ C: teeth
# Installers shaped like F6's real lines (anyio in mcp's own pip command / spec list, and named in a comment too) and
# CI's (anyio only in a comment). Fixed text, so the teeth do not depend on the tree's current installer state.
SYN_REQS = "neo4j>=6.0.1,<7\nmcp>=1.24,<2\nanyio>=4.5   # direct import\npyyaml\ngraphifyy>=0.8.39,<0.8.52\n"
SYN = {
    "install.sh": ("  # anyio: imported directly, so it is installed with requirements.txt's floor, in mcp's command\n"
                   '  py -m pip install --upgrade "neo4j>=6.0.1,<7" "mcp>=1.24,<2" "anyio>=4.5" pyyaml\n'
                   '  py -m pip install --upgrade "graphifyy>=0.8.39,<0.8.52"\n'),
    "install.ps1": ("function Install-PipSpecs([string]$PyCmd, [string[]]$Specs) {\n"
                    '    Invoke-Py $PyCmd @("-m", "pip", "install", "--upgrade", "-r", $req)\n}\n'
                    "  # anyio: imported directly, so it rides in mcp's spec list\n"
                    '  Install-PipSpecs $PyCmd @("neo4j>=6.0.1,<7", "mcp>=1.24,<2", "anyio>=4.5", "pyyaml")\n'),
    CI_WORKFLOW: ("      # `mcp` also brings `anyio`, so it needs no entry here\n"
                  '        run: python -m pip install --upgrade pip pyyaml "neo4j>=6.0.1,<7" "mcp>=1.24,<2"\n'),
}


def _without_code_mentions(text: str, pkg: str) -> str:
    """`text` with every quoted `pkg` requirement removed from its CODE (a trailing / whole-line comment keeps its)."""
    out = []
    for ln in text.splitlines():
        code = _strip_comment(ln)
        out.append(re.sub(rf'\s*"{re.escape(pkg)}(?:[<>=!~][^"]*)?",?', "", code) + ln[len(code):])
    return "\n".join(out) + "\n"


def gate_teeth(root: Path) -> None:
    print("\n-- C: the detector has teeth --")
    pkg, parent = "anyio", "mcp"
    declared = declared_packages(SYN_REQS)
    named = ground_truth(SYN, pkg, parent, declared)
    check("installers shaped like F6's (anyio in mcp's pip command, and in a comment) -> both name it in mcp's "
          "command; CI (anyio only in a comment) does not",
          all(v["named"] and v["with_parent"] for v in named["installers"].values()) and not named["ci_named"],
          str(named))
    for where, old in sorted(PRE_FIX.items()):
        got = claim_problems(where, old, pkg, parent, named)
        check(f"pre-fix {where} sentence -> flagged (says the installers do not name anyio)",
              any("do not name anyio" in p for p in got), str(got))
    for old in PRE_FIX_FIELDS:
        check(f"pre-fix distribution field {old[:40]!r}… -> flagged",
              bool(field_problems("field", old, pkg, parent, named)))
    new = "Both installers install it next to mcp with the same floor; CI gets it through mcp."
    check("the fixed sentence -> consistent", claim_problems("x", new, pkg, parent, named) == [],
          str(claim_problems("x", new, pkg, parent, named)))
    check("'installed next to mcp' in a distribution field -> consistent",
          field_problems("f", "bundled (pip; installed next to mcp)", pkg, parent, named) == [])
    check("a CI clause inside a distribution field is not an installer claim",
          field_problems("f", "bundled (pip); both installers install it next to mcp; CI gets it through mcp", pkg,
                         parent, named) == [])
    split = claims("(mcp's own; both installers install it next to mcp with that floor, CI gets it through mcp)",
                   pkg, parent)
    check("one clause per subject: 'both installers install it …, CI gets it through mcp' -> names / not",
          [(s, v) for s, v, _ in split] == [("installers", "names-next-to"), ("ci", "not")], str(split))
    joint = claims("it arrives with mcp, so the installers and CI do not name it", pkg, parent)
    check("a shared predicate ('the installers and CI do not name it') binds both subjects",
          [(s, v) for s, v, _ in joint] == [("installers", "not"), ("ci", "not")], str(joint))
    check("a clause with no installer / CI subject makes no claim ('It arrives with mcp, and DeepMap imports it')",
          claims("It arrives with mcp, and DeepMap also imports it directly.", pkg, parent) == [])
    check("an artifact place that says nothing about the installers makes no installer claim (presence is enforced)",
          not [c for c in claims("MIT; imported directly by build/mcp_bounds.py.", pkg, parent) if c[0] == "installers"])

    sh_off = _without_code_mentions(SYN["install.sh"], pkg)
    ps1_off = _without_code_mentions(SYN["install.ps1"], pkg)
    dropped = ground_truth({**SYN, "install.sh": sh_off}, pkg, parent, declared)
    check("install.sh with anyio removed from its pip line (its comment still names it) -> not named",
          not dropped["installers"]["install.sh"]["named"] and "anyio" in sh_off, str(dropped["installers"]))
    got = claim_problems("x", new, pkg, parent, dropped)
    check("…and the fixed sentence is then flagged (installers stop naming anyio -> the artifacts must follow)",
          any("install.sh never names it" in p for p in got), str(got))
    check("…and the pre-fix sentence is still flagged while install.ps1 names it (only one installer drifted)",
          bool(claim_problems("x", PRE_FIX["NOTICE"], pkg, parent, dropped)))
    none = ground_truth({**SYN, "install.sh": sh_off, "install.ps1": ps1_off}, pkg, parent, declared)
    check("neither installer names anyio (the pre-F6 state) -> the pre-fix sentence is consistent (the rule is "
          "symmetric)", claim_problems("x", PRE_FIX["NOTICE"], pkg, parent, none) == [],
          str(claim_problems("x", PRE_FIX["NOTICE"], pkg, parent, none)))
    apart = ground_truth({**SYN, "install.sh": sh_off + '  py -m pip install --upgrade "anyio>=4.5"\n'},
                         pkg, parent, declared)
    check("install.sh naming anyio in a separate command -> 'next to mcp' is flagged",
          any("apart from mcp" in p for p in claim_problems("x", new, pkg, parent, apart)))
    ci_named = ground_truth({**SYN, CI_WORKFLOW: SYN[CI_WORKFLOW] + '        run: python -m pip install "anyio>=4.5"\n'},
                            pkg, parent, declared)
    check("CI naming anyio -> 'CI gets it through mcp' is flagged",
          any("names it" in p and "CI" in p for p in claim_problems("x", new, pkg, parent, ci_named)))
    ci_req = ground_truth({**SYN, CI_WORKFLOW: '        run: python -m pip install --upgrade -r requirements.txt\n'},
                          pkg, parent, declared)
    check("CI running `pip install -r requirements.txt` names every declared package, anyio included",
          ci_req["ci_named"], str(ci_req))
    ps1_arg = ground_truth({**SYN, "install.ps1": ps1_off + '  Invoke-Py $PyCmd @("-m", "pip", "install", "--upgrade", '
                                                            '"anyio>=4.5", "mcp>=1.24,<2")\n'}, pkg, parent, declared)
    check("a raw Invoke-Py pip array that names anyio with mcp counts as naming it in mcp's command",
          ps1_arg["installers"]["install.ps1"]["named"] and ps1_arg["installers"]["install.ps1"]["with_parent"],
          str(ps1_arg["installers"]["install.ps1"]))
    texts, _sbom, _err = _load(root)
    comments = {rel: "\n".join(ln for ln in texts[rel].splitlines() if not _strip_comment(ln).strip())
                for rel, _kind in INSTALLERS}
    real = ground_truth({**comments, CI_WORKFLOW: ""}, pkg, parent, declared_packages(texts["requirements.txt"]))
    check("the tree's real installers reduced to their comments name nothing (a comment that mentions anyio — "
          f"{sum(c.count(pkg) for c in comments.values())} here — never counts)",
          not any(v["named"] or v["commands"] for v in real["installers"].values()), str(real["installers"]))


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
    print(f"DeepMap — round-3 E: the compliance artifacts say how anyio is installed (root: {root})")
    print("=" * 72)
    gate_real(root)
    gate_teeth(root)
    total = len(PASSES) + len(FAILS)
    print("\n" + "=" * 72)
    if FAILS:
        print(f"RESULT: RED — {len(PASSES)}/{total} gates passed.")
        return 1
    print(f"RESULT: GREEN — {len(PASSES)}/{total} gates passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
