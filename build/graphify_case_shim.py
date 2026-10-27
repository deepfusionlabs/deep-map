#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run `graphify update --no-cluster` with CASE-INSENSITIVE extension dispatch and with engine-skipped
directories that hold git-tracked source re-included.

Second job (2026-09 live validation, G3): graphify's `detect()` prunes directories by NAME at any depth
(build, dist, target, out, env, *_env, snapshots, ...) before any include rule, so DeepMap's own build/
— all of its product code — never reached the graph. The parent (graphify_extract.extract) lists the
QUALIFYING source under such dirs — git-tracked, not gitignored, hand-written, never under the
virtualenv/env family or dist/ (graphify_extract.skipped_source_dirs, round-2 decision D5) — and hands
the exact root-relative dir paths down in `DEEPMAP_RESCUE_DIRS`; `rescue_skipped_dirs` wraps
`detect._is_noise_dir` so those paths (and only those) are walked. The same name elsewhere in the tree
stays skipped. The engine walks a re-included dir whole, so the parent drops whatever it finds there that
did not qualify; this shim only widens the walk. The parent asks for a re-include only when the installed
graphify is inside the range it was validated on (graphify_extract.SUPPORTED_GRAPHIFY_SPEC — graphify 0.9
decides several of those directories from their contents instead of their names); outside it the parent
sets no DEEPMAP_RESCUE_DIRS and warns, so this shim never narrows a rule it was not built for.

Why this exists (2026-08 Delphi field report). The upstream engine's extractor table is keyed by the
EXACT suffix — `_DISPATCH.get(path.suffix)` — and its file discovery filters on
`p.suffix in set(_DISPATCH)`. Both are case-SENSITIVE, so a legacy Windows tree carrying `UNIT1.PAS` /
`MAIN.DPR` (the classic Delphi casing) is skipped entirely: the engine yields ZERO nodes and DeepMap
can only report the emptiness honestly. This shim aliases each such suffix onto the extractor already
registered for its lowercase form, then delegates to graphify's own `main()`.

MULTIPROCESSING IS THE WHOLE DESIGN CONSTRAINT — read this before touching the module layout.
graphify hands per-file extraction to a `ProcessPoolExecutor` as soon as a run has
`_PARALLEL_THRESHOLD` (20) uncached files, and the worker resolves `_DISPATCH.get(path.suffix)` INSIDE
the child process. Under the `spawn` start method (Windows always, macOS by default) that child
re-imports `graphify.extract` fresh, so an alias applied only in the parent is GONE in the worker: the
files are still discovered (the parent's patched table drives discovery, and `detect` lowercases), then
extracted to nothing. The measured cliff is exact and brutal — 19 uppercase files extract fine, 20
extract to ZERO — i.e. the naive parent-only patch works on a toy sample and silently no-ops on every
real legacy codebase, which is precisely the repo class this exists for.

So the alias is applied at MODULE IMPORT time from `DEEPMAP_CASE_ALIASES`, not only inside `main()`.
A spawn worker re-imports the parent's `__main__` module (this file) as `__mp_main__` and inherits the
environment, so the module-level block re-applies the alias in every child. `main()` sets that variable
before delegating. Both halves are required: the env var alone patches nothing, the import-time block
alone has nothing to read.

Third job (round 2 of the macOS fix): graphify's `detect()` has no dot-file rule, so it lists macOS metadata as
code — an AppleDouble twin (a small BINARY `._<name>` beside every file on an exFAT / SMB volume), everything under
a macOS archive's `__MACOSX/` mirror, Netatalk's `.AppleDouble/<name>` twins — and parses it. A twin gets the SAME
engine id as its original (`pkg/._a.py` and `pkg/a.py` are both `pkg_a_py_pkg_a`) and the last in sort order wins,
so a file whose name sorts before `._` lost its own :Module (measured, 0.8.39: `routes/+page.ts` came back as Module
`._+page`). The parent pre-scans the tree (graphify_extract.macos_metadata_present) and, only when it finds some,
sets DEEPMAP_SKIP_MACOS_METADATA=1; `skip_macos_metadata` then wraps `detect.detect` so its file lists never hold a
metadata path. The engine's discovery runs in this (the parent) process, but the wrap is applied at import time like
the other two, so whichever process calls detect() has it. sync drops any metadata node that still gets through.
detect() is not the engine's only walk (round 3): while it EXTRACTS, graphify lists the tree itself — its
Pascal/Delphi resolvers count `*.pas` / `*.dpr` children with Path.glob to pick a project root (a `._Common.pas`
twin made a directory holding one .pas file a root) and map unit / class names to files with Path.rglob
(`.AppleDouble/Unit1.pas` has Unit1.pas's stem and is listed after it, so every `uses Unit1` pointed at a file the
graph does not hold); pnpm workspace globs and 0.8.51's XAML pass list it the same way. Those run inside extract(),
so in the pool workers at 20+ files. Every such walk is a pathlib listing (tests/macos_appledouble_gates.py section
J inventories every directory listing in the installed engine), so `hide_macos_metadata_listings` wraps
Path.glob / rglob / iterdir in the engine process — at import time, under the same variable — and the engine's own
listings never yield metadata either.

Safety properties (this must never be worse than not running):
  * The PARENT (build/graphify_extract.extract) decides. It scans the tree once for suffixes that
    differ from their lowercase form but whose lowercase form IS a DeepMap source extension, and only
    when it finds some does it invoke this shim. A repo with no odd-case source files (nor engine-skipped
    tracked source, nor macOS metadata) runs the byte-identical `python -m graphify update` command, with the
    byte-identical environment, it always did — nothing to regress.
  * NO scanning and NO renaming of the user's files happens here; only the lookup key set is widened (and the
    discovered file list narrowed). Extraction, output layout and graph.json remain the engine's, unchanged.
  * `_DISPATCH` is a private upstream symbol, so every access is defensive: a missing/renamed/empty
    table, an unimportable module, or any exception aliasing a key degrades to "alias nothing" and the
    run proceeds exactly as an unpatched graphify would. The worst case is the pre-existing
    "uppercase files are skipped" behaviour, which the setup verdict already reports honestly.

