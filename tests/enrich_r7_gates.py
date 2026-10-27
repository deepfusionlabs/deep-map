#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Round-7 gates for `deepmap enrich` (owner W3: build/enrich.py). RED-first, hermetic, no LLM, no spend.

Locks (the sweep's "Make enrich --deterministic honest and more useful", PARTIAL):
  P7  enrichment PRIORITY puts production code before test code: a test helper with a high name in-degree
      (fixtures are called everywhere) no longer takes the --max-nodes budget ahead of the product. Test paths
      use sync._is_test_path's rule (directory component / filename convention — never a substring, so
      src/contest.py and Latest.java are production). Among production (or among test) nodes the old order
      (public, Route, in-degree, file, line) is unchanged.
  C7  the doc COMMENT above an undocumented PYTHON def (a `#` block, decorators in between skipped) is
      harvested by `enrich --deterministic`, like the //, ///, /** */ and # comments of other languages already
      were; tooling / separator / encoding comments and a comment cut off by a blank line are not. A def with
      no comment still gets no invented text (unchanged, nothing written).

Hermetic: temp project dirs, DEEPMAP_EXTRACTOR=ast for the end-to-end run (no graphify subprocess), no Neo4j,
no network; never touches ~/.deepmap.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build"

_PASS = 0
_FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {label}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}{(' — ' + str(detail)[:500]) if detail else ''}")


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _node(name, sf, line=1, label="Function"):
    return {"name": name, "source_file": sf, "start_line": line, "label": label}


def gate_priority(E) -> None:
    print("\n-- P7: production code is enriched before test code --")
    prod = _node("settle", "pkg/billing.py", 10)
    test_helper = _node("make_invoice", "a_tests/test_helpers.py", 3)      # sorts BEFORE pkg/ by path
    fixture = _node("client", "tests/conftest.py", 1)
    junit = _node("InvoiceTest.charges", "src/test/java/InvoiceTest.java", 5, "Method")
    in_deg = {"make_invoice": 40, "client": 90, "charges": 7, "settle": 0}
    got = sorted([test_helper, fixture, junit, prod], key=lambda n: E._priority(n, in_deg))
    check("a production def with in-degree 0 comes before test helpers with in-degree 40/90",
          got[0] is prod, [n["name"] for n in got])
    check("... and every test node follows every production node", all(n is not prod for n in got[1:]),
          [n["name"] for n in got])
    lookalike = [_node("rank", "src/contest.py", 1), _node("Latest.build", "src/Latest.java", 1, "Method")]
    got = sorted(lookalike + [test_helper], key=lambda n: E._priority(n, {"make_invoice": 99}))
    check("src/contest.py and Latest.java are production (no substring match), so they precede the test helper",
          [n["name"] for n in got][-1] == "make_invoice", [n["name"] for n in got])
    a, b = _node("_private", "pkg/a.py", 1), _node("public", "pkg/z.py", 1)
    r = _node("route_x", "pkg/m.py", 1, "Route")
    got = sorted([a, b, r], key=lambda n: E._priority(n, {"public": 1, "route_x": 0, "_private": 50}))
    check("control: among production nodes the old order holds (public before _private)",
          [n["name"] for n in got] == ["route_x", "public", "_private"], [n["name"] for n in got])


_PY_SRC = '''import functools

# Compute the settlement fee for a merchant batch, rounding half-up.
@functools.lru_cache(maxsize=None)
def fee(batch):
    return round(sum(batch) * 0.02, 2)


# pylint: disable=too-many-arguments
def tooling_only(a, b, c, d, e, f):
    return a


# ---- section: helpers -----------------------------------------------------
def separated(x):
    return x * 2

# Cut off from the def below by a blank line, so it is not its doc comment.

def detached(y):
    return y + 1


def bare(z):
    return z - 1


# old = compute_fee(batch)
def commented_code(batch):
    return batch
'''


