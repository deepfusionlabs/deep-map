#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Round-11c engine gate (WE, C2 + C3): which call sites belong to a Python :Module caller when P1 (the receiver
un-keyer) judges a keyed call — DEEPMAP_GRAPHIFY_CALLS_PLUS / DEEPMAP_PRECISE_CALLS only; the default graph never
runs P1.

C2 (LOW): R11/R2 scoped a :Module's call sites to its MODULE-LEVEL statements (a def's / class's body is that
node's). But a def / class the graph has NO node for — graphify merges ids: if/else twin defs, `def config` beside
`class Config` (case-folded), `_main` beside `main` (leading underscore stripped), a duplicate def — has its calls
attributed to the :Module by _calls_plus, while P1 did not look there: a correct keyed edge (the twin's bare
`main()` -> the same file's main) was judged receiver-only against the module's `engine_cli.main()` and demoted
EXTRACTED -> AMBIGUOUS (a fan-out to every `main`). Fix: graphify_extract._py_module_scopes (ONE rule, read by
map-time _receiver_unkeyer and push-time sync._unkey_receiver_calls) adds one scope per node-less def / class —
_calls_plus's own has-a-node test — each read with its own imports and values over the module's. The smaller of
the plan's two options: "never return 'name' for a :Module caller" would also keep C3's wrong key below.
C3 (LOW): R2's "an import-bound name is never a shadowing value" (local - aliases) also ignored a genuine
module-level REBINDING (`import helpers as h` / `h = Wrapper()` / `h.helper()` -> a wrong INFERRED edge into
helpers.helper). Fix (_py_scope_local): an import-bound name stays the import only when every other binding of it
is a FALL-BACK — `name = None`, or an assignment in an `except` of a `try` that imports it; anything else makes it
a value (the call is then a receiver call: the flag-off name fan-out). A lambda parameter / comprehension target
never rebinds it (own scope). The same binding is what a def WITH a node reads when it uses that global without
importing the name itself (_py_rebound_globals): `def use(): return hh.assist()` after `hh = Proxy2()` is un-keyed
too, while a def that imports hh itself keeps its key.

Sections (hermetic unless noted — temp dirs, no Neo4j / network):
  A  the shared rule on ast sources: _py_region / _py_module_scopes / _py_scope_local; sync's wrappers agree
  B  push time: sync's passes on a frozen graphify 0.8.39 graph of the probe tree, CALLS_PLUS (+ precise): the
     four C2 keys stay EXTRACTED; R2's control (a def WITH a node) keeps only its own key; C3's rebinding is
     un-keyed (at module level and in a def reading it); its fall-back controls keep their keys
  C  map time: graphify_extract._receiver_unkeyer on synthetic engine links from the :Module callers (precise)
  D  the REAL engine on the probe tree (skipped when graphify is absent)
  S  S1b (typed-receiver call-linking design 3.1 / 6.1 / 9 S1b): CALLS_PLUS resolves a module name by IMPORT ROOT
     (build/py_import_resolver: the repository root, `src/`, each top-level package's parent, the importer's own
     directory only outside a package; relative imports by package path), never by the dotted-suffix match that read
     flask's `import json` inside src/flask/json/provider.py as flask's own json package (flask at the benchmark pin,
     each engine: DefaultJSONProvider.dumps / .loads -> flask.json.dumps / .loads, 2 false INFERRED edges, the only
     change on the five Python corpora). On the S1b tree's captured graph.json of graphify 0.8.39 AND 0.8.51 (and the
     installed engine): exactly
     the five import-root calls are added; the stdlib-shadow, no-implicit-relative, two-roots, sys.path-only and
     above-the-root shapes add nothing; the resolver is read only when CALLS_PLUS is on, and without it nothing
     module-qualified is guessed. `--capture` re-writes tests/fixtures/typed_calls/<engine>/precise/
     calls_plus_roots.json under the installed engine (run it under 0.8.39 and 0.8.51 after editing S1B_TREE).
  Z  the flags-off and precise-only pushes, and every non-Module caller's edges, byte-identical to the pre-fix build
