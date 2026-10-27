#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DeepMap graphify-proper extraction (Phase 3b) — map graphify's graph.json to the typed schema.

Production DeepMap's design mandate is the upstream `graphify` AST engine (graphifyy, imports as
`graphify`). `graphify update <path> --no-cluster` re-extracts code with NO LLM and writes
`graphify-out/graph.json`. That graph is **loosely typed** — nodes carry no explicit kind; the
DeepMap label (:Function/:Method/:Class/:Module/:Route) must be INFERRED from:

  * `file_type`        — "code" vs "rationale" (a rationale node IS a docstring/summary, not a
                         graph node; it maps onto its target's body_summary via `rationale_for`);
  * the `label` shape  — "api.py" -> Module, "OrderService" -> Class, ".place_order()" -> Method,
                         "charge()" -> Function;
  * edge `relation`    — contains (module membership) / method (class membership) /
                         calls (-> :CALLS) / imports|imports_from (-> :IMPORTS) / rationale_for.

`map_graph(graph_dict)` is PURE (dict in -> (nodes, calls, imports) in sync.py's shape) so it is
unit-testable against a captured fixture with no graphify run; pass `root=` to also let it read the
call-site lines it needs to correct graphify's `self.m()` resolution (G5, below). `extract(root)`
shells out to `graphify update` and returns None when the run left no graph to map, so the caller can
fall back to the ast path (a non-zero engine exit that still wrote a fresh graph.json is mapped — the
result may be partial, or hold 0 code nodes); `extract_with_report(root)` is the same run plus a report of
WHY it failed or came back partial, so the fallback is never silent (sync records it; the verdict names it).

Where graphify writes (G1): NOT `<root>/graphify-out/` any more but a short per-root directory under
DEEPMAP_HOME (`graphify_out_dir`). graphify's per-file cache temp name adds ~109 characters to its
output dir, so on Windows (MAX_PATH 260) any project root over ~150 characters used to fail every
cache write — silently degrading to the Python-only ast fallback. Each run also starts from an EMPTY
graph.json (G2): graphify's `update` merges the previous graph.json back in, so a removed call
survived every re-sync as a phantom edge, links grew by a full copy per sync, and deleting a function
tripped graphify's shrink guard into refusing the rewrite. The per-file AST cache is kept (speed). Each
output dir carries a small owner marker (`deepmap_owner.json`) so `gout_dirs_for_project` can find every
dir a project left behind (for `provision instance-rm --purge`).

Directories the engine skips by NAME (G3 + round-2 decision D5): graphify prunes build/, out/, target/,
snapshots/, dist/, env/, *_env, ... at any depth, even when they hold git-tracked source (DeepMap's own
build/ was 100% missing). `skipped_source_dirs` lists the tracked, not-gitignored, hand-written source
under them — never under the virtualenv/env family or dist/, never a minified/bundle/compiled/generated
file — and `extract` re-includes exactly those directories through the spawn-safe shim, then drops
anything else the engine found in them. Opt-out: DEEPMAP_RESCUE_SKIPPED_DIRS=0. What cannot be
re-included is still NAMED, not silently missing (round 7, `unindexed_source_dirs`): hand-written
tracked source under an env-family dir or dist/ that is not a real virtualenv, and — in a non-git tree,
where tracked-ness is unknowable so nothing is re-included — hand-written source under any engine-skipped
dir that is not generated and not a byte-identical copy of source elsewhere; the verdict turns each into
a DEGRADED reason.

Two default-ON corrections of the engine's output, each with an opt-out for parity with older graphs:
self./cls./super() call re-binding (G5, DEEPMAP_SELF_CALL_REBIND=0) and clean owner names for generic /
reference owner types (`&'a mut Serializer<W, F>.serialize_str` -> `Serializer.serialize_str`,
DEEPMAP_CLEAN_OWNER_NAMES=0 — every plausible owner, i.e. all Python/Java/C#/TS/Ruby classes, is
untouched).

Round 7 (2026-09 sweep) adds, default ON (correctness / coverage): calls the language itself rules out
are dropped (a Go non-_test caller -> a _test.go symbol; a callee name whose case differs on the call
line; a bare call bound to another file's method in Python/Go/Rust/JS/TS — unless the name may hold a
bound method / method value in the caller's scope (round 9: an imported `get = _default.get` alias, Go's
`var First = q.First`, a parameter or local); a Go `pkg.F()` bound to the
caller package's own F — DEEPMAP_FALSE_CALL_FILTER=0 keeps them); an @overload group's node points at the
implementation; a Go file keeps its :Module when graphify reuses its id for a type; TS/JS declarations
lost to an id collision and Rust trait methods are recovered as nodes; bare Python module imports are
resolved. And default OFF (benchmark-gated, byte-identical graph when off): DEEPMAP_MODULE_DOCSTRINGS,
DEEPMAP_SUMMARY_NORMALIZE, DEEPMAP_GRAPHIFY_CALLS_PLUS and DEEPMAP_SUPPLEMENTAL_TS_RUST (the recovered
nodes' body calls). The engine child runs so it can never outlive the sync (_run_engine: a Windows
kill-on-close job object / a POSIX process-group watchdog).

Multi-language: this mapper is validated end-to-end on real captured `graphify update --no-cluster`
graphs for Python, TypeScript, JavaScript, Go, Rust, Java, Ruby, and C#
(tests/graphify_multilang_gates.py). The one cross-language junk source graphify emits is the
CommonJS `const { x } = require(...)` destructuring (a node labelled `{ x }`); `_plausible_name`
drops it (and any anonymous/bracketed artifact) so it never becomes a bogus `Class` node.

Deliberate scope decisions (keep the extractor swap a NODE + CALLS change only, so the STAGE-0
no-regression oracle + the with/without benchmark are what validate it):
  * :IMPORTS is returned as the third tuple and persisted by push_to_neo4j via
    edge_resolve (since v0.24.0). It is not part of the ranking path or the default
    deepmap_impact walk. A PYTHON import whose target graphify could not turn into a node id (a bare
    sys.path-style module name: `import helper`, `from pkg import m`) is resolved here against the
    graph's own Python modules (round 7, T3#10 — the importer's directory wins a tie; several
    candidates -> the module name, which edge_resolve grades AMBIGUOUS); a name no repo module answers
    (stdlib / third-party) is dropped, like the ast path's. Other languages' unresolved targets are
    still dropped.
    THE RESOLVED-IMPORT-KEY CONTRACT (round 3; default ON, `DEEPMAP_RESOLVED_IMPORTS=0` restores the
    old name tuples byte for byte): each import is a distinct 2-tuple `(src_key, target)`. When the
    engine resolved the import to a node DeepMap keeps (a file :Module, or a symbol a TS/JS named import
    points at), `target` is THAT node's key — guaranteed to be the `key` of a node in the same returned
    node list (sync's vendor/test filters drop any import whose key target they removed, see
    drop_dangling_import_keys). Otherwise `target` is the bare target name, as before. Consumers tell the
    two apart exactly like precise CALLS: `target` is a node key of this sync -> bind exactly that node
    (EXTRACTED — the engine resolved it from the literal import path); else resolve it as a name. This
    is what stops N same-named modules (a JS/TS tree of `index.ts` files) from turning every import of
    one of them into N AMBIGUOUS edges (N^2 overall).
  * Node keys are graphify's own ids (e.g. `pkg_cls_method`), NOT the ast path's
    `relpath::name:line` — with two DeepMap-made exceptions: a Go file whose id graphify reused for a
    same-named type is keyed `<id>::file` (T8#10), and a declaration recovered from source beside the
    engine's graph (a TS/JS declaration lost to an id collision, a Rust trait method) is keyed
    `<base id>@<name>:L<line>` — neither can collide with an engine id (casefolded [\\w]+) or a
    recognizer key (`<source_file>::...`). This is invisible to retrieval/oracle — the re-sync-stable
    identity the oracle compares is `source_file::name:line` derived from the RESULT fields, not the key.
    (Switching DEEPMAP_EXTRACTOR on an existing graph changes the key namespace, so re-sync with
    `--stale-cleanup` for a clean rebuild — a normal expectation when changing the engine.)

Known limitation vs the ast stand-in: graphify's `update` exposes no decorator info here, so
**Route** nodes are not distinguished — a route handler maps to Function. (Routes are a refinement;
CALLS/typing are preserved.)
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import summarize  # noqa: E402  (reuse humanize() for name-derived summaries)

# PROGRAMMING-language extensions only. graphify tags data/markup files (.json, .yaml, .md, ...)
# as file_type=="code" too, so their keys leak in as bogus nodes (e.g. a JSON "title" key -> a
# Class). DeepMap is a CODE graph: map only nodes whose source file is real source code.
#
# This allowlist is a DECISION about graphify's parser table, not a capability limit — and letting it
# fall behind that table silently deletes a whole language. The 2026-08 Delphi field report is exactly
# that failure: graphify has had a first-class Pascal extractor (`.pas/.pp/.dpr/.dpk/.lpr/.inc` ->
# extract_pascal) the entire time, emitted correct typed nodes + CALLS for a real Delphi tree, and
# DeepMap dropped 100% of them here — then reported READY over the resulting Delphi-free graph. So the
# rule is now explicit and both-sided: every extension the engine parses is EITHER allowed below OR
# listed in EXCLUDED_BY_DESIGN_EXTS, and tests/delphi_pascal_gates.py fails on any third case.
SOURCE_CODE_EXTS = (".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".go", ".rs",
                    ".java", ".rb", ".php", ".c", ".cc", ".cpp", ".cxx", ".h", ".hpp", ".cs", ".kt",
                    ".kts", ".swift", ".scala", ".sh", ".bash", ".ps1", ".psm1", ".lua", ".luau",
                    ".pl", ".pm", ".r", ".sql", ".vue", ".svelte",
                    # Delphi / Object Pascal (units, programs, packages, includes) — the field gap.
                    ".pas", ".pp", ".dpr", ".dpk", ".lpr", ".inc",
                    # the rest of graphify's parser table, so no other language is silently dropped
                    ".groovy", ".gradle", ".zig", ".ex", ".exs", ".m", ".mm", ".jl", ".dart",
                    ".astro", ".v", ".sv", ".svh", ".razor", ".cshtml", ".cls", ".trigger", ".dm",
                    ".f", ".f90", ".f95", ".f03", ".f08",
                    # CUDA / Metal (C/C++ family): graphify parses them with its C++ grammar from
                    # 0.8.46 (.cu/.cuh) and 0.8.50 (.metal) — inside SUPPORTED_GRAPHIFY_SPEC (F3).
                    ".cu", ".cuh", ".metal")

# The OTHER side of that decision: extensions graphify parses that DeepMap deliberately does NOT map
# into the code graph. These are data/markup/manifest formats — the very inputs the allowlist exists
# to keep out (a JSON key would otherwise become a bogus `Class`). Kept as an explicit set (not an
# omission) so the drift gate can tell "decided against" from "nobody noticed".
EXCLUDED_BY_DESIGN_EXTS = frozenset({
    ".md", ".mdx", ".qmd",                                   # prose/docs, not code
    ".json",                                                 # data (the original bogus-Class source)
    ".tf", ".tfvars", ".hcl",                                # declarative infra config
    ".sln", ".slnx", ".csproj", ".fsproj", ".vbproj",        # build/project manifests
    ".dfm", ".lfm", ".lpk",                                  # Delphi/Lazarus DESIGNER + package data
    ".toc",                                                  # addon manifest (parsed as Lua by graphify)
    ".dme", ".dmi", ".dmm", ".dmf",                          # BYOND env/manifest + binary map assets
    ".psd1",                                                 # PowerShell DATA manifest (a hashtable of
                                                             # module metadata; the code is the .psm1)
    ".xaml",                                                 # WPF/MAUI UI markup — designer data, like
                                                             # Delphi's .dfm (the code-behind is the .cs)
})
# Directories skipped by the odd-case suffix scan (mirrors sync's vendor filter closely enough that a
# vendored tree cannot slow the scan or contribute alias suffixes DeepMap would not map anyway).
_SCAN_IGNORE_DIRS = frozenset({"node_modules", "bower_components", "vendor", "public", "coverage",
                               ".git", ".hg", ".svn", ".venv", "venv", "__pycache__", ".deepmap",
                               "graphify-out", "dist", "build", "target", "bin", "obj", ".next",
                               ".nuxt", ".turbo", ".svelte-kit", ".yarn", ".idea", ".vs"})

# G3: directory names DeepMap never treats as project source, so an engine-skipped dir by one of these
# names is never re-included even when it holds tracked files. It is sync's own vendor filter
# (build/sync.py:_VENDOR_DIR_PARTS, locked as a subset by tests/extraction_engine_gates.py) plus
# VCS / cache / engine-output dirs, the whole virtualenv/env family and generated output (round-2
# decision D5). The env family (`env`, `.env`, `*_env`, `venv`, `*_venv`, `site-packages`, `lib64`) is
# overwhelmingly a committed or stray virtualenv — third-party code, not the project's. `dist` is
# build output: a tracked ncc/webpack bundle there (the usual GitHub-Action layout) duplicated every
# src/ symbol and dragged bundled dependencies into search/impact. Compared lowercase; `*_venv`,
# `*_env` and `*.egg-info` by suffix (_never_rescue).
_NEVER_RESCUE_DIRS = frozenset({
    "node_modules", "bower_components", "vendor", "public", "coverage", ".yarn", ".pnp", ".next",
    ".nuxt", ".turbo", ".svelte-kit",
    ".git", ".hg", ".svn", "__pycache__", "venv", ".venv", "env", ".env", "site-packages", "lib64",
    ".tox", ".eggs",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".cache", ".parcel-cache", ".angular",
    ".terraform", ".serverless", ".idea", "lcov-report", "storybook-static", "graphify-out",
    ".graphify", ".worktrees", ".deepmap",
    "dist", "dist-protected",
})
_NEVER_RESCUE_SUFFIXES = ("_venv", "_env", ".egg-info")

# Mirror of build/sync.py:_VENDOR_FILE_SUFFIXES (drift-locked by tests/extraction_policy_gates.py): a
# file sync's vendor filter would drop is never a reason to re-include a directory (D5).
_VENDOR_FILE_SUFFIXES = (".min.js", ".min.css", ".bundle.js", "-min.js", ".min.mjs")

# --- generated-file signatures (D5: re-include only HAND-WRITTEN source) -----------------------------
# A directory the engine skips by name (build/, out/, target/ ...) is re-included for the tracked source
# someone wrote there — DeepMap's own build/ is the field case. A tracked file there that a tool emitted
# (a bundle, compiled TypeScript, generated protobuf/gRPC code, a declaration file) is a second copy of
# code that lives elsewhere, so it is not re-included. Read from the head (and, for JS, the tail).
_GENERATED_HEADER = re.compile(rb"DO NOT EDIT")                       # Go/protobuf/gRPC convention
_GENERATED_HEADER_I = re.compile(rb"@generated\b|<auto-generated|\bauto-?generated by\b|"
                                 rb"\bautomatically generated by\b", re.IGNORECASE)
_JS_EXTS = (".js", ".mjs", ".cjs", ".jsx")
_DECLARATION_SUFFIXES = (".d.ts", ".d.mts", ".d.cts")
_JS_BUNDLE_MARKERS = (
    b"__webpack_require__", b"webpackBootstrap", b"__webpack_modules__", b"__nccwpck_require__",
    b"parcelRequire", b"System.register(", b"__toESM(", b"__toCommonJS(", b"__commonJS(",
    b'Object.defineProperty(exports, "__esModule"', b"Object.defineProperty(exports, '__esModule'",
    b"(this && this.__", b"_interopRequireDefault(", b"typeof define === 'function' && define.amd",
    b'typeof define === "function" && define.amd',
)
_MINIFIED_LINE = 2000        # a JS line this long is minified/bundled output, not hand-written code
_GENERATED_HEAD_BYTES = 16384

# Environment contract with build/graphify_case_shim.py (spawn workers inherit the environment, so it
# is the one channel that reaches them). Mirrored there as ENV_ALIASES / ENV_RESCUE / ENV_SKIP_METADATA.
ENV_CASE_ALIASES = "DEEPMAP_CASE_ALIASES"
ENV_RESCUE = "DEEPMAP_RESCUE_DIRS"
ENV_SKIP_MACOS_METADATA = "DEEPMAP_SKIP_MACOS_METADATA"   # "1" only when macos_metadata_present() found some
ENV_SAFE_PATH = "PYTHONSAFEPATH"   # the engine child never gets the project root on sys.path (3.11+)
_RESCUE_ENV_MAX = 16000   # past this, rescue by directory NAME instead of path (Windows env block limit)

# The explicit L<n> line marker: an UPPERCASE 'L' at a non-letter boundary (start, '/', ':', space)
# so a lowercase path word like 'l10n' or 'utils' can't be mistaken for the marker (bug-hunt #12).
_LINE_RE = re.compile(r"(?:^|[^A-Za-z])L(\d+)")
_MAX_CALLEES = 6


def _looks_like_file(label: str) -> bool:
    # Case-INSENSITIVE, matching _is_code_node: legacy Windows trees (classically Delphi) carry
    # `UNIT1.PAS` / `MAIN.DPR`, and a case-sensitive match would fail to type such a file node as a
    # :Module and then mis-type it as a `Class` named "UNIT1.PAS".
    return str(label or "").lower().endswith(SOURCE_CODE_EXTS)


def _is_code_node(node: dict) -> bool:
    """A node DeepMap should type: real source code, not a doc/data/rationale node."""
    if node.get("file_type") != "code":
        return False
    sf = str(node.get("source_file", "") or "").lower()
    return sf.endswith(SOURCE_CODE_EXTS)


def _line_of(node: dict) -> int:
    loc = str(node.get("source_location", "") or "")
    m = _LINE_RE.search(loc)              # prefer the explicit L<n> marker
    if m:
        return int(m.group(1))
    nums = re.findall(r"\d+", loc)         # else the LAST number (so a path part like "v2/" doesn't win)
    return int(nums[-1]) if nums else 1


def _bare(label: str) -> str:
    """Strip graphify's decorations: leading '.', trailing '()'."""
    base = label.strip()
    if base.endswith("()"):
        base = base[:-2]
    return base.lstrip(".").strip()


# Characters that never appear in a real single code-entity NAME. graphify emits a graph node for
# certain import artifacts — most notably the CommonJS `const { x } = require(...)` destructuring,
# whose label is `{ x }` (verified across the JS/TS import forms). Such a node is NOT a
# function/class/method; typed naively it becomes a bogus `Class { x }`. A bare name containing any
# of these (or whitespace) is an import/destructuring artifact, not an entity — drop it.
_IMPLAUSIBLE_NAME_CHARS = frozenset("{}[]<>\"',;=&|")


def _plausible_name(bare: str) -> bool:
    """True iff `bare` (a _bare()'d graphify label) is a plausible single code-entity name. Rejects
    empty names and import/destructuring artifacts like `{ checkout }` (braces + whitespace), so
    they never leak in as bogus Class nodes. Deliberately permissive otherwise — dotted/underscored/
    `$`-prefixed identifiers across languages all pass; only bracketed/spaced/quoted junk is cut."""
    if not bare:
        return False
    if any(ch.isspace() for ch in bare):
        return False
    return not any(ch in _IMPLAUSIBLE_NAME_CHARS for ch in bare)


def _env_on(name: str) -> bool:
    """A default-ON switch: only 0/false/no/off turns it off."""
    return str(os.environ.get(name, "1")).strip().lower() not in {"0", "false", "no", "off"}


def self_call_rebind_enabled() -> bool:
    """G5 (default ON). `DEEPMAP_SELF_CALL_REBIND=0` keeps graphify's own binding of in-file
    `self.m()` / `cls.m()` / `super().m()` calls, byte for byte — for parity with graphs (and oracles /
    benchmark runs) built before the correction. The correction itself is the right default: the
    engine's last-definition-wins binding hangs a caller on the wrong class's method."""
    return _env_on("DEEPMAP_SELF_CALL_REBIND")


def clean_owner_names_enabled() -> bool:
    """Default ON. `DEEPMAP_CLEAN_OWNER_NAMES=0` stores a method's owner exactly as the engine labelled
    it (`&'a mut Serializer<W, F>.serialize_str`) instead of the clean type name (see _clean_owner)."""
    return _env_on("DEEPMAP_CLEAN_OWNER_NAMES")


# A generic / reference / pointer owner type as the engine labels it (Rust `impl` targets, Go generic
# receivers): leading reference/pointer/trait-object sigils, then a (possibly path-qualified) type name,
# then generic arguments or trait-object bounds.
_OWNER_SIGILS = re.compile(r"^(?:&\s*(?:'\w+\s*)?(?:mut\b\s*)?|\*\s*(?:const|mut)\b\s*|(?:dyn|impl|mut)\s+)+")
_OWNER_PATH = re.compile(r"[A-Za-z_]\w*(?:(?:::|\.)[A-Za-z_]\w*)*")


def _clean_owner(label: str) -> str:
    """The type name a method belongs to, without generics, lifetimes or reference sigils:
    `&'a mut Serializer<W, F>` -> `Serializer`, `Box<dyn Thing + 'de>` -> `Box`, `*const Node<T>` ->
    `Node`, Go `List[T]` -> `List`. Only an owner that is not already a plausible name is touched, so
    every Python/Java/C#/TS/Ruby class (and a path like `crate::io::Reader`) keeps its label exactly; an
    owner with no leading type name (a tuple `(A, B)`, a slice `[T]`, a fn pointer) keeps its label too.
    Stored names then match what people type — `deepmap impact Serializer.serialize_str` — and the
    name-derived summaries read cleanly."""
    if _plausible_name(label):
        return label
    s = _OWNER_SIGILS.sub("", label.strip(), count=1)
    m = _OWNER_PATH.match(s)
    if not m:
        return label
    rest = s[m.end():].lstrip()
    return m.group(0) if (not rest or rest[0] in "<[+") else label


def resolved_imports_enabled() -> bool:
    """Default ON (the resolved-import-key contract in the module docstring). `DEEPMAP_RESOLVED_IMPORTS=0`
    returns every import as `(src_key, bare target name)` exactly as before round 3 — for parity with
    older graphs; the name form fans an import of one `index` module out to every `index` module."""
    return _env_on("DEEPMAP_RESOLVED_IMPORTS")


def import_key_counts(imports, nodes) -> tuple:
    """(bound by key, bound by name): how many import tuples carry a node KEY of `nodes` as their target
    — exactly the tuples a consumer binds exactly — and how many carry a name. Never raises."""
    try:
        keys = {n.get("key") for n in (nodes or [])}
        by_key = sum(1 for imp in (imports or []) if len(imp) >= 2 and imp[1] in keys)
        return by_key, len(imports or []) - by_key
    except Exception:  # noqa: BLE001
        return 0, 0


def drop_dangling_import_keys(imports, before_nodes, after_nodes):
    """The imports whose KEY target is a node of `before_nodes` that is not in `after_nodes` (a filter
    removed it: vendored, test, or a non-qualifying file in a re-included dir) are dropped, so a key-form
    target always names a node of the graph being pushed — never a key a consumer would then look up as
    a name. Returns the SAME list object when nothing is dropped (byte-identity downstream)."""
    try:
        gone = {n.get("key") for n in (before_nodes or [])} - {n.get("key") for n in (after_nodes or [])}
        if not gone or not imports:
            return imports
        kept = [imp for imp in imports if not (len(imp) >= 2 and imp[1] in gone)]
        return imports if len(kept) == len(imports) else kept
    except Exception:  # noqa: BLE001
        return imports


# ---- S1: DEEPMAP_PRECISE_CALLS is scoped by LANGUAGE FAMILY ------------------------------------------------
# Precise CALLS was measured per language family (the engine resolves calls better in some), so it flips per
# family (edge_resolve's family names: python, js, go, ...). The flag's value is that set: unset -> the defaults
# below (EMPTY until a family passes its measured decision — the S8 flip edits only this constant); 0 / false /
# no / off -> none; 1 / true / yes / on -> every file, whatever its extension (the old switch); a CSV of family
# names -> those (a name edge_resolve does not know selects nothing). map_graph keys an engine call only when its
# CALLER's file is in a selected family (precise_for).
_PRECISE_DEFAULT_FAMILIES: frozenset = frozenset()
_PRECISE_ALL = "*"
_PRECISE_OFF_WORDS = frozenset({"0", "false", "no", "off"})
_PRECISE_ON_WORDS = frozenset({"1", "true", "yes", "on"})


def _precise_family_names() -> frozenset:
    """The family names edge_resolve knows (its _FAMILY_EXTS), or none when it cannot be imported."""
    try:
        import edge_resolve   # lazy: one family table for the whole product (edge_resolve.lang_family)
        return frozenset(getattr(edge_resolve, "_FAMILY_EXTS", {}) or {})
    except Exception:  # noqa: BLE001
        return frozenset()


def _precise_raw(value) -> str:
    if value is None:
        value = os.environ.get("DEEPMAP_PRECISE_CALLS", "")
    return str(value or "").strip().lower()


def precise_calls_families(value=None) -> frozenset:
    """The language families DEEPMAP_PRECISE_CALLS selects (see the block comment above): `_PRECISE_DEFAULT_FAMILIES`
    when unset or blank, the empty set for 0 / false / no / off, {_PRECISE_ALL} for 1 / true / yes / on, else the
    known family names of the comma-separated value (case and blanks ignored; an unknown name selects nothing —
    extract_with_report names it in the sync log, precise_calls_unknown). `value` (default: the environment's) lets
    a caller read a recorded value the same way (two values with one result build the same CALLS)."""
    raw = _precise_raw(value)
    if not raw:
        return _PRECISE_DEFAULT_FAMILIES
    if raw in _PRECISE_OFF_WORDS:
        return frozenset()
    if raw in _PRECISE_ON_WORDS:
        return frozenset({_PRECISE_ALL})
    known = _precise_family_names()
    return frozenset(p for p in (part.strip() for part in raw.split(",")) if p in known)


def precise_calls_unknown(value=None) -> list:
    """The names of a DEEPMAP_PRECISE_CALLS family CSV that edge_resolve does not know (sorted, lower-case): they
    select nothing (`typescript`, `all`, a typo). [] when the value is unset, an OFF / ON word or only known names.
    `value`: as precise_calls_families."""
    raw = _precise_raw(value)
    if not raw or raw in _PRECISE_OFF_WORDS or raw in _PRECISE_ON_WORDS:
        return []
    known = _precise_family_names()
    return sorted({p for p in (part.strip() for part in raw.split(",")) if p and p not in known})


def precise_for(path, families=None) -> bool:
    """Is precise CALLS on for a caller in `path`? `families` (default: precise_calls_families()) holding
    _PRECISE_ALL means every path, any extension or none; else the path's edge_resolve family must be one of them
    (a path without a family never is)."""
    fams = precise_calls_families() if families is None else families
    if not fams:
        return False
    if _PRECISE_ALL in fams:
        return True
    try:
        import edge_resolve
        fam = edge_resolve.lang_family(path)
    except Exception:  # noqa: BLE001 — no family table: no family is precise
        return False
    return fam is not None and fam in fams


def precise_fixes_enabled() -> bool:
    """Default ON with precise CALLS (S1). `DEEPMAP_PRECISE_FIXES=0` turns off the two corrections of the precise
    output — the TS value-over-type re-key and the 0.8.39 placeholder-duplicate drop (P1b), see map_graph — and
    restores the precise tuples exactly as before them."""
    return _env_on("DEEPMAP_PRECISE_FIXES")


def precise_calls_enabled() -> bool:
    """P0-1 (opt-in, default OFF — Rails field-review). When on, the graphify path KEEPS graphify's
    already-resolved call target (a node KEY) instead of discarding it for the callee NAME, so
    push_to_neo4j creates ONE firm `:CALLS{confidence:'EXTRACTED'}` edge rather than fanning the name
    out to every same-named node (the 82.9%-AMBIGUOUS / 3.25x-inflation the field-review measured).
    S1: true when DEEPMAP_PRECISE_CALLS selects at least one language family (precise_calls_families); which
    callers it applies to is precise_for's. Off -> byte-identical to before; the default flip is
    benchmark-gated, per family. ast-path calls are name-only (never a node key), so this is a
    graphify-path-only change."""
    return bool(precise_calls_families())


def keyed_calls_enabled() -> bool:
    """True when a CALLS tuple's second element can be a node KEY instead of a callee name: under
    DEEPMAP_PRECISE_CALLS (every call the engine resolved for a caller of a family it selects) or
    DEEPMAP_GRAPHIFY_CALLS_PLUS (its additions,
    which resolve to exactly one node — R10/P5). A consumer that binds a key-form callee to exactly that
    node (sync's push) must do so whenever this is true; both flags off -> every call is a name, as always."""
    return precise_calls_enabled() or graphify_calls_plus_enabled()


# ---- round 11 (R1): a CALLS callee that IS a node key says so ---------------------------------------------
# A CALLS tuple is (caller key, callee) where the callee is either a NAME (fanned out over every node of that
# name at the push) or a node KEY (bound to exactly that node). Key-ness used to be INFERRED downstream from
# `callee in <node keys>` — so a NAME that happened to equal a node's key (a root `cli.py` whose Module key is
# `cli` and which defines `cli()`; `build/insights.py` = Module key `build_insights`, which defines
# `build_insights()`; `from main import main` -> the Module key `main`) was bound to that :Module as an
# EXTRACTED / INFERRED edge and the real targets were dropped: under DEEPMAP_GRAPHIFY_CALLS_PLUS alone (whose
# engine calls stay names) and through the R10/P1 fall-back names under DEEPMAP_PRECISE_CALLS. Now the
# producer MARKS a key: map_graph wraps every callee it resolved to one node (a precise engine call, a precise
# supplemental TS/Rust call, a CALLS_PLUS addition) in CallKey, and every consumer (sync's push passes,
# _keep_rescued_only) takes the keyed branch only for a marked callee. CallKey is a str: it compares, hashes,
# prints and serialises exactly like the plain key, so every lookup by key, every list comparison and every
# test that reads the tuples is unchanged — only a consumer that asks is_call_key() can tell the two apart.
# Both flags off: nothing is ever wrapped (every callee is a name, as always).
class CallKey(str):
    """A CALLS callee that is a node KEY — the producer resolved the call to exactly this node (R11/R1)."""
    __slots__ = ()
    _DEEPMAP_CALL_KEY = True


def call_key(key) -> "CallKey":
    """`key` marked as a node key (see CallKey); a CallKey comes back unchanged."""
    return key if is_call_key(key) else CallKey(key)


def is_call_key(value) -> bool:
    """True iff `value` is a callee its producer marked as a node KEY. Checked by marker attribute, not by
    class identity, so a CallKey made by another loaded copy of this module (a test that loads build/*.py by
    path) is still recognised; a plain str never is."""
    return isinstance(value, str) and getattr(value, "_DEEPMAP_CALL_KEY", False) is True


# G5: a member call `<receiver>.<name>(` on one source line; `super()` / `super(C, self)` count as one
# receiver. The receiver is the word right before the dot, so `self.ctx.invoke(` reads as `ctx`.
_RECV_CALL = r"(super\s*\([^()]*\)|[A-Za-z_]\w*)\s*\.\s*{name}\s*\("


def _self_call_rebinder(root, links, by_id, method_owner, kinds, source=None):
    """G5: correct graphify's binding of in-file Python method calls. Returns rebind(link, src, tgt).

    graphify resolves a call inside one file by bare NAME with the LAST definition winning, so in a
    click-style core.py every `self.invoke()` / `ctx.invoke()` became a call to the last class's
    `invoke` (Group.invoke), stamped EXTRACTED and graded INFERRED downstream, while the real target
    (Context.invoke) showed no callers at all. graph.json records the call line but not the receiver,
    so the receiver is read from that source line (numbered as the engine numbers it: '\\n' only):

      * `self.m()` / `cls.m()`: the caller's own class, then its in-file bases (breadth-first) —
        the first that defines m wins. `super().m()` starts at the bases.
      * none of them defines m: graphify's pick is kept when the caller's class header names the
        pick's class (a base the engine could not parse, e.g. `class T(Base[int])` emits no
        inherits edge); a dunder is dropped (`super().__init__()` into list/object/Exception lives
        outside the file, and a name fan-out would hang it on every `__init__` in the repo);
        anything else becomes the bare name, which push grades AMBIGUOUS across its candidates (never a
        :Module or a :Class — R11c/C1).
      * any other receiver (`ctx.m()`): graphify's pick is a guess when more than one class in the
        file defines m, so the bare name again; with a single in-file m its pick is kept. A DUNDER
        reached that way (`BaseEnvironment.__init__(self, **options)`, `dict.__init__(self)`) never
        becomes the bare name — the self/super rule above, for the same reason (R11b/G1, flask: one
        such call fanned out to all 41 `__init__`s, 13 of them package `__init__.py` :Modules): when
        the receiver is an in-file class (`SessionMixin.__init__(self)`), that class's in-file MRO
        binds it exactly as `self.` does; otherwise it is dropped.

    rebind returns None (keep graphify's target), ("key", node_id), ("name", bare_name), or
    ("drop", None) for a self-call that is really recursion. Anything it cannot read or parse keeps
    graphify's target, so this can only move an edge onto better evidence. A ("name", m) result is a
    MEMBER call (it always had a receiver): map_graph records it on the caller as `call_member`, and the
    push never resolves such a name to a :Module or a :Class — :Method and module-level :Function targets
    stay, graded as the name fan-out always was (R11b/G1, R11c/C1 — see sync._resolve_calls)."""
    root = Path(root)
    methods_of: dict = {}          # class id -> {bare method name: method id}
    for mid, cid in method_owner.items():
        if kinds.get(mid) == "Method" and cid in kinds:
            methods_of.setdefault(cid, {})[_bare((by_id.get(mid) or {}).get("label", ""))] = mid
    bases_of: dict = {}
    for l in links:
        if l.get("relation") in ("inherits", "extends") and l.get("source") in kinds \
                and l.get("target") in kinds:
            bases_of.setdefault(l["source"], []).append(l["target"])
    lines: dict = {}

    def file_of(nid) -> str:
        return str((by_id.get(nid) or {}).get("source_file") or "").replace("\\", "/")

    def line_text(sf: str, lineno: int):
        # The ENGINE's numbering ('\n' only — _engine_lines), never str.splitlines(): after a form feed, a U+2028
        # or a lone '\r' every later call line and class header was read from the wrong line (ISS-build:016).
        if sf not in lines:
            try:
                if source is not None:          # map_graph's shared _Source (one open + one split per file)
                    lines[sf] = source.lines(sf)
                else:
                    lines[sf] = _engine_lines((root / sf).read_bytes())
            except (OSError, ValueError):
                lines[sf] = None
        text = lines[sf]
        return text[lineno - 1] if text and 1 <= lineno <= len(text) else None

    def mro(cid) -> list:
        order, queue, seen = [], [cid], set()
        while queue:
            c = queue.pop(0)
            if c not in seen:
                seen.add(c)
                order.append(c)
                queue.extend(bases_of.get(c, []))
        return order

    def header_names(cid, name: str) -> bool:
        """Does class `cid`'s `class X(...):` header (up to 6 lines) mention `name`?"""
        start = _line_of(by_id.get(cid) or {})
        text = ""
        for n in range(start, start + 6):
            line = line_text(file_of(cid), n)
            if line is None:
                break
            text += " " + line
            if line.rstrip().endswith(":"):
                break
        return bool(name) and re.search(rf"\b{re.escape(name)}\b", text.split("class", 1)[-1]) is not None

    classes_in: dict = {}          # source file -> {bare class name: [class ids]} (built on first use)

    def in_file_class(sf: str, word: str):
        """The one class named `word` defined in file `sf`, or None (none, or more than one)."""
        if None not in classes_in:
            classes_in[None] = {}      # built
            for cid, kind in kinds.items():
                if kind == "Class":
                    classes_in.setdefault(file_of(cid), {}).setdefault(
                        _bare((by_id.get(cid) or {}).get("label", "")), []).append(cid)
        hits = classes_in.get(sf, {}).get(word, [])
        return hits[0] if len(hits) == 1 else None

    def rebind(link, src, tgt):
        if kinds.get(tgt) != "Method":
            return None
        sf = file_of(src)
        if not sf.lower().endswith((".py", ".pyi")) or file_of(tgt) != sf:
            return None
        name = _bare((by_id.get(tgt) or {}).get("label", ""))
        m = _LINE_RE.search(str(link.get("source_location") or ""))
        text = line_text(sf, int(m.group(1))) if (m and name) else None
        if text is None:
            return None
        words = re.findall(_RECV_CALL.format(name=re.escape(name)), text)
        recvs = {("super" if r.startswith("super") else "self" if r in ("self", "cls") else "other")
                 for r in words}
        dunder = name.startswith("__") and name.endswith("__")
        own = method_owner.get(src) if kinds.get(src) == "Method" else None
        if own is not None and recvs in ({"self"}, {"super"}):
            chain = mro(own) if recvs == {"self"} else mro(own)[1:]
            hit = next((methods_of[c][name] for c in chain if name in methods_of.get(c, {})), None)
            if hit is None:
                if header_names(own, _bare((by_id.get(method_owner.get(tgt)) or {}).get("label", ""))):
                    return None
                return ("drop", None) if dunder else ("name", name)
            if hit == src:
                return ("drop", None)
            return None if hit == tgt else ("key", hit)
        if not recvs:
            return None
        in_file = sum(1 for c, ms in methods_of.items() if name in ms and file_of(c) == sf)
        if in_file <= 1:
            return None
        if dunder:
            # R11b/G1 (F1): a dunder through any other receiver is never fanned out by name. An explicit
            # in-file class receiver (`SessionMixin.__init__(self)`, the pre-super idiom) starts the lookup at
            # that class, exactly as `self.` starts it at the caller's; anything else (an imported base
            # `BaseEnvironment.__init__`, a builtin `dict.__init__`, an instance `obj.__exit__`) is outside
            # what this file can bind, so the call is dropped like the self/super dunder above.
            cls = in_file_class(sf, words[0]) if len(set(words)) == 1 else None
            hit = next((methods_of[c][name] for c in mro(cls) if name in methods_of.get(c, {})), None) \
                if cls is not None else None
            if hit is None or hit == src:
                return ("drop", None)
            return None if hit == tgt else ("key", hit)
        return ("name", name)

    return rebind


# ================================================================================================
# Round 7 (2026-09 live validation sweep): corrections of the engine's output (default ON) and three
# default-OFF extraction flags. Every helper here is best-effort: anything it cannot read or parse leaves
# the engine's output exactly as it was.
# ================================================================================================
_PY_EXTS = (".py", ".pyi")
_TSJS_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
# A bare call (no receiver) can never be a METHOD call in these languages: Python needs `self.`/`obj.`,
# Go/Rust/JS/TS need a receiver or `this.`. (Java/C#/Kotlin/Ruby/C++/Swift have an implicit `this`/`self`,
# so a bare call there CAN be an inherited method — they are deliberately not listed.)
_BARE_CALL_NO_METHOD_EXTS = _PY_EXTS + (".go", ".rs") + _TSJS_EXTS
# Case-sensitive languages: a call written `Compute(` can never bind a callee named `compute`. graphify
# resolves call names case-FOLDED, so it can. (PHP, Pascal/Delphi, SQL, Fortran, Apex and PowerShell
# resolve names case-insensitively and are deliberately not listed.)
_CASE_SENSITIVE_EXTS = _BARE_CALL_NO_METHOD_EXTS + (
    ".java", ".kt", ".kts", ".scala", ".swift", ".cs", ".rb", ".c", ".h", ".cc", ".cpp", ".cxx", ".hpp",
    ".dart", ".zig", ".jl", ".ex", ".exs", ".groovy", ".lua", ".luau", ".m", ".mm", ".cu", ".cuh", ".metal")
_FILE_KEY_SUFFIX = "::file"


def false_call_filter_enabled() -> bool:
    """Default ON (ROUND7 I3 — compiler-guaranteed, so a correction, not a heuristic). `DEEPMAP_FALSE_CALL_
    FILTER=0` keeps every call the engine emitted, byte for byte."""
    return _env_on("DEEPMAP_FALSE_CALL_FILTER")


def _flag_on(name: str) -> bool:
    """A default-OFF switch: only 1/true/yes/on turns it on."""
    return str(os.environ.get(name, "")).strip().lower() in {"1", "true", "yes", "on"}


def module_docstrings_enabled() -> bool:
    """Default OFF (ROUND7 I2, T2#5). `DEEPMAP_MODULE_DOCSTRINGS=1`: a .py :Module node's summary is the
    module's real docstring (ast.get_docstring — found behind a shebang/coding line, and never replaced
    by a `# TODO` comment inside a function), and `documented` follows it. Off: graphify's rationale, as
    before. Changes Module summaries (embedded), so it is benchmark-gated."""
    return _flag_on("DEEPMAP_MODULE_DOCSTRINGS")


def summary_normalize_enabled() -> bool:
    """Default OFF (ROUND7 I2, T4d-F5). `DEEPMAP_SUMMARY_NORMALIZE=1`: a Python docstring summary is the
    docstring's real first sentence read with `ast` (graphify cuts its label at 80 characters, mid-word,
    and keeps an `r\"\"\"` prefix and indentation runs), cut at a word boundary within 300 characters."""
    return _flag_on("DEEPMAP_SUMMARY_NORMALIZE")


def graphify_calls_plus_enabled() -> bool:
    """Default OFF (ROUND7 I2, T3#3 / T4d-F4). `DEEPMAP_GRAPHIFY_CALLS_PLUS=1`: Python calls the engine
    does not emit are added from the file's own `ast` — `alias.f()` / `module.f()` resolved through the
    file's import table, calls inside nested closures attributed to the enclosing graph node, and
    module-level calls recorded on the :Module node. Only a call that resolves to exactly ONE node is
    added, and it is added as a keyed edge to exactly that node — with or without DEEPMAP_PRECISE_CALLS
    (R10/P5; see keyed_calls_enabled). Only NON-test callers (sync._is_test_path: a test directory or a
    test-file name) get additions unless DEEPMAP_GRAPHIFY_CALLS_PLUS_TESTS=1 (R10/P6). Changes CALLS
    (the graph arm), so it is benchmark-gated."""
    return _flag_on("DEEPMAP_GRAPHIFY_CALLS_PLUS")


def calls_plus_test_callers_enabled() -> bool:
    """Default OFF (R10/P6). `DEEPMAP_GRAPHIFY_CALLS_PLUS_TESTS=1`: DEEPMAP_GRAPHIFY_CALLS_PLUS also adds the
    calls of TEST callers (test dirs / test files), as it did before round 10. Off, a test's calls into the
    library it tests do not inflate the graph-weighted arm's in-degree prior (click: 154 of 181 additions)."""
    return _flag_on("DEEPMAP_GRAPHIFY_CALLS_PLUS_TESTS")


_TEST_DIRS_FALLBACK = frozenset({"test", "tests", "__tests__", "spec", "specs", "testing", "e2e", "__mocks__"})


def _test_path_predicate():
    """THE test-code rule, sync._is_test_path (directory component or filename convention, never a
    substring — src/contest.py and Latest.java are production); sync is already imported on the sync path.
    A minimal mirror when sync cannot be imported. Never raises."""
    try:
        import sync  # noqa: E402 — the owner of the rule (DEEPMAP_EXCLUDE_TESTS / F8b use it too)
        fn = getattr(sync, "_is_test_path", None)
        if callable(fn):
            return fn
    except Exception:  # noqa: BLE001
        pass

    def fallback(source_file) -> bool:
        parts = str(source_file or "").replace("\\", "/").split("/")
        low = parts[-1].lower()
        return (any(p.lower() in _TEST_DIRS_FALLBACK for p in parts[:-1])
                or (low.startswith("test_") and low.endswith(".py"))
                or low.endswith(("_test.py", "_test.go", "_test.rs", "_spec.rb"))
                or any(low.endswith(f"{m}{e}") for m in (".test", ".spec")
                       for e in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"))
                or parts[-1].endswith(("Test.java", "Tests.java")))
    return fallback


def supplemental_ts_rust_calls_enabled() -> bool:
    """Default OFF (ROUND7 I3 rule: new NODES are a coverage fix and default ON; new CALLS are not).
    `DEEPMAP_SUPPLEMENTAL_TS_RUST=1` also emits the body calls of the TS/JS declarations recovered from
    id collisions and of Rust trait default methods."""
    return _flag_on("DEEPMAP_SUPPLEMENTAL_TS_RUST")


def _engine_id(text) -> str:
    """graphify's `_make_id` for ONE part (graphify/extract.py: strip '_.', NFKC, non-word runs -> '_',
    squash '_', strip, casefold) — how the engine names an import target it could not resolve to a file
    (`import pkg.mod` -> `pkg_mod`). Replicated (the engine module is heavy to import in-process)."""
    import unicodedata
    combined = unicodedata.normalize("NFKC", str(text or "").strip("_."))
    cleaned = re.sub(r"[^\w]+", "_", combined, flags=re.UNICODE)
    cleaned = re.sub(r"_+", "_", cleaned)
    return cleaned.strip("_").casefold()


def _dir_of(path: str) -> str:
    p = str(path or "").replace("\\", "/")
    return p.rsplit("/", 1)[0] if "/" in p else ""


def _engine_lines(data: bytes) -> list:
    """A source file's bytes as text lines numbered the way the ENGINE numbers them: tree-sitter advances its
    row on '\\n' ONLY, so split on '\\n' alone (a CRLF file's trailing '\\r' is dropped). Never str.splitlines(),
    which also breaks on a form feed (a `^L` page break), \\x0b, \\x1c-\\x1e, \\x85, U+2028 / U+2029 and a lone
    '\\r' — after one of those every later line is read off the engine's number (ISS-build:016). The one rule
    for _Source and the G5 re-binder's no-source fallback."""
    text = data.decode("utf-8-sig", errors="replace")
    return [s.rstrip("\r") for s in text.split("\n")]


class _Source:
    """Cached, never-raising reads of the project's source files (root-relative paths, as graphify
    reports them): text lines (split on '\\n' only, so numbering matches the engine's — _engine_lines), a
    Python `ast`, and a tree-sitter tree. Every miss is None."""

    def __init__(self, root):
        self.root = Path(root)
        self._bytes: dict = {}
        self._lines: dict = {}
        self._ast: dict = {}
        self._ts: dict = {}

    def _path(self, sf: str) -> Path:
        return self.root / str(sf or "").replace("\\", "/")

    def raw(self, sf: str):
        """The file's bytes (read once per map_graph — each open costs ~10 ms under Windows' scanner), or
        None when unreadable."""
        if sf not in self._bytes:
            try:
                self._bytes[sf] = self._path(sf).read_bytes()
            except (OSError, ValueError):
                self._bytes[sf] = None
        return self._bytes[sf]

    def lines(self, sf: str):
        if sf not in self._lines:
            data = self.raw(sf)
            self._lines[sf] = None if data is None else _engine_lines(data)
        return self._lines[sf]

    def line(self, sf: str, lineno) -> str | None:
        text = self.lines(sf)
        try:
            n = int(lineno)
        except (TypeError, ValueError):
            return None
        return text[n - 1] if text and 1 <= n <= len(text) else None

    def tree(self, sf: str):
        """ast.Module of a Python file, or None (unreadable / not Python / syntax error)."""
        if sf not in self._ast:
            import ast
            tree = None
            text = self.lines(sf)
            if text is not None:
                try:
                    tree = ast.parse("\n".join(text))
                except (SyntaxError, ValueError, RecursionError, MemoryError):
                    tree = None
            self._ast[sf] = tree
        return self._ast[sf]

    def ts_tree(self, sf: str):
        """(tree, source_bytes) from tree-sitter for a .rs / TS / JS file, or None when the grammar is not
        importable (they are graphify's own dependencies) or the file is unreadable."""
        if sf not in self._ts:
            self._ts[sf] = None
            try:
                low = str(sf).lower()
                import tree_sitter as ts
                if low.endswith(".rs"):
                    import tree_sitter_rust as grammar
                    lang = ts.Language(grammar.language())
                elif low.endswith(".tsx"):
                    import tree_sitter_typescript as grammar
                    lang = ts.Language(grammar.language_tsx())
                elif low.endswith(".ts"):
                    import tree_sitter_typescript as grammar
                    lang = ts.Language(grammar.language_typescript())
                elif low.endswith((".js", ".jsx", ".mjs", ".cjs")):
                    import tree_sitter_javascript as grammar
                    lang = ts.Language(grammar.language())
                else:
                    return None
                data = self.raw(sf)
                if data is None:
                    return None
                self._ts[sf] = (ts.Parser(lang).parse(data), data)
            except Exception:  # noqa: BLE001 — no grammar / unreadable: the supplement is skipped
                self._ts[sf] = None
        return self._ts[sf]


def _link_line(link) -> int | None:
    m = _LINE_RE.search(str((link or {}).get("source_location") or ""))
    return int(m.group(1)) if m else None


# ---- T8#10: one id for a Go file and its same-named type ------------------------------------------
_FILE_SIDE_SOURCE = frozenset({"contains", "imports", "imports_from", "re_exports"})
_FILE_SIDE_TARGET = frozenset({"imports", "imports_from", "re_exports"})


def _engine_file_id(sf: str) -> str:
    """graphify's id for a FILE node (extract._file_node_id: `_make_id(parent_dir + '.' + stem)` of the
    root-relative path — `models/customer.go` -> `models_customer`, a root-level `gin.go` -> `gin`).
    Identical in 0.8.39 and 0.8.51."""
    from pathlib import PurePosixPath
    p = PurePosixPath(str(sf or "").replace("\\", "/"))
    parent = p.parent.name
    return _engine_id(f"{parent}.{p.stem}" if parent and parent != "." else p.stem)


def _restore_swallowed_file_nodes(raw: list, links: list):
    """R8/J2: graphify 0.8.39 writes BOTH nodes when a file and an entity share an id (the case
    `_split_reused_file_ids` repairs); from the 0.8.4x line on, the `--no-cluster` writer collapses nodes
    by id (`build.dedupe_nodes`, last one wins), so the FILE node is simply gone — models/customer.go
    had no :Module on 0.8.51, and a file's own links (imports, contains) hung on the type. The links
    survive, so the file node is rebuilt from them. A source file that has NO file node is restored under
    the id X when X names only NON-file nodes and either
      * X `contains` X — the self-link a same-file swallow always leaves (`models_customer ->
        models_customer`; it also covers the ids graphify disambiguates across files, e.g. gin's
        `binding_binding_go_binding_binding`), or
      * X is the file's own engine id (`_engine_file_id`) and `contains` something from that file.
    Every extracted file has a file node, so a file without one next to such links is exactly the
    swallowed case. The rebuilt node goes right before the id's first node — where 0.8.39 emitted it —
    so the split that follows sees exactly the 0.8.39 shape. Pure (no source needed).
    Returns (raw, n_restored); the SAME list comes back when nothing is missing."""
    by_id: dict = {}
    file_srcs: set = set()
    for n in raw:
        if not isinstance(n, dict) or not n.get("id"):
            continue
        by_id.setdefault(n["id"], []).append(n)
        sf = str(n.get("source_file") or "").replace("\\", "/")
        if sf and n.get("file_type") == "code" and _looks_like_file(n.get("label", "")):
            file_srcs.add(sf)
    add: dict = {}                             # id -> the rebuilt file node
    for l in links:
        if not isinstance(l, dict) or l.get("relation") != "contains":
            continue
        x = l.get("source")
        owners = by_id.get(x) or []
        if not owners or x in add or any(_looks_like_file(o.get("label", "")) for o in owners):
            continue
        lsf = str(l.get("source_file") or "").replace("\\", "/")
        if not lsf and x == l.get("target"):   # a writer that drops link source files: the self-link's node
            lsf = str(owners[0].get("source_file") or "").replace("\\", "/")
        if not lsf or lsf in file_srcs or not lsf.lower().endswith(SOURCE_CODE_EXTS):
            continue
        if x != l.get("target") and x != _engine_file_id(lsf):
            continue
        node = {"id": x, "label": lsf.rsplit("/", 1)[-1], "file_type": "code", "source_file": lsf,
                "source_location": "L1"}
        if "_origin" in owners[0]:
            node["_origin"] = owners[0]["_origin"]
        add[x] = node
        file_srcs.add(lsf)
    if not add:
        return raw, 0
    n_restored = len(add)
    out: list = []
    for n in raw:
        nid = n.get("id") if isinstance(n, dict) else None
        if nid in add:
            out.append(add.pop(nid))
        out.append(n)
    return out, n_restored


def _split_reused_file_ids(raw: list, links: list):
    """graphify ids a Go type by package dir + name and a file by parent dir + stem, so `models/customer.go`
    and its `type Customer` share the id `models_customer`; map_graph's id-keyed maps kept only the last,
    and the file lost its :Module (and every typed edge anchored on it: a route declared in server/
    server.go persisted 0 of 2). TS/JS reaches the same state through a declaration whose name
    normalizes to nothing — `export const $ = ...` in lots-of-objects.ts is `_make_id(stem, '$')`, the
    file's own id (zod: 7 files). When an id names exactly ONE file node and at least one other node, the
    file node is re-keyed `<id>::file`: links FROM the id that only a file emits (contains, imports) and
    links TO it that name a file (imports) move to the file; everything else (the type's methods and
    references, calls, `contains` of the type itself) stays on the type, which keeps the engine's id.
    (graphify 0.8.4x+ no longer writes the file node at all — `_restore_swallowed_file_nodes` rebuilds
    it first.) Returns (raw, links, n_split); the SAME objects come back when no id is reused."""
    groups: dict = {}
    for n in raw:
        nid = n.get("id") if isinstance(n, dict) else None
        if nid:
            groups.setdefault(nid, []).append(n)
    remap: dict = {}
    taken = set(groups)
    for nid, ns in groups.items():
        if len(ns) < 2:
            continue
        files = [n for n in ns if _looks_like_file(n.get("label", ""))]
        if len(files) != 1 or len(files) == len(ns):
            continue
        fid = nid + _FILE_KEY_SUFFIX
        while fid in taken:
            fid += "_"
        taken.add(fid)
        remap[nid] = (fid, files[0])
    if not remap:
        return raw, links, 0
    new_raw = [dict(n, id=remap[n.get("id")][0]) if (isinstance(n, dict) and n.get("id") in remap
                                                    and n is remap[n.get("id")][1]) else n for n in raw]
    new_links = []
    for l in links:
        s, t, rel = l.get("source"), l.get("target"), l.get("relation")
        ns_, nt_ = s, t
        if s in remap and rel in _FILE_SIDE_SOURCE:
            ns_ = remap[s][0]
        if t in remap and t != s:
            if rel in _FILE_SIDE_TARGET:
                nt_ = remap[t][0]
            elif rel == "rationale_for" and (_link_line(l) or 2) <= 1:
                nt_ = remap[t][0]                # a file-leading comment/docstring documents the file
        new_links.append(dict(l, source=ns_, target=nt_) if (ns_, nt_) != (s, t) else l)
    return new_raw, new_links, len(remap)


# ---- T3#10: bare Python module imports ------------------------------------------------------------
def _py_module_index(modules) -> dict:
    """{engine id of every dotted suffix of a Python module path: [module keys]} for `modules` =
    [(key, source_file)]. `scripts/pkg/mod.py` answers `scripts_pkg_mod`, `pkg_mod` and `mod`; a
    package's `__init__.py` answers for the package (`pkg`). A .pyi stub beside its .py never competes
    with it."""
    idx: dict = {}
    stems: dict = {}
    for key, sf in modules:
        p = str(sf or "").replace("\\", "/")
        low = p.lower()
        if low.endswith(".pyi"):
            stem_path, is_stub = p[:-4], True
        elif low.endswith(".py"):
            stem_path, is_stub = p[:-3], False
        else:
            continue
        parts = [x for x in stem_path.split("/") if x and x != "."]
        if parts and parts[-1] == "__init__":
            parts = parts[:-1]
        if not parts:
            continue
        if is_stub and stem_path in stems:
            continue
        stems.setdefault(stem_path, key)
        for i in range(len(parts)):
            idx.setdefault(_engine_id(".".join(parts[i:])), []).append((key, is_stub, stem_path))
    out: dict = {}
    for ident, cands in idx.items():
        real = {sp for _k, stub, sp in cands if not stub}
        out[ident] = [k for k, stub, sp in cands if not (stub and sp in real)]
    return out


def _resolve_py_module(ident: str, idx: dict, importer_file: str, importer_key: str, file_of):
    """('key', module_key) for a unique match — or the unique one in the importer's own directory (a
    script's sys.path[0]); ('names', [keys]) when several remain; None when nothing in the repo matches
    (stdlib / third-party)."""
    cands = [k for k in dict.fromkeys(idx.get(ident) or []) if k != importer_key]
    if not cands:
        return None
    if len(cands) == 1:
        return ("key", cands[0])
    here = _dir_of(importer_file)
    local = [k for k in cands if _dir_of(file_of(k)) == here]
    if len(local) == 1:
        return ("key", local[0])
    return ("names", cands)


# ---- T4#4: @overload groups -----------------------------------------------------------------------
def _is_overload_deco(d) -> bool:
    import ast
    t = d.func if isinstance(d, ast.Call) else d
    return (getattr(t, "attr", None) or getattr(t, "id", None)) == "overload"


def _py_def_scopes(tree) -> dict:
    """{(def line, name): (scope, is_overload)} for every function def in a Python `ast`, `scope` being
    the nearest enclosing def/class (None = the module) — the unit graphify keys a function's id on, so
    an @overload stub and its implementation are one engine node exactly when they share a scope and a
    name (`if TYPE_CHECKING:` / `try:` blocks do not open one)."""
    import ast
    out: dict = {}
    stack = [(tree, None)]
    while stack:
        node, scope = stack.pop()
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out[(child.lineno, child.name)] = (scope, any(_is_overload_deco(d) for d in child.decorator_list))
                stack.append((child, child))
            elif isinstance(child, ast.ClassDef):
                stack.append((child, child))
            else:
                stack.append((child, scope))
    return out


def _overload_lines(src: _Source, links, by_id, kinds) -> dict:
    """{node id: implementation line} for Python Function/Method nodes whose engine line is the first
    `@overload` stub of a group (graphify merges the stubs and the implementation into one id and keeps
    the FIRST def's line). The implementation is the first def of the same name, in the same scope,
    after the node's line that is not @overload-decorated. A stub-only group (.pyi) keeps its line.

    Read from the SOURCE (R8/J2), not from the engine's links: graphify 0.8.39 repeated the group's
    contains/method link once per def, but the 0.8.4x+ `--no-cluster` writer collapses repeated links
    (`build.dedupe_edges`), so on 0.8.51 only the first stub's line was left and the fix never fired.
    `links` is unused and kept for the call signature."""
    del links
    out: dict = {}
    scopes_of: dict = {}                       # source file -> _py_def_scopes (None: no overload / no parse)
    taken: set = set()                         # (file, line, bare name) of every engine node: never move onto one
    for nid, node in by_id.items():
        if nid in kinds:
            taken.add((str(node.get("source_file") or "").replace("\\", "/"), _line_of(node),
                       _bare(node.get("label", "")).rsplit(".", 1)[-1]))
    for nid, kind in kinds.items():
        if kind not in ("Function", "Method"):
            continue
        node = by_id.get(nid) or {}
        sf = str(node.get("source_file") or "").replace("\\", "/")
        if not sf.lower().endswith(_PY_EXTS):
            continue
        if sf not in scopes_of:
            raw = src.raw(sf)
            tree = src.tree(sf) if raw is not None and b"overload" in raw else None   # cheap prefilter
            scopes_of[sf] = _py_def_scopes(tree) if tree is not None else None
        scopes = scopes_of[sf]
        if not scopes:
            continue
        start, name = _line_of(node), _bare(node.get("label", ""))
        first = scopes.get((start, name))
        if first is None or not first[1]:
            continue
        impl = min((ln for (ln, nm), (scope, ovl) in scopes.items()
                    if nm == name and ln > start and scope is first[0] and not ovl), default=None)
        if impl is None:
            continue
        if (sf, impl, name) in taken:          # the engine already has a node there (a separate id)
            continue
        out[nid] = impl
    return out


# ---- T5#4 / T4d-F3: compiler-guaranteed false CALLS -----------------------------------------------
_QUALIFIER = re.compile(r"([A-Za-z_$][\w$]*)\s*(?:\?\.|\.|::|->)\s*$")
_DEF_BEFORE = re.compile(r"(?:^|[^\w$])(?:def|fn|func|function|class|new|async\s+def)\s*$")
# R9/K10: a line that ENDS in a member operator continues on the next one — gofmt's only legal multi-line
# chain form (`return q.` / `\t\tFirst()`), and legal in JS/TS/Rust and in Python inside brackets or after
# an explicit `\`. A float literal `1.` ends a statement instead; `..` / `...` are ranges / ellipses.
_MEMBER_TAIL = ("?.", ".", "::", "->")
_NUM_DOT_TAIL = re.compile(r"(?:^|[^\w$.])\d[\d_]*\.$")


def _strip_trailing_comment(line: str, py: bool) -> str:
    """`line` without a trailing `#` (Python) or `//` comment that sits outside a string literal."""
    q = None
    i, n = 0, len(line)
    while i < n:
        ch = line[i]
        if q:
            if ch == "\\":
                i += 2
                continue
            if ch == q:
                q = None
        elif ch in "'\"`":
            q = ch
        elif (py and ch == "#") or (not py and ch == "/" and line[i + 1:i + 2] == "/"):
            return line[:i]
        i += 1
    return line


def _is_comment_only(stripped: str, py: bool) -> bool:
    if py:
        return stripped.startswith("#")
    return stripped.startswith(("//", "/*", "*/")) or (stripped[:1] == "*" and stripped[1:2] in ("", " ", "\t", "/"))


def _prev_code_line(lines, idx: int, py: bool, limit: int = 50):
    """The code line before 0-based line `idx` of `lines` — blank and comment-only lines skipped (at most
    `limit` back), a trailing comment cut — or None when there is none (K10)."""
    j = idx - 1
    while j >= 0 and idx - j <= limit:
        s = lines[j].strip()
        if s and not _is_comment_only(s, py):
            return _strip_trailing_comment(lines[j], py).rstrip()
        j -= 1
    return None


def _py_string_lines(lines) -> set:
    """1-based numbers of the lines of a Python file that lie INSIDE a multi-line string literal (a
    docstring's second line onwards) — prose there is not code. Empty when the file does not tokenize."""
    import io
    import tokenize
    out: set = set()
    kinds = {tokenize.STRING, getattr(tokenize, "FSTRING_MIDDLE", tokenize.STRING)}
    try:
        for tok in tokenize.generate_tokens(io.StringIO("\n".join(lines) + "\n").readline):
            if tok.type in kinds and tok.end[0] > tok.start[0]:
                out.update(range(tok.start[0] + 1, tok.end[0] + 1))
    except (tokenize.TokenError, SyntaxError, ValueError):
        pass
    return out


def _member_tail(prev):
    """`prev` when that code line ends in a member operator — the next line's leading name is then a
    receiver call / member, never a bare one — else None. An explicit `\\` continuation is looked through."""
    if not prev:
        return None
    p = prev.rstrip()
    if p.endswith("\\"):
        p = p[:-1].rstrip()
    if not p.endswith(_MEMBER_TAIL) or p.endswith("..") or _NUM_DOT_TAIL.search(p):
        return None
    return p


def _call_sites(text: str, name: str, *, ignore_case: bool = False, prev=None) -> list:
    """How `name(` occurs on one source line: a list of ('bare', None) / ('qualified', qualifier_or_None)
    per call occurrence — `name` not preceded by an identifier character and followed (after blanks) by
    `(`. Definitions and `new X(` are skipped. A plain string scan: compiling one regex per callee name
    cost ~1 ms each on a real repo (thousands of names).

    `prev` (R9/K10) is the previous code line (or a zero-argument callable returning it, read only when
    needed): a name that opens its line after a line ending in `.` / `?.` / `::` / `->` is the continuation
    of a receiver call (`q.` / `First()`), so it is 'qualified', never 'bare'."""
    return [(k, q) for k, q, _op, _head, _nseg in _call_site_details(text, name, ignore_case=ignore_case, prev=prev,
                                                                      chains=False)]


# R10/P1: the member chain a qualified call site ends with — `self._stream.` / `pkg.util.` / `util::` — as
# (chain, operator). Read from at most the last _CHAIN_WINDOW characters before the name (bounded work on a
# long line); a chain that does not start at an identifier (`f().`, `x[0].`, `super().`) has no head.
_CHAIN_END = re.compile(r"(?<![\w$])([A-Za-z_$][\w$]*(?:\s*(?:\?\.|\.|::|->)\s*[A-Za-z_$][\w$]*)*)"
                        r"\s*(\?\.|\.|::|->)\s*$")
_CHAIN_IDENT = re.compile(r"[A-Za-z_$][\w$]*")
_CHAIN_WINDOW = 300
_NO_CHAIN = (None, None, 0)


def _chain_of(before: str):
    """(operator, head identifier or None, number of identifiers in the chain) of the member chain that
    `before` (the text in front of a qualified call's name) ends with."""
    s = before[-_CHAIN_WINDOW:]
    m = _CHAIN_END.search(s)
    if m is None:
        op = next((t for t in _MEMBER_TAIL if s.rstrip().endswith(t)), None)
        return op, None, 0
    idents = _CHAIN_IDENT.findall(m.group(1))
    return m.group(2), (idents[0] if idents else None), len(idents)


def _call_site_details(text: str, name: str, *, ignore_case: bool = False, prev=None, chains: bool = True) -> list:
    """`_call_sites` with the member chain of each qualified site: a list of (kind, qualifier, operator,
    head, n_segments) — ('bare', None, None, None, 0) for a bare call; for a qualified one the operator
    (`.` / `?.` / `::` / `->`), the chain's first identifier (`self` of `self._stream.isatty(`, `pkg` of
    `pkg.util.helper(`; None for `f().m(`) and how many identifiers the chain has (R10/P1). `chains=False`
    (what _call_sites — the default false-call filter's hot path — asks for) skips the chain: (None, None, 0)."""
    out: list = []
    if not text or not name:
        return out
    hay, needle = (text.lower(), name.lower()) if ignore_case else (text, name)
    i = hay.find(needle)
    while i >= 0:
        pch = hay[i - 1] if i else ""
        k = i + len(needle)
        while k < len(hay) and hay[k] in " \t":
            k += 1
        if not (pch and (pch.isalnum() or pch in "_$")) and k < len(hay) and hay[k] == "(":
            before = text[:i].rstrip()
            tail = None
            if not before and prev is not None:
                tail = _member_tail(prev() if callable(prev) else prev)
            if tail is not None:
                q = _QUALIFIER.search(tail)
                out.append(("qualified", q.group(1) if q else None, *(_chain_of(tail) if chains else _NO_CHAIN)))
            elif not _DEF_BEFORE.search(before):
                if before.endswith(_MEMBER_TAIL):
                    q = _QUALIFIER.search(before)
                    out.append(("qualified", q.group(1) if q else None,
                                *(_chain_of(before) if chains else _NO_CHAIN)))
                else:
                    out.append(("bare", None, None, None, 0))
        i = hay.find(needle, i + 1)
    return out


def _value_mention(text: str, name: str, prev=None) -> bool:
    """True iff `name` occurs on the line as a whole word that is neither called (`name(`), a member
    (`x.name`, or the continuation of a `recv.` line break — K10) nor a one-word string literal (`"name"`):
    a declaration, an assignment, a parameter, an argument, an import — a VALUE (R9/K11)."""
    i = text.find(name)
    while i >= 0:
        k = i + len(name)
        a = text[i - 1] if i else ""
        b = text[k] if k < len(text) else ""
        if not (a and (a.isalnum() or a in "_$")) and not (b and (b.isalnum() or b in "_$")):
            j = k
            while j < len(text) and text[j] in " \t":
                j += 1
            before = text[:i].rstrip()
            member = before.endswith(_MEMBER_TAIL) or (
                not before and prev is not None and _member_tail(prev() if callable(prev) else prev) is not None)
            quoted = a != "" and a in "'\"`" and b == a
            if not (j < len(text) and text[j] == "(") and not member and not quoted:
                return True
        i = text.find(name, i + 1)
    return False


# ---- R9/K11: a bare name that may hold a bound method / a method value ----------------------------
# The 'bare-method' rule rests on "a call without a receiver can never reach a method". That holds for a
# name bound to a def/class, a builtin or an external module's object — but a VALUE can hold a bound
# method or a method value: `get = _default.get` (stdlib `random` does exactly this), a plugin registry's
# `register = _registry.register`, Go's `var First = defaultQuery.First`, a parameter / local holding
# `obj.m`. The engine binds such a call to the method correctly, so the call is kept whenever the name MAY
# be such a value in the caller's scope; the rule drops only what the language rules out.
_GO_VAR_LINE = re.compile(r"^var\s+([A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)*)")
_GO_VAR_BLOCK = re.compile(r"^var\s*\(\s*(?://.*)?$")
_GO_VAR_SPEC = re.compile(r"^[ \t]+([A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)*)")
_TS_IMPORT_START = re.compile(r"^\s*(?:import\b(?!\s*[(.])|export\s*(?:type\s*)?[{*])")
_TS_AS_RENAME = re.compile(r"(?<![\w$])[\w$]+\s+as\s+(?=[\w$])")
_TS_FROM_SPEC = re.compile(r"\bfrom\s*(['\"])([^'\"]*)\1")
_TS_BARE_IMPORT = re.compile(r"^\s*import\s*(['\"])")
_TS_REQUIRE = re.compile(r"\brequire\s*\(\s*(['\"])([^'\"]*)\1\s*\)")
_WORD = "(?<![\\w$]){}(?![\\w$])"


def _ts_stmt_done(text: str, nxt: str) -> bool:
    """Whether a gathered JS/TS import / export-list statement is complete: it names its specifier
    (`from '...'`, `require('...')`, a side-effect `import '...'`), ends with `;`, or its braces closed and
    the next line does not carry its `from`."""
    if (_TS_FROM_SPEC.search(text) or _TS_REQUIRE.search(text) or _TS_BARE_IMPORT.match(text)
            or text.rstrip().endswith(";")):
        return True
    o = text.count("{")
    return o > 0 and o == text.count("}") and not nxt.lstrip().startswith("from")


def _is_project_specifier(spec: str) -> bool:
    """A JS/TS module specifier that names a file of this project (relative / absolute path) — a bare
    package specifier (`path`, `axios`, `@scope/pkg`) is an external module."""
    return spec.startswith((".", "/"))


def _py_bindings(tree) -> dict:
    """The module-level binding table of a Python `ast`: `defs` (def/class names), `values` (every other
    Store target outside defs, classes and lambdas — assignment / for / with / walrus / except / match
    captures, a comprehension variable too: over-counting only keeps calls — plus every `global` name),
    `mods` (`import x` / `import x as y`), `imports` ({bound name: [(module, original name, level)]} of the
    from-imports), `stars` ([(module, level)]) and `getattr` (a module-level `def __getattr__`)."""
    import ast
    defs: set = set()
    values: set = set()
    mods: set = set()
    imports: dict = {}
    stars: list = []
    has_getattr = False
    stack = list(ast.iter_child_nodes(tree))
    while stack:
        x = stack.pop()
        if isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defs.add(x.name)
            has_getattr = has_getattr or (x.name == "__getattr__" and not isinstance(x, ast.ClassDef))
            continue
        if isinstance(x, ast.Lambda):
            continue
        if isinstance(x, ast.ImportFrom):
            for a in x.names:
                if a.name == "*":
                    stars.append((x.module, x.level or 0))
                else:
                    imports.setdefault(a.asname or a.name, []).append((x.module, a.name, x.level or 0))
            continue
        if isinstance(x, ast.Import):
            for a in x.names:
                mods.add(a.asname or a.name.split(".")[0])
            continue
        if isinstance(x, ast.Name) and isinstance(x.ctx, (ast.Store, ast.Del)):
            values.add(x.id)
        elif isinstance(x, ast.ExceptHandler) and x.name:
            values.add(x.name)
        elif type(x).__name__ in ("MatchAs", "MatchStar", "MatchMapping"):
            v = getattr(x, "name", None) or getattr(x, "rest", None)
            if isinstance(v, str):
                values.add(v)
        stack.extend(ast.iter_child_nodes(x))
    for x in ast.walk(tree):
        if isinstance(x, ast.Global):
            values.update(x.names)
    return {"defs": defs, "values": values, "mods": mods, "imports": imports, "stars": stars,
            "getattr": has_getattr}


def _py_locally_bound(fn) -> set:
    """Names a def / class (or anything nested in it) binds itself as values: parameters, Store targets,
    except names. A nested def / class NAME is a function, not a value, and is not counted."""
    import ast
    names: set = set()
    for x in ast.walk(fn):
        if isinstance(x, ast.Name) and isinstance(x.ctx, (ast.Store, ast.Del)):
            names.add(x.id)
        elif isinstance(x, ast.arg):
            names.add(x.arg)
        elif isinstance(x, ast.ExceptHandler) and x.name:
            names.add(x.name)
    return names


def _py_scope_locals(tree, fn) -> set:
    """The value names local to `fn`'s body: its own (_py_locally_bound) and those of every def around it
    (a closure sees them)."""
    import ast
    end = getattr(fn, "end_lineno", None) or fn.lineno
    out = set(_py_locally_bound(fn))
    for d in ast.walk(tree):
        if (d is not fn and isinstance(d, (ast.FunctionDef, ast.AsyncFunctionDef)) and d.lineno <= fn.lineno
                and (getattr(d, "end_lineno", None) or 0) >= end):
            out |= _py_locally_bound(d)
    return out


def _bare_alias_checker(src: _Source, by_id: dict, kinds: dict, span):
    """alias(s, t, name, sf, tf) -> True when the bare `name(` that caller `s` (file `sf`) makes — bound by
    the engine to METHOD `t` of file `tf` — may be a VALUE holding a bound method or a method value (R9/K11),
    so the call is NOT ruled out. `span(s, sf)` gives the caller's own body lines. Per language:
      Python  (ast) a parameter / local of the caller or of a def around it; a module-level value of the
              caller's module; a from-import (at any level) unless the imported module provably binds that
              name as a def / class / module — followed through relative imports, re-exports and star
              imports — or is outside the project (then kept only when the method's own module binds the
              name as a value: the alias may be what was imported)
      Go      a package-level `var` of that name in the caller's package (its directory; a _test.go file
              only for a test caller), or a parameter / local binding in the caller
      JS/TS   a value binding anywhere in the caller file — an import from a project path (a bare package
              specifier is an external value), a const / let / var, a destructuring, a parameter — or a
              const / let / var / `exports.name` of that name in the method's own file
      Rust    a `let` / parameter binding in the caller (a `use`d item is a fn, never an inherent method)
    Anything that cannot be read keeps the call."""
    py_tabs: dict = {}
    go_vars: dict = {}
    scope_names: dict = {}                 # (file, def line, def name) -> value names local to that def
    memo: dict = {}                        # (caller, callee) -> answer
    mod_idx: list = [None]

    def file_of(nid) -> str:
        return str((by_id.get(nid) or {}).get("source_file") or "").replace("\\", "/")

    all_files = {file_of(nid) for nid in by_id}
    py_files = {f for f in all_files if f.lower().endswith(_PY_EXTS)}
    go_by_dir: dict = {}
    for f in all_files:
        if f.lower().endswith(".go"):
            go_by_dir.setdefault(_dir_of(f), []).append(f)

    def py(sf: str):
        """{mod, defs_at, all_from, all_stars, tree} of a Python file, or None when it cannot be parsed."""
        if sf not in py_tabs:
            import ast
            tree = src.tree(sf)
            if tree is None:
                py_tabs[sf] = None
            else:
                defs_at: dict = {}
                all_from: dict = {}
                all_stars: list = []
                for d in ast.walk(tree):
                    if isinstance(d, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        for ln in {d.lineno, *(x.lineno for x in d.decorator_list)}:
                            defs_at.setdefault((ln, d.name), d)
                    elif isinstance(d, ast.ImportFrom):
                        for a in d.names:
                            if a.name == "*":
                                all_stars.append((d.module, d.level or 0))
                            else:
                                all_from.setdefault(a.asname or a.name, []).append((d.module, a.name, d.level or 0))
                py_tabs[sf] = {"tree": tree, "mod": _py_bindings(tree), "defs_at": defs_at, "all_from": all_from,
                               "all_stars": all_stars}
        return py_tabs[sf]

    def resolve(importer: str, module, level: int) -> list:
        """The project files a Python import names ([] = outside the project)."""
        if level:
            parts = [p for p in importer.split("/")[:-1] if p]
            up = level - 1
            if up > len(parts):
                return []
            base = parts[:len(parts) - up]
            stem = "/".join(base + ([p for p in module.split(".") if p] if module else []))
            pre = stem + "/" if stem else ""
            cands = ([stem + ".py", stem + ".pyi"] if module else []) + [pre + "__init__.py", pre + "__init__.pyi"]
            return [c for c in cands if c in py_files]
        if not module:
            return []
        if mod_idx[0] is None:
            mod_idx[0] = _py_module_index([(k, file_of(k)) for k, kind in kinds.items() if kind == "Module"])
        got = _resolve_py_module(_engine_id(module), mod_idx[0], importer, None, file_of)
        if got is None:
            return []
        return [file_of(k) for k in ([got[1]] if got[0] == "key" else got[1])]

    def import_kind(importer: str, module, orig: str, level: int, depth: int, seen: tuple = ()) -> str:
        """'value' (the imported name may hold anything), 'def' (a def / class / module — or not bound
        there at all), or 'external' (the module is outside the project). An import cycle binds nothing
        new; past 4 hops the answer is 'value' (kept)."""
        files = resolve(importer, module, level)
        if not files:
            return "external"
        found: set = set()
        for f in files:
            if (f, orig) in seen:
                found.add("def")
                continue
            seen_f = seen + ((f, orig),)
            tab = py(f)
            if tab is None:
                return "value"
            b = tab["mod"]
            if orig in b["values"]:
                return "value"
            if orig in b["imports"]:
                if depth >= 4:
                    return "value"
                for m2, o2, l2 in b["imports"][orig]:
                    k = import_kind(f, m2, o2, l2, depth + 1, seen_f)
                    if k == "value":
                        return "value"
                    found.add(k)
                continue
            if orig in b["defs"] or orig in b["mods"]:
                found.add("def")
                continue
            if b["getattr"]:
                return "value"
            for m2, l2 in b["stars"]:
                if depth >= 4 or import_kind(f, m2, orig, l2, depth + 1, seen_f) == "value":
                    return "value"
            found.add("def")
        return "external" if found == {"external"} else "def"

    def module_value(sf: str, *names) -> bool:
        tab = py(sf) if sf.lower().endswith(_PY_EXTS) else None
        return tab is not None and any(n in tab["mod"]["values"] for n in names)

    def span_value(s, sf: str, name: str, *, skip_use: bool = False) -> bool:
        """A value mention of `name` in the caller's own body lines (text)."""
        is_py = sf.lower().endswith(_PY_EXTS)
        lines = src.lines(sf) or []
        for ln in span(s, sf):
            text = src.line(sf, ln) or ""
            st = text.strip()
            if name not in text or not st or _is_comment_only(st, is_py):
                continue
            if skip_use and st.startswith(("use ", "pub use ", "pub(crate) use ")):
                continue
            if _value_mention(_strip_trailing_comment(text, is_py), name,
                              prev=lambda ln=ln: _prev_code_line(lines, ln - 1, is_py)):
                return True
        return False

    def py_alias(s, t, name: str, sf: str, tf: str) -> bool:
        tab = py(sf)
        if tab is None:                    # unparseable: any value-like mention in the file keeps the call
            return any(_value_mention(_strip_trailing_comment(x, True), name) for x in (src.lines(sf) or [])
                       if name in x and not x.lstrip().startswith("#"))
        if name in tab["mod"]["values"]:
            return True
        if kinds.get(s) != "Module":
            nd = by_id.get(s) or {}
            fn = tab["defs_at"].get((_line_of(nd), _bare(nd.get("label", "")).rsplit(".", 1)[-1]))
            if fn is None:
                if span_value(s, sf, name):
                    return True
            else:
                ck = (sf, fn.lineno, fn.name)
                if ck not in scope_names:
                    scope_names[ck] = _py_scope_locals(tab["tree"], fn)
                if name in scope_names[ck]:
                    return True
        for m, orig, lvl in tab["all_from"].get(name, ()):
            k = import_kind(sf, m, orig, lvl, 0)
            if k == "value" or (k == "external" and module_value(tf, orig, name)):
                return True
        for m, lvl in tab["all_stars"]:
            k = import_kind(sf, m, name, lvl, 0)
            if k == "value" or (k == "external" and module_value(tf, name)):
                return True
        return False

    def go_pkg_vars(f: str) -> set:
        if f not in go_vars:
            names: set = set()
            block = False
            for raw in src.lines(f) or []:
                if block:
                    if raw.startswith(")"):
                        block = False
                        continue
                    m = _GO_VAR_SPEC.match(raw)
                elif _GO_VAR_BLOCK.match(raw):
                    block = True
                    continue
                else:
                    m = _GO_VAR_LINE.match(raw)
                if m:
                    names.update(x.strip() for x in m.group(1).split(","))
            go_vars[f] = names
        return go_vars[f]

    def go_alias(s, t, name: str, sf: str, tf: str) -> bool:
        if span_value(s, sf, name):
            return True
        test = sf.lower().endswith("_test.go")
        return any(name in go_pkg_vars(f) for f in go_by_dir.get(_dir_of(sf), ())
                   if test or not f.lower().endswith("_test.go"))

    def ts_alias(s, t, name: str, sf: str, tf: str) -> bool:
        lines = src.lines(sf) or []
        word = re.compile(_WORD.format(re.escape(name)))
        i = 0
        while i < len(lines):
            raw = lines[i]
            st = raw.strip()
            if not st or _is_comment_only(st, False):
                i += 1
                continue
            if _TS_IMPORT_START.match(raw):            # import ... / export {...} [from] / export * from
                j = i
                text = _strip_trailing_comment(raw, False)
                while j + 1 < len(lines) and j - i < 60 and not _ts_stmt_done(text, lines[j + 1]):
                    j += 1
                    text += " " + _strip_trailing_comment(lines[j], False)
                i = j + 1
                if not text.lstrip().startswith("import") or re.match(r"\s*import\s+type\b", text):
                    continue                           # a re-export / export list binds nothing new here
                fm = _TS_FROM_SPEC.search(text) or _TS_REQUIRE.search(text)
                clause = _TS_AS_RENAME.sub("", text[:fm.start()]) if fm else ""   # `{ get as g }` binds g
                if word.search(clause) and _is_project_specifier(fm.group(2) if fm else ""):
                    return True
                continue
            rq = _TS_REQUIRE.search(raw)
            if rq and word.search(raw[:rq.start()]):
                if _is_project_specifier(rq.group(2)):
                    return True
                i += 1
                continue
            if name in raw and _value_mention(_strip_trailing_comment(raw, False), name,
                                              prev=lambda i=i: _prev_code_line(lines, i, False)):
                return True
            i += 1
        # the alias beside the method (also behind a workspace package's bare specifier)
        decl = re.compile(r"^\s*(?:export\s+)?(?:declare\s+)?(?:const|let|var)\s+(?:[\[{][^\]}]*)?"
                          + _WORD.format(re.escape(name))
                          + "|(?:^|[^\\w$.])(?:module\\.)?exports\\." + re.escape(name) + r"\s*=")
        return any(decl.search(x) for x in (src.lines(tf) or []) if name in x)

    def decide(s, t, name: str, sf: str, tf: str) -> bool:
        try:
            low = sf.lower()
            if low.endswith(_PY_EXTS):
                return py_alias(s, t, name, sf, tf)
            if low.endswith(".go"):
                return go_alias(s, t, name, sf, tf)
            if low.endswith(_TSJS_EXTS):
                return ts_alias(s, t, name, sf, tf)
            if low.endswith(".rs"):
                return span_value(s, sf, name, skip_use=True)
            return False
        except Exception:  # noqa: BLE001 — anything that cannot be read keeps the engine's call
            return True

    def alias(s, t, name: str, sf: str, tf: str) -> bool:
        if (s, t) not in memo:                 # the engine may repeat a (caller, callee) link
            memo[(s, t)] = decide(s, t, name, sf, tf)
        return memo[(s, t)]

    return alias


def _false_call_filter(src, by_id, kinds, reader=None, alias_box=None):
    """filter(link, src_id, tgt_id) -> the reason a call the engine emitted CANNOT be real, or None.
    Rules, each guaranteed by the language (so default ON, opt-out DEEPMAP_FALSE_CALL_FILTER=0):
      * go-test-file  : a Go file not ending in _test.go never calls into a _test.go file (the compiler
                        builds _test.go only for `go test`). Needs no source line.
      * case          : on the call line the callee name appears only in a DIFFERENT case (`Must(` vs a
                        `must` def) — a case-sensitive language cannot bind it; graphify casefolds names.
      * bare-method   : every occurrence on the call line is a bare `name(` (no receiver) and the target
                        is a METHOD in another file — Python/Go/Rust/JS/TS have no implicit `this` — AND
                        `name` cannot be a VALUE holding a bound method / method value in the caller's
                        scope (R9/K11: an imported `get = _default.get` alias, Go's `var First =
                        q.First`, a parameter or local — `_bare_alias_checker`).
      * go-selector   : a Go call written only `pkg.F(` in a non-test file never names the caller
                        package's OWN top-level function F (a package cannot refer to itself by name; an
                        external `x_test` package beside it can, so _test.go callers are exempt).
    The three source rules judge the line the engine recorded, but the engine records ONE line per
    (caller, callee) pair (its extractor collapses a caller's repeated calls, and the 0.8.4x+ writer
    collapses repeated links again), so before a pair is ruled out the rest of the caller's body is
    read too (R8/J2): any other line that calls the same name in a way the rules allow (`obj.helper(`
    beside a bare `helper(`, `compute(` beside `Compute(`) keeps the call. A call that opens the line
    after one ending in `.` / `?.` / `::` / `->` is a receiver call (R9/K10: gofmt's `q.` / `First()`).
    Anything that cannot be read keeps the engine's call. `alias_box` ([checker or None]) shares the K11 checker,
    and its per-file tables, with the precise J3-on-keys guard (_bare_key_guard) of the same map_graph."""

    def file_of(nid) -> str:
        return str((by_id.get(nid) or {}).get("source_file") or "").replace("\\", "/")

    body_lines, prev_of = reader or _caller_body_reader(src, by_id, kinds)
    alias_of: list = alias_box if alias_box is not None else [None]   # the K11 checker, built on first need

    def verdict(text, name, t, sf, tf, low_s, ln):
        """The rule a call of `name` on this one line breaks: 'case' / 'bare-method' / 'go-selector',
        'ok' when the line calls it in an allowed way, None when the line does not call it."""
        sites = _call_sites(text, name, prev=lambda: prev_of(sf, ln))
        if not sites:
            if low_s.endswith(_CASE_SENSITIVE_EXTS) and _call_sites(text, name, ignore_case=True):
                return "case"
            return None
        bare = [q for k, q in sites if k == "bare"]
        qual = [q for k, q in sites if k == "qualified"]
        if (bare and not qual and kinds.get(t) == "Method" and tf != sf
                and low_s.endswith(_BARE_CALL_NO_METHOD_EXTS)):
            return "bare-method"
        # (a _test.go caller is exempt: an external test package `package x_test` lives in the same
        # directory and legitimately calls `x.F()`)
        if (sf.endswith(".go") and not sf.endswith("_test.go") and qual and not bare
                and kinds.get(t) == "Function" and tf.endswith(".go") and _dir_of(tf) == _dir_of(sf)
                and all(qual)):
            return "go-selector"
        return "ok"

    def check(link, s, t):
        sf, tf = file_of(s), file_of(t)
        low_s = sf.lower()
        if sf.endswith(".go") and not sf.endswith("_test.go") and tf.endswith("_test.go"):
            return "go-test-file"
        if src is None:
            return None
        name = _bare((by_id.get(t) or {}).get("label", ""))
        at = _link_line(link)
        text = src.line(sf, at) if name else None
        if text is None:
            return None
        why = verdict(text, name, t, sf, tf, low_s, at)
        if why in (None, "ok"):
            return None
        for ln in body_lines(s, sf):
            if ln != at and verdict(src.line(sf, ln) or "", name, t, sf, tf, low_s, ln) == "ok":
                return None
        if why == "bare-method":
            if alias_of[0] is None:
                alias_of[0] = _bare_alias_checker(src, by_id, kinds, body_lines)
            if alias_of[0](s, t, name, sf, tf):
                return None
        return why

    return check


# ---- S1b review (DEEPMAP_PRECISE_FIXES): J3 on precise KEYS ------------------------------------------------
# A call written without a receiver never reaches a method in Python / Go / Rust / JS / TS. The map-time filter
# above drops such a call the engine bound to ANOTHER file's method, and the push-time J3 rule
# (sync._bare_unreachable) drops a same-file method of ANOTHER class from a NAME's fan-out, but a precise key
# skipped both: graphify binds werkzeug's bare `get_host(environ)` (`from ..wsgi import get_host`) in
# Map.bind_to_environ to the same file's MapAdapter.get_host, and click's `shell_complete(...)` (a function-local
# import) in Command._main_shell_completion to Parameter.shell_complete — and precise CALLS made both firm
# EXTRACTED edges (both engines). Such a key is now dropped: no edge, and no name either (the flag-off name
# `MapAdapter.get_host` resolves to the same impossible method). The rule is J3's own, judged like the map-time
# filter judges a call: the engine's line calls the name only bare, no other line of the caller's body calls it
# through a receiver (one such line keeps the key), and the name cannot be a VALUE holding a bound method
# (_bare_alias_checker, R9/K11). The caller's own class (a class body runs its earlier defs by bare name), an
# owner-less method, a :Function target, a language with an implicit this, a line that shows no call and an
# unreadable file keep the key. Like the map-time filter's cross-file drop, a dropped key is no longer there for
# the opt-in DEEPMAP_LOCAL_DEF_CALLS to re-bind to a module-level def of the caller's file.
def _bare_key_guard(src, by_id, kinds, key_to_name, reader=None, alias_box=None):
    """ruled_out(link, s, t) -> True when the precise key the engine gave call `link` from caller `s` to :Method
    `t` names a method the call cannot reach: the caller's file is in a language without an implicit this
    (_BARE_CALL_NO_METHOD_EXTS), `t` is a :Method in another file or of a class other than the caller's own
    (sync._bare_unreachable over map_graph's names: a :Class caller is its own class, a :Method's class is the
    owner its name carries, a :Module or :Function belongs to none; an owner-less method is kept), the link's own
    line calls t's name only bare, no other line of the caller (its body; a :Module's whole file) calls it through a
    receiver, and the name is no value in the caller's scope (_bare_alias_checker). A body longer than the reader's
    span, a :Module file over _P1B_MODULE_MAX_LINES lines, anything unread or unclear keeps the key. Never
    raises. `alias_box` shares the K11 checker with the false-call filter (_false_call_filter) of the same graph."""
    body_lines, prev_of = reader or _caller_body_reader(src, by_id, kinds)
    alias_of: list = alias_box if alias_box is not None else [None]

    def file_of(nid) -> str:
        return str((by_id.get(nid) or {}).get("source_file") or "").replace("\\", "/")

    def class_of(nid):
        kind = kinds.get(nid)
        if kind not in ("Class", "Method", "Function"):
            return None
        name = str(key_to_name.get(nid) or "")
        if kind == "Class":
            return name or None
        return name.rsplit(".", 1)[0] if "." in name else None

    def unreachable(s, t, sf: str, tf: str) -> bool:
        if kinds.get(t) != "Method":
            return False
        if tf != sf:
            return True
        tname = str(key_to_name.get(t) or "")
        return "." in tname and class_of(s) != tname.rsplit(".", 1)[0]

    def verdict(sf: str, ln, name: str):
        """'bare' when line `ln` calls `name` only without a receiver, 'ok' when it calls it through one, None
        when it does not call it (as far as the line scan sees)."""
        sites = _call_sites(src.line(sf, ln) or "", name, prev=lambda: prev_of(sf, ln))
        if not sites:
            return None
        return "bare" if all(k == "bare" for k, _q in sites) else "ok"

    def span(s, sf: str):
        """The lines read for another call of the name, or None when they cannot be read whole: a :Module's whole
        file (its statements sit between the defs; one over _P1B_MODULE_MAX_LINES lines is not read), else the
        reader's body span unless it was cut at _RESCUE_SPAN before the end of the file (the rest of the body is
        unknown; a span that reaches the file's end — the file's last node — was read whole)."""
        lines = src.lines(sf)
        if not lines:
            return None
        if kinds.get(s) == "Module":
            return range(1, len(lines) + 1) if len(lines) <= _P1B_MODULE_MAX_LINES else None
        sp = body_lines(s, sf)
        return None if (len(sp) > _RESCUE_SPAN and sp[-1] < len(lines)) else sp

    def ruled_out(link, s, t) -> bool:
        try:
            sf, tf = file_of(s), file_of(t)
            if not sf.lower().endswith(_BARE_CALL_NO_METHOD_EXTS) or not unreachable(s, t, sf, tf):
                return False
            name = _bare((by_id.get(t) or {}).get("label", ""))
            at = _link_line(link)
            if not name or at is None or verdict(sf, at, name) != "bare":
                return False
            lines = span(s, sf)
            if lines is None or any(verdict(sf, ln, name) == "ok" for ln in lines if ln != at):
                return False
            if alias_of[0] is None:
                alias_of[0] = _bare_alias_checker(src, by_id, kinds, body_lines)
            return not alias_of[0](s, t, name, sf, tf)
        except Exception:  # noqa: BLE001 — anything that cannot be read keeps the engine's key
            return False

    return ruled_out


def _caller_body_reader(src, by_id, kinds):
    """(body_lines(s, sf), prev_of(sf, ln)) over the engine nodes of one graph — shared by the false-call
    filter (R8/J2, R9/K10) and the precise-CALLS receiver guard (R10/P1):
      body_lines  the caller's own lines: a Python def/class's ast span; otherwise up to the next engine
                  node of the file (nested closures included — reading more can only keep a call). Bounded.
      prev_of     the code line before line `ln` of `sf` (K10), or None — also for a Python line inside a
                  multi-line string (a docstring sentence ending in `.` is prose, not a receiver)."""

    def file_of(nid) -> str:
        return str((by_id.get(nid) or {}).get("source_file") or "").replace("\\", "/")

    starts: dict = {}                          # file -> sorted start lines of its non-Module engine nodes
    py_ends: dict = {}                         # file -> {(def line, name): end line}
    py_str_lines: dict = {}                    # Python file -> line numbers inside a multi-line string

    def body_lines(s, sf: str):
        """The caller's own lines: a Python def/class's ast span; otherwise up to the next engine node
        of the file (nested closures included — reading more can only keep a call). Bounded."""
        start = _line_of(by_id.get(s) or {})
        end = None
        if sf.lower().endswith(_PY_EXTS):
            if sf not in py_ends:
                import ast
                tree = src.tree(sf)
                py_ends[sf] = {(d.lineno, d.name): getattr(d, "end_lineno", None) for d in ast.walk(tree)
                               if isinstance(d, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))} \
                    if tree is not None else {}
            end = py_ends[sf].get((start, _bare((by_id.get(s) or {}).get("label", "")).rsplit(".", 1)[-1]))
        if end is None:
            if not starts:
                for nid, kind in kinds.items():
                    if kind != "Module":
                        starts.setdefault(file_of(nid), set()).add(_line_of(by_id.get(nid) or {}))
                for k in list(starts):
                    starts[k] = sorted(starts[k])
            nxt = next((ln for ln in starts.get(sf, ()) if ln > start), None)
            end = nxt - 1 if nxt else start + _RESCUE_SPAN
        return range(start, min(end, start + _RESCUE_SPAN) + 1)

    def prev_of(sf: str, ln):
        """The code line before line `ln` of `sf` (K10), or None — also for a Python line inside a
        multi-line string (a docstring sentence ending in `.` is prose, not a receiver)."""
        lines = src.lines(sf)
        try:
            i = int(ln) - 1
        except (TypeError, ValueError):
            return None
        if not lines or not 0 <= i < len(lines):
            return None
        py = sf.lower().endswith(_PY_EXTS)
        if py:
            if sf not in py_str_lines:
                py_str_lines[sf] = _py_string_lines(lines)
            if i + 1 in py_str_lines[sf]:
                return None
        return _prev_code_line(lines, i, py)

    return body_lines, prev_of


# ---- R10/P1 (DEEPMAP_PRECISE_CALLS only): a member call is never keyed to a module-level def ---------------
# graphify binds a call by NAME, so `obj.shell_complete(ctx, x)` (click's ShellComplete.get_completions) and
# `self._stream.isatty()` (click's _NonClosingTextIOWrapper.isatty) were bound to the same-file MODULE-LEVEL
# functions shell_complete / isatty — and precise CALLS turned those guesses into firm EXTRACTED edges while
# the real polymorphic targets (Command/Group/Parameter.shell_complete) were dropped. The same engine binds
# `util.helper()` / `ns.helper()` / Rust `util::helper()` to a same-file `helper` when one exists. A member call
# reaches a module-level function only through a MODULE / namespace / package / path that names it, so the key
# stays only when some call of the name in the caller's body is bare or goes through such a reference. A call
# that is ONLY a member call becomes the bare name again (the push fans it out exactly as with the flag off);
# a caller that calls the name both ways keeps the key AND gets the name (its member call's targets). The
# push-time pass sync._unkey_receiver_calls (W6) applies the same Python rule; on this module's output it
# finds nothing left to change.
_P1_JS_SELF_HEADS = frozenset({"exports", "module"})          # CommonJS: `exports.f()` is the file's own f
_P1_JS_GLOBAL_HEADS = frozenset({"exports", "module", "window", "globalThis", "global", "self"})
_P1_RS_SAME_FILE_HEADS = frozenset({"self", "super", "crate"})  # a path that can stay in the same file
_JS_IMPORT_CLAUSE = re.compile(r"\bimport\s+([^;'\"`]*?)\s*\bfrom\s*['\"]", re.S)
_JS_IMPORT_EQ = re.compile(r"\bimport\s+([A-Za-z_$][\w$]*)\s*=\s*require\b")
_JS_REQUIRE_BIND = re.compile(r"\b(?:const|let|var)\s+([^=;]+?)\s*=\s*(?:await\s+)?(?:require|import)\s*\(")
_JS_IMPORT_NOISE = frozenset({"type", "typeof", "as", "from", "import", "default"})
_GO_IMPORT_BLOCK = re.compile(r"(?m)^[ \t]*import[ \t]*\(([^)]*)\)")
_GO_IMPORT_LINE = re.compile(r"(?m)^[ \t]*import[ \t]+([^\s(][^\n]*)$")


def _js_import_words(text: str) -> set:
    """Every identifier a JS/TS file's import / require statements mention (`import ... from`, `import x = require`,
    `const ... = require(` / `import(`) — a superset of the names they bind (over-counting only keeps keys; R10/P1, and
    the S1 re-key's namespace / imported-name test)."""
    out: set = set()
    for rx in (_JS_IMPORT_CLAUSE, _JS_IMPORT_EQ, _JS_REQUIRE_BIND):
        for m in rx.finditer(text):
            out.update(_CHAIN_IDENT.findall(m.group(1)))
    return out - _JS_IMPORT_NOISE


def _py_import_targets(tree, nodes=None) -> dict:
    """{bound name: dotted import target} for every import anywhere in a Python `ast` (sync._import_aliases'
    rule): `import a.b` binds a -> 'a', `import a.b as c` binds c -> 'a.b', `from . import x` binds x -> '.x',
    `from p import q` binds q -> 'p.q'. A name bound to two different targets maps to None (unknown). `nodes`
    (R11/R2): only the imports among these ast nodes — a module's own (_py_module_level_nodes)."""
    import ast
    out: dict = {}

    def bind(name, target):
        out[name] = target if out.get(name, target) == target else None

    for node in (ast.walk(tree) if nodes is None else nodes):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.asname:
                    bind(a.asname, a.name)
                else:
                    top = a.name.split(".")[0]
                    bind(top, top)
        elif isinstance(node, ast.ImportFrom):
            mod = "." * int(node.level or 0) + (node.module or "")
            for a in node.names:
                if a.name != "*":
                    bind(a.asname or a.name, (mod + a.name) if (not mod or mod.endswith(".")) else f"{mod}.{a.name}")
    return out


def _py_module_names(source_file) -> set:
    """The names a module reference can use for `source_file`: its stem (none for an `__init__`) and every
    directory of its path (a package that re-exports it) — sync._module_names_of's rule."""
    parts = [p for p in str(source_file or "").replace("\\", "/").split("/") if p]
    if not parts:
        return set()
    stem = parts[-1].rsplit(".", 1)[0]
    return set(parts[:-1]) | ({stem} if stem != "__init__" else set())


_BARE_CALL = object()      # _py_scope_calls: the `root` of a bare `f(...)` call
_PY_STMT_LISTS = ("body", "orelse", "finalbody", "handlers", "cases")


def _py_stmt_index(tree) -> dict:
    """A walk over the STATEMENTS of a Python `ast` only (defs and imports are statements — no expression is
    visited): `defs_at` {(line, name): [def/class nodes]} — the def line and each decorator line — and
    `imports`, _py_import_targets' table of every import anywhere in the file (R10/P1)."""
    import ast
    defs_at: dict = {}
    imports: dict = {}

    def bind(name, target):
        imports[name] = target if imports.get(name, target) == target else None

    stack = [tree]
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            for ln in {node.lineno, *(x.lineno for x in node.decorator_list)}:
                defs_at.setdefault((ln, node.name), []).append(node)
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.asname:
                    bind(a.asname, a.name)
                else:
                    top = a.name.split(".")[0]
                    bind(top, top)
        elif isinstance(node, ast.ImportFrom):
            mod = "." * int(node.level or 0) + (node.module or "")
            for a in node.names:
                if a.name != "*":
                    bind(a.asname or a.name, (mod + a.name) if (not mod or mod.endswith(".")) else f"{mod}.{a.name}")
        for field in _PY_STMT_LISTS:
            for child in getattr(node, field, None) or ():
                if isinstance(child, ast.AST):
                    stack.append(child)
    return {"defs_at": defs_at, "imports": imports}


def _py_region(roots, has_node=None) -> list:
    """The ast nodes ONE graph scope evaluates, walked from `roots`: every statement and expression under them
    (nested if / try / with / for / while blocks and lambdas included), plus the decorators, defaults,
    annotations, bases and keywords of a def / class met on the way (they run where the statement is). The BODY
    of a def / class is its own node's — unless `has_node` (R11c/C2) says the graph has NO node for it: then its
    body and its parameters are this scope's too, exactly as _calls_plus attributes a node-less def's calls to
    the node that encloses it."""
    import ast
    out: list = []
    todo = list(roots)
    while todo:
        x = todo.pop()
        out.append(x)
        if isinstance(x, ast.ClassDef):
            todo.extend([*x.decorator_list, *x.bases, *x.keywords])
            if has_node is not None and not has_node(x):
                todo.extend(x.body)
        elif isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef)):
            a = x.args
            params = [p for p in (*a.posonlyargs, *a.args, *a.kwonlyargs, a.vararg, a.kwarg) if p is not None]
            todo.extend([*x.decorator_list, *a.defaults, *(d for d in a.kw_defaults if d is not None)])
            todo.extend(p.annotation for p in params if p.annotation is not None)
            if x.returns is not None:
                todo.append(x.returns)
            if has_node is not None and not has_node(x):
                out.extend(params)                   # its parameters bind in this scope (annotations walked above)
                todo.extend(x.body)
        else:
            todo.extend(ast.iter_child_nodes(x))
    return out


def _py_def_has_node(keys):
    """R11c/C2: does the graph have a node for this def / class statement? _calls_plus's own test — the node's
    (start line, bare name) equals the statement's (def line, name). `keys`: {(line, bare name)} of the file's
    Function / Method / Class nodes."""
    return lambda st: (getattr(st, "lineno", None), getattr(st, "name", None)) in keys


def _py_bound_import_name(alias, is_from: bool) -> str:
    """The name one `import` alias binds: `import a.b` -> a, `import a.b as c` -> c, `from p import q` -> q."""
    return alias.asname or (alias.name if is_from else alias.name.split(".")[0])


def _py_scope_local(nodes, aliases: dict) -> set:
    """R11c/C3: the names `nodes` (one scope's) bind as values — Store / Del targets, parameters, except names —
    that shadow an import. An import-bound name (a key of `aliases`) is kept as the import only when every other
    binding of it among `nodes` is a FALL-BACK of that import: `name = None` (plain or annotated), or an
    assignment inside an `except` handler of a `try` whose body imports that name (`try: import x as y` /
    `except ImportError: y = Stub()` binds ONE variable). Any other rebinding (`h = Wrapper()` after `import
    helpers as h`) makes it a value again, so a receiver call through it is no longer the module's. A lambda's
    parameter and a comprehension's target live in their own scope, so they never rebind the import (they stay
    in the set for a name no import binds, as before)."""
    import ast
    region = {id(x) for x in nodes}
    fallback: set = set()                        # ids of bindings that are an import's fall-back, or in an inner scope
    try_types = (ast.Try,) + ((ast.TryStar,) if hasattr(ast, "TryStar") else ())
    for x in nodes:
        if isinstance(x, ast.Lambda):
            a = x.args
            fallback.update(id(p) for p in (*a.posonlyargs, *a.args, *a.kwonlyargs, a.vararg, a.kwarg) if p is not None)
        elif isinstance(x, ast.comprehension):
            fallback.update(id(y) for y in ast.walk(x.target) if isinstance(y, ast.Name))
        elif (isinstance(x, (ast.Assign, ast.AnnAssign)) and isinstance(x.value, ast.Constant)
              and x.value.value is None) or (isinstance(x, ast.AnnAssign) and x.value is None):
            # `y = None` / `y: T = None` — and a bare annotation `y: T`, which binds nothing at all
            for t in (x.targets if isinstance(x, ast.Assign) else [x.target]):
                if isinstance(t, ast.Name):
                    fallback.add(id(t))
        elif isinstance(x, try_types):
            imported = {_py_bound_import_name(a, isinstance(y, ast.ImportFrom))
                        for st in x.body for y in ast.walk(st)
                        if id(y) in region and isinstance(y, (ast.Import, ast.ImportFrom)) for a in y.names}
            if imported:
                fallback.update(id(y) for h in x.handlers for st in h.body for y in ast.walk(st)
                                if id(y) in region and isinstance(y, ast.Name) and isinstance(y.ctx, ast.Store)
                                and y.id in imported)
    bound: set = set()
    rebound: set = set()
    for x in nodes:
        if isinstance(x, ast.Name) and isinstance(x.ctx, (ast.Store, ast.Del)):
            bound.add(x.id)
            if id(x) not in fallback:
                rebound.add(x.id)
        elif isinstance(x, ast.arg):
            bound.add(x.arg)
            if id(x) not in fallback:
                rebound.add(x.arg)
        elif isinstance(x, ast.ExceptHandler) and x.name:
            bound.add(x.name)
            rebound.add(x.name)
    return {n for n in bound if n not in aliases or n in rebound}


def _py_module_scopes(tree, has_node=None) -> list:
    """[(nodes, aliases, local)] — the scopes whose calls a Python :Module node owns, for P1's site kinds (map time
    here, push time in sync._unkey_receiver_calls). First the module's OWN scope (R11/R2): its module-level nodes,
    the imports among them (an import inside a def binds a name of that def) and the names they bind as values
    (_py_scope_local, R11c/C3). Then, when `has_node` is given (R11c/C2), one scope per def / class the graph has
    NO node for (graphify merges ids: if/else twin defs, `def config` beside `class Config`, `_main` beside `main`,
    a duplicate def) — DEEPMAP_GRAPHIFY_CALLS_PLUS attributes those bodies' calls to the :Module, so their call
    sites are the module's too, each read with its own imports and values over the module's."""
    import ast
    m_nodes = _py_region(list(ast.iter_child_nodes(tree)))
    m_aliases = _py_import_targets(tree, m_nodes)
    m_local = _py_scope_local(m_nodes, m_aliases)
    scopes = [(m_nodes, m_aliases, m_local)]
    if has_node is not None:
        for x in m_nodes:
            if isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and not has_node(x):
                a = getattr(x, "args", None)
                params = [p for p in (*a.posonlyargs, *a.args, *a.kwonlyargs, a.vararg, a.kwarg) if p is not None] \
                    if a is not None else []
                x_nodes = params + _py_region(x.body, has_node)
                x_imports = _py_import_targets(tree, x_nodes)
                scopes.append((x_nodes, {**m_aliases, **x_imports},
                               (m_local - set(x_imports)) | _py_scope_local(x_nodes, x_imports)))
    return scopes


def _py_rebound_globals(tree, file_aliases: dict) -> set:
    """R11c/C3: the names this file imports somewhere (`file_aliases`, its whole import table) that the MODULE
    scope rebinds as a real value (_py_scope_local: `import helpers as h` / `h = Wrapper()`). A def that reads
    such a global without importing the name itself reads that value, so its `h.helper()` is a receiver call,
    exactly as the module's own is — callers subtract the names the def imports."""
    return {n for n in _py_module_scopes(tree)[0][2] if n in file_aliases}


def _py_module_level_nodes(tree, has_node=None) -> list:
    """R11/R2: the ast nodes a Python MODULE evaluates in its own scope — every module-level statement and
    expression (nested if / try / with / for / while blocks and lambdas included) plus the decorators,
    defaults, annotations, bases and keywords of a def / class, which run where the statement is — but
    never the BODY of a def or a class (those calls are that node's). R11c/C2: with `has_node`, also the body
    of every def / class the graph has NO node for (_py_module_scopes). sync._py_module_level_nodes is this
    same function at push time."""
    return [x for nodes, _a, _l in _py_module_scopes(tree, has_node) for x in nodes]


def _py_scope_calls(scope, nodes=None):
    """(calls, local) of one Python scope — a def / class node or a whole module — in ONE ast.walk: `calls`
    [(called name, receiver root or _BARE_CALL, middle attributes)] and `local`, the names it (nested scopes
    included) binds as values: parameters, Store targets, except names (_py_locally_bound's rule). Everything
    nested in the scope counts, decorators and defaults too (R10/P1). `nodes` (R11/R2): walk exactly these
    ast nodes instead — a :Module's own (_py_module_level_nodes)."""
    import ast
    calls: list = []
    local: set = set()
    for node in (ast.walk(scope) if nodes is None else nodes):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name):
                calls.append((f.id, _BARE_CALL, ()))
            elif isinstance(f, ast.Attribute):
                parts, cur = [], f.value
                while isinstance(cur, ast.Attribute):
                    parts.append(cur.attr)
                    cur = cur.value
                calls.append((f.attr, cur.id if isinstance(cur, ast.Name) else None, tuple(reversed(parts))))
        elif isinstance(node, ast.Name):
            if isinstance(node.ctx, (ast.Store, ast.Del)):
                local.add(node.id)
        elif isinstance(node, ast.arg):
            local.add(node.arg)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            local.add(node.name)
    return calls, local


