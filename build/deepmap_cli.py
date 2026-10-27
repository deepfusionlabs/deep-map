#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DeepMap CLI — use the product directly (no MCP transport needed).

A thin command-line front end over the validated modules, so DeepMap is usable now even
where the MCP Python SDK's stdio transport misbehaves (a Windows/anyio quirk independent of
DeepMap). Mirrors the /deep-map commands.

    python build/deepmap_cli.py sync   <dir> [--stale-cleanup] [--switch-project] [--force-prune]  # build/refresh
    python build/deepmap_cli.py search "<query>" [--top-k N]     # fused hybrid search
    python build/deepmap_cli.py impact "<symbol>" [--limit N] [--max-hops N] [--edges T,...]  # what reaches it (multi-hop + edge confidence)
    python build/deepmap_cli.py cypher "<read query>" [--params JSON] [--limit N] [--json]  # read-only Cypher (deepmap_cypher's twin)
    python build/deepmap_cli.py status                           # node/edge/index/embed census
    python build/deepmap_cli.py doctor                           # health check (Neo4j/indexes/MCP/ML)
    python build/deepmap_cli.py version                          # print the version (from the manifest)
    python build/deepmap_cli.py learning [--enable|--disable|--status]  # one-command enable of the opt-in learning loop + accrual
    python build/deepmap_cli.py tune [<root>] [--status|--report|--apply|--dry-run|...]  # offline weight tuner (opt-in learning)
    python build/deepmap_cli.py insights [<root>] [--json] [--since <iso>]  # local read-only summary of the opt-in retrieval log
    python build/deepmap_cli.py enrich [<root>] [--status|--dry-run|--deterministic|...]  # opt-in summary enrichment

Env: NEO4J_URI (unset: this project's own instance, bolt://127.0.0.1:<its port>; set: an operator endpoint used
verbatim), NEO4J_USER (neo4j), NEO4J_PASSWORD (deepmap), DEEPMAP_GRAPHIFY_TIMEOUT (seconds: the graphify engine's
time budget for sync; default max(600, 1.5 s per source file), capped at 4 h; 60-86400).

`sync` exit codes: 0 synced (READY, or DEGRADED with this run's graph pushed); 1 refused / failed / verdict FAILED
(no usable graph from this run); 3 Neo4j unreachable; 4 NOT PUSHED (the sync ran but pushed nothing: the previous
graph is kept unchanged and may be STALE, e.g. graphify ran out of time and the engine-downgrade guard refused the
push — raise DEEPMAP_GRAPHIFY_TIMEOUT and re-sync).

`enrich` exits 4 when it REFUSED because a sync (or another enrich) of the project holds its lock: nothing was
extracted, sent, spent or written (see `enrich --help`).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import os
import re
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import mcp_bounds  # noqa: E402 — search/impact count bounds, shared with the MCP server (one source of truth)
import cypher_readonly  # noqa: E402 — `cypher`'s validator, caps and result shape, shared with deepmap_cypher (stdlib only)
logging.getLogger("neo4j").setLevel(logging.ERROR)


def _utf8():
    """UTF-8 stdout/stderr, LINE-BUFFERED: under a pipe (the Bash tool, a script) a block-buffered stdout
    interleaves out of order with stderr notes. Never raises."""
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", line_buffering=True)
        except Exception:
            try:
                s.reconfigure(encoding="utf-8")
            except Exception:
                pass


def _python_interpreter() -> str:
    """The interpreter DeepMap should NAME when it emits a command for the user to run later (the
    printed access-hook snippet, doctor hints). Resolution: DEEPMAP_PYTHON (explicit override) >
    sys.executable (the interpreter already running this CLI — it demonstrably exists and matches the
    user's environment) > "python" (last-resort literal). We never blindly rewrite python -> python3:
    Windows ships python.exe, not python3.exe, so a hardcoded python3 would break the emitted hook."""
    override = os.environ.get("DEEPMAP_PYTHON", "").strip()
    if override:
        return override
    exe = sys.executable
    return exe if exe else "python"


def _interp() -> str:
    """_python_interpreter() with forward slashes (safe inside a quoted, copy-pasteable command)."""
    return _python_interpreter().replace("\\", "/")


def _cli_cmd(sub: str) -> str:
    """A copy-pasteable command for THIS CLI (absolute path + the running interpreter) — hints must be
    runnable from the user's project, where a relative `python build/...` path does not exist."""
    return f'"{_interp()}" "{(HERE / "deepmap_cli.py").as_posix()}" {sub}'


def _q_root(sub: str, root) -> str:
    """`<sub> "<root>"` — an offline subcommand aimed at an explicit project root, so the hint runs the
    same from any directory. Never raises."""
    try:
        return f'{sub} "{Path(root).as_posix()}"'
    except Exception:
        return sub


def _provision_cmd(sub: str) -> str:
    """Like _cli_cmd, for build/provision.py (heal / status verbs)."""
    interp = _python_interpreter().replace("\\", "/")
    return f'"{interp}" "{(HERE / "provision.py").as_posix()}" {sub}'


_ROOT_CACHE: dict = {}


def _project_root() -> Path:
    """The project this invocation is about, resolved from the cwd (retrieval_log.project_root — the ONE
    root rule sync._sync_root, the hooks and provision's MCP-server root also use): inside a git work tree
    the OUTERMOST ancestor up to the toplevel holding a sync manifest (.deepmap/graph_build.json) — the
    root sync itself syncs — else the cwd (where a sync run here would write); outside git the cwd (a
    non-git directory is its own project, as for sync and the Neo4j routing). So every subcommand run
    from a SUBDIRECTORY of a synced project reads/writes the same .deepmap/ state (learning flag,
    retrieval log, sidecar, manifest) the sync and the MCP servers use; the Neo4j routing keys on the git
    identity either way. One resolution per cwd per invocation (main() resets it). Never raises."""
    try:
        cwd = Path.cwd()
    except Exception:
        return Path(".")
    key = str(cwd)
    if key not in _ROOT_CACHE:
        root = None
        try:
            import retrieval_log as _rl   # the shared resolver (also retrieval_log.default_root)
            root = _rl.project_root(cwd)
        except Exception:
            root = None
        _ROOT_CACHE[key] = Path(root) if root is not None else cwd
    return _ROOT_CACHE[key]


class ProjectResolutionError(RuntimeError):
    """This project's Neo4j graph can't be identified — the port registry exists but is unreadable,
    this project has no instance while another project owns bolt 7687, or the legacy 7687 graph is
    provably not this project's. Raised instead of answering from the base bolt 7687, which on a
    multi-project machine is ANOTHER project's (or program's) database. Its message names the fix."""


class ForeignBaseClaimError(ProjectResolutionError):
    """ROUND-5 G1: this project's REGISTRY claim on the local base store is refused — that store holds
    ANOTHER project's graph (a <= v0.27.4 mis-adoption, F2). A ProjectResolutionError (the same exit-3 /
    doctor-FAIL handling); its own class so doctor can also flag this project's MCP servers, which were
    started on that claim and so serve the other project's graph."""


class _Route:
    """Stand-in for port_registry.Neo4jRoute when that module can't be imported."""

    def __init__(self, uri, source, problem=None, *, bolt=None, http=None, key=None, kind=None):
        self.uri, self.source, self.problem = uri, source, problem
        self.bolt, self.http, self.key, self.kind = bolt, http, key, kind


_LAST_ROUTE = None   # the route _neo4j_route() last resolved — auth / refusal messages word it
# ROUND-8 J4: the one spelling of a DeepMap-generated bolt URI is 127.0.0.1 (port_registry.bolt_uri) — the address
# every instance binds; "localhost" resolves to ::1 first on Windows (a refused IPv6 connect, ~2 s per connection,
# or a different listener on [::1]). Used only when port_registry itself can't be imported.
_FALLBACK_BOLT_URI = "bolt://127.0.0.1:7687"


def _neo4j_route():
    """THIS project's Neo4j route — port_registry.resolve_neo4j_route, the ONE routing rule shared with
    sync / indexes / provision / the hooks (ROUND-2 D1): an explicit env NEO4J_URI verbatim (the
    operator escape hatch); else the project's registered instance; else its own instance re-found from
    its conf (a lost registry entry); else bolt 7687 as a LEGACY route ("legacy-unverified"), which
    _driver() verifies before any read — the graph's :_DeepMapMeta owner must be this project.

    Raises ProjectResolutionError (the resolver's own message — it names /deep-map:setup) when there is
    no safe route: the registry exists but is unreadable, or this project has no instance of its own
    while another registered project owns 7687 (it used to answer from THAT project's graph). Only a
    MISSING port_registry module falls back to NEO4J_URI or 7687 (then a legacy route, verified too)."""
    global _LAST_ROUTE
    try:
        import port_registry as PR
    except Exception:
        env_uri = os.environ.get("NEO4J_URI")
        _LAST_ROUTE = _Route(env_uri or _FALLBACK_BOLT_URI, "env" if env_uri else "legacy-unverified",
                             bolt=None if env_uri else 7687, http=None if env_uri else 7474)
        return _LAST_ROUTE
    route = PR.resolve_neo4j_route(_project_root())
    _LAST_ROUTE = route
    if getattr(route, "problem", None) or not getattr(route, "uri", None):
        raise ProjectResolutionError(getattr(route, "problem", None)
                                     or "could not resolve this project's Neo4j instance")
    return route


def _neo4j_uri() -> str:
    """This project's Neo4j bolt URI (see _neo4j_route; raises ProjectResolutionError when there is no
    safe route — never a silent 7687)."""
    return _neo4j_route().uri


def _uri_for_message() -> str:
    """_neo4j_uri() for an error/hint message, its credentials MASKED (_redact_uri — an operator NEO4J_URI is dialled
    verbatim but never printed with its password). Never raises."""
    try:
        return _redact_uri(_neo4j_uri())
    except Exception:
        return "this project's Neo4j"


# ---- F1 (r283): a NEO4J_URI's credentials are never printed ------------------------------------------------------
# An operator NEO4J_URI is DIALLED verbatim (the escape hatch) but only ever PRINTED masked. The neo4j driver refuses a
# user name or password inside the URI ("Username is not supported in the URI"), so `bolt://user:password@host` fails
# — and every message about that failure (doctor, status, main's exit-3 line) used to print it whole, password
# included, into the terminal, the Claude Code transcript and any log. Where the userinfo ends is read two ways, and
# both readings' secrets are masked (_uri_secret_spans; they agree on every ordinary URI): urlsplit's — the driver's own
# parser: the netloc, up to the first '/', '?' or '#', holds the userinfo up to its last "@", so an "@" after the host
# (a query holding one) is not userinfo (round 3, cov-build[8]: taking the URI's last "@" alone printed a token whole
# and masked the port instead) — and the URI's LAST "@", because a password holding '/', '?' or '#' ends that netloc
# early. So a password holding ':', '@', '/', '?', '#' or a space is masked whole; the user name stays readable before
# a password, and a lone value (no ':') is masked too — it can be a token.
_URI_SCHEME_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*://")
_URI_IN_TEXT_RE = re.compile(r"(?<![A-Za-z0-9+.\-])[A-Za-z][A-Za-z0-9+.\-]*://\S*")
_URI_MASK = "***"


def _uri_secret_spans(uri: str) -> list:
    """[(start, end)] of the secret(s) in `uri`'s userinfo, in order and merged — [] when `uri` carries no userinfo.
    The userinfo ends at the netloc's last '@' (urlsplit's reading — the netloc stops at the first '/', '?' or '#'
    after the scheme's '//') and, second reading, at the URI's last '@'; in each, the secret is the part after the
    userinfo's first ':' (the password), or the whole userinfo when it has no ':'. Never raises."""
    try:
        m = _URI_SCHEME_RE.match(uri)
        start = m.end() if m else 0
        ends = [i for i in (uri.find(c, start) for c in "/?#") if i >= 0]
        spans = []
        for at in (uri.rfind("@", start, min(ends) if ends else len(uri)), uri.rfind("@", start)):
            if at >= 0:
                colon = uri.find(":", start, at)
                spans.append((colon + 1 if colon >= 0 else start, at))
        merged: list = []
        for s0, s1 in sorted(set(spans)):
            if merged and s0 <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], s1))
            else:
                merged.append((s0, s1))
        return merged
    except Exception:
        return []


def _redact_uri(uri) -> str:
    """`uri` as the CLI prints it: its userinfo secret masked — `bolt://user:***@host:7687` (scheme, user, host, port
    and query kept); a lone userinfo prints as `***@`. When the two readings of the userinfo differ (an '@' after the
    host, or a password holding '/', '?' or '#'), both secrets are masked — `neo4j+s://***@host:***@b` for
    `neo4j+s://<token>@host:7687?x=a@b`, a port given up rather than a token printed. A URI without userinfo — every URI
    DeepMap generates — is returned unchanged. For printing only: the driver is always handed the URI verbatim. Never
    raises (an unprintable value -> a placeholder, never its raw text)."""
    try:
        text = str(uri)
    except Exception:
        return "<NEO4J_URI>"
    for s0, s1 in reversed(_uri_secret_spans(text)):   # right to left: the earlier offsets stay valid
        text = text[:s0] + _URI_MASK + text[s1:]
    return text


def _operator_uri_echoes() -> list:
    """[(piece, masked piece)] — what THIS invocation's operator NEO4J_URI can leak through text the CLI does not word
    itself, longest first: the verbatim URI, and each URL component the neo4j driver derives from it (netloc — its
    "host:port" address — path, params, query, fragment) whose span in the URI overlaps the secret, with that overlap
    masked. A password with '/', '?' or '#' splits the URI early: the driver then quotes the rest back as a query
    string ("Invalid parameters: ... in query string '<rest>@host'"), or dials `<user>:<start of the password>` and
    names that address in its connect / DNS errors. [] when NEO4J_URI is unset or carries no userinfo. Never raises."""
    try:
        uri = os.environ.get("NEO4J_URI") or ""
        spans = _uri_secret_spans(uri) if uri else []
        if not spans:
            return []
        out = {uri: _redact_uri(uri)}
        try:
            from urllib.parse import urlparse
            p = urlparse(uri)
            comps = (p.netloc, p.path, p.params, p.query, p.fragment)
        except Exception:
            comps = ()
        m = _URI_SCHEME_RE.match(uri)
        cursor = m.end() if m else 0
        frags = [f for s0, s1 in spans for f in re.split(r"[\s/?#@:;\[\]]+", uri[s0:s1]) if f]
        for c in comps:          # in the URI's own order: netloc, path, params, query, fragment
            if not c:
                continue
            at = uri.find(c, cursor)
            if at < 0:           # not verbatim (urlparse drops a tab / newline): masked whole if it holds secret text
                if any(f in c for f in frags):
                    out.setdefault(c, _URI_MASK)
                continue
            cursor = at + len(c)
            masked = c
            for s0, s1 in reversed(spans):   # right to left: the earlier offsets into `c` stay valid
                lo, hi = max(at, s0), min(at + len(c), s1)
                if lo < hi:      # this component holds part of a secret: mask exactly that part
                    masked = masked[:lo - at] + _URI_MASK + masked[hi - at:]
            if masked != c:
                out.setdefault(c, masked)
        return sorted(out.items(), key=lambda kv: len(kv[0]), reverse=True)
    except Exception:
        return []


def _redact_text(text) -> str:
    """`text` as the CLI prints it: every `scheme://...` URI in it masked exactly as _redact_uri masks one, and every
    piece of the operator NEO4J_URI that carries its secret (_operator_uri_echoes) masked — so neither a URI nor the
    driver's own echo of part of one prints a password. Applied where a Neo4j message is printed: doctor's lines,
    main's exit-3 line, the dropped-connection notice. Text holding neither comes back unchanged. Never raises."""
    try:
        s = str(text)
    except Exception:
        return ""
    try:
        for piece, masked in _operator_uri_echoes():
            if piece in s:
                s = s.replace(piece, masked)
    except Exception:
        pass
    try:
        s = _URI_IN_TEXT_RE.sub(lambda m: _redact_uri(m.group(0)), s)
    except Exception:
        pass
    return s


def _verify_legacy_route(drv, uri) -> None:
    """D1: may THIS project read the graph behind the LEGACY bolt 7687 route? Only when its
    :_DeepMapMeta owner is this project, or it is unowned AND empty (sync.verify_legacy_owner — W6's
    read-only check; port_registry.legacy_owner_verdict holds the policy). Raises ProjectResolutionError
    with the refusal (it names the owner and /deep-map:setup) otherwise. A database error (instance down,
    bad credentials) PROPAGATES — an outage is reported as an outage, never as a verdict."""
    root = _project_root()
    try:
        verify = _load("sync").verify_legacy_owner
    except Exception:
        verify = None
    if verify is not None:
        ok, reason = verify(drv, root, uri=uri)
    else:   # sync can't be loaded: read the owner here; port_registry holds the policy
        import port_registry as PR
        with drv.session() as s:
            rows = list(s.run("MATCH (m:_DeepMapMeta) RETURN m.owning_root AS owning_root LIMIT 1"))
            owner = next((r["owning_root"] for r in rows if r["owning_root"]), None)
            has_nodes = None
            if not owner:
                c = list(s.run("MATCH (n) WHERE NOT n:_DeepMapMeta RETURN count(n) AS c"))
                has_nodes = bool(c) and bool(c[0]["c"])
        ok, reason = PR.legacy_owner_verdict(root, owner, has_nodes, uri=uri)
    if not ok:
        raise ProjectResolutionError(reason or f"{uri} can't be shown to hold this project's graph — run "
                                               "/deep-map:setup in this project to give it its own instance")


def _is_base_claim_route(route) -> bool:
    """ROUND-5 G1: True iff `route` is THIS project's REGISTRY claim on the LOCAL base store (bolt 7687 —
    the base pair 7687/7474) with no operator NEO4J_URI set: the one route a <= v0.27.4 mis-adoption can
    have left pointing at ANOTHER project's store (F2). A per-project instance, the operator escape hatch
    and the legacy route (verified by _verify_legacy_route) are not. Never raises."""
    try:
        if os.environ.get("NEO4J_URI") or getattr(route, "source", None) != "registry":
            return False
        try:
            import port_registry as PR
            base_bolt = int(PR.BASE_BOLT)
        except Exception:
            base_bolt = 7687
        from urllib.parse import urlparse
        u = urlparse(str(getattr(route, "uri", "") or ""))
        # any loopback spelling: this build writes 127.0.0.1 (J4), an older build's route says localhost
        if (u.hostname or "").lower() not in ("localhost", "127.0.0.1", "::1", "0:0:0:0:0:0:0:1"):
            return False
        bolt = getattr(route, "bolt", None) or u.port or base_bolt
        return int(bolt) == base_bolt
    except Exception:
        return False


def _base_claim_record(route, root):
    """(problem, proven_ours, provision) for a base-claim route, from the base store's OWNER RECORD — a
    registry + file read, no Neo4j (provision.foreign_base_claim_problem, the public F2 check): `problem` is
    its one-line refusal when the record names ANOTHER project (it names that project and /deep-map:setup);
    `proven_ours` is True when the record names THIS project (THE owner predicate), which makes the live
    owner read unnecessary. provision unloadable, or an older copy without these helpers -> (None, False,
    ...) so the live read decides. Never raises."""
    try:
        P = _load("provision")
    except Exception:
        return None, False, None
    try:
        check = getattr(P, "foreign_base_claim_problem", None)
        problem = check(root, getattr(route, "uri", None)) if callable(check) else None
        if problem:
            return str(problem), False, P
        is_ours = getattr(P, "_base_owner_is", None)
        return None, (bool(is_ours(root)) if callable(is_ours) else False), P
    except Exception:
        return None, False, P


def _foreign_claim_message(owner, uri, P=None) -> str:
    """The refusal for a base claim whose LIVE graph records another project's owner — provision's own
    wording (_foreign_claim_text) when it is loadable, so the CLI, heal and status say the same thing."""
    fn = getattr(P, "_foreign_claim_text", None) if P is not None else None
    if callable(fn):
        try:
            return str(fn(owner, uri, "the owner its live graph records"))
        except Exception:
            pass
    return (f"this project is registered on the base Neo4j ({uri}), but that store belongs to project "
            f"{owner} (the owner its live graph records) — a claim an older DeepMap wrote by mistake; "
            "DeepMap never uses another project's graph as this one's: run /deep-map:setup to give this "
            "project its own instance")


def _verify_base_claim_owner(drv, route, root, P=None) -> None:
    """ROUND-5 G1, the live half (no owner record names this project yet — a pre-upgrade claim): the
    :_DeepMapMeta owner of the graph behind THIS project's base claim decides, by
    port_registry.owner_is_this_project (THE owner predicate, F1 — a nested repository is a project of its
    own, a subdirectory of the same repo is this one). Another project's owner -> ProjectResolutionError
    naming it and /deep-map:setup (which releases the claim — F2). An unowned graph is no proof -> allowed
    (as provision._foreign_base_owner). Read-only. A database error PROPAGATES: an outage is reported as an
    outage, never as a verdict."""
    with drv.session() as s:
        rows = list(s.run("MATCH (m:_DeepMapMeta) RETURN m.owning_root AS owning_root LIMIT 1"))
    owner = next((r["owning_root"] for r in rows if r["owning_root"]), None)
    if not owner:
        return
    try:
        import port_registry as PR
        ours = bool(PR.owner_is_this_project(root, owner))
    except Exception:
        ours = False   # can't be shown to be this project's -> not used as this project's graph
    if not ours:
        raise ForeignBaseClaimError(_foreign_claim_message(owner, getattr(route, "uri", None), P))


def _driver():
    """A driver for THIS project's graph (see _neo4j_route), with bounded connect / acquisition timeouts
    (mcp_bounds.neo4j_driver_timeouts — a dead or frozen Neo4j fails the CLI in bounded time, like the
    MCP servers). A read never answers from another project's graph:
      * on the LEGACY 7687 route the graph's owner is verified first (_verify_legacy_route);
      * on THIS project's REGISTRY claim on the local base store (ROUND-5 G1 — F2 for reads; never with an
        operator NEO4J_URI): the base store's owner record naming another project refuses BEFORE a driver
        exists (a file read); when no record names this project yet, the live :_DeepMapMeta owner on the
        opened driver must be this project (or absent). A per-project instance pays nothing for either.
    The driver is closed on any failure there."""
    from neo4j import GraphDatabase
    route = _neo4j_route()
    base_claim = _is_base_claim_route(route)
    proven_ours, P = False, None
    if base_claim:
        problem, proven_ours, P = _base_claim_record(route, _project_root())
        if problem:
            raise ForeignBaseClaimError(problem)   # that store is never even connected to
    drv = GraphDatabase.driver(
        route.uri,
        auth=(os.environ.get("NEO4J_USER", "neo4j"), os.environ.get("NEO4J_PASSWORD", "deepmap")),
        notifications_min_severity="OFF",
        **mcp_bounds.neo4j_driver_timeouts())
    verify = None
    if getattr(route, "source", None) == "legacy-unverified":
        verify = lambda: _verify_legacy_route(drv, route.uri)   # noqa: E731
    elif base_claim and not proven_ours:
        verify = lambda: _verify_base_claim_owner(drv, route, _project_root(), P)   # noqa: E731
    if verify is not None:
        try:
            verify()
        except BaseException:
            try:
                drv.close()
            except Exception:
                pass
            raise
    return drv