"""
from __future__ import annotations

import ast
import contextlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build"
sys.path.insert(0, str(BUILD))

import graphify_extract as GX  # noqa: E402

_PASS = _FAIL = 0
_TMP = Path(tempfile.mkdtemp(prefix="dm_r11c_c23_")).resolve()   # created by THIS gate, removed by exact path
_FLAGS = ("DEEPMAP_PRECISE_CALLS", "DEEPMAP_GRAPHIFY_CALLS_PLUS", "DEEPMAP_GRAPHIFY_CALLS_PLUS_TESTS",
          "DEEPMAP_LOCAL_DEF_CALLS", "DEEPMAP_FALSE_CALL_FILTER", "DEEPMAP_DROP_APP_TO_TEST_CALLS",
          "DEEPMAP_SELF_CALL_REBIND", "DEEPMAP_EXTRACTOR", "DEEPMAP_EXCLUDE_TESTS", "DEEPMAP_INCLUDE_VENDOR",
          "DEEPMAP_RESOLVED_IMPORTS", "DEEPMAP_SUPPLEMENTAL_TS_RUST", "DEEPMAP_MODULE_DOCSTRINGS",
          "DEEPMAP_SUMMARY_NORMALIZE", "DEEPMAP_CASE_INSENSITIVE_EXT", "DEEPMAP_RESCUE_SKIPPED_DIRS",
          "DEEPMAP_CLEAN_OWNER_NAMES", "DEEPMAP_PRECISE_FIXES", "DEEPMAP_CALLS_FAMILY_FILTER")
FX = REPO / "tests" / "fixtures" / "typed_calls"
BUCKETS = {"0.8.39": "0839", "0.8.51": "0851"}
MODES = {"off": {}, "plus": {"DEEPMAP_GRAPHIFY_CALLS_PLUS": "1"}, "precise": {"DEEPMAP_PRECISE_CALLS": "1"},
         "both": {"DEEPMAP_PRECISE_CALLS": "1", "DEEPMAP_GRAPHIFY_CALLS_PLUS": "1"}}


def check(label: str, cond: bool, detail="") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {label}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}{(' — ' + str(detail)[:900]) if detail != '' else ''}")


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


def _mode_env(mode: str, **extra) -> dict:
    env = {f: None for f in _FLAGS}
    env.update(MODES[mode])
    env.update(extra)
    return env


def _load(name: str, alias: str):
    spec = importlib.util.spec_from_file_location(alias, BUILD / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


S = _load("sync", "dm_r11c_c23_sync")


# ---- the probe tree (the R2 skeptics' merged-id shapes + the rebinding) --------------------------------------
TREE = {
    # C2: an if/else twin def — the else-branch `runner` has no node (one id); its bare main() is the module's call
    "app/twin.py": (
        "import sys\n"
        "import engine_cli\n"
        "\n"
        "\n"
        "def main():\n"
        "    return 0\n"
        "\n"
        "\n"
        "if sys.platform == 'win32':\n"
        "    def runner():\n"
        "        return 1\n"
        "else:\n"
        "    def runner():\n"
        "        return main()\n"
        "\n"
        "\n"
        "engine_cli.main()\n"
    ),
    # C2: `def config` beside `class Config` (graphify ids are case-folded: one node) — the class body's load()
    "app/cfg.py": (
        "import settings\n"
        "\n"
        "\n"
        "def load():\n"
        "    return {}\n"
        "\n"
        "\n"
        "def config():\n"
        "    return 1\n"
        "\n"
        "\n"
        "class Config:\n"
        "    data = load()\n"
        "\n"
        "\n"
        "settings.load()\n"
    ),
    # C2: `_main` beside `main` (graphify strips the leading underscore: one id)
    "app/entry.py": (
        "import engine_cli\n"
        "\n"
        "\n"
        "def main():\n"
        "    return 0\n"
        "\n"
        "\n"
        "def _main():\n"
        "    return main()\n"
        "\n"
        "\n"
        "engine_cli.main()\n"
    ),
    # C2: a duplicate def — one of the two `helper`s has no node
    "app/dup.py": (
        "import engine_cli\n"
        "\n"
        "\n"
        "def main():\n"
        "    return 0\n"
        "\n"
        "\n"
        "def helper():\n"
        "    return 1\n"
        "\n"
        "\n"
        "def helper():\n"
        "    return main()\n"
        "\n"
        "\n"
        "engine_cli.main()\n"
    ),
    # R2 control: a def WITH a node — its receiver call is that def's, never the module's
    "app/tool.py": (
        "import engine_cli\n"
        "\n"
        "\n"
        "def main():\n"
        "    return engine_cli.main()\n"
        "\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    main()\n"
    ),
    # C3: an import-bound name REBOUND at module level — `h.helper()` is Wrapper's, not helpers.helper
    "app/rebind.py": (
        "import helpers as h\n"
        "\n"
        "\n"
        "class Wrapper:\n"
        "    def helper(self):\n"
        "        return 9\n"
        "\n"
        "\n"
        "h = Wrapper()\n"
        "h.helper()\n"
    ),
    # C3 control: the fall-back idiom — `_h = None` in the except of the try that imports it is the same variable
    "app/hook.py": (
        "try:\n"
        "    import helpers as _h\n"
        "except ImportError:\n"
        "    _h = None\n"
        "\n"
        "\n"
        "if _h is not None:\n"
        "    _h.helper()\n"
    ),
    # C3 control: a module-level `name = None` reset of an import-bound name is a fall-back too
    "app/reset.py": (
        "import helpers as hp\n"
        "\n"
        "if not hasattr(hp, 'helper'):\n"
        "    hp = None\n"
        "\n"
        "hp.helper()\n"
    ),
    # C3 in a def: a def WITH a node reading the rebound global (`hh = Proxy2()`) — unless it imports hh itself
    "app/rebind2.py": (
        "import helpers as hh\n"
        "\n"
        "\n"
        "class Proxy2:\n"
        "    def assist(self):\n"
        "        return 9\n"
        "\n"
        "\n"
        "hh = Proxy2()\n"
        "\n"
        "\n"
        "def use():\n"
        "    return hh.assist()\n"
        "\n"
        "\n"
        "def use_own():\n"
        "    import helpers as hh\n"
        "    return hh.assist()\n"
    ),
    "helpers.py": (
        "def helper():\n"
        "    return 1\n"
        "\n"
        "\n"
        "def assist():\n"
        "    return 2\n"
    ),
    "other/x.py": "def main():\n    return 9\n\n\ndef load():\n    return 2\n",
    "other/y.py": "def main():\n    return 8\n\n\ndef load():\n    return 3\n",
}

# graphify 0.8.39's graph.json for TREE (captured from the real engine; nodes' _origin and links' weight dropped)
GRAPH_JSON = r"""
{"nodes": [
{"file_type": "code", "id": "app_cfg", "label": "cfg.py", "source_file": "app/cfg.py", "source_location": "L1"},
{"file_type": "code", "id": "app_cfg_load", "label": "load()", "source_file": "app/cfg.py", "source_location": "L4"},
{"file_type": "code", "id": "app_cfg_config", "label": "config()", "source_file": "app/cfg.py", "source_location": "L8"},
{"file_type": "code", "id": "app_dup", "label": "dup.py", "source_file": "app/dup.py", "source_location": "L1"},
{"file_type": "code", "id": "app_dup_main", "label": "main()", "source_file": "app/dup.py", "source_location": "L4"},
{"file_type": "code", "id": "app_dup_helper", "label": "helper()", "source_file": "app/dup.py", "source_location": "L8"},
{"file_type": "code", "id": "app_entry", "label": "entry.py", "source_file": "app/entry.py", "source_location": "L1"},
{"file_type": "code", "id": "app_entry_main", "label": "main()", "source_file": "app/entry.py", "source_location": "L4"},
{"file_type": "code", "id": "app_hook", "label": "hook.py", "source_file": "app/hook.py", "source_location": "L1"},
{"file_type": "code", "id": "app_rebind", "label": "rebind.py", "source_file": "app/rebind.py", "source_location": "L1"},
{"file_type": "code", "id": "app_rebind_wrapper", "label": "Wrapper", "source_file": "app/rebind.py", "source_location": "L4"},
{"file_type": "code", "id": "app_rebind_wrapper_helper", "label": ".helper()", "source_file": "app/rebind.py", "source_location": "L5"},
{"file_type": "code", "id": "app_rebind2", "label": "rebind2.py", "source_file": "app/rebind2.py", "source_location": "L1"},
{"file_type": "code", "id": "app_rebind2_proxy2", "label": "Proxy2", "source_file": "app/rebind2.py", "source_location": "L4"},
{"file_type": "code", "id": "app_rebind2_proxy2_assist", "label": ".assist()", "source_file": "app/rebind2.py", "source_location": "L5"},
{"file_type": "code", "id": "app_rebind2_use", "label": "use()", "source_file": "app/rebind2.py", "source_location": "L12"},
{"file_type": "code", "id": "app_rebind2_use_own", "label": "use_own()", "source_file": "app/rebind2.py", "source_location": "L16"},
{"file_type": "code", "id": "app_reset", "label": "reset.py", "source_file": "app/reset.py", "source_location": "L1"},
{"file_type": "code", "id": "app_tool", "label": "tool.py", "source_file": "app/tool.py", "source_location": "L1"},
{"file_type": "code", "id": "app_tool_main", "label": "main()", "source_file": "app/tool.py", "source_location": "L4"},
{"file_type": "code", "id": "app_twin", "label": "twin.py", "source_file": "app/twin.py", "source_location": "L1"},
{"file_type": "code", "id": "app_twin_main", "label": "main()", "source_file": "app/twin.py", "source_location": "L5"},
{"file_type": "code", "id": "app_twin_runner", "label": "runner()", "source_file": "app/twin.py", "source_location": "L10"},
{"file_type": "code", "id": "helpers", "label": "helpers.py", "source_file": "helpers.py", "source_location": "L1"},
{"file_type": "code", "id": "helpers_helper", "label": "helper()", "source_file": "helpers.py", "source_location": "L1"},
{"file_type": "code", "id": "helpers_assist", "label": "assist()", "source_file": "helpers.py", "source_location": "L5"},
{"file_type": "code", "id": "other_x", "label": "x.py", "source_file": "other/x.py", "source_location": "L1"},
{"file_type": "code", "id": "other_x_main", "label": "main()", "source_file": "other/x.py", "source_location": "L1"},
{"file_type": "code", "id": "other_x_load", "label": "load()", "source_file": "other/x.py", "source_location": "L5"},
{"file_type": "code", "id": "other_y", "label": "y.py", "source_file": "other/y.py", "source_location": "L1"},
{"file_type": "code", "id": "other_y_main", "label": "main()", "source_file": "other/y.py", "source_location": "L1"},
{"file_type": "code", "id": "other_y_load", "label": "load()", "source_file": "other/y.py", "source_location": "L5"}
], "links": [
{"confidence": "EXTRACTED", "context": "import", "relation": "imports", "source": "app_cfg", "source_file": "app/cfg.py", "source_location": "L1", "target": "settings"},
{"confidence": "EXTRACTED", "relation": "contains", "source": "app_cfg", "source_file": "app/cfg.py", "source_location": "L4", "target": "app_cfg_load"},
{"confidence": "EXTRACTED", "relation": "contains", "source": "app_cfg", "source_file": "app/cfg.py", "source_location": "L8", "target": "app_cfg_config"},
{"confidence": "EXTRACTED", "relation": "contains", "source": "app_cfg", "source_file": "app/cfg.py", "source_location": "L12", "target": "app_cfg_config"},
{"confidence": "EXTRACTED", "context": "import", "relation": "imports", "source": "app_dup", "source_file": "app/dup.py", "source_location": "L1", "target": "engine_cli"},
{"confidence": "EXTRACTED", "relation": "contains", "source": "app_dup", "source_file": "app/dup.py", "source_location": "L4", "target": "app_dup_main"},
{"confidence": "EXTRACTED", "relation": "contains", "source": "app_dup", "source_file": "app/dup.py", "source_location": "L8", "target": "app_dup_helper"},
{"confidence": "EXTRACTED", "relation": "contains", "source": "app_dup", "source_file": "app/dup.py", "source_location": "L12", "target": "app_dup_helper"},
{"confidence": "EXTRACTED", "context": "call", "relation": "calls", "source": "app_dup_helper", "source_file": "app/dup.py", "source_location": "L13", "target": "app_dup_main"},
{"confidence": "EXTRACTED", "context": "import", "relation": "imports", "source": "app_entry", "source_file": "app/entry.py", "source_location": "L1", "target": "engine_cli"},
{"confidence": "EXTRACTED", "relation": "contains", "source": "app_entry", "source_file": "app/entry.py", "source_location": "L4", "target": "app_entry_main"},
{"confidence": "EXTRACTED", "relation": "contains", "source": "app_entry", "source_file": "app/entry.py", "source_location": "L8", "target": "app_entry_main"},
{"confidence": "EXTRACTED", "context": "import", "relation": "imports", "source": "app_hook", "source_file": "app/hook.py", "source_location": "L2", "target": "helpers"},
{"confidence": "EXTRACTED", "context": "import", "relation": "imports", "source": "app_rebind", "source_file": "app/rebind.py", "source_location": "L1", "target": "helpers"},
{"confidence": "EXTRACTED", "relation": "contains", "source": "app_rebind", "source_file": "app/rebind.py", "source_location": "L4", "target": "app_rebind_wrapper"},
{"confidence": "EXTRACTED", "relation": "method", "source": "app_rebind_wrapper", "source_file": "app/rebind.py", "source_location": "L5", "target": "app_rebind_wrapper_helper"},
{"confidence": "EXTRACTED", "context": "import", "relation": "imports", "source": "app_rebind2", "source_file": "app/rebind2.py", "source_location": "L1", "target": "helpers"},
{"confidence": "EXTRACTED", "relation": "contains", "source": "app_rebind2", "source_file": "app/rebind2.py", "source_location": "L4", "target": "app_rebind2_proxy2"},
{"confidence": "EXTRACTED", "relation": "method", "source": "app_rebind2_proxy2", "source_file": "app/rebind2.py", "source_location": "L5", "target": "app_rebind2_proxy2_assist"},
{"confidence": "EXTRACTED", "relation": "contains", "source": "app_rebind2", "source_file": "app/rebind2.py", "source_location": "L12", "target": "app_rebind2_use"},
{"confidence": "EXTRACTED", "relation": "contains", "source": "app_rebind2", "source_file": "app/rebind2.py", "source_location": "L16", "target": "app_rebind2_use_own"},
{"confidence": "EXTRACTED", "context": "call", "relation": "calls", "source": "app_rebind2_use", "source_file": "app/rebind2.py", "source_location": "L13", "target": "app_rebind2_proxy2_assist"},
{"confidence": "EXTRACTED", "context": "call", "relation": "calls", "source": "app_rebind2_use_own", "source_file": "app/rebind2.py", "source_location": "L18", "target": "app_rebind2_proxy2_assist"},
{"confidence": "EXTRACTED", "context": "import", "relation": "imports", "source": "app_reset", "source_file": "app/reset.py", "source_location": "L1", "target": "helpers"},
{"confidence": "EXTRACTED", "context": "import", "relation": "imports", "source": "app_tool", "source_file": "app/tool.py", "source_location": "L1", "target": "engine_cli"},
{"confidence": "EXTRACTED", "relation": "contains", "source": "app_tool", "source_file": "app/tool.py", "source_location": "L4", "target": "app_tool_main"},
{"confidence": "EXTRACTED", "context": "import", "relation": "imports", "source": "app_twin", "source_file": "app/twin.py", "source_location": "L1", "target": "sys"},
{"confidence": "EXTRACTED", "context": "import", "relation": "imports", "source": "app_twin", "source_file": "app/twin.py", "source_location": "L2", "target": "engine_cli"},
{"confidence": "EXTRACTED", "relation": "contains", "source": "app_twin", "source_file": "app/twin.py", "source_location": "L5", "target": "app_twin_main"},
{"confidence": "EXTRACTED", "relation": "contains", "source": "app_twin", "source_file": "app/twin.py", "source_location": "L10", "target": "app_twin_runner"},
{"confidence": "EXTRACTED", "relation": "contains", "source": "app_twin", "source_file": "app/twin.py", "source_location": "L13", "target": "app_twin_runner"},
{"confidence": "EXTRACTED", "context": "call", "relation": "calls", "source": "app_twin_runner", "source_file": "app/twin.py", "source_location": "L14", "target": "app_twin_main"},
{"confidence": "EXTRACTED", "relation": "contains", "source": "helpers", "source_file": "helpers.py", "source_location": "L1", "target": "helpers_helper"},
{"confidence": "EXTRACTED", "relation": "contains", "source": "helpers", "source_file": "helpers.py", "source_location": "L5", "target": "helpers_assist"},
{"confidence": "EXTRACTED", "relation": "contains", "source": "other_x", "source_file": "other/x.py", "source_location": "L1", "target": "other_x_main"},
{"confidence": "EXTRACTED", "relation": "contains", "source": "other_x", "source_file": "other/x.py", "source_location": "L5", "target": "other_x_load"},
{"confidence": "EXTRACTED", "relation": "contains", "source": "other_y", "source_file": "other/y.py", "source_location": "L1", "target": "other_y_main"},
{"confidence": "EXTRACTED", "relation": "contains", "source": "other_y", "source_file": "other/y.py", "source_location": "L5", "target": "other_y_load"}
]}
"""


def _write_tree(root: Path, tree: dict) -> Path:
    for rel, text in tree.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode("utf-8"))
    return root


def _graph() -> dict:
    return json.loads(GRAPH_JSON)


def _passes(root: Path, nodes: list, calls: list):
    """sync's push-time passes in _sync_pass order, then _resolve_calls (what push_to_neo4j persists)."""
    with contextlib.redirect_stderr(io.StringIO()):
        c2, _n = S._unkey_receiver_calls(root, nodes, list(calls))
        S._attach_bare_calls(root, nodes, c2)
        S._attach_local_defs(root, nodes, c2)
        S._attach_local_def_calls(root, nodes, c2)
        return S._resolve_calls(nodes, c2), c2


