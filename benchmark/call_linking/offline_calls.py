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

D3-cs (benchmark/call_linking/PREREG_CS.md §8.4): the `effective` block also reports `typed_iface`, C#'s
interface-dispatch modes as typed_calls._config read DEEPMAP_TYPED_CALLS_IFACE (static / impl; [] for none), so VT-cs,
VT-cs-ifs and VT-cs-if0 are never an inert pair, and a DEEPMAP_TYPED_CALLS_IFACE the product did not turn on exactly
is VOID; and a dump whose DEEPMAP_TYPED_CALLS names csharp, of a C# corpus (a node in a `.cs` file), is VOID when its
call_linking stats show csharp disabled for any reason (langs_disabled: no reader, canary, binder, max_files) or no C#
file read (csharp_void).

usage:
  python offline_calls.py build <corpus> <out.json> [--build <build dir>] [--expect-engine graphify]
      (with --expect-engine, a dump whose extraction resolved to another engine or fell back is VOID: exit 3)
  python offline_calls.py codehash [--build <build dir>]        # sha256 of build/*.py (PREREG RC identity)
  python offline_calls.py order                                   # the step order this tool mirrors
  python offline_calls.py stage0-corpus <dest> [--repo-id self-graphify-build]
      # the registered STAGE-0 corpus (tests/oracles/registry.json) exactly as tests/stage0_refreeze.py
      # materializes it: `git archive <sha>:build` — build/'s CONTENTS at the root, a non-git tree
  python offline_calls.py identity <V0.json> <VT-cs.json> [--corpus <root>] [--rule auto|N1|N2] [--json out.json]
      # PREREG_CS N1 / N2 (the byte comparison of the N layer): N1 = nodes, edges, counts and invariant byte-identical
      # and neither dump VOID (a corpus without a .cs file); N2 = every edge whose caller's file is not .cs identical,
      # pair and grade (a C# corpus, or an N corpus holding a .cs file: `self`). --corpus lists the corpus's .cs
      # files (git ls-files, else a walk) to pick the rule. Exit 0 holds, 1 fails, 3 a dump is VOID
  python offline_calls.py kithash [--kit <benchmark/call_linking dir>] [--truth <truth.json>] [--json out.json]
                                  [--build <build dir> [--record <rc_identity.json>]]
      # PREREG_CS §1 / AC7: the kit hash (every file of the kit dir, truth_roslyn/ included; __pycache__/,
      # truth_roslyn/bin/, truth_roslyn/obj/, RESULTS_CS.md, DECISION_CS.md and cs/ excluded; byte order of the
      # forward-slash relative paths, each its path, a NUL, its bytes and a NUL) and the reference tool identity (the
      # same scheme over truth_roslyn.py + truth_roslyn/{Program.cs, TruthRoslyn.csproj, global.json, nuget.config},
      # checked equal to that kit's own `truth_roslyn.py identity`); --truth adds the versions the truth's resolver
      # block records, checked against the tool sources it was built from. Run it on the RC snapshot's kit. --build
      # adds build/*.py's code hash; --record writes the RC identity (code + kit hash + reference identity, no path)
      # into a new file, or checks it against an existing one (run_matrix.sh's out/rc_identity.json). Exit 0, 1 a
      # mismatch (identity, truth or record), 2 refused
  python offline_calls.py manifest <.../benchmark/call_linking/cs> [--write]
      # PREREG_CS §8.7 G5b / AC15: cs/MANIFEST.sha256 (`<sha256>  <path relative to cs/>`, forward slashes, byte order
      # of the paths, LF; never the manifest itself or a sheet.md) over every file placed under cs/, with
      # cs/.gitattributes (`* -text`) placed first and listed. Without --write nothing is written: it prints the
      # manifest (or checks an existing one). --write (the RC run's G5b step only) writes .gitattributes when absent
      # and the manifest when absent; an existing different manifest is refused (delete it by hand to redo). Refused
      # (exit 2): a sheet.md, a dump or truth file, a symbolic link, a wrong .gitattributes, an empty folder. Prints
      # the line DECISION_CS.md records. Exit 0 written / matches, 1 an existing manifest differs
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
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
# DEEPMAP_TYPED_CALLS_SHAPES / _REPLACE / _TESTS / _NONPROD / _MAX_FILES / _IFACE): effective key -> _Config field.
# typed_iface is C#'s dispatch modes (typed_calls.IFACE_MODES: static = the statically bound member, interface or
# virtual; impl = its project implementers / the overrides — AC9: DEEPMAP_TYPED_CALLS_IFACE governs interface AND virtual
# dispatch; [] = none), which the product reads only while a language with that rule is on (PREREG_CS §8.4: so VT-cs,
# VT-cs-ifs and VT-cs-if0 are never an inert pair); None on a tree without the field.
TYPED_CONFIG_FIELDS = (("typed_shapes", "shapes"), ("typed_replace", "replace"), ("typed_tests", "tests"),
                       ("typed_nonprod", "nonprod"), ("typed_max_files", "max_files"), ("typed_iface", "iface"))


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
    why += csharp_void(dump)
    return "; ".join(why) or None


