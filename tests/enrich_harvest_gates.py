#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j, no-spend gates for `deepmap enrich --deterministic` honesty (2026-09 live validation,
owner W3 — bug C7).

Locks:
  * the doc comment ABOVE a non-Python definition (`//`, `///`, `/** */`, `#`) is harvested — the real
    compute_span starts AT the def line, so a span-only harvest never saw it (the documented F10a remedy
    silently did nothing on real nodes; only a hand-built span in a unit test started at the comment),
  * the node's default summary (e.g. graphify's '; calls …' clause) is KEPT as the base — the free tier
    must never make a summary thinner than the one sync already had,
  * a node with no extra local signal is NOT written and NOT counted as enriched (it was reported as
    'enriched' with a byte-identical summary),
  * a moved-but-unchanged definition (a pure line shift) keeps its summary: enrich re-homes the entry
    (no LLM re-bill, no orphan left behind) and apply_sidecar finds it by (file, name, hash),
  * the enrich() API clamps a negative max_nodes to 0 (was slice semantics: -5 enriched 9 of 14).

Hermetic: `_rebuild_code` is replaced by a graphify-shaped node list over REAL source files in a temp
dir (the span reader + harvest run for real); the LLM transport is a scripted fake (no claude, no spend).
"""
from __future__ import annotations

import importlib.util
import json
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


def _load_enrich():
    if str(BUILD) not in sys.path:
        sys.path.insert(0, str(BUILD))
    spec = importlib.util.spec_from_file_location("dm_enrich_harvest", BUILD / "enrich.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


GO_SRC = """package router

import "strings"

// CleanPath returns the canonical URL path, collapsing repeated slashes.
// It is safe to call on untrusted input.
func CleanPath(p string) string {
\treturn app(strings.TrimSpace(p))
}

func app(p string) string {
\treturn p + "/"
}
"""

TS_SRC = """import { scale } from './scale';

/**
 * Computes the retry delay for the given attempt number.
 */
export function computeDelay(attempt: number): number {
  return scale(attempt) * 100;
}

