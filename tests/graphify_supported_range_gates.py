#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gates for the graphify SUPPORTED RANGE (round-4 decision F3). No Neo4j, no network.

The defect (round-3 review, high + medium): the E3 pin `graphifyy>=0.8.39,<0.9` resolves to graphify
0.8.51 on every fresh install and in CI, but only 0.8.39 had been validated. 0.8.51's parser table
dispatches `.cu`/`.cuh`/`.metal` (CUDA + Metal, parsed with the C++ grammar) and `.psd1`/`.xaml`, none of
which DeepMap had decided: map_graph dropped every CUDA/Metal node while the census had no language for
them, so the verdict said READY over a graph with no GPU code in it (the Delphi-class silent drop), and
tests/delphi_pascal_gates.py's drift lock went RED under 0.8.51.

F3 (binding): the supported range is exactly what was validated — 0.8.39 AND 0.8.51, by running the
graphify-dependent suites on both — so `SUPPORTED_GRAPHIFY_SPEC = 'graphifyy>=0.8.39,<0.8.52'`; CUDA/Metal
go into SOURCE_CODE_EXTS with a verdict-census language (the C/C++ family); `.psd1` (a PowerShell data
manifest) and `.xaml` (UI markup) are excluded by design.

What this gate locks, beyond the literal decision:
  A. the spec literal and the (MIN, BELOW) tuples graphify_support() compares agree with each other;
  B. the WHOLE range, not just whichever engine happens to be installed: a frozen snapshot of the parser
     table of every graphifyy release inside the range (0.8.39 .. 0.8.51, statically diffed from the 12
     published wheels) — every suffix any in-range engine parses is decided, and graphify_extract's own
     table of the parsers that appeared INSIDE the range (ENGINE_EXT_SINCE) matches the snapshot;
  C. that snapshot is REAL: when graphify is installed at a snapshotted version, its live `_DISPATCH` is
     exactly the snapshotted set (0.8.39 on the dev box, 0.8.51 on CI);
  D. a REAL captured graphify-0.8.51 graph of a CUDA + Metal + XAML + .psd1 tree maps to typed CUDA/Metal
     nodes + their CALLS/IMPORTS, and drops the XAML/.psd1 nodes;
  E. the census counts CUDA/Metal and not .xaml/.psd1;
  F. honesty on the LOWER end of the range: 0.8.39 is supported but has no parser for .cu/.cuh/.metal (nor
     .psm1) — the verdict names exactly those files, the release that parses them and the runnable
     `--upgrade` command, instead of a bare READY (or a density heuristic that a .cpp-heavy repo masks);
     never for a suffix no in-range engine parses (.pyi stubs next to .py must not DEGRADE a repo);
  G. every remedy prints DeepMap's interpreter QUOTED with forward slashes, so it survives Git Bash (the
     shell Claude Code runs on Windows), which eats the backslashes of an unquoted `C:\\...\\python.exe`;
  H. the REAL installed engine end to end over a CUDA/Metal tree (whichever branch the installed version
     takes: CUDA nodes in the graph on >= 0.8.46, the named upgrade reason on 0.8.39);
  I. the one in-range RE-ROUTE: 0.8.50 moved `.vue` from extract_js to extract_vue (script block only,
     plus a new `dynamic_import` relation) — a REAL captured 0.8.51 SFC graph maps to the right typed
     nodes/CALLS/IMPORTS with no duplicate or stub leak, and the installed engine agrees live.
