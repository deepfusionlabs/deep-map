#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NEO4J_URI scheme rule (2026-10-24) — DeepMap itself decides which NEO4J_URI it dials: Bolt only. RED-first.

Found by the Linux CI-like suite: the neo4j Python driver 6.4.1 — inside requirements.txt's `neo4j>=6.0.1,<7`, and
what CI resolves — reads http:// and https:// as a PREVIEW HTTP Query API driver that needs aiohttp. An operator
NEO4J_URI mistyped as the Neo4j Browser's address (http://127.0.0.1:7474) then raised ModuleNotFoundError('aiohttp')
at driver construction instead of 6.2.0's ConfigurationError (a missing-package remedy for a typo), and with aiohttp
installed it would silently have run an HTTP driver DeepMap was never validated on. The scheme is now DeepMap's
decision, in the ONE routing rule (KL-architecture:002):

A  the rule — port_registry.neo4j_uri_problem(uri): the truth table. Accepted: the six Bolt schemes (bolt, bolt+s,
   bolt+ssc, neo4j, neo4j+s, neo4j+ssc) in any case, with or without a port, a port 0-65535 (0 is provision's
   fail-closed "no route" sentinel), a path / query, a user name inside (still the driver's own ConfigurationError,
   which the CLI words masked — F1), unset / empty (= unset: the routing goes on). Rejected, each with one line that
   names NEO4J_URI, Bolt, a copy-pasteable bolt:// fix and /deep-map:setup: http / https (any case — the Browser's
   7474 / 7473 mapped to Bolt 7687, a REGISTERED instance's http port to its own bolt port, an Aura https host to
   neo4j+s://), any other scheme (ws, bolt+tls, bolt+routing, jdbc...), no scheme at all (host:port, bolt:host),
   blank, whitespace or control characters anywhere (a trailing space after `set NEO4J_URI=...`), a port that is not
   0-65535 ASCII digits. Deterministic across driver AND Python versions (the driver accepts surrounding spaces and
   only fails a bad port when it connects; urllib's port parsing differs between 3.10 and 3.11+). A password inside a
   rejected value is never echoed — one holding '@' included: the echo is masked up to the value's LAST '@'.
   Never raises.
B  the routing rule — resolve_neo4j_route's operator branch: a rejected NEO4J_URI is a route PROBLEM (uri None,
   source "none", kind "uri", the rule's text) — never a verbatim "env" route; an accepted one stays verbatim;
   honor_env=False (provision, the injector) never sees it; neo4j_uri_for raises Neo4jUriError (a
   RegistryUnreadableError, so every existing `except` still catches it). The rule is applied with the env the route
   was asked about (its registry maps a registered instance's http port to that instance's bolt port).
C  every consumer shows the rule's text and NEVER constructs a driver (a recorder seam on
   neo4j.GraphDatabase.driver — never a raising one: the CLI swallows exceptions): the CLI's doctor (FAIL, exit 1,
   verdict FAILED, never "driver error" / "unreachable" / heal), status / search / impact / cypher (exit 3, "nothing
   was queried"), sync (exit 1, sync's own "cannot sync" line); sync._resolve_route; provision's apply_ports_for in
   escape-hatch mode (neo4j_route_problem, source "none", idempotent, the operator's env left as it was; a
   whitespace-padded value is judged as set, never stripped — resolve_neo4j_route's own problem) and its
   setup (FAILED, nothing started) / ensure / status / stop / mcp-start / heal / neo4j-upgrade; the hooks'
   bolt_target (silence, no probe); auto_sync's route. C5: the isolation line of `provision.py status` and the CLI's
   doctor says "isolation: none — the NEO4J_URI set for this run is refused (see above)" next to the refusal, never
   "OPERATOR-MANAGED ... overrides DeepMap's per-project routing" (an accepted operator URI still is).
D  one rule: the CLI's, sync's and provision's texts ARE neo4j_uri_problem's; resolve_neo4j_route and provision call
   it (AST); the accepted set IS the real driver's own Bolt set (read from its error for a bogus scheme), and the
   installed driver's handling of http:// is printed for information only.

Hermetic: a temp DEEPMAP_HOME and temp projects; DEEPMAP_SEARCH_PORT / DEEPMAP_EMBED_PORT set (provision never
allocates MCP ports); neo4j.GraphDatabase.driver and provision's lifecycle seams are recorders; the ML stack faked.
No socket, no Neo4j, no network, no spend. The real driver is only CONSTRUCTED (lazy — nothing connects) for the
accepted URIs in D and for the informational http:// probe.
Usage: python tests/neo4j_uri_scheme_gates.py [--root <tree>]
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
import sys
import tempfile
import time
import types
import warnings
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PASSES: list[str] = []
FAILS: list[str] = []

_SCRUB = ("NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD", "DEEPMAP_SEARCH_PORT", "DEEPMAP_EMBED_PORT", "DEEPMAP_LEARNING",
          "DEEPMAP_EXTRACTOR", "DEEPMAP_PROJECT_ROOT", "DEEPMAP_PROJECT_KEY", "DEEPMAP_RETRIEVAL_LOG",
          "DEEPMAP_APPLY_LEARNED", "DEEPMAP_IMPACT_EDGES", "DEEPMAP_IMPACT_PROFILE", "DEEPMAP_MCP_HOST", "DEEPMAP_PYTHON",
          "DEEPMAP_HOOKS", "DEEPMAP_NO_BASE_ADOPT", "DEEPMAP_AUTO_SYNC", "NEO4J_VERSION", "DEEPMAP_MANAGED_VENV")
_HTTP = "http://127.0.0.1:7474"
_PW = "S3cr3t-Pw9"
_OWN_BOLT, _OWN_HTTP = 17689, 17476
_BOLT_SIX = {"bolt", "bolt+s", "bolt+ssc", "neo4j", "neo4j+s", "neo4j+ssc"}
_STALE = ("driver error", "unreachable", "check that it is running", "stopped or hibernated", "revive it",
          "pip install", "aiohttp")

ACCEPTED = (
    "bolt://127.0.0.1:7687", "bolt://db.example.invalid", "BOLT://DB.example.invalid:7687", "Bolt+S://h.example:7687",
    "bolt+ssc://h.example:1", "neo4j://h.example:7687", "neo4j+s://abc123.databases.neo4j.io", "NEO4J+SSC://h.example",
    "bolt://[::1]:7687", "bolt://[::1]", "bolt://127.0.0.1:0", "bolt://h.example:65535", "bolt://h.example:",
    "neo4j://db.example.invalid:7687?policy=eu", "bolt://h.example:7687/", f"bolt://neo4j:{_PW}@127.0.0.1:7687",
    "neo4j+s://Tok9Z@db.example.invalid:7687?x=a@b",
    # an '@' leaves the port to the driver: where the userinfo ends is ambiguous (F1), so 'b:9z' is never read as a port
    "neo4j+s://Tok9Z@db.example.invalid:7687?x=a@b:9z",
)
# (value, what the line must say about it: substrings, all required)
REJECTED = (
    (_HTTP, ("Browser", "bolt://127.0.0.1:7687")),
    ("http://localhost:7474/browser/", ("Browser", "bolt://localhost:7687")),
    ("HTTP://127.0.0.1:7474", ("Browser", "bolt://127.0.0.1:7687")),
    ("http://db.example.invalid", ("bolt://db.example.invalid",)),
    ("http://db.example.invalid:8080", ("bolt://db.example.invalid:7687",)),
    ("https://127.0.0.1:7473", ("HTTPS", "neo4j+s://127.0.0.1:7687")),
    ("Https://abc123.databases.neo4j.io", ("HTTPS", "neo4j+s://abc123.databases.neo4j.io")),
    ("ws://h.example:7687", ("'ws'", "bolt://h.example:7687")),
    ("bolt+tls://h.example", ("'bolt+tls'", "bolt://h.example")),
    ("bolt+routing://h.example:7687", ("'bolt+routing'", "bolt://h.example:7687")),
    ("jdbc:neo4j:bolt://h.example:7687", ("bolt://",)),
    ("127.0.0.1:7687", ("no scheme", "bolt://127.0.0.1:7687")),
    ("localhost:7687", ("no scheme", "bolt://localhost:7687")),
    ("127.0.0.1:7474", ("no scheme", "bolt://127.0.0.1:7687")),
    ("db.example.invalid", ("no scheme", "bolt://db.example.invalid")),
    ("bolt:127.0.0.1:7687", ("no scheme", "bolt://127.0.0.1:7687")),
    ("bolt:/127.0.0.1:7687", ("no scheme", "bolt://127.0.0.1:7687")),
    ("   ", ("blank",)),
    ("\t\n", ("blank",)),
    (" bolt://127.0.0.1:7687", ("spaces", "NEO4J_URI=bolt://127.0.0.1:7687")),
    ("bolt://127.0.0.1:7687 ", ("spaces", "NEO4J_URI=bolt://127.0.0.1:7687")),
    ("bolt://127.0.0.1:7687\n", ("spaces", "NEO4J_URI=bolt://127.0.0.1:7687")),
    ("bolt://my host:7687", ("spaces",)),
    ("bolt://h.example\x00:7687", ("spaces",)),
    (" http://127.0.0.1:7474 ", ("Browser", "bolt://127.0.0.1:7687")),
    ("bolt://h.example:99999", ("port", "bolt://h.example:7687")),
    ("bolt://h.example:65536", ("port",)),
    ("bolt://h.example:abc", ("port",)),
    ("bolt://h.example:-1", ("port",)),
    ("neo4j+s://h.example:\uff17\uff16\uff18\uff17", ("port", "neo4j+s://h.example:7687")),
    ("bolt://[::1]:x", ("port",)),
)


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSES if ok else FAILS).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok or not detail else f" — {str(detail)[:900]}"))


