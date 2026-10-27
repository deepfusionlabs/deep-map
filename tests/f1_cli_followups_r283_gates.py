#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""0.28.3 fix-all round 2 (F1, build/deepmap_cli.py) — three CLI follow-ups, RED-first.

A  enrich refusal exit code (O9 follow-up). `deepmap enrich` refuses at once when a sync (or another enrich) of the
   project holds its per-root lock — build/enrich.py prints one `[deepmap enrich] REFUSED — ...` line and reports it
   through `last_refusal` / enrich()'s stats['refused']; nothing is extracted, sent, spent or written. cmd_enrich
   still exited 0 and then printed summary lines that read like a finished run: --dry-run "undocumented candidates:
   0 ... pending: 0", --deterministic "enriched: 0 ... Nothing new to apply — the graph's summaries already carry every
   local signal.", the LLM tier "enrichable: 0 ... spend: ~$0.0000 ... Run /deep-map:refresh to embed the updated
   summaries." Now every mode exits 4 — the repo's "did nothing, the previous state is kept" code (sync's NOT PUSHED)
   — and prints nothing after enrich's own refusal line (kept, exactly once); `enrich --help` lists the exit codes.
B  tune help text (O10 follow-up; D3 / D4 / D2). `--tune-rrf-k` said "also tune the RRF k constant", but the tuned k
   is recorded as a recommendation only — live search and every learned-vs-equal judgement fuse at k=60
   (retrieval.RRF_K). `--seed-from-prior` said only "warm-start from the local cross-project prior", but on the default
   grid optimizer the seed is the tie-break anchor (among equally-scoring weights the one closest to the prior wins)
   and coord's starting point. `--apply` said "needs benchmark evidence", but that evidence must be bound to the
   current weights (weights_fingerprint). tune's own output printed a recorded non-default rrf_k bare; it now says it
   is a recommendation (the live k prints exactly as before).
