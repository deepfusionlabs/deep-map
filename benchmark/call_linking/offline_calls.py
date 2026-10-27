#!/usr/bin/env python3
"""Offline (no Neo4j) dump of the exact :CALLS edge set `deepmap sync` would push for a corpus.

Mirrors build/sync.py `_sync_pass` (the body of `sync()`) from extraction to the push, step for step:

    _rebuild_code -> enrich.apply_sidecar -> _apply_recognizers -> _unkey_receiver_calls
      -> _typed_calls_link (design §2.8 step 2; only when build/sync.py has it)
      -> _attach_bare_calls -> _attach_local_defs -> _attach_local_def_calls
      -> push_to_neo4j, whose first step is the PURE `_resolve_calls(nodes, calls)`: this tool returns
         exactly that dict (push_to_neo4j writes exactly it).

The typed-call step is reached through `sync._typed_calls_link` when sync defines it (slice S2 adds it between
`_unkey_receiver_calls` and `_attach_bare_calls`; it imports build/typed_calls.py lazily and is a no-op without
it). Until then sync has no such step, and neither does this mirror — so the tool follows the current AND the
future order. tests/call_linking_harness_gates.py reads both files' ASTs and accepts exactly these two orders,
and drives the REAL sync() pass with a fake driver to prove the pushed edges equal this tool's.

Flags come from the environment (use variants.py for a clean one). DEEPMAP_HOME must name a scratch home (the
engine writes <DEEPMAP_HOME>/gout/<key>/); the operator's ~/.deepmap is refused. A corpus under a read-only
clone root (CALL_LINKING_READONLY_ROOTS, os.pathsep-separated; default C:/tmp/deepmap-bench-clones) is refused:
copy it with `git clone --no-hardlinks` first.

The dump is deterministic (sorted, no timings): two runs on the same tree, code, engine and flags must be
byte-identical (design §8.4 G). The determinism check is about graph CONTENT; a timing is P-layer data and goes to
<out>.timing.json: t_extract_s / t_typed_s / t_total_s (this tool's own clocks) and call_linking_seconds (the typed
pass's own `seconds`, as build/typed_calls.py measures it). typed_calls.link's stats carry that `seconds` — "the only
non-deterministic value" — and make_dump leaves it out of the dump's `call_linking` block (WALL_CLOCK_FIELDS). Until
2026-10-24 the block copied it verbatim, so two runs of a typed variant differed in it alone; strip_wall_clock turns
such a pre-fix dump into what make_dump writes now, for re-judging its pair.

Exit codes of `build`: 0 data; 2 refused (nothing written); 3 VOID — the dump is written with a `void` reason and
is NOT data: the extraction did not resolve to --expect-engine (engine_void), or the variant's flags were inert
on the code under test (variant_void: a flag no build/*.py file names, the typed-calls step absent or erroring
under DEEPMAP_TYPED_CALLS / DEEPMAP_CALLS_AUDIT, a PRECISE family list the code turned into no family, a
requested switch the product's own predicate left off — the cross-family filter, a typed-calls shape, TESTS);
4 INVARIANT — the dump is data but breaks a grading invariant (`invariant`: an EXTRACTED edge whose caller and
callee sit in different files, design §4) — the variant FAILS whatever else it measures.

usage:
  python offline_calls.py build <corpus> <out.json> [--build <build dir>] [--expect-engine graphify]
      (with --expect-engine, a dump whose extraction resolved to another engine or fell back is VOID: exit 3)
  python offline_calls.py codehash [--build <build dir>]        # sha256 of build/*.py (PREREG RC identity)
  python offline_calls.py order                                   # the step order this tool mirrors
  python offline_calls.py stage0-corpus <dest> [--repo-id self-graphify-build]
      # the registered STAGE-0 corpus (tests/oracles/registry.json) exactly as tests/stage0_refreeze.py
      # materializes it: `git archive <sha>:build` — build/'s CONTENTS at the root, a non-git tree
"""
from __future__ import annotations

import argparse
import contextlib
import os
import re
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as C  # noqa: E402