def _foreign_owner(root, owner):
    """None when the graph's recorded owner (:_DeepMapMeta owning_root) is THIS project or is unknown; else
    a short reason naming the owner (an operator NEO4J_URI shared by several projects serves the
    last-synced one's graph). "This project" is port_registry.owner_is_this_project — THE owner predicate
    (ROUND-4 F1), via legacy_owner_verdict: the same project identity (a subdirectory of the same repo, a
    vanished narrower stamp, a pre-UTF-8-fix stamp), never a nested repository or a non-git folder inside
    or around this one. Never raises."""
    if not owner:
        return None
    try:
        import port_registry as PR
        ok, _reason = PR.legacy_owner_verdict(root, owner, True)
        return None if ok else f"this Neo4j holds project {owner}'s graph, not this project's"
    except Exception:
        return None


def _route_label(route=None) -> str:
    """' (legacy shared port — ...)' for the verified legacy 7687 route, else ''. Never raises."""
    try:
        route = route if route is not None else _LAST_ROUTE
        if getattr(route, "source", None) == "legacy-unverified":
            return " (legacy shared port; its graph is this project's)"
    except Exception:
        pass
    return ""


def _load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _vector_quantization_line(session):
    """ROUND-10 P4: doctor's (level, detail) for the vector-index quantization mode (indexes.
    vector_quantization_report), or None when it tells nothing useful (the server could not be read, or no
    vector index exists yet — the indexes line already says so). Never raises."""
    try:
        rep = _load("indexes").vector_quantization_report(session)
    except Exception:
        return None
    if not (isinstance(rep, tuple) and len(rep) == 2):
        return None
    level, detail = rep
    if level != "OK" and "re-creates" not in str(detail):   # only an actual quantized/exact MISMATCH warns
        return None
    return level, str(detail)


def _read_manifest(root) -> dict:
    """<root>/.deepmap/graph_build.json (the last sync's manifest) as a dict; {} when absent/malformed.
    Never raises."""
    try:
        data = json.loads((Path(root) / ".deepmap" / "graph_build.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _manifest_reasons(data: dict) -> list:
    """The last sync's verdict reasons (str list). Never raises."""
    try:
        return [str(r) for r in (data.get("reasons") or []) if str(r).strip()]
    except Exception:
        return []


def _clip_reason(text: str, limit: int = 200) -> str:
    """`text` shortened to about `limit` chars for a doctor line — never cutting a pasteable command in
    half (a truncated `"<python>" -m pip install ...` is not runnable, D6): a command that would not fit is
    dropped whole, from its interpreter's opening quote. Never raises."""
    try:
        text = str(text).strip()
        if len(text) <= limit:
            return text
        cut = limit
        for m in re.finditer(r" -m pip ", text):
            close = text.rfind('"', 0, m.start())
            if close != -1 and close == m.start() - 1:         # a quoted interpreter: "<python>" -m pip
                start = max(text.rfind('"', 0, close), 0)
            else:                                              # an unquoted one: python -m pip
                start = text.rfind(" ", 0, m.start()) + 1
            q1 = text.find('"', m.end())
            q2 = text.find('"', q1 + 1) if q1 != -1 else -1
            end = q2 + 1 if q2 != -1 else len(text)            # after the quoted spec
            if start < limit < end:                            # this command would be cut in half
                cut = start
                break
        return text[:cut].rstrip(" :;,—-(") + " …"
    except Exception:
        return str(text)[:limit]


def _manifest_fallback_reason(data: dict):
    """Why the last sync's `auto` extractor fell back, if sync recorded it (any non-empty string field
    whose key mentions 'fallback'), else None — clipped for a doctor line without ever halving a pasteable
    command (_clip_reason). Never raises."""
    try:
        for k, v in data.items():
            if "fallback" in str(k).lower() and isinstance(v, str) and v.strip():
                return _clip_reason(v)
    except Exception:
        pass
    return None


def _manifest_count(value):
    """A non-negative int from a manifest count field, else None (absent / bool / malformed). Never raises."""
    if value is None or isinstance(value, bool):
        return None
    try:
        n = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return n if n >= 0 else None


def _manifest_dirs(value) -> list:
    """A manifest dir list as display strings ('build/'); a bare string is one dir. Never raises."""
    try:
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, (list, tuple)):
            return []
        return [str(v).strip().rstrip("/\\") + "/" for v in value if v is not None and str(v).strip()]
    except Exception:
        return []


def _graphify_engine():
    """build/graphify_extract (a light import — the heavy graphify engine is loaded lazily inside it),
    or None. Never raises."""
    try:
        import graphify_extract as GX
        return GX
    except Exception:
        return None


def _installed_graphify():
    """(supported, version) of the graphify installed NOW (graphify_extract.graphify_support — the range
    the installers pin), or (None, None) when unknowable. Never raises."""
    try:
        GX = _graphify_engine()
        if GX is not None:
            supported, version = GX.graphify_support()
            return supported, (str(version).strip() or None) if version else None
    except Exception:
        pass
    return None, None


def _ver_tuple(text):
    """The numeric release part of a version string padded to 3 ('0.9' -> (0, 9, 0), '0.8.39.post1' ->
    (0, 8, 39)), or None (graphify_extract._version_tuple's rule). Never raises."""
    try:
        m = re.match(r"\s*v?(\d+(?:\.\d+)*)", str(text or ""))
        if not m:
            return None
        nums = [int(x) for x in m.group(1).split(".")]
        return tuple((nums + [0, 0, 0])[:3])
    except Exception:
        return None


def _graphify_range():
    """(min_inclusive, below_exclusive) version tuples of the graphify range DeepMap supports:
    graphify_extract's SUPPORTED_GRAPHIFY_MIN / _BELOW, else parsed from the spec string itself
    ('graphifyy>=A,<B'); None when neither is readable. Never raises."""
    try:
        GX = _graphify_engine()
        lo = getattr(GX, "SUPPORTED_GRAPHIFY_MIN", None) if GX is not None else None
        hi = getattr(GX, "SUPPORTED_GRAPHIFY_BELOW", None) if GX is not None else None
        if (isinstance(lo, tuple) and isinstance(hi, tuple) and lo and hi
                and all(isinstance(x, int) for x in lo + hi)):
            return (tuple((list(lo) + [0, 0, 0])[:3]), tuple((list(hi) + [0, 0, 0])[:3]))
        spec = _graphify_spec()
        m_lo = re.search(r">=\s*(\d+(?:\.\d+)*)", spec)
        m_hi = re.search(r"(?<![<>=!~])<\s*(\d+(?:\.\d+)*)", spec)
        if m_lo and m_hi:
            return _ver_tuple(m_lo.group(1)), _ver_tuple(m_hi.group(1))
    except Exception:
        pass
    return None


def _graphify_in_range(version):
    """True / False: is graphify `version` inside the supported range (_graphify_range)? None when the
    version or the range is unreadable (no claim either way). Never raises."""
    try:
        t, rng = _ver_tuple(version) if version else None, _graphify_range()
        if t is None or not rng or rng[0] is None or rng[1] is None:
            return None
        return rng[0] <= t < rng[1]
    except Exception:
        return None


def _graphify_version_fact(built, installed=(None, None), *, cmd_above=False):
    """(line, warn, gives_fix) about the graphify the last sync RAN (manifest `graphify_version`) vs the
    range DeepMap supports (the validated range the installers pin — ROUND-4 F3): inside -> an
    informational line naming the range; outside -> `warn`, and the line carries the ONE fix (a
    /deep-map:refresh when the graphify `installed` NOW is a supported one, else the pinned pip command —
    or, `cmd_above`, a pointer to that command on the extractor line), so no other line of the block
    repeats it (`gives_fix`). (None, False, False) when the version is unknown/unreadable (an older
    manifest prints as before). Never raises."""
    try:
        built = str(built or "").strip()
        inside = _graphify_in_range(built) if built else None
        if inside is None:
            return None, False, False
        spec = _graphify_spec()
        if inside:
            return f"graphify {built}: inside DeepMap's supported range ({spec})", False, False
        sup_now, now = installed if isinstance(installed, tuple) and len(installed) == 2 else (None, None)
        if cmd_above:
            fix = "the install command above puts a supported one in place; then re-run /deep-map:refresh"
        elif sup_now is True and now:
            fix = (f"the graphify {now} installed now is supported — re-run /deep-map:refresh to rebuild the "
                   "graph with it")
        else:
            also = (f"the graphify {now} installed now is outside it too; " if (sup_now is False and now
                                                                                and str(now).strip() != built)
                    else "")
            fix = (f"{also}install a supported one into DeepMap's Python: {_graphify_install_cmd()}, then "
                   "re-run /deep-map:refresh")
        return (f"graphify {built}: OUTSIDE DeepMap's supported range ({spec}) — DeepMap was not validated "
                f"against it (its parsers and directory skips may differ), so this graph may miss or mis-map "
                f"code; {fix}"), True, True
    except Exception:
        return None, False, False


def _graphify_build_facts(manifest: dict, installed=(None, None), *, cmd_above=False):
    """(lines, warn, gives_fix): HOW the last sync's graphify run built the graph, from its manifest (the
    keys sync records from the extraction report): the graphify version it ran and whether that is inside
    the supported range (_graphify_version_fact — outside: `warn`, and that line carries the fix, which
    `gives_fix` reports so the caller does not repeat it), whether in-file self./cls. calls were re-bound
    to the caller's own class (and how many), which engine-skipped dirs holding git-tracked source were
    re-included — and which DeepMap could NOT re-include because the graphify that ran was outside the
    supported range (`warn`; the fix is the version line's, else: a /deep-map:refresh when the graphify
    `installed` NOW — (supported, version) from _installed_graphify — is a supported one, else the runnable
    pip remedy. Whether their source is really missing is the sync verdict's LOW COVERAGE reason, which
    doctor already reports), how imports were bound (engine-resolved target vs name), and how many files
    graphify could not extract. Only meaningful when graphify's graph is the one kept. A key the manifest
    lacks prints nothing (an older sync's manifest reads exactly as before). `cmd_above`: the extractor
    line already prints the install command (graphify not importable now), so no fact repeats it.
    Never raises."""
    lines, warn, gives_fix = [], False, False
    try:
        vline, vwarn, gives_fix = _graphify_version_fact(manifest.get("graphify_version"), installed,
                                                         cmd_above=cmd_above)
        if vline:
            lines.append(vline)
            warn = warn or vwarn
    except Exception:
        pass
    try:
        if "graphify_self_call_rebind" in manifest:
            if manifest.get("graphify_self_call_rebind") is True:
                n = _manifest_count(manifest.get("graphify_self_calls_rebound")) or 0
                lines.append("self-call re-binding: on — " + (
                    f"{n} in-file self./cls./super() call(s) re-bound to the caller's own class"
                    if n else "no in-file self./cls. call needed re-binding"))
            else:
                lines.append("self-call re-binding: OFF (DEEPMAP_SELF_CALL_REBIND=0) — in-file self./cls. "
                             "calls keep the engine's binding, which can name a same-named method of ANOTHER "
                             "class in the file")
        rescued = _manifest_dirs(manifest.get("graphify_rescued_dirs"))
        if rescued:
            dropped = _manifest_count(manifest.get("graphify_rescue_dropped")) or 0
            lines.append(f"re-included engine-skipped dir(s) holding git-tracked source: {', '.join(rescued)}"
                         + (f" ({dropped} node(s) the engine found beside that source dropped)" if dropped
                            else ""))
        blocked = _manifest_dirs(manifest.get("graphify_rescue_blocked"))
        if blocked:
            ver = str(manifest.get("graphify_version") or "").strip() or None
            sup_now, now = installed if isinstance(installed, tuple) and len(installed) == 2 else (None, None)
            if gives_fix:   # the version line above names the unsupported graphify AND its one fix
                hint = f"graphify {ver} ran that sync — the fix is on the graphify {ver} line above"
            elif cmd_above:
                hint = (f"graphify {ver or '(unknown version)'} ran that sync, outside the range DeepMap "
                        f"supports ({_graphify_spec()}) — the install command above puts a supported one "
                        "in place; then re-run /deep-map:refresh")
            elif sup_now is True and now and now != ver:
                hint = (f"graphify {ver or '(unknown version)'} ran that sync, outside the range DeepMap "
                        f"supports; the graphify {now} installed now is supported — re-run /deep-map:refresh "
                        "to re-include them")
            else:
                hint = _unsupported_graphify_hint(ver)
            lines.append(f"NOT re-included by DeepMap: engine-skipped dir(s) {', '.join(blocked)} — any "
                         f"git-tracked source the engine skipped there is not in the graph; {hint}")
            warn = True
        if "graphify_resolved_imports" in manifest:
            by_key = _manifest_count(manifest.get("graphify_imports_resolved")) or 0
            by_name = _manifest_count(manifest.get("graphify_imports_by_name")) or 0
            if manifest.get("graphify_resolved_imports") is True:
                lines.append(f"imports: {by_key} bound to the engine-resolved target, {by_name} by name")
            else:
                lines.append(f"imports: all {by_name} bound by name (engine-resolved binding off: "
                             "DEEPMAP_RESOLVED_IMPORTS=0)")
        failed = _manifest_count(manifest.get("graphify_failed_files"))
        if failed:
            lines.append(f"{failed} file(s) graphify could not extract are missing from the graph — "
                         "/deep-map:refresh shows graphify's warnings")
    except Exception:
        pass
    return lines, warn, gives_fix


def _installed_graphify_note(manifest: dict, installed=(None, None), *, fix_given=False):
    """(status_warn, note) comparing the graphify the last sync ran (manifest `graphify_version`) with the
    one `installed` NOW ((supported, version) from _installed_graphify): a different version -> the next
    /deep-map:refresh uses it; one outside the supported range -> WARN + the runnable pip remedy (the next
    sync would not re-include engine-skipped dirs, and its census and extraction disagree) — unless
    `fix_given` (the build-facts version line already carries the fix: WARN, no repeated command).
    (False, "") when unknowable. Never raises."""
    try:
        supported, now = installed if isinstance(installed, tuple) and len(installed) == 2 else (None, None)
        built = str(manifest.get("graphify_version") or "").strip()
        if now and supported is False:
            return True, ("" if fix_given else f"installed now — {_unsupported_graphify_hint(now)}")
        if now and built and str(now).strip() != built:
            return False, f"graphify {now} is installed now (the next /deep-map:refresh uses it)"
    except Exception:
        pass
    return False, ""


def _setup_remedy(*names):
    """deps_bootstrap.setup_remedy: "run /deep-map:setup (...)" when one of `names` is missing from this Python or
    outside its range (setup installs it into DeepMap's own environment, never into this Python); None otherwise,
    under DEEPMAP_MANAGED_VENV=0, or when unavailable — then the pip command for this Python stands. Never raises."""
    try:
        import deps_bootstrap
        return deps_bootstrap.setup_remedy(names)
    except Exception:
        return None


def _pip_python() -> str:
    """The interpreter running DeepMap (the one a missing package must be installed into), QUOTED with
    forward slashes — a bare Windows path loses its backslashes in bash (the shell Claude Code runs), so
    only this form pastes into bash, PowerShell and cmd alike (D6). graphify_extract.hint_python is the
    one source; this is its fallback. Never raises."""
    try:
        GX = _graphify_engine()
        fn = getattr(GX, "hint_python", None) if GX is not None else None
        if callable(fn):
            got = fn()
            if isinstance(got, str) and got.strip():
                return got
    except Exception:
        pass
    try:
        return f'"{Path(sys.executable).as_posix()}"' if sys.executable else "python"
    except Exception:
        return "python"


def _graphify_spec() -> str:
    """The graphify range DeepMap supports (graphify_extract.SUPPORTED_GRAPHIFY_SPEC — the validated range
    the installers pin, ROUND-4 F3). The literal is only a fallback for an unimportable graphify_extract;
    tests/cli_round4_gates.py locks it to the constant. Never raises."""
    GX = _graphify_engine()
    return str(getattr(GX, "SUPPORTED_GRAPHIFY_SPEC", "") or "graphifyy>=0.8.39,<0.8.52")


def _graphify_install_cmd() -> str:
    """The runnable install command for DeepMap's pinned graphify (D6) — graphify_extract's
    graphify_install_command, the ONE source every surface prints: DeepMap's own interpreter, quoted with
    forward slashes, `-m pip install --upgrade "<SUPPORTED_GRAPHIFY_SPEC>"` (`--upgrade` lifts an old
    in-range copy and replaces an out-of-range one; an unpinned install may target another Python and
    resolve to an unvalidated release). Formatted identically here only when that helper is unavailable.
    Never raises."""
    try:
        GX = _graphify_engine()
        fn = getattr(GX, "graphify_install_command", None) if GX is not None else None
        if callable(fn):
            got = fn()
            if isinstance(got, str) and got.strip():
                return got
    except Exception:
        pass
    return f'{_pip_python()} -m pip install --upgrade "{_graphify_spec()}"'


def _unsupported_graphify_hint(version) -> str:
    """The runnable remedy for a graphify outside the range DeepMap supports (graphify_extract's
    SUPPORTED_GRAPHIFY_SPEC — the installers' pin), for DeepMap's own interpreter: /deep-map:setup when this
    Python holds it (_setup_remedy). Never raises."""
    spec = _graphify_spec()
    head = f"graphify {version or '(unknown version)'} is outside the range DeepMap supports ({spec})"
    setup = _setup_remedy(re.split(r"[<>=!~;\[\s]", spec, maxsplit=1)[0])   # the spec's distribution name
    if setup:
        return f"{head} — {setup}, which also rebuilds the graph"
    return f"{head} — install it into DeepMap's Python: {_graphify_install_cmd()}, then re-run /deep-map:refresh"


def _ml_stack_remedy() -> str:
    """The runnable fix for a missing ML stack (D6): /deep-map:setup when this Python lacks one of its packages
    (_setup_remedy), else the pip commands for the interpreter that just failed the import (the one running
    DeepMap; _pip_python) — the same packages the sync verdict's VECTOR SEARCH DISABLED reason names. Never
    raises."""
    setup = _setup_remedy("torch", "sentence-transformers", "transformers")
    if setup:
        return setup
    py = _pip_python()
    return (f"install it into DeepMap's Python: {py} -m pip install --index-url "
            f"https://download.pytorch.org/whl/cpu torch && {py} -m pip install sentence-transformers "
            f"transformers — then re-run /deep-map:setup")


def _neo4j_driver_spec() -> str:
    """The neo4j driver range DeepMap supports, e.g. 'neo4j>=6.0.1,<7' — the `neo4j` line of the plugin's
    requirements.txt, the one pin the installers, CI and scripts/ml_stack_selfcheck.py mirror
    (tests/compliance_gates.py), read here so no second copy can drift. '' when unreadable. Never raises."""
    try:
        for raw in (HERE.parent / "requirements.txt").read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*neo4j\s*((?:[<>=!~]=?\s*\d[^,\s]*\s*,?\s*)+)$", raw.split("#", 1)[0],
                         re.IGNORECASE)
            if m:
                return "neo4j" + re.sub(r"\s+", "", m.group(1)).rstrip(",")
    except Exception:
        pass
    return ""


def _installed_neo4j_version():
    """The version of the neo4j driver distribution installed in DeepMap's Python (importlib.metadata — the
    package itself is not imported), or None when it is not installed / unreadable. Never raises."""
    try:
        from importlib import metadata
        return str(metadata.version("neo4j")).strip() or None
    except Exception:
        return None


def _version_in_spec(version, spec):
    """True / False: does `version` satisfy every >= / > / <= / < / == / != clause of a requirements-style
    range ('neo4j>=6.0.1,<7'), compared on the numeric release part (_ver_tuple)? None when either is
    unreadable (no claim either way). Never raises."""
    try:
        v = _ver_tuple(version) if version else None
        clauses = re.findall(r"(>=|<=|==|!=|>|<)\s*(\d+(?:\.\d+)*)", str(spec or ""))
        if v is None or not clauses:
            return None
        cmp = {">=": lambda a, b: a >= b, ">": lambda a, b: a > b, "<=": lambda a, b: a <= b,
               "<": lambda a, b: a < b, "==": lambda a, b: a == b, "!=": lambda a, b: a != b}
        return all(cmp[op](v, _ver_tuple(bound)) for op, bound in clauses)
    except Exception:
        return None


def _neo4j_driver_fix() -> str:
    """The runnable install of the neo4j driver range DeepMap supports (_neo4j_driver_spec): /deep-map:setup when
    this Python lacks it or holds it out of range (_setup_remedy: DeepMap's own environment), else into DeepMap's
    own Python (_pip_python): `-m pip install --upgrade "<spec>"` — `--upgrade` lifts an old copy, and the range
    keeps pip off an unvalidated major release (a bare `pip install neo4j` would take one). DeepMap's installer
    when the range is unreadable. Never raises."""
    setup = _setup_remedy("neo4j")
    if setup:
        return setup
    spec = _neo4j_driver_spec()
    if spec:
        return f'{_pip_python()} -m pip install --upgrade "{spec}"'
    return "re-run DeepMap's installer (install.sh / install.ps1 in the plugin directory)"


def _manifest_coverage_lines(root) -> list:
    """Best-effort human lines from <root>/.deepmap/graph_build.json: the last-sync verdict + the
    per-language / per-conventional-dir coverage sync recorded (the Rails honesty layer) + EVERY reason
    the verdict carried (a non-READY verdict must say why, not just its name). Reads ONE small JSON — no
    Neo4j. Returns [] when the manifest is absent, legacy (no verdict fields), or malformed. Never
    raises."""
    try:
        data = _read_manifest(root)
        out = []
        if data.get("verdict"):
            out.append(f"last-sync verdict: {data['verdict']}")
        cov = data.get("coverage") or {}
        if cov:
            V = _load("verdict")
            out.extend(V.format_coverage(cov))
        out.extend(_manifest_reasons(data))
        return out
    except Exception:
        return []


def _git_pre() -> list:
    """`-c core.longpaths=true` on Windows, [] elsewhere — put before `-C <root>` in every git probe the CLI runs
    (ROUND-8 J6; the same flag as port_registry.git_longpaths_args). Git for Windows refuses a path past MAX_PATH
    (260) unless core.longpaths is set: a committed long file then read as an uncommitted change (or `git status`
    failed outright when the repository's own objects were past it). Harmless where paths are short."""
    return ["-c", "core.longpaths=true"] if os.name == "nt" else []


def _mtime(path) -> float:
    """os.stat(path).st_mtime. On Windows a path past MAX_PATH — which a plain stat can't open unless long paths
    are enabled system-wide — is retried in its extended-length form (the `\\\\?\\` prefix) (ROUND-8 J6: once git
    reports such a file, its mtime must be readable too, or a genuine edit of it goes unnoticed). Raises OSError
    like os.stat."""
    try:
        return os.stat(path).st_mtime
    except OSError:
        if os.name != "nt":
            raise
        full = os.path.abspath(str(path))
        if full.startswith("\\\\?\\"):
            raise
        full = ("\\\\?\\UNC\\" + full[2:]) if full.startswith("\\\\") else ("\\\\?\\" + full)
        return os.stat(full).st_mtime


