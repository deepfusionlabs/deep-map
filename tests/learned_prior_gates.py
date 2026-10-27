#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j, no-spend gates for RM-1 COLD-START SEEDING (the user-level prior under DEEPMAP_HOME).

An optional, LOCAL-MACHINE-ONLY prior (~/.deepmap/learned_prior.json) pools per-arm weight
tendencies from projects where learning WON (beats_equal AND significant — noise is never
exported). A NEW project below the cold-start threshold can consume it as (a) a tuner
warm-start (tune_profiles(seed_prior=...) -> _optimize(prior=..., anchor=...): coordinate descent's
starting point AND the tie-break anchor among equally-scoring candidates, which is how the default grid
optimizer honours it — never the safety envelope) and (b) a `learned_fusion.seed` RECOMMENDATION block.
The seed is NEVER read by live ranking: resolve_arm_weights_for_live / _select_weights are
byte-untouched; the project's own learned state always shadows the prior; equal weights remain the
ultimate fallback.

Locks:
  - export refuses non-significant results; replaces only its own project's entry; confined to
    DEEPMAP_HOME,
  - pooled_prior: n_labeled-weighted mean, clamped to the equal-anchored envelope, stale entries
    excluded, the consuming project excluded (no self-echo), None when nothing usable,
  - consume: the seed block is a recommendation only — with DEEPMAP_APPLY_LEARNED=1 a seed-only
    instance STILL resolves to None (equal),
  - warm-start: below the threshold equal stands regardless of the seed; deterministic,
  - learned_fusion.py contains neither `learned_prior` nor a seed read,
  - zero-egress: only <home>/learned_prior.json and <root>/.deepmap/instance.json are touched.
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


def _load(name: str, base: Path):
    spec = importlib.util.spec_from_file_location(f"dm_{name}", base / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)
    return m


WIN_LF = {"arm_weights": {"vector": 1.5, "bm25": 0.5, "graph_weighted": 1.0},
          "beats_equal": True, "significant": True, "objective": "ndcg",
          "n_labeled_queries": 100, "generated_utc": "2026-07-10T00:00:00Z",
          "profiles": {"impact": {"arm_weights": {"vector": 1.5, "bm25": 0.5, "graph_weighted": 1.0},
                                  "n_labeled": 50}}}


def _write_instance(root: Path, lf: dict) -> None:
    inst = root / ".deepmap" / "instance.json"
    inst.parent.mkdir(parents=True, exist_ok=True)
    inst.write_text(json.dumps({"learned_fusion": lf}), encoding="utf-8")


def gate_export_requires_significance(LP) -> None:
    print("\n-- export refuses anything that did not WIN (beats_equal AND significant) --")
    with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as proj:
        env = {"DEEPMAP_HOME": home}
        root = Path(proj)
        path, reason = LP.export_prior(root, env=env)
        check("no learned_fusion -> refused with a reason", path is None and bool(reason), str(reason))
        _write_instance(root, dict(WIN_LF, significant=False))
        path, reason = LP.export_prior(root, env=env)
        check("not significant -> refused", path is None, str(reason))
        _write_instance(root, dict(WIN_LF, beats_equal=False))
        path, reason = LP.export_prior(root, env=env)
        check("beats_equal false -> refused", path is None, str(reason))
        check("refusals leave NO prior file", not (Path(home) / "learned_prior.json").exists())


def gate_export_writes(LP) -> None:
    print("\n-- a WIN exports a provenance-carrying entry; re-export replaces only its own entry --")
    with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as pa, \
            tempfile.TemporaryDirectory() as pb:
        env = {"DEEPMAP_HOME": home}
        ra, rb = Path(pa), Path(pb)
        _write_instance(ra, dict(WIN_LF))
        path, reason = LP.export_prior(ra, env=env)
        check("win -> exported (path returned)", path is not None and Path(path).is_file(), str(reason))
        prior = json.loads((Path(home) / "learned_prior.json").read_text(encoding="utf-8"))
        check("schema: version/kind/projects", prior.get("version") == 1
              and prior.get("kind") == "deepmap_learned_prior" and isinstance(prior.get("projects"), dict))
        key_a = LP.project_key(ra)
        ent = prior["projects"].get(key_a, {})
        check("entry carries weights + provenance",
              ent.get("arm_weights") == WIN_LF["arm_weights"] and ent.get("n_labeled_queries") == 100
              and ent.get("significant") is True and bool(ent.get("exported_utc")), str(ent))
        check("entry carries profiles", "impact" in (ent.get("profiles") or {}))
        # Re-export with different weights replaces ONLY project A's entry.
        _write_instance(rb, dict(WIN_LF, arm_weights={"vector": 0.5, "bm25": 1.5, "graph_weighted": 1.0},
                                 n_labeled_queries=300, profiles={}))
        LP.export_prior(rb, env=env)
        _write_instance(ra, dict(WIN_LF, arm_weights={"vector": 1.25, "bm25": 0.75, "graph_weighted": 1.0}))
        LP.export_prior(ra, env=env)
        prior = json.loads((Path(home) / "learned_prior.json").read_text(encoding="utf-8"))
        check("two projects -> two entries", len(prior["projects"]) == 2, str(len(prior["projects"])))
        check("re-export replaced only its OWN entry",
              prior["projects"][key_a]["arm_weights"]["vector"] == 1.25
              and prior["projects"][LP.project_key(rb)]["arm_weights"]["bm25"] == 1.5)
        stray = [p.name for p in Path(home).rglob("*") if p.is_file() and p.name != "learned_prior.json"]
        check("confinement: only learned_prior.json under the home", stray == [], str(stray))


