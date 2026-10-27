#!/usr/bin/env python3
"""0.28.3 (O11) — each benchmark arm sees exactly its own tools and none of DeepMap's own steering.

Findings (DeepInit refresh 2026-09-29, validated):
  * ISS-benchmark:007 (D14) — the ON arm passed `--mcp-config <clone pair>` WITHOUT `--strict-mcp-config`, so every
    ambient MCP server loaded next to the clone's pair: user-level servers and the plugin-scoped deepmap-search /
    deepmap-embed on 127.0.0.1:8787/8788, which serve WHICHEVER project owns that pair (often the dev repo). An ON-arm
    answer could come from another project's graph; assert_on_arm_serves_repo checks only the env pair, so it could
    not see this. `--strict-mcp-config` means "only the servers in --mcp-config" — the clone's pair still loads
    (the probe proves it). A gate locked the old argv ("it loads servers"), a misreading of the flag.
  * ISS-benchmark:008 (D15) — every `claude -p` child the harness starts (both arms, the probe, the cli judge) ran
    with the operator's full environment, so DeepMap's default-on plugin hooks fired inside the measurement: the
    SessionStart notify ("prefer the deepmap_search MCP tool over raw grep") and the Grep|Glob nudge — in the OFF
    grep baseline too (every clone carries the .deepmap/graph_build.json marker they key on) — and the SessionStart
    heal, which can restart the clone's MCP servers onto the INSTALLED plugin's code mid-matrix. The children now
    carry every hook opt-out (the master DEEPMAP_HOOKS=0 plus each hook's own switch).

Locks (hermetic: subprocess.run is replaced by a recorder; no claude, no Neo4j, no spend):
  A. build_claude_argv: the ON arm is --strict-mcp-config + --mcp-config <cfg>; ON == OFF + the --mcp-config pair
     (the arms differ ONLY in which servers the config names), also with a --claude-model override; the ON probe
     call carries it too.
  B. run_one (ON + OFF), _probe_call (ON + OFF) and the cli judge pass env= with DEEPMAP_HOOKS=0 and every hook
     opt-out = "0", override an operator's DEEPMAP_HOOKS=1, inherit the rest of the environment, and never mutate
     the harness's own os.environ.
  C. the opt-out list is complete: every `*OPTOUT` switch the hooks declare (read from hooks/*.py) is set, and the
     REAL hooks/deepmap_hook_state.feature_enabled() reads each one as OFF under the child env (ON without it).

Usage: python tests/o11_arm_isolation_r283_gates.py [--root <tree>]
"""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.dont_write_bytecode = True     # never drop __pycache__ into a --root snapshot

REPO = Path(__file__).resolve().parent.parent
PASSES: list[str] = []
FAILS: list[str] = []
SENTINEL = "O11_ARM_ISOLATION_SENTINEL"


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSES if ok else FAILS).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok or not detail else f" — {detail}"))


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def hook_optouts(root: Path) -> set:
    """Every opt-out env switch the DeepMap hooks declare: module-level `*OPTOUT = "DEEPMAP_..."` constants in
    hooks/*.py, plus the master DEEPMAP_HOOKS (read by deepmap_hook_state.feature_enabled)."""
    names = {"DEEPMAP_HOOKS"}
    for py in sorted((root / "hooks").glob("*.py")):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        for node in tree.body:
            if (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                    and node.targets[0].id.endswith("OPTOUT") and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str) and node.value.value.startswith("DEEPMAP_")):
                names.add(node.value.value)
    return names


class _Recorder:
    """A stand-in for run_benchmark's `subprocess` module: records each run(argv, **kwargs) and answers with a
    well-formed claude envelope (a verdict JSON for the judge)."""

    def __init__(self):
        self.calls: list = []

    def run(self, argv, **kwargs):
        self.calls.append((list(argv), dict(kwargs)))
        env = {"result": '{"correctness_pct": 100, "wrong_high": 0}', "total_cost_usd": 0.0, "num_turns": 1,
               "is_error": False, "usage": {"input_tokens": 1, "output_tokens": 1}}
        return SimpleNamespace(stdout=json.dumps(env), stderr="", returncode=0)