# The order mirrored (sync step names; "_resolve_calls" stands for push_to_neo4j's first step).
PIPELINE = ("_rebuild_code", "enrich.apply_sidecar", "_apply_recognizers", "_unkey_receiver_calls",
            "_typed_calls_link", "_attach_bare_calls", "_attach_local_defs", "_attach_local_def_calls",
            "_resolve_calls")
DEFAULT_READONLY_ROOTS = "C:/tmp/deepmap-bench-clones"
NODE_FIELDS = ("key", "label", "name", "source_file", "start_line", "end_line")


def load_sync(build_dir=None):
    """Import build/sync.py from `build_dir` (default: this repository's build/)."""
    b = str(Path(build_dir or C.BUILD).resolve())
    if b not in sys.path:
        sys.path.insert(0, b)
    import sync  # noqa: WPS433
    return sync


def typed_calls_step(S, root, nodes, calls):
    """Design §2.8 step 2, exactly as sync runs it: `calls, stats = sync._typed_calls_link(root, nodes, calls)`.
    A sync without the helper (today's order) has no such step: (calls, None), the list unchanged."""
    fn = getattr(S, "_typed_calls_link", None)
    if not callable(fn):
        return calls, None
    out = fn(root, nodes, calls)
    if isinstance(out, tuple) and len(out) == 2:
        return out[0], out[1]
    return out, None


def run_pipeline(S, root, *, extraction=None):
    """The mirrored passes over `root`. `extraction` (a sync._Extraction or a (nodes, calls, imports, engine)
    tuple) replaces `_rebuild_code(root)` — the gate feeds sync() and this function the same frozen one.
    Returns (nodes, calls, edges, meta)."""
    t0 = time.perf_counter()
    extracted = S._rebuild_code(root) if extraction is None else extraction
    nodes, calls, imports, engine = extracted
    info = getattr(extracted, "info", None)
    t_extract = time.perf_counter() - t0
    S.enrich.apply_sidecar(root, nodes)
    nodes, typed_edges = S._apply_recognizers(root, nodes)
    calls, n_unkey = S._unkey_receiver_calls(root, nodes, calls)
    t_typed0 = time.perf_counter()
    calls, call_stats = typed_calls_step(S, root, nodes, calls)
    t_typed = time.perf_counter() - t_typed0
    n_bare = S._attach_bare_calls(root, nodes, calls)
    S._attach_local_defs(root, nodes, calls)
    n_local = S._attach_local_def_calls(root, nodes, calls)
    edges = S._resolve_calls(nodes, calls)
    meta = {"engine": engine, "info": info if isinstance(info, dict) else {}, "n_unkey": n_unkey,
            "n_bare": n_bare, "n_local": n_local, "call_stats": call_stats,
            "typed_step": "present" if callable(getattr(S, "_typed_calls_link", None)) else "absent",
            "n_imports": len(imports or []), "n_typed_edges": len(typed_edges or []),
            "t_extract_s": round(t_extract, 2), "t_typed_s": round(t_typed, 2),
            "t_total_s": round(time.perf_counter() - t0, 2)}
    return nodes, calls, edges, meta


# The typed-calls configuration fields effective_switches reports (typed_calls._config, the one reader of
# DEEPMAP_TYPED_CALLS_SHAPES / _REPLACE / _TESTS / _NONPROD / _MAX_FILES): effective key -> _Config field.
TYPED_CONFIG_FIELDS = (("typed_shapes", "shapes"), ("typed_replace", "replace"), ("typed_tests", "tests"),
                       ("typed_nonprod", "nonprod"), ("typed_max_files", "max_files"))


def _plain(v):
    return sorted(v) if isinstance(v, (set, frozenset)) else v