def _run(mode: str, root: Path, graph=None):
    with _env(**_mode_env(mode)), contextlib.redirect_stderr(io.StringIO()):
        nodes, calls, _imports = GX.map_graph(graph or _graph(), root=root)
        edges, c2 = _passes(root, nodes, calls)
    return nodes, calls, c2, edges


def _of(edges: dict, caller: str) -> dict:
    return {b: c for (a, b), c in edges.items() if a == caller}


def _labels(nodes) -> dict:
    return {n["key"]: n["label"] for n in nodes}


def _fan(own: str, name: str) -> dict:
    """A 'both' verdict: the caller's own key (EXTRACTED, same file) + the name fan-out for the receiver site."""
    pool = {"main": ("app_dup_main", "app_entry_main", "app_tool_main", "app_twin_main", "other_x_main",
                     "other_y_main"),
            "load": ("app_cfg_load", "other_x_load", "other_y_load")}[name]
    return {k: ("EXTRACTED" if k == own else "AMBIGUOUS") for k in pool}


# the :Module callers' pushed CALLS with DEEPMAP_GRAPHIFY_CALLS_PLUS (alone or with precise)
MODULE_EDGES = {
    "app_twin": _fan("app_twin_main", "main"),       # C2: the else-branch runner's bare main() (no node)
    "app_cfg": _fan("app_cfg_load", "load"),         # C2: class Config's body (config() took the id)
    "app_entry": _fan("app_entry_main", "main"),     # C2: _main's body (main took the id)
    "app_dup": _fan("app_dup_main", "main"),         # C2: the duplicate helper's body
    "app_tool": {"app_tool_main": "EXTRACTED"},      # R2 control: def main HAS a node — its receiver call is its own
    "app_rebind": {"app_rebind_wrapper": "EXTRACTED", "helpers_helper": "AMBIGUOUS",   # C3: h = Wrapper()
                   "app_rebind_wrapper_helper": "AMBIGUOUS", "app_dup_helper": "AMBIGUOUS"},
    "app_hook": {"helpers_helper": "INFERRED"},      # C3 control: `_h = None` in the except of the importing try
    "app_reset": {"helpers_helper": "INFERRED"},     # C3 control: `hp = None` reset
}
# the pre-fix build (11b) on the same graph: the pushes C2 / C3 must not touch (frozen)
_PRE_TWIN_DUP = [["app_dup_helper", k, "AMBIGUOUS"] for k in ("app_dup_main", "app_entry_main", "app_tool_main",
                                                               "app_twin_main", "other_x_main", "other_y_main")]
