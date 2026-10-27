#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j gates for the provisioner's Neo4j-download SHA-256 verification (supply-chain hardening).

Locks `build/provision.py` checksum logic without any network or Neo4j:
  * expected_sha256 — precedence DEEPMAP_NEO4J_SHA256 override > baked KNOWN_SHA256 > None (unknown).
  * _sha256 — correct streaming digest.
  * verify_checksum — (True,None) when unpinned; (True,actual) on match; (False,actual) on mismatch
    (a pinned-but-mismatched download is a HARD failure — never used).
  * KNOWN_SHA256 — entries are 64-hex and keyed by the exact dist_filename for the default version.

Pure + hermetic: imports the module, hashes a tempfile; never downloads, never starts Neo4j.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
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


def _load_provision():
    spec = importlib.util.spec_from_file_location("dm_provision", BUILD / "provision.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# SHA-256 of b"hello" — a fixed, independently-verifiable vector.
HELLO_SHA256 = "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"


def gate_known_table() -> None:
    print("\n-- KNOWN_SHA256: 64-hex entries keyed by the exact dist_filename --")
    P = _load_provision()
    check("table is non-empty", len(P.KNOWN_SHA256) >= 2, str(len(P.KNOWN_SHA256)))
    for name, digest in P.KNOWN_SHA256.items():
        check(f"{name}: 64-hex SHA-256",
              len(digest) == 64 and all(c in "0123456789abcdef" for c in digest.lower()), digest)
    # The table keys must EQUAL what dist_filename emits for the default version — else a download is
    # never matched against its baked hash (silent loss of verification).
    for windows in (False, True):
        fn = P.dist_filename(P.DEFAULT_VERSION, windows)
        check(f"default-version dist_filename present in table ({'win' if windows else 'unix'}): {fn}",
              fn in P.KNOWN_SHA256, fn)


def gate_expected_sha256_precedence() -> None:
    print("\n-- expected_sha256: override > baked > None --")
    P = _load_provision()
    saved = os.environ.get("DEEPMAP_NEO4J_SHA256")
    try:
        os.environ.pop("DEEPMAP_NEO4J_SHA256", None)
        unix = P.expected_sha256(P.DEFAULT_VERSION, False)
        win = P.expected_sha256(P.DEFAULT_VERSION, True)
        check("baked unix hash returned", unix == P.KNOWN_SHA256[P.dist_filename(P.DEFAULT_VERSION, False)])
        check("baked windows hash returned", win == P.KNOWN_SHA256[P.dist_filename(P.DEFAULT_VERSION, True)])
        check("unknown version -> None (warn, not hard-fail)",
              P.expected_sha256("99.99.99", False) is None)

        valid = "ab" * 32  # a well-formed 64-hex override
        os.environ["DEEPMAP_NEO4J_SHA256"] = valid.upper() + "  "  # upper + whitespace
        check("valid override takes precedence (lowercased + stripped)",
              P.expected_sha256(P.DEFAULT_VERSION, False) == valid)
        check("valid override applies even to an unknown version",
              P.expected_sha256("99.99.99", True) == valid)

        # A MALFORMED override (not 64-hex) must NOT be honored as a pin — it is ignored (warn) and
        # we fall back to the baked table, so an env typo can't become a bogus "pin".
        os.environ["DEEPMAP_NEO4J_SHA256"] = "AbCdEf00"  # 8-hex, malformed
        check("malformed override is ignored -> falls back to baked hash",
              P.expected_sha256(P.DEFAULT_VERSION, False)
              == P.KNOWN_SHA256[P.dist_filename(P.DEFAULT_VERSION, False)])
        check("malformed override + unknown version -> None (not the bogus pin)",
              P.expected_sha256("99.99.99", True) is None)
    finally:
        if saved is None:
            os.environ.pop("DEEPMAP_NEO4J_SHA256", None)
        else:
            os.environ["DEEPMAP_NEO4J_SHA256"] = saved


def gate_sha256_and_verify() -> None:
    print("\n-- _sha256 + verify_checksum: match / mismatch / unpinned --")
    P = _load_provision()
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "blob.bin"
        f.write_bytes(b"hello")
        check("_sha256 matches the known 'hello' vector", P._sha256(f) == HELLO_SHA256, P._sha256(f))

        ok, actual = P.verify_checksum(f, None)
        check("no pinned hash -> (True, None) (unknown version warns, never blocks)",
              ok is True and actual is None)

        ok, actual = P.verify_checksum(f, HELLO_SHA256)
        check("matching hash -> ok True", ok is True and actual == HELLO_SHA256)

        ok, actual = P.verify_checksum(f, HELLO_SHA256.upper())
        check("hash compare is case-insensitive", ok is True)

        ok, actual = P.verify_checksum(f, "0" * 64)
        check("mismatched hash -> ok False (HARD integrity failure)",
              ok is False and actual == HELLO_SHA256)


class _FakeResp:
    """Minimal context-manager response: yields all bytes on the first read(), then EOF."""
    def __init__(self, data: bytes):
        self._data = data
        self._done = False

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n):
        if self._done:
            return b""
        self._done = True
        return self._data