def effective_switches(S) -> dict:
    """What the code under test actually turned ON for this run's environment — a variant whose flags the code
    does not understand (e.g. DEEPMAP_PRECISE_CALLS=python,js before slice S1's family parser) would otherwise
    silently equal V0. Each probe is read through the product's own predicate when it exists (None: absent): the
    precise / keyed / CALLS_PLUS switches (graphify_extract), the cross-family filter (sync), the typed and audit
    languages and the typed-calls configuration (typed_calls._config: shapes, replace, tests, nonprod, max_files).
    Every §8.3 variant sets something reported here, so two variants with different flags have different blocks
    (delta.py VOIDs a pair whose flags differ while the blocks are equal — an inert variant)."""
    gx = getattr(S, "graphify_extract", None)
    out = {}
    try:
        import typed_calls as tc  # noqa: WPS433 — lazy, optional (slice S3a)
    except ImportError:
        tc = None
    except Exception:  # noqa: BLE001
        tc = None
    for name, owner, attr in (("precise_calls", gx, "precise_calls_enabled"),
                              ("keyed_calls", gx, "keyed_calls_enabled"),
                              ("precise_families", gx, "precise_calls_families"),
                              ("precise_fixes", gx, "precise_fixes_enabled"),
                              ("calls_plus", gx, "graphify_calls_plus_enabled"),
                              ("calls_family_filter", S, "_calls_family_filter_enabled"),
                              ("typed_languages", tc, "typed_langs"),
                              ("audit_languages", tc, "audit_langs")):
        fn = getattr(owner, attr, None) if owner is not None else None
        try:
            v = fn() if callable(fn) else None
        except Exception as exc:  # noqa: BLE001
            v = f"error: {type(exc).__name__}"
        out[name] = _plain(v)
    cfg_fn = getattr(tc, "_config", None) if tc is not None else None
    try:
        cfg = cfg_fn(None, tc.LANGUAGES) if callable(cfg_fn) else None
        cfg_err = None
    except Exception as exc:  # noqa: BLE001
        cfg, cfg_err = None, f"error: {type(exc).__name__}"
    for name, field in TYPED_CONFIG_FIELDS:
        out[name] = cfg_err or (_plain(getattr(cfg, field, None)) if cfg is not None else None)
    out["typed_step"] = "present" if callable(getattr(S, "_typed_calls_link", None)) else "absent"
    return out


def _git(root: Path, *args):
    try:
        p = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=120)
        return p.stdout.strip() if p.returncode == 0 else None
    except Exception:  # noqa: BLE001
        return None


def corpus_state(root: Path) -> dict:
    head = _git(root, "rev-parse", "HEAD")
    dirty = _git(root, "status", "--porcelain", "--untracked-files=no") if head else None
    return {"head": head, "dirty": bool(dirty) if dirty is not None else None}


def extracted_cross_file(nodes, edges) -> list:
    """Sorted [caller, callee] pairs graded EXTRACTED whose caller and callee sit in different files. Design §4:
    EXTRACTED needs the caller, the target and every evidence link in the caller's file, so any such edge is
    grade inflation — exact and cheap on every dump, where the audit only samples."""
    file_of = {n.get("key"): C.norm_path(n.get("source_file")) for n in nodes}
    return sorted([str(a), str(b)] for (a, b), g in edges.items()
                  if g == "EXTRACTED" and file_of.get(a) != file_of.get(b))


# The wall-clock values a dump never carries, {dump section: (field, ...)}: typed_calls.link's stats hold the pass's
# own wall time `seconds` (build/typed_calls.py: "the only non-deterministic value"). A timing is P-layer data: it
# goes to <out>.timing.json (timing_sidecar), never into the dump, whose bytes two runs must reproduce exactly.
WALL_CLOCK_FIELDS = {"call_linking": ("seconds",)}


def _without_wall_clock(section, block):
    """`block` (a dump section) without the WALL_CLOCK_FIELDS of `section`: a new dict, the input untouched."""
    drop = WALL_CLOCK_FIELDS.get(section) or ()
    if not isinstance(block, dict) or not any(k in block for k in drop):
        return block
    return {k: v for k, v in block.items() if k not in drop}


def strip_wall_clock(dump: dict) -> dict:
    """`dump` without its wall-clock fields (WALL_CLOCK_FIELDS) — exactly what make_dump writes since 2026-10-24.
    A dump of the pre-fix kit carried typed_calls' `call_linking.seconds`; stripped, its bytes (C.dump_json) are the
    graph content the determinism check compares. Returns a shallow copy; the input is never changed."""
    if not isinstance(dump, dict):
        return dump
    out = dict(dump)
    for section in WALL_CLOCK_FIELDS:
        if section in out:
            out[section] = _without_wall_clock(section, out[section])
    return out


