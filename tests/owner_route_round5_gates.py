#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j, no-network gates for the ROUND-5 owner / route fixes in build/port_registry.py and the
hooks (binding decisions G2 hook side, G3, G4):

  G3a An owner stamp that is NOT an absolute path on THIS OS (a Windows 'C:/...' stamp read on
      Linux/macOS, a drive-less '/...' or a relative stamp on Windows) is a DIFFERENT project. It used to
      be resolved against the working directory, so run from the project root it became "a narrower
      stamp whose directory is gone" -> this project's: a shared escape-hatch instance's Windows owner was
      merged into by a macOS/Linux developer's sync, and WSL was told the Windows graph was its own.
  G3b E1's legacy (garbled, pre-UTF-8-fix) owner branch applies the same F1 rule as _same_project_path:
      a garbled stamp INSIDE this project's garbled identity is mapped back to the path it came from
      (re-encoded with the candidate code page, decoded as UTF-8) and judged by identity / nearest
      existing ancestor. Raw containment made a parent repo accept a NESTED repo's pre-fix stamp as its
      own (read its graph, push into it, adopt its store).
  G4  The REAL owner of a mis-claimed base store is not locked out: when another project's registry entry
      claims the base (7687, 7474) but the base store's owner record names THIS project, that claim is
      VOID for this project — resolve_neo4j_route routes it "legacy-unverified" (the live owner check still
      applies) instead of "none", and the Neo4j allocator consults base_adopt_ok for the base instead of
      skipping it. The record reader mirrors provision's path exactly (drift-locked).
  G2  (hook side) A heal POLICY refusal (status "refused": true / "refusal_kind" foreign-base-claim |
      legacy-owner, or a round-4 status whose reason is such a refusal) is never "superseded" by a port
      probe — listening ports cannot tell "up" from "not yours". notify says DeepMap must not be used for
      this project (no "it may recover" promise) and the nudge stays silent. bolt_target also refuses to
      vouch for a registry claim on the base pair whose owner record names another project. Drift lock:
      the hooks' refusal kinds / phrases are checked against the REAL texts provision and port_registry
      write (and a no-instance / unreachable / credentials reason is never taken for a refusal).
  G4+ With two entries on 7687 (the claimant's + the re-adopting owner's), touch_last_used_by_bolt touches
      the MCP server's own project (DEEPMAP_PROJECT_KEY) — throttle and write alike; the MCP pool never
      voids a claim.
  DOC commands/status.md / doctor.md / impact.md explain the "belongs to project X" refusal of a base
      claim (-> /deep-map:setup) and no longer say a foreign owner is only possible on NEO4J_URI;
      refresh.md says a graph synced from another OS (WSL vs Windows) is a different project (G3).

Hermetic: temp git repos + a temp DEEPMAP_HOME; the free-port / port-listening probes stubbed; no
Neo4j, no sockets opened, no network. DMGATE_ROOT runs these gates against another copy of the repo (how
the RED evidence was taken). Exit 0 = green.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# Auto-sync (DeepMap's background refresh at session start) is on by default in an attended session; this gate
# locks the hooks WITHOUT it, so it is pinned off for this process and every hook process the gate starts
# (tests/auto_sync_hooks_gates.py locks the hooks with it on).
os.environ["DEEPMAP_AUTO_SYNC"] = "0"
REPO = Path(os.environ.get("DMGATE_ROOT") or Path(__file__).resolve().parent.parent)
HOOKS = REPO / "hooks"
BUILD = REPO / "build"
COMMANDS = REPO / "commands" if (REPO / "commands").is_dir() else Path(__file__).resolve().parent.parent / "commands"
sys.path.insert(0, str(HOOKS))
sys.path.insert(0, str(BUILD))
os.environ.pop("NEO4J_URI", None)          # provision reads the escape hatch at import time
import port_registry as PR  # noqa: E402

_PASS = 0
_FAIL = 0
_SCRUB = ("PYTHONUTF8", "PYTHONIOENCODING", "DEEPMAP_HOOKS", "DEEPMAP_HEAL", "DEEPMAP_NUDGE",
          "DEEPMAP_ANNOUNCE", "DEEPMAP_ONBOARD", "NEO4J_URI", "DEEPMAP_PROJECT_ROOT", "DEEPMAP_PROJECT_KEY",
          "NEO4J_VERSION")
_GIT_ENV = {"GIT_AUTHOR_NAME": "g", "GIT_AUTHOR_EMAIL": "g@x", "GIT_COMMITTER_NAME": "g",
            "GIT_COMMITTER_EMAIL": "g@x"}
F2_REASON = ("this project is registered on the base Neo4j (bolt://localhost:7687), but that store belongs to "
             "project C:/w/owner (its owner record) — a claim an older DeepMap wrote by mistake; DeepMap never "
             "uses another project's graph as this one's: run /deep-map:setup to give this project its own "
             "instance")
LEGACY_REASON = ("bolt://localhost:7687 holds project C:/w/owner's DeepMap graph — this project has no DeepMap "
                 "Neo4j instance of its own (never set up here, or removed by instance-rm) — run "
                 "/deep-map:setup in this project to give it one")


def check(label: str, cond: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {label}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}{(' — ' + str(detail)[:500]) if detail else ''}")