_PRE_RUNNER = [["app_twin_runner", k, "AMBIGUOUS"] for k in ("app_dup_main", "app_entry_main", "app_tool_main",
                                                              "app_twin_main", "other_x_main", "other_y_main")]
PRE_OFF = (_PRE_TWIN_DUP + [["app_rebind2_use", "app_rebind2_proxy2_assist", "INFERRED"],
                            ["app_rebind2_use_own", "app_rebind2_proxy2_assist", "INFERRED"]] + _PRE_RUNNER)
PRE_PRECISE = [["app_dup_helper", "app_dup_main", "EXTRACTED"],
               ["app_rebind2_use", "app_rebind2_proxy2_assist", "EXTRACTED"],
               ["app_rebind2_use_own", "app_rebind2_proxy2_assist", "EXTRACTED"],
               ["app_twin_runner", "app_twin_main", "EXTRACTED"]]
_PRE_ASSIST = [["app_rebind2_use", "helpers_assist", "INFERRED"], ["app_rebind2_use_own", "helpers_assist", "INFERRED"]]
PRE_NON_MODULE = {"off": PRE_OFF, "plus": PRE_OFF + _PRE_ASSIST, "precise": PRE_PRECISE,
                  "both": PRE_PRECISE + _PRE_ASSIST}
# the ONE non-Module edge C3 changes (a def WITH a node reading the rebound global): INFERRED -> AMBIGUOUS
C3_DEF_CHANGE = {("app_rebind2_use", "helpers_assist"): "AMBIGUOUS"}
# ... and what the pre-fix build pushed for the :Module callers C2 / C3 change (the RED this gate catches)
PRE_DEMOTED = {"app_twin": "app_twin_main", "app_cfg": "app_cfg_load", "app_entry": "app_entry_main",
               "app_dup": "app_dup_main"}


# ---- A ---------------------------------------------------------------------------------------------------------
SRC_C2 = (
    "import sys\n"
    "import engine_cli\n"
    "\n"
    "\n"
    "def main():\n"
    "    return 0\n"
    "\n"
    "\n"
    "if sys.platform == 'win32':\n"
    "    def runner():\n"
    "        return in_twin_a()\n"
    "else:\n"
    "    def runner(engine_cli=None):\n"
    "        import extra_mod as xm\n"
    "\n"
    "        def inner():\n"
    "            return nested_fn()\n"
    "        return main() or xm.go() or engine_cli.main()\n"
    "\n"
    "\n"
    "class Config:\n"
    "    data = cls_body_fn()\n"
    "\n"
    "    def method(self):\n"
    "        return method_body_fn()\n"
    "\n"
    "\n"
    "def config():\n"
    "    return node_def_fn()\n"
    "\n"
    "\n"
    "engine_cli.main()\n"
)


def _called(nodes) -> set:
    out = set()
    for x in nodes:
        if isinstance(x, ast.Call):
            f = x.func
            out.add(f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else "?")
    return out


def _defs(tree) -> dict:
    """{(name, occurrence): def / class node} in source order."""
    out, seen = {}, {}
    for x in sorted((y for y in ast.walk(tree) if isinstance(y, (ast.FunctionDef, ast.ClassDef))),
                    key=lambda y: y.lineno):
        seen[x.name] = seen.get(x.name, 0) + 1
        out[(x.name, seen[x.name])] = x
    return out


def _local_of(src: str) -> set:
    tree = ast.parse(src)
    nodes, aliases, local = GX._py_module_scopes(tree)[0]
    return local


