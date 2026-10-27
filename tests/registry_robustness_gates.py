#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j, no-network gates for the port registry's IDENTITY + CONCURRENCY robustness
(build/port_registry.py), from the 2026-09 live validation:

  A1  project_identity decoded `git rev-parse --show-toplevel` in the LOCALE code page (text=True, no
      encoding). On a Windows box whose ANSI code page is not UTF-8 (cp1255 / cp1252 / ...), a project
      under a non-ASCII path got a garbled or fallback identity -> a different key -> a registry miss ->
      CLI / doctor / status / heal routed to bolt 7687 / MCP 8787 (another project's instance). Locked:
        * a REAL child interpreter with UTF-8 mode OFF (-X utf8=0) computes the SAME key as one with it
          ON, from the repo root AND from a subdirectory, and prints no UnicodeDecodeError;
        * a deterministic simulation of a cp1252 locale (fake subprocess.run applying the real text-mode
          decoding rules) yields the correct identity on every platform — for BOTH a Windows- and a
          POSIX-shaped toplevel on every OS (the round-2 POSIX fixture spelled "josé" while asserting
          "José", so ubuntu CI was RED regardless of the product code) — and legacy_identities
          reproduces the pre-fix (cp1252) identity the ROUND-3 E1 key migration looks for.
  A3  load_registry() returned None on a TRANSIENT read error (a Windows sharing violation while
      _write_registry os.replace()s the file) -> neo4j_uri_for silently fell back to 7687 (3/45 in the
      field repro); and the WRITER's os.replace failed the same way and was swallowed (a lost write).
      Locked: transient read / replace errors are retried; a registry that EXISTS but stays unreadable
      or unrecognized makes neo4j_uri_for RAISE RegistryUnreadableError (never a silent 7687); an
      absent registry still resolves to the frozen 7687 default; a live writer-vs-reader race resolves
      to the registered port every time (Windows).
  A4  _allocate_pair returned the BASE pair on a registry-lock timeout -> setup wired a new project to
      ANOTHER project's servers. Locked: a lock timeout (and scan exhaustion, and a failed registry
      write) returns None — never the base pair — so callers can fail loud; the uncontended path, the
      garbage-registry contract and idempotency are unchanged.

Hermetic: DEEPMAP_HOME is a throwaway dir, the free-port probe is stubbed, no Neo4j, no network, no
real ports bound. Exit 0 = green.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
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
    """A throwaway DEEPMAP_HOME + work dir; the free-port probe stubbed to `occupied`; restores all."""

    def __init__(self, occupied=None):
        self.occupied = set(occupied or ())

    def __enter__(self):
        self._td_home = tempfile.TemporaryDirectory()
        self._td_work = tempfile.TemporaryDirectory()
        self.home = Path(self._td_home.name)
        self.work = Path(self._td_work.name)
        self._saved_home = os.environ.get("DEEPMAP_HOME")
        self._saved_uri = os.environ.pop("NEO4J_URI", None)
        os.environ["DEEPMAP_HOME"] = str(self.home)
        self._saved_probe = PR._port_in_use
        PR._port_in_use = lambda host, port: port in self.occupied
        self._saved_knobs = {k: getattr(PR, k, None) for k in
                             ("_LOCK_WAIT_S", "_ALLOC_LOCK_WAIT_S", "_IO_RETRY_S")}
        return self

    def __exit__(self, *exc):
        PR._port_in_use = self._saved_probe
        for k, v in self._saved_knobs.items():
            if v is None:
                if hasattr(PR, k):
                    delattr(PR, k)
            else:
                setattr(PR, k, v)
        if self._saved_home is None:
            os.environ.pop("DEEPMAP_HOME", None)
        else:
            os.environ["DEEPMAP_HOME"] = self._saved_home
        if self._saved_uri is not None:
            os.environ["NEO4J_URI"] = self._saved_uri
        self._td_home.cleanup()
        self._td_work.cleanup()
        return False


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


# --------------------------------------------------------------------------- A1

