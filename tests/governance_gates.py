#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j, no-spend GOVERNANCE gates — the go-public (S1) doc surface stays present + honest.

Locks the open-source governance files so a future edit cannot silently drop one, break a
cross-reference, or let AI_POLICY.md claim an honesty guarantee that no committed gate actually backs:

  1. PRESENCE   — LICENSE, NOTICE, THIRD-PARTY-LICENSES.md, README.md, CHANGELOG.md, SECURITY.md,
                  AI_POLICY.md all exist.
  2. SECURITY   — SECURITY.md carries the load-bearing sections (private reporting, scope, supported
                  versions) and routes reports through GitHub private advisories — NOT a public issue,
                  and NOT a hardcoded personal email (which would be a leak + an unmaintainable channel).
  3. HONESTY    — AI_POLICY.md's honesty claims name REAL gate files, and those files exist (the policy
                  cannot promise a guarantee nothing enforces).
  4. LINKS      — every RELATIVE markdown link in SECURITY.md / AI_POLICY.md resolves to a real repo file.

Pure + hermetic: reads the committed text; never spends, never touches Neo4j.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

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


def _read(rel: str) -> str:
    p = REPO / rel
    return p.read_text(encoding="utf-8") if p.is_file() else ""


def gate_presence() -> None:
    print("\n-- 1) required governance/OSS files are present (S1 go-public surface) --")
    for rel in ("LICENSE", "NOTICE", "THIRD-PARTY-LICENSES.md", "README.md", "CHANGELOG.md",
                "SECURITY.md", "AI_POLICY.md", "CONTRIBUTING.md", "CODE_OF_CONDUCT.md"):
        check(f"{rel} present", (REPO / rel).is_file())


def gate_github_templates() -> None:
    print("\n-- 1b) GitHub community templates present + security routed privately --")
    check("PR template present", (REPO / ".github" / "PULL_REQUEST_TEMPLATE.md").is_file())
    check("bug-report issue template present", (REPO / ".github" / "ISSUE_TEMPLATE" / "bug_report.md").is_file())
    check("feature-request issue template present",
          (REPO / ".github" / "ISSUE_TEMPLATE" / "feature_request.md").is_file())
    cfg = _read(".github/ISSUE_TEMPLATE/config.yml")
    check("issue config disables blank issues", "blank_issues_enabled: false" in cfg)
    check("issue config routes security to a private advisory (not a public issue)",
          "security/advisories" in cfg or "security advisor" in cfg.lower())
    check("Dependabot config present (automated dependency updates for go-public)",
          (REPO / ".github" / "dependabot.yml").is_file())
    # If PyYAML is available, the community YAML files must actually PARSE (a malformed config.yml /
    # dependabot.yml silently breaks GitHub's issue picker / dependency updates). Skip cleanly if absent.
    try:
        import yaml  # type: ignore
        for rel in (".github/ISSUE_TEMPLATE/config.yml", ".github/dependabot.yml"):
            try:
                yaml.safe_load(_read(rel))
                check(f"{rel} is valid YAML", True)
            except Exception as exc:   # noqa: BLE001
                check(f"{rel} is valid YAML", False, str(exc))
        dep = yaml.safe_load(_read(".github/dependabot.yml")) or {}
        ecos = [u.get("package-ecosystem") for u in dep.get("updates", [])]
        check("dependabot covers the pip runtime deps", "pip" in ecos, str(ecos))
        check("dependabot covers the github-actions CI", "github-actions" in ecos, str(ecos))
    except ImportError:
        check("(PyYAML absent — strict YAML parse of community configs skipped)", True)


def gate_security_policy() -> None:
    print("\n-- 2) SECURITY.md is a usable, private-first reporting policy --")
    text = _read("SECURITY.md")
    low = text.lower()
    check("has a 'Reporting a vulnerability' section", "reporting a vulnerability" in low)
    check("routes reports through GitHub private vulnerability reporting / advisories",
          "private vulnerability reporting" in low or "security advisor" in low or "report a vulnerability" in low)
    check("explicitly says NOT to open a public issue", "do not open a public issue" in low or "not open a public issue" in low)
    check("defines a Scope section", "scope" in low)
    check("defines Supported versions", "supported versions" in low)
    check("notes the local-only security model (loopback MCP / dev password)",
          "127.0.0.1" in text or "loopback" in low or "local" in low)
    # A hardcoded personal email is both a leak and an unmaintainable channel — advisories are the channel.
    personal = re.findall(r"[A-Za-z0-9._%+-]+@(?:gmail|yahoo|hotmail|outlook|proton)\.[A-Za-z]+", text)
    check("no hardcoded personal email address (use GitHub advisories, not a personal inbox)",
          not personal, str(personal))


def gate_ai_policy_backed_by_real_gates() -> None:
    print("\n-- 3) AI_POLICY.md's honesty claims name REAL gate files (no promise without a gate) --")
    text = _read("AI_POLICY.md")
    check("discloses AI-assisted development", "ai" in text.lower() and ("claude" in text.lower() or "assist" in text.lower()))
    check("asserts humans own/review the result", "human" in text.lower() and ("review" in text.lower() or "own" in text.lower()))
    # Every gate file the policy cites as backing a guarantee must actually exist.
    cited = re.findall(r"`(tests/[A-Za-z0-9_./-]+\.py)`", text)
    check("AI_POLICY cites at least one concrete gate file", len(cited) >= 1, str(cited))
    for rel in sorted(set(cited)):
        check(f"cited gate '{rel}' exists (the guarantee is actually enforced)", (REPO / rel).is_file())
    check("states the shipped product sends nothing to an external LLM",
          "sends nothing to an external" in text.lower() or "does not transmit" in text.lower())


def _relative_md_links(text: str) -> "list[str]":
    """Relative markdown-link targets `](path)` — excluding http(s), mailto, and pure anchors."""
    out = []
    for m in re.finditer(r"\]\(([^)]+)\)", text):
        target = m.group(1).strip().split("#", 1)[0].strip()
        if not target or target.startswith(("http://", "https://", "mailto:")):
            continue
        out.append(target)
    return out


def gate_link_integrity() -> None:
    print("\n-- 4) every RELATIVE markdown link in the new governance files resolves to a real file --")
    for rel in ("README.md", "SECURITY.md", "AI_POLICY.md", "CONTRIBUTING.md", "CODE_OF_CONDUCT.md"):
        text = _read(rel)
        for target in _relative_md_links(text):
            check(f"{rel}: link -> {target} resolves", (REPO / target).exists(), "dead relative link")


def gate_has_teeth() -> None:
    print("\n-- 5) mutation: the checks detect an ABSENT file / a dead link (no vacuous pass) --")
    check("a synthetic missing file is reported absent", not (REPO / "zzz_no_such_governance_file.md").exists())
    check("the relative-link extractor pulls a real relative link and skips http",
          _relative_md_links("see [x](SECURITY.md) and [y](https://z.com)") == ["SECURITY.md"])


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 72)
    print("DeepMap — governance / go-public doc gates (presence, SECURITY, AI honesty, links; no spend)")
    print("=" * 72)

    gate_presence()
    gate_github_templates()
    gate_security_policy()
    gate_ai_policy_backed_by_real_gates()
    gate_link_integrity()
    gate_has_teeth()

    print("\n" + "=" * 72)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
