#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ROUND-8 W1 gates: one spelling for DeepMap-generated bolt URIs (J4), core.longpaths on the W1 git probes (J6),
the post-edit sync flag's "a commit also re-syncs it" claim made only when the hook provably re-syncs THIS project
(J1, hook side), and the W1 doc drift (hooks/*, port_registry, commands, skills/deep-map/SKILL.md).

  A  J4 — the helper. port_registry.bolt_uri(port) -> "bolt://127.0.0.1:<port>" (the address every per-project
     instance binds: provision's managed conf block writes server.bolt.listen_address=127.0.0.1:<bolt>).
     "localhost" resolves to ::1 FIRST on Windows, so every new connection paid a refused IPv6 connect (~2 s:
     driver connect p50 2054 ms vs 29 ms, live) and a listener on [::1]:<port> (Docker publishes [::1]:7687)
     could be reached instead of the instance. canonical_bolt_uri / same_bolt_endpoint keep URI COMPARISONS
     backward-compatible (bolt://localhost:7689 == bolt://127.0.0.1:7689).
  B  J4 — every URI port_registry GENERATES uses it (registry / instance-conf / legacy routes, neo4j_uri_for,
     registry_stats, the legacy owner verdict and the unreadable-registry text); an operator's NEO4J_URI stays
     VERBATIM; no DeepMap-generated "bolt://localhost" string is left in port_registry or the hooks.
  C  J4 — the hooks' probes. A DeepMap-managed instance (route registry / instance-conf) is probed on 127.0.0.1
     ONLY — whatever loopback spelling its URI carries — so a ::1 listener can never "prove" it up; an
     operator's "localhost" endpoint is probed IPv4 first, then ::1.
  D  J6 — core.longpaths. On Windows git refuses paths past MAX_PATH unless core.longpaths is set: in a repository
     whose root is ~215 chars, `rev-list` cannot read the loose objects (.git/objects/xx/<38 hex>) and the hooks'
     commit distance went missing. port_registry._toplevel_raw, deepmap_hook_state.git_head / commit_distance
     and the sync flag's hook probe pass `-c core.longpaths=true` (Windows only); a REAL long-root repo proves it.
  E  J1 (hook side) — the sync flag's claim. The `# deepmap` marker alone was taken as proof. The round-7 review
     reproduced a hook file shared across projects (a global core.hooksPath) carrying project A's block while
     project B was told "a commit also re-syncs it"; a Python hook with sh appended, a block after an
     unconditional exec, and a deleted plugin's deepmap_cli.py never run it either. The claim now needs a DeepMap
     line that syncs THIS project's root (the sync root, not merely the git toplevel), reachable, in an sh hook.
  G  The open round-7 W2->W1 NEED: port_registry._pid_alive treated a POSIX ZOMBIE as running (os.kill(pid, 0)
     succeeds on one), so the E1 key migration waited on a dead instance forever. It now reads /proc state (Z/X ->
     dead unless a thread still runs, "Zl") like provision._pid_alive; tested through a fake /proc.
  H  J5 (hook side). After a plugin update the project's servers keep LISTENING on the old code, so the SessionStart
     notify's down-at-start reconnect notes never fired while the heal replaced them (and the client's connection
     broke). port_registry.code_identity / server_code_stale define the owner-sidecar code fields (code_root,
     code_version) heal and the hooks share; notify adds a reconnect-via-/mcp note when this project's live
     servers provably run another version (an older sidecar without the fields is unknown, never a false alarm).
  F  Doc drift in W1's files: setup.md names the 127.0.0.1 bolt URI; plugin-update.md says servers keep running the
     previous version's code until they restart (/deep-map:setup restarts them); no "bolt://localhost" is left
     in commands/*.md or SKILL.md.

Hermetic: temp project dirs + a temp DEEPMAP_HOME + temp git repos; socket connects are stubbed in-process (no socket
is opened); no Neo4j, no network, never touches ~/.deepmap or the committed .mcp.json. Exit 0 = GREEN.
"""
from __future__ import annotations

import ast
import importlib.util
import io
import json
import os
import re
import shutil
import stat
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
REAL_REPO = Path(__file__).resolve().parent.parent
HOOKS = REPO / "hooks"
BUILD = REPO / "build"
COMMANDS = REPO / "commands"
SKILL = REPO / "skills" / "deep-map" / "SKILL.md"
sys.path.insert(0, str(BUILD))
import port_registry as PR  # noqa: E402

_PASS = 0
_FAIL = 0
_SCRUB = ("PYTHONUTF8", "PYTHONIOENCODING", "DEEPMAP_HOOKS", "DEEPMAP_HEAL", "DEEPMAP_NUDGE",
          "DEEPMAP_SYNC_FLAG", "DEEPMAP_SYNC_FLAG_EVERY", "DEEPMAP_ANNOUNCE", "DEEPMAP_ONBOARD",
          "DEEPMAP_HIBERNATE", "DEEPMAP_RETRIEVAL_LOG", "CLAUDE_PROJECT_DIR", "DEEPMAP_PROJECT_ROOT",
          "NEO4J_URI", "DEEPMAP_LEARNING", "CLAUDE_PLUGIN_ROOT", "DEEPMAP_NOTIFY_HEAL_WAIT", "DEEPMAP_PYTHON")
_GIT_ENV = {"GIT_AUTHOR_NAME": "g", "GIT_AUTHOR_EMAIL": "g@x", "GIT_COMMITTER_NAME": "g",
            "GIT_COMMITTER_EMAIL": "g@x", "GIT_CONFIG_NOSYSTEM": "1"}
_WIN = os.name == "nt"
_LONG = ["-c", "core.longpaths=true"]


def check(label: str, cond: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {label}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}{(' — ' + str(detail)[:600]) if detail else ''}")


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"dmr8_{name}_{time.monotonic_ns()}", HOOKS / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _tmpdir():
    return tempfile.TemporaryDirectory(ignore_cleanup_errors=True)


class _PythonOnPath:
    """ROUND-11 L3: a fixture that spells the interpreter as a bare `python` — the literal line an installer wrote — needs
    that word to resolve: the sync flag's _interp_runs (ROUND-10 L5) rightly makes no claim for a `python` that is not
    on PATH, which is every python3-only host (stock Debian / Ubuntu / macOS). When PATH lacks `python`, a temp dir
    holding a `python` launcher for the running interpreter goes first on PATH for the duration; a host that has
    `python` is left untouched."""

    def __enter__(self):
        self.saved = os.environ.get("PATH")
        self.tmp = None
        if shutil.which("python"):
            return self
        self.tmp = tempfile.TemporaryDirectory(prefix="dmr11py", ignore_cleanup_errors=True)
        d = Path(self.tmp.name)
        if _WIN:
            (d / "python.cmd").write_text(f'@"{sys.executable}" %*\r\n', encoding="utf-8")
        else:
            f = d / "python"
            f.write_text("#!/bin/sh\nexec '" + sys.executable.replace("'", "'\\''") + "' \"$@\"\n", encoding="utf-8")
            os.chmod(f, 0o755)
        os.environ["PATH"] = str(d) + os.pathsep + (self.saved or "")
        return self

    def __exit__(self, *exc):
        if self.saved is None:
            os.environ.pop("PATH", None)
        else:
            os.environ["PATH"] = self.saved
        if self.tmp is not None:
            self.tmp.cleanup()
        return False


def _git(d: Path, *args, check_rc: bool = False) -> str:
    p = subprocess.run(["git", *_LONG, "-C", str(d), *args], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=60, env=dict(os.environ, **_GIT_ENV))
    if check_rc and p.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {p.stderr.strip()[:300]}")
    return (p.stdout or "").strip()


def _repo(parent: Path, name: str = "proj") -> Path:
    d = parent / name
    d.mkdir(parents=True, exist_ok=True)
    _git(d, "init", "-q", check_rc=True)
    _git(d, "commit", "-q", "--allow-empty", "-m", "c1", check_rc=True)
    return d.resolve()


def _manifest(root: Path, verdict="READY", nodes=10) -> Path:
    d = root / ".deepmap"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "graph_build.json"
    p.write_text(json.dumps({"nodes": nodes, "extractor": "graphify", "verdict": verdict}), encoding="utf-8")
    return p


def _register(home: Path, root: Path, *, search=18911, embed=18912, bolt=17689, http=17476, others=()) -> None:
    projects = {PR.project_key(root): {"root": str(root).replace("\\", "/"), "bolt_port": bolt, "http_port": http,
                                       "search_port": search, "embed_port": embed}}
    for key, ent in others:
        projects[key] = ent
    home.mkdir(parents=True, exist_ok=True)
    (home / "registry.json").write_text(json.dumps({"version": 1, "kind": "deepmap_port_registry",
                                                    "projects": projects}), encoding="utf-8")


class _Env:
    """Scrub the knobs, point DEEPMAP_HOME at `home`, apply `extra`; restore on exit."""

    def __init__(self, home: Path, extra=None):
        self.home, self.extra = home, dict(extra or {})

    def __enter__(self):
        self.saved = {k: os.environ.get(k) for k in set(_SCRUB) | {"DEEPMAP_HOME"} | set(self.extra)}
        for k in _SCRUB:
            os.environ.pop(k, None)
        os.environ["DEEPMAP_HOME"] = str(self.home)
        os.environ.update(self.extra)
        return self

    def __exit__(self, *exc):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class _Conn:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeNet:
    """Stub socket.create_connection (globally, restored on exit). `listening` = the (host, port) pairs that accept.
    "localhost" resolves like Windows: ::1 FIRST, then 127.0.0.1 — so a bare create_connection(("localhost", p))
    succeeds when either listens, exactly like the real call. Records every (host, port) dialled."""

    def __init__(self, listening=()):
        self.listening = {(str(h), int(p)) for h, p in listening}
        self.calls: list = []

    def _fake(self, address, timeout=None, source_address=None, **kw):
        host, port = str(address[0]), int(address[1])
        self.calls.append((host, port))
        cands = ["::1", "127.0.0.1"] if host.lower() == "localhost" else [host]
        if any((h, port) in self.listening for h in cands):
            return _Conn()
        raise ConnectionRefusedError(f"refused {host}:{port}")

    def __enter__(self):
        import socket
        self._sock = socket
        self._saved = socket.create_connection
        socket.create_connection = self._fake
        return self

    def __exit__(self, *exc):
        self._sock.create_connection = self._saved


class _RecordRun:
    """Stub subprocess.run (globally, restored on exit): records argv, answers like a successful git."""

    def __init__(self, stdout=""):
        self.calls: list = []
        self.stdout = stdout

    def _fake(self, argv, *a, **kw):
        self.calls.append(list(argv))
        out = self.stdout
        if not kw.get("text") and not kw.get("encoding"):
            out = out.encode("utf-8")
        return subprocess.CompletedProcess(argv, 0, stdout=out, stderr=b"" if isinstance(out, bytes) else "")

    def __enter__(self):
        self._saved = subprocess.run
        subprocess.run = self._fake
        return self

    def __exit__(self, *exc):
        subprocess.run = self._saved


# =========================================================================== A: the helper
def gate_a_helper() -> None:
    print("\n-- A (J4): port_registry.bolt_uri + the comparison helpers --")
    for fn in ("bolt_uri", "canonical_bolt_uri", "same_bolt_endpoint", "is_loopback_host", "git_longpaths_args"):
        check(f"port_registry exposes {fn}()", callable(getattr(PR, fn, None)))
    check("port_registry.LOOPBACK_HOST == '127.0.0.1'", getattr(PR, "LOOPBACK_HOST", None) == "127.0.0.1")
    if not callable(getattr(PR, "bolt_uri", None)):
        return
    check("bolt_uri(7689) == 'bolt://127.0.0.1:7689'", PR.bolt_uri(7689) == "bolt://127.0.0.1:7689",
          PR.bolt_uri(7689))
    check("bolt_uri(7687) == 'bolt://127.0.0.1:7687' (the base port too)", PR.bolt_uri(7687) == "bolt://127.0.0.1:7687")
    check("bolt_uri accepts an int-valued string", PR.bolt_uri(" 7690 ") == "bolt://127.0.0.1:7690")
    check("bolt_uri(0) is allowed (provision's no-route sentinel port)", PR.bolt_uri(0) == "bolt://127.0.0.1:0")
    for bad in (True, -1, 65536, None, 7689.0, "x", "76 89", ""):
        try:
            PR.bolt_uri(bad)
            ok = False
        except ValueError:
            ok = True
        except Exception:
            ok = False
        check(f"bolt_uri({bad!r}) raises ValueError", ok)
    eq = PR.same_bolt_endpoint
    check("same_bolt_endpoint: localhost == 127.0.0.1 (a URI an older build wrote still matches)",
          eq("bolt://localhost:7689", "bolt://127.0.0.1:7689"))
    check("same_bolt_endpoint: [::1] / case / trailing slash normalised",
          eq("BOLT://[::1]:7689/", PR.bolt_uri(7689)) and eq("bolt://LocalHost:7689", "bolt://127.0.0.1:7689/"))
    check("same_bolt_endpoint: a missing port on a bolt scheme is the driver default 7687",
          eq("bolt://localhost", PR.bolt_uri(7687)))
    check("same_bolt_endpoint: different ports differ", not eq("bolt://localhost:7689", "bolt://127.0.0.1:7691"))
    check("same_bolt_endpoint: a remote host is never equated with loopback",
          not eq("bolt://10.0.0.5:7689", PR.bolt_uri(7689)))
    check("same_bolt_endpoint: empty / None -> False", not eq(None, None) and not eq("", ""))
    c = PR.canonical_bolt_uri
    check("canonical_bolt_uri keeps a remote endpoint's host", c("neo4j+s://db.example.io:7687") ==
          "neo4j+s://db.example.io:7687", c("neo4j+s://db.example.io:7687"))
    check("canonical_bolt_uri(None) is None and never raises on garbage", c(None) is None and c(object()) is not None)
    check("is_loopback_host: localhost / 127.0.0.1 / ::1 / [::1]; not 10.0.0.5",
          all(PR.is_loopback_host(h) for h in ("localhost", "127.0.0.1", "::1", "[::1]", "LOCALHOST"))
          and not PR.is_loopback_host("10.0.0.5") and not PR.is_loopback_host(None))
    la = PR.git_longpaths_args() if callable(getattr(PR, "git_longpaths_args", None)) else None
    check("git_longpaths_args(): ['-c','core.longpaths=true'] on Windows, [] elsewhere (a fresh list)",
          la == (_LONG if _WIN else []) and la is not PR.git_longpaths_args(), la)


# =========================================================================== B: generated URIs
def gate_b_generated_uris() -> None:
    print("\n-- B (J4): every URI port_registry generates dials 127.0.0.1; NEO4J_URI stays verbatim --")
    with _tmpdir() as tw, _tmpdir() as th:
        home, root = Path(th), _repo(Path(tw))
        env = {"DEEPMAP_HOME": str(home)}
        r0 = PR.resolve_neo4j_route(root, env=env, timeout=10)
        check("no registry -> the legacy route is bolt://127.0.0.1:7687", r0.source == "legacy-unverified"
              and r0.uri == "bolt://127.0.0.1:7687", repr(r0))
        check("neo4j_uri_for (no registry) == bolt://127.0.0.1:7687", PR.neo4j_uri_for(root, env) ==
              "bolt://127.0.0.1:7687")
        _register(home, root, bolt=17689, http=17476)
        r1 = PR.resolve_neo4j_route(root, env=env, timeout=10)
        check("registry route -> bolt://127.0.0.1:<its bolt>", r1.source == "registry"
              and r1.uri == "bolt://127.0.0.1:17689" and r1.bolt == 17689, repr(r1))
        check("neo4j_uri_for (registry) == bolt://127.0.0.1:17689",
              PR.neo4j_uri_for(root, env) == "bolt://127.0.0.1:17689")
        check("registry route unpacks to the 127.0.0.1 tuple",
              tuple(r1) == ("bolt://127.0.0.1:17689", "registry", None))
        stats = PR.registry_stats(env)
        uris = [p.get("neo4j_uri") for p in stats.get("projects", [])]
        check("registry_stats neo4j_uri is the 127.0.0.1 form", uris == ["bolt://127.0.0.1:17689"], uris)
        (home / "registry.json").unlink()
        conf = PR.instance_conf_path(PR.project_key(root), env)
        conf.parent.mkdir(parents=True, exist_ok=True)
        conf.write_text("server.bolt.listen_address=127.0.0.1:17693\nserver.http.listen_address=127.0.0.1:17480\n",
                        encoding="utf-8")
        r2 = PR.resolve_neo4j_route(root, env=env, timeout=10)
        check("instance-conf route -> bolt://127.0.0.1:<conf bolt>", r2.source == "instance-conf"
              and r2.uri == "bolt://127.0.0.1:17693", repr(r2))
        for override in ("bolt://localhost:7999", "bolt://LOCALHOST:7999/", "neo4j+s://db.example.io"):
            r3 = PR.resolve_neo4j_route(root, env=dict(env, NEO4J_URI=override), timeout=10)
            check(f"an operator NEO4J_URI {override!r} is used VERBATIM (never rewritten)",
                  r3.source == "env" and r3.uri == override, repr(r3))
        ok, why = PR.legacy_owner_verdict(root, "/somewhere/else/entirely", True, timeout=10)
        check("legacy_owner_verdict names the base as bolt://127.0.0.1:7687 by default",
              not ok and "bolt://127.0.0.1:7687" in str(why) and "localhost" not in str(why), why)
        (home / "registry.json").write_text("{not json", encoding="utf-8")
        r4 = PR.resolve_neo4j_route(root, env=env, timeout=10)
        check("the unreadable-registry problem text names bolt://127.0.0.1:7687, not localhost",
              r4.source == "none" and "bolt://127.0.0.1:7687" in str(r4.problem) and "localhost" not in str(r4.problem),
              r4.problem)
    # no DeepMap-generated "bolt://localhost" string literal is left (docstrings/comments may still NAME the old form)
    files = [BUILD / "port_registry.py"] + sorted(HOOKS.glob("*.py"))
    hits = []
    for f in files:
        tree = ast.parse(f.read_text(encoding="utf-8"))
        docs = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
                first = node.body[0]
                if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                        and isinstance(first.value.value, str):
                    docs.add(id(first.value))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs \
                    and "bolt://localhost" in node.value:
                hits.append(f"{f.name}:{node.lineno}")
    check("no generated 'bolt://localhost' string literal in port_registry.py or hooks/*.py", not hits, hits)


# =========================================================================== C: the hooks' probes
def gate_c_hook_probes() -> None:
    print("\n-- C (J4): the hooks probe a DeepMap instance on 127.0.0.1 only; 'localhost' IPv4-first --")
    hs = _load("deepmap_hook_state")
    with _FakeNet(listening=[("127.0.0.1", 7999)]) as net:
        up = hs.port_listening(7999, host="localhost")
    check("port_listening('localhost') dials 127.0.0.1 FIRST (never a refused ::1 connect before it)",
          up and net.calls[:1] == [("127.0.0.1", 7999)] and len(net.calls) == 1, net.calls)
    with _FakeNet(listening=[("::1", 7999)]) as net:
        up = hs.port_listening(7999, host="localhost")
    check("port_listening('localhost'): an IPv6-only operator listener is still found (127.0.0.1, then ::1)",
          up and net.calls == [("127.0.0.1", 7999), ("::1", 7999)], net.calls)
    with _FakeNet(listening=[("::1", 7999)]) as net:
        up = hs.port_listening(7999, host="[::1]")
    check("port_listening('[::1]') dials ::1 (bracketed IPv6 literal)", up and net.calls == [("::1", 7999)], net.calls)
    with _FakeNet(listening=[("127.0.0.1", 8123)]) as net:
        up = hs.port_listening(8123)
    check("port_listening default host stays 127.0.0.1 (the MCP probes)", up and net.calls == [("127.0.0.1", 8123)],
          net.calls)
    with _tmpdir() as tw, _tmpdir() as th:
        home, root = Path(th), _repo(Path(tw))
        _manifest(root)
        _register(home, root, bolt=17689, http=17476)
        with _Env(home):
            t = hs.bolt_target(root)
        check("bolt_target (registry route) == ('127.0.0.1', 17689)", t == ("127.0.0.1", 17689), t)
        # an older provision injected / recorded the localhost form: the managed instance is still probed on IPv4
        real = hs._registry()
        saved = real.resolve_neo4j_route
        try:
            real.resolve_neo4j_route = lambda r, **kw: real.Neo4jRoute("bolt://localhost:17689", "registry",
                                                                        bolt=17689, http=17476, key="k")
            with _Env(home):
                t2 = hs.bolt_target(root)
            real.resolve_neo4j_route = lambda r, **kw: real.Neo4jRoute("bolt://[::1]:17693", "instance-conf",
                                                                        bolt=17693, http=17480, key="k")
            with _Env(home):
                t3 = hs.bolt_target(root)
        finally:
            real.resolve_neo4j_route = saved
        check("bolt_target: a registry route spelled bolt://localhost:<p> is probed on 127.0.0.1",
              t2 == ("127.0.0.1", 17689), t2)
        check("bolt_target: an instance-conf route spelled [::1] is probed on 127.0.0.1 (the only address it binds)",
              t3 == ("127.0.0.1", 17693), t3)
        with _Env(home, {"NEO4J_URI": "bolt://localhost:7999"}):
            t4 = hs.bolt_target(root)
        check("bolt_target: an operator's bolt://localhost endpoint keeps its host (probed IPv4-first)",
              t4 == ("localhost", 7999), t4)
        with _Env(home, {"NEO4J_URI": "bolt://db.example.io:7687"}):
            t5 = hs.bolt_target(root)
        check("bolt_target: a remote operator endpoint is never probed", t5 is None, t5)
        # the recorded 'database down' failure must NOT be superseded by a ::1 listener on this instance's port
        (root / ".mcp.json").write_text(json.dumps({"mcpServers": {
            "deepmap-search": {"type": "http", "url": "http://127.0.0.1:18911/mcp"},
            "deepmap-embed": {"type": "http", "url": "http://127.0.0.1:18912/mcp"}}}), encoding="utf-8")
        with _Env(home), _FakeNet(listening=[("127.0.0.1", 18911), ("127.0.0.1", 18912), ("::1", 17689)]) as net:
            sup = hs.heal_problem_superseded(root, ("neo4j", "database unreachable"), refusal_fields=True)
        check("a ::1 listener on the instance's bolt port never 'proves' the DeepMap instance up",
              sup is False and ("::1", 17689) not in net.calls and ("localhost", 17689) not in net.calls, net.calls)
        with _Env(home), _FakeNet(listening=[("127.0.0.1", 18911), ("127.0.0.1", 18912),
                                             ("127.0.0.1", 17689)]) as net:
            sup = hs.heal_problem_superseded(root, ("neo4j", "database unreachable"), refusal_fields=True)
        check("control: the instance listening on 127.0.0.1 does supersede the old failure",
              sup is True and ("127.0.0.1", 17689) in net.calls, net.calls)


# =========================================================================== D: core.longpaths
def _mk_long_root(base: Path, target_len: int = 215) -> Path:
    """A directory whose absolute path is ~target_len chars under `base` (git's -C still works below MAX_PATH, but
    .git/objects/xx/<38 hex> lands past it)."""
    cur = base.resolve()
    i = 0
    while len(str(cur)) < target_len - 10:
        room = target_len - len(str(cur)) - 1
        cur = cur / (f"L{i}" + "x" * max(1, min(60, room - 2)))
        i += 1
    cur.mkdir(parents=True, exist_ok=True)
    return cur


def _rm_long(base: Path) -> None:
    """Delete ONLY `base` (a temp dir this gate created), long paths and git's read-only objects included."""
    p = str(base.resolve())
    if _WIN and not p.startswith("\\\\?\\"):
        p = "\\\\?\\" + p

    def onexc(fn, path, exc):
        try:
            os.chmod(path, stat.S_IWRITE)
            fn(path)
        except Exception:
            pass
    try:
        shutil.rmtree(p, onexc=onexc)
    except TypeError:   # Python < 3.12
        shutil.rmtree(p, onerror=onexc)
    except Exception:
        pass


def gate_d_longpaths() -> None:
    print("\n-- D (J6): the W1 git probes pass core.longpaths (Windows) --")
    hs = _load("deepmap_hook_state")
    sf = _load("deepmap_postedit_syncflag")
    want = _LONG if _WIN else []

    def has_flag(argv) -> bool:
        if not argv:
            return False
        i = argv.index("-C") if "-C" in argv else len(argv)
        pre = argv[1:i]
        return (pre == want) if _WIN else ("core.longpaths=true" not in argv)

    with _tmpdir() as tw:
        root = Path(tw).resolve()
        with _RecordRun(stdout=str(root) + "\n") as rr:
            PR._toplevel_raw(root, 5)
        check("port_registry._toplevel_raw: `git -c core.longpaths=true -C <root> rev-parse --show-toplevel`"
              if _WIN else "port_registry._toplevel_raw: no longpaths flag off Windows",
              len(rr.calls) == 1 and has_flag(rr.calls[0]) and rr.calls[0][-2:] == ["rev-parse", "--show-toplevel"],
              rr.calls)
        with _RecordRun(stdout="a" * 40 + "\n") as rr:
            hs.git_head(root)
        check("hook_state.git_head passes the longpaths flag" if _WIN else "hook_state.git_head: no flag off Windows",
              len(rr.calls) == 1 and has_flag(rr.calls[0]) and rr.calls[0][-2:] == ["rev-parse", "HEAD"], rr.calls)
        with _RecordRun(stdout="3\n") as rr:
            hs.commit_distance(root, "a" * 40)
        check("hook_state.commit_distance passes the longpaths flag" if _WIN
              else "hook_state.commit_distance: no flag off Windows",
              len(rr.calls) == 1 and has_flag(rr.calls[0]) and "rev-list" in rr.calls[0], rr.calls)
        with _RecordRun(stdout=f"{root}\n.git/hooks/post-commit\n") as rr:
            sf._git_hook_installed(root)
        check("syncflag's hook probe passes the longpaths flag" if _WIN else "syncflag's hook probe: no flag off Windows",
              len(rr.calls) >= 1 and has_flag(rr.calls[0]) and "--git-path" in rr.calls[0], rr.calls)
    if not _WIN:
        check("(real long-root repo check is Windows-only)", True, "skipped off Windows")
        return
    base = Path(tempfile.mkdtemp(prefix="dm-w1r8-"))
    try:
        root = _mk_long_root(base, 215)
        _git(root, "init", "-q", check_rc=True)
        (root / "a.py").write_text("x = 1\n", encoding="utf-8")
        _git(root, "add", "a.py", check_rc=True)
        _git(root, "commit", "-q", "-m", "c1", check_rc=True)
        c1 = _git(root, "rev-parse", "HEAD")
        (root / "a.py").write_text("x = 2\n", encoding="utf-8")
        _git(root, "commit", "-q", "-am", "c2", check_rc=True)
        c2 = _git(root, "rev-parse", "HEAD")
        plain = subprocess.run(["git", "-C", str(root), "rev-list", "--count", f"{c1}..HEAD"], capture_output=True,
                               text=True, timeout=30, env=dict(os.environ, **_GIT_ENV))
        check(f"(precondition: root is {len(str(root))} chars and plain `git rev-list` fails there)",
              len(str(root)) >= 205 and len(str(root)) < 250 and plain.returncode != 0,
              f"len={len(str(root))} rc={plain.returncode} {plain.stderr.strip()[:200]}")
        check("hook_state.git_head reads HEAD in a ~215-char repo root", hs.git_head(root) == c2.lower(),
              (hs.git_head(root), c2))
        d = hs.commit_distance(root, c1)
        check("hook_state.commit_distance works in a ~215-char repo root (1 commit later)", d == 1, d)
        with _tmpdir() as th:
            man = _manifest(root)
            data = json.loads(man.read_text(encoding="utf-8"))
            data["built_at_sha"] = c1
            man.write_text(json.dumps(data), encoding="utf-8")
            with _Env(Path(th)):
                st = hs.staleness(root, data)
        check("the staleness note carries the commit distance there ('1 commit later')",
              isinstance(st, dict) and st.get("kind") == "stale" and st.get("distance") == 1, st)
    finally:
        _rm_long(base)
    check("the long-root temp tree was removed (exact path)", not base.exists(), str(base))


# =========================================================================== E: the sync flag's claim
def _provision_block(root: Path) -> str:
    """provision's REAL post-commit block for `root` (a drift lock with W2's git-hook --install); a hand-written
    block of the same form when provision is unavailable."""
    try:
        if str(REAL_REPO / "build") not in sys.path:
            sys.path.insert(0, str(REAL_REPO / "build"))
        import provision  # type: ignore
        fn = getattr(provision, "_git_hook_block")
        try:
            return fn(str(root))
        except TypeError:
            return fn(str(root), str(root))
    except Exception:
        cli = (REAL_REPO / "build" / "deepmap_cli.py").as_posix()
        py = "'" + Path(sys.executable).as_posix().replace("'", "'\\''") + "'"   # ROUND-11 L3: never a bare `python`
        return ("# >>> deepmap (managed; existing hook preserved on chain) >>>\n"
                f"mkdir -p '/tmp' 2>/dev/null; ( PYTHONUTF8=1 nohup {py} '{cli}' sync '{root.as_posix()}' "
                ">'/tmp/sync.log' 2>&1 & ) || true   # deepmap\n# <<< deepmap <<<\n")


def _hook_file(repo: Path) -> Path:
    rel = _git(repo, "rev-parse", "--git-path", "hooks/post-commit")
    p = Path(rel) if Path(rel).is_absolute() else repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _write_hook(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")
    if not _WIN:
        os.chmod(path, 0o755)


def gate_e_syncflag_claim() -> None:
    print("\n-- E (J1, hook side): 'a commit also re-syncs it' only when the hook re-syncs THIS project --")
    cli = (REAL_REPO / "build" / "deepmap_cli.py").as_posix()
    with _tmpdir() as tw, _tmpdir() as th:
        home = Path(th)
        with _Env(home):
            sf = _load("deepmap_postedit_syncflag")
            a = _repo(Path(tw), "projA")
            b = _repo(Path(tw), "projB")
            _manifest(a)
            _manifest(b)
            ha = _hook_file(a)
            block_a = _provision_block(a)
            _write_hook(ha, "#!/bin/sh\n" + block_a)
            check("provision's real block for project A -> A's claim is made", sf._git_hook_installed(a) is True,
                  block_a)
            # the installers' double-quoted form (the Git-Bash /c/... path form on Windows)
            ap = a.as_posix()
            if _WIN and re.match(r"^[A-Za-z]:/", ap):
                ap = "/" + ap[0].lower() + ap[2:]
            _write_hook(ha, "#!/bin/sh\necho mine\n# >>> deepmap (managed; existing hook preserved on chain) >>>\n"
                            f"mkdir -p \"$HOME/.deepmap\" 2>/dev/null; ( nohup python \"{cli}\" sync \"{ap}\" "
                            ">\"$HOME/.deepmap/sync.log\" 2>&1 & ) || true   # deepmap\n# <<< deepmap <<<\n")
            with _PythonOnPath():   # ROUND-11 L3: the installers' literal bare `python` must resolve (python3-only hosts)
                claimed = sf._git_hook_installed(a)
            check("the shell installers' double-quoted block (Git-Bash path form) -> the claim is made",
                  claimed is True, ha.read_text(encoding="utf-8"))
            # a hook file SHARED across projects (core.hooksPath) carrying A's block: B must not be told it re-syncs
            shared = Path(tw) / "shared-hooks"
            shared.mkdir()
            _git(a, "config", "core.hooksPath", str(shared))
            _git(b, "config", "core.hooksPath", str(shared))
            hs_path = _hook_file(b)
            check("(precondition: A and B resolve the same shared post-commit hook)",
                  _hook_file(a).resolve() == hs_path.resolve(), (str(_hook_file(a)), str(hs_path)))
            _write_hook(hs_path, "#!/bin/sh\n" + _provision_block(a))
            check("shared hook with A's block: A still gets the claim", sf._git_hook_installed(a) is True)
            check("shared hook with A's block: B is NOT told a commit re-syncs it (round-7 review repro)",
                  sf._git_hook_installed(b) is False)
            _write_hook(hs_path, "#!/bin/sh\n" + _provision_block(a) + _provision_block(b))
            check("a shared hook scoped per project (both blocks) -> B's claim is made",
                  sf._git_hook_installed(b) is True)
            _git(a, "config", "--unset", "core.hooksPath")
            _git(b, "config", "--unset", "core.hooksPath")
            # a sub-project: the sync root is <top>/sub, the hook syncs the toplevel (the pre-J1 block)
            top = _repo(Path(tw), "mono")
            sub = top / "svc"
            sub.mkdir()
            _manifest(sub)
            hm = _hook_file(top)
            _write_hook(hm, "#!/bin/sh\n" + _provision_block(top))
            check("sub-project synced at <top>/svc, hook syncs the git toplevel -> no claim for the sub-project",
                  sf._git_hook_installed(sub) is False)
            _write_hook(hm, "#!/bin/sh\n" + _provision_block(sub))
            check("hook syncs the sub-project's own sync root -> the claim is made", sf._git_hook_installed(sub) is True)
            # a Python hook with the sh block appended never runs it
            _write_hook(ha, "#!/usr/bin/env python3\nprint('hi')\n" + _provision_block(a))
            check("a Python post-commit hook with the block appended -> no claim", sf._git_hook_installed(a) is False)
            _write_hook(ha, "#!/usr/bin/env bash\necho hi\n" + _provision_block(a))
            check("control: `#!/usr/bin/env bash` hook -> the claim is made", sf._git_hook_installed(a) is True)
            # unreachable after an unconditional exec / exit, reachable after an indented (conditional) one
            _write_hook(ha, "#!/bin/sh\nexec lefthook run post-commit \"$@\"\n" + _provision_block(a))
            check("block after an unconditional `exec` -> no claim", sf._git_hook_installed(a) is False)
            _write_hook(ha, "#!/bin/sh\necho x\nexit 0\n" + _provision_block(a))
            check("block after an unconditional `exit 0` -> no claim", sf._git_hook_installed(a) is False)
            _write_hook(ha, "#!/usr/bin/env sh\n. \"$(dirname \"$0\")/h\"\n" + _provision_block(a))
            check("block after husky's runner (`. \"$(dirname \"$0\")/h\"`) -> no claim",
                  sf._git_hook_installed(a) is False)
            _write_hook(ha, "#!/bin/sh\n" + _provision_block(a) + "exec lefthook run post-commit \"$@\"\n")
            check("block inserted BEFORE the exec -> the claim is made", sf._git_hook_installed(a) is True)
            _write_hook(ha, "#!/bin/sh\nif [ -n \"$SKIP\" ]; then\n  exit 0\nfi\n" + _provision_block(a))
            check("an indented (conditional) exit before the block -> the claim is still made",
                  sf._git_hook_installed(a) is True)
            _write_hook(ha, "#!/bin/sh\nexec >\"$HOME/hook.log\" 2>&1\nexec 1>&2\n" + _provision_block(a))
            check("a redirect-only `exec >log 2>&1` / `exec 1>&2` (the script goes on) -> the claim is still made",
                  sf._git_hook_installed(a) is True)
            _write_hook(ha, "#!/bin/sh\necho x\nexit 0 # done\n" + _provision_block(a))
            check("`exit 0 # done` (a trailing comment) still ends the script -> no claim",
                  sf._git_hook_installed(a) is False)
            # a deleted plugin's CLI runs nothing
            gone = (Path(tw) / "old-plugin" / "0.27.2" / "build" / "deepmap_cli.py").as_posix()
            _write_hook(ha, "#!/bin/sh\n# >>> deepmap (managed; existing hook preserved on chain) >>>\n"
                            f"( nohup python '{gone}' sync '{a.as_posix()}' --stale-cleanup >/dev/null 2>&1 & ) "
                            "|| true   # deepmap\n# <<< deepmap <<<\n")
            check("a block naming a deleted plugin's deepmap_cli.py -> no claim", sf._git_hook_installed(a) is False)
            # the round-1 phantom line (marker only, no deepmap_cli.py sync) proves nothing
            _write_hook(ha, "#!/bin/sh\n( nohup python x sync y & ) || true   # deepmap\n")
            check("a marker line that runs no deepmap_cli.py sync of this project -> no claim",
                  sf._git_hook_installed(a) is False)
            if not _WIN:
                _write_hook(ha, "#!/bin/sh\n" + _provision_block(a))
                os.chmod(ha, 0o644)
                check("a NON-executable hook (git skips it) -> no claim", sf._git_hook_installed(a) is False)
            # end to end: the emitted reminder carries the suffix only for the project the hook re-syncs
            _write_hook(ha, "#!/bin/sh\n" + _provision_block(a))
            _write_hook(_hook_file(b), "#!/bin/sh\n" + _provision_block(a))
            outs = {}
            for name, root in (("A", a), ("B", b)):
                saved = (sys.stdin, sys.stdout)
                try:
                    sys.stdin = io.StringIO(json.dumps({"cwd": str(root), "session_id": f"s-{name}",
                                                        "tool_name": "Edit", "tool_input": {}}))
                    sys.stdout = io.StringIO()
                    sf.main()
                    outs[name] = sys.stdout.getvalue()
                finally:
                    sys.stdin, sys.stdout = saved
            check("end to end: A's reminder says a commit re-syncs it", "git hook" in outs.get("A", "").lower(),
                  outs.get("A"))
            check("end to end: B (whose hook syncs A) gets the reminder WITHOUT the claim",
                  "/deep-map:refresh" in outs.get("B", "") and "git hook" not in outs.get("B", "").lower(),
                  outs.get("B"))


# =========================================================================== G: zombie-aware _pid_alive
def gate_g_pid_alive() -> None:
    print("\n-- G (round-7 W2->W1 NEED): port_registry._pid_alive treats a zombie as dead (POSIX) --")
    if not all(hasattr(PR, n) for n in ("_windows", "_PROC", "_proc_state")):
        check("port_registry has the POSIX process-state seam (_windows / _PROC / _proc_state)", False)
        return
    saved = (PR._windows, PR._PROC)
    with _tmpdir() as tp:
        proc = Path(tp)

        def fake(pid, state, threads=None, task_states=None):
            d = proc / str(pid)
            d.mkdir(parents=True, exist_ok=True)
            (d / "stat").write_text(f"{pid} (java) {state} 1 1 1 0 -1\n", encoding="utf-8")
            if threads is not None:
                (d / "status").write_text(f"Name:\tjava\nThreads:\t{threads}\n", encoding="utf-8")
            for i, ts in enumerate(task_states or ()):
                t = d / "task" / str(pid + i)
                t.mkdir(parents=True, exist_ok=True)
                (t / "stat").write_text(f"{pid + i} (java) {ts} 1 1 1\n", encoding="utf-8")
        fake(41001, "S")
        fake(41002, "Z", threads=1)
        fake(41003, "Z", task_states=("Z", "S"))
        fake(41004, "X", task_states=("X",))
        fake(41005, "Z", task_states=("Z",))
        try:
            PR._windows = lambda: False
            PR._PROC = str(proc)
            check("a sleeping process (S) is alive", PR._pid_alive(41001) is True)
            check("a zombie (Z, one thread) is DEAD — the key migration no longer waits on it",
                  PR._pid_alive(41002) is False)
            check("a zombie leader with a live thread ('Zl', a JVM still shutting down) stays ALIVE",
                  PR._pid_alive(41003) is True)
            check("a dead (X) process is dead", PR._pid_alive(41004) is False)
            check("a zombie whose every task is a zombie is dead", PR._pid_alive(41005) is False)
            check("no /proc entry on the Windows host through the seam -> never os.kill, 'unknown' -> True",
                  (PR._pid_alive(41999) is True) if _WIN else True)
        finally:
            PR._windows, PR._PROC = saved
    check("the real probe still sees THIS process alive", PR._pid_alive(os.getpid()) is True)


# =========================================================================== H: J5 hook side
def _notify_inproc(mod, payload: dict, home: Path, *, up=(), env=None) -> str:
    hs = mod._hs
    saved_io, saved_probe = (sys.stdin, sys.stdout), hs.port_listening
    with _Env(home, env):
        try:
            ups = {int(p) for p in up}
            hs.port_listening = lambda port, host="127.0.0.1", timeout=None: int(port) in ups
            sys.stdin = io.StringIO(json.dumps(payload))
            sys.stdout = io.StringIO()
            mod.main()
            return sys.stdout.getvalue()
        finally:
            sys.stdin, sys.stdout = saved_io
            hs.port_listening = saved_probe


def _ctx(out: str) -> str:
    try:
        return json.loads(out.strip().splitlines()[-1])["hookSpecificOutput"]["additionalContext"]
    except Exception:
        return ""


def gate_h_update_restart_note() -> None:
    print("\n-- H (J5, hook side): servers started from another DeepMap version -> the reconnect note --")
    for fn in ("code_identity", "server_code_stale"):
        check(f"port_registry exposes {fn}()", callable(getattr(PR, fn, None)))
    if not callable(getattr(PR, "code_identity", None)):
        return
    ci = PR.code_identity(REAL_REPO / "build")
    want_ver = json.loads((REAL_REPO / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"]
    check("code_identity(): the plugin root (forward slashes) + the plugin.json version",
          ci.get("code_root") == str(REAL_REPO.resolve()).replace("\\", "/") and ci.get("code_version") == want_ver, ci)
    st = PR.server_code_stale
    other_root = "C:/old/plugins/deep-map/0.27.4" if _WIN else "/old/plugins/deep-map/0.27.4"
    check("server_code_stale: a record without the code fields is UNKNOWN (None), never stale",
          st({"pid": 1, "project_key": "k"}, ci) is None)
    check("server_code_stale: the same code root + version -> False",
          st({"code_root": ci["code_root"], "code_version": ci["code_version"]}, ci) is False)
    check("server_code_stale: another plugin root -> True", st({"code_root": other_root}, ci) is True)
    check("server_code_stale: another version -> True", st({"code_version": "0.0.1"}, ci) is True)
    if _WIN:
        check("server_code_stale: the root compares case-insensitively on Windows",
              st({"code_root": ci["code_root"].upper()}, ci) is False)
    hs = _load("deepmap_hook_state")
    notify = _load("deepmap_sessionstart_notify")
    with _tmpdir() as tw, _tmpdir() as th:
        home, root = Path(th), _repo(Path(tw))
        _manifest(root)
        _register(home, root, search=18911, embed=18912)
        (root / ".mcp.json").write_text(json.dumps({"mcpServers": {
            "deepmap-search": {"type": "http", "url": "http://127.0.0.1:18911/mcp"},
            "deepmap-embed": {"type": "http", "url": "http://127.0.0.1:18912/mcp"}}}), encoding="utf-8")
        run = home / "run"
        run.mkdir(parents=True, exist_ok=True)
        key = PR.project_key(root)
        pid = os.getpid()
        # ROUND-10 L1: the note promises the restart heal performs, and heal restarts a recorded server only when the
        # pid IS that server process (its command line runs the server script) — this gate's own pid stands in for
        # it, so its command line is presented as the recorded server's
        real_cmd = PR.pid_cmdline
        srv_cmd = (f'"{sys.executable}" "{other_root}/build/deepmap_search_server.py"' if _WIN
                   else f"{sys.executable}\0{other_root}/build/deepmap_search_server.py")

        class _AsServer:
            def __enter__(self):
                PR.pid_cmdline = lambda p: srv_cmd if int(p) == pid else real_cmd(p)

            def __exit__(self, *exc):
                PR.pid_cmdline = real_cmd

        def owners(**fields):
            for name, port in (("deepmap-search", 18911), ("deepmap-embed", 18912)):
                (run / f"{name}-{port}.pid").write_text(str(pid), encoding="utf-8")
                rec = {"pid": pid, "project_key": key, "root": str(root).replace("\\", "/")}
                rec.update(fields)
                (run / f"{name}-{port}.owner").write_text(json.dumps(rec), encoding="utf-8")

        owners(code_root=other_root, code_version="0.27.4")
        with _Env(home), _AsServer():
            got = hs.mcp_code_stale(root)
        check("mcp_code_stale: this project's live server recorded 0.27.4 code -> {'old': '0.27.4', 'new': <current>}",
              isinstance(got, dict) and got.get("old") == "0.27.4" and got.get("new") == PR.code_identity()
              .get("code_version"), got)
        owners()
        with _Env(home):
            got = hs.mcp_code_stale(root)
        check("mcp_code_stale: an owner record without the code fields (an older build) -> None", got is None, got)
        owners(code_root=PR.code_identity()["code_root"], code_version=PR.code_identity()["code_version"])
        with _Env(home):
            got = hs.mcp_code_stale(root)
        check("mcp_code_stale: servers already on the installed code -> None", got is None, got)
        owners(code_root=other_root, code_version="0.27.4", project_key="someoneelse1")
        with _Env(home):
            got = hs.mcp_code_stale(root)
        check("mcp_code_stale: another project's record -> None (never this project's news)", got is None, got)
        owners(code_root=other_root, code_version="0.27.4", pid=pid + 1)
        with _Env(home):
            got = hs.mcp_code_stale(root)
        check("mcp_code_stale: a record whose pid is not the pid file's -> None", got is None, got)
        # end to end through the SessionStart notify: READY, ports UP (no down-at-start note), old code recorded
        owners(code_root=other_root, code_version="0.27.4")
        with _AsServer():
            out = _ctx(_notify_inproc(notify, {"cwd": str(root), "session_id": "u1"}, home, up=(18911, 18912)))
        check("notify (READY, servers up on 0.27.4 code): says a different version runs, the heal restarts them, "
              "and to reconnect via /mcp", "different DeepMap version (0.27.4)" in out and "/mcp" in out
              and "heal restarts them" in out, out)
        with _AsServer():
            out = _ctx(_notify_inproc(notify, {"cwd": str(root), "session_id": "u2"}, home, up=(18911, 18912),
                                      env={"DEEPMAP_HEAL": "0"}))
        check("notify with the heal disabled: /deep-map:setup restarts them (no 'heal restarts' promise)",
              "different DeepMap version (0.27.4)" in out and "/deep-map:setup" in out
              and "heal restarts them" not in out, out)
        owners()
        out = _ctx(_notify_inproc(notify, {"cwd": str(root), "session_id": "u3"}, home, up=(18911, 18912)))
        check("control: an older owner record (no code fields) -> the plain READY announce, no version note",
              "DeepMap is ACTIVE" in out and "different DeepMap version" not in out, out)


# =========================================================================== F: docs
def gate_f_docs() -> None:
    print("\n-- F: W1 doc drift (setup.md bolt URI, plugin-update.md restart guidance) --")
    su = (COMMANDS / "setup.md").read_text(encoding="utf-8")
    check("setup.md names the first project's bolt URI as bolt://127.0.0.1:7687", "bolt://127.0.0.1:7687" in su)
    stale = [f.name for f in sorted(COMMANDS.glob("*.md")) if "bolt://localhost" in f.read_text(encoding="utf-8")]
    check("no commands/*.md says bolt://localhost", not stale, stale)
    check("SKILL.md does not say bolt://localhost", "bolt://localhost" not in SKILL.read_text(encoding="utf-8"))
    pu = " ".join((COMMANDS / "plugin-update.md").read_text(encoding="utf-8").split())
    sents = [s for s in re.split(r"(?<=[.!?])\s+", pu) if "/deep-map:setup" in s]
    check("plugin-update.md: after an update, each project's MCP servers keep running the previous version's code "
          "until restarted, and /deep-map:setup restarts them",
          "previous version's code" in pu.lower() and any("restart" in s.lower() for s in sents), sents)
    check("plugin-update.md: the client must reconnect (/mcp) once a server is replaced",
          "/mcp" in pu and "reconnect" in pu.lower())


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 72)
    print("DeepMap — round-8 W1: bolt_uri (J4), core.longpaths (J6), sync-flag hook proof (J1), docs")
    print(f"(hooks under test: {HOOKS}; build: {BUILD})")
    print("=" * 72)
    if not shutil.which("git"):
        print("RESULT: GREEN — 0/0 gates passed. (git not on PATH: skipped)")
        return 0
    for g in (gate_a_helper, gate_b_generated_uris, gate_c_hook_probes, gate_d_longpaths, gate_e_syncflag_claim,
              gate_g_pid_alive, gate_h_update_restart_note, gate_f_docs):
        try:
            g()
        except Exception as exc:  # noqa: BLE001 — a crashing section is a RED check, never a crash of the suite
            import traceback
            check(f"{g.__name__} ran without raising", False, "".join(traceback.format_exception(exc))[-900:])
    print("\n" + "=" * 72)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