def _edited_since_build(root, sample, built_at_utc):
    """The first of the manifest's `dirty_paths_sample` (paths relative to the sync root) whose file
    changed AFTER the build (mtime past `built_at_utc`), else None — proof that a graph built from
    uncommitted changes no longer matches them. Bounded (the sample is at most 10 paths). Never raises."""
    try:
        from datetime import datetime, timezone
        built = datetime.strptime(str(built_at_utc), "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc).timestamp()
    except Exception:
        return None
    for rel in list(sample or [])[:10]:
        try:
            if _mtime(Path(root) / str(rel)) > built + 1.0:
                return str(rel)
        except Exception:
            continue
    return None


_ARTIFACT_PREFIXES = (".deepmap/", "graphify-out/")   # DeepMap's own artifact dirs (+ .mcp.json below)
_STATUS_MAX_ENTRIES = 500


def _source_suffixes():
    """The census source extensions (verdict._SOURCE_EXTS), or None when unknowable (then any file counts)."""
    try:
        return {str(k).lower() for k in _load("verdict")._SOURCE_EXTS}
    except Exception:
        return None


def _uncommitted_since_build(root, built_at_utc, git):
    """(n, first): uncommitted SOURCE changes in the work tree right now (git status — modified, added,
    renamed or untracked, .gitignore honoured) whose file changed AFTER the build, i.e. edits the graph cannot
    hold. DeepMap's own artifacts (.deepmap/, graphify-out/, .mcp.json) never count; deletions are skipped
    (their time is unknowable). Bounded: a 5 s git status, the first 500 entries. (0, None) when unknowable.
    Never raises."""
    try:
        import subprocess
        from datetime import datetime, timezone
        built = datetime.strptime(str(built_at_utc), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
        p = subprocess.run([git, *_git_pre(), "-C", str(root), "status", "--porcelain=v1", "-z",
                            "--untracked-files=normal"], capture_output=True, timeout=5)
        if p.returncode != 0:
            return 0, None
        exts = _source_suffixes()
        toks = (p.stdout or b"").split(b"\0")
        n, first, i, seen = 0, None, 0, 0
        while i < len(toks) and seen < _STATUS_MAX_ENTRIES:
            tok = toks[i]
            i += 1
            if len(tok) < 4:
                continue
            seen += 1
            xy, rel = tok[:2].decode("ascii", "replace"), tok[3:].decode("utf-8", "surrogateescape")
            if "R" in xy or "C" in xy:
                i += 1                                   # a rename / copy carries its old path next
            if "D" in xy:
                continue
            norm = rel.replace("\\", "/")
            if norm == ".mcp.json" or norm.startswith(_ARTIFACT_PREFIXES):
                continue
            if exts is not None and not norm.endswith("/") and Path(norm).suffix.lower() not in exts:
                continue                                 # docs / data: not what the graph indexes
            try:
                if _mtime(Path(root) / norm) > built + 1.0:
                    n += 1
                    first = first or norm
            except OSError:
                continue
        return n, first
    except Exception:
        return 0, None


def _staleness_line(root):
    """Best-effort graph-freshness line (doctor / status): the manifest's build commit vs the current
    git HEAD (short-timeout subprocess), worded for the ROUND-2 D4 manifest:
      * HEAD moved since the build                           -> "may be STALE" (a round-1 dirty manifest
        recorded only built_at_base_sha — read as the build commit, so staleness is not switched off);
      * HEAD unchanged but the build ran on uncommitted changes (`dirty`) -> "built from uncommitted
        changes on top of <sha>", and "may be STALE" once a sampled dirty file changed after the build;
      * `dirty` recorded as null (unknown)                    -> says the work-tree state was unknown;
      * a clean build, but git status NOW shows source files changed after it (uncommitted edits, new
        files) -> "may be STALE", naming one (DeepMap's own artifacts never count);
      * otherwise                                             -> fresh as of the last commit.
    None when unknowable (no manifest / sha / git). Informational — never changes a verdict or exit
    code. Never raises."""
    try:
        import shutil
        import subprocess
        data = _read_manifest(root)
        built = str(data.get("built_at_sha") or data.get("built_at_base_sha") or "").lower()
        git = shutil.which("git")
        if not built or not git:
            return None
        p = subprocess.run([git, *_git_pre(), "-C", str(root), "rev-parse", "HEAD"],
                           capture_output=True, encoding="utf-8", errors="surrogateescape", timeout=5)
        head = (p.stdout or "").strip().lower()
        if p.returncode != 0 or len(head) != 40:
            return None
        # One rule with the hooks (KL-architecture:002): when DeepMap's background refresh PROVED the graph current at
        # this exact HEAD (no source change since the build — a docs-only commit; deepmap_hook_state._fresh_proof:
        # outcome "fresh", fresh_at_head == HEAD, the manifest's own source fingerprint, auto-sync on), say so instead
        # of "may be STALE". Without that proof every line below is unchanged.
        try:
            if _load("auto_sync")._load_hook_state()._fresh_proof(root, data, head) is not None:
                return (f"current at {head[:7]} — DeepMap's background refresh found no source change since the "
                        f"build at {built[:7]}")
        except Exception:  # noqa: BLE001 — informational
            pass
        # H3: an edit is "after the build" when it is after the build's SOURCE CAPTURE (edits_seen_through_utc: an
        # edit made while the sync ran is not in the graph); a manifest from before auto-sync has only built_at_utc
        seen_through = data.get("edits_seen_through_utc") or data.get("built_at_utc")
        has_dirty = "dirty" in data or "built_at_dirty" in data
        dirty = data.get("dirty") if "dirty" in data else data.get("built_at_dirty")
        if head != built:
            return (f"graph built at {built[:7]} but HEAD is {head[:7]} — may be STALE; run "
                    f"/deep-map:refresh"
                    + (" (that build also included uncommitted changes)" if dirty is True else ""))
        if dirty is True:
            n = data.get("dirty_paths_count")
            sample = [str(x) for x in (data.get("dirty_paths_sample") or []) if str(x).strip()]
            what = (f"{n} uncommitted change(s)" if isinstance(n, int) and not isinstance(n, bool) and n > 0
                    else "uncommitted changes")
            edited = _edited_since_build(root, sample, seen_through)
            if not edited:
                n_after, edited = _uncommitted_since_build(root, seen_through, git)
            if edited:
                return (f"graph built from {what} on top of {built[:7]} (== HEAD), but {edited} changed "
                        f"after that build — may be STALE; run /deep-map:refresh")
            eg = f", e.g. {sample[0]}" if sample else ""
            return (f"graph built from {what} on top of {built[:7]} (== HEAD{eg}) — files edited after "
                    f"that sync are not in the graph; run /deep-map:refresh if you changed any since")
        if has_dirty and dirty is None:
            return (f"graph built at {built[:7]} == HEAD, but the work-tree state at build time is "
                    f"unknown (uncommitted edits may be missing) — run /deep-map:refresh to be sure")
        # a CLEAN build: uncommitted edits made since (git status now) are not in the graph either
        n_after, first = _uncommitted_since_build(root, seen_through, git)
        if n_after:
            return (f"graph built at {built[:7]} == HEAD, but {n_after} uncommitted source file(s) changed "
                    f"after that build (e.g. {first}) — may be STALE; run /deep-map:refresh")
        return f"graph built at {built[:7]} == HEAD (fresh as of the last commit)"
    except Exception:
        return None


def _sync_run_verdict(sync_mod, target, started: float):
    """The verdict of the sync that just ran, or None when unknowable: sync's own `last_verdict` (set
    by sync.sync for exactly this purpose), else the manifest — but ONLY if this run wrote it (a stale
    manifest from an earlier sync says nothing about this run). Never raises."""
    v = getattr(sync_mod, "last_verdict", None)
    if isinstance(v, str) and v.strip():
        return v.strip().upper()
    try:
        root = Path(target).resolve()
        try:   # a subdirectory of a repo synced higher up syncs THAT root (sync._sync_root) — read its manifest
            root = Path(sync_mod._sync_root(root))
        except Exception:
            pass
        mp = root / ".deepmap" / "graph_build.json"
        if mp.stat().st_mtime >= started - 2.0:
            v = _read_manifest(mp.parent.parent).get("verdict")
            return str(v).strip().upper() if v else None
    except Exception:
        pass
    return None


# Round 11 (M1): `sync` exits 4 when it ran but PUSHED NOTHING, leaving the previous graph in place (unchanged, maybe
# stale) — e.g. graphify ran out of time, sync fell back to ast and the engine-downgrade guard refused the push. It
# used to exit 0, so /deep-map:refresh, scripts and the git hook read success over a stale graph. Distinct from 1 (the
# sync refused / failed, or its verdict is FAILED: no usable graph from this run) and 3 (Neo4j unreachable, main):
# the kept graph still answers search/impact, so a caller can tell "stale but usable" from "broken". Mirrors
# sync.SYNC_NOT_PUSHED_RC (tests/sync_timeout_r11_gates.py locks the two together).
SYNC_NOT_PUSHED_RC = 4

_SYNC_EXIT_CODES = (
    "exit codes:\n"
    "  0  synced: READY, or DEGRADED with this run's graph pushed (also: a re-run was queued behind a sync\n"
    "     of this project that is already running — it includes your changes)\n"
    "  1  refused or failed: no safe route to this project's Neo4j, another project's graph, an index\n"
    "     failure, or verdict FAILED (this run produced no usable graph)\n"
    "  3  Neo4j unreachable / a database error\n"
    "  4  NOT PUSHED: the sync ran but pushed nothing — the previous graph is kept unchanged and may be\n"
    "     STALE (search/impact still answer from it). E.g. graphify ran out of time and the engine-\n"
    "     downgrade guard refused the push: raise DEEPMAP_GRAPHIFY_TIMEOUT and re-sync.\n"
    "\n"
    "--unattended=session-start (DeepMap's own background refresh): 0 = the graph is or will be current (no source\n"
    "  change, covered by a running sync, synced, queued, or handed to a sync you started); 1 = failed; 3 = the\n"
    "  database did not come up; 4 = not refreshed (skipped by policy, or not pushed). The outcome and its reason\n"
    "  are in .deepmap/autosync.json and .deepmap/autosync.log.\n"
    "\n"
    "env: DEEPMAP_GRAPHIFY_TIMEOUT=<seconds> — the graphify engine's time budget (default max(600, 1.5 s per\n"
    "source file), capped at 4 h; accepted 60-86400).")


def cmd_sync(args):
    if getattr(args, "unattended", None):
        # DeepMap's background refresh: auto_sync.run sets the offline environment and replays the build recipe BEFORE
        # sync is imported, then owns the run (its records; exit 0/1/3/4)
        if (getattr(args, "stale_cleanup", False) or getattr(args, "switch_project", False)
                or getattr(args, "force_prune", False)):
            print("[deepmap sync] --unattended never takes --stale-cleanup / --switch-project / --force-prune",
                  file=sys.stderr)
            return 2
        return _load("auto_sync").run(args.target, trigger=args.unattended, session=getattr(args, "session", None))
    sync = _load("sync")
    started = time.time()
    ok = sync.sync(args.target, cleanup_stale=args.stale_cleanup,
                   switch_project=args.switch_project,
                   force_prune=bool(getattr(args, "force_prune", False)))
    if not ok:
        return 1
    # A sync that ran but whose verdict is FAILED (0 code nodes, nothing searchable) is not a success:
    # exit non-zero so a script / the refresh command can't read it as one. DEGRADED stays 0 (usable).
    if _sync_run_verdict(sync, args.target, started) == "FAILED":
        print("[deepmap sync] verdict FAILED — this run produced no usable graph (see the verdict "
              "above); exiting non-zero.", file=sys.stderr)
        return 1
    # ... nor is one that pushed NOTHING over the graph it kept (M1): exit 4, never 0.
    why = getattr(sync, "last_not_pushed", None)
    if isinstance(why, str) and why.strip():
        print(f"[deepmap sync] NOT PUSHED — {why.strip()}. The graph was left as it was: search and impact "
              f"still answer from the previous build, which may be STALE. Exiting {SYNC_NOT_PUSHED_RC}.",
              file=sys.stderr)
        return SYNC_NOT_PUSHED_RC
    return 0


_FALSEY = {"0", "false", "no", "off"}
_EMBED_SERVER_TIMEOUT_S = 15.0   # a warm server answers in well under a second; env DEEPMAP_CLI_EMBED_TIMEOUT_S
_SLOW_EMBED_NOTE_S = 5.0         # an in-process embed slower than this is a model load: say so once


def _env_float(name: str, default: float) -> float:
    try:
        v = float(os.environ.get(name, "").strip() or default)
        return v if v >= 0 else default
    except (TypeError, ValueError):
        return default


def _embed_server_url(root):
    """THIS project's deepmap-embed server URL (T8d-X3) — an explicit DEEPMAP_EMBED_PORT, else the embed port
    the port registry gives this project — or None: DEEPMAP_CLI_EMBED_SERVER=0, no registered pair (the base
    default pair is never assumed: on a multi-project machine it is another project's), or an unusable port.
    A registry read only — no network. Never raises."""
    try:
        if str(os.environ.get("DEEPMAP_CLI_EMBED_SERVER", "")).strip().lower() in _FALSEY:
            return None
        port = None
        raw = str(os.environ.get("DEEPMAP_EMBED_PORT", "")).strip()
        if raw:
            port = int(raw)
        else:
            pair = _load("port_registry").lookup_ports(root)
            port = int(pair[1]) if pair else None
        if not port or not 0 < int(port) < 65536:
            return None
        host = str(os.environ.get("DEEPMAP_MCP_HOST", "127.0.0.1")).strip() or "127.0.0.1"
        if host in ("0.0.0.0", "::", "[::]"):   # a bind-all server is reached on loopback
            host = "127.0.0.1"
        return f"http://{host}:{int(port)}/mcp"
    except Exception:
        return None


def _mcp_http_post(url: str, body: dict, timeout: float) -> dict:
    """POST one JSON-RPC message to a streamable-HTTP MCP endpoint and return the JSON-RPC reply (a
    json_response server answers application/json; an SSE answer is read from its `data:` lines). DeepMap's
    servers are STATELESS, so a single tools/call needs no initialize handshake. Local only: no proxy.
    Raises on any transport / HTTP / parse failure."""
    import urllib.request
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json",
                                          "Accept": "application/json, text/event-stream"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=timeout) as resp:
        ctype = str(resp.headers.get("Content-Type") or "").lower()
        raw = resp.read().decode("utf-8", errors="replace")
    if "event-stream" in ctype:
        for line in raw.splitlines():
            if line.startswith("data:"):
                msg = json.loads(line[5:].strip())
                if isinstance(msg, dict) and msg.get("id") == body.get("id"):
                    return msg
        raise ValueError("no JSON-RPC reply in the event stream")
    msg = json.loads(raw)
    if not isinstance(msg, dict):
        raise ValueError("the JSON-RPC reply is not an object")
    return msg


def _embed_via_server(url: str, text: str, *, dim: int = 768, timeout: float | None = None) -> list:
    """The query vector from the deepmap-embed server at `url` (its frozen contract: `deepmap_embed(text)` ->
    768-d, 'search_query: ' prefix, the same model — so the vector is the one embeddings.embed_query computes
    in-process, returned losslessly: float32 values survive the JSON round trip). Raises unless the reply is a
    successful tool result holding exactly `dim` finite numbers."""
    import math
    t = _env_float("DEEPMAP_CLI_EMBED_TIMEOUT_S", _EMBED_SERVER_TIMEOUT_S) if timeout is None else float(timeout)
    msg = _mcp_http_post(url, {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                               "params": {"name": "deepmap_embed", "arguments": {"text": text}}}, t)
    if msg.get("error"):
        err = msg.get("error")
        raise RuntimeError(f"JSON-RPC error: {err.get('message') if isinstance(err, dict) else err}")
    res = msg.get("result")
    if not isinstance(res, dict):
        raise ValueError("no tool result")
    if res.get("isError"):
        texts = [str(c.get("text")) for c in (res.get("content") or []) if isinstance(c, dict) and c.get("text")]
        raise RuntimeError("the tool failed: " + (texts[0][:200] if texts else "isError"))
    vec = res.get("structuredContent")
    vec = vec.get("result") if isinstance(vec, dict) else None
    if vec is None:   # an older SDK without structured content: one text block per float
        vec = [c.get("text") for c in (res.get("content") or []) if isinstance(c, dict)]
        vec = [float(x) for x in vec]
    if not isinstance(vec, list) or len(vec) != int(dim):
        raise ValueError(f"expected a {dim}-d vector, got {len(vec) if isinstance(vec, list) else type(vec).__name__}")
    out = []
    for x in vec:
        if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x):
            raise ValueError("the vector holds a non-number")
        out.append(float(x))
    return out


def _is_refused(exc) -> bool:
    """True iff `exc` (or its cause / urllib reason) says nothing listens on the port. Never raises."""
    try:
        for e in _exc_chain(exc):
            if isinstance(e, ConnectionRefusedError) or isinstance(getattr(e, "reason", None), ConnectionRefusedError):
                return True
    except Exception:
        pass
    return False


def _query_embedder(root, embeddings):
    """The embed_fn `deepmap search` hands the retriever (T8d-X3). The CLI used to load the sentence-
    transformers model in-process on EVERY invocation (minutes on a loaded box) while this project's
    deepmap-embed server held it warm: the query now goes to that server (_embed_server_url — this project's
    registered port) and falls back to the in-process model on ANY failure, so the vector is the same either
    way and search never gets worse. A server that answered but failed is reported once on stderr; a server
    that is simply not running is silent. An in-process embed slower than DEEPMAP_CLI_SLOW_NOTE_S (5 s — a
    model load) prints one note saying why. The network is touched only when the retriever calls it."""
    url = _embed_server_url(root)
    dim = getattr(embeddings, "EMBED_DIM", 768) or 768
    state = {"server_ok": url is not None}

    def _in_process(text, why=""):
        note = ("[deepmap search] loading the embedding model in-process to embed the query — a cold load can "
                f"take a minute or more{why}; the MCP deepmap_search tool answers from a warm model")
        timer = threading.Timer(_env_float("DEEPMAP_CLI_SLOW_NOTE_S", _SLOW_EMBED_NOTE_S),
                                lambda: print(note, file=sys.stderr, flush=True))
        timer.daemon = True
        timer.start()
        try:
            return embeddings.embed_query(text)
        finally:
            timer.cancel()

    def embed(text):
        why = ""
        if state["server_ok"]:
            try:
                return _embed_via_server(url, text, dim=dim)
            except Exception as exc:  # noqa: BLE001 — the in-process model is always the fallback
                state["server_ok"] = False
                if _is_refused(exc):
                    why = " (this project's embed server is not running — /deep-map:setup restarts it)"
                else:
                    print(f"[deepmap search] this project's embed server ({url}) did not return a usable "
                          f"vector ({_short_error(exc)}) — embedding the query in-process instead",
                          file=sys.stderr, flush=True)
        return _in_process(text, why)
    return embed


def cmd_search(args):
    if not args.query or not args.query.strip():   # P2: reject a contentless query before Neo4j
        print("empty query — nothing to search (provide an intent phrase).")
        return 0
    # The shared query-length cap (the MCP boundary applies the same one): an over-long query is cut at a
    # word boundary with a note instead of spending tens of seconds embedding it. Called DIRECTLY — a
    # soft getattr lookup under a wrong name made this a silent no-op; a rename must now fail loudly.
    args.query, _q_note = mcp_bounds.cap_query(args.query)
    if _q_note:
        print(_q_note, file=sys.stderr)
    embeddings = _load("embeddings")
    R = _load("retrieval")
    top_k, _clamp_note = mcp_bounds.clamp_int(args.top_k, 1, mcp_bounds.TOP_K_MAX, "top-k")
    if _clamp_note:                             # clamp to the MCP boundary; tell the user we clamped
        print(_clamp_note, file=sys.stderr)     # (a negative/0/huge value must not reach Neo4j)
    embed_fn = None   # built once, AFTER the first driver resolves the route (a retry reuses the query vector)

    def _search_once():
        nonlocal embed_fn
        drv = _driver()
        try:                              # close the driver even if a query/connect raises
            drv.verify_connectivity()
            # RM-1 (gated, OFF by default): apply learned weights ONLY when the full gate passes
            # (env DEEPMAP_APPLY_LEARNED + applied + beats_equal + significant). Default -> None -> equal,
            # so retrieval.py's ranking is unchanged. Resolving is per-query (per-intent profile aware).
            try:
                arm_weights = _load("learned_fusion").resolve_arm_weights_for_live(query=args.query)
            except Exception:
                arm_weights = None
            # T8d-X3: the query is embedded by this project's warm deepmap-embed server when it answers, else
            # in-process (the same vector either way; the retriever and its ranking are unchanged).
            if embed_fn is None:
                embed_fn = _once_per_text(_query_embedder(_project_root(), embeddings))
            ret = R.DeepMapRetriever(drv, embed_fn=embed_fn, arm_weights=arm_weights)
            results = ret.search(args.query, top_k=top_k)
            # RM-1: opt-in local retrieval logging (off by default; no-op unless DEEPMAP_RETRIEVAL_LOG
            # is set). Observes only — ranking is unchanged. A logging failure must never break the
            # search, so the whole call is isolated (mirrors the MCP server path).
            try:
                _load("retrieval_log").maybe_log_search(ret, args.query, results, top_k=top_k)
            except Exception:
                pass
            # v0.27: bump this project's last_used (the idle-hibernation signal). Best-effort; never
            # affects the search result. Keyed on the project root, like the Neo4j route.
            try:
                _load("port_registry").touch_last_used(_project_root())
            except Exception:
                pass
            return results
        finally:
            drv.close()
    # ROUND-8 J6: a connection the server accepted and then dropped is retried ONCE (a read is idempotent).
    results = _read_retry("search", _search_once)
    shown = args.query if len(args.query) <= 120 else args.query[:117].rstrip() + "..."   # a capped
    noun = "result" if len(results) == 1 else "results"                                  # query is still long
    print(f"\n{len(results)} {noun} for {shown!r}:")
    for r in results:
        typ, name = str(r.get("type") or ""), str(r.get("name") or "")
        loc = f"{r.get('file') or '?'}:{r.get('line') if r.get('line') is not None else '?'}"
        score = r.get("score") or 0.0      # a NULL field must not crash the formatter (bug-hunt #15)
        print(f"  [{typ:8s}] {name:38s} {loc}  (score {score:.4f})")
        summ = " ".join(str(r.get("summary") or "").split())
        if len(summ) > 100:                # cut at a word boundary, and SAY it was cut
            summ = summ[:100].rsplit(" ", 1)[0].rstrip(" ,;:") + " …"
        if summ:
            print(f"             {summ}")
    return 0


def _known_edge_types() -> set:
    """The persisted relationship vocabulary `--edges` can name: CALLS + the typed framework/runtime
    edges (framework_edges.ALLOWED_EDGE_TYPES — the one whitelist). Empty set when unknowable (then no
    type is flagged as unknown). Never raises."""
    try:
        import framework_edges as _fe
        return {"CALLS"} | {str(t).upper() for t in _fe.ALLOWED_EDGE_TYPES}
    except Exception:
        return set()