def info(text: str) -> None:
    print(f"  [INFO] {text}")


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
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
def _attrs(obj, **kv):
    missing = object()
    saved = {k: getattr(obj, k, missing) for k in kv}
    for k, v in kv.items():
        setattr(obj, k, v)
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is missing:
                try:
                    delattr(obj, k)
                except AttributeError:
                    pass
            else:
                setattr(obj, k, v)


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


class _FakeDriver:
    def verify_connectivity(self):
        raise AssertionError("gate: a driver built for a rejected NEO4J_URI was used")

    def session(self, **kw):
        raise AssertionError("gate: a driver built for a rejected NEO4J_URI was used")

    def close(self):
        pass


class _Recorder:
    """A RECORDER seam (never a raising one — the CLI and provision swallow exceptions): records every call and
    returns `ret` (a callable is called with the arguments)."""

    def __init__(self, ret=None):
        self.calls: list = []
        self.ret = ret

    def __call__(self, *a, **k):
        self.calls.append((a, k))
        return self.ret(*a, **k) if callable(self.ret) else self.ret


@contextlib.contextmanager
def _driver_recorder():
    """neo4j.GraphDatabase.driver replaced by a recorder (returns a fake driver that fails loudly if used)."""
    import neo4j
    raw = neo4j.GraphDatabase.__dict__["driver"]
    rec = _Recorder(lambda *a, **k: _FakeDriver())
    neo4j.GraphDatabase.driver = rec
    try:
        yield rec
    finally:
        setattr(neo4j.GraphDatabase, "driver", raw)


