#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j, no-spend gates for the benchmark's DETERMINISTIC completeness scorer.

`benchmark/score_completeness.py` computes the HERO metric `completeness_pct` = the fraction of a
question's pinned, enumerable `ground_truth_set` that the agent's answer actually enumerated — with
NO model and NO judgment (the reproducible half of the chosen scoring method:
deterministic-completeness + blind-LLM-judge-correctness + human-spot-check). Determinism + honest
matching are load-bearing: a fabricated/over-credited completeness number would defeat the product
thesis. These gates lock the matcher against over- and under-crediting.

Pure + hermetic: imports the module, calls the function on synthetic answer/ground-truth pairs.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BENCH = REPO / "benchmark"

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


def _load():
    spec = importlib.util.spec_from_file_location("dm_score_completeness", BENCH / "score_completeness.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _pct(S, answer, gts):
    return S.score_completeness(answer, gts)["completeness_pct"]


def gate_basic_fractions() -> None:
    print("\n-- score_completeness: exact fraction of the ground-truth set enumerated --")
    S = _load()
    gts = ["alpha", "beta", "gamma", "delta"]
    check("all four enumerated -> 100.0",
          _pct(S, "alpha, beta, gamma and delta are affected", gts) == 100.0)
    check("two of four -> 50.0", _pct(S, "alpha and gamma", gts) == 50.0)
    check("none -> 0.0", _pct(S, "nothing relevant here", gts) == 0.0)
    r = S.score_completeness("alpha only", gts)
    check("returns matched + missed + n", r["n"] == 4 and r["matched"] == ["alpha"]
          and set(r["missed"]) == {"beta", "gamma", "delta"}, str(r))
    # empty ground-truth set is a wiring smell, not a 100% freebie.
    empty = S.score_completeness("anything", [])
    check("empty ground_truth_set -> 0.0 with a flag (not a free 100%)",
          empty["completeness_pct"] == 0.0 and empty.get("empty_oracle") is True, str(empty))


def gate_word_boundary_no_substring_overcredit() -> None:
    print("\n-- symbol match is word-bounded (no substring over-credit) --")
    S = _load()
    # 'auth' must NOT be credited by 'authorize'/'author' (substring) — word boundary required.
    check("'auth' NOT credited by 'authorize' (substring guard)",
          _pct(S, "the authorize() helper runs first", ["auth"]) == 0.0)
    check("'auth' credited by a real 'auth' token", _pct(S, "calls auth then logs", ["auth"]) == 100.0)
    # dotted symbols: Class.method credited by the exact dotted form.
    check("'Svc.charge' credited by the dotted form", _pct(S, "Svc.charge is called", ["Svc.charge"]) == 100.0)


def gate_file_line_and_node_items() -> None:
    print("\n-- file:line and symbol@file:line items --")
    S = _load()
    check("file:line credited by the same file:line",
          _pct(S, "see jobs/invoice_runner.py:44 for the bug", ["jobs/invoice_runner.py:44"]) == 100.0)
    check("file:line tolerates a backslash path + L-form",
          _pct(S, "at jobs\\invoice_runner.py#L44", ["jobs/invoice_runner.py:44"]) == 100.0)
    # SYMMETRY: the GROUND-TRUTH item is normalized like the answer (#L44 -> :44), so a curator's
    # GitHub-style 'pay.py#L12' still credits an answer's 'pay.py:12' (no silent under-credit).
    check("GT '#L' line marker matches an answer ':' line (symmetric normalization)",
          _pct(S, "see billing/pay.py:12", ["billing/pay.py#L12"]) == 100.0)
    check("file:line NOT credited by the wrong line",
          _pct(S, "at jobs/invoice_runner.py:99", ["jobs/invoice_runner.py:44"]) == 0.0)
    # The line number must be RIGHT-bounded: 'pay.py:12' must NOT be credited by 'pay.py:120' (a
    # DIFFERENT line) — a substring match would over-credit the HERO metric (bug-hunt #6).
    check("file:line NOT credited by a LONGER line number (12 vs 120)",
          _pct(S, "see billing/pay.py:120 for details", ["billing/pay.py:12"]) == 0.0)
    check("basename file:line NOT credited by a longer line number",
          _pct(S, "at pay.py:120", ["pay.py:12"]) == 0.0)
    # ...but a real ':12' adjacent to a different ':120' IS still credited (boundary, not blanket fail).
    check("file:line still credited when the exact line is present among others",
          _pct(S, "both config.py:120 and config.py:12 changed", ["config.py:12"]) == 100.0)
    # The PATH must be LEFT-bounded: 'pay.py:12' must NOT be credited by 'mypay.py:12' (a diff file).
    check("file:line NOT credited by a different file sharing the basename suffix",
          _pct(S, "at mypay.py:12", ["pay.py:12"]) == 0.0)
    # symbol@file:line — the STRONGEST item: needs BOTH the location and the symbol.
    item = "charge@billing/pay.py:12"
    check("symbol@file:line needs BOTH location and symbol",
          _pct(S, "charge() at billing/pay.py:12", [item]) == 100.0)
    check("symbol@file:line NOT credited by the symbol alone (wrong file)",
          _pct(S, "charge() somewhere else", [item]) == 0.0)


def gate_edges_and_aliases() -> None:
    print("\n-- edge items (a->b co-occurrence) + alias forms --")
    S = _load()
    check("edge 'a->b' credited when both endpoints appear",
          _pct(S, "a is called by b in the chain", ["a->b"]) == 100.0)
    check("edge 'a->b' NOT credited when only one endpoint appears",
          _pct(S, "only a appears", ["a->b"]) == 0.0)
    # alias: a pipe-separated item is covered if ANY accepted surface form matches.
    check("alias item credited by any accepted form",
          _pct(S, "the RetryWorker dispatches it", ["retry_worker | RetryWorker | retry-worker"]) == 100.0)


def gate_rich_curated_atom_format() -> None:
    """The SHIPPED benchmark ground_truth_set atoms are RICH descriptive strings of the form
    "<file:line> — <Symbol> (<human description>)" (all 234 atoms in questions.yaml). The matcher must
    credit completeness on the LEADING file:line locator and IGNORE the trailing prose (which itself
    can contain '->', ':', parens). Without this the HERO metric is ~0% for every good answer (the whole
    rich atom is never present verbatim) — the Scenario-A scoring bug."""
    print("\n-- RICH curated atom 'file:line — Symbol (desc)' credited on the leading file:line --")
    S = _load()
    rich = "src/requests/sessions.py:784 — Session.send (adapter.send call: r = adapter.send(request, **kwargs))"
    check("rich atom credited when the answer cites the leading file:line",
          _pct(S, "the dispatch happens at src/requests/sessions.py:784", [rich]) == 100.0)
    check("rich atom NOT credited by a WRONG line (right-bound preserved through the rich form)",
          _pct(S, "see src/requests/sessions.py:999", [rich]) == 0.0)
    check("rich atom NOT credited by prose alone (no file:line cited)",
          _pct(S, "Session.send eventually calls adapter.send on the request", [rich]) == 0.0)
    # An atom whose DESCRIPTION contains '->' must still match on its file:line, NOT be mis-parsed as an edge.
    arrow_desc = "src/requests/sessions.py:158 — should_strip_auth (hostname mismatch -> True)"
    check("rich atom with '->' in the description matches on file:line (not edge-parsed)",
          _pct(S, "the check is at sessions.py:158", [arrow_desc]) == 100.0)
    # A real curated multi-atom set scored against an answer that cites SOME of the lines -> partial.
    gts = [
        "src/requests/sessions.py:784 — Session.send (adapter.send call)",
        "src/requests/sessions.py:778 — Session.send (get_adapter call)",
        "src/requests/adapters.py:634 — HTTPAdapter.send (def send)",
        "src/requests/adapters.py:262 — HTTPAdapter.init_poolmanager",
    ]
    ans = "It flows through sessions.py:784 and sessions.py:778, then into adapters.py:634."
    r = S.score_completeness(ans, gts)
    check("3 of 4 rich atoms cited -> 75.0", r["completeness_pct"] == 75.0, str(r["completeness_pct"]))
    # basename-only citation also credits (answers often drop the src/ prefix).
    check("rich atom credited by a BASENAME file:line citation",
          _pct(S, "at sessions.py:784 the adapter is invoked", [rich]) == 100.0)


def gate_determinism() -> None:
    print("\n-- deterministic: same inputs -> same score, twice --")
    S = _load()
    gts = ["alpha", "billing/pay.py:7", "x->y", "Svc.run"]
    ans = "alpha, Svc.run, billing/pay.py:7, and x reaches y"
    a = S.score_completeness(ans, gts)["completeness_pct"]
    b = S.score_completeness(ans, gts)["completeness_pct"]
    check("two runs agree exactly", a == b, f"{a} vs {b}")
    check("mixed item types all credited -> 100.0", a == 100.0, str(a))


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 64)
    print("DeepMap — benchmark deterministic completeness-scorer gates (no spend)")
    print("=" * 64)

    gate_basic_fractions()
    gate_word_boundary_no_substring_overcredit()
    gate_file_line_and_node_items()
    gate_edges_and_aliases()
    gate_rich_curated_atom_format()
    gate_determinism()

    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
