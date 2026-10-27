#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j, no-kill gates for the ROUND-8 provisioner fixes (build/provision.py, owner W2):

  A  J4 — every bolt URI provision GENERATES dials 127.0.0.1 (port_registry.bolt_uri): the per-instance Neo4j binds
     127.0.0.1 only, and `localhost` resolves to ::1 first on Windows (~2 s per new connection; a [::1] listener —
     Docker publishes one — could answer instead). An operator's NEO4J_URI stays verbatim. URIs are COMPARED by
     endpoint: a server an older build started with bolt://localhost:<p> is NOT "stale config" (heal would restart
     it once for nothing); instance-rm clears sync's per-instance lock under both spellings; isolation_note counts
     two spellings of one endpoint as one instance.
  B  J5 — a detached MCP server records the code it runs (owner sidecar: code_root + code_version + script); heal
     and mcp-start RESTART this project's own server (owner-verified: sidecar + live pid + server script + start
     stamp) when it runs other code than the provisioner running now — a plugin update used to keep the old
     version serving. A pre-J5 sidecar (no code fields) is judged by the running process's command line (the
     script path it runs); an unreadable one is unknown -> left alone. Another project's server, an unverifiable
     pid, current code: never restarted. A restarted server is reported in heal's `mcp_started`.
  C  J6 — setup's verify query is FTS-visible (never a 1-2 char name the BM25 arm drops: a Go `T.Do` -> 'Do'), a
     summary word when every name is short, and an empty answer retries the fixed phrase before FAILED;
     `instance-rm --purge` also removes setup's persisted blocker and the per-port MCP server logs (not while a
     server still listens there, never another project's); provision's git probes pass core.longpaths on Windows.
  D  J1 — `provision.py git-hook --install` syncs the project's SYNC ROOT (not the git toplevel); its block is
     scoped to THIS work tree at run time (a linked worktree / main checkout share one hooks file: each project's
     block runs only for its own commits — proven by real `git commit`s); it REFUSES a hooks file outside the
     repository (a global / relative-outside core.hooksPath), a husky-managed or git-TRACKED hook, a non-sh (Python,
     Node) or binary hook, and a disabled (non-executable, POSIX) hook; it INSERTS the block right after the shebang
     (a trailing `exec` / `exit` can never skip it); `--status` tells the current block from a stale one (an old
     installer's --stale-cleanup line, a deleted / other plugin path, an unscoped round-7 block, a wrong target)
     and --install replaces those in place; an old installer's post-checkout line is removed; uninstall removes only
     this project's block. Cross-checked with the SessionStart syncflag hook's detector (W1).

Reuses the round-4/round-6 hermetic harnesses (same owner): DEEPMAP_HOME -> a throwaway dir; port / bolt probes,
drivers, pid liveness / command line / creation time, spawns and kills are stubbed. The git-hook gates use REAL git
in throwaway repos with an isolated global/system git config and a stub interpreter (a sh script that records the
sync it was asked to run) — no DeepMap sync, no Neo4j, no network. Exit 0 = GREEN.
"""
from __future__ import annotations

import ast
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
from provision_round4_gates import (P, PR, _Patch, _Sandbox, _mk_project, _captured, _quiet,  # noqa: E402
                                    _clear_port_env)
from provision_round6_gates import _World, _mcp_env, _seed_owned  # noqa: E402

_PASS = 0
_FAIL = 0
SEARCH, EMBED = "deepmap-search", "deepmap-embed"
LOOP = "127.0.0.1"


def check(label: str, cond: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {label}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}{(' — ' + detail) if detail else ''}")


def _run_gate(fn) -> None:
    """An unexpected raise (e.g. a not-yet-built API) counts as ONE failed check, never a crash."""
    try:
        fn()
    except Exception as e:   # noqa: BLE001
        import traceback
        tb = traceback.format_exc().strip().splitlines()
        check(f"{fn.__name__} ran without raising", False, f"{type(e).__name__}: {e} " + " | ".join(tb[-4:]))


def _rmtree(path) -> None:
    """Delete a dir THIS gate created (exact path) — git's read-only object files included."""
    def onerr(fn, p, _exc):
        try:
            os.chmod(p, stat.S_IWRITE)
            fn(p)
        except Exception:
            pass
    if path and Path(path).exists():
        shutil.rmtree(str(path), onerror=onerr)


def _read_json(p: Path) -> dict:
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return {}


# =========================================================================== A — J4 URIs

def _string_literals_outside_docstrings(tree) -> list:
    """(lineno, text, assigned-name) of every str constant / f-string part that is not a docstring."""
    doc_ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", None) or []
            if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) \
                    and isinstance(body[0].value.value, str):
                doc_ids.add(id(body[0].value))
    owner = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            for sub in ast.walk(node.value):
                owner[id(sub)] = names[0] if names else None
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in doc_ids:
            out.append((node.lineno, node.value, owner.get(id(node))))
    return out


def gate_a_generated_uris_dial_ipv4() -> None:
    print("\n-- A (J4): every bolt URI provision generates dials 127.0.0.1; an operator's NEO4J_URI is verbatim --")
    check("DEFAULT_URI == port_registry.bolt_uri(BASE_BOLT) (bolt://127.0.0.1:7687)",
          P.DEFAULT_URI == PR.bolt_uri(PR.BASE_BOLT) == f"bolt://{LOOP}:7687", P.DEFAULT_URI)
    check("the no-route sentinel is unconnectable on IPv4 too (bolt://127.0.0.1:0)",
          P._NO_ROUTE_URI == PR.bolt_uri(0), P._NO_ROUTE_URI)
    tree = ast.parse((REPO / "build" / "provision.py").read_text(encoding="utf-8"))
    bad = [(ln, s[:70]) for ln, s, name in _string_literals_outside_docstrings(tree)
           if "bolt://localhost" in s and name != "_LEGACY_BOLT_URI_FMT"]
    check("no string in provision.py (outside docstrings / the named legacy-spelling constant) builds "
          "'bolt://localhost…'", not bad, str(bad[:6]))
    with _Sandbox() as (home, work):
        a = _mk_project(work, "a", marker=True)
        PR.allocate_neo4j_ports(a, base_adopt_ok=lambda: False)
        bolt = PR.lookup_neo4j_ports(a)[0]
        _clear_port_env()
        P.apply_ports_for(a, allocate=False)
        check(f"a registered project gets NEO4J_URI=bolt://127.0.0.1:{bolt} injected",
              os.environ.get("NEO4J_URI") == f"bolt://{LOOP}:{bolt}", os.environ.get("NEO4J_URI"))
    with _Sandbox() as (home, work):
        # a project with no instance while the base is another project's -> the fail-closed sentinel
        b = _mk_project(work, "b", marker=True)
        other = _mk_project(work, "other", marker=True)
        PR.allocate_neo4j_ports(other, base_adopt_ok=lambda: True)   # the base pair is the other project's
        _clear_port_env()
        P.apply_ports_for(b, allocate=False)
        uri = os.environ.get("NEO4J_URI")
        check("no route of its own -> the injected URI is the IPv4 no-route sentinel (fails closed)",
              uri == PR.bolt_uri(0) and P.neo4j_route_problem() is not None, f"{uri} {P.neo4j_route_problem()}")
    with _Sandbox() as (home, work):
        a = _mk_project(work, "a", marker=True)
        os.environ["NEO4J_URI"] = "bolt://localhost:7689"
        with _Patch(P, _USER_SET_NEO4J_URI=True):
            P.apply_ports_for(a, allocate=False)
        check("an operator's NEO4J_URI (escape hatch) is used VERBATIM, localhost spelling kept",
              os.environ.get("NEO4J_URI") == "bolt://localhost:7689", os.environ.get("NEO4J_URI"))
    # base-store probes dial 127.0.0.1
    seen: list = []
    with _Sandbox() as (home, work):
        a = _mk_project(work, "a", marker=True)
        with _Patch(P, is_bolt_up=lambda uri, *x, **k: seen.append(uri) or False):
            P._foreign_base_owner(a, "neo4j", "deepmap")
            P._record_live_base_owner(a)
    check("base-store probes (owner read, live-owner record) dial bolt://127.0.0.1:7687",
          bool(seen) and all(u == f"bolt://{LOOP}:7687" for u in seen), str(seen))
    with _Sandbox() as (home, work):
        fake = {"n_projects": 1, "projects": [{"key": "ab" * 20, "root": "/x", "bolt_port": 7691, "http_port": 7478,
                                               "neo4j_uri": None}]}
        with _Patch(PR, registry_stats=lambda env=None: fake), _Patch(P, _instance_running_for=lambda k: False):
            rows = P.instance_table()
        check("instance_table: a row whose record has no neo4j_uri shows ITS bolt port on 127.0.0.1",
              bool(rows) and rows[0].get("neo4j_uri") == f"bolt://{LOOP}:7691", str(rows))


