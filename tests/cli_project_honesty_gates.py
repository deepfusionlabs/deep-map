#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j, no-socket, no-spend gates for the CLI's per-project + honesty fixes (2026-09 live
validation, owner W3 — bugs C1-C6).

Locks:
  C1  `doctor` probes THIS project's MCP pair (explicit env > registry > .mcp.json > base default),
      never the base 8787/8788 for a project that owns another pair (a false OK/READY on every 2nd+
      project was the field failure).
  C2  `doctor`'s extractor line reports the engine the LAST SYNC actually used (graph_build.json
      `extractor`), flags an automatic ast fallback, and any non-READY last-sync verdict WARNs + feeds
      the final verdict (was "[OK] ... DEGRADED" followed by "verdict: READY").
  C3  `deepmap sync` accepts --force-prune and passes it through; a FAILED sync verdict exits non-zero.
  C4  learning / insights / tune / enrich + retrieval-log default_root resolve the PROJECT root (the
      outermost .deepmap/graph_build.json marker up to the git toplevel; with no marker, the start dir —
      sync._sync_root parity since round 3) — not the raw cwd of a subdirectory of a synced project.
  C5  an AuthError is reported as an authentication failure (not "unreachable / is it running?"); a
      query-cost error (transaction-memory OOM) as "reachable but too expensive"; hints use the
      /deep-map:setup colon form and never a relative `python build/...` path a user can't run.
  C6  tune/insights print "none yet" (not a wall of None) after `learning --enable`; the DEEPMAP_LEARNING
      veto is named; invalid input exits 2 (missing root / negative counts / conflicting learning flags);
      `--since` filtering everything is not "no retrieval log yet"; impact strips the symbol, notes a
      clamped --max-hops and warns about unknown --edges types.

Hermetic: a temp DEEPMAP_HOME, stubbed Neo4j driver + provision probes (no real sockets / Neo4j /
network), temp project dirs; never touches ~/.deepmap or the committed .mcp.json.
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build"
CLI_PATH = BUILD / "deepmap_cli.py"
PY = sys.executable

_PASS = 0
_FAIL = 0

# Env knobs that must not leak in from the developer's shell (they change every resolution path).
_SCRUB = ("NEO4J_URI", "DEEPMAP_SEARCH_PORT", "DEEPMAP_EMBED_PORT", "DEEPMAP_LEARNING",
          "DEEPMAP_EXTRACTOR", "DEEPMAP_PROJECT_ROOT", "DEEPMAP_RETRIEVAL_LOG", "DEEPMAP_APPLY_LEARNED",
          "DEEPMAP_IMPACT_EDGES", "DEEPMAP_IMPACT_PROFILE", "DEEPMAP_IMPACT_MAX_HOPS", "DEEPMAP_ENRICH",
          "DEEPMAP_MCP_HOST")


def check(label: str, cond: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {label}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}{(' — ' + detail) if detail else ''}")


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _load_cli():
    return _load_module(CLI_PATH, f"dm_cli_honesty_{time.time_ns()}")


def _sub_env(home: Path, **extra) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in _SCRUB}
    env.update({"DEEPMAP_HOME": str(home), "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1",
                "DEEPMAP_EXTRACTOR": "ast"})
    env.update({k: v for k, v in extra.items() if v is not None})
    for k, v in extra.items():
        if v is None:
            env.pop(k, None)
    return env


def _cli(args, cwd, home, timeout=180, **extra):
    return subprocess.run([PY, str(CLI_PATH), *args], cwd=str(cwd), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=_sub_env(home, **extra),
                          timeout=timeout)


def _git_init(d: Path) -> bool:
    git = shutil.which("git")
    if not git:
        return False
    r = subprocess.run([git, "init", "-q", str(d)], capture_output=True, text=True, timeout=30)
    return r.returncode == 0


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
    """Make `import torch` / `sentence_transformers` resolve instantly (doctor only probes importability)."""
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


# ---- fakes ------------------------------------------------------------------
class _Res:
    def __init__(self, rows):
        self.rows = rows

    def single(self):
        return self.rows[0] if self.rows else None

    def __iter__(self):
        return iter(self.rows)


