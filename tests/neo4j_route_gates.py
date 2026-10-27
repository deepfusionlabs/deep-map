#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j, no-network gates for THE shared Neo4j routing rule (ROUND-2 decision D1):
port_registry.resolve_neo4j_route(root) -> Neo4jRoute(uri, source, problem) — one answer to "which Neo4j
holds this project's graph" for the CLI, sync, indexes, provision and the hooks.

The 2026-09 review finding it closes (HIGH, isolation-safety): provision refused a project with no
registered bolt pair when another project owned bolt 7687, but port_registry.neo4j_uri_for — the path
the CLI (/deep-map:impact, doctor, search, status) and sync (/deep-map:refresh, the git hook) use —
still returned bolt://localhost:7687. After `instance-rm B --yes`, B's searches answered from project
A's graph, and B's refresh hit A's owner guard whose `--switch-project` advice WIPED A's graph. A lost
registry entry likewise sent B to 7687 although B's own instance (its conf) was intact.

Locked here:
  * the rule, in order: env NEO4J_URI (escape hatch) > an unusable registry fails LOUD > the registered
    pair > the project's OWN instance conf (lost registry entry; not if another project claims those
    ports) > NO route when another registered project claims the base 7687 > the base 7687 as a
    LEGACY route that the caller must verify (source "legacy-unverified");
  * neo4j_uri_for delegates: same signature, raises NoProjectInstanceError (a RegistryUnreadableError,
    so every existing handler catches it) instead of answering from another project's graph; the
    zero-drift 7687 answer for a lone / unregistered project on a machine with no claims is unchanged;
  * the route unpacks as the (uri, source, problem) 3-tuple the plan specified, and carries bolt/http/key;
  * honor_env=False (provision is itself the NEO4J_URI injector) ignores an in-process injection;
  * the conf parser moved into port_registry agrees with provision's own (on provision's real block);
  * legacy_owner_verdict: the pure owner-check policy for a legacy route (this project / unowned+empty
    -> ok; another project / unowned-with-nodes / unknown -> refuse, naming /deep-map:setup — D6: never
    `instance-rm` on the base store). Since ROUND-4 F1 a subdirectory session is "this project" only
    within one git repo; a non-git parent folder is a project of its own (key_migration_gates M11 has
    the full owner-predicate matrix);
  * END-TO-END through the CLI: deepmap_cli._neo4j_uri() refuses (ProjectResolutionError) in the
    instance-rm scenario instead of returning 7687.

Hermetic: throwaway DEEPMAP_HOME + project dirs, no Neo4j, no sockets, no network. Set DMGATE_ROOT to
a directory holding build/ to run these gates against another copy (how the RED evidence was taken).
Exit 0 = green.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(os.environ.get("DMGATE_ROOT") or Path(__file__).resolve().parent.parent)
BUILD = REPO / "build"
sys.path.insert(0, str(BUILD))
import port_registry as PR  # noqa: E402

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


class _Home:
    """A throwaway DEEPMAP_HOME + work dir (plain dirs -> identity = resolved path); no NEO4J_URI; the
    free-port probe stubbed to "nothing listens"; restores everything."""

    def __enter__(self):
        self._td_home = tempfile.TemporaryDirectory()
        self._td_work = tempfile.TemporaryDirectory()
        self.home = Path(self._td_home.name).resolve()
        self.work = Path(self._td_work.name).resolve()
        self._saved_home = os.environ.get("DEEPMAP_HOME")
        self._saved_uri = os.environ.pop("NEO4J_URI", None)
        os.environ["DEEPMAP_HOME"] = str(self.home)
        self._saved_probe = PR._port_in_use
        PR._port_in_use = lambda host, port: False
        return self

    def __exit__(self, *exc):
        PR._port_in_use = self._saved_probe
        if self._saved_home is None:
            os.environ.pop("DEEPMAP_HOME", None)
        else:
            os.environ["DEEPMAP_HOME"] = self._saved_home
        os.environ.pop("NEO4J_URI", None)
        if self._saved_uri is not None:
            os.environ["NEO4J_URI"] = self._saved_uri
        self._td_home.cleanup()
        self._td_work.cleanup()
        return False

    def project(self, name: str) -> Path:
        d = self.work / name
        d.mkdir(parents=True, exist_ok=True)
        return d.resolve()


