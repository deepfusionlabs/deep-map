#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j gates for the benchmark WITH-arm MCP wiring (ISS-build:002).

Locks `benchmark/run_benchmark.py:write_on_mcp_config` so it can't silently regress to the old
"server files absent / command TBD" contract once the servers are present, and so its emitted
HTTP wiring stays consistent with the `.mcp.json` that /deep-map:setup writes into each project
(`provision.write_mcp_json`: type:http, /mcp, 8787/8788 by default; the plugin ships no root `.mcp.json`)
and honours the DEEPMAP_MCP_HOST / _SEARCH_PORT / _EMBED_PORT overrides.

Pure: imports the harness module and the provisioner and calls both writers against temp paths (plus a
`git init` of a throwaway temp project); never spends, never invokes claude or Neo4j.
"""
from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BENCH = REPO / "benchmark"
BUILD = REPO / "build"
sys.path.insert(0, str(BENCH))

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
    spec = importlib.util.spec_from_file_location("dm_run_benchmark", BENCH / "run_benchmark.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _write(B, **env):
    """Call write_on_mcp_config under a temporary env, return (returned_bool, parsed_json)."""
    saved = {k: os.environ.get(k) for k in env}
    os.environ.update({k: v for k, v in env.items()})
    try:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "mcp_on.json"
            ret = B.write_on_mcp_config(path)
            data = json.loads(path.read_text(encoding="utf-8"))
            return ret, data
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def gate_presence() -> None:
    print("\n-- _server_files_present: true in this (built) repo --")
    B = _load_benchmark()
    check("server files detected present", B._server_files_present() is True,
          "deepmap_*_server.py should exist in build/")


def gate_real_wiring() -> None:
    print("\n-- write_on_mcp_config: real type:http wiring when present --")
    B = _load_benchmark()
    ret, data = _write(B)  # default env
    check("returns True (real wiring, not contract)", ret is True)
    servers = data.get("mcpServers", {})
    s = servers.get("deepmap-search", {})
    e = servers.get("deepmap-embed", {})
    check("deepmap-search is type:http", s.get("type") == "http", str(s))
    check("deepmap-embed is type:http", e.get("type") == "http", str(e))
    check("search url is the 8787 /mcp endpoint", s.get("url") == "http://127.0.0.1:8787/mcp", str(s))
    check("embed url is the 8788 /mcp endpoint", e.get("url") == "http://127.0.0.1:8788/mcp", str(e))
    check("no live deepmap server still says 'absent'/'TBD'",
          not any("absent" in json.dumps(v).lower() or "tbd" in json.dumps(v).lower()
                  for k, v in servers.items() if k.startswith("deepmap-")),
          json.dumps(servers))
    # mcpServers must contain ONLY loadable servers (mirror .mcp.json) — a member without a
    # transport key could make a strict CLI reject the whole --mcp-config (review finding).
    check("mcpServers contains EXACTLY the two loadable HTTP servers (no malformed member)",
          set(servers) == {"deepmap-search", "deepmap-embed"}, str(list(servers)))
    check("every mcpServers member has a transport (type/command/url)",
          all(any(k in v for k in ("type", "command", "url")) for v in servers.values()),
          json.dumps(servers))
    check("mcp-neo4j-cypher documented in a SIBLING key, not inside mcpServers",
          "mcp-neo4j-cypher" in data.get("_deferred_servers", {}) and "mcp-neo4j-cypher" not in servers)
    check("no fabricated 'measured' result anywhere", "measured" not in json.dumps(data).lower())


def gate_env_overrides() -> None:
    print("\n-- write_on_mcp_config: honours host/port env overrides --")
    B = _load_benchmark()
    _ret, data = _write(B, DEEPMAP_MCP_HOST="0.0.0.0",
                        DEEPMAP_SEARCH_PORT="9001", DEEPMAP_EMBED_PORT="9002")
    servers = data.get("mcpServers", {})
    check("search url reflects host+port override",
          servers.get("deepmap-search", {}).get("url") == "http://0.0.0.0:9001/mcp",
          str(servers.get("deepmap-search")))
    check("embed url reflects host+port override",
          servers.get("deepmap-embed", {}).get("url") == "http://0.0.0.0:9002/mcp",
          str(servers.get("deepmap-embed")))


_WIRING_ENV = ("DEEPMAP_MCP_HOST", "DEEPMAP_SEARCH_PORT", "DEEPMAP_EMBED_PORT")


@contextlib.contextmanager
def _env(**kv):
    """Set (str) / unset (None) env vars for the block, then restore them exactly."""
    saved = {k: os.environ.get(k) for k in kv}
    try:
        for k, v in kv.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _load_provision():
    if str(BUILD) not in sys.path:
        sys.path.append(str(BUILD))     # write_mcp_json imports port_registry by name
    spec = importlib.util.spec_from_file_location("dm_provision_for_wiring", BUILD / "provision.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _setup_wiring(P, td: Path):
    """The mcpServers that setup writes (provision.write_mcp_json, with the provisioner's own host/ports for the
    current env) into a throwaway git project under `td`. None when that project would not be its own toplevel
    (no git and `td` inside another checkout): write_mcp_json would then write into THAT checkout, so it is not
    called."""
    import port_registry as PR   # noqa: WPS433 (importable once _load_provision put build/ on sys.path)
    proj = td / "proj"
    proj.mkdir()
    git = shutil.which("git")
    if git:   # its own git toplevel, so write_mcp_json's project identity is this dir whatever %TEMP% sits in
        try:
            subprocess.run([git, "init", "-q"], cwd=str(proj), capture_output=True, timeout=30)
        except Exception:  # noqa: BLE001
            pass
    ident = Path(PR.project_identity(proj))
    if os.path.normcase(str(ident.resolve())) != os.path.normcase(str(proj.resolve())):
        return None
    written = P.write_mcp_json(proj, P.mcp_host(), P.mcp_port("deepmap-search"), P.mcp_port("deepmap-embed"))
    if not written:
        return {}
    return json.loads(Path(written).read_text(encoding="utf-8")).get("mcpServers", {})


def gate_matches_setup_mcp_json() -> None:
    """The ON arm must reach the servers exactly as a real project does. The plugin ships no root .mcp.json; the
    wiring every project gets is what /deep-map:setup writes (provision.write_mcp_json), so the benchmark's emitted
    wiring is compared with THAT, for the default env and for an override env."""
    print("\n-- emitted wiring is consistent with the .mcp.json setup writes (provision.write_mcp_json) --")
    try:
        P = _load_provision()
    except Exception as exc:  # noqa: BLE001
        check("build/provision.py loads (its write_mcp_json is the wiring to compare with)", False,
              f"{type(exc).__name__}: {exc}")
        return
    B = _load_benchmark()
    cases = (("default env", {k: None for k in _WIRING_ENV}),
             ("host/port overrides", {"DEEPMAP_MCP_HOST": "0.0.0.0", "DEEPMAP_SEARCH_PORT": "9001",
                                      "DEEPMAP_EMBED_PORT": "9002"}))
    for label, env in cases:
        with _env(**env), tempfile.TemporaryDirectory() as td:
            setup = _setup_wiring(P, Path(td))
            if setup is None:
                print(f"  [skip] {label}: a throwaway project under {td} is not its own git toplevel (no git here), "
                      "so setup's .mcp.json is not written: the benchmark-to-setup comparison cannot run")
                continue
            _ret, data = _write(B)
            emitted = data.get("mcpServers", {})
            check(f"{label}: setup wrote both deepmap servers",
                  all(name in setup for name in ("deepmap-search", "deepmap-embed")), str(setup))
            for name in ("deepmap-search", "deepmap-embed"):
                s, em = setup.get(name, {}), emitted.get(name, {})
                check(f"{label}: {name}: type matches setup's .mcp.json",
                      s.get("type") == em.get("type") == "http", f"setup={s}, emitted={em}")
                check(f"{label}: {name}: url matches setup's .mcp.json",
                      bool(s.get("url")) and s.get("url") == em.get("url"),
                      f"setup={s.get('url')}, emitted={em.get('url')}")


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 64)
    print("DeepMap — benchmark WITH-arm wiring gates (no Neo4j, ISS-build:002)")
    print("=" * 64)

    gate_presence()
    gate_real_wiring()
    gate_env_overrides()
    gate_matches_setup_mcp_json()

    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
