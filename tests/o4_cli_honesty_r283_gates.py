#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""0.28.3 (O4, build/deepmap_cli.py) — three CLI honesty findings from the 2026-09-29 DeepInit refresh, RED-first.

A  ISS-build:025 (C7): `deepmap doctor` had no wording for the `driver` error kind. _classify_db_error returns
   'driver' for a CLIENT-side driver failure: a DriverError that is not ServiceUnavailable / SessionExpired /
   ConnectionPoolError (a user name inside a Bolt URI; a 5.6-or-older driver rejects notifications_min_severity
   the same way), and any non-OSError,
   non-Neo4j exception (a DNS failure the driver raises as ValueError — a resolver error other than an unknown
   host, e.g. EAI_FAIL; a neo4j package that won't import). `main` words that kind "Neo4j driver error talking
   to <uri> — <error> ... Diagnose with /deep-map:doctor", but doctor fell through to "unreachable (<Type>) at
   <uri> — <check that it is running / revive it with heal>": the error text was dropped and the remedy was a
   stopped instance's. Now doctor FAILs with the actual error text and a remedy for the route in use — fix
   NEO4J_URI (a Bolt URI whose host resolves) for an operator endpoint; the pinned neo4j driver install (the
   range requirements.txt pins, into DeepMap's own Python) for a package that is missing or outside that range
   — and never the heal/start remedy. FAIL + exit 1 are kept; the other kinds keep their wording.
   Round 3 (docs[3]): an operator host that does NOT resolve is not that kind. The real driver maps the
   resolver's errno (neo4j/_async_compat/network/_util.py): the ordinary unknown host (EAI_NONAME) and a
   temporary DNS failure (EAI_AGAIN) raise ServiceUnavailable — the 'unreachable' kind — and only other resolver
   errors raise ValueError. Gate A5 used to script a ValueError over gaierror(11001) (EAI_NONAME on Windows), a
   pair the driver never raises, and commands/doctor.md and _driver_remedy's docstring listed "a host that
   doesn't resolve" as a driver error. A5 now drives the REAL driver over a scripted getaddrinfo (no DNS query,
   no socket): an unknown host stays `unreachable (ServiceUnavailable)`, and doctor's line — which used to drop
   the driver's "Failed to DNS resolve address ..." text and say "check that it is running" — now shows that
   text and says to check the NEO4J_URI host name (main's message gets the same remedy); EAI_FAIL is the driver
   kind. The docstring and doctor.md are checked against those facts.
   2026-10-24 (A0/A1): an operator NEO4J_URI that is the Neo4j Browser's http:// address no longer reaches the
   driver at all. neo4j 6.2 rejected it with ConfigurationError (this gate's old precondition), but neo4j 6.4.1 —
   inside requirements.txt's range, what CI resolves — reads http:// as a preview HTTP Query API driver
   (ModuleNotFoundError 'aiohttp' without that package), so the precondition failed on Linux. DeepMap now refuses
   it ITSELF in the one routing rule (port_registry.neo4j_uri_problem via resolve_neo4j_route): A0 asserts that
   rejection (the driver's own handling is printed as information only), A1 that doctor FAILs (exit 1, verdict
   FAILED) with the rule's text, constructs no driver, and never words it a driver error / unreachable / heal.
   tests/neo4j_uri_scheme_gates.py holds the full truth table and every other consumer.
B  ISS-build:018 (C6, the CLI part): cmd_impact's docstring said impact "Degrades to one-hop :CALLS on a
   pre-Track-C graph". query_impact walks a graph without typed edges over :CALLS alone, still multi-hop; one
   hop happens only on an error path (an unsupported-feature error or a server-side timeout -> direct :CALLS
   callers; a transaction-memory error -> a 1-hop walk). Those facts are proven here on the REAL
   retrieval.query_impact over a scripted CALLS-only graph, then the docstring is checked against them.
C  D6 residual: the comment above `learning --enable`'s access-hook snippet said a matcher without
   MultiEdit|NotebookEdit "silently dropped" those strong labels. Claude Code reads a matcher holding any
   character beyond letters, digits, `_ - , |` and space as an UNANCHORED JavaScript regex
   (code.claude.com/docs/en/hooks: "`Edit.*` matches both `Edit` and `NotebookEdit`"), so the documented
   `Read|Edit|Write|.*deepmap_impact` already matches both through `Edit`. The comment is corrected; the
   printed snippet is unchanged (it still names both tools).
D  ISS-benchmark:012, the CLI surface (not in that record's provenance; O10/O11 fix the refusal and the flag):
   `deepmap tune --report` said "benchmark_evidence: none yet (run ... run_benchmark.py --ab-weights)", but
   `--ab-weights` only PRINTS the A/B plan; the evidence comes from benchmark/weight_ab.py's G4 procedure. The
   line now names that procedure — the very text `tune --apply`'s refusal uses (tune_weights._G4_PROCEDURE when
   the tree has it), with an absolute weight_ab.py path — never the plan-only flag.

Hermetic: a temp DEEPMAP_HOME and temp project dirs; neo4j.GraphDatabase.driver replaced by a factory (the
real driver is only CONSTRUCTED for the informational http:// probe — a neo4j driver is lazy, and any driver it
returns is closed unused); in A5 the real driver runs over a scripted socket.getaddrinfo
(it raises the chosen gaierror; socket connects raise too, so nothing can leave the machine); provision stubbed
(no MCP probe); the ML stack faked. No sockets, no DNS query, no Neo4j, no network, no spend.
Usage: python tests/o4_cli_honesty_r283_gates.py [--root <tree>]
"""
from __future__ import annotations

import argparse
import ast
import contextlib
import importlib.util
import io
import json
import os
import re
import socket
import sys
import tempfile
import time
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PASSES: list[str] = []
FAILS: list[str] = []

_SCRUB = ("NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD", "DEEPMAP_SEARCH_PORT", "DEEPMAP_EMBED_PORT",
          "DEEPMAP_LEARNING", "DEEPMAP_EXTRACTOR", "DEEPMAP_PROJECT_ROOT", "DEEPMAP_RETRIEVAL_LOG",
          "DEEPMAP_APPLY_LEARNED", "DEEPMAP_IMPACT_EDGES", "DEEPMAP_IMPACT_PROFILE", "DEEPMAP_IMPACT_MAX_HOPS",
          "DEEPMAP_IMPACT_TIMEOUT_S", "DEEPMAP_ENRICH", "DEEPMAP_MCP_HOST", "DEEPMAP_PYTHON", "DEEPMAP_HOOKS")
_HTTP_URI = "http://127.0.0.1:7474"            # the Neo4j Browser's address, mistyped as NEO4J_URI
_OWN_BOLT, _OWN_HTTP, _OWN_SEARCH, _OWN_EMBED = 17689, 17476, 18911, 18912


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSES if ok else FAILS).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok or not detail else f" — {str(detail)[:900]}"))


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@contextlib.contextmanager
def _env(**kv):
    saved = {k: os.environ.get(k) for k in kv}
    for k, v in kv.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@contextlib.contextmanager
def _chdir(d: Path):
    prev = os.getcwd()
    os.chdir(str(d))
    try:
        yield
    finally:
        os.chdir(prev)


@contextlib.contextmanager
def _fake_ml_stack():
    saved = {m: sys.modules.get(m) for m in ("torch", "sentence_transformers")}
    for m in saved:
        sys.modules[m] = types.ModuleType(m)
    try:
        yield
    finally:
        for m, v in saved.items():
            if v is None:
                sys.modules.pop(m, None)
            else:
                sys.modules[m] = v


@contextlib.contextmanager
def _neo4j_dist_version(version):
    """importlib.metadata.version('neo4j') answers `version` (None -> not installed); every other name is real."""
    import importlib.metadata as md
    real = md.version

    def fake(name):
        if str(name).strip().lower() == "neo4j":
            if version is None:
                raise md.PackageNotFoundError(name)
            return version
        return real(name)
    md.version = fake
    try:
        yield
    finally:
        md.version = real


# ---- fake Neo4j ----------------------------------------------------------------------------------------------
class _Result(list):
    def single(self):
        return self[0] if self else None


class _Session:
    def __init__(self, drv):
        self.drv = drv

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, q, **kw):
        text = str(getattr(q, "text", q))
        self.drv.queries.append(text)
        return _Result(self.drv.answer(text, kw))


class _Driver:
    """A fake driver: verify_connectivity raises `connect_exc` when given; sessions answer via `answer`."""

    def __init__(self, answer=None, connect_exc=None):
        self._answer, self.connect_exc = answer, connect_exc
        self.queries: list = []
        self.closed = False

    def answer(self, q, kw):
        return self._answer(q, kw) if self._answer is not None else []

    def verify_connectivity(self):
        if self.connect_exc is not None:
            raise self.connect_exc

    def session(self, **kw):
        return _Session(self)

    def close(self):
        self.closed = True


def _raising(exc):
    """A GraphDatabase.driver stand-in that raises `exc` at construction (where the real driver rejects a URI)."""
    calls: list = []

    def factory(uri, *a, **kw):
        calls.append(uri)
        raise exc
    factory.calls = calls
    return factory


@contextlib.contextmanager
def _patched_graphdb(factory):
    import neo4j
    raw = neo4j.GraphDatabase.__dict__["driver"]
    neo4j.GraphDatabase.driver = factory
    try:
        yield
    finally:
        setattr(neo4j.GraphDatabase, "driver", raw)


@contextlib.contextmanager
def _scripted_dns(errno: int):
    """For the block, socket.getaddrinfo raises gaierror(`errno`) for every name — what the resolver answers for an
    unknown host (EAI_NONAME), a temporary failure (EAI_AGAIN), a non-recoverable one (EAI_FAIL) — and any socket
    connect raises, so the REAL neo4j driver fails its resolve exactly as it would, with no DNS query and no socket."""
    had_connect = "connect" in socket.socket.__dict__
    real_gai, real_cc, real_connect = socket.getaddrinfo, socket.create_connection, socket.socket.connect

    def gai(*a, **k):
        raise socket.gaierror(errno, "getaddrinfo failed")

    def no_connect(*a, **k):
        raise AssertionError("gate: a socket connect was attempted")
    socket.getaddrinfo, socket.create_connection, socket.socket.connect = gai, no_connect, no_connect
    try:
        yield
    finally:
        socket.getaddrinfo, socket.create_connection = real_gai, real_cc
        if had_connect:
            socket.socket.connect = real_connect
        else:
            del socket.socket.connect


class _TextlessError(Exception):
    """An error whose text cannot be read (str() raises) — a diagnostic must still word it."""

    def __str__(self):
        raise RuntimeError("no text")


def _real_dns_error(uri: str, errno: int):
    """The exception the REAL neo4j driver raises from verify_connectivity() on `uri` when the resolver answers
    gaierror(`errno`) (scripted: _scripted_dns), or None when it raised nothing."""
    import neo4j
    with _scripted_dns(errno):
        try:
            drv = neo4j.GraphDatabase.driver(uri, auth=("neo4j", "x"))
        except Exception as e:  # noqa: BLE001
            return e
        try:
            drv.verify_connectivity()
        except Exception as e:  # noqa: BLE001
            return e
        finally:
            drv.close()
    return None


def _fake_provision():
    ns = types.SimpleNamespace()
    ns.MCP_SERVERS = {"deepmap-search": {"port_env": "DEEPMAP_SEARCH_PORT", "default_port": 8787},
                      "deepmap-embed": {"port_env": "DEEPMAP_EMBED_PORT", "default_port": 8788}}
    ns.mcp_host = lambda: "127.0.0.1"
    ns.mcp_port = lambda name: int(os.environ.get(ns.MCP_SERVERS[name]["port_env"],
                                                  ns.MCP_SERVERS[name]["default_port"]))
    ns.is_http_up = lambda host, port, timeout=2.0: False          # never a socket
    ns._ports_from_mcp_json = lambda root: None
    ns.isolation_note = lambda *a, **k: "isolation: (test stub)"
    ns.print_instance_table = lambda *a, **k: None
    ns._USER_SET_NEO4J_URI = False
    ns.neo4j_edition = lambda drv: "community"
    ns.paths = lambda *a, **k: {"data": Path("/nonexistent/deepmap/neo4j/data")}
    return ns


def _doctor(C, cwd: Path, *, factory=None, driver_fn=None):
    """(rc, stdout, stderr, raised) of an in-process C.cmd_doctor: GraphDatabase.driver = `factory` (or
    C._driver = `driver_fn`), provision stubbed, the ML stack faked."""
    orig_load, orig_driver = C._load, C._driver
    prov = _fake_provision()
    C._load = lambda name: prov if name == "provision" else orig_load(name)
    if driver_fn is not None:
        C._driver = driver_fn
    out, err = io.StringIO(), io.StringIO()
    rc, raised = None, None
    try:
        ctx = _patched_graphdb(factory) if factory is not None else contextlib.nullcontext()
        with ctx, _chdir(cwd), _fake_ml_stack(), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = C.cmd_doctor(argparse.Namespace())
    except BaseException as e:  # noqa: BLE001 — a diagnostic that crashes is the failure being gated
        raised = e
    finally:
        C._load, C._driver = orig_load, orig_driver
    return rc, out.getvalue(), err.getvalue(), raised


def _neo_fail_line(out: str) -> str:
    return next((ln for ln in out.splitlines() if "[FAIL] Neo4j —" in ln), "")


def _verdict_of(out: str) -> str:
    for ln in out.splitlines():
        if "[deepmap] verdict:" in ln:
            return ln.split("verdict:", 1)[1].strip()
    return ""


def _pinned_neo4j_spec(root: Path) -> str:
    """The neo4j range the tree's requirements.txt pins ('neo4j>=6.0.1,<7') — read independently of the CLI."""
    for raw in (root / "requirements.txt").read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*neo4j\s*((?:[<>=!~]=?\s*\d[^,\s]*\s*,?\s*)+)$", raw.split("#", 1)[0], re.IGNORECASE)
        if m:
            return "neo4j" + re.sub(r"\s+", "", m.group(1)).rstrip(",")
    return ""


def _write_registry(PR, root: Path) -> None:
    """This project's own instance (bolt 17689) + MCP pair in the temp DEEPMAP_HOME's registry."""
    ent = {"root": PR.project_identity(root), "bolt_port": _OWN_BOLT, "http_port": _OWN_HTTP,
           "search_port": _OWN_SEARCH, "embed_port": _OWN_EMBED}
    p = PR.registry_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"version": PR.REGISTRY_VERSION, "kind": PR.REGISTRY_KIND,
                             "projects": {PR.project_key(root): ent}}), encoding="utf-8")