def gate_a_rule() -> None:
    print("\n-- A: the shared scope rule (graphify_extract._py_module_scopes; sync delegates) --")
    tree = ast.parse(SRC_C2)
    d = _defs(tree)
    # the graph's nodes: main, the if-branch runner, Config.method and config(); the else-branch runner, class
    # Config (its id went to config()) and the nested inner have none
    keys = {(d[k].lineno, d[k].name) for k in (("main", 1), ("runner", 1), ("method", 1), ("config", 1))}
    has_node = GX._py_def_has_node(keys)
    bare = GX._py_module_level_nodes(tree)
    check("without has_node: the module-level statements only (R2 unchanged) — one call, engine_cli.main()",
          _called(bare) == {"main"} and not any(isinstance(x, ast.Call) and isinstance(x.func, ast.Name)
                                                for x in bare), sorted(_called(bare)))
    full = GX._py_module_level_nodes(tree, has_node)
    got = _called(full)
    check("with has_node: + the node-less else-branch runner (its nested def too) and class Config's body",
          {"main", "go", "nested_fn", "cls_body_fn"} <= got, sorted(got))
    check("... but never the body of a def / class that HAS a node (if-branch runner, Config.method, config())",
          not ({"in_twin_a", "method_body_fn", "node_def_fn"} & got), sorted(got & {"in_twin_a", "method_body_fn",
                                                                                  "node_def_fn"}))
    scopes = GX._py_module_scopes(tree, has_node)
    check("one scope for the module + one per node-less def / class reached from it (else-runner, Config)",
          len(scopes) == 3, len(scopes))
    kinds: dict = {}
    for nodes, aliases, local in scopes:
        for nm, got_k in S._py_site_kinds(tree, {"main", "go"}, aliases, local, nodes=nodes).items():
            kinds.setdefault(nm, []).extend(got_k)
    check("site kinds: the module's `engine_cli.main()` is a module call; inside the twin, `main()` is bare and "
          "`engine_cli.main()` a MEMBER call (its parameter shadows the import); `xm.go()` uses the twin's own import",
          sorted(kinds.get("main", [])) == sorted([("mod", "engine_cli"), ("bare", None), ("member", None)])
          and kinds.get("go") == [("mod", "extra_mod")], kinds)
    m_local = scopes[0][2]
    check("the twin's parameter / import never leak into the MODULE's scope (engine_cli stays the import there)",
          "engine_cli" not in m_local and "xm" not in scopes[0][1], (sorted(m_local), sorted(scopes[0][1])))
    snodes = S._py_module_level_nodes(tree, has_node)
    check("sync._py_module_level_nodes walks exactly graphify_extract's nodes (map time == push time)",
          [id(x) for x in snodes] == [id(x) for x in full])
    facts = S._py_module_scope_facts(tree)
    check("sync._py_module_scope_facts still returns the module's own (nodes, aliases, local)",
          isinstance(facts, tuple) and len(facts) == 3 and [id(x) for x in facts[0]] == [id(x) for x in bare],
          type(facts))
    keys_s = S._py_def_keys([{"label": "Function", "name": "main", "source_file": "a.py", "start_line": 5},
                             {"label": "Method", "name": "Config.method", "source_file": "a.py", "start_line": 24},
                             {"label": "Module", "name": "a", "source_file": "a.py", "start_line": 1},
                             {"label": "Function", "name": "x", "source_file": "b.py", "start_line": 1}])
    check("sync._py_def_keys: per file, Function / Method / Class only, bare names at their start line",
          keys_s == {"a.py": {(5, "main"), (24, "method")}, "b.py": {(1, "x")}}, keys_s)
    # ---- C3: _py_scope_local ----
    cases = [
        ("import helpers as h\nh = Wrapper()\nh.helper()\n", "h", True, "`h = Wrapper()` REBINDS the import"),
        ("try:\n    import helpers as _h\nexcept ImportError:\n    _h = None\n", "_h", False,
         "`_h = None` in the except of the importing try is its fall-back"),
        ("try:\n    import helpers as _h\nexcept ImportError:\n    _h = Stub()\n", "_h", False,
         "`_h = Stub()` in the except of the importing try is its fall-back"),
        ("import helpers as hp\nif cond:\n    hp = None\n", "hp", False, "a `hp = None` reset is a fall-back"),
        ("import helpers as hp\nhp: object = None\n", "hp", False, "an annotated `= None` is a fall-back"),
        ("import helpers as hp\nhp: object\n", "hp", False, "a bare annotation binds nothing"),
        ("import helpers as h\nfor h in xs:\n    pass\n", "h", True, "a for-target rebinds"),
        ("import helpers as h\nwith opener() as h:\n    pass\n", "h", True, "a with-target rebinds"),
        ("import helpers as h\nf = lambda h: h.helper()\n", "h", False, "a lambda parameter is its own scope"),
        ("import helpers as h\nys = [h for h in xs]\n", "h", False, "a comprehension target is its own scope"),
        ("import helpers as h\ntry:\n    pass\nexcept ImportError:\n    h = Stub()\n", "h", True,
         "an except of a try that does NOT import it rebinds"),
        ("import helpers as h\ntry:\n    import other\nexcept Exception as h:\n    pass\n", "h", True,
         "`except ... as h` rebinds"),
        ("try:\n    import a.b\nexcept ImportError:\n    a = None\n", "a", False,
         "`import a.b` binds a; its fall-back"),
        ("try:\n    from p import q\nexcept ImportError:\n    q = fallback_q\n", "q", False,
         "`from p import q` + the except's `q = fallback_q`"),
        ("x = 1\nfor item in xs:\n    pass\n", "item", True, "a name no import binds is a value, as before"),
        ("handler = lambda y: y\n", "handler", True, "... and so is a lambda-bound module variable"),
    ]
    for src, name, want, why in cases:
        got_l = _local_of(src)
        check(f"C3 _py_scope_local: {why} -> {name} {'IS' if want else 'is NOT'} a value", (name in got_l) == want,
              sorted(got_l))
    tree2 = ast.parse("import helpers as h\n\n\ndef f(h):\n    return h.x()\n\n\nh.helper()\n")
    d2 = _defs(tree2)
    sc2 = GX._py_module_scopes(tree2, GX._py_def_has_node(set()))    # f has no node
    check("C3 x C2: a node-less def's PARAMETER named like the import shadows it inside that def only",
          "h" not in sc2[0][2] and "h" in sc2[1][2], (sorted(sc2[0][2]), sorted(sc2[1][2])))
    sc3 = GX._py_module_scopes(tree2, GX._py_def_has_node({(d2[("f", 1)].lineno, "f")}))
    check("... and a def WITH a node adds no scope to the module", len(sc3) == 1, len(sc3))
    tree3 = ast.parse("import helpers as hh\nimport os\ntry:\n    import ujson as js\nexcept ImportError:\n"
                      "    js = None\nhh = Proxy2()\n\n\ndef f():\n    import json as late\n    return 1\n\n\n"
                      "late = 3\n")
    rb = GX._py_rebound_globals(tree3, S._import_aliases(tree3))
    check("_py_rebound_globals: the imported names the MODULE rebinds as a value (hh, and `late`, imported only in "
          "a def) — never a fall-back (js) or an untouched import (os)", rb == {"hh", "late"}, sorted(rb))


# ---- B ---------------------------------------------------------------------------------------------------------
def gate_b_push(root: Path) -> None:
    print("\n-- B: push time — the :Module callers under CALLS_PLUS (+ precise) --")
    for mode in ("plus", "both"):
        nodes, _calls, c2, edges = _run(mode, root)
        lab = _labels(nodes)
        e = dict(edges)
        for caller, want in MODULE_EDGES.items():
            check(f"{mode}: {caller} (a :Module) pushes exactly its expected CALLS", _of(e, caller) == want,
                  _of(e, caller))
        for caller, own in PRE_DEMOTED.items():
            check(f"{mode}: C2 — {caller}'s keyed call into its own {own.rsplit('_', 1)[-1]} stays a MARKED key",
                  any(a == caller and b == own and GX.is_call_key(b) for a, b in c2),
                  [(a, b, GX.is_call_key(b)) for a, b in c2 if a == caller])
        check(f"{mode}: C3 — app_rebind's `h.helper()` key into helpers.helper is un-keyed (the name fan-out)",
              ("app_rebind", "helpers_helper") not in [(a, b) for a, b in c2 if GX.is_call_key(b)]
              and ("app_rebind", "helper") in c2, [(a, b, GX.is_call_key(b)) for a, b in c2 if a == "app_rebind"])
        own = "INFERRED" if mode == "plus" else "EXTRACTED"
        check(f"{mode}: C3 in a def — use()'s `hh.assist()` (hh = Proxy2() at module level) is no longer a firm edge "
              "into helpers.assist (AMBIGUOUS name fan-out)",
              _of(e, "app_rebind2_use") == {"app_rebind2_proxy2_assist": own, "helpers_assist": "AMBIGUOUS"},
              _of(e, "app_rebind2_use"))
        check(f"{mode}: C3 in a def — use_own() imports hh itself, so its key into helpers.assist stays (INFERRED)",
              _of(e, "app_rebind2_use_own") == {"app_rebind2_proxy2_assist": own, "helpers_assist": "INFERRED"},
              _of(e, "app_rebind2_use_own"))
        check(f"{mode}: no :Module caller's key is into a :Module",
              not [(a, b) for a, b in c2 if lab.get(a) == "Module" and GX.is_call_key(b) and lab.get(b) == "Module"])