class _Sess:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, q, **kw):
        if "SHOW INDEXES" in q:
            return _Res([{"name": "deepmap_fn_vec", "type": "VECTOR", "state": "ONLINE"}])
        if "owning_root" in q:   # the graph is the doctor's OWN project's (the cwd it runs in)
            return _Res([{"r": os.getcwd(), "owning_root": os.getcwd()}])
        return _Res([{"c": 7}])


class _OkDriver:
    def verify_connectivity(self):
        pass

    def session(self):
        return _Sess()

    def close(self):
        pass


class _RaisingDriver:
    def __init__(self, exc):
        self.exc = exc

    def verify_connectivity(self):
        raise self.exc

    def session(self):
        raise self.exc

    def close(self):
        pass


def _fake_provision(up_ports: set, probed: list):
    ns = types.SimpleNamespace()
    ns.MCP_SERVERS = {"deepmap-search": {"port_env": "DEEPMAP_SEARCH_PORT", "default_port": 8787},
                      "deepmap-embed": {"port_env": "DEEPMAP_EMBED_PORT", "default_port": 8788}}
    ns.mcp_host = lambda: os.environ.get("DEEPMAP_MCP_HOST", "127.0.0.1")

    def mcp_port(name):
        cfg = ns.MCP_SERVERS[name]
        return int(os.environ.get(cfg["port_env"], cfg["default_port"]))

    ns.mcp_port = mcp_port
    ns.mcp_url = lambda name: f"http://{ns.mcp_host()}:{mcp_port(name)}/mcp"

    def is_http_up(host, port, timeout=2.0):
        probed.append(int(port))
        return int(port) in up_ports

    ns.is_http_up = is_http_up

    def _ports_from_mcp_json(root):
        try:
            obj = json.loads((Path(root) / ".mcp.json").read_text(encoding="utf-8"))
            srv = obj["mcpServers"]
            s = int(srv["deepmap-search"]["url"].rsplit(":", 1)[1].split("/")[0])
            e = int(srv["deepmap-embed"]["url"].rsplit(":", 1)[1].split("/")[0])
            return (s, e)
        except Exception:
            return None

    ns._ports_from_mcp_json = _ports_from_mcp_json
    ns.isolation_note = lambda *a, **k: "isolation: (test stub)"
    ns.print_instance_table = lambda *a, **k: None
    ns._USER_SET_NEO4J_URI = False
    ns.neo4j_edition = lambda drv: "community"
    return ns


def _run_doctor(C, *, driver, provision, cwd: Path) -> tuple:
    """(rc, stdout) of an in-process cmd_doctor with the driver + provision probes stubbed."""
    orig_load, orig_driver = C._load, C._driver

    def _load(name):
        if name == "provision":
            return provision
        return orig_load(name)

    C._load = _load
    C._driver = lambda: driver
    out = io.StringIO()
    rc = None
    try:
        with _chdir(cwd), _fake_ml_stack(), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(io.StringIO()):
            rc = C.cmd_doctor(argparse.Namespace())
    finally:
        C._load, C._driver = orig_load, orig_driver
    return rc, out.getvalue()


def _write_registry(PR, root: Path, search: int, embed: int) -> None:
    reg = {"version": PR.REGISTRY_VERSION, "kind": PR.REGISTRY_KIND, "projects": {
        PR.project_key(root): {"root": PR.project_identity(root), "search_port": search,
                               "embed_port": embed}}}
    p = PR.registry_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(reg), encoding="utf-8")


def _write_manifest(root: Path, **fields) -> None:
    d = root / ".deepmap"
    d.mkdir(parents=True, exist_ok=True)
    data = {"nodes": 7, "calls_edges": 3, "embedded": 7}
    data.update(fields)
    (d / "graph_build.json").write_text(json.dumps(data), encoding="utf-8")


def _verdict_of(out: str) -> str:
    for ln in out.splitlines():
        if "[deepmap] verdict:" in ln:
            return ln.split("verdict:", 1)[1].strip()
    return ""


