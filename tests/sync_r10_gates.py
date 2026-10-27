#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Round-10 W6 gates: build/sync.py + build/indexes.py (Phase M A/B findings P1-P4).

P1 (MED) — DEEPMAP_PRECISE_CALLS keyed a RECEIVER call `x.m()` to a same-named MODULE-LEVEL function as a
firm EXTRACTED edge and dropped the real polymorphic targets (click shell_completion.py:287
`obj.shell_complete(...)` -> the module function shell_complete; _compat.py:76 `self._stream.isatty()` ->
_compat.isatty). A receiver never reaches a module-level def (except through the module itself or a `::`
scope path), so such a key falls back to the flag-off (caller, name) tuple:
  A  Python: receiver-only -> exactly the flag-off edges for that name; a bare call keeps its EXTRACTED key;
     bare + receiver -> the key AND the fan-out; a module / package receiver (`shell.f(`, `pkg.f(`) keeps the
     key; `os.isatty(` / `x.helper(` into another file fall back; a :Method key and undecidable callers
     (def not found, unreadable file, no visible site) are untouched.
  B  other languages (text): a same-file `obj.f(` falls back, `f(` and Rust `self::f(` keep the key, another
     file's function (`pkg.F(`) keeps it; _text_call_forms is unchanged by the kinds refactor.
  C  flag OFF: the SAME calls list, nothing read; sync() end to end (fake driver) pushes the fan-out under
     the flag; the REAL engine (graphify, when installed) on the click-shaped tree.
P2 (LOW) — the FTS index's BM25 statistics depended on its update history (deleted/updated Lucene docs stay
in docCount/docFreq until a merge), so a summary-changing sync ranked differently from a fresh build:
  D  _fts_changed_docs counts EXISTING nodes whose indexed text the push rewrites (server side; short_name
     only while it is indexed); indexes.rebuild_fts_index = DROP + CREATE on fts_properties().
  E  sync() (fake driver): changed text / a pruning prune / a pending stamp -> the code FTS index is
     re-created AFTER the push and the prune, the pending stamp set before the push and cleared after; a
     no-op re-sync writes nothing to the index; a project switch re-creates it right after the wipe, before
     the push.
P3 (LOW) — DEEPMAP_FTS_SHORT_NAME off left a stale n.short_name on every node:
  F  flag off: one count read, and (only when some exist) a batched `REMOVE n.short_name` IN TRANSACTIONS;
     none -> no write; flag on -> nothing read; the OFF upsert statement is byte-identical; sync() removes
     them and re-creates the FTS index.