Invoked as:  python build/graphify_case_shim.py <root> [<suffix> ...]
             (+ DEEPMAP_RESCUE_DIRS='{"root": ..., "dirs": [...]}' and/or DEEPMAP_SKIP_MACOS_METADATA=1 in the
             environment)
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

#: Carries the alias list into ProcessPoolExecutor workers (see the multiprocessing note above).
ENV_ALIASES = "DEEPMAP_CASE_ALIASES"
#: The engine-skipped dirs to walk anyway, as JSON {"root", "dirs"} (or {"root", "names"}), set by the
#: parent. Read at import time too, like the aliases, so every process that runs detect() sees it.
ENV_RESCUE = "DEEPMAP_RESCUE_DIRS"
#: "1" when the parent's pre-scan found macOS metadata in the tree (graphify_extract.macos_metadata_present):
#: detect() then leaves it out of the engine's file lists. Read at import time too.
ENV_SKIP_METADATA = "DEEPMAP_SKIP_MACOS_METADATA"

# Mirror of build/framework_edges.is_macos_metadata (this file imports nothing of DeepMap's; drift-locked with the
# other copies by tests/macos_appledouble_gates.py).
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


def alias_suffixes(suffixes) -> dict:
    """Alias each odd-case `suffix` onto the extractor registered for its lowercase form.

    Returns {alias_suffix: source_key} for what was actually aliased ({} if the engine's table is
    unavailable or nothing matched). Never raises — an unpatchable engine simply gets no aliases.
    """
    try:
        import graphify.extract as engine
    except Exception:
        return {}
    table = getattr(engine, "_DISPATCH", None)
    if not isinstance(table, dict) or not table:
        return {}
    # Resolve case-insensitively against the table's OWN keys, so an engine that itself registers a
    # mixed-case key (graphify registers Fortran's `.F90` alongside `.f90`) is handled too.
    by_lower = {}
    for key in list(table):
        if isinstance(key, str):
            by_lower.setdefault(key.lower(), key)
    aliased: dict = {}
    for suffix in suffixes:
        try:
            if not suffix or not isinstance(suffix, str) or suffix in table:
                continue
            source_key = by_lower.get(suffix.lower())
            if source_key is None:
                continue                       # the engine cannot parse this language at all
            table[suffix] = table[source_key]
            aliased[suffix] = source_key
        except Exception:
            continue                           # one bad key must not cost the whole run
    return aliased