def gate_pooled_math(LP) -> None:
    print("\n-- pooled_prior: n-weighted mean, clamped, stale/self excluded, None when unusable --")
    now = "2026-07-11T00:00:00Z"
    prior = {"version": 1, "kind": "deepmap_learned_prior", "projects": {
        "aaa": {"root": "c:/x/a", "arm_weights": {"vector": 1.5, "bm25": 0.5, "graph_weighted": 1.0},
                "n_labeled_queries": 100, "beats_equal": True, "significant": True,
                "exported_utc": "2026-07-01T00:00:00Z",
                "profiles": {"impact": {"arm_weights": {"vector": 1.5, "bm25": 0.5, "graph_weighted": 1.0},
                                        "n_labeled": 50}}},
        "bbb": {"root": "c:/x/b", "arm_weights": {"vector": 0.5, "bm25": 1.5, "graph_weighted": 1.0},
                "n_labeled_queries": 300, "beats_equal": True, "significant": True,
                "exported_utc": "2026-07-01T00:00:00Z", "profiles": {}},
    }}
    pooled = LP.pooled_prior(prior, now=now)
    check("n-weighted mean (hand-computed)",
          pooled and abs(pooled["arm_weights"]["vector"] - 0.75) < 1e-9
          and abs(pooled["arm_weights"]["bm25"] - 1.25) < 1e-9
          and abs(pooled["arm_weights"]["graph_weighted"] - 1.0) < 1e-9, str(pooled))
    check("provenance: n_projects + n_labeled_total",
          pooled.get("n_projects") == 2 and pooled.get("n_labeled_total") == 400, str(pooled))
    check("profiles pooled when present", "impact" in (pooled.get("profiles") or {}), str(pooled.get("profiles")))
    # Clamp: a hand-forged extreme entry is pulled into the equal-anchored envelope [0.5, 1.5].
    forged = {"version": 1, "projects": {"z": {"root": "c:/x/z",
              "arm_weights": {"vector": 5.0, "bm25": 0.1, "graph_weighted": 1.0},
              "n_labeled_queries": 10, "beats_equal": True, "significant": True,
              "exported_utc": "2026-07-01T00:00:00Z"}}}
    pooled = LP.pooled_prior(forged, now=now)
    check("clamped to the equal-anchored envelope",
          pooled and pooled["arm_weights"]["vector"] == 1.5 and pooled["arm_weights"]["bm25"] == 0.5,
          str(pooled))
    # Staleness: an entry older than max_age_days is excluded.
    stale = {"version": 1, "projects": {"old": {"root": "c:/x/old",
             "arm_weights": {"vector": 1.5, "bm25": 0.5, "graph_weighted": 1.0},
             "n_labeled_queries": 10, "beats_equal": True, "significant": True,
             "exported_utc": "2025-01-01T00:00:00Z"}}}
    check("stale entry excluded -> None", LP.pooled_prior(stale, now=now) is None)
    # Self-echo: the consuming project's own entry is excluded.
    with tempfile.TemporaryDirectory() as proj:
        root = Path(proj)
        own = {"version": 1, "projects": {LP.project_key(root): {"root": str(root),
               "arm_weights": {"vector": 1.5, "bm25": 0.5, "graph_weighted": 1.0},
               "n_labeled_queries": 10, "beats_equal": True, "significant": True,
               "exported_utc": "2026-07-01T00:00:00Z"}}}
        check("own entry excluded (no self-echo) -> None",
              LP.pooled_prior(own, exclude_root=root, now=now) is None)
    check("None prior -> None", LP.pooled_prior(None) is None)
    check("wrong version -> load_prior None + pooled None",
          LP.pooled_prior({"version": 99, "projects": {}}) is None)


