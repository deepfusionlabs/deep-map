#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j gates for the OPT-IN test-directory exclusion (build/sync.py).

`DEEPMAP_EXCLUDE_TESTS=1` makes extraction drop test files (test dirs + filename conventions) so
retrieval favors library code over test code — a cross-language mitigation for the "search returns
tests, not lib" pollution seen on large repos (e.g. express). It is DEFAULT-OFF and, when off, a
strict no-op: `_maybe_exclude_tests` returns the SAME objects, so the STAGE-0 Python oracle build is
byte-identical (the frozen retriever is untouched — this is an extraction-scope knob only).

These gates prove, with no Neo4j / network / graphify run:
  * `_is_test_path` is COMPONENT-anchored (never a substring): `test/`, `tests/`, `__tests__/`,
    `spec/` dirs + `test_*.py` / `*_test.{py,go,rb,rs}` / `*.test.{ts,tsx,js,...}` / `*.spec.*` /
    `*_spec.rb` / `*Test(s).java` match; `contest.py` / `latest.js` / `attest.rb` / `src/foo.py` do NOT;
    a .NET test-project directory (`src/Shop.Tests/`, `Shop.UnitTests/`, ... a component ENDING with a dotted
    test suffix) matches; `src/Contest/`, `Shop.Latest/`, `Shop.Testing.Abstractions/` do NOT;
  * OFF (env unset) -> `_maybe_exclude_tests` returns the identical objects (byte-identity invariant);
  * ON -> test nodes drop, calls FROM a test node drop, and a callee defined ONLY in a dropped test
    node fails to resolve via edge_resolve.build_name_index(kept) -> the CALLS graph auto-prunes with
    NO corruption of surviving edges; test-authored imports drop too.
RED-first: the helpers don't exist until Part A lands, so this file is RED before the feature.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

BUILD = Path(__file__).resolve().parent.parent / "build"
sys.path.insert(0, str(BUILD))


