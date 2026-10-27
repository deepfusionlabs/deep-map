#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j gates for the TRUE-embedded-count fix (P1-5, Rails field-review).

THE BUG (0.23.1): `embeddings.generate_embeddings` returns the count re-embedded THIS RUN — a
per-run DELTA, not the total. On a re-sync of an already-fully-embedded graph the delta is
legitimately 0, and `sync.py` fed that 0 to BOTH `verdict.classify(n_embedded=…)` and the manifest
`embedded` field. `verdict.classify` turns `n_embedded == 0` into DEGRADED "VECTOR SEARCH DISABLED",
so a HEALTHY, fully-embedded graph was falsely reported DEGRADED and that verdict was persisted to
the manifest and echoed by doctor.

THE FIX: after `generate_embeddings`, sync queries the TRUE total once
(`MATCH (n) WHERE n.embedding IS NOT NULL RETURN count(n)`, the same query already used by
status/doctor/provision) via a new best-effort `sync._count_embedded_nodes(driver, fallback)`, and
passes THAT total to `verdict.classify`, `write_graph_build_manifest`, and `format_block`. The
per-run delta survives only in the human "embedded this run" log line.

These gates lock: (1) the helper returns the true total from a fake session and falls back (never
raises) on a query error; (2) the WIRING — the sync body feeds the helper's total (not the delta)
to both the verdict and the manifest. Pure + hermetic (a fake driver; no Neo4j, no spend).
"""
from __future__ import annotations

import importlib.util
import re
import sys
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


# --- a minimal fake neo4j driver: session() ctx-mgr, run().single() -> Record-like dict ----------
class _FakeResult:
    def __init__(self, count):
        self._count = count

    def single(self):
        return None if self._count is None else {"c": self._count}


class _FakeSession:
    def __init__(self, count, raise_on_run):
        self._count = count
        self._raise = raise_on_run

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, *a, **kw):
        if self._raise:
            raise RuntimeError("boom (simulated Neo4j error)")
        return _FakeResult(self._count)


class _FakeDriver:
    def __init__(self, count=0, raise_on_run=False):
        self._count = count
        self._raise = raise_on_run

    def session(self, *a, **kw):
        return _FakeSession(self._count, self._raise)


def gate_helper(S) -> None:
    print("\n-- sync._count_embedded_nodes: TRUE total, best-effort fallback, never raises --")
    fn = getattr(S, "_count_embedded_nodes", None)
    check("_count_embedded_nodes exists", callable(fn))
    if not callable(fn):
        return
    # The keystone: a fully-embedded graph re-synced with delta 0 still reports its true total.
    check("returns the true embedded total (4239) even when the per-run delta (fallback) is 0",
          fn(_FakeDriver(count=4239), 0) == 4239)
    check("returns 0 when genuinely nothing is embedded", fn(_FakeDriver(count=0), 0) == 0)
    # Best-effort: a query error degrades to the fallback (the old delta) — never worse, never raises.
    try:
        got = fn(_FakeDriver(raise_on_run=True), 7)
        check("query error -> fallback (7), no raise", got == 7, f"got {got!r}")
    except Exception as exc:  # pragma: no cover - the whole point is it must not raise
        check("query error -> fallback, no raise", False, f"raised {type(exc).__name__}: {exc}")
    # An empty result set (single() -> None) also falls back rather than crashing on None["c"].
    check("empty result (single()->None) -> fallback", fn(_FakeDriver(count=None), 3) == 3)


def gate_wiring(S) -> None:
    print("\n-- sync body wires the TRUE total (not the per-run delta) into verdict + manifest --")
    src = (BUILD / "sync.py").read_text(encoding="utf-8")
    check("sync computes an n_emb_total via _count_embedded_nodes",
          re.search(r"n_emb_total\s*=\s*_count_embedded_nodes\(", src) is not None)
    # classify() and write_graph_build_manifest() must both receive n_embedded=n_emb_total.
    check("verdict.classify(...) receives n_embedded=n_emb_total",
          re.search(r"classify\((?:[^()]|\([^()]*\))*n_embedded\s*=\s*n_emb_total", src) is not None)
    check("write_graph_build_manifest(...) receives n_embedded=n_emb_total",
          re.search(r"write_graph_build_manifest\((?:[^()]|\([^()]*\))*n_embedded\s*=\s*n_emb_total",
                    src) is not None)
    # The OLD bug form (feeding the raw per-run delta n_emb as n_embedded) must be gone at both sites.
    check("no site still feeds the per-run delta as n_embedded=n_emb",
          re.search(r"n_embedded\s*=\s*n_emb\b(?!_total)", src) is None)


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 64)
    print("DeepMap — true-embedded-count / no-false-DEGRADED gates (no Neo4j)")
    print("=" * 64)
    S = _load("sync", BUILD)
    gate_helper(S)
    gate_wiring(S)
    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
