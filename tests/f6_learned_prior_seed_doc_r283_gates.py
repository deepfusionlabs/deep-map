#!/usr/bin/env python3
"""0.28.3 (F6) — build/learned_prior.py's docstring says what a cold-start seed really does inside the tuner.

Finding (round-1 O10 / D4, routed to the unowned learned_prior.py): O10 made a seed prior ALSO the tuner's tie-break
anchor — tune_weights._optimize(anchor=...): among equally-scoring weight vectors the one closest to the seed wins,
which is the only way the default GRID optimizer can honour a seed — for the global fit AND every CV fold (the grid
ignores a starting point, so before D4 `--seed-from-prior` / DEEPMAP_SEED_PRIOR changed nothing on the default
optimizer). learned_prior.py's module docstring still said the warm-start "moves the coordinate-descent starting
point, never the equal-anchored clamp bounds" — i.e. that on the default grid optimizer a seed does nothing.

Locks (hermetic: no Neo4j, no network, never reads or writes DEEPMAP_HOME):
  A  the docstring's CONSUMPTION bullet names each thing the seed is and is not: coordinate descent's starting point,
     the tie-break anchor among EQUALLY-SCORING candidates (how the default GRID optimizer honours it), the global fit
     and every CV fold, never the equal-anchored clamp bounds, never read by live ranking;
  B  each of those claims is TRUE of the tune_weights in the same tree — the real tuner on a tie-plateau fixture:
     the seeded grid fit is the best-scoring candidate closest to the seed (and differs from the unseeded pick), it
     scores exactly the data's best, a far seed stays inside the grid / envelope learned_prior clamps to, coord starts
     at the seed, and the global fit + every CV fold are anchored to it;
  C  teeth: the claim checker flags the pre-fix bullet and a bullet without the equal-score scoping, and passes the
     fixed one.
Usage: python tests/f6_learned_prior_seed_doc_r283_gates.py [--root <tree>]
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True     # never drop __pycache__ into a --root snapshot

REPO = Path(__file__).resolve().parent.parent
PASSES: list[str] = []
FAILS: list[str] = []
ARMS = ("vector", "bm25", "graph_weighted")
EQUAL = {a: 1.0 for a in ARMS}
# O10's tie fixture: at many grid weights p (the accessed result) and x fuse to an exact tie or p wins, so the grid
# leaves a PLATEAU of equally-scoring candidates — exactly where a tie-break anchor decides the pick.
TIE = {"vector": ["x", "p"], "bm25": ["p", "x"], "graph_weighted": ["x", "p"]}
SEED = {"vector": 0.5, "bm25": 1.5, "graph_weighted": 1.0}

# The bullet as it stood before this fix (verbatim), for the teeth check.
PRE_FIX_BULLET = (
    "CONSUMPTION is offline-only: (a) a tuner WARM-START via tune_profiles(seed_prior=...) — it\n"
    "    moves the coordinate-descent starting point, never the equal-anchored clamp bounds — and\n"
    "    (b) a `learned_fusion.seed` RECOMMENDATION block. The seed is NEVER read by live ranking\n"
    "    (build/learned_fusion.py is byte-untouched and reads only arm_weights/profiles + the\n"
    "    4-condition gate); the project's own learned state always shadows it; equal weights remain\n"
    "    the ultimate fallback.")

# Each claim the CONSUMPTION bullet must make: (label, regexes that must ALL match the normalized bullet).
CLAIMS = (
    ("names coordinate descent's STARTING POINT",
     (r"coordinate[\s-]+descent(?:['’]s)?[\s-]+start|start\w*\s+point\s+(?:of|for)\s+coordinate[\s-]+descent",)),
    ("names the TIE-BREAK anchor", (r"tie[\s-]?break",)),
    ("scopes the tie-break to EQUALLY-SCORING candidates (a seed never beats a better score)", (r"equally[\s-]+scoring",)),
    ("says the default GRID optimizer honours the seed through it", (r"\bgrid\b",)),
    ("says the seed reaches the global fit AND every CV fold",
     (r"global fit", r"(?:\bcv\b|cross[\s-]*validation)[^.;]*\bfold")),
    ("says it never moves the equal-anchored clamp bounds", (r"never[^.;]*clamp bounds",)),
    ("keeps: the seed is NEVER read by live ranking", (r"never read by live ranking",)),
)


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSES if ok else FAILS).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok or not detail else f" — {detail}"))


def _norm(text: str) -> str:
    """Lowercase, unicode dashes -> '-', whitespace collapsed (a claim may wrap across docstring lines)."""
    return re.sub(r"\s+", " ", re.sub("[‐-―−]", "-", text or "")).strip().lower()


def consumption_bullet(doc: str) -> str:
    """The `* CONSUMPTION ...` bullet of the module docstring (up to the next `* ` bullet), or ''."""
    m = re.search(r"^\s*\*\s*(CONSUMPTION\b.*?)(?=^\s*\*\s|\Z)", doc or "", re.MULTILINE | re.DOTALL)
    return m.group(1).strip() if m else ""


def bullet_problems(bullet: str) -> list[str]:
    """Pure: the CLAIMS the bullet does not make ([] = it names every one)."""
    if not bullet:
        return ["no CONSUMPTION bullet in the module docstring"]
    text = _norm(bullet)
    return [label for label, pats in CLAIMS if not all(re.search(p, text) for p in pats)]


def _load(root: Path):
    b = str(root / "build")
    if b not in sys.path:
        sys.path.insert(0, b)
    import retrieval_log as L     # noqa: E402
    import tune_weights as T      # noqa: E402
    import learned_prior as LP    # noqa: E402
    return L, T, LP


def _log(L, reps: int):
    recs = []
    for i in range(reps):
        returned = [{"id": r, "file": f"f/{r}.py", "name": r, "line": 1} for r in ("x", "p")]
        recs.append(L.build_record(f"q{i}", dict(TIE), returned, ts=f"t{i:04d}"))
        recs.append({"type": "access", "v": 2, "paths": ["f/p.py"], "symbols": [], "kind": "edit", "ts": f"t{i:04d}b"})
    return recs


def _l1(w, ref):
    return sum(abs(w[a] - ref[a]) for a in ARMS)


def _fit(res: dict):
    """The tuner's global fit (tune_profiles' `candidate_learned`) — the vector the seed acts on, before the
    significance verdict may replace it with equal weights."""
    return res.get("candidate_learned") or res.get("arm_weights")


def gate_a_docstring(root: Path) -> None:
    print("\n-- A: the CONSUMPTION bullet names what the seed is, and is not, in the tuner --")
    path = root / "build" / "learned_prior.py"
    try:
        doc = ast.get_docstring(ast.parse(path.read_text(encoding="utf-8"))) or ""
    except Exception as exc:  # noqa: BLE001
        check(f"{path.name} parses", False, repr(exc))
        return
    bullet = consumption_bullet(doc)
    check("the module docstring has a CONSUMPTION bullet", bool(bullet))
    missing = bullet_problems(bullet)
    for label, _ in CLAIMS:
        check(f"the bullet {label}", label not in missing, _norm(bullet)[:160])


def gate_b_claims_true(root: Path) -> None:
    print("\n-- B: every claim the bullet makes is TRUE of the tune_weights in the same tree --")
    try:
        L, T, LP = _load(root)
    except Exception as exc:  # noqa: BLE001
        check("build/retrieval_log.py, tune_weights.py and learned_prior.py import", False, repr(exc))
        return
    recs = _log(L, 12)
    labeled = T.build_qrels(recs)
    dev, floor = T.DEFAULT_MAX_DEVIATION, T.DEFAULT_EXPLORATION_FLOOR
    grid = list(T.candidate_weights(dev, floor))
    scored = [(T.score_objective(labeled, w), w) for w in grid]
    top = max(s for s, _ in scored)
    plateau = [w for s, w in scored if s == top]
    check("(fixture) the grid leaves a plateau of equally-scoring candidates", len(plateau) > 3, str(len(plateau)))
    want = max(plateau, key=lambda w: (-_l1(w, SEED), -w["vector"], -w["bm25"], -w["graph_weighted"]))
    unseeded = _fit(T.tune_profiles(records=recs, min_labeled=4))
    seeded = T.tune_profiles(records=recs, min_labeled=4, seed_prior=dict(SEED))
    sw = _fit(seeded)
    check("tie-break anchor: the seeded GRID fit is the equally-scoring candidate closest to the seed",
          sw == want, f"{sw} vs {want}")
    check("... which is not the unseeded pick (on the default grid the seed is not a no-op)",
          sw != unseeded, f"{sw} == {unseeded}")
    check("... and scores exactly the data's best (a seed only breaks ties; it never beats a better score)",
          isinstance(sw, dict) and T.score_objective(labeled, sw) == top,
          f"{T.score_objective(labeled, sw) if isinstance(sw, dict) else sw} vs {top}")
    check("... and the seeded tune still reports it (a significant win, not reverted to equal)",
          seeded.get("arm_weights") == sw and seeded.get("significant") is True,
          f"arm_weights={seeded.get('arm_weights')} significant={seeded.get('significant')}")

    far = {"vector": 5.0, "bm25": 0.1, "graph_weighted": 9.0}
    fw = _fit(T.tune_profiles(records=recs, min_labeled=4, seed_prior=dict(far)))
    check("never the clamp bounds / the grid: a far-away seed still yields a grid candidate",
          fw in grid, str(fw))
    lo, hi = max(floor, 1.0 - dev), 1.0 + dev
    check("learned_prior clamps a pooled seed to exactly the tuner's equal-anchored envelope",
          (LP._CLAMP_LO, LP._CLAMP_HI) == (lo, hi), f"({LP._CLAMP_LO}, {LP._CLAMP_HI}) vs ({lo}, {hi})")

    real_opt, real_coord = T._optimize, T._coord_descent
    opt_calls: list = []
    coord_priors: list = []

    def spy_opt(labeled_, **kw):
        opt_calls.append(dict(kw))
        return real_opt(labeled_, **kw)

    def spy_coord(labeled_, **kw):
        coord_priors.append(dict(kw.get("prior") or {}))
        return real_coord(labeled_, **kw)

    T._optimize, T._coord_descent = spy_opt, spy_coord
    try:
        grid_res = T.tune_profiles(records=recs, min_labeled=4, min_labeled_bucket=4, seed_prior=dict(SEED))
        n_grid_opt = len(opt_calls)
        T.tune_profiles(records=recs, min_labeled=4, optimizer="coord", seed_prior=dict(SEED))
    finally:
        T._optimize, T._coord_descent = real_opt, real_coord
    anchored = [c for c in opt_calls[:n_grid_opt] if c.get("anchor") == SEED and c.get("prior") == SEED]
    n_folds = grid_res.get("n_folds") or 0
    check("the seed reaches the global fit AND every CV fold (each anchored to it and started from it)",
          n_folds >= 2 and len(anchored) >= 1 + n_folds,
          f"{len(anchored)} anchored _optimize call(s) of {n_grid_opt}; n_folds={n_folds}")
    check("coordinate descent starts from the seed (optimizer='coord')",
          bool(coord_priors) and coord_priors[0] == SEED, str(coord_priors[:1]))


def gate_c_teeth() -> None:
    print("\n-- C: the claim checker has teeth --")
    pre = bullet_problems(PRE_FIX_BULLET)
    check("the pre-fix bullet is flagged: no tie-break, no equal-score scoping, no grid, no CV folds",
          {CLAIMS[1][0], CLAIMS[2][0], CLAIMS[3][0], CLAIMS[4][0]} <= set(pre), str(pre))
    good = ("CONSUMPTION is offline-only: (a) a tuner WARM-START via tune_profiles(seed_prior=...), for the global "
            "fit and every CV fold: it is coordinate descent's starting point AND the tie-break anchor (among "
            "equally-scoring weight vectors the one closest to the seed wins, which is how the default grid "
            "optimizer honours it), and it never moves the equal-anchored clamp bounds or the grid - and (b) a "
            "RECOMMENDATION block. The seed is NEVER read by live ranking.")
    check("a bullet that names every claim passes", bullet_problems(good) == [], str(bullet_problems(good)))
    unscoped = good.replace("among equally-scoring weight vectors the one closest", "the weight vector closest")
    check("a bullet that drops the equal-score scoping (the seed would 'pick' the weights) is flagged",
          bullet_problems(unscoped) == [CLAIMS[2][0]], str(bullet_problems(unscoped)))
    reworded = good.replace("coordinate descent's starting point", "the starting point of coordinate descent") \
                   .replace("every CV fold", "every cross-validation fold")
    check("harmless rewording still passes (the starting point of coordinate descent / cross-validation fold)",
          bullet_problems(reworded) == [], str(bullet_problems(reworded)))
    check("a docstring without a CONSUMPTION bullet is flagged", bool(bullet_problems(consumption_bullet("x\n* A\n"))))
    doc = "Title.\n\n  * EXPORT only WINS.\n  * " + good + "\n  * One env gates it.\n"
    check("the bullet is cut at the next `* ` bullet (a claim elsewhere cannot satisfy it)",
          consumption_bullet(doc).endswith("NEVER read by live ranking.") and "One env" not in consumption_bullet(doc))


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
    print(f"DeepMap — F6: learned_prior's docstring vs what a seed does in the tuner (root: {root})")
    print("=" * 72)
    gate_a_docstring(root)
    try:
        gate_b_claims_true(root)
    except Exception as exc:  # noqa: BLE001 — a tree whose tuner lacks the seam is a failure, never a crash
        check("section B ran to completion", False, repr(exc))
    gate_c_teeth()
    total = len(PASSES) + len(FAILS)
    print("\n" + "=" * 72)
    if FAILS:
        print(f"RESULT: RED — {len(PASSES)}/{total} gates passed.")
        return 1
    print(f"RESULT: GREEN — {len(PASSES)}/{total} gates passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
