#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j, no-network gates for the per-project MCP PORT REGISTRY (side-by-side projects).

Proves the load-bearing contract of build/port_registry.py + provision.write_mcp_json:
  * TWO projects get DISTINCT free (search, embed) port pairs, and DISTINCT `.mcp.json` wiring;
  * a SINGLE project is UNCHANGED — it resolves to the frozen defaults 8787/8788 and its generated
    `.mcp.json` is exactly the two type:http servers on 127.0.0.1:8787/8788; the plugin itself ships NO
    root `.mcp.json` (tests/plugin_manifest_gates.py's rule; its .gitignore half is re-checked here);
  * allocation is idempotent/stable (re-allocating a root returns the same pair, no re-probe);
  * a taken base port BUMPS to the next free pair (the free-port probe is load-bearing);
  * `.mcp.json` writes MERGE (foreign servers preserved) and are semantically idempotent;
  * everything NEVER raises (garbage registry / garbage `.mcp.json` degrade, never crash);
  * the per-port pidfile/log fix keeps two projects' servers independently manageable.

Pure & hermetic: DEEPMAP_HOME is redirected to a throwaway dir (the real ~/.deepmap is untouched),
the free-port probe is stubbed (NO real sockets are bound), no Neo4j, no network. Exit 0 = green.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import redirect_stderr
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
sys.path.insert(0, str(BUILD))
# Direct imports (single module instances): provision.apply_ports_for/write_mcp_json do their own
# `import port_registry`, so patching THIS port_registry._port_in_use reaches provision too — a
# separately-`_load`ed copy would not, and provision's allocate path would hit a real socket.
import port_registry as PR  # noqa: E402
import provision as P       # noqa: E402

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


def _mk_project(parent: Path, name: str) -> Path:
    """A throwaway project dir; git-init it (when git is present) so _project_identity resolves each
    to its own git toplevel deterministically — distinct dirs => distinct identities/keys."""
    d = parent / name
    d.mkdir(parents=True, exist_ok=True)
    git = shutil.which("git")
    if git:
        try:
            subprocess.run([git, "init", "-q"], cwd=str(d), capture_output=True, timeout=30)
        except Exception:
            pass
    return d.resolve()


def _fake_probe(occupied: set):
    """A stub for the free-port probe: True iff the port is in `occupied` (no real socket)."""
    return lambda host, port: port in occupied


# --------------------------------------------------------------------------- gates

def gate_distinct_ports(PR) -> None:
    print("\n-- two projects -> distinct free port pairs --")
    saved = PR._port_in_use
    PR._port_in_use = _fake_probe(set())  # a clean machine: every port free
    with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as work:
        _saved_home = os.environ.get("DEEPMAP_HOME")
        os.environ["DEEPMAP_HOME"] = home
        try:
            a = _mk_project(Path(work), "proj_a")
            b = _mk_project(Path(work), "proj_b")
            pa = PR.allocate_ports(a)
            pb = PR.allocate_ports(b)
            check("project A gets the base pair (8787, 8788)", pa == (8787, 8788), f"got {pa}")
            check("project B gets the next free pair (8789, 8790)", pb == (8789, 8790), f"got {pb}")
            check("the two projects' ports are DISTINCT", pa != pb and set(pa).isdisjoint(pb),
                  f"A={pa} B={pb}")
            stats = PR.registry_stats()
            check("registry records 2 projects", stats.get("n_projects") == 2, str(stats))
            check("registry file exists under DEEPMAP_HOME", PR.registry_path().exists())
        finally:
            if _saved_home is None:
                os.environ.pop("DEEPMAP_HOME", None)
            else:
                os.environ["DEEPMAP_HOME"] = _saved_home
            PR._port_in_use = saved


def gate_distinct_mcp_json(PR, P) -> None:
    print("\n-- two projects -> distinct .mcp.json wiring --")
    saved = PR._port_in_use
    PR._port_in_use = _fake_probe(set())
    with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as work:
        _saved_home = os.environ.get("DEEPMAP_HOME")
        os.environ["DEEPMAP_HOME"] = home
        try:
            a = _mk_project(Path(work), "proj_a")
            b = _mk_project(Path(work), "proj_b")
            sa, ea = PR.allocate_ports(a)
            sb, eb = PR.allocate_ports(b)
            P.write_mcp_json(a, "127.0.0.1", sa, ea)
            P.write_mcp_json(b, "127.0.0.1", sb, eb)
            ca = json.loads((a / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]
            cb = json.loads((b / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]
            check("A .mcp.json search url points at A's port",
                  ca["deepmap-search"]["url"] == f"http://127.0.0.1:{sa}/mcp", str(ca))
            check("A .mcp.json embed url points at A's port",
                  ca["deepmap-embed"]["url"] == f"http://127.0.0.1:{ea}/mcp", str(ca))
            check("B .mcp.json search url points at B's (different) port",
                  cb["deepmap-search"]["url"] == f"http://127.0.0.1:{sb}/mcp", str(cb))
            check("A and B .mcp.json search URLs DIFFER",
                  ca["deepmap-search"]["url"] != cb["deepmap-search"]["url"])
            check("both declare type:http",
                  ca["deepmap-search"]["type"] == "http" and cb["deepmap-embed"]["type"] == "http")
        finally:
            if _saved_home is None:
                os.environ.pop("DEEPMAP_HOME", None)
            else:
                os.environ["DEEPMAP_HOME"] = _saved_home
            PR._port_in_use = saved


def _shipped_mcp_json_status():
    """tests/plugin_manifest_gates.py's ONE rule for "the plugin ships no root .mcp.json" (loaded by path)."""
    spec = importlib.util.spec_from_file_location("plugin_manifest_gates", ROOT / "tests" / "plugin_manifest_gates.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.shipped_mcp_json_status(ROOT), mod.GIT_RM_HINT


# The exact wiring a lone (first) project gets: the frozen default pair, streamable HTTP on 127.0.0.1.
_SOLO_WIRING = {
    "deepmap-search": {"type": "http", "url": "http://127.0.0.1:8787/mcp"},
    "deepmap-embed": {"type": "http", "url": "http://127.0.0.1:8788/mcp"},
}


def gate_single_project_unchanged(PR, P) -> None:
    print("\n-- single project unchanged (defaults 8787/8788; setup's .mcp.json; the plugin ships none) --")
    (listed, _tracked), _hint = _shipped_mcp_json_status()
    check("the plugin ships no root .mcp.json: .gitignore lists it (setup writes each project's own)", listed,
          "add a '.mcp.json' line to .gitignore")
    # "git does not track it" is checked ONCE, in plugin_manifest_gates.gate_no_shipped_mcp_json, so a still-tracked
    # file turns one gate RED, not three.
    saved = PR._port_in_use
    PR._port_in_use = _fake_probe(set())
    with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as work:
        _saved_home = os.environ.get("DEEPMAP_HOME")
        os.environ["DEEPMAP_HOME"] = home
        try:
            a = _mk_project(Path(work), "solo")
            pa = PR.allocate_ports(a)
            check("lone project resolves to the frozen defaults (8787, 8788)", pa == (8787, 8788),
                  f"got {pa}")
            P.write_mcp_json(a, "127.0.0.1", *pa)
            gen = json.loads((a / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]
            for name in ("deepmap-search", "deepmap-embed"):
                check(f"setup's .mcp.json: {name} == {_SOLO_WIRING[name]} (semantic match)",
                      gen.get(name) == _SOLO_WIRING[name], f"gen={gen.get(name)}")
            check("setup's .mcp.json declares exactly the two deepmap servers (nothing else invented)",
                  set(gen) == set(_SOLO_WIRING), sorted(gen))
        finally:
            if _saved_home is None:
                os.environ.pop("DEEPMAP_HOME", None)
            else:
                os.environ["DEEPMAP_HOME"] = _saved_home
            PR._port_in_use = saved


def gate_idempotent(PR, P) -> None:
    print("\n-- allocation & .mcp.json writes are idempotent/stable --")
    saved = PR._port_in_use
    PR._port_in_use = _fake_probe(set())
    with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as work:
        _saved_home = os.environ.get("DEEPMAP_HOME")
        os.environ["DEEPMAP_HOME"] = home
        try:
            a = _mk_project(Path(work), "proj_a")
            first = PR.allocate_ports(a)
            # Now pretend the base port is occupied: a re-allocate MUST still return the stored pair
            # (the idempotent short-circuit runs BEFORE any probe — see the migrate-itself footgun).
            PR._port_in_use = _fake_probe({8787})
            again = PR.allocate_ports(a)
            check("re-allocating the same root returns the SAME pair (no re-probe/migration)",
                  again == first, f"first={first} again={again}")
            check("lookup_ports returns the same stored pair", PR.lookup_ports(a) == first)
            # semantic-idempotent .mcp.json: a second write is a no-op (content unchanged)
            P.write_mcp_json(a, "127.0.0.1", *first)
            c1 = (a / ".mcp.json").read_text(encoding="utf-8")
            P.write_mcp_json(a, "127.0.0.1", *first)
            c2 = (a / ".mcp.json").read_text(encoding="utf-8")
            check(".mcp.json re-write is a no-op (byte-identical)", c1 == c2)
        finally:
            if _saved_home is None:
                os.environ.pop("DEEPMAP_HOME", None)
            else:
                os.environ["DEEPMAP_HOME"] = _saved_home
            PR._port_in_use = saved


def gate_port_taken_bumps(PR) -> None:
    print("\n-- a taken base port bumps to the next free pair (probe is load-bearing) --")
    saved = PR._port_in_use
    PR._port_in_use = _fake_probe({8787})  # a FOREIGN process holds 8787
    with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as work:
        _saved_home = os.environ.get("DEEPMAP_HOME")
        os.environ["DEEPMAP_HOME"] = home
        try:
            a = _mk_project(Path(work), "proj_a")
            pa = PR.allocate_ports(a)
            check("project #1 with 8787 occupied gets (8789, 8790), NOT (8787, 8788)",
                  pa == (8789, 8790), f"got {pa}")
        finally:
            if _saved_home is None:
                os.environ.pop("DEEPMAP_HOME", None)
            else:
                os.environ["DEEPMAP_HOME"] = _saved_home
            PR._port_in_use = saved


def gate_merge_preserves_foreign(P) -> None:
    print("\n-- write_mcp_json MERGES: foreign mcpServers survive --")
    with tempfile.TemporaryDirectory() as work:
        a = Path(work) / "proj"
        a.mkdir()
        (a / ".mcp.json").write_text(json.dumps({
            "mcpServers": {"some-other": {"type": "http", "url": "http://127.0.0.1:9999/mcp"}},
            "otherTopKey": {"keep": True},
        }), encoding="utf-8")
        P.write_mcp_json(a, "127.0.0.1", 8789, 8790)
        obj = json.loads((a / ".mcp.json").read_text(encoding="utf-8"))
        servers = obj["mcpServers"]
        check("foreign server 'some-other' preserved",
              servers.get("some-other", {}).get("url") == "http://127.0.0.1:9999/mcp", str(servers))
        check("unrelated top-level key preserved", obj.get("otherTopKey") == {"keep": True})
        check("deepmap-search added at the merged port",
              servers.get("deepmap-search", {}).get("url") == "http://127.0.0.1:8789/mcp")
        check("deepmap-embed added at the merged port",
              servers.get("deepmap-embed", {}).get("url") == "http://127.0.0.1:8790/mcp")


def gate_never_raises(PR, P) -> None:
    print("\n-- never-raises: garbage registry / garbage .mcp.json degrade, never crash --")
    saved = PR._port_in_use
    PR._port_in_use = _fake_probe(set())
    with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as work:
        _saved_home = os.environ.get("DEEPMAP_HOME")
        os.environ["DEEPMAP_HOME"] = home
        try:
            # (1) garbage registry file -> allocate falls back to the base pair WITHOUT raising and
            #     WITHOUT clobbering the (possibly newer/unknown) file.
            Path(home).mkdir(parents=True, exist_ok=True)
            PR.registry_path().write_text("}{ not json", encoding="utf-8")
            a = _mk_project(Path(work), "proj_a")
            try:
                pa = PR.allocate_ports(a)
                ok = True
            except Exception as exc:  # noqa: BLE001
                ok = False
                pa = None
                print(f"    (raised: {exc})")
            check("garbage registry -> allocate returns base pair, no raise",
                  ok and pa == (8787, 8788), f"got {pa}")
            check("garbage registry NOT overwritten (unknown version preserved)",
                  PR.registry_path().read_text(encoding="utf-8") == "}{ not json")

            # (2) garbage existing .mcp.json -> write_mcp_json REFUSES (never destroys a hand-edit).
            b = _mk_project(Path(work), "proj_b")
            (b / ".mcp.json").write_text("<<<not json>>>", encoding="utf-8")
            with redirect_stderr(io.StringIO()):
                try:
                    P.write_mcp_json(b, "127.0.0.1", 8791, 8792)
                    raised = False
                except Exception as exc:  # noqa: BLE001
                    raised = True
                    print(f"    (raised: {exc})")
            check("garbage .mcp.json -> write refuses, no raise", not raised)
            check("garbage .mcp.json left intact (not destroyed)",
                  (b / ".mcp.json").read_text(encoding="utf-8") == "<<<not json>>>")
        finally:
            if _saved_home is None:
                os.environ.pop("DEEPMAP_HOME", None)
            else:
                os.environ["DEEPMAP_HOME"] = _saved_home
            PR._port_in_use = saved


def gate_per_port_pidfile(P) -> None:
    print("\n-- per-port pidfile/log: two projects' servers are independently manageable --")
    p1 = str(P._mcp_pidfile("deepmap-search", 8789))
    p2 = str(P._mcp_pidfile("deepmap-search", 8791))
    check("pidfile path embeds the port", p1.endswith("deepmap-search-8789.pid"), p1)
    check("different ports -> different pidfiles (no cross-project overwrite)", p1 != p2, f"{p1} vs {p2}")


def gate_apply_ports_allocate(PR, P) -> None:
    print("\n-- apply_ports_for(allocate=True) injects the allocated pair into env --")
    saved = PR._port_in_use
    PR._port_in_use = _fake_probe(set())
    # This gate tests the MCP half ONLY. Force the Neo4j-URI escape hatch so apply_ports_for skips its
    # Neo4j half (no bolt allocation, no is_bolt_up probe, no NEO4J_URI injection) — keeps it hermetic
    # (never touches a real local Neo4j). The Neo4j half is covered by tests/per_project_neo4j_gates.py.
    _saved_hatch = P._USER_SET_NEO4J_URI
    P._USER_SET_NEO4J_URI = True
    with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as work:
        keys = ("DEEPMAP_HOME", "DEEPMAP_SEARCH_PORT", "DEEPMAP_EMBED_PORT")
        _saved = {k: os.environ.get(k) for k in keys}
        os.environ["DEEPMAP_HOME"] = home
        os.environ.pop("DEEPMAP_SEARCH_PORT", None)
        os.environ.pop("DEEPMAP_EMBED_PORT", None)
        try:
            a = _mk_project(Path(work), "proj_a")
            with redirect_stderr(io.StringIO()):
                pair = P.apply_ports_for(str(a), allocate=True)
            check("apply_ports_for returns the allocated pair", pair == (8787, 8788), str(pair))
            check("DEEPMAP_SEARCH_PORT injected into env", os.environ.get("DEEPMAP_SEARCH_PORT") == "8787")
            check("DEEPMAP_EMBED_PORT injected into env", os.environ.get("DEEPMAP_EMBED_PORT") == "8788")
            check("the project is recorded in the registry", PR.lookup_ports(a) == (8787, 8788))
            # lookup-only on a FRESH unallocated project (no registry entry, no .mcp.json) -> None.
            os.environ.pop("DEEPMAP_SEARCH_PORT", None)
            os.environ.pop("DEEPMAP_EMBED_PORT", None)
            b = _mk_project(Path(work), "proj_b_unrecorded")
            with redirect_stderr(io.StringIO()):
                got = P.apply_ports_for(str(b), allocate=False)
            check("lookup-only on an unallocated project -> None, env left unset",
                  got is None and "DEEPMAP_SEARCH_PORT" not in os.environ, str(got))
        finally:
            for k, v in _saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            PR._port_in_use = saved
            P._USER_SET_NEO4J_URI = _saved_hatch


def gate_isolation_note(P) -> None:
    print("\n-- isolation_note: per-project INSTANCE isolation (tri-state, honest) --")

    def _stats(rows):
        return {"n_projects": len(rows), "projects": rows}

    # one project on its own instance -> isolation TRUE, no warning
    one = P.isolation_note("community", _stats([{"root": "/a", "neo4j_uri": "bolt://localhost:7687"}]))
    check("community: graph is per-project (own instance), NOT shared",
          "per-project" in one and "OWN Neo4j instance" in one and "SHARED" not in one, one)
    check("community: explains a per-project INSTANCE (Community has no per-project DB)",
          "per-project instance" in one, one)
    check("single project: NO convergence warning", "WARNING" not in one, one)
    # two projects on DISTINCT instances -> still isolation TRUE, NO warning
    two = P.isolation_note("community", _stats([
        {"root": "/a", "neo4j_uri": "bolt://localhost:7687"},
        {"root": "/b", "neo4j_uri": "bolt://localhost:7689"}]))
    check("two DISTINCT instances: NO warning (truly isolated)", "WARNING" not in two, two)
    # two projects CONVERGED on one instance -> WARNING they share a graph
    conv = P.isolation_note("community", _stats([
        {"root": "/a", "neo4j_uri": "bolt://localhost:7687"},
        {"root": "/b", "neo4j_uri": "bolt://localhost:7687"}]))
    check("converged projects: WARNING they still share a graph",
          "WARNING" in conv and "share a graph" in conv, conv)
    check("converged: honest 'last-synced' degrade wording", "last-synced" in conv, conv)
    # enterprise / unknown
    ent = P.isolation_note("enterprise", _stats([{"root": "/a", "neo4j_uri": "bolt://localhost:7687"}]))
    check("enterprise: per-project instances (DB would also work)", "per-project instances" in ent, ent)
    unk = P.isolation_note(None, _stats([]))
    check("unknown edition -> edition=unknown (honest, never raises)", "edition=unknown" in unk, unk)
    # operator escape hatch -> operator-managed, isolation disabled
    esc = P.isolation_note("community", _stats([{"root": "/a"}, {"root": "/b"}]), escape_hatch=True)
    check("escape hatch: OPERATOR-MANAGED single endpoint", "OPERATOR-MANAGED" in esc, esc)
    check("escape hatch: owner guard keeps them from mixing", "owner guard" in esc, esc)


def gate_ensure_mcp_json_create_only(P) -> None:
    print("\n-- _ensure_mcp_json: CREATE if missing, NEVER overwrite (heal / mcp-start) --")
    with tempfile.TemporaryDirectory() as work:
        keys = ("DEEPMAP_SEARCH_PORT", "DEEPMAP_EMBED_PORT")
        _saved = {k: os.environ.get(k) for k in keys}
        os.environ["DEEPMAP_SEARCH_PORT"] = "8801"
        os.environ["DEEPMAP_EMBED_PORT"] = "8802"
        try:
            a = Path(work) / "proj"
            a.mkdir()  # a plain (non-git) tmp dir -> project_identity resolves to itself
            mcpj = a / ".mcp.json"
            with redirect_stderr(io.StringIO()):
                P._ensure_mcp_json(str(a))
            check("missing -> .mcp.json created", mcpj.exists())
            if mcpj.exists():
                url = json.loads(mcpj.read_text(encoding="utf-8"))["mcpServers"]["deepmap-search"]["url"]
                check("created at the env-injected port (8801)", "8801" in url, url)
                os.environ["DEEPMAP_SEARCH_PORT"] = "8803"  # ports change...
                os.environ["DEEPMAP_EMBED_PORT"] = "8804"
                before = mcpj.read_text(encoding="utf-8")
                with redirect_stderr(io.StringIO()):
                    P._ensure_mcp_json(str(a))
                check("existing -> left UNCHANGED (create-only; the ports Claude already uses)",
                      mcpj.read_text(encoding="utf-8") == before)
        finally:
            for k, v in _saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


def gate_env_override_respected(PR, P) -> None:
    print("\n-- explicit DEEPMAP_*_PORT override disables auto-allocation --")
    # Force the Neo4j escape hatch so this MCP-only gate stays hermetic (no bolt allocation / probe).
    _saved_hatch = P._USER_SET_NEO4J_URI
    P._USER_SET_NEO4J_URI = True
    with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as work:
        _saved = {k: os.environ.get(k) for k in ("DEEPMAP_HOME", "DEEPMAP_SEARCH_PORT", "DEEPMAP_EMBED_PORT")}
        os.environ["DEEPMAP_HOME"] = home
        os.environ["DEEPMAP_SEARCH_PORT"] = "9001"
        os.environ["DEEPMAP_EMBED_PORT"] = "9002"
        try:
            a = _mk_project(Path(work), "proj_a")
            with redirect_stderr(io.StringIO()):
                P.apply_ports_for(str(a), allocate=True)
            check("manual override -> project NOT recorded in the registry (no allocation)",
                  PR.lookup_ports(a) is None, str(PR.load_registry()))
        finally:
            for k, v in _saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            P._USER_SET_NEO4J_URI = _saved_hatch


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 64)
    print("DeepMap — per-project MCP port registry gates (no Neo4j, no network)")
    print("=" * 64)
    gate_distinct_ports(PR)
    gate_distinct_mcp_json(PR, P)
    gate_single_project_unchanged(PR, P)
    gate_idempotent(PR, P)
    gate_port_taken_bumps(PR)
    gate_merge_preserves_foreign(P)
    gate_never_raises(PR, P)
    gate_per_port_pidfile(P)
    gate_apply_ports_allocate(PR, P)
    gate_isolation_note(P)
    gate_ensure_mcp_json_create_only(P)
    gate_env_override_respected(PR, P)
    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
