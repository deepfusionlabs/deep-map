#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gate: macOS metadata files are never taken for source (AppleDouble `._<name>` twins, `__MACOSX/`, Netatalk's
`.AppleDouble/`). No Neo4j, no network, temp dirs only (a temp DEEPMAP_HOME too); section F also asks git and the
installed graphify's own skip rule, and section H drives the REAL engine (a SKIP line, never a pass, without it).

THE DEFECT. On a volume without extended attributes (exFAT / FAT USB drives, SMB / NFS shares) macOS writes an
AppleDouble twin, a small BINARY `._<name>` file, next to every file it touches, and macOS-made zip / tar
archives carry the same twins (a zip under a `__MACOSX/` directory that mirrors the tree). Netatalk (AFP shares on
Linux NAS boxes) keeps them in a `.AppleDouble/` directory inside every directory a Mac touches, under the ORIGINAL
names. DeepMap took them for source: sync._iter_ast_files yielded `._x.py` (measured: ['._x.py', 'x.py'] for a
directory holding both), so the ast path printed a "skipped ._x.py" line per file (the twin holds NUL bytes) and
turned a parseable file under `__MACOSX/` into graph nodes; the verdict census counted the twins as source files,
so a layer the census could otherwise leave alone was pushed past a LOW-COVERAGE file threshold with no new nodes
(a false DEGRADED); framework_edges' and the recognizer registry's walks fed them to the recognizers (an `.erb`
twin became a junk :Module view node); and the skipped-dir lists graphify_extract hands the rescue and the verdict
counted a committed twin as hand-written source to re-include and to expect nodes from.
On the DEFAULT engine path it was worse (round 2): graphify's detect() has no dot-file rule, so it parsed every
twin and everything under `__MACOSX/`, and a twin gets the SAME engine id as its original (`pkg/._a.py` and
`pkg/a.py` are both `pkg_a_py_pkg_a`): the last in sort order wins, so a file whose name sorts before `._` lost
its own :Module — measured on 0.8.39, `routes/+page.ts` came back as Module `._+page` with source_file
`routes/._+page.ts`, and `.AppleDouble/x.py` (binary) as a Module `x`.

THE FIX. One predicate, `is_macos_metadata(rel, is_dir=False)`: a FILE whose name starts with `._`, or anything
with a `__MACOSX` or `.AppleDouble` directory component below the root. It lives in build/framework_edges.py
(stdlib only; sync and language_recognizers already import it) and is mirrored privately in verdict.py,
graphify_extract.py and graphify_case_shim.py (they import neither): the four copies are drift-locked in section A.
On the engine path: graphify_extract pre-scans the tree the way the engine walks it (macos_metadata_present) and
ONLY when it finds metadata routes `graphify update` through the case shim with DEEPMAP_SKIP_MACOS_METADATA=1,
whose IMPORT-TIME block wraps graphify.detect.detect so the engine's file list never holds metadata (spawn-started
pool workers re-import the shim and inherit the environment, the repo's rule for every engine patch); every other
repo keeps the byte-identical command and environment. sync._rebuild_code then drops, as a backstop, any node whose
source_file is metadata, with its calls and imports, whichever engine produced it.

What this gate locks:
  A. the predicate: what it rejects and, KL-mistake:008, the shapes it must NOT match (a normal dot-file, a
     dot-dir, `_x.py`, `x._y.py`, a `macosx/` platform dir, a `__MACOSX.py` file, a DIRECTORY named `._dir`,
     `.appledouble/`, `.AppleDoubles/`), identical in all four copies;
  B-E. every walker that decides what is source (sync's ast walk and graph, the incremental cache, the verdict
     census and coverage, framework_edges, the recognizer registry): a fixture tree WITHOUT metadata gives exactly
     what it gave before the fix (the expected values were taken from the pre-fix code), and the SAME tree with a
     twin beside every file, a `__MACOSX/` mirror and a `.AppleDouble/` in every directory gives the identical
     result;
  F. graphify_extract's skipped-dir lists (git and non-git) and the engine's time-budget count ignore metadata
     (the engine is told to leave it out, so it costs no engine time);
  G. the engine path, hermetic: the pre-scan (found exactly where the engine would walk), the engine command and
     environment (byte-identical without metadata — the keystone; through the shim with the variable only when the
     pre-scan finds some), the shim's detect() filter (exact, idempotent, degrades to no patch) and its import-time
     block in a fresh interpreter;
  H. the REAL engine (graphify 0.8.39 on the dev box, 0.8.51 in CI / a Linux container): x.py + ._x.py,
     routes/+page.ts + routes/._+page.ts, __MACOSX/ and .AppleDouble/ give nodes only for x.py and routes/+page.ts,
     identical to the tree without metadata, +page.ts keeping its own Module; the same above graphify's 20-file pool
     threshold (spawn workers); the same inside a re-included engine-skipped directory (the rescue keep list);
  I. sync's node backstop: metadata nodes dropped with their calls and imports (a keyed target too), the same
     objects back when there is none, and through _rebuild_code when an engine's result still holds some;
  J. (round 3) graphify's own disk walks OUTSIDE detect(). Its Pascal/Delphi resolvers list the tree themselves
     while they extract (in the pool workers at 20+ files): _pascal_project_root counts `*.pas` / `*.dpr` children
     with Path.glob (a `._Common.pas` twin made a directory holding one .pas file a project root, so a unit in a
     sibling directory resolved where the clean tree leaves it unresolved), and _pascal_resolve_unit /
     _pascal_resolve_class build stem -> id maps with Path.rglob (`.AppleDouble/Unit1.pas` has Unit1.pas's stem and
     was listed after it, so every `uses Unit1` and `class(TBase)` pointed at a file the graph does not hold:
     measured on 0.8.39, the clean tree's 3 resolved imports became 0). J1 inventories every directory listing in
     the installed engine (an AST scan; both ends of the range) and fails on one nobody has accounted for; J2 locks
     the shim's pathlib listing filter (Path.glob / rglob / iterdir) in a fresh interpreter; J3 / J4 drive the REAL
     engine over Delphi trees with twins, `.AppleDouble/` copies and a `__MACOSX/` mirror — the graph must equal the
     clean tree's, below and above the 20-file pool threshold.
     Round 4: those resolvers' rglob is UNPRUNED, so a `.AppleDouble/` or `__MACOSX/` that sits only under a
     directory detect() skips (out/, build/, node_modules/) still took over a unit's stem (0.8.39 and 0.8.51: the
     clean tree's imports main->base, main->unit1, unit1->base became main->unit1 alone), and the Pascal project root
     can be a directory ABOVE the sync root. In a tree with Pascal source the pre-scan therefore also walks the
     skipped directories under the Pascal roots and, for a root above, that root's tree and the twins on the climb
     (G1 hermetic, the engine's own _pascal_project_root checked for the 11-directory reach; J3 on the real engine,
     including ._ twins under skipped directories, which keep a stem no unit has and so never engage the filter). A
     tree without Pascal source keeps the walk detect() makes (G1, G2).
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build"
sys.path.insert(0, str(BUILD))

os.environ.setdefault("DEEPMAP_AUTO_SYNC", "0")
# Only graphify_extract decides when the shim filters metadata; an inherited value must not patch this process's
# graphify at import (the shim's import-time block) nor leak into a section's expectations.
os.environ.pop("DEEPMAP_SKIP_MACOS_METADATA", None)

import framework_edges as FE  # noqa: E402
import graphify_case_shim as SH  # noqa: E402
import graphify_extract as GX  # noqa: E402
import language_recognizers as LR  # noqa: E402
import sync as S  # noqa: E402
import verdict as V  # noqa: E402

ENV_SKIP = "DEEPMAP_SKIP_MACOS_METADATA"
_HAS_ENGINE = importlib.util.find_spec("graphify") is not None
_PASS = _FAIL = 0


def check(desc: str, cond: bool, detail="") -> None:
    global _PASS, _FAIL
    ok = bool(cond)
    _PASS += ok
    _FAIL += (not ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {desc}" + ("" if ok else f"  -- {detail}"))


def note(msg: str) -> None:
    print(f"  [note] {msg}")


def skip(msg: str) -> None:
    print(f"  [SKIP] {msg}")


# A real AppleDouble v2 header (magic 0x00051607, version 0x00020000, "Mac OS X" filler, 2 entries: Finder info
# and resource fork), zero-padded to the 4096 bytes macOS writes — binary, NUL bytes, never source.
APPLEDOUBLE = (b"\x00\x05\x16\x07\x00\x02\x00\x00Mac OS X        \x00\x02"
               b"\x00\x00\x00\x09\x00\x00\x00\x32\x00\x00\x0e\xb0"
               b"\x00\x00\x00\x02\x00\x00\x0e\xe2\x00\x00\x01\x1e")
APPLEDOUBLE += b"\x00" * (4096 - len(APPLEDOUBLE))

# The tree WITHOUT metadata. It deliberately holds the shapes the fix must leave alone: a dot-file, a file under a
# dot-dir, a DIRECTORY whose name starts with `._` (macOS writes twins as files only), `_x.py`, `x._y.py` and a
# `macosx/` platform directory.
CLEAN = {
    "pkg/__init__.py": "",
    "pkg/a.py": "from pkg.b import g_b\n\n\ndef f_a():\n    return g_b()\n",
    "pkg/b.py": "def g_b():\n    return 1\n",
    "web.py": ('from flask import Flask\n\napp = Flask(__name__)\n\n\n@app.route("/hello")\n'
               'def hello():\n    return "hi"\n'),
    ".hidden.py": "def hidden():\n    return 0\n",
    ".github/scripts/ci.py": "def ci():\n    return 0\n",
    "._dir/y.py": "def y():\n    return 0\n",
    "_x.py": "def under():\n    return 0\n",
    "x._y.py": "def dotted():\n    return 0\n",
    "macosx/plat.py": "def plat():\n    return 0\n",
    "app/models/order.rb": "class Order < ApplicationRecord\n  has_many :items\nend\n",
    "app/models/item.rb": "class Item < ApplicationRecord\n  belongs_to :order\nend\n",
    "app/views/orders/index.html.erb": "<h1>Orders</h1>\n<%= render 'row' %>\n",
    "app/views/orders/_row.html.erb": "<p>row</p>\n",
    "src/app.ts": "export function main(): number {\n  return 1;\n}\n",
    "README.md": "# fixture\n",
}

# What the pre-fix code gave for CLEAN (the fix must not move any of it).
EXPECTED_PY = ["._dir/y.py", ".github/scripts/ci.py", ".hidden.py", "_x.py", "macosx/plat.py",
               "pkg/__init__.py", "pkg/a.py", "pkg/b.py", "web.py", "x._y.py"]
EXPECTED_WALK = ["._dir/y.py", ".github/scripts/ci.py", ".hidden.py", "_x.py", "app/models/item.rb",
                 "app/models/order.rb", "app/views/orders/_row.html.erb", "app/views/orders/index.html.erb",
                 "macosx/plat.py", "pkg/__init__.py", "pkg/a.py", "pkg/b.py", "src/app.ts", "web.py", "x._y.py"]
EXPECTED_CENSUS = {"Python": 10, "Ruby": 2, "ERB": 2, "TypeScript": 1}
EXPECTED_AST_KEYS = [
    "._dir/y.py::module", "._dir/y.py::y:1", ".github/scripts/ci.py::ci:1", ".github/scripts/ci.py::module",
    ".hidden.py::hidden:1", ".hidden.py::module", "_x.py::module", "_x.py::under:1", "macosx/plat.py::module",
    "macosx/plat.py::plat:1", "pkg/__init__.py::module", "pkg/a.py::f_a:4", "pkg/a.py::module", "pkg/b.py::g_b:1",
    "pkg/b.py::module", "web.py::hello:7", "web.py::module", "x._y.py::dotted:1", "x._y.py::module"]


def _write(root: Path, files: dict) -> None:
    for rel, body in files.items():
        p = root.joinpath(*rel.split("/"))
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(body, bytes):
            p.write_bytes(body)
        else:
            p.write_bytes(body.encode("utf-8"))      # LF on every OS


def _metadata_for(files: dict) -> dict:
    """A twin beside every file, the same twins under a `__MACOSX/` mirror, a Netatalk `.AppleDouble/` in every
    directory holding a file (the twin under the ORIGINAL name, and the directory's own `.Parent`), twins of the top
    directories, and PARSEABLE stray files under `__MACOSX` / `.AppleDouble` directories (anything there is
    metadata, whatever it holds)."""
    meta: dict = {}
    dirs = set()
    for rel in files:
        d, _, name = rel.rpartition("/")
        twin = f"{d}/._{name}" if d else f"._{name}"
        meta[twin] = APPLEDOUBLE
        meta[f"__MACOSX/{twin}"] = APPLEDOUBLE
        meta[f"{d}/.AppleDouble/{name}" if d else f".AppleDouble/{name}"] = APPLEDOUBLE
        dirs.add(d)
    for d in dirs:
        meta[f"{d}/.AppleDouble/.Parent" if d else ".AppleDouble/.Parent"] = APPLEDOUBLE
    for top in sorted({rel.split("/")[0] for rel in files if "/" in rel}):
        meta[f"._{top}"] = APPLEDOUBLE
    meta["__MACOSX/pkg/stray.py"] = "def stray():\n    return 0\n"
    meta["pkg/__MACOSX/inner.py"] = "def inner():\n    return 0\n"
    meta["app/models/__MACOSX/ghost.rb"] = "class Ghost < ApplicationRecord\nend\n"
    meta["pkg/.AppleDouble/inner2.py"] = "def inner2():\n    return 0\n"
    meta["app/models/.AppleDouble/ghost2.rb"] = "class Ghost2 < ApplicationRecord\nend\n"
    return meta


class Trees:
    """A fresh CLEAN tree and a fresh CLEAN + metadata tree per call (sections never share one: the incremental
    cache writes into its root, graphify's per-file cache is keyed by the root)."""

    def __init__(self, base: Path):
        self.base, self.n = base, 0

    def pair(self, files: dict = CLEAN, meta: dict | None = None):
        self.n += 1
        clean, meta_root = self.base / f"clean{self.n}", self.base / f"meta{self.n}"
        _write(clean, files)
        _write(meta_root, files)
        _write(meta_root, _metadata_for(files) if meta is None else meta)
        return clean, meta_root

    def one(self, files: dict):
        self.n += 1
        root = self.base / f"tree{self.n}"
        root.mkdir(parents=True)
        _write(root, files)
        return root


def _rmtree(path: Path) -> None:
    """Remove the gate's own mkdtemp dir (git leaves read-only object files on Windows)."""
    def _retry(func, p, _exc):
        try:
            os.chmod(p, 0o700)
            func(p)
        except OSError:
            pass
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_retry)
    else:
        shutil.rmtree(path, onerror=_retry)


def _quiet(fn, *a, **kw):
    """(fn(*a, **kw), what it printed on stderr); its stdout chatter is dropped."""
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf), contextlib.redirect_stdout(io.StringIO()):
        out = fn(*a, **kw)
    return out, buf.getvalue()


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