def _receiver_unkeyer(src, by_id, kinds, reader=None, lines=None):
    """verdict(s, t) for precise CALLS on the engine's key from caller `s` to target `t` (R10/P1): None =
    keep the key; 'name' = no call of t's name in s's body can name t (only member calls) — the bare name
    instead; 'both' = s calls the name both ways — the key AND the bare name. Only a module-level :Function
    target is judged; the reference a member call goes through decides:
      bare `f(...)`   names it (the audit's 150 of 153 same-file EXTRACTED edges)
      Python (ast)    `mod.f(` / `pkg.sub.f(` whose receiver is an import (not shadowed by a local of the
                      caller) naming the TARGET's module or one of its packages, target in another file;
                      `os.f(`, `self.x.f(`, `obj.f(`, `super().f(`, `f().g(` never do
      Go              `pkg.F(` with pkg a word of the file's import specs, target in another file
      Rust            a `::` path to another file (`util::f()`), or `self::` / `super::` / `crate::` in the same
                      file; a `.` call is a method call, never a free fn
      JS/TS           a name the file imports / requires, or a global object, target in another file;
                      `exports.f()` / `module.exports.f()` in the same file; `this.f()` / `obj.f()` never
    Callers in other languages, a caller that cannot be located, no visible call site or an unreadable file
    keep the engine's key (today's behaviour). Comment lines are not read. `lines` (map_graph's {node id:
    corrected line}, R11c/C2) places the nodes exactly where _calls_plus sees them. Never raises."""
    import ast
    body_lines, prev_of = reader or _caller_body_reader(src, by_id, kinds)
    py_files: dict = {}
    py_sites: dict = {}
    words: dict = {}
    memo: dict = {}
    member_seen: dict = {}
    texts: dict = {}

    def file_of(nid) -> str:
        return str((by_id.get(nid) or {}).get("source_file") or "").replace("\\", "/")

    def lang(sf: str):
        low = sf.lower()
        if low.endswith(_PY_EXTS):
            return "py"
        if low.endswith(".go"):
            return "go"
        if low.endswith(".rs"):
            return "rs"
        if low.endswith(_TSJS_EXTS):
            return "js"
        return None

    def py_file(sf: str):
        if sf not in py_files:
            tree = src.tree(sf)
            py_files[sf] = dict(_py_stmt_index(tree), tree=tree) if tree is not None else None
        return py_files[sf]

    node_keys: dict = {}           # source file -> {(line, bare name)} of its Function / Method / Class nodes

    def has_node(sf: str):
        """R11c/C2: _calls_plus's test of whether a def / class of `sf` has a graph node (map_graph's lines)."""
        if sf not in node_keys:
            node_keys[sf] = {(int((lines or {}).get(nid, _line_of(by_id.get(nid) or {}))),
                              _bare((by_id.get(nid) or {}).get("label", "")).rsplit(".", 1)[-1])
                             for nid, kind in kinds.items()
                             if kind in ("Function", "Method", "Class") and file_of(nid) == sf}
        return _py_def_has_node(node_keys[sf])

    def py_calls(s, sf: str):
        """{called name: [('bare', None) | ('mod', dotted import) | ('member', None)]} over the caller's own
        ast — its def / class (decorators and defaults included), a :Module's own scopes (R11/R2: its MODULE-LEVEL
        statements — its defs' and classes' bodies are theirs — plus, R11c/C2, the bodies of the defs / classes
        the graph has no node for, whose calls _calls_plus gives the :Module) — or None when it cannot be located."""
        if s not in py_sites:
            facts = py_file(sf)
            res = None
            if facts is not None:
                node = by_id.get(s) or {}
                if kinds.get(s) == "Module":
                    # R11/R2 + R11c/C2/C3: each scope with its own nodes, imports (one inside a def binds that
                    # def's name) and values (_py_scope_local: an import's fall-back never shadows it, a real
                    # rebinding does) — sync._unkey_receiver_calls reads the same _py_module_scopes at push time
                    if "module_scopes" not in facts:
                        facts["module_scopes"] = [(_py_scope_calls(None, nodes=nodes)[0], aliases, local)
                                                  for nodes, aliases, local
                                                  in _py_module_scopes(facts["tree"], has_node(sf))]
                    units = facts["module_scopes"]
                else:
                    nm = _bare(node.get("label", "")).rsplit(".", 1)[-1]
                    units = []
                    for sc in facts["defs_at"].get((_line_of(node), nm), []):
                        sc_calls, local = _py_scope_calls(sc)
                        # R11c/C3: a global the module rebinds (`h = Wrapper()` after `import helpers as h`) is a
                        # value here too, unless this def imports the name itself
                        if "rebound" not in facts:
                            facts["rebound"] = _py_rebound_globals(facts["tree"], facts["imports"])
                        if facts["rebound"]:
                            local = local | (facts["rebound"] - set(_py_import_targets(sc)))
                        units.append((sc_calls, facts["imports"], local))
                if units:
                    res = {}
                    for sc_calls, imports, local in units:
                        for name, root, parts in sc_calls:
                            if root is _BARE_CALL:
                                res.setdefault(name, []).append(("bare", None))
                                continue
                            target = imports.get(root) if root and root not in local else None
                            if target:
                                res.setdefault(name, []).append(("mod", ".".join((target,) + parts)))
                            else:
                                res.setdefault(name, []).append(("member", None))
            py_sites[s] = res
        return py_sites[s]

    def import_words(sf: str, language: str) -> set:
        """Every identifier the file's import / require statements mention (a superset of the names they
        bind — over-counting only keeps keys)."""
        if (sf, language) not in words:
            out: set = set()
            lines = src.lines(sf)
            text = "\n".join(lines) if lines else ""
            if language == "go":
                for m in list(_GO_IMPORT_BLOCK.finditer(text)) + list(_GO_IMPORT_LINE.finditer(text)):
                    out.update(re.findall(r"[A-Za-z_]\w*", m.group(1)))
            elif language == "js":
                out = _js_import_words(text)
            words[(sf, language)] = out
        return words[(sf, language)]

    def member_mention(sf: str, name: str) -> bool:
        """False only when the file NEVER writes `name` right after a `.` (blanks, newlines and a `\\`
        continuation looked through) followed by `(` — then every call of it is bare and no ast is needed.
        An exact prefilter: an ast member call `x.name(...)` always has that shape."""
        if (sf, name) not in member_seen:
            if sf not in texts:
                lines = src.lines(sf)
                texts[sf] = "\n".join(lines) if lines else ""
            text = texts[sf]
            found = False
            i = text.find(name)
            while i >= 0 and not found:
                k = i + len(name)
                while k < len(text) and text[k] in " \t\r\n\\":
                    k += 1
                if k < len(text) and text[k] == "(":
                    j = i - 1
                    while j >= 0 and text[j] in " \t\r\n\\":
                        j -= 1
                    found = j >= 0 and text[j] == "."
                i = text.find(name, i + 1)
            member_seen[(sf, name)] = found
        return member_seen[(sf, name)]

    def text_sites(s, sf: str, name: str) -> list:
        out: list = []
        for ln in body_lines(s, sf):
            raw = src.line(sf, ln)
            if not raw or name not in raw or _is_comment_only(raw.strip(), False):
                continue
            text = _strip_trailing_comment(raw, False)
            out.extend(_call_site_details(text, name, prev=lambda ln=ln: prev_of(sf, ln)))
        return out

    def judge(s, t):
        sf, tf = file_of(s), file_of(t)
        language = lang(sf)
        name = _bare((by_id.get(t) or {}).get("label", ""))
        if language is None or not name or src is None:
            return None
        same = sf == tf
        direct = receiver = False
        if language == "py":
            if not member_mention(sf, name):
                return None                              # every call of it is bare: the key stays
            sites = (py_calls(s, sf) or {}).get(name)
            if not sites:
                return None
            mods = _py_module_names(tf)
            for kind, dotted in sites:
                if kind == "bare" or (kind == "mod" and not same
                                      and str(dotted or "").lstrip(".").split(".")[-1] in mods):
                    direct = True
                else:
                    receiver = True
        else:
            sites = text_sites(s, sf, name)
            if not sites:
                return None
            for kind, _q, op, head, n in sites:
                if kind == "bare":
                    ok = True
                elif language == "rs":
                    ok = op == "::" and (not same or head in _P1_RS_SAME_FILE_HEADS)
                elif language == "go":
                    ok = not same and op == "." and n == 1 and head in import_words(sf, "go")
                elif same:                                       # js
                    ok = head in _P1_JS_SELF_HEADS
                else:
                    ok = head is not None and (head in _P1_JS_GLOBAL_HEADS or head in import_words(sf, "js"))
                direct = direct or ok
                receiver = receiver or not ok
        if not receiver:
            return None
        return "both" if direct else "name"

    def verdict(s, t):
        if kinds.get(t) != "Function":
            return None
        if (s, t) not in memo:
            try:
                memo[(s, t)] = judge(s, t)
            except Exception:  # noqa: BLE001 — anything unreadable keeps the engine's key
                memo[(s, t)] = None
        return memo[(s, t)]

    return verdict