def cmd_impact(args):
    """What REACHES this symbol — multi-hop reverse reachability across :CALLS + framework/runtime
    edges (routes/DI/events/jobs/inheritance), surfacing each dependent's hop depth, the wiring it
    arrives through (`via`), and confidence (the audit wedge).

    AMBIGUOUS edges (the name maps to several symbols, or a reflective/dynamic hop) are flagged so the
    answer is auditable, not asserted. Matches by bare name or Class.method suffix. A graph without typed
    edges (pre-Track-C) is walked over :CALLS alone, still multi-hop. The walk drops to one hop only on an
    error path, and a printed note says so: direct :CALLS callers after an unsupported-feature error or a
    server-side timeout, a 1-hop walk after a transaction-memory error (a hub name); a name too expensive
    even at 1 hop exits 3 with the retriever's remedy (ImpactTooExpensive).
    """
    R = _load("retrieval")
    name = (args.name or "").strip()   # a padded symbol (' foo ', a trailing newline) must still match
    if not name:                       # P2: reject a contentless symbol before Neo4j
        print("empty symbol — nothing to trace (provide a function/method/class name).")
        return 0
    limit, _clamp_note = mcp_bounds.clamp_int(args.limit, 1, mcp_bounds.IMPACT_LIMIT_MAX, "limit")
    if _clamp_note:                             # clamp to the MCP boundary; tell the user we clamped
        print(_clamp_note, file=sys.stderr)     # (no invalid LIMIT reaches Neo4j)
    max_hops = getattr(args, "max_hops", None)
    if max_hops is not None:                    # the retriever clamps silently; say so, like --limit
        max_hops, _hops_note = mcp_bounds.clamp_int(
            max_hops, 1, int(getattr(R, "_IMPACT_MAX_HOPS_CAP", 5) or 5), "max-hops")
        if _hops_note:
            print(_hops_note, file=sys.stderr)
    edge_types = None
    raw_edges = [t.strip() for t in (getattr(args, "edges", None) or "").split(",") if t.strip()]
    if raw_edges:
        known = _known_edge_types()
        unknown = [t for t in raw_edges if known and t.upper() not in known]
        if unknown and len(unknown) == len(raw_edges):
            # Nothing the user asked for exists: dropping them all would silently walk CALLS only (and the
            # retriever's note would call the request "empty"). A typo'd flag value is a usage error.
            print(f"deepmap impact: --edges names no known edge type ({', '.join(unknown)}). Known: "
                  f"{', '.join(sorted(known))} (CALLS is always traversed). Fix the spelling, or omit --edges "
                  "for the default walk.", file=sys.stderr)
            return 2
        if unknown:   # they would match nothing; drop them and say so instead of a silent no-op
            print(f"--edges: unknown edge type(s) {', '.join(unknown)} ignored "
                  f"(known: {', '.join(sorted(known))}; CALLS is always traversed).", file=sys.stderr)
        edge_types = []
        for t in raw_edges:   # the relationship-type spelling (the retriever upper-cases them too)
            if t not in unknown and t.upper() not in edge_types:
                edge_types.append(t.upper())
    # Even a 1-hop walk can exhaust Neo4j's transaction memory on a hub name: the retriever raises
    # ImpactTooExpensive (a RuntimeError, so main()'s DB-error handler would not see it) with a remedy.
    too_expensive = getattr(R, "ImpactTooExpensive", None) or ()

    def _impact_once():
        drv = _driver()
        try:                              # close the driver even if a query/connect raises
            drv.verify_connectivity()
            try:
                found = R.query_impact(drv, name, limit, edge_types=edge_types, max_hops=max_hops)
            except too_expensive as exc:
                return None, exc
            # this project's last_used (the idle-hibernation signal) — impact is a use, like search. Best-effort.
            try:
                _load("port_registry").touch_last_used(_project_root())
            except Exception:
                pass
            return found, None
        finally:
            drv.close()
    # ROUND-8 J6: a connection the server accepted and then dropped is retried ONCE (a read is idempotent).
    rows, costly = _read_retry("impact", _impact_once)
    if costly is not None:
        print(f"[deepmap] impact: {costly}", file=sys.stderr)
        return 3
    # The retriever's honesty notes (ImpactRows.notes): not in the graph vs in the graph with no resolved
    # dependents (NOT proof nothing uses it), truncated at --limit, or walked at reduced depth / 1 hop.
    notes = [str(n) for n in (getattr(rows, "notes", None) or []) if str(n).strip()]
    if not rows:
        for n in notes or [f"No dependents of {name!r} were found in the graph. That is NOT proof nothing "
                           "uses it (the symbol may not be indexed, or its callers are not resolved) — "
                           "verify with Grep or by reading the call sites."]:
            print(n)
        return 0
    print(f"\n{len(rows)} dependent(s) of {name!r} (reverse reachability; hop · via · confidence):")
    for r in rows:
        conf, caller = str(r.get("confidence") or ""), str(r.get("caller") or "")
        callee = str(r.get("callee") or "")
        loc = f"{r.get('file') or '?'}:{r.get('line') if r.get('line') is not None else '?'}"
        hop = r.get("hop")
        hoptag = f"{hop}h" if hop is not None else "1h"
        via = str(r.get("via") or "CALLS")
        print(f"  [{conf:9s}] {hoptag:>3s} via {via:12s} {caller:32s} -> {callee:22s} {loc}")
    amb = sum(1 for r in rows if r.get("confidence") == "AMBIGUOUS")
    if amb:
        print(f"\n  Note: {amb} edge(s) are AMBIGUOUS (the name maps to multiple symbols, or a "
              "reflective/dynamic hop) — verify by reading the source.")
    for n in notes:                       # truncated / reduced depth / one-hop fallback: say so
        print(f"\n  Note: {n}")
    return 0


def cmd_cypher(args):
    """Read-only Cypher over THIS project's graph — the deepmap_cypher MCP tool's twin: the same validator, READ
    transaction (always rolled back), server-side timeout (DEEPMAP_CYPHER_TIMEOUT_S), row / byte caps and result shape
    (build/cypher_readonly.py). A query the validator rejects, or --params that are not a JSON object, exits 2 before
    Neo4j is touched; a Neo4j error (syntax, timeout, an outage) is classified by main() and exits 3."""
    CR = cypher_readonly
    try:
        statement = CR.check_read_only(args.query)
        params = CR.check_params(json.loads(args.params) if args.params else None)
    except json.JSONDecodeError as exc:
        print(f"deepmap cypher: --params is not valid JSON ({exc}) — pass an object, e.g. "
              "--params '{\"name\": \"save\"}'.", file=sys.stderr)
        return 2
    except CR.CypherRejected as exc:
        print(f"deepmap cypher: rejected — {exc}", file=sys.stderr)
        return 2
    limit, _clamp_note = mcp_bounds.clamp_int(args.limit, 1, CR.LIMIT_MAX, "limit")
    if _clamp_note:                             # the MCP tool's cap; tell the user we clamped
        print(_clamp_note, file=sys.stderr)

    def _cypher_once():
        drv = _driver()                   # THIS project's graph (routing + owner checks) — never a raw driver
        try:
            drv.verify_connectivity()
            out = CR.run_read_only(drv, statement, params, limit=limit, timeout=CR.timeout_s())
            # a structural query is a use, like search and impact: bump last_used (idle hibernation). Best-effort.
            try:
                _load("port_registry").touch_last_used(_project_root())
            except Exception:
                pass
            return out
        finally:
            drv.close()
    # ROUND-8 J6: a connection the server accepted and then dropped is retried ONCE (a read is idempotent).
    rows, notes = _read_retry("cypher", _cypher_once)
    if args.json:
        print(json.dumps({"rows": rows, "notes": notes}, ensure_ascii=False, indent=2, default=str))
        return 0
    print(f"\n{len(rows)} row(s):")
    for row in rows:
        print("  " + json.dumps(row, ensure_ascii=False, default=str))
    for n in notes:
        print(f"\n  Note: {n}")
    return 0


def _instance_write_refusal(root, IS=None):
    """Why a write of <root>/.deepmap/instance.json just returned None, worded with what works for it (round 3,
    cov-build[4]: every refusal used to read as an unwritable directory — "Set DEEPMAP_LEARNING=1 in the env instead" /
    "non-repo or read-only" — although the writers also refuse a file that is not a JSON object rather than wipe it,
    and give up after a bounded wait while another DeepMap process holds the write lock). instance_state.write_problem
    (`IS`, the module the write went through, or a fresh load) names the cause without writing. Returns None when the
    directory itself is unwritable, the root unusable, or the cause cannot be asked — each caller keeps its own wording
    for those — else the reason + the remedy, one clause. Never raises."""
    try:
        why = (IS if IS is not None else _load("instance_state")).write_problem(root)
    except Exception:
        return None
    if why in ("unwritable", "bad-root"):
        return None
    if why == "unparseable":
        return ("it is not a JSON object, so it was left untouched — overwriting it would wipe its other settings: fix "
                "or delete it, then re-run")
    if why == "locked":
        return "another DeepMap process is writing it (it holds the file's write lock) — retry in a moment"
    if why == "unreadable":
        return "it could not be read (another program may be holding it open) — retry in a moment"
    return ("nothing blocks the write now (most likely another DeepMap process was writing it) — retry; if it keeps "
            f"failing, check that {Path(root) / '.deepmap'} is writable and its disk has room")


def cmd_learning(args):
    """One-command enable for the OPT-IN, offline, zero-egress local learning loop.

    `--enable` flips the DURABLE project flag (.deepmap/instance.json learned_fusion.learning_enabled)
    so retrieval logging + the offline post-sync tuner turn on WITHOUT needing DEEPMAP_LEARNING in the
    env each session, then prints the copy-paste access-hook registration snippet (the hook is opt-in
    and is NEVER auto-installed). It does NOT activate learned weights — live ranking stays
    equal-weight until the separate G4 gate opens. No Neo4j; nothing leaves the machine.
    """
    # The PROJECT root (the outermost synced ancestor — retrieval_log.project_root), not the raw cwd: from
    # a subdirectory the flag must land in — and be read from — the same .deepmap/ search logging uses.
    root = _project_root()
    LL = _load("learning_loop")
    env_learning = str(os.environ.get("DEEPMAP_LEARNING", "")).strip().lower()
    vetoed = env_learning in {"0", "false", "no", "off"}
    target = root / ".deepmap" / "instance.json"
    if getattr(args, "enable", False):
        IS = _load("instance_state")
        path = IS.merge_learned_fusion(root, {"learning_enabled": True})
        if path is None:
            why = _instance_write_refusal(root, IS)
            print(f"Could not write {target}: {why}." if why else
                  f"Could not write {target} (not a writable project dir). Set DEEPMAP_LEARNING=1 in the env instead.")
            return 1
        if vetoed:   # say it FIRST: the flag is written, but learning stays off while the env vetoes it
            print(f"Project flag ENABLED ({path}) — but DEEPMAP_LEARNING="
                  f"{os.environ.get('DEEPMAP_LEARNING')} in your environment VETOES it: learning stays "
                  "OFF until you unset that variable.")
        else:
            print(f"Learning ENABLED for this project "
                  f"(durable: {path} learned_fusion.learning_enabled=true).")
        print(("  -> once the veto is gone, retrieval logging + the offline post-sync tuner turn on. "
               if vetoed else "  -> retrieval logging + the offline post-sync tuner are now on. ")
              + "Live ranking is")
        print("     UNCHANGED (equal-weight) — learned weights never apply until the benchmark shows they beat "
              "equal weights.")
        if not vetoed:
            print("  note: an explicit DEEPMAP_LEARNING=0 in your environment still vetoes this flag.")
        hook = str((HERE.parent / "hooks" / "deepmap_access_hook.py").resolve()).replace("\\", "/")
        interp = _python_interpreter().replace("\\", "/")
        interp_q = f'"{interp}"' if " " in interp else interp   # quote a spaced venv/interp path
        # The hook grades Read as a weak and Edit/Write/MultiEdit/NotebookEdit as a strong access (its
        # _FILE_TOOLS). Claude Code reads a matcher holding any character beyond letters, digits, `_ - , |` and
        # spaces (here the `.*`) as an UNANCHORED regex, so `Edit` alone already matches MultiEdit and
        # NotebookEdit: the shorter `Read|Edit|Write|.*deepmap_impact` (the hook's docstring, docs/RM1-ENABLE.md)
        # drops no label. The two names are listed so the matcher still covers them if the `.*deepmap_impact`
        # alternative ever goes (with no regex character left it would be a list of EXACT tool names).
        snippet = {"hooks": {"PostToolUse": [
            {"matcher": "Read|Edit|Write|.*deepmap_impact|MultiEdit|NotebookEdit",
             "hooks": [{"type": "command", "command": f'{interp_q} "{hook}"'}]}]}}
        print("\nTo capture WHICH results you act on (the implicit relevance labels the tuner learns")
        print("from), add this PostToolUse hook to your Claude Code settings.json — opt-in; DeepMap")
        print("never installs it for you:\n")
        print(json.dumps(snippet, indent=2))
        print("\n  note: that hook path is THIS plugin version's copy — after a plugin update, re-run")
        print(f"        {_cli_cmd('learning --enable')} and replace the command with the new path.")
        print(f"\n  learning: {LL.accrual_line(root)}")
        return 0
    if getattr(args, "disable", False):
        IS = _load("instance_state")
        path = IS.merge_learned_fusion(root, {"learning_enabled": False})
        if path is None:
            why = _instance_write_refusal(root, IS)
            print(f"Could not write {target}: {why}." if why else
                  f"Could not write {target} (not a writable project dir).")
            return 1
        print(f"Learning DISABLED for this project ({path} learned_fusion.learning_enabled=false).")
        if env_learning in {"1", "true", "yes", "on"}:
            print("  note: DEEPMAP_LEARNING is set truthy in your environment and still forces "
                  "learning ON regardless of this flag — unset it to fully disable.")
        return 0
    # --status / no flag: honest one-line snapshot (works with no log -> 0/150).
    st = LL.learning_status(root)
    state = "ON" if st.get("enabled") else "OFF"
    src = st.get("source")
    frag = LL.labeled_fragment(st.get("n_labeled"), st.get("target_labeled") or 150)
    print(f"learning: {state}" + (f" (source: {src})" if src else "") + f" — {frag}")
    if not st.get("enabled"):
        if st.get("vetoed") or vetoed:   # `--enable` cannot help while the env veto stands
            flag = " (which is ENABLED)" if st.get("instance_flag") is True else ""
            print(f"  DEEPMAP_LEARNING={os.environ.get('DEEPMAP_LEARNING')} in your environment vetoes "
                  f"the project flag{flag} — unset it to turn learning on.")
        else:
            print(f"  enable with:  {_cli_cmd('learning --enable')}")
    return 0


_EDGE_MIX_QUERY = ("MATCH ()-[r]->() RETURN type(r) AS t, coalesce(toString(r.confidence), '') AS c, "
                   "count(*) AS n")
_FANOUT_QUERY = ("MATCH (s)-[r:CALLS]->(t) WHERE r.confidence = 'AMBIGUOUS' "
                 "WITH s, split(coalesce(t.name, ''), '.')[-1] AS nm, count(DISTINCT t) AS k "
                 "RETURN nm, max(k) AS fan ORDER BY fan DESC, nm LIMIT 1")
_EDGE_MIX_TIMEOUT_S = 20.0
_CONF_ORDER = ("EXTRACTED", "INFERRED", "AMBIGUOUS")


def _bounded(text: str):
    """`text` as a neo4j Query with a server-side timeout (a census must not hang doctor), else the text."""
    try:
        from neo4j import Query
        return Query(text, timeout=_EDGE_MIX_TIMEOUT_S)
    except Exception:
        return text


def _edge_mix(session):
    """What the graph's edges actually are (T1#4 at-minimum / the 'engine + edge-confidence mix' ask): {types:
    {type: n}, calls: {confidence: n}, fanout: (name, n) | None}, or None when the session can't answer (an
    old / stubbed / overloaded instance — the caller prints nothing). One grouped scan + one bounded AMBIGUOUS
    fan-out probe. Never raises."""
    try:
        types_, calls = {}, {}
        for row in session.run(_bounded(_EDGE_MIX_QUERY)):
            t, c, n = row.get("t"), row.get("c"), row.get("n")
            if not isinstance(t, str) or not t or isinstance(n, bool) or not isinstance(n, int) or n < 0:
                continue
            types_[t] = types_.get(t, 0) + n
            if t == "CALLS":
                key = str(c or "").upper() or "UNGRADED"
                calls[key] = calls.get(key, 0) + n
        if not types_:
            return None
        fanout = None
        if calls.get("AMBIGUOUS"):
            try:
                rows = list(session.run(_bounded(_FANOUT_QUERY)))
                if rows:
                    nm, fan = rows[0].get("nm"), rows[0].get("fan")
                    if isinstance(nm, str) and nm and isinstance(fan, int) and not isinstance(fan, bool) and fan > 1:
                        fanout = (nm, fan)
            except Exception:
                fanout = None
        return {"types": types_, "calls": calls, "fanout": fanout}
    except Exception:
        return None


def _calls_mix_text(calls: dict) -> str:
    """'EXTRACTED 5% · INFERRED 25% · AMBIGUOUS 70%' (any other grade after them). Never raises."""
    try:
        total = sum(calls.values()) or 1
        keys = [k for k in _CONF_ORDER if calls.get(k)] + sorted(k for k in calls if k not in _CONF_ORDER)
        return " · ".join(f"{k} {round(100.0 * calls[k] / total)}%" for k in keys)
    except Exception:
        return ""


def _edge_mix_detail(mix: dict):
    """(status, detail) for doctor's `edges` line: the CALLS count + its confidence shares, the typed-edge
    counts, and — when most CALLS edges are AMBIGUOUS (a called name matches several definitions) — a WARN that
    impact / the graph arm over-connect there, naming the widest fan-out. Informational: never a verdict reason.
    Never raises."""
    try:
        types_ = mix.get("types") or {}
        calls = mix.get("calls") or {}
        n_calls = types_.get("CALLS", 0)
        parts = []
        if n_calls:
            parts.append(f"CALLS {n_calls:,} ({_calls_mix_text(calls)})")
        typed = sorted(((t, n) for t, n in types_.items() if t != "CALLS"), key=lambda x: (-x[1], x[0]))
        if typed:
            parts.append("typed: " + ", ".join(f"{t} {n:,}" for t, n in typed))
        detail = "; ".join(parts) or "no edges"
        amb = calls.get("AMBIGUOUS", 0)
        if n_calls and amb * 2 > n_calls:
            fan = mix.get("fanout")
            widest = f" (widest: a call to '{fan[0]}' matches {fan[1]} definitions)" if fan else ""
            detail += (f" — most CALLS edges are AMBIGUOUS{widest}: deepmap_impact and the graph-weighted arm "
                       "over-connect there, so verify callers by reading the call site (DEEPMAP_PRECISE_CALLS=1 is "
                       "the opt-in fix on the graphify path)")
            return "WARN", detail
        return "OK", detail
    except Exception:
        return "OK", ""


def cmd_status(args):
    # RM-1 accrual (offline, zero-egress) — printed BEFORE the Neo4j connect so it shows even when the
    # instance is down, and never breaks status.
    root = _project_root()
    try:
        print(f"learning: {_load('learning_loop').accrual_line(root)}")
    except Exception:
        pass
    try:   # DeepMap's background refresh: file-only, so it shows even when the instance is down
        _as_level, _as_text = _load("auto_sync").status_line(root)
        print(f"auto-sync: {_as_text}" if _as_level == "OK" else f"auto-sync: [{_as_level}] {_as_text}")
    except Exception:
        pass
    def _census():
        drv = _driver()
        try:                              # close the driver even if a query/connect raises
            drv.verify_connectivity()
            with drv.session() as s:
                # Exclude the :_DeepMapMeta ownership singleton from the node census (it's bookkeeping, not
                # a code node) so counts match the real graph.
                n_nodes = s.run("MATCH (n) WHERE NOT n:_DeepMapMeta RETURN count(n) AS c").single()["c"]
                n_edges = s.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]
                n_emb = s.run("MATCH (n) WHERE n.embedding IS NOT NULL RETURN count(n) AS c").single()["c"]
                own = list(s.run("MATCH (m:_DeepMapMeta) RETURN m.owning_root AS r LIMIT 1"))
                indexes = list(s.run("SHOW INDEXES YIELD name, type, state WHERE name STARTS WITH 'deepmap_' "
                                     "RETURN name, type, state ORDER BY type, name"))
                return n_nodes, n_edges, n_emb, own, indexes, _edge_mix(s)   # the mix is best-effort (None)
        finally:
            drv.close()
    # ROUND-8 J6: a connection the server accepted and then dropped is retried ONCE (a read is idempotent).
    nodes, edges, embedded, owner, idx, mix = _read_retry("status", _census)
    owner_root = owner[0]["r"] if owner else None
    foreign = _foreign_owner(root, owner_root)
    print(f"DeepMap status @ {_uri_for_message()}{_route_label()}")
    print(f"  owner={owner_root or '—'}" + ("  (NOT this project — search/impact here answer about that "
                                            "project)" if foreign else ""))
    print(f"  nodes={nodes}  edges={edges}  embedded={embedded}")
    if mix:
        typed = sorted((mix.get("types") or {}).items(), key=lambda x: (x[0] != "CALLS", -x[1], x[0]))
        print("  edges by type: " + "  ".join(f"{t}={n}" for t, n in typed))
        if mix.get("calls"):
            print(f"  CALLS confidence: {_calls_mix_text(mix['calls'])}")
    print(f"  indexes ({len(idx)}):")
    for r in idx:
        print(f"     {r['name']:32s} {r['type']:9s} {r['state']}")
    try:   # a newer Neo4j patch release for this project's store (provision's file reads; informational)
        _note = _load("provision").neo4j_upgrade_note_for(root)
        if _note:
            print(f"  {_note}")
    except Exception:
        pass
    # Per-language coverage from the last sync's manifest (best-effort; reflects the last sync of
    # the CURRENT directory's project — the same marker file the hooks gate on).
    cov_lines = _manifest_coverage_lines(root)
    if cov_lines:
        print("  coverage (as of last sync):")
        for ln in cov_lines:
            print(f"     {ln}")
    stale = _staleness_line(root)
    if stale:
        print(f"  freshness: {stale}")
    return 0


def cmd_version(args):
    """Print the DeepMap version, read from the plugin manifest (the single source of truth).

    No Neo4j needed. Never crashes on a missing/garbled manifest — prints 'unknown' instead.
    """
    import json
    manifest = HERE.parent / ".claude-plugin" / "plugin.json"
    ver = None
    try:
        ver = json.loads(manifest.read_text(encoding="utf-8")).get("version")
    except Exception:  # noqa: BLE001 — a version probe must report, not crash
        ver = None
    print(f"deepmap {ver}" if ver else "deepmap (version unknown — .claude-plugin/plugin.json not found)")
    return 0


