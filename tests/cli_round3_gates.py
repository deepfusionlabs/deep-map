#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j, no-socket, no-spend gates for the CLI's round-3 fixes (2026-09 live validation, owner W3).

Locks:
  RL  Neo4j's temporary login LOCK (Neo.ClientError.Security.AuthenticationRateLimit — the 6.x driver
      raises a plain ClientError for it) is an AUTH problem everywhere in the CLI: raised directly, raised
      from inside a ServiceUnavailable, or carrying only the "too many times in a row" message. It is
      never worded "not reachable", and its remedy leads with the fix that works while the lock holds
      (stop whatever keeps logging in with wrong credentials, retry in a few seconds) — a destructive
      `instance-rm` is offered only for a retry that is REJECTED rather than locked. doctor says the same.
      A plain rejection keeps the plain auth wording; a bare outage stays "not reachable".
  IM  `impact`'s over-budget message (a transaction-memory / timeout error reaching main()) names a remedy
      that works in that state (D6): a qualified Class.method, DEEPMAP_NEO4J_HEAP_MAX, or
      DEEPMAP_IMPACT_TIMEOUT_S — never `--max-hops 1` (the retriever already retries at 1 hop itself).
  EX  doctor shows HOW the last sync built the graph, next to the extractor line (from the manifest): the
      graphify version, self-call re-binding (on / OFF, and how many calls it re-bound), the engine-skipped
      dirs it re-included, the ones it could NOT re-include (unsupported graphify: WARN + the runnable pip
      remedy, or just /deep-map:refresh when a supported graphify is installed NOW), resolved-vs-by-name
      import binding, and files graphify failed on; an unsupported graphify installed NOW WARNs for the next
      sync. An older manifest without these keys prints exactly as before; an ast fallback never shows
      graphify's facts.
  ML  doctor's ML-stack WARN names a RUNNABLE fix (`"<python>" -m pip install ... sentence-transformers`
      for DeepMap's own interpreter, quoted with forward slashes so it survives bash), not a path to an
      installer script (D6); every "install graphify" hint is likewise the PINNED runnable command
      (`"<python>" -m pip install --upgrade "graphifyy>=0.8.39,<0.8.52"` — round 4: the F3 validated range,
      `--upgrade`), never an unpinned install.
  PR  retrieval_log.project_root follows the ONE root rule every other surface uses — sync._sync_root, the
      hooks' project_root, provision's server root: inside git, the OUTERMOST synced ancestor up to the
      toplevel; with NO manifest anywhere, the start directory itself (was: the git toplevel — the one
      case where `learning --enable` / enrich / tune disagreed with where the next sync writes).

Hermetic: temp DEEPMAP_HOME, fake Neo4j drivers (neo4j.GraphDatabase.driver patched), temp project dirs;
never touches ~/.deepmap or the committed .mcp.json. No sockets. Helpers are shared with
tests/cli_route_impact_gates.py (the round-2 CLI gate).
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import time
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build"
HOOKS = REPO / "hooks"

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


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


H = _load_module(REPO / "tests" / "cli_route_impact_gates.py", f"dm_cli_r2_helpers_{time.time_ns()}")


def _rate_limit_error():
    import neo4j.exceptions as E
    return E.Neo4jError._hydrate_neo4j(
        code="Neo.ClientError.Security.AuthenticationRateLimit",
        message="The client has provided incorrect authentication details too many times in a row.")


def _unauthorized_error():
    import neo4j.exceptions as E
    return E.Neo4jError._hydrate_neo4j(code="Neo.ClientError.Security.Unauthorized",
                                       message="The client is unauthorized due to authentication failure.")


def _wrapped(inner):
    """`inner` re-raised as the cause of a ServiceUnavailable (how a routing/pool layer can surface it)."""
    import neo4j.exceptions as E
    try:
        try:
            raise inner
        except Exception as exc:
            raise E.ServiceUnavailable("Unable to retrieve routing information") from exc
    except Exception as outer:  # noqa: BLE001
        return outer


