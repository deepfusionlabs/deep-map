#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j gates for the opt-in impact edge_types / ORM-association profile (P1-1, Rails field-review).

On an ORM app "what breaks if I change this model" IS the association graph, but HAS_MANY/REFERENCES
are excluded from the DEFAULT impact walk (a review-locked, benchmark-gated invariant). This delivers
the capability WITHOUT flipping that frozen default:
  * `DeepMapRetriever.impact(symbol, limit, edge_types=None)` + the `deepmap_impact` MCP tool accept a
    per-call `edge_types` override;
  * `DEEPMAP_IMPACT_PROFILE=rails|orm|associations` ergonomically folds ORM associations into the walk;
  * precedence: explicit `edge_types` arg > `DEEPMAP_IMPACT_EDGES` CSV > `DEEPMAP_IMPACT_PROFILE` > the
    frozen `DEFAULT_IMPACT_EDGE_TYPES` (which stays association-free — the benchmark-gate invariant).

Behavioural (fake driver captures the emitted Cypher) + source (MCP param wired). No Neo4j, no spend.
"""
from __future__ import annotations

import contextlib
import importlib.util
import os
import sys
from pathlib import Path

BUILD = Path(__file__).resolve().parent.parent / "build"
sys.path.insert(0, str(BUILD))

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


def _load(name, fname):
    spec = importlib.util.spec_from_file_location(name, BUILD / fname)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@contextlib.contextmanager
def _env(**kv):
    """Set/clear env vars for the block; restore exactly on exit."""
    saved = {k: os.environ.get(k) for k in kv}
    try:
        for k, v in kv.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ---- fake driver: records Cypher, serves the probe + an empty impact result ----
class _Session:
    def __init__(self, drv):
        self.drv = drv

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, cypher, **p):
        self.drv.log.append((cypher, p))
        if "db.relationshipTypes" in cypher:
            return [{"types": list(self.drv.present)}]
        return []


class _Driver:
    def __init__(self, present):
        self.present = present
        self.log = []

    def session(self):
        return _Session(self)


def _main_cypher(drv):
    for c, _ in drv.log:
        if "MATCH path = (caller)" in c:
            return c
    return ""


def gate_resolve_precedence(R) -> None:
    print("\n-- _resolve_impact_params: default frozen; profile opt-in; correct precedence --")
    ASSOC = ("HAS_MANY", "REFERENCES")
    # (1) THE KEYSTONE: no env -> the DEFAULT set, association-FREE (the benchmark-gate invariant).
    with _env(DEEPMAP_IMPACT_EDGES=None, DEEPMAP_IMPACT_PROFILE=None):
        types, _ = R._resolve_impact_params(None, None)
    check("default (no env) == the frozen set, NO ORM associations",
          "CALLS" in types and all(a not in types for a in ASSOC), str(types))
    # (2) profile rails/orm/associations -> DEFAULT + associations.
    for prof in ("rails", "orm", "associations", "RAILS"):
        with _env(DEEPMAP_IMPACT_EDGES=None, DEEPMAP_IMPACT_PROFILE=prof):
            types, _ = R._resolve_impact_params(None, None)
        check(f"DEEPMAP_IMPACT_PROFILE={prof} adds HAS_MANY+REFERENCES (keeps CALLS+CASCADES)",
              all(a in types for a in ASSOC) and "CALLS" in types and "CASCADES" in types, str(types))
    # (3) an unknown profile is ignored -> frozen default.
    with _env(DEEPMAP_IMPACT_EDGES=None, DEEPMAP_IMPACT_PROFILE="bogus"):
        types, _ = R._resolve_impact_params(None, None)
    check("an unknown profile falls back to the frozen default (no associations)",
          all(a not in types for a in ASSOC), str(types))
    # (4) explicit env CSV wins over the profile.
    with _env(DEEPMAP_IMPACT_EDGES="CALLS,ROUTES_TO", DEEPMAP_IMPACT_PROFILE="rails"):
        types, _ = R._resolve_impact_params(None, None)
    check("DEEPMAP_IMPACT_EDGES CSV wins over the profile", set(types) == {"CALLS", "ROUTES_TO"}, str(types))
    # (5) an explicit edge_types arg wins over everything.
    with _env(DEEPMAP_IMPACT_EDGES="CALLS,ROUTES_TO", DEEPMAP_IMPACT_PROFILE="rails"):
        types, _ = R._resolve_impact_params(["CALLS", "INHERITS"], None)
    check("an explicit edge_types arg wins over env + profile",
          set(types) == {"CALLS", "INHERITS"}, str(types))


def gate_threading(R) -> None:
    print("\n-- edge_types threads through query_impact + DeepMapRetriever.impact + the profile --")
    present = ("CALLS", "HAS_MANY", "REFERENCES", "ROUTES_TO", "CASCADES", "INHERITS")
    # (a) query_impact(edge_types=...) -> the association types reach the traversal Cypher.
    drv = _Driver(present)
    with _env(DEEPMAP_IMPACT_EDGES=None, DEEPMAP_IMPACT_PROFILE=None):
        R.query_impact(drv, "Organization", edge_types=["CALLS", "HAS_MANY", "REFERENCES"])
    cy = _main_cypher(drv)
    check("query_impact(edge_types=[...]) folds HAS_MANY+REFERENCES into the rel pattern",
          "HAS_MANY" in cy and "REFERENCES" in cy, cy)
    # (b) DeepMapRetriever.impact threads edge_types down to query_impact.
    drv = _Driver(present)
    ret = R.DeepMapRetriever(drv, embed_fn=lambda q: [0.0])
    with _env(DEEPMAP_IMPACT_EDGES=None, DEEPMAP_IMPACT_PROFILE=None):
        ret.impact("Organization", edge_types=["CALLS", "HAS_MANY"])
    cy = _main_cypher(drv)
    check("DeepMapRetriever.impact(edge_types=[...]) reaches the traversal Cypher", "HAS_MANY" in cy, cy)
    # (c) the ergonomic path: PROFILE alone (no per-call arg) folds associations in end-to-end.
    drv = _Driver(present)
    with _env(DEEPMAP_IMPACT_EDGES=None, DEEPMAP_IMPACT_PROFILE="rails"):
        R.query_impact(drv, "Organization")
    cy = _main_cypher(drv)
    check("DEEPMAP_IMPACT_PROFILE=rails folds associations in with NO per-call arg",
          "HAS_MANY" in cy and "REFERENCES" in cy, cy)
    # (d) the frozen default really excludes them end-to-end (no arg, no env).
    drv = _Driver(present)
    with _env(DEEPMAP_IMPACT_EDGES=None, DEEPMAP_IMPACT_PROFILE=None):
        R.query_impact(drv, "Organization")
    cy = _main_cypher(drv)
    check("the default walk still EXCLUDES HAS_MANY/REFERENCES (benchmark-gate invariant holds)",
          "HAS_MANY" not in cy and "REFERENCES" not in cy, cy)


def gate_mcp_param() -> None:
    print("\n-- the deepmap_impact MCP tool exposes + threads edge_types --")
    src = (BUILD / "deepmap_search_server.py").read_text(encoding="utf-8")
    check("deepmap_impact signature accepts edge_types",
          "def deepmap_impact(" in src and "edge_types" in src.split("def deepmap_impact(", 1)[1][:200])
    check("deepmap_impact passes edge_types to .impact",
          "edge_types=edge_types" in src)


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 64)
    print("DeepMap — impact edge_types / ORM-association profile gates (no Neo4j)")
    print("=" * 64)
    R = _load("retrieval", "retrieval.py")
    gate_resolve_precedence(R)
    gate_threading(R)
    gate_mcp_param()
    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