def timing_sidecar(meta) -> dict:
    """The P-layer timings of one run, for <out>.timing.json: this tool's own extract / typed-step / total seconds
    and the typed pass's own `seconds` (call_linking_seconds; None when no typed step ran or it reported none)."""
    stats = meta.get("call_stats")
    secs = stats.get("seconds") if isinstance(stats, dict) else None
    out = {k: meta.get(k) for k in ("t_extract_s", "t_typed_s", "t_total_s")}
    out["call_linking_seconds"] = secs if isinstance(secs, (int, float)) and not isinstance(secs, bool) else None
    return out


def make_dump(root: Path, nodes, calls, edges, meta, *, code=None) -> dict:
    """The dump of one run: graph content and run identity only, never a wall-clock value (WALL_CLOCK_FIELDS go to
    the timing sidecar instead), so two runs on the same tree, code, engine and flags are byte-identical."""
    conf = Counter(edges.values())
    keyed =sum(1 for _c, callee in calls if getattr(callee, "_DEEPMAP_CALL_KEY", False) is True)
    info = meta.get("info") or {}
    xfile = extracted_cross_file(nodes, edges)
    return {
        "schema": C.DUMP_SCHEMA,
        "corpus": {"root": C.norm_path(root), **corpus_state(root)},
        "code": code,
        "engine": meta.get("engine"),
        "extraction": {k: info.get(k) for k in ("requested", "resolved", "fallback_reason", "timed_out")},
        "graphify_version": info.get("graphify_version"),
        "python": ".".join(str(x) for x in sys.version_info[:3]),
        "flags": C.env_flags(),
        "effective": meta.get("effective"),
        "pipeline":[s for s in PIPELINE if s != "_typed_calls_link" or meta.get("typed_step") == "present"],
        "typed_step": meta.get("typed_step"),
        "call_linking": _without_wall_clock("call_linking", meta.get("call_stats")),
        "counts": {
            "n_nodes": len(nodes), "n_call_tuples": len(calls), "n_keyed_tuples": keyed,
            "n_edges": len(edges), "conf": dict(sorted(conf.items())),
            "ambiguous_share": round(conf.get("AMBIGUOUS", 0) / max(1, len(edges)), 4),
            "n_unkey": meta.get("n_unkey"), "n_bare": meta.get("n_bare"), "n_local": meta.get("n_local"),
            "n_call_typed_nodes": sum(1 for n in nodes if n.get("call_typed")),
            "n_call_weak_nodes": sum(1 for n in nodes if n.get("call_weak")),
            "n_imports": meta.get("n_imports"), "n_typed_edges": meta.get("n_typed_edges"),
            "extracted_cross_file": len(xfile),
        },
        "invariant": ({"extracted_cross_file": xfile[:50]} if xfile else None),
        "nodes": sorted(({k: n.get(k) for k in NODE_FIELDS} for n in nodes),
                        key=lambda n: (str(n["key"]), str(n["label"]), str(n["source_file"]), n["start_line"] or 0)),
        "edges": sorted([str(a), str(b), str(c)] for (a, b), c in edges.items()),
    }


def _home_problem() -> "str | None":
    home = os.environ.get("DEEPMAP_HOME", "").strip()
    if not home:
        return "set DEEPMAP_HOME to a scratch directory (the engine writes <DEEPMAP_HOME>/gout/)"
    try:
        if Path(home).resolve() == (Path.home() / ".deepmap").resolve():
            return f"DEEPMAP_HOME={home} is the operator's own DeepMap home — use a scratch directory"
    except Exception as exc:  # noqa: BLE001
        return f"DEEPMAP_HOME={home} could not be resolved ({exc})"
    return None


def _readonly_problem(root: Path) -> "str | None":
    roots = os.environ.get("CALL_LINKING_READONLY_ROOTS", DEFAULT_READONLY_ROOTS)
    r = os.path.normcase(str(root.resolve()))
    for ro in [x for x in roots.split(os.pathsep) if x.strip()]:
        try:
            base = os.path.normcase(str(Path(ro).resolve()))
        except Exception:  # noqa: BLE001
            continue
        if r == base or r.startswith(base.rstrip("\\/") + os.sep):
            return (f"{root} is under the read-only clone root {ro} — copy it first "
                    f"(git clone --no-hardlinks <clone> <scratch>/<name>)")
    return None


