#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j, no-socket, no-spend gates for the CLI's round-5 fixes (2026-09 live validation, owner W3).

Locks:
  G1  (ROUND-5 G1 = F2 for CLI READS) The CLI never answers from ANOTHER project's graph through a
      persisted mis-adopted base claim: when this project's route is its REGISTRY claim on the local base
      pair (bolt 7687 / http 7474) and no operator NEO4J_URI is set,
        * the base store's owner record naming another project (provision.foreign_base_claim_problem —
          a file read) refuses `search` / `impact` / `status` BEFORE a driver exists (exit 3, the refusal
          names that project and /deep-map:setup, which releases the claim), and doctor FAILs the Neo4j
          line with it and never shows that graph's census as this project's;
        * with NO record naming this project yet, the LIVE :_DeepMapMeta owner read on the opened driver
          decides by port_registry.owner_is_this_project (THE owner predicate): another project's owner
          (incl. the parent repo of a NESTED repository) -> refused, the driver closed; this project's own
          owner (also from a subdirectory of the same repo) or an unowned graph (no proof) -> proceeds;
        * doctor: once the claim is refused, this project's MCP servers that are UP (started on that claim,
          so they serve the other project's store) are WARNed with /deep-map:setup and named in the verdict
          — never "[OK] UP"; with this project's own graph behind its claim they stay [OK];
        * a record naming this project skips the live read (no per-call cost); a per-project instance and
          an operator NEO4J_URI never pay for the check (provision is not even loaded) and are unchanged;
        * an outage during the owner read stays an OUTAGE (the classified "not reachable" message), never a
          made-up verdict; provision failing to load still leaves the live read in force.
  NE  _classify_db_error builds its exception tuple defensively: against a neo4j 5.x-shaped exceptions
      module (NO ConnectionPoolError — it is new in 6.0) every CLI Neo4j failure is classified (never an
      AttributeError), `deepmap status` / `search` still print the friendly exit-3 message, and doctor —
      whose own except handler classifies — still reports instead of crashing. A name that exists but is
      not an exception class is skipped too. The real (6.x) driver's ConnectionPoolError still classifies
      as unreachable.

Hermetic: temp DEEPMAP_HOME, fake Neo4j drivers (neo4j.GraphDatabase.driver patched), a stub
neo4j.exceptions module swapped into sys.modules only for the NE section, temp project dirs (git repos
when git is on PATH); never touches ~/.deepmap or the committed .mcp.json. No sockets.
Helpers are shared with tests/cli_route_impact_gates.py (the round-2 CLI gate).
"""
from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build"

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


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


H = _load_module(REPO / "tests" / "cli_route_impact_gates.py", f"dm_cli_r2_helpers5_{time.time_ns()}")

BASE = {"bolt_port": 7687, "http_port": 7474, "search_port": 8787, "embed_port": 8788}
OWN = {"bolt_port": 7689, "http_port": 7476, "search_port": 8789, "embed_port": 8790}


def _write_owner_record(P, root_ident) -> Path:
    """The base store's owner record (<base data dir>/deepmap_base_owner.json) naming `root_ident`."""
    path = P.paths(None)["data"] / "deepmap_base_owner.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"root": str(root_ident).replace("\\", "/"), "source": "setup-sync",
                                "ts": "2026-09-26T00:00:00Z"}), encoding="utf-8")
    return path


def _clear_owner_record(P) -> None:
    try:
        (P.paths(None)["data"] / "deepmap_base_owner.json").unlink()
    except FileNotFoundError:
        pass


class _LoadSpy:
    """Wraps C._load: counts the modules loaded by name; `fail` names modules whose load raises."""

    def __init__(self, C, fail=()):
        self.C, self.orig, self.fail, self.names = C, C._load, set(fail), []

    def __enter__(self):
        def _load(name):
            self.names.append(name)
            if name in self.fail:
                raise ImportError(f"(gate) {name} unavailable")
            return self.orig(name)
        self.C._load = _load
        return self

    def __exit__(self, *a):
        self.C._load = self.orig
        return False