def _root_arg(args, cmd: str):
    """(root, None) for an offline subcommand's optional <root>: an explicit root must be an EXISTING
    directory (a typo must not silently create <typo>/.deepmap/ or report an empty project), and it maps
    through the SAME project-root rule as the cwd (retrieval_log.project_root — the outermost synced
    directory up to the git toplevel, i.e. the root sync itself syncs): `/deep-map:enrich` passes "."
    from a subdirectory, and a sidecar written to <subdir>/.deepmap/ is one the next sync never reads.
    Omitted -> the project root resolved from the cwd. (None, 2) + a stderr message on a bad root."""
    raw = getattr(args, "root", None)
    if not raw:
        return _project_root(), None
    p = Path(raw)
    if not p.is_dir():
        print(f"deepmap {cmd}: no such directory: {raw}", file=sys.stderr)
        return None, 2
    try:
        import retrieval_log as _rl
        mapped = _rl.project_root(p)
        if mapped is not None:
            return Path(mapped), None
    except Exception:
        pass
    return p, None


_BARE_CMD_RE = re.compile(r"`deepmap ([a-z][\w-]*)((?: [^`]*)?)`")
_ROOT_ARG_CMDS = {"tune", "insights", "enrich"}   # the offline subcommands that take an explicit <root>


def _runnable_text(text, root=None) -> str:
    """`text` with every `deepmap <sub> ...` hint rewritten as the runnable command (the absolute deepmap_cli.py
    path + the running interpreter — no `deepmap` executable is installed, D6), aimed at `root` for the offline
    subcommands, and the benchmark's relative `python benchmark/run_benchmark.py` made absolute. For messages
    the frozen modules word (learned_fusion.gate_reasons, tune_weights.apply_learned, learned_prior) — the CLI
    rewrites them on the way out instead of editing those files. Never raises."""
    try:
        def _sub(m):
            name, rest = m.group(1), m.group(2) or ""
            head = _q_root(name, root) if (root is not None and name in _ROOT_ARG_CMDS) else name
            return _cli_cmd(head + rest)
        out = _BARE_CMD_RE.sub(_sub, str(text))
        bench = (HERE.parent / "benchmark" / "run_benchmark.py").as_posix()
        return re.sub(r"\bpython benchmark/run_benchmark\.py", lambda _m: f'"{_interp()}" "{bench}"', out)
    except Exception:
        return str(text)


# retrieval.RRF_K — the k live search ALWAYS fuses at (frozen; hash-locked by tests/fts_r7_gates.py), named here for
# tune's help and output without importing retrieval on every CLI start; tests/f1_cli_followups_r283_gates.py locks the
# two together. A `tune --tune-rrf-k` k is recorded as a RECOMMENDATION only: tune_weights judges every learned-vs-equal
# comparison (CV significance, per-slice, profile rows, the drift guard) at RRF_K, the k live search uses.
_LIVE_RRF_K = 60


def _rrf_k_text(k) -> str:
    """A recorded rrf_k as tune prints it: the live k bare (the output is unchanged), any other k — a --tune-rrf-k
    result — labelled a recommendation, so it never reads as the k live search would use. Never raises."""
    try:
        if k is not None and not isinstance(k, bool) and int(k) != _LIVE_RRF_K:
            return (f"{k} — a recommendation only; live search and every learned-vs-equal judgement use "
                    f"k={_LIVE_RRF_K}")
    except (TypeError, ValueError):
        pass
    return f"{k}"


def cmd_tune(args):
    """RM-1 OFFLINE learned-fusion-weight tuner. Reads the opt-in local retrieval log
    (<root>/.deepmap/retrieval_log.jsonl), tunes per-arm Weighted-RRF weights (graded objective,
    per-intent PROFILES, k-fold CV + bootstrap significance) with cold-start safety, and writes
    <root>/.deepmap/instance.json (learned_fusion, applied:false).

    NO Neo4j, NO network, NO spend — pure log analysis. The learned weights are NOT applied to
    live ranking: build/retrieval.py uses equal weights until gate G4 (learned > equal, statistically
    significant, on the benchmark). This command is a data producer only.

        deepmap tune [<root>] [--min-queries N] [--objective ndcg|map|mrr] [--optimizer grid|coord]
                     [--tune-rrf-k] [--no-profiles] [--min-queries-bucket N] [--dry-run]
        deepmap tune [<root>] --status    # show the current learned weights + profiles
    """
    root, rc = _root_arg(args, "tune")
    if rc is not None:
        return rc
    for flag, value in (("--min-queries", getattr(args, "min_queries", None)),
                        ("--min-queries-bucket", getattr(args, "min_queries_bucket", None))):
        if value is not None and value < 1:   # a 0/negative cold-start threshold would "tune" on nothing
            print(f"deepmap tune: {flag} must be >= 1 (got {value})", file=sys.stderr)
            return 2
    T = _load("tune_weights")
    L = _load("retrieval_log")
    IS = _load("instance_state")

    if getattr(args, "status", False):
        inst = root / ".deepmap" / L.INSTANCE_FILENAME
        recs = L.read_records(root)
        n_search = sum(1 for r in recs if r.get("type") == "search")
        n_labeled = len(T.build_qrels(recs))
        print(f"deepmap tune --status  ({root})")
        try:   # the SAME line doctor prints — one source of truth for the loop's state
            LL = _load("learning_loop")
            _st, _detail = LL.format_status_line(LL.learning_status(root))
            print(f"  learning: {_detail}")
        except Exception:
            pass
        print(f"  retrieval log: {n_search} search records, {n_labeled} labeled (>=1 accessed)")
        print(f"  logging enabled: {L.logging_enabled(root)}")
        lf = None
        try:
            lf = json.loads(inst.read_text(encoding="utf-8")).get("learned_fusion")
        except Exception:
            lf = None
        if not IS.has_tune_result(lf):   # a `learning --enable` block is not a tune (no wall of None)
            print(f"  learned weights: none yet (run {_cli_cmd(_q_root('tune', root))} after collecting "
                  "labeled queries)")
            return 0
        print(f"  learned weights: {lf.get('arm_weights')}  (applied={lf.get('applied')}, "
              f"beats_equal={lf.get('beats_equal')}, significant={lf.get('significant')}, "
              f"objective={lf.get('objective')}, reason={lf.get('reason')})")
        prof = lf.get("profiles") or {}
        if prof:
            print("  per-intent profiles (applied only if the gate opens):")
            for bucket, p in prof.items():
                print(f"     [{bucket:12s}] {p.get('arm_weights')}  (n={p.get('n_labeled')})")
        seed = lf.get("seed")
        if isinstance(seed, dict):
            print(f"  seed: recommendation from the user prior ({seed.get('n_projects')} project(s); "
                  f"never read by live ranking — tuner warm-start only)")
        print(f"  log summary: {_cli_cmd(_q_root('insights', root))}")
        return 0

    if getattr(args, "report", False):
        LF = _load("learned_fusion")
        lf = None
        try:
            lf = json.loads((root / ".deepmap" / L.INSTANCE_FILENAME).read_text(encoding="utf-8")).get("learned_fusion")
        except Exception:
            lf = None
        print(f"deepmap tune --report  ({root})   [benchmark evidence]")
        if not IS.has_tune_result(lf):
            print(f"  no learned_fusion tune yet — run {_cli_cmd(_q_root('tune', root))} after collecting "
                  "labeled queries.")
            return 0
        print(f"  objective={lf.get('objective')}  weights={lf.get('arm_weights')}  "
              f"rrf_k={_rrf_k_text(lf.get('rrf_k'))}")
        print(f"  beats_equal={lf.get('beats_equal')}  significant={lf.get('significant')}  "
              f"labeled={lf.get('n_labeled_queries')}  folds={lf.get('n_folds')}")
        bs = lf.get("bootstrap") or {}
        print(f"  CV mean delta {lf.get('cv_mean_delta')}  95% CI [{bs.get('ci_lo')}, {bs.get('ci_hi')}]")
        print(f"  per-slice deltas: {lf.get('per_slice_delta')}")
        be = lf.get("benchmark_evidence")
        if not be:
            # ISS-benchmark:012: the evidence comes from benchmark/weight_ab.py's G4 A/B — run_benchmark.py
            # --ab-weights only PRINTS that plan. Named in the words `tune --apply`'s refusal uses
            # (tune_weights._G4_PROCEDURE), so the two never disagree; the absolute path is the fallback.
            proc = getattr(T, "_G4_PROCEDURE", None)
            if not (isinstance(proc, str) and proc.strip()):
                proc = (f'the learned-vs-equal benchmark A/B in "{(HERE.parent / "benchmark" / "weight_ab.py").as_posix()}" '
                        "(see its ab_env)")
            be = f"none yet — run {proc}"
        print(f"  benchmark_evidence: {be}")
        reasons = LF.gate_reasons(root)
        print(f"  live-apply gate: {'WOULD APPLY (all conditions met)' if not reasons else 'BLOCKED'}")
        for r in reasons:
            print(f"     - {_runnable_text(r, root)}")
        return 0

    if getattr(args, "apply", False):
        ok, reason = T.apply_learned(root, require_benchmark=True)
        print(f"deepmap tune --apply  ({root})")
        print(f"  {'APPLIED' if ok else 'REFUSED'}: {_runnable_text(reason, root)}")
        if ok:
            print("  live search uses learned weights only when DEEPMAP_APPLY_LEARNED is set "
                  "(opt-in; still local).")
        return 0 if ok else 1

    if getattr(args, "export_prior", False):
        LP = _load("learned_prior")
        path, reason = LP.export_prior(root)
        print(f"deepmap tune --export-prior  ({root})")
        print(f"  {('EXPORTED -> ' + str(path)) if path else ('REFUSED: ' + _runnable_text(reason, root))}")
        if path:
            print("  (local machine only — a recommendation input for other projects' tuners; "
                  "never read by live ranking)")
        return 0 if path else 1

    if not args.dry_run:
        try:   # drift guard (RM-1): never silently keep a stale learned profile — a manual tune
               # re-validates the STANDING weights on the newest slice before replacing them.
            d = T.apply_drift_guard(root)
            if d.get("reverted"):
                print(f"  drift guard: REVERTED stale learned weights to equal "
                      f"(newest-slice mean_delta={d.get('mean_delta') or 0.0:.4f}; "
                      f"recorded under learned_fusion.drift)")
        except Exception:
            pass
    pooled = None
    if getattr(args, "seed_from_prior", False):
        try:   # explicit consume: warm-start from the user prior (~/.deepmap/learned_prior.json)
            LP = _load("learned_prior")
            pooled = LP.pooled_prior(LP.load_prior(), exclude_root=root)
            print("  seed prior: " + (f"pooled from {pooled.get('n_projects')} project(s)"
                                      if pooled else "none available (~/.deepmap/learned_prior.json)"))
        except Exception:
            pooled = None
    # None (flag omitted) -> the canonical tuner thresholds; keeps the CLI in lockstep with
    # tune_weights.DEFAULT_MIN_LABELED / DEFAULT_MIN_LABELED_BUCKET instead of re-declaring 150/40
    # (the same drift-lock insights.py uses; enforced by tests/tune_cli_gates.py).
    _min_labeled = args.min_queries if args.min_queries is not None else T.DEFAULT_MIN_LABELED
    _min_labeled_bucket = (args.min_queries_bucket if args.min_queries_bucket is not None
                           else T.DEFAULT_MIN_LABELED_BUCKET)
    res = T.tune_profiles(root=root, min_labeled=_min_labeled, objective=args.objective,
                          optimizer=args.optimizer, tune_rrf_k=args.tune_rrf_k,
                          with_profiles=not args.no_profiles, min_labeled_bucket=_min_labeled_bucket,
                          seed_prior=(pooled or {}).get("arm_weights"))
    print(f"deepmap tune  ({root})")
    n_lab = res["n_labeled_queries"]
    # the cold-start threshold the tuner applied — below it the weights stay equal, so SAY how far away it is
    lab = (f"{n_lab}/{_min_labeled} (below the cold-start threshold — weights stay equal until {_min_labeled})"
           if res.get("reason") == "below_threshold" else f"{n_lab} (threshold {_min_labeled})")
    print(f"  labeled queries: {lab}   objective: {res['objective']} (rrf_k={_rrf_k_text(res['rrf_k'])})")
    print(f"  reason: {res['reason']}   beats_equal: {res['beats_equal']}   significant: {res['significant']}")
    bs = res.get("bootstrap") or {}
    if res.get("n_folds"):
        print(f"  {res['n_folds']}-fold CV mean delta {res.get('cv_mean_delta', 0.0):.4f}  "
              f"95% CI [{bs.get('ci_lo', 0.0):.4f}, {bs.get('ci_hi', 0.0):.4f}]")
    print(f"  weights: {res['arm_weights']}   (applied=False — NOT live until the benchmark shows learned weights "
          "beat equal weights)")
    for bucket, p in (res.get("profiles") or {}).items():
        print(f"  profile[{bucket}]: {p.get('arm_weights')}  (n={p.get('n_labeled')}, beats_global)")
    if args.dry_run:
        print("  --dry-run: instance.json NOT written")
        return 0
    out = T.write_instance_weights(root, res)
    if out:
        print(f"  wrote: {out}")
    else:
        why = _instance_write_refusal(root)
        print(f"  (could not write instance.json — {why or 'non-repo or read-only'}; result above stands)")
    if pooled and res.get("reason") == "below_threshold":
        try:   # cold project: record the pooled prior as a RECOMMENDATION (never live)
            LP = _load("learned_prior")
            if LP.write_seed_recommendation(root, pooled):
                print("  seed: recorded the pooled prior under learned_fusion.seed (recommendation only)")
        except Exception:
            pass
    return 0


def cmd_insights(args):
    """LOCAL, read-only summary of the opt-in retrieval log (<root>/.deepmap/retrieval_log.jsonl).
    NO Neo4j, NO network, writes NOTHING — reports n everywhere and refuses conclusions below the
    tuner's cold-start thresholds (tune_weights.DEFAULT_MIN_LABELED / DEFAULT_MIN_LABELED_BUCKET).

        deepmap insights [<root>] [--json] [--since 2026-07-01]
    """
    root, rc = _root_arg(args, "insights")
    if rc is not None:
        return rc
    I = _load("insights")
    data = I.insights_for_root(root, since=getattr(args, "since", None))
    if getattr(args, "json", False):
        print(json.dumps(data, indent=2, sort_keys=True))
        return 0
    print(I.render_text(data))
    return 0


# F1 (r283): `enrich` exits 4 when it REFUSED before extracting (build/enrich.py: stats['refused'] / last_refusal) —
# nothing was extracted, sent, spent or written, and enrich's one stderr line names the cause: a sync (or another
# enrich) of this project holds the project's lock, the lock cannot be opened (e.g. .deepmap is a file, or permission
# is denied), or <root> is not a directory (the CLI checks it first, so only a directory that vanished meanwhile).
# Only the first passes by waiting (round 3, cov-build[7]). 4 is the repo's "did nothing, the previous state is kept"
# code (sync's NOT PUSHED). It used to exit 0 and then print summary lines that read like a finished run
# ("undocumented candidates: 0", "Nothing new to apply", "Run /deep-map:refresh"); now the refusal line is the whole
# answer.
ENRICH_REFUSED_RC = 4

_ENRICH_EXIT_CODES = (
    "exit codes:\n"
    "  0  ran: the printed counts say what was enriched, skipped or is left\n"
    "  2  refused before any work: a bad argument or <root>, or the LLM tier without its opt-in\n"
    "     (DEEPMAP_ENRICH=1) or --confirm-spend\n"
    "  4  REFUSED before extracting — nothing was extracted, sent, spent or written. enrich's stderr line\n"
    "     names the cause: a sync (or another enrich) of this project is running and holds its lock (re-run\n"
    "     the same command once that run finishes); or the lock cannot be opened (e.g. .deepmap is a file,\n"
    "     or permission is denied), or <root> is not a directory — fix what it names, then re-run")


def _enrich_refusal(E, res=None):
    """Why this enrich did nothing at all — enrich()'s stats['refused'], else the module's last_refusal (a sync or
    another enrich holds the project's lock, the lock cannot be opened, or <root> is not a directory) — or None when
    it ran. Read defensively: an enrich module without that channel (an older build) never refuses here. Never
    raises."""
    try:
        why = res.get("refused") if isinstance(res, dict) else None
        return why or getattr(E, "last_refusal", None) or None
    except Exception:
        return None


def cmd_enrich(args):
    """OPT-IN LLM intent-summary enrichment for UNDOCUMENTED code (zero-egress BY DEFAULT).

    Uses your EXISTING Claude session (`claude -p --model <model>`, ~$0 on the plan, no API key, no new
    service) to write one-sentence behavioral summaries for undocumented nodes into a gitignored sidecar
    (<root>/.deepmap/summaries.jsonl) that `deepmap sync` reads to override body_summary. The LLM path is
    OFF unless DEEPMAP_ENRICH=1 (opt-in) AND --confirm-spend (it consumes plan quota). --dry-run /
    --status / --deterministic spend nothing and send nothing anywhere.

        deepmap enrich [<root>] [--model claude-sonnet-5] [--max-nodes N] [--batch-size N]
                       [--max-total-usd F] [--include-modules] [--confirm-spend]
        deepmap enrich [<root>] --deterministic   # richer LOCAL summaries: no LLM, no network, no spend
        deepmap enrich [<root>] --status          # sidecar state (no extraction, no spend)
        deepmap enrich [<root>] --dry-run         # count candidates + priority (no spend)

    Exits ENRICH_REFUSED_RC (4) with no summary when enrich REFUSED before extracting — nothing was extracted, sent,
    spent or written, and enrich's own stderr line names the cause: a sync (or another enrich) of the project holds its
    lock (re-run once that run finishes), the lock cannot be opened, or <root> is not a directory (fix what it names).
    """
    root, rc = _root_arg(args, "enrich")
    if rc is not None:
        return rc
    E = _load("enrich")

    if getattr(args, "status", False):
        st = E.status(root)
        print(f"deepmap enrich --status  ({root})")
        print(f"  opt-in enabled: {st['enabled']}   sidecar: {st['sidecar']}")
        print(f"  cached summaries: {st['cached']}   by model: {st['by_model'] or '{}'}")
        return 0

    if getattr(args, "dry_run", False):
        nodes = E.iter_enrichable_nodes(root, include_modules=args.include_modules)
        if _enrich_refusal(E):   # enrich refused and said why on stderr — "0 candidates" would be false
            return ENRICH_REFUSED_RC
        sc = E.load_sidecar(root)
        done = sum(1 for n in nodes if sc.get(n["node_id"], {}).get("hash") == n["hash"])
        print(f"deepmap enrich --dry-run  ({root})   [spends nothing, sends nothing]")
        print(f"  undocumented candidates: {len(nodes)}   already enriched: {done}   "
              f"pending: {len(nodes) - done}")
        for n in nodes[:15]:
            print(f"     [{str(n['kind']):8s}] {n['name']}  ({n['source_file']}:{n['start_line']})")
        return 0

    if getattr(args, "deterministic", False):
        res = E.enrich(root, deterministic=True, max_nodes=args.max_nodes,
                       include_modules=args.include_modules)
        if _enrich_refusal(E, res):   # nothing was done: no counts, no "nothing new to apply"
            return ENRICH_REFUSED_RC
        print(f"deepmap enrich --deterministic  ({root})   [no LLM, no network, no spend]")
        # `enriched` counts only summaries that CHANGED; a node with no extra local signal is reported
        # as unchanged and not written (it was counted as "enriched" with a byte-identical summary).
        print(f"  enriched: {res['enriched']} (summary improved)   unchanged: {res.get('unchanged', 0)} "
              f"(no extra local signal — nothing written)   already_done: {res['already_done']}   "
              f"remaining: {res['remaining']}")
        if res["enriched"]:
            print("  Run /deep-map:refresh to embed the updated summaries.")
        else:
            print("  Nothing new to apply — the graph's summaries already carry every local signal.")
        return 0

    # ---- LLM path: spends plan quota. Require the opt-in flag AND --confirm-spend. ----
    if not E.enrich_enabled(root):
        print("deepmap enrich: REFUSING — opt-in is OFF. Set DEEPMAP_ENRICH=1 (or enrichment.enabled in "
              ".deepmap/instance.json) to enable the LLM tier; it uses your EXISTING Claude session. "
              "`--dry-run`/`--status`/`--deterministic` spend nothing.", file=sys.stderr)
        return 2
    if not getattr(args, "confirm_spend", False):
        print("deepmap enrich: REFUSING — the LLM tier calls `claude -p` (consumes your Claude plan "
              "quota). Re-run with --confirm-spend to proceed, or use --dry-run/--deterministic (no "
              "spend).", file=sys.stderr)
        return 2

    res = E.enrich(root, model=args.model, max_nodes=args.max_nodes, batch_size=args.batch_size,
                   max_total_usd=args.max_total_usd, include_modules=args.include_modules)
    if _enrich_refusal(E, res):   # nothing was sent or spent: no counts, no refresh hint
        return ENRICH_REFUSED_RC
    print(f"deepmap enrich  ({root})   [model={res['model']}]")
    print(f"  enrichable: {res['enrichable']}   already_done: {res['already_done']}   "
          f"enriched: {res['enriched']}   skipped: {res['skipped']}   remaining: {res['remaining']}")
    print(f"  spend: ~${res['spend_usd']:.4f} (claude -p on your existing plan)")
    print("  Run /deep-map:refresh to embed the updated summaries.")
    return 0


def cmd_hibernate(args):
    """Spin down idle per-project Neo4j instances (each is a ~0.5-1 GB JVM). Lifecycle only — no Neo4j
    query, no port allocation. The graph is kept. A hibernated instance is NOT revived by a CLI/MCP query (that
    fails with 'not reachable'): the SessionStart heal hook revives it when a Claude Code session next opens
    that project, or `provision.py heal <project dir>` / /deep-map:setup revive it by hand."""
    P = _load("provision")
    scope = "all" if getattr(args, "all", False) else "others"
    n = P.hibernate_idle(args.target, idle_minutes=args.idle_minutes, scope=scope)
    print(f"hibernated {n} idle instance(s) (idle > {args.idle_minutes} min, scope={scope}).")
    if n:
        print(f"  their graphs are kept; the next Claude Code session in a hibernated project revives it (the "
              f"SessionStart heal hook), or run {_provision_cmd('heal')} in that project (no re-sync).")
    return 0


def cmd_instance_rm(args):
    """Remove a project's per-project Neo4j instance (stop the JVM, delete its data dir, drop its ports
    from the registry). Guards the base/legacy store and the current project (needs --yes)."""
    P = _load("provision")
    return P.instance_rm(args.target, key=args.key, yes=args.yes, purge=args.purge)