def gate_a_uri_comparisons_by_endpoint() -> None:
    print("\n-- A (J4): URIs are compared by endpoint — an older build's localhost spelling is the same config --")
    with _Sandbox() as (home, work):
        a = _mk_project(work, "a", marker=True)
        s, e = PR.allocate_ports(a)
        _mcp_env((s, e))
        w = _World({6101: (SEARCH, s)})
        with _Patch(P, **w.stubs()):
            _seed_owned(SEARCH, s, 6101, a, uri="bolt://localhost:7691")   # what a <= v0.27 build recorded
            owner = P._mcp_owner_record(SEARCH, s)
            os.environ["NEO4J_URI"] = f"bolt://{LOOP}:7691"
            check("a sidecar fingerprinted with bolt://localhost:7691 is NOT stale against bolt://127.0.0.1:7691",
                  P._recorded_config_stale(owner, "deepmap") is False, str(owner))
            check("... nor for _own_server_config_stale (heal would restart it once for nothing)",
                  P._own_server_config_stale(SEARCH, s, a, P._server_config(password="deepmap")) is False)
            os.environ["NEO4J_URI"] = f"bolt://{LOOP}:7693"
            check("control: another PORT is a stale config", P._recorded_config_stale(owner, "deepmap") is True)
            os.environ["NEO4J_URI"] = f"bolt://{LOOP}:7691"
            check("control: another PASSWORD is a stale config", P._recorded_config_stale(owner, "other-pw") is True)
            _seed_owned(SEARCH, s, 6101, a, uri=f"bolt://{LOOP}:7691")
            owner2 = P._mcp_owner_record(SEARCH, s)
            os.environ["NEO4J_URI"] = "bolt://localhost:7691"
            check("a new sidecar (127.0.0.1) is not stale against a localhost-spelled current URI either",
                  P._recorded_config_stale(owner2, "deepmap") is False, str(owner2))
    # isolation_note: two spellings of one endpoint are ONE instance holding two projects
    stats = {"n_projects": 2, "projects": [{"neo4j_uri": "bolt://localhost:7691"},
                                           {"neo4j_uri": f"bolt://{LOOP}:7691"}]}
    note = P.isolation_note("community", stats)
    check("isolation_note: bolt://localhost:7691 + bolt://127.0.0.1:7691 -> the 'share a graph' WARNING",
          "WARNING" in note and "share a graph" in note, note)
    stats = {"n_projects": 2, "projects": [{"neo4j_uri": f"bolt://{LOOP}:7691"}, {"neo4j_uri": f"bolt://{LOOP}:7693"}]}
    check("control: two distinct instances -> no WARNING", "WARNING" not in P.isolation_note("community", stats))
    # instance-rm clears sync's per-instance write lock under BOTH spellings
    with _Sandbox() as (home, work):
        import sync as _sync
        locks = [_sync._instance_lock_path("bolt://localhost:7695"), _sync._instance_lock_path(f"bolt://{LOOP}:7695")]
        for lk in locks:
            lk.parent.mkdir(parents=True, exist_ok=True)
            lk.write_text("", encoding="utf-8")
        P._remove_instance_locks("ab" * 20, (), neo4j_pair=(7695, 7482), mcp_pair=None)
        check("instance-rm removes sync's per-instance lock for bolt://localhost:7695 AND bolt://127.0.0.1:7695",
              not any(lk.exists() for lk in locks), str([str(lk) for lk in locks if lk.exists()]))


# =========================================================================== B — J5 code identity

class _CodeWorld(_World):
    """_World whose spawn stub also records the start in provision's _MCP_STARTED (as the real spawn does)."""

    def _spawn(self, name, host, port, *, password=None, wait=None, target=None):
        ok = super()._spawn(name, host, port, password=password, wait=wait, target=target)
        pid = 7000 + len(self.spawned)          # the respawned server: a live process of the world, recorded like the
        self.procs[pid] = (name, int(port))     # real _spawn_mcp_server does (pid file + owner sidecar + _MCP_STARTED)
        self.live.add(pid)
        P._record_mcp_server(name, int(port), pid, target)
        P._MCP_STARTED.append(name)
        return ok


def _rewrite_owner(name, port, **fields) -> dict:
    of = P._mcp_ownerfile(name, int(port))
    rec = json.loads(of.read_text(encoding="utf-8"))
    for k, v in fields.items():
        if v is None:
            rec.pop(k, None)
        else:
            rec[k] = v
    of.write_text(json.dumps(rec), encoding="utf-8")
    return rec


def _script_cmd(root: str, name: str) -> str:
    script = f"{root}/build/{P.MCP_SERVERS[name]['script']}"
    return f'"{sys.executable}" "{script}"' if os.name == "nt" else f"{sys.executable} {script}"


def gate_b_sidecar_records_code() -> None:
    print("\n-- B (J5): a started server's owner sidecar records the code it runs --")
    with _Sandbox() as (home, work):
        a = _mk_project(work, "a", marker=True)
        s, e = PR.allocate_ports(a)
        _mcp_env((s, e))
        _seed_owned(SEARCH, s, 6201, a)
        rec = _read_json(P._mcp_ownerfile(SEARCH, s))
        cur = PR.code_identity(P.HERE)
        check("sidecar has code_root == port_registry.code_identity(build)['code_root']",
              rec.get(PR.CODE_ROOT_FIELD) == cur.get(PR.CODE_ROOT_FIELD) and rec.get(PR.CODE_ROOT_FIELD), str(rec))
        check("sidecar has code_version == plugin.json's version",
              rec.get(PR.CODE_VERSION_FIELD) == cur.get(PR.CODE_VERSION_FIELD), str(rec))
        check("sidecar names the server script it runs",
              str(rec.get("script") or "").replace("\\", "/").endswith(f"build/{P.MCP_SERVERS[SEARCH]['script']}"),
              str(rec.get("script")))
        check("port_registry reads it as CURRENT code (server_code_stale False)",
              PR.server_code_stale(rec, cur) is False, str(PR.server_code_stale(rec, cur)))