# The stopped-instance wording + remedies the old fall-through printed (_down_remedy): an operator endpoint's "check
# that it is running", this project's "may be stopped or hibernated — revive it with <provision.py> heal ...".
# (A line that says healing would NOT help is fine — only prescribing it is the defect.)
_STALE_REMEDY = ("check that it is running", "stopped or hibernated", "revive it", 'provision.py" heal', "unreachable")


# =========================================================================== A: doctor's `driver` kind
def gate_a_doctor_driver(C, PR, root: Path, work: Path) -> None:
    print("\n-- A (ISS-build:025): doctor words a `driver`-kind failure with its error text and a fitting remedy --")
    import neo4j
    import neo4j.exceptions as NE
    spec = _pinned_neo4j_spec(root)
    install = f'-m pip install --upgrade "{spec}"'
    check("precondition: the tree's requirements.txt pins a neo4j range", bool(spec), spec)

    # A0 (2026-10-24): the http:// Neo4j Browser address is refused by DeepMap ITSELF — the one routing rule
    # (port_registry.neo4j_uri_problem, applied by resolve_neo4j_route), before any driver exists. What the installed
    # driver does with http:// is informational only: neo4j 6.2 raised ConfigurationError, 6.4 (inside the pinned
    # range) reads it as a preview HTTP Query API driver (ModuleNotFoundError 'aiohttp' without that package).
    rule = getattr(PR, "neo4j_uri_problem", None)
    why = rule(_HTTP_URI) if callable(rule) else None
    check("precondition: DeepMap's own rule rejects the http:// Browser address (port_registry.neo4j_uri_problem), "
          "naming NEO4J_URI, Bolt, bolt:// and /deep-map:setup",
          isinstance(why, str) and all(t in why for t in ("NEO4J_URI", "Bolt", "bolt://", "/deep-map:setup")), why)
    with _env(NEO4J_URI=_HTTP_URI):
        route = PR.resolve_neo4j_route(str(work))
    check("precondition: the routing rule turns it into a route problem in those words (never a verbatim env route)",
          route.uri is None and route.source == "none" and route.problem == why, repr(route))
    try:
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            drv = neo4j.GraphDatabase.driver(_HTTP_URI, auth=("neo4j", "x"))
            seen = f"builds {type(drv).__name__}"
            try:
                drv.close()                               # lazy: nothing was connected; never used
            except Exception:  # noqa: BLE001
                pass
    except BaseException as e:  # noqa: BLE001
        seen = f"raises {type(e).__name__}: {str(e)[:100]}"
    print(f"  [INFO] the installed neo4j driver on {_HTTP_URI} {seen} (informational — DeepMap rejects it first)")
    # main's wording of the 'driver' kind (a client-side DriverError — e.g. a user name inside a Bolt URI) is unchanged
    drv_exc = NE.ConfigurationError("Username is not supported in the URI")
    check("precondition: _classify_db_error calls a client-side ConfigurationError 'driver' (main words it a driver "
          "error)", C._classify_db_error(drv_exc) == "driver", C._classify_db_error(drv_exc))
    msg = C._db_error_message("search", drv_exc)
    check("main's message for that kind is unchanged: a driver error that sends the user to doctor",
          "Neo4j driver error" in msg and "/deep-map:doctor" in msg, msg)

    proj_env = work / "a_env"
    proj_env.mkdir()
    # A1: an operator NEO4J_URI mistyped as the Neo4j Browser's http:// address — DeepMap's own route problem, worded
    # by doctor's 'resolve' branch. GraphDatabase.driver RECORDS every call (_raising: its calls list is the proof —
    # the raise alone would be swallowed by doctor), so "no driver was built" holds whatever the installed driver
    # does with http://
    rec = _raising(NE.ConfigurationError("gate: GraphDatabase.driver must not be reached for an http:// NEO4J_URI"))
    with _env(NEO4J_URI=_HTTP_URI):
        rc, out, err, raised = _doctor(C, proj_env, factory=rec)
    ln = _neo_fail_line(out)
    check("A1 http:// NEO4J_URI: doctor does not crash, exits 1 (FAIL kept), verdict FAILED",
          raised is None and rc == 1 and _verdict_of(out) == "FAILED", f"{rc} {raised!r} {_verdict_of(out)}")
    check("A1: a [FAIL] Neo4j line", bool(ln), out[:900])
    check("A1: it carries DeepMap's own rejection — the rule's text (port_registry.neo4j_uri_problem)",
          bool(why) and why in ln, ln)
    check("A1: no driver was constructed for the http:// URI (GraphDatabase.driver never called)", rec.calls == [],
          rec.calls)
    check("A1: never worded as a driver error / 'unreachable' / 'check that it is running' / heal / aiohttp",
          "driver error" not in ln and "aiohttp" not in ln and not any(t in ln for t in _STALE_REMEDY), ln)
    check("A1: the remedy is runnable here — fix NEO4J_URI to a Bolt URI (bolt://...), or unset it and run setup",
          "NEO4J_URI" in ln and "bolt://" in ln and "/deep-map:setup" in ln, ln)
    check("A1: no driver install is prescribed (a typo is not a missing package)", "pip install" not in ln, ln)

    # A5 (round 3, docs[3]): an operator NEO4J_URI whose host does not resolve. The REAL driver picks the exception
    # by the resolver's errno: an unknown host (EAI_NONAME) — and a temporary DNS failure (EAI_AGAIN) — is a
    # ServiceUnavailable ('unreachable'); only another resolver error (EAI_FAIL, EAI_SERVICE, ...) is a ValueError
    # ('driver'). getaddrinfo is scripted (_scripted_dns): no DNS query, no socket.
    dns_uri, dns_text = "bolt://db.example.invalid:7687", "Failed to DNS resolve address db.example.invalid:7687"
    not_running = [t for t in _STALE_REMEDY if t != "unreachable"]
    nx = _real_dns_error(dns_uri, socket.EAI_NONAME)
    check("A5 premise: the real driver raises ServiceUnavailable for an unknown host (EAI_NONAME), with its DNS text "
          "— the CLI's 'unreachable' kind, not 'driver'",
          type(nx).__name__ == "ServiceUnavailable" and dns_text in str(nx) and C._classify_db_error(nx) == "unreachable",
          f"{nx!r} -> {C._classify_db_error(nx)}")
    again = _real_dns_error(dns_uri, socket.EAI_AGAIN)
    check("A5 premise: ... and for a temporary DNS failure (EAI_AGAIN) too",
          type(again).__name__ == "ServiceUnavailable" and C._classify_db_error(again) == "unreachable", repr(again))
    eai_fail = _real_dns_error(dns_uri, socket.EAI_FAIL)
    check("A5 premise: only another resolver error (EAI_FAIL) is a ValueError — the 'driver' kind",
          type(eai_fail).__name__ == "ValueError" and dns_text in str(eai_fail)
          and C._classify_db_error(eai_fail) == "driver", f"{eai_fail!r} -> {C._classify_db_error(eai_fail)}")

    with _env(NEO4J_URI=dns_uri), _scripted_dns(socket.EAI_NONAME):
        rc, out, err, raised = _doctor(C, proj_env)                       # the real driver, no factory
    ln = _neo_fail_line(out)
    check("A5 unknown NEO4J_URI host: FAIL + exit 1, worded 'unreachable (ServiceUnavailable) at <uri>' — never a "
          "driver error", raised is None and rc == 1 and f"unreachable (ServiceUnavailable) at {dns_uri}" in ln
          and "driver error" not in ln, f"{rc} {raised!r} {ln}")
    check("A5: doctor's line shows the driver's DNS error text (it used to drop it)", dns_text in ln, ln)
    check("A5: the remedy is the NEO4J_URI host name (it does not resolve) — not 'check that it is running' / heal",
          "does not resolve" in ln and "NEO4J_URI" in ln and not any(t in ln for t in not_running), ln)
    with _env(NEO4J_URI=dns_uri):
        msg = C._db_error_message("search", nx)
    check("A5 main's message: 'is not reachable' with the DNS error and the same host-name remedy (not 'check that it "
          "is running')", "is not reachable" in msg and dns_text in msg and "does not resolve" in msg
          and "NEO4J_URI" in msg and not any(t in msg for t in not_running), msg)

    with _env(NEO4J_URI=dns_uri), _scripted_dns(socket.EAI_FAIL):
        rc, out, err, raised = _doctor(C, proj_env)
    ln = _neo_fail_line(out)
    check("A5b another resolver error (EAI_FAIL -> ValueError): FAIL + exit 1, a driver error with the DNS text",
          raised is None and rc == 1 and "driver error" in ln and dns_text in ln, f"{rc} {raised!r} {ln}")
    check("A5b: its remedy names NEO4J_URI (a host that resolves), not 'check that it is running'",
          "NEO4J_URI" in ln and "resolves" in ln and not any(t in ln for t in _STALE_REMEDY), ln)

    refused_op = NE.ServiceUnavailable("Couldn't connect to db.example.invalid:7687 (resolved to ('10.0.0.5:7687',)): "
                                       "Failed to establish connection (reason [WinError 10061] No connection could "
                                       "be made because the target machine actively refused it)")
    refused_op.__cause__ = ConnectionRefusedError(10061, "actively refused")
    with _env(NEO4J_URI=dns_uri):
        rc, out, err, raised = _doctor(C, proj_env, factory=lambda uri, *a, **k: _Driver(connect_exc=refused_op))
    ln = _neo_fail_line(out)
    check("A5 control: a REFUSED operator endpoint keeps 'unreachable' + 'check that it is running' (no DNS remedy)",
          raised is None and rc == 1 and "unreachable (ServiceUnavailable)" in ln and "check that it is running" in ln
          and "does not resolve" not in ln, f"{rc} {ln}")
    down = getattr(C, "_down_remedy", None)
    try:
        odd = [down(x) for x in (None, object(), _TextlessError(), nx)] if callable(down) else []
        ok = bool(odd) and all(isinstance(s, str) and s.strip() for s in odd)
    except TypeError:          # a _down_remedy that takes no error (the pre-round-3 signature) cannot word DNS
        ok, odd = False, "_down_remedy(exc) is not accepted"
    except Exception as e:  # noqa: BLE001
        ok, odd = False, repr(e)
    check("A5: _down_remedy(exc) never raises (odd inputs, an unprintable error)", ok, odd)

    rem = " ".join((getattr(getattr(C, "_driver_remedy", None), "__doc__", None) or "").split())
    check("A5 docstring: _driver_remedy no longer lists 'a host that does not resolve' as a driver-kind cause",
          bool(rem) and "a host that does not resolve" not in rem, rem[:600])
    check("A5 docstring: _driver_remedy names the DNS failure it does cover (a ValueError) and says an unknown host is "
          "ServiceUnavailable — the 'unreachable' kind",
          "ValueError" in rem and "ServiceUnavailable" in rem and "unreachable" in rem, rem[:600])
    md = " ".join((root / "commands" / "doctor.md").read_text(encoding="utf-8").split())
    m = re.search(r"driver error talking to[^.]*?client-side failure(.*?)Nothing showed", md)
    causes = m.group(1) if m else ""
    check("A5 doctor.md: the 'driver error' paragraph no longer lists a host that doesn't resolve among its causes",
          bool(m) and re.search(r"does(?:n't| not) resolve", causes) is None, causes or md[:400])
    check("A5 doctor.md: says a NEO4J_URI host that doesn't resolve is reported unreachable (ServiceUnavailable), with "
          "the driver's DNS text and a check-the-host remedy",
          re.search(r"doesn't resolve[^.]*\*\*unreachable \(ServiceUnavailable\)\*\*", md) is not None
          and "Failed to DNS resolve address" in md, md[md.find("client-side failure"):][:900])

    # This project's OWN instance route (registry, bolt 17689): DeepMap generated the URI, so a driver-kind
    # failure is the driver package's — never "stopped or hibernated — revive it with heal".
    proj = work / "a_own"
    proj.mkdir()
    _write_registry(PR, proj)
    old = NE.ConfigurationError("Unexpected config keys: notifications_min_severity")
    with _neo4j_dist_version("5.4.0"):
        rc, out, err, raised = _doctor(C, proj, factory=_raising(old))
    ln = _neo_fail_line(out)
    check("A2 own route, neo4j 5.4.0 installed (outside the pin): FAIL + exit 1, the error text shown",
          raised is None and rc == 1 and "Unexpected config keys: notifications_min_severity" in ln, f"{rc} {ln}")
    check("A2: never the stopped-instance remedy (heal / stopped or hibernated / unreachable)",
          not any(t in ln for t in _STALE_REMEDY), ln)
    check("A2: names the installed 5.4.0 as outside the supported range and gives the pinned --upgrade install",
          "5.4.0" in ln and "outside" in ln and install in ln, ln)

    other = NE.DriverError("a client-side failure the driver raised")
    with _neo4j_dist_version("6.2.0"):
        rc, out, err, raised = _doctor(C, proj, factory=_raising(other))
    ln = _neo_fail_line(out)
    check("A3 own route, neo4j 6.2.0 (inside the pin), another DriverError: FAIL + exit 1, the error text shown",
          raised is None and rc == 1 and "a client-side failure the driver raised" in ln, f"{rc} {ln}")
    check("A3: says the version is inside the range (no reinstall prescribed) and never the heal remedy",
          "inside the range" in ln and "pip install" not in ln and not any(t in ln for t in _STALE_REMEDY), ln)

    with _neo4j_dist_version(None):
        rc, out, err, raised = _doctor(C, proj, factory=_raising(other))
    ln = _neo_fail_line(out)
    check("A3b own route, the driver version unreadable: the pinned install, offered conditionally",
          raised is None and rc == 1 and install in ln and "if " in ln
          and not any(t in ln for t in _STALE_REMEDY), f"{rc} {ln}")

    # A4: the neo4j package itself does not import (a ModuleNotFoundError out of _driver)
    def _no_neo4j():
        raise ModuleNotFoundError("No module named 'neo4j'", name="neo4j")
    check("precondition: a missing neo4j package is the 'driver' kind",
          C._classify_db_error(ModuleNotFoundError("No module named 'neo4j'", name="neo4j")) == "driver")
    rc, out, err, raised = _doctor(C, proj, driver_fn=_no_neo4j)
    ln = _neo_fail_line(out)
    check("A4 neo4j not importable: FAIL + exit 1 with 'No module named 'neo4j''",
          raised is None and rc == 1 and "No module named 'neo4j'" in ln, f"{rc} {raised!r} {ln}")
    check("A4: the remedy is the pinned driver install into DeepMap's Python, not heal",
          install in ln and not any(t in ln for t in _STALE_REMEDY), ln)

    # A6: the other kinds are unchanged — a REFUSED connection on this project's own instance keeps the
    # unreachable wording and the heal remedy (it IS a stopped instance).
    refused = NE.ServiceUnavailable(f"Couldn't connect to 127.0.0.1:{_OWN_BOLT} (resolved to "
                                    f"('127.0.0.1:{_OWN_BOLT}',)): Failed to establish connection (reason "
                                    "[WinError 10061] No connection could be made because the target machine "
                                    "actively refused it)")
    refused.__cause__ = ConnectionRefusedError(10061, "actively refused")
    rc, out, err, raised = _doctor(C, proj, factory=lambda uri, *a, **k: _Driver(connect_exc=refused))
    ln = _neo_fail_line(out)
    check("A6 control: a refused own instance stays 'unreachable' with the heal remedy, exit 1",
          raised is None and rc == 1 and "unreachable" in ln and "heal" in ln and "driver error" not in ln,
          f"{rc} {ln}")

    # A7: a diagnostic never crashes — a driver-kind error whose text itself can't be read
    class _Unprintable(Exception):
        def __str__(self):
            raise RuntimeError("no text")
    rc, out, err, raised = _doctor(C, proj, factory=_raising(_Unprintable()))
    check("A7 a driver-kind error whose str() raises: doctor still reports FAIL and exits 1 (no traceback)",
          raised is None and rc == 1 and bool(_neo_fail_line(out)), f"{rc} {raised!r} {out[:400]}")
    fn = getattr(C, "_driver_remedy", None)
    ok = callable(fn)
    if ok:
        saved = C._current_route
        try:
            C._current_route = lambda: (_ for _ in ()).throw(RuntimeError("route"))
            outs = [fn(x) for x in (None, object(), _Unprintable(), ValueError("x"))]
            ok = all(isinstance(s, str) and s.strip() for s in outs)
        except Exception as e:  # noqa: BLE001
            ok, outs = False, repr(e)
        finally:
            C._current_route = saved
    check("A7: deepmap_cli._driver_remedy exists and never raises (odd inputs, a failing route lookup)", ok)
    try:
        PR.registry_path().unlink()
    except OSError:
        pass


