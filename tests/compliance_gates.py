#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j, no-spend LICENSE-COMPLIANCE drift gates (§9.9 — NOTICE/SBOM vs the real dependency set).

Settled, load-bearing facts that had NOTHING locking them against silent drift:

  1. ATTRIBUTION COMPLETENESS — every SHIPPED pip dependency (what `requirements.txt` declares and the
     installers `pip install`) MUST be attributed in ALL THREE artifacts: `NOTICE`,
     `THIRD-PARTY-LICENSES.md`, and `sbom.cdx.json`. Shipping an un-attributed dependency is a real
     open-source-compliance defect (it bit us: PyYAML was installed by both installers + in
     requirements.txt but attributed nowhere, while OPTIONAL graphify was in the SBOM).

  2. ranx is NOT SHIPPED (DD-4 / FD-6 / T11, resolved 2026-06-28 — the fusion is direct `_weighted_rrf`,
     ranx was dropped). It must NEVER re-enter `requirements.txt`, the installers, or the SBOM as a
     bundled/optional component; it may appear in NOTICE/THIRD-PARTY ONLY in an explicit "not shipped /
     removed" context. `mcp-neo4j-cypher` is likewise NOT a pip dependency (the deferred stdio tool).

  3. PINS (ROUND-3 E3 / ROUND-4 F3 / ROUND-5 W8) — neo4j / mcp / graphifyy carry ONE measured range in
     every install source (requirements.txt, both installers, CI, the runtime graphify guard), installed
     with `pip install --upgrade`, AND the shipped compliance artifacts (SBOM component versions, NOTICE,
     THIRD-PARTY-LICENSES.md) state that same range + the validated releases, never a bare install.

  4. DECLARED IMPORTS (2026-09 DeepInit dependency pass) — every third-party module the shipped code imports
     (build/, hooks/, scripts/, benchmark/; lazy imports included) is a requirements.txt package, or a listed
     OPTIONAL import each site of which handles its absence. Sections 1-2 read the dependency set FROM
     requirements.txt, so an import nobody declared was invisible: v0.28 began importing `anyio` directly and it
     was declared and attributed nowhere. anyio's declared range must also stay compatible with the requirement of
     `mcp`, the pinned package it arrives through.

