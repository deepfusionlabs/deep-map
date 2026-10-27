#!/usr/bin/env python3
"""0.28.3 (F6) — .github/dependabot.yml's pip comment describes DeepMap's real pins (requirements.txt).

Finding (round-1 O12, routed): the comment above the pip update entry said "DeepMap pins loosely (unpinned names)".
Since ROUND-3 E3 / ROUND-4 F3, neo4j / mcp / graphifyy carry VALIDATED two-sided ranges (both ends measured) that
requirements.txt, both installers, CI and the compliance artifacts repeat, and anyio carries a floor (mcp's own) —
so a Dependabot PR that moves one of them in requirements.txt alone is RED in CI (tests/compliance_gates.py), which is
exactly what a maintainer triaging those PRs needs to know.

Locks (reads the committed text; no network, no GitHub):
  A  the comment's pinning claims follow requirements.txt: no "pins loosely / unpinned names" claim while some
     requirement carries a version constraint; every RANGED requirement is named and called a range, every
     FLOORED one is named and called a floor; the drift lock it cites exists in the tree and covers every install
     source (PIN_SOURCES) and every floored package (DIRECT_VIA_PINNED);
  B  the file is still valid YAML with the pip and github-actions entries (the fix is a comment);
  C  teeth: the pre-fix comment is flagged, a comment that drops a ranged package or the word "floor" is flagged,
     and the pre-fix comment is NOT flagged against a requirements.txt that really pins nothing (grounded in the
     file it describes, not a phrase ban).
Usage: python tests/f6_dependabot_comment_r283_gates.py [--root <tree>]
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PASSES: list[str] = []
FAILS: list[str] = []
PIN_SOURCES_WANT = {"requirements.txt", "install.sh", "install.ps1", ".github/workflows/validate.yml"}

PRE_FIX = ("version: 2\nupdates:\n"
           "  # Python runtime dependencies (requirements.txt). DeepMap pins loosely (unpinned names); Dependabot\n"
           "  # surfaces upstream security + version updates as PRs. Keep the batch small and reviewable.\n"
           "  - package-ecosystem: pip\n    directory: \"/\"\n")
REQS = "neo4j>=6.0.1,<7\nmcp>=1.24,<2\nanyio>=4.5\ntorch\ngraphifyy>=0.8.39,<0.8.52\npyyaml\n"
LOOSE = re.compile(r"\bpins?\s+loosely\b|\bloosely\s+pinned\b|\(unpinned names\)")


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSES if ok else FAILS).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok or not detail else f" — {detail}"))


def _read(root: Path, rel: str) -> str:
    p = root / rel
    return p.read_text(encoding="utf-8") if p.is_file() else ""


def pin_classes(requirements: str) -> "tuple[list, list, list]":
    """(ranged, floored, unpinned) package names of a requirements file: a floor AND a ceiling / a floor only / bare."""
    ranged, floored, bare = [], [], []
    for raw in requirements.splitlines():
        line = raw.split("#", 1)[0].split(";", 1)[0].strip()
        m = re.match(r"([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*(.*)$", line)
        if not m:
            continue
        ops = re.findall(r"(===|==|>=|<=|!=|~=|>|<)", m.group(2))
        floor = any(o in (">=", ">", "==", "===", "~=") for o in ops)
        ceiling = any(o in ("<", "<=", "==", "===", "~=") for o in ops)
        (ranged if floor and ceiling else floored if floor else bare).append(m.group(1).lower())
    return ranged, floored, bare


def pip_comment(yml: str) -> str:
    """The contiguous comment lines directly above the `- package-ecosystem: pip` entry, `#` stripped, joined."""
    lines = yml.splitlines()
    for i, ln in enumerate(lines):
        if re.match(r"^\s*-\s*package-ecosystem:\s*[\"']?pip[\"']?\s*$", ln):
            block = []
            j = i - 1
            while j >= 0 and lines[j].strip().startswith("#"):
                block.insert(0, lines[j].strip().lstrip("#").strip())
                j -= 1
            return " ".join(block)
    return ""


def comment_problems(comment: str, requirements: str) -> "list[str]":
    """Pure: what in the pip comment contradicts requirements.txt ([] = consistent)."""
    if not comment:
        return ["no comment above the pip update entry"]
    text = re.sub(r"\s+", " ", comment).lower()
    ranged, floored, _ = pin_classes(requirements)
    probs = []
    if (ranged or floored) and LOOSE.search(text):
        probs.append(f"calls DeepMap's pins loose / unpinned ({LOOSE.search(text).group(0)!r}), but requirements.txt "
                     f"constrains {', '.join(ranged + floored)}")
    for pkg in ranged + floored:
        if not re.search(rf"(?<![\w-]){re.escape(pkg)}(?![\w-])", text):
            probs.append(f"does not name {pkg} (requirements.txt: {'a range' if pkg in ranged else 'a floor'})")
    if ranged and not re.search(r"\branges?\b", text):
        probs.append("does not say the ranged packages carry a range")
    if floored and not re.search(r"\bfloors?\b", text):
        probs.append("does not say the floored packages carry a floor")
    return probs


def _literal(path: Path, name: str):
    try:
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == name for t in node.targets):
                return ast.literal_eval(node.value)
    except (OSError, SyntaxError, ValueError):
        return None
    return None


def gate_a(root: Path) -> None:
    print("\n-- A: the pip comment's pinning claims follow requirements.txt --")
    yml, req = _read(root, ".github/dependabot.yml"), _read(root, "requirements.txt")
    ranged, floored, bare = pin_classes(req)
    check("requirements.txt parses to ranged + floored + unpinned names (not vacuous)",
          bool(ranged) and bool(floored) and bool(bare), f"ranged={ranged} floored={floored} bare={bare}")
    comment = pip_comment(yml)
    check("dependabot.yml has a comment directly above its pip entry", bool(comment))
    probs = comment_problems(comment, req)
    check("it does not call the pins loose / 'unpinned names' while requirements.txt constrains packages",
          not any(p.startswith("calls DeepMap") for p in probs), "; ".join(p for p in probs if p.startswith("calls")))
    for pkg in ranged + floored:
        check(f"it names {pkg} ({'a validated range' if pkg in ranged else 'a floor'} in requirements.txt)",
              not any(p.startswith(f"does not name {pkg} ") for p in probs))
    check("it says the ranged packages carry a RANGE and the floored ones a FLOOR",
          not any(p.startswith("does not say") for p in probs), "; ".join(p for p in probs if p.startswith("does not say")))
    cited = sorted(set(re.findall(r"tests/[A-Za-z0-9_]+\.py", comment)))
    check("it names the drift lock (a tests/*.py file)", bool(cited), comment[:200])
    for rel in cited:
        path = root / rel if (root / rel).is_file() else REPO / rel
        check(f"the cited {rel} exists", path.is_file(), str(path))
        if rel.endswith("compliance_gates.py") and path.is_file():
            sources = set(_literal(path, "PIN_SOURCES") or ())
            check(f"{rel} PIN_SOURCES covers requirements.txt, both installers and CI",
                  PIN_SOURCES_WANT <= sources, str(sorted(sources)))
            via = _literal(path, "DIRECT_VIA_PINNED") or {}
            check(f"{rel} DIRECT_VIA_PINNED covers every floored package ({', '.join(floored)})",
                  set(floored) <= set(via), str(sorted(via)))


def gate_b(root: Path) -> None:
    print("\n-- B: still valid YAML with its pip + github-actions entries (the fix is a comment) --")
    try:
        import yaml  # type: ignore
    except ImportError:
        print("  [skip] PyYAML absent — the structural parse is skipped")
        return
    try:
        dep = yaml.safe_load(_read(root, ".github/dependabot.yml")) or {}
    except Exception as exc:  # noqa: BLE001
        check(".github/dependabot.yml is valid YAML", False, str(exc))
        return
    check(".github/dependabot.yml is valid YAML", True)
    ups = {u.get("package-ecosystem"): u for u in dep.get("updates", []) if isinstance(u, dict)}
    check("the pip and github-actions update entries are both present", {"pip", "github-actions"} <= set(ups),
          str(sorted(ups)))
    ml = (((ups.get("pip") or {}).get("groups") or {}).get("ml-stack") or {}).get("patterns") or []
    check("the pip entry keeps its ml-stack group", {"torch", "transformers", "sentence-transformers"} <= set(ml),
          str(ml))


def gate_c() -> None:
    print("\n-- C: the comment checker has teeth --")
    got = comment_problems(pip_comment(PRE_FIX), REQS)
    check("the pre-fix comment is flagged: 'pins loosely' + none of neo4j / mcp / graphifyy / anyio named",
          any(p.startswith("calls DeepMap") for p in got)
          and all(any(p.startswith(f"does not name {k} ") for p in got) for k in ("neo4j", "mcp", "graphifyy", "anyio")),
          "; ".join(got))
    good = ("Python runtime dependencies (requirements.txt). neo4j / mcp / graphifyy are pinned to VALIDATED ranges "
            "and anyio to a floor (mcp's own); the other names are unpinned.")
    check("a comment naming each constrained package, the ranges and the floor passes", comment_problems(good, REQS) == [],
          str(comment_problems(good, REQS)))
    check("a comment that drops graphifyy is flagged",
          comment_problems(good.replace("/ graphifyy ", ""), REQS) == ["does not name graphifyy (requirements.txt: a range)"],
          str(comment_problems(good.replace("/ graphifyy ", ""), REQS)))
    check("a comment that never says 'floor' is flagged",
          "does not say the floored packages carry a floor" in comment_problems(good.replace("to a floor", "too"), REQS))
    check("grounded: the pre-fix comment is NOT flagged against a requirements.txt that pins nothing",
          comment_problems(pip_comment(PRE_FIX), "torch\npyyaml\nneo4j\n") == [])
    check("pin_classes: two-sided -> ranged, >= only -> floored, bare -> unpinned",
          pin_classes(REQS) == (["neo4j", "mcp", "graphifyy"], ["anyio"], ["torch", "pyyaml"]), str(pin_classes(REQS)))
    check("the comment is taken only from directly above the pip entry",
          pip_comment("updates:\n  # other\n\n  # mine\n  - package-ecosystem: pip\n") == "mine"
          and pip_comment("updates:\n  - package-ecosystem: npm\n") == "")


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
    print(f"DeepMap — F6: dependabot.yml's pip comment vs requirements.txt's real pins (root: {root})")
    print("=" * 72)
    gate_a(root)
    gate_b(root)
    gate_c()
    total = len(PASSES) + len(FAILS)
    print("\n" + "=" * 72)
    if FAILS:
        print(f"RESULT: RED — {len(PASSES)}/{total} gates passed.")
        return 1
    print(f"RESULT: GREEN — {len(PASSES)}/{total} gates passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