_RESCUE_SPAN = 400      # at most this many lines of a caller's body are read before a call is ruled out


# ---- default-OFF summaries (MODULE_DOCSTRINGS / SUMMARY_NORMALIZE) --------------------------------
_STRING_PREFIX = re.compile(r"""^\s*[rRbBuUfF]{0,2}(?:\"\"\"|'''|\"|')""")
_STRING_SUFFIX = re.compile(r"""(?:\"\"\"|''')\s*$""")
_SENTENCE_END = re.compile(r"""[.!?](?=\s+[A-Z0-9"'(\[`]|\s*$)""")


def _first_sentence(text, cap: int = 300) -> str:
    """The first sentence of a docstring: the first paragraph with whitespace runs collapsed, up to the
    first `.`/`!`/`?` that ends a sentence, then cut at a word boundary within `cap` characters (graphify
    cut its label at 80 characters mid-word and kept `r\"\"\"` prefixes and indentation runs)."""
    t = _STRING_SUFFIX.sub("", _STRING_PREFIX.sub("", str(text or "").strip())).strip()
    para = re.split(r"\n\s*\n", t, maxsplit=1)[0]
    s = " ".join(para.split())
    m = _SENTENCE_END.search(s)
    if m:
        s = s[:m.end()]
    if len(s) > cap:
        cut = s[:cap + 1]
        sp = cut.rfind(" ")
        s = (cut[:sp] if sp > 0 else s[:cap]).rstrip(" ,;:")
    return s