def _names_metadata(text: str) -> list:
    return [ln for ln in text.splitlines() if "._" in ln or "__MACOSX" in ln or ".AppleDouble" in ln]


# --- A. the predicate and its three mirrors ------------------------------------------------------------------
_REJECT = ["._x.py", "pkg/._x.py", "pkg\\._x.py", "a/b/._index.html.erb", "._", "._.py", "._pkg",
           "__MACOSX/pkg/x.py", "__MACOSX/._x.py", "a/__MACOSX/b/c.rb", "pkg/__MACOSX/inner.py",
           "a\\__MACOSX\\c.py", "./._x.py", "pkg//._x.py",
           ".AppleDouble/x.py", "pkg/.AppleDouble/a.py", "a\\.AppleDouble\\b.rb", ".AppleDouble/.Parent",
           "a/.AppleDouble/b/c.ts", "src/routes/.AppleDouble/+page.ts"]
_KEEP = ["x.py", ".x.py", ".hidden.py", ".github/workflows/ci.py", "._dir/y.py", "a/._dir/y.py", "_x.py",
         "__x.py", "x._y.py", ".._x.py", "a._/b.py", "macosx/plat.py", "MACOSX/x.py", "__macosx/x.py",
         "__MACOSX_tools/x.py", "src/__MACOSX.py", "__MACOSX", "", ".", "pkg/", "pkg/.git/x.py",
         ".AppleDouble", "src/.AppleDouble", ".AppleDouble.py", "src/.AppleDouble.py", ".appledouble/x.py",
         "AppleDouble/x.py", ".AppleDoubles/x.py", ".AppleDouble_old/x.py", "x.AppleDouble/y.py",
         "routes/+page.ts"]
_DIR_REJECT = ["__MACOSX", "a/__MACOSX", "__MACOSX/a", "a\\__MACOSX\\b",
               ".AppleDouble", "a/.AppleDouble", ".AppleDouble/x", "a\\.AppleDouble"]
_DIR_KEEP = ["._dir", "a/._dir", ".git", ".github", "MACOSX", "macosx", "__macosx", "__MACOSX_tools", "", "pkg",
             ".appledouble", "AppleDouble", ".AppleDoubles", ".AppleDouble_old", ".github/workflows"]


def _copies():
    out = []
    for where, mod, name in (("framework_edges", FE, "is_macos_metadata"),
                             ("verdict", V, "_is_macos_metadata"),
                             ("graphify_extract", GX, "_is_macos_metadata"),
                             ("graphify_case_shim", SH, "_is_macos_metadata")):
        fn = getattr(mod, name, None)
        check(f"{where}.{name} exists", callable(fn), "missing")
        if callable(fn):
            out.append((f"{where}.{name}", fn))
    return out


def gate_predicate() -> None:
    print("\n-- A. is_macos_metadata: what it rejects, what it must keep, four identical copies --")
    copies = _copies()
    for label, fn in copies:
        bad = [r for r in _REJECT if fn(r) is not True]
        check(f"{label}: rejects every AppleDouble twin and anything under __MACOSX/ or .AppleDouble/", not bad, bad)
        bad = [r for r in _KEEP if fn(r) is not False]
        check(f"{label}: keeps dot-files, dot-dirs, a ._dir/ directory, _x.py, x._y.py, macosx/, __MACOSX.py, "
              f".appledouble/, .AppleDoubles/", not bad, bad)
        bad = [r for r in _DIR_REJECT if fn(r, is_dir=True) is not True]
        check(f"{label}: a directory is metadata only when it is (or is under) __MACOSX or .AppleDouble", not bad,
              bad)
        bad = [r for r in _DIR_KEEP if fn(r, is_dir=True) is not False]
        check(f"{label}: a directory named ._dir / .git / .github / macosx / __macosx / .appledouble is NOT pruned",
              not bad, bad)
        weird = []
        for arg in (None, 3, b"._x.py", object()):
            try:
                if fn(arg) is not False:
                    weird.append(repr(arg))
            except Exception as e:  # noqa: BLE001
                weird.append(f"{arg!r} raised {type(e).__name__}")
        check(f"{label}: never raises, False for a non-path", not weird, weird)
    if len(copies) == 4:
        table = [(r, False) for r in _REJECT + _KEEP] + [(r, True) for r in _DIR_REJECT + _DIR_KEEP]
        ref = [copies[0][1](r, is_dir=d) for r, d in table]
        for label, fn in copies[1:]:
            got = [fn(r, is_dir=d) for r, d in table]
            diff = [table[i] for i in range(len(table)) if got[i] != ref[i]]
            check(f"{label} matches framework_edges.is_macos_metadata on every case (drift lock)", not diff, diff)


# --- B. sync: the ast walk, the graph, the incremental cache -----------------------------------------------
def gate_sync(trees: Trees) -> None:
    print("\n-- B. sync._iter_ast_files / _rebuild_code_ast / the incremental cache --")
    clean, meta = trees.pair()
    got_clean = [rel for _py, rel in S._iter_ast_files(clean)]
    check("a tree without metadata walks exactly the .py list it walked before the fix (dot-files, dot-dirs, "
          "._dir/, _x.py, x._y.py, macosx/ kept)", got_clean == EXPECTED_PY, got_clean)
    got_meta = [rel for _py, rel in S._iter_ast_files(meta)]
    check("the same tree with ._ twins, a __MACOSX/ mirror and .AppleDouble/ dirs walks the identical list",
          got_meta == got_clean, [r for r in got_meta if r not in got_clean])
    g_clean, err_clean = _quiet(S._rebuild_code_ast, clean)
    check("the clean graph has exactly the node keys it had before the fix",
          sorted(n["key"] for n in g_clean[0]) == EXPECTED_AST_KEYS, sorted(n["key"] for n in g_clean[0]))
    g_meta, err_meta = _quiet(S._rebuild_code_ast, meta)
    check("the graph built from the metadata tree is identical (nodes, calls, imports)", g_meta == g_clean,
          sorted({n["source_file"] for n in g_meta[0]} - {n["source_file"] for n in g_clean[0]}))
    check("no 'skipped ._x.py' (NUL bytes) line, nor any line naming a metadata file", not _names_metadata(err_meta),
          _names_metadata(err_meta)[:3])
    check("the clean build prints nothing about metadata either", not _names_metadata(err_clean),
          _names_metadata(err_clean)[:3])
    _, inc_meta = trees.pair()
    with _env(DEEPMAP_INCREMENTAL_SYNC="1"):
        cold, _e1 = _quiet(S._rebuild_code_ast_incremental, inc_meta)
        warm, _e2 = _quiet(S._rebuild_code_ast_incremental, inc_meta)
    check("the incremental cache (cold and warm) over the metadata tree equals the clean full rebuild",
          cold == g_clean and warm == g_clean,
          sorted({n["source_file"] for n in cold[0]} - {n["source_file"] for n in g_clean[0]}))
    with _env(DEEPMAP_EXTRACTOR="ast"):
        (ex, _err) = _quiet(S._rebuild_code, meta)
    check("sync._rebuild_code (ast) over the metadata tree gives the clean graph",
          tuple(ex[:3]) == tuple(g_clean) and ex[3] == "ast", sorted({n["source_file"] for n in ex[0]}))


# --- C. verdict: the census, coverage, the false LOW COVERAGE ----------------------------------------------
def gate_verdict(trees: Trees) -> None:
    print("\n-- C. verdict: extension_census / build_coverage / _coverage_reasons --")
    clean, meta = trees.pair()
    c_clean, c_meta = V.extension_census(clean), V.extension_census(meta)
    check("the census of a tree without metadata is what it was before the fix", c_clean == EXPECTED_CENSUS, c_clean)
    check("the census of the same tree with twins, a __MACOSX/ mirror and .AppleDouble/ dirs is identical",
          c_meta == c_clean, c_meta)
    nodes = [n for n in S._rebuild_code_ast(clean)[0]]
    cov_clean = V.build_coverage(clean, nodes, census=c_clean)
    cov_meta = V.build_coverage(meta, nodes, census=c_meta)
    check("build_coverage (languages and conventional dirs app/models, app/views) is identical",
          cov_meta == cov_clean, {"clean": cov_clean, "meta": cov_meta})
    check("the conventional dirs count their real files only",
          cov_meta.get("dirs", {}).get("app/models", {}).get("files") == 2
          and cov_meta.get("dirs", {}).get("app/views", {}).get("files") == 2, cov_meta.get("dirs"))
    # Four C# files the engine produced nothing for stay silent by design (the zero-node rule waits for 5 files);
    # their four twins alone pushed C# to 8 "files" and raised a LOW COVERAGE reason no code change caused.
    cs = {f"svc/s{i}.cs": f"class S{i} {{ }}\n" for i in range(4)}
    cs_clean, cs_meta = trees.pair(cs)
    r_clean = V._coverage_reasons(V.build_coverage(cs_clean, []))
    r_meta = V._coverage_reasons(V.build_coverage(cs_meta, []))
    check("4 C# files with 0 nodes: no reason, as before", not [r for r in r_clean if "C#" in r], r_clean)
    check("the same 4 C# files with their twins: still no LOW COVERAGE for C# (the twins are not files)",
          not [r for r in r_meta if "C#" in r], r_meta)
    # The long-path walker (Windows, LongPathsEnabled=0) lists through \\?\ and must apply the same rule.
    if os.name == "nt":
        got = sorted(os.path.relpath(p, str(meta)).replace("\\", "/")
                     for p in V._walk_long(str(meta), V._dir_ignore_rule(), 1000))
        ref = sorted(os.path.relpath(p, str(clean)).replace("\\", "/")
                     for p in V._walk_long(str(clean), V._dir_ignore_rule(), 1000))
        check("verdict._walk_long (the \\\\?\\ census walk) skips the metadata too", got == ref,
              [r for r in got if r not in ref])
    else:
        note("verdict._walk_long walks through \\\\?\\ paths: Windows only, not run here")