def _stale_world(a, s, pid=6301, **owner_fields):
    w = _CodeWorld({pid: (SEARCH, s)})
    with _Patch(P, **w.stubs()):
        _seed_owned(SEARCH, s, pid, a)
    if owner_fields:
        _rewrite_owner(SEARCH, s, **owner_fields)
    return w


def gate_b_heal_restarts_old_code() -> None:
    print("\n-- B (J5): heal / mcp-start restart THIS project's server when it runs other DeepMap code --")
    old = {PR.CODE_ROOT_FIELD: "/plugins/cache/deep-map/0.27.5", PR.CODE_VERSION_FIELD: "0.27.5",
           "script": "/plugins/cache/deep-map/0.27.5/build/deepmap_search_server.py"}
    with _Sandbox() as (home, work):
        a = _mk_project(work, "a", marker=True)
        s, e = PR.allocate_ports(a)
        _mcp_env((s, e))
        w = _stale_world(a, s, **old)
        del P._MCP_STARTED[:]
        with _Patch(P, **w.stubs()):
            ok, out = _captured(P.start_mcp_server, SEARCH, password="deepmap", target=a)
        check("an own server recorded as running 0.27.5 from another plugin dir is restarted (stop + spawn)",
              ok is True and (6301, True) in w.killed and (SEARCH, s) in w.spawned, f"{ok} {w.killed} {w.spawned}")
        check("... it says why (the old code), and the restart is reported as a start (_MCP_STARTED)",
              "0.27.5" in out and SEARCH in P._MCP_STARTED, out.strip()[-300:])
    with _Sandbox() as (home, work):
        a = _mk_project(work, "a", marker=True)
        s, e = PR.allocate_ports(a)
        _mcp_env((s, e))
        w = _stale_world(a, s, **{PR.CODE_VERSION_FIELD: "0.0.1"})   # same dir, another version (in-place update)
        with _Patch(P, **w.stubs()):
            ok = _quiet(P.start_mcp_server, SEARCH, password="deepmap", target=a)
        check("same plugin dir but another recorded version -> restarted", ok is True and w.spawned, str(w.spawned))
    with _Sandbox() as (home, work):
        a = _mk_project(work, "a", marker=True)
        s, e = PR.allocate_ports(a)
        _mcp_env((s, e))
        w = _stale_world(a, s)   # current code
        with _Patch(P, **w.stubs()):
            ok, out = _captured(P.start_mcp_server, SEARCH, password="deepmap", target=a)
        check("control: an own server running the CURRENT code is left alone ('already listening')",
              ok is True and not w.killed and not w.spawned and "already listening" in out, f"{w.killed} {out[-200:]}")
    # pre-J5 sidecars: the running process's command line decides
    for label, root, cmd_none, want in (("pre-J5 sidecar, command line runs another plugin dir -> restarted",
                                         "/plugins/cache/deep-map/0.27.5", False, True),
                                        ("pre-J5 sidecar, command line runs THIS code (started after it was "
                                         "written — ROUND-10 L3) -> left alone",
                                         str(P.HERE.parent).replace("\\", "/"), False, False),
                                        ("pre-J5 sidecar, command line unreadable -> unknown, left alone",
                                         "/plugins/cache/deep-map/0.27.5", True, False)):
        with _Sandbox() as (home, work):
            a = _mk_project(work, "a", marker=True)
            s, e = PR.allocate_ports(a)
            _mcp_env((s, e))
            w = _stale_world(a, s, **{PR.CODE_ROOT_FIELD: None, PR.CODE_VERSION_FIELD: None, "script": None})
            started_now = time.time()   # ROUND-10 L3: a server started from the code now on disk (not before an update)
            w.created = started_now
            _rewrite_owner(SEARCH, s, created=started_now)
            w.cmd[6301] = None if cmd_none else _script_cmd(root, SEARCH)
            if cmd_none:
                w.cmd[6301] = None
            with _Patch(P, **w.stubs()):
                ok = _quiet(P.start_mcp_server, SEARCH, password="deepmap", target=a)
            restarted = bool(w.spawned)
            check(label, ok is True and restarted is want, f"ok={ok} killed={w.killed} spawned={w.spawned}")
    # never another project's server, never an unverifiable pid
    with _Sandbox() as (home, work):
        a = _mk_project(work, "a", marker=True)
        b = _mk_project(work, "b", marker=True)
        s, e = PR.allocate_ports(a)
        _mcp_env((s, e))
        w = _CodeWorld({6401: (SEARCH, s)})
        with _Patch(P, **w.stubs()):
            _seed_owned(SEARCH, s, 6401, b)   # the sidecar names project b
        _rewrite_owner(SEARCH, s, **old)
        with _Patch(P, **w.stubs()):
            ok = _quiet(P.start_mcp_server, SEARCH, password="deepmap", target=a)
        check("another project's server with old code is never signalled", not w.killed and not w.spawned,
              f"{w.killed} {w.spawned}")
    with _Sandbox() as (home, work):
        a = _mk_project(work, "a", marker=True)
        s, e = PR.allocate_ports(a)
        _mcp_env((s, e))
        w = _stale_world(a, s, **old)
        w.created = 555.0   # the live pid started at another time than the sidecar recorded: not provably ours
        with _Patch(P, **w.stubs()):
            ok = _quiet(P.start_mcp_server, SEARCH, password="deepmap", target=a)
        check("an own sidecar whose pid is not provably the recorded process (start stamp) is never signalled",
              not w.killed and not w.spawned, f"{w.killed} {w.spawned}")
    with _Sandbox() as (home, work):
        a = _mk_project(work, "a", marker=True)
        s, e = PR.allocate_ports(a)
        _mcp_env((s, e))
        w = _stale_world(a, s, **old)
        w.kill_ok = False
        with _Patch(P, **w.stubs()):
            ok, out = _captured(P.start_mcp_server, SEARCH, password="deepmap", target=a)
        check("a restart whose stop fails keeps the old (working) server: True + a WARNING, never a false 'down'",
              ok is True and not w.spawned and "WARNING" in out, f"{ok} {out.strip()[-300:]}")


def gate_b_cmdline_script_parse() -> None:
    print("\n-- B (J5): the script path a server's command line runs (the evidence for a pre-J5 sidecar) --")
    s = "deepmap_search_server.py"
    for label, cmd, want in (
            ("Windows, quoted (a path with spaces)",
             r'"C:\Program Files\Py\python.exe" "C:\Users\a b\plugins\deep-map\0.27.5\build\deepmap_search_server.py"',
             r"C:\Users\a b\plugins\deep-map\0.27.5\build\deepmap_search_server.py"),
            ("Windows, unquoted", r"C:\Py\python.exe C:\p\deep-map\0.27.5\build\deepmap_search_server.py",
             r"C:\p\deep-map\0.27.5\build\deepmap_search_server.py"),
            ("POSIX /proc form", "/usr/bin/python3 /home/u/.claude/plugins/deep-map/0.27.5/build/deepmap_search_server.py",
             "/home/u/.claude/plugins/deep-map/0.27.5/build/deepmap_search_server.py"),
            ("another script only", "/usr/bin/python3 /x/build/deepmap_embed_server.py", None),
            ("unreadable (None)", None, None)):
        got = P._script_from_cmdline(cmd, s)
        check(f"{label}: {got!r}", got == want, f"want {want!r}")
    with tempfile.TemporaryDirectory(prefix="dmr8s") as d:
        spaced = Path(d) / "plug root" / "build"
        spaced.mkdir(parents=True)
        (spaced / s).write_text("x", encoding="utf-8")
        cmd = f"/usr/bin/python3 {(spaced / s)}"   # a space-joined POSIX command line, a path WITH a space
        got = P._script_from_cmdline(cmd, s)
        check("a space-joined command line whose script path has a space: the existing file is found",
              got is not None and _same_dir(got, spaced / s), str(got))