def _def_docstring(src: _Source, sf: str, line: int, name: str):
    """The docstring of the def/class named `name` at `line` in `sf` (ast), '' when it has none, or None
    when the file or the def cannot be found."""
    import ast
    tree = src.tree(sf)
    if tree is None:
        return None
    for d in ast.walk(tree):
        if (isinstance(d, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and d.lineno == line
                and d.name == name):
            return ast.get_docstring(d) or ""
    return None


# ---- T5#2 / T5#3: TS/JS declarations lost to id collisions + Rust trait methods ------------------
_TS_DECL = re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:declare\s+)?"
                      r"(?:(?P<fn>(?:async\s+)?function\s*\*?)\s*|(?:abstract\s+)?(?P<cls>class|interface|type|enum)"
                      r"\s+|(?P<var>const|let|var)\s+)(?P<name>[A-Za-z_$][\w$]*)")
_TS_ARROW = re.compile(r"^\s*(?::[^=]+)?=\s*(?:async\s+)?(?:function\b|\([^)]*\)\s*(?::[^=]+)?=>|[A-Za-z_$][\w$]*\s*=>)")


def _ts_decl_kind(m) -> str:
    """'fn' / 'var' / 'class' / 'type' for a _TS_DECL match (interface, type alias and enum are types)."""
    if m.group("fn"):
        return "fn"
    if m.group("var"):
        return "var"
    return "class" if m.group("cls") == "class" else "type"


def _supp_key(base: str, name: str, line: int) -> str:
    """Key of a node DeepMap recovered from source beside the engine's graph: never an engine id (those
    are casefolded [\\w]+) and never a recognizer key (`<source_file>::...`)."""
    return f"{base}@{name}:L{line}"


def _ts_node_at(src: _Source, sf: str, line: int, name: str):
    """The tree-sitter declaration node declaring `name` on `line` of a TS/JS file, or None."""
    got = src.ts_tree(sf)
    if not got:
        return None
    tree, data = got
    stack = [tree.root_node]
    while stack:
        n = stack.pop()
        if n.start_point[0] > line - 1 or n.end_point[0] < line - 1:
            continue
        if n.type in ("variable_declarator", "function_declaration", "generator_function_declaration",
                      "class_declaration", "abstract_class_declaration", "interface_declaration",
                      "type_alias_declaration", "enum_declaration") and n.start_point[0] == line - 1:
            nm = n.child_by_field_name("name")
            if nm is not None and data[nm.start_byte:nm.end_byte].decode("utf-8", "replace") == name:
                return n
        stack.extend(n.children)
    return None


def _ts_callees(node, data) -> list:
    """Identifier callees (`f(...)`) under a tree-sitter node, in source order, distinct."""
    out: list = []
    stack = [node]
    while stack:
        n = stack.pop()
        if n.type == "call_expression":
            fn = n.child_by_field_name("function")
            if fn is not None and fn.type == "identifier":
                nm = data[fn.start_byte:fn.end_byte].decode("utf-8", "replace")
                if nm not in out:
                    out.append(nm)
        stack.extend(reversed(n.children))
    return out


# What graphify's JS/TS walker (extract._extract_generic + _js_extra_walk, 0.8.39 and 0.8.51) makes a
# node of: the configured class/function declaration types anywhere except inside a function/method body
# (it never walks those for declarations), and a MODULE-level `const`/`let` declarator whose value is a
# function (0.8.39: an arrow only; 0.8.51 adds function expressions) or an object/array/as/call/new
# expression — a declarator it handles is not descended further.
_TS_ENGINE_DECL_TYPES = frozenset({"function_declaration", "class_declaration", "abstract_class_declaration",
                                   "interface_declaration", "type_alias_declaration", "enum_declaration"})
_TS_ENGINE_NO_DESCEND = frozenset({"function_declaration", "method_definition"})
_TS_ENGINE_CONST_VALUES = frozenset({"arrow_function", "function_expression", "function", "object", "array",
                                     "as_expression", "call_expression", "new_expression"})


def _ts_module_level_const(decl) -> bool:
    """A `variable_declarator` graphify makes a node of: in a `lexical_declaration` (const/let — never
    `var`) directly under the program or under a program-level `export`, with an emitted value type."""
    lex = decl.parent if decl is not None else None
    if lex is None or lex.type != "lexical_declaration":
        return False
    up = lex.parent
    if up is not None and up.type == "export_statement":
        up = up.parent
    value = decl.child_by_field_name("value")
    return up is not None and up.type == "program" and value is not None and value.type in _TS_ENGINE_CONST_VALUES


def _ts_engine_emits(ts_node) -> bool:
    """Would graphify have emitted a node for this tree-sitter declaration? (See _TS_ENGINE_DECL_TYPES.)"""
    if ts_node is None:
        return False
    if ts_node.type == "variable_declarator":
        ok = _ts_module_level_const(ts_node)
    else:
        ok = ts_node.type in _TS_ENGINE_DECL_TYPES
    anc = ts_node.parent
    while ok and anc is not None:
        if anc.type in _TS_ENGINE_NO_DESCEND or (anc.type == "variable_declarator" and _ts_module_level_const(anc)):
            ok = False
        anc = anc.parent
    return ok


def _recover_ts_collisions(src: _Source, links, by_id, key_to_name, kinds, existing) -> list:
    """T5#2: graphify's `_make_id` casefolds and strips `$`/`_`, so `export type $Parse` and `export const
    _parse = ...` (or `interface $ZodThing` and `export const $ZodThing = ...`) get ONE id and the later
    declaration disappears — zod's parse engine was not in the graph while the verdict said READY.
    Candidates come from two places, the same lines either way:
      * the engine's own evidence — graphify 0.8.39 records the lost declaration as a repeated `contains`
        link at ITS line;
      * the SOURCE (R8/J2) — the 0.8.4x+ `--no-cluster` writer collapses repeated links
        (`build.dedupe_edges`), so on 0.8.51 that evidence is gone (zod: 381 recovered -> 28). Every
        declaration line of a TS/JS file whose engine name (`_engine_id`) equals a kept node's of that
        file at ANOTHER line is a candidate, kept only when graphify would have made a node of it
        (`_ts_engine_emits`, read with tree-sitter; no grammar -> no source candidates).
    The declared name is read from the line. Returns [(node_dict, base_id, callee_names_or_None)]."""
    out: list = []
    seen: set = set()
    cands: list = []                           # (kept node id, file, line, from_source)
    groups: dict = {}                          # file -> {engine name: [kept FILE-level node ids]}
    for t in key_to_name:
        node = by_id.get(t) or {}
        sf = str(node.get("source_file") or "").replace("\\", "/")
        # a file-level entity's id is `_make_id(stem, name)`, so two of them collide exactly when their
        # names do; a method's id is class-scoped (and a Module's is the file's) — never in a group
        if sf.lower().endswith(_TSJS_EXTS) and kinds.get(t) in ("Function", "Class"):
            groups.setdefault(sf, {}).setdefault(_engine_id(_bare(node.get("label", ""))), []).append(t)
    for l in links:
        if l.get("relation") != "contains":
            continue
        t = l.get("target")
        if t not in key_to_name:
            continue
        node = by_id.get(t) or {}
        sf = str(node.get("source_file") or "").replace("\\", "/")
        if sf.lower().endswith(_TSJS_EXTS):
            cands.append((t, sf, _link_line(l), False))
    for sf in sorted(groups):
        for i, text in enumerate(src.lines(sf) or (), 1):
            m = _TS_DECL.match(text)
            if m:
                cands.extend((t, sf, i, True) for t in groups[sf].get(_engine_id(m.group("name")), ()))
    for t, sf, ln, from_source in cands:
        node = by_id.get(t) or {}
        if not ln or ln == _line_of(node) or (sf, ln) in seen:
            continue
        text = src.line(sf, ln)
        m = _TS_DECL.match(text or "")
        if not m:
            continue
        name = m.group("name")
        # A repeat of the SAME name is recovered only when it is a different kind of declaration:
        # TypeScript declares a type and a value of one name side by side (`interface $ZodThing` + `const
        # $ZodThing = ...`), while `function f(a: string); function f(a: number); function f(a) {...}` is
        # one overloaded function (same kind), not a lost entity. Deduped by (name, file, LINE): a file
        # module can share a const's name.
        if (name, sf, ln) in existing:
            continue
        if name == _bare(node.get("label", "")):
            kept = _TS_DECL.match(src.line(sf, _line_of(node)) or "")
            if kept is None or _ts_decl_kind(kept) == _ts_decl_kind(m):
                continue
        ts_node = _ts_node_at(src, sf, ln, name)
        if from_source and not _ts_engine_emits(ts_node):
            continue
        seen.add((sf, ln))
        if m.group("fn"):
            kind = "Function"
        elif m.group("cls"):
            kind = "Class"
        else:
            value = ts_node.child_by_field_name("value") if ts_node is not None else None
            if value is not None:
                kind = "Function" if value.type in ("arrow_function", "function_expression", "function") else "Class"
            else:
                kind = "Function" if _TS_ARROW.match(text[m.end():]) else "Class"
        callees = None
        if ts_node is not None:
            got = src.ts_tree(sf)
            body = ts_node.child_by_field_name("value") or ts_node.child_by_field_name("body") or ts_node
            callees = [c for c in _ts_callees(body, got[1]) if c != name] if got else None
        out.append((dict(key=_supp_key(t, name, ln), label=kind, name=name, source_file=sf, start_line=ln,
                         documented=False, body_summary=f"{summarize.humanize(name)}: {kind.lower()}"[:300]),
                    t, callees))
        existing.add((name, sf, ln))
    return out


def _rust_trait_methods(src: _Source, files, key_to_name, kinds, by_id, existing) -> list:
    """T5#3: graphify's Rust extractor does not walk a trait's body, so a trait's default methods (and its
    required signatures) never became nodes, and a default body's calls were lost. One `Trait.method`
    :Method per trait item, read with tree-sitter (graphify's own grammar dependency; skipped when it is
    not importable). Returns [(node_dict, base_id, [(callee_kind, name)] or None)]."""
    out: list = []
    for sf in sorted(files):
        got = src.ts_tree(sf)
        if not got:
            continue
        tree, data = got
        owners = {}
        mod_key = None
        for nid, nm in key_to_name.items():
            nsf = str((by_id.get(nid) or {}).get("source_file") or "").replace("\\", "/")
            if nsf != sf:
                continue
            if kinds.get(nid) == "Class":
                owners.setdefault(nm, nid)
            elif kinds.get(nid) == "Module":
                mod_key = mod_key or nid
        stack = [tree.root_node]
        while stack:
            n = stack.pop()
            if n.type != "trait_item":
                stack.extend(reversed(n.children))
                continue
            tn = n.child_by_field_name("name")
            body = n.child_by_field_name("body")
            if tn is None or body is None:
                continue
            trait = data[tn.start_byte:tn.end_byte].decode("utf-8", "replace")
            base = owners.get(trait) or mod_key
            if base is None:
                continue
            items = [c for c in body.children if c.type in ("function_item", "function_signature_item")]
            siblings = {data[c.child_by_field_name("name").start_byte:c.child_by_field_name("name").end_byte]
                        .decode("utf-8", "replace") for c in items if c.child_by_field_name("name") is not None}
            for c in items:
                fnn = c.child_by_field_name("name")
                if fnn is None:
                    continue
                fname = data[fnn.start_byte:fnn.end_byte].decode("utf-8", "replace")
                name = f"{trait}.{fname}"
                line = c.start_point[0] + 1
                if (name, sf) in existing:
                    continue
                existing.add((name, sf))
                callees = None
                blk = c.child_by_field_name("body")
                if blk is not None:
                    callees = []
                    st = [blk]
                    while st:
                        x = st.pop()
                        if x.type == "call_expression":
                            f = x.child_by_field_name("function")
                            if f is not None and f.type == "field_expression":
                                v, fld = f.child_by_field_name("value"), f.child_by_field_name("field")
                                if (v is not None and fld is not None
                                        and data[v.start_byte:v.end_byte] == b"self"):
                                    nm = data[fld.start_byte:fld.end_byte].decode("utf-8", "replace")
                                    if nm in siblings:
                                        callees.append(("sibling", f"{trait}.{nm}"))
                            elif f is not None and f.type == "scoped_identifier":
                                path, nmn = f.child_by_field_name("path"), f.child_by_field_name("name")
                                if (path is not None and nmn is not None
                                        and data[path.start_byte:path.end_byte] == b"Self"):
                                    nm = data[nmn.start_byte:nmn.end_byte].decode("utf-8", "replace")
                                    if nm in siblings:
                                        callees.append(("sibling", f"{trait}.{nm}"))
                            elif f is not None and f.type == "identifier":
                                callees.append(("name", data[f.start_byte:f.end_byte].decode("utf-8", "replace")))
                        st.extend(reversed(x.children))
                out.append((dict(key=_supp_key(base, name, line), label="Method", name=name, source_file=sf,
                                 start_line=line, documented=False,
                                 body_summary=f"{summarize.humanize(name)}: method"[:300]), base, callees))
    return out


# ---- default-OFF: DEEPMAP_GRAPHIFY_CALLS_PLUS -----------------------------------------------------
def _import_rel(p) -> str:
    """A root-relative path as py_import_resolver keys it: '/' separators, no leading './' or '/', case KEPT
    (not _norm_rel below, which case-folds on Windows for the rescue filter)."""
    s = str(p or "").replace("\\", "/")
    while s.startswith("./"):
        s = s[2:]
    return s.strip("/")


def _py_import_roots(src: _Source, nodes: list):
    """(ImportRoots over the graph's Python :Module files, {file: its ONE :Module key}) for _calls_plus — or
    (None, {}) when build/py_import_resolver.py cannot be imported (then no module-qualified call resolves:
    never the suffix guess). A file with two :Module nodes maps to none (no guess between them)."""
    mods: dict = {}
    for n in nodes:
        sf = str(n.get("source_file") or "")
        if n.get("label") == "Module" and sf.lower().endswith(_PY_EXTS):
            mods.setdefault(_import_rel(sf), []).append(n["key"])
    try:
        import py_import_resolver   # lazy, stdlib-only sibling (receiver-calls design 3.1)
        roots = py_import_resolver.ImportRoots(sorted(mods), getattr(src, "root", None))
    except Exception:  # noqa: BLE001 — no resolver: module-qualified calls stay unresolved, never guessed
        return None, {}
    return roots, {f: ks[0] for f, ks in mods.items() if len(ks) == 1}


