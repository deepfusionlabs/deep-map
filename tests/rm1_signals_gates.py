#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j, no-spend gates for the RM-1 RICHER-SIGNALS extension (design §2).

The foundation logged a single BINARY signal (first-access). This extension captures a graded,
negatives-aware, intent-bucketed signal — still opt-in, still zero-egress, still frozen ranking.
Locks:
  - GRADED relevance: Read -> weak (0.5), Edit/Write/impact -> strong (1.0); the strongest grade wins,
  - SKIP-ABOVE negatives (cascade model): returned ids ranked above the top accessed id, un-accessed,
    become soft negatives — never inventing an id that wasn't returned,
  - correlation: one-search->many-accesses accumulates; many-searches->one-access credits nearest,
  - v1 BACK-COMPAT: a v1 record folds to the SAME accessed_ids as the binary path (no silent change),
  - query features -> 4 interpretable intent buckets; malformed input -> 'default' (never raises),
  - schema v2: build_record carries session_id + features; the hook grades kind + carries session_id,
  - opt-in query REDACTION hashes the stored query but leaves qid (hence folding) unchanged.

Pure + hermetic — no Neo4j, no network, no spend.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build"
HOOKS = REPO / "hooks"

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
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)
    return m


def _search(L, qid_query, ids, ts="1", **kw):
    """A search record whose `returned` is in rank order `ids` (file f/<id>.py)."""
    returned = [{"id": i, "file": f"f/{i}.py", "name": i, "line": 1} for i in ids]
    per_arm = {"vector": ids, "bm25": ids, "graph_weighted": ids}
    return L.build_record(qid_query, per_arm, returned, ts=ts, **kw)


def _access(paths=None, symbols=None, kind="", ts="2"):
    return {"type": "access", "v": 2, "paths": list(paths or []),
            "symbols": list(symbols or []), "kind": kind, "ts": ts}


def gate_graded_fold(L) -> None:
    print("\n-- graded fold: Read=0.5, Edit/impact=1.0, strongest grade wins --")
    s = _search(L, "q", ["A", "B", "C"], ts="1")
    # Read of A -> 0.5.
    folded = L.fold_graded([s, _access(paths=["f/A.py"], kind="read", ts="2")])
    check("Read grades the accessed id at 0.5", folded[0]["graded"].get("A") == 0.5, str(folded[0]["graded"]))
    # Edit of B -> 1.0.
    folded = L.fold_graded([s, _access(paths=["f/B.py"], kind="edit", ts="2")])
    check("Edit grades the accessed id at 1.0", folded[0]["graded"].get("B") == 1.0, str(folded[0]["graded"]))
    # impact symbol -> 1.0.
    folded = L.fold_graded([s, _access(symbols=["C"], kind="impact", ts="2")])
    check("deepmap_impact grades the symbol at 1.0", folded[0]["graded"].get("C") == 1.0, str(folded[0]["graded"]))
    # Read THEN Edit of the same id -> max (1.0).
    folded = L.fold_graded([s, _access(paths=["f/A.py"], kind="read", ts="2"),
                            _access(paths=["f/A.py"], kind="edit", ts="3")])
    check("strongest grade wins (Read then Edit -> 1.0)", folded[0]["graded"].get("A") == 1.0, str(folded[0]["graded"]))
    # unknown kind -> weak default.
    folded = L.fold_graded([s, _access(paths=["f/A.py"], kind="mystery", ts="2")])
    check("unknown access kind -> weak default 0.5", folded[0]["graded"].get("A") == 0.5, str(folded[0]["graded"]))