def _capture(fn, *a, **k):
    """(result, stdout, stderr, raised) of fn(*a, **k)."""
    out, err = io.StringIO(), io.StringIO()
    res, raised = None, None
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            res = fn(*a, **k)
    except BaseException as e:  # noqa: BLE001 — a consumer that raises is the failure being gated
        raised = e
    return res, out.getvalue(), err.getvalue(), raised


def _mk_project(work: Path, name: str) -> Path:
    p = work / name
    p.mkdir(parents=True)
    (p / "app.py").write_text("def handler(x):\n    return x + 1\n", encoding="utf-8")
    return p


def _rule(PR, uri, env=None):
    fn = getattr(PR, "neo4j_uri_problem", None)
    if not callable(fn):
        return "<port_registry.neo4j_uri_problem is missing>"
    return fn(uri) if env is None else fn(uri, env)


# =========================================================================== A: the rule's truth table
def gate_a_truth_table(PR, home: Path, work: Path) -> None:
    print("\n-- A: port_registry.neo4j_uri_problem — DeepMap's own NEO4J_URI scheme rule (truth table) --")
    fn = getattr(PR, "neo4j_uri_problem", None)
    check("port_registry.neo4j_uri_problem exists", callable(fn))
    if not callable(fn):
        return
    check("the accepted schemes are exactly the six Bolt schemes (port_registry.BOLT_URI_SCHEMES)",
          set(getattr(PR, "BOLT_URI_SCHEMES", ()) or ()) == _BOLT_SIX, getattr(PR, "BOLT_URI_SCHEMES", None))
    bad = [(u, fn(u)) for u in ACCEPTED if fn(u) is not None]
    check(f"accepted: {len(ACCEPTED)} Bolt URIs (any case, with/without port, port 0 and 65535, IPv6, path, query, "
          "a user name inside — the driver's own F1 error) -> None", not bad, bad)
    check("unset (None) and empty ('' — the routing rule's 'unset') -> None", fn(None) is None and fn("") is None)
    for value, needs in REJECTED:
        msg = fn(value)
        ok = (isinstance(msg, str) and "NEO4J_URI" in msg and "Bolt" in msg and "/deep-map:setup" in msg
              and re.search(r"(?:bolt|neo4j)(?:\+s|\+ssc)?://", msg) is not None
              and all(n in msg for n in needs) and "\n" not in msg and len(msg) <= 700)
        check(f"rejected {value!r}: one line naming NEO4J_URI, Bolt, a Bolt-scheme fix ({', '.join(needs)}) and "
              "/deep-map:setup", ok, msg)
    shown = fn("bolt://127.0.0.1:7687\n") or ""
    check("whitespace is SHOWN escaped in the quoted value (a trailing newline is visible: '\\n')", "\\n" in shown, shown)
    for value in (f"http://neo4j:{_PW}@127.0.0.1:7474", f"neo4j:{_PW}@127.0.0.1:7687", f"ws://u:{_PW}@h.example:1",
                  f" http://neo4j:{_PW}@h.example:7474", f"http://neo4j:{_PW}/x@h.example:7474",
                  f"bolt://neo4j:{_PW}@my host:7687", f"u:{_PW}@x://h.example", f"ftp://h/{_PW}@x"):
        msg = fn(value)
        check(f"a password inside a rejected value is never echoed: {value.replace(_PW, '<pw>')!r}",
              isinstance(msg, str) and _PW not in msg and "Pw9" not in msg, msg)
    # a password that itself holds '@': the echo hides everything up to the value's LAST '@' (masking to the FIRST '@'
    # would print the password's tail — 'ss' here — which the _PW checks above can't see)
    for value, echo, tail in (("http://neo4j:p@ss@db.example.invalid:7474", "'http://***@db.example.invalid:7474'", "ss@"),
                              (f"http://neo4j:Ab@{_PW}@127.0.0.1:7474", "'http://***@127.0.0.1:7474'", "Pw9"),
                              ("neo4j:p@ss@127.0.0.1:7687", "'***@127.0.0.1:7687'", "ss@"),
                              ("ws://u:p@s@s@h.example:1", "'ws://***@h.example:1'", "s@s")):
        msg = fn(value)
        check(f"a password holding '@' is masked up to the LAST '@': {value.replace(_PW, '<pw>')!r} is echoed as {echo}",
              isinstance(msg, str) and echo in msg and tail not in msg and "***@" in msg, msg)
    long = "http://" + "a" * 5000 + ".example:7474"
    msg = fn(long)
    check("a huge value is rejected with a bounded line (no 5000-char echo)", isinstance(msg, str) and len(msg) < 900,
          len(msg or ""))

    class _Textless(str):
        def __str__(self):
            raise RuntimeError("no text")
    odd = []
    for v in (object(), 7687, b"bolt://127.0.0.1:7687", _Textless("http://x"), ["bolt://h"]):
        try:
            r = fn(v)
            odd.append(r is None or isinstance(r, str))
        except Exception as e:  # noqa: BLE001
            odd.append(f"raised {e!r}")
    check("never raises on odd inputs (an object, an int, bytes, an unprintable str, a list)",
          all(x is True for x in odd), odd)
    msg = fn(_Textless("bolt://127.0.0.1:7687"))
    check("a value that can't be read as text is a problem (fail closed), never accepted",
          isinstance(msg, str) and "NEO4J_URI" in msg and "Bolt" in msg, msg)

    # a REGISTERED instance's http port maps to ITS bolt port (this project's own instance, typed from the Browser)
    reg = {"version": PR.REGISTRY_VERSION, "kind": PR.REGISTRY_KIND,
           "projects": {"k1": {"root": str(work / "p1"), "bolt_port": _OWN_BOLT, "http_port": _OWN_HTTP,
                               "search_port": 18911, "embed_port": 18912}}}
    PR.registry_path().write_text(json.dumps(reg), encoding="utf-8")
    try:
        msg = fn(f"http://127.0.0.1:{_OWN_HTTP}")
        check(f"http://127.0.0.1:{_OWN_HTTP} (a registered instance's http port) -> its own bolt://127.0.0.1:{_OWN_BOLT}",
              isinstance(msg, str) and f"bolt://127.0.0.1:{_OWN_BOLT}" in msg, msg)
        msg = fn(f"http://127.0.0.1:{_OWN_HTTP}", {"DEEPMAP_HOME": str(home / "elsewhere")})
        check("... read from the registry of the env given (another DEEPMAP_HOME has no such instance: 7687)",
              isinstance(msg, str) and "bolt://127.0.0.1:7687" in msg and str(_OWN_BOLT) not in msg, msg)
        msg = fn(f"http://db.example.invalid:{_OWN_HTTP}")
        check("... only for this machine: a remote host's same port is not mapped to a local instance's",
              isinstance(msg, str) and str(_OWN_BOLT) not in msg and "bolt://db.example.invalid:7687" in msg, msg)
    finally:
        PR.registry_path().unlink()