def _try(fn, *a, **k):
    try:
        return fn(*a, **k)
    except Exception as exc:  # noqa: BLE001
        return f"raised {type(exc).__name__}: {exc}"


def fwd(p) -> str:
    return str(p).replace("\\", "/")


def _git(d: Path, *args) -> str:
    p = subprocess.run([shutil.which("git"), "-C", str(d), *args], capture_output=True, timeout=60,
                       env=dict(os.environ, **_GIT_ENV))
    return (p.stdout or b"").decode("utf-8", "replace").strip()


def _repo(parent: Path, name: str, commit: bool = False) -> Path:
    d = parent / name
    d.mkdir(parents=True, exist_ok=True)
    _git(d, "init", "-q")
    if commit:
        _git(d, "commit", "-q", "--allow-empty", "-m", "c1")
    return d.resolve()


def _garbled(root: Path, enc: str = "cp1252") -> str:
    """What a <= v0.27.4 build stamped for `root` with its locale code page = `enc` (the git toplevel's UTF-8
    bytes decoded in that code page)."""
    raw = subprocess.run([shutil.which("git"), "-C", str(root), "rev-parse", "--show-toplevel"],
                         capture_output=True, timeout=30).stdout
    top = raw.decode(enc).replace("\r\n", "\n").strip()
    return str(Path(top).resolve())


@contextlib.contextmanager
def _chdir(d: Path):
    old = os.getcwd()
    os.chdir(str(d))
    try:
        yield
    finally:
        os.chdir(old)


class _Home:
    """A throwaway DEEPMAP_HOME + work dir; the free-port probe stubbed (`occupied`); the legacy code page
    pinned to cp1252 (portable); env scrubbed; restores everything."""

    def __init__(self):
        self.occupied: set = set()

    def __enter__(self):
        self._td_home = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._td_work = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.home = Path(self._td_home.name).resolve()
        self.work = Path(self._td_work.name).resolve()
        self._saved_env = {k: os.environ.get(k) for k in set(_SCRUB) | {"DEEPMAP_HOME"}}
        for k in _SCRUB:
            os.environ.pop(k, None)
        os.environ["DEEPMAP_HOME"] = str(self.home)
        self._saved_probe = PR._port_in_use
        PR._port_in_use = lambda host, port: int(port) in self.occupied
        self._saved_enc = getattr(PR, "_legacy_encodings", None)
        PR._legacy_encodings = lambda: ["cp1252"]
        return self

    def __exit__(self, *exc):
        PR._port_in_use = self._saved_probe
        if self._saved_enc is not None:
            PR._legacy_encodings = self._saved_enc
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._td_home.cleanup()
        self._td_work.cleanup()
        return False

    def register(self, entries: dict) -> None:
        reg = {"version": PR.REGISTRY_VERSION, "kind": PR.REGISTRY_KIND, "projects": entries}
        (self.home / PR.REGISTRY_FILENAME).write_text(json.dumps(reg, indent=2), encoding="utf-8")

    def registry(self) -> dict:
        try:
            return json.loads((self.home / PR.REGISTRY_FILENAME).read_text(encoding="utf-8"))
        except Exception:
            return {}

    def record_path(self, version: str = "5.26.0") -> Path:
        return self.home / "neo4j" / f"neo4j-community-{version}" / "data" / "deepmap_base_owner.json"

    def record(self, root, version: str = "5.26.0") -> Path:
        p = self.record_path(version)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"root": fwd(root), "source": "setup-sync", "ts": "2026-09-26T00:00:00Z"}),
                     encoding="utf-8")
        return p


def _entry(root, *, mcp=(8787, 8788), bolt=None, **extra) -> dict:
    e = {"root": fwd(root), "search_port": mcp[0], "embed_port": mcp[1], "allocated_utc": "2026-08-01T00:00:00Z",
         "last_used": "2026-08-01T00:00:00Z"}
    if bolt:
        e.update({"bolt_port": bolt[0], "http_port": bolt[1], "neo4j_allocated_utc": "2026-08-01T00:00:00Z"})
    e.update(extra)
    return e


