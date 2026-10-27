#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j / no-key / no-network / no-spend gates for the RM-2 Phase-2 OpenRouter ARM RUNNER
(`benchmark/arm_openrouter.py`) + its `run_benchmark.py` dispatch + Enabler A/B.

This is the RED-before-GREEN gate suite for the multi-LLM benchmark build
(benchmark/README.md). It locks the ONE load-bearing contract: an
OpenRouter arm synthesizes a byte-compatible `claude`-shaped envelope so the FROZEN blind scorer
(`score_completeness` / `score_correctness` / `score_run`) grades a non-Claude arm's answer with
ZERO edits (§2.1 the parity keystone). Everything here is hermetic:
  - a SCRIPTED fake chat transport (no OpenRouter, no key, no network),
  - a fake deepmap tool transport (no MCP servers, no Neo4j),
  - a temp clone (no real repo),
so the whole agentic loop + dispatch + cost accounting + parity is proven without spending a cent.

Gates (design §5):
  1  tool_specs fairness         — OFF = {read_file, search_files}; ON adds the 3 deepmap tools; OFF has NO deepmap_*.
  2  executor sandbox            — read/search stay inside the clone; ../ / absolute / symlink-out / bad-regex -> tool-error, no raise.
  3  deepmap adapter routing     — deepmap_* -> injected transport -> string; a transport raise -> tool-error, not a crash.
  4  agentic loop (happy path)   — scripted tool-call then final; role:"tool" appended, stop=final, is_error False, counts summed.
  5  honest failure              — max_turns/budget/max_output -> is_error True, distinct stop, empty answer, _is_errored_run True.
  6  envelope parity             — parse_envelope(build_openrouter_envelope(...)) matches a reference Claude envelope; _answer_text == answer.
  7  record-shape parity         — run_one_openrouter writes EXACTLY run_one's key set; answer_text_path resolves to a real stdout.json.
  8  THE MONEY GATE              — the produced record + its stdout.json score through the FROZEN blind scorer exactly as a Claude card.
  9  dispatch                    — arm_specs/arm_family map --arm-model to ON+OFF specs and back; missing-key refuses (no spend).
  10 cost accounting             — per-round usage summed; cost == hand-computed PRICES figure; unknown model -> default tuple (flagged).
  11 Enabler A                   — parallel score_records yields the SAME per-card verdicts as sequential (mocked judge).
  12 Enabler B                   — the pre-matrix spend guardrail refuses OpenRouter spend without an explicit confirm; projection is honest.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import os
import shutil
import sys
import tempfile
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


def _load(name: str, fname: str):
    spec = importlib.util.spec_from_file_location(name, BENCH / fname)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# --------------------------------------------------------------------------- #
# Scripted fakes — no network, no key, no MCP, no Neo4j.
# --------------------------------------------------------------------------- #