_CHILD = ("import json, sys; sys.path.insert(0, sys.argv[1]); import port_registry as PR; "
          "print(json.dumps({'key': PR.project_key(sys.argv[2]), "
          "'ident': PR.project_identity(sys.argv[2])}))")


def _child(path: Path, utf8: bool) -> tuple:
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONUTF8", "PYTHONIOENCODING")}
    p = subprocess.run([sys.executable, "-X", "utf8" if utf8 else "utf8=0", "-c", _CHILD,
                        str(BUILD), str(path)], capture_output=True, env=env, timeout=120)
    err = p.stderr.decode("utf-8", "replace")
    try:
        out = json.loads(p.stdout.decode("utf-8", "replace").strip().splitlines()[-1])
    except Exception:
        out = {}
    return out, err


def gate_a1_real_child_locale() -> None:
    print("\n-- A1: non-ASCII project path -> SAME key with UTF-8 mode off and on, root and subdir --")
    if not shutil.which("git"):
        check("(git on PATH for the identity gate)", True, "skipped — git absent")
        return
    with tempfile.TemporaryDirectory() as td:
        for name in ("proj é א", "José_proj"):
            try:
                root = _mk_project(Path(td), name)
                (root / "pkg").mkdir(exist_ok=True)
            except (OSError, UnicodeError) as exc:
                check(f"(non-ASCII dir creatable for {name!a})", True, f"skipped — {exc}")
                continue
            keys = {}
            errs = []
            for where, path in (("root", root), ("pkg", root / "pkg")):
                for utf8 in (False, True):
                    out, err = _child(path, utf8)
                    keys[(where, utf8)] = out.get("key")
                    errs.append(err)
            want = keys[("root", True)]
            check(f"{name!a}: utf8=0 at the ROOT -> same key as utf8=1",
                  keys[("root", False)] == want and want, str(keys))
            check(f"{name!a}: utf8=0 from a SUBDIR -> the repo's key (git toplevel), not the subdir's",
                  keys[("pkg", False)] == want, str(keys))
            check(f"{name!a}: utf8=1 from a SUBDIR -> the repo's key", keys[("pkg", True)] == want, str(keys))
            check(f"{name!a}: no UnicodeDecodeError from the git reader thread",
                  not any("UnicodeDecodeError" in e for e in errs), next((e[-300:] for e in errs if e), ""))


_SIM_TOPLEVELS = ("C:/Users/José/proj é", "/home/José/proj é")   # BOTH shapes on EVERY platform


def _simulated_git(top: str, fn):
    """Run fn() with git "printing" `top` as UTF-8 bytes and subprocess.run applying its real decoding
    rules: an explicit encoding wins; else text=True decodes in the locale code page (here a non-UTF-8
    one, as on a Western-European Windows box); else bytes."""
    real_run = PR.subprocess.run
    top_bytes = (top + "\n").encode("utf-8")

    def fake_run(argv, **kw):
        if kw.get("encoding"):
            out = top_bytes.decode(kw["encoding"], kw.get("errors") or "strict")
        elif kw.get("text") or kw.get("universal_newlines"):
            out = top_bytes.decode("cp1252", "strict")
        else:
            out = top_bytes
        return subprocess.CompletedProcess(argv, 0, out, "" if isinstance(out, str) else b"")

    saved_which = PR.shutil.which
    saved_enc = getattr(PR, "_legacy_encodings", None)
    try:
        PR.subprocess.run = fake_run
        PR.shutil.which = lambda name: "git"
        if saved_enc is not None:
            PR._legacy_encodings = lambda: ["cp1252"]
        return fn()
    finally:
        PR.subprocess.run = real_run
        PR.shutil.which = saved_which
        if saved_enc is not None:
            PR._legacy_encodings = saved_enc


