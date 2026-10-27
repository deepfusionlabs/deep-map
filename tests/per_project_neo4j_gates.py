#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j, no-network gates for TRUE per-project Neo4j ISOLATION (one binary, N per-project
instances via NEO4J_CONF dirs + a per-project bolt/http port pair).

Proves the load-bearing contract of the isolation feature WITHOUT a live Neo4j:
  * a per-project (bolt, http) pool alongside the MCP pool — first project keeps 7687/7474 (zero
    drift), a second gets 7689/7476, the two pools MERGE into one registry entry (neither clobbers);
  * OWNER-AWARE base adoption (the field-failure fix): a 2nd project cannot seize the base 7687
    instance owned by another — proven in BOTH provisioning orders;
  * neo4j_uri_for precedence (env override wins; registry bolt; unallocated -> 7687);
  * resolved_instance -> None for the legacy/base/escape-hatch layout, (key,bolt,http) otherwise;
  * paths(project_key=None) is BYTE-IDENTICAL to before (no silent rebuild for existing users);
  * the conf helpers (strip managed dup keys, content-addressed managed block);
  * _base_adopt_ok verdict scenarios;
  * apply_ports_for's Neo4j half injects NEO4J_URI (and the escape hatch skips it);
  * the per-instance write lock is per-URI (per-project).