def gate_python_comment(E, work: Path) -> None:
    print("\n-- C7: the # comment ABOVE an undocumented Python def is harvested (deterministic tier) --")
    proj = work / "pyc"
    proj.mkdir()
    (proj / "billing.py").write_text(_PY_SRC, encoding="utf-8")
    lines = _PY_SRC.splitlines()

    def _line_of(prefix):
        return next(i for i, ln in enumerate(lines, start=1) if ln.startswith(prefix))
    cache: dict = {}
    got = E._leading_comment_for(proj, _node("fee", "billing.py", _line_of("def fee")), cache)
    check("fee (decorated; start_line = the def line): its doc comment is found above the decorator",
          got.startswith("Compute the settlement fee"), repr(got))
    got = E._leading_comment_for(proj, _node("fee", "billing.py", _line_of("@functools")), cache)
    check("fee (start_line = the decorator line, graphify's convention): the same comment",
          got.startswith("Compute the settlement fee"), repr(got))
    for name, prefix in (("tooling_only", "def tooling_only"), ("separated", "def separated"),
                         ("detached", "def detached"), ("bare", "def bare"),
                         ("commented_code", "def commented_code")):
        got = E._leading_comment_for(proj, _node(name, "billing.py", _line_of(prefix)), cache)
        check(f"{name}: no doc comment harvested (tooling / separator / blank-line cut / none / commented-out code)",
              got == "", repr(got))
    check("a Module node still gets no leading comment (whole-file node)",
          E._leading_comment_for(proj, _node("billing", "billing.py", 1, "Module"), cache) == "")
    import ast
    tree = ast.parse(_PY_SRC)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "fee")
    summ = E.deterministic_rich_summary("def fee(batch): ...", fn, "fee", "Function",
                                        leading_comment="Compute the settlement fee for a merchant batch, "
                                                        "rounding half-up.")
    check("deterministic_rich_summary carries a Python def's leading comment (it was ignored for ast nodes)",
          "settlement fee" in summ, summ)
    check("... alongside the decorator it already harvested", "lru_cache" in summ, summ)

    # end to end: `enrich --deterministic` on a real (ast-extracted) project writes it to the sidecar
    saved = {k: os.environ.get(k) for k in ("DEEPMAP_EXTRACTOR", "DEEPMAP_ENRICH")}
    os.environ["DEEPMAP_EXTRACTOR"] = "ast"
    os.environ.pop("DEEPMAP_ENRICH", None)
    try:
        stats = E.enrich(proj, deterministic=True)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    side = proj / ".deepmap" / "summaries.jsonl"
    rows = [json.loads(ln) for ln in side.read_text(encoding="utf-8").splitlines() if ln.strip()] \
        if side.exists() else []
    check("enrich --deterministic writes fee's summary WITH its doc comment",
          any("settlement fee" in r.get("summary", "") for r in rows), json.dumps(rows)[:400])
    check("... bare (no comment, no other local signal) is not written (no invented text)",
          not any("bare" in r.get("node_id", "") for r in rows), [r.get("node_id") for r in rows])
    check("... and the run reports honest counts (enriched >= 1, nothing crashed)",
          isinstance(stats, dict) and stats.get("enriched", 0) >= 1, str(stats))


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 72)
    print("DeepMap — enrich round-7 gates (W3: test-last priority, Python doc-comment harvest)")
    print("=" * 72)
    saved_home = os.environ.get("DEEPMAP_HOME")
    with tempfile.TemporaryDirectory() as td:
        base = Path(td).resolve()
        (base / "home").mkdir()
        work = base / "work"
        work.mkdir()
        os.environ["DEEPMAP_HOME"] = str(base / "home")
        try:
            if str(BUILD) not in sys.path:
                sys.path.insert(0, str(BUILD))
            E = _load_module(BUILD / "enrich.py", f"dm_enrich_r7_{time.time_ns()}")
            for gate in (lambda: gate_priority(E), lambda: gate_python_comment(E, work)):
                try:
                    gate()
                except Exception as exc:  # noqa: BLE001 — a crashing gate is a RED gate
                    import traceback
                    traceback.print_exc()
                    check(f"gate ran without crashing ({type(exc).__name__}: {exc})", False)
        finally:
            if saved_home is None:
                os.environ.pop("DEEPMAP_HOME", None)
            else:
                os.environ["DEEPMAP_HOME"] = saved_home
    print("\n" + "=" * 72)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