# =========================================================================== B: the routing rule
def gate_b_routing(PR, home: Path, work: Path) -> None:
    print("\n-- B: resolve_neo4j_route's operator branch applies the rule (a problem, never a verbatim env route) --")
    proj = _mk_project(work, "b_proj")
    base = {"DEEPMAP_HOME": str(home)}
    for value, _needs in REJECTED[:3] + (("127.0.0.1:7687", ()), ("   ", ()), ("bolt://h.example:99999", ())):
        r = PR.resolve_neo4j_route(str(proj), dict(base, NEO4J_URI=value))
        want = _rule(PR, value, dict(base, NEO4J_URI=value))
        check(f"NEO4J_URI={value!r}: a route problem (uri None, source 'none', kind 'uri') with the rule's text",
              r.uri is None and r.source == "none" and getattr(r, "kind", None) == "uri" and r.problem == want
              and not r.ok, repr(r))
    for value in ("bolt://db.example.invalid:9999", "neo4j+s://abc123.databases.neo4j.io", "BOLT://H.example"):
        r = PR.resolve_neo4j_route(str(proj), dict(base, NEO4J_URI=value))
        check(f"NEO4J_URI={value!r}: still the verbatim operator route ('env', no problem)",
              tuple(r) == (value, "env", None), repr(r))
    r = PR.resolve_neo4j_route(str(proj), dict(base, NEO4J_URI=""))
    check("NEO4J_URI='' is unset: the project's own route (here legacy 7687 — no registry), not a problem",
          r.source == "legacy-unverified" and r.problem is None, repr(r))
    r = PR.resolve_neo4j_route(str(proj), dict(base, NEO4J_URI=_HTTP), honor_env=False)
    check("honor_env=False (provision, the injector) never reads NEO4J_URI — no problem from it",
          r.source == "legacy-unverified" and r.problem is None, repr(r))
    exc_cls = getattr(PR, "Neo4jUriError", None)
    check("port_registry.Neo4jUriError exists and is a RegistryUnreadableError (every existing except catches it)",
          isinstance(exc_cls, type) and issubclass(exc_cls, PR.RegistryUnreadableError)
          and not issubclass(exc_cls, PR.NoProjectInstanceError))
    try:
        PR.neo4j_uri_for(str(proj), dict(base, NEO4J_URI=_HTTP))
        got = None
    except Exception as e:  # noqa: BLE001
        got = e
    check("neo4j_uri_for raises Neo4jUriError with the rule's text for a rejected NEO4J_URI",
          exc_cls is not None and isinstance(got, exc_cls) and str(got) == _rule(PR, _HTTP), repr(got))
    check("neo4j_uri_for still returns an accepted operator URI verbatim",
          PR.neo4j_uri_for(str(proj), dict(base, NEO4J_URI="bolt://db.example.invalid:9999"))
          == "bolt://db.example.invalid:9999")

    # the rule is applied with the env the route was ASKED about: its registry (DEEPMAP_HOME) maps a registered
    # instance's http port to its own bolt port — this process's DEEPMAP_HOME has no such instance
    other_home = home.parent / "home_b_env"
    other_home.mkdir(exist_ok=True)
    other = {"DEEPMAP_HOME": str(other_home)}
    reg = {"version": PR.REGISTRY_VERSION, "kind": PR.REGISTRY_KIND,
           "projects": {"kb": {"root": str(work / "pb"), "bolt_port": _OWN_BOLT, "http_port": _OWN_HTTP,
                               "search_port": 18921, "embed_port": 18922}}}
    PR.registry_path(other).write_text(json.dumps(reg), encoding="utf-8")
    value = f"http://127.0.0.1:{_OWN_HTTP}"
    try:
        r = PR.resolve_neo4j_route(str(proj), dict(other, NEO4J_URI=value))
        check(f"NEO4J_URI={value!r} with an env whose registry holds that instance: the route problem's fix is "
              f"ITS own bolt://127.0.0.1:{_OWN_BOLT} (the rule read the env's registry, not this process's)",
              r.uri is None and getattr(r, "kind", None) == "uri" and isinstance(r.problem, str)
              and f"bolt://127.0.0.1:{_OWN_BOLT}" in r.problem
              and r.problem == _rule(PR, value, dict(other, NEO4J_URI=value)), repr(r))
        own_env = _rule(PR, value)
        check("control: this process's own DEEPMAP_HOME holds no such instance (the env-less rule says 7687)",
              isinstance(own_env, str) and str(_OWN_BOLT) not in own_env and "bolt://127.0.0.1:7687" in own_env,
              own_env)
    finally:
        PR.registry_path(other).unlink()