def _line_with(out: str, needle: str) -> str:
    return next((ln for ln in out.splitlines() if needle in ln), "")


# ---- C1: doctor probes THIS project's MCP pair --------------------------------
def gate_c1_doctor_mcp_ports(C, PR, work: Path) -> None:
    print("\n-- C1: doctor probes THIS project's MCP pair (registry > .mcp.json > base) --")
    proj = work / "c1proj"
    proj.mkdir()
    _write_manifest(proj, extractor="graphify", verdict="READY")

    # (a) registry pair (8911, 8912) down; ANOTHER project's servers hold 8787/8788.
    _write_registry(PR, proj, 8911, 8912)
    probed: list = []
    rc, out = _run_doctor(C, driver=_OkDriver(), provision=_fake_provision({8787, 8788}, probed),
                          cwd=proj)
    check("registry pair: doctor probes 8911 + 8912", 8911 in probed and 8912 in probed, str(probed))
    check("registry pair: doctor does NOT probe the base 8787/8788", 8787 not in probed and 8788 not in probed,
          str(probed))
    check("registry pair: the printed MCP URL is the project's (:8911/mcp)", ":8911/mcp" in out,
          _line_with(out, "deepmap-search"))
    check("project servers down -> verdict DEGRADED (not a false READY)",
          _verdict_of(out).startswith("DEGRADED"), _verdict_of(out))

    # (b) healthy project pair UP while nothing holds 8787/8788 -> no false 'down'.
    probed = []
    rc, out = _run_doctor(C, driver=_OkDriver(), provision=_fake_provision({8911, 8912}, probed), cwd=proj)
    check("project pair UP, base free -> MCP lines OK (no false 'down')",
          "down" not in _line_with(out, "MCP deepmap-search") and "UP" in _line_with(out, "MCP deepmap-search"),
          _line_with(out, "MCP deepmap-search"))

    # (c) no registry entry -> the project's .mcp.json pair.
    PR.registry_path().unlink()
    (proj / ".mcp.json").write_text(json.dumps({"mcpServers": {
        "deepmap-search": {"type": "http", "url": "http://127.0.0.1:8913/mcp"},
        "deepmap-embed": {"type": "http", "url": "http://127.0.0.1:8914/mcp"}}}), encoding="utf-8")
    probed = []
    _run_doctor(C, driver=_OkDriver(), provision=_fake_provision(set(), probed), cwd=proj)
    check(".mcp.json fallback: doctor probes 8913 + 8914", 8913 in probed and 8914 in probed, str(probed))

    # (d) an explicit env override still wins.
    os.environ["DEEPMAP_SEARCH_PORT"], os.environ["DEEPMAP_EMBED_PORT"] = "9001", "9002"
    probed = []
    try:
        _run_doctor(C, driver=_OkDriver(), provision=_fake_provision(set(), probed), cwd=proj)
    finally:
        os.environ.pop("DEEPMAP_SEARCH_PORT", None)
        os.environ.pop("DEEPMAP_EMBED_PORT", None)
    check("explicit DEEPMAP_*_PORT env wins (9001/9002)", 9001 in probed and 9002 in probed, str(probed))

    # (e) nothing recorded anywhere -> the legacy base pair.
    (proj / ".mcp.json").unlink()
    probed = []
    _run_doctor(C, driver=_OkDriver(), provision=_fake_provision(set(), probed), cwd=proj)
    check("no registry / .mcp.json -> base 8787/8788 (legacy single project)",
          8787 in probed and 8788 in probed, str(probed))


