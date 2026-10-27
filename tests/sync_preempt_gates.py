#!/usr/bin/env python3
"""auto-sync sync-core — a sync the user starts (/deep-map:refresh, /deep-map:setup, deepmap enrich) preempts
DeepMap's background refresh and runs in its OWN process, with its own environment, flags and verdict (H1).

  1  a manual sync() vs an in-process background holder (a thread holding the REAL lock with a live
     origin:"unattended" record): a preempt record is written, the holder lets go, the manual pass runs here with
     cleanup_stale=True and the preempt record is gone; PREEMPT_START / PREEMPT_ACQUIRED on stderr.
  2  the holder never stops (PREEMPT_WAIT_S patched to 1 s): True, last_not_pushed "busy: DeepMap's background
     refresh ...", last_outcome preempt-timeout, no sync_last.json from the waiter, the preempt record removed.
  3  any other holder — a manual one, no record at all — today's exact queue message, no preempt record, no wait;
     a background holder that turns out to be a user sync while it waits (another user sync took the lock first)
     -> today's queue path.
  4  UnattendedControl: checkpoint semantics (live / dead / expired preempts); the hard exit (a recorder) only in
     start/extract, never in write/embed/manifest, only while this process holds the lock; sync_last (preempted,
     rc 0) written and sync_running cleared BEFORE on_hard_exit; a 200-iteration thread race never yields both
     "phase write" and a hard exit.
  5  a request a user's sync queued (or an older DeepMap): the background refresh never runs it and never claims
     it — the file stays byte for byte; with no user's sync waiting it is NOT a preemption: outcome
     user-request-pending, rc 4, a truthful stderr line, the same on every run (review r1: it was recorded
     "preempted" rc 0 forever while the agent was told a user's sync was running); with a user's sync waiting it
     is a real hand-over (preempted rc 0); a request that turns into a user's between the look and the claim is put
     back with its own ts; after a synced pass of its own it stops and keeps that pass's record; after any yield
     no further pass runs.
  6  the write checkpoint inside a real pass: a waiting user sync -> nothing pushed, outcome preempted; the
     pre_write veto (git-busy, a raising check) -> nothing pushed, "vetoed: ..."; before_embed False -> the
     embeddings postponed, manifest embeddings_pending, sync_last embeddings_deferred.
  7  enrich: a background holder -> enrich waits, then runs (its own holder record: origin enrich, phase
     enrich-extract, removed afterwards); a manual holder -> today's refusal text exactly; a background holder that
     never stops -> ENRICH_PREEMPT_TIMEOUT, nothing extracted.
  8  the background refresh misses the lock: behind enrich -> busy-enrich, no request; behind a user sync -> a
     request with origin unattended, cleanup_stale false, prune_policy evidence.
  9  a claimed request of origin unattended that asks for the full prune, cleanup_stale and force_prune (a
     hand-edited or foreign-version file) runs capped: evidence, cleanup_stale False, force_prune False.
  10 a yield only to `deepmap enrich` (every live preempt by "enrich") is not a hand-over to a user's sync: outcome
     busy-enrich, rc 4, preempted_by ["enrich"], ENRICH_YIELD — at the start checkpoint, at the write checkpoint
     (nothing pushed) and in the watcher's hard exit (on_hard_exit gets that record); a user's sync among them ->
     preempted rc 0 (yield_fields).
  11 an unattended sync never adopts a graph with no recorded owner: refused (refusal_kind unowned, rc 1) before
     anything is extracted or written; a sync the user starts still adopts it.

Hermetic: temp projects, a temp DEEPMAP_HOME, in-process holder threads on the REAL OS lock, every driver a fake,
no network, never ~/.deepmap. Nothing is killed: the "hard exit" is a recorder.
Usage: python tests/sync_preempt_gates.py [--root <tree>]   (a --root without build/ is a usage error, exit 2)
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import random
import shutil
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parent.parent
PASSES: list = []
FAILS: list = []

# today's texts (v0.29.0), as literals
QUEUE_MSG = ("[deepmap sync] another sync of this project, or a deepmap enrich run, is in progress — queued a re-run: "
             "a running sync performs it before it finishes, so changes made after it started are included; deepmap "
             "enrich never syncs, so if enrich holds the lock, run /deep-map:refresh once it finishes")
ENRICH_REFUSAL = ("REFUSED — a sync (or another enrich) of this project is running: it holds {lock}. enrich re-runs "
                  "the same extraction and would overwrite that run's engine output, so nothing was extracted, sent, "
                  "spent or written. Re-run it once that run finishes.")


def check(name: str, ok: bool, detail="") -> None:
    (PASSES if ok else FAILS).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok or not detail else f" — {str(detail)[:700]}"))


def _root_arg(argv: list) -> Path:
    if "--root" not in argv:
        return REPO
    i = argv.index("--root")
    tree = argv[i + 1] if i + 1 < len(argv) else ""
    if not tree or not (Path(tree) / "build").is_dir():
        print(f"usage: python tests/sync_preempt_gates.py [--root <tree holding build/>] (got {tree!r})",
              file=sys.stderr)
        raise SystemExit(2)
    return Path(tree).resolve()


@contextlib.contextmanager
def _env(**kv):
    saved = {k: os.environ.get(k) for k in kv}
    try:
        for k, v in kv.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = str(v)
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@contextlib.contextmanager
def _patched(obj, **attrs):
    missing = object()
    saved = {k: getattr(obj, k, missing) for k in attrs}
    try:
        for k, v in attrs.items():
            setattr(obj, k, v)
        yield
    finally:
        for k, v in saved.items():
            if v is missing:
                try:
                    delattr(obj, k)
                except AttributeError:
                    pass
            else:
                setattr(obj, k, v)


def _read(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _captured(fn, *a, **k):
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf), contextlib.redirect_stdout(buf):
        out = fn(*a, **k)
    return out, buf.getvalue()


def _preempt_files(root: Path) -> list:
    d = root / ".deepmap"
    return sorted(p.name for p in d.glob("sync.preempt.*.json")) if d.is_dir() else []


class _Holder:
    """A thread holding the project's REAL sync lock with a holder record of `origin` (this process: live)."""

    def __init__(self, S, ST, root: Path, *, origin="unattended", release_on_preempt=True, max_s=30.0,
                 on_preempt=None, phase="extract"):
        self.S, self.ST, self.root = S, ST, root
        self.origin, self.release_on_preempt, self.max_s = origin, release_on_preempt, max_s
        self.on_preempt, self.phase = on_preempt, phase
        self.ready, self.released, self.stop = threading.Event(), threading.Event(), threading.Event()
        self.saw = []
        self.got = None
        self.t = threading.Thread(target=self._run, daemon=True)

    def __enter__(self):
        self.t.start()
        self.ready.wait(10)
        return self

    def __exit__(self, *a):
        self.stop.set()
        self.released.wait(10)
        return False

    def _run(self):
        h = self.S._acquire_sync_lock(self.root)
        self.got = h is not None
        now = time.time()
        self.ST.write_running(self.root, {"id": f"holder-{os.getpid()}", "origin": self.origin,
                                          "trigger": "session-start" if self.origin == "unattended" else None,
                                          "session": None, "started_utc": "2026-10-22T10:00:00Z",
                                          "started_epoch": now - 300, "pass": 1, "phase": self.phase,
                                          "head_at_start": None, "source_fingerprint": None})
        self.ready.set()
        t0 = time.monotonic()
        try:
            while time.monotonic() - t0 < self.max_s and not self.stop.is_set():
                live = self.ST.live_preempts(self.root)
                if live:
                    if not self.saw:
                        self.saw = live
                        if self.on_preempt is not None:
                            self.on_preempt(self)
                    if self.release_on_preempt:
                        break
                time.sleep(0.05)
        finally:
            self.ST.clear_running(self.root)
            self.S._release_sync_lock(h)
            self.released.set()