P4 (LOW, opt-in) — the vector indexes use Neo4j's default int8 quantization (approximate vector order):
  G  DEEPMAP_VECTOR_QUANTIZATION=0 creates them with `vector.quantization.enabled`: false and re-creates an
     existing index whose reported setting differs (both ways); unset / any other value -> the default DDL
     byte for byte; an index that does not report it is left alone; a server that rejects the option gets
     the default DDL; vector_quantization_report (for doctor) names the mode; sync prints a re-create.
  H  the frozen ranking: build/retrieval.py's 3-arm ranking functions and build/learned_fusion.py are
     byte-identical to `git show HEAD:` — a guard for UNCOMMITTED edits only: once a change is committed (and
     always in CI's fresh checkout) the file IS HEAD, so the check is tautological and passes whatever the file
     says (CLAUDE.md lesson 5). The durable byte locks are tests/o12_learned_fusion_lock_r283_gates.py
     (learned_fusion.py's pinned sha256) and tests/fts_r7_gates.py _FROZEN (the ranking symbols, hashed at the
     v0.27.4 base 9c8d50bce).

Hermetic: temp dirs, fake drivers that log every statement, temp DEEPMAP_HOME; no sockets, no Neo4j. The
real-engine check in C skips when graphify is not installed. The Neo4j behaviour the fakes model (the
`options` map, a same-value SET writes no index update, the rebuild cost) was verified live on Neo4j 5.26
(round-10 W6 notes).
"""
from __future__ import annotations

import ast
import contextlib
import importlib.util
import io
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build"
sys.path.insert(0, str(BUILD))

_PASS = _FAIL = 0
_FLAGS = ("DEEPMAP_PRECISE_CALLS", "DEEPMAP_DROP_APP_TO_TEST_CALLS", "DEEPMAP_FALSE_CALL_FILTER",
          "DEEPMAP_LOCAL_DEF_CALLS", "DEEPMAP_EXCLUDE_TESTS", "DEEPMAP_INCLUDE_VENDOR", "DEEPMAP_EXTRACTOR",
          "DEEPMAP_INCREMENTAL_SYNC", "DEEPMAP_FTS_SHORT_NAME", "DEEPMAP_VECTOR_QUANTIZATION",
          "DEEPMAP_LEARNING", "DEEPMAP_AUTO_TUNE", "DEEPMAP_GRAPHIFY_CALLS_PLUS", "DEEPMAP_SELF_CALL_REBIND")


def check(label: str, cond: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {label}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}{(' — ' + str(detail)[:700]) if detail else ''}")


def _load(name="sync"):
    spec = importlib.util.spec_from_file_location(f"dm_r10_{name}", BUILD / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@contextlib.contextmanager
def _env(**kv):
    base = {k: None for k in _FLAGS}
    base.update(kv)
    saved = {k: os.environ.get(k) for k in base}
    try:
        for k, v in base.items():
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


def _write_tree(root: Path, tree: dict) -> None:
    for rel, text in tree.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode("utf-8"))


def _n(key, name, sf, line=1, label="Function"):
    return {"key": key, "label": label, "name": name, "source_file": sf, "start_line": line,
            "body_summary": name, "documented": False}


def _passes(S, root, nodes, calls):
    """The push-time source passes in _sync_pass order (P1 first when the module has it)."""
    unkey = getattr(S, "_unkey_receiver_calls", None)
    with contextlib.redirect_stderr(io.StringIO()):
        if callable(unkey):
            calls, _n_unkey = unkey(root, nodes, calls)
        S._attach_bare_calls(root, nodes, calls)
        S._attach_local_defs(root, nodes, calls)
        S._attach_local_def_calls(root, nodes, calls)
    return calls


def _edges(S, tree, nodes, calls, **env):
    nodes = [dict(n) for n in nodes]
    with tempfile.TemporaryDirectory() as td, _env(**env):
        root = Path(td)
        _write_tree(root, tree)
        calls = _passes(S, root, nodes, list(calls))
        return S._resolve_calls(nodes, calls)


def _of(edges, caller):
    return {b: c for (a, b), c in edges.items() if a == caller}


# =====================================================================================================
# A — P1, Python: a receiver call is never keyed to a module-level function
# =====================================================================================================
_PY = {
    "pkg/__init__.py": "",
    "pkg/shell.py": (
        "from pkg.core import Command\n"                              # 1
        "\n\n"                                                          # 2-3
        "def shell_complete(cli, ctx_args):\n"                          # 4
        "    return cli\n"                                              # 5
        "\n\n"                                                          # 6-7
        "class ShellComplete:\n"                                        # 8
        "    def get_completions(self, obj, args):\n"                   # 9
        "        return obj.shell_complete(args, 1)\n"                  # 10
        "\n"                                                            # 11
        "    def run(self):\n"                                          # 12
        "        return shell_complete(self, [])\n"                     # 13
        "\n"                                                            # 14
        "    def both(self, obj):\n"                                    # 15
        "        shell_complete(self, [])\n"                            # 16
        "        return obj.shell_complete(1, 2)\n"                     # 17
        "\n\n"                                                          # 18-19
        "class Wrapper:\n"                                              # 20
        "    def __init__(self, stream):\n"                             # 21
        "        self._stream = stream\n"                               # 22
        "\n"                                                            # 23
        "    def isatty(self):\n"                                       # 24
        "        return self._stream.isatty()\n"                        # 25
        "\n\n"                                                          # 26-27
        "def isatty(stream):\n"                                         # 28
        "    return True\n"),                                           # 29
    "pkg/core.py": (
        "class Command:\n"                                              # 1
        "    def shell_complete(self, ctx, incomplete):\n"              # 2
        "        return []\n"                                           # 3
        "\n\n"                                                          # 4-5
        "class Group(Command):\n"                                       # 6
        "    def shell_complete(self, ctx, incomplete):\n"              # 7
        "        return []\n"                                           # 8
        "\n\n"                                                          # 9-10
        "class Parameter:\n"                                            # 11
        "    def shell_complete(self, ctx, incomplete):\n"              # 12
        "        return []\n"),                                         # 13
    "pkg/term.py": "class ConsoleStream:\n    def isatty(self):\n        return True\n",
    "pkg/util.py": "def helper(x):\n    return x\n",
    "pkg/use.py": (
        "from pkg import shell\n"                                       # 1
        "import pkg\n"                                                  # 2
        "import os\n"                                                   # 3
        "\n\n"                                                          # 4-5
        "def via_module(x):\n"                                          # 6
        "    return shell.shell_complete(x, [])\n"                      # 7
        "\n\n"                                                          # 8-9
        "def via_package(x):\n"                                         # 10
        "    return pkg.helper(x)\n"                                    # 11
        "\n\n"                                                          # 12-13
        "def via_os(x):\n"                                              # 14
        "    return os.isatty(x)\n"                                     # 15
        "\n\n"                                                          # 16-17
        "def via_obj(x):\n"                                             # 18
        "    return x.helper()\n"                                       # 19
        "\n\n"                                                          # 20-21
        "def no_site(x):\n"                                             # 22
        "    return x\n"),                                              # 23
}
_PY_NODES = [
    _n("pkg_shell", "shell", "pkg/shell.py", 1, "Module"),
    _n("pkg_shell_shell_complete", "shell_complete", "pkg/shell.py", 4),
    _n("pkg_shell_shellcomplete", "ShellComplete", "pkg/shell.py", 8, "Class"),
    _n("pkg_shell_get_completions", "ShellComplete.get_completions", "pkg/shell.py", 9, "Method"),
    _n("pkg_shell_run", "ShellComplete.run", "pkg/shell.py", 12, "Method"),
    _n("pkg_shell_both", "ShellComplete.both", "pkg/shell.py", 15, "Method"),
    _n("pkg_shell_wrapper", "Wrapper", "pkg/shell.py", 20, "Class"),
    _n("pkg_shell_wrapper_init", "Wrapper.__init__", "pkg/shell.py", 21, "Method"),
    _n("pkg_shell_wrapper_isatty", "Wrapper.isatty", "pkg/shell.py", 24, "Method"),
    _n("pkg_shell_isatty", "isatty", "pkg/shell.py", 28),
    _n("pkg_core_command", "Command", "pkg/core.py", 1, "Class"),
    _n("pkg_core_command_sc", "Command.shell_complete", "pkg/core.py", 2, "Method"),
    _n("pkg_core_group", "Group", "pkg/core.py", 6, "Class"),
    _n("pkg_core_group_sc", "Group.shell_complete", "pkg/core.py", 7, "Method"),
    _n("pkg_core_parameter", "Parameter", "pkg/core.py", 11, "Class"),
    _n("pkg_core_parameter_sc", "Parameter.shell_complete", "pkg/core.py", 12, "Method"),
    _n("pkg_term_consolestream_isatty", "ConsoleStream.isatty", "pkg/term.py", 2, "Method"),
    _n("pkg_util_helper", "helper", "pkg/util.py", 1),
    _n("pkg_use_via_module", "via_module", "pkg/use.py", 6),
    _n("pkg_use_via_package", "via_package", "pkg/use.py", 10),
    _n("pkg_use_via_os", "via_os", "pkg/use.py", 14),
    _n("pkg_use_via_obj", "via_obj", "pkg/use.py", 18),
    _n("pkg_use_no_site", "no_site", "pkg/use.py", 22),
    _n("pkg_use_lost", "lost", "pkg/use.py", 99),                       # its def is not at that line
]
# what graphify hands the push under DEEPMAP_PRECISE_CALLS (keys) and with the flag off (target names)
_PY_KEYED = [
    ("pkg_shell_get_completions", "pkg_shell_shell_complete"),   # obj.shell_complete(  -> the module fn (bug)
    ("pkg_shell_run", "pkg_shell_shell_complete"),               # shell_complete(      -> right
    ("pkg_shell_both", "pkg_shell_shell_complete"),              # both forms
    ("pkg_shell_wrapper_isatty", "pkg_shell_isatty"),            # self._stream.isatty( -> the module fn (bug)
    ("pkg_use_via_module", "pkg_shell_shell_complete"),          # shell.shell_complete( (module receiver)
    ("pkg_use_via_package", "pkg_util_helper"),                  # pkg.helper( (package re-export)
    ("pkg_use_via_os", "pkg_shell_isatty"),                      # os.isatty( -> not that module
    ("pkg_use_via_obj", "pkg_util_helper"),                      # x.helper( -> a receiver
    ("pkg_use_no_site", "pkg_util_helper"),                      # no visible site: undecidable
    ("pkg_use_lost", "pkg_util_helper"),                         # def not found: undecidable
    ("pkg_shell_get_completions", "pkg_core_command_sc"),        # a :Method key: never touched
]
# R11/R1: graphify-RESOLVED callees reach the push as MARKED keys (graphify_extract.CallKey, as map_graph marks
# them) — an unmarked string is a NAME even when it equals a node key
import graphify_extract as _GX_R11  # noqa: E402
_PY_KEYED = [(a, _GX_R11.call_key(b)) for a, b in _PY_KEYED]


def _name_of(nodes):
    return {n["key"]: n["name"] for n in nodes}


def _flag_off_calls(calls, nodes):
    nm = _name_of(nodes)
    return [(a, nm.get(b, b)) for a, b in calls]


def gate_a_python() -> None:
    print("\n-- A: P1, Python — a receiver call falls back to the flag-off fan-out; bare / module keep the key --")
    S = _load()
    check("sync._unkey_receiver_calls exists", callable(getattr(S, "_unkey_receiver_calls", None)))
    on = _edges(S, _PY, _PY_NODES, _PY_KEYED, DEEPMAP_PRECISE_CALLS="1")
    off = _edges(S, _PY, _PY_NODES, _flag_off_calls(_PY_KEYED, _PY_NODES))
    methods_sc = {"pkg_core_command_sc", "pkg_core_group_sc", "pkg_core_parameter_sc"}
    gc = _of(on, "pkg_shell_get_completions")
    check("obj.shell_complete(): no firm EXTRACTED edge to the module-level function",
          gc.get("pkg_shell_shell_complete") != "EXTRACTED", gc)
    check("obj.shell_complete(): the polymorphic methods are back (Command/Group/Parameter.shell_complete)",
          methods_sc <= set(gc), gc)
    # the caller's OTHER keyed tuple (-> Command.shell_complete, a :Method) is resolved as precise resolves it on
    # its own (round 7's T3#6 grades it: the caller's module defines a `shell_complete`)
    alone = _of(_edges(S, _PY, _PY_NODES, [("pkg_shell_get_completions", _GX_R11.call_key("pkg_core_command_sc"))],
                       DEEPMAP_PRECISE_CALLS="1"), "pkg_shell_get_completions")
    check("obj.shell_complete(): the edges equal the flag-OFF edges for that caller; its :Method key keeps the "
          "grade precise gives it alone",
          {k: v for k, v in gc.items() if k != "pkg_core_command_sc"}
          == {k: v for k, v in _of(off, "pkg_shell_get_completions").items() if k != "pkg_core_command_sc"}
          and "pkg_core_command_sc" in alone and gc.get("pkg_core_command_sc") == alone["pkg_core_command_sc"],
          (gc, _of(off, "pkg_shell_get_completions"), alone))
    wi = _of(on, "pkg_shell_wrapper_isatty")
    check("self._stream.isatty(): == the flag-OFF fan-out (isatty fn + ConsoleStream.isatty, AMBIGUOUS)",
          wi == _of(off, "pkg_shell_wrapper_isatty") and wi.get("pkg_shell_isatty") == "AMBIGUOUS"
          and "pkg_term_consolestream_isatty" in wi, (wi, _of(off, "pkg_shell_wrapper_isatty")))
    check("a BARE shell_complete() keeps its firm same-file key (EXTRACTED, no fan-out)",
          _of(on, "pkg_shell_run") == {"pkg_shell_shell_complete": "EXTRACTED"}, _of(on, "pkg_shell_run"))
    both = _of(on, "pkg_shell_both")
    check("bare + receiver in one caller: the key stays EXTRACTED AND the receiver's methods are added",
          both.get("pkg_shell_shell_complete") == "EXTRACTED" and methods_sc <= set(both)
          and all(both[m] == "AMBIGUOUS" for m in methods_sc), both)
    check("a module receiver (`from pkg import shell; shell.shell_complete(`) keeps the key (INFERRED, cross-file)",
          _of(on, "pkg_use_via_module") == {"pkg_shell_shell_complete": "INFERRED"}, _of(on, "pkg_use_via_module"))
    check("a package receiver (`import pkg; pkg.helper(` -> pkg/util.py) keeps the key",
          _of(on, "pkg_use_via_package") == {"pkg_util_helper": "INFERRED"}, _of(on, "pkg_use_via_package"))
    vo = _of(on, "pkg_use_via_os")
    check("`os.isatty(` into another file's module-level isatty falls back to the flag-OFF fan-out",
          vo == _of(off, "pkg_use_via_os") and len(vo) >= 2, (vo, _of(off, "pkg_use_via_os")))
    check("`x.helper(` falls back to the flag-OFF tuple (a unique name: INFERRED, as with the flag off)",
          _of(on, "pkg_use_via_obj") == _of(off, "pkg_use_via_obj") == {"pkg_util_helper": "INFERRED"},
          (_of(on, "pkg_use_via_obj"), _of(off, "pkg_use_via_obj")))
    check("undecidable callers keep graphify's key (no visible call site; def not found at the node's line)",
          _of(on, "pkg_use_no_site") == {"pkg_util_helper": "INFERRED"}
          and _of(on, "pkg_use_lost") == {"pkg_util_helper": "INFERRED"},
          (_of(on, "pkg_use_no_site"), _of(on, "pkg_use_lost")))
    unkey = getattr(S, "_unkey_receiver_calls", None)
    if callable(unkey):
        with tempfile.TemporaryDirectory() as td, _env(DEEPMAP_PRECISE_CALLS="1"):
            root = Path(td)
            _write_tree(root, _PY)
            got, n = unkey(root, [dict(x) for x in _PY_NODES], list(_PY_KEYED))
            with_missing, n2 = unkey(Path(td) / "gone", [dict(x) for x in _PY_NODES], list(_PY_KEYED))
        fb = [c for c in got if c not in _PY_KEYED]
        check("the fallback (caller, name) tuples come FIRST, so a keyed edge of the same caller keeps its grade",
              got[:len(fb)] == fb and len(fb) == n and n == 5, (n, got[:8]))
        check("the :Method key (get_completions -> Command.shell_complete) is untouched",
              ("pkg_shell_get_completions", "pkg_core_command_sc") in got)
        check("an unreadable tree: the calls come back unchanged", with_missing == list(_PY_KEYED) and n2 == 0,
              (n2, with_missing[:3]))


# =====================================================================================================
# B — P1, other languages (same-file text rule) + the _text_call_forms refactor
# =====================================================================================================
_JS = {
    "web/api.js": (
        "function helper(x) {\n"                     # 1
        "  return x;\n"                              # 2
        "}\n"                                        # 3
        "\n"                                         # 4
        "class Api {\n"                              # 5
        "  helper(x) {\n"                            # 6
        "    return x;\n"                            # 7
        "  }\n"                                      # 8
        "}\n"                                        # 9
        "\n"                                         # 10
        "function viaObj(obj) {\n"                   # 11
        "  return obj.helper(1);\n"                  # 12
        "}\n"                                        # 13
        "\n"                                         # 14
        "function viaBare() {\n"                     # 15
        "  return helper(2);\n"                      # 16
        "}\n"                                        # 17
        "\n"                                         # 18
        "function viaChain(api) {\n"                 # 19
        "  return api.\n"                            # 20
        "    helper(3);\n"                           # 21
        "}\n"                                        # 22
        "\n"                                         # 23
        "function viaExports() {\n"                  # 24
        "  return exports.helper(5) + module.exports.helper(6);\n"   # 25
        "}\n"),                                      # 26
    "web/other.js": "function useIt(ns) {\n  return ns.helper(4);\n}\n",
    "src/lib.rs": (
        "fn helper() -> i32 {\n"                     # 1
        "    1\n"                                    # 2
        "}\n"                                        # 3
        "\n"                                         # 4
        "fn via_path() -> i32 {\n"                   # 5
        "    self::helper()\n"                       # 6
        "}\n"                                        # 7
        "\n"                                         # 8
        "fn via_dot(s: &S) -> i32 {\n"               # 9
        "    s.helper()\n"                           # 10
        "}\n"),                                      # 11
}
_JS_NODES = [
    _n("web_api_helper", "helper", "web/api.js", 1),
    _n("web_api_api", "Api", "web/api.js", 5, "Class"),
    _n("web_api_api_helper", "Api.helper", "web/api.js", 6, "Method"),
    _n("web_api_viaobj", "viaObj", "web/api.js", 11),
    _n("web_api_viabare", "viaBare", "web/api.js", 15),
    _n("web_api_viachain", "viaChain", "web/api.js", 19),
    _n("web_api_viaexports", "viaExports", "web/api.js", 24),
    _n("web_other_useit", "useIt", "web/other.js", 1),
    _n("src_lib_helper", "helper", "src/lib.rs", 1),
    _n("src_lib_via_path", "via_path", "src/lib.rs", 5),
    _n("src_lib_via_dot", "via_dot", "src/lib.rs", 9),
    _n("src_lib_s_helper", "S.helper", "src/lib.rs", 20, "Method"),
]
_JS_KEYED = [("web_api_viaobj", "web_api_helper"), ("web_api_viabare", "web_api_helper"),
             ("web_api_viachain", "web_api_helper"), ("web_other_useit", "web_api_helper"),
             ("web_api_viaexports", "web_api_helper"),
             ("src_lib_via_path", "src_lib_helper"), ("src_lib_via_dot", "src_lib_helper")]
_JS_KEYED = [(a, _GX_R11.call_key(b)) for a, b in _JS_KEYED]   # R11/R1: marked keys (see _PY_KEYED)


def gate_b_text() -> None:
    print("\n-- B: P1, other languages — same-file `obj.f(` falls back; `f(` / `self::f(` / another file keep --")
    S = _load()
    on = _edges(S, _JS, _JS_NODES, _JS_KEYED, DEEPMAP_PRECISE_CALLS="1")
    off = _edges(S, _JS, _JS_NODES, _flag_off_calls(_JS_KEYED, _JS_NODES))
    check("JS `obj.helper(` (same file) == the flag-OFF fan-out (function + Api.helper)",
          _of(on, "web_api_viaobj") == _of(off, "web_api_viaobj")
          and "web_api_api_helper" in _of(on, "web_api_viaobj"), (_of(on, "web_api_viaobj"), _of(off, "web_api_viaobj")))
    check("JS `api.` / `helper(` split after the dot (R9/K10 continuation) falls back too",
          _of(on, "web_api_viachain") == _of(off, "web_api_viachain"), _of(on, "web_api_viachain"))
    check("JS bare `helper(` keeps the EXTRACTED key",
          _of(on, "web_api_viabare") == {"web_api_helper": "EXTRACTED"}, _of(on, "web_api_viabare"))
    check("JS `exports.helper(` / `module.exports.helper(` (CommonJS: the file's own f) keep the EXTRACTED key, "
          "as the map-time rule reads them",
          _of(on, "web_api_viaexports") == {"web_api_helper": "EXTRACTED"}, _of(on, "web_api_viaexports"))
    check("JS `ns.helper(` in ANOTHER file keeps graphify's key (a namespace import is how files call it)",
          _of(on, "web_other_useit") == {"web_api_helper": "INFERRED"}, _of(on, "web_other_useit"))
    check("Rust `self::helper()` (a scope path) keeps the EXTRACTED key",
          _of(on, "src_lib_via_path") == {"src_lib_helper": "EXTRACTED"}, _of(on, "src_lib_via_path"))
    check("Rust `s.helper()` (method syntax) falls back to the flag-OFF fan-out",
          _of(on, "src_lib_via_dot") == _of(off, "src_lib_via_dot") and "src_lib_s_helper" in _of(on, "src_lib_via_dot"),
          (_of(on, "src_lib_via_dot"), _of(off, "src_lib_via_dot")))
    kinds = getattr(S, "_text_call_kinds", None)
    check("sync._text_call_kinds exists (the operator-aware split of _text_call_forms)", callable(kinds))
    lines = [("  return obj.helper(1);", None), ("  return helper(2);", None), ("  self::helper()", None),
             ("  p->helper(1);", None), ("  a?.helper(1)", None), ("    helper(3);", "  return api."),
             ("    helper(3);", "  return crate::"), ("    helper(3);", "  x = 1."), ("function helper(x) {", None),
             ("  helper(x) {", None), ("  new helper(1)", None), ("  helper (1) + obj.helper(2)", None),
             ("    helper(3);", "  return a \\"), ("  f(helper(1))", None), ("  // helper(1)", None)]
    if callable(kinds):
        check("_text_call_kinds: `.`/`?.`/`->` = member, `::` = path, continuation takes the previous operator",
              [kinds(t, "helper", p) for t, p in lines[:8]]
              == [["member"], ["bare"], ["path"], ["member"], ["member"], ["member"], ["path"], ["bare"]],
              [kinds(t, "helper", p) for t, p in lines[:8]])
        check("_text_call_forms == kinds mapped to bare/qualified on every probe line (byte-identical output)",
              all(S._text_call_forms(t, "helper", p) == ["bare" if k == "bare" else "qualified" for k in kinds(t, "helper", p)]
                  for t, p in lines), [(t, S._text_call_forms(t, "helper", p)) for t, p in lines])
    more = lines + [("\tFirst()", "\treturn q."), ("\tFirst()", "\tx := 1"), ("\tFirst()", "\ts := \"end.\""),
                    ("\tFirst()", None), ("\tx := First()", "\treturn q."), ("  return a..helper(1)", None),
                    ("    helper(1)", "  x = 1."), ("    helper(1)", "  a = [1, 2, ..."), ("  func (r T) helper(x) {", None),
                    ("  public helper(x): number {", None), ("  helper(a, helper(b))", "  return q."),
                    ("    helper(3);", lambda: "  return api.")]
    ref = [(t, _ref_text_call_forms(S, t, "helper" if "helper" in t else "First", p)) for t, p in more]
    got = [(t, S._text_call_forms(t, "helper" if "helper" in t else "First", p)) for t, p in more]
    check("_text_call_forms: the round-9b implementation's output on every probe line (the kinds refactor is "
          "output-identical)", got == ref, [(a, b) for a, b in zip(got, ref) if a != b])


def _ref_text_call_forms(S, text: str, name: str, prev=None) -> list:
    """The round-9b sync._text_call_forms, verbatim (the reference the round-10 refactor must reproduce),
    over the module's own constants."""
    out: list = []
    i = text.find(name)
    while i >= 0:
        k = i + len(name)
        j = k
        while j < len(text) and text[j] in " \t":
            j += 1
        if not S._is_ident_char(text[i - 1] if i else "") and not S._is_ident_char(text[k] if k < len(text) else "") \
                and j < len(text) and text[j] == "(":
            before = text[:i].rstrip()
            if before.endswith(S._MEMBER_TAIL) or (not before and prev is not None and S._continues_member(prev)):
                out.append("qualified")
            elif not (S._DEF_WORD_BEFORE.search(before) or S._GO_METHOD_DEF_BEFORE.search(before)):
                depth, close = 0, None
                for p in range(j, len(text)):
                    if text[p] == "(":
                        depth += 1
                    elif text[p] == ")":
                        depth -= 1
                        if depth == 0:
                            close = p
                            break
                rest = text[close + 1:].lstrip() if close is not None else ""
                member_def = ((S._MODIFIERS_ONLY.match(before) and rest[:1] in ("{", ":"))
                              or (before.endswith(("{", ",")) and rest[:1] == "{"))
                if not member_def:
                    out.append("bare")
        i = text.find(name, i + 1)
    return out


# =====================================================================================================
# C — P1: flag OFF identity, sync() wiring, the real engine
# =====================================================================================================
class _Res:
    def __init__(self, rows=None):
        self._rows = list(rows or [])

    def __iter__(self):
        return iter(self._rows)

    def single(self):
        return self._rows[0] if self._rows else None

    def consume(self):
        return None


class _Fake:
    """A fake driver over a tiny property store: answers the reads the round-10 pass makes and logs every
    statement, in order. `graph` = {key: {"name", "summ", "short"}} (the existing indexed text)."""

    def __init__(self, graph=None, pending=False, short_nodes=0, indexes=(), config=None, reject_exact=False):
        self.graph = dict(graph or {})
        self.pending, self.short_nodes = pending, short_nodes
        self.indexes, self.config, self.reject_exact = list(indexes), dict(config or {}), reject_exact
        self.log: list = []

    def session(self, *a, **k):
        return _FakeSess(self)

    def verify_connectivity(self):
        return None

    def close(self):
        return None


class _FakeSess:
    def __init__(self, d):
        self.d = d

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, cypher, **p):
        d = self.d
        d.log.append(cypher)
        if "UNWIND $rows AS row MATCH" in cypher and "RETURN count(x) AS c" in cypher:
            nul = p.get("nul")
            c = 0
            for r in p.get("rows") or []:
                g = d.graph.get(r["key"])
                if g is None:
                    continue
                pairs = []           # only the properties the statement's WHERE actually compares
                if "coalesce(x.name, $nul) <> coalesce(row.name, $nul)" in cypher:
                    pairs.append((g.get("name"), r.get("name")))
                if "coalesce(x.body_summary, $nul) <> coalesce(row.summ, $nul)" in cypher:
                    pairs.append((g.get("summ"), r.get("summ")))
                if "coalesce(x.short_name, $nul) <> coalesce(row.short, $nul)" in cypher:
                    pairs.append((g.get("short"), r.get("short")))
                c += any((a if a is not None else nul) != (b if b is not None else nul) for a, b in pairs)
            return _Res([{"c": c}])
        if "fts_rebuild_pending AS p" in cypher:
            return _Res([{"p": True}] if d.pending else [])
        if "SET m.fts_rebuild_pending = true" in cypher:
            d.pending = True
        if "REMOVE m.fts_rebuild_pending" in cypher:
            d.pending = False
        if "n.short_name IS NOT NULL RETURN count(n)" in cypher:
            return _Res([{"c": d.short_nodes}])
        if "REMOVE n.short_name" in cypher:
            d.short_nodes = 0
        if cypher.startswith("SHOW INDEXES YIELD name, options"):
            name = cypher.split("name = '", 1)[1].split("'", 1)[0]
            cfg = d.config.get(name)
            return _Res([{"options": {"indexConfig": cfg}}] if cfg is not None else [])
        if cypher.startswith("SHOW INDEXES YIELD name, properties"):
            return _Res([{"properties": ["name", "body_summary", "docstring", "rationale"]}])
        if cypher.startswith("SHOW INDEXES YIELD name, state"):
            return _Res([{"name": n, "state": "ONLINE"} for n in d.indexes])
        if cypher.startswith("SHOW INDEXES"):
            return _Res([{"name": n} for n in d.indexes])
        if d.reject_exact and "vector.quantization.enabled" in cypher and cypher.startswith("CREATE"):
            raise RuntimeError("Neo.ClientError.Statement.ArgumentError: invalid index config key")
        return _Res()