def _calls_plus(src: _Source, nodes: list, mod_idx: dict | None = None) -> list:
    """(src_key, target_key) Python calls the engine did not emit, read from each file's `ast`: calls
    through an imported module alias / dotted module path / `from m import f`, calls to same-file
    top-level functions and classes, `self.`/`cls.` calls inside NESTED closures (the method's own body is
    the engine's and G5's), each attributed to the innermost enclosing graph node — a nested def that is
    not a node gives its calls to the def that contains it; module-level statements give theirs to the
    :Module node. Only a call that resolves to exactly ONE node is returned.

    S1b (receiver-calls design 3.1 / 6.1): a module name is resolved by IMPORT ROOT (py_import_resolver: the
    repository root, `src/`, the parent of each top-level package, and the importer's own directory only when the
    importer is not inside a package; relative imports by the importer's package path) — never by the dotted-
    suffix match `_py_module_index` makes, which read flask's `import json` inside src/flask/json/provider.py
    as the project's own flask/json package (false edges into flask.json.dumps / loads). A name that no root
    holds, that two roots hold differently, or that exists on disk outside the graph resolves to nothing.
    `mod_idx` is no longer read (kept so an old caller's positional call still works)."""
    import ast
    # module-level compound statements whose imports still bind module-scope names (M5)
    block_stmts = (ast.Try, ast.If, ast.With, ast.AsyncWith) + ((ast.TryStar,) if hasattr(ast, "TryStar") else ())
    by_file: dict = {}
    for n in nodes:
        sf = str(n.get("source_file") or "")
        if sf.lower().endswith(_PY_EXTS):
            by_file.setdefault(sf, []).append(n)
    file_of = {n["key"]: str(n.get("source_file") or "") for n in nodes}
    out: list = []
    roots, module_of_file = _py_import_roots(src, nodes)

    def module_key(dotted: str, importer_sf: str, importer_key: str, level: int = 0):
        """The :Module key the import names for this importer (S1b: by import root), or None. A module
        resolving to the importer itself is no target (as before)."""
        if roots is None:
            return None
        try:
            t = roots.module(dotted, _import_rel(importer_sf), level)
        except Exception:  # noqa: BLE001 — an unreadable name resolves to nothing
            return None
        k = module_of_file.get(t.rel) if t.kind == "file" else None
        return k if k and k != importer_key else None

    def top_level(mod_k: str, name: str):
        sf = file_of.get(mod_k)
        hits = [n["key"] for n in by_file.get(sf, []) if n["label"] in ("Function", "Class") and n["name"] == name]
        return hits[0] if len(hits) == 1 else None

    for sf, fnodes in by_file.items():
        tree = src.tree(sf)
        if tree is None:
            continue
        mod = next((n for n in fnodes if n["label"] == "Module"), None)
        if mod is None:
            continue
        at = {(n["start_line"], n["name"].rsplit(".", 1)[-1]): n for n in fnodes if n["label"] != "Module"}
        methods = {n["name"]: n["key"] for n in fnodes if n["label"] == "Method"}

        def alias_entries(st):
            """(bound name, alias) for one import statement. A `from` import keeps its own module text and
            relative LEVEL (S1b: py_import_resolver resolves `from ..json import dumps` by the importer's
            package path, never as a root-relative dotted suffix)."""
            if isinstance(st, ast.Import):
                for a in st.names:
                    if a.asname:
                        yield a.asname, ("module", a.name)
                    else:
                        yield a.name.split(".")[0], ("root", a.name.split(".")[0])
            elif isinstance(st, ast.ImportFrom):
                base, level = st.module or "", int(st.level or 0)
                for a in st.names:
                    if a.name != "*":
                        yield a.asname or a.name, ("from", base, a.name, level)

        aliases: dict = {}
        for st in tree.body:
            for name, alias in alias_entries(st):
                aliases[name] = alias
        # Round 11 (M5): an import inside a MODULE-LEVEL try / if / with binds a module alias just the same — every
        # DeepMap hook does `try: import deepmap_hook_state as _hs` / `except Exception: _hs = None`, and none of
        # its `_hs.f()` calls resolved. Such a name is added only when no top-level import binds it (those keep
        # their meaning), and a name the branches bind to two DIFFERENT targets is left unresolved (no guess).
        nested: dict = {}
        clash: set = set()
        stack = [st for st in tree.body if isinstance(st, block_stmts)]
        while stack:
            st = stack.pop()
            kids = [*(getattr(st, "body", None) or ()), *(getattr(st, "orelse", None) or ()),
                    *(getattr(st, "finalbody", None) or ())]
            for h in getattr(st, "handlers", None) or ():
                kids.extend(getattr(h, "body", None) or ())
            for kid in kids:
                if isinstance(kid, block_stmts):
                    stack.append(kid)
                for name, alias in alias_entries(kid):
                    if nested.setdefault(name, alias) != alias:
                        clash.add(name)
        for name, alias in nested.items():
            if name not in aliases and name not in clash:
                aliases[name] = alias

        def resolve(call, cls_name, nested, local_defs):
            f = call.func
            if isinstance(f, ast.Name):
                if f.id in local_defs:
                    return None
                a = aliases.get(f.id)
                if a and a[0] == "from":           # `from m import f` / `from . import f` (the package itself)
                    mk = module_key(a[1], sf, mod["key"], a[3]) if (a[1] or a[3]) else None
                    return top_level(mk, a[2]) if mk else None
                return top_level(mod["key"], f.id)
            if isinstance(f, ast.Attribute):
                chain, cur = [f.attr], f.value
                while isinstance(cur, ast.Attribute):
                    chain.insert(0, cur.attr)
                    cur = cur.value
                if not isinstance(cur, ast.Name):
                    return None
                head = cur.id
                if head in ("self", "cls") and nested and cls_name and len(chain) == 1:
                    return methods.get(f"{cls_name}.{chain[0]}")
                a = aliases.get(head)
                if not a:
                    return None
                fname, mids = chain[-1], chain[:-1]
                level = 0
                if a[0] == "module":
                    dotted = ".".join([a[1]] + mids)
                elif a[0] == "root":
                    dotted = ".".join([a[1]] + mids)
                else:                                   # `from pkg import mod` then `mod.f()` (a submodule)
                    dotted = ".".join([x for x in (a[1], a[2]) if x] + mids)
                    level = a[3]
                mk = module_key(dotted, sf, mod["key"], level)
                return top_level(mk, fname) if mk else None
            return None

        def bound_names(fn) -> set:
            """Names a def binds locally (parameters, assignments, for/with/except targets, local
            imports): a call through one of them is not a call of a same-file function."""
            a = fn.args
            names = {p.arg for p in list(a.posonlyargs) + list(a.args) + list(a.kwonlyargs)}
            names |= {p.arg for p in (a.vararg, a.kwarg) if p is not None}
            for x in ast.walk(fn):
                if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Store):
                    names.add(x.id)
                elif isinstance(x, (ast.Import, ast.ImportFrom)):
                    names |= {(al.asname or al.name).split(".")[0] for al in x.names}
            return names

        def visit(body, src_key, cls_name, nested, local_defs):
            local_defs = set(local_defs) | {s.name for s in body
                                            if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef))}
            for st in body:
                if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    own = at.get((st.lineno, st.name))
                    if isinstance(st, ast.ClassDef):
                        if own is not None:
                            visit(st.body, own["key"], st.name, False, set())
                        else:
                            visit(st.body, src_key, cls_name, True, local_defs)
                        continue
                    shadow = bound_names(st)
                    if own is not None:
                        visit(st.body, own["key"], cls_name, False, shadow)
                    else:                               # a nested def that is no node: its calls are
                        visit(st.body, src_key, cls_name, True, local_defs | shadow)  # the encloser's
                    for d in st.decorator_list + list(st.args.defaults) + list(st.args.kw_defaults or []):
                        if d is not None:
                            scan(d, src_key, cls_name, nested, local_defs)
                    continue
                scan(st, src_key, cls_name, nested, local_defs)

        def scan(node, src_key, cls_name, nested, local_defs):
            stack = [node]
            while stack:
                x = stack.pop()
                if isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    visit([x], src_key, cls_name, nested, local_defs)
                    continue
                if isinstance(x, ast.Lambda):
                    stack.extend(ast.iter_child_nodes(x))
                    continue
                if isinstance(x, ast.Call):
                    tk = resolve(x, cls_name, nested, local_defs)
                    if tk and tk != src_key:
                        out.append((src_key, tk))
                stack.extend(ast.iter_child_nodes(x))

        for st in tree.body:
            if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                visit([st], mod["key"], None, False, set())
            elif not isinstance(st, (ast.Import, ast.ImportFrom)):
                scan(st, mod["key"], None, False, set())
    return list(dict.fromkeys(out))


def map_graph(graph: dict, *, root=None, stats: dict | None = None,
              source: _Source | None = None) -> tuple[list[dict], list[tuple], list[tuple]]:
    """Map a parsed graphify graph.json into (nodes, calls, imports) — sync.py's push shape.

    Pure unless `root` is given: then source files are read to correct what the engine gets wrong —
    graphify's last-definition-wins binding of in-file Python method calls (G5, `_self_call_rebinder`;
    off with DEEPMAP_SELF_CALL_REBIND=0), the line of an @overload group (the implementation, not the
    first stub), calls the language itself rules out (`_false_call_filter`; off with
    DEEPMAP_FALSE_CALL_FILTER=0; its Go _test.go rule needs no source), TS/JS declarations lost to an id
    collision and Rust trait methods (recovered as nodes) — plus the default-OFF flags
    DEEPMAP_MODULE_DOCSTRINGS / DEEPMAP_SUMMARY_NORMALIZE / DEEPMAP_GRAPHIFY_CALLS_PLUS /
    DEEPMAP_SUPPLEMENTAL_TS_RUST. Without `root` (or with nothing to correct) the output is what it
    always was, except for the pure corrections: a file whose id graphify reused for a same-named entity
    (a Go type, a TS `const $`) keeps its :Module (`<id>::file`), a Python import the engine left as a
    bare module name is resolved against the graph's Python modules (resolved-import-key contract; off
    with DEEPMAP_RESOLVED_IMPORTS=0), and the Go _test.go call rule. Repeated links are collapsed, so the
    returned tuples (and the sync log's count) are distinct. A method whose owner is a generic/reference
    type is named after the clean type (`_clean_owner`; off with DEEPMAP_CLEAN_OWNER_NAMES=0). A caller whose
    call G5 could only leave as a bare NAME (a member call `x.m()` it cannot pin to one class) carries those
    names as `call_member` — private metadata the push reads to keep them off :Module / :Class nodes (R11b/G1,
    R11c/C1: :Method and module-level :Function targets stay, and the grade is never promoted).

    Engine-version independence (R8/J2): every correction reads either the links every supported
    graphify writes or the source itself — never the repeated nodes/links graphify 0.8.39 writes and the
    0.8.4x+ `--no-cluster` writer collapses (`build.dedupe_nodes` / `dedupe_edges`: one node per id, one
    link per (source, target, relation)). The same tree maps to the same graph on 0.8.39 and 0.8.51
    (tests/extraction_r8_gates.py), except where the engine's own extraction changed.

    `stats`, when given, receives `self_calls_rebound` (distinct calls the correction changed) and its
    breakdown `self_calls_moved` / `self_calls_ambiguous` / `self_calls_dropped`, `resolved_imports`
    (the switch), `imports_resolved` / `imports_by_name` (how many import tuples carry a node key / a
    name), `imports_bare_resolved` (bare module imports bound), `calls_filtered` (distinct engine calls
    dropped as impossible), `overloads_moved`, `supplemental_nodes` (recovered TS/JS + Rust nodes),
    `file_ids_split`, `file_nodes_restored` (file nodes the engine's writer dropped and DeepMap rebuilt
    before the split; also counted in file_ids_split), `calls_plus` (calls the CALLS_PLUS flag
    added), `calls_plus_test_skipped` (additions left out because the caller is test code — R10/P6),
    `precise_unkeyed` (engine-keyed member calls into a module-level def that precise CALLS left to the
    name fan-out — R10/P1) and `precise_receiver_names` (callers that call such a def both bare and through
    a member call: the key stays and the name is added too), and the S1 precise fixes: `precise_type_rekeyed` /
    `precise_type_names` (precise keys to a TS type alias / interface moved to the value declared beside it / left
    to the name), `precise_placeholder_dups` (P1b: duplicate names from graphify 0.8.39 placeholders dropped) and
    `precise_bare_dropped` (S1b review, J3 on keys: (caller, :Method) pairs whose every engine link is a bare call the
    method is out of reach of — _bare_key_guard — so neither a key nor a name was emitted).

    S1: DEEPMAP_PRECISE_CALLS is a set of language families (precise_calls_families); an engine call is keyed only
    when its caller's file is in one (precise_for). Wherever it is, the precise fixes apply unless
    DEEPMAP_PRECISE_FIXES=0 (the TS value-over-type re-key, P1b, and — S1b review — J3 on keys: a key to a :Method
    the caller's bare call cannot reach is dropped, _bare_key_guard, also off under DEEPMAP_FALSE_CALL_FILTER=0);
    unset / 0 never key anything, and the node dicts never depend on the fixes.

    `source` (S1b, the shared-reader seam): a `_Source` of the SAME `root` to read the files through instead of
    a fresh one — extract_with_report passes its caller's (sync's typed-call pass then reuses every read and
    parse, design 2.1). It changes no output: the reads are the same either way. Ignored without `root` (the
    mapping stays pure) or when it is not a _Source of that root (a fresh one is used)."""
    raw = graph.get("nodes", []) or []
    links = graph.get("links", []) or []
    raw, n_restored = _restore_swallowed_file_nodes(raw, links)  # R8/J2 — the 0.8.4x+ writer drops the file
    raw, links, n_split = _split_reused_file_ids(raw, links)     # T8#10 — same objects when no id is reused
    by_id = {n.get("id"): n for n in raw if n.get("id")}

    # ---- edge-derived role sets (first pass) --------------------------------
    method_owner: dict = {}        # method_id -> owning class_id
    class_with_methods: set = set()
    rationale_text: dict = {}      # target_id -> docstring/summary text
    defined_ids: set = set()       # ids DEFINED here: targets of `contains` (module->entity) or
                                   # `method` (class->method). A node that is ONLY a `references`/
                                   # `calls` target (an external type like Path/Any/ArgumentParser)
                                   # is NOT defined here -> not a real entity.
    for l in links:
        rel, src, tgt = l.get("relation"), l.get("source"), l.get("target")
        if rel == "method":
            method_owner[tgt] = src
            class_with_methods.add(src)
            if tgt:
                defined_ids.add(tgt)
        elif rel == "contains":
            if tgt:
                defined_ids.add(tgt)
        elif rel == "rationale_for":
            rationale_text[tgt] = (by_id.get(src) or {}).get("label", "")

    method_targets = set(method_owner)

    def classify(node: dict, nid: str) -> str | None:
        if node.get("file_type") == "rationale":
            return None
        label = node.get("label", "")
        if _looks_like_file(label):
            return "Module"
        # Drop import/destructuring artifacts + anonymous entities BEFORE any typing. graphify emits a
        # node for `const { x } = require(...)` labelled `{ x }` (and anonymous callables labelled e.g.
        # `<lambda>`); typed naively they become bogus `Class` nodes. A bare name with braces/brackets/
        # angle-brackets/whitespace is never a real single entity — cut it (verified across JS/TS imports).
        if not _plausible_name(_bare(label)):
            return None
        # Owns methods -> Class. Checked BEFORE method-membership so a NESTED class (which is both
        # a method-edge target of its parent AND a method-owner itself) types as Class, not Method.
        if nid in class_with_methods:
            return "Class"
        # A member of some class -> Method, but ONLY on edge evidence (a `method` edge). A lone
        # leading '.' in the label is NOT enough: a dotted/decorated top-level callable with no
        # method edge would otherwise become a Method with no owner (and a bare, un-namespaced name).
        if nid in method_targets:
            return "Method"
        if label.endswith("()"):
            return "Function"
        # Bare name, no parens, not a file, owns/joins no method: EITHER an empty class defined here,
        # OR an external type merely REFERENCED (graphify emits a node for referenced types like
        # `Path`, `Any`, `ArgumentParser`, `ast.AST` — validated on real graphs). Keep it only if it
        # is DEFINED here (a `contains`/`method` target); otherwise it is import/type noise -> drop.
        return "Class" if nid in defined_ids else None

    clean_owners = clean_owner_names_enabled()

    def node_name(node: dict, nid: str, kind: str) -> str:
        if kind == "Module":
            return Path(node.get("source_file") or node.get("label", nid)).stem
        base = _bare(node.get("label", "")) or nid
        if kind == "Method":
            owner = by_id.get(method_owner.get(nid), {})
            cls = _bare(owner.get("label", ""))
            if cls:
                return f"{_clean_owner(cls) if clean_owners else cls}.{base}"
        return base

    # ---- build typed nodes + a key->name map (second pass over callees) -----
    nodes: list[dict] = []
    key_to_name: dict = {}
    kinds: dict = {}
    for n in raw:
        nid = n.get("id")
        if not nid or not _is_code_node(n):   # drop doc/data/rationale + non-source-code files
            continue
        kind = classify(n, nid)
        if kind is None:
            continue
        key_to_name[nid] = node_name(n, nid, kind)
        kinds[nid] = kind

    if root is None:
        source = None
    elif not (isinstance(source, _Source) and source.root == Path(root)):
        source = _Source(root)
    reader = _caller_body_reader(source, by_id, kinds) if source is not None else None
    # T5#4 / T4d-F3: calls the language itself rules out, decided once per link (the name-derived summary
    # below must not name a callee the edge list drops).
    bad_links: set = set()
    bad_pairs: set = set()
    alias_box: list = [None]       # the K11 checker, shared by the filter and the precise J3-on-keys guard below
    if false_call_filter_enabled():
        call_check = _false_call_filter(source, by_id, kinds, reader=reader, alias_box=alias_box)
        for i, l in enumerate(links):
            if (l.get("relation") == "calls" and l.get("source") in key_to_name
                    and l.get("target") in key_to_name and call_check(l, l["source"], l["target"])):
                bad_links.add(i)
                bad_pairs.add((l["source"], l["target"]))
        if bad_links:
            # a (caller, callee) pair with ONE genuine call site keeps its edge
            bad_pairs -= {(l.get("source"), l.get("target")) for i, l in enumerate(links)
                          if l.get("relation") == "calls" and i not in bad_links}

    # callee names per node (from the call edges) for name-derived summaries
    callees_by_src: dict = {}
    for i, l in enumerate(links):
        if l.get("relation") == "calls" and i not in bad_links:
            src, tgt = l.get("source"), l.get("target")
            if src in key_to_name:
                nm = key_to_name.get(tgt) or _bare((by_id.get(tgt) or {}).get("label", ""))
                if nm:
                    callees_by_src.setdefault(src, [])
                    if nm not in callees_by_src[src]:
                        callees_by_src[src].append(nm)

    lines_fix = _overload_lines(source, links, by_id, kinds) if source is not None else {}   # T4#4
    mod_docs = module_docstrings_enabled() and source is not None
    normalize = summary_normalize_enabled() and source is not None

    for nid, name in key_to_name.items():
        n = by_id[nid]
        kind = kinds[nid]
        rationale = (rationale_text.get(nid) or "").strip()
        summary = rationale.split("\n")[0][:300]
        documented = bool(rationale)
        sf = str(n.get("source_file", "") or "").replace("\\", "/")
        start_line = lines_fix.get(nid, _line_of(n))
        if (mod_docs or normalize) and sf.lower().endswith(_PY_EXTS):
            summary, documented = _python_summary(source, sf, kind, name, start_line, rationale, summary,
                                                  documented, mod_docs=mod_docs, normalize=normalize)
        if not summary:
            summary = f"{summarize.humanize(name)}: {kind.lower()}"
            callees = callees_by_src.get(nid, [])[:_MAX_CALLEES]
            if callees:
                summary += "; calls " + ", ".join(summarize.humanize(c) for c in callees)
            summary = summary[:300]
        nodes.append(dict(key=nid, label=kind, name=name,
                          # normalize the separator so source_file is OS-portable (audit #2),
                          # matching the ast extractor (build/sync.py)
                          source_file=sf,
                          start_line=start_line,
                          # documented iff graphify attached a rationale (docstring) to this node —
                          # the signal the opt-in enrichment pass uses to enrich ONLY undocumented code.
                          documented=documented,
                          body_summary=summary))

    # ---- CALLS + IMPORTS edges as (caller_key, callee_name) -----------------
    # P0-1 (opt-in): under DEEPMAP_PRECISE_CALLS, a CALL whose target graphify ALREADY resolved to a
    # kept node keeps that node's KEY (not just its name), so push_to_neo4j makes ONE firm EXTRACTED
    # edge instead of fanning the name out to every same-named node. Flag OFF -> name tuple, byte-
    # identical to today. Imports: an import the engine resolved to a KEPT node carries that node's key
    # (default ON — the resolved-import-key contract above); else the bare target name, as before.
    # S1: the flag selects language families; a link is keyed only when its CALLER's file is in one (precise_on).
    fams = precise_calls_families()
    precise = bool(fams)
    fixes = precise and precise_fixes_enabled()
    precise_files: dict = {}

    def precise_on(sf) -> bool:
        if sf not in precise_files:
            precise_files[sf] = precise_for(sf, fams)
        return precise_files[sf]

    keyed_imports = resolved_imports_enabled()
    n_imp_keys = 0
    n_bare = 0
    rebind = (_self_call_rebinder(root, links, by_id, method_owner, kinds, source=source)
              if root is not None and self_call_rebind_enabled() else None)
    changed: dict = {}             # (src, graphify's target) -> how the correction changed it
    # R10/P1: under precise CALLS, a key the engine guessed for a MEMBER call into a module-level def
    # goes back to the bare name (see _receiver_unkeyer); only built when there is source to read
    p1 = _receiver_unkeyer(source, by_id, kinds, reader, lines_fix) if (precise and source is not None) else None
    # S1b review (DEEPMAP_PRECISE_FIXES; J3's own opt-out DEEPMAP_FALSE_CALL_FILTER=0 turns it off too): a precise
    # key to a :Method the caller's bare call cannot reach is dropped (_bare_key_guard)
    bare_guard = (_bare_key_guard(source, by_id, kinds, key_to_name, reader, alias_box)
                  if (fixes and source is not None and false_call_filter_enabled()) else None)
    bare_out: set = set()          # (src, tgt) pairs a link of which _bare_key_guard ruled out
    unkeyed: set = set()           # (src, tgt) pairs P1 left to the name fan-out
    fanned: set = set()            # (src, tgt) pairs P1 kept AND gave the name (called both ways)
    engine_called: set = set()     # (src, node key) of every engine call kept (CALLS_PLUS adds only others)
    # R11b/G1 (F2): (caller, bare name) pairs G5 turned into a NAME fall-back — a member call `x.m()` whose
    # class this file cannot pin — and the (caller, name) tuples produced any other way. A pair only the
    # fall-back produced is recorded on the caller as `call_member`, and the push keeps it off :Module (a file is
    # not callable) and :Class nodes; a module-level :Function stays — `LR.run(root)` through an imported module
    # calls it (R11c/C1).
    member_pairs: set = set()
    plain_pairs: set = set()
    # S1 fixes (DEEPMAP_PRECISE_FIXES): the (caller, name) tuples of a precise caller that only a link to a node
    # map_graph does not keep produced (P1b's candidates) vs those any other link produced (never dropped), and the
    # precise keys to a TS/JS :Class with the lines of their links (the value-over-type re-key's candidates).
    ph_names: set = set()
    other_names: set = set()
    type_keyed: dict = {}
    calls: list[tuple] = []
    imports: list[tuple] = []
    seen: set = set()
    mod_idx = None

    def file_of(k) -> str:
        return str((by_id.get(k) or {}).get("source_file") or "").replace("\\", "/")

    for i, l in enumerate(links):
        rel, src, tgt = l.get("relation"), l.get("source"), l.get("target")
        if src not in key_to_name:
            continue
        tgt_name = key_to_name.get(tgt) or _bare((by_id.get(tgt) or {}).get("label", ""))
        if not tgt_name:
            # T3#10: a Python import the engine could not turn into a node id (`import helper` — a bare
            # sys.path-style module) is resolved against the graph's own Python modules, like the ast
            # path does, instead of being dropped. A stdlib/third-party name matches nothing -> dropped.
            if (keyed_imports and rel in ("imports", "imports_from") and tgt and tgt not in by_id
                    and file_of(src).lower().endswith(_PY_EXTS)):
                if mod_idx is None:
                    mod_idx = _py_module_index([(k, file_of(k)) for k in key_to_name if kinds.get(k) == "Module"])
                got = _resolve_py_module(str(tgt), mod_idx, file_of(src), src, file_of)
                item = None
                if got and got[0] == "key":
                    item = (src, got[1])
                elif got:
                    stems = {key_to_name[k] for k in got[1]}
                    item = (src, stems.pop()) if len(stems) == 1 else None
                if item is not None and ("i", item) not in seen:
                    seen.add(("i", item))
                    imports.append(item)
                    n_bare += 1
                    n_imp_keys += 1 if item[1] in key_to_name else 0
            continue
        if rel == "calls":
            if i in bad_links:
                continue
            fix = rebind(l, src, tgt) if rebind is not None else None
            g5_name = False
            if fix is not None:
                how, val = fix
                changed.setdefault((src, tgt), how)
                if how == "drop":
                    continue
                tgt, tgt_name = (val, key_to_name[val]) if how == "key" else (None, val)
                g5_name = how == "name"
            src_precise = precise and precise_on(file_of(src))
            if src_precise and tgt in key_to_name and bare_guard is not None and bare_guard(l, src, tgt):
                bare_out.add((src, tgt))            # a bare call never reaches this :Method — no key, no name
                continue
            if src_precise and tgt in key_to_name:      # graphify's resolved target IS a kept node key
                how_p1 = p1(src, tgt) if p1 is not None else None
                # R11/R1: the key is MARKED (CallKey); P1's fall-back is a plain name and always fans out
                item = (src, tgt_name) if how_p1 == "name" else (src, CallKey(tgt))
                if how_p1 == "name":
                    unkeyed.add((src, tgt))         # R10/P1: only member calls — they never name this def
                elif how_p1 == "both":
                    plain_pairs.add((src, tgt_name))
                    other_names.add((src, tgt_name))
                    if _seen_call((src, tgt_name)) not in seen:
                        fanned.add((src, tgt))      # the member call's targets, BEFORE the key (whose grade
                        seen.add(_seen_call((src, tgt_name)))    # then wins for the def itself at the push)
                        calls.append((src, tgt_name))
                if fixes and is_call_key(item[1]) and kinds.get(tgt) == "Class" \
                        and file_of(tgt).lower().endswith(_TSJS_EXTS):
                    type_keyed.setdefault((src, tgt), set()).add(_link_line(l))
            else:
                item = (src, tgt_name)
            if not is_call_key(item[1]):
                (member_pairs if g5_name else plain_pairs).add(item)
                if fixes and src_precise:
                    (ph_names if (tgt is not None and tgt not in key_to_name) else other_names).add(item)
            if tgt in key_to_name:
                engine_called.add((src, tgt))
            if _seen_call(item) not in seen:
                seen.add(_seen_call(item))
                calls.append(item)
        elif rel in ("imports", "imports_from"):
            by_key = keyed_imports and tgt in key_to_name     # the engine resolved it to a kept node
            item = (src, tgt) if by_key else (src, tgt_name)
            if ("i", item) not in seen:
                seen.add(("i", item))
                imports.append(item)
                n_imp_keys += 1 if by_key else 0
    if unkeyed or fanned:
        calls = _names_before_keys(calls, {(s, key_to_name[t]) for s, t in unkeyed | fanned})

    # ---- S1/P1b (DEEPMAP_PRECISE_FIXES): graphify 0.8.39 also links `Order()` to a placeholder node of an
    # annotation's name (`o: Order`) that map_graph does not keep, so a precise caller got the key AND that bare
    # name (the receiver probe: 11 tuples on 0.8.39, 9 on 0.8.51). The name goes when it only came from such links,
    # the caller holds a precise key whose last name segment it is, and the caller's body has no more `Order(`
    # sites than such keys (_placeholder_dups; each caller's span scanned once, _p1b_counter). Anything unread or
    # unclear keeps the name: an over-long span, a call form the scan cannot count (`Order?.(`, `Order[int](`...).
    n_dups = 0
    if fixes and ph_names and source is not None:
        dups = _placeholder_dups(calls, ph_names - other_names, key_to_name,
                                 _p1b_counter(source, reader, kinds, file_of))
        if dups:
            calls = [c for c in calls if is_call_key(c[1]) or (c[0], c[1]) not in dups]
            n_dups = len(dups)

    # ---- T5#2 / T5#3: declarations the engine lost (nodes default ON; their calls behind a flag) ----
    n_supp = 0
    if source is not None:
        at_line = {(nd["name"], nd["source_file"], nd["start_line"]) for nd in nodes}
        supp = _recover_ts_collisions(source, links, by_id, key_to_name, kinds, at_line)
        rs_files = {nd["source_file"] for nd in nodes if nd["source_file"].lower().endswith(".rs")}
        if rs_files:
            named = {(nd["name"], nd["source_file"]) for nd in nodes}
            supp += _rust_trait_methods(source, rs_files, key_to_name, kinds, by_id, named)
        supp_calls = supplemental_ts_rust_calls_enabled()
        by_name_file: dict = {}
        if supp_calls:
            for nd in nodes + [s[0] for s in supp]:
                by_name_file.setdefault((nd["source_file"], nd["name"]), []).append(nd["key"])
        for nd, _base, callees in supp:
            nodes.append(nd)
            n_supp += 1
            if not (supp_calls and callees):
                continue
            for c in callees:
                cname = c[1] if isinstance(c, tuple) else c
                hits = by_name_file.get((nd["source_file"], cname), [])
                item = (nd["key"], CallKey(hits[0])) if (precise_on(nd["source_file"]) and len(hits) == 1) \
                    else (nd["key"], cname)
                if not is_call_key(item[1]):
                    plain_pairs.add(item)
                if item[1] != nd["key"] and _seen_call(item) not in seen:
                    seen.add(_seen_call(item))
                    calls.append(item)

    # ---- S1: the TS value-over-type re-key (DEEPMAP_PRECISE_FIXES) --------------------------------------------
    # TypeScript declares a type and a value of one name side by side (zod: `export type OK<T> = ...` + `export
    # const OK = ...`); graphify gives both ONE id, keeps the TYPE and binds every `OK(...)` to it, so precise CALLS
    # keyed 14 zod calls to a type alias while the const (recovered above as `<id>@OK:L<n>`) had no caller. Such a
    # key now binds the value declaration of the type's own file that collided with it and that the caller calls by
    # name, or else becomes the name — the flag-off tuple (_ts_type_rekey). Runs after the recovery (it needs the
    # recovered values) and before CALLS_PLUS (whose additions are never TS).
    n_rekeyed = n_type_names = 0
    if fixes and type_keyed and source is not None:
        moves = _ts_type_rekey(type_keyed, nodes, source, reader, file_of)
        if moves:
            out: list = []
            kept_calls: set = set()
            renamed: set = set()
            for c in calls:
                new = moves.get((c[0], str(c[1]))) if is_call_key(c[1]) else None
                if new is not None:
                    seen.discard(_seen_call(c))
                    c = (c[0], new)
                    if is_call_key(new):
                        n_rekeyed += 1
                    else:
                        n_type_names += 1
                        renamed.add(c)
                k = _seen_call(c)
                if k not in kept_calls:          # a re-keyed tuple the caller already had is not repeated
                    kept_calls.add(k)
                    seen.add(k)
                    out.append(c)
            calls = _names_before_keys(out, renamed) if renamed else out

    # ---- DEEPMAP_GRAPHIFY_CALLS_PLUS (default OFF) -----------------------------------------------
    # R10/P5: each addition resolved to exactly ONE node, so it carries that node's KEY in BOTH modes (it
    # used to become the bare name without DEEPMAP_PRECISE_CALLS, and the push fanned it out to every
    # same-named node — flask: 44 calls -> 84 edges, 57 AMBIGUOUS); a call the engine already made to that
    # node is not added again. R10/P6: only NON-test callers (sync._is_test_path) by default — test and
    # fixture calls inflated the graph-weighted arm's in-degree prior (click: 154 of 181 additions came
    # from tests/); DEEPMAP_GRAPHIFY_CALLS_PLUS_TESTS=1 adds them too.
    n_plus = 0
    plus_test_skipped: set = set()
    if source is not None and graphify_calls_plus_enabled():
        # S1b: module names resolve by import root (py_import_resolver), not the suffix index the import
        # mapping above keeps using.
        file_of_node = {nd["key"]: str(nd.get("source_file") or "") for nd in nodes}
        is_test = None if calls_plus_test_callers_enabled() else _test_path_predicate()
        for s, t in _calls_plus(source, nodes):
            if (s, t) in bad_pairs or (s, t) in engine_called or t not in file_of_node:
                continue
            if is_test is not None and is_test(file_of_node.get(s, "")):
                plus_test_skipped.add((s, t))
                continue
            item = (s, CallKey(t))                  # R11/R1: an addition is a marked key in both modes
            if _seen_call(item) not in seen:
                seen.add(_seen_call(item))
                calls.append(item)
                n_plus += 1

    # R11b/G1 (F2): a (caller, name) pair ONLY the G5 fall-back produced is a member call — recorded on the caller
    # (private push metadata, never pushed; sync._resolve_calls reads it — R11c/C1). A name the caller also reaches
    # another way (a bare call to a module-level def, P1's name) keeps its whole fan-out. Nothing is attached when G5 is
    # off or made no name fall-back, so those node dicts are byte-identical to before.
    member_only = member_pairs - plain_pairs
    if member_only:
        per_caller: dict = {}
        for s, nm in member_only:
            per_caller.setdefault(s, set()).add(str(nm))
        for nd in nodes:
            if nd["key"] in per_caller:
                nd["call_member"] = sorted(per_caller[nd["key"]])

    if isinstance(stats, dict):
        hows = list(changed.values())
        stats.update(self_call_rebind=rebind is not None, self_calls_rebound=len(hows),
                     self_calls_moved=hows.count("key"), self_calls_ambiguous=hows.count("name"),
                     self_calls_dropped=hows.count("drop"), resolved_imports=keyed_imports,
                     imports_resolved=n_imp_keys, imports_by_name=len(imports) - n_imp_keys,
                     imports_bare_resolved=n_bare, calls_filtered=len(bad_pairs),
                     overloads_moved=len(lines_fix), supplemental_nodes=n_supp, file_ids_split=n_split,
                     file_nodes_restored=n_restored, calls_plus=n_plus,
                     precise_unkeyed=len(unkeyed), precise_receiver_names=len(fanned),
                     calls_plus_test_skipped=len(plus_test_skipped), precise_type_rekeyed=n_rekeyed,
                     precise_type_names=n_type_names, precise_placeholder_dups=n_dups,
                     precise_bare_dropped=len(bare_out - engine_called))
    return nodes, calls, imports