def _run(C, argv, *, cwd, owner, nodes=7, session_exc=None, env=None, fail_loads=()):
    """(rc, raised, out, err, factory, captured, loaded) of `deepmap <argv>` in `cwd` over a fake graph."""
    captured: dict = {}
    overrides = H._search_stubs(captured)
    ret_cls = overrides["retrieval"].DeepMapRetriever

    def _query_impact(drv, name, limit, **kw):   # a non-refused impact records its symbol ("query")
        captured["query"] = name
        return []
    overrides["retrieval"] = types.SimpleNamespace(DeepMapRetriever=ret_cls, query_impact=_query_impact)
    fac = H._Factory(lambda: H._GraphDriver(owner=owner, nodes=nodes, session_exc=session_exc))
    with H._env(**(env or {})), H._patched_graphdb(fac), _LoadSpy(C, fail_loads) as spy:
        rc, raised, out, err = H._main(C, argv, overrides=overrides, cwd=cwd)
    return rc, raised, out, err, fac, captured, list(spy.names)


def _check_mcp_not_ok(out: str, label: str) -> None:
    """Doctor refused this project's base claim (another project's store): the MCP servers on this project's
    pair (UP in the fake provision) were started on that claim, so they serve THAT store — never [OK]."""
    for name in ("deepmap-search", "deepmap-embed"):
        ln = H._line_with(out, name)
        check(f"{label}: MCP {name} is UP but NOT reported [OK] — it serves the refused store",
              "[WARN]" in ln and "/deep-map:setup" in ln and "[OK" not in ln, ln)
    check(f"{label}: the verdict names the MCP servers serving another project's graph",
          any("MCP" in r and "another project" in r for r in _reasons(out)), " | ".join(_reasons(out)))


def _reasons(out: str) -> list:
    """The doctor verdict's `  - <reason>` lines."""
    return [ln.strip()[2:] for ln in out.splitlines() if ln.startswith("  - ")]


def _owner_queries(fac) -> int:
    drv = fac.last
    return sum(1 for q in (drv.queries if drv else []) if "_DeepMapMeta" in q and "owning_root" in q)