CSHARP = "csharp"


def is_csharp_corpus(dump) -> bool:
    """A C# corpus for the VOID rule below: the dump holds a node in a `.cs` file (case-insensitive). The D3-cs
    corpora (PREREG_CS §8.2) hold thousands; a corpus without C# source never meets the rule."""
    return any(C.is_cs_path(n.get("source_file")) for n in (dump.get("nodes") or []))


def csharp_void(dump) -> list:
    """PREREG_CS §8.4's C# VOID rule: a dump whose DEEPMAP_TYPED_CALLS names csharp (VT-cs and its family: shapes,
    +t, +np, -ifs, -if0, VRC-cs), of a C# corpus (is_csharp_corpus), whose call_linking stats show csharp disabled for
    any reason — typed_calls' langs_disabled (no reader, a failed grammar canary, a failed binder, max_files) — or
    show no C# file read at all (by_lang.csharp absent, or 0 files: the pass did not run on the corpus's C#), is
    VOID. A typed step that erred or returned no stats is VOID by variant_void's own rules."""
    flags = dump.get("flags") or {}
    if CSHARP not in _csv(flags.get("DEEPMAP_TYPED_CALLS")) or not is_csharp_corpus(dump):
        return []
    cl = dump.get("call_linking")
    if not isinstance(cl, dict) or cl.get("error"):
        return []
    off = (cl.get("langs_disabled") or {}).get(CSHARP)
    if off:
        return [f"DEEPMAP_TYPED_CALLS names csharp but the typed-calls pass disabled csharp on this C# corpus ({off})"]
    files = ((cl.get("by_lang") or {}).get(CSHARP) or {}).get("files")
    if not files:
        return [f"DEEPMAP_TYPED_CALLS names csharp but the typed-calls pass read no C# file of this C# corpus "
                f"(by_lang.csharp.files={files!r})"]
    return []


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
    iface = flags.get("DEEPMAP_TYPED_CALLS_IFACE")
    if iface is not None and str(iface).strip() and "typed_iface" in eff:
        # PREREG_CS §8.4: VT-cs-ifs (static) and VT-cs-if0 (0) — the modes the product reports must be exactly those
        # requested (an OFF-word asks for none); a mode it did not turn on, or one it ignored, makes the arm inert
        got = eff.get("typed_iface")
        raw = str(iface).strip().lower()
        want = set() if raw in _OFF_WORDS else {x.strip() for x in raw.split(",") if x.strip()}
        if not isinstance(got, list) or {str(x).lower() for x in got} != want:
            why.append(f"DEEPMAP_TYPED_CALLS_IFACE={iface} but the product turned on typed_iface={got!r} "
                       f"(requested: {sorted(want)})")
    return why


IDENTITY_SECTIONS = ("nodes", "edges", "counts", "invariant")


def tracked_cs_files(root) -> dict:
    """The `.cs` files (case-insensitive) a corpus holds, for PREREG_CS N1's rule ("an N corpus that holds a
    git-tracked .cs file is judged by N2"): `git ls-files` in a git tree, else every file under the root (a non-git
    tree such as the STAGE-0 corpus). Returns {"files": [...], "source": "git ls-files" | "walk"}."""
    root = Path(root)
    out = _git(root, "ls-files", "-z")
    if out is not None and (root / ".git").exists():
        files = sorted(f for f in out.split("\0") if f and C.is_cs_path(f))
        return {"files": files, "source": "git ls-files"}
    files = sorted(p.relative_to(root).as_posix() for p in root.rglob("*")
                   if p.is_file() and ".git" not in p.relative_to(root).parts and C.is_cs_path(p.name))
    return {"files": files, "source": "walk"}


