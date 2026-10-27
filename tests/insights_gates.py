#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j, no-spend gates for `deepmap insights` (build/insights.py) — a LOCAL, READ-ONLY
summary of the opt-in retrieval log. It writes NOTHING (not even under .deepmap/) and changes no
ranking. Honest about sample sizes: every metric reports n; below the tuner's cold-start
thresholds it prints counts only and refuses conclusions.

Locks:
  - the metric DEFINITIONS over the v2 record schema: arm win = the arm that ranked a
    BEST-GRADED id highest (ties share 1/m fractionally; all-miss counted as no_arm_surfaced),
    zero-result = empty `returned`, abandonment = returned-but-never-accessed WITH the
    no-access-data honesty guard (a missing hook is 'not captured', never '100% abandoned'),
  - redaction honesty: raw query text appears NOWHERE; redacted hashes are never shown as
    examples; intent buckets still work (features were computed at log time),
  - refusal below threshold: rates are null in JSON and absent in text below 150 labeled
    (40 per bucket) — counts only, no conclusions,
  - the tuner dry-run writes NOTHING (instance.json byte-identical before/after),
  - the CLI subcommand: exit 0 on an empty root, all sections, --json, --since,
  - never raises on malformed logs; the naive win definitions provably DISAGREE (teeth);
    rendered copy contains no forbidden phrases.
"""
from __future__ import annotations

import importlib.util
import json
import os
import random
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build"
CLI = BUILD / "deepmap_cli.py"
PY = sys.executable

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


def _lq(qid, per_arm, graded, bucket="default"):
    """A hand-built labeled query in build_qrels' output shape."""
    return {"qid": qid, "per_arm": per_arm, "positives": set(graded), "graded": dict(graded),
            "negatives": [], "features": {}, "bucket": bucket}


def _seed_records(L, n=8, accessed=True):
    """n searches (+ n access events when accessed) with bm25 surfacing the used 'p' first."""
    recs = []
    per_arm = {"vector": ["x", "p"], "bm25": ["p", "x"], "graph_weighted": ["x", "p"]}
    for i in range(n):
        returned = [{"id": r, "file": f"f/{r}.py", "name": r, "line": 1} for r in ("x", "p")]
        recs.append(L.build_record(f"how does sync decide staleness {i}", per_arm, returned, ts=f"t{i:03d}"))
        if accessed:
            recs.append({"type": "access", "ts": f"t{i:03d}b", "paths": ["f/p.py"], "symbols": [],
                         "kind": "edit"})
    return recs


EXPECT_KEYS = {"v", "root", "generated_utc", "window", "counts", "sufficient", "thresholds",
               "intents", "zero_result", "abandonment", "arm_wins", "tuner"}


def gate_module_contract(I, T) -> None:
    print("\n-- module contract: thresholds imported, full v1 shape on empty, read-only --")
    check("thresholds equal the tuner's (single source of truth)",
          I.DEFAULT_MIN_LABELED == T.DEFAULT_MIN_LABELED == 150
          and I.DEFAULT_MIN_BUCKET == T.DEFAULT_MIN_LABELED_BUCKET == 40)
    src = (BUILD / "insights.py").read_text(encoding="utf-8")
    check("thresholds are IMPORTED, not re-declared (drift-lock)",
          "_tw.DEFAULT_MIN_LABELED" in src and "_tw.DEFAULT_MIN_LABELED_BUCKET" in src)
    data = I.build_insights([])
    check("empty input -> full v1 shape", EXPECT_KEYS.issubset(set(data)), str(sorted(data)))
    check("empty input -> zero counts + insufficient",
          data["counts"]["n_search"] == 0 and data["sufficient"] is False)
    check("v is 1", data.get("v") == 1)
    out = I.insights_for_root(None)
    check("insights_for_root(None) -> full shape, no raise", EXPECT_KEYS.issubset(set(out)))
    txt = I.render_text(data)
    check("empty render mentions enabling logging", "enable" in txt.lower() and "RM1-ENABLE" in txt, txt[:200])
    check("header carries the read-only disclaimer",
          "read-only" in txt and "writes nothing" in txt and "changes no ranking" in txt, txt[:200])