# ---- G1: F2 for CLI reads --------------------------------------------------------------------------------
def gate_base_claim_reads(C, PR, P, work: Path) -> None:
    print("\n-- G1: a persisted mis-adopted base claim never serves ANOTHER project's graph to CLI reads --")
    a, b = work / "claimA", work / "ownerB"
    has_git = bool(H._git_repo(a))
    a.mkdir(exist_ok=True)
    b.mkdir()
    ident_a, ident_b = PR.project_identity(a), PR.project_identity(b)

    # (1) the base store's owner record names B; A's registry entry claims the base pair.
    H._write_registry(PR, {a: BASE, b: OWN})
    _write_owner_record(P, ident_b)
    for argv in (["search", "x"], ["impact", "foo"], ["status"]):
        rc, raised, out, err, fac, cap, _ = _run(C, argv, cwd=a, owner=ident_b, nodes=99)
        check(f"record names B ({argv[0]}): refused with exit 3, no traceback",
              raised is None and rc == 3, f"{rc} {raised!r} {err[-300:]}")
        check(f"record names B ({argv[0]}): the refusal names B and /deep-map:setup",
              "/deep-map:setup" in err and b.name in err, err[-400:])
        check(f"record names B ({argv[0]}): refused BEFORE a driver exists (B's graph never queried)",
              not fac.calls and "query" not in cap and "99" not in out, f"{fac.calls} {cap} {out[-200:]}")
    # doctor: FAIL with the refusal, never B's census as A's.
    real_prov = P
    prov = H._fake_provision({8787, 8788}, [])
    for name in ("foreign_base_claim_problem", "_base_owner_is", "_foreign_claim_text"):
        setattr(prov, name, getattr(real_prov, name))
    fac = H._Factory(lambda: H._GraphDriver(owner=ident_b, nodes=99))
    with H._patched_graphdb(fac):
        rc, out = H._run_doctor(C, cwd=a, provision=prov)
    neo = H._line_with(out, "Neo4j")
    check("doctor, record names B: the Neo4j line FAILs, naming B and /deep-map:setup",
          "[FAIL]" in neo and b.name in neo and "/deep-map:setup" in neo, neo)
    check("doctor, record names B: B's census (99 nodes) is NOT shown as this project's; verdict FAILED",
          "99 nodes" not in out and H._verdict_of(out) == "FAILED" and rc == 1,
          f"{H._line_with(out, 'nodes')} {H._verdict_of(out)} {rc}")
    _check_mcp_not_ok(out, "doctor, record names B")
    _clear_owner_record(P)

    # (2) no record names A yet (a pre-upgrade claim): the LIVE owner decides.
    for argv in (["search", "x"], ["impact", "foo"], ["status"]):
        rc, raised, out, err, fac, cap, _ = _run(C, argv, cwd=a, owner=ident_b, nodes=99)
        check(f"no record, live owner B ({argv[0]}): refused with exit 3 before any read of B's graph",
              raised is None and rc == 3 and "query" not in cap and "99" not in out,
              f"{rc} {raised!r} {cap} {err[-300:]}")
        check(f"no record, live owner B ({argv[0]}): the refusal names B and /deep-map:setup",
              "/deep-map:setup" in err and b.name in err, err[-400:])
        check(f"no record, live owner B ({argv[0]}): the driver it opened is closed",
              fac.last is not None and fac.last.closed, "")
    fac = H._Factory(lambda: H._GraphDriver(owner=ident_b, nodes=99))
    with H._patched_graphdb(fac):
        rc, out = H._run_doctor(C, cwd=a, provision=prov)
    neo = H._line_with(out, "Neo4j")
    check("doctor, live owner B: the Neo4j line FAILs naming B; B's census is not shown",
          "[FAIL]" in neo and b.name in neo and "99 nodes" not in out, neo)
    _check_mcp_not_ok(out, "doctor, live owner B")
    # non-regression: this project's OWN graph behind its base claim -> its UP servers stay [OK].
    fac = H._Factory(lambda: H._GraphDriver(owner=ident_a, nodes=12))
    with H._patched_graphdb(fac):
        rc, out = H._run_doctor(C, cwd=a, provision=prov)
    ln = H._line_with(out, "deepmap-search")
    check("doctor, live owner = this project: its UP MCP servers stay [OK] (no refusal, no WARN)",
          "[OK" in ln and "UP" in ln and "[OK" in H._line_with(out, "Neo4j reachable"),
          f"{ln} | {H._line_with(out, 'Neo4j')}")
    # ... the same with a doctor provision that lacks every F2 helper (an older copy): the live read holds.
    fac = H._Factory(lambda: H._GraphDriver(owner=ident_b, nodes=99))
    with H._patched_graphdb(fac):
        rc, out = H._run_doctor(C, cwd=a, provision=H._fake_provision({8787, 8788}, []))
    check("doctor, provision without the F2 helpers: the live owner read still refuses B's graph",
          "[FAIL]" in H._line_with(out, "Neo4j") and "99 nodes" not in out, H._line_with(out, "Neo4j"))
    # provision can't be loaded at all: the live read still applies.
    rc, raised, out, err, fac, cap, loaded = _run(C, ["search", "x"], cwd=a, owner=ident_b,
                                                  fail_loads=("provision",))
    check("provision unloadable: the live owner read still refuses B's graph (exit 3, not searched)",
          raised is None and rc == 3 and "query" not in cap and "/deep-map:setup" in err,
          f"{rc} {raised!r} {cap} {err[-300:]}")

    # this project's own graph / an unowned graph -> proceeds.
    rc, raised, out, err, fac, cap, _ = _run(C, ["search", "x"], cwd=a, owner=ident_a)
    check("no record, live owner = this project: search proceeds (exit 0)",
          raised is None and rc == 0 and "query" in cap, f"{rc} {raised!r} {err[-300:]}")
    rc, raised, out, err, fac, cap, _ = _run(C, ["search", "x"], cwd=a, owner=None)
    check("no record, unowned live graph (no proof of another owner): search proceeds (exit 0)",
          raised is None and rc == 0 and "query" in cap, f"{rc} {raised!r} {err[-300:]}")
    rc, raised, out, err, fac, cap, _ = _run(C, ["status"], cwd=a, owner=ident_a, nodes=12)
    check("no record, live owner = this project: status shows the census (exit 0)",
          raised is None and rc == 0 and "nodes=12" in out, f"{rc} {raised!r} {out[-300:]} {err[-200:]}")

    # a record naming THIS project skips the live read (no per-call cost once proven).
    _write_owner_record(P, ident_a)
    rc, raised, out, err, fac, cap, _ = _run(C, ["search", "x"], cwd=a, owner=ident_a)
    check("record names this project: search proceeds and runs NO owner query",
          raised is None and rc == 0 and "query" in cap and _owner_queries(fac) == 0,
          f"{rc} {raised!r} {fac.last.queries if fac.last else None}")
    _clear_owner_record(P)

    # an outage during the owner read stays an outage.
    import neo4j.exceptions as E
    rc, raised, out, err, fac, cap, _ = _run(C, ["search", "x"], cwd=a, owner=ident_b,
                                             session_exc=E.ServiceUnavailable("connection refused"))
    check("owner read on a DOWN base: the classified 'not reachable' message (exit 3), not a refusal",
          raised is None and rc == 3 and "not reachable" in err and "belongs to project" not in err,
          f"{rc} {raised!r} {err[-300:]}")
    check("owner read on a DOWN base: the driver is closed", fac.last is not None and fac.last.closed, "")

    # a per-project instance: no check at all (provision not loaded, no owner query), even with a record.
    H._write_registry(PR, {a: OWN, b: BASE})
    _write_owner_record(P, ident_b)
    rc, raised, out, err, fac, cap, loaded = _run(C, ["search", "x"], cwd=a, owner=ident_b)
    check("per-project instance (7689): search proceeds, unchanged",
          raised is None and rc == 0 and "query" in cap and fac.calls
          and fac.calls[0][0] == PR.bolt_uri(7689), f"{rc} {raised!r} {fac.calls[:1]} {err[-200:]}")   # J4: 127.0.0.1
    check("per-project instance: provision is not loaded and no owner query runs (zero added cost)",
          "provision" not in loaded and _owner_queries(fac) == 0, f"{loaded} {_owner_queries(fac)}")
    _clear_owner_record(P)

    # the operator escape hatch (NEO4J_URI) is used verbatim — its own foreign-owner WARN path is unchanged.
    H._write_registry(PR, {a: BASE, b: OWN})
    _write_owner_record(P, ident_b)
    rc, raised, out, err, fac, cap, loaded = _run(C, ["search", "x"], cwd=a, owner=ident_b,
                                                  env={"NEO4J_URI": "bolt://localhost:7687"})
    check("operator NEO4J_URI (even at 7687): used verbatim, search proceeds, no F2 check",
          raised is None and rc == 0 and "query" in cap and "provision" not in loaded,
          f"{rc} {raised!r} {loaded} {err[-200:]}")
    _clear_owner_record(P)

    if not has_git:
        check("git present for the nested-repo / subdirectory cases", True, "(skipped: no git)")
        return
    # a NESTED repository claiming the base while the parent repo's graph is there: a different project.
    nested = a / "nested"
    H._git_repo(nested)
    H._write_registry(PR, {nested: BASE, b: OWN})
    rc, raised, out, err, fac, cap, _ = _run(C, ["search", "x"], cwd=nested, owner=ident_a)
    check("nested repo claiming the base over the PARENT's graph: refused (F1 — a project of its own)",
          raised is None and rc == 3 and "query" not in cap and "/deep-map:setup" in err,
          f"{rc} {raised!r} {cap} {err[-300:]}")
    # a plain subdirectory of the same repo: the same project.
    sub = a / "pkg" / "sub"
    sub.mkdir(parents=True)
    H._write_registry(PR, {a: BASE, b: OWN})
    rc, raised, out, err, fac, cap, _ = _run(C, ["search", "x"], cwd=sub, owner=ident_a)
    check("subdirectory of the claiming repo over its own graph: search proceeds (the same project)",
          raised is None and rc == 0 and "query" in cap, f"{rc} {raised!r} {err[-300:]}")