def _sync_with(S, root, nodes, calls, drv, *, push_log=True, plan=None, owner=None, switch=False):
    """Run sync() on `root` with the extraction stubbed to (nodes, calls) and a fake driver; the push is
    logged as a 'PUSH' marker (and applied to the fake graph), the prune as 'PRUNE'. Returns
    (ok, edges the push resolved, captured stderr)."""
    Ext = getattr(S, "_Extraction")
    got: dict = {}

    def _push(driver, n, c, i, typed_edges=None):
        got["edges"] = S._resolve_calls(n, c)
        drv.log.append("PUSH")
        for x in n:
            drv.graph[x["key"]] = {"name": x["name"], "summ": x["body_summary"],
                                   "short": S._short_name(x["name"]) if S._fts_short_name_enabled() else
                                   drv.graph.get(x["key"], {}).get("short")}
        return (len(n), len(got["edges"]))

    S._rebuild_code = lambda r: Ext([dict(x) for x in nodes], list(calls), [], "graphify",
                                    {"requested": "auto", "resolved": "graphify", "fallback_reason": None})
    S._apply_recognizers = lambda r, ns: (ns, [])
    S.push_to_neo4j = _push
    S._graph_has_nodes = lambda d: bool(drv.graph)
    S._graph_holds_nodes = lambda d: bool(drv.graph)
    S._reuse_rekeyed_embeddings = lambda d, ns: 0
    S._read_owning_root = lambda d: owner
    S._wipe_all = lambda d: (drv.log.append("WIPE"), drv.graph.clear())
    S._cleanup_stale = lambda d, live: drv.log.append("PRUNE")
    if plan is not None:
        S._plan_prune = lambda *a, **k: dict(plan)
    real = _load("indexes")
    S.indexes = SimpleNamespace(**{k: getattr(real, k) for k in dir(real) if not k.startswith("__")})
    S.indexes.wait_for_indexes = lambda d, timeout=120: True
    S.embeddings = SimpleNamespace(generate_embeddings=lambda d: 0)
    S._count_embedded_nodes = lambda d, fallback=0: len(nodes)
    S._maybe_auto_tune = lambda r: None
    out = io.StringIO()
    with contextlib.redirect_stderr(out), contextlib.redirect_stdout(out):
        ok = S.sync(root, driver=drv, cleanup_stale=plan is not None, switch_project=switch)
    return ok, got.get("edges") or {}, out.getvalue()