def gate_a1_simulated_cp1252() -> None:
    print("\n-- A1: simulated cp1252 locale (deterministic on every platform, both path shapes) --")
    for top in _SIM_TOPLEVELS:
        ident = _simulated_git(top, lambda: PR.project_identity("."))
        # Path(...).resolve() may prefix a drive / the cwd, but never changes the letters of the tail.
        check(f"{top!a}: identity decodes git's UTF-8 output as UTF-8 (not the locale code page)",
              "José" in ident and "proj é" in ident and "Ã" not in ident, ascii(ident))
        fn = getattr(PR, "legacy_identities", None)
        legacy = _simulated_git(top, lambda: fn(".")) if callable(fn) else None
        check(f"{top!a}: the pre-fix (cp1252) identity is reproduced for the E1 key migration",
              isinstance(legacy, list) and len(legacy) == 1 and "JosÃ©" in legacy[0], ascii(legacy))


# --------------------------------------------------------------------------- A3

def _registry_with(home: Path, root: Path, bolt=7707, http=7494) -> None:
    key = PR.project_key(root)
    reg = {"kind": PR.REGISTRY_KIND, "version": PR.REGISTRY_VERSION, "updated_utc": "x",
           "projects": {key: {"root": str(root), "bolt_port": bolt, "http_port": http,
                              "search_port": 8805, "embed_port": 8806}}}
    (home / PR.REGISTRY_FILENAME).write_text(json.dumps(reg), encoding="utf-8")


class _FlakyRead:
    """Patch Path.read_text so reads of the registry raise PermissionError `n` times (the Windows
    sharing violation during a concurrent os.replace), then behave normally."""

    def __init__(self, n):
        self.n = n
        self.calls = 0

    def __enter__(self):
        self._orig = Path.read_text
        me = self

        def read_text(path_self, *a, **k):
            if path_self.name == PR.REGISTRY_FILENAME:
                me.calls += 1
                if me.n is None or me.calls <= me.n:
                    raise PermissionError(13, "The process cannot access the file (simulated)")
            return me._orig(path_self, *a, **k)
        Path.read_text = read_text
        return self

    def __exit__(self, *exc):
        Path.read_text = self._orig
        return False


def gate_a3_transient_read() -> None:
    print("\n-- A3: a TRANSIENT read error is retried (never a silent 7687) --")
    with _Home() as h:
        PR._IO_RETRY_S = 1.0
        a = _mk_project(h.work, "a")
        _registry_with(h.home, a)
        with _FlakyRead(3) as fr:
            uri = PR.neo4j_uri_for(a)
        check("3 transient PermissionErrors -> neo4j_uri_for still returns the REGISTERED bolt 7707",
              uri == "bolt://127.0.0.1:7707", f"{uri} (reads={fr.calls})")
        with _FlakyRead(2):
            pair = PR.lookup_ports(a)
        check("lookup_ports survives a transient read error -> (8805, 8806)", pair == (8805, 8806), str(pair))


def gate_a3_persistent_unreadable() -> None:
    print("\n-- A3: a registry that EXISTS but stays unreadable/unrecognized -> loud, never 7687 --")
    exc_type = getattr(PR, "RegistryUnreadableError", None)
    check("port_registry exports RegistryUnreadableError", exc_type is not None)
    with _Home() as h:
        PR._IO_RETRY_S = 0.2
        a = _mk_project(h.work, "a")
        _registry_with(h.home, a)
        raised, uri = False, None
        with _FlakyRead(None):
            try:
                uri = PR.neo4j_uri_for(a)
            except Exception as exc:  # noqa: BLE001
                raised = exc_type is not None and isinstance(exc, exc_type)
                uri = f"raised {type(exc).__name__}: {exc}"
            status = PR.registry_status() if hasattr(PR, "registry_status") else None
            lk = PR.lookup_neo4j_ports(a)
        check("persistently unreadable -> neo4j_uri_for RAISES RegistryUnreadableError (not 7687)",
              raised, str(uri))
        check("registry_status() reports 'unreadable'", status == "unreadable", str(status))
        check("lookup_neo4j_ports stays never-raise (None)", lk is None, str(lk))

    with _Home() as h:
        a = _mk_project(h.work, "a")
        (h.home / PR.REGISTRY_FILENAME).write_text("}{ not json", encoding="utf-8")
        raised, uri = False, None
        try:
            uri = PR.neo4j_uri_for(a)
        except Exception as exc:  # noqa: BLE001
            raised = exc_type is not None and isinstance(exc, exc_type)
            uri = f"raised {type(exc).__name__}"
        check("garbage registry -> neo4j_uri_for RAISES (can't know whose 7687 it is)", raised, str(uri))
        st = PR.registry_status() if hasattr(PR, "registry_status") else None
        check("registry_status() reports 'unrecognized'", st == "unrecognized", str(st))
        check("the garbage file is left untouched",
              (h.home / PR.REGISTRY_FILENAME).read_text(encoding="utf-8") == "}{ not json")

    with _Home() as h:
        a = _mk_project(h.work, "a")
        check("ABSENT registry -> the frozen default bolt://127.0.0.1:7687 (unchanged)",
              PR.neo4j_uri_for(a) == "bolt://127.0.0.1:7687", PR.neo4j_uri_for(a))
        st = PR.registry_status() if hasattr(PR, "registry_status") else None
        check("registry_status() reports 'absent'", st == "absent", str(st))
        env = {"NEO4J_URI": "bolt://byo:1234", "DEEPMAP_HOME": str(h.home)}
        (h.home / PR.REGISTRY_FILENAME).write_text("}{", encoding="utf-8")
        check("an operator NEO4J_URI still wins verbatim (even over a garbage registry)",
              PR.neo4j_uri_for(a, env=env) == "bolt://byo:1234")


