#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j, no-spend gates for the atomic `neo4j.conf` edit in `build/provision.py:configure_neo4j`.

The old code did read-check-then-`open(conf,"a")`-append: two concurrent provisions could both pass
the `CONF_MARKER` check and each append the DeepMap block -> a DUPLICATE block (and a crash mid-append
left a partial block). The fix reads once, and if the marker is absent writes `txt + conf_block` to a
UNIQUE temp file then `os.replace`s it in — idempotent + content-addressed, so every interleaving
converges to exactly ONE block and a reader only ever sees the old or new COMPLETE file.

The TOCTOU is reproduced DETERMINISTICALLY (single-threaded) with a reentrancy trick: monkeypatch
`conf_block` so that, on its first call, it runs a NESTED `configure_neo4j` to completion (the "other
provision") between the outer call's read and its write, then returns the real block. On the append
code that yields two blocks (RED); on the atomic code exactly one (GREEN).

Pure + hermetic: a temp conf file, `paths`/`conf_block` monkeypatched. No Neo4j.
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
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
        print(f"  [FAIL] {label}{(' — ' + detail) if detail else ''}")


def _load(name: str, base: Path):
    spec = importlib.util.spec_from_file_location(f"dm_{name}", base / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def gate_toctou_single_block(P) -> None:
    print("\n-- TOCTOU: a nested provision between read & write -> exactly ONE block --")
    marker = P.CONF_MARKER
    real_conf_block = P.conf_block
    with tempfile.TemporaryDirectory() as td:
        conf = Path(td) / "neo4j.conf"
        conf.write_text("# base neo4j.conf\ndbms.example=1\n", encoding="utf-8")
        saved = {"paths": P.paths, "conf_block": P.conf_block}
        state = {"fired": False}

        def reentrant_conf_block(min_pw_len):
            # On the FIRST call (the outer provision, mid-edit), simulate a concurrent provision that
            # completes fully before the outer one writes. Guarded so the nested call doesn't recurse.
            if not state["fired"]:
                state["fired"] = True
                P.configure_neo4j("5.26.0")
            return real_conf_block(min_pw_len)

        try:
            P.paths = lambda v: {"conf": conf}
            P.conf_block = reentrant_conf_block
            P.configure_neo4j("5.26.0")
        finally:
            P.paths = saved["paths"]
            P.conf_block = saved["conf_block"]

        n = conf.read_text(encoding="utf-8").count(marker)
        check("CONF_MARKER appears exactly once after a raced edit", n == 1, f"{n} block(s)")


def gate_idempotent_and_guarded(P) -> None:
    print("\n-- idempotency + I/O guard --")
    marker = P.CONF_MARKER
    with tempfile.TemporaryDirectory() as td:
        conf = Path(td) / "neo4j.conf"
        conf.write_text("# base\n", encoding="utf-8")
        saved = P.paths
        try:
            P.paths = lambda v: {"conf": conf}
            first = P.configure_neo4j("5.26.0")
            second = P.configure_neo4j("5.26.0")
        finally:
            P.paths = saved
        txt = conf.read_text(encoding="utf-8")
        check("first call writes the block (returns True)", first is True)
        check("second call is a no-op (returns False)", second is False)
        check("still exactly one block after two sequential calls", txt.count(marker) == 1,
              f"{txt.count(marker)}")

    print("\n-- unwritable/missing conf degrades (False, no raise) --")
    with tempfile.TemporaryDirectory() as td:
        missing = Path(td) / "no_such_dir" / "neo4j.conf"   # parent doesn't exist
        saved = P.paths
        raised = None
        rc = None
        try:
            P.paths = lambda v: {"conf": missing}
            try:
                rc = P.configure_neo4j("5.26.0")
            except Exception as exc:  # noqa: BLE001
                raised = type(exc).__name__
        finally:
            P.paths = saved
        check("missing conf -> returns False, does not raise", rc is False and raised is None,
              f"rc={rc} raised={raised}")


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 64)
    print("DeepMap — configure_neo4j atomic-edit gates (no Neo4j)")
    print("=" * 64)
    P = _load("provision", BUILD)
    gate_toctou_single_block(P)
    gate_idempotent_and_guarded(P)
    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