# --- D. framework_edges -------------------------------------------------------------------------------------
def gate_framework_edges(trees: Trees) -> None:
    print("\n-- D. framework_edges._iter_py / extract_framework_edges --")
    clean, meta = trees.pair()
    got_clean = [rel for rel, _p in FE._iter_py(clean)]
    check("_iter_py over a tree without metadata is what it was before the fix", got_clean == EXPECTED_PY, got_clean)
    got_meta = [rel for rel, _p in FE._iter_py(meta)]
    check("_iter_py over the metadata tree is identical", got_meta == got_clean,
          [r for r in got_meta if r not in got_clean])
    e_clean, err_clean = _quiet(FE.extract_framework_edges, clean)
    e_meta, err_meta = _quiet(FE.extract_framework_edges, meta)
    check("the clean tree still yields its Flask route", any(e.edge_type == "ROUTES_TO" for e in e_clean),
          e_clean)
    check("extract_framework_edges over the metadata tree yields the identical edges", e_meta == e_clean,
          [e for e in e_meta if e not in e_clean][:3])
    check("no 'framework-edges skipped ._x.py' line", not _names_metadata(err_meta), _names_metadata(err_meta)[:3])


# --- E. the recognizer registry ------------------------------------------------------------------------------
def gate_recognizers(trees: Trees) -> None:
    print("\n-- E. language_recognizers.walk_files / iter_source / run --")
    clean, meta = trees.pair()
    exts, ign = {".py", ".rb", ".erb", ".ts"}, {"node_modules"}
    got_clean = [rel for rel, _p in LR.walk_files(clean, exts, ign)]
    check("walk_files over a tree without metadata is what it was before the fix", got_clean == EXPECTED_WALK,
          got_clean)
    got_meta = [rel for rel, _p in LR.walk_files(meta, exts, ign)]
    check("walk_files over the metadata tree is identical (TypeScript's module listing and receiver_facts_ts "
          "use it too)", got_meta == got_clean, [r for r in got_meta if r not in got_clean])
    ruby = next((e for e in LR.REGISTRY if e.name == "ruby"), None)
    if ruby is not None:
        src_clean = list(LR.iter_source(clean, ruby))
        src_meta = list(LR.iter_source(meta, ruby))
        check("iter_source (ruby: .rb + .erb) reads the identical files", src_meta == src_clean,
              [r for r, _s in src_meta if (r, _s) not in src_clean])
    (n_clean, ed_clean), err_clean = _quiet(LR.run, clean)
    (n_meta, ed_meta), err_meta = _quiet(LR.run, meta)
    check("the clean tree still yields its ERB view node",
          any(n.get("source_file") == "app/views/orders/index.html.erb" for n in n_clean),
          [n.get("source_file") for n in n_clean])
    check("every recognizer's nodes over the metadata tree are identical (no ._index.html.erb view node)",
          n_meta == n_clean, sorted({n.get("source_file") for n in n_meta} - {n.get("source_file") for n in n_clean}))
    check("every recognizer's typed edges over the metadata tree are identical", ed_meta == ed_clean,
          [e for e in ed_meta if e not in ed_clean][:3])
    check("no recognizer line names a metadata file", not _names_metadata(err_meta), _names_metadata(err_meta)[:3])


# --- F. graphify_extract: the skipped-dir lists, and the engine's own file count -----------------------------
def _git(root: Path, *args) -> bool:
    git = shutil.which("git")
    if not git:
        return False
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0")
    p = subprocess.run([git, "-C", str(root), *args], capture_output=True, env=env, timeout=120)
    return p.returncode == 0


def _git_tree(root: Path) -> bool:
    return (_git(root, "init", "-q") and _git(root, "add", "-A")
            and _git(root, "-c", "user.name=gate", "-c", "user.email=gate@example.invalid", "commit", "-q",
                     "-m", "fixture"))


def gate_graphify_extract(trees: Trees) -> None:
    print("\n-- F. graphify_extract: skipped_source_dirs / unindexed_source_dirs / engine_source_file_count --")
    if GX._engine_noise_dir() is None:
        skip("graphify (its directory-skip rule) is not importable here: the skipped-dir lists are empty by "
             "design, sections F1-F3 not run")
    else:
        files = {"build/tool.py": "def tool():\n    return 0\n", "env/helpers.py": "def helper():\n    return 0\n",
                 "src/main.py": "def main():\n    return 0\n"}
        clean, meta = trees.pair(files)
        if not (_git(clean, "init", "-q") and _git(clean, "add", "-A") and _git(meta, "init", "-q")
                and _git(meta, "add", "-A")):
            skip("git is not usable here: F1-F2 not run")
        else:
            s_clean, s_meta = GX.skipped_source_dirs(clean), GX.skipped_source_dirs(meta)
            check("F1 skipped_source_dirs of a git tree without metadata: build/tool.py, as before",
                  s_clean == {"build": ["build/tool.py"]}, s_clean)
            check("F1 ...and with committed twins, a committed __MACOSX/ mirror and .AppleDouble/ dirs: identical (no "
                  "._tool.py or .AppleDouble/tool.py to re-include, no __MACOSX/build/ to expect nodes from)",
                  s_meta == s_clean, s_meta)
            u_clean, u_meta = GX.unindexed_source_dirs(clean), GX.unindexed_source_dirs(meta)
            check("F2 unindexed_source_dirs (git): env/helpers.py, as before",
                  u_clean == {"env": {"files": ["env/helpers.py"], "kind": "never-rescued"}}, u_clean)
            check("F2 ...identical with the metadata committed", u_meta == u_clean, u_meta)
        ng_clean, ng_meta = trees.pair({"build/tool.py": "def tool():\n    return 0\n",
                                        "src/main.py": "def main():\n    return 0\n"})
        n_clean, n_meta = GX.unindexed_source_dirs(ng_clean), GX.unindexed_source_dirs(ng_meta)
        check("F3 unindexed_source_dirs (non-git): build/tool.py, as before",
              n_clean == {"build": {"files": ["build/tool.py"], "kind": "non-git"}}, n_clean)
        check("F3 ...identical with twins, a __MACOSX/ mirror and .AppleDouble/ dirs (no false 'LOW COVERAGE in "
              "build/')", n_meta == n_clean, n_meta)
    # F4 (round 2): the engine is told to leave metadata out of its file list (section G), so it costs no engine
    # time and the time budget no longer counts it — census, rescue, graph and budget agree.
    small = {"pkg/a.py": "def a():\n    return 0\n"}
    c_clean, c_meta = trees.pair(small)
    k_clean, k_meta = GX.engine_source_file_count(c_clean), GX.engine_source_file_count(c_meta)
    check("F4 engine_source_file_count does not count macOS metadata (the engine no longer parses it)",
          k_clean == 1 and k_meta == k_clean, (k_clean, k_meta))
    if GX._engine_noise_dir() is not None:
        r_clean, r_meta = trees.pair({"build/tool.py": "def tool():\n    return 0\n"})
        k_clean = GX.engine_source_file_count(r_clean, rescue=(["build"], []))
        k_meta = GX.engine_source_file_count(r_meta, rescue=(["build"], []))
        check("F4 ...nor inside a re-included engine-skipped directory", k_clean == 1 and k_meta == 1,
              (k_clean, k_meta))


# --- G. the engine path, hermetic: pre-scan, command + environment, the shim's detect() filter ---------------
def _capture(root: Path, **env_over):
    """(argv, env, stderr) of the engine run extract_with_report(root) would start, with the subprocess and the
    engine's presence stubbed (the graph it 'writes' is empty)."""
    seen: dict = {}
    real_run, real_spec = GX.subprocess.run, importlib.util.find_spec

    class _Proc:
        returncode, stdout, stderr = 0, "", ""

    def fake_run(cmd, **kw):
        flat = " ".join(str(c) for c in cmd)
        if not ("graphify" in flat and ("-m" in cmd or "graphify_case_shim" in flat)):
            return real_run(cmd, **kw)           # git (the skipped-dir lists) runs for real
        seen["cmd"], seen["env"] = list(cmd), dict(kw.get("env") or {})
        out = GX.graphify_out_dir(root)
        out.mkdir(parents=True, exist_ok=True)
        (out / "graph.json").write_text(json.dumps({"nodes": [], "links": []}), encoding="utf-8")
        return _Proc()

    def fake_spec(name, *a, **kw):
        return object() if name == "graphify" else real_spec(name, *a, **kw)

    GX.subprocess.run, importlib.util.find_spec = fake_run, fake_spec
    try:
        with _env(**env_over):
            _res, err = _quiet(GX.extract_with_report, root)
    finally:
        GX.subprocess.run, importlib.util.find_spec = real_run, real_spec
    return seen.get("cmd") or [], seen.get("env") or {}, err


def _expected_env(root: Path) -> dict:
    """The engine environment extract_with_report has always built (before the metadata fix)."""
    env = dict(os.environ, PYTHONUTF8="1", GRAPHIFY_OUT=str(GX.graphify_out_dir(root.resolve())))
    for k in (GX.ENV_CASE_ALIASES, GX.ENV_RESCUE, ENV_SKIP):
        env.pop(k, None)
    env["PYTHONSAFEPATH"] = "1"
    return env


@contextlib.contextmanager
def _fake_detect(detect_fn):
    """`graphify.detect` resolving to a fake module whose `detect` is `detect_fn` (None: the attribute is absent);
    sys.modules restored afterwards."""
    import types
    saved = {k: sys.modules.get(k) for k in ("graphify", "graphify.detect")}
    pkg, det = types.ModuleType("graphify"), types.ModuleType("graphify.detect")
    if detect_fn is not None:
        det.detect = detect_fn
    pkg.detect = det
    sys.modules["graphify"], sys.modules["graphify.detect"] = pkg, det
    try:
        yield det
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


def _pas_unit(name: str) -> str:
    return f"unit {name};\ninterface\nimplementation\nend.\n"


def _under(prefix: str, files: dict) -> dict:
    return {f"{prefix}/{k}": v for k, v in files.items()}


