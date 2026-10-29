#!/usr/bin/env python3
"""DeepMap sync status — who holds a project's sync lock, how its last sync ended, and the rules DeepMap's own
background refresh (auto-sync) shares with every other writer of a project's graph.

ONE module answers these questions for every consumer (KL-architecture:002) — sync, enrich, the auto-sync runner
(build/auto_sync.py), the hooks and provision:

  * who holds <root>/.deepmap/sync.lock and is it alive?   write_running / update_running / clear_running /
                                                            read_running / read_holder (`live` = pid + start stamp)
  * how did the last sync pass end?                        write_last / read_last (.deepmap/sync_last.json)
  * is a user's sync waiting for the background refresh?   write_preempt / clear_preempt / live_preempts
                                                            (.deepmap/sync.preempt.<pid>.json)
  * which environment knobs shape the graph?               GRAPH_SHAPING_ENV (+ NON_SHAPING_ENV, INTERNAL_ENV,
                                                            ROUTE_ENV: every DEEPMAP_* name the pipeline reads is in
                                                            exactly one set — tests/sync_status_gates.py locks it),
                                                            canonical_env_value / recipe_env / recipe_conflicts /
                                                            replay_env, make_build_recipe
  * is the embedding model fully cached?                   model_cache_state (a stdlib file check; never imports
                                                            huggingface_hub, never downloads)
  * the OS lock primitive and the atomic JSON writer       os_lock / try_lock / release_lock, atomic_write_json

and holds `UnattendedControl`, the cooperative-preemption contract of an unattended (background) sync: a sync the
user starts (/deep-map:refresh, /deep-map:setup, deepmap enrich) that finds the background refresh holding the
lock writes a preempt record and waits; the background holder stops at its next safe point — before it writes
anything to Neo4j it simply exits (a "hard yield", start/extract phases only), at its write checkpoint it returns
without pushing (a "soft yield") — so the user's sync always runs in ITS OWN process, with its own environment,
flags, prune and verdict.

Import rule: stdlib only at module level. `port_registry` (pid start stamps, DEEPMAP_HOME), `deps_bootstrap`
(MODEL_KEY_FILES, the Hugging Face cache rule) and `embeddings` (MODEL_NAME) are imported lazily, from this file's
own directory. Never imports sync, neo4j or torch — the hooks import this module.

Every record is a JSON object, UTF-8, "schema": 1, written atomically (temp + os.replace, with a Windows
sharing-violation retry). A record write that fails never changes a sync's return value, verdict or exit code:
every function here is best-effort and never raises (UnattendedControl's constructor and the explicit ValueErrors
excepted).
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

_BUILD = Path(__file__).resolve().parent

SCHEMA = 1
RUNNING_FILE = "sync_running.json"
LAST_FILE = "sync_last.json"
PREEMPT_GLOB = "sync.preempt.*.json"
PREEMPT_WAIT_S = 900.0          # a user's sync waits at most this long for the background refresh to stop
PREEMPT_POLL_S = 0.5            # ... trying the lock this often
PREEMPT_PROGRESS_S = 60.0       # ... and saying it still waits this often
HOLDER_SETTLE_S = 2.0           # a lock-miss waits this long for the holder's record (written right after acquire)
WATCH_POLL_S = 1.0              # the unattended holder looks for a waiting user sync this often
PHASES = ("start", "extract", "write", "embed", "manifest", "enrich-extract")
HARD_EXIT_PHASES = ("start", "extract")     # nothing has been written to Neo4j yet: the holder may just exit
ORIGINS = ("manual", "unattended", "enrich")
PRUNE_POLICIES = ("none", "evidence", "full")

# The words a phase is said with (shared with the hooks' notes, spec §2.8).
PHASE_WORDS = {
    "start": "checking what changed", "checking": "checking what changed",
    "waiting-db": "waiting for the database",
    "waiting-slot": "waiting for another project's refresh to finish",
    "extract": "reading the code", "write": "writing the graph", "embed": "computing embeddings",
    "manifest": "finishing", "enrich-extract": "reading the code for deepmap enrich",
}

# ---- the preemption texts (spec §2.8) ------------------------------------------------------------------------------
PREEMPT_START = ("[deepmap sync] DeepMap's background refresh of this project is running (started {ago} ago; "
                 "{phase}) — asking it to stop at its next safe point; this sync then runs on its own, with your "
                 "settings (waiting at most {wait} min).")
PREEMPT_PROGRESS = ("[deepmap sync] still waiting for DeepMap's background refresh to stop ({phase}; {waited} so "
                    "far).")
PREEMPT_ACQUIRED = "[deepmap sync] the background refresh stopped — syncing now."
PREEMPT_TIMEOUT = ("busy: DeepMap's background refresh of this project did not stop within {wait} min (it was "
                   "{phase}), so this run synced nothing — run it again once that refresh finishes")
ENRICH_PREEMPT_START = ("waiting for DeepMap's background refresh of this project to stop at its next safe point "
                        "(started {ago} ago; {phase}) — at most {wait} min; nothing is extracted, sent or spent "
                        "meanwhile.")
ENRICH_PREEMPT_TIMEOUT = ("REFUSED — DeepMap's background refresh of this project did not stop within {wait} min "
                          "({phase}). Nothing was extracted, sent, spent or written. Re-run it once that refresh "
                          "finishes.")
UNATTENDED_YIELD = ("[deepmap sync] a sync started by the user (or setup / enrich) is waiting — this background "
                    "refresh stops here ({where}); nothing was pushed.")
YIELD_WHERE = {"start": "before reading the code", "extract": "while reading the code",
               "write": "before writing the graph"}
# A yield only to `deepmap enrich` (every live preempt record says by "enrich"): enrich extracts but never syncs, so
# nothing will make the graph current — not a hand-over to a user's sync (yield_fields).
ENRICH_YIELD = ("[deepmap sync] a deepmap enrich run is waiting for this project's lock — this background refresh "
                "stops here ({where}); nothing was pushed, and enrich does not refresh the graph (the next refresh "
                "does; /deep-map:refresh runs one now).")
# A re-run request a user's sync queued (origin manual, or one an older DeepMap wrote) while no user's sync waits for
# the lock: the background refresh never runs it (it carries that sync's own settings and prune) and syncs nothing.
USER_REQUEST_PENDING = ("[deepmap sync] a re-run that a sync the user started queued for this project is waiting — "
                        "this background refresh never runs it (it carries that sync's own settings and prune), so it "
                        "synced nothing; /deep-map:refresh runs it.")
USER_REQUEST_REASON = ("a re-run that a sync the user started queued is still waiting, and a background refresh never "
                       "runs one (it carries that sync's own settings and prune; a sync the user starts runs it)")
USER_REQUEST_AFTER_PASS = ("[deepmap sync] a re-run that a sync the user started queued is waiting — this background "
                           "refresh leaves it for a sync the user starts (/deep-map:refresh) and stops after the pass "
                           "it finished.")


def yield_fields(by, where) -> dict:
    """THE rule for what a background refresh that yielded is recorded as (sync_last.json; sync and the hard-yield
    watcher both use it). `by`: the "by" of the live preempt records it yielded to; `where`: a YIELD_WHERE key. A
    user's sync / setup is waiting (any record not by "enrich", or none known) -> outcome "preempted", rc 0: that sync
    runs next and reports its own result. Only `deepmap enrich` runs -> outcome "busy-enrich", rc 4: enrich never
    syncs, so the graph stays as it was (maybe stale) until the next refresh. Never raises."""
    try:
        names = sorted({str(b) for b in (by or ()) if b})
    except Exception:  # noqa: BLE001
        names = []
    w = YIELD_WHERE.get(where or "start", "before reading the code")
    if names and all(b == "enrich" for b in names):
        return {"outcome": "busy-enrich", "rc": 4, "preempted_by": names, "where": w,
                "reason": f"a deepmap enrich run needed this project's sync lock; stopped {w} (enrich does not "
                          f"refresh the graph)",
                "log": ENRICH_YIELD.format(where=w)}
    return {"outcome": "preempted", "rc": 0, "preempted_by": names, "where": w,
            "reason": f"a sync the user started was waiting; stopped {w}",
            "log": UNATTENDED_YIELD.format(where=w)}

# ---- graph-shaping environment (spec §2.3) -------------------------------------------------------------------------
# Every DEEPMAP_* name the sync pipeline reads is in EXACTLY ONE of the four sets below (the drift lock,
# tests/sync_status_gates.py section G). GRAPH_SHAPING_ENV maps a knob to its CANONICAL DEFAULT: a knob whose
# canonical value differs from it is recorded in the manifest's build_recipe.env, replayed by the background
# refresh, and an explicit conflicting value makes that refresh skip (it never rebuilds a graph with settings
# the user did not build it with).
_ON_WORDS = frozenset({"1", "true", "yes", "on"})
_OFF_WORDS = frozenset({"0", "false", "no", "off"})
_DEFAULT_OFF_KNOBS = (     # on iff the value is an ON-word (the readers' `in {"1","true","yes","on"}`)
    "DEEPMAP_EXCLUDE_TESTS", "DEEPMAP_INCLUDE_VENDOR", "DEEPMAP_GRAPHIFY_CALLS_PLUS",
    "DEEPMAP_GRAPHIFY_CALLS_PLUS_TESTS", "DEEPMAP_SUPPLEMENTAL_TS_RUST", "DEEPMAP_LOCAL_DEF_CALLS",
    "DEEPMAP_FTS_SHORT_NAME", "DEEPMAP_SUMMARY_NORMALIZE", "DEEPMAP_MODULE_DOCSTRINGS", "DEEPMAP_MERGE_SUMMARIES",
    "DEEPMAP_DROP_APP_TO_TEST_CALLS",
    # typed-receiver call linking (typed_calls._config: `in _TRUE`) and the D0 cross-family filter
    # (sync._calls_family_filter_enabled, while sync._CALLS_FAMILY_FILTER_DEFAULT is off — tests/
    # calls_family_filter_gates.py locks the two together)
    "DEEPMAP_TYPED_CALLS_TESTS", "DEEPMAP_TYPED_CALLS_NONPROD", "DEEPMAP_CALLS_FAMILY_FILTER")
_DEFAULT_ON_KNOBS = (      # off iff the value is an OFF-word (the readers' `not in {"0","false","no","off"}`)
    "DEEPMAP_CASE_INSENSITIVE_EXT", "DEEPMAP_RESCUE_SKIPPED_DIRS", "DEEPMAP_FALSE_CALL_FILTER",
    "DEEPMAP_SELF_CALL_REBIND", "DEEPMAP_CLEAN_OWNER_NAMES", "DEEPMAP_RESOLVED_IMPORTS",
    "DEEPMAP_VECTOR_QUANTIZATION", "DEEPMAP_PRECISE_FIXES",
    "DEEPMAP_TYPED_CALLS_REPLACE",                       # typed_calls._config: `not in _FALSE`
    "DEEPMAP_CSHARP_MEMBERS")                            # graphify_extract: C# property / constructor nodes
# Set-valued knobs, read through the PRODUCT's own reader (one rule — KL-architecture:002). Unset (or blank) is ""
# = whatever the code selects by default when the graph is built. An EXPLICIT value's canonical form is what it
# SELECTS — "off" for nothing, "on" for DEEPMAP_PRECISE_CALLS' every-file mode, else the selected names sorted —
# even when that equals today's default set: so an explicit opt-out (`=0`) is recorded and replayed as "off", and a
# later default flip (slice S8) never turns it into the new default. A typo is recorded as what it selects: nothing
# for most knobs, every mode for DEEPMAP_TYPED_CALLS_IFACE (whose reader ignores unknown tokens and reads a value
# naming no mode as unset). Every canonical value reads back to itself (replay_env). recipe_conflicts compares what
# the two values SELECT (so `=0` and unset never conflict while both select nothing). When the reader cannot be
# loaded, an explicit value is kept AS SPELLED (replaying the spelling makes the reader select what it selected)
# and recipe_conflicts never calls it a conflict: it cannot judge (and a sync that cannot load that reader cannot
# apply the knob either).
_SET_KNOBS = ("DEEPMAP_PRECISE_CALLS", "DEEPMAP_TYPED_CALLS", "DEEPMAP_CALLS_AUDIT", "DEEPMAP_TYPED_CALLS_SHAPES",
              "DEEPMAP_TYPED_CALLS_IFACE")
GRAPH_SHAPING_ENV: dict = {
    "DEEPMAP_EXTRACTOR": "auto",
    **{name: "off" for name in _DEFAULT_OFF_KNOBS},
    **{name: "on" for name in _DEFAULT_ON_KNOBS},
    # a language-family set (graphify_extract.precise_calls_families): unset = the code's default families,
    # an OFF-word = none, an ON-word = every file, else a CSV of family names
    "DEEPMAP_PRECISE_CALLS": "",
    # typed-receiver call linking (build/typed_calls.py): language sets (typed_calls.typed_langs / audit_langs —
    # unset = the code's default, an OFF-word = none, an ON-word = the wave-1 languages, else a CSV), the shape
    # set (unset = every shape), and the per-language file cap (a language with more files is skipped whole)
    "DEEPMAP_TYPED_CALLS": "",
    "DEEPMAP_CALLS_AUDIT": "",
    "DEEPMAP_TYPED_CALLS_SHAPES": "",
    # C# interface and virtual dispatch (typed_calls._iface_modes; read only while a language with that lookup rule is
    # on): unset or an ON-word = every mode (static, impl), an OFF-word = none, else a CSV of typed_calls.IFACE_MODES
    # (an unknown token is ignored; a value naming no mode at all selects every mode, like unset)
    "DEEPMAP_TYPED_CALLS_IFACE": "",
    "DEEPMAP_TYPED_CALLS_MAX_FILES": "20000",
    # the recognizer set (language_recognizers._enabled_entries): unset / ON-word / "all" = every entry,
    # an OFF-word = none, else a CSV of entry names
    "DEEPMAP_RECOGNIZERS": "",
    # the bulk-embedding thread count (embeddings.bulk_embed_threads): the vectors follow it
    "DEEPMAP_EMBED_THREADS": "8",
}
NON_SHAPING_ENV = frozenset({
    "DEEPMAP_AUTO_GITIGNORE", "DEEPMAP_AUTO_TUNE", "DEEPMAP_EMBED_PROGRESS_S", "DEEPMAP_ENRICH",
    "DEEPMAP_GRAPHIFY_TIMEOUT", "DEEPMAP_INCREMENTAL_SYNC", "DEEPMAP_AUTO_SYNC", "DEEPMAP_AUTO_SYNC_MAX_S",
    "DEEPMAP_AUTO_SYNC_PRUNE", "DEEPMAP_HOOKS", "DEEPMAP_HEAL"})
INTERNAL_ENV = frozenset({"DEEPMAP_CASE_ALIASES", "DEEPMAP_RESCUE_DIRS",   # recomputed per run by graphify_extract
                          "DEEPMAP_SKIP_MACOS_METADATA"})
ROUTE_ENV = frozenset({"DEEPMAP_HOME"})                                     # recorded as build_recipe.deepmap_home

_EMBED_THREADS_LIMIT = 64


def _csv_canonical(low: str) -> str:
    """A CSV's tokens: stripped, sorted, unique, ','-joined; "off" when it names nothing (the readers then select
    nothing)."""
    tokens = sorted({t.strip() for t in low.split(",") if t.strip()})
    return ",".join(tokens) if tokens else "off"


def canonical_env_value(name: str, raw) -> str:
    """The canonical form of graph-shaping knob `name` set to `raw` (None / blank = unset = its default); a name that
    is not a graph-shaping knob gives its stripped value. Two values with one canonical form build the same graph.
    An explicit value of a set-valued knob (_SET_KNOBS) is never "" — see the block comment there. Never raises."""
    try:
        default = GRAPH_SHAPING_ENV.get(name)
        text = "" if raw is None else str(raw).strip()
        if default is None:
            return text
        if not text:
            return default
        low = text.lower()
        if name == "DEEPMAP_EXTRACTOR":
            return low
        if name in _DEFAULT_OFF_KNOBS:
            return "on" if low in _ON_WORDS else "off"
        if name in _DEFAULT_ON_KNOBS:
            return "off" if low in _OFF_WORDS else "on"
        if name == "DEEPMAP_RECOGNIZERS":
            if low in _ON_WORDS or low == "all":
                return ""
            if low in _OFF_WORDS:
                return "off"
            return _csv_canonical(low)
        if name in _SET_KNOBS:
            got = _set_selection(name, text)
            if got is None:
                return text      # the reader cannot be loaded: kept as spelled (recipe_conflicts never judges it)
            return _render_set(*got)
        if name == "DEEPMAP_TYPED_CALLS_MAX_FILES":
            return _max_files_canonical(text, default)
        if name == "DEEPMAP_EMBED_THREADS":
            if text.isascii() and text.isdigit():
                digits = text.lstrip("0")
                if digits:
                    return str(_EMBED_THREADS_LIMIT if len(digits) > 3 else min(int(digits), _EMBED_THREADS_LIMIT))
                return default
            return "auto" if low == "auto" else default
        return low
    except Exception:  # noqa: BLE001
        return "" if raw is None else str(raw)


def _render_set(chosen, everything=None) -> str:
    """An explicit _SET_KNOBS value's canonical form, from what it selects: "off" = nothing, "on" = `everything`
    (PRECISE's every-file mode), else the sorted names. Never "" (that is unset)."""
    if not chosen:
        return "off"
    if everything is not None and everything in chosen:
        return "on"
    return ",".join(sorted(str(x) for x in chosen))


def _set_selection(name: str, text: str):
    """(the set `text` selects, the every-file marker or None) for a _SET_KNOBS knob, through the product's OWN reader
    — graphify_extract.precise_calls_families for DEEPMAP_PRECISE_CALLS (S1 review finding 6), typed_calls' flag
    readers for the typed call knobs; "" (unset) selects the code's default — or None when that module cannot be
    loaded. Lazy: only a knob a value is given for imports it (the session-start precheck reads only the knobs the
    environment sets). Never raises."""
    try:
        if name == "DEEPMAP_PRECISE_CALLS":
            gx = _lazy("graphify_extract")
            fn = getattr(gx, "precise_calls_families", None) if gx is not None else None
            if not callable(fn):
                return None
            return frozenset(fn(text)), getattr(gx, "_PRECISE_ALL", "*")
        tc = _lazy("typed_calls")
        if tc is None:
            return None
        if name in ("DEEPMAP_TYPED_CALLS", "DEEPMAP_CALLS_AUDIT"):
            fn = getattr(tc, "typed_langs" if name == "DEEPMAP_TYPED_CALLS" else "audit_langs", None)
            if not callable(fn):
                return None
            return frozenset(fn({name: text} if text else {})), None
        if name == "DEEPMAP_TYPED_CALLS_IFACE":
            # what the value selects for a language that has the iface_dispatch rule (the only one it is read for):
            # an ON-word selects every mode like unset (canonical "impl,static", explicit); an OFF-word nothing
            # ("off"); an unknown token is ignored, and a value naming no mode at all selects every mode, so a typo
            # is recorded as what it builds (every mode), never as "off"
            fn, registry = getattr(tc, "_iface_modes", None), getattr(tc, "LANGUAGES", None)
            langs = [n for n, s in (registry or {}).items() if "iface_dispatch" in (getattr(s, "lookup_rules", ()) or ())]
            if not callable(fn) or not langs:
                return None
            return frozenset(fn({name: text} if text else {}, langs, registry)), None
        cfg, registry = getattr(tc, "_config", None), getattr(tc, "LANGUAGES", None)
        if not callable(cfg) or registry is None:
            return None
        return frozenset(cfg({name: text} if text else {}, registry).shapes), None
    except Exception:  # noqa: BLE001
        return None


def _selections_differ(name: str, current, built) -> bool:
    """Do two values of a _SET_KNOBS knob (None / blank = unset) select different sets? False when the product's
    reader cannot be loaded (it cannot judge). Never raises."""
    try:
        a = _set_selection(name, "" if current is None else str(current).strip())
        b = _set_selection(name, "" if built is None else str(built).strip())
        if a is None or b is None:
            return False
        return a[0] != b[0]
    except Exception:  # noqa: BLE001
        return False


def _max_files_canonical(text: str, default: str) -> str:
    """DEEPMAP_TYPED_CALLS_MAX_FILES as typed_calls reads it: a positive int, else its default. Never raises."""
    try:
        tc = _lazy("typed_calls")
        cfg, registry = getattr(tc, "_config", None), getattr(tc, "LANGUAGES", None)
        if callable(cfg) and registry is not None:
            return str(int(cfg({"DEEPMAP_TYPED_CALLS_MAX_FILES": text}, registry).max_files))
    except Exception:  # noqa: BLE001
        pass
    try:
        n = int(text)
        return str(n) if n > 0 else default
    except (TypeError, ValueError):
        return default


def _env_set(env, name) -> bool:
    """Is `name` EXPLICITLY set in `env` (present and not blank)? A blank value reads as unset everywhere."""
    try:
        v = env.get(name)
        return v is not None and bool(str(v).strip())
    except Exception:  # noqa: BLE001
        return False


def recipe_env(env=None) -> dict:
    """{name: canonical} for every graph-shaping knob whose canonical value in `env` (default os.environ) differs
    from its default — what build_recipe.env records. Never raises."""
    e = os.environ if env is None else env
    out = {}
    for name, default in GRAPH_SHAPING_ENV.items():
        try:
            value = canonical_env_value(name, e.get(name))
        except Exception:  # noqa: BLE001
            continue
        if value != default:
            out[name] = value
    return out


def recipe_conflicts(recorded, env=None) -> list:
    """[(name, recorded_effective, current_canonical)] for every graph-shaping knob `env` (default os.environ) sets
    EXPLICITLY to a value whose canonical form differs from the effective value the graph was built with
    (`recorded` = build_recipe.env: only the non-default knobs; a knob it lacks was at its default — so a legacy
    manifest with no recipe means "all defaults"). An unset knob is never a conflict (replay_env supplies it). A
    set-valued knob (_SET_KNOBS) conflicts when the two values SELECT different sets (unset = the code's default
    set), never when its reader cannot be loaded (_selections_differ). Never raises."""
    e = os.environ if env is None else env
    rec = recorded if isinstance(recorded, dict) else {}
    out = []
    for name, default in GRAPH_SHAPING_ENV.items():
        if not _env_set(e, name):
            continue
        try:
            current = canonical_env_value(name, e.get(name))
            built = canonical_env_value(name, rec.get(name)) if name in rec else default
            if name in _SET_KNOBS:
                differ = _selections_differ(name, e.get(name), rec.get(name) if name in rec else None)
            else:
                differ = current != built
        except Exception:  # noqa: BLE001
            continue
        if differ:
            out.append((name, built, current))
    return out


def replay_env(recorded, env=None) -> list:
    """Set env[name] = recorded[name] for every graph-shaping knob the graph was built with that `env` (default
    os.environ) leaves unset — so a background refresh rebuilds with the recipe the user built the graph with.
    Returns the names it set. Never raises."""
    e = os.environ if env is None else env
    rec = recorded if isinstance(recorded, dict) else {}
    done = []
    for name, value in rec.items():
        try:
            if name not in GRAPH_SHAPING_ENV or value is None or _env_set(e, name):
                continue
            e[name] = str(value)
            done.append(name)
        except Exception:  # noqa: BLE001
            continue
    return done


# ---- OS locks --------------------------------------------------------------------------------------------------------
def os_lock(handle) -> bool:
    """Take a non-blocking exclusive OS lock on an open file handle. True iff acquired (THE lock primitive; sync's
    _os_lock is this function)."""
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


def try_lock(path):
    """An open handle holding the OS lock on `path` (created with its directory), or None when another holder has
    it or it cannot be opened. Release with release_lock. Never raises."""
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        handle = open(p, "a+")
    except OSError:
        return None
    if not os_lock(handle):
        try:
            handle.close()
        except OSError:
            pass
        return None
    return handle


def release_lock(handle) -> None:
    """Release a handle os_lock / try_lock locked, and close it. Safe with None. Never raises."""
    if handle is None:
        return
    try:
        if os.name == "nt":
            import msvcrt
            try:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        else:
            import fcntl
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
    except Exception:  # noqa: BLE001
        pass
    finally:
        try:
            handle.close()
        except Exception:  # noqa: BLE001
            pass


# ---- atomic JSON ------------------------------------------------------------------------------------------------------
_REPLACE_RETRIES = 5
_REPLACE_RETRY_S = 0.1


def _replace(src, dst) -> None:
    """os.replace — a seam the gates replace with a recorder."""
    os.replace(src, dst)


def atomic_write_text(path, text: str) -> bool:
    """Write `text` (UTF-8, the platform's newline translation — what Path.write_text did) to `path` atomically: a
    temp file ".<name>.<pid>-<thread>.tmp" in the same directory, then os.replace. On Windows a PermissionError
    with winerror 5 / 32 (a reader holds the target open) is retried _REPLACE_RETRIES times, _REPLACE_RETRY_S
    apart. True on success; False on any failure, leaving the old target intact (never a partial one) and no temp
    file. Never raises."""
    tmp = None
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f".{p.name}.{os.getpid()}-{threading.get_ident()}.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
        for attempt in range(_REPLACE_RETRIES + 1):
            try:
                _replace(str(tmp), str(p))
                tmp = None
                return True
            except PermissionError as exc:
                if getattr(exc, "winerror", None) in (5, 32) and attempt < _REPLACE_RETRIES:
                    time.sleep(_REPLACE_RETRY_S)
                    continue
                return False
    except Exception:  # noqa: BLE001 — a record is best-effort
        return False
    finally:
        if tmp is not None:
            try:
                Path(tmp).unlink()
            except OSError:
                pass
    return False


def _unlink(path) -> bool:
    """Remove `path`; True when it is gone afterwards. On Windows a reader that has the file open at that instant
    (another process polling the record) makes unlink fail with a sharing violation — retried like a replace, so a
    record its writer removes never lingers. Never raises."""
    for attempt in range(_REPLACE_RETRIES + 1):
        try:
            Path(path).unlink()
            return True
        except FileNotFoundError:
            return True
        except PermissionError:
            if attempt < _REPLACE_RETRIES:
                time.sleep(_REPLACE_RETRY_S)
                continue
            return False
        except OSError:
            return False
    return False


def atomic_write_json(path, obj) -> bool:
    """atomic_write_text of json.dumps(obj, indent=2, sort_keys=True). Never raises."""
    try:
        text = json.dumps(obj, indent=2, sort_keys=True)
    except (TypeError, ValueError):
        return False
    return atomic_write_text(path, text)


def read_json(path):
    """The JSON object at `path` (a dict), or None when it is absent, unreadable or not an object. Never raises."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001
        return None


def _utc(epoch=None) -> str:
    t = time.time() if epoch is None else epoch
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def duration_words(seconds) -> str:
    """'N s' under 90 s, 'N min' under 90 min, else 'H h M min' (auto_sync.human_duration's rule). Never raises."""
    try:
        s = max(0.0, float(seconds))
    except (TypeError, ValueError):
        return "an unknown time"
    if s < 90:
        return f"{int(round(s))} s"
    if s < 5400:
        return f"{int(round(s / 60))} min"
    h, m = divmod(int(round(s / 60)), 60)
    return f"{h} h {m} min"


def phase_words(phase) -> str:
    return PHASE_WORDS.get(str(phase or ""), str(phase or "running"))


# ---- process identity -------------------------------------------------------------------------------------------------
def _lazy(name):
    """A sibling module of build/, imported lazily (None when it cannot be). Never raises."""
    try:
        if str(_BUILD) not in sys.path:
            sys.path.insert(0, str(_BUILD))
        return importlib.import_module(name)
    except Exception:  # noqa: BLE001
        return None


def _pid_create_time(pid):
    pr = _lazy("port_registry")
    return pr.pid_create_time(pid) if pr is not None else None


def _pid_alive(pid):
    """True / False — port_registry's liveness rule (unknown counts as alive); None without port_registry."""
    pr = _lazy("port_registry")
    fn = getattr(pr, "_pid_alive", None) if pr is not None else None
    if fn is None:
        return None
    try:
        return bool(fn(pid))
    except Exception:  # noqa: BLE001
        return None


def _same_start(recorded, current):
    pr = _lazy("port_registry")
    return pr.same_start(recorded, current) if pr is not None else None


_IDENTITY: dict = {}


def process_identity() -> dict:
    """{"pid": os.getpid(), "pid_created": this process's start stamp (port_registry.pid_create_time)} — cached per
    pid. Never raises."""
    pid = os.getpid()
    got = _IDENTITY.get(pid)
    if got is None:
        try:
            created = _pid_create_time(pid)
        except Exception:  # noqa: BLE001
            created = None
        got = {"pid": pid, "pid_created": created}
        if created is not None:
            _IDENTITY[pid] = got
    return dict(got)


def holder_live(rec):
    """Is the process a record names (its "pid" + "pid_created" start stamp) still running? True / False, or None
    when that cannot be told (no stamp recorded, or none readable — the pid alone never proves identity, it is
    reused). A pid that is gone, or now names a process that started at another time, is False. Never raises."""
    try:
        if not isinstance(rec, dict):
            return None
        pid = int(rec.get("pid"))
        if pid <= 0:
            return None
        recorded = rec.get("pid_created")
        if pid == os.getpid():
            own = process_identity().get("pid_created")
            if recorded is None or own is None:
                return None
            return _same_start(recorded, own)
        if _pid_alive(pid) is False:
            return False
        if recorded is None:
            return None
        current = _pid_create_time(pid)
        if current is None:
            return None
        return _same_start(recorded, current)
    except Exception:  # noqa: BLE001
        return None


# ---- sync_running.json / sync_last.json --------------------------------------------------------------------------------
def _state_dir(root) -> Path:
    return Path(root) / ".deepmap"


def running_path(root) -> Path:
    return _state_dir(root) / RUNNING_FILE


def last_path(root) -> Path:
    return _state_dir(root) / LAST_FILE


def write_running(root, record: dict) -> bool:
    """Write <root>/.deepmap/sync_running.json — only the CURRENT holder of the project's sync lock calls this, right
    after acquiring it. `record` is completed with schema / pid / pid_created / phase_utc when it lacks them. Never
    raises; False when it could not be written."""
    try:
        rec = dict(record or {})
        ident = process_identity()
        rec.setdefault("schema", SCHEMA)
        rec.setdefault("pid", ident["pid"])
        rec.setdefault("pid_created", ident["pid_created"])
        rec.setdefault("phase", "start")
        rec.setdefault("phase_utc", _utc())
        rec.pop("live", None)
        return atomic_write_json(running_path(root), rec)
    except Exception:  # noqa: BLE001
        return False


def update_running(root, **fields) -> bool:
    """Merge `fields` into this process's own sync_running.json (a "phase" also stamps phase_utc). False — nothing
    written — when the record is absent or names another process. Never raises."""
    try:
        cur = read_json(running_path(root))
        if not cur or cur.get("pid") != os.getpid():
            return False
        cur.update(fields)
        if "phase" in fields:
            cur["phase_utc"] = _utc()
        cur.pop("live", None)
        return atomic_write_json(running_path(root), cur)
    except Exception:  # noqa: BLE001
        return False


def clear_running(root, *, pid=None) -> bool:
    """Remove sync_running.json only when it names `pid` (default: this process). True iff removed. Never raises."""
    try:
        want = os.getpid() if pid is None else int(pid)
        path = running_path(root)
        cur = read_json(path)
        if not cur or cur.get("pid") != want:
            return False
        return _unlink(path)
    except Exception:  # noqa: BLE001
        return False


def read_running(root):
    """The holder record (sync_running.json) plus "live": True / False / None (holder_live), or None when there is
    no readable record. Never raises."""
    rec = read_json(running_path(root))
    if rec is None:
        return None
    rec["live"] = holder_live(rec)
    return rec


def read_holder(root, *, settle_s=None):
    """read_running, polled every 0.1 s for up to `settle_s` (default HOLDER_SETTLE_S, read at call time) while
    there is no record yet (a holder writes it right after it takes the lock, so a sync that just missed the lock
    may look before it exists). Never raises."""
    if settle_s is None:
        settle_s = HOLDER_SETTLE_S
    try:
        deadline = time.monotonic() + max(0.0, float(settle_s or 0.0))
    except (TypeError, ValueError):
        deadline = time.monotonic()
    while True:
        rec = read_running(root)
        if rec is not None or time.monotonic() >= deadline:
            return rec
        time.sleep(0.1)


LAST_DEFAULTS = {
    "schema": SCHEMA, "id": None, "origin": None, "trigger": None, "pid": None, "started_utc": None,
    "finished_utc": None, "finished_epoch": None, "wall_s": None, "passes": 0, "rc": None, "outcome": None,
    "not_pushed_kind": None, "veto": None, "engine_timed_out": False, "refusal_kind": None, "reason": None,
    "verdict": None, "reasons": [], "built_at_sha": None, "dirty": None, "source_fingerprint": None,
    "pushed_nodes": 0, "pruned": 0, "prune_policy": None, "stale_cleanup_skipped": None, "stale_kept": 0,
    "embeddings_deferred": False, "deepmap_version": None,
}


def last_record(**fields) -> dict:
    """A sync_last.json record: LAST_DEFAULTS overlaid with `fields` (unknown keys kept)."""
    rec = dict(LAST_DEFAULTS)
    rec["reasons"] = []
    rec.update(fields)
    return rec


def write_last(root, record: dict) -> bool:
    """Write <root>/.deepmap/sync_last.json — only the holder of the project's sync lock, once per pass. Never
    raises."""
    try:
        rec = dict(record or {})
        rec.setdefault("schema", SCHEMA)
        return atomic_write_json(last_path(root), rec)
    except Exception:  # noqa: BLE001
        return False


def read_last(root):
    return read_json(last_path(root))


# ---- preempt records ---------------------------------------------------------------------------------------------------
def write_preempt(root, *, by: str):
    """Ask the background refresh holding this project's lock to stop at its next safe point: write
    <root>/.deepmap/sync.preempt.<pid>.json. Returns its path, or None when it could not be written. The writer
    removes it (clear_preempt) once it got the lock or stopped waiting. Never raises."""
    try:
        ident = process_identity()
        now = time.time()
        rec = {"schema": SCHEMA, "pid": ident["pid"], "pid_created": ident["pid_created"], "by": str(by),
               "ts_utc": _utc(now), "ts_epoch": now}
        path = _state_dir(root) / f"sync.preempt.{ident['pid']}.json"
        return path if atomic_write_json(path, rec) else None
    except Exception:  # noqa: BLE001
        return None


def clear_preempt(path) -> None:
    """Remove a preempt record write_preempt returned (None: no-op). Never raises."""
    if not path:
        return
    _unlink(path)


def live_preempts(root, *, now=None) -> list:
    """The preempt records whose writer is provably alive (holder_live True) and that are recent (ts_epoch within
    PREEMPT_WAIT_S + 120 s). A record whose writer is provably gone is deleted. Never raises."""
    out = []
    try:
        t = time.time() if now is None else float(now)
        d = _state_dir(root)
        if not d.is_dir():
            return out
        for p in sorted(d.glob(PREEMPT_GLOB)):
            rec = read_json(p)
            if rec is None:
                continue
            live = holder_live(rec)
            if live is False:
                _unlink(p)
                continue
            try:
                fresh = float(rec.get("ts_epoch")) > t - (PREEMPT_WAIT_S + 120.0)
            except (TypeError, ValueError):
                fresh = False
            if live is True and fresh:
                out.append(rec)
    except Exception:  # noqa: BLE001
        return out
    return out


def preempt_wait(root, *, acquire, by: str, holder=None, say=None, wait_s=None, poll_s=None,
                 progress_s=None):
    """The waiting side of a preemption: write a preempt record, then try `acquire()` (a callable returning a lock
    handle or None) every `poll_s` until it returns a handle, the live holder is no longer DeepMap's background
    refresh (another user sync took the lock first — the caller then takes its usual path), or `wait_s` passed.
    `say(kind, **fmt)` is called with kind "progress" (fmt: phase, waited) every `progress_s`. The preempt record is
    removed in every case. Returns (handle, "acquired") | (None, "fallback") | (None, "timeout"), plus the holder's
    last known phase: (handle, status, phase). Never raises (a failing acquire counts as not acquired)."""
    wait_s = PREEMPT_WAIT_S if wait_s is None else float(wait_s)
    poll_s = PREEMPT_POLL_S if poll_s is None else float(poll_s)
    progress_s = PREEMPT_PROGRESS_S if progress_s is None else float(progress_s)
    phase = (holder or {}).get("phase") if isinstance(holder, dict) else None
    pf = write_preempt(root, by=by)
    t0 = time.monotonic()
    last_note = t0
    try:
        while True:
            try:
                handle = acquire()
            except Exception:  # noqa: BLE001
                handle = None
            if handle is not None:
                return handle, "acquired", phase
            h = read_running(root)
            if h is not None:
                phase = h.get("phase") or phase
                if h.get("live") is True and h.get("origin") != "unattended":
                    return None, "fallback", phase
            now = time.monotonic()
            if now - t0 >= wait_s:
                return None, "timeout", phase
            if say is not None and now - last_note >= progress_s:
                last_note = now
                try:
                    say("progress", phase=phase_words(phase), waited=duration_words(now - t0))
                except Exception:  # noqa: BLE001
                    pass
            time.sleep(max(0.01, min(poll_s, wait_s - (now - t0))))
    finally:
        clear_preempt(pf)


# ---- the embedding model cache ----------------------------------------------------------------------------------------
_FALLBACK_KEY_FILES = ("modules.json", "config.json", "model.safetensors", "tokenizer.json",
                       "tokenizer_config.json", "special_tokens_map.json", "sentence_bert_config.json",
                       "config_sentence_transformers.json")


def _hub_cache(env) -> Path:
    db = _lazy("deps_bootstrap")
    fn = getattr(db, "_hf_hub_cache", None) if db is not None else None
    if fn is not None:
        return Path(fn(env))
    for key in ("HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE"):
        if env.get(key):
            return Path(os.path.expandvars(os.path.expanduser(env[key])))
    hf_home = env.get("HF_HOME") or os.path.join(env.get("XDG_CACHE_HOME") or os.path.join(
        os.path.expanduser("~"), ".cache"), "huggingface")
    return Path(os.path.expandvars(os.path.expanduser(hf_home))) / "hub"


def _model_name():
    emb = _lazy("embeddings")
    return getattr(emb, "MODEL_NAME", None) if emb is not None else None


def model_cache_state(env=None) -> dict:
    """Is the embedding model (embeddings.MODEL_NAME) complete in the local Hugging Face cache — the snapshot
    refs/main names holding every deps_bootstrap.MODEL_KEY_FILES file plus each module's config.json that
    modules.json lists (Normalize has none: deps_bootstrap's rule)? {"complete", "revision" (the snapshot dir name),
    "path", "missing": [...]}. A stdlib file check: never imports huggingface_hub, never downloads, never raises."""
    e = os.environ if env is None else env
    try:
        name = _model_name()
        if not name:
            return {"complete": False, "revision": None, "path": None, "missing": ["embeddings.MODEL_NAME"]}
        db = _lazy("deps_bootstrap")
        key_files = tuple(getattr(db, "MODEL_KEY_FILES", None) or _FALLBACK_KEY_FILES)
        mdir = _hub_cache(e) / ("models--" + str(name).replace("/", "--"))
        ref = mdir / "refs" / "main"
        try:
            rev = ref.read_text(encoding="utf-8").strip() if ref.is_file() else ""
        except OSError:
            rev = ""
        if not rev:
            return {"complete": False, "revision": None, "path": None, "missing": ["refs/main"]}
        snap = mdir / "snapshots" / rev
        if not snap.is_dir():
            return {"complete": False, "revision": rev, "path": str(snap), "missing": [f"snapshots/{rev}"]}
        need = list(key_files)
        try:
            with open(snap / "modules.json", encoding="utf-8") as fh:
                for m in json.load(fh):
                    p, t = str(m.get("path") or ""), str(m.get("type") or "")
                    if p and not t.endswith("Normalize"):
                        need.append(p + "/config.json")
        except FileNotFoundError:
            pass
        except Exception:  # noqa: BLE001
            need.append("modules.json (unreadable)")
        missing = [f for f in need if not (snap / f).is_file()]
        return {"complete": not missing, "revision": rev, "path": str(snap), "missing": missing}
    except Exception:  # noqa: BLE001
        return {"complete": False, "revision": None, "path": None, "missing": ["unreadable"]}


# ---- build_recipe ---------------------------------------------------------------------------------------------------------
def strip_userinfo(uri):
    """`uri` without any user:password@ part (never recorded or compared). None stays None. Never raises."""
    if uri is None:
        return None
    s = str(uri)
    try:
        from urllib.parse import urlsplit, urlunsplit
        u = urlsplit(s)
        if "@" in (u.netloc or ""):
            return urlunsplit((u.scheme, u.netloc.rsplit("@", 1)[1], u.path, u.query, u.fragment))
        return s
    except Exception:  # noqa: BLE001
        return s.rsplit("@", 1)[1] if "@" in s else s


def _home_realpath(env) -> str | None:
    try:
        pr = _lazy("port_registry")
        home = pr.home_dir(env) if pr is not None else Path(env.get("DEEPMAP_HOME", str(Path.home() / ".deepmap")))
        return os.path.realpath(str(home))
    except Exception:  # noqa: BLE001
        return None


def make_build_recipe(*, engine: str, extraction, route_source, neo4j_uri, code_version, env=None) -> dict:
    """The manifest's build_recipe (spec §2.6.5): how this graph was built — the DeepMap version, the Python X.Y,
    the requested and resolved extractor, the graphify version, the embedding model and its cached snapshot, the
    non-default graph-shaping knobs, the route (source + URI without userinfo) and realpath(DEEPMAP_HOME). Never a
    password. Never raises."""
    e = os.environ if env is None else env
    info = extraction if isinstance(extraction, dict) else {}
    try:
        revision = model_cache_state(e).get("revision")
    except Exception:  # noqa: BLE001
        revision = None
    try:
        requested = canonical_env_value("DEEPMAP_EXTRACTOR", info.get("requested") or e.get("DEEPMAP_EXTRACTOR"))
    except Exception:  # noqa: BLE001
        requested = None
    return {
        "schema": SCHEMA,
        "deepmap_version": (str(code_version) if code_version else None),
        "python": f"{sys.version_info[0]}.{sys.version_info[1]}",
        "extractor_requested": requested,
        "engine": (str(engine) if engine else None),
        "graphify_version": (str(info.get("graphify_version")) if info.get("graphify_version") else None),
        "embed_model": _model_name(),
        "embed_model_revision": revision,
        "env": recipe_env(e),
        "route": {"source": (str(route_source) if route_source else None), "uri": strip_userinfo(neo4j_uri)},
        "deepmap_home": _home_realpath(e),
    }


# ---- the unattended holder's side of preemption ------------------------------------------------------------------------
class UnattendedControl:
    """What sync.sync(..., unattended=ctl) runs under: DeepMap's own background refresh (build/auto_sync.py).

    sync() drives it: after it takes the lock and writes sync_running.json it calls begin() (a daemon watcher) and
    checkpoint(to_phase="start"); enter("extract") before extracting; at the write checkpoint pre_write(engine,
    extraction) (a reason vetoes the push) then checkpoint(to_phase="write"); enter("embed") then before_embed()
    (False postpones the embeddings); enter("manifest"); end() before it releases the lock. While the phase is
    start/extract the watcher, every `poll_s`, stops the whole process (hard_exit, default os._exit) when a user's
    sync is waiting (live_preempts): nothing has been written to Neo4j yet, the OS releases every lock, the engine
    child dies with its job / watchdog. The watcher first writes sync_last.json (yield_fields: outcome "preempted",
    rc 0 — or "busy-enrich", rc 4, when only deepmap enrich runs waited), clears sync_running.json and calls
    on_hard_exit(record). checkpoint() and the watcher share one phase lock, so a
    pass never both enters "write" and hard-exits."""

    def __init__(self, root, *, trigger: str, session=None, prune_policy: str = "evidence", pre_write=None,
                 before_embed=None, on_hard_exit=None, poll_s=None, hard_exit=None):
        if prune_policy not in ("none", "evidence"):
            raise ValueError(f"an unattended sync prunes 'none' or 'evidence', never {prune_policy!r}")
        self.root = Path(root)
        self.trigger = trigger
        self.session = session
        self.prune_policy = prune_policy
        self.pre_write = pre_write
        self.before_embed = before_embed
        self.on_hard_exit = on_hard_exit
        self.poll_s = float(WATCH_POLL_S if poll_s is None else poll_s)
        self.hard_exit = hard_exit if hard_exit is not None else os._exit
        self.phase = "start"
        self.where = None                     # where it yielded: "start" | "extract" | "write"
        self.yielded_to = ()                  # the "by" of the live preempt records it yielded to ("sync" / "enrich")
        self._yielded = None
        self._plock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None

    # -- the watcher
    def begin(self) -> None:
        """Start the daemon watcher (idempotent while it runs)."""
        t = self._thread
        if t is not None and t.is_alive():
            return
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._watch, args=(self._stop,), name="deepmap-unattended-watch",
                                        daemon=True)
        self._thread.start()

    def end(self) -> None:
        """Stop the watcher (idempotent). Never raises."""
        try:
            self._stop.set()
            t = self._thread
            if t is not None and t is not threading.current_thread():
                t.join(timeout=max(1.0, self.poll_s * 3))
        except Exception:  # noqa: BLE001
            pass

    def _watch(self, stop) -> None:
        while not stop.wait(self.poll_s):
            try:
                with self._plock:
                    if self._yielded or self.phase not in HARD_EXIT_PHASES:
                        continue
                    live = live_preempts(self.root)
                    if not live:
                        continue
                    rec = read_running(self.root)
                    if not rec or rec.get("pid") != os.getpid():
                        continue                     # this process does not hold the lock: nothing to stop
                    self._yielded = "preempted"
                    self.where = self.phase
                    self._note_by(live)
                    self._hard_yield(rec)
                    return
            except Exception:  # noqa: BLE001 — the watcher never takes the process down by accident
                continue

    def _hard_yield(self, rec: dict) -> None:
        now = time.time()
        try:
            started = float(rec.get("started_epoch"))
        except (TypeError, ValueError):
            started = None
        y = self.yield_record()
        last = last_record(
            id=rec.get("id"), origin="unattended", trigger=self.trigger, pid=os.getpid(),
            started_utc=rec.get("started_utc"), finished_utc=_utc(now), finished_epoch=now,
            wall_s=(round(now - started, 3) if started is not None else None), passes=rec.get("pass") or 1,
            rc=y["rc"], outcome=y["outcome"], reason=y["reason"], preempted_by=y["preempted_by"],
            built_at_sha=rec.get("head_at_start"), source_fingerprint=rec.get("source_fingerprint"),
            prune_policy=self.prune_policy, deepmap_version=rec.get("deepmap_version"))
        write_last(self.root, last)
        clear_running(self.root)
        try:
            print(y["log"], file=sys.stderr)
        except Exception:  # noqa: BLE001
            pass
        if self.on_hard_exit is not None:
            try:
                self.on_hard_exit(last)
            except Exception:  # noqa: BLE001
                pass
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.flush()
            except Exception:  # noqa: BLE001
                pass
        self.hard_exit(0)

    # -- the phases
    def enter(self, phase: str) -> None:
        """Set the phase (under the phase lock) and record it in sync_running.json."""
        with self._plock:
            self.phase = phase
        update_running(self.root, phase=phase)

    def checkpoint(self, *, to_phase: str):
        """Atomically: a user's sync is waiting (or this control already yielded) -> mark it yielded and return
        "preempted"; else set the phase to `to_phase` and return None."""
        with self._plock:
            if self._yielded:
                return self._yielded
            live = live_preempts(self.root)
            if live:
                self._yielded = "preempted"
                self.where = to_phase if to_phase in YIELD_WHERE else self.phase
                self._note_by(live)
                return self._yielded
            self.phase = to_phase
        update_running(self.root, phase=to_phase)
        return None

    def mark_yielded(self, where: str = "start") -> None:
        """Yield without a checkpoint (recorded as a hand-over to a user's sync: yield_fields with no "by")."""
        with self._plock:
            if not self._yielded:
                self._yielded = "preempted"
                self.where = where

    def yielded(self):
        """"preempted" once this control yielded, else None."""
        return self._yielded

    def _note_by(self, live) -> None:
        """Remember who it yields to: the "by" of the live preempt records (called under the phase lock)."""
        try:
            self.yielded_to = tuple(sorted({str(r.get("by")) for r in live if isinstance(r, dict) and r.get("by")}))
        except Exception:  # noqa: BLE001
            self.yielded_to = ()

    def yield_record(self) -> dict:
        """How this yield is recorded (yield_fields): "preempted" rc 0 for a user's sync / setup, "busy-enrich" rc 4
        when only deepmap enrich runs waited — plus "preempted_by", the reason and the log line."""
        return yield_fields(self.yielded_to, self.where or "start")
