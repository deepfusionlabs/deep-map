#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gate: deepmap_impact accepts the Ruby `Class#method` symbol notation (F10d). No Neo4j.

The 2026-07 field audit: `deepmap_impact('User#related_to_user?')` returned [] because the impact
matcher only stripped a LEADING dot ('.foo' -> 'foo') and never translated Ruby's `#` instance-method
separator to the `.` the graph stores (every node name is dotted `Class.method`, never `Class#method`).
So the natural Ruby form silently returned no dependents while the dotted form worked.

Fix: query_callers + query_impact normalize the QUERY INPUT `symbol.lstrip('.').replace('#', '.')`.
Lossless — it is applied to the query string, never to stored data; no node name contains '#', so
dotted/bare queries pass through unchanged (fully backward compatible). This lives on the impact path
(query_callers/query_impact), NOT the byte-frozen 3-arm ranking (_arm_*/_weighted_rrf/_hydrate/search),
so it is the same OFF-ranking category as the v0.24 impact(edge_types=) / _resolve_impact_params deltas.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "build"))

import retrieval as R  # noqa: E402

_PASS = _FAIL = 0


def check(desc: str, cond: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    ok = bool(cond)
    _PASS += ok
    _FAIL += (not ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {desc}" + ("" if ok else f"  -- {detail}"))


class _Rows:
    """An empty, benign Neo4j result: iterates to nothing, single() is None (so _present_rel_types and
    the row builders both no-op without raising)."""
    def __iter__(self):
        return iter(())

    def single(self):
        return None


class _Sess:
    def __init__(self, calls):
        self.calls = calls

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, cypher, **params):
        self.calls.append((cypher, params))
        return _Rows()


class _Drv:
    def __init__(self):
        self.calls = []

    def session(self):
        return _Sess(self.calls)

    def verify_connectivity(self):
        return None


def _name_param(fn, symbol):
    """Run fn(driver, symbol) against a fake driver and return the `name` bound param the query used
    (the last query that bound one — the main match, not the rel-type probe). None if none bound."""
    drv = _Drv()
    fn(drv, symbol)
    named = [p["name"] for _, p in drv.calls if "name" in p]
    return named[-1] if named else None


def gate_hash_notation() -> None:
    print("\n-- F10d: Class#method -> Class.method normalization on the impact query input --")
    for fn_name, fn in (("query_impact", R.query_impact), ("query_callers", R.query_callers)):
        check(f"{fn_name}('User#related_to_user?') binds name='User.related_to_user?'",
              _name_param(fn, "User#related_to_user?") == "User.related_to_user?",
              str(_name_param(fn, "User#related_to_user?")))
        # bang/predicate methods keep their suffix
        check(f"{fn_name}('User#save!') binds name='User.save!'",
              _name_param(fn, "User#save!") == "User.save!", str(_name_param(fn, "User#save!")))
        # a leading dot AND a '#' both normalize
        check(f"{fn_name}('.User#x') binds name='User.x'",
              _name_param(fn, ".User#x") == "User.x", str(_name_param(fn, ".User#x")))
        # BACKWARD COMPAT: no '#' -> pass through unchanged (bare, leading-dot, dotted)
        check(f"{fn_name}('.foo') still binds name='foo'",
              _name_param(fn, ".foo") == "foo", str(_name_param(fn, ".foo")))
        check(f"{fn_name}('foo') still binds name='foo'",
              _name_param(fn, "foo") == "foo", str(_name_param(fn, "foo")))
        check(f"{fn_name}('Svc.foo') still binds name='Svc.foo' (dotted, unchanged)",
              _name_param(fn, "Svc.foo") == "Svc.foo", str(_name_param(fn, "Svc.foo")))


def gate_ranking_untouched() -> None:
    print("\n-- the fix is OFF the frozen ranking path (impact-only) --")
    # The normalization lives in query_callers/query_impact only; the ranking functions are unchanged.
    import inspect
    for fname in ("_weighted_rrf", "DEFAULT_ARM_WEIGHTS", "RRF_K"):
        check(f"module-level ranking symbol retrieval.{fname} still present (freeze intact)",
              hasattr(R, fname))
    src = inspect.getsource(R.query_impact) + inspect.getsource(R.query_callers)
    check("both impact fns normalize '#'-> '.' on the input symbol",
          'replace("#", ".")' in src or "replace('#', '.')" in src, "normalization missing")


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 64)
    print("DeepMap — impact Class#method notation gates (F10d, no Neo4j)")
    print("=" * 64)
    gate_hash_notation()
    gate_ranking_untouched()
    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