def _conf(home: Path, key: str, bolt: int, http: int) -> Path:
    p = home / "instances" / key / "conf" / "neo4j.conf"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("# shipped defaults\n#server.bolt.listen_address=:7687\n#server.http.listen_address=:7474\n"
                 "# DEEPMAP-BEGIN\n"
                 f"server.bolt.listen_address=127.0.0.1:{bolt}\n"
                 f"server.http.listen_address=127.0.0.1:{http}\n"
                 "# DEEPMAP-END\n", encoding="utf-8")
    return p


def _route(root, **kw):
    fn = getattr(PR, "resolve_neo4j_route", None)
    if fn is None:
        return None
    return fn(root, **kw)


def _uri_or_exc(root, env=None):
    try:
        return PR.neo4j_uri_for(root, env) if env is not None else PR.neo4j_uri_for(root)
    except Exception as exc:  # noqa: BLE001 — the gate records WHICH exception
        return exc


# --------------------------------------------------------------------------- API

def gate_api() -> None:
    print("\n-- the shared resolver API exists (D1) --")
    for name in ("resolve_neo4j_route", "Neo4jRoute", "NoProjectInstanceError", "instance_conf_ports",
                 "neo4j_claims", "legacy_owner_verdict", "no_instance_problem", "instance_conf_path"):
        check(f"port_registry.{name} exists", hasattr(PR, name))
    nie = getattr(PR, "NoProjectInstanceError", None)
    check("NoProjectInstanceError is a RegistryUnreadableError (existing handlers catch it)",
          isinstance(nie, type) and issubclass(nie, PR.RegistryUnreadableError))
    check("Neo4jRouteError names the same base class", getattr(PR, "Neo4jRouteError", None) is PR.RegistryUnreadableError)


# --------------------------------------------------------------------------- the rule

def gate_env_and_absent() -> None:
    print("\n-- env escape hatch; absent registry -> legacy 7687 (zero drift) --")
    with _Home() as h:
        a = h.project("a")
        r = _route(a)
        check("absent registry -> source legacy-unverified",
              r is not None and r.source == "legacy-unverified", repr(r))
        check("... uri bolt://127.0.0.1:7687, pair (7687, 7474), needs_owner_check",
              r is not None and r.uri == "bolt://127.0.0.1:7687" and r.pair == (7687, 7474)
              and r.needs_owner_check and r.problem is None, repr(r))
        check("neo4j_uri_for unchanged: bolt://127.0.0.1:7687", _uri_or_exc(a) == "bolt://127.0.0.1:7687")
        env = {"NEO4J_URI": "bolt://byo-host:9999", "DEEPMAP_HOME": str(h.home)}
        r = _route(a, env=env)
        check("env NEO4J_URI -> source env, verbatim", r is not None and tuple(r) == ("bolt://byo-host:9999", "env", None),
              repr(r))
        check("neo4j_uri_for(env) verbatim", _uri_or_exc(a, env) == "bolt://byo-host:9999")
        r = _route(a, env=env, honor_env=False) if r is not None else None
        check("honor_env=False ignores an in-process NEO4J_URI injection",
              r is not None and r.source == "legacy-unverified", repr(r))


def gate_registered() -> None:
    print("\n-- registered pair -> source registry (3-tuple unpack + extras) --")
    with _Home() as h:
        a, b = h.project("a"), h.project("b")
        PR.allocate_neo4j_ports(a, base_adopt_ok=lambda: True)
        PR.allocate_neo4j_ports(b, base_adopt_ok=lambda: True)
        r = _route(b)
        ok = r is not None
        if ok:
            uri, source, problem = r
        check("B -> ('bolt://127.0.0.1:7689', 'registry', None) as a 3-tuple",
              ok and (uri, source, problem) == ("bolt://127.0.0.1:7689", "registry", None), repr(r))
        check("... bolt 7689, http 7476, key == project_key(B), not needs_owner_check",
              ok and r.pair == (7689, 7476) and r.key == PR.project_key(b) and not r.needs_owner_check, repr(r))
        check("A (base owner) -> registry 7687", _route(a) is not None and _route(a).source == "registry"
              and _route(a).uri == "bolt://127.0.0.1:7687")
        check("neo4j_uri_for(B) == bolt://127.0.0.1:7689", _uri_or_exc(b) == "bolt://127.0.0.1:7689")