def gate_b_heal_end_to_end() -> None:
    print("\n-- B (J5): heal end to end — the restarted servers land in the heal status (mcp_started + epoch) --")
    old = {PR.CODE_ROOT_FIELD: "/plugins/cache/deep-map/0.27.5", PR.CODE_VERSION_FIELD: "0.27.5"}
    with _Sandbox() as (home, work):
        a = _mk_project(work, "a", marker=True)
        s, e = PR.allocate_ports(a)
        PR.allocate_neo4j_ports(a, base_adopt_ok=lambda: False)
        w = _CodeWorld({6501: (SEARCH, s), 6502: (EMBED, e)})
        _mcp_env((s, e))
        own = PR.bolt_uri(PR.lookup_neo4j_ports(a)[0])   # the config heal gives a server (the project's own instance)
        with _Patch(P, **w.stubs()):
            _seed_owned(SEARCH, s, 6501, a, uri=own)
            _seed_owned(EMBED, e, 6502, a, uri=own)
        _rewrite_owner(SEARCH, s, **old)
        _rewrite_owner(EMBED, e, **old)
        _clear_port_env()
        with _Patch(P, **w.stubs()), _Patch(P, ensure_neo4j=lambda **k: True, _ensure_mcp_json=lambda t: None):
            rc, out = _captured(P.main, ["heal", str(a)])
        st = _read_json(home / "run" / f"heal-{PR.project_key(a)}.json")
        check("heal rc 0, both old-code servers restarted", rc == 0 and len(w.spawned) == 2,
              f"rc={rc} spawned={w.spawned} {out.strip()[-300:]}")
        check("heal status: mcp_started names both + an mcp_started_epoch (the client must reconnect)",
              sorted(st.get("mcp_started") or []) == sorted([SEARCH, EMBED])
              and isinstance(st.get("mcp_started_epoch"), (int, float)), str(st))
        rec = _read_json(P._mcp_ownerfile(SEARCH, s))
        check("... and the new sidecars record the current code", PR.server_code_stale(rec) is False, str(rec))
    with _Sandbox() as (home, work):
        a = _mk_project(work, "a", marker=True)
        s, e = PR.allocate_ports(a)
        PR.allocate_neo4j_ports(a, base_adopt_ok=lambda: False)
        w = _CodeWorld({6601: (SEARCH, s), 6602: (EMBED, e)})
        _mcp_env((s, e))
        own = "bolt://localhost:%d" % PR.lookup_neo4j_ports(a)[0]   # an older build's spelling: same config (J4)
        with _Patch(P, **w.stubs()):
            _seed_owned(SEARCH, s, 6601, a, uri=own)
            _seed_owned(EMBED, e, 6602, a, uri=own)
        _rewrite_owner(SEARCH, s, **old)
        _clear_port_env()
        with _Patch(P, **w.stubs()), _Patch(P, verify_mcp=lambda *x, **k: True, _ensure_mcp_json=lambda t: None,
                                            _print_mcp_hint=lambda *x, **k: None):
            rc, out = _captured(P.main, ["mcp-start", str(a)])
        check("mcp-start restarts the old-code server too (only that one)", rc == 0 and w.spawned == [(SEARCH, s)],
              f"rc={rc} {w.spawned}")


# =========================================================================== C — J6

class _QRes:
    def __init__(self, rows):
        self.rows = rows

    def __iter__(self):
        return iter(self.rows)

    def single(self):
        return self.rows[0] if self.rows else None


class _QSess:
    def __init__(self, drv):
        self.drv = drv

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, q, **k):
        if "body_summary" in q or "docstring" in q:
            return _QRes([{"text": t} for t in self.drv.summaries])
        return _QRes([{"name": n} for n in self.drv.names])


class _QDrv:
    def __init__(self, names, summaries=()):
        self.names, self.summaries = list(names), list(summaries)

    def verify_connectivity(self):
        return None

    def session(self, *a, **k):
        return _QSess(self)

    def close(self):
        return None


def _fts_visible(q: str) -> bool:
    """retrieval._fts_terms keeps a word > 2 chars with alphanumeric content — the BM25 arm's view of a query."""
    try:
        import retrieval as R   # build/ is on sys.path (the round-4 harness)
        return bool(R._fts_terms(q))
    except Exception:
        return any(len(t) > 2 and any(c.isalnum() for c in t) for t in q.split())


def gate_c_verify_query_fts_visible() -> None:
    print("\n-- C (J6): setup's verify query is FTS-visible; an empty answer retries the fixed phrase --")
    for label, names, summaries, want in (
            ("Go `T.Do` first (tail 'Do' is dropped by FTS)", ["T.Do", "db", "ok"], [], "T.Do"),
            ("short names first, a real identifier later", ["Do", "ok", "parse_config"], [], "parse_config"),
            ("a plain identifier beats a dotted method name", ["Context.invoke", "helper"], [], "helper"),
            ("every name <= 2 chars -> a word from a stored summary",
             ["f", "g"], ["Function f in a.py: returns the total"], None)):
        q = P._verify_query(_QDrv(names, summaries))
        ok = (q == want) if want is not None else (q != P._VERIFY_FALLBACK_QUERY and q.lower() in summaries[0].lower())
        check(f"{label}: query {q!r} is FTS-visible" + (f" and == {want!r}" if want else " (a summary word)"),
              ok and _fts_visible(q), q)
    check("no readable name or summary -> the fixed phrase", P._verify_query(_QDrv([], [])) == P._VERIFY_FALLBACK_QUERY)
    seen: list = []

    class _FtsOnlyRet:
        """keyword-only search (the embedding model is unavailable): answers iff the query has an FTS term."""
        def __init__(self, drv, embed_fn=None):
            pass

        def search(self, q, top_k=5):
            seen.append(q)
            return [{"name": "x", "type": "Function"}] if _fts_visible(q) else []
    mods = {"embeddings": types.SimpleNamespace(embed_query=lambda t: None),
            "retrieval": types.SimpleNamespace(DeepMapRetriever=_FtsOnlyRet)}
    with _Patch(P, _load=lambda n: mods[n], _make_driver=lambda *a, **k: _QDrv(["Do", "T.Do"])):
        ok = _quiet(P.verify_search, f"bolt://{LOOP}:7689", "neo4j", "deepmap")
    check("an FTS-only setup whose first node is `Do` does NOT fail the verify", ok is True, str(seen))

    class _EmptyFirstRet(_FtsOnlyRet):
        def search(self, q, top_k=5):
            seen.append(q)
            return [{"name": "x", "type": "Function"}] if q == P._VERIFY_FALLBACK_QUERY else []
    seen.clear()
    mods["retrieval"] = types.SimpleNamespace(DeepMapRetriever=_EmptyFirstRet)
    with _Patch(P, _load=lambda n: mods[n], _make_driver=lambda *a, **k: _QDrv(["parse_config"])):
        ok = _quiet(P.verify_search, f"bolt://{LOOP}:7689", "neo4j", "deepmap")
    check("the derived query answers [] -> the fixed phrase is tried before FAILED",
          ok is True and seen == ["parse_config", P._VERIFY_FALLBACK_QUERY], str(seen))
    seen.clear()
    with _Patch(P, _load=lambda n: mods[n], _make_driver=lambda *a, **k: _QDrv(["parse_config"])):
        ok = _quiet(P.verify_search, f"bolt://{LOOP}:7689", "neo4j", "deepmap", "explicit query")
    check("an explicit query is used as given (no silent substitute)", ok is False and seen == ["explicit query"],
          f"{ok} {seen}")


