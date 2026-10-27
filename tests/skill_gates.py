#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j gates for the C11 reasoning skill (skills/deep-map/SKILL.md) + the /deep-map commands.

Locks the plugin's reasoning surface: the skill exists, has valid frontmatter (name +
directive description), routes to the live MCP tool, teaches the load-bearing reliability facts
(score-not-comparable, don't-fabricate-on-empty), and carries no cut marketing phrases. Also
checks every commands/*.md has frontmatter. Pure: reads files only.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "deep-map" / "SKILL.md"
COMMANDS = ROOT / "commands"

# The same cut phrases tests/copy_honesty_scan.py forbids on public copy (honesty).
CUT_PHRASES = ("self-improving", "logs every", "auto-optimi", "runtime learning")

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


def _frontmatter(text: str) -> dict:
    """Parse the leading YAML-ish frontmatter into a flat {key: value} (values may span lines)."""
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    if not m:
        return {}
    fm, body, key = {}, m.group(1), None
    for line in body.splitlines():
        km = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
        if km:
            key = km.group(1)
            fm[key] = km.group(2).strip()
        elif key and line.strip():           # continuation (folded scalar)
            fm[key] = (fm[key] + " " + line.strip()).strip()
    return fm


def gate_skill() -> None:
    print("\n-- C11 reasoning skill --")
    check("skills/deep-map/SKILL.md exists", SKILL.exists())
    if not SKILL.exists():
        return
    text = SKILL.read_text(encoding="utf-8")
    fm = _frontmatter(text)
    check("has frontmatter name == 'deep-map'", fm.get("name") == "deep-map", f"got {fm.get('name')!r}")
    desc = fm.get("description", "")
    check("has a non-trivial description (>=80 chars)", len(desc) >= 80, f"len={len(desc)}")
    check("description is directive (mentions deepmap_search)", "deepmap_search" in desc)
    low = text.lower()
    check("body documents the deepmap_search tool", "deepmap_search(" in text)
    check("teaches score-not-comparable", "not comparable" in low or "not comparable across" in low)
    check("teaches don't-fabricate on empty/down", "do not invent" in low or "do not fabricate" in low or "not invent a file" in low)
    check("points at /deep-map setup for an empty graph", "/deep-map" in text)
    for p in CUT_PHRASES:
        check(f"no cut phrase {p!r}", p not in low, "honesty violation in SKILL.md")


def gate_commands() -> None:
    print("\n-- /deep-map commands frontmatter --")
    if not COMMANDS.exists():
        check("commands/ dir exists", False)
        return
    mds = sorted(COMMANDS.glob("*.md"))
    check("commands/ has >= 4 commands", len(mds) >= 4, f"got {len(mds)}")
    for md in mds:
        fm = _frontmatter(md.read_text(encoding="utf-8"))
        check(f"{md.name}: has a description", bool(fm.get("description")))


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 64)
    print("DeepMap — reasoning skill + commands gates (no Neo4j)")
    print("=" * 64)
    gate_skill()
    gate_commands()
    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