def gate_arm_win_definition(I) -> None:
    print("\n-- arm win definition: best-graded target, first-rank argmin, fractional ties --")
    # Sole winner: bm25 ranks the (only) positive first.
    labeled = [_lq("q1", {"vector": ["x", "p"], "bm25": ["p", "x"], "graph_weighted": ["x", "y"]},
                   {"p": 1.0})]
    w = I.arm_win_rates(labeled, min_labeled=1, min_bucket=1)
    check("sole winner takes 1.0", w["overall"]["wins"]["bm25"] == 1.0
          and w["overall"]["wins"]["vector"] == 0.0, str(w["overall"]["wins"]))
    # Two-arm tie: vector + bm25 both rank p first -> 0.5 each; wins sum to 1 per query.
    labeled = [_lq("q2", {"vector": ["p", "x"], "bm25": ["p", "x"], "graph_weighted": ["x", "p"]},
                   {"p": 1.0})]
    w = I.arm_win_rates(labeled, min_labeled=1, min_bucket=1)
    check("two-arm tie -> 0.5 each", w["overall"]["wins"]["vector"] == 0.5
          and w["overall"]["wins"]["bm25"] == 0.5, str(w["overall"]["wins"]))
    check("tie shares sum to exactly 1", abs(sum(w["overall"]["wins"].values()) - 1.0) < 1e-9)
    # Best-grade-only: a weak Read (0.5) ranked first must NOT beat the strong Edit (1.0) target.
    labeled = [_lq("q3", {"vector": ["r", "e"], "bm25": ["e", "r"], "graph_weighted": []},
                   {"r": 0.5, "e": 1.0})]
    w = I.arm_win_rates(labeled, min_labeled=1, min_bucket=1)
    check("best-grade-only: the Edit target decides (bm25 wins)",
          w["overall"]["wins"]["bm25"] == 1.0 and w["overall"]["wins"]["vector"] == 0.0,
          str(w["overall"]["wins"]))
    # No arm surfaced it.
    labeled = [_lq("q4", {"vector": ["a"], "bm25": ["b"], "graph_weighted": ["c"]}, {"zz": 1.0})]
    w = I.arm_win_rates(labeled, min_labeled=1, min_bucket=1)
    check("all-miss -> no_arm_surfaced counted, zero wins",
          w["overall"]["no_arm_surfaced"] == 1 and sum(w["overall"]["wins"].values()) == 0.0)
    # Per-bucket split.
    labeled = [
        _lq("q5", {"vector": ["p"], "bm25": ["x", "p"], "graph_weighted": []}, {"p": 1.0}, bucket="impact"),
        _lq("q6", {"vector": ["x", "p"], "bm25": ["p"], "graph_weighted": []}, {"p": 1.0}, bucket="nl_long"),
    ]
    w = I.arm_win_rates(labeled, min_labeled=1, min_bucket=1)
    check("per-bucket split follows the qrel bucket",
          w["by_bucket"]["impact"]["wins"]["vector"] == 1.0
          and w["by_bucket"]["nl_long"]["wins"]["bm25"] == 1.0, str(w["by_bucket"]))
    check("bucket ns reported", w["by_bucket"]["impact"]["n_labeled"] == 1
          and w["overall"]["n_labeled"] == 2)


def gate_zero_result_and_intents(I, L) -> None:
    print("\n-- zero-result + intent mix: descriptive counts, deterministic, example-carrying --")
    recs = _seed_records(L, n=4)
    recs.append(L.build_record("quaternion slerp helper nothing found at all here",
                               {"vector": [], "bm25": [], "graph_weighted": []}, [], ts="t900"))
    recs.append(L.build_record("who calls prune_stale_edge_types",
                               {"vector": ["a"], "bm25": ["a"], "graph_weighted": ["a"]},
                               [{"id": "a", "file": "f/a.py", "name": "a", "line": 1}], ts="t901"))
    searches = [r for r in recs if r.get("type") == "search"]
    z = I.zero_result(searches)
    check("zero-result counted (1 of 6)", z["n"] == 1 and z["n_search"] == 6, str(z))
    check("zero-result example surfaces the query", any("quaternion" in str(e.get("query", ""))
          for e in z.get("examples", [])), str(z.get("examples")))
    m = I.intent_mix(searches)
    check("intent buckets counted", m["buckets"].get("impact", {}).get("n") == 1
          and m["buckets"].get("nl_long", {}).get("n", 0) >= 1, str({b: v.get("n") for b, v in m["buckets"].items()}))
    check("examples per bucket", any("prune_stale_edge_types" in q
          for q in m["buckets"].get("impact", {}).get("examples", [])), str(m["buckets"].get("impact")))
    m2 = I.intent_mix(searches)
    check("deterministic", m == m2)