# =========================================================================== B: cmd_impact's docstring vs query_impact
def gate_b_impact_docstring(root: Path) -> None:
    print("\n-- B (ISS-build:018): cmd_impact's docstring matches what query_impact does on a CALLS-only graph --")
    import neo4j.exceptions as NE
    R = _load(root / "build" / "retrieval.py", f"o4_retrieval_{time.time_ns()}")
    row = {"caller": "c1", "caller_type": "Function", "file": "m.py", "line": 3, "confidence": "INFERRED",
           "callee": "target", "hop": 2, "via": "CALLS", "via_path": ["CALLS", "CALLS"]}

    def scripted(walk=None, retry=None):
        state = {"walks": 0}

        def answer(q, kw):
            if "AS matched" in q:
                return [{"matched": 1}]
            if "db.relationshipTypes" in q:
                return [{"types": ["CALLS"]}]                # a pre-Track-C graph: CALLS only
            if "MATCH path" in q:
                state["walks"] += 1
                exc = walk if state["walks"] == 1 else retry
                if exc is not None:
                    raise exc
                return [dict(row)]
            if "MATCH (caller)-[r:CALLS]->(callee)" in q:
                return [dict(row, hop=1, via_path=["CALLS"])]
            return []
        return _Driver(answer=answer)

    d = scripted()
    got = R.query_impact(d, "target")
    walks = [q for q in d.queries if "MATCH path" in q]
    check("fact: a CALLS-only graph is walked over :CALLS alone, MULTI-hop (rels:CALLS*1..3, max_hops 3)",
          len(walks) == 1 and "[rels:CALLS*1..3]" in walks[0] and getattr(got, "max_hops", None) == 3
          and not any("1 hop" in n or "1-hop" in n for n in getattr(got, "notes", [])),
          f"{walks} {getattr(got, 'max_hops', None)} {getattr(got, 'notes', None)}")
    unsupported = NE.Neo4jError._hydrate_neo4j(code="Neo.ClientError.Statement.SyntaxError", message="Invalid input")
    d = scripted(walk=unsupported)
    got = R.query_impact(d, "target")
    check("fact: an unsupported-feature error on the walk -> direct :CALLS callers (1 hop) + a note",
          any("MATCH (caller)-[r:CALLS]->(callee)" in q for q in d.queries) and getattr(got, "max_hops", None) == 1
          and any("1 hop" in n for n in getattr(got, "notes", [])), f"{getattr(got, 'notes', None)}")
    mem = NE.Neo4jError._hydrate_neo4j(code="Neo.TransientError.General.MemoryPoolOutOfMemoryError",
                                       message="dbms.memory.transaction.total.max threshold reached")
    d = scripted(walk=mem)
    got = R.query_impact(d, "target")
    walks = [q for q in d.queries if "MATCH path" in q]
    check("fact: a transaction-memory error on the deep walk -> a 1-hop walk (rels:CALLS*1..1) + a note",
          len(walks) == 2 and "[rels:CALLS*1..1]" in walks[1] and getattr(got, "max_hops", None) == 1
          and any("transaction memory" in n for n in getattr(got, "notes", [])), f"{walks[-1:]}")

    tree = ast.parse((root / "build" / "deepmap_cli.py").read_text(encoding="utf-8"))
    fn = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "cmd_impact"), None)
    doc = " ".join((ast.get_docstring(fn) or "").split()) if fn is not None else ""
    sents = [s for s in re.split(r"(?<=[.!?])\s+", doc) if s]
    one_hop = re.compile(r"\b(?:one|1)[- ]hop\b", re.IGNORECASE)
    pre = [s for s in sents if re.search(r"pre-Track-C|without typed edges|no typed edges", s, re.IGNORECASE)]
    check("docstring: still says what happens on a graph without typed edges (pre-Track-C)", bool(pre), doc)
    check("docstring: no sentence about that graph claims a one-hop walk (it stays multi-hop)",
          bool(pre) and not any(one_hop.search(s) for s in pre), pre)
    check("docstring: ... and says the walk there stays multi-hop over :CALLS",
          any("multi-hop" in s and "CALLS" in s for s in pre), pre)
    err_paths = [s for s in sents if one_hop.search(s)]
    check("docstring: one hop is tied to the error paths — unsupported-feature, timeout, transaction-memory",
          any("unsupported-feature" in s and "timeout" in s and re.search(r"transaction[- ]memory", s)
              for s in err_paths), err_paths)