def _g1_pascal(fn, trees: Trees) -> None:
    """Round 4: graphify's Pascal/Delphi resolvers list the tree from the Pascal project root with an UNPRUNED
    Path.rglob, so a `.AppleDouble/` or `__MACOSX/` under a directory detect() skips (out/, build/, node_modules/)
    still takes over a unit's stem (J3 measures it on the real engine). In a tree with Pascal source the pre-scan
    must find it; in a tree without, nothing changes (the cases above)."""
    d = DELPHI_CLEAN
    for label, meta in (
            ("app/out/.AppleDouble/Base.pas (out/: skipped by detect(), listed by the resolvers' rglob)",
             {"app/out/.AppleDouble/Base.pas": APPLEDOUBLE}),
            ("app/build/.AppleDouble/Unit1.pas", {"app/build/.AppleDouble/Unit1.pas": APPLEDOUBLE}),
            ("app/node_modules/__MACOSX/Base.pas", {"app/node_modules/__MACOSX/Base.pas": APPLEDOUBLE}),
            ("app/out/x/y/.AppleDouble/Base.pas (deep inside the skipped directory)",
             {"app/out/x/y/.AppleDouble/Base.pas": APPLEDOUBLE}),
            ("app/node_modules/p/node_modules/.AppleDouble/Base.pas (a skipped directory inside a skipped one)",
             {"app/node_modules/p/node_modules/.AppleDouble/Base.pas": APPLEDOUBLE})):
        r = trees.one({**d, **meta})
        check(f"G1 a Delphi tree whose only metadata is {label}: found", fn(r) is True, fn(r))
    r = trees.one({**d, "app/out/._Base.pas": APPLEDOUBLE, "app/build/._Unit1.pas": APPLEDOUBLE,
                   "app/node_modules/._x.js": APPLEDOUBLE, "app/out/._Main.dpr": APPLEDOUBLE})
    check("G1 a Delphi tree whose only metadata is ._ twins under skipped directories: none found (a twin's stem is "
          "`._base`, never a unit or class name, and a skipped directory is on no parsed file's climb; J3 runs it)",
          fn(r) is False, fn(r))
    r = trees.one({**d, "app/out/Main.exe": b"MZ\x00\x00", "app/node_modules/p/i.js": "x\n"})
    check("G1 a clean Delphi tree with skipped directories: none found", fn(r) is False, fn(r))
    r = trees.one({"APP/MAIN.DPR": "program Main;\nuses Unit1;\nbegin\nend.\n", "APP/UNIT1.PAS": _pas_unit("Unit1"),
                   "APP/out/.AppleDouble/UNIT1.PAS": APPLEDOUBLE})
    check("G1 an odd-case Delphi tree (MAIN.DPR, UNIT1.PAS) with out/.AppleDouble/: found (Pascal source in any case, "
          "as DeepMap compares extensions)", fn(r) is True, fn(r))
    for ext in (".dpk", ".lpr", ".lpk"):
        r = trees.one({f"src/one{ext}": "x\n", "src/out/.AppleDouble/Base.pas": APPLEDOUBLE})
        check(f"G1 a lone {ext} file (the engine runs the Pascal resolvers for it) with out/.AppleDouble/: found",
              fn(r) is True, fn(r))
    # .inc / .pp are also C/C++/PHP/assembly includes and Puppet manifests: the engine parses them as Pascal, but a
    # resolver runs only from a `uses` clause or a base list, so only such a text makes the skipped directories count.
    for ext, text in ((".inc", "uses Base;\n"), (".pp", "unit One;\ninterface\nuses Base;\nimplementation\nend.\n"),
                      (".inc", "type TFoo = class(TBase)\nend;\n"), (".inc", "type THelp = class helper for TBase\nend;\n")):
        r = trees.one({f"src/one{ext}": text, "src/out/.AppleDouble/Base.pas": APPLEDOUBLE})
        check(f"G1 a lone {ext} file whose text reaches a resolver ({' '.join(text.split())[:44]!r}) "
              "with out/.AppleDouble/: found", fn(r) is True, fn(r))
    for ext, text in ((".inc", "; nasm include\nSECTION .text\n"), (".inc", "#define LIMIT 10\nstruct s { int a; };\n"),
                      (".pp", "class apache ($port = 80) {\n  package { 'httpd': ensure => installed }\n}\n"),
                      (".inc", "<?php\nclass Foo extends Bar {}\n")):
        r = trees.one({f"src/one{ext}": text, "src/node_modules/__MACOSX/Base.pas": APPLEDOUBLE,
                       "src/out/.AppleDouble/Base.pas": APPLEDOUBLE})
        check(f"G1 a {ext} that is not Pascal ({' '.join(text.split())[:44]!r}) with metadata only under skipped "
              "directories: none found (no resolver runs; the skipped directories are not walked)", fn(r) is False,
              fn(r))
    # Bounded to the Pascal project roots: a skipped directory outside every one of them is never listed by the
    # resolvers (delphi/app holds Main.dpr, delphi/shared and delphi/ their own files; web/ none).
    two = {**_under("delphi", d), "web/x.ts": "export const x = 1;\n", "web/node_modules/__MACOSX/a.js": "x\n",
           "web/node_modules/p/.AppleDouble/Base.pas": APPLEDOUBLE}
    r = trees.one(two)
    check("G1 metadata only in a skipped directory outside every Pascal project root (web/node_modules/): none found",
          fn(r) is False, fn(r))
    r = trees.one({**two, "delphi/app/out/.AppleDouble/Base.pas": APPLEDOUBLE})
    check("G1 ...but found in one under a Pascal project root (delphi/app/out/)", fn(r) is True, fn(r))

    # Above the root: the engine climbs from each Pascal file's directory through up to 11 directories above it to
    # the highest holding two *.pas or one *.dpr, never a filesystem root (graphify.extract._pascal_project_root),
    # so a directory the project sits in can be the Pascal project root, and its rglob covers the project's
    # siblings. A `._` twin there counts while the engine picks the root.
    up = {"A.pas": _pas_unit("A"), "B.pas": _pas_unit("B"), **_under("proj", d)}
    r = trees.one(up)
    check("G1 a Delphi project in a directory with two .pas files (the Pascal root lies above the sync root), "
          "nothing there: none found", fn(r / "proj") is False, fn(r / "proj"))
    r = trees.one({**up, "other/.AppleDouble/Base.pas": APPLEDOUBLE})
    check("G1 ...with other/.AppleDouble/Base.pas beside the project (the resolvers' rglob from the root above "
          "lists it): found", fn(r / "proj") is True, fn(r / "proj"))
    r = trees.one({**up, "other/out/__MACOSX/Base.pas": APPLEDOUBLE})
    check("G1 ...and with other/out/__MACOSX/ (skipped by nothing there): found", fn(r / "proj") is True,
          fn(r / "proj"))
    r = trees.one({"A.pas": _pas_unit("A"), "._A.pas": APPLEDOUBLE, **_under("proj", d)})
    check("G1 one .pas file and its ._ twin in the directory above (the twin makes it count two): found",
          fn(r / "proj") is True, fn(r / "proj"))
    r = trees.one({"._Top.dpr": APPLEDOUBLE, **_under("proj", d)})
    check("G1 a ._*.dpr twin alone in the directory above (it makes that directory a root): found",
          fn(r / "proj") is True, fn(r / "proj"))
    r = trees.one({"A.pas": _pas_unit("A"), "B.pas": _pas_unit("B"), "notes/._readme.txt": APPLEDOUBLE,
                   **_under("proj", d)})
    check("G1 a ._ twin of another kind beside the project (not on the climb, a stem no unit has): none found",
          fn(r / "proj") is False, fn(r / "proj"))
    r = trees.one({"A.pas": _pas_unit("A"), "B.pas": _pas_unit("B"), **_under("__MACOSX/proj", d)})
    check("G1 a project that itself lives under a __MACOSX/ below that root: the directory holding the project is "
          "not counted (the filter would hide the project's own files from a listing rooted above it)",
          fn(r / "__MACOSX" / "proj") is False, fn(r / "__MACOSX" / "proj"))
    # The reach: a Pascal file's directory and the 11 above it. One unit 10 directories deep reaches the directory
    # above the sync root; 11 deep it does not.
    deep10 = "/".join(f"d{i}" for i in range(1, 11))
    deep11 = deep10 + "/d11"
    far = {"A.pas": _pas_unit("A"), "B.pas": _pas_unit("B"), "other/.AppleDouble/Base.pas": APPLEDOUBLE}
    r10 = trees.one({**far, f"proj/{deep10}/X.pas": _pas_unit("X")})
    r11 = trees.one({**far, f"proj/{deep11}/X.pas": _pas_unit("X")})
    check("G1 the climb's reach: a unit 10 directories below the sync root reaches the directory above it (found); "
          "one 11 below does not (none found)", fn(r10 / "proj") is True and fn(r11 / "proj") is False,
          (fn(r10 / "proj"), fn(r11 / "proj")))
    try:
        import graphify.extract as GE
        climb = getattr(GE, "_pascal_project_root", None)
    except Exception:  # noqa: BLE001
        climb = None
    if callable(climb):
        got10 = climb((r10 / "proj" / deep10 / "X.pas").resolve())
        got11 = climb((r11 / "proj" / deep11 / "X.pas").resolve())
        check("G1 ...which is the installed engine's own climb (_pascal_project_root: the root above for the unit 10 "
              "deep, not for the one 11 deep)", Path(got10) == r10.resolve() and Path(got11) != r11.resolve(),
              (got10, got11))
        app_root = climb((trees.one(d) / "app" / "Unit1.pas").resolve())
        check("G1 ...and the Delphi fixture's Pascal root is its app/ directory (Main.dpr), not the tree root",
              Path(app_root).name == "app", app_root)
    else:
        skip("G1 the engine's _pascal_project_root is not importable here: the climb's premise was not checked")