def gate_redaction_honesty(I, L) -> None:
    print("\n-- redaction honesty: raw text NEVER shown; hashes never shown as examples --")
    saved = os.environ.get("DEEPMAP_LOG_REDACT")
    try:
        os.environ["DEEPMAP_LOG_REDACT"] = "1"
        recs = []
        per_arm = {"vector": ["x", "p"], "bm25": ["p", "x"], "graph_weighted": ["x", "p"]}
        for i in range(4):
            returned = [{"id": r, "file": f"f/{r}.py", "name": r, "line": 1} for r in ("x", "p")]
            recs.append(L.build_record(f"secret proprietary algo {i}", per_arm, returned, ts=f"t{i}"))
            recs.append({"type": "access", "ts": f"t{i}b", "paths": ["f/p.py"], "symbols": [], "kind": "read"})
    finally:
        if saved is None:
            os.environ.pop("DEEPMAP_LOG_REDACT", None)
        else:
            os.environ["DEEPMAP_LOG_REDACT"] = saved
    check("fixture sanity: queries stored redacted", all(str(r.get("query", "")).startswith("redacted:")
          for r in recs if r.get("type") == "search"))
    data = I.build_insights(recs, with_tuner=False)
    blob = I.render_text(data) + json.dumps(data)
    check("raw query text appears NOWHERE", "secret proprietary algo" not in blob)
    check("redacted hashes never shown as examples", "redacted:" not in json.dumps(
        [data["intents"]["buckets"], data["zero_result"].get("examples"),
         data["abandonment"].get("examples")]))
    check("n_redacted counted", data["counts"]["n_redacted_queries"] == 4, str(data["counts"]))
    check("intent buckets still computed (features logged pre-redaction)",
          sum(v.get("n", 0) for v in data["intents"]["buckets"].values()) == 4)
    check("the redaction note is rendered", "redact" in I.render_text(data).lower())


def gate_abandonment_honesty(I, L) -> None:
    print("\n-- abandonment: the no-access-data guard + pre-hook exclusion --")
    # (a) NO access signal at all -> not computed, honestly explained.
    recs = _seed_records(L, n=5, accessed=False)
    data = I.build_insights(recs, with_tuner=False)
    ab = data["abandonment"]
    check("no access data -> status no_access_data", ab.get("status") == "no_access_data", str(ab))
    txt = I.render_text(data)
    check("copy explains the hook is not registered (never '100% abandoned')",
          "hook" in txt and "not captured" in txt, txt[-400:])
    check("no abandonment NUMBER is rendered in that state", "earned no captured" not in txt)
    # (b) With signal: 3 pre-hook searches excluded; 2 of 5 considered are abandoned.
    recs = []
    per_arm = {"vector": ["x", "p"], "bm25": ["p", "x"], "graph_weighted": ["x", "p"]}
    for i in range(8):   # s0..s2 pre-hook; s3,s5,s7 accessed; s4,s6 abandoned
        rid = f"p{i}"
        returned = [{"id": rid, "file": f"f/{rid}.py", "name": rid, "line": 1}]
        recs.append(L.build_record(f"query {i}", {k: [rid] for k in per_arm}, returned, ts=f"t{i:03d}"))
        if i in (3, 5, 7):
            recs.append({"type": "access", "ts": f"t{i:03d}b", "paths": [f"f/p{i}.py"], "symbols": [],
                         "kind": "edit"})
    data = I.build_insights(recs, with_tuner=False)
    ab = data["abandonment"]
    check("status ok with signal", ab.get("status") == "ok", str(ab))
    check("abandoned = returned-but-never-accessed AFTER the hook went live (2)",
          ab.get("n_abandoned") == 2, str(ab))
    check("considered = labeled + post-hook (5)", ab.get("n_considered") == 5, str(ab))
    check("pre-hook searches excluded + reported (3)", ab.get("n_pre_hook") == 3, str(ab))
    txt = I.render_text(data)
    check("the standing upper-bound caveat is ALWAYS printed with the metric",
          "upper bound" in txt and "opt-in" in txt, txt[-500:])