# =========================================================================== C: the access-hook matcher comment
def _cc_matches(matcher: str, tool: str) -> bool:
    """Claude Code's documented matcher semantics (code.claude.com/docs/en/hooks): '', '*' -> all; only letters,
    digits, `_ - , |` and spaces -> an exact name or a list of exact names split on `|` / `,`; anything else -> an
    UNANCHORED JavaScript regex (RegExp.test). Python's re.search is the same test for these patterns."""
    if matcher in ("", "*"):
        return True
    if re.fullmatch(r"[A-Za-z0-9_\-, |]+", matcher):
        return tool in {p.strip() for p in re.split(r"[|,]", matcher) if p.strip()}
    return re.search(matcher, tool) is not None


def gate_c_matcher_comment(C, root: Path, work: Path) -> None:
    print("\n-- C (D6 residual): the learning snippet's comment states the real matcher semantics --")
    src = (root / "build" / "deepmap_cli.py").read_text(encoding="utf-8")
    fn = next((n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "cmd_learning"), None)
    lines = src.splitlines()[fn.lineno - 1:fn.end_lineno] if fn is not None else []
    comment = " ".join(ln.strip().lstrip("#").strip() for ln in lines if ln.strip().startswith("#"))
    check("comment: no longer claims a matcher without MultiEdit|NotebookEdit 'silently dropped' labels",
          bool(lines) and "silently dropped" not in comment, comment[:600])
    check("comment: says the matcher is an unanchored regex (so `Edit` already matches both tools)",
          "unanchored" in comment.lower() and "MultiEdit" in comment and "NotebookEdit" in comment, comment[:600])

    proj = work / "c_learn"
    proj.mkdir()
    out = io.StringIO()
    with _env(DEEPMAP_LEARNING=None), _chdir(proj), contextlib.redirect_stdout(out), \
            contextlib.redirect_stderr(io.StringIO()):
        rc = C.cmd_learning(argparse.Namespace(enable=True, disable=False, status=False))
    text = out.getvalue()
    try:
        matcher = json.loads(text[text.index("{"):text.rindex("}") + 1])["hooks"]["PostToolUse"][0]["matcher"]
    except Exception:  # noqa: BLE001
        matcher = ""
    printed = "Read|Edit|Write|.*deepmap_impact|MultiEdit|NotebookEdit"
    check("snippet unchanged: learning --enable still prints the matcher naming MultiEdit and NotebookEdit",
          rc == 0 and matcher == printed, f"{rc} {matcher!r}")

    graded = ("Read", "Edit", "Write", "MultiEdit", "NotebookEdit", "mcp__deepmap-search__deepmap_impact",
              "mcp__plugin_deepmap_deepmap-search__deepmap_impact")
    documented = "Read|Edit|Write|.*deepmap_impact"   # hooks/deepmap_access_hook.py + docs/RM1-ENABLE.md
    check("semantics: the printed matcher selects every tool the hook grades",
          all(_cc_matches(printed, t) for t in graded), [t for t in graded if not _cc_matches(printed, t)])
    check("semantics: so does the shorter documented matcher — `Edit` matches MultiEdit and NotebookEdit (no drop)",
          all(_cc_matches(documented, t) for t in graded), [t for t in graded if not _cc_matches(documented, t)])
    check("semantics (control): without a regex character the matcher is an exact list — `Read|Edit|Write` misses them",
          not _cc_matches("Read|Edit|Write", "MultiEdit") and not _cc_matches("Read|Edit|Write", "NotebookEdit"))
    hook = _load(root / "hooks" / "deepmap_access_hook.py", f"o4_access_hook_{time.time_ns()}")
    kinds = {t: hook._action_kind(hook._tool_base(t)) for t in ("Read", "MultiEdit", "NotebookEdit")}
    check("the hook grades Read weak and MultiEdit/NotebookEdit as strong edits (what the comment says)",
          kinds == {"Read": "read", "MultiEdit": "edit", "NotebookEdit": "edit"}, kinds)