class ScriptedTransport:
    """A fake OpenRouter /chat/completions transport. Returns pre-scripted response dicts (the raw
    OpenAI-shaped JSON body) and RECORDS every `messages` list it was called with, so a test can
    assert the loop appended the tool results before the next turn."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, messages, tools):
        self.calls.append([copy.deepcopy(m) for m in messages])
        if not self.responses:
            # Default: a terminal empty answer so a mis-scripted test fails loudly, not hangs.
            return _resp_final("")
        return self.responses.pop(0)


def _resp_tool_call(name, args, tid="c1", pin=100, pout=10):
    return {"choices": [{"message": {"role": "assistant", "content": None,
             "tool_calls": [{"id": tid, "type": "function",
                             "function": {"name": name, "arguments": json.dumps(args)}}]}}],
            "usage": {"prompt_tokens": pin, "completion_tokens": pout}}


def _resp_final(text, pin=50, pout=20):
    return {"choices": [{"message": {"role": "assistant", "content": text}}],
            "usage": {"prompt_tokens": pin, "completion_tokens": pout}}


def _make_clone() -> Path:
    d = Path(tempfile.mkdtemp(prefix="dm_arm_clone_"))
    (d / "pkg").mkdir()
    (d / "pkg" / "mod.py").write_text("def foo():\n    return 42\n", encoding="utf-8")
    (d / "README.md").write_text("hello world\n", encoding="utf-8")
    return d


# --------------------------------------------------------------------------- #
# Gates.
# --------------------------------------------------------------------------- #

def gate_tool_specs(AO, B) -> None:
    print("\n-- 1) tool_specs fairness (OFF is the fair grep baseline; ON adds deepmap) --")
    off = AO.tool_specs(B.ARM_OFF)
    on = AO.tool_specs(B.ARM_ON)
    off_names = {t["function"]["name"] for t in off}
    on_names = {t["function"]["name"] for t in on}
    check("OFF = exactly {read_file, search_files}", off_names == {"read_file", "search_files"}, str(off_names))
    check("ON adds the three deepmap tools",
          {"read_file", "search_files", "deepmap_search", "deepmap_impact", "deepmap_embed"} <= on_names,
          str(on_names))
    check("OFF has NO deepmap_* tool (the assert_off_arm_isolated analog)",
          not any("deepmap" in n for n in off_names), str(off_names))
    check("every tool spec is an OpenAI function object with a name",
          all(t.get("type") == "function" and t.get("function", {}).get("name") for t in on + off))
    # A NAMESPACED arm (deepmap_off@openai/gpt-5) must resolve to its family for fairness.
    ns_off = {t["function"]["name"] for t in AO.tool_specs(B.ARM_OFF + "@openai/gpt-5")}
    check("namespaced OFF arm still has no deepmap_* tool", not any("deepmap" in n for n in ns_off), str(ns_off))
    # The SYSTEM prompt must be identical across arms — only tool availability differs (§2.4).
    check("SYSTEM_PROMPT is identical for ON and OFF (only tools differ)",
          AO.SYSTEM_PROMPT(B.ARM_ON) == AO.SYSTEM_PROMPT(B.ARM_OFF))


def gate_executor_sandbox(AO) -> None:
    print("\n-- 2) executor sandbox (read/search never escape the clone; failures are tool-errors) --")
    clone = _make_clone()
    outside = clone.parent / "SECRET_do_not_read.txt"
    outside.write_text("TOP SECRET", encoding="utf-8")
    try:
        ok = AO.exec_read_file(clone, "pkg/mod.py")
        check("read_file reads a file INSIDE the clone", "def foo" in ok, ok[:80])

        esc = AO.exec_read_file(clone, "../SECRET_do_not_read.txt")
        check("read_file('../…') returns a structured error, reads nothing outside",
              '"error"' in esc and "TOP SECRET" not in esc, esc[:120])

        absres = AO.exec_read_file(clone, str(outside))
        check("read_file(absolute host path) is rejected (reads nothing outside)",
              '"error"' in absres and "TOP SECRET" not in absres, absres[:120])

        # symlink-out — best-effort (Windows may forbid symlink creation without privilege).
        link = clone / "escape.py"
        try:
            os.symlink(outside, link)
            symres = AO.exec_read_file(clone, "escape.py")
            check("read_file(symlink-out) is rejected (resolve() escapes the clone)",
                  '"error"' in symres and "TOP SECRET" not in symres, symres[:120])
        except (OSError, NotImplementedError):
            check("read_file(symlink-out) [skipped: cannot create a symlink in this environment]", True)

        good = AO.exec_search_files(clone, "foo")
        check("search_files finds a match inside the clone", "mod.py" in good or "foo" in good, good[:120])

        bad = AO.exec_search_files(clone, "(")   # invalid regex -> tool-error, NOT a raise
        check("search_files(bad regex) -> structured error, no raise", '"error"' in bad, bad[:120])

        # _resolve_in_clone is the pure sandbox primitive — assert directly too.
        try:
            AO._resolve_in_clone(clone, "../x")
            check("_resolve_in_clone rejects '..' escape (raises)", False)
        except Exception:
            check("_resolve_in_clone rejects '..' escape (raises)", True)
        inside = AO._resolve_in_clone(clone, "pkg/mod.py")
        check("_resolve_in_clone resolves an in-clone path", Path(inside).name == "mod.py", str(inside))
    finally:
        shutil.rmtree(clone, ignore_errors=True)
        outside.unlink(missing_ok=True)


def gate_deepmap_routing(AO) -> None:
    print("\n-- 3) deepmap adapter routing (deepmap_* -> injected transport; a raise -> tool-error) --")
    clone = _make_clone()
    try:
        seen = {}
        def fake_transport(name, arguments):
            seen["name"] = name
            seen["args"] = arguments
            return json.dumps({"routed": name, "top": arguments.get("query")})

        out = AO.execute_tool("deepmap_search", {"query": "auth"}, repo_dir=clone,
                              deepmap_transport=fake_transport, tool_timeout_s=5)
        check("deepmap_search routes to the injected transport", seen.get("name") == "deepmap_search")
        check("deepmap adapter returns a STRING the model can read", isinstance(out, str) and "deepmap_search" in out, out[:120])

        def boom(name, arguments):
            raise RuntimeError("MCP server down")
        crashed = AO.execute_tool("deepmap_impact", {"symbol": "charge"}, repo_dir=clone,
                                  deepmap_transport=boom, tool_timeout_s=5)
        check("a deepmap transport exception -> tool-error STRING (not a crash)",
              isinstance(crashed, str) and '"error"' in crashed, crashed[:120])

        unknown = AO.execute_tool("no_such_tool", {}, repo_dir=clone, deepmap_transport=fake_transport)
        check("an unknown tool name -> tool-error string", '"error"' in unknown, unknown[:120])
    finally:
        shutil.rmtree(clone, ignore_errors=True)


def gate_agentic_loop(AO, B) -> None:
    print("\n-- 4) agentic loop happy path (tool-call round then a final answer) --")
    clone = _make_clone()
    try:
        st = ScriptedTransport([
            _resp_tool_call("read_file", {"path": "pkg/mod.py"}, pin=100, pout=10),
            _resp_final("The function foo() returns 42.", pin=50, pout=20),
        ])
        seen = {"deepmap": 0}
        def dm(name, arguments):
            seen["deepmap"] += 1
            return "{}"
        result = AO.run_agentic_loop(
            model="openai/gpt-5", arm=B.ARM_ON, question={"prompt": "what does foo do?"},
            repo_dir=clone, transport=st, deepmap_transport=dm,
            max_turns=6, max_output_tokens=1_000_000, budget_usd=5.0, tool_timeout_s=5)
        check("loop stops on the final answer (stop == 'final')", result["stop"] == "final", str(result.get("stop")))
        check("a successful loop is NOT an error", result["is_error"] is False)
        check("the answer text is captured", result["answer"] == "The function foo() returns 42.", result["answer"])
        check("tool_calls counted (1 executed tool)", result["tool_calls"] == 1, str(result["tool_calls"]))
        check("tokens_in summed across rounds (100+50)", result["tokens_in"] == 150, str(result["tokens_in"]))
        check("tokens_out summed across rounds (10+20)", result["tokens_out"] == 30, str(result["tokens_out"]))
        check("cost accrued (> 0)", result["cost"] > 0, str(result["cost"]))
        # The 2nd transport call must have SEEN a role:"tool" message appended by the loop.
        second = st.calls[1] if len(st.calls) > 1 else []
        check("a role:'tool' result was appended before the next turn",
              any(m.get("role") == "tool" for m in second), str([m.get("role") for m in second]))
        check("the executed read_file was a LOCAL tool (deepmap transport not called)", seen["deepmap"] == 0)
    finally:
        shutil.rmtree(clone, ignore_errors=True)


def gate_honest_failure(AO, B) -> None:
    print("\n-- 5) honest failure (max_turns / budget / max_output -> is_error True, no fabrication) --")
    clone = _make_clone()
    try:
        # max_turns: the model keeps calling a tool, never gives a final answer.
        loop_forever = ScriptedTransport([_resp_tool_call("read_file", {"path": "pkg/mod.py"}) for _ in range(10)])
        r_turns = AO.run_agentic_loop(model="openai/gpt-5", arm=B.ARM_OFF, question={"prompt": "q"},
                                      repo_dir=clone, transport=loop_forever, deepmap_transport=lambda n, a: "{}",
                                      max_turns=2, max_output_tokens=1_000_000, budget_usd=100.0, tool_timeout_s=5)
        check("max_turns -> stop == 'max_turns'", r_turns["stop"] == "max_turns", str(r_turns["stop"]))
        check("max_turns -> is_error True", r_turns["is_error"] is True)
        check("max_turns -> empty answer (never fabricated)", (r_turns["answer"] or "") == "")

        # budget: one heavy round blows the tiny budget.
        heavy = ScriptedTransport([_resp_tool_call("read_file", {"path": "pkg/mod.py"}, pin=10_000_000, pout=10)
                                   for _ in range(5)])
        r_budget = AO.run_agentic_loop(model="openai/gpt-5", arm=B.ARM_OFF, question={"prompt": "q"},
                                       repo_dir=clone, transport=heavy, deepmap_transport=lambda n, a: "{}",
                                       max_turns=5, max_output_tokens=1_000_000_000, budget_usd=0.01, tool_timeout_s=5)
        check("budget exhausted -> stop == 'budget'", r_budget["stop"] == "budget", str(r_budget["stop"]))
        check("budget -> is_error True", r_budget["is_error"] is True)

        # max_output_tokens: a round emits a huge completion. Budget is set FAR above the round cost so
        # the max-output cap is the one that trips (the loop checks budget BEFORE max_output, by design).
        loud = ScriptedTransport([_resp_tool_call("read_file", {"path": "pkg/mod.py"}, pin=10, pout=10_000_000)
                                  for _ in range(5)])
        r_out = AO.run_agentic_loop(model="openai/gpt-5", arm=B.ARM_OFF, question={"prompt": "q"},
                                    repo_dir=clone, transport=loud, deepmap_transport=lambda n, a: "{}",
                                    max_turns=5, max_output_tokens=1000, budget_usd=1_000_000.0, tool_timeout_s=5)
        check("max_output -> stop == 'max_output_tokens'", r_out["stop"] == "max_output_tokens", str(r_out["stop"]))
        check("max_output -> is_error True", r_out["is_error"] is True)

        # An errored loop, once turned into a record, must be EXCLUDED from medians by _is_errored_run.
        out_dir = B.RESULTS_ROOT / "_arm_runner_gate_tmp"
        try:
            rec = AO.run_one_openrouter("openai/gpt-5", B.ARM_OFF, {"repo": "o/r", "id": "q1", "prompt": "q"},
                                        clone, 1, out_dir, budget_usd=0.01, timeout_s=5, transport=heavy,
                                        deepmap_transport=lambda n, a: "{}")
            check("an errored openrouter record -> _is_errored_run(record) is True",
                  B._is_errored_run(rec) is True, json.dumps(rec.get("metrics", {})))
        finally:
            shutil.rmtree(out_dir, ignore_errors=True)
    finally:
        shutil.rmtree(clone, ignore_errors=True)


def gate_loop_edge_cases(AO, B) -> None:
    print("\n-- 4b) agentic loop edge cases (multi-tool-call turn / malformed args / empty final) --")
    clone = _make_clone()
    try:
        # TWO tool calls in a SINGLE assistant turn -> both execute, both tool results appended, count=2.
        multi = ScriptedTransport([
            {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
                {"id": "a", "type": "function", "function": {"name": "read_file", "arguments": json.dumps({"path": "pkg/mod.py"})}},
                {"id": "b", "type": "function", "function": {"name": "search_files", "arguments": json.dumps({"pattern": "foo"})}},
            ]}}], "usage": {"prompt_tokens": 100, "completion_tokens": 10}},
            _resp_final("both tools ran.", pin=10, pout=5),
        ])
        r = AO.run_agentic_loop(model="openai/gpt-5", arm=B.ARM_OFF, question={"prompt": "q"},
                                repo_dir=clone, transport=multi, deepmap_transport=lambda n, a: "{}",
                                max_turns=5, max_output_tokens=1_000_000, budget_usd=100.0, tool_timeout_s=5)
        check("two tool calls in one turn -> tool_calls == 2", r["tool_calls"] == 2, str(r["tool_calls"]))
        check("multi-tool turn still reaches the final answer", r["stop"] == "final" and r["is_error"] is False)
        tool_msgs = [m for m in multi.calls[1] if m.get("role") == "tool"]
        check("both tool results were appended before the next turn (2 role:'tool' msgs)",
              len(tool_msgs) == 2, str(len(tool_msgs)))
        check("the two tool results carry the matching tool_call_ids",
              {m.get("tool_call_id") for m in tool_msgs} == {"a", "b"}, str([m.get("tool_call_id") for m in tool_msgs]))

        # MALFORMED tool-call arguments (not JSON) -> the executor gets an honest tool-error, no crash.
        malformed = ScriptedTransport([
            {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
                {"id": "x", "type": "function", "function": {"name": "read_file", "arguments": "{not valid json"}}]}}],
             "usage": {"prompt_tokens": 5, "completion_tokens": 5}},
            _resp_final("recovered.", pin=5, pout=5),
        ])
        r2 = AO.run_agentic_loop(model="openai/gpt-5", arm=B.ARM_OFF, question={"prompt": "q"},
                                 repo_dir=clone, transport=malformed, deepmap_transport=lambda n, a: "{}",
                                 max_turns=5, max_output_tokens=1_000_000, budget_usd=100.0, tool_timeout_s=5)
        check("malformed tool arguments -> loop survives and still answers", r2["stop"] == "final")
        tool_out = next((m for m in malformed.calls[1] if m.get("role") == "tool"), {})
        check("malformed args -> the tool result is an honest error string (not a crash)",
              '"error"' in (tool_out.get("content") or ""), str(tool_out.get("content"))[:100])

        # A response with EMPTY choices -> treated as a final (empty) answer, is_error False, no crash.
        empty = ScriptedTransport([{"choices": [], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}])
        r3 = AO.run_agentic_loop(model="openai/gpt-5", arm=B.ARM_OFF, question={"prompt": "q"},
                                 repo_dir=clone, transport=empty, deepmap_transport=lambda n, a: "{}",
                                 max_turns=5, max_output_tokens=1_000_000, budget_usd=100.0, tool_timeout_s=5)
        check("empty choices -> final with an empty answer (a failed capture, not a crash)",
              r3["stop"] == "final" and (r3["answer"] or "") == "", str(r3))
    finally:
        shutil.rmtree(clone, ignore_errors=True)


def gate_loop_fuzz(AO, B) -> None:
    print("\n-- 5b) fuzz: run_agentic_loop never crashes on MALFORMED model output (untrusted input) --")
    import random
    clone = _make_clone()
    try:
        rng = random.Random(20260701)   # deterministic — reproducible, no wall-clock/entropy

        def _rand_usage():
            return rng.choice([
                {"prompt_tokens": rng.randint(0, 1000), "completion_tokens": rng.randint(0, 1000)},
                {}, None, {"prompt_tokens": "not-a-number"}, {"completion_tokens": None},
                {"cost": rng.choice([0.001, "x", None])}, "not-a-dict",
            ])

        def _rand_tool_calls():
            return rng.choice([
                [{"id": "t", "type": "function", "function": {"name": "read_file",
                  "arguments": json.dumps({"path": "pkg/mod.py"})}}],
                [{"id": "t", "function": {"name": "search_files", "arguments": "{not json"}}],
                [{"function": {"name": "deepmap_search"}}],       # missing id + args
                [{"id": "t"}],                                     # missing function
                [None, "junk", 42],                                # non-dict entries
                {"a": 1},                                          # a DICT, not a list (adversarial)
                "tool_calls_as_string",                            # a string
                [], None,                                          # empty / none -> final
            ])

        def _rand_message():
            return rng.choice([
                {"role": "assistant", "content": rng.choice(["done", None, ""]),
                 "tool_calls": _rand_tool_calls()},
                {"role": "assistant", "content": rng.choice(["final answer", None])},
                {}, None,
            ])

        def _rand_response():
            return rng.choice([
                {"choices": [{"message": _rand_message()}], "usage": _rand_usage()},
                {"choices": [], "usage": _rand_usage()},
                {"usage": _rand_usage()},                          # missing choices
                {}, None, {"choices": "not-a-list"},
            ])

        class _FuzzTransport:
            def __call__(self, messages, tools):
                return _rand_response()

        valid_stops = {"final", "budget", "max_output_tokens", "max_turns"}
        crashes = 0
        bad_shape = 0
        for i in range(400):
            try:
                r = AO.run_agentic_loop(
                    model="openai/gpt-5", arm=rng.choice([B.ARM_ON, B.ARM_OFF]),
                    question={"prompt": "q"}, repo_dir=clone, transport=_FuzzTransport(),
                    deepmap_transport=lambda n, a: rng.choice(["{}", 42, None, {"x": 1}]),
                    max_turns=rng.randint(1, 4), max_output_tokens=rng.randint(1, 100000),
                    budget_usd=rng.choice([0.0001, 5.0, 1e9]), tool_timeout_s=5)
            except Exception as exc:   # noqa: BLE001 — the WHOLE point: the loop must never raise
                crashes += 1
                if crashes <= 3:
                    print(f"       crash on iter {i}: {type(exc).__name__}: {exc}")
                continue
            # Shape contract: always a dict with the required keys + sane types.
            ok = (isinstance(r, dict)
                  and set(("answer", "tool_calls", "tokens_in", "tokens_out", "cost", "is_error", "stop")) <= set(r)
                  and isinstance(r["tokens_in"], int) and r["tokens_in"] >= 0
                  and isinstance(r["tokens_out"], int) and r["tokens_out"] >= 0
                  and isinstance(r["cost"], (int, float)) and r["cost"] >= 0
                  and r["stop"] in valid_stops
                  and isinstance(r["is_error"], bool))
            if not ok:
                bad_shape += 1
                if bad_shape <= 3:
                    print(f"       bad result shape on iter {i}: {r}")
        check("run_agentic_loop NEVER raised across 400 malformed-input fuzz iterations", crashes == 0,
              f"{crashes} crashes")
        check("every fuzz result is a well-formed result dict (keys + non-negative counts + valid stop)",
              bad_shape == 0, f"{bad_shape} bad-shaped results")
    finally:
        shutil.rmtree(clone, ignore_errors=True)


def gate_envelope_parity(AO, B) -> None:
    print("\n-- 6) envelope parity (an openrouter envelope parses byte-identically to a Claude one) --")
    result = {"answer": "foo() returns 42", "tool_calls": 3, "tokens_in": 1234, "tokens_out": 56,
              "cost": 0.0731, "is_error": False, "stop": "final"}
    env = AO.build_openrouter_envelope(result, "openai/gpt-5")
    m = B.parse_envelope(json.dumps(env))
    # A reference Claude envelope with the SAME numbers must parse to the same metric values.
    claude_env = {"type": "result", "subtype": "success", "is_error": False, "num_turns": 3,
                  "result": "foo() returns 42", "total_cost_usd": 0.0731,
                  "usage": {"input_tokens": 1234, "output_tokens": 56}}
    cm = B.parse_envelope(json.dumps(claude_env))
    for k in ("cost_usd", "tool_calls", "tokens_in", "tokens_out", "_is_error"):
        check(f"metric {k} matches the Claude reference envelope", m[k] == cm[k], f"{m[k]} != {cm[k]}")
    check("_answer_text == the loop's answer", m["_answer_text"] == "foo() returns 42", str(m["_answer_text"]))
    check("subtype rides as 'success' for a final answer", m["_subtype"] == "success", str(m.get("_subtype")))
    # A non-final (errored) loop rides its stop reason as the subtype and is_error True.
    e_env = AO.build_openrouter_envelope(
        {"answer": "", "tool_calls": 1, "tokens_in": 5, "tokens_out": 5, "cost": 0.0,
         "is_error": True, "stop": "max_turns"}, "openai/gpt-5")
    em = B.parse_envelope(json.dumps(e_env))
    check("an errored loop's envelope carries is_error True", em["_is_error"] is True)
    check("an errored loop's subtype is its stop reason", em["_subtype"] == "max_turns", str(em.get("_subtype")))


def gate_record_shape_parity(AO, B) -> None:
    print("\n-- 7) record-shape parity (run_one_openrouter writes EXACTLY run_one's key set) --")
    clone = _make_clone()
    out_dir = B.RESULTS_ROOT / "_arm_runner_gate_tmp"
    try:
        st = ScriptedTransport([_resp_final("foo() returns 42.", pin=100, pout=20)])
        rec = AO.run_one_openrouter("openai/gpt-5", B.ARM_ON,
                                    {"repo": "owner/repo", "id": "q1", "prompt": "what does foo do?",
                                     "family": "concept_to_impl", "grep_can_do": False},
                                    clone, 1, out_dir, budget_usd=5.0, timeout_s=5,
                                    transport=st, deepmap_transport=lambda n, a: "{}")
        expected_keys = {"repo", "question_id", "family", "grep_can_do", "arm", "run",
                         "exit_code", "started_at", "metrics", "answer_text_path", "scored"}
        check("record has EXACTLY run_one's top-level key set", set(rec.keys()) == expected_keys,
              str(sorted(set(rec.keys()) ^ expected_keys)))
        expected_metric_keys = set(B.METRIC_KEYS) | {"_is_error", "_subtype", "_exit_code"}
        check("metrics carry the METRIC_KEYS + the error-state keys _is_errored_run reads",
              set(rec["metrics"].keys()) == expected_metric_keys,
              str(sorted(set(rec["metrics"].keys()) ^ expected_metric_keys)))
        check("the arm is NAMESPACED (deepmap_on@openai/gpt-5) so aggregate groups per model",
              rec["arm"] == B.ARM_ON + "@openai/gpt-5", rec["arm"])
        check("scored is False at capture (blind Stage-2 flips it)", rec["scored"] is False)
        check("score fields are None at capture (never fabricated)",
              all(rec["metrics"][k] is None for k in B.SCORE_KEYS))
        check("wall_clock_s was measured", isinstance(rec["metrics"]["wall_clock_s"], (int, float)))
        answer_file = B.RESULTS_ROOT.parent / rec["answer_text_path"]
        check("answer_text_path resolves to a REAL stdout.json under RESULTS_ROOT.parent",
              answer_file.is_file() and answer_file.name == "stdout.json", str(answer_file))
        # The stdout.json is the exact envelope the blind scorer will read.
        env = json.loads(answer_file.read_text(encoding="utf-8"))
        check("the persisted stdout.json is a byte-compatible envelope (has 'result')", "result" in env)
    finally:
        shutil.rmtree(clone, ignore_errors=True)
        shutil.rmtree(out_dir, ignore_errors=True)


def gate_money(AO, B, SR) -> None:
    print("\n-- 8) THE MONEY GATE (the FROZEN blind scorer grades an openrouter card as a Claude card) --")
    clone = _make_clone()
    out_dir = B.RESULTS_ROOT / "_arm_runner_gate_tmp"
    try:
        answer = "It breaks BillingService.run, InvoiceJob.perform, and RetryWorker."
        st = ScriptedTransport([_resp_final(answer, pin=100, pout=30)])
        rec = AO.run_one_openrouter("openai/gpt-5", B.ARM_ON,
                                    {"repo": "owner/repo", "id": "q1", "prompt": "what breaks if I change charge()?",
                                     "family": "transitive_impact", "grep_can_do": False},
                                    clone, 1, out_dir, budget_usd=5.0, timeout_s=5,
                                    transport=st, deepmap_transport=lambda n, a: "{}")
        questions_by_id = {"q1": {
            "id": "q1", "repo": "owner/repo", "prompt": "what breaks if I change charge()?",
            "answer_key": {"ground_truth_set": ["BillingService.run", "InvoiceJob.perform", "RetryWorker"],
                           "ground_truth_answer": "BillingService.run, InvoiceJob.perform, RetryWorker"}}}
        judge = lambda prompt: '{"correctness_pct": 100, "wrong_high": 0}'
        reader = lambda r: B.read_answer_from_scorecard(r)
        scored = SR.score_records([rec], questions_by_id, judge_call_fn=judge, read_answer_fn=reader,
                                  pinned_sha_by_repo={"owner/repo": "a" * 40})
        s = scored[0]
        check("blind scorer read the openrouter answer via its stdout.json",
              s["metrics"]["completeness_pct"] == 100.0, str(s["metrics"].get("completeness_pct")))
        check("correctness filled from the (mocked) judge", s["metrics"]["correctness_pct"] == 100.0)
        check("the openrouter card is fully scored (parity with a Claude card)", s["scored"] is True)
    finally:
        shutil.rmtree(clone, ignore_errors=True)
        shutil.rmtree(out_dir, ignore_errors=True)


class _Args:
    def __init__(self, **kw):
        self.arm_model = kw.get("arm_model")
        self.confirm_spend = kw.get("confirm_spend", False)


def gate_deepmap_transport_wire(AO) -> None:
    print("\n-- 8b) default_deepmap_transport wire protocol (§2.3: JSON-RPC tools/call, port map) — mocked urlopen --")
    import urllib.request

    captured = {}

    class _FakeResp:
        def __init__(self, payload):
            self._payload = json.dumps(payload).encode("utf-8")
        def read(self):
            return self._payload
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    def _fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data.decode("utf-8"))
        captured["headers"] = {k.lower(): v for k, v in req.header_items()}
        # A well-formed MCP tools/call response: result.content[].text (each a serialized result dict).
        return _FakeResp({"jsonrpc": "2.0", "id": 1, "result": {"content": [
            {"type": "text", "text": json.dumps({"name": "charge", "score": 1.0})},
            {"type": "text", "text": json.dumps({"name": "refund", "score": 0.9})}]}})

    saved = urllib.request.urlopen
    urllib.request.urlopen = _fake_urlopen
    try:
        out = AO.default_deepmap_transport("deepmap_search", {"query": "billing", "top_k": 15})
        check("deepmap_search routes to :8787/mcp", captured["url"] == "http://127.0.0.1:8787/mcp", captured.get("url"))
        check("body is a JSON-RPC 2.0 tools/call", captured["body"].get("jsonrpc") == "2.0"
              and captured["body"].get("method") == "tools/call", str(captured.get("body")))
        check("body carries the tool name + arguments",
              captured["body"]["params"]["name"] == "deepmap_search"
              and captured["body"]["params"]["arguments"]["query"] == "billing", str(captured["body"]["params"]))
        check("Accept header allows text/event-stream (stateless streamable-HTTP)",
              "text/event-stream" in captured["headers"].get("accept", ""), str(captured.get("headers")))
        check("response content[].text is joined into the returned string",
              "charge" in out and "refund" in out, out[:120])

        AO.default_deepmap_transport("deepmap_embed", {"text": "x"})
        check("deepmap_embed routes to the :8788/mcp port (embed server)",
              captured["url"] == "http://127.0.0.1:8788/mcp", captured.get("url"))
        AO.default_deepmap_transport("deepmap_impact", {"symbol": "charge"})
        check("deepmap_impact routes to :8787/mcp (search/impact server)",
              captured["url"] == "http://127.0.0.1:8787/mcp", captured.get("url"))

        # An MCP error response must RAISE (execute_tool turns it into an honest tool-error upstream).
        def _err_urlopen(req, timeout=None):
            return _FakeResp({"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "boom"}})
        urllib.request.urlopen = _err_urlopen
        raised = False
        try:
            AO.default_deepmap_transport("deepmap_search", {"query": "x"})
        except Exception:
            raised = True
        check("an MCP error response RAISES (honest failure, not a silent empty result)", raised)
    finally:
        urllib.request.urlopen = saved


def gate_chat_transport_request(AO, B) -> None:
    print("\n-- 8c) _openrouter_chat_transport builds a correct /chat/completions request — mocked urlopen --")
    import urllib.request

    captured = {}

    class _FakeResp:
        def __init__(self, payload):
            self._payload = json.dumps(payload).encode("utf-8")
        def read(self):
            return self._payload
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    def _ok_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["headers"] = {k.lower(): v for k, v in req.header_items()}
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _FakeResp({"choices": [{"message": {"role": "assistant", "content": "hi"}}],
                          "usage": {"prompt_tokens": 5, "completion_tokens": 2}})

    saved = urllib.request.urlopen
    urllib.request.urlopen = _ok_urlopen
    try:
        transport = AO._openrouter_chat_transport(model="openai/gpt-5", api_key="sk-test-key", timeout_s=30)
        tools = AO.tool_specs(B.ARM_ON)
        resp = transport([{"role": "user", "content": "q"}], tools)
        check("POSTs to the OpenRouter chat-completions URL", captured["url"] == B.OPENROUTER_URL, captured.get("url"))
        check("sends the Authorization: Bearer <key> header",
              captured["headers"].get("authorization") == "Bearer sk-test-key", str(captured.get("headers")))
        body = captured["body"]
        check("body carries the model", body.get("model") == "openai/gpt-5", str(body.get("model")))
        check("body carries messages + tools + tool_choice=auto",
              isinstance(body.get("messages"), list) and isinstance(body.get("tools"), list)
              and body.get("tool_choice") == "auto", str({k: body.get(k) for k in ("tool_choice",)}))
        check("body carries a max_tokens cap", isinstance(body.get("max_tokens"), int) and body["max_tokens"] > 0)
        check("returns the parsed response dict on success", isinstance(resp, dict) and resp.get("choices"))

        # No API key -> RAISES (the run is marked errored upstream, never a silent empty result).
        no_key = AO._openrouter_chat_transport(model="openai/gpt-5", api_key=None, timeout_s=30)
        raised = False
        try:
            no_key([{"role": "user", "content": "q"}], tools)
        except Exception:
            raised = True
        check("a missing API key RAISES (honest failure)", raised)

        # A no-choices response -> RAISES (a malformed API reply is an errored run, not empty success).
        def _empty_urlopen(req, timeout=None):
            return _FakeResp({"choices": []})
        urllib.request.urlopen = _empty_urlopen
        raised2 = False
        try:
            transport([{"role": "user", "content": "q"}], tools)
        except Exception:
            raised2 = True
        check("a no-choices response RAISES (not a silent empty success)", raised2)
    finally:
        urllib.request.urlopen = saved


def gate_dispatch(B) -> None:
    print("\n-- 9) dispatch (arm_specs / arm_family / openrouter_arms_missing_key) --")
    # No --arm-model -> today's Claude-only behaviour (zero change).
    specs0 = B.arm_specs(_Args())
    check("no --arm-model -> exactly the two Claude specs (unchanged default)",
          specs0 == [("claude", B.ARM_ON), ("claude", B.ARM_OFF)], str(specs0))
    # --arm-model adds ON+OFF openrouter specs for each model (alongside the Claude anchor).
    specs1 = B.arm_specs(_Args(arm_model=["openai/gpt-5"]))
    check("--arm-model adds an ON openrouter spec",
          ("openrouter:openai/gpt-5", B.ARM_ON) in specs1, str(specs1))
    check("--arm-model adds an OFF openrouter spec",
          ("openrouter:openai/gpt-5", B.ARM_OFF) in specs1, str(specs1))
    check("the Claude anchor arms are still present (Opus stays the anchor)",
          ("claude", B.ARM_ON) in specs1 and ("claude", B.ARM_OFF) in specs1, str(specs1))
    # arm_family strips the @model suffix so OFF-isolation + honesty invariants apply per family.
    check("arm_family('deepmap_on@openai/gpt-5') == ARM_ON", B.arm_family(B.ARM_ON + "@openai/gpt-5") == B.ARM_ON)
    check("arm_family(ARM_OFF) == ARM_OFF (plain arm passes through)", B.arm_family(B.ARM_OFF) == B.ARM_OFF)
    # ENV-guard: --arm-model requested but no key -> refuse (fail closed, no spend).
    saved = os.environ.pop("OPENROUTER_API_KEY", None)
    try:
        check("openrouter arm + no key -> missing_key True (refuse, no spend)",
              B.openrouter_arms_missing_key(["openai/gpt-5"]) is True)
        check("no openrouter arm -> missing_key False (no key needed for the Claude path)",
              B.openrouter_arms_missing_key([]) is False)
        os.environ["OPENROUTER_API_KEY"] = "sk-test-not-real"
        check("openrouter arm + key present -> missing_key False",
              B.openrouter_arms_missing_key(["openai/gpt-5"]) is False)
    finally:
        os.environ.pop("OPENROUTER_API_KEY", None)
        if saved is not None:
            os.environ["OPENROUTER_API_KEY"] = saved


def gate_cost_accounting(AO, B) -> None:
    print("\n-- 10) cost accounting (per-round usage summed; PRICES applied; unknown -> default) --")
    # gpt-5 PRICES = (1.25, 10.0) $/M. 1M in + 1M out = 1.25 + 10.0 = 11.25.
    p = AO.price_of("openai/gpt-5", {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000})
    check("price_of applies the per-model PRICES ($11.25 for 1M+1M on gpt-5)", abs(p - 11.25) < 1e-9, str(p))
    # Unknown model -> the honest default tuple (3.0, 12.0): 1M+1M = 15.0, and it's FLAGGED as unpriced.
    pu = AO.price_of("vendor/unknown", {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000})
    check("unknown model -> default (3.0,12.0) => $15.00 (still costs honestly)", abs(pu - 15.0) < 1e-9, str(pu))
    check("model_priced flags a KNOWN model", AO.model_priced("openai/gpt-5") is True)
    check("model_priced flags an UNKNOWN model (so a gate can surface the missing row)",
          AO.model_priced("vendor/unknown") is False)
    check("zero usage costs zero", AO.price_of("openai/gpt-5", {}) == 0.0)
    # End-to-end: the loop sums cost across rounds == the hand-computed figure.
    clone = _make_clone()
    try:
        st = ScriptedTransport([
            _resp_tool_call("read_file", {"path": "pkg/mod.py"}, pin=100_000, pout=1_000),
            _resp_final("done", pin=50_000, pout=500),
        ])
        r = AO.run_agentic_loop(model="openai/gpt-5", arm=B.ARM_OFF, question={"prompt": "q"},
                                repo_dir=clone, transport=st, deepmap_transport=lambda n, a: "{}",
                                max_turns=5, max_output_tokens=1_000_000_000, budget_usd=1000.0, tool_timeout_s=5)
        # round1: 100000/1e6*1.25 + 1000/1e6*10 = 0.125 + 0.01 = 0.135
        # round2:  50000/1e6*1.25 +  500/1e6*10 = 0.0625 + 0.005 = 0.0675  => 0.2025
        check("loop cost == hand-computed sum across rounds (0.2025)", abs(r["cost"] - 0.2025) < 1e-9, str(r["cost"]))
        check("loop tokens_in == 150000", r["tokens_in"] == 150_000, str(r["tokens_in"]))
        check("loop tokens_out == 1500", r["tokens_out"] == 1_500, str(r["tokens_out"]))
    finally:
        shutil.rmtree(clone, ignore_errors=True)


def gate_enabler_a_parallel(B, SR) -> None:
    print("\n-- 11) Enabler A: parallel blind scoring == sequential (honesty-preserving) --")
    questions_by_id = {
        "q1": {"id": "q1", "repo": "o/r", "answer_key": {"ground_truth_set": ["Alpha", "Beta", "Gamma"]}},
        "q2": {"id": "q2", "repo": "o/r", "answer_key": {"ground_truth_set": ["Delta", "Epsilon"]}},
    }
    answers = {"q1": "Alpha and Beta matter here.", "q2": "Delta, Epsilon."}
    reader = lambda rec: answers[rec["question_id"]]
    # A judge whose verdict varies with the answer content (so a swap would be detectable).
    def judge(prompt):
        wh = 1 if "Epsilon" in prompt else 0
        return json.dumps({"correctness_pct": 88 if "Alpha" in prompt else 77, "wrong_high": wh})

    def _cards():
        cards = []
        for qid in ("q1", "q2", "q1", "q2", "q1", "q2"):   # 6 cards -> real concurrency
            cards.append({"repo": "o/r", "question_id": qid, "arm": B.ARM_ON, "run": 1, "exit_code": 0,
                          "started_at": "t", "metrics": {k: None for k in B.METRIC_KEYS},
                          "answer_text_path": "p", "scored": False, "family": "f", "grep_can_do": False})
        return cards

    seq = SR.score_records(_cards(), questions_by_id, judge, reader, {"o/r": "s"}, max_workers=1)
    par = SR.score_records(_cards(), questions_by_id, judge, reader, {"o/r": "s"}, max_workers=4)

    def _fingerprint(cards):
        return [(c["question_id"], c["scored"], c["metrics"]["completeness_pct"],
                 c["metrics"]["correctness_pct"], c["metrics"]["wrong_high"]) for c in cards]

    check("parallel per-card verdicts == sequential (order-preserved)", _fingerprint(seq) == _fingerprint(par),
          f"seq={_fingerprint(seq)} par={_fingerprint(par)}")
    check("every card was scored under concurrency (no lost task)", all(c["scored"] for c in par))
    check("no cross-card contamination (q1 completeness != q2 completeness)",
          par[0]["metrics"]["completeness_pct"] != par[1]["metrics"]["completeness_pct"])
    # score_records without max_workers behaves exactly like the sequential path (backward compat).
    default = SR.score_records(_cards(), questions_by_id, judge, reader, {"o/r": "s"})
    check("default (no max_workers) == sequential (backward compatible signature)",
          _fingerprint(default) == _fingerprint(seq))


def gate_enabler_b_guardrail(B) -> None:
    print("\n-- 12) Enabler B: the pre-matrix spend guardrail refuses OpenRouter spend without confirm --")
    # No openrouter arms -> no OpenRouter spend -> the guardrail does not block (the ENGINE guard covers Claude).
    check("no --arm-model -> guardrail allows (no OpenRouter spend to confirm)",
          B.require_spend_confirmation(_Args()) is None)
    # OpenRouter arms requested WITHOUT an explicit confirm -> refuse (fail closed).
    reason = B.require_spend_confirmation(_Args(arm_model=["openai/gpt-5"]))
    check("openrouter arm + no --confirm-spend -> refuse (fail closed, returns a reason)",
          isinstance(reason, str) and reason, str(reason))
    # WITH the explicit confirm -> allowed.
    check("openrouter arm + --confirm-spend -> allowed",
          B.require_spend_confirmation(_Args(arm_model=["openai/gpt-5"], confirm_spend=True)) is None)
    # The projection is honest: a positive out-of-pocket range for a non-Claude arm.
    proj = B.cost_projection(_Args(arm_model=["openai/gpt-5"]), n_repos=5, n_questions=33, runs_per_arm=4)
    check("cost projection returns an out-of-pocket high > 0 for a non-Claude arm",
          isinstance(proj, dict) and proj.get("oop_high", 0) > 0, str(proj))
    check("cost projection names the OpenRouter arm model", "openai/gpt-5" in json.dumps(proj))


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 72)
    print("DeepMap — RM-2 OpenRouter ARM RUNNER gates (no key / no network / no Neo4j / no spend)")
    print("=" * 72)

    B = _load("dm_run_benchmark_arm", "run_benchmark.py")
    SR = _load("dm_score_run_arm", "score_run.py")

    # RED-first: arm_openrouter.py does not exist until Phase 2 is implemented -> this load fails.
    try:
        AO = _load("dm_arm_openrouter", "arm_openrouter.py")
    except Exception as exc:
        check("benchmark/arm_openrouter.py exists and imports", False, f"{type(exc).__name__}: {exc}")
        print("\n" + "=" * 72)
        print(f"RESULT: RED — arm runner absent ({type(exc).__name__}). Implement benchmark/arm_openrouter.py.")
        return 1

    check("benchmark/arm_openrouter.py exists and imports", True)

    gate_tool_specs(AO, B)
    gate_executor_sandbox(AO)
    gate_deepmap_routing(AO)
    gate_agentic_loop(AO, B)
    gate_loop_edge_cases(AO, B)
    gate_honest_failure(AO, B)
    gate_loop_fuzz(AO, B)
    gate_envelope_parity(AO, B)
    gate_record_shape_parity(AO, B)
    gate_money(AO, B, SR)
    gate_deepmap_transport_wire(AO)
    gate_chat_transport_request(AO, B)
    gate_dispatch(B)
    gate_cost_accounting(AO, B)
    gate_enabler_a_parallel(B, SR)
    gate_enabler_b_guardrail(B)

    print("\n" + "=" * 72)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
