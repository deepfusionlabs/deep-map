#!/usr/bin/env python3
"""0.28.3 (F4) — a resuming driver reuses a capture only when it is THIS DeepMap's graph AND the whole planned matrix.

Findings (fix-all round 2, F4 — O11's round-1 residuals and its review):
  1. The graph manifest a capture records (results.json graph_builds) did not say which DeepMap BUILT the graph, so
     run_benchmark.resume_state could only compare the version that RAN the capture (machine_spec.deepmap_version): a
     capture of a graph an older DeepMap built, or of a clone re-synced by another DeepMap since, still read as "this
     version's graph". build/sync.py now stamps `deepmap_version` into .deepmap/graph_build.json (F2); resume_state
     uses it strictly when recorded and falls back to machine_spec.deepmap_version when a manifest predates it.
  2. A capture that stopped part-way (a fail-closed abort mid-matrix, or Ctrl-C) persisted its partial cards in
     do_run's `finally`, and resume_state called it 'captured': the driver only scored it, so it could end up
     'measured' with most of its cards missing. do_run now records capture_complete (true only when the whole planned
     matrix ran), a re-score and a merge carry it, and resume_state sends an unfinished capture of this graph back to
     'none' — captured again, never scored as the repo's measurement.

Locks (hermetic: temp dirs; claude / git / Neo4j / MCP seams stubbed; no spend):
  A. graph_manifest_version reads the key the REAL build/sync.py writer stamps (a subprocess writes a manifest into a
     temp dir; read_graph_manifest keeps the field; the version round-trips).
  B. resume_state: the builder version is strict when recorded (an older builder -> stale; the clone re-synced by
     another DeepMap since -> stale; recorded, but the clone's manifest has none -> stale) and falls back to the
     version that ran the capture when absent (the pre-0.28.3 behaviour).
  C. capture_complete: do_run records true (whole matrix) / false (a fail-closed abort) / false (an interruption,
     which still propagates); a re-score keeps it; a merge combines it; resume_state and resume_state_for turn an
     unfinished capture of this graph into 'none' (never 'captured' / 'scored'); another graph stays 'stale'; a record
     without the field behaves as before.

Usage: python tests/f4_resume_provenance_r283_gates.py [--root <tree>]
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.dont_write_bytecode = True     # never drop __pycache__ into a --root snapshot

REPO = Path(__file__).resolve().parent.parent
PASSES: list[str] = []
FAILS: list[str] = []
PIN = "c" * 40
V = "0.28.3"
MANIFEST = {"built_at_sha": PIN, "built_at_utc": "2026-09-30T10:00:00Z", "extractor": "graphify",
            "graphify_version": "0.8.51", "nodes": 100, "calls_edges": 50, "embedded": 100, "dirty": False,
            "built_at_dirty": False, "provenance": "git", "verdict": "READY"}


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSES if ok else FAILS).append(name)
    print(f"  [PASS] {name}" if ok else f"  [FAIL] {name}" + (f" — {detail}" if detail else ""))


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _card(B, repo="o/r", scored=True, qid="q1", arm=None):
    m = {k: None for k in B.METRIC_KEYS}
    if scored:
        m.update(completeness_pct=50.0, correctness_pct=90.0, wrong_high=0)
    return {"repo": repo, "question_id": qid, "arm": arm or B.ARM_ON, "run": 1, "exit_code": 0, "metrics": m,
            "scored": scored}


def _record(B, *, version=V, graph=None, cards=None, complete=None):
    cards = [_card(B), _card(B, arm=B.ARM_OFF)] if cards is None else cards
    rec = {"machine_spec": {"os": "t", "deepmap_version": version},
           "graph_builds": {"o/r": dict(MANIFEST if graph is None else graph)},
           "scorecards": cards, "measured": all(c.get("scored") for c in cards)}
    if complete is not None:
        rec["capture_complete"] = complete
    return rec


# ---------------------------------------------------------------------------------------------- A
def gate_manifest_version(root: Path, B) -> None:
    print("\n-- A. graph_manifest_version reads the DeepMap version build/sync.py stamps into graph_build.json --")
    fn = getattr(B, "graph_manifest_version", None)
    check("run_benchmark.graph_manifest_version exists", callable(fn))
    if not callable(fn):
        return
    cases = [({"deepmap_version": V}, V), ({"deepmap_version": f" {V} "}, V), ({}, None),
             ({"deepmap_version": ""}, None), ({"deepmap_version": True}, None), ({"deepmap_version": None}, None),
             ("not a dict", None), (None, None)]
    bad = [(m, want, fn(m)) for m, want in cases if fn(m) != want]
    check("a recorded version is returned (trimmed); absent / blank / non-string / not a dict -> None (never raises)",
          not bad, str(bad))

    want = json.loads((root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")).get("version")
    code = ("import importlib.util, json, sys\n"
            "from pathlib import Path\n"
            "build = Path(sys.argv[1]); sys.path.insert(0, str(build))\n"
            "spec = importlib.util.spec_from_file_location('f4_sync_probe', build / 'sync.py')\n"
            "S = importlib.util.module_from_spec(spec); spec.loader.exec_module(S)\n"
            "out = S.write_graph_build_manifest(Path(sys.argv[2]), n_nodes=1, n_edges=0, n_embedded=1,\n"
            "    extractor='graphify', source_state={'sha': 'a' * 40, 'dirty': False})\n"
            "print('WROTE' if out else 'NOTHING')\n")
    with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as home:
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1", DEEPMAP_HOME=home)   # never ~/.deepmap
        p = subprocess.run([sys.executable, "-c", code, str(root / "build"), td], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=180, env=env)
        wrote = p.returncode == 0 and "WROTE" in (p.stdout or "")
        check("(premise) the real build/sync.py writer stamps a manifest in a temp dir", wrote,
              (p.stderr or p.stdout or "")[-400:])
        manifest = B.read_graph_manifest(Path(td)) if wrote else None
        got = fn(manifest)
        check("the version sync stamps round-trips through read_graph_manifest -> graph_manifest_version "
              "(the writer and the reader agree on the key)", bool(want) and got == want,
              f"got {got!r}, plugin.json {want!r}, manifest keys {sorted(manifest or {})}")


# ---------------------------------------------------------------------------------------------- B
def gate_builder_version(B) -> None:
    print("\n-- B. resume_state: the DeepMap that BUILT the measured graph (strict when recorded, fallback when not) --")

    def st(record, clone=dict(MANIFEST), current=V):
        return B.resume_state(record, repo="o/r", pinned_sha=PIN, current_version=current, clone_manifest=clone)

    older = dict(MANIFEST, deepmap_version="0.28.2")
    s, why = st(_record(B, graph=older), clone=dict(older))
    check("a 0.28.3 capture of a graph DeepMap 0.28.2 built -> stale (the reason names the builder)",
          s == "stale" and "0.28.2" in why, f"{s}: {why}")
    s, why = st(_record(B, graph=older), clone=None)
    check("...also when the clone is gone (the recorded builder decides alone)", s == "stale", f"{s}: {why}")
    same = dict(MANIFEST, deepmap_version=V)
    s, why = st(_record(B, graph=same), clone=dict(same))
    check("built AND captured by this DeepMap, every card scored -> scored", s == "scored", f"{s}: {why}")
    s, why = st(_record(B, graph=same), clone=dict(same, built_at_utc="2026-10-01T00:00:00Z", nodes=101))
    check("a rebuild by the SAME DeepMap (only the build time / counts moved) stays reusable", s == "scored",
          f"{s}: {why}")
    s, why = st(_record(B, graph=same), clone=dict(same, deepmap_version="0.28.4"))
    check("the clone was re-synced by ANOTHER DeepMap since the capture -> stale (names the new builder)",
          s == "stale" and "0.28.4" in why, f"{s}: {why}")
    s, why = st(_record(B, graph=same), clone=dict(MANIFEST))
    check("the capture recorded its builder but the clone's manifest now has none (re-synced by a DeepMap that "
          "records no version) -> stale", s == "stale", f"{s}: {why}")
    s, why = st(_record(B))
    check("FALLBACK: neither manifest records a builder -> decided as before (scored)", s == "scored", f"{s}: {why}")
    s, why = st(_record(B), clone=dict(MANIFEST, deepmap_version=V))
    check("FALLBACK: a capture from before the field, the clone since re-synced by the SAME version -> reusable",
          s == "scored", f"{s}: {why}")
    s, why = st(_record(B), clone=dict(MANIFEST, deepmap_version="0.28.4"))
    check("FALLBACK: a capture from before the field, the clone re-synced by ANOTHER version since -> stale",
          s == "stale" and "0.28.4" in why, f"{s}: {why}")
    s, why = st(_record(B, graph=dict(MANIFEST, deepmap_version="")), clone=dict(MANIFEST))
    check("a blank recorded builder counts as unrecorded (fallback), never as a version", s == "scored", f"{s}: {why}")
    s, why = st(_record(B, version="0.28.2", graph=same), clone=dict(same))
    check("the version that RAN the capture is still checked (0.28.2 harness over a 0.28.3 graph -> stale)",
          s == "stale" and "0.28.2" in why, f"{s}: {why}")


# ---------------------------------------------------------------------------------------------- C
def gate_resume_unfinished(B) -> None:
    print("\n-- C1. resume_state: an UNFINISHED capture of this graph is captured again, never scored as the repo's --")

    def st(record, clone=dict(MANIFEST)):
        return B.resume_state(record, repo="o/r", pinned_sha=PIN, current_version=V, clone_manifest=clone)

    s, why = st(_record(B, complete=False))
    check("capture_complete=false, every captured card blind-scored -> none (NOT 'scored': most of the matrix may "
          "be missing)", s == "none" and "unfinished" in why.lower(), f"{s}: {why}")
    s, why = st(_record(B, cards=[_card(B, scored=False)], complete=False))
    check("capture_complete=false, a card unscored -> none (NOT 'captured': scoring it would not complete it)",
          s == "none", f"{s}: {why}")
    s, why = st(_record(B, version="0.27.4", complete=False))
    check("an unfinished capture of ANOTHER graph is still stale (provenance first: never overwritten)",
          s == "stale", f"{s}: {why}")
    s, why = st(_record(B, complete=True))
    check("capture_complete=true -> scored, as before", s == "scored", f"{s}: {why}")
    s, why = st(_record(B))
    check("a record without capture_complete (made before it existed) -> decided as before", s == "scored",
          f"{s}: {why}")


def _stub_do_run(B, td: Path, *, fail_reset_at=None, interrupt_at=None):
    """do_run's seams replaced: 2 questions x (ON, OFF) x 1 run = 4 runs; nothing reaches claude / git / Neo4j."""
    repos = [{"name": "o/clone", "url": "u", "pinned_sha": PIN, "license": "MIT"}]
    qs = [{"id": f"q{i}", "repo": "o/clone", "prompt": "p", "grep_can_do": False, "family": "f",
           "answer_key": {"ground_truth_set": ["x"]}} for i in (1, 2)]
    B.load_config = lambda: (repos, qs)
    B.validate_config = lambda r, q: []
    B._which = lambda name: "claude"
    B.machine_spec = lambda: {"os": "t", "deepmap_version": V}
    B._server_files_present = lambda: True
    B._unreachable_mcp_servers = lambda: []
    B.write_on_mcp_config = lambda p: True
    B.assert_pinned_clean = lambda d, s: None
    B.assert_graph_at_pinned_sha = lambda d, s: None
    B.assert_on_arm_serves_repo = lambda d: None
    B.deepmap_version = lambda *a, **k: V
    counts = {"reset": 0, "run": 0}

    def reset(d, s):
        counts["reset"] += 1
        return "simulated reset failure" if fail_reset_at and counts["reset"] == fail_reset_at else None

    def run_one(arm, q, repo_dir, cfg, run_idx, out_dir, **kw):
        counts["run"] += 1
        if interrupt_at and counts["run"] == interrupt_at:
            raise KeyboardInterrupt
        return {"repo": q["repo"], "question_id": q["id"], "arm": arm, "run": run_idx, "exit_code": 0,
                "metrics": {k: None for k in B.METRIC_KEYS}, "scored": False}

    B.reset_pristine = reset
    B.run_one = run_one
    B.RESULTS_ROOT = td / "results"
    clone = td / "clones" / "o__clone"
    (clone / ".deepmap").mkdir(parents=True, exist_ok=True)
    (clone / ".deepmap" / "graph_build.json").write_text(json.dumps(dict(MANIFEST, deepmap_version=V)),
                                                         encoding="utf-8")
    return repos, qs, clone


