#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DeepMap — per-project PORT REGISTRY (local-only, zero-egress): run several projects side-by-side,
each with its OWN MCP servers AND its OWN Neo4j instance (isolated graph), without clashing on ports.

Each project gets TWO distinct free port pairs, recorded in one small global registry at
<DEEPMAP_HOME>/registry.json (~/.deepmap by default — the same home provision.py / sync.py /
learned_prior.py use): the MCP `(search, embed)` pair (base 8787/8788) AND the Neo4j `(bolt, http)`
pair (base 7687/7474). `provision.py setup` allocates both, launches the detached MCP servers on the
first pair, and stands up a per-project Neo4j INSTANCE on the second (its own data dir + conf via
NEO4J_CONF); it writes a per-project `<root>/.mcp.json` pointing at the MCP pair and injects
`NEO4J_URI=bolt://127.0.0.1:<bolt>` (bolt_uri — ROUND-8 J4) so every consumer (sync / detached
servers / CLI) reaches THIS project's graph. The SessionStart heal hook LOOKS both pairs back up on
revive.

Isolation is now REAL at both layers: distinct MCP servers (routed via each project's own `.mcp.json`,
the only wiring: the plugin ships none) AND distinct Neo4j instances (one graph each). Graph-level
isolation uses N Community instances (one binary, per-instance NEO4J_CONF dirs) — Community can't host
a per-project *database* (Enterprise-only), so DeepMap runs a per-project *instance* instead. An
explicit `NEO4J_URI` (or BYO/Aura endpoint) is the escape hatch back to a single shared instance.
See docs/known-limitations.md ("Per-project isolation") and provision.isolation_note (the honest doctor line).