def _load(mod: str, path: Path):
    spec = importlib.util.spec_from_file_location(mod, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


S = _load("dm_sync_excl", BUILD / "sync.py")
ER = _load("dm_edge_resolve_excl", BUILD / "edge_resolve.py")

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


def _node(key: str, sf: str, name: str, label: str = "Function") -> dict:
    return dict(key=key, label=label, name=name, source_file=sf, start_line=1,
                documented=False, body_summary="")


def gate_is_test_path() -> None:
    print("\n-- _is_test_path (component-anchored, never substring) --")
    should = [
        "tests/test_svc.py", "test/helpers.py", "pkg/__tests__/a.js", "src/spec/x.rb",
        "a/test_thing.py", "svc_test.go", "router_test.rs", "queue.test.ts", "queue.test.tsx",
        "schema.spec.js", "model_spec.rb", "com/app/RouterTest.java", "com/app/RouterTests.java",
        "e2e/flow.ts", "pkg/testing/util.go", "__mocks__/fs.js",
        # The .NET test-project convention (2026-10, review cs-test-fake-dotnet-layout): a test project sits
        # BESIDE the code it tests, so a typed C# call through an interface saw its fakes as production
        # implementers. A directory component ENDING with .Tests / .Test / .UnitTests / .UnitTest /
        # .IntegrationTests / .IntegrationTest / .FunctionalTests / .AcceptanceTests / .Specs is test code, at any
        # depth, in any letter case, with either separator.
        "src/Shop.Tests/X.cs", "src/Shop.Tests/RepoTests.cs", "src/Shop.UnitTests/Fakes/FakeRepo.cs",
        "Shop.Tests/FakeRepo.cs", "src/Shop.Test/X.cs", "src/Shop.UnitTest/X.cs",
        "src/Shop.IntegrationTests/X.cs", "src/Shop.IntegrationTest/X.cs", "src/Shop.FunctionalTests/Fake.cs",
        "src/Shop.AcceptanceTests/X.cs", "src/Shop.Specs/Steps/X.cs", "SRC/SHOP.TESTS/X.CS",
        "src\\Shop.Api.Tests\\Controllers\\X.cs", "tools/Shop.Tests/nested/deep/X.ts",
    ]
    shouldnt = [
        "src/contest.py", "lib/latest.js", "pkg/attest.rb", "src/requests/sessions.py",
        "fastapi/routing.py", "router.go", "src/de.rs", "packages/zod/src/v4/core/parse.ts",
        "src/testament.py", "contestant.go", "manifesto.java", "greatest.ts",
        # PascalCase Java classes that a well-meaning "lowercase this rule for consistency" refactor
        # would silently reclassify as tests and DELETE from the graph. The JUnit rule is
        # case-SENSITIVE on purpose (the capital T is the only anchor it has) — these lock that
        # intent, which the JS/TS/Ruby entries above did not cover. (2026-08 sweep: a proposed
        # case-insensitivity fix here was verified to match all five and was reverted.)
        "com/app/Latest.java", "com/app/Contest.java", "com/app/Protest.java",
        "com/app/Attest.java", "com/app/Greatest.java",
        # The .NET suffix rule is dot-anchored and directory-only: a name that merely ends with the letters, a
        # suffix in the middle of a name, and a FILE named like a test project are production. Decided:
        # Shop.Testing.Abstractions is production too. Its last segment names a library, and a repository that
        # authors a *.Testing / *.Testing.* project ships it as a package (test-support API other projects
        # consume), so DEEPMAP_EXCLUDE_TESTS must not drop it and a production caller may dispatch into it.
        "src/Contest/X.cs", "src/Latest.cs", "src/Shop.Testing.Abstractions/X.cs", "src/Shop.Contests/X.cs",
        "src/Shop.Latest/X.cs", "src/Shop.Attest/X.cs", "src/My.Protests/X.cs", "src/Shop.TestsHelpers/X.cs",
        "src/Shop.Tests.cs", "Shop.Tests", "src/Shop.Testing/X.cs", "src/Shop.Spec/X.cs",
        "src/Shop.Tests.Common.Abstractions/X.cs",
    ]
    for sf in should:
        check(f"test  {sf!r}", S._is_test_path(sf) is True)
    for sf in shouldnt:
        check(f"keep  {sf!r}", S._is_test_path(sf) is False)


def gate_off_is_noop() -> None:
    print("\n-- OFF (default): byte-identity no-op (STAGE-0 oracle safe) --")
    os.environ.pop("DEEPMAP_EXCLUDE_TESTS", None)
    nodes = [_node("src/x.py::a", "src/x.py", "a")]
    calls = [("src/x.py::a", "b")]
    imports = [("src/x.py", "os")]
    rn, rc, ri = S._maybe_exclude_tests(nodes, calls, imports)
    check("disabled -> _exclude_tests_enabled() is False", S._exclude_tests_enabled() is False)
    check("disabled -> returns the SAME nodes object", rn is nodes)
    check("disabled -> returns the SAME calls object", rc is calls)
    check("disabled -> returns the SAME imports object", ri is imports)


def gate_on_drops_and_autoprunes() -> None:
    print("\n-- ON: drop test nodes; callee auto-prune with no CALLS corruption --")
    os.environ["DEEPMAP_EXCLUDE_TESTS"] = "1"
    try:
        real = _node("src/svc.py::place", "src/svc.py", "place")
        helper = _node("src/util.py::helper", "src/util.py", "helper")
        tnode = _node("tests/test_svc.py::t", "tests/test_svc.py", "onlyintest")
        nodes = [real, helper, tnode]
        # a call FROM the test node, a real->real call, and a real->test-only-name call
        calls = [("tests/test_svc.py::t", "place"),        # from test -> dropped
                 ("src/svc.py::place", "helper"),          # real -> real, kept + resolves
                 ("src/svc.py::place", "onlyintest")]      # real -> name only defined in the dropped test
        imports = [("tests/test_svc.py", "os"),            # ast-style: src is a test relpath -> dropped
                   ("src/svc.py", "sys")]                  # real import -> kept
        rn, rc, ri = S._maybe_exclude_tests(nodes, calls, imports)
        keys = {n["key"] for n in rn}
        check("enabled -> _exclude_tests_enabled() is True", S._exclude_tests_enabled() is True)
        check("test node dropped", "tests/test_svc.py::t" not in keys and len(rn) == 2)
        check("call FROM test dropped", ("tests/test_svc.py::t", "place") not in rc)
        check("real->real call kept", ("src/svc.py::place", "helper") in rc)
        # the real->onlyintest call tuple survives (caller kept) BUT auto-drops at push because the
        # only node defining 'onlyintest' was excluded -> build_name_index has no such name.
        idx = ER.build_name_index(rn)
        check("callee 'onlyintest' unresolvable after exclusion (auto-prune, no corruption)",
              not idx.get("onlyintest"))
        check("callee 'helper' still resolves", bool(idx.get("helper")))
        check("test-authored import dropped", ("tests/test_svc.py", "os") not in ri)
        check("real import kept", ("src/svc.py", "sys") in ri)
    finally:
        os.environ.pop("DEEPMAP_EXCLUDE_TESTS", None)


def gate_on_no_tests_unchanged() -> None:
    print("\n-- ON but no test files: unchanged (same objects) --")
    os.environ["DEEPMAP_EXCLUDE_TESTS"] = "1"
    try:
        nodes = [_node("src/x.py::a", "src/x.py", "a")]
        calls = [("src/x.py::a", "b")]
        imports = [("src/x.py", "os")]
        rn, rc, ri = S._maybe_exclude_tests(nodes, calls, imports)
        check("no test nodes -> returns same objects", rn is nodes and rc is calls and ri is imports)
    finally:
        os.environ.pop("DEEPMAP_EXCLUDE_TESTS", None)


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 72)
    print("DeepMap — opt-in test-exclusion gates (no Neo4j; DEEPMAP_EXCLUDE_TESTS)")
    print("=" * 72)
    gate_is_test_path()
    gate_off_is_noop()
    gate_on_drops_and_autoprunes()
    gate_on_no_tests_unchanged()
    print("\n" + "=" * 72)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
