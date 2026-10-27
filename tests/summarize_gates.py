#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j gates for build/summarize.py — richer intent-summaries for UNDOCUMENTED code.

Locks the Phase-3a behavior: when a function/method/class/module has no docstring, DeepMap must
synthesize a summary from code structure (humanized name + params + callees / members) instead of
the old bare ``"{kind} {name}"`` fallback — so the embedder has real intent tokens to match.
Docstrings, when present, still win (best intent signal).

Pure: parses source with ast and calls the summarizer; no Neo4j, no model, no network.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

BUILD = Path(__file__).resolve().parent.parent / "build"
sys.path.insert(0, str(BUILD))
import summarize as S  # noqa: E402

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


def _first_func(src: str):
    tree = ast.parse(src)
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return n
    raise AssertionError("no function in source")


def gate_humanize() -> None:
    print("\n-- humanize --")
    cases = {
        "_weighted_rrf": "weighted rrf",
        "embed_query": "embed query",
        "DeepMapRetriever.search": "deep map retriever search",
        "visit_ClassDef": "visit class def",
        "RRF": "rrf",
        "search": "search",
        "_hydrate": "hydrate",
    }
    for raw, want in cases.items():
        got = S.humanize(raw)
        check(f"humanize({raw!r}) == {want!r}", got == want, f"got {got!r}")


def gate_callable_synthesis() -> None:
    print("\n-- callable synthesis (undocumented) --")
    src = (
        "def _weighted_rrf(rankings, weights, k):\n"
        "    scores = defaultdict(float)\n"
        "    for r in rankings:\n"
        "        fuse_one(r)\n"
        "    return sorted(scores)\n"
    )
    node = _first_func(src)
    summ = S.summarize(node, "_weighted_rrf", "Function")
    check("contains humanized name 'weighted rrf'", "weighted rrf" in summ, summ)
    check("mentions a param (rankings/weights/k)", any(p in summ for p in ("rankings", "weights")), summ)
    check("mentions a meaningful callee (fuse one)", "fuse" in summ, summ)
    check("is NOT the bare fallback", summ != "Function _weighted_rrf" and summ != "function _weighted_rrf", summ)
    # self/cls dropped from params
    m = _first_func("class C:\n def run(self, target, deep):\n  go(target)\n")
    msumm = S.summarize(m, "C.run", "Method")
    check("drops self from params", "self" not in msumm.split("calls")[0], msumm)
    check("keeps real params (target/deep)", "target" in msumm, msumm)


def gate_docstring_wins() -> None:
    print("\n-- docstring precedence --")
    src = 'def f(a):\n    """Fetch the user and cache it.\n\n    More."""\n    return a\n'
    node = _first_func(src)
    summ = S.summarize(node, "f", "Function")
    check("uses docstring first line", summ == "Fetch the user and cache it.", summ)


def gate_class_and_module() -> None:
    print("\n-- class + module synthesis --")
    cls = ast.parse("class PaymentValidator:\n def validate(self): pass\n def _help(self): pass\n").body[0]
    csumm = S.summarize(cls, "PaymentValidator", "Class")
    check("class humanized name", "payment validator" in csumm, csumm)
    check("class lists a method", "validate" in csumm, csumm)

    mod = ast.parse("def alpha(): pass\nclass Beta: pass\n")
    msumm = S.summarize(mod, "my_module", "Module")
    check("module humanized name", "my module" in msumm, msumm)
    check("module lists a definition", "alpha" in msumm or "beta" in msumm, msumm)


def gate_noise_filtered() -> None:
    print("\n-- generic-builtin callees filtered --")
    node = _first_func("def g(x):\n print(len(str(x)))\n return list(x)\n")
    summ = S.summarize(node, "g", "Function")
    # generic builtins shouldn't dominate; with only-noise callees, no 'calls' clause is fine
    check("no generic-builtin noise in callees", not any(w in summ.split("calls")[-1] for w in ("print", "len", "str", "list")) or "calls" not in summ, summ)


def gate_determinism() -> None:
    """The summary (and thus its content hash) must be DETERMINISTIC — same code in, same string
    out, every run and every re-parse — even when calls live in different branches (ast.walk order,
    stable per AST). This is the property the embedder's content-hash gating relies on; it does NOT
    claim source-line order (a call in a later branch may precede one in an earlier branch)."""
    print("\n-- summary determinism (content-hash stability) --")
    src = (
        "def handle(req, db):\n"
        "    if req.ok:\n"
        "        validate(req)\n"
        "    else:\n"
        "        reject(req)\n"
        "    for row in db.rows():\n"
        "        persist(row)\n"
        "    return finish(req)\n"
    )
    a = S.summarize(_first_func(src), "handle", "Function")
    b = S.summarize(_first_func(src), "handle", "Function")   # re-parse -> must match
    check("same source -> identical summary (re-parse stable)", a == b, f"{a!r} != {b!r}")
    node = _first_func(src)
    check("same node summarized twice -> identical", S.summarize(node, "handle", "Function") == a)
    check("real callees present (branch + loop bodies walked)",
          all(c in a for c in ("validate", "reject", "persist", "finish")), a)
    # class + module summaries are deterministic too
    csrc = "class Svc:\n def a(self): pass\n def b(self): pass\n"
    cls = ast.parse(csrc).body[0]
    check("class summary deterministic", S.summarize(cls, "Svc", "Class") == S.summarize(ast.parse(csrc).body[0], "Svc", "Class"))


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 64)
    print("DeepMap — intent-summary synthesis gates (no Neo4j, no model)")
    print("=" * 64)
    gate_humanize()
    gate_callable_synthesis()
    gate_docstring_wins()
    gate_class_and_module()
    gate_noise_filtered()
    gate_determinism()
    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