def gate_a3_writer_retry() -> None:
    print("\n-- A3: the WRITER retries a transient os.replace failure (no silently lost write) --")
    real_replace = os.replace
    with _Home() as h:
        PR._IO_RETRY_S = 1.0
        a = _mk_project(h.work, "a")
        state = {"n": 0}

        def flaky_replace(src, dst):
            if str(dst).endswith(PR.REGISTRY_FILENAME) and state["n"] < 2:
                state["n"] += 1
                raise PermissionError(5, "Access is denied (simulated concurrent reader)")
            return real_replace(src, dst)
        os.replace = flaky_replace
        try:
            pair = PR.allocate_ports(a)
        finally:
            os.replace = real_replace
        check("allocation after 2 transient replace failures -> (8787, 8788)", pair == (8787, 8788), str(pair))
        check("...and the allocation is PERSISTED (a lookup finds it)", PR.lookup_ports(a) == (8787, 8788),
              str(PR.lookup_ports(a)))

    with _Home() as h:
        PR._IO_RETRY_S = 0.2
        a = _mk_project(h.work, "a")

        def dead_replace(src, dst):
            if str(dst).endswith(PR.REGISTRY_FILENAME):
                raise PermissionError(5, "Access is denied (simulated, persistent)")
            return real_replace(src, dst)
        os.replace = dead_replace
        try:
            pair = PR.allocate_ports(a)
        finally:
            os.replace = real_replace
        check("a registry write that NEVER lands -> allocate returns None (not an unpersisted pair)",
              pair is None, str(pair))


_RACE_WRITER = r"""
import json, os, sys, time
sys.path.insert(0, sys.argv[1])
import port_registry as PR
env = {"DEEPMAP_HOME": sys.argv[2]}
reg = PR.load_registry(env)
deadline = time.time() + float(sys.argv[3])
while time.time() < deadline:
    PR._write_registry(reg, env)
"""


