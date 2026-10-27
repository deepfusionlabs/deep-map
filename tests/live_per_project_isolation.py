#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LIVE integration test for TRUE per-project Neo4j isolation — the one thing the hermetic gates
CANNOT cover: that the generated per-instance conf ACTUALLY starts Neo4j, that two instances coexist
on different bolt ports with SEPARATE graphs, and that stopping one leaves the other serving.

This validates the on-a-real-box uncertainties the design flagged:
  * U1 — NEO4J_CONF is a COMPLETE conf dir (a real start fails if server-logs.xml etc. are missing).
  * U2 — strict config validation accepts the managed block (a duplicate active key refuses startup).
  * U4 — the launcher (neo4j / neo4j.bat) honours NEO4J_CONF from the inherited env (Windows + POSIX).
  * The core claim: project A and project B have ISOLATED graphs; stop(A) doesn't affect B.

OPT-IN and ISOLATED. It runs ONLY when DEEPMAP_LIVE_TEST=1 (so run_all never touches a real Neo4j; only the
manual .github/workflows/macos.yml live job sets it, on a throwaway macOS runner),
needs Java 17/21 on PATH, and does EVERYTHING under a throwaway DEEPMAP_HOME (a temp dir) — it never
reads or writes the operator's real ~/.deepmap, and it never touches a base-port instance the operator
may be running (it allocates NON-base ports). It stands up two real Neo4j JVMs and ALWAYS tears them
down (stop_neo4j per instance + a hard-kill fallback) in a finally block. Reuses the operator's cached
Neo4j dist zip if present (no re-download), else downloads once.