def gate_engine_path_hermetic(trees: Trees) -> None:
    print("\n-- G. the engine path (hermetic): macos_metadata_present, the engine command/env, the shim's filter --")
    check("G0 graphify_extract and the shim name the same variable, DEEPMAP_SKIP_MACOS_METADATA",
          getattr(GX, "ENV_SKIP_MACOS_METADATA", None) == ENV_SKIP and getattr(SH, "ENV_SKIP_METADATA", None) == ENV_SKIP,
          (getattr(GX, "ENV_SKIP_MACOS_METADATA", None), getattr(SH, "ENV_SKIP_METADATA", None)))
    fn = getattr(GX, "macos_metadata_present", None)
    check("G1 graphify_extract.macos_metadata_present exists", callable(fn))
    if callable(fn):
        clean, meta = trees.pair()
        check("G1 a tree without metadata: none found (dot-files, .github/, a ._dir/ DIRECTORY, x._y.py, macosx/)",
              fn(clean) is False, fn(clean))
        check("G1 the same tree with twins, __MACOSX/ and .AppleDouble/: found", fn(meta) is True, fn(meta))
        lone = {"one ._x.py twin": {"x.py": "x = 1\n", "._x.py": APPLEDOUBLE},
                "one twin of a non-source file (._README.md: the engine reads .md too)":
                    {"x.py": "x = 1\n", "._README.md": APPLEDOUBLE},
                "one Netatalk .AppleDouble/ directory": {"x.py": "x = 1\n", "src/.AppleDouble/.Parent": APPLEDOUBLE},
                "one __MACOSX/ directory": {"x.py": "x = 1\n", "__MACOSX/notes.txt": "x\n"}}
        for label, files in lone.items():
            r = trees.one(files)
            check(f"G1 {label}: found", fn(r) is True, fn(r))
        r = trees.one({"x.py": "x = 1\n", "node_modules/pkg/._index.js": APPLEDOUBLE,
                       "node_modules/pkg/__MACOSX/a.js": "x\n"})
        check("G1 a tree without Pascal source, metadata only under node_modules/ (detect() never walks it and no "
              "Pascal resolver lists it): none found", fn(r) is False, fn(r))
        if GX._engine_noise_dir() is not None:
            r = trees.one({"x.py": "x = 1\n", "build/tool.py": "x = 1\n", "build/._tool.py": APPLEDOUBLE})
            check("G1 a tree without Pascal source, metadata only under build/ (detect() skips it by name): none found",
                  fn(r) is False, fn(r))
            check("G1 ...but found when build/ is re-included (the rescue the shim receives)",
                  fn(r, rescue=(["build"], [])) is True and fn(r, rescue=([], ["build"])) is True)
            r = trees.one({"x.py": "x = 1\n", "build/.AppleDouble/x.py": APPLEDOUBLE, "out/__MACOSX/y.py": "y = 1\n",
                           "node_modules/p/.AppleDouble/i.js": APPLEDOUBLE})
            check("G1 a tree without Pascal source, .AppleDouble/ and __MACOSX/ only under build/, out/, "
                  "node_modules/: none found (today's walk: nothing in the engine lists them)", fn(r) is False, fn(r))
            _g1_pascal(fn, trees)
        else:
            skip("G1 the engine-skipped-dir cases need graphify's skip rule")
        check("G1 a missing root / a non-path: False, never raises",
              fn(trees.base / "no" / "such") is False and fn(None) is False)
        # Round 3: a budget that runs out before the tree does must never answer "no metadata" — the unsafe answer
        # (the shim is not engaged, a twin takes its original's engine id and sync's backstop then drops the real
        # file's node by key). Five clean files at the top, the only twin in a directory walked after them.
        deep = {f"m{i}.py": "x = 1\n" for i in range(5)}
        deep["z/deep/x.py"] = "x = 1\n"
        r = trees.one({**deep, "z/deep/._x.py": APPLEDOUBLE})
        check("G1 a budget spent before the walk reaches the twin answers True, never False",
              fn(r, limit=3) is True, fn(r, limit=3))
        r_clean = trees.one(deep)
        check("G1 ...and so does a spent budget on a tree it could not finish (it cannot rule metadata out; the "
              "filter is a no-op on a clean tree)", fn(r_clean, limit=3) is True, fn(r_clean, limit=3))
        check("G1 a budget the tree fits in, and the default: a clean tree is clean",
              fn(r_clean, limit=50) is False and fn(r_clean) is False, (fn(r_clean, limit=50), fn(r_clean)))
        import inspect
        default = inspect.signature(fn).parameters.get("limit")
        check("G1 the default has no budget: the walk covers the tree detect() walks (plus what .gitignore / "
              ".graphifyignore hide from it), and in a Pascal tree the skipped directories under the Pascal roots "
              "(it ends at the first hit)",
              default is not None and default.default is None, default)
    # The odd-case suffix scan must not take a twin's suffix for source either (`._UNIT1.PAS`, `.AppleDouble/MAIN.DPR`).
    r = trees.one({"src/unit1.pas": "unit U;\n", "src/._UNIT1.PAS": APPLEDOUBLE, "src/.AppleDouble/MAIN.DPR": APPLEDOUBLE,
                   "__MACOSX/src/LIB.PAS": "unit L;\n"})
    check("G1 odd_case_source_suffixes ignores macOS metadata (no alias for a suffix only a twin carries)",
          GX.odd_case_source_suffixes(r) == [], GX.odd_case_source_suffixes(r))
    cmd, env, _err = _capture(r)
    check("G1 ...so such a tree runs the shim for the metadata only, with no suffix to alias",
          cmd == [sys.executable, str(BUILD.resolve() / "graphify_case_shim.py"), str(r.resolve())]
          and env.get(ENV_SKIP) == "1", (cmd, env.get(ENV_SKIP)))

    # G2 THE KEYSTONE: a tree without metadata starts the engine exactly as before (command AND environment).
    clean = trees.one({"src/main.py": "def f():\n    return 1\n", "._dir/y.py": "y = 1\n", ".hidden.py": "h = 1\n"})
    cmd, env, _err = _capture(clean)
    want = [sys.executable, "-m", "graphify", "update", str(clean.resolve()), "--no-cluster"]
    check("G2 no metadata -> the byte-identical `python -m graphify update <root> --no-cluster` command",
          cmd == want, cmd)
    check("G2 ...and the byte-identical environment (no DEEPMAP_SKIP_MACOS_METADATA)",
          env == _expected_env(clean), sorted(set(env) ^ set(_expected_env(clean))))
    cmd, env, _err = _capture(clean, **{ENV_SKIP: "1"})
    check("G2 an inherited DEEPMAP_SKIP_MACOS_METADATA=1 never reaches the engine (only this run's own decision does)",
          cmd == want and ENV_SKIP not in env, (cmd, env.get(ENV_SKIP)))
    meta = trees.one({"src/main.py": "def f():\n    return 1\n", "src/._main.py": APPLEDOUBLE})
    cmd, env, err = _capture(meta)
    shim = str(BUILD.resolve() / "graphify_case_shim.py")
    check("G2 metadata -> through the case shim, the root its only argument (no suffix to alias)",
          cmd == [sys.executable, shim, str(meta.resolve())], cmd)
    check("G2 ...with DEEPMAP_SKIP_MACOS_METADATA=1 in the engine's environment", env.get(ENV_SKIP) == "1",
          env.get(ENV_SKIP))
    check("G2 ...and everything else in that environment as before",
          {k: v for k, v in env.items() if k != ENV_SKIP} == _expected_env(meta),
          sorted(set(env) ^ set(_expected_env(meta))))
    check("G2 ...and the run says so in the log", "macOS metadata" in err, err[-300:])
    odd = trees.one({"src/UNIT1.PAS": "unit U;\n"})
    cmd, env, _err = _capture(odd)
    check("G2 an odd-case repo without metadata: the case shim as before, no DEEPMAP_SKIP_MACOS_METADATA",
          cmd[-1:] == [".PAS"] and shim in cmd and ENV_SKIP not in env, (cmd, env.get(ENV_SKIP)))
    cmd, env, _err = _capture(meta, DEEPMAP_CASE_INSENSITIVE_EXT="0")
    check("G2 the case-insensitive opt-out does not turn the metadata filter off",
          shim in cmd and env.get(ENV_SKIP) == "1", (cmd, env.get(ENV_SKIP)))
    # Round 4: a Delphi tree. Clean (skipped directories included): the plain command; metadata only under a
    # skipped out/ (listed by the Pascal resolvers' rglob): through the shim with the variable.
    delphi = trees.one({**DELPHI_CLEAN, "app/out/Main.exe": b"MZ\x00\x00", "app/node_modules/p/i.js": "x\n"})
    cmd, env, _err = _capture(delphi)
    check("G2 a clean Delphi tree with skipped directories: the byte-identical command and environment",
          cmd == [sys.executable, "-m", "graphify", "update", str(delphi.resolve()), "--no-cluster"]
          and env == _expected_env(delphi), (cmd, sorted(set(env) ^ set(_expected_env(delphi)))))
    delphi_meta = trees.one({**DELPHI_CLEAN, "app/out/.AppleDouble/Base.pas": APPLEDOUBLE})
    cmd, env, _err = _capture(delphi_meta)
    check("G2 a Delphi tree whose only metadata is app/out/.AppleDouble/Base.pas: through the shim, "
          "DEEPMAP_SKIP_MACOS_METADATA=1, everything else as before",
          cmd == [sys.executable, shim, str(delphi_meta.resolve())] and env.get(ENV_SKIP) == "1"
          and {k: v for k, v in env.items() if k != ENV_SKIP} == _expected_env(delphi_meta),
          (cmd, env.get(ENV_SKIP)))

    # G3 the shim's filter over a fake graphify.detect (the real one: section H; the import-time block: G4).
    fn = getattr(SH, "skip_macos_metadata", None)
    check("G3 graphify_case_shim.skip_macos_metadata exists", callable(fn))
    if callable(fn):
        root = trees.one({"x.py": "x = 1\n"}).resolve()
        listed = [str(root / "x.py"), str(root / "._x.py"), str(root / "routes" / "+page.ts"),
                  str(root / "routes" / "._+page.ts"), str(root / "__MACOSX" / "stray.py"),
                  str(root / ".AppleDouble" / "x.py"), str(root / "._dir" / "y.py"), str(root / ".github" / "ci.py")]
        calls = []

        def engine_detect(r, *, follow_symlinks=None, **kw):
            calls.append((r, follow_symlinks))
            return {"files": {"code": list(listed), "document": [str(root / "._README.md"), str(root / "README.md")],
                              "image": []}, "total_files": len(listed) + 2, "total_words": 7}

        with _fake_detect(engine_detect) as det:
            ok = fn()
            got = det.detect(root, follow_symlinks=False)
            check("G3 the patch is in place", ok is True and getattr(det.detect, "__wrapped__", None) is engine_detect)
            check("G3 the engine's own detect() still runs, with the caller's arguments",
                  calls == [(root, False)], calls)
            check("G3 every macOS-metadata path is gone from the file lists; nothing else is (._dir/, .github/ kept)",
                  got["files"]["code"] == [str(root / "x.py"), str(root / "routes" / "+page.ts"),
                                           str(root / "._dir" / "y.py"), str(root / ".github" / "ci.py")]
                  and got["files"]["document"] == [str(root / "README.md")] and got["files"]["image"] == [],
                  got["files"])
            check("G3 total_files follows the lists", got.get("total_files") == 5 and got.get("total_words") == 7,
                  got)
            outside = str(Path(tempfile.gettempdir()).resolve() / "elsewhere" / "._z.py")
            calls.clear()
            listed[:] = [outside, str(root / "x.py")]
            got = det.detect(str(root))
            check("G3 a listed path outside the root is judged by its name (a twin), never raises",
                  got["files"]["code"] == [str(root / "x.py")], got["files"]["code"])
            first = det.detect
            fn()
            check("G3 re-applying never double-wraps (it rewraps the engine's original)",
                  det.detect is not first and getattr(det.detect, "__wrapped__", None) is engine_detect)
        for label, impl in (("an engine result that is not a dict", lambda r, **kw: ["not", "a", "dict"]),
                            ("a result whose files is not a dict", lambda r, **kw: {"files": ["x"]}),
                            ("a list holding a non-path", lambda r, **kw: {"files": {"code": [None, 3]}})):
            with _fake_detect(impl) as det:
                fn()
                try:
                    out, raised = det.detect(root), None
                except Exception as e:  # noqa: BLE001
                    out, raised = None, e
                check(f"G3 {label}: handed back as the engine returned it, never raises",
                      raised is None and out == impl(root), (out, raised))
        with _fake_detect(None):
            check("G3 no detect symbol in the engine -> False, no patch, never raises", fn() is False)
        with _fake_detect("not callable"):
            check("G3 a non-callable detect -> False", fn() is False)
    src = (BUILD / "graphify_case_shim.py").read_text(encoding="utf-8")
    check("G3 the shim still never renames/moves/deletes the user's files",
          not any(tok in src for tok in ("os.rename", "shutil.move", "os.replace", "unlink", "rmtree")))

    # G4 the import-time block, in a fresh interpreter (a spawn worker re-imports the shim exactly like this).
    fake = trees.base / "fake_engine"
    _write(fake, {"graphify/__init__.py": "",
                  "graphify/detect.py": ("def detect(root, **kw):\n"
                                         "    return {'files': {'code': [str(root) + '/._x.py', str(root) + '/x.py']}}\n")})
    probe = ("import sys; sys.path[:0] = [sys.argv[1], sys.argv[2]]\n"
             "import graphify_case_shim\n"
             "import graphify.detect as d\n"
             "print(hasattr(d.detect, '__wrapped__'), d.detect(sys.argv[3])['files']['code'])\n")
    root = trees.one({"x.py": "x = 1\n"}).resolve()
    outs = {}
    for label, value in (("set", "1"), ("unset", None)):
        env = dict(os.environ, PYTHONUTF8="1")
        env.pop(ENV_SKIP, None)
        if value is not None:
            env[ENV_SKIP] = value
        p = subprocess.run([sys.executable, "-I", "-c", probe, str(fake), str(BUILD), str(root)],
                           capture_output=True, text=True, env=env, timeout=120)
        outs[label] = (p.returncode, p.stdout.strip(), p.stderr.strip()[-300:])
    check("G4 with DEEPMAP_SKIP_MACOS_METADATA=1, merely importing the shim patches detect() (what a spawn worker "
          "does)", outs["set"][0] == 0 and outs["set"][1].startswith("True") and "._x.py" not in outs["set"][1],
          outs["set"])
    check("G4 without it, importing the shim patches nothing", outs["unset"][0] == 0
          and outs["unset"][1].startswith("False") and "._x.py" in outs["unset"][1], outs["unset"])


# --- H. the REAL engine ---------------------------------------------------------------------------------------
_PAGE_TS = "export function load() {\n  return compute();\n}\n\nfunction compute() {\n  return 1;\n}\n"
_X_PY = "import os\n\n\ndef fx():\n    return helper()\n\n\ndef helper():\n    return os.sep\n"
ENGINE_CLEAN = {"x.py": _X_PY, "routes/+page.ts": _PAGE_TS}
ENGINE_META = {"._x.py": APPLEDOUBLE, "routes/._+page.ts": APPLEDOUBLE, "__MACOSX/._x.py": APPLEDOUBLE,
               "__MACOSX/stray.py": "def stray():\n    return 0\n", "__MACOSX/routes/._+page.ts": APPLEDOUBLE,
               ".AppleDouble/x.py": APPLEDOUBLE, ".AppleDouble/.Parent": APPLEDOUBLE,
               "routes/.AppleDouble/+page.ts": APPLEDOUBLE}


def _proj(res) -> tuple:
    """A root-independent, order-free view of an extraction result (nodes, calls, imports)."""
    nodes, calls, imports = res[0], res[1], res[2]
    return (sorted((str(n.get("key")), str(n.get("label")), str(n.get("name")), str(n.get("source_file")),
                    str(n.get("line"))) for n in nodes),
            sorted(tuple(str(x) for x in c) for c in calls),
            sorted(tuple(str(x) for x in i) for i in imports))


