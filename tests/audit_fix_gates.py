#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j regression gates for the 2026-06-28 audit-finding fixes.

Locks the fixes so they cannot silently regress:
  * OS path-separator normalization in BOTH extractors (build/sync.py ast path +
    build/graphify_extract.py) — node keys + source_file must be '/'-normalized so a
    Windows-built graph keys identically to a POSIX one.
  * configure_neo4j's file-I/O guard (build/provision.py) — a missing/unwritable conf
    must return False with a diagnostic, never a raw traceback.

Pure: extraction is Neo4j-free; the conf guard is exercised against a temp file. The setup() gate
runs in a throwaway DEEPMAP_HOME + temp project (_SetupSandbox): setup() clears the target's setup
blocker and records the base store's owner, and against the real home with the target "x" it deleted
<home>/run/setup-blocked-<key of <repo>/x>.json and once left the machine's base-store owner record
naming <repo>/x (r283 F5; proven hermetic by tests/f5_test_hygiene_r283_gates.py).
"""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

BUILD = Path(__file__).resolve().parent.parent / "build"
sys.path.insert(0, str(BUILD))

_PASS = 0
_FAIL = 0
_MISSING = object()


def check(label: str, cond: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {label}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}{(' — ' + detail) if detail else ''}")


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"dm_{name}", BUILD / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class _SetupSandbox:
    """A throwaway machine for a gate that drives provision.setup() (r283 F5). Even with its ports, Neo4j, sync
    and MCP start stubbed, setup() clears the target's setup blocker (<DEEPMAP_HOME>/run/setup-blocked-<key>.json),
    runs git on the target and, for a local base URI, records the base store's owner (_record_base_owner: a port
    probe, a JVM listing, <home>/neo4j/neo4j-community-*/data/deepmap_base_owner.json). Here: a temp DEEPMAP_HOME,
    a temp project (`target`, one source file), the per-project env and git's repo-discovery env cleared (git never
    searches above the temp dir), the escape hatch pinned off, and the Bolt / HTTP / port / process probes answering
    "down / free / gone" — no socket, no process listing, no real Neo4j driver. Restores everything on exit."""

    _ENV = ("DEEPMAP_HOME", "DEEPMAP_SEARCH_PORT", "DEEPMAP_EMBED_PORT", "DEEPMAP_PROJECT_ROOT",
            "DEEPMAP_PROJECT_KEY", "DEEPMAP_NO_BASE_ADOPT", "NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD",
            "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE", "GIT_CEILING_DIRECTORIES")

    def __init__(self, P):
        self.P = P

    def __enter__(self):
        import port_registry as PR   # the module setup() imports (BUILD is on sys.path)

        def no_driver(*a, **k):
            raise RuntimeError("sandbox: no real Neo4j driver")

        self._td = tempfile.TemporaryDirectory(prefix="dm_setup_sandbox_")
        base = Path(self._td.name)
        self.home, self.target = base / "deepmap_home", base / "proj"
        self.target.mkdir()
        (self.target / "app.py").write_text("def main():\n    return 1\n", encoding="utf-8")
        self._env = {k: os.environ.get(k) for k in self._ENV}
        for k in self._ENV:
            os.environ.pop(k, None)
        os.environ["DEEPMAP_HOME"] = str(self.home)
        os.environ["GIT_CEILING_DIRECTORIES"] = str(base)
        stubs = ((self.P, {"_USER_SET_NEO4J_URI": False, "_bolt_state": lambda *a, **k: "down",
                           "is_bolt_up": lambda *a, **k: False, "is_http_up": lambda *a, **k: False,
                           "_port_in_use": lambda *a, **k: False, "_make_driver": no_driver,
                           "_list_processes": lambda: [], "_pid_alive": lambda pid: False,
                           "_pid_cmdline": lambda pid: None, "_pid_image": lambda pid: None,
                           "_pid_create_time": lambda pid: None}),
                 (PR, {"_port_in_use": lambda *a, **k: False}))
        self._saved = [(obj, k, getattr(obj, k, _MISSING)) for obj, attrs in stubs for k in attrs]
        for obj, attrs in stubs:
            for k, v in attrs.items():
                setattr(obj, k, v)
        return self

    def __exit__(self, *exc):
        for obj, k, v in reversed(self._saved):
            if v is _MISSING:
                try:
                    delattr(obj, k)
                except AttributeError:
                    pass
            else:
                setattr(obj, k, v)
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._td.cleanup()
        return False


def gate_graphify_separator() -> None:
    print("\n-- graphify_extract.map_graph: source_file '/'-normalized --")
    G = _load("graphify_extract")
    graph = {
        "nodes": [
            {"id": "m", "label": "mod.py", "file_type": "code",
             "source_file": "pkg\\sub\\mod.py", "source_location": "L1", "_origin": "ast"},
            {"id": "m_f", "label": "f()", "file_type": "code",
             "source_file": "pkg\\sub\\mod.py", "source_location": "L3", "_origin": "ast"},
        ],
        "links": [],
    }
    nodes, _calls, _imports = G.map_graph(graph)
    sfs = [n["source_file"] for n in nodes]
    check("no backslash in any source_file", all("\\" not in s for s in sfs), str(sfs))
    check("backslash path folded to forward slash",
          any(s == "pkg/sub/mod.py" for s in sfs), str(sfs))


def gate_sync_separator() -> None:
    # NOTE: this gate is *discriminating only on Windows* — the sync fix (Path.as_posix) only has
    # an effect where `relative_to` yields OS backslashes, i.e. Windows. On POSIX `relative_to`
    # already returns forward slashes, so there is nothing to convert and the test passes with or
    # without the fix. The CROSS-OS discriminating coverage of the same normalization principle is
    # gate_graphify_separator below (it injects a literal backslash into a string source_file, which
    # is OS-independent). Together they lock the normalization on every platform.
    print("\n-- sync._rebuild_code_ast: keys + source_file '/'-normalized (Windows-discriminating) --")
    S = _load("sync")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        pkg = root / "pkg" / "sub"
        pkg.mkdir(parents=True)
        (pkg / "mod.py").write_text(
            "def alpha():\n    return beta()\n\n\ndef beta():\n    return 1\n",
            encoding="utf-8")
        nodes, _calls, _imports = S._rebuild_code_ast(root)
        keys = [n.get("key", "") for n in nodes]
        sfs = [n.get("source_file", "") for n in nodes]
        check("extracted at least the module + 2 functions", len(nodes) >= 3, f"{len(nodes)} nodes")
        check("no backslash in any node key", all("\\" not in k for k in keys), str(keys[:4]))
        check("no backslash in any source_file", all("\\" not in s for s in sfs), str(sfs[:4]))
        check("keys use the forward-slash relpath (pkg/sub/mod.py)",
              any(k.startswith("pkg/sub/mod.py::") for k in keys), str(keys[:4]))


def gate_configure_neo4j_guard() -> None:
    print("\n-- configure_neo4j: I/O guard (no raw traceback) --")
    P = _load("provision")
    orig_paths = P.paths
    try:
        # 1) missing conf -> returns False, no raise (the guard). The path is inside this gate's own temp dir
        #    (never a fixed name in the shared temp dir, which anyone could have created).
        with tempfile.TemporaryDirectory() as td0:
            P.paths = lambda v, _m=Path(td0) / "dm_nonexistent_xyz" / "neo4j.conf": {"conf": _m}
            raised = False
            result = None
            try:
                result = P.configure_neo4j("5.26")
            except Exception as exc:  # noqa: BLE001 — a raise here is the bug we fixed
                raised = True
                result = repr(exc)
        check("missing conf -> no exception", not raised, str(result))
        check("missing conf -> returns False", result is False, repr(result))

        # 2) happy path: a real temp conf gets the block once; second call is idempotent.
        with tempfile.TemporaryDirectory() as td:
            conf = Path(td) / "neo4j.conf"
            conf.write_text("# base conf\n", encoding="utf-8")
            P.paths = lambda v, _c=conf: {"conf": _c}
            first = P.configure_neo4j("5.26")
            check("fresh conf -> block written (True)", first is True)
            check("marker present after write", P.CONF_MARKER in conf.read_text(encoding="utf-8"))
            second = P.configure_neo4j("5.26")
            check("idempotent: second call returns False", second is False)
    finally:
        P.paths = orig_paths


def gate_extract_path_traversal() -> None:
    """zip-slip / tar-slip guard: extract_neo4j must REFUSE a member that escapes the target dir
    (CWE-22). Mutation-resistant — without the _within guard, extractall would write outside."""
    print("\n-- extract_neo4j: rejects path-traversal archive members (zip-slip) --")
    import zipfile
    P = _load("provision")
    orig_paths = P.paths
    try:
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            badzip = tdp / "neo4j-evil.zip"
            with zipfile.ZipFile(badzip, "w") as z:
                z.writestr("neo4j-x/bin/neo4j", "ok")        # benign member
                z.writestr("../../../escape.txt", "PWNED")    # traversal member
            home = tdp / "home"
            home.mkdir()
            P.paths = lambda v, _h=home: {"neo4j_bin": _h / "nope",
                                          "neo4j_home": _h / "neo4j", "home": _h}
            raised = False
            try:
                P.extract_neo4j(badzip, "5.26")
            except RuntimeError:
                raised = True
            check("traversal member is REJECTED (RuntimeError)", raised, "extractall ran unguarded")
            escaped = (tdp / "escape.txt").exists() or (tdp.parent / "escape.txt").exists()
            check("nothing was written outside the target dir", not escaped, "a file escaped!")
    finally:
        P.paths = orig_paths


def gate_extract_tar_traversal() -> None:
    """The UNIX download is a `.tar.gz`, so the tar branch (the production Linux/macOS path) needs the
    same zip-slip coverage — a traversal member AND an escaping symlink must both be REFUSED before
    extractall(); only the zip branch was tested before (bug-hunt #3)."""
    print("\n-- extract_neo4j: rejects tar path-traversal + escaping symlink (unix path) --")
    import io
    import tarfile
    P = _load("provision")
    orig_paths = P.paths
    try:
        # (a) a '../' traversal member
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            badtar = tdp / "neo4j-evil.tar.gz"
            with tarfile.open(badtar, "w:gz") as t:
                ok = tarfile.TarInfo("neo4j-x/bin/neo4j"); ok.size = 2
                t.addfile(ok, io.BytesIO(b"ok"))
                ev = tarfile.TarInfo("../../../escape.txt"); ev.size = 5
                t.addfile(ev, io.BytesIO(b"PWNED"))
            home = tdp / "home"; home.mkdir()
            P.paths = lambda v, _h=home: {"neo4j_bin": _h / "nope", "neo4j_home": _h / "neo4j", "home": _h}
            raised = False
            try:
                P.extract_neo4j(badtar, "5.26")
            except RuntimeError:
                raised = True
            check("tar traversal member is REJECTED (RuntimeError)", raised, "tar extractall ran unguarded")
            check("nothing escaped the target (tar traversal)", not (tdp / "escape.txt").exists(), "escaped!")
        # (b) a symlink whose target escapes the extraction dir
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            badtar = tdp / "neo4j-link.tar.gz"
            with tarfile.open(badtar, "w:gz") as t:
                ln = tarfile.TarInfo("neo4j-x/evil-link")
                ln.type = tarfile.SYMTYPE
                ln.linkname = "../../../../etc/passwd"
                t.addfile(ln)
            home = tdp / "home2"; home.mkdir()
            P.paths = lambda v, _h=home: {"neo4j_bin": _h / "nope", "neo4j_home": _h / "neo4j", "home": _h}
            raised = False
            try:
                P.extract_neo4j(badtar, "5.26")
            except RuntimeError:
                raised = True
            check("escaping tar SYMLINK is REJECTED (RuntimeError)", raised, "symlink escape not caught")
    finally:
        P.paths = orig_paths


def gate_neo4j_version_validation() -> None:
    """neo4j_version() must REFUSE a malformed NEO4J_VERSION (it flows into the download URL + the
    cache path — an injection/traversal vector) and fall back to the validated default."""
    print("\n-- neo4j_version: refuses an unvalidated NEO4J_VERSION (URL/path-injection guard) --")
    import os as _os
    P = _load("provision")
    saved = _os.environ.get("NEO4J_VERSION")
    try:
        for bad in ("../../etc", "5.26.0; rm -rf /", "latest", "5.26.0/../x", ""):
            _os.environ["NEO4J_VERSION"] = bad
            check(f"bad {bad!r} -> falls back to default", P.neo4j_version() == P.DEFAULT_VERSION,
                  P.neo4j_version())
        _os.environ["NEO4J_VERSION"] = "5.26.0"
        check("valid X.Y.Z -> passed through", P.neo4j_version() == "5.26.0")
        _os.environ["NEO4J_VERSION"] = "4.4.10"
        check("another valid X.Y.Z -> passed through", P.neo4j_version() == "4.4.10")
    finally:
        if saved is None:
            _os.environ.pop("NEO4J_VERSION", None)
        else:
            _os.environ["NEO4J_VERSION"] = saved


def gate_terminate_escalation() -> None:
    """POSIX _terminate_pid must escalate SIGTERM -> SIGKILL when the process outlives SIGTERM, and
    return True only once it is actually gone (so stop() never deletes the pid file on a lingering
    JVM -> an orphan). Mocked is_windows()->False + os.kill + _pid_alive; no real process."""
    print("\n-- _terminate_pid: POSIX SIGTERM->SIGKILL escalation (orphan guard) --")
    import os as _os
    import types
    P = _load("provision")
    saved = (P.is_windows, P._pid_alive, P.time, _os.kill)
    try:
        P.is_windows = lambda: False
        P.time = types.SimpleNamespace(sleep=lambda s: None)
        # (a) a process that ignores SIGTERM and only dies on SIGKILL(9)
        sent, state = [], {"alive": True}
        def kill_a(pid, sig):
            sent.append(sig)
            if sig == 9:
                state["alive"] = False
        _os.kill = kill_a
        P._pid_alive = lambda pid: state["alive"]
        ok = P._terminate_pid(4242, tree=False)
        check("SIGTERM(15) sent first, then escalates to SIGKILL(9)", sent[0] == 15 and 9 in sent, str(sent))
        check("returns True only once the process is gone", ok is True)
        # (b) a process that dies on SIGTERM -> no SIGKILL escalation
        sent2, state2 = [], {"alive": True}
        def kill_b(pid, sig):
            sent2.append(sig)
            if sig == 15:
                state2["alive"] = False
        _os.kill = kill_b
        P._pid_alive = lambda pid: state2["alive"]
        ok2 = P._terminate_pid(4242, tree=False)
        check("graceful SIGTERM stop -> NO SIGKILL escalation", sent2 == [15] and ok2 is True, str(sent2))
    finally:
        P.is_windows, P._pid_alive, P.time, _os.kill = saved


def gate_setup_verify_guard() -> None:
    """setup(): a fail-loud RAISE inside verify_search must NOT abort an otherwise-good setup after
    Neo4j + sync already succeeded — it is caught, and the MCP servers still start. Runs in _SetupSandbox
    (a temp DEEPMAP_HOME + temp project): never the real ~/.deepmap, never a target inside this repo."""
    print("\n-- setup: a verify_search RAISE does not abort an otherwise-good setup --")
    import types
    P = _load("provision")
    keys = ("ensure_neo4j", "_load", "verify_search", "start_mcp_servers", "verify_mcp",
            "_print_mcp_hint", "apply_ports_for", "write_mcp_json")
    saved = {k: getattr(P, k) for k in keys}
    calls = {"mcp": 0}
    try:
        P.ensure_neo4j = lambda **kw: True
        P._load = lambda name: types.SimpleNamespace(sync=lambda *a, **k: True)
        def boom(*a, **k):
            raise RuntimeError("simulated fail-loud DB error during verify")
        P.verify_search = boom
        P.start_mcp_servers = lambda **kw: (calls.__setitem__("mcp", calls["mcp"] + 1) or True)
        P.verify_mcp = lambda name: True
        P._print_mcp_hint = lambda *a, **k: None
        # Neutralise the per-project port side effects (tested hermetically in port_registry_gates) so
        # setup() never allocates in a registry or writes a stray .mcp.json.
        P.apply_ports_for = lambda *a, **k: None
        P.write_mcp_json = lambda *a, **k: None
        with _SetupSandbox(P) as sb:
            ok = P.setup(str(sb.target), verify=True)
        check("setup() did NOT abort on a verify_search raise (returns True)", ok is True)
        check("setup() continued past the verify error to start the MCP servers", calls["mcp"] == 1)
    finally:
        for k, v in saved.items():
            setattr(P, k, v)


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 64)
    print("DeepMap — audit-fix regression gates (no Neo4j)")
    print("=" * 64)

    gate_graphify_separator()
    gate_sync_separator()
    gate_configure_neo4j_guard()
    gate_extract_path_traversal()
    gate_extract_tar_traversal()
    gate_neo4j_version_validation()
    gate_terminate_escalation()
    gate_setup_verify_guard()

    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