# ---- C2: doctor extractor + last-sync verdict from the manifest ----------------
def gate_c2_doctor_extractor(C, work: Path) -> None:
    print("\n-- C2: doctor reports the LAST SYNC's engine + verdict (graph_build.json) --")
    proj = work / "c2proj"
    proj.mkdir()
    up = {8787, 8788}

    _write_manifest(proj, extractor="graphify", verdict="READY", reasons=[])
    rc, out = _run_doctor(C, driver=_OkDriver(), provision=_fake_provision(up, []), cwd=proj)
    check("baseline (graphify, READY manifest) -> doctor verdict READY", _verdict_of(out) == "READY",
          _verdict_of(out) + " | " + " / ".join(ln for ln in out.splitlines() if "  - " in ln))
    check("baseline extractor line names graphify", "graphify" in _line_with(out, "extractor"),
          _line_with(out, "extractor"))

    _write_manifest(proj, extractor="ast", verdict="DEGRADED",
                    reasons=["graphify failed (path too long) — fell back automatically to ast"])
    rc, out = _run_doctor(C, driver=_OkDriver(), provision=_fake_provision(up, []), cwd=proj)
    ext = _line_with(out, "extractor")
    check("ast-fallback manifest: extractor line does NOT claim 'auto -> graphify'",
          "-> graphify" not in ext, ext)
    check("ast-fallback manifest: extractor line reports ast + the automatic fallback",
          "ast" in ext and "fell back" in ext.lower() and ext.strip().startswith("[WARN"), ext)
    check("DEGRADED last sync: the coverage/last-sync line is WARN (not '[OK] ... DEGRADED')",
          not any(ln.strip().startswith("[OK") and "DEGRADED" in ln for ln in out.splitlines()),
          _line_with(out, "last-sync verdict"))
    check("DEGRADED last sync -> doctor verdict DEGRADED (not READY)", _verdict_of(out) == "DEGRADED",
          _verdict_of(out))
    check("the last sync's own reason is listed under the verdict",
          "path too long" in out.split("[deepmap] verdict:", 1)[-1], out[-400:])

    # C4 twin: doctor from a SUBDIRECTORY of a git repo still reads the project's manifest (a non-git
    # subdirectory is its own project — sync._sync_root / the hooks / the Neo4j routing agree).
    _git_init(proj)
    sub = proj / "pkg" / "deep"
    sub.mkdir(parents=True)
    rc, out = _run_doctor(C, driver=_OkDriver(), provision=_fake_provision(up, []), cwd=sub)
    check("doctor from a subdir reads the project root's manifest (last-sync verdict shown)",
          "last-sync verdict" in out and "ast" in _line_with(out, "extractor"), _line_with(out, "extractor"))


# ---- C3: sync --force-prune + FAILED exit code ---------------------------------
def gate_c3_sync(C, work: Path) -> None:
    print("\n-- C3: `deepmap sync` accepts --force-prune and exits non-zero on a FAILED verdict --")
    p = C.build_parser()
    try:
        a = p.parse_args(["sync", ".", "--stale-cleanup", "--force-prune"])
        parsed = True
    except SystemExit:
        a, parsed = None, False
    check("parser accepts `sync . --stale-cleanup --force-prune`", parsed)
    if not parsed:
        return
    calls: list = []

    def _fake_sync_mod(verdict=None, manifest_verdict=None):
        m = types.ModuleType("fake_sync")

        def sync(target, **kw):
            calls.append(kw)
            if manifest_verdict is not None:
                _write_manifest(Path(target), extractor="ast", verdict=manifest_verdict)
            return True

        m.sync = sync
        if verdict is not None:
            m.last_verdict = verdict
        return m

    proj = work / "c3proj"
    proj.mkdir()
    orig = C._load
    try:
        C._load = lambda name: _fake_sync_mod() if name == "sync" else orig(name)
        a = p.parse_args(["sync", str(proj), "--stale-cleanup", "--force-prune"])
        rc = a.fn(a)
        check("cmd_sync passes force_prune=True through to sync.sync",
              bool(calls) and calls[-1].get("force_prune") is True, str(calls[-1:] ))
        check("a successful sync with no FAILED verdict exits 0", rc == 0, repr(rc))

        C._load = lambda name: _fake_sync_mod(verdict="FAILED") if name == "sync" else orig(name)
        a = p.parse_args(["sync", str(proj)])
        with contextlib.redirect_stderr(io.StringIO()):
            rc = a.fn(a)
        check("sync.last_verdict FAILED -> exit 1", rc == 1, repr(rc))
        check("--force-prune omitted -> force_prune=False", calls[-1].get("force_prune") is False, str(calls[-1]))

        C._load = lambda name: _fake_sync_mod(verdict="DEGRADED") if name == "sync" else orig(name)
        rc = a.fn(a)
        check("a DEGRADED (usable) verdict still exits 0", rc == 0, repr(rc))

        C._load = lambda name: (_fake_sync_mod(manifest_verdict="FAILED") if name == "sync"
                                else orig(name))
        with contextlib.redirect_stderr(io.StringIO()):
            rc = a.fn(a)
        check("no last_verdict attr: a manifest written THIS run with FAILED -> exit 1", rc == 1, repr(rc))
    finally:
        C._load = orig