def _modules(res) -> dict:
    return {n.get("source_file"): n.get("name") for n in res[0] if n.get("label") == "Module"}


def gate_real_engine(trees: Trees) -> None:
    print("\n-- H. the REAL engine: no metadata node, every real file keeps its own Module --")
    if not _HAS_ENGINE:
        skip("graphify is not installed here: the real-engine checks (H1-H3) did not run")
        return
    note(f"graphify {GX.graphify_version() or '(unknown version)'}")
    with _env(DEEPMAP_EXTRACTOR="graphify", DEEPMAP_CASE_INSENSITIVE_EXT=None, DEEPMAP_RESCUE_SKIPPED_DIRS=None):
        # H1 the reviewer's tree
        clean, meta = trees.pair(ENGINE_CLEAN, ENGINE_META)
        (r_clean, rep_clean), _e = _quiet(GX.extract_with_report, clean)
        (r_meta, rep_meta), err = _quiet(GX.extract_with_report, meta)
        check("H1 the tree without metadata extracts (sanity)", bool(r_clean) and set(_modules(r_clean)) ==
              {"x.py", "routes/+page.ts"}, (_modules(r_clean) if r_clean else rep_clean.get("cause")))
        if not r_clean or not r_meta:
            check("H1 the metadata tree extracts", False, rep_meta.get("cause"))
            return
        files = {n.get("source_file") for n in r_meta[0]}
        check("H1 with ._x.py, routes/._+page.ts, __MACOSX/ and .AppleDouble/: nodes only for x.py and "
              "routes/+page.ts", files == {"x.py", "routes/+page.ts"}, sorted(files))
        check("H1 routes/+page.ts keeps its OWN Module (the twin no longer takes its engine id)",
              _modules(r_meta).get("routes/+page.ts") == _modules(r_clean).get("routes/+page.ts")
              and not str(_modules(r_meta).get("routes/+page.ts")).startswith("._"), _modules(r_meta))
        check("H1 the extraction (nodes, calls, imports) is identical to the tree without metadata",
              _proj(r_meta) == _proj(r_clean), [t for t in _proj(r_meta)[0] if t not in _proj(r_clean)[0]][:4])
        check("H1 the engine was told to leave the metadata out (and said so)",
              rep_meta.get("macos_metadata") is True and "macOS metadata" in err, (rep_meta.get("macos_metadata"),
                                                                                   err[-300:]))
        check("H1 the tree without metadata ran the plain engine command (no shim, nothing reported)",
              not rep_clean.get("macos_metadata"), rep_clean.get("macos_metadata"))
        (x_meta, _e2) = _quiet(S._rebuild_code, meta)
        (x_clean, _e3) = _quiet(S._rebuild_code, clean)
        check("H1 sync._rebuild_code (graphify) over the metadata tree gives the clean graph",
              x_meta[3] == "graphify" and _proj(x_meta) == _proj(x_clean), sorted({n.get("source_file")
                                                                                  for n in x_meta[0]}))

        # H2 above graphify's ProcessPoolExecutor threshold (>= 20 uncached files): spawn workers re-import the shim
        big = {f"pkg/m{i:02d}.py": f"def f{i}():\n    return g{i}()\n\n\ndef g{i}():\n    return {i}\n"
               for i in range(24)}
        big["routes/+page.ts"] = _PAGE_TS
        big_meta = _metadata_for({k: v for k, v in big.items()})
        big_meta.update({f"__MACOSX/{k}": v for k, v in big.items()})     # parseable copies under __MACOSX/
        clean, meta = trees.pair(big, big_meta)
        (r_clean, rep_clean), _e = _quiet(GX.extract_with_report, clean)
        (r_meta, rep_meta), _e = _quiet(GX.extract_with_report, meta)
        ok = bool(r_clean) and bool(r_meta)
        check("H2 25 real files (+ about 100 metadata files) extract, above the 20-file pool threshold", ok,
              (rep_clean.get("cause"), rep_meta.get("cause")))
        if ok:
            check("H2 every real file keeps its own Module (25)", len(_modules(r_meta)) == 25
                  and _modules(r_meta) == _modules(r_clean), sorted(set(_modules(r_meta)) ^ set(_modules(r_clean))))
            check("H2 no node from a metadata file, the extraction identical to the tree without metadata",
                  _proj(r_meta) == _proj(r_clean),
                  sorted({n.get("source_file") for n in r_meta[0]} - set(big))[:4])

        # H3 inside a re-included engine-skipped directory (a committed twin beside tracked source in build/)
        if GX._engine_noise_dir() is None or not GX.graphify_support()[0] or not shutil.which("git"):
            skip("H3 needs git and graphify's skip rule inside the supported range")
            return
        resc = {"src/main.py": "def main():\n    return 0\n", "build/routes/+page.ts": _PAGE_TS,
                "build/tool.py": "def tool():\n    return 1\n"}
        resc_meta = {"build/routes/._+page.ts": APPLEDOUBLE, "build/._tool.py": APPLEDOUBLE,
                     "build/.AppleDouble/tool.py": APPLEDOUBLE, "build/routes/.AppleDouble/+page.ts": APPLEDOUBLE}
        clean, meta = trees.pair(resc, resc_meta)
        if not (_git_tree(clean) and _git_tree(meta)):
            skip("H3 git could not commit the fixture")
            return
        (r_clean, rep_clean), _e = _quiet(GX.extract_with_report, clean)
        (r_meta, rep_meta), _e = _quiet(GX.extract_with_report, meta)
        ok = bool(r_clean) and bool(r_meta)
        check("H3 both trees extract with build/ re-included", ok and rep_clean.get("rescued_dirs") == ["build"]
              and rep_meta.get("rescued_dirs") == ["build"], (rep_clean.get("rescued_dirs"),
                                                              rep_meta.get("rescued_dirs")))
        if ok:
            check("H3 build/routes/+page.ts keeps its own Module (it is on the rescue keep list; its twin never "
                  "reached the engine)", _modules(r_meta).get("build/routes/+page.ts") is not None
                  and _modules(r_meta) == _modules(r_clean), _modules(r_meta))
            check("H3 the extraction is identical to the tree without metadata, nothing dropped beside the rescue",
                  _proj(r_meta) == _proj(r_clean) and not rep_meta.get("rescue_dropped"),
                  (rep_meta.get("rescue_dropped"), [t for t in _proj(r_meta)[0] if t not in _proj(r_clean)[0]][:4]))


# --- I. sync's node backstop --------------------------------------------------------------------------------
def gate_sync_backstop(trees: Trees) -> None:
    print("\n-- I. sync._drop_macos_metadata: the node backstop at sync's convergence point --")
    fn = getattr(S, "_drop_macos_metadata", None)
    check("I1 sync._drop_macos_metadata exists", callable(fn))
    if not callable(fn):
        return
    root = trees.one({"x.py": _X_PY}).resolve()

    def node(key, label, name, sf):
        return {"key": key, "label": label, "name": name, "source_file": sf, "line": 1}

    real = [node("x_py_x", "Module", "x", "x.py"), node("x_fx", "Function", "fx", "x.py"),
            node("gh_ci", "Function", "ci", ".github/ci.py"), node("dir_y", "Function", "y", "._dir/y.py")]
    junk = [node("routes_page_ts_routes_page", "Module", "._+page", "routes/._+page.ts"),
            node("macosx_stray", "Function", "stray", "__MACOSX/stray.py"),
            node("appledouble_x", "Module", "x", ".AppleDouble/x.py"),
            node("abs_twin", "Module", "._a", str(root / "pkg" / "._a.py")),
            node("win_twin", "Module", "._b", "pkg\\._b.py")]
    nodes = real[:2] + junk + real[2:]
    calls = [("x_fx", "helper"), ("macosx_stray", "fx"), ("x_fx", "appledouble_x"),
             ("x_fx", GX.call_key("appledouble_x")), ("x_fx", GX.call_key("dir_y"))]
    imports = [("x_py_x", "os"), ("routes_page_ts_routes_page", "util"), ("x_py_x", "appledouble_x"),
               ("abs_twin", "x_py_x")]
    with _env(DEEPMAP_PRECISE_CALLS="1"):
        (got, err) = _quiet(fn, root, nodes, calls, imports)
    n2, c2, i2 = got
    check("I1 every metadata node is dropped (a twin, __MACOSX/, .AppleDouble/, an absolute path, a Windows path); "
          "._dir/ and .github/ kept", [n["key"] for n in n2] == [n["key"] for n in real], [n["key"] for n in n2])
    check("I1 their calls go with them; a keyed call INTO a dropped node too; a NAME equal to a dropped key stays",
          [(str(a), str(b)) for a, b in c2] == [("x_fx", "helper"), ("x_fx", "appledouble_x"), ("x_fx", "dir_y")],
          c2)
    check("I1 their imports go with them, and an import resolved to a dropped node's key",
          i2 == [("x_py_x", "os")], i2)
    check("I1 the drop is said on stderr", "macOS metadata" in err, err)
    clean_nodes, clean_calls, clean_imports = list(real), [("x_fx", "helper")], [("x_py_x", "os")]
    (same, err) = _quiet(fn, root, clean_nodes, clean_calls, clean_imports)
    check("I1 nothing to drop -> the SAME objects back, nothing printed (byte-identity downstream)",
          same[0] is clean_nodes and same[1] is clean_calls and same[2] is clean_imports and not err, err)

    # I2 through _rebuild_code: an engine result still holding metadata (an engine the shim could not patch).
    saved = S.graphify_extract.extract_with_report
    S.graphify_extract.extract_with_report = lambda r, **kw: (
        (list(nodes), [c for c in calls if not GX.is_call_key(c[1])], list(imports)),
        {"resolved_imports": True, "graphify_version": "test"})
    try:
        with _env(DEEPMAP_EXTRACTOR="graphify"):
            (ex, err) = _quiet(S._rebuild_code, root)
        check("I2 sync._rebuild_code drops them whatever produced them",
              ex[3] == "graphify" and [n["key"] for n in ex[0]] == [n["key"] for n in real]
              and ("macosx_stray", "fx") not in ex[1] and ("routes_page_ts_routes_page", "util") not in ex[2],
              ([n["key"] for n in ex[0]], ex[1], ex[2]))
        S.graphify_extract.extract_with_report = lambda r, **kw: ((list(junk), [], []), {})
        with _env(DEEPMAP_EXTRACTOR="auto"):
            (ex, err) = _quiet(S._rebuild_code, root)
        check("I2 an engine result that is ALL metadata counts as empty: auto falls back to ast",
              ex[3] == "ast" and {n["source_file"] for n in ex[0]} == {"x.py"}, (ex[3], ex[0][:2]))
    finally:
        S.graphify_extract.extract_with_report = saved