@contextlib.contextmanager
def _project(tree, **env):
    with tempfile.TemporaryDirectory() as td, _env(NEO4J_URI="bolt://127.0.0.1:7999",
                                                   DEEPMAP_HOME=str(Path(td) / "home"), **env):
        root = Path(td) / "p"
        root.mkdir()
        (root / ".deepmap-root").write_text("", encoding="utf-8")
        _write_tree(root, tree)
        yield root


def gate_c_off_wiring_engine() -> None:
    print("\n-- C: P1 — flag OFF identity; sync() pushes the fan-out; the real engine --")
    S = _load()
    unkey = getattr(S, "_unkey_receiver_calls", None)
    if callable(unkey):
        reads = []
        orig = S._read_source_lines
        S._read_source_lines = lambda root, rel: (reads.append(rel), orig(root, rel))[1]
        try:
            with tempfile.TemporaryDirectory() as td, _env(DEEPMAP_PRECISE_CALLS=None):
                root = Path(td)
                _write_tree(root, _PY)
                calls = list(_PY_KEYED)
                got, n = unkey(root, [dict(x) for x in _PY_NODES], calls)
        finally:
            S._read_source_lines = orig
        check("flag OFF: _unkey_receiver_calls returns the SAME list object, 0 changes, reads no file",
              got is calls and n == 0 and not reads, (n, reads))
    else:
        check("flag OFF: _unkey_receiver_calls exists", False)
    # R10/P5 contract (W5): a key-form callee binds whenever graphify_extract.keyed_calls_enabled() is true —
    # under DEEPMAP_GRAPHIFY_CALLS_PLUS alone too — and the P1 guard judges those keys as well
    kce = getattr(S.graphify_extract, "keyed_calls_enabled", None)
    if callable(kce):
        truth = []
        for p, c in ((None, None), ("1", None), (None, "1"), ("0", "0")):
            with _env(DEEPMAP_PRECISE_CALLS=p, DEEPMAP_GRAPHIFY_CALLS_PLUS=c):
                truth.append((S._precise_calls_enabled(), kce()))
        check("sync._precise_calls_enabled follows graphify_extract.keyed_calls_enabled (PRECISE and/or CALLS_PLUS)",
              truth == [(False, False), (True, True), (True, True), (False, False)], truth)
        plus = _edges(S, _PY, _PY_NODES, _PY_KEYED, DEEPMAP_GRAPHIFY_CALLS_PLUS="1")
        check("CALLS_PLUS alone: a keyed tuple binds its node (no fan-out) and the P1 guard still applies",
              _of(plus, "pkg_shell_run") == {"pkg_shell_shell_complete": "EXTRACTED"}
              and _of(plus, "pkg_shell_wrapper_isatty").get("pkg_shell_isatty") == "AMBIGUOUS",
              (_of(plus, "pkg_shell_run"), _of(plus, "pkg_shell_wrapper_isatty")))
    else:
        print("  [SKIP] graphify_extract.keyed_calls_enabled is absent — the P5 contract check is not run")
    lazy = []
    kinds = getattr(S, "_text_call_kinds", None)
    if callable(kinds):
        kinds("\t\tFirst()", "First", prev=lambda: lazy.append(1) or "\treturn q.")
    check("_text_call_kinds reads a lazy previous line at most once (the r9b contract)", lazy == [1], lazy)
    # sync() end to end with a fake driver (the push resolves the calls exactly as push_to_neo4j would)
    with _project(_PY, DEEPMAP_PRECISE_CALLS="1") as root:
        drv = _Fake()
        ok, edges, text = _sync_with(S, root, _PY_NODES, _PY_KEYED, drv)
    gc = _of(edges, "pkg_shell_get_completions")
    check("sync() under the flag: get_completions reaches Command/Group/Parameter.shell_complete, not a firm "
          "edge to the module function",
          ok and {"pkg_core_group_sc", "pkg_core_parameter_sc"} <= set(gc)
          and gc.get("pkg_shell_shell_complete") == "AMBIGUOUS", (ok, gc, text[-600:]))
    check("sync() says what it did (one stderr line naming the fallback)",
          "receiver call(s)" in text and "fan out by name" in text, text[-800:])
    with _project(_PY, DEEPMAP_PRECISE_CALLS=None) as root:
        drv = _Fake()
        ok, edges_off, text = _sync_with(S, root, _PY_NODES, _flag_off_calls(_PY_KEYED, _PY_NODES), drv)
    check("sync() flag OFF: no fallback line, and the edges equal _resolve_calls over the flag-off tuples",
          ok and "receiver call(s)" not in text
          and edges_off == _edges(S, _PY, _PY_NODES, _flag_off_calls(_PY_KEYED, _PY_NODES)), text[-400:])
    _engine_check(S)