def _message_only_lock():
    """The lock with no code (an older / differently-hydrating driver): only its message says so."""
    import neo4j.exceptions as E
    return E.ClientError("The client has provided incorrect authentication details too many times in a row.")


# ---- RL: the login lock is an auth problem ---------------------------------------------------------------
def gate_rate_limit(C, PR, work: Path) -> None:
    print("\n-- RL: Neo4j's AuthenticationRateLimit login lock is classified + worded as auth --")
    import neo4j.exceptions as E
    rl, wrapped, bare = _rate_limit_error(), _wrapped(_rate_limit_error()), _message_only_lock()
    check("direct AuthenticationRateLimit ClientError -> 'auth'", C._classify_db_error(rl) == "auth",
          C._classify_db_error(rl))
    check("AuthenticationRateLimit raised from inside a ServiceUnavailable -> 'auth' (not 'unreachable')",
          C._classify_db_error(wrapped) == "auth", C._classify_db_error(wrapped))
    check("a code-less ClientError saying 'too many times in a row' -> 'auth' (not 'query')",
          C._classify_db_error(bare) == "auth", C._classify_db_error(bare))
    down = E.ServiceUnavailable("Couldn't connect to localhost:7805")
    check("a bare ServiceUnavailable (no auth cause) stays 'unreachable'",
          C._classify_db_error(down) == "unreachable", C._classify_db_error(down))

    p = work / "rlP"
    p.mkdir()
    H._write_registry(PR, {p: {"bolt_port": 7689, "http_port": 7476}})   # a per-project instance

    for label, exc, argv in (("direct", rl, ["status"]), ("wrapped", wrapped, ["search", "x"]),
                             ("message-only", bare, ["impact", "foo"])):
        rc, raised, out, err = H._main(C, argv, overrides=H._search_stubs({}),
                                       driver=H._GraphDriver(session_exc=exc), cwd=p)
        low = err.lower()
        check(f"{label} lock ({argv[0]}): exit 3, no traceback", raised is None and rc == 3, f"{rc} {raised!r}")
        check(f"{label} lock ({argv[0]}): names the login LOCK (too many failed logins), not an outage",
              "too many" in low and "lock" in low and "not reachable" not in low and "unreachable" not in low,
              err[-500:])
        check(f"{label} lock ({argv[0]}): says to retry in a few seconds", "retry" in low and "seconds" in low,
              err[-500:])
        check(f"{label} lock ({argv[0]}): points at what keeps logging in (MCP servers -> /deep-map:setup)",
              "mcp" in low and "/deep-map:setup" in err, err[-500:])
        cut = err.find("REJECTED")
        check(f"{label} lock ({argv[0]}): `instance-rm` only as the fallback for a REJECTED (not locked) retry",
              "instance-rm" not in err or (cut != -1 and err.find("instance-rm") > cut), err[-500:])

    rc, raised, out, err = H._main(C, ["status"], driver=H._GraphDriver(session_exc=_unauthorized_error()), cwd=p)
    check("a plain rejection (Unauthorized) keeps the plain auth wording (no lock text)",
          rc == 3 and "authentication failed" in err and "too many" not in err.lower(), err[-300:])

    rc, raised, out, err = H._main(C, ["status"], driver=H._GraphDriver(session_exc=down), cwd=p)
    check("a bare outage is still 'not reachable'", rc == 3 and "not reachable" in err, err[-300:])

    # doctor: the FAIL line says the same
    fac = H._Factory(lambda: H._GraphDriver(session_exc=_rate_limit_error()))
    with H._patched_graphdb(fac):
        rc, out = H._run_doctor(C, cwd=p)
    neo = H._line_with(out, "Neo4j")
    low = neo.lower()
    check("doctor + lock: FAIL line names the login lock, not 'unreachable'",
          "[FAIL]" in neo and "too many" in low and "lock" in low and "unreachable" not in low, neo)
    check("doctor + lock: verdict FAILED, exit 1", H._verdict_of(out) == "FAILED" and rc == 1,
          f"{H._verdict_of(out)} {rc}")
    fac = H._Factory(lambda: H._GraphDriver(session_exc=_wrapped(_rate_limit_error())))
    with H._patched_graphdb(fac):
        rc, out = H._run_doctor(C, cwd=p)
    neo = H._line_with(out, "Neo4j")
    check("doctor + a lock wrapped in ServiceUnavailable: still the lock, not 'unreachable'",
          "too many" in neo.lower() and "unreachable" not in neo.lower(), neo)
    H._clear_registry(PR)