Pure + hermetic: reads the committed text artifacts (and, where installed, package metadata); never spends, never
touches Neo4j.
Usage: python tests/compliance_gates.py [--root <tree>]
"""
from __future__ import annotations

import ast
import json
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


def _norm(name: str) -> str:
    """Normalize a package name for matching: lowercase, drop '-'/'_' and surrounding whitespace, so
    `PyYAML` / `pyyaml`, `sentence-transformers` / `sentence_transformers`, `huggingface_hub` all compare
    equal to their attribution spellings."""
    return re.sub(r"[-_\s]", "", (name or "").strip().lower())


def _read(rel: str) -> str:
    p = REPO / rel
    return p.read_text(encoding="utf-8") if p.is_file() else ""


def _requirements_pkgs() -> "list[str]":
    """The declared shipped pip deps: each non-comment line of requirements.txt, package token only
    (strip an inline `# comment` and any version specifier)."""
    pkgs = []
    for raw in _read("requirements.txt").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        # package name = up to the first version operator / whitespace / bracket
        name = re.split(r"[<>=!~;\[\s]", line, maxsplit=1)[0].strip()
        if name:
            pkgs.append(name)
    return pkgs


def _installer_pip_pkgs() -> "set[str]":
    """Every package name the installers `pip install` (normalized, version range stripped). Covers
    install.sh + install.ps1 — incl. install.ps1's Install-PipSpecs helper, which installs a pinned spec
    list through a temporary requirements file (E3)."""
    pkgs: set = set()
    for rel, kind in (("install.sh", "sh"), ("install.ps1", "ps1")):
        for line in _install_lines(_read(rel), kind):
            m = re.search(r"pip[\"'\s,)]+install(.*)", line) or re.search(r"Install-PipSpecs(.*)", line)
            if not m:
                continue
            # quoted specs keep their `,` (a range like "mcp>=1.2,<2" is ONE token); then bare words
            tail = m.group(1)
            quoted = re.findall(r"\"([^\"]*)\"|'([^']*)'", tail)
            toks = [a or b for a, b in quoted] + re.split(r"[\s,@()]+", re.sub(r"\"[^\"]*\"|'[^']*'", " ", tail))
            for tok in toks:
                tok = tok.strip()
                if not tok or tok.startswith(("-", "$")) or "://" in tok or tok.lower() in (
                        "install", "upgrade", "py", "invoke-py", "pip", "python", "install-pipspecs"):
                    continue
                name = re.split(r"[<>=!~;\[\s]", tok, maxsplit=1)[0].strip()
                if name and re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]*$", name):
                    pkgs.add(_norm(name))
    return pkgs


def _sbom_component_keys() -> "set[str]":
    """Normalized component identifiers from the SBOM — both the `name` and any pypi purl `pkg:pypi/<n>`."""
    keys: set = set()
    try:
        data = json.loads(_read("sbom.cdx.json") or "{}")
    except json.JSONDecodeError:
        return keys
    for c in data.get("components", []):
        keys.add(_norm(str(c.get("name", ""))))
        purl = str(c.get("purl", ""))
        m = re.search(r"pkg:pypi/([^@?\s]+)", purl)
        if m:
            keys.add(_norm(m.group(1)))
    return keys


def _attributed_in_text(pkg: str, text: str) -> bool:
    """A dep is attributed in a prose artifact if its normalized name appears in the artifact's normalized
    text. (The artifacts spell deps in prose, e.g. `* pyyaml — MIT`, so substring-on-normalized is the
    right check; unique names like `pyyaml` can't false-match.)"""
    return _norm(pkg) in _norm(text)


def gate_attribution_completeness() -> None:
    print("\n-- 1) every SHIPPED pip dependency is attributed in NOTICE + THIRD-PARTY + SBOM --")
    reqs = _requirements_pkgs()
    check("requirements.txt parsed to a non-empty dependency set", len(reqs) >= 5, str(reqs))
    notice = _read("NOTICE")
    third = _read("THIRD-PARTY-LICENSES.md")
    sbom_keys = _sbom_component_keys()
    for pkg in reqs:
        check(f"'{pkg}' attributed in NOTICE", _attributed_in_text(pkg, notice))
        check(f"'{pkg}' attributed in THIRD-PARTY-LICENSES.md", _attributed_in_text(pkg, third))
        check(f"'{pkg}' present in sbom.cdx.json components", _norm(pkg) in sbom_keys,
              f"sbom keys: {sorted(sbom_keys)}")


def gate_installer_deps_attributed() -> None:
    print("\n-- 2) every package the installers pip-install is a KNOWN, attributed dep --")
    installed = _installer_pip_pkgs()
    check("installers pip-install a non-empty set", len(installed) >= 3, str(sorted(installed)))
    notice_third = _norm(_read("NOTICE") + _read("THIRD-PARTY-LICENSES.md"))
    for pkg in sorted(installed):
        check(f"installer dep '{pkg}' is attributed (NOTICE/THIRD-PARTY)", pkg in notice_third,
              "an installed package must be license-attributed")


def gate_ranx_not_shipped() -> None:
    print("\n-- 3) ranx is NOT SHIPPED (DD-4/FD-6/T11) — never a pip dep / installer / SBOM component --")
    reqs_norm = {_norm(p) for p in _requirements_pkgs()}
    check("ranx NOT in requirements.txt", "ranx" not in reqs_norm, str(sorted(reqs_norm)))
    check("ranx NOT pip-installed by the installers", "ranx" not in _installer_pip_pkgs())
    check("ranx NOT a component in sbom.cdx.json", "ranx" not in _sbom_component_keys())
    # If ranx is mentioned in NOTICE at all, it must be in an explicit 'not shipped / removed' context.
    notice = _read("NOTICE")
    if "ranx" in notice.lower():
        idx = notice.lower().find("ranx")
        window = notice[max(0, idx - 20): idx + 120].lower()
        check("ranx in NOTICE only in a 'NOT A DEPENDENCY / removed / not shipped' context",
              any(k in window for k in ("not a dependency", "not shipped", "removed", "not wired")),
              window.strip())


def gate_deferred_not_shipped() -> None:
    print("\n-- 4) mcp-neo4j-cypher (the deferred stdio 3rd tool) is NOT a pip dependency --")
    reqs_norm = {_norm(p) for p in _requirements_pkgs()}
    check("mcp-neo4j-cypher NOT in requirements.txt", "mcpneo4jcypher" not in reqs_norm)
    check("mcp-neo4j-cypher NOT pip-installed by the installers",
          "mcpneo4jcypher" not in _installer_pip_pkgs())


def _dep_in_installer(pkg: str, rel: str) -> bool:
    """True iff the (normalized) dep name appears in an installer's text. Robust to line wraps (the ps1
    torch install spans two lines) — a normalized-substring presence check, since these deps only ever
    appear in the installers' pip-install invocations (verified)."""
    return _norm(pkg) in _norm(_read(rel))


def gate_installer_cross_platform_parity() -> None:
    print("\n-- 5) install.sh and install.ps1 ship the SAME runtime dep set (cross-platform parity) --")
    # A dep installed on one OS but not the other = Windows and POSIX users get different runtime
    # environments (a real cross-platform bug). Lock parity over the declared requirements set (the
    # authoritative dependency manifest); a dep that is transitive-only on BOTH agrees (both absent).
    candidates = _requirements_pkgs()
    check("requirements.txt yields a candidate dep set", len(candidates) >= 5, str(candidates))
    for pkg in candidates:
        in_sh = _dep_in_installer(pkg, "install.sh")
        in_ps1 = _dep_in_installer(pkg, "install.ps1")
        check(f"'{pkg}' parity: install.sh ({in_sh}) == install.ps1 ({in_ps1})", in_sh == in_ps1,
              "a dep installed on one OS but not the other splits the runtime environment")
    # The core import-critical deps must be in BOTH (not merely 'equal by being absent from both').
    for core in ("neo4j", "mcp"):
        check(f"core dep '{core}' is pip-installed by BOTH installers",
              _dep_in_installer(core, "install.sh") and _dep_in_installer(core, "install.ps1"))


def gate_installer_parity_has_teeth() -> None:
    print("\n-- 6) mutation: the installer-parity check detects an actual divergence --")
    # Prove the presence check isn't vacuous: a dep genuinely present in one installer's text and not
    # the synthetic 'other' must compare unequal.
    real_sh = _dep_in_installer("torch", "install.sh")
    fake_other = _norm("torch") in _norm("pip install neo4j mcp pyyaml only")   # genuinely no torch token
    check("a real dep present in install.sh but absent in a synthetic installer -> UNEQUAL (teeth)",
          real_sh is True and fake_other is False and (real_sh != fake_other))
    check("a shared dep compares EQUAL across both real installers",
          _dep_in_installer("neo4j", "install.sh") == _dep_in_installer("neo4j", "install.ps1") == True)


def gate_matcher_has_teeth() -> None:
    print("\n-- 7) mutation: the attribution matcher actually detects a MISSING dep (no vacuous pass) --")
    # A package that appears in NO artifact must be reported UNATTRIBUTED — proves the gate isn't a no-op.
    check("a synthetic absent package is NOT falsely 'attributed'",
          not _attributed_in_text("zzz_nonexistent_pkg_9999", _read("NOTICE") + _read("THIRD-PARTY-LICENSES.md")))
    check("a real attributed package IS detected", _attributed_in_text("neo4j", _read("NOTICE")))
    # The SBOM key extraction must actually find pypi purls.
    check("sbom purl extraction finds pypi components", "neo4j" in _sbom_component_keys())


# --------------------------------------------------------------------------------------------------- #
# 8) DEPENDENCY PINS (ROUND-3 E3, ROUND-4 F3). An unpinned `pip install mcp` resolved to mcp 2.x, which
#    removed `mcp.server.fastmcp` — both MCP servers died at import on every fresh install and in CI. An
#    unpinned `graphifyy` resolved to 0.9.x, whose skip-dir walk the D5 rescue was never validated against.
#    The validated ranges live in FOUR places a user or CI actually installs from; each must pin every one
#    of these packages, never install it bare, and carry the IDENTICAL range (requirements.txt is the
#    reference). Every range needs a floor AND a ceiling, and BOTH ends are measured, not guessed:
#      * ceiling — the first release nobody validated stays out (KNOWN_ABOVE_CEILING): mcp 2 (no
#        mcp.server.fastmcp in the 2.0.0 wheel), graphifyy 0.8.52 (the validated range is exactly the
#        releases run end to end, 0.8.39 and 0.8.51, plus the statically diffed ones between — the old
#        `<0.9` let pip install an unvalidated 0.8.51 whose CUDA/Metal parsers were dropped silently) and
#        graphifyy 0.9 (changed skip-dir walk), neo4j 7 (unreleased);
#      * floor — the releases MEASURED broken stay out (KNOWN_BELOW_FLOOR), because an older copy that
#        already satisfies a too-low floor is kept by pip ("Requirement already satisfied"):
#          mcp 1.13.1 — both servers die at import (`TypeError: issubclass() arg 1 must be a class`) next
#            to the pydantic 2.13 / starlette 1.7 a fresh resolve installs (1.14.0 is the first that imports);
#          mcp 1.23.3 — the newest release without `mcp.client.streamable_http.streamable_http_client`, the
#            client the MCP project-guard / project-label gates drive (1.24.0 added it);
#          neo4j 5.28.6 — the newest 5.x: failed the gate suite when measured (round 4: no
#            `neo4j.exceptions.ConnectionPoolError`, which the CLI's error classifier then used unguarded, so
#            `deepmap doctor` crashed on every 5.x driver — the classifier has been defensive since round 5,
#            but no 5.x is re-validated, so it stays below the floor; 5.6.0 also rejects
#            notifications_min_severity);
#          neo4j 6.0.0 — its bolt layer reads `socket.EAI_ADDRFAMILY` unguarded, which Windows lacks: every
#            Neo4j connection dies at import there (guarded from 6.0.1).
#        Evidence: full tests/run_all.py in real venvs at the floors (mcp 1.24.0 + neo4j 6.0.1) and at the
#        top of the ranges (mcp 1.30.0 + neo4j 6.3.1 + graphifyy 0.8.51), recorded in requirements.txt.
#    The installers install these specs with `pip install --upgrade`, so an old in-range copy (graphifyy
#    0.8.39 without the CUDA/Metal parsers) is lifted to the top of the range and an out-of-range one is
#    replaced (UPGRADE check below).
# --------------------------------------------------------------------------------------------------- #
PINNED_PACKAGES = ("neo4j", "mcp", "graphifyy")
KNOWN_ABOVE_CEILING = {"mcp": ("2",), "graphifyy": ("0.8.52", "0.9"), "neo4j": ("7",)}   # must stay OUTSIDE
KNOWN_BELOW_FLOOR = {"mcp": ("1.13.1", "1.23.3"),                                      # measured broken
                     "neo4j": ("5.6.0", "5.28.6", "6.0.0")}
PIN_SOURCES = ("requirements.txt", "install.sh", "install.ps1", ".github/workflows/validate.yml")

_SPEC_RE = r"(?:\s*(?:===|==|>=|<=|!=|~=|>|<)\s*[0-9][0-9A-Za-z.*+!-]*\s*,?)+"


def _norm_spec(spec: str) -> str:
    """Canonical form of a specifier set: whitespace dropped, clauses sorted — so `<2,>=1.2` == `>=1.2, <2`."""
    parts = [p.strip() for p in re.sub(r"\s+", "", spec or "").split(",") if p.strip()]
    return ",".join(sorted(parts))


def _strip_comment(line: str, kind: str) -> str:
    """Drop a trailing comment (`#` for sh / ps1 / yml / requirements) — but not a `#` inside quotes."""
    out, q = [], None
    for ch in line:
        if q:
            out.append(ch)
            if ch == q:
                q = None
            continue
        if ch in "\"'":
            q = ch
        elif ch == "#":
            break
        out.append(ch)
    return "".join(out)


def _install_lines(text: str, kind: str) -> "list[str]":
    """The code lines (comments stripped) that INSTALL packages in one source:
      * requirements -> every requirement line;
      * sh / yml     -> lines running `pip install`;
      * ps1          -> lines calling pip with an `"install"` argument, or the Install-PipSpecs helper
                        (which installs a spec list through a temporary requirements file)."""
    lines = []
    for raw in text.splitlines():
        line = _strip_comment(raw, kind).strip()
        if not line:
            continue
        if kind == "requirements":
            lines.append(line)
        elif kind in ("sh", "yml"):
            if re.search(r"\bpip\b[\"'\s]+install\b", line):
                lines.append(line)
        elif kind == "ps1":
            if (('"pip"' in line and '"install"' in line) or re.search(r"\bInstall-PipSpecs\b", line)) \
                    and not re.match(r"function\b", line, re.IGNORECASE):
                lines.append(line)
    return lines


def pin_findings(text: str, kind: str) -> "tuple[dict, dict]":
    """(pins, bare) for one install source: pins = {pkg: set(normalized specs)} for every pinned-package
    requirement it installs WITH a version range; bare = {pkg: [lines]} for every install of a pinned
    package WITHOUT one (the defect: `pip install mcp` -> whatever PyPI's newest is)."""
    pins: dict = {}
    bare: dict = {}
    for line in _install_lines(text, kind):
        for pkg in PINNED_PACKAGES:
            # the package as a whole token: not part of `.mcp.json`, `mcp-start`, `graphifyy[all]` …
            for m in re.finditer(rf"(?<![\w.\-/\[$]){re.escape(pkg)}(?![\w\-.\[])", line):
                tail = line[m.end():]
                spec = re.match(_SPEC_RE, tail)
                if spec and spec.group(0).strip():
                    pins.setdefault(pkg, set()).add(_norm_spec(spec.group(0)))
                else:
                    bare.setdefault(pkg, []).append(line)
    return pins, bare


def _ps1_function_body(text: str, name: str) -> str:
    """The body of `function <name> ... { ... }` in a PowerShell script (brace-matched), or ''."""
    m = re.search(r"^function\s+" + re.escape(name) + r"\b[^{]*\{", text, re.MULTILINE | re.IGNORECASE)
    if not m:
        return ""
    depth, i = 1, m.end()
    while i < len(text) and depth:
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        i += 1
    return text[m.end():i - 1]


def upgrade_findings(text: str, kind: str) -> "list[str]":
    """The pinned packages one install source installs WITHOUT `--upgrade` (sorted). sh / yml: the pip
    install line itself must carry `--upgrade`/`-U`; ps1: the pinned specs go through Install-PipSpecs,
    whose pip call must carry `"--upgrade"` (a raw `"pip", "install"` array with a pinned package must too)."""
    missing: set = set()
    helper_upgrades = False
    if kind == "ps1":
        body = _ps1_function_body(text, "Install-PipSpecs")
        helper_upgrades = bool(re.search(r"[\"']--upgrade[\"']|[\"']-U[\"']", body))
    for line in _install_lines(text, kind):
        pins, bare = pin_findings(line, kind)
        pkgs = set(pins) | set(bare)
        if not pkgs:
            continue
        if kind == "ps1" and re.search(r"\bInstall-PipSpecs\b", line):
            ok = helper_upgrades
        else:
            ok = bool(re.search(r"(?<![\w-])(?:--upgrade|-U)(?![\w-])", line))
        if not ok:
            missing |= pkgs
    return sorted(missing)


def _kind_for(rel: str) -> str:
    return {"requirements.txt": "requirements", "install.sh": "sh", "install.ps1": "ps1"}.get(rel, "yml")


def _parse_version(v: str) -> "tuple":
    """A release tuple for ordering (`1.28.1` -> (1, 28, 1)); pre-release/local suffixes are ignored."""
    nums = []
    for part in re.split(r"[.+-]", v.strip()):
        m = re.match(r"\d+", part)
        if not m:
            break
        nums.append(int(m.group(0)))
    while len(nums) > 1 and nums[-1] == 0:
        nums.pop()
    return tuple(nums)


def spec_allows(spec: str, version: str) -> bool:
    """Does the (normalized, `>=`/`<`/`==`/`<=`/`>`/`!=` only) specifier set admit `version`?"""
    v = _parse_version(version)
    for clause in [c for c in spec.split(",") if c]:
        m = re.match(r"(===|==|>=|<=|!=|~=|>|<)(.+)", clause)
        if not m:
            return False
        op, bound = m.group(1), _parse_version(m.group(2))
        ok = {">=": v >= bound, ">": v > bound, "<=": v <= bound, "<": v < bound,
              "==": v == bound, "===": v == bound, "!=": v != bound,
              "~=": v >= bound and v[:max(1, len(bound) - 1)] == bound[:max(1, len(bound) - 1)]}[op]
        if not ok:
            return False
    return True


def _has_floor_and_ceiling(spec: str) -> bool:
    ops = [re.match(r"(===|==|>=|<=|!=|~=|>|<)", c).group(1) for c in spec.split(",") if c]
    return any(o in (">=", ">", "==", "===", "~=") for o in ops) and any(o in ("<", "<=", "==", "===", "~=")
                                                                     for o in ops)


def gate_dependency_pins() -> None:
    print("\n-- 8) E3: neo4j / mcp / graphifyy are PINNED to one validated range in every install source --")
    found = {}
    for rel in PIN_SOURCES:
        text = _read(rel)
        check(f"{rel} exists", bool(text))
        found[rel] = pin_findings(text, _kind_for(rel))
    ref_pins, _ = found["requirements.txt"]
    for pkg in PINNED_PACKAGES:
        ref = ref_pins.get(pkg, set())
        check(f"requirements.txt pins '{pkg}' with exactly one range", len(ref) == 1, str(sorted(ref)))
        spec = next(iter(ref)) if len(ref) == 1 else ""
        if spec:
            check(f"'{pkg}' range {spec!r} has a floor AND a ceiling", _has_floor_and_ceiling(spec))
            for v in KNOWN_ABOVE_CEILING.get(pkg, ()):
                check(f"'{pkg}' range {spec!r} excludes the unvalidated/breaking {pkg} {v} (ceiling)",
                      not spec_allows(spec, v))
            for v in KNOWN_BELOW_FLOOR.get(pkg, ()):
                check(f"'{pkg}' range {spec!r} excludes {pkg} {v}, measured broken (floor)",
                      not spec_allows(spec, v))
        for rel in PIN_SOURCES[1:]:
            pins, bare = found[rel]
            if rel.endswith(".yml") and pkg not in pins and pkg not in bare:
                continue   # CI need not install every runtime dep — but what it installs must be pinned
            check(f"{rel}: installs '{pkg}' pinned (never bare)", pkg not in bare,
                  f"bare install: {bare.get(pkg)}")
            check(f"{rel}: '{pkg}' range == requirements.txt ({spec or '?'})",
                  pins.get(pkg) == ref and bool(ref), f"{rel} has {sorted(pins.get(pkg, set()))}")
    # CI must exercise the pinned MCP SDK + driver (the servers + retriever import them at module load).
    ci_pins, _ = found[".github/workflows/validate.yml"]
    for core in ("neo4j", "mcp"):
        check(f"CI installs '{core}' (pinned) for the no-Neo4j gate", core in ci_pins)
    # The runtime half of the graphify pin: build/graphify_extract.py warns + skips the skipped-dir rescue
    # outside SUPPORTED_GRAPHIFY_SPEC — it must name the very range the installers install.
    src = _read("build/graphify_extract.py")
    m = re.search(r"^SUPPORTED_GRAPHIFY_SPEC\s*=\s*[\"']([^\"']+)[\"']", src, re.MULTILINE)
    runtime = pin_findings(m.group(1), "requirements")[0].get("graphifyy") if m else None
    check("build/graphify_extract.py SUPPORTED_GRAPHIFY_SPEC == requirements.txt's graphifyy range",
          runtime is not None and runtime == ref_pins.get("graphifyy"),
          f"runtime guard {m.group(1) if m else 'MISSING'!r} vs {sorted(ref_pins.get('graphifyy', set()))}")
    # UPGRADE (F3): a pinned spec installed WITHOUT --upgrade leaves an old copy that satisfies it in place
    # (graphifyy 0.8.39 has no CUDA/Metal parser, and a stale in-range copy is never refreshed).
    for rel in PIN_SOURCES[1:]:
        missing = upgrade_findings(_read(rel), _kind_for(rel))
        check(f"{rel}: every install of a pinned package uses `pip install --upgrade`", not missing,
              f"installed without --upgrade: {missing}")


def gate_installed_versions_in_range() -> None:
    print("\n-- 9) E3: the INSTALLED neo4j / mcp / graphifyy (when present) are inside the pinned range --")
    try:
        from importlib import metadata as _md
    except Exception as exc:  # pragma: no cover — py3.8+ always has it
        check("importlib.metadata available", False, repr(exc))
        return
    ref_pins, _ = pin_findings(_read("requirements.txt"), "requirements")
    for pkg in PINNED_PACKAGES:
        spec = next(iter(ref_pins.get(pkg, {""})))
        try:
            ver = _md.version(pkg)
        except Exception:
            print(f"  [skip] {pkg} is not installed here — nothing to check")
            continue
        check(f"installed {pkg} {ver} satisfies the pinned range {spec!r}", bool(spec) and spec_allows(spec, ver),
              "this environment runs an UNVALIDATED version — reinstall from requirements.txt")


def gate_pin_detectors_have_teeth() -> None:
    print("\n-- 10) mutation: the pin detectors catch a bare install, a missing pin and a divergent range --")
    old_sh = "  py -m pip install neo4j mcp pyyaml\n  py -m pip install graphifyy\n"
    pins, bare = pin_findings(old_sh, "sh")
    check("the pre-E3 install.sh lines are flagged BARE for neo4j, mcp and graphifyy",
          set(bare) == {"neo4j", "mcp", "graphifyy"} and not pins, f"pins={pins} bare={sorted(bare)}")
    old_ps = '  Invoke-Py $PyCmd @("-m", "pip", "install", "neo4j", "mcp", "pyyaml")\n'
    pins, bare = pin_findings(old_ps, "ps1")
    check("the pre-E3 install.ps1 pip array is flagged BARE for neo4j and mcp",
          set(bare) == {"neo4j", "mcp"} and not pins, f"pins={pins} bare={sorted(bare)}")
    old_ci = "        run: python -m pip install --upgrade pip pyyaml neo4j mcp\n"
    check("the pre-E3 CI install step is flagged BARE for neo4j and mcp",
          set(pin_findings(old_ci, "yml")[1]) == {"neo4j", "mcp"})
    new_sh = '  py -m pip install "neo4j>=5,<7" "mcp>=1.2,<2" pyyaml\n'
    pins, bare = pin_findings(new_sh, "sh")
    check("a pinned install line yields the ranges and nothing bare",
          pins == {"neo4j": {"<7,>=5"}, "mcp": {"<2,>=1.2"}} and not bare, f"pins={pins} bare={bare}")
    new_ps = '  Install-PipSpecs $PyCmd @("neo4j>=5,<7", "mcp>=1.2,<2", "pyyaml")\n'
    check("the ps1 Install-PipSpecs call is read the same way",
          pin_findings(new_ps, "ps1")[0] == {"neo4j": {"<7,>=5"}, "mcp": {"<2,>=1.2"}})
    diverged = pin_findings('  py -m pip install "mcp>=1.2,<3"\n', "sh")[0]
    check("a divergent range compares UNEQUAL to requirements.txt's",
          diverged.get("mcp") != {"<2,>=1.2"}, str(diverged))
    check("prose / other tokens are not mistaken for installs (.mcp.json, mcp-start, graphifyy[all])",
          pin_findings('  py -m pip install foo  # .mcp.json mcp-start\n', "sh") == ({}, {})
          and "graphifyy" not in pin_findings('  py -m pip install graphifyy[all]\n', "sh")[1])
    check("spec_allows: mcp 1.28.1 in >=1.2,<2; mcp 2.2.0 NOT; graphifyy 0.9.67 NOT in >=0.8.39,<0.9",
          spec_allows("<2,>=1.2", "1.28.1") and not spec_allows("<2,>=1.2", "2.2.0")
          and not spec_allows("<0.9,>=0.8.39", "0.9.67") and spec_allows("<0.9,>=0.8.39", "0.8.39"))
    check("a floor-only range (the pre-E3 kind of drift) has no ceiling -> rejected",
          not _has_floor_and_ceiling(">=1.2") and _has_floor_and_ceiling("<2,>=1.2"))
    # ROUND-4 F3 teeth: the round-3 ranges are rejected by the measured floor / ceiling lists
    check("teeth: the round-3 mcp range >=1.2,<2 admits the measured-broken mcp 1.13.1 (fails the floor)",
          any(spec_allows("<2,>=1.2", v) for v in KNOWN_BELOW_FLOOR["mcp"]))
    check("teeth: the round-3 neo4j range >=5,<7 admits the 5.x drivers the CLI crashes on (fails the floor)",
          all(spec_allows("<7,>=5", v) for v in KNOWN_BELOW_FLOOR["neo4j"]))
    check("teeth: the round-3 graphifyy range >=0.8.39,<0.9 admits the unvalidated 0.8.52 (fails the ceiling)",
          spec_allows("<0.9,>=0.8.39", "0.8.52"))
    check("teeth: a pinned sh install WITHOUT --upgrade is flagged; the same line WITH it is not",
          upgrade_findings('  py -m pip install "neo4j>=6.0,<7" "mcp>=1.14,<2" pyyaml\n', "sh") == ["mcp", "neo4j"]
          and upgrade_findings('  py -m pip install --upgrade "neo4j>=6.0,<7" "mcp>=1.14,<2" pyyaml\n', "sh") == [])
    helper_no = ('function Install-PipSpecs([string]$PyCmd, [string[]]$Specs) {\n'
                 '  Invoke-Py $PyCmd @("-m", "pip", "install", "-r", $req)\n}\n'
                 'Install-PipSpecs $PyCmd @("graphifyy>=0.8.39,<0.8.52")\n')
    helper_up = helper_no.replace('"install", "-r"', '"install", "--upgrade", "-r"')
    check("teeth: ps1 Install-PipSpecs without --upgrade is flagged; with it, not",
          upgrade_findings(helper_no, "ps1") == ["graphifyy"] and upgrade_findings(helper_up, "ps1") == [])
    check("teeth: a CI line without --upgrade is flagged",
          upgrade_findings('        run: python -m pip install pyyaml "mcp>=1.14,<2"\n', "yml") == ["mcp"])


# --------------------------------------------------------------------------------------------------- #
# 11) THE SHIPPED COMPLIANCE ARTIFACTS CARRY THE SAME PINS (ROUND-5 W8). Round 4 pinned neo4j / mcp /
#     graphifyy in every install source, but sbom.cdx.json still said "UNPINNED" for all three and SBOM /
#     NOTICE / THIRD-PARTY-LICENSES.md still told a reader to `pip install graphifyy` ("Validated at
#     0.8.39") — which resolves to the unvalidated 0.9.x the pin exists to keep out. Sections 1-10 check
#     presence and license only, so they stayed GREEN. Now each artifact must state requirements.txt's
#     EXACT range for every pinned package; the SBOM component `version` IS that range (a range — the
#     release is resolved at install), with the validated releases and the install command as
#     properties; every `pip install` of a pinned package they print is the pinned, `--upgrade` form; and
#     graphify's "validated at" releases are requirements.txt's.
# --------------------------------------------------------------------------------------------------- #
# The releases the full gate suite ran on, per package (round-4 W8 venv evidence, recorded in
# requirements.txt: the floors, the validated baseline, the top of each range). Every one must lie inside
# its pinned range and the range's floor must be one of them (a floor is measured, never guessed).
VALIDATED_AT = {"neo4j": ("6.0.1", "6.2.0", "6.3.1"),
                "mcp": ("1.24.0", "1.28.1", "1.30.0"),
                "graphifyy": ("0.8.39", "0.8.51")}
ARTIFACTS = ("NOTICE", "THIRD-PARTY-LICENSES.md", "sbom.cdx.json")


def req_raw_specs(text: str) -> dict:
    """{pkg: 'pkg>=x,<y'} — the requirement token, as written, for each pinned package in requirements.txt."""
    out: dict = {}
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        name = re.split(r"[<>=!~;\[\s]", line, maxsplit=1)[0].strip() if line else ""
        if _norm(name) in PINNED_PACKAGES:
            out[_norm(name)] = re.sub(r"\s+", "", line)
    return out


def req_validated_graphify(text: str) -> tuple:
    """The graphify releases requirements.txt says were validated ('Validated at graphifyy A and B.')."""
    m = re.search(r"Validated at graphifyy (\d+(?:\.\d+)+) and (\d+(?:\.\d+)+)", text)
    return (m.group(1), m.group(2)) if m else ()


def install_command(raw_spec: str) -> str:
    """The one install instruction a compliance artifact may print for a pinned package."""
    return f'pip install --upgrade "{raw_spec}"'


def _sbom_strings(node) -> "list[str]":
    """Every string value in a parsed SBOM (keys excluded)."""
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [s for v in node.values() for s in _sbom_strings(v)]
    if isinstance(node, list):
        return [s for v in node for s in _sbom_strings(v)]
    return []


def _sbom_pinned_component(sbom: dict, pkg: str) -> "list[dict]":
    comps = sbom.get("components", []) if isinstance(sbom, dict) else []
    out = []
    for c in comps:
        m = re.search(r"pkg:pypi/([^@?\s]+)", str(c.get("purl", ""))) if isinstance(c, dict) else None
        if m and _norm(m.group(1)) == pkg:
            out.append(c)
    return out


def install_segments(text: str) -> "list[str]":
    """Each `pip install ...` instruction printed in prose: from `pip` to the end of its code span (the next
    backtick) or of the line. A package NAMED elsewhere on the line (a table row's name column) is not an
    install; the command itself is."""
    return [m.group(0) for m in re.finditer(r"\bpip\b[\"'\s]+install\b[^`\n]*", text)]


def _props(component: dict) -> dict:
    return {str(p.get("name")): str(p.get("value")) for p in (component.get("properties") or [])
            if isinstance(p, dict)}


def artifact_pin_problems(texts: dict, sbom: dict, raw: dict, validated: tuple) -> "list[str]":
    """Pure: what in the compliance artifacts contradicts the pins. `texts` = {artifact name: its text} (the
    SBOM's text = its string values), `sbom` = the parsed SBOM, `raw` = req_raw_specs(requirements.txt),
    `validated` = req_validated_graphify(requirements.txt). [] = consistent."""
    probs: list = []
    if set(raw) != set(PINNED_PACKAGES):
        probs.append(f"requirements.txt does not pin all of {PINNED_PACKAGES} (found {sorted(raw)})")
    for pkg, spec in sorted(raw.items()):
        want = _norm_spec(spec[len(pkg):])
        comps = _sbom_pinned_component(sbom, pkg)
        if len(comps) != 1:
            probs.append(f"sbom.cdx.json: {len(comps)} components with purl pkg:pypi/{pkg} (want exactly 1)")
        for c in comps:
            ver = str(c.get("version", ""))
            if _norm_spec(ver) != want or "unpinned" in ver.lower():
                probs.append(f"sbom.cdx.json: {pkg} version {ver!r} != requirements.txt's range {spec[len(pkg):]!r}")
            props = _props(c)
            if props.get("deepmap:install") != install_command(spec):
                probs.append(f"sbom.cdx.json: {pkg} deepmap:install {props.get('deepmap:install')!r} != "
                             f"{install_command(spec)!r}")
            if props.get("deepmap:validated_versions") != ", ".join(VALIDATED_AT.get(pkg, ())):
                probs.append(f"sbom.cdx.json: {pkg} deepmap:validated_versions {props.get('deepmap:validated_versions')!r}"
                             f" != {', '.join(VALIDATED_AT.get(pkg, ()))!r}")
        for name, text in texts.items():
            flat = re.sub(r"\s+", "", text)
            if spec not in flat:
                probs.append(f"{name}: does not state {pkg}'s pinned range {spec!r}")
            for seg in install_segments(text):
                pins, bare = pin_findings(seg, "sh")
                if pkg in bare:
                    probs.append(f"{name}: an UNPINNED install of {pkg}: {seg.strip()[:140]!r}")
                elif pkg in pins and pins[pkg] != {want}:
                    probs.append(f"{name}: installs {pkg} with {sorted(pins[pkg])}, not {want!r}: {seg.strip()[:140]!r}")
                if pkg in (set(pins) | set(bare)) and pkg in upgrade_findings(seg, "sh"):
                    probs.append(f"{name}: installs {pkg} without --upgrade (an old in-range copy is kept): "
                                 f"{seg.strip()[:140]!r}")
    graphify = raw.get("graphifyy")
    for name, text in texts.items():
        if graphify and install_command(graphify) not in text:
            probs.append(f"{name}: never prints the pinned graphify install {install_command(graphify)!r}")
        if validated:
            pat = rf"validated at (?:graphifyy )?{re.escape(validated[0])} and {re.escape(validated[1])}"
            if not re.search(pat, text, re.IGNORECASE):
                probs.append(f"{name}: does not say graphify is validated at {validated[0]} and {validated[1]}")
        stale = re.search(r"versions? (?:fields )?(?:are|is) (?:intentionally )?(?:unpinned|placeholders?)", text,
                          re.IGNORECASE)
        if stale:
            probs.append(f"{name}: still says versions are unpinned / placeholders ({stale.group(0)!r})")
    return probs


def _artifact_texts() -> "tuple[dict, dict]":
    try:
        sbom = json.loads(_read("sbom.cdx.json") or "{}")
    except json.JSONDecodeError:
        sbom = {}
    texts = {"NOTICE": _read("NOTICE"), "THIRD-PARTY-LICENSES.md": _read("THIRD-PARTY-LICENSES.md"),
             "sbom.cdx.json": "\n".join(_sbom_strings(sbom))}
    return texts, sbom


def gate_artifacts_carry_pins() -> None:
    print("\n-- 11) SBOM / NOTICE / THIRD-PARTY-LICENSES state the SAME pinned ranges as requirements.txt --")
    req = _read("requirements.txt")
    raw = req_raw_specs(req)
    validated = req_validated_graphify(req)
    check("requirements.txt: every pinned package has one raw requirement token",
          set(raw) == set(PINNED_PACKAGES), str(raw))
    check("requirements.txt names the validated graphify releases ('Validated at graphifyy A and B')",
          len(validated) == 2, str(validated))
    check("VALIDATED_AT['graphifyy'] == the releases requirements.txt says were validated",
          tuple(VALIDATED_AT["graphifyy"]) == tuple(validated), f"{VALIDATED_AT['graphifyy']} vs {validated}")
    for pkg, spec in sorted(raw.items()):
        norm = _norm_spec(spec[len(pkg):])
        vs = VALIDATED_AT.get(pkg, ())
        check(f"'{pkg}': every validated release {vs} lies inside {spec!r}",
              bool(vs) and all(spec_allows(norm, v) for v in vs))
        floor = next((c[2:] for c in norm.split(",") if c.startswith(">=")), None)
        check(f"'{pkg}': the range's floor ({floor}) is a validated release (measured, not guessed)",
              floor is not None and any(_parse_version(floor) == _parse_version(v) for v in vs), str(vs))
    texts, sbom = _artifact_texts()
    probs = artifact_pin_problems(texts, sbom, raw, validated)
    for name in ARTIFACTS:
        mine = [p for p in probs if p.startswith(name + ":")]
        check(f"{name}: consistent with the pins (ranges, validated releases, pinned --upgrade install wording)",
              not mine, "; ".join(mine[:6]))
    rest = [p for p in probs if not p.startswith(ARTIFACTS)]
    check("no pin inconsistency outside the three artifacts", not rest, "; ".join(rest))
    # The Neo4j server is not a pip dep: the SBOM names the release the provisioner fetches by default.
    m = re.search(r'^DEFAULT_VERSION\s*=\s*"(\d+\.\d+\.\d+)"', _read("build/provision.py"), re.MULTILINE)
    server = [c for c in sbom.get("components", []) if isinstance(c, dict) and c.get("bom-ref") == "neo4j-community"]
    check("sbom.cdx.json: Neo4j Community's version names the provisioner's default release (DEFAULT_VERSION)",
          bool(m) and len(server) == 1 and str(server[0].get("version", "")).startswith(m.group(1)),
          f"{m.group(1) if m else 'DEFAULT_VERSION?'} vs {[c.get('version') for c in server]}")
    for name in ("NOTICE", "THIRD-PARTY-LICENSES.md"):
        check(f"{name}: names the Neo4j release the provisioner fetches by default ('fetches <DEFAULT_VERSION> by "
              "default')", bool(m) and f"fetches {m.group(1)} by default" in re.sub(r"\s+", " ", texts[name]),
              m.group(1) if m else "DEFAULT_VERSION?")


def gate_artifact_detector_has_teeth() -> None:
    print("\n-- 12) mutation: the artifact pin check catches the round-4 state (UNPINNED + bare pip install) --")
    req = _read("requirements.txt")
    raw = req_raw_specs(req)
    validated = req_validated_graphify(req)
    texts, sbom = _artifact_texts()
    old_sbom = {"components": [
        {"name": "graphifyy", "version": "UNPINNED", "purl": "pkg:pypi/graphifyy", "properties": [
            {"name": "deepmap:distribution", "value": "Installed BARE by both installers + requirements.txt "
                                                      "(`pip install graphifyy`, no extras). Validated at 0.8.39."}]},
        {"name": "neo4j", "version": "UNPINNED", "purl": "pkg:pypi/neo4j"},
        {"name": "mcp", "version": "UNPINNED", "purl": "pkg:pypi/mcp"}]}
    old = artifact_pin_problems({"sbom.cdx.json": "\n".join(_sbom_strings(old_sbom))}, old_sbom, raw, validated)
    check("the round-4 SBOM (version UNPINNED, `pip install graphifyy`, 'Validated at 0.8.39') is flagged",
          any("version 'UNPINNED'" in p for p in old) and any("UNPINNED install of graphifyy" in p for p in old)
          and any("validated at" in p for p in old), "; ".join(old[:4]))
    old_notice = ("    graphify|auto), installed BARE (`pip install graphifyy`, no extras) by both\n"
                  "  version fields are placeholders until pinned at T11\n")
    got = artifact_pin_problems({"NOTICE": old_notice}, sbom, raw, validated)
    check("the round-4 NOTICE wording (bare `pip install graphifyy`, 'placeholders') is flagged",
          any("UNPINNED install of graphifyy" in p for p in got) and any("placeholders" in p for p in got),
          "; ".join(got[:4]))
    spec = raw.get("graphifyy", "graphifyy>=0.8.39,<0.8.52")
    no_up = texts["NOTICE"].replace(install_command(spec), f'pip install "{spec}"')
    got = artifact_pin_problems({"NOTICE": no_up}, sbom, raw, validated)
    check("a pinned install printed WITHOUT --upgrade is flagged",
          any("without --upgrade" in p for p in got), "; ".join(got[:4]))
    wide = json.loads(json.dumps(sbom))
    for c in _sbom_pinned_component(wide, "graphifyy"):
        c["version"] = ">=0.8.39,<0.9"
    got = artifact_pin_problems({}, wide, raw, validated)
    check("an SBOM range that differs from requirements.txt ('<0.9') is flagged",
          any("graphifyy version '>=0.8.39,<0.9'" in p for p in got), "; ".join(got[:4]))
    row = "| `graphifyy` (MIT) | bundled | installers also `pip install pyyaml` |\n"
    check("a package NAMED on a line whose install command is for another package is not an install",
          install_segments(row) == ["pip install pyyaml"] and not pin_findings(install_segments(row)[0], "sh")[1],
          str(install_segments(row)))
    check("the CURRENT artifacts yield no problem (the check is not vacuous both ways)",
          artifact_pin_problems(texts, sbom, raw, validated) == [])


# --------------------------------------------------------------------------------------------------- #
# 13) EVERY THIRD-PARTY MODULE THE SHIPPED CODE IMPORTS IS DECLARED (2026-09 DeepInit dependency pass).
#     Sections 1-2 read the dependency set FROM requirements.txt and the installers, so a module the code imports
#     that nobody declared is invisible to them. That is how `anyio` slipped through: v0.28 imports it directly
#     (build/mcp_bounds.py runs each MCP tool body in a worker thread under a deadline; the search server bounds its
#     project-guard roots request with it), but it arrived only through `mcp` and was declared and attributed
#     nowhere. Now every ABSOLUTE import in the shipped code — build/, hooks/, scripts/ (the installers run
#     scripts/ml_stack_selfcheck.py) and benchmark/ (requirements.txt declares "runtime + harness dependencies"),
#     lazy imports inside functions included — must be the stdlib, a module of this repo, a requirements.txt
#     package, or an OPTIONAL_UNDECLARED module every import site of which handles its absence. tests/ is not
#     shipped and is not scanned.
# --------------------------------------------------------------------------------------------------- #
SHIPPED_CODE_DIRS = ("build", "hooks", "scripts", "benchmark")
IMPORT_DIST = {"yaml": "pyyaml", "graphify": "graphifyy"}   # import name -> distribution, where _norm can't map it
# Third-party modules the shipped code may import WITHOUT declaring them, each with the reason. Every import site
# must handle the module's absence; anything not listed here must be declared in requirements.txt.
OPTIONAL_UNDECLARED = (
    (r"tree_sitter(?:_\w+)?", "a tree-sitter binding or grammar: graphifyy's own dependency (graphifyy pins them); "
                             "build/graphify_extract.py skips its supplement for a file whose grammar is not "
                             "importable"),
    (r"packaging", "scripts/ml_stack_selfcheck.py uses packaging's SpecifierSet when importable, else its own "
                   "comparator"),
    (r"anthropic", "the benchmark's opt-in Anthropic-API judge transport (DEEPMAP_JUDGE_TRANSPORT=api): an "
                   "operator-installed SDK whose absence is reported as a clear error"),
)
_ABSENCE_HANDLERS = frozenset({"ImportError", "ModuleNotFoundError", "Exception", "BaseException"})


def _handles_absence(handler: ast.ExceptHandler) -> bool:
    """Does this `except` clause catch an ImportError (bare, ImportError / ModuleNotFoundError, or a superclass)?"""
    if handler.type is None:
        return True
    kinds = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return any((k.id if isinstance(k, ast.Name) else getattr(k, "attr", "")) in _ABSENCE_HANDLERS for k in kinds)


def import_sites(src: str) -> "list[tuple[str, int, bool]]":
    """(top-level module, line, guarded) for every ABSOLUTE import in `src`, module-level or lazy. `guarded` = the
    import sits in the body of a `try` — in the same function — with an `except` that catches ImportError, i.e. the
    code handles the module being absent. A function defined inside a `try` body runs later, unguarded."""
    tries = tuple(t for t in (getattr(ast, "Try", None), getattr(ast, "TryStar", None)) if t is not None)
    sites: list = []

    def walk(node, guarded: bool) -> None:
        if isinstance(node, ast.Import):
            sites.extend((a.name.split(".")[0], node.lineno, guarded) for a in node.names)
            return
        if isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                sites.append((node.module.split(".")[0], node.lineno, guarded))
            return
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            guarded = False
        if isinstance(node, tries):
            inner = guarded or any(_handles_absence(h) for h in node.handlers)
            for stmt in node.body:
                walk(stmt, inner)
            for stmt in [*node.handlers, *node.orelse, *node.finalbody]:
                walk(stmt, guarded)
            return
        for child in ast.iter_child_nodes(node):
            walk(child, guarded)

    walk(ast.parse(src), False)
    return sites


def _local_modules(root: Path) -> set:
    """Names a shipped file may import from THIS repo: the *.py stems of the scanned dirs and tests/ (each script
    puts its sibling dirs on sys.path) and the repo's top-level directory names."""
    names = {p.name for p in root.iterdir() if p.is_dir() and p.name.isidentifier()} if root.is_dir() else set()
    for d in SHIPPED_CODE_DIRS + ("tests",):
        names |= {p.stem for p in (root / d).glob("*.py")}
    return names


def third_party_imports(root: Path) -> "tuple[dict, list]":
    """({top-level module: [(file, line, guarded), ...]}, [files that did not parse]) over the shipped code — the
    flat *.py module dirs run_all py_compiles — skipping __future__, the stdlib and this repo's own modules."""
    std = set(getattr(sys, "stdlib_module_names", ()))
    local = _local_modules(root)
    found: dict = {}
    broken: list = []
    for d in SHIPPED_CODE_DIRS:
        for path in sorted((root / d).glob("*.py")):
            rel = f"{d}/{path.name}"
            try:
                sites = import_sites(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, SyntaxError, ValueError, RecursionError) as exc:
                broken.append(f"{rel}: {type(exc).__name__}")
                continue
            for mod, line, guarded in sites:
                if mod == "__future__" or mod in std or mod in local:
                    continue
                found.setdefault(mod, []).append((rel, line, guarded))
    return found, broken


def _optional_reason(mod: str) -> "str | None":
    return next((why for pat, why in OPTIONAL_UNDECLARED if re.fullmatch(pat, mod)), None)


def import_problem(mod: str, sites: list, declared: set) -> str:
    """Pure: '' when the shipped code may import `mod` the way it does (`sites` = [(file, line, guarded)]), else why
    not. `declared` = the normalized requirements.txt package names."""
    if _norm(IMPORT_DIST.get(mod, mod)) in declared:
        return ""
    why = _optional_reason(mod)
    if why is None:
        where = ", ".join(f"{f}:{n}" for f, n, _ in sites[:6]) + (" ..." if len(sites) > 6 else "")
        return (f"'{mod}' is imported ({where}) but not declared in requirements.txt — declare it and attribute it in "
                "NOTICE / THIRD-PARTY-LICENSES.md / sbom.cdx.json, or make every import optional and list it in "
                "OPTIONAL_UNDECLARED with the reason")
    bare = [f"{f}:{n}" for f, n, guarded in sites if not guarded]
    if bare:
        return (f"'{mod}' is a listed OPTIONAL import ({why}), but {', '.join(bare[:6])} import it without handling "
                "its absence — guard it or declare it")
    return ""


def gate_imports_declared() -> None:
    print("\n-- 13) every third-party module the shipped code imports is DECLARED (or a guarded, listed optional) --")
    check("the stdlib module list is available (sys.stdlib_module_names, Python 3.10+)",
          bool(getattr(sys, "stdlib_module_names", None)))
    found, broken = third_party_imports(REPO)
    check("every shipped *.py file parses", not broken, "; ".join(broken))
    check("the scan sees the shipped code's known third-party imports (neo4j, mcp) — it is not vacuous",
          {"neo4j", "mcp"} <= set(found), str(sorted(found)))
    declared = {_norm(p) for p in _requirements_pkgs()}
    for mod in sorted(found):
        sites = found[mod]
        optional = _norm(IMPORT_DIST.get(mod, mod)) not in declared and _optional_reason(mod) is not None
        label = (f"'{mod}' is a listed optional import and all {len(sites)} site(s) handle its absence" if optional
                 else f"'{mod}' ({len(sites)} import site(s)) is declared in requirements.txt")
        problem = import_problem(mod, sites, declared)
        check(label, not problem, problem)


# --------------------------------------------------------------------------------------------------- #
# 14) A DIRECT IMPORT THAT ALSO ARRIVES THROUGH A PINNED PACKAGE — anyio, through mcp — is declared with a range
#     COMPATIBLE with that package's own requirement, and NOTICE / THIRD-PARTY-LICENSES.md / sbom.cdx.json state
#     it. requirements.txt: `anyio>=4.5`, the floor mcp itself requires (mcp 1.28.1's metadata: anyio>=4.5; the
#     `abandon_on_cancel=` DeepMap passes to to_thread.run_sync is new in anyio 4.1), so declaring it never changes
#     what pip resolves next to mcp; no ceiling, because only the three PINNED_PACKAGES carry measured ranges and
#     anyio has no known breaking release. Both installers install it next to mcp, in mcp's own pip command (locked
#     by tests/f6_installer_declared_specs_r283_gates.py); CI gets it through mcp.
# --------------------------------------------------------------------------------------------------- #
DIRECT_VIA_PINNED = {"anyio": ("mcp", "MIT")}   # declared package -> (the pinned package it arrives through, license)


def declared_specs(text: str) -> dict:
    """{normalized package: normalized specifier ('' when bare)} for every requirement line of a requirements file."""
    out: dict = {}
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        name = re.split(r"[<>=!~;\[\s]", line, maxsplit=1)[0].strip()
        rest = re.sub(r"^\s*\[[^\]]*\]", "", line[len(name):]).split(";", 1)[0]
        out[_norm(name)] = _norm_spec(rest)
    return out


def _raw_requirement(text: str, pkg: str) -> str:
    """The requirement token for `pkg` as requirements.txt writes it, whitespace dropped (e.g. 'anyio>=4.5'); ''."""
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if line and _norm(re.split(r"[<>=!~;\[\s]", line, maxsplit=1)[0]) == pkg:
            return re.sub(r"\s+", "", line)
    return ""


def _floors(spec: str) -> "list[str]":
    return [m.group(2) for c in spec.split(",") if (m := re.match(r"(>=|===|==|~=|>)(.+)", c))]


def specs_compatible(a: str, b: str, also: "tuple[str, ...]" = ()) -> bool:
    """Do the normalized specifier sets `a` and `b` admit a common release? The witnesses tried are each one's floor,
    the `also` releases (e.g. the installed one) and 0 — exact for the >= / < shapes these files and metadata use."""
    return any(spec_allows(a, v) and spec_allows(b, v) for v in [*_floors(a), *_floors(b), *also, "0"])


def requirement_on(dist: str, pkg: str) -> "str | None":
    """The installed `dist`'s own normalized specifier on `pkg` ('' = any version), or None when `dist` is not
    installed or requires `pkg` only under a marker / extra (not compared)."""
    try:
        from importlib import metadata as _md
        reqs = _md.requires(dist) or []
    except Exception:
        return None
    for req in reqs:
        body, _, marker = req.partition(";")
        m = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*\(?([^)]*)\)?\s*$", body)
        if m and _norm(m.group(1)) == _norm(pkg) and not marker.strip():
            return _norm_spec(m.group(2))
    return None


