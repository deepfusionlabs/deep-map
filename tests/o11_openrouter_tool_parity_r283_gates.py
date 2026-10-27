#!/usr/bin/env python3
"""0.28.3 (O11) — the non-Claude ON arm is offered the SAME deepmap tools the Claude ON arm gets over MCP.

Finding (DeepInit refresh 2026-09-29, validated) — ISS-benchmark:013 (D18): benchmark/arm_openrouter.py hand-writes the
OpenAI `tools` schema of the deepmap tools. Its `deepmap_impact` advertised `{symbol, depth}` and described a
"Reverse-CALLS blast radius", while the live server (build/deepmap_search_server.py) takes `symbol, limit, edge_types`
and walks :CALLS PLUS the typed framework/runtime edges. The server never had `depth` (it is silently dropped by the
tool's argument model), so a GPT/Gemini ON arm could never set `limit` or `edge_types` — a cross-model parity gap the
runner's own contract ("the SAME with-vs-without contrast the Claude arm uses") forbids. Nothing checked the schema
against the server.

Follow-up (2026-10-23): the search server gained deepmap_cypher; the Claude ON arm loads the real server and saw it at
once, but the OpenRouter arm's hand-kept list (search, impact, embed) did not — the same gap, one tool later. The tool
list is therefore no longer typed into this gate: it is DERIVED from the functions the two MCP servers register as
tools (mcp_bounds.bounded_tool), so the next new tool fails here instead of silently breaking parity.

Locks (hermetic: the servers are read with `ast`, never imported or started; the MCP transport is a recorder):
  - the deepmap tools the arm offers (DEEPMAP_TOOLS, the ON arm's tool_specs, the names execute_tool routes) are
    EXACTLY the tool functions of build/deepmap_search_server.py + build/deepmap_embed_server.py, and each is sent to
    the server that registers it,
  - for each of them: the offered parameters are EXACTLY the server function's parameters, `required` is exactly
    those without a default, and each JSON type matches the annotation (str -> string, int -> integer, dict ->
    object, list[str] -> array of string),
  - deepmap_impact: no `depth`; `limit` / `edge_types` offered with the server's real default / cap
    (build/mcp_bounds.py); the description is the multi-hop CALLS + framework/runtime-edge walk, not CALLS-only,
  - deepmap_cypher: `limit` states the real default / cap (build/cypher_readonly.py); the description says it is
    read-only, when to prefer search / impact, the schema and the caps,
  - every deepmap tool description fits a portable 1024-char function-description budget,
  - execute_tool forwards each tool's arguments to the MCP transport unchanged.

Usage: python tests/o11_openrouter_tool_parity_r283_gates.py [--root <tree>]
"""
from __future__ import annotations

import ast
import importlib.util
import json
import sys
import tempfile
import urllib.request
from pathlib import Path

sys.dont_write_bytecode = True     # never drop __pycache__ into a --root snapshot

REPO = Path(__file__).resolve().parent.parent
PASSES: list[str] = []
FAILS: list[str] = []
SERVERS = ("deepmap_search_server.py", "deepmap_embed_server.py")
_TYPES = {"str": "string", "int": "integer", "float": "number", "bool": "boolean", "dict": "object"}
# A function the servers register as an MCP tool is decorated with one of these (every DeepMap tool must go through
# mcp_bounds.bounded_tool — CLAUDE.md; a bare FastMCP @mcp.tool would still be a tool the Claude arm sees).
_TOOL_DECORATORS = ("mcp_bounds.bounded_tool", "bounded_tool", "mcp.tool")


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSES if ok else FAILS).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok or not detail else f" — {detail}"))


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _registered(fn: ast.FunctionDef) -> bool:
    for d in fn.decorator_list:
        target = d.func if isinstance(d, ast.Call) else d
        if ast.unparse(target) in _TOOL_DECORATORS:
            return True
    return False


def server_tools(root: Path) -> dict:
    """{tool: server file} — every function the two MCP servers register as a tool, read with ast (never imported)."""
    out: dict = {}
    for fname in SERVERS:
        tree = ast.parse((root / "build" / fname).read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and _registered(node):
                out[node.name] = fname
    return out


def server_signatures(root: Path, tools: dict) -> dict:
    """{tool: {param: (required, annotation)}} read from the REAL server functions with ast (never imported)."""
    out: dict = {}
    for tool, fname in tools.items():
        tree = ast.parse((root / "build" / fname).read_text(encoding="utf-8"))
        fn = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == tool), None)
        if fn is None:
            continue
        args = fn.args.args
        n_req = len(args) - len(fn.args.defaults)
        out[tool] = {a.arg: (i < n_req, ast.unparse(a.annotation) if a.annotation is not None else "")
                     for i, a in enumerate(args)}
    return out


