#!/usr/bin/env python3
"""0.28.3 (O10) — the RM-1 G4 evidence bridge measures what it claims, on the weights it claims.

Findings (DeepInit refresh 2026-09-29, validated):
  * ISS-benchmark:009 (D17) — weight_ab.score_answer_set (reused by enrich_ab) read a TOP-LEVEL
    `ground_truth_set`; the shipped benchmark/questions.yaml nests it under `answer_key`, so every shipped
    question scored 0% — a perfect answer included — and the A/B read as an honest null.
  * D1 — the documented "learned" capture only set DEEPMAP_APPLY_LEARNED in the served MCP server's env; the
    live gate also needs learned_fusion.applied:true (+ beats_equal/significant) in the SERVED project's
    (the clone's) .deepmap/instance.json, which nothing wrote -> the A/B compared equal with equal.
  * D2 — `benchmark_evidence` was not bound to the weights it measured: after a re-tune changed the weights,
    `deepmap tune --apply` still passed on the old evidence and put never-benchmarked weights live.
  * ISS-benchmark:012 (tune_weights part) — apply_learned's refusal sent the operator to
    `run_benchmark.py --ab-weights`, which only prints a plan.

Locks (hermetic: temp dirs, the FROZEN learned_fusion read side as the oracle; no capture, no spend):
  - a nested answer_key.ground_truth_set scores (perfect answer -> 100); flat fixtures still score; on the
    REAL questions.yaml a perfect answer set beats an empty one, significantly (and enrich_ab sees the lift),
  - weight_ab.stage_learned_weights / staged_learned_weights make the served clone's live gate resolve the
    learned weights (profiles included) under ab_env('learned') — and still equal under ab_env('equal');
    the clone's instance.json is restored byte-for-byte (or removed when there was none), also after an
    exception; refusals: equal-only weights, invalid weights, a root that is not a synced project (a
    wrong root — nothing created there), a capture already staged, an unparseable file (left
    untouched); a write during the capture is detected and only the staged keys are undone; a lost token
    is recoverable from the in-file marker; ab_env documents the full procedure,
  - build_g4_evidence records weights_fingerprint; apply_learned applies only when it matches
    learned_weights_fingerprint() of the CURRENT block (re-tuned weights or unbound evidence -> refused),
  - apply_learned's refusal names the real procedure (absolute benchmark/weight_ab.py + stage + evidence),
    never the plan-only --ab-weights.

Usage: python tests/o10_g4_bridge_r283_gates.py [--root <tree>]
"""
from __future__ import annotations

import inspect
import json
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True     # never drop __pycache__ into a --root snapshot

REPO = Path(__file__).resolve().parent.parent
PASSES: list[str] = []
FAILS: list[str] = []

BM25_HEAVY = {"vector": 0.5, "bm25": 1.5, "graph_weighted": 1.0}
IMPACT_ROW = {"vector": 0.75, "bm25": 0.5, "graph_weighted": 1.5}
EQUAL = {"vector": 1.0, "bm25": 1.0, "graph_weighted": 1.0}
ON = {"DEEPMAP_APPLY_LEARNED": "1"}
TUNED = {"arm_weights": dict(BM25_HEAVY), "beats_equal": True, "significant": True, "applied": False,
         "reason": "learned_beats_equal", "objective": "ndcg", "rrf_k": 60,
         "profiles": {"impact": {"arm_weights": dict(IMPACT_ROW), "rrf_k": 60, "n_labeled": 44}},
         "learning_enabled": True, "generated_utc": "2026-09-29T00:00:00Z"}


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSES if ok else FAILS).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok or not detail else f" — {detail}"))


def _load(root: Path):
    for sub in ("benchmark", "build"):
        p = str(root / sub)
        if p not in sys.path:
            sys.path.insert(0, p)
    import instance_state as S           # noqa: E402
    import tune_weights as T             # noqa: E402
    import learned_fusion as LF          # noqa: E402
    import weight_ab as W                # noqa: E402
    import enrich_ab as E                # noqa: E402
    return S, T, LF, W, E


def _inst(root: Path) -> Path:
    return root / ".deepmap" / "instance.json"