def gate_download_orchestration() -> None:
    print("\n-- download_neo4j: end-to-end fail-loud on mismatch + tampered-cache re-download --")
    import hashlib as _h
    P = _load_provision()

    DATA = b"x" * 1_100_000           # > the 1 MB cache-size gate
    STALE = b"y" * 1_100_000          # different bytes => different hash (a 'tampered' cache)
    good = _h.sha256(DATA).hexdigest()
    bad = "0" * 64

    saved_urlopen = P.urllib.request.urlopen
    saved_archive_ok = P._archive_ok
    saved_env = os.environ.get("DEEPMAP_NEO4J_SHA256")
    calls = {"n": 0}

    def fake_urlopen(url, timeout=600):
        calls["n"] += 1
        return _FakeResp(DATA)

    try:
        P.urllib.request.urlopen = fake_urlopen       # no real network
        P._archive_ok = lambda p: True                # isolate the checksum path from zip validity
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)

            # (a) fresh download, pin MATCHES -> served, network hit once.
            os.environ["DEEPMAP_NEO4J_SHA256"] = good
            calls["n"] = 0
            a = td / "a.zip"
            out = P.download_neo4j("9.9.9", a)
            check("fresh download, matching pin -> returns dest", out == a and a.exists())
            check("fresh matching download hit the network once", calls["n"] == 1, str(calls["n"]))

            # (b) fresh download, pin MISMATCHES -> RAISES, .part cleaned, dest not created.
            os.environ["DEEPMAP_NEO4J_SHA256"] = bad
            b = td / "b.zip"
            raised = False
            try:
                P.download_neo4j("9.9.9", b)
            except RuntimeError:
                raised = True
            check("fresh download, mismatched pin -> RAISES (fail loud)", raised)
            check("mismatch leaves no .part artifact",
                  not b.with_suffix(b.suffix + ".part").exists())
            check("mismatch did not create the dest (never serves a bad archive)", not b.exists())

            # (c) cache hit, pin MATCHES -> served WITHOUT touching the network.
            os.environ["DEEPMAP_NEO4J_SHA256"] = good
            c = td / "c.zip"; c.write_bytes(DATA)
            calls["n"] = 0
            outc = P.download_neo4j("9.9.9", c)
            check("cache hit, matching pin -> served", outc == c)
            check("cache hit, matching pin -> NO network call", calls["n"] == 0, str(calls["n"]))

            # (d) TAMPERED cache (hash != pin) -> must NOT serve stale; re-downloads the good artifact.
            os.environ["DEEPMAP_NEO4J_SHA256"] = good
            d = td / "d.zip"; d.write_bytes(STALE)
            calls["n"] = 0
            outd = P.download_neo4j("9.9.9", d)
            check("tampered cache -> re-downloaded (network hit), not served stale", calls["n"] == 1, str(calls["n"]))
            check("tampered cache -> replaced with the good artifact", _h.sha256(d.read_bytes()).hexdigest() == good)
            check("tampered cache -> still returns dest after a clean re-download", outd == d)

            # (e) UNKNOWN version, NO pin -> provisions (structural check only), never hard-fails.
            os.environ.pop("DEEPMAP_NEO4J_SHA256", None)
            e = td / "e.zip"
            oute = P.download_neo4j("9.9.9", e)   # 9.9.9 not in KNOWN_SHA256
            check("unknown version, no pin -> provisions (structural only), no raise",
                  oute == e and e.exists())

            # (f) #1 — a mid-stream network/IO error must re-raise AND leave no orphan .part.
            class _BoomResp:
                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

                def read(self, n):
                    raise OSError("connection reset mid-stream")

            P.urllib.request.urlopen = lambda url, timeout=600: _BoomResp()
            os.environ["DEEPMAP_NEO4J_SHA256"] = good
            f = td / "boom.zip"
            boomed = False
            try:
                P.download_neo4j("9.9.9", f)
            except OSError:
                boomed = True
            check("mid-stream network error is re-raised (#1)", boomed)
            check("mid-stream failure leaves NO orphan .part (#1)",
                  not f.with_suffix(f.suffix + ".part").exists())
    finally:
        P.urllib.request.urlopen = saved_urlopen
        P._archive_ok = saved_archive_ok
        if saved_env is None:
            os.environ.pop("DEEPMAP_NEO4J_SHA256", None)
        else:
            os.environ["DEEPMAP_NEO4J_SHA256"] = saved_env