def _json_type(annotation: str) -> "tuple[str | None, str | None]":
    """(JSON type, array item type) for a server annotation."""
    a = annotation.replace(" ", "")
    if a.startswith("list[") or a.startswith("List["):
        inner = a[5:].split("]", 1)[0]
        return "array", _TYPES.get(inner)
    head = a.split("|", 1)[0]
    return _TYPES.get(head.split("[", 1)[0]), None


class _Resp:
    """A recorder for urllib.request.urlopen: which port a deepmap tool call was POSTed to."""
    def __init__(self, url: str, hit: dict):
        hit["port"] = int(url.split("://", 1)[1].split("/", 1)[0].rsplit(":", 1)[1])

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return json.dumps({"result": {"content": [{"type": "text", "text": "x"}]}}).encode()


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
    print(f"DeepMap — OpenRouter ON arm: deepmap tool schemas == the real MCP tools (root: {root})")
    print("=" * 72)
    AO = _load(root / "benchmark" / "arm_openrouter.py", "o11_arm_openrouter")
    MB = _load(root / "build" / "mcp_bounds.py", "o11_mcp_bounds")
    CR = _load(root / "build" / "cypher_readonly.py", "o11_cypher_readonly")
    tools = server_tools(root)
    sigs = server_signatures(root, tools)

    print("\n-- the arm offers exactly the tools the MCP servers register (derived, not a hand-kept list) --")
    check("(premise) the servers register at least search, impact and embed",
          {"deepmap_search", "deepmap_impact", "deepmap_embed"} <= set(tools), str(sorted(tools)))
    check("(premise) every registered tool's signature was read", set(sigs) == set(tools), str(sorted(sigs)))
    on = [t["function"]["name"] for t in AO.tool_specs(AO._B.ARM_ON)]
    offered = {n for n in on if n.startswith("deepmap_")}
    check(f"the ON arm's tool_specs offer exactly the server tools {sorted(tools)}", offered == set(tools),
          f"offered {sorted(offered)}; missing {sorted(set(tools) - offered)}; extra {sorted(offered - set(tools))}")
    check("DEEPMAP_TOOLS (what execute_tool routes to the MCP servers) == the server tools",
          set(AO.DEEPMAP_TOOLS) == set(tools), f"{sorted(AO.DEEPMAP_TOOLS)} vs {sorted(tools)}")
    check("each tool is offered once", len(on) == len(set(on)), str(on))
    off = {t["function"]["name"] for t in AO.tool_specs(AO._B.ARM_OFF)}
    check("the OFF arm still offers no deepmap tool", not any(n.startswith("deepmap_") for n in off), str(sorted(off)))
    specs = {t["function"]["name"]: t["function"] for t in AO.tool_specs(AO._B.ARM_ON)}

    print("\n-- parameters, required set and JSON types mirror the server functions --")
    for tool, params in sorted(sigs.items()):
        fn = specs.get(tool) or {}
        schema = fn.get("parameters") or {}
        props = schema.get("properties") or {}
        check(f"{tool}: offered parameters == the server's ({', '.join(params)})", set(props) == set(params),
              f"offered {sorted(props)}")
        want_req = sorted(p for p, (req, _a) in params.items() if req)
        check(f"{tool}: required == the parameters without a default ({want_req})",
              sorted(schema.get("required") or []) == want_req, str(schema.get("required")))
        for p, (_req, ann) in params.items():
            jt, item = _json_type(ann)
            got = props.get(p) or {}
            ok = jt is not None and got.get("type") == jt and (item is None or (got.get("items") or {}).get("type") == item)
            check(f"{tool}.{p}: JSON type matches the annotation {ann!r} ({jt}{'/' + item if item else ''})", ok,
                  str(got))

    print("\n-- deepmap_impact says what the server does --")
    imp = specs.get("deepmap_impact") or {}
    props = (imp.get("parameters") or {}).get("properties") or {}
    desc = imp.get("description") or ""
    check("no `depth` parameter (the server has none — it was silently dropped)", "depth" not in props, str(sorted(props)))
    lim = (props.get("limit") or {}).get("description", "")
    check(f"`limit` states the server's default {MB.IMPACT_LIMIT_DEFAULT} and cap {MB.IMPACT_LIMIT_MAX}",
          str(MB.IMPACT_LIMIT_DEFAULT) in lim and str(MB.IMPACT_LIMIT_MAX) in lim, lim)
    et = (props.get("edge_types") or {}).get("description", "")
    check("`edge_types` says it REPLACES the default walk (CALLS always kept)", "replace" in et.lower()
          and "CALLS" in et, et)
    check("the description is the CALLS + framework/runtime-edge walk, not a CALLS-only one",
          "Reverse-CALLS" not in desc and "CALLS" in desc and "framework" in desc.lower(), desc)
    top = (((specs.get("deepmap_search") or {}).get("parameters") or {}).get("properties") or {}).get("top_k") or {}
    check(f"deepmap_search `top_k` states the server's default {MB.TOP_K_DEFAULT} and cap {MB.TOP_K_MAX}",
          str(MB.TOP_K_DEFAULT) in top.get("description", "") and str(MB.TOP_K_MAX) in top.get("description", ""),
          str(top))

    if "deepmap_cypher" in tools:
        print("\n-- deepmap_cypher says what the server does --")
        cy = specs.get("deepmap_cypher") or {}
        cprops = (cy.get("parameters") or {}).get("properties") or {}
        cdesc = cy.get("description") or ""
        clim = (cprops.get("limit") or {}).get("description", "")
        check(f"`limit` states the server's default {CR.LIMIT_DEFAULT} and cap {CR.LIMIT_MAX} (cypher_readonly)",
              str(CR.LIMIT_DEFAULT) in clim and str(CR.LIMIT_MAX) in clim, clim)
        check("`params` says its values bind the query's $parameters",
              "$" in (cprops.get("params") or {}).get("description", ""), str(cprops.get("params")))
        low = cdesc.lower()
        check("the description says read-only, names deepmap_search / deepmap_impact, and that AMBIGUOUS is unverified",
              "read-only" in low and "deepmap_search" in cdesc and "deepmap_impact" in cdesc and "AMBIGUOUS" in cdesc
              and "unverified" in low, cdesc)
        check("...the schema an agent needs (labels, key properties, CALLS, confidence)",
              all(w in cdesc for w in (":Function", "key", "source_file", "start_line", "CALLS", "confidence")), cdesc)
        check(f"...and the caps the server applies ({CR.LIMIT_MAX} rows, about 64 KB of text)",
              str(CR.LIMIT_MAX) in clim and "64 KB" in cdesc, cdesc)

    for tool in sorted(tools):
        d = (specs.get(tool) or {}).get("description") or ""
        check(f"{tool}: description fits a portable 1024-char function-description budget ({len(d)})",
              0 < len(d) <= 1024, "")

    print("\n-- the arguments reach the MCP server unchanged --")
    seen: dict = {}

    def transport(name, args):
        seen.update(name=name, args=dict(args))
        return "ok"

    with tempfile.TemporaryDirectory() as td:
        args = {"symbol": "charge", "limit": 60, "edge_types": ["CALLS", "ROUTES_TO", "HAS_MANY"]}
        out = AO.execute_tool("deepmap_impact", dict(args), repo_dir=td, deepmap_transport=transport)
        check("execute_tool forwards symbol / limit / edge_types to the deepmap transport verbatim",
              out == "ok" and seen.get("name") == "deepmap_impact" and seen.get("args") == args, str(seen))
        for tool in sorted(tools):
            seen.clear()
            args = {"query": "MATCH (f:Function {name: $n}) RETURN f.key", "params": {"n": "save"}, "limit": 20}
            out = AO.execute_tool(tool, dict(args), repo_dir=td, deepmap_transport=transport)
            check(f"execute_tool routes {tool} to the deepmap transport with its arguments verbatim",
                  out == "ok" and seen.get("name") == tool and seen.get("args") == args, str(seen) or out)

    ports = {"deepmap-search": 18787, "deepmap-embed": 18788}
    hit: dict = {}
    saved = urllib.request.urlopen
    urllib.request.urlopen = lambda req, timeout=None: _Resp(req.full_url, hit)
    try:
        for tool, fname in sorted(tools.items()):
            hit.clear()
            AO.default_deepmap_transport(tool, {}, host="127.0.0.1", ports=ports)
            want = ports["deepmap-embed" if fname == "deepmap_embed_server.py" else "deepmap-search"]
            check(f"{tool} is POSTed to the server that registers it ({fname}, port {want})", hit.get("port") == want,
                  str(hit))
    finally:
        urllib.request.urlopen = saved

    total = len(PASSES) + len(FAILS)
    print("\n" + "=" * 72)
    if FAILS:
        print(f"RESULT: RED — {len(PASSES)}/{total} gates passed.")
        return 1
    print(f"RESULT: GREEN — {len(PASSES)}/{total} gates passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
