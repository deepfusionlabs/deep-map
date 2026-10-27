#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DeepMap benchmark — the OpenRouter ARM RUNNER (cross-model arms; benchmark/README.md §7.5).

Lets a NON-Claude model (GPT-5, Gemini, …) be a benchmark ARM: an agentic tool-use loop over
OpenRouter's OpenAI-compatible /chat/completions, with the SAME with-vs-without contrast the Claude
arm uses — OFF = a fair grep baseline ({read_file, search_files}); ON = those PLUS the deepmap tools
the MCP servers register (search, impact, cypher, embed), routed over the live HTTP MCP servers (so
`build/retrieval.py` ranking is untouched).

THE PARITY KEYSTONE: the loop synthesizes a
byte-compatible `claude`-shaped result envelope and persists it as `stdout.json`, so the FROZEN blind
scorer (`score_completeness` / `score_correctness` / `score_run`) and every `run_benchmark` helper
(`parse_envelope`, `aggregate`, `_is_errored_run`, `build_record`, …) grade a non-Claude arm's answer
with ZERO edits. `run_one_openrouter` is a drop-in twin of `run_benchmark.run_one`: same record shape,
same per-run files, same `answer_text_path`.

HONESTY: a loop that hits a HARD cap (max_turns / budget / max_output_tokens) or a transport failure
returns `is_error=True` with an EMPTY answer — the errored run is EXCLUDED from medians by
`_is_errored_run` (because `is_error` rides in the envelope). A cap NEVER fabricates an answer.