_ENGINE_TREE = {
    "pkg/__init__.py": "",
    "pkg/shell.py": _PY["pkg/shell.py"],
    "pkg/core.py": _PY["pkg/core.py"],
    "pkg/term.py": _PY["pkg/term.py"],
}


def _engine_check(S) -> None:
    try:
        import graphify  # noqa: F401
    except Exception:
        print("  [SKIP] graphify is not installed in this interpreter — the real-engine check is not run")
        return
    res = {}
    for flag in (None, "1"):
        with tempfile.TemporaryDirectory() as td, _env(DEEPMAP_EXTRACTOR="graphify", DEEPMAP_PRECISE_CALLS=flag,
                                                        DEEPMAP_HOME=str(Path(td) / "home")):
            root = Path(td) / "probe"
            _write_tree(root, _ENGINE_TREE)
            Sx = _load()
            with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
                nodes, calls, _imports, engine = Sx._rebuild_code(root)
            calls = _passes(Sx, root, nodes, list(calls))
            edges = Sx._resolve_calls(nodes, calls)
            nm = {n["key"]: n["name"] for n in nodes}
            res[flag] = (engine, {(nm.get(a), nm.get(b)): c for (a, b), c in edges.items()})
    eng, on = res["1"]
    _e, off = res[None]
    if eng != "graphify":
        print(f"  [SKIP] the engine resolved to {eng!r}, not graphify — the real-engine check is not run")
        return

    def mine(edges, caller):
        return {b: c for (a, b), c in edges.items() if a == caller}
    check("real engine, precise: ShellComplete.get_completions -> the three shell_complete methods, == flag OFF",
          mine(on, "ShellComplete.get_completions") == mine(off, "ShellComplete.get_completions")
          and {"Command.shell_complete", "Group.shell_complete", "Parameter.shell_complete"}
          <= set(mine(on, "ShellComplete.get_completions")),
          (mine(on, "ShellComplete.get_completions"), mine(off, "ShellComplete.get_completions")))
    check("real engine, precise: Wrapper.isatty -> == flag OFF (no firm edge to the module function isatty)",
          mine(on, "Wrapper.isatty") == mine(off, "Wrapper.isatty")
          and mine(on, "Wrapper.isatty").get("isatty") != "EXTRACTED", mine(on, "Wrapper.isatty"))
    check("real engine, precise: the bare shell_complete() in ShellComplete.run stays one EXTRACTED edge",
          mine(on, "ShellComplete.run") == {"shell_complete": "EXTRACTED"}, mine(on, "ShellComplete.run"))
    both, both_off = mine(on, "ShellComplete.both"), mine(off, "ShellComplete.both")
    check("real engine, precise: ShellComplete.both (a bare AND an `obj.` call) keeps its EXTRACTED key AND reaches "
          "the three methods (the map-time rule keeps the key only; the push adds the receiver's fan-out)",
          both.get("shell_complete") == "EXTRACTED" and set(both) == set(both_off)
          and {"Command.shell_complete", "Group.shell_complete", "Parameter.shell_complete"} <= set(both),
          (both, both_off))