def gate_c_instance_rm_purge_cleanup() -> None:
    print("\n-- C (J6): instance-rm --purge removes setup's blocker and the per-port MCP logs (never a live one's) --")
    with _Sandbox() as (home, work):
        a = _mk_project(work, "a", marker=True)
        b = _mk_project(work, "b", marker=True)
        s, e = PR.allocate_ports(a)
        sb, eb = PR.allocate_ports(b)
        PR.allocate_neo4j_ports(a, base_adopt_ok=lambda: False)
        k = PR.project_key(a)
        blk = PR.setup_blocker_path(a)
        blk.parent.mkdir(parents=True, exist_ok=True)
        blk.write_text(json.dumps({"kind": "mcp-blocked", "reason": "x"}), encoding="utf-8")
        blk_b = PR.setup_blocker_path(b)
        blk_b.write_text(json.dumps({"kind": "mcp-blocked", "reason": "y"}), encoding="utf-8")
        logs = {n: home / f"{n}-{p}.log" for n, p in ((SEARCH, s), (EMBED, e))}
        other_logs = [home / f"{SEARCH}-{sb}.log", home / f"{EMBED}-{eb}.log"]
        for lf in list(logs.values()) + other_logs:
            lf.write_text("old server output\n", encoding="utf-8")
        with _Patch(P, _stop_neo4j_and_wait=lambda *x, **kw: None, _live_instance_pid=lambda kk: None,
                    _stop_mcp_servers_for_key=lambda *x, **kw: None, is_http_up=lambda h, p, timeout=2.0: False):
            rc, out = _captured(P.instance_rm, str(a), key=k, yes=True, purge=True)
        check("instance-rm --purge rc 0", rc == 0, out.strip()[-300:])
        check("... setup's persisted blocker (setup-blocked-<key>.json) is gone", not blk.exists(), str(blk))
        check("... the project's per-port server logs are gone", not any(p.exists() for p in logs.values()),
              str([str(p) for p in logs.values() if p.exists()]))
        check("... another project's blocker and logs are untouched", blk_b.exists() and all(p.exists() for p in other_logs))
    with _Sandbox() as (home, work):
        a = _mk_project(work, "a", marker=True)
        s, e = PR.allocate_ports(a)
        k = PR.project_key(a)
        lf = home / f"{SEARCH}-{s}.log"
        lf.parent.mkdir(parents=True, exist_ok=True)
        lf.write_text("live\n", encoding="utf-8")
        with _Patch(P, is_http_up=lambda h, p, timeout=2.0: False):
            P._remove_instance_locks(k, (), mcp_pair=(s, e), purge=True)
        gone = not lf.exists()
        lf.write_text("live\n", encoding="utf-8")
        with _Patch(P, is_http_up=lambda h, p, timeout=2.0: int(p) == s):
            P._remove_instance_locks(k, (), mcp_pair=(s, e), purge=True)
        check("the lock cleanup removes a quiet port's log, but keeps one a server still listens on",
              gone and lf.exists(), f"gone={gone} kept={lf.exists()}")
        lf2 = home / f"{EMBED}-{e}.log"
        lf2.write_text("x\n", encoding="utf-8")
        with _Patch(P, is_http_up=lambda h, p, timeout=2.0: False):
            P._remove_instance_locks(k, (), mcp_pair=(s, e), purge=False)
        check("a plain (non-purge) instance-rm keeps the logs (the MCP pair stays registered)", lf2.exists())


class _ArgvSpy:
    """A stand-in for provision's `subprocess` module that records argv and answers like git."""

    def __init__(self):
        self.calls: list = []
        self.TimeoutExpired = subprocess.TimeoutExpired
        self.DEVNULL = subprocess.DEVNULL
        self.PIPE = subprocess.PIPE

    def run(self, argv, *a, **k):
        self.calls.append(list(argv))
        return types.SimpleNamespace(returncode=0, stdout=b"/tmp/x\n" if not k.get("text") else "/tmp/x\n",
                                     stderr=b"" if not k.get("text") else "")


def gate_c_git_longpaths() -> None:
    print("\n-- C (J6): provision's git probes pass -c core.longpaths=true on Windows (only there) --")
    spy = _ArgvSpy()
    with _Patch(P, subprocess=spy):
        P._git_text(".", "rev-parse", "--show-toplevel")
        P._git_tracked(Path("."), ".mcp.json")
    gits = [c for c in spy.calls if c and Path(str(c[0])).stem.lower() == "git"]
    want = os.name == "nt"
    check(f"every git argv {'carries' if want else 'omits'} core.longpaths=true (os.name={os.name})",
          bool(gits) and all(("core.longpaths=true" in c) == want for c in gits), str(gits))


# =========================================================================== D — J1 git hook

class _GitBox:
    """A throwaway work dir + DEEPMAP_HOME, an ISOLATED git config (empty global, no system — a developer's own
    core.hooksPath must not leak in), and DEEPMAP_PYTHON -> a sh stub that records `sync <target>` calls."""

    def __enter__(self):
        self.base = Path(tempfile.mkdtemp(prefix="dmr8g")).resolve()
        self.saved = {k: os.environ.get(k) for k in ("DEEPMAP_HOME", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM",
                                                     "DEEPMAP_PYTHON", "GIT_DIR", "GIT_WORK_TREE")}
        for k in ("GIT_DIR", "GIT_WORK_TREE"):
            os.environ.pop(k, None)
        self.home = self.base / "home"
        self.home.mkdir()
        self.gcfg = self.base / "gitconfig"
        self.gcfg.write_text("[user]\n\tname = t\n\temail = t@t\n[init]\n\tdefaultBranch = main\n", encoding="utf-8")
        self.log = self.base / "synced.log"
        self.stub = self.base / "stub-python.sh"
        self.stub.write_text("#!/bin/sh\n# records: <cli> sync <target>\n"
                             f"printf '%s\\n' \"$3\" >> '{self.log.as_posix()}'\n", encoding="utf-8", newline="\n")
        os.chmod(self.stub, 0o755)
        os.environ.update({"DEEPMAP_HOME": str(self.home), "GIT_CONFIG_GLOBAL": str(self.gcfg),
                           "GIT_CONFIG_NOSYSTEM": "1", "DEEPMAP_PYTHON": self.stub.as_posix()})
        self.work = self.base / "w"
        self.work.mkdir()
        return self

    def __exit__(self, *a):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _rmtree(self.base)

    # ---- helpers
    def git(self, cwd, *args, check=True) -> str:
        r = subprocess.run([shutil.which("git"), "-C", str(cwd), *args], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=60)
        if check and r.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()}")
        return r.stdout.strip()

    def repo(self, name, *, manifest_at="") -> Path:
        d = self.work / name
        d.mkdir(parents=True)
        self.git(d, "init", "-q")
        (d / "a.txt").write_text("a\n", encoding="utf-8")
        self.git(d, "add", "a.txt")
        self.git(d, "commit", "-qm", "init")
        if manifest_at is not None:
            self.manifest(d / manifest_at if manifest_at else d)
        return d.resolve()

    @staticmethod
    def manifest(d: Path) -> None:
        (d / ".deepmap").mkdir(parents=True, exist_ok=True)
        (d / ".deepmap" / "graph_build.json").write_text('{"verdict": "READY", "nodes": 5}', encoding="utf-8")

    def hook(self, repo) -> Path:
        rel = self.git(repo, "rev-parse", "--git-path", "hooks/post-commit")
        p = Path(rel)
        return p if p.is_absolute() else (Path(repo) / p)

    def synced(self, wait_s: float = 12.0, want: int = 1) -> list:
        deadline = time.time() + wait_s
        while time.time() < deadline:
            if self.log.exists() and len(self.log.read_text(encoding="utf-8").splitlines()) >= want:
                break
            time.sleep(0.2)
        time.sleep(0.3)
        return [ln.strip() for ln in self.log.read_text(encoding="utf-8").splitlines()] if self.log.exists() else []

    def reset_log(self) -> None:
        self.log.unlink(missing_ok=True)

    def commit(self, repo, msg="c") -> int:
        f = Path(repo) / "a.txt"
        f.write_text(f.read_text(encoding="utf-8") + msg + "\n", encoding="utf-8")
        r = subprocess.run([shutil.which("git"), "commit", "-qam", msg], cwd=str(repo), capture_output=True,
                           text=True, timeout=60)
        return r.returncode

    def run_hook(self, hook: Path, cwd) -> int:
        sh = shutil.which("sh") or shutil.which("bash")
        r = subprocess.run([sh, str(hook)], cwd=str(cwd), capture_output=True, text=True, timeout=60)
        return r.returncode


