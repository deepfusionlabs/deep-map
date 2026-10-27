#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Round-11 engine gate (WE): R1 — a CALLS callee is a node KEY only when its producer MARKED it; R2 — a :Module
caller's call sites are its MODULE-LEVEL statements only.

R1. Key-ness used to be INFERRED downstream from `callee in <node keys>`. Under DEEPMAP_GRAPHIFY_CALLS_PLUS alone
(whose engine calls stay NAMES, R10/P5 made the push key-aware) and through R10/P1's fall-back names under
DEEPMAP_PRECISE_CALLS, a NAME that equals a node's KEY was bound to that node as a firm EXTRACTED / INFERRED edge
and the real targets were dropped:
  * a root `cli.py` (Module key `cli`) defining `cli()`: `entry(): return cli()` -> the :Module, not the def;
  * `build/insights.py` (Module key `build_insights`) defining `build_insights()` (here: pkg/insights.py +
    `pkg_insights()`) -> the :Module;
  * `from main import main; main()` -> the :Module main.py;
  * P1's `other.cli()` fall-back name `cli` -> the :Module (the method fan-out was lost).
map_graph now wraps every callee it resolved to one node in graphify_extract.CallKey (a str: equal, hashable
and printable exactly like the key) and every consumer takes the keyed branch only for a marked callee.
R2. sync._unkey_receiver_calls (push time) and graphify_extract._receiver_unkeyer (map time) judged a :Module
caller over the WHOLE file, so a receiver call inside one of its defs (`engine_cli.main()` in `def main`)
turned the module's own bare `main()` into 'both' and fanned it out to every `main` in the repo
(build/graphify_case_shim.py: 5 correct edges -> 214). Now only the module's own nodes count (def / class
bodies excluded; decorators, defaults, bases and module-level lambdas included), with its own imports, and
an import-bound name is never taken for a shadowing value (`try: import x as y` / `except: y = None`).