def gate_load_prior(LP) -> None:
    print("\n-- load_prior: absent/garbage/wrong-version -> None (fail-safe: no seed) --")
    with tempfile.TemporaryDirectory() as home:
        env = {"DEEPMAP_HOME": home}
        check("absent -> None", LP.load_prior(env=env) is None)
        pp = Path(home) / "learned_prior.json"
        pp.write_text("{garbage", encoding="utf-8")
        check("garbage -> None, no raise", LP.load_prior(env=env) is None)
        pp.write_text(json.dumps({"version": 99, "kind": "deepmap_learned_prior", "projects": {}}),
                      encoding="utf-8")
        check("unknown version -> None (fail-safe)", LP.load_prior(env=env) is None)
        pp.write_text(json.dumps({"version": 1, "kind": "deepmap_learned_prior", "projects": {}}),
                      encoding="utf-8")
        check("valid v1 -> dict", isinstance(LP.load_prior(env=env), dict))


def gate_consume_cold_project(LP, LF) -> None:
    print("\n-- consume: a seed is a RECOMMENDATION only; it can never go live --")
    with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as pa, \
            tempfile.TemporaryDirectory() as pnew:
        env = {"DEEPMAP_HOME": home}
        ra, rnew = Path(pa), Path(pnew)
        _write_instance(ra, dict(WIN_LF))
        LP.export_prior(ra, env=env)
        pooled = LP.pooled_prior(LP.load_prior(env=env), exclude_root=rnew)
        check("a cold project pools the other project's win", pooled is not None
              and pooled["arm_weights"] == WIN_LF["arm_weights"], str(pooled))
        out = LP.write_seed_recommendation(rnew, pooled)
        check("seed recommendation written", out is not None)
        lf = json.loads((rnew / ".deepmap" / "instance.json").read_text(encoding="utf-8")).get("learned_fusion", {})
        seed = lf.get("seed", {})
        check("seed carries source + provenance + the honesty note",
              seed.get("source") == "user_prior" and seed.get("n_projects") == 1
              and "never read by live ranking" in str(seed.get("note")), str(seed)[:200])
        check("the seed writes NO gate keys (arm_weights/profiles/applied untouched)",
              "arm_weights" not in lf and "profiles" not in lf and "applied" not in lf, str(sorted(lf)))
        got = LF.resolve_arm_weights_for_live(rnew, env={"DEEPMAP_APPLY_LEARNED": "1"})
        check("seed-only instance + APPLY env -> resolve None (equal)", got is None, str(got))
        # seed_for_tuner is env-gated.
        check("seed_for_tuner: env off -> None", LP.seed_for_tuner(rnew, env={"DEEPMAP_HOME": home}) is None)
        pooled2 = LP.seed_for_tuner(rnew, env={"DEEPMAP_HOME": home, "DEEPMAP_SEED_PRIOR": "1"})
        check("seed_for_tuner: env on -> the pooled dict",
              isinstance(pooled2, dict) and pooled2.get("arm_weights") == WIN_LF["arm_weights"], str(pooled2))


def gate_shadowing(LP) -> None:
    print("\n-- per-project learned state always shadows the prior --")
    with tempfile.TemporaryDirectory() as proj:
        root = Path(proj)
        own = {"arm_weights": {"vector": 1.25, "bm25": 0.75, "graph_weighted": 1.0},
               "beats_equal": True, "significant": True, "profiles": {"impact": {"arm_weights": {
                   "vector": 1.25, "bm25": 0.75, "graph_weighted": 1.0}, "n_labeled": 44}}}
        _write_instance(root, dict(own))
        pooled = {"arm_weights": {"vector": 0.5, "bm25": 1.5, "graph_weighted": 1.0},
                  "profiles": {}, "n_projects": 3, "n_labeled_total": 500}
        LP.write_seed_recommendation(root, pooled)
        lf = json.loads((root / ".deepmap" / "instance.json").read_text(encoding="utf-8")).get("learned_fusion", {})
        check("own arm_weights untouched by the seed", lf.get("arm_weights") == own["arm_weights"])
        check("own profiles untouched by the seed", lf.get("profiles") == own["profiles"])
        check("the seed sits beside them (recommendation only)", lf.get("seed", {}).get("n_projects") == 3)


