#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DeepMap RM-1 — the SHARED .deepmap/instance.json reader and its ONE locked writer (local-only, zero-egress).

EVERY writer of <root>/.deepmap/instance.json goes through one read-modify-write here
(`update_instance`): tune_weights.write_instance_weights / apply_learned, the drift guard, `deepmap
learning --enable/--disable` and the cold-start seed recommendation (both via merge_learned_fusion), the
offline `experiments.*` blocks, and the benchmark bridges (weight_ab.write_benchmark_evidence + its
learned-capture stage/restore, enrich_ab.write_enrich_evidence). One read/merge/confinement copy, one rule:
  * CROSS-PROCESS LOCKED — a kernel byte-lock (msvcrt on Windows, flock on POSIX; the OS drops it if the
    holder dies, so there is no stale-lock logic) on the sidecar <root>/.deepmap/instance.json.lock, taken
    with a BOUNDED wait (LOCK_WAIT_S): a writer that cannot get it skips its write, it never hangs. A
    detached post-commit sync's auto-tune and an interactive `deepmap learning` / `tune` now serialise
    instead of silently losing each other's update.
  * ATOMIC — the new object goes to a temp file in the same dir and replaces the old one with os.replace,
    so a reader sees the old or the new file, never a torn one.
  * NEVER WIPES — an existing file that is not a JSON object (a torn write from an older version, a hand
    edit, a list) REFUSES the write; the old writers read such a file as {} and the next write dropped
    every other key. An empty file holds nothing and is written normally.
Readers (learned_fusion, retrieval_log, read_instance) stay lock-free and fail safe. A writer's caller that got None
back names the cause with `write_problem` (it never writes instance.json).

Hard invariants:
  * ZERO-EGRESS: the only targets are <root>/.deepmap/instance.json and its .lock sidecar
    (confinement-guarded, exactly like retrieval_log.append_record).
  * NEVER raises: any failure returns None / {} — a state write must never break its caller.
  * `write_experiment` UNCONDITIONALLY forces applied:false. Experiments live under a TOP-LEVEL
    `experiments.<name>` key — a SIBLING of `learned_fusion`, never inside it — so they are
    structurally incapable of reaching the live activation gate (which reads only
    learned_fusion.*; see build/learned_fusion.py). No live search path reads experiments.*.