def _same_dir(a, b) -> bool:
    try:
        na, nb = str(Path(a).resolve()), str(Path(b).resolve())
        return os.path.normcase(na) == os.path.normcase(nb)
    except Exception:
        return False


def _targets(text: str) -> list:
    return [m.group(1) for m in re.finditer(r"deepmap_cli\.py'?\"?\s+sync\s+'([^']*)'", text or "")]


def _syncflag():
    hooks = REPO / "hooks"
    if str(hooks) not in sys.path:
        sys.path.insert(0, str(hooks))
    try:
        import deepmap_postedit_syncflag as SF   # type: ignore
        return SF
    except Exception:
        return None


def gate_d_sync_root_not_toplevel() -> None:
    print("\n-- D (J1): the hook syncs the project's SYNC ROOT (a sub-project set up on its own), not the toplevel --")
    if not shutil.which("git"):
        check("git is available for the git-hook gates", False)
        return
    with _GitBox() as gb:
        top = gb.repo("mono", manifest_at="backend")
        backend = top / "backend"
        (backend / "pkg").mkdir(parents=True)
        rc, out = _captured(P.git_hook, str(backend / "pkg"), "install")
        text = gb.hook(top).read_text(encoding="utf-8") if gb.hook(top).exists() else ""
        tg = _targets(text)
        check("install from backend/pkg -> rc 0 and ONE sync target", rc == 0 and len(tg) == 1, f"rc={rc} {tg} {out}")
        check("... the target is backend (the sync root: its manifest), not the git toplevel",
              bool(tg) and _same_dir(tg[0], backend), str(tg))
        gb.reset_log()
        check("... committing runs it: the stub records a sync of backend",
              gb.commit(top) == 0 and [t for t in gb.synced() if _same_dir(t, backend)], str(gb.synced(0.1)))
        rep = P._git_hook_report(str(backend))
        check("status for the sub-project: 'current'", rep.get("state") == "current", str(rep))
        SF = _syncflag()
        if SF is not None:
            check("the syncflag hook's detector (W1) claims it for backend, not for the toplevel",
                  SF._git_hook_installed(backend) is True and SF._git_hook_installed(top) is False)


def gate_d_worktrees_scoped() -> None:
    print("\n-- D (J1): a hooks file shared by a main checkout and its linked worktree — each block runs only for its "
          "own work tree (real commits) --")
    if not shutil.which("git"):
        return
    with _GitBox() as gb:
        m = gb.repo("M")
        wt = gb.work / "W"
        gb.git(m, "worktree", "add", "-q", str(wt))
        wt = wt.resolve()
        gb.manifest(wt)
        hm, hw = gb.hook(m), gb.hook(wt)
        check("git resolves ONE shared hooks file for both work trees (the common dir)", _same_dir(hm, hw), f"{hm} {hw}")
        rc = _quiet(P.git_hook, str(wt), "install")
        check("install in the worktree -> rc 0", rc == 0)
        rep_m = P._git_hook_report(str(m))
        check("the main checkout's status: NOT installed (W's block is not M's), and it names the other work tree",
              rep_m.get("state") != "current" and int(rep_m.get("others") or 0) == 1, str(rep_m))
        gb.reset_log()
        gb.commit(m, "m1")
        got_m = gb.synced(4.0)
        check("a commit in M does NOT sync W (the block is scoped to W's work tree)", got_m == [], str(got_m))
        gb.reset_log()
        gb.commit(wt, "w1")
        got_w = gb.synced()
        check("a commit in W syncs W", len(got_w) == 1 and _same_dir(got_w[0], wt), str(got_w))
        rc = _quiet(P.git_hook, str(m), "install")
        text = hm.read_text(encoding="utf-8")
        check("install in M adds M's own block next to W's (two scoped blocks)",
              rc == 0 and len(_targets(text)) == 2, str(_targets(text)))
        gb.reset_log()
        gb.commit(m, "m2")
        got = gb.synced()
        check("now a commit in M syncs M only", len(got) == 1 and _same_dir(got[0], m), str(got))
        rc = _quiet(P.git_hook, str(m), "uninstall")
        text = hm.read_text(encoding="utf-8") if hm.exists() else ""
        tg = _targets(text)
        check("M's uninstall removes ONLY M's block — W's stays", rc == 0 and len(tg) == 1 and _same_dir(tg[0], wt),
              str(tg))
        check("W's status is still current", P._git_hook_report(str(wt)).get("state") == "current")
        SF = _syncflag()
        if SF is not None:
            check("syncflag detector (W1): W claims, M does not", SF._git_hook_installed(wt) is True
                  and SF._git_hook_installed(m) is False)
        gb.git(m, "worktree", "remove", "--force", str(wt), check=False)