# ---- C ---------------------------------------------------------------------------------------------------------
def gate_c_map(root: Path) -> None:
    print("\n-- C: map time — graphify_extract._receiver_unkeyer on engine links from the :Module callers --")
    g = _graph()
    for src, tgt, line, sf in (("app_twin", "app_twin_main", "L14", "app/twin.py"),
                               ("app_cfg", "app_cfg_load", "L13", "app/cfg.py"),
                               ("app_rebind", "helpers_helper", "L10", "app/rebind.py"),
                               ("app_hook", "helpers_helper", "L8", "app/hook.py"),
                               ("app_tool", "app_tool_main", "L9", "app/tool.py"),
                               ("app_rebind2_use", "helpers_assist", "L13", "app/rebind2.py"),
                               ("app_rebind2_use_own", "helpers_assist", "L18", "app/rebind2.py")):
        g["links"].append({"confidence": "EXTRACTED", "context": "call", "relation": "calls", "source": src,
                           "source_file": sf, "source_location": line, "target": tgt})
    stats: dict = {}
    with _env(**_mode_env("precise")), contextlib.redirect_stderr(io.StringIO()):
        _n, calls, _i = GX.map_graph(g, root=root, stats=stats)

    def of(caller):
        return [(b, GX.is_call_key(b)) for a, b in calls if a == caller]

    check("C2: app_twin keeps its MARKED key and adds the name for the module-level receiver site ('both')",
          of("app_twin") == [("main", False), ("app_twin_main", True)], of("app_twin"))
    check("C2: app_cfg — class Config's bare load() keeps the key; `settings.load()` adds the name",
          of("app_cfg") == [("load", False), ("app_cfg_load", True)], of("app_cfg"))
    check("C3: app_rebind's key into helpers.helper becomes the bare name (h is a rebound value)",
          of("app_rebind") == [("helper", False)], of("app_rebind"))
    check("C3 control: app_hook keeps its key (`_h = None` is the import's fall-back)",
          of("app_hook") == [("helpers_helper", True)], of("app_hook"))
    check("R2 control: app_tool keeps its key alone (def main HAS a node: its engine_cli.main() is its own)",
          of("app_tool") == [("app_tool_main", True)], of("app_tool"))
    check("C3 in a def: use()'s key into helpers.assist becomes the bare name (it reads the rebound global)",
          ("helpers_assist", True) not in of("app_rebind2_use") and ("assist", False) in of("app_rebind2_use"),
          of("app_rebind2_use"))
    check("C3 in a def: use_own() imports hh itself — its key into helpers.assist stays",
          ("helpers_assist", True) in of("app_rebind2_use_own") and ("assist", False) not in of("app_rebind2_use_own"),
          of("app_rebind2_use_own"))
    check("stats: 2 un-keyed (app_rebind, use), 2 given the name too (app_twin, app_cfg)",
          stats.get("precise_unkeyed") == 2 and stats.get("precise_receiver_names") == 2, stats)


# ---- D ---------------------------------------------------------------------------------------------------------
def gate_d_real_engine() -> None:
    print("\n-- D: the REAL engine on the probe tree --")
    try:
        import graphify  # noqa: F401
    except Exception:
        print("  [SKIP] graphify is not installed in this interpreter — the real-engine section is not run")
        return
    root = _write_tree(_TMP / "real", TREE)
    for mode in ("plus", "both"):
        with _env(**_mode_env(mode, DEEPMAP_HOME=str(_TMP / "home"))), contextlib.redirect_stderr(io.StringIO()), \
                contextlib.redirect_stdout(io.StringIO()):
            res, rep = GX.extract_with_report(root)
            if not res:
                check(f"real engine ({mode}) produced a graph", False, rep.get("cause"))
                return
            nodes, calls, _i = res
            edges, _c2 = _passes(root, nodes, calls)
        e = dict(edges)
        bad = {c: _of(e, c) for c, want in MODULE_EDGES.items() if _of(e, c) != want}
        check(f"real ({mode}): every :Module caller pushes exactly its expected CALLS (C2 keys EXTRACTED, C3 un-keyed)",
              not bad, bad)
        check(f"real ({mode}): C3 in a def — use() -> helpers.assist AMBIGUOUS, use_own() -> helpers.assist INFERRED",
              e.get(("app_rebind2_use", "helpers_assist")) == "AMBIGUOUS"
              and e.get(("app_rebind2_use_own", "helpers_assist")) == "INFERRED",
              (_of(e, "app_rebind2_use"), _of(e, "app_rebind2_use_own")))


# ---- S (S1b) ---------------------------------------------------------------------------------------------------
# A flask-shaped package (`src/flk`, flk/json is the project's own json package) plus the import-root rules. Every
# caller below is a :Function / :Method the engine links nothing for, so what CALLS_PLUS adds is all that moves.
S1B_TREE = {
    "src/flk/__init__.py": (
        "from . import json as json\n"
        "\n"
        "\n"
        "def version():\n"
        "    return \"1\"\n"
    ),
    "src/flk/json/__init__.py": (
        "import json as _json\n"
        "\n"
        "\n"
        "def dumps(obj):\n"
        "    return _json.dumps(obj)\n"
        "\n"
        "\n"
        "def loads(s):\n"
        "    return _json.loads(s)\n"
    ),
    # flask's src/flask/json/provider.py: `import json` INSIDE the package is the standard library
    "src/flk/json/provider.py": (
        "import json\n"
        "\n"
        "\n"
        "class DefaultJSONProvider:\n"
        "    def dumps(self, obj):\n"
        "        return json.dumps(obj)\n"
        "\n"
        "    def loads(self, s):\n"
        "        return json.loads(s)\n"
        "\n"
        "\n"
        "def pretty(obj):\n"
        "    return json.dumps(obj, indent=2)\n"
    ),
    # flask's tag.py: the package's own dumps through a relative import (the engine links these itself)
    "src/flk/json/tag.py": (
        "from ..json import dumps\n"
        "from ..json import loads\n"
        "\n"
        "\n"
        "def tag_dump(x):\n"
        "    return dumps(x)\n"
        "\n"
        "\n"
        "def tag_load(s):\n"
        "    return loads(s)\n"
    ),
    # flask's config.py: the standard library again
    "src/flk/config.py": (
        "import json\n"
        "\n"
        "\n"
        "def from_env(v):\n"
        "    return json.loads(v)\n"
    ),
    # the package's json reached as a SUBMODULE: relatively, by `from` and by a dotted import (src/ is a root)
    "src/flk/app.py": (
        "from . import json as fjson\n"
        "from flk.json import loads as jl\n"
        "import flk.json\n"
        "\n"
        "\n"
        "def render(x):\n"
        "    return fjson.dumps(x)\n"
        "\n"
        "\n"
        "def parse(s):\n"
        "    return jl(s)\n"
        "\n"
        "\n"
        "def full(x):\n"
        "    return flk.json.dumps(x)\n"
    ),
    # a MODULE-LEVEL call of a name imported from the package itself (`from . import version`): the engine links no
    # :Module caller, so only CALLS_PLUS adds this one
    "src/flk/boot.py": (
        "from . import version\n"
        "\n"
        "version()\n"
    ),
    # inside a package a bare `import config` is NOT the sibling (Python 3 has no implicit relative import)
    "src/flk/cli.py": (
        "import config\n"
        "\n"
        "\n"
        "def run(v):\n"
        "    return config.from_env(v)\n"
    ),
    # a relative import climbing above the repository root names nothing (the suffix match found nowhere.py)
    "src/flk/deep.py": (
        "from .... import nowhere\n"
        "\n"
        "\n"
        "def climb():\n"
        "    return nowhere.thing()\n"
    ),
    "nowhere.py": (
        "def thing():\n"
        "    return 0\n"
    ),
    # a script (no __init__.py): its own directory IS a root. Mixed case (Scripts/Helper.py, `import Helper`): a
    # case-folded path key misses it on Windows, where the file still exists on disk under the folded name
    "Scripts/run.py": (
        "import Helper\n"
        "\n"
        "\n"
        "def main():\n"
        "    return Helper.go()\n"
    ),
    "Scripts/Helper.py": (
        "def go():\n"
        "    return 1\n"
    ),
    # two roots ("" and src/) hold a regular `util`: ambiguous, never a guess (the suffix match took the local one)
    "util.py": (
        "def go():\n"
        "    return 2\n"
    ),
    "src/util.py": (
        "def go():\n"
        "    return 3\n"
    ),
    "main.py": (
        "import util\n"
        "\n"
        "\n"
        "def start():\n"
        "    return util.go()\n"
    ),
    # a sys.path-style import: no root holds `engine`, so lib/engine.py is never reached by its suffix
    "tools/use_lib.py": (
        "import sys\n"
        "\n"
        "sys.path.insert(0, \"lib\")\n"
        "import engine  # noqa: E402\n"
        "\n"
        "\n"
        "def drive():\n"
        "    return engine.run()\n"
    ),
    "lib/engine.py": (
        "def run():\n"
        "    return 4\n"
    ),
}
S1B_FIXTURE = "calls_plus_roots"
# what CALLS_PLUS adds (a marked key each; pushed INFERRED: the target is in another file)
S1B_ADDED = {
    ("flk_app_render", "json_init_dumps"): "`from . import json as fjson; fjson.dumps()` (relative, a submodule)",
    ("flk_app_parse", "json_init_loads"): "`from flk.json import loads as jl; jl()` (src/ is a root)",
    ("flk_app_full", "json_init_dumps"): "`import flk.json; flk.json.dumps()` (a dotted module path)",
    ("scripts_run_main", "scripts_helper_go"): "a script's own directory is a root (Scripts/Helper.py, mixed case)",
    ("flk_boot", "flk_init_version"): "a :Module's `from . import version; version()` (the package itself)",
}
# what the dotted-suffix match added and import roots must not (each one a false edge)
S1B_MUST_NOT = {
    ("json_provider_defaultjsonprovider_dumps", "json_init_dumps"): "flask provider.py: `import json` is the stdlib",
    ("json_provider_defaultjsonprovider_loads", "json_init_loads"): "flask provider.py: `import json` is the stdlib",
    ("json_provider_pretty", "json_init_dumps"): "`import json` inside the package is the stdlib (a :Function caller)",
    ("flk_config_from_env", "json_init_loads"): "flask config.py: `import json` is the stdlib",
    ("flk_cli_run", "flk_config_from_env"): "inside a package `import config` is not the sibling module",
    ("main_start", "util_go"): "util.py and src/util.py both match: no guess",
    ("main_start", "src_util_go"): "util.py and src/util.py both match: no guess",
    ("tools_use_lib_drive", "lib_engine_run"): "no root holds `engine` (sys.path-only): never by suffix",
    ("flk_deep_climb", "nowhere_thing"): "a relative import above the repository root names nothing",
}
_S1B_CALLERS = {s for s, _t in S1B_ADDED} | {s for s, _t in S1B_MUST_NOT}