# ---- C4: project-root resolution for the offline commands ----------------------
def gate_c4_project_root(home: Path, work: Path) -> None:
    print("\n-- C4: offline commands resolve the PROJECT root, not the raw cwd --")
    L = _load_module(BUILD / "retrieval_log.py", f"dm_rl_c4_{time.time_ns()}")
    check("retrieval_log exposes project_root()", hasattr(L, "project_root"))
    if not hasattr(L, "project_root"):
        return
    proj = work / "c4proj"
    shop = proj / "shop" / "deep"
    shop.mkdir(parents=True)
    _write_manifest(proj, extractor="ast", verdict="READY")
    git_ok = _git_init(proj)   # a subdir resolves to its repo's synced root (non-git: its own project)
    check("marker walk: a subdir resolves to the ancestor holding .deepmap/graph_build.json",
          Path(L.project_root(shop)).resolve() == proj.resolve(), str(L.project_root(shop)))
    with _chdir(shop):
        check("default_root() (no DEEPMAP_PROJECT_ROOT) resolves the project root from a subdir",
              Path(L.default_root()).resolve() == proj.resolve(), str(L.default_root()))
    lone = work / "c4lone"
    lone.mkdir()
    check("no marker, no git -> the directory itself (legacy behaviour)",
          Path(L.project_root(lone)).resolve() == lone.resolve(), str(L.project_root(lone)))

    if git_ok:
        g = work / "c4git"
        (g / "a" / "b").mkdir(parents=True)
        _git_init(g)
        # round 3: the ONE root rule (sync._sync_root / the hooks / provision's server root) — with no
        # manifest anywhere the start dir is where a sync of it writes .deepmap/ (was: the git toplevel).
        check("git repo, no marker -> the start dir itself (sync._sync_root parity)",
              Path(L.project_root(g / "a" / "b")).resolve() == (g / "a" / "b").resolve(),
              str(L.project_root(g / "a" / "b")))
    else:
        check("(git on PATH for the toplevel case)", True, "skipped — git absent")

    # The CLI: `learning --enable` at the root, then --status / --disable / insights from the subdir.
    r = _cli(["learning", "--enable"], proj, home)
    check("learning --enable at the root exits 0", r.returncode == 0, r.stderr[-300:])
    r = _cli(["learning", "--status"], shop, home)
    check("learning --status from a subdir sees the project flag (ON)", "learning: ON" in r.stdout,
          r.stdout.strip()[:200])
    r = _cli(["learning", "--disable"], shop, home)
    lf = {}
    try:
        lf = json.loads((proj / ".deepmap" / "instance.json").read_text(encoding="utf-8"))["learned_fusion"]
    except Exception:
        pass
    check("learning --disable from a subdir flips the ROOT flag", lf.get("learning_enabled") is False, str(lf))
    check("learning --disable from a subdir writes NO stray <subdir>/.deepmap/instance.json",
          not (shop / ".deepmap").exists() and not (proj / "shop" / ".deepmap").exists())
    rl = _load_module(BUILD / "retrieval_log.py", f"dm_rl_c4b_{time.time_ns()}")
    rl.append_record(proj, rl.build_record("q", {}, [], ts="2026-09-01T00:00:00Z"))
    r = _cli(["insights", "--json"], shop, home)
    try:
        n = json.loads(r.stdout)["counts"]["n_records"]
    except Exception:
        n = None
    check("insights from a subdir reads the project's log (1 record)", n == 1, str(n) + r.stderr[-200:])


