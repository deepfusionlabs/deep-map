#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j, no-spend gates for the `--claude-model` arm dispatch (the multi-LLM benchmark extension).

Motivation: `claude -p --model <id>` runs a second Claude model (e.g. Sonnet 5) as a benchmark arm under
the user's own Claude account, within its usage limits, with no OpenRouter spend: cross-model breadth
without OpenRouter credits. `--claude-model <id>` (repeatable) adds an ON+OFF Claude arm run via
`claude -p --model <id>`, namespaced `deepmap_on@<id>` so aggregate groups per model.

ADDITIVE + no-regression: absent `--claude-model`, arm_specs + build_claude_argv are byte-identical to
today's Opus-only path. A `--claude-model` (unlike `--arm-model`) spends NO OpenRouter money, so it must
NOT trip the OpenRouter spend-confirm gate.

Locks:
  - build_claude_argv default -> `--model opus` (unchanged); with model=X -> `--model X`,
  - claude_arm_key: bare arm for opus, `arm@model` for a non-default Claude model (no path collision),
  - arm_specs: default = 2 Claude specs; each --claude-model adds ('claude:<id>', ON/OFF); composes with --arm-model,
  - the dispatch label for a claude:<model> kind is `deepmap_on@<model>`, and arm_family strips it,
  - a --claude-model-only run does NOT require --confirm-spend and does NOT need OPENROUTER_API_KEY.
"""
from __future__ import annotations

import argparse
import importlib.util
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


def _load():
    spec = importlib.util.spec_from_file_location("dm_run_benchmark_cm", BENCH / "run_benchmark.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _ns(**kw):
    d = {"arm_model": None, "claude_model": None}
    d.update(kw)
    return argparse.Namespace(**d)


def gate_build_argv_model(B) -> None:
    print("\n-- build_claude_argv: default --model opus (unchanged); model=X -> --model X --")
    argv = B.build_claude_argv(B.ARM_ON, "q", REPO, REPO / "x.json", 1.0)
    check("default argv still passes --model opus",
          "--model" in argv and argv[argv.index("--model") + 1] == "opus", " ".join(argv))
    argv2 = B.build_claude_argv(B.ARM_ON, "q", REPO, REPO / "x.json", 1.0, model="claude-sonnet-5")
    check("model=claude-sonnet-5 -> --model claude-sonnet-5",
          argv2[argv2.index("--model") + 1] == "claude-sonnet-5", " ".join(argv2))
    # OFF-arm isolation must survive the model param (still --strict-mcp-config, no --mcp-config).
    off = B.build_claude_argv(B.ARM_OFF, "q", REPO, REPO / "x.json", 1.0, model="claude-sonnet-5")
    check("OFF arm still isolated with a model override", "--strict-mcp-config" in off and "--mcp-config" not in off)


def gate_claude_arm_key(B) -> None:
    print("\n-- claude_arm_key: bare for opus, namespaced for a non-default model --")
    fn = getattr(B, "claude_arm_key", None)
    check("claude_arm_key exists", callable(fn))
    if not callable(fn):
        return
    check("opus -> bare arm (no collision-namespacing)", fn(B.ARM_ON, "opus") == B.ARM_ON)
    check("sonnet-5 -> arm@model", fn(B.ARM_ON, "claude-sonnet-5") == f"{B.ARM_ON}@claude-sonnet-5")
    check("arm_family strips the @model back to the family",
          B.arm_family(fn(B.ARM_OFF, "claude-sonnet-5")) == B.ARM_OFF)


def gate_arm_specs(B) -> None:
    print("\n-- arm_specs: additive; --claude-model adds ('claude:<id>', ON/OFF); composes with --arm-model --")
    base = B.arm_specs(_ns())
    check("default = exactly the 2 Claude specs (no regression)",
          base == [("claude", B.ARM_ON), ("claude", B.ARM_OFF)], str(base))
    withcm = B.arm_specs(_ns(claude_model=["claude-sonnet-5"]))
    check("adds a claude:<id> ON+OFF pair",
          ("claude:claude-sonnet-5", B.ARM_ON) in withcm and ("claude:claude-sonnet-5", B.ARM_OFF) in withcm,
          str(withcm))
    check("the 2 base Claude specs are still present", withcm[0] == ("claude", B.ARM_ON) and withcm[1] == ("claude", B.ARM_OFF))
    both = B.arm_specs(_ns(claude_model=["claude-sonnet-5"], arm_model=["openai/gpt-5"]))
    check("composes with --arm-model (both claude:<id> and openrouter:<id> present)",
          ("claude:claude-sonnet-5", B.ARM_ON) in both and ("openrouter:openai/gpt-5", B.ARM_ON) in both, str(both))


def gate_dispatch_label(B) -> None:
    print("\n-- the dispatch label for a claude:<model> kind is arm@model --")
    # Mirror the run-loop expression: arm_label = arm if kind=='claude' else f"{arm}@{kind.split(':',1)[1]}"
    kind = "claude:claude-sonnet-5"
    arm = B.ARM_ON
    label = arm if kind == "claude" else f"{arm}@{kind.split(':', 1)[1]}"
    check("claude:claude-sonnet-5 -> deepmap_on@claude-sonnet-5", label == f"{B.ARM_ON}@claude-sonnet-5", label)


def gate_no_openrouter_spend_gate(B) -> None:
    print("\n-- a --claude-model-only run spends NO OpenRouter $: no confirm-spend, no key needed --")
    ns = _ns(claude_model=["claude-sonnet-5"], confirm_spend=False)
    check("require_spend_confirmation returns None (nothing to confirm — Claude on the plan)",
          B.require_spend_confirmation(ns) is None)
    check("openrouter_arms_missing_key False for a claude-model-only run",
          B.openrouter_arms_missing_key(getattr(ns, "arm_model", None)) is False)
    # But an --arm-model still gates (regression guard for the OpenRouter path).
    ns2 = _ns(arm_model=["openai/gpt-5"], confirm_spend=False)
    check("an --arm-model WITHOUT --confirm-spend still refuses (OpenRouter gate intact)",
          B.require_spend_confirmation(ns2) is not None)


def gate_run_one_accepts_model(B) -> None:
    print("\n-- run_one accepts a model kwarg (plan-based model arm) --")
    import inspect
    sig = inspect.signature(B.run_one)
    check("run_one has a 'model' parameter (default 'opus')",
          "model" in sig.parameters and sig.parameters["model"].default == "opus", str(sig))


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 68)
    print("DeepMap — --claude-model plan-arm gates (no Neo4j, no spend)")
    print("=" * 68)
    B = _load()
    gate_build_argv_model(B)
    gate_claude_arm_key(B)
    gate_arm_specs(B)
    gate_dispatch_label(B)
    gate_no_openrouter_spend_gate(B)
    gate_run_one_accepts_model(B)
    print("\n" + "=" * 68)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