# =====================================================================================================
# D — P2 units: the change count and the rebuild statement
# =====================================================================================================
def gate_d_fts_units() -> None:
    print("\n-- D: P2 units — _fts_changed_docs (server-side count) and indexes.rebuild_fts_index --")
    S = _load()
    I = _load("indexes")
    nodes = [_n("a", "Svc.run", "a.py", 1, "Method"), _n("b", "top", "a.py", 5), _n("c", "new", "a.py", 9),
             _n("x", "Odd", "a.py", 12, "Community")]
    fn = getattr(S, "_fts_changed_docs", None)
    check("sync._fts_changed_docs exists", callable(fn))
    if callable(fn):
        graph = {"a": {"name": "Svc.run", "summ": "Svc.run"}, "b": {"name": "top", "summ": "OLD text"}}
        with _env():
            drv = _Fake(graph=graph)
            c = fn(drv, nodes)
        stmts = [s for s in drv.log if "UNWIND" in s]
        check("counts only EXISTING nodes whose indexed text changes (b: summary rewritten; a same; c new)",
              c == 1, c)
        check("server-side: UNWIND $rows ... MATCH by key ... RETURN count — no texts come back; short_name "
              "not compared while it is not indexed; a non-FTS label is not sent",
              stmts and all("RETURN count(x) AS c" in s and "short_name" not in s for s in stmts)
              and not any(":Community" in s for s in stmts), stmts)
        with _env(DEEPMAP_FTS_SHORT_NAME="1"):
            drv = _Fake(graph={"a": {"name": "Svc.run", "summ": "Svc.run", "short": None}})
            c2 = fn(drv, nodes)
        check("DEEPMAP_FTS_SHORT_NAME=1: a short_name the push adds counts (a: None -> 'run')", c2 == 1, c2)
        with _env():
            drv = _Fake(graph={"b": {"name": "top", "summ": None}})
            c3 = fn(drv, [dict(nodes[1], body_summary="")])
        check("a missing summary vs '' is a change (coalesce sentinel, not '' )", c3 == 1, c3)
    rb = getattr(I, "rebuild_fts_index", None)
    check("indexes.rebuild_fts_index exists", callable(rb))
    if callable(rb):
        for flag, props in ((None, "n.name, n.body_summary, n.docstring, n.rationale"),
                            ("1", "n.name, n.body_summary, n.docstring, n.rationale, n.short_name")):
            with _env(DEEPMAP_FTS_SHORT_NAME=flag):
                drv = _Fake()
                rb(drv)
            check(f"rebuild_fts_index (short_name flag {flag or 'off'}): DROP then CREATE on fts_properties()",
                  drv.log == ["DROP INDEX deepmap_code_fts IF EXISTS",
                              "CREATE FULLTEXT INDEX deepmap_code_fts IF NOT EXISTS FOR "
                              f"(n:Function|Class|Module|Method|Route) ON EACH [{props}]"], drv.log)
        try:
            rb(_Fake(), "deepmap_fn_embeddings")
            refused = False
        except ValueError:
            refused = True
        check("rebuild_fts_index refuses a name that is not a DeepMap full-text index", refused)


# =====================================================================================================
# E — P2 wiring: the index is re-created AFTER the push when (and only when) its documents changed
# =====================================================================================================
_E_TREE = {"a.py": "def top():\n    return 1\n\n\nclass Svc:\n    def run(self):\n        return top()\n"}
_E_NODES = [_n("a_top", "top", "a.py", 1), _n("a_svc", "Svc", "a.py", 5, "Class"),
            _n("a_svc_run", "Svc.run", "a.py", 6, "Method")]
_E_CALLS = [("a_svc_run", "top")]
_DROP = "DROP INDEX deepmap_code_fts IF EXISTS"


def _fts_creates(log):
    return [i for i, s in enumerate(log) if s.startswith("CREATE FULLTEXT INDEX deepmap_code_fts")]


