#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gates for the EXTRACTOR-AGNOSTIC sync seam (enrich.apply_sidecar) — no Neo4j, no spend.

The default extractor is graphify (opaque node keys, no bodies); the ast engine is the fallback
(`relpath::name:line` keys). enrich.apply_sidecar overrides body_summary on the produced node-dict LIST
by RESULT identity (source_file::name::start_line) — so it works identically for BOTH engines. These
gates feed apply_sidecar a graphify-SHAPED node dict AND an ast-SHAPED one and prove: fresh hit ->
override; the opaque `key` and CALLS-relevant fields are never touched; a no-sidecar run is a no-op.
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "build"))
import enrich  # noqa: E402

FAILS = []


def check(cond, msg):
    print(f"  [{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        FAILS.append(msg)


def _write(d, rel, txt):
    p = Path(d) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(txt, encoding="utf-8")


def _seed(d, source_file, name, start_line, summary):
    node = dict(source_file=source_file, name=name, start_line=start_line, label="Function")
    span, _ = enrich.compute_span(d, node, {})
    nid = enrich.node_id(source_file, name, start_line)
    enrich.append_entries(d, [enrich.make_entry(nid, enrich.content_hash(span), summary,
                                                model="claude-sonnet-5", kind="Function")])


def test_graphify_shaped_override():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "svc/pay.py", "def charge(card, amount):\n    validate(card)\n    return create(amount)\n")
        _seed(d, "svc/pay.py", "charge", 1, "Charges the card for the given amount.")
        # a GRAPHIFY-shaped node: opaque key, methods as Class.method, Route possible — but the RESULT
        # identity fields (source_file/name/start_line) are what apply_sidecar matches on.
        gnode = dict(key="pkg_pay_charge_deadbeef", label="Function", name="charge",
                     source_file="svc/pay.py", start_line=1, documented=False, body_summary="DET")
        st = enrich.apply_sidecar(d, [gnode])
        check(st["applied"] == 1, "graphify-shaped: fresh hit applied via result identity")
        check(gnode["body_summary"] == "Charges the card for the given amount.", "graphify-shaped: body_summary overridden")
        check(gnode["key"] == "pkg_pay_charge_deadbeef", "graphify-shaped: opaque key untouched")


def test_ast_shaped_override():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "m.py", "def refund(txn):\n    return reverse(txn)\n")
        _seed(d, "m.py", "refund", 1, "Reverses the given transaction.")
        anode = dict(key="m.py::refund:1", label="Function", name="refund",
                     source_file="m.py", start_line=1, documented=False, body_summary="DET")
        st = enrich.apply_sidecar(d, [anode])
        check(st["applied"] == 1 and anode["body_summary"] == "Reverses the given transaction.",
              "ast-shaped: fresh hit applied")
        check(anode["key"] == "m.py::refund:1", "ast-shaped: key untouched")


def test_no_sidecar_noop_and_survives():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "m.py", "def f(x):\n    return g(x)\n")
        node = dict(key="k", label="Function", name="f", source_file="m.py", start_line=1,
                    documented=False, body_summary="DET")
        st = enrich.apply_sidecar(d, [node])
        check(st == {"applied": 0, "checked": 0} and node["body_summary"] == "DET",
              "no sidecar: apply is a byte-identical no-op")
        # after seeding + applying, the sidecar file still exists (apply never deletes it)
        _seed(d, "m.py", "f", 1, "Applies g to x.")
        enrich.apply_sidecar(d, [dict(node)])
        check(enrich.sidecar_path(d).exists(), "sidecar survives apply (never deleted by the seam)")


def test_mixed_batch_and_partial():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "m.py", "def a(x):\n    return x\n\n\ndef b(y):\n    return y\n")
        _seed(d, "m.py", "a", 1, "Returns x unchanged.")   # only 'a' is enriched
        nodes = [dict(key="k1", label="Function", name="a", source_file="m.py", start_line=1,
                      documented=False, body_summary="DET-A"),
                 dict(key="k2", label="Function", name="b", source_file="m.py", start_line=5,
                      documented=False, body_summary="DET-B")]
        st = enrich.apply_sidecar(d, nodes)
        check(st["applied"] == 1, "mixed batch: only the enriched node is overridden")
        check(nodes[0]["body_summary"] == "Returns x unchanged." and nodes[1]["body_summary"] == "DET-B",
              "mixed batch: un-enriched node keeps its deterministic summary")


def main():
    print("=" * 72)
    print("enrich_sync_seam_gates — extractor-agnostic body_summary override (no Neo4j/spend)")
    print("=" * 72)
    for fn in (test_graphify_shaped_override, test_ast_shaped_override,
               test_no_sidecar_noop_and_survives, test_mixed_batch_and_partial):
        print(f"\n--- {fn.__name__} ---")
        try:
            fn()
        except Exception as exc:
            FAILS.append(f"{fn.__name__} raised {type(exc).__name__}: {exc}")
            print(f"  [FAIL] {fn.__name__} raised {type(exc).__name__}: {exc}")
    print("\n" + "=" * 72)
    print("RESULT:", "GREEN" if not FAILS else f"RED — {len(FAILS)}: {FAILS}")
    return 0 if not FAILS else 1


if __name__ == "__main__":
    raise SystemExit(main())