def gate_d_refuses_shared_or_foreign_hooks() -> None:
    print("\n-- D (J1): refuses hooks files outside the repo, husky / tracked / non-sh / binary / disabled hooks --")
    if not shutil.which("git"):
        return
    with _GitBox() as gb:
        a = gb.repo("a")
        shared = gb.base / "global-hooks"
        shared.mkdir()
        gb.git(a, "config", "--global", "core.hooksPath", shared.as_posix())
        rc, out = _captured(P.git_hook, str(a), "install")
        check("a global core.hooksPath (outside the repo) -> refused rc 2, nothing written",
              rc == 2 and not (shared / "post-commit").exists() and "hooksPath" in out, f"rc={rc} {out.strip()[-300:]}")
        rep = P._git_hook_report(str(a))
        check("... status explains the refusal", bool(rep.get("refusal")), str(rep))
        gb.git(a, "config", "--global", "--unset", "core.hooksPath")
        gb.git(a, "config", "core.hooksPath", "../outside-hooks")
        rc = _quiet(P.git_hook, str(a), "install")
        check("a relative core.hooksPath that leaves the repo -> refused", rc == 2 and not (gb.work / "outside-hooks"
                                                                                        / "post-commit").exists())
        gb.git(a, "config", "core.hooksPath", ".husky/_")
        (a / ".husky" / "_").mkdir(parents=True)
        (a / ".husky" / "_" / "post-commit").write_text('#!/usr/bin/env sh\n. "$(dirname "$0")/h"\n', encoding="utf-8")
        before = (a / ".husky" / "_" / "post-commit").read_bytes()
        rc, out = _captured(P.git_hook, str(a), "install")
        check("a husky-managed hooks dir -> refused, the stub untouched",
              rc == 2 and (a / ".husky" / "_" / "post-commit").read_bytes() == before and "husky" in out.lower(),
              f"rc={rc} {out.strip()[-200:]}")
        gb.git(a, "config", "core.hooksPath", ".githooks")
        (a / ".githooks").mkdir()
        (a / ".githooks" / "post-commit").write_text("#!/bin/sh\necho team hook\n", encoding="utf-8", newline="\n")
        gb.git(a, "add", ".githooks/post-commit")
        gb.git(a, "commit", "-qm", "team hooks")
        before = (a / ".githooks" / "post-commit").read_bytes()
        rc, out = _captured(P.git_hook, str(a), "install")
        check("a git-TRACKED hook (a committed .githooks) -> refused, the file untouched",
              rc == 2 and (a / ".githooks" / "post-commit").read_bytes() == before and "tracked" in out.lower(),
              f"rc={rc} {out.strip()[-200:]}")
        gb.git(a, "config", "--unset", "core.hooksPath")
    with _GitBox() as gb:
        a = gb.repo("a")
        hp = gb.hook(a)
        hp.parent.mkdir(parents=True, exist_ok=True)
        for label, body, word in (("a Python hook", b"#!/usr/bin/env python3\nprint('mine')\n", "python"),
                                  ("a Node hook", b"#!/usr/bin/env node\nconsole.log('mine')\n", "node"),
                                  ("a binary hook", b"\x7fELF\x02\x01\x01\x00\x00\x00", "")):
            hp.write_bytes(body)
            rc, out = _captured(P.git_hook, str(a), "install")
            check(f"{label} -> refused rc 2, byte-identical" + (f", the message names {word}" if word else ""),
                  rc == 2 and hp.read_bytes() == body and (word in out.lower() if word else True),
                  f"rc={rc} {out.strip()[-200:]}")
        if os.name != "nt":
            hp.write_text("#!/bin/sh\necho disabled\n", encoding="utf-8")
            os.chmod(hp, 0o644)
            before = hp.read_bytes()
            rc = _quiet(P.git_hook, str(a), "install")
            check("POSIX: a NON-executable (disabled) hook -> refused, never re-enabled",
                  rc == 2 and hp.read_bytes() == before and not os.access(str(hp), os.X_OK))


def gate_d_insert_after_shebang() -> None:
    print("\n-- D (J1): the block goes right after the shebang — a trailing exec / exit can never skip it --")
    if not shutil.which("git"):
        return
    for label, body in (("a hook ending in `exec ...` (the pre-commit framework's)",
                         "#!/bin/sh\necho user-hook\nexec true\n"),
                        ("a hook with a top-level `exit 0`", "#!/usr/bin/env bash\necho user-hook\nexit 0\n"),
                        ("a CRLF sh hook", "#!/bin/sh\r\necho user-hook\r\n")):
        with _GitBox() as gb:
            a = gb.repo("a")
            hp = gb.hook(a)
            hp.parent.mkdir(parents=True, exist_ok=True)
            with open(hp, "w", encoding="utf-8", newline="") as f:
                f.write(body)
            os.chmod(hp, 0o755)
            rc, out = _captured(P.git_hook, str(a), "install")
            text = hp.read_bytes().decode("utf-8")
            lines = text.splitlines()
            check(f"{label}: rc 0, the block sits right after the shebang",
                  rc == 0 and len(lines) > 2 and lines[1].startswith("# >>> deepmap"), f"rc={rc} {text!r}")
            eol = "\r\n" if "\r\n" in body else "\n"
            rest = text.split(eol, 1)[1]
            check(f"{label}: the user's hook is kept verbatim after the block",
                  text.startswith(body.split(eol, 1)[0] + eol) and rest.endswith(body.split(eol, 1)[1]), repr(text))
            if eol == "\n":
                gb.reset_log()
                gb.run_hook(hp, a)
                got = gb.synced()
                check(f"{label}: running the hook syncs the project", len(got) == 1 and _same_dir(got[0], a), str(got))
            rc = _quiet(P.git_hook, str(a), "uninstall")
            check(f"{label}: uninstall restores the original bytes", rc == 0 and hp.read_bytes() == body.encode("utf-8"),
                  repr(hp.read_bytes()))


def gate_d_status_and_upgrade_of_stale_blocks() -> None:
    print("\n-- D (J1): --status tells a current block from a stale one; --install replaces stale ones in place --")
    if not shutil.which("git"):
        return
    with _GitBox() as gb:
        a = gb.repo("a")
        hp = gb.hook(a)
        pc = hp.with_name("post-checkout")
        hp.parent.mkdir(parents=True, exist_ok=True)
        old_cli = "/home/u/.claude/plugins/cache/deep-map/0.27.2/build/deepmap_cli.py"
        legacy = (f'mkdir -p "$HOME/.deepmap" 2>/dev/null; ( nohup python3 "{old_cli}" sync "{a.as_posix()}" '
                  '--stale-cleanup >"$HOME/.deepmap/sync.log" 2>&1 & ) || true   # deepmap')
        user = "#!/bin/sh\necho user-hook\n"
        hp.write_text(user + "\n# >>> deepmap (chained; existing hook preserved) >>>\n" + legacy + "\n# <<< deepmap <<<\n",
                      encoding="utf-8", newline="\n")
        pc.write_text("#!/bin/sh\n" + legacy + "\n", encoding="utf-8", newline="\n")
        os.chmod(hp, 0o755)
        os.chmod(pc, 0o755)
        rep = P._git_hook_report(str(a))
        why = " ".join(rep.get("reasons") or [])
        check("a v0.27.2 installer line: status is STALE (not 'installed')", rep.get("state") == "stale", str(rep))
        check("... the reasons name --stale-cleanup and the missing plugin path",
              "--stale-cleanup" in why and ("no longer exists" in why or "missing" in why), why)
        rc, out = _captured(P.git_hook, str(a), "status")
        check("... `git-hook --status` says so (rc 0) and names the post-checkout line too",
              rc == 0 and "stale" in out.lower() and "post-checkout" in out, out.strip()[-400:])
        rc, out = _captured(P.git_hook, str(a), "install")
        text = hp.read_text(encoding="utf-8")
        check("--install replaces it in place: no --stale-cleanup, no old path, exactly one current block",
              rc == 0 and "--stale-cleanup" not in text and old_cli not in text and text.count("# >>> deepmap") == 1
              and P._git_hook_report(str(a)).get("state") == "current", text)
        check("... the user's own hook lines are kept", "echo user-hook" in text and text.startswith("#!/bin/sh\n"))
        pct = pc.read_text(encoding="utf-8") if pc.exists() else ""
        check("... the old installer's destructive post-checkout line is removed", "--stale-cleanup" not in pct
              and "deepmap_cli" not in pct, pct)
    with _GitBox() as gb:
        a = gb.repo("a")
        hp = gb.hook(a)
        hp.parent.mkdir(parents=True, exist_ok=True)
        # a round-7 block: unscoped, appended at the end, syncing the toplevel
        cli = (P.HERE / "deepmap_cli.py").as_posix()
        r7 = ("# >>> deepmap (managed; existing hook preserved on chain) >>>\n"
              f"mkdir -p '{gb.home.as_posix()}' 2>/dev/null; ( PYTHONUTF8=1 nohup '{gb.stub.as_posix()}' '{cli}' sync "
              f"'{a.as_posix()}' >'{(gb.home / 'sync.log').as_posix()}' 2>&1 & ) || true   # deepmap\n"
              "# <<< deepmap <<<\n")
        hp.write_text("#!/bin/sh\necho x\n\n" + r7, encoding="utf-8", newline="\n")
        os.chmod(hp, 0o755)   # an active hook (POSIX: git skips a non-executable one, and install refuses it)
        rep = P._git_hook_report(str(a))
        check("a round-7 (unscoped) block: status stale, reason says it is not scoped to this work tree",
              rep.get("state") == "stale" and any("scoped" in r for r in rep.get("reasons") or []), str(rep))
        rc = _quiet(P.git_hook, str(a), "install")
        text = hp.read_text(encoding="utf-8")
        check("--install upgrades it (one scoped block, after the shebang)", rc == 0 and text.count("# >>> deepmap") == 1
              and text.splitlines()[1].startswith("# >>> deepmap") and "-ef" in text, text)
        before = hp.read_bytes()
        rc, out = _captured(P.git_hook, str(a), "install")
        check("--install again: 'already installed', byte-identical", rc == 0 and hp.read_bytes() == before
              and "already" in out.lower(), out.strip())
        # a block naming the wrong target (the toplevel while the project is a sub-project)
        (a / "sub").mkdir()
        gb.manifest(a / "sub")
        (a / ".deepmap" / "graph_build.json").unlink(missing_ok=True)   # the project is sub/, not the top
        rep = P._git_hook_report(str(a / "sub"))
        check("a block syncing the toplevel while the project is sub/: stale, the reason names the sync root",
              rep.get("state") == "stale" and any("sync root" in r for r in rep.get("reasons") or []), str(rep))
    # the setup hint
    with _GitBox() as gb:
        a = gb.repo("a")
        out = _captured(P._print_git_hook_hint, str(a))[1]
        check("setup hint when nothing is installed: the opt-in tip", "git-hook --install" in out, out)
        _quiet(P.git_hook, str(a), "install")
        out = _captured(P._print_git_hook_hint, str(a))[1]
        check("... and none once the current block is installed", out.strip() == "", out)
        gb.git(a, "config", "core.hooksPath", (gb.base / "elsewhere").as_posix())
        out = _captured(P._print_git_hook_hint, str(a))[1]
        check("... and no 'run --install' tip where --install would refuse (hooks outside the repo)",
              "git-hook --install" not in out, out)


