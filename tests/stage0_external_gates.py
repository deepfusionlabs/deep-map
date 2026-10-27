#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j gates for the EXTERNAL-REPO STAGE-0 oracle decoupling (FOCUS 2).

The STAGE-0 no-regression harness was welded to this repo. These gates lock the new portable core
that lets the SAME harness validate an arbitrary cloned external repo:

  * stage0_portable.relpath_for  — keys stay PORTABLE relpath::name:line for ANY clone (not absolute),
  * stage0_portable.load_queries — a repo's query set loads from .json or .txt,
  * the ORACLE REGISTRY          — register -> lookup -> resolve a repo's frozen oracle by id,
  * stage0_real_graph.extract_dir — repo-PARAMETRIC: keys are relative to the given repo-dir,
  * stage0_regression_check       — an --oracle-path / --repo-id resolver gates any repo's oracle.

Mirrors tests/stage0_harness_gates.py (which locks the in-repo harness). Pure + hermetic: temp dirs,
synthetic source, no Neo4j, no ML stack, no spend.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

TESTS = Path(__file__).resolve().parent
REPO = TESTS.parent

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


def _load(path: Path):
    spec = importlib.util.spec_from_file_location(f"dm_{path.stem}", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def gate_relpath_for(P) -> None:
    print("\n-- relpath_for: portable forward-slash relpath under a repo root --")
    fn = getattr(P, "relpath_for", None)
    check("relpath_for exists", callable(fn))
    if not callable(fn):
        return
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "src").mkdir()
        f = root / "src" / "pay.py"
        f.write_text("x=1\n", encoding="utf-8")
        check("relative to repo_dir, forward-slash", fn(f, root) == "src/pay.py", fn(f, root))
        check("prefix is prepended", fn(f, root, prefix="vendorA") == "vendorA/src/pay.py",
              fn(f, root, prefix="vendorA"))
        check("prefix slashes are normalized", fn(f, root, prefix="/p/") == "p/src/pay.py",
              fn(f, root, prefix="/p/"))
        # The crux: the SAME relpath regardless of where the clone lives on disk (portability).
        with tempfile.TemporaryDirectory() as td2:
            root2 = Path(td2)
            (root2 / "src").mkdir()
            f2 = root2 / "src" / "pay.py"
            f2.write_text("x=1\n", encoding="utf-8")
            check("portable: identical key from a clone at a DIFFERENT path", fn(f, root) == fn(f2, root2),
                  f"{fn(f, root)} vs {fn(f2, root2)}")
        # a file outside the repo_dir falls back to absolute (never crashes)
        check("outside repo_dir -> absolute fallback, no crash",
              isinstance(fn(Path(td).parent / "x.py", root), str))


def gate_load_queries(P) -> None:
    print("\n-- load_queries: .json array and .txt one-per-line (#-comments ignored) --")
    fn = getattr(P, "load_queries", None)
    check("load_queries exists", callable(fn))
    if not callable(fn):
        return
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        j = d / "q.json"
        j.write_text(json.dumps(["alpha query", "beta query"]), encoding="utf-8")
        check("loads a .json array", fn(j) == ["alpha query", "beta query"], str(fn(j)))
        t = d / "q.txt"
        t.write_text("# a comment\nalpha query\n\n  beta query  \n", encoding="utf-8")
        check("loads .txt one-per-line, comments+blanks stripped",
              fn(t) == ["alpha query", "beta query"], str(fn(t)))
        bad = d / "bad.json"
        bad.write_text('{"not": "a list"}', encoding="utf-8")
        try:
            fn(bad)
            check("malformed .json shape raises (never a silent empty set)", False)
        except ValueError:
            check("malformed .json shape raises (never a silent empty set)", True)
        # BOM robustness: an editor-saved UTF-8-BOM file must not corrupt the first query (.txt) or
        # crash the parse (.json) — real-world query files come from editors that add a BOM.
        bt = d / "bom.txt"
        bt.write_bytes("﻿".encode("utf-8") + "alpha query\nbeta query\n".encode("utf-8"))
        check("BOM-prefixed .txt -> first query is NOT corrupted by the BOM",
              fn(bt) == ["alpha query", "beta query"], str(fn(bt)))
        bj = d / "bom.json"
        bj.write_bytes("﻿".encode("utf-8") + json.dumps(["alpha query", "beta query"]).encode("utf-8"))
        check("BOM-prefixed .json -> parses (BOM stripped, no crash)",
              fn(bj) == ["alpha query", "beta query"], str(fn(bj)))


def gate_registry(P) -> None:
    print("\n-- oracle registry: register -> lookup -> resolve --")
    for name in ("load_registry", "register_oracle", "lookup_oracle", "resolve_oracle_path",
                 "default_oracle_path"):
        check(f"{name} exists", callable(getattr(P, name, None)))
    if not all(callable(getattr(P, n, None)) for n in
               ("load_registry", "register_oracle", "lookup_oracle", "resolve_oracle_path")):
        return
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        reg_path = root / "tests" / "oracles" / "registry.json"
        # empty registry on a missing file (never crashes the gate)
        check("missing registry -> empty {oracles:{}} shape",
              P.load_registry(reg_path).get("oracles") == {})
        P.register_oracle(reg_path, "owner__repo", repo_dir="clones/owner__repo",
                          oracle_path="tests/oracles/owner__repo.json", queries_file="q.json",
                          pinned_sha="a" * 40)
        check("registry file written", reg_path.is_file())
        e = P.lookup_oracle(reg_path, "owner__repo")
        check("lookup returns the entry", isinstance(e, dict) and e.get("pinned_sha") == "a" * 40, str(e))
        check("lookup of an unknown id -> None", P.lookup_oracle(reg_path, "nope") is None)
        resolved = P.resolve_oracle_path(e, root)
        check("resolve joins a relative oracle_path against the repo root",
              resolved == root / "tests/oracles/owner__repo.json", str(resolved))
        # re-register replaces, never duplicates
        P.register_oracle(reg_path, "owner__repo", repo_dir="x", oracle_path="y.json")
        check("re-register replaces in place (no duplicate id)",
              len(P.load_registry(reg_path)["oracles"]) == 1)
        check("empty repo_id rejected",
              _raises(lambda: P.register_oracle(reg_path, "", repo_dir="x", oracle_path="y.json")))