# ---- IM: impact's over-budget remedy is runnable (D6) ---------------------------------------------------
def gate_impact_cost(C, work: Path) -> None:
    print("\n-- IM: impact's over-budget message names a remedy that works (never --max-hops 1) --")
    import neo4j.exceptions as E
    p = work / "imP"
    p.mkdir()
    timeout = E.Neo4jError._hydrate_neo4j(
        code="Neo.ClientError.Transaction.TransactionTimedOutClientConfiguration",
        message="The transaction has been terminated. Retry your operation in a new transaction, and you "
                "should see a successful result. The transaction has not completed within the timeout "
                "specified at its start by the client.")
    with H._env(NEO4J_URI="bolt://localhost:1"):
        rc, raised, out, err = H._main(C, ["impact", "hub"], driver=H._GraphDriver(session_exc=H._mem_error()),
                                       cwd=p)
        check("impact + transaction-memory error: exit 3, no traceback", raised is None and rc == 3,
              f"{rc} {raised!r}")
        check("... does NOT advise --max-hops 1 (the retriever already fell back to 1 hop)",
              "--max-hops 1" not in err, err[-400:])
        check("... names a qualified Class.method AND DEEPMAP_NEO4J_HEAP_MAX",
              "Class.method" in err and "DEEPMAP_NEO4J_HEAP_MAX" in err, err[-400:])
        rc, raised, out, err = H._main(C, ["impact", "hub"], driver=H._GraphDriver(session_exc=timeout), cwd=p)
        check("impact + transaction timeout: names DEEPMAP_IMPACT_TIMEOUT_S, not --max-hops 1",
              rc == 3 and "DEEPMAP_IMPACT_TIMEOUT_S" in err and "--max-hops 1" not in err, err[-400:])


# ---- EX: doctor shows how the last sync built the graph ------------------------------------------------
_SPEC = "graphifyy>=0.8.39,<0.8.52"   # graphify_extract.SUPPORTED_GRAPHIFY_SPEC (ROUND-4 F3)
_INSTALLED = None   # (supported, version) the doctor sees as installed NOW; set by _installed_graphify_as


@contextlib.contextmanager
def _installed_graphify_as(C, supported, version, spec=_SPEC):
    """Make the doctor see graphify `version` (supported or not) as installed NOW — deterministic whatever
    this box has installed (CI may carry another in-range release, or none)."""
    global _INSTALLED
    import importlib.util as IU
    stub = types.SimpleNamespace(graphify_support=lambda: (supported, version), SUPPORTED_GRAPHIFY_SPEC=spec)
    saved_eng, saved_find, saved_inst = C._graphify_engine, IU.find_spec, _INSTALLED
    real_find = IU.find_spec
    C._graphify_engine = lambda: stub
    IU.find_spec = lambda name, *a, **k: (object() if name == "graphify" else real_find(name, *a, **k))
    _INSTALLED = (supported, version)
    try:
        yield
    finally:
        C._graphify_engine, IU.find_spec, _INSTALLED = saved_eng, saved_find, saved_inst