Pure + injectable: the network transport (`transport`) and the deepmap tool transport
(`deepmap_transport`) are INJECTED, so the whole loop is unit-tested with scripted fakes — no key, no
network, no MCP servers, no Neo4j, no spend (tests/benchmark_arm_runner_gates.py).
"""
from __future__ import annotations

import fnmatch
import importlib.util
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

_HERE = Path(__file__).resolve().parent


def _load(fname: str):
    spec = importlib.util.spec_from_file_location(f"dm_arm_{Path(fname).stem}", _HERE / fname)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# The harness (parity contract) + the cost model (PRICES). Loaded once; the reused helpers are pure.
_B = _load("run_benchmark.py")
_CE = _load("cost_estimate.py")

# Defaults for the agentic loop (a real matrix passes explicit values). Kept modest so a runaway model
# can never spend unbounded — the HARD caps below are the honesty backstop, not a suggestion.
# DEFAULT_MAX_TURNS is env-overridable (DEEPMAP_ARM_MAX_TURNS): some models (e.g. GPT-5) explore far
# more turns than Claude before converging, so a too-low cap aborts them (max_turns errored runs,
# excluded from medians) and starves their data. Default stays 20 when the env is unset (no behavior
# change for existing callers/gates); a real matrix raises it (e.g. 40) to let exploratory models finish.
def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default


DEFAULT_MAX_TURNS = _env_int("DEEPMAP_ARM_MAX_TURNS", 20)
DEFAULT_MAX_OUTPUT_TOKENS = _env_int("DEEPMAP_ARM_MAX_OUTPUT_TOKENS", 16_000)
DEFAULT_TOOL_TIMEOUT_S = 30
BYTE_CAP = 64 * 1024                     # per read_file/search_files output cap (keeps context sane)
# Every tool the two MCP servers register — the Claude ON arm loads the real servers and sees all of them, so this
# arm offers the same set (tests/o11_openrouter_tool_parity_r283_gates.py derives it from the servers' source).
DEEPMAP_TOOLS = ("deepmap_search", "deepmap_impact", "deepmap_cypher", "deepmap_embed")
DEFAULT_PRICE = (3.0, 12.0)              # $/M (input, output) for an UNPRICED model — costs honestly

# The task framing is IDENTICAL for both arms — the ONLY difference is tool availability (§2.4 fairness).
_SYSTEM_PROMPT = (
    "You are a precise software-engineering agent answering a HARD question about a code repository "
    "you can explore ONLY through the provided tools. Investigate with the tools; when you are "
    "confident, reply with a plain-text answer and NO tool call — that plain reply is your FINAL answer. "
    "Cite concrete evidence as file:line. Be COMPLETE: enumerate every relevant symbol/call-site the "
    "question asks for. Do not guess — if the tools cannot establish something, say so explicitly."
)


def SYSTEM_PROMPT(arm: str) -> str:
    """The system prompt for an arm. IDENTICAL across ON/OFF (the fairness contract); `arm` is accepted
    for symmetry with the rest of the API but never changes the text."""
    return _SYSTEM_PROMPT


def _family(arm: str) -> str:
    """Strip a `@model` namespace suffix -> ARM_ON / ARM_OFF (mirrors run_benchmark.arm_family)."""
    return str(arm or "").split("@", 1)[0]


# --------------------------------------------------------------------------- #
# Tool specs — OpenAI `tools` array. OFF = the fair grep baseline; ON adds the deepmap tools.
# --------------------------------------------------------------------------- #

def _fn(name: str, description: str, params: Dict[str, Any]) -> Dict[str, Any]:
    return {"type": "function", "function": {"name": name, "description": description, "parameters": params}}


_READ_FILE = _fn(
    "read_file", "Read a UTF-8 text file from the repository (optionally a start_line..end_line range).",
    {"type": "object", "properties": {
        "path": {"type": "string", "description": "repository-relative path"},
        "start_line": {"type": "integer", "description": "1-based first line (optional)"},
        "end_line": {"type": "integer", "description": "1-based last line (optional)"}},
     "required": ["path"]})

_SEARCH_FILES = _fn(
    "search_files", "Regex-search the repository's files (the fair grep baseline available to BOTH arms).",
    {"type": "object", "properties": {
        "pattern": {"type": "string", "description": "a regular expression"},
        "glob": {"type": "string", "description": "optional filename glob, e.g. *.py"},
        "max_results": {"type": "integer", "description": "cap on matches (default 50)"}},
     "required": ["pattern"]})

# The deepmap tools MIRROR the live MCP tools the Claude ON arm sees (build/deepmap_search_server.py /
# build/deepmap_embed_server.py): the same parameters with the same defaults / caps (the literal fallback applies
# only when build/mcp_bounds.py cannot be read), and a faithful, compact description — each under a portable
# 1024-char function-description budget. `deepmap_impact` used to advertise a `depth` the server never had (silently
# dropped) and to omit `limit` / `edge_types`, describing a CALLS-only walk (ISS-benchmark:013), and the arm lacked
# deepmap_cypher after the server gained it; tests/o11_openrouter_tool_parity_r283_gates.py derives the tool list from
# the servers' registered functions and checks these schemas against their signatures.
def _mcp_bounds() -> Dict[str, int]:
    """TOP_K_DEFAULT / TOP_K_MAX / IMPACT_LIMIT_DEFAULT / IMPACT_LIMIT_MAX / QUERY_MAX_CHARS from build/mcp_bounds.py
    — the caps the real tools clamp to (it imports only os/sys) — else the shipped literal values. Never raises."""
    vals = {"TOP_K_DEFAULT": 15, "TOP_K_MAX": 50, "IMPACT_LIMIT_DEFAULT": 30, "IMPACT_LIMIT_MAX": 100,
            "QUERY_MAX_CHARS": 2000}
    try:
        spec = importlib.util.spec_from_file_location("dm_arm_mcp_bounds", _HERE.parent / "build" / "mcp_bounds.py")
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        for k in vals:
            v = getattr(m, k, None)
            if isinstance(v, int) and not isinstance(v, bool) and v > 0:
                vals[k] = v
    except Exception:  # noqa: BLE001 — a stripped tree keeps the literal caps
        pass
    return vals


_BOUNDS = _mcp_bounds()

_DEEPMAP_SEARCH = _fn(
    "deepmap_search",
    "Search this repository's code by what it DOES — intent and behaviour, not literal tokens: fused vector + BM25 + "
    "graph-weighted search over the DeepMap code graph, so it finds the implementation even when no word of the query "
    "appears in the code (grep stays right for a known exact token). Returns ranked entities {id, name, type, file, "
    "line, summary, confidence, score} across Function/Method/Class/Module/Route. For who-calls / blast-radius "
    f"questions use deepmap_impact. A query over {_BOUNDS['QUERY_MAX_CHARS']:,} characters is truncated.",
    {"type": "object", "properties": {
        "query": {"type": "string", "description": "what the code does, in natural language"},
        "top_k": {"type": "integer", "description": (f"results to return (default {_BOUNDS['TOP_K_DEFAULT']}, "
                                                     f"max {_BOUNDS['TOP_K_MAX']})")}},
     "required": ["query"]})

_DEEPMAP_IMPACT = _fn(
    "deepmap_impact",
    "What REACHES a symbol — multi-hop reverse-reachability (refactor impact / blast radius) across CALLS plus "
    "framework/runtime edges (routes, dependency injection, events, scheduled jobs, hooks, inheritance, cascades, "
    "template renders): the entry points a grep or a plain call graph can't see. Returns dependents {caller, "
    "caller_type, file, line, confidence, callee, hop, via, via_path}; confidence is the WEAKEST edge on the path "
    "(EXTRACTED / INFERRED / AMBIGUOUS — verify AMBIGUOUS by reading). Trailing {note} entries flag a symbol not in "
    "the graph, no resolved dependents, or a truncated walk.",
    {"type": "object", "properties": {
        "symbol": {"type": "string",
                   "description": "the symbol: foo, .foo, Class.method, Class#method, Owner::method or module.foo"},
        "limit": {"type": "integer", "description": (f"max dependents to return (default "
                                                     f"{_BOUNDS['IMPACT_LIMIT_DEFAULT']}, max "
                                                     f"{_BOUNDS['IMPACT_LIMIT_MAX']})")},
        "edge_types": {"type": "array", "items": {"type": "string"},
                       "description": ("optional edge types to walk; it REPLACES the default walk (CALLS is always "
                                       "kept). The default is CALLS, ROUTES_TO, DEPENDS_ON, TRIGGERS, SCHEDULED_AS, "
                                       "HOOKS, INHERITS, CASCADES, RENDERS; to ADD the ORM associations pass that "
                                       "whole list plus HAS_MANY and REFERENCES")}},
     "required": ["symbol"]})

def _cypher_bounds() -> Dict[str, int]:
    """LIMIT_DEFAULT / LIMIT_MAX from build/cypher_readonly.py — the row default / cap deepmap_cypher clamps to (it is
    stdlib-only and side-effect-free on import) — else the shipped literal values. Never raises."""
    vals = {"LIMIT_DEFAULT": 100, "LIMIT_MAX": 500}
    try:
        spec = importlib.util.spec_from_file_location("dm_arm_cypher_readonly",
                                                      _HERE.parent / "build" / "cypher_readonly.py")
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        for k in vals:
            v = getattr(m, k, None)
            if isinstance(v, int) and not isinstance(v, bool) and v > 0:
                vals[k] = v
    except Exception:  # noqa: BLE001 — a stripped tree keeps the literal caps
        pass
    return vals


_CYPHER = _cypher_bounds()

_DEEPMAP_CYPHER = _fn(
    "deepmap_cypher",
    "Read-only Cypher over this repository's DeepMap code graph, for ad-hoc STRUCTURE questions: list, count, group, "
    "find paths. Prefer deepmap_search (what code does) and deepmap_impact (what depends on X) when they fit. Nodes "
    ":Function :Method :Class :Module with key, name (methods: Class.method), source_file, start_line, body_summary, "
    "confidence. Links (a)-[:TYPE]->(b): CALLS, IMPORTS, INHERITS, IMPLEMENTS, ROUTES_TO, DEPENDS_ON, HOOKS, "
    "SCHEDULED_AS, TRIGGERS, DISPATCHES, CASCADES, HAS_MANY, REFERENCES, RENDERS, each with confidence EXTRACTED | "
    "INFERRED | AMBIGUOUS (AMBIGUOUS = the name matched several symbols: unverified, read the code). Example: MATCH "
    "(c:Class)<-[:INHERITS]-(s) RETURN c.name, count(s) AS n ORDER BY n DESC LIMIT 10. Writes, LOAD CSV, admin and "
    "apoc/gds/dbms procedures are rejected; at most `limit` rows and about 64 KB of text come back, without embedding "
    "vectors; trailing {note} entries are DeepMap's remarks.",
    {"type": "object", "properties": {
        "query": {"type": "string", "description": "ONE read-only Cypher query (starts with MATCH, OPTIONAL MATCH, "
                                                   "WITH, UNWIND, RETURN, CALL or SHOW)"},
        "params": {"type": "object", "description": ('optional values for the query\'s $parameters, e.g. '
                                                     '{"name": "save"} for $name')},
        "limit": {"type": "integer", "description": (f"max rows to return (default {_CYPHER['LIMIT_DEFAULT']}, "
                                                     f"max {_CYPHER['LIMIT_MAX']})")}},
     "required": ["query"]})

_DEEPMAP_EMBED = _fn(
    "deepmap_embed",
    "Embed text as a 768-d query vector (the same 'search_query: ' embedding deepmap_search uses) for semantic code "
    f"retrieval. Text longer than {_BOUNDS['QUERY_MAX_CHARS']:,} characters is truncated.",
    {"type": "object", "properties": {"text": {"type": "string", "description": "the text to embed"}},
     "required": ["text"]})


def tool_specs(arm: str) -> List[Dict[str, Any]]:
    """The OpenAI `tools` array for an arm. OFF = {read_file, search_files} EXACTLY (the fair grep
    baseline — never a deepmap tool, the programmatic analog of assert_off_arm_isolated); ON adds the
    deepmap tools the MCP servers register (DEEPMAP_TOOLS: search, impact, cypher, embed). Accepts a namespaced arm
    (`deepmap_off@model`) and resolves it to its family."""
    base = [_READ_FILE, _SEARCH_FILES]
    if _family(arm) == _B.ARM_ON:
        return base + [_DEEPMAP_SEARCH, _DEEPMAP_IMPACT, _DEEPMAP_CYPHER, _DEEPMAP_EMBED]
    return base


# --------------------------------------------------------------------------- #
# Local tool executors — sandboxed to the clone. NEVER touch the host FS.
# --------------------------------------------------------------------------- #

def _resolve_in_clone(repo_dir: Any, rel: Any) -> Path:
    """Resolve `rel` inside `repo_dir` and assert the result stays inside it. RAISES on a `..` escape,
    an absolute host path, or a symlink pointing out (resolve() follows symlinks). This is the sandbox
    primitive — the executors wrap it so a violation becomes a tool-error string, never a host read."""
    base = Path(repo_dir).resolve()
    if rel is None or str(rel).strip() == "":
        raise ValueError("no path given")
    target = (base / str(rel)).resolve()
    if target != base and base not in target.parents:
        raise ValueError(f"path {rel!r} escapes the clone sandbox")
    return target


def exec_read_file(repo_dir: Any, path: Any, start_line: Optional[int] = None,
                   end_line: Optional[int] = None) -> str:
    """Read a UTF-8 file INSIDE the clone (optionally a line range). Any failure (escape, missing file,
    decode) returns a structured tool-error STRING — never raises, never reads outside the clone."""
    try:
        target = _resolve_in_clone(repo_dir, path)
    except Exception as exc:
        return json.dumps({"error": f"read_file: {exc}"})
    try:
        data = target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return json.dumps({"error": f"read_file: {type(exc).__name__}: {exc}"})
    if start_line or end_line:
        lines = data.splitlines()
        s = max(0, (int(start_line) - 1)) if start_line else 0
        e = int(end_line) if end_line else len(lines)
        data = "\n".join(lines[s:e])
    b = data.encode("utf-8")
    if len(b) > BYTE_CAP:
        data = b[:BYTE_CAP].decode("utf-8", errors="replace") + "\n…[truncated]"
    return data


def exec_search_files(repo_dir: Any, pattern: Any, glob: Optional[str] = None,
                      max_results: int = 50) -> str:
    """Regex-search files inside the clone. Prefers `rg` (argv list, cwd=clone, never shell=True) and
    falls back to os.walk + Python `re` when rg is absent (CI). A bad regex returns a tool-error STRING
    (never raises). Results are `relpath:line:text`, capped at `max_results` and BYTE_CAP-limited."""
    if not pattern:
        return json.dumps({"error": "search_files: no pattern given"})
    # Pre-validate with Python re so a clearly-bad pattern is a deterministic tool-error regardless of
    # whether rg (Rust regex) or the fallback runs — makes the sandbox behaviour environment-independent.
    try:
        rx = re.compile(str(pattern))
    except re.error as exc:
        return json.dumps({"error": f"search_files: bad regex: {exc}"})

    base = Path(repo_dir).resolve()
    cap = max(1, int(max_results or 50))

    rg = shutil.which("rg")
    if rg:
        argv = [rg, "--line-number", "--no-heading", "--color", "never", "-m", str(cap)]
        if glob:
            argv += ["--glob", str(glob)]
        argv += ["--", str(pattern)]
        try:
            p = subprocess.run(argv, cwd=str(base), capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=30)
            if p.returncode in (0, 1):     # 0 = matches, 1 = no matches — both are clean results
                lines = [ln for ln in (p.stdout or "").splitlines() if ln][:cap]
                out = "\n".join(lines) if lines else json.dumps({"matches": 0})
                return out[:BYTE_CAP]
            # rg returned an error code (2) — fall through to the Python fallback rather than leaking it.
        except (OSError, subprocess.TimeoutExpired):
            pass

    hits: List[str] = []
    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", ".deepmap", "node_modules")]
        for name in files:
            if glob and not fnmatch.fnmatch(name, str(glob)):
                continue
            fp = Path(root) / name
            try:
                for i, ln in enumerate(fp.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                    if rx.search(ln):
                        rel = fp.relative_to(base).as_posix()
                        hits.append(f"{rel}:{i}:{ln.strip()[:200]}")
                        if len(hits) >= cap:
                            return "\n".join(hits)[:BYTE_CAP]
            except OSError:
                continue
    return ("\n".join(hits) if hits else json.dumps({"matches": 0}))[:BYTE_CAP]


# --------------------------------------------------------------------------- #
# The deepmap tool transport — over the live HTTP MCP servers (the SAME path the Claude ON arm uses).
# --------------------------------------------------------------------------- #

def default_deepmap_transport(name: str, arguments: Dict[str, Any], *, host: Optional[str] = None,
                              ports: Optional[Dict[str, int]] = None, timeout_s: int = 30) -> str:
    """Call a deepmap_* tool over the HTTP MCP servers (search/impact → :8787/mcp, embed → :8788/mcp) as
    a single stateless JSON-RPC `tools/call` POST (stateless_http=True on the FastMCP servers — no
    handshake). Returns the serialized `content` text the model reads. RAISES on an HTTP/shape error so
    execute_tool turns it into an honest tool-error (a single tool miss never fails the whole run)."""
    import urllib.request
    if host is None or ports is None:
        h, p = _B._mcp_endpoints()
        host = host or h
        ports = ports or p
    port = ports["deepmap-embed"] if name == "deepmap_embed" else ports["deepmap-search"]
    url = f"http://{host}:{port}/mcp"
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": name, "arguments": arguments}}).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if isinstance(data, dict) and data.get("error"):
        raise RuntimeError(f"deepmap MCP error: {data['error']}")
    result = (data or {}).get("result") or {}
    content = result.get("content") or []
    texts = [c.get("text", "") for c in content if isinstance(c, dict)]
    return "\n".join(texts) if texts else json.dumps(result)


def execute_tool(name: Optional[str], arguments: Any, *, repo_dir: Any,
                 deepmap_transport: Callable[[str, Dict[str, Any]], Any],
                 tool_timeout_s: int = DEFAULT_TOOL_TIMEOUT_S) -> str:
    """Single tool dispatch. `read_file`/`search_files` run locally (sandboxed); `deepmap_*` route to
    the injected transport. ANY executor exception (incl. a transport raise) is caught and returned as a
    json tool-error STRING — the model can react to it, and a single tool miss never crashes the run."""
    args = arguments if isinstance(arguments, dict) else {}
    try:
        if name == "read_file":
            return exec_read_file(repo_dir, args.get("path"), args.get("start_line"), args.get("end_line"))
        if name == "search_files":
            return exec_search_files(repo_dir, args.get("pattern"), args.get("glob"),
                                     args.get("max_results", 50))
        if name in DEEPMAP_TOOLS:
            res = deepmap_transport(name, args)
            return res if isinstance(res, str) else json.dumps(res)
        return json.dumps({"error": f"unknown tool {name!r}"})
    except Exception as exc:   # noqa: BLE001 — an honest tool-error, never a crashed run
        return json.dumps({"error": f"{name}: {type(exc).__name__}: {exc}"})


# --------------------------------------------------------------------------- #
# Cost accounting — Σ over rounds from cost_estimate.PRICES (prefer a native usage.cost if present).
# --------------------------------------------------------------------------- #

def _prices() -> Dict[str, tuple]:
    return dict(getattr(_CE, "PRICES", {}))


def model_priced(model: str) -> bool:
    """True iff `model` has a pinned row in cost_estimate.PRICES (so a gate can flag a missing one)."""
    return model in _prices()


def _safe_int(v: Any) -> int:
    """Coerce an untrusted usage value to a NON-negative int; anything malformed/None -> 0. Model output
    is untrusted (a token field can be a string, None, or negative), so token math must never raise."""
    if isinstance(v, bool):   # bool is an int subclass — a stray True/False is not a token count
        return 0
    try:
        n = int(v)
    except (TypeError, ValueError):
        return 0
    return n if n >= 0 else 0


def _as_text(v: Any) -> str:
    """Coerce an answer/content value to a string (a model may return null or a non-string content)."""
    return "" if v is None else (v if isinstance(v, str) else str(v))


def price_of(model: str, usage: Optional[Dict[str, Any]]) -> float:
    """Cost of one round: prompt/1e6*in + completion/1e6*out from PRICES (default (3.0,12.0) for an
    unpriced model — still costs honestly). Prefers a native `usage.cost` when OpenRouter returns one.
    Robust to untrusted usage shapes (non-dict / non-numeric fields) — never raises."""
    u = usage if isinstance(usage, dict) else {}
    native = u.get("cost")
    if isinstance(native, (int, float)) and not isinstance(native, bool):
        return float(native) if native >= 0 else 0.0
    pin, pout = _prices().get(model, DEFAULT_PRICE)
    return _safe_int(u.get("prompt_tokens")) / 1e6 * pin + _safe_int(u.get("completion_tokens")) / 1e6 * pout


def _tolerant_json(raw: Any) -> Dict[str, Any]:
    """Parse a tool-call's `arguments` (a JSON string) into a dict. A malformed/absent value becomes a
    dict carrying the raw text so the executor produces an honest tool-error, never a crash."""
    if isinstance(raw, dict):
        return raw
    if raw is None or raw == "":
        return {}
    try:
        v = json.loads(raw)
        return v if isinstance(v, dict) else {"_value": v}
    except (json.JSONDecodeError, TypeError):
        return {"_malformed_arguments": str(raw)}


# --------------------------------------------------------------------------- #
# The agentic loop (§2.4).
# --------------------------------------------------------------------------- #

def _result(answer: str, tool_calls: int, tok_in: int, tok_out: int, cost: float,
            is_error: bool, stop: str) -> Dict[str, Any]:
    return {"answer": answer or "", "tool_calls": tool_calls, "tokens_in": tok_in,
            "tokens_out": tok_out, "cost": round(cost, 6), "is_error": is_error, "stop": stop}


def run_agentic_loop(*, model: str, arm: str, question: Dict[str, Any], repo_dir: Any,
                     transport: Callable[[List[dict], List[dict]], Dict[str, Any]],
                     deepmap_transport: Callable[[str, Dict[str, Any]], Any],
                     max_turns: int = DEFAULT_MAX_TURNS,
                     max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
                     budget_usd: float = 5.0,
                     tool_timeout_s: int = DEFAULT_TOOL_TIMEOUT_S) -> Dict[str, Any]:
    """Run the OpenAI-style tool-use loop for ONE (model, arm, question). `transport(messages, tools)`
    (the network) and `deepmap_transport(name, args)` (the MCP tools) are INJECTED so the loop is
    fully unit-testable with scripted fakes. Returns a result dict {answer, tool_calls, tokens_in,
    tokens_out, cost, is_error, stop}. HARD caps (max_turns / budget / max_output_tokens) end the loop
    with is_error=True and an EMPTY answer — never a fabricated one. `transport` may raise (a network
    failure); the caller (run_one_openrouter) turns that into an errored run."""
    messages: List[dict] = [{"role": "system", "content": SYSTEM_PROMPT(arm)},
                            {"role": "user", "content": question.get("prompt", "")}]
    tools = tool_specs(arm)
    tool_calls = tok_in = tok_out = 0
    cost = 0.0

    for _turn in range(max(1, int(max_turns))):
        resp = transport(messages, tools)                     # may raise -> caller marks the run errored
        # Model/API output is UNTRUSTED — every field below is defended so a malformed shape degrades
        # gracefully (a skipped tool call, an errored run at a cap) rather than raising mid-loop.
        if not isinstance(resp, dict):
            resp = {}
        usage = resp.get("usage")
        if not isinstance(usage, dict):
            usage = {}
        tok_in += _safe_int(usage.get("prompt_tokens"))
        tok_out += _safe_int(usage.get("completion_tokens"))
        cost += price_of(model, usage)

        choices = resp.get("choices")
        first = choices[0] if (isinstance(choices, list) and choices and isinstance(choices[0], dict)) else {}
        msg = first.get("message")
        if not isinstance(msg, dict):
            msg = {}
        messages.append(msg)
        tcs = msg.get("tool_calls")
        if not isinstance(tcs, list):                         # a non-list tool_calls (dict/str/None) == none
            tcs = []

        if not tcs:                                           # no tool call == the FINAL answer
            return _result(_as_text(msg.get("content")), tool_calls, tok_in, tok_out, cost, False, "final")

        for tc in tcs:
            if not isinstance(tc, dict):                      # skip a malformed tool-call entry, never crash
                continue
            tool_calls += 1
            fn = tc.get("function")
            if not isinstance(fn, dict):
                fn = {}
            args = _tolerant_json(fn.get("arguments"))
            out = execute_tool(fn.get("name"), args, repo_dir=repo_dir,
                               deepmap_transport=deepmap_transport, tool_timeout_s=tool_timeout_s)
            messages.append({"role": "tool", "tool_call_id": tc.get("id"), "content": out})

        if cost >= budget_usd:
            return _result("", tool_calls, tok_in, tok_out, cost, True, "budget")
        if tok_out >= max_output_tokens:
            return _result("", tool_calls, tok_in, tok_out, cost, True, "max_output_tokens")

    return _result("", tool_calls, tok_in, tok_out, cost, True, "max_turns")


# --------------------------------------------------------------------------- #
# Envelope synthesis + the drop-in run_one twin (the parity keystone, §2.1).
# --------------------------------------------------------------------------- #

def build_openrouter_envelope(result: Dict[str, Any], model: str) -> Dict[str, Any]:
    """Synthesize a byte-compatible `claude --output-format json` result envelope from a loop result, so
    parse_envelope + the whole scoring pipeline consume it unchanged (§2.1). `subtype` is 'success' for a
    final answer else the stop reason; `is_error` rides the envelope; audit-only extras are `_`-prefixed."""
    stop = result.get("stop") or ("final" if not result.get("is_error") else "error")
    return {
        "type": "result",
        "subtype": "success" if stop == "final" else stop,
        "is_error": bool(result.get("is_error")),
        "num_turns": result.get("tool_calls", 0),
        "result": result.get("answer") or "",
        "total_cost_usd": result.get("cost", 0.0),
        "usage": {"input_tokens": result.get("tokens_in", 0),
                  "output_tokens": result.get("tokens_out", 0)},
        "_arm_runner": "openrouter",
        "_model": model,
        "_stop_reason": stop,
    }


def _openrouter_chat_transport(*, model: str, api_key: Optional[str], timeout_s: int,
                               max_tokens: int = 4096):
    """The REAL network transport: a stdlib-urllib POST to OPENROUTER_URL with
    {model, messages, tools, tool_choice:'auto', max_tokens}. Bound to `model` at construction (the loop
    calls it as transport(messages, tools)). RAISES on a missing key / HTTP / no-choices error → the run
    is marked errored. Not exercised in the no-network gates (injected fakes cover the loop)."""
    def transport(messages: List[dict], tools: List[dict]) -> Dict[str, Any]:
        import urllib.request
        if not api_key:
            raise RuntimeError("OpenRouter arm needs OPENROUTER_API_KEY in the env")
        payload = {"model": model, "messages": messages, "tools": tools,
                   "tool_choice": "auto", "max_tokens": max_tokens}
        req = urllib.request.Request(
            _B.OPENROUTER_URL, data=json.dumps(payload).encode("utf-8"), method="POST",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
                     "HTTP-Referer": "https://github.com/deepfusionlabs/deep-map",
                     "X-Title": "DeepMap benchmark arm"})
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if not (data or {}).get("choices"):
            raise RuntimeError(f"openrouter returned no choices: {str(data)[:160]}")
        return data
    return transport


def run_one_openrouter(model: str, arm: str, question: Dict[str, Any], repo_dir: Any, run_idx: int,
                       out_dir: Any, budget_usd: float, timeout_s: int,
                       transport=None, deepmap_transport=None,
                       max_turns: int = DEFAULT_MAX_TURNS,
                       max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS) -> Dict[str, Any]:
    """A DROP-IN twin of run_benchmark.run_one for an OpenRouter arm. Runs the agentic loop, synthesizes
    a byte-compatible envelope, persists the SAME per-run files (stdout.json = the file the scorer reads,
    stderr.txt, argv.json, record.json), and returns a record with the EXACT key set run_one writes. The
    record `arm` is NAMESPACED (`deepmap_on@<model>`) so run_output_dir writes a distinct subtree and
    aggregate groups per (repo, question, arm-model). Score fields are None — the blind Stage-2 pass fills
    them. `transport`/`deepmap_transport` are injected for testing; absent, the real ones are built."""
    namespaced = f"{arm}@{model}"
    run_dir = _B.run_output_dir(out_dir, question, namespaced, run_idx)
    run_dir.mkdir(parents=True, exist_ok=True)

    if transport is None:
        transport = _openrouter_chat_transport(model=model, api_key=os.environ.get("OPENROUTER_API_KEY"),
                                               timeout_s=timeout_s)
    if deepmap_transport is None:
        deepmap_transport = default_deepmap_transport

    started = time.monotonic()
    started_iso = _B._utcnow()
    try:
        result = run_agentic_loop(
            model=model, arm=arm, question=question, repo_dir=Path(repo_dir),
            transport=transport, deepmap_transport=deepmap_transport,
            max_turns=max_turns, max_output_tokens=max_output_tokens,
            budget_usd=budget_usd, tool_timeout_s=min(DEFAULT_TOOL_TIMEOUT_S, timeout_s))
    except Exception as exc:   # noqa: BLE001 — a transport/network failure is an errored run, never fabricated
        result = {"answer": "", "tool_calls": 0, "tokens_in": 0, "tokens_out": 0, "cost": 0.0,
                  "is_error": True, "stop": f"transport_error:{type(exc).__name__}", "_error": str(exc)}
    wall = round(time.monotonic() - started, 3)

    env = build_openrouter_envelope(result, model)
    (run_dir / "stdout.json").write_text(json.dumps(env), encoding="utf-8")
    (run_dir / "stderr.txt").write_text(
        (result.get("_error") or "") + f"\nstop={result.get('stop')} model={model} arm={namespaced}",
        encoding="utf-8")
    (run_dir / "argv.json").write_text(
        json.dumps({"arm_runner": "openrouter", "model": model, "arm": namespaced,
                    "cwd": str(repo_dir), "stop": result.get("stop")}, indent=2), encoding="utf-8")

    metrics = _B.parse_envelope(json.dumps(env))
    metrics["wall_clock_s"] = wall
    # The loop always "exits 0" (it ran to completion); an honest failure rides via _is_error in the
    # envelope (a budget/max-turns abort), EXACTLY like a `claude` is_error:true at exit 0 (run_one).
    metrics["_exit_code"] = 0
    for sk in _B.SCORE_KEYS:
        metrics[sk] = None

    record = {
        "repo": question["repo"],
        "question_id": question["id"],
        "family": question.get("family"),
        "grep_can_do": question.get("grep_can_do"),
        "arm": namespaced,
        "run": run_idx,
        "exit_code": 0,
        "started_at": started_iso,
        "metrics": {
            **{k: metrics.get(k) for k in _B.METRIC_KEYS},
            **{k: metrics.get(k) for k in ("_is_error", "_subtype", "_exit_code")},
        },
        "answer_text_path": str((run_dir / "stdout.json").relative_to(_B.RESULTS_ROOT.parent)),
        "scored": False,   # blind Stage-2 flips this
    }
    (run_dir / "record.json").write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
    return record
