#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DeepMap provisioner (C1) — one-command setup.

Replaces the manual CLI dance. `python build/provision.py setup [<dir>]`:
  1. ensures a Neo4j Community instance is running (the project's own, e.g. bolt://127.0.0.1:7689) with auth
     neo4j/<password> — fetching + configuring + starting it if absent;
  2. runs the full sync -> index -> embed pipeline over <dir> (default ".");
  3. verifies the search path end-to-end (the same call the MCP `deepmap_search` tool makes).

Bakes in the first live-Neo4j findings so first contact just works:
  - FD-1.6 (AUTH-1): relaxes `dbms.security.auth_minimum_password_length` so the 7-char dev
    password "deepmap" is accepted (Neo4j's default 8-char minimum rejects it).
  - TELEMETRY-1: disables Neo4j anonymous usage reporting + client telemetry (the
    zero-telemetry / air-gap claim).

Idempotent: a second run finds Neo4j already up and only re-syncs. Cross-platform (Windows
.bat / POSIX bin); the validated/running reference platform is Windows + Neo4j 5.26 LTS (a new store gets
5.26.32; a store created earlier keeps its version until setup / `neo4j-upgrade` moves it — upgrade_neo4j).

Commands:
    python build/provision.py setup [<dir>]   # ensure Neo4j + sync + start MCP servers + verify
    python build/provision.py ensure          # ensure Neo4j only (download/configure/start)
    python build/provision.py status          # graph + index + embedding census
    python build/provision.py stop            # stop the managed Neo4j instance
    python build/provision.py mcp-start       # start the deepmap_search/deepmap_embed HTTP servers (detached)
    python build/provision.py mcp-stop        # stop the managed MCP servers
    python build/provision.py mcp-status      # show MCP server URLs + liveness
    python build/provision.py heal            # revive an already-set-up project: ensure Neo4j + MCP, no re-sync
    python build/provision.py git-hook --install|--uninstall|--status [<dir>]
                                              # OPT-IN post-commit auto-sync (chained; never installed by setup)
    python build/provision.py neo4j-upgrade [<dir>] [--to X.Y.Z] [--check]
                                              # move this project's store to a newer Neo4j patch release
                                              # (verified, rolled back on failure; setup does it by default)

Env: DEEPMAP_HOME (~/.deepmap), NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD,
     NEO4J_VERSION (the release a new store gets, and a store that exists runs — never an OLDER release than
     that store runs, nor a base folder holding no store while another holds it: ignored there, with a
     WARNING; it also blocks the upgrade; unset: a new store gets 5.26.32, an existing one keeps its own),
     DEEPMAP_NEO4J_AUTO_UPGRADE (0: setup never upgrades a store's Neo4j; after a rolled-back upgrade setup
     waits a week before trying that release again — neo4j-upgrade retries at once),
     DEEPMAP_MCP_START_WAIT (120s listen budget), DEEPMAP_MCP_VERIFY_DEADLINE (default: the servers'
     warm-up budget + one tool call + 30s = 390s — a cold first call).
`heal` also writes <DEEPMAP_HOME>/run/heal-<project_key>.json (see write_heal_status); a POLICY refusal
there (`refused` + `refusal_kind`: another project's store behind a mis-adopted base claim, or a legacy graph
that is not this project's — ROUND-5 G2) is final for the hooks, and heal stops this project's own
(owner-verified) MCP servers that served the refused store.

Routing (ROUND-2 D1): which Neo4j a project uses is port_registry.resolve_neo4j_route — its registered
instance, else its own instance re-found from its conf, else NO route (an unusable registry, or 7687
owned by another project), else the base 7687 as a LEGACY route that status / heal / stop / mcp-start
use only once the graph's :_DeepMapMeta owner proves it is this project's (heal never STARTS that legacy
store). Process identity (D2/E2) is pid + start stamp (Windows CreationDate; Linux boot_id + start ticks,
immune to wall-clock steps; macOS lstart in UTC): nothing is signalled that can't be proven to be the
process a pid file recorded, and a mismatch never deletes the pid file of a process that still runs the
expected command. The MCP servers get the project's SYNC root as DEEPMAP_PROJECT_ROOT (E5).
"""
from __future__ import annotations

import calendar
import csv
import hashlib
import importlib.util
import io
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Optional, Tuple

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
logging.getLogger("neo4j").setLevel(logging.ERROR)

# The Neo4j a NEW store gets: the latest 5.26 LTS patch release (5.26.0 ships netty 4.1.115; later patches fix
# CVE-2025-55163 (5.26.12) and CVE-2025-58057 (5.26.13); 5.26.32 ships netty 4.1.138). An EXISTING store keeps the
# version it runs (port_registry.store_neo4j_version — sticky) until upgrade_neo4j moves it.
DEFAULT_VERSION = "5.26.32"
PREPIN_VERSION = "5.26.0"           # what every build up to 0.29.0 provisioned (the STAGE-0 oracle's; == port_registry)
DEFAULT_URI = "bolt://127.0.0.1:7687"   # == port_registry.bolt_uri(BASE_BOLT) — ROUND-8 J4 (gate-locked)
DEFAULT_USER = "neo4j"
DEFAULT_PASSWORD = "deepmap"
MIN_PW_LEN = 4                      # < len("deepmap")==7, so the dev password is accepted (FD-1.6)
DIST_BASE = "https://dist.neo4j.org"

# Pinned, out-of-band SHA-256 checksums for the supported Neo4j distributions (supply-chain
# integrity — the deliberate hardening pass docs/known-limitations.md called for). These were
# fetched ONCE over HTTPS (TLS-authenticated) from the official dist host and baked into source, so
# EVERY future download is verified against a version-controlled, code-reviewed hash — not a
# same-host hash that an archive-tamperer would also control. Keyed by the exact dist filename.
# Precedence at use: the operator's out-of-band DEEPMAP_NEO4J_SHA256 override > this table > None
# (an UNKNOWN version warns + falls back to the structural check; it never silently hard-fails).
# 5.26.32 (2026-10-22): each archive downloaded over HTTPS, hashed locally, and equal to Neo4j's own published
# <archive>.sha256 on dist.neo4j.org. 5.26.0 stays pinned: a store created with it keeps running it (sticky).
KNOWN_SHA256 = {
    "neo4j-community-5.26.0-unix.tar.gz": "ad8ac3398606145502b8f489530bbd39333707ae4668156af16b6086b5b037d7",
    "neo4j-community-5.26.0-windows.zip": "ea9c98111b72310dab8ae00b7ae4d43df59a1ea82a490579b886bffb3e30de8f",
    "neo4j-community-5.26.32-unix.tar.gz": "d279ae6a9b38034280681a558b0cd222dce89f87cc3d9867f1f7bd28ef5ebc30",
    "neo4j-community-5.26.32-windows.zip": "00eca54f38f601b3adb596611505280a4784d9726c6df5f0b72b7a4a04cc87ec",
}

# Marker so the appended config block is written exactly once (idempotent re-runs).
CONF_MARKER = "DeepMap provisioner settings"
# Fences for the per-INSTANCE managed conf region (content-addressed, rewritten when ports/heap change
# — unlike the legacy single-instance append-once block above). START..END is DeepMap's sole authority
# over the keys it manages, so strict config validation never sees a duplicate active key.
CONF_BLOCK_START = f"# ===== {CONF_MARKER} START (auto; do not edit inside) ====="
CONF_BLOCK_END = f"# ===== {CONF_MARKER} END ====="

# The escape hatch, captured ONCE at import: did the OPERATOR set NEO4J_URI (BYO / Aura / shared
# single-instance), vs DeepMap injecting a per-project URI later? Read this — NOT the live env — so a
# URI we injected for project A is never mistaken for an operator override when routing project B.
_USER_SET_NEO4J_URI = bool(os.environ.get("NEO4J_URI"))

# The routing verdicts the LAST apply_ports_for recorded (reset at its start): why THIS project could
# NOT be routed to a Neo4j instance / an MCP port pair of its OWN — None when it was. The env
# (NEO4J_URI / DEEPMAP_*_PORT) cannot say "no route", so every verb that touches Neo4j or starts servers
# checks these first (neo4j_route_problem() / mcp_route_problem()). A project with no route gets an
# UNCONNECTABLE NEO4J_URI, so a consumer that ignores the verdict fails closed ("connection refused")
# instead of silently reading another project's graph on 7687 (2026-09 live validation, #46/#66).
_NEO4J_ROUTE_PROBLEM: str | None = None
_MCP_ROUTE_PROBLEM: str | None = None
_NO_ROUTE_URI = "bolt://127.0.0.1:0"    # == port_registry.bolt_uri(0): refused on IPv4 too (ROUND-8 J4)
# ROUND-8 J4: the spelling DeepMap builds <= v0.27.x GENERATED (bolt://localhost:<port>). Every URI this build
# generates dials 127.0.0.1 (_bolt_uri) — the address the instance binds; `localhost` resolves to ::1 first on
# Windows (~2 s per new connection, and a [::1] listener could answer instead). Used ONLY to recognise / clean
# up what an older build left behind (a config fingerprint, sync's per-URI lock file), never to dial.
_LEGACY_BOLT_URI_FMT = "bolt://localhost:{}"
# HOW the last apply_ports_for routed its project's Neo4j (ROUND-2 D1 — port_registry.resolve_neo4j_route
# is the one rule): "registry" | "instance-conf" | "legacy-unverified" | "env" (the operator escape hatch)
# | "none" (no route) | None (not resolved / a stubbed seam). "legacy-unverified" is the base 7687 of a
# project with no instance of its own: every verb verifies the graph's owner before using it.
_NEO4J_ROUTE_SOURCE: str | None = None


# ---- pure helpers (no Neo4j, no network — unit-testable) --------------------
def is_windows() -> bool:
    return platform.system() == "Windows"


# Windows creation flags for the long-lived background processes (Neo4j, the MCP servers): a NEW HIDDEN
# console (CREATE_NO_WINDOW) in its own process group (CREATE_NEW_PROCESS_GROUP, so a Ctrl+C in the
# caller's console never reaches it). NOT DETACHED_PROCESS: a detached process has no console at all, so
# every console program IT starts (cmd -> java, and the git / tasklist / powershell probes) gets a brand
# new VISIBLE console window — a Windows Terminal window flashing up per call. Children of a
# CREATE_NO_WINDOW process inherit its hidden console instead. Both flavours outlive their parent.
WINDOWS_BACKGROUND_FLAGS = 0x08000000 | 0x00000200   # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP


def deepmap_home() -> Path:
    return Path(os.environ.get("DEEPMAP_HOME", str(Path.home() / ".deepmap")))


_NEO4J_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


def neo4j_version() -> str:
    """The Neo4j version a NEW store gets — the one to fetch: NEO4J_VERSION, else DEFAULT_VERSION. A version string
    flows into BOTH the download URL and the cache filesystem path, so an unvalidated env value is a path-traversal /
    URL-injection vector — refuse anything that is not a clean X.Y.Z and fall back to the validated default (bug-hunt
    #3). An EXISTING store's version is store_neo4j_version (sticky) — what paths() and ensure_neo4j use."""
    v = os.environ.get("NEO4J_VERSION", DEFAULT_VERSION)
    if not _NEO4J_VERSION_RE.match(v):
        print(f"[provision] WARNING: NEO4J_VERSION={v!r} is not a valid X.Y.Z version — refusing it "
              f"(it flows into the download URL + cache path); using the default {DEFAULT_VERSION}.",
              file=sys.stderr)
        return DEFAULT_VERSION
    return v


def store_neo4j_version(project_key: str | None = None) -> str:
    """The Neo4j version THIS store runs — the base / legacy store's (project_key None) or per-project instance
    `project_key`'s: port_registry.store_neo4j_version, THE rule (NEO4J_VERSION, unless it would downgrade or orphan
    an existing store; an upgrade in progress; the version the store was created with or upgraded to; where no store
    exists yet, the default). A newer DEFAULT_VERSION
    therefore never moves an existing store — heal, the hooks and every verb revive the version it runs; only
    upgrade_neo4j changes it. neo4j_version() (env / default) only if port_registry can't be imported. Never raises."""
    try:
        import port_registry as PR
        v = PR.store_neo4j_version(project_key)
        if _NEO4J_VERSION_RE.match(str(v)):
            return str(v)
    except Exception:
        pass
    return neo4j_version()


def dist_filename(version: str, windows: bool) -> str:
    """The Neo4j distribution archive name for a platform."""
    return (f"neo4j-community-{version}-windows.zip" if windows
            else f"neo4j-community-{version}-unix.tar.gz")


def dist_url(version: str, windows: bool) -> str:
    return f"{DIST_BASE}/{dist_filename(version, windows)}"


def _is_sha256(s: str) -> bool:
    return len(s) == 64 and all(c in "0123456789abcdef" for c in s)


def expected_sha256(version: str, windows: bool) -> Optional[str]:
    """The TRUSTED SHA-256 for this distribution, or None if unpinned. Precedence: the operator's
    out-of-band DEEPMAP_NEO4J_SHA256 override (lets a user pin a version not baked here) > the baked
    KNOWN_SHA256 table > None. A None result means 'unknown version' — the caller warns and falls
    back to the structural check; it does NOT hard-fail (so a future Neo4j version still provisions).

    A *malformed* override (not a 64-hex SHA-256) is NEVER silently honored: it is loudly ignored
    (warn to stderr) and we fall back to the baked table — so an env typo can neither be treated as a
    valid pin nor quietly downgrade integrity to a wrong value."""
    override = os.environ.get("DEEPMAP_NEO4J_SHA256", "").strip().lower()
    if override:
        if _is_sha256(override):
            return override
        print(f"[provision] WARNING: DEEPMAP_NEO4J_SHA256={override!r} is not a 64-hex SHA-256 — "
              f"ignoring it and falling back to the baked checksum.", file=sys.stderr)
    return KNOWN_SHA256.get(dist_filename(version, windows))


def _sha256(path: Path) -> str:
    """Stream the file through SHA-256 (constant memory — the archive is ~180 MB)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_checksum(path: Path, expected: Optional[str]) -> Tuple[bool, Optional[str]]:
    """Return (ok, actual_hash). ok is True when NO hash is pinned (unknown version — the caller
    warns) OR the file's SHA-256 matches the pinned hash. A pinned-but-mismatched hash is a HARD
    integrity failure (ok=False) — a tampered/corrupt/wrong-version download must never be used."""
    if not expected:
        return True, None
    actual = _sha256(path)
    return (actual.lower() == expected.lower()), actual


# ---- bounded memory: EVERY DeepMap Neo4j (each per-project instance AND the base / legacy store) --------------
DEFAULT_HEAP_MAX = "512m"   # the heap cap (env DEEPMAP_NEO4J_HEAP_MAX) — docs/known-limitations.md's "capped at 512m"
_HEAP_INITIAL = "256m"
_PAGECACHE = "192m"
# Neo4j's own byte-size units (org.neo4j.io.ByteUnit; all binary: K = KiB) -> the power of 1024. A value with any
# other unit fails Neo4j 5's strict config validation, and the JVM then never starts.
_NEO4J_BYTE_UNITS = {"B": 0, "KiB": 1, "KB": 1, "K": 1, "kB": 1, "kb": 1, "k": 1, "MiB": 2, "MB": 2, "M": 2,
                     "mB": 2, "mb": 2, "m": 2, "GiB": 3, "GB": 3, "G": 3, "gB": 3, "gb": 3, "g": 3,
                     "TiB": 4, "TB": 4, "PiB": 5, "PB": 5, "EiB": 6, "EB": 6}


def _byte_size(value):
    """(the conf spelling, bytes) of a byte-size setting — digits, optional blanks, then nothing or one of Neo4j's own
    units (the blanks dropped: '768 MiB' -> '768MiB') — or None for anything Neo4j would refuse (a stray unit, a sign,
    a second line). Never raises."""
    try:
        m = re.fullmatch(r"(\d+)[ \t]*([A-Za-z]*)", str(value).strip())
        if not m or (m.group(2) and m.group(2) not in _NEO4J_BYTE_UNITS):
            return None
        return (m.group(1) + m.group(2), int(m.group(1)) * 1024 ** _NEO4J_BYTE_UNITS.get(m.group(2), 0))
    except Exception:
        return None


def _heap_max_setting(heap_max=None) -> str:
    """The heap cap a DeepMap Neo4j runs with: `heap_max`, else DEEPMAP_NEO4J_HEAP_MAX, else 512m. A value Neo4j could
    not parse is REFUSED with a warning and the default used — it would otherwise stop that Neo4j from starting (since
    ISS-build:020 the base store's too), and a second line in it could inject a conf key. Never raises."""
    raw = heap_max if heap_max else os.environ.get("DEEPMAP_NEO4J_HEAP_MAX", "")
    val = str(raw or "").strip()
    if not val:
        return DEFAULT_HEAP_MAX
    got = _byte_size(val)
    if got is None or got[1] <= 0:
        print(f"[provision] WARNING: the Neo4j heap cap {val!r} (DEEPMAP_NEO4J_HEAP_MAX) is not a byte size Neo4j "
              f"accepts (e.g. 512m, 1g, 768MiB) — using {DEFAULT_HEAP_MAX}.", file=sys.stderr)
        return DEFAULT_HEAP_MAX
    return got[0]


def _memory_conf_lines(heap_max=None, *, comment: str = "# Bounded memory (override DEEPMAP_NEO4J_HEAP_MAX)") -> str:
    """The bounded-memory conf lines EVERY DeepMap Neo4j gets — each per-project instance (conf_instance_block) and,
    since ISS-build:020, the base / legacy store (conf_block): the heap capped at _heap_max_setting (default 512m), an
    initial heap of 256m — never above the cap (the JVM refuses to start with initial > max, e.g. a 128m cap) — and a
    192m page cache. Pure but for the one warning _heap_max_setting may print."""
    cap = _heap_max_setting(heap_max)
    init = _HEAP_INITIAL if _byte_size(cap)[1] >= _byte_size(_HEAP_INITIAL)[1] else cap
    return (f"{comment}\n"
            f"server.memory.heap.initial_size={init}\n"
            f"server.memory.heap.max_size={cap}\n"
            f"server.memory.pagecache.size={_PAGECACHE}\n")


def conf_block(min_pw_len: int = MIN_PW_LEN, heap_max: str | None = None) -> str:
    """The exact neo4j.conf block the provisioner writes into the LEGACY / base store's conf (the shipped conf dir
    the base Neo4j runs from — NEO4J_CONF is not set for it): both STAGE-0 fixes plus, since ISS-build:020, the
    bounded memory every per-project instance gets (_memory_conf_lines) — the base store (every machine's first
    project) used to keep Neo4j's default heap, so DEEPMAP_NEO4J_HEAP_MAX and the documented 512m cap did nothing
    there. configure_neo4j keeps this block the SOLE definition of its keys (_legacy_conf_desired).

    Every key is validated against Neo4j 5.26's strict config validation (an unknown key blocks
    startup); each was checked against the settings the Neo4j 5.26 jars declare.
    """
    return (
        f"\n# ===== {CONF_MARKER} (auto-added) =====\n"
        "# FD-1.6: relax min password length so the DeepMap dev password 'deepmap' (7 chars) is accepted\n"
        f"dbms.security.auth_minimum_password_length={min_pw_len}\n"
        "# TELEMETRY-1: zero-telemetry / air-gap — disable Neo4j anonymous usage + client telemetry\n"
        "dbms.usage_report.enabled=false\n"
        "client.allow_telemetry=false\n"
        + _memory_conf_lines(heap_max)
    )


def _conf_active_keys(text: str) -> set:
    """The keys the UNCOMMENTED `key=value` lines of `text` set — what Neo4j's strict validator sees."""
    keys = set()
    for line in text.splitlines():
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            keys.add(s.split("=", 1)[0].strip())
    return keys


def _legacy_conf_desired(text: str, block: str) -> str:
    """The legacy / base-store conf with `block` (conf_block) as the SOLE definition of every key it sets: each earlier
    copy of DeepMap's block (its own comment lines — the header included, whichever build wrote it) and each ACTIVE line
    setting one of the block's keys is dropped from `text`, and `block` goes at the end. Content-addressed — applying it
    to its own result changes nothing — so a re-run writes only when the block changed (e.g. DEEPMAP_NEO4J_HEAP_MAX),
    and no key is ever active twice: Neo4j 5's strict validation refuses a duplicate (the old append-once block
    duplicated any of its keys the conf already set). Pure (ISS-build:020)."""
    keys = _conf_active_keys(block)
    own = {ln.strip() for ln in block.splitlines() if ln.strip().startswith("#")}
    kept = []
    for line in text.splitlines():
        s = line.strip()
        if s in own:
            continue
        if s and not s.startswith("#") and "=" in s and s.split("=", 1)[0].strip() in keys:
            continue
        kept.append(line)
    base = "\n".join(kept).rstrip("\n")
    return f"{base}\n{block}" if base.strip() else block.lstrip("\n")


# ---- per-instance conf (per-project isolation): one binary, N NEO4J_CONF dirs -----------------
# Every key DeepMap writes into a per-instance neo4j.conf. Used both to EMIT the managed block and to
# STRIP any duplicate from the shipped conf — Neo4j 5.x strict validation refuses a duplicate active
# key (the shipped 5.26 conf ships `server.https.enabled=false` UNCOMMENTED, so this is load-bearing,
# not insurance).
_MANAGED_CONF_KEYS = (
    "dbms.security.auth_minimum_password_length", "dbms.usage_report.enabled", "client.allow_telemetry",
    "server.directories.data", "server.directories.logs", "server.directories.run",
    "server.bolt.listen_address", "server.bolt.advertised_address",
    "server.http.listen_address", "server.http.advertised_address", "server.https.enabled",
    "server.memory.heap.initial_size", "server.memory.heap.max_size", "server.memory.pagecache.size",
)


def _conf_fwd(p) -> str:
    """neo4j.conf wants forward slashes even on Windows (a backslash is an escape there)."""
    return str(p).replace("\\", "/")


def conf_instance_block(data, logs, run, bolt, http, *, heap_max=None, min_pw_len: int = MIN_PW_LEN) -> str:
    """The complete managed conf body for ONE per-project instance: the STAGE-0 fixes (auth length +
    telemetry off) PLUS per-instance directories, bolt/http listen addresses, and BOUNDED memory (N
    instances share the box; override the heap cap via DEEPMAP_NEO4J_HEAP_MAX — _memory_conf_lines, the
    same lines the base store's conf_block carries). Bound to 127.0.0.1 (zero-egress). Pure but for the
    warning a malformed heap cap prints; every key here is in _MANAGED_CONF_KEYS."""
    return (
        "# FD-1.6 + TELEMETRY-1 (STAGE-0 fixes)\n"
        f"dbms.security.auth_minimum_password_length={min_pw_len}\n"
        "dbms.usage_report.enabled=false\n"
        "client.allow_telemetry=false\n"
        "# Per-instance directories + ports (per-project graph isolation)\n"
        f"server.directories.data={_conf_fwd(data)}\n"
        f"server.directories.logs={_conf_fwd(logs)}\n"
        f"server.directories.run={_conf_fwd(run)}\n"
        f"server.bolt.listen_address=127.0.0.1:{bolt}\n"
        f"server.bolt.advertised_address=127.0.0.1:{bolt}\n"
        f"server.http.listen_address=127.0.0.1:{http}\n"
        f"server.http.advertised_address=127.0.0.1:{http}\n"
        "server.https.enabled=false\n"
        + _memory_conf_lines(heap_max, comment="# Bounded per-instance memory (override DEEPMAP_NEO4J_HEAP_MAX)")
    )


def _filter_managed_keys(text: str) -> str:
    """Drop any UNCOMMENTED line that sets a key DeepMap manages, so the managed block is the SOLE
    definition (strict validation refuses duplicate active keys). Commented lines are kept verbatim."""
    out = []
    for line in text.splitlines():
        s = line.lstrip()
        if s and not s.startswith("#") and "=" in s:
            key = s.split("=", 1)[0].strip()
            if key in _MANAGED_CONF_KEYS:
                continue
        out.append(line)
    return "\n".join(out)


def _strip_managed_block(text: str) -> str:
    """Remove a prior CONF_BLOCK_START..CONF_BLOCK_END managed region (inclusive) if present, so the
    per-instance rewrite is content-addressed (idempotent) rather than append-growing."""
    start = text.find(CONF_BLOCK_START)
    if start == -1:
        return text
    end = text.find(CONF_BLOCK_END, start)
    if end == -1:
        return text[:start]
    end += len(CONF_BLOCK_END)
    return text[:start] + text[end:]


def _seed_instance_conf(version: str, project_key: str) -> Path:
    """Populate a per-instance NEO4J_CONF dir from the shipped conf dir (U1: NEO4J_CONF REPLACES, not
    merges, so the dir must be complete — server-logs.xml/user-logs.xml included). Re-seeds when the
    Neo4j VERSION changed (a stale conf can refuse startup under strict validation). Creates the
    instance data/logs/run dirs. Best-effort; never raises. Returns the instance conf dir."""
    shipped = paths(version)["conf_dir"]                       # nhome/conf (the extracted defaults)
    p = paths(version, project_key)
    inst_conf = p["conf_dir"]
    stamp = inst_conf / ".seeded-version"
    fresh = True
    try:
        if inst_conf.exists() and stamp.exists() and stamp.read_text(encoding="utf-8").strip() == str(version):
            fresh = False
    except OSError:
        fresh = True
    if fresh:
        try:
            inst_conf.mkdir(parents=True, exist_ok=True)
            for f in shipped.iterdir():
                if f.is_file() and f.name != ".seeded-version":
                    shutil.copy2(str(f), str(inst_conf / f.name))
            stamp.write_text(str(version), encoding="utf-8")
        except OSError as exc:
            print(f"[provision] WARNING: could not seed instance conf {inst_conf}: {exc}", file=sys.stderr)
    for key in ("data", "logs", "run"):
        try:
            p[key].mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
    return inst_conf


def paths(version: str | None = None, project_key: str | None = None) -> dict:
    """Resolve every filesystem path the provisioner uses, for one Neo4j version.

    The extracted binary + download cache are ALWAYS shared (one install drives N instances). When
    `project_key` is None the conf/data/run/log paths are the LEGACY single-instance tree (BYTE-
    IDENTICAL to the pre-isolation behaviour — every existing caller and gate is unaffected). When a
    `project_key` is given they point at a per-instance tree under <home>/instances/<key>/ so several
    projects run their own Neo4j with their own graph + auth store + ports (per-project isolation).

    `version` None -> the version THAT store runs (store_neo4j_version: sticky — a store created with 5.26.0 keeps
    5.26.0's binary, and the base store, which lives inside its version's folder, keeps being found there)."""
    version = version or store_neo4j_version(project_key)
    home = deepmap_home()
    win = is_windows()
    nhome = home / "neo4j" / f"neo4j-community-{version}"   # SHARED binary
    bindir = nhome / "bin"
    if project_key is None:                                 # LEGACY / single-instance (unchanged)
        conf_dir = nhome / "conf"
        data, logs, run = nhome / "data", nhome / "logs", nhome / "run"
        log_file = home / "neo4j-console.log"
    else:                                                   # per-project instance
        inst = home / "instances" / project_key
        conf_dir = inst / "conf"
        data, logs, run = inst / "data", inst / "logs", inst / "run"
        log_file = inst / "neo4j-console.log"
    return {
        "home": home,
        "cache": home / "cache",
        "zip": home / "cache" / dist_filename(version, win),
        "neo4j_home": nhome,                               # SHARED
        "conf_dir": conf_dir,                              # the NEO4J_CONF directory
        "conf": conf_dir / "neo4j.conf",
        "neo4j_bin": bindir / ("neo4j.bat" if win else "neo4j"),
        "admin_bin": bindir / ("neo4j-admin.bat" if win else "neo4j-admin"),
        "data": data,
        "logs": logs,
        "run": run,
        "databases": data / "databases",
        "pidfile": run / "neo4j.pid",
        "log": log_file,
    }


# ---- Neo4j lifecycle --------------------------------------------------------
def _make_driver(uri: str, user: str, password: str):
    from neo4j import GraphDatabase
    return GraphDatabase.driver(uri, auth=(user, password),
                                notifications_min_severity="OFF")


def _missing_modules(names) -> list:
    """The modules in `names` this interpreter cannot import (none found). One already loaded, or one whose lookup
    fails some other way, counts as present: a doubt never blocks. Never raises."""
    out = []
    for name in names:
        if sys.modules.get(name) is not None:
            continue
        try:
            if importlib.util.find_spec(name) is None:
                out.append(name)
        except ModuleNotFoundError:
            out.append(name)
        except Exception:
            pass
    return out


def _missing_packages_text(absent) -> str:
    """The one line for "this Python lacks DeepMap's packages" (setup, ensure, heal) — never "Neo4j is down"."""
    try:
        import deps_bootstrap as DB
        where, optout = DB.venv_dir(), DB.opted_out(os.environ)
    except Exception:
        where, optout = None, False
    if optout:
        how = ("DEEPMAP_MANAGED_VENV=0 is set, so setup does not install them: install them into this Python "
               "(install.sh / install.ps1), or unset it and re-run /deep-map:setup")
    else:
        how = (f"/deep-map:setup installs them into DeepMap's own environment{f' ({where})' if where else ''} and "
               "runs from there: re-run it (it repairs an incomplete environment)")
    return (f"DeepMap's Python packages are missing from this Python ({sys.executable}): {', '.join(absent)} not "
            f"installed. {how}")


def _is_rate_limited(exc) -> bool:
    """True iff `exc` is Neo4j's temporary login lock after repeated failed authentications
    (Neo.ClientError.Security.AuthenticationRateLimit — the driver raises a plain ClientError for it)."""
    code = str(getattr(exc, "code", "") or "")
    return "AuthenticationRateLimit" in code or "too many times in a row" in str(exc).lower()


def _is_auth_error(exc) -> bool:
    """True iff `exc` is Neo4j REJECTING the credentials (an instance answered — it is not down),
    including its temporary lock after repeated failed logins."""
    try:
        from neo4j.exceptions import AuthError
        if isinstance(exc, AuthError):
            return True
    except Exception:
        pass
    return ("AuthError" in type(exc).__name__ or str(getattr(exc, "code", "") or "").endswith("Unauthorized")
            or _is_rate_limited(exc))


_AUTH_LOCK_WAIT_S = 6.0   # Neo4j's default dbms.security.auth_lock_time is 5 s
_LAST_AUTH_RATE_LIMITED = False   # the last _bolt_state 'auth' was Neo4j's login LOCK, not a plain rejection


def _bolt_state(uri: str, user: str, password: str, timeout: float = 4.0) -> str:
    """'up' (reachable with these credentials) | 'auth' (a Neo4j ANSWERED but rejected the credentials —
    it is running; the password is wrong, or the store was initialised with other credentials, or another
    program's Neo4j holds the port) | 'down' (nothing usable answered). Lets every message tell "wrong
    password" from "not running" (#84: an AuthError used to read as "unreachable" / "port in use").

    A Neo4j that is LOCKING logins after repeated failures (e.g. an MCP server started with a wrong
    password keeps retrying) is running: the lock is waited out (twice, _AUTH_LOCK_WAIT_S each) and the
    credentials re-tried — the live-found case where heal read such an instance as "down", tried to
    provision a fresh one and failed preflight on its own ports. An 'auth' answer records whether the lock
    was still on (_LAST_AUTH_RATE_LIMITED — the auth texts name it). Never raises."""
    global _LAST_AUTH_RATE_LIMITED
    _LAST_AUTH_RATE_LIMITED = False
    for attempt in range(3):
        try:
            d = _make_driver(uri, user, password)
            try:
                d.verify_connectivity()
                return "up"
            finally:
                d.close()
        except Exception as exc:
            if _is_rate_limited(exc) and attempt < 2:
                time.sleep(_AUTH_LOCK_WAIT_S)
                continue
            _LAST_AUTH_RATE_LIMITED = _is_rate_limited(exc)
            return "auth" if _is_auth_error(exc) else "down"
    _LAST_AUTH_RATE_LIMITED = True
    return "auth"


def is_bolt_up(uri: str, user: str, password: str, timeout: float = 4.0) -> bool:
    """True iff a Neo4j is reachable on `uri` with these credentials."""
    return _bolt_state(uri, user, password, timeout) == "up"


def _is_loopback(host) -> bool:
    """THE loopback-host rule — port_registry.is_loopback_host, the one sync, the CLI and the hooks apply too: localhost,
    127.0.0.1, ::1 and its expanded form 0:0:0:0:0:0:0:1, bracketed or not, any case (C11: provision's three private
    copies left the expanded form out). Never raises."""
    try:
        import port_registry as PR
        return PR.is_loopback_host(host)
    except Exception:
        return False


def _is_local_base_uri(uri) -> bool:
    """True iff `uri` is the local base bolt 7687 (the legacy / first-project store). Never raises."""
    try:
        from urllib.parse import urlparse
        u = urlparse(str(uri))
        return _is_loopback(u.hostname) and (u.port or 7687) == 7687
    except Exception:
        return False


def _bolt_uri(port) -> str:
    """bolt://127.0.0.1:<port> — THE form of every bolt URI provision generates (ROUND-8 J4, port_registry.bolt_uri):
    the per-project instance binds 127.0.0.1 only, and `localhost` resolves to ::1 first on Windows (a refused IPv6
    connect cost ~2 s on EVERY new connection; a [::1] listener — Docker publishes one on 7687 — could answer instead).
    An operator's NEO4J_URI is never rebuilt through this (it is used verbatim)."""
    try:
        import port_registry as PR
        return PR.bolt_uri(port)
    except Exception:
        return f"bolt://127.0.0.1:{int(port)}"


def _canon_uri(uri):
    """`uri` in the form used to COMPARE / fingerprint bolt URIs (port_registry.canonical_bolt_uri): a loopback host
    spelled 127.0.0.1, the default port explicit — so bolt://localhost:7689 (what builds <= v0.27.x generated) and
    bolt://127.0.0.1:7689 are ONE endpoint. The URI unchanged when it can't be normalized. Never raises."""
    try:
        import port_registry as PR
        return PR.canonical_bolt_uri(uri) or uri
    except Exception:
        return uri


def _uri_spellings(uri) -> list:
    """Every spelling an older record of `uri`'s endpoint may carry: the canonical form (this build), `uri` as given
    and, for a loopback endpoint, the bolt://localhost:<port> form builds <= v0.27.x generated. Never raises."""
    out: list = []
    try:
        for u in (_canon_uri(uri), uri):
            if u is not None and u not in out:
                out.append(u)
        c = str(_canon_uri(uri) or "")
        m = re.match(r"^([a-z][a-z0-9+.-]*)://127\.0\.0\.1(:\d+)?(.*)$", c)
        if m:
            legacy = f"{m.group(1)}://localhost{m.group(2) or ''}{m.group(3)}"
            if legacy not in out:
                out.append(legacy)
    except Exception:
        pass
    return out or [uri]


_PW_NOTE = "check NEO4J_PASSWORD (DeepMap's default is 'deepmap')"
_DEFAULT_CREDS_NOTE = ("If an earlier setup failed at set-initial-password, the store was initialised with "
                       "Neo4j's default credentials")


def _auth_failed_text(uri: str, project_key: str | None = None, *, rate_limited: bool | None = None) -> str:
    """The actionable message for a Neo4j that is UP but rejected our credentials — or is temporarily
    LOCKING our logins after repeated failures (Neo.ClientError.Security.AuthenticationRateLimit;
    `rate_limited`, default: what the last _bolt_state saw). ROUTE-AWARE and worded like the CLI's
    _auth_hint (deepmap_cli), and RUNNABLE in the state it names (ROUND-2 D6):
      * operator NEO4J_URI (escape hatch) -> that endpoint's own credentials; DeepMap does not manage its store;
      * LEGACY route (no instance of its own) -> most likely another program's Neo4j on the shared port:
        /deep-map:setup gives this project its own instance (never a store deletion);
      * a per-project instance -> `instance-rm --key <key> --yes` + /deep-map:setup;
      * the base 7687 store (never an instance-rm target) -> stop + move its data directory aside + setup."""
    if rate_limited is None:
        rate_limited = _LAST_AUTH_RATE_LIMITED
    if rate_limited:
        head = (f"Neo4j at {uri} is running but is temporarily LOCKING DeepMap's logins after too many failed "
                "attempts in a row (Neo.ClientError.Security.AuthenticationRateLimit) — something keeps logging in "
                "with wrong credentials (often an MCP server or a hook started with an older NEO4J_PASSWORD); wait "
                "a few seconds and retry.")
    else:
        head = f"Neo4j at {uri} is running but REJECTED DeepMap's credentials (AuthError)."
    if _USER_SET_NEO4J_URI:
        fix = (" Check NEO4J_USER / NEO4J_PASSWORD for the Neo4j your NEO4J_URI points at — an operator-managed "
               "endpoint whose store DeepMap does not manage (unset NEO4J_URI and run /deep-map:setup to give this "
               "project its own instance instead).")
    elif _NEO4J_ROUTE_SOURCE == "legacy-unverified":
        fix = (" This project has no DeepMap instance of its own, so the legacy shared port was tried — and the "
               "Neo4j listening there rejected DeepMap's credentials. If it is DeepMap's own pre-isolation store, "
               f"{_PW_NOTE}; otherwise it is most likely another program's Neo4j (e.g. a Docker container), not "
               "DeepMap's — run /deep-map:setup in this project to give it its own instance.")
    elif project_key:
        fix = (f" {_PW_NOTE[0].upper()}{_PW_NOTE[1:]}. {_DEFAULT_CREDS_NOTE}: remove this project's instance with "
               f"{_provision_cmd(f'instance-rm --key {project_key} --yes')} and re-run /deep-map:setup (the graph "
               "is rebuilt from source).")
    elif _is_local_base_uri(uri):
        fix = (f" {_PW_NOTE[0].upper()}{_PW_NOTE[1:]}. {_DEFAULT_CREDS_NOTE}: stop Neo4j with "
               f"{_provision_cmd('stop')} (from this project's folder), move the base store's data directory "
               f"{paths(None)['data']} aside, then re-run /deep-map:setup (it initialises a fresh store with "
               "DeepMap's password; the graph is rebuilt from source). Or another program holds bolt 7687 with "
               "its own credentials.")
    else:
        fix = (" Check NEO4J_USER / NEO4J_PASSWORD — that endpoint is not a DeepMap-managed instance: use the "
               "credentials it was set up with.")
    return head + fix


def _auth_failed_reason(uri: str, *, rate_limited: bool | None = None) -> str:
    """The SHORT form of _auth_failed_text for the heal status file (the SessionStart notify line): one
    line (< 300 chars) that says "credentials" (the hooks key on it) and points at the full remedy."""
    if rate_limited is None:
        rate_limited = _LAST_AUTH_RATE_LIMITED
    if rate_limited:
        return (f"the Neo4j at {uri} is temporarily locking DeepMap's logins after too many failed attempts "
                "(wrong credentials somewhere — often a server started with an older NEO4J_PASSWORD) — check "
                "NEO4J_PASSWORD (the full remedy: /deep-map:status)")
    if _NEO4J_ROUTE_SOURCE == "legacy-unverified" and not _USER_SET_NEO4J_URI:
        return (f"the Neo4j at {uri} (the shared legacy port — this project has no instance of its own) rejected "
                "DeepMap's credentials — run /deep-map:setup to give this project its own instance")
    return (f"the Neo4j at {uri} is running but rejected DeepMap's credentials — set NEO4J_PASSWORD to its "
            "password (the full remedy: /deep-map:status)")


def _provision_cmd(verb: str) -> str:
    """An absolute, copy-pasteable provision command for a hint (a user's project has no build/ dir, so a
    relative `python build/provision.py` hint can't be run from there)."""
    return f'`"{sys.executable}" "{HERE / "provision.py"}" {verb}`'


def download_neo4j(version: str, dest_zip: Path, *, force: bool = False) -> Path:
    """Download (once) the Neo4j distribution to `dest_zip`; verify it is a STRUCTURALLY-valid archive
    AND (when the version is pinned) that its SHA-256 matches the trusted, baked checksum. Cached
    re-runs skip the network but are STILL checksum-verified (a tampered cache is caught). A pinned
    checksum mismatch FAILS LOUD; an unpinned version warns and falls back to the structural check."""
    dest_zip.parent.mkdir(parents=True, exist_ok=True)
    exp = expected_sha256(version, is_windows())
    if dest_zip.exists() and not force and dest_zip.stat().st_size > 1_000_000:
        ok_struct = _archive_ok(dest_zip)
        ok_sum, actual = verify_checksum(dest_zip, exp)
        if ok_struct and ok_sum:
            print(f"[provision] cache hit: {dest_zip.name}"
                  + (" (SHA-256 verified)" if exp else " (structural check only)"))
            if not exp:
                print(f"[provision] WARNING: no pinned SHA-256 for {dist_filename(version, is_windows())} — "
                      f"structural check only. Pin out-of-band via DEEPMAP_NEO4J_SHA256 to verify integrity.",
                      file=sys.stderr)
            return dest_zip
        reason = "corrupt archive" if not ok_struct else f"SHA-256 mismatch (got {actual})"
        print(f"[provision] cached {reason}, re-downloading: {dest_zip.name}")
    url = dist_url(version, is_windows())
    print(f"[provision] downloading {url}")
    # UNIQUE per-process temp (was a shared `<name>.part`) so two concurrent first-setups that both
    # miss the download lock can never write the SAME partial file — each streams to its own temp and
    # atomically replaces dest_zip; the checksum/structural gate below rejects any bad result. mkstemp
    # lands in the cache dir (same volume -> atomic os.replace).
    fd, tmp = tempfile.mkstemp(dir=str(dest_zip.parent), prefix=dest_zip.name + ".", suffix=".part")
    os.close(fd)
    part = Path(tmp)
    try:
        with urllib.request.urlopen(url, timeout=600) as r, open(part, "wb") as f:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
    except BaseException:
        # A mid-stream network/IO error, a timeout, or an interrupt must never leave a stale .part
        # orphaned in the cache (it would be invisible to the next run, which only inspects dest_zip).
        part.unlink(missing_ok=True)
        raise
    if not _archive_ok(part):
        part.unlink(missing_ok=True)
        raise RuntimeError(f"downloaded archive failed integrity check: {url}")
    ok_sum, actual = verify_checksum(part, exp)
    if not ok_sum:
        part.unlink(missing_ok=True)
        raise RuntimeError(
            f"SHA-256 mismatch for {url}: expected {exp}, got {actual} — refusing to use a "
            f"tampered/corrupt/wrong-version download (supply-chain integrity).")
    part.replace(dest_zip)
    if exp:
        print(f"[provision] downloaded {dest_zip.stat().st_size} bytes -> {dest_zip.name} (SHA-256 verified)")
    else:
        print(f"[provision] downloaded {dest_zip.stat().st_size} bytes -> {dest_zip.name}", file=sys.stdout)
        print(f"[provision] WARNING: no pinned SHA-256 for {dist_filename(version, is_windows())} — "
              f"structural check only. Pin out-of-band via DEEPMAP_NEO4J_SHA256 to verify integrity.",
              file=sys.stderr)
    return dest_zip


def _archive_ok(p: Path) -> bool:
    """True iff `p` is a complete archive: a zip whose every member passes its CRC check, else a readable tar. Zip or
    tar is told by CONTENT: a download is checked under a `.part` temp name (`<name>.part` from 0.2.0,
    `<name>.<random>.part` since 0.26.0), so a name test read every fresh Windows zip as a tar and refused it ("failed
    integrity check", 0.2.0 to 0.28.2)."""
    try:
        if zipfile.is_zipfile(p):
            with zipfile.ZipFile(p) as z:
                return z.testzip() is None
        if p.name.lower().endswith(".zip"):
            return False   # named a zip, but not one
        import tarfile
        with tarfile.open(p) as t:
            t.getmembers()
        return True
    except Exception:
        return False


def _within(target: Path, name: str) -> bool:
    """True iff archive member `name` resolves to a path inside `target` (no path traversal).
    Guards against zip-slip / tar-slip (CWE-22): a member like '../../x' or an absolute path
    would otherwise let extractall() write outside the extraction dir."""
    dest = (target / name).resolve()
    return dest == target or target in dest.parents


def extract_neo4j(archive: Path, version: str) -> Path:
    """Extract the distribution into <home>/neo4j/. Idempotent (skips if already extracted).

    Extraction is path-traversal-safe: every member (and any tar symlink/hardlink target) is
    validated to resolve inside the extraction dir before extractall() runs (defensive hardening —
    even though the archive is the integrity-checked official Neo4j download).
    """
    p = paths(version)
    # Idempotency gates on a COMPLETION SENTINEL written as the LAST extract step (not on the mere
    # presence of one binary) — an extraction interrupted after bin/ but before lib/ would otherwise
    # pass this check and leave a corrupt, unlaunchable neo4j_home (audit/bug-hunt #2).
    sentinel = p["neo4j_home"] / ".deepmap-extracted"
    if sentinel.exists():
        print(f"[provision] already extracted: {p['neo4j_home'].name}")
        return p["neo4j_home"]
    # Back-compat: a complete install from before the sentinel existed — adopt it (write the sentinel)
    # rather than needlessly re-extracting ~180 MB.
    if (p["neo4j_bin"].exists() and (p["neo4j_home"] / "lib").is_dir()
            and (p["neo4j_home"] / "conf").is_dir()):
        sentinel.write_text(version, encoding="utf-8")
        print(f"[provision] already extracted (adopted complete install): {p['neo4j_home'].name}")
        return p["neo4j_home"]
    target = (p["home"] / "neo4j")
    target.mkdir(parents=True, exist_ok=True)
    target = target.resolve()
    print(f"[provision] extracting {archive.name} -> {target}")
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            for name in z.namelist():
                if not _within(target, name):
                    raise RuntimeError(f"refusing unsafe archive member (path traversal): {name}")
            z.extractall(target)
    else:
        import tarfile
        with tarfile.open(archive) as t:
            for m in t.getmembers():
                if not _within(target, m.name):
                    raise RuntimeError(f"refusing unsafe archive member (path traversal): {m.name}")
                if m.issym() or m.islnk():
                    link_dest = ((target / m.name).parent / m.linkname).resolve()
                    if not (link_dest == target or target in link_dest.parents):
                        raise RuntimeError(f"refusing unsafe archive link: {m.name} -> {m.linkname}")
            # filter='data' (Python 3.12+) is belt-and-suspenders with the _within checks above and
            # silences the 3.12+ "extractall without filter" DeprecationWarning. Guarded for 3.10/3.11.
            if sys.version_info >= (3, 12):
                t.extractall(target, filter="data")
            else:
                t.extractall(target)
    # Write the completion sentinel LAST — only a fully-extracted tree is treated as "done" (#2).
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_text(version, encoding="utf-8")
    return p["neo4j_home"]


def _atomic_write_conf(conf: Path, new_txt: str) -> bool:
    """Atomic conf write (unique temp + os.replace in the same dir). Best-effort diagnostic on an
    OSError (returns False), never a raw traceback. Shared by the legacy + per-instance paths."""
    try:
        fd, tmp = tempfile.mkstemp(dir=str(conf.parent), prefix=".deepmap-conf-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(new_txt)
            os.replace(tmp, conf)   # atomic same-volume swap (MoveFileEx REPLACE_EXISTING on Windows)
            tmp = None
        finally:
            if tmp is not None and os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
        return True
    except OSError as exc:
        print(f"[provision] ERROR: could not write Neo4j conf at {conf}: {exc}", file=sys.stderr)
        return False


def configure_neo4j(version: str, *, project_key: str | None = None, bolt: int | None = None,
                    http: int | None = None, heap_max: str | None = None,
                    min_pw_len: int = MIN_PW_LEN) -> bool:
    """Write DeepMap's Neo4j conf. Returns True iff the conf changed.

    project_key None (LEGACY / base store — the first project's Neo4j, which runs from the shipped conf dir):
    a CONTENT-ADDRESSED rewrite of that conf with conf_block — the FD-1.6 + TELEMETRY-1 fixes AND, since
    ISS-build:020, the bounded memory every instance has (heap cap DEEPMAP_NEO4J_HEAP_MAX, default 512m) —
    as the sole definition of its keys (_legacy_conf_desired: an older build's append-once block and any
    active duplicate are replaced, never appended to). A no-op when nothing changed; a heap change is picked
    up on the next start. Atomic; never a raw traceback.

    project_key set (per-project instance): seed the per-instance NEO4J_CONF dir (once / on a version
    bump), then CONTENT-ADDRESSED rewrite `<conf_dir>/neo4j.conf` = the shipped conf (with the managed
    keys stripped) + a fenced managed block carrying the per-instance dirs/ports/memory. Re-runs are a
    no-op when nothing changed, but a PORT/heap change is picked up (unlike a marker-once skip that
    would strand an instance on stale ports). Atomic (temp + os.replace); never a raw traceback."""
    if project_key is None:
        conf = paths(version)["conf"]
        try:
            txt = conf.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"[provision] ERROR: could not read Neo4j conf at {conf}: {exc}", file=sys.stderr)
            return False
        block = conf_block(min_pw_len) if heap_max is None else conf_block(min_pw_len, heap_max)
        desired = _legacy_conf_desired(txt, block)
        if desired == txt:
            print("[provision] conf already has the current DeepMap block")
            return False
        if not _atomic_write_conf(conf, desired):
            return False
        cap = re.search(r"^server\.memory\.heap\.max_size=(\S+)", block, re.M)
        print("[provision] conf: DeepMap block written (auth-min-length + telemetry off + heap capped at "
              f"{cap.group(1) if cap else DEFAULT_HEAP_MAX})")
        return True

    # ---- per-instance ----
    _seed_instance_conf(version, project_key)
    p = paths(version, project_key)
    conf = p["conf"]
    managed = (f"{CONF_BLOCK_START}\n"
               + conf_instance_block(p["data"], p["logs"], p["run"], bolt, http,
                                     heap_max=heap_max, min_pw_len=min_pw_len)
               + f"{CONF_BLOCK_END}\n")
    try:
        cur = conf.read_text(encoding="utf-8") if conf.exists() else ""
    except OSError as exc:
        print(f"[provision] ERROR: could not read instance conf at {conf}: {exc}", file=sys.stderr)
        return False
    base = _filter_managed_keys(_strip_managed_block(cur)).rstrip("\n")
    desired = (base + "\n\n" + managed) if base else managed
    if cur == desired:
        return False   # content-addressed no-op (ports/heap unchanged)
    if not _atomic_write_conf(conf, desired):
        return False
    print(f"[provision] instance conf written: bolt={bolt} http={http} ({conf})")
    return True


def _is_initialized(version: str, project_key: str | None = None) -> bool:
    """True if THIS instance's store has already been created (set-initial-password is then a no-op).
    Per-instance: each project has its OWN auth+data store, so a fresh instance runs its own
    first-start password set."""
    db = paths(version, project_key)["databases"]
    return db.exists() and any(db.iterdir())


_WIN_UNSAFE_PW_CHARS = frozenset("&|^<>%")


def _win_password_cmd_safe(password: str) -> bool:
    """True iff `password` can reach `neo4j-admin.bat` on Windows WITHOUT cmd.exe mangling it.

    A `.bat` is launched by the OS through an implicit `cmd.exe` regardless of how subprocess is
    called, and Python adds NO escaping on that path (CPython documents this rather than fixing it);
    `neo4j-admin dbms set-initial-password` also takes the password as a positional argument only
    (no stdin / env-var / file), so the characters `& | ^ < >` (shell metacharacters) and `%`
    (%VAR% expansion) cannot be delivered intact — nor can leading/trailing whitespace (cmd trims /
    re-splits it). ASCII-safe passwords, INCLUDING internal spaces and embedded double-quotes (which
    the existing `cmd.exe /s /c "…"` wrapper already handles), are fine. The dev default `deepmap`
    is safe."""
    if password != password.strip():
        return False
    return not any(c in _WIN_UNSAFE_PW_CHARS for c in password)


def set_initial_password(version: str, password: str, project_key: str | None = None) -> bool:
    """Set the initial neo4j password (only effective on a fresh, never-started install). Returns True
    when the password is set (or the store was already initialised), False when it was NOT — the caller
    must then NOT start Neo4j: a first start without it initialises the store with the default
    neo4j/neo4j credentials and locks DeepMap out for good (2026-09 live validation #54: a JVM that could
    not reserve its heap failed this step, setup started Neo4j anyway, and the store was poisoned).

    Hardened: UTF-8 decode (no crash on a non-Latin code page), a timeout (the JVM tool can't hang
    provisioning forever), and exception-guarded so a launch failure degrades instead of crashing.

    Windows metacharacter guard: on Windows we run `cmd.exe /s /c "<cmdline>"` (a STRING, so
    subprocess doesn't re-quote) — this delivers SPACES and embedded double-quotes correctly, but
    `cmd.exe` still parses the shell metacharacters `& | ^ < >` and `%VAR%` expansion in the inner
    command, and there is no way to escape them through the `.bat` (see `_win_password_cmd_safe`).
    Rather than SILENTLY mangling such a password (which used to march setup into a confusing
    downstream connect/timeout), we now FAIL LOUD and run nothing, telling the user to pick a
    metacharacter-free `NEO4J_PASSWORD` on Windows. The dev default `deepmap` is unaffected.
    """
    if _is_initialized(version, project_key):
        print("[provision] Neo4j already initialized — skipping set-initial-password")
        return True
    if is_windows() and not _win_password_cmd_safe(password):
        print("[provision] ERROR: the NEO4J_PASSWORD contains characters that cannot be passed "
              "safely to neo4j-admin.bat on Windows (any of & | ^ < > % or leading/trailing "
              "whitespace) — cmd.exe re-parses them and there is no way to escape them through the "
              "batch file. set-initial-password was NOT run and Neo4j was NOT started (the store stays "
              "fresh). Choose a password without those characters (the dev default 'deepmap' works), "
              "then re-run setup.", file=sys.stderr)
        return False
    admin = paths(version)["admin_bin"]
    base = [str(admin), "dbms", "set-initial-password", password]
    if is_windows():
        cmd = f'cmd.exe /s /c "{subprocess.list2cmdline(base)}"'   # string -> no subprocess re-quote
    else:
        cmd = base
    # Per-instance: neo4j-admin must write auth into THIS instance's data dir -> point it at the
    # instance conf via NEO4J_CONF (U3: honored by neo4j-admin(.bat) to locate server.directories.data).
    env = dict(os.environ)
    if project_key is not None:
        env["NEO4J_CONF"] = str(paths(version, project_key)["conf_dir"])
    _neo4j_java_env(env)   # neo4j-admin runs the JDK `neo4j console` will (the preflight's choice, when it made one)
    try:
        out = subprocess.run(cmd, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=120, env=env)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"[provision] ERROR: set-initial-password failed to run: {exc}", file=sys.stderr)
        return False
    msg = ((out.stdout or "") + (out.stderr or "")).strip().splitlines()
    tail = msg[-1] if msg else ""
    if out.returncode != 0:
        # rc was previously only surfaced when there was NO output, so a real failure with a message
        # printed as if normal and setup marched on into a confusing downstream timeout (bug-hunt #3).
        # "already set" (live users in the system db) is the one benign non-zero outcome.
        if re.search(r"already|live neo4j-users", "\n".join(msg), re.I):
            print(f"[provision] set-initial-password: already set ({tail})")
            return True
        print(f"[provision] ERROR: set-initial-password returned rc={out.returncode}: {tail}",
              file=sys.stderr)
        return False
    print("[provision] set-initial-password:", tail or "ok")
    return True


def _tasklist_alive(pid: int) -> bool:
    """Windows liveness: tasklist lists the PID. Can't tell -> True (never suppress a real stop)."""
    try:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                             capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=15)
        return str(pid) in (out.stdout or "")
    except Exception:
        return True


def _proc_state(pid) -> str | None:
    """The state letter of a LINUX process — /proc/<pid>/stat field 3 (R S D T Z X ...) — or None when there is
    no readable /proc entry for it (not Linux, gone, hidden by hidepid). Never raises."""
    try:
        raw = Path(_PROC, str(int(pid)), "stat").read_text(encoding="utf-8", errors="replace")
        rest = raw[raw.rindex(")") + 2:].split()
        return rest[0] if rest else None
    except Exception:
        return None


def _proc_live_thread(pid) -> bool:
    """True iff a LINUX process whose main thread is a zombie still has a thread running — `ps` shows it as "Zl": a
    JVM whose main thread returned while its shutdown threads still run (sockets still bound, files still open).
    That process is NOT dead yet. Reads /proc/<pid>/task/*/stat, else the Threads: count in /proc/<pid>/status.
    Never raises (False when neither can be read)."""
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
        return (out.stdout or "").strip() or None
    except Exception:
        return None


def _pid_alive(pid: int) -> bool:
    """Best-effort cross-platform liveness probe for a PID. True iff a process with this PID exists
    right now. Used so a STALE/recycled pid file never makes stop signal a dead or unrelated PID.

    ROUND-7 (I4): a ZOMBIE is not alive. os.kill(pid, 0) succeeds on one, so a stopped JVM / server left in
    state Z (a container whose PID 1 does not reap orphans, or a child of this very process that nobody has
    waited for) used to read as running: _terminate_pid then escalated, waited ~7 s and reported "stop FAILED",
    the pid file stayed, and instance-rm's guard refused the delete on every retry. Linux reads the state from
    /proc/<pid>/stat (Z / X -> dead); other POSIX systems ask `ps -o stat=`. On a real Windows host — reached
    only when a test seam makes is_windows() say otherwise — the os.kill probe is never used: there,
    os.kill(pid, 0) TERMINATES the process."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    if is_windows():
        return _tasklist_alive(pid)
    state = _proc_state(pid)
    if state is not None:
        if state[:1] in ("Z", "X", "x"):
            return _proc_live_thread(pid)   # "Zl": the main thread is gone but the process is still shutting down
        return True
    if os.name == "nt":
        return _tasklist_alive(pid)
    try:
        os.kill(pid, 0)   # signal 0 = existence/permission probe, sends nothing
    except ProcessLookupError:
        return False
    except PermissionError:
        return True       # exists but not ours to signal (its state can't be read either)
    except Exception:
        return True
    if Path(_PROC).is_dir():
        return True       # Linux (a /proc mount) with no readable entry for it (hidepid): it exists
    return not str(_ps_state(pid) or "").startswith("Z")


# ---- process identity = pid + START STAMP (ROUND-2 D2; the Linux form is ROUND-3 E2) ----------------
# A pid file outlives a reboot and the OS recycles PIDs, and every DeepMap Neo4j / MCP server runs the
# same shared binary / script, so "alive + the right command line" cannot tell THIS instance's process
# from another project's that happens to have inherited the pid (2026-09 review #14). The start stamp
# can: it is recorded when the process is started (next to run/neo4j.pid, and in the MCP owner sidecar)
# and compared before every signal. A pid file without a record (an older build) falls back to "the
# process must be older than its pid file". An UNKNOWN stamp is never trusted (never signal).
# ROUND 4 (_pid_check): two COMPARABLE stamps that differ are PROOF the pid is another process — its stale
# pid file is cleared (never signalled); the no-record fallback is only suggestive (reported, kept), and on
# Linux — where it needs today's btime — a "started later" is UNPROVEN: DeepMap's own command line decides,
# unless the process's environment or another instance's verified record proves it is someone else's.
#
# The stamp is what the OS keeps FIXED for the life of a process:
#   * Windows — CIM Win32_Process.CreationDate as epoch seconds (the kernel's absolute CreateTime);
#   * Linux   — the token "linux:<boot_id>:<ticks>": /proc/<pid>/stat field 22 (start time in clock ticks
#               since boot) + /proc/sys/kernel/random/boot_id. NEVER an epoch built from /proc/stat `btime`:
#               the kernel recomputes btime (wall clock minus time since boot) on every read, so it moves
#               whenever the wall clock is stepped (NTP makestep, a WSL2 / VM resume — days, measured) and
#               DeepMap disowned its own live Neo4j / servers (2026-09 review, E2). Only another boot_id or
#               another tick count proves the pid was recycled;
#   * macOS / BSD — `ps -o lstart=` read in UTC (TZ=UTC0, C locale): epoch seconds at a 1-SECOND resolution
#               (the kernel's p_starttime is wall clock at fork; the tolerance below absorbs the truncation).
_CT_TOLERANCE_S = 2.0   # Windows / macOS epoch stamps (macOS lstart has a 1 s resolution)
_PROC = "/proc"         # the Linux proc mount (a seam for the hermetic gates)


def _clk_tck() -> float:
    try:
        return float(os.sysconf("SC_CLK_TCK"))
    except Exception:
        return 100.0


def _linux_boot_id() -> str | None:
    try:
        return Path(_PROC, "sys", "kernel", "random", "boot_id").read_text(encoding="utf-8").strip() or None
    except Exception:
        return None


def _linux_btime() -> float | None:
    """/proc/stat btime NOW (the wall clock at boot, as the kernel currently computes it). Used ONLY to turn a
    Linux stamp into an approximate epoch for the no-record pid-file fallback — never for an identity
    comparison. None when unreadable."""
    try:
        for ln in Path(_PROC, "stat").read_text(encoding="utf-8", errors="replace").splitlines():
            if ln.startswith("btime "):
                return float(ln.split()[1])
    except Exception:
        pass
    return None


def _pid_create_time(pid):
    """The START STAMP of the live process `pid`, or None when unknown (no such process, or the OS would not
    say): a float epoch (Windows, macOS/BSD) or the Linux token "linux:<boot_id>:<ticks>" (immune to wall-
    clock steps — see the block comment above). JSON-serialisable; compare two with _same_start. (The name
    is kept from round 2: records store it under `created`.) Never raises."""
    try:
        pid = int(pid)
        if pid <= 0:
            return None
        if is_windows():
            out = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                 "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
                 f"$p=Get-CimInstance Win32_Process -Filter 'ProcessId={pid}'; "
                 "if ($p -and $p.CreationDate) { $p.CreationDate.ToFileTimeUtc() }"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
            txt = (out.stdout or "").strip()
            if not re.fullmatch(r"\d{6,}", txt):
                return None
            return (int(txt) - 116444736000000000) / 1e7          # FILETIME (100 ns since 1601) -> epoch
        stat = Path(_PROC, str(pid), "stat")
        if stat.exists():
            raw = stat.read_text(encoding="utf-8", errors="replace")
            fields = raw[raw.rindex(")") + 2:].split()             # fields[0] is field 3 (state)
            ticks = int(fields[19])                                # field 22: starttime, in clock ticks
            return f"linux:{_linux_boot_id() or '-'}:{ticks}"
        out = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=15,
                             env=dict(os.environ, LC_ALL="C", LANG="C", TZ="UTC0"))
        txt = " ".join((out.stdout or "").split())
        if not txt:
            return None
        return float(calendar.timegm(time.strptime(txt, "%a %b %d %H:%M:%S %Y")))   # printed in UTC
    except Exception:
        return None


def _parse_linux_stamp(stamp):
    """(boot_id | None, ticks) of a Linux stamp token, or None when `stamp` is not one."""
    try:
        if isinstance(stamp, str) and stamp.startswith("linux:"):
            _, boot, ticks = stamp.split(":", 2)
            return (None if boot in ("", "-") else boot), int(ticks)
    except Exception:
        pass
    return None


def _same_start(recorded, current) -> bool | None:
    """Do two start stamps name the same process? True / False, or None when they are not comparable (a
    round-2 epoch record read on Linux, which now stamps tokens) — the caller then falls back. Linux: the
    tick counts must be equal, and so must the boot ids when both are known. Never raises."""
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


def _start_epoch(stamp) -> float | None:
    """An approximate epoch for a start stamp — for the no-record fallback ONLY (compared with a pid file's
    mtime, itself wall clock). A Linux token is converted with today's btime, so a wall-clock step since the
    start skews it: that is why _pid_check calls a Linux "started after the pid file" UNPROVEN (the command
    line decides) rather than recycled. None when unknown."""
    lin = _parse_linux_stamp(stamp)
    if lin is not None:
        bt = _linux_btime()
        return None if bt is None else bt + lin[1] / _clk_tck()
    try:
        return None if isinstance(stamp, bool) else float(stamp)
    except (TypeError, ValueError):
        return None


def _pid_check(pid, *, recorded=None, pidfile=None, created=None):
    """(verdict, basis): is the live `pid` the process a record describes, and what does the answer rest on?

      * (True | False, "stamp")  — two COMPARABLE start stamps (the recorded one and the live one). A False is
        PROOF that the pid now belongs to another process (another boot / another start): the pid file is
        stale (ROUND-4: callers clear it and never signal).
      * (True | False, "pidfile") — no comparable record (a pid file written by an older build): the process
        against its pid file's mtime. A process older than its pid file is taken as the recorded one; one that
        started AFTER it was written is suggestive of recycling but not proof (a clock step between the two
        writes can fake it): never signal it, never delete the pid file on that alone.
      * (None, "unproven") — the same fallback on LINUX, where the start can only be turned into an epoch with
        today's /proc/stat btime: a wall-clock step since boot (NTP makestep, a WSL2 / VM resume — days,
        measured) moves it later than the pid file, so "later" proves nothing. The caller's COMMAND-LINE check
        decides (a pre-upgrade Neo4j / server stays stoppable after a clock step — 2026-09 review of E2).
      * (None, "unknown") — the start stamp can't be read: never a reason to signal.
    `created` = the live stamp when the caller already has it. Never raises."""
    try:
        if created is None:
            created = _pid_create_time(pid)
        if created is None:
            return None, "unknown"
        if recorded is not None:
            same = _same_start(recorded, created)
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


def _pid_identity(pid, *, recorded=None, pidfile=None) -> bool | None:
    """Is the live `pid` the process a record describes? True = provably (its start stamp matches the
    recorded one, or — no comparable record — it is older than its pid file); False = NOT (another boot /
    another start — see _pid_check for which Falses are proof); None = unknown (the stamp can't be read, or a
    Linux no-record comparison a clock step could have skewed) — never a reason to signal. Never raises."""
    return _pid_check(pid, recorded=recorded, pidfile=pidfile)[0]


def _proc_environ(pid) -> dict | None:
    """The environment a live LINUX process was started with (/proc/<pid>/environ), or None when it can't be
    read (not Linux, gone, another user's). Every DeepMap Neo4j instance and MCP server runs the same shared
    binary / script — its environment is what says WHICH one it is (NEO4J_CONF = the instance's conf dir;
    DEEPMAP_SEARCH_PORT / DEEPMAP_EMBED_PORT = the server's port). Never raises."""
    try:
        if is_windows():
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


def _same_path(a, b) -> bool:
    try:
        norm = (lambda p: os.path.normcase(str(Path(p).resolve())).replace("\\", "/").rstrip("/"))
        return norm(a) == norm(b)
    except Exception:
        return False


def _neo4j_pid_owned_elsewhere(pid, project_key, pidfile, *, created=None) -> str | None:
    """What (other than THIS instance) provably runs the live Neo4j `pid`, else None — the extra evidence
    stop_neo4j consults when there is no comparable start record:
      * its environment (Linux) was launched for ANOTHER instance: NEO4J_CONF names another instance's conf
        dir (or none, for a per-project instance — start_neo4j always sets it), or — for the base store — an
        instance's conf dir under <home>/instances/;
      * ANOTHER instance's pid file names the same pid and ITS start record matches the live process.
    ROUND-7 (I4): first the conf dir the process SERVES (its --config-dir — the server JVM Neo4j itself names in
    run/neo4j.pid on POSIX): another instance's conf dir (or, for the base store, any instance's) proves it is
    another instance's, on every OS. Never raises."""
    try:
        served = _conf_dir_from_cmdline(_pid_cmdline(pid))
        if served:
            inst_root = deepmap_home() / "instances"
            if project_key is not None and not _same_path(served, paths(None, project_key)["conf_dir"]):
                return f"the Neo4j instance whose conf is {served}"
            if project_key is None and _strictly_under(served, inst_root):
                return f"the Neo4j instance whose conf is {served}"
        env = _proc_environ(pid)
        if env is not None:
            conf = env.get("NEO4J_CONF")
            inst_root = deepmap_home() / "instances"
            if project_key is not None:
                mine = paths(None, project_key)["conf_dir"]
                if not conf:
                    return "the base/legacy Neo4j (no instance conf in its environment)"
                if not _same_path(conf, mine):
                    return f"the Neo4j instance whose conf is {conf}"
            elif conf and _strictly_under(conf, inst_root):
                return f"the Neo4j instance whose conf is {conf}"
        if created is None:
            created = _pid_create_time(pid)
        if created is None:
            return None
        cands = []
        inst_root = deepmap_home() / "instances"
        if inst_root.is_dir():
            cands += sorted(inst_root.glob("*/run/neo4j.pid"))
        cands += sorted((deepmap_home() / "neo4j").glob("neo4j-community-*/run/neo4j.pid"))
        for pf in cands:
            try:
                if _same_path(pf, pidfile) or int(pf.read_text(encoding="utf-8").strip()) != int(pid):
                    continue
                rec = _recorded_create_time(_neo4j_identity_file(pf), pid)
                if rec is not None and _same_start(rec, created) is True:
                    owner = pf.parent.parent.name if pf.parent.parent.parent == inst_root else "the base/legacy"
                    return f"instance {owner}'s Neo4j (its own pid file and start record name it)"
            except Exception:
                continue
    except Exception:
        pass
    return None


def _strictly_under(child, parent) -> bool:
    try:
        c = os.path.normcase(str(Path(child).resolve())).replace("\\", "/").rstrip("/")
        p = os.path.normcase(str(Path(parent).resolve())).replace("\\", "/").rstrip("/")
        return c != p and c.startswith(p + "/")
    except Exception:
        return False


def _neo4j_identity_file(pidfile) -> Path:
    """The creation-time record kept next to a Neo4j pid file (run/neo4j.pid.identity). A separate file so
    older builds' `int(pidfile)` readers keep working."""
    return Path(pidfile).with_name("neo4j.pid.identity")


def _recorded_create_time(idfile, pid):
    """The creation time recorded for `pid` in a Neo4j identity file, or None (no file / another pid)."""
    try:
        rec = json.loads(Path(idfile).read_text(encoding="utf-8"))
        if isinstance(rec, dict) and int(rec.get("pid", -1)) == int(pid):
            return rec.get("created")
    except Exception:
        pass
    return None


# ---- ROUND-7 I4: the POSIX Neo4j pid file belongs to Neo4j ----------------------------------------------
# Neo4j 5.26's Unix bootloader (`bin/neo4j console` execs it, so it keeps the launcher's pid) reads run/neo4j.pid
# BEFORE it starts the server and refuses to run — "Neo4j is already running (pid:N)" — when that file names a
# LIVE process; after the start it writes the file ITSELF, naming its server JVM (a child in the launcher's
# process group). DeepMap used to pre-write the launcher's own pid there, so on Linux / macOS no local Neo4j
# could ever start (setup, heal, the H3b owner read, the benchmark's clone builds — 2026-09 review, verified live
# on the real 5.26.0 tarball and in its bytecode). On POSIX DeepMap therefore keeps the launcher's identity in its
# OWN file (run/neo4j.launcher.json: pid + start stamp + instance) and verifies the SERVING JVM by the conf dir
# the bootloader passes it (`--config-dir=<conf>`; its environment carries NEO4J_CONF too). Windows is unchanged:
# its bootloader asks the service manager, never the pid file, and console mode writes none.

def _neo4j_launcher_file(pidfile) -> Path:
    """DeepMap's OWN record of the POSIX launcher it started (run/neo4j.launcher.json, next to Neo4j's pid file):
    {"launcher_pid", "pgid", "created", "project_key", "conf_dir"}."""
    return Path(pidfile).with_name("neo4j.launcher.json")


def _hibernation_marker(project_key: str | None, version: str | None = None) -> Path:
    """The marker hibernate_idle leaves in an instance's run dir when IT stopped the instance — instance_table
    tells a hibernated instance from one that is merely stopped (T10d-W). A start removes it."""
    return paths(version, project_key)["run"] / "deepmap-hibernated"


_CONF_DIR_ARG_RE = re.compile(r'"--config-dir=([^"]*)"|--config-dir=(.+?)(?=\s+--|\s*$)')


def _conf_dir_from_cmdline(cmd) -> str | None:
    """The `--config-dir=<conf>` a Neo4j server JVM was started with (the bootloader passes it — see above), read
    from a command line: Linux /proc (args joined by spaces — a path may hold spaces; the value runs to the next
    ` --` option or the end) or Windows CIM (an argument with spaces is double-quoted). None when absent (e.g.
    the bootloader / launcher itself). Never raises."""
    try:
        m = _CONF_DIR_ARG_RE.search(str(cmd or ""))
        if not m:
            return None
        val = (m.group(1) if m.group(1) is not None else m.group(2) or "").strip().strip('"')
        return val or None
    except Exception:
        return None


def _neo4j_pid_conf_dir(pid) -> str | None:
    """The conf dir the live Neo4j process `pid` serves: its command line's --config-dir (the server JVM), else
    — Linux — the NEO4J_CONF in its environment. None when neither can be read. Never raises."""
    try:
        conf = _conf_dir_from_cmdline(_pid_cmdline(pid))
        if conf:
            return conf
        env = _proc_environ(pid)
        conf = (env or {}).get("NEO4J_CONF")
        return str(conf) if conf else None
    except Exception:
        return None


def _binary_present_at(nhome) -> bool:
    """True iff an extracted Neo4j binary lives at `nhome` (its completion sentinel, or bin/ + lib/). A DeepMap
    Neo4j process can only run from one — the guard before any process scan. Never raises."""
    try:
        nhome = Path(nhome)
        return (nhome / ".deepmap-extracted").exists() or ((nhome / "bin").is_dir() and (nhome / "lib").is_dir())
    except Exception:
        return False


def _list_processes() -> list:
    """[(pid, command line)] of the processes that could be a Neo4j JVM: Windows — every java.exe / javaw.exe (one
    CIM query, UTF-8 forced on the pipe); Linux — /proc/*/cmdline; else `ps -axo pid=,command=`. [] on any error.
    The one seam behind _find_neo4j_server_pids (hermetic gates stub it). Never raises."""
    rows: list = []
    try:
        if is_windows():
            out = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                 "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
                 "Get-CimInstance Win32_Process -Filter 'Name=''java.exe'' OR Name=''javaw.exe''' | "
                 "ForEach-Object { [string]$_.ProcessId + [char]9 + [string]$_.CommandLine }"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
            for ln in (out.stdout or "").splitlines():
                pid, _, cmd = ln.partition("\t")
                if pid.strip().isdigit():
                    rows.append((int(pid.strip()), cmd.strip()))
            return rows
        proc = Path(_PROC)
        if proc.is_dir():
            for d in proc.iterdir():
                if not d.name.isdigit():
                    continue
                try:
                    raw = (d / "cmdline").read_bytes()
                except OSError:
                    continue
                if raw:
                    rows.append((int(d.name), raw.replace(b"\0", b" ").decode("utf-8", "replace").strip()))
            return rows
        out = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=30)
        for ln in (out.stdout or "").splitlines():
            pid, _, cmd = ln.strip().partition(" ")
            if pid.isdigit():
                rows.append((int(pid), cmd.strip()))
    except Exception:
        return rows
    return rows


def _find_neo4j_server_pids(conf_dir) -> list:
    """The live pids of DeepMap Neo4j SERVER JVMs serving `conf_dir` — command line under DeepMap's Neo4j home
    (<home>/neo4j/neo4j-community-*) AND `--config-dir` equal to `conf_dir`. Only one JVM can serve a store, so
    more than one answer means "can't tell" to every caller. Never raises."""
    found: list = []
    try:
        marker = str(deepmap_home() / "neo4j").replace("\\", "/").lower().rstrip("/") + "/neo4j-community-"
        for pid, cmd in _list_processes():
            try:
                if not cmd or marker not in str(cmd).replace("\\", "/").lower():
                    continue
                conf = _conf_dir_from_cmdline(cmd)
                if conf and _same_path(conf, conf_dir) and _pid_alive(int(pid)):
                    found.append(int(pid))
            except Exception:
                continue
    except Exception:
        return []
    return sorted(set(found))


def _instance_bolt_port(project_key: str | None):
    """The bolt port an instance listens on: the base 7687 for the legacy layout, else the port its own conf
    declares (None when unknown). Never raises."""
    try:
        import port_registry as PR
        if project_key is None:
            return PR.BASE_BOLT
        pair = _neo4j_ports_from_instance_conf(project_key)
        return int(pair[0]) if pair else None
    except Exception:
        return None


def _unrecorded_server_jvm(p: dict, project_key: str | None):
    """THIS instance's Neo4j server JVM when no usable pid file names it — the loser of the v0.27.4 start race
    overwrote run/neo4j.pid with its own dead pid while the winner kept serving (2026-09 review: stop, hibernate
    and instance-rm could no longer see the JVM) — found by the conf dir it serves. Scans processes ONLY when an
    extracted binary is present under this DeepMap home AND the instance's bolt port is held (no JVM can be
    running otherwise). Exactly one match -> its pid, else None. Never raises."""
    try:
        if not _binary_present_at(p["neo4j_home"]):
            return None
        bolt = _instance_bolt_port(project_key)
        if not bolt or not _port_in_use("127.0.0.1", int(bolt)):
            return None
        pids = _find_neo4j_server_pids(p["conf_dir"])
        return pids[0] if len(pids) == 1 else None
    except Exception:
        return None


def _live_launcher(p: dict):
    """(pid, verdict) for DeepMap's POSIX launcher record of this instance: (pid, True) — the recorded launcher
    is alive and its start stamp is the recorded one (THIS launcher; its process group holds the server JVM);
    (pid, None) — alive but the stamps can't be compared; (None, None) — no record, or a stale one (dead, or a
    pid recycled by another process: the record is then removed). Never raises."""
    lf = _neo4j_launcher_file(p["pidfile"])
    try:
        rec = json.loads(lf.read_text(encoding="utf-8"))
        pid = int(rec.get("launcher_pid"))
    except Exception:
        return None, None
    try:
        if not _pid_alive(pid):
            lf.unlink(missing_ok=True)
            return None, None
        live, recorded = _pid_create_time(pid), rec.get("created")
        same = _same_start(recorded, live) if (live is not None and recorded is not None) else None
        if same is False:
            lf.unlink(missing_ok=True)   # two comparable stamps differ: the pid now belongs to another process
            return None, None
        return pid, (True if same else None)
    except Exception:
        return None, None


def _record_neo4j_server_identity(version: str | None, project_key: str | None) -> None:
    """POSIX, once THIS call's start is up: record the start stamp of the server JVM Neo4j named in run/neo4j.pid
    (run/neo4j.pid.identity) — but only when that JVM provably serves this instance (its --config-dir), so a later
    stop compares two start stamps ("stamp", ROUND-3 E2) instead of the no-record fallback. Best-effort; never
    raises."""
    try:
        p = paths(version, project_key)
        pid = int(p["pidfile"].read_text(encoding="utf-8").strip())
        if not _pid_alive(pid):
            return
        conf = _neo4j_pid_conf_dir(pid)
        if not conf or not _same_path(conf, p["conf_dir"]):
            return
        created = _pid_create_time(pid)
        if created is None:
            return
        _neo4j_identity_file(p["pidfile"]).write_text(json.dumps(
            {"pid": pid, "created": created, "project_key": project_key, "source": "neo4j-pidfile"}),
            encoding="utf-8")
    except Exception:
        pass


def _terminate_pid(pid: int, *, tree: bool) -> bool:
    """Terminate a PID the CALLER has already confirmed is alive; return True iff it is gone afterwards.
    `tree=True` kills the child tree (Windows /T — needed for the cmd->JVM launcher); `tree=False` signals just
    the one process (a single MCP python server). On Windows a taskkill exit code of 0 is a success; a non-zero
    one is a success only when every process taskkill could not terminate — and the pid itself — is gone
    (a survivor, an unreadable report, or a pid that was already gone before the kill stays a failure)."""
    if is_windows():
        argv = ["taskkill", "/F"] + (["/T"] if tree else []) + ["/PID", str(pid)]
        r = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode == 0:
            return True
        # 0.28.2: `taskkill /T` kills the children first; `cmd.exe /c neo4j.bat console` then ends ON ITS OWN, so the
        # final kill of a process that has just exited fails — "There is no running instance of the task." while
        # something still holds it, "The operation attempted is not supported." once nothing does — and taskkill exits
        # non-zero although the whole tree is gone (~8% of stops, measured): reported as "stop FAILED", the pid file
        # kept. Decide by liveness, not by the reason text: stopped iff every ERROR line is "could not be terminated"
        # and each process it names, and the pid itself, is gone now (a survivor — Access denied, ... — stays a
        # failure). "not found" (the pid was already gone, its children may live on orphaned) and output this cannot
        # read (a non-English Windows) keep the old answer, False. Never re-run taskkill on the pid instead: by then it
        # may name an unrelated process (PID reuse); a reused pid only reads as alive, which is the safe side.
        errors = [ln.strip() for ln in ((r.stdout or "") + "\n" + (r.stderr or "")).splitlines()
                  if ln.strip().startswith("ERROR:")]
        named = [re.match(r"ERROR: The process with PID (\d+)\b.*could not be terminated", ln) for ln in errors]
        if (errors and all(named) and not _pid_alive(pid)
                and not any(_pid_alive(int(m.group(1))) for m in named)):
            return True
        return False
    # POSIX: SIGTERM, then escalate to SIGKILL if the process is still alive (a JVM can drain
    # slowly). Previously this returned True immediately on SIGTERM, so stop() deleted the pid file
    # while a JVM that ignored/outlasted SIGTERM kept running — an orphan with no recorded pid to
    # signal again (bug-hunt #3). Return True only once the process is actually gone.
    #
    # For tree=True, signal the whole PROCESS GROUP (os.killpg), not just the launcher pid: start_neo4j
    # launches with start_new_session=True, so the launcher is a group leader and the JVM children share
    # its group — signalling only the pid would orphan them (this is the POSIX equivalent of Windows
    # taskkill /T, which the Windows branch already does). tree=False signals just the one pid.
    pgid = None
    if tree:
        try:
            pgid = os.getpgid(pid)
        except Exception:
            pgid = None                  # fall back to a single-pid signal if the group can't be read
        try:
            own_group = os.getpgrp()
        except Exception:
            own_group = None
        if pgid is not None and pgid == own_group:
            pgid = None                  # never signal OUR OWN group (a child that is not a group leader)

    def _sig(s: int) -> None:
        if pgid is not None:
            os.killpg(pgid, s)
        else:
            os.kill(pid, s)

    # ROUND-7 (I4): a tree stop of a group MEMBER that is not the leader — on POSIX, the server JVM Neo4j names in
    # run/neo4j.pid, whose group leader is the launcher — is done only once the leader has exited too (it does right
    # after its child): otherwise "stopped" was reported while the launcher was still winding down.
    leader = pgid if (pgid is not None and pgid != pid) else None

    def _gone() -> bool:
        # reap first when it is OUR child (e.g. setup's H3b stopping the store it just started) — an unreaped child
        # stays a zombie; _pid_alive also counts a zombie with no live thread as dead for the ones we can't reap.
        for q in ((pid,) if leader is None else (pid, leader)):
            try:
                if hasattr(os, "WNOHANG"):
                    os.waitpid(q, os.WNOHANG)
            except Exception:
                pass
        if _pid_alive(pid):
            return False
        return leader is None or not _pid_alive(leader)

    try:
        _sig(15)                         # SIGTERM (group if tree=True)
    except ProcessLookupError:
        return True                      # already gone
    except Exception:
        return False
    for _ in range(50):                  # up to ~5s for a graceful stop
        if _gone():
            return True
        time.sleep(0.1)
    try:
        _sig(9)                          # SIGKILL — it ignored SIGTERM
    except Exception:
        pass
    for _ in range(20):                  # up to ~2s for the kill to take effect
        if _gone():
            return True
        time.sleep(0.1)
    return _gone()


def _instances_running(env=None) -> int:
    """Best-effort count of DeepMap Neo4j instances with a LIVE pid file (for the memory-pressure
    warning before a 2nd+ instance). Never raises."""
    n = 0
    try:
        inst_root = deepmap_home() / "instances"
        pidfiles = list(inst_root.glob("*/run/neo4j.pid")) if inst_root.exists() else []
        legacy = paths()["pidfile"]
        if legacy.exists():
            pidfiles.append(legacy)
        for pf in pidfiles:
            try:
                if _pid_alive(int(pf.read_text().strip())):
                    n += 1
            except Exception:
                pass
    except Exception:
        pass
    return n


def _instance_running_for(project_key: str | None, *, version: str | None = None) -> bool:
    """True iff THIS project's Neo4j instance has a LIVE pid file. project_key=None -> the legacy/base
    instance. Best-effort; never raises. (The per-key companion to _instances_running's bare count.)"""
    try:
        p = paths(version, project_key)
        pf = p["pidfile"]
        if pf.exists() and _pid_alive(int(pf.read_text().strip())):
            return True
        if not is_windows():
            # POSIX (ROUND-7 I4): Neo4j writes its pid file only once its server JVM is spawned — the launcher
            # DeepMap started is recorded in its own file meanwhile
            return _live_launcher(p)[0] is not None
        return False
    except Exception:
        return False


def _dir_size_bounded(path, *, max_entries: int = 20000, deadline_s: float = 1.0) -> tuple[int, bool]:
    """(total_bytes, truncated) for a directory tree, bounded by entry-count AND wallclock so a huge
    graph's data dir can't stall `status`/`doctor`. truncated=True if the cap/deadline stopped the walk
    early. Never raises; (0, False) for a missing path."""
    total = 0
    seen = 0
    truncated = False
    try:
        p = Path(path)
        if not p.exists():
            return (0, False)
        deadline = time.monotonic() + deadline_s
        stack = [str(p)]
        while stack:
            if time.monotonic() >= deadline:
                truncated = True
                break
            d = stack.pop()
            try:
                with os.scandir(d) as it:
                    for e in it:
                        seen += 1
                        if seen > max_entries:
                            truncated = True
                            break
                        try:
                            if e.is_dir(follow_symlinks=False):
                                stack.append(e.path)
                            else:
                                total += e.stat(follow_symlinks=False).st_size
                        except OSError:
                            pass
                    if truncated:
                        break
            except OSError:
                pass
    except Exception:
        pass
    return (total, truncated)


def _clear_blocking_neo4j_pidfile(p: dict) -> None:
    """POSIX, before a start (ROUND-7 I4): Neo4j's Unix bootloader refuses to start while run/neo4j.pid names a
    LIVE process ("already running") and only self-clears a DEAD pid — so a pid file that is provably not this
    instance's running server would wedge every start: an unreadable one, a dead pid, a pid recycled by another
    program, or another instance's server JVM (its --config-dir). It is removed (with its identity record). A
    live JVM serving THIS instance's conf dir (or one that can't be judged) is left for Neo4j to report. Never
    raises."""
    try:
        pf = p["pidfile"]
        if not pf.exists():
            return
        try:
            pid = int(pf.read_text(encoding="utf-8").strip())
        except Exception:
            pid = None
        why = None
        if pid is None:
            why = "it is unreadable"
        elif not _pid_alive(pid):
            why = f"pid {pid} is not running"
        elif not _is_deepmap_neo4j_pid(pid):
            why = f"pid {pid} now belongs to another program"
        else:
            served = _neo4j_pid_conf_dir(pid)
            if served and not _same_path(served, p["conf_dir"]):
                why = f"pid {pid} is another Neo4j instance's server (conf {served})"
        if why:
            pf.unlink(missing_ok=True)
            _neo4j_identity_file(pf).unlink(missing_ok=True)
            print(f"[provision] cleared a stale Neo4j pid file ({why}) — Neo4j refuses to start while that file "
                  "names a live process")
    except Exception:
        pass


def _unrecorded_launch(proc, pidfile, exc) -> None:
    """start_neo4j, Windows (ISS-build:023): the launcher runs, but its pid file could not be written — `stop` could
    never find that JVM (console mode writes no pid file of its own). Stop what this call has JUST launched, through
    its own Popen handle — while the process runs its pid can't have been reused; one that already exited is never
    signalled — then raise, so ensure_neo4j reports a failed start instead of leaving a JVM running unrecorded.
    Always raises RuntimeError (naming the pid, and how to stop it by hand if the stop failed)."""
    rc = proc.poll()
    if rc is not None:
        what = f"the launcher had already exited (rc={rc})"
    else:
        try:
            stopped = _terminate_pid(proc.pid, tree=True)
        except Exception:
            stopped = False
        what = ("the launch was stopped again" if stopped else
                f"it could NOT be stopped — stop pid {proc.pid} and its java child by hand "
                f"(`taskkill /F /T /PID {proc.pid}`)")
    raise RuntimeError(f"could not record the Neo4j launcher (pid {proc.pid}) in {pidfile} "
                       f"({type(exc).__name__}: {exc}); {what}") from exc


def start_neo4j(version: str, project_key: str | None = None) -> int:
    """Start `neo4j console` as a detached background process; return the launcher PID.

    Detached so it survives the provisioning process exit; output is redirected to the (per-instance)
    console log. Per-instance: NEO4J_CONF points the launcher at THIS project's conf dir (own data/ports);
    the child inherits it on both Windows (cmd->neo4j.bat) and POSIX (U4). Every OS: when the Java preflight chose a
    JDK for Neo4j (macOS with no JAVA_HOME set; elsewhere a usable 17 / 21 chosen because the PATH java, the JavaSoft
    registry entry or the JAVA_HOME the user set names none), JAVA_HOME is that JDK (_neo4j_java_env), never one Neo4j's
    launcher picks by its own rule; otherwise the environment is passed on unchanged.

    Where the launcher is recorded differs per OS (ROUND-7 I4):
      * Windows — console mode writes NO pid file (its bootloader asks the service manager instead), so the
        launcher PID + its start stamp go into run/neo4j.pid (+ run/neo4j.pid.identity) — otherwise stop_neo4j
        would find nothing and leave the JVM running after a `setup` start (bug-hunt #5);
      * POSIX — run/neo4j.pid belongs to NEO4J: its bootloader reads it before starting (a live pid there =
        "already running", so pre-writing the launcher's own pid made every start fail) and writes it itself,
        naming its server JVM. DeepMap keeps the launcher (pid, start stamp, instance, conf dir) in its OWN
        run/neo4j.launcher.json, and first clears a pid file that would block the start
        (_clear_blocking_neo4j_pidfile).
    A start removes the instance's hibernation marker (it is no longer hibernated).
    A start that fails RAISES (ensure_neo4j turns it into a False + reason — ISS-build:023): before the launch (the run
    dir, the console log, a launcher Popen can't execute) nothing runs; on Windows a launch whose pid file can't be
    written is stopped again first (_unrecorded_launch), never left running unrecorded."""
    p = paths(version, project_key)
    neo4j = p["neo4j_bin"]
    posix = not is_windows()
    if posix:
        _clear_blocking_neo4j_pidfile(p)
    try:
        _hibernation_marker(project_key, version).unlink(missing_ok=True)
    except Exception:
        pass
    # Memory-pressure warning (never blocks): each instance is ~0.5-1 GB RSS; N instances add up.
    if project_key is not None and _instances_running() >= 1:
        print(f"[provision] NOTE: starting an additional Neo4j instance (project {project_key[:8]}); "
              "each runs its own JVM (~0.5-1 GB RAM). Cap it via DEEPMAP_NEO4J_HEAP_MAX, or stop idle "
              "projects. Per-project isolation trades RAM for a clean, separate graph.", file=sys.stderr)
    p["log"].parent.mkdir(parents=True, exist_ok=True)
    # the run dir BEFORE the launch (ISS-build:023): once the JVM runs, a failure to record it would orphan it
    p["pidfile"].parent.mkdir(parents=True, exist_ok=True)
    logf = open(p["log"], "ab")
    env = dict(os.environ)
    if project_key is not None:
        env["NEO4J_CONF"] = str(p["conf_dir"])
    _neo4j_java_env(env)   # the JDK the Java preflight chose, as JAVA_HOME (when it chose one; else env unchanged)
    try:   # close the parent's fd even if Popen raises (the child keeps its own dup'd fd; no leak)
        if is_windows():
            proc = subprocess.Popen(["cmd.exe", "/c", str(neo4j), "console"],
                                    stdout=logf, stderr=logf, stdin=subprocess.DEVNULL,
                                    creationflags=WINDOWS_BACKGROUND_FLAGS, close_fds=True, env=env)
        else:
            proc = subprocess.Popen([str(neo4j), "console"],
                                    stdout=logf, stderr=logf, stdin=subprocess.DEVNULL,
                                    start_new_session=True, close_fds=True, env=env)
    finally:
        logf.close()
    if posix:
        # POSIX: never touch run/neo4j.pid (Neo4j's own — see the docstring); the launcher goes into DeepMap's own
        # record. start_new_session=True made it a session + process-group leader: its group holds the server JVM.
        try:
            _neo4j_identity_file(p["pidfile"]).unlink(missing_ok=True)   # described an older pid
            _neo4j_launcher_file(p["pidfile"]).write_text(json.dumps(
                {"launcher_pid": int(proc.pid), "pgid": int(proc.pid), "created": _pid_create_time(proc.pid),
                 "project_key": project_key, "conf_dir": str(p.get("conf_dir") or "")}), encoding="utf-8")
        except Exception:
            pass   # best-effort: stop then verifies the server JVM Neo4j names by its conf dir
    else:
        # Windows: record the launcher PID so stop_neo4j can find it (console mode writes no pid file itself — #5),
        # plus its CREATION TIME (D2) so a later stop can tell this launcher from a recycled pid.
        try:
            p["pidfile"].write_text(str(proc.pid), encoding="utf-8")
        except OSError as exc:
            _unrecorded_launch(proc, p["pidfile"], exc)   # stops what this call just launched, then raises
        try:
            _neo4j_identity_file(p["pidfile"]).write_text(json.dumps(
                {"pid": int(proc.pid), "created": _pid_create_time(proc.pid), "project_key": project_key}),
                encoding="utf-8")
        except OSError:
            pass   # best-effort: without it stop falls back to "older than the pid file"
    # Fail-fast hint: if the launcher ALREADY exited (a bad binary, a bound Bolt port), surface it now
    # rather than letting wait_for_bolt block for its full timeout on a process that is already dead.
    rc = proc.poll()
    if rc is not None:
        print(f"[provision] WARNING: Neo4j launcher exited immediately (rc={rc}) — check {p['log']} "
              f"(common causes: a bound Bolt port, a bad binary path).", file=sys.stderr)
    print(f"[provision] started Neo4j console (launcher pid {proc.pid}); log -> {p['log']}")
    return proc.pid


def wait_for_bolt(uri: str, user: str, password: str, timeout: int = 120) -> bool:
    """Poll until Bolt accepts an authenticated connection. False on timeout (never a silent
    premature success)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_bolt_up(uri, user, password, timeout=3):
            return True
        time.sleep(2)
    return False


_LAST_STOP_OUTCOME = "nothing"   # stop_neo4j's last outcome: stopped | nothing | unverified | failed (main's stop rc)


def _stop_done(outcome: str, ret: bool) -> bool:
    global _LAST_STOP_OUTCOME
    _LAST_STOP_OUTCOME = outcome
    return ret


def _clear_instance_records(p: dict) -> None:
    """Remove an instance's pid file, its identity record and DeepMap's POSIX launcher record."""
    pf = p["pidfile"]
    for f in (pf, _neo4j_identity_file(pf), _neo4j_launcher_file(pf)):
        try:
            f.unlink(missing_ok=True)
        except OSError:
            pass


def _base_upgrade_stop_versions() -> list:
    """[the release the base store resolves to, the journal's other one] while an upgrade of the base store is open,
    else []. Never raises."""
    try:
        import port_registry as PR
        j = PR.read_upgrade_journal(None)
        if not j:
            return []
        first = store_neo4j_version(None)
        return [first] + [v for v in dict.fromkeys((j["to"], j["from"])) if v != first]
    except Exception:
        return []


def _stop_unrecorded(p: dict, project_key: str | None):
    """stop_neo4j's last resort once the pid file proved useless: THIS instance's server JVM found by its conf dir
    (_unrecorded_server_jvm — the v0.27.4 start race left run/neo4j.pid naming a dead loser). None when there is
    none; else True / False (stopped / the kill failed). Never raises."""
    pid = _unrecorded_server_jvm(p, project_key)
    if pid is None:
        return None
    print(f"[provision] found this instance's Neo4j server (pid {pid} — verified by the conf dir it serves) with no "
          "usable pid file — stopping it")
    ok = _terminate_pid(pid, tree=True)
    if ok:
        _clear_instance_records(p)
        print(f"[provision] stopped Neo4j (pid {pid})")
        return _stop_done("stopped", True)
    print(f"[provision] stop FAILED for pid {pid} (taskkill/kill returned non-zero)", file=sys.stderr)
    return _stop_done("failed", False)


def stop_neo4j(version: str | None = None, project_key: str | None = None) -> bool:
    """Stop the managed instance. Returns True if a process was signalled (and is gone).
    Per-instance: `project_key` selects THIS project's instance (its own pidfile). A live pid is signalled
    only once it is PROVEN to be this instance's Neo4j (creation time — D2 — and DeepMap's Neo4j command
    line / the conf dir it serves); a recycled pid is never killed, an unverifiable one is left alone.

    ROUND-7 (I4) — POSIX: run/neo4j.pid is Neo4j's own (it names the server JVM), so the stop goes (1) through
    DeepMap's launcher record when the recorded launcher is alive with its recorded start stamp — its process
    group (launcher + server JVM) is signalled; else (2) through the pid file, where a server JVM whose
    --config-dir is another instance's is never signalled; and (3) when the pid file is missing / stale, through
    THIS instance's server JVM found by its conf dir (only when an extracted binary exists and the instance's
    bolt port is held). Windows keeps (2) + (3). `_LAST_STOP_OUTCOME` says what happened (main's `stop` exit
    code): "stopped", "nothing" (provably nothing of this instance's running), "unverified" (a live process that
    could not be proven this instance's — left alone), "failed" (the kill failed).

    The BASE store (version None) while an upgrade of it is open (its journal): its pid file and conf dir live in its
    version's folder, and its JVM may be either release's (a Ctrl+C during setup's verification leaves the new one
    serving the copy) — the release the store resolves to is tried first, then the journal's other one when nothing
    of the first runs. An instance has one pid file whatever release runs it."""
    global _LAST_STOP_OUTCOME
    if version is None and project_key is None:
        tries = _base_upgrade_stop_versions()
        if tries:
            ok = stop_neo4j(tries[0], None)
            if _LAST_STOP_OUTCOME != "nothing" or len(tries) < 2:
                return ok
            return stop_neo4j(tries[1], None)
    _LAST_STOP_OUTCOME = "nothing"
    p = paths(version, project_key)
    pf = p["pidfile"]
    idf = _neo4j_identity_file(pf)
    launcher_unverified = None
    if not is_windows():
        lpid, verdict = _live_launcher(p)
        if lpid is not None and verdict is True:
            ok = _terminate_pid(lpid, tree=True)   # killpg: the launcher's group holds the server JVM
            if ok:
                _clear_instance_records(p)
                print(f"[provision] stopped Neo4j (launcher pid {lpid} and its server JVM)")
                return _stop_done("stopped", True)
            print(f"[provision] stop FAILED for launcher pid {lpid} (kill returned non-zero)", file=sys.stderr)
            return _stop_done("failed", False)
        launcher_unverified = lpid   # alive, but its start stamp can't be compared: never signalled on that alone
    if not pf.exists():
        r = _stop_unrecorded(p, project_key)
        if r is not None:
            return r
        if launcher_unverified is not None:
            print(f"[provision] could not verify that launcher pid {launcher_unverified} is still this instance's "
                  "Neo4j (its start time is unreadable) — NOT stopping it; stop it by hand if it is", file=sys.stderr)
            return _stop_done("unverified", False)
        print("[provision] no pid file — instance not managed/running")
        return _stop_done("nothing", False)
    try:
        pid = int(pf.read_text().strip())
    except Exception:
        print("[provision] unreadable pid file")
        return _stop_done("unverified", False)
    # A stale pid file (instance crashed; the kernel never cleared it) must NOT make us signal a
    # dead or OS-recycled PID (bug-hunt #3). Verify liveness first; if gone, just clear the file.
    if not _pid_alive(pid):
        print(f"[provision] recorded pid {pid} is not running (stale pid file) — nothing to stop")
        pf.unlink(missing_ok=True)
        idf.unlink(missing_ok=True)
        r = _stop_unrecorded(p, project_key)
        return r if r is not None else _stop_done("nothing", False)
    # ...and a LIVE pid must be the very process this pid file recorded: a pid file outlives a reboot and
    # the PID gets recycled — possibly by ANOTHER project's launcher of the same shared binary, whose
    # command line is identical (2026-09 #47 / review #14). Command line first (a process that is provably
    # NOT DeepMap's Neo4j makes the pid file stale), then the start stamp.
    if not _is_deepmap_neo4j_pid(pid):
        print(f"[provision] recorded pid {pid} is no longer this instance's Neo4j (the pid was recycled by "
              "another program) — NOT stopping it; clearing the stale pid file")
        pf.unlink(missing_ok=True)
        idf.unlink(missing_ok=True)
        r = _stop_unrecorded(p, project_key)
        return r if r is not None else _stop_done("nothing", False)
    created = _pid_create_time(pid)
    same, basis = _pid_check(pid, recorded=_recorded_create_time(idf, pid), pidfile=pf, created=created)
    if same is False and basis == "stamp":
        # ROUND-4: two COMPARABLE start stamps differ — proof that the pid now belongs to another process (after
        # a reboot, typically ANOTHER project's instance of the same shared binary). This pid file is stale:
        # clear it (never signal) — keeping it wedged instance-rm and pointed the user at the other instance.
        print(f"[provision] recorded pid {pid} now belongs to another process (its start time is not the one "
              "recorded for this instance — likely another project's Neo4j that inherited the pid) — NOT "
              "stopping it; clearing this instance's stale pid file")
        pf.unlink(missing_ok=True)
        idf.unlink(missing_ok=True)
        r = _stop_unrecorded(p, project_key)
        return r if r is not None else _stop_done("nothing", False)
    if basis != "stamp":
        # no comparable record (a pid file an older build wrote, or Neo4j's own on POSIX): the conf dir it serves,
        # its environment or another instance's own verified record may still PROVE the pid is another instance's
        elsewhere = _neo4j_pid_owned_elsewhere(pid, project_key, pf, created=created)
        if elsewhere:
            print(f"[provision] recorded pid {pid} is {elsewhere}, not this instance's — NOT stopping it; "
                  "clearing this instance's stale pid file")
            pf.unlink(missing_ok=True)
            idf.unlink(missing_ok=True)
            r = _stop_unrecorded(p, project_key)
            return r if r is not None else _stop_done("nothing", False)
    if same is False:
        # no record, and the process started AFTER this pid file was written: suggestive of a recycled pid (a
        # clock step can fake it) — never signal it, never delete the pid file on that alone: report both ways.
        print(f"[provision] pid {pid} runs DeepMap's Neo4j, but it started after this instance's pid file was "
              "written, so it can't be shown to be this instance's — it may be ANOTHER project's instance that "
              f"inherited the pid. NOT stopping it. If it is this instance's Neo4j, stop it by hand; if it is "
              f"another project's, leave it running and delete {pf} instead.", file=sys.stderr)
        return _stop_done("unverified", False)
    if basis == "unproven":
        # Linux, no record, the btime-derived start is later than the pid file — a wall-clock step since boot
        # does exactly that, so it proves nothing: the READABLE command line (DeepMap's own Neo4j binary)
        # decides, as it did before start stamps existed (E2 follow-up).
        cmd = _pid_cmdline(pid)
        marker = str(deepmap_home() / "neo4j").replace("\\", "/").lower().rstrip("/") + "/neo4j-community-"
        if not (cmd and marker in cmd.replace("\\", "/").lower()):
            print(f"[provision] could not verify that pid {pid} is still this instance's Neo4j (no start record "
                  "and no readable command line) — NOT stopping it; stop it by hand if it is", file=sys.stderr)
            return _stop_done("unverified", False)
        same = True
    if same is None:
        print(f"[provision] could not verify that pid {pid} is still this instance's Neo4j (its start time "
              "is unreadable) — NOT stopping it; stop it by hand if it is", file=sys.stderr)
        return _stop_done("unverified", False)
    ok = _terminate_pid(pid, tree=True)   # cmd->JVM tree on Windows; the process group on POSIX
    if ok:
        _clear_instance_records(p)
        print(f"[provision] stopped Neo4j (pid {pid})")
        return _stop_done("stopped", True)
    print(f"[provision] stop FAILED for pid {pid} (taskkill/kill returned non-zero)", file=sys.stderr)
    return _stop_done("failed", False)


# ---- instance lifecycle: idle hibernation + instance-rm GC ------------------------------------
# Each per-project instance is its own ~0.5-1 GB JVM. DeepMap has NO daemon, so idleness is inferred
# from a `last_used` stamp in the port registry (bumped on sync/search). These verbs never allocate
# ports / write .mcp.json (they are the GC side of the seam), read the registry read-only, and never
# raise. Revive = the ordinary ensure_neo4j/heal no-re-sync path (the data dir survives hibernation).
DEFAULT_IDLE_MIN = 30


def hibernate_idle(target: str = ".", *, idle_minutes: int | None = None, scope: str = "others") -> int:
    """Spin DOWN idle per-project Neo4j instances to reclaim RAM. Hibernates a project iff it is: (a)
    NOT the current one (unless scope="all"); (b) RUNNING (a live pidfile); (c) IDLE — its registry
    `last_used` is OLDER than idle_minutes. A MISSING last_used means 'unknown' and is NEVER hibernated
    (hibernate only on positive evidence of idleness). Returns the count stopped. Per-project errors are
    swallowed; the function never raises. Read-only on the registry (no allocation / no .mcp.json)."""
    try:
        window_min = DEFAULT_IDLE_MIN if idle_minutes is None else int(idle_minutes)
    except Exception:
        window_min = DEFAULT_IDLE_MIN
    window_s = max(0, window_min) * 60
    stopped = 0
    try:
        import port_registry as PR
        reg = PR.load_registry()
        if not reg:
            return 0
        cur_key = PR.project_key(target)
        seen_pidfiles = set()
        # Protect the CURRENT project's instance (scope=others only) — pre-seed its pidfile so a
        # base-converged current project (pk -> the legacy pidfile) can't be swept via another entry
        # that maps to it. In scope="all" nothing is protected (the current project is fair game too).
        if scope != "all":
            try:
                cur_pair = PR.lookup_neo4j_ports(target)
                cur_pk = None if (not cur_pair or (cur_pair[0], cur_pair[1]) == (PR.BASE_BOLT, PR.BASE_HTTP)) else cur_key
                seen_pidfiles.add(str(paths(None, cur_pk)["pidfile"]))
            except Exception:
                pass
        for key, ent in list(reg.get("projects", {}).items()):
            try:
                if scope != "all" and key == cur_key:
                    continue
                if not isinstance(ent, dict):
                    continue
                pair = PR._pair_from_entry(ent, ("bolt_port", "http_port"))
                if not pair:
                    continue   # no Neo4j instance registered for this project
                pk_paths = None if (pair[0], pair[1]) == (PR.BASE_BOLT, PR.BASE_HTTP) else key
                pf = str(paths(None, pk_paths)["pidfile"])
                if pf in seen_pidfiles:
                    continue   # dedup: base-converged pair / the current project's own pidfile
                if not _instance_running_for(pk_paths):
                    continue
                age = PR._age_seconds(ent.get("last_used"))
                if age is None or age < window_s:
                    continue   # unknown-idleness OR recently used -> keep
                if _sync_running_for(ent.get("root")):
                    continue   # a sync (DeepMap's background refresh included) is writing this instance right now
                if pk_paths is None and _base_claim_is_foreign(ent.get("root")):
                    continue   # F2: a mis-adopted base claim — the store is another project's; its idleness
                               # is not this entry's to judge (that project's use never bumps last_used)
                seen_pidfiles.add(pf)
                if stop_neo4j(project_key=pk_paths):
                    stopped += 1
                    try:   # instance_table tells "hibernated" (by hibernate) from "stopped" (T10d-W)
                        mk = _hibernation_marker(pk_paths)
                        mk.parent.mkdir(parents=True, exist_ok=True)
                        mk.write_text(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), encoding="utf-8")
                    except OSError:
                        pass
            except Exception:
                continue
    except Exception:
        pass
    return stopped


def _sync_running_for(root) -> bool:
    """hibernate: does the project at `root` have a LIVE sync holder right now (its .deepmap/sync_running.json names
    a process whose pid and creation time still match — sync_status.read_running)? Read-only: never takes or probes
    the per-root or per-instance sync lock (a probe would make a concurrent sync skip). Never raises."""
    try:
        if not root:
            return False
        import sync_status
        rec = sync_status.read_running(Path(root))
        return bool(rec and rec.get("live") is True)
    except Exception:
        return False


def _base_claim_is_foreign(root) -> bool:
    """hibernate, F2: does the registry entry at `root` (which records the base pair) claim a store that
    provably belongs to ANOTHER project? Its owner record names another project, or — no record yet (a
    pre-upgrade claim) — the RUNNING base's :_DeepMapMeta owner does (_foreign_base_owner). An entry with no
    root, a record naming it, or no proof -> False (judged as before). Never raises."""
    try:
        if not root:
            return False
        rec = _base_owner_root()
        if rec:
            return not _owner_is_this_project(root, rec)
        return bool(_foreign_base_owner(root))
    except Exception:
        return False


_RM_RETRY = (20, 0.5)   # (attempts, seconds between) for deleting an instance dir the JVM may still hold


def _rmtree_retry(path: Path) -> bool:
    """Delete `path`, retrying while a just-killed process still holds files in it (Windows keeps a
    dying JVM's handles open for a few seconds after taskkill returns). True iff the dir is gone."""
    attempts, delay = _RM_RETRY
    for i in range(max(1, int(attempts))):
        shutil.rmtree(str(path), ignore_errors=True)
        if not path.exists():
            return True
        if i + 1 < attempts:
            time.sleep(delay)
    return not path.exists()


def _stop_neo4j_and_wait(project_key: str, wait_s: float = 15.0) -> None:
    """stop_neo4j for one instance, then wait (bounded) until its launcher pid is really gone — Windows
    taskkill returns before the JVM tree exits and releases its store files. Best-effort; never raises."""
    try:
        pid = None
        try:
            pid = int(paths(None, project_key)["pidfile"].read_text(encoding="utf-8").strip())
        except Exception:
            pid = _live_launcher(paths(None, project_key))[0] if not is_windows() else None
        if not stop_neo4j(project_key=project_key):
            return   # nothing of ours was signalled (stale / recycled / unverifiable) -> nothing to wait for
        deadline = time.monotonic() + wait_s
        while pid and time.monotonic() < deadline and _pid_alive(pid):
            time.sleep(0.25)
    except Exception:
        pass


def _entry_aliases(key: str) -> list:
    """The OTHER registry keys project `key` is known by — a key migration's `aliases` / `migrated_from` /
    `pending_key` (port_registry E1) — validated as registry keys (hex; they become path components). Never
    raises."""
    out: list = []
    try:
        import port_registry as PR
        ent = ((PR.load_registry() or {}).get("projects") or {}).get(key)
        if isinstance(ent, dict):
            cands = list(ent.get("aliases") if isinstance(ent.get("aliases"), list) else [])
            cands.append(ent.get("migrated_from"))
            cands.append(ent.get("pending_key"))
            for a in cands:
                if isinstance(a, str) and re.fullmatch(r"[0-9a-f]{8,64}", a) and a != key and a not in out:
                    out.append(a)
    except Exception:
        pass
    return out


def _live_instance_pid(project_key):
    """A LIVE process of instance `project_key` that instance-rm must never delete the store under, else None: the
    pid in its pid file when alive; on POSIX the launcher DeepMap recorded (ROUND-7 I4 — Neo4j writes the pid file
    only once its server JVM is up); or its server JVM found by the conf dir it serves when the pid file is stale /
    missing (_unrecorded_server_jvm). Never raises."""
    try:
        p = paths(None, project_key)
        try:
            pid = int(p["pidfile"].read_text(encoding="utf-8").strip())
            if _pid_alive(pid):
                return pid
        except Exception:
            pass
        if not is_windows():
            lpid = _live_launcher(p)[0]
            if lpid is not None:
                return lpid
        return _unrecorded_server_jvm(p, project_key)
    except Exception:
        return None


def _gout_dirs_for_key(key: str) -> list:
    """The graphify output dirs under <DEEPMAP_HOME>/gout/ that belong to project `key` (the repo-root
    sync's dir + any subdirectory sync's) — graphify_extract.gout_dirs_for_project (the owner of that
    layout), with a minimal local mirror only if that module can't be imported. Never raises."""
    try:
        import graphify_extract as GX
        fn = getattr(GX, "gout_dirs_for_project", None)
        if callable(fn):
            return list(fn(key) or [])
    except Exception:
        pass
    found: list = []
    try:
        base = deepmap_home() / "gout"
        for d in (sorted(base.iterdir()) if base.is_dir() else []):
            try:
                marker = d / "deepmap_owner.json"
                data = json.loads(marker.read_text(encoding="utf-8")) if marker.is_file() else {}
                if d.is_dir() and (data.get("project_key") == key or (not data and d.name == key)):
                    found.append(d)
            except Exception:
                continue
    except Exception:
        pass
    return found


def _remove_idle_lock(path) -> bool:
    """Delete a lock FILE that nobody holds (taken non-blocking first — a start / sync that holds it right now
    keeps it). POSIX unlinks while holding it; Windows (which can't delete an open file) right after the
    release. True iff it is gone. Never raises."""
    try:
        path = Path(path)
        if not path.exists():
            return True
        handle, how = _file_lock(path, 0)
        if handle is None:
            return False
        try:
            if os.name != "nt":
                path.unlink(missing_ok=True)
        finally:
            _file_unlock(handle)
        if os.name == "nt":
            try:
                path.unlink()
            except OSError:
                pass
        return not path.exists()
    except Exception:
        return False


def _remove_instance_locks(key: str, aliases=(), *, neo4j_pair=None, mcp_pair=None, purge: bool = False) -> None:
    """instance-rm's run-dir cleanup for project `key` (and its migrated `aliases`): the per-key Neo4j start
    lock (run/neo4j-start-<key>.lock), the per-port MCP start locks of the project's pair
    (run/deepmap-{search,embed}-<port>.lock — its servers were just stopped) and, for a removed per-project
    instance, sync's per-instance write lock (<home>/instance-<digest of its bolt URI>.lock — under this build's
    127.0.0.1 spelling AND an older build's localhost one, ROUND-8 J4). With `purge` (the project is gone from the
    registry) also its heal status file + lock, setup's persisted MCP blocker (run/setup-blocked-<key>.json), the
    interpreter setup recorded for the heal hook (run/setup-python-<root id>.json naming this key — ISS-build:013), a
    DEAD server's pid file + owner record on its ports when the record names this project, and the per-port server
    logs of its pair (<home>/<name>-<port>.log) unless something still listens there (ROUND-8 J6). A lock someone
    holds is never deleted; another project's files are never touched. Best-effort; never raises."""
    try:
        keys = [key] + [a for a in aliases if a != key]
        run = deepmap_home() / "run"
        for kk in keys:
            _remove_idle_lock(run / f"neo4j-start-{kk}.lock")
        for name, port in zip(MCP_SERVERS, mcp_pair or ()):
            _remove_idle_lock(_mcp_pidfile(name, port).with_suffix(".lock"))
            if purge:
                try:
                    pf, of = _mcp_pidfile(name, port), _mcp_ownerfile(name, port)
                    rec = json.loads(of.read_text(encoding="utf-8")) if of.is_file() else None
                    pid = int(pf.read_text(encoding="utf-8").strip()) if pf.is_file() else None
                    if isinstance(rec, dict) and rec.get("project_key") in keys and not (pid and _pid_alive(pid)):
                        pf.unlink(missing_ok=True)
                        of.unlink(missing_ok=True)
                except Exception:
                    pass
        if neo4j_pair:
            try:
                import port_registry as PR
                if (neo4j_pair[0], neo4j_pair[1]) != (PR.BASE_BOLT, PR.BASE_HTTP):
                    import sync as _sync
                    # the lock of this build (127.0.0.1) and of an older build (localhost spelling, J4)
                    for u in dict.fromkeys((_bolt_uri(int(neo4j_pair[0])),
                                            _LEGACY_BOLT_URI_FMT.format(int(neo4j_pair[0])))):
                        _remove_idle_lock(_sync._instance_lock_path(u))
            except Exception:
                pass
        if purge:
            for kk in keys:
                try:
                    (run / f"heal-{kk}.json").unlink(missing_ok=True)
                except OSError:
                    pass
                _remove_idle_lock(run / f"heal-{kk}.lock")
                _remove_setup_blocker_for_key(kk)   # ROUND-8 J6: setup's persisted MCP blocker goes with the project
            try:   # ISS-build:013: the interpreter setup recorded for the heal hook (run/setup-python-<root id>.json)
                import port_registry as PR
                clear = getattr(PR, "clear_setup_python", None)
                if callable(clear):
                    clear(keys)
            except Exception:
                pass
            # ROUND-8 J6: the per-port server logs (<home>/<name>-<port>.log) of the purged project's pair — else they
            # pile up and a project that later gets the recycled port appends to a log holding this one's output.
            # Never while something still listens on the port (a server we could not stop keeps its log open).
            for name, port in zip(MCP_SERVERS, mcp_pair or ()):
                try:
                    if not is_http_up(mcp_host(), int(port)):
                        (deepmap_home() / f"{name}-{int(port)}.log").unlink(missing_ok=True)
                except OSError:
                    pass
    except Exception:
        pass


def _remove_setup_blocker_for_key(key: str) -> None:
    """Delete <home>/run/setup-blocked-<key>.json (port_registry.clear_setup_blocker by key, else the file itself).
    Never raises."""
    try:
        import port_registry as PR
        fn = getattr(PR, "clear_setup_blocker", None)
        if callable(fn):
            try:
                if fn(None, key=key) is True:
                    return
            except Exception:
                pass
        (deepmap_home() / "run" / f"setup-blocked-{key}.json").unlink(missing_ok=True)
    except Exception:
        pass


def instance_rm(target: str = ".", *, key: str | None = None, yes: bool = False, purge: bool = False) -> int:
    """Remove a project's per-project Neo4j INSTANCE — stop its MCP servers and its JVM, delete its
    instances/<key>/ tree, and drop its bolt/http from the registry (--purge drops the whole entry).
    GUARDS (hard):
      * NEVER the base/legacy store (the base 7687/7474 pair) — no flag overrides this; that store is
        shared and is not an isolated per-project instance. A project with NO allocation has nothing to
        remove (said as such — not mislabelled "the base store"), except an ORPHAN instances/<key>/ dir
        left by an earlier failed delete, which is cleaned up.
      * NEVER the CURRENT project without yes=True.
      * NEVER with an unreadable / unrecognised registry (another project's allocation can't be told
        apart from this one's — ROUND-2 D1).
    The registry allocation is dropped only once the dir is really gone (a delete the dying JVM still
    blocks returns 1 and keeps the entry, so a retry can finish the job). The project's OWN MCP servers
    (owner sidecar = this key) are stopped too: without the instance they only answer errors while
    holding ~1 GB each. `purge` also drops the whole registry entry and the project's graphify output
    dirs (<home>/gout/…) — and still does so when an earlier plain instance-rm already removed the
    instance. The project's run-dir lock files go with it (_remove_instance_locks: the Neo4j start lock,
    its MCP ports' start locks, sync's per-instance write lock; with purge its heal status, setup's persisted
    blocker, setup's recorded interpreter and its MCP ports' server logs) — never a lock someone holds, never another
    project's. Returns 0 on success, 2 on a refusal, 1 on a failed delete /
    unexpected error. Never raises."""
    try:
        import port_registry as PR
        status = PR.registry_status()
        if status in ("unreadable", "unrecognized"):
            print(f"[instance-rm] refusing: the port registry {PR.registry_path()} is {status}, so this "
                  "project's instance can't be told apart from another project's — fix it or move it aside "
                  "first (/deep-map:setup re-adopts each instance from its own conf).", file=sys.stderr)
            return 2
        # resolve the key: an explicit --key <prefix> (GC when the repo dir is gone), else the target path
        if key:
            reg = PR.load_registry() or {"projects": {}}
            matches = [k for k in reg.get("projects", {}) if k.startswith(key)]
            if len(matches) == 0:
                print(f"[instance-rm] no registered project matches key prefix {key!r}", file=sys.stderr)
                return 2
            if len(matches) > 1:
                print(f"[instance-rm] key prefix {key!r} is AMBIGUOUS ({len(matches)} matches) — refusing",
                      file=sys.stderr)
                return 2
            k = matches[0]
        else:
            k = PR.project_key(target)
        # HARD guard: the base/legacy store is never an instance-rm target (no flag overrides this).
        pair = PR.neo4j_pair_for_key(k)
        inst_root = deepmap_home() / "instances"
        inst_dir = inst_root / k
        if pair is not None and (pair[0], pair[1]) == (PR.BASE_BOLT, PR.BASE_HTTP):
            print("[instance-rm] refusing: this project uses the base/legacy Neo4j store (7687), not an "
                  "isolated per-project instance — nothing to remove.", file=sys.stderr)
            return 2
        # the project's other keys (a registry key migration — E1 — records them as aliases): their
        # instance / graphify-output dirs are this project's too and must not be left behind
        aliases = _entry_aliases(k)
        inst_keys = [k] + [a for a in aliases if (inst_root / a).exists()]
        has_instance = pair is not None or any((inst_root / kk).exists() for kk in inst_keys)
        entry = k in ((PR.load_registry() or {}).get("projects") or {})
        gout: list = []
        if purge:
            for kk in [k] + aliases:
                gout += [d for d in _gout_dirs_for_key(kk) if d not in gout]
        if not has_instance and not (purge and (entry or gout)):
            print("[instance-rm] no Neo4j instance is allocated for this project (never set up here, "
                  "already removed, or it runs on an operator NEO4J_URI) — nothing to remove.",
                  file=sys.stderr)
            return 2
        # active-project guard
        try:
            cur = PR.project_key(Path.cwd())
        except Exception:
            cur = None
        if k == cur and not yes:
            print("[instance-rm] refusing: that is the CURRENT project's instance — re-run with --yes to "
                  "remove it (its graph is deleted; a later setup/sync rebuilds it).", file=sys.stderr)
            return 2
        if aliases:                     # THIS project's own servers only (owner sidecar = k or an alias)
            _stop_mcp_servers_for_key(k, aliases=aliases)
        else:
            _stop_mcp_servers_for_key(k)
        if has_instance:
            for kk in inst_keys:
                _stop_neo4j_and_wait(kk)    # best-effort (a stale/absent pidfile is fine)
            # E2: never delete an instance dir under a Neo4j that is still running — stop_neo4j leaves a live
            # pid it could not prove to be this instance's (unknown / mismatched start stamp) or could not
            # kill; on POSIX an rmtree would succeed under the live JVM and orphan it with a deleted store.
            for kk in inst_keys:
                live = _live_instance_pid(kk)
                if live is not None:
                    pidf = paths(None, kk)["pidfile"]
                    print(f"[instance-rm] ERROR: this instance's Neo4j process {live} (its pid file, DeepMap's launcher "
                          "record, or the JVM serving its conf dir) is still running and could not be stopped or "
                          "shown to be this instance's (see above) — NOT deleting "
                          f"{inst_root / kk} under it; the registry is unchanged. If that process IS this "
                          "instance's Neo4j, stop it by hand; if it is another project's instance (a pid inherited "
                          f"after a reboot), leave it running and delete {pidf} instead. Then re-run "
                          f"`instance-rm --key {k}`.", file=sys.stderr)
                    return 1
            for kk in inst_keys:
                if not _rmtree_retry(inst_root / kk):
                    print(f"[instance-rm] ERROR: could not delete {inst_root / kk} (files still held — is its "
                          "Neo4j still stopping?). The registry was left unchanged so the instance stays "
                          f"removable: re-run `instance-rm --key {k}` in a few seconds.", file=sys.stderr)
                    return 1
        mcp_pair = None
        try:
            ent = ((PR.load_registry() or {}).get("projects") or {}).get(k)
            mcp_pair = PR._pair_of(ent) if isinstance(ent, dict) else None
        except Exception:
            mcp_pair = None
        removed = PR.remove_entry(key=k, drop_neo4j_only=not purge) if (pair or (purge and entry)) else False
        purged = sum(1 for d in gout if _rmtree_retry(Path(d)))
        _remove_instance_locks(k, aliases, neo4j_pair=pair, mcp_pair=mcp_pair, purge=purge)
        if pair:
            what = f"instance {k[:8]} (bolt {pair[0]}); dir gone"
        elif has_instance:
            what = f"orphan instance dir {k[:8]}; dir gone"
        else:
            what = f"project {k[:8]} (no instance was left)"
        reg = ("entry purged" if purge else "neo4j fields dropped") if removed else "unchanged"
        tail = f"; {purged} graphify output dir(s) deleted" if purge else ""
        print(f"[instance-rm] removed {what}; registry {reg}{tail}")
        return 0
    except Exception as e:   # noqa: BLE001 — a GC verb must report, not crash
        print(f"[instance-rm] error: {type(e).__name__}: {e}", file=sys.stderr)
        return 1


# ---- preflight (P3): fail fast BEFORE provisioning a LOCAL Neo4j -----------------------------
# A missing/wrong Java, an occupied bolt/http port, or too little disk otherwise surface only as a
# 120s wait_for_bolt timeout + log-spelunking. Detect them in <1s with an actionable message. These
# checks run ONLY on the provision-a-local-instance path (after ensure_neo4j's is_bolt_up short-
# circuit) so a bring-your-own / Aura Neo4j is never forced to have a local JRE / free ports / disk.
_JAVA_OK_MAJORS = (17, 21)   # Neo4j 5.26 LTS supported Java majors


_JAVA_DIAG: str | None = None   # why the LAST _java_version() returned None (see there)
_JAVA_PROBED_HOME: str | None = None   # the java.home the last _probe_java(props=True) read (None: none named)
# the JDK home the last _java_version() chose for Neo4j (None: nothing to hand over: a usable JAVA_HOME the user set, a
# PATH java that is a 17 / 21, no usable JDK). macOS: its own rule (_macos_java_version); every other OS: a usable JDK
# chosen because the java JAVA_HOME names (_java_pass_over) or the PATH java is missing, unsupported or broken, or
# (Windows) because the JavaSoft registry entry Neo4j would take first is not a usable 17 / 21 (_other_java_version).
# start_neo4j / set_initial_password pass it as JAVA_HOME (_neo4j_java_env); _JAVA_CHECKED says whether _java_version
# ran in this process at all.
_NEO4J_JAVA_HOME: str | None = None
_JAVA_CHECKED = False
# every OS but macOS: where the java the last _java_version() judged came from ("javahome": JAVA_HOME's, "path": the
# PATH java, "found": an installed JDK the search pinned; None: not known, e.g. a gate's stub of _java_version) and its
# path, for the preflight's words (_other_java_problems).
_JAVA_SOURCE: str | None = None
_JAVA_EXE: str | None = None
# every OS but macOS: the JAVA_HOME value the last _java_version() passed over, exactly as it was set (its java
# missing, unsupported or broken, and a usable JDK chosen instead: _java_pass_over), and the preflight's one NOTE line
# about it; None: nothing passed over. _neo4j_java_env replaces exactly this value with _NEO4J_JAVA_HOME.
_JAVA_PASSED_OVER: str | None = None
_JAVA_PASSED_NOTE: str | None = None
_JAVA_NOTES_SHOWN: set = set()   # the NOTE lines this process printed: an identical one is never printed twice

# Every OS but macOS, a PATH java that is missing, unsupported or broken: where an installed JDK 17 / 21 is looked for
# (_java_install_candidates). Windows: every JavaHome under these HKLM keys (Oracle's installer writes them, other
# vendors' MSIs on request; Neo4j's launcher reads the same keys), then each child of these vendor folders under
# %ProgramFiles% / %ProgramW6432% (Temurin, Oracle, Microsoft, Azul Zulu, Corretto, Liberica, Semeru, an OpenJDK zip).
# Every other OS: each child of these folders (distro packages, Temurin's repo, SDKMAN).
# Everywhere: <DEEPMAP_HOME>/jdk*.
_WIN_JAVASOFT_KEYS = (r"SOFTWARE\JavaSoft\JDK", r"SOFTWARE\JavaSoft\JRE")
_WIN_JDK_VENDOR_DIRS = ("Eclipse Adoptium", "Java", "Microsoft", "Zulu", "Amazon Corretto", "BellSoft", "Semeru",
                        "OpenJDK")
_POSIX_JVM_DIRS = ("/usr/lib/jvm", "/usr/lib64/jvm", "~/.sdkman/candidates/java")
_JAVA_FIND_MAX_PROBES = 4   # `java -version` runs one search may make (a candidate that only timed out ends it)
_JDK_32BIT_ARCHS = ("x86", "i386", "i486", "i586", "i686")   # a `release` OS_ARCH whose JVM Neo4j's heap does not fit
_JAVA_MAJOR_RE = re.compile(r"(?<![\d.])(?:1\.)?(\d{1,2})(?!\d)")
_JAVA_NOT_FOUND = "no JDK 17 or 21 was found in the usual install folders"
# winget as Claude Code's shell runs it (no console to answer a prompt): the exact id, the winget source only (never
# the msstore source's first-use agreement), agreements accepted. Its Temurin MSI sets PATH, JAVA_HOME and the JavaSoft
# keys.
_JAVA_WINGET = ("winget install -e --id EclipseAdoptium.Temurin.21.JDK --source winget --accept-source-agreements "
                "--accept-package-agreements")
_JAVA_ADOPTIUM = "https://adoptium.net/temurin/releases/?version=21"
# where a JAVA_HOME change is made so that it lasts (a running Claude Code and its heal keep the value they started
# with)
_JAVA_HOME_WHERE_WIN = "Settings > System > About > Advanced system settings > Environment Variables"
_JAVA_HOME_WHERE_POSIX = "your shell profile, e.g. ~/.profile, or /etc/environment"
_JAVA_HOME_SPACE = "with a space at its start or end"   # a pasted JAVA_HOME: the JDK is there, the value is not

_MACOS_JAVA_STUB = "/usr/bin/java"               # Apple's stub: $JAVA_HOME's java, else the newest registered JDK
_MACOS_JAVA_HOME_TOOL = "/usr/libexec/java_home"  # lists the JDKs registered under /Library/Java/JavaVirtualMachines
# Homebrew's JDK formulae are keg-only on macOS: not linked onto PATH and not registered with java_home (until the
# user runs the caveat's `sudo ln -sfn`). Looked for under Apple silicon's prefix first, then an Intel Homebrew's.
_MACOS_BREW_PREFIXES = ("/opt/homebrew", "/usr/local")
_MACOS_BREW_KEGS = ("openjdk@21", "openjdk@17", "openjdk")
# what the stub prints (exit 1) when no JDK is installed: macOS 11+, then older releases
_MACOS_NO_RUNTIME = ("Unable to locate a Java Runtime", "No Java runtime present")
_MACOS_JAVA_HOME_REMEDY = "export JAVA_HOME=$(/usr/libexec/java_home -v 21)"
_MACOS_TEMURIN = ("install Temurin 21 (`brew install --cask temurin@21`, or "
                  "https://adoptium.net/temurin/releases/?version=21)")


def _is_macos() -> bool:
    return sys.platform == "darwin"


def _macos_has_java_home_tool() -> bool:
    return os.access(_MACOS_JAVA_HOME_TOOL, os.X_OK)


def _java_version():
    """(major:int, first_line:str) of the Java Neo4j will run, or None if it is missing/broken/unparseable.
    `java -version` prints to STDERR. Parses 'openjdk version "17.0.9"', '"21"', legacy '"1.8.0_x"'.
    macOS: see _macos_java_version (the PATH `java` there is Apple's stub). Every other OS: see _other_java_version
    (JAVA_HOME's java when it is set, else the PATH java, else an installed JDK 21 / 17 found in the usual places,
    at most _JAVA_FIND_MAX_PROBES of them run; a JAVA_HOME whose java is missing, unsupported or broken is passed over
    for a usable JDK when there is one: _java_pass_over). A JDK chosen for Neo4j is the one start_neo4j hands it.

    WHY it returned None is left in _JAVA_DIAG — "missing" | "timeout: ..." | "failed: ..." |
    "unparseable: ..." | "javahome: ..." (JAVA_HOME names no java) — so preflight never tells a user to install a Java
    that IS on PATH (2026-09 #81/#107: on a loaded box `java -version` took >15s and was reported as "no Java on
    PATH"). A timeout is retried once with a longer budget before it counts."""
    global _JAVA_DIAG, _NEO4J_JAVA_HOME, _JAVA_CHECKED, _JAVA_SOURCE, _JAVA_EXE, _JAVA_PASSED_OVER, _JAVA_PASSED_NOTE
    _JAVA_DIAG = None
    _NEO4J_JAVA_HOME = None
    _JAVA_CHECKED = True
    _JAVA_SOURCE = _JAVA_EXE = None
    _JAVA_PASSED_OVER = _JAVA_PASSED_NOTE = None
    if _is_macos():
        return _macos_java_version()
    return _other_java_version()


def _probe_java(exe: str, *, props: bool = False):
    """`exe -version` -> (major, first_line), or None with _JAVA_DIAG saying why (see _java_version). On macOS the
    stub's "no Java runtime" answer is "missing": nothing is installed, nothing on PATH is broken.
    props (a `java` whose JDK home its path does not show: a version manager's shim on macOS, a launcher or shim off
    macOS in _java_pass_over): `exe -XshowSettings:properties -version` instead, still ONE process. The JDK then also
    prints its properties (indented `key = value` lines) before the version lines: its own java.home goes into
    _JAVA_PROBED_HOME (when that home holds bin/java; off macOS also bin\\java.exe on Windows, _java_exe_in), and the
    version is read from the unindented lines only (first_line is the version line)."""
    global _JAVA_DIAG, _JAVA_PROBED_HOME
    _JAVA_DIAG = None
    _JAVA_PROBED_HOME = None
    flags = ["-XshowSettings:properties", "-version"] if props else ["-version"]
    shown = f"`{exe} {' '.join(flags)}`"
    p = None
    for limit in (15, 60):
        try:
            p = subprocess.run([exe, *flags], capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=limit)
            break
        except subprocess.TimeoutExpired:
            continue
        except OSError as exc:
            _JAVA_DIAG = f"failed: {shown} could not be run ({exc})"
            return None
    if p is None:
        _JAVA_DIAG = f"timeout: {shown} timed out (no answer within 15s, nor on a 60s retry)"
        return None
    out = ((p.stderr or "") + "\n" + (p.stdout or "")).strip()
    version_re = r'version "?(\d+)(?:\.(\d+))?'
    if props:
        m, first_line = None, ""
        for ln in out.splitlines():   # the properties are indented; the version lines are not
            m = re.search(version_re, ln) if ln[:1].strip() else None
            if m:
                first_line = ln
                break
        hm = re.search(r"^\s+java\.home = (.+?)\s*$", out, re.M)
        home = hm.group(1) if hm else ""
        if home and (os.path.isfile(os.path.join(home, "bin", "java"))
                     or (not _is_macos() and os.path.isfile(_java_exe_in(home)))):
            _JAVA_PROBED_HOME = home
    else:
        m = re.search(version_re, out)
    if not m:
        if _is_macos() and any(s in out for s in _MACOS_NO_RUNTIME):
            _JAVA_DIAG = "missing"           # Apple's /usr/bin/java stub with no JDK installed
            return None
        rc = getattr(p, "returncode", 0)
        first = next((ln.strip() for ln in out.splitlines() if ln.strip()), "") or "no output"
        _JAVA_DIAG = f"{'failed' if rc else 'unparseable'}: {shown} (rc={rc}) printed: {first}"
        return None
    major = int(m.group(1))
    if major == 1 and m.group(2):        # legacy scheme: 1.8 -> 8
        major = int(m.group(2))
    if props:
        return major, first_line
    lines = out.splitlines()
    return major, (lines[0] if lines else "")


def _macos_jdk_home(version: str | None = None):
    """macOS: (home, None) — the home of the newest JDK registered under /Library/Java/JavaVirtualMachines
    (`/usr/libexec/java_home`), or of the newest one whose version is `version` (`-v 21`); home None when none is
    registered or the tool could not be run. (None, "timeout: ...") when the tool did not answer within 10 s nor on a
    60 s retry (a loaded machine, as for `java -version`, #81/#107): what is registered is then UNKNOWN, never read as
    "nothing registered". With no match the tool may fall back to another version: the caller reads the JDK's own
    -version. The tool only reads that registry — unlike the stub it never offers to install a Java (no --request).
    Never raises."""
    argv = [_MACOS_JAVA_HOME_TOOL] + (["-v", version] if version else [])
    for limit in (10, 60):
        try:
            p = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                               timeout=limit)
            lines = [ln.strip() for ln in (p.stdout or "").splitlines() if ln.strip()]
            if p.returncode != 0 or not lines:
                return None, None
            return (lines[-1] if os.path.isfile(os.path.join(lines[-1], "bin", "java")) else None), None
        except subprocess.TimeoutExpired:
            continue
        except Exception:
            return None, None
    return None, (f"timeout: `{' '.join(argv)}` timed out (no answer within 10s, nor on a 60s retry), so the JDKs "
                  "installed on this Mac could not be listed")


def _macos_brew_jdk_homes() -> list:
    """macOS: the homes of Homebrew's keg-only JDKs that exist — <prefix>/opt/<keg>/libexec/openjdk.jdk/Contents/Home
    for openjdk@21, openjdk@17 and openjdk, under /opt/homebrew, then /usr/local (_MACOS_BREW_PREFIXES /
    _MACOS_BREW_KEGS). The opt/ path, which a `brew upgrade` keeps pointing at the keg's current version. Only
    os.path.isfile is used: no process, no Homebrew command. Never raises."""
    homes = []
    for prefix in _MACOS_BREW_PREFIXES:
        for keg in _MACOS_BREW_KEGS:
            home = os.path.join(prefix, "opt", keg, "libexec", "openjdk.jdk", "Contents", "Home")
            try:
                if os.path.isfile(os.path.join(home, "bin", "java")):
                    homes.append(home)
            except Exception:
                continue
    return homes


def _jdk_home_of(exe: str):
    """The JDK home a `java` on PATH belongs to (<home>/bin/java, symlinks resolved — a Homebrew keg), or None (a
    version manager's shim script). Never raises."""
    try:
        real = os.path.realpath(exe)
        home = os.path.dirname(os.path.dirname(real))
        if os.path.basename(os.path.dirname(real)) == "bin" and os.path.isfile(os.path.join(home, "bin", "java")):
            return home
    except Exception:
        pass
    return None


def _macos_java_version():
    """_java_version on macOS. `java` on a Mac's PATH is normally /usr/bin/java, Apple's stub: it runs $JAVA_HOME's
    java, else the newest JDK registered under /Library/Java/JavaVirtualMachines, and with none exits 1 printing
    "Unable to locate a Java Runtime" (older: "No Java runtime present, requesting install."). Neo4j's launcher, with
    JAVA_HOME unset, picks a JDK by its own macOS rule. So:
      * JAVA_HOME set: its bin/java is checked — what the stub and Neo4j's launcher both run; never overridden.
      * else the first JDK 17 / 21 of: a `java` on PATH that is not the stub (a Homebrew keg, or a version manager's
        shim, whose JDK names its own home: one `-XshowSettings:properties -version`), the newest registered 21, the
        newest registered 17, Homebrew's keg-only openjdk@21 / openjdk@17 / openjdk (_macos_brew_jdk_homes), the
        newest registered JDK. Its home goes into _NEO4J_JAVA_HOME and Neo4j is started with exactly that JAVA_HOME
        (_neo4j_java_env): the JDK this check passed is the JDK Neo4j runs. One whose -version only TIMED OUT ends
        the search too (preflight warns and proceeds with it, as everywhere).
      * java_home itself timed out (after its retry): it is not asked again, and with no 17 / 21 found otherwise
        _JAVA_DIAG is its "timeout: ..." — preflight warns and proceeds: what is installed is unknown, never "none".
      * none: a shim's verdict (the PATH rule: a 17 / 21 that named no home passes, nothing pinned), else the PATH
        JDK's, the newest registered JDK's or a Homebrew keg's verdict (unsupported / broken), else "missing" (no JDK
        that DeepMap or Neo4j can find).
    The stub is never run (with no JDK it can offer an install dialog); every call has a timeout. Without
    /usr/libexec/java_home the PATH rule applies, the stub's "no runtime" words reading as "missing" — and with nothing
    on PATH Homebrew's kegs are still looked for."""
    global _JAVA_DIAG, _NEO4J_JAVA_HOME
    jh = (os.environ.get("JAVA_HOME") or "").strip()
    if jh:
        exe = os.path.join(jh, "bin", "java")
        if not os.path.isfile(exe):
            _JAVA_DIAG = f"javahome: JAVA_HOME is set to {jh}, but {exe} does not exist"
            return None
        return _probe_java(exe)
    path_java = shutil.which("java")
    seen: dict = {}   # JDK home -> (result, diag): each JDK runs -version once

    def probe(home):
        key = os.path.normcase(os.path.realpath(home))
        if key not in seen:
            seen[key] = (_probe_java(os.path.join(home, "bin", "java")), _JAVA_DIAG)
        return seen[key]

    def usable(res, diag) -> bool:
        return (res is not None and res[0] in _JAVA_OK_MAJORS) or (res is None and (diag or "").startswith("timeout"))

    if not _macos_has_java_home_tool():
        res = _probe_java(path_java) if path_java else None
        if path_java and _JAVA_DIAG != "missing":
            return res
        for home in _macos_brew_jdk_homes():   # nothing on PATH (or the stub with no JDK): Homebrew's kegs
            res, diag = probe(home)
            if usable(res, diag):
                _JAVA_DIAG, _NEO4J_JAVA_HOME = diag, home
                return res
        _JAVA_DIAG = "missing"
        return None
    stub = bool(path_java) and (path_java == _MACOS_JAVA_STUB or os.path.realpath(path_java) == _MACOS_JAVA_STUB)
    path_home = _jdk_home_of(path_java) if path_java and not stub else None
    shim = path_java if path_java and not stub and not path_home else None
    shim_res = shim_diag = None
    if shim:   # a version manager's shim: no home in its path, but its JDK names one (java.home) in the same run
        shim_res = _probe_java(shim, props=True)
        shim_diag, shim_home = _JAVA_DIAG, _JAVA_PROBED_HOME
        if shim_res is not None and shim_res[0] in _JAVA_OK_MAJORS and shim_home:
            _JAVA_DIAG, _NEO4J_JAVA_HOME = shim_diag, shim_home
            return shim_res
    listing = {"timeout": None}   # java_home's "timeout: ..." once it ran out of time: it is not asked again

    def registered(version):
        if listing["timeout"]:
            return None
        home, slow = _macos_jdk_home(version)
        listing["timeout"] = slow
        return home

    def candidates():   # looked up lazily, in order: the common Mac (a registered 21) costs two short calls
        yield "path", path_home
        yield "21", registered("21")
        yield "17", registered("17")
        for home in _macos_brew_jdk_homes():
            yield "brew", home
        yield "newest", registered(None)

    newest = brew = None
    for role, home in candidates():
        if not home:
            continue
        newest = home if role == "newest" else newest
        brew = brew or (home if role == "brew" else None)
        res, diag = probe(home)
        if usable(res, diag):
            _JAVA_DIAG, _NEO4J_JAVA_HOME = diag, home
            return res
    if shim and shim_res is not None and shim_res[0] in _JAVA_OK_MAJORS:
        _JAVA_DIAG = shim_diag
        return shim_res   # a 17 / 21 that named no home: the PATH rule, as on every other OS (nothing pinned)
    if listing["timeout"]:
        _JAVA_DIAG = listing["timeout"]   # what is registered is unknown: never "missing", never "unsupported"
        return None
    if shim:
        _JAVA_DIAG = shim_diag
        return shim_res   # the PATH rule's verdict: unsupported, broken, or a -version that timed out
    # what `java -version` shows the user: the PATH JDK, else the stub's (the newest registered); else a keg's
    for home in (path_home, newest, brew):
        if home:
            res, _JAVA_DIAG = probe(home)
            return res
    _JAVA_DIAG = "missing"
    return None


def _other_java_version():
    """_java_version on every OS but macOS. Neo4j 5.26's launcher (Windows: neo4j.bat's Get-Java; POSIX: bin/neo4j)
    runs <JAVA_HOME>/bin/java when JAVA_HOME is set, with no fall-back when it is not there; else, on Windows, a JDK the
    JavaSoft registry names; else the PATH java. So:
      * JAVA_HOME set: its bin/java (bin\\java.exe on Windows) is checked, what Neo4j runs. The path is built from the
        value exactly as set, as Neo4j's launcher builds it (a trailing space a pasted value kept is no JDK there, and
        is named). A 17 / 21 there, or one whose -version only TIMED OUT: that verdict, nothing pinned, Neo4j runs it.
        Missing ("javahome: ..."), unsupported or broken: _java_pass_over hands Neo4j a usable JDK instead when there
        is one (the PATH java's own home, else an installed 21 / 17), else that verdict (the preflight refuses it,
        naming JAVA_HOME).
      * else the PATH java, as always. A 17 / 21, or one whose -version only TIMED OUT: that verdict (one `java
        -version`). Nothing is pinned, so Neo4j is started with its environment unchanged, except on Windows when the
        JavaSoft registry entry Neo4j's launcher takes before PATH names no java or not a 17 / 21
        (_win_registry_override).
      * else (no java on PATH, or one that is unsupported or does not run): the first installed JDK 21, then 17, that
        _find_installed_jdk confirms (at most _JAVA_FIND_MAX_PROBES runs). Its home goes into _NEO4J_JAVA_HOME and
        Neo4j is started with exactly that JAVA_HOME (_neo4j_java_env): the JDK this check passed is the JDK Neo4j
        runs (on Windows a JavaSoft registry entry, an Oracle 19 say, would otherwise win over it). None found: the
        PATH java's verdict (unsupported, broken, or "missing").
    Where the judged java came from is left in _JAVA_SOURCE / _JAVA_EXE (the preflight's words)."""
    global _JAVA_DIAG, _NEO4J_JAVA_HOME, _JAVA_SOURCE, _JAVA_EXE
    raw = os.environ.get("JAVA_HOME") or ""
    if raw.strip():
        exe = _java_exe_in(raw)
        _JAVA_SOURCE, _JAVA_EXE = "javahome", exe
        if not os.path.isfile(exe):
            space = raw != raw.strip() and os.path.isfile(_java_exe_in(raw.strip()))
            if space:
                _JAVA_DIAG = (f'javahome: JAVA_HOME is set to "{raw}", {_JAVA_HOME_SPACE} that Neo4j\'s launcher '
                              f'keeps, so "{exe}" does not exist')
            else:
                _JAVA_DIAG = f"javahome: JAVA_HOME is set to {raw}, but {exe} does not exist"
            return _java_pass_over(raw, exe, None, space=space)
        res = _probe_java(exe)
        timed_out = res is None and (_JAVA_DIAG or "").startswith("timeout")
        if timed_out or (res is not None and res[0] in _JAVA_OK_MAJORS):
            return res
        return _java_pass_over(raw, exe, res)
    exe = shutil.which("java")
    _JAVA_SOURCE, _JAVA_EXE = "path", exe
    res = _probe_java(exe) if exe else None
    if not exe:
        _JAVA_DIAG = "missing"
    elif (res is not None and res[0] in _JAVA_OK_MAJORS) or (res is None and (_JAVA_DIAG or "").startswith("timeout")):
        if res is not None and is_windows():
            home, found = _win_registry_override(exe)
            if found:   # an installed JDK, confirmed by its own -version: that is what Neo4j will run
                _NEO4J_JAVA_HOME, res, _JAVA_DIAG = found
                _JAVA_SOURCE, _JAVA_EXE = "found", _java_exe_in(_NEO4J_JAVA_HOME)
            elif home:  # the PATH java's own JDK home: the java this check just ran
                _NEO4J_JAVA_HOME = home
        return res
    path_diag = _JAVA_DIAG
    found = _find_installed_jdk(skip=exe)
    if found:
        home, res, diag = found
        _JAVA_DIAG, _NEO4J_JAVA_HOME = diag, home
        _JAVA_SOURCE, _JAVA_EXE = "found", _java_exe_in(home)
        return res
    _JAVA_DIAG = path_diag
    return res


def _java_pass_over(raw: str, jh_exe: str, jh_res, *, space: bool = False):
    """_other_java_version, JAVA_HOME set and its java (jh_exe) missing, unsupported (jh_res) or broken: the
    PASS-OVER. A JAVA_HOME whose -version only TIMED OUT never comes here (it warns and proceeds, as before). Up to
    0.6.2 such a machine passed when its PATH java was a 17 / 21 (Neo4j then ran JAVA_HOME's JDK with a warning);
    refusing it would also leave every SessionStart heal refusing and Neo4j down. So a usable JDK is chosen instead:
    (1) the PATH java's JDK: its home is <home>/bin/java(.exe) once links are resolved (_java_own_home; asked plain
    -version), else, for a launcher or shim, the java.home its JVM names in the same single run
    (-XshowSettings:properties); taken when its -version says 17 / 21, or (its own home only) when that -version only
    TIMED OUT and the home's `release` file or name says 17, 21 or nothing (the search's rule); never a 32-bit JDK
    (_jdk_home_facts); else (2) the first installed JDK 21, then 17, that _find_installed_jdk confirms (one whose
    -version only timed out ends the search and is taken: its rule). JAVA_HOME's java and the PATH java never run
    twice. A choice: _NEO4J_JAVA_HOME is its home (source "found"), the JAVA_HOME value exactly as set goes into
    _JAVA_PASSED_OVER and the preflight's NOTE into _JAVA_PASSED_NOTE, and that JDK's result is returned; Neo4j is then
    started with that home in place of exactly that JAVA_HOME value (_neo4j_java_env), and the user's JAVA_HOME itself
    is unchanged. Nothing found: JAVA_HOME's own verdict (jh_res, its diagnosis and source), which the preflight refuses
    naming JAVA_HOME (_other_java_problems), exactly as before. `space`: the value only carries a space at its start
    or end (named in the NOTE; "remove that space" is the advice only when the JDK without it is a 17 / 21:
    _java_home_space_only). Never raises."""
    global _JAVA_DIAG, _NEO4J_JAVA_HOME, _JAVA_SOURCE, _JAVA_EXE, _JAVA_PASSED_OVER, _JAVA_PASSED_NOTE
    jh_diag = _JAVA_DIAG
    choice = None
    try:
        def same(a, b):
            return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))
        judged = [jh_exe] if os.path.isfile(jh_exe) else []   # a java that is not there never ran
        path_exe = shutil.which("java")
        if path_exe and not any(same(path_exe, j) for j in judged):
            judged.append(path_exe)
            home = _java_own_home(path_exe)
            res = _probe_java(path_exe, props=not home)   # a launcher / shim: its JVM names its home in the same run
            diag = _JAVA_DIAG
            own = bool(home)
            if not home and _JAVA_PROBED_HOME and os.path.isfile(_java_exe_in(_JAVA_PROBED_HOME)):
                home = _JAVA_PROBED_HOME
            major, x86 = _jdk_home_facts(home) if home else (None, True)
            if not x86 and res is not None and res[0] in _JAVA_OK_MAJORS:
                choice = home, res, diag
            elif (not x86 and own and res is None and (diag or "").startswith("timeout")
                  and major in (21, 17, None)):
                choice = home, None, diag
        if choice is None:
            choice = _find_installed_jdk(skip=judged)
    except Exception:
        choice = None
    if not choice:
        _JAVA_DIAG = jh_diag   # the probes above left theirs; _JAVA_SOURCE / _JAVA_EXE still name JAVA_HOME's java
        return jh_res
    home, res, diag = choice
    verdict = "cannot use"
    if jh_res is not None:   # a Java that runs: Neo4j 5.26 may still start on it, with its own warning
        what, verdict = f"Java {jh_res[0]} at {jh_exe}", "does not support"
    elif space:
        what = f'no java ("{jh_exe}" does not exist: the value has a space at its start or end)'
    elif not os.path.isfile(jh_exe):
        what = f"no java ({jh_exe} does not exist)"
    else:
        what = f"a java that does not run ({jh_exe})"
    jdk = f"the JDK {res[0]}" if res is not None else "the JDK"
    if space and _java_home_space_only(raw):
        fix = "remove that space from JAVA_HOME"
    else:
        fix = "point JAVA_HOME at a JDK 17 or 21, or unset it"
    _JAVA_DIAG, _NEO4J_JAVA_HOME = diag, home
    _JAVA_SOURCE, _JAVA_EXE = "found", _java_exe_in(home)
    _JAVA_PASSED_OVER = raw
    _JAVA_PASSED_NOTE = (f"[provision] NOTE: JAVA_HOME names {what}, which Neo4j 5.26 {verdict}; DeepMap starts "
                         f"Neo4j with {jdk} at {home} instead (only in Neo4j's own environment; your JAVA_HOME is "
                         f"unchanged). To make this permanent, {fix}.")
    return res


def _jdk_home_facts(home: str):
    """(major, x86) of a JDK home, nothing run: major is what its `release` file says (JAVA_VERSION), else what its
    folder name says, else None (_java_install_candidates' rule); x86 is True for a 32-bit JDK (`release` OS_ARCH in
    _JDK_32BIT_ARCHS: Neo4j's heap does not fit). Never raises."""
    try:
        version, arch = _jdk_release(home)
        major = _java_major_of(version) if version else None
        if major is None:
            major = _java_major_of(os.path.basename(os.path.normpath(home)))
        return major, (arch or "").strip().lower() in _JDK_32BIT_ARCHS
    except Exception:
        return None, False


def _java_home_space_only(raw: str) -> bool:
    """True when the JAVA_HOME value `raw` carries a space at its start or end and the value without it is a 64-bit
    JDK whose `release` file or folder name says 17 or 21 (_jdk_home_facts; nothing run): removing the space is then
    the whole fix. False otherwise (no such space, no java there, another or unknown major: the advice is to point
    JAVA_HOME at a 17 / 21). Never raises."""
    try:
        home = raw.strip()
        if not home or raw == home or not os.path.isfile(_java_exe_in(home)):
            return False
        major, x86 = _jdk_home_facts(home)
        return major in _JAVA_OK_MAJORS and not x86
    except Exception:
        return False


def _java_own_home(exe: str):
    """The JDK home whose launcher `exe` is: <home>/bin/java(.exe) (_java_exe_in) once links are resolved; None for a
    launcher outside any JDK home (Oracle's javapath copy, a scoop or version-manager shim). File checks only, no
    process. Never raises."""
    try:
        home = os.path.dirname(os.path.dirname(os.path.realpath(exe)))
        own = _java_exe_in(home)
        if os.path.isfile(own) and os.path.normcase(os.path.realpath(own)) == os.path.normcase(os.path.realpath(exe)):
            return home
    except Exception:
        pass
    return None


def _win_registry_override(path_exe: str):
    """Windows, JAVA_HOME unset, the PATH java a 17 / 21 (_other_java_version): (home, found) to hand Neo4j, so that it
    runs the Java this check passed. Neo4j's Get-Java takes the JavaSoft registry's pick (_java_registry_pick) BEFORE
    the PATH java, with no fall-back. No pick, a pick that IS the PATH java, or one whose JDK is a 17 / 21 (its
    `release` file, else its key name): Neo4j runs a supported Java, so (None, None): nothing handed over, Neo4j's
    environment as it always was (no process, a registry read and a file read). A pick that names no java (a JDK
    uninstalled since: Neo4j would not start) or another major (an Oracle 19 left registered: Neo4j would run it):
    the PATH java's own JDK home when the PATH java is <home>\\bin\\java.exe once links are resolved, (home, None),
    still no process; else (a launcher outside any JDK: a scoop shim, a javapath copy) the first installed 21 / 17
    _find_installed_jdk confirms, (home, (home, result, diag)); none: (None, None), as before. Never raises."""
    global _JAVA_DIAG
    saved = _JAVA_DIAG
    try:
        pick = _java_registry_pick()
        if not pick:
            return None, None
        key, home = pick
        path_real = os.path.normcase(os.path.realpath(path_exe))
        pick_exe = _java_exe_in(home)
        if os.path.isfile(pick_exe):
            if os.path.normcase(os.path.realpath(pick_exe)) == path_real:
                return None, None
            version, _arch = _jdk_release(home)
            major = _java_major_of(version) if version else None
            if major is None:
                major = _java_major_of(key)
            if major is None or major in _JAVA_OK_MAJORS:
                return None, None
        own = _java_own_home(path_exe)
        if own:
            return own, None
        found = _find_installed_jdk(skip=path_exe)
        if found and found[1] is not None:
            return found[0], found
    except Exception:
        pass
    finally:
        _JAVA_DIAG = saved
    return None, None


def _java_exe_in(home: str) -> str:
    """<home>/bin/java (bin\\java.exe on Windows): the file Neo4j's launcher runs for JAVA_HOME = home."""
    return os.path.join(home, "bin", "java.exe" if is_windows() else "java")


def _java_major_of(text):
    """The Java major a version string or a JDK folder / registry key name carries ('21.0.1', '1.8.0_402' -> 8,
    'jdk-21.0.5.11-hotspot', 'java-21-openjdk-amd64', 'zulu-21', 'jdk21.0.5_11', '21.0.4-tem'), or None."""
    m = _JAVA_MAJOR_RE.search(str(text or ""))
    return int(m.group(1)) if m else None


def _jdk_release(home: str):
    """(JAVA_VERSION, OS_ARCH) from the `release` file at a JDK's root (every JDK 9+ ships one), each None when absent.
    A file read, no process. Never raises."""
    try:
        with open(os.path.join(home, "release"), encoding="utf-8", errors="replace") as fh:
            text = fh.read(16384)
    except Exception:
        return None, None
    ver = re.search(r'^JAVA_VERSION="([^"\r\n]*)"', text, re.M)
    arch = re.search(r'^OS_ARCH="([^"\r\n]*)"', text, re.M)
    return (ver.group(1) if ver else None), (arch.group(1) if arch else None)


def _java_registry_homes() -> list:
    """Windows: [(version key name, JavaHome)] of EVERY version subkey (not only CurrentVersion) under HKLM
    _WIN_JAVASOFT_KEYS, in the 64-bit registry view, then the 32-bit one. Read with winreg (no process); [] on another
    OS, with no such key or on any error. Never raises."""
    out: list = []
    try:
        import winreg
    except Exception:
        return out
    for view in (getattr(winreg, "KEY_WOW64_64KEY", 0), getattr(winreg, "KEY_WOW64_32KEY", 0)):
        for path in _WIN_JAVASOFT_KEYS:
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_READ | view) as key:
                    for i in range(256):
                        try:
                            name = winreg.EnumKey(key, i)
                        except OSError:
                            break
                        try:
                            with winreg.OpenKey(key, name, 0, winreg.KEY_READ | view) as sub:
                                home, _typ = winreg.QueryValueEx(sub, "JavaHome")
                        except OSError:
                            continue
                        if isinstance(home, str) and home.strip():
                            out.append((name, home.strip()))
            except Exception:
                continue
    return out


def _java_registry_pick():
    """Windows: (CurrentVersion, JavaHome) of the JavaSoft registry entry Neo4j 5.26's launcher (Get-Java) runs when
    JAVA_HOME is unset, or None (no such entry: it then runs the PATH java). Get-Java's own order and rules: HKLM
    JavaSoft\\JDK, then \\JRE, in the 64-bit view, then the same two in the 32-bit view (Wow6432Node); in each, the
    CurrentVersion value names a subkey whose JavaHome is taken; a missing value or subkey, or an empty JavaHome, moves
    on to the next key; the first JavaHome found wins, as it is (whether a java is there is not checked: Neo4j does
    not fall back). Read with winreg (no process); None on another OS or on any error. Never raises."""
    try:
        import winreg
    except Exception:
        return None
    for view in (getattr(winreg, "KEY_WOW64_64KEY", 0), getattr(winreg, "KEY_WOW64_32KEY", 0)):
        for path in _WIN_JAVASOFT_KEYS:
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_READ | view) as key:
                    cur, _typ = winreg.QueryValueEx(key, "CurrentVersion")
                    cur = str(cur or "")
                    if not cur:
                        continue
                    with winreg.OpenKey(key, cur, 0, winreg.KEY_READ | view) as sub:
                        home, _typ = winreg.QueryValueEx(sub, "JavaHome")
                home = home if isinstance(home, str) else ""
                if home:
                    return cur, home
            except Exception:
                continue
    return None


def _jdk_dirs_in(root: str, prefix: str = "") -> list:
    """The sub-folders of `root` whose name starts with `prefix` (any case), newest version first (a natural sort:
    jdk-21.0.13 before jdk-21.0.5). [] when `root` is missing or unreadable. Never raises."""
    try:
        names = [n for n in os.listdir(root) if n.lower().startswith(prefix.lower())]
    except Exception:
        return []

    def natural(name):   # isdecimal, not isdigit: int() refuses a superscript '²' that isdigit() takes
        return [(0, int(t), "") if t.isdecimal() else (1, 0, t.lower()) for t in re.split(r"(\d+)", name)]
    try:
        names = sorted(names, key=natural, reverse=True)
    except Exception:
        names = sorted(names, reverse=True)
    out = []
    for name in names:
        path = os.path.join(root, name)
        try:
            if os.path.isdir(path):
                out.append(path)
        except Exception:
            continue
    return out


def _java_install_candidates() -> list:
    """[(major or None, home)]: the JDKs installed in the usual places, in search order, each holding the java Neo4j's
    launcher would run (_java_exe_in) and listed once (symlinks resolved). major is what the JDK's own `release` file
    says (JAVA_VERSION), else what its registry key or folder name says, else None; a 32-bit JDK (`release` OS_ARCH x86)
    is left out. Windows: every JavaHome under HKLM JavaSoft\\JDK / \\JRE in both registry views
    (_java_registry_homes), then each child of _WIN_JDK_VENDOR_DIRS under %ProgramFiles% / %ProgramW6432%, then a
    per-user Temurin under %LOCALAPPDATA%\\Programs\\Eclipse Adoptium. Every other OS: each child of _POSIX_JVM_DIRS.
    Everywhere last: <DEEPMAP_HOME>/jdk* (a JDK unpacked there by hand). Only the registry and the file system are read,
    no process. Never raises."""
    out: list = []
    seen: set = set()

    def add(home, name=None):
        try:
            home = str(home or "").strip()
            if not home or not os.path.isfile(_java_exe_in(home)):
                return   # nothing Neo4j could run there (a registry entry left by an uninstalled JDK, a stray folder)
            key = os.path.normcase(os.path.realpath(home))
            if key in seen:
                return
            seen.add(key)
            version, arch = _jdk_release(home)
            if (arch or "").strip().lower() in _JDK_32BIT_ARCHS:
                return
            major = _java_major_of(version) if version else None
            if major is None:
                major = _java_major_of(name or os.path.basename(os.path.normpath(home)))
            out.append((major, home))
        except Exception:
            return

    try:
        if is_windows():
            for name, home in _java_registry_homes():
                add(home, name)
            roots = []
            for var in ("ProgramFiles", "ProgramW6432"):
                base = (os.environ.get(var) or "").strip()
                if base:
                    roots += [os.path.join(base, vendor) for vendor in _WIN_JDK_VENDOR_DIRS]
            local = (os.environ.get("LOCALAPPDATA") or "").strip()
            if local:
                roots.append(os.path.join(local, "Programs", "Eclipse Adoptium"))
        else:
            roots = [os.path.expanduser(d) for d in _POSIX_JVM_DIRS]
        for root in roots:
            for home in _jdk_dirs_in(root):
                add(home)
        for home in _jdk_dirs_in(str(deepmap_home()), "jdk"):
            add(home)
    except Exception:
        pass
    return out


def _find_installed_jdk(skip: str | None = None):
    """(home, result, diag) of the first installed JDK 21, then 17, whose own -version confirms it; None when there is
    none. The candidates (_java_install_candidates) are ranked by what their `release` file or name says, so no process
    runs for a JDK that is neither 17 nor 21; one whose version nothing names is tried after them. At most
    _JAVA_FIND_MAX_PROBES candidates are run (each `java -version` with _probe_java's 15 s + 60 s retry budget), and one
    whose -version only TIMED OUT ends the search too (as on macOS: preflight warns and proceeds with it), so the worst
    case is one timed-out probe after a few fast failures. `skip`: the java (or list of javas) already judged (the PATH
    one; JAVA_HOME's), never run twice. Never raises."""
    try:
        ranked = sorted((c for c in _java_install_candidates() if c[0] in (21, 17, None)),
                        key=lambda c: {21: 0, 17: 1}.get(c[0], 2))
        skips = [skip] if isinstance(skip, str) else list(skip or ())
        skip_keys = {os.path.normcase(os.path.realpath(s)) for s in skips if s}
        probes = 0
        for _major, home in ranked:
            if probes >= _JAVA_FIND_MAX_PROBES:
                break
            exe = _java_exe_in(home)
            if os.path.normcase(os.path.realpath(exe)) in skip_keys:
                continue
            probes += 1
            res = _probe_java(exe)
            diag = _JAVA_DIAG
            if (res is not None and res[0] in _JAVA_OK_MAJORS) or (res is None and (diag or "").startswith("timeout")):
                return home, res, diag
    except Exception:
        pass
    return None


def _neo4j_java_env(env: dict) -> dict:
    """`env` for a Neo4j process (`neo4j console`, neo4j-admin). JAVA_HOME unset: JAVA_HOME = the JDK home the Java
    preflight chose (_NEO4J_JAVA_HOME), so Neo4j's launcher runs exactly that JDK instead of choosing one by its own
    rule (macOS: the newest registered JDK; Windows: a JavaSoft registry entry before PATH). Off macOS a home is chosen
    only when the PATH java is missing, unsupported or broken and an installed 17 / 21 was found, or (Windows) when the
    registry entry Neo4j would take is not a usable 17 / 21 (_win_registry_override); a machine whose PATH java is a
    17 / 21 and whose registry agrees starts Neo4j with its environment unchanged. JAVA_HOME set: kept, except the one
    value the preflight passed over (_java_pass_over: its java missing, unsupported or broken, a usable JDK chosen),
    which is replaced with the chosen home only when `env` holds EXACTLY that value (a JAVA_HOME that is a usable
    17 / 21, or any other value, is never touched; the user's own environment is not changed). macOS runs the
    preflight's rule here first when this process has not run it; off macOS every Neo4j start already runs the
    preflight first in the same process (_ensure_neo4j, _upgrade_neo4j; a gate locks it), so a process that has not run
    it starts Neo4j as it always did. No JDK chosen: `env` unchanged. Never raises."""
    try:
        cur = env.get("JAVA_HOME")
        if str(cur or "").strip():
            if _NEO4J_JAVA_HOME and _JAVA_PASSED_OVER is not None and cur == _JAVA_PASSED_OVER:
                env["JAVA_HOME"] = _NEO4J_JAVA_HOME
            return env
        if not _JAVA_CHECKED and _is_macos():
            _java_version()
        if _NEO4J_JAVA_HOME:
            env["JAVA_HOME"] = _NEO4J_JAVA_HOME
    except Exception:
        pass
    return env


def _port_in_use(host: str, port: int) -> bool:
    """True iff something is already listening on host:port. (Extracted so tests can stub it.)"""
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            return s.connect_ex((host, port)) == 0
    except OSError:
        return False


def _disk_free_gb(path) -> float:
    """Free GB at `path`, or +inf if the FS can't be probed (don't block on an odd mount)."""
    try:
        return shutil.disk_usage(str(path)).free / (1024 ** 3)
    except OSError:
        return float("inf")


def _java_preflight_problems() -> list:
    """preflight's Java rule: [(message, short form)] for a missing, broken or unsupported Java; [] for Java 17 / 21,
    and for a `java -version` that only TIMED OUT (a loaded machine — the WARNING is printed here). Shared with the
    environment bootstrap, which checks Java before its downloads (deps_bootstrap). macOS has its own wording: a JDK is
    installed (Temurin through Homebrew) rather than put on PATH, and JAVA_HOME is named only when the user set it.
    Every other OS: _other_java_problems; a JAVA_HOME passed over for a usable JDK (_java_pass_over) is no problem: its
    one NOTE line is printed here, once per process (_JAVA_NOTES_SHOWN: setup may run this up to three times)."""
    global _JAVA_DIAG, _JAVA_SOURCE, _JAVA_EXE, _JAVA_PASSED_OVER, _JAVA_PASSED_NOTE
    _JAVA_DIAG = None
    _JAVA_SOURCE = _JAVA_EXE = None
    _JAVA_PASSED_OVER = _JAVA_PASSED_NOTE = None
    jv = _java_version()
    diag = _JAVA_DIAG or "missing"
    mac = _is_macos()
    if not mac and _JAVA_PASSED_NOTE and _JAVA_PASSED_NOTE not in _JAVA_NOTES_SHOWN:
        _JAVA_NOTES_SHOWN.add(_JAVA_PASSED_NOTE)   # once per process: setup may judge Java up to three times
        print(_JAVA_PASSED_NOTE, file=sys.stderr)
    if jv is None and diag.startswith("timeout") and mac and _MACOS_JAVA_HOME_TOOL in diag:
        # java_home itself did not answer: which JDKs are installed is unknown — never "no Java", never "unsupported"
        print(f"[provision] WARNING: {diag.split(': ', 1)[-1]} — the machine is probably under heavy load; continuing "
              "(Neo4j's own launcher picks the JDK). If Neo4j then fails to start, check `/usr/libexec/java_home -V`: "
              "Neo4j 5.26 needs a JDK 17 or 21.", file=sys.stderr)
        return []
    if jv is None and diag.startswith("timeout"):
        where = "installed" if mac or _JAVA_SOURCE in ("javahome", "found") else "on PATH"
        print(f"[provision] WARNING: {diag.split(': ', 1)[-1]}. Java IS {where}, the "
              "machine is probably under heavy load; continuing. If Neo4j then fails to start, check `java -version`.",
              file=sys.stderr)
        return []
    if not mac:
        return _other_java_problems(jv, diag)
    mp = _macos_java_problems(jv, diag)
    if mp is not None:
        return mp
    if jv is None:   # macOS, a JDK that is found but does not run (missing / unsupported are worded above)
        return [(f"Java was found, but {diag.split(': ', 1)[-1]}. Neo4j 5.26 needs a WORKING JDK/JRE 17 or "
                 "21 — fix that Java (or point JAVA_HOME / PATH at a Temurin 21) and re-run.",
                 "java -version failed")]
    return []


def _other_java_problems(jv, diag: str) -> list:
    """_java_preflight_problems' messages on every OS but macOS. The short forms are those of old ("no Java on PATH",
    "Java N unsupported (needs 17/21)", "java -version failed"; plus macOS's "JAVA_HOME has no bin/java"), and every
    remedy works under _other_java_version's rule: a JAVA_HOME the user set is named (_java_home_remedy: point it at a
    17 / 21 or unset it, durably, then restart Claude Code); otherwise no JDK 17 / 21 was found where DeepMap looks, so
    the remedy is to install one (_java_install_text: winget / apt / dnf, or an archive unpacked where DeepMap looks),
    which the next /deep-map:setup finds by itself."""
    from_jh = _JAVA_SOURCE == "javahome"
    install = _java_install_text()
    finds_it = "then re-run /deep-map:setup (DeepMap finds it there itself; no PATH or JAVA_HOME change is needed)"
    tail = diag.split(": ", 1)[-1]
    if jv is None and diag.startswith("javahome"):
        space = _JAVA_HOME_SPACE in diag and _java_home_space_only(os.environ.get("JAVA_HOME") or "")
        remedy = _java_home_remedy(_JAVA_EXE, space=space)
        return [(f"{tail}. Neo4j runs the java JAVA_HOME names, so it would not start. {remedy}",
                 "JAVA_HOME has no bin/java")]
    if jv is None and diag != "missing":
        if from_jh:
            return [(f"Java was found, but {tail}. That is the java JAVA_HOME names, which Neo4j runs, and Neo4j 5.26 "
                     f"needs a WORKING JDK/JRE 17 or 21. {_java_home_remedy(_JAVA_EXE)}", "java -version failed")]
        return [(f"Java was found, but {tail}. Neo4j 5.26 needs a WORKING JDK/JRE 17 or 21, and {_JAVA_NOT_FOUND}. "
                 f"Fix that Java or {install}, {finds_it}.", "java -version failed")]
    if jv is None:
        return [(f"no Java on PATH, and {_JAVA_NOT_FOUND}. Neo4j 5.26 needs a JDK/JRE 17 or 21: {install}, "
                 f"{finds_it}. The Neo4j Community download does not bundle a JRE.", "no Java on PATH")]
    if jv[0] not in _JAVA_OK_MAJORS:
        short = f"Java {jv[0]} unsupported (needs 17/21)"
        found = f"Java {jv[0]} found" + (f" at {_JAVA_EXE}" if _JAVA_EXE else "") + f" ({jv[1]!r})"
        if from_jh:
            return [(f"{found}, the java JAVA_HOME names, which Neo4j runs; Neo4j 5.26 needs 17 or 21. "
                     f"{_java_home_remedy(_JAVA_EXE)}",
                     short)]
        return [(f"{found}, but Neo4j 5.26 needs 17 or 21, and {_JAVA_NOT_FOUND}. {install[:1].upper()}{install[1:]}, "
                 f"{finds_it}.", short)]
    return []


def _java_install_text() -> str:
    """The install remedy off macOS (lower-case, to follow "...: " or "Fix that Java or "): Temurin 21 through winget on
    Windows (or its .msi), apt / dnf elsewhere; on both, the adoptium.net archive unpacked so its jdk-21 folder sits in
    <DEEPMAP_HOME>, the one place an unpacked JDK is looked for (_java_install_candidates). Each lands where the next
    /deep-map:setup finds it with no PATH or JAVA_HOME change. Never raises."""
    try:
        home = str(deepmap_home())
    except Exception:
        home = "~/.deepmap"
    where = f"unpacked so its jdk-21 folder sits directly in {home}"
    if is_windows():
        return (f"install Temurin 21 (`{_JAVA_WINGET}`, or the .msi from {_JAVA_ADOPTIUM}; a .zip from there works "
                f"too, {where})")
    return ("install Java 21 (Debian / Ubuntu: `sudo apt install openjdk-21-jre-headless`, on Debian 12 "
            "`openjdk-17-jre-headless`; Fedora / RHEL: `sudo dnf install java-21-openjdk-headless`; or Temurin 21's "
            f".tar.gz from {_JAVA_ADOPTIUM}, {where})")


def _java_home_remedy(skip_exe, *, space: bool = False) -> str:
    """The remedy when the java JAVA_HOME names is missing, unsupported or broken (off macOS). Point JAVA_HOME at a 17 /
    21, naming one that is installed (_java_install_candidates: its `release` file or name, nothing run; `skip_exe`,
    JAVA_HOME's own java, is never named), or say none was found and give the install command first; or unset it.
    `space`: the value only carries a space Neo4j's launcher keeps and the JDK without it is a 17 / 21
    (_java_home_space_only): remove it. Then how the change reaches DeepMap: made where JAVA_HOME is set, not for one
    command, and Claude Code restarted, since a running Claude Code and the SessionStart heal it starts keep the
    JAVA_HOME they started with (a value set for one command passes that setup and leaves every later heal refusing).
    Never raises."""
    hint = None
    if not space:
        try:
            skip = os.path.normcase(os.path.realpath(skip_exe)) if skip_exe else None
            found = [(m, h) for m, h in _java_install_candidates() if m in _JAVA_OK_MAJORS
                     and os.path.normcase(os.path.realpath(_java_exe_in(h))) != skip]
            found.sort(key=lambda c: -c[0])   # 21 before 17, in search order within a major
            hint = found[0] if found else None
        except Exception:
            hint = None
    if space:
        target = "Remove that space from JAVA_HOME."
    elif hint:
        target = (f"Point JAVA_HOME at a JDK 17 or 21 (a JDK {hint[0]} is installed at {hint[1]}), or unset it "
                  "(DeepMap then finds an installed 17 / 21 itself).")
    else:
        target = ("Point JAVA_HOME at a JDK 17 or 21, or unset it (DeepMap then finds an installed 17 / 21 itself); "
                  f"{_JAVA_NOT_FOUND}, so first {_java_install_text()}.")
    if is_windows():
        where, restart = _JAVA_HOME_WHERE_WIN, "restart Claude Code (and VS Code, when it runs there)"
    else:
        where, restart = _JAVA_HOME_WHERE_POSIX, "restart Claude Code from a new terminal"
    return (f"{target} Change it where it is set ({where}), not for one command only: a running Claude Code, and the "
            f"SessionStart heal it starts, keep the JAVA_HOME they started with, so {restart}, then re-run "
            "/deep-map:setup.")


def _macos_java_problems(jv, diag: str):
    """_java_preflight_problems' macOS messages (the same short forms as elsewhere, except "no JDK 17 or 21 found" and
    "JAVA_HOME has no bin/java"); None for a case worded as on every OS (a JDK that is found but does not run)."""
    if jv is None and diag.startswith("javahome"):
        return [(f"{diag.split(': ', 1)[-1]} — Neo4j runs the Java JAVA_HOME names. Point JAVA_HOME at a JDK 17 or 21 "
                 f"(`{_MACOS_JAVA_HOME_REMEDY}`) or unset it (DeepMap then picks a registered 17 / 21 itself), then "
                 "re-run /deep-map:setup.", "JAVA_HOME has no bin/java")]
    if jv is None and diag == "missing":
        return [("no JDK 17 or 21 that DeepMap or Neo4j can find is installed on this Mac: none is registered with "
                 "/usr/libexec/java_home (/Library/Java/JavaVirtualMachines), none is on PATH (/usr/bin/java is "
                 "Apple's stub: it runs a JDK only once one is installed), and Homebrew holds no openjdk@21 / "
                 f"openjdk@17 keg. Neo4j 5.26 needs a JDK 17 or 21 — {_MACOS_TEMURIN}, then re-run /deep-map:setup; "
                 "DeepMap finds it through /usr/libexec/java_home, no JAVA_HOME needed. (The Neo4j Community download "
                 "does not bundle a JRE.)", "no JDK 17 or 21 found")]
    if jv is not None and jv[0] not in _JAVA_OK_MAJORS:
        short = f"Java {jv[0]} unsupported (needs 17/21)"
        if (os.environ.get("JAVA_HOME") or "").strip():
            return [(f"Java {jv[0]} found ({jv[1]!r}) — the JDK JAVA_HOME names — but Neo4j 5.26 needs 17 or 21. "
                     f"Point JAVA_HOME at one (`{_MACOS_JAVA_HOME_REMEDY}`) or unset it (DeepMap then picks a "
                     f"registered 17 / 21 itself), then re-run /deep-map:setup. With no 21 installed, first "
                     f"{_MACOS_TEMURIN}.", short)]
        return [(f"Java {jv[0]} found ({jv[1]!r}), but Neo4j 5.26 needs 17 or 21, and no working JDK 17 or 21 is "
                 f"registered on this Mac (/usr/libexec/java_home -v 21 / -v 17) or held in a Homebrew openjdk@21 / "
                 f"openjdk@17 keg. I{_MACOS_TEMURIN[1:]}, then re-run /deep-map:setup; DeepMap finds it there itself, "
                 "no JAVA_HOME needed.", short)]
    return None


def preflight(version: str | None = None, *, bolt: int | None = None, http: int | None = None,
              problems: list | None = None) -> bool:
    """Fail-fast checks before downloading/starting a LOCAL Neo4j: a supported JRE (17/21), free
    bolt/http ports, and enough disk. Returns True to proceed; else prints an actionable one-liner
    per problem and returns False (a short form of each is appended to `problems` when given). Call ONLY
    when about to provision (after the is_bolt_up short-circuit) — the bring-your-own-Neo4j path must
    not be forced through these. `bolt`/`http` default to the base (7687/7474); a per-project instance
    passes its ALLOCATED pair. A `java -version` that merely TIMED OUT (a loaded machine) warns and
    proceeds — Java is on PATH, and the start itself surfaces a real JVM problem."""
    version = version or neo4j_version()
    ok = True
    problems = problems if problems is not None else []

    def _fail(msg: str, short: str) -> None:
        nonlocal ok
        print(f"[provision] PREFLIGHT FAILED: {msg}", file=sys.stderr)
        problems.append(short)
        ok = False

    for msg, short in _java_preflight_problems():
        _fail(msg, short)

    for port, what in ((bolt or 7687, "Bolt"), (http or 7474, "HTTP")):
        if _port_in_use("127.0.0.1", port):
            _fail(f"port {port} ({what}) is already in use — another "
                  "process, or a Neo4j with different credentials. Stop it, or set NEO4J_URI / "
                  "NEO4J_PASSWORD to match your existing instance.", f"port {port} ({what}) in use")

    try:
        cache = paths(version)["cache"]
        cache.mkdir(parents=True, exist_ok=True)
        free = _disk_free_gb(cache)
        if free < 1.5:
            _fail(f"only {free:.1f} GB free at {cache} — Neo4j needs "
                  "~1 GB (tarball + extracted store). Free some space and re-run.", "low disk")
    except Exception:
        pass  # disk probe is best-effort — never block provisioning on an odd FS

    try:
        # T6#9: Windows without long-path support can't extract Neo4j under a long DEEPMAP_HOME (its longest archive
        # member adds ~90 characters) — say so BEFORE the ~180 MB download, not as a failed extraction after it
        if is_windows() and not _shared_binary_present(version) and not _win_long_paths_enabled():
            home = paths(version)["home"]
            need = len(str(home / "neo4j")) + 1 + _NEO4J_ARCHIVE_LONGEST
            if need >= _WIN_MAX_PATH:
                _fail(f"DEEPMAP_HOME ({home}, {len(str(home))} characters) is too long for Windows' "
                      f"{_WIN_MAX_PATH}-character MAX_PATH: extracting Neo4j there needs {need}-character paths. Set "
                      "DEEPMAP_HOME to a short path (e.g. C:\\dm) or enable Win32 long paths (LongPathsEnabled=1), "
                      "then re-run /deep-map:setup.", "DEEPMAP_HOME too long for Windows MAX_PATH")
    except Exception:
        pass  # a path-length probe is best-effort

    return ok


_WIN_MAX_PATH = 260
# The Windows zip's longest member path — re-measured on each pinned archive: 89 in 5.26.0
# (neo4j-community-5.26.0/lib/listenablefuture-9999.0-empty-to-avoid-conflict-with-guava.jar) and 89 in 5.26.32
# (neo4j-community-5.26.32/lib/netty-transport-native-epoll-4.1.138.Final-linux-aarch_64.jar). Windows only (the
# unix tarball's is 94, but long paths are no limit there).
_NEO4J_ARCHIVE_LONGEST = 89


def _win_long_paths_enabled() -> bool:
    """True iff Windows long-path support is on (HKLM\\SYSTEM\\CurrentControlSet\\Control\\FileSystem
    LongPathsEnabled = 1 — Python honours it). False when off or unreadable (and on every other OS). Never raises."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem") as key:
            value, _typ = winreg.QueryValueEx(key, "LongPathsEnabled")
        return int(value) == 1
    except Exception:
        return False


# ---- dedicated OS locks (start / download serialisation) ----------------------------------------
def _try_os_lock(handle) -> bool:
    """Non-blocking exclusive OS lock on an open handle (msvcrt byte-lock / flock — the kernel releases
    it if the holder dies, so a crash never wedges the next caller). True iff acquired."""
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


def _file_lock(path: Path, wait_s: float):
    """Bounded-blocking acquire of a DEDICATED lock file. Returns (handle, how): how is "ok" (taken at
    once), "waited" (another holder finished first — re-check the state it may have produced),
    "timeout" (handle None), or "unavailable" (the file can't be opened — handle None; callers proceed
    unlocked, best-effort). Release with _file_unlock. Never raises."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(path, "a+")
    except OSError:
        return None, "unavailable"
    deadline = time.monotonic() + max(0.0, float(wait_s))
    waited = False
    while True:
        if _try_os_lock(handle):
            return handle, ("waited" if waited else "ok")
        if time.monotonic() >= deadline:
            try:
                handle.close()
            except OSError:
                pass
            return None, "timeout"
        waited = True
        time.sleep(0.1)


def _file_unlock(handle) -> None:
    if handle is None:
        return
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
    try:
        handle.close()
    except OSError:
        pass


def _install_failed_text(exc) -> str:
    """Actionable text for a failed download/extract of the shared Neo4j binary (never a raw traceback).
    On Windows a long DEEPMAP_HOME overflows MAX_PATH inside the archive (its longest member adds ~100
    characters), which surfaces as a bare FileNotFoundError from zipfile."""
    msg = f"installing the Neo4j binary failed ({type(exc).__name__}: {exc})"
    home = str(deepmap_home())
    if is_windows() and isinstance(exc, OSError) and len(home) + 100 >= 260:
        msg += (f" — DEEPMAP_HOME ({len(home)} characters) plus Neo4j's longest file path exceeds Windows' "
                "260-character MAX_PATH: set DEEPMAP_HOME to a short path (e.g. C:\\dm) or enable Win32 "
                "long paths, then re-run /deep-map:setup")
    return msg


def ensure_neo4j(*, uri: str | None = None, user: str | None = None,
                 password: str | None = None, version: str | None = None,
                 project_key: str | None = None, bolt: int | None = None, http: int | None = None,
                 timeout: int = 120, create: bool = True, diag: dict | None = None) -> bool:
    """Ensure a Neo4j is up on `uri` with these creds — download/configure/start if needed.

    Short-circuits if Bolt is already reachable (idempotent). Returns True when reachable. When
    `project_key` is set, provisions a PER-PROJECT instance (own conf dir via NEO4J_CONF, own data
    dir + auth store, own bolt/http ports) so several projects run side-by-side with isolated graphs;
    `project_key=None` is the legacy single-instance path (byte-identical to before).

      * A Neo4j that answers but REJECTS the credentials is reported as that (never "port in use").
      * `create=False` (heal) only REVIVES an existing store — it never provisions one from scratch, so a
        removed or never-set-up project gets "run /deep-map:setup", not a fresh empty instance.
      * Everything past the fast path runs under a per-INSTANCE start lock: two concurrent setups/heals
        of one project launch ONE Neo4j (the loser used to overwrite run/neo4j.pid with its dead
        launcher's pid, so `stop` could no longer stop the real JVM).
      * A failed set-initial-password aborts BEFORE the first start (see set_initial_password).
    `diag` (optional dict) receives {"reason": <one line>} whenever it returns False (the SHORT form for
    a credentials rejection — it is persisted in the heal status file), and {"started": True} whenever THIS
    call launched the instance (whatever it returns — a caller that started a store only to read its owner
    stops it again; a store a concurrent setup/heal started is not this call's to stop).

    Never raises (ISS-build:023): a download that breaks off mid-stream (http.client.IncompleteRead), a corrupt
    archive (tarfile / zipfile errors), a console log or launcher that can't be opened / executed, or anything
    else unexpected is a False with its one-line reason in `diag` — never a raw traceback out of setup / `ensure`
    (which print a FAILED verdict instead). A Windows launch whose pid file can't be written is stopped again by
    start_neo4j rather than left running unrecorded."""
    diag = diag if diag is not None else {}
    try:
        return _ensure_neo4j(uri=uri, user=user, password=password, version=version, project_key=project_key,
                             bolt=bolt, http=http, timeout=timeout, create=create, diag=diag)
    except Exception as exc:  # noqa: BLE001 — "never raises" is this function's contract (ISS-build:023)
        diag["reason"] = " ".join(f"provisioning this project's Neo4j failed unexpectedly ({type(exc).__name__}: "
                                  f"{exc})".split())
        print(f"[provision] ERROR: {diag['reason']}", file=sys.stderr)
        return False


def _ensure_neo4j(*, uri, user, password, version, project_key, bolt, http, timeout, create, diag) -> bool:
    """ensure_neo4j's body (see there) — ensure_neo4j turns anything this raises into a False + diag reason."""
    uri = uri or os.environ.get("NEO4J_URI", DEFAULT_URI)
    user = user or os.environ.get("NEO4J_USER", DEFAULT_USER)
    password = password or os.environ.get("NEO4J_PASSWORD", DEFAULT_PASSWORD)
    if version is None:
        _warn_ignored_pin(project_key)                       # a NEO4J_VERSION that would downgrade / orphan the store
    version = version or store_neo4j_version(project_key)   # sticky: an existing store keeps its version
    diag = diag if diag is not None else {}

    def _fail(reason: str, short: str | None = None) -> bool:
        diag["reason"] = " ".join(str(short or reason).split())   # ONE line: it is persisted (heal status, verdicts)
        print(f"[provision] ERROR: {reason}", file=sys.stderr)
        return False

    absent = _missing_modules(("neo4j",))
    if absent:
        # no driver: every probe below would read as "down" and provision + start a JVM it can never reach
        return _fail(_missing_packages_text(absent), f"DeepMap's Python packages are missing ({', '.join(absent)} "
                                                     f"is not installed for {sys.executable})")
    state = _bolt_state(uri, user, password)
    if state == "up":
        print(f"[provision] Neo4j already up at {uri}")
        return True
    if state == "auth":
        return _fail(_auth_failed_text(uri, project_key), _auth_failed_reason(uri))
    if not _is_initialized(version, project_key):
        # an upgrade of this store still open (a rollback cut short): its graph may sit in the pre-upgrade copy —
        # never "never set up here", never a fresh empty store over it (the next setup's recovery restores it)
        open_upgrade = _open_upgrade_problem(project_key)
        if open_upgrade:
            return _fail(open_upgrade)
        if not create:
            return _fail(f"no DeepMap Neo4j store exists for this project ({uri}) — it was never set up here, or "
                         "was removed by instance-rm; heal only revives an existing store: run /deep-map:setup")

    lock, how = _file_lock(deepmap_home() / "run" / f"neo4j-start-{project_key or 'base'}.lock",
                           wait_s=timeout + 900)   # a holder may be downloading (<=600s) then starting
    if how == "timeout":
        return _fail(f"another DeepMap process is still starting this Neo4j instance ({uri}) — retry shortly")
    try:
        if how == "waited" and is_bolt_up(uri, user, password):
            print(f"[provision] Neo4j already up at {uri} (started by a concurrent setup/heal)")
            return True
        # Fail fast (P3) on a missing/wrong Java, an occupied port, or low disk — BEFORE the ~180MB
        # download + a 120s bolt-timeout. Only on this provision-a-local-instance path.
        problems: list = []
        if not preflight(version, bolt=bolt, http=http, problems=problems):
            diag["reason"] = "preflight failed: " + ("; ".join(problems) or "see the PREFLIGHT lines above")
            return False

        where = f" (project instance {project_key[:8]}, bolt={bolt})" if project_key else ""
        if _is_initialized(version, project_key) and _shared_binary_present(version):
            # T10d-W: a plain restart of an existing store (heal after a reboot, a hibernated instance) is not a
            # provisioning — nothing is downloaded or created
            print(f"[provision] Neo4j not running at {uri} — starting the existing instance ({version}){where}")
        else:
            print(f"[provision] Neo4j not reachable at {uri} — provisioning {version}{where}")
        try:
            p = paths(version)                       # shared binary/cache paths
            p["cache"].mkdir(parents=True, exist_ok=True)
            _download_extract_shared(version)        # serialized: one binary for all instances
        except Exception as exc:  # noqa: BLE001 — ISS-build:023: IncompleteRead, tarfile / zipfile errors too
            return _fail(_install_failed_text(exc))
        configure_neo4j(version, project_key=project_key, bolt=bolt, http=http)
        if not set_initial_password(version, password, project_key):
            return _fail("set-initial-password failed (see above) — Neo4j was NOT started: a first start "
                         "without it would initialise this store with the default neo4j/neo4j credentials "
                         "and lock DeepMap out. Fix the cause, then re-run /deep-map:setup (the store is "
                         "still fresh).")
        try:
            start_neo4j(version, project_key)
        except Exception as exc:  # noqa: BLE001 — ISS-build:023: a start that failed is a verdict, not a traceback
            return _fail(f"could not start Neo4j{where} ({type(exc).__name__}: {exc}) — see "
                         f"{paths(version, project_key)['log']}")
        diag["started"] = True   # THIS call launched it (a caller that started a store only to read it stops it)
        if not wait_for_bolt(uri, user, password, timeout=timeout):
            if _bolt_state(uri, user, password) == "auth":
                return _fail(_auth_failed_text(uri, project_key), _auth_failed_reason(uri))
            return _fail(f"Neo4j did not become reachable within {timeout}s "
                         f"(see {paths(version, project_key)['log']})")
        if not is_windows():
            _record_neo4j_server_identity(version, project_key)   # POSIX: Neo4j named its server JVM (I4)
        print(f"[provision] Neo4j is up at {uri}")
        return True
    finally:
        _file_unlock(lock)


def _download_extract_shared(version: str) -> None:
    """Download + extract the SHARED Neo4j binary once, serialized under its OWN download lock (NOT the
    registry lock: holding that across a ~180 MB download + a multi-second archive re-verification
    starved every concurrent port allocation into a silent fallback to another project's base ports —
    2026-09 live validation, E7/A4). An already-extracted binary (completion sentinel) returns at once:
    no lock, no archive re-verification. The lock LOSER finds the winner's completed `dest_zip` and
    `download_neo4j`'s cache gate short-circuits (no second download). Best-effort — if the lock can't
    be taken it still proceeds (`download_neo4j`'s own unique-temp + checksum keep it crash-safe and the
    extract sentinel-written-LAST guards a half-tree). Never raises beyond download/extract."""
    p = paths(version)
    if (p["neo4j_home"] / ".deepmap-extracted").exists():
        return
    lock, _how = _file_lock(p["cache"] / "neo4j-download.lock", wait_s=620)   # ~ urlopen timeout + slack
    try:
        download_neo4j(version, p["zip"])
        extract_neo4j(p["zip"], version)
    finally:
        _file_unlock(lock)


# ---- Neo4j PATCH upgrade: the ONE place an existing store changes version -------------------------------------
# Versions are STICKY (store_neo4j_version): a newer DEFAULT_VERSION gives NEW stores the new release, while an
# existing store keeps running the version it runs — heal, the hooks, status, stop, hibernate and instance-rm never
# change it. upgrade_neo4j moves THIS project's own store (its per-project instance, or the base store it provably
# owns) to a newer PATCH release of the same line — setup runs it by default (DEEPMAP_NEO4J_AUTO_UPGRADE=0: never),
# `provision.py neo4j-upgrade` on request:
#   1. nothing changes until the new binary is downloaded + verified, Java and disk / path budget are checked, and
#      this instance's start lock and sync's per-instance write lock are held (no start, heal or sync runs meanwhile);
#   2. the graph's answers on the OLD version (counts, owner, ONLINE indexes, one index query) are recorded;
#   3. a journal is written, the old Neo4j is stopped through stop_neo4j (owner-proven) and its JVM must be gone;
#   4. the store is COPIED: an instance's data dir to a backup next to it (the new version opens it in place), the
#      base store into the new version's folder (it lives inside its version's folder; the original stays put);
#   5. the new version starts and must give the same answers: then the old copy is removed (base: the version record
#      is written first) — otherwise it is stopped again, the original store is put back and the OLD version restarts
#      and must give the same answers ("rolled-back"). A crash leaves the journal: the store stays the old version's
#      until "verified", and the next setup finishes or undoes it (recover_neo4j_upgrade) before it starts Neo4j.
# Every phase is written before the step it names, and a phase that can't be written stops the upgrade there (back to
# the old release). A journal is closed only once the disk, without it, resolves the store to the release it is left
# on (port_registry.settled_store_version: the base version record / the folder holding the base store, or the
# instance's conf stamp), and an old copy is deleted only after that. A rolled-back automatic upgrade is recorded
# (_UPGRADE_FAILURE_FILE): setup does not retry the same release for _UPGRADE_RETRY_AFTER_S; the verb always tries.
NEO4J_AUTO_UPGRADE_ENV = "DEEPMAP_NEO4J_AUTO_UPGRADE"   # "0" / "false" / "no" / "off": setup never upgrades
_UPGRADE_START_TIMEOUT_S = 180     # the first start of a version on a copied store (a busy box needs > 120 s)
_UPGRADE_INDEX_WAIT_S = 180        # for every index ONLINE before to be ONLINE again
_UPGRADE_STOP_WAIT_S = 90          # for a stopped JVM to release the store (Windows keeps handles a few seconds)
_UPGRADE_LOCK_WAIT_S = 30          # for this instance's start lock (a concurrent start / heal finishing)
_UPGRADE_DISK_MARGIN = 512 * 1024 ** 2
_UPGRADE_ARCHIVE_BYTES = 700 * 1024 ** 2   # a download not yet made: the archive (~153 MB) + its extraction
_UPGRADE_RETRY_AFTER_S = 7 * 86400  # setup's next automatic attempt at a release whose upgrade was rolled back
_UPGRADE_FAILURE_FILE = "deepmap-neo4j-upgrade-failed.json"   # next to the store's upgrade journal
_PREFERRED_PROBE_INDEX = "deepmap_code_fts"
_LAST_UPGRADE: dict = {}           # the last upgrade's report (main's exit code; the gates)


def neo4j_upgrade_target(current, target=None) -> str | None:
    """`target` (default DEFAULT_VERSION) when it is a NEWER PATCH release of `current`'s major.minor line, else None:
    only a patch release is an upgrade here (5.26.0 -> 5.26.32 keeps the store format); a newer minor / major, an equal
    or older version, or anything that is not a clean X.Y.Z never is. Never raises."""
    try:
        import port_registry as PR
        t = target or DEFAULT_VERSION
        c, n = PR.neo4j_version_tuple(current), PR.neo4j_version_tuple(t)
        if not c or not n or c[:2] != n[:2] or n <= c:
            return None
        return PR.clean_neo4j_version(t)
    except Exception:
        return None


def neo4j_upgrade_available(project_key: str | None = None):
    """(current, target) when THIS store (the base store for project_key None) exists, NEO4J_VERSION does not pin it,
    no upgrade of it is open, and DEFAULT_VERSION is a newer patch release of the version it runs; else None. File reads
    only (no Neo4j, no process probe). Never raises."""
    try:
        import port_registry as PR
        if PR.neo4j_version_pin() or PR.read_upgrade_journal(project_key):
            return None
        cur = store_neo4j_version(project_key)
        if not _is_initialized(cur, project_key):
            return None
        tgt = neo4j_upgrade_target(cur)
        return (cur, tgt) if tgt else None
    except Exception:
        return None


def _auto_upgrade_enabled() -> bool:
    return str(os.environ.get(NEO4J_AUTO_UPGRADE_ENV, "")).strip().lower() not in ("0", "false", "no", "off")


def _upgrade_cmd_hint(root=None) -> str:
    """The copy-pasteable `provision.py neo4j-upgrade "<project folder>"` for a hint: with the project's folder when the
    caller knows it (the bare verb acts on the folder it is run from), else the bare command "(from this project's
    folder)", like the sibling hints. Never raises."""
    try:
        if root:
            return _provision_cmd(f'neo4j-upgrade "{Path(str(root)).resolve().as_posix()}"')
    except Exception:
        pass
    return f"{_provision_cmd('neo4j-upgrade')} (from this project's folder)"


def _registry_root(project_key) -> str | None:
    """The project folder the port registry records for instance `project_key` (its entry's root), or None (the base
    store, no entry, an unreadable registry). File read only. Never raises."""
    try:
        if not project_key:
            return None
        import port_registry as PR
        ent = ((PR.load_registry() or {}).get("projects") or {}).get(project_key)
        root = ent.get("root") if isinstance(ent, dict) else None
        return str(root) if root else None
    except Exception:
        return None


def neo4j_upgrade_note(project_key: str | None = None, root=None) -> str | None:
    """One status / doctor line when a newer patch release is available for THIS store (neo4j_upgrade_available), else
    None. `root`: the project's folder, quoted in the neo4j-upgrade command (_upgrade_cmd_hint). Never raises."""
    try:
        avail = neo4j_upgrade_available(project_key)
        if not avail:
            return None
        cur, tgt = avail
        fail = _recent_upgrade_failure(project_key, cur, tgt)
        if fail:
            how = (f"an upgrade to it was rolled back on {str(fail.get('ts') or '?')[:10]} ({fail.get('reason')}), so "
                   f"/deep-map:setup does not retry it before {_retry_date(fail)}")
        elif _auto_upgrade_enabled():
            how = "/deep-map:setup upgrades it"
        else:
            how = f"{NEO4J_AUTO_UPGRADE_ENV}=0 keeps /deep-map:setup from upgrading it"
        return (f"Neo4j {cur}: patch release {tgt} is available (security fixes, same store format) — {how}, or run "
                f"{_upgrade_cmd_hint(root)}; the upgrade is checked against the current graph and rolled back on "
                "failure")
    except Exception:
        return None


def neo4j_upgrade_note_for(target) -> str | None:
    """neo4j_upgrade_note for `target`'s project, READ-ONLY (for the CLI's doctor / status): its route by
    port_registry.resolve_neo4j_route — its own instance, or the base store when the base pair is registered to it (a
    legacy route only when the base store's owner record names it); none for an operator NEO4J_URI or no route. No
    port allocation, no Neo4j connection. Never raises."""
    try:
        if _USER_SET_NEO4J_URI:
            return None
        import port_registry as PR
        r = PR.resolve_neo4j_route(target, honor_env=False)
        if r.source in ("registry", "instance-conf") and r.pair:
            base = (r.pair[0], r.pair[1]) == (PR.BASE_BOLT, PR.BASE_HTTP)
            return neo4j_upgrade_note(None if base else (r.key or PR.project_key(target, migrate=False)), root=target)
        if r.source == "legacy-unverified" and _base_owner_is(target):
            return neo4j_upgrade_note(None, root=target)
    except Exception:
        pass
    return None


def _store_size(data_dir, deadline_s: float = 120.0):
    """(bytes, longest path relative to `data_dir`) of a store, or None when it can't be walked within `deadline_s`.
    Never raises."""
    total, longest = 0, 0
    try:
        base = str(data_dir)
        end = time.monotonic() + deadline_s
        for dirpath, _dirs, files in os.walk(base):
            if time.monotonic() > end:
                return None
            rel = os.path.relpath(dirpath, base)
            for f in files:
                try:
                    total += os.stat(os.path.join(dirpath, f), follow_symlinks=False).st_size
                except OSError:
                    pass
                longest = max(longest, len(os.path.normpath(os.path.join(rel, f))))
        return total, longest
    except Exception:
        return None


def _copy_store(src: Path, dst: Path, attempts: int = 5) -> None:
    """Copy a STOPPED store's data dir `src` to `dst` (created, or merged into an empty skeleton). A file a just-stopped
    JVM still holds (Windows) is retried for a few seconds. Raises on failure."""
    for i in range(attempts):
        try:
            shutil.copytree(str(src), str(dst), symlinks=True, dirs_exist_ok=True)
            return
        except (OSError, shutil.Error):
            if i + 1 >= attempts:
                raise
            time.sleep(2.0)


def _free_sibling(data: Path, name: str) -> Path:
    """data.parent/<name> — short on purpose (a store's own paths are long, and Windows without long paths stops at 260
    characters) — or <name>-<UTC timestamp> when that name is already taken (never an existing directory)."""
    p = Path(data).parent / name
    if not p.exists():
        return p
    return Path(data).parent / f"{name}-{time.strftime('%Y%m%dT%H%M%S', time.gmtime())}"


def _move_dir(src: Path, dst: Path, wait_s: float = 20.0) -> bool:
    """os.replace a directory, retried while a just-stopped JVM still holds a file in it (Windows). True iff moved.
    Never raises."""
    end = time.monotonic() + wait_s
    while True:
        try:
            os.replace(str(src), str(dst))
            return True
        except OSError:
            if time.monotonic() >= end:
                return False
            time.sleep(0.5)


def _write_json_atomic(path: Path, rec: dict) -> bool:
    """temp + os.replace in the same dir; False when it could not be written. Never raises."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".deepmap-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                json.dump(rec, f)
            os.replace(tmp, path)
            tmp = None
        finally:
            if tmp is not None:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
        return True
    except Exception:
        return False


def _write_upgrade_journal(project_key, rec: dict) -> bool:
    import port_registry as PR
    return _write_json_atomic(PR.upgrade_journal_path(project_key), rec)


def _clear_upgrade_journal(project_key) -> None:
    try:
        import port_registry as PR
        PR.upgrade_journal_path(project_key).unlink(missing_ok=True)
    except Exception:
        pass


def _journal_path_text(project_key) -> str:
    try:
        import port_registry as PR
        return str(PR.upgrade_journal_path(project_key))
    except Exception:
        return "(the upgrade journal)"


def _write_base_version_record(version: str) -> bool:
    """Record the version the base store runs (<home>/neo4j/deepmap-base-version.json) — written once an upgrade of it
    is verified; port_registry.base_store_version reads it."""
    import port_registry as PR
    return _write_json_atomic(PR.base_version_record_path(),
                              {"version": str(version), "source": "neo4j-upgrade",
                               "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})


def _journal_copy(key, journal: dict):
    """Where an upgrade journal's pre-upgrade copy is NOW: the base store's copy is the new version's data dir; an
    instance's backup is found by its NAME next to the instance's data dir (the journal moves with instances/<key>/;
    an absolute path would not survive a project-key migration or a moved DEEPMAP_HOME). None when the journal names
    no backup an upgrade makes (data.bak-*): recovery never moves or deletes anything else. Never raises."""
    try:
        if key is None:
            return paths(journal["to"])["data"]
        name = Path(str(journal.get("copy") or "")).name
        if not name.startswith("data.bak-"):
            return None
        return paths(journal["from"], key)["data"].parent / name
    except Exception:
        return None


def _settle_journal(key, version: str) -> bool:
    """Close this store's upgrade journal — only when, without it, the store resolves to `version` (the release it is
    left on: port_registry.settled_store_version). Otherwise the journal stays (while it is open the store resolves to
    the old release, or to the new one once verified) and False. Never raises."""
    try:
        import port_registry as PR
        if PR.settled_store_version(key) != version:
            return False
        _clear_upgrade_journal(key)
        return PR.read_upgrade_journal(key) is None
    except Exception:
        return False


def _upgrade_failure_path(key) -> Path:
    import port_registry as PR
    return PR.upgrade_journal_path(key).parent / _UPGRADE_FAILURE_FILE


def _read_upgrade_failure(key) -> dict | None:
    """The record a rolled-back upgrade of this store left ({"from", "to", "outcome", "reason", "ts", "epoch"}), or
    None. Never raises."""
    try:
        rec = json.loads(_upgrade_failure_path(key).read_text(encoding="utf-8"))
        return rec if isinstance(rec, dict) and rec.get("to") else None
    except Exception:
        return None


def _recent_upgrade_failure(key, cur, tgt) -> dict | None:
    """The failure record when an upgrade of THIS store from `cur` to `tgt` was rolled back less than
    _UPGRADE_RETRY_AFTER_S ago (setup then does not try it again), else None. Never raises."""
    try:
        rec = _read_upgrade_failure(key)
        if rec and rec.get("to") == tgt and rec.get("from") == cur and \
                time.time() - float(rec.get("epoch") or 0) < _UPGRADE_RETRY_AFTER_S:
            return rec
    except Exception:
        pass
    return None


def _retry_date(rec: dict) -> str:
    try:
        return time.strftime("%Y-%m-%d", time.gmtime(float(rec.get("epoch") or 0) + _UPGRADE_RETRY_AFTER_S))
    except Exception:
        return "a week later"


def _note_upgrade_outcome(key, report: dict) -> None:
    """After an upgrade: a rolled-back one is recorded (to-version, reason) so setup's next automatic attempt at the
    same release waits _UPGRADE_RETRY_AFTER_S — a deterministic failure (a big store whose indexes come back slower than
    _UPGRADE_INDEX_WAIT_S) would otherwise restart Neo4j twice on every setup; an upgrade that succeeded removes it.
    Best-effort; never raises."""
    try:
        out = report.get("outcome")
        if out == "upgraded":
            _upgrade_failure_path(key).unlink(missing_ok=True)
        elif out in ("rolled-back", "rollback-failed") and report.get("from") and report.get("to"):
            _write_json_atomic(_upgrade_failure_path(key),
                               {"from": report["from"], "to": report["to"], "outcome": out,
                                "reason": report.get("reason"), "epoch": time.time(),
                                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    except Exception:
        pass


def _open_upgrade_problem(project_key) -> str | None:
    """ensure_neo4j's reason when THIS store is not in place while an upgrade of it is still open (its journal): the
    graph may sit in the pre-upgrade copy, so it is neither "never set up here" (heal) nor a reason to create a fresh
    empty store (setup) — the next setup's recover_neo4j_upgrade finishes or undoes the upgrade. None when no upgrade
    of it is open. Never raises."""
    try:
        import port_registry as PR
        j = PR.read_upgrade_journal(project_key)
        if not j:
            return None
        copy = _journal_copy(project_key, j)
        held = f" (the graph is kept at {copy})" if copy is not None and PR.neo4j_store_present(copy) else ""
        return (f"an interrupted upgrade of this project's Neo4j store to {j['to']} is still open and the store is not "
                f"in place{held} — nothing was created; run /deep-map:setup (it finishes or undoes the upgrade), or "
                f"see {PR.upgrade_journal_path(project_key)}")
    except Exception:
        return None


def _warn_ignored_pin(project_key, root=None) -> None:
    """One WARNING when NEO4J_VERSION is ignored for THIS store (port_registry.neo4j_pin_ignored: a downgrade, or a
    base folder holding no store). Its neo4j-upgrade command quotes the project's folder: `root`, else the one the port
    registry records for the instance; else it says to run it from this project's folder. Never raises."""
    try:
        import port_registry as PR
        ig = PR.neo4j_pin_ignored(project_key)
        if not ig:
            return
        pin, own, why = ig
        reason = ("Neo4j does not support moving a store to an older release" if why == "older"
                  else f"the base store lives in neo4j-community-{own}, and the pin's folder holds none (it would start "
                       "an empty store there)")
        print(f"[provision] WARNING: NEO4J_VERSION={pin} is ignored for this store — it runs Neo4j {own}, and "
              f"{reason}. Unset NEO4J_VERSION; {_upgrade_cmd_hint(root or _registry_root(project_key))} moves a "
              "store to a newer patch release.", file=sys.stderr)
    except Exception:
        pass


def _phrase(term: str) -> str:
    """`term` as one Lucene phrase (only a backslash and a double quote need escaping inside one)."""
    return '"' + str(term).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _index_probe(drv, idx: dict, probe: dict | None = None) -> dict | None:
    """ONE index query and its hit count. First call (probe None): a full-text query on an ONLINE deepmap_* full-text
    index (deepmap_code_fts first) for a name from the graph itself (_verify_query), else a vector query with an
    embedding stored in the graph; None when there is no such index. With `probe` (the old version's): the same query
    again. A database error propagates."""
    if probe is None:
        online = sorted(n for n, (_t, st) in idx.items() if st == "ONLINE")
        fts = [n for n in online if str(idx[n][0]).upper() == "FULLTEXT"]
        vec = [n for n in online if str(idx[n][0]).upper() == "VECTOR"]
        if fts:
            name = _PREFERRED_PROBE_INDEX if _PREFERRED_PROBE_INDEX in fts else fts[0]
            probe = {"kind": "fulltext", "index": name, "term": _verify_query(drv)}
        elif vec:
            with drv.session() as s:
                row = s.run("MATCH (n) WHERE n.embedding IS NOT NULL RETURN n.embedding AS e LIMIT 1").single()
            if row is None or not row["e"]:
                return None
            probe = {"kind": "vector", "index": vec[0], "vector": [float(x) for x in row["e"]]}
        else:
            return None
    with drv.session() as s:
        if probe["kind"] == "fulltext":
            row = s.run("CALL db.index.fulltext.queryNodes($i, $q) YIELD node RETURN count(node) AS c",
                        i=probe["index"], q=_phrase(probe["term"])).single()
        else:
            row = s.run("CALL db.index.vector.queryNodes($i, 10, $v) YIELD node RETURN count(node) AS c",
                        i=probe["index"], v=probe["vector"]).single()
    return dict(probe, hits=int(row["c"]) if row is not None else 0)


def _store_fingerprint(uri: str, user: str, password: str, *, probe: dict | None = None, online=(),
                       wait_s: float = 0.0) -> dict:
    """What the store behind `uri` answers — an upgrade's "same graph" evidence: its node and relationship counts, its
    :_DeepMapMeta owner, its ONLINE deepmap_* indexes and ONE index query with its hit count (_index_probe). `probe`
    repeats the old version's query; `online` (the old version's ONLINE indexes) are first waited for, up to `wait_s`.
    A database error propagates."""
    drv = _make_driver(uri, user, password)
    try:
        drv.verify_connectivity()
        end = time.monotonic() + max(0.0, float(wait_s))
        while True:
            with drv.session() as s:
                idx = {r["name"]: (r["type"], r["state"]) for r in s.run(
                    "SHOW INDEXES YIELD name, type, state WHERE name STARTS WITH 'deepmap_' RETURN name, type, state")}
            if all(idx.get(n, ("", ""))[1] == "ONLINE" for n in online) or time.monotonic() >= end:
                break
            time.sleep(2.0)
        with drv.session() as s:
            nodes = s.run("MATCH (n) RETURN count(n) AS c").single()["c"]
            rels = s.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]
        return {"nodes": int(nodes), "rels": int(rels), "owner": _read_graph_owner(drv),
                "online": sorted(n for n, (_t, st) in idx.items() if st == "ONLINE"),
                "probe": _index_probe(drv, idx, probe)}
    finally:
        try:
            drv.close()
        except Exception:
            pass


def _store_differs(before: dict, after: dict) -> str | None:
    """Why `after` (the new version's answers) is not the graph `before` (the old version's) described, or None."""
    if after.get("nodes") != before.get("nodes"):
        return f"{after.get('nodes')} nodes instead of {before.get('nodes')}"
    if after.get("rels") != before.get("rels"):
        return f"{after.get('rels')} relationships instead of {before.get('rels')}"
    if after.get("owner") != before.get("owner"):
        return f"graph owner {after.get('owner')!r} instead of {before.get('owner')!r}"
    lost = sorted(set(before.get("online") or ()) - set(after.get("online") or ()))
    if lost:
        return f"index(es) not ONLINE: {', '.join(lost)}"
    bp, ap = before.get("probe"), after.get("probe")
    if bp is not None and (ap is None or ap.get("hits") != bp.get("hits")):
        return (f"the {bp.get('kind')} query on {bp.get('index')} returned {None if ap is None else ap.get('hits')} "
                f"hits instead of {bp.get('hits')}")
    return None


def _wait_store_released(conf_dir, bolt, wait_s: float | None = None) -> bool:
    """After a stop: True once the bolt port is free (not checked when `bolt` is None) AND no DeepMap Neo4j JVM serves
    `conf_dir` any more (only then is every store file closed), False when that did not happen within `wait_s`
    (default _UPGRADE_STOP_WAIT_S). Never raises."""
    try:
        end = time.monotonic() + (_UPGRADE_STOP_WAIT_S if wait_s is None else wait_s)
        while True:
            if not (bolt and _port_in_use("127.0.0.1", int(bolt))) and not _find_neo4j_server_pids(conf_dir):
                return True
            if time.monotonic() >= end:
                return False
            time.sleep(1.0)
    except Exception:
        return False


def _start_version(version: str, project_key, bolt, http, uri: str, user: str, password: str) -> bool:
    """Configure + start `version` on this store (the caller holds its start lock) and wait for Bolt. Never raises."""
    try:
        configure_neo4j(version, project_key=project_key, bolt=bolt, http=http)
        start_neo4j(version, project_key)
        ok = wait_for_bolt(uri, user, password, timeout=_UPGRADE_START_TIMEOUT_S)
        if ok and not is_windows():
            _record_neo4j_server_identity(version, project_key)
        return bool(ok)
    except Exception as exc:  # noqa: BLE001
        print(f"[provision] ERROR: could not start Neo4j {version} ({type(exc).__name__}: {exc})", file=sys.stderr)
        return False


def _upgrade_locks(project_key, uri):
    """((start lock, sync lock), None) — this instance's start lock (ensure_neo4j's: no start / heal runs meanwhile)
    and sync's per-instance write lock (no sync writes meanwhile) — or (None, why) when either is held. Never
    raises."""
    try:
        start, how = _file_lock(deepmap_home() / "run" / f"neo4j-start-{project_key or 'base'}.lock",
                                wait_s=_UPGRADE_LOCK_WAIT_S)
        if start is None:
            return None, ("another DeepMap process is starting this Neo4j right now" if how == "timeout"
                          else "this Neo4j's start lock could not be opened")
        try:
            import sync as _sync
            wlock = _sync._acquire_instance_lock(uri)
        except Exception:
            wlock = None
        if wlock is None:
            _file_unlock(start)
            return None, "a sync is writing to this graph right now"
        return (start, wlock), None
    except Exception as exc:  # noqa: BLE001
        return None, f"could not take the upgrade locks ({type(exc).__name__}: {exc})"


def _release_upgrade_locks(locks) -> None:
    if not locks:
        return
    start, wlock = locks
    try:
        import sync as _sync
        _sync._release_sync_lock(wlock)
    except Exception:
        pass
    _file_unlock(start)


def upgrade_neo4j(target=".", *, uri: str | None = None, user: str | None = None, password: str | None = None,
                  inst=None, to_version: str | None = None, report: dict | None = None) -> str:
    """Move THIS project's store to a newer Neo4j PATCH release (to_version, default DEFAULT_VERSION) — see the section
    comment. `inst` is resolved_instance(target)'s answer (None: the base store, which must be the local 7687 and
    provably this project's); call after apply_ports_for. Returns the outcome (also report["outcome"]):
      "current"          nothing to do (already that release or newer, or another release line);
      "skipped"          not attempted, nothing changed (report["reason"]: an operator NEO4J_URI or NEO4J_VERSION, an
                         open journal, a target release with no pinned SHA-256, no Java / disk / path budget, the
                         download failed, a lock is held, a running Neo4j rejects these credentials (then nothing is
                         started or stopped), the store is not provably this project's, or its Neo4j could not be
                         stopped through the owner-proven stop);
      "upgraded"         the new release serves the same graph; the old copy is gone (or, when the store's new version
                         could not be recorded yet, kept with the "verified" journal for the next setup to finish);
      "rolled-back"      the new release did not (report["reason"]), or the copy / a journal phase could not be written
                         before the new release opened anything; the original store is back on the old release, which
                         serves the same graph again (recorded: setup does not retry that release for a week);
      "rollback-failed"  the old release could not be shown to serve it again — nothing is deleted (report["reason"]
                         names what is where; the next setup finishes the rollback from the journal when one is left).
    Never raises."""
    report = report if report is not None else {}
    _LAST_UPGRADE.clear()
    try:
        out = _upgrade_neo4j(target, uri=uri or os.environ.get("NEO4J_URI", DEFAULT_URI),
                             user=user or os.environ.get("NEO4J_USER", DEFAULT_USER),
                             password=password or os.environ.get("NEO4J_PASSWORD", DEFAULT_PASSWORD),
                             inst=inst, to_version=to_version, report=report)
    except Exception as exc:  # noqa: BLE001 — "never raises"
        report["outcome"] = out = "skipped" if not report.get("touched") else "rollback-failed"
        report["reason"] = f"the upgrade failed unexpectedly ({type(exc).__name__}: {exc})"
    _note_upgrade_outcome(inst[0] if inst else None, report)
    _LAST_UPGRADE.update(report)
    return out


def _upgrade_neo4j(target, *, uri, user, password, inst, to_version, report) -> str:
    import port_registry as PR
    key = inst[0] if inst else None
    bolt = inst[1] if inst else PR.BASE_BOLT
    http = inst[2] if inst else PR.BASE_HTTP
    cur = store_neo4j_version(key)
    where = f"this project's instance ({key[:8]})" if key else "the base store"
    report.update({"outcome": None, "from": cur, "to": None, "reason": None, "layout": "instance" if key else "base",
                   "touched": False})

    def done(outcome: str, reason: str | None = None) -> str:
        report["outcome"] = outcome
        if reason:
            report["reason"] = " ".join(str(reason).split())
        return outcome

    if _USER_SET_NEO4J_URI:
        return done("skipped", "an operator NEO4J_URI names this project's Neo4j — DeepMap does not manage its version")
    pin = PR.neo4j_version_pin()
    if pin:
        return done("skipped", f"NEO4J_VERSION={pin} pins the Neo4j version — unset it to let DeepMap upgrade the store")
    if PR.read_upgrade_journal(key):
        return done("skipped", "an earlier upgrade of this store is still open — /deep-map:setup finishes or undoes it "
                               "first")
    if to_version is not None and not PR.clean_neo4j_version(to_version):
        return done("skipped", f"{to_version!r} is not an X.Y.Z version")
    new = neo4j_upgrade_target(cur, to_version)
    report["to"] = new or PR.clean_neo4j_version(to_version or DEFAULT_VERSION)
    if not new:
        tv = PR.neo4j_version_tuple(report["to"])
        cv = PR.neo4j_version_tuple(cur)
        if tv and cv and tv[:2] != cv[:2] and tv > cv:
            return done("skipped", f"{report['to']} is not a patch release of Neo4j {cur} — only a newer "
                                   f"{cv[0]}.{cv[1]}.x is an upgrade DeepMap makes")
        return done("current")
    if not _is_initialized(cur, key):
        return done("current")   # no store yet: its first start gets the default anyway
    if not expected_sha256(new, is_windows()):
        return done("skipped", f"Neo4j {new}'s archive has no SHA-256 pinned in this DeepMap — a store is only moved to "
                               "a release whose download is verified (pin it out-of-band with DEEPMAP_NEO4J_SHA256)")
    if key is None and not _is_local_base_uri(uri):
        return done("skipped", f"{uri} is not this machine's base store")
    if key is None and _foreign_base_record(target):
        return done("skipped", "the base store's owner record names another project")
    if key is not None and PR.instance_conf_ports(key) is None:
        return done("skipped", f"instance {key} has no conf of its own")
    problems = _java_preflight_problems()
    if problems:
        return done("skipped", f"no usable Java for Neo4j ({problems[0][1]})")
    p_old = paths(cur, key)
    data = p_old["data"]
    size = _store_size(data)
    if size is None:
        return done("skipped", f"could not measure the store at {data}")
    copy_to = _free_sibling(data, f"data.bak-{cur}") if key else paths(new)["data"]
    need = size[0] + _UPGRADE_DISK_MARGIN + (0 if _binary_present_at(paths(new)["neo4j_home"])
                                             else _UPGRADE_ARCHIVE_BYTES)
    free = _disk_free_gb(data.parent) * 1024 ** 3
    if free < need:
        return done("skipped", f"only {free / 1024 ** 3:.1f} GB free at {data.parent}; the upgrade needs about "
                               f"{need / 1024 ** 3:.1f} GB (a copy of the {size[0] / 1024 ** 2:.0f} MB store + Neo4j {new})")
    if is_windows() and not _win_long_paths_enabled() and len(str(copy_to)) + 1 + size[1] >= _WIN_MAX_PATH:
        return done("skipped", f"copying the store to {copy_to} would exceed Windows' {_WIN_MAX_PATH}-character "
                               "MAX_PATH (a shorter DEEPMAP_HOME or Win32 long paths)")
    try:
        _download_extract_shared(new)   # before anything stops: a failed download changes nothing
    except Exception as exc:  # noqa: BLE001
        return done("skipped", _install_failed_text(exc))
    if key is None and PR.neo4j_store_present(copy_to):
        return done("skipped", f"{copy_to} already holds a store (one an earlier NEO4J_VERSION={new} created) — "
                               "DeepMap never writes over it")
    locks, why = _upgrade_locks(key, uri)
    if locks is None:
        return done("skipped", why)
    try:
        return _upgrade_locked(target, key, bolt, http, uri, user, password, cur, new, p_old, copy_to, where,
                               report, done)
    finally:
        _release_upgrade_locks(locks)


def _upgrade_locked(target, key, bolt, http, uri, user, password, cur, new, p_old, copy_to, where, report, done):
    """upgrade_neo4j once its checks passed and its locks are held (see there)."""
    import port_registry as PR
    data = p_old["data"]
    started = False

    def skip(reason: str) -> str:
        if started:   # this call started the old release only to read it: leave the store as it was found (down)
            stop_neo4j(cur, key)
        return done("skipped", reason)

    state = _bolt_state(uri, user, password, timeout=3)
    if state == "auth":
        # a Neo4j ANSWERED but rejected these credentials: the graph can't be read with them (before or after), so
        # nothing is started or stopped — the owner-proven stop would only take the user's running store down for an
        # upgrade that can't go ahead
        return done("skipped", f"{_auth_failed_reason(uri)}; nothing was started or stopped")
    if state != "up":
        # the store is down (a stopped / hibernated instance): its answers on the OLD release are read first
        if key is None and not _base_owner_is(target):
            return skip("the base store is not running and its owner record does not name this project")
        started = True
        if not _start_version(cur, key, bolt, http, uri, user, password):
            return skip(f"Neo4j {cur} did not start on {where} (see {p_old['log']})")
    if key is None and not _legacy_base_instance_alive():
        return skip(f"bolt {PR.BASE_BOLT} is not served by DeepMap's own base Neo4j")
    try:
        before = _store_fingerprint(uri, user, password)
    except Exception as exc:  # noqa: BLE001
        return skip(f"could not read the graph on Neo4j {cur} ({type(exc).__name__}: {exc})")
    owner = before.get("owner")
    if owner and not _owner_is_this_project(target, owner):
        return skip(f"{where} holds project {owner}'s graph, not this project's")
    if key is None and not owner and before.get("nodes"):
        return skip("the base store holds a graph no project is recorded as owning")
    print(f"[provision] upgrading {where} from Neo4j {cur} to {new} ({before['nodes']} nodes, "
          f"{before['rels']} relationships)")
    journal = {"from": cur, "to": new, "phase": "stopping", "layout": "instance" if key else "base", "key": key,
               # an instance's backup by NAME (found next to its data dir: _journal_copy); the base's copy is the new
               # version's data dir — recorded for a human reading the journal
               "copy": copy_to.name if key else str(copy_to), "root": PR.project_identity(target), "pid": os.getpid(),
               "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    if not _write_upgrade_journal(key, journal):
        return done("skipped", "could not write the upgrade journal")
    stopped = stop_neo4j(cur, key) and _LAST_STOP_OUTCOME == "stopped"
    if not stopped or not _wait_store_released(p_old["conf_dir"], bolt):
        _clear_upgrade_journal(key)
        if not is_bolt_up(uri, user, password, timeout=3) and not _start_version(cur, key, bolt, http, uri, user,
                                                                                 password):
            return done("rollback-failed", f"Neo4j {cur} could not be stopped cleanly for the upgrade and did not start "
                                           f"again — the store is untouched at {data}; run /deep-map:setup")
        return done("skipped", f"Neo4j {cur} could not be stopped through DeepMap's owner-proven stop (or its JVM did "
                               "not exit) — nothing was changed")
    report["touched"] = True

    def untouched(why: str) -> str:
        # the new release never opened anything: the copy (a backup / the new folder's copy — never the original)
        # goes, the journal closes, the old release starts again on the store it left
        if not _same_path(copy_to, data):
            _rmtree_retry(copy_to)
        _clear_upgrade_journal(key)
        back = _start_version(cur, key, bolt, http, uri, user, password)
        return done("rolled-back" if back else "rollback-failed",
                    f"{why}; the store is untouched at {data}"
                    + ("" if back else f" — Neo4j {cur} did not start again: run /deep-map:setup"))

    journal["phase"] = "copying"
    if not _write_upgrade_journal(key, journal):
        return untouched(f"the upgrade journal could not be updated ({PR.upgrade_journal_path(key)})")
    try:
        _copy_store(data, copy_to)
    except Exception as exc:  # noqa: BLE001
        return untouched(f"copying the store failed ({type(exc).__name__}: {exc})")
    journal["phase"] = "started"
    if not _write_upgrade_journal(key, journal):
        # the next phase opens the store with the new release: never without "started" on disk (a crash would leave
        # "copying", whose recovery deletes the copy — an instance's pristine backup)
        return untouched(f"the upgrade journal could not be updated ({PR.upgrade_journal_path(key)})")
    why = None
    if not _start_version(new, key, bolt, http, uri, user, password):
        why = f"Neo4j {new} did not answer within {_UPGRADE_START_TIMEOUT_S}s (see {paths(new, key)['log']})"
    else:
        try:
            after = _store_fingerprint(uri, user, password, probe=before.get("probe"), online=before.get("online"),
                                       wait_s=_UPGRADE_INDEX_WAIT_S)
            why = _store_differs(before, after)
        except Exception as exc:  # noqa: BLE001
            why = f"the graph could not be read on Neo4j {new} ({type(exc).__name__}: {exc})"
    if why is None:
        journal["phase"] = "verified"
        _write_upgrade_journal(key, journal)   # when this fails _finish_upgrade still settles on the conf stamp / record
        _finish_upgrade(key, journal)
        print(f"[provision] upgraded {where} to Neo4j {new} (same graph: {before['nodes']} nodes, "
              f"{before['rels']} relationships, {len(before.get('online') or ())} indexes ONLINE)")
        return done("upgraded")
    print(f"[provision] ERROR: Neo4j {new} did not serve the same graph ({why}) — rolling back to {cur}",
          file=sys.stderr)
    return _rollback_upgrade(target, key, bolt, http, uri, user, password, journal, before, why, done)


def _finish_upgrade(key, journal: dict) -> bool:
    """The verified upgrade's last steps (also recover_neo4j_upgrade's roll-forward): the base store's version record,
    then — only once the disk resolves the store to the new release WITHOUT the journal (settled_store_version: that
    record, or the instance's conf stamp) and the upgraded store is in place — the journal closes and the OLD copy goes
    (an instance's backup; the base store's original folder data). Otherwise nothing is deleted, the journal stays
    (a "verified" one resolves to the new release; the next setup retries) and False. A copy that can't be removed
    yet stays (named). Never raises."""
    try:
        import port_registry as PR
        old_v, new = journal["from"], journal["to"]
        new_data = paths(new, key)["data"]
        old = paths(old_v)["data"] if key is None else _journal_copy(key, journal)
        if not PR.neo4j_store_present(new_data):
            print(f"[provision] NOTE: the upgraded store is not at {new_data} — nothing was removed (the upgrade journal "
                  f"{PR.upgrade_journal_path(key)} stays; /deep-map:setup undoes it).", file=sys.stderr)
            return False
        if key is None:
            _write_base_version_record(new)
        if PR.settled_store_version(key) != new:
            print(f"[provision] NOTE: could not record that the store now runs Neo4j {new} "
                  f"({PR.base_version_record_path() if key is None else paths(new, key)['conf_dir']}) — the pre-upgrade "
                  f"copy is kept at {old} and the upgrade journal stays; the next /deep-map:setup finishes it.",
                  file=sys.stderr)
            return False
        verified_on_disk = (PR.read_upgrade_journal(key) or {}).get("phase") == "verified"
        if not verified_on_disk:
            # the "verified" write failed: the journal on disk still names the OLD release — close it first, so an open
            # one never coexists with a deleted backup (recovery would read that as a rollback)
            _clear_upgrade_journal(key)
            if PR.read_upgrade_journal(key) is not None:
                return False
        if old is not None and old.exists() and not _same_path(old, new_data) and not _rmtree_retry(old):
            print(f"[provision] NOTE: could not remove the pre-upgrade copy {old} yet — delete it by hand.",
                  file=sys.stderr)
        _clear_upgrade_journal(key)
        return True
    except Exception:
        return False


def _restore_instance_store(key, journal: dict, bolt, http):
    """Put instance `key`'s ORIGINAL store (its pre-upgrade backup) back in place of the one the new release opened —
    a rollback's and recover_neo4j_upgrade's step. The old release's conf is seeded FIRST (from then on, whatever a
    crash leaves, the store resolves to the old release once the journal closes), the journal records the restore
    (phase "restoring": an absent backup then means "already moved back"), the opened store is set aside
    (data.failed-<new>; skipped when an earlier attempt already moved it), and the backup moves to data.
    (set-aside dir or None, None) once the original is in place, else (None, why) — the journal stays. Never raises."""
    import port_registry as PR
    cur, new = journal["from"], journal["to"]
    data = paths(cur, key)["data"]
    copy = _journal_copy(key, journal)
    try:
        if copy is None or not copy.is_dir():
            return None, f"its pre-upgrade backup is not where the journal names it ({copy or journal.get('copy')})"
        try:
            configure_neo4j(cur, project_key=key, bolt=bolt, http=http)
        except Exception:
            pass
        if PR.instance_version_stamp(key) != cur:
            return None, (f"Neo4j {cur}'s conf could not be written to {paths(cur, key)['conf_dir']} — the original store "
                          f"is kept at {copy}")
        failed = _free_sibling(data, f"data.failed-{new}")
        _write_upgrade_journal(key, dict(journal, phase="restoring", failed=failed.name))   # best-effort (see above)
        if data.is_dir() and not any(data.iterdir()):
            data.rmdir()   # an earlier attempt already set the opened store aside; configure_neo4j re-made an empty dir
        if data.exists() and not _move_dir(data, failed):
            return None, (f"the store Neo4j {new} opened could not be moved aside ({data}: files in use) — the original "
                          f"store (the graph) is kept at {copy}")
        if not _move_dir(copy, data):
            return None, (f"the original store could not be moved back to {data} (files in use) — the graph is at "
                          f"{copy}")
        aside = sorted((x for x in data.parent.iterdir() if x.name.startswith(f"data.failed-{new}")),
                       key=lambda x: x.name)
        return (failed if failed.exists() else (aside[-1] if aside else None)), None
    except Exception as exc:  # noqa: BLE001
        return None, f"restoring the original store failed ({type(exc).__name__}: {exc}) — the graph is at {copy}"


def _rollback_upgrade(target, key, bolt, http, uri, user, password, journal, before, why, done):
    """Undo a started upgrade whose new release did not serve the same graph: stop it (owner-proven), put the original
    store back, restart the OLD release and check it gives the old answers. See upgrade_neo4j's outcomes."""
    cur, new = journal["from"], journal["to"]
    p_new = paths(new, key)
    stop_neo4j(new, key)
    if _LAST_STOP_OUTCOME in ("unverified", "failed") or not _wait_store_released(p_new["conf_dir"], bolt):
        return done("rollback-failed", f"{why}; and Neo4j {new} could not be stopped through DeepMap's owner-proven "
                                       f"stop — nothing was moved back (the original store: "
                                       f"{_journal_copy(key, journal) if key else paths(cur)['data']}). Stop that "
                                       "Neo4j, then re-run /deep-map:setup (it finishes the rollback)")
    failed = None
    if key is not None:
        failed, bad = _restore_instance_store(key, journal, bolt, http)
        if bad:
            return done("rollback-failed", f"{why}; and {bad} — re-run /deep-map:setup (it finishes the rollback)")
    else:
        copy = _journal_copy(None, journal)
        if copy is not None and not _same_path(copy, paths(cur)["data"]):
            _rmtree_retry(copy)                # the new folder's copy; the original never moved
    if not _settle_journal(key, cur):          # the store is the old release's again (an instance: its conf says so)
        print(f"[provision] NOTE: the upgrade journal {_journal_path_text(key)} stays open (the store resolves to Neo4j "
              f"{cur} while it is); the next /deep-map:setup closes it.", file=sys.stderr)
    back = _start_version(cur, key, bolt, http, uri, user, password)
    differs = None
    if back:
        try:
            differs = _store_differs(before, _store_fingerprint(uri, user, password, probe=before.get("probe"),
                                                                online=before.get("online"),
                                                                wait_s=_UPGRADE_INDEX_WAIT_S))
        except Exception as exc:  # noqa: BLE001
            differs = f"{type(exc).__name__}: {exc}"
    if back and differs is None:
        if failed is not None:
            _rmtree_retry(failed)
        return done("rolled-back", f"{why}; Neo4j {cur} serves the graph again")
    return done("rollback-failed", f"{why}; the original store is back in place on Neo4j {cur}, but "
                                   + (f"it did not start (see {paths(cur, key)['log']})" if not back
                                      else f"it does not give the old answers ({differs})")
                                   + (f"; the failed copy is kept at {failed}" if failed is not None else "")
                                   + " — run /deep-map:setup")


def recover_neo4j_upgrade(target, *, inst=None, uri: str | None = None) -> str | None:
    """setup / neo4j-upgrade, BEFORE Neo4j is started: finish or undo an upgrade of THIS project's store that a crash
    left open (its journal). "verified" (and the upgraded store in place) -> roll forward (_finish_upgrade); "stopping"
    / "copying" (the new release never opened the store) -> the partial copy goes; "started" / "restoring" (or a
    "verified" one whose upgraded store is gone) -> the new release is stopped (owner-proven) and the original store
    is put back (an instance: the backup, or it already is; the base store: the original never moved, the copy goes).
    The journal closes only once the store resolves to the release it is left on without it (_settle_journal).
    Returns a one-line note of what it did, or None (no journal / nothing it may do: a lock held — the upgrade may
    still be running elsewhere —, or a Neo4j it could not stop: the journal is kept). Never raises."""
    try:
        import port_registry as PR
        key = inst[0] if inst else None
        j = PR.read_upgrade_journal(key)
        if not j:
            return None
        uri = uri or os.environ.get("NEO4J_URI", DEFAULT_URI)
        bolt = inst[1] if inst else PR.BASE_BOLT
        http = inst[2] if inst else PR.BASE_HTTP
        locks, why = _upgrade_locks(key, uri)
        if locks is None:
            return f"an interrupted Neo4j upgrade of this store is still open ({why}) — left as it is"
        try:
            return _recover_locked(key, j, bolt, http)
        finally:
            _release_upgrade_locks(locks)
    except Exception:
        return None


def _recover_locked(key, j: dict, bolt, http) -> str:
    """recover_neo4j_upgrade once its locks are held (see there)."""
    import port_registry as PR
    cur, new, phase = j["from"], j["to"], j.get("phase")
    if phase == "verified" and PR.neo4j_store_present(paths(new, key)["data"]):
        if _finish_upgrade(key, j):
            return f"finished an interrupted upgrade to Neo4j {new}"
        return (f"an interrupted upgrade to Neo4j {new} was verified but could not be finished yet (see the NOTE above) "
                f"— the store runs {new} meanwhile")
    stuck = (f"an interrupted upgrade to Neo4j {new} could not be undone: its Neo4j could not be stopped through "
             "DeepMap's owner-proven stop — stop it, then re-run /deep-map:setup")
    if phase in ("started", "restoring", "verified"):
        # a "verified" journal whose upgraded store is gone (removed by hand) is undone like an unverified one
        stop_neo4j(new, key)
        outcome = _LAST_STOP_OUTCOME
        if outcome in ("unverified", "failed"):
            return stuck
        if key is None and outcome == "nothing":
            # the original base store never moved: a base Neo4j the SessionStart heal revived on it (the journal names
            # the OLD release until "verified") may legitimately hold the bolt port — only no JVM may serve the new folder
            released = _wait_store_released(paths(new)["conf_dir"], None)
        else:
            released = _wait_store_released(paths(new, key)["conf_dir"], bolt)
        if not released:
            return stuck
        if key is not None:
            return _recover_instance(key, j, bolt, http)
    # "stopping" / "copying" — or an opened / missing copy of the BASE store: the original never moved; the copy goes
    copy = _journal_copy(key, j)
    if copy is not None and copy.exists() and not _same_path(copy, paths(cur, key)["data"]):
        _rmtree_retry(copy)
    if key is not None:
        configure_neo4j(cur, project_key=key, bolt=bolt, http=http)
    if not _settle_journal(key, cur):
        return (f"an interrupted upgrade to Neo4j {new} could not be closed yet: the store does not resolve to {cur} "
                f"without its journal ({_journal_path_text(key)}) — it stays on {cur} meanwhile; re-run /deep-map:setup")
    return f"undid an interrupted upgrade to Neo4j {new}: the store stays on {cur}"


def _recover_instance(key, j: dict, bolt, http) -> str:
    """_recover_locked for an instance whose new release is stopped: put the original store back (see there)."""
    import port_registry as PR
    cur, new = j["from"], j["to"]
    data = paths(cur, key)["data"]
    copy = _journal_copy(key, j)
    if copy is not None and copy.is_dir():
        aside, bad = _restore_instance_store(key, j, bolt, http)
        if bad:
            return f"an interrupted upgrade to Neo4j {new} could not be undone yet: {bad} — re-run /deep-map:setup"
        if not _settle_journal(key, cur):
            return (f"undid an interrupted upgrade to Neo4j {new} (the original store is back), but its journal "
                    f"{_journal_path_text(key)} could not be closed — re-run /deep-map:setup")
        return (f"undid an interrupted upgrade to Neo4j {new}: the store is back on {cur}"
                + (f" (the store the new release had opened is kept at {aside})" if aside is not None else ""))
    if not PR.neo4j_store_present(data):
        return (f"an interrupted upgrade to Neo4j {new} could not be undone: neither the store nor its pre-upgrade backup "
                f"is in {data.parent} (look for data.failed-* there) — its journal {_journal_path_text(key)} is kept")
    failed_aside = any(x.name.startswith(f"data.failed-{new}") for x in data.parent.iterdir())
    if j.get("phase") == "restoring" or failed_aside:
        # the backup was already moved back (a rollback or a recovery cut short): the original store is in place
        configure_neo4j(cur, project_key=key, bolt=bolt, http=http)
        if not _settle_journal(key, cur):
            return (f"an interrupted upgrade to Neo4j {new}: the original store is back, but Neo4j {cur}'s conf could not "
                    f"be written — its journal {_journal_path_text(key)} is kept; re-run /deep-map:setup")
        return f"finished undoing an interrupted upgrade to Neo4j {new}: the original store is back on {cur}"
    # no backup and no sign it was moved back (removed by hand): the store stays with the release that last opened it
    last = PR.instance_version_stamp(key) or cur
    if not _settle_journal(key, last):
        return f"an interrupted upgrade to Neo4j {new} left no backup and could not be closed — re-run /deep-map:setup"
    return (f"an interrupted upgrade to Neo4j {new} left no backup to restore — the store stays as it is, on Neo4j "
            f"{last} (the release that last opened it)")


# ---- MCP server lifecycle (C8/C9 over streamable-HTTP) ----------------------
# stdio hangs under Claude Code on Windows (the mcp-SDK reader); the servers run over
# streamable-HTTP instead, started detached here so they survive a Claude Code reload and the
# HTTP `.mcp.json` URLs connect. See build/_mcp_transport.py.
MCP_SERVERS = {
    "deepmap-search": {"script": "deepmap_search_server.py",
                       "port_env": "DEEPMAP_SEARCH_PORT", "default_port": 8787,
                       "tool": "deepmap_search"},
    "deepmap-embed":  {"script": "deepmap_embed_server.py",
                       "port_env": "DEEPMAP_EMBED_PORT", "default_port": 8788,
                       "tool": "deepmap_embed"},
}


def mcp_host() -> str:
    return os.environ.get("DEEPMAP_MCP_HOST", "127.0.0.1")


def mcp_port(name: str) -> int:
    cfg = MCP_SERVERS[name]
    return int(os.environ.get(cfg["port_env"], cfg["default_port"]))


def mcp_url(name: str) -> str:
    return f"http://{mcp_host()}:{mcp_port(name)}/mcp"


# ---- per-project port allocation + Neo4j URI routing (side-by-side projects; build/port_registry.py) ----
def apply_ports_for(target, *, allocate: bool = True, allocate_neo4j: bool | None = None):
    """Resolve THIS project's per-project resources and inject them into os.environ so the whole path
    is consistent — ONE seam, no other call-site edits. Returns the MCP (search, embed) pair (or None
    when the MCP env was left untouched). Two INDEPENDENT halves:

    MCP ports (search, embed):
      * An explicit user override (DEEPMAP_SEARCH_PORT / DEEPMAP_EMBED_PORT) is respected verbatim.
      * allocate=True (setup / mcp-start): claim a free pair; first project gets 8787/8788 (unchanged).
      * allocate=False (heal / status): LOOK up (never claim new ports that drift from .mcp.json).

    Neo4j URI (bolt) — injected so sync + the detached servers + CLI reach THIS project's own graph:
      * The escape hatch is an OPERATOR-set NEO4J_URI (captured at import — BYO/Aura/shared): the whole
        Neo4j half is skipped, the operator owns the endpoint — once port_registry.neo4j_uri_problem accepts
        it (a Bolt URI); a URI it refuses (the Neo4j Browser's http:// address, ...) is neo4j_route_problem().
      * `allocate_neo4j` defaults to `allocate`; setup allocates a per-project (bolt, http) pair
        (first project keeps 7687/7474, owner-aware — see _base_adopt_ok), mcp-start/heal/status LOOK
        it up (see _neo4j_unregistered_route for a project with no pair). NEO4J_URI is set to
        bolt://127.0.0.1:<its bolt> (port_registry.bolt_uri — ROUND-8 J4).

    ROUTING VERDICTS: it records why the project could NOT be given resources of its OWN —
    neo4j_route_problem() / mcp_route_problem(), None when routed — and HOW its Neo4j was routed
    (neo4j_route_source(); "legacy-unverified" = the base 7687 of a project with no instance of its own,
    which every verb verifies before use). A pair the allocator returned but never recorded for this
    project (a lock timeout / exhausted scan / unreadable registry fallback — possibly ANOTHER project's
    pair) is never injected; a project with no Neo4j of its own gets an unconnectable NEO4J_URI (fail
    closed) instead of 7687. Never raises."""
    global _NEO4J_ROUTE_PROBLEM, _MCP_ROUTE_PROBLEM, _NEO4J_ROUTE_SOURCE
    _NEO4J_ROUTE_PROBLEM = _MCP_ROUTE_PROBLEM = _NEO4J_ROUTE_SOURCE = None
    if allocate_neo4j is None:
        allocate_neo4j = allocate
    mcp_pair = None
    try:
        if "DEEPMAP_SEARCH_PORT" not in os.environ and "DEEPMAP_EMBED_PORT" not in os.environ:
            import port_registry as PR
            host = mcp_host()
            if allocate:
                mcp_pair = PR.allocate_ports(target, host=host, prefer=_mcp_json_pair_reusable(target))
                if not mcp_pair or PR.lookup_ports(target) != tuple(mcp_pair):
                    mcp_pair = None
                    _MCP_ROUTE_PROBLEM = _allocation_failure("MCP (search, embed)")
            else:
                mcp_pair = PR.lookup_ports(target)
                if not mcp_pair:
                    # last resort: the pair this project's .mcp.json declares (what Claude Code connects to) —
                    # unless the registry gives those ports to ANOTHER project (a copied checkout / worktree
                    # inherits the original's committed .mcp.json): then they are that project's servers,
                    # never this one's to revive, stop or report (2026-09 review).
                    mcp_pair = _ports_from_mcp_json(PR.project_identity(target))
                    other = _mcp_pair_claimed_by_other(target, mcp_pair) if mcp_pair else None
                    if other:
                        _MCP_ROUTE_PROBLEM = (f"the MCP ports this project's .mcp.json declares ({mcp_pair[0]}/"
                                              f"{mcp_pair[1]}) are registered to another project ({other}) — "
                                              "they are its servers, not this project's; run /deep-map:setup "
                                              "here to give this project ports of its own")
                        mcp_pair = None
                if not mcp_pair and not _MCP_ROUTE_PROBLEM:
                    _MCP_ROUTE_PROBLEM = ("no MCP port pair is registered for this project (and no .mcp.json "
                                          "declares one) — run /deep-map:setup")
            if mcp_pair:
                os.environ["DEEPMAP_SEARCH_PORT"] = str(mcp_pair[0])
                os.environ["DEEPMAP_EMBED_PORT"] = str(mcp_pair[1])
    except Exception as exc:
        mcp_pair = None
        _MCP_ROUTE_PROBLEM = f"could not resolve this project's MCP ports ({type(exc).__name__}: {exc})"
    _apply_neo4j_env(target, allocate=allocate_neo4j)
    return mcp_pair


def neo4j_route_problem() -> str | None:
    """Why the last apply_ports_for could NOT route its project to a Neo4j instance of its own (a
    one-line, user-facing reason naming the fix), or None when it was routed / in escape-hatch mode."""
    return _NEO4J_ROUTE_PROBLEM


def mcp_route_problem() -> str | None:
    """Why the last apply_ports_for could NOT give its project an MCP port pair of its own, or None."""
    return _MCP_ROUTE_PROBLEM


def neo4j_route_source() -> str | None:
    """HOW the last apply_ports_for routed its project's Neo4j: "registry" | "instance-conf" |
    "legacy-unverified" (the base 7687 of a project with no instance of its own — verify the graph's
    owner before using it: _legacy_route_verdict) | "env" (operator escape hatch) | "none" (no route) |
    None (not resolved / a stubbed seam)."""
    return _NEO4J_ROUTE_SOURCE


def _resolve_ports(target, *, allocate: bool, allocate_neo4j: bool | None = None):
    """apply_ports_for + the two routing verdicts it recorded: (mcp_pair, neo4j_problem, mcp_problem).
    Resets the verdicts first, so a stubbed/no-op seam reads as "routed" (never a stale verdict)."""
    global _NEO4J_ROUTE_PROBLEM, _MCP_ROUTE_PROBLEM, _NEO4J_ROUTE_SOURCE
    _NEO4J_ROUTE_PROBLEM = _MCP_ROUTE_PROBLEM = _NEO4J_ROUTE_SOURCE = None
    pair = apply_ports_for(target, allocate=allocate, allocate_neo4j=allocate_neo4j)
    return pair, _NEO4J_ROUTE_PROBLEM, _MCP_ROUTE_PROBLEM


def _allocation_failure(what: str) -> str:
    """Why an allocation came back WITHOUT a pair recorded for this project (a user-facing line)."""
    try:
        import port_registry as PR
        path = PR.registry_path()
        status = PR.registry_status()
        if status in ("unreadable", "unrecognized"):
            why = ("could not be read" if status == "unreadable"
                   else "is not recognised (corrupt, or written by a newer DeepMap)")
            return (f"could not allocate this project's {what} ports: the port registry {path} {why} — fix it "
                    "or move it aside, then re-run /deep-map:setup (instances are re-adopted from their conf)")
        n = PR.registry_stats().get("n_projects", 0)
        return (f"could not allocate this project's {what} ports (registry {path}, {n} projects): the "
                "registry was busy (another DeepMap setup/heal held its lock) or no free pair is left — retry "
                "/deep-map:setup; if it persists, free stale projects with "
                f"{_provision_cmd('instance-rm --key <k> --purge --yes')}")
    except Exception:
        return f"could not allocate this project's {what} ports — retry /deep-map:setup"


def _neo4j_lookup_route(target):
    """((bolt, http) | None, problem | None, source) — THIS project's Neo4j on the LOOKUP path (heal /
    status / stop / mcp-start never allocate). It IS port_registry.resolve_neo4j_route (ROUND-2 D1: one
    rule for the CLI, sync, indexes, provision and the hooks; honor_env=False because provision is the
    NEO4J_URI injector — the operator escape hatch is decided at import time):
      * the project's registered pair, or its OWN instance re-found from its conf (a lost registry entry);
      * NO route when the registry exists but is unreadable / unrecognised (other projects' claims are
        invisible — never guess 7687; review #11), or when another registered project owns 7687 (#46);
      * else the base 7687 as a LEGACY route ("legacy-unverified") — which the caller must verify
        (_legacy_route_verdict) — except that a 7687 held by a program that is NOT DeepMap's and that
        rejects our credentials is no route at all (the Docker-Neo4j field case, #66).
    ROUND-6 H2 (lookup side): when the only reason for "no route" is ANOTHER project's claim on the base pair
    (kind "no-instance") and the base answers with our credentials, its LIVE owner is read first
    (_record_live_base_owner) — the store's real owner, locked out by a <= v0.27.4 mis-adoption, is then
    routed to it (legacy-unverified: the caller's live owner check still decides) without a rebuild.
    Never raises."""
    try:
        import port_registry as PR
        r = PR.resolve_neo4j_route(target, honor_env=False)
        if r.kind == "no-instance" and _record_live_base_owner(target):
            r = PR.resolve_neo4j_route(target, honor_env=False)
        if r.problem or not r.uri or not r.pair:
            return None, (r.problem or "could not resolve this project's Neo4j instance — run /deep-map:setup"), \
                "none"
        if r.source == "legacy-unverified" and _base_held_by_other():
            user = os.environ.get("NEO4J_USER", DEFAULT_USER)
            password = os.environ.get("NEO4J_PASSWORD", DEFAULT_PASSWORD)
            if not is_bolt_up(r.uri, user, password, timeout=3):
                return None, (f"no DeepMap Neo4j instance is allocated for this project, and bolt "
                              f"{PR.BASE_BOLT} is held by another program (not DeepMap's) — run "
                              "/deep-map:setup to give this project its own instance"), "none"
        return (r.pair[0], r.pair[1]), None, r.source
    except Exception as exc:
        return None, f"could not resolve this project's Neo4j instance ({type(exc).__name__}: {exc})", "none"


def _record_live_base_owner(target) -> bool:
    """ROUND-6 H2 (lookup side): read the LIVE base's :_DeepMapMeta owner (only a base that already answers with
    our credentials — nothing is started) and record it (_foreign_base_owner: DeepMap's own running instance only);
    True iff the owner record now names THIS project. A record already naming it -> True without a read (the
    resolver voided the claim already). Never raises."""
    try:
        if _base_owner_is(target):
            return True
        import port_registry as PR
        user = os.environ.get("NEO4J_USER", DEFAULT_USER)
        password = os.environ.get("NEO4J_PASSWORD", DEFAULT_PASSWORD)
        if not is_bolt_up(_bolt_uri(PR.BASE_BOLT), user, password, timeout=3):
            return False
        _foreign_base_owner(target, user, password)
        return _base_owner_is(target)
    except Exception:
        return False


def _neo4j_unregistered_route(target):
    """((bolt, http) | None, problem | None) — _neo4j_lookup_route without the source (kept for callers
    of the round-1 name). Never raises."""
    pair, problem, _source = _neo4j_lookup_route(target)
    return pair, problem


def _apply_neo4j_env(target, *, allocate: bool) -> None:
    """Inject NEO4J_URI for THIS project (its own per-project instance), or leave the env untouched in
    escape-hatch mode (operator-set NEO4J_URI). On the allocate path, claim the project's (bolt, http)
    pair — owner-aware for the base 7687 so a 2nd project can't seize a live instance owned by another
    (the field failure). The lookup path is _neo4j_lookup_route (the shared resolver). A project left
    WITHOUT a route records neo4j_route_problem() and gets the unconnectable _NO_ROUTE_URI; the route's
    kind is neo4j_route_source(). In escape-hatch mode the operator's NEO4J_URI is judged by
    port_registry.neo4j_uri_problem (Bolt only): a URI it refuses is no route (source "none", its reason) but
    stays in the env untouched, so a second resolution in this process refuses it the same way. Never raises."""
    global _NEO4J_ROUTE_PROBLEM, _NEO4J_ROUTE_SOURCE
    try:
        if _USER_SET_NEO4J_URI:
            import port_registry as PR
            # the ONE NEO4J_URI scheme rule (the same call resolve_neo4j_route makes): DeepMap dials Bolt only, so an
            # operator URI it won't dial (the Neo4j Browser's http:// address, ...) is no route — every verb then
            # refuses with the rule's words, before any driver, JVM or server. The operator's value is left in the
            # env as it was: re-resolving in this process gives the same answer (an injected sentinel would not).
            bad = PR.neo4j_uri_problem(os.environ.get("NEO4J_URI"))
            if bad:
                _NEO4J_ROUTE_PROBLEM = bad
                _NEO4J_ROUTE_SOURCE = "none"
                return
            _NEO4J_ROUTE_SOURCE = "env"
            return   # operator owns the endpoint (BYO/Aura/shared) — single-instance, no injection
        import port_registry as PR
        if allocate:
            _release_foreign_base_claim(target)   # un-wedge a base claim a foreign listener now holds
            prefer = _neo4j_ports_from_instance_conf(PR.project_key(target))  # recover a lost registry
            # ROUND-6 H2/H3b: make the base store's owner KNOWN before the allocator decides on it (the live
            # owner is the proof — the record alone locked the real owner out when it acted first after the
            # upgrade, and stranded a legacy user whose store was down after a reboot)
            started_base = _settle_base_owner(target, prefer=prefer)
            PR.allocate_neo4j_ports(target, host=mcp_host(), prefer=prefer,
                                    base_adopt_ok=lambda: _base_adopt_ok(target))
            # We ARE the injector (an operator NEO4J_URI returned above), so read the registry, never an
            # earlier injection in this process. A pair not RECORDED for us is never guessed.
            pair = PR.lookup_neo4j_ports(target)
            if started_base and pair != (PR.BASE_BOLT, PR.BASE_HTTP):
                # setup started the store only to read its owner, and it is not this project's to use: leave it
                # as it was found (down) — its owner revives it
                print(f"[provision] the base store on {PR.BASE_BOLT} is not this project's — stopping it again "
                      "(setup started it only to read its owner).", file=sys.stderr)
                stop_neo4j(project_key=None)
            problem = None if pair else _allocation_failure("Neo4j (bolt, http)")
            source = "registry" if pair else "none"
        else:
            pair, problem, source = _neo4j_lookup_route(target)
        os.environ["NEO4J_URI"] = _bolt_uri(pair[0]) if pair else _NO_ROUTE_URI
        _NEO4J_ROUTE_PROBLEM = problem
        _NEO4J_ROUTE_SOURCE = source
    except Exception as exc:
        os.environ["NEO4J_URI"] = _NO_ROUTE_URI
        _NEO4J_ROUTE_PROBLEM = f"could not resolve this project's Neo4j instance ({type(exc).__name__}: {exc})"
        _NEO4J_ROUTE_SOURCE = "none"


def _base_claimed_by_other(target) -> bool:
    """True iff ANOTHER registered project's entry records a port of the base (7687, 7474) pair. Never raises."""
    try:
        import port_registry as PR
        claims = PR.neo4j_claims(PR.project_key(target))
        return PR.BASE_BOLT in claims or PR.BASE_HTTP in claims
    except Exception:
        return False


def _shared_binary_present(version: str | None = None) -> bool:
    """True iff the shared Neo4j binary is already extracted — extract_neo4j's own idempotency test (its
    completion sentinel, or a complete pre-sentinel install). A legacy store lives INSIDE that tree, so a real
    legacy user always has it; without it, reading a store's owner would need a ~180 MB download. Never raises."""
    try:
        p = paths(version)
        return ((p["neo4j_home"] / ".deepmap-extracted").exists()
                or (p["neo4j_bin"].exists() and (p["neo4j_home"] / "lib").is_dir()
                    and (p["neo4j_home"] / "conf").is_dir()))
    except Exception:
        return False


def _settle_base_owner(target, *, prefer=None, user: str | None = None, password: str | None = None) -> bool:
    """ROUND-6 H2 + H3b — setup's allocate path, BEFORE the allocator: make the base (7687) store's owner KNOWN
    when it decides this project's route, so the allocator's owner-aware verdicts (G4 _base_claim_void,
    _base_adopt_ok) rest on the LIVE owner, never on a missing record. Only for a project with no Neo4j pair of
    its own yet (a pair is returned as is; a lost registry entry re-adopts its own instance — `prefer`), no
    operator NEO4J_URI, adoption allowed (not DEEPMAP_NO_BASE_ADOPT=1), and no owner record naming this project.

      H2  the base ANSWERS with our credentials and ANOTHER project's entry claims it (a <= v0.27.4 mis-adoption
          of this project's store, or that project's legitimate one): read its live :_DeepMapMeta owner
          (_foreign_base_owner — which records it for DeepMap's own running instance). A record naming this
          project voids the other claim for it (G4), and the allocator's live branch re-verifies and adopts:
          the real owner acting FIRST after the upgrade keeps its store (it used to be moved to a new instance and
          its store abandoned). The base is never started for this — if it is down, H3b below applies.
      H3b the base is DOWN, a legacy store is on disk with NO owner record at all, both base ports are FREE and
          the shared binary is already extracted: START the store (the legacy layout, revive-only create=False,
          under ensure_neo4j's per-instance start lock) and read its live owner the same way. The allocator then
          adopts it when it is this project's (or unowned + empty — the live branch's rule); otherwise the
          caller stops it again (only when THIS call started it). A legacy single-project user whose store was
          down after a reboot keeps it (it used to get a fresh instance and a full re-sync, its store orphaned).
    Never: a store whose owner record already names ANOTHER project (known — deferred without a start), base
    ports held by another program (deferred as before), a missing binary (no download just to read an owner).
    Returns True iff THIS call started the base store (the caller stops it when the project did not get it).
    Never raises."""
    try:
        if _USER_SET_NEO4J_URI or str(os.environ.get(NO_BASE_ADOPT_ENV, "")).strip() == "1":
            return False
        import port_registry as PR
        if PR.lookup_neo4j_ports(target) or prefer or _base_owner_is(target):
            return False
        user = user or os.environ.get("NEO4J_USER", DEFAULT_USER)
        password = password or os.environ.get("NEO4J_PASSWORD", DEFAULT_PASSWORD)
        base_uri = _bolt_uri(PR.BASE_BOLT)
        if is_bolt_up(base_uri, user, password, timeout=3):
            if _base_claimed_by_other(target):
                _foreign_base_owner(target, user, password)   # H2: records the live owner (our own instance only)
            return False
        if _base_owner_root() is not None:
            return False   # the owner is known and it is not this project (checked above): defer, never start it
        if PR._port_in_use("127.0.0.1", PR.BASE_BOLT) or PR._port_in_use("127.0.0.1", PR.BASE_HTTP):
            return False   # held (another program, or our own instance not answering yet): defer as before
        if not _is_initialized(store_neo4j_version(None), None) or not _shared_binary_present():
            return False   # no legacy store (a clean machine adopts freely) / no binary to run it with
        print(f"[provision] NOTE: the base Neo4j store ({PR.BASE_BOLT}) holds a project graph with no owner record "
              "and is not running — starting it to read whose graph it is (this project keeps it only if it is "
              "its own).", file=sys.stderr)
        diag: dict = {}
        ok = ensure_neo4j(uri=base_uri, user=user, password=password, project_key=None, create=False, diag=diag)
        started = bool(diag.get("started"))
        if not ok:
            if started:
                stop_neo4j(project_key=None)   # it could not be used (e.g. other credentials): leave it as found
            return False
        _foreign_base_owner(target, user, password)          # records the live owner (our own instance only)
        return started
    except Exception:
        return False


def resolved_instance(target):
    """(project_key, bolt, http) for the per-project Neo4j INSTANCE lifecycle, or None for the LEGACY
    single-instance layout (base 7687/7474 pair, operator escape hatch, legacy / no route). The shared
    resolver's answer (its registered pair, or its own instance conf after a lost registry entry — the
    same route apply_ports_for injects). Call AFTER apply_ports_for. Never raises."""
    try:
        if _USER_SET_NEO4J_URI:
            return None   # escape hatch: operator endpoint -> legacy layout, no instances/<key>/ dir
        import port_registry as PR
        r = PR.resolve_neo4j_route(target, honor_env=False)
        if r.source not in ("registry", "instance-conf") or not r.pair:
            return None
        if (r.pair[0], r.pair[1]) == (PR.BASE_BOLT, PR.BASE_HTTP):
            return None   # base pair -> legacy layout (paths(project_key=None))
        return (r.key or PR.project_key(target), r.pair[0], r.pair[1])
    except Exception:
        return None


def _verify_legacy_owner(driver, target, uri):
    """(ok, problem) — may `target` use the graph behind a LEGACY route? sync.verify_legacy_owner (the
    D1 owner check: ok for this project's own graph, or an unowned EMPTY one), with the same policy
    (port_registry.legacy_owner_verdict) read inline only if sync can't be imported. A database error
    propagates."""
    fn = None
    try:
        import sync as _sync
        fn = getattr(_sync, "verify_legacy_owner", None)
    except Exception:
        fn = None
    if callable(fn):
        return fn(driver, target, uri=uri)
    import port_registry as PR
    with driver.session() as s:
        rows = list(s.run("MATCH (m:_DeepMapMeta) RETURN m.owning_root AS owning_root LIMIT 1"))
    owner = next((r["owning_root"] for r in rows if r["owning_root"]), None)
    has_nodes = None
    if not owner:
        with driver.session() as s:
            rows = list(s.run("MATCH (n) WHERE NOT n:_DeepMapMeta RETURN count(n) AS c"))
        has_nodes = bool(rows) and bool(rows[0]["c"])
    return PR.legacy_owner_verdict(target, owner, has_nodes, uri=uri)


def _legacy_route_verdict(target, uri, user, password):
    """(state, problem) for USING a "legacy-unverified" route (the base 7687 of a project with no instance
    of its own — D1): "ok" (the graph is provably this project's, or unowned + empty), "refused" (another
    project's graph / an unowned non-empty one), "auth" (the Neo4j there rejects our credentials) or
    "down" (not reachable / the check itself failed). Only "ok" may be read, written, revived for, served
    or stopped; every other state's `problem` is one user-facing line naming /deep-map:setup.

    ROUND-6 H4: only "refused" is a POLICY refusal, and only its text uses refusal phrasing (the hooks'
    _REFUSAL_WORDS: "can't be shown to be this project's" / "belongs to project" / "holds project"). The
    "down" / "auth" texts are outages / a credentials problem — say so, never "can't be shown to be this
    project's" — so a key-less reader of heal's status never takes them for a final refusal ("auth" keeps
    the word "credentials": the hooks never let a port probe supersede a credentials rejection).
    ROUND-6 H3a: an "ok" whose graph's owner stamp names this project RECORDS that proof next to the store
    (_note_verified_legacy_owner — DeepMap's own running base instance only), so a later setup that finds the
    store DOWN can still tell it is this project's. Never raises."""
    drv = None
    try:
        drv = _make_driver(uri, user, password)
        drv.verify_connectivity()
    except Exception as exc:  # noqa: BLE001
        try:
            if drv is not None:
                drv.close()
        except Exception:
            pass
        if _is_auth_error(exc):
            if _is_rate_limited(exc):
                return "auth", (f"the Neo4j at {uri} is temporarily locking DeepMap's logins after too many failed "
                                "attempts (wrong credentials somewhere — often a server started with an older "
                                "NEO4J_PASSWORD), so the owner of its graph could not be checked — retry shortly; "
                                "this project has no DeepMap Neo4j instance of its own: run /deep-map:setup to give "
                                "it one")
            return "auth", (f"the Neo4j at {uri} (the shared legacy port) rejected DeepMap's credentials, so the "
                            "owner of its graph could not be checked — this project has no DeepMap Neo4j instance "
                            "of its own: run /deep-map:setup to give it one")
        return "down", (f"the Neo4j at {uri} is not reachable ({type(exc).__name__}), so the owner of its graph "
                        "could not be checked — this project has no DeepMap Neo4j instance of its own: run "
                        "/deep-map:setup to give it one")
    try:
        ok, problem = _verify_legacy_owner(drv, target, uri)
        if ok:
            _note_verified_legacy_owner(drv, target, uri)
            return "ok", None
        return "refused", (problem or f"{uri} holds a graph that can't be shown to be this project's — "
                                      "run /deep-map:setup to give this project its own instance")
    except Exception as exc:  # noqa: BLE001
        return "down", (f"could not verify who owns the graph at {uri} ({type(exc).__name__}: {exc}) — run "
                        "/deep-map:setup to give this project its own instance")
    finally:
        try:
            drv.close()
        except Exception:
            pass


def _read_graph_owner(drv):
    """The :_DeepMapMeta owning_root of the graph behind `drv`, or None (unowned). sync._read_owning_root, with
    the same one-row query inline only if sync can't be imported. A database error propagates."""
    fn = None
    try:
        import sync as _sync
        fn = getattr(_sync, "_read_owning_root", None)
    except Exception:
        fn = None
    if callable(fn):
        return fn(drv)
    with drv.session() as s:
        rows = list(s.run("MATCH (m:_DeepMapMeta) RETURN m.owning_root AS owning_root LIMIT 1"))
    return next((r["owning_root"] for r in rows if r["owning_root"]), None)


def _note_verified_legacy_owner(drv, target, uri) -> None:
    """ROUND-6 H3a: the legacy route's owner check just PASSED on `drv` — when the graph's owner stamp names this
    project (not an unowned+empty graph: that proves nothing about ownership), record it next to the base store
    (_record_base_owner: self-guarded to DeepMap's own running base instance), so the proof exists before any later
    decision — e.g. a setup after a reboot that finds the store DOWN adopts it instead of moving the project to a new
    instance. A record already naming this project costs one file read. Only for the local base 7687. Never
    raises."""
    try:
        if not _is_local_base_uri(uri) or _base_owner_is(target):
            return
        owner = _read_graph_owner(drv)
        if owner and _owner_is_this_project(target, owner):
            _record_base_owner(target, source="owner-read")
    except Exception:
        pass


def _escape_hatch_instance(uri):
    """(managed, project_key) for an OPERATOR NEO4J_URI: which DeepMap-managed local instance it names —
    (True, None) for the base 7687 (the legacy layout), (True, key) for a per-project instance (a
    registered bolt port, or an instances/<key>/ conf declaring it), (False, None) for anything DeepMap
    does not run (a remote / BYO / Aura endpoint). Lets `stop` act on the instance the operator named —
    never the base store because some other URI was given. Never raises."""
    try:
        from urllib.parse import urlparse
        import port_registry as PR
        u = urlparse(str(uri))
        if not _is_loopback(u.hostname):
            return False, None
        port = u.port or PR.BASE_BOLT
        if port == PR.BASE_BOLT:
            return True, None
        for k, ent in ((PR.load_registry() or {}).get("projects") or {}).items():
            pair = PR._pair_from_entry(ent, ("bolt_port", "http_port")) if isinstance(ent, dict) else None
            if pair and pair[0] == port:
                return True, k
        inst = deepmap_home() / "instances"
        for d in (sorted(inst.iterdir()) if inst.is_dir() else []):
            own = _neo4j_ports_from_instance_conf(d.name)
            if own and own[0] == port:
                return True, d.name
    except Exception:
        pass
    return False, None


def _neo4j_ports_from_instance_conf(project_key):
    """The (bolt, http) an existing per-project instance's own neo4j.conf declares, or None. Lets a
    project re-adopt its instance after a registry loss (the note is no longer 'safe to delete') so
    re-allocation can't drift it onto new ports. The same parser as port_registry.instance_conf_ports
    (the shared resolver's), which it delegates to. Never raises."""
    try:
        import port_registry as PR
        fn = getattr(PR, "instance_conf_ports", None)
        if callable(fn):
            return fn(project_key)
    except Exception:
        pass
    try:
        conf = paths(None, project_key)["conf"]
        if not conf.exists():
            return None
        txt = conf.read_text(encoding="utf-8")
        bolt = re.search(r"^\s*server\.bolt\.listen_address\s*=\s*[^:\n]*:(\d+)", txt, re.M)
        http = re.search(r"^\s*server\.http\.listen_address\s*=\s*[^:\n]*:(\d+)", txt, re.M)
        if bolt and http:
            return (int(bolt.group(1)), int(http.group(1)))
    except Exception:
        pass
    return None


NO_BASE_ADOPT_ENV = "DEEPMAP_NO_BASE_ADOPT"   # "1": never adopt the base 7687 store (_base_adopt_ok)


def _base_adopt_ok(target, *, user: str | None = None, password: str | None = None) -> bool:
    """Owner-aware verdict: may `target` adopt the BASE (7687/7474) Neo4j instance? Computed in
    provision (port_registry stays neo4j-free). Prevents a 2nd project seizing a live instance owned
    by another (the reported field failure). Conservative — False whenever adoption could MIX graphs.

      1. Base bolt LIVE: read the recorded owner; adopt iff no owner / empty / SAME project — judged by THE
         owner predicate (_owner_is_this_project, ROUND-4 F1: a nested but distinct project is not the
         same; the project's own pre-UTF-8-fix stamp is).
      2. Base NOT reachable with our credentials, yet a listener HOLDS 7687/7474 and it is not
         DeepMap's own legacy instance (the user's Neo4j in Docker/Desktop, or any other program):
         DEFER — adopting could only fail preflight ("port already in use") and wedge setup; this
         project gets its own instance on the next free pair. (2026-09 field report.)
      3. Base DOWN but a legacy default store exists: adopt ONLY on proof that the store is this project's —
         its owner record (_record_base_owner: written when setup's sync to the base succeeded, or by a live
         owner read) names this project. Registry entries prove nothing about graph ownership: the adopter's
         OWN entry (just written by its MCP allocation) used to count as "the only project, so the owner",
         and a pre-registry owner with no entry was then locked out of its own store (2026-09 review).
         Otherwise DEFER: this project gets its own instance (setup re-syncs it anyway), the store stays
         with its owner. (ROUND-6 H3b: setup's _settle_base_owner runs BEFORE the allocator and, for a down
         store with NO record at all and free base ports, starts it — so this verdict then takes branch 1 on the
         LIVE owner instead of deferring on a missing record.)
      4. No legacy store (clean machine): adopt freely — the first project keeps 7687 (zero drift).
    DEEPMAP_NO_BASE_ADOPT=1 (an opt-in operator switch) -> never: the project always gets an instance of its
    own, which `instance-rm` can remove (the base store is never an instance-rm target, so a throwaway project
    that took 7687 could not be cleaned up by any DeepMap command). The benchmark's per-clone builds get the same
    guarantee WITHOUT this switch (C14): benchmark/clone_graph.py pre-claims a non-base pair for the clone with
    port_registry.allocate_neo4j_ports(clone, base_adopt_ok=lambda: False) before setup runs, and setup adopts
    that registered pair as-is (this verdict is never asked for it).

    What a LEGACY single-project user (graph on the base store, no bolt pair of its own — <= v0.25 or pre-registry)
    sees after the upgrade (ROUND-6 H3):
      * base ports FREE (DeepMap's own store, down after a reboot): the SessionStart heal never starts a store
        whose owner it can't read, so it says "run /deep-map:setup"; setup starts the store, reads its owner and —
        when it is this project's (or unowned + empty) — keeps it: the base pair is recorded for the project, the
        same graph is re-synced in place, nothing is orphaned. Any earlier session in which heal verified the
        running store also recorded the proof (H3a), so setup then adopts it from the record without a start.
        An unowned but POPULATED store (a pre-owner-stamping build's) can't be proven this project's: it is
        stopped again and the project gets its own instance (a full re-sync); the old store stays on disk.
      * base ports HELD by another program (e.g. a Neo4j in Docker took 7687): nothing is started; the project
        gets its own instance and a full re-sync, and the legacy store stays on disk untouched (unreachable while
        the ports are held — set NEO4J_URI to use that other Neo4j instead)."""
    if str(os.environ.get(NO_BASE_ADOPT_ENV, "")).strip() == "1":
        return False
    user = user or os.environ.get("NEO4J_USER", DEFAULT_USER)
    password = password or os.environ.get("NEO4J_PASSWORD", DEFAULT_PASSWORD)
    try:
        import port_registry as PR
        base_uri = _bolt_uri(PR.BASE_BOLT)
        if is_bolt_up(base_uri, user, password, timeout=3):
            # A LIVE base: read the owner (and, if unowned, whether the graph is populated) on ONE
            # driver. Conservative — a base we could NOT inspect, or an unowned-but-POPULATED base
            # (a pre-owner-stamping legacy store), must NEVER be adopted: adopting would merge THIS
            # project's root-relative keys into an unknown graph (the anti-pattern this gate exists
            # to prevent). Only an unowned+EMPTY base (someone hand-started a blank Neo4j) or a
            # same-project owner is safe to adopt.
            read_ok = True
            owner = None
            populated = False
            try:
                import sync
                drv = _make_driver(base_uri, user, password)
                try:
                    owner = sync._read_owning_root(drv)
                    if owner is None:
                        populated = sync._graph_has_nodes(drv)
                finally:
                    drv.close()
            except Exception:
                read_ok = False
            if not read_ok:
                return False   # a live base we could not inspect -> defer (get a fresh instance)
            if owner is None:
                return not populated   # unowned+EMPTY -> adopt (zero-drift); unowned+POPULATED -> defer
            _record_base_owner(identity=owner)   # proof, either way: keeps a later down-store verdict true
            return _owner_is_this_project(target, owner)   # F1: THE owner predicate (E1 legacy stamps too)
        # NOT reachable with our credentials — but "can't authenticate" is not "down": a FOREIGN
        # listener on 7687/7474 must never be adopted (see 2. above).
        if _base_held_by_other():
            print(f"[provision] NOTE: Neo4j ports {PR.BASE_BOLT}/{PR.BASE_HTTP} are held by another "
                  "program (not DeepMap's instance — e.g. your own Neo4j in Docker/Desktop). It is left "
                  "untouched; this project gets its OWN DeepMap instance on the next free ports. To use "
                  "that existing Neo4j instead, set NEO4J_URI (+ NEO4J_PASSWORD) and re-run.",
                  file=sys.stderr)
            return False
        # base is DOWN. Does a LEGACY default store exist on disk?
        try:
            db = paths(None)["databases"]
            has_legacy = db.exists() and any(db.iterdir())
        except Exception:
            has_legacy = False
        if not has_legacy:
            return True   # clean machine -> first project keeps base (zero drift)
        if _base_owner_is(target):
            return True   # proven: the store's recorded owner is this project
        print(f"[provision] NOTE: the base Neo4j ({PR.BASE_BOLT}) holds a project graph but is not running, and "
              "nothing proves it is this project's — leaving it for its owner; this project gets its own "
              "instance (setup builds its graph there).", file=sys.stderr)
        return False
    except Exception:
        return False


_BASE_OWNER_FILE = "deepmap_base_owner.json"


def _base_owner_path() -> Path:
    """The base/legacy store's owner record — next to the store (<base data dir>/deepmap_base_owner.json), so
    it lives and dies with it (deleting the store's data dir, the auth remedy, removes it too)."""
    return paths(None)["data"] / _BASE_OWNER_FILE


def _record_base_owner(target=None, *, identity: str | None = None, source: str | None = None) -> None:
    """Record which project owns the base/legacy Neo4j store, ONLY on proof: setup's sync to the base
    succeeded (sync stamps / verifies the graph's :_DeepMapMeta owner), or a live owner read (`identity` =
    the graph's owning_root; or `target` with source="owner-read" when the read verified that owner is
    `target`'s project — ROUND-6 H3a). Lets _base_adopt_ok tell the owner from anyone else while the store is
    DOWN. Only for DeepMap's OWN base store: a record is written only while DeepMap's legacy instance is the
    one running (_legacy_base_instance_alive) — a foreign Neo4j that accepts our credentials on 7687 says
    nothing about the store on disk. Best-effort; never raises."""
    try:
        import port_registry as PR
        root = str(identity) if identity else PR.project_identity(target)
        if _base_owner_root() == root.replace("\\", "/"):
            return   # unchanged — no probe, no rewrite on every live check
        if not _legacy_base_instance_alive():
            return
        _write_base_owner_record(root, source=source or ("owner-read" if identity else "setup-sync"))
    except Exception:
        pass


def _base_owner_root() -> str | None:
    """The project root the base store's owner record names, or None. Never raises."""
    try:
        rec = json.loads(_base_owner_path().read_text(encoding="utf-8"))
        root = rec.get("root") if isinstance(rec, dict) else None
        return str(root) if root else None
    except Exception:
        return None


def _write_base_owner_record(root, *, source: str = "setup-sync") -> bool:
    """Atomically write the base store's owner record naming `root` (a project identity). Only when the
    store's data dir already exists — never creates dirs inside the shared Neo4j home. Never raises."""
    try:
        path = _base_owner_path()
        if not path.parent.is_dir():
            return False
        rec = {"root": str(root).replace("\\", "/"), "source": source,
               "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".owner-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                json.dump(rec, f)
            os.replace(tmp, path)
            tmp = None
        finally:
            if tmp is not None:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
        return True
    except Exception:
        return False


def _base_owner_is(target) -> bool:
    """True iff the base store's owner record names `target`'s project — judged by THE owner predicate
    (_owner_is_this_project, F1: the project's identity or a path of the same identity, or an identity a
    pre-UTF-8-fix build stamped; a nested but distinct project is NOT this one). False when there is no
    record or it can't be read. Never raises."""
    try:
        root = _base_owner_root()
        return bool(root) and _owner_is_this_project(target, root)
    except Exception:
        return False


def _owner_is_this_project(target, owner) -> bool:
    """ROUND-4 F1 — THE owner predicate for every "is that graph / store this project's?" question in
    provision: port_registry.owner_is_this_project (identity-aware containment: only paths that map to the
    same project identity count as one project, a vanished narrower stamp still does; plus the identities a
    pre-UTF-8-fix build computed — E1). sync._same_project (the same predicate, via sync) only if
    port_registry lacks it. Never raises (-> False)."""
    try:
        if not owner:
            return False
        import port_registry as PR
        fn = getattr(PR, "owner_is_this_project", None)
        if callable(fn):
            return bool(fn(target, owner))
    except Exception:
        return False
    try:
        import sync
        return bool(sync._same_project(sync._project_identity(target), owner))
    except Exception:
        return False


def _foreign_base_owner(target, user: str | None = None, password: str | None = None) -> str | None:
    """F2: the owner of the LIVE base (7687) graph when it provably is ANOTHER project's, else None. Only a
    recorded :_DeepMapMeta owner counts (only DeepMap writes it): an unowned graph, an unreadable one or a
    down base -> None (no proof). Whatever process serves 7687, its graph IS what a base claim routes this
    project to — the same live read _base_adopt_ok's live branch trusts — so another project's owner there
    releases / refuses the claim even when that process can't be shown to be DeepMap's legacy instance. The
    owner is RECORDED next to the store only for DeepMap's own running instance (_record_base_owner guards
    that: a foreign Neo4j says nothing about the store on disk), so a later check while it is DOWN still
    knows it. Never raises."""
    try:
        import port_registry as PR
        user = user or os.environ.get("NEO4J_USER", DEFAULT_USER)
        password = password or os.environ.get("NEO4J_PASSWORD", DEFAULT_PASSWORD)
        base_uri = _bolt_uri(PR.BASE_BOLT)
        if not is_bolt_up(base_uri, user, password, timeout=3):
            return None
        import sync
        drv = _make_driver(base_uri, user, password)
        try:
            owner = sync._read_owning_root(drv)
        finally:
            try:
                drv.close()
            except Exception:
                pass
        if not owner:
            return None
        _record_base_owner(identity=owner)
        return None if _owner_is_this_project(target, owner) else str(owner)
    except Exception:
        return None


def _foreign_base_record(target) -> str | None:
    """F2: the project the base store's owner record names when it is ANOTHER project, else None (no record,
    or it names this one). A file read only — no Neo4j, no process probe. Never raises."""
    try:
        rec = _base_owner_root()
        return rec if rec and not _owner_is_this_project(target, rec) else None
    except Exception:
        return None


def _foreign_claim_text(other, where, basis: str) -> str:
    """The one-line refusal for a base claim whose store is project `other`'s (`basis` says how that is known)."""
    return (f"this project is registered on the base Neo4j ({where}), but that store belongs to project "
            f"{other} ({basis}) — a claim an older DeepMap wrote by mistake; DeepMap never uses another "
            "project's graph as this one's: run /deep-map:setup to give this project its own instance")


def _is_base_claim_route(target, uri) -> bool:
    """After apply_ports_for: True iff THIS project's Neo4j route is its REGISTRY claim on the local base store
    (7687, 7474) — not an operator NEO4J_URI, not a per-project instance, not the legacy route. Never raises."""
    try:
        return (not _USER_SET_NEO4J_URI and neo4j_route_source() == "registry" and _is_local_base_uri(uri)
                and resolved_instance(target) is None and _is_base_claim(target))
    except Exception:
        return False


def _foreign_base_claim_problem(target, uri, *, live: bool = False, user: str | None = None,
                                password: str | None = None) -> str | None:
    """F2 for the LOOKUP verbs (heal / status / stop / mcp-start — call after apply_ports_for; hibernate, which
    resolves no ports, asks _base_claim_is_foreign): the
    one-line refusal when THIS project's registry claim on the base store (7687) is one the <= v0.27.4
    mis-adoption wrote — the store belongs to ANOTHER project — else None. Using that route would read, serve,
    or stop that project's graph as this one's. Setup is where the claim is re-verified and RELEASED; these
    verbs only refuse.
      * the store's owner record names another project -> refused (a file read — no Neo4j, no process probe);
      * `live` and NO record names this project yet (a pre-upgrade claim: the record is new in v0.28): the
        LIVE base's :_DeepMapMeta owner decides (_foreign_base_owner — reads only a base that is already up;
        DeepMap's own instance gets the answer recorded, so the next check is the file read again).
    A record naming this project skips the read (the SessionStart heal pays nothing). Never raises."""
    try:
        if not _is_base_claim_route(target, uri):
            return None
        problem = foreign_base_claim_problem(target, uri)
        if problem or not live or _base_owner_is(target):
            return problem
        other = _foreign_base_owner(target, user, password)
        return _foreign_claim_text(other, uri, "the owner its live graph records") if other else None
    except Exception:
        return None


def foreign_base_claim_problem(target, uri=None) -> str | None:
    """PUBLIC (for the CLI / sync route checks — no apply_ports_for state needed): the one-line refusal when
    THIS project's registry entry claims the base (7687, 7474) pair while the base store's owner record
    names ANOTHER project (a <= v0.27.4 mis-adoption — F2), else None. Not in escape-hatch mode (an operator
    NEO4J_URI owns the endpoint). A registry + file read only. Never raises."""
    try:
        if _USER_SET_NEO4J_URI or not _is_base_claim(target):
            return None
        other = _foreign_base_record(target)
        if not other:
            return None
        import port_registry as PR
        return _foreign_claim_text(other, uri or _bolt_uri(PR.BASE_BOLT), "its owner record")
    except Exception:
        return None


def _is_base_claim(target) -> bool:
    """True iff THIS project's registry entry records the base (7687, 7474) pair. Never raises."""
    try:
        import port_registry as PR
        return PR.lookup_neo4j_ports(target) == (PR.BASE_BOLT, PR.BASE_HTTP)
    except Exception:
        return False


def _legacy_base_instance_alive() -> bool:
    """True iff DeepMap's OWN legacy base Neo4j is running: under ANY extracted version (a NEO4J_VERSION
    change must not hide it), a neo4j-community-*/run/neo4j.pid names a live PID whose command line runs
    from DeepMap's Neo4j home (the launcher is `<home>/neo4j/neo4j-community-<v>/bin/neo4j[.bat] console`).
    A pid file outlives a reboot/crash (only stop_neo4j clears it) and its PID gets recycled, so liveness
    alone would let an unrelated process pose as our instance and re-wedge setup on a foreign-held base.
    A readable command line decides (marker = `<home>/neo4j/neo4j-community-`, so e.g. a process tailing
    `<home>/neo4j-console.log` can't match). An UNREADABLE one (Windows hides SYSTEM/service processes'
    command lines from a non-elevated user — exactly what a recycled PID usually lands on) falls back to
    the image name, which is always visible: only a launcher-shaped image (cmd.exe / java / the neo4j
    script's shell) stays conservatively "ours"; svchost & co. do not. Never raises."""
    try:
        root = deepmap_home() / "neo4j"
        for pf in sorted(root.glob("neo4j-community-*/run/neo4j.pid")):
            try:
                pid = int(pf.read_text(encoding="utf-8").strip())
            except Exception:
                continue
            if not _pid_alive(pid):
                continue
            if _pid_identity(pid, recorded=_recorded_create_time(_neo4j_identity_file(pf), pid),
                             pidfile=pf) is False:
                continue   # D2: the pid was recycled after this pid file was written — not our launcher
            if _is_deepmap_neo4j_pid(pid):
                return True
        return _adopt_base_server_jvm()
    except Exception:
        return False


def _adopt_base_server_jvm() -> bool:
    """ROUND-7 I4 / H2: DeepMap's own base JVM running under a STALE pid file — the loser of the v0.27.4 start race
    wrote its dead pid last, so every record writer, `stop` and hibernate were blind to the JVM and G4 locked the
    store's real owner out (2026-09 review). When the base bolt / http port is held and an extracted binary is
    present, a server JVM whose --config-dir is that binary's base conf (under DeepMap's Neo4j home) IS DeepMap's
    legacy base instance: exactly one such JVM -> its pid is RE-RECORDED in that binary's run/neo4j.pid (+ its start
    stamp), so records, stop and hibernate work again -> True. Anything else -> False. Never raises."""
    try:
        import port_registry as PR
        if not (PR._port_in_use("127.0.0.1", PR.BASE_BOLT) or PR._port_in_use("127.0.0.1", PR.BASE_HTTP)):
            return False
        for nhome in sorted((deepmap_home() / "neo4j").glob("neo4j-community-*")):
            if not _binary_present_at(nhome):
                continue
            pids = _find_neo4j_server_pids(nhome / "conf")
            if len(pids) != 1:
                continue
            pid = pids[0]
            pf = nhome / "run" / "neo4j.pid"
            try:
                pf.parent.mkdir(parents=True, exist_ok=True)
                pf.write_text(str(pid), encoding="utf-8")
                _neo4j_identity_file(pf).write_text(json.dumps(
                    {"pid": pid, "created": _pid_create_time(pid), "project_key": None, "source": "adopted-by-conf"}),
                    encoding="utf-8")
                print(f"[provision] NOTE: DeepMap's base Neo4j (pid {pid}) was running under a stale pid file — "
                      "re-recorded it (stop, hibernate and the store's owner record work again).", file=sys.stderr)
            except OSError:
                pass
            return True
        return False
    except Exception:
        return False


def _is_deepmap_neo4j_pid(pid) -> bool:
    """True iff a LIVE `pid` is (conservatively) DeepMap's own Neo4j launcher/JVM — every instance runs
    the ONE shared binary under <home>/neo4j/neo4j-community-<v>/, so a readable command line must carry
    that marker; an unreadable one falls back to the image name (launcher-shaped = ours; see
    _legacy_base_instance_alive). The pid-recycling guard before any Neo4j stop. Never raises."""
    try:
        marker = str(deepmap_home() / "neo4j").replace("\\", "/").lower().rstrip("/") + "/neo4j-community-"
        cmd = _pid_cmdline(pid)
        if cmd is not None:
            return marker in cmd.replace("\\", "/").lower()   # readable and not ours -> a recycled PID
        image = _pid_image(pid)
        return image is None or image.lower() in _LEGACY_LAUNCHER_IMAGES
    except Exception:
        return True   # can't tell -> the pre-existing behaviour (never block a genuine stop)


# Image names DeepMap's legacy Neo4j launcher pid can have: `cmd.exe /c neo4j.bat console` on Windows;
# the neo4j shell script (or the JVM it execs) on POSIX.
_LEGACY_LAUNCHER_IMAGES = frozenset({"cmd.exe", "java", "java.exe", "neo4j", "bash", "sh", "dash"})


def _base_held_by_other() -> bool:
    """True iff something LISTENS on the base Neo4j bolt 7687 or http 7474 and it is NOT DeepMap's own
    legacy base instance (_legacy_base_instance_alive). Only meaningful AFTER is_bolt_up(base) returned
    False: then the listener is one we cannot use — the user's own Neo4j (Docker / Desktop / another
    tool, other credentials) or an unrelated program — and preflight would refuse those ports anyway.
    Our OWN legacy instance holding them (a password mismatch) is deliberately NOT "other": the old
    verdict + preflight's "set NEO4J_PASSWORD" hint stay the right answer there. Probes via
    port_registry._port_in_use (the patchable seam every hermetic gate stubs) on 127.0.0.1, exactly the
    host preflight checks. Never raises (False on any error -> the pre-existing verdict applies)."""
    try:
        import port_registry as PR
        held = PR._port_in_use("127.0.0.1", PR.BASE_BOLT) or PR._port_in_use("127.0.0.1", PR.BASE_HTTP)
        return bool(held) and not _legacy_base_instance_alive()
    except Exception:
        return False


def _release_foreign_base_claim(target) -> None:
    """Un-wedge a PERSISTED base claim: if THIS project's registry entry records the base (7687, 7474) and
    that base is provably not this project's to use, drop just the Neo4j pool so the allocator re-scans
    (-> the project's own instance on the next free pair; the MCP pair is kept). Needed because the
    allocator returns an existing entry WITHOUT re-probing. Released when:
      * the base is not reachable with our credentials AND a foreign listener now holds it
        (_base_held_by_other: a claim the pre-fix _base_adopt_ok wrote reading "can't authenticate" as
        "down", or a base the user later handed to Docker — preflight would fail on every setup forever);
      * ROUND-4 F2: the store belongs to ANOTHER project — the live base's :_DeepMapMeta owner
        (_foreign_base_owner), or while it is down its owner record (_foreign_base_record), names a
        project that is not this one. v0.26-v0.27.4's _base_adopt_ok counted the adopter's own new MCP entry
        as proof it owned a DOWN legacy store, so such claims exist in the field: the claimant read, stopped
        and could wipe the real owner's graph, and the owner was locked out of it.
    A base that is merely DOWN with no owner record, unowned, unreadable, this project's own, or our own
    live legacy instance keeps its claim (no proof). Setup (allocate) path ONLY — heal stays lookup-only
    (it refuses such a claim instead). Never raises."""
    try:
        import port_registry as PR
        if not _is_base_claim(target):
            return
        user = os.environ.get("NEO4J_USER", DEFAULT_USER)
        password = os.environ.get("NEO4J_PASSWORD", DEFAULT_PASSWORD)
        if is_bolt_up(_bolt_uri(PR.BASE_BOLT), user, password, timeout=3):
            other = _foreign_base_owner(target, user, password)
            why = (f"but that store holds project {other}'s graph (a claim an older DeepMap wrote by mistake)"
                   if other else None)
        elif _base_held_by_other():
            why = "which another program now holds"
        else:
            other = _foreign_base_record(target)
            why = (f"but that store's owner record names project {other} (a claim an older DeepMap wrote by "
                   "mistake)" if other else None)
        if why and PR.remove_entry(target, drop_neo4j_only=True):
            print(f"[provision] NOTE: this project was registered on Neo4j {PR.BASE_BOLT}/{PR.BASE_HTTP}, {why} "
                  "— releasing that claim: this project gets its own instance (setup builds its graph there) "
                  "and the base store stays with its owner.", file=sys.stderr)
    except Exception:
        pass


def _ports_from_mcp_json(root):
    """The (search, embed) ports declared in <root>/.mcp.json, or None. Heal's last-resort fallback so
    a revive uses exactly the ports Claude Code is configured to connect to. Never raises."""
    try:
        # utf-8-sig: a BOM-prefixed .mcp.json is valid for Claude Code (verified: `claude mcp list` loads it)
        obj = json.loads((Path(root) / ".mcp.json").read_text(encoding="utf-8-sig"))
        servers = obj.get("mcpServers", {}) if isinstance(obj, dict) else {}

        def _p(name):
            m = re.search(r":(\d+)/mcp", str(servers.get(name, {}).get("url", "")))
            return int(m.group(1)) if m else None

        s, e = _p("deepmap-search"), _p("deepmap-embed")
        return (s, e) if s and e else None
    except Exception:
        return None


def _mcp_json_pair_reusable(target):
    """The (search, embed) pair THIS project's .mcp.json already declares, if setup may re-adopt it —
    else None. The MCP twin of _neo4j_ports_from_instance_conf: .mcp.json is where a project with no
    registry entry (pre-registry provision, or a lost registry) has its ports recorded, and it is
    exactly where Claude Code connects. Re-adopting it keeps the committed .mcp.json stable and stops
    setup from stranding servers the SessionStart heal already revived there (heal falls back to
    .mcp.json and never registers) on a pair Claude Code no longer uses. Each port must be FREE or held
    by THIS project's own DeepMap server (_own_mcp_server_pid: owner sidecar naming this project + live
    pid + server command line) — never another project's server on a shared/copied .mcp.json pair,
    never a recycled pid; a claimed-by-another-project pair is rejected by the allocator. Never raises."""
    try:
        import port_registry as PR
        pair = _ports_from_mcp_json(PR.project_identity(target))
        if not pair or pair[0] == pair[1]:
            return None
        host = mcp_host()
        for name, port in zip(MCP_SERVERS, pair):   # (deepmap-search, deepmap-embed) order
            if PR._port_in_use(host, port) and _own_mcp_server_pid(name, port, target) is None:
                return None
        return pair
    except Exception:
        return None


def write_mcp_json(root, host: str, search_port: int, embed_port: int):
    """Write the project-scoped <toplevel>/.mcp.json so THIS project's Claude Code connects to its own
    servers. It is the ONLY wiring: the plugin ships no .mcp.json of its own (a plugin-root copy is loaded
    in every project, and the one DeepMap used to ship named the first project's ports, so every other
    project's plugin-scoped tools answered from the first project's graph). MERGE-preserving
    + semantically idempotent:

      * target is port_registry's git-toplevel identity (where Claude Code looks + what the registry
        keys on);
      * any non-deepmap mcpServers entries + all other top-level keys are preserved verbatim;
      * if the two deepmap-* entries already equal the target {type:http,url}, it is a NO-OP — this
        keeps an already-correct .mcp.json byte-for-byte untouched (no spurious git diff in a repository
        that commits it), whatever its byte formatting;
      * a garbage/unparseable existing file is NOT overwritten (never destroy a hand-edit) — it warns
        and returns False. Returns the path on write/no-op, False on refusal or a failed write (the
        deepmap tools will then NOT register — setup fails on it, #83). Never raises.
      * ROUND-7 (T10d-11): a UTF-8 BOM is parsed and KEPT (Claude Code loads a BOM-prefixed .mcp.json —
        verified with `claude mcp list`); an empty / whitespace-only file is an empty object; an `mcpServers`
        that is not an object (a list, a string, a number) is REFUSED with a warning — never silently
        replaced by the two deepmap entries (a `null` one counts as absent).
      * a rewrite keeps the file's line endings and indent (an LF file is never turned CRLF on Windows —
        every line used to show as changed) and, when the file is git-TRACKED, says so: the deepmap
        entries carry this machine's ports (2026-09 #105). It is a minimal TEXT edit
        (_patch_mcp_json_text): only the deepmap entries' bytes change — every other entry, compact array,
        key order and non-ASCII character stays verbatim; a layout it can't patch safely (the result must
        parse back to exactly the merged object) falls back to a full re-serialisation."""
    path = None
    try:
        import port_registry as PR
        top = Path(PR.project_identity(root))
        path = top / ".mcp.json"
        want = {
            "deepmap-search": {"type": "http", "url": f"http://{host}:{search_port}/mcp"},
            "deepmap-embed":  {"type": "http", "url": f"http://{host}:{embed_port}/mcp"},
        }
        existing = None
        raw = ""
        bom = ""
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8", newline="") as f:
                    raw = f.read()
                if raw.startswith("\ufeff"):
                    bom, raw = "\ufeff", raw[1:]   # kept on write (the user's bytes stay theirs)
                existing = json.loads(raw) if raw.strip() else {}   # an empty file is an empty object
                if not isinstance(existing, dict):
                    raise ValueError("not a JSON object")
            except Exception:
                print(f"[provision] WARNING: {path} is not valid JSON — leaving it untouched. Edit it "
                      "by hand or delete it, then re-run setup.", file=sys.stderr)
                return False
        obj = dict(existing) if isinstance(existing, dict) else {}
        cur = obj.get("mcpServers")
        if cur is not None and not isinstance(cur, dict):
            print(f"[provision] WARNING: {path} has an \"mcpServers\" that is not a JSON object "
                  f"({type(cur).__name__}) — leaving the file untouched (replacing it would drop whatever it holds). "
                  "Make mcpServers an object (or delete the file), then re-run setup.", file=sys.stderr)
            return False
        servers = dict(cur) if isinstance(cur, dict) else {}
        if servers.get("deepmap-search") == want["deepmap-search"] \
                and servers.get("deepmap-embed") == want["deepmap-embed"]:
            return path   # semantic no-op: already correct (no spurious git diff for the default case)
        servers.update(want)
        obj["mcpServers"] = servers
        eol = "\r\n" if "\r\n" in raw else "\n"
        # A minimal TEXT edit first (#105): only the two deepmap entries change — every other server entry,
        # key order, compact array and non-ASCII byte stays exactly as the user / another tool wrote it. The
        # patched text must parse back to exactly the merged object, else the whole file is re-serialised.
        text = _patch_mcp_json_text(raw, want, obj, eol) if (existing is not None and raw.strip()) else None
        if text is None:
            text = json.dumps(obj, indent=_json_indent_of(raw),
                              ensure_ascii=not any(ord(c) > 127 for c in raw)) + "\n"
            text = text.replace("\n", eol) if eol != "\n" else text
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(bom + text)
        if existing is not None and _git_tracked(top, path.name):
            print(f"[provision] NOTE: updated the git-tracked {path}: deepmap-search/deepmap-embed now point at "
                  f"{host}:{search_port}/{embed_port} — these ports are this machine's; review the change "
                  "before committing it (`git diff .mcp.json`).", file=sys.stderr)
        return path
    except Exception as exc:
        print(f"[provision] WARNING: could not write {path or '.mcp.json'} ({type(exc).__name__}: {exc})",
              file=sys.stderr)
        return False


_JSON_SCALAR_RE = re.compile(r"-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?|true|false|null")


def _json_object_spans(text: str):
    """A span tree of the JSON document `text` (whose top level must be an object), for minimal text edits:
    every object is {"start", "end", "members": [(key, key_start, value_start, value_end, child_or_None)]}
    (offsets into `text`; `end` is one past the closing brace). Raises ValueError on anything it can't
    parse — the caller then falls back to a full re-serialisation."""
    n = len(text)

    def ws(i):
        while i < n and text[i] in " \t\r\n":
            i += 1
        return i

    def string_end(i):
        j = i + 1
        while j < n:
            c = text[j]
            if c == "\\":
                j += 2
                continue
            if c == '"':
                return j + 1
            j += 1
        raise ValueError("unterminated string")

    def value(i, depth):
        if depth > 64:
            raise ValueError("too deep")
        i = ws(i)
        if i >= n:
            raise ValueError("unexpected end")
        c = text[i]
        if c == "{":
            node = {"start": i, "members": []}
            j = ws(i + 1)
            if j < n and text[j] == "}":
                node["end"] = j + 1
                return node, j + 1
            while True:
                j = ws(j)
                if j >= n or text[j] != '"':
                    raise ValueError("expected a key")
                ks, ke = j, string_end(j)
                key = json.loads(text[ks:ke])
                j = ws(ke)
                if j >= n or text[j] != ":":
                    raise ValueError("expected ':'")
                vs = ws(j + 1)
                child, ve = value(vs, depth + 1)
                node["members"].append((key, ks, vs, ve, child))
                j = ws(ve)
                if j < n and text[j] == ",":
                    j += 1
                    continue
                if j < n and text[j] == "}":
                    node["end"] = j + 1
                    return node, j + 1
                raise ValueError("expected ',' or '}'")
        if c == "[":
            j = ws(i + 1)
            if j < n and text[j] == "]":
                return None, j + 1
            while True:
                _, j = value(j, depth + 1)
                j = ws(j)
                if j < n and text[j] == ",":
                    j += 1
                    continue
                if j < n and text[j] == "]":
                    return None, j + 1
                raise ValueError("expected ',' or ']'")
        if c == '"':
            return None, string_end(i)
        m = _JSON_SCALAR_RE.match(text, i)
        if not m:
            raise ValueError("bad value")
        return None, m.end()

    root, end = value(0, 0)
    if root is None or ws(end) != n:
        raise ValueError("not a single JSON object")
    return root


def _line_indent(text: str, pos: int):
    """The whitespace before `pos` on its line when only whitespace precedes it there, else None (the
    token shares its line with other tokens — a compact layout)."""
    ls = max(text.rfind("\n", 0, pos), text.rfind("\r", 0, pos)) + 1
    pre = text[ls:pos]
    return pre if pre.strip(" \t") == "" else None


def _patch_mcp_json_text(raw: str, want: dict, merged: dict, eol: str):
    """`raw` with ONLY the deepmap-search / deepmap-embed entries replaced (or inserted into mcpServers, or
    mcpServers inserted into the top-level object) — every other byte kept (#105: a rewrite used to
    re-serialise every other server's entry). Each new value mirrors the layout it replaces (one line vs
    indented block) and the file's indent unit + line endings. Returns None when the text can't be patched
    safely — the result is only accepted if it parses back to exactly `merged`. Never raises."""
    try:
        root = _json_object_spans(raw)
        ind = _json_indent_of(raw)
        unit = "\t" if ind == "\t" else " " * int(ind)

        def fmt(val, member_indent):
            """`val` serialised for a member whose key line is indented by `member_indent` (None = compact)."""
            if member_indent is None:
                return json.dumps(val, ensure_ascii=False)
            txt = json.dumps(val, indent=unit, ensure_ascii=False)
            return txt.replace("\n", eol + member_indent)

        def member(name, val, member_indent):
            return json.dumps(name, ensure_ascii=False) + ": " + fmt(val, member_indent)

        def insert(obj, items):
            """An edit adding `items` [(name, value)] to the object `obj`."""
            if obj["members"]:
                last = obj["members"][-1]
                first_ind = _line_indent(raw, obj["members"][0][1])
                if first_ind is None:     # a one-line object: stay on its line
                    add = "".join(", " + member(k, v, None) for k, v in items)
                else:
                    add = "".join("," + eol + first_ind + member(k, v, first_ind) for k, v in items)
                return (last[3], last[3], add)
            outer = _line_indent(raw, obj["start"])
            if outer is None:             # `"key": {}` on a line with other tokens: its line's indent
                ls = max(raw.rfind("\n", 0, obj["start"]), raw.rfind("\r", 0, obj["start"])) + 1
                outer = raw[ls:ls + (len(raw[ls:obj["start"]]) - len(raw[ls:obj["start"]].lstrip(" \t")))]
            inner = outer + unit
            body = ("," + eol + inner).join(member(k, v, inner) for k, v in items)
            return (obj["start"], obj["end"], "{" + eol + inner + body + eol + outer + "}")

        edits = []
        servers = [m for m in root["members"] if m[0] == "mcpServers"]
        if servers:
            srv = servers[-1][4]              # json.loads keeps the LAST duplicate key
            if srv is None:
                return None                   # mcpServers is not an object: re-serialise
            present = {}
            for m in srv["members"]:
                present[m[0]] = m             # the last duplicate wins, as in json.loads
            missing = []
            for name, val in want.items():
                m = present.get(name)
                if m is None:
                    missing.append((name, val))
                    continue
                if json.loads(raw[m[2]:m[3]]) == val:
                    continue                  # already right: keep its bytes (no diff line for it)
                multiline = any(c in raw[m[2]:m[3]] for c in "\r\n")
                edits.append((m[2], m[3], fmt(val, _line_indent(raw, m[1]) if multiline else None)))
            if missing:
                edits.append(insert(srv, missing))
        else:
            edits.append(insert(root, [("mcpServers", dict(want))]))
        out = raw
        for start, end, rep in sorted(edits, key=lambda e: e[0], reverse=True):
            out = out[:start] + rep + out[end:]
        return out if json.loads(out) == merged else None
    except Exception:
        return None


def _json_indent_of(raw: str):
    """The indent an existing JSON text uses ("\\t" or a space count; 2 when unknown), so a rewrite keeps it."""
    for line in (raw or "").splitlines()[1:]:
        m = re.match(r"^([ \t]+)\S", line)
        if m:
            ws = m.group(1)
            if "\t" in ws:
                return "\t"
            return len(ws) if 1 <= len(ws) <= 8 else 2
    return 2


def _git_argv(git: str, cwd, *args) -> list:
    """`git [-c core.longpaths=true] -C <cwd> <args>` — every git probe provision runs. ROUND-8 J6: on Windows git
    refuses paths past MAX_PATH (260) unless core.longpaths is set (a committed long-path file then read as an
    uncommitted change); harmless where paths are short, and not passed elsewhere (port_registry.git_longpaths_args
    — the one rule)."""
    try:
        import port_registry as PR
        pre = list(PR.git_longpaths_args())
    except Exception:
        pre = ["-c", "core.longpaths=true"] if os.name == "nt" else []
    return [git, *pre, "-C", str(cwd), *args]


def _git_tracked(top: Path, name: str) -> bool:
    """True iff `name` is tracked by git in the work tree at `top`. Never raises (False on any doubt)."""
    try:
        git = shutil.which("git")
        if not git:
            return False
        r = subprocess.run(_git_argv(git, top, "ls-files", "--error-unmatch", "--", name),
                           capture_output=True, timeout=15)
        return r.returncode == 0
    except Exception:
        return False


def _ensure_mcp_json(target) -> None:
    """CREATE <toplevel>/.mcp.json at the current (env-injected) ports IF MISSING — never overwrite an
    existing file (that is the config Claude Code already connects to). Used by heal / mcp-start;
    `setup` writes UNCONDITIONALLY (the deliberate configure step). Never raises."""
    try:
        import port_registry as PR
        if not (Path(PR.project_identity(target)) / ".mcp.json").exists():
            write_mcp_json(target, mcp_host(), mcp_port("deepmap-search"), mcp_port("deepmap-embed"))
    except Exception:
        pass


def _mcp_pidfile(name: str, port: int) -> Path:
    """Per-PORT pidfile so two projects' same-named servers (deepmap-search on 8787 vs 8789) don't
    overwrite each other's pid — `mcp-stop` then manages only THIS project's server, never orphans
    the other. See build/port_registry.py (side-by-side projects)."""
    return deepmap_home() / "run" / f"{name}-{port}.pid"


def _pid_cmdline(pid) -> str | None:
    """The command line of a live process: a str, "" when it is readable but EMPTY (a Linux kernel
    thread / zombie — definitely not a DeepMap process), or None when it can't be read (gone, or on
    Windows a SYSTEM/service process a non-elevated user may not inspect — so None never means "ours";
    see _pid_image). Used ONLY to prove a pid-file PID is really the DeepMap process it claims to be — a
    pid file outlives a reboot and the PID may since belong to an unrelated process, so liveness alone
    never justifies a signal or an identity verdict. Windows: one CIM query with UTF-8 forced on the
    pipe (the console code page would mangle a non-ASCII profile path and the marker would never match);
    Linux: /proc; else `ps`. Never raises."""
    try:
        pid = int(pid)
        if pid <= 0:
            return None
        if is_windows():
            out = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                 "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
                 f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').CommandLine"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
            return (out.stdout or "").strip() or None
        proc = Path(_PROC, str(pid), "cmdline")
        if proc.exists():
            return proc.read_bytes().replace(b"\0", b" ").decode("utf-8", "replace").strip()
        out = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=15)
        return (out.stdout or "").strip() or None
    except Exception:
        return None


def _pid_image(pid) -> str | None:
    """The executable image name of a live process ('cmd.exe', 'svchost.exe', 'java'), or None if
    unknown. Readable WITHOUT elevation even where the full command line is not (Windows tasklist shows
    every process's image), so it is the fallback identity when _pid_cmdline returns None. Never
    raises."""
    try:
        pid = int(pid)
        if pid <= 0:
            return None
        if is_windows():
            out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                                 capture_output=True, text=True, encoding="utf-8", errors="replace",
                                 timeout=15)
            for row in csv.reader(io.StringIO(out.stdout or "")):
                if len(row) >= 2 and row[1].strip() == str(pid):
                    return row[0].strip() or None
            return None
        comm = Path(_PROC, str(pid), "comm")
        if comm.exists():
            return comm.read_text(encoding="utf-8", errors="replace").strip() or None
        out = subprocess.run(["ps", "-p", str(pid), "-o", "comm="], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=15)
        name = (out.stdout or "").strip()
        return Path(name).name if name else None
    except Exception:
        return None


def _mcp_ownerfile(name: str, port: int) -> Path:
    """The per-port OWNER sidecar next to the pid file: {"pid", "project_key", "root"} of the project a
    server was started FOR. Every project's server runs the identical `<python> <plugin>/build/<script>`
    command line (the project lives only in its env), so the pid file + command line prove "a DeepMap
    server", never "THIS project's" — this sidecar is the project identity. A separate file (not a
    richer pid file) so older builds' `int(pidfile)` readers keep working."""
    return _mcp_pidfile(name, port).with_suffix(".owner")


def _config_fp(salt: str, uri, user, password) -> str:
    """A salted fingerprint of a server's Neo4j config (URI + user + password) — lets heal tell that a
    running server carries a STALE config without ever storing the password (#69). Writers pass the CANONICAL
    URI (_canon_uri); readers compare through _config_fp_matches (every spelling of the endpoint)."""
    blob = "\0".join(str(x or "") for x in (salt, uri, user, password))
    return hashlib.sha256(blob.encode("utf-8", "surrogatepass")).hexdigest()[:32]


def _config_fp_matches(salt, fp, config) -> bool:
    """Does an owner record's fingerprint `fp` (salted with `salt`) name the config `config` = (uri, user,
    password)? The URI is compared by ENDPOINT (ROUND-8 J4): a server an older build started with
    bolt://localhost:<port> — fingerprinted over that spelling — serves the same config as bolt://127.0.0.1:<port>,
    so heal never restarts it once for nothing. Never raises (-> False)."""
    try:
        uri, user, password = config
        return any(_config_fp(salt, u, user, password) == fp for u in _uri_spellings(uri))
    except Exception:
        return False


# ROUND-8 J5: WHICH CODE a detached server runs. A project's MCP servers outlive the plugin version that started them;
# after a plugin update heal found them "already listening" and kept the OLD code serving until a setup or a reboot.
# The owner sidecar therefore records the code a server was started from (port_registry's contract: code_root +
# code_version, plus the server `script`), and heal / mcp-start restart THIS project's own server when it differs.
def _code_identity() -> dict:
    """{code_root, code_version} of the DeepMap code this provisioner runs (port_registry.code_identity of this
    build/ dir) — what a server started now runs. Never raises."""
    try:
        import port_registry as PR
        fn = getattr(PR, "code_identity", None)
        if callable(fn):
            rec = fn(HERE)
            if isinstance(rec, dict):
                return dict(rec)
    except Exception:
        pass
    return {"code_root": str(HERE.parent).replace("\\", "/"), "code_version": None}


def _abs_path_text(p: str) -> bool:
    """Does `p` spell an absolute path (POSIX '/…', a UNC / rooted '\\…', or a Windows drive 'C:\\…')?"""
    return bool(p) and (p.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:[\\/]", p) is not None)


def _script_from_cmdline(cmd, script_name: str):
    """The absolute path of `script_name` (e.g. deepmap_search_server.py) a process command line runs, or None. Handles a
    quoted Windows path ("C:\\Program Files\\...\\build\\x.py"), an unquoted token, and a space-joined POSIX command
    line (a path with spaces is resolved by trying each candidate start that names an existing file; ROUND-10 L6: when
    that file is GONE — a removed plugin dir — a candidate `<...>/build/<script>` is still taken when the text before it
    names an existing file, the interpreter: `/usr/bin/python3 /gone/plug ins/deep-map/0.27.4/build/<script>`). Never
    raises."""
    try:
        s = str(cmd or "")
        if not s or script_name not in s:
            return None
        m = re.search(r'"([^"]*[\\/]' + re.escape(script_name) + r')"', s)
        if m:
            return m.group(1)
        end = s.find(script_name) + len(script_name)
        head = s[:end]
        starts = sorted(set([i + 1 for i, ch in enumerate(head) if ch == " "] + [0]))
        for start in starts:
            cand = head[start:].strip()
            if cand and _abs_path_text(cand) and Path(cand).is_file():
                return cand
        for start in starts:
            cand, before = head[start:].strip(), head[:start].strip().strip('"')
            if start and cand and before and _abs_path_text(cand) and Path(cand).parent.name.lower() == "build" \
                    and _abs_path_text(before) and Path(before).is_file():
                return cand
        tok = head.rsplit(" ", 1)[-1]
        return tok if tok.endswith(script_name) and _abs_path_text(tok) else None
    except Exception:
        return None


def _parse_procargs2(raw: bytes):
    """The argv in a macOS KERN_PROCARGS2 buffer — int32 argc, the exec path, NUL padding, then argc NUL-terminated
    arguments (the environment follows) — or None when it is not one. Never raises."""
    try:
        if not isinstance(raw, (bytes, bytearray)) or len(raw) < 5:
            return None
        argc = int.from_bytes(bytes(raw[:4]), sys.byteorder, signed=True)
        rest = bytes(raw[4:])
        end = rest.find(b"\0")
        if argc <= 0 or end < 0:
            return None
        i = end
        while i < len(rest) and rest[i] == 0:
            i += 1
        args = rest[i:].split(b"\0")
        if len(args) < argc:
            return None
        return [a.decode("utf-8", "surrogateescape") for a in args[:argc]]
    except Exception:
        return None


def _darwin_argv(pid):
    """The exact argv of macOS process `pid` (sysctl KERN_PROCARGS2 — the arguments stay apart, unlike `ps`), or None.
    Never raises."""
    try:
        import ctypes
        import ctypes.util
        libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        argmax = ctypes.c_int(0)
        size = ctypes.c_size_t(ctypes.sizeof(argmax))
        mib = (ctypes.c_int * 2)(1, 8)                                   # CTL_KERN, KERN_ARGMAX
        if libc.sysctl(mib, 2, ctypes.byref(argmax), ctypes.byref(size), None, ctypes.c_size_t(0)) != 0:
            return None
        buf = ctypes.create_string_buffer(max(int(argmax.value), 4096))
        size = ctypes.c_size_t(len(buf))
        mib3 = (ctypes.c_int * 3)(1, 49, int(pid))                       # CTL_KERN, KERN_PROCARGS2, pid
        if libc.sysctl(mib3, 3, buf, ctypes.byref(size), None, ctypes.c_size_t(0)) != 0:
            return None
        return _parse_procargs2(buf.raw[:size.value])
    except Exception:
        return None


def _pid_argv(pid):
    """ROUND-10 L6: the EXACT argument vector of live process `pid` — Linux /proc/<pid>/cmdline split on NUL, macOS
    KERN_PROCARGS2 — so a path with a space stays ONE argument even when that path no longer exists (_pid_cmdline joins
    the arguments with spaces, and a removed plugin dir can't be found by trying each split). None elsewhere (Windows
    keeps the quoting in its one command line, which _script_from_cmdline reads) or when unreadable. Never raises."""
    try:
        pid = int(pid)
        if pid <= 0 or is_windows():
            return None
        proc = Path(_PROC, str(pid), "cmdline")
        if proc.exists():
            raw = proc.read_bytes().rstrip(b"\0")
            return [a.decode("utf-8", "surrogateescape") for a in raw.split(b"\0")] if raw else []
        if sys.platform == "darwin":
            return _darwin_argv(pid)
        return None
    except Exception:
        return None


def _script_from_argv(argv, script_name: str):
    """The absolute `<...>/<script_name>` argument of an exact argv (_pid_argv), or None. Never raises."""
    try:
        for a in argv or ():
            a = str(a).strip()
            if a and re.split(r"[\\/]", a)[-1] == script_name and _abs_path_text(a):
                return a
    except Exception:
        pass
    return None


def _running_code_root(pid, name: str, cmd=None):
    """The plugin root (<root>/build/<script>) the live server `pid` runs — the evidence for a server whose owner record
    predates the code fields (a pre-J5 sidecar) or that has no usable owner record at all (a v0.27.4 server). ROUND-10
    L6: read from the process's EXACT argv where the OS keeps one (_pid_argv — Linux /proc, macOS KERN_PROCARGS2: a
    removed root with a space in its path stays one argument), else from its command line (`cmd` when the caller
    already has it); the script must sit in a directory named `build` (port_registry.running_code_root's rule — the
    hook side reads the same root). None when unreadable. Never raises."""
    try:
        sname = MCP_SERVERS[name]["script"]
        script = _script_from_argv(_pid_argv(pid), sname) \
            or _script_from_cmdline(_pid_cmdline(pid) if cmd is None else cmd, sname)
        if not script:
            return None
        parts = [x for x in re.split(r"[\\/]+", str(script)) if x]
        if len(parts) < 3 or parts[-2].lower() != "build":
            return None
        return str(Path(script).parent.parent).replace("\\", "/")
    except Exception:
        return None


# ROUND-9 K4: NEVER A DOWNGRADE. J5 restarted onto ANY different code: an older Claude Code window still on the previous
# plugin version (its SessionStart heal fires on startup / resume / clear / compact) restarted the newer window's servers
# onto the OLD code, and two windows then ping-ponged them. The ONE rule (_code_restart_verdict — port_registry's hook-side
# server_code_stale / hook_state.mcp_code_stale mirror it for the reconnect note):
#   * no recorded root and no recorded version                     -> None  (unknown: never restarted)
#   * the SAME code root (resolved; case-insensitive on Windows):
#       both versions known and different (an in-place update)     -> True  (the running code is gone from that dir, and
#                                                                            there is no other install to flip it back)
#       recorded version UNKNOWN (no owner record — a v0.27.4 server — or one older than the code fields), and the
#       process provably started before <root>/.claude-plugin/plugin.json or <root>/build/<its script> was last
#       written (ROUND-10 L3: a --plugin-dir / git-clone install updated IN PLACE — those two files, never any other
#       file's touch)                                              -> True
#       otherwise                                                  -> False (the same code)
#   * ANOTHER code root:
#       the running root provably no longer holds DeepMap (<root>/build is missing: a removed plugin version)
#                                                                  -> True  (nothing left to protect)
#       its version (the record's, else <root>/.claude-plugin/plugin.json) vs the current one:
#         current NEWER                                            -> True  (an upgrade)
#         current OLDER                                            -> False (never a downgrade)
#         EQUAL                                                    -> False (ROUND-10 L4: never while the running root
#                                                                            still exists — two INSTALLED copies of one
#                                                                            version (two CLAUDE_CONFIG_DIR accounts, two
#                                                                            marketplaces) each read as "the installed
#                                                                            newest" in their own window and ping-ponged
#                                                                            the servers; setup, the deliberate verb,
#                                                                            still restarts its own)
#         unordered                                                -> None  (unknown: never restarted)
# Versions order as semver (numeric core padded, a pre-release below its release, build metadata ignored, a leading v).


def _parse_version(v):
    """(numeric core tuple, pre-release identifiers) of a version string, or None when it is not a version."""
    try:
        s = str(v).strip() if v is not None else ""
        if s[:1] in ("v", "V"):
            s = s[1:]
        s = s.split("+", 1)[0]
        core, _, pre = s.partition("-")
        nums = core.split(".")
        if not core or not all(n.isdigit() for n in nums):
            return None
        return tuple(int(n) for n in nums), (pre.split(".") if pre else [])
    except Exception:
        return None


def _version_cmp(a, b):
    """-1 / 0 / 1 as version `a` orders before / equal to / after `b` (semver precedence: '0.28.0-rc.1' < '0.28.0',
    '1.0' == '1.0.0', 'v0.28.0' == '0.28.0+build'), or None when either is not a version. Never raises."""
    try:
        pa, pb = _parse_version(a), _parse_version(b)
        if pa is None or pb is None:
            return None
        na, nb = list(pa[0]), list(pb[0])
        width = max(len(na), len(nb))
        na += [0] * (width - len(na))
        nb += [0] * (width - len(nb))
        if na != nb:
            return -1 if na < nb else 1
        ra, rb = pa[1], pb[1]
        if not ra or not rb:
            return 0 if (not ra and not rb) else (1 if not ra else -1)   # a pre-release sorts below its release
        for x, y in zip(ra, rb):
            if x == y:
                continue
            if x.isdigit() and y.isdigit():
                return -1 if int(x) < int(y) else 1
            if x.isdigit() != y.isdigit():
                return -1 if x.isdigit() else 1                           # numeric identifiers sort first
            return -1 if x < y else 1
        return 0 if len(ra) == len(rb) else (-1 if len(ra) < len(rb) else 1)
    except Exception:
        return None


def _plugin_json_version(root):
    """The "version" in <root>/.claude-plugin/plugin.json, or None. Never raises."""
    try:
        data = json.loads((Path(str(root)) / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8-sig"))
        v = data.get("version") if isinstance(data, dict) else None
        return (str(v).strip() or None) if v is not None else None
    except Exception:
        return None


def _code_root_gone(root) -> bool:
    """True when `root` PROVABLY no longer holds DeepMap code (<root>/build is missing or not a directory — a removed
    plugin version, or a path of another machine). A path that can't be read (permissions) is NOT gone: a restart is
    never triggered on a guess (port_registry.code_present's rule). Never raises (-> False)."""
    try:
        import stat as _stat
        st = os.stat(os.path.join(str(root), "build"))
        return not _stat.S_ISDIR(st.st_mode)
    except (FileNotFoundError, NotADirectoryError):
        return True
    except Exception:
        return False


def _code_replaced_since(root, started, script=None) -> bool:
    """ROUND-10 L3: was DeepMap's code at `root` replaced IN PLACE after a process started at epoch `started`? True iff
    <root>/.claude-plugin/plugin.json (a release's version marker) or <root>/build/<script> (the server itself) was last
    written more than _CT_TOLERANCE_S after the start — the state after a `git pull` / checkout in a --plugin-dir or
    git-clone install whose servers were started by the previous code. Only those two files count: touching any other
    file never restarts anything. An unknown start or no readable file -> False. Never raises."""
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


def _code_restart_verdict(rec_root, rec_ver, cur=None, *, started=None, script=None):
    """ROUND-9 K4 / ROUND-10 L3+L4: may heal / mcp-start restart this project's own server — recorded to run the code at
    `rec_root` (version `rec_ver`; either may be None) — onto the code running now (`cur` = {code_root, code_version},
    default _code_identity())? `started` = the server process's start as an epoch (for the same-root, unknown-version
    case — L3) and `script` its server script's file name. True = restart; False = never (the same code, an equal
    version from another directory that still exists — L4 —, or the running code is NEWER: never a downgrade); None =
    unknown (never restarted). The rule is the section note above. Never raises (-> None)."""
    try:
        cur = cur if isinstance(cur, dict) else _code_identity()
        cur_root, cur_ver = cur.get("code_root"), cur.get("code_version")
        rec_root = str(rec_root).strip() if rec_root else None
        rec_ver = str(rec_ver).strip() if rec_ver else None
        cur_ver = str(cur_ver).strip() if cur_ver else None
        if not rec_root and not rec_ver:
            return None
        if rec_root and cur_root and _same_path(rec_root, cur_root):
            if rec_ver and cur_ver:
                return rec_ver != cur_ver and _version_cmp(rec_ver, cur_ver) != 0
            if not rec_ver:
                return _code_replaced_since(rec_root, started, script)
            return False
        if rec_root and _code_root_gone(rec_root):
            return True
        if rec_root and not rec_ver:
            rec_ver = _plugin_json_version(rec_root)
        order = _version_cmp(cur_ver, rec_ver)
        if order == 1:
            return True
        if order in (-1, 0):
            return False
        return None
    except Exception:
        return None


def _describe_code(root, ver) -> str:
    """ "0.27.4 from /…/deep-map/0.27.4" — the code a server runs, for messages. Never raises."""
    try:
        ver = ver or (_plugin_json_version(root) if root else None)
        return " from ".join(str(x) for x in (ver, root) if x) or "an older build"
    except Exception:
        return "an older build"


def _recorded_code(owner: dict):
    """(root, version) an owner record names for its server's code — the canonical fields, their older aliases, or the
    recorded server `script` (<root>/build/<script>) — else (None, None). Never raises."""
    try:
        root = next((owner.get(k) for k in ("code_root", "plugin_root") if owner.get(k)), None)
        if not root and owner.get("script"):
            root = str(Path(str(owner["script"])).parent.parent).replace("\\", "/")
        ver = next((owner.get(k) for k in ("code_version", "deepmap_version", "plugin_version") if owner.get(k)), None)
        return (str(root) if root else None, str(ver).strip() if ver else None)
    except Exception:
        return (None, None)


def _is_current_root(root) -> bool:
    """Is `root` the plugin root of the code running now (_code_identity)? Never raises."""
    try:
        cur = _code_identity().get("code_root")
        return bool(root and cur) and _same_path(root, cur)
    except Exception:
        return False


def _describe_same_root(root) -> str:
    """The code a server started before its (same) plugin dir was updated in place runs, for messages (ROUND-10 L3)."""
    return f"an older build from {root} (that directory was updated in place after the server started)"


def _own_server_code_stale(name: str, port: int, target) -> str | None:
    """ROUND-8 J5 / ROUND-9 K3+K4 / ROUND-10 L1+L3: a one-line description of the OLDER DeepMap code the server on `port`
    runs (e.g. "0.27.5 from /…/deep-map/0.27.5") when it is provably THIS project's own server and _code_restart_verdict
    says to restart it onto the code running now (never onto older code — K4). "Provably this project's":
      * with an owner record — an owner sidecar that names the pid file's pid (_mcp_owner_record): it names one of this
        project's keys + live pid + server script + recorded start stamp (_own_mcp_server_pid); a record written before
        the code fields existed is judged by the running process's exact argv / command line (the script path it runs;
        and, for its own plugin dir, whether that dir was updated in place after it started — L3); an unreadable one is
        unknown -> None (left alone); a record naming ANOTHER project -> None;
      * with NO owner record — none at all (every server a v0.27.4 build started — K3, the real upgrade) or one that
        names ANOTHER pid than the pid file / can't be read (ROUND-10 L1: the record of an EARLIER process on the port,
        e.g. a newer build's server that died before an older window's heal respawned it — the hook's rule,
        hook_state.mcp_code_stale): _preowner_server_code_stale's evidence decides.
    The cheap record compare runs first, so the process probes happen only when a restart is warranted. Never
    raises."""
    try:
        if target is None:
            return None
        owner = _mcp_owner_record(name, port)
        if not owner:
            return _preowner_server_code_stale(name, port, target)
        if owner.get("project_key") not in _own_keys(target):
            return None
        root, ver = _recorded_code(owner)
        started = None
        if not root and not ver:
            pid = int(owner["pid"])
            if not _pid_alive(pid):
                return None
            root = _running_code_root(pid, name)
            if not root:
                return None
            # L3: when it started (its recorded stamp — _own_mcp_server_pid below proves the record is this process's)
            stamp = owner.get("created")
            started = _start_epoch(stamp if stamp is not None else _pid_create_time(pid))
        if _code_restart_verdict(root, ver, started=started, script=MCP_SERVERS[name]["script"]) is not True:
            return None
        if _own_mcp_server_pid(name, port, target) is None:
            return None
        if not ver and _is_current_root(root):
            return _describe_same_root(root)
        return _describe_code(root, ver)
    except Exception:
        return None


def _preowner_server_code_stale(name: str, port: int, target) -> str | None:
    """ROUND-9 K3: the v0.27.4 -> this upgrade. v0.27.4 wrote only run/<name>-<port>.pid (no owner sidecar), so J5's
    sidecar rule never restarted its servers and a plugin update kept the old code serving until a reboot. A server
    with NO owner record is this project's own for a code restart only on ALL of this evidence (setup's pre-upgrade rule
    — ROUND-6 H1 — plus the code check):
      * the port registry gives `port` to THIS project (_mcp_port_registered_to — never a pair read from a committed /
        copied .mcp.json);
      * the per-port pid file names a LIVE pid whose READABLE command line runs this server's script, from a code root
        (its exact argv where the OS keeps one — ROUND-10 L6) that _code_restart_verdict says to restart from: older
        code, a removed plugin dir (K4), or — its root being this code's own — a dir updated in place after the process
        started (ROUND-10 L3);
      * that pid is the process the pid file recorded: it started BEFORE the pid file was written (on Linux, where
        that comparison is unproven after a clock step, the readable command line decides — stop_mcp_server's rule);
      * nothing says it is another port's / another project's server (_mcp_pid_owned_elsewhere: its environment, or
        another port's pid file + owner record naming it).
    "No owner record" (ROUND-10 L1) is _mcp_owner_record's None: no .owner file, one that can't be read, or one naming
    ANOTHER pid than the pid file (the record of an earlier process on the port — exactly how stop_mcp_server and the
    hook's reconnect note treat it; the old "any .owner file -> leave it" left such a server on old code forever while
    the note promised a restart). An owner record that names the pid file's pid is the sidecar path's, never this one's
    -> None here. Returns the old code's description or None (left alone). The restart itself goes through
    stop_mcp_server(name, target=target), which re-checks all of this before it signals anything. Never raises."""
    try:
        if _mcp_owner_record(name, port) is not None:
            return None
        if not _mcp_port_registered_to(target, port):
            return None
        pf = _mcp_pidfile(name, port)
        pid = _pidfile_pid(pf)
        if pid is None or not _pid_alive(pid):
            return None
        cmd = _pid_cmdline(pid)
        if not cmd or MCP_SERVERS[name]["script"] not in cmd:
            return None
        root = _running_code_root(pid, name, cmd=cmd)
        if not root:
            return None
        created = _pid_create_time(pid)
        if _code_restart_verdict(root, None, started=_start_epoch(created),
                                 script=MCP_SERVERS[name]["script"]) is not True:
            return None
        same, basis = _pid_check(pid, recorded=None, pidfile=pf, created=created)
        if not (same is True or (same is None and basis == "unproven")):
            return None
        if _mcp_pid_owned_elsewhere(pid, name, port, pf, target=target, created=created):
            return None
        if _is_current_root(root):
            return _describe_same_root(root) + " — started by a DeepMap build that kept no owner record"
        return _describe_code(root, None) + " — started by a DeepMap build that kept no owner record"
    except Exception:
        return None


def _server_config(env=None, password=None):
    """(uri, user, password) a server gets / got: exactly _spawn_mcp_server's derivation from `env`
    (default: the current environment)."""
    e = os.environ if env is None else env
    return (e.get("NEO4J_URI") or DEFAULT_URI, e.get("NEO4J_USER") or DEFAULT_USER,
            password or e.get("NEO4J_PASSWORD") or DEFAULT_PASSWORD)


def _write_mcp_owner(name: str, port: int, pid: int, target, *, created=None, config=None) -> None:
    """Record which project a just-started server belongs to (or clear a stale record when the project
    is unknown), with the process's CREATION TIME (D2 — `created`, epoch seconds or None) and a salted
    fingerprint of the Neo4j config it was started with (`config` = (uri, user, password); the password
    itself is never written). Best-effort — a missing sidecar only means setup won't adopt/restart it.
    Never raises."""
    try:
        of = _mcp_ownerfile(name, port)
        if target is None:
            of.unlink(missing_ok=True)
            return
        import port_registry as PR
        uri, user, pw = config if config else _server_config()
        salt = os.urandom(8).hex()
        rec = {"pid": int(pid), "project_key": PR.project_key(target),
               "root": PR.project_identity(target).replace("\\", "/"),
               "created": created, "neo4j_uri": uri, "neo4j_user": user,
               # J4: the fingerprint is over the CANONICAL URI (readers compare every spelling — _config_fp_matches)
               "cfg_salt": salt, "cfg_fp": _config_fp(salt, _canon_uri(uri), user, pw)}
        # J5: the code this server runs (port_registry's contract: code_root + code_version) and its script
        rec.update(_code_identity())
        rec["script"] = str(HERE / MCP_SERVERS[name]["script"]).replace("\\", "/") if name in MCP_SERVERS else None
        of.write_text(json.dumps(rec), encoding="utf-8")
    except Exception:
        pass


def _mcp_owner_record(name: str, port: int) -> dict | None:
    """The owner sidecar of the server on `port` when it matches the per-port pid file (else None)."""
    try:
        pid = int(_mcp_pidfile(name, port).read_text().strip())
        owner = json.loads(_mcp_ownerfile(name, port).read_text(encoding="utf-8"))
        if isinstance(owner, dict) and int(owner.get("pid", -1)) == pid:
            return owner
    except Exception:
        pass
    return None


def _owned_mcp_pid(name: str, port: int, key: str):
    """The PID of the DeepMap MCP server on `port` that belongs to the project with registry KEY `key`,
    else None (the key-based core of _own_mcp_server_pid — instance-rm has a key, not a root). The pid
    must be alive, run the server script AND be the very process the sidecar recorded (its creation time
    — D2; an unknown time is not proof)."""
    try:
        owner = _mcp_owner_record(name, port)
        if not owner or owner.get("project_key") != key:
            return None
        pid = int(owner["pid"])
        if not _pid_alive(pid):
            return None
        cmd = _pid_cmdline(pid)
        if not (cmd and MCP_SERVERS[name]["script"] in cmd):
            return None
        if _pid_identity(pid, recorded=owner.get("created"), pidfile=_mcp_pidfile(name, port)) is not True:
            return None
        return pid
    except Exception:
        return None


def _own_server_config_stale(name: str, port: int, target, config) -> bool:
    """True iff the server on `port` is provably THIS project's (_own_mcp_server_pid) AND its sidecar's
    config fingerprint differs from `config` (uri, user, password) — e.g. it was started with a wrong
    password or before the project's Neo4j moved (#69: heal kept such a server as "already listening").
    An older sidecar with no fingerprint is unknown -> False (left alone). The cheap fingerprint compare
    runs first, so the process-identity probes (heal runs on every SessionStart) happen only when a
    restart is actually warranted. Never raises."""
    try:
        if target is None:
            return False
        owner = _mcp_owner_record(name, port) or {}
        salt, fp = owner.get("cfg_salt"), owner.get("cfg_fp")
        if not salt or not fp or _config_fp_matches(salt, fp, config):
            return False
        return _own_mcp_server_pid(name, port, target) is not None
    except Exception:
        return False


def _config_vouched(name: str, port: int, config) -> bool:
    """ROUND-10 L2: does the server's owner record (matching its pid file) carry a config fingerprint that names
    `config` (uri, user, password) — i.e. was the running server started with exactly the configuration a restart would
    give it? Then a restart can't swap a working config for a broken one. No record, no fingerprint, or another config
    -> False. Never raises."""
    try:
        owner = _mcp_owner_record(name, port) or {}
        salt, fp = owner.get("cfg_salt"), owner.get("cfg_fp")
        return bool(salt and fp) and _config_fp_matches(salt, fp, config)
    except Exception:
        return False


def _own_keys(target) -> list:
    """Every registry key THIS project is known by: its key (project_key — the entry's key) plus the other
    keys its entry records (a registry key migration's `aliases` / `migrated_from` / `pending_key`, E1). An
    owner sidecar written before the migration — or one whose re-key failed (a Windows sharing violation, a
    process killed between the registry write and the side-file re-key) — still names an old key; it is
    this project's own server all the same. Never raises (-> [] when the key can't be computed)."""
    try:
        import port_registry as PR
        key = PR.project_key(target)
        return [key] + [a for a in _entry_aliases(key) if a != key]
    except Exception:
        return []


def _own_mcp_server_pid(name: str, port: int, target):
    """The PID of THIS PROJECT's DeepMap MCP server on `port`, else None. All must hold: the per-port pid
    file names a pid, the owner sidecar records that SAME pid for this project's key (or one of its
    migrated keys — _own_keys), the pid is alive, and its command line runs the server script (a pid file
    outlives a reboot — a recycled PID must never pass). A server with no owner sidecar (started by an
    older build, or with no known project) is NOT provably ours -> None. The only identity trusted before
    setup adopts a port or signals a process. Never raises."""
    try:
        if target is None:
            return None
        for key in _own_keys(target):
            pid = _owned_mcp_pid(name, port, key)
            if pid is not None:
                return pid
        return None
    except Exception:
        return None


def _mcp_foreign_owner(name: str, port: int, target) -> str | None:
    """The root of ANOTHER project whose live server holds `port` per its owner sidecar, else None. The
    guard that keeps a worktree / copied checkout (whose committed .mcp.json names the original project's
    ports) from stopping, or being wired to, that project's servers (2026-09 #44). A sidecar naming one of
    this project's own migrated keys (E1 — _own_keys) is not another project's. Never raises."""
    try:
        if target is None:
            return None
        owner = _mcp_owner_record(name, port)
        if not owner:
            return None
        if owner.get("project_key") is None or owner.get("project_key") in _own_keys(target) \
                or not _pid_alive(int(owner["pid"])):
            return None
        return str(owner.get("root") or owner.get("project_key"))
    except Exception:
        return None


def _mcp_pid_owned_elsewhere(pid, name: str, port: int, pidfile, *, target=None, created=None) -> str | None:
    """What (other than THIS port's server) provably runs the live MCP-server `pid`, else None — the extra
    evidence stop_mcp_server consults when there is no comparable start record:
      * its environment (Linux) says it serves ANOTHER port (the per-port env var every server is started
        with), or another project (DEEPMAP_PROJECT_KEY not one of this project's keys);
      * ANOTHER port's pid file names the same pid and its owner record's start stamp matches the process.
    Never raises."""
    try:
        env = _proc_environ(pid)
        if env is not None:
            want = env.get(MCP_SERVERS[name]["port_env"])
            if want is not None and str(want).strip() != str(port):
                return f"the {name} server for port {want}"
            pk = env.get("DEEPMAP_PROJECT_KEY")
            if pk and target is not None:
                mine = _own_keys(target)
                if mine and pk not in mine:
                    return f"project {pk}'s {name} server"
        if created is None:
            created = _pid_create_time(pid)
        if created is None:
            return None
        run = deepmap_home() / "run"
        for pf in (sorted(run.glob("deepmap-*-*.pid")) if run.is_dir() else []):
            try:
                if _same_path(pf, pidfile) or int(pf.read_text(encoding="utf-8").strip()) != int(pid):
                    continue
                rec = json.loads(pf.with_suffix(".owner").read_text(encoding="utf-8"))
                if isinstance(rec, dict) and int(rec.get("pid", -1)) == int(pid) \
                        and rec.get("created") is not None and _same_start(rec["created"], created) is True:
                    return f"the server of {pf.stem} (its own pid file and owner record name it)"
            except Exception:
                continue
    except Exception:
        pass
    return None


def _mcp_port_registered_to(target, port) -> bool:
    """True iff the port registry gives `port` to THIS project (its search or embed port). Never raises."""
    try:
        import port_registry as PR
        return int(port) in (PR.lookup_ports(target) or ())
    except Exception:
        return False


def _mcp_pair_claimed_by_other(target, pair) -> str | None:
    """The root (or key) of ANOTHER registered project whose MCP pair includes a port of `pair`, else None —
    for a pair read from .mcp.json (a copied checkout / worktree inherits the original's committed ports).
    Never raises (None when the registry can't be read: the owner-record rule in stop_mcp_server still
    protects)."""
    try:
        import port_registry as PR
        reg = PR.load_registry() or {}
        mine = PR.project_key(target)
        own = set(_own_keys(target)) | {mine}
        want = {int(p) for p in pair}
        for key, ent in (reg.get("projects") or {}).items():
            if key in own or not isinstance(ent, dict):
                continue
            if mine in (ent.get("aliases") or ()):
                continue   # this project's own entry under a migrated key (E1)
            other = PR._pair_of(ent)
            if other and want & set(other):
                return str(ent.get("root") or key)
    except Exception:
        pass
    return None


def _stop_mcp_servers_for_key(key: str, aliases=()) -> None:
    """instance-rm: stop the MCP servers that provably belong to project `key` (owner sidecar naming `key`
    or one of its `aliases` + live pid + server command line) on the MCP pair its registry entry records.
    Another project's server on the same port is never signalled. Best-effort; never raises."""
    try:
        import port_registry as PR
        ent = ((PR.load_registry() or {}).get("projects") or {}).get(key)
        pair = PR._pair_of(ent) if isinstance(ent, dict) else None
        for name, port in zip(MCP_SERVERS, pair or ()):   # (deepmap-search, deepmap-embed) order
            pid = next((p for p in (_owned_mcp_pid(name, port, kk) for kk in [key, *aliases]) if p is not None),
                       None)
            if pid is not None and _terminate_pid(pid, tree=True):
                _mcp_pidfile(name, port).unlink(missing_ok=True)
                _mcp_ownerfile(name, port).unlink(missing_ok=True)
                print(f"[instance-rm] {name}: stopped this project's MCP server (pid {pid}, port {port})")
    except Exception:
        pass


_RESTART_WAIT_S = 15.0   # how long setup's restart waits for a stopped server's port to close
_MCP_BLOCKED: dict = {}  # server name -> why setup's restart left a server it could NOT restart on the port (H1)


def _wait_port_closed(host: str, port: int, wait_s: float) -> bool:
    """Poll until nothing accepts connections on host:port (True) or `wait_s` runs out (False)."""
    deadline = time.time() + max(0.0, float(wait_s))
    while is_http_up(host, port):
        if time.time() >= deadline:
            return False
        time.sleep(0.25)
    return True


def _pidfile_pid(pf: Path):
    try:
        return int(pf.read_text(encoding="utf-8").strip())
    except Exception:
        return None


def _recorded_config_stale(owner: dict, password: str | None) -> bool:
    """True iff an owner sidecar's config fingerprint provably differs from the config a server started now would
    get (_server_config — URI + user + password). A sidecar without a fingerprint is unknown -> False (as
    _own_server_config_stale). Never raises."""
    try:
        salt, fp = owner.get("cfg_salt"), owner.get("cfg_fp")
        return bool(salt and fp) and not _config_fp_matches(salt, fp, _server_config(password=password))
    except Exception:
        return False


_REQUIRE_LISTENER_PROOF = False   # set by start_mcp_servers(restart_own=True) — setup's H1 attribution (ROUND-7)


def _listener_proven_own(name: str, port: int, target, password: str | None = None) -> bool:
    """ROUND-7 I4/H1: may setup call the server LISTENING on `port` this project's, serving its CURRENT config? Its
    owner sidecar (matching the per-port pid file) names this project (or one of its migrated keys), records the
    Neo4j config a server started now would get (URI + user + password fingerprint — an older sidecar without one
    counts as unknown, not stale), and its pid is alive, runs the server script (or its command line is
    unreadable) and is not provably another process (start stamps). A stale pid file, none at all, another
    project's sidecar or a stale config -> False. Never raises."""
    try:
        if target is None:
            return False
        owner = _mcp_owner_record(name, port)
        if not owner or owner.get("project_key") not in _own_keys(target):
            return False
        if _recorded_config_stale(owner, password):
            return False
        pid = int(owner["pid"])
        if not _pid_alive(pid):
            return False
        cmd = _pid_cmdline(pid)
        if cmd is not None and MCP_SERVERS[name]["script"] not in cmd:
            return False
        return _pid_check(pid, recorded=owner.get("created"), pidfile=_mcp_pidfile(name, port))[0] is not False
    except Exception:
        return False


def _unattributed_listener(name: str, host: str, port: int, target) -> str:
    """Print setup's ERROR for a listener on this project's MCP port that setup can't attribute to this project's
    current servers, and return the one-line reason (start_mcp_servers records it; setup persists it)."""
    find = (f"netstat -ano | findstr :{port}" if is_windows() else f"lsof -iTCP:{port} -sTCP:LISTEN -P")
    foreign = _mcp_foreign_owner(name, port, target)
    what = (f"project {foreign}'s {name} server" if foreign else
            "a process setup can't attribute to this project's current servers (no owner record naming this project "
            "with the current Neo4j configuration — a stale pid file or none: an unmanaged server, e.g. one an older "
            "DeepMap build started, or another program)")
    reason = f"{name} on {host}:{port} is held by {what}"
    print(f"[provision] ERROR: {reason}. It may answer deepmap_search / deepmap_impact from ANOTHER graph than this "
          f"project's, so setup does not report success over it. Stop that process by hand (find it: {find}), then "
          "re-run /deep-map:setup.", file=sys.stderr)
    return reason


def _restart_blocked(name: str, host: str, port: int, pid, why: str) -> str:
    """Print setup's ERROR for a DeepMap server it could not restart on this project's port, and return the one-
    line reason (start_mcp_servers records it; setup's verdict names it)."""
    find = (f"netstat -ano | findstr :{port}" if is_windows() else f"lsof -iTCP:{port} -sTCP:LISTEN -P")
    reason = (f"{name} on {host}:{port} is still served by a DeepMap MCP server (pid {pid}) that setup could not "
              f"restart — {why}")
    print(f"[provision] ERROR: {reason}. It may answer deepmap_search / deepmap_impact from ANOTHER graph than this "
          f"project's, so setup does not report success over it. Stop that process by hand (find it: {find}), then "
          "re-run /deep-map:setup.", file=sys.stderr)
    return reason


def _restart_own_mcp_server(name: str, target, password: str | None = None) -> str | None:
    """setup-only: stop THIS project's already-running DeepMap server `name` so the start that follows
    brings it up with the CURRENT env. start_mcp_server is idempotent on "port listening", so without
    this a server started earlier keeps a STALE config — e.g. the SessionStart heal revived it with a
    NEO4J_URI that setup has since re-allocated (a foreign-held base), or it runs a pre-upgrade build —
    and setup would report success while search hits the wrong graph. Signals ONLY a server that is THIS
    project's:
      * proven (_own_mcp_server_pid: owner sidecar + live pid + command line + creation time), or
      * ROUND-6 H1 — a PRE-UPGRADE server: a per-port pid file with NO owner sidecar (every server a <= v0.27.4
        build started) on a port the registry gives THIS project — the rule stop_mcp_server(name, target)
        itself applies (_mcp_port_registered_to), which also keeps its pid-file checks (server script, a
        process older than its pid file, an environment naming no other project): it is stopped via
        stop_mcp_server(name, target=target) and respawned by the start that follows.
    Never another project's server that sits on this port, never a recycled PID.

    Returns None when setup may go on, or a one-line reason when a DeepMap server setup could NOT restart still
    holds this project's port and may be serving a stale config — a pre-upgrade server whose stop was refused /
    that kept the port, a live sidecar-less DeepMap server on a port NOT registered to this project (unknown
    project + config), or this project's own server whose process can't be verified while its recorded Neo4j
    config provably differs from the current one. start_mcp_servers then fails for it (setup never reports
    success while it keeps serving another graph — the field case: the old server answered from another
    project's graph after setup moved this project off a mis-adopted base claim). Anything else not provably
    ours (an unmanaged listener, a stale pid file, another project's server — start_mcp_server refuses that
    itself, an unverifiable own server whose config is current) is left alone with a WARNING. Never raises."""
    try:
        host, port = mcp_host(), mcp_port(name)
        if not is_http_up(host, port):
            return None
        pf = _mcp_pidfile(name, port)
        if not pf.exists():
            return None   # an unmanaged/foreign listener — start_mcp_server reports it as "already listening"
        if _own_mcp_server_pid(name, port, target) is not None:
            print(f"[provision] {name}: restarting the running server on {host}:{port} to apply the current "
                  "config (the new Claude Code session below reconnects it)")
            if stop_mcp_server(name):
                _wait_port_closed(host, port, _RESTART_WAIT_S)
            return None
        owner = _mcp_owner_record(name, port)
        pid = _pidfile_pid(pf)
        alive = pid is not None and _pid_alive(pid)
        cmd = _pid_cmdline(pid) if alive else None
        deepmap_server = alive and (cmd is None or MCP_SERVERS[name]["script"] in cmd)
        if owner is None and _mcp_port_registered_to(target, port):
            if not deepmap_server:
                # a STALE pid file (its pid is gone, or recycled by another program): the listener is not the server
                # it recorded — an unmanaged listener, as with no pid file at all. stop_mcp_server clears the stale
                # file (never signalling) and warns about the listener.
                stop_mcp_server(name, target=target)
                return None
            print(f"[provision] {name}: the server on {host}:{port} (pid {pid}) has no owner record — an older "
                  "DeepMap build started it on this project's port — restarting it so it serves this project's "
                  "current graph (the new Claude Code session below reconnects it)")
            if stop_mcp_server(name, target=target) and _wait_port_closed(host, port, _RESTART_WAIT_S):
                return None
            if not is_http_up(host, port):
                return None
            return _restart_blocked(name, host, port, pid, "an older DeepMap build started it (no owner record) "
                                    "and it could not be shown to be the process its pid file recorded, or would "
                                    "not stop")
        foreign = owner is not None and owner.get("project_key") not in _own_keys(target)
        if not foreign and deepmap_server:
            if owner is None:
                return _restart_blocked(name, host, port, pid, "it has no owner record and this port is not "
                                        "registered to this project, so neither its project nor its Neo4j config "
                                        "is known")
            # its sidecar's config is provably not the current one — unless the start stamps PROVE the pid is another
            # process by now (a stale pid file: the listener is then an unmanaged one, as above)
            if _recorded_config_stale(owner, password) \
                    and _pid_check(pid, recorded=owner.get("created"), pidfile=pf)[:2] != (False, "stamp"):
                return _restart_blocked(name, host, port, pid, "its owner record names this project but a Neo4j "
                                        "configuration that is no longer the current one, and the process can't be "
                                        "verified as the one recorded")
        print(f"[provision] WARNING: {name} is already listening on {host}:{port} but could not be "
              "verified as THIS project's DeepMap server (another project's, or one started by an "
              "older DeepMap build), so it was NOT restarted — it may be serving a stale config. If "
              f"it is this project's: {_provision_cmd('mcp-stop')} then re-run /deep-map:setup.",
              file=sys.stderr)
        return None
    except Exception:
        return None


def is_http_up(host: str, port: int, timeout: float = 2.0) -> bool:
    """True iff something is accepting TCP connections on host:port (cheap liveness probe)."""
    import socket
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


MCP_START_WAIT_S = 120        # listen budget per server (a loaded box took >40s — #109); DEEPMAP_MCP_START_WAIT
# The verify's first tool call may wait on the servers' cold start: they warm the model + driver in the
# background and a call arriving meanwhile waits up to mcp_bounds.warmup_timeout_s() (300 s) before its
# own tool_timeout_s() (60 s). The default covers both (+ slack) so a slow cold start reads as
# success-when-warm, not a failed verify (#99). Env DEEPMAP_MCP_VERIFY_DEADLINE overrides it.
MCP_VERIFY_DEADLINE_S = 390
_MCP_STARTED: list = []       # servers THIS process spawned (heal reports them: a live client must reconnect)
_MCP_CODE_RESTARTED: dict = {}  # server name -> the older code heal / mcp-start restarted it from (heal status, K3)
_VERIFY_DETAIL: dict = {}     # server name -> why its last verify_mcp failed (setup's final banner)
_VERIFY_DEGRADED: dict = {}   # server name -> why its last verify_mcp passed DEGRADED (keyword-only search)
_VERIFY_AUTH: dict = {}       # server name -> "rejected" | "rate-limited": its last verify got Neo4j's AUTH answer


def _env_seconds(var: str, default: int) -> int:
    try:
        v = int(os.environ.get(var, ""))
        return v if v > 0 else default
    except (TypeError, ValueError):
        return default


def _record_mcp_server(name: str, port: int, pid: int, target, env=None) -> None:
    """Write the per-port pid file + owner sidecar for a server WE started and that owns its port: its
    project, its creation time (D2) and a fingerprint of the Neo4j config in `env` (the child's env;
    default the current one) — see _write_mcp_owner."""
    try:
        pf = _mcp_pidfile(name, port)
        pf.parent.mkdir(parents=True, exist_ok=True)
        pf.write_text(str(pid))
        _write_mcp_owner(name, port, pid, target, created=_pid_create_time(pid), config=_server_config(env))
    except OSError as exc:
        print(f"[provision] WARNING: could not write the pidfile for {name} ({exc}); the server is "
              f"running (pid {pid}) but `mcp-stop` won't manage it — stop it manually if needed.",
              file=sys.stderr)


def start_mcp_server(name: str, *, password: str | None = None, wait: int | None = None, target=None) -> bool:
    """Start one MCP server detached over streamable-HTTP; wait until it accepts connections.

    Idempotent: if the port is already serving, it's left alone — unless its owner sidecar names
    ANOTHER project (False + a WARNING: never report someone else's server as this project's). The server
    outlives this process so a Claude Code reload can connect to the `.mcp.json` HTTP URL. `target` (the
    project it serves) is recorded in the per-port owner sidecar — the identity setup later trusts before
    it re-adopts the port or restarts the server.

    Race-safe (2026-09 #42/#68): check-listening -> spawn -> wait runs under a per-PORT start lock, so
    concurrent heals/setups spawn ONE server (the losers find it listening); and the pid file + owner
    sidecar are written only for a child that is ALIVE once the port is up — a child that died (lost the
    bind) is never recorded. `wait` defaults to MCP_START_WAIT_S (env DEEPMAP_MCP_START_WAIT); a child
    still starting when it expires is ours (we hold the lock), so it is recorded and reported.

    Stale config (#69): a server that is provably THIS project's but was started with another Neo4j
    config (URI / user / password — e.g. a wrong password, or before the project's instance moved) is
    restarted with the current one, instead of being kept as "already listening" while every call fails.
    The URI is compared by endpoint (ROUND-8 J4): bolt://localhost:<p> and bolt://127.0.0.1:<p> are one config.

    Stale CODE (ROUND-8 J5, heal / mcp-start): a server that is provably THIS project's but runs OLDER DeepMap code
    than this provisioner (its owner sidecar's code_root / code_version, or — a sidecar older than those fields —
    the script path its command line runs) is restarted on the current code: a plugin update used to keep the old
    version serving until a setup or a reboot. ROUND-9 K3: a server a v0.27.4 build started (no owner sidecar at all)
    qualifies on evidence — this project's registered port + its pid file + its command line (_preowner_server_code_
    stale); so does one whose owner record names ANOTHER pid than the pid file (ROUND-10 L1: the record of an earlier
    process — the hook's reconnect note judges it the same way). ROUND-9 K4: never onto OLDER code
    (_code_restart_verdict) — an older Claude Code window's heal leaves a newer window's servers alone; ROUND-10 L4: nor
    for an EQUAL version from another plugin dir that still exists (two installed copies of one version no longer
    ping-pong); ROUND-10 L3: a server started before its own plugin dir was updated in place (plugin.json / its script
    rewritten since) is restarted too. ROUND-10 L2: a server whose owner record can't vouch for the current Neo4j config
    (no fingerprint — every v0.27.4 server) is replaced only when the current config connects (_bolt_state 'up'), else
    it keeps serving with a NOTE. The restart is recorded like a start (_MCP_STARTED -> heal's `mcp_started`, and
    _MCP_CODE_RESTARTED -> `mcp_code_restarted`). Setup restarts its own servers anyway (restart_own).
    """
    host, port = mcp_host(), mcp_port(name)
    wait = _env_seconds("DEEPMAP_MCP_START_WAIT", MCP_START_WAIT_S) if wait is None else wait
    lock, how = _file_lock(_mcp_pidfile(name, port).with_suffix(".lock"), wait_s=wait + 30)
    # ROUND-7 I4/H1 — setup (restart_own) only: a listener it did not just spawn must be PROVEN this project's with
    # the current config (_listener_proven_own), else it is a blocker (_MCP_BLOCKED) — never "already listening"
    proof = _REQUIRE_LISTENER_PROOF and target is not None
    try:
        if how == "timeout":
            if is_http_up(host, port):
                if proof and not _listener_proven_own(name, port, target, password):
                    _MCP_BLOCKED[name] = _unattributed_listener(name, host, port, target)
                    return False
                print(f"[provision] {name} already listening on {host}:{port} (started by a concurrent "
                      "heal/setup)")
                return True
            print(f"[provision] ERROR: another DeepMap process is still starting {name} on {host}:{port}; "
                  "retry shortly", file=sys.stderr)
            return False
        if is_http_up(host, port):
            foreign = _mcp_foreign_owner(name, port, target)
            if foreign:
                if proof:
                    _MCP_BLOCKED[name] = _unattributed_listener(name, host, port, target)
                    return False
                print(f"[provision] WARNING: {host}:{port} is held by project {foreign}'s {name} server, "
                      "not this project's — NOT using it (re-run /deep-map:setup to give this project its "
                      "own ports)", file=sys.stderr)
                return False
            config = _server_config(password=password)
            if not _own_server_config_stale(name, port, target, config):
                # ROUND-8 J5 (heal / mcp-start — setup's restart_own restarted its own servers already): this project's
                # own server started from OTHER DeepMap code (a plugin update) is restarted on the current code
                old_code = None if proof else _own_server_code_stale(name, port, target)
                if old_code is not None:
                    # ROUND-10 L2: only a config that WORKS replaces a running one. A server whose owner record does
                    # not vouch for the current config (no record at all — every v0.27.4 server —, or one without a
                    # config fingerprint) may be serving fine with credentials `mcp-start` never proved (heal did, but
                    # mcp-start from a terminal without NEO4J_PASSWORD did not): probe the current config first, as
                    # the stale-config branch below does, and leave the old server serving when it can't connect.
                    if not _config_vouched(name, port, config):
                        state = _bolt_state(*config)
                        if state != "up":
                            print(f"[provision] NOTE: {name} on {host}:{port} runs DeepMap {old_code} — superseded by "
                                  f"the code running now, but the current Neo4j configuration can't connect ({state}), "
                                  "so the running server was left serving as is (the next heal or /deep-map:setup "
                                  "with the right credentials restarts it)")
                            return True
                    return _restart_for_code(name, host, port, old_code, password=password, wait=wait, target=target)
                if proof and not _listener_proven_own(name, port, target, password):
                    _MCP_BLOCKED[name] = _unattributed_listener(name, host, port, target)
                    return False
                print(f"[provision] {name} already listening on {host}:{port}")
                return True
            # Only a config that WORKS replaces a running one: `mcp-start` (unlike heal) never proved the
            # current credentials, and a wrong password must not take down a server that is serving.
            state = _bolt_state(*config)
            if state != "up":
                if proof:   # setup never reports success over a server wired to another configuration
                    _MCP_BLOCKED[name] = _unattributed_listener(name, host, port, target)
                    return False
                print(f"[provision] NOTE: {name} on {host}:{port} was started with a different Neo4j "
                      f"configuration, but the current one can't connect ({state}) — left as is")
                return True
            print(f"[provision] {name}: this project's server on {host}:{port} was started with a different "
                  "Neo4j configuration (URI or credentials changed since) — restarting it with the current one")
            stop_mcp_server(name, target=target)
            deadline = time.time() + 15
            while time.time() < deadline and is_http_up(host, port):
                time.sleep(0.25)
            if is_http_up(host, port):
                print(f"[provision] ERROR: {name} on {host}:{port} still runs its OLD configuration (it could "
                      f"not be stopped) — run {_provision_cmd('mcp-stop')} from this project's folder, then "
                      "/deep-map:setup", file=sys.stderr)
                return False
        return _spawn_mcp_server(name, host, port, password=password, wait=wait, target=target)
    finally:
        _file_unlock(lock)


def _restart_for_code(name: str, host: str, port: int, old_code: str, *, password, wait: int, target) -> bool:
    """ROUND-8 J5 (called under the per-port start lock): stop THIS project's own server that runs older DeepMap code
    (`old_code` — _own_server_code_stale proved it ours and _code_restart_verdict said to restart: never a downgrade)
    and start it on the current code. The respawn lands in _MCP_STARTED (heal's `mcp_started`: a connected Claude
    Code session must reconnect via /mcp) and in _MCP_CODE_RESTARTED (heal's `mcp_code_restarted`: why). A stop that
    fails leaves the old server serving this project's graph — it still works, so True with a WARNING, never a false
    "down"."""
    cur = _code_identity()
    now = " from ".join(str(x) for x in (cur.get("code_version"), cur.get("code_root")) if x) or "this build"
    print(f"[provision] {name}: this project's server on {host}:{port} runs DeepMap {old_code} — superseded by the code "
          f"running now ({now}); restarting it on the current code (a connected Claude Code session reconnects via "
          "/mcp)")
    stopped = stop_mcp_server(name, target=target)
    if not stopped or not _wait_port_closed(host, port, _RESTART_WAIT_S):
        if is_http_up(host, port):
            print(f"[provision] WARNING: {name} on {host}:{port} could not be restarted — it keeps serving this project "
                  f"with the OLD code ({old_code}). Run {_provision_cmd('mcp-stop')} from this project's folder, then "
                  "/deep-map:setup.", file=sys.stderr)
            return True
    ok = _spawn_mcp_server(name, host, port, password=password, wait=wait, target=target)
    if ok and name in _MCP_STARTED:
        _MCP_CODE_RESTARTED[name] = str(old_code)
    return ok


def _spawn_mcp_server(name: str, host: str, port: int, *, password, wait: int, target) -> bool:
    """start_mcp_server's spawn-and-wait half (called under the per-port start lock)."""
    cfg = MCP_SERVERS[name]
    script = HERE / cfg["script"]
    env = dict(os.environ)
    env.update({
        "DEEPMAP_MCP_TRANSPORT": "streamable-http",
        "DEEPMAP_MCP_HOST": host,
        cfg["port_env"]: str(port),
        "NEO4J_URI": env.get("NEO4J_URI", DEFAULT_URI),
        "NEO4J_USER": env.get("NEO4J_USER", DEFAULT_USER),
        "NEO4J_PASSWORD": password or env.get("NEO4J_PASSWORD", DEFAULT_PASSWORD),
        "PYTHONUTF8": "1",
        "HF_HUB_OFFLINE": env.get("HF_HUB_OFFLINE", "1"),
    })
    if target is not None:
        # D3/E5: the project this server indexes. DEEPMAP_PROJECT_ROOT is the SYNC root — where the
        # project's .deepmap/ state lives — because RM-1 logging, the learning flag and learned fusion
        # (retrieval_log.default_root) use it verbatim, and the CLI / access hook / tuner use that same root
        # (retrieval_log.project_root); the git toplevel split a sub-project synced on its own into two
        # logs (2026-09 review). DEEPMAP_PROJECT_KEY is the identity key; the search server's project guard
        # treats nested paths as one project. (The server itself runs with cwd = the plugin dir.)
        try:
            import port_registry as PR
            env["DEEPMAP_PROJECT_ROOT"] = _server_project_root(target)
            env["DEEPMAP_PROJECT_KEY"] = PR.project_key(target)
        except Exception:
            pass
    rundir = deepmap_home() / "run"
    rundir.mkdir(parents=True, exist_ok=True)
    logf = open(deepmap_home() / f"{name}-{port}.log", "ab")
    # close the parent's fd even if Popen raises (child keeps its own dup'd fd). This runs from the
    # long-lived mcp-start path, so a leak-on-failure would accumulate fds — hence try/finally.
    try:
        if is_windows():
            proc = subprocess.Popen([sys.executable, str(script)],
                                    stdout=logf, stderr=logf, stdin=subprocess.DEVNULL,
                                    creationflags=WINDOWS_BACKGROUND_FLAGS, close_fds=True,
                                    env=env, cwd=str(HERE.parent))
        else:
            proc = subprocess.Popen([sys.executable, str(script)],
                                    stdout=logf, stderr=logf, stdin=subprocess.DEVNULL,
                                    start_new_session=True, close_fds=True,
                                    env=env, cwd=str(HERE.parent))
    finally:
        logf.close()
    # Poll the CHILD first, then the port: a child that already exited lost the bind (another process
    # holds the port) and must never be recorded as the server — the old port-first loop reported
    # "up (pid <dead loser>)" and wrote that dead pid, so mcp-stop later orphaned the real listener.
    deadline = time.time() + wait
    while time.time() < deadline:
        if proc.poll() is not None:
            if is_http_up(host, port):
                print(f"[provision] NOTE: {name} on {host}:{port} is served by ANOTHER process (ours exited, "
                      f"rc={proc.returncode}) — not recording it as this project's server", file=sys.stderr)
                return True
            print(f"[provision] ERROR: {name} exited early (rc={proc.returncode}); "
                  f"see {deepmap_home() / (name + f'-{port}.log')}", file=sys.stderr)
            return False
        if is_http_up(host, port):
            _record_mcp_server(name, port, proc.pid, target, env=env)
            _MCP_STARTED.append(name)
            print(f"[provision] {name} up on {host}:{port} (pid {proc.pid})")
            return True
        time.sleep(1)
    # Still starting (a slow, loaded machine): it IS ours (we hold the start lock) — record it so
    # mcp-status/mcp-stop can manage it instead of leaving an unmanaged orphan.
    _record_mcp_server(name, port, proc.pid, target, env=env)
    print(f"[provision] ERROR: {name} (pid {proc.pid}) did not listen within {wait}s — it may still be "
          "starting on a busy machine (check mcp-status in a minute; raise DEEPMAP_MCP_START_WAIT); log: "
          f"{deepmap_home() / (name + f'-{port}.log')}", file=sys.stderr)
    return False


def _server_project_root(target) -> str:
    """The SYNC root of `target` — the directory its .deepmap/ state lives in — for the servers'
    DEEPMAP_PROJECT_ROOT (E5). The rule of retrieval_log.project_root / sync._sync_root: inside a git work
    tree, the OUTERMOST directory from `target` up to the work tree's top that holds a sync manifest
    (.deepmap/graph_build.json); with none yet (mcp-start before any sync), `target` itself — the directory
    a sync of it will use. Outside git, `target`. DEEPMAP_HOME's own state dir is never a marker.
    Recomputed on every call (retrieval_log caches per process, and setup syncs in-process just before it
    starts the servers). Never raises."""
    try:
        t = Path(target).resolve()
    except Exception:
        return str(target)
    try:
        home_state = deepmap_home().resolve()
    except Exception:
        home_state = None
    best = top = None
    d = t
    try:
        for _ in range(256):
            try:
                marker = d / ".deepmap"
                if (marker / "graph_build.json").is_file() and (home_state is None
                                                                 or marker.resolve() != home_state):
                    best = d                          # keep walking: the OUTERMOST manifest wins
                if (d / ".git").exists():
                    top = d                           # the work tree's top bounds the walk
                    break
            except OSError:
                pass
            if d.parent == d:
                break
            d = d.parent
    except Exception:
        best = top = None
    return str((best or t) if top is not None else t)


def start_mcp_servers(*, password: str | None = None, restart_own: bool = False, target=None) -> bool:
    """Start both MCP servers (idempotent). `target` (the project) is recorded in each started server's
    owner sidecar. `restart_own=True` (setup ONLY — the deliberate configure step) first restarts THIS
    project's own already-running servers so they pick up the current config (needs `target`); the
    default never disrupts a running server (heal runs on every SessionStart, mcp-start is a
    start/verify). BOTH servers are always attempted — one slow/failed start never leaves the other
    stopped (the old all(<generator>) short-circuit did, right after restart_own had stopped it — #109).
    ROUND-6 H1: a server the restart could NOT restart while it still holds this project's port (_MCP_BLOCKED,
    see _restart_own_mcp_server) is a FAILURE for that server — never "already listening".
    ROUND-7 I4/H1: with restart_own, a listener the start then finds on this project's port that setup did not just
    spawn must be provably this project's server with the CURRENT config (_listener_proven_own) — a stale pid file
    or none (an unmanaged / pre-upgrade server still wired to another graph), another project's server, or a
    stale config is a blocker too (start_mcp_server records it in _MCP_BLOCKED)."""
    global _REQUIRE_LISTENER_PROOF
    _MCP_BLOCKED.clear()
    proof = bool(restart_own and target is not None)
    if proof:
        for n in MCP_SERVERS:
            why = _restart_own_mcp_server(n, target, password=password)
            if why:
                _MCP_BLOCKED[n] = why
    prev, _REQUIRE_LISTENER_PROOF = _REQUIRE_LISTENER_PROOF, proof
    try:
        results = [False if n in _MCP_BLOCKED else start_mcp_server(n, password=password, target=target)
                   for n in MCP_SERVERS]
    finally:
        _REQUIRE_LISTENER_PROOF = prev
    return all(results)


def _warn_unmanaged_listener(name: str) -> None:
    """After a stop found nothing of ours to signal: say LOUDLY if the port is still held (an unmanaged
    server — e.g. one a pre-lock concurrent start orphaned), instead of silently reporting success."""
    host, port = mcp_host(), mcp_port(name)
    if is_http_up(host, port):
        print(f"[provision] WARNING: {name}: {host}:{port} is still LISTENING, but not by a process this "
              "project manages — an unmanaged server (or another program). Find its PID "
              f"({'netstat -ano | findstr :' if is_windows() else 'lsof -iTCP -sTCP:LISTEN -P | grep :'}{port}) "
              "and stop it by hand if it is a stale DeepMap server.", file=sys.stderr)


def stop_mcp_server(name: str, target=None) -> bool:
    """Stop one managed MCP server via its pid file.

    Both "nothing to stop" states return True: `stop_mcp_servers` is `all(...)`, so a stale or
    absent pid file (normal after a crash/reboot — the kernel never clears the file) must not fail
    the aggregate `mcp-stop` exit code and abort a user's `mcp-stop && mcp-start` chain (2026-07
    field report: the stale-pidfile wedge). Only a live pid that a kill FAILS on returns False.

    The pidfile is keyed by THIS project's allocated port (`mcp_port` reads the env the dispatch
    injected via apply_ports_for). With `target`, a server whose owner sidecar names ANOTHER project is
    never stopped (a worktree / copied checkout inherits the original's ports from its committed
    .mcp.json — 2026-09 #44); a pid whose command line is not the server script, or whose CREATION TIME
    is not the recorded one (D2 — recycled), is never signalled, and one whose creation time can't be
    read is left alone (returns False: not stopped); a port still held after "nothing to stop" is warned
    about, not silently ignored. The server is stopped with its process TREE (a Windows venv python.exe
    is a launcher whose child is the real server — a single-pid kill orphaned it on the port)."""
    pf = _mcp_pidfile(name, mcp_port(name))
    of = _mcp_ownerfile(name, mcp_port(name))   # the owner sidecar lives and dies with the pid file
    foreign = _mcp_foreign_owner(name, mcp_port(name), target)
    if foreign:
        print(f"[provision] {name}: port {mcp_port(name)} is held by project {foreign}'s server — NOT "
              "stopped (it is not this project's)")
        return True
    if not pf.exists():
        print(f"[provision] {name}: no pid file — not managed/running (nothing to stop)")
        _warn_unmanaged_listener(name)
        return True
    try:
        pid = int(pf.read_text().strip())
    except Exception:
        # An unreadable pid file means we cannot know what to signal — clear it (the server, if
        # any, is unmanaged from here) and treat as nothing-to-stop.
        print(f"[provision] {name}: unreadable pid file — clearing it (nothing to stop)")
        pf.unlink(missing_ok=True)
        of.unlink(missing_ok=True)
        return True
    # Stale/recycled pid file -> don't signal a dead/unrelated PID; just clear the file (#3).
    if not _pid_alive(pid):
        print(f"[provision] {name}: recorded pid {pid} not running (stale pid file) — nothing to stop")
        pf.unlink(missing_ok=True)
        of.unlink(missing_ok=True)
        _warn_unmanaged_listener(name)
        return True
    cmd = _pid_cmdline(pid)
    owner = _mcp_owner_record(name, mcp_port(name))
    if cmd is not None and MCP_SERVERS[name]["script"] not in cmd:
        print(f"[provision] {name}: recorded pid {pid} is no longer this server's process (the pid was "
              "recycled by another program) — NOT signalling it; clearing the stale pid file")
        pf.unlink(missing_ok=True)
        of.unlink(missing_ok=True)
        _warn_unmanaged_listener(name)
        return True
    if target is not None and owner is None and not _mcp_port_registered_to(target, mcp_port(name)):
        # A server with no owner record (a pre-upgrade build's) on a port the registry does NOT give this
        # project — the pair came from a committed/copied .mcp.json, or the registry was lost: it may be
        # another project's server. Only a record naming this project, or its own registered pair, proves
        # "ours" (2026-09 review: mcp-stop via the .mcp.json fallback killed another project's servers).
        print(f"[provision] {name}: the server on port {mcp_port(name)} (pid {pid}) has no DeepMap owner record "
              "and that port is not registered to this project, so it can't be shown to be this project's — "
              "NOT stopped. If it is, stop it by hand; /deep-map:setup gives this project ports of its own.")
        return True
    created = _pid_create_time(pid)
    same, basis = _pid_check(pid, recorded=(owner or {}).get("created"), pidfile=pf, created=created)
    if same is False and basis == "stamp":
        # ROUND-4: two COMPARABLE start stamps differ — the pid now belongs to another process (e.g. another
        # project's server after a reboot). The pid file + owner record are stale: clear them, never signal.
        print(f"[provision] {name}: recorded pid {pid} now belongs to another process (its start time is not the "
              "one recorded for this port) — NOT signalling it; clearing the stale pid file (nothing to stop)")
        pf.unlink(missing_ok=True)
        of.unlink(missing_ok=True)
        _warn_unmanaged_listener(name)
        return True
    if basis != "stamp":
        elsewhere = _mcp_pid_owned_elsewhere(pid, name, mcp_port(name), pf, target=target, created=created)
        if elsewhere:
            print(f"[provision] {name}: recorded pid {pid} is {elsewhere}, not this port's — NOT signalling it; "
                  "clearing the stale pid file (nothing to stop)")
            pf.unlink(missing_ok=True)
            of.unlink(missing_ok=True)
            _warn_unmanaged_listener(name)
            return True
    if same is False:
        # no comparable record, and the process started AFTER the pid file was written: suggestive, not proof —
        # never signal it, never delete the pid file / owner record on that alone: report and leave.
        print(f"[provision] {name}: pid {pid} runs a DeepMap MCP server, but it started after this port's pid "
              f"file was written, so it can't be shown to be this server — NOT signalling it. If it is this "
              f"project's server, stop it by hand; if not, delete {pf} instead.", file=sys.stderr)
        return False
    if basis == "unproven":
        # Linux, no comparable record, a clock-step-ambiguous start: the READABLE command line decides (E2)
        if not (cmd and MCP_SERVERS[name]["script"] in cmd):
            print(f"[provision] {name}: could not verify that pid {pid} is still this server's process (no start "
                  "record and no readable command line) — NOT signalling it; stop it by hand if it is",
                  file=sys.stderr)
            return False
        same = True
    if same is None:
        print(f"[provision] {name}: could not verify that pid {pid} is still this server's process (its "
              "start time is unreadable) — NOT signalling it; stop it by hand if it is", file=sys.stderr)
        return False
    ok = _terminate_pid(pid, tree=True)   # the tree: a venv launcher's child is the real server
    if ok:
        pf.unlink(missing_ok=True)
        of.unlink(missing_ok=True)
        print(f"[provision] {name}: stopped (pid {pid})")
    else:
        print(f"[provision] {name}: stop FAILED for pid {pid} (taskkill/kill returned non-zero)",
              file=sys.stderr)
    return ok


def stop_mcp_servers(target=None) -> bool:
    return all([stop_mcp_server(n, target=target) for n in MCP_SERVERS])


def _neo4j_endpoint(uri):
    """(host, port) a Neo4j URI connects to — loopback spellings folded into one ('localhost', '127.0.0.1',
    '::1', '0:0:0:0:0:0:0:1' — _is_loopback), the scheme ignored (bolt / neo4j / +s name the same server), the port
    defaulting to 7687 — or None when it can't be parsed. Never raises."""
    try:
        from urllib.parse import urlparse
        u = urlparse(str(uri or "").strip())
        host = (u.hostname or "").lower()
        if not host:
            return None
        if _is_loopback(host):
            host = "localhost"
        return host, (u.port or 7687)
    except Exception:
        return None


def _stop_own_servers_serving(target, uri):
    """ROUND-5 G2 (heal side): after heal REFUSED the Neo4j at `uri` on policy (another project's store behind
    a mis-adopted base claim, or a legacy graph that is not this project's), stop THIS project's own MCP servers
    that were started against that very store — a v0.27.4 heal left them serving the other graph, and the
    SessionStart hooks' port probe then read the listening ports as "healthy". Owner-verified ONLY: the port's
    owner sidecar records `uri` (_neo4j_endpoint-equal) AND _own_mcp_server_pid proves the live process is the
    one it recorded for this project (or one of its migrated keys) — server script + creation time. Never a
    server of another project, one pointing at another Neo4j, one with no sidecar, or a recycled pid.
    Returns (stopped server names, unverified ports): the latter are this project's MCP ports that still listen
    for a DeepMap server heal could NOT verify — its pid file has no owner sidecar (an older build started it)
    or its sidecar names this project + `uri` but the process can't be shown to be the recorded one — so it
    may still answer from the refused graph (reported, never signalled). Probes a port only when a pid file
    exists for it. Never raises."""
    stopped, unverified = [], []
    want = _neo4j_endpoint(uri)
    if want is None:
        return stopped, unverified
    try:
        host, own = mcp_host(), set(_own_keys(target))
    except Exception:
        return stopped, unverified
    for name in MCP_SERVERS:
        try:
            port = mcp_port(name)
            pf = _mcp_pidfile(name, port)
            if not pf.exists():
                continue   # nothing DeepMap started on this port -> nothing heal can reason about
            rec = _mcp_owner_record(name, port)
            ours_there = bool(rec) and rec.get("project_key") in own and _neo4j_endpoint(rec.get("neo4j_uri")) == want
            if ours_there:
                pid = _own_mcp_server_pid(name, port, target)
                if pid is not None and _terminate_pid(pid, tree=True):
                    pf.unlink(missing_ok=True)
                    _mcp_ownerfile(name, port).unlink(missing_ok=True)
                    stopped.append(name)
                    print(f"[provision] heal: {name}: stopped this project's server (pid {pid}, port {port}) — it "
                          f"was serving the refused Neo4j at {uri}", file=sys.stderr)
                    continue
            if (rec is None or ours_there) and is_http_up(host, port):
                unverified.append(int(port))
        except Exception:
            continue
    return stopped, unverified


_REFUSAL_KINDS = ("foreign-base-claim", "legacy-owner")
# ROUND-7 I4: setup's MCP blocker — a listener on this project's port setup could not restart / attribute (H1). NOT a
# refusal kind (the hooks' _REFUSAL_KINDS drift lock): its own kind, persisted in <home>/run/setup-blocked-<key>.json
# (port_registry's write/read/clear_setup_blocker — W1's contract, mirrored here when that API is absent) and in
# heal's status (refused: true + refusal_kind "mcp-blocked") until the held listener is gone or setup succeeds.
_BLOCKED_KIND = "mcp-blocked"


def _setup_blocker_path(target) -> Path:
    """<DEEPMAP_HOME>/run/setup-blocked-<project_key>.json (port_registry.setup_blocker_path when it exists)."""
    import port_registry as PR
    fn = getattr(PR, "setup_blocker_path", None)
    if callable(fn):
        try:
            return Path(fn(target))
        except Exception:
            pass
    return deepmap_home() / "run" / f"setup-blocked-{PR.project_key(target)}.json"


def _write_setup_blocker(target, reasons: dict) -> None:
    """Persist setup's MCP blocker (`reasons`: server name -> one-line reason) so heal and the SessionStart hooks
    treat the held port as unusable after this process exits: port_registry.write_setup_blocker, else the same
    record written here (atomic, UTF-8, LF). Best-effort; never raises."""
    try:
        import port_registry as PR
        servers = [n for n in MCP_SERVERS if n in reasons] or list(reasons)
        ports = [int(mcp_port(n)) for n in servers if n in MCP_SERVERS]
        pids = [p for p in (_pidfile_pid(_mcp_pidfile(n, mcp_port(n))) for n in servers if n in MCP_SERVERS) if p]
        reason = ("; ".join(str(reasons[n]) for n in servers)
                  + " — stop that process by hand, then re-run /deep-map:setup")
        fn = getattr(PR, "write_setup_blocker", None)
        if callable(fn):
            try:
                if fn(target, reason=reason, servers=servers, ports=ports, pids=pids) is True:
                    return
            except Exception:
                pass
        path = _setup_blocker_path(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        now = time.time()
        rec = {"kind": _BLOCKED_KIND, "reason": reason, "servers": servers, "ports": ports, "pids": pids,
               "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)), "ts_epoch": now,
               "project_key": PR.project_key(target), "root": PR.project_identity(target).replace("\\", "/")}
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".blocker-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                json.dump(rec, f)
            os.replace(tmp, path)
            tmp = None
        finally:
            if tmp is not None:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
    except Exception:
        pass


def _read_setup_blocker(target) -> dict | None:
    """This project's persisted setup blocker, or None. A file that exists but can't be read still counts (never
    steer toward a held port on a doubt). Never raises."""
    try:
        import port_registry as PR
        fn = getattr(PR, "read_setup_blocker", None)
        if callable(fn):
            try:
                rec = fn(target)
                return rec if isinstance(rec, dict) else None
            except Exception:
                pass
        path = _setup_blocker_path(target)
        if not path.exists():
            return None
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(rec, dict):
                return rec
        except Exception:
            pass
        return {"kind": _BLOCKED_KIND, "reason": f"an unreadable setup blocker ({path}) — run /deep-map:setup",
                "servers": list(MCP_SERVERS)}
    except Exception:
        return None


def _clear_setup_blocker(target) -> None:
    """Remove this project's setup blocker (setup started its own servers; or the held listener is gone). Never
    raises."""
    try:
        import port_registry as PR
        fn = getattr(PR, "clear_setup_blocker", None)
        if callable(fn):
            try:
                fn(target)
            except Exception:
                pass
        _setup_blocker_path(target).unlink(missing_ok=True)
    except Exception:
        pass


def _setup_blocker_still_holds(target, held_before: dict, password: str | None = None):
    """heal / mcp-start, AFTER their start: (blocker record, [server names it still blocks]) — or (None, []) when
    there is no blocker or it was lifted (and cleared): a blocked server whose port was FREE before this start (the
    held listener is gone — the start ran a fresh one with the current config), or whose listener is now provably
    this project's with the current config (_listener_proven_own). Never raises."""
    try:
        blk = _read_setup_blocker(target)
        if not blk:
            return None, []
        names = [n for n in (blk.get("servers") or ()) if n in MCP_SERVERS] or list(MCP_SERVERS)
        remaining = [n for n in names if held_before.get(n)
                     and not _listener_proven_own(n, mcp_port(n), target, password)]
        if not remaining:
            _clear_setup_blocker(target)
            return None, []
        return blk, remaining
    except Exception:
        return None, []


def _refuse_heal(target, report: dict, kind: str, reason: str, uri) -> str:
    """ROUND-5 G2: record a POLICY refusal in heal's report — `refused` + `refusal_kind` (one of
    _REFUSAL_KINDS; the SessionStart hooks never let a port probe "supersede" it: a listening port can't tell
    "up" from "not yours") — and stop this project's own servers that serve the refused store
    (_stop_own_servers_serving). The persisted reason is the refusal line plus what heal did about the servers.
    Returns the suffix for heal's printed line. Never raises."""
    report.update({"neo4j_ok": False, "mcp_ok": False, "refused": True,
                   "refusal_kind": kind if kind in _REFUSAL_KINDS else None})
    try:
        stopped, unverified = _stop_own_servers_serving(target, uri)
    except Exception:
        stopped, unverified = [], []
    report["mcp_stopped"], report["mcp_unverified"] = stopped, unverified
    tail = ""
    if stopped:
        tail += f"; heal stopped this project's MCP servers that were serving it ({', '.join(stopped)})"
    if unverified:
        ports = "/".join(str(p) for p in unverified)
        tail += (f"; this project's MCP port(s) {ports} still answer from a DeepMap server heal could not verify as "
                 "its own (e.g. one an older DeepMap started), so deepmap_search / deepmap_impact may return that "
                 "graph — /deep-map:setup restarts this project's servers on its registered ports (or says how to "
                 "stop one it cannot)")
        print(f"[provision] heal: NOTE — port(s) {ports} still answer from a DeepMap MCP server heal could not verify "
              "as this project's (no matching owner record); it may serve the refused graph. If it is this "
              f"project's: {_provision_cmd('mcp-stop')} from this project's folder, then /deep-map:setup.",
              file=sys.stderr)
    report["reason"] = f"{reason}{tail}"
    return ("; stopped this project's MCP servers that were serving it (" + ", ".join(stopped) + ")") if stopped else ""


_COLD_START = ("a cold start: the first tool call loads the embedding model, which can take minutes on a busy "
               "machine — the server is up and may still be warming")


def _default_verify_deadline() -> int:
    """MCP_VERIFY_DEADLINE_S, raised to the servers' own warm-up budget + one tool call + slack when those
    are configured higher (mcp_bounds is the servers' single source of truth). Never raises."""
    try:
        import mcp_bounds as MB
        need = float(MB.warmup_timeout_s()) + float(MB.tool_timeout_s()) + 30.0
    except Exception:
        need = float(MCP_VERIFY_DEADLINE_S)
    return int(max(MCP_VERIFY_DEADLINE_S, need))


_VERIFY_BLOCKED: dict = {}    # server name -> why its last verify_mcp(target=) found it serving another / no project

# The MCP `initialize` answer carries the server's `instructions`; since round 3 (E4) every DeepMap server started
# with DEEPMAP_PROJECT_ROOT/KEY names the project it indexes there (mcp_bounds.project_instructions). Run in a child
# process with a hard timeout (the verify smoke test's pattern) so a wedged server can never hang the provisioner.
_LABEL_PROBE_SRC = r"""
import asyncio, json, sys
async def _main(url):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
    async with streamablehttp_client(url) as (read, write, _sid):
        async with ClientSession(read, write) as session:
            res = await session.initialize()
            info = getattr(res, "serverInfo", None)
            print(json.dumps({"instructions": getattr(res, "instructions", None),
                              "server": getattr(info, "name", None)}))
asyncio.run(asyncio.wait_for(_main(sys.argv[1]), timeout=float(sys.argv[2])))
"""


def _probe_mcp_instructions(url, timeout=None):
    """(known, instructions) of the MCP server at `url`: known=True with its `instructions` text (None when it
    announces none) once `initialize` answered; (False, None) when the probe could not tell (no mcp client, not
    an MCP server, a timeout). Never raises."""
    t = float(timeout or _env_seconds("DEEPMAP_MCP_LABEL_TIMEOUT", 45))
    try:
        out = subprocess.run([sys.executable, "-c", _LABEL_PROBE_SRC, str(url), str(t)], capture_output=True,
                             text=True, encoding="utf-8", errors="replace", timeout=t + 15,
                             env=dict(os.environ, PYTHONUTF8="1"))
    except Exception:
        return False, None
    if getattr(out, "returncode", 1) != 0:
        return False, None
    for ln in reversed((out.stdout or "").splitlines()):
        ln = ln.strip()
        if not ln.startswith("{"):
            continue
        try:
            d = json.loads(ln)
        except Exception:
            continue
        if isinstance(d, dict):
            ins = d.get("instructions")
            return True, (str(ins) if ins else None)
    return False, None


def _label_names_project(text: str, target) -> bool:
    """Does a server's E4 label name THIS project? The label is read with mcp_bounds.parse_project_instructions — the
    inverse of the very formatter the servers announce it with (mcp_bounds.project_instructions: '... project <name>
    (<root>) ...' or '... project with DeepMap key <key> ...'), so the two can't drift apart (ISS-build:024; this used
    to be substring checks + a private regex, which also took another project's label whose path CONTAINS
    '(<this root>)', or a key that merely starts with this project's key, for this project's). A root: this project's
    root as the server was given it (the sync root / the git toplevel / the target, in mcp_bounds.label_path's form),
    or — for any other spelling — a root the owner predicate says is this project. A key: exactly one of its keys. No
    DeepMap label at all -> False. Never raises."""
    try:
        import mcp_bounds as MB
        parsed = MB.parse_project_instructions(text)
        if not parsed:
            return False
        path, key = parsed
        if key:
            return key.lower() in {str(k).lower() for k in _own_keys(target)}
        fold = (lambda s: str(s).replace("\\", "/").rstrip("/").lower()) if is_windows() else \
            (lambda s: str(s).replace("\\", "/").rstrip("/"))
        cands = set()
        for get in (lambda: _server_project_root(target), lambda: _pr().project_identity(target),
                    lambda: str(Path(target).resolve())):
            try:
                c = get()
                if c:
                    cands.add(fold(MB.label_path(c)))
            except Exception:
                continue
        if fold(path) in cands:
            return True
        return _owner_is_this_project(target, path)
    except Exception:
        return False


def _pr():
    import port_registry as PR
    return PR


def _mcp_label_problem(name: str, target) -> str | None:
    """ROUND-7 I4/H1: one line when the server answering on this project's `name` port does NOT serve this project —
    it announces no project label (an older DeepMap build's server: every current one names its project) or names
    another project — else None (it names this project, or the probe could not tell: the smoke test decides).
    Never raises."""
    try:
        known, text = _probe_mcp_instructions(mcp_url(name))
        if not known:
            return None
        port = mcp_port(name)
        import mcp_bounds as MB
        parsed = MB.parse_project_instructions(text)
        if not parsed:
            # no label, or instructions that name no project in DeepMap's form (mcp_bounds.project_instructions)
            seen = " ".join(str(text or "").split())[:120]
            return (f"{name} on port {port} answers WITHOUT a project label"
                    + (f" (its MCP instructions: {seen!r})" if seen else "")
                    + " — current DeepMap servers name the project they index, so this is an older build's server (or "
                    "another program), which may answer from another graph than this project's")
        if _label_names_project(text, target):
            return None
        label = MB.project_label(*parsed) or str(text)[:160]
        return f"{name} on port {port} serves ANOTHER project ({label}), not this one"
    except Exception:
        return None


def _accepts_kw(fn, name: str) -> bool:
    """True iff calling `fn(..., name=...)` is valid (a named parameter or **kwargs) — so setup passes the verify
    target only to a verify_mcp that takes it (an older / stubbed one keeps working). Never raises."""
    try:
        import inspect
        params = inspect.signature(fn).parameters
        return name in params or any(p.kind is p.VAR_KEYWORD for p in params.values())
    except Exception:
        return False


def verify_mcp(name: str, *, deadline: int | None = None, target=None) -> bool:
    """Prove the MCP HTTP path end-to-end via the same client protocol Claude Code uses.

    Reuses tests/mcp_http_smoketest.py in a subprocess with a hard OS timeout, so a wedged
    server surfaces as a clean failure here rather than hanging the provisioner. `deadline` defaults to
    the servers' warm-up budget + one call (_default_verify_deadline; env DEEPMAP_MCP_VERIFY_DEADLINE) — it
    must cover a COLD first call (model load; measured 100s+ on a loaded box, #99). A failure says WHICH
    failure (timeout / the server still starting up / the embedding model unavailable / tool error / bad
    embedding size), recorded in _VERIFY_DETAIL for setup's final verdict.

    DEGRADED (a pass WITH a warning, W4): a search that answered keyword-only — its result ends with a
    {"note": "Keyword-only results: vector search is unavailable ..."} entry, which the smoke test prints as
    `NOTE: ...` — returns True (the server works) but is recorded in _VERIFY_DEGRADED and warned about, so
    setup's banner says "setup verdict: DEGRADED" instead of a silent success.

    ROUND-7 I4/H1 — with `target` (setup): the server's E4 PROJECT LABEL is checked first (_mcp_label_problem). A
    server that names no project (an older build's) or another project fails — recorded in _VERIFY_BLOCKED, which
    setup turns into a FAILED verdict + a persisted blocker, never a DEGRADED "the tools usually work once warm".
    """
    deadline = (_env_seconds("DEEPMAP_MCP_VERIFY_DEADLINE", _default_verify_deadline())
                if deadline is None else deadline)
    _VERIFY_DETAIL.pop(name, None)
    _VERIFY_DEGRADED.pop(name, None)
    _VERIFY_AUTH.pop(name, None)
    _VERIFY_BLOCKED.pop(name, None)
    if target is not None:
        problem = _mcp_label_problem(name, target)
        if problem:
            _VERIFY_BLOCKED[name] = problem
            _VERIFY_DETAIL[name] = problem
            print(f"[provision] ERROR: MCP {name} verify: {problem}.", file=sys.stderr)
            return False
    smoke = HERE.parent / "tests" / "mcp_http_smoketest.py"
    if not smoke.exists():
        print(f"[provision] (skip MCP verify for {name} — smoke test not found)")
        return True
    cfg = MCP_SERVERS[name]
    cmd = [sys.executable, str(smoke), "--url", mcp_url(name),
           "--tool", cfg["tool"], "--deadline", str(deadline)]
    try:
        # Decode the smoke-test child's output as UTF-8, not the OS locale codepage (cp125x on
        # Windows), so non-ASCII graph content can't crash the verify with UnicodeDecodeError
        # (mirrors _terminate_pid). errors="replace" keeps a stray byte non-fatal.
        out = subprocess.run(cmd, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=deadline + 30)
    except subprocess.TimeoutExpired:
        rc, stdout, stderr = 2, "", ""
    else:
        rc, stdout, stderr = out.returncode, out.stdout, out.stderr
    if stdout:
        print(stdout.strip())
    if rc != 0:
        if stderr:
            print(stderr.strip(), file=sys.stderr)
        # the smoke test's exit codes: 2 = deadline, 3 = wrong embedding size, else a tool/protocol error —
        # of which the server's own "still starting up" answer (its warm-up budget ran out) is a cold start
        detail = {2: f"timed out after {deadline}s ({_COLD_START})",
                  3: "returned an embedding of the wrong size"}.get(rc)
        both = f"{stdout}\n{stderr}".lower()
        if detail is None:
            if "refusing deepmap's logins" in both or "authenticationratelimit" in both:
                # the server's Neo4j AUTH answer (mcp_bounds.AuthBackoff) — a credentials problem, not a cold start
                detail = ("failed: Neo4j is temporarily refusing the server's logins (its login lock, after "
                          "repeated failed login attempts) — it clears on its own once the lock expires; if it "
                          "persists the server's credentials are wrong: run /deep-map:doctor")
                _VERIFY_AUTH[name] = "rate-limited"
            elif "rejected this deepmap server's credentials" in both or (
                    "neo.clienterror.security.unauthorized" in both):
                detail = ("failed: Neo4j rejected the server's credentials — an authentication problem, not a slow "
                          "start (the server was started with a password that Neo4j does not accept): run "
                          "/deep-map:doctor")
                _VERIFY_AUTH[name] = "rejected"
            elif "still starting up" in both:
                detail = f"got the server's 'still starting up' answer ({_COLD_START})"
            elif "embedding model is unavailable" in both:
                detail = ("failed: the embedding model is unavailable (deepmap_embed can't answer and "
                          "deepmap_search runs keyword-only) — run /deep-map:doctor")
            else:
                detail = "tool call failed (see the error above)"
        _VERIFY_DETAIL[name] = detail
        print(f"[provision] WARNING: MCP {name} verify {detail}", file=sys.stderr)
        return False
    notes = [ln.strip()[len("NOTE:"):].strip() for ln in (stdout or "").splitlines()
             if ln.strip().startswith("NOTE:")]
    kw = [n for n in notes if "keyword-only" in n.lower() or "vector search" in n.lower()]
    if kw:
        _VERIFY_DEGRADED[name] = ("answered KEYWORD-ONLY — vector search is unavailable (" + kw[0][:300] + "); "
                                  "if the embedding model is still loading (a cold start) this clears on its own, "
                                  "otherwise run /deep-map:doctor")
        print(f"[provision] WARNING: MCP {name} verify passed DEGRADED: {_VERIFY_DEGRADED[name]}", file=sys.stderr)
    return True


def mcp_status(target=None) -> int:
    print("DeepMap MCP servers (streamable-HTTP):")
    for name in MCP_SERVERS:
        host, port = mcp_host(), mcp_port(name)
        up = is_http_up(host, port)
        foreign = _mcp_foreign_owner(name, port, target) if up else None
        note = f"  (held by project {foreign}'s server, NOT this project's)" if foreign else ""
        print(f"  {name:16s} {mcp_url(name):34s} {'UP' if up else 'down'}{note}")
    try:
        import port_registry as PR
        n = PR.registry_stats().get("n_projects", 0)
        if n:
            print(f"  ({n} project{'s' if n != 1 else ''} in the port registry "
                  f"{PR.registry_path()}; servers/ports are per-project, and so is the Neo4j instance "
                  "unless NEO4J_URI points them all at one)")
    except Exception:
        pass
    return 0


# ---- the one-command setup --------------------------------------------------
def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_VERIFY_FALLBACK_QUERY = "fuse ranked search results"   # the historic fixed phrase (DeepMap's own vocabulary)


_VERIFY_IDENT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{2,}$")   # a plain identifier the BM25 analyzer keeps whole
_VERIFY_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{2,}")
_LUCENE_WORDS = frozenset({"AND", "OR", "NOT", "TO"})


def _fts_visible(q: str) -> bool:
    """Would the BM25 arm keep every word of `q`? retrieval._fts_terms drops a word of <= 2 characters or with no
    alphanumeric content — a query made only of such words searches nothing by keyword. Never raises."""
    try:
        words = str(q or "").split()
        return bool(words) and all(len(w) > 2 and any(ch.isalnum() for ch in w) for w in words)
    except Exception:
        return False


def _verify_query(drv) -> str:
    """T4d-I / ROUND-8 J6: the query setup's end-to-end search check uses — a code node's name from THIS graph, so the
    check asks the project's own graph about its own code rather than a phrase from DeepMap's vocabulary. It must be
    FTS-VISIBLE: an FTS-only setup (the embedding model unavailable — a supported DEGRADED mode) answers by keyword
    alone, and a 1-2 character name (a Go `T.Do` -> 'Do', `db`, `ok`) is dropped by the BM25 arm, so the check would
    report FAILED over a working graph. In order: a plain identifier (>= 3 chars) of a Function / Class / Method; else a
    dotted name as a WHOLE (`T.Do` — the analyzer keeps `Class.method` as one token, so its bare tail would miss); else
    a >= 3-char word of a stored summary (an indexed field); else the fixed phrase. Never raises."""
    try:
        with drv.session() as s:
            rows = list(s.run("MATCH (n) WHERE (n:Function OR n:Class OR n:Method) AND n.name IS NOT NULL "
                              "AND size(n.name) >= 3 RETURN n.name AS name LIMIT 200"))
        names = [" ".join(str(r["name"]).split()) for r in rows if r is not None and r["name"]]
        plain = next((n for n in names if _VERIFY_IDENT_RE.match(n) and n.upper() not in _LUCENE_WORDS), None)
        if plain:
            return plain
        whole = next((n for n in names if " " not in n and _fts_visible(n) and any(ch.isalpha() for ch in n)), None)
        if whole:
            return whole
        with drv.session() as s:
            rows = list(s.run("MATCH (n) WHERE (n:Function OR n:Class OR n:Method OR n:Module) "
                              "AND n.body_summary IS NOT NULL RETURN n.body_summary AS text LIMIT 50"))
        for r in rows:
            for w in _VERIFY_WORD_RE.findall(str((r["text"] if r is not None else "") or "")):
                if w.upper() not in _LUCENE_WORDS:
                    return w
    except Exception:
        pass
    return _VERIFY_FALLBACK_QUERY


def verify_search(uri: str, user: str, password: str, query: str | None = None) -> bool:
    """Run the exact call the MCP `deepmap_search` tool makes, to prove the path end-to-end. `query` defaults to one
    derived from this project's own graph (_verify_query — T4d-I); when that derived query finds nothing, the fixed
    phrase is tried once before the check reports "no results" (ROUND-8 J6). An explicit `query` is used as given."""
    embeddings = _load("embeddings")
    R = _load("retrieval")
    drv = _make_driver(uri, user, password)
    try:
        drv.verify_connectivity()
        queries = [query] if query is not None else list(dict.fromkeys([_verify_query(drv), _VERIFY_FALLBACK_QUERY]))
        ret = R.DeepMapRetriever(drv, embed_fn=embeddings.embed_query)
        results: list = []
        for q in queries:
            query = q
            results = ret.search(q, top_k=5)
            if results:
                break
            if q != queries[-1]:
                print(f"[provision] verify_search({q!r}) -> 0 results; retrying with {queries[-1]!r}")
    finally:
        drv.close()
    print(f"[provision] verify_search({query!r}) -> {len(results)} results"
          + (f"; top: {results[0]['name']} ({results[0]['type']})" if results else ""))
    return len(results) > 0


_CENSUS_PRUNE = frozenset({".git", ".hg", ".svn", ".deepmap", "node_modules", "bower_components", "__pycache__",
                           ".venv", "venv", "graphify-out"})
_CENSUS_DEADLINE_S = 15.0


def _source_census_problem(target) -> str | None:
    """ROUND-7 (T10d-W): setup's census BEFORE any port is allocated or Neo4j started — one line when `target`
    holds NO source file DeepMap can index (an empty or docs-only directory, a typo'd path), else None. An empty
    dir used to allocate ports, start a ~1 GB JVM and leave both behind before its sync said FAILED. The extension
    set is the verdict census's (every extension the extractor maps has one — the v0.27.4 drift lock) plus .py,
    matched case-insensitively; only directories DeepMap never indexes are skipped (VCS / its own state /
    dependency and virtualenv trees) — an engine-skipped dir like build/ still counts (the rescue may re-include
    it). Loaded through setup's own module seam (_load) — a caller that stubs it skips the census. Bounded: a walk
    that runs past its deadline is "unknown" (None — the sync decides). Never raises."""
    try:
        V = _load("verdict")
        exts = getattr(V, "_SOURCE_EXTS", None)
    except Exception:
        return None
    if not isinstance(exts, dict) or not exts:
        return None
    try:
        root = Path(target)
        if not root.exists():
            return f"the directory {root} does not exist"
        if not root.is_dir():
            return f"{root} is not a directory"
        want = {str(e).lower() for e in exts} | {".py"}
        deadline = time.monotonic() + _CENSUS_DEADLINE_S
        for dirpath, dirnames, filenames in os.walk(str(root)):
            dirnames[:] = [d for d in dirnames if d not in _CENSUS_PRUNE and not d.endswith(".egg-info")]
            for name in filenames:
                dot = name.rfind(".")
                if dot >= 0 and name[dot:].lower() in want:
                    return None
            if time.monotonic() > deadline:
                return None
        return (f"no source code DeepMap can index was found under {root.resolve()} (none of its {len(want)} "
                "supported file types, e.g. .py / .ts / .go / .java / .cs / .rb) — an empty or docs-only directory")
    except Exception:
        return None


def _census_verdict(target) -> str | None:
    """setup's verdict when `target` holds nothing to index (_source_census_problem), else None. Also the environment
    bootstrap's first check: nothing to index -> nothing is installed either."""
    census = _source_census_problem(target)
    if not census:
        return None
    return (f"setup verdict: FAILED — {census}; nothing was started (no ports were allocated, no Neo4j was started). "
            "Run /deep-map:setup in a project directory that holds source code.")


def _stop_started_instance(diag: dict, inst) -> bool:
    """setup: stop the Neo4j instance THIS setup started (ensure_neo4j's diag["started"]) after its sync built no
    usable graph — an idle ~1 GB JVM over an empty graph helps nobody; a store that was already running is left
    as found. True iff it stopped one. Never raises."""
    try:
        if not (diag or {}).get("started"):
            return False
        return bool(stop_neo4j(project_key=(inst[0] if inst else None)))
    except Exception:
        return False


# Round 11 (M1): a setup whose sync PUSHED NOTHING (sync.last_not_pushed — e.g. graphify ran out of time and the
# engine-downgrade guard kept the previous graphify graph) still brings the tools up over the graph it kept, but it did
# not build this project's graph: setup's banner says "setup verdict: DEGRADED — the graph was NOT rebuilt" and
# `provision.py setup` exits SETUP_GRAPH_KEPT_RC — the CLI's sync exit code for the same case (deepmap_cli
# SYNC_NOT_PUSHED_RC) — never a plain 0 that scripts, the installers and the benchmark would read as a fresh build.
SETUP_GRAPH_KEPT_RC = 4
_SETUP_GRAPH_KEPT: dict = {}   # {"why": ...} while the running setup's sync kept the previous graph


def _report_graph_kept() -> None:
    """setup's final word when its sync pushed nothing (no-op otherwise): a DEGRADED banner naming why."""
    why = _SETUP_GRAPH_KEPT.get("why")
    if not why:
        return
    print("[provision] " + "=" * 66)
    print(f"[provision] WARNING: this setup's sync pushed NOTHING — {why}.")
    print("[provision]          The MCP tools are up, but they answer from the PREVIOUS graph build, which may be")
    if "DEEPMAP_GRAPHIFY_TIMEOUT" in why:
        print("[provision]          stale. graphify ran out of time (it is not broken): run /deep-map:refresh with a")
        print("[provision]          larger DEEPMAP_GRAPHIFY_TIMEOUT (seconds), ideally when the machine is less busy.")
    else:
        print("[provision]          stale. Fix the cause the sync printed above, then run /deep-map:refresh.")
    print(f"[provision] setup verdict: DEGRADED — the graph was NOT rebuilt (exit {SETUP_GRAPH_KEPT_RC})")
    print("[provision] " + "=" * 66)


def setup(target: str = ".", *, uri: str | None = None, user: str | None = None,
          password: str | None = None, verify: bool = True) -> bool:
    """The one-command path: ensure Neo4j, sync the repo, verify search. True also when the sync kept the previous
    graph (the tools work over it) — _SETUP_GRAPH_KEPT then says why, and main() exits SETUP_GRAPH_KEPT_RC."""
    _SETUP_GRAPH_KEPT.clear()   # only THIS setup's sync decides it
    user = user or os.environ.get("NEO4J_USER", DEFAULT_USER)
    password = password or os.environ.get("NEO4J_PASSWORD", DEFAULT_PASSWORD)

    # ROUND-7 (T10d-W): nothing to index -> nothing is allocated or started (no registry entry, no JVM)
    census = _census_verdict(target)
    if census:
        print(f"[provision] {census}", file=sys.stderr)
        return False
    # no driver: ensure_neo4j would read every probe as "Neo4j is down", provision a JVM and time out on it
    absent = _missing_modules(("neo4j", "mcp"))
    if absent:
        print(f"[provision] setup verdict: FAILED — {_missing_packages_text(absent)}. Nothing was started.",
              file=sys.stderr)
        return False

    # Per-project resources (side-by-side projects): claim this project's free MCP (search, embed) pair
    # AND its Neo4j (bolt, http) pair, injecting DEEPMAP_*_PORT + NEO4J_URI into the env BEFORE anything
    # reads them — the first project on a clean machine keeps 8787/8788 + 7687/7474 (single-project
    # unchanged). INJECTION-FIRST: re-resolve `uri` from the env AFTER this so the project's injected
    # NEO4J_URI wins over any caller default (else setup would sync+verify the wrong project's graph).
    # An allocation that could not be RECORDED for this project (registry busy / full / unreadable) is
    # fatal: the fallback pair may be another project's (2026-09 #63 — setup "succeeded" wired to it).
    _, neo_problem, mcp_problem = _resolve_ports(target, allocate=True)
    if neo_problem or mcp_problem:
        for problem in (neo_problem, mcp_problem):
            if problem:
                print(f"[provision] ERROR: {problem}", file=sys.stderr)
        # escape-hatch mode allocates no Neo4j ports: its only Neo4j problem is the operator's NEO4J_URI itself
        what = ("the NEO4J_URI set for this run is not a Bolt URI DeepMap can use" if neo_problem and _USER_SET_NEO4J_URI
                else "this project's ports could not be allocated")
        print(f"[provision] setup verdict: FAILED — {what}; nothing was started.", file=sys.stderr)
        return False
    uri = os.environ.get("NEO4J_URI") or uri or DEFAULT_URI
    inst = resolved_instance(target)   # (project_key, bolt, http) or None (legacy/base/escape hatch)
    # F2: a persisted claim on the base store is re-verified — _release_foreign_base_claim (inside the port
    # resolution above) settles it when the store is live or carries an owner record; a store that is DOWN
    # with no record can only be read once it runs, so its owner is checked right after it is up.
    base_claim = (inst is None and not _USER_SET_NEO4J_URI and _is_local_base_uri(uri)
                  and neo4j_route_source() == "registry" and _is_base_claim(target))
    was_up = bool(base_claim) and _bolt_state(uri, user, password) == "up"
    if inst is not None or (not _USER_SET_NEO4J_URI and _is_local_base_uri(uri)):
        # a Neo4j upgrade a crash left open is finished or undone BEFORE the store is started (its journal)
        recovered = recover_neo4j_upgrade(target, inst=inst, uri=uri)
        if recovered:
            print(f"[provision] NOTE: {recovered}", file=sys.stderr)

    neo_diag: dict = {}   # {"started": True} when THIS setup launched the instance (T10d-W: a FAILED sync stops it)
    if not ensure_neo4j(uri=uri, user=user, password=password,
                        project_key=(inst[0] if inst else None),
                        bolt=(inst[1] if inst else None), http=(inst[2] if inst else None), diag=neo_diag):
        _neo4j_failed_verdict(neo_diag)
        return False
    other = _foreign_base_owner(target, user, password) if base_claim else None
    if other:
        moved = _move_off_foreign_base(target, other, stop_base=not was_up)
        if moved is None:
            return False
        uri, inst = moved
        neo_diag = {}
        if not ensure_neo4j(uri=uri, user=user, password=password,
                            project_key=(inst[0] if inst else None),
                            bolt=(inst[1] if inst else None), http=(inst[2] if inst else None), diag=neo_diag):
            _neo4j_failed_verdict(neo_diag)
            return False
    if not _setup_neo4j_upgrade(target, uri, user, password, inst):
        return False
    sync = _load("sync")
    if not sync.sync(target, cleanup_stale=True):
        stopped = _stop_started_instance(neo_diag, inst)
        print("[provision] sync failed" + ("; the Neo4j instance this setup started was stopped again" if stopped
                                           else ""), file=sys.stderr)
        return False
    if str(getattr(sync, "last_verdict", "") or "").upper() == "FAILED":
        # The sync's own honest verdict (0 code nodes pushed / Neo4j unreachable) — never start servers
        # over a graph this run could not build, even if an older build is still in the store.
        stopped = _stop_started_instance(neo_diag, inst)
        print("[provision] setup verdict: FAILED — the sync built no usable graph"
              f"{_build_failure_detail(target)}. See the sync verdict above; fix the cause and re-run "
              "/deep-map:setup (the MCP servers were not started"
              + ("; the Neo4j instance this setup started was stopped again" if stopped else "") + ").",
              file=sys.stderr)
        return False
    kept = getattr(sync, "last_not_pushed", None)
    if isinstance(kept, str) and kept.strip():
        # M1: the sync kept the previous graph (nothing pushed). It is intact, so the tools still come up over it,
        # but this setup did not build the graph — say so now and in the final verdict, and exit 4 (main).
        _SETUP_GRAPH_KEPT["why"] = kept.strip()
        print(f"[provision] WARNING: the sync pushed nothing ({kept.strip()}) — continuing over the PREVIOUS graph "
              "build; see the setup verdict at the end.", file=sys.stderr)
    if inst is None and not _USER_SET_NEO4J_URI and _is_local_base_uri(uri):
        # this project's graph now provably owns the base store (sync stamps / verifies its owner): record it
        # next to the store, so a later adoption while the store is DOWN can tell its owner (_base_adopt_ok)
        _record_base_owner(target)
    if verify:
        # A fail-loud retrieval/connectivity error inside verify_search must NOT abort an otherwise
        # successful setup AFTER Neo4j + sync already succeeded — catch it so the MCP servers still
        # start and the operator gets a clear warning instead of a traceback (bug-hunt #3).
        try:
            if not verify_search(uri, user, password):
                print("[provision] setup verdict: FAILED — search returned no results (an empty graph)"
                      f"{_build_failure_detail(target)}. See the sync output above for the language census "
                      "and the extractor's own reason.", file=sys.stderr)
                return False
        except Exception as exc:  # noqa: BLE001 — verification is best-effort; never abort here
            print(f"[provision] WARNING: verify_search raised ({type(exc).__name__}: {exc}); "
                  "Neo4j + sync succeeded — continuing to start the MCP servers.", file=sys.stderr)

    # Start the MCP servers (streamable-HTTP, detached) and prove the path Claude Code will use.
    # A GENUINE MCP startup failure is FATAL to the one-command setup contract (the deepmap tools are
    # the product) — fail LOUD so the exit code reflects it (bug-hunt: setup returned 0 on MCP failure).
    # start_mcp_server is idempotent (an already-listening port returns True), so a False here is a real
    # failure (a server exited early / never listened), not a benign "already running". restart_own: a
    # server of OURS already running (e.g. heal-revived with a now-stale NEO4J_URI) is restarted first.
    _MCP_BLOCKED.clear()   # only THIS start's verdicts (start_mcp_servers records them)
    if not start_mcp_servers(password=password, restart_own=True, target=target):
        if _MCP_BLOCKED:
            # ROUND-6/7 H1: a server setup could not restart — or a listener it can't attribute to this project's
            # current config — still holds this project's port; it may answer from another graph (e.g. a pre-upgrade
            # server still wired to a store setup just moved off), so the tools are NOT reported ready and no
            # "reload now" is printed; the blocker is PERSISTED so heal / the hooks don't call it healthy later
            _write_setup_blocker(target, dict(_MCP_BLOCKED))
            ports = ", ".join(str(mcp_port(n)) for n in MCP_SERVERS if n in _MCP_BLOCKED)
            print(f"[provision] setup verdict: FAILED — this project's MCP port(s) {ports} are still held by a DeepMap "
                  "server setup could not restart, or by a listener it can't attribute to this project's current "
                  "servers (see the ERROR above). Neo4j + sync SUCCEEDED; stop that process by hand, then re-run "
                  "/deep-map:setup.", file=sys.stderr)
            return False
        print("[provision] ERROR: an MCP server did not come up — Neo4j + sync SUCCEEDED, but the "
              "deepmap MCP tools are NOT available. See the ERROR above and the server logs in "
              f"{deepmap_home()} (common causes: the port is held by another program, or the server was "
              "still starting when the wait ran out on a busy machine — raise DEEPMAP_MCP_START_WAIT); "
              "fix it and re-run /deep-map:setup.", file=sys.stderr)
        return False
    _clear_setup_blocker(target)   # this project's own current servers now hold its ports
    verify_failed, verify_degraded = [], []
    if verify:
        # Verify BOTH servers (search AND embed) — an embed server that started but fails on its first
        # (model-loading) tool call would otherwise pass unnoticed until the user hits deepmap_embed.
        # verify_mcp prints WHY (timeout vs tool error); the outcome is folded into the final verdict — and
        # so is a search that only answered KEYWORD-ONLY (vector search unavailable): DEGRADED, not silent.
        # ROUND-7: the target lets it check each server's E4 project label (a server naming no project / another
        # project is a blocker, _VERIFY_BLOCKED — FAILED, never DEGRADED).
        _VERIFY_DEGRADED.clear()
        _VERIFY_BLOCKED.clear()
        with_target = _accepts_kw(verify_mcp, "target")
        for srv in ("deepmap-search", "deepmap-embed"):
            ok = verify_mcp(srv, target=target) if with_target else verify_mcp(srv)
            if not ok:
                verify_failed.append(srv)
            elif srv in _VERIFY_DEGRADED:
                verify_degraded.append(srv)
        if _VERIFY_BLOCKED:
            _write_setup_blocker(target, dict(_VERIFY_BLOCKED))
            ports = ", ".join(str(mcp_port(n)) for n in MCP_SERVERS if n in _VERIFY_BLOCKED)
            print(f"[provision] setup verdict: FAILED — this project's MCP port(s) {ports} answer from a server that "
                  "does not serve this project (see the ERROR above). Neo4j + sync SUCCEEDED; stop that process by "
                  "hand, then re-run /deep-map:setup.", file=sys.stderr)
            return False
    # Write THIS project's .mcp.json so its Claude Code connects to its own servers (the only wiring: the
    # plugin ships no .mcp.json). A semantic no-op when it already names these ports (no spurious git diff).
    # A refused/failed write means the tools can NEVER register — setup fails (no false "reload now").
    mcp_json = write_mcp_json(target, mcp_host(), mcp_port("deepmap-search"), mcp_port("deepmap-embed"))
    if mcp_json is False:
        print("[provision] ERROR: this project's .mcp.json could not be written (see the WARNING above) — "
              "the deepmap MCP tools will NOT register in Claude Code until it is fixed or deleted; then "
              "re-run /deep-map:setup.", file=sys.stderr)
        print("[provision] setup verdict: FAILED — .mcp.json not written (the deepmap tools are unavailable).",
              file=sys.stderr)
        return False
    if verify_failed and (not all(is_http_up(mcp_host(), mcp_port(s)) for s in verify_failed)
                          or any(_VERIFY_AUTH.get(s) == "rejected" for s in verify_failed)):
        # FAILED — never "reload now" for a dead server, nor for one Neo4j rejects (it can never answer)
        return _report_verify_failure(verify_failed, verify_degraded)
    _print_mcp_hint(target)
    _print_git_hook_hint(target)
    if verify_failed or verify_degraded:
        ok = _report_verify_failure(verify_failed, verify_degraded)
        if ok:
            _record_setup_python(target, mcp_json)
            _report_graph_kept()
        return ok
    _record_setup_python(target, mcp_json)
    _report_graph_kept()
    return True


def _setup_neo4j_upgrade(target, uri, user, password, inst) -> bool:
    """setup, once this project's Neo4j is up and the owner of a base claim checked: when the store runs an older patch
    release than DEFAULT_VERSION, move it there (upgrade_neo4j — verified, rolled back on failure); with
    DEEPMAP_NEO4J_AUTO_UPGRADE=0, or within _UPGRADE_RETRY_AFTER_S of a rolled-back upgrade to that same release (its
    record), only say that one is available. An upgrade that is skipped or rolled back never fails
    setup (the store keeps serving on its version); False only when the store's Neo4j is not usable afterwards (a
    FAILED verdict is printed). Never raises."""
    try:
        key = inst[0] if inst else None
        if _USER_SET_NEO4J_URI or (key is None and not _is_local_base_uri(uri)):
            return True
        avail = neo4j_upgrade_available(key)
        if not avail:
            return True
        if not _auto_upgrade_enabled() or _recent_upgrade_failure(key, *avail):
            # switched off, or an upgrade to this release was rolled back recently (its record): the note says which
            print(f"[provision] NOTE: {neo4j_upgrade_note(key, root=target)}", file=sys.stderr)
            return True
        rep: dict = {}
        out = upgrade_neo4j(target, uri=uri, user=user, password=password, inst=inst, report=rep)
        cur, tgt = avail
        if out == "skipped":
            print(f"[provision] NOTE: Neo4j stays on {cur} (the upgrade to {tgt} was not attempted: {rep.get('reason')})",
                  file=sys.stderr)
        elif out == "rolled-back":
            print(f"[provision] WARNING: the upgrade to Neo4j {tgt} was rolled back ({rep.get('reason')}); continuing on "
                  f"{cur}.", file=sys.stderr)
        elif out == "rollback-failed":
            print(f"[provision] ERROR: the upgrade to Neo4j {tgt} failed and could not be fully rolled back: "
                  f"{rep.get('reason')}", file=sys.stderr)
        if _bolt_state(uri, user, password) == "up":
            return True
        diag: dict = {}
        if ensure_neo4j(uri=uri, user=user, password=password, project_key=key, bolt=(inst[1] if inst else None),
                        http=(inst[2] if inst else None), create=False, diag=diag):
            return True
        _neo4j_failed_verdict(diag)
        return False
    except Exception:
        return True


def _record_setup_python(target, mcp_json=None) -> None:
    """ISS-build:013: remember the interpreter THIS setup ran under for `target`'s project — it just synced the graph
    and its MCP servers are up on it, so it provably runs DeepMap (the installers put its dependencies there) — in
    DeepMap's machine state, <DEEPMAP_HOME>/run/setup-python-<root id>.json keyed by the project's sync root
    (port_registry.write_setup_python; never a file in the project, whose contents a repository controls). After a
    reboot the SessionStart heal hook starts `provision.py heal` with it (deepmap_hook_state.heal_interpreter) instead
    of whichever of python / python3 ran the hook. Only a setup that ends with working servers AND this project's
    .mcp.json naming them (`mcp_json`: what write_mcp_json returned — the file) records: those are the servers heal
    revives. A FAILED setup never overwrites a good record. Best-effort; never raises."""
    try:
        if not mcp_json:
            return
        import port_registry as PR
        write = getattr(PR, "write_setup_python", None)
        if callable(write) and not write(_server_project_root(target)):
            print(f"[provision] NOTE: could not record this setup's Python ({sys.executable}) for the SessionStart "
                  "heal — after a reboot it runs under whichever python / python3 starts the hook (set DEEPMAP_PYTHON "
                  "to pin one).", file=sys.stderr)
    except Exception:
        pass


def _neo4j_failed_verdict(diag: dict) -> None:
    """setup's final word when this project's Neo4j could not be made usable (ensure_neo4j returned False and printed
    its ERROR): ONE FAILED verdict naming why (ISS-build:023 — such a failure used to end setup with a raw traceback,
    or with no verdict line at all). Never raises."""
    try:
        why = " ".join(str((diag or {}).get("reason") or "see the ERROR above").split())
        print(f"[provision] setup verdict: FAILED — this project's Neo4j is not usable ({why}); nothing was synced and "
              "no MCP server was started. Fix the cause, then re-run /deep-map:setup.", file=sys.stderr)
    except Exception:
        pass


def _move_off_foreign_base(target, other: str, *, stop_base: bool):
    """setup, F2: this project's registry claim on the base store turned out to name ANOTHER project's graph
    (`other`) once the store was running — release the claim, allocate the project its own instance and
    return (uri, resolved_instance) for it; None (after printing why) when that allocation fails.
    `stop_base`: setup itself started the store (it was down) — stop it again, leaving the owner's store as
    it was found. Never raises."""
    try:
        import port_registry as PR
        print(f"[provision] NOTE: this project was registered on Neo4j {PR.BASE_BOLT}/{PR.BASE_HTTP}, but that "
              f"store holds project {other}'s graph (a claim an older DeepMap wrote by mistake) — releasing the "
              "claim: this project gets its own instance (setup builds its graph there) and the base store stays "
              f"with {other}.", file=sys.stderr)
        PR.remove_entry(target, drop_neo4j_only=True)
        _, neo_problem, mcp_problem = _resolve_ports(target, allocate=True)
        if stop_base:
            stop_neo4j(project_key=None)   # setup started it only to read its owner: leave it as it was found
        uri = os.environ.get("NEO4J_URI") or DEFAULT_URI
        inst = resolved_instance(target)
        if not (neo_problem or mcp_problem) and inst is None:
            neo_problem = ("could not give this project an instance of its own (the base store is project "
                           f"{other}'s) — retry /deep-map:setup")
        if neo_problem or mcp_problem:
            for problem in (neo_problem, mcp_problem):
                if problem:
                    print(f"[provision] ERROR: {problem}", file=sys.stderr)
            print("[provision] setup verdict: FAILED — this project's ports could not be allocated; nothing was "
                  "built.", file=sys.stderr)
            return None
        return uri, inst
    except Exception as exc:  # noqa: BLE001
        print(f"[provision] ERROR: could not move this project off project {other}'s base store "
              f"({type(exc).__name__}: {exc}) — retry /deep-map:setup", file=sys.stderr)
        return None


def _build_failure_detail(target) -> str:
    """': <the recorded cause>' from this project's FAILED graph-build manifest (the engine's own error,
    else why auto fell back to ast, else the first verdict reason — sync writes them), or '' when there is
    none. Setup quotes it instead of guessing ("graphify isn't installed"). Never raises."""
    try:
        import port_registry as PR
        roots = [Path(target).resolve(), Path(PR.project_identity(target))]
    except Exception:
        roots = [Path(target)]
    for root in roots:
        try:
            m = json.loads((root / ".deepmap" / "graph_build.json").read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(m, dict) or str(m.get("verdict") or "").upper() != "FAILED":
            continue
        reasons = m.get("reasons") if isinstance(m.get("reasons"), list) else []
        for cause in (m.get("extractor_engine_error"), m.get("extractor_fallback_reason"),
                      reasons[0] if reasons else None):
            if cause:
                text = " ".join(str(cause).split())
                return ": " + (text if len(text) <= 400 else text[:399] + "…")
    return ""


def _report_verify_failure(failed: list, degraded=()) -> bool:
    """setup's final word when the end-to-end MCP check did not pass cleanly: a prominent WARNING + a
    DEGRADED verdict while every failed server is still LISTENING (exit 0 — typically a cold first call that
    outlived the deadline; the tools usually work once warm) or when a server only passed DEGRADED (search
    answered keyword-only: vector search unavailable — exit 0, the tools work without it); FAILED (exit 1)
    when a failed server no longer listens."""
    listening = all(is_http_up(mcp_host(), mcp_port(s)) for s in failed)
    why = "; ".join(f"{s}: {_VERIFY_DETAIL.get(s, 'verify failed')}" for s in failed)
    rejected = [s for s in failed if _VERIFY_AUTH.get(s) == "rejected"]
    locked = [s for s in failed if _VERIFY_AUTH.get(s) == "rate-limited"]
    print("[provision] " + "=" * 66)
    for s in degraded:
        print(f"[provision] WARNING: {s} {_VERIFY_DEGRADED.get(s, 'answered keyword-only (vector search unavailable)')}.")
    if rejected:
        # W4: the server is up but Neo4j rejects its credentials — it can never answer; not a slow start
        print(f"[provision] ERROR: the end-to-end MCP check did NOT pass — {why}.")
        print("[provision]          Neo4j REJECTED the credentials of the running server(s); they will not work")
        print("[provision]          until that is fixed: run /deep-map:doctor (a server started with an old")
        print(f"[provision]          password: {_provision_cmd('mcp-stop')} from this project's folder, then")
        print("[provision]          /deep-map:setup).")
        print(f"[provision] setup verdict: FAILED — Neo4j rejected the MCP server's credentials ({', '.join(rejected)})")
        print("[provision] " + "=" * 66)
        return False
    if locked and listening:
        print(f"[provision] WARNING: the end-to-end MCP check did NOT pass — {why}.")
        print("[provision]          Neo4j's login lock is refusing the server's logins after repeated failed")
        print("[provision]          attempts; the servers ARE listening, so setup exits 0 — the lock expires on")
        print("[provision]          its own. If a deepmap tool still errors after that, run /deep-map:doctor.")
        print(f"[provision] setup verdict: DEGRADED — Neo4j login lock engaged for {', '.join(locked)}")
        print("[provision] " + "=" * 66)
        return True
    if not failed:
        print("[provision]          The servers work, so setup exits 0 — but semantic (vector) search is OFF")
        print("[provision]          until the embedding model loads: run /deep-map:doctor.")
        print(f"[provision] setup verdict: DEGRADED — vector search unavailable ({', '.join(degraded)} answered "
              "keyword-only)")
    elif listening:
        print(f"[provision] WARNING: the end-to-end MCP check did NOT pass — {why}.")
        print("[provision]          The servers ARE listening, so setup exits 0; if a deepmap tool errors")
        print("[provision]          in the new session, run /deep-map:doctor (or re-run /deep-map:setup).")
        print(f"[provision] setup verdict: DEGRADED — MCP verify failed for {', '.join(failed)}")
    else:
        down = [s for s in failed if not is_http_up(mcp_host(), mcp_port(s))]
        print(f"[provision] ERROR: the end-to-end MCP check did NOT pass — {why} — and {', '.join(down)} "
              f"is no longer listening (see the logs in {deepmap_home()}).")
        print(f"[provision] setup verdict: FAILED — MCP server(s) down after verify: {', '.join(down)}")
    print("[provision] " + "=" * 66)
    return listening


def _print_mcp_hint(root: str = ".") -> None:
    # The reload requirement is a CLIFF, not a nicety (2026-07 field report: a user ran a fully
    # successful setup, never reloaded, and the MCP tools never appeared — they fell back to the
    # CLI). Make it unmissable. Check the PROJECT's .mcp.json (the git toplevel, where Claude Code
    # looks + where write_mcp_json writes), not the plugin/dev-repo root.
    try:
        import port_registry as PR
        mcp_json = Path(PR.project_identity(root)) / ".mcp.json"
    except Exception:
        mcp_json = Path(root).resolve() / ".mcp.json"
    if mcp_json.exists():
        print("[provision] .mcp.json present (HTTP transport: "
              f"{mcp_url('deepmap-search')} , {mcp_url('deepmap-embed')}).")
        print("[provision] " + "=" * 66)
        print("[provision] ACTION REQUIRED: start a new Claude Code session NOW, and approve")
        print("[provision]                  the two deepmap servers if Claude Code asks.")
        print("[provision]                  deepmap_search / deepmap_impact / deepmap_embed will NOT")
        print("[provision]                  appear as tools until you do: Claude Code reads this")
        print("[provision]                  project's .mcp.json when a session starts. (The servers")
        print("[provision]                  are already running detached and keep running across")
        print("[provision]                  sessions. Setup succeeding is NOT enough by itself.)")
        print("[provision]                  No prompt, or declined earlier? Run")
        print("[provision]                  `claude mcp reset-project-choices` here, then start")
        print("[provision]                  a new session.")
        print("[provision] " + "=" * 66)
    else:
        print("[provision] note: no .mcp.json found at repo root; MCP tools won't auto-register.")


# ---- the OPT-IN git post-commit auto-sync (ROUND-7 I3 / T9#7 / S-4; ROUND-8 J1) -----------------------------------
# setup never installs a git hook (a hook is the user's repo, not DeepMap's); `provision.py git-hook --install` writes a
# sentinel-wrapped post-commit block — the one the syncflag hook recognises — so a commit re-syncs the graph in the
# background. Never auto-installed; setup prints the hint. ROUND-8 J1 — the block:
#   * syncs the project's SYNC ROOT (_server_project_root = sync._sync_root's rule), never the git toplevel of a
#     sub-project set up on its own (that re-pushed every node with toplevel-relative keys: duplicates);
#   * is SCOPED to its work tree at run time (`[ "$(git rev-parse --show-toplevel)" -ef '<top>' ]`): git resolves ONE
#     hooks file for a main checkout and all its linked worktrees, so several projects' blocks may share it and each
#     runs only for its own commits (a commit in one never syncs — or claims to sync — another);
#   * is never written into a hooks file outside the repository (a global / relative-outside core.hooksPath runs for
#     every repository using it), a husky-managed or git-TRACKED hook, a non-sh (Python / Node) or binary hook, or a
#     disabled (non-executable, POSIX) one — install refuses (rc 2) and prints the line to add by hand;
#   * goes right AFTER the shebang, so a trailing `exec` / `exit` (the pre-commit framework's hook) can never skip it.
# `--status` tells the current block from a stale one (an old installer's --stale-cleanup line, a removed / other plugin
# path, an unscoped round-7 block, a wrong target, one git never reaches) and `--install` replaces those in place; an
# old installer's destructive / dead post-checkout line goes too. `--uninstall` removes only THIS work tree's blocks.
# ROUND-9: git < 2.31 (no `rev-parse --path-format`) is supported (K1); every printed git-hook command carries the sync
# root, and a bare --install at a graph-less directory while another directory of the same work tree was set up is
# refused (K2 — the old setup tip re-opened the toplevel sync from the repo top); an in-repo hooks directory the team
# commits, or a NEW hook git would not ignore, is refused like a tracked hook (K5). ROUND-10 L5: a command-line
# DEEPMAP_PYTHON ('py -3') is resolved to the interpreter it runs (one that can't run is refused), and --status checks
# that the interpreter a block names exists / runs.
_GIT_HOOK_BEGIN = "# >>> deepmap (managed; existing hook preserved on chain) >>>"
_GIT_HOOK_END = "# <<< deepmap <<<"
_GIT_HOOK_MARKER = "# deepmap"
_HOOK_SH_NAMES = frozenset({"sh", "bash", "dash", "zsh", "ksh", "ash", "mksh", "busybox"})
_HOOK_TOP_EXPR = '"$(git rev-parse --show-toplevel 2>/dev/null)"'
_HOOK_DESTRUCTIVE_FLAGS = ("--stale-cleanup", "--switch-project", "--force-prune")
_HOOK_REDIR_RE = re.compile(r"^(\d*)(>>|>\||<>|>&|<&|&>>|&>|>|<)(.*)$")
_PATH_FORMAT_ABS = "--path-format=absolute"   # git >= 2.31 only (ROUND-9 K1: older git echoes it back)
_HOOK_ROOT_WALK_S = 10.0                      # the bounded work-tree walk for another project's graph (ROUND-9 K2)


def _git_text(target, *args) -> str | None:
    """`git [-c core.longpaths=true] -C <target> <args>` stdout (UTF-8), or None on any failure. Never raises."""
    try:
        git = shutil.which("git")
        if not git:
            return None
        r = subprocess.run(_git_argv(git, target, *args), capture_output=True, timeout=15)
        if r.returncode != 0:
            return None
        out = r.stdout
        if isinstance(out, bytes):
            out = out.decode("utf-8", "surrogateescape")
        return str(out or "").strip() or None
    except Exception:
        return None


def _post_commit_hook(target):
    """(hook path, work-tree top) for `target`'s repository — the path git itself resolves (core.hooksPath and
    worktrees honoured), or (None, None) outside a git work tree. Never raises."""
    env = _hook_env(target)
    if env is None:
        return None, None
    return env["hook"], env["top"]


def _hook_env(target) -> dict | None:
    """What git resolves for `target`'s repository: {"top" (as git prints it), "top_path", "git_dir", "common_dir",
    "hook" (the post-commit file git runs), "hooks_path" (core.hooksPath or None), "linked" (a linked worktree),
    "worktrees" (work trees sharing the common dir)}, or None outside a git work tree. Never raises."""
    try:
        args = ("--show-toplevel", "--absolute-git-dir", "--git-common-dir", "--git-path", "hooks/post-commit")

        def _lines(out) -> list:
            return [ln.strip() for ln in (out or "").splitlines() if ln.strip()]
        # ROUND-9 K1: git < 2.31 (Debian 11: 2.30, Ubuntu 20.04: 2.25) does not know --path-format — rev-parse ECHOES
        # the unknown option as its first output line and exits 0, which shifted every field by one (top =
        # '--path-format=absolute', hook = <top>/.git). An echoed flag, like a failed call, means: ask again without it
        # (relative answers are resolved against the -C directory below).
        lines = _lines(_git_text(target, "rev-parse", _PATH_FORMAT_ABS, *args))
        if len(lines) < 4 or _PATH_FORMAT_ABS in lines:
            lines = _lines(_git_text(target, "rev-parse", *args))
        if len(lines) < 4 or any(ln.startswith("--") for ln in lines[:4]):
            return None
        base = Path(target).resolve()   # a relative answer is relative to the -C directory

        def _abs(s) -> Path:
            q = Path(s)
            return q if q.is_absolute() else (base / q)
        top_path = Path(lines[0])
        # the toplevel goes into the hook's run-time guard and its sync target: only an existing absolute directory
        if not (top_path.is_absolute() and top_path.is_dir()):
            return None
        git_dir, common, hook = _abs(lines[1]), _abs(lines[2]), _abs(lines[3])
        if hook.is_dir() or not git_dir.is_dir():
            return None
        wt = _git_text(target, "worktree", "list", "--porcelain") or ""
        return {"top": lines[0], "top_path": top_path, "git_dir": git_dir, "common_dir": common,
                "hook": hook, "hooks_path": _git_text(target, "config", "--get", "core.hooksPath"),
                "linked": not _same_path(git_dir, common),
                "worktrees": max(1, sum(1 for ln in wt.splitlines() if ln.startswith("worktree ")))}
    except Exception:
        return None


def _sh_quote(s) -> str:
    return "'" + str(s).replace("'", "'\\''") + "'"


# ROUND-10 L5: the interpreter a post-commit block runs. The block writes it as ONE single-quoted sh word, so a COMMAND
# LINE in DEEPMAP_PYTHON ('py -3' — the Windows launcher form the installers try first —, '/usr/bin/env python3',
# 'python3 -X utf8') made sh look for a command literally named 'py -3': every commit synced nothing (`|| true` hid the
# 127) while --status compared the block with the same env value and called it current. A command line is now resolved
# ONCE to the interpreter it runs (its sys.executable — the installers' K8 rule), and a value that can't run is refused;
# --status checks that the interpreter a block names exists / runs whatever the block says.
_HOOK_PY_TIMEOUT_S = 20.0
_HOOK_PY_CACHE: dict = {}


def _split_command_line(val: str) -> list:
    """The words of a command-line DEEPMAP_PYTHON value — shlex's POSIX rules; on Windows without backslash escapes (a
    'C:\\...' path keeps its backslashes) and one layer of matching quotes removed. [] when it can't be split (unbalanced
    quotes). port_registry.split_command_line — the ONE rule, which the SessionStart heal hook reads DEEPMAP_PYTHON with
    too (ISS-build:013). Never raises."""
    try:
        import port_registry as PR
        return PR.split_command_line(val, windows=is_windows())
    except Exception:
        return []


def _resolve_command_python(val: str):
    """(the interpreter path command line `val` runs, None) — its own sys.executable, an existing absolute file — or
    (None, why it can't be used). Cached per (value, PATH). Never raises."""
    key = (val, os.environ.get("PATH", ""))
    if key in _HOOK_PY_CACHE:
        return _HOOK_PY_CACHE[key]
    words = _split_command_line(val)
    if not words:
        res = (None, f"DEEPMAP_PYTHON={val!r} can't be read as a command line (unbalanced quotes)")
    else:
        try:
            r = subprocess.run([*words, "-c", "import sys; sys.stdout.write(sys.executable)"], capture_output=True,
                               stdin=subprocess.DEVNULL, timeout=_HOOK_PY_TIMEOUT_S,
                               env=dict(os.environ, PYTHONIOENCODING="utf-8"))
            out = (r.stdout or b"").decode("utf-8", "replace").strip()
            if r.returncode == 0 and out and Path(out).is_absolute() and Path(out).is_file():
                res = (out, None)
            else:
                res = (None, f"DEEPMAP_PYTHON={val!r} did not run as a Python interpreter (exit {r.returncode})")
        except FileNotFoundError:
            res = (None, f"DEEPMAP_PYTHON={val!r} names a command that was not found ({words[0]})")
        except subprocess.TimeoutExpired:
            res = (None, f"DEEPMAP_PYTHON={val!r} did not answer within {int(_HOOK_PY_TIMEOUT_S)} s")
        except Exception as exc:  # noqa: BLE001
            res = (None, f"DEEPMAP_PYTHON={val!r} could not be run ({type(exc).__name__})")
    _HOOK_PY_CACHE[key] = res
    return res


def _bare_command_problem(name: str, label: str):
    """Why the bare command `name` (no path) can't serve as a hook's Python — not on PATH, or it does not run
    `-c "import sys"` (e.g. Windows' python3 Store alias with no Python installed) — or None. Cached. Never raises."""
    key = ("bare", name, os.environ.get("PATH", ""))
    if key in _HOOK_PY_CACHE:
        return _HOOK_PY_CACHE[key]
    try:
        exe = shutil.which(name)
        if not exe:
            res = f"{label} ({name}) is not a command on PATH"
        else:
            r = subprocess.run([exe, "-c", "import sys"], capture_output=True, stdin=subprocess.DEVNULL,
                               timeout=_HOOK_PY_TIMEOUT_S)
            res = None if r.returncode == 0 else f"{label} ({name}) does not run (exit {r.returncode})"
    except subprocess.TimeoutExpired:
        res = f"{label} ({name}) did not answer within {int(_HOOK_PY_TIMEOUT_S)} s"
    except Exception:
        res = None
    _HOOK_PY_CACHE[key] = res
    return res


def _hook_interp_problem(py, *, label: str = "its Python", gone: str = "no longer exists"):
    """ROUND-10 L5: why the interpreter word `py` of a post-commit block (a sh word, unquoted) can never run — or None
    when it can, or can't be judged here (computed at run time, a relative path). A rooted path must exist (and be
    executable, POSIX); a word with whitespace is ONE sh word that names no command; a bare command must be on PATH and
    run. Never raises."""
    try:
        s = str(py or "").strip()
        if not s or "$" in s or "`" in s:
            return None
        p = _sh_path(s)
        if p is not None and _rooted(p):
            if not p.exists():
                return f"{label} ({s}) {gone}"
            if p.is_dir():
                return f"{label} ({s}) is a directory, not a Python interpreter"
            if not is_windows() and not os.access(str(p), os.X_OK):
                return f"{label} ({s}) is not executable"
            return None
        if re.search(r"\s", s):
            return (f"{label} is `{s}` as ONE word — sh looks for a command literally named that (a command line in "
                    "DEEPMAP_PYTHON), so it never runs and the hook syncs nothing")
        if "/" in s or "\\" in s:
            return None
        return _bare_command_problem(s, label)
    except Exception:
        return None


def _hook_python():
    """ROUND-10 L5: (the interpreter the post-commit block runs, problem or None) from DEEPMAP_PYTHON (F4):
      * unset / blank -> the Python running this provisioner (it has DeepMap's dependencies);
      * an existing file, or ONE word -> that value verbatim; one that does not exist / is not a runnable command is a
        `problem` (--install refuses it; the block text keeps naming it, as before);
      * a COMMAND LINE (whitespace, not a file: 'py -3', '/usr/bin/env python3') -> resolved once to the interpreter it
        runs (its sys.executable — the installers' K8 rule); one that does not run is a `problem`, and the block names
        this provisioner's Python meanwhile (so --status never mistakes the old one-word block for the current one).
    Never raises."""
    try:
        val = os.environ.get("DEEPMAP_PYTHON") or ""
        if not val.strip():
            return sys.executable, None
        if os.path.isfile(val):
            return val, None
        if re.search(r"\s", val.strip()) and not os.path.isdir(val):
            py, problem = _resolve_command_python(val.strip())
            return (py, None) if py else (sys.executable, problem)
        return val, _hook_interp_problem(val, label="DEEPMAP_PYTHON", gone="does not exist")
    except Exception:
        return sys.executable, None


def _git_hook_block(root, top=None) -> str:
    """The post-commit block for the project whose sync root is `root` in the work tree `top` (default: git's toplevel
    of `root`): a DETACHED sync (nohup + &) that can never fail or slow a commit (`|| true`), run only when the commit
    happens in THAT work tree (`-ef`: a hooks file shared by worktrees runs every project's block), logging to
    <DEEPMAP_HOME>/sync.log — no --stale-cleanup (never a destructive flag in a background hook, F1). The interpreter
    honours DEEPMAP_PYTHON (F4 — a command line is resolved to the interpreter it runs, ROUND-10 L5: _hook_python), else
    the one running this provisioner (it has DeepMap's dependencies). Paths are single-quoted (any character is safe)
    and use forward slashes on Windows (git's sh)."""
    fwd = (lambda p: Path(p).as_posix()) if is_windows() else (lambda p: str(p))
    if top is None:
        top = _git_text(root, "rev-parse", "--show-toplevel") or str(root)
    py = _hook_python()[0] or sys.executable
    home = deepmap_home()
    line = (f"if [ {_HOOK_TOP_EXPR} -ef {_sh_quote(fwd(top))} ]; then ( mkdir -p {_sh_quote(fwd(home))} 2>/dev/null; "
            f"PYTHONUTF8=1 nohup {_sh_quote(fwd(py))} {_sh_quote(fwd(HERE / 'deepmap_cli.py'))} sync "
            f"{_sh_quote(fwd(root))} >{_sh_quote(fwd(home / 'sync.log'))} 2>&1 & ) || true; fi   {_GIT_HOOK_MARKER}")
    return f"{_GIT_HOOK_BEGIN}\n{line}\n{_GIT_HOOK_END}\n"


def _sh_path(p):
    """A path as a hook (git's sh) spells it -> a Path on this OS: '/c/Users/x' (the Git-Bash form an installer wrote)
    -> 'C:/Users/x' on Windows. None when empty or computed at run time ($…, `…`). Never raises."""
    try:
        s = str(p or "").strip()
        if not s or "$" in s or "`" in s:
            return None
        if is_windows():
            m = re.match(r"^/([A-Za-z])(/.*)?$", s)
            if m:
                s = f"{m.group(1).upper()}:{m.group(2) or '/'}"
        return Path(s)
    except Exception:
        return None


def _rooted(p) -> bool:
    """An absolute path, or a rooted one: on Windows `/home/u/x` is rooted on the current drive (a POSIX path from another
    machine's hook) though not absolute. Never raises."""
    try:
        q = Path(p)
        return q.is_absolute() or bool(q.anchor)
    except Exception:
        return False


def _is_within(child, parent) -> bool:
    """Is `child` the directory `parent` or inside it (resolved; case-insensitive on Windows)? Never raises."""
    try:
        c = os.path.normcase(str(Path(child).resolve())).rstrip("\\/")
        p = os.path.normcase(str(Path(parent).resolve())).rstrip("\\/")
        return c == p or c.startswith(p + os.sep)
    except Exception:
        return False


def _target_in_work_tree(tp, top) -> bool:
    """Does the directory `tp` belong to the work tree `top` — `top` itself, or below it with no nested repository /
    linked worktree (a `.git` entry) in between? A path that no longer exists is judged by its prefix. Never raises."""
    try:
        if not _is_within(tp, top):
            return False
        d, top_n = Path(tp), os.path.normcase(str(Path(top).resolve())).rstrip("\\/")
        for _ in range(256):
            if os.path.normcase(str(d.resolve())).rstrip("\\/") == top_n:
                return True
            if (d / ".git").exists():
                return False
            if d.parent == d:
                return False
            d = d.parent
    except Exception:
        pass
    return False


def _hook_interpreter(text: str) -> str | None:
    """None when git runs this hook text as sh — a shebang naming a POSIX shell (directly or via env), or none (git runs
    a shebang-less hook with sh); else the interpreter it names ('python3', 'node', ...). Never raises."""
    try:
        first = (text.splitlines() or [""])[0].strip()
        if not first.startswith("#!"):
            return None
        words = first[2:].split()
        if not words:
            return None
        name = words[0].replace("\\", "/").rsplit("/", 1)[-1]
        if name.lower() == "env":
            rest = [w for w in words[1:] if not w.startswith("-") and "=" not in w]
            name = rest[0].replace("\\", "/").rsplit("/", 1)[-1] if rest else ""
        return None if name.lower() in _HOOK_SH_NAMES else (name or first[2:].strip() or "an unknown interpreter")
    except Exception:
        return "an unknown interpreter"


def _hook_exec_replaces_shell(rest: str) -> bool:
    """For `exec <rest>`: True when it runs a command (nothing after it runs), False when it only redirects the shell's
    own descriptors (`exec >log 2>&1`) or is bare. Never raises (-> True)."""
    try:
        words = rest.split("#", 1)[0].split()
        i = 0
        while i < len(words):
            m = _HOOK_REDIR_RE.match(words[i])
            if not m:
                return True
            if not m.group(3):
                i += 1
            i += 1
        return False
    except Exception:
        return True


def _hook_line_terminates(line: str) -> bool:
    """True iff an UNINDENTED hook line ends the script unconditionally — `exec <cmd>`, `exit [n]`, or sourcing husky's
    runner (it exits itself) — so nothing after it runs (the syncflag hook's rule). Never raises."""
    try:
        if not line or line[0] in " \t":
            return False
        s = line.strip()
        if s.startswith("#"):
            return False
        if re.match(r"^exit\b(?!\s*=)", s):
            return True
        m = re.match(r"^exec\b(?!\s*=)(.*)$", s)
        if m:
            return _hook_exec_replaces_shell(m.group(1))
        return bool(re.match(r"""^\.\s+["']?\$\(dirname\s+(--\s+)?"?\$0"?\)/(h|_/husky\.sh)["']?\s*$""", s)
                    or re.match(r"""^\.\s+["']?\$\{0%/\*\}/h["']?\s*$""", s))
    except Exception:
        return False


def _hook_units(lines: list) -> list:
    """DeepMap's content among a hook's lines: (start, end) spans of every sentinel block (`# >>> deepmap` … `# <<<
    deepmap`; a block whose END is missing spans its BEGIN line and the marker lines right after it), every bare line
    ending with the `# deepmap` marker (an old installer's), and an orphaned END line. Never raises."""
    units: list = []
    try:
        i, n = 0, len(lines)
        while i < n:
            s = lines[i].strip()
            if s.startswith("# >>> deepmap"):
                j = i + 1
                while j < n and not lines[j].strip().startswith(("# <<< deepmap", "# >>> deepmap")):
                    j += 1
                if j < n and lines[j].strip().startswith("# <<< deepmap"):
                    units.append((i, j + 1))
                    i = j + 1
                    continue
                j = i + 1
                while j < n and lines[j].strip().endswith(_GIT_HOOK_MARKER) and not lines[j].strip().startswith("#"):
                    j += 1
                units.append((i, j))
                i = j
                continue
            if s.startswith("# <<< deepmap") or (s.endswith(_GIT_HOOK_MARKER) and not s.startswith("#")):
                units.append((i, i + 1))
            i += 1
    except Exception:
        pass
    return units


def _parse_hook_sync(line: str) -> dict | None:
    """The DeepMap sync one hook line runs — {"cli", "target", "flags", "interp", "guard"} as sh words (unquoted) — or
    None when it runs no `deepmap_cli.py sync`; {"unparsed": True} when it names deepmap_cli.py but can't be read.
    Never raises."""
    if "deepmap_cli.py" not in (line or ""):
        return None
    try:
        import shlex
        lex = shlex.shlex(line, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        toks = list(lex)
    except Exception:
        return {"unparsed": True}
    for i, t in enumerate(toks):
        if not (t.endswith("deepmap_cli.py") and i + 2 < len(toks) and toks[i + 1] == "sync"):
            continue
        flags = []
        for f in toks[i + 3:]:
            if not f.startswith("-"):
                break
            flags.append(f)
        interp = None
        for w in reversed(toks[:i]):
            if w in ("nohup", "exec", "command") or re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", w):
                continue
            if w not in ("(", ")", ";", "&&", "||", "&", "|", "then", "do", "{", "}", "else"):
                interp = w
            break
        guard = next((toks[k + 1] for k in range(i) if toks[k] == "-ef" and k + 1 < len(toks)), None)
        return {"cli": t, "target": toks[i + 2], "flags": flags, "interp": interp, "guard": guard}
    return {"unparsed": True}


def _hook_unit_info(lines: list, span, env: dict, root, want_lines: list) -> dict:
    """One DeepMap unit of a hook, judged for the project at sync root `root` of the work tree `env`:
      mine        — it belongs to THIS work tree: its run-time guard names this top; an unguarded line syncs a target in
                    this work tree (or one computed at run time); an unreadable one (DeepMap's, in this repo's file);
      current     — it is exactly the block this build writes for this project;
      reasons     — why a unit of this work tree is not current (the --status text);
      destructive — it runs a destructive sync flag; dead — its CLI no longer exists.
    Never raises."""
    body = [ln.rstrip("\r\n") for ln in lines[span[0]:span[1]]]
    info = {"span": span, "mine": True, "current": body == want_lines, "reasons": [], "destructive": False,
            "dead": False, "block": bool(body) and body[0].strip().startswith("# >>> deepmap")}
    try:
        parsed = [p for p in (_parse_hook_sync(ln) for ln in body) if p is not None and not p.get("unparsed")]
        if parsed:
            p = parsed[0]
            if p.get("guard"):
                g = _sh_path(p["guard"])
                info["mine"] = g is not None and _same_path(g, env["top_path"])
            else:
                tgt = str(p.get("target") or "")
                tp = _sh_path(tgt)
                info["mine"] = (tgt in (".", "./") or tp is None) or _target_in_work_tree(tp, env["top_path"])
        if not info["mine"]:
            return info
        # ROUND-10 L5: the interpreter the block names must exist / run, even in a block that is otherwise exactly
        # this build's (a one-word 'py -3', a removed venv) — a hook that can't start its Python syncs nothing
        py_problem = _hook_interp_problem(parsed[0].get("interp")) if parsed else None
        if info["current"] and not py_problem:
            return info
        info["current"] = False
        reasons = info["reasons"]
        if py_problem:
            reasons.append(py_problem)
        if not parsed:
            reasons.append("an empty or unreadable DeepMap block" if info["block"] else "an unreadable DeepMap line")
        for p in parsed[:1]:
            bad = [f for f in p["flags"] if f in _HOOK_DESTRUCTIVE_FLAGS]
            if bad:
                info["destructive"] = True
                reasons.append(f"it runs a destructive `sync {' '.join(bad)}` on every commit (an old installer's line)")
            cli = _sh_path(p["cli"])
            if cli is not None and _rooted(cli) and not cli.is_file():
                info["dead"] = True
                reasons.append(f"its DeepMap CLI no longer exists ({p['cli']} — a removed plugin version), so it syncs "
                               "nothing")
            elif cli is not None and not _same_path(cli, HERE / "deepmap_cli.py"):
                reasons.append(f"it runs DeepMap from {cli.parent.parent.as_posix()} — not this plugin "
                               f"({HERE.parent.as_posix()})")
            py, cur_py = p.get("interp"), _hook_python()[0] or sys.executable
            pyp = _sh_path(py) if py else None
            if py and not py_problem and not (pyp is not None and _same_path(pyp, cur_py)) and str(py) != str(cur_py):
                reasons.append(f"it runs {py} (this provisioner runs {cur_py})")
            tp = _sh_path(p["target"])
            if tp is not None and not _same_path(tp, root):
                reasons.append(f"it syncs {p['target']}, not this project's sync root ({Path(root).as_posix()})")
            if not p.get("guard"):
                reasons.append("it is not scoped to this work tree (an older block runs for every work tree that shares "
                               "this hooks file)")
        if not info["block"] and parsed:
            reasons.append("it is an old installer's line (no DeepMap sentinel block)")
        if not reasons:
            reasons.append("it differs from the block this DeepMap version writes")
    except Exception:
        info["reasons"] = info["reasons"] or ["an unreadable DeepMap block"]
    return info


def _read_hook(path: Path):
    """(raw bytes, text) of a hook file — text None when absent or binary (a NUL byte). Line endings are kept (never
    read_text: its universal newlines turned a CRLF hook into LF on rewrite). Never raises."""
    try:
        if not path.is_file():
            return None, None
        raw = path.read_bytes()
        return raw, (None if b"\x00" in raw else raw.decode("utf-8", "surrogateescape"))
    except Exception:
        return None, None


def _git_rc(top, *args) -> int | None:
    """The exit code of `git -C <top> <args>` (output discarded), or None when git can't be run. Never raises."""
    try:
        git = shutil.which("git")
        if not git:
            return None
        return subprocess.run(_git_argv(git, top, *args), capture_output=True, timeout=15).returncode
    except Exception:
        return None


def _git_tracks_under(top, rel_dir: str) -> bool | None:
    """Does git track any file under `rel_dir` (relative to the work tree `top`)? None when git can't tell. Never
    raises."""
    try:
        out = _git_text(top, "ls-files", "--", rel_dir or ".")
        if out is not None:
            return True
        return False if _git_rc(top, "rev-parse", "--is-inside-work-tree") == 0 else None
    except Exception:
        return None


def _hook_location_problem(env: dict, hook: Path) -> str | None:
    """Why DeepMap must not write into the hooks file `hook` at all — husky manages it, it lies outside this repository
    (a global / relative-outside core.hooksPath: every repository using that directory would run one project's sync),
    it is tracked by git (a committed hook every clone shares), or (ROUND-9 K5) it sits in an in-repo core.hooksPath
    directory the team COMMITS (git tracks files there — e.g. .githooks/ with a pre-commit: a new post-commit there
    carrying this machine's interpreter / plugin / DEEPMAP_HOME / project paths is one `git add .` from being shipped to
    every clone), or it would be CREATED in the work tree as a file git does not ignore (the same exposure, plus the
    untracked-file noise the v0.27.2 auto-gitignore removed) — else None. Never raises."""
    try:
        hooks_dir = hook.parent
        cfg = env.get("hooks_path")
        where = f"core.hooksPath={cfg}" if cfg else str(hooks_dir)
        if any(part.lower() == ".husky" for part in hooks_dir.parts):
            return (f"this repository's hooks are managed by husky ({where}) — husky regenerates {hook.name}, so "
                    "DeepMap won't edit it; add the line below to your own .husky/post-commit instead")
        in_common, in_top = _is_within(hooks_dir, env["common_dir"]), _is_within(hooks_dir, env["top_path"])
        if not in_common and not in_top:
            return (f"{where} points outside this repository ({hooks_dir}) — a hook there runs for every repository "
                    "that uses that directory, so DeepMap won't write one project's sync into it; add the line below "
                    "to it yourself if you want it")
        if in_top and not in_common:
            top = env["top_path"]
            rel = os.path.relpath(str(hook.resolve()), str(top.resolve())).replace("\\", "/")
            if hook.is_file() and _git_tracked(top, rel):
                return (f"{hook} is tracked by git (a committed hook every clone of this repository runs) — DeepMap "
                        "won't write this machine's paths into it; add the line below to it yourself if you want it")
            rel_dir = os.path.relpath(str(hooks_dir.resolve()), str(top.resolve())).replace("\\", "/")
            if _git_tracks_under(top, rel_dir):
                return (f"{hooks_dir} ({where}) is a hooks directory this repository commits (git tracks files in it — "
                        f"hooks the team shares) — DeepMap won't create or edit a {hook.name} there that carries this "
                        "machine's paths (one `git add` from being committed to every clone); add the line below to "
                        "your own local hook if you want it")
            if not hook.exists() and _git_rc(top, "check-ignore", "-q", "--", rel) == 1:
                return (f"{hooks_dir} ({where}) lies inside this work tree and git does not ignore it — a new "
                        f"{hook.name} there would be an untracked file carrying this machine's paths (one `git add` from "
                        "being committed); ignore that directory (e.g. in .git/info/exclude) or add the line below to "
                        "a hook of your own")
    except Exception:
        return None
    return None


def _hook_content_problem(hook: Path, raw: bytes | None) -> str | None:
    """Why DeepMap's sh block can't be chained onto the existing hook — a binary, a non-sh script (git runs a Python /
    Node hook with that interpreter: appended sh broke it), or (POSIX) a hook that is not executable (git skips it: a
    disabled hook DeepMap must not re-enable) — else None. Never raises."""
    try:
        if raw is None:
            return None
        if b"\x00" in raw:
            return f"{hook} is a binary file, not a script — DeepMap won't edit it"
        interp = _hook_interpreter(raw.decode("utf-8", "surrogateescape"))
        if interp:
            return (f"{hook} is a {interp} script — git runs it with {interp}, so DeepMap's sh block can't be chained "
                    "onto it (it would break the hook); run the sync below from it yourself, in its own language")
        if not is_windows() and not os.access(str(hook), os.X_OK):
            return (f"{hook} is not executable, so git skips it (a disabled hook) — DeepMap won't re-enable it; make "
                    "it executable (or remove it) first if you want the auto-sync")
    except Exception:
        return None
    return None


def _git_hook_cmd(action: str, root=None) -> str:
    """The copy-pasteable `provision.py git-hook --<action> "<root>"` for a message (ROUND-9 K2: always with the
    project's sync root — a bare command run from the repo top resolves the TOP, which re-opened J1a)."""
    arg = f' "{Path(str(root)).as_posix()}"' if root else ""
    return _provision_cmd(f"git-hook --{action}{arg}")


def _other_graph_roots(env: dict, root) -> list:
    """The OTHER sync roots of this work tree — directories (not `root`) holding a DeepMap manifest
    (.deepmap/graph_build.json; DEEPMAP_HOME's own state dir never counts), nested repositories / linked worktrees (a
    `.git` entry: another work tree, another project key) excluded — found by a walk of the work tree bounded by
    _HOOK_ROOT_WALK_S (a walk that runs out of time answers what it found). Never raises."""
    found: list = []
    try:
        top = env["top_path"]
        try:
            home_state = deepmap_home().resolve()
        except Exception:
            home_state = None
        deadline = time.monotonic() + _HOOK_ROOT_WALK_S
        for dirpath, dirnames, _files in os.walk(str(top)):
            d = Path(dirpath)
            if ".deepmap" in dirnames:
                m = d / ".deepmap"
                try:
                    if (m / "graph_build.json").is_file() and (home_state is None or m.resolve() != home_state) \
                            and not _same_path(d, root):
                        found.append(d)
                except OSError:
                    pass
            keep = []
            for n in dirnames:
                if n in _CENSUS_PRUNE or n.endswith(".egg-info"):
                    continue
                try:
                    if (d / n / ".git").exists():
                        continue
                except OSError:
                    continue
                keep.append(n)
            dirnames[:] = keep
            if len(found) >= 5 or time.monotonic() > deadline:
                break
    except Exception:
        pass
    return found


def _hook_root_problem(env: dict, root) -> tuple:
    """ROUND-9 K2: (why --install must not write a block for the sync root `root`, the other sync roots) — `root` has no
    DeepMap graph of its own while another directory of the SAME work tree was set up. One git work tree is one project
    key, so one Neo4j instance: a hook syncing `root` (e.g. the repo top, from a bare `git-hook --install` there) would
    write the whole tree into the sub-project's graph — J1a's corruption, re-opened through the old setup tip. (None,
    []) when `root` has its own manifest or no other one exists. Never raises."""
    try:
        if (Path(str(root)) / ".deepmap" / "graph_build.json").is_file():
            return None, []
        others = _other_graph_roots(env, root)
        if not others:
            return None, []
        names = ", ".join(p.as_posix() for p in others[:3]) + (" …" if len(others) > 3 else "")
        r = Path(str(root)).as_posix()
        return (f"{r} has no DeepMap graph of its own (no .deepmap/graph_build.json there), but this work tree's graph "
                f"was set up for {names} — one git work tree is one DeepMap project (one Neo4j instance), so a hook "
                f"syncing {r} would write all of it into that project's graph; install the hook for the project that "
                f"was set up instead: {_git_hook_cmd('install', others[0])}"), others
    except Exception:
        return None, []


def _git_hook_report(target) -> dict:
    """What `target`'s repository hook holds for this project — the data behind --status / --install / --uninstall and
    setup's hint:
      state    "current" (exactly this build's block, reachable) | "stale" (a DeepMap block of this work tree that is
               not — `reasons` say why) | "absent" | "no-git" (not a git work tree) | "error";
      refusal  why --install must not write the hooks file (location / content), or None;
      root_problem  why --install must not write a block for this sync root (ROUND-9 K2: it has no graph of its own
               while another directory of this work tree was set up — `other_roots`), or None;
      others   DeepMap blocks in the file that belong to OTHER work trees (left alone);
      post_checkout  this work tree's DeepMap units in post-checkout (an old installer's), each with its reasons.
    Never raises."""
    rep: dict = {"state": "no-git", "reasons": [], "refusal": None, "root_problem": None, "other_roots": [],
                 "others": 0, "units": [], "lines": [], "post_checkout": [], "hook": None, "top": None, "root": None,
                 "raw": None, "text": None}
    try:
        env = _hook_env(target)
        if env is None:
            return rep
        root = _server_project_root(target)
        hook = env["hook"]
        want = _git_hook_block(root, env["top"]).splitlines()
        raw, text = _read_hook(hook)
        python, py_problem = _hook_python()
        rep.update(env=env, hook=hook, top=env["top"], root=root, raw=raw, text=text, want=want,
                   shared=bool(env["linked"] or env["worktrees"] > 1), python=python, py_problem=py_problem)
        rep["refusal"] = _hook_location_problem(env, hook) or _hook_content_problem(hook, raw)
        rep["root_problem"], rep["other_roots"] = _hook_root_problem(env, root)
        lines = text.splitlines(keepends=True) if text else []
        units = [_hook_unit_info(lines, sp, env, root, want) for sp in _hook_units(lines)]
        mine = [u for u in units if u["mine"]]
        rep.update(lines=lines, units=units, others=len(units) - len(mine))
        if not mine:
            rep["state"] = "absent"
        else:
            reasons: list = []
            interp = _hook_interpreter(text or "")
            if interp:
                reasons.append(f"the hook is a {interp} script — git never runs DeepMap's sh block in it")
            cur = [u for u in mine if u["current"]]
            if len(mine) > 1:
                reasons.append(f"it holds {len(mine)} DeepMap blocks for this work tree")
            for u in mine:
                if not u["current"]:
                    reasons += u["reasons"]
            if cur and not interp:
                term = next(((i + 1, lines[i].strip()) for i in range(cur[0]["span"][0])
                             if _hook_line_terminates(lines[i].rstrip("\r\n"))), None)
                if term:
                    reasons.append(f"it comes after `{term[1]}` (line {term[0]}), so git never reaches it")
            if not is_windows() and not os.access(str(hook), os.X_OK):
                reasons.append("the hook file is not executable, so git skips it")
            rep["reasons"] = list(dict.fromkeys(reasons))
            rep["state"] = "current" if (cur and not rep["reasons"]) else "stale"
        _pc_raw, pc_text = _read_hook(hook.with_name("post-checkout"))
        if pc_text:
            pl = pc_text.splitlines(keepends=True)
            rep["post_checkout"] = [u for u in (_hook_unit_info(pl, sp, env, root, want) for sp in _hook_units(pl))
                                    if u["mine"]]
    except Exception as exc:
        rep.update(state="error", error=f"{type(exc).__name__}: {exc}")
    return rep


def _drop_spans(lines: list, spans) -> list:
    """`lines` without the given (start, end) spans; a blank line right BEFORE a dropped sentinel block (the separator
    an append-style chain added) goes with it. Never raises."""
    drop: set = set()
    for a, b in spans:
        drop.update(range(a, b))
        if a > 0 and (a - 1) not in drop and not lines[a - 1].strip() and lines[a].strip().startswith("# >>> deepmap"):
            drop.add(a - 1)
    return [ln for i, ln in enumerate(lines) if i not in drop]


def _write_hook_text(path: Path, text: str) -> None:
    """Write a hook's text back byte-exactly (UTF-8 + surrogateescape, no newline translation); a NEW file is made
    executable (POSIX). An existing file keeps its mode."""
    new = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8", "surrogateescape"))
    if new and not is_windows():
        try:
            os.chmod(path, path.stat().st_mode | 0o111)
        except OSError:
            pass


def _clean_post_checkout(rep: dict, *, everything: bool) -> list:
    """Remove this work tree's DeepMap units from post-checkout — all of them (`everything`, uninstall) or only an old
    installer's destructive (--stale-cleanup) / dead (removed plugin) ones (install). A file left with nothing but a
    `#!/bin/sh` line is deleted. Returns what was removed (for the message). Never raises."""
    done: list = []
    try:
        pc = rep["hook"].with_name("post-checkout")
        units = [u for u in rep.get("post_checkout") or [] if everything or u["destructive"] or u["dead"]]
        if not units:
            return done
        if _hook_location_problem(rep["env"], pc):
            return done   # a tracked / foreign post-checkout: report only (status names it)
        _raw, text = _read_hook(pc)
        if not text:
            return done
        rest = "".join(_drop_spans(text.splitlines(keepends=True), [u["span"] for u in units]))
        if rest.strip() in ("", "#!/bin/sh"):
            pc.unlink(missing_ok=True)
        else:
            _write_hook_text(pc, rest)
        done = [r for u in units for r in (u["reasons"] or ["a DeepMap line"])]
    except Exception:
        pass
    return done


def _hook_manual_line(rep: dict) -> str:
    want = rep.get("want") or []
    return want[1] if len(want) > 1 else ""


def _git_hook_status(rep: dict) -> int:
    hook, root = rep["hook"], rep["root"]
    state = rep["state"]
    if state == "current":
        print(f"[provision] git-hook: installed — each commit re-syncs {Path(root).as_posix()} in the background "
              f"({hook})")
    elif state == "stale":
        print(f"[provision] git-hook: installed but STALE — {hook}: " + "; ".join(rep["reasons"]) + ". "
              + (f"Run {_git_hook_cmd('install', root)} to update it (or --uninstall)."
                 if not (rep["refusal"] or rep.get("root_problem"))
                 else f"Run {_git_hook_cmd('uninstall', root)} to remove it."))
    else:
        print(f"[provision] git-hook: not installed — {hook}"
              + ("" if (rep["refusal"] or rep.get("root_problem"))
                 else f" (opt in: {_git_hook_cmd('install', root)})"))
    if rep.get("others"):
        print(f"[provision] git-hook: this hooks file also holds DeepMap block(s) of {rep['others']} other work tree(s) "
              "(a hooks file shared by worktrees) — each runs only for its own commits; left alone.")
    for u in rep.get("post_checkout") or []:
        if not u["current"]:
            print(f"[provision] git-hook: post-checkout carries an old DeepMap line ({'; '.join(u['reasons'])}) — "
                  f"{_git_hook_cmd('install', root)} removes it if it is destructive or dead, --uninstall always.")
    if rep.get("root_problem"):
        print(f"[provision] git-hook: NOTE — --install is not available for {Path(root).as_posix()}: "
              f"{rep['root_problem']}.")
    if rep["refusal"]:
        print(f"[provision] git-hook: NOTE — --install is not available here: {rep['refusal']}.")
    if rep.get("py_problem"):
        print(f"[provision] git-hook: NOTE — --install is not available until DEEPMAP_PYTHON names a working Python "
              f"interpreter: {rep['py_problem']}.")
    return 0


def _git_hook_install(rep: dict) -> int:
    hook, root, env = rep["hook"], rep["root"], rep["env"]
    if rep.get("root_problem"):
        # ROUND-9 K2 first: the block itself would name the wrong sync root, so there is no manual line to offer
        print(f"[provision] git-hook: NOT installed — {rep['root_problem']}.", file=sys.stderr)
        return 2
    if rep.get("py_problem"):
        # ROUND-10 L5: a block naming an interpreter that can't run syncs nothing on every commit (and `|| true` hides it)
        print(f"[provision] git-hook: NOT installed — {rep['py_problem']}, so a hook naming it would never sync. Set "
              "DEEPMAP_PYTHON to a Python interpreter's full path (or unset it: the hook then runs "
              f"{Path(sys.executable).as_posix()}), then re-run {_git_hook_cmd('install', root)}.", file=sys.stderr)
        return 2
    if rep["refusal"]:
        print(f"[provision] git-hook: NOT installed — {rep['refusal']}:\n    {_hook_manual_line(rep)}", file=sys.stderr)
        return 2
    raw, text = rep["raw"], rep["text"] or ""
    eol = "\r\n" if "\r\n" in text else "\n"
    block = "".join(ln + eol for ln in rep["want"])
    mine = [u for u in rep["units"] if u["mine"]]
    kept = _drop_spans(rep["lines"], [u["span"] for u in mine])
    if raw is None or not text.strip():
        new, how = "#!/bin/sh\n" + block.replace("\r\n", "\n"), ("created" if raw is None else "written into the empty hook")
    elif kept and kept[0].startswith("#!"):
        first = kept[0] if kept[0].endswith(("\n", "\r")) else kept[0] + eol
        new = first + block + "".join(kept[1:])
        how = "updated" if mine else "chained onto the existing hook, right after its first line"
    else:
        new = block + "".join(kept)
        how = "updated" if mine else "chained onto the existing hook, at its top"
    cleaned = _clean_post_checkout(rep, everything=False)
    extra = (f" Also removed an old installer's post-checkout line ({'; '.join(dict.fromkeys(cleaned))})."
             if cleaned else "")
    if raw is not None and new == text:
        print(f"[provision] git-hook: already installed in {hook} — each commit re-syncs {Path(root).as_posix()}.{extra}")
        return 0
    _write_hook_text(hook, new)
    why = (" (was: " + "; ".join(rep["reasons"]) + ")") if (mine and rep["reasons"]) else ""
    print(f"[provision] git-hook: installed ({how}{why}) in {hook} — each commit in this work tree now re-syncs "
          f"{Path(root).as_posix()} in the background (log: {deepmap_home() / 'sync.log'}). Remove it with "
          f"{_git_hook_cmd('uninstall', root)}.{extra}")
    if rep.get("shared"):
        print(f"[provision] git-hook: NOTE — this hooks file is shared by this repository's work trees ({hook}); "
              f"DeepMap's block runs only for commits in {env['top']}.")
    if not (Path(root) / ".deepmap" / "graph_build.json").is_file():
        print(f"[provision] git-hook: NOTE — no DeepMap graph has been built for {Path(root).as_posix()} yet (nothing "
              "in this work tree was set up): run /deep-map:setup there first — until then the hook's sync has no "
              "project set up to refresh.")
    return 0


def _git_hook_uninstall(rep: dict) -> int:
    hook = rep["hook"]
    mine = [u for u in rep["units"] if u["mine"]]
    cleaned = _clean_post_checkout(rep, everything=True)
    extra = " (and this work tree's DeepMap line from post-checkout)" if cleaned else ""
    if not mine:
        print(f"[provision] git-hook: not installed in {hook} — nothing to remove{extra}.")
        return 0
    rest = "".join(_drop_spans(rep["lines"], [u["span"] for u in mine]))
    if rest.strip() in ("", "#!/bin/sh"):
        hook.unlink(missing_ok=True)
        print(f"[provision] git-hook: removed {hook} (it held only DeepMap's block){extra}.")
    else:
        _write_hook_text(hook, rest)
        print(f"[provision] git-hook: removed this work tree's DeepMap block from {hook} (the rest of the hook is "
              f"kept){extra}.")
    return 0


def git_hook(target: str = ".", action: str = "status") -> int:
    """`provision.py git-hook [--install | --uninstall | --status] [dir]` — the OPT-IN post-commit auto-sync of the
    project `dir` belongs to (its sync root). See the section note (ROUND-8 J1):
      * install   — insert the scoped block right after the existing hook's shebang (the rest kept byte-for-byte, same
                    line endings) or create the hook (`#!/bin/sh` + the block, executable); a stale DeepMap block of
                    this work tree is replaced in place; idempotent. REFUSED (rc 2, the line to add by hand printed)
                    for a hooks file outside the repository, a husky-managed / git-tracked / non-sh / binary /
                    disabled hook, a hooks directory the repository commits or a new hook git would not ignore
                    (ROUND-9 K5); REFUSED (rc 2, the right command printed) for a sync root with no graph of its own
                    while another directory of the same work tree was set up (ROUND-9 K2), and while DEEPMAP_PYTHON
                    names an interpreter that can't run (ROUND-10 L5 — a command line such as `py -3` is resolved to
                    the interpreter it runs).
      * uninstall — remove only THIS work tree's DeepMap blocks (other work trees' stay); a hook that held nothing
                    else is removed.
      * status    — current / stale (why — incl. an interpreter that no longer exists / runs, ROUND-10 L5) / not
                    installed, the other work trees' blocks, an old post-checkout line.
    Returns 0 on success, 2 outside a git work tree, on a refusal or a bad action, 1 on a failure. Never raises."""
    try:
        if action not in ("status", "install", "uninstall"):
            print(f"[provision] git-hook: unknown action {action!r} (use --install | --uninstall | --status)",
                  file=sys.stderr)
            return 2
        rep = _git_hook_report(target)
        if rep["state"] == "no-git":
            print(f"[provision] git-hook: {target} is not inside a git work tree (or git is not installed) — "
                  "nothing to do.", file=sys.stderr)
            return 2
        if rep["state"] == "error":
            print(f"[provision] git-hook: failed to read the hook ({rep.get('error')})", file=sys.stderr)
            return 1
        if action == "status":
            return _git_hook_status(rep)
        if action == "install":
            return _git_hook_install(rep)
        return _git_hook_uninstall(rep)
    except Exception as exc:  # noqa: BLE001
        print(f"[provision] git-hook: failed ({type(exc).__name__}: {exc})", file=sys.stderr)
        return 1


def _print_git_hook_hint(target) -> None:
    """setup's one-line OPT-IN hint for the post-commit auto-sync — inside a git work tree whose hook does not re-sync
    this project already: the opt-in tip (none where --install would refuse), or, for a stale block, how to update /
    remove it. setup never installs it. ROUND-9 K2: the printed command carries THIS project's sync root (a bare
    `git-hook --install` followed from the repo top resolved the top — J1a's toplevel sync into a sub-project's
    graph). Never raises."""
    try:
        rep = _git_hook_report(target)
        state = rep.get("state")
        if state in ("no-git", "current", "error"):
            return
        root = rep.get("root") or target
        if state == "stale":
            blocked = bool(rep.get("refusal") or rep.get("root_problem"))
            print(f"[provision] tip: this repo's DeepMap post-commit hook is out of date ({rep['reasons'][0]}) — "
                  f"{_git_hook_cmd('uninstall' if blocked else 'install', root)} {'removes' if blocked else 'updates'} "
                  "it.")
            return
        if rep.get("refusal") or rep.get("root_problem"):
            return
        fix = (f" First point DEEPMAP_PYTHON at a working Python interpreter ({rep['py_problem']})."
               if rep.get("py_problem") else "")
        print(f"[provision] tip (opt-in): keep this graph current on every commit — {_git_hook_cmd('install', root)} "
              f"adds a chained post-commit sync of this project (never installed automatically).{fix}")
    except Exception:
        pass


def heal(target: str = ".", *, password: str | None = None, report: dict | None = None) -> bool:
    """Lightweight recovery (P2): bring an ALREADY-PROVISIONED DeepMap back up after a reboot —
    ensure Neo4j is running and (re)start the MCP servers, WITHOUT a full re-sync. Idempotent
    (Neo4j-already-up + ports-already-listening both short-circuit), so it is safe to call on every
    session start via the SessionStart hook. Returns True iff Neo4j is reachable AND both MCP
    servers are up. Unlike `setup`, it never re-extracts the graph.

    Port-aware but LOOKUP-only: heal resolves THIS project's already-allocated (search, embed) MCP pair
    AND its (bolt, http) Neo4j instance from the registry, never CLAIMS new ports — allocating on every
    session start could grab a different pair than the .mcp.json Claude Code already connects to. It
    revives the project's OWN Neo4j instance (its data dir survives a reboot; a true no-re-sync revive).

    It never starts anything it cannot stand behind (2026-09 live validation): no route to a Neo4j of
    this project's own (no registry entry while 7687 is a foreign program or another project's instance,
    #66/#46; an unreadable registry) -> nothing is started; Neo4j unreachable (down / wrong password) ->
    the MCP servers are NOT started (they would only serve errors, #65); on the LEGACY 7687 route the
    base Neo4j is never started (its owner can't be read while it is down) and an already-running one is
    served only when its graph's owner is this project (D1); no store -> never provisioned from
    scratch. A running server of this project's with a stale Neo4j config is restarted (#69). `report`
    (optional dict) receives neo4j_ok / mcp_ok / reason / uri / mcp_started — main() persists it as the
    heal status file the SessionStart notify hook reads (see write_heal_status).

    ROUND-5 G2 — a POLICY refusal is marked `refused` + `refusal_kind` in the report: "foreign-base-claim"
    (this project's base claim routes to a store that is provably ANOTHER project's — by its owner record, or
    by the live owner heal read) or "legacy-owner" (the legacy 7687 route's live graph is not this project's).
    Such a refusal is final until a policy re-check (setup / the next heal), never "superseded" by a port
    probe; and heal also stops this project's own MCP servers that serve the refused store (owner-verified
    only — _stop_own_servers_serving; `mcp_stopped` / `mcp_unverified` say what it did / could not do). Every
    other failure (down, unreachable, credentials, a failed build, no route) is not a refusal, and its reason
    never uses refusal phrasing (ROUND-6 H4).

    ROUND-6: a project routed nowhere only because ANOTHER project's registry entry claims the base pair has the
    LIVE base's owner read first (H2 — _neo4j_lookup_route): when it is this project's, the store is served as
    its legacy route (the owner check below still decides) without a rebuild. A verified legacy route RECORDS its
    owner next to the store (H3a — _legacy_route_verdict), so a later setup that finds the store down after a
    reboot keeps it.

    Which running MCP servers heal restarts (ROUND-8 J5, ROUND-9 K3/K4, ROUND-10 L1-L4, L6 — start_mcp_server): only
    THIS project's own servers, and only onto code that SUPERSEDES theirs (_code_restart_verdict: an older version, a
    removed plugin dir, or their own plugin dir updated in place after they started — never a downgrade, never an equal
    version from another plugin dir that still exists). A server with an owner record naming the pid file's pid is
    judged by that record; one with NO usable owner record — none (every server a v0.27.4 build started), or one naming
    another pid (an earlier process's) — is signalled only on the K3 evidence: its port is REGISTERED to this project,
    its pid file names a live pid whose exact argv / command line runs this server's script from that code, the
    process is older than its pid file, and nothing (its environment, another port's records) says it is another
    port's or project's; the stop goes through stop_mcp_server's own checks, and a server whose record can't vouch for
    the current Neo4j config is replaced only when that config connects (L2). Any other server with no owner record
    is never signalled by heal: setup — the deliberate verb — restarts those on this project's registered ports (H1).

    ROUND-7 I4/H1: when setup left a persisted MCP blocker (a listener on this project's port it could not restart or
    attribute to this project's current servers — setup-blocked-<key>.json), heal honours it: while that port is still
    held by something that is not provably this project's current server, heal reports mcp_ok=false, `refused` +
    refusal_kind "mcp-blocked" and the held port(s) in mcp_unverified (final for the hooks — a listening port is the
    problem, not proof of health) and never signals it. A port that was FREE when heal ran (the held listener is gone;
    heal starts a fresh server) or a listener now provably this project's lifts — and clears — the blocker."""
    report = report if report is not None else {}
    password = password or os.environ.get("NEO4J_PASSWORD", DEFAULT_PASSWORD)
    user = os.environ.get("NEO4J_USER", DEFAULT_USER)
    # inject THIS project's ports + NEO4J_URI first (lookup-only)
    _, neo_problem, mcp_problem = _resolve_ports(target, allocate=False)
    uri = os.environ.get("NEO4J_URI", DEFAULT_URI)   # injection-first: read AFTER the seam
    report.update({"uri": uri, "neo4j_ok": False, "mcp_ok": False, "reason": None, "mcp_started": [],
                   "refused": False, "refusal_kind": None, "mcp_stopped": [], "mcp_unverified": []})
    problem = neo_problem or mcp_problem or _failed_build_reason(target)
    if problem:
        report["reason"] = problem
        print(f"[provision] heal: {problem} — nothing was started.", file=sys.stderr)
        return False
    if neo4j_route_source() == "legacy-unverified":
        # D1: no instance of its own -> the base 7687 is served only when it is ALREADY running and its graph
        # is provably this project's. It is never STARTED here: while it is down its owner can't be read, and
        # starting (and re-configuring) it revived whichever project's ~1 GB store it is — undoing a
        # hibernate on every session — only to refuse it (2026-09 review).
        state = _bolt_state(uri, user, password)
        if state == "auth":
            report["reason"] = _auth_failed_reason(uri)
            print(f"[provision] heal: {_auth_failed_text(uri)} — nothing was started.", file=sys.stderr)
            return False
        if state != "up":
            report["reason"] = ("this project has no DeepMap Neo4j instance of its own, and the shared legacy "
                                f"Neo4j on {uri} is not running — heal never starts a store whose owner it can't "
                                "read: run /deep-map:setup to give this project its own instance")
            print(f"[provision] heal: {report['reason']} — nothing was started.", file=sys.stderr)
            return False
        state, why = _legacy_route_verdict(target, uri, user, password)
        if state == "refused":
            # a POLICY refusal (ROUND-5 G2): the live graph is another project's / unowned-but-populated — final
            # until a policy re-check (a port probe can't disprove it); this project's own servers on it stop
            done = _refuse_heal(target, report, "legacy-owner", why, uri)
            print(f"[provision] heal: {why} — the MCP servers were NOT started{done}.", file=sys.stderr)
            return False
        if state != "ok":
            report["reason"] = why
            print(f"[provision] heal: {why} — the MCP servers were NOT started.", file=sys.stderr)
            return False
        report["neo4j_ok"] = neo_ok = True
    else:
        inst = resolved_instance(target)
        foreign = _foreign_base_claim_problem(target, uri)
        if foreign:
            # F2: a base claim whose store's owner record names another project (written by the <= v0.27.4
            # mis-adoption) — reviving it would serve that project's graph as this one's. setup re-verifies
            # the claim and gives this project its own instance. A POLICY refusal (ROUND-5 G2).
            done = _refuse_heal(target, report, "foreign-base-claim", foreign, uri)
            print(f"[provision] heal: {foreign} — nothing was started{done}.", file=sys.stderr)
            return False
        # F2, a base claim with NO owner record naming this project yet (a pre-upgrade claim): its owner can
        # only be read once the store runs — read it right after it is up, before any server is started.
        live_check = _is_base_claim_route(target, uri) and not _base_owner_is(target)
        was_up = live_check and _bolt_state(uri, user, password) == "up"
        diag: dict = {}
        neo_ok = ensure_neo4j(uri=uri, user=user, password=password,
                              project_key=(inst[0] if inst else None),
                              bolt=(inst[1] if inst else None), http=(inst[2] if inst else None),
                              create=False, diag=diag)
        report["neo4j_ok"] = bool(neo_ok)
        if not neo_ok:
            reason = diag.get("reason") or f"this project's Neo4j ({uri}) is not reachable"
            report["reason"] = (reason if "/deep-map:" in reason
                                else f"{reason} — run /deep-map:setup")
            print("[provision] heal: this project's Neo4j is not usable (see above) — the MCP servers were NOT "
                  "started; run /deep-map:setup", file=sys.stderr)
            return False
        other = _foreign_base_owner(target, user, password) if live_check else None
        if other:
            # the store this claim routes to holds ANOTHER project's graph: serving it would answer that
            # project's graph as this one's. setup releases the claim; heal only refuses — and stops the store
            # again when it started it just to read the owner (leave it as found; the owner is now recorded, so
            # the next heal refuses from the file without starting anything).
            why = _foreign_claim_text(other, uri, "the owner its live graph records")
            if not was_up:
                stop_neo4j(project_key=None)
            done = _refuse_heal(target, report, "foreign-base-claim", why, uri)   # a POLICY refusal (ROUND-5 G2)
            print(f"[provision] heal: {why} — the MCP servers were NOT started"
                  + ("" if was_up else "; the store heal started was stopped again") + f"{done}.", file=sys.stderr)
            return False
    # ROUND-7 I4/H1: setup's persisted MCP blocker — whether each blocked port is held BEFORE this start decides if
    # the listener setup could not stop is still there (a free port means it is gone: the start runs a fresh server)
    held_before = None
    if _read_setup_blocker(target):
        host = mcp_host()
        held_before = {n: is_http_up(host, mcp_port(n)) for n in MCP_SERVERS}
    del _MCP_STARTED[:]
    _MCP_CODE_RESTARTED.clear()
    mcp_ok = start_mcp_servers(password=password, target=target)
    report["mcp_ok"] = bool(mcp_ok)
    report["mcp_started"] = list(_MCP_STARTED)
    report["mcp_code_restarted"] = {n: v for n, v in _MCP_CODE_RESTARTED.items() if n in _MCP_STARTED}
    if held_before is not None:
        blk, still = _setup_blocker_still_holds(target, held_before, password)
        if still:
            # FINAL until setup succeeds or the held listener is gone (refused + refusal_kind: neither the old nor the
            # new hooks let a listening port "supersede" it — the port answering is exactly the problem); heal never
            # signals that process
            ports = [int(mcp_port(n)) for n in still]
            why = " ".join(str((blk or {}).get("reason") or "").split())
            if not why or not any(str(p) in why for p in ports):
                why = (f"this project's MCP port(s) {'/'.join(map(str, ports))} are held by a server setup could not "
                       "restart or attribute to this project" + (f" ({why})" if why else ""))
            if "/deep-map:setup" not in why:
                why += " — stop that process by hand, then re-run /deep-map:setup"
            report.update({"mcp_ok": False, "refused": True, "refusal_kind": _BLOCKED_KIND,
                           "mcp_unverified": ports, "reason": why})
            print(f"[provision] heal: setup's MCP blocker still holds — {why}. deepmap_search / deepmap_impact on "
                  f"port(s) {'/'.join(map(str, ports))} must not be used until then.", file=sys.stderr)
            return False
    # Only CREATE a MISSING .mcp.json (e.g. an older setup that predates per-project files) — never
    # overwrite an existing one: that file is the ports Claude Code already connects to, and heal
    # revived on exactly those (apply_ports_for fell back to parsing it).
    if mcp_ok:
        _ensure_mcp_json(target)
    else:
        report["reason"] = (f"an MCP server did not come up (see the logs in {deepmap_home()}) — "
                            "run /deep-map:setup")
    return bool(neo_ok and mcp_ok)


def _failed_build_reason(target) -> str | None:
    """A reason when this project's last recorded graph build FAILED / holds 0 code nodes — its setup
    never produced a graph, so reviving ~1 GB of processes for tools that can only return [] is wrong
    (2026-09 #19: the SessionStart heal revived a FAILED setup every session). Reads the
    .deepmap/graph_build.json marker sync writes (at the target and at its git toplevel). No manifest, a
    garbage one, or one without a verdict -> None (no evidence: heal proceeds as before). Never raises."""
    try:
        import port_registry as PR
        roots = {Path(target).resolve(), Path(PR.project_identity(target))}
    except Exception:
        roots = {Path(target)}
    for root in roots:
        try:
            m = json.loads((root / ".deepmap" / "graph_build.json").read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(m, dict) and (str(m.get("verdict") or "").upper() == "FAILED" or m.get("nodes") == 0):
            return ("this project's last setup/sync FAILED (0 code nodes in its graph) — heal only revives a "
                    "working graph; run /deep-map:setup")
    return None


def heal_status_path(target) -> Path:
    """<DEEPMAP_HOME>/run/heal-<project_key>.json — the heal status file for `target` (key =
    port_registry.project_key, the git-toplevel identity; subdirs of one repo share it)."""
    import port_registry as PR
    return deepmap_home() / "run" / f"heal-{PR.project_key(target)}.json"


def write_heal_status(target, report: dict) -> None:
    """Persist the outcome of a heal run — the SessionStart hooks' only window into a heal they spawn
    DETACHED with output discarded (#65: an unreachable Neo4j used to leave no trace, and the notify hook
    kept saying READY). Written on EVERY heal run, atomically, as UTF-8 JSON:
        {"ts": "<ISO-8601 UTC, YYYY-MM-DDTHH:MM:SSZ>", "ts_epoch": <float seconds>,
         "neo4j_ok": bool, "mcp_ok": bool, "reason": <one line naming the fix> | null,
         "mcp_started": [MCP servers a heal (re)started — a Claude Code session that was already running
                         must reconnect to them via /mcp], "mcp_started_epoch": <float> | null,
         "mcp_code_restarted": {server name: the OLDER code heal restarted it from, e.g. "0.27.4 from <root>"}
                         (ROUND-9 K3 — a subset of mcp_started: those restarts were a plugin update's),
         "refused": bool, "refusal_kind": "foreign-base-claim" | "legacy-owner" | "mcp-blocked" | null,
         "mcp_stopped": [this project's servers heal stopped because they served the refused store],
         "mcp_unverified": [this project's MCP ports still answered by a DeepMap server heal could not verify],
         "uri": <bolt URI heal resolved>, "project_key": str, "root": str}
    `refused` (ROUND-5 G2) marks a POLICY refusal (see heal): the hooks never let a port probe supersede it.
    `refusal_kind` is written only with `refused` and only as one of _REFUSAL_KINDS or "mcp-blocked" (ROUND-7:
    setup's persisted MCP blocker still holds — the listener on this project's port is not its server), else null.
    Nothing of a refusal is carried forward: every heal writes its own verdict.
    `mcp_started` (with `mcp_code_restarted`) is CARRIED FORWARD while the servers stay up (mcp_ok) with the epoch of
    that start, so the concurrent heals of several SessionStarts (the later ones only find "already listening") never
    erase the fact that the servers were just started. Best-effort; never raises."""
    lock = None
    try:
        import port_registry as PR
        path = heal_status_path(target)
        now = time.time()
        refused = report.get("refused") is True
        kind = (report.get("refusal_kind") if refused and report.get("refusal_kind") in _REFUSAL_KINDS + (_BLOCKED_KIND,)
                else None)
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)), "ts_epoch": now,
               "neo4j_ok": bool(report.get("neo4j_ok")), "mcp_ok": bool(report.get("mcp_ok")),
               "reason": report.get("reason"), "mcp_started": list(report.get("mcp_started") or []),
               "mcp_started_epoch": now if report.get("mcp_started") else None,
               "mcp_code_restarted": {str(k): str(v) for k, v in (report.get("mcp_code_restarted") or {}).items()},
               "refused": refused, "refusal_kind": kind,
               "mcp_stopped": [str(n) for n in (report.get("mcp_stopped") or [])],
               "mcp_unverified": [int(p) for p in (report.get("mcp_unverified") or []) if str(p).isdigit()],
               "uri": report.get("uri"), "project_key": PR.project_key(target),
               "root": PR.project_identity(target).replace("\\", "/")}
        path.parent.mkdir(parents=True, exist_ok=True)
        lock, _how = _file_lock(path.with_suffix(".lock"), wait_s=10)
        if rec["mcp_ok"] and not rec["mcp_started"]:
            try:
                prev = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(prev, dict) and prev.get("mcp_ok") and prev.get("mcp_started"):
                    rec["mcp_started"] = list(prev["mcp_started"])
                    rec["mcp_started_epoch"] = prev.get("mcp_started_epoch")
                    if isinstance(prev.get("mcp_code_restarted"), dict):
                        rec["mcp_code_restarted"] = {str(k): str(v) for k, v in prev["mcp_code_restarted"].items()}
            except Exception:
                pass
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".heal-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                json.dump(rec, f)
            os.replace(tmp, path)
            tmp = None
        finally:
            if tmp is not None:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
    except Exception:
        pass
    finally:
        _file_unlock(lock)


def neo4j_edition(driver) -> str | None:
    """The Neo4j edition ('community' / 'enterprise'), or None if unknown/unreachable. Read-only —
    used ONLY for the honest isolation line in status/doctor; it never changes graph behaviour. This
    is why a per-project DATABASE isn't offered: the provisioner ships Community, which can't
    CREATE DATABASE (Enterprise-only). Never raises."""
    try:
        with driver.session() as s:
            row = s.run("CALL dbms.components() YIELD edition RETURN edition LIMIT 1").single()
        return str(row["edition"]).lower() if row and row.get("edition") else None
    except Exception:
        return None


def isolation_note(edition, stats, *, escape_hatch: bool = False, uri_refused: bool = False) -> str:
    """The HONEST isolation summary. With per-project Neo4j INSTANCES the graph is now ISOLATED — each
    project runs its OWN instance (own data dir + bolt port, routed via NEO4J_URI + .mcp.json). TRI-STATE
    so it stays honest when several projects still converge on ONE instance (the operator escape hatch,
    or not-yet-migrated projects):

      * distinct instances                -> isolation=TRUE (per-project instances)
      * >=2 projects share one bolt URI    -> WARNING naming the convergence (they still share a graph)
      * operator NEO4J_URI override        -> isolation is operator-managed (single/shared/BYO)
      * that NEO4J_URI REFUSED (uri_refused: port_registry.neo4j_uri_problem — the Neo4j Browser's http://
        address, ...) -> "isolation: none": this run is routed to no Neo4j at all, so no override is in force
        (the caller prints the refusal itself, above this line)

    `stats` is registry_stats() (dict) or a bare project count (int, legacy). Pure; unit-testable.

    The escape hatch is PER INVOCATION (NEO4J_URI in this process's env): it never implies that other
    registered projects share that endpoint — those with an instance of their own keep it (#72). A row
    with NO instance (an MCP-only entry) converges on 7687 only when no registered project owns 7687;
    otherwise it has no route at all (see _neo4j_unregistered_route) and shares nothing (#77)."""
    ed = (edition or "unknown").lower()
    if isinstance(stats, dict):
        rows = [r for r in (stats.get("projects") or []) if isinstance(r, dict)]
        n = stats.get("n_projects", len(rows))
        # compared by ENDPOINT (J4): bolt://localhost:7691 (an older build's record) and bolt://127.0.0.1:7691 are
        # one instance holding two projects, not two instances
        uris = [_canon_uri(r["neo4j_uri"]) for r in rows if r.get("neo4j_uri")]
        unallocated = n - len(uris) if rows else n
        base = _bolt_uri(7687)
        if base not in uris:
            uris += [base] * unallocated   # legacy fallback: they share the base store
    else:
        n = int(stats or 0)
        uris, unallocated = [], n
    if uri_refused:
        own = n - unallocated
        note = "isolation: none — the NEO4J_URI set for this run is refused (see above), so this run reaches no Neo4j"
        if own > 0:
            note += (f"; the {own} registered project(s) with an instance of their own keep it whenever "
                     "run without NEO4J_URI")
        return note
    if escape_hatch:
        own = n - unallocated
        note = ("isolation: OPERATOR-MANAGED — this invocation's explicit NEO4J_URI overrides DeepMap's "
                "per-project routing (BYO/Aura/shared endpoint)")
        if own > 0:
            note += (f"; the {own} registered project(s) with an instance of their own keep it whenever "
                     "run without NEO4J_URI")
        note += f"; edition={ed}"
        if unallocated > 1:
            note += (f"\n  NOTE: {unallocated} registered projects have no instance of their own and share "
                     "whatever NEO4J_URI they are run with — the :_DeepMapMeta owner guard keeps them from "
                     "mixing (one project's graph at a time).")
        return note
    note = ("isolation: MCP servers/ports AND the Neo4j graph are per-project — each project runs its "
            "OWN Neo4j instance (own data dir + bolt port, routed via NEO4J_URI + .mcp.json)")
    if "enterprise" in ed:
        note += " (a per-project Enterprise database would also work; this build uses per-project instances)"
    elif "community" in ed:
        note += " (Community can't host a per-project database, so DeepMap runs a per-project instance)"
    note += f"; edition={ed}"
    converged = sorted(u for u in set(uris) if uris.count(u) > 1)
    if converged:
        note += (f"\n  WARNING: {len(converged)} Neo4j instance(s) still hold >1 project "
                 f"({', '.join(converged)}) — those projects share a graph (only the last-synced is "
                 "live). Re-provision them so each gets its own instance (docs/known-limitations.md, "
                 "\"Per-project isolation\").")
    return note


def _fmt_bytes(n) -> str:
    """A compact human size (e.g. 12.3 MB). Never raises."""
    try:
        n = float(n)
    except Exception:
        return "?"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def instance_table(env=None) -> list:
    """Per-project instance visibility for doctor/status: one row per registered project with its bolt
    URI, running/hibernated/absent state, bounded data-dir size, and last_used. The EXPENSIVE half
    (pid probe + data-dir du) lives here, NOT in port_registry.registry_stats (which is on the
    allocation hot path). A project with NO Neo4j allocation (an MCP-only entry — mcp-start before
    setup, or an instance-rm'd project) is shown as "unallocated" — never as the legacy 7687 store's
    state + size, which is not its store (#77). Never raises."""
    rows = []
    try:
        import port_registry as PR
        for r in (PR.registry_stats(env).get("projects") or []):
            try:
                if not isinstance(r, dict):
                    continue
                bolt = r.get("bolt_port")
                key = r.get("key")
                if bolt is None:
                    rows.append({"key": key, "root": r.get("root"), "neo4j_uri": "(no instance)",
                                 "state": "unallocated", "data_bytes": 0, "truncated": False,
                                 "last_used": r.get("last_used")})
                    continue
                # the base pair -> the legacy/base store (paths(None, None))
                pk_paths = None if (bolt, r.get("http_port")) == (PR.BASE_BOLT, PR.BASE_HTTP) else key
                running = _instance_running_for(pk_paths)
                data_dir = paths(None, pk_paths)["data"]
                size, truncated = _dir_size_bounded(data_dir)
                if running:
                    state = "running"
                elif data_dir.exists():
                    # T10d-W: "hibernated" only when hibernate stopped it (its marker); otherwise merely "stopped"
                    state = "hibernated" if _hibernation_marker(pk_paths).exists() else "stopped"
                else:
                    state = "absent"
                rows.append({"key": key, "root": r.get("root"),
                             "neo4j_uri": r.get("neo4j_uri") or _bolt_uri(bolt),
                             "state": state, "data_bytes": size, "truncated": truncated,
                             "last_used": r.get("last_used"),
                             "neo4j_version": (store_neo4j_version(pk_paths) if PR.neo4j_store_present(data_dir)
                                               else None)})
            except Exception:
                continue
    except Exception:
        pass
    return rows


def format_instance_table(rows) -> str:
    """Render instance_table() rows as aligned lines (pure; unit-testable). "" when there are no rows."""
    try:
        rows = [r for r in (rows or []) if isinstance(r, dict)]
        if not rows:
            return ""
        lines = ["instances (per-project Neo4j):"]
        for r in rows:
            uri = str(r.get("neo4j_uri") or "?")
            state = str(r.get("state") or "?")
            size = _fmt_bytes(r.get("data_bytes")) + ("+" if r.get("truncated") else "")
            last = r.get("last_used") or "never"
            root = str(r.get("root") or "?")
            ver = f"  neo4j={r['neo4j_version']}" if r.get("neo4j_version") else ""
            lines.append(f"  {uri:24s} {state:10s} data={size:>9s}  last_used={last}{ver}  {root}")
        return "\n".join(lines)
    except Exception:
        return ""


def print_instance_table(env=None) -> None:
    """Best-effort printer used by both status surfaces. Prints nothing when no projects are registered."""
    try:
        txt = format_instance_table(instance_table(env))
        if txt:
            print("  " + txt.replace("\n", "\n  "))
    except Exception:
        pass


STATUS_UNAVAILABLE_RC = 3   # the CLI's exit code for "Neo4j unavailable" (deepmap_cli main), shared here


class _LegacyRefused(Exception):
    """cmd_status: the legacy 7687 graph is not provably this project's (D1) — no census."""


def _neo4j_uri_refused() -> bool:
    """Is this run's operator NEO4J_URI (the escape hatch) one DeepMap refuses to dial — the same
    port_registry.neo4j_uri_problem call _apply_neo4j_env makes on the value as set (which it leaves in the env)?
    Then neo4j_route_problem() is that refusal and the isolation line must not call the route operator-managed.
    Never raises (-> False)."""
    try:
        if not _USER_SET_NEO4J_URI:
            return False
        import port_registry as PR
        return bool(PR.neo4j_uri_problem(os.environ.get("NEO4J_URI")))
    except Exception:
        return False


def _print_isolation(edition) -> None:
    """The isolation line + per-project instance table (both status surfaces). Best-effort."""
    try:
        import port_registry as PR
        print("  " + isolation_note(edition, PR.registry_stats(), escape_hatch=_USER_SET_NEO4J_URI,
                                    uri_refused=_neo4j_uri_refused()))
        print_instance_table()
    except Exception:
        pass


def cmd_status(uri: str, user: str, password: str, *, legacy_root=None, project_key: str | None = None,
               root=None) -> int:
    """Graph + index + embedding census of THIS project's Neo4j. A down / hibernated instance or a
    rejected password is a friendly one-liner naming the fix + the isolation line + the instance table
    (which shows the instance's state) and exit STATUS_UNAVAILABLE_RC — never a raw driver traceback
    (#12/#22: the command the CLI's own error message recommends used to crash). `legacy_root`: `uri` is
    the LEGACY 7687 route of that project (no instance of its own — D1), so the graph is censused only
    when its :_DeepMapMeta owner is that project (or it is unowned + empty); otherwise the refusal is
    printed (exit STATUS_UNAVAILABLE_RC). `project_key` words the auth remedy for a per-project instance."""
    refused = None
    try:
        drv = _make_driver(uri, user, password)
        try:
            drv.verify_connectivity()
            if legacy_root is not None:
                ok, refused = _verify_legacy_owner(drv, legacy_root, uri)
                if ok:
                    refused = None
                else:
                    refused = refused or f"{uri} holds a graph that can't be shown to be this project's"
                    raise _LegacyRefused()
            with drv.session() as s:
                # Exclude the :_DeepMapMeta ownership singleton from the node census (bookkeeping, not code).
                nodes = s.run("MATCH (n) WHERE NOT n:_DeepMapMeta RETURN count(n) AS c").single()["c"]
                edges = s.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]
                embedded = s.run("MATCH (n) WHERE n.embedding IS NOT NULL RETURN count(n) AS c").single()["c"]
                idx = list(s.run("SHOW INDEXES YIELD name, type, state WHERE name STARTS WITH 'deepmap_' "
                                 "RETURN name, type, state ORDER BY type, name"))
            edition = neo4j_edition(drv)
        finally:
            drv.close()
    except Exception as exc:  # noqa: BLE001 — a status command must report, not crash
        if isinstance(exc, _LegacyRefused):
            why = refused   # never census another project's graph (D1) — it names /deep-map:setup
        elif _is_auth_error(exc):
            why = _auth_failed_text(uri, project_key, rate_limited=_is_rate_limited(exc))
        elif legacy_root is not None:
            # no instance of its own: heal never starts the shared legacy store (its owner can't be read while
            # it is down), so "revive it with heal" would not be runnable here (D6)
            why = (f"this project has no DeepMap Neo4j instance of its own, and the shared legacy Neo4j on {uri} "
                   f"is not reachable ({type(exc).__name__}) — run /deep-map:setup to give this project its own "
                   "instance")
        else:
            why = (f"this project's Neo4j ({uri}) is not reachable ({type(exc).__name__}) — it is stopped or "
                   f"hibernated (see the instance table below): run /deep-map:setup, or "
                   f"{_provision_cmd('heal')} to revive it without a re-sync")
        print(f"DeepMap status @ {uri}\n  {why}")
        _print_isolation(None)
        return STATUS_UNAVAILABLE_RC
    print(f"DeepMap status @ {uri}\n  nodes={nodes}  edges={edges}  embedded={embedded}")
    print(f"  indexes ({len(idx)}):")
    for r in idx:
        print(f"     {r['name']:32s} {r['type']:9s} {r['state']}")
    if project_key is not None or (not _USER_SET_NEO4J_URI and _is_local_base_uri(uri)):
        note = neo4j_upgrade_note(project_key, root=root)   # a newer patch release for this store (file reads only)
        if note:
            print(f"  {note}")
    _print_isolation(edition)
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    for s in (sys.stdout, sys.stderr):
        try:
            # line-buffered: stdout + stderr stay in order under a pipe (the Bash tool / a hook) — the setup log
            # used to interleave its ERROR lines out of order
            s.reconfigure(encoding="utf-8", line_buffering=True)
        except Exception:
            pass
    # INJECTION-FIRST: every branch resolves its target, runs the seam (which injects this project's
    # NEO4J_URI + MCP ports), and ONLY THEN reads uri from the env — so a stale pre-branch uri can
    # never make a command act on the wrong project's graph. user/password have no per-project variant.
    cmd = argv[0] if argv else "setup"
    user = os.environ.get("NEO4J_USER", DEFAULT_USER)
    password = os.environ.get("NEO4J_PASSWORD", DEFAULT_PASSWORD)

    def _u() -> str:
        return os.environ.get("NEO4J_URI", DEFAULT_URI)   # read AFTER the seam has injected

    if cmd == "setup":
        target = argv[1] if len(argv) > 1 else "."
        if not setup(target, user=user, password=password):   # setup self-injects+resolves
            return 1
        return SETUP_GRAPH_KEPT_RC if _SETUP_GRAPH_KEPT.get("why") else 0   # M1: tools up over the PREVIOUS graph
    if cmd == "ensure":
        target = argv[1] if len(argv) > 1 else "."
        _, problem, _ = _resolve_ports(target, allocate=True)
        if problem:
            print(f"[provision] ERROR: {problem}", file=sys.stderr)
            return 1
        inst = resolved_instance(target)
        return 0 if ensure_neo4j(uri=_u(), user=user, password=password,
                                 project_key=(inst[0] if inst else None),
                                 bolt=(inst[1] if inst else None),
                                 http=(inst[2] if inst else None)) else 1
    if cmd == "status":
        target = argv[1] if len(argv) > 1 else "."
        _, problem, _ = _resolve_ports(target, allocate=False)
        problem = problem or _foreign_base_claim_problem(target, _u(), live=True, user=user,
                                                         password=password)   # F2: a mis-adopted base claim
        if problem:   # no instance of its own: never census another project's graph (#46)
            print(f"DeepMap status: {problem}")
            _print_isolation(None)
            return STATUS_UNAVAILABLE_RC
        inst = resolved_instance(target)
        return cmd_status(_u(), user, password,
                          legacy_root=(target if neo4j_route_source() == "legacy-unverified" else None),
                          project_key=(inst[0] if inst else None), root=target)
    if cmd == "stop":
        target = argv[1] if len(argv) > 1 else "."
        _, problem, _ = _resolve_ports(target, allocate=False)
        if problem:   # never stop an instance this project does not own (#47)
            print(f"[provision] {problem} — nothing to stop.")
            return 1
        if _USER_SET_NEO4J_URI:
            # operator escape hatch: stop the DeepMap instance NEO4J_URI names — never the base store just
            # because some other endpoint was given, and nothing DeepMap does not run.
            managed, key = _escape_hatch_instance(_u())
            if not managed:
                print(f"[provision] NEO4J_URI={_u()} is not a DeepMap-managed local instance — nothing to stop "
                      "(stop that Neo4j where it runs).")
                return 1
            return _stop_rc(stop_neo4j(project_key=key), _u())
        foreign = _foreign_base_claim_problem(target, _u(), live=True, user=user, password=password)
        if foreign:   # F2: never stop another project's store through a mis-adopted base claim
            print(f"[provision] not stopping the Neo4j at {_u()}: {foreign} — nothing stopped.")
            return 1
        if neo4j_route_source() == "legacy-unverified":
            # D1: the base 7687 is stopped for this project only when its graph is provably this project's.
            state, why = _legacy_route_verdict(target, _u(), user, password)
            if state != "ok":
                print(f"[provision] not stopping the Neo4j at {_u()}: {why} — nothing stopped.")
                return 1
        inst = resolved_instance(target)
        return _stop_rc(stop_neo4j(project_key=(inst[0] if inst else None)), _u())
    if cmd == "mcp-start":
        target = argv[1] if len(argv) > 1 else "."
        # Claim MCP ports; LOOKUP-only for Neo4j (allocation belongs to setup, which also STARTS the
        # instance — mcp-start must never claim a bolt port with no instance behind it, e.g. on a
        # benchmark clone). A never-setup project routes to 7687 only when 7687 is not another project's
        # instance / a foreign program AND its graph is provably this project's (D1) — otherwise it
        # refuses (never wire servers to another graph, #46).
        _, neo_problem, mcp_problem = _resolve_ports(target, allocate=True, allocate_neo4j=False)
        neo_problem = neo_problem or _foreign_base_claim_problem(
            target, _u(), live=True, user=user, password=password)   # F2: a mis-adopted base claim
        if neo_problem or mcp_problem:
            print(f"[provision] ERROR: MCP servers NOT started — {neo_problem or mcp_problem}", file=sys.stderr)
            return 1
        if neo4j_route_source() == "legacy-unverified":
            state, why = _legacy_route_verdict(target, _u(), user, password)
            if state != "ok":
                print(f"[provision] ERROR: MCP servers NOT started — {why}", file=sys.stderr)
                return 1
        held_before = None
        if _read_setup_blocker(target):   # ROUND-7 I4/H1: setup's persisted blocker (see heal)
            held_before = {n: is_http_up(mcp_host(), mcp_port(n)) for n in MCP_SERVERS}
        ok = start_mcp_servers(password=password, target=target)
        if ok and held_before is not None:
            _blk, still = _setup_blocker_still_holds(target, held_before, password)
            if still:
                ports = "/".join(str(mcp_port(n)) for n in still)
                print(f"[provision] ERROR: this project's MCP port(s) {ports} are still held by the server setup could "
                      "not restart or attribute to this project (see /deep-map:setup's earlier ERROR) — stop that "
                      "process by hand, then re-run /deep-map:setup.", file=sys.stderr)
                return 1
        if ok:
            # best-effort (exit 0 while the servers listen); verify_mcp prints WHY a check failed
            failed = [srv for srv in ("deepmap-search", "deepmap-embed") if not verify_mcp(srv)]
            if failed:
                print(f"[provision] WARNING: MCP verify did not pass for {', '.join(failed)} (servers are "
                      "listening; see above)", file=sys.stderr)
            _ensure_mcp_json(target)   # create if missing; never overwrite (setup is the configure step)
        _print_mcp_hint(target)
        return 0 if ok else 1
    if cmd == "mcp-stop":
        target = argv[1] if len(argv) > 1 else "."
        _, _, mcp_problem = _resolve_ports(target, allocate=False)   # THIS project's ports
        if mcp_problem:
            # no pair of its own: the default ports belong to another project (review #13) — a server
            # started before the owner sidecars existed has no other guard, so never touch them.
            print(f"[provision] {mcp_problem} — nothing to stop.")
            return 0
        return 0 if stop_mcp_servers(target=target) else 1
    if cmd == "mcp-status":
        target = argv[1] if len(argv) > 1 else "."
        apply_ports_for(target, allocate=False)  # show THIS project's ports
        if mcp_route_problem():
            print(f"[provision] NOTE: {mcp_route_problem()} (showing the default ports)")
        return mcp_status(target=target)
    if cmd == "heal":
        target = argv[1] if len(argv) > 1 else "."
        report: dict = {}
        try:
            ok = heal(target, password=password, report=report)
        except Exception as exc:  # noqa: BLE001 — the status file must still say what happened
            ok = False
            report["reason"] = f"heal crashed ({type(exc).__name__}: {exc}) — run /deep-map:setup"
            print(f"[provision] heal: {report['reason']}", file=sys.stderr)
        write_heal_status(target, report)
        return 0 if ok else 1
    if cmd == "hibernate":
        # LIFECYCLE/GC — deliberately NOT injection-first: hibernate must not allocate ports or write
        # .mcp.json (that would pollute the registry it is reclaiming). It reads the registry directly.
        target = argv[1] if len(argv) > 1 and not argv[1].startswith("-") else "."
        idle = DEFAULT_IDLE_MIN
        if "--idle-minutes" in argv:
            try:
                idle = int(argv[argv.index("--idle-minutes") + 1])
            except Exception:
                idle = DEFAULT_IDLE_MIN
        scope = "all" if "--all" in argv else "others"
        n = hibernate_idle(target, idle_minutes=idle, scope=scope)
        print(f"[provision] hibernated {n} idle instance(s)")
        return 0
    if cmd == "instance-rm":
        # LIFECYCLE/GC — NOT injection-first (same reason as hibernate).
        target = argv[1] if len(argv) > 1 and not argv[1].startswith("-") else "."
        key = None
        if "--key" in argv:
            try:
                key = argv[argv.index("--key") + 1]
            except Exception:
                key = None
        yes = ("--yes" in argv) or ("--force" in argv)
        purge = "--purge" in argv
        return instance_rm(target, key=key, yes=yes, purge=purge)
    if cmd == "git-hook":
        # OPT-IN post-commit auto-sync (ROUND-7 I3) — not injection-first: it touches no port, registry or Neo4j
        action = ("install" if "--install" in argv else "uninstall" if "--uninstall" in argv else "status")
        rest = [a for a in argv[1:] if not a.startswith("--")]
        return git_hook(rest[0] if rest else ".", action)
    if cmd == "neo4j-upgrade":
        return _neo4j_upgrade_cmd(argv[1:], user, password)
    print(f"unknown command: {cmd!r} (use setup|ensure|status|stop|mcp-start|mcp-stop|mcp-status|heal|hibernate|"
          f"instance-rm|git-hook|neo4j-upgrade)", file=sys.stderr)
    return 2


def _neo4j_upgrade_cmd(args: list, user: str, password: str) -> int:
    """`provision.py neo4j-upgrade [<dir>] [--to X.Y.Z] [--check]`: move THIS project's store to a newer Neo4j patch
    release (upgrade_neo4j; default DEFAULT_VERSION) — the explicit twin of what setup does by default. LOOKUP-only
    routing (never claims ports); the same refusals as `stop` (no route, a mis-adopted base claim, a legacy route whose
    graph is not provably this project's). `--check` only reports. Exit 0: upgraded / already current (or --check);
    1: not upgraded (refused, rolled back, or a rollback that needs /deep-map:setup — the line says which); 2: usage."""
    to, rest, i = None, [], 0
    while i < len(args):
        a = args[i]
        if a == "--to":
            if i + 1 >= len(args):
                print("[provision] neo4j-upgrade: --to needs a version (X.Y.Z)", file=sys.stderr)
                return 2
            to, i = args[i + 1], i + 2
            continue
        if a.startswith("--to="):
            to = a.split("=", 1)[1]
        elif a not in ("--check",):
            if a.startswith("-"):
                print(f"[provision] neo4j-upgrade: unknown option {a!r}", file=sys.stderr)
                return 2
            rest.append(a)
        i += 1
    target = rest[0] if rest else "."
    _, problem, _ = _resolve_ports(target, allocate=False)
    uri = os.environ.get("NEO4J_URI", DEFAULT_URI)
    if problem:
        print(f"[provision] {problem} — nothing upgraded.")
        return 1
    if _USER_SET_NEO4J_URI:
        print(f"[provision] NEO4J_URI={uri} is an operator-managed endpoint — DeepMap does not manage its Neo4j "
              "version; nothing upgraded.")
        return 1
    foreign = _foreign_base_claim_problem(target, uri, live=True, user=user, password=password)
    if foreign:
        print(f"[provision] not upgrading the Neo4j at {uri}: {foreign}")
        return 1
    if neo4j_route_source() == "legacy-unverified":
        state, why = _legacy_route_verdict(target, uri, user, password)
        if state != "ok":
            print(f"[provision] not upgrading the Neo4j at {uri}: {why}")
            return 1
    inst = resolved_instance(target)
    key = inst[0] if inst else None
    where = f"this project's instance ({key[:8]})" if key else "the base store"
    if "--check" in args:
        cur = store_neo4j_version(key)
        note = neo4j_upgrade_note(key, root=target)
        print(f"[provision] {where}: " + (note if note else f"Neo4j {cur} (the current default is {DEFAULT_VERSION}; "
                                                            "nothing to upgrade)"))
        return 0
    recovered = recover_neo4j_upgrade(target, inst=inst, uri=uri)
    if recovered:
        print(f"[provision] NOTE: {recovered}")
    rep: dict = {}
    out = upgrade_neo4j(target, uri=uri, user=user, password=password, inst=inst, to_version=to, report=rep)
    words = {"upgraded": f"{where} now runs Neo4j {rep.get('to')}",
             "current": f"{where} already runs Neo4j {rep.get('from')} — nothing to upgrade",
             "skipped": f"not upgraded ({rep.get('reason')}); {where} stays on Neo4j {rep.get('from')}",
             "rolled-back": f"the upgrade was ROLLED BACK ({rep.get('reason')})",
             "rollback-failed": f"the upgrade FAILED and was not fully rolled back: {rep.get('reason')}"}
    print(f"[provision] neo4j-upgrade: {words.get(out, out)}", file=sys.stdout if out in ("upgraded", "current")
          else sys.stderr)
    if (out in ("upgraded", "rolled-back", "rollback-failed") and not mcp_route_problem()
            and any(is_http_up(mcp_host(), mcp_port(n)) for n in MCP_SERVERS)
            and _bolt_state(uri, user, password) == "up"):
        # Neo4j was restarted under this project's running MCP servers: their pooled Bolt connections are dead, and
        # the first search would fail on one. Restart them (THIS project's own servers only — setup's restart rule).
        print("[provision] restarting this project's MCP servers so they reconnect to the restarted Neo4j")
        if not start_mcp_servers(password=password, restart_own=True, target=target):
            print("[provision] WARNING: the MCP servers did not all come back — run /deep-map:setup",
                  file=sys.stderr)
    return 0 if out in ("upgraded", "current") else 1


_STOP_PORT_WAIT_S = 20.0   # `stop`: how long to wait for the stopped instance's bolt port to close


def _stop_rc(ok: bool, uri) -> int:
    """`provision.py stop`'s exit code from stop_neo4j's result + _LAST_STOP_OUTCOME (T8d-X4): 0 when it stopped the
    instance (after waiting, bounded, for its bolt port to close), and 0 when there was provably NOTHING of this
    instance's to stop — no / a stale pid file and its bolt port free — exactly like mcp-stop's nothing-to-stop; 1
    for a refusal, a live process that could not be proven this instance's, a failed kill, or "nothing of ours"
    while the bolt port is still held (an unmanaged Neo4j / another program — say so). Never raises."""
    try:
        ep = _neo4j_endpoint(uri)
        port = ep[1] if ep and ep[0] == "localhost" else None
        if ok:
            deadline = time.monotonic() + _STOP_PORT_WAIT_S
            while port and _port_in_use("127.0.0.1", int(port)) and time.monotonic() < deadline:
                time.sleep(0.25)
            if port and _port_in_use("127.0.0.1", int(port)):
                print(f"[provision] NOTE: bolt {port} is still listening {int(_STOP_PORT_WAIT_S)}s after the stop — "
                      "the JVM may still be shutting down.", file=sys.stderr)
            return 0
        if _LAST_STOP_OUTCOME != "nothing":
            return 1
        if port and _port_in_use("127.0.0.1", int(port)):
            find = (f"netstat -ano | findstr :{port}" if is_windows() else f"lsof -iTCP:{port} -sTCP:LISTEN -P")
            print(f"[provision] nothing of this instance's to stop, but bolt {port} is still LISTENING — a Neo4j this "
                  f"project does not manage (another program, or a DeepMap JVM whose pid file was lost). Find it "
                  f"({find}) and stop it by hand if it is DeepMap's.", file=sys.stderr)
            return 1
        print("[provision] this project's Neo4j is not running — nothing to stop.")
        return 0
    except Exception:
        return 0 if ok else 1


def _bootstrap_checks():
    """deps_bootstrap.SetupChecks with provision's own rules (none exists twice): setup's census, preflight's Java rule
    (an operator NEO4J_URI needs no local Java), whether the shared Neo4j binary is still to be downloaded, the OS file
    lock and the Windows long-path probe."""
    import deps_bootstrap as DB
    byo = _USER_SET_NEO4J_URI
    return DB.SetupChecks(census=_census_verdict,
                          java_problems=(lambda: []) if byo else _java_preflight_problems,
                          neo4j_download_needed=lambda: not byo and not _shared_binary_present(),
                          file_lock=_file_lock, file_unlock=_file_unlock,
                          long_paths_enabled=_win_long_paths_enabled)


if __name__ == "__main__":
    import deps_bootstrap  # noqa: E402 — stdlib only: setup installs DeepMap's own environment; the verbs run under it
    _rc = deps_bootstrap.entry(Path(__file__).resolve(), sys.argv[1:], checks=_bootstrap_checks)
    raise SystemExit(main() if _rc is None else _rc)