@contextlib.contextmanager
def _graphify_missing(C):
    """The doctor sees NO importable graphify (find_spec('graphify') is None)."""
    import importlib.util as IU
    real_find = IU.find_spec
    IU.find_spec = lambda name, *a, **k: (None if name == "graphify" else real_find(name, *a, **k))
    try:
        with _installed_graphify_as(C, None, None):
            IU.find_spec = lambda name, *a, **k: (None if name == "graphify" else real_find(name, *a, **k))
            yield
    finally:
        IU.find_spec = real_find


def _doctor_for(C, PR, proj: Path, manifest: dict, *, ml_ok=True):
    """doctor output for `proj` with `manifest` as its .deepmap/graph_build.json (a healthy fake graph);
    graphify 0.8.39 (supported) counts as installed unless a caller set another via _installed_graphify_as."""
    if _INSTALLED is None:
        with _installed_graphify_as(C, True, "0.8.39"):
            return _doctor_for(C, PR, proj, manifest, ml_ok=ml_ok)
    H._write_manifest(proj, **manifest)
    fac = H._Factory(lambda: H._GraphDriver(owner=PR.project_identity(proj), nodes=7))
    with H._env(NEO4J_URI="bolt://localhost:7999"), H._patched_graphdb(fac):
        if ml_ok:
            return H._run_doctor(C, cwd=proj, provision=H._fake_provision({8787, 8788}, []))
        return _run_doctor_no_ml(C, cwd=proj, provision=H._fake_provision({8787, 8788}, []))


def _run_doctor_no_ml(C, *, cwd: Path, provision):
    """H._run_doctor, but with torch / sentence_transformers NOT importable."""
    orig_load = C._load
    C._load = lambda name: provision if name == "provision" else orig_load(name)
    saved = {m: sys.modules.get(m, "absent") for m in ("torch", "sentence_transformers")}
    for m in saved:
        sys.modules[m] = None                    # `import torch` -> ImportError
    out = io.StringIO()
    try:
        with H._chdir(cwd), contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            rc = C.cmd_doctor(argparse.Namespace())
    finally:
        C._load = orig_load
        for m, v in saved.items():
            if v == "absent":
                sys.modules.pop(m, None)
            else:
                sys.modules[m] = v
    return rc, out.getvalue()


def _extractor_block(out: str) -> list:
    """The extractor line + its indented continuation lines."""
    lines = out.splitlines()
    for i, ln in enumerate(lines):
        if "] extractor" in ln:
            block = [ln]
            for nxt in lines[i + 1:]:
                if nxt.startswith("  [") or not nxt.strip() or nxt.startswith("["):
                    break
                block.append(nxt)
            return block
    return []