def _seen_call(item) -> tuple:
    """map_graph's de-duplication key for a CALLS tuple: a marked key and a plain name that happen to be the
    same string are DIFFERENT calls (R11/R1) — one binds that node, the other fans out by name. Without a
    marked key (both flags off) this de-duplicates exactly as `("c", item)` did."""
    return ("c", item, is_call_key(item[1]))


def _names_before_keys(calls: list, names: set) -> list:
    """`calls` with each (caller, name) tuple of `names` moved in front of that caller's first tuple (R10/P1).
    The push grades a repeated (caller, node) pair by its LAST tuple, so a name fan-out that followed the
    caller's own key would demote the def the key names from EXTRACTED to the fan-out's grade. Every other
    tuple keeps its order. A marked key (CallKey) is never one of the names, even when it is the same string."""
    def is_name(c) -> bool:
        return c in names and not is_call_key(c[1])

    moved: dict = {}
    for c in calls:
        if is_name(c):
            moved.setdefault(c[0], []).append(c)
    out: list = []
    done: set = set()
    for c in calls:
        if c[0] in moved and c[0] not in done:
            out.extend(moved[c[0]])             # at the caller's first tuple, whatever it is
            done.add(c[0])
        if not is_name(c):
            out.append(c)
    return out


# ---- S1: the precise fixes' readers ------------------------------------------------------------------------
_DECL_BEFORE = re.compile(r"(?:^|[^\w$])(?:def|fn|func|function|class|async\s+def)\s*$")
_CALL_GENERICS = r"(?:<(?:[^<>()]|<[^<>()]*>)*>\s*)?"     # `OK<T>(` / `OK<Array<T>>(` (one nesting level)
_IDENT = r"(?:[^\W\d]|\$)[\w$]*"                          # one identifier (Unicode letters, `$`)
_IDENT_FULL = re.compile(_IDENT)
# P1b's site: an identifier followed by `(` — `X(`, `X<T>(`, `ns.X(`, `obj.X(`, `new X(` (a declaration is not one).
# One scan finds every name's sites: an identifier inside a generic list is never followed by `(`, so the sites
# are exactly those a per-name `(?<![\w$])X\s*<generics>?\(` finds.
_SITE_RE = re.compile(r"(?<![\w$])(" + _IDENT + r")\s*" + _CALL_GENERICS + r"\(")
# P1b's DOUBT: a call of the identifier the site scan does not count; its name is then never dropped (review of S1,
# finding 4 — an undercount is what drops a name). Any language / JS-TS only.
_DOUBT_ANY = (re.compile(r"(?<![\w$])(" + _IDENT + r")\s*\?\.\s*\("),                # `X?.(`
              re.compile(r"(?<![\w$])(" + _IDENT + r")\s*\[[^\]\n]*\]\s*\("),          # `X[int](`
              re.compile(r"(?<![\w$])(" + _IDENT + r")\s*\.\s*(?:call|apply)\s*\("))   # `X.call(` / `X.apply(`
_DOUBT_JS = (re.compile(r"(?<![\w$])(" + _IDENT + r")\s*`"),                           # a tagged template X`...`
             re.compile(r"(?<![\w$])new\s+(?:" + _IDENT + r"\s*\.\s*)*(" + _IDENT + r")(?![\w$])(?!\s*[(<.])"))  # `new X`
_LINE_END_NAME = re.compile(r"(?<![\w$])(" + _IDENT + r")\s*\\?\s*$")                  # `X` / `X \` ending a line
_P1B_MODULE_MAX_LINES = 5000   # a :Module caller is read over its whole file; a longer one is not read (the name stays)


def _last_name_segment(name: str) -> str:
    return re.split(r"\.|::", str(name or ""))[-1]


def _span_call_sites(lines, span, py: bool) -> tuple:
    """({name: call sites}, {names with a call the scan cannot count}) over the lines `span` of a file's `lines`.
    A site is `X(` / `X<T>(` / `ns.X(` / `obj.X(` / `new X(`, a declaration (`def X(`, `function X(`, `class X(`)
    excluded; a trailing comment is cut and a comment-only line skipped; a string literal is read like code (it can
    only add sites). A DOUBT is `X?.(`, `X[...](`, `X.call(` / `X.apply(`, an `X` ending a line (after an optional
    `\\`) whose next non-blank line opens with `(`, and in JS / TS a tagged template, `new X` without parentheses and
    any call after a cut `//` (a regex literal can hold one)."""
    counts: dict = {}
    doubt: set = set()
    n = len(lines)
    for ln in span:
        if not 1 <= ln <= n:
            continue
        raw = lines[ln - 1]
        if not raw:
            continue
        line = _strip_trailing_comment(raw, py)
        st = line.strip()
        if not st or _is_comment_only(st, py):
            continue
        for m in _SITE_RE.finditer(line):
            if not _DECL_BEFORE.search(line[:m.start()]):
                counts[m.group(1)] = counts.get(m.group(1), 0) + 1
        for rx in _DOUBT_ANY if py else _DOUBT_ANY + _DOUBT_JS:
            doubt.update(m.group(1) for m in rx.finditer(line))
        if not py and len(line) < len(raw):
            doubt.update(m.group(1) for m in _SITE_RE.finditer(raw[len(line):]))
        m = _LINE_END_NAME.search(line)
        if m and next((t.strip() for t in lines[ln:ln + 50] if t.strip()), "").startswith("("):
            doubt.add(m.group(1))
    return counts, doubt


def _p1b_caller_sites(src, reader, kinds, s, sf: str):
    """_span_call_sites over caller `s` of file `sf`, or None when its span cannot be read whole: no source, an
    unreadable file, a body longer than the reader's span, a :Module caller (read over its whole file — its calls are
    scattered between the defs) in a file over _P1B_MODULE_MAX_LINES lines."""
    lines = src.lines(sf) if (src is not None and reader is not None) else None
    if not lines:
        return None
    if kinds.get(s) == "Module":
        if len(lines) > _P1B_MODULE_MAX_LINES:
            return None
        span = range(1, len(lines) + 1)
    else:
        span = reader[0](s, sf)
        if len(span) > _RESCUE_SPAN:
            return None
    return _span_call_sites(lines, span, sf.lower().endswith(_PY_EXTS))


def _p1b_counter(src, reader, kinds, file_of):
    """count(caller, name) for P1b (_placeholder_dups): how many `name(` sites (`new name(` included) the caller's
    span holds, or None when that cannot be known — the span cannot be read whole (_p1b_caller_sites), `name` is not
    one identifier, or the span calls it in a form the scan cannot count (_span_call_sites' doubts). Each caller's
    span is scanned ONCE, whatever the number of names asked about."""
    memo: dict = {}

    def count(s, name):
        if s not in memo:
            memo[s] = _p1b_caller_sites(src, reader, kinds, s, file_of(s))
        got = memo[s]
        name = str(name)
        if got is None or not _IDENT_FULL.fullmatch(name) or name in got[1]:
            return None
        return got[0].get(name, 0)

    return count


def _placeholder_dups(calls: list, candidates: set, key_to_name: dict, count_sites) -> set:
    """P1b: the (caller, name) tuples of `candidates` — names only links to a node map_graph does not keep produced
    (graphify 0.8.39's placeholder of an annotation's name) — that duplicate the caller's own precise keys: the
    caller holds a marked key whose last name segment IS the name, and `count_sites(caller, name)` finds no more
    call sites of it than such keys. Two `Order(` sites beside one key, no key of that name, or an unknown count
    (None): the name stays."""
    keyed: dict = {}
    for c in calls:
        if is_call_key(c[1]) and c[1] in key_to_name:
            keyed.setdefault(c[0], {}).setdefault(_last_name_segment(key_to_name[c[1]]), set()).add(str(c[1]))
    drop: set = set()
    for s, nm in sorted(candidates):
        n_keys = len(keyed.get(s, {}).get(nm, ()))
        if n_keys:
            sites = count_sites(s, nm)
            if sites is not None and sites <= n_keys:
                drop.add((s, nm))
    return drop


def _ts_value_decl(line, name: str) -> bool:
    """Does source `line` declare the VALUE `name` (a const / let / var, a function or a class — never a type,
    an interface or an enum)?"""
    m = _TS_DECL.match(line or "")
    return bool(m) and m.group("name") == name and _ts_decl_kind(m) in ("fn", "var", "class")


# The re-key's call test reads the caller with tree-sitter (graphify's own grammar dependency), by SCOPE (review of
# S1, finding 1): a text reading took `r.OK(1)` and a shadowing parameter's `OK(1)` for calls of the module-level
# const. A name node BINDS when it is a parameter, a declarator or destructuring target, a nested function / class
# name, a catch parameter or a for-in/of target (_TS_BINDING_*); a scope is a function-like node or a block.
_TS_FUNC_NODES = frozenset({"function_declaration", "generator_function_declaration", "function_expression",
                            "function", "generator_function", "arrow_function", "method_definition"})
_TS_SCOPE_NODES = _TS_FUNC_NODES | frozenset({"statement_block", "for_statement", "for_in_statement", "catch_clause",
                                              "class_static_block"})
_TS_BINDING_FIELDS = {"required_parameter": "pattern", "optional_parameter": "pattern", "arrow_function": "parameter",
                      "variable_declarator": "name", "function_declaration": "name",
                      "generator_function_declaration": "name", "function_expression": "name", "function": "name",
                      "generator_function": "name", "class_declaration": "name",
                      "abstract_class_declaration": "name", "class": "name", "catch_clause": "parameter",
                      "for_in_statement": "left", "pair_pattern": "value", "assignment_pattern": "left",
                      "object_assignment_pattern": "left"}
_TS_BINDING_PARENTS = frozenset({"formal_parameters", "array_pattern", "rest_pattern"})   # each name child binds
_TS_NAME_NODES = frozenset({"identifier", "type_identifier", "shorthand_property_identifier_pattern"})


def _ts_text(node, data: bytes) -> str:
    return data[node.start_byte:node.end_byte].decode("utf-8", "replace")


def _ts_is_binding(node) -> bool:
    """Does tree-sitter name node `node` BIND its name (see _TS_BINDING_FIELDS) rather than use it?"""
    if node.type == "shorthand_property_identifier_pattern":
        return True
    p = node.parent
    if p is None:
        return False
    if p.type in _TS_BINDING_PARENTS:
        return True
    field = _TS_BINDING_FIELDS.get(p.type)
    c = p.child_by_field_name(field) if field else None
    return c is not None and c.start_byte == node.start_byte and c.end_byte == node.end_byte and c.type == node.type


def _ts_scope_binds(scope, name: str, data: bytes, memo: dict) -> bool:
    """Does tree-sitter scope node `scope` bind `name` — its own parameters / name, or a binding anywhere in it outside
    nested functions (whose own DECLARED name binds here)? Block scoping is not modelled: a `let` in a sibling block
    counts too (the call then gives the name — the flag-off tuple)."""
    key = (scope.start_byte, scope.end_byte, scope.type, name)
    if key not in memo:
        found = False
        stack = list(scope.children)
        while stack and not found:
            n = stack.pop()
            if n.type in _TS_FUNC_NODES:
                if n.type in ("function_declaration", "generator_function_declaration"):
                    nm = n.child_by_field_name("name")
                    found = nm is not None and _ts_text(nm, data) == name
                continue
            if n.type in _TS_NAME_NODES and _ts_text(n, data) == name and _ts_is_binding(n):
                found = True
            stack.extend(n.children)
        memo[key] = found
    return memo[key]


def _ts_bound_locally(node, name: str, data: bytes, memo: dict) -> bool:
    """Is `name`, used at tree-sitter `node`, bound by a scope between it and the program — so not the module-level
    declaration or import of that name?"""
    anc = node.parent
    while anc is not None and anc.type != "program":
        if anc.type in _TS_SCOPE_NODES and _ts_scope_binds(anc, name, data, memo):
            return True
        anc = anc.parent
    return False


def _ts_value_called(src, caller_file: str, lines, name: str, same_file: bool, memo: dict) -> bool:
    """Does the caller, on source `lines` of `caller_file`, call the module-level VALUE `name` of the type's file? Read
    with tree-sitter, by scope. A call site counts when it is
      a bare `name(` / `name<T>(` / `name?.(` that no enclosing scope binds — in another file only when the file's
          import / require statements name it;
      `ns.name(` in ANOTHER file, `ns` a name the file's import / require statements bind (`import * as ns`, `const
          ns = require(...)`) that no enclosing scope binds.
    Never: `new name(` / `new ns.name(`, a member call on anything else (`r.name(`, `this.name(`, an unbound
    `ns.name(`, any member call in the type's own file), a longer chain (`ns.inner.name(`), a call in a comment or a
    string. No tree (no grammar, an unreadable file, a non-TS/JS caller): False."""
    got = src.ts_tree(caller_file) if lines else None
    if not got:
        return False
    tree, data = got
    rows = {int(ln) - 1 for ln in lines}
    lo, hi = min(rows), max(rows)
    fmemo = memo.setdefault(("scope", caller_file), {})
    words = None
    if not same_file:
        if ("imports", caller_file) not in memo:
            memo[("imports", caller_file)] = _js_import_words("\n".join(src.lines(caller_file) or ()))
        words = memo[("imports", caller_file)]
    stack = [tree.root_node]
    while stack:
        n = stack.pop()
        if n.end_point[0] < lo or n.start_point[0] > hi:
            continue
        stack.extend(n.children)
        if n.type != "call_expression":
            continue
        fn = n.child_by_field_name("function")
        if fn is None:
            continue
        if fn.type == "identifier":
            if (_ts_text(fn, data) == name and (n.start_point[0] in rows or fn.start_point[0] in rows)
                    and (same_file or name in words) and not _ts_bound_locally(fn, name, data, fmemo)):
                return True
        elif fn.type == "member_expression" and not same_file:
            prop, obj = fn.child_by_field_name("property"), fn.child_by_field_name("object")
            if (prop is not None and obj is not None and obj.type == "identifier" and _ts_text(prop, data) == name
                    and (n.start_point[0] in rows or prop.start_point[0] in rows)):
                head = _ts_text(obj, data)
                if head in words and not _ts_bound_locally(obj, head, data, fmemo):
                    return True
    return False


def _ts_type_rekey(type_keyed: dict, nodes: list, src, reader, file_of) -> dict:
    """The TS value-over-type re-key: {(caller, key): new callee} for the precise keys of `type_keyed`
    ({(caller, key of a TS/JS :Class): lines of the engine's links}) whose target's own declaration line is a
    `type` / `interface` of its name. The new callee is the marked key of the ONE value declaration (_ts_value_decl)
    of the type's own file that collided with it (the same engine id — `OK` with `OK`, zod's `$Parse` with
    `_parse`) and that the caller calls by name on a link line or in its body (_ts_value_called: a bare call no
    enclosing scope shadows, or `ns.X(` through an imported namespace; never `new X(` or a member call on anything
    else); else the type's name — the flag-off tuple. A class, an enum, a target whose line declares another name or
    whose file cannot be read: no entry (the key stays). A value in another file is never a candidate."""
    by_key = {nd["key"]: nd for nd in nodes}
    by_file: dict = {}
    for nd in nodes:
        if nd.get("label") in ("Function", "Class"):
            by_file.setdefault(nd.get("source_file"), []).append(nd)
    out: dict = {}
    memo: dict = {}
    for (s, t), link_lines in type_keyed.items():
        tn = by_key.get(t)
        if tn is None or tn.get("label") != "Class":
            continue
        sf = str(tn.get("source_file") or "")
        m = _TS_DECL.match(src.line(sf, tn.get("start_line")) or "")
        if not m or m.group("cls") not in ("type", "interface") or m.group("name") != tn.get("name"):
            continue
        eid = _engine_id(tn["name"])
        values = [nd for nd in by_file.get(sf, ()) if nd["key"] != t and _engine_id(nd["name"]) == eid
                  and _ts_value_decl(src.line(sf, nd.get("start_line")), nd["name"])]
        caller_file = file_of(s)
        lines = set(ln for ln in link_lines if ln)
        if values and reader is not None:
            lines.update(reader[0](s, caller_file))
        called = [nd for nd in values if _ts_value_called(src, caller_file, lines, nd["name"], caller_file == sf, memo)]
        out[(s, str(t))] = CallKey(called[0]["key"]) if len(called) == 1 else str(tn["name"])
    return out


def _python_summary(source, sf, kind, name, line, rationale, summary, documented, *, mod_docs, normalize):
    """(summary, documented) for a Python node under the default-OFF summary flags. A Module node under
    DEEPMAP_MODULE_DOCSTRINGS takes the module docstring (its first line — the ast path's rule — or its
    first sentence under DEEPMAP_SUMMARY_NORMALIZE), or '' + undocumented when the module has none (the
    name-derived summary follows), or graphify's own result when the file cannot be parsed. Under
    DEEPMAP_SUMMARY_NORMALIZE a docstring summary becomes the docstring's first sentence, read with ast (a
    comment rationale — a '# TODO' — is left as it is)."""
    import ast
    if kind == "Module":
        if mod_docs or (normalize and rationale and not rationale.startswith("#")):
            tree = source.tree(sf)
            if tree is None:
                return summary, documented
            doc = ast.get_docstring(tree) or ""
            if doc.strip():
                return (_first_sentence(doc) if normalize else doc.strip().split("\n")[0][:300]), True
            if mod_docs:
                return "", False
        return summary, documented
    if normalize and rationale and not rationale.startswith("#"):
        doc = _def_docstring(source, sf, line, name.rsplit(".", 1)[-1])
        if doc:
            return _first_sentence(doc), documented
        text = rationale
        if len(text) >= 80 and " " in text:           # graphify's 80-char label cut: drop the split word
            text = text.rsplit(" ", 1)[0]
        return _first_sentence(text) or summary, documented
    return summary, documented


def case_insensitive_ext_enabled() -> bool:
    """Default ON. `DEEPMAP_CASE_INSENSITIVE_EXT=0` restores the raw engine invocation."""
    return str(os.environ.get("DEEPMAP_CASE_INSENSITIVE_EXT", "1")).strip().lower() not in {
        "0", "false", "no", "off"}


def odd_case_source_suffixes(root, *, limit: int = 200000) -> list:
    """Suffixes present under `root` that DeepMap would map if only their case matched.

    The 2026-08 Delphi field report's residual half: graphify keys its extractor table by the EXACT
    suffix, so a legacy Windows tree of `UNIT1.PAS` / `MAIN.DPR` extracts NOTHING while the identical
    lowercase tree extracts fine. This finds those suffixes (`.PAS`, `.Dpr`, ...) so `extract()` can
    alias them in the engine instead of asking a human to rename their repo.

    Deliberately narrow: only suffixes whose LOWERCASE form is already a DeepMap source extension
    qualify, so this can never widen the language set — it only recovers files DeepMap was always meant
    to map. Returns [] for a normal all-lowercase repo, which is what keeps the engine invocation
    byte-identical for everyone else.

    Walks with os.walk and PRUNES ignored directories in place, for two reasons beyond speed: a
    vendored tree is never descended at all, and `limit` is then a budget over real candidate FILES
    rather than being consumed by node_modules paths. Ignore matching is on directory names DISCOVERED
    UNDER the root — never on the absolute prefix above it, or a repo living at `C:/build/App` or
    `/srv/target/app` would match `build`/`target` and silently disable the feature (the same absolute-
    vs-relative trap that caused the v0.18.0 recognizer CI failure). macOS metadata (`._UNIT1.PAS`, a
    `.AppleDouble/` or `__MACOSX/` directory: _is_macos_metadata) is never source, so its suffixes are not
    listed. Never raises.
    """
    found: set = set()
    try:
        seen = 0
        for dirpath, dirnames, filenames in os.walk(str(Path(root))):
            dirnames[:] = [d for d in dirnames if d not in _SCAN_IGNORE_DIRS and not _is_macos_metadata(d, is_dir=True)]
            for name in filenames:
                if seen >= limit:
                    return sorted(found)
                seen += 1
                dot = name.rfind(".")
                if dot <= 0 or name.startswith("._"):   # no extension, a dotfile with no stem, an AppleDouble twin
                    continue
                suffix = name[dot:]
                if suffix == suffix.lower():       # the overwhelmingly common case, cheapest first
                    continue
                if suffix.lower() in SOURCE_CODE_EXTS:
                    found.add(suffix)
    except Exception:  # noqa: BLE001 - a scan problem must never break extraction
        return []
    return sorted(found)


def graphify_out_dir(root) -> Path:
    """G1: the SHORT per-root directory graphify writes to (passed as an absolute GRAPHIFY_OUT).

    `<DEEPMAP_HOME>/gout/<key>`, key = sha1(resolved root, forward slashes)[:12] — the same value as
    port_registry.project_key for a repo-root sync. Its length no longer depends on how deep the
    project lives, so graphify's cache temp files (~109 chars past this dir) stay under Windows'
    260-char MAX_PATH. One dir per sync ROOT, not per project: graphify's stat index maps absolute
    paths to hashes computed relative to the root, so a root sync and a subdir sync sharing one dir
    would read each other's cache entries with wrong source paths."""
    ident = str(Path(root).resolve()).replace("\\", "/")
    # absolute: graphify resolves a RELATIVE GRAPHIFY_OUT against the project root, not our cwd
    return _gout_home() / hashlib.sha1(ident.encode("utf-8", "replace")).hexdigest()[:12]


def _gout_home() -> Path:
    home = os.environ.get("DEEPMAP_HOME") or str(Path.home() / ".deepmap")
    return Path(os.path.abspath(os.path.expanduser(home))) / "gout"


OWNER_MARKER = "deepmap_owner.json"


def _write_owner_marker(out: Path, root: Path) -> None:
    """Record which sync root (and which project) an output dir belongs to, so a purge can find every dir
    a project left behind — a subdirectory sync has its own dir, keyed on its own path. Best-effort."""
    try:
        key = None
        try:
            import port_registry                      # stdlib-only sibling; the registry's project key
            key = port_registry.project_key(root)
        except Exception:  # noqa: BLE001
            key = None
        data = {"root": str(root), "project_key": key}
        path = out / OWNER_MARKER
        text = json.dumps(data, sort_keys=True)
        if not path.is_file() or path.read_text(encoding="utf-8", errors="replace") != text:
            path.write_text(text, encoding="utf-8")
    except Exception:  # noqa: BLE001 - a marker must never break extraction
        pass


def gout_dirs_for_project(project_key: str) -> list:
    """Every graphify output dir under <DEEPMAP_HOME>/gout/ that belongs to the project `project_key`
    (port_registry.project_key): the repo-root sync's dir plus any subdirectory sync's. For
    `provision instance-rm --purge`, which should leave nothing of the project behind. Never raises."""
    found: list = []
    try:
        base = _gout_home()
        if not project_key or not base.is_dir():
            return found
        for d in sorted(base.iterdir()):
            try:
                if not d.is_dir():
                    continue
                marker = d / OWNER_MARKER
                data = json.loads(marker.read_text(encoding="utf-8")) if marker.is_file() else {}
                if data.get("project_key") == project_key or (not data and d.name == project_key):
                    found.append(d)
            except Exception:  # noqa: BLE001 - one unreadable dir must not hide the others
                continue
    except Exception:  # noqa: BLE001
        return found
    return found


def rescue_skipped_dirs_enabled() -> bool:
    """Default ON. `DEEPMAP_RESCUE_SKIPPED_DIRS=0` leaves the engine's directory skip list untouched."""
    return str(os.environ.get("DEEPMAP_RESCUE_SKIPPED_DIRS", "1")).strip().lower() not in {
        "0", "false", "no", "off"}


def _engine_noise_dir():
    """graphify's own directory-skip rule (`detect._is_noise_dir(name, parent=None)`), or None when the
    engine or that private symbol is unavailable (then nothing is rescued and nothing is flagged). Call
    it through _is_engine_noise, which passes the parent the way the engine's own walker does."""
    try:
        import graphify.detect as detect
    except Exception:  # noqa: BLE001 - an absent/changed engine just disables the rescue
        return None
    fn = getattr(detect, "_is_noise_dir", None)
    fn = getattr(fn, "__wrapped__", fn)   # the engine's rule even if the shim patched it in-process
    return fn if callable(fn) else None


def _is_engine_noise(noise, part: str, parent) -> bool:
    """Ask the engine's skip rule about directory `part` inside `parent` exactly as the engine's walker
    does (`_is_noise_dir(d, dp)`). graphify 0.8.x decides by name (the parent matters only for a
    `worktrees/` under a dotted dir); graphify 0.9.x decides `out` / `env` / `*_env` / `coverage` /
    `snapshots` from what is ON DISK in that directory and says "not noise" when no parent is given —
    so a name-only call made the census and the skipped-dir list disagree with what 0.9 really walks.
    A rule that takes a single argument is called with the name alone. Never raises (False)."""
    try:
        try:
            return bool(noise(part, Path(parent) if parent is not None else None))
        except TypeError:
            return bool(noise(part))
    except Exception:  # noqa: BLE001
        return False


def _noise_indexer(noise, root):
    """A memoized `first(parts)`: the index of the OUTERMOST engine-skipped directory among `parts[:-1]`
    (a root-relative file path's components), or None when the engine walks every directory on that
    path. The engine's rule is asked once per DIRECTORY (files share directories), with the parent the
    engine's walker would pass — a per-file walk cost ~8x the name-only check on a 200k-file repo."""
    root = Path(root)
    cache: dict = {(): None}

    def first_dir(dirs: tuple):
        if dirs in cache:
            return cache[dirs]
        up = first_dir(dirs[:-1])
        if up is None:
            i = len(dirs) - 1
            up = i if _is_engine_noise(noise, dirs[-1], root.joinpath(*dirs[:-1]) if i else root) else None
        cache[dirs] = up
        return up

    return lambda parts: first_dir(tuple(parts[:-1]))


def _first_noise_index(noise, root: Path, parts) -> int | None:
    """One-off form of _noise_indexer(noise, root)(parts)."""
    return _noise_indexer(noise, root)(parts)


# --- the engine versions the D5 re-include is validated against (E3) -----------------------------
# The rescue narrows graphify's name-based directory skip (graphify_case_shim.rescue_skipped_dirs) and
# mirrors it in the census; both were built and validated on graphifyy 0.8.39, whose rule prunes
# build/out/target/env/*_env/snapshots/... by NAME. graphifyy 0.9.x gates out/env/*_env/coverage/
# snapshots on what is on disk instead, so on 0.9 the rescue list, the census and the extraction stop
# agreeing (review finding: a tracked build/core.py never re-included while compiled out/*.js and a .env/
# tree entered the graph). The installers pin this range; outside it the re-include is SKIPPED (with a
# warning, and the verdict names the uncovered dirs and this remedy) rather than trusted blindly.
#
# The range is exactly the releases that were VALIDATED (round-4 decision F3): 0.8.39 and 0.8.51 ran the
# graphify-dependent suites end to end in real venvs, and every release between them (0.8.40 .. 0.8.50,
# including 0.8.48 — published, then YANKED: a range spec never selects it, but `==0.8.48` still installs
# it) was diffed statically on everything DeepMap leans on: `detect._is_noise_dir` is byte-identical in
# all of them, `_SKIP_DIRS` differs only by GRAPHIFY_OUT's basename (0.8.45+), `_PARALLEL_THRESHOLD` stays
# 20, the extractor lookup stays the exact `_DISPATCH.get(path.suffix)`, and the command line of
# `update ... --no-cluster` and the package's Requires-Dist are unchanged. What that writer PRODUCES did
# change (missed by the static diff, found by the round-7 review on Linux CI): 0.8.40 collapses repeated
# links (`build.dedupe_edges`, one per (source, target, relation)) and 0.8.41+ also collapses nodes sharing
# an id (`dedupe_nodes`, last one wins). Three round-7 corrections read exactly those repeats; since R8/J2
# every correction reads what all of them write, or the source (map_graph; tests/extraction_r8_gates.py
# holds 0.8.39's and 0.8.51's real output side by side). The parser table GREW (ENGINE_EXT_SINCE; nothing was removed)
# and re-routed ONE suffix: 0.8.50 moved `.vue` from extract_js (the whole SFC through the JS grammar, so a
# `<script setup lang="ts">` block yielded no symbols) to extract_vue (the <script> block only, plus a
# `dynamic_import` relation map_graph does not map — the same lazy import already arrives as an ordinary
# import); validated: strictly more Vue symbols, mapped like any TS module
# (tests/graphify_supported_range_gates.py section I). The old `<0.9` ceiling let pip install an
# unvalidated 0.8.51 whose new CUDA/Metal parsers DeepMap then dropped silently. The literal is read by
# tests/compliance_gates.py (it must equal requirements.txt, both installers and CI) — keep it a plain
# string literal.
SUPPORTED_GRAPHIFY_MIN = (0, 8, 39)          # inclusive
SUPPORTED_GRAPHIFY_BELOW = (0, 8, 52)        # exclusive
SUPPORTED_GRAPHIFY_SPEC = "graphifyy>=0.8.39,<0.8.52"

# Suffixes the engine's parser table GAINED inside the supported range -> the first release that
# dispatches it (from the published wheels; no in-range release removed one). A supported engine older
# than the entry cannot parse those files, and an in-range `pip install --upgrade` fixes that — the
# verdict names such files instead of reporting READY over them (upgradeable_unparsed_exts). .psd1/.xaml
# are listed for the record: DeepMap excludes them by design.
ENGINE_EXT_SINCE = {".psm1": (0, 8, 40), ".psd1": (0, 8, 41), ".cu": (0, 8, 46), ".cuh": (0, 8, 46),
                    ".metal": (0, 8, 50), ".xaml": (0, 8, 50)}


def graphify_version():
    """The installed engine's version string (distribution `graphifyy`, imported as `graphify`), or None
    when it cannot be determined. Never raises."""
    try:
        from importlib import metadata
        try:
            return str(metadata.version("graphifyy"))
        except metadata.PackageNotFoundError:
            pass
    except Exception:  # noqa: BLE001
        pass
    try:
        import graphify
        v = getattr(graphify, "__version__", None)
        return str(v) if v else None
    except Exception:  # noqa: BLE001
        return None


def _version_tuple(text):
    """The numeric release part of a version string, padded to 3 ('0.9' -> (0, 9, 0); '0.8.39.post1' ->
    (0, 8, 39); '0.9.0rc1' -> (0, 9, 0)), or None when it has no leading number."""
    m = re.match(r"\s*v?(\d+(?:\.\d+)*)", str(text or ""))
    if not m:
        return None
    nums = [int(x) for x in m.group(1).split(".")]
    return tuple((nums + [0, 0, 0])[:3])


def graphify_support():
    """(supported, version) for the D5 directory re-include: `supported` is True only when the installed
    graphify is inside [SUPPORTED_GRAPHIFY_MIN, SUPPORTED_GRAPHIFY_BELOW). An engine with no readable
    version (a source checkout without package metadata) counts as supported only when its skip rule
    behaves like the validated one (build/ and out/ skipped by name alone). Never raises."""
    try:
        v = graphify_version()
        t = _version_tuple(v) if v else None
        if t is not None:
            return SUPPORTED_GRAPHIFY_MIN <= t < SUPPORTED_GRAPHIFY_BELOW, v
        noise = _engine_noise_dir()
        ok = (noise is not None and _is_engine_noise(noise, "build", None)
              and _is_engine_noise(noise, "out", None))
        return ok, v
    except Exception:  # noqa: BLE001
        return False, None


def hint_python() -> str:
    """DeepMap's own interpreter as it must appear in a command the user pastes: ALWAYS double-quoted,
    with forward slashes. Git Bash (the shell Claude Code runs on Windows) treats the backslashes of an
    unquoted `C:\\Users\\...\\python.exe` as escapes and runs `C:Users...python.exe`; the quoted
    forward-slash form runs as written in bash and cmd (verified), and in PowerShell after its call
    operator (`& "C:/.../python.exe" -m ...`) — a leading quoted string is an expression there (D6).
    Never raises."""
    try:
        exe = sys.executable
        return f'"{Path(exe).as_posix()}"' if exe else "python"
    except Exception:  # noqa: BLE001
        return "python"


def _setup_remedy():
    """build/deps_bootstrap's "run /deep-map:setup (...)" when this Python lacks graphifyy or holds it outside its
    range (setup installs it into DeepMap's own environment, never into this Python); None otherwise. Never
    raises."""
    try:
        import deps_bootstrap
        return deps_bootstrap.setup_remedy(("graphifyy",))
    except Exception:  # noqa: BLE001
        return None


def graphify_install_command() -> str:
    """The runnable command that puts a SUPPORTED graphify into DeepMap's own interpreter: /deep-map:setup when
    this Python lacks graphifyy or holds it out of range (_setup_remedy), else pip with `--upgrade`, so an older
    in-range copy (0.8.39, which has no CUDA/Metal parser) is lifted to the top of the range. Never raises."""
    return _setup_remedy() or f'{hint_python()} -m pip install --upgrade "{SUPPORTED_GRAPHIFY_SPEC}"'


def unsupported_engine_hint(version) -> str:
    """The runnable remedy for an engine outside the validated range (D6: DeepMap's own interpreter)."""
    head = (f"graphify {version or '(unknown version)'} is outside the range DeepMap supports for this "
            f"({SUPPORTED_GRAPHIFY_SPEC})")
    setup = _setup_remedy()
    if setup:
        return f"{head} — {setup}, which also rebuilds the graph"
    return f"{head} — install it into DeepMap's Python: {graphify_install_command()}, then re-run /deep-map:refresh"


