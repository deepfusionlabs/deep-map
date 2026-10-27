#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gate: the non-Python enrich span is def-BOUNDED, not a fixed 80-line window (F10e). No Neo4j/spend.

The 2026-07 field audit: for a non-Python node, compute_span() took a FIXED MAX_SPAN_LINES window from
start_line with no boundary, so a SHORT Ruby method's window bled into the FOLLOWING def/class — the
enrichment then summarized (and content-hashed) a neighbor's body, adding churn + noise on an opt-in,
off-by-default feature.

Fix: trim the forward scan at the first SUBSEQUENT line that opens a new definition (def/class/module/
func/...) at the same-or-lower indentation as the start line, still capped at MAX_SPAN_LINES (worst case
== today's behavior). Pure; never raises. This changes the content_hash of already-enriched non-Python
nodes -> a one-time re-enrichment on the next `deepmap enrich`, no graph corruption; the default graph,
ranking, and benchmark are untouched (enrich is opt-in).
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "build"))

import enrich as E  # noqa: E402

_PASS = _FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {label}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}{(' — ' + detail) if detail else ''}")


RUBY = """class Foo
  def short
    1
  end

  def other
    neighbor_secret_token
  end

  def third
    2
  end
end
"""


def gate_span_stops_at_next_def() -> None:
    print("\n-- F10e: a short Ruby method's enrich span does not bleed into the following def --")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "foo.rb").write_text(RUBY, encoding="utf-8")
        cache: dict = {}
        # `def short` is on line 2.
        node = {"source_file": "foo.rb", "start_line": 2, "name": "Foo.short", "label": "Method"}
        span, an = E.compute_span(root, node, cache, heuristic=True)
        check("the span includes the method's OWN body ('short' / '1')",
              "short" in span and "1" in span, repr(span))
        check("the span does NOT bleed into the following def's body ('neighbor_secret_token')",
              "neighbor_secret_token" not in span, repr(span))
        check("the span does not reach the THIRD method either", "third" not in span, repr(span))

        # The LAST method has no following def -> span runs to the cap / EOF (still bounded, never raises).
        node_last = {"source_file": "foo.rb", "start_line": 10, "name": "Foo.third", "label": "Method"}
        span_last, _ = E.compute_span(root, node_last, cache, heuristic=True)
        check("the last method still yields a bounded span including its body ('2')",
              "2" in span_last and isinstance(span_last, str), repr(span_last))


def gate_cap_still_honored() -> None:
    print("\n-- F10e: the MAX_SPAN_LINES cap is still the worst-case upper bound --")
    cap = getattr(E, "MAX_SPAN_LINES", None)
    check("MAX_SPAN_LINES is a positive int", isinstance(cap, int) and cap > 0, repr(cap))
    if not isinstance(cap, int):
        return
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        # One giant method body (no inner defs) longer than the cap -> the span is capped, not the whole file.
        body = "\n".join(f"  line_{i}" for i in range(cap + 200))
        (root / "big.js").write_text("function big() {\n" + body + "\n}\n", encoding="utf-8")
        node = {"source_file": "big.js", "start_line": 1, "name": "big", "label": "Function"}
        span, _ = E.compute_span(root, node, {}, heuristic=True)
        check("a single body longer than the cap is truncated to <= MAX_SPAN_LINES lines",
              len(span.splitlines()) <= cap, str(len(span.splitlines())))


def gate_python_unchanged() -> None:
    print("\n-- F10e: the Python (exact-ast) path is unchanged --")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "m.py").write_text("def a():\n    return 1\n\ndef b():\n    return 2\n", encoding="utf-8")
        node = {"source_file": "m.py", "start_line": 1, "name": "a", "label": "Function"}
        span, an = E.compute_span(root, node, {}, heuristic=True)
        check("python def 'a' span is exact (own body, not b's)",
              "return 1" in span and "return 2" not in span and an is not None, repr(span))


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 64)
    print("DeepMap — enrich non-Python span boundedness gates (F10e, no Neo4j)")
    print("=" * 64)
    gate_span_stops_at_next_def()
    gate_cap_still_honored()
    gate_python_unchanged()
    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