def gate_unusable_registry() -> None:
    print("\n-- unusable registry -> no route, fail loud (never 7687) --")
    with _Home() as h:
        a = h.project("a")
        (h.home / "registry.json").write_text(json.dumps({"version": 99, "projects": {}}), encoding="utf-8")
        r = _route(a)
        check("unrecognized registry -> source none, kind registry, uri None",
              r is not None and r.source == "none" and r.kind == "registry" and r.uri is None and r.problem,
              repr(r))
        exc = _uri_or_exc(a)
        check("neo4j_uri_for raises RegistryUnreadableError",
              isinstance(exc, PR.RegistryUnreadableError), repr(exc))
        (h.home / "registry.json").write_text("{not json", encoding="utf-8")
        r = _route(a)
        check("garbage registry -> no route", r is not None and r.source == "none" and r.uri is None, repr(r))
        env = {"NEO4J_URI": "bolt://byo:1", "DEEPMAP_HOME": str(h.home)}
        r = _route(a, env=env)
        check("... but an explicit NEO4J_URI still wins (escape hatch)",
              r is not None and r.source == "env" and r.uri == "bolt://byo:1", repr(r))


def gate_instance_rm_scenario() -> None:
    print("\n-- review finding: B after `instance-rm B` / never-set-up C must NOT get A's 7687 --")
    with _Home() as h:
        a, b, c = h.project("a"), h.project("b"), h.project("c")
        PR.allocate_ports(a)
        PR.allocate_neo4j_ports(a, base_adopt_ok=lambda: True)
        PR.allocate_ports(b)
        PR.allocate_neo4j_ports(b, base_adopt_ok=lambda: True)
        check("setup: A on 7687, B on 7689", PR.lookup_neo4j_ports(a) == (7687, 7474)
              and PR.lookup_neo4j_ports(b) == (7689, 7476))
        PR.remove_entry(b)   # instance-rm B: drops only B's Neo4j pool (MCP pair kept)
        check("instance-rm dropped B's Neo4j pair, kept its MCP pair",
              PR.lookup_neo4j_ports(b) is None and PR.lookup_ports(b) is not None)
        for name, p in (("B (after instance-rm)", b), ("C (never set up)", c)):
            r = _route(p)
            check(f"{name}: no route (source none, kind no-instance)",
                  r is not None and r.source == "none" and r.kind == "no-instance" and r.uri is None, repr(r))
            check(f"{name}: the problem names A's instance and /deep-map:setup (no instance-rm advice)",
                  r is not None and bool(r.problem) and str(a).replace("\\", "/") in r.problem.replace("\\", "/")
                  and "/deep-map:setup" in r.problem and "instance-rm --yes" not in r.problem,
                  r.problem if r is not None else "")
            exc = _uri_or_exc(p)
            check(f"{name}: neo4j_uri_for RAISES NoProjectInstanceError (never bolt://localhost:7687)",
                  isinstance(exc, getattr(PR, "NoProjectInstanceError", ())) , repr(exc))
            check(f"{name}: ... which an existing `except RegistryUnreadableError` catches",
                  isinstance(exc, PR.RegistryUnreadableError), repr(exc))
        check("A itself still routes to its own 7687", _uri_or_exc(a) == "bolt://127.0.0.1:7687")