def identity(base, variant, cs_files=None, rule="auto") -> dict:
    """PREREG_CS N1 / N2 between the V0 dump and the VT-cs dump of one corpus × engine (pure).
      N1: the dumps' nodes, edges, counts and invariant are byte-identical in the kit's serialisation (C.dump_json),
          and neither dump is VOID — the rule for an N corpus holding no `.cs` file;
      N2: every edge whose CALLER's file does not end in `.cs` (case-insensitive) is identical, pair and grade — the
          rule for a C# corpus and for an N corpus that holds a `.cs` file (`self` at the RC).
    `cs_files` (tracked_cs_files) picks the rule under rule="auto": N2 when the corpus holds a `.cs` file, else N1;
    without it, the dumps' node files decide (rule_source "nodes"). Both are computed; `holds` is the chosen one's."""
    sec = {s: C.dump_json(base.get(s)) == C.dump_json(variant.get(s)) for s in IDENTITY_SECTIONS}
    void = [f"the {side} dump is VOID ({d['void']})" for side, d in (("base", base), ("variant", variant))
            if d.get("void")]
    file_of = {}
    for d in (base, variant):
        for n in d.get("nodes") or []:
            file_of.setdefault(n.get("key"), n.get("source_file"))

    def non_cs(edges):
        return {p: g for p, g in edges.items() if not C.is_cs_path(file_of.get(p[0]))}
    e0, e1 = non_cs(C.edges_of(base)), non_cs(C.edges_of(variant))
    diff = sorted([a, b, e0.get((a, b)), e1.get((a, b))] for (a, b) in set(e0) | set(e1)
                  if e0.get((a, b)) != e1.get((a, b)))
    if cs_files is None:
        listed = sorted({C.norm_path(f) for f in file_of.values() if C.is_cs_path(f)})
        source = "nodes"
    else:
        listed, source = list(cs_files.get("files") or []), cs_files.get("source")
    chosen = rule if rule in ("N1", "N2") else ("N2" if listed else "N1")
    n1 = all(sec.values()) and not void
    n2 = not diff and not void
    return {"rule": chosen, "rule_source": source if rule not in ("N1", "N2") else "forced", "cs_files": listed,
            "n_cs_files": len(listed), "holds": n1 if chosen == "N1" else n2, "void": void,
            "n1": {"holds": n1, "identical": sec},
            "n2": {"holds": n2, "non_cs_edges_base": len(e0), "non_cs_edges_variant": len(e1),
                   "n_differ": len(diff), "differ": diff[:50]},
            "base": base.get("_path"), "variant": variant.get("_path"),
            "base_flags": base.get("flags"), "variant_flags": variant.get("flags")}


def cmd_identity(args) -> int:
    base, var = C.load_json(args.base), C.load_json(args.variant)
    base["_path"], var["_path"] = os.path.basename(args.base), os.path.basename(args.variant)
    cs = tracked_cs_files(args.corpus) if args.corpus else None
    res = identity(base, var, cs, args.rule)
    print(f"[offline_calls] identity {res['base']} -> {res['variant']}: rule {res['rule']} ({res['rule_source']}; "
          f"{res['n_cs_files']} .cs file(s)) {'HOLDS' if res['holds'] else 'FAILS'} — N1 sections identical "
          f"{res['n1']['identical']}; N2 non-.cs-caller edges differing {res['n2']['n_differ']}")
    for why in res["void"]:
        print(f"  VOID: {why}")
    if res["n_cs_files"] and res["rule"] == "N2" and res["n_cs_files"] <= 20:
        print(f"  .cs files: {res['cs_files']}")
    if args.json:
        C.write_json(args.json, res)
    if res["void"]:
        return 3
    return 0 if res["holds"] else 1


def _load_kit_module(kit_dir, name: str):
    """A kit module loaded by PATH from `kit_dir` (the RC snapshot's kit) under a private name, never the one this
    process imported; the kit's modules import only the stdlib at module level (harness gate A), so loading one runs
    nothing else."""
    import importlib.util
    path = Path(kit_dir) / f"{name}.py"
    mname = f"_cl_kit_{name}_{os.getpid()}_{abs(hash(str(path)))}"
    spec = importlib.util.spec_from_file_location(mname, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mname] = mod
    try:
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.modules.pop(mname, None)