Run:  DEEPMAP_LIVE_TEST=1 NEO4J_PASSWORD=deepmap python tests/live_per_project_isolation.py
"""
from __future__ import annotations

import os
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


def _skip(msg: str) -> int:
    print(f"[SKIP] live per-project isolation test: {msg}")
    print("       (opt-in: set DEEPMAP_LIVE_TEST=1 and ensure Java 17/21 is on PATH)")
    return 0


def _java_present() -> bool:
    exe = shutil.which("java")
    if not exe:
        return False
    try:
        subprocess.run([exe, "-version"], capture_output=True, timeout=20)
        return True
    except Exception:
        return False


def _push_marker(uri: str, password: str, marker: str) -> None:
    from neo4j import GraphDatabase
    drv = GraphDatabase.driver(uri, auth=("neo4j", password), notifications_min_severity="OFF")
    try:
        with drv.session() as s:
            s.run("MERGE (m:_LiveTestMarker {tag:$t})", t=marker)
    finally:
        drv.close()


def _markers(uri: str, password: str) -> set:
    from neo4j import GraphDatabase
    drv = GraphDatabase.driver(uri, auth=("neo4j", password), notifications_min_severity="OFF")
    try:
        with drv.session() as s:
            return {r["t"] for r in s.run("MATCH (m:_LiveTestMarker) RETURN m.tag AS t")}
    finally:
        drv.close()


def main() -> int:
    if os.environ.get("DEEPMAP_LIVE_TEST") != "1":
        return _skip("DEEPMAP_LIVE_TEST != 1")
    if not _java_present():
        return _skip("no Java on PATH")

    import provision as P
    import port_registry as PR

    password = os.environ.get("NEO4J_PASSWORD", "deepmap")
    version = P.neo4j_version()
    real_home = P.deepmap_home()
    tmp_home = Path(tempfile.mkdtemp(prefix="deepmap-live-"))
    saved_home = os.environ.get("DEEPMAP_HOME")
    saved_uri = os.environ.get("NEO4J_URI")
    os.environ["DEEPMAP_HOME"] = str(tmp_home)
    os.environ.pop("NEO4J_URI", None)   # exercise per-project instancing, not an operator override
    P._USER_SET_NEO4J_URI = False

    keyA = keyB = None
    try:
        print("=" * 68)
        print(f"LIVE per-project Neo4j isolation — isolated DEEPMAP_HOME={tmp_home}")
        print("=" * 68)

        # Reuse the operator's cached dist zip so we don't re-download (falls back to a real download).
        (tmp_home / "cache").mkdir(parents=True, exist_ok=True)
        cached = real_home / "cache" / P.dist_filename(version, P.is_windows())
        if cached.exists():
            shutil.copy2(str(cached), str(tmp_home / "cache" / cached.name))
            print(f"[live] reused cached dist {cached.name} (no download)")

        # Two projects, allocated to NON-base ports (base_adopt_ok=False) so we never touch a base-port
        # instance the operator may be running. The allocator probes REAL sockets, so the pairs are free.
        pa = Path(tmp_home / "projA"); pa.mkdir()
        pb = Path(tmp_home / "projB"); pb.mkdir()
        keyA = PR.project_key(str(pa))
        keyB = PR.project_key(str(pb))
        boltA, httpA = PR.allocate_neo4j_ports(str(pa), base_adopt_ok=lambda: False)
        boltB, httpB = PR.allocate_neo4j_ports(str(pb), base_adopt_ok=lambda: False)
        uriA = f"bolt://localhost:{boltA}"
        uriB = f"bolt://localhost:{boltB}"
        print(f"[live] A -> {uriA} (http {httpA}); B -> {uriB} (http {httpB})")
        check("two projects got DISTINCT bolt ports", boltA != boltB, f"{boltA} vs {boltB}")

        # Bring up instance A (extract shared binary once, write A's conf, set A's password, start).
        okA = P.ensure_neo4j(uri=uriA, user="neo4j", password=password, version=version,
                             project_key=keyA, bolt=boltA, http=httpA, timeout=150)
        check("instance A started + Bolt reachable (U1/U2/U4: conf is complete + valid + NEO4J_CONF honored)",
              okA, "see the console log under the tmp home")
        if not okA:
            return 1

        # Bring up instance B on its own port + data dir, SHARING the one extracted binary.
        okB = P.ensure_neo4j(uri=uriB, user="neo4j", password=password, version=version,
                             project_key=keyB, bolt=boltB, http=httpB, timeout=150)
        check("instance B started on its OWN port (one shared binary drives N instances)", okB)
        if not okB:
            return 1

        check("A and B are BOTH live simultaneously (side-by-side)",
              P.is_bolt_up(uriA, "neo4j", password) and P.is_bolt_up(uriB, "neo4j", password))

        # Prove GRAPH ISOLATION: a marker written to A must NOT appear in B, and vice-versa.
        _push_marker(uriA, password, "ONLY-IN-A")
        _push_marker(uriB, password, "ONLY-IN-B")
        ma, mb = _markers(uriA, password), _markers(uriB, password)
        check("A's graph has A's marker and NOT B's (isolated graph)",
              "ONLY-IN-A" in ma and "ONLY-IN-B" not in ma, f"A markers={ma}")
        check("B's graph has B's marker and NOT A's (isolated graph)",
              "ONLY-IN-B" in mb and "ONLY-IN-A" not in mb, f"B markers={mb}")

        # Per-project stop: stopping A must leave B serving (the T6.1 requirement).
        P.stop_neo4j(version, keyA)
        import time
        for _ in range(20):
            if not P.is_bolt_up(uriA, "neo4j", password, timeout=2):
                break
            time.sleep(1)
        check("stop(A) brought A down", not P.is_bolt_up(uriA, "neo4j", password, timeout=2))
        check("...and B is STILL serving (T6.1: one project's downtime doesn't affect the other)",
              P.is_bolt_up(uriB, "neo4j", password, timeout=3))

        # Heal revives A on its OWN ports without a re-sync (its data dir survived the stop).
        okHeal = P.ensure_neo4j(uri=uriA, user="neo4j", password=password, version=version,
                                project_key=keyA, bolt=boltA, http=httpA, timeout=120)
        check("heal/ensure revived A on its own port", okHeal)
        if okHeal:
            check("revived A STILL has its marker (data dir survived — true no-re-sync revive)",
                  "ONLY-IN-A" in _markers(uriA, password))

        # ── v0.27: idle HIBERNATION -> revive (the data dir survives) ────────────────────────
        # Make A look IDLE (old last_used) and B recently-used, then hibernate from B's viewpoint.
        # A must go down; B (current) must keep serving; A's data dir must survive so an ensure
        # revives A WITH its marker (a true no-re-sync revive — the hibernation payoff).
        import json as _json
        from datetime import datetime as _dt, timezone as _tz, timedelta as _td

        def _set_last_used(key, minutes_ago):
            reg = _json.loads(PR.registry_path().read_text(encoding="utf-8"))
            reg["projects"][key]["last_used"] = (
                _dt.now(_tz.utc) - _td(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
            PR.registry_path().write_text(_json.dumps(reg), encoding="utf-8")

        _set_last_used(keyA, 30)   # A idle for 30 min
        _set_last_used(keyB, 0)    # B just used
        check("A instance is running before hibernation", P._instance_running_for(keyA) is True)
        n_hib = P.hibernate_idle(target=str(pb), scope="others", idle_minutes=1)
        for _ in range(20):
            if not P.is_bolt_up(uriA, "neo4j", password, timeout=2):
                break
            time.sleep(1)
        check("hibernate_idle stopped the IDLE non-current A (returned >=1)", n_hib >= 1, str(n_hib))
        check("hibernated A is DOWN", not P.is_bolt_up(uriA, "neo4j", password, timeout=2))
        check("...B (current) is STILL serving (never hibernate the current project)",
              P.is_bolt_up(uriB, "neo4j", password, timeout=3))
        okRevive = P.ensure_neo4j(uri=uriA, user="neo4j", password=password, version=version,
                                  project_key=keyA, bolt=boltA, http=httpA, timeout=120)
        check("ensure_neo4j revived the hibernated A", okRevive)
        if okRevive:
            check("revived A STILL has ONLY-IN-A (hibernation kept the data dir — no re-sync)",
                  "ONLY-IN-A" in _markers(uriA, password))

        # ── v0.27: instance-rm (dir + registry entry gone; a fresh ensure starts EMPTY) ──────
        inst_b = tmp_home / "instances" / keyB
        rc_rm = P.instance_rm(str(pb), yes=True)
        for _ in range(20):
            if not P.is_bolt_up(uriB, "neo4j", password, timeout=2):
                break
            time.sleep(1)
        check("instance_rm returned 0", rc_rm == 0, str(rc_rm))
        check("instance-rm stopped B (bolt down)", not P.is_bolt_up(uriB, "neo4j", password, timeout=2))
        check("instance-rm deleted instances/<keyB>/ (data dir gone)", not inst_b.exists())
        check("instance-rm dropped B's bolt from the registry",
              PR.lookup_neo4j_ports(str(pb)) is None, str(PR.lookup_neo4j_ports(str(pb))))
        # A fresh B on new ports must start an EMPTY graph — proving the data dir was really deleted.
        boltB2, httpB2 = PR.allocate_neo4j_ports(str(pb), base_adopt_ok=lambda: False)
        uriB2 = f"bolt://localhost:{boltB2}"
        okFresh = P.ensure_neo4j(uri=uriB2, user="neo4j", password=password, version=version,
                                 project_key=keyB, bolt=boltB2, http=httpB2, timeout=150)
        check("a fresh ensure for B started", okFresh)
        if okFresh:
            check("fresh B graph is EMPTY (ONLY-IN-B gone — the data dir was really deleted)",
                  "ONLY-IN-B" not in _markers(uriB2, password))
    finally:
        # ALWAYS tear down both JVMs and delete the throwaway home — never leave a process running.
        for k in (keyA, keyB):
            if k:
                try:
                    P.stop_neo4j(version, k)
                except Exception as exc:
                    print(f"[live] cleanup stop({k[:8]}) warning: {exc}", file=sys.stderr)
        if saved_home is None:
            os.environ.pop("DEEPMAP_HOME", None)
        else:
            os.environ["DEEPMAP_HOME"] = saved_home
        if saved_uri is not None:
            os.environ["NEO4J_URI"] = saved_uri
        try:
            shutil.rmtree(str(tmp_home), ignore_errors=True)
        except Exception:
            pass

    print("\n" + "=" * 68)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} live checks passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