"""
from __future__ import annotations

import contextlib
import importlib.util
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "build"))

import graphify_extract as GX  # noqa: E402
import verdict as V  # noqa: E402

_PASS = _FAIL = 0
_HAS_ENGINE = importlib.util.find_spec("graphify") is not None

WANT_SPEC = "graphifyy>=0.8.39,<0.8.52"

# --- the frozen parser tables of every graphifyy release inside the range --------------------------
# Source: the 13 wheels 0.8.39 .. 0.8.51 — 0.8.48 included: it WAS published (2026-06-25) and then
# YANKED, so `pip index versions` hides it and a range spec never selects it, but `==0.8.48` still
# installs it and graphify_support() accepts it — `graphify/extract.py` `_DISPATCH`, diffed statically
# (2026-09-26, re-verified). Every other contract DeepMap leans on was identical across all 13:
# `detect._is_noise_dir` (byte-identical body), `_SKIP_DIRS` (0.8.45 adds only GRAPHIFY_OUT's basename),
# `_PARALLEL_THRESHOLD = 20`, the exact-suffix `_DISPATCH.get(path.suffix)` lookup, `update ...
# --no-cluster`, and the package's Requires-Dist (so an in-range upgrade pulls no new dependency).
_DISPATCH_0839 = frozenset({
    ".F", ".F03", ".F08", ".F90", ".F95", ".astro", ".bash", ".c", ".cc", ".cls", ".cpp", ".cs",
    ".cshtml", ".csproj", ".cxx", ".dart", ".dfm", ".dm", ".dme", ".dmf", ".dmi", ".dmm", ".dpk", ".dpr",
    ".ex", ".exs", ".f", ".f03", ".f08", ".f90", ".f95", ".fsproj", ".go", ".gradle", ".groovy", ".h",
    ".hcl", ".hpp", ".inc", ".java", ".jl", ".js", ".json", ".jsx", ".kt", ".kts", ".lfm", ".lpk", ".lpr",
    ".lua", ".luau", ".m", ".md", ".mdx", ".mjs", ".mm", ".pas", ".php", ".pp", ".ps1", ".py", ".qmd",
    ".razor", ".rb", ".rs", ".scala", ".sh", ".sln", ".slnx", ".sql", ".sv", ".svelte", ".svh", ".swift",
    ".tf", ".tfvars", ".toc", ".trigger", ".ts", ".tsx", ".v", ".vbproj", ".vue", ".zig"})
# (release, suffixes that release ADDED to the table); no in-range release removed one.
_DISPATCH_ADDED = (((0, 8, 40), {".psm1"}), ((0, 8, 41), {".psd1"}), ((0, 8, 46), {".cu", ".cuh"}),
                   ((0, 8, 50), {".metal", ".xaml"}))
# (release, {suffix: extractor}) — a suffix an in-range release RE-ROUTED to a different extractor.
# 0.8.50 moved `.vue` from extract_js (the whole SFC through the JS grammar, template and all — a
# `<script setup lang="ts">` block yields no symbols) to extract_vue (only the <script> block, parsed
# with the grammar its `lang` names; plus a new `dynamic_import` relation for `import('...')`).
_DISPATCH_REROUTED = (((0, 8, 50), {".vue": "extract_vue"}),)
_DISPATCH_BASE_EXTRACTOR = {".vue": "extract_js"}
_RANGE_RELEASES = ((0, 8, 39), (0, 8, 40), (0, 8, 41), (0, 8, 42), (0, 8, 43), (0, 8, 44), (0, 8, 45),
                   (0, 8, 46), (0, 8, 47), (0, 8, 48), (0, 8, 49), (0, 8, 50), (0, 8, 51))


def _extractor_of(release: tuple, suffix: str) -> str:
    name = _DISPATCH_BASE_EXTRACTOR.get(suffix, "")
    for since, rerouted in _DISPATCH_REROUTED:
        if release >= since and suffix in rerouted:
            name = rerouted[suffix]
    return name


def _dispatch_of(release: tuple) -> frozenset:
    keys = set(_DISPATCH_0839)
    for since, added in _DISPATCH_ADDED:
        if release >= since:
            keys |= added
    return frozenset(keys)


# --- a REAL graphify 0.8.51 capture (C:/tmp fixture: src/kernel.cu + src/helpers.cuh, shaders/blur.metal,
# ui/MainWindow.xaml, mod/Tools.psd1 + mod/Tools.psm1, app.py; `python -m graphify update --no-cluster`)
_REAL_0851_NODES = [
    {"id": "app", "label": "app.py", "file_type": "code", "source_file": "app.py", "source_location": "L1"},
    {"id": "app_main", "label": "main()", "file_type": "code", "source_file": "app.py", "source_location": "L1"},
    {"id": "mod_tools_psd1_mod_tools", "label": "Tools.psd1", "file_type": "code", "source_file": "mod/Tools.psd1", "source_location": "L1"},
    {"id": "mod_tools_psm1_mod_tools", "label": "Tools.psm1", "file_type": "code", "source_file": "mod/Tools.psm1", "source_location": "L1"},
    {"id": "mod_tools_get_thing", "label": "Get-Thing()", "file_type": "code", "source_file": "mod/Tools.psm1", "source_location": "L1"},
    {"id": "shaders_blur", "label": "blur.metal", "file_type": "code", "source_file": "shaders/blur.metal", "source_location": "L1"},
    {"id": "shaders_blur_weight", "label": "weight()", "file_type": "code", "source_file": "shaders/blur.metal", "source_location": "L4"},
    {"id": "shaders_blur_blur", "label": "blur()", "file_type": "code", "source_file": "shaders/blur.metal", "source_location": "L6"},
    {"id": "kernel", "label": "kernel", "file_type": "code", "source_file": "", "source_location": ""},
    {"id": "texture2d", "label": "texture2d", "file_type": "code", "source_file": "", "source_location": ""},
    {"id": "read", "label": "read", "file_type": "code", "source_file": "", "source_location": ""},
    {"id": "write", "label": "write", "file_type": "code", "source_file": "", "source_location": ""},
    {"id": "uint2", "label": "uint2", "file_type": "code", "source_file": "", "source_location": ""},
    {"id": "src_helpers", "label": "helpers.cuh", "file_type": "code", "source_file": "src/helpers.cuh", "source_location": "L1"},
    {"id": "src_helpers_scale", "label": "scale()", "file_type": "code", "source_file": "src/helpers.cuh", "source_location": "L2"},
    {"id": "device", "label": "__device__", "file_type": "code", "source_file": "", "source_location": ""},
    {"id": "src_kernel", "label": "kernel.cu", "file_type": "code", "source_file": "src/kernel.cu", "source_location": "L1"},
    {"id": "src_kernel_vector_add", "label": "vector_add()", "file_type": "code", "source_file": "src/kernel.cu", "source_location": "L3"},
    {"id": "global", "label": "__global__", "file_type": "code", "source_file": "", "source_location": ""},
    {"id": "src_kernel_launch_add", "label": "launch_add()", "file_type": "code", "source_file": "src/kernel.cu", "source_location": "L8"},
    {"id": "src_kernel_runner", "label": "Runner", "file_type": "code", "source_file": "src/kernel.cu", "source_location": "L12"},
    {"id": "src_kernel_runner_run", "label": ".run()", "file_type": "code", "source_file": "src/kernel.cu", "source_location": "L14"},
    {"id": "ui_mainwindow", "label": "MainWindow.xaml", "file_type": "code", "source_file": "ui/MainWindow.xaml", "source_location": "L1"},
    {"id": "ui_mainwindow_window", "label": "Window", "file_type": "code", "source_file": "ui/MainWindow.xaml", "source_location": "L1"},
    {"id": "ui_mainwindow_mainwindow", "label": "MainWindow", "file_type": "code", "source_file": "ui/MainWindow.xaml", "source_location": "L1"},
    {"id": "ui_mainwindow_okbutton", "label": "OkButton", "file_type": "code", "source_file": "ui/MainWindow.xaml", "source_location": "L2"},
    {"id": "xaml_button", "label": "Button", "file_type": "concept", "source_file": "ui/MainWindow.xaml", "source_location": "L2"},
]
_REAL_0851_LINKS = [
    {"source": "app", "target": "app_main", "relation": "contains", "confidence": "EXTRACTED"},
    {"source": "mod_tools_psd1_mod_tools", "target": "tools", "relation": "imports_from", "confidence": "EXTRACTED"},
    {"source": "mod_tools_psm1_mod_tools", "target": "mod_tools_get_thing", "relation": "contains", "confidence": "EXTRACTED"},
    {"source": "shaders_blur", "target": "metal_stdlib", "relation": "imports", "confidence": "EXTRACTED"},
    {"source": "shaders_blur", "target": "shaders_blur_weight", "relation": "contains", "confidence": "EXTRACTED"},
    {"source": "shaders_blur", "target": "shaders_blur_blur", "relation": "contains", "confidence": "EXTRACTED"},
    {"source": "shaders_blur_blur", "target": "kernel", "relation": "references", "confidence": "EXTRACTED"},
    {"source": "shaders_blur_blur", "target": "texture2d", "relation": "references", "confidence": "EXTRACTED"},
    {"source": "shaders_blur_blur", "target": "read", "relation": "references", "confidence": "EXTRACTED"},
    {"source": "shaders_blur_blur", "target": "write", "relation": "references", "confidence": "EXTRACTED"},
    {"source": "shaders_blur_blur", "target": "uint2", "relation": "references", "confidence": "EXTRACTED"},
    {"source": "shaders_blur_blur", "target": "shaders_blur_weight", "relation": "calls", "confidence": "EXTRACTED"},
    {"source": "shaders_blur_blur", "target": "write", "relation": "calls", "confidence": "EXTRACTED"},
    {"source": "shaders_blur_blur", "target": "read", "relation": "calls", "confidence": "EXTRACTED"},
    {"source": "src_helpers", "target": "src_helpers_scale", "relation": "contains", "confidence": "EXTRACTED"},
    {"source": "src_helpers_scale", "target": "device", "relation": "references", "confidence": "EXTRACTED"},
    {"source": "src_kernel", "target": "src_helpers", "relation": "imports", "confidence": "EXTRACTED"},
    {"source": "src_kernel", "target": "src_kernel_vector_add", "relation": "contains", "confidence": "EXTRACTED"},
    {"source": "src_kernel_vector_add", "target": "global", "relation": "references", "confidence": "EXTRACTED"},
    {"source": "src_kernel", "target": "src_kernel_launch_add", "relation": "contains", "confidence": "EXTRACTED"},
    {"source": "src_kernel", "target": "src_kernel_runner", "relation": "contains", "confidence": "EXTRACTED"},
    {"source": "src_kernel_runner", "target": "src_kernel_runner_run", "relation": "method", "confidence": "EXTRACTED"},
    {"source": "src_kernel_runner_run", "target": "src_kernel_launch_add", "relation": "calls", "confidence": "EXTRACTED"},
    {"source": "ui_mainwindow", "target": "ui_mainwindow_window", "relation": "contains", "confidence": "EXTRACTED"},
    {"source": "ui_mainwindow", "target": "ui_mainwindow_mainwindow", "relation": "contains", "confidence": "EXTRACTED"},
    {"source": "ui_mainwindow_window", "target": "ui_mainwindow_mainwindow", "relation": "references", "confidence": "EXTRACTED"},
    {"source": "ui_mainwindow", "target": "ui_mainwindow_okbutton", "relation": "contains", "confidence": "EXTRACTED"},
    {"source": "ui_mainwindow_okbutton", "target": "xaml_button", "relation": "references", "confidence": "EXTRACTED"},
    {"source": "src_kernel_vector_add", "target": "src_helpers_scale", "relation": "calls", "confidence": "INFERRED"},
]

# The same tree as files (section H drives the real engine over it).
_CUDA_TREE = {
    "src/kernel.cu": ('#include "helpers.cuh"\n\n'
                      "__global__ void vector_add(const float* a, const float* b, float* c, int n) {\n"
                      "    int i = blockIdx.x * blockDim.x + threadIdx.x;\n"
                      "    if (i < n) c[i] = scale(a[i]) + b[i];\n}\n\n"
                      "void launch_add(const float* a, const float* b, float* c, int n) {\n"
                      "    vector_add<<<(n + 255) / 256, 256>>>(a, b, c, n);\n}\n\n"
                      "class Runner {\npublic:\n    void run() { launch_add(nullptr, nullptr, nullptr, 0); }\n};\n"),
    "src/helpers.cuh": "#pragma once\n__device__ inline float scale(float x) { return x * 2.0f; }\n",
    "shaders/blur.metal": ("#include <metal_stdlib>\nusing namespace metal;\n\n"
                           "float weight(int i) { return 1.0 / (i + 1); }\n\n"
                           "kernel void blur(texture2d<float, access::read> inTex [[texture(0)]],\n"
                           "                 texture2d<float, access::write> outTex [[texture(1)]],\n"
                           "                 uint2 gid [[thread_position_in_grid]]) {\n"
                           "    float w = weight(1);\n    outTex.write(inTex.read(gid) * w, gid);\n}\n"),
    "ui/MainWindow.xaml": ('<Window x:Class="App.MainWindow" '
                           'xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" '
                           'xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml">\n'
                           '  <Grid><Button x:Name="OkButton" Click="OnOk">OK</Button></Grid>\n</Window>\n'),
    "mod/Tools.psd1": ("@{\n    RootModule = 'Tools.psm1'\n    ModuleVersion = '1.0.0'\n"
                       "    FunctionsToExport = @('Get-Thing')\n}\n"),
    "mod/Tools.psm1": "function Get-Thing { param($x) return $x }\n",
    "app.py": "def main():\n    return 1\n",
}

# --- a REAL graphify 0.8.51 capture of Vue SFCs (extract_vue, the 0.8.50 re-route): _VUE_TREE below,
# `python -m graphify update --no-cluster`. Note the `dynamic_import` link (new relation) and that the
# lazy `import('./UserCard.vue')` ALSO arrives as an ordinary `imports_from` from the Lazy function.
_REAL_0851_VUE_NODES = [
    {"id": "src_home", "label": "Home.vue", "file_type": "code", "source_file": "src/Home.vue", "source_location": "L1"},
    {"id": "src_home_lazy", "label": "Lazy()", "file_type": "code", "source_file": "src/Home.vue", "source_location": "L3"},
    {"id": "src_home_loadhome", "label": "loadHome()", "file_type": "code", "source_file": "src/Home.vue", "source_location": "L4"},
    {"id": "src_usercard", "label": "UserCard.vue", "file_type": "code", "source_file": "src/UserCard.vue", "source_location": "L1"},
    {"id": "src_usercard_select", "label": "select()", "file_type": "code", "source_file": "src/UserCard.vue", "source_location": "L8"},
    {"id": "src_names", "label": "names.ts", "file_type": "code", "source_file": "src/names.ts", "source_location": "L1"},
    {"id": "src_names_fullname", "label": "fullName()", "file_type": "code", "source_file": "src/names.ts", "source_location": "L1"},
]
_REAL_0851_VUE_LINKS = [
    {"source": "src_home", "target": "src_home_lazy", "relation": "contains", "confidence": "EXTRACTED"},
    {"source": "src_home", "target": "src_home_loadhome", "relation": "contains", "confidence": "EXTRACTED"},
    {"source": "src_home_lazy", "target": "src_usercard", "relation": "imports_from", "confidence": "EXTRACTED"},
    {"source": "src_home", "target": "src_usercard", "relation": "dynamic_import", "confidence": "EXTRACTED"},
    {"source": "src_usercard", "target": "src_names", "relation": "imports_from", "confidence": "EXTRACTED"},
    {"source": "src_usercard", "target": "src_names_fullname", "relation": "imports", "confidence": "EXTRACTED"},
    {"source": "src_usercard", "target": "src_usercard_select", "relation": "contains", "confidence": "EXTRACTED"},
    {"source": "src_names", "target": "src_names_fullname", "relation": "contains", "confidence": "EXTRACTED"},
    {"source": "src_usercard_select", "target": "src_names_fullname", "relation": "calls", "confidence": "EXTRACTED"},
]
_VUE_TREE = {
    "src/UserCard.vue": ("<template>\n  <div @click=\"select\">{{ fullName(user) }}</div>\n</template>\n\n"
                         "<script setup lang=\"ts\">\nimport { fullName } from './names'\n\n"
                         "function select(u: { first: string; last: string }) {\n  return fullName(u)\n}\n"
                         "</script>\n"),
    "src/Home.vue": ("<template><UserCard /></template>\n"
                     "<script lang=\"ts\">\nconst Lazy = () => import('./UserCard.vue')\n"
                     "export function loadHome(id: number): string {\n  return String(id)\n}\n</script>\n"),
    "src/names.ts": ("export function fullName(u: { first: string; last: string }): string {\n"
                     "  return u.first + ' ' + u.last\n}\n"),
}


def check(label: str, cond: bool, detail="") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {label}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}{(' — ' + str(detail)[:500]) if detail != '' else ''}")


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


@contextlib.contextmanager
def _patched(obj, **attrs):
    had = {k: hasattr(obj, k) for k in attrs}
    saved = {k: getattr(obj, k, None) for k in attrs}
    try:
        for k, v in attrs.items():
            setattr(obj, k, v)
        yield
    finally:
        for k, v in saved.items():
            if had[k]:
                setattr(obj, k, v)
            else:
                delattr(obj, k)


@contextlib.contextmanager
def _executable(path: str):
    saved = sys.executable
    sys.executable = path
    try:
        yield
    finally:
        sys.executable = saved


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _spec_bounds(spec: str):
    """('graphifyy>=A,<B') -> ((A), (B)) as 3-tuples, or None."""
    m = re.fullmatch(r"graphifyy>=([0-9.]+),<([0-9.]+)", spec or "")
    if not m:
        return None
    return tuple(GX._version_tuple(m.group(1))), tuple(GX._version_tuple(m.group(2)))


def _real_bash():
    """A real POSIX bash (Git Bash on Windows), never the WSL launcher in System32. Or None."""
    b = shutil.which("bash")
    if not b or "system32" in b.lower() or "windowsapps" in b.lower():
        return None
    return b


def _bash_argv(cmd: str):
    """How bash tokenizes `cmd` (exactly as if the user pasted it), or None without a real bash."""
    b = _real_bash()
    if not b:
        return None
    try:
        p = subprocess.run([b, "-c", 'eval "set -- $DM_CMD"; printf "%s\\n" "$@"'], capture_output=True,
                           timeout=30, env=dict(os.environ, DM_CMD=cmd), encoding="utf-8", errors="replace")
        return p.stdout.splitlines() if p.returncode == 0 else None
    except Exception:  # noqa: BLE001
        return None


# ================================================================================================
def gate_a_spec() -> None:
    print("\n-- A: the supported range is exactly what was validated (0.8.39 .. 0.8.51) --")
    spec = getattr(GX, "SUPPORTED_GRAPHIFY_SPEC", None)
    check(f"SUPPORTED_GRAPHIFY_SPEC == {WANT_SPEC!r} (F3)", spec == WANT_SPEC, spec)
    bounds = _spec_bounds(spec or "")
    check("the spec literal parses as graphifyy>=MIN,<BELOW", bounds is not None, spec)
    if bounds:
        check("graphify_support()'s (MIN, BELOW) tuples are the spec literal's bounds",
              (tuple(GX.SUPPORTED_GRAPHIFY_MIN), tuple(GX.SUPPORTED_GRAPHIFY_BELOW)) == bounds,
              (GX.SUPPORTED_GRAPHIFY_MIN, GX.SUPPORTED_GRAPHIFY_BELOW, bounds))
    table = {"0.8.38": False, "0.8.39": True, "0.8.46": True, "0.8.50": True, "0.8.51": True,
             "0.8.51.post1": True, "0.8.52": False, "0.8.52rc1": False, "0.8.60": False, "0.9.0": False,
             "0.9.68": False}
    for v, want in table.items():
        with _patched(GX, graphify_version=lambda v=v: v):
            got = GX.graphify_support()
        check(f"graphify {v}: supported={want}", got == (want, v), got)


def gate_b_range_snapshot() -> None:
    print("\n-- B: every suffix ANY in-range engine parses is a decided case (hermetic, whole range) --")
    allowed = {e.lower() for e in GX.SOURCE_CODE_EXTS}
    excluded = {e.lower() for e in getattr(GX, "EXCLUDED_BY_DESIGN_EXTS", frozenset())}
    union = set()
    for rel in _RANGE_RELEASES:
        union |= {e.lower() for e in _dispatch_of(rel)}
    undecided = sorted(union - allowed - excluded)
    check("no suffix parsed anywhere in 0.8.39..0.8.51 is silently dropped", not undecided,
          f"UNDECIDED: {undecided}")
    check("CUDA/Metal (.cu .cuh .metal) are on the ALLOWED side", {".cu", ".cuh", ".metal"} <= allowed,
          sorted({".cu", ".cuh", ".metal"} - allowed))
    check(".psd1 (PowerShell data manifest) and .xaml (UI markup) are EXCLUDED by design",
          {".psd1", ".xaml"} <= excluded, sorted({".psd1", ".xaml"} - excluded))
    check("nothing is both allowed and excluded", not (allowed & excluded), sorted(allowed & excluded))
    missing = sorted(e for e in allowed if e not in V._SOURCE_EXTS)
    check("every allowed suffix has a verdict-census language (incl. CUDA/Metal)", not missing, missing)
    langs = {V._SOURCE_EXTS.get(e) for e in (".cu", ".cuh", ".metal")}
    check("CUDA/Metal census languages are their own buckets (not folded into the .cpp bucket)",
          None not in langs and "C++" not in langs, langs)
    since = getattr(GX, "ENGINE_EXT_SINCE", None)
    want = {e: s for s, added in _DISPATCH_ADDED for e in added}
    check("graphify_extract.ENGINE_EXT_SINCE == the snapshot's in-range parser additions",
          isinstance(since, dict) and {k: tuple(v) for k, v in since.items()} == want, since)


def gate_c_snapshot_is_real() -> None:
    print("\n-- C: the snapshot matches the INSTALLED engine's live parser table --")
    if not _HAS_ENGINE:
        print("     (graphify not installed — skipped)")
        return
    try:
        import graphify.extract as engine
        live = set(getattr(engine, "_DISPATCH", {}) or {})
    except Exception as e:  # noqa: BLE001
        check("graphify.extract importable", False, repr(e))
        return
    v = GX.graphify_version()
    t = GX._version_tuple(v) if v else None
    print(f"     installed graphify: {v}")
    if t not in _RANGE_RELEASES:
        print("     (installed version is not a snapshotted release — comparison skipped)")
        return
    want = set(_dispatch_of(t))
    check(f"live _DISPATCH of graphify {v} == the frozen snapshot for {v}", live == want,
          {"only_live": sorted(live - want), "only_snapshot": sorted(want - live)})
    table = getattr(engine, "_DISPATCH", {}) or {}
    for suffix in sorted(_DISPATCH_BASE_EXTRACTOR):
        got = getattr(table.get(suffix), "__name__", None)
        check(f"graphify {v} routes {suffix} to {_extractor_of(t, suffix)} (the snapshot's re-route record)",
              got == _extractor_of(t, suffix), got)
    fn = getattr(GX, "upgradeable_unparsed_exts", None)
    check("graphify_extract.upgradeable_unparsed_exts exists", callable(fn))
    if callable(fn):
        up = fn()
        low = {e.lower() for e in live}
        wrong = sorted(e for e in set(GX.ENGINE_EXT_SINCE) & set(GX.SOURCE_CODE_EXTS)
                       if (e in up) != (e not in low))
        check("upgradeable_unparsed_exts() agrees with the live table for every in-range addition",
              not wrong, (wrong, up))


def gate_d_map_real_capture() -> None:
    print("\n-- D: a REAL graphify-0.8.51 CUDA/Metal/XAML/.psd1 graph maps to typed CUDA/Metal nodes --")
    nodes, calls, imports = GX.map_graph({"nodes": _REAL_0851_NODES, "links": _REAL_0851_LINKS})
    by_key = {n["key"]: n for n in nodes}
    for key, label, name in (("src_kernel", "Module", None), ("src_kernel_vector_add", "Function", "vector_add"),
                             ("src_kernel_launch_add", "Function", "launch_add"),
                             ("src_kernel_runner", "Class", "Runner"),
                             ("src_kernel_runner_run", "Method", None),
                             ("src_helpers_scale", "Function", "scale"), ("shaders_blur", "Module", None),
                             ("shaders_blur_blur", "Function", "blur"),
                             ("shaders_blur_weight", "Function", "weight")):
        n = by_key.get(key)
        ok = n is not None and n.get("label") == label and (name is None or n.get("name") == name)
        check(f"{key} kept as :{label}", ok, n)
    # CALLS are (caller key, callee NAME) — the default name-resolved contract (precise CALLS is opt-in).
    pairs = {tuple(c[:2]) for c in calls}
    check("CUDA: vector_add -> scale (cross-file into the .cuh) is a CALLS edge",
          ("src_kernel_vector_add", "scale") in pairs, calls)
    check("CUDA: Runner.run -> launch_add is a CALLS edge", ("src_kernel_runner_run", "launch_add") in pairs,
          calls)
    check("Metal: blur -> weight is a CALLS edge", ("shaders_blur_blur", "weight") in pairs, calls)
    check("CUDA: kernel.cu IMPORTS helpers.cuh (by the engine-resolved key)",
          any(src == "src_kernel" and tgt in ("src_helpers", "helpers") for src, tgt in imports), imports)
    leaked = sorted(n["source_file"] for n in nodes if str(n.get("source_file", "")).lower().endswith(
        (".xaml", ".psd1")))
    check("no .xaml / .psd1 node reaches the code graph (excluded by design)", not leaked, leaked)
    check("the unsourced engine stubs (__global__, texture2d, ...) are not nodes",
          not ({"global", "device", "texture2d", "kernel"} & set(by_key)), sorted(by_key))
    check("an UPPERCASE KERNEL.CU file node is typed :Module (case-insensitive allowlist)",
          GX._looks_like_file("KERNEL.CU") and GX._is_code_node({"file_type": "code",
                                                                  "source_file": "SRC/KERNEL.CU"}))


def gate_e_census() -> None:
    print("\n-- E: the census counts CUDA/Metal and never .xaml/.psd1 --")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        for rel, text in _CUDA_TREE.items():
            _write(root, rel, text)
        _write(root, "src/MORE.CU", "__global__ void k() {}\n")
        census = V.extension_census(root)
    cuda, metal = V._SOURCE_EXTS.get(".cu"), V._SOURCE_EXTS.get(".metal")
    check("CUDA files (.cu, .cuh, an uppercase .CU) are counted", cuda is not None and census.get(cuda) == 3,
          census)
    check("the Metal file is counted", metal is not None and census.get(metal) == 1, census)
    check(".xaml / .psd1 are not census source (PowerShell counts only the .psm1)",
          census.get("PowerShell") == 1 and not any("XAML" in str(k).upper() for k in census), census)


def _coverage(root: Path, nodes, version):
    with _patched(GX, graphify_version=lambda: version):
        census = V.extension_census(root)
        cov = V.build_coverage(root, nodes, census=census)
        verdict, reasons = V.classify(neo4j_ok=True, n_nodes=max(1, len(nodes)), n_embedded=max(1, len(nodes)),
                                      census=census, extractor="graphify", ml_ok=True, coverage=cov)
    return census, cov, verdict, reasons


def gate_f_lower_end_honesty() -> None:
    print("\n-- F: 0.8.39 is in range but cannot parse CUDA/Metal/.psm1 — the verdict says so, with the fix --")
    fn = getattr(GX, "upgradeable_unparsed_exts", None)
    check("graphify_extract.upgradeable_unparsed_exts exists", callable(fn))
    if callable(fn):
        allowed = set(GX.SOURCE_CODE_EXTS)
        for rel in _RANGE_RELEASES:
            v = ".".join(map(str, rel))
            want = {e: ".".join(map(str, s)) for s, added in _DISPATCH_ADDED for e in added
                    if e in allowed and rel < s}
            check(f"upgradeable_unparsed_exts({v!r}) == the snapshot's missing in-range parsers {sorted(want)}",
                  fn(v) == want, fn(v))
        for v in ("0.8.38", "0.8.52", "0.9.68", "", None):
            with _patched(GX, graphify_version=lambda: None):
                got = fn(v) if v is not None else fn()
            check(f"upgradeable_unparsed_exts({v!r}) == {{}} (unknown / outside the range)", got == {}, got)
    py = f'"{Path(sys.executable).as_posix()}"'
    cmd = f'{py} -m pip install --upgrade "{WANT_SPEC}"'
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "r"
        for i in range(3):
            _write(root, f"gpu/k{i}.cu", "__global__ void k() {}\n")
        _write(root, "gpu/h.cuh", "__device__ float f(float x) { return x; }\n")
        for i in range(30):
            _write(root, f"src/m{i}.cpp", f"int f{i}() {{ return {i}; }}\n")
        nodes = [{"source_file": f"src/m{i}.cpp"} for i in range(30)] * 2
        census, cov, verdict, reasons = _coverage(root, nodes, "0.8.39")
        cuda = [r for r in reasons if r.startswith("LOW COVERAGE for CUDA")]
        check("0.8.39 + 4 CUDA files (masked by 30 well-extracted .cpp): DEGRADED, ONE CUDA reason",
              verdict == "DEGRADED" and len(cuda) == 1, (verdict, reasons))
        text = cuda[0] if cuda else ""
        head = text.split(";")[0]
        check("... the hook-visible head names the count, the suffixes and the engine version",
              "4" in head and ".cu" in head and ".cuh" in head and "0.8.39" in head and "not parsed" in head,
              head)
        check("... the reason names the release that parses them (0.8.46) and the runnable --upgrade",
              "0.8.46" in text and cmd in text and "/deep-map:refresh" in text, text)

        census, cov, verdict, reasons = _coverage(root, nodes + [{"source_file": "gpu/k0.cu"},
                                                                 {"source_file": "gpu/h.cuh"}], "0.8.51")
        check("0.8.51 (parses CUDA): no 'not parsed' reason, READY", verdict == "READY"
              and not any("not parsed" in r for r in reasons), (verdict, reasons))
        _write(root, "gpu/blur.metal", "kernel void blur() {}\n")
        census, cov, verdict, reasons = _coverage(root, nodes, "0.8.46")
        check("0.8.46 (parses .cu, not .metal): the Metal file is named, the 4 CUDA files are NOT",
              any(r.startswith("LOW COVERAGE for Metal") and "0.8.50" in r for r in reasons)
              and not any("CUDA" in r for r in reasons), reasons)
        (root / "gpu" / "blur.metal").unlink()
        census, cov, verdict, reasons = _coverage(root, nodes, None)
        check("unknown engine version: no per-suffix claim", not any("not parsed" in r for r in reasons),
              reasons)
        census, cov, verdict, reasons = _coverage(root, nodes, "0.9.68")
        check("an engine outside the range: no per-suffix claim (the range warning covers it)",
              not any("not parsed" in r for r in reasons), reasons)
        with _patched(GX, graphify_version=lambda: "0.8.39"):
            census = V.extension_census(root)
            cov = V.build_coverage(root, nodes, census=census)
            v2, r2 = V.classify(neo4j_ok=True, n_nodes=60, n_embedded=60, census=census, extractor="ast",
                                extraction={"requested": "auto", "fallback_reason": "boom"}, ml_ok=True,
                                coverage=cov)
        check("an ast fallback: no graphify 'not parsed' claim (the fallback reason covers it)",
              not any("not parsed" in r for r in r2), r2)

    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "r"
        for i in range(12):
            _write(root, f"cuda/k{i}.cu", "__global__ void k() {}\n")
        census, cov, verdict, reasons = _coverage(root, [{"source_file": "x.py"}], "0.8.39")
        cuda = [r for r in reasons if "CUDA" in r]
        check("12 CUDA files, 0 nodes on 0.8.39: exactly ONE CUDA reason (the precise one, not also the "
              "density heuristic)", len(cuda) == 1 and "not parsed" in cuda[0], cuda)

    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "r"
        for i in range(12):
            _write(root, f"ps/s{i}.ps1", f"function S{i} {{ }}\n")
        for i in range(2):
            _write(root, f"ps/m{i}.psm1", f"function M{i} {{ }}\n")
        nodes = [{"source_file": f"ps/s{i}.ps1"} for i in range(12)]
        census, cov, verdict, reasons = _coverage(root, nodes, "0.8.39")
        ps = [r for r in reasons if r.startswith("LOW COVERAGE for PowerShell")]
        check("0.8.39: the 2 .psm1 modules are named (the 12 parsed .ps1 do not hide them)",
              len(ps) == 1 and "2" in ps[0].split(";")[0] and ".psm1" in ps[0] and ".ps1," not in ps[0]
              and "0.8.40" in ps[0], ps)
        census, cov, verdict, reasons = _coverage(root, nodes + [{"source_file": "ps/m0.psm1"}], "0.8.40")
        check("0.8.40 parses .psm1: no PowerShell reason", not any("PowerShell" in r for r in reasons),
              reasons)

    if _HAS_ENGINE and shutil.which("git"):
        # tracked CUDA inside an ENGINE-SKIPPED dir (build/): the census cannot see it, the D5 re-include
        # hands it to an engine with no parser — the reason must be the parser, not the re-include.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "r"
            _write(root, "src/app.py", "def app():\n    return 1\n")
            _write(root, "build/k0.cu", "__global__ void k0() {}\n")
            _write(root, "build/k1.cu", "__global__ void k1() {}\n")
            _write(root, "build/helper.py", "def helper():\n    return 2\n")
            for args in (["init", "-q"], ["config", "user.email", "t@t.t"], ["config", "user.name", "t"],
                         ["config", "commit.gpgsign", "false"], ["add", "-A"], ["commit", "-qm", "s"]):
                subprocess.run(["git", "-C", str(root), *args], capture_output=True, timeout=60)
            nodes = [{"source_file": "src/app.py"}, {"source_file": "build/helper.py"}]
            census, cov, verdict, reasons = _coverage(root, nodes, "0.8.39")
            unp = [r for r in reasons if "not parsed" in r]
            check("0.8.39 + tracked build/*.cu: the 2 files are named as unparsed (the census cannot see "
                  "build/, the skipped-dir list can)", len(unp) == 1 and "2 .cu" in unp[0], reasons)
            check("... and build/ is NOT blamed on the re-include (its parseable helper.py did arrive)",
                  not any(r.startswith("LOW COVERAGE in build/") for r in reasons), reasons)
            census, cov, verdict, reasons = _coverage(root, nodes + [{"source_file": "build/k0.cu"}], "0.8.51")
            check("0.8.51 (parses .cu, a build/ .cu node arrived): no 'not parsed' reason",
                  not any("not parsed" in r for r in reasons), reasons)

    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "r"
        for i in range(40):
            _write(root, f"pkg/m{i}.pyi", f"def f{i}() -> int: ...\n")
        for i in range(40):
            _write(root, f"pkg/m{i}.py", f"def f{i}():\n    return {i}\n")
        nodes = [{"source_file": f"pkg/m{i}.py"} for i in range(40)]
        census, cov, verdict, reasons = _coverage(root, nodes, "0.8.39")
        check(".pyi stubs (no in-range engine parses them): no 'not parsed' reason, READY",
              verdict == "READY" and not any("not parsed" in r for r in reasons), (verdict, reasons))


def _cmd_in(text: str, py_posix: str) -> str:
    """The command that starts at the quoted interpreter, up to the closing quote of the pip spec."""
    i = text.find(f'"{py_posix}"')
    if i < 0:
        return ""
    j = text.find('"', text.find(WANT_SPEC, i) + len(WANT_SPEC)) if WANT_SPEC in text[i:] else -1
    return text[i:j + 1] if j > i else text[i:]


def gate_g_quoted_hints() -> None:
    print("\n-- G: every remedy survives Git Bash — the interpreter is QUOTED with forward slashes --")
    # Windows-shaped interpreter paths only mean anything on Windows (on POSIX a backslash is a filename
    # character, so Path() keeps it and the POSIX shell rules differ); the POSIX case runs on every OS.
    exes = ((r"C:\Users\dev\Python313\python.exe", r"C:\Program Files\Python313\python.exe")
            if os.name == "nt" else ()) + ("/usr/local/bin/python3",)
    for exe in exes:
        posix = Path(exe).as_posix() if "\\" not in exe else exe.replace("\\", "/")
        # the pip commands are a Python the operator manages (DEEPMAP_MANAGED_VENV=0): without it, a package this
        # Python lacks (CI installs no ML stack) is /deep-map:setup's — tests/setup_bootstrap_gates.py section L
        with _executable(exe), _env(DEEPMAP_MANAGED_VENV="0"):
            fn = getattr(GX, "graphify_install_command", None)
            cmd = fn() if callable(fn) else ""
            hint = GX.unsupported_engine_hint("0.9.68")
            _v, r = V.classify(neo4j_ok=True, n_nodes=5, n_embedded=0, census={"Python": 5},
                               extractor="graphify", ml_ok=False)
            ml = next((x for x in r if x.startswith("VECTOR SEARCH DISABLED")), "")
            _v, r = V.classify(neo4j_ok=True, n_nodes=5, n_embedded=5, census={"Python": 5, "Go": 3},
                               extractor="ast", ml_ok=True,
                               extraction={"requested": "auto",
                                           "fallback_reason": "graphify (the multi-language engine) is not "
                                                              "installed"})
            fb = next((x for x in r if "fell back automatically" in x), "")
        tag = f"[{exe[:22]}...]"
        check(f"{tag} graphify_install_command() == '\"<posix python>\" -m pip install --upgrade \"<spec>\"'",
              cmd == f'"{posix}" -m pip install --upgrade "{WANT_SPEC}"', cmd)
        for name, text in (("unsupported-engine hint", hint), ("not-installed fallback remedy", fb)):
            got = _cmd_in(text, posix)
            argv = shlex.split(got) if got else []
            check(f"{tag} {name}: carries the quoted install command",
                  got == f'"{posix}" -m pip install --upgrade "{WANT_SPEC}"', text[-300:])
            check(f"{tag} {name}: a POSIX shell keeps the interpreter path + the spec intact",
                  argv[:1] == [posix] and WANT_SPEC in argv, argv)
        check(f"{tag} not-installed fallback: says the engine is not installed (not 'graphify failed (graphify')",
              "not installed" in fb and "graphify failed (graphify" not in fb and "fell back automatically" in fb,
              fb[:300])
        check(f"{tag} ML remedy: the interpreter is quoted with forward slashes (both commands)",
              ml.count(f'"{posix}" -m pip install') == 2 and "\\" not in ml, ml)
        seg = ml[ml.find(f'"{posix}"'):ml.find(" torch") + len(" torch")] if f'"{posix}"' in ml else ""
        check(f"{tag} ML remedy: a POSIX shell parses it to the interpreter, not a mangled path",
              bool(seg) and shlex.split(seg)[:1] == [posix], seg)

    # the RED this fixes: Git Bash really does eat an unquoted backslash path (not a hypothetical)
    argv = _bash_argv(r"C:\Users\dev\py\python.exe -m pip") if os.name == "nt" else None
    if argv is None:
        print("     (no real bash on PATH — the live-shell half is skipped; shlex covers it)")
    else:
        check("a REAL bash mangles an UNQUOTED backslash interpreter (the defect being fixed)",
              argv[:1] == ["C:Usersdevpypython.exe"], argv)
        with _executable(r"C:\Users\dev\py\python.exe"):
            cmd = GX.graphify_install_command() if hasattr(GX, "graphify_install_command") else ""
        argv = _bash_argv(cmd) if cmd else None
        check("a REAL bash keeps DeepMap's quoted command intact",
              argv == ["C:/Users/dev/py/python.exe", "-m", "pip", "install", "--upgrade", WANT_SPEC], argv)


def gate_h_real_engine() -> None:
    print("\n-- H: the REAL installed engine over a CUDA/Metal/XAML/.psd1 tree --")
    if not _HAS_ENGINE:
        print("     (graphify not installed — skipped)")
        return
    v = GX.graphify_version()
    t = GX._version_tuple(v) if v else None
    print(f"     installed graphify: {v}")
    home = Path(tempfile.mkdtemp(prefix="dmsr_home_"))
    try:
        with tempfile.TemporaryDirectory() as td, _env(DEEPMAP_HOME=str(home)):
            root = Path(td) / "gpu"
            for rel, text in _CUDA_TREE.items():
                _write(root, rel, text)
            res, report = GX.extract_with_report(root, timeout=300)
            check("the engine ran and returned a graph", bool(res and res[0]), report.get("cause"))
            if not res:
                return
            nodes, calls, _imports = res
            files = {str(n.get("source_file", "")).replace("\\", "/") for n in nodes}
            check("no .xaml / .psd1 node in the result",
                  not any(f.lower().endswith((".xaml", ".psd1")) for f in files), sorted(files))
            census = V.extension_census(root)
            cov = V.build_coverage(root, nodes, census=census)
            verdict, reasons = V.classify(neo4j_ok=True, n_nodes=len(nodes), n_embedded=len(nodes),
                                          census=census, extractor="graphify", ml_ok=True, coverage=cov)
            if t is not None and t >= (0, 8, 46):
                names = {n.get("name") for n in nodes}
                check(f"graphify {v}: CUDA symbols are in the graph (vector_add, launch_add, Runner, scale)",
                      {"vector_add", "launch_add", "Runner", "scale"} <= names, sorted(names))
                check(f"graphify {v}: a CUDA CALLS edge survives (vector_add -> scale)",
                      any("vector_add" in str(a) and "scale" in str(b) for a, b, *_ in calls), calls)
                if t >= (0, 8, 50):
                    check(f"graphify {v}: Metal symbols are in the graph (blur, weight)",
                          {"blur", "weight"} <= names, sorted(names))
                check(f"graphify {v}: no 'not parsed' reason for CUDA",
                      not any("not parsed" in r and "CUDA" in r for r in reasons), reasons)
            else:
                check(f"graphify {v}: no CUDA nodes (the engine has no parser for .cu yet)",
                      not any(f.endswith((".cu", ".cuh")) for f in files), sorted(files))
                heads = [r.split(";")[0] for r in reasons if "not parsed" in r]
                check(f"graphify {v}: the verdict DEGRADES with ONE reason naming CUDA + Metal + the upgrade",
                      verdict == "DEGRADED" and len(heads) == 1
                      and heads[0].startswith("LOW COVERAGE for CUDA, Metal")
                      and ".cu/.cuh" in heads[0] and ".metal" in heads[0]
                      and any("not parsed" in r and "--upgrade" in r for r in reasons), (verdict, reasons))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def gate_i_vue_reroute() -> None:
    print("\n-- I: Vue SFCs — 0.8.50 re-routed .vue to extract_vue; DeepMap maps its output correctly --")
    nodes, calls, imports = GX.map_graph({"nodes": _REAL_0851_VUE_NODES, "links": _REAL_0851_VUE_LINKS})
    got = sorted((n["label"], n["name"], n["source_file"], n.get("start_line")) for n in nodes)
    want = sorted([("Module", "Home", "src/Home.vue", 1), ("Function", "Lazy", "src/Home.vue", 3),
                   ("Function", "loadHome", "src/Home.vue", 4), ("Module", "UserCard", "src/UserCard.vue", 1),
                   ("Function", "select", "src/UserCard.vue", 8), ("Module", "names", "src/names.ts", 1),
                   ("Function", "fullName", "src/names.ts", 1)])
    check("captured 0.8.51 SFC graph: the <script setup lang=\"ts\"> symbols map to typed nodes at their "
          ".vue file:line", got == want, got)
    check("captured 0.8.51 SFC graph: select -> fullName (SFC script into a .ts module) is a CALLS edge",
          ("src_usercard_select", "fullName") in {tuple(c[:2]) for c in calls}, calls)
    imp = {tuple(i) for i in imports}
    check("captured 0.8.51 SFC graph: the SFC's static import binds by the engine-resolved key",
          ("src_usercard", "src_names") in imp, sorted(imp))
    check("captured 0.8.51 SFC graph: the lazy import('./UserCard.vue') is captured exactly ONCE (the new "
          "`dynamic_import` relation adds no duplicate edge)",
          sum(1 for s, t in imp if t == "src_usercard") == 1 and ("src_home_lazy", "src_usercard") in imp,
          sorted(imp))
    check("captured 0.8.51 SFC graph: no stub/specifier-named node ('./x.vue', '../x') leaks in",
          not any(str(n["name"]).startswith(".") or "/" in str(n["name"]) for n in nodes),
          sorted(n["name"] for n in nodes))

    if not _HAS_ENGINE:
        print("     (graphify not installed — the live half is skipped)")
        return
    v = GX.graphify_version()
    t = GX._version_tuple(v) if v else None
    home = Path(tempfile.mkdtemp(prefix="dmsr_vue_home_"))
    try:
        with tempfile.TemporaryDirectory() as td, _env(DEEPMAP_HOME=str(home)):
            root = Path(td) / "vue"
            for rel, text in _VUE_TREE.items():
                _write(root, rel, text)
            res, report = GX.extract_with_report(root, timeout=300)
            check(f"graphify {v}: the engine ran over the SFC tree", bool(res and res[0]), report.get("cause"))
            if not res:
                return
            live_nodes, live_calls, _imports = res
            mods = {n["source_file"].replace("\\", "/") for n in live_nodes if n["label"] == "Module"}
            check(f"graphify {v}: both .vue files are :Module nodes", {"src/Home.vue", "src/UserCard.vue"} <= mods,
                  sorted(mods))
            if t is not None and t >= (0, 8, 50):
                live = sorted((n["label"], n["name"], n["source_file"].replace("\\", "/"), n.get("start_line"))
                              for n in live_nodes)
                check(f"graphify {v} (extract_vue): the live SFC graph maps exactly like the frozen capture",
                      live == want, live)
                check(f"graphify {v} (extract_vue): select -> fullName CALLS edge",
                      any(a == "src_usercard_select" and b == "fullName" for a, b, *_ in live_calls), live_calls)
            census = V.extension_census(root)
            check(f"graphify {v}: the census counts the 2 SFCs as Vue", census.get("Vue") == 2, census)
    finally:
        shutil.rmtree(home, ignore_errors=True)


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 78)
    print("GRAPHIFY SUPPORTED RANGE GATES — F3 (no Neo4j, no network)")
    print("=" * 78)
    with _env(DEEPMAP_EXTRACTOR=None, DEEPMAP_RESCUE_SKIPPED_DIRS=None, DEEPMAP_CASE_INSENSITIVE_EXT=None,
              DEEPMAP_SELF_CALL_REBIND=None, DEEPMAP_CLEAN_OWNER_NAMES=None, DEEPMAP_RESOLVED_IMPORTS=None,
              DEEPMAP_EXCLUDE_TESTS=None, DEEPMAP_PYTHON=None):
        gate_a_spec()
        gate_b_range_snapshot()
        gate_c_snapshot_is_real()
        gate_d_map_real_capture()
        gate_e_census()
        gate_f_lower_end_honesty()
        gate_g_quoted_hints()
        gate_h_real_engine()
        gate_i_vue_reroute()
    print("\n" + "=" * 78)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if not _FAIL else 'RED'} — {_PASS}/{total} checks passed.")
    return 0 if not _FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