# =========================================================================== C1: the CLI
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
    ns._USER_SET_NEO4J_URI = True
    ns.neo4j_edition = lambda drv: "community"
    ns.paths = lambda *a, **k: {"data": Path("/nonexistent/deepmap/neo4j/data")}
    return ns


def _cli_doctor(C, cwd: Path, prov=None):
    orig = C._load
    prov = prov if prov is not None else _fake_provision()
    C._load = lambda name: prov if name == "provision" else orig(name)
    try:
        with _chdir(cwd), _fake_ml_stack():
            return _capture(C.cmd_doctor, argparse.Namespace())
    finally:
        C._load = orig


def _cli_main(C, cwd: Path, argv: list):
    with _chdir(cwd), _fake_ml_stack(), _attrs(sys, argv=["deepmap_cli.py"] + argv):
        return _capture(C.main)


def gate_c1_cli(C, PR, work: Path) -> None:
    print("\n-- C1: the CLI words the rule's text and never builds a driver for a rejected NEO4J_URI --")
    proj = _mk_project(work, "c1_proj")
    for value in (_HTTP, "https://abc123.databases.neo4j.io", "127.0.0.1:7687", "bolt://127.0.0.1:7687 "):
        want = _rule(PR, value)
        with _env(NEO4J_URI=value), _driver_recorder() as rec:
            rc, out, err, raised = _cli_doctor(C, proj)
        ln = next((x for x in out.splitlines() if "[FAIL] Neo4j" in x), "")
        verdict = next((x.split("verdict:", 1)[1].strip() for x in out.splitlines() if "[deepmap] verdict:" in x), "")
        check(f"doctor, NEO4J_URI={value!r}: exit 1, verdict FAILED, no traceback",
              raised is None and rc == 1 and verdict == "FAILED", f"{rc} {raised!r} {verdict!r}")
        check(f"doctor, NEO4J_URI={value!r}: the FAIL line IS the rule's text (DeepMap's own rejection)",
              bool(want) and want in ln, ln)
        check(f"doctor, NEO4J_URI={value!r}: never 'driver error' / 'unreachable' / heal / pip / aiohttp",
              bool(ln) and not any(t in ln for t in _STALE), ln)
        check(f"doctor, NEO4J_URI={value!r}: no driver was constructed (recorder empty)", rec.calls == [], rec.calls)
    with _env(NEO4J_URI=f"http://neo4j:{_PW}@127.0.0.1:7474"), _driver_recorder() as rec:
        rc, out, err, raised = _cli_doctor(C, proj)
    check("doctor with a password inside an http:// NEO4J_URI: the password is never printed",
          raised is None and rc == 1 and _PW not in out + err and rec.calls == [], (out + err)[:600])

    for argv, label in ((["status"], "status"), (["search", "parse the config file"], "search"),
                        (["impact", "handler"], "impact"), (["cypher", "MATCH (n) RETURN count(n) AS c"], "cypher")):
        with _env(NEO4J_URI=_HTTP), _driver_recorder() as rec:
            rc, out, err, raised = _cli_main(C, proj, argv)
        want = _rule(PR, _HTTP)
        check(f"`deepmap {label}` with NEO4J_URI={_HTTP}: exit 3, the rule's text on stderr, 'nothing was queried'",
              raised is None and rc == 3 and bool(want) and want in err and "nothing was queried" in err,
              f"{rc} {raised!r} {err[:700]}")
        check(f"`deepmap {label}`: never a driver error / aiohttp / pip remedy, and no driver constructed",
              not any(t in err for t in ("driver error", "aiohttp", "pip install", "Traceback")) and rec.calls == [],
              f"{rec.calls} {err[:400]}")


# =========================================================================== C2: sync
def gate_c2_sync(PR, root: Path, work: Path) -> None:
    print("\n-- C2: sync refuses before anything is locked or written, in the rule's words --")
    proj = _mk_project(work, "c2_proj")
    S = _load(root / "build" / "sync.py", f"uri_sync_{time.time_ns()}")
    with _env(NEO4J_URI=_HTTP):
        route = S._resolve_route(proj)
    check("sync._resolve_route: (None, False, the rule's text)",
          tuple(route) == (None, False, _rule(PR, _HTTP)), repr(tuple(route)))
    with _env(NEO4J_URI=_HTTP), _driver_recorder() as rec:
        ok, out, err, raised = _capture(S.sync, str(proj))
    check("sync.sync: returns False (refused), no traceback, sync's 'cannot sync' line carries the rule's text",
          raised is None and ok is False and "cannot sync" in err and _rule(PR, _HTTP) in err,
          f"{ok!r} {raised!r} {err[:600]}")
    check("sync.sync: no driver constructed, nothing written under the project's .deepmap",
          rec.calls == [] and not (proj / ".deepmap" / "graph_build.json").exists(), rec.calls)