def gate_lost_registry_own_conf() -> None:
    print("\n-- a lost registry entry re-finds the project's OWN instance from its conf --")
    with _Home() as h:
        a, b = h.project("a"), h.project("b")
        PR.allocate_neo4j_ports(a, base_adopt_ok=lambda: True)
        PR.allocate_neo4j_ports(b, base_adopt_ok=lambda: True)
        kb = PR.project_key(b)
        _conf(h.home, kb, 7689, 7476)
        (h.home / "registry.json").replace(h.home / "registry.json.aside")   # the RegistryUnreadable advice
        r = _route(b)
        check("B -> source instance-conf, bolt://127.0.0.1:7689 (its own instance)",
              r is not None and r.source == "instance-conf" and r.uri == "bolt://127.0.0.1:7689"
              and r.pair == (7689, 7476) and not r.needs_owner_check, repr(r))
        check("neo4j_uri_for(B) == bolt://127.0.0.1:7689 (was 7687 = A's graph)",
              _uri_or_exc(b) == "bolt://127.0.0.1:7689", repr(_uri_or_exc(b)))
        check("A (no conf: legacy store) -> legacy-unverified 7687",
              _route(a) is not None and _route(a).source == "legacy-unverified")
    with _Home() as h:
        a, b = h.project("a"), h.project("b")
        PR.allocate_neo4j_ports(a, base_adopt_ok=lambda: True)
        PR.allocate_neo4j_ports(b, base_adopt_ok=lambda: True)   # B registered on 7689
        c = h.project("c")
        _conf(h.home, PR.project_key(c), 7689, 7476)           # C's stale conf names B's ports
        r = _route(c)
        check("own-conf ports claimed by ANOTHER project are not adopted (-> no route: A owns 7687)",
              r is not None and r.source == "none" and r.kind == "no-instance", repr(r))
    with _Home() as h:
        d = h.project("d")
        _conf(h.home, PR.project_key(d), 7687, 7474)
        r = _route(d)
        check("an own conf naming the BASE pair is not an instance route (-> legacy)",
              r is not None and r.source == "legacy-unverified", repr(r))
        _conf(h.home, PR.project_key(d), 7711, 7498)
        (h.home / "registry.json").write_text(json.dumps({"version": 1, "projects": {}}), encoding="utf-8")
        r = _route(d)
        check("own conf with an EMPTY (ok) registry -> instance-conf 7711",
              r is not None and r.source == "instance-conf" and r.bolt == 7711, repr(r))


def gate_mcp_only_entry() -> None:
    print("\n-- MCP-only entries claim no Neo4j port; the base is legacy when nobody claims it --")
    with _Home() as h:
        a, b = h.project("a"), h.project("b")
        PR.allocate_ports(a)                     # A: MCP pair only, no Neo4j pool
        r = _route(b)
        check("B with only A's MCP claim -> legacy-unverified 7687",
              r is not None and r.source == "legacy-unverified" and r.uri == "bolt://127.0.0.1:7687", repr(r))
        claims = PR.neo4j_claims(PR.project_key(b)) if hasattr(PR, "neo4j_claims") else None
        check("neo4j_claims ignores MCP-only entries", claims == {}, repr(claims))


def gate_conf_parser_parity() -> None:
    print("\n-- the moved conf parser agrees with provision's (on provision's REAL managed block) --")
    with _Home() as h:
        try:
            import provision as P
        except Exception as exc:  # noqa: BLE001
            check("import provision", False, repr(exc))
            return
        key = "abc123def456"
        conf = h.home / "instances" / key / "conf" / "neo4j.conf"
        conf.parent.mkdir(parents=True, exist_ok=True)
        block = P.conf_instance_block(h.home / "d", h.home / "l", h.home / "r", 7733, 7520)
        conf.write_text("#server.bolt.listen_address=:7687\n" + P.CONF_BLOCK_START + "\n" + block
                        + P.CONF_BLOCK_END + "\n", encoding="utf-8")
        mine = PR.instance_conf_ports(key) if hasattr(PR, "instance_conf_ports") else None
        theirs = P._neo4j_ports_from_instance_conf(key)
        check("instance_conf_ports == provision._neo4j_ports_from_instance_conf == (7733, 7520)",
              mine == theirs == (7733, 7520), f"{mine} / {theirs}")
        check("instance_conf_path == provision.paths(project_key=...)['conf']",
              hasattr(PR, "instance_conf_path") and PR.instance_conf_path(key) == P.paths(None, key)["conf"])
        check("no conf -> None", hasattr(PR, "instance_conf_ports") and PR.instance_conf_ports("nope") is None)