def gate_skip_above_negatives(L) -> None:
    print("\n-- skip-above negatives (cascade model) --")
    s = _search(L, "q", ["A", "B", "C", "D"], ts="1")   # A is top-ranked, D last
    # Access C -> negatives are the un-accessed ids ranked above it: A, B.
    folded = L.fold_graded([s, _access(paths=["f/C.py"], kind="read", ts="2")])
    check("access of C -> negatives [A, B] (ranked above, un-accessed)",
          folded[0]["negatives"] == ["A", "B"], str(folded[0]["negatives"]))
    # Access the TOP result A -> nothing above it -> no negatives.
    folded = L.fold_graded([s, _access(paths=["f/A.py"], kind="read", ts="2")])
    check("access of the top result -> no negatives", folded[0]["negatives"] == [], str(folded[0]["negatives"]))
    # No access -> no negatives (no anchor).
    folded = L.fold_graded([s])
    check("no access -> no negatives", folded[0]["negatives"] == [], str(folded[0]["negatives"]))
    # Negatives never include a non-returned id (accessing C, the negatives are a subset of returned).
    folded = L.fold_graded([s, _access(paths=["f/C.py"], kind="read", ts="2")])
    check("negatives are a subset of returned ids (never invented)",
          set(folded[0]["negatives"]) <= {"A", "B", "C", "D"}, str(folded[0]["negatives"]))
    # An accessed id above the anchor is NOT a negative (only un-accessed skips).
    folded = L.fold_graded([s, _access(paths=["f/A.py"], kind="read", ts="2"),
                            _access(paths=["f/C.py"], kind="read", ts="3")])
    check("an accessed id above the anchor is not a negative", "A" not in folded[0]["negatives"], str(folded[0]["negatives"]))


def gate_correlation_cardinality(L) -> None:
    print("\n-- correlation: one-search->many-accesses; many-searches->one-access (nearest) --")
    # one search surfaced A and B; both accessed -> both credited to that search.
    s = _search(L, "q", ["A", "B", "C"], ts="1")
    folded = L.fold_graded([s, _access(paths=["f/A.py"], kind="read", ts="2"),
                            _access(paths=["f/B.py"], kind="edit", ts="3")])
    check("one-search->many-accesses: both A and B credited",
          set(folded[0]["accessed_ids"]) == {"A", "B"}, str(folded[0]["accessed_ids"]))
    # two searches both surfaced X; an access credits the NEAREST preceding one only.
    s1 = _search(L, "q1", ["X", "Y"], ts="1")
    s2 = _search(L, "q2", ["X", "Z"], ts="2")
    folded = L.fold_graded([s1, s2, _access(paths=["f/X.py"], kind="read", ts="3")])
    by_qid = {r["qid"]: r for r in folded}
    check("many-searches->one-access: nearest (s2) credited, not s1",
          by_qid[s2["qid"]]["accessed_ids"] == ["X"] and by_qid[s1["qid"]]["accessed_ids"] == [],
          f's1={by_qid[s1["qid"]]["accessed_ids"]} s2={by_qid[s2["qid"]]["accessed_ids"]}')


def gate_v1_backcompat(L) -> None:
    print("\n-- v1 back-compat: a v1 record folds to the SAME accessed_ids as the binary path --")
    # A hand-written v1-style search record (no features/session_id) with a preset accessed id.
    v1 = {"type": "search", "v": 1, "qid": "old1", "query": "q",
          "per_arm_candidate_ids": {"vector": ["A"], "bm25": ["A"], "graph_weighted": ["A"]},
          "returned": [{"id": "A", "file": "f/A.py", "name": "A", "line": 1}],
          "accessed_ids": ["A"]}
    graded = L.fold_graded([v1])
    binary = L.fold_access_events([v1])
    check("v1 record: fold_graded preserves accessed_ids ['A']", graded[0]["accessed_ids"] == ["A"], str(graded[0]))
    check("v1 record: graded seeds the preset positive at 0.5", graded[0]["graded"].get("A") == 0.5, str(graded[0]["graded"]))
    check("fold_access_events == fold_graded accessed_ids (binary view identical)",
          binary[0]["accessed_ids"] == graded[0]["accessed_ids"], f'{binary[0]["accessed_ids"]}')
    check("binary wrapper omits graded/negatives (exact v1 output shape)",
          "graded" not in binary[0] and "negatives" not in binary[0], str(list(binary[0].keys())))


def gate_query_features_and_bucket(L) -> None:
    print("\n-- query_features -> 4 intent buckets; malformed -> 'default', never raises --")
    b = lambda q: L.intent_bucket(L.query_features(q))
    check("impact cue -> 'impact' bucket", b("who calls parse_config") == "impact", b("who calls parse_config"))
    check("short identifier -> 'symbol_short'", b("check_auth") == "symbol_short", b("check_auth"))
    check("long NL query -> 'nl_long'",
          b("find the function that validates the user session token thoroughly") == "nl_long",
          b("find the function that validates the user session token thoroughly"))
    check("plain short words -> 'default'", b("auth check") == "default", b("auth check"))
    # camelCase counts as a symbol.
    check("camelCase token -> has_symbol_token True", L.query_features("parseConfig")["has_symbol_token"] is True)
    # Malformed never raises.
    for bad in (None, 123, [], {"x": 1}):
        try:
            f = L.query_features(bad)
            ok = isinstance(f, dict) and L.intent_bucket(bad if not isinstance(bad, dict) else f) in {
                "impact", "symbol_short", "nl_long", "default"}
        except Exception as exc:
            ok = False
            f = f"raised {exc}"
        check(f"query_features/intent_bucket malformed input -> no raise ({bad!r})", ok, str(f))
    check("intent_bucket(non-dict) -> 'default'", L.intent_bucket("not a dict") == "default")