def gate_d_repairs_and_edges() -> None:
    print("\n-- D (J1): --uninstall repairs a Python hook round 7 broke; an empty hook file; orphaned sentinels --")
    if not shutil.which("git"):
        return
    with _GitBox() as gb:
        a = gb.repo("a")
        hp = gb.hook(a)
        hp.parent.mkdir(parents=True, exist_ok=True)
        py_hook = "#!/usr/bin/env python3\nprint('mine')\n"
        r7 = _quiet(P._git_hook_block, str(a))
        hp.write_text(py_hook + "\n" + r7, encoding="utf-8", newline="\n")   # what round 7's append did to it
        rep = P._git_hook_report(str(a))
        check("a Python hook carrying an appended DeepMap block: status STALE (git never runs it) + refusal",
              rep.get("state") == "stale" and rep.get("refusal") and any("python3" in r for r in rep.get("reasons")),
              str(rep.get("reasons")))
        out = _captured(P._print_git_hook_hint, str(a))[1]
        check("... setup's hint points at --uninstall (not --install, which refuses)", "git-hook --uninstall" in out, out)
        rc = _quiet(P.git_hook, str(a), "uninstall")
        check("... --uninstall restores the user's Python hook byte-for-byte", rc == 0 and hp.read_text(encoding="utf-8")
              == py_hook, repr(hp.read_text(encoding="utf-8")))
    with _GitBox() as gb:
        a = gb.repo("a")
        hp = gb.hook(a)
        hp.parent.mkdir(parents=True, exist_ok=True)
        hp.write_text("", encoding="utf-8")
        os.chmod(hp, 0o755)
        rc = _quiet(P.git_hook, str(a), "install")
        text = hp.read_text(encoding="utf-8")
        check("an EMPTY existing hook file gets a `#!/bin/sh` line + the block", rc == 0 and text.startswith(
            "#!/bin/sh\n# >>> deepmap"), repr(text))
    with _GitBox() as gb:
        a = gb.repo("a")
        hp = gb.hook(a)
        hp.parent.mkdir(parents=True, exist_ok=True)
        # what round 7's --uninstall left of an old installer's chained block: its BEGIN + END sentinels, no line
        hp.write_text("#!/bin/sh\necho mine\n\n# >>> deepmap (chained; existing hook preserved) >>>\n"
                      "# <<< deepmap <<<\n", encoding="utf-8", newline="\n")
        rc = _quiet(P.git_hook, str(a), "uninstall")
        check("orphaned old sentinels are removed by --uninstall (the user's lines kept)",
              rc == 0 and hp.read_text(encoding="utf-8") == "#!/bin/sh\necho mine\n", repr(hp.read_text(encoding="utf-8")))


def gate_d_main_rcs() -> None:
    print("\n-- D (J1): `provision.py git-hook` exit codes --")
    if not shutil.which("git"):
        return
    with _GitBox() as gb:
        a = gb.repo("a")
        rc = _quiet(P.main, ["git-hook", "--status", str(a)])
        check("--status -> rc 0", rc == 0)
        rc = _quiet(P.main, ["git-hook", "--install", str(a)])
        check("--install -> rc 0 + a hook that parses as sh", rc == 0 and gb.hook(a).exists())
        sh = shutil.which("bash") or shutil.which("sh")
        if sh:
            r = subprocess.run([sh, "-n", str(gb.hook(a))], capture_output=True, text=True, timeout=30)
            check("... `sh -n` accepts the hook", r.returncode == 0, r.stderr.strip())
        hp = gb.hook(a)
        hp.write_text("#!/usr/bin/env python3\nprint(1)\n", encoding="utf-8")
        rc = _quiet(P.main, ["git-hook", "--install", str(a)])
        check("a refusal via main -> rc 2", rc == 2)


# =========================================================================== main

def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 68)
    print("DeepMap — provisioner round-8 gates (hermetic, W2): J4 IPv4 URIs, J5 code-identity restarts,")
    print("          J6 verify query / purge cleanup / longpaths, J1 scoped + safe git hook")
    print("=" * 68)
    for g in (gate_a_generated_uris_dial_ipv4, gate_a_uri_comparisons_by_endpoint,
              gate_b_sidecar_records_code, gate_b_heal_restarts_old_code, gate_b_cmdline_script_parse,
              gate_b_heal_end_to_end,
              gate_c_verify_query_fts_visible, gate_c_instance_rm_purge_cleanup, gate_c_git_longpaths,
              gate_d_sync_root_not_toplevel, gate_d_worktrees_scoped, gate_d_refuses_shared_or_foreign_hooks,
              gate_d_insert_after_shebang, gate_d_status_and_upgrade_of_stale_blocks, gate_d_repairs_and_edges,
              gate_d_main_rcs):
        _run_gate(g)
    print("\n" + "=" * 68)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