def gate_on_arm_strict(B) -> None:
    print("\n-- A. ISS-benchmark:007: the ON arm loads ONLY the servers its --mcp-config names --")
    cfg = Path(tempfile.gettempdir()) / "o11_mcp_on.json"
    on = B.build_claude_argv(B.ARM_ON, "q", REPO, cfg, 5.0)
    off = B.build_claude_argv(B.ARM_OFF, "q", REPO, cfg, 5.0)
    check("ON arm passes --strict-mcp-config (no ambient / plugin-scoped MCP server loads)",
          "--strict-mcp-config" in on, " ".join(on))
    check("ON arm still passes --mcp-config <the clone's config>",
          "--mcp-config" in on and on[on.index("--mcp-config") + 1] == str(cfg), " ".join(on))
    check("OFF arm: --strict-mcp-config and NO --mcp-config (the empty set) — unchanged",
          "--strict-mcp-config" in off and "--mcp-config" not in off, " ".join(off))
    check("the arms differ ONLY in the --mcp-config pair (ON == OFF + --mcp-config <cfg>)",
          on == off + ["--mcp-config", str(cfg)], f"on={on} off={off}")
    on5 = B.build_claude_argv(B.ARM_ON, "q", REPO, cfg, 5.0, model="claude-sonnet-5")
    off5 = B.build_claude_argv(B.ARM_OFF, "q", REPO, cfg, 5.0, model="claude-sonnet-5")
    check("...also for a --claude-model arm (claude-sonnet-5)",
          "--strict-mcp-config" in on5 and on5 == off5 + ["--mcp-config", str(cfg)], " ".join(on5))

    rec = _Recorder()
    saved = B.subprocess
    try:
        B.subprocess = rec
        with tempfile.TemporaryDirectory() as td:
            B._probe_call(B.ARM_ON, "probe", Path(td), cfg, Path(td) / "out", timeout_s=30, max_budget_usd=0.5)
    finally:
        B.subprocess = saved
    argv = rec.calls[-1][0] if rec.calls else []
    check("the ON probe call (what --probe verifies) runs strict too — the probe proves the clone's pair loads",
          "--strict-mcp-config" in argv and "--mcp-config" in argv, " ".join(argv))


def _child_calls(B) -> "list[tuple[str, dict]]":
    """(label, kwargs) of every claude child the harness starts: run_one ON/OFF, _probe_call ON/OFF, the cli judge."""
    rec = _Recorder()
    saved_sub, saved_root = B.subprocess, B.RESULTS_ROOT
    out: list = []
    with tempfile.TemporaryDirectory() as td:
        try:
            B.subprocess = rec
            B.RESULTS_ROOT = Path(td) / "results"
            run_dir = B.RESULTS_ROOT / "o11"
            repo_dir = Path(td) / "clone"
            repo_dir.mkdir()
            cfg = Path(td) / "mcp_on.json"
            q = {"id": "q1", "repo": "o/r", "prompt": "what calls charge()?", "family": "f", "grep_can_do": False}
            for arm in (B.ARM_ON, B.ARM_OFF):
                B.run_one(arm, q, repo_dir, cfg, 1, run_dir, max_budget_usd=1.0, timeout_s=60)
                out.append((f"run_one {arm}", rec.calls[-1][1]))
            for arm in (B.ARM_ON, B.ARM_OFF):
                B._probe_call(arm, "probe", repo_dir, cfg, run_dir, timeout_s=30, max_budget_usd=0.5)
                out.append((f"_probe_call {arm}", rec.calls[-1][1]))
            judge = B._judge_call_factory(model_alias="sonnet", cwd=repo_dir, max_budget_usd=0.5, timeout_s=30)
            judge("grade this")
            out.append(("cli judge", rec.calls[-1][1]))
        finally:
            B.subprocess, B.RESULTS_ROOT = saved_sub, saved_root
    return out