# =========================================================================== D: tune --report's evidence hint
def gate_d_tune_report_hint(C, root: Path, work: Path) -> None:
    print("\n-- D (ISS-benchmark:012, CLI surface): tune --report names the real G4 procedure, not --ab-weights --")
    proj = work / "d_tune"
    (proj / ".deepmap").mkdir(parents=True)
    (proj / ".deepmap" / "instance.json").write_text(json.dumps({"learned_fusion": {
        "arm_weights": {"vector": 1.2, "bm25": 0.9, "graph_weighted": 0.9}, "applied": False,
        "beats_equal": True, "significant": True, "objective": "ndcg", "reason": "learned_beats_equal",
        "n_labeled_queries": 200}}), encoding="utf-8")                  # a tune result, no benchmark_evidence
    out = io.StringIO()
    with _env(DEEPMAP_LEARNING=None), contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        rc = C.cmd_tune(argparse.Namespace(root=str(proj), status=False, report=True, apply=False,
                                           export_prior=False, min_queries=None, min_queries_bucket=None,
                                           dry_run=False))
    ln = next((x for x in out.getvalue().splitlines() if "benchmark_evidence:" in x), "")
    wab = (root / "benchmark" / "weight_ab.py").as_posix()
    check("tune --report runs (exit 0) and prints a benchmark_evidence line", rc == 0 and bool(ln), f"{rc} {ln!r}")
    check("... that never sends the operator to the plan-only run_benchmark.py --ab-weights",
          bool(ln) and "--ab-weights" not in ln and "run_benchmark.py" not in ln, ln)
    check("... and names the G4 procedure in benchmark/weight_ab.py by its absolute path", f'"{wab}"' in ln, ln)
    tw = _load(root / "build" / "tune_weights.py", f"o4_tw_{time.time_ns()}")
    proc = getattr(tw, "_G4_PROCEDURE", None)
    if isinstance(proc, str) and proc.strip():
        check("... in the very words tune --apply's refusal uses (tune_weights._G4_PROCEDURE)", proc in ln, ln)


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
    root = REPO
    if "--root" in sys.argv:
        root = Path(sys.argv[sys.argv.index("--root") + 1]).resolve()
    print("=" * 72)
    print(f"DeepMap — O4 CLI honesty: doctor's driver kind, impact's one-hop claim, the matcher comment, tune "
          f"--report's evidence hint (root: {root})")
    print("=" * 72)
    saved_env = {k: os.environ.get(k) for k in _SCRUB + ("DEEPMAP_HOME",)}
    for k in _SCRUB:
        os.environ.pop(k, None)
    with tempfile.TemporaryDirectory() as td:
        base = Path(td).resolve()
        home, work = base / "home", base / "work"
        home.mkdir()
        work.mkdir()
        os.environ["DEEPMAP_HOME"] = str(home)
        try:
            sys.path.insert(0, str(root / "build"))
            C = _load(root / "build" / "deepmap_cli.py", f"o4_cli_{time.time_ns()}")
            import port_registry as PR
            for gate in (lambda: gate_a_doctor_driver(C, PR, root, work),
                         lambda: gate_b_impact_docstring(root),
                         lambda: gate_c_matcher_comment(C, root, work),
                         lambda: gate_d_tune_report_hint(C, root, work)):
                try:
                    gate()
                except Exception as exc:  # noqa: BLE001 — a crashing gate is a RED gate
                    import traceback
                    traceback.print_exc()
                    check(f"gate ran without crashing ({type(exc).__name__}: {exc})", False)
        finally:
            for k, v in saved_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
    total = len(PASSES) + len(FAILS)
    print("\n" + "=" * 72)
    if FAILS:
        print(f"RESULT: RED — {len(PASSES)}/{total} gates passed.")
        return 1
    print(f"RESULT: GREEN — {len(PASSES)}/{total} gates passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