def gate_c2b_cli_sync(C, PR, work: Path) -> None:
    proj = _mk_project(work, "c2b_proj")
    with _env(NEO4J_URI=_HTTP), _driver_recorder() as rec:
        rc, out, err, raised = _cli_main(C, proj, ["sync", str(proj)])
    check("`deepmap sync` with NEO4J_URI=http://...: exit 1 (refused), the rule's text, no driver",
          raised is None and rc == 1 and _rule(PR, _HTTP) in err and rec.calls == [], f"{rc} {raised!r} {err[:500]}")


# =========================================================================== C3: provision
def gate_c3_provision(P, PR, work: Path) -> None:
    print("\n-- C3: provision (the injector) applies the same rule in escape-hatch mode — every verb refuses --")
    proj = _mk_project(work, "c3_proj")
    want = _rule(PR, _HTTP)
    seams = {n: _Recorder(False) for n in ("ensure_neo4j", "start_mcp_servers", "stop_neo4j", "write_mcp_json",
                                           "start_mcp_server", "_make_driver", "is_bolt_up", "verify_search",
                                           "recover_neo4j_upgrade", "upgrade_neo4j")
             if hasattr(P, n)}
    seams["_bolt_state"] = _Recorder("down")
    with _env(NEO4J_URI=_HTTP, DEEPMAP_SEARCH_PORT="18911", DEEPMAP_EMBED_PORT="18912"), \
            _attrs(P, _USER_SET_NEO4J_URI=True, **seams), _driver_recorder() as rec:
        _r, _o, _e, raised = _capture(P.apply_ports_for, str(proj), allocate=True)
        check("apply_ports_for (escape hatch, NEO4J_URI=http://...): neo4j_route_problem() IS the rule's text",
              raised is None and P.neo4j_route_problem() == want, f"{raised!r} {P.neo4j_route_problem()!r}")
        check("... neo4j_route_source() is 'none' (no route), not 'env'", P.neo4j_route_source() == "none",
              P.neo4j_route_source())
        check("... the operator's NEO4J_URI is left as it was (never rewritten)", os.environ.get("NEO4J_URI") == _HTTP,
              os.environ.get("NEO4J_URI"))
        _capture(P.apply_ports_for, str(proj), allocate=False)
        check("... and a second resolution in the same process says the same (idempotent)",
              P.neo4j_route_problem() == want and P.neo4j_route_source() == "none", P.neo4j_route_problem())

        ok, out, err, raised = _capture(P.setup, str(proj))
        check("setup: FAILED (False), '[provision] ERROR: <the rule's text>', no traceback",
              raised is None and ok is False and f"ERROR: {want}" in err and "setup verdict: FAILED" in err,
              f"{ok!r} {raised!r} {err[:700]}")
        vline = next((x for x in err.splitlines() if "setup verdict: FAILED" in x), "")
        check("setup's verdict line names NEO4J_URI (not 'ports could not be allocated') and says nothing was started",
              "NEO4J_URI" in vline and "ports could not be allocated" not in vline and "nothing was started" in vline,
              vline)
        rc, out, err, raised = _capture(P.main, ["ensure", str(proj)])
        check("ensure: exit 1 with the rule's text", raised is None and rc == 1 and want in err, f"{rc} {err[:400]}")
        rc, out, err, raised = _capture(P.main, ["status", str(proj)])
        check("status: STATUS_UNAVAILABLE_RC with 'DeepMap status: <the rule's text>'",
              raised is None and rc == P.STATUS_UNAVAILABLE_RC and f"DeepMap status: {want}" in out,
              f"{rc} {raised!r} {out[:500]}")
        iso = next((x.strip() for x in out.splitlines() if x.strip().startswith("isolation:")), "")
        check("status: the isolation line says the refused NEO4J_URI routes this run nowhere ('isolation: none — the "
              "NEO4J_URI set for this run is refused'), never 'OPERATOR-MANAGED ... overrides'",
              iso.startswith(_ISO_REFUSED) and "OPERATOR-MANAGED" not in out and "overrides" not in out, iso or out[:600])
        rc, out, err, raised = _capture(P.main, ["stop", str(proj)])
        check("stop: exit 1, '<the rule's text> — nothing to stop'", raised is None and rc == 1
              and f"{want} — nothing to stop" in out, f"{rc} {out[:400]}")
        rc, out, err, raised = _capture(P.main, ["mcp-start", str(proj)])
        check("mcp-start: exit 1, 'MCP servers NOT started — <the rule's text>'", raised is None and rc == 1
              and f"MCP servers NOT started — {want}" in err, f"{rc} {err[:400]}")
        rc, out, err, raised = _capture(P.main, ["neo4j-upgrade", str(proj)])
        check("neo4j-upgrade: exit 1, '<the rule's text> — nothing upgraded'", raised is None and rc == 1
              and f"{want} — nothing upgraded" in out, f"{rc} {out[:400]}")
        report: dict = {}
        ok, out, err, raised = _capture(P.heal, str(proj), report=report)
        check("heal: False, report['reason'] IS the rule's text, 'nothing was started'",
              raised is None and ok is False and report.get("reason") == want and "nothing was started" in err,
              f"{ok!r} {raised!r} {report.get('reason')!r}")
        started = {n: s.calls for n, s in seams.items() if s.calls and n != "_bolt_state"}
        check("no Neo4j started / stopped / probed, no MCP server started, no .mcp.json written, no driver built",
              not started and rec.calls == [], f"{started} {rec.calls}")
    with _env(NEO4J_URI="bolt://db.example.invalid:9999", DEEPMAP_SEARCH_PORT="18911", DEEPMAP_EMBED_PORT="18912"), \
            _attrs(P, _USER_SET_NEO4J_URI=True):
        _capture(P.apply_ports_for, str(proj), allocate=False)
        check("control: an accepted operator URI keeps the escape hatch (source 'env', no problem, env untouched)",
              P.neo4j_route_problem() is None and P.neo4j_route_source() == "env"
              and os.environ.get("NEO4J_URI") == "bolt://db.example.invalid:9999",
              f"{P.neo4j_route_problem()!r} {P.neo4j_route_source()!r}")

    # the escape hatch judges the operator's value AS SET — never a strip()ped copy: a padded Bolt URI is refused with
    # the very problem resolve_neo4j_route gives for it (the CLI / sync / hooks refuse it, so provision must too)
    for value in (" bolt://127.0.0.1:7687 ", "bolt://127.0.0.1:7687 ", "\tbolt://db.example.invalid:9999"):
        route = PR.resolve_neo4j_route(str(proj), {"DEEPMAP_HOME": os.environ["DEEPMAP_HOME"], "NEO4J_URI": value})
        with _env(NEO4J_URI=value, DEEPMAP_SEARCH_PORT="18911", DEEPMAP_EMBED_PORT="18912"), \
                _attrs(P, _USER_SET_NEO4J_URI=True, **{n: _Recorder(s.ret) for n, s in seams.items()}), \
                _driver_recorder() as rec:
            _r, _o, _e, raised = _capture(P.apply_ports_for, str(proj), allocate=True)
            got = (P.neo4j_route_problem(), P.neo4j_route_source(), os.environ.get("NEO4J_URI"))
        check(f"apply_ports_for (escape hatch, NEO4J_URI={value!r}): refused with resolve_neo4j_route's own problem "
              "(the value as set, not stripped), source 'none', the env left as it was, no driver",
              raised is None and getattr(route, "kind", None) == "uri" and isinstance(route.problem, str)
              and "spaces" in route.problem and got == (route.problem, "none", value) and rec.calls == [],
              f"{raised!r} {got!r} {route!r}")


