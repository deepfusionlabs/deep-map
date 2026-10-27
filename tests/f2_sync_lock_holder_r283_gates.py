#!/usr/bin/env python3
"""F2 r283 — a sync that finds this project's lock held names BOTH possible holders: another sync, or deepmap enrich.

Fix-all round 2, follow-up of O9 (round 1). `deepmap enrich` now takes sync's OWN per-root single-writer lock
(<root>/.deepmap/sync.lock — build/enrich.py _take_sync_lock -> sync._acquire_sync_lock) while it extracts. A sync that
starts meanwhile finds the lock held, queues a re-run (.deepmap/sync.rerun) and exits 0 — and it said "another sync of
this project is in progress — queued a re-run: it syncs again before it finishes, so changes made after it started are
included". With enrich holding the lock that promise is false: enrich never syncs, so the request waits for the NEXT
sync. sync cannot tell the two holders apart (the OS lock carries no holder it can read — on Windows the locked byte
cannot even be read), so every lock-held line now names both and says what each does with the request. The lock, the
queue, the retry and the exit contract are unchanged: the sync returns True, last_not_pushed stays None when the re-run
was queued and is set (the CLI's exit 4) when it could not be.

Hermetic on every OS: temp projects + a temp DEEPMAP_HOME; NEO4J_URI names a closed loopback port nothing dials (the
lock-held path returns before a driver exists; the holder's own pass is stubbed); the holders take the REAL OS lock
in-process — a running sync, and the real enrich enumeration (its extraction stubbed) — exactly as in production. Never
touches ~/.deepmap, never starts Neo4j or a server, no network.
Usage: python tests/f2_sync_lock_holder_r283_gates.py [--root <tree>]   (--root: load build/ from another tree —
       the RED-first run against the pre-fix snapshot)
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PASSES: list = []
FAILS: list = []

OLD_PROMISE = "queued a re-run: it syncs again before it finishes"   # the pre-fix, holder-blind wording


def check(name: str, ok: bool, detail="") -> None:
    (PASSES if ok else FAILS).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok or not detail else f" — {str(detail)[:700]}"))


def _root_arg(argv: list) -> Path:
    """The tree whose build/ is loaded. A --root without a value, or naming a tree without build/, is a usage error
    (exit 2) — never a silent fallback to this checkout (a RED-first run would then report GREEN)."""
    if "--root" not in argv:
        return REPO
    i = argv.index("--root")
    tree = argv[i + 1] if i + 1 < len(argv) else ""
    if not tree or not (Path(tree) / "build").is_dir():
        print(f"usage: python tests/f2_sync_lock_holder_r283_gates.py [--root <tree holding build/>] (got {tree!r})",
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


def _captured(fn, *a, **k):
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf), contextlib.redirect_stdout(buf):
        out = fn(*a, **k)
    return out, buf.getvalue()


def _names_both_holders(text: str) -> bool:
    low = (text or "").lower()
    return "another sync" in low and "deepmap enrich" in low


# =====================================================================================================
# 1 — the lock is held (stubbed): the skipped sync's lines; the lock / queue / exit behaviour is unchanged
# =====================================================================================================
def gate_stubbed(S, base: Path) -> None:
    print("\n-- the project's lock is held: what the skipped sync says; lock, queue and exit behaviour unchanged --")
    for queued in (True, False):
        root = base / f"stub_{'queued' if queued else 'unqueued'}"
        root.mkdir()
        tries, requests, passes = [], [], []
        saved = (S._acquire_sync_lock, S._request_rerun, S._sync_pass)
        try:
            S._acquire_sync_lock = lambda r: tries.append(Path(r)) and None
            S._request_rerun = lambda r, **k: requests.append(dict(k)) or queued
            S._sync_pass = lambda *a, **k: passes.append(1) or True
            ok, out = _captured(S.sync, root, cleanup_stale=True, force_prune=True)
        finally:
            S._acquire_sync_lock, S._request_rerun, S._sync_pass = saved
        lnp = getattr(S, "last_not_pushed", "<missing>")
        low = out.lower()
        tag = "re-run QUEUED" if queued else "re-run NOT queued"
        check(f"[{tag}] behaviour unchanged: returns True, runs no pass, tries the lock twice, queues once with "
              f"the run's flags", ok is True and not passes and len(tries) == 2
              and requests == [{"cleanup_stale": True, "force_prune": True}], (ok, passes, tries, requests))
        if queued:
            check("[re-run QUEUED] not a skip: last_not_pushed stays None (the CLI exits 0)", lnp is None, lnp)
            check("[re-run QUEUED] still says it QUEUED a re-run", "queued a re-run" in low, out)
            check("[re-run QUEUED] names BOTH possible holders — another sync, or deepmap enrich",
                  _names_both_holders(out), out)
            check("[re-run QUEUED] says what each holder does: a running sync re-syncs before it finishes; deepmap "
                  "enrich never syncs, so the next sync (/deep-map:refresh) performs the request",
                  "before it finishes" in low and "never syncs" in low and "/deep-map:refresh" in out, out)
            check("[re-run QUEUED] no longer promises unconditionally that the holder re-syncs",
                  OLD_PROMISE not in out, out)
        else:
            check("[re-run NOT queued] a skip: last_not_pushed is set (the CLI exits 4)",
                  isinstance(lnp, str) and bool(lnp.strip()), lnp)
            check("[re-run NOT queued] last_not_pushed names both possible holders",
                  _names_both_holders(lnp if isinstance(lnp, str) else ""), lnp)
            check("[re-run NOT queued] the line names both holders, says SKIPPED and names /deep-map:refresh",
                  _names_both_holders(out) and "skipped" in low and "/deep-map:refresh" in out, out)


# =====================================================================================================
# 2 — holder = a running sync (the real OS lock): it performs the queued re-run before it returns
# =====================================================================================================
def gate_holder_sync(S, base: Path) -> None:
    print("\n-- holder = a running sync (the real lock): the line is accurate and the holder re-syncs --")
    root = base / "holder_sync"
    root.mkdir()
    passes, nested = [], []

    def fake_pass(r, uri, **k):
        passes.append(bool(k.get("cleanup_stale")))
        if len(passes) == 1:                        # a second sync starts while this one holds the lock
            ok, out = _captured(S.sync, Path(r))
            nested.append((ok, out, getattr(S, "last_not_pushed", "<missing>")))
        return True

    saved = S._sync_pass
    try:
        S._sync_pass = fake_pass
        ok, _out = _captured(S.sync, root)
    finally:
        S._sync_pass = saved
    n_ok, n_out, n_lnp = nested[0] if nested else (None, "", "<no nested sync ran>")
    check("the second sync found the real lock held: returns True, queued, not a skip",
          n_ok is True and n_lnp is None and "queued a re-run" in n_out.lower(), (n_ok, n_lnp, n_out))
    check("... and its line names both possible holders", _names_both_holders(n_out), n_out)
    check("the holding sync performed the queued re-run before it returned (2 passes), then no request is left",
          ok is True and len(passes) == 2 and not (root / ".deepmap" / "sync.rerun").exists(), (ok, passes))


# =====================================================================================================
# 3 — holder = deepmap enrich (its real enumeration holds the lock): the request waits for the next sync
# =====================================================================================================
def gate_holder_enrich(S, E, base: Path) -> None:
    print("\n-- holder = deepmap enrich (its real enumeration holds the lock): the request waits for the next sync --")
    root = base / "holder_enrich"
    root.mkdir()
    (root / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    seen = []

    def fake_rebuild(r):                           # enrich's extraction, run while it holds the project's lock
        ok, out = _captured(S.sync, Path(r), cleanup_stale=True)
        seen.append((ok, out, getattr(S, "last_not_pushed", "<missing>")))
        return ([], [], [], "graphify")

    saved = E._rebuild_code
    try:
        E._rebuild_code = fake_rebuild
        _items, e_out = _captured(E.iter_enrichable_nodes, root)
    finally:
        E._rebuild_code = saved
    s_ok, s_out, s_lnp = seen[0] if seen else (None, "", "<enrich never extracted>")
    check("setup: enrich took the lock and extracted (no refusal)",
          bool(seen) and getattr(E, "last_refusal", None) is None, (seen, getattr(E, "last_refusal", None)))
    check("the sync started during enrich returns True and queues a re-run (not a skip)",
          s_ok is True and s_lnp is None and "queued a re-run" in s_out.lower(), (s_ok, s_lnp, s_out))
    check("... its line names deepmap enrich as a possible holder and says enrich never syncs",
          _names_both_holders(s_out) and "never syncs" in s_out.lower(), s_out)
    check("... and does not claim the holder will re-sync", OLD_PROMISE not in s_out, s_out)
    rerun = root / ".deepmap" / "sync.rerun"
    try:
        req = json.loads(rerun.read_text(encoding="utf-8")) if rerun.is_file() else None
    except (OSError, ValueError):
        req = None
    check("enrich finished and never synced: the request is still queued, with the skipped run's flags",
          isinstance(req, dict) and req.get("cleanup_stale") is True, req)
    check("control (O9): enrich named the queued re-run and /deep-map:refresh itself",
          "queued a re-run" in e_out and "/deep-map:refresh" in e_out, e_out)

    flags = []
    saved = S._sync_pass
    try:
        S._sync_pass = lambda r, uri, **k: flags.append(bool(k.get("cleanup_stale"))) or True
        ok, _out = _captured(S.sync, root)
    finally:
        S._sync_pass = saved
    check("the NEXT sync performs it: one pass carrying the queued --stale-cleanup, and the request is consumed",
          ok is True and flags == [True] and not rerun.exists(), (ok, flags, rerun.exists()))


# =====================================================================================================
# 4 — the docstrings describe both holders too
# =====================================================================================================
def gate_docstrings(S) -> None:
    print("\n-- the lock's docstrings name deepmap enrich as a holder --")
    for fn in ("sync", "_acquire_sync_lock", "_request_rerun"):
        doc = getattr(getattr(S, fn, None), "__doc__", None) or ""
        check(f"sync.{fn}'s docstring names deepmap enrich (it holds this lock while it extracts)",
              "enrich" in doc, doc[:300])


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    tree = _root_arg(sys.argv[1:])
    print("=" * 72)
    print(f"DeepMap — lock-held sync names both holders (another sync / deepmap enrich) (root: {tree})")
    print("=" * 72)
    sys.path.insert(0, str(tree / "build"))
    base = Path(tempfile.mkdtemp(prefix="dm_f2_lock_")).resolve()
    try:
        with _env(NEO4J_URI="bolt://127.0.0.1:9", DEEPMAP_HOME=str(base / "home"), DEEPMAP_ENRICH=None,
                  DEEPMAP_AUTO_GITIGNORE=None, DEEPMAP_EXTRACTOR=None):
            import sync as S                         # noqa: E402 — from the tree under test
            import enrich as E                       # noqa: E402
            check("setup: sync and enrich were loaded from the tree under test",
                  Path(S.__file__).resolve().parent == (tree / "build").resolve()
                  and Path(E.__file__).resolve().parent == (tree / "build").resolve(), (S.__file__, E.__file__))
            for gate, args in ((gate_stubbed, (S, base)), (gate_holder_sync, (S, base)),
                               (gate_holder_enrich, (S, E, base)), (gate_docstrings, (S,))):
                try:
                    gate(*args)
                except Exception as exc:  # noqa: BLE001 — a crash on the tree under test is a RED result
                    traceback.print_exc()
                    check(f"{gate.__name__} ran without raising", False, f"{type(exc).__name__}: {exc}")
            home = base / "home"
            check("hermetic: nothing was written under the temp DEEPMAP_HOME's registry",
                  not (home / "registry.json").exists(), sorted(p.name for p in home.iterdir()) if home.is_dir() else [])
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