def _s1b_sha() -> str:
    import hashlib
    return hashlib.sha256(json.dumps(S1B_TREE, sort_keys=True).encode("utf-8")).hexdigest()


def _s1b_fixture(bucket: str) -> Path:
    return FX / bucket / "precise" / f"{S1B_FIXTURE}.json"


def _temp_id_prefixes() -> list:
    """The graphify id slugs of the temp directory (`C:\\Users\\<account>\\AppData\\Local\\Temp` ->
    `c_users_<account>_appdata_local_temp_`), longest first: an import that resolves outside the captured tree is
    an id built from that path, which would put the capturing account's name into a committed fixture."""
    import re
    out = set()
    for p in (tempfile.gettempdir(), str(Path(tempfile.gettempdir()).resolve())):
        slug = re.sub(r"[^a-z0-9]+", "_", p.lower()).strip("_")
        if slug and slug != "tmp":
            out.add(slug + "_")
    return sorted(out, key=len, reverse=True)


def _relative(graph: dict, root: Path) -> dict:
    """`graph` with every string field holding an absolute path under `root` made root-relative, and every id
    built from the temp directory's path starting `tmp_` instead (no machine path or account name in a capture;
    map_graph reads no path, and such an id names no node)."""
    prefixes = [str(root) + os.sep, str(root).replace("\\", "/") + "/"]
    temp_ids = _temp_id_prefixes()

    def fix(v):
        if isinstance(v, str):
            for p in prefixes:
                if v.lower().startswith(p.lower()):
                    return v[len(p):].replace("\\", "/")
            for p in temp_ids:
                if v.startswith(p):
                    return "tmp_" + v[len(p):]
        return v

    return {k: ([{kk: fix(vv) for kk, vv in item.items()} if isinstance(item, dict) else item for item in v]
                if isinstance(v, list) else v) for k, v in graph.items()}


def _s1b_effect(graph: dict, root: Path, mode: str):
    """(added, stats, edges, calls) for `mode` ('plus' | 'both'): the tuples map_graph adds over the same mode without
    CALLS_PLUS ('off' | 'precise') as (caller, callee, marked) triples, map_graph's stats, the pushed CALLS
    (sync's passes + _resolve_calls) and the tuples."""
    base = {"plus": "off", "both": "precise"}[mode]
    with _env(**_mode_env(base)), contextlib.redirect_stderr(io.StringIO()):
        _n0, calls0, _i0 = GX.map_graph(json.loads(json.dumps(graph)), root=root)
    stats: dict = {}
    with _env(**_mode_env(mode)), contextlib.redirect_stderr(io.StringIO()):
        nodes, calls, _i = GX.map_graph(json.loads(json.dumps(graph)), root=root, stats=stats)
        edges, _c2 = _passes(root, nodes, calls)
    before = {(a, str(b), GX.is_call_key(b)) for a, b in calls0}
    added = [(a, str(b), GX.is_call_key(b)) for a, b in calls if (a, str(b), GX.is_call_key(b)) not in before]
    return added, stats, dict(edges), calls


def _s1b_checks(tag: str, graph: dict, root: Path) -> None:
    want = sorted((s, t, True) for s, t in S1B_ADDED)
    for mode in ("plus", "both"):
        added, stats, edges, calls = _s1b_effect(graph, root, mode)
        check(f"{tag} {mode}: CALLS_PLUS adds exactly the {len(want)} import-root calls, each a MARKED key",
              sorted(added) == want, sorted(set(added) ^ set(want)))
        check(f"{tag} {mode}: map_graph's stats count them (calls_plus == {len(want)})", stats.get("calls_plus") == len(want),
              stats.get("calls_plus"))
        for (s, t), why in S1B_ADDED.items():
            check(f"{tag} {mode}: pushed {s} -> {t} INFERRED — {why}", edges.get((s, t)) == "INFERRED",
                  edges.get((s, t)))
        for (s, t), why in S1B_MUST_NOT.items():
            check(f"{tag} {mode}: no {s} -> {t} — {why}",
                  (s, t) not in edges and not any(a == s and str(b) == t for a, b in calls), edges.get((s, t)))


def gate_s_import_roots() -> None:
    print("\n-- S: S1b — CALLS_PLUS resolves module names by import root (captured graphs of both engines) --")
    root = _write_tree(_TMP / "s1b", S1B_TREE)
    for ver, b in BUCKETS.items():
        p = _s1b_fixture(b)
        if not p.is_file():
            check(f"{b}: the S1b capture exists ({p.relative_to(REPO)})", False)
            continue
        fx = json.loads(p.read_text(encoding="utf-8"))
        check(f"{b}: the capture is graphify {ver}'s, of THIS tree (re-capture after editing S1B_TREE)",
              fx.get("engine") == ver and fx.get("tree_sha256") == _s1b_sha(), (fx.get("engine"), fx.get("tree_sha256")))
        _s1b_checks(b, fx["graph"], root)

    graph = json.loads(_s1b_fixture("0839").read_text(encoding="utf-8"))["graph"]
    # the resolver is read only on the CALLS_PLUS path (a recorder, not a raising seam: map_graph swallows nothing
    # here, but a recorder also proves it WAS read when on)
    seen: list = []
    orig = GX._py_import_roots

    def rec(*a, **k):
        seen.append(a)
        return orig(*a, **k)

    GX._py_import_roots = rec
    try:
        for mode in ("off", "precise"):
            with _env(**_mode_env(mode)), contextlib.redirect_stderr(io.StringIO()):
                GX.map_graph(json.loads(json.dumps(graph)), root=root)
        n_off = len(seen)
        with _env(**_mode_env("plus")), contextlib.redirect_stderr(io.StringIO()):
            GX.map_graph(json.loads(json.dumps(graph)), root=root)
        n_plus = len(seen) - n_off
    finally:
        GX._py_import_roots = orig
    check("CALLS_PLUS off (flags off / precise only): the import-root resolver is never built", n_off == 0, n_off)
    check("CALLS_PLUS on: the import-root resolver is built once per map_graph", n_plus == 1, n_plus)

    # no resolver -> no module-qualified addition (never a fall-back to the suffix guess)
    missing = object()
    saved = sys.modules.get("py_import_resolver", missing)
    sys.modules["py_import_resolver"] = None             # `import py_import_resolver` now raises ImportError
    try:
        added, stats, edges, _calls = _s1b_effect(graph, root, "plus")
    finally:
        if saved is missing:
            sys.modules.pop("py_import_resolver", None)
        else:
            sys.modules["py_import_resolver"] = saved
    check("py_import_resolver unavailable: CALLS_PLUS adds no module-qualified call (no suffix fall-back)",
          added == [] and stats.get("calls_plus") == 0, (added, stats.get("calls_plus")))
    check("py_import_resolver unavailable: still none of the must-not edges",
          not [k for k in S1B_MUST_NOT if k in edges], [k for k in S1B_MUST_NOT if k in edges])

    # the module-level and same-file additions of section B's tree are unchanged by S1b (B / C / Z cover them in
    # full); here: the C2 keys into the same file still resolve with the resolver unavailable
    with _env(**_mode_env("plus")), contextlib.redirect_stderr(io.StringIO()):
        sys.modules["py_import_resolver"] = None
        try:
            _n, calls_nr, _i = GX.map_graph(_graph(), root=_write_tree(_TMP / "s1b_c2", TREE))
        finally:
            if saved is missing:
                sys.modules.pop("py_import_resolver", None)
            else:
                sys.modules["py_import_resolver"] = saved
    check("py_import_resolver unavailable: a :Module's same-file call (C2: app_twin -> app_twin_main) still resolves",
          any(a == "app_twin" and b == "app_twin_main" and GX.is_call_key(b) for a, b in calls_nr),
          [(a, b) for a, b in calls_nr if a == "app_twin"])