Hermetic: DEEPMAP_HOME -> a throwaway dir, the free-port probe + is_bolt_up stubbed (NO sockets, NO
Neo4j, NO network). Exit 0 = green.
"""
from __future__ import annotations

import io
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
    return lambda host, port: port in occupied


class _EnvHome:
    """Context manager: redirect DEEPMAP_HOME to a fresh tmp dir; stub PR._port_in_use to `occupied`."""
    def __init__(self, occupied=None):
        self.occupied = occupied or set()

    def __enter__(self):
        self._home = tempfile.mkdtemp()
        self._work = tempfile.mkdtemp()
        self._saved_home = os.environ.get("DEEPMAP_HOME")
        os.environ["DEEPMAP_HOME"] = self._home
        self._saved_probe = PR._port_in_use
        PR._port_in_use = _fake_probe(self.occupied)
        return Path(self._home), Path(self._work)

    def __exit__(self, *a):
        if self._saved_home is None:
            os.environ.pop("DEEPMAP_HOME", None)
        else:
            os.environ["DEEPMAP_HOME"] = self._saved_home
        PR._port_in_use = self._saved_probe
        shutil.rmtree(self._home, ignore_errors=True)
        shutil.rmtree(self._work, ignore_errors=True)


# --------------------------------------------------------------------------- gates

def gate_first_project_zero_drift() -> None:
    print("\n-- first project keeps the base (7687, 7474) — zero drift --")
    with _EnvHome() as (home, work):
        a = _mk_project(work, "a")
        pair = PR.allocate_neo4j_ports(a, base_adopt_ok=lambda: True)
        check("first project bolt/http == (7687, 7474)", pair == (7687, 7474), str(pair))
        check("lone project neo4j_uri == bolt://127.0.0.1:7687 (the base bolt, ROUND-8 J4 spelling)",
              PR.neo4j_uri_for(a) == "bolt://127.0.0.1:7687", PR.neo4j_uri_for(a))
        check("lookup_neo4j_ports returns the stored pair", PR.lookup_neo4j_ports(a) == (7687, 7474))


def gate_two_projects_distinct() -> None:
    print("\n-- two projects -> distinct (bolt, http) instances --")
    with _EnvHome() as (home, work):
        a = _mk_project(work, "a")
        b = _mk_project(work, "b")
        pa = PR.allocate_neo4j_ports(a, base_adopt_ok=lambda: True)
        pb = PR.allocate_neo4j_ports(b, base_adopt_ok=lambda: True)
        check("A gets base (7687, 7474)", pa == (7687, 7474), str(pa))
        check("B gets the next pair (7689, 7476)", pb == (7689, 7476), str(pb))
        check("A and B bolt/http disjoint", set(pa).isdisjoint(pb), f"A={pa} B={pb}")
        check("A neo4j_uri 7687, B neo4j_uri 7689",
              PR.neo4j_uri_for(a) == "bolt://127.0.0.1:7687" and PR.neo4j_uri_for(b) == "bolt://127.0.0.1:7689",
              f"{PR.neo4j_uri_for(a)} / {PR.neo4j_uri_for(b)}")


def gate_pools_coexist_merge() -> None:
    print("\n-- MCP + Neo4j pools MERGE into one entry (either order, neither clobbers) --")
    for order in ("mcp-first", "neo4j-first"):
        with _EnvHome() as (home, work):
            a = _mk_project(work, "a")
            if order == "mcp-first":
                PR.allocate_ports(a)
                PR.allocate_neo4j_ports(a, base_adopt_ok=lambda: True)
            else:
                PR.allocate_neo4j_ports(a, base_adopt_ok=lambda: True)
                PR.allocate_ports(a)
            mcp = PR.lookup_ports(a)
            bolt = PR.lookup_neo4j_ports(a)
            check(f"[{order}] MCP pair survives == (8787, 8788)", mcp == (8787, 8788), str(mcp))
            check(f"[{order}] Neo4j pair survives == (7687, 7474)", bolt == (7687, 7474), str(bolt))


def gate_idempotent_neo4j() -> None:
    print("\n-- Neo4j allocation is idempotent (re-allocate returns the same pair, no re-probe) --")
    with _EnvHome() as (home, work):
        a = _mk_project(work, "a")
        first = PR.allocate_neo4j_ports(a, base_adopt_ok=lambda: True)
        # Now pretend base is occupied AND base_adopt would say no — a re-allocate MUST still return
        # the stored pair (idempotent short-circuit runs before any probe/verdict).
        PR._port_in_use = _fake_probe({7687})
        again = PR.allocate_neo4j_ports(a, base_adopt_ok=lambda: False)
        check("re-allocate returns the SAME pair (no migration)", again == first, f"{first} vs {again}")


def gate_owner_aware_base_adoption() -> None:
    print("\n-- OWNER-AWARE base adoption: a 2nd project can't seize a live foreign base (field fix) --")
    # base_adopt_ok=False means "base is live & owned by another (or deferred)": skip base, next pair.
    with _EnvHome() as (home, work):
        a = _mk_project(work, "proj-alpha")
        pair = PR.allocate_neo4j_ports(a, base_adopt_ok=lambda: False)
        check("base refused (owner-aware) -> allocates (7689, 7476), NOT (7687, 7474)",
              pair == (7689, 7476), str(pair))
    # base_adopt_ok=True means "unowned/empty/same/clean": adopt base.
    with _EnvHome() as (home, work):
        a = _mk_project(work, "proj-beta")
        pair = PR.allocate_neo4j_ports(a, base_adopt_ok=lambda: True)
        check("base allowed -> adopts (7687, 7474)", pair == (7687, 7474), str(pair))


def gate_field_case_both_orders() -> None:
    print("\n-- FIELD CASE: two legacy projects, refused-project FIRST, then the owner --")
    # proj-alpha runs first while proj-beta owns/holds base (base_adopt_ok False for proj-alpha).
    with _EnvHome() as (home, work):
        alpha = _mk_project(work, "proj-alpha")
        beta = _mk_project(work, "proj-beta")
        # proj-alpha first: base owned by proj-beta (still live) -> refuse base -> fresh instance.
        pm = PR.allocate_neo4j_ports(alpha, base_adopt_ok=lambda: False)
        # proj-beta next: it is base's true owner -> adopt base.
        pe = PR.allocate_neo4j_ports(beta, base_adopt_ok=lambda: True)
        check("proj-alpha (refused first) got a fresh instance (7689, 7476)", pm == (7689, 7476), str(pm))
        check("proj-beta (owner) adopted base (7687, 7474) — graph intact, no re-embed", pe == (7687, 7474), str(pe))
        check("the two projects are on DISTINCT instances", set(pm).isdisjoint(pe), f"{pm} {pe}")


def gate_first_free_scan_probe() -> None:
    print("\n-- for i>0 the in-use probe stays load-bearing (a foreign listener bumps) --")
    with _EnvHome(occupied={7689}) as (home, work):
        a = _mk_project(work, "a")
        b = _mk_project(work, "b")
        PR.allocate_neo4j_ports(a, base_adopt_ok=lambda: True)     # A -> base 7687
        pb = PR.allocate_neo4j_ports(b, base_adopt_ok=lambda: True)  # 7689 occupied -> bump to 7691
        check("B skips the occupied 7689 -> (7691, 7478)", pb == (7691, 7478), str(pb))


def gate_never_raises_degrade() -> None:
    print("\n-- never-raises: garbage registry / exhaustion degrade to base --")
    with _EnvHome() as (home, work):
        (home).mkdir(parents=True, exist_ok=True)
        PR.registry_path().write_text("}{ not json", encoding="utf-8")
        a = _mk_project(work, "a")
        try:
            pair = PR.allocate_neo4j_ports(a, base_adopt_ok=lambda: True)
            ok = True
        except Exception as exc:  # noqa: BLE001
            ok, pair = False, None
            print(f"    (raised: {exc})")
        check("garbage registry -> base pair, no raise", ok and pair == (7687, 7474), str(pair))
        check("garbage registry NOT overwritten",
              PR.registry_path().read_text(encoding="utf-8") == "}{ not json")


def gate_registry_loss_recovery() -> None:
    print("\n-- registry-loss recovery: prefer= re-adopts an instance's own ports (no drift) --")
    with _EnvHome(occupied={7689, 7691}) as (home, work):
        a = _mk_project(work, "a")
        # base + several higher ports are 'in use' (our own instances), but the registry lost A's entry.
        # prefer=(7689,7476) re-adopts A's real ports WITHOUT a probe (would otherwise bump past them).
        pair = PR.allocate_neo4j_ports(a, prefer=(7689, 7476), base_adopt_ok=lambda: False)
        check("prefer= re-adopts the instance's own (7689, 7476)", pair == (7689, 7476), str(pair))


def gate_neo4j_uri_precedence() -> None:
    print("\n-- neo4j_uri_for precedence: env override > registry bolt > 7687 --")
    with _EnvHome() as (home, work):
        a = _mk_project(work, "a")
        # (a) unallocated -> default 7687
        check("unallocated -> bolt://127.0.0.1:7687", PR.neo4j_uri_for(a) == "bolt://127.0.0.1:7687")
        # (b) registry bolt reflected
        PR.allocate_ports(a)  # claim base MCP so B differs
        b = _mk_project(work, "b")
        PR.allocate_neo4j_ports(a, base_adopt_ok=lambda: True)
        PR.allocate_neo4j_ports(b, base_adopt_ok=lambda: True)
        check("B registry bolt reflected -> bolt://127.0.0.1:7689",
              PR.neo4j_uri_for(b) == "bolt://127.0.0.1:7689", PR.neo4j_uri_for(b))
        # (c) env override wins verbatim, registry ignored, never raises on garbage registry
        env = {"NEO4J_URI": "bolt://byo-host:9999", "DEEPMAP_HOME": os.environ["DEEPMAP_HOME"]}
        check("env NEO4J_URI wins verbatim", PR.neo4j_uri_for(b, env=env) == "bolt://byo-host:9999")


def gate_resolved_instance() -> None:
    print("\n-- resolved_instance: None for legacy/base/escape, (key,bolt,http) otherwise --")
    saved = P._USER_SET_NEO4J_URI
    P._USER_SET_NEO4J_URI = False
    try:
        with _EnvHome() as (home, work):
            a = _mk_project(work, "a")
            b = _mk_project(work, "b")
            # unallocated -> None
            check("unallocated -> None (legacy layout)", P.resolved_instance(a) is None)
            PR.allocate_neo4j_ports(a, base_adopt_ok=lambda: True)   # A -> base
            PR.allocate_neo4j_ports(b, base_adopt_ok=lambda: True)   # B -> 7689
            check("base project -> None (legacy layout, paths(project_key=None))",
                  P.resolved_instance(a) is None, str(P.resolved_instance(a)))
            ri = P.resolved_instance(b)
            check("non-base project -> (key, 7689, 7476)",
                  ri is not None and ri[1] == 7689 and ri[2] == 7476 and ri[0] == PR.project_key(b), str(ri))
            # escape hatch -> None regardless
            P._USER_SET_NEO4J_URI = True
            check("escape hatch -> None (operator endpoint)", P.resolved_instance(b) is None)
    finally:
        P._USER_SET_NEO4J_URI = saved


def gate_paths_byte_identity() -> None:
    print("\n-- paths(project_key=None) is BYTE-IDENTICAL (no silent rebuild for existing users) --")
    with _EnvHome() as (home, work):
        legacy = P.paths("5.26.0")
        # legacy keys unchanged: conf/data/databases/pidfile/log under the shared nhome/home
        nhome = home / "neo4j" / "neo4j-community-5.26.0"
        check("legacy conf == nhome/conf/neo4j.conf", legacy["conf"] == nhome / "conf" / "neo4j.conf",
              str(legacy["conf"]))
        check("legacy databases == nhome/data/databases",
              legacy["databases"] == nhome / "data" / "databases", str(legacy["databases"]))
        check("legacy pidfile == nhome/run/neo4j.pid", legacy["pidfile"] == nhome / "run" / "neo4j.pid")
        check("legacy log == home/neo4j-console.log", legacy["log"] == home / "neo4j-console.log")
        # per-instance keys under instances/<key>/
        inst = P.paths("5.26.0", "abc123def456")
        base = home / "instances" / "abc123def456"
        check("instance data under instances/<key>/data", inst["data"] == base / "data", str(inst["data"]))
        check("instance conf_dir under instances/<key>/conf", inst["conf_dir"] == base / "conf")
        check("instance shares the binary (neo4j_home unchanged)", inst["neo4j_home"] == nhome)


def gate_conf_helpers() -> None:
    print("\n-- conf helpers: strip managed dup keys, strip/emit the managed block --")
    shipped = ("# a comment\nserver.https.enabled=false\nserver.default_advertised_address=localhost\n"
               "#dbms.usage_report.enabled=true\nserver.memory.heap.max_size=1G\n")
    filtered = P._filter_managed_keys(shipped)
    check("uncommented managed key server.https.enabled dropped",
          "server.https.enabled=false" not in filtered, filtered)
    check("uncommented managed key server.memory.heap.max_size dropped",
          "server.memory.heap.max_size=1G" not in filtered, filtered)
    check("non-managed key preserved", "server.default_advertised_address=localhost" in filtered)
    check("commented line preserved verbatim", "#dbms.usage_report.enabled=true" in filtered)
    blk = P.conf_instance_block("/d", "/l", "/r", 7689, 7476)
    check("instance block sets the allocated bolt", "server.bolt.listen_address=127.0.0.1:7689" in blk, blk)
    check("instance block sets the allocated http", "server.http.listen_address=127.0.0.1:7476" in blk)
    check("instance block bounds heap (default 512m)", "server.memory.heap.max_size=512m" in blk)
    check("instance block uses forward slashes for dirs", "server.directories.data=/d" in blk)
    wrapped = f"{P.CONF_BLOCK_START}\n{blk}{P.CONF_BLOCK_END}\n"
    stripped = P._strip_managed_block("head\n\n" + wrapped + "\ntail\n")
    check("strip removes the fenced managed region", P.CONF_BLOCK_START not in stripped
          and "head" in stripped and "tail" in stripped, stripped)


def gate_configure_instance_content_addressed() -> None:
    print("\n-- configure_neo4j(per-instance): seeds + content-addressed rewrite (ports change picked up) --")
    with _EnvHome() as (home, work):
        key = "deadbeefcafe"
        # Seed a fake shipped conf dir so _seed_instance_conf has something to copy.
        shipped = P.paths("5.26.0")["conf_dir"]
        shipped.mkdir(parents=True, exist_ok=True)
        (shipped / "neo4j.conf").write_text("server.https.enabled=false\n# shipped default\n", encoding="utf-8")
        (shipped / "server-logs.xml").write_text("<config/>", encoding="utf-8")
        changed1 = P.configure_neo4j("5.26.0", project_key=key, bolt=7689, http=7476)
        conf = P.paths("5.26.0", key)["conf"]
        txt = conf.read_text(encoding="utf-8")
        check("instance conf written (returns True first time)", changed1 is True)
        check("seeded server-logs.xml copied (NEO4J_CONF must be complete)",
              (P.paths("5.26.0", key)["conf_dir"] / "server-logs.xml").exists())
        check("managed block present with the bolt port", "server.bolt.listen_address=127.0.0.1:7689" in txt)
        check("no DUPLICATE https key (shipped one stripped)", txt.count("server.https.enabled") == 1, txt)
        # Re-run with the SAME ports -> content-addressed no-op.
        changed2 = P.configure_neo4j("5.26.0", project_key=key, bolt=7689, http=7476)
        check("same ports -> no-op (returns False)", changed2 is False)
        # Re-run with a CHANGED port -> rewritten (no marker-once strand).
        changed3 = P.configure_neo4j("5.26.0", project_key=key, bolt=7691, http=7478)
        txt3 = conf.read_text(encoding="utf-8")
        check("changed port -> rewritten (returns True)", changed3 is True)
        check("new bolt port reflected, old gone",
              "127.0.0.1:7691" in txt3 and "127.0.0.1:7689" not in txt3, txt3)


# A realistic shipped Neo4j 5.26 conf fragment — the UNCOMMENTED keys mirror the real dist on this box
# (server.https.enabled=false, auth-min-length, usage_report — all keys DeepMap manages — plus
# server.bolt.enabled / server.http.enabled / server.directories.import which it does NOT). This is the
# U2 strict-validation trap: a per-instance conf must never leave a DUPLICATE active key.
_REALISTIC_SHIPPED_CONF = """\
# Neo4j configuration (shipped defaults, trimmed)
server.directories.import=import
#server.default_listen_address=0.0.0.0
server.bolt.enabled=true
#server.bolt.listen_address=:7687
server.http.enabled=true
server.https.enabled=false
#server.memory.heap.max_size=512m
# ===== DeepMap provisioner settings (auto-added) =====
dbms.security.auth_minimum_password_length=4
dbms.usage_report.enabled=false
client.allow_telemetry=false
"""


def _active_keys(text: str):
    """Every UNCOMMENTED `key=...` line's key (order preserved) — what Neo4j's strict validator sees."""
    keys = []
    for line in text.splitlines():
        s = line.lstrip()
        if s and not s.startswith("#") and "=" in s:
            keys.append(s.split("=", 1)[0].strip())
    return keys


def gate_conf_no_duplicate_active_keys() -> None:
    print("\n-- per-instance conf: NO duplicate ACTIVE key (U2 strict-validation would refuse startup) --")
    with _EnvHome() as (home, work):
        key = "beadfeed0001"
        shipped = P.paths("5.26.0")["conf_dir"]
        shipped.mkdir(parents=True, exist_ok=True)
        (shipped / "neo4j.conf").write_text(_REALISTIC_SHIPPED_CONF, encoding="utf-8")
        (shipped / "server-logs.xml").write_text("<config/>", encoding="utf-8")
        (shipped / "user-logs.xml").write_text("<config/>", encoding="utf-8")
        P.configure_neo4j("5.26.0", project_key=key, bolt=7689, http=7476)
        confp = P.paths("5.26.0", key)["conf"]
        txt = confp.read_text(encoding="utf-8")
        keys = _active_keys(txt)
        dupes = sorted({k for k in keys if keys.count(k) > 1})
        check("NO duplicate active key in the generated instance conf", not dupes, f"dupes={dupes}")
        # every managed key appears EXACTLY once (present + not duplicated)
        for mk in P._MANAGED_CONF_KEYS:
            check(f"managed key '{mk}' active exactly once", keys.count(mk) == 1, f"count={keys.count(mk)}")
        # NON-managed shipped active keys survive (we only strip what we own)
        check("non-managed 'server.bolt.enabled' survives", "server.bolt.enabled" in keys)
        check("non-managed 'server.directories.import' survives", "server.directories.import" in keys)
        # the listen addresses carry the ALLOCATED ports
        check("bolt listen address uses the allocated 7689", "server.bolt.listen_address=127.0.0.1:7689" in txt)
        check("http listen address uses the allocated 7476", "server.http.listen_address=127.0.0.1:7476" in txt)
        # U1: NEO4J_CONF must be a COMPLETE dir -> the log4j xmls were copied in
        cdir = P.paths("5.26.0", key)["conf_dir"]
        check("U1: server-logs.xml copied into the instance conf dir", (cdir / "server-logs.xml").exists())
        check("U1: user-logs.xml copied into the instance conf dir", (cdir / "user-logs.xml").exists())


def gate_allocation_concurrency() -> None:
    print("\n-- concurrency: N processes allocating at once -> distinct pairs, consistent registry --")
    import json as _json
    with _EnvHome() as (home, work):
        # Spawn K real subprocesses that each allocate the Neo4j pool for a DISTINCT project into the
        # SAME tmp registry, racing on a shared start barrier (a sentinel file). The dedicated OS lock +
        # re-read-under-lock must serialise them so no two projects get the same (bolt, http) pair.
        K = 6
        prog = (
            "import sys, os, time\n"
            "sys.path.insert(0, r'%s')\n" % str(BUILD).replace("\\", "\\\\") +
            "import port_registry as PR\n"
            "PR._port_in_use = lambda h, p: False\n"          # no real sockets in the children
            "root = sys.argv[1]; barrier = sys.argv[2]\n"
            "while not os.path.exists(barrier): time.sleep(0.01)\n"   # all start together
            "pair = PR.allocate_neo4j_ports(root, base_adopt_ok=lambda: True)\n"
            "print('%s' % (pair,))\n"
        )
        script = Path(home) / "racer.py"
        script.write_text(prog, encoding="utf-8")
        projs = [str(_mk_project(Path(work), f"p{i}")) for i in range(K)]
        barrier = Path(home) / "GO"
        env = dict(os.environ)
        procs = [subprocess.Popen([sys.executable, str(script), pr, str(barrier)],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, text=True)
                 for pr in projs]
        barrier.write_text("go", encoding="utf-8")   # release the barrier -> they race
        pairs = []
        for pc in procs:
            out, err = pc.communicate(timeout=60)
            pairs.append((out or "").strip())
        distinct = set(pairs)
        check(f"all {K} racers succeeded", all(pairs), str(pairs))
        check(f"all {K} allocated pairs are DISTINCT (no TOCTOU collision)", len(distinct) == K,
              f"{len(distinct)} distinct of {K}: {sorted(distinct)}")
        # the registry is internally consistent: K project entries, each with a bolt/http pool
        reg = _json.loads(PR.registry_path().read_text(encoding="utf-8"))
        n_with_bolt = sum(1 for e in reg["projects"].values() if isinstance(e, dict) and "bolt_port" in e)
        check(f"registry records {K} projects each with a bolt pool", n_with_bolt == K, str(n_with_bolt))


def gate_base_adopt_ok_verdict() -> None:
    print("\n-- _base_adopt_ok: base down + no legacy store -> adopt (clean machine) --")
    saved_bolt = P.is_bolt_up
    with _EnvHome() as (home, work):
        a = _mk_project(work, "a")
        P.is_bolt_up = lambda *a_, **k_: False   # base is DOWN
        try:
            # no legacy default store on disk (fresh DEEPMAP_HOME) -> clean machine -> adopt
            check("base down + no legacy store -> True (clean machine)", P._base_adopt_ok(a) is True)
        finally:
            P.is_bolt_up = saved_bolt
    # base down + a legacy store exists + >1 project -> defer (False)
    with _EnvHome() as (home, work):
        a = _mk_project(work, "a")
        b = _mk_project(work, "b")
        PR.allocate_ports(a)   # 2 registered projects
        PR.allocate_ports(b)
        db = P.paths("5.26.0")["databases"]
        db.mkdir(parents=True, exist_ok=True)
        (db / "neo4j").mkdir(exist_ok=True)   # a non-empty legacy store
        P.is_bolt_up = lambda *a_, **k_: False
        try:
            with redirect_stderr(io.StringIO()):
                verdict = P._base_adopt_ok(a)
            check("base down + legacy store + 2 projects -> defer (False)", verdict is False, str(verdict))
        finally:
            P.is_bolt_up = saved_bolt


def gate_base_adopt_ok_live_branch() -> None:
    print("\n-- _base_adopt_ok LIVE branch: owner-aware decision (the field-fix core) --")
    import sync as S
    saved = {"bolt": P.is_bolt_up, "drv": P._make_driver,
             "read": S._read_owning_root, "nodes": S._graph_has_nodes}

    class _FakeDrv:
        def close(self):
            pass

    P.is_bolt_up = lambda *a_, **k_: True        # base is LIVE
    P._make_driver = lambda *a_, **k_: _FakeDrv()  # no real connect
    try:
        with _EnvHome() as (home, work):
            target = _mk_project(work, "proj-alpha")
            # (a) unowned + EMPTY base -> adopt (someone hand-started a blank Neo4j; zero-drift)
            S._read_owning_root = lambda drv: None
            S._graph_has_nodes = lambda drv: False
            check("live base, unowned + EMPTY -> True (adopt)", P._base_adopt_ok(target) is True)
            # (b) unowned + POPULATED base -> DEFER (never merge into an unknown legacy graph)
            S._graph_has_nodes = lambda drv: True
            check("live base, unowned + POPULATED -> False (defer; no legacy-merge)",
                  P._base_adopt_ok(target) is False)
            # (c) FOREIGN owner -> DEFER (proj-alpha must NOT seize proj-beta's live base — the field failure)
            S._read_owning_root = lambda drv: str((work / "proj-beta").resolve())
            S._graph_has_nodes = lambda drv: True
            check("live base, FOREIGN owner -> False (the reported field failure, now blocked)",
                  P._base_adopt_ok(target) is False)
            # (d) SAME-project owner -> adopt (re-run of the owner)
            S._read_owning_root = lambda drv: S._project_identity(target)
            check("live base, SAME owner -> True (owner re-run)", P._base_adopt_ok(target) is True)
            # (e) owner READ FAILS on a live base -> DEFER (a base we couldn't inspect is never adopted)
            def _boom(drv):
                raise RuntimeError("transient DatabaseError")
            S._read_owning_root = _boom
            check("live base, owner read FAILS -> False (defer; never adopt uninspected)",
                  P._base_adopt_ok(target) is False)
    finally:
        P.is_bolt_up = saved["bolt"]
        P._make_driver = saved["drv"]
        S._read_owning_root = saved["read"]
        S._graph_has_nodes = saved["nodes"]


def gate_instance_conf_recovery() -> None:
    print("\n-- _neo4j_ports_from_instance_conf: recover an instance's ports from its own conf --")
    with _EnvHome() as (home, work):
        key = "facefeed1234"
        conf = P.paths("5.26.0", key)["conf"]
        conf.parent.mkdir(parents=True, exist_ok=True)
        conf.write_text("server.bolt.listen_address=127.0.0.1:7691\n"
                        "server.http.listen_address=127.0.0.1:7478\n", encoding="utf-8")
        got = P._neo4j_ports_from_instance_conf(key)
        check("recovers (bolt, http) from the instance conf", got == (7691, 7478), str(got))
        check("missing instance conf -> None", P._neo4j_ports_from_instance_conf("nope00000000") is None)


def gate_apply_neo4j_env_injection() -> None:
    print("\n-- apply_ports_for Neo4j half injects NEO4J_URI; escape hatch skips --")
    saved_hatch = P._USER_SET_NEO4J_URI
    saved_bolt = P.is_bolt_up
    saved_uri = os.environ.get("NEO4J_URI")
    P.is_bolt_up = lambda *a_, **k_: False   # base down -> clean-machine adopt (no real connect)
    try:
        # (1) not escape hatch: allocate=True injects NEO4J_URI + allocates bolt
        P._USER_SET_NEO4J_URI = False
        os.environ.pop("NEO4J_URI", None)
        with _EnvHome() as (home, work):
            a = _mk_project(work, "a")
            with redirect_stderr(io.StringIO()):
                P.apply_ports_for(str(a), allocate=True)
            # ROUND-8 J4: provision injects the IPv4 loopback form (port_registry.bolt_uri) — the address the instance binds
            check("NEO4J_URI injected for the project", os.environ.get("NEO4J_URI") == "bolt://127.0.0.1:7687",
                  str(os.environ.get("NEO4J_URI")))
            check("bolt pool recorded in the registry", PR.lookup_neo4j_ports(a) == (7687, 7474))
        # (2) escape hatch: env untouched, no bolt allocation
        P._USER_SET_NEO4J_URI = True
        os.environ["NEO4J_URI"] = "bolt://operator:7000"
        with _EnvHome() as (home, work):
            a = _mk_project(work, "a")
            with redirect_stderr(io.StringIO()):
                P.apply_ports_for(str(a), allocate=True)
            check("escape hatch: NEO4J_URI left as the operator's", os.environ.get("NEO4J_URI") == "bolt://operator:7000")
            check("escape hatch: NO bolt allocation", PR.lookup_neo4j_ports(a) is None)
    finally:
        P._USER_SET_NEO4J_URI = saved_hatch
        P.is_bolt_up = saved_bolt
        if saved_uri is None:
            os.environ.pop("NEO4J_URI", None)
        else:
            os.environ["NEO4J_URI"] = saved_uri


def gate_instance_lock_per_uri() -> None:
    print("\n-- sync instance-lock is per-URI (per-project instances -> per-project lock) --")
    import sync as S
    la = S._instance_lock_path("bolt://localhost:7687")
    lb = S._instance_lock_path("bolt://localhost:7689")
    check("distinct URIs -> distinct instance locks", la != lb, f"{la} vs {lb}")
    check("same URI -> same lock (stable)", S._instance_lock_path("bolt://localhost:7689") == lb)


# --------------------------------------------------------- v0.27 instance-lifecycle helpers + gates

def _reg_json():
    import json as _json
    return _json.loads(PR.registry_path().read_text(encoding="utf-8"))


def _write_reg_json(reg):
    import json as _json
    PR.registry_path().write_text(_json.dumps(reg), encoding="utf-8")


def _set_last_used(key, minutes_ago):
    from datetime import datetime, timezone, timedelta
    reg = _reg_json()
    iso = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    reg["projects"][key]["last_used"] = iso
    _write_reg_json(reg)


def _write_pidfile(key, pid=424242):
    pf = P.paths(None, key)["pidfile"]
    pf.parent.mkdir(parents=True, exist_ok=True)
    pf.write_text(str(pid), encoding="utf-8")
    return pf


def _run_gate(fn) -> None:
    """Run a (v0.27) lifecycle gate, reporting an unexpected raise as ONE failed check rather than
    aborting the whole suite — so a not-yet-implemented function shows a clean per-gate RED (RED-first)."""
    try:
        fn()
    except Exception as e:   # noqa: BLE001
        check(f"{fn.__name__} ran without raising", False, f"{type(e).__name__}: {e}")


def gate_touch_last_used() -> None:
    print("\n-- touch_last_used: stamp an existing entry, never create, throttle, preserve pools --")
    with _EnvHome() as (home, work):
        a = _mk_project(work, "a")
        PR.touch_last_used(a)   # absent registry -> no-op, creates nothing
        check("touch on an ABSENT registry creates no file", not PR.registry_path().exists())
        PR.allocate_ports(a)
        PR.allocate_neo4j_ports(a, base_adopt_ok=lambda: True)
        key = PR.project_key(a)
        PR.touch_last_used(a)
        reg = _reg_json()
        check("touch writes a last_used ISO into the entry",
              bool(reg["projects"][key].get("last_used")), str(reg["projects"][key].get("last_used")))
        check("touch preserves the MCP pool", reg["projects"][key].get("search_port") == 8787)
        check("touch preserves the bolt pool", reg["projects"][key].get("bolt_port") == 7687)
        n_before = len(reg["projects"])
        b = _mk_project(work, "b_unregistered")
        PR.touch_last_used(b)
        check("touch on an unregistered project adds no entry", len(_reg_json()["projects"]) == n_before)
        # throttle: set ~now, re-touch -> unchanged (within the 60s window)
        _set_last_used(key, 0)
        first = _reg_json()["projects"][key]["last_used"]
        PR.touch_last_used(a)
        check("throttle: a re-touch within the window does not rewrite last_used",
              _reg_json()["projects"][key]["last_used"] == first)
        # by-bolt touch (the MCP-server path)
        _set_last_used(key, 120)   # old enough to bypass the throttle
        PR.touch_last_used_by_bolt(7687)
        age = PR._age_seconds(_reg_json()["projects"][key]["last_used"])
        check("touch_last_used_by_bolt bumps the bolt-matched entry (fresh)",
              age is not None and age < 60, str(age))
        PR.touch_last_used_by_bolt(65000)   # unknown port
        check("touch_last_used_by_bolt on an unknown port is a no-op (no raise)", True)


def gate_hibernate_sweep() -> None:
    print("\n-- hibernate_idle: stop running+idle non-current instances only --")
    saved_pid = P._pid_alive
    saved_stop = P.stop_neo4j
    calls = []
    with _EnvHome() as (home, work):
        a = _mk_project(work, "a")   # current, base 7687
        b = _mk_project(work, "b")   # other, 7689
        PR.allocate_neo4j_ports(a, base_adopt_ok=lambda: True)
        PR.allocate_neo4j_ports(b, base_adopt_ok=lambda: True)
        keyA, keyB = PR.project_key(a), PR.project_key(b)
        P._pid_alive = lambda pid: True
        P.stop_neo4j = lambda version=None, project_key=None: (calls.append(project_key) or True)
        try:
            check("_instance_running_for False when no pidfile", P._instance_running_for(keyB) is False)
            _write_pidfile(keyB)
            check("_instance_running_for True for a live pidfile", P._instance_running_for(keyB) is True)
            _write_pidfile(None)                # base/legacy pidfile for A
            _set_last_used(keyB, 120); _set_last_used(keyA, 120)
            del calls[:]
            n = P.hibernate_idle(target=str(a), scope="others")
            check("stops the idle+running OTHER project B", keyB in calls, str(calls))
            check("does NOT stop the CURRENT project A (scope=others)", None not in calls, str(calls))
            check("returns the count stopped (1)", n == 1, str(n))
            _set_last_used(keyB, 0)             # recently used
            del calls[:]
            n = P.hibernate_idle(target=str(a), scope="others")
            check("SKIPS a recently-used B", keyB not in calls and n == 0, str(calls))
            del calls[:]
            n = P.hibernate_idle(target=str(a), scope="all")
            check("scope=all stops the current A (base pidfile -> project_key None)", None in calls, str(calls))
            reg = _reg_json(); reg["projects"][keyB].pop("last_used", None); _write_reg_json(reg)
            del calls[:]
            n = P.hibernate_idle(target=str(a), scope="others")
            check("SKIPS an entry with NO last_used (unknown -> keep)", keyB not in calls, str(calls))
        finally:
            P._pid_alive = saved_pid
            P.stop_neo4j = saved_stop
    with _EnvHome() as (home, work):
        check("absent registry -> 0, no raise", P.hibernate_idle(target=".", scope="others") == 0)


def gate_instance_rm() -> None:
    print("\n-- instance_rm + remove_entry: delete an instance, guard the base store --")
    saved_stop = P.stop_neo4j
    stop_calls = []
    with _EnvHome() as (home, work):
        a = _mk_project(work, "a")   # base 7687 / MCP 8787
        b = _mk_project(work, "b")   # 7689 / MCP 8789
        PR.allocate_ports(a); PR.allocate_neo4j_ports(a, base_adopt_ok=lambda: True)
        PR.allocate_ports(b); PR.allocate_neo4j_ports(b, base_adopt_ok=lambda: True)
        keyA, keyB = PR.project_key(a), PR.project_key(b)
        ok = PR.remove_entry(key=keyB, drop_neo4j_only=True)
        reg = _reg_json()
        check("remove_entry(drop_neo4j_only) returns True", ok is True)
        check("...drops bolt/http",
              "bolt_port" not in reg["projects"][keyB] and "http_port" not in reg["projects"][keyB])
        check("...KEEPS the MCP pool", reg["projects"][keyB].get("search_port") == 8789)
        check("remove_entry on a missing key -> False", PR.remove_entry(key="deadbeef0000") is False)
        # remove_entry full-pop
        PR.allocate_neo4j_ports(b, base_adopt_ok=lambda: True)   # re-add bolt for the rmtree leg
        keyB = PR.project_key(b)
        P.stop_neo4j = lambda version=None, project_key=None: (stop_calls.append(project_key) or True)
        try:
            inst_dir = P.deepmap_home() / "instances" / keyB
            (inst_dir / "data").mkdir(parents=True, exist_ok=True)
            (inst_dir / "data" / "x").write_text("hi", encoding="utf-8")
            rc = P.instance_rm(str(b), yes=False)   # b is not cwd -> no active-guard trip
            check("instance_rm returns 0", rc == 0, str(rc))
            check("instance_rm stopped B", keyB in stop_calls, str(stop_calls))
            check("instance_rm rmtree'd instances/<keyB>/", not inst_dir.exists())
            check("instance_rm dropped bolt/http from the registry",
                  "bolt_port" not in _reg_json()["projects"][keyB])
            del stop_calls[:]
            rc = P.instance_rm(str(a), yes=True)   # A is base -> refuse even with --yes
            check("REFUSES the base store (rc=2)", rc == 2, str(rc))
            check("...did NOT call stop_neo4j for the base", None not in stop_calls and keyA not in stop_calls)
            check("...A entry intact", "bolt_port" in _reg_json()["projects"][keyA])
        finally:
            P.stop_neo4j = saved_stop


def gate_instance_rm_guards() -> None:
    print("\n-- instance_rm guards: current-project needs --yes; --key prefix uniqueness --")
    import os as _os
    saved_stop = P.stop_neo4j
    stop_calls = []
    with _EnvHome() as (home, work):
        c = _mk_project(work, "c")   # base
        d = _mk_project(work, "d")   # non-base 7689
        PR.allocate_neo4j_ports(c, base_adopt_ok=lambda: True)
        PR.allocate_neo4j_ports(d, base_adopt_ok=lambda: True)
        keyD = PR.project_key(d)
        P.stop_neo4j = lambda version=None, project_key=None: (stop_calls.append(project_key) or True)
        saved_cwd = _os.getcwd()
        try:
            _os.chdir(str(d))
            rc = P.instance_rm(".", yes=False)
            check("REFUSES the current project without --yes (rc=2)", rc == 2, str(rc))
            check("...current refusal did not stop", keyD not in stop_calls)
            del stop_calls[:]
            rc = P.instance_rm(".", yes=True)
            check("--yes removes the current (non-base) project", rc == 0 and keyD in stop_calls, str(rc))
        finally:
            _os.chdir(saved_cwd)
            P.stop_neo4j = saved_stop
    with _EnvHome() as (home, work):
        x = _mk_project(work, "x")   # base
        y = _mk_project(work, "y")   # non-base 7689
        PR.allocate_neo4j_ports(x, base_adopt_ok=lambda: True)
        PR.allocate_neo4j_ports(y, base_adopt_ok=lambda: True)
        keyY = PR.project_key(y)
        P.stop_neo4j = lambda version=None, project_key=None: True
        try:
            rc = P.instance_rm(key=keyY, yes=True)
            check("--key <full key> targets the right (non-base) entry (rc=0)", rc == 0, str(rc))
            check("...Y bolt dropped", "bolt_port" not in _reg_json()["projects"].get(keyY, {}))
            reg = _reg_json()
            reg["projects"]["abc123aaaaaa"] = {"root": "r1", "bolt_port": 7691, "http_port": 7478}
            reg["projects"]["abc123bbbbbb"] = {"root": "r2", "bolt_port": 7693, "http_port": 7480}
            _write_reg_json(reg)
            rc = P.instance_rm(key="abc123", yes=True)
            check("--key <ambiguous prefix> REFUSES (rc=2)", rc == 2, str(rc))
            check("...removed nothing (both still present)",
                  "abc123aaaaaa" in _reg_json()["projects"] and "abc123bbbbbb" in _reg_json()["projects"])
        finally:
            P.stop_neo4j = saved_stop


def gate_registry_stats_and_table() -> None:
    print("\n-- registry_stats fields + instance_table + _dir_size_bounded visibility --")
    saved_pid = P._pid_alive
    with _EnvHome() as (home, work):
        a = _mk_project(work, "a")
        b = _mk_project(work, "b")
        PR.allocate_neo4j_ports(a, base_adopt_ok=lambda: True)
        PR.allocate_neo4j_ports(b, base_adopt_ok=lambda: True)
        keyA, keyB = PR.project_key(a), PR.project_key(b)
        PR.touch_last_used(a)
        stats = PR.registry_stats()
        rowA = next((r for r in stats["projects"] if r.get("key") == keyA), None)
        rowB = next((r for r in stats["projects"] if r.get("key") == keyB), None)
        check("registry_stats rows carry the project key", rowA is not None and rowB is not None)
        check("registry_stats rows carry last_used (touched A has one)", bool(rowA and rowA.get("last_used")))
        check("untouched B has last_used None", rowB is not None and rowB.get("last_used") is None)
        _write_pidfile(keyB)
        P._pid_alive = lambda pid: True
        try:
            rows = P.instance_table()
            tB = next((r for r in rows if str(r.get("neo4j_uri", "")).endswith(":7689")), None)
            check("instance_table marks B running", tB is not None and tB.get("state") == "running", str(tB))
            tA = next((r for r in rows if str(r.get("neo4j_uri", "")).endswith(":7687")), None)
            check("instance_table marks A NOT running (hibernated/absent)",
                  tA is not None and tA.get("state") in ("hibernated", "absent"), str(tA))
            txt = P.format_instance_table(rows)
            check("format_instance_table renders text carrying the bolt uri", "7689" in txt, txt[:80])
        finally:
            P._pid_alive = saved_pid
        d = P.deepmap_home() / "instances" / keyB / "data"
        d.mkdir(parents=True, exist_ok=True)
        (d / "f").write_text("x" * 100, encoding="utf-8")
        size, trunc = P._dir_size_bounded(d)
        check("_dir_size_bounded returns bytes>0 for a seeded dir", size >= 100, str(size))
        check("_dir_size_bounded on a missing path -> (0, False), no raise",
              P._dir_size_bounded(P.deepmap_home() / "nope") == (0, False))


def gate_touch_remove_race() -> None:
    print("\n-- concurrency: parallel touch + remove -> valid registry, no lost update --")
    import json as _json
    with _EnvHome() as (home, work):
        K = 5
        projs = [str(_mk_project(Path(work), f"t{i}")) for i in range(K)]
        for pr in projs:
            PR.allocate_neo4j_ports(pr, base_adopt_ok=lambda: True)
        keys = [PR.project_key(pr) for pr in projs]
        victim = keys[0]
        prog = (
            "import sys, os, time\n"
            "sys.path.insert(0, r'%s')\n" % str(BUILD).replace("\\", "\\\\") +
            "import port_registry as PR\n"
            "op, arg, barrier = sys.argv[1], sys.argv[2], sys.argv[3]\n"
            "while not os.path.exists(barrier): time.sleep(0.01)\n"
            "PR.remove_entry(key=arg, drop_neo4j_only=False) if op=='rm' else PR.touch_last_used(arg)\n"
            "print('ok')\n"
        )
        script = Path(home) / "racer2.py"
        script.write_text(prog, encoding="utf-8")
        barrier = Path(home) / "GO2"
        env = dict(os.environ)
        specs = [("rm", victim)] + [("touch", projs[i]) for i in range(1, K)]
        procs = [subprocess.Popen([sys.executable, str(script), op, arg, str(barrier)],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, text=True)
                 for op, arg in specs]
        barrier.write_text("go", encoding="utf-8")
        for pc in procs:
            pc.communicate(timeout=60)
        reg = _json.loads(PR.registry_path().read_text(encoding="utf-8"))
        check("registry remains valid JSON after the race", isinstance(reg.get("projects"), dict))
        check("the removed victim key is gone", victim not in reg["projects"], victim)
        check("the other touched projects survive WITH last_used",
              all(keys[i] in reg["projects"] and reg["projects"][keys[i]].get("last_used")
                  for i in range(1, K)),
              str({keys[i]: reg["projects"].get(keys[i], {}).get("last_used") for i in range(1, K)}))


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 64)
    print("DeepMap — per-project Neo4j ISOLATION gates (no Neo4j, no network)")
    print("=" * 64)
    gate_first_project_zero_drift()
    gate_two_projects_distinct()
    gate_pools_coexist_merge()
    gate_idempotent_neo4j()
    gate_owner_aware_base_adoption()
    gate_field_case_both_orders()
    gate_first_free_scan_probe()
    gate_never_raises_degrade()
    gate_registry_loss_recovery()
    gate_neo4j_uri_precedence()
    gate_resolved_instance()
    gate_paths_byte_identity()
    gate_conf_helpers()
    gate_configure_instance_content_addressed()
    gate_conf_no_duplicate_active_keys()
    gate_allocation_concurrency()
    gate_base_adopt_ok_verdict()
    gate_base_adopt_ok_live_branch()
    gate_instance_conf_recovery()
    gate_apply_neo4j_env_injection()
    gate_instance_lock_per_uri()
    # v0.27 instance-lifecycle (hibernation / instance-rm / visibility) — wrapped so a not-yet-built
    # function reports a clean per-gate RED instead of aborting the suite.
    _run_gate(gate_touch_last_used)
    _run_gate(gate_hibernate_sweep)
    _run_gate(gate_instance_rm)
    _run_gate(gate_instance_rm_guards)
    _run_gate(gate_registry_stats_and_table)
    _run_gate(gate_touch_remove_race)
    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