def upgradeable_unparsed_exts(version=None) -> dict:
    """{suffix: 'X.Y.Z'} — the DeepMap source suffixes the installed SUPPORTED engine has no parser for,
    though a later release inside SUPPORTED_GRAPHIFY_SPEC does (ENGINE_EXT_SINCE): the files an in-range
    `pip install --upgrade` would bring into the graph (0.8.39: .psm1, .cu, .cuh, .metal). {} when the
    version is unknown or outside the range — the range warning covers that case, and a suffix that NO
    in-range engine parses (.pyi, .r ...) is never listed: no remedy exists, so it must not DEGRADE a
    repo. `version` defaults to the installed engine's. Never raises."""
    try:
        v = graphify_version() if version is None else version
        t = _version_tuple(v) if v else None
        if t is None or not (SUPPORTED_GRAPHIFY_MIN <= t < SUPPORTED_GRAPHIFY_BELOW):
            return {}
        allowed = set(SOURCE_CODE_EXTS)
        return {ext: ".".join(str(x) for x in since) for ext, since in ENGINE_EXT_SINCE.items()
                if ext in allowed and t < since}
    except Exception:  # noqa: BLE001
        return {}


def _never_rescue(part: str) -> bool:
    low = part.lower()
    return low in _NEVER_RESCUE_DIRS or low.endswith(_NEVER_RESCUE_SUFFIXES)


# macOS metadata (an AppleDouble `._<name>` twin beside every file on an exFAT / SMB volume, a `__MACOSX/`
# mirror in a macOS-made archive, Netatalk's `.AppleDouble/` twins under the original names) is never source to
# re-include or to expect nodes from: a committed twin under build/ was listed as hand-written source, and the
# verdict then waited for nodes it can never get. The engine itself is told to leave it out of its file list and
# its own directory listings (macos_metadata_present -> graphify_case_shim.skip_macos_metadata /
# hide_macos_metadata_listings). Mirror of framework_edges.is_macos_metadata
# (this module does not import it; drift-locked by tests/macos_appledouble_gates.py).
_MACOS_METADATA_DIRS = frozenset({"__MACOSX", ".AppleDouble"})


def _is_macos_metadata(rel, is_dir: bool = False) -> bool:
    """True iff root-relative `rel` is macOS metadata: a file named `._*`, or anything with a `__MACOSX` or
    `.AppleDouble` directory component (`is_dir`: the directory is, or is under, one of them). Never raises."""
    if not isinstance(rel, str):
        return False
    parts = [p for p in rel.replace("\\", "/").split("/") if p]
    if not parts:
        return False
    if is_dir:
        return not _MACOS_METADATA_DIRS.isdisjoint(parts)
    return parts[-1].startswith("._") or not _MACOS_METADATA_DIRS.isdisjoint(parts[:-1])


def _gitignore_dir_parts(root) -> set:
    """Mirror of build/sync.py:_gitignore_dir_parts (drift-locked): the UNANCHORED bare-directory names
    in the repo-root .gitignore, which sync's vendor filter drops as directory components. Anchored,
    negated and glob patterns are skipped, exactly as there. Never raises."""
    parts = set()
    try:
        gi = Path(root) / ".gitignore"
        if not gi.is_file():
            return parts
        for line in gi.read_text(encoding="utf-8", errors="replace").splitlines():
            s = line.strip()
            if not s or s[0] in "#!/":
                continue
            if any(c in s for c in "*?[]"):
                continue
            seg = s.rstrip("/")
            if seg and "/" not in seg:
                parts.add(seg.lower())
    except Exception:  # noqa: BLE001
        pass
    return parts


def _looks_generated(path) -> bool:
    """True iff `path` looks like tool output rather than hand-written source (D5): a TypeScript
    declaration file, a "Code generated ... DO NOT EDIT" / @generated / <auto-generated> header, or — for
    JavaScript — a bundler/compiler runtime (webpack/ncc/esbuild/parcel/SystemJS/UMD, tsc or Babel
    CommonJS output), a sourceMappingURL trailer, a sibling `<name>.map` source map, or a minified
    line. Used only for files under a directory the engine skips by name, to decide what to re-include.
    Unreadable -> False (the engine's own read decides). Never raises."""
    try:
        p = Path(path)
        low = p.name.lower()
        if low.endswith(_DECLARATION_SUFFIXES):
            return True
        # A compiler/bundler writes its source map NEXT TO the output (`mod.js` + `mod.js.map`): plain-
        # looking compiled JS with no runtime marker and no trailer is still tool output. (graphify 0.9
        # treats the same .js.map as build-output evidence for a whole directory.)
        if low.endswith(_JS_EXTS) and p.with_name(p.name + ".map").is_file():
            return True
        with open(p, "rb") as fh:
            head = fh.read(_GENERATED_HEAD_BYTES)
            size = fh.seek(0, os.SEEK_END)
            tail = b""
            if low.endswith(_JS_EXTS):
                fh.seek(max(0, size - 512))
                tail = fh.read()
        top = head[:2048]
        if _GENERATED_HEADER.search(top) or _GENERATED_HEADER_I.search(top):
            return True
        if low.endswith(_JS_EXTS):
            if any(m in head for m in _JS_BUNDLE_MARKERS) or b"//# sourceMappingURL=" in tail:
                return True
            if any(len(line) >= _MINIFIED_LINE for line in head.split(b"\n")):
                return True
        return False
    except Exception:  # noqa: BLE001
        return False