def _installed(pkg: str) -> "str | None":
    try:
        from importlib import metadata as _md
        return _md.version(pkg)
    except Exception:
        return None


def _installed_license(pkg: str) -> "str | None":
    """The installed distribution's declared license text (License-Expression / License / License classifiers)."""
    try:
        from importlib import metadata as _md
        meta = _md.metadata(pkg)
    except Exception:
        return None
    parts = [meta.get("License-Expression") or "", meta.get("License") or ""]
    parts += [c for c in (meta.get_all("Classifier") or []) if c.startswith("License ::")]
    return " | ".join(p for p in parts if p)


def direct_dep_problems(pkg: str, raw: str, lic: str, texts: dict, sbom: dict) -> "list[str]":
    """Pure: what in the compliance artifacts contradicts requirements.txt's declaration `raw` (e.g. 'anyio>=4.5')
    of the directly imported `pkg`, licensed `lic`. `texts` / `sbom` as for artifact_pin_problems. [] = consistent."""
    probs: list = []
    name = re.split(r"[<>=!~;\[\s]", raw, maxsplit=1)[0]
    spec = _norm_spec(raw[len(name):])
    comps = _sbom_pinned_component(sbom, pkg)
    if len(comps) != 1:
        probs.append(f"sbom.cdx.json: {len(comps)} components with purl pkg:pypi/{pkg} (want exactly 1)")
    for c in comps:
        ver = str(c.get("version", ""))
        if not spec or _norm_spec(ver) != spec:
            probs.append(f"sbom.cdx.json: {pkg} version {ver!r} != requirements.txt's {raw[len(name):]!r}")
        ids = [str((entry.get("license") or {}).get("id", "")) for entry in (c.get("licenses") or [])
               if isinstance(entry, dict)]
        if ids != [lic]:
            probs.append(f"sbom.cdx.json: {pkg} license ids {ids} != [{lic!r}]")
    for art in ("NOTICE", "THIRD-PARTY-LICENSES.md"):
        text = texts.get(art, "")
        if raw not in re.sub(r"\s+", "", text):
            probs.append(f"{art}: does not state {pkg}'s declared range {raw!r}")
        if not re.search(rf"(?im)^.*\b{re.escape(pkg)}\b.*\b{re.escape(lic)}\b", text):
            probs.append(f"{art}: no line names {pkg} with its license {lic}")
    return probs


