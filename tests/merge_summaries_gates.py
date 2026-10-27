#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gate: opt-in recognizer summary-merge on a (name, source_file) collision (F10b). No Neo4j/spend.

The 2026-07 field audit: on the graphify happy path a Rails model class IS parsed by the base
extractor, so `merge_supplemental` DROPS the recognizer's supplemental node (base wins — correct, it
avoids a self-inflicted AMBIGUOUS). But the recognizer node carried the RICH association summary
('Order: ActiveRecord model; belongs_to :customer; has_many :line_items') while the base node kept only
graphify's thin name-echo ('Order: class') — so the vector arm never sees the association signal on the
happy path, only on the (rarer) path where graphify missed the class.

Fix (opt-in, DEFAULT OFF): DEEPMAP_MERGE_SUMMARIES=1 makes a collision APPEND the recognizer summary's
fact tail into the base node's body_summary IN PLACE (deduped, length-capped, idempotent) instead of
dropping it. Default OFF because body_summary is embedded -> it is a ranking/benchmark input; the
default graph must stay byte-identical until a benchmark A/B blesses the change. The list-identity
contract (return the ORIGINAL list object when nothing is ADDED) is preserved in BOTH modes — the merge
mutates a base node's string in place, it never appends a node.

Hermetic: constructs base+supplemental node dicts directly, toggles the env flag, asserts the summary
delta + the identity invariant + idempotency + the drop-not-fabricate default.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "build"))

import language_recognizers as LR  # noqa: E402

_PASS = _FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {label}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}{(' — ' + detail) if detail else ''}")


def _base(name="Order", summary="Order: class"):
    return {"key": "app/models/order.rb::Order", "label": "Class", "name": name,
            "source_file": "app/models/order.rb", "start_line": 1, "documented": False,
            "body_summary": summary}


def _supp(name="Order",
          summary="Order: ActiveRecord model; belongs_to :customer; has_many :line_items"):
    return {"key": "rr::app/models/order.rb::Order", "label": "Class", "name": name,
            "source_file": "app/models/order.rb", "start_line": 1, "documented": False,
            "body_summary": summary}


def _set_flag(val):
    if val is None:
        os.environ.pop("DEEPMAP_MERGE_SUMMARIES", None)
    else:
        os.environ["DEEPMAP_MERGE_SUMMARIES"] = val


def gate_default_off_base_unchanged() -> None:
    print("\n-- F10b: default (flag unset) -> base wins, base summary UNCHANGED, list identity held --")
    _set_flag(None)
    base = _base()
    nodes = [base]
    merged = LR.merge_supplemental(nodes, [_supp()])
    check("collision drops the supplemental (still one node)", len(merged) == 1, str(len(merged)))
    check("returns the ORIGINAL list object (byte-identity contract)", merged is nodes)
    check("base body_summary is UNCHANGED by default (benchmark byte-identity)",
          merged[0]["body_summary"] == "Order: class", repr(merged[0]["body_summary"]))


def gate_flag_on_appends_facts() -> None:
    print("\n-- F10b: DEEPMAP_MERGE_SUMMARIES=1 -> the recognizer fact tail is appended in place --")
    _set_flag("1")
    try:
        base = _base()
        nodes = [base]
        merged = LR.merge_supplemental(nodes, [_supp()])
        check("still one node (a summary merge never appends a node)", len(merged) == 1, str(len(merged)))
        check("list identity is STILL preserved (in-place mutation, not a new list)", merged is nodes)
        s = merged[0]["body_summary"]
        check("the base summary now carries the association facts ('belongs_to :customer')",
              "belongs_to :customer" in s, repr(s))
        check("...and 'has_many :line_items'", "has_many :line_items" in s, repr(s))
        check("the base's own text is retained ('Order: class')", "Order: class" in s, repr(s))
        check("the recognizer name-echo prefix is NOT duplicated (tail only, no second 'ActiveRecord')",
              s.count("ActiveRecord model") <= 1, repr(s))
    finally:
        _set_flag(None)


def gate_flag_on_idempotent() -> None:
    print("\n-- F10b: re-running the merge with the flag on does not double-append --")
    _set_flag("1")
    try:
        base = _base()
        nodes = [base]
        LR.merge_supplemental(nodes, [_supp()])
        first = nodes[0]["body_summary"]
        LR.merge_supplemental(nodes, [_supp()])
        second = nodes[0]["body_summary"]
        check("second merge is a no-op on the summary (idempotent, no growth)", first == second,
              f"{first!r} != {second!r}")
        check("'belongs_to :customer' appears exactly once", second.count("belongs_to :customer") == 1,
              repr(second))
    finally:
        _set_flag(None)


def gate_flag_on_length_capped() -> None:
    print("\n-- F10b: the merged summary is length-capped (no unbounded growth) --")
    _set_flag("1")
    try:
        huge = "Order: ActiveRecord model; " + "; ".join(f"has_many :assoc_{i}" for i in range(400))
        base = _base()
        nodes = [base]
        merged = LR.merge_supplemental(nodes, [_supp(summary=huge)])
        s = merged[0]["body_summary"]
        check("the merged body_summary is capped (<= 600 chars)", len(s) <= 600, str(len(s)))
    finally:
        _set_flag(None)


def gate_flag_on_still_drops_when_no_facts() -> None:
    print("\n-- F10b: a supplemental with no fact tail leaves the base summary untouched (nothing to add) --")
    _set_flag("1")
    try:
        base = _base(summary="Order: class")
        nodes = [base]
        # supplemental summary with NO ';' -> no fact tail -> nothing appended.
        LR.merge_supplemental(nodes, [_supp(summary="Order: ActiveRecord model")])
        check("no ';' fact tail -> base summary unchanged", nodes[0]["body_summary"] == "Order: class",
              repr(nodes[0]["body_summary"]))
    finally:
        _set_flag(None)


def gate_flag_on_new_node_still_added() -> None:
    print("\n-- F10b: a NON-colliding supplemental is still appended as a new node (both modes) --")
    _set_flag("1")
    try:
        base = _base()
        nodes = [base]
        fresh = _supp(name="Invoice")
        fresh["source_file"] = "app/models/invoice.rb"
        fresh["key"] = "rr::app/models/invoice.rb::Invoice"
        merged = LR.merge_supplemental(nodes, [fresh])
        check("a non-colliding recognizer node is added", len(merged) == 2, str(len(merged)))
        check("a NEW node was added -> a fresh list object (not the original)", merged is not nodes)
    finally:
        _set_flag(None)


def gate_never_raises_on_bad_input() -> None:
    print("\n-- F10b: merge is crash-proof even with the flag on and malformed nodes --")
    _set_flag("1")
    try:
        base = {"name": "X", "source_file": "x.rb"}  # no body_summary key
        nodes = [base]
        try:
            merged = LR.merge_supplemental(nodes, [{"name": "X", "source_file": "x.rb",
                                                    "body_summary": "X: m; has_many :y"}])
            check("a base node missing body_summary does not crash the merge", True)
            check("list identity preserved on the degrade path", merged is nodes)
        except Exception as e:  # noqa: BLE001
            check("a base node missing body_summary does not crash the merge", False, repr(e))
    finally:
        _set_flag(None)


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 64)
    print("DeepMap — recognizer summary-merge gates (F10b, opt-in, no Neo4j)")
    print("=" * 64)
    gate_default_off_base_unchanged()
    gate_flag_on_appends_facts()
    gate_flag_on_idempotent()
    gate_flag_on_length_capped()
    gate_flag_on_still_drops_when_no_facts()
    gate_flag_on_new_node_still_added()
    gate_never_raises_on_bad_input()
    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