_ISO_REFUSED = "isolation: none — the NEO4J_URI set for this run is refused (see above)"


# =========================================================================== C5: the isolation line
def gate_c5_isolation_line(C, P, PR, work: Path) -> None:
    print("\n-- C5: a REFUSED NEO4J_URI is not an operator-managed route: the isolation line says so --")
    stats = {"n_projects": 2, "projects": [{"root": "/a", "neo4j_uri": "bolt://127.0.0.1:7689"}, {"root": "/b"}]}
    try:
        note = P.isolation_note(None, stats, escape_hatch=True, uri_refused=True)
    except TypeError as e:          # a provision without the keyword: RED, not a crash
        note = f"<isolation_note has no uri_refused: {e}>"
    check("isolation_note(uri_refused=True): 'isolation: none — the NEO4J_URI set for this run is refused (see "
          "above)', never OPERATOR-MANAGED / 'overrides'",
          note.startswith(_ISO_REFUSED) and "OPERATOR-MANAGED" not in note and "overrides" not in note, note)
    esc = P.isolation_note(None, stats, escape_hatch=True)
    check("control: an accepted operator NEO4J_URI (uri_refused unset) is still OPERATOR-MANAGED",
          esc.startswith("isolation: OPERATOR-MANAGED") and not esc.startswith("isolation: none"), esc)
    proj = _mk_project(work, "c5_proj")
    for value, refused in ((_HTTP, True), ("127.0.0.1:7687", True), ("bolt://127.0.0.1:7687 ", True),
                           ("bolt://db.example.invalid:9999", False)):
        prov = _fake_provision()
        prov.isolation_note = P.isolation_note          # the REAL wording, the CLI's real call
        with _env(NEO4J_URI=value), _driver_recorder():
            rc, out, err, raised = _cli_doctor(C, proj, prov)
        iso = next((x.strip() for x in out.splitlines() if x.strip().startswith("isolation:")), "")
        if refused:
            check(f"doctor, refused NEO4J_URI={value!r}: the isolation line is '{_ISO_REFUSED}' — never "
                  "'OPERATOR-MANAGED ... overrides DeepMap's per-project routing' next to the refusal",
                  raised is None and iso.startswith(_ISO_REFUSED) and "OPERATOR-MANAGED" not in out
                  and "overrides" not in out, iso or out[-700:])
        else:
            check(f"doctor, control: an accepted operator NEO4J_URI={value!r} keeps 'isolation: OPERATOR-MANAGED'",
                  raised is None and iso.startswith("isolation: OPERATOR-MANAGED"), iso or out[-700:])


# =========================================================================== C4: hooks + auto_sync
def gate_c4_hooks(PR, root: Path, work: Path) -> None:
    print("\n-- C4: the hooks and the background refresh read the same route (silence / skip, never a probe) --")
    proj = _mk_project(work, "c4_proj")
    H = _load(root / "hooks" / "deepmap_hook_state.py", f"uri_hook_state_{time.time_ns()}")
    with _env(NEO4J_URI=_HTTP):
        tgt = H.bolt_target(str(proj))
    check("deepmap_hook_state.bolt_target: None for a rejected NEO4J_URI (no liveness probe)", tgt is None, tgt)
    with _env(NEO4J_URI="http://127.0.0.1:7687"):
        tgt = H.bolt_target(str(proj))
    check("... even an http:// URI on a loopback Bolt PORT is never probed (it would have been before)", tgt is None, tgt)
    with _env(NEO4J_URI="bolt://127.0.0.1:7687"):
        tgt = H.bolt_target(str(proj))
    check("control: a loopback Bolt operator URI is probed as before", tgt == ("127.0.0.1", 7687), tgt)
    path = root / "build" / "auto_sync.py"
    if path.is_file():
        A = _load(path, f"uri_auto_sync_{time.time_ns()}")
        with _env(NEO4J_URI=_HTTP):
            r = A._route(str(proj))
        check("auto_sync._route: the same problem route (its runner skips a route with a problem)",
              getattr(r, "problem", None) == _rule(PR, _HTTP) and getattr(r, "uri", "x") is None, repr(r))


