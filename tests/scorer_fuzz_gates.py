#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j, no-spend FUZZ gates for the two benchmark parsers that consume semi-arbitrary input.

The deterministic completeness matcher (`score_completeness`) parses curator ground-truth atoms +
agent answers; the blind-judge parser (`parse_judge_verdict`) parses an LLM's free-form reply. Both
must NEVER crash and must be DETERMINISTIC on any input — a crash would abort a paid scoring run, and
nondeterminism would make the HERO metric irreproducible. Hand-written edge cases (the other gates)
cover the KNOWN shapes; this fuzzes a large SEEDED set of adversarial inputs (regex metacharacters,
unicode, BOMs, deeply-nested/unbalanced braces, pipes, `@`, `->`, backslashes, control chars) to catch
the UNKNOWN ones. Seeded RNG ⇒ reproducible; pure ⇒ no Neo4j, no model, no spend.
"""
from __future__ import annotations

import importlib.util
import random
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BENCH = REPO / "benchmark"

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


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"dm_{name}", BENCH / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# Adversarial alphabet: every metacharacter the parsers special-case, plus unicode/BOM/control chars.
_POOL = "abcABC._:/#L|@-> ()[]{}\\01239\"'\t\n\r﻿é中—"
_SEED = 20260629   # fixed -> reproducible
_N = 3000


def _rand_str(rnd: random.Random, lo: int, hi: int) -> str:
    return "".join(rnd.choice(_POOL) for _ in range(rnd.randint(lo, hi)))


def gate_completeness_fuzz() -> None:
    print(f"\n-- score_completeness: {_N} seeded adversarial inputs never crash + are deterministic --")
    S = _load("score_completeness")
    rnd = random.Random(_SEED)
    crashes = 0
    nondet = 0
    bad_pct = 0
    for _ in range(_N):
        answer = _rand_str(rnd, 0, 50)
        gts = [_rand_str(rnd, 0, 14) for _ in range(rnd.randint(0, 5))]
        try:
            r1 = S.score_completeness(answer, gts)
            r2 = S.score_completeness(answer, gts)
        except Exception as exc:  # noqa: BLE001
            crashes += 1
            if crashes <= 3:
                print(f"     CRASH: {type(exc).__name__} on answer={answer!r} gts={gts!r}")
            continue
        if r1 != r2:
            nondet += 1
        # completeness_pct must always be a sane percentage in [0,100].
        p = r1.get("completeness_pct")
        if not (isinstance(p, (int, float)) and 0.0 <= p <= 100.0):
            bad_pct += 1
    check(f"no crash across {_N} fuzzed inputs", crashes == 0, f"{crashes} crashes")
    check("deterministic (same input -> same score, twice)", nondet == 0, f"{nondet} nondeterministic")
    check("completeness_pct always in [0,100]", bad_pct == 0, f"{bad_pct} out-of-range")


def gate_judge_parser_fuzz() -> None:
    print(f"\n-- parse_judge_verdict: {_N} seeded adversarial replies never crash + are deterministic --")
    C = _load("score_correctness")
    rnd = random.Random(_SEED + 1)
    crashes = 0
    nondet = 0
    bad_shape = 0
    # Mix free-form noise with occasional JSON-ish fragments so the brace-scanner is exercised hard.
    jsonish = ['{"correctness_pct":', '"wrong_high":', "```json", "}", "{", "[]", "null", "true"]
    for _ in range(_N):
        parts = []
        for _ in range(rnd.randint(0, 6)):
            parts.append(_rand_str(rnd, 0, 12) if rnd.random() < 0.6 else rnd.choice(jsonish))
        reply = " ".join(parts)
        try:
            v1 = C.parse_judge_verdict(reply)
            v2 = C.parse_judge_verdict(reply)
        except Exception as exc:  # noqa: BLE001
            crashes += 1
            if crashes <= 3:
                print(f"     CRASH: {type(exc).__name__} on reply={reply!r}")
            continue
        if v1 != v2:
            nondet += 1
        # The verdict must ALWAYS expose correctness_pct + wrong_high keys (None when unparseable).
        if "correctness_pct" not in v1 or "wrong_high" not in v1:
            bad_shape += 1
        cp = v1.get("correctness_pct")
        if cp is not None and not (0.0 <= cp <= 100.0):
            bad_shape += 1
    check(f"no crash across {_N} fuzzed replies", crashes == 0, f"{crashes} crashes")
    check("deterministic (same reply -> same verdict, twice)", nondet == 0, f"{nondet} nondeterministic")
    check("verdict shape always {correctness_pct, wrong_high}; pct None or in [0,100]",
          bad_shape == 0, f"{bad_shape} bad-shape")


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 64)
    print("DeepMap — benchmark scorer/judge fuzz gates (seeded, no spend)")
    print("=" * 64)

    gate_completeness_fuzz()
    gate_judge_parser_fuzz()

    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