def gate_child_env(B, optouts: set) -> None:
    print("\n-- B. ISS-benchmark:008: every claude child runs with DeepMap's hooks switched off --")
    saved = {k: os.environ.get(k) for k in (SENTINEL, "DEEPMAP_HOOKS")}
    os.environ[SENTINEL] = "kept"
    os.environ["DEEPMAP_HOOKS"] = "1"          # an operator who exported the master switch ON
    try:
        calls = _child_calls(B)
        check("five claude children were started (run_one ON/OFF, probe ON/OFF, cli judge)", len(calls) == 5,
              str([c[0] for c in calls]))
        for label, kw in calls:
            env = kw.get("env")
            check(f"{label}: passes an explicit env=", isinstance(env, dict), f"env={type(env).__name__}")
            if not isinstance(env, dict):
                continue
            off = sorted(n for n in optouts if str(env.get(n, "")).strip().lower() not in ("0", "false", "no", "off"))
            check(f"{label}: every hook opt-out is OFF ({', '.join(sorted(optouts))})", not off, f"still on: {off}")
            check(f"{label}: the operator's DEEPMAP_HOOKS=1 is overridden to 0", env.get("DEEPMAP_HOOKS") == "0",
                  str(env.get("DEEPMAP_HOOKS")))
            check(f"{label}: the rest of the environment is inherited (PATH + an unrelated variable)",
                  env.get(SENTINEL) == "kept" and bool(env.get("PATH") or env.get("Path")), "")
        check("the harness's own os.environ is not mutated (still DEEPMAP_HOOKS=1)",
              os.environ.get("DEEPMAP_HOOKS") == "1", str(os.environ.get("DEEPMAP_HOOKS")))
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def gate_optouts_complete(B, root: Path, optouts: set) -> None:
    print("\n-- C. the opt-out list covers every hook switch, and the REAL hook helper reads them as OFF --")
    check("the hooks declare the expected switches (DEEPMAP_HOOKS/HEAL/ANNOUNCE/ONBOARD/NUDGE/SYNC_FLAG)",
          {"DEEPMAP_HOOKS", "DEEPMAP_HEAL", "DEEPMAP_ANNOUNCE", "DEEPMAP_ONBOARD", "DEEPMAP_NUDGE",
           "DEEPMAP_SYNC_FLAG"} <= optouts, str(sorted(optouts)))
    child_env_fn = getattr(B, "claude_child_env", None)
    check("run_benchmark.claude_child_env exists (the one env every claude child gets)", callable(child_env_fn))
    if not callable(child_env_fn):
        return
    env = child_env_fn({"PATH": "x"})
    missing = sorted(n for n in optouts if env.get(n) != "0")
    check("claude_child_env sets EVERY hook opt-out the hooks declare to '0'", not missing, f"missing: {missing}")
    HS = _load(root / "hooks" / "deepmap_hook_state.py", "o11_hook_state")
    keys = sorted(optouts)
    saved = {k: os.environ.get(k) for k in keys}
    try:
        for k in keys:
            os.environ.pop(k, None)
        on_before = {k: HS.feature_enabled(k) for k in keys}
        for k in keys:
            os.environ[k] = env[k]
        on_child = {k: HS.feature_enabled(k) for k in keys}
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    check("teeth: without the opt-outs every hook feature is ON (the check can fail)", all(on_before.values()),
          str(on_before))
    check("under the child env deepmap_hook_state.feature_enabled() is OFF for every switch",
          not any(on_child.values()), str(on_child))


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
    print(f"DeepMap — benchmark arm isolation: strict MCP on both arms, no DeepMap hooks in any claude child (root: {root})")
    print("=" * 72)
    B = _load(root / "benchmark" / "run_benchmark.py", "o11_run_benchmark_iso")
    optouts = hook_optouts(root)
    gate_on_arm_strict(B)
    gate_child_env(B, optouts)
    gate_optouts_complete(B, root, optouts)

    total = len(PASSES) + len(FAILS)
    print("\n" + "=" * 72)
    if FAILS:
        print(f"RESULT: RED — {len(PASSES)}/{total} gates passed.")
        return 1
    print(f"RESULT: GREEN — {len(PASSES)}/{total} gates passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