def gate_refusal_below_threshold(I, L) -> None:
    print("\n-- refusal below threshold: counts only, rates null, no percent signs --")
    recs = _seed_records(L, n=5)
    data = I.build_insights(recs, min_labeled=6, min_bucket=3, with_tuner=False)
    check("below the boundary -> insufficient", data["sufficient"] is False)
    check("rates are NULL in JSON below threshold", data["arm_wins"]["overall"]["rates"] is None)
    txt = I.render_text(data)
    check("the refusal banner renders", "insufficient data (n_labeled=" in txt and "no conclusions" in txt, txt[:400])
    wins_section = txt[txt.find("which arm"):txt.find("tuner")] if "which arm" in txt else txt
    check("no percent sign in the wins section when refused", "%" not in wins_section, wins_section[:200])
    check("counts still shown (honest, descriptive)", "counts only" in txt, txt[:600])
    data = I.build_insights(_seed_records(L, n=6), min_labeled=6, min_bucket=3, with_tuner=False)
    check("at the boundary -> sufficient flips on", data["sufficient"] is True)
    check("rates populated when sufficient", isinstance(data["arm_wins"]["overall"]["rates"], dict),
          str(data["arm_wins"]["overall"]))
    # Globally sufficient but a small bucket is still refused per-bucket.
    per_bucket = data["arm_wins"]["by_bucket"]
    small = {b: v for b, v in per_bucket.items() if v["n_labeled"] < 3}
    check("a sub-threshold bucket row refuses its rate",
          all(v["rates"] is None for v in small.values()) if small else True, str(small))


def gate_tuner_dry_run_no_write(I, T, L) -> None:
    print("\n-- tuner dry-run: faithful, and writes NOTHING --")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        recs = _seed_records(L, n=8)
        for r in recs:
            L.append_record(root, r)
        data = I.build_insights(recs, root=root, min_labeled=4)
        check("would-be matches the REAL tuner", data["tuner"]["would_be"]["arm_weights"]
              == T.tune_profiles(records=recs, min_labeled=4)["arm_weights"], str(data["tuner"]["would_be"]))
        check("reason carried verbatim", data["tuner"]["would_be"]["reason"]
              == T.tune_profiles(records=recs, min_labeled=4)["reason"])
        check("written:false is hard-coded", data["tuner"]["written"] is False)
        check("NO instance.json was created", not (root / ".deepmap" / "instance.json").exists())
        # A pre-existing instance.json is byte-identical after, and `current` reflects it.
        inst = root / ".deepmap" / "instance.json"
        inst.write_text(json.dumps({"learned_fusion": {"arm_weights": {"vector": 1.5, "bm25": 0.5,
                        "graph_weighted": 1.0}, "applied": False, "reason": "learned_beats_equal"}}),
                        encoding="utf-8")
        before = inst.read_bytes()
        data = I.build_insights(recs, root=root, min_labeled=4)
        check("pre-existing instance.json byte-identical after", inst.read_bytes() == before)
        check("current reflected when present",
              data["tuner"]["current"]["arm_weights"]["vector"] == 1.5, str(data["tuner"]["current"]))
        txt = I.render_text(data)
        check("render points at `deepmap tune --report` (no gate_reasons duplication)",
              "tune --report" in txt, txt[-300:])
        check("render says nothing written", "nothing written" in txt.lower() or "dry-run" in txt.lower())


def gate_cli_subprocess(L) -> None:
    print("\n-- the CLI subcommand: empty root exit 0; sections; --json; --since --")
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"

    def run(args, cwd):
        return subprocess.run([PY, str(CLI), *args], cwd=str(cwd), capture_output=True, text=True,
                              env=env, timeout=120)

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        p = run(["insights", str(root)], cwd=REPO)
        check("empty root: exit 0 (an honest empty report is a successful report)",
              p.returncode == 0, (p.stdout + p.stderr)[-300:])
        check("empty root: mentions enabling logging", "enable" in p.stdout.lower())
        for r in _seed_records(L, n=6):
            L.append_record(root, r)
        p = run(["insights", str(root)], cwd=REPO)
        check("seeded root: exit 0", p.returncode == 0, (p.stdout + p.stderr)[-300:])
        out = p.stdout
        check("header disclaimer present", "read-only" in out and "writes nothing" in out, out[:200])
        for marker in ("query intents", "zero-result", "never used", "which arm", "tuner would produce"):
            check(f"section present: {marker}", marker in out, out[:800])
        check("insufficient banner on a small log", "insufficient data" in out)
        p = run(["insights", str(root), "--json"], cwd=REPO)
        check("--json: exit 0 + parses", p.returncode == 0 and json.loads(p.stdout).get("v") == 1)
        j = json.loads(p.stdout)
        check("--json carries the core keys", all(k in j for k in ("counts", "sufficient", "arm_wins", "tuner")))
        # --since: a two-era log; the older era drops out.
        p = run(["insights", str(root), "--since", "t003", "--json"], cwd=REPO)
        j = json.loads(p.stdout)
        check("--since drops the older era", j["counts"]["n_search"] == 3, str(j["counts"]))