def _project(base: Path, name: str) -> Path:
    root = base / name
    root.mkdir(parents=True)
    (root / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (root / ".deepmap-root").write_text("", encoding="utf-8")
    return root


# =====================================================================================================
# 1 / 2 / 3 — a user's sync vs the holder
# =====================================================================================================
def gate_manual_preempts(S, ST, base: Path) -> None:
    print("\n-- 1: a manual sync preempts the background refresh and runs here, with its own flags --")
    root = _project(base, "p1")
    calls = []
    with _patched(S, _sync_pass=lambda r, uri, **k: calls.append(dict(k)) or True), \
            _Holder(S, ST, root, origin="unattended") as h:
        ok, out = _captured(S.sync, root, cleanup_stale=True)
    check("1: the holder saw a live preempt record written by this sync (by 'sync')",
          bool(h.saw) and h.saw[0].get("by") == "sync" and h.saw[0].get("pid") == os.getpid(), h.saw)
    check("1: the manual pass ran in this process, once, with cleanup_stale=True and the full prune",
          ok is True and len(calls) == 1 and calls[0].get("cleanup_stale") is True
          and calls[0].get("prune_policy") == "full" and calls[0].get("unattended") is None, (ok, calls))
    check("1: PREEMPT_START (started 5 min ago; reading the code) and PREEMPT_ACQUIRED on stderr",
          "DeepMap's background refresh of this project is running (started 5 min ago; reading the code)" in out
          and "waiting at most 15 min" in out and "the background refresh stopped — syncing now." in out, out)
    check("1: the preempt record is removed; last_not_pushed None", not _preempt_files(root)
          and S.last_not_pushed is None, (_preempt_files(root), S.last_not_pushed))
    last = _read(root / ".deepmap" / "sync_last.json") or {}
    check("1: the pass that ran is recorded as the user's (origin manual)", last.get("origin") == "manual", last)

    print("\n-- 2: the background refresh never stops -> nothing synced, exit 4 --")
    root = _project(base, "p2")
    calls.clear()
    t0 = time.monotonic()
    with _patched(ST, PREEMPT_WAIT_S=1.0), _patched(S, _sync_pass=lambda r, uri, **k: calls.append(k) or True), \
            _Holder(S, ST, root, origin="unattended", release_on_preempt=False, max_s=20) as h:
        ok, out = _captured(S.sync, root, cleanup_stale=True)
        files_during = _preempt_files(root)
    took = time.monotonic() - t0
    lnp = S.last_not_pushed or ""
    check("2: returns True after ~1 s, no pass ran", ok is True and not calls and took < 15, (ok, calls, took))
    check("2: last_not_pushed names the background refresh (the CLI prints NOT PUSHED and exits 4)",
          lnp.startswith("busy: DeepMap's background refresh of this project did not stop within")
          and "reading the code" in lnp and "run it again once that refresh finishes" in lnp, lnp)
    check("2: last_outcome preempt-timeout", isinstance(S.last_outcome, dict)
          and S.last_outcome.get("outcome") == "preempt-timeout", S.last_outcome)
    check("2: no sync_last.json from the waiter; the preempt record removed",
          not (root / ".deepmap" / "sync_last.json").exists() and not files_during, files_during)

    print("\n-- 3: any other holder -> today's queue path, byte for byte, no preempt --")
    seen_preempt = []
    stop = threading.Event()

    def watch(r):
        while not stop.is_set():
            if _preempt_files(r):
                seen_preempt.append(_preempt_files(r))
            time.sleep(0.01)

    root = _project(base, "p3a")
    w = threading.Thread(target=watch, args=(root,), daemon=True)
    w.start()
    t0 = time.monotonic()
    with _Holder(S, ST, root, origin="manual", release_on_preempt=False, max_s=20):
        ok, out = _captured(S.sync, root, cleanup_stale=True)
    stop.set()
    w.join(2)
    took = time.monotonic() - t0
    req = _read(root / ".deepmap" / "sync.rerun") or {}
    check("3: a manual holder: returns True, today's exact queue message, no preempt record ever written",
          ok is True and out.strip() == QUEUE_MSG and not seen_preempt and S.last_not_pushed is None,
          (ok, out, seen_preempt))
    check("3: ... queued as a manual request (cleanup_stale + full), without waiting", req.get("origin") == "manual"
          and req.get("cleanup_stale") is True and req.get("prune_policy") == "full" and took < 10, (req, took))
    root = _project(base, "p3b")
    held = S._acquire_sync_lock(root)                         # a holder with NO record (an older DeepMap)
    t0 = time.monotonic()
    try:
        with _patched(ST, HOLDER_SETTLE_S=0.2):
            ok, out = _captured(S.sync, root)
    finally:
        S._release_sync_lock(held)
    took = time.monotonic() - t0
    check("3: no holder record: today's exact queue message, no preempt record, the settle wait only",
          ok is True and out.strip() == QUEUE_MSG and not _preempt_files(root) and took < 5, (out, took))
    # a background holder whose record turns into a user sync's while we wait -> the queue path
    root = _project(base, "p3c")

    def became_manual(h):
        h.ST.update_running(h.root, origin="manual", trigger=None)

    t0 = time.monotonic()
    with _Holder(S, ST, root, origin="unattended", release_on_preempt=False, max_s=20,
                 on_preempt=became_manual):
        ok, out = _captured(S.sync, root)
    took = time.monotonic() - t0
    check("3: a background holder that becomes a user sync while we wait -> today's queue path (queued), no long wait",
          ok is True and QUEUE_MSG in out and "DeepMap's background refresh of this project is running" in out
          and (root / ".deepmap" / "sync.rerun").exists() and not _preempt_files(root) and took < 10, (out, took))


# =====================================================================================================
# 4 — UnattendedControl
# =====================================================================================================
def _own_running(ST, root: Path, phase="extract"):
    (root / ".deepmap").mkdir(parents=True, exist_ok=True)
    ST.write_running(root, {"id": f"t-{os.getpid()}", "origin": "unattended", "trigger": "session-start",
                            "started_utc": "2026-10-22T10:00:00Z", "started_epoch": time.time() - 5, "pass": 1,
                            "phase": phase, "head_at_start": "c" * 40, "source_fingerprint": "d" * 32})


def _other_start_stamp(own):
    """A start stamp in `own`'s OWN format that names another process start: on Linux port_registry writes
    "linux:<boot id>:<start ticks>" (comparable only with another such stamp — a float there is "cannot tell", never
    "gone"), elsewhere a float epoch compared within a 2 s tolerance."""
    if isinstance(own, str) and own.startswith("linux:"):
        head, _, ticks = own.rpartition(":")
        return f"{head}:{int(ticks) + 7}"
    return float(own) + 1000.0


def _dead_preempt(ST, root: Path, stamp) -> Path:
    p = root / ".deepmap" / "sync.preempt.999999.json"
    p.write_text(json.dumps({"schema": 1, "pid": os.getpid(), "pid_created": stamp, "by": "sync",
                             "ts_epoch": time.time()}), encoding="utf-8")
    return p


def gate_control(S, ST, base: Path) -> None:
    print("\n-- 4: UnattendedControl — checkpoints, the hard exit, the phase lock --")
    root = base / "c1"
    _own_running(ST, root)
    ctl = ST.UnattendedControl(root, trigger="session-start", poll_s=60)
    check("4: no preempt -> checkpoint returns None and sets the phase",
          ctl.checkpoint(to_phase="write") is None and ctl.phase == "write"
          and (_read(root / ".deepmap" / "sync_running.json") or {}).get("phase") == "write")
    ctl = ST.UnattendedControl(root, trigger="session-start", poll_s=60)
    ctl.enter("extract")
    pf = ST.write_preempt(root, by="sync")
    check("4: a live preempt -> 'preempted', the phase stays 'extract'",
          ctl.checkpoint(to_phase="write") == "preempted" and ctl.phase == "extract"
          and ctl.yielded() == "preempted")
    ST.clear_preempt(pf)
    check("4: once yielded, every later checkpoint says 'preempted'", ctl.checkpoint(to_phase="write") == "preempted")
    # a dead writer's preempt is not live and is deleted; an expired one is not live. The dead record's stamp is
    # built in THIS platform's native format (a float never compares with Linux's "linux:<boot>:<ticks>").
    own = ST.process_identity().get("pid_created")
    check("4: this process has a readable start stamp", own is not None, own)
    dead = _dead_preempt(ST, root, _other_start_stamp(own) if own is not None else 12.5)
    ctl = ST.UnattendedControl(root, trigger="session-start", poll_s=60)
    check("4: a preempt whose writer is gone (start stamp differs) is not live and is deleted",
          ctl.checkpoint(to_phase="write") is None and not dead.exists())
    # both stamp formats on every OS: this process's identity read as a Linux stamp, then as a float epoch (the
    # Windows / macOS form). A differing stamp of the same format is "gone" (deleted); a stamp of the other format
    # cannot be compared — not live, and KEPT (an unproven death never deletes another process's record).
    real_identity = ST.process_identity
    for label, mine in (("linux", "linux:b0709402-0000-4000-8000-000000000000:2127569"), ("float", 1761130000.25)):
        other_format = 12.5 if label == "linux" else "linux:b0709402-0000-4000-8000-000000000000:2127569"
        ST.process_identity = lambda mine=mine: {"pid": os.getpid(), "pid_created": mine}
        try:
            dead = _dead_preempt(ST, root, _other_start_stamp(mine))
            gone = ST.holder_live(_read(dead)), ST.live_preempts(root), dead.exists()
            same = ST.holder_live({"pid": os.getpid(), "pid_created": mine})
            dead = _dead_preempt(ST, root, other_format)
            unproven = ST.holder_live(_read(dead)), ST.live_preempts(root), dead.exists()
            dead.unlink()
        finally:
            ST.process_identity = real_identity
        check(f"4: {label} stamps: a differing stamp is a gone writer (deleted), the same stamp is live",
              gone == (False, [], False) and same is True, (gone, same))
        check(f"4: {label} stamps: a stamp of the other format is not live and the record is kept",
              unproven == (None, [], True), unproven)
    pf = ST.write_preempt(root, by="sync")
    check("4: a preempt older than PREEMPT_WAIT_S + 120 s is not live",
          ST.live_preempts(root, now=time.time() + ST.PREEMPT_WAIT_S + 200) == [] and len(ST.live_preempts(root)) == 1)
    ST.clear_preempt(pf)
    # the hard exit (recorder), start and extract phases
    for phase in ("extract", "start"):
        r = base / f"hard_{phase}"
        _own_running(ST, r, phase=phase)
        exits, snaps = [], []

        def on_hard(rec, r=r):
            snaps.append((_read(r / ".deepmap" / "sync_last.json"), (r / ".deepmap" / "sync_running.json").exists(),
                          dict(rec)))

        ctl = ST.UnattendedControl(r, trigger="session-start", poll_s=0.02, hard_exit=exits.append,
                                   on_hard_exit=on_hard)
        ctl.begin()
        if phase == "extract":
            ctl.enter("extract")
        pf = ST.write_preempt(r, by="sync")
        t0 = time.monotonic()
        while not exits and time.monotonic() - t0 < 5:
            time.sleep(0.01)
        ctl.end()
        ST.clear_preempt(pf)
        last, running_left, rec = snaps[0] if snaps else (None, None, None)
        check(f"4: phase {phase}: a waiting user sync -> hard_exit(0), once", exits == [0], exits)
        check(f"4: phase {phase}: sync_last (preempted, rc 0, origin unattended) written and sync_running cleared "
              f"BEFORE on_hard_exit; it gets that record",
              isinstance(last, dict) and last.get("outcome") == "preempted" and last.get("rc") == 0
              and last.get("origin") == "unattended" and running_left is False and isinstance(rec, dict)
              and rec.get("outcome") == "preempted", snaps)
    for phase in ("write", "embed", "manifest"):
        r = base / f"nohard_{phase}"
        _own_running(ST, r, phase=phase)
        exits = []
        ctl = ST.UnattendedControl(r, trigger="session-start", poll_s=0.02, hard_exit=exits.append)
        ctl.enter(phase)
        ctl.begin()
        pf = ST.write_preempt(r, by="sync")
        time.sleep(0.4)
        ctl.end()
        ST.clear_preempt(pf)
        check(f"4: phase {phase}: never a hard exit (Neo4j may be mid-write)", exits == [] and ctl.yielded() is None,
              exits)
    r = base / "not_holder"
    (r / ".deepmap").mkdir(parents=True)
    ST.write_running(r, {"pid": 4, "pid_created": 1.0, "origin": "unattended", "phase": "extract"})
    exits = []
    ctl = ST.UnattendedControl(r, trigger="session-start", poll_s=0.02, hard_exit=exits.append)
    ctl.enter("extract")
    ctl.begin()
    pf = ST.write_preempt(r, by="sync")
    time.sleep(0.4)
    ctl.end()
    ST.clear_preempt(pf)
    check("4: a process whose own holder record is not there never hard-exits", exits == [], exits)
    # the race: the watcher and checkpoint(to_phase="write") under one phase lock
    rng = random.Random(7)
    both, hard_n, write_n = [], 0, 0
    for i in range(200):
        r = base / "race"
        _own_running(ST, r)
        exits = []
        ctl = ST.UnattendedControl(r, trigger="session-start", poll_s=0.0005, hard_exit=exits.append)
        ctl.enter("extract")
        ctl.begin()
        d1, d2 = rng.random() * 0.004, rng.random() * 0.012
        holder = {}

        def preempter():
            time.sleep(d1)
            holder["pf"] = ST.write_preempt(r, by="sync")

        t = threading.Thread(target=preempter)
        t.start()
        time.sleep(d2)
        got = ctl.checkpoint(to_phase="write")
        t.join()
        time.sleep(0.01)
        ctl.end()
        ST.clear_preempt(holder.get("pf"))
        if exits and ctl.phase == "write":
            both.append(i)
        if exits and got is None:
            both.append((i, "checkpoint passed after a hard exit"))
        hard_n += bool(exits)
        write_n += ctl.phase == "write"
    check(f"4: 200-iteration race: never both 'phase write' and a hard exit ({hard_n} hard exits, {write_n} writes)",
          not both and hard_n > 0 and write_n > 0, both[:5])


# =====================================================================================================
# 5 / 6 — the background refresh's own passes
# =====================================================================================================
class _FakeVerdict:
    READY, DEGRADED, FAILED = "READY", "DEGRADED", "FAILED"

    def extension_census(self, root):
        return {}

    def build_coverage(self, root, nodes, census=None):
        return {}

    def classify(self, **k):
        return "READY", []

    def ml_stack_ok(self):
        return True

    def format_block(self, v, rs, **k):
        return f"[deepmap] verdict: {v}"

    def note_engine_nodes(self, root, nodes):
        return None


class _Drv:
    def verify_connectivity(self):
        return None

    def close(self):
        return None

    def session(self):
        raise AssertionError("an unstubbed query reached the fake driver")


@contextlib.contextmanager
def _pipeline(S, *, pushes, embeds, rebuild=None):
    node = {"key": "m.py::f:1", "label": "Function", "name": "f", "source_file": "m.py", "start_line": 1,
            "body_summary": "f"}

    def _rebuild(root):
        if rebuild is not None:
            rebuild(root)
        return S._Extraction([node], [], [], "graphify", {"requested": "auto", "resolved": "graphify"})

    # the graph is THIS project's (what a background refresh normally finds): its owner is the identity _sync_pass
    # computes for the root just before it reads the owner (an unowned graph is gate 11's case)
    ident = {}
    real_identity = S._project_identity

    def _identity(root):
        ident["v"] = real_identity(root)
        return ident["v"]

    attrs = dict(
        _rebuild_code=_rebuild, _apply_recognizers=lambda root, n: (n, []), _project_identity=_identity,
        _read_owning_root=lambda d: ident.get("v"), _graph_has_nodes=lambda d: True, _graph_holds_nodes=lambda d: True,
        _write_owning_root=lambda d, ident: None, _record_base_store_owner=lambda *a, **k: None,
        _remove_stale_short_names=lambda d: 0, _fts_rebuild_pending=lambda d: False, _fts_changed_docs=lambda d, n: 0,
        _mark_fts_rebuild=lambda d, p: None, push_to_neo4j=lambda d, n, c, i, t=None: pushes.append(1) or (1, 0),
        _reuse_rekeyed_embeddings=lambda d, n: 0, _rebuild_code_fts=lambda d: True,
        _count_embedded_nodes=lambda d, fallback=0: 1, _graph_node_count=lambda d: 1, _maybe_auto_tune=lambda r: None,
        _unkey_receiver_calls=lambda root, n, c: (c, 0), _attach_bare_calls=lambda root, n, c: 0,
        _attach_local_defs=lambda root, n, c: 0, _attach_local_def_calls=lambda root, n, c: 0,
        indexes=SimpleNamespace(create_all_indexes=lambda d: {}, wait_for_indexes=lambda d, timeout=120: True,
                                VECTOR_INDEXES=(), vector_quantization_disabled=lambda: False),
        embeddings=SimpleNamespace(generate_embeddings=lambda d: embeds.append(1) or 1))
    saved = sys.modules.get("verdict")
    sys.modules["verdict"] = _FakeVerdict()
    try:
        with _patched(S, **attrs):
            yield
    finally:
        if saved is None:
            sys.modules.pop("verdict", None)
        else:
            sys.modules["verdict"] = saved


def gate_unattended_passes(S, ST, base: Path) -> None:
    print("\n-- 5: the background refresh never runs (nor claims) a request a user's sync queued --")
    root = _project(base, "u5")
    S._request_rerun(root, cleanup_stale=True, force_prune=False)          # queued by a user's sync
    rq = root / ".deepmap" / "sync.rerun"
    before_bytes = rq.read_bytes()
    before = _read(rq) or {}
    calls = []
    for run_no in (1, 2):                                                    # a later session start: the same
        ctl = ST.UnattendedControl(root, trigger="session-start", poll_s=60)
        with _patched(S, _sync_pass=lambda r, uri, **k: calls.append(k) or True):
            ok, out = _captured(S.sync, root, prune_policy="evidence", unattended=ctl, driver=_Drv())
        last = _read(root / ".deepmap" / "sync_last.json") or {}
        lo = S.last_outcome if isinstance(S.last_outcome, dict) else {}
        check(f"5 (run {run_no}): no pass ran; the user's request is still queued BYTE FOR BYTE (never claimed: its "
              f"ts, pid and flags as that sync wrote them)",
              ok is True and not calls and rq.is_file() and rq.read_bytes() == before_bytes
              and before.get("origin") == "manual" and before.get("prune_policy") == "full",
              (calls, rq.read_bytes() if rq.is_file() else None, before_bytes))
        check(f"5 (run {run_no}): no user's sync waits -> NOT a preemption: outcome user-request-pending, rc 4, "
              f"last_outcome the same record, last_not_pushed set, nothing yielded",
              ctl.yielded() is None and last.get("outcome") == "user-request-pending" and last.get("rc") == 4
              and last.get("origin") == "unattended" and lo.get("outcome") == "user-request-pending"
              and bool(S.last_not_pushed), (ctl.yielded(), last, S.last_not_pushed))
        check(f"5 (run {run_no}): stderr says why (it never runs a user's request) and never claims a user's sync "
              f"is waiting",
              "never runs it (it carries that sync's own settings and prune)" in out
              and "/deep-map:refresh runs it" in out and "a sync started by the user (or setup / enrich) is waiting"
              not in out, out)
        check(f"5 (run {run_no}): the record's reason says a sync the user starts runs it",
              "a sync the user starts runs it" in str(last.get("reason")), last.get("reason"))
    # the same request while a user's sync IS waiting for the lock: a real hand-over
    root = _project(base, "u5w")
    S._request_rerun(root, cleanup_stale=True, force_prune=False)
    rq = root / ".deepmap" / "sync.rerun"
    before_bytes = rq.read_bytes()
    calls.clear()
    pf = ST.write_preempt(root, by="sync")
    ctl = ST.UnattendedControl(root, trigger="session-start", poll_s=60)
    try:
        with _patched(S, _sync_pass=lambda r, uri, **k: calls.append(k) or True):
            ok, out = _captured(S.sync, root, prune_policy="evidence", unattended=ctl, driver=_Drv())
    finally:
        ST.clear_preempt(pf)
    last = _read(root / ".deepmap" / "sync_last.json") or {}
    check("5: a user's request + a user's sync waiting -> preempted rc 0 (it runs the request), the request untouched",
          ok is True and not calls and ctl.yielded() == "preempted" and last.get("outcome") == "preempted"
          and last.get("rc") == 0 and rq.read_bytes() == before_bytes
          and "this background refresh stops here (before reading the code)" in out, (last, out))
    # a request that becomes a user's between the look and the claim: put back with its own ts, not run
    root = _project(base, "u5r")
    rq = root / ".deepmap" / "sync.rerun"
    rq.parent.mkdir(parents=True, exist_ok=True)
    rq.write_text(json.dumps({"ts": 1700000000.5, "pid": 4242, "cleanup_stale": True, "force_prune": False,
                              "prune_policy": "full", "origin": "manual", "trigger": None}), encoding="utf-8")
    calls.clear()
    peeks = []
    ctl = ST.UnattendedControl(root, trigger="session-start", poll_s=60)
    with _patched(S, _sync_pass=lambda r, uri, **k: calls.append(k) or True,
                  _peek_rerun_request=lambda r: peeks.append(str(r)) and None):   # the look saw no request
        ok, out = _captured(S.sync, root, prune_policy="evidence", unattended=ctl, driver=_Drv())
    after = _read(rq) or {}
    last = _read(root / ".deepmap" / "sync_last.json") or {}
    check("5: a request that turned into a user's after the look: not run, put back as it was (its own ts, manual, "
          "cleanup_stale, full), outcome user-request-pending",
          ok is True and not calls and bool(peeks) and after.get("ts") == 1700000000.5
          and after.get("origin") == "manual" and after.get("cleanup_stale") is True
          and after.get("prune_policy") == "full" and last.get("outcome") == "user-request-pending",
          (peeks, after, last))
    # a pass of its own synced, then a user's sync queued a request meanwhile: stop, keep the synced pass's record
    root = _project(base, "u5p")
    calls.clear()
    ctl = ST.UnattendedControl(root, trigger="session-start", poll_s=60)

    def pass_then_user_request(r, uri, **k):
        calls.append(k)
        if len(calls) == 1:
            S._request_rerun(r, cleanup_stale=True, force_prune=False)     # a user's sync, meanwhile
        return True

    with _patched(S, _sync_pass=pass_then_user_request):
        ok, out = _captured(S.sync, root, prune_policy="evidence", unattended=ctl, driver=_Drv())
    last = _read(root / ".deepmap" / "sync_last.json") or {}
    lo = S.last_outcome if isinstance(S.last_outcome, dict) else {}
    req = _read(root / ".deepmap" / "sync.rerun") or {}
    check("5: a synced pass, then a user's request queued meanwhile -> no second pass; sync_last and last_outcome "
          "stay that pass's 'synced' record (the agent is told what it synced)",
          ok is True and len(calls) == 1 and last.get("outcome") == "synced" and lo.get("outcome") == "synced"
          and lo.get("id") == last.get("id") and req.get("origin") == "manual", (len(calls), last, lo, req))
    check("5: ... and says it left the request for a sync the user starts",
          "leaves it for a sync the user starts (/deep-map:refresh)" in out, out)
    root = _project(base, "u5b")
    S._request_rerun(root, cleanup_stale=False, force_prune=False, prune_policy="evidence", origin="unattended",
                     trigger="session-start")
    calls.clear()
    ctl = ST.UnattendedControl(root, trigger="session-start", poll_s=60)
    with _patched(S, _sync_pass=lambda r, uri, **k: calls.append(k) or True):
        ok, out = _captured(S.sync, root, prune_policy="evidence", unattended=ctl, driver=_Drv())
    check("5: its own (unattended) request is claimed and run, with the evidence prune",
          len(calls) == 1 and calls[0].get("prune_policy") == "evidence" and calls[0].get("cleanup_stale") is False
          and not (root / ".deepmap" / "sync.rerun").exists(), calls)
    # after a yield inside a pass: no further pass even with a request queued
    root = _project(base, "u5c")
    calls.clear()
    ctl = ST.UnattendedControl(root, trigger="session-start", poll_s=60)

    def yielding_pass(r, uri, **k):
        calls.append(k)
        k["unattended"].mark_yielded("write")
        k["pass_info"]["yielded"] = True
        S._request_rerun(r, cleanup_stale=False, force_prune=False, prune_policy="evidence", origin="unattended")
        return True

    with _patched(S, _sync_pass=yielding_pass):
        ok, out = _captured(S.sync, root, prune_policy="evidence", unattended=ctl, driver=_Drv())
    check("5: after a yield no further pass runs (the queued request stays)",
          len(calls) == 1 and (root / ".deepmap" / "sync.rerun").exists(), (len(calls),))

    print("\n-- 6: the write checkpoint inside a real pass --")
    root = _project(base, "u6a")
    pushes, embeds = [], []
    ctl = ST.UnattendedControl(root, trigger="session-start", poll_s=60)
    pf_holder = {}
    with _pipeline(S, pushes=pushes, embeds=embeds,
                   rebuild=lambda r: pf_holder.setdefault("pf", ST.write_preempt(r, by="sync"))):
        ok, out = _captured(S.sync, root, prune_policy="evidence", unattended=ctl, driver=_Drv())
    ST.clear_preempt(pf_holder.get("pf"))
    last = _read(root / ".deepmap" / "sync_last.json") or {}
    check("6: a user sync waiting at the write checkpoint -> nothing pushed, no manifest, outcome preempted rc 0",
          ok is True and not pushes and not (root / ".deepmap" / "graph_build.json").exists()
          and last.get("outcome") == "preempted" and last.get("rc") == 0 and S.last_not_pushed is None,
          (pushes, last, out[-300:]))
    check("6: ... said 'before writing the graph'", "stops here (before writing the graph)" in out, out[-300:])
    for label, pre_write, want_veto in (("git-busy", lambda e, x: "git-busy", "git-busy"),
                                        ("a raising check", lambda e, x: (_ for _ in ()).throw(RuntimeError("x")),
                                         "pre-write-error: RuntimeError")):
        root = _project(base, f"u6v_{label.split()[0]}")
        pushes, embeds, seen = [], [], []

        def pw(engine, extraction, f=pre_write):
            seen.append((engine, dict(extraction)))
            return f(engine, extraction)

        ctl = ST.UnattendedControl(root, trigger="session-start", poll_s=60, pre_write=pw)
        with _pipeline(S, pushes=pushes, embeds=embeds):
            ok, out = _captured(S.sync, root, prune_policy="evidence", unattended=ctl, driver=_Drv())
        last = _read(root / ".deepmap" / "sync_last.json") or {}
        check(f"6: pre_write veto ({label}) -> nothing pushed, last_not_pushed 'vetoed: {want_veto}', rc 4",
              ok is True and not pushes and S.last_not_pushed == f"vetoed: {want_veto}"
              and last.get("not_pushed_kind") == "veto" and last.get("veto") == want_veto and last.get("rc") == 4
              and last.get("outcome") == "not-pushed", (pushes, S.last_not_pushed, last))
        check(f"6: pre_write ({label}) got (engine, extraction info)", seen and seen[0][0] == "graphify"
              and seen[0][1].get("requested") == "auto", seen)
    root = _project(base, "u6e")
    pushes, embeds = [], []
    ctl = ST.UnattendedControl(root, trigger="session-start", poll_s=60, before_embed=lambda: False)
    with _pipeline(S, pushes=pushes, embeds=embeds):
        ok, out = _captured(S.sync, root, prune_policy="evidence", unattended=ctl, driver=_Drv())
    m = _read(root / ".deepmap" / "graph_build.json") or {}
    last = _read(root / ".deepmap" / "sync_last.json") or {}
    check("6: before_embed False -> pushed, embeddings NOT computed, manifest embeddings_pending, "
          "sync_last embeddings_deferred, rc 0",
          ok is True and pushes and not embeds and m.get("embeddings_pending") is True
          and last.get("embeddings_deferred") is True and last.get("rc") == 0 and last.get("outcome") == "synced",
          (pushes, embeds, m.get("embeddings_pending"), last))
    root = _project(base, "u6f")
    pushes, embeds = [], []
    ctl = ST.UnattendedControl(root, trigger="session-start", poll_s=60, before_embed=lambda: True)
    with _pipeline(S, pushes=pushes, embeds=embeds):
        ok, out = _captured(S.sync, root, prune_policy="evidence", unattended=ctl, driver=_Drv())
    m = _read(root / ".deepmap" / "graph_build.json") or {}
    check("6: before_embed True -> embeddings computed, no embeddings_pending",
          embeds and "embeddings_pending" not in m, (embeds, sorted(m)))


# =====================================================================================================
# 7 — enrich
# =====================================================================================================
def gate_enrich(S, ST, E, base: Path) -> None:
    print("\n-- 7: enrich vs the background refresh --")
    root = _project(base, "e1")
    seen = []

    def rebuild(r):
        seen.append(_read(Path(r) / ".deepmap" / "sync_running.json"))
        return ([], [], [], "graphify")

    with _patched(E, _rebuild_code=rebuild), _Holder(S, ST, root, origin="unattended") as h:
        _items, out = _captured(E.iter_enrichable_nodes, root)
    rr = seen[0] if seen else None
    check("7: a background holder -> enrich asked it to stop (by 'enrich'), waited, then extracted",
          bool(h.saw) and h.saw[0].get("by") == "enrich" and bool(seen) and E.last_refusal is None,
          (h.saw, seen, E.last_refusal, out))
    check("7: ... said so: ENRICH_PREEMPT_START (nothing extracted, sent or spent meanwhile)",
          "waiting for DeepMap's background refresh of this project to stop at its next safe point" in out
          and "nothing is extracted, sent or spent meanwhile" in out, out)
    check("7: while enrich held the lock its holder record said origin enrich, phase enrich-extract",
          isinstance(rr, dict) and rr.get("origin") == "enrich" and rr.get("phase") == "enrich-extract"
          and rr.get("pid") == os.getpid(), rr)
    check("7: ... removed when it released; no preempt record left",
          not (root / ".deepmap" / "sync_running.json").exists() and not _preempt_files(root))
    root = _project(base, "e2")
    seen.clear()
    with _patched(E, _rebuild_code=rebuild), _Holder(S, ST, root, origin="manual", release_on_preempt=False,
                                                     max_s=20):
        _items, out = _captured(E.iter_enrichable_nodes, root)
    want = ENRICH_REFUSAL.format(lock=root / ".deepmap" / "sync.lock")
    check("7: a manual holder -> today's refusal text, exactly; nothing extracted; no preempt record",
          E.last_refusal == want and not seen and not _preempt_files(root), (E.last_refusal, want))
    root = _project(base, "e3")
    seen.clear()
    with _patched(ST, PREEMPT_WAIT_S=1.0), _patched(E, _rebuild_code=rebuild), \
            _Holder(S, ST, root, origin="unattended", release_on_preempt=False, max_s=20):
        _items, out = _captured(E.iter_enrichable_nodes, root)
    check("7: a background holder that never stops -> ENRICH_PREEMPT_TIMEOUT refusal, nothing extracted",
          str(E.last_refusal or "").startswith("REFUSED — DeepMap's background refresh of this project did not stop "
                                               "within") and "Nothing was extracted, sent, spent or written"
          in str(E.last_refusal) and not seen and not _preempt_files(root), (E.last_refusal, seen))


# =====================================================================================================
# 8 — the background refresh misses the lock
# =====================================================================================================
def gate_unattended_miss(S, ST, base: Path) -> None:
    print("\n-- 8: the background refresh finds the lock held --")
    root = _project(base, "m1")
    calls = []
    ctl = ST.UnattendedControl(root, trigger="session-start", poll_s=60)
    with _patched(S, _sync_pass=lambda r, uri, **k: calls.append(k) or True), \
            _Holder(S, ST, root, origin="enrich", release_on_preempt=False, max_s=20):
        ok, out = _captured(S.sync, root, prune_policy="evidence", unattended=ctl, driver=_Drv())
    check("8: behind deepmap enrich -> True, last_outcome busy-enrich, no request queued, no pass, no preempt",
          ok is True and isinstance(S.last_outcome, dict) and S.last_outcome.get("outcome") == "busy-enrich"
          and bool(S.last_not_pushed) and not (root / ".deepmap" / "sync.rerun").exists() and not calls
          and not _preempt_files(root), (ok, S.last_outcome, out))
    root = _project(base, "m2")
    calls.clear()
    ctl = ST.UnattendedControl(root, trigger="session-start", poll_s=60)
    with _patched(S, _sync_pass=lambda r, uri, **k: calls.append(k) or True), \
            _Holder(S, ST, root, origin="manual", release_on_preempt=False, max_s=20):
        ok, out = _captured(S.sync, root, prune_policy="evidence", unattended=ctl, driver=_Drv())
    req = _read(root / ".deepmap" / "sync.rerun") or {}
    check("8: behind a user sync -> a request: origin unattended, cleanup_stale false, prune_policy evidence",
          {k: req.get(k) for k in ("origin", "cleanup_stale", "prune_policy", "trigger")} == {
              "origin": "unattended", "cleanup_stale": False, "prune_policy": "evidence", "trigger": "session-start"},
          req)
    check("8: ... returns True, last_outcome queued, the background wording, no preempt record",
          ok is True and S.last_outcome == {"outcome": "queued"} and "queued a background re-run" in out
          and not _preempt_files(root) and not calls, (S.last_outcome, out))


# =====================================================================================================
# 9 — a claimed request never lifts the background refresh's prune
# =====================================================================================================
def gate_request_cap(S, ST, base: Path) -> None:
    print("\n-- 9: a claimed unattended request asking for the full / forced prune runs capped --")
    root = _project(base, "q9")
    rq = root / ".deepmap" / "sync.rerun"
    rq.parent.mkdir(parents=True, exist_ok=True)
    rq.write_text(json.dumps({"ts": time.time(), "pid": 4242, "origin": "unattended", "prune_policy": "full",
                              "cleanup_stale": True, "force_prune": True, "trigger": "session-start"}),
                  encoding="utf-8")
    calls = []
    ctl = ST.UnattendedControl(root, trigger="session-start", poll_s=60)
    with _patched(S, _sync_pass=lambda r, uri, **k: calls.append(k) or True):
        ok, out = _captured(S.sync, root, prune_policy="evidence", unattended=ctl, driver=_Drv())
    got = {k: calls[0].get(k) for k in ("prune_policy", "cleanup_stale", "force_prune")} if calls else None
    check("9: {origin unattended, full, cleanup_stale, force_prune} -> the pass gets evidence / False / False",
          ok is True and len(calls) == 1 and got == {"prune_policy": "evidence", "cleanup_stale": False,
                                                     "force_prune": False} and not rq.exists(), (got, out[-300:]))
    last = _read(root / ".deepmap" / "sync_last.json") or {}
    check("9: ... and its record says evidence", last.get("prune_policy") == "evidence", last.get("prune_policy"))
    root = _project(base, "q9n")
    rq = root / ".deepmap" / "sync.rerun"
    rq.parent.mkdir(parents=True, exist_ok=True)
    rq.write_text(json.dumps({"ts": time.time(), "pid": 4242, "origin": "unattended", "prune_policy": "evidence",
                              "cleanup_stale": False, "force_prune": False, "trigger": "session-start"}),
                  encoding="utf-8")
    calls.clear()
    ctl = ST.UnattendedControl(root, trigger="session-start", poll_s=60, prune_policy="none")
    with _patched(S, _sync_pass=lambda r, uri, **k: calls.append(k) or True):
        ok, out = _captured(S.sync, root, prune_policy="none", unattended=ctl, driver=_Drv())
    check("9: an evidence request over a 'none' refresh still max-merges to evidence (H4 unchanged)",
          len(calls) == 1 and calls[0].get("prune_policy") == "evidence", calls)


# =====================================================================================================
# 10 — a yield to deepmap enrich only
# =====================================================================================================
def gate_enrich_yield(S, ST, base: Path) -> None:
    print("\n-- 10: a yield only to deepmap enrich is not a hand-over to a user's sync --")
    yf = getattr(ST, "yield_fields", None)
    check("10: sync_status.yield_fields exists", callable(yf))
    if callable(yf):
        a, b, c = yf(["enrich"], "extract"), yf(["enrich", "sync"], "write"), yf([], "start")
        check("10: yield_fields: only enrich -> busy-enrich rc 4; enrich + sync -> preempted rc 0; none known -> "
              "preempted rc 0",
              (a["outcome"], a["rc"], a["preempted_by"]) == ("busy-enrich", 4, ["enrich"])
              and (b["outcome"], b["rc"], b["preempted_by"]) == ("preempted", 0, ["enrich", "sync"])
              and (c["outcome"], c["rc"]) == ("preempted", 0) and "while reading the code" in a["reason"]
              and "before writing the graph" in b["log"], (a, b, c))
    # the start checkpoint
    root = _project(base, "y10a")
    calls = []
    pf = ST.write_preempt(root, by="enrich")
    ctl = ST.UnattendedControl(root, trigger="session-start", poll_s=60)
    try:
        with _patched(S, _sync_pass=lambda r, uri, **k: calls.append(k) or True):
            ok, out = _captured(S.sync, root, prune_policy="evidence", unattended=ctl, driver=_Drv())
    finally:
        ST.clear_preempt(pf)
    last = _read(root / ".deepmap" / "sync_last.json") or {}
    lo = S.last_outcome if isinstance(S.last_outcome, dict) else {}
    check("10: an enrich preempt at the start checkpoint -> no pass, outcome busy-enrich rc 4, preempted_by "
          "['enrich'], last_outcome the same, last_not_pushed set",
          ok is True and not calls and last.get("outcome") == "busy-enrich" and last.get("rc") == 4
          and last.get("preempted_by") == ["enrich"] and lo.get("outcome") == "busy-enrich"
          and bool(S.last_not_pushed), (last, S.last_not_pushed))
    check("10: ... stderr says enrich waits and does not refresh the graph (never 'a sync started by the user')",
          "a deepmap enrich run is waiting for this project's lock" in out and "enrich does not refresh the graph"
          in out and "a sync started by the user" not in out, out)
    # the write checkpoint
    root = _project(base, "y10b")
    pushes, embeds, holder = [], [], {}
    ctl = ST.UnattendedControl(root, trigger="session-start", poll_s=60)
    with _pipeline(S, pushes=pushes, embeds=embeds,
                   rebuild=lambda r: holder.setdefault("pf", ST.write_preempt(r, by="enrich"))):
        ok, out = _captured(S.sync, root, prune_policy="evidence", unattended=ctl, driver=_Drv())
    ST.clear_preempt(holder.get("pf"))
    last = _read(root / ".deepmap" / "sync_last.json") or {}
    check("10: an enrich preempt at the write checkpoint -> nothing pushed, no manifest, busy-enrich rc 4",
          ok is True and not pushes and not (root / ".deepmap" / "graph_build.json").exists()
          and last.get("outcome") == "busy-enrich" and last.get("rc") == 4 and last.get("preempted_by") == ["enrich"]
          and "before writing the graph" in out, (pushes, last, out[-300:]))
    # a user's sync at the write checkpoint: still the hand-over
    root = _project(base, "y10c")
    pushes, embeds, holder = [], [], {}
    ctl = ST.UnattendedControl(root, trigger="session-start", poll_s=60)
    with _pipeline(S, pushes=pushes, embeds=embeds,
                   rebuild=lambda r: holder.setdefault("pf", ST.write_preempt(r, by="sync"))):
        ok, out = _captured(S.sync, root, prune_policy="evidence", unattended=ctl, driver=_Drv())
    ST.clear_preempt(holder.get("pf"))
    last = _read(root / ".deepmap" / "sync_last.json") or {}
    check("10: a user's sync preempt at the write checkpoint -> preempted rc 0, preempted_by ['sync']",
          last.get("outcome") == "preempted" and last.get("rc") == 0 and last.get("preempted_by") == ["sync"], last)
    # the watcher's hard exit
    r = base / "y10_hard"
    _own_running(ST, r, phase="extract")
    exits, got = [], []
    ctl = ST.UnattendedControl(r, trigger="session-start", poll_s=0.02, hard_exit=exits.append,
                               on_hard_exit=lambda rec: got.append(dict(rec)))
    ctl.enter("extract")
    ctl.begin()
    pf = ST.write_preempt(r, by="enrich")
    t0 = time.monotonic()
    while not exits and time.monotonic() - t0 < 5:
        time.sleep(0.01)
    ctl.end()
    ST.clear_preempt(pf)
    last = _read(r / ".deepmap" / "sync_last.json") or {}
    check("10: the hard exit for an enrich-only preempt -> sync_last busy-enrich rc 4 (preempted_by ['enrich']), and "
          "on_hard_exit gets that record",
          exits == [0] and last.get("outcome") == "busy-enrich" and last.get("rc") == 4
          and last.get("preempted_by") == ["enrich"] and bool(got) and got[0].get("outcome") == "busy-enrich",
          (exits, last, got))


# =====================================================================================================
# 11 — an unowned graph
# =====================================================================================================
def gate_unowned(S, ST, base: Path) -> None:
    print("\n-- 11: the background refresh never adopts a graph with no recorded owner --")
    root = _project(base, "o11")
    pushes, embeds, owners, idx, rebuilt = [], [], [], [], []
    ctl = ST.UnattendedControl(root, trigger="session-start", poll_s=60)
    fake_idx = SimpleNamespace(create_all_indexes=lambda d: idx.append(1) or {},
                               wait_for_indexes=lambda d, timeout=120: True, VECTOR_INDEXES=(),
                               vector_quantization_disabled=lambda: False)
    with _pipeline(S, pushes=pushes, embeds=embeds, rebuild=lambda r: rebuilt.append(1)), \
            _patched(S, _write_owning_root=lambda d, ident: owners.append(ident), indexes=fake_idx,
                     _read_owning_root=lambda d: None, _graph_has_nodes=lambda d: True):
        ok, out = _captured(S.sync, root, prune_policy="evidence", unattended=ctl, driver=_Drv())
    last = _read(root / ".deepmap" / "sync_last.json") or {}
    check("11: unattended + an unowned, non-empty graph -> refused (False) before extracting: nothing extracted, "
          "pushed, indexed or stamped",
          ok is False and not rebuilt and not pushes and not idx and not owners and not embeds,
          (ok, rebuilt, pushes, idx, owners, out[-400:]))
    check("11: ... recorded: outcome refused, refusal_kind unowned, rc 1; the REFUSING line names the remedies",
          last.get("outcome") == "refused" and last.get("refusal_kind") == "unowned" and last.get("rc") == 1
          and "no recorded owner" in out and "/deep-map:refresh adopts it" in out, (last, out[-400:]))
    root = _project(base, "o11m")
    pushes, embeds, owners, idx, rebuilt = [], [], [], [], []
    with _pipeline(S, pushes=pushes, embeds=embeds, rebuild=lambda r: rebuilt.append(1)), \
            _patched(S, _write_owning_root=lambda d, ident: owners.append(ident), indexes=fake_idx,
                     _read_owning_root=lambda d: None, _graph_has_nodes=lambda d: True):
        ok, out = _captured(S.sync, root, driver=_Drv())
    check("11: a sync the user starts still adopts it (pushed, owner stamped, the 'adopting' line)",
          ok is True and rebuilt and pushes and owners and "adopting" in out, (ok, pushes, owners, out[-300:]))


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    tree = _root_arg(sys.argv[1:])
    print("=" * 72)
    print(f"DeepMap — auto-sync: a user's sync / setup / enrich preempts the background refresh (root: {tree})")
    print("=" * 72)
    sys.path.insert(0, str(tree / "build"))
    base = Path(tempfile.mkdtemp(prefix="dm_preempt_")).resolve()
    scrub = {k: None for k in os.environ if k.startswith("DEEPMAP_")}
    try:
        scrub.update(NEO4J_URI="bolt://127.0.0.1:9", DEEPMAP_HOME=str(base / "home"), DEEPMAP_AUTO_SYNC="0")
        with _env(**scrub):
            (base / "home").mkdir()
            import sync as S                         # noqa: E402 — from the tree under test
            import enrich as E                       # noqa: E402
            check("setup: sync and enrich were loaded from the tree under test",
                  Path(S.__file__).resolve().parent == (tree / "build").resolve()
                  and Path(E.__file__).resolve().parent == (tree / "build").resolve(), (S.__file__, E.__file__))
            try:
                import sync_status as ST             # noqa: E402
            except Exception as exc:  # noqa: BLE001
                ST = None
                check("setup: build/sync_status.py imports", False, f"{type(exc).__name__}: {exc}")
            if ST is not None:
                for gate, args in ((gate_manual_preempts, (S, ST, base)), (gate_control, (S, ST, base)),
                                   (gate_unattended_passes, (S, ST, base)), (gate_enrich, (S, ST, E, base)),
                                   (gate_unattended_miss, (S, ST, base)), (gate_request_cap, (S, ST, base)),
                                   (gate_enrich_yield, (S, ST, base)), (gate_unowned, (S, ST, base))):
                    try:
                        gate(*args)
                    except Exception as exc:  # noqa: BLE001 — a crash on the tree under test is a RED result
                        traceback.print_exc()
                        check(f"{gate.__name__} ran without raising", False, f"{type(exc).__name__}: {exc}")
    finally:
        shutil.rmtree(str(base), ignore_errors=True)   # exactly the temp dir this gate created
    total = len(PASSES) + len(FAILS)
    print("\n" + "=" * 72)
    if FAILS:
        print(f"RESULT: RED — {len(PASSES)}/{total} gates passed.")
        return 1
    print(f"RESULT: GREEN — {len(PASSES)}/{total} gates passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