def _int_at_least(lo: int):
    """An argparse `type=` that rejects a non-integer or a value below `lo` (exit 2 with a message) —
    a negative count must not silently mean "slice from the end"."""
    def _parse(text):
        try:
            v = int(text)
        except (TypeError, ValueError):
            raise argparse.ArgumentTypeError(f"{text!r} is not an integer")
        if v < lo:
            raise argparse.ArgumentTypeError(f"must be >= {lo} (got {v})")
        return v
    return _parse


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="deepmap", description="DeepMap CLI — the /deep-map commands without MCP. "
                                 "Run a subcommand with -h for its options.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    # (the sync subparser + --switch-project stay on ONE line: tests/installer_gates + switch_guard_gates scan it)
    p = sub.add_parser("sync"); p.add_argument("target", help="the project directory (a subdirectory of a synced repo syncs its root)"); p.add_argument("--stale-cleanup", action="store_true", help="also prune nodes/edges whose source is gone (guarded against implausibly large wipes)"); p.add_argument("--switch-project", dest="switch_project", action="store_true", help="allow wiping a SHARED Neo4j that holds another project's graph (escape-hatch setups)")  # noqa: E501,E702
    p.description = "Build / refresh this project's code graph."
    # DeepMap's background refresh (build/auto_sync.py), started by the SessionStart heal hook — not a user option
    p.add_argument("--unattended", default=None, choices=("session-start",), help=argparse.SUPPRESS)
    p.add_argument("--session", default=None, help=argparse.SUPPRESS)
    p.epilog, p.formatter_class = _SYNC_EXIT_CODES, argparse.RawDescriptionHelpFormatter
    try:   # list it in `deepmap -h` like the others (add_parser(help=...) would split the scanned line)
        sub._choices_actions.insert(0, sub._ChoicesPseudoAction("sync", (), "build / refresh this project's "
                                                                            "code graph"))
    except Exception:
        pass
    p.add_argument("--force-prune", dest="force_prune", action="store_true",
                   help="override the stale-cleanup wipe guard (engine downgrade / >50%% of keyed nodes)")
    p.set_defaults(fn=cmd_sync)
    p = sub.add_parser("search", help="fused hybrid search (vector + BM25 + graph) over this project's graph")
    p.add_argument("query", help="an intent phrase, e.g. \"where are refunds computed\"")
    p.add_argument("--top-k", type=int, default=mcp_bounds.TOP_K_DEFAULT,
                   help=f"results to return (default {mcp_bounds.TOP_K_DEFAULT}, max {mcp_bounds.TOP_K_MAX})")
    p.set_defaults(fn=cmd_search)
    p = sub.add_parser("impact", help="what reaches a symbol (multi-hop callers + framework edges)")
    p.add_argument("name", help="a function / method / class name (Class.method or Class#method narrows it)")
    p.add_argument("--limit", type=int, default=mcp_bounds.IMPACT_LIMIT_DEFAULT,
                   help=f"dependents to return (default {mcp_bounds.IMPACT_LIMIT_DEFAULT}, "
                        f"max {mcp_bounds.IMPACT_LIMIT_MAX})")
    p.add_argument("--max-hops", dest="max_hops", type=int, default=None,
                   help="reverse-reachability depth (default 3, max 5)")
    p.add_argument("--edges", default=None,
                   help="comma-separated edge types to walk INSTEAD of the default set (CALLS is always "
                        "walked); default: CALLS + the framework/runtime edges")
    p.set_defaults(fn=cmd_impact)
    p = sub.add_parser("cypher", help="read-only Cypher over this project's graph (list / count / paths)")
    p.add_argument("query", help="one read query, e.g. \"MATCH (c:Class)<-[:INHERITS]-(s) RETURN c.name, "
                                 "count(s) AS n ORDER BY n DESC\" (writes, LOAD CSV, admin and procedures outside "
                                 "a read-only allowlist are rejected)")
    p.add_argument("--params", default=None, help="the query's $parameters as a JSON object, e.g. '{\"name\": \"save\"}'")
    p.add_argument("--limit", type=int, default=cypher_readonly.LIMIT_DEFAULT,
                   help=f"rows to return (default {cypher_readonly.LIMIT_DEFAULT}, max {cypher_readonly.LIMIT_MAX})")
    p.add_argument("--json", action="store_true", help="print {rows, notes} as JSON")
    p.set_defaults(fn=cmd_cypher)
    p = sub.add_parser("status", help="graph census, indexes, coverage and freshness for this project")
    p.set_defaults(fn=cmd_status)
    p = sub.add_parser("doctor", help="health check: Neo4j, graph, indexes, MCP servers, ML stack, extractor")
    p.set_defaults(fn=cmd_doctor)
    p = sub.add_parser("version", help="print the DeepMap version")
    p.set_defaults(fn=cmd_version)
    p = sub.add_parser("learning", help="turn the opt-in, offline local learning loop on/off for this project")
    grp = p.add_mutually_exclusive_group()   # `--disable --enable` / `--status --disable` are errors, not a silent pick
    grp.add_argument("--enable", action="store_true", help="enable (durable project flag) + print the hook snippet")
    grp.add_argument("--disable", action="store_true", help="disable the project flag")
    grp.add_argument("--status", action="store_true", help="show the current state (the default)")
    p.set_defaults(fn=cmd_learning)
    p = sub.add_parser("tune", help="offline weight tuner over the local retrieval log (never live)")
    p.add_argument("root", nargs="?", default=None, help="project root (default: this project)")
    p.add_argument("--status", action="store_true", help="show the stored learned weights + log counts")
    p.add_argument("--min-queries", dest="min_queries", type=int, default=None,   # None -> tune_weights.DEFAULT_MIN_LABELED (drift-lock; resolved at the call site)
                   help="cold-start threshold: labeled queries needed before tuning (default: the tuner's own)")
    p.add_argument("--objective", choices=("ndcg", "map", "mrr"), default="ndcg", help="ranking objective")
    p.add_argument("--optimizer", choices=("grid", "coord"), default="grid", help="weight search strategy")
    p.add_argument("--tune-rrf-k", dest="tune_rrf_k", action="store_true",
                   help="also search the RRF k constant — the best k is recorded as a recommendation only: live "
                        f"search and every learned-vs-equal judgement use k={_LIVE_RRF_K}")
    p.add_argument("--no-profiles", dest="no_profiles", action="store_true", help="skip per-intent profiles")
    p.add_argument("--min-queries-bucket", dest="min_queries_bucket", type=int, default=None,   # None -> tune_weights.DEFAULT_MIN_LABELED_BUCKET (drift-lock; resolved at the call site)
                   help="labeled queries a per-intent profile needs (default: the tuner's own)")
    p.add_argument("--report", action="store_true", help="print the benchmark evidence + why live apply is blocked")
    p.add_argument("--apply", action="store_true",
                   help="operator-gated: mark the learned weights applied (needs benchmark evidence bound to the "
                        "current weights)")
    p.add_argument("--export-prior", dest="export_prior", action="store_true",
                   help="pool THIS project's significant win into the local cross-project prior")
    p.add_argument("--seed-from-prior", dest="seed_from_prior", action="store_true",
                   help="warm-start from the local cross-project prior: among equally-scoring weights the one "
                        "closest to the prior wins (the grid's tie-break; coord also starts there) — it never "
                        "out-scores the data or widens the safety envelope")
    p.add_argument("--dry-run", dest="dry_run", action="store_true", help="tune but do not write instance.json")
    p.set_defaults(fn=cmd_tune)
    p = sub.add_parser("insights", help="local, read-only summary of the opt-in retrieval log")
    p.add_argument("root", nargs="?", default=None, help="project root (default: this project)")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--since", default=None,
                   help="only records with ts >= this ISO date/time prefix (string compare)")
    p.set_defaults(fn=cmd_insights)
    p = sub.add_parser("enrich", help="opt-in intent summaries for undocumented code (LLM tier or local)")
    p.add_argument("root", nargs="?", default=None, help="project root (default: this project)")
    p.add_argument("--model", default="claude-sonnet-5", help="the Claude model `claude -p` uses (LLM tier)")
    p.add_argument("--max-nodes", dest="max_nodes", type=_int_at_least(0), default=200,
                   help="nodes to enrich this run (resumable; default 200)")
    p.add_argument("--batch-size", dest="batch_size", type=_int_at_least(1), default=20,
                   help="nodes per `claude -p` call (LLM tier; default 20)")
    p.add_argument("--max-total-usd", dest="max_total_usd", type=float, default=None,
                   help="stop once the reported spend reaches this (LLM tier)")
    p.add_argument("--include-modules", dest="include_modules", action="store_true",
                   help="also enrich whole-file Module nodes (low value, one call per file)")
    p.add_argument("--deterministic", action="store_true",
                   help="richer LOCAL summaries (doc comments, decorators, DSL macros) — no LLM, no spend")
    p.add_argument("--status", action="store_true", help="sidecar state only (no extraction, no spend)")
    p.add_argument("--dry-run", dest="dry_run", action="store_true",
                   help="count candidates (re-extracts the project to find them — may take a while; no spend)")
    p.add_argument("--confirm-spend", dest="confirm_spend", action="store_true",
                   help="required for the LLM tier (it consumes your Claude plan quota); needs DEEPMAP_ENRICH=1")
    p.epilog, p.formatter_class = _ENRICH_EXIT_CODES, argparse.RawDescriptionHelpFormatter
    p.set_defaults(fn=cmd_enrich)
    p = sub.add_parser("hibernate", help="stop idle per-project Neo4j instances (graphs kept)")
    p.add_argument("target", nargs="?", default=".", help="the current project (default: .)")
    p.add_argument("--idle-minutes", dest="idle_minutes", type=int, default=30,
                   help="idle threshold in minutes (default 30)")
    grp = p.add_mutually_exclusive_group()
    grp.add_argument("--others", dest="others", action="store_true",
                     help="hibernate OTHER projects' idle instances, not the current one (default)")
    grp.add_argument("--all", dest="all", action="store_true",
                     help="also hibernate the current project's idle instance")
    p.set_defaults(fn=cmd_hibernate)
    p = sub.add_parser("instance-rm", help="remove a project's per-project Neo4j instance (GC)")
    p.add_argument("target", nargs="?", default=".", help="the project directory (default: .)")
    p.add_argument("--key", default=None, help="target by project-key prefix (GC when the repo dir is gone)")
    p.add_argument("--yes", "--force", dest="yes", action="store_true",
                   help="confirm removing the CURRENT project's instance")
    p.add_argument("--purge", dest="purge", action="store_true",
                   help="also drop the whole registry entry (MCP ports too), not just the Neo4j pool")
    p.set_defaults(fn=cmd_instance_rm)
    return ap


def _project_mcp_ports(P, root):
    """(search, embed, source, problem): THIS project's MCP ports + where they came from — lookup-only,
    mirroring `provision mcp-status` (apply_ports_for(allocate=False)) WITHOUT mutating os.environ: an
    explicit DEEPMAP_SEARCH_PORT/DEEPMAP_EMBED_PORT override > the port registry > the project's
    .mcp.json > the base default pair. `problem` (None when the pair is this project's, or on a
    pre-registry single-project install — no registry file at all) says why the ports can NOT be this
    project's: the registry is unreadable, or it exists but holds no pair for this project (never set
    up here; the base pair then belongs to whichever project claimed it first — probing it reported
    that project's servers as this one's), or the pair came from a .mcp.json whose ports the registry gives to
    ANOTHER project (a copied checkout / worktree — ROUND-7 I4, provision's _mcp_pair_claimed_by_other). The
    same verdict as provision's lookup (apply_ports_for(allocate=False) / mcp_route_problem(), D1). Never
    raises."""
    if "DEEPMAP_SEARCH_PORT" in os.environ or "DEEPMAP_EMBED_PORT" in os.environ:
        return P.mcp_port("deepmap-search"), P.mcp_port("deepmap-embed"), "env override", None
    ident, PR, status = root, None, "absent"
    try:
        PR = _load("port_registry")
        try:
            status = PR.registry_status()
        except Exception:
            status = "ok"
        pair = PR.lookup_ports(root)
        if pair:
            return int(pair[0]), int(pair[1]), "registry", None
        ident = PR.project_identity(root)
    except Exception:
        pass
    try:
        pair = P._ports_from_mcp_json(ident)
        if pair:
            s, e = int(pair[0]), int(pair[1])
            # ROUND-7 I4: a copied checkout / worktree inherits the original's committed .mcp.json — ports the
            # registry gives to ANOTHER project are its servers (provision's lookup refuses them the same way).
            other = _mcp_pair_claimed_by_other(P, root, (s, e))
            if other:
                return s, e, ".mcp.json", (
                    f"the MCP ports this project's .mcp.json declares ({s}/{e}) are registered to another "
                    f"project ({other}) — its servers, not this project's (a copied checkout or worktree "
                    "inherits the original's .mcp.json); run /deep-map:setup in this project")
            return s, e, ".mcp.json", None
    except Exception:
        pass
    search, embed = P.mcp_port("deepmap-search"), P.mcp_port("deepmap-embed")
    if status in ("unreadable", "unrecognized"):
        return search, embed, "base default", (
            f"this project's MCP ports can't be looked up (the port registry is {status}) and no "
            ".mcp.json declares them — run /deep-map:setup")
    if status == "absent" or PR is None:   # a pre-registry, single-project install: the base pair is its
        return search, embed, "base default", None
    held = ""
    try:   # the registry exists but has no pair for this project: whoever is on the base pair isn't us
        key = PR.project_key(root)
        for ent in PR.registry_stats().get("projects") or []:
            if ent.get("key") != key and {ent.get("search_port"), ent.get("embed_port")} & {search, embed}:
                held = f"; {search}/{embed} belong to project {ent.get('root') or ent.get('key')}"
                break
    except Exception:
        pass
    return search, embed, "base default", (
        f"no MCP port pair is registered for this project (and no .mcp.json declares one){held} — run "
        "/deep-map:setup in this project")


def _setup_blocker(root):
    """setup's PERSISTED MCP blocker for this project (port_registry.read_setup_blocker — a FAILED setup left a
    listener on this project's port it could not restart / attribute, ROUND-7 I4/H1), or None. An unreadable
    record still counts (read_setup_blocker says so). Never raises."""
    try:
        fn = getattr(_load("port_registry"), "read_setup_blocker", None)
        rec = fn(root) if callable(fn) else None
        return rec if isinstance(rec, dict) else None
    except Exception:
        return None


def _blocks(blocker, name: str, port) -> bool:
    """Does the setup blocker cover server `name` on `port`? A record naming neither servers nor ports (corrupt)
    covers every server — never report a held port healthy on a doubt. Never raises."""
    try:
        if not blocker:
            return False
        servers = [str(s) for s in (blocker.get("servers") or [])]
        ports = [int(p) for p in (blocker.get("ports") or []) if str(p).strip()]
        if not servers and not ports:
            return True
        return name in servers or int(port) in ports
    except Exception:
        return bool(blocker)


def _mcp_pair_claimed_by_other(P, root, pair):
    """The root (or key) of ANOTHER registered project whose MCP pair includes a port of `pair`, else None —
    provision's own verdict (_mcp_pair_claimed_by_other: alias-aware, E1) when that helper exists; else the
    same check over port_registry.registry_stats (an older / stubbed provision). Never raises."""
    fn = getattr(P, "_mcp_pair_claimed_by_other", None)
    if callable(fn):
        try:
            other = fn(root, tuple(pair))
            return str(other) if other else None
        except Exception:
            pass
    try:
        PR = _load("port_registry")
        key = PR.project_key(root)
        want = {int(p) for p in pair}
        for ent in PR.registry_stats().get("projects") or []:
            if not isinstance(ent, dict) or ent.get("key") == key or key in (ent.get("aliases") or ()):
                continue
            if want & {ent.get("search_port"), ent.get("embed_port")}:
                return str(ent.get("root") or ent.get("key"))
    except Exception:
        pass
    return None


def _is_query_cost_error(exc) -> bool:
    """A server-side budget failure (transaction memory pool exhausted / transaction timed out) — the
    instance is healthy; THIS query was too expensive. Never raises."""
    try:
        code = str(getattr(exc, "code", "") or "")
        text = f"{code} {exc}"
        if any(k in code for k in ("OutOfMemory", "MemoryLimit", "MemoryPool")):   # pool / per-tx / heap
            return True
        return any(k in text for k in ("dbms.memory.transaction", "db.memory.transaction",
                                        "TransactionTimedOut", "TransactionTimedOutClientConfiguration"))
    except Exception:
        return False


def _exc_chain(exc, depth: int = 8):
    """`exc` and the exceptions it was raised from (__cause__, else __context__) — cycle-safe, bounded."""
    seen, e = set(), exc
    while e is not None and id(e) not in seen and len(seen) < depth:
        seen.add(id(e))
        yield e
        e = getattr(e, "__cause__", None) or getattr(e, "__context__", None)


def _is_login_lock(exc) -> bool:
    """True iff `exc`, or an exception it was raised from, is Neo4j's temporary LOGIN LOCK after too many
    failed authentications in a row (Neo.ClientError.Security.AuthenticationRateLimit — the 6.x driver
    raises a plain ClientError for it, not an AuthError; recognised by its message too). The lock holds
    whatever password is tried, and every new failed attempt prolongs it. Never raises."""
    try:
        for e in _exc_chain(exc):
            code = str(getattr(e, "code", "") or "")
            if "AuthenticationRateLimit" in code or "too many times in a row" in str(e).lower():
                return True
    except Exception:
        pass
    return False


def _is_auth_problem(exc, NE) -> bool:
    """Neo4j REFUSED us — an AuthError, any Neo.ClientError.Security.* (CredentialsExpired on a store left
    at neo4j's default password, Forbidden), or the login lock — raised directly or from inside another
    error (a pool / routing layer may re-raise it as ServiceUnavailable). One classifier with the MCP
    server (mcp_bounds.is_neo4j_auth_error), so the CLI and the tools never disagree. Never raises."""
    try:
        if mcp_bounds.is_neo4j_auth_error(exc) or _is_login_lock(exc):
            return True
        auth = _ne_types(NE, "AuthError") if NE is not None else ()
        server = _ne_types(NE, "Neo4jError") if NE is not None else ()
        for e in _exc_chain(exc):
            if (auth and isinstance(e, auth)) or (
                    server and isinstance(e, server) and ".Security." in str(getattr(e, "code", "") or "")):
                return True
    except Exception:
        pass
    return False


def _classify_db_error(exc) -> str:
    """'resolve' | 'auth' | 'dropped' | 'hung' | 'unreachable' | 'query' | 'driver' for an exception out of a
    Neo4j call — main (_db_error_message) and doctor word every one of them ('driver': a client-side failure —
    a DriverError that is not a connectivity error, or any non-OSError, non-Neo4j exception).
    An AuthError, any Neo.ClientError.Security.* (CredentialsExpired on a store left at neo4j's default
    password) and the temporary login lock after repeated failures (AuthenticationRateLimit, a plain
    ClientError) mean the instance is UP and refused us — never "unreachable", even when a driver layer
    re-raised it inside a ServiceUnavailable. A Neo4jError that is not auth is the SERVER answering
    (syntax / transient / database error), so the instance is reachable too.

    The driver's exception classes are looked up DEFENSIVELY (_ne_types): ConnectionPoolError exists only
    in neo4j >= 6.0, and a 5.x driver left in place by an older install (a plugin update does not re-run the
    installer) must still get the classified message — never an AttributeError out of the handler that is
    reporting an outage (main's exit-3 path, doctor's own except). Never raises."""
    if isinstance(exc, ProjectResolutionError):
        return "resolve"
    try:
        from neo4j import exceptions as NE
    except Exception:
        NE = None
    try:
        if _is_auth_problem(exc, NE):
            return "auth"
        if _drop_kind(exc) == "drop":   # ROUND-8 J6: accepted, then closed/reset — before 'hung' (text overlap)
            return "dropped"
        if _is_hung(exc, NE):
            return "hung"
        if NE is not None:
            unreachable = _ne_types(NE, "ServiceUnavailable", "SessionExpired", "ConnectionPoolError")
            if unreachable and isinstance(exc, unreachable):
                return "unreachable"
            query = _ne_types(NE, "Neo4jError")
            if query and isinstance(exc, query):
                return "query"
            driver = _ne_types(NE, "DriverError")
            if driver and isinstance(exc, driver):
                return "driver"
    except Exception:
        pass
    return "unreachable" if isinstance(exc, OSError) else "driver"


_HUNG_TEXT = ("failed to obtain a connection from the pool", "bolt handshake response", "timed out")


def _is_hung(exc, NE) -> bool:
    """True iff `exc` says Neo4j ACCEPTED the connection but never ANSWERED within the driver's deadline — a
    frozen / suspended JVM, or a badly overloaded one (T10b-3): a TimeoutError cause (the Bolt handshake read
    the real driver times out on), the pool's ConnectionAcquisitionTimeoutError, or their messages. That is
    not "stopped": /deep-map:setup can't revive an instance that still holds its port. A server-side error
    (a Neo4jError — the server answered, e.g. a transaction timeout) never counts, nor does a refused
    connection. Never raises."""
    try:
        server = _ne_types(NE, "Neo4jError") if NE is not None else ()
        if server and isinstance(exc, server):
            return False
        acq = _ne_types(NE, "ConnectionAcquisitionTimeoutError") if NE is not None else ()
        for e in _exc_chain(exc):
            if server and isinstance(e, server):
                return False
            if (acq and isinstance(e, acq)) or isinstance(e, TimeoutError) \
                    or type(e).__name__ == "SocketDeadlineExceededError":
                return True
            if isinstance(e, ConnectionRefusedError):
                return False
            msg = str(e).lower()
            if any(t in msg for t in _HUNG_TEXT):
                return True
    except Exception:
        pass
    return False


# ---- ROUND-8 J6: a connection the server ACCEPTED and then closed (a partly dead Bolt acceptor) ----------------
# A Neo4j JVM whose Bolt event loop died (seen after a long sleep/resume: debug.log "Force-closing a channel whose
# registration task was not accepted by an event loop ... RejectedExecutionException: event executor terminated")
# still accepts TCP connections but force-closes about 1 in 20-40 new ones. A read command retries such a drop ONCE
# with a fresh driver (reads are idempotent); doctor reports the condition. Never retried: a refused connection
# (nothing listens — a retry can't help), a timeout (a hung JVM — a retry would double a 30 s wait), an auth or
# server-side error (the server answered), an unresolvable project.
_DROP_TEXT = ("closed with incomplete handshake response", "connection reset", "forcibly closed", "was aborted",
              "connection aborted", "defunct connection", "broken pipe", "failed to write data to connection",
              "closed connection")
_TIMEOUT_TEXT = ("timed out", "failed to obtain a connection from the pool")
_DROP_WINERRORS = (10053, 10054)   # WSAECONNABORTED / WSAECONNRESET
_DROP_FAST_S = 10.0   # a cause-less handshake failure faster than this was a drop; a hung JVM fails at the deadline
_DROP_EVENTS: list = []   # this invocation's dropped-and-retried connections (doctor reports them); main() resets


def _drop_kind(exc):
    """How `exc` (a Neo4j failure) relates to a DROPPED connection: 'drop' — the server accepted the connection and
    then closed / reset it (an OSError reset/abort/broken-pipe in the chain, or the driver's closed-mid-handshake /
    defunct-connection text); 'timeout' — it never answered (TimeoutError, SocketDeadlineExceededError, a pool
    acquisition timeout, "timed out"); 'handshake' — a handshake read failed but its cause type was lost (an operator
    `localhost` URI: the driver raises `from` the ::1 refusal and keeps the 127.0.0.1 failure only as text); None —
    anything else (refused, auth, a server-side error, not a Neo4j failure). Never raises."""
    try:
        from neo4j import exceptions as NE
    except Exception:
        NE = None
    try:
        if isinstance(exc, ProjectResolutionError) or _is_auth_problem(exc, NE):
            return None
        server = _ne_types(NE, "Neo4jError") if NE is not None else ()
        acq = _ne_types(NE, "ConnectionAcquisitionTimeoutError") if NE is not None else ()
        drop = handshake = False
        for e in _exc_chain(exc):
            if server and isinstance(e, server):
                return None                                  # the server answered
            if isinstance(e, TimeoutError) or (acq and isinstance(e, acq)) \
                    or type(e).__name__ == "SocketDeadlineExceededError":
                return "timeout"
            if isinstance(e, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)) \
                    or (isinstance(e, OSError) and getattr(e, "winerror", None) in _DROP_WINERRORS):
                drop = True
            msg = str(e).lower()
            if any(t in msg for t in _TIMEOUT_TEXT):
                return "timeout"
            if any(t in msg for t in _DROP_TEXT):
                drop = True
            elif "handshake response" in msg:
                handshake = True
        return "drop" if drop else ("handshake" if handshake else None)
    except Exception:
        return None


