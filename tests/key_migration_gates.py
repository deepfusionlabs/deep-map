#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j gates for the NON-ASCII PROJECT-KEY MIGRATION (ROUND-3 E1, build/port_registry.py).

Up to v0.27.4 port_registry.project_identity decoded `git rev-parse --show-toplevel` in the LOCALE
code page (text=True, no encoding). On a Windows box whose ANSI code page is not UTF-8 (cp1252,
cp1255, ...), a project under a non-ASCII path was registered under the key of a GARBLED identity
("C:/Users/JosÃ©/proj"), or — when the bytes did not decode at all — under the key of the path the
command happened to run from. The fix decodes git's output as UTF-8, so the same folder now gets a
DIFFERENT key: the old entry is missed, and because it still CLAIMS its ports (bolt 7687 for a first
project) the project was refused as "another project's instance" and the stale claim could not be
removed (review finding, round 2).

Locked here (hermetic: a throwaway DEEPMAP_HOME, the port probe stubbed, no Neo4j, no network):
  M1 legacy_identities reproduces the pre-fix identity exactly: the code-page decode, and HEAD's
     decode-error fallback (the resolved root).
  M2 FULL MIGRATION (the finding's case: the base pair, no instance dir): the entry moves to the UTF-8
     key with aliases [old] and a rewritten root, together with the heal status file, the MCP owner
     sidecars and the graphify-output markers; routing/lookup/allocation then answer from it;
     idempotent; logged once; a pre-fix owner stamp is recognised as this project's.
  M3 a STOPPED per-project instance: instances/<old> moves to instances/<new> and its conf's
     directories follow; provision.resolved_instance finds it.
  M4 a RUNNING instance: nothing is moved (moving a live store is unsafe) — the entry stays under the
     legacy key with aliases [new] + pending_key, project_key() keeps answering the legacy key (so
     every key->dir mapping stays right), and the move completes on the first lookup after it stops.
     Split (ROUND-4) so EACH half of the running check is locked on its own: M4a a live pid with its
     ports free; M4b a dead / absent pid with the entry's ports listening; M4c a port only the conf
     records (on POSIX a rename of a live store SUCCEEDS, so losing the pid check splits the store).
  M5 a rename the OS refuses (Windows: files held open) -> the same deferred alias, then completes.
  M6 instances/<new> already exists -> deferred, nothing clobbered.
  M7 safety: ASCII paths never read the registry for the key; an unusable registry, an existing
     UTF-8 entry, a legacy path that is a REAL other directory, or a root mismatch -> no migration;
     never raises.
  M8 the decode-CRASH case (bytes invalid in the code page): an entry HEAD registered from a
     SUBDIRECTORY is adopted; a nested repository's own entry is not.
  M9 a REAL child interpreter with UTF-8 mode off, running the verbatim v0.27.4 code, writes the
     legacy key; a child running the fixed code (UTF-8 mode on, as the hooks/commands now run, and
     off) migrates it (Windows with a non-UTF-8 ANSI code page; skipped elsewhere).
  M10 two processes migrating at once end in ONE entry, one log line.
  M10b the lock winner is SLOW to write (a Windows sharing-violation retry, a loaded box): the racer still
     answers the UTF-8 key (it used to give up on the lock after 1 s and answer the legacy key).
  M11 (ROUND-4 F1) ONE owner predicate — owner_is_this_project, with legacy_owner_verdict and
     _same_project_path under it: containment counts only within ONE project identity (a subdirectory
     of the same git repo), never across a nested repository or a non-git folder around/inside a
     project (the review's `work` / `work/api` case); a vanished narrower stamp is judged at its nearest
     existing ancestor; E1's garbled pre-fix stamps still match, unless a REAL directory bears that name.
Exit 0 = green.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import locale
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = Path(os.environ.get("DMGATE_BUILD") or (ROOT / "build"))
sys.path.insert(0, str(BUILD))
os.environ.pop("NEO4J_URI", None)          # provision reads the escape hatch at import time
import port_registry as PR  # noqa: E402

_PASS = 0
_FAIL = 0
_DEAD_PID = 2147483644                      # never a live process (a Windows pid is a multiple of 4)


def check(label: str, cond: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {label}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}{(' — ' + str(detail)[:600]) if detail else ''}")


# ---------------------------------------------------------------- the pre-fix (v0.27.4) reference
HEAD_SRC = r'''
import hashlib, shutil, subprocess
from pathlib import Path


def project_identity(root) -> str:
    try:
        git = shutil.which("git")
        if git:
            try:
                p = subprocess.run([git, "-C", str(root), "rev-parse", "--show-toplevel"],
                                   capture_output=True, text=True, timeout=30)
                top = (p.stdout or "").strip()
                if p.returncode == 0 and top:
                    return str(Path(top).resolve())
            except (OSError, subprocess.TimeoutExpired):
                pass
        return str(Path(root).resolve())
    except Exception:
        return str(root)


def project_key(root) -> str:
    try:
        p = project_identity(root).replace("\\", "/")
    except Exception:
        p = str(root)
    return hashlib.sha1(p.encode("utf-8", "replace")).hexdigest()[:12]
'''   # verbatim from git show 9c8d50bce:build/port_registry.py (v0.27.4)


def head_identity(root, enc: str) -> str:
    """What v0.27.4 computed with its locale code page = `enc` (the in-process simulation): a strict
    text-mode decode; on Windows a decode error killed the reader thread (stdout None -> the resolved
    root), on POSIX it escaped subprocess.run (-> str(root))."""
    p = subprocess.run([shutil.which("git"), "-C", str(root), "rev-parse", "--show-toplevel"],
                       capture_output=True, timeout=30)
    try:
        text = p.stdout.decode(enc)
    except UnicodeDecodeError:
        if os.name != "nt":
            return str(root)
        text = ""
    top = text.replace("\r\n", "\n").strip()
    if p.returncode == 0 and top:
        return str(Path(top).resolve())
    return str(Path(root).resolve())


def key_of(identity: str) -> str:
    return hashlib.sha1(identity.replace("\\", "/").encode("utf-8", "replace")).hexdigest()[:12]


def fwd(s) -> str:
    return str(s).replace("\\", "/")


# ---------------------------------------------------------------- fixtures
class _Home:
    """A throwaway DEEPMAP_HOME + work dir; the port probe stubbed to `occupied`; the legacy code page
    pinned (portable: the same simulation on every platform); restores everything."""

    def __init__(self, enc="cp1252"):
        self.occupied: set = set()
        self.enc = enc

    def __enter__(self):
        self._td_home = tempfile.TemporaryDirectory()
        self._td_work = tempfile.TemporaryDirectory()
        self.home = Path(self._td_home.name).resolve()
        self.work = Path(self._td_work.name).resolve()
        self._saved_home = os.environ.get("DEEPMAP_HOME")
        os.environ["DEEPMAP_HOME"] = str(self.home)
        self._saved_probe = PR._port_in_use
        PR._port_in_use = lambda host, port: int(port) in self.occupied
        self._had_enc = hasattr(PR, "_legacy_encodings")
        self._saved_enc = getattr(PR, "_legacy_encodings", None)
        enc = self.enc
        PR._legacy_encodings = lambda: [enc]
        self.procs = []
        return self

    def __exit__(self, *exc):
        for p in self.procs:
            try:
                p.kill()
                p.wait(timeout=30)
            except Exception:
                pass
        PR._port_in_use = self._saved_probe
        if self._had_enc:
            PR._legacy_encodings = self._saved_enc
        else:
            try:
                delattr(PR, "_legacy_encodings")
            except AttributeError:
                pass
        if self._saved_home is None:
            os.environ.pop("DEEPMAP_HOME", None)
        else:
            os.environ["DEEPMAP_HOME"] = self._saved_home
        for td in (self._td_home, self._td_work):
            try:
                td.cleanup()
            except Exception:
                shutil.rmtree(td.name, ignore_errors=True)
        return False

    # -- helpers
    def reg_path(self) -> Path:
        return self.home / PR.REGISTRY_FILENAME

    def write_registry(self, projects: dict) -> None:
        reg = {"kind": PR.REGISTRY_KIND, "version": PR.REGISTRY_VERSION, "updated_utc": "2026-08-01T00:00:00Z",
               "projects": projects}
        self.reg_path().write_text(json.dumps(reg, indent=2, sort_keys=True), encoding="utf-8")

    def registry(self) -> dict:
        try:
            return json.loads(self.reg_path().read_text(encoding="utf-8"))
        except Exception:
            return {}

    def projects(self) -> dict:
        return self.registry().get("projects") or {}

    def log_lines(self) -> list:
        p = self.home / getattr(PR, "MIGRATION_LOG", "registry-migrations.log")
        try:
            return [ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
        except Exception:
            return []

    def live_pid(self) -> int:
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.procs.append(p)
        return p.pid

    def instance(self, key, bolt, http, pid=None) -> Path:
        inst = self.home / "instances" / key
        for d in ("conf", "data/databases", "logs", "run"):
            (inst / d).mkdir(parents=True, exist_ok=True)
        (inst / "data" / "databases" / "marker.txt").write_text("the graph", encoding="utf-8")
        conf = ("dbms.memory.transaction.total.max=0\n\n# >>> DeepMap managed >>>\n"
                f"server.directories.data={fwd(inst / 'data')}\n"
                f"server.directories.logs={fwd(inst / 'logs')}\n"
                f"server.directories.run={fwd(inst / 'run')}\n"
                f"server.bolt.listen_address=127.0.0.1:{bolt}\n"
                f"server.bolt.advertised_address=127.0.0.1:{bolt}\n"
                f"server.http.listen_address=127.0.0.1:{http}\n"
                f"server.http.advertised_address=127.0.0.1:{http}\n"
                "# <<< DeepMap managed <<<\n")
        (inst / "conf" / "neo4j.conf").write_text(conf, encoding="utf-8")
        if pid is not None:
            (inst / "run" / "neo4j.pid").write_text(str(pid), encoding="utf-8")
            (inst / "run" / "neo4j.pid.identity").write_text(
                json.dumps({"pid": pid, "created": 1.0, "project_key": key}), encoding="utf-8")
        return inst


def _mk_repo(parent: Path, name: str) -> Path:
    d = parent / name
    d.mkdir(parents=True, exist_ok=True)
    subprocess.run([shutil.which("git"), "init", "-q"], cwd=str(d), capture_output=True, timeout=60)
    return d.resolve()


def _entry(root_str, mcp, bolt=None, **extra) -> dict:
    e = {"root": fwd(root_str), "search_port": mcp[0], "embed_port": mcp[1],
         "allocated_utc": "2026-08-01T00:00:00Z", "last_used": "2026-08-01T00:00:00Z"}
    if bolt:
        e.update({"bolt_port": bolt[0], "http_port": bolt[1], "neo4j_allocated_utc": "2026-08-01T00:00:00Z"})
    e.update(extra)
    return e


def _quiet(fn, *a, **k):
    """Run fn with stderr captured; (result, stderr text, raised)."""
    buf = io.StringIO()
    try:
        with contextlib.redirect_stderr(buf):
            return fn(*a, **k), buf.getvalue(), None
    except Exception as exc:  # noqa: BLE001
        return None, buf.getvalue(), exc


def _try(fn, *a, **k):
    """fn(*a, **k), or 'raised <Type>: ...' (a RED run must still complete)."""
    try:
        return fn(*a, **k)
    except Exception as exc:  # noqa: BLE001
        return f"raised {type(exc).__name__}: {exc}"


def _hook_heal_problem(h: _Home, repo, key, reason):
    """Write a heal status file under `key` (as provision.write_heal_status names it) and ask the REAL
    hook module which problem it sees for `repo` — the hooks and provision must agree on the key."""
    run = h.home / "run"
    run.mkdir(parents=True, exist_ok=True)
    (run / f"heal-{key}.json").write_text(json.dumps(
        {"ts": "2026-09-25T00:00:00Z", "neo4j_ok": False, "mcp_ok": False, "reason": reason}), encoding="utf-8")
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("kmg_hook_state", ROOT / "hooks" / "deepmap_hook_state.py")
        hs = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hs)
        return hs.heal_problem(repo, recheck=False)
    except Exception as exc:  # noqa: BLE001
        return f"raised {type(exc).__name__}: {exc}"


def _latin1_fixture(h: _Home):
    repo = _mk_repo(h.work / "José", "proj")
    ident = PR.project_identity(repo)
    li = head_identity(repo, h.enc)
    return repo, ident, key_of(ident), li, key_of(li)


# ---------------------------------------------------------------- M1
def gate_m1_legacy_identities() -> None:
    print("\n-- M1: the pre-fix identity is reproduced exactly --")
    with _Home() as h:
        repo, ident, k_new, li, k_old = _latin1_fixture(h)
        check("(fixture) the UTF-8 identity is right and the cp1252 one is garbled",
              "José" in ident and "Ã" in li and k_old != k_new, ascii((ident, li)))
        fn = getattr(PR, "legacy_identities", None)
        got = fn(repo) if callable(fn) else None
        check("legacy_identities(repo) == [HEAD's cp1252 identity]", got == [li], ascii(got))
        (repo / "pkg").mkdir()
        got_sub = fn(repo / "pkg") if callable(fn) else None
        check("...the same from a subdirectory (git's toplevel, garbled the same way)", got_sub == [li],
              ascii(got_sub))
        heb = _mk_repo(h.work, "proj א")
        (heb / "pkg").mkdir()
        got_heb = fn(heb / "pkg") if callable(fn) else None
        want = head_identity(heb / "pkg", h.enc)
        check("decode error (bytes invalid in cp1252) -> HEAD's fallback, the resolved root itself",
              got_heb == [want] and fwd(want).endswith("/pkg"), ascii((got_heb, want)))
        asc = _mk_repo(h.work, "plain")
        check("ASCII path -> no legacy identity", (fn(asc) if callable(fn) else None) == [], "")


# ---------------------------------------------------------------- M2
def gate_m2_full_migration_base_pair() -> None:
    print("\n-- M2: full migration of a base-pair entry (the finding's case) --")
    with _Home() as h:
        repo, ident, k_new, li, k_old = _latin1_fixture(h)
        other = _mk_repo(h.work, "other")
        h.write_registry({k_old: _entry(li, (8787, 8788), (7687, 7474))})
        run = h.home / "run"
        run.mkdir(parents=True, exist_ok=True)
        (run / f"heal-{k_old}.json").write_text(json.dumps(
            {"ts": "2026-08-01T00:00:00Z", "neo4j_ok": True, "mcp_ok": True, "reason": None,
             "project_key": k_old, "root": fwd(li)}), encoding="utf-8")
        (run / "deepmap-search-8787.pid").write_text("4242", encoding="utf-8")
        (run / "deepmap-search-8787.owner").write_text(json.dumps(
            {"pid": 4242, "project_key": k_old, "root": fwd(li), "created": 1.0}), encoding="utf-8")
        (run / "deepmap-embed-8788.owner").write_text(json.dumps(
            {"pid": 4243, "project_key": "someoneelse1", "root": "/x", "created": 1.0}), encoding="utf-8")
        g = h.home / "gout" / key_of(str(repo))
        g.mkdir(parents=True)
        (g / "deepmap_owner.json").write_text(json.dumps({"root": str(repo), "project_key": k_old}),
                                              encoding="utf-8")

        r, err, exc = _quiet(PR.resolve_neo4j_route, repo)
        check("resolve_neo4j_route never raises", exc is None, repr(exc))
        check("the project routes to ITS registered 7687 (not 'no instance of its own')",
              r is not None and r.source == "registry" and r.bolt == 7687 and r.problem is None, repr(r))
        check("...under the UTF-8 key", r is not None and r.key == k_new, repr(r))
        projects = h.projects()
        ent = projects.get(k_new) or {}
        check("registry: the entry moved to the UTF-8 key; the legacy key is gone",
              k_new in projects and k_old not in projects, sorted(projects))
        check("registry: aliases == [legacy key], no pending_key, migrated_from recorded",
              ent.get("aliases") == [k_old] and "pending_key" not in ent and ent.get("migrated_from") == k_old,
              json.dumps(ent))
        check("registry: `root` rewritten to the UTF-8 identity; ports kept",
              ent.get("root") == fwd(ident) and (ent.get("bolt_port"), ent.get("search_port")) == (7687, 8787),
              ascii(ent.get("root")))
        heal_new, heal_old = run / f"heal-{k_new}.json", run / f"heal-{k_old}.json"
        hd = json.loads(heal_new.read_text(encoding="utf-8")) if heal_new.is_file() else {}
        check("heal status file renamed to the new key (project_key/root rewritten)",
              not heal_old.exists() and hd.get("project_key") == k_new and hd.get("root") == fwd(ident),
              json.dumps(hd))
        so = json.loads((run / "deepmap-search-8787.owner").read_text(encoding="utf-8"))
        eo = json.loads((run / "deepmap-embed-8788.owner").read_text(encoding="utf-8"))
        check("MCP owner sidecar: project_key + root rewritten, pid kept",
              so.get("project_key") == k_new and so.get("root") == fwd(ident) and so.get("pid") == 4242, json.dumps(so))
        check("another project's sidecar is untouched", eo.get("project_key") == "someoneelse1", json.dumps(eo))
        gm = json.loads((g / "deepmap_owner.json").read_text(encoding="utf-8"))
        check("graphify output marker re-keyed", gm.get("project_key") == k_new, json.dumps(gm))
        check("logged once: one line in the migration log + one stderr notice",
              len(h.log_lines()) == 1 and err.count("migrat") >= 1, (h.log_lines(), err))

        check("project_key(repo) == the UTF-8 key", PR.project_key(repo) == k_new)
        (repo / "pkg").mkdir(exist_ok=True)
        check("project_key(subdir) == the UTF-8 key", PR.project_key(repo / "pkg") == k_new)
        check("lookup_ports / lookup_neo4j_ports answer from the migrated entry",
              PR.lookup_ports(repo) == (8787, 8788) and PR.lookup_neo4j_ports(repo) == (7687, 7474))
        check("neo4j_uri_for -> bolt://127.0.0.1:7687", _try(PR.neo4j_uri_for, repo) == "bolt://127.0.0.1:7687",
              _try(PR.neo4j_uri_for, repo))
        check("allocate_ports is idempotent on the migrated entry (no second pair)",
              _try(PR.allocate_ports, repo) == (8787, 8788) and len(h.projects()) == 1)
        check("an alias lookup: neo4j_pair_for_key(legacy key) -> the pair",
              PR.neo4j_pair_for_key(k_old) == (7687, 7474), str(PR.neo4j_pair_for_key(k_old)))
        before = h.reg_path().read_text(encoding="utf-8")
        _quiet(PR.resolve_neo4j_route, repo)
        _quiet(PR.project_key, repo)
        check("idempotent: a second resolution rewrites nothing and logs nothing",
              h.reg_path().read_text(encoding="utf-8") == before and len(h.log_lines()) == 1)
        o = PR.resolve_neo4j_route(other)
        hp = _hook_heal_problem(h, repo, k_new, "probe-after-migration")
        check("the hooks read the heal status under the SAME (migrated) key",
              isinstance(hp, tuple) and hp[1] == "probe-after-migration", str(hp))
        check("another (never set up) project still gets no route on 7687 (it is ours)",
              o.source == "none" and o.kind == "no-instance", repr(o))

        verdict = getattr(PR, "legacy_owner_verdict")
        ok, why = verdict(repo, li, True)
        check("a graph owner stamped by the pre-fix build (garbled identity) is this project's", ok, str(why))
        own = getattr(PR, "owner_is_this_project", None)
        check("owner_is_this_project: garbled stamp -> True, the UTF-8 identity -> True, another -> False",
              callable(own) and own(repo, li) and own(repo, ident) and not own(repo, str(other)))
        ok2, _ = verdict(repo, str(other), True)
        check("...another project's owner is still refused", not ok2)


# ---------------------------------------------------------------- M3
def gate_m3_stopped_instance_moves() -> None:
    print("\n-- M3: a STOPPED per-project instance moves with the entry --")
    with _Home() as h:
        b = _mk_repo(h.work, "bproj")
        repo, ident, k_new, li, k_old = _latin1_fixture(h)
        kb = PR.project_key(b)
        h.write_registry({kb: _entry(b, (8787, 8788), (7687, 7474)),
                          k_old: _entry(li, (8789, 8790), (7689, 7476))})
        h.instance(k_old, 7689, 7476, pid=_DEAD_PID)
        r, err, exc = _quiet(PR.resolve_neo4j_route, repo)
        check("routes to its own 7689 under the UTF-8 key",
              exc is None and r.source == "registry" and r.bolt == 7689 and r.key == k_new, repr(r) + repr(exc))
        new_inst, old_inst = h.home / "instances" / k_new, h.home / "instances" / k_old
        check("instances/<old> moved to instances/<new> (the graph came along)",
              (new_inst / "data" / "databases" / "marker.txt").is_file() and not old_inst.exists())
        conf = (new_inst / "conf" / "neo4j.conf").read_text(encoding="utf-8") if new_inst.is_dir() else ""
        check("conf: the managed directories now point at instances/<new>; ports kept",
              f"instances/{k_new}/data" in conf and k_old not in conf
              and "server.bolt.listen_address=127.0.0.1:7689" in conf, conf[-400:])
        idf = new_inst / "run" / "neo4j.pid.identity"
        idd = json.loads(idf.read_text(encoding="utf-8")) if idf.is_file() else {}
        check("the Neo4j identity record follows the key", idd.get("project_key") in (k_new, None), json.dumps(idd))
        projects = h.projects()
        check("registry: <new> with aliases [<old>]; the other project untouched",
              (projects.get(k_new) or {}).get("aliases") == [k_old] and k_old not in projects
              and (projects.get(kb) or {}).get("bolt_port") == 7687, json.dumps(projects))
        try:
            import provision as P
            inst = P.resolved_instance(repo)
            conf_ok = P.paths(None, inst[0])["conf"].is_file() if inst else False
        except Exception as e:  # noqa: BLE001
            inst, conf_ok = repr(e), False
        check("provision.resolved_instance -> (<new>, 7689, 7476) and its conf exists",
              inst == (k_new, 7689, 7476) and conf_ok, str(inst))


# ---------------------------------------------------------------- M4
def gate_m4_running_instance_deferred() -> None:
    """M4a: a LIVE pid with its ports still FREE (a Neo4j that is starting up, or that listens on a port
    neither the conf nor the registry records) -> deferred by the pid check ALONE. Losing that check is
    the dangerous half on POSIX: os.rename of a running store's dir SUCCEEDS there, and the JVM keeps
    writing into the moved store while a later start creates new files at the conf paths (a split
    store). The deferred state is then followed through to its completion once the process stops."""
    print("\n-- M4a: a RUNNING instance (live pid, ports FREE) is never moved: deferred alias, then completes --")
    with _Home() as h:
        b = _mk_repo(h.work, "bproj")
        repo, ident, k_new, li, k_old = _latin1_fixture(h)
        kb = PR.project_key(b)
        h.write_registry({kb: _entry(b, (8787, 8788), (7687, 7474)),
                          k_old: _entry(li, (8789, 8790), (7689, 7476))})
        live = h.live_pid()
        old_inst = h.instance(k_old, 7689, 7476, pid=live)
        check("(fixture) the live pid is the ONLY busy signal: no port of the entry or the conf is listening",
              not h.occupied and not any(PR._port_in_use("127.0.0.1", p) for p in (7689, 7476)))
        r, err, exc = _quiet(PR.resolve_neo4j_route, repo)
        check("routes to its own RUNNING 7689 (no refusal)",
              exc is None and r is not None and r.source == "registry" and r.bolt == 7689, repr(r) + repr(exc))
        check("...under the LEGACY key while the instance runs (key == its instance dir)",
              r is not None and r.key == k_old, repr(r))
        check("project_key(repo) answers the legacy key while deferred", PR.project_key(repo) == k_old,
              PR.project_key(repo))
        projects = h.projects()
        ent = projects.get(k_old) or {}
        check("registry: entry kept under <old> with aliases [<new>] + pending_key <new>; no <new> entry",
              k_new not in projects and ent.get("aliases") == [k_new] and ent.get("pending_key") == k_new,
              json.dumps(ent))
        check("registry: root already rewritten to the UTF-8 identity", ent.get("root") == fwd(ident),
              ascii(ent.get("root")))
        check("nothing moved: instances/<old> intact, no instances/<new>",
              (old_inst / "data" / "databases" / "marker.txt").is_file()
              and not (h.home / "instances" / k_new).exists())
        try:
            import provision as P
            inst = P.resolved_instance(repo)
        except Exception as e:  # noqa: BLE001
            inst = repr(e)
        check("provision.resolved_instance -> (<old>, 7689, 7476): the live instance's own dir",
              inst == (k_old, 7689, 7476), str(inst))
        check("lookups answer: MCP (8789, 8790), neo4j (7689, 7476)",
              PR.lookup_ports(repo) == (8789, 8790) and PR.lookup_neo4j_ports(repo) == (7689, 7476))
        check("an alias lookup: neo4j_pair_for_key(<new>) -> (7689, 7476)",
              PR.neo4j_pair_for_key(k_new) == (7689, 7476), str(PR.neo4j_pair_for_key(k_new)))
        n_log = len(h.log_lines())
        before = h.reg_path().read_text(encoding="utf-8")
        _quiet(PR.resolve_neo4j_route, repo)
        _quiet(PR.project_key, repo)
        check("deferral logged once; a repeat lookup rewrites/logs nothing",
              n_log == 1 and len(h.log_lines()) == 1 and h.reg_path().read_text(encoding="utf-8") == before,
              (n_log, h.log_lines()))
        rb = PR.resolve_neo4j_route(b)
        check("the other project's route is unaffected", rb.source == "registry" and rb.bolt == 7687, repr(rb))
        hp = _hook_heal_problem(h, repo, k_old, "probe-while-deferred")
        check("while deferred, the hooks read the heal status under the legacy key (provision writes it there)",
              isinstance(hp, tuple) and hp[1] == "probe-while-deferred", str(hp))

        # the instance stops (hibernate / reboot) -> the next lookup completes the move
        for p in h.procs:
            p.kill()
            p.wait(timeout=30)
        h.procs.clear()
        h.occupied.clear()
        k, err2, exc2 = _quiet(PR.project_key, repo)
        projects = h.projects()
        ent = projects.get(k_new) or {}
        check("after it stops: project_key -> the UTF-8 key", exc2 is None and k == k_new, f"{k} {exc2!r}")
        check("...entry under <new>, aliases [<old>], pending_key dropped",
              k_old not in projects and ent.get("aliases") == [k_old] and "pending_key" not in ent,
              json.dumps(projects))
        check("...and instances/<old> moved to instances/<new>",
              (h.home / "instances" / k_new / "data" / "databases" / "marker.txt").is_file()
              and not old_inst.exists())
        check("...logged (deferral + completion = 2 lines)", len(h.log_lines()) == 2, h.log_lines())


def _deferred_by_ports(label: str, pid, entry_pair, conf_pair, occupied) -> None:
    """One M4b/M4c case: the pid file names NO live process (dead, or absent when `pid` is None) and ONLY
    `occupied` ports listen. The move must be deferred on the port evidence alone, then complete once the
    ports are free."""
    with _Home() as h:
        repo, ident, k_new, li, k_old = _latin1_fixture(h)
        h.write_registry({k_old: _entry(li, (8789, 8790), entry_pair)})
        old_inst = h.instance(k_old, conf_pair[0], conf_pair[1], pid=pid)
        pf = old_inst / "run" / "neo4j.pid"
        pid_live = pf.is_file() and PR._pid_alive(int(pf.read_text(encoding="utf-8").strip()))
        h.occupied.update(occupied)
        check(f"{label}: (fixture) no live pid ({'dead pid file' if pid is not None else 'no pid file'}), "
              f"ports {sorted(occupied)} listening", not pid_live)
        k, err, exc = _quiet(PR.project_key, repo)
        ent = h.projects().get(k_old) or {}
        check(f"{label}: deferred on the port evidence alone -> the legacy key, pending_key <new>",
              exc is None and k == k_old and ent.get("pending_key") == k_new and k_new not in h.projects(),
              f"{k} {exc!r} {json.dumps(h.projects())}")
        check(f"{label}: nothing moved (instances/<old> intact, no instances/<new>)",
              (old_inst / "data" / "databases" / "marker.txt").is_file()
              and not (h.home / "instances" / k_new).exists())
        h.occupied.clear()
        k2, _, exc2 = _quiet(PR.project_key, repo)
        check(f"{label}: ports free again -> the move completes under the UTF-8 key",
              exc2 is None and k2 == k_new and (h.home / "instances" / k_new / "data" / "databases"
                                                / "marker.txt").is_file() and not old_inst.exists(), str(k2))


def gate_m4_ports_busy_deferred() -> None:
    """M4b/M4c: the pid check can't see the instance (a stale pid file after a crash, a pid file the
    instance never wrote, or a recycled number that is dead now) but its ports are LISTENING -> deferred
    by the port check ALONE — once for the registry entry's pair, once for a pair only its conf records."""
    print("\n-- M4b/M4c: ports LISTENING with a dead / absent pid -> deferred by the port check alone --")
    _deferred_by_ports("M4b dead pid, entry bolt", _DEAD_PID, (7689, 7476), (7689, 7476), {7689})
    _deferred_by_ports("M4b no pid file, entry http", None, (7689, 7476), (7689, 7476), {7476})
    _deferred_by_ports("M4c dead pid, a port ONLY the conf records", _DEAD_PID, (7689, 7476), (7693, 7480),
                       {7693})


# ---------------------------------------------------------------- M5 / M6
def gate_m5_rename_refused() -> None:
    print("\n-- M5: a rename the OS refuses (Windows: files held open) -> deferred, then completes --")
    with _Home() as h:
        repo, ident, k_new, li, k_old = _latin1_fixture(h)
        h.write_registry({k_old: _entry(li, (8789, 8790), (7689, 7476))})
        old_inst = h.instance(k_old, 7689, 7476, pid=_DEAD_PID)
        real_rename = os.rename

        def refuse(src, dst, *a, **k):
            if str(src).rstrip("\\/").endswith(k_old):
                raise PermissionError(32, "The process cannot access the file (simulated)")
            return real_rename(src, dst, *a, **k)
        os.rename = refuse
        try:
            k, err, exc = _quiet(PR.project_key, repo)
        finally:
            os.rename = real_rename
        ent = h.projects().get(k_old) or {}
        check("refused rename -> the legacy key, entry kept with pending_key, dir intact",
              exc is None and k == k_old and ent.get("pending_key") == k_new and old_inst.is_dir(),
              f"{k} {exc!r} {json.dumps(ent)}")
        k2, _, _ = _quiet(PR.project_key, repo)
        check("rename allowed again -> migrated to the UTF-8 key",
              k2 == k_new and k_new in h.projects() and (h.home / "instances" / k_new).is_dir(), str(k2))


def gate_m6_conflict() -> None:
    print("\n-- M6: instances/<new> already exists -> deferred, nothing clobbered --")
    with _Home() as h:
        repo, ident, k_new, li, k_old = _latin1_fixture(h)
        h.write_registry({k_old: _entry(li, (8789, 8790), (7689, 7476))})
        old_inst = h.instance(k_old, 7689, 7476, pid=_DEAD_PID)
        clash = h.home / "instances" / k_new
        clash.mkdir(parents=True)
        (clash / "keep.txt").write_text("x", encoding="utf-8")
        k, err, exc = _quiet(PR.project_key, repo)
        check("conflict -> legacy key, both dirs untouched",
              exc is None and k == k_old and (clash / "keep.txt").is_file()
              and (old_inst / "data" / "databases" / "marker.txt").is_file(), f"{k} {exc!r}")
        r = PR.resolve_neo4j_route(repo)
        check("...and the project still routes to its own 7689", r.source == "registry" and r.bolt == 7689, repr(r))


# ---------------------------------------------------------------- M7
def gate_m7_safety() -> None:
    print("\n-- M7: safety — no false migration, no registry read for ASCII keys, never raises --")
    with _Home() as h:
        asc = _mk_repo(h.work, "plain")
        h.write_registry({"aaaaaaaaaaaa": _entry("/elsewhere", (8787, 8788), (7687, 7474))})
        calls = {"n": 0}
        real = PR._read_registry

        def counting(*a, **k):
            calls["n"] += 1
            return real(*a, **k)
        PR._read_registry = counting
        try:
            k = PR.project_key(asc)
        finally:
            PR._read_registry = real
        check("ASCII path: project_key reads no registry (zero added cost)", calls["n"] == 0 and k == key_of(
            PR.project_identity(asc)), f"reads={calls['n']}")

    with _Home() as h:
        repo, ident, k_new, li, k_old = _latin1_fixture(h)
        h.reg_path().write_text("}{ not json", encoding="utf-8")
        k, err, exc = _quiet(PR.project_key, repo)
        check("garbage registry -> the UTF-8 key, no raise, file untouched",
              exc is None and k == k_new and h.reg_path().read_text(encoding="utf-8") == "}{ not json")

    with _Home() as h:
        repo, ident, k_new, li, k_old = _latin1_fixture(h)
        h.write_registry({k_new: _entry(ident, (8791, 8792), (7691, 7478)),
                          k_old: _entry(li, (8787, 8788), (7687, 7474))})
        k, err, exc = _quiet(PR.project_key, repo)
        pj = h.projects()
        check("an existing UTF-8 entry wins; the legacy entry is left alone",
              k == k_new and k_old in pj and "aliases" not in pj[k_new], json.dumps(pj))

    with _Home() as h:
        repo, ident, k_new, li, k_old = _latin1_fixture(h)
        Path(li).mkdir(parents=True, exist_ok=True)          # the garbled path is a REAL directory
        h.write_registry({k_old: _entry(li, (8787, 8788), (7687, 7474))})
        k, err, exc = _quiet(PR.project_key, repo)
        check("the legacy identity is a REAL other directory -> not adopted",
              exc is None and k == k_new and k_old in h.projects() and k_new not in h.projects(), str(k))

    with _Home() as h:
        repo, ident, k_new, li, k_old = _latin1_fixture(h)
        h.write_registry({k_old: _entry("/some/other/root", (8787, 8788), (7687, 7474))})
        k, err, exc = _quiet(PR.project_key, repo)
        check("an entry under the legacy key whose recorded root differs -> not adopted",
              exc is None and k == k_new and k_old in h.projects(), str(k))

    with _Home() as h:
        weird = []
        for arg in ("", str(h.work / "does not exist é"), None):
            k, err, exc = _quiet(PR.project_key, arg)
            weird.append((arg, k, exc))
        check("never raises (empty / missing / None root)", all(e is None and isinstance(k, str) for _, k, e in weird),
              str(weird))


# ---------------------------------------------------------------- M8
def gate_m8_decode_crash_subdir() -> None:
    print("\n-- M8: decode-CRASH case — an entry HEAD registered from a subdirectory is adopted --")
    with _Home() as h:
        repo = _mk_repo(h.work, "proj א")
        (repo / "pkg").mkdir()
        ident = PR.project_identity(repo)
        k_new = key_of(ident)
        check("(fixture) from the ROOT, HEAD's fallback key == the UTF-8 key (no migration needed)",
              key_of(head_identity(repo, h.enc)) == k_new)
        li_pkg = head_identity(repo / "pkg", h.enc)
        k_pkg = key_of(li_pkg)
        nested = _mk_repo(repo, "vendored")                  # its OWN repository inside this one
        k_nested = key_of(PR.project_identity(nested))
        h.write_registry({k_pkg: _entry(li_pkg, (8787, 8788), (7687, 7474)),
                          k_nested: _entry(PR.project_identity(nested), (8789, 8790), (7689, 7476))})
        r, err, exc = _quiet(PR.resolve_neo4j_route, repo)
        pj = h.projects()
        check("resolved from the ROOT: the subdir-registered entry is adopted (7687, UTF-8 key)",
              exc is None and r.source == "registry" and r.bolt == 7687 and r.key == k_new, repr(r) + repr(exc))
        check("registry: <new> with aliases [<pkg key>]; the nested repo's entry untouched",
              (pj.get(k_new) or {}).get("aliases") == [k_pkg] and k_pkg not in pj
              and (pj.get(k_nested) or {}).get("bolt_port") == 7689, json.dumps(pj))
        rn = PR.resolve_neo4j_route(nested)
        check("the nested repository still routes to ITS own 7689", rn.source == "registry" and rn.bolt == 7689,
              repr(rn))

    with _Home() as h:
        repo = _mk_repo(h.work, "proj א")
        nested = _mk_repo(repo, "vendored")
        k_nested = key_of(PR.project_identity(nested))
        h.write_registry({k_nested: _entry(PR.project_identity(nested), (8787, 8788), (7687, 7474))})
        k, err, exc = _quiet(PR.project_key, repo)
        pj = h.projects()
        check("ONLY a nested repository's entry inside this repo -> never adopted as this project's",
              exc is None and k == key_of(PR.project_identity(repo)) and list(pj) == [k_nested]
              and "aliases" not in pj[k_nested], json.dumps(pj))


# ---------------------------------------------------------------- M9
_CHILD_HEAD = HEAD_SRC + "\nimport sys, json\nprint(json.dumps({'key': project_key(sys.argv[1]), " \
                         "'ident': project_identity(sys.argv[1])}))\n"
_CHILD_NEW = ("import json, sys; sys.path.insert(0, sys.argv[1]); import port_registry as PR; "
              "r = PR.resolve_neo4j_route(sys.argv[2]); "
              "print(json.dumps({'source': r.source, 'bolt': r.bolt, 'key': r.key, 'problem': r.problem}))")


def _run_child(code, args, utf8: bool, home: Path) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONUTF8", "PYTHONIOENCODING", "NEO4J_URI")}
    env["DEEPMAP_HOME"] = str(home)
    p = subprocess.run([sys.executable, "-X", "utf8" if utf8 else "utf8=0", "-c", code, *args],
                       capture_output=True, env=env, timeout=120)
    try:
        return json.loads(p.stdout.decode("utf-8", "replace").strip().splitlines()[-1])
    except Exception:
        return {"_err": p.stderr.decode("utf-8", "replace")[-600:]}


def gate_m9_real_child() -> None:
    print("\n-- M9: a REAL pre-fix child (UTF-8 mode off) writes the legacy key; the fixed code migrates it --")
    getenc = getattr(locale, "getencoding", None)
    enc = (getenc() if getenc else locale.getpreferredencoding(False)) or ""
    try:
        import codecs
        is_utf8 = codecs.lookup(enc).name == "utf-8"
    except Exception:
        is_utf8 = True
    if os.name != "nt" or is_utf8:
        with tempfile.TemporaryDirectory() as td:
            repo = _mk_repo(Path(td) / "José", "proj")
            head = _run_child(_CHILD_HEAD, [str(repo)], False, Path(td))
            check("(UTF-8 locale) the pre-fix child already computed the fixed key -> nothing to migrate",
                  head.get("key") == PR.project_key(repo), str(head))
        return
    for utf8 in (True, False):
        with _Home(enc=enc) as h:
            repo = _mk_repo(h.work / "José", "proj")
            head = _run_child(_CHILD_HEAD, [str(repo)], False, h.home)
            k_old = head.get("key")
            k_new = key_of(PR.project_identity(repo))
            check(f"(fixture) the pre-fix child under {enc} computed a DIFFERENT key", k_old and k_old != k_new,
                  str(head))
            h.write_registry({k_old: _entry(head.get("ident", ""), (8787, 8788), (7687, 7474))})
            out = _run_child(_CHILD_NEW, [str(BUILD), str(repo)], utf8, h.home)
            check(f"fixed child (UTF-8 mode {'ON' if utf8 else 'OFF'}) -> 'registry' 7687 under the UTF-8 key",
                  out.get("source") == "registry" and out.get("bolt") == 7687 and out.get("key") == k_new, str(out))
            pj = h.projects()
            check("...and the registry on disk was re-keyed with aliases [legacy]",
                  (pj.get(k_new) or {}).get("aliases") == [k_old] and k_old not in pj, json.dumps(pj))


# ---------------------------------------------------------------- M10
_CHILD_RACE = ("import json, sys, time; sys.path.insert(0, sys.argv[1]); import port_registry as PR; "
               "PR._legacy_encodings = lambda: ['cp1252']; PR._port_in_use = lambda h, p: False; "
               "t = float(sys.argv[3]);\n"
               "while time.time() < t: time.sleep(0.001)\n"
               "print(json.dumps({'key': PR.project_key(sys.argv[2])}))")


def gate_m10_concurrent() -> None:
    print("\n-- M10: two processes migrating at once -> ONE entry, one log line --")
    with _Home() as h:
        repo, ident, k_new, li, k_old = _latin1_fixture(h)
        h.write_registry({k_old: _entry(li, (8789, 8790), (7689, 7476))})
        h.instance(k_old, 7689, 7476, pid=_DEAD_PID)
        env = {k: v for k, v in os.environ.items() if k not in ("NEO4J_URI",)}
        env["DEEPMAP_HOME"] = str(h.home)
        t0 = str(time.time() + 1.5)
        ps = [subprocess.Popen([sys.executable, "-X", "utf8", "-c", _CHILD_RACE, str(BUILD), str(repo), t0],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env) for _ in range(2)]
        outs = []
        for p in ps:
            o, e = p.communicate(timeout=120)
            try:
                outs.append(json.loads(o.decode("utf-8", "replace").strip().splitlines()[-1]).get("key"))
            except Exception:
                outs.append("ERR " + e.decode("utf-8", "replace")[-300:])
        pj = h.projects()
        check("both processes answer the UTF-8 key", outs == [k_new, k_new], str(outs))
        check("exactly one entry (the UTF-8 key, aliases [legacy]) and the instance moved once",
              list(pj) == [k_new] and pj[k_new].get("aliases") == [k_old]
              and (h.home / "instances" / k_new / "data" / "databases" / "marker.txt").is_file()
              and not (h.home / "instances" / k_old).exists(), json.dumps(pj))
        check("logged once", len(h.log_lines()) == 1, h.log_lines())


_CHILD_SLOW = ("import json, sys, time; sys.path.insert(0, sys.argv[1]); import port_registry as PR; "
               "PR._legacy_encodings = lambda: ['cp1252']; PR._port_in_use = lambda h, p: False; "
               "slow = float(sys.argv[4]); real = PR._write_registry\n"
               "def _slow_write(reg, env=None):\n"
               "    time.sleep(slow)\n"
               "    return real(reg, env)\n"
               "if slow: PR._write_registry = _slow_write\n"
               "t = float(sys.argv[3])\n"
               "while time.time() < t: time.sleep(0.001)\n"
               "print(json.dumps({'key': PR.project_key(sys.argv[2])}))")


def gate_m10b_slow_migrator() -> None:
    """M10b: the process that won the lock is SLOW to write the registry (a Windows sharing violation makes
    _write_registry retry for up to _IO_RETRY_S; a loaded box) — a racer that gives up on the lock too early
    answered the LEGACY key although the move then completed, so a provision call in it could recreate an
    empty instances/<old> next to the moved store. The racer must answer the UTF-8 key."""
    print("\n-- M10b: a SLOW migrator (1.6 s registry write) -> the racing process still answers the UTF-8 key --")
    with _Home() as h:
        repo, ident, k_new, li, k_old = _latin1_fixture(h)
        h.write_registry({k_old: _entry(li, (8789, 8790), (7689, 7476))})
        h.instance(k_old, 7689, 7476, pid=_DEAD_PID)
        env = {k: v for k, v in os.environ.items() if k not in ("NEO4J_URI",)}
        env["DEEPMAP_HOME"] = str(h.home)
        t0 = time.time() + 1.5
        args = [(str(t0), "1.6"), (str(t0 + 0.4), "0")]           # the winner is slow; the racer is not
        ps = [subprocess.Popen([sys.executable, "-X", "utf8", "-c", _CHILD_SLOW, str(BUILD), str(repo), t, slow],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env) for t, slow in args]
        outs = []
        for p in ps:
            o, e = p.communicate(timeout=120)
            try:
                outs.append(json.loads(o.decode("utf-8", "replace").strip().splitlines()[-1]).get("key"))
            except Exception:
                outs.append("ERR " + e.decode("utf-8", "replace")[-300:])
        pj = h.projects()
        check("the slow winner and the racer both answer the UTF-8 key (the racer waits for the move)",
              outs == [k_new, k_new], str(outs) + f" new={k_new} old={k_old}")
        check("...one entry, the instance moved once, no instances/<old> left behind",
              list(pj) == [k_new] and not (h.home / "instances" / k_old).exists()
              and (h.home / "instances" / k_new / "data" / "databases" / "marker.txt").is_file(), json.dumps(pj))


# ---------------------------------------------------------------- M11 (ROUND-4 F1)
def gate_m11_owner_predicate() -> None:
    """F1: ONE owner predicate (owner_is_this_project; legacy_owner_verdict + _same_project_path under it).
    Containment is the same project ONLY when both sides map to the same project identity (the git toplevel,
    else the path itself) — or the owner is a NARROWER stamp whose path no longer exists. The review case:
    a git repo `api` inside a non-git DeepMap workspace `work` are two registry projects, yet the containment
    rule let each read / write / adopt the other's legacy graph."""
    print("\n-- M11 (F1): one owner predicate — containment only within one project identity --")
    own = getattr(PR, "owner_is_this_project", None)
    verdict = getattr(PR, "legacy_owner_verdict", None)
    same = getattr(PR, "_same_project_path", None)
    if not (callable(own) and callable(verdict) and callable(same)):
        check("owner_is_this_project / legacy_owner_verdict / _same_project_path exist", False)
        return

    def is_own(root, owner):
        return _try(own, root, owner) is True

    with _Home() as h:
        repo = _mk_repo(h.work, "repo")
        (repo / "pkg" / "deep").mkdir(parents=True)
        nested = _mk_repo(repo, "vendored")                  # its OWN repository inside `repo`
        ws = h.work / "ws"                                   # a non-git DeepMap workspace ...
        ws.mkdir()
        api = _mk_repo(ws, "api")                            # ... holding a git repo (the review's case)
        plain = h.work / "plain"                             # a non-git project with a plain subfolder
        (plain / "src").mkdir(parents=True)
        other = _mk_repo(h.work, "other")
        check("(fixture) api / ws / plain/src / vendored are separate registry projects",
              len({PR.project_key(x) for x in (repo, nested, ws, api, plain, plain / "src")}) == 6)

        # the same project
        check("the project's own identity -> True", is_own(repo, str(repo)))
        check("...forward slashes -> True", is_own(repo, fwd(repo)))
        if os.name == "nt":
            check("...another letter case (Windows) -> True", is_own(repo, str(repo).upper()))
        check("a SUBDIRECTORY session of the repo whose toplevel owns the graph -> True", is_own(repo / "pkg", str(repo)))
        check("an existing NARROWER stamp inside the same repo (an old subdir sync) -> True",
              is_own(repo, str(repo / "pkg" / "deep")))
        check("a NARROWER stamp whose directory no longer exists (a deleted subdir's old sync) -> True",
              is_own(repo, str(repo / "gone" / "sub")))

        # distinct projects that merely contain one another
        check("a NESTED repository's stamp is not the outer repo's -> False", not is_own(repo, str(nested)))
        check("...nor the outer repo's stamp the nested repo's -> False", not is_own(nested, str(repo)))
        check("the review case: the non-git workspace's stamp is not its inner repo's -> False",
              not is_own(api, str(ws)))
        check("...nor the inner repo's stamp the workspace's -> False", not is_own(ws, str(api)))
        check("a non-git folder inside a non-git project is its own project (keyed separately) -> False",
              not is_own(plain / "src", str(plain)) and not is_own(plain, str(plain / "src")))
        check("a VANISHED stamp under a NESTED repo belongs to that repo, not the outer one -> False",
              not is_own(repo, str(nested / "gone" / "sub")))
        check("a VANISHED stamp under a plain folder of the repo is the repo's -> True",
              is_own(repo, str(repo / "pkg" / "gone")))
        check("a disjoint project / a shared string prefix -> False",
              not is_own(repo, str(other)) and not is_own(repo, str(repo) + "x"))
        check("no owner -> False", not is_own(repo, None) and not is_own(repo, ""))

        # the D1 legacy-route policy + the helper provision uses inherit the rule
        ok_sub, _ = verdict(repo / "pkg", str(repo), True)
        ok_api, why_api = verdict(api, str(ws), True)
        ok_ws, _ = verdict(ws, str(api), True)
        check("legacy_owner_verdict: a subdir session of the owner repo -> ok", ok_sub is True)
        check("legacy_owner_verdict: api refuses ws's graph and ws refuses api's (the review case)",
              ok_api is False and ok_ws is False and "/deep-map:setup" in str(why_api), str(why_api))
        check("_same_project_path(this identity, owner record) follows the same rule",
              same(str(repo), str(repo / "pkg")) and not same(str(repo), str(nested))
              and not same(str(api), str(ws)) and same(str(repo), str(repo / "gone")))
        check("...the vanished-path relaxation is for the OWNER side only (a vanished `this` inside a real "
              "owner is not that owner's project)", not same(str(ws / "gone" / "x"), str(ws)))

    with _Home() as h:
        repo, ident, k_new, li, k_old = _latin1_fixture(h)
        check("E1: the garbled stamp of the pre-fix build -> True", is_own(repo, li))
        (repo / "pkg").mkdir(exist_ok=True)
        check("E1: ...from a subdirectory session too", is_own(repo / "pkg", li))
        check("E1: a garbled NARROWER stamp (an old subdir sync, never a real path) -> True",
              is_own(repo, fwd(li) + "/pkg"))
        other = _mk_repo(h.work, "other")
        check("E1: garbled stamps never make another project's identity ours -> False", not is_own(other, li))
        # ROUND-5 G3: the garbled branch applies the F1 rule too — a NESTED repository's pre-fix stamp lies
        # lexically inside the outer repo's garbled identity, but it is mapped back to its real path and
        # judged by project identity (raw containment made the outer repo read / write / adopt it).
        (repo / "libs").mkdir(exist_ok=True)
        core = _mk_repo(repo / "libs", "core")
        g_core = head_identity(core, h.enc)
        check("E1/G3: a NESTED repo's garbled pre-fix stamp is NOT the outer repo's -> False",
              not is_own(repo, g_core) and not is_own(repo, fwd(g_core)))
        ok_n, _ = verdict(repo, g_core, True)
        check("E1/G3: ...legacy_owner_verdict refuses it; the nested repo owns its own garbled stamp",
              ok_n is False and is_own(core, g_core))
        check("E1/G3: a garbled VANISHED stamp under the nested repo is the nested repo's, not the outer's",
              not is_own(repo, fwd(g_core) + "/gone") and is_own(core, fwd(g_core) + "/gone"))
        Path(li).mkdir(parents=True, exist_ok=True)           # a REAL directory by the garbled name
        check("E1: the garbled name is a REAL directory (a project of its own) -> not ours", not is_own(repo, li))


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 68)
    print("DeepMap — non-ASCII project-key migration (ROUND-3 E1)")
    print("=" * 68)
    if not shutil.which("git"):
        print("RESULT: GREEN — 0/0 gates passed. (git not on PATH: skipped)")
        return 0
    gate_m1_legacy_identities()
    gate_m2_full_migration_base_pair()
    gate_m3_stopped_instance_moves()
    gate_m4_running_instance_deferred()
    gate_m4_ports_busy_deferred()
    gate_m5_rename_refused()
    gate_m6_conflict()
    gate_m7_safety()
    gate_m8_decode_crash_subdir()
    gate_m9_real_child()
    gate_m10_concurrent()
    gate_m10b_slow_migrator()
    gate_m11_owner_predicate()
    print("\n" + "=" * 68)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
