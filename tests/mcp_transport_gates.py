#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j, no-network gates for the MCP HTTP transport + provisioner wiring.

Locks the load-bearing invariants of the streamable-HTTP migration (the fix for the Windows
stdio hang; see build/_mcp_transport.py) so a regression goes RED in the
offline gate (tests/run_all.py), long before a live reload:

  * transport resolver: every alias + safe fallback + the documented ``stdio`` default;
  * both MCP servers register exactly their frozen tool (deepmap_search / deepmap_embed);
  * provisioner registry/helpers are coherent (distinct ports, real scripts, both conf fixes);
  * **the .mcp.json setup writes (provision.write_mcp_json, in a throwaway project) carries the
    provisioner's computed HTTP URLs** (config↔code drift guard); the plugin itself ships NO root
    .mcp.json (tests/plugin_manifest_gates.py's rule; its .gitignore half is re-checked here).

Pure: imports modules, reads files and writes only inside a temp dir (plus a read-only `git ls-files` and a
`git init` of that temp dir); never connects to Neo4j, never opens a socket, never spawns a server. Exit 0 = all
gates green.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
sys.path.insert(0, str(BUILD))

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


def _load(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def gate_transport_resolver() -> None:
    print("\n-- transport resolver --")
    T = _load(BUILD / "_mcp_transport.py")
    cases = {
        "stdio": "stdio", "http": "streamable-http", "streamable-http": "streamable-http",
        "streamable_http": "streamable-http", "streamablehttp": "streamable-http",
        "STREAMABLE-HTTP": "streamable-http", " sse ": "sse", "sse": "sse",
        "garbage": "stdio", "": "stdio",
    }
    for raw, want in cases.items():
        got = T.resolve_transport({"DEEPMAP_MCP_TRANSPORT": raw})
        check(f"resolve_transport({raw!r}) -> {want}", got == want, f"got {got!r}")
    # No env key at all -> the documented safe default (stdio).
    check("resolve_transport(no env) -> stdio (documented default)",
          T.resolve_transport({}) == "stdio")


def gate_server_tools() -> None:
    print("\n-- MCP server tool registration (frozen contract) --")
    for fname, host_default, port_default, tool, name in (
        ("deepmap_search_server.py", "127.0.0.1", 8787, "deepmap_search", "deepmap-search"),
        ("deepmap_embed_server.py", "127.0.0.1", 8788, "deepmap_embed", "deepmap-embed"),
    ):
        m = _load(BUILD / fname)
        tools = [t.name for t in m.mcp._tool_manager.list_tools()]
        check(f"{fname}: registers {tool}", tool in tools, f"got {tools}")
        check(f"{fname}: server name {name!r}", m.mcp.name == name, f"got {m.mcp.name!r}")
        check(f"{fname}: default host {host_default}", m.mcp.settings.host == host_default)
        check(f"{fname}: default port {port_default}", m.mcp.settings.port == port_default)
        check(f"{fname}: stateless+json (robust HTTP)",
              m.mcp.settings.stateless_http and m.mcp.settings.json_response)
    # the search server's frozen top_k clamp constant + the additive deepmap_impact tool
    s = _load(BUILD / "deepmap_search_server.py")
    check("deepmap_search: TOP_K_MAX == 50 (design.md:51)", s.TOP_K_MAX == 50, f"got {s.TOP_K_MAX}")
    check("deepmap_search: TOP_K_DEFAULT == 15 (frozen default)", s.TOP_K_DEFAULT == 15, f"got {s.TOP_K_DEFAULT}")
    check("deepmap_impact: IMPACT_LIMIT_DEFAULT == 30", s.IMPACT_LIMIT_DEFAULT == 30, f"got {s.IMPACT_LIMIT_DEFAULT}")
    check("deepmap_impact: IMPACT_LIMIT_MAX == 100", s.IMPACT_LIMIT_MAX == 100, f"got {s.IMPACT_LIMIT_MAX}")
    search_tools = [t.name for t in s.mcp._tool_manager.list_tools()]
    check("search server also exposes deepmap_impact", "deepmap_impact" in search_tools, str(search_tools))
    e = _load(BUILD / "deepmap_embed_server.py")
    check("embed server stays single-tool (deepmap_embed only)",
          [t.name for t in e.mcp._tool_manager.list_tools()] == ["deepmap_embed"])
    # frozen tool PARAMETER NAMES (a rename keeps the tool registered but breaks the contract)
    import inspect
    check("deepmap_embed signature is (text)", list(inspect.signature(e.deepmap_embed).parameters) == ["text"],
          str(list(inspect.signature(e.deepmap_embed).parameters)))
    check("deepmap_search signature is (query, top_k)",
          list(inspect.signature(s.deepmap_search).parameters) == ["query", "top_k"],
          str(list(inspect.signature(s.deepmap_search).parameters)))
    # (symbol, limit) is the frozen prefix; edge_types is an ADDITIVE optional param (P1-1, Rails
    # blast-radius override) — the two required leading params must stay stable and in order.
    check("deepmap_impact signature is (symbol, limit[, edge_types])",
          list(inspect.signature(s.deepmap_impact).parameters)[:2] == ["symbol", "limit"]
          and set(inspect.signature(s.deepmap_impact).parameters) <= {"symbol", "limit", "edge_types"},
          str(list(inspect.signature(s.deepmap_impact).parameters)))


def gate_clamp() -> None:
    """The frozen MCP contract clamps top_k to [1,50] and impact limit to [1,100] AT THE BOUNDARY
    (the retriever itself does not). Verify the actual clamp logic, not just the constant — a fake
    retriever records what value the tool forwarded after clamping. No Neo4j, no model."""
    print("\n-- top_k / limit clamp at the MCP boundary --")
    s = _load(BUILD / "deepmap_search_server.py")

    class _Rec:
        def __init__(self):
            self.k = None
            self.lim = None

        def search(self, query, top_k):
            self.k = top_k
            return []

        def impact(self, symbol, limit, edge_types=None):   # edge_types: additive P1-1 override
            self.lim = limit
            self.edge_types = edge_types
            return []

    rec = _Rec()
    s._get_retriever = lambda: rec   # module-global, resolved at call time inside the tool fns

    for given, want in [(0, 1), (-5, 1), (1, 1), (25, 25), (50, 50), (51, 50), (100, 50), (9999, 50)]:
        s.deepmap_search("q", top_k=given)
        check(f"deepmap_search clamps top_k {given} -> {want}", rec.k == want, f"got {rec.k}")
    for given, want in [(0, 1), (-1, 1), (1, 1), (30, 30), (100, 100), (101, 100), (5000, 100)]:
        s.deepmap_impact("sym", limit=given)
        check(f"deepmap_impact clamps limit {given} -> {want}", rec.lim == want, f"got {rec.lim}")


def gate_provisioner() -> None:
    print("\n-- provisioner registry + pure helpers --")
    P = _load(BUILD / "provision.py")
    # distribution naming
    check("dist_filename windows", P.dist_filename("5.26.0", True) == "neo4j-community-5.26.0-windows.zip")
    check("dist_filename unix", P.dist_filename("5.26.0", False) == "neo4j-community-5.26.0-unix.tar.gz")
    check("dist_url uses dist.neo4j.org", P.dist_url("5.26.0", True).startswith("https://dist.neo4j.org/"))
    # both STAGE-0 fixes baked into the conf block
    cb = P.conf_block()
    check("conf_block: relaxes auth_minimum_password_length (FD-1.6)",
          "dbms.security.auth_minimum_password_length=" in cb)
    check("conf_block: disables usage telemetry (TELEMETRY-1)", "dbms.usage_report.enabled=false" in cb)
    check("conf_block: disables client telemetry", "client.allow_telemetry=false" in cb)
    check("conf_block: min_pw_len param honored", "auth_minimum_password_length=3" in P.conf_block(3))
    check("MIN_PW_LEN < len('deepmap')", P.MIN_PW_LEN < len("deepmap"))
    # MCP server registry coherence
    reg = P.MCP_SERVERS
    check("registry has both servers", set(reg) == {"deepmap-search", "deepmap-embed"})
    ports = {P.mcp_port(n) for n in reg}
    check("server ports are distinct", len(ports) == len(reg), f"ports={ports}")
    for n, cfg in reg.items():
        check(f"{n}: script {cfg['script']} exists", (BUILD / cfg["script"]).exists())
        check(f"{n}: url shape", P.mcp_url(n) == f"http://{P.mcp_host()}:{P.mcp_port(n)}/mcp")
    # paths() returns the keys the lifecycle relies on
    keys = set(P.paths("5.26.0"))
    needed = {"home", "zip", "neo4j_home", "conf", "neo4j_bin", "admin_bin", "databases", "pidfile", "log"}
    check("paths() exposes lifecycle keys", needed <= keys, f"missing {needed - keys}")


def gate_mcp_json_matches_code() -> None:
    """The plugin ships NO root .mcp.json (a plugin's .mcp.json is loaded in every project; the one DeepMap shipped
    named the first project's ports). The wiring is what setup's write_mcp_json writes into each project, so THAT file
    is locked against the provisioner's own URLs — in a throwaway project dir, never this repository's file."""
    print("\n-- setup's .mcp.json <-> provisioner URL coherence (drift guard); the plugin ships none --")
    PM = _load(ROOT / "tests" / "plugin_manifest_gates.py")
    listed, _tracked = PM.shipped_mcp_json_status(ROOT)
    check("the plugin ships no root .mcp.json: .gitignore lists it", listed, "add a '.mcp.json' line to .gitignore")
    # "git does not track it" is checked ONCE, in plugin_manifest_gates.gate_no_shipped_mcp_json, so a still-tracked
    # file turns one gate RED, not three.
    P = _load(BUILD / "provision.py")
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td) / "proj"
        proj.mkdir()
        git = shutil.which("git")
        if git:   # its own git toplevel, so write_mcp_json's project identity is this dir whatever %TEMP% sits in
            try:
                subprocess.run([git, "init", "-q"], cwd=str(proj), capture_output=True, timeout=30)
            except Exception:  # noqa: BLE001
                pass
        written = P.write_mcp_json(proj, P.mcp_host(), P.mcp_port("deepmap-search"), P.mcp_port("deepmap-embed"))
        check("write_mcp_json writes the project's own .mcp.json", bool(written) and Path(written).is_file(),
              repr(written))
        try:
            cfg = json.loads(Path(written).read_text(encoding="utf-8")) if written else {}
        except Exception as exc:  # noqa: BLE001
            cfg = {}
            check("the written .mcp.json is valid JSON", False, repr(exc))
        servers = cfg.get("mcpServers", {}) if isinstance(cfg, dict) else {}
        for name in P.MCP_SERVERS:
            entry = servers.get(name, {})
            check(f"{name}: present in setup's .mcp.json", bool(entry))
            check(f"{name}: type http", entry.get("type") == "http", f"got {entry.get('type')!r}")
            check(f"{name}: url matches provisioner ({P.mcp_url(name)})",
                  entry.get("url") == P.mcp_url(name), f"got {entry.get('url')!r}")


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 64)
    print("DeepMap — MCP transport + provisioner gates (no Neo4j, no network)")
    print("=" * 64)
    gate_transport_resolver()
    gate_server_tools()
    gate_clamp()
    gate_provisioner()
    gate_mcp_json_matches_code()
    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
