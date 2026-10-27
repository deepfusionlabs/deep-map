#!/usr/bin/env python3
"""O9 r283 — `deepmap enrich`: a re-homed paid summary survives, enumeration holds sync's lock, honest docstring.

DeepInit refresh 2026-09-29 (validated against 37230dd85), owner O9 (build/enrich.py):
  D24  enrich() re-homes a moved-but-unchanged definition's entry (same file, name and span hash; another start line) onto
       the node's current id whenever the entry already there is absent, stale (another hash) or unusable (another model,
       or no hash), and counts the node already_done. _compact_sidecar wrote the relocation only when that id was ABSENT:
       a stale/unusable entry there was kept and the moved PAID summary dropped, so sync no longer applied it and the next
       run re-billed the node — against the module's "never re-billed" promise. The relocation now replaces whatever the
       target held, in either sidecar line order; a FRESH usable entry at the target is left alone (none is recorded).
  shared-state pass  enumeration re-runs sync._rebuild_code — a full graphify extraction into the per-root
       <DEEPMAP_HOME>/gout/<key>/ (graph.json deleted first) — WITHOUT sync's lock, so an enrich racing a sync of the same
       root could clobber that sync's engine output. Enumeration now takes sync's own per-root single-writer OS lock
       (<root>/.deepmap/sync.lock, non-blocking) around the extraction: a sync holding it makes enrich REFUSE at once — one
       clear stderr line, `refused` in the stats and `last_refusal`, nothing extracted, sent, spent or written, no
       traceback (the CLI exits normally); the lock is released as soon as extraction ends (also when it raises); a sync
       that queued a re-run behind enrich's lock is named (enrich itself never syncs); an unopenable lock refuses as well;
       a missing root is not created; .deepmap/ gets sync's self-ignoring .gitignore when the lock creates it.
  D25  the module docstring said build/retrieval.py is byte-frozen (false since v0.28.0 — only its ranking functions are
       hash-locked) and that the non-Python heuristic span reader is off by default (it is ON since P1-2).

Hermetic on every OS: temp projects + a temp DEEPMAP_HOME; `_rebuild_code` is stubbed with graphify-shaped nodes over real
files; the "running sync" is a CHILD process holding sync._acquire_sync_lock on the temp root (the real OS lock — msvcrt /
flock — across processes); the LLM transport is a scripted fake; the end-to-end CLI checks run `deepmap_cli.py enrich`
with DEEPMAP_EXTRACTOR=ast (no graphify subprocess, no Neo4j, no network, no spend). Never touches ~/.deepmap.
Usage: python tests/o9_enrich_rehome_lock_r283_gates.py [--root <tree>]
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PASSES: list[str] = []
FAILS: list[str] = []

M1 = "claude-sonnet-5"
PAID = "PAID: charges the card after validating it."
_PAD = "".join(f"# pad {i}\n" for i in range(1, 14)) + "\n"                 # lines 1..13, a blank line 14
_SRC = _PAD + "def charge(card, amount):\n    validate(card)\n    return create(amount)\n"   # def on line 15
NEW_LINE, OLD_LINE = 15, 10


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSES if ok else FAILS).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok or not detail else f" — {str(detail)[:700]}"))


def _load(root: Path):
    """A FRESH enrich module from `root`/build (each gate stubs its own _rebuild_code)."""
    build = root / "build"
    if str(build) not in sys.path:
        sys.path.insert(0, str(build))
    spec = importlib.util.spec_from_file_location(f"_o9_enrich_{time.time_ns()}", build / "enrich.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _node() -> dict:
    """The graphify-shaped node the stubbed extraction returns: `charge`, now on line 15 of m.py."""
    return dict(key="g_charge", label="Function", name="charge", source_file="m.py", start_line=NEW_LINE,
                documented=False, body_summary="charge: function")


def _mkproj(base: Path, name: str) -> Path:
    root = base / name
    root.mkdir()
    (root / "m.py").write_text(_SRC, encoding="utf-8")
    return root


class _Stub:
    """A graphify-shaped `_rebuild_code`: records each call, runs `during(root)` while 'extracting', may raise."""

    def __init__(self, during=None, raises=None, nodes=None):
        self.calls: list = []
        self.during, self.raises = during, raises
        self.nodes = nodes if nodes is not None else (lambda r: [_node()])

    def __call__(self, r):
        self.calls.append(str(r))
        if self.during is not None:
            self.during(Path(r))
        if self.raises is not None:
            raise self.raises
        return (self.nodes(Path(r)), [], [], "graphify")


class _T:
    """A scripted `claude -p` transport: answers every unit, records every call (a call = a bill)."""

    def __init__(self):
        self.calls: list = []

    def __call__(self, prompt):
        self.calls.append(prompt)
        n = prompt.count("### Unit ")
        return {"text": json.dumps({str(i): f"Freshly billed summary {i}." for i in range(1, n + 1)}),
                "cost_usd": 0.01, "is_error": False}


# ---- child processes: the "running sync" (the real OS lock, taken from ANOTHER process) --------------------------------
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
_PROBE = r"""
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import sync
h = sync._acquire_sync_lock(Path(sys.argv[2]))
print("LOCKED" if h is not None else "BUSY", flush=True)
sync._release_sync_lock(h)
"""
_QUEUE = r"""
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import sync
print("QUEUED" if sync._request_rerun(Path(sys.argv[2]), cleanup_stale=False, force_prune=False) else "FAILED",
      flush=True)