Sections (hermetic unless noted; temp dirs + a temp DEEPMAP_HOME, no Neo4j / network):
  A  the CallKey marker contract (str-identical; recognised across module copies; sync agrees)
  B  map_graph marks exactly the resolved keys, per flag mode (a frozen graphify 0.8.39 graph of the fixture)
  C  R1 end to end through sync's push passes + _resolve_calls, per flag mode (exact edges)
  D  every consumer takes the keyed branch only for a marked callee (_resolve_calls, _attach_*,
     _unkey_receiver_calls, _keep_rescued_only, _names_before_keys, map_graph's de-duplication)
  E  R2 push time: a :Module's sites are its module-level ones (exact per fixture file + the scope helpers)
  F  R2 map time: the same rule in graphify_extract._receiver_unkeyer
  G  the REAL engine (skipped when graphify is absent): the fixture in all four modes, and
     build/graphify_case_shim.py's :Module keeps only its own edges beside decoy `main`s
  Z  flags off: byte-identical to the pre-round-11 output (frozen)
"""
from __future__ import annotations

import ast
import contextlib
import importlib.util
import io
import json
import os
import pickle
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build"
sys.path.insert(0, str(BUILD))

import graphify_extract as GX  # noqa: E402

_PASS = _FAIL = 0
_TMP = Path(tempfile.mkdtemp(prefix="dm_r11k_"))
_FLAGS = ("DEEPMAP_PRECISE_CALLS", "DEEPMAP_GRAPHIFY_CALLS_PLUS", "DEEPMAP_GRAPHIFY_CALLS_PLUS_TESTS",
          "DEEPMAP_LOCAL_DEF_CALLS", "DEEPMAP_FALSE_CALL_FILTER", "DEEPMAP_DROP_APP_TO_TEST_CALLS",
          "DEEPMAP_SELF_CALL_REBIND", "DEEPMAP_EXTRACTOR", "DEEPMAP_EXCLUDE_TESTS", "DEEPMAP_INCLUDE_VENDOR",
          "DEEPMAP_RESOLVED_IMPORTS", "DEEPMAP_SUPPLEMENTAL_TS_RUST", "DEEPMAP_MODULE_DOCSTRINGS",
          "DEEPMAP_SUMMARY_NORMALIZE", "DEEPMAP_CASE_INSENSITIVE_EXT", "DEEPMAP_RESCUE_SKIPPED_DIRS")
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


def _mode_env(mode: str) -> dict:
    env = {f: None for f in _FLAGS}
    env.update(MODES[mode])
    return env


def _load(name: str, alias: str):
    spec = importlib.util.spec_from_file_location(alias, BUILD / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


S = _load("sync", "dm_r11k_sync")


# ---- the fixture tree (its graphify 0.8.39 graph is frozen below) ------------------------------------------
TREE = {
    "cli.py": '"""Root CLI module."""\n\n\ndef cli():\n    return 1\n\n\ndef entry():\n    return cli()\n\n\n'
              'class Shell:\n    def go(self, other):\n        return other.cli()\n',
    "other.py": "class Runner:\n    def cli(self):\n        return 3\n",
    "main.py": "def main():\n    return 0\n",
    "pkg/__init__.py": "",
    "pkg/runner.py": "from main import main\n\n\ndef run():\n    return main()\n",
    "pkg/insights.py": "def pkg_insights():\n    return {}\n\n\ndef insights_for_root():\n    return pkg_insights()\n",
    # R2: the module's own bare main(); the receiver call is main()'s, not the module's
    "scripts/tool.py": "import engine_cli\n\n\ndef main():\n    return engine_cli.main()\n\n\nraise SystemExit(main())\n",
    # R2 control: a module-level receiver call IS the module's
    "scripts/both.py": "import engine_cli\n\n\ndef main():\n    return 0\n\n\nmain()\nengine_cli.main()\n",
    # a module-level lambda is no node: its call is the module's (as CALLS_PLUS attributes it)
    "scripts/lam.py": "import engine_cli\n\n\ndef main():\n    return 0\n\n\nhandler = lambda: engine_cli.main()\nmain()\n",
    # M5's pattern: `_h` is the import (or None) — one module variable, never a shadowing value
    "scripts/hook.py": "try:\n    import helpers as _h\nexcept ImportError:\n    _h = None\n\n\ndef run():\n    return 1\n\n\n"
                       "run()\n_h.helper()\n",
    "helpers.py": "def helper():\n    return 1\n",
    "decoy.py": "def helper():\n    return 2\n",
}


def _write_tree(root: Path, tree: dict) -> Path:
    for rel, text in tree.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode("utf-8"))
    return root


# graphify 0.8.39's graph.json for TREE (captured from the real engine; nodes' _origin dropped)
GRAPH = {"nodes": [
    {"file_type": "code", "id": "cli", "label": "cli.py", "source_file": "cli.py", "source_location": "L1"},
    {"file_type": "code", "id": "cli_cli", "label": "cli()", "source_file": "cli.py", "source_location": "L4"},
    {"file_type": "code", "id": "cli_entry", "label": "entry()", "source_file": "cli.py", "source_location": "L8"},
    {"file_type": "code", "id": "cli_shell", "label": "Shell", "source_file": "cli.py", "source_location": "L12"},
    {"file_type": "code", "id": "cli_shell_go", "label": ".go()", "source_file": "cli.py", "source_location": "L13"},
    {"file_type": "code", "id": "decoy", "label": "decoy.py", "source_file": "decoy.py", "source_location": "L1"},
    {"file_type": "code", "id": "decoy_helper", "label": "helper()", "source_file": "decoy.py", "source_location": "L1"},
    {"file_type": "code", "id": "helpers", "label": "helpers.py", "source_file": "helpers.py", "source_location": "L1"},
    {"file_type": "code", "id": "helpers_helper", "label": "helper()", "source_file": "helpers.py", "source_location": "L1"},
    {"file_type": "code", "id": "main", "label": "main.py", "source_file": "main.py", "source_location": "L1"},
    {"file_type": "code", "id": "main_main", "label": "main()", "source_file": "main.py", "source_location": "L1"},
    {"file_type": "code", "id": "other", "label": "other.py", "source_file": "other.py", "source_location": "L1"},
    {"file_type": "code", "id": "other_runner", "label": "Runner", "source_file": "other.py", "source_location": "L1"},
    {"file_type": "code", "id": "other_runner_cli", "label": ".cli()", "source_file": "other.py", "source_location": "L2"},
    {"file_type": "code", "id": "pkg_init", "label": "__init__.py", "source_file": "pkg/__init__.py", "source_location": "L1"},
    {"file_type": "code", "id": "pkg_insights", "label": "insights.py", "source_file": "pkg/insights.py", "source_location": "L1"},
    {"file_type": "code", "id": "pkg_insights_pkg_insights", "label": "pkg_insights()", "source_file": "pkg/insights.py", "source_location": "L1"},
    {"file_type": "code", "id": "pkg_insights_insights_for_root", "label": "insights_for_root()", "source_file": "pkg/insights.py", "source_location": "L5"},
    {"file_type": "code", "id": "pkg_runner", "label": "runner.py", "source_file": "pkg/runner.py", "source_location": "L1"},
    {"file_type": "code", "id": "pkg_runner_run", "label": "run()", "source_file": "pkg/runner.py", "source_location": "L4"},
    {"file_type": "code", "id": "scripts_both", "label": "both.py", "source_file": "scripts/both.py", "source_location": "L1"},
    {"file_type": "code", "id": "scripts_both_main", "label": "main()", "source_file": "scripts/both.py", "source_location": "L4"},
    {"file_type": "code", "id": "scripts_hook", "label": "hook.py", "source_file": "scripts/hook.py", "source_location": "L1"},
    {"file_type": "code", "id": "scripts_hook_run", "label": "run()", "source_file": "scripts/hook.py", "source_location": "L7"},
    {"file_type": "code", "id": "scripts_lam", "label": "lam.py", "source_file": "scripts/lam.py", "source_location": "L1"},
    {"file_type": "code", "id": "scripts_lam_main", "label": "main()", "source_file": "scripts/lam.py", "source_location": "L4"},
    {"file_type": "code", "id": "scripts_tool", "label": "tool.py", "source_file": "scripts/tool.py", "source_location": "L1"},
    {"file_type": "code", "id": "scripts_tool_main", "label": "main()", "source_file": "scripts/tool.py", "source_location": "L4"},
], "links": [
    {"confidence": "EXTRACTED", "relation": "contains", "source": "cli", "source_file": "cli.py", "source_location": "L4", "target": "cli_cli", "weight": 1.0},
    {"confidence": "EXTRACTED", "relation": "contains", "source": "cli", "source_file": "cli.py", "source_location": "L8", "target": "cli_entry", "weight": 1.0},
    {"confidence": "EXTRACTED", "relation": "contains", "source": "cli", "source_file": "cli.py", "source_location": "L12", "target": "cli_shell", "weight": 1.0},
    {"confidence": "EXTRACTED", "relation": "method", "source": "cli_shell", "source_file": "cli.py", "source_location": "L13", "target": "cli_shell_go", "weight": 1.0},
    {"confidence": "EXTRACTED", "context": "call", "relation": "calls", "source": "cli_entry", "source_file": "cli.py", "source_location": "L9", "target": "cli_cli", "weight": 1.0},
    {"confidence": "EXTRACTED", "context": "call", "relation": "calls", "source": "cli_shell_go", "source_file": "cli.py", "source_location": "L14", "target": "cli_cli", "weight": 1.0},
    {"confidence": "EXTRACTED", "relation": "contains", "source": "decoy", "source_file": "decoy.py", "source_location": "L1", "target": "decoy_helper", "weight": 1.0},
    {"confidence": "EXTRACTED", "relation": "contains", "source": "helpers", "source_file": "helpers.py", "source_location": "L1", "target": "helpers_helper", "weight": 1.0},
    {"confidence": "EXTRACTED", "relation": "contains", "source": "main", "source_file": "main.py", "source_location": "L1", "target": "main_main", "weight": 1.0},
    {"confidence": "EXTRACTED", "relation": "contains", "source": "other", "source_file": "other.py", "source_location": "L1", "target": "other_runner", "weight": 1.0},
    {"confidence": "EXTRACTED", "relation": "method", "source": "other_runner", "source_file": "other.py", "source_location": "L2", "target": "other_runner_cli", "weight": 1.0},
    {"confidence": "EXTRACTED", "relation": "contains", "source": "pkg_insights", "source_file": "pkg/insights.py", "source_location": "L1", "target": "pkg_insights_pkg_insights", "weight": 1.0},
    {"confidence": "EXTRACTED", "relation": "contains", "source": "pkg_insights", "source_file": "pkg/insights.py", "source_location": "L5", "target": "pkg_insights_insights_for_root", "weight": 1.0},
    {"confidence": "EXTRACTED", "context": "call", "relation": "calls", "source": "pkg_insights_insights_for_root", "source_file": "pkg/insights.py", "source_location": "L6", "target": "pkg_insights_pkg_insights", "weight": 1.0},
    {"confidence": "EXTRACTED", "context": "import", "relation": "imports_from", "source": "pkg_runner", "source_file": "pkg/runner.py", "source_location": "L1", "target": "main", "weight": 1.0},
    {"confidence": "EXTRACTED", "relation": "contains", "source": "pkg_runner", "source_file": "pkg/runner.py", "source_location": "L4", "target": "pkg_runner_run", "weight": 1.0},
    {"confidence": "EXTRACTED", "context": "import", "relation": "imports", "source": "scripts_both", "source_file": "scripts/both.py", "source_location": "L1", "target": "engine_cli", "weight": 1.0},
    {"confidence": "EXTRACTED", "relation": "contains", "source": "scripts_both", "source_file": "scripts/both.py", "source_location": "L4", "target": "scripts_both_main", "weight": 1.0},
    {"confidence": "EXTRACTED", "context": "import", "relation": "imports", "source": "scripts_hook", "source_file": "scripts/hook.py", "source_location": "L2", "target": "helpers", "weight": 1.0},
    {"confidence": "EXTRACTED", "relation": "contains", "source": "scripts_hook", "source_file": "scripts/hook.py", "source_location": "L7", "target": "scripts_hook_run", "weight": 1.0},
    {"confidence": "EXTRACTED", "context": "import", "relation": "imports", "source": "scripts_lam", "source_file": "scripts/lam.py", "source_location": "L1", "target": "engine_cli", "weight": 1.0},
    {"confidence": "EXTRACTED", "relation": "contains", "source": "scripts_lam", "source_file": "scripts/lam.py", "source_location": "L4", "target": "scripts_lam_main", "weight": 1.0},
    {"confidence": "EXTRACTED", "context": "import", "relation": "imports", "source": "scripts_tool", "source_file": "scripts/tool.py", "source_location": "L1", "target": "engine_cli", "weight": 1.0},
    {"confidence": "EXTRACTED", "relation": "contains", "source": "scripts_tool", "source_file": "scripts/tool.py", "source_location": "L4", "target": "scripts_tool_main", "weight": 1.0},
    {"confidence": "EXTRACTED", "context": "import", "relation": "imports", "source": "pkg_runner", "source_file": "pkg/runner.py", "source_location": "L1", "target": "main_main", "weight": 1.0},
    {"confidence": "EXTRACTED", "context": "call", "relation": "calls", "source": "pkg_runner_run", "source_file": "pkg/runner.py", "source_location": "L5", "target": "main_main", "weight": 1.0},
]}


def _passes(root: Path, nodes: list, calls: list):
    """sync's push-time passes in _sync_pass order, then _resolve_calls: (edges, calls after P1, n un-keyed)."""
    with contextlib.redirect_stderr(io.StringIO()):
        c2, n = S._unkey_receiver_calls(root, nodes, list(calls))
        S._attach_bare_calls(root, nodes, c2)
        S._attach_local_defs(root, nodes, c2)
        S._attach_local_def_calls(root, nodes, c2)
        return S._resolve_calls(nodes, c2), c2, n


def _mapped(mode: str, root: Path, graph=None):
    with _env(**_mode_env(mode)), contextlib.redirect_stderr(io.StringIO()):
        return GX.map_graph(json.loads(json.dumps(graph or GRAPH)), root=root)


def _run(mode: str, root: Path, graph=None):
    """(nodes, calls from map_graph, edges, calls after P1, n un-keyed) under `mode`."""
    nodes, calls, _imports = _mapped(mode, root, graph)
    with _env(**_mode_env(mode)):
        edges, c2, n = _passes(root, nodes, calls)
    return nodes, calls, edges, c2, n


def _of(edges: dict, caller: str) -> dict:
    return {b: c for (a, b), c in edges.items() if a == caller}


def _marked(calls) -> list:
    return [c for c in calls if GX.is_call_key(c[1])]


# ---- A ---------------------------------------------------------------------------------------------------
def gate_a_marker() -> None:
    print("\n-- A: the CallKey marker contract --")
    ck, is_ck = getattr(GX, "call_key", None), getattr(GX, "is_call_key", None)
    check("graphify_extract.call_key / is_call_key / CallKey exist",
          callable(ck) and callable(is_ck) and isinstance(getattr(GX, "CallKey", None), type))
    if not (callable(ck) and callable(is_ck)):
        return
    k = ck("pkg_util_helper")
    check("a CallKey IS a str: ==, hash, dict lookup, repr, f-string and JSON exactly like the plain key",
          isinstance(k, str) and k == "pkg_util_helper" and hash(k) == hash("pkg_util_helper")
          and {"pkg_util_helper": 1}[k] == 1 and repr(k) == repr("pkg_util_helper") and f"{k}" == "pkg_util_helper"
          and json.dumps([("a", k)]) == json.dumps([("a", "pkg_util_helper")]), repr(k))
    check("...and a tuple holding it compares equal to the plain tuple (every list comparison is unchanged)",
          ("a", k) == ("a", "pkg_util_helper") and [("a", k)] == [("a", "pkg_util_helper")])
    check("is_call_key: True for a marked key, False for a plain str / None / an int / a derived str",
          is_ck(k) and not is_ck("pkg_util_helper") and not is_ck(None) and not is_ck(3)
          and not is_ck(k.rsplit("_", 1)[-1]) and not is_ck(str(k)) and not is_ck(k.lower()))
    check("call_key is idempotent (a marked key comes back as the same object)", ck(k) is k)
    rt = pickle.loads(pickle.dumps(("a", k)))
    check("a marked tuple survives pickle with its mark", rt == ("a", k) and is_ck(rt[1]), rt)
    other = _load("graphify_extract", "dm_r11k_gx_copy")
    k2 = other.call_key("x")
    check("a key marked by ANOTHER loaded copy of graphify_extract is recognised (marker attribute, not class)",
          type(k2) is not type(k) and is_ck(k2) and other.is_call_key(k), (type(k2), type(k)))
    sk = getattr(S, "_is_call_key", None)
    check("sync._is_call_key agrees with graphify_extract.is_call_key",
          callable(sk) and sk(k) and sk(k2) and not sk("x") and not sk(None), sk)
    sc = getattr(GX, "_seen_call", None)
    check("map_graph's de-duplication key tells a marked key from the same plain name (and nothing else changes)",
          callable(sc) and sc(("a", k)) != sc(("a", "pkg_util_helper")) and sc(("a", "b")) == sc(("a", "b")))
    nbk = getattr(GX, "_names_before_keys", None)
    if callable(nbk):
        calls = [("s", "x0"), ("s", ck("n")), ("s", "n")]
        out = nbk(calls, {("s", "n")})
        check("_names_before_keys moves the plain name only; a marked key equal to it keeps its place",
              [(a, b, is_ck(b)) for a, b in out] == [("s", "n", False), ("s", "x0", False), ("s", "n", True)], out)


# ---- B ---------------------------------------------------------------------------------------------------
def gate_b_map_marks(root: Path) -> None:
    print("\n-- B: map_graph marks exactly the resolved keys, per flag mode --")
    nodes, off, _i = _mapped("off", root)
    keys = {n["key"] for n in nodes}
    check("flags off: no callee is marked (every callee is a name, as always)", not _marked(off), _marked(off))
    _n, prec, _i = _mapped("precise", root)
    check("precise: every engine call resolved to a kept node is a MARKED key",
          sorted(c for c in prec if c[1] in keys and GX.is_call_key(c[1]))
          == [("cli_entry", "cli_cli"), ("pkg_insights_insights_for_root", "pkg_insights_pkg_insights"),
              ("pkg_runner_run", "main_main")], prec)
    check("precise: P1's fall-back for the receiver call `other.cli()` is a PLAIN name (it always fans out)",
          ("cli_shell_go", "cli") in prec and not any(GX.is_call_key(b) for a, b in prec if a == "cli_shell_go"), prec)
    _n, plus, _i = _mapped("plus", root)
    added = [c for c in plus if c not in off]
    check("CALLS_PLUS alone: the engine's calls stay PLAIN names (exactly the flags-off tuples, none marked)",
          plus[:len(off)] == off and not _marked(plus[:len(off)]), plus)
    check("CALLS_PLUS alone: every addition is a MARKED key (and only the additions are)",
          added == _marked(plus) and sorted(added) == sorted(PLUS_ADDITIONS), (added, _marked(plus)))
    _n, both, _i = _mapped("both", root)
    check("precise + CALLS_PLUS: the engine keys and the additions are marked, the P1 name is not",
          sorted(_marked(both)) == sorted(_marked(prec) + PLUS_ADDITIONS) and ("cli_shell_go", "cli") in both,
          both)


PLUS_ADDITIONS = [("scripts_both", "scripts_both_main"), ("scripts_hook", "scripts_hook_run"),
                  ("scripts_hook", "helpers_helper"), ("scripts_lam", "scripts_lam_main"),
                  ("scripts_tool", "scripts_tool_main")]


# ---- C ---------------------------------------------------------------------------------------------------
R1_CALLERS = ("cli_entry", "cli_shell_go", "pkg_insights_insights_for_root", "pkg_runner_run")
SCRIPT_EDGES = {  # the CALLS_PLUS additions (plus / both): exact, after R1 + R2
    ("scripts_both", "main"): "AMBIGUOUS", ("scripts_both", "main_main"): "AMBIGUOUS",
    ("scripts_both", "scripts_both_main"): "EXTRACTED", ("scripts_both", "scripts_lam_main"): "AMBIGUOUS",
    ("scripts_both", "scripts_tool_main"): "AMBIGUOUS",
    ("scripts_lam", "main"): "AMBIGUOUS", ("scripts_lam", "main_main"): "AMBIGUOUS",
    ("scripts_lam", "scripts_both_main"): "AMBIGUOUS", ("scripts_lam", "scripts_lam_main"): "EXTRACTED",
    ("scripts_lam", "scripts_tool_main"): "AMBIGUOUS",
    ("scripts_hook", "scripts_hook_run"): "EXTRACTED", ("scripts_hook", "helpers_helper"): "INFERRED",
    ("scripts_tool", "scripts_tool_main"): "EXTRACTED",
}
PRECISE_EDGES = {
    ("cli_entry", "cli_cli"): "EXTRACTED",
    ("cli_shell_go", "cli"): "AMBIGUOUS", ("cli_shell_go", "cli_cli"): "AMBIGUOUS",
    ("cli_shell_go", "other_runner_cli"): "AMBIGUOUS",
    ("pkg_insights_insights_for_root", "pkg_insights_pkg_insights"): "EXTRACTED",
    ("pkg_runner_run", "main_main"): "INFERRED",
}


def _firm_into_module(nodes, edges) -> list:
    mods = {n["key"] for n in nodes if n["label"] == "Module"}
    return sorted((a, b, c) for (a, b), c in edges.items() if b in mods and c != "AMBIGUOUS")


def _sym(a: dict, b: dict) -> list:
    return sorted(set(a.items()) ^ set(b.items()))


def gate_c_r1_end_to_end(root: Path) -> None:
    print("\n-- C: R1 end to end — a NAME that equals a node key fans out by name; a marked key binds --")
    nodes, _c, off, _c2, _n = _run("off", root)
    check("precondition: flags off, the R1 names fan out (`cli` reaches the def AND the :Module by name)",
          _of(off, "cli_entry") == {"cli": "AMBIGUOUS", "cli_cli": "AMBIGUOUS"}, _of(off, "cli_entry"))
    _n, _c, plus, _c2, _n2 = _run("plus", root)
    for caller in R1_CALLERS:
        check(f"CALLS_PLUS alone: {caller}'s engine call resolves exactly as with the flags off",
              _of(plus, caller) == _of(off, caller), (_of(plus, caller), _of(off, caller)))
    check("CALLS_PLUS alone: `pkg_insights()` binds the FUNCTION (INFERRED), not the :Module whose key it equals",
          _of(plus, "pkg_insights_insights_for_root") == {"pkg_insights_pkg_insights": "INFERRED"},
          _of(plus, "pkg_insights_insights_for_root"))
    check("CALLS_PLUS alone: no EXTRACTED / INFERRED edge lands on a :Module (was: cli, main, pkg_insights)",
          not _firm_into_module(nodes, plus), _firm_into_module(nodes, plus))
    check("CALLS_PLUS alone: the exact edge set (flags-off edges + the additions, each bound exactly)",
          plus == {**off, **SCRIPT_EDGES}, _sym(plus, {**off, **SCRIPT_EDGES}))
    _n, _c, prec, _c2, _n2 = _run("precise", root)
    check("precise: P1's fall-back `cli` (from `other.cli()`) fans out like the flags-off name, Runner.cli included",
          _of(prec, "cli_shell_go") == _of(off, "cli_shell_go"), (_of(prec, "cli_shell_go"), _of(off, "cli_shell_go")))
    check("precise: no EXTRACTED / INFERRED edge lands on a :Module", not _firm_into_module(nodes, prec),
          _firm_into_module(nodes, prec))
    check("precise: the exact edge set (resolved keys bind exactly; the P1 name fans out)",
          prec == PRECISE_EDGES, _sym(prec, PRECISE_EDGES))
    _n, _c, both, _c2, _n2 = _run("both", root)
    check("precise + CALLS_PLUS: the exact edge set (precise's + the additions')",
          both == {**PRECISE_EDGES, **SCRIPT_EDGES}, _sym(both, {**PRECISE_EDGES, **SCRIPT_EDGES}))


# ---- D ---------------------------------------------------------------------------------------------------
def _nd(key, name, sf, label="Function", line=1):
    return {"key": key, "label": label, "name": name, "source_file": sf, "start_line": line, "body_summary": name}


def gate_d_consumers() -> None:
    print("\n-- D: every consumer takes the keyed branch only for a MARKED callee --")
    ck = GX.call_key
    # D1 _resolve_calls
    nodes = [_nd("cli", "cli", "cli.py", "Module"), _nd("cli_cli", "cli", "cli.py", line=4),
             _nd("x_run", "run", "x.py"), _nd("b_helper", "helper", "b.py")]
    with _env(**_mode_env("off")):
        off = S._resolve_calls([dict(n) for n in nodes], [("x_run", "cli")])
    for mode in ("precise", "plus"):
        with _env(**_mode_env(mode)):
            name_path = S._resolve_calls([dict(n) for n in nodes], [("x_run", "cli")])
            keyed = S._resolve_calls([dict(n) for n in nodes], [("x_run", ck("cli_cli"))])
            key_only = S._resolve_calls([dict(n) for n in nodes], [("x_run", "b_helper")])
            key_marked = S._resolve_calls([dict(n) for n in nodes], [("x_run", ck("b_helper"))])
        check(f"_resolve_calls ({mode}): a plain `cli` (also the :Module's KEY) fans out by name, exactly as flags off",
              name_path == off == {("x_run", "cli"): "AMBIGUOUS", ("x_run", "cli_cli"): "AMBIGUOUS"}, (name_path, off))
        check(f"_resolve_calls ({mode}): a MARKED key binds exactly that node (cross-file: INFERRED)",
              keyed == {("x_run", "cli_cli"): "INFERRED"}, keyed)
        check(f"_resolve_calls ({mode}): a plain string that is only some node's key names nothing -> no edge",
              key_only == {}, key_only)
        check(f"_resolve_calls ({mode}): ...the same string MARKED binds it",
              key_marked == {("x_run", "b_helper"): "INFERRED"}, key_marked)
    # D2 _attach_local_defs: flags off keeps the historical test byte for byte; keyed modes need the mark
    with tempfile.TemporaryDirectory() as td:
        root = _write_tree(Path(td), {"a.py": "def helper():\n    return 1\n\n\ndef go():\n    return b()\n",
                                      "pkg/b.py": "X = 1\n"})
        base = [_nd("a", "a", "a.py", "Module"), _nd("a_helper", "helper", "a.py"), _nd("a_go", "go", "a.py", line=5),
                _nd("pkg_b", "b", "pkg/b.py", "Module")]
        got = {}
        for label, mode, callee in (("off/plain", "off", "pkg_b"), ("precise/plain", "precise", "pkg_b"),
                                    ("precise/marked", "precise", ck("pkg_b")), ("plus/plain", "plus", "pkg_b")):
            ns = [dict(n) for n in base]
            with _env(**_mode_env(mode)):
                S._attach_local_defs(root, ns, [("a_go", callee)])
            got[label] = next(n for n in ns if n["key"] == "a_go").get("local_defs")
    check("_attach_local_defs, flags off: a plain name equal to another file's key still attaches (unchanged)",
          got["off/plain"] == ["go", "helper"], got)
    check("_attach_local_defs, keyed modes: a plain name is judged as a name (`pkg_b` names nothing -> nothing)",
          got["precise/plain"] is None and got["plus/plain"] is None, got)
    check("_attach_local_defs, keyed modes: a MARKED cross-file key attaches", got["precise/marked"] == ["go", "helper"],
          got)
    # D3 _attach_bare_calls: a plain name is judged even when it equals a key; a marked key is left alone
    with tempfile.TemporaryDirectory() as td:
        root = _write_tree(Path(td), {"a.py": "def go():\n    return m()\n", "m.py": "X = 1\n",
                                      "b.py": "class B:\n    def m(self):\n        return 1\n"})
        base = [_nd("a", "a", "a.py", "Module"), _nd("a_go", "go", "a.py"), _nd("m", "m", "m.py", "Module"),
                _nd("b", "b", "b.py", "Module"), _nd("b_b", "B", "b.py", "Class"),
                _nd("b_b_m", "B.m", "b.py", "Method", 2)]
        seen = {}
        for label, callee in (("plain", "m"), ("marked", ck("m"))):
            ns = [dict(n) for n in base]
            with _env(**_mode_env("precise")):
                S._attach_bare_calls(root, ns, [("a_go", callee)])
                edges = S._resolve_calls(ns, [("a_go", callee)])
            seen[label] = (next(n for n in ns if n["key"] == "a_go").get("call_bare"), edges)
    check("_attach_bare_calls (precise): a plain `m` that equals a :Module key is judged as a name (bare-only)",
          seen["plain"][0] == ["m"] and seen["plain"][1] == {("a_go", "m"): "INFERRED"}, seen["plain"])
    check("_attach_bare_calls (precise): a MARKED key is left alone (it binds exactly)",
          seen["marked"][0] is None and seen["marked"][1] == {("a_go", "m"): "INFERRED"}, seen["marked"])
    # D4 _attach_local_def_calls (DEEPMAP_LOCAL_DEF_CALLS): a plain name equal to a :Module key binds the local def
    with tempfile.TemporaryDirectory() as td:
        root = _write_tree(Path(td), {"a.py": "def check():\n    return 1\n\n\ndef gate():\n    return check()\n",
                                      "check.py": "X = 1\n"})
        base = [_nd("a", "a", "a.py", "Module"), _nd("a_check", "check", "a.py"), _nd("a_gate", "gate", "a.py", line=5),
                _nd("check", "check", "check.py", "Module")]
        ns = [dict(n) for n in base]
        with _env(**dict(_mode_env("precise"), DEEPMAP_LOCAL_DEF_CALLS="1")):
            edges, _c2, _n = _passes(root, ns, [("a_gate", "check")])
    check("DEEPMAP_LOCAL_DEF_CALLS + precise: a plain `check` (also check.py's :Module KEY) binds the caller's own "
          "def — never the :Module", edges == {("a_gate", "a_check"): "INFERRED"}, edges)
    # D5 _unkey_receiver_calls judges only marked keys, and never drops a plain tuple
    with tempfile.TemporaryDirectory() as td:
        root = _write_tree(Path(td), {"a.py": "def go(obj):\n    return obj.helper()\n",
                                      "b.py": "def helper():\n    return 1\n"})
        base = [_nd("a", "a", "a.py", "Module"), _nd("a_go", "go", "a.py"), _nd("b", "b", "b.py", "Module"),
                _nd("b_helper", "helper", "b.py")]
        with _env(**_mode_env("precise")):
            plain = [("a_go", "b_helper")]
            got_p, n_p = S._unkey_receiver_calls(root, [dict(n) for n in base], plain)
            got_k, n_k = S._unkey_receiver_calls(root, [dict(n) for n in base], [("a_go", ck("b_helper"))])
            mixed = [("a_go", ck("b_helper")), ("a_go", "b_helper")]
            got_m, _n_m = S._unkey_receiver_calls(root, [dict(n) for n in base], mixed)
    check("_unkey_receiver_calls: a PLAIN tuple is never judged (the same list back, 0 changes)",
          got_p is plain and n_p == 0, (got_p, n_p))
    check("_unkey_receiver_calls: a MARKED receiver-only key becomes the plain name (P1, unchanged)",
          [(a, b, GX.is_call_key(b)) for a, b in got_k] == [("a_go", "helper", False)] and n_k == 1, got_k)
    check("_unkey_receiver_calls: a plain tuple equal to the un-keyed pair stays (only the marked one goes)",
          [(a, b, GX.is_call_key(b)) for a, b in got_m] == [("a_go", "helper", False), ("a_go", "b_helper", False)],
          got_m)
    # D6 _keep_rescued_only: only a MARKED key into a dropped node goes with it
    if GX._engine_noise_dir() is not None:
        res = ([{"key": "a_run", "source_file": "a.py"}, {"key": "build_x_f", "source_file": "build/x.py"}],
               [("a_run", ck("build_x_f")), ("a_run", "build_x_f"), ("a_run", "f")], [])
        for mode in ("precise", "plus"):
            with _env(**_mode_env(mode)):
                (_kn, kc, _ki), nd = GX._keep_rescued_only(res, _TMP, {"build": ["build/y.py"]})
            check(f"_keep_rescued_only ({mode}): the marked key into the dropped node goes; a plain name equal to "
                  f"it stays", nd == 1 and [(a, b, GX.is_call_key(b)) for a, b in kc]
                  == [("a_run", "build_x_f", False), ("a_run", "f", False)], kc)
    else:
        print("  [SKIP] graphify's skip rule is unavailable — _keep_rescued_only is not exercised")

# ---- E ---------------------------------------------------------------------------------------------------
_SCOPE_SRC = (
    "import os\n"
    "try:\n"
    "    import helpers as _h\n"
    "except ImportError:\n"
    "    _h = None\n"
    "\n"
    "\n"
    "@deco_fn()\n"
    "def f(x=dflt_fn(), *, y: ann_fn() = kw_fn()) -> ret_fn():\n"
    "    import engine_cli as local_alias\n"
    "    in_body_fn()\n"
    "    return local_alias.main()\n"
    "\n"
    "\n"
    "@cls_deco()\n"
    "class C(base_fn(), metaclass=meta_fn()):\n"
    "    cls_body_fn()\n"
    "\n"
    "    def m(self):\n"
    "        return method_body_fn()\n"
    "\n"
    "\n"
    "handler = lambda: lam_fn()\n"
    "if os.environ:\n"
    "    block_fn()\n"
    "for item in iter_fn():\n"
    "    loop_fn(item)\n"
)
_MODULE_LEVEL_CALLS = {"deco_fn", "dflt_fn", "ann_fn", "kw_fn", "ret_fn", "cls_deco", "base_fn", "meta_fn", "lam_fn",
                       "block_fn", "iter_fn", "loop_fn"}
_BODY_CALLS = {"in_body_fn", "main", "cls_body_fn", "method_body_fn"}


def gate_e_r2_push(root: Path) -> None:
    print("\n-- E: R2 push time — a :Module's call sites are its MODULE-LEVEL ones --")
    tree = ast.parse(_SCOPE_SRC)
    mln = getattr(S, "_py_module_level_nodes", None)
    facts = getattr(S, "_py_module_scope_facts", None)
    check("sync._py_module_level_nodes / _py_module_scope_facts exist", callable(mln) and callable(facts))
    if callable(mln) and callable(facts):
        nodes = mln(tree)
        called = {x.func.id for x in nodes if isinstance(x, ast.Call) and isinstance(x.func, ast.Name)}
        called |= {x.func.attr for x in nodes if isinstance(x, ast.Call) and isinstance(x.func, ast.Attribute)}
        check("module-level: statements, nested blocks, loops, decorators, defaults, annotations, bases, keywords "
              "and lambda bodies", _MODULE_LEVEL_CALLS <= called, sorted(_MODULE_LEVEL_CALLS - called))
        check("...but never a def's or a class's BODY (those calls are that node's)", not (called & _BODY_CALLS),
              sorted(called & _BODY_CALLS))
        gnodes = GX._py_module_level_nodes(tree)
        check("graphify_extract._py_module_level_nodes walks exactly the same nodes (map time == push time)",
              {id(x) for x in gnodes} == {id(x) for x in nodes} and len(gnodes) == len(nodes),
              (len(gnodes), len(nodes)))
        _n, aliases, local = facts(tree)
        check("the module's own imports only: a def-local `import engine_cli as local_alias` is not the module's",
              "local_alias" not in aliases and aliases.get("_h") == "helpers" and aliases.get("os") == "os", aliases)
        check("an import-bound name is never a shadowing value (`_h = None` in the except branch is the import)",
              "_h" not in local and "handler" in local and "item" in local, sorted(local))
        check("graphify_extract's module import table agrees",
              GX._py_import_targets(tree, gnodes) == aliases, (GX._py_import_targets(tree, gnodes), aliases))
    nodes, calls, _i = _mapped("plus", root)
    with _env(**_mode_env("plus")), contextlib.redirect_stderr(io.StringIO()):
        c2, n = S._unkey_receiver_calls(root, nodes, list(calls))
    fb = [c for c in c2 if c not in calls]
    check("scripts/tool.py: the module's bare `main()` keeps its key — `engine_cli.main()` is def main()'s, not "
          "the module's (no fan-out to every `main`)", ("scripts_tool", "main") not in c2
          and ("scripts_tool", "scripts_tool_main") in c2, fb)
    check("scripts/both.py: a MODULE-LEVEL receiver call still counts (the key AND the name fan-out)",
          ("scripts_both", "main") in c2 and ("scripts_both", "scripts_both_main") in c2, fb)
    check("scripts/lam.py: a module-level lambda's receiver call is the module's (as CALLS_PLUS attributes it)",
          ("scripts_lam", "main") in c2, fb)
    check("scripts/hook.py: M5's `_h.helper()` keeps its key (`_h = None` in the except is the same variable)",
          ("scripts_hook", "helpers_helper") in c2 and ("scripts_hook", "helper") not in c2, fb)
    check("exactly two fall-backs are added (both, lam)", n == 2 and sorted(fb) == [("scripts_both", "main"),
                                                                                 ("scripts_lam", "main")], (n, fb))


# ---- F ---------------------------------------------------------------------------------------------------
def gate_f_r2_map(root: Path) -> None:
    print("\n-- F: R2 map time — graphify_extract._receiver_unkeyer scopes a :Module caller the same way --")
    g = json.loads(json.dumps(GRAPH))
    for src, tgt, line in (("scripts_tool", "scripts_tool_main", "L8"), ("scripts_both", "scripts_both_main", "L8")):
        g["links"].append({"confidence": "EXTRACTED", "context": "call", "relation": "calls", "source": src,
                           "source_file": f"scripts/{src.split('_', 1)[1]}.py", "source_location": line,
                           "target": tgt, "weight": 1.0})
    stats: dict = {}
    with _env(**_mode_env("precise")), contextlib.redirect_stderr(io.StringIO()):
        _n, calls, _i = GX.map_graph(g, root=root, stats=stats)
    check("an engine link from the scripts/tool.py :Module keeps its MARKED key and adds no `main` name",
          ("scripts_tool", "scripts_tool_main") in calls and ("scripts_tool", "main") not in calls
          and any(GX.is_call_key(b) for a, b in calls if a == "scripts_tool"), [c for c in calls if c[0] == "scripts_tool"])
    both = [c for c in calls if c[0] == "scripts_both"]
    check("scripts/both.py: its module-level receiver call still adds the name, BEFORE the marked key",
          [(b, GX.is_call_key(b)) for a, b in both] == [("main", False), ("scripts_both_main", True)], both)
    check("stats: exactly one caller called the name both ways", stats.get("precise_receiver_names") == 1, stats)


# ---- G ---------------------------------------------------------------------------------------------------
def gate_g_real_engine() -> None:
    print("\n-- G: the REAL engine --")
    try:
        import graphify  # noqa: F401
    except Exception:
        print("  [SKIP] graphify is not installed in this interpreter — the real-engine section is not run")
        return
    home = _TMP / "home_g"
    root = _write_tree(_TMP / "real", TREE)
    got = {}
    for mode in MODES:
        with _env(**dict(_mode_env(mode), DEEPMAP_HOME=str(home))), contextlib.redirect_stderr(io.StringIO()), \
                contextlib.redirect_stdout(io.StringIO()):
            res, rep = GX.extract_with_report(root)
            if not res:
                check(f"{mode}: the real engine produced a graph", False, rep.get("cause"))
                return
            nodes, calls, _i = res
            edges, _c2, _n = _passes(root, nodes, calls)
        got[mode] = (nodes, edges)
    nodes, off = got["off"]
    for mode in ("plus", "precise", "both"):
        e = got[mode][1]
        check(f"real engine, {mode}: no EXTRACTED / INFERRED edge lands on a :Module", not _firm_into_module(nodes, e),
              _firm_into_module(nodes, e))
    for caller in R1_CALLERS:
        check(f"real engine, CALLS_PLUS alone: {caller} resolves exactly as with the flags off",
              _of(got["plus"][1], caller) == _of(off, caller), (_of(got["plus"][1], caller), _of(off, caller)))
    check("real engine, precise: `other.cli()` fans out by name (Runner.cli included)",
              _of(got["precise"][1], "cli_shell_go") == _of(off, "cli_shell_go"), _of(got["precise"][1], "cli_shell_go"))
    for mode in ("plus", "both"):
        e = got[mode][1]
        check(f"real engine, {mode}: scripts/tool.py's :Module keeps exactly its own `main` (R2)",
              _of(e, "scripts_tool") == {"scripts_tool_main": "EXTRACTED"}, _of(e, "scripts_tool"))
        check(f"real engine, {mode}: scripts/hook.py's `_h.helper()` binds helpers.helper only",
              _of(e, "scripts_hook") == {"scripts_hook_run": "EXTRACTED", "helpers_helper": "INFERRED"},
              _of(e, "scripts_hook"))
    # the audit's repo case: build/graphify_case_shim.py (module-level `raise SystemExit(main())`, and a receiver
    # `engine_cli.main()` inside def main) beside decoy `main`s
    shim_src = (BUILD / "graphify_case_shim.py").read_text(encoding="utf-8")
    sroot = _write_tree(_TMP / "shim", {"shim/graphify_case_shim.py": shim_src,
                                        "tools/a.py": "def main():\n    return 1\n",
                                        "tools/b.py": "def main():\n    return 2\n"})
    for mode in ("plus", "both"):
        with _env(**dict(_mode_env(mode), DEEPMAP_HOME=str(home))), contextlib.redirect_stderr(io.StringIO()), \
                contextlib.redirect_stdout(io.StringIO()):
            res, rep = GX.extract_with_report(sroot)
            if not res:
                check(f"shim tree, {mode}: the real engine produced a graph", False, rep.get("cause"))
                return
            nodes, calls, _i = res
            edges, _c2, _n = _passes(sroot, nodes, calls)
        mod = next((n["key"] for n in nodes if n["label"] == "Module" and n["source_file"].endswith("graphify_case_shim.py")), None)
        file_of = {n["key"]: n["source_file"] for n in nodes}
        own = _of(edges, mod)
        adds = [b for a, b in calls if a == mod and GX.is_call_key(b)]
        check(f"shim, {mode}: the :Module keeps exactly its own CALLS_PLUS edges — each into the shim, EXTRACTED, "
              f"none to a decoy `main`", bool(adds) and own == {b: "EXTRACTED" for b in adds}
              and all(file_of.get(b, "").endswith("graphify_case_shim.py") for b in own), (sorted(own.items()), adds))


# ---- Z ---------------------------------------------------------------------------------------------------
OFF_CALLS = [("cli_entry", "cli"), ("cli_shell_go", "cli"), ("pkg_insights_insights_for_root", "pkg_insights"),
             ("pkg_runner_run", "main")]
OFF_EDGES = [  # captured from the pre-round-11 build/ (flags off), in the push's order
    ("cli_entry", "cli", "AMBIGUOUS"), ("cli_entry", "cli_cli", "AMBIGUOUS"), ("cli_shell_go", "cli", "AMBIGUOUS"),
    ("cli_shell_go", "cli_cli", "AMBIGUOUS"), ("cli_shell_go", "other_runner_cli", "AMBIGUOUS"),
    ("pkg_insights_insights_for_root", "pkg_insights_pkg_insights", "INFERRED"),
    ("pkg_runner_run", "main", "AMBIGUOUS"), ("pkg_runner_run", "main_main", "AMBIGUOUS"),
    ("pkg_runner_run", "scripts_both_main", "AMBIGUOUS"), ("pkg_runner_run", "scripts_lam_main", "AMBIGUOUS"),
    ("pkg_runner_run", "scripts_tool_main", "AMBIGUOUS")]


def gate_z_flags_off(root: Path) -> None:
    print("\n-- Z: flags off — byte-identical to the pre-round-11 output --")
    nodes, calls, edges, c2, n = _run("off", root)
    check("map_graph: the same CALLS tuples, in the same order, none marked",
          calls == OFF_CALLS and not _marked(calls) and all(type(b) is str for _a, b in calls), calls)
    check("sync's passes change nothing (P1 is off: the same list, 0 changes)", c2 == calls and n == 0, (c2, n))
    check("the resolved edges, in order, equal the pre-round-11 build's",
          [(a, b, c) for (a, b), c in edges.items()] == OFF_EDGES, list(edges.items()))
    with _env(**_mode_env("off")):
        check("sync._unkey_receiver_calls returns the SAME list object with the flags off",
              S._unkey_receiver_calls(root, nodes, calls)[0] is calls)


def main() -> int:
    print("calls_keyed_r11_gates — R1 explicit CALLS keys, R2 module-level scope for :Module callers")
    try:
        root = _write_tree(_TMP / "fixture", TREE)
        for gate, args in ((gate_a_marker, ()), (gate_b_map_marks, (root,)), (gate_c_r1_end_to_end, (root,)),
                           (gate_d_consumers, ()), (gate_e_r2_push, (root,)), (gate_f_r2_map, (root,)),
                           (gate_g_real_engine, ()), (gate_z_flags_off, (root,))):
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
    sys.exit(main())
