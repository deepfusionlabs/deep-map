#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j gates for the benchmark --probe assertions (plan §7.2.1, ISS-build:002).

Locks the THREE headless-`claude` probe assertions in `benchmark/run_benchmark.py`:
  1. ON-arm load      — the deepmap_search MCP tool loaded + responded.
  2. OFF-arm isolation — no deepmap/Neo4j tool leaked into the --strict-mcp-config baseline.
  3. Capture-path      — the envelope carries total_cost_usd + num_turns.

The assertion logic is pure (operates on parsed-envelope dicts), so it is exercised here
WITHOUT spending a token or invoking claude. The orchestration (`do_probe`) is exercised by
monkeypatching the single spending helper (`_probe_call`) — proving:
  * do_probe NEVER spends unless DEEPMAP_REAL_ENGINE=1 (the ENGINE guard is preserved),
  * do_probe returns 0 ONLY when all three assertions pass against real output (honesty
    contract: a leak / a missing tool / a malformed envelope each force a non-zero exit).

Pure + hermetic: never opens a socket to a live server, never calls claude, never touches Neo4j.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

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


def _load_benchmark():
    """Load a FRESH module instance (so per-gate monkeypatching never leaks across gates)."""
    spec = importlib.util.spec_from_file_location("dm_run_benchmark_probe", BENCH / "run_benchmark.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# --------------------------------------------------------------------------- #
# Pure assertion-logic gates (no spend, no claude).
# --------------------------------------------------------------------------- #

def gate_sentinel_detection() -> None:
    print("\n-- _detect_probe_sentinels: exact sentinel set --")
    B = _load_benchmark()
    check("OK sentinel detected", B._detect_probe_sentinels(f"x {B.PROBE_OK} y") == {B.PROBE_OK})
    check("MISSING sentinel detected", B._detect_probe_sentinels(B.PROBE_MISSING) == {B.PROBE_MISSING})
    check("PRESENT sentinel detected", B._detect_probe_sentinels(B.PROBE_PRESENT) == {B.PROBE_PRESENT})
    check("no sentinel -> empty set", B._detect_probe_sentinels("just a normal answer") == set())
    check("None answer -> empty set (no crash)", B._detect_probe_sentinels(None) == set())
    check("conflicting sentinels both detected",
          B._detect_probe_sentinels(f"{B.PROBE_OK} and {B.PROBE_MISSING}") == {B.PROBE_OK, B.PROBE_MISSING})


def gate_assert_on_arm_loaded() -> None:
    print("\n-- assert_on_arm_loaded: only a clean OK passes --")
    B = _load_benchmark()
    check("clean OK -> PASS (None)",
          B.assert_on_arm_loaded({"_answer_text": B.PROBE_OK, "cost_usd": 0.1, "tool_calls": 3}) is None)
    check("MISSING tool -> FAIL",
          B.assert_on_arm_loaded({"_answer_text": B.PROBE_MISSING}) is not None)
    check("no sentinel -> FAIL",
          B.assert_on_arm_loaded({"_answer_text": "I could not find a deepmap tool"}) is not None)
    check("ambiguous (OK+MISSING) -> FAIL",
          B.assert_on_arm_loaded({"_answer_text": f"{B.PROBE_OK} {B.PROBE_MISSING}"}) is not None)
    check("unparsable envelope -> FAIL",
          B.assert_on_arm_loaded({"_parse_error": "not JSON", "_answer_text": None}) is not None)


def gate_assert_off_arm_isolated() -> None:
    print("\n-- assert_off_arm_isolated: any leak is a hard FAIL --")
    B = _load_benchmark()
    check("clean MISSING -> PASS (None)",
          B.assert_off_arm_isolated({"_answer_text": B.PROBE_MISSING}) is None)
    check("PRESENT (leak) -> FAIL",
          B.assert_off_arm_isolated({"_answer_text": B.PROBE_PRESENT}) is not None)
    check("OK (tool actually ran in baseline) -> FAIL",
          B.assert_off_arm_isolated({"_answer_text": B.PROBE_OK}) is not None)
    check("no sentinel -> FAIL (cannot confirm isolation)",
          B.assert_off_arm_isolated({"_answer_text": "no tools here"}) is not None)
    check("unparsable envelope -> FAIL",
          B.assert_off_arm_isolated({"_parse_error": "not JSON"}) is not None)


def gate_assert_capture_path() -> None:
    print("\n-- assert_capture_path: needs a clean, non-error envelope with cost + tool_calls --")
    B = _load_benchmark()
    check("cost+turns present -> PASS (None)",
          B.assert_capture_path("ON-arm", {"cost_usd": 0.02, "tool_calls": 4}) is None)
    check("missing cost -> FAIL",
          B.assert_capture_path("ON-arm", {"cost_usd": None, "tool_calls": 4}) is not None)
    check("missing tool_calls -> FAIL",
          B.assert_capture_path("OFF-arm", {"cost_usd": 0.02, "tool_calls": None}) is not None)
    check("zero turns is still a valid number (not None) -> PASS",
          B.assert_capture_path("OFF-arm", {"cost_usd": 0.0, "tool_calls": 0}) is None)
    check("explicit exit_code 0 -> PASS",
          B.assert_capture_path("ON-arm", {"cost_usd": 0.02, "tool_calls": 4, "_exit_code": 0}) is None)
    check("unparsable envelope -> FAIL",
          B.assert_capture_path("ON-arm", {"_parse_error": "not JSON"}) is not None)
    # HIGH-1/HIGH-2 (review): an ERROR envelope still parses + carries cost/turns — it must NOT
    # be read as a clean capture. is_error and a non-zero exit are both hard fails.
    check("is_error:true envelope (budget/refusal) -> FAIL even with cost+turns",
          B.assert_capture_path("ON-arm",
                                {"cost_usd": 5.0, "tool_calls": 3, "_is_error": True,
                                 "_subtype": "error_max_turns"}) is not None)
    check("non-zero claude exit_code -> FAIL even with cost+turns",
          B.assert_capture_path("ON-arm",
                                {"cost_usd": 0.02, "tool_calls": 3, "_exit_code": 1}) is not None)


def gate_parse_envelope_error_state() -> None:
    print("\n-- parse_envelope surfaces is_error/subtype; an errored run is not a clean capture --")
    import json as _json
    B = _load_benchmark()
    # A real claude --output-format json ERROR envelope: well-formed JSON, is_error:true, the
    # result string happens to echo the OK sentinel, cost/turns populated.
    env = _json.dumps({
        "type": "result", "subtype": "error_max_turns", "is_error": True,
        "result": f"I ran out of turns. {B.PROBE_OK}", "total_cost_usd": 5.0, "num_turns": 3,
    })
    m = B.parse_envelope(env)
    check("parse_envelope records _is_error=True", m.get("_is_error") is True, str(m.get("_is_error")))
    check("parse_envelope records _subtype", m.get("_subtype") == "error_max_turns", str(m.get("_subtype")))
    check("cost/turns still extracted from the error envelope",
          m.get("cost_usd") == 5.0 and m.get("tool_calls") == 3)
    # The sentinel is present, so assert_on_arm_loaded alone would say OK — but capture_path must
    # veto the errored run, so the COMBINED verdict is FAIL (no false pass).
    on_ok = B.assert_on_arm_loaded(m) is None
    cap_fail = B.assert_capture_path("ON-arm", m) is not None
    check("error envelope with OK sentinel -> capture_path FAILS (vetoes the false pass)",
          cap_fail, "assert_capture_path must reject is_error:true")
    check("(sanity) sentinel really was OK in that envelope", on_ok)


# --------------------------------------------------------------------------- #
# Orchestration gates — monkeypatch the spending helper, never call claude.
# --------------------------------------------------------------------------- #

def _patched_probe(B, *, server_files=True, reachable=True, claude=True, probe_results=None,
                   probe_raises=False):
    """Install hermetic monkeypatches on a fresh benchmark module B. Returns a `calls` list that
    records each arm `_probe_call` was invoked for — so a pass-path test can assert the spend
    actually happened (guards against a future early-return-0 refactor), and a guard test can
    assert it stayed empty. `probe_results` maps arm -> metrics dict; `probe_raises=True` makes
    _probe_call raise if ever reached (proves the ENGINE/reachability guards short-circuit)."""
    B._server_files_present = lambda: server_files
    B._unreachable_mcp_servers = lambda: ([] if reachable else ["deepmap-search (127.0.0.1:8787)"])
    B._which = lambda name: ("claude" if claude else None)

    calls: list = []

    def fake_probe(arm, prompt, repo_dir, cfg, out_dir, timeout_s, max_budget_usd):
        calls.append(arm)
        if probe_raises:
            raise AssertionError("_probe_call was invoked — a spend happened that should not have")
        return (probe_results or {}).get(arm, {})

    B._probe_call = fake_probe
    return calls


def _run_do_probe(B, engine: str | None):
    """Call do_probe with RESULTS_ROOT redirected to a temp dir; return its exit code."""
    saved_engine = os.environ.get(B.ENGINE_ENV)
    td = tempfile.TemporaryDirectory()
    B.RESULTS_ROOT = Path(td.name)
    try:
        if engine is None:
            os.environ.pop(B.ENGINE_ENV, None)
        else:
            os.environ[B.ENGINE_ENV] = engine
        args = SimpleNamespace(clone_root=None, probe_timeout_s=60, max_budget_usd=1.0)
        return B.do_probe(args, "probe-gate-test")
    finally:
        if saved_engine is None:
            os.environ.pop(B.ENGINE_ENV, None)
        else:
            os.environ[B.ENGINE_ENV] = saved_engine
        td.cleanup()


def gate_parse_envelope_extraction() -> None:
    print("\n-- parse_envelope: extracts the §7.5 fields, never invents a missing value --")
    import json as _json
    B = _load_benchmark()

    # Canonical success envelope.
    m = B.parse_envelope(_json.dumps({
        "result": "the answer", "total_cost_usd": 0.5, "num_turns": 7,
        "usage": {"input_tokens": 100, "output_tokens": 50},
    }))
    check("cost_usd from total_cost_usd", m.get("cost_usd") == 0.5)
    check("tool_calls from num_turns", m.get("tool_calls") == 7)
    check("tokens_in from usage.input_tokens", m.get("tokens_in") == 100)
    check("tokens_out from usage.output_tokens", m.get("tokens_out") == 50)
    check("answer text from result", m.get("_answer_text") == "the answer")
    check("clean success has no _parse_error", m.get("_parse_error") is None)

    # Fallback field names (cost_usd / tool_calls / text / usage.input|output).
    m2 = B.parse_envelope(_json.dumps({
        "cost_usd": 0.3, "tool_calls": 4, "text": "hi", "usage": {"input": 10, "output": 5},
    }))
    check("cost_usd fallback key honored", m2.get("cost_usd") == 0.3)
    check("tool_calls fallback key honored", m2.get("tool_calls") == 4)
    check("tokens fallback keys (input/output) honored",
          m2.get("tokens_in") == 10 and m2.get("tokens_out") == 5)
    check("answer text fallback key (text) honored", m2.get("_answer_text") == "hi")

    # Missing fields stay None — NEVER invented.
    m3 = B.parse_envelope(_json.dumps({}))
    check("missing cost stays None (not invented)", m3.get("cost_usd") is None)
    check("missing tool_calls stays None (not invented)", m3.get("tool_calls") is None)

    # Non-JSON / non-object → _parse_error, all metrics None.
    bad = B.parse_envelope("not json at all")
    check("non-JSON sets _parse_error", bool(bad.get("_parse_error")))
    check("non-JSON leaves cost None", bad.get("cost_usd") is None)
    notobj = B.parse_envelope("[1, 2, 3]")
    check("JSON non-object sets _parse_error", bool(notobj.get("_parse_error")))


def gate_engine_guard_preserved() -> None:
    print("\n-- do_probe: ENGINE guard preserved (no spend without DEEPMAP_REAL_ENGINE=1) --")
    B = _load_benchmark()
    # servers present + reachable + claude present, but ENGINE unset: must NOT spend.
    _patched_probe(B, probe_raises=True)
    rc = _run_do_probe(B, engine=None)
    check("ENGINE unset -> non-zero (no spend)", rc != 0, f"rc={rc}")


def gate_fail_closed_before_spend() -> None:
    print("\n-- do_probe: fail-closed before spend on missing files / dead servers --")
    B = _load_benchmark()
    _patched_probe(B, server_files=False, probe_raises=True)
    check("server files absent -> non-zero, no spend", _run_do_probe(B, engine="1") != 0)

    B = _load_benchmark()
    _patched_probe(B, server_files=True, reachable=False, probe_raises=True)
    check("servers unreachable -> non-zero, no spend", _run_do_probe(B, engine="1") != 0)

    B = _load_benchmark()
    _patched_probe(B, server_files=True, reachable=True, claude=False, probe_raises=True)
    check("claude not on PATH -> non-zero, no spend", _run_do_probe(B, engine="1") != 0)


def gate_orchestration_pass_and_fail() -> None:
    print("\n-- do_probe: returns 0 only on a real 3-assertion pass --")
    B = _load_benchmark()
    good = {
        B.ARM_ON: {"_answer_text": B.PROBE_OK, "cost_usd": 0.02, "tool_calls": 3},
        B.ARM_OFF: {"_answer_text": B.PROBE_MISSING, "cost_usd": 0.005, "tool_calls": 1},
    }
    calls = _patched_probe(B, probe_results=good)
    rc = _run_do_probe(B, engine="1")
    check("all three assertions pass -> exit 0", rc == 0, f"rc={rc}")
    check("the pass path actually invoked _probe_call for BOTH arms (no early-return-0)",
          calls == [B.ARM_ON, B.ARM_OFF], str(calls))

    # OFF-arm leak (PRESENT) -> must fail.
    B = _load_benchmark()
    leak = {
        B.ARM_ON: {"_answer_text": B.PROBE_OK, "cost_usd": 0.02, "tool_calls": 3},
        B.ARM_OFF: {"_answer_text": B.PROBE_PRESENT, "cost_usd": 0.005, "tool_calls": 1},
    }
    _patched_probe(B, probe_results=leak)
    check("OFF-arm tool leak -> non-zero (invalidates comparison)", _run_do_probe(B, engine="1") != 0)

    # ON-arm tool missing -> must fail.
    B = _load_benchmark()
    miss = {
        B.ARM_ON: {"_answer_text": B.PROBE_MISSING, "cost_usd": 0.02, "tool_calls": 1},
        B.ARM_OFF: {"_answer_text": B.PROBE_MISSING, "cost_usd": 0.005, "tool_calls": 1},
    }
    _patched_probe(B, probe_results=miss)
    check("ON-arm tool missing -> non-zero (servers didn't load)", _run_do_probe(B, engine="1") != 0)

    # Capture-path broken (no cost) -> must fail.
    B = _load_benchmark()
    nocap = {
        B.ARM_ON: {"_answer_text": B.PROBE_OK, "cost_usd": None, "tool_calls": 3},
        B.ARM_OFF: {"_answer_text": B.PROBE_MISSING, "cost_usd": 0.005, "tool_calls": 1},
    }
    _patched_probe(B, probe_results=nocap)
    check("capture-path missing cost -> non-zero", _run_do_probe(B, engine="1") != 0)

    # ON-arm errored envelope (is_error/budget) but OK sentinel present -> must NOT pass (HIGH-1).
    B = _load_benchmark()
    errored = {
        B.ARM_ON: {"_answer_text": B.PROBE_OK, "cost_usd": 5.0, "tool_calls": 3,
                   "_is_error": True, "_subtype": "error_max_turns"},
        B.ARM_OFF: {"_answer_text": B.PROBE_MISSING, "cost_usd": 0.005, "tool_calls": 1},
    }
    _patched_probe(B, probe_results=errored)
    check("ON-arm is_error envelope w/ OK sentinel -> non-zero (no fabricated pass)",
          _run_do_probe(B, engine="1") != 0)

    # ON-arm non-zero claude exit -> must NOT pass (HIGH-2).
    B = _load_benchmark()
    nonzero = {
        B.ARM_ON: {"_answer_text": B.PROBE_OK, "cost_usd": 0.02, "tool_calls": 3, "_exit_code": 1},
        B.ARM_OFF: {"_answer_text": B.PROBE_MISSING, "cost_usd": 0.005, "tool_calls": 1, "_exit_code": 0},
    }
    _patched_probe(B, probe_results=nonzero)
    check("ON-arm non-zero claude exit -> non-zero (no fabricated pass)",
          _run_do_probe(B, engine="1") != 0)


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 64)
    print("DeepMap — benchmark --probe assertion gates (no Neo4j, no spend)")
    print("=" * 64)

    gate_sentinel_detection()
    gate_assert_on_arm_loaded()
    gate_assert_off_arm_isolated()
    gate_assert_capture_path()
    gate_parse_envelope_error_state()
    gate_parse_envelope_extraction()
    gate_engine_guard_preserved()
    gate_fail_closed_before_spend()
    gate_orchestration_pass_and_fail()

    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