def _write(root: Path, obj) -> bytes:
    p = _inst(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(obj, indent=4).encode("utf-8") + b"\n"     # deliberately NOT the writers' format
    p.write_bytes(raw)
    return raw


def _synced(root: Path) -> Path:
    """A served benchmark clone is always a SYNCED project: sync wrote .deepmap/graph_build.json."""
    m = root / ".deepmap" / "graph_build.json"
    m.parent.mkdir(parents=True, exist_ok=True)
    m.write_text('{"built_at_sha": "0123abc"}\n', encoding="utf-8")
    return root


def _lf(root: Path) -> dict:
    return json.loads(_inst(root).read_text(encoding="utf-8")).get("learned_fusion") or {}


def _questions(root: Path):
    """The shipped questions via PyYAML (a declared dependency — requirements.txt / CI). None without it:
    the harness's stdlib fallback keeps nested values like answer_key opaque BY DESIGN ("only validated
    when PyYAML is installed"), so the real-question checks cannot run there."""
    try:
        import yaml
    except ImportError:
        return None
    doc = yaml.safe_load((root / "benchmark" / "questions.yaml").read_text(encoding="utf-8"))
    return doc.get("questions") if isinstance(doc, dict) else doc


# ---- ISS-benchmark:009 -----------------------------------------------------------------------------
def gate_nested_ground_truth(W, E, root: Path) -> None:
    print("\n-- ISS-benchmark:009: answer_key.ground_truth_set (the shipped schema) is what gets scored --")
    atoms = ["a.py:1 — Foo (x)", "b.py:2 — Bar (y)"]
    nested = [{"id": "n1", "family": "multi_hop", "answer_key": {"ground_truth_set": list(atoms)}}]
    flat = [{"id": "f1", "family": "multi_hop", "ground_truth_set": list(atoms)}]
    perfect = "\n".join(atoms)
    check("a nested answer_key.ground_truth_set: a PERFECT answer scores 100",
          W.score_answer_set({"n1": perfect}, nested).get("n1") == 100.0, str(W.score_answer_set({"n1": perfect}, nested)))
    check("a flattened fixture (top-level ground_truth_set) still scores 100",
          W.score_answer_set({"f1": perfect}, flat).get("f1") == 100.0)
    both = [{"id": "b1", "answer_key": {"ground_truth_set": list(atoms)}, "ground_truth_set": ["zzz.py:9 — Q (q)"]}]
    check("the schema-of-record (answer_key) wins over a stray top-level key",
          W.score_answer_set({"b1": perfect}, both).get("b1") == 100.0)
    qs = _questions(root)
    if qs is None:
        print("  [SKIP] REAL questions.yaml checks — PyYAML is not installed (declared in requirements.txt; "
              "the synthetic nested/flat checks above still ran)")
        return
    shipped = [q for q in qs if isinstance(q, dict) and q.get("id")]
    check("the shipped questions.yaml loads (49 questions)", len(shipped) >= 40, str(len(shipped)))
    good = {q["id"]: "\n".join((q.get("answer_key") or {}).get("ground_truth_set") or []) for q in shipped}
    ab = W.deterministic_ab({}, good, shipped)
    check("REAL questions.yaml: perfect answers score ~100 (not 0)", ab["learned_mean"] >= 99.0, str(ab["learned_mean"]))
    check("REAL questions.yaml: empty answers score 0", ab["equal_mean"] == 0.0, str(ab["equal_mean"]))
    check("REAL questions.yaml: perfect-vs-empty is a significant win", ab["significant"] is True, str(ab["bootstrap"]))
    conc = {q["id"]: good[q["id"]] for q in shipped if q.get("family") == "concept_to_impl"}
    v = E.enrich_verdict(E.enrich_ab({}, conc, shipped))
    check("enrich_ab on the REAL questions sees the concept_to_impl lift", v["concept_delta"] > 50.0, str(v))


# ---- D1 --------------------------------------------------------------------------------------------
def _serves(LF, root: Path, env: dict, query: str = "plain words here please"):
    return LF.resolve_arm_weights_for_live(root, query=query, env=env)


def gate_stage_restore(S, T, LF, W) -> None:
    print("\n-- D1: the learned capture really serves the learned weights; the clone is restored exactly --")
    stage = getattr(W, "stage_learned_weights", None)
    restore = getattr(W, "restore_learned_weights", None)
    staged_cm = getattr(W, "staged_learned_weights", None)
    with tempfile.TemporaryDirectory() as td:
        clone = _synced(Path(td))
        before = _write(clone, {"other": 1, "learned_fusion": {"logging_enabled": True}})
        # The documented capture env alone (what the procedure used to be).
        learned_env = W.ab_env("learned", {})
        equal_env = W.ab_env("equal", dict(ON))
        if callable(staged_cm):
            with staged_cm(clone, dict(TUNED)) as tok:
                got = _serves(LF, clone, learned_env)
                got_impact = _serves(LF, clone, learned_env, query="who calls parse_config")
                got_equal = _serves(LF, clone, equal_env)
            res = tok.get("restore") or {}
        else:
            got = _serves(LF, clone, learned_env)
            got_impact = got_equal = None
            tok, res = {}, {}
        check("during the learned capture the SERVED clone resolves the learned global weights",
              got == BM25_HEAVY, str(got))
        check("... and an impact query resolves the learned impact PROFILE", got_impact == IMPACT_ROW, str(got_impact))
        check("... while ab_env('equal') still serves equal (None)", got_equal is None, str(got_equal))
        check("the token carries the weights fingerprint of what was served",
              bool(tok) and tok.get("fingerprint") == T.learned_weights_fingerprint(TUNED)
              if hasattr(T, "learned_weights_fingerprint") else False, str(tok.get("fingerprint")))
        check("after the capture the clone's instance.json is restored BYTE-FOR-BYTE",
              _inst(clone).read_bytes() == before and res.get("restored") == "exact", str(res))
        check("... and serves equal again", _serves(LF, clone, learned_env) is None)

    with tempfile.TemporaryDirectory() as td:
        clone = _synced(Path(td))
        ok = False
        if callable(staged_cm):
            try:
                with staged_cm(clone, dict(TUNED)):
                    ok = _serves(LF, clone, ON) == BM25_HEAVY
                    raise RuntimeError("capture crashed")
            except RuntimeError:
                pass
        check("no instance.json before -> staged for the capture", ok)
        check("... removed again afterwards, also when the capture raised", not _inst(clone).exists()
              and (clone / ".deepmap" / "graph_build.json").is_file())

    print("\n-- D1: refusals (never a silent equal-vs-equal capture, never a wiped file) --")
    with tempfile.TemporaryDirectory() as td:
        wrong = Path(td)
        tok = stage(wrong, dict(TUNED)) if callable(stage) else {"ok": True}
        check("a root that is not a SYNCED project (no .deepmap/graph_build.json — a wrong root) -> refused",
              tok.get("ok") is False and "graph_build.json" in str(tok.get("reason")), str(tok.get("reason")))
        check("... and nothing is created there", not (wrong / ".deepmap").exists())
    for label, block in (("equal-only weights", dict(TUNED, arm_weights=dict(EQUAL), profiles={})),
                         ("invalid weights", dict(TUNED, arm_weights={"vector": 1.0})),
                         ("not a block", None)):
        with tempfile.TemporaryDirectory() as td:
            clone = _synced(Path(td))
            before = _write(clone, {"other": 1})
            tok = stage(clone, block) if callable(stage) else {"ok": True}
            check(f"stage refuses {label}", tok.get("ok") is False and bool(tok.get("reason")), str(tok.get("reason")))
            check(f"... and leaves the clone untouched ({label})", _inst(clone).read_bytes() == before)
            raised = False
            if callable(staged_cm):
                try:
                    with staged_cm(clone, block):
                        pass
                except ValueError:
                    raised = True
            check(f"the context manager RAISES on {label} (a capture must not run on equal)", raised)
    with tempfile.TemporaryDirectory() as td:
        clone = _synced(Path(td))
        p = _inst(clone)
        p.write_bytes(b'{"learned_fusion": {"arm_we')
        tok = stage(clone, dict(TUNED)) if callable(stage) else {"ok": True}
        check("an unparseable served instance.json -> refused, left byte-identical",
              tok.get("ok") is False and p.read_bytes() == b'{"learned_fusion": {"arm_we', str(tok.get("reason")))
    with tempfile.TemporaryDirectory() as td:
        clone = _synced(Path(td))
        before = _write(clone, {"other": 1})
        t1 = stage(clone, dict(TUNED)) if callable(stage) else {}
        t2 = stage(clone, dict(TUNED, arm_weights={"vector": 1.5, "bm25": 0.5, "graph_weighted": 1.0})) \
            if callable(stage) else {"ok": True}
        check("a second stage while one is staged -> refused (never nests over applied:true)",
              t2.get("ok") is False, str(t2.get("reason")))
        r = restore(t1) if callable(restore) else {}
        check("... the first one still restores exactly", _inst(clone).read_bytes() == before, str(r))

    print("\n-- D1: a write during the capture is detected; only the staged keys are undone --")
    with tempfile.TemporaryDirectory() as td:
        clone = _synced(Path(td))
        _write(clone, {"other": 1, "learned_fusion": {"arm_weights": dict(EQUAL), "applied": False}})
        tok = stage(clone, dict(TUNED)) if callable(stage) else {}
        S.merge_learned_fusion(clone, {"learning_enabled": True})     # another writer, mid-capture
        r = restore(tok) if callable(restore) else {}
        lf = _lf(clone)
        check("restore reports changed_during_capture (treat the capture as suspect)",
              r.get("changed_during_capture") is True and r.get("restored") == "keys", str(r))
        check("... the staged gate keys are back to their previous values",
              lf.get("arm_weights") == EQUAL and lf.get("applied") is False and "beats_equal" not in lf
              and "profiles" not in lf, str(lf))
        check("... the other writer's change is kept, the marker is gone",
              lf.get("learning_enabled") is True and not any(k.startswith("ab_") for k in lf), str(sorted(lf)))

    print("\n-- D1: a lost token (crashed driver) is recoverable from the in-file marker --")
    with tempfile.TemporaryDirectory() as td:
        clone = _synced(Path(td))
        _write(clone, {"other": 1})
        if callable(stage):
            stage(clone, dict(TUNED))
        r = restore(clone) if callable(restore) else {}
        data = json.loads(_inst(clone).read_text(encoding="utf-8"))
        check("restore(<served root>) undoes the stage without the token",
              r.get("ok") is True and "learned_fusion" not in data and data.get("other") == 1, f"{r} {data}")
        check("... nothing serves learned weights any more", _serves(LF, clone, ON) is None)
    with tempfile.TemporaryDirectory() as td:
        bare = Path(td)
        r = restore(bare) if callable(restore) else {"ok": True}
        check("restore(<a root with no .deepmap/>) reports nothing staged and creates nothing",
              r.get("ok") is False and not (bare / ".deepmap").exists(), str(r))

    doc = inspect.getdoc(W.ab_env) or ""
    need = ("stage_learned_weights", "restore", "deterministic_ab", "build_g4_evidence", "weights_fingerprint",
            "write_benchmark_evidence", "tune --apply", "DEEPMAP_PROJECT_ROOT")
    missing = [n for n in need if n not in doc]
    check("ab_env documents the FULL G4 procedure (stage -> capture -> restore -> score -> evidence -> apply)",
          not missing, f"missing {missing}")


# ---- D2 + ISS-benchmark:012 ------------------------------------------------------------------------
def gate_evidence_binding(S, T, LF, W, root: Path) -> None:
    print("\n-- D2: benchmark_evidence is bound to the weights it measured --")
    fp_fn = getattr(T, "learned_weights_fingerprint", None)
    fp = fp_fn(TUNED) if callable(fp_fn) else None
    check("learned_weights_fingerprint exists and fingerprints a learned block", isinstance(fp, str) and len(fp) > 16,
          repr(fp))
    if callable(fp_fn):
        check("... ignores rrf_k / stats / flags (live never reads them)",
              fp_fn(dict(TUNED, rrf_k=30, applied=True, reason="x", n_labeled_queries=9)) == fp)
        check("... changes when the global weights change",
              fp_fn(dict(TUNED, arm_weights={"vector": 0.75, "bm25": 1.5, "graph_weighted": 1.0})) != fp)
        check("... changes when a profile row changes",
              fp_fn(dict(TUNED, profiles={"impact": {"arm_weights": dict(BM25_HEAVY)}})) != fp)
        check("... ignores an invalid profile row (live ignores it too)",
              fp_fn(dict(TUNED, profiles=dict(TUNED["profiles"], nl_long={"arm_weights": "junk"}))) == fp)
        check("... 1 and 1.0 fingerprint the same", fp_fn({"arm_weights": {"vector": 1, "bm25": 2, "graph_weighted": 1}})
              == fp_fn({"arm_weights": {"vector": 1.0, "bm25": 2.0, "graph_weighted": 1.0}}))
        check("... None for an invalid global vector", fp_fn({"arm_weights": {"vector": 1.0}}) is None)
    params = inspect.signature(W.build_g4_evidence).parameters
    check("build_g4_evidence takes weights_fingerprint", "weights_fingerprint" in params, str(list(params)))
    ab = W.deterministic_ab({"q1": "nothing"}, {"q1": "a.py:1 — Foo (x)"},
                            [{"id": q, "family": "multi_hop", "answer_key": {"ground_truth_set": ["a.py:1 — Foo (x)"]}}
                             for q in ("q1",)])
    ab = dict(ab, significant=True)
    kw = {"weights_fingerprint": fp} if "weights_fingerprint" in params else {}
    ev = W.build_g4_evidence(ab, pre_registered_slice="multi_hop", **kw)
    check("the evidence records the fingerprint", ev.get("weights_fingerprint") == fp and fp is not None,
          str(ev.get("weights_fingerprint")))

    print("\n-- D2: the end-to-end procedure applies ONLY the benchmarked weights --")
    stage = getattr(W, "staged_learned_weights", None)
    with tempfile.TemporaryDirectory() as td_t, tempfile.TemporaryDirectory() as td_c:
        tuned, clone = Path(td_t), _synced(Path(td_c))
        _write(tuned, {"project": "mine", "learned_fusion": dict(TUNED)})
        _write(clone, {"clone": True})
        served = None
        tok = {}
        if callable(stage):
            with stage(clone, _lf(tuned)) as tok:
                served = _serves(LF, clone, W.ab_env("learned", {}))
        check("the learned capture served the tuned weights", served == BM25_HEAVY, str(served))
        ev = W.build_g4_evidence(ab, pre_registered_slice="multi_hop",
                                 **({"weights_fingerprint": tok.get("fingerprint")} if kw else {}))
        W.write_benchmark_evidence(tuned, ev)
        ok, reason = T.apply_learned(tuned)
        check("tune --apply APPLIES with evidence bound to the current weights", ok is True, reason)
        check("... and the live gate now serves them", LF.load_applied_weights(tuned, env=ON) == BM25_HEAVY)

        # A later auto-tune re-writes DIFFERENT weights (applied -> false; benchmark_evidence survives the merge).
        T.write_instance_weights(tuned, {"arm_weights": {"vector": 1.5, "bm25": 0.5, "graph_weighted": 1.0},
                                         "beats_equal": True, "significant": True, "profiles": {}})
        check("the re-tune kept the old benchmark_evidence (as before)",
              isinstance(_lf(tuned).get("benchmark_evidence"), dict))
        ok, reason = T.apply_learned(tuned)
        check("tune --apply REFUSES re-tuned weights the evidence never measured", ok is False, reason)
        check("... and says why (different weights / re-run the A/B)",
              "different" in reason.lower() and "weight_ab.py" in reason, reason)
        check("... applied stays false", _lf(tuned).get("applied") is False)

    with tempfile.TemporaryDirectory() as td:
        tuned = Path(td)
        _write(tuned, {"learned_fusion": dict(TUNED, benchmark_evidence={"present": True, "significant": True})})
        ok, reason = T.apply_learned(tuned)
        check("evidence with NO weights_fingerprint (unbound) -> refused", ok is False, reason)
        _write(tuned, {"learned_fusion": dict(TUNED, benchmark_evidence={"present": True, "significant": False,
                                                                        "weights_fingerprint": fp})})
        ok, reason = T.apply_learned(tuned)
        check("bound but NOT significant evidence -> refused", ok is False, reason)

    print("\n-- ISS-benchmark:012: the refusal names the REAL procedure, not the plan-only --ab-weights --")
    with tempfile.TemporaryDirectory() as td:
        tuned = Path(td)
        _write(tuned, {"learned_fusion": dict(TUNED)})
        ok, reason = T.apply_learned(tuned)
        wab = (root / "benchmark" / "weight_ab.py").as_posix()
        check("no-evidence refusal", ok is False, reason)
        check("... names the ABSOLUTE benchmark/weight_ab.py", wab in reason, reason)
        check("... names the stage helper + build_g4_evidence + write_benchmark_evidence",
              all(s in reason for s in ("stage_learned_weights", "build_g4_evidence", "write_benchmark_evidence")),
              reason)
        check("... never the plan-only run_benchmark.py --ab-weights", "--ab-weights" not in reason, reason)
        check("... the named helpers exist (no phantom pointer)",
              all(callable(getattr(W, n, None)) for n in ("stage_learned_weights", "restore_learned_weights",
                                                          "build_g4_evidence", "write_benchmark_evidence")))


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    root = REPO
    if "--root" in sys.argv:
        root = Path(sys.argv[sys.argv.index("--root") + 1]).resolve()
    print("=" * 72)
    print(f"DeepMap — O10: the G4 bridge measures the learned weights it claims (root: {root})")
    print("=" * 72)
    S, T, LF, W, E = _load(root)
    gate_nested_ground_truth(W, E, root)
    gate_stage_restore(S, T, LF, W)
    gate_evidence_binding(S, T, LF, W, root)
    total = len(PASSES) + len(FAILS)
    print("\n" + "=" * 72)
    if FAILS:
        print(f"RESULT: RED — {len(PASSES)}/{total} gates passed.")
        return 1
    print(f"RESULT: GREEN — {len(PASSES)}/{total} gates passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
