#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S0 gate — the call-linking measurement kit (benchmark/call_linking/) and its pre-registration (PREREG.md).

The kit decides whether typed-receiver call linking, the untyped-receiver audit, family-scoped precise calls and
the cross-family filter ship (PREREG §8.5). A kit that measures something other than what `deepmap sync` pushes,
scores inexactly, or lets the protocol drift after data would decide wrongly, so this gate locks:

  A  the kit's files exist, parse, and import only the stdlib / their siblings at module level; nothing the
     product ships (build/, hooks/) imports the kit or a reference resolver (jedi, TypeScript); D3-cs: the C#
     reference (truth_roslyn.py + truth_roslyn/: the .NET tool's sources, its captured self-test) exists, loads
     without .NET, imports only the stdlib at module level, holds the kit's only .cs file (Program.cs), and no text
     file under build/ or hooks/ names truth_roslyn or Microsoft.CodeAnalysis
  B  the offline pass order equals sync's, read from build/sync.py's AST: the pipeline function (`_sync_pass`,
     reached from `sync()`) runs exactly the CURRENT order or the FUTURE one with `_typed_calls_link` between
     `_unkey_receiver_calls` and `_attach_bare_calls` (and defines that helper iff it calls it); any other change
     to nodes/calls between extraction and the push fails — a rebind (a comprehension filter included), a write
     into them or into an alias / element of them, a mutating method call, any call that reaches them anywhere in
     its arguments (aliases and loop / comprehension elements tracked to a fixpoint); push_to_neo4j's first step
     is `_resolve_calls(nodes, calls)`; offline_calls.run_pipeline runs the future order through a guarded
     lookup. A self-test proves the checker accepts exactly those two orders and rejects every such shape.
  C  data flow: the REAL `sync.sync()` pass (fake driver; extraction frozen, the push recorded) and
     offline_calls.run_pipeline produce the identical :CALLS dict for the same extraction — ast engine always,
     graphify when installed, per flag mode; the passes are shown to matter (skipping one changes the edges);
     the CLI's two runs are byte-identical; a dump carries no wall-clock field (typed_calls' `seconds` stays out of
     `call_linking`: two typed runs, in process under two fake clocks and through the CLI, are byte-identical; the
     pass's own seconds go to <out>.timing.json; strip_wall_clock re-judges a pre-fix dump — 2026-10-24 ruling);
     its DEEPMAP_HOME / read-only-clone guards refuse; with graphify, VF
     against V0 end to end (the review's probe: a Python and a TypeScript `helper`) removes only cross-family
     targets and its delta is data, never an inert pair
  D  the offline counts reproduce the frozen flask graph (330 CALLS / 26 AMBIGUOUS on 0.8.39; 329 / 26 on
     0.8.51; exact edge hash) — when the benchmark clone and an in-range engine are present, else SKIPPED
  E  compare_calls, delta, indegree_drift (+ the void rule), audit_sample (seeded, blind, Wilson), engdiff,
     impact_offline, impact_spot, score_m (R1/R3), measure_self (R2; AC29: compare's exact fields from the integer
     ranks beside the unchanged 4-decimal mrr_ok / r2_holds, --min-gain read as a decimal, the CLI's two lines and
     --exact), run_variant (round trip, matched state,
     safety) and variants are exact on synthetic inputs; the S0 review round: census reads the owner stamp sync
     writes and both live captures judge it with owner_problem; the inert-variant VOID rule (offline_calls
     variant_void on the review's V0/VT pair, delta's VOID pair rule) and the cross-file EXTRACTED invariant
     (exit 4); --src-prefix filters by the CALLER's file both ways; delta's EXTRACTED bucket keeps base-EXTRACTED
     edges; the audit's top-level losses and two-strata removal share; saturated spot symbols; `$` atoms and TS
     def spans in score_m; PREREG amendment A1 in engdiff (E compares the edges whose BOTH endpoints exist on both
     engines, the raw symmetric difference reported beside it; VOID on a non-edge-only variant or a VOID dump);
     compare_calls pool refuses a several-dump --json, a non-score, mixed variants / engines (exit 2) and a VOID
     score (exit 3) instead of crashing or flattening; the `effective` block reports, through the product, every
     switch a §8.3 variant sets (the cross-family filter, the typed-calls shapes / replace / tests / nonprod / max
     files): every variant differs from V0 and from every other variant, variant_void VOIDs a requested switch the
     product left off; engdiff.typed_tuple_verdict applies PRE_RC_DECISIONS.md's PD-1 (exactly its two zod tuples)
  F  tests/stage0_refreeze.py --env forwards only the DEEPMAP_* keys it is given; child_env is otherwise unchanged
  G  compliance: jedi and TypeScript appear in no shipped list; nor do Roslyn / Microsoft.CodeAnalysis / truth_roslyn
     (requirements, the SBOM's text and components, NOTICE, THIRD-PARTY-LICENSES, both installers, every
     .claude-plugin manifest: PREREG_CS §8.2)
  H  PREREG.md: §8 verbatim (sha256-locked — thresholds never move after data), the dated "Amendments before RC"
     (A1 engine parity on common endpoints, A2 the `_mk_project` spot replaced by `read_records`), the RC identity,
     engine builds, corpus SHAs (== run_matrix.sh), the 5 TS + 5 Python spot symbols (== impact_spot.py), none
     saturated at V0, each on a pinned corpus (the STAGE-0 one defined once in the registered corpus), the §8.3
     variant table (== variants.py); §5 names the switches `effective` reports; A1 stays the only amendment of
     §8.4 E, and the zod tuples S4 found after it go the DECISION.md route (PRE_RC_DECISIONS.md PD-1: the two
     tuples, their cause, a list not a rule; §7 points to it); gt_self65.json holds the 65 recovered queries
  I  truth_jedi's call-site reader (forms, defstack, decorator lines, CHARACTER columns) without jedi; truth_jedi
     end to end on a non-ASCII / BOM + CRLF / latin-1 / syntax-error fixture when a jedi 0.20.1 python is given
     (CALL_LINKING_JEDI_PY); truth_ts.js when Node and a TypeScript 5.6.3 env are given (CALL_LINKING_TSENV);
     else SKIPPED
  J  PREREG_CS.md (D3-cs): its protocol block (strictly between the one BEGIN and one END marker line, LF-joined
     plus a final LF — H's rule) sha256-locked before the first RC run; the "Amendments before RC" section above it,
     sha256-locked too (the lines strictly between its heading line and the BEGIN line, the same joining: AC1-AC32),
     and its kit-facing rules equal to the kit's — AC13's engine-parity command == run_matrix.sh's cs_engine_parity
     (its only caller; AC29: its full log, stdout and stderr, to exactly $OUT/engine_parity_cs.log through run_log's
     .partial-then-rename), AC12's final spot list in every C# spot command of run_matrix.sh, G5a's gate list ==
     run_matrix.sh's CS_G5A_GATES (each gate present), AC14's N3 gate (typed_calls_push_gates' section I), AC16's
     VRC-cs flag == variants.vrc_cs_flag; the §1 identity slots; the two C# corpora's SHAs, read-only sources and library prefixes (§8.2) == run_matrix.sh
     cs_corpus_row == §3 == the re-create commands == the engine-parity gate's CS_CORPORA, apart from the corpus_row
     table H reads; the audit seed and n; the 10 spots and 8 reserves in order (§8.3) == impact_spot.CS_SPOTS /
     CS_RESERVES / CS_IDS == §4, apart from SPOTS; the §8.4 variants == variants.CS_VARIANTS (ids, order, flags),
     its shapes == the product's typed_calls.SHAPES, apart from VARIANTS; RECEIVER_FORMS['cs'] (§8.5); the C# sheet
     sentence (§8.6 A's, then the one AC25 adds, both read from PREREG_CS.md) == audit_sample.CS_SHEET_SENTENCE; §8.2's
     13 N corpora (read from its bullets, mapped onto corpus_row's ids) == run_matrix.sh's CS_N_CORPORA, in order
  K  the G5b flip guard (PREREG_CS §8.7): inactive while `csharp` is not in build/typed_calls._TYPED_DEFAULT_LANGS;
     active, benchmark/call_linking/cs/MANIFEST.sha256 must exist (`<sha256>  <path>` lines, paths inside cs/, in
     byte order, never a sheet.md), every listed file must match, and DECISION_CS.md must record the manifest's own
     sha256 (exactly one 64-hex value on the lines naming MANIFEST.sha256); an unreadable defaults set fails closed.
     Both branches are proven on temp copies of typed_calls.py
  L  the C# reference on its committed self-test (truth_roslyn/selftest/): selftest --check (the captured raw sites
     convert to truth.json byte for byte; expect.json holds); the replicated test rule == build/sync.py
     _is_test_path (on every path it touches, PROBE_PATHS and the gate's probes; its constants == the product's;
     PROBE_PATHS exercises each test-directory name, each .NET test-project suffix, a case variant and production
     look-alikes); the captured header's resolver.tool_sources_sha256 == truth_roslyn.sources_sha256() (an
     un-recaptured tool edit fails without .NET); every row carries `dispatch` with entries of exactly the defs entries' shape; the gate's own
     reading of the contract — forms, defstack, overloads, extension calls, interface and override dispatch,
     test-file exclusion — and the adapter's derivations on synthetic raw rows; the project evaluation Roslyn cannot
     check (each MSBuild shape's usings == real MSBuild's Using items, recorded here: verbatim property values,
     <Using Remove> wildcards, System.Net.Http on .NETFramework, Directory.Packages.props; each fix's pre-fix answer
     as a mutant expect.json must refuse; UsingsBase unchanged; R41's shapes under Msb/ == the GlobalUsings.g.cs real
     MSBuild writes, line for line, and per R41 rule the capture of the tool built with that rule reverted, which
     expect.json must refuse; the WindowsDesktop lists as data); the kit scoring it (T_s / T_d, the
     §8.5 classification, G4c's interface fact, delta's dispatch-confirmed verdict and G2d). Live, only when .NET
     SDK 8.0.202, the 8.0.3 reference packs, the captured packages (Microsoft.CodeAnalysis 4.8.0 and its closure) in
     the local packages folder and the captured runtime are present: the tool re-run on the self-test corpus (from
     a temp copy) must reproduce the committed output byte for byte; else SKIPPED with the reason
  M  the kit's other D3-cs rules (PREREG_CS §8.6 "Kit changes"): compare_calls on a dispatch truth (the §8.5
     classification, recall over T_s, dispatch recall over T_d, the static-only reading beside it) and pool
     --family=cs (receiver forms, pooled dispatch recall, its refusals); impact_spot's T_s ∪ T_d reference and its
     substitution checks (exactly one node, a reference caller, not SATURATED); audit_sample's C# call-site shapes,
     the C# sheet sentence and removed_all_exact beside removed_all; offline_calls' interface-mode VOID rule and the
     modes its effective block reports through the product
  N  the D3-cs rules on a synthetic C# corpus written from PREREG_CS §8.2 / §8.5 and AC5-AC16 (never from the kit's
     output): AC5's row fields (_static_iface reads `dispatch_rule`, else the defs' `interface`; truth_roslyn's
     contract_problems and the refusing convert; defs that disagree: any interface def makes it true); T_s, T_d =
     dispatch MINUS T_s (a pair in both), the refuted test over static AND dispatch targets (a site bound only by
     dispatch); G4c's EXTRACTED dispatch-only edges through an interface vs a virtual static target, a pair produced
     by an interface AND a virtual site (True wins, either order), a producing row breaking AC5 (not exact), and AC24's
     never-decisive count over any same-named site beside it (a site resolved to nothing never counts; a def-less site
     with `interface` dispatch does); G2d from production and non-production callers; AC9's
     dispatch stratum fallback (static_dispatchable, impl_fallback, its not-computable case); AC10 (a
     dispatch-confirmed removal is audited exhaustively) and the reference's judged rows; exact_bounds' census and
     pool_scores' lower bound; AC29's integer reading at a rounding boundary (120 of 126: Wilson 0.8999983, displayed
     0.9): exact_bounds' and removed_all_exact's full-precision lower_bound_exact, pool_scores' share_fraction /
     lower_bound_exact from each corpus's integers, the resolver's void rule (wrong x 10 >= judged; 0 of 0 judged
     voids nothing), score's printout of removed_all_exact's full-precision bound, removed_all unchanged byte for byte; AC11's reference error rate (the union of an engine's two samples counted once, the
     exact 10% threshold, either engine, conflicts, refusals, the CLI's exit codes) and AC26's three readings (a pair
     agreed in no sample is outside the rate; agreed in one sample, judged by it; TRUE in one and FALSE in the other,
     wrong once and listed), cited in the docstring and the printout; §8.3's substitution on both engines (a failing
     reserve passed over) and AC12's final list (the refusals, a changed `registered` list, an ad-hoc --symbols list on
     a C# corpus; --one-node on a non-C# one); AC29 in impact_spot (`improved` on exact fractions, SATURATED only at
     hit >= ref, 20001 reference callers); N2 on added / regraded / removed non-.cs edges and the case-insensitive
     `.cs` test; §8.4's C# VOID rule; AC15's manifest writer (dry run, --write, refusals, byte order never case-folded)
     accepted by K's guard; AC16's VRC-cs flags on temp trees; §1 / AC7's kit hash (independent recomputation, the
     exclusions) and the reference tool identity
  O  run_matrix.sh's C# orchestration on a FAKE repository (the kit, a build/ copy and the engine-parity gate,
     committed in a temp dir, then a staged and an unstaged deletion, an edit, an untracked and an ignored file):
     AC7's self-snapshot (its index == the working tree's files, the deleted ones gone; made once, never touched) and
     guard (a C# phase from the working tree's script, with another build/, or without CL_SELF_RC is refused); the
     dry-run plans: AC13's engine-parity command once as the rest phase's last command, its full output into
     <out>/engine_parity_cs.log (AC29; run_log itself run: stdout and stderr, the exit code, no .partial left; skipped
     when the C# corpora are narrowed), AC12's --final spot commands, AC11's refrate per corpus, AC15's manifest phase, G5a's gates under both
     engines' Pythons plus the no-Neo4j suite, every phase checking the snapshot's RC identity first. Only when a bash
     that reads this checkout's paths (Git Bash on Windows) and git are present, else SKIPPED

Hermetic except D/I and L's live re-run (opt-in by presence): temp dirs + a temp DEEPMAP_HOME, no Neo4j, no network;
never touches ~/.deepmap. Options (for the RED-first runs and mutation tests): --kit <dir> (load the kit from another dir),
--sync <file> (read another sync.py for B), --refreeze <file> (load another stage0_refreeze.py for F),
--no-real (skip C's graphify modes and D).
Usage: python tests/call_linking_harness_gates.py [--kit DIR] [--sync FILE] [--refreeze FILE] [--no-real]
"""
from __future__ import annotations

import ast
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parent.parent
TESTS = REPO / "tests"
BUILD = REPO / "build"
KIT_DEFAULT = REPO / "benchmark" / "call_linking"
PASSES: list = []
FAILS: list = []
SKIPS: list = []

KIT_FILES = ("offline_calls.py", "truth_jedi.py", "truth_ts.js", "compare_calls.py", "delta.py", "indegree_drift.py",
             "engdiff.py", "impact_offline.py", "impact_spot.py", "audit_sample.py", "run_variant.py",
             "measure_self.py", "score_m.py", "gt_self65.json", "run_matrix.sh", "PREREG.md", "README.md",
             "variants.py", "_common.py", "frozen/flask_V0.json", "PRE_RC_DECISIONS.md")
KIT_MODULES = ("_common", "variants", "offline_calls", "compare_calls", "delta", "indegree_drift", "engdiff",
               "impact_offline", "impact_spot", "audit_sample", "run_variant", "measure_self", "score_m",
               "truth_jedi")
ORDER_NOW = ["_rebuild_code", "enrich.apply_sidecar", "_apply_recognizers", "_unkey_receiver_calls",
             "_attach_bare_calls", "_attach_local_defs", "_attach_local_def_calls", "push_to_neo4j"]
ORDER_FUTURE = ORDER_NOW[:4] + ["_typed_calls_link"] + ORDER_NOW[4:]
STEP_NAMES = set(ORDER_FUTURE)
# Calls between extraction and the push that take nodes/calls but never change them (sync's prune PLANNING —
# _plan_prune and the auto-sync evidence-only _plan_prune_evidence read the live keys and the graph — the FTS diff,
# and builtins). A new call on nodes/calls fails B until it is classified here or mirrored in offline_calls.
READ_ONLY_OK = {"_plan_prune", "_plan_prune_evidence", "_fts_changed_docs", "print", "len", "bool", "list", "dict",
                "sorted", "set", "getattr", "isinstance", "hasattr", "tuple", "sum", "any", "all", "min", "max",
                "enumerate", "zip", "str", "int", "repr"}
# Methods that never change their receiver: a call `nodes.<m>(...)` / `n.<m>(...)` (n an element of nodes/calls)
# with any other m fails B.
READ_ONLY_METHODS = {"get", "items", "keys", "values", "count", "index", "copy"}
PREREG_S8_SHA256 = "7fcd02d98b48f7b3fe375d2b09be5843bf0b15890804546484683b3173e13d0c"
FLASK_SHA = "36e4a824f340fdee7ed50937ba8e7f6bc7d17f81"

# D3-cs (benchmark/call_linking/PREREG_CS.md): the C# reference truth_roslyn and the registration, apart from the D3
# lists above (an old kit read with --kit keeps its own A/H meaning).
KIT_FILES_CS = ("PREREG_CS.md", "truth_roslyn.py", "truth_roslyn/Program.cs", "truth_roslyn/TruthRoslyn.csproj",
                "truth_roslyn/global.json", "truth_roslyn/nuget.config", "truth_roslyn/.gitattributes",
                "truth_roslyn/.gitignore", "truth_roslyn/selftest/expect.json", "truth_roslyn/selftest/sites.jsonl",
                "truth_roslyn/selftest/truth.json")
KIT_MODULES_CS = ("truth_roslyn",)
# The protocol block of PREREG_CS.md (the lines strictly between its BEGIN and END marker lines, joined with LF, plus
# one final LF), locked before the first RC run (PREREG_CS "Rules"; section J).
PREREG_CS_PROTOCOL_SHA256 = "5cde7ab3812a372a67bef8ffa97a152eb357b1a7e97582837096d8d3bdb9cac7"
# Its 'Amendments before RC' (the lines strictly between the `## Amendments before RC` heading line and the BEGIN
# marker line, joined with LF, plus one final LF): AC1-AC18 dated 2026-10-28, AC19-AC30 dated 2026-10-29 and
# AC31-AC32 dated 2026-10-31, locked beside the block (section J).
PREREG_CS_AMENDMENTS_SHA256 = "f3111fbdfcf921e5fab4290eb45f82766cc4391705ec677b8fdd2345a2413797"
CS_CORPUS_IDS = {"jellyfin": "jellyfin__jellyfin", "eShop": "dotnet__eShop"}
CS_LANG = "csharp"
# What the A scan of build/ and hooks/ reads (every text file the product could load or ship), and skips.
PRODUCT_TEXT_SUFFIXES = (".py", ".json", ".md", ".txt", ".toml", ".cfg", ".ini", ".yaml", ".yml", ".sh", ".ps1",
                         ".bat", ".cmd", ".xml", ".csv", "")
PRODUCT_SKIP_DIRS = {"__pycache__", "graphify-out", ".deepmap", "node_modules", ".git"}
CS_REFERENCE_NAMES = re.compile(r"truth_roslyn|microsoft\.codeanalysis", re.I)
CS_SHIPPED_NAMES = re.compile(r"roslyn|microsoft\.codeanalysis", re.I)


def check(name: str, ok: bool, detail="") -> None:
    (PASSES if ok else FAILS).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok or detail == "" else f" — {str(detail)[:900]}"))


def skip(name: str, why: str) -> None:
    SKIPS.append(name)
    print(f"  [SKIP] {name} — {why}")


@contextlib.contextmanager
def _env(**kv):
    saved = {k: os.environ.get(k) for k in kv}
    try:
        for k, v in kv.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _graph_flags_cleared() -> dict:
    """Every DEEPMAP_* graph flag unset (the caller's shell must not leak into a mode)."""
    return {k: None for k in os.environ if k.startswith("DEEPMAP_") and k not in ("DEEPMAP_HOME", "DEEPMAP_PYTHON")}


def _quiet():
    return contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO())


def _load_file(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    so, se = _quiet()
    with so, se:
        spec.loader.exec_module(mod)
    return mod


# =====================================================================================================
# A — inventory, imports, isolation from the product
# =====================================================================================================
def gate_a(kit: Path) -> dict:
    print("\n-- A: the kit's files, their imports, and the product never importing them --")
    mods = {}
    missing = [f for f in KIT_FILES if not (kit / f).is_file()]
    check(f"every S0 file exists under {kit.name}/ ({len(KIT_FILES)} files)", not missing, missing)
    std = set(getattr(sys, "stdlib_module_names", ()))
    local = set(KIT_MODULES)
    bad = {}
    for py in sorted(kit.glob("*.py")):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            bad[py.name] = f"syntax: {exc}"
            continue
        for node in tree.body:   # module level only: lazy imports inside functions are the point
            names = []
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module.split(".")[0]]
            for n in names:
                if n != "__future__" and n not in std and n not in local:
                    bad.setdefault(py.name, []).append(n)
    check("every kit module imports only the stdlib and its siblings at module level (jedi, neo4j, yaml, the "
          "product's modules are imported lazily, inside functions)", not bad, bad)
    if str(kit) not in sys.path:
        sys.path.insert(0, str(kit))
    for m in KIT_MODULES:
        try:
            so, se = _quiet()
            with so, se:
                mods[m] = __import__(m)
        except Exception as exc:  # noqa: BLE001
            check(f"kit module {m} imports", False, repr(exc))
    check("every kit module imports in the product's Python (no jedi / TypeScript needed to load them)",
          len(mods) == len(KIT_MODULES), sorted(set(KIT_MODULES) - set(mods)))
    leaks = []
    for d in (BUILD, REPO / "hooks"):
        for p in sorted(d.glob("*.py")):
            t = p.read_text(encoding="utf-8", errors="replace")
            if re.search(r"^\s*(import|from)\s+(jedi|parso)\b", t, re.M) or re.search(r"benchmark[./\\]+call_linking", t):
                leaks.append(p.relative_to(REPO).as_posix())
    check("nothing under build/ or hooks/ imports jedi / parso or names the measurement kit", not leaks, leaks)
    gate_a_cs(kit, mods, std)
    return mods


def _module_imports(path: Path) -> "list | None":
    """The top-level names a module imports at module level (a relative import is '.<module>'); None on a syntax
    error."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return None
    out = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            out += [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            out.append(("." * node.level) + (node.module or "").split(".")[0] if node.level else
                       (node.module or "").split(".")[0])
    return out


def product_text_files(roots=None) -> list:
    """Every text file under build/ and hooks/ (recursively; caches, graphify output and VCS dirs skipped)."""
    out = []
    for d in roots or (BUILD, REPO / "hooks"):
        if not d.is_dir():
            continue
        for p in sorted(d.rglob("*")):
            rel = p.relative_to(d).parts
            if any(part in PRODUCT_SKIP_DIRS for part in rel[:-1]) or not p.is_file():
                continue
            if p.suffix.lower() in PRODUCT_TEXT_SUFFIXES and p.stat().st_size < 8_000_000:
                out.append(p)
    return out


def gate_a_cs(kit: Path, mods: dict, std: set) -> None:
    """A for D3-cs (PREREG_CS §8.2 "The reference is measurement only"): truth_roslyn.py and truth_roslyn/ exist,
    load without .NET, import only the stdlib at module level, and nothing under build/ or hooks/ names them."""
    print("   (D3-cs: the C# reference truth_roslyn.py + truth_roslyn/, PREREG_CS §8.2)")
    missing = [f for f in KIT_FILES_CS if not (kit / f).is_file()]
    check(f"every D3-cs kit file exists under {kit.name}/ ({len(KIT_FILES_CS)} files: PREREG_CS.md, truth_roslyn.py, "
          f"the .NET tool's sources and its captured self-test)", not missing, missing)
    imps = _module_imports(kit / "truth_roslyn.py")
    bad = None if imps is None else sorted({n for n in imps if n != "__future__" and n not in std})
    check("truth_roslyn.py imports only the stdlib at module level (not even a sibling: the kit's _common and the "
          "product's test rule load lazily, inside functions)", imps is not None and not bad,
          "syntax error / unreadable" if imps is None else bad)
    for m in KIT_MODULES_CS:
        try:
            so, se = _quiet()
            with so, se:
                mods[m] = __import__(m)
        except Exception as exc:  # noqa: BLE001
            check(f"kit module {m} imports", False, repr(exc))
    check("truth_roslyn imports in the product's Python (no .NET needed to load it)",
          all(m in mods for m in KIT_MODULES_CS), sorted(set(KIT_MODULES_CS) - set(mods)))
    cs = sorted(p.relative_to(kit).as_posix() for p in kit.rglob("*") if p.is_file() and p.suffix.lower() == ".cs"
                and "__pycache__" not in p.parts
                and not (p.relative_to(kit).parts[:1] == ("truth_roslyn",)
                         and p.relative_to(kit).parts[1:2] in (("bin",), ("obj",))))
    check("the kit's only .cs file is truth_roslyn/Program.cs (the self-test corpus is stored as .txt; PREREG_CS §7: "
          "the `self` N corpus holds exactly that one; the tool's bin/ and obj/ build outputs are not sources)",
          cs == ["truth_roslyn/Program.cs"], cs)
    leaks = {}
    for p in product_text_files():
        hits = sorted({m.group(0) for m in CS_REFERENCE_NAMES.finditer(p.read_text(encoding="utf-8", errors="replace"))})
        if hits:
            leaks[p.relative_to(REPO).as_posix() if p.is_relative_to(REPO) else p.as_posix()] = hits
    check("nothing under build/ or hooks/ (every text file, hooks.json and docs included) names truth_roslyn or "
          "Microsoft.CodeAnalysis (PREREG_CS §8.2: the reference is measurement only)", not leaks, leaks)


# =====================================================================================================
# B — the pass order, read from the ASTs
# =====================================================================================================
def _call_name(call: ast.Call) -> str:
    f = call.func
    parts = []
    while isinstance(f, ast.Attribute):
        parts.append(f.attr)
        f = f.value
    if isinstance(f, ast.Name):
        parts.append(f.id)
    return ".".join(reversed(parts))


def _calls_in(fn) -> list:
    out = [n for n in ast.walk(fn) if isinstance(n, ast.Call)]
    out.sort(key=lambda c: (c.lineno, c.col_offset))
    return out


def _arg_names(call: ast.Call) -> set:
    return {a.id for a in list(call.args) + [k.value for k in call.keywords] if isinstance(a, ast.Name)}


def _names_in(*exprs) -> set:
    return {n.id for e in exprs if e is not None for n in ast.walk(e) if isinstance(n, ast.Name)}


def _root_name(expr):
    """The Name at the base of an Attribute / Subscript / Starred chain (`nodes[0]['x']` -> nodes), else None."""
    while isinstance(expr, (ast.Attribute, ast.Subscript, ast.Starred)):
        expr = expr.value
    return expr.id if isinstance(expr, ast.Name) else None


def _target_roots(t) -> list:
    """[(name, direct)] of an assignment target: direct = the name itself is rebound; else written INTO."""
    if isinstance(t, (ast.Tuple, ast.List)):
        return [r for e in t.elts for r in _target_roots(e)]
    if isinstance(t, ast.Starred):
        return _target_roots(t.value)
    if isinstance(t, ast.Name):
        return [(t.id, True)]
    r = _root_name(t)
    return [(r, False)] if r else []


def _unmirrored(pipe, l0: int, l1: int) -> list:
    """Every statement / call between extraction (line l0) and the push (line l1) that could CHANGE the nodes or
    calls the offline mirror reproduces without being a mirrored step:
      (1) an assignment / del / walrus / loop target that rebinds `nodes` / `calls` (unless the value is a
          mirrored step call or the unpack of the `_rebuild_code` result), or that writes INTO one of them or into
          an alias / element of them (`calls[:] = ...`, `nodes[0]['name'] = ...`, `n['x'] = ...` in a loop);
      (2) a method call on nodes / calls / an alias / an element of them (`calls.append(...)`, `n.setdefault(...)`)
          other than READ_ONLY_METHODS;
      (3) any other call that reaches nodes / calls / an alias / an element ANYWHERE in its arguments
          (`_drop_calls(list(calls))`, `f(items=calls)`) unless it is a step or READ_ONLY_OK;
    where aliases and elements are tracked to a fixpoint: a name bound to nodes / calls (or a part of them) by an
    assignment, and the target of a `for` / comprehension over them."""
    def _line(n):   # an ast.comprehension has no position of its own: its iterable's
        return n.iter.lineno if isinstance(n, ast.comprehension) else getattr(n, "lineno", -1)
    body = [n for n in ast.walk(pipe) if l0 <= _line(n) <= l1]
    extract_alias = {t.id for n in body if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call)
                     and _call_name(n.value) == "_rebuild_code" for t in n.targets if isinstance(t, ast.Name)}
    base = {"nodes", "calls"}
    tracked = set(base) | extract_alias

    def may_alias(v) -> bool:
        if isinstance(v, ast.Name):
            return v.id in tracked and v.id not in extract_alias
        if isinstance(v, (ast.Attribute, ast.Subscript, ast.Starred)):
            return _root_name(v) in tracked
        if isinstance(v, ast.IfExp):
            return may_alias(v.body) or may_alias(v.orelse)
        if isinstance(v, ast.BoolOp):
            return any(may_alias(x) for x in v.values)
        if isinstance(v, (ast.Tuple, ast.List, ast.Set)):
            return any(may_alias(x) for x in v.elts)
        if isinstance(v, ast.NamedExpr):
            return may_alias(v.value)
        if isinstance(v, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            return any(_names_in(g.iter) & tracked for g in v.generators)
        return False

    while True:
        before = len(tracked)
        for n in body:
            if isinstance(n, (ast.For, ast.AsyncFor, ast.comprehension)) and _names_in(n.iter) & tracked:
                tracked |= {x for x in _names_in(n.target)}
            elif isinstance(n, (ast.Assign, ast.AnnAssign, ast.NamedExpr)) and n.value is not None \
                    and may_alias(n.value):
                tgts = n.targets if isinstance(n, ast.Assign) else [n.target]
                tracked |= {name for t in tgts for name, direct in _target_roots(t) if direct}
        if len(tracked) == before:
            break

    def step_value(v) -> bool:
        return (isinstance(v, ast.Call) and _call_name(v) in STEP_NAMES) or (
            isinstance(v, ast.Name) and v.id in extract_alias)

    out = []
    for n in sorted(body, key=lambda x: (_line(x), getattr(x, "col_offset", 0))):
        if isinstance(n, (ast.Assign, ast.AugAssign, ast.AnnAssign, ast.Delete, ast.NamedExpr, ast.For,
                          ast.AsyncFor)):
            tgts = (n.targets if isinstance(n, (ast.Assign, ast.Delete)) else [n.target])
            for t in tgts:
                for name, direct in _target_roots(t):
                    if name in base and direct:
                        if not (isinstance(n, (ast.Assign, ast.AnnAssign)) and step_value(n.value)):
                            out.append(f"rebinds {name} (line {n.lineno})")
                    elif name in tracked and not direct:
                        out.append(f"writes into {name} (line {n.lineno})")
                    elif name in tracked and direct and isinstance(n, (ast.AugAssign, ast.Delete)):
                        out.append(f"changes {name} in place (line {n.lineno})")
        elif isinstance(n, ast.Call):
            cn = _call_name(n)
            if cn in STEP_NAMES:
                continue
            f = n.func
            if isinstance(f, ast.Attribute) and _root_name(f.value) in tracked:
                if f.attr not in READ_ONLY_METHODS:
                    out.append(f"{cn or f.attr} (a method call on {_root_name(f.value)}, line {n.lineno})")
                continue
            if _names_in(*n.args, *[k.value for k in n.keywords]) & tracked \
                    and (cn or "").split(".")[-1] not in READ_ONLY_OK:
                out.append(f"{cn or '<call>'} (line {n.lineno})")
    return sorted(set(out), key=lambda s: int(re.search(r"line (\d+)", s).group(1)))


def sync_order(src: str) -> dict:
    """{function, steps, unknown, typed_helper, reached_from_sync, push_first_resolve, problems} of a sync.py."""
    tree = ast.parse(src)
    top = {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    pipe = None
    for name, fn in top.items():
        names = {_call_name(c) for c in _calls_in(fn)}
        if "_rebuild_code" in names and "push_to_neo4j" in names:
            pipe = fn
            break
    res = {"function": pipe.name if pipe else None, "steps": [], "unknown": [], "problems": [],
           "typed_helper": "_typed_calls_link" in top}
    if pipe is None:
        res["problems"].append("no function calls both _rebuild_code and push_to_neo4j")
        return res
    calls = _calls_in(pipe)
    names = [_call_name(c) for c in calls]
    i0 = names.index("_rebuild_code")
    i1 = names.index("push_to_neo4j")
    for c, n in zip(calls[i0:i1 + 1], names[i0:i1 + 1]):
        if n in STEP_NAMES:
            res["steps"].append(n)
    res["unknown"] = _unmirrored(pipe, calls[i0].lineno, calls[i1].lineno)
    sync_fn = top.get("sync")
    res["reached_from_sync"] = pipe.name == "sync" or (
        sync_fn is not None and pipe.name in {_call_name(c) for c in _calls_in(sync_fn)})
    push = top.get("push_to_neo4j")
    first = None
    if push is not None:
        for c in _calls_in(push):
            if _arg_names(c) & {"calls"}:
                first = c
                break
    res["push_first_resolve"] = bool(first is not None and _call_name(first) == "_resolve_calls"
                                     and [getattr(a, "id", None) for a in first.args[:2]] == ["nodes", "calls"])
    if push is not None and first is not None:
        for n in ast.walk(push):
            if isinstance(n, (ast.Assign, ast.AugAssign, ast.AnnAssign)) and n.lineno < first.lineno:
                tg = n.targets if isinstance(n, ast.Assign) else [n.target]
                if any(isinstance(t, ast.Name) and t.id in ("nodes", "calls") for x in tg for t in ast.walk(x)):
                    res["problems"].append(f"push_to_neo4j rebinds nodes/calls before _resolve_calls (line {n.lineno})")
    return res


def order_verdict(res: dict) -> "str | None":
    """None when the sync order is one the offline tool mirrors, else why not."""
    if res["problems"]:
        return "; ".join(res["problems"])
    if res["steps"] not in (ORDER_NOW, ORDER_FUTURE):
        return f"step order {res['steps']} is neither the current nor the typed-calls order"
    if res["unknown"]:
        return f"unmirrored change(s) to nodes/calls between extraction and the push: {res['unknown']}"
    if (res["steps"] == ORDER_FUTURE) != res["typed_helper"]:
        return ("sync calls _typed_calls_link without defining it at module level" if res["steps"] == ORDER_FUTURE
                else "sync defines _typed_calls_link but its pipeline does not call it (the offline mirror would)")
    if not res["reached_from_sync"]:
        return f"{res['function']} is not called by sync()"
    if not res["push_first_resolve"]:
        return "push_to_neo4j does not start from _resolve_calls(nodes, calls)"
    return None


_SYNTH = '''
def sync(root):
    return _sync_pass(root)

def _sync_pass(root):
    extracted = _rebuild_code(root)
    nodes, calls, imports, engine = extracted
    enrich.apply_sidecar(root, nodes)
    nodes, typed_edges = _apply_recognizers(root, nodes)
    plan = _plan_prune(driver, root, nodes, engine, prior, force)
    calls, n_unkey = _unkey_receiver_calls(root, nodes, calls)
{typed}    n_bare = _attach_bare_calls(root, nodes, calls)
{after_bare}    _attach_local_defs(root, nodes, calls)
    n_local = _attach_local_def_calls(root, nodes, calls)
    fts_changed = _fts_changed_docs(driver, nodes) if nodes else 0
    n_pushed, n_edges = push_to_neo4j(driver, nodes, calls, imports, typed_edges)

def push_to_neo4j(driver, nodes, calls, imports, typed_edges=None):
    if not nodes:
        return 0, 0
    edges = _resolve_calls(nodes, calls)
{helper}'''
_TYPED_LINE = "    calls, call_stats = _typed_calls_link(root, nodes, calls)\n"
_HELPER = "\ndef _typed_calls_link(root, nodes, calls):\n    return calls, None\n"


def gate_b(mods: dict, sync_path: Path, kit: Path) -> None:
    print("\n-- B: the offline pass order equals sync()'s (read from build/sync.py's AST) --")
    cases = {
        "the current order": (dict(typed="", after_bare="", helper=""), True),
        "the typed-calls order (+ its helper)": (dict(typed=_TYPED_LINE, after_bare="", helper=_HELPER), True),
        "the typed step without its helper": (dict(typed=_TYPED_LINE, after_bare="", helper=""), False),
        "the helper defined but never called": (dict(typed="", after_bare="", helper=_HELPER), False),
        "the typed step AFTER _attach_bare_calls": (dict(typed="", after_bare=_TYPED_LINE, helper=_HELPER), False),
        "an unmirrored pass on calls": (dict(typed="", after_bare="    _attach_foo(root, nodes, calls)\n", helper=""),
                                        False),
    }
    # The shapes a concurrent edit of sync.py could add between extraction and the push without a direct
    # `f(nodes, calls)` call (each must be REJECTED), and read-only shapes that must stay ACCEPTED.
    mutations = {
        "a comprehension rebinding calls": ("    calls = [(a, b) for (a, b) in calls if not str(b).startswith('_')]\n",
                                            False),
        "calls.append(...)": ("    calls.append(('x', 'y'))\n", False),
        "a call hiding calls inside a nested call": ("    calls = _drop_calls(list(calls))\n", False),
        "a slice assignment into calls": ("    calls[:] = calls[:1]\n", False),
        "nodes filtered by a comprehension": ("    nodes = [n for n in nodes if n.get('label') != 'Method']\n", False),
        "a write into a node": ("    nodes[0]['name'] = 'x'\n", False),
        "calls passed as a keyword argument": ("    calls = _drop_calls(items=calls)\n", False),
        "calls passed inside a keyword argument's expression": ("    _check(items=sorted(calls)[:5])\n", False),
        "an augmented assignment to calls": ("    calls += [('x', 'y')]\n", False),
        "del calls[0]": ("    del calls[0]\n", False),
        "a loop writing into each node": ("    for n in nodes:\n        n['x'] = 1\n", False),
        "a loop calling a mutator on each node": ("    for i, n in enumerate(nodes):\n        n.setdefault('x', i)\n",
                                                  False),
        "a comprehension calling a mutator on each node": ("    _ = [n.update(x=1) for n in nodes]\n", False),
        "an alias of calls, then a mutation through it": ("    c2 = calls\n    c2.append(('x', 'y'))\n", False),
        "an element alias, then a write into it": ("    first = nodes[0]\n    first['name'] = 'x'\n", False),
        "a read-only loop over nodes": ("    for n in nodes:\n        print(n.get('label'), len(n))\n", True),
        "read-only builtins over calls": ("    print(len(calls), sorted({c for _a, c in calls})[:1])\n", True),
    }
    for label, (line, want) in mutations.items():
        cases[label] = (dict(typed="", after_bare=line, helper=""), want)
    for label, (fill, want) in cases.items():
        v = order_verdict(sync_order(_SYNTH.format(**fill)))
        check(f"self-test: the checker {'accepts' if want else 'rejects'} {label}", (v is None) == want, v)
    swapped = _SYNTH.format(typed="", after_bare="", helper="").replace(
        "    _attach_local_defs(root, nodes, calls)\n    n_local = _attach_local_def_calls(root, nodes, calls)\n",
        "    n_local = _attach_local_def_calls(root, nodes, calls)\n    _attach_local_defs(root, nodes, calls)\n")
    check("self-test: the checker rejects two passes swapped", order_verdict(sync_order(swapped)) is not None)
    try:
        res = sync_order(sync_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        check(f"{sync_path.name} parses", False, repr(exc))
        return
    v = order_verdict(res)
    check(f"{sync_path.name}: {res['function']} runs exactly the current or the typed-calls order (found: "
          f"{'typed-calls' if res['steps'] == ORDER_FUTURE else 'current' if res['steps'] == ORDER_NOW else res['steps']})",
          v is None, v)
    oc_path = kit / "offline_calls.py"
    try:
        tree = ast.parse(oc_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        check("offline_calls.py parses", False, repr(exc))
        return
    fns = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    rp = fns.get("run_pipeline")
    mapping = {"typed_calls_step": "_typed_calls_link", "_resolve_calls": "push_to_neo4j"}
    steps = []
    if rp is not None:
        for c in _calls_in(rp):
            n = _call_name(c)
            n = n[2:] if n.startswith("S.") else n
            n = mapping.get(n, n)
            if n in STEP_NAMES:
                steps.append(n)
    check("offline_calls.run_pipeline runs the typed-calls order (the step is a no-op while sync lacks it)",
          steps == ORDER_FUTURE, steps)
    ts = fns.get("typed_calls_step")
    guarded = ts is not None and any(
        isinstance(n, ast.Call) and _call_name(n) == "getattr" and len(n.args) >= 3
        and isinstance(n.args[1], ast.Constant) and n.args[1].value == "_typed_calls_link"
        for n in ast.walk(ts))
    check("offline_calls reaches the typed step only through getattr(S, '_typed_calls_link', None) (guarded)",
          guarded)
    oc = mods.get("offline_calls")
    if oc is not None:
        want = tuple("_resolve_calls" if s == "push_to_neo4j" else s for s in ORDER_FUTURE)
        check("offline_calls.PIPELINE spells the same order", tuple(getattr(oc, "PIPELINE", ())) == want,
              getattr(oc, "PIPELINE", None))
        dummy = SimpleNamespace()
        sentinel = [("a", "b")]
        check("typed_calls_step: a sync without the helper returns the SAME list and no stats",
              oc.typed_calls_step(dummy, None, [], sentinel) == (sentinel, None)
              and oc.typed_calls_step(dummy, None, [], sentinel)[0] is sentinel)
        seen = []
        dummy2 = SimpleNamespace(_typed_calls_link=lambda r, n, c: (seen.append((r, len(n), len(c))) or (c + [("x", "y")], {"ok": 1})))
        out = oc.typed_calls_step(dummy2, "R", [1], sentinel)
        check("typed_calls_step: a sync with the helper calls it once with (root, nodes, calls) and returns its "
              "(calls, stats)", seen == [("R", 1, 1)] and out == (sentinel + [("x", "y")], {"ok": 1}), (seen, out))


# =====================================================================================================
# C — data flow: the real sync() pass vs offline_calls.run_pipeline on the same extraction
# =====================================================================================================
PROJECT = {
    "pkg/__init__.py": "",
    "pkg/models.py": (
        "class Order:\n"
        "    def save(self):\n"
        "        return self.validate()\n\n"
        "    def validate(self):\n"
        "        return True\n\n\n"
        "class Invoice:\n"
        "    def save(self):\n"
        "        return 1\n\n\n"
        "class Serializer:\n"
        "    def dumps(self, obj):\n"
        "        return str(obj)\n\n\n"
        "def helper():\n"
        "    return 2\n"),
    "pkg/service.py": (
        "from pkg.models import Order, helper\n\n\n"
        "def place():\n"
        "    o = Order()\n"
        "    o.save()\n"
        "    helper()\n"
        "    return save()\n\n\n"
        "def save():\n"
        "    return 3\n\n\n"
        "class Runner:\n"
        "    def run(self):\n"
        "        self.step()\n"
        "        return place()\n\n"
        "    def step(self):\n"
        "        return helper()\n\n\n"
        "def finish():\n"
        "    return save()\n"),
    "pkg/other.py": (
        "from json import dumps\n\n\n"
        "def helper():\n"
        "    return 4\n\n\n"
        "def use():\n"
        "    dumps({})\n"
        "    return helper()\n"),
    "tests/test_service.py": (
        "from pkg.service import place\n\n\n"
        "def test_place():\n"
        "    place()\n"),
}


def _write_project(root: Path) -> Path:
    for rel, text in PROJECT.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    (root / ".deepmap-root").write_text("", encoding="utf-8")
    return root


class _Res(list):
    def single(self):
        return self[0] if self else None

    def consume(self):
        return None

    def data(self):
        return []


class _Sess:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, *a, **k):
        return _Res()

    def begin_transaction(self):
        return self

    def commit(self):
        return None

    def close(self):
        return None


class _Drv:
    def session(self, *a, **k):
        return _Sess()

    def verify_connectivity(self):
        return None

    def close(self):
        return None


def _fresh_sync():
    if str(BUILD) not in sys.path:
        sys.path.insert(0, str(BUILD))
    return _load_file(BUILD / "sync.py", f"_cl_s0_sync_{time.time_ns()}")


def _clone_extraction(S, ex):
    nodes, calls, imports, engine = ex
    return S._Extraction([copy.deepcopy(n) for n in nodes], list(calls), list(imports or []), engine,
                         copy.deepcopy(getattr(ex, "info", {}) or {}))


def _sync_edges(root: Path, frozen) -> dict:
    """The REAL sync() pass on `root` with the extraction frozen and push_to_neo4j recorded: the :CALLS dict
    sync's push would write (its own _resolve_calls on exactly the nodes/calls sync handed the push)."""
    S = _fresh_sync()
    got = {}

    def _push(driver, nodes, calls, imports, typed_edges=None):
        got["edges"] = S._resolve_calls(nodes, calls)
        got["calls"] = list(calls)
        return len(nodes), len(got["edges"])
    S._rebuild_code = lambda r: _clone_extraction(S, frozen)
    S.push_to_neo4j = _push
    S._graph_has_nodes = lambda d: False
    S._graph_holds_nodes = lambda d: False
    S._graph_node_count = lambda d: 0
    S._prune_blast_radius = lambda d, live: (0, 0)
    S._cleanup_stale = lambda d, live: None
    S._reuse_rekeyed_embeddings = lambda d, ns: 0
    S.indexes = SimpleNamespace(create_all_indexes=lambda d: {}, wait_for_indexes=lambda d, timeout=120: True,
                                VECTOR_INDEXES=(), vector_quantization_disabled=lambda: False)
    S.embeddings = SimpleNamespace(generate_embeddings=lambda d: 0)
    S._count_embedded_nodes = lambda d, fallback=0: 0
    S._maybe_auto_tune = lambda r: None
    so, se = _quiet()
    with so, se:
        ok = S.sync(root, driver=_Drv())
    got["ok"] = ok
    return got


def gate_c(mods: dict, base: Path, real: bool) -> None:
    print("\n-- C: the real sync() pass and offline_calls.run_pipeline push the identical :CALLS dict --")
    oc = mods.get("offline_calls")
    if oc is None:
        check("offline_calls is importable for the data-flow check", False)
        return
    if str(BUILD) not in sys.path:
        sys.path.insert(0, str(BUILD))
    import graphify_extract as GX  # noqa: E402
    engines =[("ast", {"DEEPMAP_EXTRACTOR": "ast"})]
    have_graphify = importlib.util.find_spec("graphify") is not None
    if real and have_graphify:
        engines.append(("graphify", {"DEEPMAP_EXTRACTOR": "graphify"}))
    elif not have_graphify:
        skip("C: the graphify-engine modes", "graphify is not installed in this Python")
    modes = {"default": {}, "DEEPMAP_PRECISE_CALLS=1": {"DEEPMAP_PRECISE_CALLS": "1"},
             "DEEPMAP_LOCAL_DEF_CALLS=1": {"DEEPMAP_LOCAL_DEF_CALLS": "1"}}
    S0 = _fresh_sync()
    if callable(getattr(S0, "_typed_calls_link", None)) and importlib.util.find_spec("typed_calls") is not None:
        modes["DEEPMAP_TYPED_CALLS=python"] = {"DEEPMAP_TYPED_CALLS": "python", "DEEPMAP_CALLS_AUDIT": "1"}
    teeth = {}                                   # pass -> the (engine, mode) where skipping it changed the edges
    for eng, eng_env in engines:
        for mode, menv in modes.items():
            root = _write_project(base / f"proj_{eng}_{re.sub(r'[^A-Za-z0-9]+', '_', mode)}")
            home = base / f"home_{eng}"
            env = dict(_graph_flags_cleared(), **eng_env, **menv, NEO4J_URI="bolt://127.0.0.1:9",
                       DEEPMAP_HOME=str(home), DEEPMAP_AUTO_GITIGNORE="0")
            try:
                with _env(**env):
                    S = _fresh_sync()
                    so, se = _quiet()
                    with so, se:
                        frozen = S._rebuild_code(root)
                    synced = _sync_edges(root, frozen)
                    S2 = _fresh_sync()
                    with so, se:
                        _n, calls2, edges2, meta = oc.run_pipeline(S2, root, extraction=_clone_extraction(S2, frozen))
            except Exception as exc:  # noqa: BLE001
                check(f"C [{eng} | {mode}] runs", False, f"{exc!r}\n{traceback.format_exc()[-600:]}")
                continue
            e1 = synced.get("edges")
            check(f"C [{eng} | {mode}]: sync() pushed {len(e1 or {})} CALLS edges; offline_calls computes the identical "
                  f"dict, in the same order (engine {meta.get('engine')}, {len(frozen[0])} nodes)",
                  synced.get("ok") is True and e1 is not None and list(e1.items()) == list(edges2.items()),
                  {"ok": synced.get("ok"), "only_sync": sorted(set(e1 or {}) - set(edges2))[:5],
                   "only_offline": sorted(set(edges2) - set(e1 or {}))[:5]})
            check(f"C [{eng} | {mode}]: ... and the calls handed to the push are identical (keys stay marked)",
                  [(a, b, GX.is_call_key(b)) for a, b in synced.get("calls", [])]
                  == [(a, b, GX.is_call_key(b)) for a, b in calls2])
            for skipped_pass in ("_unkey_receiver_calls", "_attach_bare_calls", "_attach_local_defs",
                                 "_attach_local_def_calls"):
                with _env(**env):
                    S3 = _fresh_sync()
                    ex = _clone_extraction(S3, frozen)
                    so, se = _quiet()
                    with so, se:
                        nodes3, _typed3 = S3._apply_recognizers(root, ex[0])
                        calls3 = ex[1]
                        if skipped_pass != "_unkey_receiver_calls":
                            calls3, _u = S3._unkey_receiver_calls(root, nodes3, calls3)
                        for step in ("_attach_bare_calls", "_attach_local_defs", "_attach_local_def_calls"):
                            if step != skipped_pass:
                                getattr(S3, step)(root, nodes3, calls3)
                        without = S3._resolve_calls(nodes3, calls3)
                if without != e1:
                    teeth.setdefault(skipped_pass, f"{eng} | {mode}")
    check("C: not vacuous — skipping _attach_local_def_calls changes the pushed edges (DEEPMAP_LOCAL_DEF_CALLS=1)",
          "_attach_local_def_calls" in teeth, teeth)
    if any(e == "graphify" for e, _x in engines):
        check("C: not vacuous — skipping _attach_bare_calls changes the pushed edges (graphify: a bare-only `save()` "
              "keeps only the module function)", "_attach_bare_calls" in teeth, teeth)
    # The CLI: deterministic bytes, and its guards.
    root = _write_project(base / "cli_proj")
    home = base / "cli_home"
    oc_path = Path(oc.__file__)
    env = dict(os.environ)
    for k in list(env):
        if k.startswith("DEEPMAP_") or k == "NEO4J_URI":
            env.pop(k)
    env.update(DEEPMAP_HOME=str(home), DEEPMAP_EXTRACTOR="ast", PYTHONUTF8="1", PYTHONHASHSEED="0",
               CALL_LINKING_READONLY_ROOTS=str(base / "ro_nothing"))
    outs = []
    for rep in ("a", "b"):
        o = base / f"cli_{rep}.json"
        p = subprocess.run([sys.executable, str(oc_path), "build", str(root), str(o), "--build", str(BUILD)],
                           env=env, capture_output=True, text=True, timeout=600)
        outs.append((p.returncode, o.read_bytes() if o.is_file() else b"", p.stderr[-400:]))
    check("offline_calls build: two runs on the same tree are byte-identical (no timings in the dump)",
          outs[0][0] == 0 and outs[1][0] == 0 and outs[0][1] == outs[1][1] and outs[0][1], [o[0] for o in outs] + [outs[0][2]])
    if outs[0][1]:
        d = json.loads(outs[0][1])
        check("... the dump records schema, code hash, engine, flags, the effective switches and sorted edges",
              d.get("schema") == "call-linking/offline-calls/1" and len((d.get("code") or {}).get("sha256", "")) == 64
              and d.get("engine") == "ast" and isinstance(d.get("effective"), dict)
              and d.get("edges") == sorted(d.get("edges") or []),
              {k: d.get(k) for k in ("schema", "engine", "effective")})
    pv = subprocess.run([sys.executable, str(oc_path), "build", str(root), str(base / "void.json"), "--build", str(BUILD),
                         "--expect-engine", "graphify"], env=env, capture_output=True, text=True, timeout=600)
    vd = json.loads((base / "void.json").read_text(encoding="utf-8")) if (base / "void.json").is_file() else {}
    pa = subprocess.run([sys.executable, str(oc_path), "build", str(root), str(base / "ok_ast.json"), "--build",
                         str(BUILD), "--expect-engine", "ast"], env=env, capture_output=True, text=True, timeout=600)
    check("offline_calls --expect-engine graphify: a dump that resolved to another engine is VOID (exit 3, `void` "
          "recorded); the engine it did resolve to passes",
          pv.returncode == 3 and "void" in vd and (vd.get("extraction") or {}).get("resolved") == "ast"
          and pa.returncode == 0, (pv.returncode, pa.returncode, pv.stderr[-300:]))
    pz = subprocess.run([sys.executable, str(oc_path), "build", str(root), str(base / "inert.json"), "--build",
                         str(BUILD)], env=dict(env, DEEPMAP_ZZ_NOT_A_FLAG="1"), capture_output=True, text=True,
                        timeout=600)
    zd = json.loads((base / "inert.json").read_text(encoding="utf-8")) if (base / "inert.json").is_file() else {}
    check("offline_calls build: a variant flag the code under test never names is VOID (exit 3, `void` recorded) — "
          "an inert variant is never scored as 'no effect'",
          pz.returncode == 3 and "DEEPMAP_ZZ_NOT_A_FLAG" in str(zd.get("void")), (pz.returncode, zd.get("void"),
                                                                               pz.stderr[-300:]))
    env_nohome = {k: v for k, v in env.items() if k != "DEEPMAP_HOME"}
    p = subprocess.run([sys.executable, str(oc_path), "build", str(root), str(base / "x.json")], env=env_nohome,
                       capture_output=True, text=True, timeout=120)
    check("offline_calls refuses to run without DEEPMAP_HOME (exit 2, nothing written)",
          p.returncode == 2 and not (base / "x.json").exists(), p.stderr[-300:])
    fake_user = base / "user"
    (fake_user / ".deepmap").mkdir(parents=True)
    env_op = dict(env, DEEPMAP_HOME=str(fake_user / ".deepmap"), USERPROFILE=str(fake_user), HOME=str(fake_user))
    p = subprocess.run([sys.executable, str(oc_path), "build", str(root), str(base / "y.json")], env=env_op,
                       capture_output=True, text=True, timeout=120)
    check("offline_calls refuses the operator's own ~/.deepmap as DEEPMAP_HOME (exit 2)",
          p.returncode == 2 and not (base / "y.json").exists(), p.stderr[-300:])
    env_ro = dict(env, CALL_LINKING_READONLY_ROOTS=str(base))
    p = subprocess.run([sys.executable, str(oc_path), "build", str(root), str(base / "z.json")], env=env_ro,
                       capture_output=True, text=True, timeout=120)
    check("offline_calls refuses a corpus under a read-only clone root (copy it first; exit 2)",
          p.returncode == 2 and not (base / "z.json").exists(), p.stderr[-300:])
    gate_c_wallclock(mods, base, env)
    gate_c_vf(mods, base, env, real and have_graphify)


# A key that names a wall-clock value (`seconds`, `t_total_s`, `elapsed`, `duration_ms`, ...): a dump must hold none.
# Case-sensitive on purpose: typed_calls' shape names are single capitals (`by_shape.S` is shape S, not seconds).
_WALL_CLOCK_KEY = re.compile(r"(?:^|_)(?:seconds|secs|elapsed|duration|wall|timestamp|time)$|_(?:s|ms|ts)$")


def _wall_clock_keys(obj, path="") -> list:
    """Every dict key under `obj` (recursively) that names a wall-clock value, as a dotted path."""
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else str(k)
            if _WALL_CLOCK_KEY.search(str(k)):
                out.append(p)
            out += _wall_clock_keys(v, p)
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:2000]):
            out += _wall_clock_keys(v, f"{path}[{i}]")
    return out


@contextlib.contextmanager
def _fake_clock(step: float):
    """Every clock of the `time` module (perf_counter / monotonic / time / process_time / thread_time and their
    _ns forms) advances by `step` seconds per read: two runs under two steps measure two different wall times."""
    names = [n for n in ("perf_counter", "monotonic", "time", "process_time", "thread_time") if hasattr(time, n)]
    saved = {n: getattr(time, n) for n in names}
    saved.update({f"{n}_ns": getattr(time, f"{n}_ns") for n in names if hasattr(time, f"{n}_ns")})
    state = {"t": 1000.0}

    def tick():
        state["t"] += step
        return state["t"]
    try:
        for n in names:
            setattr(time, n, tick)
            if f"{n}_ns" in saved:
                setattr(time, f"{n}_ns", lambda: int(tick() * 1e9))
        yield
    finally:
        for n, f in saved.items():
            setattr(time, n, f)


def gate_c_wallclock(mods: dict, base: Path, env: dict) -> None:
    """The determinism check (PREREG §8.4 G: each dump run twice is byte-identical) is about graph CONTENT; a timing
    is P-layer data. typed_calls.link's stats carry the pass's own wall time (`seconds`, build/typed_calls.py: "the
    only non-deterministic value"), and the kit used to copy them verbatim into the dump's `call_linking` block — so
    almost every typed-variant pair of the first RC matrix read "the two runs differ" (2026-10-24 coordinator ruling).
    Locked here: make_dump writes no wall-clock field (typed stats included) and leaves the caller's stats intact;
    strip_wall_clock turns a pre-fix dump into exactly that; two in-process runs of the typed pass under two
    different fake clocks give byte-identical dumps (any time-derived value reaching the dump fails this, whatever
    its name); the CLI's two typed runs are byte-identical and the pass's own seconds go to <out>.timing.json."""
    oc = mods.get("offline_calls")
    if oc is None or not callable(getattr(oc, "make_dump", None)):
        check("C wall clock: offline_calls.make_dump is importable", False)
        return
    xn = [_n("a.f", "Function", "f", "a.py", 3), _n("a.g", "Function", "g", "a.py", 10)]
    stats = {"version": 4, "engine": "ast", "emitted": 3, "removed": 1, "grades": {"EXTRACTED": 1, "INFERRED": 2},
             "notes": [], "error": None, "seconds": 1.234}
    meta = {"info": {}, "call_stats": stats, "typed_step": "present", "n_unkey": 0, "n_bare": 0, "n_local": 0}
    try:
        d = oc.make_dump(base, xn, [("a.f", "a.g")], {("a.f", "a.g"): "INFERRED"}, meta, code=None)
    except Exception as exc:  # noqa: BLE001
        check("C wall clock: make_dump builds a dump from typed-call stats", False, repr(exc))
        return
    cl = d.get("call_linking")
    check("offline_calls.make_dump: a dump built from typed-call stats carries NO wall-clock field (call_linking "
          "without typed_calls' `seconds`, every other stat kept; no timing key anywhere in the dump) and the caller's "
          "stats keep `seconds` (it is P-layer data: the timing sidecar's)",
          isinstance(cl, dict) and "seconds" not in cl and cl.get("emitted") == 3 and cl.get("removed") == 1
          and cl.get("grades") == {"EXTRACTED": 1, "INFERRED": 2} and not _wall_clock_keys(d)
          and stats.get("seconds") == 1.234,
          {"call_linking": cl, "wall_clock_keys": _wall_clock_keys(d), "caller_stats_seconds": stats.get("seconds")})
    strip = getattr(oc, "strip_wall_clock", None)
    if callable(strip):
        old = copy.deepcopy(d)
        old["call_linking"] = dict(old.get("call_linking") or {}, seconds=9.876)
        none_cl = dict(copy.deepcopy(d), call_linking=None)
        dj = oc.C.dump_json
        check("offline_calls.strip_wall_clock: a dump of the pre-fix kit (call_linking.seconds present) reduces to "
              "exactly the bytes make_dump writes now — that field and nothing else — and leaves its input unchanged; "
              "a dump with no typed stats is unchanged (the determinism re-judge of pre-fix pairs uses it)",
              dj(strip(old)) == dj(d) and old["call_linking"].get("seconds") == 9.876
              and dj(strip(none_cl)) == dj(none_cl) and dj(strip(d)) == dj(d),
              {"stripped_cl": (strip(old) or {}).get("call_linking")})
    else:
        check("offline_calls.strip_wall_clock exists (removes exactly the wall-clock fields from a dump)", False)
    # In process, the real pass under two different fake clocks: any time-derived value reaching the dump shows.
    if str(BUILD) not in sys.path:
        sys.path.insert(0, str(BUILD))
    S0 = _fresh_sync()
    if not (callable(getattr(S0, "_typed_calls_link", None)) and importlib.util.find_spec("typed_calls") is not None):
        skip("C wall clock: the typed pass under two fake clocks / the CLI's typed runs",
             "build/sync.py has no typed-call step or build/typed_calls.py is absent")
        return
    tenv = {"DEEPMAP_TYPED_CALLS": "python", "DEEPMAP_CALLS_AUDIT": "1"}
    root = _write_project(base / "wallclock_proj")
    run_env = dict(_graph_flags_cleared(), DEEPMAP_EXTRACTOR="ast", NEO4J_URI="bolt://127.0.0.1:9",
                   DEEPMAP_HOME=str(base / "wallclock_home"), DEEPMAP_AUTO_GITIGNORE="0", **tenv)
    dumps, secs = [], []
    try:
        with _env(**run_env):
            S = _fresh_sync()
            so, se = _quiet()
            with so, se:
                frozen = S._rebuild_code(root)
            for step in (0.25, 7.0):
                S2 = _fresh_sync()
                so, se = _quiet()
                with so, se, _fake_clock(step):
                    nodes, calls, edges, m = oc.run_pipeline(S2, root, extraction=_clone_extraction(S2, frozen))
                m["effective"] = oc.effective_switches(S2)
                dumps.append(oc.C.dump_json(oc.make_dump(root, nodes, calls, edges, m, code=None)))
                secs.append((m.get("call_stats") or {}).get("seconds"))
    except Exception as exc:  # noqa: BLE001
        check("C wall clock: the typed pass runs in process under a fake clock", False,
              f"{exc!r}\n{traceback.format_exc()[-600:]}")
        return
    d0 = json.loads(dumps[0]) if dumps else {}
    cl0 = d0.get("call_linking")
    check("offline_calls: the real typed pass (DEEPMAP_TYPED_CALLS=python + DEEPMAP_CALLS_AUDIT=1, ast engine) run "
          "twice on one extraction under two different fake clocks gives byte-identical dumps — the pass's own "
          "`seconds` differed (the clocks bit) and reached no byte of the dump",
          len(dumps) == 2 and dumps[0] == dumps[1] and isinstance(cl0, dict) and not cl0.get("error")
          and len(secs) == 2 and None not in secs and secs[0] != secs[1] and not _wall_clock_keys(d0),
          {"seconds": secs, "call_linking_error": (cl0 or {}).get("error") if isinstance(cl0, dict) else cl0,
           "wall_clock_keys": _wall_clock_keys(d0)[:5],
           "first_diff": next((i for i, (x, y) in enumerate(zip(*dumps)) if x != y), None) if len(dumps) == 2 else None})
    # The CLI: two typed runs, byte-identical; the pass's own seconds in the sidecar.
    cenv = dict(env, **tenv)
    outs = []
    for rep in ("a", "b"):
        o = base / f"cli_typed_{rep}.json"
        p = subprocess.run([sys.executable, str(Path(oc.__file__)), "build", str(root), str(o), "--build", str(BUILD)],
                           env=cenv, capture_output=True, text=True, timeout=600)
        tf = Path(str(o) + ".timing.json")
        outs.append((p.returncode, o.read_bytes() if o.is_file() else b"",
                     json.loads(tf.read_text(encoding="utf-8")) if tf.is_file() else None, p.stderr[-400:]))
    da = json.loads(outs[0][1]) if outs[0][1] else {}
    cla = da.get("call_linking")
    timings = [o[2] for o in outs]
    check("offline_calls build with the typed pass on: two runs on the same tree are byte-identical (exit 0), the dump "
          "holds the typed stats without any wall-clock field",
          [o[0] for o in outs] == [0, 0] and outs[0][1] and outs[0][1] == outs[1][1] and isinstance(cla, dict)
          and not cla.get("error") and "seconds" not in cla and not _wall_clock_keys(da),
          {"rc": [o[0] for o in outs], "identical": outs[0][1] == outs[1][1], "wall_clock_keys": _wall_clock_keys(da)[:5],
           "stderr": outs[0][3]})
    check("... and each run's timing sidecar (<out>.timing.json) carries the P-layer timings: the typed pass's own "
          "seconds (call_linking_seconds, as typed_calls measured it) beside t_extract_s / t_typed_s / t_total_s",
          all(isinstance(t, dict) and isinstance(t.get("call_linking_seconds"), (int, float))
              and not isinstance(t.get("call_linking_seconds"), bool)
              and all(isinstance(t.get(k), (int, float)) for k in ("t_extract_s", "t_typed_s", "t_total_s"))
              for t in timings), timings)


# The review's probe: a Python and a TypeScript caller of `helper()`, with a `helper` in each language.
VF_PROJECT = {"a.py": "from c import helper\n\n\ndef main():\n    return helper()\n",
              "c.py": "def helper():\n    return 2\n",
              "b.ts": ("export function helper(): number {\n  return 1;\n}\n\n"
                       "export function run(): number {\n  return helper();\n}\n")}


def gate_c_vf(mods: dict, base: Path, env: dict, graphify: bool) -> None:
    """End to end, the real CLI on the real engine: VF (DEEPMAP_CALLS_FAMILY_FILTER=1) against V0 under the
    variants' clean environments. The filter removes the cross-family targets, and delta measures it — never VOID
    as an inert pair (the review found every VF delta VOID: `effective` did not report the filter)."""
    V, D, oc = mods.get("variants"), mods.get("delta"), mods.get("offline_calls")
    if not graphify:
        skip("C: VF vs V0 end to end (offline_calls build + delta)", "needs the graphify engine (TypeScript)")
        return
    if V is None or D is None or oc is None:
        check("C: variants / delta / offline_calls are importable for the VF end-to-end check", False)
        return
    root = base / "vf_proj"
    for rel, text in VF_PROJECT.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    (root / ".deepmap-root").write_text("", encoding="utf-8")
    rcs, dumps = {}, {}
    for vid in ("V0", "VF"):
        out = base / f"vf_{vid}.json"
        p = subprocess.run([sys.executable, str(Path(oc.__file__)), "build", str(root), str(out), "--build", str(BUILD),
                            "--expect-engine", "graphify"], env=V.child_env(vid, base=env), capture_output=True,
                           text=True, timeout=900)
        rcs[vid] = (p.returncode, p.stderr[-300:])
        dumps[vid] = json.loads(out.read_text(encoding="utf-8")) if out.is_file() else {}
    rc_d, out_d, _e, ex_d = _cli(D.main, [str(base / "vf_V0.json"), str(base / "vf_VF.json")])
    d = D.delta(dumps.get("V0") or {}, dumps.get("VF") or {}) if dumps.get("V0") and dumps.get("VF") else {}

    def ext(f):
        return Path(str(f or "")).suffix.lower()
    removed = (d.get("items") or {}).get("removed") or []
    check("C: VF vs V0 end to end on the real engine — both dumps are data (exit 0), the filter removed only "
          "cross-family targets, and delta measures it (exit 0, not VOID)",
          rcs["V0"][0] == 0 and rcs["VF"][0] == 0 and (rc_d, ex_d) == (0, None) and d.get("void") == []
          and len(removed) == 2 and all(ext(i["caller_file"]) != ext(i["callee_file"]) for i in removed)
          and (dumps["VF"].get("effective") or {}).get("calls_family_filter") is True
          and (dumps["V0"].get("effective") or {}).get("calls_family_filter") is False,
          {"rc": rcs, "delta": (rc_d, ex_d, out_d[-400:]), "removed": [(i["caller"], i["callee"]) for i in removed]})


# =====================================================================================================
# D — the frozen flask graph
# =====================================================================================================
def gate_d(mods: dict, kit: Path, base: Path, real: bool) -> None:
    print("\n-- D: the offline counts reproduce the frozen flask graph (330 CALLS / 26 AMBIGUOUS on 0.8.39) --")
    try:
        frozen = json.loads((kit / "frozen" / "flask_V0.json").read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        check("frozen/flask_V0.json loads", False, repr(exc))
        return
    e39 = (frozen.get("engines") or {}).get("0.8.39") or {}
    check("the frozen 0.8.39 flask graph is the registered one: 330 CALLS, 26 AMBIGUOUS",
          e39.get("n_edges") == 330 and (e39.get("conf") or {}).get("AMBIGUOUS") == 26, e39)
    if not real:
        skip("D: re-dump flask", "--no-real")
        return
    clones = Path(os.environ.get("CALL_LINKING_CLONES", "C:/tmp/deepmap-bench-clones"))
    src = clones / "pallets__flask"
    if not (src / ".git").exists():
        skip("D: re-dump flask", f"no benchmark clone at {src} (set CALL_LINKING_CLONES)")
        return
    try:
        import importlib.metadata as md
        ver = md.version("graphifyy")
    except Exception:  # noqa: BLE001
        ver = None
    want = (frozen.get("engines") or {}).get(ver or "")
    if not want:
        skip("D: re-dump flask", f"graphifyy {ver} has no frozen flask graph (frozen: {sorted(frozen.get('engines') or {})})")
        return
    dest = base / "flask"
    for argv in (["git", "clone", "-q", "--no-hardlinks", str(src), str(dest)],
                 ["git", "-C", str(dest), "checkout", "-q", "--detach", FLASK_SHA]):
        p = subprocess.run(argv, capture_output=True, text=True, timeout=600)
        if p.returncode != 0:
            check("D: copy the flask clone at its pinned SHA", False, p.stderr[-300:])
            return
    oc = mods["offline_calls"]
    V = mods["variants"]
    env = V.child_env("V0")
    env.update(DEEPMAP_HOME=str(base / "flask_home"))
    out = base / "flask_V0.json"
    p = subprocess.run([sys.executable, oc.__file__, "build", str(dest), str(out), "--build", str(BUILD),
                        "--expect-engine", "graphify"], env=env, capture_output=True, text=True, timeout=1800)
    void = [ln for ln in (p.stderr or "").splitlines() if ln.startswith("[offline_calls] VOID")]
    check("D: offline_calls dumps flask engine-pinned (--expect-engine graphify: exit 0, no VOID — an ast fallback "
          "would be named here, not read as a count mismatch)", p.returncode == 0 and out.is_file() and not void,
          f"exit {p.returncode}: {void[:1] or p.stderr[-500:]}")
    if p.returncode != 0 or not out.is_file():
        return
    d = json.loads(out.read_text(encoding="utf-8"))
    C = mods["_common"]
    sk = {n["key"]: C.stable_key(n) for n in d["nodes"]}
    h = hashlib.sha256("\n".join(sorted(f"{sk[a]}\t{sk[b]}\t{g}" for a, b, g in d["edges"])).encode("utf-8")).hexdigest()
    got = {"n_nodes": d["counts"]["n_nodes"], "n_edges": d["counts"]["n_edges"], "conf": d["counts"]["conf"]}
    check(f"D: flask on graphifyy {ver}: {got['n_edges']} CALLS / {got['conf'].get('AMBIGUOUS', 0)} AMBIGUOUS, "
          f"{got['n_nodes']} nodes == frozen", {k: want[k] for k in got} == got, (got, want))
    check("D: ... and the exact frozen edge set (sha256 over the version-stable edge lines)",
          h == want.get("edges_sha256"), h)


# =====================================================================================================
# E — the scorers, exact on synthetic inputs
# =====================================================================================================
def _n(key, label, name, f, line):
    return {"key": key, "label": label, "name": name, "source_file": f, "start_line": line, "end_line": None}


NODES = [_n("a", "Module", "a", "a.py", 1), _n("a.f", "Function", "f", "a.py", 3), _n("a.g", "Function", "g", "a.py", 10),
         _n("a.C", "Class", "C", "a.py", 20), _n("a.C.m", "Method", "C.m", "a.py", 21),
         _n("b.D.m", "Method", "D.m", "b.py", 5), _n("b.h", "Function", "h", "b.py", 1),
         _n("t.test_x", "Function", "test_x", "tests/t.py", 1)]


def _row(f, stack, form, name, defs):
    return {"file": f, "line": 0, "col": 0, "form": form, "name": name, "defstack": stack, "defs": defs}


def _d(f, line, typ, name, alt=()):
    return {"file": f, "line": line, "alt_lines": list(alt), "type": typ, "name": name}


TRUTH = {"schema": "call-linking/truth/1", "rows": [
    _row("a.py", [["f", 3, []]], "bare", "g", [_d("a.py", 10, "function", "g")]),
    _row("a.py", [["f", 3, []]], "name_attr", "m", [_d("a.py", 21, "function", "m")]),
    _row("a.py", [["g", 10, []]], "recv", "m", [_d("b.py", 5, "function", "m")]),
    _row("a.py", [["g", 10, []]], "bare", "C", [_d("a.py", 20, "class", "C")]),
    _row("a.py", [], "bare", "f", [_d("a.py", 3, "function", "f")]),
    _row("a.py", [["f", 3, []]], "bare", "print", []),
    _row("b.py", [["h", 2, [1]]], "bare", "g", [_d("a.py", 10, "function", "g")]),
    _row("tests/t.py", [["test_x", 1, []]], "name_attr", "h", [_d("b.py", 2, "function", "h", [1])]),
    _row("a.py", [["f", 3, []]], "self", "zz", [_d("a.py", 99, "function", "zz")]),
]}


def _dump(edges):
    return {"schema": "call-linking/offline-calls/1", "nodes": copy.deepcopy(NODES), "edges": [list(e) for e in edges],
            "flags": {}}


DUMP = _dump([("a.f", "a.g", "INFERRED"), ("a.f", "b.D.m", "AMBIGUOUS"), ("a.f", "a.C.m", "AMBIGUOUS"),
              ("a.g", "b.D.m", "EXTRACTED"), ("a.g", "a.f", "INFERRED"), ("b.h", "a.g", "INFERRED"),
              ("a", "a.f", "INFERRED")])


def _eg(nodes, edges, **extra) -> dict:
    return dict({"nodes": [_n(*x) for x in nodes], "edges": [list(e) for e in edges]}, **extra)


def _cli(fn, argv) -> tuple:
    """(exit code or None, stdout, stderr, the exception's repr or None) of a kit main(): a crash is a RED result."""
    out, err = io.StringIO(), io.StringIO()
    rc, exc = None, None
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = fn(argv)
        except SystemExit as e:   # noqa: PERF203 — a SystemExit is an exit code, not a crash
            rc = e.code
        except Exception as e:  # noqa: BLE001
            exc = repr(e)
    return rc, out.getvalue(), err.getvalue(), exc


def gate_e_engdiff_common(EG, base: Path) -> None:
    """PREREG amendment A1 (before RC): engine parity E compares the edges whose BOTH endpoints exist on both
    engines (version-stable keys) — graphify 0.8.39 makes no node for TS `static create = () =>` members that
    0.8.51 does, so an edge touching such a node can exist on one engine only whatever the variant does; the raw
    symmetric difference over all edges is reported beside it, never decisive."""
    N39 = [("p", "Function", "f", "a.py", 1), ("q", "Function", "g", "a.py", 5)]
    N51 = [("P", "Function", "f", "a.py", 1), ("Q", "Function", "g", "a.py", 5), ("H", "Method", "K.h", "a.py", 9)]
    b39 = _eg(N39, [("p", "q", "INFERRED")])
    b51 = _eg(N51, [("P", "Q", "INFERRED"), ("H", "Q", "INFERRED")])                     # H: a node on 0.8.51 only
    t39 = _eg(N39, [("p", "q", "INFERRED")])
    t51 = _eg(N51, [("P", "Q", "INFERRED"), ("H", "Q", "INFERRED"), ("P", "H", "EXTRACTED"), ("Q", "H", "INFERRED")])
    x39 = _eg(N39, [("p", "q", "INFERRED"), ("q", "p", "EXTRACTED")])                     # g -> f on 0.8.39 only
    try:
        pr = EG.parity(b39, b51)
        cr = EG.criterion(t39, t51, b39, b51)
        cx = EG.criterion(x39, t51, b39, b51)
    except Exception as exc:  # noqa: BLE001 — the pre-amendment engdiff is a RED result, never a crash
        check("engdiff (amendment A1) runs on a corpus with a node on one engine only", False, repr(exc))
        return
    check("engdiff parity (A1): a node on one engine only is listed; an edge touching it counts in the RAW "
          "symmetric difference (1) and not in the one over edges whose both endpoints exist on both engines (0)",
          (pr.get("symdiff"), pr.get("symdiff_common"), pr.get("nodes_common"), pr.get("nodes_only_a"),
           pr.get("nodes_only_b")) == (1, 0, 2, [], ["Method:K.h@a.py:9"]),
          {k: pr.get(k) for k in ("symdiff", "symdiff_common", "nodes_common", "nodes_only_a", "nodes_only_b")})
    check("engdiff E (A1): typed edges to a node 0.8.39 lacks (raw symdiff 3 > V0's 1) HOLD on the common "
          "endpoints (0 <= 0); the raw comparison is reported (holds_raw False), the nodes on one engine are named, "
          "and no engine-specific change is left on common endpoints (2 raw)",
          cr.get("holds") is True and cr.get("holds_raw") is False
          and (cr.get("variant_symdiff_common"), cr.get("v0_symdiff_common")) == (0, 0)
          and (cr.get("variant_symdiff_raw"), cr.get("v0_symdiff_raw")) == (3, 1)
          and cr.get("nodes_only_0839") == [] and cr.get("nodes_only_0851") == ["Method:K.h@a.py:9"]
          and cr.get("nodes_common") == 2 and cr.get("engine_specific_changes") == []
          and cr.get("engine_specific_changes_raw") == 2 and not cr.get("void"), cr)
    check("engdiff E (A1): an edge between two nodes BOTH engines have, on one engine only, still FAILS E (1 > 0) "
          "and is listed as engine-specific",
          cx.get("holds") is False and (cx.get("variant_symdiff_common"), cx.get("v0_symdiff_common")) == (1, 0)
          and cx.get("engine_specific_changes") == [(("Function:g@a.py:5", "Function:f@a.py:1"), None, "EXTRACTED")],
          cx)
    d = base / "engdiff_a1"
    d.mkdir(exist_ok=True)
    files = {"b39": b39, "b51": b51, "t39": t39, "t51": t51, "x39": x39,
             "n51": _eg(N51 + [("Z", "Function", "z", "z.py", 1)], t51["edges"]),       # a node V0 does not have
             "v51": dict(copy.deepcopy(t51), void="engine_void: extraction resolved to ast")}
    for k, obj in files.items():
        (d / f"{k}.json").write_text(json.dumps(obj), encoding="utf-8")
    p = {k: str(d / f"{k}.json") for k in files}
    bases = [f"--base39={p['b39']}", f"--base51={p['b51']}"]
    rc_t, out_t, _e, ex_t = _cli(EG.main, [p["t39"], p["t51"], *bases, f"--json={d / 'out.json'}"])
    try:
        js = json.loads((d / "out.json").read_text(encoding="utf-8")).get("criterion") or {}
    except (OSError, ValueError):
        js = {}
    e_line = next((ln for ln in out_t.splitlines() if ln.startswith("E ")), "")
    check("engdiff CLI (A1): exit 0 when E holds on the common endpoints although the raw comparison is worse; "
          "the E line names both figures (0 <= 0 HOLDS; raw 3 vs 1) and the JSON records both",
          rc_t == 0 and ex_t is None and "both engines" in e_line and "HOLDS" in e_line
          and "symdiff 0 <= V0 symdiff 0" in e_line and "raw" in e_line and "3 vs 1" in e_line
          and js.get("holds") is True and js.get("holds_raw") is False and js.get("variant_symdiff_raw") == 3,
          (rc_t, ex_t, e_line, js))
    rc_x, _o, _e, ex_x = _cli(EG.main, [p["x39"], p["t51"], *bases])
    check("engdiff CLI (A1): exit 1 when E fails on the common endpoints", (rc_x, ex_x) == (1, None), (rc_x, ex_x))
    rc_n, out_n, _e, ex_n = _cli(EG.main, [p["t39"], p["n51"], *bases])
    rc_v, out_v, _e, ex_v = _cli(EG.main, [p["t39"], p["v51"], *bases])
    check("engdiff CLI: VOID (exit 3) when a variant's node set differs from its base's on one engine (every §8.3 "
          "variant is edge-only, so the common-endpoint domain would differ) or a dump carries `void`",
          (rc_n, rc_v, ex_n, ex_v) == (3, 3, None, None) and "VOID" in out_n and "VOID" in out_v,
          (rc_n, rc_v, ex_n, ex_v, out_n[-300:], out_v[-300:]))


# PD-1 (PRE_RC_DECISIONS.md, decided before RC; NOT an amendment of E): the two typed tuples S4's corpora run found
# on graphify 0.8.51 only — true (tsc), both endpoints on both engines, the final CALLS edge identical on both —
# whose evidence chain runs through `ZodTuple.create`, a node only 0.8.51 makes. Exactly these, on exactly that
# engine and grade; changing the list means changing this gate.
PD1_CORPUS = "colinhacks__zod"
PD1_TUPLES = (("Method:ZodFunction.args@packages/zod/src/v3/types.ts:3912",
               "Method:ZodTuple.rest@packages/zod/src/v3/types.ts:3456", "INFERRED"),
              ("Method:ZodFunction.create@packages/zod/src/v3/types.ts:3952",
               "Method:ZodTuple.rest@packages/zod/src/v3/types.ts:3456", "INFERRED"))
PD1_ENGINE = "0.8.51"


def gate_e_pd1(EG, base: Path) -> None:
    """engdiff.typed_tuple_verdict: §8.4 E bullet 1 (typed tuples identical across the engines on the common
    endpoints) as registered, plus the pre-RC decision PD-1 applied mechanically — exactly its listed tuples, on
    exactly the engine and grade listed, for its corpus; anything else still fails."""
    acc_fn, ver = getattr(EG, "accepted_typed_tuples", None), getattr(EG, "typed_tuple_verdict", None)
    if not callable(acc_fn) or not callable(ver):
        check("engdiff.accepted_typed_tuples / typed_tuple_verdict exist (PD-1 applied mechanically from "
              "PRE_RC_DECISIONS.md)", False)
        return
    try:
        acc = acc_fn()
        want = {(PD1_CORPUS, PD1_ENGINE, *t): "PD-1" for t in PD1_TUPLES}
        two = ver(PD1_CORPUS, [], [list(t) for t in PD1_TUPLES])
        extra_t = ("Method:ZodType.pipe@packages/zod/src/v3/types.ts:522",
                   "Method:ZodPipeline.create@packages/zod/src/v3/types.ts:4827", "EXTRACTED")
        extra = ver(PD1_CORPUS, [], list(PD1_TUPLES) + [extra_t])
        cases = {"the PD-1 tuple on 0.8.39 only": ver(PD1_CORPUS, [PD1_TUPLES[0]], []),
                 "another grade": ver(PD1_CORPUS, [], [PD1_TUPLES[0][:2] + ("AMBIGUOUS",)]),
                 "another corpus": ver("pallets__flask", [], [PD1_TUPLES[0]])}
        none = ver(PD1_CORPUS, [], [])
        bad_path = base / "pd_broken.md"
        bad_path.write_text("# no machine block here\n", encoding="utf-8")
        broken = acc_fn(bad_path)
    except Exception as exc:  # noqa: BLE001
        check("engdiff.typed_tuple_verdict runs", False, repr(exc))
        return
    check("engdiff.accepted_typed_tuples reads PRE_RC_DECISIONS.md: exactly PD-1's two zod tuples (ZodFunction.args / "
          "ZodFunction.create -> ZodTuple.rest, INFERRED, on 0.8.51 only), nothing else; an unreadable record "
          "accepts nothing", acc == want and broken == {}, {"got": acc, "broken": broken})
    check("engdiff.typed_tuple_verdict: PD-1's two tuples on 0.8.51 only FAIL E as registered (holds_as_registered "
          "False) and are accepted by PD-1 (holds True, both listed with the decision id); no difference holds both "
          "ways", two.get("holds_as_registered") is False and two.get("holds") is True
          and len(two.get("accepted") or []) == 2 and not two.get("unaccepted") and two.get("decisions") == ["PD-1"]
          and none.get("holds_as_registered") is True and none.get("holds") is True, (two, none))
    check("engdiff.typed_tuple_verdict: PD-1 is a list, not a rule — a third differing tuple, a listed tuple on the "
          "other engine, with another grade or on another corpus is unaccepted and fails E",
          extra.get("holds") is False and len(extra.get("unaccepted") or []) == 1
          and (extra.get("unaccepted") or [{}])[0].get("caller") == extra_t[0]
          and all(v.get("holds") is False and len(v.get("unaccepted") or []) == 1 for v in cases.values()),
          {"extra": extra, **cases})


def gate_e_pool_cli(CC, s: dict, base: Path) -> None:
    """compare_calls.py pool refuses — never crashes on, never silently flattens — inputs it cannot pool: a --json
    written for several dumps (a LIST), a file that is not a score, scores of different variants or engines (that
    would mix V0 and VT into one pooled recall); a VOID score is not data (exit 3)."""
    d = base / "pool_cli"
    d.mkdir(exist_ok=True)
    vt = dict(copy.deepcopy(s), flags={"DEEPMAP_TYPED_CALLS": "python,typescript,javascript", "DEEPMAP_CALLS_AUDIT": "1"})
    e51 = dict(copy.deepcopy(s), graphify_version="0.8.51")
    files = {"one": s, "multi": [s, vt], "notscore": _dump([]), "vt": vt, "e51": e51,
             "void": dict(copy.deepcopy(s), void="variant_void: inert")}
    for k, obj in files.items():
        (d / f"{k}.json").write_text(json.dumps(obj), encoding="utf-8")
    p = {k: str(d / f"{k}.json") for k in files}
    rc_ok, out_ok, _e, ex_ok = _cli(CC.main, ["pool", p["one"], p["one"], "--family=py"])
    try:
        got = json.loads(out_ok)
    except ValueError:
        got = {}
    check("compare_calls pool CLI: two scores of one variant and engine pool to 4/6 (exit 0)",
          rc_ok == 0 and ex_ok is None and (got.get("receiver_n"), got.get("receiver_hit")) == (6, 4),
          (rc_ok, ex_ok, out_ok[-300:]))
    rc_m, _o, err_m, ex_m = _cli(CC.main, ["pool", p["multi"], "--family=py"])
    check("compare_calls pool CLI: a --json written for several dumps (a list) is refused with a message (exit 2), "
          "never an AttributeError and never flattened", (rc_m, ex_m) == (2, None) and "several dumps" in err_m,
          (rc_m, ex_m, err_m[-300:]))
    rc_n, _o, err_n, ex_n = _cli(CC.main, ["pool", p["one"], p["notscore"]])
    check("compare_calls pool CLI: a file that is not a compare_calls score is refused (exit 2)",
          (rc_n, ex_n) == (2, None) and "not a compare_calls score" in err_n, (rc_n, ex_n, err_n[-300:]))
    rc_v, _o, err_v, ex_v = _cli(CC.main, ["pool", p["one"], p["vt"], "--family=py"])
    rc_e, _o, err_e, ex_e = _cli(CC.main, ["pool", p["one"], p["e51"], "--family=py"])
    check("compare_calls pool CLI: scores of different variants (flags) or engines are refused (exit 2): one pool "
          "is one variant on one engine", (rc_v, rc_e, ex_v, ex_e) == (2, 2, None, None)
          and "one variant" in err_v and "one variant" in err_e, (rc_v, rc_e, ex_v, ex_e, err_v[-200:], err_e[-200:]))
    rc_x, _o, err_x, ex_x = _cli(CC.main, ["pool", p["one"], p["void"]])
    check("compare_calls pool CLI: a score of a VOID dump is not data (exit 3)", (rc_x, ex_x) == (3, None)
          and "VOID" in err_x, (rc_x, ex_x, err_x[-300:]))


def _ms_scored(ranks) -> dict:
    """A measure_self score (score()'s shape: the ALL/ALL metrics and the per-query integer ranks) of the ranks."""
    return {"metrics": {"ALL/ALL": _ms_metrics(ranks)},
            "per_query": [{"id": f"q{i:04d}", "rank": r} for i, r in enumerate(ranks)]}


def _ms_metrics(ranks) -> dict:
    """measure_self.metrics written out here (the 4-decimal display the closed decisions D3 / D1-py read)."""
    n = len(ranks)
    mrr = sum(1.0 / r for r in ranks if r is not None) / n
    return {"n": n, "hit@1": round(sum(1 for r in ranks if r is not None and r <= 1) / n, 4),
            "hit@5": round(sum(1 for r in ranks if r is not None and r <= 5) / n, 4),
            "hit@15": round(sum(1 for r in ranks if r is not None and r <= 15) / n, 4), "MRR": round(mrr, 4)}


def gate_e_measure_self_exact(MS, base: Path) -> None:
    """AC29 for R2 (PREREG_CS §8.7 R, AC23, §8.10): measure_self.compare's exact fields beside the unchanged ones."""
    # The review's counter-example (65 queries): q0 13 -> 14 and q1 15 -> 14, both inside the top 15, the rest equal.
    # Exact MRRs (derived by hand): base (1/13 + 1/15 + 18 + 1/2) / 65 = 7271/25350, variant (2/14 + 18 + 1/2) / 65
    # = 261/910 — the variant is LOWER, yet both display 0.2868, so the 4-decimal mrr_ok holds.
    rest = [1] * 18 + [2] + [None] * 44
    near_b, near_v = _ms_scored([13, 15] + rest), _ms_scored([14, 14] + rest)

    def fields(c):
        return (c.get("mrr_base"), c.get("mrr_variant"), c.get("mrr_ok"), c.get("r2_holds"), c.get("mrr_ok_exact"),
                c.get("r2_holds_exact"), c.get("mrr_base_fraction"), c.get("mrr_variant_fraction"),
                c.get("min_gain_fraction"))
    try:
        got = fields(MS.compare(near_b, near_v))
    except Exception as exc:  # noqa: BLE001 — RED, never a crash of the section
        got = f"{type(exc).__name__}: {exc}"
    want = (0.2868, 0.2868, True, True, False, False, [7271, 25350], [261, 910], [0, 1])
    check("AC29 (measure_self.compare, R2): beside the unchanged 4-decimal mrr_ok / r2_holds (closed decisions D3 and "
          "D1-py read them) the exact fields come from the integer ranks — base 7271/25350 > variant 261/910 although "
          "both display 0.2868: mrr_ok True (kept) but mrr_ok_exact False, r2_holds_exact False",
          got == want, {"got": got, "want": want})

    def agree():
        out = []
        sb = _ms_scored([1, 4] + [None] * 3)
        for b, v, g in ((sb, sb, 0.0), (sb, _ms_scored([2, 4] + [None] * 3), 0.0),
                        (sb, _ms_scored([1, 3] + [None] * 3), 0.0),
                        # a higher MRR (1/5 > (2/15 + 1/2)/5) that loses 2 queries from the top 15: R2 fails both ways
                        (_ms_scored([15, 15, 2] + [None] * 2), _ms_scored([None, None, 1] + [None] * 2), 0.0),
                        # 1000 queries, the variant 17 more rank-1 hits: its MRR is the base's + 17/1000 EXACTLY, so
                        # a min gain of 0.017 read as the decimal 17/1000 holds (as the binary float it would not)
                        (_ms_scored([None] * 1000), _ms_scored([1] * 17 + [None] * 983), 0.017),
                        (_ms_scored([None] * 1000), _ms_scored([1] * 16 + [None] * 984), 0.017)):
            c = MS.compare(b, v, min_gain=g)
            out.append((c["mrr_ok"], c.get("mrr_ok_exact"), c["r2_holds"], c.get("r2_holds_exact"),
                        c.get("min_gain_fraction"), c["min_gain"]))
        return out
    try:
        got2 = agree()
    except Exception as exc:  # noqa: BLE001
        got2 = f"{type(exc).__name__}: {exc}"
    want2 = [(True, True, True, True, [0, 1], 0.0), (False, False, False, False, [0, 1], 0.0),
             (True, True, True, True, [0, 1], 0.0), (True, True, False, False, [0, 1], 0.0),
             (True, True, True, True, [17, 1000], 0.017),
             (False, False, False, False, [17, 1000], 0.017)]
    check("AC29 (measure_self.compare): where the rounded and the exact readings agree they agree — equal ranks hold, "
          "a lower MRR fails, a higher holds, a higher one losing 2 queries from the top 15 fails R2 (r2_holds_exact "
          "False with mrr_ok_exact True); --min-gain is read exactly from its decimal string (0.017 -> 17/1000: a "
          "gain of exactly 17/1000 holds, 16/1000 fails); min_gain itself is returned unchanged",
          got2 == want2, {"got": got2, "want": want2})
    d = base / "ms_exact"
    d.mkdir(parents=True, exist_ok=True)
    pb, pv = d / "scored_V0.json", d / "scored_VX.json"
    pb.write_text(json.dumps(near_b), encoding="utf-8")
    pv.write_text(json.dumps(near_v), encoding="utf-8")
    rc, out, err, exc = _cli(MS.main, ["compare", str(pb), str(pv)])
    rc_x, out_x, _e, exc_x = _cli(MS.main, ["compare", str(pb), str(pv), "--exact"])
    lines = out.splitlines()
    check("AC29 (measure_self compare CLI): the old line is printed unchanged (R2 HOLDS on the 4-decimal MRRs, exit 0 "
          "as before) and a second line prints the exact reading D3-cs uses (R2 exact FAILS, both fractions at full "
          "precision); --exact makes the exit code follow r2_holds_exact (exit 1)",
          exc is None and exc_x is None and rc == 0 and rc_x == 1 and len(lines) == 2
          and lines[0] == "R2 HOLDS: MRR 0.2868 -> 0.2868 (min gain 0.0); hit@15 lost [] gained []"
          and lines[1].startswith("R2 exact (AC29") and "FAILS" in lines[1] and "7271/25350" in lines[1]
          and "261/910" in lines[1] and repr(7271 / 25350) in lines[1] and repr(261 / 910) in lines[1]
          and out_x == out,
          {"rc": rc, "rc_exact": rc_x, "out": out, "err": err[-300:], "exc": exc or exc_x})
    # --min-gain as typed on the command line: the string branch of exact_decimal (a float read would make the gain
    # 1224979098644775/72057594037927936, a hair above 17/1000, and an exact +17/1000 gain would FAIL)
    pn, p17, p16 = d / "scored_none.json", d / "scored_17.json", d / "scored_16.json"
    pn.write_text(json.dumps(_ms_scored([None] * 1000)), encoding="utf-8")
    p17.write_text(json.dumps(_ms_scored([1] * 17 + [None] * 983)), encoding="utf-8")
    p16.write_text(json.dumps(_ms_scored([1] * 16 + [None] * 984)), encoding="utf-8")
    rc17, out17, err17, exc17 = _cli(MS.main, ["compare", str(pn), str(p17), "--min-gain=0.017", "--exact"])
    rc16, out16, _e16, exc16 = _cli(MS.main, ["compare", str(pn), str(p16), "--min-gain=0.017", "--exact"])
    try:
        direct = (MS.exact_decimal("0.017"), MS.exact_decimal(" 0.017 "), MS.exact_decimal(0.017))
    except Exception as exc:  # noqa: BLE001
        direct = f"{type(exc).__name__}: {exc}"
    l17 = out17.splitlines()
    check("AC29 (measure_self compare CLI, --min-gain=0.017 --exact): the typed gain is read as the decimal 17/1000 "
          "(never through a binary float), so an exact +17/1000 gain HOLDS (exit 0) and +16/1000 FAILS (exit 1); "
          "exact_decimal reads '0.017', ' 0.017 ' and 0.017 all as 17/1000",
          exc17 is None and exc16 is None and rc17 == 0 and rc16 == 1 and len(l17) == 2
          and "HOLDS" in l17[1] and "min gain 17/1000" in l17[1] and "min gain 17/1000" in out16
          and direct == (Fraction(17, 1000),) * 3,
          {"rc17": rc17, "rc16": rc16, "out17": out17, "out16": out16, "err": err17[-300:], "direct": direct,
           "exc": exc17 or exc16})


def gate_e(mods: dict, base: Path) -> None:
    print("\n-- E: compare_calls / delta / drift / audit / engdiff / impact / score_m / measure_self / run_variant / "
          "variants are exact on synthetic inputs --")
    need = ("_common", "compare_calls", "delta", "indegree_drift", "audit_sample", "engdiff", "impact_offline",
            "impact_spot", "score_m", "measure_self", "run_variant", "variants")
    if any(m not in mods for m in need):
        check("E: every scorer module is importable", False, sorted(set(need) - set(mods)))
        return
    C, CC, D, ID, AS = (mods[m] for m in ("_common", "compare_calls", "delta", "indegree_drift", "audit_sample"))
    # ---- compare_calls -------------------------------------------------------------------------------
    s = CC.score(TRUTH, DUMP)
    want = {"T": 7, "G": 7, "confirmed": 5, "refuted": 1, "unverified": 1, "recall": 0.7143,
            "precision_confirmed": 0.7143, "refuted_share": 0.1429, "ambiguous_share": 0.2857,
            "extracted_refuted_share": 0.0}
    check("compare_calls: |T| 7, |G| 7, confirmed 5 / refuted 1 / unverified 1, recall .7143, refuted .1429, "
          "AMBIGUOUS .2857, EXTRACTED refuted 0", {k: s.get(k) for k in want} == want, {k: s.get(k) for k in want})
    check("compare_calls: per form — bare 3/3, name_attr 1/2, recv 1/1, ctor 0/1 (a decorated caller and a "
          "decorated callee map through their decorator line)",
          s["by_form"] == {"bare": {"n": 3, "hit": 3}, "name_attr": {"n": 2, "hit": 1}, "recv": {"n": 1, "hit": 1},
                           "ctor": {"n": 1, "hit": 0}}, s["by_form"])
    check("compare_calls: truth stats — 9 sites, 1 unresolved, 1 callee unmapped, 0 callers unmapped",
          s["truth_stats"].get("sites") == 9 and s["truth_stats"].get("unresolved_or_external") == 1
          and s["truth_stats"].get("callee_unmapped") == 1 and not s["truth_stats"].get("caller_unmapped"),
          s["truth_stats"])
    check("compare_calls: per grade — AMBIGUOUS 2 (1 confirmed, 1 refuted), EXTRACTED 1 confirmed, INFERRED 4 "
          "(3 confirmed, 1 unverified)",
          s["by_grade"] == {"AMBIGUOUS": {"n": 2, "confirmed": 1, "refuted": 1, "unverified": 0},
                            "EXTRACTED": {"n": 1, "confirmed": 1, "refuted": 0, "unverified": 0},
                            "INFERRED": {"n": 4, "confirmed": 3, "refuted": 0, "unverified": 1}}, s["by_grade"])
    st = CC.score(TRUTH, DUMP, ["tests/"])
    check("compare_calls --src-prefix restricts BOTH sides to the prefix's callers (tests/: |T| 1, |G| 0)",
          (st["T"], st["G"], st["recall"]) == (1, 0, 0.0), (st["T"], st["G"], st["recall"]))
    p = CC.pool([s, s], "py")
    check("compare_calls pool: receiver forms of py = name_attr + recv, pooled 4/6 over two copies",
          (p["receiver_n"], p["receiver_hit"], p["receiver_recall"]) == (6, 4, 0.6667), p)
    gate_e_pool_cli(CC, s, base)
    # ---- delta ---------------------------------------------------------------------------------------
    b = _dump([("a.f", "a.g", "INFERRED"), ("a.f", "b.D.m", "AMBIGUOUS"), ("a.f", "a.C.m", "AMBIGUOUS"),
               ("a.g", "a.f", "INFERRED")])
    v = _dump([("a.f", "a.g", "INFERRED"), ("a.f", "a.C.m", "EXTRACTED"), ("a.g", "a.f", "AMBIGUOUS"),
               ("a.g", "b.D.m", "INFERRED"), ("t.test_x", "b.h", "INFERRED")])
    dd = D.delta(b, v, TRUTH)
    pairs = {k: [(i["caller"], i["callee"]) for i in dd["items"][k]] for k in dd["items"]}
    check("delta: added / removed / demoted / promoted / EXTRACTED buckets exact",
          pairs == {"added": [("a.g", "b.D.m"), ("t.test_x", "b.h")], "removed": [("a.f", "b.D.m")],
                    "demoted": [("a.g", "a.f")], "promoted": [("a.f", "a.C.m")], "EXTRACTED": [("a.f", "a.C.m")]},
          pairs)
    check("delta: caller class (tests/ -> test) and resolver verdict per item",
          [(i["caller_class"], i["verdict"]) for i in dd["items"]["added"]] == [("prod", "confirmed"), ("test", "confirmed")]
          and dd["items"]["removed"][0]["verdict"] == "refuted" and dd["node_set_identical"], dd["counts"])
    v2 = copy.deepcopy(v)
    v2["nodes"].append(_n("z", "Function", "z", "z.py", 1))
    check("delta: a differing node set is reported (not an edge-only pair)",
          D.delta(b, v2)["node_set_identical"] is False and D.delta(b, v2)["n_node_mismatch"] == 1)
    paths = {"a.py": "prod", "tests/t.py": "test", "docs/x.py": "nonprod", "src/examples/y.py": "nonprod",
             "examples.py": "prod", "src/docs_parser.py": "prod", "mydocs/z.py": "prod", "docs_src/q.py": "nonprod",
             "Demo/k.py": "nonprod"}
    got = {pth: C.caller_class(pth) for pth in paths}
    check("caller class: §2.6 component match — examples.py, src/docs_parser.py and mydocs/ are production",
          got == paths, got)
    try:
        if str(BUILD) not in sys.path:
            sys.path.insert(0, str(BUILD))
        import typed_calls as TC
        prod_rule = getattr(TC, "is_nonprod_path", None)
        prod_set = getattr(TC, "NONPROD_DIRS", None)
    except Exception:  # noqa: BLE001
        prod_rule = prod_set = None
    if prod_rule is None:
        skip("caller class == build/typed_calls.py's rule", "typed_calls.is_nonprod_path is not in this tree yet")
    else:
        agree = {pth: (bool(prod_rule(pth)), C.local_is_nonprod_path(pth)) for pth in paths}
        check("the kit's non-production rule agrees with build/typed_calls.py (the kit uses the product's when present)",
              all(x == y for x, y in agree.values()) and (prod_set is None or set(prod_set) == set(C.NONPROD_PARTS)),
              agree)
    # ---- indegree drift + void rule --------------------------------------------------------------------
    nb = [_n("T", "Function", "T", "t.py", 1), _n("U", "Function", "U", "u.py", 1)] + \
         [_n(f"c{i}", "Function", f"c{i}", "c.py", i + 1) for i in range(4)]
    base_d = {"nodes": nb, "edges": [["c0", "U", "INFERRED"], ["c1", "U", "INFERRED"]]}
    var_d = {"nodes": nb, "edges": [["c0", "U", "INFERRED"], ["c0", "T", "INFERRED"], ["c1", "T", "INFERRED"],
                                    ["c2", "T", "INFERRED"]]}
    dr = ID.drift(base_d, var_d)
    cands = [(c["key"], c["ratio"]) for c in dr["candidates"]]
    check("indegree_drift: T 0 -> 3 (x2.3863, a candidate), U 2 -> 1 (x0.8068); max ratio exact",
          cands == [("T", 2.3863)] and dr["n_changed"] == 2 and dr["ratio_max"] == 2.3863 and dr["ratio_min"] == 0.8068,
          (cands, dr["ratio_min"], dr["ratio_max"]))

    def cap(tops):
        return {"questions": [{"id": q, "search": [{"name": n, "file": f, "line": ln} for n, f, ln in t]}
                              for q, t in tops.items()]}
    tn = ("T", "t.py", 1)
    other = ("X", "x.py", 1)
    cb = cap({"q1": [other], "q2": [other], "q3": [other], "q4": [tn]})
    cv3 = cap({"q1": [tn], "q2": [other, tn], "q3": [tn], "q4": [tn]})
    cv2 = cap({"q1": [tn], "q2": [tn], "q3": [other], "q4": [tn]})
    r3, r2 = ID.void_rule(dr["candidates"], cb, cv3), ID.void_rule(dr["candidates"], cb, cv2)
    check("drift void rule: a 2x candidate newly in the fused top-3 of 3 questions VOIDS; of 2 does not "
          "(a question where it already was top-3 does not count)",
          r3["void"] and r3["newly_top3"]["T"] == ["q1", "q2", "q3"] and not r2["void"], (r3, r2))
    # ---- audit_sample ----------------------------------------------------------------------------------
    items = {"added": [{"caller": f"p{i}", "callee": f"q{i}", "verdict": "confirmed"} for i in range(10)],
             "removed": [{"caller": f"r{i}", "callee": f"s{i}", "verdict": "confirmed" if i < 3 else "refuted"}
                         for i in range(10)],
             "demoted": [], "EXTRACTED": [{"caller": f"e{i}", "callee": f"f{i}"} for i in range(3)]}
    d1 = AS.draw(items, 20261022, "flask", 4)
    d1b = AS.draw(copy.deepcopy(items), 20261022, "flask", 4)
    d2 = AS.draw(items, 7, "flask", 4)
    check("audit draw: seeded — the same seed and label give the same sample", d1 == d1b)
    check("audit draw: another seed gives another sample", d1["added"] != d2["added"] or d1["removed"] != d2["removed"])
    check("audit draw: n per bucket (4 of 10; all 3 of a 3-edge bucket; an empty bucket stays empty)",
          (len(d1["added"]), len(d1["EXTRACTED"]), len(d1["demoted"])) == (4, 3, 0))
    check("audit draw: EVERY resolver-confirmed removal is audited (exhaustive), and the sampled removals come "
          "from the rest", [i["caller"] for i in d1["removed_confirmed"]] == ["r0", "r1", "r2"]
          and all(i["verdict"] != "confirmed" for i in d1["removed"]) and len(d1["removed"]) == 4)
    corpus = base / "audit_corpus"
    (corpus / "tests").mkdir(parents=True)
    (corpus / "a.py").write_text("\n\ndef f():\n    g()\n    return C().m()\n\n\n\n\ndef g():\n    return D().m()\n"
                                 + "\n" * 8 + "class C:\n    def m(self):\n        return 1\n", encoding="utf-8")
    (corpus / "b.py").write_text("def h():\n    pass\n\n\nclass D:\n    def m(self):\n        return 2\n", encoding="utf-8")
    picked = {"added": [{"caller": "a.g", "callee": "b.D.m", "base": None, "variant": "INFERRED", "verdict": "confirmed",
                         "caller_class": "prod"}],
              "removed": [{"caller": "a.f", "callee": "b.D.m", "base": "AMBIGUOUS", "variant": None,
                           "verdict": "refuted", "caller_class": "prod"}],
              "demoted": [], "EXTRACTED": [], "removed_confirmed": []}
    rows, key = AS.build_rows(corpus, _dump([]), _dump([]), picked, 20261022, "synthetic")
    sheet = AS.render_sheet("synthetic", rows)
    leaked = [w for w in AS.BLIND_FORBIDDEN if w.lower() in sheet.lower()]
    check("audit sheet is blind: it names no bucket, grade, verdict, flag or variant", not leaked, leaked)
    check("audit sheet shows the caller's call sites with line numbers (g -> D().m() at a.py:11)",
          any("11" in str(ln) and "m()" in txt for r in rows for ln, txt in r["sites"]), rows)
    check("audit key keeps what the sheet hides (buckets, grades, verdict) per row id",
          {tuple(k["buckets"]) for k in key.values()} == {("added",), ("removed",)}
          and all(len(rid) == 12 for rid in key))
    keyd = {"label": "x", "rows": {
        "r1": {"buckets": ["added"], "verdict": "confirmed"}, "r2": {"buckets": ["added"], "verdict": "refuted"},
        "r3": {"buckets": ["added"], "verdict": None}, "r4": {"buckets": ["added", "EXTRACTED"], "verdict": "confirmed"},
        "r5": {"buckets": ["removed"], "verdict": "refuted"}, "r6": {"buckets": ["removed"], "verdict": "refuted"},
        "r7": {"buckets": ["removed_confirmed"], "verdict": "confirmed"}}}
    s1 = {"r1": "TRUE", "r2": "TRUE", "r3": "TRUE", "r4": "TRUE", "r5": "FALSE", "r6": "FALSE", "r7": "FALSE"}
    s2 = {"r1": "TRUE", "r2": "TRUE", "r3": "UNSURE", "r4": "TRUE", "r5": "FALSE", "r6": "FALSE", "r7": "FALSE"}
    sc = AS.score(keyd, [s1, s2])
    bk = sc["buckets"]
    check("audit score: added precision 3/4 (an UNSURE is not TRUE), Wilson 95% .3006..9544",
          (bk["added"]["true"], bk["added"]["n"], bk["added"]["share"], bk["added"]["wilson_lb"],
           bk["added"]["wilson_ub"]) == (3, 4, 0.75, 0.3006, 0.9544), bk["added"])
    check("audit score: removed = correct-removal share 2/2 (Wilson LB .3424); a confirmed removal audited FALSE "
          "is no loss", (bk["removed"]["false"], bk["removed"]["wilson_lb"], bk["removed_confirmed"]["true"])
          == (2, 0.3424, 0), (bk["removed"], bk["removed_confirmed"]))
    check("audit score: the resolver was wrong on 2 of 6 judged rows (r2 refuted but TRUE; r7 confirmed but FALSE) "
          "-> error rate .3333 voids the G deltas",
          sc["resolver"] == {"judged": 6, "wrong": 2, "error_rate": 0.3333, "voids_g_deltas": True}, sc["resolver"])
    # ---- engdiff -------------------------------------------------------------------------------------------
    EG = mods["engdiff"]
    a39 = {"nodes": [_n("k1", "Function", "f", "a.py", 1), _n("k2", "Function", "g", "a.py", 5)],
           "edges": [["k1", "k2", "INFERRED"]]}
    a51 = {"nodes": [_n("Q1", "Function", "f", "a.py", 1), _n("Q2", "Function", "g", "a.py", 5)],
           "edges": [["Q1", "Q2", "INFERRED"], ["Q2", "Q1", "AMBIGUOUS"]]}
    pr = EG.parity(a39, a51)
    check("engdiff: engine keys differ, version-stable identities match: symmetric difference 1", pr["symdiff"] == 1
          and pr["only_b"] == [("Function:g@a.py:5", "Function:f@a.py:1")], pr)
    b51 = copy.deepcopy(a51)
    b51["edges"] = [["Q1", "Q2", "INFERRED"]]                  # V0 agrees across the engines (symdiff 0)
    v39 = copy.deepcopy(a39)
    v39["edges"].append(["k2", "k1", "INFERRED"])              # the variant adds g -> f on 0.8.39 only
    cr = EG.criterion(v39, copy.deepcopy(b51), a39, b51)
    check("engdiff E criterion: a variant whose engine parity is worse than V0's FAILS (1 > 0); its one-engine-only "
          "change is listed", cr.get("holds") is False
          and (cr.get("variant_symdiff_common"), cr.get("v0_symdiff_common")) == (1, 0)
          and len(cr.get("engine_specific_changes") or ()) == 1, cr)
    same = EG.criterion(a39, b51, a39, b51)
    check("engdiff E criterion: V0 against itself holds, with no engine-specific change", same.get("holds")
          and not same.get("engine_specific_changes"), same)
    gate_e_engdiff_common(EG, base)
    gate_e_pd1(EG, base)
    # ---- impact_offline / impact_spot ---------------------------------------------------------------------------
    IO, IS = mods["impact_offline"], mods["impact_spot"]
    chain = {"nodes": [_n(x, "Function", x, "c.py", i) for i, x in enumerate("ABCD", 1)] +
                      [_n("E", "Method", "K.D", "k.py", 1)],
             "edges": [["A", "B", "INFERRED"], ["B", "C", "INFERRED"], ["C", "D", "AMBIGUOUS"], ["Z", "A", "INFERRED"]]}
    seeds, rows3 = IO.reach(chain, "D", 3)
    seeds2, rows2 = IO.reach(chain, "D", 2)
    check("impact_offline.reach: seeds by name or '.name' suffix; 3 hops reach C, B, A; 2 hops stop at B",
          seeds == ["D", "E"] and [(a, h) for a, h, _c in rows3] == [("C", 1), ("B", 2), ("A", 3)]
          and [a for a, _h, _c in rows2] == ["C", "B"], (seeds, rows3, rows2))
    spot_ref = {"nodes": NODES, "edges": [["a.f", "a.C.m", "TRUTH"], ["a.g", "a.C.m", "TRUTH"]]}
    spot_got = {"nodes": NODES, "edges": [["a.f", "a.C.m", "AMBIGUOUS"], ["b.h", "a.C.m", "INFERRED"],
                                          ["a.f", "b.D.m", "AMBIGUOUS"]]}
    sp = IS.spot(spot_ref, spot_got, "m@a.py")
    check("impact_spot: 'Name@file' targets only that file's node; hop-1 P 1/2, R 1/2",
          (sp["targets"], sp["ref"], sp["got"], sp["hit"], sp["precision"], sp["recall"]) == (1, 2, 2, 1, 0.5, 0.5), sp)
    check("impact_spot registers 5 Python and 5 TS/JS spot symbols",
          len(IS.PY_SPOTS) == 5 and len(IS.TS_SPOTS) == 5
          and sorted(s.split("@")[0] for k, v in IS.SPOTS.items() for s in v) == sorted(IS.PY_SPOTS + IS.TS_SPOTS))
    check("impact_spot: the saturated design-given spot `_mk_project` is replaced (PREREG amendment A2) by "
          "`read_records@retrieval_log.py` on the pinned STAGE-0 corpus; the moving `self` corpus holds no spot",
          "_mk_project" not in IS.PY_SPOTS and "read_records" in IS.PY_SPOTS
          and IS.SPOTS.get("stage0_build") == ["read_records@retrieval_log.py"] and "self" not in IS.SPOTS
          and not any("_mk_project" in s for v in IS.SPOTS.values() for s in v),
          {k: v for k, v in IS.SPOTS.items() if k in ("self", "stage0_build")})
    # ---- score_m R1 / R3 ---------------------------------------------------------------------------------------
    SM = mods["score_m"]

    def scq(qid, fam, atoms):
        return {"id": qid, "family": fam, "union_strict": sum(a["union_strict"] for a in atoms) / len(atoms),
                "impact_strict_hits": sum(a["impact_strict"] for a in atoms), "atoms": atoms}

    def at(name, us, sp_, imp):
        return {"atom": name, "union_strict": us, "union_spec": sp_, "impact_strict": imp}
    base_s = [scq("q1", "multi_hop", [at("x:1 - a", True, True, True), at("x:2 - b", False, True, False)]),
              scq("q2", "transitive", [at("y:1 - c", True, True, False)])]
    good = copy.deepcopy(base_s)
    good[0]["atoms"][1]["union_strict"] = True
    good[0]["union_strict"] = 1.0
    bad = copy.deepcopy(base_s)
    bad[1]["atoms"][0]["union_spec"] = False
    r_good, r_bad = SM.r1_compare(base_s, good), SM.r1_compare(base_s, bad)
    check("score_m R1: a gained atom holds; a lost SPEC atom fails (threshold 0) and is named",
          r_good["r1_holds"] and r_good["r3_holds"] and not r_bad["r1_holds"]
          and r_bad["atom_losses"] == [{"question": "q2", "atom": "y:1 - c", "measure": "union_spec"}], (r_good, r_bad))
    lost_imp = copy.deepcopy(base_s)
    lost_imp[0]["impact_strict_hits"] = 0
    check("score_m R3: fewer multi_hop impact STRICT atoms than V0 fails", not SM.r1_compare(base_s, lost_imp)["r3_holds"])
    # ---- measure_self R2 -----------------------------------------------------------------------------------------
    MS = mods["measure_self"]
    gt = {"queries": [{"id": "q1", "set": "A", "type": "concept", "expected": [["build/x.py", "f"]]},
                      {"id": "q2", "set": "B", "type": "code", "expected": [["hooks/y.py", "g"]]}]}

    def raw(r1, r2):
        def rows(r, f, nm):
            return [{"file": "z.py", "name": "nope"}] * (r - 1) + [{"file": f, "name": nm}] if r else []
        return {"queries": [{"id": "q1", "rows": rows(r1, "build/x.py", "Mod.f")},
                            {"id": "q2", "rows": rows(r2, "hooks/y.py", "g")}]}
    sb = MS.score(gt, raw(1, 4))
    check("measure_self: MRR (1 + 1/4) / 2 = .625, hit@1 .5, hit@5 1.0; a dotted name ending in .f is a hit",
          sb["metrics"]["ALL/ALL"] == {"n": 2, "hit@1": 0.5, "hit@5": 1.0, "hit@15": 1.0, "MRR": 0.625},
          sb["metrics"]["ALL/ALL"])
    check("measure_self R2: equal MRR holds; a lower MRR fails; D1-py's +0.017 gain is enforced when asked",
          MS.compare(sb, sb)["r2_holds"] and not MS.compare(sb, MS.score(gt, raw(2, 4)))["r2_holds"]
          and not MS.compare(sb, sb, min_gain=0.017)["r2_holds"])
    two_lost = MS.score(gt, raw(0, 0))
    check("measure_self R2: losing hit@15 on 2 queries fails (at most 1 allowed)",
          MS.compare(sb, two_lost)["hit15_net_drop"] == 2 and not MS.compare(sb, two_lost)["hit15_ok"])
    gate_e_measure_self_exact(MS, base)
    # ---- run_variant ---------------------------------------------------------------------------------------------
    RV = mods["run_variant"]
    q = {"id": "q", "search": [{"name": "f", "file": "a.py", "line": 1, "score": 0.5}],
         "impact": {"f": {"rows": [{"caller": "g", "hop": 1}]}}}
    q2 = copy.deepcopy(q)
    q2["search"][0]["score"] = 0.9
    q3 = copy.deepcopy(q)
    q3["impact"]["f"]["rows"][0]["hop"] = 2
    check("run_variant round trip: identical rows (a score change ignored) are EXACT; an impact row change is VOID",
          RV.roundtrip({"questions": [q]}, {"questions": [q2]})["exact"]
          and not RV.roundtrip({"questions": [q]}, {"questions": [q3]})["exact"])
    ok_log = "[deepmap sync] 120 nodes, 300 CALLS edges, 0 embedded this run (120 embedded total) from x"
    check("run_variant matched state: 0 re-embedded and no FTS re-create is matched; either one VOIDS",
          not RV.check_sync_log(ok_log)["void"]
          and RV.check_sync_log(ok_log.replace(" 0 embedded", " 3 embedded"))["void"]
          and RV.check_sync_log(ok_log + "\n[deepmap sync] full-text index re-created (x) in 1s")["void"]
          and RV.check_sync_log("no sync line")["void"])
    cen = {"nodes": 5, "embedded": 5, "summary_fp": "s", "node_keys_fp": "k"}
    check("run_variant matched state: a different node-text fingerprint is VOID",
          RV.matched(cen, dict(cen))["matched"] and not RV.matched(cen, dict(cen, summary_fp="t"))["matched"])
    home_ok = base / "h"
    route = SimpleNamespace(source="registry", bolt=7701, problem=None)
    envok = {"DEEPMAP_HOME": str(home_ok)}
    probs = {"ok": RV.safety_problems(base, route, environ=envok),
             "uri": RV.safety_problems(base, route, environ=dict(envok, NEO4J_URI="bolt://x")),
             "base": RV.safety_problems(base, SimpleNamespace(source="registry", bolt=7687, problem=None), environ=envok),
             "legacy": RV.safety_problems(base, SimpleNamespace(source="legacy-unverified", bolt=7687, problem="p"),
                                          environ=envok),
             "nohome": RV.safety_problems(base, route, environ={}),
             "operator": RV.safety_problems(base, route, environ={"DEEPMAP_HOME": str(Path.home() / ".deepmap")})}
    check("run_variant safety: a throwaway home + own instance passes; NEO4J_URI, bolt 7687, a legacy route, no "
          "home and the operator's ~/.deepmap are each refused",
          probs["ok"] == [] and all(probs[k] for k in ("uri", "base", "legacy", "nohome", "operator")), probs)

    class _Ret:
        def search(self, q, top_k):
            return [{"name": "f", "file": "a.py", "line": 1, "embedding": [1]}, {"name": "f", "file": "a.py", "line": 1},
                    {"name": "g"}, {"name": "h"}]

        def impact(self, name, limit):
            return [{"caller": name + "_caller", "limit": limit}]
    pas = RV.run_pass(_Ret(), [{"id": "t", "family": "transitive", "prompt": "p"},
                               {"id": "c", "family": "concept_to_impl", "prompt": "p"}])
    check("run_variant.run_pass: impact(limit=30) on the distinct names of the top-3 for graph families only; "
          "embeddings dropped", sorted(pas[0]["impact"]) == ["f", "g"] and pas[1]["impact"] == {}
          and "embedding" not in pas[0]["search"][0] and pas[0]["impact"]["f"]["rows"][0]["limit"] == 30, pas)
    # ---- variants ------------------------------------------------------------------------------------------------
    V = mods["variants"]
    env = V.child_env("VT", base={"PATH": "p", "NEO4J_URI": "bolt://x", "DEEPMAP_HOME": "h", "DEEPMAP_PYTHON": "py",
                                  "DEEPMAP_GRAPHIFY_TIMEOUT": "3600", "DEEPMAP_PRECISE_CALLS": "1",
                                  "DEEPMAP_EXCLUDE_TESTS": "1"})
    check("variants.child_env: every stray DEEPMAP_* and NEO4J_URI dropped, the run settings kept, the variant's "
          "flags set, PYTHONHASHSEED=0",
          env == {"PATH": "p", "DEEPMAP_HOME": "h", "DEEPMAP_PYTHON": "py", "DEEPMAP_GRAPHIFY_TIMEOUT": "3600",
                  "DEEPMAP_TYPED_CALLS": "python,typescript,javascript", "DEEPMAP_CALLS_AUDIT": "1",
                  "PYTHONUTF8": "1", "PYTHONHASHSEED": "0"}, env)
    errs = []
    for bad_vrc in ((("VRC", None)), ("VT", ["DEEPMAP_HOME=x"]), ("VT", ["NEO4J_URI=x"]), ("V0", ["NOEQUALS"])):
        try:
            V.flags_of(bad_vrc[0], bad_vrc[1])
            errs.append(bad_vrc)
        except (ValueError, KeyError):
            pass
    check("variants: VRC needs --set; --set refuses run settings, non-DEEPMAP keys and malformed items", not errs, errs)
    slugs = [V.slug(k) for k in V.VARIANTS]
    check("variants: every slug is unique and file-name safe", len(set(slugs)) == len(slugs)
          and all(re.fullmatch(r"[A-Za-z0-9_\-]+", s) for s in slugs), slugs)
    # ---- the engine VOID rule + the CLI option forms ------------------------------------------------------------
    OC = mods.get("offline_calls")
    if OC is not None:
        ev = OC.engine_void
        cases = {"fallback to ast": ev({"engine": "ast", "extraction": {"fallback_reason": "0 nodes"}}, "graphify"),
                 "graphify after a timeout": ev({"engine": "graphify", "extraction": {"timed_out": True}}, "graphify"),
                 "clean graphify": ev({"engine": "graphify", "extraction": {"resolved": "graphify"}}, "graphify"),
                 "no expectation": ev({"engine": "ast", "extraction": {}}, None)}
        check("offline_calls.engine_void: a fallback or a timeout is VOID for an engine-pinned run; clean graphify and "
              "an unpinned run are not", bool(cases["fallback to ast"]) and bool(cases["graphify after a timeout"])
              and cases["clean graphify"] is None and cases["no expectation"] is None, cases)
    o1, p1 = C.parse_cli(["score", "--seed", "20261022", "--n=40", "--sheet", "a.csv", "--sheet=b.csv", "x.json",
                          "--flag"], ("--seed", "--n"), ("--sheet",))
    check("the kit's CLIs take both `--opt value` (the design's spelling) and `--opt=value`; repeatable options collect",
          o1 == {"--seed": "20261022", "--n": "40", "--sheet": ["a.csv", "b.csv"], "--flag": ""}
          and p1 == ["score", "x.json"], (o1, p1))
    gate_e_review(mods, base)
    gate_e_effective(mods)


# The switches offline_calls.effective_switches must report, read through the product: the cross-family filter
# (sync._calls_family_filter_enabled) and the typed-calls configuration (typed_calls._config) beside the language
# lists, so every §8.3 variant's block differs from V0's AND from every other variant's (VF vs V0; VT+t / VT+np /
# VT[shape] vs VT). Without them VF's delta against V0 is VOID as an inert pair although the filter worked.
_EFF_NEW = ("calls_family_filter", "typed_shapes", "typed_replace", "typed_tests", "typed_nonprod", "typed_max_files")


def _variant_effective(OC, S, V, vid: str) -> dict:
    """offline_calls.effective_switches(S) under the variant's clean flags (every other DEEPMAP_* graph flag unset)."""
    with _env(**dict(_graph_flags_cleared(), **V.flags_of(vid))):
        return OC.effective_switches(S)


def gate_e_effective(mods: dict) -> None:
    """The `effective` block covers every switch a §8.3 variant sets (review: VF was always VOID): each variant's
    block, under its own clean flags and this tree's product predicates, differs from V0's and from every other
    variant's; the block is read through the product (a predicate that ignores a flag reports it off); variant_void
    VOIDs a dump whose requested switch the product left off; delta's inert-pair rule no longer voids VF."""
    OC, V, D = mods.get("offline_calls"), mods.get("variants"), mods.get("delta")
    if OC is None or V is None or D is None or not callable(getattr(OC, "effective_switches", None)):
        check("offline_calls.effective_switches / variants / delta are importable for the effective-block checks",
              False)
        return
    if str(BUILD) not in sys.path:
        sys.path.insert(0, str(BUILD))
    import sync as _SY  # noqa: E402 — the product's own predicates
    vids = [v for v, fl in V.VARIANTS.items() if fl is not None]
    try:
        eff = {v: _variant_effective(OC, _SY, V, v) for v in vids}
    except Exception as exc:  # noqa: BLE001 — a crash is a RED result
        check("offline_calls.effective_switches runs under every §8.3 variant's flags", False, repr(exc))
        return
    missing = sorted(k for k in _EFF_NEW if k not in eff["V0"])
    check("offline_calls.effective_switches reports the cross-family filter and the typed-calls configuration "
          f"({', '.join(_EFF_NEW)}), each read through the product", not missing, missing)
    same_as_v0 = sorted(v for v in vids if v != "V0" and eff[v] == eff["V0"])
    check("every §8.3 variant's effective block (its clean flags, this tree's product predicates) differs from "
          "V0's — VF included (calls_family_filter on): no variant is an inert pair against V0", not same_as_v0,
          {v: eff[v] for v in same_as_v0[:3]})
    seen, clashes = {}, []
    for v in vids:
        sig = json.dumps(eff[v], sort_keys=True, default=str)
        if sig in seen:
            clashes.append((seen[sig], v))
        seen.setdefault(sig, v)
    check("every two §8.3 variants with different flags have different effective blocks (an ablation compared "
          "against VT — VT+t, VT+np, VT[shape] — is never VOID as an inert pair)", not clashes, clashes)
    check("VF reports calls_family_filter True and V0 False; VT+t / VT+np report typed_tests / typed_nonprod True "
          "where VT reports False; VT[R1] reports typed_shapes ['R1'] where VT reports every shape",
          eff["VF"].get("calls_family_filter") is True and eff["V0"].get("calls_family_filter") is False
          and eff["VT+t"].get("typed_tests") is True and eff["VT"].get("typed_tests") is False
          and eff["VT+np"].get("typed_nonprod") is True and eff["VT"].get("typed_nonprod") is False
          and eff["VT[R1]"].get("typed_shapes") == ["R1"] and eff["VT"].get("typed_shapes") == sorted(V.SHAPES),
          {v: {k: eff[v].get(k) for k in _EFF_NEW} for v in ("V0", "VF", "VT", "VT+t", "VT+np", "VT[R1]")})
    # Read through the product: a sync whose filter predicate ignores the flag, a typed config that ignores TESTS.
    deaf = SimpleNamespace(graphify_extract=getattr(_SY, "graphify_extract", None),
                           _typed_calls_link=getattr(_SY, "_typed_calls_link", None),
                           _calls_family_filter_enabled=lambda: False)
    via = {}
    try:
        via["VF, a filter predicate that ignores the flag"] = _variant_effective(OC, deaf, V, "VF").get(
            "calls_family_filter")
        import typed_calls as _TC  # noqa: E402
        import dataclasses
        orig = _TC._config
        _TC._config = lambda env, registry: dataclasses.replace(orig(env, registry), tests=False)
        try:
            via["VT+t, a typed config that ignores TESTS"] = _variant_effective(OC, _SY, V, "VT+t").get("typed_tests")
        finally:
            _TC._config = orig
    except Exception as exc:  # noqa: BLE001
        via["error"] = repr(exc)
    check("effective_switches reads the switches through the product, never the flag text: a filter predicate that "
          "ignores DEEPMAP_CALLS_FAMILY_FILTER reports it off under VF, a typed config that ignores "
          "DEEPMAP_TYPED_CALLS_TESTS reports it off under VT+t", via == {
              "VF, a filter predicate that ignores the flag": False,
              "VT+t, a typed config that ignores TESTS": False}, via)
    # variant_void: a requested switch the product left off is VOID (the real blocks are not).
    vv = OC.variant_void
    cl = {"error": None, "emitted": 1}

    def dump_of(vid, **override):
        return {"flags": V.flags_of(vid), "effective": dict(eff[vid], **override), "typed_step": "present",
                "call_linking": cl}
    cases = {"VF, the filter left off": vv(dump_of("VF", calls_family_filter=False)),
             "VT+t, tests left off": vv(dump_of("VT+t", typed_tests=False)),
             "VT+np, non-production left off": vv(dump_of("VT+np", typed_nonprod=False)),
             "VT[R1], the shape not turned on": vv(dump_of("VT[R1]", typed_shapes=[])),
             "V9, CALLS_PLUS left off": vv(dump_of("V9", calls_plus=False)),
             "REPLACE=0, the product still replacing": vv(
                 {"flags": dict(V.flags_of("VT"), DEEPMAP_TYPED_CALLS_REPLACE="0"),
                  "effective": dict(eff["VT"], typed_replace=True), "typed_step": "present", "call_linking": cl}),
             "PRECISE=1, precise calls left off": vv({"flags": {"DEEPMAP_PRECISE_CALLS": "1"},
                                                      "effective": dict(eff["V0"]), "typed_step": "present"})}
    real = {v: vv(dump_of(v)) for v in vids}
    check("offline_calls.variant_void: a dump whose requested switch the product left off is VOID and names the flag "
          "(the cross-family filter, TESTS, NONPROD, a SHAPES arm, CALLS_PLUS, REPLACE=0, PRECISE=1); every §8.3 "
          "variant's real block on this tree is not",
          all(cases.values()) and not any(real.values())
          and "DEEPMAP_CALLS_FAMILY_FILTER" in str(cases["VF, the filter left off"])
          and "DEEPMAP_TYPED_CALLS_SHAPES" in str(cases["VT[R1], the shape not turned on"]),
          ({k: v for k, v in cases.items() if not v}, {k: v for k, v in real.items() if v}))
    # delta: the review's probe — VF against V0 with the real blocks is a measurement, not an inert pair.
    pair = [("a.f", "a.g", "AMBIGUOUS"), ("a.f", "b.h", "AMBIGUOUS")]
    d_vf = D.delta(dict(_dump(pair), flags={}, effective=eff["V0"]),
                   dict(_dump(pair[:1]), flags=V.flags_of("VF"), effective=eff["VF"]))
    d_ab = D.delta(dict(_dump(pair), flags=V.flags_of("VT"), effective=eff["VT"]),
                   dict(_dump(pair), flags=V.flags_of("VT+t"), effective=eff["VT+t"]))
    check("delta: VF against V0 (the real effective blocks) is not VOID — it measures the filter; an ablation "
          "against VT (VT+t) is not VOID either", d_vf["void"] == [] and d_ab["void"] == [],
          (d_vf["void"], d_ab["void"]))


# The V0 / VT dump pair of the S0 review (requests on 0.8.39, a tree with build/typed_calls.py but no
# sync._typed_calls_link): identical edges, VT's `effective` differs from V0's only in the language lists.
_EFF_V0 = {"audit_languages": [], "calls_plus": False, "keyed_calls": False, "precise_calls": False,
           "precise_families": [], "precise_fixes": True, "typed_languages": [], "typed_step": "absent"}
_EFF_VT = dict(_EFF_V0, audit_languages=["javascript", "python", "typescript"],
               typed_languages=["javascript", "python", "typescript"])
_FLAGS_VT = {"DEEPMAP_CALLS_AUDIT": "1", "DEEPMAP_TYPED_CALLS": "python,typescript,javascript"}


class _MetaDriver:
    """A recorder standing in for Neo4j under run_variant.census: a graph whose :_DeepMapMeta node carries ONLY
    the property sync writes (`owning_root`), plus one CALLS edge. A query for any other meta property reads null,
    exactly as Neo4j would."""

    def __init__(self, owning_root):
        self.meta = {"id": "singleton", "owning_root": owning_root}
        self.queries = []

    def session(self, *a, **k):
        drv = self

        class _S(_Sess):
            def run(self, q, *a, **k):
                drv.queries.append(q)
                m = re.search(r"_DeepMapMeta\)\s+RETURN\s+m\.(\w+)\s+AS\s+(\w+)", q)
                if m:
                    return _Res([{m.group(2): drv.meta.get(m.group(1))}])
                if "labels(n)[0]" in q:
                    return _Res([{"l": "Function", "c": 2}])
                if "n.embedding IS NOT NULL" in q:
                    return _Res([{"c": 2}])
                if "r.confidence AS k" in q:
                    return _Res([{"k": "INFERRED", "c": 1}])
                if "a.key AS a" in q:
                    return _Res([{"a": "f", "b": "g", "c": "INFERRED"}])
                if "n.key AS k" in q:
                    return _Res([{"k": "f", "s": "", "nm": "f"}, {"k": "g", "s": "", "nm": "g"}])
                return _Res([])
        return _S()


def gate_e_review(mods: dict, base: Path) -> None:
    """The S0 review round: the owner check of the live captures, the inert-variant VOID rule, the cross-file
    EXTRACTED invariant, compare_calls' prefix side, delta's EXTRACTED bucket, the audit's losses and stratified
    removal share, saturated spot symbols and the TS/JS atom spans."""
    C, CC, D, AS, IS, SM, RV = (mods[m] for m in ("_common", "compare_calls", "delta", "audit_sample", "impact_spot",
                                                  "score_m", "run_variant"))
    OC = mods.get("offline_calls")
    # ---- run_variant.census / owner_problem: the owner stamp sync writes -----------------------------------------
    if str(BUILD) not in sys.path:
        sys.path.insert(0, str(BUILD))
    import sync as _SY  # noqa: E402 — the product's identity, as run_variant's check computes it
    clone = base / "owner_clone"
    clone.mkdir(exist_ok=True)
    stamp = str(_SY._project_identity(clone))
    drv = _MetaDriver(stamp)
    try:
        cen = RV.census(drv)
    except Exception as exc:  # noqa: BLE001
        cen = {"error": repr(exc)}
    check("run_variant.census reads the owner stamp sync writes (:_DeepMapMeta owning_root) — a graph stamped by "
          "sync for the clone reports that stamp, not null",
          cen.get("owner") == stamp and any("owning_root" in q for q in drv.queries),
          {"owner": cen.get("owner"), "queries": [q for q in drv.queries if "_DeepMapMeta" in q]})
    op = getattr(RV, "owner_problem", None)
    if not callable(op):
        check("run_variant.owner_problem exists (the one owner check both live captures use)", False)
    else:
        other = base / "owner_other"
        other.mkdir(exist_ok=True)
        verdicts = {"own stamp": op(clone, stamp), "another project's stamp": op(clone, str(_SY._project_identity(other))),
                    "no stamp": op(clone, None)}
        check("run_variant.owner_problem: a capture whose graph is stamped for the clone passes; another project's "
              "stamp and a missing stamp are refused", verdicts["own stamp"] is None
              and verdicts["another project's stamp"] and verdicts["no stamp"], verdicts)
    uses = {}
    for mod in ("run_variant", "measure_self"):
        tree = ast.parse(Path(mods[mod].__file__).read_text(encoding="utf-8"))
        fn = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "cmd_capture"), None)
        names = {_call_name(c) for c in _calls_in(fn)} if fn is not None else set()
        uses[mod] = bool(names & {"owner_problem", "RV.owner_problem"}) and "_same_project" not in \
            {n.split(".")[-1] for n in names}
    check("run_variant and measure_self capture through owner_problem (no private copy of the owner rule)",
          all(uses.values()), uses)
    # ---- offline_calls: the inert-variant VOID rule, the cross-file EXTRACTED invariant ---------------------------
    if OC is not None and callable(getattr(OC, "variant_void", None)):
        vv = OC.variant_void
        v0 = {"flags": {}, "effective": dict(_EFF_V0), "typed_step": "absent", "call_linking": None}
        vt = {"flags": dict(_FLAGS_VT), "effective": dict(_EFF_VT), "typed_step": "absent", "call_linking": None}
        vt_err = dict(vt, typed_step="present", effective=dict(_EFF_VT, typed_step="present"),
                      call_linking={"error": "RuntimeError: boom", "added": 0})
        vt_ok = dict(vt_err, call_linking={"error": None, "added": 3})
        vt_lang = dict(vt_ok, effective=dict(_EFF_VT, typed_step="present", typed_languages=["python"]))
        vp_csv = {"flags": {"DEEPMAP_PRECISE_CALLS": "python,js"}, "effective": dict(_EFF_V0), "typed_step": "absent"}
        vp_ok = dict(vp_csv, effective=dict(_EFF_V0, precise_families=["js", "python"], precise_calls=True))
        got = {"V0": vv(v0), "VT, no typed step (the review's pair)": vv(vt),
               "VT, the step erred": vv(vt_err), "VT, the step ran": vv(vt_ok),
               "VT, a requested language not turned on": vv(vt_lang),
               "PRECISE=python,js, no family turned on": vv(vp_csv), "PRECISE=python,js, families on": vv(vp_ok),
               "a flag no build/*.py names": vv({"flags": {"DEEPMAP_ZZ": "1"}}, "x = 'DEEPMAP_ZZZ'"),
               "a flag the code names": vv({"flags": {"DEEPMAP_ZZ": "1"}}, "x = 'DEEPMAP_ZZ'")}
        want_void = {"VT, no typed step (the review's pair)", "VT, the step erred",
                     "VT, a requested language not turned on", "PRECISE=python,js, no family turned on",
                     "a flag no build/*.py names"}
        absent_with_stats = vv(dict(vt, call_linking={"error": None, "added": 0}))
        check("offline_calls.variant_void: the review's V0/VT pair — VT on a sync without _typed_calls_link — is "
              "VOID (and says the step is missing, even beside stats); so are an erring typed step, a requested "
              "language left off, a PRECISE family list the code ignores and a flag no build/*.py file names; V0, a "
              "typed step that ran and PRECISE families on are not",
              {k for k, v in got.items() if v} == want_void
              and "_typed_calls_link" in str(got["VT, no typed step (the review's pair)"])
              and "_typed_calls_link" in str(absent_with_stats), (got, absent_with_stats))
        dv = D.delta(dict(_dump([("a.f", "a.g", "INFERRED")]), **v0), dict(_dump([("a.f", "a.g", "INFERRED")]), **vt))
        same_eff = D.delta(dict(_dump([]), **v0), dict(_dump([]), flags=dict(_FLAGS_VT), effective=dict(_EFF_V0)))
        flagged = D.delta(dict(_dump([]), **v0), dict(_dump([]), **dict(vt, void="the variant was inert")))
        check("delta: a pair whose variant flags differ but whose effective switches equal V0's is VOID, and so is a "
              "pair holding a VOID dump; the review's V0/VT pair (effective lists differ) is left to variant_void",
              bool(same_eff["void"]) and bool(flagged["void"]) and dv["void"] == [],
              (same_eff["void"], flagged["void"], dv["void"]))
    else:
        check("offline_calls.variant_void exists (the inert-variant VOID rule in code)", False)
    if OC is not None and callable(getattr(OC, "extracted_cross_file", None)) and callable(getattr(OC, "exit_code", None)):
        xn = [_n("a.f", "Function", "f", "a.py", 3), _n("a.g", "Function", "g", "a.py", 10),
              _n("b.h", "Function", "h", "b.py", 1)]
        xe = {("a.f", "a.g"): "EXTRACTED", ("a.f", "b.h"): "EXTRACTED", ("a.g", "b.h"): "INFERRED"}
        md = OC.make_dump(base, xn, [("a.f", "a.g")], xe, {"info": {}}, code=None)
        clean = OC.make_dump(base, xn, [], {("a.f", "a.g"): "EXTRACTED"}, {"info": {}}, code=None)
        check("offline_calls: an EXTRACTED edge across files is counted (counts.extracted_cross_file) and named "
              "(invariant) — build exits 4 for it, 3 for a VOID dump, 0 for a clean one",
              md["counts"]["extracted_cross_file"] == 1 and md["invariant"] == {"extracted_cross_file": [["a.f", "b.h"]]}
              and clean["counts"]["extracted_cross_file"] == 0 and clean["invariant"] is None
              and (OC.exit_code(md), OC.exit_code(clean), OC.exit_code(dict(md, void="x"))) == (4, 0, 3),
              (md["counts"], md["invariant"]))
    else:
        check("offline_calls.extracted_cross_file / exit_code exist (the cross-file EXTRACTED invariant)", False)
    # ---- compare_calls: --src-prefix filters by the CALLER's file (both directions) -------------------------------
    pn = [_n("lib.f", "Function", "f", "lib/a.py", 1), _n("lib.g", "Function", "g", "lib/a.py", 10),
          _n("ext.bh", "Function", "h", "ext/b.py", 1), _n("ext.ch", "Function", "h", "ext/c.py", 1)]
    pt = {"schema": "call-linking/truth/1", "rows": [
        _row("lib/a.py", [["f", 1, []]], "bare", "h", [_d("ext/b.py", 1, "function", "h")]),
        _row("lib/a.py", [["f", 1, []]], "bare", "g", [_d("lib/a.py", 10, "function", "g")]),
        _row("ext/b.py", [["h", 1, []]], "bare", "g", [_d("lib/a.py", 10, "function", "g")])]}
    pd = {"nodes": pn, "edges": [["lib.f", "ext.bh", "INFERRED"], ["lib.f", "ext.ch", "AMBIGUOUS"],
                                 ["lib.f", "lib.g", "INFERRED"], ["ext.ch", "lib.g", "INFERRED"]]}
    keys = ("T", "G", "confirmed", "refuted", "unverified", "recall", "refuted_share", "ambiguous_share")
    s_lib, s_ext = CC.score(pt, pd, ["lib/"]), CC.score(pt, pd, ["ext/"])
    check("compare_calls --src-prefix keeps a pair by its CALLER's file: lib/ (calls INTO ext/ kept, ext/'s call "
          "into lib/ dropped) |T| 2, |G| 3, confirmed 2, refuted 1, refuted share .3333; ext/ |T| 1, |G| 1, 0 "
          "confirmed, 1 unverified",
          tuple(s_lib[k] for k in keys) == (2, 3, 2, 1, 0, 1.0, 0.3333, 0.3333)
          and tuple(s_ext[k] for k in keys) == (1, 1, 0, 0, 1, 0.0, 0.0, 0.0),
          ({k: s_lib[k] for k in keys}, {k: s_ext[k] for k in keys}))
    # ---- delta: the EXTRACTED bucket holds ALL the variant's EXTRACTED edges (kept ones too) -----------------------
    b3 = _dump([("a.f", "a.g", "EXTRACTED"), ("a.f", "a.C.m", "INFERRED")])
    v3 = _dump([("a.f", "a.g", "EXTRACTED"), ("a.f", "a.C.m", "EXTRACTED"), ("a.g", "b.D.m", "EXTRACTED")])
    d3 = D.delta(b3, v3)
    check("delta: the EXTRACTED bucket holds every EXTRACTED edge of the variant — one the base already had "
          "EXTRACTED, a promoted one and a new one; the cross-file one (a.py -> b.py) is counted",
          [(i["caller"], i["callee"]) for i in d3["items"]["EXTRACTED"]]
          == [("a.f", "a.C.m"), ("a.f", "a.g"), ("a.g", "b.D.m")] and d3.get("extracted_cross_file") == 1,
          (d3["items"]["EXTRACTED"], d3.get("extracted_cross_file")))
    sx = CC.score(TRUTH, v3)
    check("compare_calls reports the cross-file EXTRACTED edges of a dump (a.g -> b.D.m)",
          sx.get("extracted_cross_file") == 1 and sx.get("extracted_cross_file_pairs") == [["a.g", "b.D.m"]],
          sx.get("extracted_cross_file"))
    # ---- audit_sample: losses over both strata; the stratified correct-removal share ------------------------------
    keyd = {"label": "x", "strata": {"removed": {"population": 10, "sampled": 2},
                                     "removed_confirmed": {"population": 1, "sampled": 1}},
            "rows": {"r5": {"buckets": ["removed"], "verdict": "refuted", "caller": "c5", "callee": "d5"},
                     "r6": {"buckets": ["removed"], "verdict": "refuted", "caller": "c6", "callee": "d6"},
                     "r7": {"buckets": ["removed_confirmed"], "verdict": "confirmed", "caller": "c7", "callee": "d7"},
                     "r8": {"buckets": ["added"], "verdict": "confirmed", "caller": "c8", "callee": "d8"}}}
    votes = {"r5": "TRUE", "r6": "FALSE", "r7": "FALSE", "r8": "TRUE"}
    sc = AS.score(keyd, [dict(votes), dict(votes)])
    lo = sc.get("losses") or {}
    ra = sc.get("removed_all") or {}
    check("audit score: a SAMPLED removal audited TRUE by every reviewer is a LOSS (top-level losses, with its id), "
          "not only a lower correct-removal share; an added row audited TRUE is no loss",
          lo.get("n") == 1 and [x.get("id") for x in lo.get("rows", [])] == ["r5"], lo)
    check("audit score: the correct-removal share over BOTH strata, population-weighted: (10 x 1/2 + 1 x 1/1) / 11 "
          "= .5455, lower bound (10 x .0945 + 1) / 11 = .1768",
          (ra.get("population"), ra.get("share"), ra.get("lower_bound")) == (11, 0.5455, 0.1768), ra)
    st = AS.strata({"removed": [{"verdict": "confirmed"}, {"verdict": "refuted"}, {"verdict": None}]},
                   {"removed": [{"verdict": "refuted"}], "removed_confirmed": [{"verdict": "confirmed"}]}) \
        if callable(getattr(AS, "strata", None)) else None
    check("audit sample: key.json records both removal strata (population, sampled)",
          st == {"removed": {"population": 2, "sampled": 1}, "removed_confirmed": {"population": 1, "sampled": 1}}, st)
    # ---- impact_spot: a spot saturated at V0 can never improve ------------------------------------------------------
    tdir = base / "spot_sat"
    tdir.mkdir(exist_ok=True)
    full = _dump([("a.f", "a.C.m", "AMBIGUOUS")])
    empty = _dump([])
    for nm, obj in (("t.json", TRUTH), ("full.json", full), ("empty.json", empty)):
        (tdir / nm).write_text(json.dumps(obj), encoding="utf-8")
    so, se = _quiet()
    with so, se:
        rc_sat = IS.main([f"--truth={tdir / 't.json'}", "--symbols=m@a.py", str(tdir / "full.json"),
                          f"--base={tdir / 'full.json'}"])
        rc_ok = IS.main([f"--truth={tdir / 't.json'}", "--symbols=m@a.py", str(tdir / "full.json"),
                         f"--base={tdir / 'empty.json'}"])
    check("impact_spot --base: a spot whose base hop-1 recall is already 1 is SATURATED (exit 1: it can never count "
          "toward D3-G1); one the base misses is not (exit 0)", (rc_sat, rc_ok) == (1, 0), (rc_sat, rc_ok))
    # ---- score_m: `$` symbols and TS/JS def spans -----------------------------------------------------------------
    a1 = SM.parse_atom("packages/zod/src/v4/core/errors.ts:248 — $ZodError (export const $ZodError = $constructor)")
    check("score_m parses a `$` atom symbol ($ZodError)", bool(a1) and a1["sym"] == "$ZodError" and a1["last"] == "$ZodError",
          a1)
    try:
        import tree_sitter  # noqa: F401
        import tree_sitter_typescript  # noqa: F401
        have_ts = True
    except Exception:  # noqa: BLE001
        have_ts = False
    if not have_ts:
        skip("score_m: TS/JS def spans", "tree-sitter / tree-sitter-typescript are not importable in this Python")
        return
    ts_clone = base / "sm_ts"
    (ts_clone / "src").mkdir(parents=True, exist_ok=True)
    (ts_clone / "src" / "x.ts").write_text(
        "export const $ZodError = $constructor(\"$ZodError\", initializer);\n"
        "export function _parse(schema: any, value: unknown) {\n"
        "  const result = schema._zod.run({ value, issues: [] }, ctx);\n"
        "  if (result.issues.length) {\n"
        "    throw new Err(result.issues.map((iss: any) => finalizeIssue(iss)));\n"
        "  }\n"
        "  return result.value;\n"
        "}\n"
        "export class Repo {\n"
        "  @Log()\n"
        "  find(id: number) {\n"
        "    return id;\n"
        "  }\n"
        "}\n", encoding="utf-8")
    at_err = SM.parse_atom("src/x.ts:1 — $ZodError (export const $ZodError = $constructor)")
    at_throw = SM.parse_atom("src/x.ts:5 — throw new Err mapping result.issues through finalizeIssue")
    at_ret = SM.parse_atom("src/x.ts:12 — return id")
    n_err = {"name": "$ZodError", "type": "Class", "file": "src/x.ts", "line": 1}
    n_parse = {"name": "_parse", "type": "Function", "file": "src/x.ts", "line": 2}
    n_find = {"name": "Repo.find", "type": "Method", "file": "src/x.ts", "line": 11}
    h = {"$ZodError node, $ZodError atom": SM.hits(at_err, [n_err], ts_clone),
         "_parse node, throw atom inside it": SM.hits(at_throw, [n_parse], ts_clone),
         "Repo.find node, throw atom outside it": SM.hits(at_throw, [n_find], ts_clone),
         "Repo.find node, atom in its decorated span": SM.hits(at_ret, [n_find], ts_clone)}
    got = {k: (v["strict"], v["spec"]) for k, v in h.items()}
    check("score_m on TS: a `$ZodError` node at the atom line hits STRICT; a node whose TS span (tree-sitter) covers "
          "the atom line hits SPEC (and STRICT as the innermost def); a node elsewhere hits neither",
          got == {"$ZodError node, $ZodError atom": (True, True), "_parse node, throw atom inside it": (True, True),
                  "Repo.find node, throw atom outside it": (False, False),
                  "Repo.find node, atom in its decorated span": (True, True)}, got)


# =====================================================================================================
# F — stage0_refreeze --env
# =====================================================================================================
class _Abort(Exception):
    pass


def gate_f(refreeze_path: Path, base: Path) -> None:
    print("\n-- F: stage0_refreeze --env forwards only the DEEPMAP_* keys it is given; child_env otherwise unchanged --")
    try:
        sys.path.insert(0, str(TESTS))
        RF = _load_file(refreeze_path, f"_cl_s0_refreeze_{time.time_ns()}")
    except Exception as exc:  # noqa: BLE001
        check(f"{refreeze_path.name} loads", False, repr(exc))
        return
    basenv = {"NEO4J_URI": "bolt://localhost:7687", "DEEPMAP_EXTRACTOR": "ast", "DEEPMAP_HOME": "/h",
              "DEEPMAP_PYTHON": "/py", "PATH": "/bin", "NEO4J_PASSWORD": "pw"}
    plain = RF.child_env(dict(basenv))
    check("child_env without --env is exactly the stripped default environment",
          plain == {"DEEPMAP_HOME": "/h", "DEEPMAP_PYTHON": "/py", "PATH": "/bin", "NEO4J_PASSWORD": "pw",
                    "PYTHONUTF8": "1", "PYTHONHASHSEED": "0", "HF_HUB_OFFLINE": "1"}, plain)
    try:
        with_extra = RF.child_env(dict(basenv), extra={"DEEPMAP_TYPED_CALLS": "1", "DEEPMAP_CALLS_AUDIT": "1"})
        none_extra = RF.child_env(dict(basenv), extra=None)
    except TypeError as exc:
        check("child_env takes the --env flags (extra=)", False, repr(exc))
        return
    check("child_env(extra=...) == the stripped default + exactly those keys", with_extra == dict(
        plain, DEEPMAP_TYPED_CALLS="1", DEEPMAP_CALLS_AUDIT="1") and none_extra == plain, with_extra)
    parse = getattr(RF, "parse_env_args", None)
    if not callable(parse):
        check("stage0_refreeze.parse_env_args exists", False)
        return
    ok, why = parse(["DEEPMAP_TYPED_CALLS=python,typescript", "DEEPMAP_CALLS_AUDIT=1", "DEEPMAP_X=a=b"])
    check("--env accepts DEEPMAP_* graph flags (a value may hold '=' and ',')",
          why is None and ok == {"DEEPMAP_TYPED_CALLS": "python,typescript", "DEEPMAP_CALLS_AUDIT": "1",
                                 "DEEPMAP_X": "a=b"}, (ok, why))
    refused = {item: parse([item])[1] for item in ("NEO4J_URI=bolt://x", "PATH=/x", "DEEPMAP_HOME=/x",
                                                   "DEEPMAP_PYTHON=/x", "deepmap_typed_calls=1", "DEEPMAP_=1",
                                                   "DEEPMAP_TYPED_CALLS")}
    check("--env refuses non-DEEPMAP keys, the home / interpreter, lower case, an empty name and a missing '='",
          all(refused.values()), refused)
    def run_main(args):
        so, se = _quiet()
        try:
            with so, se:
                return RF.main(args)
        except SystemExit as exc:          # argparse rejecting an unknown option (a tree without --env)
            return exc.code if isinstance(exc.code, int) else 2

    work = base / "rf_work"
    rc_bad = run_main(["--work", str(work), "--env", "NEO4J_URI=bolt://x"])
    rc_freeze = run_main(["--work", str(work), "--freeze", "--env", "DEEPMAP_TYPED_CALLS=1"])
    check("main: a refused --env exits 2 before anything is created", rc_bad == 2 and not work.exists(), rc_bad)
    check("main: --env with --freeze exits 2 (an oracle is frozen on the default graph only)",
          rc_freeze == 2 and not work.exists(), rc_freeze)
    saved = (RF.child_env, RF._registry, RF._load_build)
    for argv, want, label in (
            (["--check", "--env", "DEEPMAP_TYPED_CALLS=1"], {"DEEPMAP_TYPED_CALLS": "1"},
             "main --check --env DEEPMAP_TYPED_CALLS=1: every step's environment gets exactly that flag"),
            (["--check"], {}, "main --check without --env: no flag is added (the default pipeline, as before)")):
        seen = {}

        def _rec(base_=None, extra=None, _seen=seen):   # a RECORDER: the real child_env still runs
            _seen["extra"] = dict(extra or {})
            return saved[0](base_, extra=extra)

        def _abort(name):
            raise _Abort(name)
        RF.child_env = _rec
        RF._registry = lambda: {"oracles": {rid: {"corpus": {"sha": "a" * 40}} for rid in RF.ORDER}}
        RF._load_build = _abort            # the first step after the environment is built: stop there
        try:
            rc = run_main(["--work", str(base / f"rf_work_{len(argv)}")] + argv)
            check(label, False, f"main returned {rc} without reaching the build")
        except _Abort:
            check(label, seen.get("extra") == want, seen)
        finally:
            RF.child_env, RF._registry, RF._load_build = saved


# =====================================================================================================
# G — compliance
# =====================================================================================================
def gate_g() -> None:
    print("\n-- G: compliance — jedi, TypeScript and the C# reference (Roslyn) appear in no shipped list --")
    hits = {}
    for rel in ("requirements.txt", "sbom.cdx.json", "NOTICE", "THIRD-PARTY-LICENSES.md", "install.sh", "install.ps1",
                ".claude-plugin/plugin.json", ".claude-plugin/marketplace.json"):
        p = REPO / rel
        if not p.is_file():
            continue
        t = p.read_text(encoding="utf-8", errors="replace")
        found = [w for w in ("jedi", "parso") if re.search(rf"\b{w}\b", t, re.I)]
        if re.search(r"typescript@|npm\s+(i|install)\b|\"typescript\"|^\s*typescript\b", t, re.I | re.M):
            found.append("typescript (npm package)")
        if found:
            hits[rel] = found
    try:
        comps = [str(c.get("name", "")).lower() for c in json.loads((REPO / "sbom.cdx.json").read_text(
            encoding="utf-8")).get("components", [])]
    except Exception:  # noqa: BLE001
        comps = []
    bad_c = [c for c in comps if c in ("jedi", "parso", "typescript")]
    check("requirements / SBOM / NOTICE / THIRD-PARTY-LICENSES / installers / manifests name neither jedi (parso) nor "
          "the TypeScript package", not hits and not bad_c, (hits, bad_c))
    gate_g_cs()


SHIPPED_LISTS = ("requirements.txt", "sbom.cdx.json", "NOTICE", "THIRD-PARTY-LICENSES.md", "install.sh", "install.ps1")


def shipped_list_hits(repo: Path) -> dict:
    """{relative path: [matches]} of the C# reference's names in every shipped list (PREREG_CS §8.2): requirements,
    the SBOM (its text and its components), NOTICE, THIRD-PARTY-LICENSES, both installers, every plugin manifest."""
    rels = list(SHIPPED_LISTS) + sorted(p.relative_to(repo).as_posix()
                                        for p in (repo / ".claude-plugin").glob("*.json"))
    hits = {}
    for rel in rels:
        p = repo / rel
        if not p.is_file():
            continue
        found = sorted({m.group(0) for m in CS_SHIPPED_NAMES.finditer(p.read_text(encoding="utf-8", errors="replace"))})
        if found:
            hits[rel] = found
    try:
        comps = json.loads((repo / "sbom.cdx.json").read_text(encoding="utf-8")).get("components", [])
    except Exception:  # noqa: BLE001 — a missing SBOM has nothing to list
        comps = []
    bad = [c.get("name") for c in comps if isinstance(c, dict) and any(
        CS_SHIPPED_NAMES.search(str(c.get(k) or "")) for k in ("name", "group", "purl", "bom-ref"))]
    if bad:
        hits["sbom.cdx.json components"] = bad
    return hits


def gate_g_cs() -> None:
    hits = shipped_list_hits(REPO)
    check("requirements / SBOM (text and components) / NOTICE / THIRD-PARTY-LICENSES / install.sh / install.ps1 / "
          "every .claude-plugin manifest name neither Roslyn, Microsoft.CodeAnalysis nor truth_roslyn (PREREG_CS "
          "§8.2: the C# reference is measurement only, never shipped)", not hits, hits)


# =====================================================================================================
# H — PREREG.md and gt_self65.json
# =====================================================================================================
def gate_h(mods: dict, kit: Path) -> None:
    print("\n-- H: PREREG.md (§8 verbatim, hash-locked) and gt_self65.json --")
    try:
        pre = (kit / "PREREG.md").read_text(encoding="utf-8")
    except OSError as exc:
        check("PREREG.md exists", False, repr(exc))
        return
    lines = pre.split("\n")
    try:
        b = next(i for i, ln in enumerate(lines) if ln.startswith("<!-- PREREG-SECTION-8 BEGIN"))
        e = next(i for i, ln in enumerate(lines) if ln.startswith("<!-- PREREG-SECTION-8 END"))
        block = "\n".join(lines[b + 1:e]) + "\n"
    except StopIteration:
        block = ""
    got = hashlib.sha256(block.encode("utf-8")).hexdigest()
    check("PREREG §8 is the design's §8 verbatim (sha256 locked: thresholds are never edited after data)",
          got == PREREG_S8_SHA256 and block.startswith("## 8. Measurement protocol\n"), got)
    check("PREREG records the RC code SHA as the S5 working tree (no commit) and a build/*.py content hash slot",
          "the working tree as of the S5 suite run (no commit: the operator commits)" in pre
          and "Content hash of build/\\*.py" in pre and "offline_calls.py codehash" in pre)
    check("PREREG records the engine builds (graphifyy 0.8.39 + 0.8.51) and the reference resolvers (jedi 0.20.1, "
          "TypeScript 5.6.3)", all(w in pre for w in ("graphifyy 0.8.39", "graphifyy 0.8.51", "jedi 0.20.1",
                                                      "TypeScript 5.6.3")))
    rm = (kit / "run_matrix.sh").read_text(encoding="utf-8") if (kit / "run_matrix.sh").is_file() else ""
    rm_shas = dict(re.findall(r"^\s*([\w\-]+__[\w.\-]+|stage0_build)\)\s+echo \"([0-9a-f]{40})\|", rm, re.M))
    pre_shas = set(re.findall(r"\b([0-9a-f]{40})\b", pre.split("<!-- PREREG-SECTION-8 BEGIN")[0]))
    check("every corpus SHA run_matrix.sh pins is in PREREG (and the TS app + JS repo are pinned there)",
          rm_shas and set(rm_shas.values()) <= pre_shas
          and "c1c2cc4e448b279ff083272df1ac50d20c3304fa" in pre_shas
          and "179ae84efec61b14206d0305d941daed6c6d07f9" in pre_shas, (rm_shas, sorted(pre_shas)))
    IS = mods.get("impact_spot")
    if IS is not None:
        check("PREREG names the 5 TS/JS spot symbols impact_spot.py registers, and the 5 Python ones",
              all(f"`{s}`" in pre for s in IS.TS_SPOTS) and all(f"`{s}`" in pre for s in IS.PY_SPOTS),
              [s for s in IS.TS_SPOTS + IS.PY_SPOTS if f"`{s}`" not in pre])
        head = pre.split("<!-- PREREG-SECTION-8 BEGIN")[0]
        sec4 = head.split("## 4.")[-1].split("## 5.")[0] if "## 4." in head else ""
        rows = {}   # symbol -> (the integer-only cells [reference callers, V0 hits 0.8.39, V0 hits 0.8.51], line)
        for ln in sec4.splitlines():
            cells = [c.strip() for c in ln.strip().strip("|").split("|")] if ln.startswith("|") else []
            sym = next((c[1:-1] for c in cells[:2] if re.fullmatch(r"`[^`]+`", c)), None)
            if sym:   # cells[0] is the row number (TS table) or the symbol (Python table): never a count
                rows[sym] = ([int(c) for c in cells[1:] if re.fullmatch(r"\d+", c)][-3:], ln)

        def unsaturated(nums):
            return len(nums) == 3 and nums[0] > 0 and nums[1] < nums[0] and nums[2] < nums[0]
        ts_bad = {s: (rows[s][0] if s in rows else "missing") for s in IS.TS_SPOTS
                  if s not in rows or not unsaturated(rows[s][0])}
        check("PREREG §4 records every TS/JS spot's V0 hop-1 recall on both engines (reference callers, hits) and none "
              "is saturated (V0 recall < 1: the D3-G1 '3 of 5' counts five spots that CAN improve)", not ts_bad, ts_bad)
        py_bad = {s: (rows[s][0] if s in rows else "missing") for s in IS.PY_SPOTS
                  if s not in rows or not unsaturated(rows[s][0])}
        check("PREREG §4 records every Python spot's V0 hop-1 recall on both engines and none is saturated (the "
              "design-given `_mk_project` was replaced before RC: amendment A2)", not py_bad, py_bad)
        check("every spot symbol lives on a corpus run_matrix.sh pins to a SHA (its PREREG V0 numbers are "
              "reproducible at RC; the `self` working tree moves)", rm_shas and set(IS.SPOTS) <= set(rm_shas),
              sorted(set(IS.SPOTS) - set(rm_shas)))
    amend = pre.split("## Amendments before RC")[-1].split("<!-- PREREG-SECTION-8 BEGIN")[0] \
        if "## Amendments before RC" in pre else ""
    a1 = amend.split("### A1")[-1].split("### A2")[0] if "### A1" in amend else ""
    a2 = amend.split("### A2")[-1] if "### A2" in amend else ""
    check("PREREG has a dated 'Amendments before RC' section ahead of the verbatim §8 (which stays hash-locked)",
          bool(amend) and "2026-10-23" in amend and "before any RC data" in amend
          and pre.index("## Amendments before RC") < pre.index("<!-- PREREG-SECTION-8 BEGIN"))
    check("PREREG amendment A1 records the E change with its reason: parity on edges whose BOTH endpoints exist on "
          "both engines (version-stable keys), the raw symmetric difference reported beside it, the zod static "
          "arrow-field `create` members graphify 0.8.39 makes no node for",
          all(w in a1 for w in ("both endpoints", "raw symmetric difference", "label:name@file:line", "engdiff.py",
                                "static create", "0.8.39", "90", "74")), a1[:300])
    check("PREREG amendment A2 records the spot replacement with its reason: `_mk_project` saturated at V0, "
          "`read_records` chosen by V0 data only, before any typed-linking (VT) data on it",
          all(w in a2 for w in ("`_mk_project`", "`read_records`", "SATURATED", "V0 data only", "no typed-linking",
                                "impact_spot.py")), a2[:300])
    check("PREREG §5 states the inert-variant VOID rule is enforced in code (offline_calls variant_void, delta)",
          "variant_void" in pre and "delta.py" in pre.split("## 5.")[-1].split("## 6.")[0] if "## 5." in pre else False)
    sec5 = pre.split("## 5.")[-1].split("## 6.")[0] if "## 5." in pre else ""
    check("PREREG §5 names the switches the effective block reports through the product beyond the language lists "
          "(the cross-family filter, the typed-calls shapes / tests / non-production / replace) and the VOID rule for "
          "a requested switch the product left off",
          all(w in sec5 for w in ("DEEPMAP_CALLS_FAMILY_FILTER", "DEEPMAP_TYPED_CALLS_SHAPES",
                                  "DEEPMAP_TYPED_CALLS_TESTS", "DEEPMAP_TYPED_CALLS_NONPROD",
                                  "DEEPMAP_TYPED_CALLS_REPLACE", "left off")), sec5[-400:])
    # PD-1: the zod engine-specific typed tuples go the DECISION.md route, recorded before RC; E is not amended again.
    heads = re.split(r"^### ", amend, flags=re.M)[1:]           # one chunk per subsection, heading to heading
    e_amends = [h.split()[0] for h in heads if re.match(r"A\d+\b", h) and "§8.4 E" in h]
    check("PREREG: A1 stays the only amendment of §8.4 E (the zod tuples S4 found after A1 were not answered with a "
          "second amendment of E)", e_amends == ["A1"], e_amends)
    sec7 = pre.split("## 7.")[-1].split("## Amendments before RC")[0] if "## 7." in pre else ""
    check("PREREG §7 (PILOT) reports S4's near-RC engine-parity run and points to PD-1 in PRE_RC_DECISIONS.md",
          "PD-1" in sec7 and "PRE_RC_DECISIONS.md" in sec7 and "454" in sec7 and "456" in sec7, sec7[-500:])
    try:
        pd = (kit / "PRE_RC_DECISIONS.md").read_text(encoding="utf-8")
    except OSError as exc:
        pd = ""
        check("PRE_RC_DECISIONS.md exists", False, repr(exc))
    pd1 = " ".join((pd.split("## PD-1")[-1].split("\n## ")[0] if "## PD-1" in pd else "").split())
    pdn = " ".join(pd.split())                                      # whitespace-normalized: phrases span lines
    check("PRE_RC_DECISIONS.md: decisions recorded BEFORE the first RC run go the DECISION.md route (copied verbatim "
          "into DECISION.md), change no criterion or threshold, and are not decision data",
          all(w in pdn for w in ("before the first RC run", "verbatim into DECISION.md", "changes no criterion",
                                 "no threshold", "is not decision data")), pdn[:400])
    check("PD-1 records the two zod tuples with the general cause (an evidence chain through a node only one engine "
          "makes: `ZodTuple.create`, A1's generic static arrow member), the TypeScript 5.6.3 truth, the identical "
          "final CALLS edge, and that it is a list, not a rule",
          "2026-10-24" in pd1 and all(t[0].split("@")[0] in pd1 for t in PD1_TUPLES)
          and all(w in pd1 for w in ("evidence chain through a node only one engine makes", "ZodTuple.create",
                                     "TypeScript 5.6.3", "not a rule", "A1", "INFERRED", "0.8.51",
                                     "D3-js G6", "§8.4 E")), pd1[:600])
    check("run_matrix.sh materializes the STAGE-0 corpus as REGISTERED (offline_calls.py stage0-corpus, build/'s "
          "contents at the root, the whole corpus as its library code) and PREREG §3 says so",
          bool(re.search(r'stage0_build\)\s+echo "9c8d50bceab96288f83877c5306167b54b6d918a\|\|py\|\|\|stage0"', rm))
          and 'offline_calls.py" stage0-corpus' in rm and "stage0-corpus" in pre.split("## 3.")[-1].split("## 4.")[0])
    OC = mods.get("offline_calls")
    sha_ok = subprocess.run(["git", "-C", str(REPO), "cat-file", "-e", "9c8d50bceab96288f83877c5306167b54b6d918a^{commit}"],
                            capture_output=True).returncode == 0
    if OC is None or not callable(getattr(OC, "stage0_corpus", None)):
        check("offline_calls.stage0_corpus exists", False)
    elif not sha_ok:
        skip("H: materialize the STAGE-0 corpus", "commit 9c8d50bc is not in this clone (a shallow CI checkout)")
    else:
        dest = Path(tempfile.mkdtemp(prefix="dm_cl_s0c_"))
        IS = mods.get("impact_spot")
        s0_spots = list((IS.SPOTS.get("stage0_build") or []) if IS is not None else [])
        spot_src = {}
        try:
            res = OC.stage0_corpus(dest / "c")
            top = sorted(p.name for p in (dest / "c").iterdir())
            for sym in s0_spots:
                f = sym.partition("@")[2]
                if f and (dest / "c" / f).is_file():
                    spot_src[f] = (dest / "c" / f).read_text(encoding="utf-8")
        except Exception as exc:  # noqa: BLE001 — a failure is a RED check, never a crash of the section
            res, top = {"error": repr(exc)}, []
        finally:
            shutil.rmtree(dest, ignore_errors=True)
        check("offline_calls stage0-corpus: the registered self-graphify-build corpus — build/'s CONTENTS at the "
              "root (sync.py, no build/ dir), 34 files, no .git",
              res.get("corpus", {}).get("tree") == "build" and res.get("files") == 34 and "sync.py" in top
              and "build" not in top and ".git" not in top, (res, top[:8]))
        check("the STAGE-0 corpus holds a registered spot symbol (amendment A2)", bool(s0_spots), s0_spots)
        for sym in s0_spots:
            name, _, f = sym.partition("@")
            defs = re.findall(rf"^\s*def {re.escape(name.split('.')[-1])}\(", spot_src.get(f, ""), re.M)
            check(f"the STAGE-0 spot `{sym}` is defined exactly once in its file of the registered corpus",
                  bool(f) and len(defs) == 1, (f, len(defs)))
    V = mods.get("variants")
    if V is not None:
        table = re.findall(r"^\| (V[^|]*?) \| (.*?) \|$", block.split("### 8.3 Variants")[-1].split("### 8.4")[0], re.M)
        ids = [t[0].strip().replace("\u2212", "-") for t in table if t[0].strip() != "Id"]
        expanded = []
        for i in ids:
            expanded += [f"VT[{s}]" for s in V.SHAPES] if i == "VT[shape]" else \
                ["VT+t", "VT+np"] if i == "VT+t / VT+np" else [i]
        check("variants.py spells every §8.3 variant and no other", sorted(expanded) == sorted(V.VARIANTS),
              (sorted(expanded), sorted(V.VARIANTS)))
        want = {"V0": {}, "VF": {"DEEPMAP_CALLS_FAMILY_FILTER": "1"}, "VA": {"DEEPMAP_CALLS_AUDIT": "1"},
                "VT": {"DEEPMAP_TYPED_CALLS": "python,typescript,javascript", "DEEPMAP_CALLS_AUDIT": "1"},
                "VP": {"DEEPMAP_PRECISE_CALLS": "python,js"}}
        want["VT-a"] = dict(want["VT"], DEEPMAP_CALLS_AUDIT="0")
        want["VPA"] = dict(want["VP"], DEEPMAP_CALLS_AUDIT="1")
        want["VPT"] = dict(want["VP"], **want["VT"])
        want["V9"] = dict(want["VPT"], DEEPMAP_GRAPHIFY_CALLS_PLUS="1")
        want["VT+t"] = dict(want["VT"], DEEPMAP_TYPED_CALLS_TESTS="1")
        want["VT+np"] = dict(want["VT"], DEEPMAP_TYPED_CALLS_NONPROD="1")
        for s_ in V.SHAPES:
            want[f"VT[{s_}]"] = dict(want["VT"], DEEPMAP_TYPED_CALLS_SHAPES=s_)
        got_f = {k: v for k, v in V.VARIANTS.items() if v is not None}
        check("variants.py's flags are §8.3's (VT = TYPED wave 1 + AUDIT; VT-a pins AUDIT=0; VP = PRECISE "
              "python,js; V9 = VPT + CALLS_PLUS; one SHAPES arm per shape)", got_f == want and V.VARIANTS["VRC"] is None,
              {k: (got_f.get(k), want.get(k)) for k in set(got_f) | set(want) if got_f.get(k) != want.get(k)})
    check("PREREG labels the S0 numbers PILOT and not decision data", "PILOT" in pre and "NOT decision data" in pre)
    check("PREREG and run_matrix.sh pin the engine (an ast fallback is VOID: --expect-engine graphify)",
          "--expect-engine graphify" in pre and "--expect-engine graphify" in rm)
    try:
        gt = json.loads((kit / "gt_self65.json").read_text(encoding="utf-8"))
        qs = gt["queries"]
        ok = (len(qs) == 65 and len({q["id"] for q in qs}) == 65 and gt["_provenance"]["queries"] == 65
              and all(q.get("q") and q.get("expected") and q.get("set") in ("A", "B") for q in qs)
              and len(gt["_provenance"].get("original_sha256", "")) == 64)
    except Exception as exc:  # noqa: BLE001
        ok, qs = False, repr(exc)
    check("gt_self65.json: 65 unique queries (sets A/B) with expected symbols and a provenance block", ok,
          qs if not ok else "")


# =====================================================================================================
# I — the truth tools
# =====================================================================================================
TS_FIXTURE = {
    "repo.ts": "export class Repo {\n  find(id: number) { return id; }\n}\n",
    "service.ts": ("import { Repo } from './repo';\n\nexport class Service {\n  constructor(private repo: Repo) {}\n"
                   "  run() {\n    this.helper();\n    return this.repo.find(1);\n  }\n  helper() { return new Repo(); }\n}\n"),
}


def gate_i(mods: dict, kit: Path, base: Path) -> None:
    print("\n-- I: the truth tools --")
    TJ = mods.get("truth_jedi")
    if TJ is None:
        check("truth_jedi imports without jedi", False)
        return
    src = ("import os\n\n\nclass K(Base):\n    @staticmethod\n    @other\n    def m(self):\n        self.a()\n"
           "        super().b()\n        os.path.join()\n        x.c()\n        f()\n        x[0]()\n\n"
           "    @classmethod\n    def n(cls):\n        cls.d()\n\n\ndef top():\n    y = K()\n    return K().m()\n\nmain()\n")
    v = TJ.Sites()
    v.visit(ast.parse(src))
    forms = [(c["form"], c["name"], [e[0] for e in c["defstack"]]) for c in v.calls]
    check("truth_jedi call sites: forms self / super / recv / name_attr / bare / other / cls, with their defstack",
          forms == [("self", "a", ["K.m"]), ("super", "b", ["K.m"]), ("bare", "super", ["K.m"]),
                    ("recv", "join", ["K.m"]), ("name_attr", "c", ["K.m"]), ("bare", "f", ["K.m"]),
                    ("other", None, ["K.m"]), ("cls", "d", ["K.n"]), ("bare", "K", ["top"]),
                    ("recv", "m", ["top"]), ("bare", "K", ["top"]), ("bare", "main", [])], forms)
    m_entry = v.calls[0]["defstack"][0]
    check("truth_jedi defstack carries the def line AND the first decorator line (a graph node may sit on either)",
          m_entry[1] == 7 and m_entry[2] == [5], m_entry)
    line = "    print(\"éé —\", r.f(helper()))"
    u8 = "def use(r):\n" + line + "\n"
    try:
        vu = TJ.Sites(u8.split("\n"))
        vu.visit(ast.parse(u8))
        pos = {c["name"]: c["pos"] for c in vu.calls}
        want = {"print": (2, line.index("print")), "f": (2, line.index("f(helper")), "helper": (2, line.index("helper"))}
    except TypeError as exc:
        pos, want = repr(exc), "Sites(lines)"
    check("truth_jedi gives jedi CHARACTER columns: on a call line with non-ASCII text before the call, r.f() and "
          "helper() sit at their character positions (ast's UTF-8 byte offsets would point jedi at the wrong name)",
          pos == want, (pos, want))
    jedi_py = os.environ.get("CALL_LINKING_JEDI_PY", "")
    if not (jedi_py and Path(jedi_py).is_file()):
        skip("I: truth_jedi end to end (jedi 0.20.1)", "set CALL_LINKING_JEDI_PY to a jedi 0.20.1 venv's python")
    else:
        jc = base / "jedi_enc"
        jc.mkdir(parents=True, exist_ok=True)
        (jc / "mod.py").write_text("class Repo:\n    def f(self, x):\n        return x\n\n\ndef helper():\n    return 1\n\n\n"
                                   "def use(r: Repo):\n" + line + "\n", encoding="utf-8")
        (jc / "bom.py").write_bytes("﻿def g():\r\n    return 2\r\n\r\n\r\ndef call_g():\r\n    return g()\r\n"
                                    .encode("utf-8"))
        (jc / "lat.py").write_bytes("# -*- coding: latin-1 -*-\ndef h():\n    return 3\n\n\ndef call_h():\n"
                                    "    s = \"é\"; return h()\n".encode("latin-1"))
        (jc / "broken.py").write_text("def x(:\n", encoding="utf-8")
        out = base / "jedi_enc.json"
        p = subprocess.run([jedi_py, "-I", str(kit / "truth_jedi.py"), str(jc), str(out)], capture_output=True,
                           text=True, timeout=600)
        if p.returncode != 0 or not out.is_file():
            check("I: truth_jedi runs on the encoding fixture", False, p.stderr[-400:])
        else:
            tj = json.loads(out.read_text(encoding="utf-8"))
            res = sorted((r["file"], r["name"], tuple((d["file"], d["line"]) for d in r["defs"])) for r in tj["rows"])
            check("I: truth_jedi resolves a call after non-ASCII text, in a BOM + CRLF file and in a PEP 263 latin-1 "
                  "file; only the syntax-error file is a parse error, named",
                  res == sorted([("mod.py", "print", ()), ("mod.py", "f", (("mod.py", 2),)),
                                 ("mod.py", "helper", (("mod.py", 6),)), ("bom.py", "g", (("bom.py", 1),)),
                                 ("lat.py", "h", (("lat.py", 2),))])
                  and tj.get("parse_errors") == ["broken.py: SyntaxError"], (res, tj.get("parse_errors")))
    tsenv = os.environ.get("CALL_LINKING_TSENV", "")
    node = shutil.which("node")
    if not (tsenv and node):
        skip("I: truth_ts.js on a TS fixture", "set CALL_LINKING_TSENV to a dir holding node_modules/typescript@5.6.3")
        return
    proj = base / "tsproj"
    (proj / "src").mkdir(parents=True)
    for rel, text in TS_FIXTURE.items():
        (proj / "src" / rel).write_text(text, encoding="utf-8")
    out = base / "ts_truth.json"
    p = subprocess.run([node, str(kit / "truth_ts.js"), tsenv, str(proj), "src", str(out)], capture_output=True,
                       text=True, timeout=600)
    if p.returncode != 0:
        check("truth_ts.js runs on the fixture", False, p.stderr[-400:])
        return
    rows = json.loads(out.read_text(encoding="utf-8"))["rows"]
    got = sorted((r["file"], r["form"], r["name"], tuple((d["file"], d["line"], d["type"]) for d in r["defs"]))
                 for r in rows)
    check("truth_ts.js: this.helper() / this.repo.find() (typed through the constructor parameter property) / "
          "new Repo() resolve to their declarations",
          got == sorted([("src/service.ts", "this", "helper", (("src/service.ts", 9, "function"),)),
                         ("src/service.ts", "recv", "find", (("src/repo.ts", 2, "function"),)),
                         ("src/service.ts", "ctor", "Repo", (("src/repo.ts", 1, "class"),))]), got)


# =====================================================================================================
# J — PREREG_CS.md (D3-cs): the protocol block hash-locked, its tables equal to the kit's
# =====================================================================================================
def prereg_cs_block(text: str) -> "tuple[str | None, str | None, str]":
    """(block, None, head) — the lines strictly between the ONE line starting `<!-- PREREG-CS-PROTOCOL BEGIN` and the
    ONE line starting `<!-- PREREG-CS-PROTOCOL END`, joined with LF plus one final LF (section H's rule for PREREG
    §8), and the text above the BEGIN line — or (None, why, head). A marker counts only as a whole line's start: the
    Rules paragraph quotes both markers inside a sentence."""
    lines = text.split("\n")
    b = [i for i, ln in enumerate(lines) if ln.startswith("<!-- PREREG-CS-PROTOCOL BEGIN")]
    e = [i for i, ln in enumerate(lines) if ln.startswith("<!-- PREREG-CS-PROTOCOL END")]
    head = "\n".join(lines[:b[0]]) + "\n" if b else text
    if len(b) != 1 or len(e) != 1 or b[0] >= e[0]:
        return (None, f"want exactly one BEGIN marker line before exactly one END marker line (BEGIN at {b}, END at "
                      f"{e})", head)
    return "\n".join(lines[b[0] + 1:e[0]]) + "\n", None, head


def prereg_cs_amendments(text: str) -> "tuple[str | None, str | None]":
    """(section, None) — PREREG_CS's 'Amendments before RC': the lines strictly between the ONE heading line
    `## Amendments before RC` and the ONE line starting `<!-- PREREG-CS-PROTOCOL BEGIN`, joined with LF plus one final
    LF (the block's rule) — or (None, why)."""
    lines = text.split("\n")
    h = [i for i, ln in enumerate(lines) if ln == "## Amendments before RC"]
    b = [i for i, ln in enumerate(lines) if ln.startswith("<!-- PREREG-CS-PROTOCOL BEGIN")]
    if len(h) != 1 or len(b) != 1 or h[0] >= b[0]:
        return None, f"want exactly one `## Amendments before RC` heading line before exactly one BEGIN marker line " \
                     f"(heading at {h}, BEGIN at {b})"
    return "\n".join(lines[h[0] + 1:b[0]]) + "\n", None


def _sh_joined(rm: str) -> str:
    """run_matrix.sh with its `\\`-continued lines joined."""
    return re.sub(r"\\\n\s*", "", rm)


def _sh_function(rm: str, name: str) -> str:
    """The body of a bash function `name() {` ... `\\n}` of run_matrix.sh, its `\\`-continued lines joined."""
    rm = _sh_joined(rm)
    if f"\n{name}() {{" not in rm:
        return ""
    return rm.split(f"\n{name}() {{", 1)[1].split("\n}", 1)[0]


def gate_j_amendments(mods: dict, kit: Path, block: str, amend: str) -> None:
    """The amendments' kit-facing rules, read from their text and equal to the kit's: AC13's engine-parity command
    (== run_matrix.sh's cs_engine_parity, the only caller), AC12's final spot list (every C# spot command of
    run_matrix.sh), G5a's gate list (== run_matrix.sh's CS_G5A_GATES; each gate exists), AC14's N3 gate (the push
    gate's csharp identity section), AC16's VRC-cs flags (== variants.vrc_cs_flag on this tree)."""
    flat = " ".join(amend.split())
    rm = (kit / "run_matrix.sh").read_text(encoding="utf-8") if (kit / "run_matrix.sh").is_file() else ""
    m = re.search(r"The gate command is `python tests/typed_calls_engine_parity_gates\.py --corpora --only (\S+) "
                  r"--py39 <dev python> --py51 (\S+)`", flat)
    only, py51 = (m.group(1), m.group(2)) if m else (None, None)
    ce = re.search(r"\| Engine, CI end \| graphifyy 0\.8\.51 in the uv venv `([^`]+)` \(`Scripts/python\.exe`", block)
    check("AC13 names the engine-parity gate command: --corpora --only the two C# corpora (jellyfin__jellyfin,"
          "dotnet__eShop, §8.2's ids) --py39 <dev python> --py51 the §8.2 CI end's python",
          only == ",".join(CS_CORPUS_IDS.values()) and ce is not None and py51 == f"{ce.group(1)}/Scripts/python.exe",
          (only, py51, ce.group(1) if ce else None))
    body = _sh_function(rm, "cs_engine_parity")
    calls = [ln.strip() for ln in body.splitlines() if "typed_calls_engine_parity_gates.py" in ln]
    want = ('env CALL_LINKING_CS_SOURCES="$CS_SOURCES" "$PY39" "$REPO/tests/typed_calls_engine_parity_gates.py" '
            f'--corpora --only {only} --py39 "$PY39" --py51 "$PY51"')
    elsewhere = [ln for ln in _sh_joined(rm).replace(body, "").splitlines()
                 if "typed_calls_engine_parity_gates.py" in ln and not ln.lstrip().startswith("#")]
    check("run_matrix.sh runs the engine-parity gate's C# corpora EXACTLY as AC13 names it (cs_engine_parity: one "
          "command, the dry-run plan and the run alike through run_log, CALL_LINKING_CS_SOURCES = §8.2's read-only "
          "root, the snapshot's tests/), called once from the rest phase, and from nowhere else",
          len(calls) == 1 and all(c.endswith(want) for c in calls) and not elsewhere
          and len(re.findall(r"^\s*cs_engine_parity ;;", rm, re.M)) == 1, (calls, want, elsewhere))
    rl = _sh_function(rm, "run_log")
    rl_lines = [ln.strip() for ln in rl.splitlines() if ln.strip() and not ln.strip().startswith("#")]

    def at(s):
        return rl_lines.index(s) if s in rl_lines else -1
    i_run = at('"$@" > "$f.partial" 2>&1; rc=$?')
    i_rc = at('echo "[run_matrix] exit $rc" >> "$f.partial"')
    i_mv = at('mv "$f.partial" "$f"')
    i_dry = at("""if [ -n "$DRY" ]; then { printf '%q ' "$@"; printf '> %q 2>&1; mv %q %q\\n' "$f.partial" """
               """"$f.partial" "$f"; } >&2; return 0; fi""")
    check("AC29 (C1 to C4 come from the command's FULL log, out/engine_parity_cs.log): cs_engine_parity writes the "
          "gate's stdout AND stderr to exactly \"$OUT/engine_parity_cs.log\" through run_log — `> <log>.partial 2>&1`, "
          "the exit code appended, then renamed to <log> (a killed run never leaves a log that reads complete); a dry "
          "run prints that same redirect; the present log is never re-run and the RESULT line is read from it",
          re.search(r'^\s*local lg="\$OUT/engine_parity_cs\.log" rc$', body, re.M) is not None
          and calls == ['run_log "$lg" ' + want]
          and 'if [ -z "$DRY" ] && [ -f "$lg" ]; then' in body and "grep -h '^RESULT:' \"$lg\"" in body
          and rl_lines[:1] == ['local f="$1" rc; shift'] and 0 <= i_dry < i_run < i_rc < i_mv
          and rl_lines[i_mv + 1:] == ["return $rc"],
          {"calls": calls, "run_log": rl_lines})
    sc = _sh_function(rm, "cs_score")
    spots = [ln.strip() for ln in sc.splitlines() if "impact_spot.py" in ln]
    check("AC12: every spot command run_matrix.sh runs on a C# corpus (cs_score) measures its FINAL list — "
          "--corpus=<id> --final=<id>/spots_final.json — and the spots phase writes that file with `impact_spot.py "
          "substitute` over both engines' V0 dumps",
          bool(spots) and all('"--corpus=$c" "--final=$fin"' in ln for ln in spots)
          and 'fin="$OUT/$1/spots_final.json"' in sc
          and bool(re.search(r'impact_spot\.py" substitute "--truth=\S+" "--corpus=\$c"\s+"--base39=\S+_V0_g39\.json" '
                             r'"--base51=\S+_V0_g51\.json" "--json=\$OUT/\$c/spots_final\.json"',
                             _sh_joined(rm))), spots)
    sec = block.split("- **G5a:**", 1)[-1].split("The full no-Neo4j suite", 1)[0] if "- **G5a:**" in block else ""
    g5a = re.findall(r"\b([a-z0-9_]+_gates)\b", sec)
    rmg = re.search(r'^CS_G5A_GATES="([^"]*)"', rm, re.M)
    missing = [g for g in g5a if not (TESTS / f"{g}.py").is_file()]
    check(f"G5a's gate list (§8.7, {len(g5a)} gates plus the N3 gate) == run_matrix.sh's CS_G5A_GATES, in order; every "
          f"listed gate exists under tests/ (a listed gate that does not exist at RC does not pass)",
          len(g5a) == 14 and "the N3 gate" in sec and rmg is not None and rmg.group(1).split() == g5a and not missing,
          {"prereg": g5a, "run_matrix": rmg.group(1).split() if rmg else None, "missing": missing})
    push = (TESTS / "typed_calls_push_gates.py").read_text(encoding="utf-8") if (
        TESTS / "typed_calls_push_gates.py").is_file() else ""
    runs_i = re.search(r"for sec in \(([^)]*)\):", push)
    check("AC14: the N3 gate is typed_calls_push_gates.py's csharp identity section (it runs: section I, "
          "DEEPMAP_TYPED_CALLS=csharp == flags off on its corpus) plus typed_calls_cs_gates.py section G, both G5a gates",
          "typed_calls_push_gates.py`'s csharp identity section" in flat and "typed_calls_cs_gates.py` section G" in flat
          and runs_i is not None and "sec_i" in runs_i.group(1) and "def sec_i" in push and "AC14" in push
          and {"typed_calls_push_gates", "typed_calls_cs_gates"} <= set(g5a),
          (runs_i.group(1) if runs_i else None))
    V = mods.get("variants")
    langs, why = typed_default_langs(BUILD)
    m16 = re.search(r"The flags are `--set DEEPMAP_TYPED_CALLS=<every language in the RC tree's _TYPED_DEFAULT_LANGS>,"
                    r"csharp`", flat)
    try:
        flag = V.vrc_cs_flag(BUILD) if V is not None else None
    except Exception as exc:  # noqa: BLE001
        flag = f"{type(exc).__name__}: {exc}"
    want16 = ",".join([x for x in (langs or []) if x != CS_LANG] + [CS_LANG]) if langs is not None else None
    check("AC16: VRC-cs is `--set DEEPMAP_TYPED_CALLS=<the RC tree's defaults>,csharp` (§8.4's table sets it with "
          "--set) — variants.CS_VARIANTS['VRC-cs'] is set at decision time, and variants.vrc_cs_flag on this tree is "
          f"that value ({want16})", m16 is not None and V is not None and V.CS_VARIANTS.get("VRC-cs", 0) is None
          and flag == want16 and why is None, (m16 is not None, flag, want16, why))


def _cells(line: str) -> list:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _table_rows(text: str, first: str) -> list:
    """The cells of every markdown table row whose first cell matches the regex `first` (in order)."""
    return [_cells(ln) for ln in text.splitlines() if ln.startswith("|") and re.fullmatch(first, _cells(ln)[0])]


def _ticks(cell: str) -> list:
    return re.findall(r"`([^`]+)`", cell)


def cs_corpora_of_prereg(block: str) -> dict:
    """§8.2's C# corpora table: {corpus id: {sha, source, prefixes}} (the prefixes are the backticked directories
    before the parenthetical, in order)."""
    out = {}
    for c in _table_rows(block, r"jellyfin__jellyfin|dotnet__eShop"):
        if len(c) < 6:
            continue
        sha = re.search(r"\b[0-9a-f]{40}\b", c[2])
        out[c[0]] = {"sha": sha.group(0) if sha else None, "source": (_ticks(c[3]) or [None])[0],
                     "prefixes": _ticks(c[4].split("(")[0])}
    return out


def cs_corpora_of_run_matrix(rm: str) -> "tuple[dict, str | None]":
    """run_matrix.sh's cs_corpus_row (sha | prefixes | truth kind | source dir | origin) and CL_CS_SOURCES' default."""
    body = rm.split("cs_corpus_row() {", 1)[1].split("\n}", 1)[0] if "cs_corpus_row() {" in rm else ""
    rows = {m.group(1): m.group(2).split("|")
            for m in re.finditer(r'^\s*([\w.\-]+__[\w.\-]+)\)\s+printf\s+\S+\s+"([^"]*)"', body, re.M)}
    src = re.search(r"CL_CS_SOURCES:-([^}\"]+)\}", rm)
    return rows, (src.group(1) if src else None)


def literal_assignments(path: Path, names) -> dict:
    """{name: value} of the module-level `name = <literal>` assignments of a Python file, read from its AST (the file
    is never imported)."""
    out = {}
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return out
    for n in tree.body:
        tg = n.targets if isinstance(n, ast.Assign) else [n.target] if isinstance(n, ast.AnnAssign) else []
        for t in tg:
            if isinstance(t, ast.Name) and t.id in names and n.value is not None:
                try:
                    out[t.id] = ast.literal_eval(n.value)
                except ValueError:
                    out[t.id] = "<not a literal>"
    return out


def cs_spots_of_prereg(block: str) -> "tuple[dict, dict]":
    """§8.3's spot table and reserve table: ({corpus id: [(id, "Name@file")]}, the same for the reserves), in order;
    the file is the Definition cell's first backticked `path:line`."""
    sec = block.split("### 8.3")[-1].split("### 8.4")[0] if "### 8.3" in block else ""

    def rows(pat):
        out = {}
        for c in _table_rows(sec, pat):
            if len(c) < 4:
                continue
            m = re.search(r"`([^`]+?):\d+`", c[3])
            out.setdefault(CS_CORPUS_IDS.get(c[2], c[2]), []).append(
                (c[0], f"{c[1].strip('`')}@{m.group(1) if m else '?'}"))
        return out
    return rows(r"[JE][1-9]"), rows(r"[JE]-r[1-9]")


def cs_variants_of_prereg(block: str) -> "tuple[dict | None, list, str | None]":
    """§8.4's table as {id: flags} (VT-cs[shape] expanded over the shapes it lists, `VT-cs+t / VT-cs+np` split; V0 {},
    VRC-cs None — its flags are set at decision time with --set), the shapes, and a parse problem."""
    sec = block.split("### 8.4 Variants")[-1].split("### 8.5")[0] if "### 8.4 Variants" in block else ""
    out, shapes = {}, []
    for ids, cell, _role in re.findall(r"^\| (V[^|]*?) \| (.*?) \| (.*?) \|$", sec, re.M):
        names = [x.strip() for x in ids.split(" / ")]
        kvs = re.findall(r"`(DEEPMAP_[A-Z0-9_]+)=([^`]*)`", cell)
        base = dict(out.get("VT-cs") or {}) if cell.startswith("VT-cs +") else {}
        if names == ["V0"]:
            if kvs or "default" not in cell:
                return None, shapes, f"V0's row names flags: {cell}"
            out["V0"] = {}
        elif names == ["VRC-cs"]:
            if "--set" not in cell:
                return None, shapes, f"VRC-cs's row does not say it is set with --set: {cell}"
            out["VRC-cs"] = None
        elif names[0].endswith("[shape]"):
            m = re.search(r"\(([A-Z0-9 ]+)\)", cell)
            shapes = m.group(1).split() if m else []
            for s in shapes:
                out[names[0].replace("[shape]", f"[{s}]")] = dict(base, **{k: (s if v == "<shape>" else v) for k, v in kvs})
        elif len(names) > 1:
            if len(names) != len(kvs):
                return None, shapes, f"{ids}: {len(names)} ids, {len(kvs)} flags"
            for nm, (k, v) in zip(names, kvs):
                out[nm] = dict(base, **{k: v})
        else:
            out[names[0]] = dict(base, **dict(kvs))
    return out, shapes, None


def gate_j(mods: dict, kit: Path) -> None:
    print("\n-- J: PREREG_CS.md (D3-cs): the protocol block verbatim (sha256-locked), its tables == the kit's --")
    try:
        pre = (kit / "PREREG_CS.md").read_text(encoding="utf-8")
    except OSError as exc:
        check("PREREG_CS.md exists", False, repr(exc))
        return
    block, why, head = prereg_cs_block(pre)
    got = hashlib.sha256(block.encode("utf-8")).hexdigest() if block is not None else None
    check("PREREG_CS's protocol block (the lines strictly between its one BEGIN and one END marker line, LF-joined "
          "plus a final LF) is the registered one verbatim (sha256 locked before the first RC run: thresholds and "
          "rules never move after data)",
          block is not None and got == PREREG_CS_PROTOCOL_SHA256
          and block.startswith("## 8. Measurement protocol (D3-cs)\n"), why or got)
    block = block or ""
    amend_ok = ("## Amendments before RC" in head and "## 7." in head
                and head.rindex("## Amendments before RC") > head.rindex("## 7."))
    check("PREREG_CS has its 'Amendments before RC' section just above the protocol block, and its rules name this "
          "gate as the lock", amend_ok and "tests/call_linking_harness_gates.py" in head)
    amend, why_a = prereg_cs_amendments(pre)
    got_a = hashlib.sha256(amend.encode("utf-8")).hexdigest() if amend is not None else None
    check("PREREG_CS's 'Amendments before RC' (the lines strictly between its one `## Amendments before RC` heading "
          "line and the BEGIN marker line, LF-joined plus a final LF) is the dated AC1-AC32 text verbatim (sha256 "
          "locked beside the block's before step 1: an amendment governs the items it names, so it never moves after "
          "data either)", amend is not None and got_a == PREREG_CS_AMENDMENTS_SHA256
          and [int(n) for n in re.findall(r"^- \*\*AC(\d+)\.", amend, re.M)] == list(range(1, 33)), why_a or got_a)
    amend = amend or ""
    gate_j_amendments(mods, kit, block, amend)
    sec1 = head.split("## 1.")[-1].split("## 2.")[0] if "## 1." in head else ""
    check("PREREG_CS §1 holds the RC identity slots (the build/*.py content hash by `offline_calls.py codehash`, the "
          "kit hash, the reference tool identity) and the frozen-snapshot rule",
          all(w in sec1 for w in ("Content hash of build/\\*.py", "offline_calls.py codehash", "Kit hash",
                                  "Reference tool identity", "frozen snapshot")), sec1[:300])
    rm = (kit / "run_matrix.sh").read_text(encoding="utf-8") if (kit / "run_matrix.sh").is_file() else ""
    want = cs_corpora_of_prereg(block)
    rows, src_default = cs_corpora_of_run_matrix(rm)
    check("§8.2 pins exactly the two C# corpora, each with a SHA, a read-only source and library prefixes",
          sorted(want) == sorted(CS_CORPUS_IDS.values())
          and all(w["sha"] and w["source"] and w["prefixes"] for w in want.values()), want)
    got_rm = {c: {"sha": r[0], "prefixes": [x for x in r[1].split(",") if x],
                  "source": f"{src_default}/{r[3]}" if src_default else None, "kind": r[2], "origin": r[4]}
              for c, r in rows.items() if len(r) == 5}
    want_rm = {c: dict(w, kind="roslyn", origin="readonly") for c, w in want.items()}
    check("run_matrix.sh's cs_corpus_row == PREREG_CS §8.2 for both C# corpora: the pinned SHA, the library prefixes "
          "(in order), the read-only source (CL_CS_SOURCES' default + its directory), a Roslyn truth, read-only origin",
          {c: got_rm.get(c) for c in want} == want_rm, {c: (got_rm.get(c), want_rm[c]) for c in want
                                                        if got_rm.get(c) != want_rm[c]})
    sec3 = head.split("## 3.")[-1].split("## 4.")[0] if "## 3." in head else ""
    s3 = {c[0]: (re.findall(r"\b[0-9a-f]{40}\b", c[1]) or [None])[0]
          for c in _table_rows(sec3, r"jellyfin/jellyfin|dotnet/eShop")}
    recreate = set(re.findall(r"\b[0-9a-f]{40}\b", block.split("To re-create a read-only source")[-1].split("**The N")[0]))
    shas = {w["sha"] for w in want.values()}
    check("§3's corpus table and §8.2's re-create commands pin the same two SHAs as §8.2's table",
          {s3.get("jellyfin/jellyfin"), s3.get("dotnet/eShop")} == shas and recreate == shas, (s3, sorted(recreate)))
    h_rows = dict(re.findall(r"^\s*([\w\-]+__[\w.\-]+|stage0_build)\)\s+echo \"([0-9a-f]{40})\|", rm, re.M))
    check("the C# corpora are apart from corpus_row, the table section H reads for PREREG (neither id nor SHA in it)",
          not (set(h_rows) & set(want)) and not (set(h_rows.values()) & shas), sorted(set(h_rows) & set(want)))
    pg = literal_assignments(TESTS / "typed_calls_engine_parity_gates.py", {"CS_CORPORA", "CS_SOURCES_DEFAULT"})
    got_pg = {c: {"sha": v[0], "source": f"{pg.get('CS_SOURCES_DEFAULT')}/{v[1]}"}
              for c, v in (pg.get("CS_CORPORA") or {}).items()} if isinstance(pg.get("CS_CORPORA"), dict) else None
    check("the engine-parity gate's --corpora C# table (tests/typed_calls_engine_parity_gates.py CS_CORPORA, "
          "CS_SOURCES_DEFAULT) == PREREG_CS §8.2 (SHA and read-only source; §8.6 E)",
          got_pg == {c: {"sha": w["sha"], "source": w["source"]} for c, w in want.items()}, (got_pg, pg))
    check("run_matrix.sh's audit draw is §8.6 A's (seed 20261022, n 40)",
          bool(re.search(r"^CS_SEED=20261022$", rm, re.M)) and bool(re.search(r"^CS_AUDIT_N=40$", rm, re.M))
          and "--seed 20261022 --n 40" in block)
    IS = mods.get("impact_spot")
    spots, reserves = cs_spots_of_prereg(block)
    if IS is None or not hasattr(IS, "CS_SPOTS"):
        check("impact_spot.py has the C# spot tables (CS_SPOTS, CS_RESERVES, CS_IDS)", False)
    else:
        check("§8.3 registers 10 spots (J1-J5, E1-E5) and 8 reserves (J-r1..J-r5, E-r1..E-r3)",
              [len(spots.get(c, [])) for c in CS_CORPUS_IDS.values()] == [5, 5]
              and [len(reserves.get(c, [])) for c in CS_CORPUS_IDS.values()] == [5, 3],
              (spots, reserves))
        for label, table, mine in (("spots", spots, IS.CS_SPOTS), ("reserves (in substitution order)", reserves,
                                                                   IS.CS_RESERVES)):
            want_t = {c: [s for _i, s in v] for c, v in table.items()}
            check(f"impact_spot.py's C# {label} == PREREG_CS §8.3's, `Name@file` with the Definition's file, in order",
                  {c: list(v) for c, v in mine.items()} == want_t,
                  {c: (mine.get(c), want_t.get(c)) for c in set(mine) | set(want_t) if list(mine.get(c) or []) !=
                   want_t.get(c)})
        ids = {s: i for v in list(spots.values()) + list(reserves.values()) for i, s in v}
        check("impact_spot.CS_IDS gives each C# spot and reserve its PREREG_CS id (J1.., E-r1..)",
              dict(IS.CS_IDS) == ids, {s: (IS.CS_IDS.get(s), i) for s, i in ids.items() if IS.CS_IDS.get(s) != i})
        sec4 = head.split("## 4.")[-1].split("## 5.")[0] if "## 4." in head else ""
        s4 = {CS_CORPUS_IDS[m.group(1)]: _ticks(m.group(2))
              for m in re.finditer(r"^- \*\*(jellyfin|eShop):\*\* (.*(?:\n  .*)*)", sec4, re.M)}
        check("PREREG_CS §4 names the same five spot symbols per corpus as §8.3, in order",
              s4 == {c: [s.split("@")[0] for _i, s in v] for c, v in spots.items()}, s4)
        flat = {s for v in getattr(IS, "SPOTS", {}).values() for s in v}
        check("the C# spots are apart from impact_spot.SPOTS (the table section H locks to PREREG.md)",
              not (flat & set(ids)) and not (set(getattr(IS, "SPOTS", {})) & set(CS_CORPUS_IDS.values())))
    V = mods.get("variants")
    table, shapes, why = cs_variants_of_prereg(block)
    if V is None or not hasattr(V, "CS_VARIANTS"):
        check("variants.py has the D3-cs table (CS_VARIANTS)", False)
    else:
        check("variants.py's CS_VARIANTS == PREREG_CS §8.4's table: the same ids in the same order with the same flags "
              "(VT-cs = DEEPMAP_TYPED_CALLS=csharp, no audit flag; one SHAPES arm per shape; +t / +np; -ifs = IFACE "
              "static; -if0 = IFACE 0; VRC-cs set with --set)",
              table is not None and list(table) == list(V.CS_VARIANTS) and table == V.CS_VARIANTS,
              why or {k: (V.CS_VARIANTS.get(k), (table or {}).get(k)) for k in set(V.CS_VARIANTS) | set(table or {})
                      if V.CS_VARIANTS.get(k) != (table or {}).get(k)} or (list(table or {}), list(V.CS_VARIANTS)))
        prod = literal_assignments(BUILD / "typed_calls.py", {"SHAPES"}).get("SHAPES")
        check("the shapes PREREG_CS §8.4 lists == variants.CS_SHAPES == the product's typed_calls.SHAPES",
              list(shapes) == list(V.CS_SHAPES) == list(prod or ()), (shapes, V.CS_SHAPES, prod))
        check("the D3-cs variants are apart from §8.3's VARIANTS (only V0 is in both; section H locks VARIANTS to "
              "PREREG.md)", set(V.CS_VARIANTS) & set(V.VARIANTS) == {"V0"}, sorted(set(V.CS_VARIANTS) & set(V.VARIANTS)))
    C = mods.get("_common")
    m = re.search(r"\*\*Receiver forms for G1 \(cs\):\*\* ((?:`\w+`(?: \+ )?)+)", block)
    forms = tuple(_ticks(m.group(1))) if m else ()
    check("_common.RECEIVER_FORMS['cs'] == PREREG_CS §8.5's receiver forms for G1 (name_attr + recv + this)",
          C is not None and forms == ("name_attr", "recv", "this") and tuple(C.RECEIVER_FORMS.get("cs") or ()) == forms,
          (forms, C.RECEIVER_FORMS.get("cs") if C is not None else None))
    AS = mods.get("audit_sample")
    m = re.search(r'\*\*The C# sheet adds one instruction, fixed now:\*\* "(.*?)"', block, re.S)
    sentence = " ".join(m.group(1).split()) if m else None
    # AC25: "The instruction gains one sentence: "..."" — read from AC25's own bullet (never a second copy here)
    a25 = " ".join(amend.split("- **AC25.", 1)[-1].split("- **AC26.", 1)[0].split()) if "- **AC25." in amend else ""
    m25 = re.search(r'The instruction gains one sentence: "(.*?)"', a25)
    ac25 = m25.group(1) if m25 else None
    want_sheet = f"{sentence} {ac25}" if sentence and ac25 else None
    check("audit_sample.CS_SHEET_SENTENCE is PREREG_CS §8.6 A's C# sheet instruction followed by the one sentence AC25 "
          "adds to it, both word for word (read from PREREG_CS.md: the block's instruction, AC25's quoted sentence)",
          AS is not None and want_sheet is not None
          and " ".join(str(getattr(AS, "CS_SHEET_SENTENCE", "")).split()) == want_sheet,
          (want_sheet, getattr(AS, "CS_SHEET_SENTENCE", None)))
    # §8.2's N corpora (MX23): the bullets name 13 corpora; each maps to ONE corpus_row id (a SHA by that SHA, a
    # backticked id as itself, a short name by its '-'-separated words, all among the id's words); in that order they
    # are run_matrix.sh's CS_N_CORPORA, and exactly corpus_row's corpora (PREREG.md §3's, which section H locks)
    nsec = block.split("**The N corpora (non-C# identity).**", 1)[-1].split("**Retrieval ground truth.**", 1)[0] \
        if "**The N corpora (non-C# identity).**" in block else ""
    crow = {i: f.split("|")[0] for i, f in re.findall(r'^\s*([\w.\-]+)\)\s+echo "([^"]*)"', _sh_function(rm, "corpus_row"),
                                                     re.M)}
    derived, unmapped = [], []
    for b in [ln[2:].strip().rstrip(";.") for ln in nsec.splitlines() if ln.startswith("- ")]:
        sha, tick = re.search(r"\b[0-9a-f]{40}\b", b), _ticks(b)
        if sha:
            names, hits = [sha.group(0)], [[i for i, s in crow.items() if s == sha.group(0)]]
        elif tick:
            names, hits = tick, [[i for i in crow if i == t] for t in tick]
        else:
            inner = re.search(r"\(([^)]*)\)", b)
            names = [x for x in re.split(r",\s*|\s+and\s+", inner.group(1) if inner else b) if x]
            hits = [[i for i in crow if set(x.split("-")) <= set(re.split(r"[-_]+", i))] for x in names]
        for x, h in zip(names, hits):
            (derived.append(h[0]) if len(h) == 1 else unmapped.append((x, h)))
    rmn = re.search(r'^CS_N_CORPORA="([^"]*)"', rm, re.M)
    check("PREREG_CS §8.2's N corpora (its 13 bullets' corpora, in order: the five Python ones, stage0_build, zod, "
          "nestjs-realworld, node-express-boilerplate, gin, httprouter, serde-json, self) == run_matrix.sh's "
          "CS_N_CORPORA, and they are exactly corpus_row's corpora (PREREG.md §3's)",
          "That is 13 corpora." in nsec and not unmapped and len(derived) == 13 and rmn is not None
          and rmn.group(1).split() == derived and sorted(derived) == sorted(crow),
          {"derived": derived, "unmapped": unmapped, "run_matrix": rmn.group(1).split() if rmn else None,
           "corpus_row": sorted(crow)})


# =====================================================================================================
# K — the G5b flip guard (PREREG_CS §8.7): csharp never becomes a default without the committed effect diff
# =====================================================================================================
def _load_registered(path: Path, name: str):
    """A module loaded by path under `name` (registered in sys.modules while it executes: dataclasses read it), its
    directory first on sys.path; both restored after."""
    saved = list(sys.path)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        sys.path.insert(0, str(path.parent))
        so, se = _quiet()
        with so, se:
            spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path[:] = saved
        sys.modules.pop(name, None)


def typed_default_langs(build_dir: Path) -> "tuple[list | None, str | None]":
    """(the product's typed_calls._TYPED_DEFAULT_LANGS, lower-cased and sorted, or None, why not) — imported from
    `build_dir`, so whatever expression defines it is what counts."""
    p = build_dir / "typed_calls.py"
    try:
        mod = _load_registered(p, f"_cl_cs_typed_calls_{time.time_ns()}")
        return sorted({str(x).strip().lower() for x in mod._TYPED_DEFAULT_LANGS}), None
    except BaseException as exc:  # noqa: BLE001 — unreadable means the guard cannot clear the tree (fail closed)
        return None, f"cannot read {p.name}'s _TYPED_DEFAULT_LANGS: {type(exc).__name__}: {exc}"[:400]


def flip_guard(build_dir: Path, kit_dir: Path) -> dict:
    """PREREG_CS §8.7 G5b's guard. Inactive (no problem) while `csharp` is not in build_dir's
    typed_calls._TYPED_DEFAULT_LANGS. Active, it requires kit_dir/cs/MANIFEST.sha256 — one `<sha256>  <relative
    path>` line per file (sha256sum's text form; the path relative to cs/, forward slashes, inside cs/, in byte order,
    each once, never the manifest itself nor a sheet.md, which is never committed) with every listed file present and
    matching byte for byte — and kit_dir/DECISION_CS.md recording the manifest's own sha256: on the lines that name
    `MANIFEST.sha256`, exactly one distinct 64-hex value, equal to it. An unreadable defaults set fails closed.
    Returns {active, defaults, manifest_sha256, entries, problems}."""
    res = {"active": None, "defaults": None, "manifest_sha256": None, "entries": 0, "problems": []}
    langs, why = typed_default_langs(build_dir)
    if langs is None:
        res["problems"].append(why)
        return res
    res["defaults"], res["active"] = langs, CS_LANG in langs
    if not res["active"]:
        return res
    probs = res["problems"]
    cs = kit_dir / "cs"
    man = cs / "MANIFEST.sha256"
    if not man.is_file():
        probs.append(f"{man.relative_to(kit_dir).as_posix()} is missing (G5b: the committed effect diff)")
    else:
        raw = man.read_bytes()
        res["manifest_sha256"] = hashlib.sha256(raw).hexdigest()
        if b"\r" in raw:
            probs.append("MANIFEST.sha256 holds CR characters (a CRLF checkout changes every byte-exact hash: "
                         "cs/ must be checked out byte for byte)")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = ""
            probs.append("MANIFEST.sha256 is not UTF-8")
        lines = text.split("\n")
        if lines and lines[-1] == "":
            lines = lines[:-1]
        paths = []
        for i, ln in enumerate(lines, 1):
            m = re.fullmatch(r"([0-9a-f]{64})  (.+)", ln.rstrip("\r"))
            if not m:
                probs.append(f"MANIFEST.sha256 line {i} is not `<sha256>  <relative path>`: {ln[:120]!r}")
                continue
            h, rel = m.group(1), m.group(2)
            parts = rel.split("/")
            if rel.startswith("/") or "\\" in rel or re.match(r"^[A-Za-z]:", rel) or any(
                    x in ("", ".", "..") for x in parts):
                probs.append(f"MANIFEST.sha256 line {i}: {rel!r} is not a forward-slash path inside cs/")
                continue
            paths.append(rel)
            if rel == "MANIFEST.sha256":
                probs.append("MANIFEST.sha256 lists itself")
            elif parts[-1].lower() == "sheet.md":
                probs.append(f"MANIFEST.sha256 lists {rel} (sheet.md is never committed: it quotes corpus source)")
            elif not (cs / rel).is_file():
                probs.append(f"cs/{rel} is listed but missing")
            elif hashlib.sha256((cs / rel).read_bytes()).hexdigest() != h:
                probs.append(f"cs/{rel} differs from its manifest sha256")
        res["entries"] = len(paths)
        if not paths:
            probs.append("MANIFEST.sha256 lists no file")
        if paths != sorted(paths, key=lambda s: s.encode("utf-8")) or len(set(paths)) != len(paths):
            probs.append("MANIFEST.sha256's paths are not each listed once in byte order")
    dec = kit_dir / "DECISION_CS.md"
    if not dec.is_file():
        probs.append("DECISION_CS.md is missing (it records the manifest's sha256)")
    else:
        rec = sorted({h.lower() for ln in dec.read_text(encoding="utf-8", errors="replace").splitlines()
                      if "MANIFEST.sha256" in ln for h in re.findall(r"\b[0-9a-fA-F]{64}\b", ln)})
        if len(rec) != 1:
            probs.append(f"DECISION_CS.md must record exactly one sha256 on the lines naming MANIFEST.sha256 "
                         f"(found {len(rec)})")
        elif res["manifest_sha256"] and rec[0] != res["manifest_sha256"]:
            probs.append(f"DECISION_CS.md records {rec[0][:16]}... but cs/MANIFEST.sha256 hashes to "
                         f"{res['manifest_sha256'][:16]}...")
    return res


def _flip_copy(src: Path, dest: Path, value: str) -> bool:
    """dest/typed_calls.py = src/typed_calls.py with its _TYPED_DEFAULT_LANGS line set to `value`."""
    text = (src / "typed_calls.py").read_text(encoding="utf-8")
    new, n = re.subn(r"^_TYPED_DEFAULT_LANGS\b.*$", f"_TYPED_DEFAULT_LANGS: frozenset = {value}", text, count=1,
                     flags=re.M)
    dest.mkdir(parents=True, exist_ok=True)
    with open(dest / "typed_calls.py", "w", encoding="utf-8", newline="\n") as fh:
        fh.write(new)
    return n == 1


def _write(p: Path, data) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))


def _manifest_kit(root: Path, files: dict, *, record=True, order=None, extra_lines=(), crlf=False) -> str:
    """A kit dir holding cs/<files>, cs/MANIFEST.sha256 over them and (record) DECISION_CS.md naming its sha256."""
    for rel, data in files.items():
        _write(root / "cs" / rel, data)
    rels = order or sorted(files, key=lambda s: s.encode("utf-8"))
    lines = [f"{hashlib.sha256(files[r].encode('utf-8') if isinstance(files[r], str) else files[r]).hexdigest()}  {r}"
             for r in rels] + list(extra_lines)
    body = "\n".join(lines) + "\n"
    if crlf:
        body = body.replace("\n", "\r\n")
    _write(root / "cs" / "MANIFEST.sha256", body)
    sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
    if record:
        _write(root / "DECISION_CS.md", f"# DECISION_CS\n\n- G5b: `cs/MANIFEST.sha256` sha256 `{sha}`\n")
    return sha


def gate_k(kit: Path, base: Path, build: Path = None) -> None:
    print("\n-- K: the G5b flip guard (PREREG_CS §8.7): csharp joins _TYPED_DEFAULT_LANGS only with the committed "
          "effect diff --")
    build = build or BUILD
    g = flip_guard(build, kit)
    if g["active"] is False:
        check(f"G5b guard on this tree: inactive — csharp is not in typed_calls._TYPED_DEFAULT_LANGS "
              f"({g['defaults']}), so no manifest is required yet", not g["problems"], g["problems"])
    else:
        check(f"G5b guard on this tree: csharp IS a default ({g['defaults']}): benchmark/call_linking/cs/"
              f"MANIFEST.sha256 exists, every listed file matches ({g['entries']}), and DECISION_CS.md records the "
              f"manifest's sha256", g["active"] is True and not g["problems"], g["problems"][:8])
    k = base / "k_guard"
    on, on2, off, bad = k / "b_on", k / "b_on2", k / "b_off", k / "b_bad"
    built = (_flip_copy(build, off, "frozenset()") and _flip_copy(build, on, 'frozenset({"csharp"})')
             and _flip_copy(build, on2, 'frozenset({"python", "csharp"})'))
    _write(bad / "typed_calls.py", "_TYPED_DEFAULT_LANGS: frozenset = frozenset(\n")
    check("K self-test: the product's _TYPED_DEFAULT_LANGS line can be set in a temp copy of typed_calls.py", built)
    if not built:
        return
    files = {"delta_VT-cs_g39.json": '{"a":1}\n', "audit/jellyfin_g39/key.json": '{"k":2}\n',
             "audit/jellyfin_g39/sheet_r1.csv": "id,verdict,note\r\nx,TRUE,\r\n"}
    ok_kit = k / "kit_ok"
    _manifest_kit(ok_kit, files)
    cases = []

    def case(label, build_dir, setup, want_active, want_ok, must=None):
        kd = k / f"kit_{len(cases)}"
        if setup is not None:
            setup(kd)
        r = flip_guard(build_dir, kd)
        hit = must is None or any(must in p for p in r["problems"])
        cases.append(label)
        check(f"K self-test: {label}", r["active"] is want_active and (not r["problems"]) == want_ok and hit,
              {"active": r["active"], "problems": r["problems"][:6]})

    def mk(**kw):
        return lambda kd: _manifest_kit(kd, files, **kw)

    def changed(kd):
        _manifest_kit(kd, files)
        _write(kd / "cs" / "delta_VT-cs_g39.json", '{"a":2}\n')

    def deleted(kd):
        _manifest_kit(kd, files)
        (kd / "cs" / "audit" / "jellyfin_g39" / "key.json").unlink()

    def other_sha(kd):
        _manifest_kit(kd, files, record=False)
        _write(kd / "DECISION_CS.md", "- G5b: `cs/MANIFEST.sha256` sha256 `" + "ab" * 32 + "`\n")

    def two_shas(kd):
        sha = _manifest_kit(kd, files, record=False)
        _write(kd / "DECISION_CS.md", f"- `cs/MANIFEST.sha256`: `{sha}`\n- earlier `cs/MANIFEST.sha256`: `{'cd' * 32}`\n")

    def sheet_md(kd):
        _manifest_kit(kd, dict(files, **{"audit/jellyfin_g39/sheet.md": "# sheet\n"}))

    def traversal(kd):
        _write(kd / "x.json", "{}\n")
        _manifest_kit(kd, files, extra_lines=(f"{hashlib.sha256(b'{}' + bytes([10])).hexdigest()}  ../x.json",))

    case("inactive (csharp not a default) with nothing under cs/: holds, nothing required", off, None, False, True)
    case("inactive with a broken manifest: holds (the guard applies only once csharp is a default)", off,
         lambda kd: (_manifest_kit(kd, files), _write(kd / "cs" / "delta_VT-cs_g39.json", "changed\n")), False, True)
    case("ACTIVE with no cs/ and no DECISION_CS.md: FAILS (the manifest is required)", on, None, True, False,
         "MANIFEST.sha256 is missing")
    case("ACTIVE with the manifest, every file matching and DECISION_CS.md recording its sha256: holds", on, mk(),
         True, True)
    case("ACTIVE through a defaults set that also holds python: holds the same way", on2, mk(), True, True)
    case("ACTIVE, a listed file changed after the manifest: FAILS", on, changed, True, False, "differs")
    case("ACTIVE, a listed file missing: FAILS", on, deleted, True, False, "missing")
    case("ACTIVE, DECISION_CS.md records another sha256: FAILS", on, other_sha, True, False, "records")
    case("ACTIVE, DECISION_CS.md records two different sha256s for the manifest: FAILS", on, two_shas, True, False,
         "exactly one")
    case("ACTIVE, no DECISION_CS.md: FAILS", on, mk(record=False), True, False, "DECISION_CS.md is missing")
    case("ACTIVE, the manifest's paths out of byte order: FAILS", on,
         mk(order=sorted(files, key=lambda s: s.encode("utf-8"), reverse=True)), True, False, "byte order")
    case("ACTIVE, the manifest lists a path outside cs/ (`../x.json`): FAILS", on, traversal, True, False,
         "inside cs/")
    case("ACTIVE, the manifest lists a sheet.md: FAILS (never committed: it quotes corpus source)", on, sheet_md,
         True, False, "sheet.md")
    case("ACTIVE, a CRLF manifest: FAILS (its bytes are not the recorded ones)", on, mk(crlf=True), True, False, "CR")
    case("an unreadable typed_calls.py: FAILS closed (the guard cannot clear the tree)", bad, mk(), None, False,
         "cannot read")


# =====================================================================================================
# L — the C# reference truth_roslyn on its committed self-test (no .NET), and live when .NET is present
# =====================================================================================================
# The gate's own reading of PREREG_CS §8.2's contract on the self-test corpus (truth_roslyn/selftest/corpus/<path>.txt),
# written from the sources: (file, line, name or None, site kind or None, col or None) -> what the row must say.
CS_OH = "App/OrderHandler.cs"
CS_FORMS = [(CS_OH, 29, "Find", "name_attr", "x.M(): a field"), (CS_OH, 30, "Log", "this", "this.M()"),
            (CS_OH, 31, "Log", "super", "base.M()"), (CS_OH, 32, "Log", "bare", "M(): an implicit receiver"),
            (CS_OH, 33, "Save", "recv", "this._f.M()"), (CS_OH, 34, "Handle", "name_attr", "x?.M() takes x.M()'s form"),
            (CS_OH, 36, "Handle", "recv", "x[i].M()"), (CS_OH, 37, "Peek", "recv", "a cast receiver"),
            (CS_OH, 41, "TaxOf", "name_attr", "a type name receiver"), (CS_OH, 42, "TaxOf", "recv", "global::..."),
            (CS_OH, 43, "TaxOf", "recv", "a qualified type Ns.T.M()"),
            (CS_OH, 44, "Join", "name_attr", "a predefined type keyword"), (CS_OH, 45, "Convert", "bare", "M<T>()"),
            (CS_OH, 49, None, "other", "a[i]()"), (CS_OH, 38, "OrderHandler", "ctor", "new T(...)"),
            ("App/Dispatch.cs", 27, "Order", "ctor", "target-typed new()")]
CS_DEFSTACK = [("App/Members.cs", 5, "Compute", [], "a field initializer: no entry"),
               ("App/Members.cs", 7, "Compute", [["Members.Auto", 7, []]], "a property initializer: the property"),
               ("App/Members.cs", 33, "Compute", [["Members.Members", 31, []]], "the first (static) constructor"),
               ("App/Members.cs", 38, "Compute", [["Members.Members", 36, [31]]],
                "a constructor overload: alt = the first same-named member's line"),
               ("App/Members.cs", 61, "Compute", [["Members.Spread", 55, [57, 59]]],
                "attribute lists: line = the first attribute, alt = the line after them and the name line"),
               ("App/Members.cs", 73, "Compute", [["Members.Over", 69, [64]], ["Members.Over.Local", 71, []]],
                "a lambda is no entry, a local function is; an overload's alt"),
               (CS_OH, 29, "Find", [["OrderHandler.Handle", 27, []]], "containing type, then the member"),
               ("App/Program.cs", 3, "OrderRepository", [], "top-level statements: no entry")]


def _cs_rows(doc, f, line, name=None, kind=None, col=None) -> list:
    return [r for r in doc.get("rows") or [] if r.get("file") == f and r.get("line") == line
            and (name is None or r.get("name") == name) and (kind is None or r.get("site_kind") == kind)
            and (col is None or r.get("col") == col)]


def _cs_locs(entries) -> list:
    return sorted([e.get("file"), e.get("line")] for e in entries or [])


def _cs_expect(doc, cases) -> list:
    """The cases (file, line, name, {field: want} [, col]) that do not hold on `doc` (exactly one row each)."""
    bad = []
    for c in cases:
        f, line, name, want = c[:4]
        rows = _cs_rows(doc, f, line, name, col=c[4] if len(c) > 4 else None)
        if len(rows) != 1:
            bad.append(f"{f}:{line} {name}: {len(rows)} rows")
            continue
        r = rows[0]
        for k, v in want.items():
            got = (_cs_locs(r.get(k)) if k in ("defs", "dispatch") else
                   [d.get("alt_lines") for d in r.get("defs") or []] if k == "def_alt" else
                   [d.get("type") for d in r.get("defs") or []] if k == "def_types" else r.get(k))
            if got != v:
                bad.append(f"{f}:{line} {name}: {k} {got!r} != {v!r}")
    return bad


IOR, IMPL, FAKE, REPOS = ("Core/Domain/IOrderRepository.cs", "Infra/OrderRepository.cs",
                          "tests/App.Tests/FakeOrderRepository.cs", "Core/Domain/Repositories.cs")
SHP = "Core/Shapes/"
CS_CATEGORIES = {
    "overloads (graphify's one node for all overloads: the first same-named member's line is an alt line; an object "
    "creation binds every declaration of a partial TYPE)": [
        ("App/Members.cs", 77, "Over", {"defs": [["App/Members.cs", 64]], "def_alt": [[]]}, 26),
        ("App/Members.cs", 77, "Over", {"defs": [["App/Members.cs", 69]], "def_alt": [[64]]}, 47),
        ("App/Members.cs", 77, "Spread", {"defs": [["App/Members.cs", 55]], "def_alt": [[57, 59]]}),
        ("Infra/OrderRepository.cs", 14, "Order", {"defs": [["Core/Domain/Order.Lines.cs", 3], ["Core/Domain/Order.cs", 5]],
                                                   "def_types": ["class", "class"], "def_alt": [[], [6]]})],
    "extension calls (the ReducedFrom method, in both call forms; no dispatch)": [
        (CS_OH, 46, "Label", {"defs": [["Core/Domain/Pricing.cs", 10]], "dispatch": [], "form": "name_attr"}),
        (CS_OH, 47, "Label", {"defs": [["Core/Domain/Pricing.cs", 10]], "dispatch": []})],
    "interface dispatch (the interface member is the static def; the implementers of the receiver's construction "
    "are the dispatch targets, a bodiless one dropped)": [
        (CS_OH, 29, "Find", {"defs": [[IOR, 5]], "dispatch": [[IMPL, 7]], "dispatch_rule": "interface"}),
        (CS_OH, 33, "Save", {"defs": [[IOR, 7]], "dispatch": [[IMPL, 9]], "dispatch_rule": "interface"}),
        (CS_OH, 55, "Get", {"defs": [[REPOS, 5]], "dispatch": [[REPOS, 14]], "dispatch_dropped": {"bodiless": 1}})],
    "override dispatch (every override below the receiver's static type, or the containing type for this. / an "
    "implicit receiver; none for base., a sealed member or a `new` slot's other chain)": [
        (CS_OH, 30, "Log", {"defs": [[CS_OH, 8]], "dispatch": [[CS_OH, 86]], "dispatch_rule": "override"}),
        (CS_OH, 32, "Log", {"defs": [[CS_OH, 8]], "dispatch": [[CS_OH, 86]]}),
        (CS_OH, 31, "Log", {"defs": [[CS_OH, 8]], "dispatch": []}),
        ("App/Geometry.cs", 9, "Area", {"defs": [[SHP + "Shape.cs", 5]],
                                        "dispatch": [[SHP + "Circle.cs", 5], [SHP + "Ring.cs", 5], [SHP + "Square.cs", 5]]}),
        ("App/Geometry.cs", 10, "Area", {"defs": [[SHP + "Circle.cs", 5]], "dispatch": [[SHP + "Ring.cs", 5]]}),
        ("App/Geometry.cs", 11, "Describe", {"defs": [[SHP + "Ring.cs", 7]], "dispatch": []}),
        ("App/Geometry.cs", 12, "Describe", {"defs": [[SHP + "Square.cs", 7]], "dispatch": [[SHP + "Square.cs", 12]]}),
        ("App/Geometry.cs", 13, "Describe", {"defs": [[SHP + "Shape.cs", 7]],
                                             "dispatch": [[SHP + "Circle.cs", 7], [SHP + "Ring.cs", 7]]})],
    "test-file exclusion (a non-test caller drops a test-file target; a test caller keeps it)": [
        (CS_OH, 29, "Find", {"dispatch": [[IMPL, 7]], "dispatch_dropped": {"test_file": 1}}),
        (CS_OH, 33, "Save", {"dispatch": [[IMPL, 9]], "dispatch_dropped": {"test_file": 1}}),
        ("tests/App.Tests/HandlerTests.cs", 11, "Find", {"dispatch": [[IMPL, 7], [FAKE, 7]], "dispatch_dropped": {}})],
}
# Extra probes of the replicated test rule (both corpora's shapes), beside truth_roslyn.PROBE_PATHS.
CS_TEST_PROBES = ("tests/Ordering.UnitTests/Builders.cs", "src/Ordering.API/Application/Commands/X.cs",
                  "Jellyfin.Server/Migrations/JellyfinMigrationService.cs", "src/MyApp.Tests/A.cs", "Foo.Test.cs",
                  "a/TEST/b.cs", "x/Spec/y.cs", "x_spec.rb", "a.spec.tsx", "Foo_test.go", "FooTest.java", "e2e.cs",
                  "src/__mocks__/m.ts", "Testing/T.cs", "fuzz/Jellyfin.Api.Fuzz/Program.cs",
                  # the .NET test-project directory rule (build/sync.py _DOTNET_TEST_DIR_SUFFIXES), shapes beside
                  # truth_roslyn's own probes
                  "src/Ordering.FunctionalTests/A.cs", "SRC/SHOP.INTEGRATIONTESTS/X.CS", "src/Shop.Tests.Helpers/X.cs",
                  "src/Shop.Testing/X.cs", "src/Shop.Tests", "src/Shop.Tests/", "Shop.Specs\\Steps\\X.cs",
                  "src/Shop.UnitTest/Sub/Deeper/X.cs", "src/.tests/X.cs", "src/Tests.Shop/X.cs")
# The project evaluation, which a Roslyn check alone cannot see (the reference shared these defects with the product's
# MSBuild reader): each self-test project's Using items as real MSBuild gave them (`dotnet msbuild <proj>
# -getItem:Using -p:Configuration=Release -p:Platform=AnyCPU -p:TargetFramework=<first>`, SDK 8.0.202), not the tool.
CS_MSB7 = ("System", "System.Collections.Generic", "System.IO", "System.Linq", "System.Net.Http", "System.Threading",
           "System.Threading.Tasks")
CS_MSB6 = tuple(x for x in CS_MSB7 if x != "System.Net.Http")
CS_MSB_USINGS = {
    "Ws/Ws.csproj": ("Ws.Lib",),
    "Rm/Glob/RmGlob.csproj": ("Rm.Lib", "System", "System.Collections.Generic", "System.IO", "System.Net.Http"),
    "Rm/Stars/RmStars.csproj": ("System",), "Rm/Dots/RmDots.csproj": (),
    "NetFx/Main/NetFxMain.csproj": ("NetFx.Lib", "NetFx.Other") + CS_MSB6, "NetFx/Dotted/NetFxDotted.csproj": CS_MSB6,
    "NetFx/Tfi/NetFxTfi.csproj": CS_MSB6, "NetFx/Core5/NetFxCore5.csproj": CS_MSB7,
    "NetFx/Std/NetFxStd.csproj": CS_MSB7, "NetFx/Imp/NetFxImp.csproj": CS_MSB7,
    "Pkg/On/PkgOn.csproj": ("Pkg.Lib",), "Pkg/Cpm/PkgCpm.csproj": ("Pkg.Lib",), "Pkg/Off/PkgOff.csproj": CS_MSB7,
    "Pkg/Proj/PkgProj.csproj": ("Pkg.Lib",) + CS_MSB7, "Pkg/Twice/PkgTwice.csproj": ("Pkg.Lib",) + CS_MSB7,
    "Pkg/Path/PkgPath.csproj": ("Pkg.Alt",) + CS_MSB7,
}
# Per fix: the rule a reverted tool breaks, its project, the header fields and usings the tool gave BEFORE the fix
# (measured), and the site that binds only under the fix, to (file, line).
CS_MSB_MUTANTS = (
    ("a property value trimmed: a multi-line <ImplicitUsings> read as enable, a condition blind to the whitespace",
     "Ws/Ws.csproj", {"implicit_usings": True}, CS_MSB7 + ("Ws.Other",),
     ("Ws/WsCalls.cs", 20, "Tick"), ("Ws/WsCalls.cs", 5)),
    ("<Using Remove> compared literally: no wildcard, the removed namespaces stay", "Rm/Glob/RmGlob.csproj", {},
     CS_MSB7 + ("Rm.Lib",), ("Rm/Glob/RmCalls.cs", 15, "Tick"), ("Rm/Glob/RmCalls.cs", 5)),
    ("System.Net.Http on .NETFramework, and TargetFrameworkIdentifier empty in item conditions",
     "NetFx/Main/NetFxMain.csproj", {}, CS_MSB7 + ("NetFx.Lib",), ("NetFx/Main/NetFxCalls.cs", 20, "Send"),
     ("NetFx/Main/NetFxCalls.cs", 5)),
    ("Directory.Packages.props never evaluated", "Pkg/On/PkgOn.csproj",
     {"implicit_usings": True, "read": ["Directory.Build.props", "Pkg/On/PkgOn.csproj"]}, CS_MSB7,
     ("Pkg/On/PkgOnCalls.cs", 15, "Tick"), ("Pkg/On/PkgOnCalls.cs", 5)),
)
# R41's self-test projects (selftest/corpus/Msb/): the GlobalUsings.g.cs real MSBuild writes for each, line for
# line (`dotnet msbuild <proj> -t:GenerateGlobalUsings -p:Configuration=Release -p:Platform=AnyCPU`, SDK 8.0.202;
# no file at all without the target, MSB4057), sorted.
CS_MSB_LINES = {
    'Msb/Bool/BoolProj.csproj': (
        'global using global::Bo.Bare;',
        'global using global::Bo.Lib;',
        'global using global::System.Collections.Generic;',
        'global using global::System.IO;',
        'global using global::System.Linq;',
        'global using global::System.Net.Http;',
        'global using global::System.Threading.Tasks;',
        'global using global::System;',
    ),
    'Msb/Choose/ChooseProj.csproj': (
        'global using global::Ch.Lib;',
        'global using global::Ch.Prop;',
    ),
    'Msb/Desk/DeskProj.csproj': (
        'global using global::Dk.Lib;',
        'global using global::System.Collections.Generic;',
        'global using global::System.Drawing;',
        'global using global::System.Linq;',
        'global using global::System.Threading.Tasks;',
        'global using global::System.Threading;',
        'global using global::System.Windows.Forms;',
        'global using global::System;',
    ),
    'Msb/Dots/DotsProj.csproj': (
        'global using global::Dt.Lib;',
        'global using global::System.Collections.Generic;',
        'global using global::System.IO;',
        'global using global::System.Net.Http;',
        'global using global::System.Threading.Tasks;',
        'global using global::System;',
    ),
    'Msb/Esc/EscProj.csproj': (
        'global using Eo = global::Es.Other;',
        'global using global::Es.Lib;',
        'global using global::System.Collections.Generic;',
        'global using global::System.IO;',
        'global using global::System.Linq;',
        'global using global::System.Net.Http;',
        'global using global::System.Threading.Tasks;',
        'global using global::System;',
    ),
    'Msb/Exists/Proj/ExProj.csproj': (
        'global using global::Ex.Dir;',
        'global using global::Ex.Lib;',
        'global using global::Ex.Shared;',
    ),
    'Msb/Lower/LowerProj.csproj': (
        'global using global::Lw.Lib;',
        'global using global::System.Collections.Generic;',
        'global using global::System.IO;',
        'global using global::System.Linq;',
        'global using global::System.Net.Http;',
        'global using global::System.Threading.Tasks;',
        'global using global::System;',
    ),
    'Msb/NoSdk/Bare/NoSdkBare.csproj': (
    ),
    'Msb/NoSdk/PropsOnly/PropsOnly.csproj': (
    ),
    'Msb/Rsp/Proj/RspProj.csproj': (
        'global using global::Rs.Lib;',
        'global using global::Rs.Two;',
    ),
    'Msb/SdkImp/Attr/SdkAttr.csproj': (
        'global using global::Microsoft.Extensions.Configuration;',
        'global using global::Microsoft.Extensions.DependencyInjection;',
        'global using global::Microsoft.Extensions.Hosting;',
        'global using global::Microsoft.Extensions.Logging;',
        'global using global::System.Collections.Generic;',
        'global using global::System.IO;',
        'global using global::System.Linq;',
        'global using global::System.Net.Http;',
        'global using global::System.Threading.Tasks;',
        'global using global::System.Threading;',
        'global using global::System;',
    ),
    'Msb/SdkImp/Elem/SdkElem.csproj': (
        'global using global::Microsoft.AspNetCore.Builder;',
        'global using global::Microsoft.AspNetCore.Hosting;',
        'global using global::Microsoft.AspNetCore.Http;',
        'global using global::Microsoft.AspNetCore.Routing;',
        'global using global::Microsoft.Extensions.Configuration;',
        'global using global::Microsoft.Extensions.DependencyInjection;',
        'global using global::Microsoft.Extensions.Hosting;',
        'global using global::Microsoft.Extensions.Logging;',
        'global using global::System.Collections.Generic;',
        'global using global::System.IO;',
        'global using global::System.Linq;',
        'global using global::System.Net.Http.Json;',
        'global using global::System.Net.Http;',
        'global using global::System.Threading.Tasks;',
        'global using global::System.Threading;',
        'global using global::System;',
    ),
    'Msb/TgtOnly/TgtOnly.csproj': (
        'global using global::Tg.Lib;',
    ),
    'Msb/Tlp/Proj/TlpProj.csproj': (
        'global using global::Tl.Lib;',
    ),
    'Msb/Update/UpProj.csproj': (
        'global using Thr = global::System.Threading;',
        'global using global::System.Collections.Generic;',
        'global using global::System.IO;',
        'global using global::System.Linq;',
        'global using global::System.Net.Http;',
        'global using global::System.Threading.Tasks;',
        'global using global::System;',
        'global using global::Up.Lib;',
        'global using static global::System.Math;',
    ),
}
# Per R41 rule: the capture the tool built with that ONE rule reverted wrote (a scratch copy of Program.cs, the
# rule's RED evidence): the project's fields expect.json asserts, its global usings, the site, its target under
# the fix, and the site as the reverted tool bound it (None: unmoved).
CS_MSB_R41_MUTANTS = (
    ('Exists() not evaluated (taken as false)',
     'Msb/Exists/Proj/ExProj.csproj',
     {'notes': ['Msb/Exists/Directory.Build.props: condition not evaluated (taken as false): '
                "Exists('../../../../far.txt')",
                "Msb/Exists/Directory.Build.props: condition not evaluated (taken as false): Exists('ExCalls.cs')",
                "Msb/Exists/Directory.Build.props: condition not evaluated (taken as false): Exists('Gone.cs')",
                "Msb/Exists/Directory.Build.props: condition not evaluated (taken as false): Exists('Gone.props')",
                'Msb/Exists/Directory.Build.props: condition not evaluated (taken as false): '
                "Exists('Proj/ExCalls.cs')",
                'Msb/Exists/Directory.Build.props: condition not evaluated (taken as false): '
                "Exists('Shared.props')"],
      'read': ['Msb/Exists/Directory.Build.props', 'Msb/Exists/Proj/ExProj.csproj']},
     (),
     ('Msb/Exists/Proj/ExCalls.cs', 15, 'Tick'), ('Msb/Exists/Proj/ExCalls.cs', 5),
     {'defs': [], 'dispatch': [], 'resolution': 'none'}),
    ('Exists() read from the project directory in every condition',
     'Msb/Exists/Proj/ExProj.csproj',
     {'notes': ['Msb/Exists/Directory.Build.props: Exists() of a path outside the corpus, not read (taken as '
                'false): ../../../../far.txt'],
      'read': ['Msb/Exists/Directory.Build.props', 'Msb/Exists/Proj/ExProj.csproj']},
     ('global using global::Ex.Lib;',),
     ('Msb/Exists/Proj/ExCalls.cs', 15, 'Tick'), ('Msb/Exists/Proj/ExCalls.cs', 5),
     None),
    ("== / != compared as strings; a lone operand true only when 'true'",
     'Msb/Bool/BoolProj.csproj',
     {'implicit_usings': False},
     ('global using global::Bo.Never;',),
     ('Msb/Bool/BoolCalls.cs', 15, 'Tick'), ('Msb/Bool/BoolCalls.cs', 5),
     {'defs': [], 'dispatch': [], 'resolution': 'none'}),
    ('Directory.Build.rsp not read',
     'Msb/Rsp/Proj/RspProj.csproj',
     {'implicit_usings': True, 'read': ['Directory.Build.props', 'Msb/Rsp/Proj/RspProj.csproj']},
     ('global using global::System.Collections.Generic;', 'global using global::System.IO;',
      'global using global::System.Linq;', 'global using global::System.Net.Http;',
      'global using global::System.Threading.Tasks;', 'global using global::System.Threading;',
      'global using global::System;'),
     ('Msb/Rsp/Proj/RspCalls.cs', 15, 'Tick'), ('Msb/Rsp/Proj/RspCalls.cs', 5),
     {'defs': [], 'dispatch': [], 'resolution': 'none'}),
    ('TreatAsLocalProperty ignored',
     'Msb/Tlp/Proj/TlpProj.csproj',
     {},
     (),
     ('Msb/Tlp/Proj/TlpCalls.cs', 15, 'Tick'), ('Msb/Tlp/Proj/TlpCalls.cs', 5),
     {'defs': [], 'dispatch': [], 'resolution': 'none'}),
    ("the project's TreatAsLocalProperty held only from the project file on (after Directory.Build.props)",
     'Msb/Tlp/Proj/TlpProj.csproj',
     {},
     (),
     ('Msb/Tlp/Proj/TlpCalls.cs', 15, 'Tick'), ('Msb/Tlp/Proj/TlpCalls.cs', 5),
     {'defs': [], 'dispatch': [], 'resolution': 'none'}),
    ('an SDK named by an imported file (<Project Sdk> in Directory.Build.props) not imported',
     'Msb/SdkImp/Attr/SdkAttr.csproj',
     {'sdk': ['Microsoft.NET.Sdk']},
     ('global using global::System.Collections.Generic;', 'global using global::System.IO;',
      'global using global::System.Linq;', 'global using global::System.Net.Http;',
      'global using global::System.Threading.Tasks;', 'global using global::System.Threading;',
      'global using global::System;'),
     ('Msb/SdkImp/Attr/SdkAttrCalls.cs', 15, 'Spin'), ('Msb/SdkImp/Attr/SdkAttrCalls.cs', 5),
     {'defs': [], 'dispatch': [], 'resolution': 'none'}),
    ('an SDK named by an imported file (<Sdk Name> in an imported .props) not imported',
     'Msb/SdkImp/Elem/SdkElem.csproj',
     {'sdk': ['Microsoft.NET.Sdk']},
     ('global using global::System.Collections.Generic;', 'global using global::System.IO;',
      'global using global::System.Linq;', 'global using global::System.Net.Http;',
      'global using global::System.Threading.Tasks;', 'global using global::System.Threading;',
      'global using global::System;'),
     ('Msb/SdkImp/Elem/SdkElemCalls.cs', 15, 'Spin'), ('Msb/SdkImp/Elem/SdkElemCalls.cs', 5),
     {'defs': [], 'dispatch': [], 'resolution': 'none'}),
    ('<Using Update> ignored',
     'Msb/Update/UpProj.csproj',
     {},
     ('global using global::System.Collections.Generic;', 'global using global::System.IO;',
      'global using global::System.Linq;', 'global using global::System.Math;',
      'global using global::System.Net.Http;', 'global using global::System.Threading.Tasks;',
      'global using global::System.Threading;', 'global using global::System;', 'global using global::Up.Lib;'),
     ('Msb/Update/UpCalls.cs', 15, 'Tick'), ('Msb/Update/UpCalls.cs', 5),
     {'defs': [], 'dispatch': [], 'resolution': 'none'}),
    ("a <Using Include>'s Exclude ignored",
     'Msb/Update/UpProj.csproj',
     {},
     ('global using Thr = global::System.Threading;', 'global using global::System.Collections.Generic;',
      'global using global::System.IO;', 'global using global::System.Linq;',
      'global using global::System.Net.Http;', 'global using global::System.Threading.Tasks;',
      'global using global::System;', 'global using global::Up.Lib;', 'global using global::Up.Other;',
      'global using static global::System.Math;'),
     ('Msb/Update/UpCalls.cs', 15, 'Tick'), ('Msb/Update/UpCalls.cs', 5),
     {'defs': [], 'dispatch': [], 'resolution': 'none'}),
    ('%XX not decoded (condition operands, identities, metadata)',
     'Msb/Esc/EscProj.csproj',
     {'implicit_usings': False},
     ('global using E%6F = global::Es.Other;', 'global using global::Es%2ELib;'),
     ('Msb/Esc/EscCalls.cs', 15, 'Tick'), ('Msb/Esc/EscCalls.cs', 5),
     {'defs': [], 'dispatch': [], 'resolution': 'none'}),
    ("%XX decoded BEFORE the ';' split (%3B splits one Remove into two)",
     'Msb/Esc/EscProj.csproj',
     {'implicit_usings': True},
     ('global using Eo = global::Es.Other;', 'global using global::Es.Lib;',
      'global using global::System.Collections.Generic;', 'global using global::System.Net.Http;',
      'global using global::System.Threading.Tasks;', 'global using global::System;'),
     ('Msb/Esc/EscCalls.cs', 15, 'Tick'), ('Msb/Esc/EscCalls.cs', 5),
     None),
    ("a Remove compared without Windows' trailing-period rule",
     'Msb/Dots/DotsProj.csproj',
     {},
     ('global using global::Dt.Lib;', 'global using global::System.Collections.Generic;',
      'global using global::System.IO;', 'global using global::System.Linq;',
      'global using global::System.Net.Http;', 'global using global::System.Threading.Tasks;',
      'global using global::System.Threading;', 'global using global::System;'),
     ('Msb/Dots/DotsCalls.cs', 15, 'Tick'), ('Msb/Dots/DotsCalls.cs', 5),
     {'defs': [], 'dispatch': [], 'resolution': 'none'}),
    ('a project with no SDK still writes its Using items',
     'Msb/NoSdk/Bare/NoSdkBare.csproj',
     {'notes': ["ImplicitUsings is on but no SDK's Sdk.props is imported: no SDK Using items"], 'sdk': []},
     ('global using global::Nb.Other;',),
     ('Msb/NoSdk/Bare/NbCalls.cs', 17, 'Tick'), ('Msb/NoSdk/Bare/NbCalls.cs', 7),
     {'defs': [], 'dispatch': [], 'resolution': 'none'}),
    ('Sdk.props without Sdk.targets still writes the usings',
     'Msb/NoSdk/PropsOnly/PropsOnly.csproj',
     {'notes': []},
     ('global using global::Po.Other;', 'global using global::System.Collections.Generic;',
      'global using global::System.IO;', 'global using global::System.Linq;',
      'global using global::System.Net.Http;', 'global using global::System.Threading.Tasks;',
      'global using global::System.Threading;', 'global using global::System;'),
     ('Msb/NoSdk/PropsOnly/PoCalls.cs', 17, 'Tick'), ('Msb/NoSdk/PropsOnly/PoCalls.cs', 7),
     {'defs': [], 'dispatch': [], 'resolution': 'none'}),
    ('an Sdk.targets-only import adds the SDK list',
     'Msb/TgtOnly/TgtOnly.csproj',
     {'notes': []},
     ('global using global::System.Collections.Generic;', 'global using global::System.IO;',
      'global using global::System.Linq;', 'global using global::System.Net.Http;',
      'global using global::System.Threading.Tasks;', 'global using global::System.Threading;',
      'global using global::System;', 'global using global::Tg.Lib;'),
     ('Msb/TgtOnly/TgCalls.cs', 15, 'Tick'), ('Msb/TgtOnly/TgCalls.cs', 5),
     {'defs': [], 'dispatch': [], 'resolution': 'none'}),
    ('UseWPF / UseWindowsForms not read',
     'Msb/Desk/DeskProj.csproj',
     {},
     ('global using global::Dk.Lib;', 'global using global::System.Collections.Generic;',
      'global using global::System.IO;', 'global using global::System.Linq;',
      'global using global::System.Net.Http;', 'global using global::System.Threading.Tasks;',
      'global using global::System.Threading;', 'global using global::System;'),
     ('Msb/Desk/DkCalls.cs', 15, 'Touch'), ('Msb/Desk/DkCalls.cs', 5),
     {'defs': [], 'dispatch': [], 'resolution': 'none'}),
    ('an item type matched in its own letter case only (<using> ignored)',
     'Msb/Lower/LowerProj.csproj',
     {},
     ('global using global::System.Collections.Generic;', 'global using global::System.IO;',
      'global using global::System.Linq;', 'global using global::System.Net.Http;',
      'global using global::System.Threading.Tasks;', 'global using global::System.Threading;',
      'global using global::System;'),
     ('Msb/Lower/LowerCalls.cs', 15, 'Tick'), ('Msb/Lower/LowerCalls.cs', 5),
     {'defs': [], 'dispatch': [], 'resolution': 'none'}),
    ('<Choose> not evaluated',
     'Msb/Choose/ChooseProj.csproj',
     {},
     (),
     ('Msb/Choose/ChooseCalls.cs', 15, 'Tick'), ('Msb/Choose/ChooseCalls.cs', 5),
     {'defs': [], 'dispatch': [], 'resolution': 'none'}),
    ('a <When> decided with the final properties (the item pass)',
     'Msb/Choose/ChooseProj.csproj',
     {},
     ('global using global::Ch.Late;', 'global using global::Ch.Prop;'),
     ('Msb/Choose/ChooseCalls.cs', 15, 'Tick'), ('Msb/Choose/ChooseCalls.cs', 5),
     {'defs': [{'alt_lines': [],
                'body': True,
                'file': 'Msb/Choose/ChooseCalls.cs',
                'full_name': 'Ch.Late.Timer.Tick',
                'interface': False,
                'line': 24,
                'name': 'Tick',
                'type': 'function'}],
      'dispatch': [],
      'resolution': 'symbol'}),
)


def _cs_using_items(project: dict) -> list:
    """A project's captured global usings as MSBuild's item identities."""
    return sorted(re.sub(r"^global using (?:static )?(?:\w+ = )?global::|;$", "", x)
                  for x in project.get("global_usings") or [])


def gate_l_msbuild(TR, kit: Path, doc: dict) -> None:
    """The self-test's MSBuild shapes against real MSBuild's answers, and per fix a mutant: the capture a reverted tool
    would write, which selftest/expect.json must refuse (`selftest --check` then fails, without .NET). The same
    reverts, built from a scratch copy of Program.cs, are the fixes' RED evidence."""
    projects = {p.get("path"): p for p in (doc.get("resolver") or {}).get("projects") or []}
    bad = {k: _cs_using_items(projects.get(k) or {}) for k, v in CS_MSB_USINGS.items()
           if k not in projects or _cs_using_items(projects[k]) != sorted(v)}
    check(f"the reference's global usings == real MSBuild's Using items on {len(CS_MSB_USINGS)} self-test projects "
          f"(SDK 8.0.202): verbatim property values, <Using Remove> wildcards, System.Net.Http outside .NETFramework, "
          f"Directory.Packages.props", not bad, bad)
    for label, proj, fields, usings, site, target in CS_MSB_MUTANTS:
        rows = _cs_rows(doc, *site)
        check(f"{site[0]}:{site[1]} binds {target[0]}:{target[1]} only under the rule below",
              len(rows) == 1 and _cs_locs(rows[0].get("defs")) == [list(target)], rows[:1])
        mut = copy.deepcopy(doc)
        p = next((x for x in mut["resolver"]["projects"] if x.get("path") == proj), None)
        if p is None:
            check(f"mutant ({label}): {proj} is in the capture", False)
            continue
        p.update(fields, global_usings=[f"global using global::{x};" for x in usings])
        for r in _cs_rows(mut, *site):
            r.update(resolution="none", defs=[], dispatch=[])
        try:
            with open(kit / "truth_roslyn" / "selftest" / "expect.json", encoding="utf-8") as fh:
                probs = TR.expect_problems(mut, json.load(fh))
        except Exception as exc:  # noqa: BLE001
            probs = [repr(exc)]
        on_project = [x for x in probs if proj in x]
        on_site = [x for x in probs if x.startswith("fix ")]      # a site row names its `what`, not its file
        check(f"mutant, the capture a tool without the rule writes ({label}): expect.json refuses it, on the "
              f"project's evaluation and on the site, and on nothing else",
              on_project and on_site and len(on_project) + len(on_site) == len(probs), probs[:6])
    gate_l_msbuild_r41(TR, kit, doc, projects)
    mt = re.search(r"\bstring\[\]\s+UsingsBase\s*=\s*\{(.*?)\};",
                   (kit / "truth_roslyn" / "Program.cs").read_text(encoding="utf-8"), re.S)
    got = tuple(re.findall(r'"([^"]*)"', mt.group(1))) if mt else None
    check("Program.cs's UsingsBase still lists the SDK's seven namespaces, System.Net.Http among them: a drift lock "
          "compares it with the product's list, and the .NETFramework rule filters it where it is read",
          got == CS_MSB7, got)


def gate_l_msbuild_r41(TR, kit: Path, doc: dict, projects: dict) -> None:
    """R41's MSBuild shapes (selftest/corpus/Msb/): each project's global usings == the GlobalUsings.g.cs real MSBuild
    writes for it, line for line (CS_MSB_LINES); per rule, the capture the tool built with that rule reverted wrote
    (CS_MSB_R41_MUTANTS, measured: red evidence), which expect.json must refuse on the project and, where the revert
    moves the site, on the site, and on nothing else; and the WindowsDesktop lists the tool reads, as data."""
    bad = {k: sorted((projects.get(k) or {}).get("global_usings") or []) for k, v in CS_MSB_LINES.items()
           if k not in projects or sorted(projects[k].get("global_usings") or []) != sorted(v)}
    check(f"R41: the reference's global usings == the GlobalUsings.g.cs real MSBuild writes (`dotnet msbuild <proj> "
          f"-t:GenerateGlobalUsings`, SDK 8.0.202; none without the target), line for line, on {len(CS_MSB_LINES)} "
          f"self-test projects: Exists(), boolean-like ==, Directory.Build.rsp, TreatAsLocalProperty, an imported "
          f"file's SDK, Using Update / Exclude, %XX after the ';' split, a trailing-period Remove, no SDK targets, "
          f"Sdk.targets alone, UseWPF / UseWindowsForms, a lower-case item type, <Choose>", not bad, bad)
    try:
        with open(kit / "truth_roslyn" / "selftest" / "expect.json", encoding="utf-8") as fh:
            expect = json.load(fh)
    except Exception as exc:  # noqa: BLE001
        check("R41: selftest/expect.json reads", False, repr(exc))
        return
    for label, proj, fields, lines, site, target, moved in CS_MSB_R41_MUTANTS:
        rows = _cs_rows(doc, *site)
        check(f"R41: {site[0]}:{site[1]} binds {target[0]}:{target[1]} (the fixed tool's capture)",
              len(rows) == 1 and _cs_locs(rows[0].get("defs")) == [list(target)], rows[:1])
        mut = copy.deepcopy(doc)
        p = next((x for x in mut["resolver"]["projects"] if x.get("path") == proj), None)
        if p is None:
            check(f"R41 mutant ({label}): {proj} is in the capture", False)
            continue
        p.update(copy.deepcopy(fields), global_usings=list(lines))
        for r in _cs_rows(mut, *site):
            if moved is not None:
                r.update(copy.deepcopy(moved))
        probs = TR.expect_problems(mut, expect)
        on_project = [x for x in probs if proj in x]
        on_site = [x for x in probs if x.startswith("fix ")]
        check(f"R41 mutant, the capture the tool with this rule reverted wrote ({label}): expect.json refuses it on "
              f"the project's evaluation{' and on the site' if moved is not None else ''}, and on nothing else",
              on_project and bool(on_site) == (moved is not None) and len(on_project) + len(on_site) == len(probs),
              probs[:6])
    text = (kit / "truth_roslyn" / "Program.cs").read_text(encoding="utf-8")
    got = {}
    for nm in ("WindowsFormsAddedUsings", "WpfRemovedUsings"):
        mt = re.search(r"\bstring\[\]\s+" + nm + r"\s*=\s*\{(.*?)\};", text, re.S)
        got[nm] = tuple(re.findall(r'"([^"]*)"', mt.group(1))) if mt else None
    check("R41: Program.cs's WindowsDesktop lists are SDK 8.0.202's (Microsoft.NET.Sdk.WindowsDesktop.WindowsForms.props "
          "adds System.Drawing and System.Windows.Forms, ...WPF.props removes System.IO and System.Net.Http)",
          got == {"WindowsFormsAddedUsings": ("System.Drawing", "System.Windows.Forms"),
                  "WpfRemovedUsings": ("System.IO", "System.Net.Http")}, got)


def _cs_synthetic_dump(edges=()) -> dict:
    """Graph nodes for the self-test corpus's callers and targets the L scoring checks read (keys are short names)."""
    def n(key, label, name, f, line):
        return {"key": key, "label": label, "name": name, "source_file": f, "start_line": line, "end_line": None}
    nodes = [n("m_oh", "Module", "OrderHandler", CS_OH, 1), n("m_ht", "Module", "HandlerTests",
                                                               "tests/App.Tests/HandlerTests.cs", 1),
             n("h", "Method", "OrderHandler.Handle", CS_OH, 27), n("t", "Method", "HandlerTests.Finds",
                                                                 "tests/App.Tests/HandlerTests.cs", 9),
             n("iF", "Method", "IOrderRepository.Find", IOR, 5), n("iS", "Method", "IOrderRepository.Save", IOR, 7),
             n("oF", "Method", "OrderRepository.Find", IMPL, 7), n("oS", "Method", "OrderRepository.Save", IMPL, 9),
             n("fF", "Method", "FakeOrderRepository.Find", FAKE, 7), n("lg", "Method", "BaseHandler.Log", CS_OH, 8),
             n("sl", "Method", "SpecialHandler.Log", CS_OH, 86), n("cp", "Method", "Members.Compute", "App/Members.cs", 80)]
    return {"schema": "call-linking/offline-calls/1", "nodes": nodes, "edges": [list(e) for e in edges], "flags": {}}


def gate_l(mods: dict, kit: Path, base: Path) -> None:
    print("\n-- L: the C# reference truth_roslyn — its committed self-test through the adapter (no .NET), the gate's own "
          "reading of the contract, the kit scoring it; live when .NET SDK 8.0.202 is present --")
    TR, C, D = mods.get("truth_roslyn"), mods.get("_common"), mods.get("delta")
    if TR is None:
        check("truth_roslyn imports (section A)", False)
        return
    try:
        probs = TR.check_selftest()
    except Exception as exc:  # noqa: BLE001 — RED, never a crash of the section
        probs = [f"check_selftest crashed: {exc!r}"]
    check("truth_roslyn selftest --check: the captured selftest/sites.jsonl converts through the adapter to "
          "selftest/truth.json byte for byte, and every selftest/expect.json assertion holds (no .NET)", not probs,
          probs[:8])
    S = _fresh_sync()
    product = S._is_test_path
    try:
        header, rows = TR.read_sites(kit / "truth_roslyn" / "selftest" / "sites.jsonl")
        # the contract's derivation with the PRODUCT's own test rule (PREREG_CS §8.2), so a drifted replica (the
        # next check) never hides the rest of this section
        doc = TR.to_truth(header, rows, product)
    except Exception as exc:  # noqa: BLE001
        check("the adapter's derivation (truth_roslyn.to_truth) reads the captured raw sites", False, repr(exc))
        return
    bad = TR.test_rule_disagreements(TR.touched_paths(rows) + list(CS_TEST_PROBES), product)
    n_paths = len(set(TR.touched_paths(rows)) | set(TR.PROBE_PATHS) | set(CS_TEST_PROBES))
    check(f"truth_roslyn.is_test_path == build/sync.py _is_test_path on every path the self-test touches, "
          f"truth_roslyn.PROBE_PATHS and the gate's own C# probes ({n_paths} paths; a product change to the rule must "
          f"be replicated, or truth_roslyn refuses every run)", not bad, bad[:10])
    consts = {k: (getattr(TR, k, None), getattr(S, k, None)) for k in ("_TEST_DIR_PARTS", "_DOTNET_TEST_DIR_SUFFIXES")}
    check("the replica's constants == the product's (truth_roslyn._TEST_DIR_PARTS == build/sync.py's, and "
          "_DOTNET_TEST_DIR_SUFFIXES the same tuple): the .NET test-project directory rule is copied, not "
          "re-derived", all(a is not None and b is not None and (set(a) == set(b) if "PARTS" in k else tuple(a) ==
                                                                tuple(b)) for k, (a, b) in consts.items()), consts)
    probes = list(TR.PROBE_PATHS)
    comps = [c for p in probes for c in p.replace("\\", "/").split("/")[:-1]]
    uncovered = ([f"suffix {s}" for s in getattr(S, "_DOTNET_TEST_DIR_SUFFIXES", ()) if not any(
        c.lower().endswith(s) for c in comps)] + [f"dir {d}" for d in getattr(S, "_TEST_DIR_PARTS", ()) if not any(
            c.lower() == d for c in comps)])
    lookalike = [p for p in probes if not product(p) and any("test" in c.lower() or "spec" in c.lower() for c in
                                                            p.replace("\\", "/").split("/")[:-1])]
    cased = [p for p in probes if product(p) and any(c != c.lower() and c.lower().endswith(
        tuple(getattr(S, "_DOTNET_TEST_DIR_SUFFIXES", ()) or ("\0",))) for c in p.replace("\\", "/").split("/")[:-1])]
    check("truth_roslyn.PROBE_PATHS exercises every branch of the product's directory rule: each test-directory name "
          "and each .NET test-project suffix as a directory component, a suffix in another letter case, and production "
          "look-alikes the rule must NOT match (a 'test' / 'spec' inside a production directory name)",
          not uncovered and lookalike and cased, {"uncovered": uncovered, "look-alikes": lookalike[:4],
                                                  "case variants": cased[:4]})
    try:
        recorded = (header.get("resolver") or {}).get("tool_sources_sha256")
        current = TR.sources_sha256()
    except Exception as exc:  # noqa: BLE001
        recorded, current = None, repr(exc)
    check("the captured self-test was made by the tool built from the CURRENT sources: selftest/sites.jsonl's "
          "resolver.tool_sources_sha256 == truth_roslyn.sources_sha256() (an edit of Program.cs, the csproj, "
          "global.json or nuget.config that was not re-captured fails here, without .NET too)",
          isinstance(recorded, str) and len(recorded) == 64 and recorded == current, {"recorded": recorded,
                                                                                     "current sources": current})
    shape_bad = []
    for r in doc["rows"]:
        if not isinstance(r.get("dispatch"), list) or not isinstance(r.get("defs"), list):
            shape_bad.append(f"{r.get('file')}:{r.get('line')}: defs / dispatch not lists")
            continue
        for e in r["defs"] + r["dispatch"]:
            if set(e) != set(TR.ENTRY_KEYS):
                shape_bad.append(f"{r['file']}:{r['line']}: entry keys {sorted(e)}")
    check("every truth row carries `dispatch` (a list, empty when none) and every defs and dispatch entry has exactly "
          "the same shape (truth_roslyn.ENTRY_KEYS: the call-linking/truth/1 def fields file, line, alt_lines, type, "
          "name plus full_name, interface, body)",
          not shape_bad and {"file", "line", "alt_lines", "type", "name"} <= set(TR.ENTRY_KEYS), shape_bad[:6])
    if C is not None:
        try:
            C.check_truth_shape(doc)
            refusal = None
        except ValueError as exc:
            refusal = str(exc)
        check("the kit reads the reference as a dispatch truth (_common.has_dispatch; check_truth_shape accepts it)",
              C.has_dispatch(doc) and refusal is None, refusal)
    forms_bad = [f"{f}:{ln} {nm}: {len(rs)} rows" if len(rs) != 1 else f"{f}:{ln} {nm} ({why_}): {rs[0].get('form')}"
                 for f, ln, nm, form, why_ in CS_FORMS
                 for rs in [_cs_rows(doc, f, ln, nm, "object_creation" if form == "ctor" else "invocation")]
                 if len(rs) != 1 or rs[0].get("form") != form]
    nameof = _cs_rows(doc, CS_OH, 51)
    check(f"forms, read from the syntax (bare / this / super / name_attr / recv / ctor / other; {len(CS_FORMS)} sites), "
          f"and nameof(...) is no site", not forms_bad and not nameof, (forms_bad, len(nameof)))
    ds_bad = [f"{f}:{ln} {nm} ({w}): {[r.get('defstack') for r in rs]}"
              for f, ln, nm, want, w in CS_DEFSTACK for rs in [_cs_rows(doc, f, ln, nm)]
              if len(rs) != 1 or rs[0].get("defstack") != want]
    check(f"defstack: the enclosing members, outermost first, [qualified name, first line counting attribute lists, "
          f"alt lines] ({len(CS_DEFSTACK)} sites)", not ds_bad, ds_bad)
    for label, cases in CS_CATEGORIES.items():
        bad = _cs_expect(doc, cases)
        check(f"{label} ({len(cases)} sites)", not bad, bad)
    gate_l_msbuild(TR, kit, doc)
    # the adapter's derivations on synthetic raw rows (pure functions; the captured file is not involved)
    t = {"name": "Find", "full_name": "I.Find", "interface": True, "decls": [{"file": "src/I.cs", "line": 2,
                                                                             "alt_lines": [], "body": False}]}
    raw = {"file": "src/A.cs", "line": 3, "col": 4, "form": "name_attr", "name": "Find", "resolution": "symbol",
           "site_kind": "invocation", "target": t, "dispatch_raw": [
               {"file": "src/Impl.cs", "line": 5, "alt_lines": [4], "name": "Find", "full_name": "Impl.Find",
                "interface": False, "body": True},
               {"file": "src/Abs.cs", "line": 7, "name": "Find", "full_name": "Abs.Find", "body": False},
               {"file": "tests/Fake.cs", "line": 3, "name": "Find", "full_name": "Fake.Find", "body": True},
               {"file": "src/Impl.cs", "line": 5, "name": "Find", "full_name": "Impl.Find", "body": True}]}
    over = {"file": "src/B.cs", "line": 9, "col": 4, "form": "this", "name": "Run", "resolution": "symbol",
            "site_kind": "invocation", "target": {"name": "Run", "full_name": "B.Run", "interface": False,
                                                  "decls": [{"file": "src/B.cs", "line": 4, "body": True}]},
            "dispatch_raw": [{"file": "src/B.cs", "line": 4, "name": "Run", "body": True},
                             {"file": "src/D.cs", "line": 2, "name": "Run", "body": True}]}
    try:
        r1, r1t = TR.truth_row(raw, product), TR.truth_row(dict(raw, file="tests/T.cs"), product)
        r2 = TR.truth_row(over, product)
        r3 = TR.truth_row(dict(raw, resolution="candidates"), product)
        r4 = TR.truth_row(dict(raw, site_kind="object_creation", dispatch_raw=[]), product)
        got = {"defs": (_cs_locs(r1["defs"]), [d["interface"] for d in r1["defs"]], [d["type"] for d in r1["defs"]]),
               "dispatch": _cs_locs(r1["dispatch"]), "dropped": r1["dispatch_dropped"],
               "test_caller": (_cs_locs(r1t["dispatch"]), r1t["dispatch_dropped"]),
               "static_target": (_cs_locs(r2["dispatch"]), r2["dispatch_dropped"]),
               "candidates": (r3["defs"], r3["dispatch"]), "creation": [d["type"] for d in r4["defs"]],
               "alt": [d["alt_lines"] for d in r1["dispatch"]]}
    except Exception as exc:  # noqa: BLE001
        got = repr(exc)
    check("the adapter on synthetic raw rows: defs = the symbol's declarations (an object creation: type class; "
          "candidates only: none); dispatch drops bodiless targets, S's own location and, for a non-test caller only, "
          "test-file targets, each once",
          got == {"defs": ([["src/I.cs", 2]], [True], ["function"]), "dispatch": [["src/Impl.cs", 5]],
                  "dropped": {"bodiless": 1, "test_file": 1},
                  "test_caller": ([["src/Impl.cs", 5], ["tests/Fake.cs", 3]], {"bodiless": 1}),
                  "static_target": ([["src/D.cs", 2]], {"static_target": 1}), "candidates": ([], []),
                  "creation": ["class"], "alt": [[4]]}, got)
    if C is not None:
        gate_l_scoring(C, D, doc)
    gate_l_live(TR, kit, base)


def gate_l_scoring(C, D, doc) -> None:
    """The kit's C# scoring (PREREG_CS §8.5) on the reference's own output: T_s / T_d, the classification, the G4c
    interface fact, delta's verdict and G2d."""
    dump = _cs_synthetic_dump()
    try:
        P = C.truth_pairs_cs(doc, dump["nodes"])
        Ts, Td = set(P["Ts"]), set(P["Td"])
    except Exception as exc:  # noqa: BLE001
        check("_common.truth_pairs_cs reads the reference truth", False, repr(exc))
        return
    want_s = {("h", "iF"), ("h", "iS"), ("h", "lg"), ("t", "iF")}
    want_d = {("h", "oF"), ("h", "oS"), ("h", "sl"), ("t", "oF"), ("t", "fF")}
    check("T_s holds the static pairs (Handle -> IOrderRepository.Find / .Save, BaseHandler.Log; the test's Finds -> "
          "IOrderRepository.Find); T_d the dispatch pairs minus T_s (the implementers, the override, the test's fake); "
          "the non-test caller's fake is in neither",
          want_s <= Ts and want_d <= Td and not (Ts & Td) and ("h", "fF") not in Ts | Td,
          {"Ts": sorted(Ts), "Td": sorted(Td)})
    name_of = {n["key"]: n["name"] for n in dump["nodes"]}
    verdict = {p: C.classify_pair_cs(p, P["Ts"], P["Td"], P["by_cn"], name_of)
               for p in (("h", "iF"), ("h", "oF"), ("h", "fF"), ("h", "cp"))}
    check("§8.5's classification: Handle -> IOrderRepository.Find confirmed, -> OrderRepository.Find "
          "dispatch-confirmed, -> FakeOrderRepository.Find (a test fake) refuted, -> Members.Compute (no such site) "
          "unverified", verdict == {("h", "iF"): "confirmed", ("h", "oF"): C.DISPATCH_CONFIRMED,
                                    ("h", "fF"): "refuted", ("h", "cp"): "unverified"}, verdict)
    def ref_iface(r):   # the reference's own answer: the row's dispatch rule, else its static defs' `interface`
        return r.get("dispatch_rule") == "interface" if r.get("dispatch") else any(
            d.get("interface") is True for d in r.get("defs") or [])
    unknown = [f"{r['file']}:{r['line']} {r.get('name')} ({r.get('dispatch_rule')}): _static_iface -> "
               f"{C._static_iface(r)!r}, the reference says {ref_iface(r)}" for r in doc["rows"]
               if (r.get("dispatch") or r.get("defs")) and C._static_iface(r) is not ref_iface(r)]
    check("G4c's input (and §8.5's fallback stratum's) is computable from the reference: for every resolved row and "
          "every row with dispatch targets, the kit's _common._static_iface (\"does the site bind statically to an "
          "interface member\") is the reference's own answer (the dispatch rule `interface`; the static defs' "
          "`interface`), never None", not unknown and any(r.get("dispatch") for r in doc["rows"]),
          f"{len(unknown)} rows, e.g. {unknown[:6]}")
    if D is not None:
        base = _cs_synthetic_dump()
        var = _cs_synthetic_dump([("h", "iF", "INFERRED"), ("h", "oF", "INFERRED"), ("h", "fF", "INFERRED"),
                                  ("h", "cp", "INFERRED")])
        try:
            dl = D.delta(base, var, doc)
            items = {(i["caller"], i["callee"]): (i.get("verdict"), i.get("verdict_static"))
                     for i in dl["items"]["added"]}
            g2d = (dl.get("cs") or {}).get("g2d_added_nontest_to_test")
        except Exception as exc:  # noqa: BLE001
            items, g2d = repr(exc), None
        check("delta.py on the reference: the verdict covers dispatch-confirmed (the static-only verdict beside it reads "
              "the implementer edge as refuted), and G2d counts the non-test caller's edge to the test fake (1)",
              items == {("h", "iF"): ("confirmed", "confirmed"), ("h", "oF"): (C.DISPATCH_CONFIRMED, "refuted"),
                        ("h", "fF"): ("refuted", "refuted"), ("h", "cp"): ("unverified", "unverified")} and g2d == 1,
              (items, g2d))


def _dotnet_problem(TR, header) -> "tuple[str | None, str | None]":
    """(dotnet, None) when the live re-run can reproduce the capture — .NET SDK 8.0.202, the 8.0.3 reference packs, the
    captured packages (Microsoft.CodeAnalysis 4.8.0 and its closure) in the local packages folder, and the runtime the
    capture recorded as the one `dotnet` rolls forward to — else (None, why not)."""
    dotnet = TR.dotnet_exe()
    if not dotnet:
        return None, "no `dotnet` on PATH (or DOTNET)"

    def run(*args):
        try:
            p = subprocess.run([dotnet, *args], capture_output=True, text=True, timeout=120,
                               env=dict(os.environ, DOTNET_CLI_TELEMETRY_OPTOUT="1", DOTNET_NOLOGO="1"))
            return p.stdout if p.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            return None
    sdks = run("--list-sdks")
    if sdks is None:
        return None, f"`{dotnet}` does not run"
    if not re.search(rf"^{re.escape(TR.WANT_SDK)} \[", sdks, re.M):
        return None, f".NET SDK {TR.WANT_SDK} is not installed"
    rts = re.findall(r"^Microsoft\.NETCore\.App (\d+)\.(\d+)\.(\d+) \[(.+)\]$", run("--list-runtimes") or "", re.M)
    eight = sorted(((int(c), v) for a, b, c, v in rts if (a, b) == ("8", "0")), key=lambda x: x[0])
    if not eight:
        return None, "no .NET 8.0 runtime"
    roots = {Path(v).parent.parent for _c, v in eight}
    for pack in ("Microsoft.NETCore.App.Ref", "Microsoft.AspNetCore.App.Ref"):
        if not any((r / "packs" / pack / "8.0.3" / "ref" / "net8.0").is_dir() for r in roots):
            return None, f"the reference pack {pack} 8.0.3 is not installed"
    res = header.get("resolver") or {}
    pkgs = Path(os.environ.get("NUGET_PACKAGES") or (Path.home() / ".nuget" / "packages"))
    missing = [f"{n} {v}" for n, v in sorted((res.get("packages") or {}).items())
               if not (pkgs / n.lower() / v).is_dir()]
    if missing or not res.get("packages"):
        return None, f"not in the local packages folder {pkgs}: {missing or 'the capture names no package'}"
    runtime = f"8.0.{eight[-1][0]}"
    if res.get("runtime") != runtime:
        return None, (f"the capture records runtime {res.get('runtime')}, `dotnet` here rolls forward to {runtime}: "
                      f"re-capture with `truth_roslyn.py selftest --update` to compare on this machine")
    return dotnet, None


def gate_l_live(TR, kit: Path, base: Path) -> None:
    """The live re-run: the tool on the self-test corpus must reproduce the committed sites.jsonl and truth.json byte
    for byte. Run from a temporary copy of the tool (its bin/ copied when built from the current sources, else built
    there), so the gate writes nothing into this repository."""
    try:
        header, _rows = TR.read_sites(kit / "truth_roslyn" / "selftest" / "sites.jsonl")
    except Exception as exc:  # noqa: BLE001
        check("L live: the captured header reads", False, repr(exc))
        return
    dotnet, why = _dotnet_problem(TR, header)
    if dotnet is None:
        skip("L live: truth_roslyn selftest re-run (.NET)", why)
        return
    tk = base / "tr_live"
    tool = tk / "truth_roslyn"
    try:
        tool.mkdir(parents=True)
        for f in ("truth_roslyn.py", "_common.py"):
            shutil.copy2(kit / f, tk / f)
        for f in TR.SOURCES:
            shutil.copy2(kit / "truth_roslyn" / f, tool / f)
        shutil.copytree(kit / "truth_roslyn" / "selftest", tool / "selftest")
        if not TR.needs_build():
            shutil.copytree(kit / "truth_roslyn" / "bin", tool / "bin")
        env = dict(os.environ, CALL_LINKING_BUILD=str(BUILD), CALL_LINKING_REPO=str(REPO), PYTHONUTF8="1",
                   PYTHONHASHSEED="0")
        t0 = time.time()
        p = subprocess.run([sys.executable, "-X", "utf8", str(tk / "truth_roslyn.py"), "selftest"], cwd=str(tk),
                           capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=1800)
        tail = (p.stdout + p.stderr)[-1500:]
        check(f"L live: the reference tool (.NET SDK {TR.WANT_SDK}, runtime {header['resolver'].get('runtime')}, "
              f"Roslyn {TR.WANT_ROSLYN}) re-run on the self-test corpus reproduces the committed sites.jsonl and "
              f"truth.json byte for byte, and expect.json holds ({time.time() - t0:.0f} s"
              f"{'' if TR.needs_build() is False else ', built in a temp copy'})",
              p.returncode == 0 and "selftest: PASS" in p.stderr, tail)
    except Exception as exc:  # noqa: BLE001 — RED, never a crash of the section
        check("L live: truth_roslyn selftest re-run", False, f"{exc!r}\n{traceback.format_exc()[-600:]}")


# =====================================================================================================
# M — the kit's other D3-cs rules (PREREG_CS §8.6 "Kit changes"), on the reference's self-test and synthetic inputs
# =====================================================================================================
def gate_m(mods: dict, kit: Path) -> None:
    print("\n-- M: the kit's D3-cs rules — compare_calls' C# fields and pool --family=cs, impact_spot's T_s ∪ T_d "
          "reference and exactly-one-node check, audit_sample's C# call shapes / sheet / removed_all_exact, "
          "offline_calls' interface-mode rule --")
    TR, C, CC, IS, AS, OC = (mods.get(m) for m in ("truth_roslyn", "_common", "compare_calls", "impact_spot",
                                                    "audit_sample", "offline_calls"))
    if None in (TR, C, CC, IS, AS, OC):
        check("the kit modules M reads import (section A)", False)
        return
    try:
        header, rows = TR.read_sites(kit / "truth_roslyn" / "selftest" / "sites.jsonl")
        doc = TR.to_truth(header, rows, _fresh_sync()._is_test_path)
    except Exception as exc:  # noqa: BLE001
        check("the self-test truth reads (section L)", False, repr(exc))
        return
    edges = [("h", "iF", "INFERRED"), ("h", "oF", "INFERRED"), ("h", "fF", "AMBIGUOUS"), ("h", "cp", "INFERRED")]
    dump = _cs_synthetic_dump(edges)
    P = C.truth_pairs_cs(doc, dump["nodes"])
    try:
        s = CC.score(doc, dump)
        got = {k: s.get(k) for k in ("truth_kind", "G", "confirmed", "dispatch_confirmed", "refuted", "unverified",
                                     "recall", "dispatch_recall", "refuted_share", "ambiguous_share")}
        got["static_only"] = {k: (s.get("static_only") or {}).get(k) for k in ("confirmed", "refuted", "unverified")}
        got["T"] = (s.get("T_s"), s.get("T_d"))
    except Exception as exc:  # noqa: BLE001
        s, got = None, repr(exc)
    want = {"truth_kind": "dispatch", "G": 4, "confirmed": 1, "dispatch_confirmed": 1, "refuted": 1, "unverified": 1,
            "recall": round(1 / len(P["Ts"]), 4), "dispatch_recall": round(1 / len(P["Td"]), 4),
            "refuted_share": 0.25, "ambiguous_share": 0.25,
            "static_only": {"confirmed": 1, "refuted": 2, "unverified": 1}, "T": (len(P["Ts"]), len(P["Td"]))}
    check("compare_calls on a dispatch truth (§8.6 G): confirmed / dispatch-confirmed / refuted / unverified, recall over "
          "T_s, dispatch recall over T_d, the refuted and AMBIGUOUS shares, the static-only classification beside it "
          "(the implementer edge reads refuted there)", got == want, {"got": got, "want": want})
    try:
        sp = CC.score(doc, dump, ["App/"])
        pl = CC.pool([s, s], "cs")
        forms = C.RECEIVER_FORMS["cs"]
        rn = 2 * sum((s["by_form"].get(f) or {}).get("n", 0) for f in forms)
        rh = 2 * sum((s["by_form"].get(f) or {}).get("hit", 0) for f in forms)
        got = (pl.get("receiver_forms"), pl.get("receiver_n"), pl.get("receiver_hit"), pl.get("dispatch_n"),
               pl.get("dispatch_hit"))
        jedi = {"by_form": {"recv": {"n": 1, "hit": 0}}, "flags": {}}
        refusals = (CC.pool_problem([("a", s), ("b", jedi)], "cs"), CC.pool_problem([("a", s)], "py"),
                    CC.pool_problem([("a", s), ("b", sp)], "cs"), CC.pool_problem([("a", s), ("b", s)], "cs"))
        ok_ref = [r[0] if r else None for r in refusals] == [2, 2, 2, None]
    except Exception as exc:  # noqa: BLE001
        got, rn, rh, ok_ref, refusals = repr(exc), None, None, False, None
    check("compare_calls pool --family=cs: the receiver forms name_attr + recv + this pooled over T_s and the dispatch "
          "recall over T_d; it refuses a jedi / tsc score, a dispatch score under another family, and a mix of "
          "whole-graph and --src-prefix scores (exit 2)",
          got == (list(forms), rn, rh, 2 * len(P["Td"]), 2) and ok_ref, (got, (rn, rh), refusals))
    ref = C.truth_as_dump(doc, dump)
    grades = {(a, b): g for a, b, g in ref["edges"]}
    r = IS.spot(ref, dump, "OrderRepository.Find@" + IMPL)
    check("impact_spot's reference for a dispatch truth is T_s ∪ T_d (truth_as_dump: TRUTH / TRUTH_DISPATCH): "
          "OrderRepository.Find's hop-1 callers are Handle and the test's Finds, both through dispatch",
          grades.get(("h", "iF")) == "TRUTH" and grades.get(("h", "oF")) == "TRUTH_DISPATCH"
          and set(IS.callers(ref, {"oF"})) == {"h", "t"} and r["ref"] == 2 and r["hit"] == 1,
          (sorted(grades.items())[:8], r))
    two = _cs_synthetic_dump(edges)
    two["nodes"].append(dict(next(n for n in two["nodes"] if n["key"] == "oF"), key="oF2", start_line=99))
    base_sat = _cs_synthetic_dump([("h", "oF", "INFERRED"), ("t", "oF", "INFERRED")])
    probs = {k: IS.spot_problems(doc, d, sym)[1] for k, d, sym in (
        ("ok", dump, "OrderRepository.Find@" + IMPL), ("two nodes", two, "OrderRepository.Find@" + IMPL),
        ("no caller", dump, "Members.Compute@App/Members.cs"), ("saturated", base_sat, "OrderRepository.Find@" + IMPL))}
    check("impact_spot.spot_problems (§8.3's substitution checks): a spot passes; one mapping to two nodes, one the "
          "reference gives no hop-1 caller, and one whose V0 recall is already 1 (SATURATED) are each named",
          probs["ok"] == [] and any("exactly one" in p for p in probs["two nodes"])
          and any("no hop-1 caller" in p for p in probs["no caller"])
          and any("SATURATED" in p for p in probs["saturated"]), probs)
    shapes = {"x.Get(1)": True, "x?.Get(1)": True, "Get(1)": True, "x.Get<Order>(1)": True, "x?.Get<int>(2)": True,
              "Map<Dictionary<string, List<int>>>(y)": True, "Pick<(int, string)>(t)": True, "FooGet(1)": False,
              "x.GetAll(1)": False, "if (a < Get && b > (c))": False, "var g = Get;": False}
    names = {k: ("Map" if k.startswith("Map") else "Pick" if k.startswith("Pick") else "Get") for k in shapes}
    got = {k: AS._cs_call_in(k, names[k]) for k in shapes}
    check("audit_sample's C# call-site lines (§8.6 A): `M(`, `?.M(` and `M<...>(` with nested type arguments and tuples "
          "match; a longer name, a comparison and a method group do not", got == shapes,
          {k: v for k, v in got.items() if v != shapes[k]})
    sheet_cs, sheet_py = AS.render_sheet("x", [], cs=True), AS.render_sheet("x", [], cs=False)
    check("the C# audit sheet carries the §8.6 A sentence verbatim, a non-C# sheet does not",
          AS.CS_SHEET_SENTENCE in sheet_cs and AS.CS_SHEET_SENTENCE not in sheet_py)
    b = {"removed": {"n": 10, "false": 10, "wilson_lb": 0.7225}, "removed_confirmed": {"n": 5, "false": 5}}
    census = {"removed": {"population": 10, "sampled": 10}, "removed_confirmed": {"population": 5, "sampled": 5}}
    sampled = {"removed": {"population": 40, "sampled": 10}, "removed_confirmed": {"population": 5, "sampled": 5}}
    try:
        got = (AS.removed_all(b, census)["lower_bound"], AS.removed_all_exact(b, census)["lower_bound"],
               AS.removed_all_exact(b, sampled)["lower_bound"])
    except Exception as exc:  # noqa: BLE001
        got = repr(exc)
    check("removed_all_exact (§8.6 A, reading 2): a stratum audited in full counts at its exact share (10/10 + 5/5 -> "
          "1.0) where removed_all keeps the Wilson bound (0.8150); a stratum sampled below its population keeps its "
          "Wilson bound in both", got == (round((10 * 0.7225 + 5) / 15, 4), 1.0, round((40 * 0.7225 + 5) / 45, 4)), got)
    left = {k: OC._switches_left_off({"DEEPMAP_TYPED_CALLS_IFACE": f}, {"typed_iface": e}) for k, (f, e) in {
        "static asked, static on": ("static", ["static"]), "static asked, both on": ("static", ["impl", "static"]),
        "0 asked, none on": ("0", []), "0 asked, static on": ("0", ["static"]),
        "static asked, not reported": ("static", None)}.items()}
    check("offline_calls' interface-mode rule (§8.4): DEEPMAP_TYPED_CALLS_IFACE must be turned on exactly as asked "
          "(static -> [static]; 0 -> []) or the arm is inert, VOID",
          {k: bool(v) for k, v in left.items()} == {"static asked, static on": False, "static asked, both on": True,
                                                     "0 asked, none on": False, "0 asked, static on": True,
                                                     "static asked, not reported": True}, left)
    S = _fresh_sync()
    eff = {}
    for label, env in (("VT-cs", {}), ("VT-cs-ifs", {"DEEPMAP_TYPED_CALLS_IFACE": "static"}),
                       ("VT-cs-if0", {"DEEPMAP_TYPED_CALLS_IFACE": "0"})):
        with _env(**_graph_flags_cleared()), _env(DEEPMAP_TYPED_CALLS="csharp", **env):
            e = OC.effective_switches(S)
            eff[label] = (e.get("typed_languages"), e.get("typed_iface"))
    blocks = {k: tuple(map(repr, v)) for k, v in eff.items()}
    check("offline_calls' effective block reports the interface-dispatch modes through the product: VT-cs, VT-cs-ifs "
          "and VT-cs-if0 differ (never an inert pair); csharp on in each", len(set(blocks.values())) == 3
          and all(v[0] and "csharp" in v[0] for v in eff.values()) and eff["VT-cs-ifs"][1] == ["static"]
          and eff["VT-cs-if0"][1] == [], eff)


# =====================================================================================================
# N — the D3-cs rules on synthetic rows (PREREG_CS AC5-AC16): the teeth the kit check's mutants found missing
# =====================================================================================================
# One synthetic C# corpus in the kit's truth / dump shapes, written from PREREG_CS §8.2 / §8.5 and the amendments
# (never from the kit's output): a caller Service.Run that calls an interface member (two implementers, one a test
# fake), an abstract member with an override, the override itself through a concrete receiver (so one pair is in
# T_s AND among the dispatch targets), a virtual member with an override; Service.Other whose only `Get` site binds
# statically OUTSIDE the corpus (no def) with an in-corpus implementer; a test caller; a non-production caller; a
# `.CS` (upper-case) caller; a Python caller.
NF = {"svc": "src/Shop/Service.cs", "irepo": "src/Shop/IRepo.cs", "sql": "src/Shop/SqlRepo.cs",
      "mem": "src/Shop/MemRepo.cs", "shape": "src/Shop/Shape.cs", "circle": "src/Shop/Circle.cs",
      "order": "src/Shop/Order.cs", "big": "src/Shop/BigOrder.cs", "fake": "tests/Shop.Fakes/FakeRepo.cs",
      "rtest": "tests/Shop.Tests/RepoTests.cs", "demo": "samples/Demo.cs", "legacy": "src/Shop/Legacy.CS",
      "tool": "tools/gen.py"}
N_NODES = [("m_svc", "Module", "Service", NF["svc"], 1), ("run", "Method", "Service.Run", NF["svc"], 10),
           ("oth", "Method", "Service.Other", NF["svc"], 20), ("iG", "Method", "IRepo.Get", NF["irepo"], 3),
           ("iS", "Method", "IRepo.Save", NF["irepo"], 4), ("sG", "Method", "SqlRepo.Get", NF["sql"], 5),
           ("sS", "Method", "SqlRepo.Save", NF["sql"], 9), ("mG", "Method", "MemRepo.Get", NF["mem"], 5),
           ("shA", "Method", "Shape.Area", NF["shape"], 3), ("cA", "Method", "Circle.Area", NF["circle"], 4),
           ("oT", "Method", "Order.Total", NF["order"], 3), ("bT", "Method", "BigOrder.Total", NF["big"], 3),
           ("fG", "Method", "FakeRepo.Get", NF["fake"], 5), ("tst", "Method", "RepoTests.Finds", NF["rtest"], 7),
           ("demo", "Method", "Demo.Show", NF["demo"], 3), ("leg", "Method", "Legacy.Call", NF["legacy"], 2),
           ("gen", "Function", "main", NF["tool"], 1)]


def _ne(f, line, name, iface=False, body=True, typ="function"):
    """A defs / dispatch entry of the reference's contract (truth_roslyn.ENTRY_KEYS)."""
    return {"file": f, "line": line, "alt_lines": [], "type": typ, "name": name, "full_name": name,
            "interface": iface, "body": body}


def _nrow(f, line, caller, cline, name, defs, dispatch, rule, form="name_attr", col=8):
    return {"file": f, "line": line, "col": col, "form": form, "name": name, "defstack": [[caller, cline, []]],
            "defs": defs, "dispatch": dispatch, "dispatch_rule": rule}


def n_truth() -> dict:
    run, oth = ("Service.Run", 10), ("Service.Other", 20)
    rows = [
        _nrow(NF["svc"], 12, *run, "Get", [_ne(NF["irepo"], 3, "Get", True, False)], [_ne(NF["sql"], 5, "Get")],
              "interface"),
        _nrow(NF["svc"], 13, *run, "Save", [_ne(NF["irepo"], 4, "Save", True, False)], [_ne(NF["sql"], 9, "Save")],
              "interface"),
        _nrow(NF["svc"], 14, *run, "Area", [_ne(NF["shape"], 3, "Area", body=False)], [_ne(NF["circle"], 4, "Area")],
              "override"),
        _nrow(NF["svc"], 15, *run, "Area", [_ne(NF["circle"], 4, "Area")], [], None),
        _nrow(NF["svc"], 16, *run, "Total", [_ne(NF["order"], 3, "Total")], [_ne(NF["big"], 3, "Total")], "override"),
        _nrow(NF["svc"], 22, *oth, "Get", [], [_ne(NF["mem"], 5, "Get")], "interface"),
        _nrow(NF["rtest"], 9, "RepoTests.Finds", 7, "Get", [_ne(NF["irepo"], 3, "Get", True, False)],
              [_ne(NF["fake"], 5, "Get"), _ne(NF["sql"], 5, "Get")], "interface"),
    ]
    return {"schema": "call-linking/truth/1", "resolver": {}, "rows": rows}


def n_dump(edges=(), nodes=None) -> dict:
    return {"schema": "call-linking/offline-calls/1", "nodes": [_n(*x) for x in (nodes or N_NODES)],
            "edges": sorted([list(e) for e in edges]), "flags": {}}


def _ncheck(name, fn, want=None):
    """check(name, fn() == want) — or fn() truthy when want is None; a crash is RED with its reason."""
    try:
        got = fn()
        ok = bool(got) if want is None else got == want
    except Exception as exc:  # noqa: BLE001 — RED, never a crash of the section
        got, ok = f"{type(exc).__name__}: {exc}", False
    check(name, ok, got if want is None else {"got": got, "want": want})


def _sheet_csv(path: Path, verdicts: dict) -> None:
    _write(path, "id,verdict,note\n" + "".join(f"{k},{v},\n" for k, v in sorted(verdicts.items())))


def gate_n(mods: dict, kit: Path, base: Path, build: Path = None) -> None:
    print("\n-- N: the D3-cs rules on synthetic rows (PREREG_CS AC5-AC16): T_s / T_d, the refuted test, G4c, G2d, N2, "
          "the C# VOID rule, the audit strata and bounds, the reference error rate, the spot list, the G5b manifest "
          "writer, VRC-cs's flags, the kit hash --")
    build = build or BUILD
    TR, C, CC, D, IS, AS, OC, V = (mods.get(m) for m in ("truth_roslyn", "_common", "compare_calls", "delta",
                                                         "impact_spot", "audit_sample", "offline_calls", "variants"))
    if None in (TR, C, CC, D, IS, AS, OC, V):
        check("the kit modules N reads import (section A)", False)
        return
    nb = base / "n_rules"
    T = n_truth()
    dump0 = n_dump()
    name_of = {n["key"]: n["name"] for n in dump0["nodes"]}

    # ---- AC5: the row fields the scoring reads --------------------------------------------------------------------
    rows = {(r["line"], r["name"]): r for r in T["rows"]}
    _ncheck("AC5: _common._static_iface reads `dispatch_rule` for a row with dispatch targets (interface -> True, "
            "override -> False; a static target declared OUTSIDE the corpus, no def, still says interface) and the "
            "defs' `interface` for a row without; an unknown rule or a non-boolean `interface` is None, never guessed",
            lambda: [C._static_iface(rows[k]) for k in ((12, "Get"), (14, "Area"), (15, "Area"), (16, "Total"),
                                                         (22, "Get"))]
            + [C._static_iface(dict(rows[(12, "Get")], dispatch_rule="weird")),
               C._static_iface(dict(rows[(15, "Area")], defs=[dict(_ne(NF["circle"], 4, "Area"), interface="yes")]))],
            [True, False, False, False, True, None, None])
    mixed = [_ne(NF["circle"], 4, "Area"), _ne(NF["irepo"], 3, "Get", True, False)]
    _ncheck("AC5: a row without dispatch targets whose defs disagree (one an interface member, one not) binds "
            "statically to an interface member, in either order: ANY def whose `interface` is true makes it true, "
            "never only all of them (_static_iface; static_dispatchable reads it the same way)",
            lambda: [f(dict(rows[(15, "Area")], defs=d)) for f in (C._static_iface, C.static_dispatchable)
                     for d in (mixed, mixed[::-1])], [True, True, True, True])
    raw = {"file": "src/A.cs", "line": 3, "col": 4, "form": "name_attr", "name": "Find", "resolution": "symbol",
           "site_kind": "invocation", "target": {"name": "Find", "full_name": "I.Find", "interface": True,
                                                 "decls": [{"file": "src/I.cs", "line": 2, "body": False}]},
           "dispatch_raw": [{"file": "src/Impl.cs", "line": 5, "name": "Find", "full_name": "Impl.Find",
                             "interface": False, "body": True}]}
    hdr = {"resolver": {"microsoft_codeanalysis": TR.WANT_ROSLYN, "microsoft_codeanalysis_csharp": TR.WANT_ROSLYN}}

    def contract():
        good = TR.to_truth(hdr, [dict(raw, dispatch_rule="interface")], TR.is_test_path)
        bad_rule = TR.to_truth(hdr, [dict(raw)], TR.is_test_path)
        no_list = {"rows": [dict(good["rows"][0], dispatch=None)]}
        extra_key = {"rows": [dict(good["rows"][0], defs=[dict(good["rows"][0]["defs"][0], extra=1)])]}
        str_flag = {"rows": [dict(good["rows"][0], dispatch=[dict(good["rows"][0]["dispatch"][0], interface="no")])]}
        conv = (TR.convert(hdr, [dict(raw, dispatch_rule="interface")], TR.is_test_path)[1],
                TR.convert(hdr, [dict(raw)], TR.is_test_path)[0])
        return ([len(TR.contract_problems(d)) for d in (good, bad_rule, no_list, extra_key, str_flag)],
                conv[0] is None, conv[1] is None)
    _ncheck("AC5: truth_roslyn.contract_problems — every row carries `dispatch` and `defs` lists, a row with dispatch "
            "targets carries dispatch_rule interface|override, every entry has exactly ENTRY_KEYS with boolean "
            "interface / body; convert() refuses a truth that breaks it (a row with targets and no rule)", contract,
            ([0, 1, 1, 1, 1], True, True))

    # ---- §8.5: T_s, T_d, the refuted test (K4's M09, M10) -----------------------------------------------------------
    def pairs():
        P = C.truth_pairs_cs(T, dump0["nodes"])
        return (sorted(P["Ts"]), sorted(P["Td"]), {f"{a}|{n}": sorted(v) for (a, n), v in sorted(P["by_cn"].items())})
    want_ts = sorted([("run", "iG"), ("run", "iS"), ("run", "shA"), ("run", "cA"), ("run", "oT"), ("tst", "iG")])
    want_td = sorted([("run", "sG"), ("run", "sS"), ("run", "bT"), ("oth", "mG"), ("tst", "sG"), ("tst", "fG")])
    _ncheck("§8.5: T_s = the static pairs; T_d = the dispatch pairs MINUS T_s — Run -> Circle.Area is a dispatch "
            "target of `shape.Area()` and the static target of `circle.Area()`, so it is in T_s only; the refuted "
            "test's sites hold the static AND the dispatch targets (Other's `Get` binds only by dispatch)", pairs,
            (want_ts, want_td, {"oth|Get": ["mG"], "run|Area": ["cA", "shA"], "run|Get": ["iG", "sG"],
                                "run|Save": ["iS", "sS"], "run|Total": ["bT", "oT"],
                                "tst|Get": ["fG", "iG", "sG"]}))

    def verdicts():
        P = C.truth_pairs_cs(T, dump0["nodes"])
        return {f"{a}->{b}": C.classify_pair_cs((a, b), P["Ts"], P["Td"], P["by_cn"], name_of)
                for a, b in (("oth", "sG"), ("oth", "mG"), ("run", "cA"), ("run", "fG"), ("leg", "sG"), ("tst", "fG"))}
    _ncheck("§8.5 / AC6: Other -> SqlRepo.Get is REFUTED (Other's only Get site resolves by dispatch, to MemRepo.Get); "
            "Other -> MemRepo.Get dispatch-confirmed; Run -> Circle.Area confirmed (T_s wins); a non-test caller -> the "
            "test fake refuted; a caller with no such site unverified; the test caller -> its fake dispatch-confirmed",
            verdicts, {"oth->sG": "refuted", "oth->mG": C.DISPATCH_CONFIRMED, "run->cA": "confirmed",
                       "run->fG": "refuted", "leg->sG": "unverified", "tst->fG": C.DISPATCH_CONFIRMED})

    # ---- compare_calls on the synthetic truth: T_d's size and G4c (K4's M10, M21) --------------------------------
    ext = [("run", "sG", "EXTRACTED"), ("run", "sS", "EXTRACTED"), ("run", "bT", "EXTRACTED"), ("run", "cA", "EXTRACTED"),
           ("oth", "mG", "INFERRED"), ("oth", "sG", "AMBIGUOUS")]

    def g4c():
        s = CC.score(T, n_dump(ext))
        return (s["T_s"], s["T_d"], s["confirmed"], s["dispatch_confirmed"], s["refuted"],
                {k: s["g4c"][k] for k in ("extracted_dispatch", "extracted_dispatch_iface", "exact")},
                sorted(map(tuple, s["g4c"]["pairs"])))
    _ncheck("G4c: of the EXTRACTED edges, Run -> SqlRepo.Get / .Save / BigOrder.Total are dispatch-only (T_d; Run -> "
            "Circle.Area is in T_s, so not one), and two of them come from a site bound statically to an INTERFACE "
            "member (BigOrder.Total's is the override rule): extracted_dispatch 3, through an interface 2, exact",
            g4c, (6, 6, 1, 4, 1, {"extracted_dispatch": 3, "extracted_dispatch_iface": 2, "exact": True},
                  [("run", "sG"), ("run", "sS")]))
    itot = _ne("src/Shop/ITotal.cs", 3, "Total", True, False)      # an interface member with no graph node

    def with_row(row, before16=False):
        t2 = copy.deepcopy(T)
        i16 = next(i for i, r in enumerate(t2["rows"]) if r["line"] == 16)
        t2["rows"].insert(i16 if before16 else i16 + 1, row)
        return t2

    def g4c_both_sites():
        out = []
        for before in (False, True):      # the interface site after, then before, the virtual site of row 16
            t2 = with_row(_nrow(NF["svc"], 17, "Service.Run", 10, "Total", [itot], [_ne(NF["big"], 3, "Total")],
                                "interface"), before)
            g = CC.score(t2, n_dump(ext))["g4c"]
            out.append((C.truth_pairs_cs(t2, dump0["nodes"])["td_iface"].get(("run", "bT")),
                        g["extracted_dispatch_iface"], g["exact"]))
        return out
    _ncheck("G4c (td_iface): a dispatch pair produced by BOTH a site bound statically to an interface member and a "
            "virtual site (Run -> BigOrder.Total, the interface site after or before the virtual one) reads True — True "
            "wins over False, the strict reading — so G4c counts it: 3 through an interface, exact", g4c_both_sites,
            [(True, 3, True), (True, 3, True)])

    def g4c_broken_rule():
        t2 = copy.deepcopy(T)
        for r in t2["rows"]:
            if r["line"] == 16:
                r["dispatch_rule"] = "virtual"          # not `interface` / `override`: the row breaks AC5
        g = CC.score(t2, n_dump(ext))["g4c"]
        return (g["extracted_dispatch"], g["extracted_dispatch_iface"], g["exact"], g["iface_unknown"])
    _ncheck("G4c: an EXTRACTED dispatch-only edge whose producing row breaks AC5 (dispatch targets, a rule that is "
            "neither interface nor override: Run -> BigOrder.Total) leaves the count NOT exact (exact False, "
            "iface_unknown 1): the true count could be higher", g4c_broken_rule, (3, 2, False, 1))

    def g4c_any_site():
        out = []
        for t2 in (T, with_row(_nrow(NF["svc"], 17, "Service.Run", 10, "Total", [itot], [], None))):
            s = CC.score(t2, n_dump(ext))
            g = s["g4c"]
            text = CC.format_score(s)
            line = next((ln for ln in text.splitlines() if ln.startswith("G4c:")), "")
            out.append((g["extracted_dispatch_iface"], g.get("extracted_dispatch_iface_any_site"),
                        g.get("any_site_decisive"),
                        bool(re.search(r"any same-named site[^=]*=%d\b" % (g.get("extracted_dispatch_iface_any_site")
                                                                         or -1), line)) and "never decisive" in line))
        return out
    _ncheck("AC24: beside G4c's decisive producing-site count compare_calls reports the count over ANY same-named site "
            "of the caller (extracted_dispatch_iface_any_site, any_site_decisive False, printed on the G4c line): Run's "
            "interface-bound `Total` site that produces nothing lifts it to 3 while the producing-site count stays 2 "
            "(BigOrder.Total comes from the virtual site); without that site the two agree", g4c_any_site,
            [(2, 2, False, True), (2, 3, False, True)])

    def g4c_any_site_rows():
        out = []
        for row in (
                # (a) a same-named site the reference resolved to nothing: no defs, no dispatch targets, no rule
                #     (_static_iface None) — it binds to no interface member, so it never counts
                _nrow(NF["svc"], 17, "Service.Run", 10, "Total", [], [], None),
                # (b) a site bound statically to an interface member declared OUTSIDE the corpus (no def), with a
                #     dispatch target (Order.Total, already a static pair of Run's, so no new T_d pair) and the rule
                #     `interface` — it binds to an interface member, so it counts though it has no def
                _nrow(NF["svc"], 17, "Service.Run", 10, "Total", [], [_ne(NF["order"], 3, "Total")], "interface")):
            t2 = with_row(row)
            g = CC.score(t2, n_dump(ext))["g4c"]
            out.append((g["extracted_dispatch_iface"], g.get("extracted_dispatch_iface_any_site"),
                        ("run", "Total") in C.truth_pairs_cs(t2, dump0["nodes"])["iface_sites"]))
        return out
    _ncheck("AC24 (iface_sites): a same-named site with no defs and no dispatch target (resolution none) is NOT an "
            "interface-bound site (the any-site count stays 2); a site with no def whose dispatch targets come by the "
            "`interface` rule (an external interface's member) IS one (the any-site count rises to 3); the decisive "
            "producing-site count stays 2 in both", g4c_any_site_rows, [(2, 2, False), (2, 3, True)])

    # ---- delta: G2d (K4's M16) ----------------------------------------------------------------------------------
    def g2d():
        var = n_dump([("run", "fG", "AMBIGUOUS"), ("demo", "fG", "AMBIGUOUS"), ("tst", "fG", "INFERRED"),
                      ("leg", "sG", "AMBIGUOUS")])
        cs = D.delta(dump0, var, T)["cs"]
        return (C.caller_class(NF["demo"]), cs["g2d_added_nontest_to_test"], sorted(map(tuple, cs["g2d_pairs"])))
    _ncheck("G2d: an added edge from a NON-TEST caller — production (Run) and non-production (samples/Demo.cs) — to a "
            "node declared in a test file counts; a test caller's does not, nor an edge between production nodes",
            g2d, ("nonprod", 2, [("demo", "fG"), ("run", "fG")]))

    # ---- AC9: the dispatch stratum's fallback (static_dispatchable / impl_fallback) ------------------------------
    def fallback(truth):
        var = n_dump([("run", "sG", "INFERRED"), ("run", "bT", "INFERRED"), ("run", "cA", "INFERRED"),
                      ("run", "iG", "INFERRED"), ("oth", "sG", "INFERRED"), ("leg", "sG", "INFERRED")])
        added = D.delta(dump0, var, truth)["items"]["added"]
        items, why = AS.impl_fallback(added, truth, var["nodes"])
        return sorted((i["caller"], i["callee"]) for i in items), why is None
    _ncheck("AC9: static_dispatchable — either dispatch rule (interface, override) is dispatchable, a row without "
            "dispatch targets answers by its defs' `interface`, an unknown rule is None",
            lambda: [C.static_dispatchable(rows[k]) for k in ((12, "Get"), (14, "Area"), (15, "Area"), (16, "Total"),
                                                               (22, "Get"))]
            + [C.static_dispatchable(dict(rows[(16, "Total")], dispatch_rule=None))],
            [True, True, False, True, True, None])
    _ncheck("AC9: the fallback stratum (--impl-fallback) over delta(V0, VT-cs) is the added edges (a, b) whose caller "
            "has a site of b's name with an interface, virtual, abstract or override static target OTHER than b: "
            "Run -> SqlRepo.Get, BigOrder.Total (an override), Circle.Area (abstract Shape.Area's site), Other -> "
            "SqlRepo.Get (an external interface member); never Run -> IRepo.Get (b is the static target) or a caller "
            "without such a site", lambda: fallback(T),
            (sorted([("run", "sG"), ("run", "bT"), ("run", "cA"), ("oth", "sG")]), True))

    def fallback_unknown():
        t2 = copy.deepcopy(T)
        for r in t2["rows"]:
            if r["name"] == "Total":
                r["dispatch_rule"] = None
        return fallback(t2)[1]
    _ncheck("AC9: a site whose row does not say its rule through AC5's fields makes the fallback NOT computable "
            "exactly (a problem, never a silent guess)", fallback_unknown, False)

    # ---- AC10 / §8.6 A: the removal strata, the reference's judged rows (K4's M11) --------------------------------
    def strata():
        full = n_dump([("oth", "mG", "INFERRED"), ("run", "iG", "INFERRED"), ("run", "fG", "AMBIGUOUS")])
        items = D.delta(full, dump0, T)["items"]
        picked = AS.draw(items, 20261022, "n_g39", 40)
        return (sorted((i["caller"], i["callee"]) for i in picked["removed_confirmed"]),
                sorted((i["caller"], i["callee"]) for i in picked["removed"]), AS.strata(items, picked))
    _ncheck("AC10: every removed edge the reference confirms, DISPATCH-confirmed included (Other -> MemRepo.Get), is "
            "audited exhaustively beside the confirmed one; only the refuted removal is in the sampled stratum",
            strata, ([("oth", "mG"), ("run", "iG")], [("run", "fG")],
                     {"removed": {"population": 1, "sampled": 1},
                      "removed_confirmed": {"population": 2, "sampled": 2}}))
    key3 = {"label": "n_g39", "rows": {
        "r1": {"caller": "oth", "callee": "mG", "verdict": C.DISPATCH_CONFIRMED, "buckets": ["added"]},
        "r2": {"caller": "run", "callee": "iG", "verdict": "confirmed", "buckets": ["added"]},
        "r3": {"caller": "run", "callee": "fG", "verdict": "refuted", "buckets": ["added"]}}}
    sh3 = {"r1": "TRUE", "r2": "FALSE", "r3": "FALSE"}
    _ncheck("§8.6 A: the reference's judged rows include dispatch-confirmed (it predicts TRUE): 3 judged, 1 wrong",
            lambda: {k: AS.score(key3, [sh3, dict(sh3)])["resolver"][k] for k in ("judged", "wrong")},
            {"judged": 3, "wrong": 1})

    # ---- §8.6 A bounds: a census at its exact share; the pooled bound (K4's M12, M13) -----------------------------
    def bounds():
        census = AS.exact_bounds({"added": {"n": 5, "true": 5, "false": 0, "wilson_lb": 0.5655}},
                                 {"bucket_sizes": {"added": 5}, "sampled": {"added": 5}})["added"]
        sampled = AS.exact_bounds({"added": {"n": 5, "true": 5, "false": 0, "wilson_lb": 0.5655}},
                                  {"bucket_sizes": {"added": 50}, "sampled": {"added": 5}})["added"]
        return (census["census"], census["lower_bound"], sampled["census"], sampled["lower_bound"])
    _ncheck("§8.6 A (exact_bounds): a bucket audited in full (sampled == population) counts at its exact share, "
            "lower bound 1.0; sampled below its population it keeps its Wilson bound", bounds,
            (True, 1.0, False, 0.5655))
    lb_b = round(C.wilson(38, 40)[1], 4)

    def pooled():
        a = {"label": "jf_g39", "exact": {"added": {"population": 10, "audited": 10, "good": 10, "share": 1.0,
                                                    "lower_bound": 1.0, "census": True}}}
        b = {"label": "es_g39", "exact": {"added": {"population": 90, "audited": 40, "good": 38, "share": 0.95,
                                                    "lower_bound": lb_b, "census": False}}}
        r = AS.pool_scores([("a", a), ("b", b)], "added")
        return (r["population"], r["share"], r["lower_bound"], r["summed_rows"]["decisive"])
    _ncheck("G3a / G3b pooled (pool_scores): each corpus's population times its share and times its LOWER BOUND (a "
            "census at its exact share, else Wilson), over the total population; summed-row Wilson beside it, never "
            "decisive", pooled, (100, round((10 + 90 * 0.95) / 100, 4), round((10 + 90 * lb_b) / 100, 4), False))

    # ---- AC29: every threshold on the integer inputs; the 4-decimal fields are display only -----------------------
    # 120 of 126: Wilson's lower bound is 0.8999983..., printed (4 decimals) as 0.9 — at the 90% threshold the display
    # passes and the exact bound does not. A full-precision value is compared to 1e-12 (relative): far finer than the
    # 4-decimal display, independent of the order a sum is taken in.
    w120 = C.wilson(120, 126)[1]

    def close(got, want):
        return isinstance(got, float) and abs(got - want) <= 1e-12 * max(1.0, abs(want))

    def audit_key(label, bucket, good, bad, population, verdict=None):
        rows_ = {f"{label}{i}": {"caller": f"a{i}", "callee": f"b{i}", "verdict": verdict, "buckets": [bucket]}
                 for i in range(good + bad)}
        want = "FALSE" if bucket in ("removed", "removed_confirmed") else "TRUE"
        other = "TRUE" if want == "FALSE" else "FALSE"
        sheet = {f"{label}{i}": (want if i < good else other) for i in range(good + bad)}
        key = {"label": label, "rows": rows_, "bucket_sizes": {bucket: population}, "sampled": {bucket: good + bad}}
        if bucket in ("removed", "removed_confirmed"):
            key["strata"] = {"removed": {"population": population, "sampled": good + bad},
                             "removed_confirmed": {"population": 0, "sampled": 0}}
        return key, [sheet, dict(sheet)]

    def exact_at_boundary():
        out = {}
        for tag, pop, lb in (("sampled 126 of 1000", 1000, w120), ("census 126 of 126", 126, 120 / 126)):
            e = AS.score(*audit_key("x_g39_", "added", 120, 6, pop))["exact"]["added"]
            out[tag] = (e["lower_bound"], close(e.get("lower_bound_exact"), lb), close(e.get("share_exact"), 120 / 126),
                        e["good"], e["audited"], e.get("lower_bound_exact", 1) < 0.9, e.get("share_fraction"),
                        e.get("lower_bound_fraction"))
        return out
    _ncheck("AC29 (exact_bounds): a stratum sampled below its population carries its Wilson lower bound at FULL "
            "precision from the integers (lower_bound_exact 0.8999983 < 0.9: the threshold fails) beside the 4-decimal "
            "display (lower_bound 0.9); a census carries its exact share 120/126 (share_fraction, and lower_bound_fraction "
            "for the census only: a Wilson bound is irrational); good / audited are the integer inputs",
            exact_at_boundary, {"sampled 126 of 1000": (0.9, True, True, 120, 126, True, [20, 21], None),
                                "census 126 of 126": (round(120 / 126, 4), True, True, 120, 126, False, [20, 21],
                                                      [20, 21])})

    def removed_exact_at_boundary():
        r = AS.score(*audit_key("x_g39_", "removed", 120, 6, 1000))["removed_all_exact"]
        return (r["lower_bound"], close(r.get("lower_bound_exact"), w120), r.get("lower_bound_exact", 1) < 0.9,
                close(r.get("share_exact"), 120 / 126), close((r["strata"].get("removed") or {}).get("lb_exact"), w120),
                r.get("share_fraction"), r.get("lower_bound_fraction"))
    _ncheck("AC29 (removed_all_exact): its population-weighted lower bound is computed from the integers at full "
            "precision (the sampled stratum's Wilson bound 0.8999983, never the rounded 0.9 score() prints): "
            "lower_bound_exact < 0.9 beside the display lower_bound 0.9; share_fraction exact (20/21)",
            removed_exact_at_boundary, (0.9, True, True, True, True, [20, 21], None))

    def old_removed_all(buckets, st):      # the closed decisions' removed_all, written out here (PREREG_CS reading 2)
        num = den = num_lb = 0.0
        parts = {}
        for b in ("removed", "removed_confirmed"):
            e = buckets.get(b)
            pop = int(((st.get(b) or {}).get("population")) or 0)
            if not e or not e.get("n") or pop <= 0:
                continue
            share = e["false"] / e["n"]
            lb = share if b == "removed_confirmed" else (e.get("wilson_lb") or 0.0)
            num, num_lb, den = num + pop * share, num_lb + pop * lb, den + pop
            parts[b] = {"population": pop, "audited": e["n"], "false": e["false"], "share": round(share, 4),
                        "lb_used": round(lb, 4)}
        if not den:
            return {"population": 0, "share": None, "lower_bound": None, "strata": parts}
        return {"population": int(den), "share": round(num / den, 4), "lower_bound": round(num_lb / den, 4),
                "strata": parts}

    def removed_all_unchanged():
        out = []
        # (1 of 4 sampled from 1000: removed_all reads 0.0466 from the rounded Wilson bound 0.0456; the unrounded
        # bound would give 0.0465 — a removed_all moved to full precision changes a closed decision's number)
        for good, bad, pop in ((120, 6, 1000), (10, 0, 10), (3, 4, 40), (1, 3, 1000)):
            key, sheets = audit_key("x_g39_", "removed", good, bad, pop)
            key["rows"]["c0"] = {"caller": "c", "callee": "d", "verdict": "confirmed", "buckets": ["removed_confirmed"]}
            key["strata"]["removed_confirmed"] = {"population": 1, "sampled": 1}
            for s in sheets:
                s["c0"] = "FALSE"
            res = AS.score(key, sheets)
            out.append(res["removed_all"] == old_removed_all(res["buckets"], key["strata"]))
        return out
    _ncheck("PREREG_CS reading 2: removed_all keeps its old computation byte for byte (the sampled stratum at the "
            "Wilson bound score() rounds, the exhaustive one at its share), so the closed decisions recompute "
            "identically", removed_all_unchanged, [True, True, True, True])

    pool_share = (1000 * Fraction(120, 126) + 10 * Fraction(10, 10)) / 1010
    pool_lb = (1000 * w120 + 10 * 1.0) / 1010

    def pooled_exact():
        a = AS.score(*audit_key("a_g39_", "added", 120, 6, 1000))
        b = AS.score(*audit_key("b_g39_", "added", 10, 0, 10))
        r = AS.pool_scores([("a", a), ("b", b)], "added")
        c = AS.score(*audit_key("c_g39_", "added", 120, 6, 126))           # a census: every part of r2 is one
        r2 = AS.pool_scores([("b", b), ("c", c)], "added")
        return (r.get("share_fraction"), close(r.get("share_exact"), float(pool_share)),
                close(r.get("lower_bound_exact"), pool_lb), r["lower_bound"],
                [(p.get("good"), p.get("audited"), p.get("sampled")) for p in r["parts"]], r.get("lower_bound_fraction"),
                (r2.get("share_fraction"), r2.get("lower_bound_fraction")))
    _ncheck("AC29 (pool_scores): the pooled share and lower bound are computed from each corpus's integers (good, "
            "audited, population, sampled) at full precision — share_fraction the exact rational, lower_bound_exact "
            "from the unrounded Wilson bound of 120/126 and the census 10/10 — never from a part's 4-decimal fields; "
            "the parts carry the integers; with every part a census the pooled bound is exact too (lower_bound_fraction "
            "65/68 = (10 + 120) / 136)", pooled_exact,
            ([pool_share.numerator, pool_share.denominator], True, True, round(pool_lb, 4),
             [(120, 126, 126), (10, 10, 10)], None, ([65, 68], [65, 68])))

    def voids_integers():
        out = []
        for wrong, judged in ((1, 10), (1, 11), (2000, 20001), (0, 5), (0, 0)):
            rows_ = {f"r{i}": {"caller": f"a{i}", "callee": f"b{i}", "verdict": "confirmed", "buckets": ["added"]}
                     for i in range(judged)}
            sheet = {f"r{i}": ("FALSE" if i < wrong else "TRUE") for i in range(judged)}
            for i in range(3):            # rows the reference did not judge (unverified): never judged, never wrong
                rows_[f"u{i}"] = {"caller": f"u{i}", "callee": f"w{i}", "verdict": "unverified", "buckets": ["added"]}
                sheet[f"u{i}"] = "FALSE"
            r = AS.score({"label": "v_g39", "rows": rows_}, [sheet, dict(sheet)])["resolver"]
            out.append((r["judged"], r["wrong"], r["error_rate"], r["voids_g_deltas"]))
        return out
    _ncheck("AC29 (score's resolver): the reference error rate voids the corpus's G deltas when wrong x 10 >= judged, "
            "compared on the integers: 1 of 10 voids; 1 of 11 does not; 2000 of 20001 does not although its display "
            "error_rate reads 0.1; 0 wrong of 0 judged (only unverified rows) has no rate and voids nothing",
            voids_integers,
            [(10, 1, 0.1, True), (11, 1, 0.0909, False), (20001, 2000, 0.1, False), (5, 0, 0.0, False),
             (0, 0, None, False)])

    def score_cli_full_precision():
        key, sheets = audit_key("x_g39_", "removed", 120, 6, 1000)
        d = nb / "score_cli_ac29"
        d.mkdir(parents=True, exist_ok=True)
        _write(d / "key.json", json.dumps(key))
        _sheet_csv(d / "s1.csv", sheets[0])
        _sheet_csv(d / "s2.csv", sheets[1])
        rc, out, _err, exc = _cli(AS.main, ["score", f"--key={d / 'key.json'}", f"--sheet={d / 's1.csv'}",
                                            f"--sheet={d / 's2.csv'}", f"--json={d / 'score.json'}"])
        res = json.loads((d / "score.json").read_text(encoding="utf-8")) if (d / "score.json").is_file() else {}
        rx = res.get("removed_all_exact") or {}
        ex = (res.get("exact") or {}).get("removed") or {}
        rxl = [ln for ln in out.splitlines() if ln.startswith("removed_all_exact")]
        bl = [ln for ln in out.splitlines() if ln.strip().startswith("removed ") and "population=" in ln]
        tail = "; full precision (AC29: what a threshold reads) "
        return (rc, exc, close(rx.get("lower_bound_exact"), w120), rx.get("lower_bound"),
                len(rxl) == 1 and rxl[0].endswith(tail + repr(rx.get("lower_bound_exact")))
                and f"(lower bound {rx.get('lower_bound')})" in rxl[0],
                len(bl) == 1 and bl[0].endswith(tail + repr(ex.get("lower_bound_exact"))))
    _ncheck("AC29 (audit_sample score's printout): the removed_all_exact line prints its lower_bound_exact at FULL "
            "precision (0.8999983..., what a threshold reads) beside the rounded display (0.9), like the per-bucket "
            "exact lines", score_cli_full_precision, (0, None, True, 0.9, True, True))

    # ---- AC11: the reference error rate per corpus x engine (union of the main and dispatch samples) -------------
    def sample(label, pairs_, wrong=(), verdict="confirmed", flip=None):
        rows_ = {f"{label}-{i}": {"caller": a, "callee": b, "verdict": verdict, "buckets": ["added"]}
                 for i, (a, b) in enumerate(pairs_)}
        sheet = {f"{label}-{i}": ("FALSE" if (a, b) in wrong else "TRUE") for i, (a, b) in enumerate(pairs_)}
        if flip:
            sheet.update({f"{label}-{i}": flip[1] for i, p in enumerate(pairs_) if p == flip[0]})
        return ({"label": label, "rows": rows_}, [sheet, dict(sheet)], label)
    P10 = [(f"a{i}", f"b{i}") for i in range(10)]
    Q3 = [("q1", "r1"), ("q2", "r2"), ("q3", "r3")]

    def union():
        r = AS.reference_error_rate([sample("c_g39", P10, {P10[0]}), sample("c_g39_impl", [P10[0]] + Q3, {P10[0]}),
                                     sample("c_g51", P10), sample("c_g51_impl", Q3)])
        g = r["engines"]["g39"]
        return (g["judged"], g["wrong"], g["pairs_in_both"], g["unreliable"], r["unreliable"])
    _ncheck("AC11: a pair audited in BOTH of an engine's samples counts once — g39 1 wrong of 13 united pairs (the main "
            "sample alone would read 1/10 = 10%, unreliable): reliable when g51 is too", union, (13, 1, 1, False, False))

    def threshold():
        at = AS.reference_error_rate([sample("c_g39", P10, {P10[0]}), sample("c_g39_impl", P10, {P10[0]}),
                                      sample("c_g51", P10), sample("c_g51_impl", P10)])
        p11 = P10 + [("a10", "b10")]
        below = AS.reference_error_rate([sample("c_g39", p11, {P10[0]}), sample("c_g39_impl", p11, {P10[0]}),
                                         sample("c_g51", p11), sample("c_g51_impl", p11)])
        g51 = AS.reference_error_rate([sample("c_g39", p11), sample("c_g39_impl", p11),
                                       sample("c_g51", P10, {P10[0]}), sample("c_g51_impl", P10, {P10[0]})])
        return (at["engines"]["g39"]["unreliable"], at["unreliable"], below["unreliable"], g51["unreliable"])
    _ncheck("AC11: exactly 10% (1 of 10) is unreliable and 1 of 11 is not (the threshold compared exactly); EITHER "
            "engine at 10% or more makes the corpus unreliable", threshold, (True, True, False, True))

    def conflict():
        r = AS.reference_error_rate([sample("c_g39", P10), sample("c_g39_impl", [P10[0]] + Q3, flip=(P10[0], "FALSE")),
                                     sample("c_g51", P10), sample("c_g51_impl", Q3)])
        g = r["engines"]["g39"]
        return (g["judged"], g["wrong"], len(g["conflicts"]))
    _ncheck("AC11: a pair agreed TRUE in one sample and FALSE in the other counts ONCE, as wrong (the strict reading), "
            "and is listed", conflict, (13, 1, 1))

    # AC26's three readings: rows of (pair, reference verdict, (reviewer 1, reviewer 2)) per sample
    def votes(label, rows_):
        key = {"label": label, "rows": {f"{label}-{i}": {"caller": p[0], "callee": p[1], "verdict": v,
                                                         "buckets": ["added"]} for i, (p, v, _vv) in enumerate(rows_)}}
        return (key, [{f"{label}-{i}": vv[j] for i, (_p, _v, vv) in enumerate(rows_)} for j in (0, 1)], label)
    ok10 = [(p, "confirmed", ("TRUE", "TRUE")) for p in P10]
    disputed, half_f, half_t, split = ("d1", "e1"), ("h1", "k1"), ("h2", "k2"), ("s1", "x1")
    ac26_main = ok10 + [(disputed, "confirmed", ("TRUE", "FALSE")), (half_f, "confirmed", ("FALSE", "FALSE")),
                        (half_t, "confirmed", ("UNSURE", "TRUE")), (split, "refuted", ("TRUE", "TRUE"))]
    ac26_impl = [(disputed, "confirmed", ("UNSURE", "UNSURE")), (half_f, "confirmed", ("TRUE", "UNSURE")),
                 (half_t, "confirmed", ("TRUE", "TRUE")), (split, "refuted", ("FALSE", "FALSE"))]
    ac26_samples = [votes("c_g39", ac26_main), votes("c_g39_impl", ac26_impl), votes("c_g51", ok10),
                    votes("c_g51_impl", ok10[:3])]

    def ac26():
        g = AS.reference_error_rate(ac26_samples)["engines"]["g39"]
        return (g["judged"], g["wrong"], sorted((x["caller"], x["callee"]) for x in g["conflicts"]),
                sorted((x["caller"], x["callee"]) for x in g["wrong_rows"]))
    _ncheck("AC26: a pair the reviewers agreed on in NO sample (a dispute in one, UNSURE in the other) is outside the "
            "rate, neither judged nor wrong; a pair agreed in one sample and not in the other is judged by the agreed one "
            "(FALSE on a confirmed pair: wrong; TRUE: right); a pair agreed TRUE in one and FALSE in the other counts "
            "once, as wrong, and is listed: 13 judged, 2 wrong", ac26,
            (13, 2, [split], sorted([half_f, split])))

    def ac26_cited():
        dirs = []
        for key, sheets, label in ac26_samples:
            d = nb / "refrate_ac26" / label
            _write(d / "key.json", json.dumps(key))
            for i, s in enumerate(sheets, 1):
                _sheet_csv(d / f"sheet_r{i}.csv", s)
            dirs.append(f"--dir={d}")
        rc, out, _err, exc = _cli(AS.main, ["refrate"] + dirs)
        return {"docstring": "AC26" in (AS.reference_error_rate.__doc__ or ""), "rc": (rc, exc),
                "printout": "AC26" in out and "2/13" in out}
    _ncheck("AC26 is cited where the rule lives: reference_error_rate's docstring and `refrate`'s printout (2 wrong of "
            "13 judged on that sample set)", ac26_cited, {"docstring": True, "rc": (0, None), "printout": True})

    def refusals():
        out = []
        for samples in ([sample("c_g39", P10), sample("c_g39", Q3)], [sample("c_g39", P10), sample("d_g51", P10)],
                        [sample("c_g39", P10), sample("c_g39_impl", P10, verdict="refuted")],
                        [sample("c_gXX", P10)]):
            try:
                AS.reference_error_rate(samples)
                out.append("accepted")
            except ValueError:
                out.append("refused")
        missing = AS.reference_error_rate([sample("c_g39", P10), sample("c_g39_impl", Q3), sample("c_g51", P10)])
        return out + [missing["unreliable"]]
    _ncheck("AC11: a sample given twice, samples of two corpora, one pair with two reference verdicts and an unknown "
            "label are refused; with one engine's dispatch sample missing the rate is NOT computable (None)",
            refusals, ["refused", "refused", "refused", "refused", None])

    def refrate_cli():
        out = {}
        for tag, skip in (("all", ()), ("missing", ("c_g51_impl",))):
            dirs = []
            for label, pairs_, wrong in (("c_g39", P10, {P10[0]}), ("c_g39_impl", [P10[0]] + Q3, {P10[0]}),
                                         ("c_g51", P10, ()), ("c_g51_impl", Q3, ())):
                if label in skip:
                    continue
                key, sheets, _l = sample(label, pairs_, wrong)
                d = nb / "refrate" / tag / label
                _write(d / "key.json", json.dumps(key))
                for i, s in enumerate(sheets, 1):
                    _sheet_csv(d / f"sheet_r{i}.csv", s)
                dirs.append(f"--dir={d}")
            js = nb / "refrate" / f"{tag}.json"
            rc = _cli(AS.main, ["refrate"] + dirs + [f"--json={js}"])[0]
            out[tag] = (rc, json.loads(js.read_text(encoding="utf-8"))["unreliable"] if js.is_file() else "no json")
        return out
    _ncheck("AC11 (CLI `audit_sample.py refrate --dir=...`): computed (exit 0, reliable); one sample missing -> exit 3, "
            "not computable", refrate_cli, {"all": (0, False), "missing": (3, None)})

    # ---- AC12 + §8.3: the spot substitution on both engines, and the FINAL list (K4's M24) ------------------------
    es = "dotnet__eShop"

    def spot_fixture(n_reserves=1, no_ref=()):
        """The corpus's spots then its first n_reserves reserves, each a target node with one caller (a reference row
        and a dump edge); a symbol whose index is in no_ref gets no reference row (no hop-1 caller)."""
        syms = list(IS.CS_SPOTS[es]) + list(IS.CS_RESERVES[es][:n_reserves])
        nodes, rows_, edges = [], [], []
        for i, sym in enumerate(syms):
            name, _, f = sym.partition("@")
            cf = f"src/Caller{i}/C{i}.cs"
            nodes += [(f"t{i}", "Method", name, f, 10), (f"c{i}", "Method", f"C{i}.Call", cf, 3)]
            if i not in no_ref:
                rows_.append(_nrow(cf, 5, f"C{i}.Call", 3, name.split(".")[-1], [_ne(f, 10, name.split(".")[-1])], [],
                                   None))
            edges.append((f"c{i}", f"t{i}", "INFERRED"))
        return syms, {"schema": "call-linking/truth/1", "resolver": {}, "rows": rows_}, nodes, edges

    def substitution():
        syms, t, nodes, edges = spot_fixture()
        g39 = n_dump([], nodes)
        g51 = n_dump([edges[0]], nodes)          # the first registered spot is SATURATED on 0.8.51 only
        r = IS.substitute(t, {"g39": g39, "g51": g51}, es)
        return (r["final"] == [syms[5]] + syms[1:5], [s["spot"] for s in r["substitutions"]],
                sorted(r["substitutions"][0]["problems"]) if r["substitutions"] else None)
    _ncheck("§8.3's substitution rule on BOTH engines: a spot SATURATED on 0.8.51 only is replaced by the first reserve "
            "(E-r1) in its place; the others stay", substitution,
            (True, [IS.CS_SPOTS[es][0]], ["g51"]))

    def failing_reserve():
        syms, t, nodes, edges = spot_fixture(n_reserves=2, no_ref=(5,))      # E-r1 has no reference caller
        r = IS.substitute(t, {"g39": n_dump([], nodes), "g51": n_dump([edges[0]], nodes)}, es)
        return (r["final"] == [syms[6]] + syms[1:5], (r["substitutions"] or [{}])[0].get("replacement") == syms[6],
                syms[5] in r["final"], sorted(r["checks"].get(syms[5], {}).get("g39", {}).get("problems") or []))
    _ncheck("§8.3: a reserve that fails the checks is never used — E-r1 (no hop-1 caller) is passed over and E-r2, the "
            "first reserve that passes on both engines, replaces the SATURATED spot", failing_reserve,
            (True, True, False, ["the reference gives it no hop-1 caller"]))

    def final_list():
        syms, t, nodes, edges = spot_fixture()
        d = nb / "spots"
        tp, dp, bp = d / "truth.json", d / "dump.json", d / "base.json"
        _write(tp, json.dumps(t))
        _write(dp, json.dumps(n_dump(edges, nodes)))
        _write(bp, json.dumps(n_dump([], nodes)))
        final = {"corpus": es, "registered": list(IS.CS_SPOTS[es]), "reserves": list(IS.CS_RESERVES[es]),
                 "final": [syms[5], syms[2], syms[1]], "final_ids": ["E-r1", "E3", "E2"], "engines": ["g39", "g51"]}
        fps = {}
        for tag, f in (("ok", final), ("other corpus", dict(final, corpus="jellyfin__jellyfin")),
                       ("a stray symbol", dict(final, final=final["final"] + ["Nope.X@src/x.cs"])),
                       ("one engine", dict(final, engines=["g39"])),
                       ("a repeat", dict(final, final=final["final"] + [syms[1]])),
                       ("a changed registered list", dict(final, registered=list(IS.CS_SPOTS[es])[::-1]))):
            fps[tag] = d / f"final_{len(fps)}.json"
            _write(fps[tag], json.dumps(f))
        js = d / "out.json"
        base_args = [f"--truth={tp}", str(dp), f"--base={bp}"]
        rcs = {"no --final": _cli(IS.main, base_args + [f"--corpus={es}"])[0],
               "--final": _cli(IS.main, base_args + [f"--corpus={es}", f"--final={fps['ok']}", f"--json={js}"])[0],
               "--corpus of another corpus": _cli(IS.main, base_args + ["--corpus=jellyfin__jellyfin",
                                                                        f"--final={fps['ok']}"])[0],
               "--symbols with --final": _cli(IS.main, base_args + [f"--final={fps['ok']}", "--symbols=A.b"])[0]}
        for tag in ("other corpus", "a stray symbol", "one engine", "a repeat", "a changed registered list"):
            rcs[tag] = _cli(IS.main, base_args + [f"--final={fps[tag]}"])[0]
        measured = [r["symbol"] for r in json.loads(js.read_text(encoding="utf-8"))[0]["spots"]] if js.is_file() else None
        return rcs, measured == final["final"]
    _ncheck("AC12: impact_spot on a C# --corpus measures its FINAL list (--final=<id>/spots_final.json): without "
            "--final it refuses (exit 2); with it, exactly that list in its order (a reserve included); a final list "
            "of another corpus, holding a symbol `substitute` cannot write, settled on one engine, repeating a symbol, "
            "whose `registered` list is not the corpus's CS_SPOTS, or given with --symbols is refused (exit 2)",
            final_list,
            ({"no --final": 2, "--final": 0, "--corpus of another corpus": 2, "--symbols with --final": 2,
              "other corpus": 2, "a stray symbol": 2, "one engine": 2, "a repeat": 2, "a changed registered list": 2},
             True))

    def ac12_symbols():
        syms, t, nodes, edges = spot_fixture()
        d = nb / "spots_symbols"
        tp, dp, bp, js = d / "truth.json", d / "dump.json", d / "base.json", d / "nonc.json"
        _write(tp, json.dumps(t))
        _write(dp, json.dumps(n_dump(edges, nodes)))
        _write(bp, json.dumps(n_dump([], nodes)))
        args, sym = [f"--truth={tp}", str(dp), f"--base={bp}"], f"--symbols={syms[1]}"

        def refused(res):     # the AC12 refusal, told apart from the usage text (which also mentions AC12)
            rc, _o, err, exc = res
            return (rc, exc is None and "[impact_spot] REFUSED" in err and "AC12" in err and es in err
                    and "usage:" not in err)
        usage = _cli(IS.main, args + ["--corpus=no_such_corpus"])     # no spot list at all: the usage path
        nonc = _cli(IS.main, args + ["--corpus=pallets__werkzeug", sym, "--one-node", f"--json={js}"])
        rows_ = json.loads(js.read_text(encoding="utf-8"))[0]["spots"] if js.is_file() else []
        return {"no --symbols": refused(_cli(IS.main, args + [f"--corpus={es}"])),
                "--symbols": refused(_cli(IS.main, args + [f"--corpus={es}", sym])),
                "usage": (usage[0], "usage:" in usage[2], "[impact_spot] REFUSED" in usage[2]),
                "--one-node --symbols on a non-C# corpus": (nonc[0], [(r["symbol"], r.get("one_node")) for r in rows_])}
    _ncheck("AC12: a C# --corpus without --final is REFUSED with or without an ad-hoc --symbols list (exit 2, the AC12 "
            "message naming the corpus — never the usage text); a non-C# corpus still measures an ad-hoc --symbols "
            "list, --one-node included", ac12_symbols,
            {"no --symbols": (2, True), "--symbols": (2, True), "usage": (2, True, False),
             "--one-node --symbols on a non-C# corpus": (0, [(spot_fixture()[0][1], True)])})

    # ---- AC29: impact_spot compares hop-1 recall on the integers (hit, ref); its 4-decimal fields are display ------
    def big_spot(d, hits):
        """A Python-shaped corpus: target T.M (t.py) with 20001 reference callers (c.py lines 1..20001); a dump whose
        first `hits` callers have the edge."""
        nodes = [("tM", "Method", "T.M", "t.py", 1)] + [(f"c{i}", "Function", f"c{i}", "c.py", i + 1)
                                                         for i in range(20001)]
        p = d / f"dump_{hits}.json"
        if not p.is_file():
            _write(p, json.dumps(n_dump([(f"c{i}", "tM", "INFERRED") for i in range(hits)], nodes)))
        tp = d / "truth.json"
        if not tp.is_file():
            _write(tp, json.dumps({"schema": "call-linking/truth/1", "resolver": {}, "rows": [
                {"file": "c.py", "line": i + 1, "col": 0, "form": "name_attr", "name": "M",
                 "defstack": [[f"c{i}", i + 1, []]], "defs": [_d("t.py", 1, "function", "M")]} for i in range(20001)]}))
        return tp, p

    def spot_integers():
        d = nb / "spots_int"
        tp, b10000 = big_spot(d, 10000)
        _tp, v10001 = big_spot(d, 10001)
        _tp, s20000 = big_spot(d, 20000)
        _tp, f20001 = big_spot(d, 20001)
        out = {}
        for tag, var, base_ in (("10001 vs 10000 of 20001", v10001, b10000), ("20000 of 20001 at the base", s20000,
                                                                               s20000),
                                ("20001 of 20001 at the base", f20001, f20001)):
            js = d / f"out_{len(out)}.json"
            rc = _cli(IS.main, [f"--truth={tp}", "--symbols=T.M@t.py", str(var), f"--base={base_}", f"--json={js}"])[0]
            r = json.loads(js.read_text(encoding="utf-8"))[0]["spots"][0] if js.is_file() else {}
            out[tag] = (rc, r.get("recall"), r.get("base_recall"), r.get("improved"), r.get("saturated"))
        truth = json.loads(tp.read_text(encoding="utf-8"))
        out["spot_problems at 20000 / 20001"] = [IS.spot_problems(truth, json.loads(p.read_text(encoding="utf-8")),
                                                                  "T.M@t.py")[1] for p in (s20000, f20001)]
        return out
    _ncheck("AC29 (impact_spot): `improved` compares hit / ref exactly (10001 of 20001 over 10000 of 20001 improves "
            "although both display 0.5); SATURATED needs hit >= ref (20000 of 20001 displays 1.0 and can still improve; "
            "20001 of 20001 is saturated, exit 1), in main and in spot_problems' substitution check", spot_integers,
            {"10001 vs 10000 of 20001": (0, 0.5, 0.5, True, False),
             "20000 of 20001 at the base": (0, 1.0, 1.0, False, False),
             "20001 of 20001 at the base": (1, 1.0, 1.0, False, True),
             "spot_problems at 20000 / 20001": [[], ["SATURATED: its V0 hop-1 recall is already 1"]]})

    def recall_or_0(r):      # today's reading of a spot without reference callers: `recall or 0`
        return Fraction(r["hit"], r["ref"]) if r["ref"] else Fraction(0)
    imp_cases = [({"hit": 1, "ref": 3}, {"hit": 0, "ref": 3}), ({"hit": 0, "ref": 0}, {"hit": 0, "ref": 0}),
                 ({"hit": 1, "ref": 3}, {"hit": 0, "ref": 0}), ({"hit": 0, "ref": 0}, {"hit": 1, "ref": 2}),
                 ({"hit": 2, "ref": 4}, {"hit": 1, "ref": 2}), ({"hit": 3, "ref": 4}, {"hit": 2, "ref": 3}),
                 ({"hit": 0, "ref": 5}, {"hit": 0, "ref": 0})]
    _ncheck("AC29 (impact_spot.improved): hop-1 recall strictly above the base's, read as exact fractions hit / ref, a "
            "spot without reference callers reading 0 exactly as `recall or 0` did (no reference caller never improves)",
            lambda: [IS.improved(r, b) for r, b in imp_cases], [recall_or_0(r) > recall_or_0(b) for r, b in imp_cases])

    # ---- N2 and the `.cs` test, case-insensitive (K4's M18, M23) -------------------------------------------------
    base_e = [("gen", "sG", "INFERRED"), ("leg", "sG", "AMBIGUOUS"), ("run", "iG", "INFERRED")]

    def n2():
        b = n_dump(base_e)
        cases = {"a non-.cs caller's edge added": n_dump(base_e + [("gen", "iG", "INFERRED")]),
                 "a non-.cs caller's edge regraded": n_dump([("gen", "sG", "EXTRACTED")] + base_e[1:]),
                 "a non-.cs caller's edge removed": n_dump(base_e[1:]),
                 "only .cs / .CS callers' edges changed": n_dump([base_e[0], ("leg", "sG", "INFERRED"),
                                                                  ("leg", "iG", "INFERRED"), ("run", "sG", "INFERRED")])}
        return {k: OC.identity(b, v, rule="N2")["holds"] for k, v in cases.items()}
    _ncheck("N2 (offline_calls.identity): every edge whose caller's file is not `.cs` must be identical in pair AND "
            "grade — an added, a regraded or a removed one fails; changes from `.cs` callers (an upper-case `.CS` "
            "included) do not", n2, {"a non-.cs caller's edge added": False, "a non-.cs caller's edge regraded": False,
                                     "a non-.cs caller's edge removed": False,
                                     "only .cs / .CS callers' edges changed": True})

    def cs_case():
        upper = {"schema": "call-linking/truth/1", "rows": [{"file": "src/X.CS", "line": 1, "col": 0, "form": "bare",
                                                             "name": "m", "defstack": [], "defs": []}]}
        try:
            C.check_truth_shape(upper)
            refused = False
        except ValueError:
            refused = True
        return ([C.is_cs_path(p) for p in ("a/B.CS", "a/b.Cs", "a/b.cs", "a/b.csx", "a/b.cs.txt")], refused,
                OC.is_csharp_corpus(n_dump([], [("x", "Method", "X.m", "src/X.CS", 1)])))
    _ncheck("the `.cs` test is case-insensitive (PREREG_CS §8.6 N): `.CS` / `.Cs` are C#, `.csx` / `.cs.txt` are "
            "not; a `.CS` truth without dispatch lists is refused; a dump with a `.CS` node is a C# corpus", cs_case,
            ([True, True, True, False, False], True, True))

    # ---- §8.4: the C# VOID rule (offline_calls.csharp_void) ----------------------------------------------------
    def void():
        dn = n_dump([], [("x", "Method", "X.m", "src/X.cs", 1)])

        def d(flag, cl, nodes_dump=dn):
            return dict(nodes_dump, flags={"DEEPMAP_TYPED_CALLS": flag} if flag else {}, call_linking=cl)
        ok = {"langs_disabled": {}, "by_lang": {"csharp": {"files": 3}}}
        py = n_dump([], [("x", "Function", "m", "src/x.py", 1)])
        return [bool(OC.csharp_void(x)) for x in (
            d("csharp", ok), d("csharp", {"langs_disabled": {"csharp": "grammar canary failed"},
                                          "by_lang": {"csharp": {"files": 3}}}),
            d("csharp", {"langs_disabled": {}, "by_lang": {}}), d("python,csharp", {"langs_disabled": {},
                                                                                    "by_lang": {"csharp": {"files": 0}}}),
            d("python", {"langs_disabled": {"csharp": "x"}}), d("csharp", {"langs_disabled": {"csharp": "x"}}, py))]
    _ncheck("§8.4's C# VOID rule (csharp_void): a VT-cs-family dump of a C# corpus whose typed pass disabled csharp, "
            "or read no C# file, is VOID; a clean one is not; nor is a dump whose flags name no csharp or a corpus "
            "without C# source", void, [False, True, True, True, False, False])

    # ---- AC15: the G5b manifest writer, accepted by K's guard -----------------------------------------------------
    def manifest():
        files = {"delta_VT-cs_g39.json": '{"a":1}\n', "audit/jellyfin_g39/key.json": '{"k":2}\n',
                 "audit/jellyfin_g39/sheet_r1.csv": "id,verdict,note\r\nx,TRUE,\r\n", "gates/core.log": "ok\n"}
        kd = nb / "g5b_ok" / "call_linking"
        for rel, data in files.items():
            _write(kd / "cs" / rel, data)
        dry = _cli(OC.main, ["manifest", str(kd / "cs")])
        nothing = not (kd / "cs" / "MANIFEST.sha256").exists() and not (kd / "cs" / ".gitattributes").exists()
        rc, out, _err, exc = _cli(OC.main, ["manifest", str(kd / "cs"), "--write"])
        body = (kd / "cs" / "MANIFEST.sha256").read_bytes() if (kd / "cs" / "MANIFEST.sha256").is_file() else b""
        rels = [ln.split("  ", 1)[1] for ln in body.decode("utf-8").splitlines() if "  " in ln]
        m = re.search(r"- G5b: `cs/MANIFEST\.sha256` sha256 `([0-9a-f]{64})`", out)
        _write(kd / "DECISION_CS.md", "# DECISION_CS\n\n" + (m.group(0) if m else "") + "\n")
        on = nb / "g5b_build_on"
        g = flip_guard(on, kd) if _flip_copy(build, on, 'frozenset({"csharp"})') else {"problems": ["no flip copy"]}
        again = _cli(OC.main, ["manifest", str(kd / "cs"), "--write"])[0]
        return {"dry": (dry[0], nothing), "write": rc, "exc": exc,
                "lines": rels == sorted(list(files) + [".gitattributes"], key=lambda s: s.encode("utf-8")),
                "gitattributes": (kd / "cs" / ".gitattributes").read_bytes() if (kd / "cs" / ".gitattributes").is_file()
                else None, "lf": b"\r" not in body,
                "recorded": bool(m) and m.group(1) == hashlib.sha256(body).hexdigest(),
                "guard": (g.get("active"), g.get("problems"), g.get("entries")), "re-run": again}
    _ncheck("AC15: `offline_calls.py manifest <cs>` writes nothing without --write; with it, cs/.gitattributes (`* "
            "-text`, listed) and cs/MANIFEST.sha256 (`<sha256>  <path relative to cs/>`, byte order, LF; a CRLF "
            "sheet.csv keeps its bytes) and prints the DECISION_CS.md line; section K's G5b guard, with csharp a "
            "default, accepts the result; a re-run on the unchanged folder matches (exit 0)", manifest,
            {"dry": (0, True), "write": 0, "exc": None, "lines": True, "gitattributes": b"* -text\n", "lf": True,
             "recorded": True, "guard": (True, [], 5), "re-run": 0})

    def manifest_refusals():
        out = {}
        for tag, extra in (("a sheet.md", {"audit/x/sheet.md": "# sheet\n"}),
                           ("a dump", {"d.json": json.dumps({"schema": "call-linking/offline-calls/1"})}),
                           ("a truth", {"t.json": json.dumps({"schema": "call-linking/truth/1", "rows": []})}),
                           ("a wrong .gitattributes", {".gitattributes": "* text=auto\n"})):
            cs = nb / f"g5b_{len(out)}" / "cs"
            for rel, data in dict({"delta.json": '{"a":1}\n'}, **extra).items():
                _write(cs / rel, data)
            out[tag] = (_cli(OC.main, ["manifest", str(cs), "--write"])[0], (cs / "MANIFEST.sha256").exists())
        cs = nb / "g5b_changed" / "cs"
        _write(cs / "delta.json", '{"a":1}\n')
        _cli(OC.main, ["manifest", str(cs), "--write"])
        before = (cs / "MANIFEST.sha256").read_bytes()
        _write(cs / "delta.json", '{"a":2}\n')
        out["changed after the manifest"] = (_cli(OC.main, ["manifest", str(cs), "--write"])[0],
                                             (cs / "MANIFEST.sha256").read_bytes() == before)
        _write(nb / "g5b_notcs" / "x.json", "{}\n")
        out["a folder not named cs"] = (_cli(OC.main, ["manifest", str(nb / "g5b_notcs"), "--write"])[0], False)
        return out
    _ncheck("AC15: the writer refuses (exit 2, nothing written) a sheet.md, a dump, a truth file, a wrong "
            "cs/.gitattributes, a folder not named cs/; a manifest that no longer matches is never overwritten "
            "(exit 1)", manifest_refusals,
            {"a sheet.md": (2, False), "a dump": (2, False), "a truth": (2, False), "a wrong .gitattributes": (2, False),
             "changed after the manifest": (1, True), "a folder not named cs": (2, False)})

    def manifest_order():
        cs = nb / "g5b_order" / "cs"
        for rel in ("a.json", "B.json", "sub/b.csv", "sub/Z.csv", "_x.log"):
            _write(cs / rel, "{}\n")
        text, probs = C.g5b_manifest(cs, {".gitattributes": C.G5B_GITATTRIBUTES.encode("ascii")})
        return [ln.split("  ", 1)[1] for ln in text.splitlines()], probs
    _ncheck("AC15: the manifest lists its paths in byte order of their UTF-8 bytes, never case-folded: `.gitattributes`, "
            "`B.json` before `_x.log` before `a.json`, `sub/Z.csv` before `sub/b.csv`", manifest_order,
            ([".gitattributes", "B.json", "_x.log", "a.json", "sub/Z.csv", "sub/b.csv"], []))

    # ---- AC16: VRC-cs = every default language plus csharp -------------------------------------------------------
    def vrc():
        empty, py = nb / "b_empty", nb / "b_py"
        if not (_flip_copy(build, empty, "frozenset()") and _flip_copy(build, py, 'frozenset({"python"})')):
            return "no flip copy"
        now = typed_default_langs(build)[0]
        return {"flag (defaults empty)": V.vrc_cs_flag(empty), "flag (defaults python)": V.vrc_cs_flag(py),
                "csharp alone on python defaults": V.vrc_cs_problem({"DEEPMAP_TYPED_CALLS": "csharp"}, py) is None,
                "python,csharp": V.vrc_cs_problem({"DEEPMAP_TYPED_CALLS": "python,csharp"}, py) is None,
                "an extra language (a union release)":
                    V.vrc_cs_problem({"DEEPMAP_TYPED_CALLS": "python,csharp,typescript"}, py) is None,
                "no DEEPMAP_TYPED_CALLS": V.vrc_cs_problem({}, empty) is None,
                "an ON-word (wave 1: no csharp)": V.vrc_cs_problem({"DEEPMAP_TYPED_CALLS": "1"}, empty) is None,
                "this tree": V.vrc_cs_flag(build) == ",".join([x for x in (now or []) if x != "csharp"] + ["csharp"])
                and now is not None}
    _ncheck("AC16: VRC-cs's DEEPMAP_TYPED_CALLS is every language of the RC tree's _TYPED_DEFAULT_LANGS plus csharp "
            "(`csharp` while the defaults are empty, `python,csharp` on a tree whose defaults are {python}); "
            "variants.py refuses a value that leaves out a default language or csharp, or none at all, and allows "
            "extra languages; this tree's flag follows its own defaults", vrc,
            {"flag (defaults empty)": "csharp", "flag (defaults python)": "python,csharp",
             "csharp alone on python defaults": False, "python,csharp": True,
             "an extra language (a union release)": True, "no DEEPMAP_TYPED_CALLS": False,
             "an ON-word (wave 1: no csharp)": False, "this tree": True})
    def vrc_cli():
        if build != BUILD:          # variants.py reads _common.BUILD: another build/ cannot be shown through the CLI
            return True
        line = f"DEEPMAP_TYPED_CALLS={V.vrc_cs_flag(build)}"
        env = _cli(V.main, ["env", "VRC-cs", "--set", line])[:2]
        printed = _cli(V.main, ["vrc-cs"])[1]
        return {"env": env, "vrc-cs": printed} == {"env": (0, line + "\n"), "vrc-cs": line + "\n"} or {
            "env": env, "vrc-cs": printed, "want": line}
    _ncheck("AC16 (CLI): `variants.py env VRC-cs --set DEEPMAP_TYPED_CALLS=<vrc-cs>` is accepted on this tree, and "
            "`variants.py vrc-cs` prints that flag", vrc_cli, True)

    # ---- §1 / AC7: the kit hash and the reference tool identity ---------------------------------------------------
    def kit_copy(dst: Path) -> Path:
        tool = (kit / "truth_roslyn").resolve()

        def ignore(d, names):
            here = Path(d).resolve()
            return [n for n in names if n == "__pycache__" or (here == tool and n in ("bin", "obj"))]
        shutil.copytree(kit, dst, ignore=ignore)
        return dst

    def indep_hash(root: Path):
        rels = []
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            rel = p.relative_to(root).as_posix()
            if ("__pycache__" in rel.split("/")[:-1] or rel.startswith(("truth_roslyn/bin/", "truth_roslyn/obj/", "cs/"))
                    or rel in ("RESULTS_CS.md", "DECISION_CS.md")):
                continue
            rels.append(rel)
        h = hashlib.sha256()
        for rel in sorted(rels, key=lambda r: r.encode("utf-8")):
            h.update(rel.encode("utf-8") + b"\0" + (root / rel).read_bytes() + b"\0")
        return h.hexdigest(), len(rels)

    def kithash():
        k = kit_copy(nb / "kit" / "call_linking")
        want, n = indep_hash(k)
        first = C.kit_hash(k)
        for rel in ("__pycache__/x.pyc", "truth_roslyn/bin/Release/x.dll", "truth_roslyn/obj/x.g.cs", "RESULTS_CS.md",
                    "DECISION_CS.md", "cs/delta.json", "truth_roslyn/selftest/__pycache__/y.pyc"):
            _write(k / rel, b"junk\n")
        excluded = C.kit_hash(k)["sha256"]
        nested = sorted(p.relative_to(k).as_posix() for p in (k / "truth_roslyn" / "selftest").rglob("*")
                        if p.is_file() and ("obj" in p.relative_to(k).parts or "bin" in p.relative_to(k).parts))
        _write(k / "frozen" / "flask_V0.json", (k / "frozen" / "flask_V0.json").read_bytes() + b" ")
        edited = C.kit_hash(k)["sha256"]
        return {"scheme": first["sha256"] == want and first["files"] == n, "exclusions": excluded == want,
                "a nested obj/ of the self-test corpus is hashed": bool(nested) and all(not C.kit_hash_excluded(r)
                                                                                         for r in nested),
                "an edit changes it": edited != want}
    _ncheck("§1 / AC7 kit hash (_common.kit_hash): sha256 over every file of the kit in byte order of its relative "
            "path (path, NUL, bytes, NUL — recomputed independently here); __pycache__/ at any depth, "
            "truth_roslyn/bin/, truth_roslyn/obj/, RESULTS_CS.md, DECISION_CS.md and cs/ leave it unchanged; any other "
            "file changes it (a self-test corpus's own obj/ folder is a kit file)", kithash,
            {"scheme": True, "exclusions": True, "a nested obj/ of the self-test corpus is hashed": True,
             "an edit changes it": True})

    def identity_():
        files = ("truth_roslyn.py", "truth_roslyn/Program.cs", "truth_roslyn/TruthRoslyn.csproj",
                 "truth_roslyn/global.json", "truth_roslyn/nuget.config")
        h = hashlib.sha256()
        for rel in sorted(files, key=lambda r: r.encode("utf-8")):
            h.update(rel.encode("utf-8") + b"\0" + (kit / rel).read_bytes() + b"\0")
        res, probs = OC.kithash(kit)
        sites = kit / "truth_roslyn" / "selftest" / "truth.json"
        t = json.loads(sites.read_text(encoding="utf-8"))
        good, bad = nb / "id_good.json", nb / "id_bad.json"
        _write(good, json.dumps(dict(t, resolver=dict(t.get("resolver") or {},
                                                      tool_sources_sha256=TR.sources_sha256()))))
        _write(bad, json.dumps(dict(t, resolver=dict(t.get("resolver") or {}, tool_sources_sha256="0" * 64))))
        return {"AC7's files": sorted(TR.IDENTITY_FILES) == sorted(files),
                "== the independent hash": C.reference_identity(kit)["sha256"] == h.hexdigest()
                == TR.tool_identity()["sha256"],
                "kithash agrees": res["reference_identity"]["truth_roslyn_identity"] == h.hexdigest() and not probs,
                "a truth from these sources": not OC.kithash(kit, good)[1],
                "a truth from other sources": bool(OC.kithash(kit, bad)[1])}
    _ncheck("AC7 reference tool identity: the §1 scheme over truth_roslyn.py + truth_roslyn/{Program.cs, "
            "TruthRoslyn.csproj, global.json, nuget.config} (independent recomputation) == `truth_roslyn.py identity` "
            "== offline_calls kithash's; a truth whose resolver.tool_sources_sha256 is not these sources' is a "
            "mismatch", identity_, {"AC7's files": True, "== the independent hash": True, "kithash agrees": True,
                                    "a truth from these sources": True, "a truth from other sources": True})


# =====================================================================================================
# O — run_matrix.sh's C# orchestration on a FAKE repository (AC7's snapshot and guard; AC11 / AC12 / AC13 / AC15 /
#     G5a in the dry-run plans), when bash and git are present
# =====================================================================================================
def _posix(p) -> str:
    return str(p).replace("\\", "/")


def _o_bash(script: Path, env: dict, timeout=900) -> tuple:
    """(rc, stdout, stderr) of `bash script` with every CL_* / DEEPMAP_* variable of this process removed, then env."""
    e = {k: v for k, v in os.environ.items() if not k.startswith(("CL_", "DEEPMAP_"))}
    e.update(PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1", **env)
    try:
        p = subprocess.run(["bash", _posix(script)], capture_output=True, text=True, encoding="utf-8", errors="replace",
                           env=e, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except (OSError, subprocess.SubprocessError) as exc:
        return None, "", repr(exc)


def _o_git(root: Path, *args) -> "subprocess.CompletedProcess":
    return subprocess.run(["git", "-C", str(root), "-c", "core.longpaths=true", "-c", "core.autocrlf=false",
                           "-c", "user.email=gate@scratch", "-c", "user.name=gate", "-c", "commit.gpgsign=false", *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          env=dict(os.environ, GIT_OPTIONAL_LOCKS="0"))


def _o_plan(text: str) -> list:
    """A dry run's printed commands, one per line, each re-joined from its words (bash printf %q escapes a space as
    `\\ ` and a comma as `\\,`: shlex reads them back)."""
    out = []
    for ln in text.splitlines():
        if not ln.strip():
            continue
        try:
            out.append(" ".join(shlex.split(ln)))
        except ValueError:
            out.append(ln.strip())
    return out


def gate_o(kit: Path, base: Path) -> None:
    print("\n-- O: run_matrix.sh's C# orchestration on a FAKE repository — AC7's self-snapshot (deleted tracked files "
          "removed, its index == the working tree's files) and guard (every other phase from the snapshot only), and "
          "the dry-run plans of AC11 / AC12 / AC13 / AC15 / G5a --")
    script = kit / "run_matrix.sh"
    probe = subprocess.run(["bash", "-c", 'test -f "$1" && echo ok', "_", _posix(script)], capture_output=True,
                           text=True) if shutil.which("bash") else None
    gitv = shutil.which("git")
    if probe is None or probe.stdout.strip() != "ok" or not gitv:
        skip("O: run_matrix.sh on a fake repository", "needs a bash that reads this checkout's paths (Git Bash on "
             "Windows) and git")
        return
    o = base / "o_rm"
    w = o / "w"
    tool = (kit / "truth_roslyn").resolve()

    def ign_kit(d, names):
        here = Path(d).resolve()
        return [n for n in names if n == "__pycache__" or (here == tool and n in ("bin", "obj"))]
    shutil.copytree(kit, w / "benchmark" / "call_linking", ignore=ign_kit)
    shutil.copytree(BUILD, w / "build", ignore=shutil.ignore_patterns("__pycache__", "graphify-out", ".deepmap"))
    _write(w / "tests" / "typed_calls_engine_parity_gates.py", (TESTS / "typed_calls_engine_parity_gates.py").read_bytes())
    for n in ("a.txt", "b.txt", "c.txt"):
        _write(w / "docs" / n, f"{n}\n")
    _write(w / ".gitignore", "*.log\n__pycache__/\n")
    made = (_o_git(w, "init", "-q").returncode == 0 and _o_git(w, "add", "-A").returncode == 0
            and _o_git(w, "commit", "-q", "-m", "fake").returncode == 0)
    check("O: the fake repository (the kit, a build/ copy, the engine-parity gate) is committed in the gate's temp dir",
          made)
    if not made:
        return
    (w / "docs" / "a.txt").unlink()                                     # a tracked file deleted, not staged
    _o_git(w, "rm", "-q", "docs/b.txt")                                 # a tracked file deleted, staged
    _write(w / "docs" / "c.txt", "c changed\n")                         # modified, not staged
    _write(w / "docs" / "new.txt", "new\n")                             # untracked, not ignored
    _write(w / "docs" / "ign.log", "ignored\n")                         # ignored
    snap = o / "s"
    env = {"CL_WORK": _posix(o / "k"), "CL_DMHOME": _posix(o / "h"), "CL_MATRIX": "cs", "CL_PY39": _posix(sys.executable),
           "CL_PY51": "py51-placeholder", "CL_SELF_RC": _posix(snap), "CL_CS_SOURCES": _posix(o / "no_sources")}
    rm_wt = w / "benchmark" / "call_linking" / "run_matrix.sh"
    rc, out, err = _o_bash(rm_wt, dict(env, CL_PHASE="self-snapshot"))
    have = set(_o_git(snap, "ls-files").stdout.split()) if snap.is_dir() else set()
    want = (set(_o_git(w, "ls-files", "--cached", "--others", "--exclude-standard").stdout.split())
            - set(_o_git(w, "ls-files", "--deleted").stdout.split()))
    check("O: AC7 self-snapshot — the snapshot's index lists exactly the working tree's files: tracked minus the "
          "deleted ones (an unstaged and a staged deletion are gone from its index and its disk), plus untracked-not-"
          "ignored ones, with the working tree's bytes; no ignored file",
          rc == 0 and have and have == want and not {"docs/a.txt", "docs/b.txt"} & have
          and not (snap / "docs" / "a.txt").exists() and not (snap / "docs" / "b.txt").exists()
          and (snap / "docs" / "c.txt").read_bytes() == b"c changed\n" and (snap / "docs" / "new.txt").is_file()
          and not (snap / "docs" / "ign.log").exists(),
          {"rc": rc, "differ": sorted(have ^ want)[:8], "tail": (out + err)[-500:]})
    rc2, out2, err2 = _o_bash(rm_wt, dict(env, CL_PHASE="self-snapshot"))
    check("O: AC7 — an existing snapshot is never touched (a second self-snapshot is refused)",
          rc2 not in (0, None) and "REFUSED" in out2 + err2, (rc2, (out2 + err2)[-300:]))
    rm_snap = snap / "benchmark" / "call_linking" / "run_matrix.sh"
    dry = dict(env, CL_DRY="1")
    refused = {"the working tree's run_matrix.sh": _o_bash(rm_wt, dict(dry, CL_PHASE="truth")),
               "CL_BUILD naming another build/": _o_bash(rm_snap, dict(dry, CL_PHASE="truth",
                                                                       CL_BUILD=_posix(w / "build"))),
               "no CL_SELF_RC": _o_bash(rm_snap, {k: v for k, v in dict(dry, CL_PHASE="truth").items()
                                                  if k != "CL_SELF_RC"})}
    ok_run = _o_bash(rm_snap, dict(dry, CL_PHASE="truth"))
    check("O: AC7 guard — every C# phase but self-snapshot is refused (exit 2) when run from the working tree's "
          "run_matrix.sh, with CL_BUILD naming another build/, or without CL_SELF_RC; the snapshot's own copy runs it "
          "on the snapshot's build/",
          all(r[0] == 2 and "REFUSED" in r[1] + r[2] for r in refused.values()) and ok_run[0] == 0
          and f"build={_posix(snap)}/build" in (ok_run[1] + ok_run[2]).replace("\\", "/"),
          {k: (r[0], (r[1] + r[2])[-160:]) for k, r in dict(refused, ok=ok_run).items()})
    # the rest phase's plan with no N corpus and one C# arm (each arm and corpus costs a Python start per line on
    # Windows); AC13's and AC12's rules do not depend on how many
    narrow = {"CL_N_CORPORA": " ", "CL_CS_VARIANTS": "VT-cs"}
    plans, secs = {"truth": ok_run}, {}
    for ph in ("spots", "rest", "audit", "manifest", "gates"):
        t0 = time.time()
        plans[ph] = _o_bash(rm_snap, dict(dry, CL_PHASE=ph, **(narrow if ph == "rest" else {}),
                                          **({"CL_CS_DIR": _posix(o / "kitx" / "cs")} if ph == "manifest" else {})))
        secs[ph] = round(time.time() - t0)
    print(f"     (dry-run plans, seconds per phase: {secs})")
    rest = _o_plan(plans["rest"][2])
    hits = [i for i, ln in enumerate(rest) if "typed_calls_engine_parity_gates.py" in ln]
    lg13 = f"{_posix(o / 'k')}/out/engine_parity_cs.log"      # AC29: C1 to C4 are read from this FULL log
    want13 = (f"env CALL_LINKING_CS_SOURCES={_posix(o / 'no_sources')} {_posix(sys.executable)} {_posix(snap)}/tests/"
              f"typed_calls_engine_parity_gates.py --corpora --only jellyfin__jellyfin,dotnet__eShop --py39 "
              f"{_posix(sys.executable)} --py51 py51-placeholder > {lg13}.partial 2>&1; mv {lg13}.partial {lg13}")
    others = {ph: [ln for ln in _o_plan(plans[ph][2]) if "typed_calls_engine_parity_gates.py --corpora" in ln]
              for ph in plans if ph != "rest"}
    narrowed = _o_bash(rm_snap, dict(dry, CL_PHASE="rest", CL_CS_CORPORA="dotnet__eShop", **narrow))
    check("O: AC13 — the rest phase runs the snapshot's engine-parity gate's C# corpora mode exactly as AC13 names it, "
          "once, as its last command, its stdout AND stderr into <out>/engine_parity_cs.log.partial, then renamed to "
          "<out>/engine_parity_cs.log (AC29: C1 to C4 come from that full log); no other phase runs that mode (the "
          "gates phase runs the gate itself, G5a); narrowing the C# corpora (CL_CS_CORPORA) skips it",
          plans["rest"][0] == 0 and len(hits) == 1 and rest[hits[0]] == want13 and hits[0] == len(rest) - 1
          and not any(others.values()) and narrowed[0] == 0
          and "typed_calls_engine_parity_gates" not in narrowed[2],
          {"rc": plans["rest"][0], "got": [rest[i] for i in hits], "want": want13, "last": rest[-1:],
           "others": {k: v for k, v in others.items() if v}, "narrowed": (narrowed[0], narrowed[1][-200:])})
    run_log_def = _sh_function(script.read_text(encoding="utf-8"), "run_log")
    lgd = o / "run_log"
    lgd.mkdir(parents=True, exist_ok=True)
    outs = {}
    for tag, dry_v in (("run", ""), ("dry", "1")):
        lp = lgd / f"{tag}.log"
        prog = ("run_log() {" + run_log_def + "\n}\n" + f"DRY={shlex.quote(dry_v)}\n"
                + "run_log \"$1\" bash -c 'echo to-stdout; echo to-stderr >&2; exit 3'\necho \"rc=$?\"\n")
        try:
            p = subprocess.run(["bash", "-c", prog, "_", _posix(lp)], capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=120)
            outs[tag] = (p.returncode, p.stdout, p.stderr, lp.read_text(encoding="utf-8") if lp.is_file() else None,
                         Path(str(lp) + ".partial").exists())
        except (OSError, subprocess.SubprocessError) as exc:
            outs[tag] = (None, "", repr(exc), None, None)
    r_run, r_dry = outs["run"], outs["dry"]
    check("O: AC29 — run_log (cs_engine_parity's writer) puts the command's stdout AND stderr in the log, appends the "
          "exit code, leaves no .partial and returns the command's exit code; a dry run prints the redirect and writes "
          "nothing",
          bool(run_log_def) and r_run[1].strip() == "rc=3" and r_run[3] == "to-stdout\nto-stderr\n[run_matrix] exit 3\n"
          and r_run[4] is False and r_dry[1].strip() == "rc=0" and r_dry[3] is None and r_dry[4] is False
          and _o_plan(r_dry[2]) == [f"bash -c echo to-stdout; echo to-stderr >&2; exit 3 > {_posix(lgd / 'dry.log')}"
                                    f".partial 2>&1; mv {_posix(lgd / 'dry.log')}.partial {_posix(lgd / 'dry.log')}"],
          {"run": r_run, "dry": r_dry})
    spot = [ln for ln in rest if "impact_spot.py" in ln]
    check("O: AC12 — every spot command of the rest phase measures the corpus's FINAL list (--corpus=<id> "
          "--final=<out>/<id>/spots_final.json); the spots phase writes it with `substitute` over both engines' V0 "
          "dumps",
          bool(spot) and all(re.search(r"--corpus=(jellyfin__jellyfin|dotnet__eShop) --final=\S+/\1/spots_final\.json",
                                       ln) for ln in spot)
          and all(f"--corpus={c}" in "\n".join(_o_plan(plans["spots"][2])) and "substitute" in plans["spots"][2]
                  for c in CS_CORPUS_IDS.values()), spot[:2])
    aud = "\n".join(_o_plan(plans["audit"][2]))
    check("O: AC11 — the audit phase scores every sample and runs refrate once per corpus over both engines' main "
          "and dispatch-stratum samples",
          aud.count("audit_sample.py refrate") == 2 and all(
              f"--dir={_posix(o / 'k')}/out/{c}/audit_{e}{k}" in aud for c in CS_CORPUS_IDS.values()
              for e in ("g39", "g51") for k in ("", "_impl")), aud[-400:])
    check("O: AC15 — the manifest phase writes only into CL_CS_DIR (`offline_calls.py manifest <dir> --write`) and is "
          "refused without it", any(ln.endswith(f"offline_calls.py manifest {_posix(o / 'kitx' / 'cs')} --write")
                                    for ln in _o_plan(plans["manifest"][2]))
          and _o_bash(rm_snap, dict(dry, CL_PHASE="manifest"))[0] == 2, plans["manifest"][2][-300:])
    gl = _o_plan(plans["gates"][2])
    v0 = f"{_posix(sys.executable)} {_posix(snap)}/benchmark/call_linking/variants.py run V0 -- "
    runs = sorted(ln for ln in gl if ln.endswith("_gates.py"))
    listed = re.search(r'^CS_G5A_GATES="([^"]*)"', script.read_text(encoding="utf-8"), re.M)
    n_missing = len(listed.group(1).split()) - 1 if listed else -1
    check("O: G5a — the gates phase runs the snapshot's gates in variants.py's clean V0 environment under BOTH "
          "engines' Pythons (the fake snapshot holds one listed gate: 2 runs), the no-Neo4j suite once on the dev end, "
          "and names every missing listed gate as not passing (on both engines)",
          plans["gates"][0] == 0 and runs == sorted([v0 + f"{_posix(sys.executable)} {_posix(snap)}/tests/"
                                                     "typed_calls_engine_parity_gates.py",
                                                     v0 + f"py51-placeholder {_posix(snap)}/tests/"
                                                     "typed_calls_engine_parity_gates.py"])
          and gl.count(v0 + f"{_posix(sys.executable)} {_posix(snap)}/tests/run_all.py") == 1
          and plans["gates"][1].count("does not exist in the snapshot") == 2 * n_missing,
          {"runs": runs, "missing named": plans["gates"][1].count("does not exist in the snapshot"),
           "want missing": 2 * n_missing})
    check("O: AC7 — every phase's plan starts by checking the snapshot's identity (kithash --kit=<snapshot kit> "
          "--build=<snapshot build> --record=<out>/rc_identity.json)",
          all(re.search(r"offline_calls\.py kithash --kit=\S+ --build=\S+ --record=\S+/out/rc_identity\.json",
                        "\n".join(_o_plan(plans[ph][2]))) for ph in plans), {ph: plans[ph][0] for ph in plans})


def main(argv=None) -> int:
    for s in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            s.reconfigure(encoding="utf-8")
    argv = list(sys.argv[1:] if argv is None else argv)

    def opt(name, default):
        if name in argv:
            i = argv.index(name)
            return Path(argv[i + 1]).resolve()
        return default
    kit = opt("--kit", KIT_DEFAULT)
    sync_path = opt("--sync", BUILD / "sync.py")
    refreeze = opt("--refreeze", TESTS / "stage0_refreeze.py")
    real = "--no-real" not in argv
    print("=" * 72)
    print(f"DeepMap — S0 call-linking measurement kit + pre-registration (kit: {kit})")
    print("=" * 72)
    base = Path(tempfile.mkdtemp(prefix="dm_cl_s0_")).resolve()
    try:
        with _env(DEEPMAP_HOME=str(base / "home"), NEO4J_URI=None, DEEPMAP_LEARNING=None, DEEPMAP_AUTO_TUNE=None):
            mods = {}
            for name, fn, args in (("A", gate_a, (kit,)), ("B", gate_b, None), ("C", gate_c, None), ("D", gate_d, None),
                                   ("E", gate_e, None), ("F", gate_f, (refreeze, base)), ("G", gate_g, ()),
                                   ("H", gate_h, None), ("I", gate_i, None), ("J", gate_j, None),
                                   ("K", gate_k, (kit, base)), ("L", gate_l, None), ("M", gate_m, None),
                                   ("N", gate_n, None), ("O", gate_o, (kit, base))):
                if args is None:
                    args = {"B": (mods, sync_path, kit), "C": (mods, base, real), "D": (mods, kit, base, real),
                            "E": (mods, base), "H": (mods, kit), "I": (mods, kit, base), "J": (mods, kit),
                            "L": (mods, kit, base), "M": (mods, kit), "N": (mods, kit, base)}[name]
                try:
                    out = fn(*args)
                    if name == "A":
                        mods = out or {}
                except Exception as exc:  # noqa: BLE001 — a crash on the tree under test is a RED result
                    check(f"section {name} ran without crashing", False, f"{exc!r}\n{traceback.format_exc()[-900:]}")
    finally:
        shutil.rmtree(base, ignore_errors=True)
    print("\n" + "=" * 72)
    print(f"RESULT: {len(PASSES)} passed, {len(FAILS)} failed, {len(SKIPS)} skipped")
    for f in FAILS:
        print(f"  FAIL: {f}")
    print("=" * 72)
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