@Injectable()
export class RateLimiter {
  // tracks tokens
  tokens = 0;
}
"""


def _nodes_for(root: Path):
    """graphify-shaped nodes (documented=False, name-derived body_summary incl. a calls clause) whose
    start_line points at the REAL def line in the files on disk."""
    def line_of(rel, needle):
        for i, ln in enumerate((root / rel).read_text(encoding="utf-8").splitlines(), start=1):
            if needle in ln:
                return i
        raise AssertionError(f"{needle!r} not in {rel}")
    return [
        dict(key="g1", label="Function", name="CleanPath", source_file="router/path.go",
             start_line=line_of("router/path.go", "func CleanPath"), documented=False,
             body_summary="clean path: function; calls app"),
        dict(key="g2", label="Function", name="app", source_file="router/path.go",
             start_line=line_of("router/path.go", "func app"), documented=False,
             body_summary="app: function"),
        dict(key="g3", label="Function", name="computeDelay", source_file="web/delay.ts",
             start_line=line_of("web/delay.ts", "export function computeDelay"), documented=False,
             body_summary="compute delay: function; calls scale"),
    ]


def _write_repo(root: Path, go_prefix: str = "") -> None:
    (root / "router").mkdir(parents=True, exist_ok=True)
    (root / "web").mkdir(parents=True, exist_ok=True)
    (root / "router" / "path.go").write_text(go_prefix + GO_SRC, encoding="utf-8")
    (root / "web" / "delay.ts").write_text(TS_SRC, encoding="utf-8")


def gate_harvest_and_base(E) -> None:
    print("\n-- the doc comment ABOVE the def is harvested; the calls clause survives; no-op not written --")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write_repo(root)
        E._rebuild_code = lambda r: (_nodes_for(Path(r)), [("g1", "app")], [], "graphify")
        st = E.enrich(root, deterministic=True)
        sc = {e["node_id"].split("::")[1]: e for e in E.load_sidecar(root).values()}
        cp = (sc.get("CleanPath") or {}).get("summary", "")
        check("Go `//` doc comment above `func CleanPath` is harvested",
              "canonical URL path" in cp, repr(cp))
        check("the base keeps the default '; calls app' clause", "calls app" in cp, repr(cp))
        cd = (sc.get("computeDelay") or {}).get("summary", "")
        check("TS `/** */` block above `export function computeDelay` is harvested",
              "retry delay" in cd, repr(cd))
        check("TS base keeps '; calls scale'", "calls scale" in cd, repr(cd))
        check("a node with NO extra local signal (app) is NOT written", "app" not in sc, str(sorted(sc)))
        check("stats: enriched counts only CHANGED summaries (2), unchanged reported separately (1)",
              st.get("enriched") == 2 and st.get("unchanged") == 1, str(st))
        check("stats: nothing left 'remaining' after a full run", st.get("remaining") == 0, str(st))

        # apply_sidecar (the sync seam) overrides with the richer text and keeps the calls clause.
        nodes = _nodes_for(root)
        E.apply_sidecar(root, nodes)
        by = {n["name"]: n for n in nodes}
        check("apply_sidecar: CleanPath gets the harvested summary (with its calls clause)",
              "canonical URL path" in by["CleanPath"]["body_summary"]
              and "calls app" in by["CleanPath"]["body_summary"], by["CleanPath"]["body_summary"])
        check("apply_sidecar: the un-enriched node keeps its default summary",
              by["app"]["body_summary"] == "app: function", by["app"]["body_summary"])


def gate_line_shift(E) -> None:
    print("\n-- a pure line shift keeps summaries (re-homed, no orphans, no LLM re-bill) --")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write_repo(root)
        E._rebuild_code = lambda r: (_nodes_for(Path(r)), [], [], "graphify")
        E.enrich(root, deterministic=True)
        before = E.load_sidecar(root)
        _write_repo(root, go_prefix="// header 1\n// header 2\n\n")   # shifts every Go def by 3 lines
        st = E.enrich(root, deterministic=True)
        after = E.load_sidecar(root)
        live = {E.node_id(n["source_file"], n["name"], n["start_line"]) for n in _nodes_for(root)}
        check("re-run after a line shift: the moved node is already done (not re-enriched)",
              st.get("enriched") == 0, str(st))
        check("no orphan entries left behind (every sidecar id is a live node)",
              set(after) <= live, str(sorted(set(after) - live)))
        check("the moved node's summary survived under its NEW node id",
              any("canonical URL path" in e.get("summary", "") for nid, e in after.items() if nid in live),
              str(list(after)))
        check("sidecar did not grow on a pure shift", len(after) == len(before), f"{len(before)} -> {len(after)}")
        nodes = _nodes_for(root)
        E.apply_sidecar(root, nodes)
        cp = next(n for n in nodes if n["name"] == "CleanPath")
        check("apply_sidecar applies to the moved node", "canonical URL path" in cp["body_summary"],
              cp["body_summary"])
        # the status count is honest (live entries only)
        check("enrich --status counts the live entries only", E.status(root)["cached"] == len(after),
              str(E.status(root)))


def gate_llm_no_rebill(E) -> None:
    print("\n-- LLM tier: a line shift re-sends NOTHING for unchanged spans --")

    class _T:
        def __init__(self):
            self.calls = []

        def __call__(self, prompt):
            self.calls.append(prompt)
            n = prompt.count("### Unit ")
            return {"text": json.dumps({str(i): f"Does thing {i}." for i in range(1, n + 1)}),
                    "cost_usd": 0.001, "is_error": False}

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write_repo(root)
        E._rebuild_code = lambda r: (_nodes_for(Path(r)), [], [], "graphify")
        t1 = _T()
        E.enrich(root, model="m1", claude_transport=t1)
        check("first LLM run makes a transport call", len(t1.calls) >= 1, str(len(t1.calls)))
        _write_repo(root, go_prefix="// a\n// b\n")
        t2 = _T()
        st = E.enrich(root, model="m1", claude_transport=t2)
        check("after a pure line shift: ZERO transport calls (no re-bill)", len(t2.calls) == 0,
              f"{len(t2.calls)} calls; stats={st}")


def gate_api_bounds(E) -> None:
    print("\n-- enrich() API: a negative max_nodes enriches nothing --")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write_repo(root)
        E._rebuild_code = lambda r: (_nodes_for(Path(r)), [], [], "graphify")
        st = E.enrich(root, deterministic=True, max_nodes=-5)
        check("max_nodes=-5 -> 0 enriched (no slice semantics)", st.get("enriched") == 0, str(st))


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 72)
    print("DeepMap — enrich --deterministic harvest/honesty gates (W3: C7; no Neo4j, no spend)")
    print("=" * 72)
    for gate in (gate_harvest_and_base, gate_line_shift, gate_llm_no_rebill, gate_api_bounds):
        E = _load_enrich()
        try:
            gate(E)
        except Exception as exc:  # noqa: BLE001 — a crashing gate is RED, not a crash
            import traceback
            traceback.print_exc()
            check(f"{gate.__name__} ran without crashing ({type(exc).__name__}: {exc})", False)
    print("\n" + "=" * 72)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