def gate_direct_via_pinned() -> None:
    print("\n-- 14) anyio (imported directly, arrives through mcp): a range COMPATIBLE with mcp's own requirement, "
          "stated in all three artifacts --")
    req = _read("requirements.txt")
    specs = declared_specs(req)
    texts, sbom = _artifact_texts()
    for pkg, (via, lic) in sorted(DIRECT_VIA_PINNED.items()):
        lines = sum(1 for p in _requirements_pkgs() if _norm(p) == pkg)
        spec = specs.get(pkg, "")
        raw = _raw_requirement(req, pkg)
        check(f"requirements.txt declares '{pkg}' on exactly one line", lines == 1, f"{lines} line(s)")
        check(f"'{pkg}' is declared with a floor (the minimum its direct import needs), not bare",
              bool(_floors(spec)), spec or "bare / absent")
        check(f"'{via}', the pinned package '{pkg}' arrives through, is declared too", via in specs)
        probs = direct_dep_problems(pkg, raw, lic, texts, sbom) if raw else [f"no '{pkg}' requirement to compare"]
        for art in ARTIFACTS:
            mine = [p for p in probs if p.startswith(art + ":")]
            check(f"{art}: states '{raw or pkg + '?'}' ({lic})", bool(raw) and not mine, "; ".join(mine))
        theirs = requirement_on(via, pkg)
        installed = _installed(pkg)
        if theirs is None:
            print(f"  [skip] {via} is not installed here (or needs {pkg} only under a marker) — nothing to compare")
        else:
            check(f"'{pkg}{spec}' is compatible with the installed {via} {_installed(via)}'s own '{pkg}{theirs}'",
                  bool(spec) and specs_compatible(spec, theirs, (installed,) if installed else ()))
        if installed is None:
            print(f"  [skip] {pkg} is not installed here — nothing to check")
        else:
            check(f"the installed {pkg} {installed} is inside the declared range {spec!r}",
                  bool(spec) and spec_allows(spec, installed))
            got = _installed_license(pkg) or ""
            check(f"the installed {pkg}'s own metadata names the license the artifacts state ({lic})",
                  bool(re.search(rf"\b{re.escape(lic)}\b", got)), got[:200])