def gate_lifecycle() -> None:
    print("\n-- provisioner lifecycle: _pid_alive (#3) / stop-stale-pidfile (#3/#5) / extract sentinel (#2) --")
    P = _load_provision()

    # #3 — liveness probe.
    check("_pid_alive(this process) is True", P._pid_alive(os.getpid()) is True)
    check("_pid_alive(absurd pid) is False", P._pid_alive(2_000_000_000) is False)
    check("_pid_alive(0/-1) is False", P._pid_alive(0) is False and P._pid_alive(-1) is False)
    # #4 — _terminate_pid honours the platform result type (no exception on a dead pid path here;
    # liveness is the caller's gate). Just assert it returns a bool without raising for a dead pid.
    check("_terminate_pid returns a bool (rc-gated, no exception)",
          isinstance(P._terminate_pid(2_000_000_000, tree=False), bool))

    saved_home = os.environ.get("DEEPMAP_HOME")
    with tempfile.TemporaryDirectory() as td:
        os.environ["DEEPMAP_HOME"] = td
        try:
            p = P.paths()
            # #3/#5 — stop with a STALE (dead) pid file: must NOT signal, and must clear the file.
            p["pidfile"].parent.mkdir(parents=True, exist_ok=True)
            p["pidfile"].write_text("2000000000", encoding="utf-8")
            ok = P.stop_neo4j()
            check("stop_neo4j on a stale/dead pid -> False (no kill)", ok is False)
            check("stop_neo4j clears the stale pid file", not p["pidfile"].exists())

            # #2 — a COMPLETE install with no sentinel is ADOPTED (sentinel written), WITHOUT touching
            # the archive (we pass a non-existent archive: if it tried to extract, it would fail).
            nh = p["neo4j_home"]
            (nh / "bin").mkdir(parents=True, exist_ok=True)
            p["neo4j_bin"].write_text("#!/bin/sh\n", encoding="utf-8")
            (nh / "lib").mkdir(parents=True, exist_ok=True)
            (nh / "conf").mkdir(parents=True, exist_ok=True)
            sentinel = nh / ".deepmap-extracted"
            missing_archive = Path(td) / "does-not-exist.zip"
            out = P.extract_neo4j(missing_archive, P.neo4j_version())
            check("extract_neo4j ADOPTS a complete install + writes the sentinel (no re-extract) (#2)",
                  out == nh and sentinel.exists())
            out2 = P.extract_neo4j(missing_archive, P.neo4j_version())
            check("extract_neo4j is idempotent once the sentinel exists (#2)", out2 == nh)
        finally:
            if saved_home is None:
                os.environ.pop("DEEPMAP_HOME", None)
            else:
                os.environ["DEEPMAP_HOME"] = saved_home


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 64)
    print("DeepMap — provisioner Neo4j-download SHA-256 gates (no Neo4j, no network)")
    print("=" * 64)

    gate_known_table()
    gate_expected_sha256_precedence()
    gate_sha256_and_verify()
    gate_download_orchestration()
    gate_lifecycle()

    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
