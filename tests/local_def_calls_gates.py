#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j gates for DEEPMAP_LOCAL_DEF_CALLS (default OFF) — a bare Python call binds the caller's own
definition (build/sync.py: _attach_local_def_calls + _resolve_calls).

Why: on the default path a caller reaches the push as (caller, NAME) and _resolve_calls binds EVERY node
whose last dotted segment is that name. Live on this repo (round 8), 252,048 of 262,024 CALLS edges (96%)
bound a bare Python call to ANOTHER file's same-name Function/Class although the caller's own module
defines that name at module level and does not import it. Python binds a bare name to the innermost
enclosing definition and then to the module's own global, so those edges are impossible; every
tests/*_gates.py `check` had in-degree ~1,249 and dominated the graph-weighted arm.

  A  flag OFF is byte-identical: the attach adds nothing, the resolver ignores the metadata, and the edge
     set + confidences (in order) equal the pre-change resolver on graphify-shaped AND ast fixtures, under
     the default, DEEPMAP_FALSE_CALL_FILTER=0 and DEEPMAP_PRECISE_CALLS=1 (golden digests captured from the
     code BEFORE this flag existed).
  B  the "164 copies of check" shape: each bare check() binds its OWN file's check only (INFERRED) —
     graphify-shaped and ast.
  C  the file also IMPORTS the name (from-import, alias, try/except fallback) -> today's fan-out.
  D  nested scopes: a nested def shadows (graphify: no node -> no cross-file edge; ast: the nested node
     only), a closure resolves to the enclosing function's def, a local assignment / parameter / `global`
     keeps today's behaviour.
  E  a module that does NOT define the name keeps today's behaviour.
  F  edge cases: bare + qualified in one body, recursion, star import, an unparseable file, a Module
     caller calling before the def, class-body sites.
  G  DEEPMAP_PRECISE_CALLS: a graphify key bound to ANOTHER file is re-bound to the local def (EXTRACTED,
     the F3b same-file grade); a same-file key and an imported name are unchanged.
  H  DEEPMAP_FALSE_CALL_FILTER interplay: J3's method drop and this rule compose; =0 does not veto this
     separate opt-in.
  I  sync() wiring: the pass runs before the push only when the flag is on, and says so.
  J  the real engine (graphify, when installed): the copies shape end to end.

Hermetic: temp dirs, synthetic nodes, a fake driver, stubbed index/embedding stages; no sockets, no Neo4j.
Run with --print-golden to print the OFF digests of the current resolver.
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build"
sys.path.insert(0, str(BUILD))

import graphify_extract as _GX  # noqa: E402  (R11/R1: the marker a graphify-resolved callee KEY carries)

_PASS = _FAIL = 0
_FLAG = "DEEPMAP_LOCAL_DEF_CALLS"


def _key(k: str) -> str:
    """R11/R1: `k` as map_graph hands over a graphify-RESOLVED callee — a MARKED key (graphify_extract.CallKey).
    An unmarked string is a NAME at the push, even when it equals some node's key."""
    return _GX.call_key(k)


def check(label: str, cond: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {label}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}{(' — ' + str(detail)[:700]) if detail else ''}")


def _load(name="sync"):
    spec = importlib.util.spec_from_file_location(f"dm_ldc_{name}", BUILD / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


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


# the env every resolution below runs under unless a section overrides one of them
_BASE_ENV = dict(DEEPMAP_PRECISE_CALLS=None, DEEPMAP_FALSE_CALL_FILTER=None, DEEPMAP_DROP_APP_TO_TEST_CALLS=None,
                 DEEPMAP_LOCAL_DEF_CALLS=None)


def _write(root: Path, rel: str, text: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def _n(key, name, sf, line=1, label="Function"):
    return {"key": key, "label": label, "name": name, "source_file": sf, "start_line": line,
            "body_summary": name, "documented": False}


def _attach(S, root, nodes, calls):
    """Every push-time source pass, in _sync_pass's order (the new one only when this build has it)."""
    with contextlib.redirect_stderr(io.StringIO()):
        fn = getattr(S, "_attach_bare_calls", None)
        if callable(fn):
            fn(root, nodes, calls)
        S._attach_local_defs(root, nodes, calls)
        fn = getattr(S, "_attach_local_def_calls", None)
        if callable(fn):
            fn(root, nodes, calls)


def _resolve(S, root, nodes, calls, **env):
    """Attach + resolve on a deep copy of `nodes`, under _BASE_ENV overridden by `env`."""
    nodes = json.loads(json.dumps(nodes))
    with _env(**dict(_BASE_ENV, **env)):
        _attach(S, root, nodes, calls)
        return S._resolve_calls(nodes, calls), nodes


def _digest(edges: dict) -> str:
    rows = [[a, b, c] for (a, b), c in edges.items()]           # insertion order is part of the identity
    return hashlib.sha256(json.dumps(rows, separators=(",", ":")).encode("utf-8")).hexdigest()[:24]


# =====================================================================================================
# The mixed fixture (sections A / C / D / E / F / G / H)
# =====================================================================================================
MIXED = {
    "tests/a_gates.py": (
        "import os\n"                              # 1
        "\n"                                       # 2
        "\n"                                       # 3
        "def check(name, cond):\n"                 # 4
        "    return cond\n"                        # 5
        "\n"                                       # 6
        "\n"                                       # 7
        "def gate_one():\n"                        # 8
        "    check('a', True)\n"                   # 9
        "    check('b', False)\n"                  # 10
        "\n"                                       # 11
        "\n"                                       # 12
        "def gate_rec():\n"                        # 13
        "    return gate_rec()\n"                  # 14
        "\n"                                       # 15
        "\n"                                       # 16
        "def _rec(x):\n"                           # 17
        "    return x\n"                           # 18
        "\n"                                       # 19
        "\n"                                       # 20
        "def gate_setup():\n"                      # 21
        "    def _rec(x):\n"                       # 22
        "        return x + 1\n"                   # 23
        "    return _rec(1)\n"                     # 24
        "\n"                                       # 25
        "\n"                                       # 26
        "def outer():\n"                           # 27
        "    def helper():\n"                      # 28
        "        return 1\n"                       # 29
        "\n"                                       # 30
        "    def inner():\n"                       # 31
        "        return helper()\n"                # 32
        "\n"                                       # 33
        "    return inner()\n"                     # 34
        "\n"                                       # 35
        "\n"                                       # 36
        "def gate_assign(obj):\n"                  # 37
        "    check = obj.check\n"                  # 38
        "    return check('z', 1)\n"               # 39
        "\n"                                       # 40
        "\n"                                       # 41
        "def gate_param(check):\n"                 # 42
        "    return check('p', 1)\n"               # 43
        "\n"                                       # 44
        "\n"                                       # 45
        "def gate_mixed(other):\n"                 # 46
        "    check('m', 1)\n"                      # 47
        "    return other.check('n', 2)\n"         # 48
        "\n"                                       # 49
        "\n"                                       # 50
        "def gate_nomod():\n"                      # 51
        "    return verify('q')\n"                 # 52
        "\n"                                       # 53
        "\n"                                       # 54
        "class Fake:\n"                            # 55
        "    def check(self, ip):\n"               # 56
        "        return ip\n"                      # 57
        "\n"                                       # 58
        "    def run(self):\n"                     # 59
        "        return check('r', 1)\n"           # 60
    ),
    "tests/b_gates.py": (
        "def check(name, cond):\n"                 # 1
        "    return cond\n"                        # 2
        "\n"                                       # 3
        "\n"                                       # 4
        "def gate_two():\n"                        # 5
        "    return check('c', True)\n"            # 6
        "\n"                                       # 7
        "\n"                                       # 8
        "def _rec(x):\n"                           # 9
        "    return x\n"                           # 10
        "\n"                                       # 11
        "\n"                                       # 12
        "def helper():\n"                          # 13
        "    return 2\n"                           # 14
        "\n"                                       # 15
        "\n"                                       # 16
        "def verify(x):\n"                         # 17
        "    return x\n"                           # 18
        "\n"                                       # 19
        "\n"                                       # 20
        "def gate_rec():\n"                        # 21
        "    return 0\n"                           # 22
        "\n"                                       # 23
        "\n"                                       # 24
        "def inner():\n"                           # 25
        "    return 3\n"                           # 26
    ),
    "tests/c_import.py": (
        "try:\n"                                   # 1
        "    from tests.b_gates import check\n"    # 2
        "except ImportError:\n"                    # 3
        "    def check(name, cond):\n"             # 4
        "        return cond\n"                    # 5
        "\n"                                       # 6
        "\n"                                       # 7
        "def gate_imp():\n"                        # 8
        "    return check('i', 1)\n"               # 9
    ),
    "tests/d_alias.py": (
        "from tests.b_gates import verify as check\n"   # 1
        "\n"                                       # 2
        "\n"                                       # 3
        "def other():\n"                           # 4
        "    return 1\n"                           # 5
        "\n"                                       # 6
        "\n"                                       # 7
        "def check(name, cond):\n"                 # 8
        "    return cond\n"                        # 9
        "\n"                                       # 10
        "\n"                                       # 11
        "def gate_alias():\n"                      # 12
        "    return check('al', 1)\n"              # 13
    ),
    "tests/e_global.py": (
        "def check(name, cond):\n"                 # 1
        "    return cond\n"                        # 2
        "\n"                                       # 3
        "\n"                                       # 4
        "def install(fn):\n"                       # 5
        "    global check\n"                       # 6
        "    check = fn\n"                         # 7
        "\n"                                       # 8
        "\n"                                       # 9
        "def gate_glob():\n"                       # 10
        "    return check('g', 1)\n"               # 11
    ),
    "tests/f_star.py": (
        "from tests.b_gates import *\n"            # 1
        "\n"                                       # 2
        "\n"                                       # 3
        "def check(name, cond):\n"                 # 4
        "    return cond\n"                        # 5
        "\n"                                       # 6
        "\n"                                       # 7
        "def gate_star():\n"                       # 8
        "    return check('s', 1)\n"               # 9
    ),
    "tests/g_broken.py": (
        "def check(name, cond):\n"                 # 1
        "    return cond\n"                        # 2
        "\n"                                       # 3
        "\n"                                       # 4
        "def gate_broken(:\n"                      # 5  (a syntax error: the file cannot be parsed)
        "    return check('b', 1)\n"               # 6
    ),
    "tests/h_modlevel.py": (
        "check('early', 0)\n"                      # 1  (runs at import, BEFORE the def)
        "\n"                                       # 2
        "\n"                                       # 3
        "def check(name, cond):\n"                 # 4
        "    return cond\n"                        # 5
        "\n"                                       # 6
        "\n"                                       # 7
        "check('late', 1)\n"                       # 8
    ),
    "tests/i_modlevel.py": (
        "def check(name, cond):\n"                 # 1
        "    return cond\n"                        # 2
        "\n"                                       # 3
        "\n"                                       # 4
        "check('late', 1)\n"                       # 5
    ),
    "tests/j_class.py": (
        "def check(name, cond):\n"                 # 1
        "    return cond\n"                        # 2
        "\n"                                       # 3
        "\n"                                       # 4
        "class K:\n"                               # 5
        "    def check(self):\n"                   # 6
        "        return 0\n"                       # 7
        "\n"                                       # 8
        "    y = check(None)\n"                    # 9  (class body: K's own check, defined above)
        "\n"                                       # 10
        "    def m(self):\n"                       # 11
        "        return check('m', 2)\n"           # 12 (a method: the class scope is skipped -> module)
        "\n"                                       # 13
        "\n"                                       # 14
        "class L:\n"                               # 15
        "    z = check('l', 1)\n"                  # 16
    ),
}


def _g(rel, name, line, label="Function"):
    """A graphify-shaped node: id = path + name, lowercased, like the engine's ids."""
    stem = rel[:-3].replace("/", "_")
    key = f"{stem}_{name.replace('.', '_').lower()}" if label != "Module" else stem
    return _n(key, name, rel, line, label)


MIXED_G_NODES = [
    _g("tests/a_gates.py", "a_gates", 1, "Module"),
    _g("tests/a_gates.py", "check", 4), _g("tests/a_gates.py", "gate_one", 8),
    _g("tests/a_gates.py", "gate_rec", 13), _g("tests/a_gates.py", "_rec", 17),
    _g("tests/a_gates.py", "gate_setup", 21), _g("tests/a_gates.py", "outer", 27),
    _g("tests/a_gates.py", "gate_assign", 37), _g("tests/a_gates.py", "gate_param", 42),
    _g("tests/a_gates.py", "gate_mixed", 46), _g("tests/a_gates.py", "gate_nomod", 51),
    _g("tests/a_gates.py", "Fake", 55, "Class"), _g("tests/a_gates.py", "Fake.check", 56, "Method"),
    _g("tests/a_gates.py", "Fake.run", 59, "Method"),
    _g("tests/b_gates.py", "b_gates", 1, "Module"),
    _g("tests/b_gates.py", "check", 1), _g("tests/b_gates.py", "gate_two", 5), _g("tests/b_gates.py", "_rec", 9),
    _g("tests/b_gates.py", "helper", 13), _g("tests/b_gates.py", "verify", 17),
    _g("tests/b_gates.py", "gate_rec", 21), _g("tests/b_gates.py", "inner", 25),
    _g("tests/c_import.py", "check", 4), _g("tests/c_import.py", "gate_imp", 8),
    _g("tests/d_alias.py", "other", 4), _g("tests/d_alias.py", "check", 8), _g("tests/d_alias.py", "gate_alias", 12),
    _g("tests/e_global.py", "check", 1), _g("tests/e_global.py", "install", 5), _g("tests/e_global.py", "gate_glob", 10),
    _g("tests/f_star.py", "check", 4), _g("tests/f_star.py", "gate_star", 8),
    _g("tests/g_broken.py", "check", 1), _g("tests/g_broken.py", "gate_broken", 5),
    _g("tests/h_modlevel.py", "h_modlevel", 1, "Module"), _g("tests/h_modlevel.py", "check", 4),
    _g("tests/i_modlevel.py", "i_modlevel", 1, "Module"), _g("tests/i_modlevel.py", "check", 1),
    _g("tests/j_class.py", "check", 1), _g("tests/j_class.py", "K", 5, "Class"),
    _g("tests/j_class.py", "K.check", 6, "Method"), _g("tests/j_class.py", "K.m", 11, "Method"),
    _g("tests/j_class.py", "L", 15, "Class"),
]
_K = {(n["source_file"].split("/")[-1][:-3], n["name"]): n["key"] for n in MIXED_G_NODES}


def gk(f, name):
    return _K[(f, name)]


# graphify records the calls written DIRECTLY in a def's body (not inside a nested def), as (caller, name)
MIXED_G_CALLS = [
    (gk("a_gates", "gate_one"), "check"), (gk("a_gates", "gate_rec"), "gate_rec"),
    (gk("a_gates", "gate_setup"), "_rec"), (gk("a_gates", "outer"), "inner"),
    (gk("a_gates", "outer"), "helper"),                     # (an engine that attributes the closure's call)
    (gk("a_gates", "gate_assign"), "check"), (gk("a_gates", "gate_param"), "check"),
    (gk("a_gates", "gate_mixed"), "check"), (gk("a_gates", "gate_nomod"), "verify"),
    (gk("a_gates", "Fake.run"), "check"),
    (gk("b_gates", "gate_two"), "check"),
    (gk("c_import", "gate_imp"), "check"), (gk("d_alias", "gate_alias"), "check"),
    (gk("e_global", "gate_glob"), "check"), (gk("f_star", "gate_star"), "check"),
    (gk("g_broken", "gate_broken"), "check"),
    (gk("h_modlevel", "h_modlevel"), "check"), (gk("i_modlevel", "i_modlevel"), "check"),
    (gk("j_class", "K"), "check"), (gk("j_class", "K.m"), "check"), (gk("j_class", "L"), "check"),
]

# precise CALLS: graphify's resolved target KEY (right or wrong) instead of the name
MIXED_P_CALLS = [                                             # (keys marked as map_graph marks them — R11/R1)
    (gk("a_gates", "gate_one"), _key(gk("b_gates", "check"))),    # the engine bound it to ANOTHER file
    (gk("b_gates", "gate_two"), _key(gk("b_gates", "check"))),    # same file: already right
    (gk("c_import", "gate_imp"), _key(gk("b_gates", "check"))),   # imported: really is b_gates.check
    (gk("a_gates", "gate_mixed"), _key(gk("b_gates", "check"))),  # bare + qualified: undecidable
    (gk("a_gates", "gate_nomod"), _key(gk("b_gates", "verify"))),
    (gk("a_gates", "Fake.run"), _key(gk("a_gates", "Fake.check"))),  # bare call the engine bound to a method
]


def _copies(n_files=12, prefix="tests/t"):
    """The '164 copies of check' shape: n files, each with a module-level check() and two gates."""
    tree, nodes, calls = {}, [], []
    for i in range(n_files):
        rel = f"{prefix}{i:02d}_gates.py"
        tree[rel] = ("def check(name, cond, detail=''):\n"      # 1
                     "    return cond\n"                         # 2
                     "\n"                                        # 3
                     "\n"                                        # 4
                     f"def gate_a{i}():\n"                       # 5
                     "    check('x', True)\n"                    # 6
                     "    check('y', 1 == 1, 'detail')\n"        # 7
                     "\n"                                        # 8
                     "\n"                                        # 9
                     f"def gate_b{i}():\n"                       # 10
                     "    return check('z', False)\n")           # 11
        nodes += [_g(rel, "check", 1), _g(rel, f"gate_a{i}", 5), _g(rel, f"gate_b{i}", 10)]
        calls += [(_g(rel, f"gate_a{i}", 5)["key"], "check"), (_g(rel, f"gate_b{i}", 10)["key"], "check")]
    return tree, nodes, calls


# Golden OFF digests — captured from build/sync.py BEFORE DEEPMAP_LOCAL_DEF_CALLS existed (the round-8
# resolver). Recompute (--print-golden) ONLY when a sibling resolution rule changes deliberately.
GOLDEN = {
    "graphify_default": "ac78ed4577a377124403c7fc",
    "graphify_fcf0": "2eeb9d163f8fe96cfc8bd61b",
    "graphify_precise": "efc457ea881d19115eaeb53e",
    # R9/K11 (deliberate, tests/sync_calls_r9b_gates.py G): the ast "bare" verdict no longer drops a method
    # when the bare name may hold a bound method — gate_assign (`check = obj.check`), gate_param (a `check`
    # parameter) and gate_glob (`global check; check = fn`) now keep Fake.check / K.check (AMBIGUOUS), as
    # the graphify path already did; +6 edges, nothing removed or regraded (was 6e44f4cc8f7a370eb7d15b43).
    "ast_default": "296055a0e7c09dcb9ee8a91a",
    "ast_fcf0": "4c446b2f6a2e496c9e0e7a8e",
    "copies_graphify": "3dbee632142e217da9070c9d",
    "copies_ast": "f10550f2adbf9a0b5d061575",
}


def _mixed_root(td) -> Path:
    root = Path(td) / "mixed"
    for rel, text in MIXED.items():
        _write(root, rel, text)
    return root


def _ast_extract(S, root):
    with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
        nodes, calls, _imports = S._rebuild_code_ast(root)
    return nodes, calls


def _off_digests(S) -> dict:
    out = {}
    with tempfile.TemporaryDirectory() as td:
        root = _mixed_root(td)
        out["graphify_default"] = _digest(_resolve(S, root, MIXED_G_NODES, MIXED_G_CALLS)[0])
        out["graphify_fcf0"] = _digest(_resolve(S, root, MIXED_G_NODES, MIXED_G_CALLS,
                                                DEEPMAP_FALSE_CALL_FILTER="0")[0])
        out["graphify_precise"] = _digest(_resolve(S, root, MIXED_G_NODES, MIXED_P_CALLS,
                                                   DEEPMAP_PRECISE_CALLS="1")[0])
        an, ac = _ast_extract(S, root)
        out["ast_default"] = _digest(_resolve(S, root, an, ac)[0])
        out["ast_fcf0"] = _digest(_resolve(S, root, an, ac, DEEPMAP_FALSE_CALL_FILTER="0")[0])
        tree, cn, cc = _copies()
        croot = Path(td) / "copies"
        for rel, text in tree.items():
            _write(croot, rel, text)
        out["copies_graphify"] = _digest(_resolve(S, croot, cn, cc)[0])
        an, ac = _ast_extract(S, croot)
        out["copies_ast"] = _digest(_resolve(S, croot, an, ac)[0])
    return out


# =====================================================================================================
# A — flag OFF is byte-identical
# =====================================================================================================
def gate_a_off_identity() -> None:
    print("\n-- A: DEEPMAP_LOCAL_DEF_CALLS unset / 0 is byte-identical to the pre-change resolver --")
    S = _load()
    check("sync has the new pass _attach_local_def_calls", callable(getattr(S, "_attach_local_def_calls", None)))
    check("sync has the flag reader _local_def_calls_enabled", callable(getattr(S, "_local_def_calls_enabled", None)))
    fn = getattr(S, "_local_def_calls_enabled", None)
    if callable(fn):
        truth = {}
        for v in (None, "", "0", "false", "no", "off", "1", "true", "YES", " on "):
            with _env(DEEPMAP_LOCAL_DEF_CALLS=v):
                truth[v] = fn()
        check("flag truth table: 1/true/yes/on (any case, trimmed) ON; unset/''/0/false/no/off OFF",
              [truth[v] for v in (None, "", "0", "false", "no", "off")] == [False] * 6
              and [truth[v] for v in ("1", "true", "YES", " on ")] == [True] * 4, truth)
    got = _off_digests(S)
    for name, want in GOLDEN.items():
        check(f"OFF digest '{name}' equals the pre-change resolver", want is not None and got.get(name) == want,
              f"got {got.get(name)} want {want}")
    with tempfile.TemporaryDirectory() as td:
        root = _mixed_root(td)
        for v in (None, "0", "false"):
            _edges, nodes = _resolve(S, root, MIXED_G_NODES, MIXED_G_CALLS, DEEPMAP_LOCAL_DEF_CALLS=v)
            check(f"flag={v!r}: the pass attaches no call_local metadata",
                  not any("call_local" in n for n in nodes), [n["key"] for n in nodes if "call_local" in n])
        # metadata present but flag OFF -> ignored (the resolver is gated, not only the attach)
        nodes = json.loads(json.dumps(MIXED_G_NODES))
        with _env(**dict(_BASE_ENV, DEEPMAP_LOCAL_DEF_CALLS="1")):
            _attach(S, root, nodes, MIXED_G_CALLS)
        with _env(**_BASE_ENV):
            with_meta = S._resolve_calls(nodes, MIXED_G_CALLS)
            plain = _resolve(S, root, MIXED_G_NODES, MIXED_G_CALLS)[0]
        check("flag OFF: _resolve_calls ignores call_local metadata left on the nodes",
              any("call_local" in n for n in nodes) and list(with_meta.items()) == list(plain.items()),
              {k: (with_meta.get(k), plain.get(k)) for k in set(with_meta) ^ set(plain)})


# =====================================================================================================
# B — the copies-of-check shape
# =====================================================================================================
def gate_b_copies() -> None:
    print("\n-- B: the '164 copies of check' shape binds each call to its OWN file's check --")
    S = _load()
    tree, cn, cc = _copies(12)
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "copies"
        for rel, text in tree.items():
            _write(root, rel, text)
        off = _resolve(S, root, cn, cc)[0]
        on = _resolve(S, root, cn, cc, DEEPMAP_LOCAL_DEF_CALLS="1")[0]
        an, ac = _ast_extract(S, root)
        a_off = _resolve(S, root, an, ac)[0]
        a_on = _resolve(S, root, an, ac, DEEPMAP_LOCAL_DEF_CALLS="1")[0]
    file_of = {n["key"]: n["source_file"] for n in cn}
    check("graphify-shaped OFF: every gate fans out to all 12 checks (288 edges, AMBIGUOUS)",
          len(off) == 24 * 12 and set(off.values()) == {"AMBIGUOUS"}, (len(off), set(off.values())))
    check("graphify-shaped ON: 24 edges, one per gate, each to its own file's check, INFERRED",
          len(on) == 24 and all(file_of[a] == file_of[b] and b.endswith("_check") for a, b in on)
          and set(on.values()) == {"INFERRED"}, sorted(on.items())[:6])
    a_file = {n["key"]: n["source_file"] for n in an}
    a_name = {n["key"]: n["name"] for n in an}
    check("ast OFF: the same fan-out (288 edges)", len(a_off) == 288, len(a_off))
    check("ast ON: 24 edges, each gate -> its own file's check, INFERRED",
          len(a_on) == 24 and all(a_file[a] == a_file[b] and a_name[b] == "check" for a, b in a_on)
          and set(a_on.values()) == {"INFERRED"}, sorted(a_on.items())[:6])
    in_deg = {}
    for (_a, b) in on:
        in_deg[b] = in_deg.get(b, 0) + 1
    check("ON: every check has in-degree exactly 2 (its own two gates), not 24",
          sorted(set(in_deg.values())) == [2] and len(in_deg) == 12, in_deg)


# =====================================================================================================
# C / D / E / F — the mixed fixture, graphify-shaped and ast
# =====================================================================================================
def _mixed_runs(S):
    with tempfile.TemporaryDirectory() as td:
        root = _mixed_root(td)
        g_off = _resolve(S, root, MIXED_G_NODES, MIXED_G_CALLS)[0]
        g_on, g_nodes = _resolve(S, root, MIXED_G_NODES, MIXED_G_CALLS, DEEPMAP_LOCAL_DEF_CALLS="1")
        an, ac = _ast_extract(S, root)
        a_off = _resolve(S, root, an, ac)[0]
        a_on, a_nodes = _resolve(S, root, an, ac, DEEPMAP_LOCAL_DEF_CALLS="1")
    return g_off, g_on, g_nodes, an, a_off, a_on


def _out(edges, caller):
    return {b: c for (a, b), c in edges.items() if a == caller}


def gate_cdef_mixed() -> None:
    S = _load()
    g_off, g_on, g_nodes, an, a_off, a_on = _mixed_runs(S)
    ak = {}
    for n in an:
        ak.setdefault((n["source_file"].split("/")[-1][:-3], n["name"]), []).append(n["key"])

    def ak1(f, name, line=None):
        keys = ak.get((f, name), [])
        if line is not None:
            keys = [k for k in keys if k.endswith(f":{line}")]
        return keys[0] if keys else None

    print("\n-- C: the file also IMPORTS the name -> today's fan-out --")
    for f, caller in (("c_import", "gate_imp"), ("d_alias", "gate_alias")):
        ck = gk(f, caller)
        check(f"graphify {f}.{caller}: ON == OFF (the import may be the binding)",
              _out(g_on, ck) == _out(g_off, ck) and len(_out(g_off, ck)) > 1, (_out(g_on, ck), _out(g_off, ck)))
        ack = ak1(f, caller)
        check(f"ast {f}.{caller}: ON == OFF", ack and _out(a_on, ack) == _out(a_off, ack) and _out(a_off, ack),
              (_out(a_on, ack), _out(a_off, ack)))

    print("\n-- D: nested scopes resolve locally first --")
    gs = gk("a_gates", "gate_setup")
    check("graphify gate_setup: its nested _rec shadows -> no edge to ANY other _rec (OFF had 2, AMBIGUOUS)",
          _out(g_on, gs) == {} and len(_out(g_off, gs)) == 2, (_out(g_on, gs), _out(g_off, gs)))
    ags, anested = ak1("a_gates", "gate_setup"), ak1("a_gates", "_rec", 22)
    check("ast gate_setup: binds ONLY its own nested _rec node (INFERRED)",
          anested and _out(a_on, ags) == {anested: "INFERRED"}, _out(a_on, ags))
    go = gk("a_gates", "outer")
    check("graphify outer: helper()/inner() bind outer's own nested defs -> no cross-file helper/inner edge",
          _out(g_on, go) == {} and gk("b_gates", "helper") in _out(g_off, go)
          and gk("b_gates", "inner") in _out(g_off, go), (_out(g_on, go), _out(g_off, go)))
    ainner, ahelper = ak1("a_gates", "inner", 31), ak1("a_gates", "helper", 28)
    check("ast inner(): helper() is a closure over outer's helper -> that node only, INFERRED",
          ahelper and _out(a_on, ainner) == {ahelper: "INFERRED"}, _out(a_on, ainner))
    check("ast outer(): inner() binds outer's own nested inner (not b_gates.inner)",
          ainner and _out(a_on, ak1("a_gates", "outer")).get(ainner) == "INFERRED"
          and ak1("b_gates", "inner") not in _out(a_on, ak1("a_gates", "outer")), _out(a_on, ak1("a_gates", "outer")))
    for caller in ("gate_assign", "gate_param"):
        ck = gk("a_gates", caller)
        check(f"graphify {caller}: the name is a local VALUE (assignment / parameter) -> ON == OFF",
              _out(g_on, ck) == _out(g_off, ck) and len(_out(g_off, ck)) > 1, (_out(g_on, ck), _out(g_off, ck)))
        ack = ak1("a_gates", caller)
        check(f"ast {caller}: ON == OFF", _out(a_on, ack) == _out(a_off, ack), (_out(a_on, ack), _out(a_off, ack)))
    ck = gk("e_global", "gate_glob")
    check("graphify e_global: `global check` elsewhere may rebind the global -> ON == OFF",
          _out(g_on, ck) == _out(g_off, ck) and len(_out(g_off, ck)) > 1, (_out(g_on, ck), _out(g_off, ck)))

    print("\n-- E: a module that does NOT define the name keeps today's behaviour --")
    ck = gk("a_gates", "gate_nomod")
    check("graphify gate_nomod: verify() (defined only in b_gates) -> ON == OFF (INFERRED to b_gates.verify)",
          _out(g_on, ck) == _out(g_off, ck) == {gk("b_gates", "verify"): "INFERRED"}, (_out(g_on, ck), _out(g_off, ck)))
    ack = ak1("a_gates", "gate_nomod")
    check("ast gate_nomod: ON == OFF", _out(a_on, ack) == _out(a_off, ack) and _out(a_off, ack),
          (_out(a_on, ack), _out(a_off, ack)))

    print("\n-- F: edge cases --")
    g1 = gk("a_gates", "gate_one")
    check("graphify gate_one: binds ONLY a_gates.check, INFERRED (OFF: every check + Fake/K methods)",
          _out(g_on, g1) == {gk("a_gates", "check"): "INFERRED"} and len(_out(g_off, g1)) >= 8,
          (_out(g_on, g1), len(_out(g_off, g1))))
    ck = gk("a_gates", "gate_mixed")
    check("graphify gate_mixed: bare check() + other.check() in one body -> ON == OFF",
          _out(g_on, ck) == _out(g_off, ck) and len(_out(g_off, ck)) > 1, (_out(g_on, ck), _out(g_off, ck)))
    ack = ak1("a_gates", "gate_mixed")
    check("ast gate_mixed: ON == OFF", _out(a_on, ack) == _out(a_off, ack), (_out(a_on, ack), _out(a_off, ack)))
    ck = gk("a_gates", "gate_rec")
    check("graphify gate_rec: recursion binds itself -> no edge to b_gates.gate_rec (OFF had it, T3#6 AMBIGUOUS)",
          _out(g_on, ck) == {} and _out(g_off, ck) == {gk("b_gates", "gate_rec"): "AMBIGUOUS"},
          (_out(g_on, ck), _out(g_off, ck)))
    ack = ak1("a_gates", "gate_rec")
    check("ast gate_rec: no edge (a self-call is never an edge)", _out(a_on, ack) == {} and _out(a_off, ack),
          (_out(a_on, ack), _out(a_off, ack)))
    for f, caller, why in (("f_star", "gate_star", "a star import may rebind it"),
                           ("g_broken", "gate_broken", "the file does not parse"),
                           ("h_modlevel", "h_modlevel", "a module-level call runs BEFORE the def")):
        ck = gk(f, caller)
        check(f"graphify {f}: {why} -> ON == OFF", _out(g_on, ck) == _out(g_off, ck) and len(_out(g_off, ck)) > 1,
              (_out(g_on, ck), _out(g_off, ck)))
    ck = gk("i_modlevel", "i_modlevel")
    check("graphify i_modlevel (Module caller, call after the def): binds its own check only",
          _out(g_on, ck) == {gk("i_modlevel", "check"): "INFERRED"}, _out(g_on, ck))
    fr = gk("a_gates", "Fake.run")
    check("graphify Fake.run: a bare check() in a method skips the class scope -> a_gates.check only "
          "(not Fake.check, not other files)", _out(g_on, fr) == {gk("a_gates", "check"): "INFERRED"}, _out(g_on, fr))
    afr = ak1("a_gates", "Fake.run")
    check("ast Fake.run: a_gates.check only, INFERRED",
          _out(a_on, afr) == {ak1("a_gates", "check"): "INFERRED"}, _out(a_on, afr))
    check("graphify class K body: check(None) below K.check binds K.check (the class namespace), INFERRED",
          _out(g_on, gk("j_class", "K")) == {gk("j_class", "K.check"): "INFERRED"}, _out(g_on, gk("j_class", "K")))
    check("graphify K.m: a method's bare check() -> the module's check",
          _out(g_on, gk("j_class", "K.m")) == {gk("j_class", "check"): "INFERRED"}, _out(g_on, gk("j_class", "K.m")))
    check("graphify class L body: check('l') -> the module's check",
          _out(g_on, gk("j_class", "L")) == {gk("j_class", "check"): "INFERRED"}, _out(g_on, gk("j_class", "L")))
    check("ON only ever REMOVES edges or keeps a same-file one: every ON edge that is not an OFF edge is none",
          set(g_on) <= set(g_off) and set(a_on) <= set(a_off), (set(g_on) - set(g_off), set(a_on) - set(a_off)))
    # an engine id collision: graphify gives `_main` and `main` ONE id, so the node named `_main` carries
    # main's calls too — main's `other.check(...)` really may reach another file's check
    with tempfile.TemporaryDirectory() as td:
        root = _mixed_root(td)
        _write(root, "tests/k_collide.py", "def check(name, cond):\n    return cond\n\n\n"
                                           "def _main():\n    check('a', 1)\n\n\n"
                                           "def main(other):\n    helper_only_in_main()\n"
                                           "    return other.check('b', 2)\n")
        cn = MIXED_G_NODES + [_g("tests/k_collide.py", "check", 1), _n("tests_k_collide_main", "_main",
                                                                         "tests/k_collide.py", 5)]
        cc = [("tests_k_collide_main", "check"), ("tests_k_collide_main", "helper_only_in_main")]
        c_off = _resolve(S, root, cn, cc)[0]
        c_on, c_nodes = _resolve(S, root, cn, cc, DEEPMAP_LOCAL_DEF_CALLS="1")
    check("an engine id collision (a callee with no call site in the def's source) -> the caller is not judged",
          c_on == c_off and len(c_off) > 1 and not any(n.get("call_local") for n in c_nodes), (c_on, c_off))
    # the source defines check at module level but the graph has no node for it (an engine miss): the
    # source and the graph disagree -> keep today's edges rather than drop every target
    with tempfile.TemporaryDirectory() as td:
        root = _mixed_root(td)
        _write(root, "tests/m_nonode.py", "def check(name, cond):\n    return cond\n\n\ndef gate_m():\n"
                                          "    return check('m', 1)\n")
        cn = MIXED_G_NODES + [_g("tests/m_nonode.py", "gate_m", 5)]
        cc = [(_g("tests/m_nonode.py", "gate_m", 5)["key"], "check")]
        m_off = _resolve(S, root, cn, cc)[0]
        m_on = _resolve(S, root, cn, cc, DEEPMAP_LOCAL_DEF_CALLS="1")[0]
    check("a module-level def with NO node in the graph -> today's fan-out kept (never an empty result by accident)",
          m_on == m_off and len(m_off) > 1, (m_on, m_off))
    files = {n["key"]: n["source_file"] for n in g_nodes}
    decided = {n["key"] for n in g_nodes if n.get("call_local")}
    check("every edge of a DECIDED caller is same-file under ON",
          all(files[a] == files[b] for (a, b) in g_on if a in decided) and decided, sorted(decided))


# =====================================================================================================
# G — DEEPMAP_PRECISE_CALLS
# =====================================================================================================
def gate_g_precise() -> None:
    print("\n-- G: DEEPMAP_PRECISE_CALLS — a key bound to another file is re-bound to the local def --")
    S = _load()
    with tempfile.TemporaryDirectory() as td:
        root = _mixed_root(td)
        off = _resolve(S, root, MIXED_G_NODES, MIXED_P_CALLS, DEEPMAP_PRECISE_CALLS="1")[0]
        on = _resolve(S, root, MIXED_G_NODES, MIXED_P_CALLS, DEEPMAP_PRECISE_CALLS="1", DEEPMAP_LOCAL_DEF_CALLS="1")[0]
    g1 = gk("a_gates", "gate_one")
    check("precise OFF (today): gate_one -> b_gates.check (the engine's cross-file key)",
          gk("b_gates", "check") in _out(off, g1), _out(off, g1))
    check("precise ON: gate_one -> a_gates.check only, EXTRACTED (F3b: a literal same-file binding)",
          _out(on, g1) == {gk("a_gates", "check"): "EXTRACTED"}, _out(on, g1))
    for f, caller in (("b_gates", "gate_two"), ("c_import", "gate_imp"), ("a_gates", "gate_mixed"),
                      ("a_gates", "gate_nomod")):
        ck = gk(f, caller)
        check(f"precise {f}.{caller}: unchanged (same-file key / imported / mixed / not defined locally)",
              _out(on, ck) == _out(off, ck) and _out(off, ck), (_out(on, ck), _out(off, ck)))
    fr = gk("a_gates", "Fake.run")
    check("precise Fake.run: the engine bound a BARE check() to its own class's method (off: kept) -> "
          "re-bound to the module's check, EXTRACTED (a bare call never reaches a method)",
          _out(off, fr) == {gk("a_gates", "Fake.check"): "EXTRACTED"}
          and _out(on, fr) == {gk("a_gates", "check"): "EXTRACTED"}, (_out(on, fr), _out(off, fr)))
    with tempfile.TemporaryDirectory() as td:
        root = _mixed_root(td)
        _write(root, "tests/k_self.py", "class Svc:\n    def check(self):\n        return 1\n\n"
                                        "    def run(self):\n        return self.check()\n\n\n"
                                        "def check():\n    return 0\n")
        nodes = MIXED_G_NODES + [_g("tests/k_self.py", "Svc", 1, "Class"),
                                 _g("tests/k_self.py", "Svc.check", 2, "Method"),
                                 _g("tests/k_self.py", "Svc.run", 5, "Method"), _g("tests/k_self.py", "check", 9)]
        pc = [(_g("tests/k_self.py", "Svc.run", 5)["key"], _key(_g("tests/k_self.py", "Svc.check", 2)["key"]))]
        on2 = _resolve(S, root, nodes, pc, DEEPMAP_PRECISE_CALLS="1", DEEPMAP_LOCAL_DEF_CALLS="1")[0]
    check("precise: `self.check()` (qualified) keeps the engine's Svc.check key (EXTRACTED)",
          on2 == {pc[0]: "EXTRACTED"}, on2)


# =====================================================================================================
# H — DEEPMAP_FALSE_CALL_FILTER interplay
# =====================================================================================================
def gate_h_fcf() -> None:
    print("\n-- H: DEEPMAP_FALSE_CALL_FILTER — composes with J3, does not veto this opt-in --")
    S = _load()
    with tempfile.TemporaryDirectory() as td:
        root = _mixed_root(td)
        on = _resolve(S, root, MIXED_G_NODES, MIXED_G_CALLS, DEEPMAP_LOCAL_DEF_CALLS="1")[0]
        on0 = _resolve(S, root, MIXED_G_NODES, MIXED_G_CALLS, DEEPMAP_LOCAL_DEF_CALLS="1",
                       DEEPMAP_FALSE_CALL_FILTER="0")[0]
        off0 = _resolve(S, root, MIXED_G_NODES, MIXED_G_CALLS, DEEPMAP_FALSE_CALL_FILTER="0")[0]
        off = _resolve(S, root, MIXED_G_NODES, MIXED_G_CALLS)[0]
    g1 = gk("a_gates", "gate_one")
    check("default (J3 on) + OFF: gate_one no longer binds the methods, still fans out to every check",
          gk("a_gates", "Fake.check") not in _out(off, g1) and gk("b_gates", "check") in _out(off, g1), _out(off, g1))
    check("FCF=0 + OFF: gate_one keeps the round-7 fan-out incl. the methods Fake.check / K.check",
          gk("a_gates", "Fake.check") in _out(off0, g1) and gk("j_class", "K.check") in _out(off0, g1), _out(off0, g1))
    check("FCF=0 + ON: the local rule still applies (a separate opt-in) -> a_gates.check only",
          _out(on0, g1) == {gk("a_gates", "check"): "INFERRED"}, _out(on0, g1))
    und = (("a_gates", "gate_mixed"), ("c_import", "gate_imp"), ("a_gates", "gate_assign"), ("f_star", "gate_star"))
    check("default (J3 on) + ON: undecided callers keep J3's result exactly",
          all(_out(on, gk(f, c)) == _out(off, gk(f, c)) for f, c in und),
          [(c, _out(on, gk(f, c)), _out(off, gk(f, c))) for f, c in und if _out(on, gk(f, c)) != _out(off, gk(f, c))])
    check("FCF=0 + ON: undecided callers keep the round-7 fan-out exactly",
          all(_out(on0, gk(f, c)) == _out(off0, gk(f, c)) for f, c in (("a_gates", "gate_mixed"),
                                                                        ("c_import", "gate_imp"),
                                                                        ("a_gates", "gate_assign"))),
          [(c, _out(on0, gk(f, c)), _out(off0, gk(f, c))) for f, c in (("a_gates", "gate_mixed"),)])


# =====================================================================================================
# I — sync() wiring
# =====================================================================================================
class _Res:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def __iter__(self):
        return iter(self._rows)

    def single(self):
        return self._rows[0] if self._rows else None

    def consume(self):
        return None

    def data(self):
        return [dict(r) for r in self._rows]


class _Sess:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, cypher, **p):
        return _Res()


class _Drv:
    def session(self, *a, **k):
        return _Sess()

    def verify_connectivity(self):
        return None

    def close(self):
        return None


def _sync_once(flag):
    S = _load()
    got: dict = {}
    tree, cn, cc = _copies(6)
    Ext = getattr(S, "_Extraction")

    def _push(driver, n, c, i, typed_edges=None):
        got["edges"] = S._resolve_calls(n, c)
        return (len(n), len(got["edges"]))

    S._rebuild_code = lambda root: Ext([dict(x) for x in cn], list(cc), [], "graphify",
                                       {"requested": "auto", "resolved": "graphify", "fallback_reason": None})
    S._apply_recognizers = lambda root, ns: (ns, [])
    S.push_to_neo4j = _push
    S._graph_has_nodes = lambda d: False
    S._reuse_rekeyed_embeddings = lambda d, ns: 0
    S.indexes = SimpleNamespace(create_all_indexes=lambda d: None, wait_for_indexes=lambda d, timeout=120: True)
    S.embeddings = SimpleNamespace(generate_embeddings=lambda d: 0)
    S._count_embedded_nodes = lambda d, fallback=0: len(cn)
    S._maybe_auto_tune = lambda root: None
    with tempfile.TemporaryDirectory() as td, _env(**dict(_BASE_ENV, NEO4J_URI="bolt://localhost:7999",
                                                          DEEPMAP_HOME=str(Path(td) / "home"), DEEPMAP_EXTRACTOR=None,
                                                          DEEPMAP_LEARNING=None, DEEPMAP_LOCAL_DEF_CALLS=flag)):
        root = Path(td) / "p"
        root.mkdir()
        (root / ".deepmap-root").write_text("", encoding="utf-8")
        for rel, text in tree.items():
            _write(root, rel, text)
        out = io.StringIO()
        with contextlib.redirect_stderr(out), contextlib.redirect_stdout(out):
            ok = S.sync(root, driver=_Drv())
    return ok, got.get("edges") or {}, out.getvalue(), {n["key"]: n["source_file"] for n in cn}


def gate_i_wiring() -> None:
    print("\n-- I: sync() runs the local-definition pass before the push only when the flag is on --")
    ok, edges, log, files = _sync_once("1")
    check("flag ON: sync() pushed (fake driver)", ok and bool(edges), log[-600:])
    check("flag ON: the push saw one own-file edge per gate (12), none cross-file",
          len(edges) == 12 and all(files[a] == files[b] for a, b in edges), sorted(edges)[:6])
    check("flag ON: sync says how many bare calls it bound to the caller's own definition",
          "DEEPMAP_LOCAL_DEF_CALLS" in log and "own definition" in log, log[-900:])
    ok, edges, log, _files = _sync_once(None)
    check("flag OFF: the push saw today's fan-out (12 gates x 6 checks = 72)", ok and len(edges) == 72, len(edges))
    check("flag OFF: no local-definition line in the sync log", "DEEPMAP_LOCAL_DEF_CALLS" not in log, log[-600:])


# =====================================================================================================
# J — the real engine
# =====================================================================================================
def gate_j_real_engine() -> None:
    print("\n-- J: real graphify engine -> map_graph -> push-time resolution --")
    try:
        import graphify  # noqa: F401
    except Exception:
        print("  [SKIP] graphify is not installed in this interpreter — section J not run")
        return
    S = _load()
    tree, _cn, _cc = _copies(4, prefix="tests/r")
    tree["tests/nested_gates.py"] = ("def gate_setup():\n"
                                     "    def _rec(x):\n"
                                     "        return x\n"
                                     "    return _rec(1)\n")
    tree["tests/other_rec.py"] = "def _rec(x):\n    return x\n"
    with tempfile.TemporaryDirectory() as td, _env(**dict(_BASE_ENV, DEEPMAP_HOME=str(Path(td) / "home"),
                                                          DEEPMAP_EXTRACTOR="graphify")):
        root = Path(td) / "probe"
        for rel, text in tree.items():
            _write(root, rel, text)
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            nodes, calls, _imports, engine = S._rebuild_code(root)
        off = _resolve(S, root, nodes, calls)[0]
        on = _resolve(S, root, nodes, calls, DEEPMAP_LOCAL_DEF_CALLS="1")[0]
    file_of = {n["key"]: n["source_file"] for n in nodes}
    name_of = {n["key"]: n["name"] for n in nodes}
    gates = {n["key"] for n in nodes if n["name"].startswith("gate_") and n["source_file"].startswith("tests/r")}
    check("the engine ran and produced the probe's 4 checks + 8 gates",
          engine == "graphify" and len(gates) == 8
          and sum(1 for n in nodes if n["name"] == "check") == 4, (engine, len(nodes)))
    check("real engine OFF: each gate fans out to all 4 checks",
          all(sum(1 for (a, b) in off if a == g and name_of[b] == "check") == 4 for g in gates), len(off))
    check("real engine ON: each gate binds only its own file's check, INFERRED",
          all(_out(on, g) == {b: "INFERRED" for (a, b) in off if a == g and file_of[b] == file_of[g]
                              and name_of[b] == "check"} for g in gates),
          {g: _out(on, g) for g in sorted(gates)[:2]})
    setup = next((n["key"] for n in nodes if n["name"] == "gate_setup"), None)
    check("real engine ON: gate_setup's nested _rec shadows -> no edge to other_rec.py's _rec (OFF had it)",
          setup is not None and _out(on, setup) == {} and len(_out(off, setup)) == 1,
          (_out(on, setup), _out(off, setup)))


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    if "--print-golden" in sys.argv:
        print(json.dumps(_off_digests(_load()), indent=4))
        return 0
    print("DeepMap — DEEPMAP_LOCAL_DEF_CALLS gates (bare Python call -> the caller's own definition)")
    for gate in (gate_a_off_identity, gate_b_copies, gate_cdef_mixed, gate_g_precise, gate_h_fcf,
                 gate_i_wiring, gate_j_real_engine):
        try:
            gate()
        except Exception as exc:  # noqa: BLE001 — a crash is a failure, not a skip
            check(f"{gate.__name__} ran without raising", False, f"{type(exc).__name__}: {exc}")
    total = _PASS + _FAIL
    print(f"\nRESULT: {'GREEN' if _FAIL == 0 else 'RED'}  {_PASS}/{total} checks passed")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