"""


def _child_env(home: Path) -> dict:
    env = dict(os.environ, DEEPMAP_HOME=str(home), PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1",
               PYTHONIOENCODING="utf-8")
    env.pop("DEEPMAP_ENRICH", None)
    return env


def _run_child(script: str, build: Path, root: Path, env: dict) -> str:
    r = subprocess.run([sys.executable, "-c", script, str(build), str(root)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env, timeout=120)
    out = (r.stdout or "").strip()
    return out or f"rc={r.returncode} stderr={(r.stderr or '')[-400:]}"


@contextlib.contextmanager
def _sync_holding_lock(build: Path, root: Path, env: dict):
    """A child process holding `root`'s sync lock for the duration of the block; yields its first line
    ('LOCKED' when it holds it). Released by closing its stdin; killed (it is our own child) only on a timeout."""
    proc = subprocess.Popen([sys.executable, "-c", _HOLDER, str(build), str(root)], stdin=subprocess.PIPE,
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


def _enrich_capturing(E, root, **kw):
    """(stats, stderr text, exception, seconds) of one E.enrich(root, **kw)."""
    buf, st, err = io.StringIO(), None, None
    t0 = time.monotonic()
    try:
        with contextlib.redirect_stderr(buf):
            st = E.enrich(root, **kw)
    except Exception as exc:  # noqa: BLE001 — a raise is what the gate looks for
        err = exc
    return st, buf.getvalue(), err, time.monotonic() - t0


# ---- D24 ---------------------------------------------------------------------------------------------------------------
def gate_rehome(tree: Path, base: Path) -> None:
    print("\n-- D24: a re-homed paid summary REPLACES a stale/unusable entry at its new id (never dropped, never re-billed) --")
    targets = [
        ("a STALE entry (an older body's span hash)", dict(hash="0" * 16, model=M1, summary="STALE older body.")),
        ("an entry of ANOTHER model (unusable for this run)",
         dict(hash=None, model="claude-haiku-4-5", summary="Other-model summary.")),
        ("an entry with NO hash (unusable)", dict(hash="", model=M1, summary="Hashless summary.")),
    ]
    n = 0
    for label, tgt in targets:
        for order in ("the target's line first", "the moved line first"):
            n += 1
            E = _load(tree)
            proj = _mkproj(base, f"d24_{n}")
            new_id, old_id = E.node_id("m.py", "charge", NEW_LINE), E.node_id("m.py", "charge", OLD_LINE)
            h1 = E.content_hash(E.compute_span(proj, _node(), {})[0])
            target = E.make_entry(new_id, h1 if tgt["hash"] is None else tgt["hash"], tgt["summary"],
                                  model=tgt["model"], kind="Function")
            moved = E.make_entry(old_id, h1, PAID, model=M1, kind="Function")
            E.append_entries(proj, [target, moved] if order.startswith("the target") else [moved, target])
            E._rebuild_code = _Stub()
            t1 = _T()
            st = E.enrich(proj, model=M1, claude_transport=t1)
            after = E.load_sidecar(proj)
            got = after.get(new_id) or {}
            check(f"{label} at the new id, {order}: the moved PAID summary now sits there (hash current, no orphan "
                  f"left at the old id)",
                  got.get("summary") == PAID and got.get("hash") == h1 and old_id not in after, json.dumps(after))
            nodes = [_node()]
            E.apply_sidecar(proj, nodes)
            check("... it cost no LLM call (already_done=1) and sync applies it",
                  not t1.calls and st.get("already_done") == 1 and nodes[0]["body_summary"] == PAID,
                  f"{len(t1.calls)} calls; stats={st}; applied={nodes[0]['body_summary']!r}")
            t2 = _T()
            st2 = E.enrich(proj, model=M1, claude_transport=t2)
            check("... and the NEXT run does not re-bill the node", not t2.calls and st2.get("already_done") == 1,
                  f"{len(t2.calls)} calls; stats={st2}")

    E = _load(tree)
    proj = _mkproj(base, "d24_det")
    new_id, old_id = E.node_id("m.py", "charge", NEW_LINE), E.node_id("m.py", "charge", OLD_LINE)
    h1 = E.content_hash(E.compute_span(proj, _node(), {})[0])
    E.append_entries(proj, [E.make_entry(new_id, "0" * 16, "DET stale.", model="deterministic", kind="Function",
                                         source="deterministic"),
                            E.make_entry(old_id, h1, "DET moved [kept].", model="deterministic", kind="Function",
                                         source="deterministic")])
    E._rebuild_code = _Stub()
    E.enrich(proj, deterministic=True)
    st2 = E.enrich(proj, deterministic=True)
    got = E.load_sidecar(proj).get(new_id) or {}
    check("--deterministic: the moved summary replaces the stale one too, and a re-run regenerates nothing",
          got.get("summary") == "DET moved [kept]." and st2.get("enriched") == 0 and st2.get("unchanged") == 0,
          f"{got}; stats={st2}")

    print("\n-- D24 controls: what must NOT change --")
    E = _load(tree)
    proj = _mkproj(base, "d24_fresh")
    new_id, old_id = E.node_id("m.py", "charge", NEW_LINE), E.node_id("m.py", "charge", OLD_LINE)
    h1 = E.content_hash(E.compute_span(proj, _node(), {})[0])
    E.append_entries(proj, [E.make_entry(old_id, h1, "Older copy.", model=M1, kind="Function"),
                            E.make_entry(new_id, h1, "FRESH in place.", model=M1, kind="Function")])
    E._rebuild_code = _Stub()
    t = _T()
    E.enrich(proj, model=M1, claude_transport=t)
    check("a FRESH usable entry at the node's id is kept as is (nothing is re-homed over it) and costs no call",
          (E.load_sidecar(proj).get(new_id) or {}).get("summary") == "FRESH in place." and not t.calls,
          json.dumps(E.load_sidecar(proj)))
    E = _load(tree)
    proj = _mkproj(base, "d24_empty")
    new_id, old_id = E.node_id("m.py", "charge", NEW_LINE), E.node_id("m.py", "charge", OLD_LINE)
    h1 = E.content_hash(E.compute_span(proj, _node(), {})[0])
    E.append_entries(proj, [E.make_entry(old_id, h1, PAID, model=M1, kind="Function")])
    E._rebuild_code = _Stub()
    t = _T()
    E.enrich(proj, model=M1, claude_transport=t)
    after = E.load_sidecar(proj)
    check("a pure line shift onto an EMPTY id still re-homes, with no call (as before)",
          (after.get(new_id) or {}).get("summary") == PAID and old_id not in after and not t.calls, json.dumps(after))


# ---- shared-state pass: the enumeration lock ---------------------------------------------------------------------------
def gate_lock_refusal(tree: Path, base: Path, home: Path) -> None:
    print("\n-- a sync of the project holds its lock -> enrich refuses at once (nothing extracted, sent, spent, written) --")
    build, env = tree / "build", _child_env(home)
    E = _load(tree)
    proj = _mkproj(base, "lock_busy")
    E.append_entries(proj, [E.make_entry(E.node_id("m.py", "charge", 3), "f" * 16, "Existing entry.", model=M1,
                                         kind="Function")])
    before = E.sidecar_path(proj).read_bytes()
    with _sync_holding_lock(build, proj, env) as state:
        check("setup: a child process (the running sync) holds the project's sync lock", state == "LOCKED", state)
        for mode, kw in (("LLM tier", {"model": M1}), ("--deterministic", {"deterministic": True})):
            stub, t = _Stub(), _T()
            E._rebuild_code = stub
            st, msg, err, dt = _enrich_capturing(E, proj, claude_transport=t, **kw)
            check(f"{mode}: enrich returns without raising, at once (no wait on the lock)", err is None and dt < 20,
                  f"{err!r}; {dt:.1f}s")
            check(f"{mode}: nothing is extracted (the extraction never ran) and no LLM call is made",
                  not stub.calls and not t.calls, f"extractions={len(stub.calls)} llm_calls={len(t.calls)}")
            check(f"{mode}: the result says why — stats['refused'] names the running sync",
                  isinstance(st, dict) and "sync" in str(st.get("refused") or "").lower(), str(st))
            check(f"{mode}: one clear stderr line says it REFUSED because a sync of the project is running",
                  "refused" in msg.lower() and "sync" in msg.lower() and "Traceback" not in msg, msg)
            check(f"{mode}: the sidecar is byte-for-byte untouched", E.sidecar_path(proj).read_bytes() == before)
        stub = _Stub()
        E._rebuild_code = stub
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            nodes = E.iter_enrichable_nodes(proj)
        check("--dry-run (iter_enrichable_nodes): refuses the same way — [] returned, nothing extracted, the reason on "
              "stderr", nodes == [] and not stub.calls and "refused" in buf.getvalue().lower(),
              f"nodes={len(nodes)} extractions={len(stub.calls)} stderr={buf.getvalue()!r}")
        check("... and last_refusal carries the reason for callers of the list API",
              "sync" in str(getattr(E, "last_refusal", "") or "").lower(), repr(getattr(E, "last_refusal", None)))
    stub, t = _Stub(), _T()
    E._rebuild_code = stub
    st, _msg, err, _dt = _enrich_capturing(E, proj, model=M1, claude_transport=t)
    check("control: once the sync has finished, the same enrich runs normally (extracts once, no refusal, the node "
          "then has its summary)",
          err is None and len(stub.calls) == 1 and isinstance(st, dict) and not st.get("refused")
          and E.node_id("m.py", "charge", NEW_LINE) in E.load_sidecar(proj),
          f"{err!r}; extractions={len(stub.calls)}; stats={st}")


def gate_lock_scope(tree: Path, base: Path, home: Path) -> None:
    print("\n-- the lock is held WHILE enrich extracts, and released right after (also when extraction raises) --")
    build, env = tree / "build", _child_env(home)
    E = _load(tree)
    proj = _mkproj(base, "lock_scope")
    seen: dict = {}
    E._rebuild_code = _Stub(during=lambda r: seen.__setitem__("probe", _run_child(_PROBE, build, r, env)))
    E.enrich(proj, model=M1, claude_transport=_T())
    check("while enrich extracts, a sync of the same root finds the lock HELD (it queues instead of racing)",
          seen.get("probe") == "BUSY", seen)
    check("after enrich returns, the lock is free again", _run_child(_PROBE, build, proj, env) == "LOCKED")
    E._rebuild_code = _Stub(raises=RuntimeError("engine exploded"))
    raised = None
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            E.enrich(proj, model=M1, claude_transport=_T())
    except RuntimeError as exc:
        raised = exc
    check("control: an extraction error still propagates as before", isinstance(raised, RuntimeError), repr(raised))
    check("... and the lock is released on that path too", _run_child(_PROBE, build, proj, env) == "LOCKED")

    print("\n-- a sync that queued a re-run behind enrich's lock is named (enrich itself never syncs) --")
    E = _load(tree)
    proj = _mkproj(base, "lock_rerun")
    E._rebuild_code = _Stub(during=lambda r: seen.__setitem__("queued", _run_child(_QUEUE, build, r, env)))
    _st, msg, _err, _dt = _enrich_capturing(E, proj, model=M1, claude_transport=_T())
    rerun = proj / ".deepmap" / "sync.rerun"
    check("setup: the sync queued its re-run while enrich held the lock", seen.get("queued") == "QUEUED", seen)
    check("enrich says a sync was deferred behind it and to run /deep-map:refresh",
          "/deep-map:refresh" in msg and "re-run" in msg, msg)
    check("... and leaves the request queued for the next sync (never consumes it)", rerun.is_file())
    E = _load(tree)
    proj = _mkproj(base, "lock_rerun_before")
    _run_child(_QUEUE, build, proj, env)
    E._rebuild_code = _Stub()
    _st, msg, _err, _dt = _enrich_capturing(E, proj, model=M1, claude_transport=_T())
    check("control: a re-run queued BEFORE enrich started is not reported as deferred by enrich",
          "/deep-map:refresh" not in msg, msg)

    print("\n-- the edges: an unopenable lock refuses; a missing root is not created; .deepmap/ stays out of git --")
    E = _load(tree)
    proj = _mkproj(base, "lock_unopenable")
    (proj / ".deepmap").write_text("a FILE where the .deepmap/ directory belongs", encoding="utf-8")
    stub, t = _Stub(), _T()
    E._rebuild_code = stub
    st, msg, err, _dt = _enrich_capturing(E, proj, model=M1, claude_transport=t)
    check("a lock that cannot be opened: enrich refuses without raising, extracts nothing, calls no LLM",
          err is None and not stub.calls and not t.calls and isinstance(st, dict) and st.get("refused")
          and "refused" in msg.lower(), f"{err!r}; extractions={len(stub.calls)}; stats={st}; stderr={msg!r}")
    E = _load(tree)
    missing = base / "no_such_project"
    E._rebuild_code = _Stub(nodes=lambda r: [])
    st, _msg, err, _dt = _enrich_capturing(E, missing, model=M1, claude_transport=_T())
    check("a missing root is never created (taking the lock must not conjure <root>/.deepmap/), and nothing raises",
          err is None and not missing.exists(), f"{err!r}; exists={missing.exists()}")
    for opt_out in (False, True):
        E = _load(tree)
        proj = _mkproj(base, f"lock_gitignore_{int(opt_out)}")
        E._rebuild_code = _Stub()
        saved = os.environ.get("DEEPMAP_AUTO_GITIGNORE")
        if opt_out:
            os.environ["DEEPMAP_AUTO_GITIGNORE"] = "0"
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                E.iter_enrichable_nodes(proj)
        finally:
            if saved is None:
                os.environ.pop("DEEPMAP_AUTO_GITIGNORE", None)
            else:
                os.environ["DEEPMAP_AUTO_GITIGNORE"] = saved
        dm, gi = proj / ".deepmap", proj / ".deepmap" / ".gitignore"
        if not opt_out:
            body = gi.read_text(encoding="utf-8") if gi.is_file() else ""
            check("when the lock creates .deepmap/, it gets sync's self-ignoring .gitignore (the lock file never "
                  "shows in git status)", not dm.exists() or (body.startswith("# DeepMap") and body.splitlines()[-1] == "*"),
                  repr(body))
        else:
            check("... and DEEPMAP_AUTO_GITIGNORE=0 is honoured (no .gitignore written)", not gi.exists())


def gate_cli(tree: Path, base: Path, home: Path) -> None:
    print("\n-- end to end: `deepmap_cli.py enrich` under a running sync prints the refusal, no traceback, writes nothing --")
    build, env = tree / "build", _child_env(home)
    cli = build / "deepmap_cli.py"
    proj = base / "cli_proj"
    proj.mkdir()
    (proj / "a.py").write_text("def charge(card):\n    # WHY: settles the payment first\n    return create(card)\n",
                               encoding="utf-8")
    cenv = dict(env, DEEPMAP_EXTRACTOR="ast")
    side = proj / ".deepmap" / "summaries.jsonl"

    def run(*args):
        return subprocess.run([sys.executable, str(cli), "enrich", str(proj), *args], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", env=cenv, timeout=300)
    with _sync_holding_lock(build, proj, env) as state:
        check("setup: the running sync holds the project's lock", state == "LOCKED", state)
        r = run("--deterministic")
        check("CLI --deterministic: the refusal is printed, no traceback, nothing written",
              "Traceback" not in r.stderr and "refused" in r.stderr.lower() and "sync" in r.stderr.lower()
              and not side.exists(), f"rc={r.returncode}\nstdout={r.stdout[-500:]}\nstderr={r.stderr[-700:]}")
        r = run("--dry-run")
        check("CLI --dry-run: refuses the same way (no extraction), no traceback",
              "Traceback" not in r.stderr and "refused" in r.stderr.lower(),
              f"rc={r.returncode}\nstdout={r.stdout[-500:]}\nstderr={r.stderr[-700:]}")
    r = run("--deterministic")
    check("control: after the sync finished, the same CLI run enriches (exit 0, sidecar written, no refusal)",
          r.returncode == 0 and side.exists() and "refused" not in r.stderr.lower(),
          f"rc={r.returncode}\nstdout={r.stdout[-500:]}\nstderr={r.stderr[-700:]}")


# ---- D25 ---------------------------------------------------------------------------------------------------------------
def gate_docstring(tree: Path) -> None:
    print("\n-- D25: the module docstring tells the truth --")
    E = _load(tree)
    doc = " ".join((E.__doc__ or "").split())
    check("it no longer says build/retrieval.py is byte-frozen (only its ranking functions are hash-locked)",
          "byte-frozen" not in doc, doc[-700:])
    check("it no longer calls the non-Python heuristic span reader 'off by default'",
          "span reader, off by default" not in doc, doc[-700:])
    check("it says that heuristic span is ON by default",
          re.search(r"heuristic.{0,240}?\bON by default", doc) is not None, doc[-700:])
    check("control: it still says build/retrieval.py never reads any of this",
          re.search(r"build/retrieval\.py[^.]{0,80}never reads any of this", doc) is not None, doc[-700:])


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    tree = REPO
    if "--root" in sys.argv:
        tree = Path(sys.argv[sys.argv.index("--root") + 1]).resolve()
    print("=" * 72)
    print(f"DeepMap — enrich: re-homed summaries survive, enumeration holds sync's lock, honest docs (root: {tree})")
    print("=" * 72)
    saved = {k: os.environ.get(k) for k in ("DEEPMAP_HOME", "DEEPMAP_ENRICH", "DEEPMAP_AUTO_GITIGNORE")}
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        base = Path(td).resolve()
        home, work = base / "home", base / "work"
        home.mkdir()
        work.mkdir()
        os.environ["DEEPMAP_HOME"] = str(home)
        os.environ.pop("DEEPMAP_ENRICH", None)
        os.environ.pop("DEEPMAP_AUTO_GITIGNORE", None)
        try:
            for gate in (lambda: gate_rehome(tree, work), lambda: gate_lock_refusal(tree, work, home),
                         lambda: gate_lock_scope(tree, work, home), lambda: gate_cli(tree, work, home),
                         lambda: gate_docstring(tree)):
                try:
                    gate()
                except Exception as exc:  # noqa: BLE001 — a crashing gate is a RED gate, not a crash
                    traceback.print_exc()
                    check(f"gate ran without crashing ({type(exc).__name__}: {exc})", False)
        finally:
            for k, v in saved.items():
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