def _git_z(root: Path, *args):
    """NUL-separated `git -C root <args>` output as a list (UTF-8, locale-independent), or None when
    git is absent or the command fails. Never raises."""
    git = shutil.which("git")
    if not git:
        return None
    try:
        p = subprocess.run([git, "-C", str(root), *args], capture_output=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if p.returncode != 0:
        return None
    return [s for s in (p.stdout or b"").decode("utf-8", "surrogateescape").split("\0") if s]


def skipped_source_dirs(root) -> dict:
    """G3: {outermost engine-skipped dir: [its qualifying git-tracked source files]} under `root`.

    graphify prunes directories by NAME at any depth (build, dist, target, out, env, *_env, snapshots,
    ...) before any include rule runs, so tracked source living in them never reaches the graph —
    DeepMap's own build/ was 100% missing while setup said READY. This lists what the engine would drop
    but DeepMap wants. A file qualifies (round-2 decision D5) only when it is:
      * git-tracked and not gitignored, with a source extension DeepMap maps, present on disk;
      * under no directory DeepMap never re-includes (_NEVER_RESCUE_DIRS: vendored deps, the
        virtualenv/env family, dist/, caches, VCS dirs);
      * kept by sync's default vendor filter (no minified/bundle suffix, no .gitignore bare-dir name);
      * hand-written (_looks_generated: not a bundle, compiled output, declaration file or a file whose
        header says it was generated), and not macOS metadata (_is_macos_metadata: an AppleDouble `._*`
        twin, anything under `__MACOSX/` or `.AppleDouble/`).
    So a directory holding only minified bundles or compiled output is not listed — nothing to
    re-include, nothing for the verdict to flag. A non-git tree returns {} (tracked-ness is unknowable
    there, so the engine's default stands). Used by extract() to re-include them (and to drop anything
    else graphify then finds under them) and by the verdict to flag any that still come back empty.
    Never raises."""
    try:
        root = Path(root).resolve()
        noise = _engine_noise_dir()
        if noise is None or not any((p / ".git").exists() for p in (root, *root.parents)):
            return {}
        tracked = _git_z(root, "ls-files", "-z", "--cached")
        if not tracked:
            return {}
        ignored = set(_git_z(root, "ls-files", "-z", "--cached", "--ignored", "--exclude-standard") or ())
        vendor_dirs = _gitignore_dir_parts(root)
        first_noise = _noise_indexer(noise, root)
        found: dict = {}
        for rel in tracked:
            low = rel.lower()
            if rel in ignored or not low.endswith(SOURCE_CODE_EXTS) or low.endswith(_VENDOR_FILE_SUFFIXES):
                continue
            if _is_macos_metadata(rel):
                continue
            parts = rel.split("/")
            first = first_noise(parts)
            if first is None:
                continue                                   # the engine walks it anyway
            outer = "/".join(parts[:first + 1])
            if any(_never_rescue(p) or p.lower() in vendor_dirs for p in parts[:-1]):
                continue
            path = root / rel
            if path.is_file() and not _looks_generated(path):
                found.setdefault(outer, []).append(rel)
        return {k: sorted(v) for k, v in sorted(found.items())}
    except Exception:  # noqa: BLE001 - a scan problem must never break extraction
        return {}


# --- source the engine skips and DeepMap cannot re-include (round 7: T1#0 / T2#0 / T3#0) ---------------
# D5 never re-includes the env family or dist/ (overwhelmingly a virtualenv / build output) — but a project
# can keep its OWN code there (pypa/build's src/build/env/, an RL package's cartpole_env/, create-t3-app's
# src/env/). That code was silently missing while the verdict said READY. These are the never-rescued names
# that can hold a project's own source; vendored dependency dirs (node_modules, vendor, ...) are third-party
# by convention and are never flagged.
_UNINDEXED_FLAG_DIRS = frozenset({"env", ".env", "venv", ".venv", "dist", "dist-protected"})
_UNINDEXED_FLAG_SUFFIXES = ("_env", "_venv")
_VENV_MARKERS = ("pyvenv.cfg", "conda-meta", "bin/activate", "Scripts/activate", "Scripts/activate.bat",
                 "Lib/site-packages")
_NONGIT_WALK_LIMIT = 20000


def _flaggable_never_rescued(part: str) -> bool:
    low = str(part).lower()
    return low in _UNINDEXED_FLAG_DIRS or low.endswith(_UNINDEXED_FLAG_SUFFIXES)


def _looks_like_virtualenv(d: Path) -> bool:
    """A real virtualenv / conda env (its marker files, or lib/pythonX.Y/): third-party code, never flagged."""
    try:
        if any((d / m).exists() for m in _VENV_MARKERS):
            return True
        lib = d / "lib"
        return lib.is_dir() and any(c.is_dir() and c.name.lower().startswith("python") for c in lib.iterdir())
    except OSError:
        return False


def unindexed_source_dirs(root) -> dict:
    """{outermost dir: {"files": [relpaths], "kind": "never-rescued" | "non-git"}} — hand-written source the
    engine skips by directory name that DeepMap does NOT re-include, so it is missing from the graph:

      * never-rescued (a git repo): git-tracked, not gitignored source under an env-family directory
        (env/, .env/, venv/, .venv/, *_env/, *_venv/) or dist/ — unless that directory is a real virtualenv
        (pyvenv.cfg, conda-meta, bin/activate, lib/pythonX.Y ...);
      * non-git (no git work tree): tracked-ness is unknowable, so NOTHING is re-included — the engine
        skips build/ out/ target/ snapshots/ ... and the env family and dist/ there. Listed only for files
        that are not generated (_looks_generated) and not a byte-identical copy of a same-named source file
        elsewhere in the tree (a setup.py build/lib/ tree, a copied bundle input).

    Vendored dependency dirs (node_modules, vendor, site-packages, ...), caches, VCS dirs and minified /
    bundle files are never listed; directories the rescue re-includes (skipped_source_dirs) are not listed
    here. The verdict turns what is listed into a named DEGRADED reason. {} when the engine's skip rule is
    unavailable or nothing qualifies. Never raises."""
    try:
        root = Path(root).resolve()
        noise = _engine_noise_dir()
        if noise is None:
            return {}
        in_git = shutil.which("git") is not None and any((p / ".git").exists() for p in (root, *root.parents))
        return _unindexed_git(root, noise) if in_git else _unindexed_nongit(root, noise)
    except Exception:  # noqa: BLE001 - a scan problem must never break extraction or the verdict
        return {}


def _unindexed_git(root: Path, noise) -> dict:
    tracked = _git_z(root, "ls-files", "-z", "--cached")
    if not tracked:
        return {}
    ignored = set(_git_z(root, "ls-files", "-z", "--cached", "--ignored", "--exclude-standard") or ())
    vendor_dirs = _gitignore_dir_parts(root)
    venv_cache: dict = {}
    found: dict = {}
    for rel in tracked:
        low = rel.lower()
        if rel in ignored or not low.endswith(SOURCE_CODE_EXTS) or low.endswith(_VENDOR_FILE_SUFFIXES):
            continue
        if _is_macos_metadata(rel):
            continue
        parts = rel.split("/")
        cand = None
        for i, part in enumerate(parts[:-1]):
            plow = part.lower()
            if plow in vendor_dirs or (_never_rescue(part) and not _flaggable_never_rescued(part)):
                break              # vendored / cache / VCS: third-party, never flagged
            parent = root.joinpath(*parts[:i]) if i else root
            if _flaggable_never_rescued(part) and _is_engine_noise(noise, part, parent):
                cand = "/".join(parts[:i + 1])
                break
            # a re-includable skipped dir (build/, out/ ...) is the rescue's business — but an env/dist
            # dir BELOW it (build/env/x.py) is re-included by nobody, so keep looking
        if cand is None:
            continue
        if any(p.lower() in ("site-packages", "lib64") for p in parts[:-1]):
            continue
        if cand not in venv_cache:
            venv_cache[cand] = _looks_like_virtualenv(root / cand)
        if venv_cache[cand]:
            continue
        path = root / rel
        if path.is_file() and not _looks_generated(path):
            found.setdefault(cand, []).append(rel)
    return {d: {"files": sorted(fs), "kind": "never-rescued"} for d, fs in sorted(found.items())}


def _unindexed_nongit(root: Path, noise) -> dict:
    cand_files: dict = {}                  # candidate dir -> [relpaths]
    outside: dict = {}                     # basename -> [paths] of source the engine DOES read
    seen = 0
    for dirpath, dirnames, filenames in os.walk(str(root)):
        here = Path(dirpath)
        try:
            rel_here = here.relative_to(root).as_posix()
        except ValueError:
            continue
        rel_here = "" if rel_here == "." else rel_here
        parts = rel_here.split("/") if rel_here else []
        cand = None
        for i, part in enumerate(parts):
            if "/".join(parts[:i + 1]) in cand_files:
                cand = "/".join(parts[:i + 1])
                break
        keep = []
        for d in dirnames:
            if d in (".git", ".hg", ".svn") or (_never_rescue(d) and not _flaggable_never_rescued(d)):
                continue                   # VCS / cache / vendored dependency dirs: never descended
            if _is_macos_metadata(d, is_dir=True):
                continue                   # a __MACOSX/ archive mirror, a .AppleDouble/: metadata, never source
            if _flaggable_never_rescued(d) and _looks_like_virtualenv(here / d):
                continue                   # a real virtualenv, wherever it sits
            if cand is None and _is_engine_noise(noise, d, here):
                cand_files.setdefault(f"{rel_here}/{d}" if rel_here else d, [])
            keep.append(d)
        dirnames[:] = keep
        for name in filenames:
            if seen >= _NONGIT_WALK_LIMIT:
                break
            low = name.lower()
            if not low.endswith(SOURCE_CODE_EXTS) or low.endswith(_VENDOR_FILE_SUFFIXES) or _is_macos_metadata(name):
                continue
            seen += 1
            p = here / name
            if cand is None:
                outside.setdefault(name, []).append(p)
            elif not _looks_generated(p):
                cand_files[cand].append(f"{rel_here}/{name}" if rel_here else name)
    found: dict = {}
    for d, files in cand_files.items():
        kept = [rel for rel in files if not _is_copy(root / rel, outside.get(rel.rsplit("/", 1)[-1], []))]
        if kept:
            found[d] = {"files": sorted(kept), "kind": "non-git"}
    return dict(sorted(found.items()))


def _is_copy(path: Path, others) -> bool:
    """True iff `path` has the same bytes as one of `others` (same basename elsewhere in the tree)."""
    try:
        size = path.stat().st_size
        data = None
        for o in others:
            try:
                if o.stat().st_size != size:
                    continue
                if data is None:
                    data = path.read_bytes()
                if o.read_bytes() == data:
                    return True
            except OSError:
                continue
    except OSError:
        return False
    return False


def _norm_rel(path) -> str:
    """A root-relative posix path compared the way the filesystem does (case-folded on Windows)."""
    rel = str(path or "").replace("\\", "/")
    while rel.startswith("./"):
        rel = rel[2:]
    return rel.lower() if os.name == "nt" else rel


def _keep_rescued_only(result, root: Path, skipped: dict):
    """Drop what graphify extracted under a re-included directory that did not qualify (D5).

    The shim re-includes whole DIRECTORIES (graphify decides per directory, and a very long list is
    re-included by name at any depth), so the engine also parses what sits beside the qualifying files:
    an untracked scratch file, a minified bundle, compiled output. Any node whose file lies under an
    engine-skipped directory but is not on the qualifying list is removed here, with its outgoing calls
    and imports (and, in precise mode, calls that target it by key). Returns (result, n_dropped_nodes);
    the same objects come back when nothing is dropped."""
    noise = _engine_noise_dir()
    if noise is None or not skipped:
        return result, 0
    keep = {_norm_rel(f) for files in skipped.values() for f in files}
    nodes, calls, imports = result
    first_noise = _noise_indexer(noise, root)
    dropped = set()
    for n in nodes:
        sf = str(n.get("source_file") or "").replace("\\", "/")
        if os.path.isabs(sf):
            try:
                sf = Path(sf).resolve().relative_to(root).as_posix()
            except (ValueError, OSError):
                continue
        parts = sf.split("/")
        if first_noise(parts) is not None and _norm_rel(sf) not in keep:
            dropped.add(n.get("key"))
    if not dropped:
        return result, 0
    precise = keyed_calls_enabled()       # only then can a call's second element be a node KEY
    keyed = resolved_imports_enabled()    # only then can an import's second element be a node KEY
    nodes = [n for n in nodes if n.get("key") not in dropped]
    # R11/R1: only a MARKED key targets a dropped node; a name that merely equals a dropped key stays a name
    calls = [c for c in calls if c[0] not in dropped and not (precise and is_call_key(c[1]) and c[1] in dropped)]
    imports = [i for i in imports if i[0] not in dropped and not (keyed and i[1] in dropped)]
    return (nodes, calls, imports), len(dropped)


def _rescue_payload(root: Path, skipped: dict) -> str:
    """The DEEPMAP_RESCUE_DIRS value for the shim: every engine-skipped directory on the path of a file
    to re-include (nested ones too — `build/out/x.py` needs both), as root-relative posix paths. A list
    too long for the environment degrades to rescuing those directory NAMES at any depth."""
    noise = _engine_noise_dir() or (lambda part, parent=None: False)
    root = Path(root)
    dirs = set()
    for files in skipped.values():
        for rel in files:
            parts = rel.split("/")
            dirs.update("/".join(parts[:i + 1]) for i, p in enumerate(parts[:-1])
                        if _is_engine_noise(noise, p, root.joinpath(*parts[:i]) if i else root))
    payload = json.dumps({"root": str(root), "dirs": sorted(dirs)})
    if len(payload) > _RESCUE_ENV_MAX:
        payload = json.dumps({"root": str(root), "names": sorted({d.rsplit("/", 1)[-1] for d in dirs})})
    return payload


# --- run diagnostics (G4) -------------------------------------------------------------------------
_OUTPUT_CHATTER = ("Re-extracting code files in", "Code graph updated.", "Tip: set ",
                   "[graphify watch] graph.json updated in")
# graphify's per-file failure lines: "warning: worker failed for <path>: <exc>" (parallel pool) and
# "warning: skipped <path> (<exc>)" (a parser error, either mode).
_FILE_FAILURE_RE = re.compile(r"^\s*warning: (?:worker failed for (?P<a>.+?): |skipped (?P<b>.+?) \()")


def _file_failures(stderr: str) -> list:
    """graphify's per-file failure lines for files DeepMap would map (a failure on a .md/.json it
    parses for its own purposes loses nothing from the code graph)."""
    out = []
    for line in (stderr or "").splitlines():
        m = _FILE_FAILURE_RE.match(line)
        if m and str(m.group("a") or m.group("b") or "").lower().endswith(SOURCE_CODE_EXTS):
            out.append(line.strip())
    return out
MAXPATH_HINT = ("a path exceeded the Windows 260-character MAX_PATH limit — enable long paths "
                "(LongPathsEnabled=1 under HKLM\\SYSTEM\\CurrentControlSet\\Control\\FileSystem, then "
                "reboot) or move the project / DEEPMAP_HOME to a shorter path")


def _output_tail(text: str, *, keep: int = 6, width: int = 700) -> str:
    """The last meaningful lines of graphify's combined output, one line. graphify prints its real
    failure ('Rebuild failed: ...') on STDOUT and only 'check output above' on stderr, so both streams
    are needed for the cause to reach the user."""
    kept = [s.strip() for s in (text or "").splitlines()
            if s.strip() and not s.strip().startswith(_OUTPUT_CHATTER)]
    return " | ".join(kept[-keep:])[-width:]


def looks_like_maxpath(text: str) -> bool:
    """True iff graphify's output shows a Windows path-length failure: WinError 206, or a 'No such file'
    error on a quoted path at or past the 248/260-char directory/file limits."""
    if os.name != "nt":
        return False
    t = text or ""
    if "WinError 206" in t or "filename or extension is too long" in t:
        return True
    if not re.search(r"\[(?:Errno 2|WinError 3)\]", t):
        return False
    return any(len(p) >= 248 for p in re.findall(r"'([A-Za-z]:\\[^']+)'", t))


# Counters map_graph reports for the round-7 corrections (extract_with_report copies them into its report
# and sync._rebuild_code into `.info`, so the manifest and doctor can say how the graph was built).
_ROUND7_COUNTERS = ("calls_filtered", "imports_bare_resolved", "supplemental_nodes", "overloads_moved",
                    "file_ids_split", "calls_plus")


def _log_round7_counters(report: dict) -> None:
    """One stderr line per correction that changed something (an unusual extraction step must leave a
    trace in the sync log). Never raises."""
    try:
        msgs = {
            "calls_filtered": ("dropped {} call(s) the language rules out (a _test.go target, a case "
                               "mismatch, a bare call into another file's method, a Go `pkg.F()` bound to "
                               "the caller's own F) — DEEPMAP_FALSE_CALL_FILTER=0 to keep them"),
            "imports_bare_resolved": "bound {} bare module import(s) the engine left unresolved",
            "supplemental_nodes": ("recovered {} declaration(s) the engine dropped (TS/JS id collisions, "
                                   "Rust trait methods)"),
            "overloads_moved": "pointed {} @overload group(s) at the implementation instead of the first stub",
            "file_ids_split": ("kept {} file :Module node(s) whose id the engine reused for a same-named "
                               "entity (a Go type, a TS `const $`)"),
            "calls_plus": "added {} Python call(s) from the import table / nested closures (DEEPMAP_GRAPHIFY_CALLS_PLUS)",
        }
        for k, fmt in msgs.items():
            if int(report.get(k) or 0):
                print(f"[graphify_extract] {fmt.format(int(report[k]))}", file=sys.stderr)
    except Exception:  # noqa: BLE001
        pass


# --- the engine child never outlives the sync (T10d-10) -------------------------------------------------
_STDLIB_SUBPROCESS_RUN = subprocess.run      # see _run_engine's test seam


class _EngineResult:
    """What _run_engine returns: the subprocess.CompletedProcess fields extract_with_report reads."""

    def __init__(self, args, returncode, stdout, stderr):
        self.args, self.returncode, self.stdout, self.stderr = args, returncode, stdout, stderr


# POSIX watchdog: a separate tiny process in its own session that ignores terminal signals and blocks on a
# pipe only the sync writes to. EOF without the "done" byte means the sync died — it then kills the engine's
# whole process group (the engine and its ProcessPoolExecutor workers).
_WATCHDOG_SRC = (
    "import os, signal, sys\n"
    "for s in ('SIGINT', 'SIGTERM', 'SIGHUP'):\n"
    "    try:\n"
    "        signal.signal(getattr(signal, s), signal.SIG_IGN)\n"
    "    except Exception:\n"
    "        pass\n"
    "fd, pgid = int(sys.argv[1]), int(sys.argv[2])\n"
    "try:\n"
    "    data = os.read(fd, 1)\n"
    "except OSError:\n"
    "    data = b''\n"
    "if data != b'0':\n"
    "    try:\n"
    "        os.killpg(pgid, signal.SIGKILL)\n"
    "    except OSError:\n"
    "        pass\n")


def _win_kill_on_close_job(proc):
    """A Windows job object with JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE holding `proc` (every process it starts
    joins the job; when the last handle — ours, never inherited — closes, the OS kills them all), or None
    when it cannot be set up (then the engine runs as it always did). Never raises."""
    try:
        import ctypes
        from ctypes import wintypes

        class _IoCounters(ctypes.Structure):
            _fields_ = [(n, ctypes.c_ulonglong) for n in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class _Basic(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class _Extended(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", _Basic), ("IoInfo", _IoCounters),
                        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
        k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        job = k32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = _Extended()
        info.BasicLimitInformation.LimitFlags = 0x2000          # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if (not k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
                or not k32.AssignProcessToJobObject(job, int(proc._handle))):
            k32.CloseHandle(job)
            return None
        return job
    except Exception:  # noqa: BLE001
        return None


def _win_close_job(job, *, kill: bool) -> None:
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        if kill:
            k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
            k32.TerminateJobObject(job, 1)
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        k32.CloseHandle(job)
    except Exception:  # noqa: BLE001
        pass


def _posix_watchdog(pgid: int):
    """(watchdog Popen, write fd) guarding process group `pgid`, or None. Never raises."""
    try:
        r, w = os.pipe()
    except OSError:
        return None
    try:
        wd = subprocess.Popen([sys.executable, "-c", _WATCHDOG_SRC, str(r), str(pgid)], pass_fds=(r,),
                              start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL)
    except Exception:  # noqa: BLE001
        for fd in (r, w):
            try:
                os.close(fd)
            except OSError:
                pass
        return None
    os.close(r)
    return wd, w


def _release_watchdog(handle) -> None:
    """Tell the watchdog the engine finished normally (it then exits without killing anything)."""
    wd, w = handle
    for step in (lambda: os.write(w, b"0"), lambda: os.close(w)):
        try:
            step()
        except OSError:
            pass
    try:
        wd.wait(timeout=10)
    except Exception:  # noqa: BLE001
        pass


def _kill_engine_tree(proc, job) -> None:
    """Kill the engine and everything it started (a timeout, or this process being interrupted)."""
    try:
        if job is not None:
            _win_close_job(job, kill=True)
        elif os.name != "nt":
            import signal
            os.killpg(proc.pid, signal.SIGKILL)
    except Exception:  # noqa: BLE001
        pass
    try:
        proc.kill()
    except Exception:  # noqa: BLE001
        pass


def _same_file_path(a, b) -> bool:
    try:
        return os.path.normcase(os.path.abspath(str(a))) == os.path.normcase(os.path.abspath(str(b)))
    except Exception:  # noqa: BLE001
        return False


def _venv_launcher_bypass(cmd, env):
    """(cmd, env) to start `cmd` with (R11/L2). A Windows venv's `Scripts\\python.exe` is a LAUNCHER: it starts
    the real (base) interpreter in a job object of its own that lets that interpreter's children break away
    silently — so graphify's ProcessPoolExecutor workers escaped _run_engine's KILL_ON_JOB_CLOSE job and
    survived a killed or timed-out sync forever (measured with the real engine 10 s after the kill: 9 of 15
    engine processes alive on graphifyy 0.8.51, 6 of 14 on 0.8.39; 0 with a base interpreter, and 0 with this
    bypass). When `cmd` starts this very interpreter and it is such a launcher
    (sys._base_executable differs), the engine is started the way multiprocessing starts its own children
    since bpo-35797: the base interpreter directly, with __PYVENV_LAUNCHER__ naming the venv's launcher so
    the child still resolves the venv (its sys.prefix and site-packages). Anything else — POSIX, a base
    interpreter, another program, a base path that does not exist — comes back unchanged."""
    try:
        exe = sys.executable
        base = getattr(sys, "_base_executable", None)
        if (os.name != "nt" or not cmd or not exe or not base or _same_file_path(base, exe)
                or not _same_file_path(cmd[0], exe) or not os.path.isfile(base)):
            return cmd, env
        env = dict(os.environ if env is None else env)
        env["__PYVENV_LAUNCHER__"] = exe
        return [base, *list(cmd)[1:]], env
    except Exception:  # noqa: BLE001 — the launch as it always was
        return cmd, env


def _run_engine(cmd, *, cwd, env, timeout):
    """Run the engine child like subprocess.run(capture_output, UTF-8, timeout) — but so it can never
    outlive this process (T10d-10). A plain subprocess.run left the engine and its ProcessPoolExecutor
    workers running after the sync was killed (kill -9, TerminateProcess, a closed terminal), still writing
    into the <DEEPMAP_HOME>/gout/<key> dir the next sync clears and re-reads; and a timeout killed only the
    direct child. Windows: the child goes into a KILL_ON_JOB_CLOSE job object. POSIX: the child gets its own
    session and a watchdog kills that process group when this process dies. A timeout (or an interrupt)
    kills the whole tree, then re-raises. If the job/watchdog cannot be set up the engine still runs, as
    before. Returns an _EngineResult (whose `args` is `cmd` as given). R11/L2: from a Windows venv the
    engine is started through the base interpreter, not the venv's launcher (_venv_launcher_bypass) — the
    launcher's own job let the engine's pool workers break out of ours, so they outlived a killed sync.

    Test seam: when subprocess.run has been replaced (the hermetic gates fake the engine that way — never
    the case in production), the call goes to it with the exact arguments the engine was always run
    with."""
    if subprocess.run is not _STDLIB_SUBPROCESS_RUN:
        return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env,
                              encoding="utf-8", errors="replace")
    launch, env = _venv_launcher_bypass(cmd, env)    # R11/L2: never a Windows venv launcher (a job escape)
    kw = dict(cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
              text=True, encoding="utf-8", errors="replace")
    if os.name != "nt":
        kw["start_new_session"] = True
    proc = subprocess.Popen(launch, **kw)
    job = _win_kill_on_close_job(proc) if os.name == "nt" else None
    guard = _posix_watchdog(proc.pid) if os.name != "nt" else None
    try:
        try:
            out, err = proc.communicate(timeout=timeout)
        except BaseException:
            _kill_engine_tree(proc, job)
            job = None
            try:
                proc.communicate(timeout=30)
            except Exception:  # noqa: BLE001
                pass
            raise
    finally:
        if guard is not None:
            _release_watchdog(guard)
        if job is not None:
            _win_close_job(job, kill=False)         # KILL_ON_JOB_CLOSE reaps any straggling worker
    return _EngineResult(cmd, proc.returncode, out, err)


# ---- the engine's time budget (round 11, M1) ----------------------------------------------------------------
# `graphify update` ran under a FIXED 600 s. On a large repo on a loaded machine that is not enough (measured:
# fastapi, 1,129 .py files, 522-622 s with the CPU saturated — two re-syncs crossed 600 s): the run timed out, sync
# fell back to ast, the engine-downgrade guard refused the push, and the graph silently stayed the previous build.
# The budget now scales with the source files the engine will parse, and DEEPMAP_GRAPHIFY_TIMEOUT overrides it.
ENV_GRAPHIFY_TIMEOUT = "DEEPMAP_GRAPHIFY_TIMEOUT"
GRAPHIFY_TIMEOUT_FLOOR_S = 600            # the old fixed budget: every tree gets at least this (small repos unchanged)
GRAPHIFY_TIMEOUT_PER_FILE_S = 1.5         # ~2.7x the worst per-file rate measured under load (fastapi <= 0.55 s/file)
GRAPHIFY_TIMEOUT_DEFAULT_CAP_S = 14400    # the size-aware default stops at 4 h, so a hung engine still ends
GRAPHIFY_TIMEOUT_MIN_S = 60               # an explicit DEEPMAP_GRAPHIFY_TIMEOUT is clamped into [MIN, MAX]
GRAPHIFY_TIMEOUT_MAX_S = 86400            # 24 h


def engine_source_file_count(root, *, limit: int | None = None, rescue=None) -> int:
    """How many source files under `root` the engine will parse, as DeepMap counts them (for the time budget):
    files whose suffix, in any case, is one of DeepMap's SOURCE_CODE_EXTS, walked the way the engine walks —
    its own directory-skip rule (graphify's _is_noise_dir: .git, node_modules, virtualenvs, build/, dist/, ...),
    or _SCAN_IGNORE_DIRS when that rule is unavailable. Data / markup files the engine also reads (.md, .json)
    are not counted: the per-file rate was measured against source files. Stops counting at `limit`. Never
    raises (0 on any error).

    `rescue` = (dirs, names), exactly what the case shim receives in DEEPMAP_RESCUE_DIRS (_rescue_payload): an
    engine-skipped directory the shim re-includes — by root-relative path, or by name at any depth — is walked
    the way the shim makes the engine walk it (R11b/V4): WHOLE, every source file in it, not only the git-tracked
    ones that qualified it (DeepMap drops the rest only after the engine has parsed them, _keep_rescued_only).
    Paths a .gitignore / .graphifyignore hides from the engine are still counted here — an over-count only
    lengthens the budget, never shortens it. macOS metadata (AppleDouble `._*` twins, `__MACOSX/`, `.AppleDouble/`)
    is not counted, like in the census and the skipped-dir lists: whenever the tree holds some the engine is told to
    leave it out of its file list (macos_metadata_present, graphify_case_shim.skip_macos_metadata), so it costs no
    engine time (tests/macos_appledouble_gates.py F4). Symbolic links to directories are not followed — neither does the engine DeepMap runs: `graphify update`
    calls watch._rebuild_code with follow_symlinks=False, which reaches detect() as an explicit False, so
    detect's follow-when-the-root-has-a-symlinked-child auto rule (graphify extract / a full build) never
    applies (every supported graphify, 0.8.39-0.8.51; tests/sync_timeout_r11b_gates.py locks it)."""
    n = 0
    try:
        base = Path(root)
        if not base.is_dir():
            return 0
        noise = _engine_noise_dir()
        exts = frozenset(SOURCE_CODE_EXTS)
        rescued = _rescued_dir_test(base, rescue)
        for dirpath, dirnames, filenames in os.walk(str(base)):
            if noise is not None:
                dirnames[:] = [d for d in dirnames if not _is_macos_metadata(d, is_dir=True)
                               and (not _is_engine_noise(noise, d, dirpath) or rescued(d, dirpath))]
            else:
                dirnames[:] = [d for d in dirnames if d not in _SCAN_IGNORE_DIRS
                               and not _is_macos_metadata(d, is_dir=True)]
            for name in filenames:
                dot = name.rfind(".")
                if dot > 0 and name[dot:].lower() in exts and not name.startswith("._"):
                    n += 1
                    if limit is not None and n >= limit:
                        return n
    except Exception:  # noqa: BLE001 - a count problem only sizes the budget; never break extraction
        return n
    return n


def macos_metadata_present(root, *, limit: int | None = None, rescue=None) -> bool:
    """Does the engine meet macOS metadata where it can change the graph of `root` — an AppleDouble `._*` file, a
    `__MACOSX` or a `.AppleDouble` directory (_is_macos_metadata)? extract() asks the case shim to leave it out of
    graphify's file list and directory listings (DEEPMAP_SKIP_MACOS_METADATA) only when this says so, so every other
    tree keeps the byte-identical engine command and environment.

    graphify's detect() has no dot-file rule: it parsed every twin and everything under `__MACOSX/`, and a twin
    takes its original's engine id (`pkg/._a.py` and `pkg/a.py` are both `pkg_a_py_pkg_a`; the last in sort order
    wins, so `routes/+page.ts` came back as Module `._+page`). The walk is the odd-case suffix scan's discipline
    (os.walk, directories pruned in place, names matched BELOW the root — never the path above it — an early return
    at the first hit) over the directories detect() walks, exactly as engine_source_file_count does: graphify's own
    skip rule, plus the directories the shim re-includes for `rescue` = (dirs, names), or _SCAN_IGNORE_DIRS when that
    rule is unavailable. Metadata under a directory detect() skips (node_modules/, a build/ that is not re-included)
    never reaches the engine's file list.

    It can still reach the graph through the Pascal/Delphi resolvers, which list the tree themselves for every
    .pas .pp .dpr .dpk .lpr .inc (extract_pascal, which the engine runs for a C/C++ `.inc` too) and .lpk
    (extract_lazarus_package) file the engine parses: they pick a Pascal project root — the highest of the file's
    directory and the 11 above it holding two `*.pas` or one `*.dpr` entries, never a filesystem root, so possibly a
    directory above `root` (graphify.extract._pascal_project_root) — and map unit and class names to files with an
    UNPRUNED Path.rglob from it, the last file listed for a stem winning. A `.AppleDouble/Base.pas` or a
    `__MACOSX/.../Base.pas` under a skipped out/, build/ or node_modules/ then takes over Base.pas's stem (measured on
    0.8.39 and 0.8.51: the imports of the unit it shadows were lost, and a shadowed Base.pas's TBase class node
    changed key). So when the walk meets a Pascal source (in any case, as DeepMap compares extensions; a .inc or .pp
    only when its text could reach a resolver — a `uses` clause or a base list — since both are also C/C++/PHP/
    assembly includes and Puppet manifests) and no metadata, it climbs from each such file's directory the way the
    engine does, then: walks, whole, the skipped directories under the Pascal roots inside `root`; answers True for a
    `._*.pas` / `._*.dpr` twin in any directory on the climb above `root` (the engine counts it while it picks the
    root: a directory holding one .pas and its twin becomes the root); and when a root lies above `root`, walks that
    root's tree beside `root` too. In
    those walks only a `.AppleDouble` or `__MACOSX` directory counts: a twin's stem is `._base`, which no Pascal
    identifier can be, and a skipped directory is on no parsed file's climb. A metadata directory `root` itself lives
    in is not counted — the shim's listing filter would hide the project's own files from a listing rooted above it.
    A tree without Pascal source keeps the walk detect() makes.

    The walk covers the tree detect() walks plus every directory a .gitignore / .graphifyignore hides from it (it
    reads neither), so a tree with large ignored directories costs more than the engine's own walk (measured: about
    3 s on a site-packages-sized tree of 32,601 source files, about 1 s on DeepMap's repository); in a Pascal tree,
    plus at most 11 directory listings above `root` for the climb and one walk of the skipped directories under the
    Pascal roots (the resolvers rglob each root up to nine times). A caller's budget of `limit`
    files that runs out first answers True, and so does an error mid-walk: a tree this could not finish may hold
    metadata, and only "no" is unsafe — the shim is then not engaged, a twin takes its original's id and sync's
    backstop drops the real file's node by key — while the filters are a no-op on a clean tree
    (tests/macos_appledouble_gates.py G1, J3). A directory above `root` that cannot be listed counts as empty, as it
    does for the engine's Path.glob. A missing root or a non-path: False. Never raises."""
    try:
        base = Path(root)
        if not base.is_dir():
            return False
    except Exception:  # noqa: BLE001 - not a path: nothing to scan
        return False
    pascal_exts = frozenset({".pas", ".pp", ".dpr", ".dpk", ".lpr", ".inc", ".lpk"})
    reach = re.compile(rb"\buses\b|\b(?:class|object|interface|dispinterface)\s*\(|\bhelper\s*(?:\(|for\b)", re.I)
    seen = 0

    def resolver_text(path) -> bool:
        """Can this .inc / .pp file reach the Pascal resolvers — a `uses` clause or a class / interface / helper base
        list (an over-approximating screen)? Both extensions are also C/C++/PHP/assembly includes and Puppet
        manifests, which the engine parses as Pascal but runs no resolver for. Unreadable: yes (the safe direction)."""
        try:
            with open(path, "rb") as fh:
                return bool(reach.search(fh.read(1 << 20)))
        except OSError:
            return True

    def over(n: int) -> bool:
        """n more files walked: did the budget run out before the tree did?"""
        nonlocal seen
        seen += n
        return limit is not None and seen > limit

    def globbed(names) -> tuple:
        """(`*.pas`, `*.dpr`) entries among `names`, matched the way the engine's Path.glob matches them (the OS's
        case rule)."""
        pas = dpr = 0
        for nm in names:
            nc = os.path.normcase(nm)
            pas += nc.endswith(".pas")
            dpr += nc.endswith(".dpr")
        return pas, dpr

    try:
        noise = _engine_noise_dir()
        rescued = _rescued_dir_test(base, rescue)
        walk_top = str(base)
        pruned = []          # (root-relative parts, path) of each directory the skip rule removed
        counts = {}          # root-relative parts -> (`*.pas`, `*.dpr`) entries, for a directory holding any
        pascal_dirs = set()  # root-relative parts of each directory holding a file the Pascal resolvers run for
        for dirpath, dirnames, filenames in os.walk(walk_top):
            if noise is not None:
                kept = [d for d in dirnames if not _is_engine_noise(noise, d, dirpath) or rescued(d, dirpath)]
            else:
                kept = [d for d in dirnames if d not in _SCAN_IGNORE_DIRS]
            rel = None
            if len(kept) != len(dirnames) or any(d.lower().endswith((".pas", ".dpr")) for d in dirnames):
                rel = () if dirpath == walk_top else tuple(os.path.relpath(dirpath, walk_top).split(os.sep))
                keep = set(kept)
                pruned.extend((rel + (d,), os.path.join(dirpath, d)) for d in dirnames if d not in keep)
                dir_counts = globbed(dirnames)
            else:
                dir_counts = (0, 0)
            dirnames[:] = kept
            if any(_is_macos_metadata(d, is_dir=True) for d in dirnames):
                return True
            file_counts, pascal = (0, 0), False
            for name in filenames:
                if limit is not None and seen >= limit:
                    return True                # the budget ran out before the tree did: it may hold metadata
                seen += 1
                if name.startswith("._"):
                    return True
                low = name.lower()
                if low.endswith((".pas", ".pp", ".dpr", ".dpk", ".lpr", ".inc", ".lpk")):
                    dot = low.rfind(".")
                    ext = low[dot:] if dot > 0 else ""
                    if not pascal and ext in pascal_exts:
                        pascal = ext not in (".inc", ".pp") or resolver_text(os.path.join(dirpath, name))
                    if low.endswith((".pas", ".dpr")):
                        c = globbed((name,))
                        file_counts = (file_counts[0] + c[0], file_counts[1] + c[1])
            if pascal or file_counts != (0, 0) or dir_counts != (0, 0):
                if rel is None:
                    rel = () if dirpath == walk_top else tuple(os.path.relpath(dirpath, walk_top).split(os.sep))
                if pascal:
                    pascal_dirs.add(rel)
                if file_counts != (0, 0) or dir_counts != (0, 0):
                    counts[rel] = (file_counts[0] + dir_counts[0], file_counts[1] + dir_counts[1])
        if not pascal_dirs:
            return False

        # The Pascal resolvers' own listings: climb from every Pascal directory the way the engine does.
        try:
            top = base.resolve()
        except Exception:  # noqa: BLE001
            top = Path(os.path.abspath(walk_top))
        above = []           # the directories above `root`, nearest first, up to (not including) a filesystem root
        for a in top.parents:
            if len(a.parts) <= 1:
                break
            above.append(a)
        listed = {}

        def above_counts(h: int) -> tuple:
            """(`*.pas`, `*.dpr`, a `._*.pas` / `._*.dpr` twin among them) in the h-th directory above `root`."""
            if h not in listed:
                try:
                    with os.scandir(above[h - 1]) as it:
                        names = [e.name for e in it]
                except OSError:
                    names = []                 # the engine's Path.glob lists nothing there either
                pas, dpr = globbed(names)
                twin = any(nm.startswith("._") and sum(globbed((nm,))) for nm in names)
                listed[h] = (pas, dpr, twin)
            return listed[h]

        in_roots, out_root = set(), 0
        for rel in pascal_dirs:
            best_in, best_out = rel, 0
            for level in range(12):            # the file's directory and the 11 above it (_pascal_project_root)
                if level <= len(rel):
                    cur = rel[:len(rel) - level]
                    if not cur and len(top.parts) <= 1:
                        break                  # `root` is a filesystem root: never a Pascal project root
                    pas, dpr = counts.get(cur, (0, 0))
                    if pas >= 2 or dpr >= 1:
                        best_in = cur
                else:
                    h = level - len(rel)
                    if h > len(above):
                        break
                    pas, dpr, twin = above_counts(h)
                    if twin:
                        return True            # a twin the engine counts while it picks the root
                    if pas >= 2 or dpr >= 1:
                        best_out = h
            if best_out:
                out_root = max(out_root, best_out)
            else:
                in_roots.add(best_in)
        if out_root:
            targets = [p for _r, p in pruned]  # the root above holds all of `root`
        else:
            targets = [p for r, p in pruned if any(r[:k] in in_roots for k in range(len(r)))]
        for path in targets:
            for _dp, dirnames, filenames in os.walk(path):
                if any(_is_macos_metadata(d, is_dir=True) for d in dirnames) or over(len(filenames)):
                    return True
        if out_root:
            top_key = os.path.normcase(str(top))
            for dirpath, dirnames, filenames in os.walk(str(above[out_root - 1])):
                kept = []
                for d in dirnames:
                    full = os.path.normcase(os.path.join(dirpath, d))
                    if full == top_key:
                        continue               # `root` itself: walked above
                    if _is_macos_metadata(d, is_dir=True) and not top_key.startswith(full + os.sep):
                        return True
                    kept.append(d)
                dirnames[:] = kept
                if over(len(filenames)):
                    return True
    except Exception:  # noqa: BLE001 - a scan problem must never break extraction; unfinished -> may hold some
        return True
    return False


def _rescued_dir_test(base: Path, rescue):
    """`test(part, parent)`: is directory `part` inside `parent` (a path under `base`, as os.walk yields it) one the
    case shim re-includes for `rescue` = (dirs, names)? The shim's own rule (graphify_case_shim.rescue_skipped_dirs):
    a name in `names` at any depth, or a root-relative path in `dirs`, compared with the OS's case rule. Always False
    without a rescue. Never raises."""
    try:
        dirs, names = rescue if rescue else ((), ())
        keep = {os.path.normcase(str(d).replace("/", os.sep)) for d in (dirs or ()) if d}
        keep_names = {str(nm) for nm in (names or ()) if nm}
        walk_base = str(base)
    except Exception:  # noqa: BLE001
        keep, keep_names, walk_base = set(), set(), ""
    if not keep and not keep_names:
        return lambda part, parent: False

    def test(part, parent) -> bool:
        if part in keep_names:
            return True
        if not keep:
            return False
        try:
            return os.path.normcase(os.path.relpath(os.path.join(str(parent), part), walk_base)) in keep
        except ValueError:             # another drive on Windows: not under the root
            return False

    return test


def _rescue_spec(payload) -> tuple | None:
    """(dirs, names) from a DEEPMAP_RESCUE_DIRS value (_rescue_payload's JSON), or None. Never raises."""
    try:
        data = json.loads(payload) if payload else None
        if not isinstance(data, dict):
            return None
        return list(data.get("dirs") or []), list(data.get("names") or [])
    except Exception:  # noqa: BLE001
        return None


def _env_timeout():
    """(seconds or None, problem or None) from DEEPMAP_GRAPHIFY_TIMEOUT: unset / blank -> (None, None); a positive
    finite number of seconds -> rounded up and clamped into [GRAPHIFY_TIMEOUT_MIN_S, GRAPHIFY_TIMEOUT_MAX_S] (a
    clamp is reported); anything else -> (None, why it was ignored). Never raises."""
    raw = str(os.environ.get(ENV_GRAPHIFY_TIMEOUT, "") or "").strip()
    if not raw:
        return None, None
    try:
        val = float(raw)
    except ValueError:
        val = float("nan")
    if not math.isfinite(val) or val <= 0:
        return None, (f"ignored {ENV_GRAPHIFY_TIMEOUT}={raw!r}: not a positive number of seconds (allowed "
                      f"{GRAPHIFY_TIMEOUT_MIN_S}-{GRAPHIFY_TIMEOUT_MAX_S})")
    secs = int(math.ceil(val))
    if secs < GRAPHIFY_TIMEOUT_MIN_S:
        return GRAPHIFY_TIMEOUT_MIN_S, (f"{ENV_GRAPHIFY_TIMEOUT}={raw} is below the {GRAPHIFY_TIMEOUT_MIN_S} s "
                                        f"minimum — using {GRAPHIFY_TIMEOUT_MIN_S} s")
    if secs > GRAPHIFY_TIMEOUT_MAX_S:
        return GRAPHIFY_TIMEOUT_MAX_S, (f"{ENV_GRAPHIFY_TIMEOUT}={raw} is above the {GRAPHIFY_TIMEOUT_MAX_S} s "
                                        f"(24 h) maximum — using {GRAPHIFY_TIMEOUT_MAX_S} s")
    return secs, None


def graphify_timeout(root=None, *, n_files: int | None = None, extra_files: int = 0, rescue=None) -> dict:
    """The engine's time budget: {"timeout_s", "source" ("env" | "size-aware"), "source_files", "problem"}.

    DEEPMAP_GRAPHIFY_TIMEOUT (seconds) wins when it is valid (clamped into [60, 86400]; a clamp or an ignored
    value is reported in `problem`). Otherwise the SIZE-AWARE default: max(600, ceil(1.5 s x the source files the
    engine will parse)), capped at 4 h — `n_files` when given, else engine_source_file_count(root, rescue=rescue)
    (the walk stops at the cap; `rescue` = the (dirs, names) the case shim re-includes, walked whole as the engine
    walks them — R11b/V4), plus `extra_files` (callers that count re-included files themselves). Never raises."""
    secs, problem = _env_timeout()
    if secs is not None:
        return {"timeout_s": secs, "source": "env", "source_files": n_files, "problem": problem}
    try:
        if n_files is None:
            cap_files = int(math.ceil(GRAPHIFY_TIMEOUT_DEFAULT_CAP_S / GRAPHIFY_TIMEOUT_PER_FILE_S)) + 1
            if root is None:
                n_files = 0
            elif rescue:
                n_files = engine_source_file_count(root, limit=cap_files, rescue=rescue)
            else:
                n_files = engine_source_file_count(root, limit=cap_files)
        n_files = max(0, int(n_files) + max(0, int(extra_files or 0)))
        scaled = int(math.ceil(GRAPHIFY_TIMEOUT_PER_FILE_S * n_files))
        secs = min(GRAPHIFY_TIMEOUT_DEFAULT_CAP_S, max(GRAPHIFY_TIMEOUT_FLOOR_S, scaled))
    except Exception:  # noqa: BLE001
        secs = GRAPHIFY_TIMEOUT_FLOOR_S
    return {"timeout_s": secs, "source": "size-aware", "source_files": n_files, "problem": problem}


def timeout_cause(timeout, source: str | None, n_files) -> str:
    """The failure cause for an engine run stopped by its time budget. Names DEEPMAP_GRAPHIFY_TIMEOUT early: sync
    quotes the first 200 characters of a cause in its downgrade message, the verdict and the manifest."""
    why = {"env": f"set by {ENV_GRAPHIFY_TIMEOUT}",
           "size-aware": (f"the size-aware default for {n_files} source files" if n_files is not None
                          else "the size-aware default"),
           }.get(str(source or ""), "the caller's budget")
    return (f"graphify timed out after {timeout} s ({why}) and was stopped — set {ENV_GRAPHIFY_TIMEOUT}=<seconds> to "
            f"allow it longer (up to {GRAPHIFY_TIMEOUT_MAX_S})")


def extract_with_report(workspace_root, *, timeout: int | None = None, source: _Source | None = None):
    """`extract()` plus a report of how the engine run went: returns (result, report).

    `source` (S1b, the shared-reader seam — sync passes one only while its typed-call pass will run): a `_Source`
    of this root that the CALLER keeps; map_graph reads every file it needs through it, so that later pass
    (typed_calls.link(..., source=...)) reuses those reads and parses (receiver-calls design 2.1). It never changes
    the result; None, or a reader of another root, = map_graph's own fresh reader, exactly as before.

    `result` is exactly what extract() returns. `report` always has: `cause`
    (why there is no usable result, else None), `output_tail` (graphify's last meaningful lines), `failed_files` +
    `failed_examples` (files graphify could not extract — a PARTIAL graph is still returned, but it is
    counted, logged and handed to the verdict instead of passing silently), `maxpath` (the failure
    looks like Windows MAX_PATH), `graphify_out` and `rescued_dirs` (G3), `graphify_version` +
    `graphify_supported` (E3) and, when an engine outside the validated range made DeepMap SKIP the
    re-include, `rescue_blocked` (the dirs it would have re-included), plus the import-key counts
    `resolved_imports` / `imports_resolved` / `imports_by_name`, and the engine's time budget (round 11):
    `timeout_s`, `timeout_source` ("env" | "size-aware" | "caller"), `source_files` and `timed_out` (True when
    the engine was stopped by that budget — the cause then names DEEPMAP_GRAPHIFY_TIMEOUT). `timeout` None (the
    default) = graphify_timeout(): DEEPMAP_GRAPHIFY_TIMEOUT, else the size-aware default. Never raises."""
    import time
    report: dict = {"cause": None, "output_tail": "", "failed_files": 0, "failed_examples": [],
                    "maxpath": False, "graphify_out": None, "rescued_dirs": [], "rescue_dropped": 0,
                    "rescue_blocked": [], "graphify_version": None, "graphify_supported": None,
                    "self_call_rebind": self_call_rebind_enabled(), "self_calls_rebound": 0,
                    "resolved_imports": resolved_imports_enabled(), "imports_resolved": 0,
                    "imports_by_name": 0, "timeout_s": None, "timeout_source": None, "source_files": None,
                    "timed_out": False, **{k: 0 for k in _ROUND7_COUNTERS}}
    root = Path(workspace_root).resolve()

    def fail(cause: str):
        report["cause"] = cause
        print(f"[graphify_extract] {cause}", file=sys.stderr)
        if report["maxpath"]:
            print(f"[graphify_extract] likely cause: {MAXPATH_HINT}", file=sys.stderr)

    try:
        import importlib.util
        if importlib.util.find_spec("graphify") is None:
            report["cause"] = "graphify (the multi-language engine) is not installed"
            # The cause stays short (sync, the manifest and the verdict quote it); the runnable fix —
            # pinned, for THIS interpreter, quoted for Git Bash — goes to the log beside it (D6).
            print(f"[graphify_extract] graphify is not installed in DeepMap's Python — install it: "
                  f"{graphify_install_command()}", file=sys.stderr)
            return None, report
        out = graphify_out_dir(root)
        out.mkdir(parents=True, exist_ok=True)   # graphify creates only the LAST level itself
        report["graphify_out"] = str(out)
        gpath = out / "graph.json"
        try:
            # G2: start from an EMPTY graph. `graphify update` merges the previous graph.json back in
            # (every old link between surviving ids, appended again), which kept removed calls alive,
            # grew the file by a full copy per sync, and made a deleted def trip the shrink guard.
            # Only graph.json goes — the per-file AST cache beside it is what keeps re-syncs fast.
            gpath.unlink(missing_ok=True)
        except OSError as e:
            print(f"[graphify_extract] could not clear the previous graph.json ({e}); a graph this run "
                  f"does not rewrite is still rejected below", file=sys.stderr)
        env = dict(os.environ, PYTHONUTF8="1", GRAPHIFY_OUT=str(out))
        for k in (ENV_CASE_ALIASES, ENV_RESCUE, ENV_SKIP_MACOS_METADATA):
            env.pop(k, None)             # only this run's own decisions reach the shim
        # R8: a project module named like a stdlib one must not shadow the engine's own imports. `python -m`
        # puts the CWD (the project root) first on sys.path, so a root-level types.py / enum.py / token.py
        # made `import enum` inside the engine fail and the whole extraction fell back to ast (measured:
        # click's own package tree, rc=1 "cannot import name 'MappingProxyType' from 'types'").
        # PYTHONSAFEPATH=1 (3.11+) keeps the CWD off sys.path without changing the command line; 3.10
        # ignores it, so there the engine starts in its own output dir instead (it is always handed the
        # ABSOLUTE root, and resolves nothing against its working directory).
        env[ENV_SAFE_PATH] = "1"
        engine_cwd = str(root) if sys.version_info >= (3, 11) else str(out)
        # The engine keys its extractor table by the EXACT suffix, so a legacy `UNIT1.PAS` tree
        # extracts NOTHING; and it prunes build/dist/target/out/env/... by name even when they hold
        # tracked source; and it lists macOS metadata as code. When (and only when) one applies, route the
        # SAME `graphify update` through build/graphify_case_shim.py, which widens the suffix table / narrows
        # the skip list / narrows the file list. Otherwise the command line and the environment are
        # byte-identical to what they always were.
        cmd = [sys.executable, "-m", "graphify", "update", str(root), "--no-cluster"]
        supported, version = graphify_support()
        report["graphify_version"], report["graphify_supported"] = version, supported
        if not supported:
            print(f"[deepmap] WARNING: graphify {version or '(unknown version)'} is outside the range "
                  f"DeepMap is validated against ({SUPPORTED_GRAPHIFY_SPEC})", file=sys.stderr)
        skipped = skipped_source_dirs(root) if rescue_skipped_dirs_enabled() else {}
        if skipped and not supported:
            # E3: the re-include narrows a skip rule whose semantics changed in graphify 0.9 — trusting
            # it there made extraction and the verdict disagree. Skip it; the verdict names these dirs.
            report["rescue_blocked"] = sorted(skipped)
            print(f"[deepmap] NOT re-including git-tracked source in engine-skipped directories "
                  f"({', '.join(d + '/' for d in sorted(skipped)[:5])}{', ...' if len(skipped) > 5 else ''}"
                  f"): {unsupported_engine_hint(version)}", file=sys.stderr)
            skipped = {}
        odd = odd_case_source_suffixes(root) if case_insensitive_ext_enabled() else []
        if skipped and case_insensitive_ext_enabled():
            # The suffix scan never descends build/ out/ target/ (it mirrors the engine's skip), so an
            # UPPERCASE file there (build/UNIT1.PAS) would be re-included but still not parsed.
            odd = sorted(set(odd) | {s for s in (Path(f).suffix for fs in skipped.values() for f in fs)
                                     if s != s.lower() and s.lower() in SOURCE_CODE_EXTS})
        rescue_env = _rescue_payload(root, skipped) if skipped else None
        # macOS metadata (AppleDouble `._*` twins, __MACOSX/, .AppleDouble/) the engine would list as code and parse
        # — a twin even takes its original's node id. Looked for where the engine walks (re-included dirs too).
        metadata = macos_metadata_present(root, rescue=_rescue_spec(rescue_env) if skipped else None)
        if odd or skipped or metadata:
            cmd = [sys.executable, str(Path(__file__).resolve().parent / "graphify_case_shim.py"),
                   str(root), *odd]
            # Say so. The shim's own stderr is captured and discarded on the success path, and a
            # silently-engaged (or silently no-op) shim is exactly how the ProcessPoolExecutor
            # regression stayed invisible — an unusual extraction mode must leave a trace in the log.
            if odd:
                print(f"[deepmap] odd-case source extensions present ({', '.join(odd)}) — extracting "
                      f"via the case-insensitive shim (DEEPMAP_CASE_INSENSITIVE_EXT=0 to disable)",
                      file=sys.stderr)
            if metadata:
                env[ENV_SKIP_MACOS_METADATA] = "1"
                report["macos_metadata"] = True
                print("[deepmap] macOS metadata in the tree (AppleDouble ._* files, __MACOSX/ or .AppleDouble/ "
                      "directories) — the engine is told to leave it out of its file list and its own "
                      "directory listings", file=sys.stderr)
            if skipped:
                env[ENV_RESCUE] = rescue_env
                report["rescued_dirs"] = sorted(skipped)
                print(f"[deepmap] re-including git-tracked source the engine skips by directory name "
                      f"({', '.join(d + '/' for d in sorted(skipped)[:5])}"
                      f"{', ...' if len(skipped) > 5 else ''}) — DEEPMAP_RESCUE_SKIPPED_DIRS=0 to "
                      f"disable", file=sys.stderr)
        # Round 11 (M1): the engine's time budget — an explicit `timeout`, else DEEPMAP_GRAPHIFY_TIMEOUT, else the
        # size-aware default. R11b/V4: the re-included directories count WHOLE, walked by the very rule handed to
        # the shim — the engine parses every source file in them (not only the tracked ones that qualified them).
        if timeout is None:
            budget = graphify_timeout(root, rescue=_rescue_spec(env.get(ENV_RESCUE)) if skipped else None)
            if budget.get("problem"):
                print(f"[deepmap] WARNING: {budget['problem']}", file=sys.stderr)
            timeout = int(budget["timeout_s"])
            report.update(timeout_s=timeout, timeout_source=budget.get("source"),
                          source_files=budget.get("source_files"))
        else:
            report.update(timeout_s=timeout, timeout_source="caller")
        start = time.time()   # freshness anchor, BEFORE the subprocess
        try:
            # T10d-10: never a plain subprocess.run — the engine (and its worker pool) must die with us.
            proc = _run_engine(cmd, cwd=engine_cwd, env=env, timeout=timeout)
        except subprocess.TimeoutExpired:
            # _run_engine already killed the whole engine tree. Say what happened and how to allow more time —
            # not a dump of the command line (the old cause), which pushed the remedy past what sync quotes.
            report["timed_out"] = True
            fail(timeout_cause(timeout, report.get("timeout_source"), report.get("source_files")))
            return None, report
        finally:
            # After the engine (which is the first process this run starts on a non-git tree), and even
            # when it failed or timed out: the dir exists either way and a purge must be able to find it.
            _write_owner_marker(out, root)
        output = f"{proc.stdout or ''}\n{proc.stderr or ''}"
        failures = _file_failures(proc.stderr)
        report.update(output_tail=_output_tail(output), failed_files=len(failures),
                      failed_examples=failures[:3], maxpath=looks_like_maxpath(output))
        if not gpath.exists():
            fail(f"graphify exited rc={proc.returncode} without writing graph.json: "
                 f"{report['output_tail'] or '(no output)'}")
            return None, report
        # F3e freshness guarantee: graph.json is trusted ONLY if THIS run (re)wrote it. A non-zero exit
        # can leave a prior run's file behind (e.g. when clearing it above failed); reusing it would
        # report a broken re-sync as READY. The mtime compare (vs a strict returncode==0 gate) still
        # accepts a fresh graph written alongside graphify's benign per-file warnings.
        if gpath.stat().st_mtime < start - 2:
            fail(f"graph.json is STALE (not rewritten this run; rc={proc.returncode}): "
                 f"{report['output_tail'] or '(no output)'}")
            return None, report
        stats: dict = {}
        # S1b: the caller's reader, when it handed one in (map_graph uses it only for this root)
        result = map_graph(json.loads(gpath.read_text(encoding="utf-8")), root=root, stats=stats, source=source)
        report["self_call_rebind"] = bool(stats.get("self_call_rebind"))
        report["self_calls_rebound"] = int(stats.get("self_calls_rebound") or 0)
        for k in _ROUND7_COUNTERS:
            report[k] = int(stats.get(k) or 0)
        _log_round7_counters(report)
        if int(stats.get("precise_unkeyed") or 0) or int(stats.get("precise_receiver_names") or 0):
            print(f"[graphify_extract] precise CALLS: {int(stats.get('precise_unkeyed') or 0)} member call(s) "
                  f"the engine bound to a module-level function were left to the name fan-out, "
                  f"{int(stats.get('precise_receiver_names') or 0)} more also fan out beside the key "
                  f"(`obj.f()` / `self.x.f()` never names a module-level f)", file=sys.stderr)
        n_rk, n_tn, n_dup, n_bare = (int(stats.get(k) or 0) for k in ("precise_type_rekeyed", "precise_type_names",
                                                                       "precise_placeholder_dups",
                                                                       "precise_bare_dropped"))
        if n_rk or n_tn or n_dup or n_bare:
            print(f"[graphify_extract] precise CALLS fixes: {n_rk} call(s) the engine bound to a TS type alias / "
                  f"interface now bind the value declared beside it ({n_tn} more fan out by name), {n_dup} "
                  f"duplicate name(s) from graphify placeholder nodes dropped, {n_bare} key(s) to a method a bare "
                  f"call cannot reach dropped — DEEPMAP_PRECISE_FIXES=0 to disable", file=sys.stderr)
        unknown = precise_calls_unknown()
        if unknown:
            print(f"[graphify_extract] DEEPMAP_PRECISE_CALLS: ignored unknown family name(s) "
                  f"{', '.join(repr(u) for u in unknown)} (they select nothing) — known: "
                  f"{', '.join(sorted(_precise_family_names()))}; TypeScript and JavaScript are 'js', 1 selects "
                  f"every file, 0 none", file=sys.stderr)
        if int(stats.get("calls_plus_test_skipped") or 0):
            print(f"[graphify_extract] DEEPMAP_GRAPHIFY_CALLS_PLUS: skipped {int(stats['calls_plus_test_skipped'])} "
                  f"call(s) made by test code — DEEPMAP_GRAPHIFY_CALLS_PLUS_TESTS=1 to add them", file=sys.stderr)
        if report["self_calls_rebound"]:
            print(f"[graphify_extract] re-bound {report['self_calls_rebound']} in-file self./cls./super() "
                  f"method call(s) graphify had tied to the wrong class ({stats.get('self_calls_moved', 0)} "
                  f"moved to the caller's class, {stats.get('self_calls_ambiguous', 0)} left AMBIGUOUS, "
                  f"{stats.get('self_calls_dropped', 0)} dropped) — DEEPMAP_SELF_CALL_REBIND=0 to disable",
                  file=sys.stderr)
        if skipped:
            # The engine walked each re-included directory whole; keep only its qualifying files (D5).
            result, report["rescue_dropped"] = _keep_rescued_only(result, root, skipped)
            if report["rescue_dropped"]:
                print(f"[deepmap] dropped {report['rescue_dropped']} node(s) the engine found beside the "
                      f"re-included source (untracked, minified or generated files)", file=sys.stderr)
        report["resolved_imports"] = bool(stats.get("resolved_imports"))
        report["imports_resolved"], report["imports_by_name"] = (
            import_key_counts(result[2], result[0]) if report["resolved_imports"] else (0, len(result[2])))
        if not result[0]:
            fail("graphify produced 0 code nodes" + (
                f" ({len(failures)} file(s) failed, e.g. {failures[0][:200]})" if failures else
                f": {report['output_tail']}" if report["output_tail"] else ""))
        elif failures:
            print(f"[graphify_extract] WARNING: graphify could not extract {len(failures)} file(s) — they "
                  f"are MISSING from the graph (e.g. {failures[0][:200]})", file=sys.stderr)
            if report["maxpath"]:
                print(f"[graphify_extract] likely cause: {MAXPATH_HINT}", file=sys.stderr)
        return result, report
    except Exception as e:  # noqa: BLE001 — any failure => fall back to ast
        fail(f"failed ({type(e).__name__}: {e})")
        return None, report


def extract(workspace_root, *, timeout: int | None = None, source: _Source | None = None):
    """Run `graphify update --no-cluster` over `workspace_root` and map the result (`source`: as
    extract_with_report's — a reader of this root to map through, kept by the caller).

    Returns (nodes, calls, imports), or None when the run left no graph to map — graphify absent, a run
    stopped by its time budget, graph.json missing, STALE (not rewritten by this run) or unreadable, any
    other error — so the caller can fall back to the ast extractor. A non-zero engine exit is not one of
    those by itself: a graph.json this run wrote is mapped whatever the exit code (graphify exits non-zero
    on benign per-file warnings, so the F3e freshness check decides, not the return code —
    tests/graphify_freshness_gates.py). A mapped result can be PARTIAL (the files graphify could not
    extract are missing; extract_with_report() counts them) or hold 0 code nodes (its report's `cause`
    says so), so a caller treats an empty node list like None (sync does). The engine writes to
    graphify_out_dir(root) and starts from an empty graph.json (G1/G2); the reason for a failure or a
    partial run is printed, and returned by extract_with_report().
    `timeout` None = graphify_timeout() (DEEPMAP_GRAPHIFY_TIMEOUT, else the size-aware default).
    """
    if source is None:                    # the call exactly as before S1b
        return extract_with_report(workspace_root, timeout=timeout)[0]
    return extract_with_report(workspace_root, timeout=timeout, source=source)[0]