def cmd_build(args) -> int:
    root = Path(args.corpus).resolve()
    problem = _home_problem() or (None if args.allow_readonly else _readonly_problem(root))
    if problem:
        print(f"[offline_calls] REFUSED: {problem}", file=sys.stderr)
        return 2
    if not root.is_dir():
        print(f"[offline_calls] REFUSED: {root} is not a directory", file=sys.stderr)
        return 2
    S = load_sync(args.build)
    sroot = S._sync_root(root)
    if sroot != root:
        print(f"[offline_calls] {root} is inside {sroot}, which sync syncs as a whole — dumping that",
              file=sys.stderr)
    code_dir = Path(S.__file__).parent
    code = C.build_code_hash(code_dir)
    nodes, calls, edges, meta = run_pipeline(S, sroot)
    meta["effective"] = effective_switches(S)
    dump = make_dump(sroot, nodes, calls, edges, meta, code=code)
    void = "; ".join(v for v in (engine_void(dump, args.expect_engine), variant_void(dump, code_text(code_dir)))
                     if v) or None
    if void:
        dump["void"] = void
    C.write_json(args.out, dump)
    timing = timing_sidecar(meta)                 # P-layer data: never in the dump (WALL_CLOCK_FIELDS)
    C.write_json(str(args.out) + ".timing.json", timing)
    summary = {k: dump[k] for k in ("engine", "graphify_version", "flags", "effective")}
    summary.update(dump["counts"])
    summary.update(timing)
    print(C.dump_json(summary), end="")
    rc = exit_code(dump)
    if rc == 3:
        print(f"[offline_calls] VOID: {dump['void']}", file=sys.stderr)
    elif rc == 4:
        print(f"[offline_calls] INVARIANT BROKEN: {dump['counts']['extracted_cross_file']} EXTRACTED edge(s) cross "
              f"files (design §4), e.g. {dump['invariant']['extracted_cross_file'][:3]}", file=sys.stderr)
    return rc


def exit_code(dump) -> int:
    """`build`'s exit code for a written dump: 3 VOID (not data), 4 INVARIANT (data; the variant fails), 0."""
    if dump.get("void"):
        return 3
    if dump.get("invariant"):
        return 4
    return 0


def engine_void(dump, expect) -> "str | None":
    """Why a dump is not data for an engine-pinned measurement, or None. `auto` silently falls back to the ast
    engine when graphify fails (a path over Windows MAX_PATH, a timeout under load, a tree it finds no code in),
    and that dump then measures another engine: with --expect-engine it is VOID (exit 3), never scored. The
    message reports the extraction's own fallback reason; it does not guess the cause."""
    if not expect:
        return None
    ex = dump.get("extraction") or {}
    if dump.get("engine") != expect:
        return (f"the extraction resolved to {dump.get('engine')!r}, not {expect!r} "
                f"(requested {ex.get('requested')!r}; fallback reason: {ex.get('fallback_reason')!r}; "
                f"timed out: {bool(ex.get('timed_out'))})")
    if ex.get("fallback_reason") or ex.get("timed_out"):
        return (f"the extraction fell back or timed out (fallback reason: {ex.get('fallback_reason')!r}; "
                f"timed out: {bool(ex.get('timed_out'))})")
    return None


_OFF_VALUES = ("", "0", "false", "no", "off")
_OFF_WORDS = ("0", "false", "no", "off")       # an explicit OFF (unset / blank keeps a switch's default)
_ALL_VALUES = ("1", "true", "yes", "on")


def _is_on(value) -> bool:
    return str(value if value is not None else "").strip().lower() not in _OFF_VALUES


def _csv(value) -> set:
    return {x.strip().lower() for x in str(value or "").split(",") if x.strip()}