def _aliases_from_env() -> list:
    """The suffix list handed down through the environment. Never raises."""
    try:
        return [s for s in str(os.environ.get(ENV_ALIASES, "")).split(",") if s.strip()]
    except Exception:
        return []


def rescue_skipped_dirs(root, dirs=(), names=()) -> list:
    """Stop the engine's `detect()` from pruning `dirs` (root-relative posix paths) — or, in the
    degraded form, any directory called one of `names`.

    Wraps `graphify.detect._is_noise_dir(part, parent)`: detect() calls it for every subdirectory with
    the parent path, so the wrapper can answer "not noise" for exactly the rescued paths and defer to
    the engine for everything else (the same name elsewhere stays skipped; a caller that passes no
    parent gets the engine's answer). Idempotent — re-applying rewraps the engine's original, never
    the wrapper. Returns what it rescued; [] (and no patch) when the engine symbol is missing.
    Never raises."""
    try:
        import graphify.detect as detect
        original = getattr(detect, "_is_noise_dir", None)
        original = getattr(original, "__wrapped__", original)
        if not callable(original):
            return []
        base = os.path.normcase(str(Path(root).resolve()))
        keep = {os.path.normcase(str(d).replace("/", os.sep)) for d in dirs if d}
        keep_names = {str(n) for n in names if n}
        if not keep and not keep_names:
            return []

        def _is_noise_dir(part, parent=None):
            noisy = original(part, parent)
            if noisy and part in keep_names:
                return False
            if noisy and keep and parent is not None:
                try:
                    rel = os.path.normcase(os.path.relpath(os.path.join(str(parent), part), base))
                    if rel in keep:
                        return False
                except ValueError:             # another drive on Windows: not under the root
                    pass
            return noisy

        _is_noise_dir.__wrapped__ = original
        detect._is_noise_dir = _is_noise_dir
        return sorted(set(dirs) | set(names))
    except Exception:
        return []


def _rescue_from_env():
    """(root, dirs, names) handed down through the environment, or None. Never raises."""
    try:
        raw = os.environ.get(ENV_RESCUE, "")
        if not raw.strip():
            return None
        data = json.loads(raw)
        return str(data["root"]), list(data.get("dirs") or []), list(data.get("names") or [])
    except Exception:
        return None


def _metadata_listing(path, base: str) -> bool:
    """Is `path`, as detect() lists it (under the resolved scan root `base`), macOS metadata — judged on the part
    below the root, so a project that itself lives under a `__MACOSX/` keeps its files? A path outside the root
    (another drive, a symlinked tree) is judged by its name alone. Never raises (False)."""
    try:
        text = os.fspath(path)
        if not isinstance(text, str):
            return False
        try:
            rel = os.path.relpath(text, base)
        except ValueError:                     # another drive on Windows
            rel = None
        if rel is None or rel == os.pardir or rel.startswith(os.pardir + os.sep):
            return _is_macos_metadata(os.path.basename(text))
        return _is_macos_metadata(rel)
    except Exception:
        return False


def skip_macos_metadata() -> bool:
    """Make the engine's file discovery, `graphify.detect.detect(root, ...)`, leave macOS metadata out.

    Wraps detect(): the engine's own detect() runs unchanged, then every list in its result's "files" loses the
    paths whose part below `root` is macOS metadata (_is_macos_metadata), and "total_files" follows. Nothing else
    in the result changes, and a result of any other shape is handed back as the engine returned it. `graphify
    update` reaches detect() through the module attribute at call time (watch._rebuild_code; detect_incremental
    too), so the module attribute is the one patched; a graphify module that bound the name at its own import is
    rebound as well. Idempotent — re-applying rewraps the engine's original, never the wrapper. Returns True when
    the wrapper is in place; False (and no patch) when the engine symbol is missing. Never raises."""
    try:
        import graphify.detect as detect_mod
        current = getattr(detect_mod, "detect", None)
        original = getattr(current, "__wrapped__", current)
        if not callable(original):
            return False

        def detect(root, *args, **kwargs):
            result = original(root, *args, **kwargs)
            try:
                files = result.get("files") if isinstance(result, dict) else None
                if not isinstance(files, dict):
                    return result
                base = str(Path(root).resolve())
                dropped = 0
                for kind, paths in list(files.items()):
                    if not isinstance(paths, list):
                        continue
                    kept = [p for p in paths if not _metadata_listing(p, base)]
                    if len(kept) != len(paths):
                        dropped += len(paths) - len(kept)
                        files[kind] = kept
                if dropped and isinstance(result.get("total_files"), int):
                    result["total_files"] = max(0, result["total_files"] - dropped)
            except Exception:
                pass                           # the engine's own answer, unfiltered: sync drops the nodes
            return result

        detect.__wrapped__ = original
        detect.__doc__ = getattr(original, "__doc__", None)
        detect_mod.detect = detect
        for name, mod in list(sys.modules.items()):
            try:
                if (name.startswith("graphify.") and mod is not detect_mod
                        and vars(mod).get("detect") is original):
                    mod.detect = detect
            except Exception:
                continue                       # one odd module must not cost the patch
        return True
    except Exception:
        return False


