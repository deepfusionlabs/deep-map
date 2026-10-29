#!/usr/bin/env python3
"""DeepMap setup verdict (P1 / P4 / P5) — an HONEST READY / DEGRADED / FAILED classification of a
setup or sync outcome, so `/deep-map:setup` never reports plain SUCCESS on a silently-empty or
vector-less graph.

Pure functions — no Neo4j, no network, no imports of the ranking layer — so they are fully
unit-testable and cannot disturb a live instance. The orchestration layer (build/provision.py,
build/deepmap_cli.py) feeds in the facts it already observed and prints / acts on the verdict.

Fixes three findings from real use (2026-07-05 field report), all of which were the SAME defect —
setup declaring success on a broken graph:
  P1  missing embedding model  -> setup "succeeds" with every vector arm dead (FTS-only)  -> now LOUD
  P4  non-Python repo          -> 0-node graph "succeeds" silently                        -> now census-aware FAILED
  P5  degraded outcomes        -> invisible                                               -> now a named DEGRADED verdict

The exit status belongs to the commands, not to this module. `deepmap sync` (build/deepmap_cli.py
cmd_sync) exits 0 synced (READY, or DEGRADED with this run's graph pushed) / 1 refused, failed or
verdict FAILED / 3 Neo4j unreachable / 4 NOT PUSHED (the previous graph is kept); `provision.py setup`
exits 0 / 1 FAILED / 4 graph kept. A DEGRADED verdict is printed and persisted but never sets the
exit status itself (search still works): the run exits 0 unless it pushed nothing (4). There is no
`--strict` flag: exit_code() below is an unwired helper, and its docstring says why its strict mapping
must never become a command's exit status (ISS-build:026).

This module never imports or modifies build/retrieval.py: a verdict changes what setup, sync and
doctor SAY, never how search ranks. (retrieval.py itself is not frozen; its 3-arm ranking functions
are hash-locked by tests/fts_r7_gates.py.)
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

READY, DEGRADED, FAILED = "READY", "DEGRADED", "FAILED"

# Source extensions counted for the language census (a superset incl. graphify's languages). Used
# to tell a user "you have N .cs files but the Python-only 'ast' extractor built this graph" (forced,
# or an automatic fallback from graphify) — it does not drive extraction.
_SOURCE_EXTS = {
    ".py": "Python", ".pyi": "Python", ".cs": "C#", ".ts": "TypeScript", ".tsx": "TypeScript",
    ".js": "JavaScript", ".jsx": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript",
    ".go": "Go", ".rs": "Rust", ".java": "Java",
    ".rb": "Ruby", ".erb": "ERB", ".php": "PHP", ".c": "C", ".h": "C/C++", ".cpp": "C++", ".cc": "C++",
    ".cxx": "C++", ".hpp": "C++", ".kt": "Kotlin", ".kts": "Kotlin", ".swift": "Swift",
    ".scala": "Scala", ".m": "Objective-C", ".mm": "Objective-C",
    # Delphi / Object Pascal. The census MUST know these even though graphify can already parse them:
    # it is the only layer that can see a .pas file the extractor produced nothing for, and saying so
    # (DEGRADED, named) is what stops a Delphi-free graph from reporting a confident READY. It also
    # covers the case DeepMap cannot fix — graphify's parser dispatch is case-SENSITIVE, so a legacy
    # `UNIT1.PAS` yields no nodes upstream, while this census counts it (suffixes are lowercased).
    ".pas": "Pascal/Delphi", ".pp": "Pascal/Delphi", ".dpr": "Pascal/Delphi",
    ".dpk": "Pascal/Delphi", ".lpr": "Pascal/Delphi", ".inc": "Pascal/Delphi",
    # The remainder of the extractor's allowlist. An extension DeepMap extracts but cannot COUNT is
    # invisible to _coverage_reasons, so ~0 nodes for it can never raise a LOW-COVERAGE reason —
    # tests/delphi_pascal_gates.py locks census ⊇ graphify_extract.SOURCE_CODE_EXTS.
    ".sh": "Shell", ".bash": "Shell", ".ps1": "PowerShell", ".psm1": "PowerShell",
    ".lua": "Lua", ".luau": "Lua", ".pl": "Perl", ".pm": "Perl", ".r": "R", ".sql": "SQL",
    ".vue": "Vue", ".svelte": "Svelte", ".groovy": "Groovy", ".gradle": "Groovy", ".zig": "Zig",
    ".ex": "Elixir", ".exs": "Elixir", ".jl": "Julia", ".dart": "Dart", ".astro": "Astro",
    ".v": "Verilog", ".sv": "Verilog", ".svh": "Verilog", ".razor": "Razor", ".cshtml": "Razor",
    ".cls": "Apex", ".trigger": "Apex", ".dm": "DM",
    ".f": "Fortran", ".f90": "Fortran", ".f95": "Fortran", ".f03": "Fortran", ".f08": "Fortran",
    # CUDA / Metal — the C/C++ family (graphify parses them with its C++ grammar, from 0.8.46 / 0.8.50).
    # Their OWN buckets, not folded into "C++": a GPU layer the engine could not parse (graphify 0.8.39
    # has no parser for them) would otherwise hide behind a repo's well-extracted .cpp files.
    ".cu": "CUDA", ".cuh": "CUDA", ".metal": "Metal",
}
# ".erb" is its OWN census language (NOT folded into Ruby): the Ruby recognizer emits ONE searchable
# :Module view node per .erb, so an ERB density is ~1.0 — folding it into the Ruby bucket would dilute
# the model-layer signal and could invent a false Ruby DEGRADED (F9).
# Directories never counted as project source. Mirrors the extractor's OWN default vendor filter
# (build/sync.py:_VENDOR_DIR_PARTS — node_modules/vendor/public/coverage/…) PLUS common noise, so the
# census can't count vendored files sync dropped from the graph and then report a false LOW-COVERAGE
# DEGRADED (the field case: hundreds of public/*.min.js jQuery bundles -> false "LOW COVERAGE for
# JavaScript"). `public`/`coverage`/the bundler dirs were the gap vs sync (F9).
_IGNORE_PARTS = {"__pycache__", ".venv", "venv", ".git", "node_modules", ".deepmap", "dist",
                 "build", ".mypy_cache", ".pytest_cache", ".tox", "vendor", "target", ".idea",
                 ".vs", "bin", "obj", "public", "bower_components", "coverage",
                 ".yarn", ".pnp", ".next", ".nuxt", ".turbo", ".svelte-kit"}
# Minified/generated bundle suffixes sync's vendor filter drops (build/sync.py:_VENDOR_FILE_SUFFIXES) —
# excluded from the census by SUFFIX so a `foo.min.js` outside a vendored dir is not counted either.
_VENDOR_FILE_SUFFIXES = (".min.js", ".min.css", ".bundle.js", "-min.js", ".min.mjs")
# macOS metadata (AppleDouble `._<name>` twins, `__MACOSX/` archive mirrors, Netatalk's `.AppleDouble/` twins
# under the original names) is never a census file: a twin beside every file on an exFAT / SMB volume doubled a
# language's file count with no new nodes, enough to raise a false LOW COVERAGE. Mirror of
# framework_edges.is_macos_metadata (this module imports no extraction module; drift-locked by
# tests/macos_appledouble_gates.py).
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

# --- per-language / per-directory coverage (the Rails field-report honesty layer) -----------------
# Conventional framework dirs whose emptiness signals a BLIND extraction layer (the 2026-07 field
# report: a Rails repo's app/models/ produced 0 nodes — declarative ActiveRecord DSL is invisible to
# the generic engine — yet the GLOBAL-count verdict said READY). Tracked only when the dir exists.
_CONVENTIONAL_DIRS = ("app/models", "app/controllers", "app/jobs", "app/services",
                      "app/views", "app/helpers", "lib")
_DIR_MIN_FILES = 3        # a conventional dir needs this many source files before it can be flagged
_DIR_DENSITY_FLOOR = 0.5  # nodes-per-file below which a conventional dir counts as a dead layer
_LANG_MIN_FILES = 10      # a census language needs this many files before ~0 nodes is flagged
_LANG_DENSITY_FLOOR = 0.05  # nodes-per-file below which a whole language counts as unextracted
# ...but a language with this many files and ZERO engine nodes is flagged below that floor (T8d-B1: 5 C#
# files the engine produced nothing for passed as READY). A few stray files of a minor language (< 5)
# stay silent, as they always have.
_LANG_ZERO_MIN_FILES = 5
# A partial graphify run (per-file failures) DEGRADES once the failures are this many files, or this
# share of the census — one unparseable file in a big repo is logged, not a degraded graph (G4).
_FAILED_FILES_MIN = 10
_FAILED_FILES_SHARE = 0.05
# Under the opt-in DEEPMAP_EXCLUDE_TESTS sync drops the files THE test rule (sync._is_test_path) names on purpose,
# so they are not "missing": _test_path_predicate hands that rule to the skipped / unindexed directory findings.
# These mirror build/sync.py:_TEST_DIR_PARTS and _DOTNET_TEST_DIR_SUFFIXES for its fallback (drift-locked by
# tests/extraction_engine_gates.py and tests/test_rule_mirror_gates.py).
_TEST_DIR_PARTS = {"test", "tests", "__tests__", "spec", "specs", "testing", "e2e", "__mocks__"}
_DOTNET_TEST_DIR_SUFFIXES = (".tests", ".test", ".unittests", ".unittest", ".integrationtests", ".integrationtest",
                             ".functionaltests", ".acceptancetests", ".specs")
# Windows path limits (T10#14 / T10d-8): with LongPathsEnabled=0 a directory path of 248+ characters cannot
# be listed and a file path of 260+ cannot be opened — os.walk skipped such subtrees SILENTLY, the engine
# dropped them too, and the census could not see what it never listed, so the verdict said READY.
_MAX_PATH = 260
_MAX_DIR = 248
_LONG_PATH_WALK_LIMIT = 5000
# {resolved root: {"dirs": [...], "files": [...]}} — what the last extension_census walk could not read,
# picked up (once) by build_coverage so the tree is not walked twice. Only ever filled on Windows with
# LongPathsEnabled=0.
_CENSUS_PROBLEMS: dict = {}


# {resolved root: frozenset(node keys)} — the nodes the EXTRACTION ENGINE produced for the root's last
# _rebuild_code (sync registers them via note_engine_nodes; build_coverage consumes the entry once). The
# per-language recognizers are merged in afterwards, so this is the one exact record of which nodes are
# engine output.
_ENGINE_KEYS: dict = {}


def note_engine_nodes(root, nodes) -> None:
    """Record the node keys the extraction engine produced for `root` (called by sync._rebuild_code, before
    the recognizers merge their supplemental nodes in), so the next build_coverage(root, ...) counts
    ENGINE nodes only (T8d-B1). Never raises."""
    try:
        if len(_ENGINE_KEYS) > 32:
            _ENGINE_KEYS.clear()
        _ENGINE_KEYS[_root_key(root)] = frozenset(
            str(n.get("key")) for n in (nodes or []) if isinstance(n, dict) and n.get("key") is not None)
    except Exception:
        pass


def _is_engine_node(n, engine_exts, engine_keys=None) -> bool:
    """True unless `n` is a supplemental RECOGNIZER node of a language the extraction engine parses
    (T8d-B1). The C#/Java/Go/Ruby recognizers add one node per class/model whether or not the engine parsed
    the file, so counting them made a C# layer the engine never read look extracted. A recognizer-only
    language by design (ERB views) always counts. Otherwise: an explicit `origin: "recognizer"` tag is not
    engine output; with `engine_keys` (the engine's own node keys — see note_engine_nodes) membership
    decides exactly; without any engine information every node counts, as before. Never raises."""
    try:
        if _ext_of(str(n.get("source_file") or "")) not in engine_exts:
            return True
        if n.get("origin") == "recognizer":
            return False
        if engine_keys is not None:
            return str(n.get("key")) in engine_keys
        return True
    except Exception:
        return True


def _engine_exts() -> set:
    """The suffixes the extraction engine is asked to parse (graphify_extract.SOURCE_CODE_EXTS)."""
    gx = _extract_mod()
    try:
        return {str(e).lower() for e in gx.SOURCE_CODE_EXTS} if gx is not None else set(_SOURCE_EXTS)
    except Exception:
        return set(_SOURCE_EXTS)


def _long_paths_enabled() -> bool:
    """True when this machine can read paths past MAX_PATH (every non-Windows OS; Windows with
    HKLM\\SYSTEM\\CurrentControlSet\\Control\\FileSystem LongPathsEnabled=1). Never raises."""
    if os.name != "nt":
        return True
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem") as k:
            return int(winreg.QueryValueEx(k, "LongPathsEnabled")[0]) == 1
    except Exception:
        return False


def _root_key(root) -> str:
    try:
        return os.path.normcase(str(Path(root).resolve()))
    except Exception:
        return str(root)


def _extract_mod():
    """build/graphify_extract (lazily — it is only needed for the engine's own skip rules), or None."""
    try:
        here = str(Path(__file__).resolve().parent)
        if here not in sys.path:
            sys.path.insert(0, here)
        import graphify_extract
        return graphify_extract
    except Exception:
        return None


def _setup_remedy(*names):
    """deps_bootstrap.setup_remedy: "run /deep-map:setup (...)" when one of `names` is missing from this Python or
    outside its range (setup installs it into DeepMap's own environment); None otherwise. Never raises."""
    try:
        here = str(Path(__file__).resolve().parent)
        if here not in sys.path:
            sys.path.insert(0, here)
        import deps_bootstrap
        return deps_bootstrap.setup_remedy(names)
    except Exception:
        return None


def _dir_ignore_rule():
    """The directory-name predicate the census uses: the static set above, plus every directory the
    extraction engine never walks — its own skip rule (build, out, target, snapshots, ...) and the dirs
    DeepMap never re-includes (the virtualenv/env family, dist, caches: graphify_extract._never_rescue).
    Counting files the engine is DESIGNED to skip would make the census report a gap that is not one —
    a `python -m venv env` holds thousands of .py files, a TypeScript project's out/ holds its compiled
    .js — and raise a false LOW-COVERAGE DEGRADED. Tracked source the engine skips is judged separately
    (build_coverage's skipped_dirs). The rule is `ignored(part, parent=None)`: the engine's own walker
    passes the parent directory, and graphify 0.9.x needs it (it keeps `out/` / `env/` / `coverage/` /
    `snapshots/` unless what is on disk there says build output / virtualenv / report), so the census
    asks the same question the engine asks. Never raises."""
    gx = _extract_mod()
    never = getattr(gx, "_never_rescue", None) if gx else None
    noise = gx._engine_noise_dir() if gx and hasattr(gx, "_engine_noise_dir") else None
    ask = getattr(gx, "_is_engine_noise", None) if gx else None

    def ignored(part: str, parent=None) -> bool:
        try:
            if part in _IGNORE_PARTS or (never is not None and never(part)):
                return True
            if noise is None:
                return False
            return bool(ask(noise, part, parent)) if ask is not None else bool(noise(part))
        except Exception:
            return part in _IGNORE_PARTS
    return ignored


def _ignored_below(p: Path, root: Path, rule=None) -> bool:
    """True iff a directory component BELOW `root` is ignored (`rule(part, parent)`, default: the static
    set). Matching the absolute parts made a repo living under any ancestor named build/, target/,
    vendor/, public/ ... count as empty, silently switching the whole coverage check off (the same
    absolute-vs-relative trap graphify_extract.odd_case_source_suffixes documents)."""
    try:
        parts = p.relative_to(root).parts[:-1]
        base = Path(root)
    except ValueError:
        parts = p.parts[:-1]
        base = None
    rule = rule or (lambda part, parent=None: part in _IGNORE_PARTS)
    for i, part in enumerate(parts):
        parent = (base.joinpath(*parts[:i]) if i else base) if base is not None else None
        if _rule_says(rule, part, parent):
            return True
    return False


def _lang_of_path(path: str):
    """The census language of a (posix or windows) relpath, from its extension. Never raises."""
    try:
        s = str(path)
        dot = s.rfind(".")
        return _SOURCE_EXTS.get(s[dot:].lower()) if dot >= 0 else None
    except Exception:
        return None


def _rule_says(rule, part: str, parent) -> bool:
    """`rule(part, parent)`, or `rule(part)` for a one-argument rule (a caller's own predicate)."""
    try:
        return bool(rule(part, parent))
    except TypeError:
        return bool(rule(part))


def _walk_source(base: Path, rule, limit: int, problems: dict | None = None):
    """Yield (path, language) for census-source files under `base`, at most `limit` of them. Ignored
    directories (by `rule`, on names BELOW `base`) are pruned, never descended — a virtualenv or a
    node_modules tree costs nothing. Minified/bundle suffixes and macOS metadata (a `__MACOSX/` or `.AppleDouble/`
    directory, an AppleDouble `._*` twin: _is_macos_metadata) are skipped. `rule(part, parent)` gets the
    containing directory, as the engine's walker passes it. With `problems` ({"dirs": [], "files": []}),
    a directory that could not be listed because its path reached the Windows limit, and a source file
    whose path did, are recorded there instead of vanishing silently (they are never yielded — they can
    not be read). Never raises."""
    n = 0
    onerror = None
    if problems is not None:
        def onerror(e):  # noqa: E306
            name = os.path.abspath(str(getattr(e, "filename", "") or ""))
            if len(name) >= _MAX_DIR or getattr(e, "winerror", None) == 206:
                problems.setdefault("dirs", []).append(name)
    try:
        for dirpath, dirnames, filenames in os.walk(str(base), onerror=onerror):
            here = Path(dirpath)
            dirnames[:] = [d for d in dirnames
                           if not _is_macos_metadata(d, is_dir=True) and not _rule_says(rule, d, here)]
            for name in filenames:
                if n >= limit:
                    return
                dot = name.rfind(".")
                lang = _SOURCE_EXTS.get(name[dot:].lower()) if dot >= 0 else None
                if not lang or name.lower().endswith(_VENDOR_FILE_SUFFIXES) or _is_macos_metadata(name):
                    continue
                p = Path(dirpath) / name
                if problems is not None and len(os.path.abspath(str(p))) >= _MAX_PATH:
                    problems.setdefault("files", []).append(os.path.abspath(str(p)))
                    continue
                try:
                    if not p.is_file():
                        continue
                except OSError:
                    continue
                n += 1
                yield p, lang
    except OSError:
        return


def _walk_long(dirpath: str, rule, limit: int):
    """Census-source files below a directory the normal walk could not list (its path reached the Windows
    limit): walked through the extended-length `\\\\?\\` form, which ignores the limit, honouring the same
    ignore rule. Yields plain (unprefixed) path strings. Never raises."""
    bs = chr(92)
    prefix = bs + bs + "?" + bs
    n = 0
    try:
        start = os.path.abspath(dirpath)
        if start.startswith(bs + bs):                 # UNC: \\server\share -> \\?\UNC\server\share
            start = "UNC" + start[1:]
        for dp, dirnames, filenames in os.walk(prefix + start):
            plain = dp[len(prefix):]
            if plain.startswith("UNC" + bs):
                plain = bs + plain[3:]
            dirnames[:] = [d for d in dirnames
                           if not _is_macos_metadata(d, is_dir=True) and not _rule_says(rule, d, Path(plain))]
            for name in filenames:
                if n >= limit:
                    return
                dot = name.rfind(".")
                if dot < 0 or name[dot:].lower() not in _SOURCE_EXTS or name.lower().endswith(_VENDOR_FILE_SUFFIXES):
                    continue
                if _is_macos_metadata(name):
                    continue
                n += 1
                yield os.path.join(plain, name)
    except OSError:
        return


def _long_path_findings(root, rule, limit: int) -> dict:
    """{"files": N, "examples": [relpath, ...]} — census-source files under `root` whose path is past the
    Windows MAX_PATH limit on a machine that cannot read such paths (LongPathsEnabled=0). The engine and
    DeepMap's own walkers skip them silently. Uses what the last extension_census(root) walk recorded (no
    second walk), else walks once. {} on any other OS, with long paths enabled, or when nothing is past the
    limit. Never raises."""
    try:
        if os.name != "nt" or _long_paths_enabled():
            return {}
        probs = _CENSUS_PROBLEMS.pop(_root_key(root), None)
        if probs is None:
            probs = {"dirs": [], "files": []}
            for _ in _walk_source(Path(root), rule, limit, probs):
                pass
        files = list(probs.get("files") or [])
        for d in probs.get("dirs") or []:
            files.extend(_walk_long(d, rule, _LONG_PATH_WALK_LIMIT))
        if not files:
            return {}
        base = str(Path(root).resolve())
        rels = []
        for f in files[:3]:
            try:
                rels.append(os.path.relpath(f, base).replace("\\", "/"))
            except ValueError:
                rels.append(str(f))
        return {"files": len(files), "examples": rels}
    except Exception:
        return {}


def _count_source_files(d: Path, *, limit: int = 20000, rule=None) -> int:
    """Bounded count of census-source files under `d`, honoring the ignore rule. Never raises."""
    try:
        if not d.is_dir():
            return 0
    except OSError:
        return 0
    return sum(1 for _ in _walk_source(d, rule or _dir_ignore_rule(), limit))


def _test_path_predicate():
    """sync._is_test_path, THE test-code rule DEEPMAP_EXCLUDE_TESTS drops files by (a test directory component, a
    .NET test-project directory such as src/Shop.Tests/, or a test file name; never a substring, so src/Contest/
    and Shop.Testing.Abstractions/ are production). Imported lazily, and only while that switch is on: sync
    imports this module, and on the sync path it is already loaded. A mirror of the whole rule when sync cannot
    be imported (locked against sync by tests/test_rule_mirror_gates.py). Never raises."""
    try:
        here = str(Path(__file__).resolve().parent)
        if here not in sys.path:
            sys.path.insert(0, here)
        import sync  # noqa: E402 — the owner of the rule
        fn = getattr(sync, "_is_test_path", None)
        if callable(fn):
            return fn
    except Exception:  # noqa: BLE001
        pass

    def fallback(source_file) -> bool:
        parts = str(source_file or "").replace("\\", "/").split("/")
        low = parts[-1].lower()
        return (any(p.lower() in _TEST_DIR_PARTS or p.lower().endswith(_DOTNET_TEST_DIR_SUFFIXES) for p in parts[:-1])
                or (low.startswith("test_") and low.endswith(".py"))
                or low.endswith(("_test.py", "_test.go", "_test.rs", "_spec.rb"))
                or any(low.endswith(m + e) for m in (".test", ".spec")
                       for e in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"))
                or parts[-1].endswith(("Test.java", "Tests.java")))
    return fallback


def _without_test_files(files, is_test) -> list:
    """`files` (root-relative paths) minus the ones `is_test` names: sync drops those under DEEPMAP_EXCLUDE_TESTS on
    purpose, so a directory finding counts only what the graph was meant to hold. Never raises (a file the rule
    cannot judge is kept)."""
    kept = []
    for f in files or []:
        try:
            drop = bool(is_test(f))
        except Exception:  # noqa: BLE001
            drop = False
        if not drop:
            kept.append(f)
    return kept


def _skipped_source_dirs(root) -> dict:
    """graphify_extract.skipped_source_dirs(root) — {engine-skipped dir: [git-tracked source files]} — minus,
    when DEEPMAP_EXCLUDE_TESTS drops them on purpose, every file THE test rule names (a dir left with none is not
    listed). {} on any problem."""
    try:
        here = str(Path(__file__).resolve().parent)
        if here not in sys.path:
            sys.path.insert(0, here)
        import graphify_extract
        found = graphify_extract.skipped_source_dirs(root)
        if _exclude_tests() and found:
            is_test = _test_path_predicate()
            kept: dict = {}
            for d, fs in found.items():
                files = _without_test_files(fs, is_test)
                if files:
                    kept[d] = files
            found = kept
        return found
    except Exception:
        return {}


def build_coverage(root, nodes, *, census: dict | None = None, limit: int = 20000,
                   engine_keys=None) -> dict:
    """Cross-reference the EXTRACTED nodes against the ON-DISK census: per-language and
    per-conventional-dir {files, nodes} counts, so classify() can tell "plenty of nodes overall"
    from "a whole layer of the codebase is invisible". Bounded; never raises; returns
    {"languages": {lang: {"files": F, "nodes": N}}, "dirs": {dir: {"files": F, "nodes": N}}}, plus
    "skipped_dirs" (same shape, only when non-empty): directories the graphify engine skips by name
    (build/, out/, target/, ...) that hold qualifying git-TRACKED, hand-written source
    (graphify_extract.skipped_source_dirs — never the env/venv family or dist/, never a dir of only
    minified/bundled/generated files). The census cannot see those (it mirrors the skip), so this is the
    only layer that can tell that such code never reached the graph. Also "unparsed" (only when
    non-empty, see _unparsed_upgradeable): files the installed in-range graphify has no parser for
    although a later in-range release does (0.8.39: CUDA/Metal/.psm1).

    Round 7: a language's "nodes" are ENGINE nodes only (_is_engine_node — a recognizer's per-class shell
    in a file the engine never parsed is not extraction); the recognizer-only ones are reported beside
    them as "recognizer_nodes" (only when > 0). Which nodes are the engine's: `engine_keys` when given,
    else what sync._rebuild_code registered for this root (note_engine_nodes, consumed here), else nodes
    tagged `origin: "recognizer"` are not and every other node is. Conventional dirs keep counting every node: that check
    asks whether a layer is searchable at all, which the recognizers exist to make true. Two more blocks
    (only when non-empty): "unindexed_dirs" {dir: {"files", "nodes", "kind"}} — hand-written source the
    engine skips by directory name and DeepMap cannot re-include (graphify_extract.unindexed_source_dirs:
    tracked source under an env-family dir or dist/ that is not a virtualenv; any engine-skipped dir of
    a NON-git tree) — and "long_paths" {"files", "examples"} — source past the Windows MAX_PATH limit on
    a machine that cannot read it (_long_path_findings)."""
    try:
        census = census if census is not None else extension_census(root, limit=limit)
        skipped = _skipped_source_dirs(root)
        unindexed = _unindexed_source_dirs(root)
        rule = _dir_ignore_rule()
        engine_exts = _engine_exts()
        registered = _ENGINE_KEYS.pop(_root_key(root), None)
        if engine_keys is not None:
            engine_keys = frozenset(str(k) for k in engine_keys)
        else:
            engine_keys = registered
        lang_nodes: dict = {}
        rec_nodes: dict = {}
        ext_nodes: dict = {}
        dir_nodes: dict = {d: 0 for d in _CONVENTIONAL_DIRS}
        skip_nodes: dict = {d: 0 for d in skipped}
        unidx_nodes: dict = {d: 0 for d in unindexed}
        for n in (nodes or []):
            try:
                sf = str(n.get("source_file") or "").replace("\\", "/")
            except Exception:
                continue
            if not sf:
                continue
            engine = _is_engine_node(n, engine_exts, engine_keys)
            lang = _lang_of_path(sf)
            if lang:
                if engine:
                    lang_nodes[lang] = lang_nodes.get(lang, 0) + 1
                    ext = _ext_of(sf)
                    ext_nodes[ext] = ext_nodes.get(ext, 0) + 1
                else:
                    rec_nodes[lang] = rec_nodes.get(lang, 0) + 1
            low = sf.lower()
            while low.startswith("./"):          # NOT lstrip("./"): that ate the dot of .github/ etc.
                low = low[2:]
            for d in _CONVENTIONAL_DIRS:
                if low.startswith(d + "/"):
                    dir_nodes[d] += 1
            if not engine:
                continue
            for d in skipped:
                if low.startswith(d.lower() + "/"):
                    skip_nodes[d] += 1
            for d in unindexed:
                if low.startswith(d.lower() + "/"):
                    unidx_nodes[d] += 1
        languages = {}
        for lang, files in (census or {}).items():
            languages[lang] = {"files": int(files), "nodes": int(lang_nodes.get(lang, 0))}
            if rec_nodes.get(lang):
                languages[lang]["recognizer_nodes"] = int(rec_nodes[lang])
        dirs = {}
        for d in _CONVENTIONAL_DIRS:
            files = _count_source_files(Path(root) / Path(d), limit=limit, rule=rule)
            if files:
                dirs[d] = {"files": files, "nodes": dir_nodes[d]}
        out = {"languages": languages, "dirs": dirs}
        unparsed, in_skipped = _unparsed_upgradeable(root, census, rule, limit, ext_nodes, skipped)
        if unparsed:
            out["unparsed"] = unparsed
        # A re-included dir's files the engine has no PARSER for are named by the "unparsed" reason (with
        # the right cause and fix), not blamed on the re-include; a dir left with none is not listed.
        skipped_dirs = {d: {"files": len(fs) - in_skipped.get(d, 0), "nodes": skip_nodes[d]}
                        for d, fs in skipped.items()}
        skipped_dirs = {d: c for d, c in skipped_dirs.items() if c["files"] > 0}
        if skipped_dirs:
            out["skipped_dirs"] = skipped_dirs
        unindexed_dirs = {d: {"files": len(u["files"]), "nodes": unidx_nodes[d], "kind": u["kind"]}
                          for d, u in unindexed.items()
                          if u.get("files") and unidx_nodes[d] < len(u["files"]) * _DIR_DENSITY_FLOOR}
        if unindexed_dirs:
            out["unindexed_dirs"] = unindexed_dirs
        long_paths = _long_path_findings(root, rule, limit)
        if long_paths:
            out["long_paths"] = long_paths
        return out
    except Exception:
        return {"languages": {}, "dirs": {}}


def _unindexed_source_dirs(root) -> dict:
    """graphify_extract.unindexed_source_dirs(root) — {dir: {"files": [...], "kind": ...}} — minus, when
    DEEPMAP_EXCLUDE_TESTS drops them on purpose, every file THE test rule names (a dir left with none is not
    listed). {} on any problem."""
    try:
        gx = _extract_mod()
        fn = getattr(gx, "unindexed_source_dirs", None) if gx is not None else None
        found = fn(root) if callable(fn) else {}
        if _exclude_tests() and found:
            is_test = _test_path_predicate()
            kept: dict = {}
            for d, u in found.items():
                files = _without_test_files(u.get("files"), is_test)
                if files:
                    kept[d] = {**u, "files": files}
            found = kept
        return found or {}
    except Exception:
        return {}


def _exclude_tests() -> bool:
    return str(os.environ.get("DEEPMAP_EXCLUDE_TESTS", "")).strip().lower() in {"1", "true", "yes", "on"}


def _ext_of(path: str) -> str:
    """The lowercase suffix of a (posix or windows) path's last component ('' if none). Never raises."""
    try:
        name = str(path).replace("\\", "/").rsplit("/", 1)[-1]
        dot = name.rfind(".")
        return name[dot:].lower() if dot > 0 else ""
    except Exception:
        return ""


def _unparsed_upgradeable(root, census, rule, limit: int, ext_nodes: dict, skipped=None) -> tuple:
    """Files the installed, SUPPORTED graphify cannot parse although a later release inside the supported
    range can (graphify_extract.upgradeable_unparsed_exts — graphify 0.8.39 has no parser for .cu/.cuh/
    .metal/.psm1). Returns ({"version": v, "exts": {suffix: {"files": F, "since": "X.Y.Z"}}} or {},
    {engine-skipped dir: how many of its re-included tracked files are among them}).

    A fact about the engine, not a density heuristic — so it is reported whatever the count, and it can
    not be masked by a sibling suffix in the same census language (12 parsed .ps1 next to 2 unparsed
    .psm1 modules). A suffix that DID produce nodes is left out (the engine evidently parsed it). Counts
    the census walk (same ignore rule) plus the tracked files of the engine-skipped dirs `skipped`
    ({dir: [relpaths]}, which the census cannot see). Walks the tree only when the census holds a
    language one of those suffixes belongs to — never on the top of the range, where nothing is
    upgradeable. Never raises."""
    try:
        gx = _extract_mod()
        fn = getattr(gx, "upgradeable_unparsed_exts", None) if gx is not None else None
        up = fn() if callable(fn) else {}
        if not up:
            return {}, {}
        counts: dict = {}
        per_dir: dict = {}
        for d, files in (skipped or {}).items():
            for f in files or []:
                e = _ext_of(f)
                if e in up:
                    counts[e] = counts.get(e, 0) + 1
                    per_dir.setdefault(d, {})[e] = per_dir.get(d, {}).get(e, 0) + 1
        if any((census or {}).get(_SOURCE_EXTS.get(e)) for e in up):
            for p, _lang in _walk_source(Path(root), rule, limit):
                e = _ext_of(p.name)
                if e in up:
                    counts[e] = counts.get(e, 0) + 1
        exts = {e: {"files": n, "since": str(up[e])} for e, n in sorted(counts.items())
                if n and not ext_nodes.get(e)}
        if not exts:
            return {}, {}
        in_skipped = {d: sum(n for e, n in by_ext.items() if e in exts) for d, by_ext in per_dir.items()}
        return ({"version": str(gx.graphify_version() or ""), "exts": exts},
                {d: n for d, n in in_skipped.items() if n})
    except Exception:
        return {}, {}


def _coverage_reasons(coverage, *, engine_skips: bool = True) -> list:
    """DEGRADED reasons from a build_coverage() dict: tracked source the engine skipped by directory
    name (when `engine_skips` — i.e. graphify built the graph), dead conventional dirs (primary
    detector), then ~unextracted languages (coarse fallback). Malformed input -> []. Never raises."""
    out: list = []
    try:
        gx = _extract_mod() if (coverage.get("skipped_dirs") and engine_skips) else None
        rescue_on = bool(gx.rescue_skipped_dirs_enabled()) if gx is not None else True
        supported, version = (gx.graphify_support() if gx is not None and hasattr(gx, "graphify_support")
                              else (True, None))
        for d, c in sorted(((coverage.get("skipped_dirs") or {}) if engine_skips else {}).items()):
            files, dnodes = int(c.get("files", 0)), int(c.get("nodes", 0))
            if files and dnodes < files * _DIR_DENSITY_FLOOR:
                name = d.rsplit("/", 1)[-1]
                if rescue_on and not supported:
                    # E3: DeepMap skipped the re-include on purpose — say why and how to get it back.
                    why = (f"the extraction engine skips directories named '{name}', and DeepMap did not "
                           f"re-include its tracked source because {gx.unsupported_engine_hint(version)}")
                elif rescue_on:
                    why = (f"the extraction engine skips directories named '{name}' and DeepMap's "
                           f"re-include of its tracked source did not bring it in (a .graphifyignore rule "
                           f"or a failed engine run can keep it out) — re-run /deep-map:refresh and read "
                           f"its '[deepmap] re-including' and graphify lines")
                else:
                    why = (f"the extraction engine skips directories named '{name}' and re-including "
                           f"them is switched off (DEEPMAP_RESCUE_SKIPPED_DIRS=0) — unset that variable "
                           f"and re-run /deep-map:refresh to index it")
                out.append(f"LOW COVERAGE in {d}/ — {files} git-tracked source file(s) but only {dnodes} "
                           f"extracted node(s); {why}. Until then that code is invisible to "
                           f"deepmap_search/deepmap_impact.")
        for d, c in sorted(((coverage.get("unindexed_dirs") or {}) if engine_skips else {}).items()):
            out.append(_unindexed_reason(d, int(c.get("files", 0)), str(c.get("kind") or "")))
        for d, c in sorted((coverage.get("dirs") or {}).items()):
            files, dnodes = int(c.get("files", 0)), int(c.get("nodes", 0))
            if files >= _DIR_MIN_FILES and dnodes < files * _DIR_DENSITY_FLOOR:
                out.append(
                    f"LOW COVERAGE in {d}/ — {files} source files but only {dnodes} extracted "
                    f"node(s): that layer is largely invisible to deepmap_search/deepmap_impact "
                    f"(declarative/DSL-heavy code is not captured by the generic extractor).")
        unparsed = _unparsed_by_language(coverage.get("unparsed") if engine_skips else None)
        if unparsed:
            # ONE reason for every such language (one remedy, one hook-visible head): the biggest first.
            order = sorted(unparsed, key=lambda k: (-unparsed[k]["files"], k))
            parts = ", ".join(f"{unparsed[k]['files']} {'/'.join(unparsed[k]['exts'])}" for k in order)
            since = max((unparsed[k]["since"] for k in order), key=_release)
            version = unparsed[order[0]]["version"] or "(unknown version)"
            out.append(
                f"LOW COVERAGE for {', '.join(order)} — {parts} file(s) on disk are not parsed by the "
                f"installed graphify {version}; graphify {since} and later parse them, inside the range "
                f"DeepMap supports ({_graphify_spec()}) — upgrade DeepMap's engine: "
                f"{_graphify_install_command()}, then re-run /deep-map:refresh. Until then that code is "
                f"invisible to deepmap_search/deepmap_impact.")
        excl_tests = _exclude_tests()
        for lang, c in sorted((coverage.get("languages") or {}).items()):
            files, lnodes = int(c.get("files", 0)), int(c.get("nodes", 0))
            # Files already named above as unparseable by this engine are not ALSO a density problem.
            files -= int((unparsed.get(lang) or {}).get("files", 0))
            rec = int(c.get("recognizer_nodes", 0) or 0)
            rec_note = (f" — its {rec} other node(s) are DeepMap's per-language recognizer shells (one per "
                        f"class/model, no functions or methods), which do not count as extraction"
                        if rec else "")
            if files >= _LANG_MIN_FILES and lnodes < max(1, files * _LANG_DENSITY_FLOOR):
                out.append(
                    f"LOW COVERAGE for {lang} — {files} files on disk but only {lnodes} extracted "
                    f"node(s); {lang} code is effectively invisible to search/impact{rec_note} (check the "
                    f"extractor lines in the sync output — /deep-map:doctor shows the engine the last "
                    f"sync used).")
            elif files >= _LANG_ZERO_MIN_FILES and lnodes == 0 and not excl_tests:
                # T8d-B1: a small layer is still a layer — 5 C# files the engine produced nothing for used
                # to pass as READY because the rule above waits for 10 files. (Skipped under
                # DEEPMAP_EXCLUDE_TESTS, whose dropped test files the census cannot tell apart.)
                out.append(
                    f"LOW COVERAGE for {lang} — {files} file(s) on disk but 0 extracted by the engine; "
                    f"{lang} code is invisible to search/impact{rec_note} (check the extractor lines in "
                    f"the sync output — /deep-map:doctor shows the engine the last sync used).")
        lp = coverage.get("long_paths") or {}
        if int(lp.get("files") or 0):
            ex = str((lp.get("examples") or [""])[0])[:160]
            out.append(
                f"LOW COVERAGE — {int(lp['files'])} source file(s) have a full path over the Windows "
                f"260-character MAX_PATH limit{f' (e.g. {ex})' if ex else ''}; with LongPathsEnabled=0 "
                f"neither DeepMap nor the extraction engine can read them, so they are missing from the "
                f"graph — enable long paths (LongPathsEnabled=1 under "
                f"HKLM\\SYSTEM\\CurrentControlSet\\Control\\FileSystem, then reboot) or move the project to "
                f"a shorter path, then re-run /deep-map:refresh.")
    except Exception:
        return []
    return out


# A rename that takes a directory out of the engine's skip list (checked against graphify 0.8.39's rule by
# tests/verdict_r7_gates.py) — offered only as an example.
_RENAME_HINT = {"env": "environment", ".env": "environment", "venv": "venv_src", ".venv": "venv_src",
                "dist": "distribution", "dist-protected": "distribution"}


def _rename_suggestion(name: str) -> str:
    low = name.lower()
    if low in _RENAME_HINT:
        return _RENAME_HINT[low]
    if low.endswith("_venv"):
        return name[:-5] + "_virtualenv"
    if low.endswith("_env"):
        return name[:-4] + "_environment"
    return name + "_src"


def _unindexed_reason(d: str, files: int, kind: str) -> str:
    """The DEGRADED reason for hand-written source in a directory the engine skips and DeepMap does not
    re-include (graphify_extract.unindexed_source_dirs)."""
    name = d.rsplit("/", 1)[-1]
    low = name.lower()
    env_family = low in ("env", ".env", "venv", ".venv") or low.endswith(("_env", "_venv"))
    dist = low in ("dist", "dist-protected")
    if kind == "non-git" and not (env_family or dist):
        return (f"LOW COVERAGE in {d}/ — {files} hand-written source file(s) in a directory the extraction "
                f"engine skips by name ('{name}'); this project is not a git repository, so DeepMap cannot "
                f"tell your own source from build output there and does not re-include it. That code is "
                f"invisible to deepmap_search/deepmap_impact — if it is your source, put the project under "
                f"git and commit it (git-tracked source in engine-skipped directories is re-included "
                f"automatically), then re-run /deep-map:refresh.")
    what = "a virtualenv" if env_family else "build output"
    tracked = "git-tracked " if kind == "never-rescued" else ""
    return (f"LOW COVERAGE in {d}/ — {files} {tracked}hand-written source file(s) in a directory named "
            f"'{name}', which the extraction engine always skips and DeepMap does not re-include (that name "
            f"usually holds {what}); that code is invisible to deepmap_search/deepmap_impact. If it is your "
            f"own source, rename the directory (e.g. '{name}' -> '{_rename_suggestion(name)}') and re-run "
            f"/deep-map:refresh.")


def _unparsed_by_language(unparsed) -> dict:
    """build_coverage's "unparsed" block grouped by census language: {lang: {"files", "exts", "since",
    "version"}} ("since" = the newest release among the language's suffixes, i.e. the one that parses
    all of them). {} for anything malformed. Never raises."""
    out: dict = {}
    try:
        if not isinstance(unparsed, dict):
            return {}
        version = str(unparsed.get("version") or "")
        for ext, c in sorted((unparsed.get("exts") or {}).items()):
            lang = _SOURCE_EXTS.get(str(ext).lower())
            files = int((c or {}).get("files", 0))
            if not lang or files <= 0:
                continue
            u = out.setdefault(lang, {"files": 0, "exts": [], "since": "", "version": version})
            u["files"] += files
            u["exts"].append(str(ext).lower())
            since = str((c or {}).get("since") or "")
            if _release(since) > _release(u["since"]):
                u["since"] = since
    except Exception:
        return {}
    return out


def _release(text) -> tuple:
    try:
        return tuple(int(x) for x in str(text).split(".") if x.isdigit())
    except Exception:
        return ()


def _hint_python() -> str:
    """DeepMap's interpreter for a pasteable command — QUOTED, forward slashes (graphify_extract.hint_python:
    Git Bash eats the backslashes of an unquoted Windows path). Never raises."""
    gx = _extract_mod()
    fn = getattr(gx, "hint_python", None) if gx is not None else None
    if callable(fn):
        try:
            return fn()
        except Exception:
            pass
    try:
        return f'"{Path(sys.executable).as_posix()}"' if sys.executable else "python"
    except Exception:
        return "python"


def _graphify_spec() -> str:
    gx = _extract_mod()
    return str(getattr(gx, "SUPPORTED_GRAPHIFY_SPEC", "") or "graphifyy") if gx is not None else "graphifyy"


def _graphify_install_command() -> str:
    """The runnable, pinned install/upgrade of the extraction engine into DeepMap's Python. Never raises."""
    gx = _extract_mod()
    fn = getattr(gx, "graphify_install_command", None) if gx is not None else None
    if callable(fn):
        try:
            return fn()
        except Exception:
            pass
    return f'{_hint_python()} -m pip install --upgrade "{_graphify_spec()}"'


def format_coverage(coverage) -> list:
    """Human lines for doctor/status: one per census language ('Ruby: 320 files, 4 nodes'), then one
    per tracked conventional dir. Never raises; malformed input -> []."""
    lines: list = []
    try:
        for lang, c in sorted((coverage.get("languages") or {}).items()):
            rec = int(c.get("recognizer_nodes", 0) or 0)
            lines.append(f"{lang}: {int(c.get('files', 0))} files, {int(c.get('nodes', 0))} nodes"
                         + (f" (+{rec} recognizer-only)" if rec else ""))
        for d, c in sorted((coverage.get("dirs") or {}).items()):
            lines.append(f"{d}/: {int(c.get('files', 0))} files, {int(c.get('nodes', 0))} nodes")
        for d, c in sorted((coverage.get("skipped_dirs") or {}).items()):
            lines.append(f"{d}/: {int(c.get('files', 0))} tracked files in an engine-skipped dir, "
                         f"{int(c.get('nodes', 0))} nodes")
        unparsed = coverage.get("unparsed") if isinstance(coverage.get("unparsed"), dict) else {}
        for ext, c in sorted((unparsed.get("exts") or {}).items()):
            lines.append(f"{ext}: {int(c.get('files', 0))} files the installed graphify "
                         f"{unparsed.get('version') or '?'} cannot parse (graphify {c.get('since')}+ can)")
        for d, c in sorted((coverage.get("unindexed_dirs") or {}).items()):
            lines.append(f"{d}/: {int(c.get('files', 0))} source files in a directory the engine skips and "
                         f"DeepMap does not re-include ({c.get('kind')}), {int(c.get('nodes', 0))} nodes")
        lp = coverage.get("long_paths") or {}
        if int(lp.get("files") or 0):
            lines.append(f"{int(lp['files'])} source file(s) past the Windows 260-char path limit "
                         f"(unreadable with LongPathsEnabled=0)")
    except Exception:
        return []
    return lines


def ml_stack_ok() -> bool:
    """True iff the embedding stack (torch + sentence_transformers) is importable. Import-spec probe
    only — never loads torch or the model, so it is fast and side-effect-free."""
    try:
        return (importlib.util.find_spec("torch") is not None
                and importlib.util.find_spec("sentence_transformers") is not None)
    except Exception:
        return False


def extension_census(root, *, limit: int = 20000) -> dict:
    """Count source files by human language under `root`, honoring the ignore set. Bounded (`limit`
    files scanned) so a huge tree can't stall setup. Returns {language: count}. Never raises."""
    counts: dict = {}
    try:
        # Windows with LongPathsEnabled=0 only: note what the walk cannot read, for build_coverage.
        probs = {"dirs": [], "files": []} if (os.name == "nt" and not _long_paths_enabled()) else None
        for _p, lang in _walk_source(Path(root), _dir_ignore_rule(), limit, probs):
            counts[lang] = counts.get(lang, 0) + 1
        if probs is not None:
            if len(_CENSUS_PROBLEMS) > 32:                 # never grows without bound in a long process
                _CENSUS_PROBLEMS.clear()
            _CENSUS_PROBLEMS[_root_key(root)] = probs
    except Exception:
        pass
    return counts


def _top(langs: dict, n: int) -> str:
    return ", ".join(f"{v} {k}" for k, v in sorted(langs.items(), key=lambda kv: -kv[1])[:n])


def _fallback_cause(extraction: dict) -> str:
    """'graphify failed (<cause>)' + a MAX_PATH remedy when that is what it looked like."""
    cause = str(extraction.get("fallback_reason") or "").strip()
    text = ("the graphify multi-language engine is not installed in DeepMap's Python" if _engine_missing(cause)
            else f"graphify failed ({cause[:300]})" if cause
            else "the graphify multi-language engine was unavailable or produced no code nodes")
    if extraction.get("maxpath"):
        text += (" — a path exceeded the Windows 260-char MAX_PATH limit: enable long paths "
                 "(LongPathsEnabled=1) or use a shorter project / DEEPMAP_HOME path")
    return text


def _engine_missing(text) -> bool:
    """True when an extraction cause says the graphify engine is not installed (graphify_extract's
    'graphify (the multi-language engine) is not installed'). Never raises."""
    try:
        low = str(text or "").lower()
        return "graphify" in low and "not installed" in low
    except Exception:
        return False


def _fallback_remedy(extraction: dict) -> str:
    cause = extraction.get("fallback_reason")
    if extraction.get("timed_out") or "DEEPMAP_GRAPHIFY_TIMEOUT" in str(cause or ""):
        # Round 11 (M1): the engine ran out of its time budget (a large repo / a loaded machine) — it is not broken.
        return ("graphify ran out of time, it is not broken: re-run /deep-map:refresh with a larger "
                "DEEPMAP_GRAPHIFY_TIMEOUT (seconds, e.g. twice the budget above; 60-86400), ideally when the "
                "machine is less busy.")
    if _engine_missing(cause):
        # Runnable as written (D6): the pinned install into the interpreter DeepMap itself runs under.
        return (f"Install it into DeepMap's Python: {_graphify_install_command()} — then re-run "
                f"/deep-map:refresh.")
    return ("Fix the graphify error shown in the sync output and re-run /deep-map:refresh."
            if cause else
            f"Check the extractor line in /deep-map:doctor; if graphify is not installed, install it into "
            f"DeepMap's Python: {_graphify_install_command()} — then re-run /deep-map:refresh.")


def classify(*, neo4j_ok: bool, n_nodes: int, n_embedded: int, census: dict | None = None,
             extractor: str = "ast", ml_ok: bool | None = None,
             mcp_ok: bool | None = None, coverage: dict | None = None,
             extraction: dict | None = None) -> tuple:
    """Classify a setup/sync outcome into (verdict, reasons). Pure (reads only DEEPMAP_EXTRACTOR, and
    only when `extraction` does not say which mode was requested).

    - neo4j_ok       : could we reach Neo4j at all (a hard prerequisite).
    - n_nodes        : nodes the sync pushed.
    - n_embedded     : nodes with embeddings (0 => FTS-only / vector search disabled).
    - census         : extension_census() output (may be {} / None if not computed).
    - extractor      : the RESOLVED engine sync used ("graphify" | "ast"; legacy callers pass the mode).
    - ml_ok / mcp_ok : optional observations; None means "not checked" (omitted from reasoning).
    - coverage       : optional build_coverage() output; a dead conventional dir (app/models/ with
                       files but ~no nodes), a ~unextracted language or (on the graphify engine) an
                       engine-skipped dir whose tracked source never arrived DEGRADES with a named
                       reason — plenty of nodes overall must not mask a blind layer.
    - extraction     : optional sync._rebuild_code `.info` — {"requested", "fallback_reason",
                       "engine_error", "maxpath", "failed_files", "failed_examples"}. Lets the verdict
                       say "fell back automatically" (with graphify's cause) instead of blaming a
                       "forced" ast the user never set, name the engine error of a failed forced-
                       graphify run, and DEGRADE a run where graphify lost many files (G4).

    Returns ("READY"|"DEGRADED"|"FAILED", [human reason strings]).
    """
    if not neo4j_ok:
        return FAILED, ["Neo4j is not reachable — start it (/deep-map:setup) or check NEO4J_URI/auth."]

    ext = extraction if isinstance(extraction, dict) else {}
    requested = str(ext.get("requested") or os.environ.get("DEEPMAP_EXTRACTOR") or "auto").strip().lower()
    forced_ast = extractor == "ast" and requested == "ast"
    fell_back = extractor == "ast" and not forced_ast

    langs = census or {}
    py = langs.get("Python", 0)
    other = {k: v for k, v in langs.items() if k != "Python"}

    if n_nodes == 0:
        if not langs:
            return FAILED, ["0 code nodes: no supported source files were found under the target "
                            "directory — is this the right path?"]
        if forced_ast and py == 0 and other:
            return FAILED, [
                f"0 code nodes: the forced 'ast' extractor (DEEPMAP_EXTRACTOR=ast) is Python-only, but "
                f"the target has no .py files (found {_top(other, 4)}). Unset DEEPMAP_EXTRACTOR — the "
                f"default 'auto' uses the graphify multi-language engine — or point setup at a Python "
                f"project."]
        if fell_back and py == 0 and other:
            return FAILED, [
                f"0 code nodes: {_fallback_cause(ext)}, and DeepMap fell back automatically to its "
                f"Python-only 'ast' extractor, but the target has no .py files (found "
                f"{_top(other, 4)}). {_fallback_remedy(ext)}"]
        if extractor == "graphify" and ext.get("engine_error"):
            # DEEPMAP_EXTRACTOR=graphify: no fallback by design, so the engine's own error IS the reason.
            err = str(ext.get("engine_error")).strip()[:300]
            hint = ("; a path exceeded the Windows 260-char MAX_PATH limit: enable long paths "
                    "(LongPathsEnabled=1) or use a shorter project / DEEPMAP_HOME path"
                    if ext.get("maxpath") else "")
            fix = (f"Install it into DeepMap's Python ({_graphify_install_command()}) or unset "
                   f"DEEPMAP_EXTRACTOR so the default 'auto' can fall back to the Python-only extractor"
                   if _engine_missing(err) else
                   "Fix that error (or unset DEEPMAP_EXTRACTOR so the default 'auto' can fall back to the "
                   "Python-only extractor)")
            return FAILED, [
                f"0 code nodes: the graphify engine failed ({err}){hint}, and DEEPMAP_EXTRACTOR=graphify "
                f"allows no fallback. {fix} and re-run /deep-map:refresh."]
        cause = f" — {_fallback_cause(ext)}" if (fell_back and ext.get("fallback_reason")) else ""
        return FAILED, [f"0 code nodes extracted (extractor={extractor}){cause} — nothing to search."]

    reasons = []
    if n_embedded == 0:
        if ml_ok is False:
            # Runnable as written (D6): /deep-map:setup when this Python lacks the packages (it installs them
            # into DeepMap's own environment), else the exact commands for the interpreter DeepMap itself runs
            # under — a bare "run the installer" pointed at a script inside the plugin cache. ALWAYS
            # quoted with forward slashes: Git Bash eats an unquoted path's backslashes.
            py = _hint_python()
            why = "the embedding model / ML stack is not installed"
            setup = _setup_remedy("torch", "sentence-transformers", "transformers")
            fix = (f"{setup[:1].upper()}{setup[1:]} to enable it." if setup else
                   f"Install it into DeepMap's Python — {py} -m pip install --index-url "
                   f"https://download.pytorch.org/whl/cpu torch && {py} -m pip install "
                   f"sentence-transformers transformers — then re-run /deep-map:setup to enable it.")
        else:
            why = "no embeddings were generated"
            fix = ("Re-run /deep-map:refresh (the first run downloads the embedding model and needs "
                   "network access once); /deep-map:doctor shows why the model did not load.")
        reasons.append(
            f"VECTOR SEARCH DISABLED — {why}; search will run keyword/FTS-only (DeepMap's "
            f"intent-summary semantic search is off). {fix}")
    if mcp_ok is False:
        reasons.append("MCP servers are not up — the in-editor deepmap tools won't connect until "
                       "they start (re-run /deep-map:setup).")
    if forced_ast and py and other and sum(other.values()) > py:
        reasons.append(
            f"the target is mostly non-Python ({_top(other, 3)}) but the forced 'ast' extractor indexed "
            f"only its {py} Python file(s) — unset DEEPMAP_EXTRACTOR for graphify multi-language coverage.")
    elif fell_back and other:
        # Any non-Python source is missing after an automatic fallback — say so, with the real cause.
        reasons.append(
            f"{_fallback_cause(ext)}, so DeepMap fell back automatically to the Python-only 'ast' "
            f"extractor: {sum(other.values())} non-Python source file(s) ({_top(other, 3)}) are NOT in "
            f"the graph. {_fallback_remedy(ext)}")
    failed = int(ext.get("failed_files") or 0) if extractor == "graphify" else 0
    if failed and (failed >= _FAILED_FILES_MIN or failed >= sum(langs.values()) * _FAILED_FILES_SHARE):
        example = (ext.get("failed_examples") or [""])[0]
        reasons.append(
            f"graphify could not extract {failed} file(s) — they are missing from the graph"
            f"{f' (e.g. {str(example)[:200]})' if example else ''}"
            f"{'; a path exceeded the Windows 260-char MAX_PATH limit (enable LongPathsEnabled=1 or use a shorter path)' if ext.get('maxpath') else ''}"
            f". Re-run /deep-map:refresh and read the graphify warnings.")
    if coverage:
        reasons.extend(_coverage_reasons(coverage, engine_skips=(extractor == "graphify")))

    return (DEGRADED if reasons else READY), reasons


def format_block(verdict: str, reasons, *, nodes=None, edges=None, embedded=None) -> str:
    """A single human-readable verdict block for setup / doctor output."""
    lines = [f"[deepmap] verdict: {verdict}"]
    if nodes is not None:
        lines.append(f"          graph: {nodes} nodes, "
                     f"{edges if edges is not None else '?'} CALLS edges, "
                     f"{embedded if embedded is not None else '?'} embedded")
    for r in (reasons or []):
        lines.append(f"          - {r}")
    return "\n".join(lines)


# What exit_code(DEGRADED, strict=True) returns. This code is TAKEN: `deepmap sync` exits 3 for "Neo4j
# unreachable" (build/deepmap_cli.py _SYNC_EXIT_CODES), so a strict result must never become a command's
# exit status as it is (ISS-build:026; tests/o6_verdict_contract_r283_gates.py keeps exit_code unwired).
STRICT_DEGRADED_RC = 3


def exit_code(verdict: str, *, strict: bool = False) -> int:
    """UNWIRED helper: no DeepMap command calls it, and there is NO `--strict` flag. Each command owns
    its exit contract (module docstring). It maps a verdict the way `deepmap sync` (deepmap_cli
    cmd_sync) and `provision.py setup` already treat one: FAILED -> 1; DEGRADED -> 0 (search still
    works, just degraded — non-breaking for CI / `set -e` / a git hook's `|| true`); READY -> 0.

    `strict=True` maps DEGRADED to STRICT_DEGRADED_RC (3). It is kept only for existing callers of
    this function (the verdict gates). That 3 already means "Neo4j unreachable" to `deepmap sync`, so a
    strict mode would need a code no command uses before any command could return it."""
    if verdict == FAILED:
        return 1
    if verdict == DEGRADED:
        return STRICT_DEGRADED_RC if strict else 0
    return 0