def gate_owner_verdict() -> None:
    print("\n-- legacy_owner_verdict: the pure D1 owner-check policy --")
    fn = getattr(PR, "legacy_owner_verdict", None)
    if fn is None:
        check("legacy_owner_verdict exists", False)
        return
    with _Home() as h:
        a, b = h.project("a"), h.project("b")
        sub = a / "pkg"
        sub.mkdir()
        g = h.project("g")                     # a git repo: its subdirectories ARE the repo (one identity)
        subprocess.run([shutil.which("git") or "git", "init", "-q"], cwd=str(g), capture_output=True, timeout=60)
        (g / "pkg").mkdir()
        cases = [
            ("owner == this project -> ok", fn(a, str(a), True), True),
            ("owner == this project (forward slashes) -> ok", fn(a, str(a).replace("\\", "/"), True), True),
            ("owner is the git repo this subdir session is in -> ok", fn(g / "pkg", str(g), True), True),
            ("owner is a NON-git parent folder: its own project (F1) -> refuse", fn(sub, str(a), True), False),
            ("unowned + EMPTY graph -> ok", fn(a, None, False), True),
            ("another project's graph -> refuse", fn(b, str(a), True), False),
            ("unowned graph WITH nodes -> refuse (can't be shown to be ours)", fn(b, None, True), False),
            ("unowned, node count unknown -> refuse", fn(b, None, None), False),
        ]
        for label, got, want in cases:
            ok, problem = got
            check(label, ok is want and ((problem is None) if want else bool(problem)), repr(got))
        _ok, problem = fn(b, str(a), True, uri="bolt://localhost:7687")
        check("the refusal names the URI, the owner and /deep-map:setup (never instance-rm)",
              "bolt://localhost:7687" in problem and str(a) in problem and "/deep-map:setup" in problem
              and "instance-rm" not in problem.replace("removed by instance-rm", ""), problem)


def gate_never_raises() -> None:
    print("\n-- resolve_neo4j_route never raises --")
    with _Home() as h:
        (h.home / "registry.json").write_text(json.dumps(
            {"version": 1, "projects": {"x": "not-a-dict", "y": {"bolt_port": "junk"}}}), encoding="utf-8")
        try:
            r = _route(h.project("a"), timeout=5)
            ok = r is not None and r.source in PR.ROUTE_SOURCES
        except Exception as exc:  # noqa: BLE001
            ok, r = False, exc
        check("malformed entries + timeout kwarg -> a Route, no exception", ok, repr(r))


def gate_cli_end_to_end() -> None:
    print("\n-- end-to-end: the CLI refuses instead of answering from A's graph --")
    with _Home() as h:
        a, b = h.project("a"), h.project("b")
        PR.allocate_neo4j_ports(a, base_adopt_ok=lambda: True)
        PR.allocate_neo4j_ports(b, base_adopt_ok=lambda: True)
        PR.remove_entry(b)
        try:
            import deepmap_cli as CLI
        except Exception as exc:  # noqa: BLE001
            check("import deepmap_cli", False, repr(exc))
            return
        saved = CLI._project_root
        CLI._project_root = lambda: b
        try:
            try:
                got = CLI._neo4j_uri()
            except Exception as exc:  # noqa: BLE001
                got = exc
        finally:
            CLI._project_root = saved
        check("deepmap_cli._neo4j_uri() for B raises ProjectResolutionError (not bolt://localhost:7687)",
              isinstance(got, CLI.ProjectResolutionError), repr(got))
        check("... and its message tells the user to run /deep-map:setup",
              isinstance(got, Exception) and "/deep-map:setup" in str(got), str(got))


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 68)
    print("DeepMap — the shared Neo4j routing rule (D1: resolve_neo4j_route)")
    print(f"(build under test: {BUILD})")
    print("=" * 68)
    gate_api()
    gate_env_and_absent()
    gate_registered()
    gate_unusable_registry()
    gate_instance_rm_scenario()
    gate_lost_registry_own_conf()
    gate_mcp_only_entry()
    gate_conf_parser_parity()
    gate_owner_verdict()
    gate_never_raises()
    gate_cli_end_to_end()
    print("\n" + "=" * 68)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