def gate_extractor_facts(C, PR, work: Path) -> None:
    print("\n-- EX: doctor shows how the last sync built the graph (from the manifest) --")
    spec = _SPEC
    try:
        import graphify_extract as GX
        check("the gate's spec matches graphify_extract.SUPPORTED_GRAPHIFY_SPEC (the installers' pin)",
              GX.SUPPORTED_GRAPHIFY_SPEC == spec, GX.SUPPORTED_GRAPHIFY_SPEC)
    except Exception as exc:  # noqa: BLE001
        check("graphify_extract importable (SUPPORTED_GRAPHIFY_SPEC)", False, repr(exc))
    base = {"extractor": "graphify", "extractor_requested": "auto", "verdict": "READY", "reasons": []}

    proj = work / "exFull"
    proj.mkdir()
    rc, out = _doctor_for(C, PR, proj, dict(base, graphify_version="0.8.39", graphify_self_call_rebind=True,
                                            graphify_self_calls_rebound=12, graphify_rescued_dirs=["build"],
                                            graphify_rescue_dropped=3, graphify_resolved_imports=True,
                                            graphify_imports_resolved=902, graphify_imports_by_name=4,
                                            graphify_failed_files=2))
    block = _extractor_block(out)
    text = "\n".join(block)
    check("the extractor line names the graphify version the last sync ran", bool(block)
          and "graphify 0.8.39" in block[0], block[0] if block else out[-400:])
    check("self-call re-binding: on, with how many calls it re-bound",
          "self-call re-binding: on" in text and "12" in text, text)
    check("re-included engine-skipped dirs are listed (build/) with the dropped count",
          "re-included" in text and "build/" in text and "3" in text, text)
    check("import binding: resolved vs by-name counts", "902" in text and "resolved" in text
          and "4 by name" in text, text)
    check("files graphify could not extract are counted", "2 file(s)" in text, text)
    check("... and a healthy build keeps the extractor line OK + the verdict READY",
          block and block[0].strip().startswith("[OK") and H._verdict_of(out) == "READY",
          (block[0] if block else "") + " | " + H._verdict_of(out))

    proj = work / "exOff"
    proj.mkdir()
    rc, out = _doctor_for(C, PR, proj, dict(base, graphify_version="0.8.39", graphify_self_call_rebind=False,
                                            graphify_resolved_imports=False, graphify_imports_resolved=0,
                                            graphify_imports_by_name=40))
    text = "\n".join(_extractor_block(out))
    check("self-call re-binding switched off: says OFF + names DEEPMAP_SELF_CALL_REBIND",
          "self-call re-binding: OFF" in text and "DEEPMAP_SELF_CALL_REBIND" in text, text)
    check("resolved-import binding off: all 40 imports bound by name", "40" in text and "by name" in text, text)

    low = ("LOW COVERAGE in build/ — 4 git-tracked source file(s) but only 0 extracted node(s); the extraction "
           "engine skips directories named 'build', and DeepMap did not re-include its tracked source because "
           "graphify 0.9.1 is outside the range DeepMap supports for this")
    blocked = dict(base, verdict="DEGRADED", reasons=[low], graphify_version="0.9.1",
                   graphify_rescue_blocked=["build", "out"], graphify_self_call_rebind=True)

    # (a) the unsupported graphify is STILL installed: the fix is the pinned reinstall
    proj = work / "exBlocked"
    proj.mkdir()
    with _installed_graphify_as(C, False, "0.9.1", spec):
        rc, out = _doctor_for(C, PR, proj, blocked)
    block = _extractor_block(out)
    text = "\n".join(block)
    check("dirs NOT re-included (unsupported graphify): the extractor line WARNs",
          bool(block) and block[0].strip().startswith("[WARN"), block[0] if block else out[-300:])
    check("... names the dirs and the graphify version outside the supported range",
          "build/" in text and "out/" in text and "0.9.1" in text and spec in text, text)
    check("... with the runnable pip remedy (a QUOTED forward-slash interpreter -m pip install) + /deep-map:refresh",
          f'"{Path(sys.executable).as_posix()}" -m pip install --upgrade "{spec}"' in text and "/deep-map:refresh" in text,
          text)
    check("... and the sync's LOW COVERAGE reason keeps the verdict DEGRADED",
          H._verdict_of(out).startswith("DEGRADED") and "LOW COVERAGE in build/" in out, H._verdict_of(out))

    # (b) a supported graphify is installed NOW: the fix is just a refresh (no redundant pip command)
    proj = work / "exBlockedFixed"
    proj.mkdir()
    with _installed_graphify_as(C, True, "0.8.39", spec):
        rc, out = _doctor_for(C, PR, proj, blocked)
    block = _extractor_block(out)
    text = "\n".join(block)
    check("... a supported graphify installed NOW: the extractor line says so (the next refresh uses it)",
          bool(block) and "graphify 0.8.39 is installed now" in block[0], block[0] if block else out[-300:])
    check("... and the NOT re-included line asks for a /deep-map:refresh, not a pip install",
          "NOT re-included" in text and "re-run /deep-map:refresh" in text and "-m pip install" not in text, text)

    # (c) the last build was fine, but the graphify installed NOW is unsupported: WARN for the next sync
    proj = work / "exNowBad"
    proj.mkdir()
    with _installed_graphify_as(C, False, "0.9.2", spec):
        rc, out = _doctor_for(C, PR, proj, dict(base, graphify_version="0.8.39"))
    block = _extractor_block(out)
    check("graphify installed NOW is outside the supported range: WARN + the pinned pip remedy",
          bool(block) and block[0].strip().startswith("[WARN") and "0.9.2" in block[0]
          and "-m pip install" in block[0], block[0] if block else out[-300:])
    check("... informational for the CURRENT graph: the verdict stays READY", H._verdict_of(out) == "READY",
          H._verdict_of(out))

    proj = work / "exLegacy"
    proj.mkdir()
    rc, out = _doctor_for(C, PR, proj, dict(base))
    block = _extractor_block(out)
    check("an older manifest (none of these keys): the extractor line alone, as before",
          len(block) == 1 and "last sync used graphify (multi-language)" in block[0], "\n".join(block))

    # graphify NOT importable now: every "install graphify" hint is the runnable, PINNED command for
    # DeepMap's own interpreter (a bare `pip install graphifyy` may hit another Python / an unsupported 0.9)
    pinned = f'"{Path(sys.executable).as_posix()}" -m pip install --upgrade "{spec}"'
    proj = work / "exNoEngine"
    proj.mkdir()
    with _graphify_missing(C):
        rc, out = _doctor_for(C, PR, proj, dict(base, graphify_version="0.8.39"))
    block = _extractor_block(out)
    check("graphify not importable now: WARN + the pinned runnable install command",
          bool(block) and block[0].strip().startswith("[WARN") and pinned in block[0], block[0] if block else "")
    proj = work / "exNoManifestNoEngine"
    proj.mkdir()
    with _graphify_missing(C):
        H._write_manifest(proj)   # a manifest without an `extractor` (pre-G4 sync): the no-engine hint path
        rc, out = _doctor_for(C, PR, proj, {})
    block = _extractor_block(out)
    check("auto with no graphify (no engine recorded): the pinned runnable install command, no bare pip",
          bool(block) and pinned in block[0] and " pip install graphifyy" not in block[0],
          block[0] if block else "")

    proj = work / "exFallback"
    proj.mkdir()
    rc, out = _doctor_for(C, PR, proj, {"extractor": "ast", "extractor_requested": "auto", "verdict": "DEGRADED",
                                        "reasons": [], "extractor_fallback_reason": "graphify crashed",
                                        "graphify_rescued_dirs": ["build"], "graphify_version": "0.8.39"})
    text = "\n".join(_extractor_block(out))
    check("an auto -> ast fallback: graphify's re-include facts are NOT shown (that graph was not kept)",
          "fell back automatically" in text and "re-included" not in text, text)