def gate_e_fts_wiring() -> None:
    print("\n-- E: P2 wiring — sync() re-creates deepmap_code_fts after a text-changing push, never otherwise --")
    S = _load()
    same = {n["key"]: {"name": n["name"], "summ": n["body_summary"]} for n in _E_NODES}
    # 1) a summary rewritten
    changed = dict(same, a_top={"name": "top", "summ": "an OLD summary"})
    with _project(_E_TREE) as root:
        drv = _Fake(graph=changed, indexes=["deepmap_code_fts"])
        ok, _e, text = _sync_with(S, root, _E_NODES, _E_CALLS, drv)
    log = drv.log
    push = log.index("PUSH") if "PUSH" in log else -1
    drops = [i for i, s in enumerate(log) if s == _DROP]
    creates = _fts_creates(log)
    check("a rewritten summary: deepmap_code_fts is DROPPED and re-CREATED after the push",
          ok and push >= 0 and any(i > push for i in drops) and any(i > push for i in creates), (ok, log[-12:]))
    mark = [i for i, s in enumerate(log) if "SET m.fts_rebuild_pending = true" in s]
    clear = [i for i, s in enumerate(log) if "REMOVE m.fts_rebuild_pending" in s]
    check("the pending stamp is set BEFORE the push and cleared AFTER the re-create",
          mark and clear and mark[0] < push < max(creates or [-1]) < clear[-1] and not drv.pending,
          (mark, push, creates, clear))
    check("the sync log names the rebuild and why", "full-text index re-created" in text and "rewritten" in text,
          text[-500:])
    # 2) a no-op re-sync
    with _project(_E_TREE) as root:
        drv = _Fake(graph=dict(same), indexes=["deepmap_code_fts"])
        ok, _e, text = _sync_with(S, root, _E_NODES, _E_CALLS, drv)
    check("a no-op re-sync (same text, nothing pruned): no DROP of the index, no stamp written, no rebuild line",
          ok and _DROP not in drv.log and not any("fts_rebuild_pending = true" in s for s in drv.log)
          and "full-text index re-created" not in text, [s for s in drv.log if "INDEX" in s or "pending" in s])
    # 3) a prune that deletes nodes
    with _project(_E_TREE) as root:
        drv = _Fake(graph=dict(same, gone={"name": "gone", "summ": "gone"}), indexes=["deepmap_code_fts"])
        plan = {"live": {n["key"] for n in _E_NODES}, "decision": "prune", "reason": None, "would_delete": 1,
                "existing_total": 4, "suspicious": None, "exempt": None}
        ok, _e, text = _sync_with(S, root, _E_NODES, _E_CALLS, drv, plan=plan)
    log = drv.log
    prune = log.index("PRUNE") if "PRUNE" in log else -1
    check("a stale prune that deletes nodes: the index is re-created AFTER the prune",
          ok and prune >= 0 and any(i > prune for i in _fts_creates(log)), log[-10:])
    with _project(_E_TREE) as root:
        drv = _Fake(graph=dict(same), indexes=["deepmap_code_fts"])
        plan0 = dict(plan, would_delete=0)
        ok, _e, text = _sync_with(S, root, _E_NODES, _E_CALLS, drv, plan=plan0)
    check("a prune with nothing to delete: no rebuild", ok and _DROP not in drv.log, drv.log[-8:])
    # 4) an interrupted earlier rebuild
    with _project(_E_TREE) as root:
        drv = _Fake(graph=dict(same), pending=True, indexes=["deepmap_code_fts"])
        ok, _e, text = _sync_with(S, root, _E_NODES, _E_CALLS, drv)
    check("an earlier sync's pending stamp: re-created even with no change, then the stamp is cleared",
          ok and _DROP in drv.log and not drv.pending and "interrupted" in text, (drv.log[-6:], text[-300:]))
    # 5) a project switch
    with _project(_E_TREE) as root:
        drv = _Fake(graph={"x": {"name": "x", "summ": "other project"}}, indexes=["deepmap_code_fts"])
        ok, _e, text = _sync_with(S, root, _E_NODES, _E_CALLS, drv, owner="C:/elsewhere/other", switch=True)
    log = drv.log
    wipe = log.index("WIPE") if "WIPE" in log else -1
    push = log.index("PUSH") if "PUSH" in log else -1
    check("a project switch: the index is re-created right after the wipe, BEFORE the push",
          ok and 0 <= wipe < push and any(wipe < i < push for i in _fts_creates(log))
          and any(wipe < i < push for i, s in enumerate(log) if s == _DROP), (ok, log[:14]))


# =====================================================================================================
# F — P3: DEEPMAP_FTS_SHORT_NAME off removes the stale short_name
# =====================================================================================================
_OLD_UPSERT = ("UNWIND $rows AS row MERGE (x:{label} {{key:row.key}}) "
               "SET x.name=row.name, x.source_file=row.sf, x.start_line=row.line, "
               "x.body_summary=row.summ, x.confidence=row.conf")


def gate_f_short_name() -> None:
    print("\n-- F: P3 — flag off removes a stale short_name in bounded batches; never-flagged graph: no write --")
    S = _load()
    fn = getattr(S, "_remove_stale_short_names", None)
    check("sync._remove_stale_short_names exists", callable(fn))
    if callable(fn):
        with _env():
            drv = _Fake(short_nodes=475)
            n = fn(drv)
        rem = [s for s in drv.log if "REMOVE n.short_name" in s]
        check("flag off, 475 stale: returns 475 and REMOVEs them in a batched CALL ... IN TRANSACTIONS",
              n == 475 and rem and "IN TRANSACTIONS OF" in rem[0] and "n.short_name IS NOT NULL" in rem[0], (n, rem))
        with _env():
            drv = _Fake(short_nodes=0)
            n = fn(drv)
        check("flag off, none stale (a never-flagged graph): one count read, NO write",
              n == 0 and len(drv.log) == 1 and "RETURN count(n)" in drv.log[0], drv.log)
        with _env(DEEPMAP_FTS_SHORT_NAME="1"):
            drv = _Fake(short_nodes=9)
            n = fn(drv)
        check("flag on: nothing read, nothing removed", n == 0 and not drv.log, drv.log)
    with _env():
        cap = []
        sess = SimpleNamespace(run=lambda c, **p: cap.append(c))
        S._upsert_nodes(sess, [_n("k", "Svc.run", "a.py", 1, "Method")])
    check("flag off: the node upsert statement is still byte-identical", cap == [_OLD_UPSERT.format(label="Method")],
          cap)
    with _project(_E_TREE) as root:
        same = {n["key"]: {"name": n["name"], "summ": n["body_summary"], "short": "run"} for n in _E_NODES}
        drv = _Fake(graph=same, short_nodes=2, indexes=["deepmap_code_fts"])
        ok, _e, text = _sync_with(S, root, _E_NODES, _E_CALLS, drv)
    check("sync() with the flag off over a graph an ON run left: short_names removed, index re-created",
          ok and drv.short_nodes == 0 and _DROP in drv.log and "stale short_name" in text, (drv.log[-8:], text[-300:]))


# =====================================================================================================
# G — P4: DEEPMAP_VECTOR_QUANTIZATION=0 (opt-in exact vector indexes)
# =====================================================================================================
_VEC = [("Function", "deepmap_fn_embeddings"), ("Method", "deepmap_method_embeddings"),
        ("Class", "deepmap_class_embeddings"), ("Module", "deepmap_module_embeddings"),
        ("Route", "deepmap_route_embeddings"), ("Community", "deepmap_community_embeddings")]


def _old_vec(label, name):
    return (f"CREATE VECTOR INDEX {name} IF NOT EXISTS FOR (n:{label}) ON (n.embedding) "
            "OPTIONS {indexConfig: {`vector.dimensions`: 768, `vector.similarity_function`: 'cosine', "
            "`vector.hnsw.m`: 24, `vector.hnsw.ef_construction`: 400}}")


def _vec_stmts(log):
    return [s for s in log if "VECTOR INDEX" in s or (s.startswith("DROP INDEX") and "embeddings" in s)]


def _cfg(q):
    c = {"vector.dimensions": 768, "vector.similarity_function": "COSINE", "vector.hnsw.m": 24,
         "vector.hnsw.ef_construction": 400}
    if q is not None:
        c["vector.quantization.enabled"] = q
    return c