def code_text(code_dir) -> str:
    """The text of every build/*.py of the code under test (what variant_void searches for flag names)."""
    out = []
    for p in sorted(Path(code_dir).glob("*.py")):
        try:
            out.append(p.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            pass
    return "\n".join(out)


def variant_void(dump, code=None) -> "str | None":
    """Why a variant's dump measured code that IGNORED its flags (PREREG §5: an inert variant equals V0 and must
    never be scored as "the feature had no effect"), or None. `code` is the text of the code under test
    (code_text); None skips the flag-name rule. Rules:
      - a DEEPMAP_* flag of the variant that no build/*.py file names: the code cannot read it;
      - DEEPMAP_TYPED_CALLS or DEEPMAP_CALLS_AUDIT on, but sync has no `_typed_calls_link` step, the step
        returned no stats, or its stats carry an `error` (typed_calls.link returns its input unchanged then);
      - DEEPMAP_TYPED_CALLS / DEEPMAP_CALLS_AUDIT naming languages the product's own predicate did not turn on;
      - DEEPMAP_PRECISE_CALLS given as a family list while the product's effective family set is empty;
      - a switch the variant requests that the product left off (_switches_left_off): DEEPMAP_CALLS_FAMILY_FILTER,
        DEEPMAP_TYPED_CALLS_TESTS / _NONPROD, DEEPMAP_GRAPHIFY_CALLS_PLUS or DEEPMAP_PRECISE_CALLS on while its
        effective switch is not True, DEEPMAP_TYPED_CALLS_REPLACE=0 while the product still replaces, a
        DEEPMAP_TYPED_CALLS_SHAPES shape the product did not turn on."""
    flags = dump.get("flags") or {}
    eff = dump.get("effective") or {}
    why = []
    if code is not None:
        inert = sorted(k for k in flags if not re.search(r"(?<![A-Za-z0-9_])" + re.escape(k) + r"(?![A-Za-z0-9_])",
                                                         code))
        if inert:
            why.append(f"no build/*.py file names {', '.join(inert)} (the code under test cannot read it)")
    typed, audit = flags.get("DEEPMAP_TYPED_CALLS"), flags.get("DEEPMAP_CALLS_AUDIT")
    if _is_on(typed) or _is_on(audit):
        cl = dump.get("call_linking")
        if dump.get("typed_step") != "present":
            why.append("DEEPMAP_TYPED_CALLS / DEEPMAP_CALLS_AUDIT is on but build/sync.py has no _typed_calls_link "
                       "step (typed_step absent)")
        elif not isinstance(cl, dict):
            why.append("the typed-calls step returned no stats (call_linking is null)")
        elif cl.get("error"):
            why.append(f"the typed-calls step hit an internal error and returned its input unchanged "
                       f"({str(cl.get('error'))[:200]})")
        for name, value, key in (("DEEPMAP_TYPED_CALLS", typed, "typed_languages"),
                                 ("DEEPMAP_CALLS_AUDIT", audit, "audit_languages")):
            got = eff.get(key)
            if not _is_on(value) or not isinstance(got, list):
                continue
            want = set() if str(value).strip().lower() in _ALL_VALUES else _csv(value)
            missing = sorted(want - {str(x).lower() for x in got})
            if missing or not got:
                why.append(f"{name}={value} but the product turned on {key}={got}"
                           + (f" (missing: {', '.join(missing)})" if missing else ""))
    precise = flags.get("DEEPMAP_PRECISE_CALLS")
    if _is_on(precise) and str(precise).strip().lower() not in _ALL_VALUES and not eff.get("precise_families"):
        why.append(f"DEEPMAP_PRECISE_CALLS={precise} names families but the product's effective family set is "
                   f"{eff.get('precise_families')!r}")
    why += _switches_left_off(flags, eff)
    return "; ".join(why) or None


# A switch the variant sets -> (the `effective` key reporting it, the value the product must report). Checked only
# when the dump's `effective` block carries the key (a dump of an older kit does not; it is judged by delta's
# whole-block rule).
_ON_SWITCHES = (("DEEPMAP_CALLS_FAMILY_FILTER", "calls_family_filter"), ("DEEPMAP_TYPED_CALLS_TESTS", "typed_tests"),
                ("DEEPMAP_TYPED_CALLS_NONPROD", "typed_nonprod"), ("DEEPMAP_GRAPHIFY_CALLS_PLUS", "calls_plus"),
                ("DEEPMAP_PRECISE_CALLS", "precise_calls"))


def _switches_left_off(flags, eff) -> list:
    """The reasons a variant's requested switch was left off by the product: an ON-word (1/true/yes/on; for
    DEEPMAP_PRECISE_CALLS any on-value) whose effective switch is not True; DEEPMAP_TYPED_CALLS_REPLACE given as an
    OFF-word while the product still replaces; DEEPMAP_TYPED_CALLS_SHAPES naming a shape the product did not turn on."""
    why = []
    for name, key in _ON_SWITCHES:
        value = flags.get(name)
        on = _is_on(value) if name == "DEEPMAP_PRECISE_CALLS" else str(value or "").strip().lower() in _ALL_VALUES
        if on and key in eff and eff.get(key) is not True:
            why.append(f"{name}={value} but the product left {key} off ({eff.get(key)!r})")
    replace = flags.get("DEEPMAP_TYPED_CALLS_REPLACE")
    off = str(replace or "").strip().lower() in _OFF_WORDS
    if off and "typed_replace" in eff and eff.get("typed_replace") is not False:
        why.append(f"DEEPMAP_TYPED_CALLS_REPLACE={replace} but the product left typed_replace on "
                   f"({eff.get('typed_replace')!r})")
    shapes = flags.get("DEEPMAP_TYPED_CALLS_SHAPES")
    if _is_on(shapes) and "typed_shapes" in eff:
        got = eff.get("typed_shapes")
        want = {x.strip().upper() for x in str(shapes).split(",") if x.strip()}
        missing = sorted(want - {str(x).upper() for x in got}) if isinstance(got, list) else sorted(want)
        if missing:
            why.append(f"DEEPMAP_TYPED_CALLS_SHAPES={shapes} but the product turned on typed_shapes={got!r} "
                       f"(missing: {', '.join(missing)})")
    return why


def stage0_corpus(dest, repo_id="self-graphify-build") -> dict:
    """Materialize the REGISTERED STAGE-0 corpus `repo_id` into `dest` with tests/stage0_portable.py's own
    materialize_corpus (the function tests/stage0_refreeze.py runs): the record's `tree` subdirectory's CONTENTS
    at the root (keys as the oracle has them), a non-git tree. Returns {repo_id, corpus, files}."""
    tests = str(C.REPO / "tests")
    if tests not in sys.path:
        sys.path.insert(0, tests)
    import stage0_portable as P  # noqa: WPS433 — the registry's own reader + materializer
    reg = P.load_registry(C.REPO / P.REGISTRY_RELPATH)
    corpus = ((reg.get("oracles") or {}).get(repo_id) or {}).get("corpus")
    if not corpus:
        raise KeyError(f"no corpus record for {repo_id!r} in {P.REGISTRY_RELPATH}")
    n = P.materialize_corpus(C.REPO, corpus, dest)
    return {"repo_id": repo_id, "corpus": corpus, "files": n}


def main(argv=None) -> int:
    for s in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            s.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Offline :CALLS dump mirroring build/sync.py _sync_pass.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="dump the :CALLS edges sync would push for <corpus>")
    b.add_argument("corpus")
    b.add_argument("out")
    b.add_argument("--build", default=None, help="the build/ directory to run (default: this repo's)")
    b.add_argument("--expect-engine", default=None, choices=("graphify", "ast"),
                   help="VOID (exit 3) unless the extraction resolved to this engine with no fallback")
    b.add_argument("--allow-readonly", action="store_true", help=argparse.SUPPRESS)
    h = sub.add_parser("codehash", help="sha256 of build/*.py")
    h.add_argument("--build", default=None)
    sub.add_parser("order", help="print the mirrored step order")
    m = sub.add_parser("stage0-corpus", help="materialize the registered STAGE-0 corpus into <dest>")
    m.add_argument("dest")
    m.add_argument("--repo-id", default="self-graphify-build")
    args = ap.parse_args(argv)
    if args.cmd == "build":
        return cmd_build(args)
    if args.cmd == "codehash":
        print(C.dump_json(C.build_code_hash(args.build)), end="")
        return 0
    if args.cmd == "stage0-corpus":
        try:
            res = stage0_corpus(args.dest, args.repo_id)
        except Exception as exc:  # noqa: BLE001 — git missing the commit, a non-empty dest, an unknown id
            print(f"[offline_calls] stage0-corpus FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 2
        print(C.dump_json(res), end="")
        return 0
    print("\n".join(PIPELINE))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