# ---- C5: auth vs unreachable vs query-cost; runnable hints -----------------------
def _main_with(C, argv, exc):
    from neo4j.exceptions import Neo4jError  # noqa: F401 — the driver is installed wherever the CLI runs

    saved_argv, saved_load, saved_driver = sys.argv[:], C._load, C._driver
    err, out = io.StringIO(), io.StringIO()
    rc, raised = None, None
    try:
        C._load = lambda name: types.SimpleNamespace(
            maybe_log_search=lambda *a, **k: None, resolve_arm_weights_for_live=lambda **k: None,
            embed_query=lambda q: [0.0], query_impact=lambda *a, **k: [],
            DeepMapRetriever=lambda *a, **k: types.SimpleNamespace(search=lambda *a, **k: []),
            accrual_line=lambda *a, **k: "0/150 labeled retrieval logs")
        C._driver = lambda: _RaisingDriver(exc)
        sys.argv = argv
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
            rc = C.main()
    except BaseException as e:  # noqa: BLE001
        raised = e
    finally:
        sys.argv, C._load, C._driver = saved_argv, saved_load, saved_driver
    return rc, raised, err.getvalue() + out.getvalue()


def gate_c5_error_classes(C, work: Path) -> None:
    print("\n-- C5: AuthError != unreachable; query-cost errors; runnable hints --")
    import neo4j.exceptions as E
    auth = E.Neo4jError._hydrate_neo4j(code="Neo.ClientError.Security.Unauthorized",
                                       message="The client is unauthorized due to authentication failure.")
    expired = E.Neo4jError._hydrate_neo4j(code="Neo.ClientError.Security.CredentialsExpired",
                                          message="The credentials you provided were valid, but must be changed.")
    oom = E.Neo4jError._hydrate_neo4j(
        code="Neo.TransientError.General.MemoryPoolOutOfMemoryError",
        message="The allocation of an extra 2.0 MiB would use more than the limit 358.4 MiB.")
    down = E.ServiceUnavailable("Couldn't connect to localhost:7805")

    rc, raised, txt = _main_with(C, ["deepmap", "status"], auth)
    check("AuthError: no exception escapes, exit 3", raised is None and rc == 3, f"{rc} {raised!r}")
    check("AuthError: says authentication failed + names NEO4J_PASSWORD",
          "authentication" in txt.lower() and "NEO4J_PASSWORD" in txt, txt[-300:])
    check("AuthError: NOT reported as unreachable / 'Is the instance running?'",
          "unreachable" not in txt.lower() and "is the instance running" not in txt.lower(), txt[-300:])
    rc, raised, txt = _main_with(C, ["deepmap", "search", "x"], expired)
    check("CredentialsExpired (default neo4j/neo4j store) is classified as an auth problem",
          rc == 3 and "authentication" in txt.lower() and "unreachable" not in txt.lower(), txt[-300:])

    rc, raised, txt = _main_with(C, ["deepmap", "impact", "check"], oom)
    check("impact transaction-memory OOM: exit 3, NOT 'unreachable'",
          rc == 3 and "unreachable" not in txt.lower(), txt[-300:])
    # round 3 (D6): the retriever already retries at 1 hop on a memory error, so `--max-hops 1` fails the
    # same way — the remedies that work are a qualified name and a larger heap.
    check("impact transaction-memory OOM: a runnable remedy (Class.method / DEEPMAP_NEO4J_HEAP_MAX), "
          "never --max-hops 1", "--max-hops 1" not in txt and "Class.method" in txt
          and "DEEPMAP_NEO4J_HEAP_MAX" in txt, txt[-300:])

    rc, raised, txt = _main_with(C, ["deepmap", "status"], down)
    check("ServiceUnavailable: exit 3 + says the instance is not reachable",
          rc == 3 and ("not reachable" in txt.lower() or "unreachable" in txt.lower()), txt[-300:])
    check("ServiceUnavailable hint: no relative `python build/provision.py` path",
          "python build/" not in txt, txt[-300:])
    check("ServiceUnavailable hint: names /deep-map:setup (colon form)", "/deep-map:setup" in txt, txt[-300:])

    proj = work / "c5proj"
    proj.mkdir()
    rc, out = _run_doctor(C, driver=_RaisingDriver(auth), provision=_fake_provision(set(), []), cwd=proj)
    neo = _line_with(out, "Neo4j")
    check("doctor + AuthError: FAIL line says authentication (not 'unreachable')",
          "authentication" in neo.lower() and "unreachable" not in neo.lower(), neo)
    check("doctor output never uses the '/deep-map setup' (space) form", "/deep-map setup" not in out,
          _line_with(out, "/deep-map setup"))
    rc, out = _run_doctor(C, driver=_RaisingDriver(down), provision=_fake_provision(set(), []), cwd=proj)
    neo = _line_with(out, "Neo4j")
    check("doctor + ServiceUnavailable: FAIL line points at /deep-map:setup",
          "/deep-map:setup" in neo, neo)