def _drop_detail(exc) -> str:
    """The most specific line for a dropped connection: the OSError underneath it ('ConnectionResetError: [WinError
    10054] ...') when the chain has one, else the innermost driver message ('Connection to ... closed with incomplete
    handshake response' — not the outer "Couldn't connect to ..." wrapper that repeats it). Never raises."""
    try:
        chain = list(_exc_chain(exc))
        for e in chain:
            if isinstance(e, OSError) and not isinstance(e, TimeoutError):
                return f"{type(e).__name__}: {' '.join(str(e).split())}"[:200]
        return _short_error(chain[-1] if chain else exc)[:200]
    except Exception:
        return type(exc).__name__


def _read_retry(cmd: str, op):
    """op() — a READ against this project's Neo4j that opens (and closes) its own driver — retried ONCE with a fresh
    driver when it failed on a DROPPED connection (_drop_kind 'drop', or a cause-less handshake failure that failed
    fast). The retry is recorded in _DROP_EVENTS and announced once on stderr (doctor words it itself). Any other
    failure, and a second failure, propagate unchanged (main() classifies them)."""
    t0 = time.monotonic()
    try:
        return op()
    except Exception as exc:  # noqa: BLE001 — re-raised unless it is a dropped connection
        kind = _drop_kind(exc)
        if not (kind == "drop" or (kind == "handshake" and time.monotonic() - t0 < _DROP_FAST_S)):
            raise
        detail = _drop_detail(exc)
    _DROP_EVENTS.append({"cmd": cmd, "error": detail})
    if cmd != "doctor":
        print(_redact_text(f"[deepmap] {cmd}: Neo4j at {_uri_for_message()} closed a new connection before answering "
                           f"({detail}) — retrying once with a fresh connection."), file=sys.stderr, flush=True)
    return op()


def _once_per_text(embed_fn):
    """`embed_fn` memoised per call arguments, so a search retried after a dropped connection reuses the query
    vector instead of embedding it again (a cold in-process model load can take minutes). Never changes the
    vector."""
    cache: dict = {}

    def embed(*args, **kwargs):
        try:
            key = (args, tuple(sorted(kwargs.items())))
            hash(key)
        except TypeError:
            return embed_fn(*args, **kwargs)
        if key not in cache:
            cache[key] = embed_fn(*args, **kwargs)
        v = cache[key]
        return list(v) if isinstance(v, list) else v
    return embed


_ACCEPTOR_CLOSED = "Force-closing a channel whose registration task was not accepted by an event loop"
_ACCEPTOR_DEAD = "event executor terminated"
_NEO4J_STARTED = ("======== Neo4j ", "Bolt enabled on ")   # debug.log lines a JVM writes when it starts
_LOG_TAIL_BYTES = 8 * 1024 * 1024                         # debug.log rotates at 20 MiB; read a bounded tail
_LOG_TS = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)(?:\.\d+)?(\+0000)?")


def _instance_logs_dir(route):
    """The logs directory of THIS project's DeepMap-managed Neo4j — the JVM behind `route` (a registry / instance-conf
    route: its own instance's <home>/instances/<key>/logs, or the base store's logs for its registry claim on the
    base pair) — else None: an operator NEO4J_URI (not DeepMap's JVM), the legacy unverified port (possibly another
    program), or an unknown route. Never raises."""
    try:
        if route is None or os.environ.get("NEO4J_URI"):
            return None
        if getattr(route, "source", None) not in ("registry", "instance-conf"):
            return None
        bolt = getattr(route, "bolt", None)
        if bolt is None:
            return None
        import port_registry as PR
        base = int(getattr(PR, "BASE_BOLT", 7687))
        key = None if int(bolt) == base else (getattr(route, "key", None) or PR.project_key(_project_root()))
        try:
            logs = (_load("provision").paths(project_key=key) or {}).get("logs")
            if logs:
                return Path(logs)
        except Exception:
            pass
        return (Path(PR.home_dir()) / "instances" / str(key) / "logs") if key else None
    except Exception:
        return None


def _bolt_acceptor_signal(logs_dir):
    """{'events': n, 'first': ts, 'last': ts} when the instance's debug.log records connections force-closed because
    a Bolt event loop is gone ('event executor terminated') SINCE THE JVM LAST STARTED (after its last start banner —
    a restart clears the condition), else None. Reads a bounded tail (_LOG_TAIL_BYTES): when the last banner lies
    before that window, the whole window postdates it. A missing / unreadable log -> None. Never raises."""
    try:
        if not logs_dir:
            return None
        f = Path(logs_dir) / "debug.log"
        size = f.stat().st_size
        with open(f, "rb") as fh:
            if size > _LOG_TAIL_BYTES:
                fh.seek(size - _LOG_TAIL_BYTES)
                fh.readline()                                # drop the partial first line
            text = fh.read().decode("utf-8", errors="replace")
        cut = max(text.rfind(m) for m in _NEO4J_STARTED)
        if cut > 0:
            text = text[cut:]
        events, first, last = 0, None, None
        for ln in text.splitlines():
            if _ACCEPTOR_CLOSED in ln:
                events += 1
                m = _LOG_TS.match(ln)
                ts = (m.group(1) + (" UTC" if m.group(2) else "")) if m else None
                first = first or ts
                last = ts or last
        if not events and _ACCEPTOR_DEAD in text:
            events = 1
        return {"events": events, "first": first, "last": last} if events else None
    except Exception:
        return None