def kithash(kit_dir=None, truth_path=None) -> "tuple[dict, list]":
    """(PREREG_CS §1 / AC7's identity of a kit directory, problems): the kit hash, the reference tool identity (the
    §1 scheme over REFERENCE_IDENTITY_FILES, checked equal to that kit's own truth_roslyn.tool_identity()), and, with a
    truth, the versions its resolver block records — checked to come from these tool sources (its
    tool_sources_sha256 equals that kit's truth_roslyn.sources_sha256()). Raises ValueError / FileNotFoundError when
    the kit cannot be hashed at all."""
    kit = Path(kit_dir or C.KIT).resolve()
    problems = []
    res = {"kit": C.norm_path(kit), "kit_hash": C.kit_hash(kit), "reference_identity": C.reference_identity(kit)}
    try:
        TR = _load_kit_module(kit, "truth_roslyn")
        own = TR.tool_identity()
        src = TR.sources_sha256()
    except Exception as exc:  # noqa: BLE001 — a kit whose truth_roslyn.py does not load cannot confirm the identity
        own, src = None, None
        problems.append(f"{kit / 'truth_roslyn.py'} does not load ({type(exc).__name__}: {exc}): its own `identity` "
                        f"cannot confirm the reference tool identity")
    ri = res["reference_identity"]
    ri["truth_roslyn_identity"] = (own or {}).get("sha256")
    ri["tool_sources_sha256"] = src
    if own is not None and own.get("sha256") != ri["sha256"]:
        problems.append(f"the reference tool identity {ri['sha256']} differs from `truth_roslyn.py identity` "
                        f"({own.get('sha256')}): the two hash different files")
    if truth_path:
        truth = C.load_json(truth_path)
        v = C.reference_versions(truth)
        res["truth"] = {"path": C.norm_path(truth_path), "versions": v,
                        "built_from_these_sources": src is not None and v.get("tool_sources_sha256") == src}
        if src is not None and v.get("tool_sources_sha256") != src:
            problems.append(f"{truth_path} was made by tool sources {v.get('tool_sources_sha256')}, not this kit's "
                            f"{src}: it is not this reference's output")
    return res, problems


RC_IDENTITY_SCHEMA = "call-linking/rc-identity/1"


def rc_identity(res) -> dict:
    """The content identity of an RC snapshot run (run_matrix.sh's out/rc_identity.json): build/*.py's code hash, the
    kit hash and the reference tool identity — no path and no exclusion count (a phase that builds the .NET tool or
    writes __pycache__ leaves the identity unchanged)."""
    return {"schema": RC_IDENTITY_SCHEMA, "code": res.get("code"),
            "kit_hash": {k: res["kit_hash"][k] for k in ("sha256", "files")},
            "reference_identity": {k: res["reference_identity"].get(k)
                                   for k in ("sha256", "truth_roslyn_identity", "tool_sources_sha256")}}


def cmd_kithash(args) -> int:
    if args.record and not args.build:
        print("[offline_calls] kithash REFUSED: --record needs --build (the RC identity holds build/*.py's code hash)",
              file=sys.stderr)
        return 2
    try:
        res, problems = kithash(args.kit, args.truth)
        if args.build:
            res["code"] = C.build_code_hash(args.build)
    except (ValueError, OSError) as exc:
        print(f"[offline_calls] kithash REFUSED: {exc}", file=sys.stderr)
        return 2
    if args.record:
        ident, rec = rc_identity(res), Path(args.record)
        if rec.is_file():
            have = C.load_json(rec)
            if C.dump_json(have) != C.dump_json(ident):
                problems.append(f"RC identity differs from the one {rec} records: recorded {C.dump_json(have).strip()}, "
                                f"now {C.dump_json(ident).strip()}")
            else:
                print(f"[offline_calls] RC identity matches {rec}", file=sys.stderr)
        elif not problems:
            C.write_json(rec, ident)
            print(f"[offline_calls] RC identity recorded in {rec}", file=sys.stderr)
    kh, ri = res["kit_hash"], res["reference_identity"]
    if res.get("code"):
        print(f"[offline_calls] code {C.norm_path(args.build)}: build/*.py sha256 {res['code']['sha256']} "
              f"({res['code']['files']} files)", file=sys.stderr)
    print(f"[offline_calls] kit {res['kit']}: kit hash {kh['sha256']} ({kh['files']} files; excluded {kh['excluded']})",
          file=sys.stderr)
    print(f"[offline_calls] reference tool identity {ri['sha256']} over {', '.join(ri['files'])}"
          f" (truth_roslyn.py identity: {ri.get('truth_roslyn_identity')}; tool sources {ri.get('tool_sources_sha256')})",
          file=sys.stderr)
    if res.get("truth"):
        print(f"[offline_calls] reference versions ({res['truth']['path']}): {C.dump_json(res['truth']['versions'])}",
              end="", file=sys.stderr)
    for p in problems:
        print(f"[offline_calls] MISMATCH: {p}", file=sys.stderr)
    if args.json:
        C.write_json(args.json, dict(res, problems=problems))
    print(C.dump_json(dict(res, problems=problems)), end="")
    return 1 if problems else 0