# ---- NE: _classify_db_error on a neo4j 5.x-shaped exceptions module --------------------------------------
def _stub_neo4j(with_pool: bool = False, bare: bool = False):
    """A neo4j package whose exceptions module has the 5.x shape (no ConnectionPoolError unless asked);
    `bare` -> an exceptions module with none of the classes at all."""
    ex = types.ModuleType("neo4j.exceptions")
    if not bare:
        class Neo4jError(Exception):
            code = None
        class ClientError(Neo4jError):
            pass
        class AuthError(ClientError):
            pass
        class DatabaseError(Neo4jError):
            pass
        class TransientError(Neo4jError):
            pass
        class DriverError(Exception):
            pass
        class ServiceUnavailable(DriverError):
            pass
        class SessionExpired(DriverError):
            pass
        for cls in (Neo4jError, ClientError, AuthError, DatabaseError, TransientError, DriverError,
                    ServiceUnavailable, SessionExpired):
            setattr(ex, cls.__name__, cls)
        if with_pool:
            class ConnectionPoolError(DriverError):
                pass
            ex.ConnectionPoolError = ConnectionPoolError
    pkg = types.ModuleType("neo4j")
    pkg.__path__ = []
    pkg.exceptions = ex
    pkg.__version__ = "5.28.2"
    return pkg, ex


