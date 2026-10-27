#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j gates for the default-on vendor/generated path-ignore (P0-3, Rails field-review).

Vendored libs + generated bundles (jQuery/ActiveScaffold under public/, node_modules, minified
*.min.js) are code the user never wrote — 84% of Function nodes on the review's Rails app. `sync.py`'s
`_maybe_exclude_vendor` drops them by DIRECTORY COMPONENT (+ minified suffix), honoring the repo
.gitignore's bare-dir entries, DEFAULT-ON with a DEEPMAP_INCLUDE_VENDOR=1 escape hatch.

These gates lock: (1) vendored nodes + their calls/imports drop, hand-written source survives; (2) the
dir match is a COMPONENT never a substring (src/publication.py / libvendor/x.js survive); (3) the
escape hatch + a no-vendor tree return the SAME objects (byte-identity — the STAGE-0 oracle / a Python
build is unchanged); (4) .gitignore bare-dir entries are honored, anchored/glob patterns are NOT; (5)
the filter is wired into BOTH _rebuild_code convergence points. Pure + hermetic (no Neo4j, no spend).
"""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

BUILD = Path(__file__).resolve().parent.parent / "build"

_PASS = 0
_FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {label}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}{(' — ' + detail) if detail else ''}")


def _load_sync():
    spec = importlib.util.spec_from_file_location("dm_sync_vendor", BUILD / "sync.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _nodes():
    return [
        {"key": "app/models/user.rb::User:1", "source_file": "app/models/user.rb"},          # keep
        {"key": "public/js/jquery.min.js::$:1", "source_file": "public/js/jquery.min.js"},    # drop
        {"key": "public/application-abc.js::init:1", "source_file": "public/application-abc.js"},  # drop
        {"key": "node_modules/lodash/index.js::each:1", "source_file": "node_modules/lodash/index.js"},  # drop
        {"key": "vendor/assets/x.rb::x:1", "source_file": "vendor/assets/x.rb"},              # drop
        {"key": "app/assets/app.min.js::a:1", "source_file": "app/assets/app.min.js"},        # drop (suffix)
        {"key": "src/publication.py::pub:1", "source_file": "src/publication.py"},            # keep (substring)
        {"key": "libvendor/x.js::x:1", "source_file": "libvendor/x.js"},                      # keep (substring)
    ]


def _no_gitignore_dir():
    """A temp dir with NO .gitignore (so _gitignore_dir_parts contributes nothing)."""
    d = Path(tempfile.mkdtemp(prefix="dm_vendor_"))
    return d


def gate_default_on(S) -> None:
    print("\n-- default-on: vendored nodes drop by dir-component + suffix; source survives --")
    root = _no_gitignore_dir()
    nodes = _nodes()
    calls = [("app/models/user.rb::User:1", "each"),          # from kept -> survives
             ("public/js/jquery.min.js::$:1", "ready")]        # from dropped -> gone
    imports = [("app/models/user.rb::User:1", "lodash"),       # kept src -> survives
               ("node_modules/lodash/index.js::each:1", "x"),  # dropped src (key) -> gone
               ("public/foo.js::y:1", "z")]                    # vendor path src (not a node) -> gone
    saved = os.environ.pop("DEEPMAP_INCLUDE_VENDOR", None)
    try:
        kn, kc, ki = S._maybe_exclude_vendor(root, nodes, calls, imports)
    finally:
        if saved is not None:
            os.environ["DEEPMAP_INCLUDE_VENDOR"] = saved
    kept_files = {n["source_file"] for n in kn}
    check("hand-written app/models/user.rb survives", "app/models/user.rb" in kept_files)
    check("public/ bundle + jquery.min.js dropped",
          "public/js/jquery.min.js" not in kept_files and "public/application-abc.js" not in kept_files)
    check("node_modules + vendor/ dropped",
          "node_modules/lodash/index.js" not in kept_files and "vendor/assets/x.rb" not in kept_files)
    check("*.min.js dropped even outside a vendor dir (app/assets/app.min.js)",
          "app/assets/app.min.js" not in kept_files)
    check("dir match is a COMPONENT, not a substring (src/publication.py survives)",
          "src/publication.py" in kept_files)
    check("dir match is a COMPONENT, not a substring (libvendor/x.js survives)",
          "libvendor/x.js" in kept_files)
    check("calls from a dropped node are pruned; kept node's call survives",
          kc == [("app/models/user.rb::User:1", "each")], str(kc))
    check("imports from dropped/vendor srcs pruned; kept import survives",
          ki == [("app/models/user.rb::User:1", "lodash")], str(ki))


def gate_byte_identity(S) -> None:
    print("\n-- byte-identity: escape hatch + no-vendor tree return the SAME objects --")
    root = _no_gitignore_dir()
    # (a) escape hatch -> same objects.
    nodes, calls, imports = _nodes(), [("a::b:1", "c")], [("a::b:1", "d")]
    os.environ["DEEPMAP_INCLUDE_VENDOR"] = "1"
    try:
        kn, kc, ki = S._maybe_exclude_vendor(root, nodes, calls, imports)
    finally:
        os.environ.pop("DEEPMAP_INCLUDE_VENDOR", None)
    check("DEEPMAP_INCLUDE_VENDOR=1 -> the SAME node/call/import objects (identity)",
          kn is nodes and kc is calls and ki is imports)
    # (b) a tree with NO vendored files -> same objects (the STAGE-0-oracle safety property).
    clean = [{"key": "build/sync.py::sync:1", "source_file": "build/sync.py"},
             {"key": "scripts/run.py::main:1", "source_file": "scripts/run.py"}]
    cc, ci = [("build/sync.py::sync:1", "push")], [("build/sync.py::sync:1", "os")]
    kn2, kc2, ki2 = S._maybe_exclude_vendor(root, clean, cc, ci)
    check("no-vendor tree -> SAME objects (build/ is SOURCE here, never dropped by the static set)",
          kn2 is clean and kc2 is cc and ki2 is ci)


def gate_gitignore(S) -> None:
    print("\n-- .gitignore bare-dir entries honored; anchored/glob NOT --")
    d = Path(tempfile.mkdtemp(prefix="dm_vendor_gi_"))
    (d / ".gitignore").write_text(
        "# comment\ngraphify-out/\n/anchored\n*.log\n!keep\nnested/dir\n", encoding="utf-8")
    parts = S._gitignore_dir_parts(d)
    check("bare 'graphify-out/' honored as a dir part", "graphify-out" in parts, str(parts))
    check("anchored '/anchored' NOT honored (needs a full engine)", "anchored" not in parts, str(parts))
    check("glob '*.log' NOT honored", not any("log" in p for p in parts), str(parts))
    check("negation '!keep' NOT honored", "keep" not in parts, str(parts))
    check("multi-segment 'nested/dir' NOT honored as a bare dir", "dir" not in parts and "nested" not in parts,
          str(parts))
    nodes = [{"key": "graphify-out/cache/x.py::f:1", "source_file": "graphify-out/cache/x.py"},
             {"key": "anchored/x.py::g:1", "source_file": "anchored/x.py"},
             {"key": "app/models/user.rb::U:1", "source_file": "app/models/user.rb"}]
    saved = os.environ.pop("DEEPMAP_INCLUDE_VENDOR", None)
    try:
        kn, _, _ = S._maybe_exclude_vendor(d, nodes, [], [])
    finally:
        if saved is not None:
            os.environ["DEEPMAP_INCLUDE_VENDOR"] = saved
    kept = {n["source_file"] for n in kn}
    check("a node under the gitignored 'graphify-out/' is dropped", "graphify-out/cache/x.py" not in kept)
    check("a node under the ANCHORED '/anchored' survives (anchored not honored)",
          "anchored/x.py" in kept)
    check("hand-written source under app/models survives", "app/models/user.rb" in kept)


def gate_wiring(S) -> None:
    print("\n-- _maybe_exclude_vendor is wired into BOTH _rebuild_code convergence points --")
    src = (BUILD / "sync.py").read_text(encoding="utf-8")
    # both the graphify and the ast convergence wrap _maybe_exclude_tests in _maybe_exclude_vendor.
    n = src.count("_maybe_exclude_vendor(\n") + src.count("_maybe_exclude_vendor(")
    check("_maybe_exclude_vendor is called (defined + >=2 call sites)",
          src.count("_maybe_exclude_vendor(") >= 3, f"occurrences={src.count('_maybe_exclude_vendor(')}")
    check("it wraps _maybe_exclude_tests at the convergence (vendor over tests)",
          "_maybe_exclude_vendor(\n                workspace_root, *_maybe_exclude_tests(" in src
          or "_maybe_exclude_vendor(\n        workspace_root, *_maybe_exclude_tests(" in src)


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 64)
    print("DeepMap — vendor/generated path-ignore gates (no Neo4j)")
    print("=" * 64)
    S = _load_sync()
    gate_default_on(S)
    gate_byte_identity(S)
    gate_gitignore(S)
    gate_wiring(S)
    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