def _load_hook(name: str):
    spec = importlib.util.spec_from_file_location(f"dmr5_{name}", HOOKS / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# --------------------------------------------------------------------------- G3a
def gate_g3a_foreign_os_owner() -> None:
    print("\n-- G3a: an owner that is not an absolute path on THIS OS is a different project --")
    own = getattr(PR, "owner_is_this_project", None)
    same = getattr(PR, "_same_project_path", None)
    within = getattr(PR, "_within", None)
    verdict = getattr(PR, "legacy_owner_verdict", None)
    if not all(callable(f) for f in (own, same, within, verdict)):
        check("owner_is_this_project / _same_project_path / _within / legacy_owner_verdict exist", False)
        return
    with _Home() as h:
        repo = _repo(h.work, "other")
        (repo / "pkg").mkdir()
        foreign = []
        if os.name == "nt":
            drive, rest = os.path.splitdrive(str(repo))
            foreign.append(("a drive-less '/...' stamp (a Linux/WSL owner) that maps under this repo",
                            fwd(rest) + "/gone/sub"))
            foreign.append(("a drive-RELATIVE 'C:gone' stamp", f"{drive}gone"))
        else:
            foreign.append(("a Windows drive stamp 'C:/Users/x/proj'", "C:/Users/x/proj"))
            foreign.append(("a Windows backslash stamp 'C:\\Users\\x\\proj'", "C:\\Users\\x\\proj"))
        foreign.append(("a RELATIVE stamp 'gone/sub'", "gone/sub"))
        foreign.append(("a RELATIVE stamp naming an existing subfolder 'pkg'", "pkg"))
        with _chdir(repo):                                      # the normal case: run from the project root
            for label, owner in foreign:
                check(f"owner_is_this_project(repo, {label}) -> False (cwd = the repo)",
                      _try(own, repo, owner) is False, str(_try(own, repo, owner)))
                check(f"...and from a subdirectory session (cwd = repo/pkg): {label} -> False",
                      _try(own, repo / "pkg", owner) is False)
                check(f"_same_project_path(identity, {label}) -> False",
                      _try(same, str(repo), owner) is False)
                check(f"_within({label}, repo, missing_only=True) -> False",
                      _try(within, owner, str(repo), missing_only=True) is False)
                ok, why = verdict(repo, owner, True)
                check(f"legacy_owner_verdict refuses {label} (names /deep-map:setup)",
                      ok is False and "/deep-map:setup" in str(why), str(why))
            check("control: the project's own absolute identity is still this project",
                  _try(own, repo, str(repo)) is True and _try(own, repo, fwd(repo)) is True)
            check("control: an absolute vanished narrower stamp is still this project (F1)",
                  _try(own, repo, fwd(repo / "gone" / "sub")) is True)
            check("control: an absolute existing subfolder stamp of the same repo is this project",
                  _try(own, repo, str(repo / "pkg")) is True)
        abs_fn = getattr(PR, "_abs_on_this_os", None)
        check("port_registry._abs_on_this_os exists (the one 'absolute on this OS' rule)", callable(abs_fn))
        if callable(abs_fn):
            if os.name == "nt":
                cases = [("C:/x", True), ("C:\\x", True), ("\\\\srv\\share\\x", True), ("/x", False),
                         ("C:x", False), ("x/y", False), ("", False), (None, False)]
            else:
                cases = [("/x", True), ("C:/x", False), ("C:\\x", False), ("x/y", False), ("", False),
                         (None, False)]
            bad = [(c, w, _try(abs_fn, c)) for c, w in cases if _try(abs_fn, c) is not w]
            check("_abs_on_this_os truth table", not bad, str(bad))


# --------------------------------------------------------------------------- G3b
def gate_g3b_garbled_nested() -> None:
    print("\n-- G3b: E1's garbled-stamp branch applies the F1 identity rule (no raw containment) --")
    own = getattr(PR, "owner_is_this_project", None)
    verdict = getattr(PR, "legacy_owner_verdict", None)
    if not (callable(own) and callable(verdict)):
        check("owner_is_this_project / legacy_owner_verdict exist", False)
        return
    with _Home() as h:
        proj = _repo(h.work / "José", "proj")
        (proj / "pkg").mkdir()
        (proj / "libs").mkdir()
        core = _repo(proj / "libs", "core")                   # its OWN repository, nested in proj
        g_proj, g_core = _garbled(proj), _garbled(core)
        check("(fixture) the garbled identities differ from the UTF-8 ones and do not exist",
              g_proj != str(proj) and g_core != str(core) and not Path(g_proj).exists()
              and not Path(g_core).exists(), f"{g_proj!r} {g_core!r}")
        check("(fixture) the garbled core stamp lies lexically inside proj's garbled identity",
              fwd(g_core).lower().startswith(fwd(g_proj).lower() + "/"))
        # the review's case
        check("proj does NOT own the nested repo's garbled pre-fix stamp (was: raw containment -> True)",
              _try(own, proj, g_core) is False, str(_try(own, proj, g_core)))
        ok, why = verdict(proj, g_core, True)
        check("legacy_owner_verdict(proj, garbled core) refuses", ok is False and "/deep-map:setup" in str(why),
              str(why))
        check("...forward-slash form of the garbled core stamp -> False too", _try(own, proj, fwd(g_core)) is False)
        check("...from a subdirectory session of proj -> False", _try(own, proj / "pkg", g_core) is False)
        # the nested repo still owns its own garbled stamp, and proj its own
        check("core owns its own garbled pre-fix stamp", _try(own, core, g_core) is True)
        check("proj owns its own garbled pre-fix stamp", _try(own, proj, g_proj) is True)
        check("proj owns a garbled stamp of an EXISTING plain folder of proj (same identity)",
              _try(own, proj, fwd(g_proj) + "/pkg") is True)
        check("proj owns a garbled VANISHED narrower stamp judged at proj itself",
              _try(own, proj, fwd(g_proj) + "/gone/sub") is True)
        check("a garbled VANISHED stamp under the nested repo is core's, not proj's",
              _try(own, proj, fwd(g_core) + "/gone") is False and _try(own, core, fwd(g_core) + "/gone") is True)
        check("a stamp inside the garbled identity that cannot be un-garbled (not a cp1252 decode) -> False",
              _try(own, proj, fwd(g_proj) + "/日本") is False)
        check("control: core's CURRENT (UTF-8) stamp is not proj's", _try(own, proj, str(core)) is False)
        check("control: proj's current stamp is not core's", _try(own, core, str(proj)) is False)


# --------------------------------------------------------------------------- G4
def gate_g4_real_owner_not_locked_out() -> None:
    print("\n-- G4: a base claim by another project is VOID for the project the base owner record names --")
    rec_fn = getattr(PR, "base_owner_record", None)
    path_fn = getattr(PR, "base_owner_record_path", None)
    check("port_registry.base_owner_record / base_owner_record_path exist", callable(rec_fn) and callable(path_fn))
    with _Home() as h:
        p1 = _repo(h.work, "owner")                          # the REAL owner of the base store (no pair)
        p2 = _repo(h.work, "claimant")                       # the <= v0.27.4 mis-adopter (claims 7687)
        p3 = _repo(h.work, "third")
        k1, k2 = PR.project_key(p1), PR.project_key(p2)
        h.register({k1: _entry(p1, mcp=(8789, 8790)), k2: _entry(p2, mcp=(8787, 8788), bolt=(7687, 7474))})

        r = PR.resolve_neo4j_route(p1)
        check("no owner record: the claimant's claim stands -> none / no-instance (unchanged)",
              r.source == "none" and r.kind == "no-instance", repr(r))
        h.record(p2)
        r = PR.resolve_neo4j_route(p1)
        check("record names the CLAIMANT -> none / no-instance (unchanged)",
              r.source == "none" and r.kind == "no-instance", repr(r))
        h.record(p1)
        r = PR.resolve_neo4j_route(p1)
        check("record names THIS project -> legacy-unverified on bolt 7687 (the live owner check still applies)",
              r.source == "legacy-unverified" and r.uri == "bolt://127.0.0.1:7687" and r.problem is None
              and r.needs_owner_check, repr(r))
        check("...neo4j_uri_for(owner) answers bolt://127.0.0.1:7687 instead of raising",
              _try(PR.neo4j_uri_for, p1) == "bolt://127.0.0.1:7687", str(_try(PR.neo4j_uri_for, p1)))
        r = PR.resolve_neo4j_route(p1 / "sub") if (p1 / "sub").mkdir() is None else None
        check("...from a subdirectory session of the owner too", r is not None and r.source == "legacy-unverified",
              repr(r))
        r3 = PR.resolve_neo4j_route(p3)
        check("a THIRD project is still refused (the record is not its)", r3.source == "none"
              and r3.kind == "no-instance", repr(r3))
        r2 = PR.resolve_neo4j_route(p2)
        check("the claimant's own route is unchanged (registry — refused by the CLI / heal policy, not here)",
              r2.source == "registry" and r2.bolt == 7687, repr(r2))
        nested = _repo(p1, "vendored")
        h.record(nested)
        r = PR.resolve_neo4j_route(p1)
        check("record names a NESTED repo inside the owner -> the claim stands for the outer repo (F1)",
              r.source == "none", repr(r))
        h.record(p1)
        os.environ["NEO4J_URI"] = "bolt://localhost:7999"
        try:
            r = PR.resolve_neo4j_route(p1)
            check("an operator NEO4J_URI still wins (escape hatch)", r.source == "env", repr(r))
        finally:
            os.environ.pop("NEO4J_URI", None)
        # garbage / unreadable record -> no proof -> the claim stands
        h.record_path().write_text("{not json", encoding="utf-8")
        r = PR.resolve_neo4j_route(p1)
        check("a garbage owner record proves nothing -> none (the claim stands)", r.source == "none", repr(r))

        # ---- the allocator's claimed set for base_adopt_ok
        calls = []

        def adopt_yes():
            calls.append("yes")
            return True

        def adopt_no():
            calls.append("no")
            return False

        h.record(p2)
        got = _try(PR.allocate_neo4j_ports, p1, base_adopt_ok=adopt_yes)
        check("allocator, record names the claimant: base skipped WITHOUT consulting base_adopt_ok -> (7689, 7476)",
              got == (7689, 7476) and calls == [], f"{got} calls={calls}")
        ents = h.registry().get("projects", {})
        ents[k1].pop("bolt_port", None), ents[k1].pop("http_port", None), ents[k1].pop("neo4j_allocated_utc", None)
        h.register(ents)
        h.record(p1)
        calls.clear()
        got = _try(PR.allocate_neo4j_ports, p1, base_adopt_ok=adopt_no)
        check("allocator, record names THIS project: base_adopt_ok IS consulted; its False -> next pair",
              got == (7689, 7476) and calls == ["no"], f"{got} calls={calls}")
        ents = h.registry().get("projects", {})
        for f in ("bolt_port", "http_port", "neo4j_allocated_utc"):
            ents[k1].pop(f, None)
        h.register(ents)
        calls.clear()
        got = _try(PR.allocate_neo4j_ports, p1, base_adopt_ok=adopt_yes)
        check("allocator, record names THIS project + base_adopt_ok True -> the owner re-adopts (7687, 7474)",
              got == (7687, 7474) and calls == ["yes"], f"{got} calls={calls}")
        calls.clear()
        got3 = _try(PR.allocate_neo4j_ports, p3, base_adopt_ok=adopt_yes)
        check("allocator: a THIRD project never gets the claimed base (not consulted) -> a free pair",
              got3 not in ((7687, 7474),) and isinstance(got3, tuple) and calls == [], f"{got3} calls={calls}")
        got_mcp = _try(PR.allocate_ports, p3)
        check("the MCP pool is unaffected (never consults the base owner record)",
              isinstance(got_mcp, tuple) and got_mcp not in ((8787, 8788), (8789, 8790)), str(got_mcp))

        # ---- touch_last_used_by_bolt with two claimants on 7687 (the claimant's entry + the real owner's
        # re-adopted one, until the claimant's setup releases its claim): the server's own project is touched.
        # The registry is built explicitly (k1 FIRST, both on 7687) so no check leans on the allocator flow.
        OLD = "2026-01-01T00:00:00Z"
        now_iso = PR._now_iso()

        def _two_claims(last1, last2):
            h.register({k1: _entry(p1, mcp=(8789, 8790), bolt=(7687, 7474), last_used=last1),
                        k2: _entry(p2, mcp=(8787, 8788), bolt=(7687, 7474), last_used=last2)})

        def _touch(prefer):
            if prefer:
                os.environ["DEEPMAP_PROJECT_KEY"] = prefer
            try:
                PR.touch_last_used_by_bolt(7687)
            finally:
                os.environ.pop("DEEPMAP_PROJECT_KEY", None)
            e = h.registry().get("projects", {})
            return e.get(k1, {}).get("last_used"), e.get(k2, {}).get("last_used")

        _two_claims(OLD, OLD)
        check("(fixture) two entries claim bolt 7687, k1 listed first",
              list(h.registry()["projects"]) == [k1, k2]
              and PR._key_for_bolt(h.registry(), 7687) == k1, str(list(h.registry()["projects"])))
        l1, l2 = _touch(k2)
        check("two entries claim bolt 7687: the MCP server's own project (DEEPMAP_PROJECT_KEY, the SECOND entry) "
              "is the one touched", l2 != OLD and l1 == OLD, f"k1={l1} k2={l2}")
        _two_claims(now_iso, OLD)
        l1, l2 = _touch(k2)
        check("...and when the FIRST claimant was touched moments ago, the throttle is judged on the server's OWN "
              "entry (not the first match): k2 is still touched, k1 left alone",
              l2 != OLD and l1 == now_iso, f"k1={l1} (was {now_iso}) k2={l2}")
        _two_claims(OLD, OLD)
        l1, l2 = _touch(k1)
        check("...control: DEEPMAP_PROJECT_KEY = the first entry -> it is touched, the second is not",
              l1 != OLD and l2 == OLD, f"k1={l1} k2={l2}")
        _two_claims(OLD, OLD)
        l1, l2 = _touch(None)
        check("...control: no DEEPMAP_PROJECT_KEY (an older server) -> the first match, as before",
              l1 != OLD and l2 == OLD, f"k1={l1} k2={l2}")

    # ---- the void is for the Neo4j pool ONLY: the MCP pool never takes another project's claimed base pair,
    # even for the project the base owner record names (a void there would hand out a LIVE foreign server pair)
    with _Home() as h:
        p4 = _repo(h.work, "owner4")                         # named by the record; no entry of its own yet
        p2 = _repo(h.work, "claimant")
        k2 = PR.project_key(p2)
        h.register({k2: _entry(p2, mcp=(8787, 8788), bolt=(7687, 7474))})
        h.record(p4)
        got = _try(PR.allocate_ports, p4)
        check("MCP pool, record names THIS project, another project claims (8787, 8788): never handed out",
              isinstance(got, tuple) and got != (8787, 8788) and 8787 not in got and 8788 not in got, str(got))
        calls = []
        got_n = _try(PR.allocate_neo4j_ports, p4, base_adopt_ok=lambda: calls.append("yes") or True)
        check("...while the Neo4j pool re-adopts the base for it (base_adopt_ok consulted -> (7687, 7474))",
              got_n == (7687, 7474) and calls == ["yes"], f"{got_n} calls={calls}")

    # ---- the record reader mirrors provision's path + parser exactly (drift lock)
    if not (callable(rec_fn) and callable(path_fn)):
        return
    with _Home() as h:
        try:
            import provision as P  # noqa: WPS433
        except Exception as exc:  # noqa: BLE001
            check("provision importable for the drift lock", False, repr(exc))
            return
        saved = os.environ.get("NEO4J_VERSION")
        try:
            for v in (None, "5.27.1", "../../evil"):
                if v is None:
                    os.environ.pop("NEO4J_VERSION", None)
                else:
                    os.environ["NEO4J_VERSION"] = v
                with contextlib.redirect_stderr(io.StringIO()):
                    want = P._base_owner_path()
                check(f"base_owner_record_path == provision._base_owner_path (NEO4J_VERSION={v!r})",
                      _norm(path_fn()) == _norm(want), f"{path_fn()} vs {want}")
            os.environ.pop("NEO4J_VERSION", None)
            check("no record -> None on both sides", rec_fn() is None and P._base_owner_root() is None)
            h.record("C:/w/somewhere")
            check("a record -> the same root on both sides", rec_fn() == P._base_owner_root() == "C:/w/somewhere",
                  f"{rec_fn()} / {P._base_owner_root()}")
            h.record_path().write_text("[1, 2]", encoding="utf-8")
            check("a non-object record -> None on both sides", rec_fn() is None and P._base_owner_root() is None)
            check("port_registry's default Neo4j version == provision.DEFAULT_VERSION",
                  getattr(PR, "_BASE_NEO4J_DEFAULT_VERSION", None) == P.DEFAULT_VERSION,
                  f"{getattr(PR, '_BASE_NEO4J_DEFAULT_VERSION', None)} vs {P.DEFAULT_VERSION}")
        finally:
            if saved is None:
                os.environ.pop("NEO4J_VERSION", None)
            else:
                os.environ["NEO4J_VERSION"] = saved


def _norm(p) -> str:
    return os.path.normcase(fwd(Path(p)))


# --------------------------------------------------------------------------- G2 (hook side)
class _InProc:
    """Run a hook's main() in-process with a temp DEEPMAP_HOME, faked stdin/stdout and a per-port probe stub."""

    def __init__(self, mod, home: Path, up=(), env=None):
        self.mod, self.home, self.up, self.env = mod, home, set(up), dict(env or {})

    def __call__(self, payload: dict) -> str:
        hs = self.mod._hs
        saved = {k: os.environ.get(k) for k in set(_SCRUB) | {"DEEPMAP_HOME"} | set(self.env)}
        saved_io = (sys.stdin, sys.stdout)
        saved_probe = hs.port_listening
        try:
            for k in _SCRUB:
                os.environ.pop(k, None)
            os.environ["DEEPMAP_HOME"] = str(self.home)
            os.environ.update(self.env)
            up = self.up
            hs.port_listening = lambda port, host="127.0.0.1", timeout=None: int(port) in up
            sys.stdin = io.StringIO(json.dumps(payload))
            sys.stdout = io.StringIO()
            self.mod.main()
            return sys.stdout.getvalue()
        finally:
            sys.stdin, sys.stdout = saved_io
            hs.port_listening = saved_probe
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


def _ctx(out: str) -> str:
    try:
        return json.loads(out.strip().splitlines()[-1])["hookSpecificOutput"]["additionalContext"]
    except Exception:
        return ""


def _hook_project(h: _Home, *, bolt=(7687, 7474), mcp=(18911, 18912)) -> Path:
    r = _repo(h.work, "proj", commit=True)
    d = r / ".deepmap"
    d.mkdir(parents=True, exist_ok=True)
    (d / "graph_build.json").write_text(json.dumps({"nodes": 10, "verdict": "READY", "extractor": "graphify"}),
                                        encoding="utf-8")
    h.register({PR.project_key(r): _entry(r, mcp=mcp, bolt=bolt)})
    (r / ".mcp.json").write_text(json.dumps({"mcpServers": {
        "deepmap-search": {"type": "http", "url": f"http://127.0.0.1:{mcp[0]}/mcp"},
        "deepmap-embed": {"type": "http", "url": f"http://127.0.0.1:{mcp[1]}/mcp"}}}), encoding="utf-8")
    return r


def _status(h: _Home, root: Path, *, ahead=5, **fields) -> Path:
    p = h.home / "run" / f"heal-{PR.project_key(root)}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    rec = {"ts": "2026-09-26T00:00:00Z", "ts_epoch": time.time(), "neo4j_ok": True, "mcp_ok": True, "reason": None}
    rec.update(fields)
    p.write_text(json.dumps(rec), encoding="utf-8")
    man = root / ".deepmap" / "graph_build.json"
    t = man.stat().st_mtime + ahead
    os.utime(p, (t, t))
    return p


def gate_g2_hooks_never_supersede_a_refusal() -> None:
    print("\n-- G2 (hook side): a heal POLICY refusal is final until policy re-checks it --")
    NT = _load_hook("deepmap_sessionstart_notify")
    NU = _load_hook("deepmap_presearch_nudge")
    SF = _load_hook("deepmap_postedit_syncflag")
    hs = NT._hs
    UP = {18911, 18912, 7687}                                # every port listens (the old servers + base JVM)
    cases = [
        ("foreign-base-claim refusal (refused: true)",
         dict(neo4j_ok=False, refused=True, refusal_kind="foreign-base-claim", reason=F2_REASON)),
        ("legacy-owner refusal (refused: true)",
         dict(neo4j_ok=False, refused=True, refusal_kind="legacy-owner", reason=LEGACY_REASON)),
        ("refusal_kind alone (no 'refused' flag)",
         dict(neo4j_ok=False, refusal_kind="foreign-base-claim", reason=F2_REASON)),
        ("a round-4 status (no refusal fields) whose reason IS the F2 refusal",
         dict(neo4j_ok=False, reason=F2_REASON)),
    ]
    for i, (label, st) in enumerate(cases):
        with _Home() as h:
            r = _hook_project(h)
            _status(h, r, **st)
            saved = hs.port_listening
            hs.port_listening = lambda port, host="127.0.0.1", timeout=None: int(port) in UP
            try:
                prob = hs.heal_problem(r)
            finally:
                hs.port_listening = saved
            check(f"heal_problem keeps the {label} although every port listens", prob is not None, str(prob))
            ctx = _ctx(_InProc(NT, h.home, UP)({"cwd": str(r), "session_id": f"n{i}"}))
            low = ctx.lower()
            check(f"notify, {label}: no READY announce; says do not use it for this project; names "
                  "/deep-map:setup; no 'it may recover' promise",
                  bool(ctx) and "is ACTIVE" not in ctx and "/deep-map:setup" in ctx
                  and "another project" in low and "new start-up check" not in low, ctx[:300])
            nctx = _ctx(_InProc(NU, h.home, UP)({"cwd": str(r), "session_id": f"u{i}", "tool_name": "Grep",
                                                 "tool_input": {"pattern": "x"}}))
            check(f"nudge, {label}: silent (never steers to a refused route)", nctx == "", nctx[:160])
            sctx = _ctx(_InProc(SF, h.home, UP)({"cwd": str(r), "session_id": f"s{i}", "tool_name": "Edit",
                                                 "tool_input": {"file_path": str(r / "a.py")}}))
            check(f"post-edit sync flag, {label}: silent (no 'deepmap_search reflects the last sync / run "
                  "/deep-map:refresh' steer toward a refused graph)", sctx == "", sctx[:160])
    # the sync flag keeps its behaviour everywhere else: no status, and a plain (non-policy) failure
    for i, (label, st) in enumerate((("no heal status", None),
                                     ("a plain 'not reachable' failure (not a policy refusal)",
                                      dict(neo4j_ok=False, reason="bolt://localhost:7687 not reachable after 120s")))):
        with _Home() as h:
            r = _hook_project(h)
            if st is not None:
                _status(h, r, **st)
            sctx = _ctx(_InProc(SF, h.home, set())({"cwd": str(r), "session_id": f"sc{i}", "tool_name": "Edit",
                                                     "tool_input": {"file_path": str(r / "a.py")}}))
            check(f"post-edit sync flag, {label}: still fires (/deep-map:refresh)", "/deep-map:refresh" in sctx,
                  sctx[:160])
    sup = getattr(hs, "heal_problem_superseded", None)
    with _Home() as h:
        r = _hook_project(h)
        saved = hs.port_listening
        hs.port_listening = lambda port, host="127.0.0.1", timeout=None: int(port) in UP
        try:
            check("heal_problem_superseded(('refused', reason)) -> False with every port up",
                  callable(sup) and sup(r, ("refused", F2_REASON)) is False)
            check("heal_problem_superseded(('neo4j', <an F2/legacy refusal reason>)) -> False with every port up",
                  callable(sup) and sup(r, ("neo4j", F2_REASON)) is False and sup(r, ("neo4j", LEGACY_REASON)) is False)
            check("control: a plain 'not reachable' neo4j failure with every port up IS superseded (R3 unchanged)",
                  callable(sup) and sup(r, ("neo4j", "bolt://localhost:7687 not reachable after 120s")) is True)
        finally:
            hs.port_listening = saved
    with _Home() as h:
        r = _hook_project(h)
        _status(h, r, ahead=-30, neo4j_ok=False, refused=True, refusal_kind="foreign-base-claim", reason=F2_REASON)
        check("a refusal OLDER than the manifest (a sync since passed its own owner check) is superseded by policy",
              hs.heal_problem(r) is None)
    bt = getattr(hs, "bolt_target", None)
    with _Home() as h:
        r = _hook_project(h)
        check("bolt_target: a registry claim on the base pair with NO owner record -> ('127.0.0.1', 7687)",
              callable(bt) and bt(r) == ("127.0.0.1", 7687), str(bt(r) if callable(bt) else None))
        h.record(r)
        check("bolt_target: ...record names THIS project -> ('127.0.0.1', 7687)", bt(r) == ("127.0.0.1", 7687))
        h.record("C:/w/owner" if os.name == "nt" else "/w/owner")
        check("bolt_target: ...record names ANOTHER project -> None (a probe of 7687 proves nothing about ownership)",
              bt(r) is None, str(bt(r)))
        saved = hs.port_listening
        hs.port_listening = lambda port, host="127.0.0.1", timeout=None: int(port) in UP
        try:
            check("...so an UNMARKED 'not reachable' neo4j failure is not superseded on such a claim either",
                  sup(r, ("neo4j", "bolt://localhost:7687 not reachable")) is False)
        finally:
            hs.port_listening = saved
    with _Home() as h:
        r = _hook_project(h, bolt=(7811, 7598))
        h.record("C:/w/owner" if os.name == "nt" else "/w/owner")
        check("bolt_target: a per-project instance is unaffected by the base owner record -> ('127.0.0.1', 7811)",
              bt(r) == ("127.0.0.1", 7811), str(bt(r)))


def gate_g2_drift_lock() -> None:
    """The hooks classify heal's status by kinds and phrases copied from provision / port_registry (the hooks
    must not import provision). Feed them the REAL texts so a rewording there can never silently re-open G2
    for a status written without the refusal fields, and pin what must NOT count as a refusal."""
    print("\n-- G2 drift lock: the hooks recognise every refusal heal writes, and nothing else --")
    NT = _load_hook("deepmap_sessionstart_notify")
    hs = NT._hs
    is_ref = getattr(hs, "_reason_is_refusal", None)
    st_ref = getattr(hs, "_status_is_refusal", None)
    kinds = getattr(hs, "_REFUSAL_KINDS", None)
    if not (callable(is_ref) and callable(st_ref) and kinds):
        check("deepmap_hook_state._reason_is_refusal / _status_is_refusal / _REFUSAL_KINDS exist", False)
        return
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            import provision as P  # noqa: WPS433
    except Exception as exc:  # noqa: BLE001
        check("provision importable for the G2 drift lock", False, repr(exc))
        return
    check("hook _REFUSAL_KINDS == provision._REFUSAL_KINDS (the kinds heal writes)",
          set(kinds) == set(getattr(P, "_REFUSAL_KINDS", ()) or ()),
          f"{kinds} vs {getattr(P, '_REFUSAL_KINDS', None)}")
    for k in getattr(P, "_REFUSAL_KINDS", ()) or ():
        check(f"a status heal writes for kind {k!r} is a refusal", st_ref({"neo4j_ok": False, "mcp_ok": False,
                                                                           "refused": True, "refusal_kind": k}))
    with _Home() as h:
        repo = _repo(h.work, "proj")
        other = "C:/w/other" if os.name == "nt" else "/w/other"
        real = []
        fct = getattr(P, "_foreign_claim_text", None)
        if callable(fct):
            real.append(("provision._foreign_claim_text (F2, owner record)",
                         fct(other, "bolt://localhost:7687", "its owner record")))
            real.append(("provision._foreign_claim_text (F2, live owner)",
                         fct(other, "bolt://localhost:7687", "the owner its live graph records")))
        else:
            check("provision._foreign_claim_text exists", False)
        real.append(("legacy_owner_verdict: another project's graph", PR.legacy_owner_verdict(repo, other, True)[1]))
        real.append(("legacy_owner_verdict: an unowned populated graph", PR.legacy_owner_verdict(repo, None, True)[1]))
        for label, reason in real:
            check(f"refusal recognised by its own wording: {label}",
                  bool(reason) and is_ref(reason) and st_ref({"neo4j_ok": False, "reason": reason}), str(reason)[:200])
        plain = [("no_instance_problem (no instance of its own)", PR.no_instance_problem(other)),
                 ("no_instance_problem (bare)", PR.no_instance_problem()),
                 ("an unreachable database", "bolt://localhost:7687 not reachable after 120s"),
                 ("a credentials rejection", "Neo4j at bolt://localhost:7811 rejected the credentials "
                                            "(AuthError: The client is unauthorized)")]
        for label, reason in plain:
            check(f"NOT a refusal (a probe / credentials rule decides): {label}",
                  not is_ref(reason) and not st_ref({"neo4j_ok": False, "reason": reason}), str(reason)[:200])


# --------------------------------------------------------------------------- DOC
def gate_docs() -> None:
    print("\n-- DOC: command docs explain the base-claim refusal (-> /deep-map:setup) --")
    docs = {p.name: " ".join(p.read_text(encoding="utf-8").split()) for p in COMMANDS.glob("*.md")}
    for name in ("status.md", "doctor.md", "impact.md"):
        t = docs.get(name, "")
        sent = next((s for s in re.split(r"(?<=[.!?])\s+", t) if "belongs to project" in s), "")
        check(f"{name}: the 'belongs to project X' base-claim refusal is explained and routed to /deep-map:setup",
              bool(sent) and ("/deep-map:setup" in sent or "/deep-map:setup" in t[t.find(sent):t.find(sent) + 600]),
              sent[:300])
    for name in ("status.md", "doctor.md"):
        t = docs.get(name, "")
        check(f"{name}: a foreign owner is no longer described as ONLY possible on an operator NEO4J_URI",
              "only possible on a shared operator" not in t and "(only on a shared operator" not in t)
    for name in ("status.md", "doctor.md", "impact.md"):
        t = docs.get(name, "")
        check(f"{name}: never advises --switch-project for the base-claim refusal",
              not re.search(r"belongs to project[^.]*--switch-project", t))
    # G3's user-visible consequence on a shared escape-hatch instance: a graph synced from ANOTHER OS (the same
    # repository through WSL '/mnt/c/...' and Windows 'C:\...') records that OS's path -> a different project
    t = docs.get("refresh.md", "")
    sent = next((s for s in re.split(r"(?<=[.!?])\s+", t) if "operating system" in s.lower()), "")
    check("refresh.md: a graph synced from another operating system (WSL vs Windows) counts as a different "
          "project on a shared NEO4J_URI instance",
          bool(sent) and "wsl" in sent.lower() and "different project" in sent.lower(), sent[:300])


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 68)
    print("DeepMap — round-5 owner/route fixes (G2 hook side, G3, G4)")
    print(f"(build under test: {BUILD})")
    print("=" * 68)
    if not shutil.which("git"):
        print("RESULT: GREEN — 0/0 gates passed. (git not on PATH: skipped)")
        return 0
    gate_g3a_foreign_os_owner()
    gate_g3b_garbled_nested()
    gate_g4_real_owner_not_locked_out()
    gate_g2_hooks_never_supersede_a_refusal()
    gate_g2_drift_lock()
    gate_docs()
    print("\n" + "=" * 68)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