def _capitalized(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


_DNS_FAILURE = "Failed to DNS resolve address"   # the neo4j driver's own wording for a host name that did not resolve


def _dns_failure_text(exc):
    """The error text of a host name that did NOT resolve — the driver's "Failed to DNS resolve address <host:port>:
    <resolver error>" (a socket.gaierror underneath), on one line — or None when `exc` is not such a failure. The
    driver raises an unknown host (EAI_NONAME) and a temporary resolver failure (EAI_AGAIN) as ServiceUnavailable
    (the 'unreachable' kind) and any other resolver error as ValueError (the 'driver' kind); DeepMap's own routes
    dial 127.0.0.1 and look no name up, so in practice this is an operator NEO4J_URI's host. Never raises."""
    try:
        import socket
        gai = None
        for e in _exc_chain(exc):
            try:
                text = " ".join(str(e).split())
            except Exception:
                continue
            if _DNS_FAILURE in text:
                return text[:400]
            if gai is None and isinstance(e, socket.gaierror):
                gai = text or type(e).__name__
        return f"DNS lookup failed ({gai})"[:400] if gai is not None else None
    except Exception:
        return None


def _down_remedy(exc=None) -> str:
    """What to do about a Neo4j that REFUSES connections (not running) — runnable for the route in use (D6): this
    project's own instance is revived by heal (no re-sync) or /deep-map:setup; an operator NEO4J_URI is not
    DeepMap's to start; the legacy shared port means this project has no instance of its own. `exc`, when given, is
    the failure: an operator NEO4J_URI whose host name did not resolve (_dns_failure_text — the driver raises it as
    ServiceUnavailable, so it lands here) is not a stopped server, and its remedy is that host name (round 3,
    docs[3]: it used to get "check that it is running"). Never raises."""
    route = _current_route()
    src = getattr(route, "source", None)
    if src == "env":
        if exc is not None and _dns_failure_text(exc):
            return ("the host name in your NEO4J_URI does not resolve — check that host in NEO4J_URI (a typo, a name "
                    "only another network or a VPN can resolve, or no DNS right now), or unset NEO4J_URI and run "
                    "/deep-map:setup to give this project its own instance")
        return ("the Neo4j your NEO4J_URI points at is operator-managed — check that it is running (DeepMap does "
                "not start or stop it)")
    if src == "legacy-unverified":
        return ("this project has no DeepMap instance of its own (the legacy shared port was tried) — run "
                "/deep-map:setup in this project to give it one")
    try:
        root = _project_root()
    except Exception:
        root = Path(".")
    return (f"this project's instance may be stopped or hibernated — revive it with "
            f"{_provision_cmd(_q_root('heal', root))} (the graph is kept — no re-sync) or /deep-map:setup (it "
            "revives a stopped/hibernated instance)")


def _hung_remedy() -> str:
    """What to do about an instance that accepts connections but never answers — runnable for the route in use
    (D6): restart THIS project's local instance (stop, then heal: the graph is kept, no re-sync); an operator
    NEO4J_URI is not DeepMap's to restart. Never raises."""
    route = _current_route()
    src = getattr(route, "source", None)
    if src == "env":
        return ("the Neo4j your NEO4J_URI points at is operator-managed — check that endpoint (DeepMap does not "
                "start or stop it)")
    try:
        root = _project_root()
    except Exception:
        root = Path(".")
    restart = (f"restart it: {_provision_cmd(_q_root('stop', root))} then {_provision_cmd(_q_root('heal', root))} "
               "(the graph is kept — no re-sync)")
    if src == "legacy-unverified":
        return (f"this project has no DeepMap instance of its own (the legacy shared port answered the connect "
                f"only). If it is DeepMap's own pre-isolation store, {restart}; otherwise run /deep-map:setup in "
                "this project to give it its own instance")
    return restart


def _driver_remedy(exc) -> str:
    """What to do about a `driver`-kind failure (_classify_db_error; ISS-build:025) — the Neo4j Python driver
    failed on the CLIENT side: a NEO4J_URI it can't use (the Neo4j Browser's http:// address, a user name
    inside the URI, a DNS failure the driver raises as ValueError — a resolver error other than an unknown host,
    e.g. EAI_FAIL), a neo4j package that won't import or sits outside the range DeepMap supports
    (_neo4j_driver_spec; one this Python lacks, or holds out of range, /deep-map:setup installs into DeepMap's own
    environment: _setup_remedy), or another client-side error. An unknown host (EAI_NONAME) or a temporary DNS failure
    (EAI_AGAIN) is NOT this kind: the driver raises ServiceUnavailable for it — the 'unreachable' kind, whose
    remedy (_down_remedy) names the NEO4J_URI host (round 3, docs[3]). Nothing showed the instance to be down, so
    the stopped-instance remedy (start it / heal) is never given. Runnable for the route in use (D6): an operator
    NEO4J_URI is the likeliest cause there; on DeepMap's own route (a URI it generated itself) the installed driver
    version decides. Never raises."""
    try:
        if any(isinstance(e, ImportError) for e in _exc_chain(exc)):
            setup = _setup_remedy("neo4j")
            if setup:   # not installed here, or out of range: setup puts it in DeepMap's own environment
                return (f"the Python running DeepMap has no usable neo4j driver package — {setup}, then re-run "
                        "/deep-map:doctor")
            return ("the Python running DeepMap can't import the neo4j driver package (or a module it needs) — "
                    f"install the range DeepMap supports into it: {_neo4j_driver_fix()}, then re-run "
                    "/deep-map:doctor")
        spec, have = _neo4j_driver_spec(), _installed_neo4j_version()
        fits = _version_in_spec(have, spec)
        outside = (f"neo4j {have} is installed in DeepMap's Python, outside the range DeepMap supports ({spec}) "
                   f"— install a supported one: {_neo4j_driver_fix()}" if fits is False else "")
        if getattr(_current_route(), "source", None) == "env":
            return ("check NEO4J_URI (an operator-managed endpoint): the driver needs a Bolt URI whose host "
                    "resolves — bolt://<host>:<bolt port> or neo4j://<host>:<bolt port> (bolt+s:// or neo4j+s:// "
                    "for TLS), not the Neo4j Browser's http:// address, and no user name inside it (NEO4J_USER / "
                    "NEO4J_PASSWORD carry the credentials) — or unset NEO4J_URI and run /deep-map:setup to give "
                    "this project its own instance" + (f". Also, {outside}" if outside else ""))
        if outside:
            return f"{outside}, then re-run /deep-map:doctor"
        if fits:
            return (f"neo4j {have} in DeepMap's Python is inside the range DeepMap supports ({spec}), so the error "
                    "shown is the cause — not the driver version, and not a stopped instance (starting or healing "
                    "it won't help); re-run /deep-map:doctor, and if the same error repeats, report it")
        return ("if the neo4j driver in DeepMap's Python is missing or outside the range DeepMap supports, "
                f"install a supported one: {_neo4j_driver_fix()}; then re-run /deep-map:doctor")
    except Exception:
        return "check NEO4J_URI if you set one, and the neo4j driver package installed in DeepMap's Python"


def _ne_types(NE, *names) -> tuple:
    """The exception classes `names` that the installed neo4j.exceptions module actually defines (a missing
    or non-class attribute is skipped), for isinstance() — e.g. no ConnectionPoolError on a 5.x driver.
    Never raises."""
    out = []
    for name in names:
        try:
            t = getattr(NE, name, None)
            if isinstance(t, type) and issubclass(t, BaseException):
                out.append(t)
        except Exception:
            continue
    return tuple(out)


def _short_error(exc) -> str:
    """'<Type>: <server message>' without the driver's long gql_status/diagnostic dump. Never raises."""
    try:
        msg = getattr(exc, "message", None) or str(exc)
        code = str(getattr(exc, "code", "") or "")
        args = getattr(exc, "args", None) or ()
        if (not code or "UnknownError" in code) and args and isinstance(args[0], str) and args[0].strip():
            msg = args[0]   # a code-less driver error keeps its own text, not "An unknown error occurred"
        msg = " ".join(str(msg).split())   # one line: a driver message can span several lines
        if code and "UnknownError" not in code:
            return f"{type(exc).__name__} {code}: {msg}"[:400]
        return f"{type(exc).__name__}: {msg}"[:400]
    except Exception:
        return type(exc).__name__


def _current_route():
    """The route for THIS invocation — the one _driver() resolved, else a fresh resolve_neo4j_route —
    or None when it can't be told. For wording messages only; never raises."""
    if _LAST_ROUTE is not None:
        return _LAST_ROUTE
    try:
        import port_registry as PR
        return PR.resolve_neo4j_route(_project_root())
    except Exception:
        return None


def _base_store_data_dir() -> str:
    """The legacy/base Neo4j store's data directory (provision.paths() with no project key), for the
    base-store auth remedy. Never raises."""
    try:
        return Path(_load("provision").paths()["data"]).as_posix()
    except Exception:
        return "<DEEPMAP_HOME>/neo4j/neo4j-community-<version>/data"


def _auth_hint() -> str:
    """The remedy for an AuthError, RUNNABLE in the state it names (D6) — it depends on which Neo4j the
    project was routed to: `instance-rm --yes` removes only a per-project instance (it refuses the base
    7687 store and never touches an operator NEO4J_URI), so each route gets a remedy that works there.
    Never raises."""
    route = _current_route()
    src = getattr(route, "source", None)
    base_note = ("check NEO4J_PASSWORD (DeepMap's default is 'deepmap'). If an earlier setup failed at "
                 "set-initial-password, the store was initialised with Neo4j's default credentials")
    try:
        root = _project_root()
    except Exception:
        root = Path(".")
    if src == "env":
        return ("check NEO4J_USER / NEO4J_PASSWORD for the Neo4j your NEO4J_URI points at — an "
                "operator-managed endpoint whose store DeepMap does not manage (unset NEO4J_URI and run "
                "/deep-map:setup to give this project its own instance instead)")
    if src == "legacy-unverified":
        return ("this project has no DeepMap instance of its own, so the legacy shared port was tried — and "
                "the Neo4j listening there rejected DeepMap's credentials. If it is DeepMap's own "
                "pre-isolation store, check NEO4J_PASSWORD (DeepMap's default is 'deepmap'); otherwise it "
                "is most likely another program's Neo4j (e.g. a Docker container), not DeepMap's — run "
                "/deep-map:setup in this project to give it its own instance")
    if src in ("registry", "instance-conf") and getattr(route, "bolt", None) not in (None, 7687):
        return (f"{base_note}: remove this project's instance with "
                f"{_cli_cmd(_q_root('instance-rm', root) + ' --yes')} and re-run /deep-map:setup "
                "(the graph is rebuilt from source)")
    if src in ("registry", "instance-conf"):   # the base 7687 store: instance-rm refuses it
        return (f"{base_note}: stop Neo4j with {_provision_cmd(_q_root('stop', root))}, move the base "
                f"store's data directory {_base_store_data_dir()} aside, then re-run /deep-map:setup (it "
                "initialises a fresh store with DeepMap's password; the graph is rebuilt from source). "
                "Or another program holds bolt 7687 with its own credentials")
    return (f"{base_note} (or another program holds this port). Run /deep-map:doctor to see which instance "
            "this project uses and what to do")


_LOCK_CODE = "Neo.ClientError.Security.AuthenticationRateLimit"


def _login_lock_remedy(pad: str = " ") -> str:
    """What to do while Neo4j LOCKS logins after repeated failures — runnable in that state (D6). The lock
    rejects every login, the right password included, until the failures stop, so the first fix is to stop
    whatever keeps failing (typically MCP servers started with an older NEO4J_PASSWORD — /deep-map:setup
    restarts this project's with the current credentials), then retry. Destroying the store is offered
    only for a retry that is REJECTED rather than locked (then the password itself is wrong). Never
    raises."""
    return ("Something keeps logging in with wrong credentials — most often this project's MCP servers (or "
            "a hook) started with an older NEO4J_PASSWORD: restart them with the current password via "
            f"/deep-map:setup, then retry in a few seconds.{pad}If the retry is REJECTED rather than locked, "
            f"the password itself is wrong: {_auth_hint()}")


def _query_cost_remedy(cmd: str, exc) -> str:
    """The runnable fix for a query that exceeded Neo4j's budget (D6). For `impact` the retriever has
    ALREADY fallen back to a 1-hop walk on a transaction-memory error, so `--max-hops 1` would fail the same
    way: the remedies that work are a narrower (qualified) symbol and a larger budget — the heap for
    memory, DEEPMAP_IMPACT_TIMEOUT_S for the impact query timeout. Never raises."""
    try:
        code = f"{getattr(exc, 'code', '') or ''} {exc}"
    except Exception:
        code = ""
    timed_out = "TimedOut" in code or "Timeout" in code
    if cmd == "impact":
        if timed_out:
            return ("The reverse traversal ran past the impact query timeout for this symbol (a hub / heavily "
                    "shared name). Name it as a qualified Class.method, or raise DEEPMAP_IMPACT_TIMEOUT_S "
                    "(seconds) for a very large graph.")
        return ("The reverse traversal exhausted Neo4j's transaction memory for this symbol (a hub / heavily "
                "shared name). Name it as a qualified Class.method, or give Neo4j a larger heap "
                "(DEEPMAP_NEO4J_HEAP_MAX, applied when the instance restarts).")
    if timed_out:
        return "The query ran past its timeout — narrow the request and retry."
    return ("The query exceeded Neo4j's transaction memory — narrow the request and retry (a larger heap: "
            "DEEPMAP_NEO4J_HEAP_MAX, applied when the instance restarts).")


def _db_error_message(cmd: str, exc) -> str:
    """The friendly, CLASSIFIED message for a Neo4j failure in `cmd` (a runnable hint, never a relative
    `python build/...` path), with every credential masked (_redact_text — F1: it names the NEO4J_URI and quotes the
    driver's error, either of which can carry a password). Never raises."""
    return _redact_text(_db_error_text(cmd, exc))


def _db_error_text(cmd: str, exc) -> str:
    """_db_error_message's wording, before masking. Never raises."""
    kind = _classify_db_error(exc)
    uri = _uri_for_message()
    pad = "\n          "
    if kind == "resolve":
        # The resolver's / owner check's own message names the owner and the fix (/deep-map:setup).
        return (f"[deepmap] {cmd}: {exc} — nothing was queried.{pad}Diagnose with /deep-map:doctor (or "
                f"{_cli_cmd('doctor')}).")
    if kind == "auth" and _is_login_lock(exc):
        return (f"[deepmap] {cmd}: Neo4j at {uri} is up but is temporarily LOCKING DeepMap's logins after too "
                f"many failed attempts in a row ({_LOCK_CODE}); every new failed attempt prolongs the lock."
                f"{pad}{_login_lock_remedy(pad)}.")
    if kind == "auth":
        return (f"[deepmap] {cmd}: Neo4j at {uri} is up but authentication failed "
                f"({type(exc).__name__}).{pad}{_auth_hint()}.")
    if kind == "query":
        out = f"[deepmap] {cmd}: Neo4j at {uri} is reachable, but the query failed — {_short_error(exc)}"
        if _is_query_cost_error(exc):
            out += f"{pad}{_query_cost_remedy(cmd, exc)}"
        return out
    if kind == "driver":
        return (f"[deepmap] {cmd}: Neo4j driver error talking to {uri} — {_short_error(exc)}"
                f"{pad}Diagnose with /deep-map:doctor.")
    if kind == "dropped":
        again = ", twice in a row (the retry was dropped too)" if _DROP_EVENTS else ""
        return (f"[deepmap] {cmd}: Neo4j at {uri} accepted the connection but closed it before answering{again} — "
                f"{_drop_detail(exc)}{pad}A running instance that drops new connections most likely has a partly dead "
                f"Bolt connection acceptor (Neo4j's debug.log says 'event executor terminated' — typically after a "
                f"long sleep/resume) or is badly overloaded.{pad}{_capitalized(_hung_remedy())}; diagnose with "
                f"/deep-map:doctor.")
    if kind == "hung":
        return (f"[deepmap] {cmd}: Neo4j at {uri} did not answer in time — {_short_error(exc)}"
                f"{pad}No answer within the driver's deadline — a local instance that accepts the connection but "
                f"never answers is most likely hung (a frozen or suspended JVM) or badly overloaded, and "
                f"/deep-map:setup can't revive it while it holds its port.{pad}{_capitalized(_hung_remedy())}; "
                f"diagnose with /deep-map:doctor.")
    return (f"[deepmap] {cmd}: Neo4j at {uri} is not reachable — {_short_error(exc)}"
            f"{pad}{_capitalized(_down_remedy(exc))}; diagnose with /deep-map:doctor.")


def cmd_doctor(args):
    """One-shot health check: Neo4j, the graph, indexes, the MCP servers, and the ML stack.

    Never crashes — every probe is isolated and reported as OK / WARN / FAIL so the output itself
    diagnoses 'why isn't search working?'. Exit 0 only if the core (Neo4j + a populated graph) is healthy.
    Everything is resolved for THIS project (the cwd's project root): its Neo4j instance, its MCP port
    pair, and its last sync's manifest.
    """
    global _LAST_ROUTE
    _LAST_ROUTE = None   # this run's route (the auth remedy and labels word it)
    _ROOT_CACHE.clear()  # ... and this run's project root
    core_ok = True
    neo4j_live = False
    refused_claim = False   # G1: this project's base claim was refused (the store is another project's)
    degraded_reasons: list = []
    edition = None
    root = _project_root()
    manifest = _read_manifest(root)

    def line(status, label, detail=""):
        # F1: masked — a Neo4j line names the NEO4J_URI and quotes the driver's error (either can carry a password)
        print(_redact_text(f"  [{status:4s}] {label}" + (f" — {detail}" if detail else "")))

    def degrade(reason):
        if reason and reason not in degraded_reasons:
            degraded_reasons.append(reason)

    print("DeepMap doctor:")
    _DROP_EVENTS.clear()
    db_fail_kind = None     # how the census failed (J6: 'dropped' / 'hung' mean something ACCEPTED the connection)

    def _census():
        drv = _driver()
        try:   # close the driver even if verify_connectivity / a query raises (no leak on the
               # failure path — doctor's most common case is diagnosing a down instance) — bug-hunt #13
            drv.verify_connectivity()
            with drv.session() as s:
                # Exclude the :_DeepMapMeta ownership singleton so an otherwise-empty graph can't look
                # healthy (core_ok gates on nodes > 0 below).
                c = {"nodes": s.run("MATCH (n) WHERE NOT n:_DeepMapMeta RETURN count(n) AS c").single()["c"],
                     "edges": s.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"],
                     "embedded": s.run("MATCH (n) WHERE n.embedding IS NOT NULL RETURN count(n) AS c").single()["c"],
                     "owner": list(s.run("MATCH (m:_DeepMapMeta) RETURN m.owning_root AS r LIMIT 1")),
                     "idx": list(s.run("SHOW INDEXES YIELD name, state WHERE name STARTS WITH 'deepmap_' "
                                       "RETURN name, state")),
                     "mix": _edge_mix(s),   # the edge-confidence mix (best-effort; None -> no line)
                     "vq": _vector_quantization_line(s)}   # ROUND-10 P4 (best-effort; None -> no line)
            try:   # for the isolation line below (best-effort; a down/old instance -> unknown)
                c["edition"] = _load("provision").neo4j_edition(drv)
            except Exception:
                c["edition"] = None
            return c
        finally:
            drv.close()

    try:
        # ROUND-8 J6: a connection the server accepted and then dropped is retried ONCE, and reported below.
        census = _read_retry("doctor", _census)
        nodes, edges, embedded = census["nodes"], census["edges"], census["embedded"]
        owner, idx, mix, edition = census["owner"], census["idx"], census["mix"], census["edition"]
        neo4j_live = True
        online = sum(1 for r in idx if r["state"] == "ONLINE")
        line("OK", "Neo4j reachable", _uri_for_message() + _route_label())
        line("OK" if nodes else "WARN", "graph", f"{nodes} nodes, {edges} edges, {embedded} embedded")
        if mix:   # informational: what the edges ARE (never a verdict reason)
            _mst, _mdetail = _edge_mix_detail(mix)
            if _mdetail:
                line(_mst, "edges", _mdetail)
        owner_root = owner[0]["r"] if owner else None
        foreign = _foreign_owner(root, owner_root)
        if foreign:   # e.g. an operator NEO4J_URI several projects share: it serves the last-synced one
            line("WARN", "owner", f"{owner_root} — NOT this project: search/impact here answer about that "
                                  f"project")
            degrade(f"{foreign} — search/impact answers would describe that project")
        else:
            line("OK" if owner else "WARN", "owner", owner_root or "no project recorded")
        core_ok = core_ok and nodes > 0
        line("OK" if (idx and online == len(idx)) else "WARN", "indexes", f"{online}/{len(idx)} ONLINE")
        if census.get("vq"):   # ROUND-10 P4: quantized (default) vs exact vector scores, and any mismatch
            line(census["vq"][0], "vector index", census["vq"][1])
        if nodes and not embedded:
            line("WARN", "embeddings", "0 embedded — search runs FTS-only (run /deep-map:refresh; check the ML stack)")
            degrade("VECTOR SEARCH DISABLED (0 embedded) — FTS/keyword-only")
    except Exception as e:  # noqa: BLE001 — a diagnostic must report, not crash
        kind = _classify_db_error(e)
        db_fail_kind = kind
        refused_claim = isinstance(e, ForeignBaseClaimError)
        if kind == "resolve":   # no safe route / the legacy 7687 graph is not this project's (D1)
            msg = str(e)
            line("FAIL", "Neo4j", msg + ("" if "/deep-map:setup" in msg
                                         else " — run /deep-map:setup to re-provision this project"))
        elif kind == "auth" and _is_login_lock(e):
            line("FAIL", "Neo4j", f"logins temporarily LOCKED at {_uri_for_message()} — the instance is up "
                                  f"but refuses DeepMap's logins after too many failed attempts in a row "
                                  f"({_LOCK_CODE}; every new failed attempt prolongs the lock). "
                                  f"{_login_lock_remedy()}")
        elif kind == "auth":
            line("FAIL", "Neo4j", f"authentication failed ({type(e).__name__}) at {_uri_for_message()} — "
                                  f"the instance is up but rejected the credentials; {_auth_hint()}")
        elif kind == "query":
            line("FAIL", "Neo4j", f"reachable at {_uri_for_message()} but a census query failed "
                                  f"({_short_error(e)}) — run /deep-map:setup")
        elif kind == "dropped":
            line("FAIL", "Neo4j", f"dropping connections at {_uri_for_message()} — it accepted doctor's connection "
                                  f"but closed it before answering"
                                  + (", twice in a row" if _DROP_EVENTS else "")
                                  + f" ({_drop_detail(e)}): a partly dead Bolt acceptor or a badly overloaded "
                                  f"instance, not a stopped one; {_hung_remedy()}")
        elif kind == "hung":
            line("FAIL", "Neo4j", f"not answering at {_uri_for_message()} ({type(e).__name__}: no answer within "
                                  f"the driver's deadline — hung or badly overloaded, not stopped); {_hung_remedy()}")
        elif kind == "driver":   # ISS-build:025: main words this kind a driver error and sends the user here
            line("FAIL", "Neo4j", f"driver error talking to {_uri_for_message()} ({_short_error(e)}) — the Neo4j "
                                  f"driver failed on the client side, so nothing showed the instance to be down; "
                                  f"{_driver_remedy(e)}")
        else:   # a host name that did not resolve is 'unreachable' too: show the driver's DNS text (docs[3])
            dns = _dns_failure_text(e)
            line("FAIL", "Neo4j", f"unreachable ({type(e).__name__}) at {_uri_for_message()} — "
                                  + (f"{dns} — " if dns else "") + _down_remedy(e))
        core_ok = False

    # ROUND-8 J6: a partly dead Bolt acceptor — a JVM whose Bolt event loop died (typically after a long
    # sleep/resume) keeps accepting TCP connections but force-closes some new ones, so setup's "already up" and a
    # lucky census connection both miss it. Evidence: this instance's own debug.log (only while something ACCEPTED
    # doctor's connection — a stopped instance's old log says nothing about now; never an operator NEO4J_URI's), and
    # a connection doctor itself saw dropped and retried.
    try:
        sig = (_bolt_acceptor_signal(_instance_logs_dir(_current_route()))
               if (neo4j_live or db_fail_kind in ("dropped", "hung")) else None)
        also = " (doctor's own first connection was dropped too)" if (sig and _DROP_EVENTS) else ""
        if sig:
            n = int(sig.get("events") or 1)
            when = f" (last {sig['last']})" if sig.get("last") else ""
            line("WARN", "Bolt acceptor",
                 f"{n} new connection(s) force-closed since this instance started{when}{also} — Neo4j logged 'event "
                 "executor terminated': one of its Bolt event loops died (typically after a long sleep/resume), so "
                 "about 1 in 20-40 NEW connections is dropped (the CLI retries once; a hook or a new MCP pool "
                 f"connection can still fail). {_capitalized(_hung_remedy())}")
            degrade("Neo4j's Bolt acceptor is partly dead (new connections are force-closed since it started) — "
                    "restart the instance: provision.py stop, then heal (the graph is kept)")
        elif _DROP_EVENTS and neo4j_live:
            line("WARN", "Neo4j connections",
                 f"the server dropped doctor's first connection ({_DROP_EVENTS[0].get('error')}) and the retry "
                 "answered — a running instance that drops new connections most likely has a partly dead Bolt "
                 f"acceptor (or is badly overloaded); {_hung_remedy()}")
            operator = getattr(_current_route(), "source", None) == "env"
            degrade("Neo4j dropped a new connection (retried) — its Bolt acceptor may be partly dead: "
                    + ("check the operator-managed NEO4J_URI endpoint" if operator else
                       "restart the instance (provision.py stop, then heal; the graph is kept)"))
    except Exception as e:  # noqa: BLE001
        line("WARN", "Bolt acceptor", f"could not probe ({type(e).__name__})")

    try:
        P = _load("provision")
        search_port, embed_port, port_src, port_problem = _project_mcp_ports(P, root)
        host = P.mcp_host()
        blocker = _setup_blocker(root)
        for name, port in (("deepmap-search", search_port), ("deepmap-embed", embed_port)):
            if port_problem:   # those ports are not this project's: never probe them as if they were
                line("WARN", f"MCP {name}", f"http://{host}:{port}/mcp — {port_problem}")
                degrade(f"MCP {name}: this project has no MCP servers of its own (run /deep-map:setup)")
                continue
            up = P.is_http_up(host, port)
            if up and _blocks(blocker, name, port):
                # I4/H1: setup FAILED on this port — a listener it could not restart or prove is this project's
                # current server (a pre-upgrade server may still answer from another graph). UP is not healthy.
                why = str(blocker.get("reason") or "an unreadable setup blocker record")
                line("WARN", f"MCP {name}", f"http://{host}:{port}/mcp  UP — but held by a server setup could not "
                                            f"restart or attribute to this project (setup blocker: {why})")
                degrade(f"MCP {name}: held by a server setup could not restart — stop it by hand, then "
                        "/deep-map:setup")
                continue
            if up and refused_claim:
                # G1: this project's servers were started on the base claim refused above, so they read
                # THAT store — another project's graph. UP is not healthy here.
                line("WARN", f"MCP {name}",
                     f"http://{host}:{port}/mcp  UP — but it was started on this project's base-store claim "
                     "refused above, so its deepmap_search/deepmap_impact answer from that store (another "
                     "project's graph, not this one's); run /deep-map:setup (it gives this project its own "
                     "instance and restarts its servers there)")
                degrade(f"MCP {name} serves another project's graph (this project's base-store claim is "
                        "refused) — run /deep-map:setup")
                continue
            src_note = "" if port_src == "registry" else f"  [ports: {port_src}]"
            line("OK" if up else "WARN", f"MCP {name}",
                 f"http://{host}:{port}/mcp" + ("  UP" if up else "  down — re-run /deep-map:setup")
                 + src_note)
            if not up:
                degrade(f"MCP {name} down (tools won't connect)")
    except Exception as e:  # noqa: BLE001
        line("WARN", "MCP servers", f"could not probe ({type(e).__name__})")

    # v0.27: per-project Neo4j instance visibility. Closes the doc/code gap where only
    # `provision status` printed the isolation line — `doctor` now emits it AND a per-instance table
    # (bolt uri, running/hibernated, data-dir size, last_used). Informational; never flips core_ok.
    try:
        P = _load("provision")
        PR = _load("port_registry")
        # a NEO4J_URI the routing rule REFUSED (route kind "uri", the FAIL line above) routes this run nowhere: it is
        # not an operator-managed endpoint in force, so the line must not say it overrides the per-project routing
        print("  " + P.isolation_note(edition, PR.registry_stats(), escape_hatch=P._USER_SET_NEO4J_URI,
                                      uri_refused=getattr(_LAST_ROUTE, "kind", None) == "uri"))
        P.print_instance_table()
        note = P.neo4j_upgrade_note_for(root) if hasattr(P, "neo4j_upgrade_note_for") else None
        if note:
            print("  " + note)   # a newer Neo4j patch release for this project's store (informational)
    except Exception as e:  # noqa: BLE001
        line("WARN", "isolation", f"could not probe ({type(e).__name__})")

    try:
        import importlib
        for mod in ("torch", "sentence_transformers"):
            importlib.import_module(mod)
        line("OK", "ML stack", "torch + sentence-transformers importable")
    except Exception as e:  # noqa: BLE001
        line("WARN", "ML stack", f"{type(e).__name__} — search degrades to FTS-only; {_ml_stack_remedy()}")
        degrade("ML stack not importable — vector search off (FTS-only)")

    # The LAST SYNC's verdict + per-language / per-dir coverage (the manifest the sync wrote — the Rails
    # honesty layer): "plenty of nodes overall" must not mask a dead app/models/, and a non-READY last
    # sync must WARN and feed the verdict with its own reasons (it used to print "[OK] ... DEGRADED"
    # and then "verdict: READY"). Best-effort, no Neo4j needed. Reasons a LIVE probe above already
    # re-checked (embeddings when Neo4j answered; MCP servers) are not repeated from the manifest.
    try:
        cov_lines = _manifest_coverage_lines(root)
        if cov_lines:
            last = str(manifest.get("verdict") or "").upper()
            reasons = _manifest_reasons(manifest)
            live = [r for r in reasons
                    if not ((neo4j_live and r.startswith("VECTOR SEARCH DISABLED"))
                            or r.startswith("MCP servers are not up"))]
            bad = last in ("DEGRADED", "FAILED") or any("LOW COVERAGE" in r for r in reasons)
            line("WARN" if bad else "OK", "coverage (last sync)",
                 "; ".join(ln for ln in cov_lines if ln not in reasons) or "see below")
            for r in reasons:
                line("WARN", "last sync", r)
            for r in live:
                degrade(r)
            if last in ("DEGRADED", "FAILED") and not live and not reasons:
                degrade(f"last sync verdict {last} — re-run /deep-map:refresh and read its output")
    except Exception as e:  # noqa: BLE001
        line("WARN", "coverage", f"could not read the sync manifest ({type(e).__name__})")

    # Graph freshness vs HEAD (the field report's second session found a stale graph with no
    # user-facing surface saying so). Informational: never changes the verdict/exit code.
    try:
        stale = _staleness_line(root)
        if stale:
            line("WARN" if "STALE" in stale else "OK", "freshness", stale)
    except Exception as e:  # noqa: BLE001
        line("WARN", "freshness", f"could not probe ({type(e).__name__})")

    # Extractor engine: the engine the LAST SYNC actually used (manifest `extractor` — the RESOLVED
    # engine, not the mode), so an automatic `auto` -> ast fallback (graphify missing, crashed, or
    # returned nothing — e.g. a Windows path too long for its cache) is reported instead of the
    # importability-based "auto -> graphify" claim. With no manifest yet, report the mode + whether
    # the graphify multi-language engine is importable, as before.
    try:
        import importlib.util
        mode = (os.environ.get("DEEPMAP_EXTRACTOR", "auto").strip().lower() or "auto")
        gfx = importlib.util.find_spec("graphify") is not None
        used = str(manifest.get("extractor") or "").strip().lower()
        # the mode THAT sync ran with (manifest `extractor_requested`), else today's env
        req = str(manifest.get("extractor_requested") or "").strip().lower() or mode
        try:   # engines whose nodes the graph still holds (a refused engine-downgrade prune keeps them)
            others = sorted({str(x) for x in (manifest.get("graph_engines") or []) if x} - {used})
        except Exception:
            others = []
        held = (f"; the graph still holds {', '.join(others)} nodes from an earlier sync (the "
                f"engine-downgrade prune guard kept them)" if used and others else "")
        if used:
            if used == "ast" and req != "ast":
                why = (_manifest_fallback_reason(manifest)
                       or (f"graphify engine NOT installed — {_graphify_install_cmd()}" if not gfx
                           else "graphify failed or returned nothing — see the sync output"))
                if gfx:
                    # An UNSUPPORTED graphify installed now is the likeliest cause (a new release changed
                    # what DeepMap drives) and its fix is the pinned reinstall — say it, once.
                    sup_now, now = _installed_graphify()
                    if sup_now is False and "-m pip install" not in why:
                        why += f"; {_unsupported_graphify_hint(now)}"
                line("WARN", "extractor",
                     f"last sync used ast (Python-only): '{req}' fell back automatically ({why}); "
                     f"non-Python code is not in the graph — fix it, then /deep-map:refresh{held}")
                degrade("last sync fell back to the ast extractor (Python-only) — multi-language "
                        "extraction OFF")
            elif used == "ast":
                line("OK", "extractor", "last sync used ast (Python-only, forced by DEEPMAP_EXTRACTOR)"
                     " — unset it for auto/multi-language" + held)
            else:
                ver = str(manifest.get("graphify_version") or "").strip() if used == "graphify" else ""
                detail = (f"last sync used {used}" + (f" {ver}" if ver else "")
                          + (" (multi-language)" if used == "graphify" else ""))
                status = "OK"
                # How that graphify run built the graph (re-bound self-calls, re-included / blocked
                # engine-skipped dirs, import binding, failed files) — printed under this line.
                installed = _installed_graphify() if (used == "graphify" and gfx) else (None, None)
                # graphify not importable now: the extractor line itself carries the install command,
                # so the facts refer to it instead of repeating it (one command per block).
                facts, facts_warn, fix_given = (
                    _graphify_build_facts(manifest, installed, cmd_above=not gfx)
                    if used == "graphify" else ([], False, False))
                if mode == "ast":
                    detail += "; DEEPMAP_EXTRACTOR=ast is set now — the next sync will be Python-only"
                elif used == "graphify" and not gfx:
                    status = "WARN"
                    detail += ("; graphify engine NOT importable now — the next sync would fall back "
                               f"to ast ({_graphify_install_cmd()})")
                elif used == "graphify":
                    now_warn, now_note = _installed_graphify_note(manifest, installed, fix_given=fix_given)
                    if now_note:
                        detail += f"; {now_note}"
                    status = "WARN" if now_warn else status
                if facts_warn:
                    status = "WARN"
                line(status, "extractor", detail + held)
                for fact in facts:
                    print(f"           {fact}")
        elif mode == "ast":
            line("OK", "extractor",
                 "ast (Python-only, forced) — unset DEEPMAP_EXTRACTOR for auto/multi-language"
                 + ("" if gfx else f"; graphify engine NOT installed ({_graphify_install_cmd()})"))
        elif gfx:
            # No sync yet: name the graphify the FIRST sync will run and whether it is inside the range
            # DeepMap supports — an unsupported one is worth fixing before that sync, not after.
            sup_now, now = _installed_graphify()
            inside = _graphify_in_range(now) if now else (True if sup_now is True else None)
            ver = f"graphify {now}" if now else "graphify"
            if sup_now is False or inside is False:
                line("WARN", "extractor",
                     f"{mode} -> graphify (multi-language); {ver} is installed, OUTSIDE DeepMap's supported "
                     f"range ({_graphify_spec()}) — install a supported one into DeepMap's Python before the "
                     f"first sync: {_graphify_install_cmd()} (no sync manifest yet)")
            else:
                rng = (f", inside DeepMap's supported range ({_graphify_spec()})" if inside else "")
                line("OK", "extractor", f"{mode} -> graphify (multi-language); {ver} importable{rng} "
                                        f"(no sync manifest yet)")
        elif mode == "auto":
            # auto with no graphify -> silently falls back to ast (Python-only). Surface it.
            line("WARN", "extractor",
                 "auto but graphify NOT installed — falling back to ast (Python-only); "
                 f"{_graphify_install_cmd()} for multi-language")
            degrade("DEEPMAP_EXTRACTOR=auto but graphify engine missing — multi-language OFF (ast fallback)")
        else:   # mode == "graphify", no engine -> extraction yields nothing (no fallback)
            line("FAIL", "extractor",
                 f"graphify forced but NOT installed ({_graphify_install_cmd()}) — extraction yields nothing")
            degrade("DEEPMAP_EXTRACTOR=graphify but graphify engine missing — extraction yields nothing")
    except Exception as e:  # noqa: BLE001
        line("WARN", "extractor", f"could not probe ({type(e).__name__})")

    # RM-1 learning loop (opt-in, offline): the ONE status line. Informational — it never touches
    # core_ok / degraded_reasons, so it can't flip the verdict or the exit code. Live ranking stays
    # equal-weight unless the separate 4-condition gate opens (G4); this line just says why.
    try:
        LL = _load("learning_loop")
        _lst, _ldetail = LL.format_status_line(LL.learning_status(root))
        line(_lst, "learning", _ldetail)
    except Exception as e:  # noqa: BLE001
        line("WARN", "learning", f"could not probe ({type(e).__name__})")

    # DeepMap's background refresh (build/auto_sync.py): informational — never touches core_ok / degraded_reasons.
    try:
        _as_level, _as_text = _load("auto_sync").status_line(root)
        line(_as_level, "auto-sync", _as_text)
    except Exception as e:  # noqa: BLE001
        line("WARN", "auto-sync", f"could not probe ({type(e).__name__})")

    verdict = "FAILED" if not core_ok else ("DEGRADED" if degraded_reasons else "READY")
    print(f"\n[deepmap] verdict: {verdict}")
    for r in degraded_reasons:
        print(f"  - {r}")
    print("All core checks passed." if core_ok
          else "Core needs attention (Neo4j + a populated graph) — see above.")
    # Exit 0 stays for DEGRADED-but-usable (FTS-only search still works) so scripts/CI aren't broken;
    # only a core failure (Neo4j down / 0 nodes) is non-zero. The verdict line above makes DEGRADED
    # visible without changing the exit contract.
    return 0 if core_ok else 1


def _db_error_types():
    """The narrow set of exceptions that mean 'Neo4j is unreachable / the DB errored' (plus an
    unresolvable per-project endpoint), imported lazily so a non-Neo4j command never hard-depends on
    the driver package. Intentionally scoped — NOT a bare Exception — so argparse/programming bugs
    still surface with a real traceback."""
    try:
        from neo4j.exceptions import Neo4jError, DriverError
        return (Neo4jError, DriverError, OSError, ProjectResolutionError)
    except Exception:
        return (OSError, ProjectResolutionError)


def main():
    global _LAST_ROUTE
    _utf8()
    _LAST_ROUTE = None   # one invocation, one route (in-process callers must not inherit a stale one)
    _ROOT_CACHE.clear()  # ... and one project-root resolution (a manifest may have appeared since)
    _DROP_EVENTS.clear()  # ... and one record of dropped-and-retried connections (J6)
    args = build_parser().parse_args()
    try:
        return args.fn(args)
    except _db_error_types() as exc:
        # A query command (search/impact/status/sync) hit a driver/connectivity/DB error — surface a
        # CLASSIFIED one-liner (auth failure / unreachable / query too expensive / unresolvable
        # project) instead of a raw traceback, and exit non-zero (doctor self-handles, so it never
        # reaches here). See build/retrieval.py's fail-loud classifier for the server-side story.
        print(_db_error_message(args.cmd, exc), file=sys.stderr)
        return 3


if __name__ == "__main__":
    import deps_bootstrap  # noqa: E402 — stdlib only: re-run under DeepMap's own environment when this Python lacks it
    _rc = deps_bootstrap.reexec_entry(Path(__file__).resolve(), sys.argv[1:])
    raise SystemExit(main() if _rc is None else _rc)