def gate_g_quantization() -> None:
    print("\n-- G: P4 — DEEPMAP_VECTOR_QUANTIZATION=0; the default DDL byte for byte --")
    I = _load("indexes")
    old = [_old_vec(lb, nm) for lb, nm in _VEC]
    for val in (None, "1", "true", "", "yes", " on "):
        with _env(DEEPMAP_VECTOR_QUANTIZATION=val):
            drv = _Fake()
            st = I.create_all_indexes(drv)
        check(f"default ({val!r}): fresh graph -> the 6 vector DDLs are byte-identical, nothing dropped",
              _vec_stmts(drv.log) == old and all(st.get(nm) == "created" for _l, nm in _VEC), _vec_stmts(drv.log))
    names = [nm for _l, nm in _VEC]
    with _env():
        drv = _Fake(indexes=names, config={nm: _cfg(True) for nm in names})
        st = I.create_all_indexes(drv)
    check("default over quantized indexes: no DROP, the same DDL, status exists",
          _vec_stmts(drv.log) == old and all(st.get(nm) == "exists" for nm in names), _vec_stmts(drv.log))
    with _env():
        drv = _Fake(indexes=names, config={nm: _cfg(None) for nm in names})
        st = I.create_all_indexes(drv)
    check("default over indexes that do not report the setting (Neo4j < 5.23): no DROP", _vec_stmts(drv.log) == old)
    with _env():
        drv = _Fake(indexes=names, config={nm: _cfg(False) for nm in names})
        st = I.create_all_indexes(drv)
    vs = _vec_stmts(drv.log)
    check("default over indexes an opt-in run left EXACT: DROP + the default CREATE (the never-flagged state)",
          vs == [x for lb, nm in _VEC for x in (f"DROP INDEX {nm} IF EXISTS", _old_vec(lb, nm))]
          and all(st.get(nm) == "recreated" for nm in names), vs)
    for val in ("0", "false", "no", "OFF"):
        with _env(DEEPMAP_VECTOR_QUANTIZATION=val):
            drv = _Fake()
            st = I.create_all_indexes(drv)
        vs = _vec_stmts(drv.log)
        check(f"DEEPMAP_VECTOR_QUANTIZATION={val}: fresh graph -> every vector index created with "
              f"`vector.quantization.enabled`: false",
              len(vs) == 6 and all(s == _old_vec(lb, nm)[:-2] + ", `vector.quantization.enabled`: false}}"
                                   for s, (lb, nm) in zip(vs, _VEC)), vs)
    with _env(DEEPMAP_VECTOR_QUANTIZATION="0"):
        drv = _Fake(indexes=names, config={nm: _cfg(True) for nm in names})
        st = I.create_all_indexes(drv)
    vs = _vec_stmts(drv.log)
    check("=0 over quantized indexes: DROP + the exact CREATE, status recreated",
          len(vs) == 12 and vs[0] == "DROP INDEX deepmap_fn_embeddings IF EXISTS"
          and "`vector.quantization.enabled`: false" in vs[1] and all(st.get(nm) == "recreated" for nm in names),
          (vs[:3], st))
    with _env(DEEPMAP_VECTOR_QUANTIZATION="0"):
        drv = _Fake(indexes=names, config={nm: _cfg(False) for nm in names})
        st = I.create_all_indexes(drv)
    check("=0 over exact indexes: no DROP, status exists",
          not any(s.startswith("DROP") for s in _vec_stmts(drv.log)) and all(st.get(nm) == "exists" for nm in names),
          (_vec_stmts(drv.log)[:2], st))
    with _env(DEEPMAP_VECTOR_QUANTIZATION="0"):
        drv = _Fake(indexes=names, config={nm: _cfg("true") for nm in names})
        st = I.create_all_indexes(drv)
    check("=0 over indexes reporting the setting as the string 'true': recreated", all(
        st.get(nm) == "recreated" for nm in names), st)
    with _env(DEEPMAP_VECTOR_QUANTIZATION="0"):
        drv = _Fake(indexes=names, config={nm: _cfg(None) for nm in names})
        st = I.create_all_indexes(drv)
    check("=0 over indexes that do not report the setting: no DROP (never guessed)",
          not any(s.startswith("DROP") for s in _vec_stmts(drv.log)), _vec_stmts(drv.log)[:2])
    with _env(DEEPMAP_VECTOR_QUANTIZATION="0"):
        drv = _Fake(reject_exact=True)
        try:
            st = I.create_all_indexes(drv)
            crashed = None
        except Exception as exc:  # noqa: BLE001
            st, crashed = {}, exc
    vs = _vec_stmts(drv.log)
    check("=0 on a server that rejects the option: the default DDL follows, status says 'not supported', no crash",
          crashed is None and old[0] in vs and all("not supported" in st.get(nm, "") for nm in names),
          (crashed, st, vs[:3]))
    rep = getattr(I, "vector_quantization_report", None)
    check("indexes.vector_quantization_report exists (the doctor line)", callable(rep))
    if callable(rep):
        cases = [(None, _cfg(True), "OK", "ON"), ("0", _cfg(False), "OK", "OFF"),
                 ("0", _cfg(True), "WARN", "next sync"), (None, _cfg(False), "WARN", "next sync"),
                 (None, _cfg(None), "OK", "not reported")]
        for val, cfg, lvl, word in cases:
            with _env(DEEPMAP_VECTOR_QUANTIZATION=val):
                drv = _Fake(indexes=names, config={nm: cfg for nm in names})
                with drv.session() as s:
                    got = rep(s)
            check(f"report (setting {val or 'default'}, index reports "
                  f"{cfg.get('vector.quantization.enabled', 'nothing')}): {lvl} + '{word}'",
                  got[0] == lvl and word in got[1], got)
        with _env():
            with _Fake().session() as s:
                got = rep(s)
        check("report on a graph with no vector index yet: WARN, no crash", got[0] == "WARN", got)
    S = _load()
    with _project(_E_TREE, DEEPMAP_VECTOR_QUANTIZATION="0") as root:
        drv = _Fake(graph={n["key"]: {"name": n["name"], "summ": n["body_summary"]} for n in _E_NODES},
                    indexes=names + ["deepmap_code_fts"], config={nm: _cfg(True) for nm in names})
        ok, _e, text = _sync_with(S, root, _E_NODES, _E_CALLS, drv)
    check("sync() with =0 over quantized indexes says it re-created them (and that nothing is re-embedded)",
          ok and "re-created 6 vector index(es)" in text and "nothing is re-embedded" in text, text[-500:])


# =====================================================================================================
# H — the frozen ranking
# =====================================================================================================
_RANKING = ("_arm_vector", "_arm_fts", "_arm_graph_weighted", "_weighted_rrf", "_hydrate", "search",
            "DEFAULT_ARM_WEIGHTS", "RRF_K")


def _defs(text: str) -> dict:
    out = {}
    tree = ast.parse(text)
    lines = text.splitlines()
    for node in ast.walk(tree):
        name = getattr(node, "name", None)
        if isinstance(node, ast.Assign):
            name = next((t.id for t in node.targets if isinstance(t, ast.Name)), None)
        if name in _RANKING or (name or "").startswith("_arm_"):
            out.setdefault(name, "\n".join(lines[node.lineno - 1:node.end_lineno]))
    return out


def gate_h_frozen() -> None:
    """H — the working tree vs `git show HEAD:`. It only catches an UNCOMMITTED edit of the frozen code: once a
    change is committed (and always in CI's fresh checkout) the file IS HEAD, so the comparison is tautological and
    passes whatever the file says. It is not the lock — tests/o12_learned_fusion_lock_r283_gates.py pins
    learned_fusion.py's sha256 and tests/fts_r7_gates.py (_FROZEN) hashes the 3-arm ranking symbols."""
    print("\n-- H: frozen — retrieval.py's ranking functions and learned_fusion.py == HEAD --")

    def head(path):
        try:
            p = subprocess.run(["git", "show", f"HEAD:{path}"], cwd=str(REPO), capture_output=True, timeout=60)
            return p.stdout.decode("utf-8") if p.returncode == 0 else None
        except Exception:
            return None
    r_head, f_head = head("build/retrieval.py"), head("build/learned_fusion.py")
    if r_head is None or f_head is None:
        print("  [SKIP] git HEAD is not readable here — the frozen check is not run")
        return
    cur = _defs((BUILD / "retrieval.py").read_text(encoding="utf-8"))
    old = _defs(r_head)
    check("retrieval.py: every 3-arm ranking function / constant is byte-identical to HEAD",
          old and set(cur) == set(old) and all(cur[k] == old[k] for k in old),
          sorted(k for k in old if cur.get(k) != old[k]))
    check("learned_fusion.py is byte-identical to HEAD",
          (BUILD / "learned_fusion.py").read_bytes().replace(b"\r\n", b"\n") == f_head.encode("utf-8").replace(b"\r\n", b"\n"))


def main() -> int:
    for gate in (gate_a_python, gate_b_text, gate_c_off_wiring_engine, gate_d_fts_units, gate_e_fts_wiring,
                 gate_f_short_name, gate_g_quantization, gate_h_frozen):
        try:
            gate()
        except Exception as exc:  # noqa: BLE001 — a crashing section is a failure, not a skipped one
            import traceback
            check(f"{gate.__name__} ran without crashing", False, f"{type(exc).__name__}: {exc}\n"
                                                                   + traceback.format_exc()[-1200:])
    total = _PASS + _FAIL
    print(f"\nRESULT: {'GREEN' if not _FAIL else 'RED'} — {_PASS}/{total} checks passed.")
    return 0 if not _FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
