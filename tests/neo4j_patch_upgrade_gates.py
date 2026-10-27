#!/usr/bin/env python3
"""The Neo4j 5.26.0 -> 5.26.32 patch upgrade: new stores get the new release, EXISTING stores never lose their graph.

Finding (2026-10-22): DeepMap fetched Neo4j 5.26.0 (netty 4.1.115; CVE-2025-55163 is fixed from 5.26.12 and
CVE-2025-58057 from 5.26.13). Raising DEFAULT_VERSION alone would have orphaned graphs: every store path was derived
from the DEFAULT version, and the base / first project's store LIVES inside its version's folder
(<home>/neo4j/neo4j-community-<v>/data) — a new default makes heal look for it in a folder that has none, and it would
have moved every per-project instance onto another binary behind heal's back.

The fix:
  * versions are STICKY — port_registry.store_neo4j_version is THE rule (NEO4J_VERSION pin > an open upgrade journal >
    the base version record / the instance's conf stamp > the folder holding the base store (5.26.0 first) > the
    default for a store that does not exist yet); provision.paths() and ensure_neo4j resolve through it, so heal and
    the hooks only ever revive the version a store runs;
  * the ONE place a store changes version is provision.upgrade_neo4j (setup by default — DEEPMAP_NEO4J_AUTO_UPGRADE=0
    turns it off — and `provision.py neo4j-upgrade`): only a newer PATCH release, only THIS project's own store,
    stopped through the owner-proven stop, copied first, and kept only when the new release gives the same answers
    (node + relationship counts, owner, ONLINE indexes, one index query) — otherwise the original store is put back
    and the old release restarted; a crash leaves a journal that the next setup finishes or undoes.

Hermetic: a temp DEEPMAP_HOME with fake extracted binaries (5.26.0 + 5.26.32) and a FAKE Neo4j — a Python object that
"serves" whichever store a start pointed it at (a JSON file in the store's data dir) through a fake Bolt driver. Nothing
is downloaded or started; no socket, no process listing. Usage: python tests/neo4j_patch_upgrade_gates.py [--root <tree>]
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PASSES: list = []
FAILS: list = []
OLD, NEW = "5.26.0", "5.26.32"
KEY = "abcdef123456"
BOLT, HTTP = 7689, 7476
# Neo4j's own published SHA-256s (dist.neo4j.org/<archive>.sha256), checked equal to the downloaded archives 2026-10-22
PUBLISHED = {
    "neo4j-community-5.26.32-unix.tar.gz": "d279ae6a9b38034280681a558b0cd222dce89f87cc3d9867f1f7bd28ef5ebc30",
    "neo4j-community-5.26.32-windows.zip": "00eca54f38f601b3adb596611505280a4784d9726c6df5f0b72b7a4a04cc87ec",
    "neo4j-community-5.26.0-unix.tar.gz": "ad8ac3398606145502b8f489530bbd39333707ae4668156af16b6086b5b037d7",
    "neo4j-community-5.26.0-windows.zip": "ea9c98111b72310dab8ae00b7ae4d43df59a1ea82a490579b886bffb3e30de8f",
}
# the 5.26.32 Windows zip's longest member (measured on the real archive) — what the MAX_PATH preflight must cover
LONGEST_5_26_32 = "neo4j-community-5.26.32/lib/netty-transport-native-epoll-4.1.138.Final-linux-aarch_64.jar"
SHIPPED_CONF = ("# shipped defaults (trimmed)\nserver.directories.import=import\nserver.bolt.enabled=true\n"
                "server.https.enabled=false\n#server.memory.heap.max_size=512m\n")


def check(name: str, ok, detail="") -> None:
    ok = bool(ok)
    (PASSES if ok else FAILS).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok or not detail else f" — {str(detail)[:600]}"))


def _section(fn):
    """Run one gate section; an exception (e.g. a missing function on the pre-fix tree) is one FAIL, not a crash."""
    def run(*a, **k):
        try:
            fn(*a, **k)
        except Exception as exc:  # noqa: BLE001
            check(f"{fn.__name__} ran without raising", False, f"{type(exc).__name__}: {exc}")
    run.__name__ = fn.__name__
    return run


class _Patch:
    """Set module attributes for a block, restore them after."""

    def __init__(self, mod, **attrs):
        self.mod, self.attrs, self.saved = mod, attrs, {}

    def __enter__(self):
        for k, v in self.attrs.items():
            self.saved[k] = getattr(self.mod, k, _MISSING)
            setattr(self.mod, k, v)
        return self

    def __exit__(self, *exc):
        for k, v in self.saved.items():
            if v is _MISSING:
                delattr(self.mod, k)
            else:
                setattr(self.mod, k, v)


_MISSING = object()


class _Env:
    def __init__(self, **kv):
        self.kv, self.saved = kv, {}

    def __enter__(self):
        for k, v in self.kv.items():
            self.saved[k] = os.environ.get(k)
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return self

    def __exit__(self, *exc):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _quiet(fn, *a, **k):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        r = fn(*a, **k)
    return r, out.getvalue() + err.getvalue()


# ----------------------------------------------------------------------------------------------- a fake Neo4j
class _Result(list):
    def single(self):
        return self[0] if self else None


class _Session:
    def __init__(self, neo):
        self.neo = neo

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def run(self, q_text, parameters=None, **params):
        q = q_text
        params = dict(parameters or {}, **params)
        g = self.neo.graph()
        if "SHOW INDEXES" in q:
            return _Result([{"name": n, "type": t, "state": s} for n, (t, s) in sorted(g["indexes"].items())])
        if "count(r)" in q:
            return _Result([{"c": g["rels"]}])
        if "_DeepMapMeta" in q and "owning_root" in q:
            return _Result([{"owning_root": g["owner"]}] if g.get("owner") else [])
        if "db.index.fulltext.queryNodes" in q:
            term = str(params.get("q", "")).strip('"')
            return _Result([{"c": sum(1 for n in g["names"] if term and term in n)}])
        if "db.index.vector.queryNodes" in q:
            return _Result([{"c": min(10, g["nodes"])}])
        if "n.embedding IS NOT NULL" in q:
            return _Result([{"e": [0.1, 0.2, 0.3]}])
        if "n.name IS NOT NULL" in q:
            return _Result([{"name": n} for n in g["names"]])
        if "body_summary" in q:
            return _Result([])
        if "count(n)" in q:
            return _Result([{"c": g["nodes"]}])
        raise AssertionError(f"fake Neo4j: unexpected query {q!r}")


class _Driver:
    def __init__(self, neo):
        self.neo = neo

    def verify_connectivity(self):
        if self.neo.running is None:
            raise ConnectionError("fake Neo4j: not running")

    def session(self, **_k):
        return _Session(self.neo)

    def close(self):
        pass


class FakeNeo:
    """One fake Neo4j per test home: `running` = (version, project_key) it serves; the graph is read from the store it
    was started on (<data>/databases/neo4j/fake.json). A version in `bad` answers with one node fewer; one in `nostart`
    never answers (its JVM 'runs' — a stop still finds it); `stop_out[version]` forces a stop outcome."""

    def __init__(self, P):
        self.P = P
        self.running = None
        self.zombie = None
        self.bad, self.nostart, self.stop_out = set(), set(), {}
        self.calls: list = []

    def data(self, version, key):
        return self.P.paths(version, key)["data"]

    def graph(self):
        if self.running is None:
            raise ConnectionError("fake Neo4j: not running")
        v, key = self.running
        g = json.loads((self.data(v, key) / "databases" / "neo4j" / "fake.json").read_text(encoding="utf-8"))
        if v in self.bad:
            g = dict(g, nodes=g["nodes"] - 1)
        return g

    # ---- the provision seams
    def start_neo4j(self, version, project_key=None):
        self.calls.append(("start", version, project_key))
        d = self.data(version, project_key)
        if version in self.nostart:
            self.zombie = (version, project_key)
            return 4242
        if not (d / "databases" / "neo4j" / "fake.json").is_file():
            self.zombie = (version, project_key)   # a JVM over a store that is not there: it never answers
            return 4242
        (d / f"touched-by-{version}").write_text("opened", encoding="utf-8")   # a release that opens a store marks it
        self.running = (version, project_key)
        return 4242

    def stop_neo4j(self, version=None, project_key=None):
        version = version or self.P.store_neo4j_version(project_key)
        self.calls.append(("stop", version, project_key))
        forced = self.stop_out.get(version)
        if forced:
            return self.P._stop_done(forced, False)
        live = self.running or self.zombie
        # like the real stop: an instance has ONE pid file whatever release runs; the base store's pid file and conf
        # dir live in its version's folder, so stop_neo4j(<v>, None) finds only a base JVM of release <v>
        if live is None or (project_key is None and live[0] != version):
            return self.P._stop_done("nothing", False)
        self.running = self.zombie = None
        return self.P._stop_done("stopped", True)

    def is_bolt_up(self, uri, user, password, timeout=4.0):
        return self.running is not None

    def bolt_state(self, uri, user, password, timeout=4.0):
        return "up" if self.running is not None else "down"

    def wait_for_bolt(self, uri, user, password, timeout=120):
        return self.running is not None

    def make_driver(self, uri, user, password):
        return _Driver(self)

    def download(self, version):
        self.calls.append(("download", version))
        _binary(self.P, version)

    def seams(self, **extra):
        P = self.P
        base = dict(start_neo4j=self.start_neo4j, stop_neo4j=self.stop_neo4j, is_bolt_up=self.is_bolt_up,
                    _bolt_state=self.bolt_state, wait_for_bolt=self.wait_for_bolt, _make_driver=self.make_driver,
                    _download_extract_shared=self.download, _java_preflight_problems=lambda: [],
                    _disk_free_gb=lambda p: 1000.0, _port_in_use=lambda h, p: False,
                    _find_neo4j_server_pids=lambda conf: [], _record_neo4j_server_identity=lambda v, k: None,
                    _win_long_paths_enabled=lambda: True, _legacy_base_instance_alive=lambda: True,
                    _USER_SET_NEO4J_URI=False, _UPGRADE_LOCK_WAIT_S=0.3)
        base.update(extra)
        return _Patch(P, **base)


# ----------------------------------------------------------------------------------------------- fixtures
def _binary(P, version):
    """A fake extracted Neo4j <version> under the temp home (bin/ + lib/ + conf/neo4j.conf + the sentinel)."""
    nh = P.paths(version)["neo4j_home"]
    for d in ("bin", "lib", "conf"):
        (nh / d).mkdir(parents=True, exist_ok=True)
    (nh / "bin" / ("neo4j.bat" if os.name == "nt" else "neo4j")).write_text("x", encoding="utf-8")
    conf = nh / "conf" / "neo4j.conf"
    if not conf.exists():
        conf.write_text(SHIPPED_CONF, encoding="utf-8")
    (nh / "conf" / "server-logs.xml").write_text(f"<logs v='{version}'/>", encoding="utf-8")
    (nh / ".deepmap-extracted").write_text(version, encoding="utf-8")
    return nh


def _store(data: Path, owner, *, nodes=12, rels=30) -> Path:
    """A fake store: databases/neo4j/fake.json (+ the other dirs a real store has)."""
    g = {"nodes": nodes, "rels": rels, "owner": owner,
         "names": ["parse_config", "load_graph", "RouteTable", "parse_args"],
         "indexes": {"deepmap_code_fts": ["FULLTEXT", "ONLINE"], "deepmap_function_embedding": ["VECTOR", "ONLINE"],
                     "deepmap_key_function": ["RANGE", "ONLINE"]}}
    (data / "databases" / "neo4j").mkdir(parents=True, exist_ok=True)
    (data / "databases" / "system").mkdir(parents=True, exist_ok=True)
    (data / "transactions" / "neo4j").mkdir(parents=True, exist_ok=True)
    (data / "dbms").mkdir(parents=True, exist_ok=True)
    (data / "databases" / "neo4j" / "fake.json").write_text(json.dumps(g), encoding="utf-8")
    (data / "transactions" / "neo4j" / "neostore.transaction.db.0").write_bytes(b"\0" * 2048)
    (data / "dbms" / "auth.ini").write_text("neo4j:deepmap", encoding="utf-8")
    return data


class _Home:
    """A temp DEEPMAP_HOME + a temp project; the per-project / version env cleared; restored on exit."""
    ENV = ("DEEPMAP_HOME", "NEO4J_URI", "NEO4J_VERSION", "DEEPMAP_NEO4J_SHA256", "DEEPMAP_NEO4J_AUTO_UPGRADE",
           "DEEPMAP_SEARCH_PORT", "DEEPMAP_EMBED_PORT", "DEEPMAP_PROJECT_ROOT", "DEEPMAP_PROJECT_KEY")

    def __init__(self, P):
        self.P = P

    def __enter__(self):
        self._td = tempfile.TemporaryDirectory(prefix="dm_n4jup_")
        base = Path(self._td.name)
        self.home, self.proj = base / "home", base / "proj"
        self.proj.mkdir()
        (self.proj / "app.py").write_text("def main():\n    return 1\n", encoding="utf-8")
        self._saved = {k: os.environ.get(k) for k in self.ENV}
        for k in self.ENV:
            os.environ.pop(k, None)
        os.environ["DEEPMAP_HOME"] = str(self.home)
        self.home.mkdir()
        import port_registry as PR
        self.owner = PR.project_identity(str(self.proj))
        return self

    def __exit__(self, *exc):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        try:
            self._td.cleanup()
        except Exception:
            shutil.rmtree(self._td.name, ignore_errors=True)

    def instance(self, version=OLD, owner="self", nodes=12):
        """Instance KEY on `version`: its conf seeded by the REAL configure_neo4j (stamp = version), a store."""
        P = self.P
        _binary(P, OLD)
        _binary(P, NEW)
        _quiet(P.configure_neo4j, version, project_key=KEY, bolt=BOLT, http=HTTP)
        _store(P.paths(version, KEY)["data"], self.owner if owner == "self" else owner, nodes=nodes)
        return P.paths(version, KEY)["data"]

    def base(self, version=OLD, owner="self", nodes=12, record=True):
        """The legacy base store inside <home>/neo4j/neo4j-community-<version>/data (+ its owner record)."""
        P = self.P
        _binary(P, OLD)
        _binary(P, NEW)
        data = _store(P.paths(version)["data"], self.owner if owner == "self" else owner, nodes=nodes)
        if record and owner:
            (data / "deepmap_base_owner.json").write_text(json.dumps({"root": self.owner if owner == "self" else owner}),
                                                          encoding="utf-8")
        return data


def _tree(d: Path) -> dict:
    """{relative path: bytes} of every file under d (the store's exact content)."""
    out = {}
    for p in sorted(Path(d).rglob("*")):
        if p.is_file():
            out[p.relative_to(d).as_posix()] = p.read_bytes()
    return out


def _siblings(d: Path, prefix: str) -> list:
    return sorted(x.name for x in Path(d).parent.iterdir() if x.name.startswith(prefix))


# ----------------------------------------------------------------------------------------------- A: versions + hashes
@_section
def gate_a_versions_and_hashes(P, PR) -> None:
    print("\n-- A: the default is the latest 5.26 patch; both archives of both releases are pinned --")
    check("DEFAULT_VERSION is 5.26.32", P.DEFAULT_VERSION == NEW, P.DEFAULT_VERSION)
    check("PREPIN_VERSION (what builds <= 0.29.0 provisioned) is 5.26.0", getattr(P, "PREPIN_VERSION", None) == OLD)
    for name, digest in PUBLISHED.items():
        check(f"KNOWN_SHA256[{name}] == Neo4j's published .sha256", P.KNOWN_SHA256.get(name) == digest,
              P.KNOWN_SHA256.get(name))
    for v in (OLD, NEW):
        for win in (True, False):
            check(f"expected_sha256({v}, windows={win}) is the pinned hash",
                  P.expected_sha256(v, win) == PUBLISHED[P.dist_filename(v, win)])
    check("port_registry's default == provision.DEFAULT_VERSION (drift lock)",
          getattr(PR, "_BASE_NEO4J_DEFAULT_VERSION", None) == P.DEFAULT_VERSION)
    check("port_registry's pre-pin version == provision.PREPIN_VERSION (drift lock)",
          getattr(PR, "NEO4J_PREPIN_VERSION", None) == getattr(P, "PREPIN_VERSION", "?"))
    check("the MAX_PATH preflight covers the 5.26.32 zip's longest member (89 characters)",
          P._NEO4J_ARCHIVE_LONGEST >= len(LONGEST_5_26_32) == 89, f"{P._NEO4J_ARCHIVE_LONGEST} vs {len(LONGEST_5_26_32)}")
    with _Env(NEO4J_VERSION=None):
        check("neo4j_version() (what a NEW store fetches) is the default", P.neo4j_version() == NEW)


# ----------------------------------------------------------------------------------------------- B0: no orphaned store
@_section
def gate_b0_existing_paths_unchanged(P, PR) -> None:
    print("\n-- B0: after the default moved on, every path still finds the store a 5.26.0 install holds --")
    with _Home(P) as h:
        data = h.base(OLD)
        check("a 5.26.0 base store: paths(None)['data'] still IS that store (not an empty 5.26.32 folder)",
              Path(P.paths()["data"]) == data, P.paths()["data"])
        check("...its pid file / conf are the 5.26.0 folder's (stop, hibernate, the H3b owner read)",
              Path(P.paths()["pidfile"]).parent.parent == data.parent and Path(P.paths()["conf"]).parent.parent
              == data.parent)
        check("...the hooks' base owner record path finds its record",
              Path(PR.base_owner_record_path()) == data / "deepmap_base_owner.json" and PR.base_owner_record() == h.owner,
              PR.base_owner_record_path())
    with _Home(P) as h:
        h.instance(OLD)
        check("a 5.26.0 instance: paths(None, key) runs the 5.26.0 binary (heal starts what the store was made with)",
              P.paths(None, KEY)["neo4j_home"].name == f"neo4j-community-{OLD}", P.paths(None, KEY)["neo4j_home"])


# ----------------------------------------------------------------------------------------------- B: sticky resolution
@_section
def gate_b_sticky_resolution(P, PR) -> None:
    print("\n-- B: an existing store keeps the version it runs; a new store gets the default --")
    with _Home(P) as h:
        check("empty home: the base store resolves to the default", P.store_neo4j_version(None) == NEW)
        check("empty home: a new instance resolves to the default", P.store_neo4j_version(KEY) == NEW)
        check("empty home: paths(None) is the default's folder",
              P.paths()["neo4j_home"].name == f"neo4j-community-{NEW}")
    with _Home(P) as h:
        data = h.base(OLD)
        check("a 5.26.0 base store: the base resolves to 5.26.0 (the store is found, not orphaned)",
              P.store_neo4j_version(None) == OLD, P.store_neo4j_version(None))
        check("...paths(None)['data'] IS that store", Path(P.paths()["data"]) == data)
        check("...provision's base owner record path is inside it", Path(P._base_owner_path()).parent == data)
        check("...port_registry's (the hooks') base owner record path is the same file",
              Path(PR.base_owner_record_path()) == Path(P._base_owner_path()))
        check("...and the hooks read its owner", PR.base_owner_record() == h.owner, PR.base_owner_record())
        check("...a new per-project instance still gets the default", P.store_neo4j_version(KEY) == NEW)
    with _Home(P) as h:
        _binary(P, OLD)
        (P.paths(OLD)["data"]).mkdir(parents=True, exist_ok=True)
        (P.paths(OLD)["data"] / "deepmap_base_owner.json").write_text(json.dumps({"root": "C:/w/x"}), encoding="utf-8")
        check("a 5.26.0 folder holding only the base owner record (lives next to the store) -> 5.26.0",
              P.store_neo4j_version(None) == OLD)
    with _Home(P) as h:
        h.base(OLD)
        _store(P.paths(NEW)["data"], h.owner)
        check("stores in both folders, no record -> 5.26.0 (every build <= 0.29.0 created it there)",
              P.store_neo4j_version(None) == OLD)
        (h.home / "neo4j" / "deepmap-base-version.json").write_text(json.dumps({"version": NEW}), encoding="utf-8")
        check("...the base version record (an upgrade wrote it) decides -> 5.26.32", P.store_neo4j_version(None) == NEW)
        (h.home / "neo4j" / "deepmap-base-version.json").write_text(json.dumps({"version": "5.26.9"}), encoding="utf-8")
        check("...a record naming a version whose folder is gone is ignored", P.store_neo4j_version(None) == OLD)
    with _Home(P) as h:
        _binary(P, NEW)
        _store(P.paths(NEW)["data"], h.owner)
        check("a base store only in the 5.26.32 folder -> 5.26.32", P.store_neo4j_version(None) == NEW)
        _store(P.paths("5.26.20")["data"], h.owner)
        check("stores in 5.26.20 and 5.26.32, no 5.26.0, no record -> the newest", P.store_neo4j_version(None) == NEW)
    with _Home(P) as h:
        for bad in ("neo4j-community-5.26.0-old", "neo4j-community-latest", "neo4j-community-5.26"):
            _store(h.home / "neo4j" / bad / "data", h.owner)
        (h.home / "neo4j" / "neo4j-community-5.26.7").write_text("a file, not a folder", encoding="utf-8")
        check("folders that are not neo4j-community-<X.Y.Z> (and a FILE of that name) never count -> default",
              P.store_neo4j_version(None) == NEW, P.store_neo4j_version(None))
    with _Home(P) as h:
        h.instance(OLD)
        check("an instance whose conf was seeded from 5.26.0, with a store -> 5.26.0", P.store_neo4j_version(KEY) == OLD)
        check("...paths(None, key) uses the 5.26.0 binary",
              P.paths(None, KEY)["neo4j_home"].name == f"neo4j-community-{OLD}")
        check("...its data dir is the instance's own", P.paths(None, KEY)["data"] == h.home / "instances" / KEY / "data")
        stamp = P.paths(OLD, KEY)["conf_dir"] / ".seeded-version"
        stamp.unlink()
        check("...no stamp (an instance from before it) -> 5.26.0", P.store_neo4j_version(KEY) == OLD)
        stamp.write_text("../../x", encoding="utf-8")
        check("...an unclean stamp is never a version -> 5.26.0", P.store_neo4j_version(KEY) == OLD)
        stamp.unlink()
        shutil.rmtree(P.paths(OLD)["neo4j_home"])
        check("...no stamp and no 5.26.0 binary here (only 5.26.32) -> the binary that is here",
              P.store_neo4j_version(KEY) == NEW, P.store_neo4j_version(KEY))
        _binary(P, OLD)
        stamp.write_text(NEW, encoding="utf-8")
        check("...stamp 5.26.32 -> 5.26.32", P.store_neo4j_version(KEY) == NEW)
        shutil.rmtree(P.paths(NEW, KEY)["data"] / "databases")
        stamp.write_text(OLD, encoding="utf-8")
        check("...a stamp but NO store (a first setup that never started) -> the default",
              P.store_neo4j_version(KEY) == NEW)
    with _Home(P) as h:
        h.instance(OLD)
        h.base(OLD)
        with _Env(NEO4J_VERSION="5.26.5"):
            check("NEO4J_VERSION pins an instance store (a NEWER release than it runs)",
                  P.store_neo4j_version(KEY) == "5.26.5")
            check("...but never points the base at a folder holding no store while 5.26.0's holds it (ignored)",
                  P.store_neo4j_version(None) == OLD, P.store_neo4j_version(None))
        _store(P.paths("5.26.5")["data"], h.owner)
        with _Env(NEO4J_VERSION="5.26.5"):
            check("NEO4J_VERSION pins the base when its folder holds a base store", P.store_neo4j_version(None) == "5.26.5")
        with _Env(NEO4J_VERSION="../../evil"):
            check("an unclean NEO4J_VERSION is ignored: the stores keep their versions",
                  P.store_neo4j_version(KEY) == OLD and P.store_neo4j_version(None) == OLD)
        for key in (KEY, None):
            jp = PR.upgrade_journal_path(key)
            jp.parent.mkdir(parents=True, exist_ok=True)
            jp.write_text(json.dumps({"from": OLD, "to": NEW, "phase": "started"}), encoding="utf-8")
            if key:
                (P.paths(OLD, KEY)["conf_dir"] / ".seeded-version").write_text(NEW, encoding="utf-8")
            check(f"an open upgrade journal (phase started, {'instance' if key else 'base'}) -> still 5.26.0",
                  P.store_neo4j_version(key) == OLD)
            jp.write_text(json.dumps({"from": OLD, "to": NEW, "phase": "verified"}), encoding="utf-8")
            check(f"...phase verified -> 5.26.32 ({'instance' if key else 'base'})", P.store_neo4j_version(key) == NEW)
            jp.write_text(json.dumps({"from": "../x", "to": NEW, "phase": "started"}), encoding="utf-8")
            check(f"...a journal with an unclean version is ignored ({'instance' if key else 'base'})",
                  PR.read_upgrade_journal(key) is None)
            jp.unlink()


# ----------------------------------------------------------------------------------------------- C: heal never upgrades
@_section
def gate_c_heal_and_ensure_never_upgrade(P, PR) -> None:
    print("\n-- C: ensure_neo4j / heal revive the version a store runs; they never upgrade --")

    class Rec:
        def __init__(self):
            self.calls = []

    for layout in ("instance", "base"):
        with _Home(P) as h:
            key = KEY if layout == "instance" else None
            h.instance(OLD) if key else h.base(OLD)
            neo = FakeNeo(P)
            boom = Rec()

            def no_upgrade(*a, **k):
                boom.calls.append((a, k))
                return "upgraded"

            cfg = []
            real_configure = P.configure_neo4j
            with neo.seams(preflight=lambda *a, **k: True, set_initial_password=lambda *a, **k: True,
                           _missing_modules=lambda names: [], upgrade_neo4j=no_upgrade,
                           recover_neo4j_upgrade=lambda *a, **k: boom.calls.append(("recover", a, k)),
                           configure_neo4j=lambda v, **k: cfg.append(v) or real_configure(v, **k)):
                for create in (False, True):
                    neo.calls.clear()
                    del cfg[:]
                    ok, _ = _quiet(P.ensure_neo4j, uri=f"bolt://127.0.0.1:{BOLT if key else 7687}", project_key=key,
                                   bolt=BOLT if key else None, http=HTTP if key else None, create=create)
                    started = [c for c in neo.calls if c[0] == "start"]
                    check(f"{layout}: ensure_neo4j(create={create}) starts 5.26.0 on the 5.26.0 store",
                          ok and started == [("start", OLD, key)] and cfg == [OLD], f"{ok} {neo.calls} {cfg}")
                    neo.running = None
                # heal itself, end to end over the same seams
                neo.calls.clear()
                pair = (BOLT, HTTP) if key else (7687, 7474)
                with _Patch(P, _resolve_ports=lambda t, **k: ((8787, 8788), None, None),
                            neo4j_route_source=lambda: "registry",
                            resolved_instance=lambda t: (KEY, BOLT, HTTP) if key else None,
                            _foreign_base_claim_problem=lambda *a, **k: None, _failed_build_reason=lambda t: None,
                            _is_base_claim_route=lambda *a, **k: False, _read_setup_blocker=lambda t: None,
                            start_mcp_servers=lambda **k: True, _ensure_mcp_json=lambda t: None):
                    with _Env(NEO4J_URI=f"bolt://127.0.0.1:{pair[0]}"):
                        ok, out = _quiet(P.heal, str(h.proj))
                check(f"{layout}: heal revives 5.26.0 (never the new default)",
                      ok and [c for c in neo.calls if c[0] == "start"] == [("start", OLD, key)], f"{ok} {neo.calls}")
                check(f"{layout}: heal never runs the upgrade nor its recovery", boom.calls == [], str(boom.calls))
                check(f"{layout}: heal downloads nothing new", ("download", NEW) not in neo.calls, str(neo.calls))
                check(f"{layout}: the store still resolves to 5.26.0 after heal", P.store_neo4j_version(key) == OLD)
    with _Home(P) as h:
        _binary(P, OLD)
        neo = FakeNeo(P)
        with neo.seams(preflight=lambda *a, **k: True, set_initial_password=lambda *a, **k: True,
                       _missing_modules=lambda names: []):
            _quiet(P.ensure_neo4j, uri=f"bolt://127.0.0.1:{BOLT}", project_key=KEY, bolt=BOLT, http=HTTP)
        check("a NEW project's instance (no store yet) is provisioned on the new default",
              ("download", NEW) in neo.calls and [c for c in neo.calls if c[0] == "start"][:1] == [("start", NEW, KEY)],
              str(neo.calls))


# ----------------------------------------------------------------------------------------------- D: the decision
@_section
def gate_d_upgrade_decision(P, PR) -> None:
    print("\n-- D: what counts as an upgrade (a newer PATCH of the same line only) and when one is offered --")
    t = P.neo4j_upgrade_target
    check("5.26.0 -> 5.26.32", t(OLD) == NEW)
    check("5.26.0 -> --to 5.26.31", t(OLD, "5.26.31") == "5.26.31")
    for cur, to, why in (("5.26.32", None, "already the default"), ("5.26.40", None, "never a downgrade"),
                         (OLD, "5.27.0", "a new minor is not a patch"), (OLD, "2025.01.0", "nor a calendar release"),
                         (OLD, "../../x", "an unclean version"), ("4.4.10", None, "another line"),
                         ("5.25.1", None, "another minor"), (OLD, OLD, "the same release"),
                         ("x.y.z", None, "an unparseable current")):
        check(f"not an upgrade: {cur} -> {to or 'default'} ({why})", t(cur, to) is None, t(cur, to))
    with _Home(P) as h:
        h.instance(OLD)
        h.base(OLD)
        check("an instance store on 5.26.0 -> (5.26.0, 5.26.32) available",
              P.neo4j_upgrade_available(KEY) == (OLD, NEW))
        check("the base store on 5.26.0 -> available", P.neo4j_upgrade_available(None) == (OLD, NEW))
        note = P.neo4j_upgrade_note(KEY) or ""
        check("the status note names both versions, /deep-map:setup and the verb",
              OLD in note and NEW in note and "/deep-map:setup" in note and "neo4j-upgrade" in note, note)
        with _Env(DEEPMAP_NEO4J_AUTO_UPGRADE="0"):
            note0 = P.neo4j_upgrade_note(KEY) or ""
            check("...with DEEPMAP_NEO4J_AUTO_UPGRADE=0 it says setup will not", "DEEPMAP_NEO4J_AUTO_UPGRADE=0" in note0)
        with _Env(NEO4J_VERSION=OLD):
            check("NEO4J_VERSION pins the store -> nothing offered", P.neo4j_upgrade_available(KEY) is None)
        jp = PR.upgrade_journal_path(KEY)
        jp.write_text(json.dumps({"from": OLD, "to": NEW, "phase": "copying"}), encoding="utf-8")
        check("an open journal -> nothing offered (setup recovers it first)", P.neo4j_upgrade_available(KEY) is None)
        jp.unlink()
    with _Home(P) as h:
        check("no store -> nothing offered (a new store gets the default anyway)",
              P.neo4j_upgrade_available(KEY) is None and P.neo4j_upgrade_available(None) is None)
    # setup's step: auto by default, a NOTE only when switched off, never for an operator endpoint
    with _Home(P) as h:
        h.instance(OLD)
        calls = []
        with _Patch(P, upgrade_neo4j=lambda *a, **k: calls.append(k) or "upgraded", _bolt_state=lambda *a, **k: "up",
                    _USER_SET_NEO4J_URI=False):
            r, _ = _quiet(P._setup_neo4j_upgrade, str(h.proj), f"bolt://127.0.0.1:{BOLT}", "neo4j", "deepmap",
                          (KEY, BOLT, HTTP))
            check("setup: an older patch -> upgrade_neo4j runs once (default on)", r is True and len(calls) == 1,
                  str(calls))
            del calls[:]
            with _Env(DEEPMAP_NEO4J_AUTO_UPGRADE="0"):
                r, out = _quiet(P._setup_neo4j_upgrade, str(h.proj), f"bolt://127.0.0.1:{BOLT}", "neo4j", "deepmap",
                                (KEY, BOLT, HTTP))
            check("setup with DEEPMAP_NEO4J_AUTO_UPGRADE=0: no upgrade, a NOTE naming the release",
                  r is True and calls == [] and NEW in out, out[-300:])
            r, _ = _quiet(P._setup_neo4j_upgrade, str(h.proj), "bolt://db.example.com:7687", "neo4j", "deepmap", None)
            check("setup on a non-local base URI: no upgrade", r is True and calls == [])
        with _Patch(P, upgrade_neo4j=lambda *a, **k: calls.append(k) or "upgraded", _bolt_state=lambda *a, **k: "up",
                    _USER_SET_NEO4J_URI=True):
            r, _ = _quiet(P._setup_neo4j_upgrade, str(h.proj), f"bolt://127.0.0.1:{BOLT}", "neo4j", "deepmap",
                          (KEY, BOLT, HTTP))
            check("setup with an operator NEO4J_URI: no upgrade", r is True and calls == [])
        with _Patch(P, upgrade_neo4j=lambda *a, **k: "rollback-failed", _bolt_state=lambda *a, **k: "down",
                    ensure_neo4j=lambda **k: calls.append(("ensure", k.get("create"))) or True,
                    _USER_SET_NEO4J_URI=False):
            r, _ = _quiet(P._setup_neo4j_upgrade, str(h.proj), f"bolt://127.0.0.1:{BOLT}", "neo4j", "deepmap",
                          (KEY, BOLT, HTTP))
            check("setup: a failed rollback that left Neo4j down -> the store is revived (create=False), setup goes on",
                  r is True and ("ensure", False) in calls, str(calls))
    # the store diff — shapes that must and must NOT count as "another graph"
    base = {"nodes": 5, "rels": 7, "owner": "C:/p", "online": ["a", "b"],
            "probe": {"kind": "fulltext", "index": "a", "term": "x", "hits": 2}}
    check("same answers -> same graph", P._store_differs(base, dict(base)) is None)
    check("an extra ONLINE index afterwards is not a difference",
          P._store_differs(base, dict(base, online=["a", "b", "c"])) is None)
    check("no probe before (no index) -> the probe is not compared",
          P._store_differs(dict(base, probe=None), dict(base, probe=None)) is None)
    for field, val in (("nodes", 4), ("rels", 8), ("owner", "C:/q"), ("online", ["a"]),
                       ("probe", dict(base["probe"], hits=1)), ("probe", None)):
        check(f"a different {field} -> another graph", P._store_differs(base, dict(base, **{field: val})) is not None)


# ----------------------------------------------------------------------------------------------- E: per-instance upgrade
def _up(P, h, **k):
    rep: dict = {}
    out, log = _quiet(P.upgrade_neo4j, str(h.proj), uri=f"bolt://127.0.0.1:{k.pop('bolt', BOLT)}", user="neo4j",
                      password="deepmap", inst=k.pop("inst", (KEY, BOLT, HTTP)), report=rep, **k)
    return out, rep, log


@_section
def gate_e_instance_upgrade(P, PR) -> None:
    print("\n-- E: a per-project instance moves to 5.26.32 only when 5.26.32 serves the same graph --")
    # E1 happy path
    with _Home(P) as h:
        data = h.instance(OLD)
        original = _tree(data)
        neo = FakeNeo(P)
        neo.running = (OLD, KEY)
        with neo.seams():
            out, rep, log = _up(P, h)
        check("E1 upgraded", out == "upgraded", f"{out} {rep} {log[-500:]}")
        check("E1 the instance now runs 5.26.32 (its conf re-seeded from 5.26.32: the stamp)",
              P.store_neo4j_version(KEY) == NEW and
              (P.paths(NEW, KEY)["conf_dir"] / ".seeded-version").read_text(encoding="utf-8").strip() == NEW)
        check("E1 its conf carries the shipped 5.26.32 files", "v='5.26.32'" in
              (P.paths(NEW, KEY)["conf_dir"] / "server-logs.xml").read_text(encoding="utf-8"))
        check("E1 ...and keeps its own ports", PR.instance_conf_ports(KEY) == (BOLT, HTTP), PR.instance_conf_ports(KEY))
        after = _tree(data)
        check("E1 the graph is the same store, opened in place by 5.26.32",
              {k: v for k, v in after.items() if not k.startswith("touched-by")} == original
              and "touched-by-5.26.32" in after)
        check("E1 the pre-upgrade backup is gone, no journal left",
              _siblings(data, "data.") == [] and PR.read_upgrade_journal(KEY) is None, _siblings(data, "data."))
        seq = [c for c in neo.calls if c[0] in ("download", "stop", "start")]
        check("E1 order: download 5.26.32 BEFORE stopping 5.26.0, then start 5.26.32",
              seq == [("download", NEW), ("stop", OLD, KEY), ("start", NEW, KEY)], str(seq))
        check("E1 5.26.32 serves", neo.running == (NEW, KEY))
        out2, rep2, _ = _up(P, h)
        check("E1 a second run: already current, nothing stopped", out2 == "current" and
              [c for c in neo.calls if c[0] == "stop"] == [("stop", OLD, KEY)], f"{out2} {neo.calls}")
    # E2/E3 the new release does not serve the same graph / does not start -> rolled back
    for label, setup_fn in (("serves another graph", lambda n: n.bad.add(NEW)),
                            ("never answers", lambda n: n.nostart.add(NEW))):
        with _Home(P) as h:
            data = h.instance(OLD)
            original = _tree(data)
            neo = FakeNeo(P)
            neo.running = (OLD, KEY)
            setup_fn(neo)
            with neo.seams(_UPGRADE_START_TIMEOUT_S=1, _UPGRADE_INDEX_WAIT_S=0):
                out, rep, log = _up(P, h)
            check(f"E2 5.26.32 {label} -> rolled-back", out == "rolled-back", f"{out} {rep} {log[-400:]}")
            check(f"E2 ({label}) the instance is back on 5.26.0 (stamp re-seeded)", P.store_neo4j_version(KEY) == OLD)
            after = _tree(data)
            check(f"E2 ({label}) the ORIGINAL store is back (nothing 5.26.32 opened survives)",
                  {k: v for k, v in after.items() if not k.startswith("touched-by")} == original
                  and "touched-by-5.26.32" not in after, sorted(after)[:8])
            check(f"E2 ({label}) 5.26.0 serves again", neo.running == (OLD, KEY))
            check(f"E2 ({label}) no backup / failed copy / journal left",
                  _siblings(data, "data.") == [] and PR.read_upgrade_journal(KEY) is None, _siblings(data, "data."))
            check(f"E2 ({label}) the reason is reported", bool(rep.get("reason")), rep)
    # E4 the stop can't be proven -> nothing changes
    with _Home(P) as h:
        data = h.instance(OLD)
        original = _tree(data)
        neo = FakeNeo(P)
        neo.running = (OLD, KEY)
        neo.stop_out[OLD] = "unverified"
        with neo.seams():
            out, rep, _ = _up(P, h)
        check("E4 a stop that can't be proven this instance's -> skipped", out == "skipped", f"{out} {rep}")
        check("E4 ...nothing changed (store, version, no journal, no copy, still serving 5.26.0)",
              _tree(data) == original and P.store_neo4j_version(KEY) == OLD and PR.read_upgrade_journal(KEY) is None
              and _siblings(data, "data.") == [] and neo.running == (OLD, KEY))
    # E5-E9, E12, E13: refusals before anything stops
    refusals = (
        ("E5 NEO4J_VERSION pins it", dict(env={"NEO4J_VERSION": OLD})),
        ("E6 an operator NEO4J_URI", dict(seams={"_USER_SET_NEO4J_URI": True})),
        ("E9 the graph is another project's", dict(owner="C:/elsewhere/other-project")),
        ("E12 the download fails", dict(seams={"_download_extract_shared": "raise"})),
        ("E13 not enough disk", dict(seams={"_disk_free_gb": lambda p: 0.0})),
        ("E13b the copy would exceed MAX_PATH", dict(seams={"is_windows": lambda: True,
                                                            "_win_long_paths_enabled": lambda: False,
                                                            "_store_size": lambda d, **k: (1000, 250)})),
        ("E13c no usable Java", dict(seams={"_java_preflight_problems": lambda: [("no java", "no Java on PATH")]})),
        ("E13d an unclean --to", dict(kw={"to_version": "../5.26.40"})),
        ("E13e --to a new minor", dict(kw={"to_version": "5.27.0"})),
    )
    for label, spec in refusals:
        with _Home(P) as h:
            data = h.instance(OLD, owner=spec.get("owner", "self"))
            original = _tree(data)
            neo = FakeNeo(P)
            neo.running = (OLD, KEY)
            seams = dict(spec.get("seams", {}))
            if seams.get("_download_extract_shared") == "raise":
                def _raise(v):
                    raise OSError("network down")
                seams["_download_extract_shared"] = _raise
            with neo.seams(**seams), _Env(**spec.get("env", {})):
                out, rep, _ = _up(P, h, **spec.get("kw", {}))
            check(f"{label} -> skipped with a reason", out == "skipped" and rep.get("reason"), f"{out} {rep}")
            check(f"{label}: nothing stopped, store and version unchanged",
                  not [c for c in neo.calls if c[0] in ("stop", "start")] and _tree(data) == original
                  and P.store_neo4j_version(KEY) == (OLD if "NEO4J_VERSION" not in spec.get("env", {}) else OLD)
                  and PR.read_upgrade_journal(KEY) is None, str(neo.calls))
    # E8 a sync holds the per-instance write lock
    with _Home(P) as h:
        data = h.instance(OLD)
        neo = FakeNeo(P)
        neo.running = (OLD, KEY)
        import sync as S
        held = S._acquire_instance_lock(f"bolt://127.0.0.1:{BOLT}")
        try:
            with neo.seams():
                out, rep, _ = _up(P, h)
        finally:
            S._release_sync_lock(held)
        check("E8 a sync is writing (its per-instance lock is held) -> skipped, nothing stopped",
              held is not None and out == "skipped" and "sync" in str(rep.get("reason"))
              and not [c for c in neo.calls if c[0] == "stop"], f"{out} {rep}")
        with neo.seams():
            out, rep, _ = _up(P, h)
        check("E8 ...once it is released the upgrade runs", out == "upgraded", f"{out} {rep}")
    # E8b another DeepMap process holds the instance's start lock
    with _Home(P) as h:
        h.instance(OLD)
        neo = FakeNeo(P)
        neo.running = (OLD, KEY)
        lk, _how = P._file_lock(h.home / "run" / f"neo4j-start-{KEY}.lock", 1)
        try:
            with neo.seams():
                out, rep, _ = _up(P, h)
        finally:
            P._file_unlock(lk)
        check("E8b a start / heal holds the instance's start lock -> skipped, nothing stopped",
              lk is not None and out == "skipped" and not [c for c in neo.calls if c[0] == "stop"], f"{out} {rep}")
    # E7 already current
    with _Home(P) as h:
        h.instance(NEW)
        neo = FakeNeo(P)
        neo.running = (NEW, KEY)
        with neo.seams():
            out, rep, _ = _up(P, h)
        check("E7 a store already on 5.26.32 -> current, nothing touched", out == "current" and neo.calls == [],
              f"{out} {neo.calls}")
    # E10 the rollback's restart fails too -> rollback-failed, the ORIGINAL store is in place, nothing deleted
    with _Home(P) as h:
        data = h.instance(OLD)
        original = _tree(data)
        neo = FakeNeo(P)
        neo.running = (OLD, KEY)
        neo.bad.add(NEW)
        real_start = neo.start_neo4j

        def start_old_fails(version, project_key=None):
            if version == OLD and ("start", NEW, KEY) in neo.calls:
                neo.calls.append(("start", version, project_key))
                neo.zombie = (version, project_key)
                return 4242
            return real_start(version, project_key)

        with neo.seams(start_neo4j=start_old_fails, _UPGRADE_START_TIMEOUT_S=1, _UPGRADE_INDEX_WAIT_S=0):
            out, rep, _ = _up(P, h)
        after = _tree(data)
        check("E10 5.26.32 fails AND 5.26.0 does not come back -> rollback-failed", out == "rollback-failed", rep)
        check("E10 ...the ORIGINAL store is in place (never deleted)",
              {k: v for k, v in after.items() if not k.startswith("touched-by")} == original
              and "touched-by-5.26.32" not in after)
        check("E10 ...the store resolves to 5.26.0 (heal / setup start that)", P.store_neo4j_version(KEY) == OLD)
        check("E10 ...the copy 5.26.32 opened is KEPT and named", len(_siblings(data, "data.failed-")) == 1
              and "data.failed-" in str(rep.get("reason")), f"{_siblings(data, 'data.')} {rep}")
    # E11 the new release can't be stopped -> rollback-failed, the journal stays; recovery finishes the rollback
    with _Home(P) as h:
        data = h.instance(OLD)
        original = _tree(data)
        neo = FakeNeo(P)
        neo.running = (OLD, KEY)
        neo.bad.add(NEW)
        neo.stop_out[NEW] = "unverified"
        with neo.seams(_UPGRADE_INDEX_WAIT_S=0):
            out, rep, _ = _up(P, h)
        j = PR.read_upgrade_journal(KEY)
        check("E11 5.26.32 can't be stopped -> rollback-failed; the journal stays (phase started)",
              out == "rollback-failed" and j is not None and j.get("phase") == "started", f"{out} {j}")
        check("E11 ...the store still resolves to 5.26.0 (the journal), the backup is kept",
              P.store_neo4j_version(KEY) == OLD and len(_siblings(data, "data.bak-")) == 1)
        neo.stop_out.clear()
        with neo.seams():
            note, _ = _quiet(P.recover_neo4j_upgrade, str(h.proj), inst=(KEY, BOLT, HTTP),
                             uri=f"bolt://127.0.0.1:{BOLT}")
        after = _tree(data)
        check("E11 recover_neo4j_upgrade puts the ORIGINAL store back and closes the journal",
              note and PR.read_upgrade_journal(KEY) is None
              and {k: v for k, v in after.items() if not k.startswith("touched-by")} == original
              and "touched-by-5.26.32" not in after, f"{note} {sorted(after)[:6]}")
        check("E11 ...on 5.26.0's conf again", P.store_neo4j_version(KEY) == OLD and
              (P.paths(OLD, KEY)["conf_dir"] / ".seeded-version").read_text(encoding="utf-8").strip() == OLD)
    # E14 a stopped instance (the verb on a hibernated store): its answers on 5.26.0 are read first
    with _Home(P) as h:
        h.instance(OLD)
        neo = FakeNeo(P)
        with neo.seams():
            out, rep, _ = _up(P, h)
        seq = [c for c in neo.calls if c[0] in ("stop", "start")]
        check("E14 a stopped instance: 5.26.0 is started to read the graph, then the upgrade runs",
              out == "upgraded" and seq == [("start", OLD, KEY), ("stop", OLD, KEY), ("start", NEW, KEY)], str(seq))


# ----------------------------------------------------------------------------------------------- F: the base store
@_section
def gate_f_base_upgrade(P, PR) -> None:
    print("\n-- F: the base store (inside its version's folder) moves only when it is provably this project's --")
    base_inst = None
    with _Home(P) as h:
        old_data = h.base(OLD)
        original = _tree(old_data)
        neo = FakeNeo(P)
        neo.running = (OLD, None)
        with neo.seams():
            out, rep, log = _up(P, h, bolt=7687, inst=base_inst)
        new_data = P.paths(NEW)["data"]
        check("F1 the base store is upgraded", out == "upgraded", f"{out} {rep} {log[-400:]}")
        check("F1 the store is now in the 5.26.32 folder, same content",
              {k: v for k, v in _tree(new_data).items() if not k.startswith("touched-by")} == original)
        check("F1 the 5.26.0 folder's store is gone (the 5.26.0 binary stays)",
              not old_data.exists() and P.paths(OLD)["neo4j_home"].is_dir())
        check("F1 the base version record says 5.26.32; the base resolves there",
              PR.read_base_version_record() == NEW and P.store_neo4j_version(None) == NEW)
        check("F1 the hooks' owner record path follows the store (owner still this project)",
              Path(PR.base_owner_record_path()).parent == new_data and PR.base_owner_record() == h.owner)
        check("F1 5.26.32 serves the base; no journal left",
              neo.running == (NEW, None) and PR.read_upgrade_journal(None) is None)
    with _Home(P) as h:
        old_data = h.base(OLD)
        original = _tree(old_data)
        neo = FakeNeo(P)
        neo.running = (OLD, None)
        neo.bad.add(NEW)
        with neo.seams(_UPGRADE_INDEX_WAIT_S=0):
            out, rep, _ = _up(P, h, bolt=7687, inst=base_inst)
        check("F2 5.26.32 serves another graph -> rolled-back", out == "rolled-back", rep)
        check("F2 the 5.26.0 store was never moved or opened by 5.26.32",
              {k: v for k, v in _tree(old_data).items() if not k.startswith("touched-by")} == original
              and "touched-by-5.26.32" not in _tree(old_data))
        check("F2 the copy in the 5.26.32 folder is gone; no record; the base resolves to 5.26.0",
              not PR.neo4j_store_present(P.paths(NEW)["data"]) and PR.read_base_version_record() is None
              and P.store_neo4j_version(None) == OLD)
        check("F2 5.26.0 serves the base again", neo.running == (OLD, None))
    for label, kw, seams in (("F3 the 5.26.32 folder already holds a store", {"other_store": True}, {}),
                             ("F4 an unowned but populated base store", {"owner": None}, {}),
                             ("F4b the base store is another project's", {"owner": "C:/elsewhere/other"}, {}),
                             ("F5 7687 is not DeepMap's own base Neo4j", {}, {"_legacy_base_instance_alive": lambda: False})):
        with _Home(P) as h:
            old_data = h.base(OLD, owner=kw.get("owner", "self"), record=False)
            if kw.get("other_store"):
                _store(P.paths(NEW)["data"], "C:/elsewhere/other")
            original = _tree(old_data)
            neo = FakeNeo(P)
            neo.running = (OLD, None)
            with neo.seams(**seams):
                out, rep, _ = _up(P, h, bolt=7687, inst=base_inst)
            check(f"{label} -> skipped, nothing stopped, store untouched",
                  out == "skipped" and not [c for c in neo.calls if c[0] == "stop"] and _tree(old_data) == original,
                  f"{out} {rep} {neo.calls}")


# ----------------------------------------------------------------------------------------------- G: crash recovery
@_section
def gate_g_recovery(P, PR) -> None:
    print("\n-- G: a crashed upgrade's journal is finished or undone by the next setup — never a lost graph --")

    def journal(key, phase, copy):
        PR.upgrade_journal_path(key).parent.mkdir(parents=True, exist_ok=True)
        PR.upgrade_journal_path(key).write_text(json.dumps(
            {"from": OLD, "to": NEW, "phase": phase, "copy": str(copy), "key": key}), encoding="utf-8")

    def recover(P, h, inst):
        return _quiet(P.recover_neo4j_upgrade, str(h.proj), inst=inst,
                      uri=f"bolt://127.0.0.1:{inst[1] if inst else 7687}")[0]

    with _Home(P) as h:   # G1 crashed while copying: the partial backup goes, the store is untouched
        data = h.instance(OLD)
        original = _tree(data)
        bak = data.parent / "data.bak-5.26.0-x"
        (bak / "databases").mkdir(parents=True)
        journal(KEY, "copying", bak)
        neo = FakeNeo(P)
        with neo.seams():
            note = recover(P, h, (KEY, BOLT, HTTP))
        check("G1 copying: the partial backup is removed, the store untouched, the journal closed",
              note and not bak.exists() and _tree(data) == original and PR.read_upgrade_journal(KEY) is None
              and P.store_neo4j_version(KEY) == OLD, note)
    with _Home(P) as h:   # G2 crashed after 5.26.32 opened the store: the backup is put back
        data = h.instance(OLD)
        original = _tree(data)
        bak = data.parent / "data.bak-5.26.0-x"
        shutil.copytree(data, bak)
        (data / "touched-by-5.26.32").write_text("opened", encoding="utf-8")
        _quiet(P.configure_neo4j, NEW, project_key=KEY, bolt=BOLT, http=HTTP)   # re-seeded before the crash
        journal(KEY, "started", bak)
        neo = FakeNeo(P)
        neo.running = (NEW, KEY)
        with neo.seams():
            note = recover(P, h, (KEY, BOLT, HTTP))
        check("G2 started: whatever runs is stopped through the owner-proven stop",
              ("stop", NEW, KEY) in neo.calls, str(neo.calls))
        check("G2 ...the ORIGINAL store is back, 5.26.0's conf, the journal closed",
              note and _tree(data) == original and P.store_neo4j_version(KEY) == OLD
              and PR.read_upgrade_journal(KEY) is None and not bak.exists(), f"{note} {sorted(_tree(data))[:5]}")
        check("G2 ...the store 5.26.32 opened is kept aside (named in the note)",
              len(_siblings(data, "data.failed-")) == 1 and "data.failed-" in str(note))
    with _Home(P) as h:   # G3 crashed after verification: rolled forward
        data = h.instance(OLD)
        bak = data.parent / "data.bak-5.26.0-x"
        shutil.copytree(data, bak)
        _quiet(P.configure_neo4j, NEW, project_key=KEY, bolt=BOLT, http=HTTP)
        journal(KEY, "verified", bak)
        with FakeNeo(P).seams():
            note = recover(P, h, (KEY, BOLT, HTTP))
        check("G3 verified: rolled forward (backup removed, journal closed, 5.26.32)",
              note and not bak.exists() and PR.read_upgrade_journal(KEY) is None and P.store_neo4j_version(KEY) == NEW,
              note)
    with _Home(P) as h:   # G4 base, crashed after 5.26.32 started on its copy: the copy goes, the original stays
        old_data = h.base(OLD)
        original = _tree(old_data)
        new_data = P.paths(NEW)["data"]
        shutil.copytree(old_data, new_data, dirs_exist_ok=True)
        journal(None, "started", new_data)
        neo = FakeNeo(P)
        neo.running = (NEW, None)
        with neo.seams():
            note = recover(P, h, None)
        check("G4 base started: 5.26.32 stopped, its copy removed, the original kept, journal closed",
              note and ("stop", NEW, None) in neo.calls and not PR.neo4j_store_present(new_data)
              and _tree(old_data) == original and PR.read_upgrade_journal(None) is None
              and P.store_neo4j_version(None) == OLD, f"{note} {neo.calls}")
    with _Home(P) as h:   # G5 base, crashed after verification: rolled forward
        old_data = h.base(OLD)
        new_data = P.paths(NEW)["data"]
        shutil.copytree(old_data, new_data, dirs_exist_ok=True)
        journal(None, "verified", new_data)
        with FakeNeo(P).seams():
            note = recover(P, h, None)
        check("G5 base verified: rolled forward (record 5.26.32, the old store removed, journal closed)",
              note and PR.read_base_version_record() == NEW and not old_data.exists()
              and PR.neo4j_store_present(new_data) and PR.read_upgrade_journal(None) is None, note)
    with _Home(P) as h:   # G6 the upgrade may still be running elsewhere (its start lock is held): left alone
        data = h.instance(OLD)
        bak = data.parent / "data.bak-5.26.0-x"
        shutil.copytree(data, bak)
        journal(KEY, "started", bak)
        lk, _how = P._file_lock(h.home / "run" / f"neo4j-start-{KEY}.lock", 1)
        try:
            neo = FakeNeo(P)
            with neo.seams():
                note = recover(P, h, (KEY, BOLT, HTTP))
        finally:
            P._file_unlock(lk)
        check("G6 a held start lock (an upgrade still running): nothing touched, the journal stays",
              lk is not None and PR.read_upgrade_journal(KEY) is not None and bak.exists() and neo.calls == [],
              f"{note} {neo.calls}")
    with _Home(P) as h:
        check("G7 no journal -> recover does nothing", recover(P, h, (KEY, BOLT, HTTP)) is None)


# ----------------------------------------------------------------------------------------------- H: setup wiring
@_section
def gate_h_setup_order(P, PR) -> None:
    print("\n-- H: setup recovers a crashed upgrade BEFORE starting Neo4j, upgrades AFTER the owner checks --")
    order = []

    class FakeSync:
        last_verdict = "FAILED"
        last_not_pushed = None

        @staticmethod
        def sync(target, cleanup_stale=True):
            order.append("sync")
            return False

    with _Home(P) as h:
        with _Patch(P, _census_verdict=lambda t: None, _missing_modules=lambda names: [],
                    _resolve_ports=lambda t, **k: ((8787, 8788), None, None),
                    resolved_instance=lambda t: (KEY, BOLT, HTTP), _USER_SET_NEO4J_URI=False,
                    recover_neo4j_upgrade=lambda *a, **k: order.append("recover"),
                    ensure_neo4j=lambda **k: order.append("ensure") or True,
                    _setup_neo4j_upgrade=lambda *a, **k: order.append("upgrade") or True,
                    _load=lambda name: FakeSync, _stop_started_instance=lambda *a, **k: False):
            with _Env(NEO4J_URI=f"bolt://127.0.0.1:{BOLT}"):
                _quiet(P.setup, str(h.proj))
        check("setup: recover -> ensure -> upgrade -> sync", order == ["recover", "ensure", "upgrade", "sync"],
              str(order))
        del order[:]
        with _Patch(P, _census_verdict=lambda t: None, _missing_modules=lambda names: [],
                    _resolve_ports=lambda t, **k: ((8787, 8788), None, None),
                    resolved_instance=lambda t: (KEY, BOLT, HTTP), _USER_SET_NEO4J_URI=False,
                    recover_neo4j_upgrade=lambda *a, **k: None,
                    ensure_neo4j=lambda **k: order.append("ensure") or True,
                    _setup_neo4j_upgrade=lambda *a, **k: order.append("upgrade") or False,
                    _load=lambda name: FakeSync, _stop_started_instance=lambda *a, **k: False):
            with _Env(NEO4J_URI=f"bolt://127.0.0.1:{BOLT}"):
                r, _ = _quiet(P.setup, str(h.proj))
        check("setup: a store left unusable by the upgrade step -> setup fails before any sync",
              r is False and "sync" not in order, str(order))


# ----------------------------------------------------------------------------------------------- I: the verb + status
@_section
def gate_i_verb_and_status(P, PR) -> None:
    print("\n-- I: `provision.py neo4j-upgrade` and the status line --")
    with _Home(P) as h:
        r, out = _quiet(P.main, ["neo4j-upgrade", str(h.proj), "--to"])
        check("neo4j-upgrade --to without a value -> exit 2", r == 2, out[-200:])
        r, out = _quiet(P.main, ["neo4j-upgrade", str(h.proj), "--bogus"])
        check("neo4j-upgrade with an unknown option -> exit 2", r == 2, out[-200:])
        with _Patch(P, _resolve_ports=lambda t, **k: (None, "no route here — run /deep-map:setup", None)):
            r, out = _quiet(P.main, ["neo4j-upgrade", str(h.proj)])
        check("neo4j-upgrade with no route -> exit 1, nothing upgraded", r == 1 and "nothing upgraded" in out, out)
        h.instance(OLD)
        calls, restarts = [], []
        with _Patch(P, _resolve_ports=lambda t, **k: ((8787, 8788), None, None), neo4j_route_source=lambda: "registry",
                    _foreign_base_claim_problem=lambda *a, **k: None, resolved_instance=lambda t: (KEY, BOLT, HTTP),
                    _USER_SET_NEO4J_URI=False, recover_neo4j_upgrade=lambda *a, **k: None,
                    upgrade_neo4j=lambda *a, **k: calls.append(k) or "upgraded", is_http_up=lambda *a, **k: False,
                    start_mcp_servers=lambda **k: restarts.append(k) or True, _MCP_ROUTE_PROBLEM=None):
            r, out = _quiet(P.main, ["neo4j-upgrade", str(h.proj), "--check"])
            check("--check reports the version and the available release, changes nothing",
                  r == 0 and OLD in out and NEW in out and calls == [], out[-300:])
            r, out = _quiet(P.main, ["neo4j-upgrade", str(h.proj), "--to", "5.26.31"])
            check("neo4j-upgrade --to X.Y.Z passes the version, exit 0 when upgraded",
                  r == 0 and calls and calls[-1].get("to_version") == "5.26.31", f"{r} {calls}")
            check("...no MCP server of this project running -> none is started", restarts == [], str(restarts))
            with _Patch(P, is_http_up=lambda *a, **k: True, _bolt_state=lambda *a, **k: "up"):
                r, out = _quiet(P.main, ["neo4j-upgrade", str(h.proj)])
            check("...this project's MCP servers running -> restarted (restart_own) so they reconnect to the new JVM",
                  r == 0 and len(restarts) == 1 and restarts[0].get("restart_own") is True
                  and restarts[0].get("target") == str(h.proj), f"{r} {restarts}")
        with _Patch(P, _resolve_ports=lambda t, **k: ((8787, 8788), None, None), neo4j_route_source=lambda: "registry",
                    _foreign_base_claim_problem=lambda *a, **k: None, resolved_instance=lambda t: (KEY, BOLT, HTTP),
                    _USER_SET_NEO4J_URI=False, recover_neo4j_upgrade=lambda *a, **k: None,
                    upgrade_neo4j=lambda *a, **k: (k["report"].update(reason="x") or "rolled-back"),
                    is_http_up=lambda *a, **k: False):
            r, out = _quiet(P.main, ["neo4j-upgrade", str(h.proj)])
        check("neo4j-upgrade rolled back -> exit 1, says ROLLED BACK", r == 1 and "ROLLED BACK" in out, out[-200:])
        rows = [{"key": KEY, "neo4j_uri": f"bolt://127.0.0.1:{BOLT}", "state": "running", "data_bytes": 10,
                 "truncated": False, "last_used": "now", "root": str(h.proj), "neo4j_version": OLD}]
        check("the instance table names each store's Neo4j version",
              f"neo4j={OLD}" in P.format_instance_table(rows), P.format_instance_table(rows))
        with _Patch(PR, registry_stats=lambda env=None: {"projects": [{"key": KEY, "root": str(h.proj),
                                                                      "bolt_port": BOLT, "http_port": HTTP}]}):
            got = P.instance_table()
        check("instance_table() reads the version the store runs", got and got[0].get("neo4j_version") == OLD, got)
        with _Patch(PR, resolve_neo4j_route=lambda t, **k: PR.Neo4jRoute(f"bolt://127.0.0.1:{BOLT}", "registry",
                                                                         bolt=BOLT, http=HTTP, key=KEY)), \
                _Patch(P, _USER_SET_NEO4J_URI=False):
            note = P.neo4j_upgrade_note_for(str(h.proj))
        check("neo4j_upgrade_note_for (the CLI's doctor / status line) names the available release",
              note and NEW in note and OLD in note, note)
        with _Patch(P, _USER_SET_NEO4J_URI=True):
            check("...never for an operator NEO4J_URI", P.neo4j_upgrade_note_for(str(h.proj)) is None)


# ----------------------------------------------------------------------------------------------- J: rollback cut short
def _ensure(P, neo, key, create, pw_calls):
    with neo.seams(preflight=lambda *a, **k: True, set_initial_password=lambda *a, **k: pw_calls.append(1) or True,
                   _missing_modules=lambda names: []):
        return _quiet(P.ensure_neo4j, uri=f"bolt://127.0.0.1:{BOLT if key else 7687}", project_key=key,
                      bolt=BOLT if key else None, http=HTTP if key else None, create=create)


def _recover(P, h, inst):
    return _quiet(P.recover_neo4j_upgrade, str(h.proj), inst=inst, uri=f"bolt://127.0.0.1:{inst[1] if inst else 7687}")[0]


def _no_touch(tree: dict) -> dict:
    return {k: v for k, v in tree.items() if not k.startswith("touched-by")}


@_section
def gate_j_rollback_cut_short(P, PR) -> None:
    print("\n-- J: a rollback cut short (backup not moved back / a crash) is finished by the next setup, never a fresh "
          "store --")
    # J1 the backup can't be moved back (a handle held in it): data is gone, the graph is in the backup
    with _Home(P) as h:
        data = h.instance(OLD)
        original = _tree(data)
        neo = FakeNeo(P)
        neo.running = (OLD, KEY)
        neo.bad.add(NEW)
        real_move = P._move_dir

        def move(src, dst, wait_s=20.0):
            if Path(src).name.startswith("data.bak-"):
                return False
            return real_move(src, dst, wait_s=0.2)

        with neo.seams(_move_dir=move, _UPGRADE_INDEX_WAIT_S=0):
            out, rep, _ = _up(P, h)
        bak = _siblings(data, "data.bak-")
        check("J1 the backup can't be moved back -> rollback-failed, the reason names the backup as the graph's place",
              out == "rollback-failed" and len(bak) == 1 and bak[0] in str(rep.get("reason")), f"{out} {rep}")
        check("J1 ...the journal stays open and the store resolves to 5.26.0",
              PR.read_upgrade_journal(KEY) is not None and P.store_neo4j_version(KEY) == OLD)
        pw: list = []
        neo.calls.clear()
        ok, log = _ensure(P, neo, KEY, True, pw)
        check("J1 setup's ensure_neo4j while the graph sits in the backup: no fresh empty store, names the upgrade",
              not ok and pw == [] and "upgrade" in log and not [c for c in neo.calls if c[0] == "start"]
              and not data.exists(), f"{neo.calls} {log[-400:]}")
        ok, log = _ensure(P, neo, KEY, False, pw)
        check("J1 heal names the open upgrade (not 'never set up here')",
              not ok and "upgrade" in log and "never set up" not in log, log[-300:])
        with neo.seams():
            note = _recover(P, h, (KEY, BOLT, HTTP))
        after = _tree(data) if data.exists() else {}
        check("J1 recover_neo4j_upgrade moves the backup back, closes the journal, 5.26.0's conf (names where the "
              "store 5.26.32 opened is kept)",
              note and "data.failed-5.26.32" in note and PR.read_upgrade_journal(KEY) is None
              and _no_touch(after) == original
              and "touched-by-5.26.32" not in after and P.store_neo4j_version(KEY) == OLD
              and PR.instance_version_stamp(KEY) == OLD and _siblings(data, "data.bak-") == [],
              f"{note} {sorted(after)[:4]} {_siblings(data, 'data.')}")
    # J2 a crash right after the backup was moved back (before the journal closes / the old release starts)
    for label, crash_at in (("after the backup is back", "move"), ("before the old release starts", "start")):
        with _Home(P) as h:
            data = h.instance(OLD)
            original = _tree(data)
            neo = FakeNeo(P)
            neo.running = (OLD, KEY)
            neo.bad.add(NEW)
            real_move, real_start = P._move_dir, P._start_version

            def move(src, dst, wait_s=20.0, _real=real_move):
                ok = _real(src, dst, wait_s=0.2)
                if crash_at == "move" and Path(src).name.startswith("data.bak-"):
                    raise KeyboardInterrupt("killed")
                return ok

            def start(version, *a, _real=real_start, **k):
                if crash_at == "start" and version == OLD and ("start", NEW, KEY) in neo.calls:
                    raise KeyboardInterrupt("killed")
                return _real(version, *a, **k)

            try:
                with neo.seams(_move_dir=move, _start_version=start, _UPGRADE_INDEX_WAIT_S=0):
                    _up(P, h)
            except KeyboardInterrupt:
                pass
            neo.running = None
            check(f"J2 a crash {label}: the original store resolves to 5.26.0 (never the release that failed)",
                  P.store_neo4j_version(KEY) == OLD and _no_touch(_tree(data)) == original
                  and "touched-by-5.26.32" not in _tree(data), f"{P.store_neo4j_version(KEY)} {_siblings(data, 'data.')}")
            with neo.seams():
                note = _recover(P, h, (KEY, BOLT, HTTP))
            check(f"J2 ({label}) the next setup's recovery leaves it on 5.26.0 with the journal closed",
                  PR.read_upgrade_journal(KEY) is None and P.store_neo4j_version(KEY) == OLD
                  and PR.instance_version_stamp(KEY) == OLD and _no_touch(_tree(data)) == original, f"{note}")


# ----------------------------------------------------------------------------------------------- K: journal writes
@_section
def gate_k_journal_writes(P, PR) -> None:
    print("\n-- K: a journal phase that can't be written never lets the upgrade advance past it --")
    for phase in ("copying", "started"):
        with _Home(P) as h:
            data = h.instance(OLD)
            original = _tree(data)
            neo = FakeNeo(P)
            neo.running = (OLD, KEY)
            real_wj = P._write_upgrade_journal

            def wj(key, rec, _real=real_wj, _phase=phase):
                return False if rec.get("phase") == _phase else _real(key, rec)

            with neo.seams(_write_upgrade_journal=wj):
                out, rep, _ = _up(P, h)
            check(f"K the '{phase}' journal write fails -> not upgraded; 5.26.32 never started",
                  out in ("rolled-back", "rollback-failed") and ("start", NEW, KEY) not in neo.calls, f"{out} {neo.calls}")
            check(f"K ('{phase}') the original store untouched, no backup left, journal closed, 5.26.0 serves again",
                  _no_touch(_tree(data)) == original and "touched-by-5.26.32" not in _tree(data)
                  and _siblings(data, "data.") == [] and PR.read_upgrade_journal(KEY) is None
                  and P.store_neo4j_version(KEY) == OLD and neo.running == (OLD, KEY), f"{_siblings(data, 'data.')}")
    with _Home(P) as h:
        data = h.instance(OLD)
        neo = FakeNeo(P)
        neo.running = (OLD, KEY)
        real_wj = P._write_upgrade_journal
        with neo.seams(_write_upgrade_journal=lambda key, rec: False if rec.get("phase") == "verified"
                       else real_wj(key, rec)):
            out, rep, _ = _up(P, h)
        check("K the 'verified' write fails -> still upgraded (its conf stamp records 5.26.32): no backup, no journal",
              out == "upgraded" and _siblings(data, "data.") == [] and PR.read_upgrade_journal(KEY) is None
              and P.store_neo4j_version(KEY) == NEW, f"{out} {_siblings(data, 'data.')}")


# ----------------------------------------------------------------------------------------------- L: finishing
@_section
def gate_l_finish_guards(P, PR) -> None:
    print("\n-- L: the old copy is deleted only once the disk resolves the store to the new release --")
    with _Home(P) as h:
        old_data = h.base(OLD)
        neo = FakeNeo(P)
        neo.running = (OLD, None)
        with neo.seams(_write_base_version_record=lambda v: False):
            out, rep, log = _up(P, h, bolt=7687, inst=None)
        new_data = P.paths(NEW)["data"]
        j = PR.read_upgrade_journal(None)
        check("L1 base: the version record can't be written -> the 5.26.0 store is KEPT, the journal stays 'verified'",
              out == "upgraded" and PR.neo4j_store_present(old_data) and j is not None and j.get("phase") == "verified",
              f"{out} {j} {log[-300:]}")
        check("L1 ...meanwhile the base resolves to the verified 5.26.32 store",
              P.store_neo4j_version(None) == NEW and Path(P.paths()["data"]) == new_data)
        with neo.seams():
            note = _recover(P, h, None)
        check("L1 the next setup finishes it: record 5.26.32, the old store removed, the journal closed",
              note and PR.read_base_version_record() == NEW and not PR.neo4j_store_present(old_data)
              and PR.read_upgrade_journal(None) is None and PR.neo4j_store_present(new_data), note)
    with _Home(P) as h:
        old_data = h.base(OLD)
        original = _tree(old_data)
        PR.upgrade_journal_path(None).write_text(json.dumps(
            {"from": OLD, "to": NEW, "phase": "verified", "copy": str(P.paths(NEW)["data"])}), encoding="utf-8")
        with FakeNeo(P).seams():
            note = _recover(P, h, None)
        check("L2 a 'verified' base journal whose 5.26.32 store is gone: the ONLY store (5.26.0) is kept, journal closed",
              _tree(old_data) == original and PR.read_upgrade_journal(None) is None and P.store_neo4j_version(None) == OLD,
              f"{note} {old_data.exists()}")


# ----------------------------------------------------------------------------------------------- M: back-off
@_section
def gate_m_backoff(P, PR) -> None:
    print("\n-- M: a rolled-back automatic upgrade is not retried by every setup --")
    with _Home(P) as h:
        h.instance(OLD)
        neo = FakeNeo(P)
        neo.running = (OLD, KEY)
        neo.bad.add(NEW)
        with neo.seams(_UPGRADE_INDEX_WAIT_S=0):
            out, rep, _ = _up(P, h)
        fail = getattr(P, "_read_upgrade_failure", lambda k: None)(KEY)
        check("M1 a rolled-back upgrade records it (to-version + reason)",
              out == "rolled-back" and fail and fail.get("to") == NEW and fail.get("reason"), f"{out} {fail}")
        calls: list = []
        with _Patch(P, upgrade_neo4j=lambda *a, **k: calls.append(k) or "upgraded", _bolt_state=lambda *a, **k: "up",
                    _USER_SET_NEO4J_URI=False):
            r, log = _quiet(P._setup_neo4j_upgrade, str(h.proj), f"bolt://127.0.0.1:{BOLT}", "neo4j", "deepmap",
                            (KEY, BOLT, HTTP))
            check("M2 the next setup does not retry it: a NOTE naming the reason and neo4j-upgrade",
                  r is True and calls == [] and "neo4j-upgrade" in log and NEW in log, log[-300:])
            note = P.neo4j_upgrade_note(KEY) or ""
            check("M2 ...and the status note no longer says setup upgrades it", "rolled back" in note
                  and "neo4j-upgrade" in note, note)
            jp = P._upgrade_failure_path(KEY)
            rec = json.loads(jp.read_text(encoding="utf-8"))
            jp.write_text(json.dumps(dict(rec, epoch=rec["epoch"] - 8 * 86400)), encoding="utf-8")
            _quiet(P._setup_neo4j_upgrade, str(h.proj), f"bolt://127.0.0.1:{BOLT}", "neo4j", "deepmap", (KEY, BOLT, HTTP))
            check("M3 a week later setup tries again", len(calls) == 1, str(calls))
            jp.write_text(json.dumps(dict(rec, to="5.26.31")), encoding="utf-8")
            _quiet(P._setup_neo4j_upgrade, str(h.proj), f"bolt://127.0.0.1:{BOLT}", "neo4j", "deepmap", (KEY, BOLT, HTTP))
            check("M3 a record about another release does not hold this one back", len(calls) == 2, str(calls))
            jp.write_text(json.dumps(rec), encoding="utf-8")
        with neo.seams():
            neo.bad.clear()
            out, rep, _ = _up(P, h)
        check("M4 the explicit upgrade still runs, and a success removes the record",
              out == "upgraded" and P._read_upgrade_failure(KEY) is None, f"{out} {rep}")


# ----------------------------------------------------------------------------------------------- N: pinned releases
@_section
def gate_n_pinned_only(P, PR) -> None:
    print("\n-- N: an upgrade only ever lands on a release whose archive is SHA-256-pinned --")
    with _Home(P) as h:
        data = h.instance(OLD)
        original = _tree(data)
        neo = FakeNeo(P)
        neo.running = (OLD, KEY)
        with neo.seams():
            out, rep, _ = _up(P, h, to_version="5.26.31")
        check("N1 --to an unpinned 5.26.31 -> skipped (names DEEPMAP_NEO4J_SHA256), nothing stopped or downloaded",
              out == "skipped" and "DEEPMAP_NEO4J_SHA256" in str(rep.get("reason")) and _tree(data) == original
              and not [c for c in neo.calls if c[0] in ("stop", "download")], f"{out} {rep} {neo.calls}")
        with neo.seams(), _Env(DEEPMAP_NEO4J_SHA256="ab" * 32):
            out, rep, _ = _up(P, h, to_version="5.26.31")
        check("N2 ...with an out-of-band DEEPMAP_NEO4J_SHA256 it proceeds", out == "upgraded", f"{out} {rep}")


# ----------------------------------------------------------------------------------------------- O: stop + base journal
@_section
def gate_o_stop_open_base_journal(P, PR) -> None:
    print("\n-- O: `stop` / hibernate find the base JVM of either release while a base upgrade is open --")
    killed: list = []

    def seams(running_conf):
        return _Patch(P, is_windows=lambda: True, _terminate_pid=lambda pid, tree=False: killed.append(pid) or True,
                      _unrecorded_server_jvm=lambda p, k: 4343 if Path(p["conf_dir"]) == Path(running_conf) else None)

    with _Home(P) as h:
        h.base(OLD)
        new_conf = P.paths(NEW)["conf_dir"]
        with seams(new_conf):
            ok, _ = _quiet(P.stop_neo4j, project_key=None)
        check("O1 no journal: stop looks only at the base's own release (a 5.26.32 JVM is not its)",
              not ok and killed == [] and P._LAST_STOP_OUTCOME == "nothing", f"{killed} {P._LAST_STOP_OUTCOME}")
        shutil.copytree(P.paths(OLD)["data"], P.paths(NEW)["data"], dirs_exist_ok=True)
        PR.upgrade_journal_path(None).write_text(json.dumps(
            {"from": OLD, "to": NEW, "phase": "started", "copy": str(P.paths(NEW)["data"])}), encoding="utf-8")
        with seams(new_conf):
            ok, _ = _quiet(P.stop_neo4j, project_key=None)
        check("O2 a base upgrade open (phase started, 5.26.32 running): `stop` stops the 5.26.32 JVM",
              ok and killed == [4343] and P._LAST_STOP_OUTCOME == "stopped", f"{killed} {P._LAST_STOP_OUTCOME}")
        del killed[:]
        with seams(P.paths(OLD)["conf_dir"]):
            ok, _ = _quiet(P.stop_neo4j, project_key=None)
        check("O3 ...and the 5.26.0 JVM when that is the one running (the version the store resolves to first)",
              ok and killed == [4343], str(killed))


# ----------------------------------------------------------------------------------------------- P: recovery
@_section
def gate_p_recovery_more(P, PR) -> None:
    print("\n-- P: recovery after heal revived the old base, after a key migration, and never outside its own dirs --")
    with _Home(P) as h:   # P1 base 'started', its JVM died; heal then revived the untouched 5.26.0 base on 7687
        old_data = h.base(OLD)
        original = _tree(old_data)
        new_data = P.paths(NEW)["data"]
        shutil.copytree(old_data, new_data, dirs_exist_ok=True)
        PR.upgrade_journal_path(None).write_text(json.dumps(
            {"from": OLD, "to": NEW, "phase": "started", "copy": str(new_data)}), encoding="utf-8")
        neo = FakeNeo(P)
        neo.running = (OLD, None)
        real_wait = P._wait_store_released
        with neo.seams(_port_in_use=lambda host, port: neo.running is not None,
                       _wait_store_released=lambda conf, bolt, wait_s=None: real_wait(conf, bolt, 2)):
            note = _recover(P, h, None)
        check("P1 base 'started' + the old base revived by heal: the copy goes, the journal closes, the old base runs on",
              note and PR.read_upgrade_journal(None) is None and not PR.neo4j_store_present(new_data)
              and _no_touch(_tree(old_data)) == _no_touch(original) and neo.running == (OLD, None)
              and ("stop", OLD, None) not in neo.calls and P.store_neo4j_version(None) == OLD, f"{note} {neo.calls}")
    with _Home(P) as h:   # P2 the journal names its backup under the project's OLD key dir (a key migration since)
        data = h.instance(OLD)
        original = _tree(data)
        bak = data.parent / "data.bak-5.26.0"
        shutil.copytree(data, bak)
        (data / "touched-by-5.26.32").write_text("opened", encoding="utf-8")
        _quiet(P.configure_neo4j, NEW, project_key=KEY, bolt=BOLT, http=HTTP)
        PR.upgrade_journal_path(KEY).write_text(json.dumps(
            {"from": OLD, "to": NEW, "phase": "started", "key": "0ldkey000000",
             "copy": str(h.home / "instances" / "0ldkey000000" / "data.bak-5.26.0")}), encoding="utf-8")
        neo = FakeNeo(P)
        with neo.seams():
            note = _recover(P, h, (KEY, BOLT, HTTP))
        check("P2 the backup is found next to the instance's data dir by its name: the original store is restored",
              note and _tree(data) == original and P.store_neo4j_version(KEY) == OLD
              and PR.read_upgrade_journal(KEY) is None, f"{note} {_siblings(data, 'data.')}")
    with _Home(P) as h:   # P3 a journal naming a directory that is not an upgrade's backup: never deleted
        data = h.instance(OLD)
        precious = h.home / "instances" / KEY / "precious"
        (precious / "x").mkdir(parents=True)
        PR.upgrade_journal_path(KEY).write_text(json.dumps(
            {"from": OLD, "to": NEW, "phase": "copying", "copy": str(precious)}), encoding="utf-8")
        with FakeNeo(P).seams():
            note = _recover(P, h, (KEY, BOLT, HTTP))
        check("P3 a journal whose 'copy' is not a data.bak-* backup: recovery deletes nothing it names",
              precious.is_dir() and PR.neo4j_store_present(data), note)


# ----------------------------------------------------------------------------------------------- Q: no downgrade by pin
@_section
def gate_q_pin_never_downgrades(P, PR) -> None:
    print("\n-- Q: NEO4J_VERSION never moves an existing store to an older release, nor orphans the base store --")
    with _Home(P) as h:
        data = h.instance(OLD)
        neo = FakeNeo(P)
        neo.running = (OLD, KEY)
        with neo.seams():
            _up(P, h)
        neo.running = None
        neo.calls.clear()
        with _Env(NEO4J_VERSION=OLD):
            check("Q1 an instance upgraded to 5.26.32 + NEO4J_VERSION=5.26.0: still resolves to 5.26.32",
                  P.store_neo4j_version(KEY) == NEW, P.store_neo4j_version(KEY))
            ok, log = _ensure(P, neo, KEY, False, [])
            check("Q1 ...heal starts 5.26.32, warns about the ignored pin, the stamp stays 5.26.32",
                  ok and [c for c in neo.calls if c[0] == "start"] == [("start", NEW, KEY)] and "NEO4J_VERSION" in log
                  and PR.instance_version_stamp(KEY) == NEW, f"{neo.calls} {log[-300:]}")
        with _Env(NEO4J_VERSION="5.26.40"):
            check("Q2 a NEWER pin still applies to an instance (the operator's explicit choice)",
                  P.store_neo4j_version(KEY) == "5.26.40")
        with _Env(NEO4J_VERSION=NEW):
            check("Q2 an equal pin applies", P.store_neo4j_version(KEY) == NEW)
    with _Home(P) as h:
        h.base(OLD)
        neo = FakeNeo(P)
        neo.running = (OLD, None)
        with neo.seams():
            _up(P, h, bolt=7687, inst=None)
        neo.running = None
        neo.calls.clear()
        pw: list = []
        with _Env(NEO4J_VERSION=OLD):
            check("Q3 a base upgraded to 5.26.32 + NEO4J_VERSION=5.26.0: the base still resolves to its store",
                  P.store_neo4j_version(None) == NEW and PR.neo4j_store_present(P.paths()["data"]))
            ok, log = _ensure(P, neo, None, True, pw)
            check("Q3 ...setup's ensure starts 5.26.32 on it, never a new empty store in the 5.26.0 folder",
                  ok and [c for c in neo.calls if c[0] == "start"] == [("start", NEW, None)]
                  and not PR.neo4j_store_present(P.paths(OLD)["data"]), f"{neo.calls} {log[-300:]}")
    with _Home(P) as h:
        with _Env(NEO4J_VERSION="5.26.5"):
            check("Q4 no store anywhere: the pin picks the release a new store gets",
                  P.store_neo4j_version(None) == "5.26.5" and P.store_neo4j_version(KEY) == "5.26.5")


# ----------------------------------------------------------------------------------------------- R: residuals
class _StatusDriver:
    """A Bolt driver that answers cmd_status's census (every count 3, no index) — the status line's wiring only."""

    def verify_connectivity(self):
        pass

    def session(self, **_k):
        class _S:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def run(self, q, *a, **k):
                return _Result([]) if "SHOW INDEXES" in q else _Result([{"c": 3}])
        return _S()

    def close(self):
        pass


def _folder(p) -> str:
    """The project folder exactly as a hint must quote it after `neo4j-upgrade`."""
    return f'neo4j-upgrade "{Path(str(p)).resolve().as_posix()}"'


@_section
def gate_r_residuals(P, PR) -> None:
    print("\n-- R: credentials that don't authenticate never stop the store; the stop / owner / finish guards; the "
          "hint names the project --")
    # R1 a RUNNING store that rejects these credentials: nothing is started or stopped (the upgrade can't read the
    # graph, so stopping the user's Neo4j for it would only take the store down)
    for layout in ("instance", "base"):
        with _Home(P) as h:
            key = KEY if layout == "instance" else None
            data = h.instance(OLD) if key else h.base(OLD)
            original = _tree(data)
            neo = FakeNeo(P)
            neo.running = (OLD, key)
            with neo.seams(is_bolt_up=lambda *a, **k: False, _bolt_state=lambda *a, **k: "auth",
                           _NEO4J_ROUTE_SOURCE="registry"):
                out, rep, log = _up(P, h, **({} if key else {"bolt": 7687, "inst": None}))
            reason = str(rep.get("reason"))
            check(f"R1 {layout}: a running store that rejects the credentials -> skipped, the reason says so",
                  out == "skipped" and "credentials" in reason and "NEO4J_PASSWORD" in reason, f"{out} {rep}")
            check(f"R1 {layout}: ...nothing started or stopped, store / version / journal unchanged, still serving",
                  not [c for c in neo.calls if c[0] in ("stop", "start")] and _tree(data) == original
                  and P.store_neo4j_version(key) == OLD and PR.read_upgrade_journal(key) is None
                  and neo.running == (OLD, key), f"{neo.calls}")
    # R1b a store that is DOWN is still started to read it first (the path the auth check must not close)
    with _Home(P) as h:
        h.instance(OLD)
        neo = FakeNeo(P)
        with neo.seams():
            out, rep, _ = _up(P, h)
        check("R1b a stopped store (state 'down') is still started on 5.26.0 to read it, then upgraded",
              out == "upgraded" and [c for c in neo.calls if c[0] == "start"][:1] == [("start", OLD, KEY)],
              f"{out} {neo.calls}")
    # R2a the stop reports "stopped" but the JVM did not let go of the store: nothing is copied
    for label, extra in (("its JVM is still found serving the conf dir", "pid"),
                         ("its bolt port stays in use", "port")):
        with _Home(P) as h:
            data = h.instance(OLD)
            original = _tree(data)
            neo = FakeNeo(P)
            neo.running = (OLD, KEY)
            seams = {"_UPGRADE_STOP_WAIT_S": 0}
            if extra == "pid":
                def stop_lies(version=None, project_key=None, _neo=neo):
                    _neo.calls.append(("stop", version or OLD, project_key))
                    return P._stop_done("stopped", True)   # says stopped; the JVM (and the store) stay up
                seams.update(stop_neo4j=stop_lies, _find_neo4j_server_pids=lambda conf: [4242])
            else:
                seams.update(_port_in_use=lambda host, port: True)
            with neo.seams(**seams):
                out, rep, _ = _up(P, h)
            check(f"R2a stop says stopped but {label} -> skipped", out == "skipped", f"{out} {rep}")
            check(f"R2a ({label}) nothing copied, the journal closed, the store unchanged, 5.26.32 never started",
                  _siblings(data, "data.") == [] and PR.read_upgrade_journal(KEY) is None
                  and _no_touch(_tree(data)) == _no_touch(original) and "touched-by-5.26.32" not in _tree(data)
                  and ("start", NEW, KEY) not in neo.calls and P.store_neo4j_version(KEY) == OLD,
                  f"{_siblings(data, 'data.')} {neo.calls}")
            check(f"R2a ({label}) the old release still serves", neo.running == (OLD, KEY), f"{neo.running}")
    # R2b the base owner record names ANOTHER project, although the live graph's owner says this one
    with _Home(P) as h:
        old_data = h.base(OLD, record=False)
        (old_data / "deepmap_base_owner.json").write_text(json.dumps({"root": "C:/elsewhere/other-project"}),
                                                          encoding="utf-8")
        original = _tree(old_data)
        neo = FakeNeo(P)
        neo.running = (OLD, None)
        with neo.seams():
            out, rep, _ = _up(P, h, bolt=7687, inst=None)
        check("R2b the base owner record names another project (live owner: this one) -> skipped, names the record",
              out == "skipped" and "owner record" in str(rep.get("reason")), f"{out} {rep}")
        check("R2b ...nothing stopped or started, the store untouched, no journal",
              not [c for c in neo.calls if c[0] in ("stop", "start")] and _tree(old_data) == original
              and PR.read_upgrade_journal(None) is None and neo.running == (OLD, None), f"{neo.calls}")
    # R2c finishing an instance upgrade whose data dir is gone while its backup is there: nothing is deleted
    with _Home(P) as h:
        data = h.instance(OLD)
        _quiet(P.configure_neo4j, NEW, project_key=KEY, bolt=BOLT, http=HTTP)   # the stamp already says 5.26.32
        original = _tree(data)
        bak = data.parent / "data.bak-5.26.0"
        os.replace(str(data), str(bak))
        if data.exists() and not any(data.iterdir()):
            data.rmdir()
        journal = {"from": OLD, "to": NEW, "phase": "verified", "copy": bak.name, "key": KEY}
        PR.upgrade_journal_path(KEY).write_text(json.dumps(journal), encoding="utf-8")
        r, log = _quiet(P._finish_upgrade, KEY, dict(journal))
        check("R2c _finish_upgrade with the data dir missing and the backup present -> False",
              r is False and not data.exists(), f"{r} {log[-300:]}")
        check("R2c ...the backup (the only copy of the graph) is kept, untouched, and the journal stays",
              bak.is_dir() and _tree(bak) == original and PR.read_upgrade_journal(KEY) is not None,
              f"{bak.exists()} {PR.read_upgrade_journal(KEY)}")
    # R3 the upgrade hint names the project folder wherever the root is known
    with _Home(P) as h:
        h.instance(OLD)
        want = _folder(h.proj)
        note = P.neo4j_upgrade_note(KEY, root=str(h.proj)) or ""
        check("R3 neo4j_upgrade_note(root=) quotes the project folder after neo4j-upgrade", want in note, note)
        bare = P.neo4j_upgrade_note(KEY) or ""
        check("R3 ...without a root it says to run it from this project's folder",
              "neo4j-upgrade` (from this project's folder)" in bare, bare)
        with _Patch(PR, resolve_neo4j_route=lambda t, **k: PR.Neo4jRoute(f"bolt://127.0.0.1:{BOLT}", "registry",
                                                                         bolt=BOLT, http=HTTP, key=KEY)), \
                _Patch(P, _USER_SET_NEO4J_URI=False):
            note_for = P.neo4j_upgrade_note_for(str(h.proj)) or ""
        check("R3 neo4j_upgrade_note_for (the CLI's doctor / status) quotes the project folder", want in note_for,
              note_for)
        with _Patch(P, _resolve_ports=lambda t, **k: ((8787, 8788), None, None), neo4j_route_source=lambda: "registry",
                    _foreign_base_claim_problem=lambda *a, **k: None, resolved_instance=lambda t: (KEY, BOLT, HTTP),
                    _USER_SET_NEO4J_URI=False, _make_driver=lambda *a, **k: _StatusDriver(),
                    neo4j_edition=lambda d: "community", _print_isolation=lambda e: None):
            with _Env(NEO4J_URI=f"bolt://127.0.0.1:{BOLT}"):
                r, out = _quiet(P.main, ["status", str(h.proj)])
            check("R3 `provision.py status <dir>` quotes that project's folder in the upgrade line",
                  r == 0 and want in out, out[-400:])
            r, out = _quiet(P.main, ["neo4j-upgrade", str(h.proj), "--check"])
            check("R3 `neo4j-upgrade --check` quotes it too", r == 0 and want in out, out[-400:])
        with _Patch(P, upgrade_neo4j=lambda *a, **k: "upgraded", _USER_SET_NEO4J_URI=False), \
                _Env(DEEPMAP_NEO4J_AUTO_UPGRADE="0"):
            r, out = _quiet(P._setup_neo4j_upgrade, str(h.proj), f"bolt://127.0.0.1:{BOLT}", "neo4j", "deepmap",
                            (KEY, BOLT, HTTP))
        check("R3 setup's NOTE (auto-upgrade off) quotes the project folder", r is True and want in out, out[-400:])
    # R3b _warn_ignored_pin: the registry's root for the instance when there is one, else "from this project's folder"
    with _Home(P) as h:
        h.instance(NEW)
        out = io.StringIO()
        with _Env(NEO4J_VERSION=OLD), contextlib.redirect_stderr(out):
            P._warn_ignored_pin(KEY)
        check("R3b the ignored-pin WARNING without a recorded root says to run it from this project's folder",
              "NEO4J_VERSION" in out.getvalue() and "neo4j-upgrade` (from this project's folder)" in out.getvalue(),
              out.getvalue())
        PR.registry_path().write_text(json.dumps({"version": PR.REGISTRY_VERSION, "projects": {
            KEY: {"root": h.owner.replace("\\", "/"), "bolt_port": BOLT, "http_port": HTTP}}}), encoding="utf-8")
        out = io.StringIO()
        with _Env(NEO4J_VERSION=OLD), contextlib.redirect_stderr(out):
            P._warn_ignored_pin(KEY)
        check("R3b ...with this instance's registry entry it quotes the project folder",
              _folder(h.owner) in out.getvalue(), out.getvalue())
        out = io.StringIO()
        with _Env(NEO4J_VERSION=OLD), contextlib.redirect_stderr(out):
            P._warn_ignored_pin(KEY, root=str(h.proj))
        check("R3b ...and the caller's root when it passes one", _folder(h.proj) in out.getvalue(), out.getvalue())


# ----------------------------------------------------------------------------------------------- main
def _load(root: Path):
    sys.path.insert(0, str(root / "build"))
    import port_registry as PR  # noqa: E402
    import provision as P  # noqa: E402
    return P, PR


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    root = REPO
    if "--root" in sys.argv:
        root = Path(sys.argv[sys.argv.index("--root") + 1]).resolve()
    print("=" * 72)
    print(f"DeepMap — Neo4j 5.26.32 for new stores; existing stores sticky, upgraded only when verified (root: {root})")
    print("=" * 72)
    P, PR = _load(root)
    for gate in (gate_a_versions_and_hashes, gate_b0_existing_paths_unchanged, gate_b_sticky_resolution, gate_c_heal_and_ensure_never_upgrade,
                 gate_d_upgrade_decision, gate_e_instance_upgrade, gate_f_base_upgrade, gate_g_recovery,
                 gate_h_setup_order, gate_i_verb_and_status, gate_j_rollback_cut_short, gate_k_journal_writes,
                 gate_l_finish_guards, gate_m_backoff, gate_n_pinned_only, gate_o_stop_open_base_journal,
                 gate_p_recovery_more, gate_q_pin_never_downgrades, gate_r_residuals):
        gate(P, PR)
    total = len(PASSES) + len(FAILS)
    print("\n" + "=" * 72)
    if FAILS:
        print(f"RESULT: RED — {len(PASSES)}/{total} gates passed.")
        return 1
    print(f"RESULT: GREEN — {len(PASSES)}/{total} gates passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