# --- J. graphify's own disk walks outside detect() ------------------------------------------------------------
# Every call in the engine's package that lists a directory (an AST scan: a path's .glob / .rglob / .iterdir /
# .walk / .scandir / .listdir, os.walk / scandir / listdir, glob.glob / iglob), on both ends of the supported
# range (0.8.39 and 0.8.51), and why macOS metadata cannot reach the graph through it:
#   detect    the engine's file discovery, os.walk: graphify_case_shim.skip_macos_metadata filters its result;
#   pathlib   a pathlib listing of the project tree while the engine extracts: graphify_case_shim
#             .hide_macos_metadata_listings drops the metadata from what Path.glob / rglob / iterdir yield;
#   output    graphify's own output directory (GRAPHIFY_OUT, under DEEPMAP_HOME), never the project;
#   off-path  never reached by the `graphify update --no-cluster` DeepMap runs (another graphify command or API).
# A walk that is not listed FAILS J1: decide which of these it is (or what else it needs), then add it.
_ENGINE_WALKS = {
    ("detect.py", "detect", "os.walk"): "detect",
    ("detect.py", "_auto_follow_symlinks", "<path>.iterdir"): "pathlib",     # not consulted: update passes False
    ("extract.py", "_pascal_project_root", "<path>.glob"): "pathlib",        # counts *.pas / *.dpr children
    ("extract.py", "_pascal_resolve_unit", "<path>.rglob"): "pathlib",       # unit name -> file (stem map)
    ("extract.py", "_pascal_resolve_class", "<path>.rglob"): "pathlib",      # TFoo -> Foo.pas (stem map)
    ("extract.py", "_load_workspace_packages", "<path>.glob"): "pathlib",    # pnpm-workspace package dirs
    ("extract.py", "_xaml_project_root", "<path>.iterdir"): "pathlib",       # 0.8.51: .csproj / .sln markers
    ("extract.py", "_xaml_codebehind_path", "<path>.iterdir"): "pathlib",    # 0.8.51: X.xaml.cs, by casefold
    ("extract.py", "_xaml_csharp_class_nodes", "<path>.rglob"): "pathlib",   # 0.8.51: *.cs ViewModel classes
    ("extract.py", "collect_files", "os.walk"): "off-path",                  # graphify's extract API / CLI
    ("cache.py", "_cleanup_stale_ast_entries", "<path>.iterdir"): "output",
    ("cache.py", "cached_files", "<path>.glob"): "output",
    ("cache.py", "clear_cache", "<path>.glob"): "output",
    ("watch.py", "_rebuild_code", "<path>.glob"): "output",                  # *-callflow.html in GRAPHIFY_OUT
    ("wiki.py", "to_wiki", "<path>.glob"): "off-path",
    ("transcribe.py", "download_audio", "<path>.glob"): "off-path",
    ("cargo_introspect.py", "_member_manifest_paths", "<path>.glob"): "off-path",   # `graphify extract --cargo`
    ("reflect.py", "lessons_fresh", "<path>.glob"): "off-path",              # 0.8.51 `graphify reflect`
    ("reflect.py", "load_memory_docs", "<path>.glob"): "off-path",
    ("__main__.py", "main", "glob.glob"): "off-path",                        # `graphify merge-chunks`
}
_LISTING_ATTRS = {"glob", "rglob", "iterdir", "walk", "scandir", "listdir", "iglob"}


def _engine_walks(pkg: Path) -> set:
    """{(file, enclosing function, kind)} for every directory listing in the engine package `pkg` (see
    _ENGINE_WALKS). A local tree-walk helper (`def walk(node)` called as `walk(child)`) and `ast.walk` are not disk
    walks; `os` / `glob` imported under another name, or a listing function imported from them, still count."""
    import ast
    found = set()
    for f in sorted(pkg.rglob("*.py")):
        if "__pycache__" in f.parts:
            continue
        tree = ast.parse(f.read_text(encoding="utf-8"), str(f))
        mods, funcs = {}, {}
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                for a in n.names:
                    if a.name in ("os", "glob"):
                        mods[a.asname or a.name] = a.name
            elif isinstance(n, ast.ImportFrom) and n.module in ("os", "glob"):
                for a in n.names:
                    if a.name in _LISTING_ATTRS:
                        funcs[a.asname or a.name] = f"{n.module}.{a.name}"
        rel, stack = f.relative_to(pkg).as_posix(), []

        class _V(ast.NodeVisitor):
            def visit_FunctionDef(self, n):
                stack.append(n.name)
                self.generic_visit(n)
                stack.pop()

            visit_AsyncFunctionDef = visit_FunctionDef

            def visit_Call(self, n):
                fn, kind = n.func, None
                if isinstance(fn, ast.Attribute) and fn.attr in _LISTING_ATTRS:
                    recv = fn.value
                    if isinstance(recv, ast.Name) and recv.id in mods:
                        kind = f"{mods[recv.id]}.{fn.attr}"
                    elif not (isinstance(recv, ast.Name) and recv.id == "ast"):
                        kind = f"<path>.{fn.attr}"
                elif isinstance(fn, ast.Name) and fn.id in funcs:
                    kind = funcs[fn.id]
                if kind:
                    found.add((rel, stack[0] if stack else "<module>", kind))
                self.generic_visit(n)

        _V().visit(tree)
    return found


# The listing fixture for J2 (relative paths; bytes = an AppleDouble header). METADATA: what a listing must drop.
_LISTING_TREE = {"Unit1.pas": "unit Unit1;\n", "._Unit1.pas": APPLEDOUBLE, "Main.dpr": "program Main;\n",
                 "._Main.dpr": APPLEDOUBLE, "._Gone.pas": APPLEDOUBLE,           # an orphan twin (its original gone)
                 ".AppleDouble/Unit1.pas": APPLEDOUBLE, ".AppleDouble/.Parent": APPLEDOUBLE,
                 "__MACOSX/._Unit1.pas": APPLEDOUBLE, "__MACOSX/app/Stray.pas": "unit Stray;\n",
                 "__MACOSX/proj/P.pas": "unit P;\n",
                 "._dir/Kept.pas": "unit Kept;\n", "__MACOSX_tools/Tool.pas": "unit Tool;\n", "x._y.pas": "unit Y;\n",
                 ".hidden.pas": "unit H;\n", "sub/__MACOSX": "a FILE named __MACOSX\n", "sub/A.pas": "unit A;\n",
                 "sub/._A.pas": APPLEDOUBLE, "sub/.AppleDouble/A.pas": APPLEDOUBLE}
_LISTING_META_TOPS = ("._Unit1.pas", "._Main.dpr", "._Gone.pas", ".AppleDouble", "__MACOSX", "sub/._A.pas",
                      "sub/.AppleDouble")

_LISTING_PROBE = r"""
import json, pathlib, sys
build, root = sys.argv[1], pathlib.Path(sys.argv[2])
sys.path.insert(0, build)
names = ("glob", "rglob", "iterdir")
orig = {n: getattr(pathlib.Path, n) for n in names}
import graphify_case_shim as SH
now = {n: getattr(pathlib.Path, n) for n in names}

def rel(paths, base):
    return [p.relative_to(base).as_posix() for p in paths]

def stage(fn):
    try:
        it = fn()
    except Exception as e:
        return ["call", type(e).__name__]
    try:
        list(it)
    except Exception as e:
        return ["iterate", type(e).__name__]
    return ["ok", None]

calls = {"iterdir": lambda f: f(root), "glob *.pas": lambda f: f(root, "*.pas"), "rglob *.pas": lambda f: f(root, "*.pas"),
         "glob **/*": lambda f: f(root, "**/*"), "rglob *": lambda f: f(root, "*")}
which = {"iterdir": "iterdir", "glob *.pas": "glob", "rglob *.pas": "rglob", "glob **/*": "glob", "rglob *": "rglob"}
out = {"patched": {n: now[n] is not orig[n] for n in names},
       "wraps_original": {n: getattr(now[n], "__wrapped__", None) is orig[n] for n in names},
       "listings": {k: [rel(c(orig[which[k]]), root), rel(c(getattr(pathlib.Path, which[k])), root)]
                    for k, c in calls.items()}}
under = root / "__MACOSX" / "proj"
out["under"] = rel(under.rglob("*.pas"), under)
out["errors"] = {
    "glob('')": [stage(lambda: orig["glob"](root, "")), stage(lambda: root.glob(""))],
    "iterdir(missing)": [stage(lambda: orig["iterdir"](root / "no-such-dir")), stage(lambda: (root / "no-such-dir").iterdir())],
}
if hasattr(SH, "hide_macos_metadata_listings"):
    out["again"] = SH.hide_macos_metadata_listings()
    out["again_wraps_original"] = {n: getattr(getattr(pathlib.Path, n), "__wrapped__", None) is orig[n] for n in names}
print(json.dumps(out))
"""


def _is_listing_meta(rel: str) -> bool:
    return any(rel == t or rel.startswith(t + "/") for t in _LISTING_META_TOPS)