#: The pathlib methods that list a directory; graphify's walks of the project tree outside detect() use only these.
_LISTING_METHODS = ("glob", "rglob", "iterdir")
_LISTING_MARK = "_deepmap_hides_macos_metadata"   # set on our wrappers only, so a re-apply unwraps ours and no other


def _listed_metadata(path, base_parts) -> bool:
    """Is `path`, as a pathlib listing of the directory whose parts are `base_parts` yields it, macOS metadata
    (_is_macos_metadata) — judged on the part below that directory, so a listing of a directory that itself sits
    under a `__MACOSX/` keeps its own files? A path not below it is judged by its name alone. The path is stat-ed
    only when its own name could be metadata (`._*`, `__MACOSX`, `.AppleDouble`): a DIRECTORY named `._dir` is not
    a twin (macOS writes twins as files) and a FILE named `__MACOSX` is not a metadata directory. Never raises
    (False: the engine's own answer stands)."""
    try:
        parts = tuple(path.parts)
        n = len(base_parts)
        rel = parts[n:] if parts[:n] == base_parts else parts[-1:]
        if not rel:
            return False
        if not _MACOS_METADATA_DIRS.isdisjoint(rel[:-1]):
            return True
        last = rel[-1]
        if not (last.startswith("._") or last in _MACOS_METADATA_DIRS):
            return False
        return _is_macos_metadata("/".join(rel), is_dir=path.is_dir())
    except Exception:
        return False


def _listing_without_metadata(original):
    """A Path.glob / rglob / iterdir that is `original` minus the macOS metadata it lists."""
    import functools

    @functools.wraps(original)
    def listing(self, *args, **kwargs):
        found = original(self, *args, **kwargs)   # called now: an error the engine's call raises is raised as before
        try:
            base = tuple(self.parts)
        except Exception:
            return found
        return (p for p in found if not _listed_metadata(p, base))

    setattr(listing, _LISTING_MARK, True)
    return listing


def hide_macos_metadata_listings() -> bool:
    """Make pathlib's directory listings — Path.glob, Path.rglob, Path.iterdir — leave macOS metadata out, in this
    process (the engine child, and every pool worker that re-imports this file).

    detect() is the engine's file discovery (skip_macos_metadata), but graphify also walks the tree itself while it
    extracts: graphify.extract._pascal_project_root (Path.glob `*.pas` / `*.dpr` children), _pascal_resolve_unit and
    _pascal_resolve_class (Path.rglob stem maps, the last listed file winning), _load_workspace_packages (pnpm
    workspace globs) and, in 0.8.51, the XAML pass (_xaml_project_root / _xaml_codebehind_path iterdir,
    _xaml_csharp_class_nodes rglob). Wrapping the three pathlib methods rather than those private functions reaches
    all of them on both ends of the supported range and any in-range move or rename, without copying the engine's
    logic; os.walk is left alone (detect()'s own walk is filtered by its result, and collect_files is not on the
    `update` path — tests/macos_appledouble_gates.py J1 fails on any listing nobody has accounted for).

    Each listing runs unchanged and at the same moment (so its own errors are raised where they always were); what
    it yields loses the paths whose part below the listed directory is macOS metadata (_listed_metadata), in the
    engine's own order. Only the engine child imports this file, and only when the parent found metadata
    (DEEPMAP_SKIP_MACOS_METADATA) does it patch anything — every other run keeps pathlib untouched. Idempotent:
    re-applying rewraps the original, never a wrapper. True when the concrete path class (PosixPath / WindowsPath)
    lists through all three wrappers; False otherwise — with nothing patched when pathlib lacks one of the methods.
    Never raises."""
    try:
        import pathlib
        cls = pathlib.Path
        plan = {}
        for name in _LISTING_METHODS:
            current = getattr(cls, name, None)
            original = current.__wrapped__ if getattr(current, _LISTING_MARK, False) else current
            if not callable(original):
                return False
            plan[name] = _listing_without_metadata(original)
        for name, wrapper in plan.items():
            setattr(cls, name, wrapper)
        concrete = type(cls())                 # PosixPath / WindowsPath: inherits the methods, never overrides them
        return all(getattr(getattr(concrete, name, None), _LISTING_MARK, False) for name in _LISTING_METHODS)
    except Exception:
        return False