def gate_warm_start(T) -> None:
    print("\n-- tuner warm-start: a seed is coord's START + the grid's tie-break anchor, never the safety envelope --")
    seed = {"vector": 1.5, "bm25": 0.5, "graph_weighted": 1.0}
    # Below the threshold, equal stands REGARDLESS of the seed.
    res = T.tune_profiles(records=[], seed_prior=seed)
    check("below threshold + seed -> equal stands (below_threshold)",
          res["reason"] == "below_threshold" and res["arm_weights"] == {a: 1.0 for a in ("vector", "bm25", "graph_weighted")})
    # With signal, seeded and unseeded runs are both deterministic; the seed must not raise and
    # must keep the result inside the equal-anchored envelope.
    L = _load("retrieval_log", BUILD)
    recs = []
    per_arm = {"vector": ["x", "p"], "bm25": ["p", "x"], "graph_weighted": ["x", "p"]}
    for i in range(12):
        returned = [{"id": r, "file": f"f/{r}.py", "name": r, "line": 1} for r in ("x", "p")]
        recs.append(L.build_record(f"q{i}", per_arm, returned, ts=f"t{i}"))
        recs.append({"type": "access", "ts": f"t{i}b", "paths": ["f/p.py"], "symbols": [], "kind": "edit"})
    r1 = T.tune_profiles(records=recs, min_labeled=4, optimizer="coord", seed_prior=seed)
    r2 = T.tune_profiles(records=recs, min_labeled=4, optimizer="coord", seed_prior=seed)
    check("seeded coord run is deterministic", r1["arm_weights"] == r2["arm_weights"])
    envelope_ok = all(0.25 <= v <= 1.5 for v in r1["arm_weights"].values())
    check("seeded result stays inside the equal-anchored envelope", envelope_ok, str(r1["arm_weights"]))
    r3 = T.tune_profiles(records=recs, min_labeled=4, seed_prior={"vector": "garbage"})
    check("an invalid seed is ignored (no raise, valid result)", isinstance(r3.get("arm_weights"), dict))


def gate_live_resolution_untouched(LF) -> None:
    print("\n-- the live read side never learns about the prior --")
    src = (BUILD / "learned_fusion.py").read_text(encoding="utf-8")
    check("learned_fusion.py never references learned_prior", "learned_prior" not in src)
    check('learned_fusion.py never reads a "seed" key', '"seed"' not in src and "'seed'" not in src)


def gate_never_raises_zero_egress(LP) -> None:
    print("\n-- never raises; zero-egress --")
    for label, arg in (("bad home", {"DEEPMAP_HOME": "\x00::bad::"}), ("empty env", {})):
        raised = False
        try:
            out = LP.load_prior(env=arg) if label == "bad home" else LP.pooled_prior({"version": 1, "projects": "junk"})
        except Exception:
            raised = True
            out = "raised"
        check(f"{label} -> no raise", not raised, str(out))
    raised = False
    try:
        out = LP.export_prior("\x00::bad::", env={"DEEPMAP_HOME": "\x00::bad::"})
    except Exception:
        raised = True
        out = "raised"
    check("export with bad root+home -> (None, reason), no raise",
          (not raised) and isinstance(out, tuple) and out[0] is None, str(out))
    raised = False
    try:
        out = LP.write_seed_recommendation("\x00::bad::", {"arm_weights": {}})
    except Exception:
        raised = True
    check("write_seed_recommendation bad root -> None, no raise", (not raised) and out is None)


def gate_wiring() -> None:
    print("\n-- wiring: sync consumes/exports behind DEEPMAP_SEED_PRIOR; CLI flags exist --")
    sync_src = (BUILD / "sync.py").read_text(encoding="utf-8")
    idx = sync_src.find("def _maybe_auto_tune")
    body = sync_src[idx:] if idx > 0 else ""
    check("sync: warm-start consumed via seed_for_tuner", "seed_for_tuner" in body)
    check("sync: cold-project recommendation + win export wired",
          "write_seed_recommendation" in body and "export_prior" in body)
    cli_src = (BUILD / "deepmap_cli.py").read_text(encoding="utf-8")
    check("CLI: --export-prior + --seed-from-prior flags", "--export-prior" in cli_src
          and "--seed-from-prior" in cli_src)
    check("CLI: tune --status shows the seed line", '"seed"' in cli_src or "'seed'" in cli_src)


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 68)
    print("DeepMap — learned-prior (cold-start seeding) gates (no Neo4j, no spend)")
    print("=" * 68)
    if str(BUILD) not in sys.path:
        sys.path.insert(0, str(BUILD))
    LF = _load("learned_fusion", BUILD)
    T = _load("tune_weights", BUILD)
    try:
        LP = _load("learned_prior", BUILD)
    except Exception as e:
        check("build/learned_prior.py loads", False, repr(e))
        print(f"\nRESULT: RED — {_PASS}/{_PASS + _FAIL} gates passed.")
        return 1

    gate_export_requires_significance(LP)
    gate_export_writes(LP)
    gate_pooled_math(LP)
    gate_load_prior(LP)
    gate_consume_cold_project(LP, LF)
    gate_shadowing(LP)
    gate_warm_start(T)
    gate_live_resolution_untouched(LF)
    gate_never_raises_zero_egress(LP)
    gate_wiring()

    print("\n" + "=" * 68)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