@contextlib.contextmanager
def _swapped_neo4j(pkg, ex):
    saved = {k: sys.modules.get(k) for k in ("neo4j", "neo4j.exceptions")}
    sys.modules["neo4j"], sys.modules["neo4j.exceptions"] = pkg, ex
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


def _classify(C, exc):
    try:
        return C._classify_db_error(exc), None
    except BaseException as e:  # noqa: BLE001 — a raise is the failure being gated
        return None, e


def gate_classify_5x(C, work: Path) -> None:
    print("\n-- NE: _classify_db_error never raises on a neo4j 5.x exceptions module (no ConnectionPoolError) --")
    pkg, ex = _stub_neo4j()
    check("(fixture) the stub exceptions module has no ConnectionPoolError, like neo4j 5.x",
          not hasattr(ex, "ConnectionPoolError"), "")
    cases = (("ServiceUnavailable", ex.ServiceUnavailable("refused"), "unreachable"),
             ("SessionExpired", ex.SessionExpired("gone"), "unreachable"),
             ("a non-auth Neo4jError (the server answered)", ex.DatabaseError("boom"), "query"),
             ("a DriverError", ex.DriverError("driver"), "driver"),
             ("an AuthError", ex.AuthError("unauthorized"), "auth"),
             ("an OSError", ConnectionRefusedError("refused"), "unreachable"),
             ("a RuntimeError", RuntimeError("x"), "driver"),
             ("ProjectResolutionError", C.ProjectResolutionError("no route"), "resolve"))
    with _swapped_neo4j(pkg, ex):
        for label, exc, want in cases:
            got, raised = _classify(C, exc)
            check(f"5.x driver: {label} -> {want!r}, never raises", raised is None and got == want,
                  f"got {got!r} raised {raised!r}")
    bare_pkg, bare_ex = _stub_neo4j(bare=True)
    with _swapped_neo4j(bare_pkg, bare_ex):
        got, raised = _classify(C, ConnectionRefusedError("refused"))
        check("an exceptions module missing every class: an OSError still -> 'unreachable', never raises",
              raised is None and got == "unreachable", f"got {got!r} raised {raised!r}")
        got, raised = _classify(C, RuntimeError("x"))
        check("... and anything else -> 'driver', never raises", raised is None and got == "driver",
              f"got {got!r} raised {raised!r}")
    # a name that exists but is NOT an exception class (a deprecated alias left as None / a function) must be
    # skipped too — isinstance() with a non-class in its tuple raises TypeError.
    odd_pkg, odd_ex = _stub_neo4j()
    odd_ex.ConnectionPoolError = None
    odd_ex.SessionExpired = lambda *a: None
    with _swapped_neo4j(odd_pkg, odd_ex):
        got, raised = _classify(C, odd_ex.ServiceUnavailable("refused"))
        check("a non-class ConnectionPoolError / SessionExpired attribute is skipped: "
              "ServiceUnavailable -> 'unreachable'", raised is None and got == "unreachable",
              f"got {got!r} raised {raised!r}")
        got, raised = _classify(C, odd_ex.DatabaseError("boom"))
        check("... and a server error -> 'query' (the class lookups after it still work)",
              raised is None and got == "query", f"got {got!r} raised {raised!r}")
    pool_pkg, pool_ex = _stub_neo4j(with_pool=True)
    with _swapped_neo4j(pool_pkg, pool_ex):
        got, raised = _classify(C, pool_ex.ConnectionPoolError("pool"))
        check("a driver WITH ConnectionPoolError: it still classifies as 'unreachable'",
              raised is None and got == "unreachable", f"got {got!r} raised {raised!r}")
    try:
        import neo4j.exceptions as RE
        real_pool = getattr(RE, "ConnectionPoolError", None)
    except Exception:
        real_pool = None
    if isinstance(real_pool, type):
        got, raised = _classify(C, real_pool("pool"))
        check("the installed (6.x) driver's ConnectionPoolError -> 'unreachable' (non-regression)",
              raised is None and got == "unreachable", f"got {got!r} raised {raised!r}")

    # the user-facing paths: main() (status / search) and doctor's own except handler.
    p = work / "ne5x"
    p.mkdir()
    with _swapped_neo4j(pkg, ex):
        for argv in (["status"], ["search", "x"]):
            drv = H._GraphDriver(session_exc=ex.ServiceUnavailable("Couldn't connect to localhost:7689"))
            rc, raised, out, err = H._main(C, argv, overrides=H._search_stubs({}), driver=drv, cwd=p)
            check(f"5.x driver, Neo4j down ({argv[0]}): no AttributeError escapes main()",
                  not isinstance(raised, AttributeError), repr(raised))
            check(f"5.x driver, Neo4j down ({argv[0]}): the friendly exit-3 message, no AttributeError",
                  raised is None and rc == 3 and "not reachable" in err, f"{rc} {raised!r} {err[-300:]}")
        saved_driver = C._driver

        def _down():
            raise ex.ServiceUnavailable("Couldn't connect to localhost:7689")
        C._driver = _down
        try:
            try:
                rc, out = H._run_doctor(C, cwd=p)
                raised = None
            except BaseException as e:  # noqa: BLE001
                rc, out, raised = None, "", e
        finally:
            C._driver = saved_driver
        neo = H._line_with(out, "Neo4j")
        check("5.x driver, Neo4j down: doctor reports FAIL 'unreachable' instead of crashing in its handler",
              raised is None and "[FAIL]" in neo and "unreachable" in neo and rc == 1,
              f"{rc} {raised!r} {neo}")


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 72)
    print("DeepMap — CLI round-5 gates (W3: F2 for CLI reads, defensive 5.x DB-error classification)")
    print("=" * 72)
    saved_env = {k: os.environ.get(k) for k in H._SCRUB + ("DEEPMAP_HOME",)}
    for k in saved_env:
        if k != "DEEPMAP_HOME":
            os.environ.pop(k, None)
    with tempfile.TemporaryDirectory() as td:
        base = Path(td).resolve()
        home = base / "home"
        home.mkdir()
        work = base / "work"
        work.mkdir()
        os.environ["DEEPMAP_HOME"] = str(home)
        try:
            if str(BUILD) not in sys.path:
                sys.path.insert(0, str(BUILD))
            C = H._load_cli()
            PR = _load_module(BUILD / "port_registry.py", f"dm_pr_r5_{time.time_ns()}")
            P = _load_module(BUILD / "provision.py", f"dm_prov_r5_{time.time_ns()}")
            for gate in (lambda: gate_base_claim_reads(C, PR, P, work),
                         lambda: gate_classify_5x(C, work)):
                try:
                    gate()
                except Exception as exc:  # noqa: BLE001 — a crashing gate is a RED gate, not a crash
                    import traceback
                    traceback.print_exc()
                    check(f"gate ran without crashing ({type(exc).__name__}: {exc})", False)
                finally:
                    H._clear_registry(PR)
                    _clear_owner_record(P)
        finally:
            for k, v in saved_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
    print("\n" + "=" * 72)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