def cmd_manifest(args) -> int:
    """PREREG_CS §8.7 G5b / AC15 (see the module doc): print, check or (--write) write cs/MANIFEST.sha256."""
    cs = Path(args.cs_dir)
    if cs.name != "cs":
        print(f"[offline_calls] manifest REFUSED: {cs} is not a folder named cs/ (AC15: benchmark/call_linking/cs/)",
              file=sys.stderr)
        return 2
    ga, man = cs / ".gitattributes", cs / "MANIFEST.sha256"
    want_ga = C.G5B_GITATTRIBUTES.encode("ascii")
    virtual = {} if ga.is_file() else {".gitattributes": want_ga}
    text, problems = C.g5b_manifest(cs, virtual)
    if problems:
        for p in problems:
            print(f"[offline_calls] manifest REFUSED: {p}", file=sys.stderr)
        return 2
    body = text.encode("utf-8")
    sha = hashlib.sha256(body).hexdigest()
    record = f"- G5b: `cs/MANIFEST.sha256` sha256 `{sha}`"
    n = text.count("\n")
    if man.is_file():
        have = man.read_bytes()
        if have == body:
            print(f"[offline_calls] {man} matches the {n} file(s) under cs/ (sha256 {sha}); DECISION_CS.md records: "
                  f"{record}")
            return 0
        print(f"[offline_calls] {man} DIFFERS from the files under cs/ (on disk sha256 "
              f"{hashlib.sha256(have).hexdigest()}, the files give {sha}); nothing written — delete it by hand to "
              f"redo the manifest", file=sys.stderr)
        return 1
    if not args.write:
        print(text, end="")
        print(f"[offline_calls] (dry run: nothing written) cs/MANIFEST.sha256 would list {n} file(s), sha256 {sha}"
              + ("; cs/.gitattributes (`* -text`) would be placed first" if virtual else ""))
        return 0
    if virtual:
        with open(ga, "wb") as fh:
            fh.write(want_ga)
    text2, problems2 = C.g5b_manifest(cs)
    if problems2 or text2 != text:
        print(f"[offline_calls] manifest REFUSED: cs/ changed while the manifest was made ({problems2})",
              file=sys.stderr)
        return 2
    with open(man, "wb") as fh:
        fh.write(body)
    print(f"[offline_calls] wrote {man}: {n} file(s), sha256 {sha}"
          + (" (and cs/.gitattributes `* -text`, listed)" if virtual else ""))
    print(f"[offline_calls] DECISION_CS.md records: {record}")
    return 0


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
    i = sub.add_parser("identity", help="PREREG_CS N1 / N2 between a V0 and a VT-cs dump of one corpus x engine")
    i.add_argument("base")
    i.add_argument("variant")
    i.add_argument("--corpus", default=None, help="the corpus root: its .cs files pick the rule (N2 if any)")
    i.add_argument("--rule", default="auto", choices=("auto", "N1", "N2"))
    i.add_argument("--json", default=None)
    k = sub.add_parser("kithash", help="PREREG_CS §1 / AC7: the kit hash and the reference tool identity")
    k.add_argument("--kit", default=None, help="the benchmark/call_linking dir to hash (default: this kit)")
    k.add_argument("--truth", default=None, help="a truth_roslyn output: also print its resolver block's versions")
    k.add_argument("--build", default=None, help="also hash this build/ (build/*.py, as codehash)")
    k.add_argument("--record", default=None, help="record the RC identity in this file, or check it against it")
    k.add_argument("--json", default=None)
    g = sub.add_parser("manifest", help="PREREG_CS §8.7 G5b / AC15: cs/MANIFEST.sha256 (dry run unless --write)")
    g.add_argument("cs_dir")
    g.add_argument("--write", action="store_true", help="write .gitattributes + the manifest (the RC run's G5b step)")
    args = ap.parse_args(argv)
    if args.cmd == "build":
        return cmd_build(args)
    if args.cmd == "identity":
        return cmd_identity(args)
    if args.cmd == "kithash":
        return cmd_kithash(args)
    if args.cmd == "manifest":
        return cmd_manifest(args)
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
