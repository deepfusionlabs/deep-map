#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j, no-spend gates: every `claude`/external subprocess decodes as UTF-8, not the OS codepage.

On Windows the default `text=True` decode uses the locale codepage (cp1252/cp1255/…), so a child's
UTF-8 output (a `claude --output-format json` answer, a graph node name, a git path) with ANY non-ASCII
byte raises UnicodeDecodeError mid-run. The benchmark's claude subprocesses (capture run, probe, blind
judge) and the provisioner's MCP smoke-test subprocess must pin `encoding="utf-8", errors="replace"`
(mirroring `_terminate_pid`, which already does). These gates exercise the REAL functions with a
monkeypatched `subprocess.run` that records the kwargs — a behavioral assertion on the actual call,
not a source-substring grep — so the decode policy can't silently regress.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BENCH = REPO / "benchmark"
BUILD = REPO / "build"

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


def _load(name, base):
    spec = importlib.util.spec_from_file_location(name, base / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class _FakeProc:
    def __init__(self):
        self.stdout = '{"result": "ok", "total_cost_usd": 0.1, "num_turns": 1, "is_error": false}'
        self.stderr = ""
        self.returncode = 0


def _recorder(calls):
    def fake_run(argv, **kwargs):
        calls.append(kwargs)
        return _FakeProc()
    return fake_run


def gate_benchmark_subprocesses() -> None:
    print("\n-- run_benchmark: run_one / _probe_call / judge subprocess decode as UTF-8 --")
    import tempfile
    B = _load("run_benchmark", BENCH)
    calls: list = []
    B.subprocess.run = _recorder(calls)

    q = {"id": "q1", "repo": "owner/repo", "prompt": "p", "family": "f", "grep_can_do": False}
    with tempfile.TemporaryDirectory() as td:
        out_dir = Path(td)
        B.RESULTS_ROOT = out_dir
        repo_dir = out_dir / "clone"
        repo_dir.mkdir()
        cfg = out_dir / "mcp.json"
        cfg.write_text("{}", encoding="utf-8")
        # run_one (the capture subprocess)
        calls.clear()
        B.run_one(B.ARM_OFF, q, repo_dir, cfg, 1, out_dir, max_budget_usd=1.0, timeout_s=60)
        check("run_one's claude subprocess pins encoding=utf-8 + errors=replace",
              calls and calls[-1].get("encoding") == "utf-8" and calls[-1].get("errors") == "replace",
              str(calls[-1] if calls else None))

        # _probe_call (the probe subprocess)
        calls.clear()
        B._probe_call(B.ARM_OFF, "probe", repo_dir, cfg, out_dir, timeout_s=30, max_budget_usd=0.5)
        check("_probe_call's claude subprocess pins encoding=utf-8 + errors=replace",
              calls and calls[-1].get("encoding") == "utf-8" and calls[-1].get("errors") == "replace",
              str(calls[-1] if calls else None))

        # the blind-judge call from _judge_call_factory
        calls.clear()
        judge = B._judge_call_factory(model_alias="sonnet", cwd=None, max_budget_usd=0.5, timeout_s=30)
        judge("judge this")
        check("judge subprocess pins encoding=utf-8 + errors=replace",
              calls and calls[-1].get("encoding") == "utf-8" and calls[-1].get("errors") == "replace",
              str(calls[-1] if calls else None))


def gate_provision_verify_mcp() -> None:
    print("\n-- provision.verify_mcp: smoke-test subprocess decodes as UTF-8 --")
    P = _load("provision", BUILD)
    fn = getattr(P, "verify_mcp", None)
    if not callable(fn):
        check("verify_mcp exists", False)
        return
    calls: list = []
    P.subprocess.run = _recorder(calls)
    # verify_mcp returns early (True) if the smoke test file is absent; it IS present in this repo,
    # so the subprocess.run path is exercised.
    try:
        fn(next(iter(P.MCP_SERVERS)), deadline=5)
    except Exception:
        pass
    check("verify_mcp's subprocess pins encoding=utf-8 + errors=replace",
          calls and calls[-1].get("encoding") == "utf-8" and calls[-1].get("errors") == "replace",
          str(calls[-1] if calls else "no subprocess call recorded"))


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 64)
    print("DeepMap — subprocess UTF-8 decode gates (no spend, cross-platform safety)")
    print("=" * 64)

    gate_benchmark_subprocesses()
    gate_provision_verify_mcp()

    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