"""
from __future__ import annotations

import contextlib
import json
import os
import time
from pathlib import Path

INSTANCE_FILENAME = "instance.json"
LOCK_SUFFIX = ".lock"     # the write-lock sidecar: <root>/.deepmap/instance.json.lock (never holds data)
LOCK_WAIT_S = 5.0         # bounded wait for the write lock — a writer gives up, it never waits forever
_LOCK_SLEEP_S = 0.02
_IO_RETRY_S = 2.0         # Windows: a replace / read fails while another process briefly holds the file
_IO_SLEEP_S = 0.02


def _instance_path(root):
    """The confined path, or None when the root is unusable / the path escapes .deepmap/."""
    try:
        d = Path(root) / ".deepmap"
        path = d / INSTANCE_FILENAME
        confine = d.resolve()
        if not str(path.resolve().parent).startswith(str(confine)):
            return None
        return path
    except Exception:
        return None


def read_instance(root) -> dict:
    """The parsed instance.json object, or {} on absent/garbage/non-dict/bad-root. Never raises."""
    path = _instance_path(root)
    if path is None:
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


# ---- the cross-process write lock (mirrors sync._os_lock / port_registry._acquire_lock) -------------
def _os_lock(handle) -> bool:
    """Take a non-blocking exclusive OS lock on an open file handle. True iff acquired."""
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _os_unlock(handle) -> None:
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


@contextlib.contextmanager
def instance_lock(root, *, wait_s=None):
    """Hold the cross-process write lock of <root>/.deepmap/instance.json for the block. Yields the
    confined instance path — or None when the root is unusable or the lock was not free within the
    bounded wait (`wait_s`, default LOCK_WAIT_S); the caller must then NOT write. The lock is released on
    exit, also when the block raises. Nothing here raises by itself."""
    path = _instance_path(root)
    handle = None
    if path is not None:
        try:
            wait = LOCK_WAIT_S if wait_s is None else max(0.0, float(wait_s))
            path.parent.mkdir(parents=True, exist_ok=True)
            handle = open(path.with_name(path.name + LOCK_SUFFIX), "a+")
            deadline = time.monotonic() + wait
            while not _os_lock(handle):
                if time.monotonic() >= deadline:
                    handle.close()
                    handle = None
                    break
                time.sleep(_LOCK_SLEEP_S)
        except (OSError, ValueError, TypeError):
            if handle is not None:
                try:
                    handle.close()
                except OSError:
                    pass
            handle = None
    try:
        yield path if handle is not None else None
    finally:
        if handle is not None:
            _os_unlock(handle)
            try:
                handle.close()
            except OSError:
                pass


def read_locked(path):
    """A WRITER's strict read of instance.json (hold instance_lock). Returns (status, data, raw):
      ('absent', {}, None)        no file;
      ('ok', dict, raw)           a JSON object — an EMPTY / whitespace-only file is ('ok', {}, raw): it
                                  holds nothing to lose;
      ('unparseable', None, raw)  anything else (a torn write, garbage, a list) — the caller must REFUSE
                                  to write rather than replace it with a fresh object;
      ('unreadable', None, None)  the file kept failing to open (a transient OSError — on Windows another
                                  process holding it — is retried for _IO_RETRY_S first).
    A UTF-8 BOM is tolerated. Never raises."""
    deadline = time.monotonic() + _IO_RETRY_S
    while True:
        try:
            raw = Path(path).read_bytes()
            break
        except FileNotFoundError:
            return "absent", {}, None
        except OSError:
            if time.monotonic() >= deadline:
                return "unreadable", None, None
            time.sleep(_IO_SLEEP_S)
        except Exception:
            return "unreadable", None, None
    try:
        text = raw.decode("utf-8-sig")
        if not text.strip():
            return "ok", {}, raw
        data = json.loads(text)
    except Exception:
        return "unparseable", None, raw
    if not isinstance(data, dict):
        return "unparseable", None, raw
    return "ok", data, raw


def replace_locked(path, payload) -> bool:
    """Atomically replace instance.json (hold instance_lock) with `payload`: raw bytes verbatim, or a dict
    as json.dumps(indent=2, sort_keys=True) written in text mode — the exact bytes the writers always
    produced. A unique temp file in the same dir, fsync'd, then os.replace — retried for _IO_RETRY_S (on
    Windows a reader holding the target open makes it fail briefly). True iff the new content landed; on
    failure the old file is untouched and the temp removed. Never raises."""
    path = Path(path)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{os.urandom(4).hex()}.tmp")
    try:
        if isinstance(payload, (bytes, bytearray)):
            fh = open(tmp, "xb")
            data = bytes(payload)
        else:
            data = json.dumps(payload, indent=2, sort_keys=True)
            fh = open(tmp, "x", encoding="utf-8")
        with fh:
            fh.write(data)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        deadline = time.monotonic() + _IO_RETRY_S
        while True:
            try:
                os.replace(str(tmp), str(path))
                return True
            except OSError:
                if time.monotonic() >= deadline:
                    return False
                time.sleep(_IO_SLEEP_S)
    except Exception:
        return False
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def remove_locked(path) -> bool:
    """Remove instance.json (hold instance_lock). Used ONLY to undo a file this package's own stage created
    (benchmark/weight_ab.restore_learned_weights). Retried for _IO_RETRY_S. True iff it is gone. Never raises."""
    deadline = time.monotonic() + _IO_RETRY_S
    while True:
        try:
            Path(path).unlink()
            return True
        except FileNotFoundError:
            return True
        except OSError:
            if time.monotonic() >= deadline:
                return False
            time.sleep(_IO_SLEEP_S)
        except Exception:
            return False


def update_instance(root, mutate, *, wait_s=None):
    """THE read-modify-write every instance.json writer goes through: under the cross-process lock, read
    the current object STRICTLY (read_locked), pass it to `mutate(data) -> new dict | None` (None = nothing
    to write), and atomically replace the file with the result (replace_locked). Returns (path, status):
    the written path with 'written', or None with 'bad-root' | 'locked' (not acquired within the bounded
    wait, or the dir is not writable) | 'unreadable' | 'unparseable' (an existing file that is not a JSON
    object — REFUSED, never wiped) | 'skipped' (mutate returned None) | 'error'. Never raises (an
    exception inside `mutate` is 'error')."""
    try:
        with instance_lock(root, wait_s=wait_s) as path:
            if path is None:
                return None, ("bad-root" if _instance_path(root) is None else "locked")
            status, data, _raw = read_locked(path)
            if status in ("unreadable", "unparseable"):
                return None, status
            new = mutate(data)
            if new is None:
                return None, "skipped"
            if not isinstance(new, dict):
                return None, "error"
            return (path, "written") if replace_locked(path, new) else (None, "error")
    except Exception:
        return None, "error"


def write_problem(root):
    """Why a write of <root>/.deepmap/instance.json is refused right now, for a caller that only got None back from a
    writer (merge_learned_fusion, tune_weights.write_instance_weights, ...) and must say why — the CLI's `learning
    --enable/--disable` and `tune`. It runs update_instance ITSELF with a mutate that writes nothing and without
    waiting for the lock, so it answers by the very rule every writer follows (no copy of it):
      'unparseable'  an existing file that is not a JSON object — every writer refuses it rather than wipe it;
      'locked'       another process holds the write lock;
      'unwritable'   .deepmap/ or the lock sidecar cannot be created or opened (update_instance reports those as
                     'locked', but waiting will not help), or the directory refuses the new file a write creates;
      'unreadable'   the file kept failing to open;
      'bad-root'     the path is unusable or escapes .deepmap/;
      'error'        an unexpected failure;
      None           nothing stands in the way now (whatever refused the write has cleared — a retry can land).
    instance.json itself is never modified (like any writer, this may create .deepmap/ and the lock sidecar). Never
    raises."""
    try:
        _path, status = update_instance(root, lambda _data: None, wait_s=0)
        if status not in ("locked", "skipped"):
            return status
        path = _instance_path(root)
        if status == "locked":   # instance_lock yields None for a HELD lock and for a sidecar it cannot open
            try:
                with open(path.with_name(path.name + LOCK_SUFFIX), "a+"):
                    pass
            except OSError:
                return "unwritable"
            return "locked"
        # 'skipped': the lock was free and the file readable. replace_locked still needs a new temp file in the dir.
        return None if os.access(path.parent, os.W_OK) else "unwritable"
    except Exception:
        return None


def has_tune_result(lf) -> bool:
    """True iff a `learned_fusion` block carries an actual TUNE result (the tuner always writes
    `arm_weights` + `generated_utc`, even below threshold). `learning --enable/--disable` write a
    one-key {"learning_enabled": ...} block that is NOT a tune — every "current learned weights"
    display must treat it as "none yet", never render its missing fields as None. Never raises."""
    try:
        return isinstance(lf, dict) and (isinstance(lf.get("arm_weights"), dict)
                                         or bool(lf.get("generated_utc")))
    except Exception:
        return False


def merge_learned_fusion(root, updates: dict):
    """Merge `updates` into the `learned_fusion` block, preserving every other top-level key and
    every unrelated learned_fusion sub-key (logging_enabled, benchmark_evidence, ...) — one locked,
    atomic update_instance. Returns the path, or None on failure (incl. an existing file that is not a
    JSON object: refused, never wiped). Never raises."""
    try:
        if not isinstance(updates, dict):
            return None
        upd = dict(updates)

        def _merge(data):
            lf = data.get("learned_fusion")
            lf = dict(lf) if isinstance(lf, dict) else {}
            lf.update(upd)
            data["learned_fusion"] = lf
            return data

        return update_instance(root, _merge)[0]
    except Exception:
        return None


def write_experiment(root, name, block: dict):
    """Persist an OFFLINE experiment result under the top-level `experiments.<name>` key.
    `applied` is FORCED to False — an experiment block can never claim to be live. Sibling
    experiments and all other top-level keys (notably `learned_fusion`) are preserved verbatim.
    One locked, atomic update_instance. Returns the path, or None on failure. Never raises."""
    try:
        if not isinstance(name, str) or not name:
            return None
        blk = dict(block) if isinstance(block, dict) else {}
        blk["applied"] = False   # NEVER live — offline evidence only (the G4 discipline)

        def _put(data):
            exp = data.get("experiments")
            exp = dict(exp) if isinstance(exp, dict) else {}
            exp[name] = blk
            data["experiments"] = exp
            return data

        return update_instance(root, _put)[0]
    except Exception:
        return None


def read_experiments(root) -> dict:
    """The top-level `experiments` object, or {} when absent/malformed. Never raises."""
    data = read_instance(root)
    exp = data.get("experiments")
    return exp if isinstance(exp, dict) else {}