# =========================================================================== D: one rule
def _calls(tree, func_name: str) -> set:
    fn = next((n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == func_name), None)
    out = set()
    for n in ast.walk(fn) if fn is not None else ():
        if isinstance(n, ast.Call):
            f = n.func
            out.add(f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None))
    return out


def gate_d_one_rule(PR, root: Path) -> None:
    print("\n-- D: one rule in one module (KL-architecture:002), and it agrees with the real driver --")
    pr_tree = ast.parse((root / "build" / "port_registry.py").read_text(encoding="utf-8"))
    check("resolve_neo4j_route calls neo4j_uri_problem (the operator branch applies the rule)",
          "neo4j_uri_problem" in _calls(pr_tree, "resolve_neo4j_route"))
    pv_tree = ast.parse((root / "build" / "provision.py").read_text(encoding="utf-8"))
    check("provision._apply_neo4j_env calls port_registry's neo4j_uri_problem (no copy of the rule in provision)",
          "neo4j_uri_problem" in _calls(pv_tree, "_apply_neo4j_env"))
    copies = []
    for f in sorted((root / "build").glob("*.py")) + sorted((root / "hooks").glob("*.py")):
        if f.name == "port_registry.py":
            continue
        txt = f.read_text(encoding="utf-8", errors="replace")
        if re.search(r"""["']bolt\+ssc["']\s*,\s*["']neo4j["']|\{\s*["']bolt["']\s*,\s*["']bolt\+s["']""", txt):
            copies.append(f.name)
    check("no other module carries its own list of accepted Bolt schemes", not copies, copies)

    import neo4j
    try:
        neo4j.GraphDatabase.driver("deepmap-bogus-scheme://h.example:7687", auth=("neo4j", "x")).close()
        listed = None
    except Exception as e:  # noqa: BLE001
        m = re.search(r"Supported URI schemes are \[([^\]]*)\]", str(e))
        listed = set(re.findall(r"'([^']+)'", m.group(1))) if m else None
    check("the real driver's own Bolt schemes (its error for a bogus scheme, minus http/https) ARE DeepMap's set",
          listed is not None and listed - {"http", "https"} == set(getattr(PR, "BOLT_URI_SCHEMES", ()) or ()),
          listed)
    built = []
    for u in ("bolt://127.0.0.1:7687", "BOLT://DB.example.invalid:7687", "neo4j+s://abc123.databases.neo4j.io",
              "bolt+ssc://h.example:1", "bolt://[::1]:7687", "bolt://127.0.0.1:0"):
        try:
            d = neo4j.GraphDatabase.driver(u, auth=("neo4j", "x"))     # lazy: nothing connects
            built.append(type(d).__name__)
            d.close()
        except Exception as e:  # noqa: BLE001
            built.append(f"{u}: {type(e).__name__}")
    check("every accepted URI builds a BOLT driver in the installed driver (BoltDriver / Neo4jDriver)",
          all(b in ("BoltDriver", "Neo4jDriver") for b in built), built)
    try:
        from importlib.metadata import version as _v
        ver = _v("neo4j")
    except Exception:  # noqa: BLE001
        ver = "?"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            d = neo4j.GraphDatabase.driver(_HTTP, auth=("neo4j", "x"))
            what = f"builds {type(d).__name__} (the preview HTTP Query API driver)"
            d.close()
        except BaseException as e:  # noqa: BLE001
            what = f"raises {type(e).__name__}: {str(e)[:120]}"
    info(f"neo4j {ver} on {_HTTP} {what} — DeepMap no longer depends on it: the rule rejects it first")


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
    print(f"DeepMap — NEO4J_URI scheme rule: Bolt only, decided by DeepMap in the one routing rule (root: {root})")
    print("=" * 72)
    saved_env = {k: os.environ.get(k) for k in _SCRUB + ("DEEPMAP_HOME",)}
    for k in _SCRUB:
        os.environ.pop(k, None)
    with tempfile.TemporaryDirectory(prefix="dm_uri_") as td:
        base = Path(td).resolve()
        home, work = base / "home", base / "work"
        home.mkdir()
        work.mkdir()
        os.environ["DEEPMAP_HOME"] = str(home)
        os.environ["DEEPMAP_MANAGED_VENV"] = "0"
        try:
            sys.path.insert(0, str(root / "build"))
            PR = _load(root / "build" / "port_registry.py", "port_registry")
            C = _load(root / "build" / "deepmap_cli.py", f"uri_cli_{time.time_ns()}")
            P = _load(root / "build" / "provision.py", f"uri_provision_{time.time_ns()}")
            for gate in (lambda: gate_a_truth_table(PR, home, work),
                         lambda: gate_b_routing(PR, home, work),
                         lambda: gate_c1_cli(C, PR, work),
                         lambda: gate_c2_sync(PR, root, work),
                         lambda: gate_c2b_cli_sync(C, PR, work),
                         lambda: gate_c3_provision(P, PR, work),
                         lambda: gate_c5_isolation_line(C, P, PR, work),
                         lambda: gate_c4_hooks(PR, root, work),
                         lambda: gate_d_one_rule(PR, root)):
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