def gate_never_raise_malformed(I) -> None:
    print("\n-- never raises on malformed logs (seeded fuzz) --")
    rng = random.Random(20260711)
    garbage_pool = [
        {"type": "search"}, {"type": "search", "returned": "not-a-list", "per_arm_candidate_ids": 7},
        {"type": "search", "returned": [{"id": None}], "features": "junk", "ts": 42,
         "query": None, "accessed_ids": {"a": 1}},
        {"type": "access", "paths": 13, "symbols": None, "kind": ["x"]},
        {"type": 99}, "not a dict", None, 3.14,
        {"type": "search", "per_arm_candidate_ids": {"vector": "x", "bm25": [1, None], "graph_weighted": {}}},
        {"type": "search", "returned": [], "ts": None, "query": b"".decode() if True else ""},
    ]
    raised = 0
    for _ in range(100):
        recs = [rng.choice(garbage_pool) for _ in range(rng.randint(1, 12))]
        try:
            data = I.build_insights(recs, with_tuner=(rng.random() < 0.3))
            I.render_text(data)
            json.dumps(data)
        except Exception:
            raised += 1
    check("100 seeded malformed logs -> zero raises", raised == 0, f"{raised} raised")
    check("shape holds under garbage", EXPECT_KEYS.issubset(set(I.build_insights([None, "x"]))))


def gate_has_teeth(I, L) -> None:
    print("\n-- TEETH: naive definitions disagree; rendered copy is honest --")
    # Naive 1: first arm in ARM_NAMES order containing ANY graded id wins outright (no ties).
    tie = _lq("t1", {"vector": ["p", "x"], "bm25": ["p", "x"], "graph_weighted": ["x", "p"]}, {"p": 1.0})
    naive1 = "vector"   # ARM_NAMES order, winner-take-all
    w = I.arm_win_rates([tie], min_labeled=1, min_bucket=1)["overall"]["wins"]
    check("fractional ties DISAGREE with winner-take-all-first-arm", w["vector"] == 0.5 != (naive1 == "vector"),
          str(w))
    # Naive 2: any-positive (ignores grades).
    graded_q = _lq("t2", {"vector": ["r", "e"], "bm25": ["e", "r"], "graph_weighted": []},
                   {"r": 0.5, "e": 1.0})
    naive2_winner = "vector"   # r (any positive) is vector's rank 1
    w = I.arm_win_rates([graded_q], min_labeled=1, min_bucket=1)["overall"]["wins"]
    check("best-grade-only DISAGREES with any-positive", w["bm25"] == 1.0 and naive2_winner == "vector", str(w))
    # Rendered copy of a POPULATED report contains no forbidden phrases.
    data = I.build_insights(_seed_records(L, n=6), min_labeled=4, min_bucket=2, with_tuner=True)
    txt = I.render_text(data).lower()
    for phrase in ("self-improving", "runtime learning", "auto-optimi", "search-quality monitoring",
                   "search quality monitoring", "health report", "dashboard", "analytics"):
        check(f"forbidden phrase absent: {phrase}", phrase not in txt)


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 68)
    print("DeepMap — `deepmap insights` gates (local read-only log summary; no Neo4j, no spend)")
    print("=" * 68)
    L = _load("retrieval_log", BUILD)
    if str(BUILD) not in sys.path:
        sys.path.insert(0, str(BUILD))
    T = _load("tune_weights", BUILD)
    try:
        I = _load("insights", BUILD)
    except Exception as e:
        check("build/insights.py loads", False, repr(e))
        print(f"\nRESULT: RED — {_PASS}/{_PASS + _FAIL} gates passed.")
        return 1

    gate_module_contract(I, T)
    gate_arm_win_definition(I)
    gate_zero_result_and_intents(I, L)
    gate_redaction_honesty(I, L)
    gate_abandonment_honesty(I, L)
    gate_refusal_below_threshold(I, L)
    gate_tuner_dry_run_no_write(I, T, L)
    gate_cli_subprocess(L)
    gate_never_raise_malformed(I)
    gate_has_teeth(I, L)

    print("\n" + "=" * 68)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