# ---- ML: the ML-stack remedy is runnable -----------------------------------------------------------------
def gate_ml_remedy(C, PR, work: Path) -> None:
    print("\n-- ML: doctor's ML-stack WARN names a runnable pip command (D6) --")
    proj = work / "mlP"
    proj.mkdir()
    # a Python the operator manages (DEEPMAP_MANAGED_VENV=0): without it, a Python that lacks the ML stack (CI installs
    # none) is sent to /deep-map:setup instead — tests/setup_bootstrap_gates.py section L
    with H._env(DEEPMAP_MANAGED_VENV="0"):
        rc, out = _doctor_for(C, PR, proj, {"extractor": "graphify", "verdict": "READY", "reasons": []}, ml_ok=False)
    ml = H._line_with(out, "ML stack")
    check("ML stack not importable: WARN", ml.strip().startswith("[WARN"), ml)
    check("... names `-m pip install` for torch + sentence-transformers", "-m pip install" in ml
          and "torch" in ml and "sentence-transformers" in ml, ml)
    check("... for DeepMap's own interpreter (the one running the CLI)",
          Path(sys.executable).as_posix() in ml.replace("\\", "/"), ml)
    check("... not a path to an installer script", "install.ps1" not in ml and "install.sh" not in ml, ml)


# ---- PR: one root rule --------------------------------------------------------------------------------
def gate_project_root_parity(C, work: Path) -> None:
    print("\n-- PR: retrieval_log.project_root == sync._sync_root == the hooks' rule, in every case --")
    if not shutil.which("git"):
        check("(git on PATH for the parity cases)", True, "skipped — git absent")
        return
    L = _load_module(BUILD / "retrieval_log.py", f"dm_rl_r3_{time.time_ns()}")
    HS = _load_module(HOOKS / "deepmap_hook_state.py", f"dm_hs_r3_{time.time_ns()}")
    S = _load_module(BUILD / "sync.py", f"dm_sync_r3_{time.time_ns()}")

    def same(a, b) -> bool:
        return a is not None and b is not None and \
            os.path.normcase(str(Path(a).resolve())) == os.path.normcase(str(Path(b).resolve()))

    g = work / "prRepo"
    H._git_repo(g)
    deep = g / "svc" / "api"
    deep.mkdir(parents=True)
    got = L.project_root(deep)
    check("git repo, NO manifest anywhere: the start dir itself (where a sync of it writes)",
          same(got, deep), str(got))
    check("... == sync._sync_root(start)", same(got, S._sync_root(deep.resolve())),
          f"{got} vs {S._sync_root(deep.resolve())}")
    check("... == the hooks' project_root(start)", same(got, HS.project_root(deep)), f"{got} vs {HS.project_root(deep)}")
    r, rc = C._root_arg(argparse.Namespace(root=str(deep)), "enrich")
    check("... and an explicit <root> for enrich/tune/insights maps the same way", rc is None and same(r, deep),
          f"{r} {rc}")
    H._write_manifest(deep, verdict="READY")               # a sub-project set up on its own
    for start in (deep, deep / "x"):
        start.mkdir(exist_ok=True)
        got = L.project_root(start)
        check(f"sub-project synced on its own, start={start.relative_to(g).as_posix()}: its sync root",
              same(got, deep) and same(got, S._sync_root(start.resolve())) and same(got, HS.project_root(start)),
              f"{got} | sync {S._sync_root(start.resolve())} | hooks {HS.project_root(start)}")
    H._write_manifest(g, verdict="READY")                  # the repo root synced too: the outermost wins
    got = L.project_root(deep / "x")
    check("repo root synced as well: the OUTERMOST manifest (the root) for every surface",
          same(got, g) and same(got, S._sync_root((deep / "x").resolve())) and same(got, HS.project_root(deep / "x")),
          f"{got} | sync {S._sync_root((deep / 'x').resolve())} | hooks {HS.project_root(deep / 'x')}")


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 72)
    print("DeepMap — CLI round-3 gates (W3: login-lock auth, impact remedy, doctor build facts, ML remedy, root)")
    print("=" * 72)
    saved_env = {k: os.environ.get(k) for k in H._SCRUB + ("DEEPMAP_HOME", "DEEPMAP_SELF_CALL_REBIND",
                                                          "DEEPMAP_RESCUE_SKIPPED_DIRS")}
    for k in saved_env:
        if k != "DEEPMAP_HOME":
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
            C = H._load_cli()
            PR = _load_module(BUILD / "port_registry.py", f"dm_pr_r3_{time.time_ns()}")
            for gate in (lambda: gate_rate_limit(C, PR, work),
                         lambda: gate_impact_cost(C, work),
                         lambda: gate_extractor_facts(C, PR, work),
                         lambda: gate_ml_remedy(C, PR, work),
                         lambda: gate_project_root_parity(C, work)):
                try:
                    gate()
                except Exception as exc:  # noqa: BLE001 — a crashing gate is a RED gate, not a crash
                    import traceback
                    traceback.print_exc()
                    check(f"gate ran without crashing ({type(exc).__name__}: {exc})", False)
                finally:
                    H._clear_registry(PR)
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