def gate_import_and_range_detectors_have_teeth() -> None:
    print("\n-- 15) mutation: the import scan and the range check catch the pre-fix state and its variants --")
    check("a module-level and a lazy (in-function) import are both found, unguarded",
          import_sites("import zzz_a\ndef f():\n    from zzz_b.sub import x\n")
          == [("zzz_a", 1, False), ("zzz_b", 3, False)], str(import_sites("import zzz_a\n")))
    guarded = ("try:\n    import zzz_a\nexcept ImportError:\n    zzz_a = None\n"
               "try:\n    import zzz_b\nexcept (OSError, Exception):\n    pass\n"
               "try:\n    import zzz_c\nexcept:\n    pass\n")
    check("guarded: a `try` whose except catches ImportError, a superclass in a tuple, or a bare except",
          [g for _, _, g in import_sites(guarded)] == [True, True, True], str(import_sites(guarded)))
    unguarded = ("try:\n    import zzz_a\nexcept ValueError:\n    pass\n"
                 "try:\n    pass\nexcept ImportError:\n    import zzz_b\n"
                 "try:\n    def f():\n        import zzz_c\nexcept ImportError:\n    pass\n")
    check("NOT guarded: an except that can't catch ImportError, an import in the handler, a function defined in the try",
          [g for _, _, g in import_sites(unguarded)] == [False, False, False], str(import_sites(unguarded)))
    check("relative imports (this repo's own modules) are not third-party sites",
          import_sites("from . import x\nfrom .y import z\n") == [])
    found, _ = third_party_imports(REPO)
    declared = {_norm(p) for p in _requirements_pkgs()}
    anyio_sites = found.get("anyio") or [("build/mcp_bounds.py", 1, False)]
    pre_fix = import_problem("anyio", anyio_sites, declared - {"anyio"})
    check("the pre-fix state (anyio imported, requirements.txt without it) is flagged as undeclared",
          "not declared" in pre_fix, pre_fix)
    check("a guarded import of a LISTED optional module passes; the same import unguarded is flagged",
          import_problem("tree_sitter_go", [("build/x.py", 3, True)], declared) == ""
          and "without handling its absence" in import_problem("tree_sitter_go", [("build/x.py", 3, False)], declared))
    check("a guarded import of an UNLISTED module is still flagged (optional needs an explicit, reasoned listing)",
          "not declared" in import_problem("zzz_unlisted", [("build/x.py", 3, True)], declared))
    check("an import name maps to its distribution (yaml -> pyyaml, graphify -> graphifyy)",
          import_problem("yaml", [("benchmark/x.py", 1, False)], {"pyyaml"}) == ""
          and import_problem("graphify", [("build/x.py", 1, False)], {"graphifyy"}) == "")
    check("specs_compatible: >=4.5 vs >=4.5 yes; <4.5 vs >=4.5 no; <5,>=4.5 vs >=4.5 yes; <5,>=4.5 vs >=5 no",
          specs_compatible(">=4.5", ">=4.5") and not specs_compatible("<4.5", ">=4.5")
          and specs_compatible("<5,>=4.5", ">=4.5") and not specs_compatible("<5,>=4.5", ">=5"))
    check("declared_specs reads a bare, a floor-only and a ranged requirement",
          declared_specs("torch  # c\nanyio>=4.5   # c\nmcp>=1.24,<2\n") == {"torch": "", "anyio": ">=4.5",
                                                                           "mcp": "<2,>=1.24"})
    texts, sbom = _artifact_texts()
    raw = _raw_requirement(_read("requirements.txt"), "anyio") or "anyio>=4.5"
    stale = json.loads(json.dumps(sbom))
    for c in _sbom_pinned_component(stale, "anyio"):
        c["version"] = "UNPINNED"
        c["licenses"] = [{"license": {"id": "Apache-2.0"}}]
    got = direct_dep_problems("anyio", raw, "MIT", {}, stale)
    check("an SBOM whose anyio component is missing, UNPINNED or mislicensed is flagged",
          any("components with purl pkg:pypi/anyio" in p or "version 'UNPINNED'" in p for p in got)
          and (not _sbom_pinned_component(stale, "anyio") or any("license ids" in p for p in got)), "; ".join(got[:4]))
    got = direct_dep_problems("anyio", raw, "MIT", {"NOTICE": "* anyio — MIT\n"}, sbom)
    check("a NOTICE that names anyio but not its declared range is flagged",
          any(p.startswith("NOTICE: does not state") for p in got), "; ".join(got[:4]))


