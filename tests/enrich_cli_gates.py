#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gates for the `deepmap enrich` CLI — drives the real CLI as a subprocess (no Neo4j, no spend).

Proves the spend-safe surface: --status / --dry-run / --deterministic spend nothing and send nothing;
the LLM path REFUSES without the opt-in flag AND without --confirm-spend (so a bare invocation can never
spend). DEEPMAP_EXTRACTOR=ast for deterministic Python-only enumeration.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLI = ROOT / "build" / "deepmap_cli.py"
FAILS = []


def check(cond, msg):
    print(f"  [{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        FAILS.append(msg)


def _run(root, args, extra_env=None):
    env = dict(os.environ, DEEPMAP_EXTRACTOR="ast", PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    env.update(extra_env or {})
    return subprocess.run([sys.executable, str(CLI), "enrich", str(root)] + args,
                          capture_output=True, text=True, env=env, timeout=120)


def main():
    print("=" * 72)
    print("enrich_cli_gates — `deepmap enrich` CLI surface (subprocess; no Neo4j/spend)")
    print("=" * 72)
    with tempfile.TemporaryDirectory() as d:
        # A WHY comment = a real local signal, so --deterministic genuinely improves the summary (a
        # signal-free function is reported 'unchanged' and NOT written — the byte-identical-summary fix).
        (Path(d) / "a.py").write_text("def charge(card):\n    # WHY: settles the payment first\n"
                                      "    return create(card)\n", encoding="utf-8")

        r = _run(d, ["--status"])
        check(r.returncode == 0 and "opt-in enabled: False" in r.stdout, "--status: exit 0, reports opt-in OFF")

        r = _run(d, ["--dry-run"])
        check(r.returncode == 0 and "candidates: 1" in r.stdout and "charge" in r.stdout,
              "--dry-run: lists candidates, exit 0")
        check(not (Path(d) / ".deepmap" / "summaries.jsonl").exists(), "--dry-run: writes NO sidecar (no spend)")

        r = _run(d, [])
        check(r.returncode == 2 and "opt-in is OFF" in r.stderr, "LLM path: REFUSES without DEEPMAP_ENRICH")

        r = _run(d, [], {"DEEPMAP_ENRICH": "1"})
        check(r.returncode == 2 and "--confirm-spend" in r.stderr,
              "LLM path: REFUSES without --confirm-spend even when opted in")

        r = _run(d, ["--deterministic"])
        check(r.returncode == 0 and "no spend" in r.stdout and "enriched: 1" in r.stdout,
              "--deterministic: runs, exit 0, no spend")
        check((Path(d) / ".deepmap" / "summaries.jsonl").exists(), "--deterministic: writes the sidecar (local only)")

    print("\n" + "=" * 72)
    print("RESULT:", "GREEN" if not FAILS else f"RED — {len(FAILS)}: {FAILS}")
    return 0 if not FAILS else 1


if __name__ == "__main__":
    raise SystemExit(main())