# ---- C6: offline honesty + input validation ----------------------------------------
def gate_c6_offline(C, home: Path, work: Path) -> None:
    print("\n-- C6: no wall of None; veto named; invalid input exits 2; impact flag notes --")
    proj = work / "c6proj"
    proj.mkdir()
    r = _cli(["learning", "--enable"], proj, home)
    check("learning --enable exits 0", r.returncode == 0, r.stderr[-200:])

    r = _cli(["tune", "--status"], proj, home)
    check("tune --status after enable (no tune yet): 'none yet', no 'None  (applied=None'",
          "none yet" in r.stdout and "applied=None" not in r.stdout, r.stdout[-400:])
    r = _cli(["tune", "--report"], proj, home)
    check("tune --report after enable: no 'CV mean delta None' / no 'stored arm_weights invalid'",
          "CV mean delta None" not in r.stdout and "arm_weights invalid" not in r.stdout
          and "objective=None" not in r.stdout, r.stdout[-400:])
    r = _cli(["insights"], proj, home)
    rl = _load_module(BUILD / "retrieval_log.py", f"dm_rl_c6_{time.time_ns()}")
    rl.append_record(proj, rl.build_record("q", {}, [], ts="2026-09-01T00:00:00Z"))
    r = _cli(["insights"], proj, home)
    check("insights after enable: current instance.json 'none yet' (not 'weights None')",
          "weights None" not in r.stdout and "none yet" in r.stdout, r.stdout[-400:])

    r = _cli(["learning", "--status"], proj, home, DEEPMAP_LEARNING="0")
    check("DEEPMAP_LEARNING=0 veto: --status names the env veto",
          "veto" in r.stdout.lower() and "DEEPMAP_LEARNING" in r.stdout, r.stdout.strip()[:300])
    check("DEEPMAP_LEARNING=0 veto: --status does NOT advise `--enable` (it can't help)",
          "learning --enable" not in r.stdout, r.stdout.strip()[:300])
    r = _cli(["learning"], work / "c6proj", home)
    check("learning hint never uses a relative `python build/deepmap_cli.py` path",
          "python build/" not in r.stdout, r.stdout.strip()[:300])

    missing = work / "no_such_dir"
    for args in (["tune", str(missing)], ["insights", str(missing)], ["enrich", str(missing), "--dry-run"]):
        r = _cli(args, proj, home)
        check(f"`{args[0]} <missing dir>` exits 2", r.returncode == 2, f"rc={r.returncode} {r.stderr[-160:]}")
    check("`tune <missing dir>` does NOT create the directory", not missing.exists())
    r = _cli(["enrich", str(proj), "--deterministic", "--max-nodes", "-5"], proj, home)
    check("enrich --max-nodes -5 -> exit 2", r.returncode == 2, f"rc={r.returncode}")
    r = _cli(["enrich", str(proj), "--batch-size", "0", "--dry-run"], proj, home)
    check("enrich --batch-size 0 -> exit 2", r.returncode == 2, f"rc={r.returncode}")
    before = (proj / ".deepmap" / "instance.json").read_text(encoding="utf-8")
    r = _cli(["learning", "--disable", "--enable"], proj, home)
    after = (proj / ".deepmap" / "instance.json").read_text(encoding="utf-8")
    check("learning --disable --enable -> exit 2 (mutually exclusive) and no write",
          r.returncode == 2 and before == after, f"rc={r.returncode}")

    r = _cli(["insights", "--since", "2999-01-01"], proj, home)
    check("insights --since filtering every record is NOT 'no retrieval log yet'",
          "no retrieval log yet" not in r.stdout and "--since" in r.stdout, r.stdout[-300:])

    # impact flags (in-process; stubbed retrieval captures what reaches the query).
    cap: dict = {}

    class _R:
        _IMPACT_MAX_HOPS_CAP = 5

        @staticmethod
        def query_impact(drv, name, limit, edge_types=None, max_hops=None):
            cap.update(name=name, edge_types=edge_types, max_hops=max_hops)
            return []

    orig_load, orig_driver = C._load, C._driver
    C._load = lambda name: _R if name == "retrieval" else orig_load(name)
    C._driver = lambda: _OkDriver()
    try:
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            C.cmd_impact(argparse.Namespace(name="  build_record \n", limit=5, max_hops=None, edges=None))
        check("impact strips a whitespace-padded symbol", cap.get("name") == "build_record", repr(cap.get("name")))
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            C.cmd_impact(argparse.Namespace(name="x", limit=5, max_hops=0, edges=None))
        check("--max-hops 0 -> clamped to 1 with a note", cap.get("max_hops") == 1 and "max-hops" in err.getvalue(),
              f"{cap.get('max_hops')} {err.getvalue().strip()}")
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            C.cmd_impact(argparse.Namespace(name="x", limit=5, max_hops=9, edges=None))
        check("--max-hops 9 -> clamped to 5 with a note", cap.get("max_hops") == 5 and "max-hops" in err.getvalue(),
              f"{cap.get('max_hops')} {err.getvalue().strip()}")
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            C.cmd_impact(argparse.Namespace(name="x", limit=5, max_hops=None, edges="CALLS,BOGUS"))
        check("--edges with an unknown type warns (names it)", "BOGUS" in err.getvalue(), err.getvalue().strip())
        check("--edges: the unknown type is dropped, the known ones kept",
              [t.upper() for t in (cap.get("edge_types") or [])] == ["CALLS"], str(cap.get("edge_types")))
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            C.cmd_impact(argparse.Namespace(name="x", limit=5, max_hops=2, edges="calls,routes_to"))
        check("in-range --max-hops + known --edges stay silent",
              err.getvalue().strip() == "" and cap.get("max_hops") == 2, err.getvalue().strip())
    finally:
        C._load, C._driver = orig_load, orig_driver


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 72)
    print("DeepMap — CLI per-project + honesty gates (W3: C1-C6; no Neo4j, no sockets)")
    print("=" * 72)
    saved_env = {k: os.environ.get(k) for k in _SCRUB + ("DEEPMAP_HOME",)}
    for k in _SCRUB:
        os.environ.pop(k, None)
    with tempfile.TemporaryDirectory() as td:
        base = Path(td).resolve()
        home = base / "home"
        home.mkdir()
        work = base / "work"
        work.mkdir()
        os.environ["DEEPMAP_HOME"] = str(home)
        try:
            if str(BUILD) not in sys.path:
                sys.path.insert(0, str(BUILD))
            C = _load_cli()
            PR = _load_module(BUILD / "port_registry.py", f"dm_pr_honesty_{time.time_ns()}")
            for gate in (lambda: gate_c1_doctor_mcp_ports(C, PR, work),
                         lambda: gate_c2_doctor_extractor(C, work),
                         lambda: gate_c3_sync(C, work),
                         lambda: gate_c4_project_root(home, work),
                         lambda: gate_c5_error_classes(C, work),
                         lambda: gate_c6_offline(C, home, work)):
                try:
                    gate()
                except Exception as exc:  # noqa: BLE001 — a crashing gate is a RED gate, not a crash
                    import traceback
                    traceback.print_exc()
                    check(f"gate ran without crashing ({type(exc).__name__}: {exc})", False)
        finally:
            for k, v in saved_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
    print("\n" + "=" * 72)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