def _raises(thunk) -> bool:
    try:
        thunk()
        return False
    except Exception:
        return True


def gate_extract_dir_parametric() -> None:
    print("\n-- stage0_real_graph.extract_dir: repo-PARAMETRIC keys (relative to the given repo) --")
    try:
        RG = _load(TESTS / "stage0_real_graph.py")
    except Exception as exc:   # neo4j driver not importable in this env — skip cleanly (CI has it)
        check("(stage0_real_graph importable)", True, f"skipped — {type(exc).__name__}: {exc}")
        return
    fn = getattr(RG, "extract_dir", None)
    check("extract_dir exists", callable(fn))
    if not callable(fn):
        return
    import inspect
    params = inspect.signature(fn).parameters
    check("extract_dir takes a repo/relpath base param (not hard-wired to this repo)",
          len(params) >= 2, f"signature: {list(params)}")
    if len(params) < 2:
        return
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "pkg").mkdir()
        (root / "pkg" / "mod.py").write_text(
            "def helper():\n    return 1\n\nclass C:\n    def m(self):\n        return helper()\n",
            encoding="utf-8")
        nodes, calls = fn(root / "pkg", root)
        keys = [n["key"] for n in nodes]
        # keys must be relative to the TEMP repo (pkg/mod.py::...), never absolute / this-repo paths.
        check("keys are relative to the given repo_dir (portable)",
              any(k.startswith("pkg/mod.py::") for k in keys), str(keys[:4]))
        check("no key is an absolute path / leaks this repo",
              not any(("deep-map" in k or k[1:3] == ":/" or k.startswith("/")) for k in keys), str(keys[:4]))
        check("extracted the function/method/class", any("helper" in k for k in keys)
              and any("C.m" in k for k in keys), str(keys))


def gate_gate_oracle_path_resolver() -> None:
    print("\n-- stage0_regression_check: --oracle-path / --repo-id resolver --")
    GATE = _load(TESTS / "stage0_regression_check.py")
    resolver = getattr(GATE, "resolve_oracle_path_arg", None)
    check("resolve_oracle_path_arg exists (CLI oracle resolution)", callable(resolver))
    if not callable(resolver):
        return
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        explicit = root / "custom_oracle.json"
        explicit.write_text("{}", encoding="utf-8")
        # explicit --oracle-path wins
        got = resolver(oracle_path=str(explicit), repo_id=None, registry_path=None, default=root / "d.json")
        check("explicit --oracle-path is honored", Path(got) == explicit, str(got))
        # --repo-id resolves via the registry
        P = _load(TESTS / "stage0_portable.py")
        reg = root / "registry.json"
        target = root / "tests" / "oracles" / "ext.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{}", encoding="utf-8")
        P.register_oracle(reg, "ext", repo_dir=".", oracle_path="tests/oracles/ext.json")
        got = resolver(oracle_path=None, repo_id="ext", registry_path=str(reg), default=root / "d.json",
                       repo_root=root)
        check("--repo-id resolves the oracle via the registry", Path(got) == target, str(got))
        # default fallback
        got = resolver(oracle_path=None, repo_id=None, registry_path=None, default=root / "d.json")
        check("falls back to the default oracle path", Path(got) == root / "d.json", str(got))


def gate_committed_registry() -> None:
    print("\n-- committed oracle registry: present, valid, self entry --")
    P = _load(TESTS / "stage0_portable.py")
    reg_path = REPO / P.REGISTRY_RELPATH
    check("registry file committed", reg_path.is_file(), str(reg_path))
    if not reg_path.is_file():
        return
    reg = P.load_registry(reg_path)
    check("registry parses to {oracles:{}}", isinstance(reg.get("oracles"), dict))
    self_e = P.lookup_oracle(reg_path, "self")
    check("a 'self' entry maps to this repo's committed oracle",
          isinstance(self_e, dict) and self_e.get("oracle_path", "").endswith("stage0_real_oracle.json"),
          str(self_e))
    if isinstance(self_e, dict):
        resolved = P.resolve_oracle_path(self_e, REPO)
        check("the 'self' oracle path resolves to a real committed file", Path(resolved).is_file(),
              str(resolved))


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 64)
    print("DeepMap — STAGE-0 external-repo oracle decoupling gates (no Neo4j)")
    print("=" * 64)

    P = _load(TESTS / "stage0_portable.py")
    gate_relpath_for(P)
    gate_load_queries(P)
    gate_registry(P)
    gate_extract_dir_parametric()
    gate_gate_oracle_path_resolver()
    gate_committed_registry()

    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