Discipline (mirrors build/learned_prior.py + build/instance_state.py):
  * STDLIB-ONLY and self-contained (the identity/key/lock helpers are small mirrors of the sync.py /
    learned_prior.py originals, copied to keep this module dependency-light — importing sync would
    pull in the whole extraction/ML stack). The ONE cross-module call is the free-port probe, which
    lazily delegates to provision._port_in_use (with a local fallback) so tests can stub it.
  * NEVER guesses another project's resources. The base pairs (8787/8788, 7687/7474) belong to the
    FIRST project on a multi-project machine, so no failure path RECORDS them for another project: an
    allocation that can't serialise (lock timeout), can't read the registry, finds no free pair, or
    can't persist its choice returns None and the caller fails loud; neo4j_uri_for RAISES
    RegistryUnreadableError when the registry exists but can't be read or recognised. The ONE exception
    to "None on failure" (ISS-build:027): an UNRECOGNIZED registry (garbage JSON, an unknown version, a
    bad shape — possibly a newer DeepMap's schema) makes the allocators return the BASE pair WITHOUT
    writing it (the unknown-schema contract: nothing is clobbered) — a pair recorded for nobody, which a
    caller must not use: confirm a returned pair with lookup_ports / lookup_neo4j_ports (None there) or
    check registry_status() first, as provision.apply_ports_for and benchmark/clone_graph do. The
    read-only helpers (lookup_*, registry_stats, touch_*) still never raise. An unknown/garbage registry
    is NEVER overwritten (that would drop other projects' allocations).
  * ONE routing rule: resolve_neo4j_route(root) is the single answer to "which Neo4j holds this
    project's graph" for the CLI, sync, indexes, provision and the hooks (registry pair > the project's
    own instance conf > no route when another project owns 7687 — unless the base store's owner record
    names THIS project, which voids that claim (G4) > a LEGACY 7687 the caller verifies with
    legacy_owner_verdict). neo4j_uri_for is its thin, raising wrapper. An explicit NEO4J_URI comes first,
    judged by the ONE NEO4J_URI scheme rule, neo4j_uri_problem: Bolt only (DeepMap decides, not the installed
    neo4j driver — 6.4 reads http:// as a preview HTTP driver); anything else is a route problem in its words.
  * ONE owner predicate (F1): owner_is_this_project(root, owner) decides whether a recorded graph owner
    (a :_DeepMapMeta stamp, the base store's owner record) is this project — for sync, provision and
    the legacy-route check alike. Containment counts only within ONE project identity (a subdirectory
    of the same repo; never a nested repo or a non-git folder around/inside it), plus a vanished
    narrower stamp and a pre-UTF-8-fix identity of this project (a garbled stamp inside it is mapped back
    to its real path and judged by the same rule). An owner that is not an absolute path on THIS OS is
    another project, never resolved against the working directory (G3).
  * The registry is rewritten with temp + os.replace, and on Windows a reader holding the file open
    makes the replace fail (and a replace in flight makes the read fail) — a sharing violation, not
    a missing file. Both sides therefore retry a transient OSError for a short, bounded budget.
  * The allocate path takes a DEDICATED, bounded-blocking OS lock (never the registry file itself)
    so two concurrent `setup`s can't both grab the same free port (a real TOCTOU).
  * The project key is sha1 of git's toplevel decoded as UTF-8. Builds <= v0.27.4 decoded it in the
    locale code page, so on a non-UTF-8 Windows box a non-ASCII project was registered under another
    key; project_key re-finds such an entry (legacy_identities) and migrates it ONCE to the UTF-8 key,
    with everything keyed on it — or, while the project's own instance runs from instances/<old>/,
    keeps the old key (pending_key/aliases record the new one) until it can move safely. Every key
    lookup honours those aliases (_entry_key). An ASCII path never pays for any of this.
  * ROUND-7 I4: setup's MCP BLOCKER is durable (write/read/clear_setup_blocker —
    <DEEPMAP_HOME>/run/setup-blocked-<key>.json): a FAILED setup that left a server it could not restart
    on this project's MCP port is remembered, so the unattended SessionStart path (heal + the hooks) never
    reports that server healthy. mcp_pair_claimed_by_other tells a .mcp.json pair that the registry gives
    to ANOTHER project (a copied checkout's inherited file) from this project's own.
  * ISS-build:013: the interpreter a project's setup ran under is durable too (write/read_setup_python —
    <DEEPMAP_HOME>/run/setup-python-<root id>.json, machine state keyed by the sync root, never a file in
    the project), so the SessionStart heal after a reboot runs under the Python that has DeepMap's
    dependencies, not whichever of python / python3 ran the hook; split_command_line is THE rule for
    reading a command-line DEEPMAP_PYTHON ('py -3').
"""
from __future__ import annotations

import codecs
import hashlib
import json
import locale
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REGISTRY_FILENAME = "registry.json"
LOCK_FILENAME = "registry.lock"           # DEDICATED lock file — never lock registry.json itself
REGISTRY_VERSION = 1
REGISTRY_KIND = "deepmap_port_registry"

BASE_SEARCH = 8787                         # frozen single-project default (keeps .mcp.json drift gates green)
BASE_EMBED = 8788
BASE_BOLT = 7687                           # frozen single-project Neo4j default (zero drift for project #1)
BASE_HTTP = 7474
STRIDE = 2                                 # adjacent pairs: (8787,8788),(8789,8790),... / (7687,7474),(7689,7476),...
MAX_PAIRS = 64                             # scan cap -> never unbounded; exhaustion returns None
_LOCK_WAIT_S = 5.0                         # bounded-blocking acquire budget for the best-effort writers
_ALLOC_LOCK_WAIT_S = 60.0                  # allocation waits longer: its timeout is now a FAILURE (None)
_LOCK_SLEEP_S = 0.05
_IO_RETRY_S = 2.0                          # retry budget for a TRANSIENT registry read / replace error
_IO_SLEEP_S = 0.02

# ---- ROUND-8 J4: the ONE spelling of a DeepMap-generated bolt URI ----------------------------------------
# Every per-project instance binds 127.0.0.1 only (provision's managed conf block writes
# server.bolt.listen_address=127.0.0.1:<bolt>). "localhost" resolves to ::1 FIRST on Windows, so a driver
# dialling bolt://localhost:<port> paid a refused IPv6 connect (~2 s) on EVERY new connection before falling
# back to IPv4 — and anything listening on [::1]:<port> (Docker publishes [::1]:7687) was reached instead of
# the DeepMap instance. So every URI DeepMap GENERATES (routes, provision, CLI, sync, servers, hooks) dials
# the address the instance actually binds. An operator's explicit NEO4J_URI is never rewritten (it is used
# verbatim); code that COMPARES or KEYS URIs normalizes the loopback spelling (canonical_bolt_uri), so a
# URI written by an older build (bolt://localhost:<port>) still names the same endpoint.
LOOPBACK_HOST = "127.0.0.1"
_LOOPBACK_NAMES = frozenset({"localhost", "127.0.0.1", "::1", "0:0:0:0:0:0:0:1"})
_BOLT_DEFAULT_PORT_SCHEMES = frozenset({"bolt", "bolt+s", "bolt+ssc", "neo4j", "neo4j+s", "neo4j+ssc"})
# The ONLY schemes DeepMap dials (neo4j_uri_problem — the NEO4J_URI scheme rule): the neo4j driver's own Bolt schemes.
# Its http:// / https:// (a preview HTTP Query API driver since 6.4) are never among them.
BOLT_URI_SCHEMES = _BOLT_DEFAULT_PORT_SCHEMES
_URI_RE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9+.-]*)://(?:([^@/?#]*)@)?(\[[^\]]*\]|[^/:?#@]*)(?::(\d*))?(.*?)\s*$")


def bolt_uri(port) -> str:
    """``bolt://127.0.0.1:<port>`` — THE form of every bolt URI DeepMap generates (ROUND-8 J4).

    `port` is an int (an int-valued string is accepted); 0 is allowed (provision's "no route" sentinel
    port). Raises ValueError for anything that is not a port number (a bool, a negative or > 65535 value,
    garbage) — callers pass registry/conf ports, which are validated ints."""
    if isinstance(port, str) and port.strip().isascii() and port.strip().isdigit():
        p = int(port.strip())
    elif isinstance(port, int) and not isinstance(port, bool):
        p = int(port)
    else:
        raise ValueError(f"not a port number: {port!r}")
    if not 0 <= p <= 65535:
        raise ValueError(f"not a port number: {port!r}")
    return f"bolt://{LOOPBACK_HOST}:{p}"


def is_loopback_host(host) -> bool:
    """True iff `host` names THIS machine's loopback interface: localhost, 127.0.0.1, ::1 or its expanded form
    0:0:0:0:0:0:0:1 (bracketed or not; any case). THE loopback rule: sync, the CLI, the hooks and provision
    (provision._is_loopback) all apply this one set. Never raises (-> False)."""
    try:
        h = str(host or "").strip().lower()
        if h.startswith("[") and h.endswith("]"):
            h = h[1:-1]
        return h in _LOOPBACK_NAMES
    except Exception:
        return False


def canonical_bolt_uri(uri):
    """`uri` in the form used to COMPARE or KEY bolt URIs (never to dial an operator's endpoint): scheme and
    host lower-cased, a loopback host (localhost / ::1 / [::1]) spelled 127.0.0.1, a missing port on a
    bolt/neo4j scheme made explicit (7687, the driver's default), a trailing '/' dropped. So
    ``bolt://localhost:7689`` (what builds <= v0.27.x generated) and ``bolt://127.0.0.1:7689`` (what this
    build generates) are ONE endpoint. None for None / an empty or unparseable value. Never raises."""
    try:
        if uri is None:
            return None
        s = str(uri).strip()
        m = _URI_RE.match(s)
        if not s or not m:
            return s or None
        scheme, userinfo, host, port, rest = m.groups()
        scheme = scheme.lower()
        host = (host or "").lower()
        if is_loopback_host(host):
            host = LOOPBACK_HOST
        if not port and scheme in _BOLT_DEFAULT_PORT_SCHEMES:
            port = str(BASE_BOLT)
        if rest == "/":
            rest = ""
        auth = f"{userinfo}@" if userinfo else ""
        return f"{scheme}://{auth}{host}{':' + port if port else ''}{rest}"
    except Exception:
        return str(uri) if uri is not None else None


def same_bolt_endpoint(a, b) -> bool:
    """True iff two bolt URIs name the same endpoint once the loopback spelling is normalized
    (canonical_bolt_uri): ``bolt://localhost:7689`` == ``bolt://127.0.0.1:7689``. False when either is
    empty. Never raises."""
    try:
        ca, cb = canonical_bolt_uri(a), canonical_bolt_uri(b)
        return bool(ca) and ca == cb
    except Exception:
        return False

# The registry's self-describing note. NOTE: no longer "safe to delete" — it now maps each project to
# its OWN Neo4j instance's ports; losing it drops that map (instances are re-adopted from their conf).
_REGISTRY_NOTE = ("Local-only per-project port map (DeepMap): MCP (search, embed) + Neo4j (bolt, http) "
                  "per project. Each project has its OWN Neo4j instance (isolated graph). Do NOT delete "
                  "while projects are provisioned — the port map is lost (instances recover from conf).")


class RegistryUnreadableError(RuntimeError):
    """The registry file EXISTS but could not be read (a persistent I/O error) or recognised (garbage
    / unknown version). Raised by neo4j_uri_for instead of returning the base bolt 7687, which on a
    multi-project machine is ANOTHER project's (or another program's) database.

    It is also the BASE class of every "this project has no safe Neo4j route" error neo4j_uri_for
    raises (see NoProjectInstanceError), so one `except RegistryUnreadableError` handles them all.
    `Neo4jRouteError` is the same class under its general name."""


class NoProjectInstanceError(RegistryUnreadableError):
    """This project has NO DeepMap Neo4j instance of its own, and the only other candidate — the base
    bolt 7687 — is registered to ANOTHER project (never set up here, or its pair was dropped by
    instance-rm). Raised by neo4j_uri_for instead of answering from that project's graph."""


class Neo4jUriError(RegistryUnreadableError):
    """The operator's NEO4J_URI is not a Bolt URI DeepMap dials (neo4j_uri_problem — an http:// Neo4j Browser
    address, another scheme, no scheme, blank, whitespace, a bad port). Raised by neo4j_uri_for; its message is the
    rule's own one-line reason, which names the fix."""


Neo4jRouteError = RegistryUnreadableError   # the general name for "no safe route" (all three classes above)


# ---- home / paths (shared DEEPMAP_HOME resolution) --------------------------
def home_dir(env=None) -> Path:
    """DEEPMAP_HOME (~/.deepmap by default) — the same resolution provision.py/sync.py use."""
    e = env if env is not None else os.environ
    return Path(e.get("DEEPMAP_HOME", str(Path.home() / ".deepmap")))


def registry_path(env=None) -> Path:
    return home_dir(env) / REGISTRY_FILENAME


def _lock_path(env=None) -> Path:
    return home_dir(env) / LOCK_FILENAME


# ---- project identity / key (mirror sync._project_identity + learned_prior.project_key) ----
def git_longpaths_args() -> list:
    """``["-c", "core.longpaths=true"]`` on Windows, ``[]`` elsewhere — the prefix every DeepMap git probe
    puts before ``-C <root>`` (ROUND-8 J6). Git for Windows refuses any path past MAX_PATH (260) unless
    core.longpaths is set, so in a repository with a long root it could not read its own loose objects
    (``rev-list`` failed) and ``status`` reported a committed >260-char file as an uncommitted change. The
    flag is harmless where paths are short. A NEW list on every call (callers may extend it)."""
    return ["-c", "core.longpaths=true"] if os.name == "nt" else []


def _toplevel_raw(root, timeout: float = 30):
    """(returncode, raw stdout bytes) of `git -C <root> rev-parse --show-toplevel`, or None when git is
    absent or could not run / timed out. BYTES, so the current identity and the pre-fix one (see
    legacy_identities) are both derived from ONE git call."""
    git = shutil.which("git")
    if not git:
        return None
    try:
        p = subprocess.run([git, *git_longpaths_args(), "-C", str(root), "rev-parse", "--show-toplevel"],
                           capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    out = p.stdout
    if isinstance(out, str):          # a stub that answers in text mode
        out = out.encode("utf-8", "surrogateescape")
    return (p.returncode, out or b"")


def _decode_identity(root, res) -> str:
    """The identity from _toplevel_raw's result: git's UTF-8 output decoded as UTF-8."""
    if res is not None:
        rc, raw = res
        top = raw.decode("utf-8", "surrogateescape").strip()
        if rc == 0 and top:
            return str(Path(top).resolve())
    return str(Path(root).resolve())


def _key_of(identity: str) -> str:
    """sha1[:12] of the forward-slash identity — the registry key of a project identity."""
    return hashlib.sha1(str(identity).replace("\\", "/").encode("utf-8", "replace")).hexdigest()[:12]


def project_identity(root, *, timeout: float = 30) -> str:
    """The canonical project id for a root: the git toplevel if `root` is in a git repo, else the
    resolved path. All subdirs of one repo map to the SAME id (mirrors sync._project_identity), so a
    subdir setup and a later SessionStart from a different subdir resolve to one registry entry.

    git prints the path as UTF-8, so it is decoded as UTF-8 — NOT in the locale code page, which is
    what text=True alone does. Without UTF-8 mode on a cp1252/cp1255 Windows box that garbled a
    non-ASCII path (or crashed the reader thread and fell back to the subdir), giving a different key
    and a registry miss that routed the CLI/hooks to another project's instance. surrogateescape
    keeps any non-UTF-8 byte stable, exactly as the filesystem decoder does on POSIX. Registries
    written by those older builds are migrated by project_key (see there)."""
    try:
        return _decode_identity(root, _toplevel_raw(root, timeout))
    except Exception:
        return str(root)


# ---- ROUND-3 E1: the pre-fix (<= v0.27.4) identity + the one-time key migration -----------------
# v0.27.4 and earlier ran the same git call with text=True and no encoding, i.e. decoded it in the
# LOCALE code page. On a Windows box whose ANSI code page is not UTF-8, a non-ASCII toplevel either came
# out GARBLED ("C:/Users/JosÃ©/proj" on cp1252) or did not decode at all — the reader thread died and the
# identity fell back to the path the command ran from. The registry entry such a build wrote is keyed on
# that identity, so the fixed key misses it; the entry still CLAIMS its ports (7687 for a first project),
# which made the project look like "another project's instance". project_key therefore looks for the
# pre-fix key on a miss and moves the entry (and everything keyed on it) to the UTF-8 key — or, while the
# project's own Neo4j instance runs from instances/<old>/ (moving a live store is unsafe; Windows refuses
# anyway), keeps the entry under the old key with `pending_key`/`aliases` naming the new one and keeps
# answering the OLD key, so every key -> instance-dir mapping stays right until the move can complete.
MIGRATION_LOG = "registry-migrations.log"    # <DEEPMAP_HOME>/registry-migrations.log (one line per event)
_MIGRATIONS = [0]                            # registry rewrites by a migration in THIS process (a cheap signal)
# How long a migration waits for the registry lock: the holder's worst case — another migration's registry
# read + write, each retried for up to _IO_RETRY_S through a Windows sharing violation — plus a margin. A
# shorter wait (it was 1 s) made a racer give up while the holder was still writing and answer the LEGACY
# key although the move then completed (a provision call in it would recreate an empty instances/<old>).
# Only a CONTENDED lock waits at all, and only while a pre-fix entry is still being migrated.
_MIGRATE_LOCK_WAIT_S = 3 * _IO_RETRY_S


def _legacy_encodings() -> list:
    """The code pages a pre-fix build's text=True decode could have used, as codec names: the locale's
    own (locale.getencoding — the ANSI code page even under UTF-8 mode, which the hooks and commands now
    enable) and the preferred one. UTF-8 stays in the list (strict): it reproduces the old decode-error
    fallback for bytes that are not valid UTF-8. A module-global so gates can pin it. Never raises."""
    cands = []
    try:
        getenc = getattr(locale, "getencoding", None)          # 3.11+
        if getenc is not None:
            cands.append(getenc())
        elif os.name == "nt":
            import ctypes
            cands.append(f"cp{ctypes.windll.kernel32.GetACP()}")
    except Exception:
        pass
    try:
        cands.append(locale.getpreferredencoding(False))
    except Exception:
        pass
    cands.append("utf-8")
    out: list = []
    for enc in cands:
        try:
            name = codecs.lookup(str(enc)).name
        except Exception:
            continue
        if name not in out:
            out.append(name)
    return out


def _head_identity(root, res, enc):
    """(identity, crashed): what the pre-fix project_identity returned when its locale code page was
    `enc` — a strict text-mode decode; a decode error killed the Windows reader thread (stdout None ->
    the resolved root) or escaped subprocess.run on POSIX (-> str(root)). (None, False) for an unknown
    codec. Never raises."""
    try:
        if res is None:
            return str(Path(root).resolve()), False
        rc, raw = res
        crashed = False
        try:
            text = raw.decode(enc)
        except UnicodeDecodeError:
            crashed = True
            if os.name != "nt":
                return str(root), True
            text = ""
        except LookupError:
            return None, False
        top = text.replace("\r\n", "\n").replace("\r", "\n").strip()
        if rc == 0 and top:
            return str(Path(top).resolve()), crashed
        return str(Path(root).resolve()), crashed
    except Exception:
        return str(root), False


def _legacy_candidates_enc(root, res, ident):
    """([(identity, codec, from_decode_error)], any_decode_error): the distinct pre-fix identities of
    `root` that differ from the current one `ident` (empty for an ASCII toplevel), each with the code
    page that produced it — so a garbled owner stamp can be mapped back to its real path (_ungarble).
    Never raises."""
    out: list = []
    crashed_any = False
    try:
        if res is None or not res[1] or res[1].isascii():
            return out, False
        for enc in _legacy_encodings():
            li, crashed = _head_identity(root, res, enc)
            crashed_any = crashed_any or crashed
            if li and li != ident and all(li != x for x, _, _ in out):
                out.append((li, enc, crashed))
    except Exception:
        pass
    return out, crashed_any


def _legacy_candidates(root, res, ident):
    """([(identity, from_decode_error)], any_decode_error): the distinct pre-fix identities of `root`
    that differ from the current one `ident` (empty for an ASCII toplevel). Never raises."""
    try:
        cands, crashed_any = _legacy_candidates_enc(root, res, ident)
        return [(li, crashed) for li, _enc, crashed in cands], crashed_any
    except Exception:
        return [], False


def _ungarble(text, enc):
    """The string a pre-fix build garbled into `text` by decoding UTF-8 bytes in code page `enc` — i.e.
    `text` re-encoded in `enc` and decoded as UTF-8 (strict) — or None when `text` cannot be such a decode
    (a character outside `enc`, or bytes that are not UTF-8). Maps a garbled owner stamp back to the real
    path it named. Never raises."""
    try:
        return str(text).encode(enc).decode("utf-8")
    except Exception:
        return None


def legacy_identities(root, *, timeout: float = 30) -> list:
    """The identities a pre-fix (<= v0.27.4) build computed for `root` that differ from today's — the
    locale-code-page decode of git's output, or, where those bytes did not decode, the old fallback (the
    resolved root itself). [] for an ASCII path. Used to recognise registry entries and graph owner
    stamps written by those builds. Never raises."""
    try:
        res = _toplevel_raw(root, timeout)
        ident = _decode_identity(root, res)
        return [li for li, _ in _legacy_candidates(root, res, ident)[0]]
    except Exception:
        return []


def _norm_path(p) -> str:
    """normcase'd, forward-slash, trailing-slash-free form of a path string (a lexical compare)."""
    return os.path.normcase(str(p).replace("\\", "/").rstrip("/")).replace("\\", "/")


def _strictly_inside(child, parent) -> bool:
    """child is a strict descendant of parent (lexical, case-insensitive on Windows). Never raises."""
    try:
        c, p = _norm_path(Path(child).resolve()), _norm_path(Path(parent).resolve())
        return c != p and c.startswith(p + "/")
    except Exception:
        return False


def _entry_key(projects, key):
    """The registry key whose entry IS project `key`: `key` itself, else the entry that lists it as an
    alias — a migrated entry's old key (`aliases`), or a deferred entry's UTF-8 key (`pending_key`). Every
    key lookup goes through this. None if unknown. Never raises."""
    try:
        if not key or not isinstance(projects, dict):
            return None
        if isinstance(projects.get(key), dict):
            return key
        for k, ent in projects.items():
            if not isinstance(ent, dict):
                continue
            aliases = ent.get("aliases")
            if ent.get("pending_key") == key or (isinstance(aliases, list) and key in aliases):
                return k
    except Exception:
        pass
    return None


def _legacy_entry_key(root, key, ident, res, projects, timeout):
    """The registry key of the entry a pre-fix build wrote for this project, or None:
      * the key of a locale-decoded (garbled) identity whose entry records that identity as its root, as
        long as that garbled path is not a REAL directory (then it could be a different project);
      * the key of the old decode-error fallback (the root the command ran from, inside this repo);
      * after a decode error, any entry recorded for a SUBDIRECTORY of this repo (the old build keyed a
        setup run from a subdirectory on that subdirectory) whose current identity is this repo — a
        nested repository's own entry is never taken.
    Never raises."""
    try:
        cands, crashed = _legacy_candidates(root, res, ident)
        for li, from_error in cands:
            lk = _key_of(li)
            ent = projects.get(lk)
            if lk == key or not isinstance(ent, dict) or ent.get("pending_key"):
                continue
            rec = ent.get("root")
            if rec and _norm_path(rec) != _norm_path(li):
                continue
            if from_error:
                if not _strictly_inside(li, ident):
                    continue
            elif Path(li).exists():
                continue
            return lk
        if crashed:
            best = None
            for k, ent in projects.items():
                if k == key or not isinstance(ent, dict) or ent.get("pending_key") or ent.get("migrated_from"):
                    continue
                rec = ent.get("root")
                if not rec or _key_of(rec) != k or not _strictly_inside(rec, ident) or not Path(rec).is_dir():
                    continue
                if _norm_path(project_identity(rec, timeout=timeout)) != _norm_path(ident):
                    continue   # a nested repository is its own project
                rank = (_pair_from_entry(ent, ("bolt_port", "http_port")) is not None, str(ent.get("last_used") or ""))
                if best is None or rank > best[0]:
                    best = (rank, k)
            if best is not None:
                return best[1]
    except Exception:
        pass
    return None


_PROC = "/proc"   # a test seam (== provision._PROC): where the Linux process table is read


def _windows() -> bool:
    """os.name == "nt" — a seam so the POSIX zombie branch can be tested on any OS (it never signals there)."""
    return os.name == "nt"


def _proc_state(pid) -> str | None:
    """The state letter of a LINUX process (/proc/<pid>/stat field 3: R S D T Z X ...), or None when there is no
    readable /proc entry (not Linux, gone, hidepid). A stdlib mirror of provision._proc_state. Never raises."""
    try:
        raw = Path(_PROC, str(int(pid)), "stat").read_text(encoding="utf-8", errors="replace")
        rest = raw[raw.rindex(")") + 2:].split()
        return rest[0] if rest else None
    except Exception:
        return None


def _proc_live_thread(pid) -> bool:
    """True iff a LINUX process whose main thread is a zombie still runs a thread ("Zl": a JVM still shutting
    down, its store files still open). /proc/<pid>/task/*/stat, else the Threads: count. A mirror of
    provision._proc_live_thread. Never raises (False when neither can be read)."""
    try:
        task = Path(_PROC, str(int(pid)), "task")
        if task.is_dir():
            for t in task.iterdir():
                try:
                    raw = (t / "stat").read_text(encoding="utf-8", errors="replace")
                    rest = raw[raw.rindex(")") + 2:].split()
                    if rest and rest[0][:1] not in ("Z", "X", "x"):
                        return True
                except Exception:
                    continue
            return False
        for ln in Path(_PROC, str(int(pid)), "status").read_text(encoding="utf-8", errors="replace").splitlines():
            if ln.startswith("Threads:"):
                return int(ln.split()[1]) > 1
    except Exception:
        pass
    return False


def _ps_state(pid) -> str | None:
    """`ps -o stat=` of a process (macOS / BSD: no /proc) — e.g. 'S', 'Z+' — or None. Never raises."""
    try:
        out = subprocess.run(["ps", "-o", "stat=", "-p", str(int(pid))], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=15)
        st = (out.stdout or "").strip()
        return st or None
    except Exception:
        return None


def _pid_alive(pid) -> bool:
    """Is `pid` a running process? Unknown -> True (a store we can't vouch for is never moved).

    ROUND-8 (closes the round-7 W2->W1 NEED): a ZOMBIE is not alive — os.kill(pid, 0) succeeds on one, so a
    stopped JVM left in state Z (a container whose PID 1 does not reap) kept the key migration deferred forever.
    Linux reads /proc/<pid>/stat (Z / X -> dead, unless a thread still runs: "Zl"); macOS / BSD ask
    `ps -o stat=`. The same rule as provision._pid_alive."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        if _windows():
            import ctypes
            from ctypes import wintypes
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.OpenProcess.restype = wintypes.HANDLE
            k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
            k32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
            k32.CloseHandle.argtypes = (wintypes.HANDLE,)
            h = k32.OpenProcess(0x1000, False, pid)     # PROCESS_QUERY_LIMITED_INFORMATION
            if not h:
                return ctypes.get_last_error() == 5      # ACCESS_DENIED: it exists
            try:
                code = wintypes.DWORD()
                if not k32.GetExitCodeProcess(h, ctypes.byref(code)):
                    return True
                return code.value == 259                  # STILL_ACTIVE
            finally:
                k32.CloseHandle(h)
        state = _proc_state(pid)
        if state is not None:
            if state[:1] in ("Z", "X", "x"):
                return _proc_live_thread(pid)   # "Zl": the main thread is gone, the process is still shutting down
            return True
        if os.name == "nt":
            return True   # the POSIX branch reached on Windows through the test seam: never os.kill here
        os.kill(pid, 0)
        if Path(_PROC).is_dir():
            return True   # Linux with no readable entry for it (hidepid): it exists
        return not str(_ps_state(pid) or "").startswith("Z")
    except (ProcessLookupError, OverflowError):
        return False
    except PermissionError:
        return True
    except Exception:
        return True


def _instance_busy(inst: Path, ent) -> bool:
    """True when the per-project instance under `inst` may be running: its pid file names a live
    process, or one of its ports (registry entry + its own conf) is listening. Never raises (-> True)."""
    try:
        pf = inst / "run" / "neo4j.pid"
        if pf.is_file():
            try:
                pid = int(pf.read_text(encoding="utf-8").strip())
            except (OSError, ValueError):
                pid = None
            if pid and _pid_alive(pid):
                return True
        ports = set(_pair_from_entry(ent, ("bolt_port", "http_port")) or ())
        try:
            import re
            txt = (inst / "conf" / "neo4j.conf").read_text(encoding="utf-8", errors="replace")
            for rx in (_BOLT_CONF_RE, _HTTP_CONF_RE):
                m = re.search(rx, txt, re.M)
                if m:
                    ports.add(int(m.group(1)))
        except (OSError, ValueError):
            pass
        return any(_port_in_use("127.0.0.1", p) for p in sorted(ports))
    except Exception:
        return True


def _atomic_write_text(path: Path, text: str) -> None:
    """temp + os.replace in the same dir (LF). Raises OSError on failure (callers are best-effort)."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8", newline="\n")
        os.replace(str(tmp), str(path))
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def _retarget_instance(inst_new: Path, old: str, new: str) -> None:
    """After instances/<old> moved to instances/<new>: point the conf's managed `server.directories.*`
    lines at the new dir (provision rewrites the managed block on the next start anyway) and re-key the
    Neo4j identity record. Best-effort; never raises."""
    import re
    try:
        conf = inst_new / "conf" / "neo4j.conf"
        if conf.is_file():
            txt = conf.read_text(encoding="utf-8")
            pat = re.compile(r"(instances[/\\])" + re.escape(old) + r"(?=[/\\]|\s*$)")
            lines = txt.splitlines(keepends=True)
            out = [pat.sub(lambda m: m.group(1) + new, ln) if ln.lstrip().startswith("server.directories.")
                   else ln for ln in lines]
            if out != lines:
                _atomic_write_text(conf, "".join(out))
    except Exception:
        pass
    try:
        idf = inst_new / "run" / "neo4j.pid.identity"
        if idf.is_file():
            data = json.loads(idf.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("project_key") == old:
                data["project_key"] = new
                _atomic_write_text(idf, json.dumps(data))
    except Exception:
        pass


def _rekey_side_files(env, old: str, new: str, ident_fwd: str) -> None:
    """Re-key what else names the project by key: the heal status file (run/heal-<key>.json), setup's MCP
    blocker (run/setup-blocked-<key>.json), the MCP
    per-port owner sidecars (run/*.owner) and the graphify output markers (gout/*/deepmap_owner.json —
    the gout dirs themselves are named by the sync ROOT's path, which never depended on the decoding).
    Best-effort per file; never raises."""
    home = home_dir(env)
    run = home / "run"
    try:
        ho, hn = run / f"heal-{old}.json", run / f"heal-{new}.json"
        if ho.is_file():
            if not hn.exists():
                data = json.loads(ho.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    data["project_key"] = new
                    data["root"] = ident_fwd
                _atomic_write_text(hn, json.dumps(data))
            ho.unlink()
    except Exception:
        pass
    try:
        (run / f"heal-{old}.lock").unlink()
    except Exception:
        pass
    try:
        # ROUND-7 I4: setup's durable MCP blocker is keyed the same way — it must survive the move, or a
        # migrated project would lose the FAILED verdict the hooks honour
        bo, bn = run / f"{SETUP_BLOCKER_PREFIX}{old}.json", run / f"{SETUP_BLOCKER_PREFIX}{new}.json"
        if bo.is_file():
            if not bn.exists():
                try:
                    data = json.loads(bo.read_text(encoding="utf-8"))
                except Exception:
                    data = None
                if isinstance(data, dict):
                    data["project_key"] = new
                    data["root"] = ident_fwd
                    _atomic_write_text(bn, json.dumps(data))
                else:
                    _atomic_write_text(bn, bo.read_text(encoding="utf-8", errors="replace"))
            bo.unlink()
    except Exception:
        pass
    try:
        for f in sorted(run.glob("*.owner")) if run.is_dir() else []:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data.get("project_key") == old:
                    data["project_key"] = new
                    data["root"] = ident_fwd
                    _atomic_write_text(f, json.dumps(data))
            except Exception:
                continue
    except Exception:
        pass
    try:
        gout = home / "gout"
        for d in sorted(gout.iterdir()) if gout.is_dir() else []:
            try:
                m = d / "deepmap_owner.json"
                if not m.is_file():
                    continue
                data = json.loads(m.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data.get("project_key") == old:
                    data["project_key"] = new
                    _atomic_write_text(m, json.dumps(data, sort_keys=True))   # graphify_extract's format
            except Exception:
                continue
    except Exception:
        pass


def _log_migration(env, msg: str) -> None:
    """One line in <home>/registry-migrations.log + one stderr notice. Never raises."""
    try:
        path = home_dir(env) / MIGRATION_LOG
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8", newline="\n") as f:
            f.write(f"{_now_iso()} {msg}\n")
    except Exception:
        pass
    try:
        print(f"[deepmap] {msg}", file=sys.stderr)
    except Exception:
        pass


def _migrate_key(old: str, new: str, ident: str, env=None) -> str:
    """Move registry entry `old` (written by a pre-fix build) to the UTF-8 key `new`, under the registry
    lock: the entry (root rewritten, `aliases: [old]`, `migrated_from`), then instances/<old> ->
    instances/<new> (conf directories follow), the heal status file, the MCP owner sidecars and the
    graphify output markers. Returns the key the project is known by afterwards — `new`, or `old` when
    the move is DEFERRED: its instance may be running, the OS refused the rename, instances/<new> already
    exists, or the lock/registry was unavailable. A deferred entry keeps its key and records
    `pending_key: new` + `aliases: [new]`; the next lookup retries. Idempotent; each event is logged once;
    never raises."""
    handle = _acquire_lock(env, wait_s=_MIGRATE_LOCK_WAIT_S)
    if handle is None:     # a concurrent process may be migrating right now
        st, reg = _read_registry(env)
        return new if st == "ok" and isinstance(reg["projects"].get(new), dict) else old
    written = False
    try:
        st, reg = _read_registry(env)
        if st != "ok":
            return old
        projects = reg["projects"]
        if isinstance(projects.get(new), dict):
            return new     # already done (a concurrent process)
        ent = projects.get(old)
        if not isinstance(ent, dict):
            return new     # nothing left to migrate
        ident_fwd = str(ident).replace("\\", "/")
        home = home_dir(env)
        inst_old, inst_new = home / "instances" / old, home / "instances" / new
        moved, why = False, None
        if inst_old.exists():
            if inst_new.exists():
                why = f"{inst_new} already exists"
            elif _instance_busy(inst_old, ent):
                why = "its Neo4j instance is running"
            else:
                try:
                    os.rename(str(inst_old), str(inst_new))
                    moved = True
                except OSError as exc:
                    why = f"its instance directory could not be moved ({type(exc).__name__})"
        if why:
            aliases = [a for a in (ent.get("aliases") if isinstance(ent.get("aliases"), list) else [])]
            before = json.dumps(ent, sort_keys=True)
            if new not in aliases:
                aliases.append(new)
            ent["aliases"] = aliases
            ent["pending_key"] = new
            ent["root"] = ident_fwd
            if json.dumps(ent, sort_keys=True) != before:
                reg["updated_utc"] = _now_iso()
                if _write_registry(reg, env):
                    _MIGRATIONS[0] += 1
                    _log_migration(env, f"project {ident_fwd}: key migration {old} -> {new} DEFERRED ({why}); "
                                        f"it keeps using key {old} until then (one-time upgrade from a "
                                        f"pre-UTF-8 build)")
            return old
        new_ent = dict(ent)
        aliases = [a for a in (ent.get("aliases") if isinstance(ent.get("aliases"), list) else [])
                   if a not in (old, new)]
        aliases.append(old)
        new_ent["aliases"] = aliases
        new_ent.pop("pending_key", None)
        new_ent["root"] = ident_fwd
        new_ent["migrated_from"] = old
        new_ent["migrated_utc"] = _now_iso()
        del projects[old]
        projects[new] = new_ent
        reg["updated_utc"] = _now_iso()
        if not _write_registry(reg, env):
            if moved:
                try:
                    os.rename(str(inst_new), str(inst_old))
                except OSError:
                    pass
            return old
        written = True
        _MIGRATIONS[0] += 1
        if moved:
            _retarget_instance(inst_new, old, new)
        _rekey_side_files(env, old, new, ident_fwd)
        _log_migration(env, f"project {ident_fwd}: registry key migrated {old} -> {new}"
                            + (" (with its Neo4j instance directory)" if moved else "")
                            + " — one-time upgrade from a pre-UTF-8 build")
        return new
    except Exception:
        return new if written else old
    finally:
        _release_lock(handle)


def _effective_key(root, key, ident, res, env, timeout, migrate) -> str:
    """The key the registry knows this project by (see project_key). Never raises (-> `key`)."""
    try:
        status, reg = _read_registry(env)
        if status != "ok":
            return key
        projects = reg["projects"]
        if isinstance(projects.get(key), dict):
            return key
        old = next((k for k, e in projects.items() if isinstance(e, dict) and e.get("pending_key") == key), None)
        if old is None:
            old = _legacy_entry_key(root, key, ident, res, projects, timeout)
        if old is None:
            return key
        return _migrate_key(old, key, ident, env) if migrate else old
    except Exception:
        return key


def project_key(root, *, timeout: float = 30, env=None, migrate: bool = True) -> str:
    """A stable, filesystem/JSON-safe key for a project (sha1[:12] of the resolved forward-slash
    identity). Keyed on the git toplevel so subdirs share one entry (mirrors learned_prior.project_key
    composed with the identity above). `timeout` bounds the git call (hooks pass a short one).

    For a NON-ASCII toplevel it is also the key the REGISTRY knows the project by (E1): when the UTF-8
    key misses and an entry written by a pre-fix build matches (legacy_identities), that entry is
    migrated to the UTF-8 key on the spot; while the migration is deferred (the project's instance is
    running from instances/<old>/) the OLD key is returned, so provision's paths, the heal status file
    and the MCP owner sidecars keep naming the files that exist. `migrate=False` only looks. An ASCII
    toplevel never reads the registry here. Never raises."""
    res = None
    try:
        res = _toplevel_raw(root, timeout)
        ident = _decode_identity(root, res)
    except Exception:
        ident = str(root)
    key = _key_of(ident)
    try:
        if res is None or not res[1] or res[1].isascii():
            return key
        return _effective_key(root, key, ident, res, env, timeout, migrate)
    except Exception:
        return key


def owner_is_this_project(root, owner, *, timeout: float = 30) -> bool:
    """THE owner predicate (ROUND-4 F1 — sync's foreign-owner check, provision's base adoption / base-owner
    record and the D1 legacy-route check all ask this): does a recorded owner — a :_DeepMapMeta
    owning_root stamp, or the base store's owner record — name THIS project (the one `root` is in)?

      * the project's identity (project_identity: the git toplevel, else the path itself), or a path
        inside / around it that maps to the SAME identity (a subdirectory of the same repo)  -> True;
        a nested repository, or a non-git folder inside or around this project, is a project of its own
        (the registry keys it separately) -> False (_same_project_path);
      * a NARROWER stamp whose path no longer exists (an old sync of a since-deleted subdirectory) -> True,
        judged at its nearest existing ancestor (under a nested repository it is that repository's);
      * an identity a pre-UTF-8-fix build computed for this project (legacy_identities — the garbled
        code-page decode) -> True, so a graph stamped before the fix is still its own (the caller re-stamps
        it with the current identity) — unless a REAL directory bears that garbled name: then it is a
        project of its own and its stamp is never taken as ours (E1's rule);
      * a garbled stamp INSIDE that garbled identity (ROUND-5 G3) is mapped back to the path it came from
        (_ungarble: re-encoded in that code page, decoded as UTF-8) and judged by the SAME rule as a current
        stamp (_same_project_path: one identity, or a vanished path judged at its nearest existing
        ancestor). Raw containment made a parent repo take a NESTED repo's pre-fix stamp as its own;
      * an owner that is NOT an absolute path on THIS OS (_abs_on_this_os: a Windows 'C:/...' stamp read on
        Linux/macOS, a drive-less or relative stamp on Windows) -> False, a different project (ROUND-5 G3):
        it was resolved against the working directory and, run from the project root, passed for "a
        narrower stamp whose directory is gone".
    Never raises (-> False)."""
    try:
        if not owner or not _abs_on_this_os(owner):
            return False
        res = _toplevel_raw(root, timeout)
        ident = _decode_identity(root, res)
        if _same_project_path(ident, owner, timeout=timeout):
            return True
        o = None
        for li, enc, from_error in _legacy_candidates_enc(root, res, ident)[0]:
            if from_error or _path_exists(li):
                continue   # the old decode-error fallback is a real path (judged above); a real dir by the
                #            garbled name is a project of its own
            if o is None:
                o = _norm_path(Path(owner).resolve())
            lg = _norm_path(Path(li).resolve())
            if o == lg:
                return True                                # this project's own pre-fix stamp
            if not o.startswith(lg + "/"):
                continue
            real = _ungarble(owner, enc)                   # the path the garbled stamp really named
            if real and _abs_on_this_os(real) and _same_project_path(ident, real, timeout=timeout):
                return True
        return False
    except Exception:
        return False


# ---- dedicated OS write lock (mirror sync._os_lock; bounded-blocking, not fire-and-skip) ----
def _os_lock(handle) -> bool:
    """Take a non-blocking exclusive OS lock on an open file handle. True iff acquired."""
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _os_unlock(handle) -> None:
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


def _acquire_lock(env=None, wait_s: float | None = None):
    """Bounded-BLOCKING acquire of the dedicated registry lock. Returns an OPEN handle holding the OS
    lock, or None on timeout (a best-effort writer then skips; an allocation FAILS — see
    _allocate_pair). Unlike the sync lock's fire-and-skip, it retries for a budget so a concurrent
    setup is serialised rather than dropped. `wait_s` overrides the default _LOCK_WAIT_S budget."""
    try:
        path = _lock_path(env)
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(path, "a+")
    except OSError:
        return None
    deadline = time.monotonic() + (wait_s if wait_s is not None else _LOCK_WAIT_S)
    while True:
        if _os_lock(handle):
            return handle
        if time.monotonic() >= deadline:
            try:
                handle.close()
            except OSError:
                pass
            return None
        time.sleep(_LOCK_SLEEP_S)


def _release_lock(handle) -> None:
    if handle is None:
        return
    _os_unlock(handle)
    try:
        handle.close()
    except OSError:
        pass


# ---- free-port probe (delegates to provision; patchable module-global) ------
def _port_in_use(host: str, port: int) -> bool:
    """True iff something is already listening on host:port. Delegates to provision._port_in_use
    (lazy import avoids a circular dependency); falls back to a local connect-probe. Never raises.
    A module-global so tests can patch port_registry._port_in_use deterministically (no real socket)."""
    try:
        import provision  # lazy: provision imports port_registry at call time, so this can't cycle
        return bool(provision._port_in_use(host, port))
    except Exception:
        pass
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            return s.connect_ex((host, port)) == 0
    except OSError:
        return False


# ---- registry I/O (version-gated; never raises) -----------------------------
def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _age_seconds(iso) -> float | None:
    """Seconds since the ISO-8601 UTC timestamp `iso` (as written by _now_iso), or None if it's
    missing/unparseable. Powers the touch throttle + the idle-hibernation window. Never raises."""
    try:
        if not iso:
            return None
        dt = datetime.strptime(str(iso), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds()
    except Exception:
        return None


def _read_registry(env=None):
    """(status, registry) where status is "ok" (registry is the parsed dict), "absent" (no file),
    "unreadable" (the file exists but a read kept failing) or "unrecognized" (garbage / unknown
    version / bad shape). A read that fails with an OSError other than not-found is retried for
    _IO_RETRY_S: on Windows that is the sharing violation of a concurrent _write_registry os.replace,
    and treating it as "no data" is what silently routed a project to bolt 7687. Never raises."""
    try:
        path = registry_path(env)
    except Exception:
        return ("unreadable", None)
    deadline = time.monotonic() + _IO_RETRY_S
    while True:
        try:
            raw = path.read_text(encoding="utf-8")
            break
        except FileNotFoundError:
            return ("absent", None)
        except OSError:
            if time.monotonic() >= deadline:
                return ("unreadable", None)
            time.sleep(_IO_SLEEP_S)
        except Exception:
            return ("unrecognized", None)   # e.g. undecodable bytes
    try:
        data = json.loads(raw)
    except Exception:
        return ("unrecognized", None)
    if not isinstance(data, dict) or data.get("version") != REGISTRY_VERSION \
            or not isinstance(data.get("projects"), dict):
        return ("unrecognized", None)
    return ("ok", data)


def registry_status(env=None) -> str:
    """"ok" | "absent" | "unreadable" | "unrecognized" — see _read_registry. Lets a caller tell "no
    project was ever allocated here" (absent) from "the map exists but can't be used" (the last two). A
    caller must never ROUTE a project to a base port on either of those — and note that the allocators
    still RETURN the base pair, WITHOUT writing it, for "unrecognized" (the unknown-schema contract, see
    _allocate_pair; ISS-build:027), so check this before trusting an allocation. Never raises."""
    return _read_registry(env)[0]


def load_registry(env=None):
    """The parsed registry, or None on absent / unreadable / garbage / unknown version / bad shape
    (fail-safe: treated as no data; a transient read error is retried first). Never raises. Callers
    must NOT overwrite the file when this returns None on a PRESENT-but-unknown file — that would drop
    a possibly-newer schema's allocations. Use registry_status() to tell the None cases apart."""
    status, data = _read_registry(env)
    return data if status == "ok" else None


def _registry_sig(env=None):
    """(mtime_ns, size) of the registry file, or None — tells whether it was rewritten meanwhile."""
    try:
        st = registry_path(env).stat()
        return (st.st_mtime_ns, st.st_size)
    except Exception:
        return None


def _registry_present(env=None) -> bool:
    try:
        return registry_path(env).exists()
    except Exception:
        return False


def _pair_from_entry(entry, keys):
    """(a, b) for a given port pool `keys` (e.g. ("search_port","embed_port") or
    ("bolt_port","http_port")) from a registry entry, or None if that pool is absent/malformed."""
    try:
        a, b = int(entry[keys[0]]), int(entry[keys[1]])
        if a > 0 and b > 0:
            return (a, b)
    except (KeyError, TypeError, ValueError):
        pass
    return None


def _pair_of(entry):
    """(search, embed) from a registry entry, or None if malformed (the MCP pool)."""
    return _pair_from_entry(entry, ("search_port", "embed_port"))


def _lookup(root, keys, env=None):
    """The project's stored pair for the given pool, or None. No port probe (the key lookup may migrate a
    pre-fix entry — see project_key). Never raises."""
    try:
        key = project_key(root, env=env)
        reg = load_registry(env)
        if not reg:
            return None
        k = _entry_key(reg["projects"], key)
        ent = reg["projects"].get(k) if k else None
        return _pair_from_entry(ent, keys) if isinstance(ent, dict) else None
    except Exception:
        return None


def lookup_ports(root, env=None):
    """The project's stored (search, embed) MCP pair, or None if unallocated (status/doctor + heal)."""
    return _lookup(root, ("search_port", "embed_port"), env)


def lookup_neo4j_ports(root, env=None):
    """The project's stored (bolt, http) Neo4j pair, or None if unallocated. Read-only; never raises."""
    return _lookup(root, ("bolt_port", "http_port"), env)


def _claimed(reg, skip_key, keys):
    """The set of ports in the given pool claimed by OTHER projects (excluding skip_key, or the entry
    that lists it as an alias)."""
    claimed: set = set()
    skip = _entry_key(reg.get("projects", {}), skip_key) or skip_key
    for key, ent in reg.get("projects", {}).items():
        if key == skip or not isinstance(ent, dict):
            continue
        pair = _pair_from_entry(ent, keys)
        if pair:
            claimed.update(pair)
    return claimed


def _claimed_ports(reg, skip_key):
    """The set of MCP ports claimed by OTHER projects (excluding skip_key)."""
    return _claimed(reg, skip_key, ("search_port", "embed_port"))


def _merge_entry(reg, key, root, fields) -> None:
    """Merge `fields` into project `key`'s entry, PRESERVING every OTHER pool's ports (so the MCP
    writer never drops a bolt allocation and vice-versa) and refreshing the canonical `root`. This is
    the load-bearing change vs. the original whole-entry replace, which dropped the sibling pool."""
    ent = reg["projects"].get(key)
    ent = dict(ent) if isinstance(ent, dict) else {}
    ent["root"] = project_identity(root).replace("\\", "/")
    ent.update(fields)
    reg["projects"][key] = ent


# ---- last_used touch (the idle-hibernation signal) + entry removal (instance-rm GC) --------------
_TOUCH_THROTTLE_S = 60.0   # skip a re-touch within this window so repeated searches don't hammer the lock


def touch_last_used(root, env=None) -> None:
    """Stamp project `root`'s registry entry with a fresh `last_used` — the idle-hibernation signal,
    bumped on every USE of a project's Neo4j (sync + search). Sits on the hot path, so:
      * NEVER creates a registry or an entry — a fresh ~/.deepmap stays clean; only an EXISTING entry
        is touched (an unknown/unregistered project is a no-op).
      * THROTTLED — a re-touch within _TOUCH_THROTTLE_S returns without locking/writing.
      * lock-guarded + atomic; mutates the entry in place so BOTH port pools survive.
    Never raises."""
    try:
        if not _registry_present(env):
            return
        key = project_key(root, env=env)   # first: it may re-key a pre-fix entry (E1)
        reg = load_registry(env)
        if reg is None:
            return
        key = _entry_key(reg["projects"], key)
        ent = reg["projects"].get(key) if key else None
        if not isinstance(ent, dict):
            return
        age = _age_seconds(ent.get("last_used"))
        if age is not None and age < _TOUCH_THROTTLE_S:
            return
        handle = _acquire_lock(env)
        if handle is None:
            return
        try:
            reg = load_registry(env)
            if reg is None or key not in reg.get("projects", {}):
                return
            reg["projects"][key]["last_used"] = _now_iso()
            reg["updated_utc"] = _now_iso()
            _write_registry(reg, env)
        finally:
            _release_lock(handle)
    except Exception:
        pass


def _key_for_bolt(reg, bolt_port, prefer_key=None) -> str | None:
    """The project key whose Neo4j bolt port == `bolt_port`, or None. When SEVERAL entries claim it (a
    <= v0.27.4 mis-adopted base claim next to the real owner's, until the claimant's setup releases it —
    ROUND-5 G4), the entry `prefer_key` names (alias-aware) wins, else the first. Never raises."""
    try:
        bp = int(bolt_port)
        projects = reg.get("projects", {})
        matches = []
        for key, ent in projects.items():
            if not isinstance(ent, dict):
                continue
            pair = _pair_from_entry(ent, ("bolt_port", "http_port"))
            if pair and pair[0] == bp:
                matches.append(key)
        if not matches:
            return None
        if len(matches) > 1 and prefer_key:
            k = _entry_key(projects, str(prefer_key))
            if k in matches:
                return k
        return matches[0]
    except Exception:
        pass
    return None


def touch_last_used_by_bolt(bolt_port, env=None) -> None:
    """touch_last_used keyed by a project's Neo4j BOLT port instead of its root — for the MCP search
    server, whose cwd is the plugin repo (not the user project), so it identifies its project only from
    the injected NEO4J_URI's bolt port. When several entries claim that port, the server's own project
    (DEEPMAP_PROJECT_KEY, which provision injects into every server it starts) is the one touched. No-op on
    an unknown port / absent registry. Never raises."""
    try:
        if not _registry_present(env):
            return
        reg = load_registry(env)
        if reg is None:
            return
        try:
            prefer = (env if env is not None else os.environ).get("DEEPMAP_PROJECT_KEY")
        except Exception:
            prefer = None
        key = _key_for_bolt(reg, bolt_port, prefer)
        if key is None:
            return
        ent = reg["projects"].get(key)
        if not isinstance(ent, dict):
            return
        age = _age_seconds(ent.get("last_used"))
        if age is not None and age < _TOUCH_THROTTLE_S:
            return
        handle = _acquire_lock(env)
        if handle is None:
            return
        try:
            reg = load_registry(env)
            if reg is None:
                return
            key = _key_for_bolt(reg, bolt_port, prefer)   # re-resolve under lock
            if key is None or key not in reg.get("projects", {}):
                return
            reg["projects"][key]["last_used"] = _now_iso()
            reg["updated_utc"] = _now_iso()
            _write_registry(reg, env)
        finally:
            _release_lock(handle)
    except Exception:
        pass


def neo4j_pair_for_key(key, env=None):
    """(bolt, http) for a project KEY (not a root), or None — for the instance-rm base-store guard.
    Never raises."""
    try:
        reg = load_registry(env)
        if not reg:
            return None
        k = _entry_key(reg["projects"], key)
        return _pair_from_entry(reg["projects"].get(k), ("bolt_port", "http_port")) if k else None
    except Exception:
        return None


def remove_entry(root=None, *, key=None, drop_neo4j_only=True, env=None) -> bool:
    """Remove a project's Neo4j allocation from the registry (the instance-rm GC path).

    drop_neo4j_only=True (default): pop just the Neo4j pool (bolt_port/http_port/neo4j_allocated_utc),
    KEEPING the MCP pool + the project's .mcp.json wiring (low drift). False: pop the whole entry.
    Resolves `key` from `root` when not given. NEVER creates a registry. Returns True iff it changed
    something. Lock-guarded + atomic. Never raises."""
    try:
        k = key or (project_key(root, env=env) if root is not None else None)
        if not k or not _registry_present(env):
            return False
        handle = _acquire_lock(env)
        if handle is None:
            return False
        try:
            reg = load_registry(env)
            k = _entry_key((reg or {}).get("projects"), k) if reg is not None else None
            if reg is None or k is None or k not in reg.get("projects", {}):
                return False
            if drop_neo4j_only:
                ent = reg["projects"][k]
                changed = False
                for f in ("bolt_port", "http_port", "neo4j_allocated_utc"):
                    if isinstance(ent, dict) and f in ent:
                        del ent[f]
                        changed = True
                if not changed:
                    return False
            else:
                del reg["projects"][k]
            reg["updated_utc"] = _now_iso()
            return _write_registry(reg, env)   # True only if the removal actually landed
        finally:
            _release_lock(handle)
    except Exception:
        return False


def _prefer_pair(prefer, claimed):
    """`prefer` as an (a, b) int pair when neither port is claimed by another project, else None (the scan
    decides). Never raises."""
    if not prefer:
        return None
    try:
        pa, pb = int(prefer[0]), int(prefer[1])
    except (TypeError, ValueError, IndexError):
        return None
    return (pa, pb) if pa not in claimed and pb not in claimed else None


def _base_open(root, base, claimed, env=None) -> bool:
    """May the Neo4j scan consult base_adopt_ok for the base pair, given the ports other projects claim?
    Yes when neither base port is claimed, or when that claim is VOID for this project (ROUND-5 G4:
    _base_claim_void — the base store's owner record names this project). Never raises (-> False)."""
    try:
        if base[0] not in claimed and base[1] not in claimed:
            return True
        return _base_claim_void(root, env=env)
    except Exception:
        return False


_NOT_ASKED = object()   # _base_verdict_unlocked: the scan would not consult base_adopt_ok (yet)


def _ask_base_adopt_ok(base_adopt_ok) -> bool:
    try:
        return bool(base_adopt_ok())
    except Exception:
        return False


def _base_verdict_unlocked(root, key0, *, bases, keys, prefer, base_adopt_ok, env=None):
    """ROUND-6 (W2 H2/H3 support): base_adopt_ok's answer, asked BEFORE the registry lock is taken — or
    _NOT_ASKED when, on an unlocked read of the registry, the locked scan would not consult it (the project
    already has a pair in this pool, an adoptable `prefer` decides, or another project holds a claim on the
    base that is not void for this one) or the registry can't be read.

    base_adopt_ok is provision._base_adopt_ok: Neo4j I/O — a bolt probe of the base plus a live :_DeepMapMeta
    owner read (seconds; setup's H2/H3b owner settling runs just before the allocator, and any heavier verdict
    would land here too). Asked under the lock, it stalled every other project's allocation for the whole call
    (one fails after _ALLOC_LOCK_WAIT_S) and made any registry writer it reached (a last_used touch) wait on its
    own process's lock. The verdict is about the base STORE, not about the registry, so asking it first is
    sound: the claimed set is re-read under the lock, and a base another project claimed meanwhile is still
    skipped without using it. Never raises."""
    try:
        status, reg = _read_registry(env)
        if status == "absent":
            reg = {"projects": {}}
        elif status != "ok":
            return _NOT_ASKED
        projects = reg.get("projects") or {}
        key = _entry_key(projects, key0) or key0
        ent = projects.get(key)
        if isinstance(ent, dict) and _pair_from_entry(ent, keys):
            return _NOT_ASKED
        claimed = _claimed(reg, key, keys)
        if _prefer_pair(prefer, claimed) is not None or not _base_open(root, bases, claimed, env):
            return _NOT_ASKED
        return _ask_base_adopt_ok(base_adopt_ok)
    except Exception:
        return _NOT_ASKED


def _allocate_pair(root, *, bases, keys, stamp, base_adopt_ok=None, prefer=None, host=None, env=None):
    """Idempotent per-project allocation of a distinct free pair for ONE pool (MCP or Neo4j).

    Shared core behind allocate_ports/allocate_neo4j_ports:
      1. Already allocated for THIS pool -> return it WITHOUT probing (the migrate-itself footgun).
      2. Present-but-garbage registry -> return the base pair WITHOUT writing (the documented
         unknown-schema contract).
      3. Under the dedicated lock (waited for up to _ALLOC_LOCK_WAIT_S), re-read, pick the first
         candidate where NEITHER port is claimed-by-another AND NEITHER is in use; MERGE it into the
         entry (never clobbering the other pool); write atomically.
      4. Lock timeout / an unreadable registry / exhaustion / a write that never lands -> None, no
         write. It used to return the BASE pair here, which on a multi-project machine is the FIRST
         project's: a setup that lost the lock race was wired to that project's servers and graph.
         None lets the caller fail loud instead. Never raises.

    Pool hooks (both default to the MCP behaviour, so allocate_ports is byte-identical to the original):
      * `prefer`: an (a,b) pair to adopt WITHOUT an in-use probe when it is not claimed-by-another
        (used to re-adopt an existing project's own Neo4j instance / recover a lost registry entry).
      * `base_adopt_ok`: a 0-arg callable consulted ONLY at the base candidate (i==0) when the base is
        unclaimed-by-another; True -> adopt the base WITHOUT an in-use probe (adopt our own live
        instance), False -> SKIP the base and scan from i>=1. None -> the base is probed like any other
        candidate (the MCP pool's exact original behaviour). It is asked at most once, and — ROUND-6 —
        BEFORE the registry lock whenever an unlocked read shows the scan will need it
        (_base_verdict_unlocked: it does Neo4j I/O — a live owner read of the base); the claimed set is
        re-read under the lock, so a base claimed meanwhile is still skipped. Only a base that became
        consultable while this call waited for the lock is asked under it (as before).
    """
    base_a, base_b = bases
    host = host or os.environ.get("DEEPMAP_MCP_HOST", "127.0.0.1")
    base = (base_a, base_b)
    try:
        existing = _lookup(root, keys, env)
        if existing:
            return existing

        # A PRESENT but unknown/garbage registry: don't allocate-and-clobber it. Fall back to base.
        status = registry_status(env)
        if status == "unrecognized":
            return base
        if status == "unreadable":
            return None   # other projects' claims are invisible -> never guess a pair

        # The key BEFORE the lock: resolving it may migrate a pre-fix entry, which takes the lock itself.
        key0 = project_key(root, env=env)
        # base_adopt_ok's Neo4j I/O never runs under the registry lock (ROUND-6; see _base_verdict_unlocked).
        verdict = (_base_verdict_unlocked(root, key0, bases=base, keys=keys, prefer=prefer,
                                          base_adopt_ok=base_adopt_ok, env=env)
                   if base_adopt_ok is not None else _NOT_ASKED)
        handle = _acquire_lock(env, wait_s=_ALLOC_LOCK_WAIT_S)
        if handle is None:
            return None   # couldn't serialise -> fail; the base pair may be another project's
        try:
            status, reg = _read_registry(env)
            if status == "unrecognized":
                return base   # present-but-unknown appeared under the lock -> don't clobber
            if status == "unreadable":
                return None
            if reg is None:
                reg = {"version": REGISTRY_VERSION, "kind": REGISTRY_KIND, "projects": {}}
            key = _entry_key(reg["projects"], key0) or key0
            ent = reg["projects"].get(key)
            existing = _pair_from_entry(ent, keys) if isinstance(ent, dict) else None
            if existing:
                return existing   # someone allocated it between our lookup and the lock
            claimed = _claimed(reg, key, keys)
            chosen = _prefer_pair(prefer, claimed)   # re-adopt our own / recovered ports; no probe
            if chosen is None:
                # ROUND-5 G4: another project's claim on the base is VOID for the project the base store's
                # owner record names (a <= v0.27.4 mis-adoption of that project's store) — so its setup asks
                # base_adopt_ok (which reads the live owner / the record) instead of skipping the base and
                # abandoning its own store. The MCP pool (base_adopt_ok None) never looks at the record.
                void_base = (base_adopt_ok is not None and (base_a in claimed or base_b in claimed)
                             and _base_claim_void(root, env=env))
                for i in range(MAX_PAIRS):
                    a = base_a + i * STRIDE
                    b = base_b + i * STRIDE
                    if (a in claimed or b in claimed) and not (i == 0 and void_base):
                        continue
                    if i == 0 and base_adopt_ok is not None:
                        # the answer asked before the lock (ROUND-6); asked here only when the base became
                        # consultable while this call waited for the lock (a claim released meanwhile)
                        ok = verdict if verdict is not _NOT_ASKED else _ask_base_adopt_ok(base_adopt_ok)
                        if ok:
                            chosen = (a, b)
                            break
                        continue   # owner-aware: skip the base, scan from i>=1
                    if _port_in_use(host, a) or _port_in_use(host, b):
                        continue
                    chosen = (a, b)
                    break
            if chosen is None:
                return None   # exhausted the scan window -> fail, never a taken/foreign base pair
            _merge_entry(reg, key, root, {keys[0]: chosen[0], keys[1]: chosen[1], stamp: _now_iso()})
            reg["updated_utc"] = _now_iso()
            reg["note"] = _REGISTRY_NOTE
            if not _write_registry(reg, env):
                return None   # unrecorded -> the next allocation could hand the same pair out again
            return chosen
        finally:
            _release_lock(handle)
    except Exception:
        return None


def allocate_ports(root, host=None, env=None, prefer=None):
    """Idempotent per-project allocation of a distinct free (search, embed) MCP pair, or None when no
    pair could be safely allocated (see _allocate_pair) — the caller must then fail loud, never fall
    back to 8787/8788. On a clean machine the first project gets exactly (8787, 8788). EXCEPTION
    (ISS-build:027): a present but UNRECOGNIZED registry returns the base (8787, 8788) WITHOUT recording
    it (the unknown-schema contract) — confirm a returned pair with lookup_ports(root) (None there) or
    check registry_status() before using it.
    `prefer` (default None = the unchanged scan) is the MCP twin of the Neo4j pool's instance-conf
    recovery: the pair the project's own .mcp.json already declares, re-adopted WITHOUT an in-use probe
    when no other project claims it. The CALLER (provision) vouches that each port is free or held by
    this project's own server; an existing registry entry still wins."""
    return _allocate_pair(root, bases=(BASE_SEARCH, BASE_EMBED),
                          keys=("search_port", "embed_port"), stamp="allocated_utc",
                          prefer=prefer, host=host, env=env)


def allocate_neo4j_ports(root, *, base_adopt_ok=None, prefer=None, host=None, env=None):
    """Idempotent per-project allocation of a distinct (bolt, http) Neo4j pair for a per-project
    instance. MERGES into the project's existing entry (never drops the MCP pair). The first project
    keeps (7687, 7474) — zero drift — subject to the owner-aware `base_adopt_ok` verdict so a second
    project can't seize a live instance owned by another. Never raises; returns None when no pair
    could be safely allocated (lock timeout, exhaustion, an unreadable or unwritable registry) — and,
    like allocate_ports, the base (7687, 7474) UNRECORDED for an UNRECOGNIZED registry (ISS-build:027):
    confirm it with lookup_neo4j_ports(root) (None there) or check registry_status() before using it."""
    return _allocate_pair(root, bases=(BASE_BOLT, BASE_HTTP),
                          keys=("bolt_port", "http_port"), stamp="neo4j_allocated_utc",
                          base_adopt_ok=base_adopt_ok, prefer=prefer, host=host, env=env)


# ---- THE Neo4j routing rule (D1) — one resolver for CLI, sync, indexes, provision and the hooks -----
ROUTE_SOURCES = ("registry", "env", "instance-conf", "legacy-unverified", "none")
_BOLT_CONF_RE = r"^\s*server\.bolt\.listen_address\s*=\s*[^:\n]*:(\d+)"
_HTTP_CONF_RE = r"^\s*server\.http\.listen_address\s*=\s*[^:\n]*:(\d+)"


class Neo4jRoute:
    """Where THIS project's graph lives — the answer of resolve_neo4j_route.

    Unpacks as the 3-tuple ``uri, source, problem`` and also carries ``bolt``, ``http`` (ints or
    None), ``key`` (the project key) and ``kind`` (why there is a problem: "registry" for an
    unreadable / unrecognised registry, "no-instance" for a base 7687 owned by another project,
    "uri" for an operator NEO4J_URI that is not a Bolt URI DeepMap dials (neo4j_uri_problem),
    "error" for anything unexpected; None when routed).

    ``source``:
      * "registry"          — the project's own registered (bolt, http) pair. Use it.
      * "env"               — an explicit NEO4J_URI (the operator escape hatch). Use it verbatim.
      * "instance-conf"     — no registry pair, but the project's OWN per-project instance still exists
                              (<home>/instances/<key>/conf/neo4j.conf) and nobody else claims its ports
                              (a lost registry entry). Use it — it is this project's instance.
      * "legacy-unverified" — no pair and no own instance: the base bolt 7687 as a LEGACY route. It may
                              hold another project's graph, so the caller MUST verify the graph's
                              :_DeepMapMeta owner (legacy_owner_verdict / sync.verify_legacy_owner)
                              before reading or writing it.
      * "none"              — no route; ``uri`` is None and ``problem`` is the user-facing reason
                              (it names the fix). Fail loud — never fall back to 7687.
    """
    __slots__ = ("uri", "source", "problem", "bolt", "http", "key", "kind")

    def __init__(self, uri, source, problem=None, *, bolt=None, http=None, key=None, kind=None):
        self.uri = uri
        self.source = source
        self.problem = problem
        self.bolt = bolt
        self.http = http
        self.key = key
        self.kind = kind

    def _t(self):
        return (self.uri, self.source, self.problem)

    def __iter__(self):
        return iter(self._t())

    def __len__(self):
        return 3

    def __getitem__(self, i):
        return self._t()[i]

    def __eq__(self, other):
        if isinstance(other, Neo4jRoute):
            return (self._t(), self.bolt, self.http, self.key, self.kind) == \
                (other._t(), other.bolt, other.http, other.key, other.kind)
        return isinstance(other, tuple) and self._t() == other

    def __hash__(self):
        return hash((self._t(), self.bolt, self.http, self.key, self.kind))

    def __repr__(self):
        return (f"Neo4jRoute(uri={self.uri!r}, source={self.source!r}, problem={self.problem!r}, "
                f"bolt={self.bolt!r}, http={self.http!r}, key={self.key!r}, kind={self.kind!r})")

    @property
    def ok(self) -> bool:
        """True iff there is a URI to use (it may still need the owner check — see needs_owner_check)."""
        return self.problem is None and bool(self.uri)

    @property
    def pair(self):
        """(bolt, http) when both are known, else None."""
        return (self.bolt, self.http) if self.bolt and self.http else None

    @property
    def needs_owner_check(self) -> bool:
        """True iff the caller must verify the graph's owner before using it (the legacy 7687 route)."""
        return self.source == "legacy-unverified"


Route = Neo4jRoute   # short alias (the name ROUND2 D1 used)


def instance_conf_path(key, env=None) -> Path:
    """<home>/instances/<key>/conf/neo4j.conf — where provision puts a per-project instance's conf (the
    same path as provision.paths(project_key=key)["conf"], which is version-independent)."""
    return home_dir(env) / "instances" / str(key) / "conf" / "neo4j.conf"


def instance_conf_ports(key, env=None):
    """The (bolt, http) an existing per-project instance's own neo4j.conf declares, or None (no conf /
    no listen addresses). Lets a project whose registry entry was lost re-find its own instance
    instead of drifting to 7687. Stdlib-only mirror of provision's parser. Never raises."""
    import re
    try:
        conf = instance_conf_path(key, env)
        if not conf.is_file():
            return None
        txt = conf.read_text(encoding="utf-8", errors="replace")
        bolt = re.search(_BOLT_CONF_RE, txt, re.M)
        http = re.search(_HTTP_CONF_RE, txt, re.M)
        if bolt and http:
            b, h = int(bolt.group(1)), int(http.group(1))
            if b > 0 and h > 0:
                return (b, h)
    except Exception:
        pass
    return None


def neo4j_claims(skip_key=None, env=None, reg=None) -> dict:
    """{port: owner} for every Neo4j port (bolt AND http) a registered project OTHER than `skip_key`
    claims; the owner is that entry's recorded root (else its key). Reads the registry unless `reg`
    is given. {} for an absent / unusable registry. Never raises."""
    out: dict = {}
    try:
        if reg is None:
            reg = load_registry(env)
        projects = (reg or {}).get("projects") or {}
        skip = _entry_key(projects, skip_key) or skip_key
        for k, ent in projects.items():
            if k == skip or not isinstance(ent, dict):
                continue
            pair = _pair_from_entry(ent, ("bolt_port", "http_port"))
            if pair:
                out[pair[0]] = out[pair[1]] = ent.get("root") or k
    except Exception:
        pass
    return out


def _registry_problem(status, env=None) -> str:
    return (f"the DeepMap port registry {registry_path(env)} exists but is {status}, so this "
            f"project's own Neo4j instance can't be identified (refusing to guess "
            f"{bolt_uri(BASE_BOLT)}, which may be another project's). If it persists, move "
            f"the file aside and re-run /deep-map:setup — instances are re-adopted from their conf.")


def no_instance_problem(owner=None) -> str:
    """The one user-facing line for "this project has no DeepMap instance of its own" (D1/D6: it names
    a fix that works in that state — /deep-map:setup, never instance-rm on the base store)."""
    held = (f" — bolt {BASE_BOLT} is project {owner}'s instance, not this one's" if owner else "")
    return (f"this project has no DeepMap Neo4j instance of its own{held} (never set up here, or "
            f"removed by instance-rm) — run /deep-map:setup in this project to give it one")


# ---- THE NEO4J_URI scheme rule (2026-10-24) ------------------------------------------------------------------------
# DeepMap talks to Neo4j over Bolt only, and DeepMap — not the installed driver — decides it. The neo4j driver 6.4
# (inside requirements.txt's neo4j>=6.0.1,<7, what CI resolves) reads http:// and https:// as a PREVIEW HTTP Query API
# driver that needs aiohttp: an operator NEO4J_URI mistyped as the Neo4j Browser's address (http://127.0.0.1:7474)
# raised ModuleNotFoundError('aiohttp') at driver construction (6.2 raised ConfigurationError) — a missing-package
# remedy for a typo — and with aiohttp installed it would silently have run an HTTP driver DeepMap was never
# validated on. neo4j_uri_problem is the ONE rule; resolve_neo4j_route applies it to the operator's NEO4J_URI (so the
# CLI, sync, indexes, the hooks and auto-sync all see a route PROBLEM in its words, before any driver exists) and
# provision's escape hatch calls it too. Accepted: exactly BOLT_URI_SCHEMES, in any case (the driver lower-cases the
# scheme); no whitespace or control character anywhere (the driver strips surrounding spaces but a port with a
# trailing one fails only when it connects, and differently per Python version); a port, when given, of 0-65535
# ASCII digits (0 is provision's fail-closed "no route" sentinel, refused on connect). A user name inside the URI is
# left to the driver's own ConfigurationError (worded masked by the CLI — F1), and the port is then not judged here
# (where the userinfo ends is ambiguous; a password must never be quoted as a "port").
_URI_FIX_TAIL = "or unset NEO4J_URI and run /deep-map:setup to give this project its own instance"
_URI_SCHEMES_NOTE = "bolt:// or neo4j:// in the clear; bolt+s:// or neo4j+s:// for TLS, as Aura uses"
_URI_ECHO_MAX = 160                                  # characters of the operator's value a problem quotes
_URI_HOST_MAX = 100                                  # a longer host is suggested as <host>
_URI_SCHEME_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*")
_URI_HOSTPORT = re.compile(r"(\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9._\-]+)(?::(\d{1,5}))?")
# the HTTP ports a stock Neo4j pairs with its Bolt 7687 (http 7474, https 7473); a registered DeepMap instance's own
# http port maps to ITS bolt port (_bolt_port_for_http)
_STOCK_HTTP_TO_BOLT = {7474: BASE_BOLT, 7473: BASE_BOLT}


def _uri_echo(value) -> str:
    """`value` as a NEO4J_URI problem quotes it: everything up to its LAST '@' after the scheme's '//' (or from the
    start, when there is no '//' before that '@') masked — a user name, password or token is never printed (masking
    the user name too is the safe side) — capped at _URI_ECHO_MAX characters, and quoted by repr() so a space, a tab
    or a line break is visible. Never raises."""
    try:
        s = str(value)
    except Exception:
        return "<unprintable>"
    try:
        at = s.rfind("@")
        if at >= 0:
            sep = s.find("://")
            start = sep + 3 if 0 <= sep < at else 0
            s = s[:start] + "***" + s[at:]
        if len(s) > _URI_ECHO_MAX:
            s = s[:_URI_ECHO_MAX] + "..."
        return repr(s)
    except Exception:
        return "<unprintable>"


def _uri_host_port(rest: str):
    """(host | None, port text | None) of the authority in `rest` — the part of a URI after 'scheme://': only what
    follows its last '@' (never userinfo), cut at the first '/', '?' or '#'. A bracketed IPv6 host keeps its brackets;
    an unbracketed one with several ':' has no port. A host longer than _URI_HOST_MAX -> None. Never raises."""
    try:
        auth = re.split(r"[/?#]", rest.rsplit("@", 1)[-1], 1)[0]
        if auth.startswith("["):
            close = auth.find("]")
            if close < 0:
                return None, None
            host, tail = auth[:close + 1], auth[close + 1:]
            port = tail[1:] if tail.startswith(":") else None
        elif auth.count(":") == 1:
            host, port = auth.split(":", 1)
        else:
            host, port = auth, None
        if not host or len(host) > _URI_HOST_MAX or any(c.isspace() for c in host):
            host = None
        return host, port
    except Exception:
        return None, None


def _port_number(text):
    """int(text) when `text` is 1-5 ASCII digits naming 0-65535, else None. Never raises."""
    try:
        if text and text.isascii() and text.isdigit() and len(text) <= 5 and int(text) <= 65535:
            return int(text)
    except Exception:
        pass
    return None


def _bolt_port_for_http(host, http_port, env=None):
    """The Bolt port that pairs with HTTP port `http_port` on `host`: on THIS machine, a registered DeepMap instance's
    own bolt port (its registry pair); else a stock Neo4j's (7474 / 7473 -> 7687). None when unknown. Never raises."""
    try:
        if http_port is None:
            return None
        if is_loopback_host(host):
            reg = load_registry(env) or {}
            for ent in (reg.get("projects") or {}).values():
                pair = _pair_from_entry(ent, ("bolt_port", "http_port")) if isinstance(ent, dict) else None
                if pair and pair[1] == http_port:
                    return pair[0]
        return _STOCK_HTTP_TO_BOLT.get(http_port)
    except Exception:
        return None


def _bolt_suggestion(scheme: str, host, port) -> str:
    """'<scheme>://<host>[:<port>]' with '<host>' standing in for an unknown host. Never raises."""
    return f"{scheme}://{host or '<host>'}" + (f":{port}" if port is not None else "")


def _has_space_or_control(s: str) -> bool:
    return any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in s)


def neo4j_uri_problem(uri, env=None) -> str | None:
    """THE NEO4J_URI scheme rule: None when DeepMap may dial `uri` — a Bolt URI (BOLT_URI_SCHEMES, any case) with no
    whitespace or control character and, when it names one (and holds no user name), a port of 0-65535 — or when
    `uri` is unset (None) or empty (the routing rule's "unset": this project's own instance is used). Otherwise the
    one-line, user-facing reason, which quotes the value (masked: _uri_echo), says DeepMap talks Bolt, gives a
    copy-pasteable fix (the Neo4j Browser's http://<host>:7474 -> bolt://<host>:7687; a registered DeepMap instance's
    http port -> its own bolt port; https://<host> -> neo4j+s://<host>, Aura's form) and names /deep-map:setup.
    `env` is only read for the registry (DEEPMAP_HOME) behind the port mapping. Never raises (a value that can't be
    read as text is a problem)."""
    try:
        if uri is None:
            return None
        s = str(uri)                                 # a value that can't be read as text -> the except below
        if s == "":
            return None
        t = s.strip()
        if not t:
            return ("NEO4J_URI is set but blank — unset it (DeepMap then uses this project's own Neo4j instance; "
                    "/deep-map:setup gives it one) or set it to a Bolt URI such as "
                    f"bolt://127.0.0.1:{BASE_BOLT} (DeepMap talks to Neo4j over Bolt)")
        shown = _uri_echo(s)
        sep = t.find("://")
        if sep < 0:
            # no scheme:// at all — host:port, a bare host, or bolt:host (the driver reads 'localhost:7687' as the
            # scheme 'localhost', and builds a driver for 'bolt:host' that then dials no host at all)
            m = re.match(r"^([A-Za-z][A-Za-z0-9+.\-]*):/*(.*)$", t, re.S)
            body = m.group(2) if (m and m.group(1).lower() in BOLT_URI_SCHEMES) else t
            scheme = m.group(1).lower() if (m and m.group(1).lower() in BOLT_URI_SCHEMES) else "bolt"
            hp = _URI_HOSTPORT.fullmatch(body) if "@" not in t else None
            if hp and len(hp.group(1)) <= _URI_HOST_MAX:
                p = _port_number(hp.group(2)) if hp.group(2) else None
                suggest = _bolt_suggestion(scheme, hp.group(1), _bolt_port_for_http(hp.group(1), p, env) or p)
            else:
                suggest = _bolt_suggestion("bolt", None, BASE_BOLT)
            return (f"NEO4J_URI is {shown} — it has no scheme (bolt://...), and DeepMap talks to Neo4j over Bolt: set "
                    f"NEO4J_URI={suggest} ({_URI_SCHEMES_NOTE}), {_URI_FIX_TAIL}")
        scheme_text, rest = t[:sep], t[sep + 3:]
        scheme = scheme_text.lower()
        if scheme not in BOLT_URI_SCHEMES:
            host, port_text = _uri_host_port(rest)
            port = _port_number(port_text) if port_text else None
            if scheme in ("http", "https"):
                bolt = _bolt_port_for_http(host, port, env) if port is not None else None
                guessed = port is not None and bolt is None
                suggest = _bolt_suggestion("bolt" if scheme == "http" else "neo4j+s", host,
                                           BASE_BOLT if guessed else bolt)
                what = "an HTTP" if scheme == "http" else "an HTTPS"
                note = (f"{BASE_BOLT} is Neo4j's default Bolt port; " if guessed else "") + _URI_SCHEMES_NOTE
                return (f"NEO4J_URI is {shown} — that is {what} address (the Neo4j Browser's, or the HTTP Query "
                        f"API's), but DeepMap talks to Neo4j over Bolt: set NEO4J_URI={suggest} ({note}), "
                        f"{_URI_FIX_TAIL}")
            named = (f"its scheme {scheme_text!r}" if _URI_SCHEME_TOKEN.fullmatch(scheme_text)
                     else "its scheme")
            return (f"NEO4J_URI is {shown} — {named} is not one DeepMap dials: DeepMap talks to Neo4j over Bolt "
                    f"({', '.join(s_ + '://' for s_ in sorted(BOLT_URI_SCHEMES))}): set NEO4J_URI="
                    f"{_bolt_suggestion('bolt', host, port)} (neo4j+s:// for Aura), {_URI_FIX_TAIL}")
        if _has_space_or_control(s):
            clean = t if ("@" not in t and not _has_space_or_control(t)) else None
            fix = (f"set NEO4J_URI={clean}" if clean
                   else "set NEO4J_URI to the Bolt URI without them (bolt://<host>:<port>)")
            return (f"NEO4J_URI is {shown} — it holds spaces, tabs, line breaks or control characters (a trailing space "
                    "after `set NEO4J_URI=...` is a common cause), which DeepMap does not strip from a Bolt URI: "
                    f"{fix}, {_URI_FIX_TAIL}")
        if "@" not in t:
            host, port_text = _uri_host_port(rest)
            if port_text and _port_number(port_text) is None:
                return (f"NEO4J_URI is {shown} — its port is not a number from 0 to 65535; DeepMap talks to Neo4j over "
                        f"Bolt: set NEO4J_URI={_bolt_suggestion(scheme, host, BASE_BOLT)} ({BASE_BOLT} is Neo4j's "
                        f"default Bolt port), {_URI_FIX_TAIL}")
        return None
    except Exception as exc:
        return (f"NEO4J_URI could not be checked ({type(exc).__name__}) — DeepMap talks to Neo4j over Bolt: set it to "
                f"a Bolt URI such as bolt://127.0.0.1:{BASE_BOLT}, {_URI_FIX_TAIL}")


# ---- the base store's OWNER RECORD (read side; provision writes it) — ROUND-5 G4 --------------------
# provision records which project owns the base/legacy store (7687) next to the store, ONLY on proof (setup's
# sync to the base succeeded, or a live :_DeepMapMeta owner read): <home>/neo4j/neo4j-community-<v>/data/
# deepmap_base_owner.json = {"root": <project identity>, ...}. A stdlib mirror of provision._base_owner_path /
# _base_owner_root (importing provision costs ~1 s, too much for the hooks); the path, the default version
# and the parse are drift-locked against provision by tests/owner_route_round5_gates.py.
BASE_OWNER_FILE = "deepmap_base_owner.json"
_BASE_NEO4J_DEFAULT_VERSION = "5.26.32"           # == provision.DEFAULT_VERSION (drift-locked)
_NEO4J_VERSION_RE = r"^\d+\.\d+\.\d+$"            # == provision._NEO4J_VERSION_RE


# ---- which Neo4j VERSION a store runs: STICKY (the 5.26.0 -> 5.26.32 patch upgrade) ----------------------
# The default version moves with DeepMap releases, but an EXISTING store keeps the version it runs: heal and the
# hooks only revive what exists, and the base store LIVES inside its version's folder
# (<home>/neo4j/neo4j-community-<v>/data), so resolving it from the default alone would orphan it the day the
# default changed. Only provision.upgrade_neo4j (setup, or `provision.py neo4j-upgrade`) moves a store to a newer
# patch release — verified against the old version's answers, rolled back otherwise — and records the move here:
#   * base store: <home>/neo4j/deepmap-base-version.json {"version": ...} (written once an upgrade is verified);
#   * per-project instance: instances/<key>/conf/.seeded-version (the shipped conf it was seeded from — the
#     version it runs; provision re-seeds it whenever it starts the instance on another version);
#   * an upgrade in progress: its journal (<home>/neo4j/ or instances/<key>/ + UPGRADE_JOURNAL_FILE) — until the
#     new version is verified the store is the OLD version's (a crashed upgrade is recovered by the next setup).
# THE rule (store_neo4j_version), here because the hooks read the base owner record (stdlib, no provision import)
# and provision.paths() resolves every store path through it:
#   1. NEO4J_VERSION (a clean X.Y.Z): the operator's explicit pin — except that it never moves an EXISTING store to
#      an OLDER release than rules 2-5 give it (Neo4j does not support downgrades), nor points the base at a version
#      folder holding no store while another folder holds the base store (that orphaned it, and setup then created
#      a new empty one): such a pin is ignored for that store (neo4j_pin_ignored; provision warns);
#   2. an upgrade journal: its "to" once phase "verified", else its "from";
#   3. base: the recorded version (its folder still holds the store); instance: when its store exists, the stamp;
#   4. a store that exists without a record: base — the version folder holding it (5.26.0 first, which every
#      build up to 0.29.0 created, else the newest; a folder holding only the store's owner record, which lives
#      next to the store, counts after those); instance (its stamp write failed) — 5.26.0 when that binary is
#      extracted here, else the newest one that is;
#   5. no store: the default (a NEW store gets the current default).
NEO4J_PREPIN_VERSION = "5.26.0"                    # == provision.PREPIN_VERSION (drift-locked)
BASE_VERSION_FILE = "deepmap-base-version.json"     # under <home>/neo4j/
UPGRADE_JOURNAL_FILE = "deepmap-neo4j-upgrade.json"  # under <home>/neo4j/ (base) or <home>/instances/<key>/
INSTANCE_VERSION_STAMP = ".seeded-version"          # under <home>/instances/<key>/conf/


def clean_neo4j_version(v) -> str | None:
    """`v` when it is a clean X.Y.Z (it flows into a download URL and a path), else None. Never raises."""
    try:
        s = str(v).strip() if v is not None else ""
        return s if re.match(_NEO4J_VERSION_RE, s) else None
    except Exception:
        return None


def neo4j_version_tuple(v) -> tuple | None:
    """(major, minor, patch) of a clean X.Y.Z, else None. Never raises."""
    c = clean_neo4j_version(v)
    return tuple(int(x) for x in c.split(".")) if c else None


def neo4j_version_pin(env=None) -> str | None:
    """The operator's NEO4J_VERSION when it is a clean X.Y.Z (rule 1), else None. Never raises."""
    try:
        e = env if env is not None else os.environ
        return clean_neo4j_version(e.get("NEO4J_VERSION"))
    except Exception:
        return None


def neo4j_dist_home(version, env=None) -> Path:
    """<home>/neo4j/neo4j-community-<version> — the extracted binary (and, for the base layout, its store)."""
    return home_dir(env) / "neo4j" / f"neo4j-community-{version}"


def neo4j_store_present(data_dir) -> bool:
    """True iff the store under `data_dir` exists (its databases/ dir holds anything) — provision._is_initialized's
    test. Never raises."""
    try:
        db = Path(data_dir) / "databases"
        return db.is_dir() and any(db.iterdir())
    except Exception:
        return False


def base_version_record_path(env=None) -> Path:
    return home_dir(env) / "neo4j" / BASE_VERSION_FILE


def upgrade_journal_path(key=None, env=None) -> Path:
    """The journal of an upgrade of the base store (key None) or of instance `key`."""
    if key is None:
        return home_dir(env) / "neo4j" / UPGRADE_JOURNAL_FILE
    return home_dir(env) / "instances" / str(key) / UPGRADE_JOURNAL_FILE


def read_upgrade_journal(key=None, env=None) -> dict | None:
    """The upgrade journal of the base store / instance `key` when present and well-formed ("from" and "to" clean
    versions, "phase" a string), else None. Never raises."""
    try:
        rec = json.loads(upgrade_journal_path(key, env).read_text(encoding="utf-8"))
        if (isinstance(rec, dict) and clean_neo4j_version(rec.get("from")) and clean_neo4j_version(rec.get("to"))
                and isinstance(rec.get("phase"), str)):
            return rec
    except Exception:
        pass
    return None


def journal_store_version(rec: dict) -> str:
    """The version the store holds while journal `rec` is open: "to" once the upgrade was verified, else "from"."""
    return str(rec["to"] if rec.get("phase") == "verified" else rec["from"])


def read_base_version_record(env=None) -> str | None:
    """The version <home>/neo4j/deepmap-base-version.json records for the base store, or None. Never raises."""
    try:
        rec = json.loads(base_version_record_path(env).read_text(encoding="utf-8"))
        return clean_neo4j_version(rec.get("version")) if isinstance(rec, dict) else None
    except Exception:
        return None


def _base_store_folders(env=None) -> tuple:
    """([versions whose neo4j-community-<v> folder holds a store], [versions whose folder holds only the base owner
    record, which lives next to the store]). Never raises."""
    stores, records = [], []
    try:
        root = home_dir(env) / "neo4j"
        for d in (root.iterdir() if root.is_dir() else ()):
            name = d.name
            if name.startswith("neo4j-community-") and d.is_dir():
                cv = clean_neo4j_version(name[len("neo4j-community-"):])
                if cv and neo4j_store_present(d / "data"):
                    stores.append(cv)
                elif cv and (d / "data" / BASE_OWNER_FILE).is_file():
                    records.append(cv)
    except Exception:
        pass
    return stores, records


def instance_version_stamp(key, env=None) -> str | None:
    """The clean X.Y.Z instance `key`'s conf was last seeded from (instances/<key>/conf/.seeded-version), or None.
    Never raises."""
    try:
        p = home_dir(env) / "instances" / str(key) / "conf" / INSTANCE_VERSION_STAMP
        return clean_neo4j_version(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def settled_store_version(key=None, env=None) -> str:
    """Rules 3-5 alone: the version the base store (key None) / instance `key` resolves to with NO NEO4J_VERSION pin
    and NO open upgrade journal — what it runs once a journal is closed. provision closes an upgrade journal only when
    this names the release the store is left on. Never raises."""
    try:
        if key is None:
            v = read_base_version_record(env)
            if v and neo4j_store_present(neo4j_dist_home(v, env) / "data"):
                return v
            for held in _base_store_folders(env):
                if NEO4J_PREPIN_VERSION in held:
                    return NEO4J_PREPIN_VERSION
                if held:
                    return max(held, key=neo4j_version_tuple)
        elif neo4j_store_present(home_dir(env) / "instances" / str(key) / "data"):
            stamp = instance_version_stamp(key, env)
            if stamp:
                return stamp
            # no stamp (its best-effort write failed): 5.26.0 when that binary is here, else the newest one here
            if neo4j_binary_present(NEO4J_PREPIN_VERSION, env):
                return NEO4J_PREPIN_VERSION
            have = installed_neo4j_versions(env)
            return have[-1] if have else NEO4J_PREPIN_VERSION
    except Exception:
        pass
    return _BASE_NEO4J_DEFAULT_VERSION


def _unpinned_store_version(key=None, env=None) -> str:
    """Rules 2-5: an open upgrade journal, else settled_store_version."""
    rec = read_upgrade_journal(key, env)
    if rec:
        return journal_store_version(rec)
    return settled_store_version(key, env)


def neo4j_pin_ignored(key=None, env=None):
    """(pin, the store's own version, why) when NEO4J_VERSION is refused for THIS existing store (rule 1), else None:
    why "older" — the pin is an older release than the store runs (a downgrade: an instance store the new release
    opened, or the base whose old folder an upgrade emptied); "elsewhere" — the base store lives in another version's
    folder and the pin's holds none (it would orphan the store and make setup create an empty one). A store that does
    not exist yet always takes the pin. Never raises."""
    try:
        pin = neo4j_version_pin(env)
        if not pin:
            return None
        own = _unpinned_store_version(key, env)
        if key is None:
            stores = _base_store_folders(env)[0]
            exists = bool(stores)
        else:
            stores = None
            exists = neo4j_store_present(home_dir(env) / "instances" / str(key) / "data")
        if not exists:
            return None
        if neo4j_version_tuple(pin) < neo4j_version_tuple(own):
            return pin, own, "older"
        if stores is not None and pin not in stores:
            return pin, own, "elsewhere"
    except Exception:
        pass
    return None


def base_store_version(env=None) -> str:
    """The Neo4j version the BASE / legacy store runs (rules 1-5 above). Never raises."""
    try:
        pin = neo4j_version_pin(env)
        if pin and not neo4j_pin_ignored(None, env):
            return pin
        return _unpinned_store_version(None, env)
    except Exception:
        pass
    return _BASE_NEO4J_DEFAULT_VERSION


def instance_store_version(key, env=None) -> str:
    """The Neo4j version per-project instance `key` runs (rules 1-5 above). Never raises."""
    try:
        pin = neo4j_version_pin(env)
        if pin and not neo4j_pin_ignored(key, env):
            return pin
        return _unpinned_store_version(key, env)
    except Exception:
        pass
    return _BASE_NEO4J_DEFAULT_VERSION


def neo4j_binary_present(version, env=None) -> bool:
    """True iff Neo4j <version> is extracted under <home>/neo4j (its completion sentinel, or bin/ + lib/) — the test of
    provision._binary_present_at. Never raises."""
    try:
        nh = neo4j_dist_home(version, env)
        return (nh / ".deepmap-extracted").is_file() or ((nh / "bin").is_dir() and (nh / "lib").is_dir())
    except Exception:
        return False


def installed_neo4j_versions(env=None) -> list:
    """The clean X.Y.Z versions extracted under <home>/neo4j, oldest first. Never raises."""
    out = []
    try:
        root = home_dir(env) / "neo4j"
        for d in (root.iterdir() if root.is_dir() else ()):
            if d.name.startswith("neo4j-community-") and d.is_dir():
                v = clean_neo4j_version(d.name[len("neo4j-community-"):])
                if v and neo4j_binary_present(v, env):
                    out.append(v)
    except Exception:
        pass
    return sorted(set(out), key=neo4j_version_tuple)


def store_neo4j_version(key=None, env=None) -> str:
    """THE answer to "which Neo4j version does this store run": the base store's (key None) or instance `key`'s."""
    return base_store_version(env) if key is None else instance_store_version(key, env)


def base_owner_record_path(env=None) -> Path:
    """Where the base store's owner record lives — exactly provision._base_owner_path(): the base data dir of the
    Neo4j version the base store runs (base_store_version: NEO4J_VERSION when it is a clean X.Y.Z that neither
    downgrades nor orphans the base store, else the version the store was created with or upgraded to, else the
    default)."""
    return neo4j_dist_home(base_store_version(env), env) / "data" / BASE_OWNER_FILE


def base_owner_record(env=None) -> str | None:
    """The project root the base store's owner record names, or None (no record, unreadable, not an
    object, no root) — exactly provision._base_owner_root(). A file read only. Never raises."""
    try:
        rec = json.loads(base_owner_record_path(env).read_text(encoding="utf-8"))
        root = rec.get("root") if isinstance(rec, dict) else None
        return str(root) if root else None
    except Exception:
        return None


def _base_claim_void(root, *, timeout: float = 30, env=None) -> bool:
    """ROUND-5 G4: is ANOTHER project's registry claim on the base (7687, 7474) pair void for `root`? True
    iff the base store's owner record names this project (owner_is_this_project) — the claim is then the
    <= v0.27.4 mis-adoption of this project's store, and trusting it locked the REAL owner out (its route
    said "none", and if it ran setup first its store was abandoned). The caller still verifies the live
    graph's owner before using the base (a stale record proves nothing about what is there now). Never
    raises (-> False)."""
    try:
        rec = base_owner_record(env)
        return bool(rec) and owner_is_this_project(root, rec, timeout=timeout)
    except Exception:
        return False


def base_record_foreign_owner(root, *, timeout: float = 30, env=None) -> str | None:
    """The root the base store's owner record names when it is ANOTHER project (not owner_is_this_project),
    else None (no record, or it names this project). For a caller whose route is this project's registry
    claim on the base pair (the hooks' bolt_target): such a claim on another project's store is the
    <= v0.27.4 mis-adoption, and a listening 7687 proves nothing about whose graph it serves. A file read +
    the owner predicate; provision.foreign_base_claim_problem is the provision-side twin. Never raises."""
    try:
        rec = base_owner_record(env)
        if not rec or owner_is_this_project(root, rec, timeout=timeout):
            return None
        return rec
    except Exception:
        return None


def resolve_neo4j_route(root, env=None, *, honor_env: bool = True, timeout: float = 30) -> Neo4jRoute:
    """THE rule for "which Neo4j holds this project's graph" (ROUND-2 D1). In order:

      1. ``honor_env`` and an explicit NEO4J_URI            -> source "env" (the escape hatch, verbatim) —
         when neo4j_uri_problem accepts it (a Bolt URI); otherwise source "none", kind "uri", the rule's
         own reason (an http:// Neo4j Browser address, another scheme, blank, ...): never handed to a
         driver, and never ignored in favour of this project's own instance (the operator chose an endpoint).
      2. the registry EXISTS but is unreadable / unrecognised -> source "none", kind "registry" (fail
         loud: other projects' claims are invisible, so 7687 can't be told apart).
      3. the project's registered (bolt, http) pair          -> source "registry".
      4. its OWN per-project instance conf declares a pair no other project claims (a lost registry
         entry)                                              -> source "instance-conf".
      5. ANOTHER registered project claims the base 7687/7474 -> source "none", kind "no-instance" —
         unless the base store's owner record names THIS project (ROUND-5 G4, _base_claim_void): that
         claim is another project's mis-adoption of this project's store, so it is void here and the
         route falls through to 6 (the live owner check still decides).
      6. otherwise the base 7687                             -> source "legacy-unverified": the caller
         MUST verify the graph's owner before using it.

    ``honor_env=False`` is for provision, which is itself the NEO4J_URI injector (its operator escape
    hatch is decided at import time), so an earlier injection in the same process never shadows the
    registry. ``timeout`` bounds the git call behind the project key (hooks pass a short one).
    A registry that is simply ABSENT is not a problem (a fresh machine / pre-registry install): the
    route is legacy-unverified. Never raises (an unexpected error -> source "none", kind "error")."""
    key = None
    try:
        e = env if env is not None else os.environ
        if honor_env:
            try:
                override = e.get("NEO4J_URI")
            except Exception:
                override = None
            if override:
                bad = neo4j_uri_problem(override, env)
                if bad:
                    return Neo4jRoute(None, "none", bad, kind="uri")
                return Neo4jRoute(str(override), "env")
        sig, moves = _registry_sig(env), _MIGRATIONS[0]
        status, reg = _read_registry(env)
        if status in ("unreadable", "unrecognized"):
            return Neo4jRoute(None, "none", _registry_problem(status, env), kind="registry")
        key = project_key(root, timeout=timeout, env=env)
        if status == "ok" and _entry_key(reg["projects"], key) is None \
                and (_MIGRATIONS[0] != moves or _registry_sig(env) != sig):
            status, reg = _read_registry(env)      # the key lookup just migrated a pre-fix entry (E1)
            if status in ("unreadable", "unrecognized"):
                return Neo4jRoute(None, "none", _registry_problem(status, env), kind="registry")
        if status == "ok":
            key = _entry_key(reg["projects"], key) or key
            ent = reg["projects"].get(key)
            pair = _pair_from_entry(ent, ("bolt_port", "http_port")) if isinstance(ent, dict) else None
            if pair:
                return Neo4jRoute(bolt_uri(pair[0]), "registry",
                                  bolt=pair[0], http=pair[1], key=key)
        claims = neo4j_claims(key, env, reg=reg if status == "ok" else {})
        own = instance_conf_ports(key, env)
        if own and own != (BASE_BOLT, BASE_HTTP) and own[0] not in claims and own[1] not in claims:
            return Neo4jRoute(bolt_uri(own[0]), "instance-conf",
                              bolt=own[0], http=own[1], key=key)
        owner = claims.get(BASE_BOLT) or claims.get(BASE_HTTP)
        if owner and not _base_claim_void(root, timeout=timeout, env=env):
            return Neo4jRoute(None, "none", no_instance_problem(owner), key=key, kind="no-instance")
        return Neo4jRoute(bolt_uri(BASE_BOLT), "legacy-unverified",
                          bolt=BASE_BOLT, http=BASE_HTTP, key=key)
    except Exception as exc:
        return Neo4jRoute(None, "none",
                          f"could not resolve this project's Neo4j instance ({type(exc).__name__}: {exc})",
                          key=key, kind="error")


def _path_exists(p) -> bool:
    """Does path `p` exist? An unrepresentable / unreadable path -> False. Never raises."""
    try:
        return Path(p).exists()
    except Exception:
        return False


def _abs_on_this_os(p) -> bool:
    """Is `p` an ABSOLUTE path on THIS OS? Windows: a drive AND a root ('C:\\x', 'C:/x') or a UNC share —
    never a drive-less '/x' (a POSIX / WSL stamp), a drive-relative 'C:x' or a relative path; POSIX: a
    leading '/' — never a Windows 'C:/x'. A recorded owner that is not absolute here came from another OS
    (a shared escape-hatch instance, WSL reaching the Windows base) or is garbage: resolving it against the
    working directory would invent a path inside whatever project the command runs from (ROUND-5 G3).
    Never raises (-> False)."""
    try:
        if p is None:
            return False
        s = os.fspath(p)
        if not isinstance(s, str) or not s:
            return False
        if os.name == "nt":
            from pathlib import PureWindowsPath
            return PureWindowsPath(s).is_absolute()
        return s.startswith("/")
    except Exception:
        return False


def _within(owner, base, *, missing_only: bool) -> bool:
    """`owner` is `base` itself, or lies strictly inside it (lexically, on resolved paths; case-insensitive
    on Windows) — with `missing_only`, a strictly-inside owner counts only when its path no longer exists.
    An owner that is not an absolute path on this OS is never within anything (_abs_on_this_os).
    Never raises (-> False)."""
    try:
        if not _abs_on_this_os(owner):
            return False
        o, b = _norm_path(Path(owner).resolve()), _norm_path(Path(base).resolve())
        if o == b:
            return True
        return o.startswith(b + "/") and not (missing_only and _path_exists(owner))
    except Exception:
        return False


def _nearest_existing(p):
    """The closest existing ancestor of path `p` (a Path), or None. Never raises."""
    try:
        for anc in list(Path(p).resolve().parents)[:256]:
            if _path_exists(anc):
                return anc
    except Exception:
        pass
    return None


def _same_project_path(this, owner, *, timeout: float = 30) -> bool:
    """Do `this` (a project's identity or path) and `owner` (a recorded owner: a graph's owning_root stamp,
    the base store's owner record) name ONE project? The ROUND-4 F1 rule behind owner_is_this_project:
      * the same resolved path                                                         -> True;
      * one contains the other: True ONLY when both map to the same project identity (project_identity —
        the git toplevel, else the path itself). A subdirectory of one git repo is that repo; a nested
        repository, or a non-git folder inside (or around) a project, is a project of its own — the
        registry keys it separately, and the old "one contains the other" rule let each read, write and
        adopt the other's legacy graph;
      * `owner` lies strictly inside `this` and its path NO LONGER EXISTS — an old, narrower stamp (a sync
        of a since-deleted subdirectory): raw containment counts, judged at the stamp's nearest EXISTING
        ancestor — `this` itself, or a folder of it that maps to this identity -> True; a vanished stamp
        under a nested repository (or a non-git folder that is its own project) is that project's -> False.
        Owner side only: a vanished `this` inside a real owner is not that owner's project;
      * `owner` is not an absolute path on THIS OS (_abs_on_this_os — a Windows stamp on POSIX, a drive-less
        or relative one on Windows) -> False: another project, never resolved against the cwd (ROUND-5 G3).
    Argument order matters (this, owner). Never raises (-> False)."""
    try:
        if not this or not owner or not _abs_on_this_os(owner):
            return False
        t, o = _norm_path(Path(this).resolve()), _norm_path(Path(owner).resolve())
        if t == o:
            return True
        probe = owner
        if o.startswith(t + "/"):                          # the owner is NARROWER
            if not _path_exists(owner):                    # an old stamp whose directory is gone
                anc = _nearest_existing(owner)
                a = _norm_path(anc) if anc is not None else None
                if a is None or a == t or not a.startswith(t + "/"):
                    return True                            # nothing of it is left but this project's own dir
                probe = anc
        elif not t.startswith(o + "/"):
            return False                                   # disjoint
        return _norm_path(project_identity(this, timeout=timeout)) \
            == _norm_path(project_identity(probe, timeout=timeout))
    except Exception:
        return False


def legacy_owner_verdict(root, owner, graph_has_nodes, *, uri=None, timeout: float = 30):
    """(ok, problem) for USING a "legacy-unverified" route — the pure policy half of the D1 owner check
    (the caller reads the graph: owner = the :_DeepMapMeta owning_root or None, graph_has_nodes = any
    real node, or None if unknown):
      * owner recorded and it is THIS project (owner_is_this_project, F1: the same project identity — a
        subdirectory of the same repo, never a nested repo or a non-git folder around/inside it — a
        vanished narrower stamp, or an identity a pre-UTF-8-fix build stamped)             -> ok;
      * no owner recorded and the graph is EMPTY                                       -> ok (fresh);
      * anything else (another project's graph, or an unowned non-empty / unknown one) -> refuse with
        no_instance_problem(...): the graph can't be shown to be this project's.
    Never raises (an error -> refuse)."""
    where = uri or bolt_uri(BASE_BOLT)
    try:
        if owner:
            if owner_is_this_project(root, owner, timeout=timeout):   # incl. a pre-UTF-8-fix stamp (E1)
                return True, None
            return False, (f"{where} holds project {owner}'s DeepMap graph — "
                           + no_instance_problem())
        if graph_has_nodes is False:
            return True, None
        return False, (f"{where} holds a DeepMap graph with no recorded owner, so it can't be shown to "
                       f"be this project's — " + no_instance_problem())
    except Exception as exc:
        return False, f"could not verify who owns {where} ({type(exc).__name__}: {exc})"


def neo4j_uri_for(root, env=None) -> str:
    """The bolt URI for THIS project's Neo4j — resolve_neo4j_route's URI (see it for the rule): an
    explicit env NEO4J_URI wins verbatim; else the registered bolt; else the project's own instance
    (from its conf, after a lost registry entry); else bolt://127.0.0.1:7687 as a LEGACY route, which a
    caller that can reach the graph should verify with legacy_owner_verdict (resolve_neo4j_route tells
    it apart). A lone or unallocated project, or a machine with NO registry, still resolves to the base
    bolt 7687. Every URI it generates is bolt_uri(<port>) — 127.0.0.1, the address the instances bind
    (ROUND-8 J4); compare URIs with same_bolt_endpoint, never byte-for-byte (older builds wrote localhost).

    Raises RegistryUnreadableError when the registry EXISTS but stays unreadable (after the transient
    retry) or is unrecognised, and its subclass NoProjectInstanceError when this project has no instance
    and the base 7687 is registered to ANOTHER project: the old silent 7687 answer pointed the CLI /
    sync at another project's graph; and its subclass Neo4jUriError when the explicit NEO4J_URI is not a Bolt
    URI DeepMap dials (neo4j_uri_problem). No other exception."""
    r = resolve_neo4j_route(root, env)
    if r.problem or not r.uri:
        cls = {"no-instance": NoProjectInstanceError, "uri": Neo4jUriError}.get(r.kind, RegistryUnreadableError)
        raise cls(r.problem or "could not resolve this project's Neo4j instance")
    return r.uri


def _write_registry(reg, env=None) -> bool:
    """Atomic write of the registry (temp + os.replace). True iff the new content landed. A replace
    that fails with an OSError is retried for _IO_RETRY_S — on Windows it fails whenever a reader has
    the file open, and a swallowed failure silently lost an allocation (a later setup could then hand
    the same ports to another project). Never raises."""
    tmp = None
    try:
        path = registry_path(env)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.parent / (path.name + ".tmp")
        tmp.write_text(json.dumps(reg, indent=2, sort_keys=True), encoding="utf-8")
        deadline = time.monotonic() + _IO_RETRY_S
        while True:
            try:
                os.replace(str(tmp), str(path))
                return True
            except OSError:
                if time.monotonic() >= deadline:
                    break
                time.sleep(_IO_SLEEP_S)
    except Exception:
        pass
    try:
        if tmp is not None:
            tmp.unlink()
    except Exception:
        pass
    return False


def registry_stats(env=None) -> dict:
    """A small summary for the doctor/status honesty line:
    {n_projects, projects:[{key, root, search_port, embed_port, bolt_port, http_port, neo4j_uri,
    last_used}...]}. CHEAP by design (no pid/data-dir probe — it's on the allocation hot path via
    provision._base_adopt_ok / mcp_status). Never raises."""
    try:
        reg = load_registry(env)
        if not reg:
            return {"n_projects": 0, "projects": []}
        rows = []
        for key, ent in reg["projects"].items():
            if not isinstance(ent, dict):
                continue
            mcp = _pair_of(ent)
            bolt = _pair_from_entry(ent, ("bolt_port", "http_port"))
            rows.append({"key": key,
                         "root": ent.get("root"),
                         "search_port": mcp[0] if mcp else None,
                         "embed_port": mcp[1] if mcp else None,
                         "bolt_port": bolt[0] if bolt else None,
                         "http_port": bolt[1] if bolt else None,
                         "neo4j_uri": (bolt_uri(bolt[0]) if bolt else None),
                         "last_used": ent.get("last_used")})
        return {"n_projects": len(rows), "projects": rows}
    except Exception:
        return {"n_projects": 0, "projects": []}


# ---- ROUND-7 I4: setup's MCP BLOCKER (durable) + .mcp.json pair ownership ------------------------
# setup can hit a DeepMap MCP server it cannot restart that still holds THIS project's registered MCP port
# (a server an older build started with no owner record, whose start time can't be read, or that refuses to
# stop). setup then says FAILED — but that verdict lived only in setup's process, and the next SessionStart
# heal found the listener "already listening", wrote mcp_ok:true, and the hooks announced READY over a
# server that may answer from ANOTHER graph. The blocker below is the durable trace: setup writes it when it
# leaves such a server behind and clears it when a later setup succeeds; the hooks treat it as FINAL (a port
# probe can't disprove it — the held port listens by definition — and a refresh doesn't free the port). The
# only other lift is a newer heal that STARTED the blocked servers itself (the port was free again, so the
# server now on it is a current one). <DEEPMAP_HOME>/run/setup-blocked-<project_key>.json, UTF-8, LF:
#   {"kind": "mcp-blocked", "reason": <one line naming the held port/pid and the fix>, "servers": [names],
#    "ports": [int], "pids": [int], "ts": <ISO-8601 UTC>, "ts_epoch": <float>, "project_key", "root"}
SETUP_BLOCKER_KIND = "mcp-blocked"
SETUP_BLOCKER_PREFIX = "setup-blocked-"


def setup_blocker_path(root, env=None, *, timeout: float = 30, key=None) -> Path:
    """<DEEPMAP_HOME>/run/setup-blocked-<project_key>.json for the project `root` is in (`key` skips the git
    call when the caller already has it). The key is project_key's (alias/migration-aware), so every subdir
    of one repo shares the file."""
    k = key or project_key(root, timeout=timeout, env=env)
    return home_dir(env) / "run" / f"{SETUP_BLOCKER_PREFIX}{k}.json"


def _int_list(values) -> list:
    out = []
    for v in (values or ()):
        try:
            n = int(v)
        except (TypeError, ValueError):
            continue
        if n > 0:
            out.append(n)
    return out


def write_setup_blocker(root, *, reason, servers=(), ports=(), pids=(), env=None, timeout: float = 30) -> bool:
    """Persist setup's MCP blocker for `root`'s project (see the section note). Atomic (temp + os.replace),
    UTF-8 JSON with LF. True iff the file landed. Never raises."""
    try:
        key = project_key(root, timeout=timeout, env=env)
        path = setup_blocker_path(root, env, key=key)
        path.parent.mkdir(parents=True, exist_ok=True)
        now = time.time()
        rec = {"kind": SETUP_BLOCKER_KIND,
               "reason": " ".join(str(reason or "").split()) or None,
               "servers": [str(s) for s in (servers or ()) if str(s)],
               "ports": _int_list(ports), "pids": _int_list(pids),
               "ts": datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "ts_epoch": now, "project_key": key,
               "root": project_identity(root, timeout=timeout).replace("\\", "/")}
        _atomic_write_text(path, json.dumps(rec))
        return True
    except Exception:
        return False


def read_setup_blocker(root, env=None, *, timeout: float = 30, key=None):
    """The persisted setup blocker of `root`'s project as a dict, or None when there is none. A file that
    exists but can't be read or parsed still READS AS A BLOCKER ({"kind": "mcp-blocked", "reason": None,
    "corrupt": True, ...}) — the hooks must never steer toward a server on a doubt. `ts_epoch` is always a
    number (the file's mtime when the record lacks a valid one). Never raises."""
    try:
        path = setup_blocker_path(root, env, timeout=timeout, key=key)
    except Exception:
        return None
    try:
        mtime = path.stat().st_mtime
    except FileNotFoundError:
        return None
    except OSError:
        mtime = time.time()
    data = None
    deadline = time.monotonic() + _IO_RETRY_S
    while True:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            break
        except FileNotFoundError:
            return None                      # cleared meanwhile
        except OSError:
            if time.monotonic() >= deadline:  # a Windows sharing violation mid-replace: retry briefly
                break
            time.sleep(_IO_SLEEP_S)
        except Exception:
            break                            # corrupt
    if not isinstance(data, dict):
        return {"kind": SETUP_BLOCKER_KIND, "reason": None, "servers": [], "ports": [], "pids": [],
                "ts_epoch": mtime, "corrupt": True}
    rec = dict(data)
    ts = rec.get("ts_epoch")
    if isinstance(ts, bool) or not isinstance(ts, (int, float)):
        rec["ts_epoch"] = mtime
    rec["servers"] = [str(s) for s in rec.get("servers") or [] if str(s)] \
        if isinstance(rec.get("servers"), list) else []
    rec["ports"] = _int_list(rec.get("ports") if isinstance(rec.get("ports"), list) else ())
    return rec


def clear_setup_blocker(root, env=None, *, timeout: float = 30, key=None) -> bool:
    """Remove `root`'s setup blocker (a successful setup calls this). True iff no blocker file remains
    (nothing to clear included). A Windows sharing violation is retried briefly. Never raises."""
    try:
        path = setup_blocker_path(root, env, timeout=timeout, key=key)
    except Exception:
        return False
    deadline = time.monotonic() + _IO_RETRY_S
    while True:
        try:
            path.unlink()
            return True
        except FileNotFoundError:
            return True
        except OSError:
            if time.monotonic() >= deadline:
                return False
            time.sleep(_IO_SLEEP_S)
        except Exception:
            return False


# ---- ISS-build:013: the Python interpreter a project's setup ran under ---------------------------------------------
# hooks.json starts the SessionStart heal hook as `python … ; python3 …`: whichever interpreter read the payload used
# to spawn `provision.py heal` with ITS sys.executable, and provision starts the MCP servers with heal's. On a host where
# that Python is not the one the installers / setup put DeepMap's dependencies into (install.ps1 tries `py -3` first,
# install.sh uses python3), every revive after a reboot failed. So a setup that ends with working MCP servers (and this
# project's .mcp.json naming them) records the interpreter it ran under, in DeepMap's MACHINE state, keyed by the
# project's SYNC root —
#   <DEEPMAP_HOME>/run/setup-python-<root id>.json
#   {"python": <absolute path>, "root": <the sync root>, "project_key": <key> | null, "version": "3.x.y" | null,
#    "ts": <ISO-8601 UTC>, "ts_epoch": <float>}
# — never a file in the project: the heal hook STARTS the program a record names, on every session start, so a
# repository must never be able to supply one (a committed file would be one). <root id> (setup_python_root_id) is a
# digest of the sync root's path, the directory setup (provision._server_project_root) and the hook
# (deepmap_hook_state.project_root) both find with stat()s alone, so the hook reads the record with no git call and no
# subprocess (a hook counts every millisecond, and nothing may start before its one heal spawn). A record counts only
# when it names THIS root, an absolute path to a program named like a Python interpreter (python… / pypy…), and lies
# OUTSIDE the project's own tree (a DEEPMAP_HOME inside the project is refused by writer and readers alike); heal is
# then started with it while that file still exists (deepmap_hook_state.heal_interpreter; DEEPMAP_PYTHON, when set to a
# startable interpreter, still wins). The hook reads it WITHOUT importing this module (deepmap_hook_state mirrors
# SETUP_PYTHON_PREFIX, setup_python_root_id and read_setup_python's rule; tests/o1_heal_interpreter_r283_gates.py locks
# them together) — change both or neither. instance-rm --purge removes a project's records (clear_setup_python).
SETUP_PYTHON_PREFIX = "setup-python-"
_SETUP_PYTHON_WALK = 64   # ancestor levels _setup_python_tree inspects (== deepmap_hook_state._MAX_WALK)


def split_command_line(val, *, windows=None) -> list:
    """The words of a command-line interpreter value — DEEPMAP_PYTHON such as 'py -3', '/usr/bin/env python3' or
    '"C:\\Program Files\\Py\\python.exe" -X utf8': shlex's POSIX rules; on Windows (`windows`, default: this OS) without
    backslash escapes (a 'C:\\...' path keeps its backslashes) and with one layer of matching quotes removed. [] when it
    can't be split (unbalanced quotes). THE rule for provision._split_command_line (the post-commit block) and the
    SessionStart heal hook (deepmap_hook_state.heal_interpreter). Never raises."""
    try:
        import shlex
        win = (os.name == "nt") if windows is None else bool(windows)
        if win:
            words = shlex.split(str(val), posix=False)
            return [w[1:-1] if len(w) >= 2 and w[0] == w[-1] and w[0] in "\"'" else w for w in words]
        return shlex.split(str(val))
    except Exception:
        return []


def _setup_python_norm(path) -> str:
    """`path` absolute, normalised and case-folded where the OS folds case (os.path.normcase) — the form two spellings
    of one directory compare (and digest) equal in. (Not _norm_path: that one is the lexical forward-slash form the
    owner / identity checks above compare in.)"""
    return os.path.normcase(os.path.abspath(str(path)))


def setup_python_root_id(sync_root) -> str:
    """<root id> of `sync_root`'s interpreter record: the first 16 hex digits of the SHA-256 of its normalised path
    (_setup_python_norm). `sync_root` is the directory whose .deepmap/graph_build.json describes the graph
    (provision._server_project_root / deepmap_hook_state.project_root — one rule, both resolve it), so a session
    opened in any subdirectory finds the record its setup wrote."""
    return hashlib.sha256(_setup_python_norm(sync_root).encode("utf-8", "surrogatepass")).hexdigest()[:16]


def setup_python_path(sync_root, env=None) -> Path:
    """<DEEPMAP_HOME>/run/setup-python-<root id>.json — see the section note."""
    return home_dir(env) / "run" / f"{SETUP_PYTHON_PREFIX}{setup_python_root_id(sync_root)}.json"


def _setup_python_tree(sync_root) -> Path:
    """The project tree `sync_root` belongs to: the nearest directory from it upward that holds `.git` (its work tree's
    top — a repository controls everything under it), else `sync_root` itself. stat()s only, like the walk that found
    the root. Never raises."""
    top = Path(os.path.abspath(str(sync_root)))
    d = top
    try:
        for _ in range(_SETUP_PYTHON_WALK):
            try:
                if (d / ".git").exists():
                    return d
            except OSError:
                pass
            if d.parent == d:
                break
            d = d.parent
    except Exception:
        pass
    return top


def _setup_python_inside(path, top) -> bool:
    """True iff `path` is `top` or lies under it (normalised; a different Windows drive is outside). True when it can't
    be told — a caller refusing on True never trusts a doubt. Never raises."""
    try:
        p, t = _setup_python_norm(path), _setup_python_norm(top)
        try:
            return os.path.commonpath([p, t]) == t
        except ValueError:
            return False   # different drives: not inside
    except Exception:
        return True


def _named_like_python(path) -> bool:
    """True iff `path`'s file name is a Python interpreter's (python…, pypy… — python3.12, python.exe, pypy3): a
    record naming anything else (a shell, a script) is never started as heal's interpreter — a sanity check; what
    keeps a repository from choosing the program is where the record lives. Never raises."""
    try:
        name = os.path.basename(str(path)).lower()
        name = name[:-4] if name.endswith(".exe") else name
        return name.startswith(("python", "pypy"))
    except Exception:
        return False


def _setup_python_record_home_ok(path, sync_root) -> bool:
    """True iff the record file `path` lies OUTSIDE `sync_root`'s project tree (symlinks resolved): a record the
    project's own files could supply is never written or read. Never raises."""
    try:
        return not _setup_python_inside(Path(path).resolve(), _setup_python_tree(sync_root))
    except Exception:
        return False


def write_setup_python(sync_root, python=None, *, key=None, env=None, timeout: float = 30) -> bool:
    """Record `python` (default: this process's sys.executable) as the interpreter the project at `sync_root` (its SYNC
    root, resolved) was set up under (see the section note). Only an absolute path to an existing file named like a
    Python interpreter is recorded, and never into a DEEPMAP_HOME inside the project's own tree — otherwise an earlier
    record stays as it was. `key` (else project_key(sync_root), looked up without migrating) is stored so instance-rm
    --purge can find the record. Atomic (temp + os.replace), UTF-8 JSON with LF. True iff the file landed. Never
    raises."""
    try:
        py = str(python or sys.executable or "")
        if not py or not os.path.isabs(py) or not os.path.isfile(py) or not _named_like_python(py):
            return False
        path = setup_python_path(sync_root, env)
        if not _setup_python_record_home_ok(path, sync_root):
            return False
        if key is None:
            try:
                key = project_key(sync_root, timeout=timeout, env=env, migrate=False)
            except Exception:
                key = None
        now = time.time()
        rec = {"python": py, "root": str(sync_root), "project_key": key,
               "version": sys.version.split()[0] if python is None else None,
               "ts": datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "ts_epoch": now}
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(path, json.dumps(rec))
        return True
    except Exception:
        return False


def read_setup_python(sync_root, env=None):
    """The interpreter write_setup_python recorded for the project at `sync_root` — an absolute path, as a str — or
    None: no record; an unreadable / corrupt one; one that names another root (a digest collision), a value that is not
    an absolute path or not named like a Python interpreter; a record inside the project's own tree. Whether that file
    still EXISTS is the caller's check (the heal hook falls back when it does not). No git, no subprocess. Never
    raises."""
    try:
        path = setup_python_path(sync_root, env)
        if not _setup_python_record_home_ok(path, sync_root):
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        root = data.get("root")
        if not isinstance(root, str) or not root.strip() or _setup_python_norm(root) != _setup_python_norm(sync_root):
            return None
        py = data.get("python")
        ok = isinstance(py, str) and py.strip() and os.path.isabs(py) and _named_like_python(py)
        return py if ok else None
    except Exception:
        return None


def clear_setup_python(keys, env=None) -> int:
    """Remove the interpreter records (see the section note) whose project_key is one of `keys` — instance-rm --purge
    (a project's migrated keys included). Another project's record, or one naming no key, is never touched. Returns
    how many were removed. Never raises."""
    removed = 0
    try:
        want = {str(k) for k in (keys or ()) if k}
        if not want:
            return 0
        run = home_dir(env) / "run"
        if not run.is_dir():
            return 0
        for f in sorted(run.glob(f"{SETUP_PYTHON_PREFIX}*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if isinstance(data, dict) and str(data.get("project_key") or "") in want:
                    f.unlink()
                    removed += 1
            except (OSError, ValueError):
                continue
    except Exception:
        pass
    return removed


def mcp_pair_claimed_by_other(root, pair, env=None, *, timeout: float = 30):
    """The root (else the registry key) of ANOTHER project whose registered MCP pair holds a port of `pair`
    — e.g. the (search, embed) ports of a copied checkout's inherited .mcp.json, which Claude Code connects
    to although they are the original project's servers (answering from ITS graph). None when no other
    entry claims them (this project's own pair, an unregistered pair, no/unreadable registry). Read-only;
    never raises."""
    try:
        ports = set(_int_list(pair or ()))
        if not ports:
            return None
        reg = load_registry(env)
        if not reg:
            return None
        projects = reg["projects"]
        key = project_key(root, timeout=timeout, env=env)
        own_key = _entry_key(projects, key) or key
        own = {key, own_key}
        ent0 = projects.get(own_key)
        if isinstance(ent0, dict):   # every key this project's entry records (E1 migration: aliases & co.)
            for field in ("aliases", "migrated_from", "pending_key"):
                v = ent0.get(field)
                own.update(str(x) for x in (v if isinstance(v, list) else [v]) if x)
        for k, ent in projects.items():
            if k in own or not isinstance(ent, dict):
                continue
            if key in (ent.get("aliases") if isinstance(ent.get("aliases"), list) else ()):
                continue   # this project's own entry under a migrated key
            p = _pair_of(ent)
            if p and ports & set(p):
                return str(ent.get("root") or k)
        return None
    except Exception:
        return None


# ---- ROUND-8 J5: WHICH CODE a detached MCP server runs (owner-sidecar contract) ---------------------------
# A project's MCP servers are long-running detached processes started from the plugin directory of the DeepMap
# version that launched them. After a plugin update they kept serving the OLD code (heal saw them "already
# listening"; doctor said OK) until a setup or a reboot. The owner sidecar (<DEEPMAP_HOME>/run/<name>-<port>.owner,
# written by provision) therefore records the code a server was started from — CODE_ROOT_FIELD (the plugin root,
# forward slashes) and CODE_VERSION_FIELD (.claude-plugin/plugin.json "version") — and heal / the SessionStart
# notify compare it with the code running now (code_identity). A sidecar written before these fields existed is
# UNKNOWN, never "stale": a reader that guessed would restart (or warn about) every server of an older build.
CODE_ROOT_FIELD = "code_root"
CODE_VERSION_FIELD = "code_version"
_CODE_ROOT_ALIASES = (CODE_ROOT_FIELD, "plugin_root")
_CODE_VERSION_ALIASES = (CODE_VERSION_FIELD, "deepmap_version", "plugin_version")


def _plugin_version(plugin_root: Path):
    """The "version" in <plugin_root>/.claude-plugin/plugin.json, or None. Never raises."""
    try:
        data = json.loads((Path(plugin_root) / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8-sig"))
        v = data.get("version") if isinstance(data, dict) else None
        return str(v).strip() or None if v is not None else None
    except Exception:
        return None


def code_identity(build_dir=None) -> dict:
    """{CODE_ROOT_FIELD: <plugin root, forward slashes>, CODE_VERSION_FIELD: <plugin.json version or None>} of
    the DeepMap code at `build_dir` (default: the build/ dir this module runs from) — what provision records in
    a server's owner sidecar when it starts one, and what a reader compares a running server's record with.
    Never raises (a best-effort dict; the root falls back to the unresolved path)."""
    try:
        build = Path(build_dir) if build_dir else Path(__file__).resolve().parent
        try:
            build = build.resolve()
        except Exception:
            pass
        root = build.parent
        return {CODE_ROOT_FIELD: str(root).replace("\\", "/"), CODE_VERSION_FIELD: _plugin_version(root)}
    except Exception:
        return {CODE_ROOT_FIELD: None, CODE_VERSION_FIELD: None}


def _recorded_code(owner: dict):
    """(root, version) an owner record names for its server's code, or (None, None). Accepts the canonical
    fields and their aliases, and a recorded server `script` path (<root>/build/<script>). Never raises."""
    try:
        root = next((owner.get(k) for k in _CODE_ROOT_ALIASES if owner.get(k)), None)
        if not root and owner.get("script"):
            root = str(Path(str(owner["script"])).parent.parent)
        ver = next((owner.get(k) for k in _CODE_VERSION_ALIASES if owner.get(k)), None)
        return (str(root) if root else None, str(ver).strip() if ver else None)
    except Exception:
        return (None, None)


def server_code_stale(owner, current=None, *, started=None, script=None):
    """Should the server whose owner record is `owner` be RESTARTED onto `current` (default: code_identity())? The
    record's code (_recorded_code) judged by code_restart_verdict — the ONE rule heal / mcp-start apply
    (provision._code_restart_verdict) and the SessionStart notify's reconnect note relies on (`started` / `script`: see
    there):
      True  — restart it forward (see code_restart_verdict);
      False — leave it (the current code, a NEWER version, or the same version from a directory that still holds it);
      None  — unknown: no record, a record written before the code fields existed (never guessed stale), or a
              version-only record whose order can't be told.
    Never raises (-> None)."""
    try:
        if not isinstance(owner, dict):
            return None
        rec_root, rec_ver = _recorded_code(owner)
        if not rec_root and not rec_ver:
            return None
        return code_restart_verdict(rec_root, rec_ver, current, started=started, script=script)
    except Exception:
        return None


def code_restart_verdict(rec_root, rec_ver, current=None, *, started=None, script=None):
    """ROUND-9 K4 / ROUND-10 L3+L4: may heal / mcp-start restart a server recorded (or proven by its command line) to
    run the code at `rec_root` (version `rec_ver`; either may be None) onto `current` ({code_root, code_version}, default
    code_identity())? `started` = the server process's start as an epoch and `script` its server script's file name
    (the same-root, unknown-version case — L3). The mirror of provision._code_restart_verdict — the hook's reconnect
    note promises exactly the restart heal performs:
      * no recorded root and no recorded version                      -> None  (unknown: never restarted)
      * the SAME code root (resolved; case-insensitive on Windows):
          both versions known, and different versions                  -> True  (updated in place: the code on disk is
                                                                                  the only code)
          the recorded version UNKNOWN (no owner record — a v0.27.4 server — or one older than the code fields) and
          the process started before <root>/.claude-plugin/plugin.json or <root>/build/<its script> was last written
          (L3 — a --plugin-dir / git-clone install updated in place; no other file's touch counts)
                                                                      -> True  (code_replaced_since)
          otherwise                                                   -> False
      * ANOTHER code root:
          that root provably no longer holds DeepMap (<root>/build is missing — code_present)
                                                                      -> True  (nothing left to protect)
          its version (the record's, else <root>/.claude-plugin/plugin.json) vs the current one:
            current NEWER                                             -> True  (an upgrade)
            current OLDER                                             -> False (never a downgrade)
            EQUAL                                                     -> False (L4: never while that directory still
                  holds it — two INSTALLED copies of one version, two Claude Code accounts or two marketplaces, each
                  read as "the installed one" in its own window and ping-ponged the servers; setup still restarts)
            unordered                                                 -> None  (unknown: never restarted)
    Never raises (-> None)."""
    try:
        cur = current if isinstance(current, dict) else code_identity()
        cur_root, cur_ver = cur.get(CODE_ROOT_FIELD), cur.get(CODE_VERSION_FIELD)
        rec_root = str(rec_root).strip() if rec_root else None
        rec_ver = str(rec_ver).strip() if rec_ver else None
        cur_ver = str(cur_ver).strip() if cur_ver else None
        if not rec_root and not rec_ver:
            return None
        if rec_root and cur_root and _same_code_root(rec_root, cur_root):
            if rec_ver and cur_ver:
                return rec_ver != cur_ver and compare_versions(rec_ver, cur_ver) != 0
            if not rec_ver:
                return code_replaced_since(rec_root, started, script)
            return False
        if rec_root and not code_present(rec_root):
            return True
        if rec_root and not rec_ver:
            rec_ver = _plugin_version(rec_root)
        order = compare_versions(cur_ver, rec_ver)
        if order == 1:
            return True
        if order in (-1, 0):
            return False
        return None
    except Exception:
        return None


def code_replaced_since(root, started, script=None) -> bool:
    """ROUND-10 L3: was DeepMap's code at plugin root `root` replaced IN PLACE after a process started at epoch
    `started`? True iff <root>/.claude-plugin/plugin.json (a release's version marker) or <root>/build/<script> (the
    server itself) was last written more than _CT_TOLERANCE_S after that start — a `git pull` / checkout in a
    --plugin-dir or git-clone install whose servers the previous code started. No other file counts. An unknown start
    or no readable file -> False (provision._code_replaced_since). Never raises."""
    try:
        if started is None or isinstance(started, bool):
            return False
        files = [Path(str(root)) / ".claude-plugin" / "plugin.json"]
        if script:
            files.append(Path(str(root)) / "build" / str(script))
        stamps = []
        for f in files:
            try:
                stamps.append(f.stat().st_mtime)
            except OSError:
                continue
        return bool(stamps) and float(started) + _CT_TOLERANCE_S < max(stamps)
    except Exception:
        return False


def _same_code_root(a, b) -> bool:
    try:
        na = _norm_path(Path(str(a)).resolve()) if _abs_on_this_os(a) else str(a).replace("\\", "/").rstrip("/")
        nb = _norm_path(Path(str(b)).resolve()) if _abs_on_this_os(b) else str(b).replace("\\", "/").rstrip("/")
        return na == nb
    except Exception:
        return str(a) == str(b)


# ---- ROUND-9 K4: a restart only ever moves a server FORWARD ----------------------------------------------------------
# The helpers the restart rule orders code by: the plugin.json version (compare_versions) and whether a recorded
# root's code is still on disk (code_present). ROUND-10 L4: no longer which copy Claude Code lists as installed —
# each Claude Code account / marketplace reads its own installed_plugins.json, so two windows each found their own
# copy "the installed one" and restarted the servers onto it in turn; an equal version never restarts now. All
# read-only and never raise.
_VERSION_RE = re.compile(r"^[vV]?(\d+(?:\.\d+)*)(?:[-.]?([0-9A-Za-z][0-9A-Za-z.-]*))?(?:\+[0-9A-Za-z.-]*)?$")


def _version_key(v):
    """A sortable key for a plugin version ('0.28.0', 'v0.28', '0.28.0-rc.1', '0.28.0+build'), or None when it is
    missing / not a version. Trailing zero components are dropped (0.28 == 0.28.0); a pre-release tag sorts BELOW
    its release (numeric identifiers numerically, below alphanumeric ones); build metadata is ignored."""
    try:
        if v is None or isinstance(v, bool):
            return None
        m = _VERSION_RE.match(str(v).strip())
        if not m:
            return None
        nums = [int(x) for x in m.group(1).split(".")]
        while len(nums) > 1 and nums[-1] == 0:
            nums.pop()
        tags = tuple((0, int(t), "") if t.isdigit() else (1, 0, t.lower())
                     for t in re.split(r"[.-]", m.group(2) or "") if t)
        return (tuple(nums), 0 if tags else 1, tags)
    except Exception:
        return None


def compare_versions(a, b):
    """1 when version `a` is NEWER than `b`, -1 when it is older, 0 when they are the same version, None when either
    is missing or unparseable (never guessed). Never raises."""
    ka, kb = _version_key(a), _version_key(b)
    if ka is None or kb is None:
        return None
    return (ka > kb) - (ka < kb)


def code_present(root) -> bool:
    """Is the DeepMap code at plugin root `root` still on disk (<root>/build is a directory)? False only when that is
    PROVEN missing (not found / not a directory / an empty root); an unreadable path counts as present, so a restart
    is never triggered on a guess. Never raises."""
    try:
        if not root:
            return False
        st = os.stat(os.path.join(str(root), "build"))
        import stat as _stat
        return _stat.S_ISDIR(st.st_mode)
    except (FileNotFoundError, NotADirectoryError):
        return False
    except Exception:
        return True


# ---- ROUND-9 K3 / ROUND-10 L6: WHICH code a server runs — its own ARGUMENT VECTOR --------------------------------------
# v0.27.4 (and every build before round 8) wrote no owner sidecar at all, only run/<name>-<port>.pid, so after the
# upgrade heal and the SessionStart notify had no record to compare. The running process's argv (<python> <plugin
# root>/build/<server script>) names the code it runs. Read EXACTLY (ROUND-10 L6 — a removed plugin root whose path
# holds a space must still be found): Linux /proc/<pid>/cmdline (NUL-separated), macOS the kernel's own copy of argv
# (sysctl KERN_PROCARGS2 — `ps` joins the words with spaces, so a path with a space can't be told from two words),
# Windows the process's command line (NtQueryInformationProcess, sub-millisecond, no PowerShell) split by the Windows
# argv rules. provision reads the argv the same way (its _pid_argv / _script_from_argv), so heal and the hooks agree.
MCP_SERVER_SCRIPTS = {"deepmap-search": "deepmap_search_server.py", "deepmap-embed": "deepmap_embed_server.py"}
MCP_PORT_ENVS = {"deepmap-search": "DEEPMAP_SEARCH_PORT", "deepmap-embed": "DEEPMAP_EMBED_PORT"}


def _win_cmdline(pid):
    """The command line of Windows process `pid` (ProcessCommandLineInformation, Windows 8.1+), or None when it can't
    be opened / read. Never raises."""
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        ntdll = ctypes.WinDLL("ntdll")
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        k32.CloseHandle.argtypes = (wintypes.HANDLE,)
        q = ntdll.NtQueryInformationProcess
        q.restype = ctypes.c_long
        q.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong))
        h = k32.OpenProcess(0x1000, False, int(pid))     # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return None
        try:
            need = ctypes.c_ulong(0)
            q(h, 60, None, 0, ctypes.byref(need))        # 60 = ProcessCommandLineInformation: ask the size first
            size = max(int(need.value), 64)
            for _ in range(3):
                buf = ctypes.create_string_buffer(size)
                status = q(h, 60, buf, size, ctypes.byref(need)) & 0xFFFFFFFF
                if status == 0:
                    break
                if status in (0xC0000004, 0x80000005, 0xC0000023) and int(need.value) > size:
                    size = int(need.value)                # the command line grew: retry with the size it needs
                    continue
                return None
            else:
                return None

            class _UnicodeString(ctypes.Structure):
                _fields_ = [("Length", ctypes.c_ushort), ("MaximumLength", ctypes.c_ushort),
                            ("Buffer", ctypes.c_void_p)]
            us = _UnicodeString.from_buffer(buf)
            if not us.Buffer or not us.Length:
                return ""
            return ctypes.wstring_at(us.Buffer, us.Length // 2)
        finally:
            k32.CloseHandle(h)
    except Exception:
        return None


def _parse_procargs2(raw):
    """argv from a macOS sysctl KERN_PROCARGS2 buffer — <int argc><exec path>\\0<\\0 padding><argv[0]>\\0...
    <argv[argc-1]>\\0<environment...> — or None when it is truncated / not that layout. Never raises."""
    try:
        raw = bytes(raw or b"")
        if len(raw) < 5:
            return None
        argc = int.from_bytes(raw[:4], sys.byteorder)
        if argc <= 0 or argc > 65536:
            return None
        i = raw.find(b"\0", 4)                          # end of the exec path
        if i < 0:
            return None
        while i < len(raw) and raw[i] == 0:              # its NUL padding
            i += 1
        args = []
        while len(args) < argc:
            if i >= len(raw):
                return None                              # truncated: never a partial argv
            j = raw.find(b"\0", i)
            if j < 0:
                return None
            args.append(raw[i:j].decode("utf-8", "surrogateescape"))
            i = j + 1
        return args
    except Exception:
        return None


def _darwin() -> bool:
    """sys.platform == "darwin" — a seam (KERN_PROCARGS2 is macOS's; the gates simulate it on any OS)."""
    return sys.platform == "darwin"


def _darwin_argv(pid):
    """The exact argv of a macOS process via sysctl KERN_PROCARGS2 (the kernel's copy; a process of the same user), or
    None when it can't be read (not macOS, another user's process). Never raises."""
    try:
        if not _darwin():
            return None
        import ctypes
        import ctypes.util
        lib = ctypes.util.find_library("c")
        if not lib:
            return None
        libc = ctypes.CDLL(lib, use_errno=True)
        libc.sysctl.restype = ctypes.c_int
        libc.sysctl.argtypes = (ctypes.POINTER(ctypes.c_int), ctypes.c_uint, ctypes.c_void_p,
                                ctypes.POINTER(ctypes.c_size_t), ctypes.c_void_p, ctypes.c_size_t)
        argmax = ctypes.c_int(0)
        size = ctypes.c_size_t(ctypes.sizeof(argmax))
        mib = (ctypes.c_int * 2)(1, 8)                    # CTL_KERN, KERN_ARGMAX
        if libc.sysctl(mib, 2, ctypes.byref(argmax), ctypes.byref(size), None, 0) != 0 or argmax.value <= 0:
            return None
        buf = ctypes.create_string_buffer(argmax.value)
        size = ctypes.c_size_t(argmax.value)
        mib3 = (ctypes.c_int * 3)(1, 49, int(pid))       # CTL_KERN, KERN_PROCARGS2, pid
        if libc.sysctl(mib3, 3, buf, ctypes.byref(size), None, 0) != 0:
            return None
        return _parse_procargs2(buf.raw[:size.value])
    except Exception:
        return None


def pid_cmdline(pid):
    """The command line of live process `pid` as one string, or None when it can't be read (gone, another user's
    process, no reader). Where the ARGUMENTS are known exactly they are joined with NUL ("\\0"), so a path with a
    space stays one argument: Linux (/proc) and macOS (sysctl KERN_PROCARGS2); elsewhere, or when that can't be read,
    `ps -o command=` (words joined with spaces). Windows returns the process's own command line (split exactly by
    cmdline_argv). "" means readable but empty (a kernel thread / zombie). Never raises."""
    try:
        pid = int(pid)
        if pid <= 0:
            return None
        if _windows():
            return _win_cmdline(pid)
        proc = Path(_PROC, str(pid), "cmdline")
        if proc.exists():
            return proc.read_bytes().rstrip(b"\0").decode("utf-8", "surrogateescape")
        argv = _darwin_argv(pid)
        if argv:
            return "\0".join(argv)
        if os.name == "nt":
            return None   # the POSIX branch reached on Windows through the test seam: no ps here
        out = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=5)
        return (out.stdout or "").strip() or None
    except Exception:
        return None


def _split_windows_cmdline(s: str) -> list:
    """argv of a Windows command line, by the rules the MS C runtime / CommandLineToArgvW apply (what
    subprocess.list2cmdline targets): argv[0] ends at the closing quote (no escapes) or at white space; later words
    split at unquoted white space, 2n backslashes + '"' -> n backslashes + a quote toggle, 2n+1 backslashes + '"' ->
    n backslashes + a literal '"', '""' inside quotes -> a literal '"', other backslashes are literal. Leading white
    space makes argv[0] empty, as the API does; "" -> []."""
    args: list = []
    i, n = 0, len(s)
    if not s:
        return args
    if s[i] == '"':
        j = s.find('"', i + 1)
        if j < 0:
            args.append(s[i + 1:])
            return args
        args.append(s[i + 1:j])
        i = j + 1
    else:
        j = i
        while j < n and s[j] not in " \t":
            j += 1
        args.append(s[i:j])
        i = j
    while True:
        while i < n and s[i] in " \t":
            i += 1
        if i >= n:
            return args
        cur: list = []
        quoted = False
        while i < n:
            ch = s[i]
            if ch == "\\":
                j = i
                while j < n and s[j] == "\\":
                    j += 1
                k = j - i
                if j < n and s[j] == '"':
                    cur.append("\\" * (k // 2))
                    if k % 2:
                        cur.append('"')
                        i = j + 1
                    else:
                        i = j
                    continue
                cur.append("\\" * k)
                i = j
                continue
            if ch == '"':
                if quoted and i + 1 < n and s[i + 1] == '"':
                    cur.append('"')
                    i += 2
                    continue
                quoted = not quoted
                i += 1
                continue
            if ch in " \t" and not quoted:
                break
            cur.append(ch)
            i += 1
        args.append("".join(cur))


def cmdline_argv(cmd, *, windows=None):
    """The EXACT argument vector a pid_cmdline string encodes, or None when it can't be told exactly: a NUL-joined
    argv (Linux, macOS KERN_PROCARGS2) is split on NUL; a Windows command line by the Windows argv rules
    (_split_windows_cmdline); a POSIX string without NULs is exact only as ONE word with no white space (a lossy
    `ps` line joins the words with spaces). `windows` defaults to _windows(). Never raises."""
    try:
        if cmd is None:
            return None
        s = str(cmd)
        if "\0" in s:
            return s.split("\0")
        if _windows() if windows is None else windows:
            return _split_windows_cmdline(s)
        return [s] if s and not any(c.isspace() for c in s) else None
    except Exception:
        return None


def pid_argv(pid):
    """The exact argv of live process `pid` (a list of str — cmdline_argv of pid_cmdline), or None when it can't be
    read exactly. Never raises."""
    try:
        return cmdline_argv(pid_cmdline(pid))
    except Exception:
        return None


def _looks_absolute(p: str) -> bool:
    return bool(p) and (p.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:[\\/]", p) is not None)


def _is_script_path(p: str, name: str) -> bool:
    base = re.split(r"[\\/]", str(p).strip())[-1]
    return base.lower() == name.lower() if os.name == "nt" else base == name


def script_from_argv(argv, script_name: str):
    """The absolute path of `script_name` (e.g. deepmap_search_server.py) that argument vector `argv` runs — the
    first ABSOLUTE argument whose file name is `script_name` (case-insensitively on Windows) — or None. The file need
    not exist any more (a removed plugin version still names the code it runs). Never raises."""
    try:
        name = str(script_name or "")
        if not name or not isinstance(argv, (list, tuple)):
            return None
        for arg in argv:
            a = str(arg or "").strip()
            if a and _is_script_path(a, name) and _looks_absolute(a):
                return a
        return None
    except Exception:
        return None


def script_from_cmdline(cmd, script_name: str):
    """The absolute path of `script_name` (e.g. deepmap_search_server.py) that command line `cmd` runs, or None. An
    exactly-known argv (cmdline_argv: NUL-joined, or a Windows command line) is read word by word
    (script_from_argv). A lossy space-joined POSIX line (`ps`) is read by candidate start: the first that names an
    existing file, else — a script that was removed since — the one whose preceding words name an existing file
    (the interpreter), else an absolute last token. The file name compares case-insensitively on Windows. Never
    raises."""
    try:
        s = str(cmd or "")
        name = str(script_name or "")
        if not s or not name:
            return None
        argv = cmdline_argv(s)
        if argv is not None:
            got = script_from_argv(argv, name)
            if got or "\0" in s:
                return got
        flags = re.IGNORECASE if os.name == "nt" else 0
        m = re.search(r'"([^"]*[\\/]' + re.escape(name) + r')"', s, flags)
        if m and _is_script_path(m.group(1), name) and _looks_absolute(m.group(1)):
            return m.group(1)
        idx = (s.lower().find(name.lower()) if flags else s.find(name))
        if idx < 0:
            return None
        head = s[:idx + len(name)]
        if not _is_script_path(head, name):
            return None
        starts = sorted({i + 1 for i, ch in enumerate(head) if ch == " "} | {0})
        for start in starts:
            cand = head[start:].strip().strip('"')
            if _looks_absolute(cand) and Path(cand).is_file():
                return cand
        for start in starts:
            if start == 0:
                continue
            cand = head[start:].strip().strip('"')
            interp = head[:start].strip().strip('"')
            if _looks_absolute(cand) and interp and _looks_absolute(interp) and Path(interp).is_file():
                return cand
        tok = head.rsplit(" ", 1)[-1].strip('"')
        return tok if _looks_absolute(tok) and _is_script_path(tok, name) else None
    except Exception:
        return None


def running_code_root(pid, server, cmd=None):
    """The DeepMap plugin root (forward slashes) a live MCP server process runs — <root>/build/<script>, read from
    its argv (`cmd` = its pid_cmdline when the caller already has it). `server` is the server name (deepmap-search /
    deepmap-embed) or the script's file name. The root need not exist any more (a removed plugin version). None
    when unknown: an unreadable command line, another script, a script not inside a build/ directory. Never
    raises."""
    try:
        script = MCP_SERVER_SCRIPTS.get(str(server), str(server))
        path = script_from_cmdline(pid_cmdline(pid) if cmd is None else cmd, script)
        if not path:
            return None
        parts = re.split(r"[\\/]+", path.rstrip("\\/"))
        if len(parts) < 3 or parts[-2].lower() != "build":
            return None
        root = path.rstrip("\\/")[: -len(parts[-1])].rstrip("\\/")
        root = root[: -len(parts[-2])].rstrip("\\/")
        return root.replace("\\", "/") or None
    except Exception:
        return None


# ---- ROUND-10 L1: WHICH process a pid file names — its START STAMP (the mirror of provision's D2 rule) -------------
# heal signals a server only when the pid in its pid file is provably the process that file (and the owner record)
# describes: a pid file outlives a reboot and the OS recycles PIDs. The hook's reconnect note must apply the SAME
# identity rule, or it promises a restart heal refuses. The stamp is what the OS keeps FIXED for a process's life,
# in provision._pid_create_time's format (records are compared across the two modules): Windows — the creation
# time as epoch seconds (GetProcessTimes: sub-millisecond, no PowerShell); Linux — "linux:<boot_id>:<ticks>"
# (/proc/<pid>/stat field 22 — never an epoch built from btime, which moves with the wall clock); macOS / BSD —
# `ps -o lstart=` read in UTC, epoch seconds at a 1-second resolution.
_CT_TOLERANCE_S = 2.0


def _win_create_time(pid):
    """The creation time of Windows process `pid` as epoch seconds (GetProcessTimes), or None. Never raises."""
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        k32.CloseHandle.argtypes = (wintypes.HANDLE,)
        ft_p = ctypes.POINTER(wintypes.FILETIME)
        k32.GetProcessTimes.restype = wintypes.BOOL
        k32.GetProcessTimes.argtypes = (wintypes.HANDLE, ft_p, ft_p, ft_p, ft_p)
        h = k32.OpenProcess(0x1000, False, int(pid))     # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return None
        try:
            c, e, k, u = (wintypes.FILETIME() for _ in range(4))
            if not k32.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(e), ctypes.byref(k), ctypes.byref(u)):
                return None
            ft = (int(c.dwHighDateTime) << 32) | int(c.dwLowDateTime)
            return (ft - 116444736000000000) / 1e7 if ft > 116444736000000000 else None
        finally:
            k32.CloseHandle(h)
    except Exception:
        return None


def _linux_boot_id():
    try:
        return Path(_PROC, "sys", "kernel", "random", "boot_id").read_text(encoding="utf-8").strip() or None
    except Exception:
        return None


def _linux_btime():
    try:
        for ln in Path(_PROC, "stat").read_text(encoding="utf-8", errors="replace").splitlines():
            if ln.startswith("btime "):
                return float(ln.split()[1])
    except Exception:
        pass
    return None


def _clk_tck() -> float:
    try:
        return float(os.sysconf("SC_CLK_TCK"))
    except Exception:
        return 100.0


def pid_create_time(pid):
    """The START STAMP of live process `pid` in provision._pid_create_time's format (see the section note), or None
    when unknown. Never raises."""
    try:
        pid = int(pid)
        if pid <= 0:
            return None
        if _windows():
            return _win_create_time(pid)
        stat = Path(_PROC, str(pid), "stat")
        if stat.exists():
            raw = stat.read_text(encoding="utf-8", errors="replace")
            fields = raw[raw.rindex(")") + 2:].split()
            return f"linux:{_linux_boot_id() or '-'}:{int(fields[19])}"
        if os.name == "nt":
            return None   # the POSIX branch reached on Windows through the test seam: no ps here
        import calendar
        out = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=5,
                             env=dict(os.environ, LC_ALL="C", LANG="C", TZ="UTC0"))
        txt = " ".join((out.stdout or "").split())
        if not txt:
            return None
        return float(calendar.timegm(time.strptime(txt, "%a %b %d %H:%M:%S %Y")))
    except Exception:
        return None


def _parse_linux_stamp(stamp):
    try:
        if isinstance(stamp, str) and stamp.startswith("linux:"):
            _, boot, ticks = stamp.split(":", 2)
            return (None if boot in ("", "-") else boot), int(ticks)
    except Exception:
        pass
    return None


def same_start(recorded, current):
    """Do two start stamps name the same process? True / False, or None when they are not comparable (provision's
    _same_start). Never raises."""
    try:
        a, b = _parse_linux_stamp(recorded), _parse_linux_stamp(current)
        if a is not None and b is not None:
            if a[1] != b[1]:
                return False
            return a[0] is None or b[0] is None or a[0] == b[0]
        if a is not None or b is not None:
            return None
        if isinstance(recorded, bool) or isinstance(current, bool):
            return None
        return abs(float(recorded) - float(current)) <= _CT_TOLERANCE_S
    except Exception:
        return None


def _start_epoch(stamp):
    lin = _parse_linux_stamp(stamp)
    if lin is not None:
        bt = _linux_btime()
        return None if bt is None else bt + lin[1] / _clk_tck()
    try:
        return None if isinstance(stamp, bool) else float(stamp)
    except (TypeError, ValueError):
        return None


def pid_check(pid, *, recorded=None, pidfile=None, created=None):
    """(verdict, basis): is the live `pid` the process a record describes? provision._pid_check's rule, verbatim:
    (True|False, "stamp") — two comparable start stamps; (True|False, "pidfile") — no comparable record: the process
    against its pid file's mtime (started later = suggestive, never signalled); (None, "unproven") — that fallback on
    LINUX (a clock step can fake "later": the command line decides); (None, "unknown") — no readable stamp. Never
    raises."""
    try:
        if created is None:
            created = pid_create_time(pid)
        if created is None:
            return None, "unknown"
        if recorded is not None:
            same = same_start(recorded, created)
            if same is not None:
                return same, "stamp"
        if pidfile is not None:
            try:
                written = Path(pidfile).stat().st_mtime
            except OSError:
                return None, "unknown"
            epoch = _start_epoch(created)
            if epoch is None:
                return None, "unknown"
            if epoch <= written + _CT_TOLERANCE_S:
                return True, "pidfile"
            if _parse_linux_stamp(created) is not None:
                return None, "unproven"
            return False, "pidfile"
        return None, "unknown"
    except Exception:
        return None, "unknown"


def proc_environ(pid):
    """The environment a live LINUX process was started with (/proc/<pid>/environ), or None when it can't be read (not
    Linux, gone, another user's). Never raises."""
    try:
        if _windows():
            return None
        raw = Path(_PROC, str(int(pid)), "environ").read_bytes()
        env: dict = {}
        for item in raw.split(b"\0"):
            if b"=" in item:
                k, _, v = item.partition(b"=")
                env[k.decode("utf-8", "surrogateescape")] = v.decode("utf-8", "surrogateescape")
        return env
    except Exception:
        return None


# ---- ROUND-10 L1: ONE rule for "heal restarts this project's server onto the current code" ---------------------------
# The SessionStart notify promises "the heal restarts them" exactly when heal will: own_server_code_stale is the
# mirror of provision._own_server_code_stale (+ _preowner_server_code_stale, _own_mcp_server_pid,
# _mcp_pid_owned_elsewhere) over the same files and OS facts, with in-process probes (the hooks run under a 10 s
# budget). ROUND-10 L1: an owner record naming ANOTHER pid than the pid file is the record of an EARLIER process on
# that port (a newer build's servers died and an older build — v0.27.4 writes only the pid file — respawned them): it
# is the same as no record, and the pid-file process is judged on the pre-owner evidence — as is an owner file that
# can't be read as a record (provision._mcp_owner_record returns None for all three; that evidence alone is what a
# restart may rest on).
OWNER_ABSENT = "absent"
OWNER_EARLIER = "earlier"
OWNER_MATCH = "match"
OWNER_UNREADABLE = "unreadable"


def own_keys(root, *, timeout: float = 30, env=None) -> list:
    """Every registry key THIS project is known by: project_key(root) plus the keys its entry records (a key
    migration's `aliases` / `migrated_from` / `pending_key`, validated as hex keys) — provision._own_keys's rule.
    [] when the key can't be computed. Never raises."""
    try:
        key = project_key(root, timeout=timeout, env=env)
    except Exception:
        return []
    out = [key]
    try:
        ent = ((load_registry(env) or {}).get("projects") or {}).get(key)
        if isinstance(ent, dict):
            cands = list(ent.get("aliases") if isinstance(ent.get("aliases"), list) else [])
            cands += [ent.get("migrated_from"), ent.get("pending_key")]
            for a in cands:
                if isinstance(a, str) and re.fullmatch(r"[0-9a-f]{8,64}", a) and a not in out:
                    out.append(a)
    except Exception:
        pass
    return out


def mcp_run_files(name: str, port: int, env=None):
    """(pid file, owner file) of MCP server `name` on `port`: <DEEPMAP_HOME>/run/<name>-<port>.pid / .owner."""
    run = home_dir(env) / "run"
    return run / f"{name}-{int(port)}.pid", run / f"{name}-{int(port)}.owner"


def _read_pidfile(pf):
    try:
        return int(Path(pf).read_text(encoding="utf-8").strip())
    except Exception:
        return None


def mcp_owner_state(name: str, port: int, pid, env=None):
    """(state, record) of the owner sidecar of server `name` on `port` whose pid file names `pid`:
      OWNER_ABSENT     — no owner file;
      OWNER_MATCH      — a record (a JSON object with an integer `pid`) naming `pid`;
      OWNER_EARLIER    — a record naming ANOTHER pid: an earlier process's (ROUND-10 L1: the same as no record);
      OWNER_UNREADABLE — a file that is not such a record (partly written, garbage, unreadable): no record either.
    Only OWNER_MATCH is an owner record (provision._mcp_owner_record). Never raises."""
    try:
        _pf, of = mcp_run_files(name, port, env)
        try:
            text = of.read_text(encoding="utf-8")
        except FileNotFoundError:
            return OWNER_ABSENT, None
        rec = json.loads(text)
        if not isinstance(rec, dict) or isinstance(rec.get("pid"), bool):
            return OWNER_UNREADABLE, None
        rec_pid = int(rec["pid"])
        return (OWNER_MATCH if pid is not None and rec_pid == int(pid) else OWNER_EARLIER), rec
    except Exception:
        return OWNER_UNREADABLE, None


def mcp_pid_owned_elsewhere(pid, name: str, port: int, pidfile, *, keys=(), created=None, env=None):
    """What (other than THIS port's server) provably runs the live MCP-server `pid`, else None — provision's
    _mcp_pid_owned_elsewhere: its environment (Linux) says it serves ANOTHER port or another project
    (DEEPMAP_PROJECT_KEY not one of `keys`), or ANOTHER port's pid file names the same pid and its owner record's start
    stamp matches the process. Never raises."""
    try:
        penv = proc_environ(pid)
        if penv is not None:
            want = penv.get(MCP_PORT_ENVS.get(name, ""))
            if want is not None and str(want).strip() != str(port):
                return f"the {name} server for port {want}"
            pk = penv.get("DEEPMAP_PROJECT_KEY")
            if pk and keys and pk not in keys:
                return f"project {pk}'s {name} server"
        if created is None:
            created = pid_create_time(pid)
        if created is None:
            return None
        run = home_dir(env) / "run"
        for pf in (sorted(run.glob("deepmap-*-*.pid")) if run.is_dir() else []):
            try:
                if _same_code_root(pf, pidfile) or _read_pidfile(pf) != int(pid):
                    continue
                rec = json.loads(pf.with_suffix(".owner").read_text(encoding="utf-8"))
                if isinstance(rec, dict) and int(rec.get("pid", -1)) == int(pid) \
                        and rec.get("created") is not None and same_start(rec["created"], created) is True:
                    return f"the server of {pf.stem} (its own pid file and owner record name it)"
            except Exception:
                continue
    except Exception:
        pass
    return None


def own_server_code_stale(root, name: str, port: int, current=None, *, keys=None, timeout: float = 30, env=None):
    """ROUND-10 L1: (old_root, old_version) — either may be None — when heal / mcp-start WILL restart THIS project's MCP
    server `name` on `port` onto the code `current` ({code_root, code_version}, default code_identity()), else None.
    The mirror of provision._own_server_code_stale, so the hook's reconnect note and heal's restart agree:
      * the per-port pid file names a pid;
      * an owner record naming that pid (OWNER_MATCH): it names one of this project's keys (own_keys); its code
        fields — else the live process's command line (running_code_root) and, for its own plugin dir, when the
        process started (its recorded stamp, else its live one — L3) — are code_restart_verdict True; the pid is alive,
        its command line runs this server's script, and it is the very process the record describes (pid_check against
        the recorded start stamp, else the pid file);
      * NO owner record (L1 — provision._mcp_owner_record's None): no owner file (a v0.27.4 server), one naming ANOTHER
        pid (OWNER_EARLIER: an earlier process's), or one that is no readable record (OWNER_UNREADABLE): the port is
        REGISTERED to this project (lookup_ports — never a pair read from a committed / copied .mcp.json); the pid is
        alive and its READABLE command line runs this server's script from a DeepMap root that code_restart_verdict
        (with the process's start — L3) says to replace; it started before its pid file was written (pid_check; Linux
        "unproven" -> the command line decides); nothing says it is another port's / another project's server
        (mcp_pid_owned_elsewhere).
    `keys` = own_keys(root) when the caller already has them. Never raises (-> None)."""
    try:
        script = MCP_SERVER_SCRIPTS.get(str(name))
        if not script:
            return None
        pf, _of = mcp_run_files(name, port, env)
        pid = _read_pidfile(pf)
        if pid is None:
            return None
        state, owner = mcp_owner_state(name, port, pid, env)
        cur = current if isinstance(current, dict) else code_identity()
        keys = list(keys) if keys else own_keys(root, timeout=timeout, env=env)
        if not keys:
            return None
        if state == OWNER_MATCH:
            if owner.get("project_key") not in keys:
                return None
            rec_root, rec_ver = _recorded_code(owner)
            started = None
            if not rec_root and not rec_ver:
                if not _pid_alive(pid):
                    return None
                rec_root = running_code_root(pid, name)
                if not rec_root:
                    return None
                stamp = owner.get("created")
                started = _start_epoch(stamp if stamp is not None else pid_create_time(pid))
            if code_restart_verdict(rec_root, rec_ver, cur, started=started, script=script) is not True:
                return None
            if not _pid_alive(pid):
                return None
            cmd = pid_cmdline(pid)
            if not (cmd and script in cmd):
                return None
            if pid_check(pid, recorded=owner.get("created"), pidfile=pf)[0] is not True:
                return None
            return (rec_root, rec_ver)
        # OWNER_ABSENT / OWNER_EARLIER / OWNER_UNREADABLE — no owner record: the pre-owner evidence (K3, ROUND-10 L1)
        if int(port) not in {int(p) for p in (lookup_ports(root, env=env) or ())}:
            return None
        if not _pid_alive(pid):
            return None
        cmd = pid_cmdline(pid)
        if not cmd or script not in cmd:
            return None
        rec_root = running_code_root(pid, name, cmd=cmd)
        if not rec_root:
            return None
        created = pid_create_time(pid)
        if code_restart_verdict(rec_root, None, cur, started=_start_epoch(created), script=script) is not True:
            return None
        same, basis = pid_check(pid, recorded=None, pidfile=pf, created=created)
        if not (same is True or (same is None and basis == "unproven")):
            return None
        if mcp_pid_owned_elsewhere(pid, name, port, pf, keys=keys, created=created, env=env):
            return None
        return (rec_root, None)
    except Exception:
        return None