def gate_a3_live_race() -> None:
    print("\n-- A3: a live writer-vs-reader race resolves to the registered port every time --")
    if os.name != "nt":
        check("(the sharing-violation race is Windows-only)", True, "skipped on POSIX — os.replace is atomic")
        return
    with _Home() as h:
        a = _mk_project(h.work, "a")
        _registry_with(h.home, a)
        env = {"DEEPMAP_HOME": str(h.home)}
        w = subprocess.Popen([sys.executable, "-c", _RACE_WRITER, str(BUILD), str(h.home), "4"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.5)
        seen: dict = {}
        t0 = time.time()
        while time.time() - t0 < 3.0:
            try:
                u = PR.neo4j_uri_for(a, env)
            except Exception as exc:  # noqa: BLE001
                u = f"raised {type(exc).__name__}"
            seen[u] = seen.get(u, 0) + 1
        w.wait(timeout=60)
        check("every resolution under a concurrent rewrite -> bolt://127.0.0.1:7707",
              set(seen) == {"bolt://127.0.0.1:7707"}, str(seen))


# --------------------------------------------------------------------------- A4

def gate_a4_lock_timeout() -> None:
    print("\n-- A4: a registry-lock TIMEOUT never returns the base pair (callers fail loud) --")
    with _Home() as h:
        PR._LOCK_WAIT_S = 0.3
        PR._ALLOC_LOCK_WAIT_S = 0.3
        a = _mk_project(h.work, "a")
        b = _mk_project(h.work, "b")
        check("A (uncontended) gets the base MCP pair (8787, 8788)", PR.allocate_ports(a) == (8787, 8788))
        h.occupied.update({8787, 8788})          # A's servers are live on the base pair
        holder = PR._acquire_lock(wait_s=1.0)
        check("(test holds the registry lock)", holder is not None)
        try:
            t0 = time.monotonic()
            pb = PR.allocate_ports(b)
            pn = PR.allocate_neo4j_ports(b, base_adopt_ok=lambda: True)
            dt = time.monotonic() - t0
        finally:
            PR._release_lock(holder)
        check("lock timeout -> allocate_ports returns None, NOT A's (8787, 8788)", pb is None, str(pb))
        check("lock timeout -> allocate_neo4j_ports returns None, NOT (7687, 7474)", pn is None, str(pn))
        check("...and nothing was recorded for B", PR.lookup_ports(b) is None, str(PR.lookup_ports(b)))
        check("the wait stays bounded", dt < 10, f"{dt:.1f}s")
        pb2 = PR.allocate_ports(b)
        check("lock released -> B allocates its OWN pair (8789, 8790)", pb2 == (8789, 8790), str(pb2))


def gate_a4_exhaustion() -> None:
    print("\n-- A4: scan EXHAUSTION never returns the (owned / in-use) base pair --")
    with _Home() as h:
        a = _mk_project(h.work, "a")
        h.occupied.update(range(8787, 8787 + 2 * PR.MAX_PAIRS + 2))
        pa = PR.allocate_ports(a)
        check("every candidate in use -> None, not (8787, 8788)", pa is None, str(pa))
        h.occupied.clear()
        h.occupied.update(range(7689, 7689 + 2 * PR.MAX_PAIRS + 2))
        h.occupied.update(range(7476, 7476 + 2 * PR.MAX_PAIRS + 2))
        pn = PR.allocate_neo4j_ports(a, base_adopt_ok=lambda: False)   # base owned by another project
        check("base refused + the rest in use -> None, not the foreign-owned (7687, 7474)", pn is None, str(pn))


def gate_a4_unchanged_contracts() -> None:
    print("\n-- A4: unchanged contracts (idempotent, garbage registry -> base, no clobber) --")
    with _Home() as h:
        a = _mk_project(h.work, "a")
        first = PR.allocate_ports(a)
        h.occupied.update({8787, 8788})
        check("idempotent: a re-allocation returns the stored pair without re-probing",
              PR.allocate_ports(a) == first == (8787, 8788), str(first))
    with _Home() as h:
        a = _mk_project(h.work, "a")
        (h.home / PR.REGISTRY_FILENAME).write_text("}{ not json", encoding="utf-8")
        pa = PR.allocate_ports(a)
        check("garbage registry -> base pair (the documented unknown-schema contract), no raise",
              pa == (8787, 8788), str(pa))
        check("garbage registry NOT overwritten",
              (h.home / PR.REGISTRY_FILENAME).read_text(encoding="utf-8") == "}{ not json")


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 68)
    print("DeepMap — port registry identity + concurrency robustness (A1/A3/A4)")
    print("=" * 68)
    gate_a1_real_child_locale()
    gate_a1_simulated_cp1252()
    gate_a3_transient_read()
    gate_a3_persistent_unreadable()
    gate_a3_writer_retry()
    gate_a3_live_race()
    gate_a4_lock_timeout()
    gate_a4_exhaustion()
    gate_a4_unchanged_contracts()
    print("\n" + "=" * 68)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