def gate_schema_v2(L) -> None:
    print("\n-- schema v2: build_record carries session_id + features --")
    rec = _search(L, "who calls parse_config", ["A", "B"], ts="1", session_id="sess-9")
    check("record version is 2", rec.get("v") == 2, str(rec.get("v")))
    check("carries session_id", rec.get("session_id") == "sess-9", str(rec.get("session_id")))
    feats = rec.get("features")
    check("carries a features dict with the 4 feature keys",
          isinstance(feats, dict) and {"len_bucket", "has_symbol_token", "is_impact_query", "fts_heavy"} <= set(feats),
          str(feats))
    check("features reflect the query (impact cue detected)", feats.get("is_impact_query") is True, str(feats))


def gate_redaction(L) -> None:
    print("\n-- opt-in query redaction: stored query hashed, qid (folding) unchanged --")
    saved = os.environ.pop("DEEPMAP_LOG_REDACT", None)
    try:
        plain = L.build_record("secret proprietary query", {"vector": ["A"]},
                               [{"id": "A", "file": "f/A.py", "name": "A", "line": 1}], ts="1")
        os.environ["DEEPMAP_LOG_REDACT"] = "1"
        redacted = L.build_record("secret proprietary query", {"vector": ["A"]},
                                  [{"id": "A", "file": "f/A.py", "name": "A", "line": 1}], ts="1")
        check("default: raw query stored", plain["query"] == "secret proprietary query", plain["query"])
        check("redact on: stored query is a hash", str(redacted["query"]).startswith("redacted:"), redacted["query"])
        check("redact on: qid UNCHANGED (folding/determinism preserved)", redacted["qid"] == plain["qid"],
              f'{redacted["qid"]} vs {plain["qid"]}')
    finally:
        os.environ.pop("DEEPMAP_LOG_REDACT", None)
        if saved is not None:
            os.environ["DEEPMAP_LOG_REDACT"] = saved


def gate_hook_kind_and_session(H) -> None:
    print("\n-- hook: grades the action kind + carries session_id (always additive) --")
    check("_action_kind Read -> 'read'", H._action_kind("read") == "read")
    check("_action_kind Edit -> 'edit'", H._action_kind("edit") == "edit")
    check("_action_kind Write -> 'edit'", H._action_kind("write") == "edit")
    check("_action_kind deepmap_impact -> 'impact'", H._action_kind("deepmap_impact") == "impact")
    check("_action_kind Grep -> '' (not an access)", H._action_kind("grep") == "")
    ev = H.build_access_event(["f/a.py"], [], kind="read", session_id="sess-1")
    check("build_access_event carries kind + session_id + v2",
          ev.get("kind") == "read" and ev.get("session_id") == "sess-1" and ev.get("v") == 2, str(ev))
    # extract_access return shape is UNCHANGED (no 'kind' key) — its own gates still pass.
    r = H.extract_access({"tool_name": "Read", "tool_input": {"file_path": "f/a.py"}})
    check("extract_access return shape unchanged (paths+symbols only)",
          set(r.keys()) == {"paths", "symbols"}, str(r))


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 68)
    print("DeepMap — RM-1 richer-signals gates (graded/negatives/intent; no Neo4j, no spend)")
    print("=" * 68)
    L = _load("retrieval_log", BUILD)
    H = _load("deepmap_access_hook", HOOKS)

    gate_graded_fold(L)
    gate_skip_above_negatives(L)
    gate_correlation_cardinality(L)
    gate_v1_backcompat(L)
    gate_query_features_and_bucket(L)
    gate_schema_v2(L)
    gate_redaction(L)
    gate_hook_kind_and_session(H)

    print("\n" + "=" * 68)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