def _run(B, td: Path, rid: str):
    saved = os.environ.get(B.ENGINE_ENV)
    os.environ[B.ENGINE_ENV] = "1"
    err = io.StringIO()
    raised = None
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            rc = B.do_run(SimpleNamespace(repo=None, clone_root=str(td / "clones"), max_budget_usd=1.0, timeout_s=60,
                                          runs_per_arm=1), rid)
    except BaseException as exc:  # noqa: BLE001 — the interruption case re-raises KeyboardInterrupt on purpose
        rc, raised = None, exc
    finally:
        if saved is None:
            os.environ.pop(B.ENGINE_ENV, None)
        else:
            os.environ[B.ENGINE_ENV] = saved
    rj = B.RESULTS_ROOT / rid / "results.json"
    rec = json.loads(rj.read_text(encoding="utf-8")) if rj.is_file() else {}
    return rc, raised, rec, err.getvalue()


def gate_capture_complete(root: Path) -> None:
    print("\n-- C2. do_run records capture_complete; a re-score and a merge carry it; resume_state_for honours it --")
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        B = _load(root / "benchmark" / "run_benchmark.py", "f4_rb_cc_full")
        _stub_do_run(B, td)
        rc, raised, rec, _err = _run(B, td, "full")
        check("the whole planned matrix ran -> rc 0, 4 cards, capture_complete=true",
              rc == 0 and len(rec.get("scorecards") or []) == 4 and rec.get("capture_complete") is True,
              f"rc={rc} raised={raised!r} cards={len(rec.get('scorecards') or [])} "
              f"capture_complete={rec.get('capture_complete')!r}")

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        B = _load(root / "benchmark" / "run_benchmark.py", "f4_rb_cc_abort")
        repos, qs, clone = _stub_do_run(B, td, fail_reset_at=3)
        rc, raised, rec, err = _run(B, td, "abort")
        check("a fail-closed abort mid-matrix (the 3rd run's reset fails) -> rc 2, the 2 spent cards kept, "
              "capture_complete=false, and it says so",
              rc == 2 and len(rec.get("scorecards") or []) == 2 and rec.get("capture_complete") is False
              and "capture_complete=false" in err,
              f"rc={rc} cards={len(rec.get('scorecards') or [])} capture_complete={rec.get('capture_complete')!r}")
        s, why = B.resume_state_for("abort", "o/clone", str(clone))
        check("resume_state_for on that partial capture -> none (captured again), not 'captured'",
              s == "none", f"{s}: {why}")
        out = B.score_captured_run(B.RESULTS_ROOT / "abort", repos, qs,
                                   lambda p: '{"correctness_pct": 90, "wrong_high": 0}', lambda r: "x",
                                   judge_model="j")
        check("a re-score keeps capture_complete=false", out.get("capture_complete") is False,
              str(out.get("capture_complete")))
        s, why = B.resume_state_for("abort", "o/clone", str(clone))
        check("...so even once every captured card is blind-scored it is never 'scored' (-> none)",
              s == "none", f"{s}: {why}")

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        B = _load(root / "benchmark" / "run_benchmark.py", "f4_rb_cc_int")
        _repos, _qs, clone = _stub_do_run(B, td, interrupt_at=2)
        rc, raised, rec, _err = _run(B, td, "interrupted")
        check("an interruption (Ctrl-C on the 2nd run) still propagates, and results.json is still written with "
              "the spent card and capture_complete=false",
              isinstance(raised, KeyboardInterrupt) and len(rec.get("scorecards") or []) == 1
              and rec.get("capture_complete") is False,
              f"raised={raised!r} cards={len(rec.get('scorecards') or [])} "
              f"capture_complete={rec.get('capture_complete')!r}")
        s, why = B.resume_state_for("interrupted", "o/clone", str(clone))
        check("resume_state_for on the interrupted capture -> none", s == "none", f"{s}: {why}")

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        B = _load(root / "benchmark" / "run_benchmark.py", "f4_rb_cc_merge")
        B.RESULTS_ROOT = td / "results"
        repos = [{"name": "o/r", "url": "u", "pinned_sha": PIN, "license": "MIT"}]
        B.load_config = lambda: (repos, [])

        def put(rid, complete):
            rec = _record(B, complete=complete)
            (B.RESULTS_ROOT / rid).mkdir(parents=True, exist_ok=True)
            (B.RESULTS_ROOT / rid / "results.json").write_text(json.dumps(rec), encoding="utf-8")

        put("a", True)
        put("b", False)
        put("c", True)
        put("d", None)

        def merged(ids, out):
            err = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
                B.do_merge(SimpleNamespace(merge=ids), out)
            return json.loads((B.RESULTS_ROOT / out / "results.json").read_text(encoding="utf-8")), err.getvalue()

        m1, err1 = merged("a,b", "m1")
        check("a merge with ONE unfinished capture -> capture_complete=false, with a warning",
              m1.get("capture_complete") is False and "unfinished" in err1.lower(),
              f"{m1.get('capture_complete')!r} / {err1[-200:]}")
        m2, _ = merged("a,c", "m2")
        check("a merge of finished captures -> capture_complete=true", m2.get("capture_complete") is True,
              str(m2.get("capture_complete")))
        m3, _ = merged("a,d", "m3")
        check("a merge including a run that records nothing -> the field is left out (unknown), never invented",
              "capture_complete" not in m3, str(m3.get("capture_complete")))


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
    print(f"DeepMap — resume reuses only this DeepMap's graph and a whole capture (root: {root})")
    print("=" * 72)
    B = _load(root / "benchmark" / "run_benchmark.py", "f4_rb_resume")
    gate_manifest_version(root, B)
    gate_builder_version(B)
    gate_resume_unfinished(B)
    gate_capture_complete(root)

    total = len(PASSES) + len(FAILS)
    print("\n" + "=" * 72)
    if FAILS:
        print(f"RESULT: RED — {len(PASSES)}/{total} gates passed.")
        return 1
    print(f"RESULT: GREEN — {len(PASSES)}/{total} gates passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