def _skip_metadata_from_env() -> bool:
    """Whether the parent asked for macOS metadata to be left out. Never raises."""
    try:
        return str(os.environ.get(ENV_SKIP_METADATA, "")).strip() == "1"
    except Exception:
        return False


# --- IMPORT-TIME aliasing -------------------------------------------------------------------------
# This block is the load-bearing half for real codebases: a spawn-started ProcessPoolExecutor worker
# re-imports this file (as `__mp_main__`) and inherits the environment, so this re-applies the alias
# inside every child. Without it, any tree of >= 20 files extracts to zero nodes. Guarded so importing
# this module for a unit test (no env var set) has no side effect whatsoever. The directory rescue and the
# macOS-metadata filter are applied the same way, so they hold in whichever process runs the engine's file
# discovery.
if _aliases_from_env():
    alias_suffixes(_aliases_from_env())
if _rescue_from_env():
    rescue_skipped_dirs(*_rescue_from_env())
if _skip_metadata_from_env():
    skip_macos_metadata()
    hide_macos_metadata_listings()


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print("usage: graphify_case_shim.py <root> [<suffix> ...]", file=sys.stderr)
        return 2
    root, suffixes = argv[0], argv[1:]
    if suffixes:
        # Publish BEFORE delegating so pool workers inherit it (they are spawned during the run).
        os.environ[ENV_ALIASES] = ",".join(suffixes)
    aliased = alias_suffixes(suffixes)
    if aliased:
        # stderr, so it can never contaminate graphify's stdout contract.
        print(f"[deepmap] case-insensitive extraction: aliased {', '.join(sorted(aliased))} "
              f"-> {', '.join(sorted(set(aliased.values())))}", file=sys.stderr)
    elif suffixes:
        print(f"[deepmap] case-insensitive extraction: the engine registers no extractor for "
              f"{', '.join(sorted(suffixes))} — those files stay unextracted", file=sys.stderr)
    rescue = _rescue_from_env()
    if rescue:
        rescued = rescue_skipped_dirs(*rescue)
        print(f"[deepmap] engine-skipped directories re-included: {', '.join(rescued)}" if rescued else
              "[deepmap] could not re-include engine-skipped directories (the engine exposes no skip "
              "rule to adjust) — they stay unextracted", file=sys.stderr)
    if _skip_metadata_from_env():
        listed, hidden = skip_macos_metadata(), hide_macos_metadata_listings()
        print("[deepmap] macOS metadata (AppleDouble ._* files, __MACOSX/, .AppleDouble/) left out of the engine's "
              "file list" + (" and its own directory listings" if hidden else "") if listed else
              "[deepmap] could not leave macOS metadata out of the engine's file list (the engine exposes no file "
              "discovery to adjust) — DeepMap drops its nodes after the run", file=sys.stderr)
        if not hidden:
            print("[deepmap] could not hide macOS metadata from the engine's own directory listings (pathlib) — a "
                  "Delphi unit or class name may resolve to a metadata copy", file=sys.stderr)
    import graphify.__main__ as engine_cli
    sys.argv = ["graphify", "update", str(root), "--no-cluster"]
    engine_cli.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