C  NEO4J_URI password echo (O4 finding). _uri_for_message() returned an operator NEO4J_URI verbatim, so every doctor /
   status / main message printed `user:password@` whole — and the neo4j driver REFUSES userinfo in the URI
   ("Username is not supported in the URI"), so the error message is exactly where it showed. The driver's own error
   text can echo part of such a password too: a '?' in it turns the rest into a query string it quotes back ("Invalid
   parameters:'<rest>@host=' in query string ..."), a '/' or '#' makes it dial `<user>:<start of the password>` and
   name that address in its connect / DNS errors. Every printed URI now masks its userinfo (`bolt://user:***@host:7687`:
   scheme, user, host and port kept; a lone value is masked whole — it can be a token), and those driver echoes are
   masked too; the URI the driver is handed is unchanged (verbatim).

Round 3 (completeness critics):
A  cov-build[7]: exit 4 has three causes, not one — a sync (or another enrich) holds the project's lock, the lock cannot
   be opened (e.g. .deepmap is a file, or permission is denied), or <root> is not a directory. The epilog, cmd_enrich's
   docstring and commands/enrich.md named only the first and gave its remedy ("re-run once that run finishes") for all
   of them. Now they say enrich's stderr line names the cause and only a busy lock passes by waiting.
C  cov-build[8]: _redact_uri took the userinfo up to the URI's LAST '@', so an '@' after the host (a query holding one)
   printed a lone token whole and masked the port: 'neo4j+s://Tok9Z@db.example:7687?x=a@b' ->
   'neo4j+s://Tok9Z@db.example:***@b'. urlsplit — the driver's own parser — ends the userinfo at the NETLOC's last '@'
   (the netloc stops at the first '/', '?' or '#'), but a password holding '/', '?' or '#' ends that netloc early, so
   urlsplit alone would print part of it ('bolt://neo4j:Xy@3/9k@db' -> netloc 'neo4j:Xy@3'). Both readings' secrets
   are masked now — they agree on every ordinary URI — in _redact_uri, in the text pass and in the operator echoes.
   C3 also models the exception pairs the real driver raises for a host that does not resolve (ServiceUnavailable for
   EAI_NONAME, ValueError for EAI_FAIL — it scripted a ValueError over errno 11001, a pair the driver never raises):
   doctor's unreachable line now quotes the driver's DNS text (O4 A5, docs[3]), and it must print that text masked.
D  cov-build[4] (D5 follow-up): the instance.json writers refuse a file that is not a JSON object (never wipe it) and
   give up after a bounded wait for the write lock, but `learning --enable/--disable` and `tune` worded every refused
   write as an unwritable directory ("not a writable project dir. Set DEEPMAP_LEARNING=1 in the env instead." / "non-repo
   or read-only"). instance_state.write_problem (update_instance itself, run with a mutate that writes nothing and no
   wait) now names the cause and the CLI says what works for it: fix or delete the file, retry while another DeepMap
   process writes it, or (a truly unwritable .deepmap/) the old wording and the env var.

Hermetic: a temp DEEPMAP_HOME and temp project dirs; the "running sync" is a CHILD process holding the project's real
sync lock (sync._acquire_sync_lock — msvcrt / flock across processes), and the "other writer" in D a child holding
instance.json's real write lock (instance_state.instance_lock); enrich's LLM transport is a recording fake and
the LLM tier runs only in-process (never `claude -p`, no spend); extraction is DEEPMAP_EXTRACTOR=ast;
neo4j.GraphDatabase.driver is either the REAL driver on a URI it refuses while parsing (nothing connects — a URI it
would accept is closed unused and the run aborted) or a fake; provision is stubbed (no MCP probe); the ML stack is
faked. No Neo4j, no sockets, no network, no spend. Never touches ~/.deepmap.
Usage: python tests/f1_cli_followups_r283_gates.py [--root <tree>]
"""
from __future__ import annotations

import argparse
import ast
import contextlib
import importlib.util
import io
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PASSES: list[str] = []
FAILS: list[str] = []

_SCRUB = ("NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD", "DEEPMAP_ENRICH", "DEEPMAP_EXTRACTOR", "DEEPMAP_PYTHON",
          "DEEPMAP_LEARNING", "DEEPMAP_RETRIEVAL_LOG", "DEEPMAP_APPLY_LEARNED", "DEEPMAP_SEED_PRIOR",
          "DEEPMAP_AUTO_GITIGNORE", "DEEPMAP_HOOKS", "DEEPMAP_SEARCH_PORT", "DEEPMAP_EMBED_PORT", "DEEPMAP_MCP_HOST",
          "DEEPMAP_CLI_EMBED_SERVER", "DEEPMAP_PROJECT_ROOT", "DEEPMAP_INCREMENTAL_SYNC", "DEEPMAP_CANDIDATE_FLOOR",
          "DEEPMAP_IMPACT_EDGES", "DEEPMAP_IMPACT_PROFILE", "DEEPMAP_IMPACT_MAX_HOPS", "DEEPMAP_IMPACT_TIMEOUT_S")


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSES if ok else FAILS).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok or not detail else f" — {str(detail)[:900]}"))


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@contextlib.contextmanager
def _env(**kv):
    saved = {k: os.environ.get(k) for k in kv}
    for k, v in kv.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


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


def _main(C, argv, *, overrides=None):
    """(rc, stdout, stderr, raised) of an in-process C.main() with sys.argv = ['deepmap_cli.py', *argv]; `overrides`
    maps a module name to what C._load returns for it."""
    orig_load = C._load
    if overrides:
        C._load = lambda name: overrides[name] if name in overrides else orig_load(name)
    out, err = io.StringIO(), io.StringIO()
    saved = sys.argv
    rc = raised = None
    try:
        sys.argv = ["deepmap_cli.py", *argv]
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = C.main()
    except BaseException as e:  # noqa: BLE001 — a crash (or an argparse exit) is what the check reports
        raised = e
    finally:
        sys.argv = saved
        C._load = orig_load
    return rc, out.getvalue(), err.getvalue(), raised


def _subparser(C, cmd: str):
    ap = C.build_parser()
    sub = next(a for a in ap._actions if isinstance(a, argparse._SubParsersAction))
    return sub.choices[cmd]


def _flag_help(sp, flag: str) -> str:
    for act in sp._actions:
        if flag in act.option_strings:
            return " ".join(str(act.help or "").split())
    return ""


# ---- fake Neo4j ------------------------------------------------------------------------------------------------------
class _Result(list):
    def single(self):
        return self[0] if self else None


class _Session:
    def __init__(self, drv):
        self.drv = drv

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, q, **kw):
        text = str(getattr(q, "text", q))
        return _Result(self.drv.answer(text, kw))


class _Driver:
    """A fake driver: verify_connectivity raises `connect_exc` when given; sessions answer via `answer`."""

    def __init__(self, answer=None, connect_exc=None):
        self._answer, self.connect_exc = answer, connect_exc
        self.closed = False

    def answer(self, q, kw):
        return self._answer(q, kw) if self._answer is not None else []

    def verify_connectivity(self):
        if self.connect_exc is not None:
            raise self.connect_exc

    def session(self, **kw):
        return _Session(self)

    def close(self):
        self.closed = True


def _census(q, kw):
    """A small healthy graph: 5 nodes / 5 edges / 5 embedded, no owner, no index rows."""
    return [{"c": 5}] if "count(" in q else []


def _raising(exc):
    """A GraphDatabase.driver stand-in that raises `exc` at construction (where the real driver rejects a URI)."""
    def factory(uri, *a, **kw):
        raise exc
    return factory


@contextlib.contextmanager
def _patched_graphdb(factory):
    import neo4j
    raw = neo4j.GraphDatabase.__dict__["driver"]
    neo4j.GraphDatabase.driver = factory
    try:
        yield
    finally:
        setattr(neo4j.GraphDatabase, "driver", raw)


@contextlib.contextmanager
def _recording_real_driver():
    """The REAL GraphDatabase.driver, recording every URI it is handed. A URI it would ACCEPT is closed unused (a
    neo4j driver is lazy — nothing was connected) and the call aborted, so a gate run can never reach the network."""
    import neo4j
    raw = neo4j.GraphDatabase.__dict__["driver"]
    real = neo4j.GraphDatabase.driver
    seen: list = []

    def rec(uri, *a, **kw):
        seen.append(uri)
        drv = real(uri, *a, **kw)
        try:
            drv.close()
        finally:
            raise RuntimeError("gate: the real driver accepted this URI — the run is aborted before any connect")
    neo4j.GraphDatabase.driver = rec
    try:
        yield seen
    finally:
        setattr(neo4j.GraphDatabase, "driver", raw)


def _fake_provision():
    ns = types.SimpleNamespace()
    ns.MCP_SERVERS = {"deepmap-search": {"port_env": "DEEPMAP_SEARCH_PORT", "default_port": 8787},
                      "deepmap-embed": {"port_env": "DEEPMAP_EMBED_PORT", "default_port": 8788}}
    ns.mcp_host = lambda: "127.0.0.1"
    ns.mcp_port = lambda name: int(os.environ.get(ns.MCP_SERVERS[name]["port_env"],
                                                  ns.MCP_SERVERS[name]["default_port"]))
    ns.is_http_up = lambda host, port, timeout=2.0: False          # never a socket
    ns._ports_from_mcp_json = lambda root: None
    ns.isolation_note = lambda *a, **k: "isolation: (test stub)"
    ns.print_instance_table = lambda *a, **k: None
    ns._USER_SET_NEO4J_URI = False
    ns.neo4j_edition = lambda drv: "community"
    ns.paths = lambda *a, **k: {"data": Path("/nonexistent/deepmap/neo4j/data")}
    return ns


def _doctor(C, cwd: Path, *, factory=None):
    """(rc, stdout, stderr, raised) of an in-process C.cmd_doctor: GraphDatabase.driver = `factory` when given (else
    whatever is installed), provision stubbed, the ML stack faked."""
    orig_load = C._load
    prov = _fake_provision()
    C._load = lambda name: prov if name == "provision" else orig_load(name)
    out, err = io.StringIO(), io.StringIO()
    rc = raised = None
    try:
        ctx = _patched_graphdb(factory) if factory is not None else contextlib.nullcontext()
        with ctx, _chdir(cwd), _fake_ml_stack(), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = C.cmd_doctor(argparse.Namespace())
    except BaseException as e:  # noqa: BLE001 — a diagnostic that crashes is a failure the checks report
        raised = e
    finally:
        C._load = orig_load
    return rc, out.getvalue(), err.getvalue(), raised


def _neo_line(out: str) -> str:
    return next((ln for ln in out.splitlines() if "] Neo4j" in ln), "")


# =========================================================================== A: enrich's refusal exit code
_HOLDER = r"""
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import sync
h = sync._acquire_sync_lock(Path(sys.argv[2]))
print("LOCKED" if h is not None else "BUSY", flush=True)
if h is not None:
    sys.stdin.read()                 # hold the lock until the gate closes stdin
    sync._release_sync_lock(h)
"""
_PY = "def charge(card):\n    # WHY: settles the payment first\n    return create(card)\n"
_REFUSED = "[deepmap enrich] REFUSED"
# what each mode printed after a refusal — lines that read like a finished run
_SUMMARY = {
    "--dry-run": ("undocumented candidates", "already enriched", "pending:", "spends nothing"),
    "--deterministic": ("enriched:", "unchanged:", "already_done:", "Nothing new to apply", "Run /deep-map:refresh"),
    "--confirm-spend": ("enrichable:", "spend:", "already_done:", "Run /deep-map:refresh"),
}


def _child_env(home: Path) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in _SCRUB}
    env.update(DEEPMAP_HOME=str(home), PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    return env


@contextlib.contextmanager
def _sync_holding_lock(build: Path, root: Path, env: dict, script: str = _HOLDER):
    """A child process holding `root`'s sync lock (or, with `script`, another lock) for the block; yields its first line
    ('LOCKED' when it holds it). Released by closing its stdin; killed (our own child) only on a timeout."""
    proc = subprocess.Popen([sys.executable, "-c", script, str(build), str(root)], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                            errors="replace", env=env)
    watchdog = threading.Timer(300, proc.kill)
    watchdog.daemon = True
    watchdog.start()
    try:
        yield (proc.stdout.readline() or "").strip()
    finally:
        try:
            proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        watchdog.cancel()
        for stream in (proc.stdout, proc.stderr):
            try:
                stream.close()
            except OSError:
                pass


def _mkproj(base: Path, name: str) -> Path:
    p = base / name
    p.mkdir()
    (p / "a.py").write_text(_PY, encoding="utf-8")
    return p


def _cli(tree: Path, argv, env: dict, cwd: Path):
    """(rc, stdout, stderr) of the REAL entry point (`python build/deepmap_cli.py ...`) as a subprocess."""
    r = subprocess.run([sys.executable, str(tree / "build" / "deepmap_cli.py"), *argv], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", env=env, cwd=str(cwd), timeout=300)
    return r.returncode, r.stdout or "", r.stderr or ""


class _Transport:
    """A scripted `claude -p`: answers every unit, records every call (a call = a bill). Never spawns anything."""

    def __init__(self):
        self.calls: list = []

    def __call__(self, prompt, **kw):
        self.calls.append(prompt)
        n = max(1, prompt.count("### Unit "))
        return {"text": json.dumps({str(i): f"Settles the card payment before creating it ({i})."
                                    for i in range(1, n + 1)}), "cost_usd": 0.0, "is_error": False}


def _refusal_checks(label: str, rc, out: str, err: str, raised, mode: str, side: Path) -> None:
    check(f"{label}: exits 4 (did nothing — the previous state is kept), not 0",
          raised is None and rc == 4, f"rc={rc} raised={raised!r} stderr={err[-300:]!r}")
    leaked = [s for s in _SUMMARY[mode] if s in out or s in err]
    check(f"{label}: prints none of the finished-run summary lines", not leaked, f"{leaked} stdout={out[-400:]!r}")
    check(f"{label}: nothing on stdout — the refusal line is the whole answer", out.strip() == "", repr(out[-400:]))
    check(f"{label}: enrich's own REFUSED line is kept — exactly once, no traceback",
          err.count(_REFUSED) == 1 and "Traceback" not in err, err[-600:])
    check(f"{label}: nothing written (no sidecar)", not side.exists())


def gate_a_enrich_refusal(C, tree: Path, base: Path, home: Path) -> None:
    print("\n-- A: `deepmap enrich` refused by a running sync exits 4 with no summary (it exited 0 under zero counts) --")
    build, env = tree / "build", _child_env(home)
    cenv = dict(env, DEEPMAP_EXTRACTOR="ast")
    E = C._load("enrich")
    check("premise: build/enrich.py reports a refusal (last_refusal / stats['refused'])",
          hasattr(E, "last_refusal") and "refused" in (E.enrich.__doc__ or ""), "")
    t = _Transport()
    E.default_transport = t                        # the LLM tier's transport: a recording fake, never `claude -p`
    over = {"enrich": E}

    for mode in ("--dry-run", "--deterministic"):
        proj = _mkproj(base, "a" + mode.strip("-").replace("-", "_"))
        side = proj / ".deepmap" / "summaries.jsonl"
        with _sync_holding_lock(build, proj, env) as state:
            check(f"A {mode} setup: a child process (the running sync) holds the project's sync lock",
                  state == "LOCKED", state)
            rc, out, err = _cli(tree, ["enrich", str(proj), mode], cenv, proj)
            _refusal_checks(f"A {mode} (the real entry point, a subprocess)", rc, out, err, None, mode, side)
            with _env(DEEPMAP_EXTRACTOR="ast"), _chdir(proj):
                rc, out, err, raised = _main(C, ["enrich", str(proj), mode], overrides=over)
            _refusal_checks(f"A {mode} (in-process main)", rc, out, err, raised, mode, side)
            if mode == "--dry-run":
                with _chdir(proj):
                    rc, out, err, raised = _main(C, ["enrich", str(proj), "--status"], overrides=over)
                check("A control: --status during the running sync still answers (exit 0) — it never extracts",
                      raised is None and rc == 0 and "deepmap enrich --status" in out, f"rc={rc} {out[-300:]!r}")
        rc, out, err = _cli(tree, ["enrich", str(proj), mode], cenv, proj)
        if mode == "--dry-run":
            check("A --dry-run control: once the sync finished, the preview runs — exit 0, 1 candidate, no refusal",
                  rc == 0 and "undocumented candidates: 1" in out and _REFUSED not in err,
                  f"rc={rc} stdout={out[-400:]!r} stderr={err[-400:]!r}")
        else:
            check("A --deterministic control: once the sync finished, it enriches — exit 0, 'enriched: 1', the "
                  "refresh hint, the sidecar written",
                  rc == 0 and "enriched: 1" in out and "Run /deep-map:refresh" in out and side.exists(),
                  f"rc={rc} stdout={out[-400:]!r} stderr={err[-400:]!r}")

    # the LLM tier (in-process only: its transport is the recording fake)
    proj = _mkproj(base, "a_llm")
    side = proj / ".deepmap" / "summaries.jsonl"
    with _sync_holding_lock(build, proj, env) as state:
        check("A LLM tier setup: the running sync holds the project's lock", state == "LOCKED", state)
        with _env(DEEPMAP_EXTRACTOR="ast", DEEPMAP_ENRICH="1"), _chdir(proj):
            rc, out, err, raised = _main(C, ["enrich", str(proj), "--confirm-spend"], overrides=over)
        _refusal_checks("A LLM tier (in-process)", rc, out, err, raised, "--confirm-spend", side)
        check("A LLM tier: the LLM transport was never called (no `claude -p`, no spend)", t.calls == [], len(t.calls))
    with _env(DEEPMAP_EXTRACTOR="ast", DEEPMAP_ENRICH="1"), _chdir(proj):
        rc, out, err, raised = _main(C, ["enrich", str(proj), "--confirm-spend"], overrides=over)
    check("A LLM tier control: once the sync finished, the tier runs — exit 0, the (fake) transport called, the "
          "spend line printed", raised is None and rc == 0 and len(t.calls) >= 1 and "spend:" in out,
          f"rc={rc} raised={raised!r} calls={len(t.calls)} stdout={out[-400:]!r} stderr={err[-400:]!r}")

    # `enrich --help` names the exit codes, 4 included
    try:
        h = " ".join(_subparser(C, "enrich").format_help().split())
    except Exception as e:  # noqa: BLE001
        h = repr(e)
    check("A `enrich --help` lists exit 4: REFUSED — a sync (or another enrich) of this project holds its lock, "
          "nothing was done", re.search(r"\b4\s+REFUSED\b", h) is not None and "lock" in h and "0 " in h, h[-700:])

    # round 3 (cov-build[7]): exit 4 has three causes, and only a BUSY lock passes by waiting. The second one, live:
    # the lock cannot be opened (.deepmap is a regular file) — enrich's own stderr line names it.
    projf = _mkproj(base, "a_lockfile")
    (projf / ".deepmap").write_text("a file where the .deepmap directory belongs", encoding="utf-8")
    rc, out, err = _cli(tree, ["enrich", str(projf), "--dry-run"], cenv, projf)
    check("A premise: a lock that cannot be opened (.deepmap is a file) also exits 4 — nothing on stdout, and enrich's "
          "REFUSED line (once) names THAT cause, not a running sync",
          rc == 4 and out.strip() == "" and err.count(_REFUSED) == 1 and "cannot open this project's sync lock" in err
          and "is running" not in err, f"rc={rc} stdout={out[-300:]!r} stderr={err[-500:]!r}")
    try:
        ep = str(getattr(_subparser(C, "enrich"), "epilog", "") or "")
    except Exception as e:  # noqa: BLE001
        ep = repr(e)
    i4 = ep.find("  4  ")
    h4 = " ".join(ep[i4:].split()) if i4 >= 0 else ""
    check("A `enrich --help` exit 4: says enrich's stderr line names the cause", "stderr line names the cause" in h4, h4)
    check("A `enrich --help` exit 4: lists a lock that cannot be opened and a <root> that is not a directory",
          "cannot be opened" in h4 and "not a directory" in h4, h4)
    check("A `enrich --help` exit 4: 're-run once that run finishes' is the busy lock's remedy only",
          re.search(r"holds its lock \(re-run[^)]*finishes\)", h4) is not None and h4.count("finishes") == 1, h4)
    doc = " ".join((getattr(C.cmd_enrich, "__doc__", None) or "").split())
    check("A cmd_enrich's docstring: exit 4 also covers a lock that cannot be opened and a <root> that is not a "
          "directory", "cannot be opened" in doc and "not a directory" in doc, doc[-600:])
    md = (tree / "commands" / "enrich.md").read_text(encoding="utf-8")
    b4 = " ".join(md[md.find("- `4`"):].split("\n\n", 1)[0].split()) if "- `4`" in md else ""
    check("A commands/enrich.md exit 4: the stderr line names the cause; a lock that cannot be opened and a target that "
          "is not a directory are named", "names the cause" in b4 and "cannot open this project's sync lock" in b4
          and "is not a directory" in b4, b4)
    pos = [b4.find(t) for t in ("is running", "wait for that run to finish", "cannot open this project's sync lock")]
    check("A commands/enrich.md exit 4: 'wait for that run to finish' belongs to the running-sync line only; the other "
          "causes are fixed first", -1 not in pos and pos == sorted(pos) and "fix what it names" in b4, b4)

    # the CLI reads the refusal defensively: an enrich module without that channel (an older build) still previews
    fake = types.SimpleNamespace(iter_enrichable_nodes=lambda root, include_modules=False: [],
                                 load_sidecar=lambda root: {})
    proj0 = base / "a_old"
    proj0.mkdir()
    with _chdir(proj0):
        rc, out, err, raised = _main(C, ["enrich", str(proj0), "--dry-run"], overrides={"enrich": fake})
    check("A control: an enrich module without the refusal channel (an older build) still previews — exit 0, no crash",
          raised is None and rc == 0 and "undocumented candidates: 0" in out, f"rc={rc} raised={raised!r}")


# =========================================================================== B: tune's help and output
def _rrf_k_of(tree: Path):
    """retrieval.RRF_K — the k live search fuses at — read from the tree's source (no import)."""
    for n in ast.parse((tree / "build" / "retrieval.py").read_text(encoding="utf-8")).body:
        tgt = n.target if isinstance(n, ast.AnnAssign) else (
            n.targets[0] if isinstance(n, ast.Assign) and len(n.targets) == 1 else None)
        if getattr(tgt, "id", None) == "RRF_K" and isinstance(getattr(n, "value", None), ast.Constant):
            return n.value.value
    return None


def _tune_args(root: Path, **kw):
    base = dict(root=str(root), status=False, report=False, apply=False, export_prior=False, seed_from_prior=False,
                min_queries=None, min_queries_bucket=None, objective="ndcg", optimizer="grid", tune_rrf_k=False,
                no_profiles=False, dry_run=False)
    base.update(kw)
    return argparse.Namespace(**base)


def gate_b_tune_help(C, tree: Path, base: Path) -> None:
    print("\n-- B: tune's help (and output) say what --tune-rrf-k / --seed-from-prior / --apply really do --")
    k = _rrf_k_of(tree)
    check("premise: retrieval.RRF_K — the k live search fuses at — is read from the tree", isinstance(k, int), k)
    T = _load(tree / "build" / "tune_weights.py", f"f1_tw_{time.time_ns()}")
    tp = " ".join((T.tune_profiles.__doc__ or "").split())
    check("premise (tune_weights): --tune-rrf-k searches other ks, and the best one is a recommendation only (every "
          "learned-vs-equal judgement fuses at RRF_K)",
          "recommendation only" in tp and getattr(T, "RRF_K", None) == k
          and any(x != k for x in getattr(T, "_RRF_K_GRID", ())), tp[-500:])
    check("premise (tune_weights): a seed is coord's starting point AND the grid's tie-break anchor (the closest to "
          "the seed wins among equal scores)", "starting point" in tp and "tie-break anchor" in tp
          and "closest to the seed" in tp, tp[:700])
    check("premise (tune_weights): --apply needs evidence bound to the CURRENT weights (weights_fingerprint)",
          "weights_fingerprint" in " ".join((T.apply_learned.__doc__ or "").split()), "")

    sp = _subparser(C, "tune")
    rk = _flag_help(sp, "--tune-rrf-k")
    check("--tune-rrf-k help: the tuned k is recorded as a recommendation only", "recommendation" in rk, rk)
    check(f"--tune-rrf-k help: live search and every learned-vs-equal judgement use k={k}",
          f"k={k}" in rk and "live search" in rk and re.search(r"judge?ment", rk) is not None, rk)
    sd = _flag_help(sp, "--seed-from-prior")
    check("--seed-from-prior help: on the grid the seed breaks ties (the weights closest to the prior win)",
          "tie" in sd and "closest" in sd and "grid" in sd, sd)
    check("--seed-from-prior help: ... and coord starts from it", "coord" in sd, sd)
    check("--seed-from-prior help: still a warm start from the local cross-project prior",
          "warm-start" in sd and "cross-project prior" in sd, sd)
    ap_ = _flag_help(sp, "--apply")
    check("--apply help: the benchmark evidence must be bound to the current weights",
          "bound to the current weights" in ap_ and "operator-gated" in ap_, ap_)
    try:
        h = " ".join(sp.format_help().split())
        ok = all(x in h for x in (f"k={k}", "closest", "bound to the current weights"))
    except Exception as e:  # noqa: BLE001 — an argparse %-format error in a help string is the failure
        ok, h = False, repr(e)
    check("`tune -h` renders (no %-format error) and shows the three corrected help texts", ok, h[-700:])

    # tune --report: a recorded non-default rrf_k is labelled; the live k prints exactly as before
    proj = base / "b_tune"
    (proj / ".deepmap").mkdir(parents=True)

    def report_line(rrf_k):
        (proj / ".deepmap" / "instance.json").write_text(json.dumps({"learned_fusion": {
            "arm_weights": {"vector": 1.25, "bm25": 0.75, "graph_weighted": 1.0}, "applied": False,
            "beats_equal": True, "significant": True, "objective": "ndcg", "reason": "learned_beats_equal",
            "n_labeled_queries": 200, "rrf_k": rrf_k}}), encoding="utf-8")
        out = io.StringIO()
        with _env(DEEPMAP_LEARNING=None), contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            rc = C.cmd_tune(_tune_args(proj, report=True))
        return rc, next((x for x in out.getvalue().splitlines() if "rrf_k=" in x), "")

    rc, ln = report_line(30)
    tail = ln.split("rrf_k=30", 1)[-1]
    check(f"tune --report: a recorded rrf_k=30 (a --tune-rrf-k result) says it is a recommendation — live k={k}",
          rc == 0 and "rrf_k=30" in ln and "recommendation" in tail and f"k={k}" in tail, f"{rc} {ln!r}")
    rc, ln = report_line(k)
    check(f"tune --report control: rrf_k={k} (the live k) prints exactly as before, unlabelled",
          rc == 0 and ln.rstrip().endswith(f"rrf_k={k}") and "recommendation" not in ln, f"{rc} {ln!r}")

    # tune (dry run): the result line — tune_profiles scripted so the recorded k is 30, then the live one
    T2 = C._load("tune_weights")

    def tune_line(rrf_k):
        T2.tune_profiles = lambda **kw: {
            "n_labeled_queries": 200, "reason": "learned_beats_equal", "objective": "ndcg", "rrf_k": rrf_k,
            "beats_equal": True, "significant": True, "n_folds": 5, "cv_mean_delta": 0.01,
            "bootstrap": {"ci_lo": 0.001, "ci_hi": 0.02}, "profiles": {},
            "arm_weights": {"vector": 1.25, "bm25": 0.75, "graph_weighted": 1.0}}
        orig = C._load
        C._load = lambda name: T2 if name == "tune_weights" else orig(name)
        out = io.StringIO()
        try:
            with _env(DEEPMAP_LEARNING=None), contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(io.StringIO()):
                rc = C.cmd_tune(_tune_args(proj, tune_rrf_k=True, dry_run=True))
        finally:
            C._load = orig
        return rc, next((x for x in out.getvalue().splitlines() if "labeled queries:" in x), "")

    rc, ln = tune_line(30)
    tail = ln.split("rrf_k=30", 1)[-1]
    check(f"tune --tune-rrf-k: a result recorded at rrf_k=30 says it is a recommendation — live k={k}",
          rc == 0 and "rrf_k=30" in ln and "recommendation" in tail and f"k={k}" in tail, f"{rc} {ln!r}")
    rc, ln = tune_line(k)
    check(f"tune control: rrf_k={k} prints exactly as before ('objective: ndcg (rrf_k={k})')",
          rc == 0 and ln.rstrip().endswith(f"objective: ndcg (rrf_k={k})"), f"{rc} {ln!r}")


# =========================================================================== C: a NEO4J_URI's password is never printed
_PW = "s3cr3tPW-f1"
_URI_PW = f"bolt://neo4j:{_PW}@127.0.0.1:7687"
_MASKED = "bolt://neo4j:***@127.0.0.1:7687"
_SECRETS = (_PW, "hunter2-f1", "w0rd-f1", "Wk-f1", "Tok3nZZ-f1", "s3cr3t-f1", "pw-f1", "Zx9other-f1", "Tok9Z-f1",
            "mer9-f1", "9k-f1")
# round 3 (cov-build[8]): an '@' AFTER the host. urlsplit (the driver's parser) ends the userinfo at the netloc's last
# '@'; the URI's LAST '@' is the other reading (a password holding '/', '?' or '#' ends that netloc early) — both
# readings' secrets are masked.
_URI_TOKEN_QUERY_AT = "neo4j+s://Tok9Z-f1@db.example.invalid:7687?x=a@b"   # urlsplit: token 'Tok9Z-f1'
_TOKEN_QUERY_AT_MASKED = "neo4j+s://***@db.example.invalid:***@b"
_CAPTURED: list = []   # every CLI output section C produced (the closing aggregate check)
_REDACT_CASES = [      # (uri, how it must print)
    (_URI_PW, _MASKED),
    ("neo4j+s://admin:hunter2-f1@db.example.invalid:7687?policy=eu",
     "neo4j+s://admin:***@db.example.invalid:7687?policy=eu"),
    ("bolt://neo4j:p@ss:w0rd-f1@db.example.invalid:7687", "bolt://neo4j:***@db.example.invalid:7687"),
    ("bolt://neo4j:Qz/Wk-f1@127.0.0.1:7687", _MASKED),
    ("bolt://neo4j:Qz?Wk-f1@127.0.0.1:7687", _MASKED),
    ("bolt://neo4j:Qz#Wk-f1@127.0.0.1:7687", _MASKED),
    ("bolt://neo4j:Qz Wk-f1@127.0.0.1:7687", _MASKED),
    ("bolt://neo4j:Qz%2FWk-f1@127.0.0.1:7687", _MASKED),
    ("bolt://neo4j:pw-f1@[::1]:7687", "bolt://neo4j:***@[::1]:7687"),
    ("bolt://Tok3nZZ-f1@db.example.invalid:7687", "bolt://***@db.example.invalid:7687"),
    ("neo4j:s3cr3t-f1@127.0.0.1:7687", "neo4j:***@127.0.0.1:7687"),
    # round 3 (cov-build[8]) — it printed 'neo4j+s://Tok9Z-f1@db.example.invalid:***@b' (the token whole, the port masked)
    (_URI_TOKEN_QUERY_AT, _TOKEN_QUERY_AT_MASKED),
    # ... and why the netloc alone is not enough: urlsplit reads 'bob' as a lone userinfo and 'Sum' as the port, and
    # 'neo4j:Xy' + host '3' — the rest of each password sits after a '/' (the netloc rule would print it)
    ("bolt://bob@corp.example:Sum/mer9-f1@db.example.invalid:7687",
     "bolt://***@corp.example:***@db.example.invalid:7687"),
    ("bolt://neo4j:Xy@3/9k-f1@db.example.invalid:7687", "bolt://neo4j:***@db.example.invalid:7687"),
]
_PLAIN = ("bolt://127.0.0.1:7687", "bolt://localhost:7999", "http://127.0.0.1:7474", "bolt://[::1]:7687",
          "neo4j+s://abc123.databases.neo4j.io", "neo4j://db.example.invalid:7687?policy=eu", "this project's Neo4j", "")


def _kinds(NE):
    """One realistic exception per kind main / doctor word (the classifier's own view is checked as a premise)."""
    def hyd(code, msg):
        return NE.Neo4jError._hydrate_neo4j(code=code, message=msg)
    refused = NE.ServiceUnavailable("Couldn't connect to 127.0.0.1:7687 (resolved to ('127.0.0.1:7687',)): Failed to "
                                    "establish connection")
    refused.__cause__ = ConnectionRefusedError(10061, "actively refused")
    hung = NE.ServiceUnavailable("Couldn't connect to 127.0.0.1:7687: timed out")
    hung.__cause__ = TimeoutError("timed out")
    dropped = NE.ServiceUnavailable("Connection to 127.0.0.1:7687 closed with incomplete handshake response")
    dropped.__cause__ = ConnectionResetError(10054, "forcibly closed")
    return [
        ("driver", "driver", NE.ConfigurationError("Username is not supported in the URI")),
        ("unreachable", "unreachable", refused),
        ("auth", "auth", hyd("Neo.ClientError.Security.Unauthorized",
                             "The client is unauthorized due to authentication failure.")),
        ("auth (login lock)", "auth", hyd("Neo.ClientError.Security.AuthenticationRateLimit",
                                          "The client has provided incorrect authentication details too many "
                                          "times in a row.")),
        ("query", "query", hyd("Neo.ClientError.Statement.SyntaxError", "Invalid input 'X'")),
        ("hung", "hung", hung),
        ("dropped", "dropped", dropped),
    ]


def gate_c_uri_redaction(C, tree: Path, base: Path) -> None:
    print("\n-- C: a NEO4J_URI's password is never printed (doctor / status / every Neo4j error); the driver still gets "
          "the URI verbatim --")
    import neo4j
    import neo4j.exceptions as NE
    R = getattr(C, "_redact_uri", None)
    check("deepmap_cli._redact_uri exists (the one masking rule for a printed URI)", callable(R))

    def redact(u):
        try:
            return R(u) if callable(R) else None
        except Exception as e:  # noqa: BLE001
            return f"<raised {type(e).__name__}>"
    for uri, want in _REDACT_CASES:
        got = redact(uri)
        check(f"_redact_uri({uri!r}) -> {want!r} (scheme/host/port kept, the secret gone)", got == want, repr(got))
    bad = [(u, redact(u)) for u in _PLAIN if redact(u) != u]
    check("_redact_uri leaves a URI without credentials (and a non-URI) unchanged", not bad, bad)
    odd = []
    for v in (None, 7687, b"bolt://a:b@h", types.SimpleNamespace(__str__=None)):
        g = redact(v)
        if not isinstance(g, str) or "b@h" in g:
            odd.append((v, g))

    class _Unprintable:
        def __str__(self):
            raise RuntimeError("no text")
    g = redact(_Unprintable())
    if not isinstance(g, str):
        odd.append(("unprintable", g))
    check("_redact_uri never raises and never returns the raw value of odd input", not odd, odd)

    # round 3 (cov-build[8]): one masking rule everywhere — masking twice changes nothing, and the text pass (a URI
    # quoted inside any message) masks a URI exactly as _redact_uri does
    T0 = getattr(C, "_redact_text", None)
    twice = [u for u, _w in _REDACT_CASES if redact(redact(u)) != redact(u)]
    check("_redact_uri is idempotent on every case (a masked URI masked again is unchanged)", not twice, twice)
    differ = []
    with _env(NEO4J_URI=None):
        for u, _w in _REDACT_CASES:
            if "://" in u and not re.search(r"\s", u):
                got_t = T0(f"refused {u} (x)") if callable(T0) else None
                if got_t != f"refused {redact(u)} (x)":
                    differ.append((u, got_t))
    check("the text pass masks a URI inside a message exactly as _redact_uri does (the '@'-after-the-host shape too)",
          not differ, differ)

    proj = base / "c_proj"
    proj.mkdir()

    # C1b (round 3): the token of an operator NEO4J_URI whose query holds an '@' — the real driver refuses the userinfo
    # ("Username is not supported in the URI"); every message names the endpoint with BOTH readings' secrets masked
    for argv in (["status"], ["search", "foo"]):
        label = " ".join(argv)
        with _env(NEO4J_URI=_URI_TOKEN_QUERY_AT), _chdir(proj), _fake_ml_stack(), _recording_real_driver() as seen:
            rc, out, err, raised = _main(C, argv)
        _CAPTURED.append(out + err)
        check(f"C1b `{label}` with a token and an '@' in the query: exit 3, the endpoint named masked "
              f"({_TOKEN_QUERY_AT_MASKED}), the token nowhere, the driver handed the URI verbatim",
              raised is None and rc == 3 and _TOKEN_QUERY_AT_MASKED in err and "Tok9Z-f1" not in out + err
              and seen == [_URI_TOKEN_QUERY_AT], f"rc={rc} raised={raised!r} seen={seen} {err[-400:]!r}")
    with _env(NEO4J_URI=_URI_TOKEN_QUERY_AT), _recording_real_driver() as seen:
        rc, out, err, raised = _doctor(C, proj)
    _CAPTURED.append(out + err)
    check("C1b doctor: FAIL + exit 1, its Neo4j line names the endpoint masked, the token nowhere",
          raised is None and rc == 1 and _TOKEN_QUERY_AT_MASKED in _neo_line(out) and "Tok9Z-f1" not in out + err,
          f"rc={rc} {raised!r} {_neo_line(out)}")

    # C1: the real driver refuses user:password@ while parsing — every command's error names the endpoint masked
    for argv in (["status"], ["impact", "foo"], ["search", "foo"]):
        label = " ".join(argv)
        with _env(NEO4J_URI=_URI_PW), _chdir(proj), _fake_ml_stack(), _recording_real_driver() as seen:
            rc, out, err, raised = _main(C, argv)
        _CAPTURED.append(out + err)
        check(f"C1 `{label}` with NEO4J_URI={_URI_PW!r}: the driver refuses it — exit 3, no traceback",
              raised is None and rc == 3, f"rc={rc} raised={raised!r}")
        check(f"C1 `{label}`: the message names the endpoint masked ({_MASKED})", _MASKED in err, err[-500:])
        check(f"C1 `{label}`: the password is printed nowhere", _PW not in out + err, (out + err)[-500:])
        check(f"C1 `{label}`: the driver was still handed the URI VERBATIM (connecting is unchanged)",
              seen == [_URI_PW], seen)

    with _env(NEO4J_URI=_URI_PW), _recording_real_driver() as seen:
        rc, out, err, raised = _doctor(C, proj)
    _CAPTURED.append(out + err)
    ln = _neo_line(out)
    check("C1 doctor: FAIL + exit 1 (unchanged), its Neo4j line names the endpoint masked",
          raised is None and rc == 1 and "[FAIL] Neo4j" in ln and _MASKED in ln, f"rc={rc} {raised!r} {ln}")
    check("C1 doctor: the password appears nowhere in doctor's output", _PW not in out + err, ln)
    check("C1 doctor: the driver was handed the URI verbatim", bool(seen) and set(seen) == {_URI_PW}, seen)

    # C2: every error kind, in main's message and in doctor's line
    for label, kind, exc in _kinds(NE):
        got = C._classify_db_error(exc)
        check(f"C2 premise: the {label} exception is classified '{kind}'", got == kind, got)
        with _env(NEO4J_URI=_URI_PW):
            msg = C._db_error_message("search", exc)
            rc, out, err, raised = _doctor(C, proj, factory=lambda uri, *a, _e=exc, **k: _Driver(connect_exc=_e))
        _CAPTURED.extend([msg, out + err])
        check(f"C2 {label}: main's message names the endpoint masked, never the password",
              _MASKED in msg and _PW not in msg, msg[:400])
        ln = _neo_line(out)
        check(f"C2 {label}: doctor FAILs (exit 1) with the endpoint masked, never the password",
              raised is None and rc == 1 and _MASKED in ln and _PW not in out + err, f"rc={rc} {raised!r} {ln}")

    # C3: the driver's OWN error text echoes part of a password holding '?' (a query string) ...
    uri_q = "bolt://neo4j:Qz?Wk-f1@127.0.0.1:7687"
    real_exc = None
    try:
        neo4j.GraphDatabase.driver(uri_q, auth=("neo4j", "x")).close()   # lazy: never connects
    except Exception as e:  # noqa: BLE001
        real_exc = e
    check("C3 premise: the real driver quotes the rest of a '?'-password back as a query string",
          real_exc is not None and "Wk-f1" in str(real_exc), repr(real_exc))
    if real_exc is None:
        real_exc = NE.ConfigurationError("Invalid parameters:'Wk-f1@127.0.0.1:7687=' in query string "
                                         "'Wk-f1@127.0.0.1:7687'.")
    with _env(NEO4J_URI=uri_q):
        msg = C._db_error_message("status", real_exc)
        rc, out, err, raised = _doctor(C, proj, factory=_raising(real_exc))
    _CAPTURED.extend([msg, out + err])
    check("C3 '?' in the password: main's message shows no part of it (the driver's echo is masked)",
          "Wk-f1" not in msg and "Qz" not in msg and "***" in msg, msg[:500])
    check("C3 '?' in the password: ... nor does doctor", raised is None and rc == 1 and "Wk-f1" not in out + err
          and "Qz" not in out + err, _neo_line(out))

    # ... and a '/' or '#' splits the URI early: the driver dials `neo4j:<start of the password>` and names it
    from urllib.parse import urlparse
    for sep in ("/", "#"):
        uri_s = f"bolt://neo4j:Qz{sep}Wk-f1@127.0.0.1:7687"
        try:
            from neo4j._api import parse_neo4j_uri
            netloc = parse_neo4j_uri(uri_s)[2].netloc
        except Exception:  # noqa: BLE001 — a driver without that helper: its urlparse, the same split
            netloc = urlparse(uri_s).netloc
        check(f"C3 premise: with '{sep}' in the password the driver's target address is 'neo4j:Qz'",
              netloc == "neo4j:Qz", netloc)
        # the pairs the real driver raises (neo4j/_async_compat/network/_util.py): an unknown host (EAI_NONAME) is a
        # ServiceUnavailable — the 'unreachable' kind, whose doctor line now quotes the DNS text (round 3, docs[3]) —
        # and another resolver error (EAI_FAIL) a ValueError, the 'driver' kind
        for exc_type, eai in ((NE.ServiceUnavailable, socket.EAI_NONAME), (ValueError, socket.EAI_FAIL)):
            dns = exc_type(f"Failed to DNS resolve address neo4j:Qz: [Errno {eai}] getaddrinfo failed")
            dns.__cause__ = socket.gaierror(eai, "getaddrinfo failed")
            tag = f"C3 '{sep}' in the password, {exc_type.__name__} ({C._classify_db_error(dns)})"
            with _env(NEO4J_URI=uri_s):
                msg = C._db_error_message("status", dns)
                rc, out, err, raised = _doctor(C, proj, factory=lambda uri, *a, _e=dns, **k: _Driver(connect_exc=_e))
            _CAPTURED.extend([msg, out + err])
            check(f"{tag}: the DNS error's address (`neo4j:Qz`) is masked in main's message",
                  "Qz" not in msg and "Wk-f1" not in msg and "Failed to DNS resolve address" in msg, msg[:500])
            check(f"{tag}: ... and in doctor, whose Neo4j line quotes the DNS error", raised is None and rc == 1
                  and "Qz" not in out + err and "Wk-f1" not in out + err
                  and "Failed to DNS resolve address neo4j:***" in _neo_line(out), _neo_line(out))

    # C4: an error text that itself carries user:password@ (any source) is masked; a credential-free URI is not
    other = NE.DriverError("cannot use bolt://admin:Zx9other-f1@10.0.0.5:7687 here")
    with _env(NEO4J_URI="bolt://127.0.0.1:7999"):
        msg = C._db_error_message("status", other)
        rc, out, err, raised = _doctor(C, proj, factory=_raising(other))
    _CAPTURED.extend([msg, out + err])
    check("C4 an error text carrying user:password@ is masked in main's message",
          "Zx9other-f1" not in msg and "bolt://admin:***@10.0.0.5:7687" in msg, msg[:400])
    check("C4 ... and in doctor", "Zx9other-f1" not in out + err and "bolt://admin:***@10.0.0.5:7687" in out,
          _neo_line(out))
    check("C4 control: a NEO4J_URI without credentials is printed verbatim (main and doctor)",
          "bolt://127.0.0.1:7999" in msg and "bolt://127.0.0.1:7999" in _neo_line(out), f"{msg[:200]} | {_neo_line(out)}")

    # C5: the OK path — a driver that accepted the URI (an older / different driver): doctor and status mask it
    with _env(NEO4J_URI=_URI_PW):
        rc, out, err, raised = _doctor(C, proj, factory=lambda uri, *a, **k: _Driver(answer=_census))
    _CAPTURED.append(out + err)
    ok_ln = next((x for x in out.splitlines() if "Neo4j reachable" in x), "")
    check("C5 doctor OK path: 'Neo4j reachable — <masked endpoint>', no password anywhere",
          raised is None and _MASKED in ok_ln and _PW not in out + err, f"{raised!r} {ok_ln}")
    with _env(NEO4J_URI=_URI_PW), _chdir(proj), _patched_graphdb(lambda uri, *a, **k: _Driver(answer=_census)):
        rc, out, err, raised = _main(C, ["status"])
    _CAPTURED.append(out + err)
    hdr = next((x for x in out.splitlines() if x.startswith("DeepMap status @")), "")
    check("C5 status header: 'DeepMap status @ <masked endpoint>', no password anywhere",
          raised is None and rc == 0 and hdr.startswith(f"DeepMap status @ {_MASKED}") and _PW not in out + err,
          f"rc={rc} {raised!r} {hdr!r}")

    # C6: a dropped-and-retried read announces the endpoint — masked
    dropped = next(e for lb, _k, e in _kinds(NE) if lb == "dropped")
    calls = {"n": 0}

    def flaky(uri, *a, **k):
        calls["n"] += 1
        return _Driver(connect_exc=dropped) if calls["n"] == 1 else _Driver(answer=_census)
    with _env(NEO4J_URI=_URI_PW), _chdir(proj), _patched_graphdb(flaky):
        rc, out, err, raised = _main(C, ["status"])
    _CAPTURED.append(out + err)
    check("C6 a dropped connection's retry notice names the endpoint masked",
          raised is None and rc == 0 and "closed a new connection" in err and _MASKED in err and _PW not in out + err,
          f"rc={rc} {raised!r} {err[-400:]!r}")

    # C7: the text masking is exact — only the secret part of a driver echo is masked (the user and host stay
    # readable), a URL component that merely LOOKS like the secret is never touched, and masking twice changes nothing
    T = getattr(C, "_redact_text", None)
    check("deepmap_cli._redact_text exists (the masking every printed Neo4j message goes through)", callable(T))

    def rt(text, uri):
        try:
            with _env(NEO4J_URI=uri):
                return T(text) if callable(T) else None
        except Exception as e:  # noqa: BLE001
            return f"<raised {type(e).__name__}>"
    got = rt("Failed to DNS resolve address neo4j:Qz: [Errno 11001] getaddrinfo failed",
             "bolt://neo4j:Qz/Wk-f1@127.0.0.1:7687")
    check("C7 a '/'-password's DNS echo keeps the user, masks only the password part: 'address neo4j:***:'",
          got == "Failed to DNS resolve address neo4j:***: [Errno 11001] getaddrinfo failed", repr(got))
    got = rt("Invalid parameters:'Wk-f1@127.0.0.1:7687=' in query string 'Wk-f1@127.0.0.1:7687'.",
             "bolt://neo4j:Qz?Wk-f1@127.0.0.1:7687")
    check("C7 a '?'-password's query-string echo keeps the host, masks only the password part",
          got == "Invalid parameters:'***@127.0.0.1:7687=' in query string '***@127.0.0.1:7687'.", repr(got))
    plain = '"C:/work/proj/python.exe" -m pip install --upgrade "neo4j>=6.0.1,<7"; see http://127.0.0.1:8787/mcp'
    got = rt(plain, "bolt://neo4j:proj@127.0.0.1:7687/proj")
    check("C7 precision: a URL path AFTER the host that only looks like the password (`/proj`) is not a secret — "
          "an unrelated path in the text is left intact", got == plain, repr(got))
    once = rt(f"at {_URI_PW} and bolt://a:b-f1@h:1 (x)", _URI_PW)
    check("C7 masking is idempotent (a masked message masked again is unchanged)",
          isinstance(once, str) and rt(once, _URI_PW) == once and _PW not in once and "b-f1" not in once, repr(once))

    # C8: nothing section C captured ever held a secret
    hits = sorted({s for s in _SECRETS for text in _CAPTURED if s in text})
    check(f"C8 across all {len(_CAPTURED)} captured outputs, no secret was ever printed", not hits, hits)


# =========================================================================== D: a refused instance.json write is named
_IS_HOLDER = r"""
import sys
sys.path.insert(0, sys.argv[1])
import instance_state
with instance_state.instance_lock(sys.argv[2], wait_s=0) as p:
    print("LOCKED" if p is not None else "BUSY", flush=True)
    if p is not None:
        sys.stdin.read()                 # hold instance.json's write lock until the gate closes stdin
"""
_NOT_AN_OBJECT = b'["a torn or hand-edited instance.json", 1]\n'
_UNWRITABLE_OLD = "not a writable project dir"          # the one wording every refused write used to get


def gate_d_instance_write_failures(C, tree: Path, base: Path, home: Path) -> None:
    print("\n-- D: a refused .deepmap/instance.json write names its cause (it always read 'not a writable project "
          "dir') --")
    build, env = tree / "build", _child_env(home)
    IS = _load(build / "instance_state.py", f"f1_is_{time.time_ns()}")
    IS.LOCK_WAIT_S = 0.4                 # the writers' bounded wait for the lock (5 s in the product), shortened here
    over = {"instance_state": IS}
    import instance_state as sys_is      # the copy tune_weights imports — its wait is shortened for the tune cases
    wp = getattr(IS, "write_problem", None)
    check("instance_state.write_problem exists (why a write was refused, read without writing)", callable(wp))

    def problem(root):
        try:
            return wp(root) if callable(wp) else "<missing>"
        except Exception as e:  # noqa: BLE001
            return f"<raised {type(e).__name__}>"

    bad = base / "d_not_object"                     # an instance.json that is not a JSON object: refused, never wiped
    (bad / ".deepmap").mkdir(parents=True)
    inst_bad = bad / ".deepmap" / "instance.json"
    inst_bad.write_bytes(_NOT_AN_OBJECT)
    busy = base / "d_busy"                          # another process holds instance.json's write lock
    (busy / ".deepmap").mkdir(parents=True)
    (busy / ".deepmap" / "instance.json").write_text('{"keep": 1}', encoding="utf-8")
    nodir = base / "d_file"                         # .deepmap is a regular file: nothing can be written there
    nodir.mkdir()
    (nodir / ".deepmap").write_text("a file where the .deepmap directory belongs", encoding="utf-8")
    fine = base / "d_ok"
    fine.mkdir()

    check("write_problem: a file that is not a JSON object -> 'unparseable' (the file left byte-identical)",
          problem(bad) == "unparseable" and inst_bad.read_bytes() == _NOT_AN_OBJECT, problem(bad))
    check("write_problem: .deepmap is a file -> 'unwritable'", problem(nodir) == "unwritable", problem(nodir))
    check("write_problem: a healthy project -> None (nothing stands in the way)", problem(fine) is None, problem(fine))

    def learning(proj, flag):
        with _env(DEEPMAP_LEARNING=None), _chdir(proj):
            return _main(C, ["learning", flag], overrides=over)

    for flag in ("--enable", "--disable"):
        rc, out, err, raised = learning(bad, flag)
        text = out + err
        check(f"D learning {flag}, instance.json not a JSON object: exit 1, it says so and to fix or delete the file — "
              f"not '{_UNWRITABLE_OLD}', not 'set DEEPMAP_LEARNING=1'",
              raised is None and rc == 1 and "not a JSON object" in text and "fix or delete" in text
              and _UNWRITABLE_OLD not in text and "DEEPMAP_LEARNING=1" not in text, f"rc={rc} {raised!r} {text[-500:]!r}")
        check(f"D learning {flag}: the file is left byte-identical (refused, never wiped)",
              inst_bad.read_bytes() == _NOT_AN_OBJECT)
    rc, out, err = _cli(tree, ["learning", "--enable"], env, bad)
    check("D the real entry point (a subprocess): learning --enable on that file -> exit 1 and the same named cause",
          rc == 1 and "not a JSON object" in out + err and _UNWRITABLE_OLD not in out + err, f"rc={rc} {(out + err)[-400:]!r}")

    with _sync_holding_lock(build, busy, env, script=_IS_HOLDER) as state:
        check("D setup: a child process (another DeepMap writer) holds instance.json's write lock", state == "LOCKED",
              state)
        check("write_problem: the write lock held by another process -> 'locked'", problem(busy) == "locked",
              problem(busy))
        rc, out, err, raised = learning(busy, "--enable")
        text = out + err
        check("D learning --enable while another DeepMap process holds the write lock: exit 1, 'another DeepMap "
              f"process' + 'retry' — not '{_UNWRITABLE_OLD}'",
              raised is None and rc == 1 and "another DeepMap process" in text and "retry" in text
              and _UNWRITABLE_OLD not in text, f"rc={rc} {raised!r} {text[-500:]!r}")
        saved_wait = sys_is.LOCK_WAIT_S
        sys_is.LOCK_WAIT_S = 0.4
        try:
            with _env(DEEPMAP_LEARNING=None), contextlib.redirect_stdout(io.StringIO()) as o, \
                    contextlib.redirect_stderr(io.StringIO()):
                rc = C.cmd_tune(_tune_args(busy))
        finally:
            sys_is.LOCK_WAIT_S = saved_wait
        ln = next((x for x in o.getvalue().splitlines() if "instance.json" in x), "")
        check("D tune (a real run) while the lock is held: its write line says another DeepMap process is writing it "
              "— retry; not 'non-repo or read-only'", rc == 0 and "another DeepMap process" in ln and "retry" in ln
              and "non-repo or read-only" not in ln, f"rc={rc} {ln!r}")
    check("D the other writer's file is untouched", (busy / ".deepmap" / "instance.json").read_text(encoding="utf-8")
          == '{"keep": 1}')

    rc, out, err, raised = learning(nodir, "--enable")
    text = out + err
    check("D control: learning --enable with .deepmap a file (truly unwritable) keeps its wording — exit 1, not "
          "writable, and the env var offered (it works there)", raised is None and rc == 1
          and ("not writable" in text or "not a writable" in text) and "DEEPMAP_LEARNING=1" in text,
          f"rc={rc} {raised!r} {text[-400:]!r}")

    with _env(DEEPMAP_LEARNING=None), contextlib.redirect_stdout(io.StringIO()) as o, \
            contextlib.redirect_stderr(io.StringIO()):
        rc = C.cmd_tune(_tune_args(bad))
    ln = next((x for x in o.getvalue().splitlines() if "instance.json" in x), "")
    check("D tune (a real run), instance.json not a JSON object: the write line names that and says fix or delete it — "
          "not 'non-repo or read-only'; exit 0 (the result above stands)",
          rc == 0 and "not a JSON object" in ln and "fix or delete" in ln and "non-repo or read-only" not in ln,
          f"rc={rc} {ln!r}")
    check("D tune: the file is left byte-identical", inst_bad.read_bytes() == _NOT_AN_OBJECT)

    rc, out, err, raised = learning(fine, "--enable")
    check("D control: a writable project — learning --enable still writes the flag (exit 0)",
          raised is None and rc == 0 and "Learning ENABLED" in out, f"rc={rc} {raised!r} {out[-300:]!r}")
    with _env(DEEPMAP_LEARNING=None), contextlib.redirect_stdout(io.StringIO()) as o, \
            contextlib.redirect_stderr(io.StringIO()):
        rc = C.cmd_tune(_tune_args(fine))
    check("D control: tune on a writable project still writes instance.json ('wrote:')",
          rc == 0 and "  wrote: " in o.getvalue(), o.getvalue()[-300:])


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
    root = REPO
    if "--root" in sys.argv:
        root = Path(sys.argv[sys.argv.index("--root") + 1]).resolve()
    print("=" * 72)
    print(f"DeepMap — F1 CLI follow-ups: enrich's refusal exit, tune's help, a NEO4J_URI's password, a refused "
          f"instance.json write (root: {root})")
    print("=" * 72)
    saved_env = {k: os.environ.get(k) for k in _SCRUB + ("DEEPMAP_HOME",)}
    for k in _SCRUB:
        os.environ.pop(k, None)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        base = Path(td).resolve()
        home, work = base / "home", base / "work"
        home.mkdir()
        work.mkdir()
        os.environ["DEEPMAP_HOME"] = str(home)
        try:
            sys.path.insert(0, str(root / "build"))
            C = _load(root / "build" / "deepmap_cli.py", f"f1_cli_{time.time_ns()}")
            for gate in (lambda: gate_a_enrich_refusal(C, root, work, home),
                         lambda: gate_b_tune_help(C, root, work),
                         lambda: gate_c_uri_redaction(C, root, work),
                         lambda: gate_d_instance_write_failures(C, root, work, home)):
                try:
                    gate()
                except Exception as exc:  # noqa: BLE001 — a crashing gate is a RED gate
                    traceback.print_exc()
                    check(f"gate ran without crashing ({type(exc).__name__}: {exc})", False)
        finally:
            for k, v in saved_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
    total = len(PASSES) + len(FAILS)
    print("\n" + "=" * 72)
    if FAILS:
        print(f"RESULT: RED — {len(PASSES)}/{total} gates passed.")
        return 1
    print(f"RESULT: GREEN — {len(PASSES)}/{total} gates passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
