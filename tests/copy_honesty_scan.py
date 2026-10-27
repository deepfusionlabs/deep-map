#!/usr/bin/env python3
"""DeepMap copy-honesty + version-consistency scanner (no Neo4j; CI-friendly).

Mechanically enforces the STAGE-3 honesty constraints (FD-7, plan §1.5) and the
single-version reconciliation (FD-4 / T16) on the PUBLIC COPY surfaces. It is the
in-session "zero-cut-phrases copy scan" + "version-consistency scan" gate the test
plan §6.3 calls for, made runnable.

Two jobs:
  1. CUT-PHRASE scan  — fail if any claim DeepMap does not back appears in copy
     (self-improving search, runtime learning, auto-optimize after N queries,
     exports fine-tuning data, code-trained/code-aware embeddings, bare
     "commercial-safe", ...). T12/T13/T14.
  2. VERSION-CONSISTENCY scan — one coherent version across plugin.json,
     marketplace.json, the MCP server version strings, and the README. T16.

The public-copy files (landing.html, README.md, SKILL.md, commands/*.md, the
manifests, the MCP servers) are NOT in this planning package yet. So this scanner:
  * reports each target file as PRESENT / ABSENT (absent is honest, not a pass);
  * runs a --selftest with embedded GOOD/BAD fixtures that PROVES it fires on a
    cut phrase and stays quiet on clean copy (load-bearing, not decorative) —
    this works with zero target files present.

It changes NO product code; it only reads copy files.

Usage:
    python tests/copy_honesty_scan.py            # scan real targets (in repo root)
    python tests/copy_honesty_scan.py --selftest # prove the scanner is load-bearing
    python tests/copy_honesty_scan.py --root <dir> --files a.html b.md ...
Exit: 0 = clean (or only ABSENT targets); 1 = a cut phrase found / version drift;
      0 = --selftest behaved correctly (fixtures fired as expected), else 1.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# --- the CUT phrases (FD-7 / plan §1.5.1-1.5.4). Each: (regex, why, task) -------
# Case-insensitive. Kept deliberately specific so the TRUE adjacent claims survive
# (e.g. "graph-weighted ranking" must NOT match — see GUARD_KEEP below).
CUT_PATTERNS = [
    (r"self[\s-]*improving\s+search", "not a DeepMap capability: no weight optimization in source", "T12"),
    (r"runtime\s+learning", "not a DeepMap capability: no learning subsystem", "T12"),
    (r"auto[\s-]*optimi[sz]e\s+(fusion\s+)?weights", "not a DeepMap capability: weights are fixed equal", "T12"),
    (r"(optimi[sz]e|learn).{0,20}\bafter\s+\d+\s+queries", "not a DeepMap capability: 'after N queries' learning",
     "T12"),
    (r"exports?\s+fine[\s-]*tuning\s+data", "not a DeepMap capability, and implies egress (contradicts 100%-local)",
     "T12"),
    (r"logs\s+every\s+(interaction|query)", "not a DeepMap capability: zero telemetry/logging in source", "T12"),
    (r"search[\s-]*quality\s+monitoring", "not a DeepMap capability: no health reports", "T12"),
    (r"health\s+reports?", "not a DeepMap capability: no monitoring subsystem", "T12"),
    (r"code[\s-]*trained\s+embeddings?", "false: ModernBERT is a general-text model", "T13"),
    (r"code[\s-]*aware\s+embeddings?", "false: general-text model; reframe to intent-summary", "T13"),
    (r"runtime\s+learning:\s*yes", "not a DeepMap capability (capability-matrix cell)", "T12"),
]

# A bare "commercial-safe" claim is only OK with the precise Neo4j/GPLv3 wording
# nearby; flag a bare/standalone occurrence (T14 / DD-12).
COMMERCIAL_SAFE = re.compile(r"commercial[\s-]*safe", re.IGNORECASE)
PRECISE_NEARBY = re.compile(r"neo4j|gplv3|gpl-3", re.IGNORECASE)

# An unqualified egress / air-gap / "nothing leaves" / "offline — no LLM" / "sends nothing to an LLM"
# claim is honest only WHILE the opt-in LLM enrichment tier stays off — once it EXISTS as an option
# (build/enrich.py), such a claim needs a "by default" / "opt-in" qualifier within +-3 lines. This is
# the CI tripwire that stops a future copy edit from silently re-introducing an unqualified
# "nothing leaves your machine". Deliberately NOT triggered by no-cloud / no-telemetry / no-subscription
# / no-API-key claims — those stay literally TRUE (enrichment uses the user's EXISTING Claude session;
# no new service, no telemetry). (T-enrich.)
EGRESS_CLAIM = re.compile(
    r"zero[\s-]*egress"
    r"|nothing\s+(?:\w+\s+){0,4}(?:leaves|is\s+sent|ever\s+leaves)"
    r"|never\s+leaves\s+(?:the\s+|your\s+)?machine"
    r"|air[\s-]*gap(?:\s*(?:capable|ped))?"
    r"|offline\s*[—:\-]?\s*no\s+llm"
    r"|sends?\s+nothing\s+to\s+(?:any\s+)?(?:external\s+)?(?:llm|model|service)",
    re.IGNORECASE)
EGRESS_QUALIFIER = re.compile(
    r"by[\s-]?default|opt[\s-]*in|unless you|when you (?:opt|enable|turn|configure)"
    r"|existing\s+claude|\bdefaults?\b",
    re.IGNORECASE)

# GUARD — these TRUE claims must NOT be cut. We don't fail on them; we just make
# sure no CUT pattern is so broad it would also strike them (a self-check).
GUARD_KEEP = [
    "graph-weighted ranking",
    "graph-weighted semantic ranking",
    "graph-weighted vector ranking",
]

# Default public-copy targets (relative to --root) — the REAL shipped paths. (`landing.html` is the
# future marketing page, not in this repo; the MCP servers live under build/, the skill under skills/.)
DEFAULT_TARGETS = [
    "README.md", "skills/deep-map/SKILL.md",
    "commands/setup.md", "commands/refresh.md", "commands/status.md",
    "commands/doctor.md", "commands/impact.md",
    "commands/version.md", "commands/plugin-update.md",
    ".claude-plugin/plugin.json", ".claude-plugin/marketplace.json",
    "build/deepmap_search_server.py", "build/deepmap_embed_server.py",
    "docs/product-page/index.html",     # the landing page — scan it so overclaims are caught
]

# Targets kept only in the private development repository (the product page belongs to the website, not the
# public repository). Scanned wherever they exist; where absent they are SKIPPED with a [skip] line instead of
# being listed as ABSENT.
PRIVATE_ONLY_TARGETS = frozenset({"docs/product-page/index.html"})

# Language-count honesty (T13 / Track A+C). The tree-sitter engine SHIPS grammars for ~25 languages,
# but only VALIDATED_LANG_COUNT are validated end-to-end with committed gates (tests/
# graphify_multilang_gates.py). A bare "N languages" claim with N != 8 is an overclaim UNLESS the
# "validated ... on 8" qualifier is nearby — so "ships grammars for 25 languages ... validated ... on 8"
# is honest and passes, while a stray hero "25 languages" is flagged. ("N langs" is intentionally not
# matched — the abbreviated "25 langs, 8 validated" form already carries its own qualifier.)
VALIDATED_LANG_COUNT = 8
LANG_COUNT_RE = re.compile(r"\b(\d+)\s+languages?\b", re.IGNORECASE)
# The qualifier is a "validated / gated ... 8" (or "8 ... validated") pairing near the claim. The `8`
# is matched bounded by non-word chars so markdown/HTML emphasis (**8**, <b>8</b>, "on 8 —") all count.
LANG_VALIDATED_QUALIFIER = re.compile(
    r"(?:validat|gate)[a-z]*[\s\S]{0,110}?[^\w]8[^\w]"
    r"|[^\w]8[^\w][\s\S]{0,60}?(?:validat|gate)[a-z]*",
    re.IGNORECASE)

# Retrieval-claim honesty (Rails field-review). Typed-graph EXTRACTION is gated on 8 languages, but the
# 3-arm hybrid RETRIEVAL is only proven end-to-end on RETRIEVAL_PROVEN_LANG_COUNT (Python/TS/Go/Rust
# — the "Retrieval-proven ✅" column of docs/multi-language-support.md; it said 5 with JavaScript until
# 2026-10-22, but no recorded run proves search on JavaScript). A claim that couples a
# retrieval capability with a "validated/proven ... 8" (i.e. "hybrid search ... validated ... on 8") is
# an overclaim: extraction ≠ retrieval. It's honest ONLY when the true retrieval count (4) is stated
# near the retrieval term, so the two legs are distinguished. The LANG_COUNT rule above (integer 8) can
# never catch this — the number in the overclaim IS 8; the defect is attaching it to *retrieval*.
RETRIEVAL_PROVEN_LANG_COUNT = 4
RETRIEVAL_CLAIM_TERM = re.compile(
    r"hybrid\s+(?:vector\s+)?search|hybrid\s+retrieval|3[\s-]*arm\s+(?:hybrid\s+)?(?:search|retrieval)",
    re.IGNORECASE)
# "validated/proven/gated ... 8" within reach of the retrieval term (the extraction count mis-applied).
RETRIEVAL_VALIDATED_ON_8 = re.compile(
    r"(?:validat|proven|gate)[a-z]*[\s\S]{0,90}?[^\w]8[^\w]"
    r"|[^\w]8[^\w][\s\S]{0,90}?(?:validat|proven|gate)[a-z]*",
    re.IGNORECASE)
# the true retrieval count stated nearby -> the claim is properly qualified.
RETRIEVAL_PROVEN_QUALIFIER = re.compile(rf"[^\w]{RETRIEVAL_PROVEN_LANG_COUNT}[^\w]")

VERSION_RE = re.compile(r'"?version"?\s*[:=]\s*["\']?v?(\d+\.\d+\.\d+)', re.IGNORECASE)


def utf8_stdout():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass


def scan_text(name, text):
    """Return list of (line_no, phrase, why, task) cut-phrase hits in `text`."""
    hits = []
    lines = text.splitlines()
    for i, line in enumerate(lines, 1):
        for pat, why, task in CUT_PATTERNS:
            m = re.search(pat, line, re.IGNORECASE)
            if m:
                hits.append((i, m.group(0), why, task))
        if COMMERCIAL_SAFE.search(line):
            # only a hit if the precise wording is not within +-2 lines
            ctx = "\n".join(lines[max(0, i - 3): i + 2])
            if not PRECISE_NEARBY.search(ctx):
                hits.append((i, "commercial-safe (bare)",
                             "needs the precise Neo4j/GPLv3 paragraph (DD-12)", "T14"))
        if EGRESS_CLAIM.search(line):
            # only a hit if a 'by default'/'opt-in' qualifier is not within +-3 lines
            ctx = "\n".join(lines[max(0, i - 4): i + 3])
            if not EGRESS_QUALIFIER.search(ctx):
                hits.append((i, "zero-egress (unqualified)",
                             "bare egress/air-gap claim needs a 'by default'/'opt-in' qualifier within "
                             "+-3 lines (opt-in LLM enrichment now exists)", "T-enrich"))
        for m in LANG_COUNT_RE.finditer(line):
            n = int(m.group(1))
            if n != VALIDATED_LANG_COUNT:
                ctx = "\n".join(lines[max(0, i - 3): i + 2])
                if not LANG_VALIDATED_QUALIFIER.search(ctx):
                    hits.append((i, f"{n} languages (unqualified)",
                                 f"only {VALIDATED_LANG_COUNT} languages are validated end-to-end with "
                                 f"gates; a bare '{n} languages' overclaims — pair with 'validated ... "
                                 f"on {VALIDATED_LANG_COUNT}'", "T13"))
        if RETRIEVAL_CLAIM_TERM.search(line):
            # a retrieval capability coupled with "validated ... 8" (the EXTRACTION count) is honest only
            # if the true retrieval-proven count is stated within +-3 lines to distinguish the legs.
            ctx = "\n".join(lines[max(0, i - 3): i + 3])
            if RETRIEVAL_VALIDATED_ON_8.search(ctx) and not RETRIEVAL_PROVEN_QUALIFIER.search(ctx):
                hits.append((i, "hybrid retrieval 'validated on 8' (retrieval overclaim)",
                             f"typed-graph EXTRACTION is gated on {VALIDATED_LANG_COUNT} but 3-arm hybrid "
                             f"RETRIEVAL is proven on only {RETRIEVAL_PROVEN_LANG_COUNT} "
                             f"(Python/TS/Go/Rust) — state the retrieval count separately "
                             f"(docs/multi-language-support.md is the source of truth)", "T13"))
    return hits


def guard_selfcheck():
    """Ensure no CUT pattern accidentally strikes a TRUE keep-claim."""
    bad = []
    for keep in GUARD_KEEP:
        for pat, _why, _task in CUT_PATTERNS:
            if re.search(pat, keep, re.IGNORECASE):
                bad.append((keep, pat))
    return bad


def scan_files(root, targets):
    present, absent, all_hits = [], [], {}
    for rel in targets:
        p = root / rel
        if not p.exists():
            if rel in PRIVATE_ONLY_TARGETS:
                print(f"  [skip] {rel}: not in this checkout (kept in the private repository, not shipped "
                      "publicly); scanned wherever it exists")
                continue
            absent.append(rel)
            continue
        present.append(rel)
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            all_hits[rel] = [(-1, f"<unreadable: {exc}>", "", "")]
            continue
        h = scan_text(rel, text)
        if h:
            all_hits[rel] = h
    return present, absent, all_hits


def version_consistency(root, targets):
    """Collect version strings from manifest/version-bearing targets that exist."""
    versions = {}
    for rel in targets:
        p = root / rel
        if not p.exists():
            continue
        if not (rel.endswith(".json") or rel.endswith("_server.py") or rel == "README.md"):
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        m = VERSION_RE.search(text)
        if m:
            versions[rel] = m.group(1)
    return versions


# ----------------------------- self-test ------------------------------------
GOOD_FIXTURE = """
DeepMap reasons over a real graph with graph-weighted semantic ranking and
confidence on every edge. Search by what your code does, not how it's spelled.
A tree-sitter engine ships grammars for 25 languages; the typed graph is
validated end-to-end, with committed gates, on 8 — Python, JS, TS, Go, Rust, Java, Ruby, C#.
DeepMap is MIT-licensed; it runs on a local Neo4j Community Edition (GPLv3) it
fetches but does not redistribute. Commercial-safe? Review Neo4j's terms.
Zero-egress by default; optional enrichment uses your existing Claude session (opt-in).
"""
BAD_FIXTURE = """
DeepMap features self-improving search that logs every interaction and will
auto-optimize fusion weights after 50 queries, plus runtime learning and it
exports fine-tuning data. Powered by code-trained embeddings. MIT, commercial-safe.
Framework-aware across 25 languages, every runtime edge discovered.
Zero-egress: nothing ever leaves your machine, air-gap capable, always.
"""

# The retrieval-overclaim rule (Rails field-review): coupling hybrid RETRIEVAL with the extraction
# count (8) is dishonest; stating the true retrieval count (4) is honest. Kept apart from GOOD/BAD so
# the language-count qualifiers don't interact.
RETRIEVAL_OVERCLAIM_FIXTURE = """
DeepMap's typed graph and 3-arm hybrid search are validated end-to-end,
with committed automated gates, on 8 languages.
"""
RETRIEVAL_HONEST_FIXTURE = """
DeepMap's typed-graph extraction is validated end-to-end, with committed gates, on 8 languages;
its 3-arm hybrid retrieval is additionally proven end-to-end on 4 of them (Python, TS, Go, Rust).
"""


def selftest():
    ok = True
    # 1) the GUARD: no cut pattern strikes a TRUE keep-claim
    bad = guard_selfcheck()
    if bad:
        ok = False
        print(f"  [FAIL] guard: a CUT pattern strikes a TRUE keep-claim: {bad}")
    else:
        print("  [PASS] guard: no CUT pattern strikes the true graph-weighted claim")

    # 2) GOOD fixture must be CLEAN (must_not_fire)
    g = scan_text("GOOD", GOOD_FIXTURE)
    if g:
        ok = False
        print(f"  [FAIL] GOOD fixture wrongly flagged: {g}")
    else:
        print("  [PASS] GOOD fixture is clean (true claims + properly-qualified commercial-safe)")

    # 3) BAD fixture must fire on EVERY family (must_fire)
    b = scan_text("BAD", BAD_FIXTURE)
    fired = {task for _, _, _, task in b}
    phrases = {p.lower() for _, p, _, _ in b}
    need = ["self-improving", "logs every", "auto-optimi", "runtime learning",
            "fine-tuning data", "code-trained", "commercial-safe (bare)",
            "zero-egress (unqualified)", "languages (unqualified)"]
    missing = [n for n in need if not any(n in ph for ph in phrases)]
    if missing:
        ok = False
        print(f"  [FAIL] BAD fixture: scanner MISSED cut phrases: {missing}")
    else:
        print(f"  [PASS] BAD fixture: all {len(b)} cut phrases flagged (tasks {sorted(fired)})")

    # 4) RETRIEVAL overclaim must fire (must_fire); the honest split must stay clean (must_not_fire).
    ro = scan_text("RETRIEVAL_OVERCLAIM", RETRIEVAL_OVERCLAIM_FIXTURE)
    if any("retrieval overclaim" in p.lower() for _, p, _, _ in ro):
        print("  [PASS] retrieval overclaim ('hybrid search ... validated on 8') is flagged")
    else:
        ok = False
        print(f"  [FAIL] retrieval overclaim NOT flagged: {ro}")
    rh = scan_text("RETRIEVAL_HONEST", RETRIEVAL_HONEST_FIXTURE)
    if any("retrieval overclaim" in p.lower() for _, p, _, _ in rh):
        ok = False
        print(f"  [FAIL] honest retrieval split wrongly flagged: {rh}")
    else:
        print("  [PASS] honest retrieval split (extraction on 8; retrieval on 4) is clean")

    print("\nRESULT:", "GREEN — scanner is load-bearing" if ok else "RED — scanner defect")
    return 0 if ok else 1


def main():
    utf8_stdout()
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--files", nargs="*", default=None)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    print("=" * 72)
    print("DeepMap copy-honesty + version-consistency scanner")
    print("=" * 72)

    if args.selftest:
        print("SELFTEST (embedded GOOD/BAD fixtures; no target files needed):")
        return selftest()

    root = Path(args.root).resolve()
    targets = args.files or DEFAULT_TARGETS
    present, absent, hits = scan_files(root, targets)

    print(f"root: {root}")
    print(f"PRESENT ({len(present)}): {present or '(none)'}")
    print(f"ABSENT  ({len(absent)}): {absent or '(none)'}")
    if absent:
        print("  note: ABSENT public-copy files are not yet in this package — scan them"
              " when STAGE 3 (T12-T16) lands; ABSENT is not a PASS.")

    rc = 0
    if hits:
        rc = 1
        print("\nCUT-PHRASE HITS (honesty violations -> fix in copy):")
        for rel, hs in hits.items():
            for line_no, phrase, why, task in hs:
                print(f"  {rel}:{line_no}  [{task}] {phrase!r} -- {why}")
    else:
        print("\nCUT-PHRASE: no violations in PRESENT files.")

    versions = version_consistency(root, targets)
    if versions:
        distinct = set(versions.values())
        print(f"\nVERSION strings found: {versions}")
        if len(distinct) > 1:
            rc = 1
            print(f"  [FAIL] version DRIFT (T16): {distinct} — reconcile to ONE coherent version.")
        else:
            print(f"  [PASS] one coherent version: {distinct.pop()}")
    else:
        print("\nVERSION: no version-bearing files present to check (T16 deferred).")

    print("\nRESULT:", "CLEAN" if rc == 0 else "VIOLATIONS FOUND")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