# --------------------------------------------------------------------------------------------------- #
# 16) THE FIFTH INSTALL SOURCE: /deep-map:setup's own environment (build/deps_bootstrap.py, 2026-10). When the Python
#     running setup lacks DeepMap's packages, setup pip-installs them into <DEEPMAP_HOME>/venv itself. It must install
#     what the installers install: requirements.txt's exact specs, READ from requirements.txt (no further copy of a
#     range), each pinned package with --upgrade, torch from the installers' PyTorch CPU index. Its pip commands are
#     rendered as text and run through the same detectors as the four sources of section 8.
# --------------------------------------------------------------------------------------------------- #
def gate_setup_bootstrap_install_source() -> None:
    print("\n-- 16) /deep-map:setup's own environment installs exactly requirements.txt (pins, --upgrade, CPU torch) --")
    build = str(REPO / "build")
    sys.path.insert(0, build)
    try:
        import deps_bootstrap as DB
    except Exception as exc:  # noqa: BLE001
        check("build/deps_bootstrap.py imports", False, repr(exc))
        return
    finally:
        if build in sys.path:
            sys.path.remove(build)
    reqs = DB.requirements(REPO / "requirements.txt")
    got = {_norm(n): _norm_spec(s) for n, s in reqs}
    check("its specs are requirements.txt's, read from the file (declared_specs)",
          got == declared_specs(_read("requirements.txt")), f"{got} vs {declared_specs(_read('requirements.txt'))}")
    text = "\n".join(" ".join(argv) for _, argv in DB.install_commands("python", reqs))
    pins, bare = pin_findings(text, "sh")
    ref_pins, _ = pin_findings(_read("requirements.txt"), "requirements")
    check("its pip commands pin neo4j / mcp / graphifyy to requirements.txt's ranges, never bare",
          pins == ref_pins and not bare, f"pins={pins} bare={sorted(bare)}")
    check("...and install every pinned package with --upgrade", upgrade_findings(text, "sh") == [],
          upgrade_findings(text, "sh"))
    check("teeth: the same commands without --upgrade are flagged",
          upgrade_findings(text.replace("--upgrade ", ""), "sh") != [])
    m = re.search(r"--index-url\s+(\S+)\s+torch", _read("install.sh"))
    check("torch comes from the installers' CPU index", m is not None and DB.TORCH_INDEX == m.group(1)
          and f"--index-url {DB.TORCH_INDEX} torch" in text, m.group(1) if m else "install.sh has no torch index")
    src = _read("build/deps_bootstrap.py")
    copies = [raw for raw in req_raw_specs(_read("requirements.txt")).values()
              if raw[len(re.split(r"[<>=!~]", raw, maxsplit=1)[0]):] in src]
    check("build/deps_bootstrap.py holds no copy of a pinned range", not copies, copies)


def main() -> int:
    global REPO
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    if "--root" in sys.argv:
        REPO = Path(sys.argv[sys.argv.index("--root") + 1]).resolve()
    print("=" * 72)
    print(f"DeepMap — license-compliance drift gates (NOTICE/SBOM vs deps; ranx-not-shipped; no spend) (root: {REPO})")
    print("=" * 72)

    gate_attribution_completeness()
    gate_installer_deps_attributed()
    gate_ranx_not_shipped()
    gate_deferred_not_shipped()
    gate_installer_cross_platform_parity()
    gate_installer_parity_has_teeth()
    gate_matcher_has_teeth()
    gate_dependency_pins()
    gate_installed_versions_in_range()
    gate_pin_detectors_have_teeth()
    gate_artifacts_carry_pins()
    gate_artifact_detector_has_teeth()
    gate_imports_declared()
    gate_direct_via_pinned()
    gate_import_and_range_detectors_have_teeth()
    gate_setup_bootstrap_install_source()

    print("\n" + "=" * 72)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