def gate_engine_walks(trees: Trees) -> None:
    print("\n-- J. graphify's own disk walks outside detect(): Pascal/Delphi resolvers, pathlib listings --")
    # J1 every directory listing in the installed engine is accounted for.
    spec = importlib.util.find_spec("graphify")
    if spec is None or not spec.submodule_search_locations:
        skip("J1 graphify is not installed here: its disk walks were not inventoried")
    else:
        pkg = Path(list(spec.submodule_search_locations)[0])
        found = _engine_walks(pkg)
        unknown = sorted(w for w in found if w not in _ENGINE_WALKS)
        check("J1 every directory listing in the installed engine is accounted for (detect / pathlib / output / "
              "off-path)", not unknown, unknown)
        listed = sorted(w for w in found if _ENGINE_WALKS.get(w) == "pathlib")
        check("J1 the project-tree walks outside detect() are all pathlib listings (glob / rglob / iterdir)",
              all(k.split(".")[-1] in ("glob", "rglob", "iterdir") and k.startswith("<path>.") for _f, _fn, k in listed),
              listed)
        check("J1 the three Pascal/Delphi walks are among them (both ends of the supported range have them)",
              {("extract.py", f, k) for f, k in (("_pascal_project_root", "<path>.glob"),
                                                  ("_pascal_resolve_unit", "<path>.rglob"),
                                                  ("_pascal_resolve_class", "<path>.rglob"))} <= found,
              sorted(w for w in found if "pascal" in w[1]))
        note(f"graphify {GX.graphify_version() or '?'}: {len(found)} directory listings; outside detect(), on the "
             f"project tree: {', '.join(f'{fn} ({k[7:]})' for _f, fn, k in listed)}")

    # J2 the pathlib listing filter, in a fresh interpreter (exactly what the engine child and a spawn worker do).
    root = trees.one(_LISTING_TREE).resolve()
    outs = {}
    for label, value in (("set", "1"), ("unset", None)):
        env = dict(os.environ, PYTHONUTF8="1")
        env.pop(ENV_SKIP, None)
        if value is not None:
            env[ENV_SKIP] = value
        p = subprocess.run([sys.executable, "-I", "-c", _LISTING_PROBE, str(BUILD), str(root)],
                           capture_output=True, text=True, env=env, timeout=120)
        try:
            outs[label] = json.loads(p.stdout.strip().splitlines()[-1])
        except Exception:  # noqa: BLE001
            outs[label] = {"rc": p.returncode, "stderr": p.stderr[-400:]}
    s, u = outs["set"], outs["unset"]
    check("J2 with DEEPMAP_SKIP_MACOS_METADATA=1, importing the shim wraps Path.glob / rglob / iterdir (the "
          "engine's originals kept as __wrapped__)", s.get("patched") == {"glob": True, "rglob": True, "iterdir": True}
          and s.get("wraps_original") == {"glob": True, "rglob": True, "iterdir": True}, s)
    check("J2 without it, importing the shim leaves pathlib untouched (the same functions)",
          u.get("patched") == {"glob": False, "rglob": False, "iterdir": False}, u)
    bad = {}
    for k, (ref, got) in (s.get("listings") or {}).items():
        want = [r for r in ref if not _is_listing_meta(r)]
        if got != want:
            bad[k] = {"extra": [g for g in got if g not in want], "missing": [w for w in want if w not in got],
                      "order": got != want}
    check("J2 each listing yields exactly the engine's own result minus the macOS metadata, in the same order "
          "(iterdir, glob *.pas, rglob *.pas, glob **/*, rglob *)", bool(s.get("listings")) and not bad, bad)
    every = set().union(*[set(got) for _ref, got in (s.get("listings") or {}).values()]) if s.get("listings") else set()
    check("J2 ...the shapes that are not metadata are kept: a DIRECTORY named ._dir, a FILE named __MACOSX, "
          "__MACOSX_tools/, x._y.pas, .hidden.pas",
          {"._dir", "._dir/Kept.pas", "sub/__MACOSX", "__MACOSX_tools/Tool.pas", "x._y.pas", ".hidden.pas"} <= every,
          sorted(every))
    check("J2 ...and the orphan twin ._Gone.pas (no original) is dropped too",
          "._Gone.pas" not in every and any("._Gone.pas" in ref for ref, _g in (s.get("listings") or {}).values()),
          sorted(every))
    check("J2 a listing of a directory that itself sits under __MACOSX/ keeps its own files (judged below it)",
          s.get("under") == ["P.pas"], s.get("under"))
    errs = s.get("errors") or {}
    check("J2 the engine's own errors are unchanged, raised at the same point (glob(''), iterdir of a missing dir)",
          bool(errs) and all(a == b for a, b in errs.values()), errs)
    check("J2 re-applying is idempotent: True, and still one wrapper over the engine's originals",
          s.get("again") is True and s.get("again_wraps_original") == {"glob": True, "rglob": True, "iterdir": True},
          (s.get("again"), s.get("again_wraps_original")))
    check("J2 without the variable every listing is the engine's own, byte for byte",
          bool(u.get("listings")) and all(ref == got for ref, got in u["listings"].values()), u.get("listings"))

    # J3/J4 the REAL engine over Delphi trees.
    if not _HAS_ENGINE:
        skip("graphify is not installed here: the real-engine Delphi checks (J3, J4) did not run")
        return
    note(f"graphify {GX.graphify_version() or '(unknown version)'}")
    with _env(DEEPMAP_EXTRACTOR="graphify", DEEPMAP_CASE_INSENSITIVE_EXT=None, DEEPMAP_RESCUE_SKIPPED_DIRS=None):
        twins = {}
        for rel in DELPHI_CLEAN:
            d, _, name = rel.rpartition("/")
            twins[f"{d}/._{name}" if d else f"._{name}"] = APPLEDOUBLE
        twins.update({"._app": APPLEDOUBLE, "._shared": APPLEDOUBLE})
        netatalk = {}
        for rel in DELPHI_CLEAN:
            d, _, name = rel.rpartition("/")
            netatalk[f"{d}/.AppleDouble/{name}" if d else f".AppleDouble/{name}"] = APPLEDOUBLE
            netatalk[f"{d}/.AppleDouble/.Parent" if d else ".AppleDouble/.Parent"] = APPLEDOUBLE
        full = _metadata_for(DELPHI_CLEAN)
        full.update({"__MACOSX/app/Unit1.pas": DELPHI_CLEAN["app/Unit1.pas"],     # parseable copies under __MACOSX/
                     "__MACOSX/app/Base.pas": DELPHI_CLEAN["app/Base.pas"]})
        # Round 4: metadata ONLY under a directory detect() skips — the Pascal resolvers' rglob skips nothing.
        skipped_only = {
            "only app/out/.AppleDouble/Base.pas (out/ is skipped by detect(), not by the resolvers)":
                {"app/out/.AppleDouble/Base.pas": APPLEDOUBLE},
            "only app/build/.AppleDouble/Unit1.pas": {"app/build/.AppleDouble/Unit1.pas": APPLEDOUBLE},
            "only app/node_modules/__MACOSX/Base.pas": {"app/node_modules/__MACOSX/Base.pas": APPLEDOUBLE},
        }
        twins_skipped = {"app/out/._Base.pas": APPLEDOUBLE, "app/build/._Unit1.pas": APPLEDOUBLE,
                         "app/out/._Main.dpr": APPLEDOUBLE}
        engaged = {}
        for label, meta_files in (("._ twins (one beside Common.pas makes the root count two .pas files)", twins),
                                  ("Netatalk .AppleDouble/ copies under the ORIGINAL names (Unit1.pas, Base.pas)",
                                   netatalk),
                                  ("every kind at once, with parseable copies under __MACOSX/", full),
                                  *skipped_only.items(),
                                  ("only ._ twins under out/ and build/ (a twin's stem is ._base: nothing to take "
                                   "over)", twins_skipped)):
            clean, meta = trees.pair(DELPHI_CLEAN, meta_files)
            (r_clean, rep_clean), _e = _quiet(GX.extract_with_report, clean)
            (r_meta, rep_meta), _e = _quiet(GX.extract_with_report, meta)
            if not r_clean or not r_meta:
                check(f"J3 [{label}] both Delphi trees extract", False, (rep_clean.get("cause"), rep_meta.get("cause")))
                continue
            engaged[label] = bool(rep_meta.get("macos_metadata"))
            pc, pm = _proj(r_clean), _proj(r_meta)
            check(f"J3 [{label}] sanity: the clean tree resolves its `uses` units (the resolvers ran) and its calls",
                  len(pc[2]) >= 3 and len(pc[1]) >= 1, (pc[1], pc[2]))
            check(f"J3 [{label}] the Delphi graph (nodes, calls, imports) is identical to the clean tree",
                  pm == pc, {"nodes+": [t for t in pm[0] if t not in pc[0]][:3],
                             "nodes-": [t for t in pc[0] if t not in pm[0]][:3],
                             "imports+": [t for t in pm[2] if t not in pc[2]][:4],
                             "imports-": [t for t in pc[2] if t not in pm[2]][:4]})
            helper = [i for i in pm[2] if "helper" in i[1].lower()]
            check(f"J3 [{label}] Unit1's `uses Helper` (another directory) stays unresolved, as in the clean tree "
                  f"(a twin no longer turns the repo root into the Pascal project root)",
                  helper == [i for i in pc[2] if "helper" in i[1].lower()] == [], helper)
        got = {k: engaged.get(k) for k in skipped_only}
        check("J3 metadata only under a skipped directory of a Delphi tree engages the filter (the pre-scan found it)",
              all(v is True for v in got.values()), got)
        tl = next((k for k in engaged if k.startswith("only ._ twins")), None)
        check("J3 ._ twins only under skipped directories do NOT engage it, and the graph is the clean tree's anyway "
              "(the twin-stem premise, on this engine)", tl is not None and engaged.get(tl) is False,
              engaged.get(tl))

        # J3 above the sync root: the project sits in a directory holding two .pas files, so the engine's Pascal
        # project root is that directory and its unpruned rglob lists the project's siblings; and a directory above
        # holding one .pas and its ._ twin counts two (the round-3 shape, one level up).
        above = {"A.pas": _pas_unit("A"), "B.pas": _pas_unit("B"), **_under("proj", DELPHI_CLEAN)}
        lone = {"A.pas": _pas_unit("A"), **_under("proj", DELPHI_CLEAN)}
        for label, files, meta_files in (
                ("other/.AppleDouble/Base.pas beside the project, the Pascal root above it", above,
                 {"other/.AppleDouble/Base.pas": APPLEDOUBLE}),
                ("a ._A.pas twin in the directory above (with A.pas it counts two)", lone, {"._A.pas": APPLEDOUBLE})):
            clean, meta = trees.pair(files, meta_files)
            (r_clean, rep_clean), _e = _quiet(GX.extract_with_report, clean / "proj")
            (r_meta, rep_meta), _e = _quiet(GX.extract_with_report, meta / "proj")
            if not r_clean or not r_meta:
                check(f"J3 above [{label}] both Delphi trees extract", False,
                      (rep_clean.get("cause"), rep_meta.get("cause")))
                continue
            pc, pm = _proj(r_clean), _proj(r_meta)
            check(f"J3 above [{label}] sanity: the clean tree resolves its `uses` units", len(pc[2]) >= 3, pc[2])
            check(f"J3 above [{label}] the graph (nodes, calls, imports) is identical to the clean tree's, the filter "
                  f"engaged", pm == pc and rep_meta.get("macos_metadata") is True,
                  {"engaged": rep_meta.get("macos_metadata"),
                   "nodes+": [t for t in pm[0] if t not in pc[0]][:3],
                   "nodes-": [t for t in pc[0] if t not in pm[0]][:3],
                   "imports+": [t for t in pm[2] if t not in pc[2]][:4],
                   "imports-": [t for t in pc[2] if t not in pm[2]][:4]})

        # J4 above graphify's 20-file pool threshold: the resolvers run in the (spawn) workers.
        big = {"Common.pas": DELPHI_CLEAN["Common.pas"], "app/Base.pas": DELPHI_CLEAN["app/Base.pas"],
               "shared/Helper.pas": DELPHI_CLEAN["shared/Helper.pas"]}
        for i in range(24):
            big[f"app/Unit{i:02d}.pas"] = DELPHI_CLEAN["app/Unit1.pas"].replace("Unit1", f"Unit{i:02d}").replace(
                "TWorker", f"TWorker{i:02d}")
        big["app/Main.dpr"] = ("program Main;\nuses " + ", ".join(f"Unit{i:02d}" for i in range(24))
                               + ", Base;\nbegin\nend.\n")
        big_meta = _metadata_for(big)
        clean, meta = trees.pair(big, big_meta)
        (r_clean, rep_clean), _e = _quiet(GX.extract_with_report, clean)
        (r_meta, rep_meta), _e = _quiet(GX.extract_with_report, meta)
        ok = bool(r_clean) and bool(r_meta)
        check(f"J4 {len(big)} Delphi files (+ {len(big_meta)} metadata files) extract, above the 20-file pool "
              f"threshold", ok, (rep_clean.get("cause"), rep_meta.get("cause")))
        if ok:
            pc, pm = _proj(r_clean), _proj(r_meta)
            check("J4 sanity: the clean tree resolves every unit's `uses Base` and Main's 25 units",
                  len(pc[2]) >= 49, len(pc[2]))
            check("J4 every Delphi file keeps its own Module, the graph (nodes, calls, imports) identical to the clean "
                  "tree", pm == pc and _modules(r_meta) == _modules(r_clean),
                  {"imports+": [t for t in pm[2] if t not in pc[2]][:4],
                   "imports-": [t for t in pc[2] if t not in pm[2]][:4],
                   "nodes+": [t for t in pm[0] if t not in pc[0]][:3]})


_PAS_UNIT1 = ("unit Unit1;\ninterface\nuses Base;\ntype\n  TWorker = class(TBase)\n  public\n"
              "    function Run(A: Integer): Integer;\n    function Calc(A: Integer): Integer;\n  end;\n"
              "implementation\nuses Helper;\n"
              "function TWorker.Calc(A: Integer): Integer;\nbegin\n  Result := A * 2;\nend;\n"
              "function TWorker.Run(A: Integer): Integer;\nbegin\n  Result := Calc(A);\nend;\nend.\n")
_PAS_BASE = ("unit Base;\ninterface\ntype\n  TBase = class\n  public\n    function Twice(A: Integer): Integer;\n"
             "  end;\nimplementation\nfunction TBase.Twice(A: Integer): Integer;\nbegin\n  Result := A * 2;\nend;\n"
             "end.\n")
# One .pas file at the root (a twin beside it made the root count two), the program and its units in app/, a unit
# only the root-wide walk can see in shared/.
DELPHI_CLEAN = {"Common.pas": "unit Common;\ninterface\nimplementation\nend.\n",
                "app/Main.dpr": "program Main;\nuses Unit1, Base;\nbegin\nend.\n",
                "app/Unit1.pas": _PAS_UNIT1, "app/Base.pas": _PAS_BASE,
                "shared/Helper.pas": ("unit Helper;\ninterface\nfunction Help: Integer;\nimplementation\n"
                                      "function Help: Integer;\nbegin\n  Result := 1;\nend;\nend.\n")}


def main() -> int:
    print("=" * 78)
    print("macOS METADATA GATES - AppleDouble ._ twins, __MACOSX/ and .AppleDouble/ are never source (no Neo4j)")
    print("=" * 78)
    base = Path(tempfile.mkdtemp(prefix="dm_appledouble_gate_"))
    home, saved = base / "home", os.environ.get("DEEPMAP_HOME")
    home.mkdir()
    os.environ["DEEPMAP_HOME"] = str(home)          # graphify's output (gout/<key>) lands here, never ~/.deepmap
    try:
        trees = Trees(base)
        gate_predicate()
        for g in (gate_sync, gate_verdict, gate_framework_edges, gate_recognizers, gate_graphify_extract,
                  gate_engine_path_hermetic, gate_real_engine, gate_sync_backstop, gate_engine_walks):
            try:
                g(trees)
            except Exception as e:  # noqa: BLE001 — a crashing section is a failing section, never a silent pass
                check(f"{g.__name__} ran to the end", False, f"{type(e).__name__}: {e}")
    finally:
        if saved is None:
            os.environ.pop("DEEPMAP_HOME", None)
        else:
            os.environ["DEEPMAP_HOME"] = saved
        _rmtree(base)
    print("\n" + "=" * 78)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if not _FAIL else 'RED'} - {_PASS}/{total} checks passed.")
    return 0 if not _FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