def gate_s_real_engine() -> None:
    print("\n-- S (real engine): the installed graphify on the S1b tree --")
    try:
        import graphify  # noqa: F401
    except Exception:
        print("  [SKIP] graphify is not installed in this interpreter — the real-engine section is not run")
        return
    ver = GX.graphify_version()
    graph, root = _s1b_real_graph()
    if graph is None:
        check(f"real engine ({ver}) produced a graph of the S1b tree", False)
        return
    _s1b_checks(f"real {ver}", graph, root)
    b = BUCKETS.get(ver)
    if b is None or not _s1b_fixture(b).is_file():
        print(f"  (no S1b capture for graphify {ver}; the comparison with a capture is skipped)")
        return
    fx = json.loads(_s1b_fixture(b).read_text(encoding="utf-8"))["graph"]
    bad = []
    for mode in ("off", "plus"):
        with _env(**_mode_env(mode)), contextlib.redirect_stderr(io.StringIO()):
            a = GX.map_graph(json.loads(json.dumps(graph)), root=root)
            f = GX.map_graph(json.loads(json.dumps(fx)), root=root)
        if json.dumps(a, sort_keys=True, default=str) != json.dumps(f, sort_keys=True, default=str):
            bad.append(mode)
    check(f"real {ver}: the same nodes / calls / imports as the {b} capture (flags off and CALLS_PLUS)", not bad, bad)


def _s1b_real_graph():
    """(graph.json the installed engine writes for S1B_TREE, the tree's root) — or (None, root)."""
    root = _write_tree(_TMP / "s1b_real", S1B_TREE)
    with _env(**_mode_env("off", DEEPMAP_HOME=str(_TMP / "home_s1b"))), contextlib.redirect_stderr(io.StringIO()), \
            contextlib.redirect_stdout(io.StringIO()):
        res, _rep = GX.extract_with_report(root)
        gp = GX.graphify_out_dir(root) / "graph.json"
        if res is None or not gp.is_file():
            return None, root
        return json.loads(gp.read_text(encoding="utf-8")), root


def do_capture() -> int:
    ver = GX.graphify_version()
    b = BUCKETS.get(ver)
    if b is None:
        print(f"--capture: graphify {ver} is not one of {sorted(BUCKETS)}")
        return 1
    graph, root = _s1b_real_graph()
    if graph is None:
        print("--capture: the engine failed on the S1b tree")
        return 1
    p = _s1b_fixture(b)
    p.parent.mkdir(parents=True, exist_ok=True)
    doc = {"_about": (f"REAL `graphify update --no-cluster` output (graphify {ver}) of "
                      "tests/calls_plus_scope_r11c_gates.S1B_TREE, written by extract_with_report (absolute paths "
                      "made root-relative; map_graph reads none of them); re-capture with "
                      "`python tests/calls_plus_scope_r11c_gates.py --capture` under this engine."),
           "engine": ver, "tree_sha256": _s1b_sha(), "graph": _relative(graph, root)}
    p.write_bytes((json.dumps(doc, indent=1, sort_keys=True) + "\n").encode("utf-8"))
    print(f"--capture: wrote {p.relative_to(REPO)} ({len(graph.get('nodes', []))} nodes, "
          f"{len(graph.get('links', []))} links)")
    return 0


# ---- Z ---------------------------------------------------------------------------------------------------------
def gate_z_unchanged(root: Path) -> None:
    print("\n-- Z: what C2 / C3 must not touch --")
    for mode in MODES:
        nodes, calls, c2, edges = _run(mode, root)
        lab = _labels(nodes)
        rows = [[a, b, c] for (a, b), c in edges.items() if lab.get(a) != "Module"]
        if mode in ("off", "precise"):
            check(f"{mode}: every non-Module caller's pushed CALLS, in order, equal the pre-fix build's",
                  rows == PRE_NON_MODULE[mode], rows)
        else:
            want = {(a, b): C3_DEF_CHANGE.get((a, b), c) for a, b, c in PRE_NON_MODULE[mode]}
            got = {(a, b): c for a, b, c in rows}
            check(f"{mode}: every non-Module caller's pushed CALLS equal the pre-fix build's but for the one C3 def "
                  "edge (use() -> helpers.assist INFERRED -> AMBIGUOUS)", got == want,
                  sorted(set(got.items()) ^ set(want.items())))
        if mode in ("off", "precise"):
            check(f"{mode}: no :Module caller at all (the engine emits none; CALLS_PLUS off) — the push is the "
                  "pre-fix build's", not [a for (a, _b) in edges if lab.get(a) == "Module"]
                  and [[a, b, c] for (a, b), c in edges.items()] == PRE_NON_MODULE[mode])
    with _env(**_mode_env("off")), contextlib.redirect_stderr(io.StringIO()):
        nodes, calls, _i = GX.map_graph(_graph(), root=root)
        same, n = S._unkey_receiver_calls(root, nodes, calls)
    check("flags off: _unkey_receiver_calls hands back the SAME list, 0 changes (nothing read)",
          same is calls and n == 0, n)


def main(argv=()) -> int:
    if "--capture" in argv:
        try:
            return do_capture()
        finally:
            shutil.rmtree(_TMP, ignore_errors=True)
    print("calls_plus_scope_r11c_gates — a :Module's call sites: node-less bodies (C2), import rebinding (C3); "
          "S1b: module names by import root")
    try:
        root = _write_tree(_TMP / "fixture", TREE)
        for gate, args in ((gate_a_rule, ()), (gate_b_push, (root,)), (gate_c_map, (root,)),
                           (gate_d_real_engine, ()), (gate_s_import_roots, ()), (gate_s_real_engine, ()),
                           (gate_z_unchanged, (root,))):
            try:
                gate(*args)
            except Exception as e:  # noqa: BLE001
                import traceback
                traceback.print_exc()
                check(f"{gate.__name__} ran without raising", False, f"{type(e).__name__}: {e}")
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
    total = _PASS + _FAIL
    if _FAIL:
        print(f"\nRESULT: RED — {_FAIL} of {total} checks failed.")
        return 1
    print(f"\nRESULT: GREEN — {total}/{total} checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
