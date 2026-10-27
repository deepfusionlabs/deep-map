#!/usr/bin/env python3
"""Gates for build/verdict.py — the honest setup verdict (P1/P4/P5). Pure: no Neo4j, no network.

Locks the classification contract that fixes the 2026-07-05 dogfooding findings:
  - missing model (embedded=0)      -> DEGRADED with a LOUD "VECTOR SEARCH DISABLED" reason (P1)
  - non-Python repo (0 nodes + .cs) -> FAILED with a language-aware message, never silent success (P4)
  - degraded outcomes               -> a distinct DEGRADED verdict that exits 0 (P5; there is no --strict flag —
                                       exit_code() is an unwired helper, tests/o6_verdict_contract_r283_gates.py)
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "build"))
import verdict as V  # noqa: E402

_PASS = _FAIL = 0


def check(desc: str, cond: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    ok = bool(cond)
    _PASS += ok
    _FAIL += (not ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {desc}" + ("" if ok else f"  -- {detail}"))


def gate_classify() -> None:
    print("\n-- classify(): honest verdicts --")

    v, r = V.classify(neo4j_ok=False, n_nodes=0, n_embedded=0)
    check("Neo4j down -> FAILED", v == V.FAILED, v)
    check("Neo4j down reason mentions Neo4j", any("neo4j" in x.lower() for x in r))

    v, r = V.classify(neo4j_ok=True, n_nodes=0, n_embedded=0, census={})
    check("0 nodes + no source -> FAILED", v == V.FAILED, v)

    # P4: the headline fix — C#-only repo must FAIL loudly, not silent-empty success.
    v, r = V.classify(neo4j_ok=True, n_nodes=0, n_embedded=0,
                      census={"C#": 120}, extractor="ast")
    check("0 nodes + C# census + ast -> FAILED", v == V.FAILED, v)
    check("C# 0-node reason names the language + graphify",
          any("C#" in x and "graphify" in x for x in r), r)

    # P1: missing model -> DEGRADED, loud, not silent success.
    v, r = V.classify(neo4j_ok=True, n_nodes=1000, n_embedded=0,
                      census={"Python": 50}, extractor="ast", ml_ok=False)
    check("nodes>0, embedded=0 -> DEGRADED (not READY, not FAILED)", v == V.DEGRADED, v)
    check("embedded=0 reason shouts VECTOR SEARCH DISABLED",
          any("VECTOR SEARCH DISABLED" in x for x in r), r)
    check("embedded=0 + ml_ok False names the ML stack",
          any("ML stack" in x or "ml stack" in x.lower() for x in r), r)

    # Healthy Python repo -> READY, no reasons.
    v, r = V.classify(neo4j_ok=True, n_nodes=1000, n_embedded=1000,
                      census={"Python": 50}, extractor="ast", ml_ok=True, mcp_ok=True)
    check("full Python build -> READY", v == V.READY, v)
    check("READY has no reasons", r == [], r)

    # mcp down -> DEGRADED
    v, r = V.classify(neo4j_ok=True, n_nodes=10, n_embedded=10, census={"Python": 5}, mcp_ok=False)
    check("mcp down -> DEGRADED", v == V.DEGRADED, v)
    check("mcp down reason mentions MCP", any("MCP" in x for x in r))

    # mostly-non-python but has some py + nodes -> DEGRADED coverage hint
    v, r = V.classify(neo4j_ok=True, n_nodes=10, n_embedded=10,
                      census={"Python": 2, "C#": 200}, extractor="ast", ml_ok=True, mcp_ok=True)
    check("mostly-non-python (some py) -> DEGRADED coverage hint", v == V.DEGRADED, v)
    check("coverage hint mentions graphify", any("graphify" in x for x in r))

    # graphify extractor with 0 nodes -> FAILED but NOT the ast-python-only message
    v, r = V.classify(neo4j_ok=True, n_nodes=0, n_embedded=0,
                      census={"C#": 10}, extractor="graphify")
    check("graphify 0 nodes -> FAILED", v == V.FAILED, v)
    check("graphify 0-node reason is generic (not 'ast is Python-only')",
          not any("Python-only" in x for x in r), r)


def gate_exit_code() -> None:
    print("\n-- exit_code() (an unwired helper): non-breaking by default; strict=True is library-only --")
    check("READY -> 0", V.exit_code(V.READY) == 0)
    check("FAILED -> 1 (always)", V.exit_code(V.FAILED) == 1 and V.exit_code(V.FAILED, strict=True) == 1)
    check("DEGRADED -> 0 by default (non-breaking)", V.exit_code(V.DEGRADED) == 0)
    check("DEGRADED -> 3 with strict=True (no --strict flag exists; 3 is `deepmap sync`'s Neo4j-unreachable code)",
          V.exit_code(V.DEGRADED, strict=True) == 3)


def gate_census() -> None:
    print("\n-- extension_census(): counts by language, honors ignores --")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "a.py").write_text("x=1", encoding="utf-8")
        (root / "b.py").write_text("y=2", encoding="utf-8")
        (root / "svc.cs").write_text("class X{}", encoding="utf-8")
        (root / "app.ts").write_text("const z=1", encoding="utf-8")
        (root / "README.md").write_text("# doc", encoding="utf-8")  # not source
        ig = root / "node_modules" / "dep"
        ig.mkdir(parents=True)
        (ig / "lib.js").write_text("//ignored", encoding="utf-8")  # ignored dir
        pc = root / "__pycache__"
        pc.mkdir()
        (pc / "a.cpython-313.pyc").write_bytes(b"\x00")
        c = V.extension_census(root)
        check("counts 2 Python", c.get("Python") == 2, c)
        check("counts 1 C#", c.get("C#") == 1, c)
        check("counts 1 TypeScript", c.get("TypeScript") == 1, c)
        check("ignores node_modules (no JavaScript)", "JavaScript" not in c, c)
        check("markdown is not counted as source", "Markdown" not in c and ".md" not in c)


def gate_format_and_probe() -> None:
    print("\n-- format_block() + ml_stack_ok() --")
    blk = V.format_block(V.DEGRADED, ["reason one"], nodes=10, edges=20, embedded=0)
    check("format_block shows the verdict", "DEGRADED" in blk)
    check("format_block shows counts", "10 nodes" in blk and "0 embedded" in blk)
    check("format_block lists reasons", "reason one" in blk)
    check("ml_stack_ok returns a bool", isinstance(V.ml_stack_ok(), bool))


def gate_selfcheck() -> None:
    print("\n-- self-check: verdicts are distinct + load-bearing --")
    check("READY/DEGRADED/FAILED are distinct", len({V.READY, V.DEGRADED, V.FAILED}) == 3)
    # a healthy build must NOT be classifiable as degraded/failed (guards against a tautology)
    v, _ = V.classify(neo4j_ok=True, n_nodes=5, n_embedded=5, census={"Python": 3},
                      ml_ok=True, mcp_ok=True)
    check("load-bearing: healthy != DEGRADED/FAILED", v == V.READY, v)


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 64)
    print("DeepMap — setup verdict gates (P1/P4/P5, no Neo4j)")
    print("=" * 64)
    gate_classify()
    gate_exit_code()
    gate_census()
    gate_format_and_probe()
    gate_selfcheck()
    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
