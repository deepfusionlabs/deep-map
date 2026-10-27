#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DeepMap RM-1 — the OPTIONAL user-level cold-start prior (LOCAL MACHINE ONLY, zero-egress).

Pools per-arm weight tendencies across THIS machine's projects into
<DEEPMAP_HOME>/learned_prior.json (~/.deepmap by default) so a NEW project below the cold-start
threshold doesn't have to start from nothing. Strictly honesty-first:

  * EXPORT only WINS: a project's weights enter the prior only when its own tune ended
    beats_equal AND significant — noise is never pooled.
  * The pooled vector is computed at READ time (n_labeled-weighted mean over fresh entries,
    clamped to the tuner's equal-anchored envelope); the consuming project's OWN entry is
    excluded (no self-echo); entries older than DEFAULT_PRIOR_MAX_AGE_DAYS are ignored; an
    unknown file version loads as None (fail-safe: no seed).
  * CONSUMPTION is offline-only: (a) a tuner WARM-START via tune_profiles(seed_prior=...), for the
    global fit and every CV fold: it is coordinate descent's starting point AND the tie-break anchor
    (among equally-scoring weight vectors the one closest to the seed wins, which is how the default
    grid optimizer honours it), and it never moves the equal-anchored clamp bounds or the grid — and
    (b) a `learned_fusion.seed` RECOMMENDATION block. The seed is NEVER read by live ranking
    (build/learned_fusion.py is byte-untouched and reads only arm_weights/profiles + the
    4-condition gate); the project's own learned state always shadows it; equal weights remain
    the ultimate fallback.
  * One env, DEEPMAP_SEED_PRIOR, gates the automatic export + in-loop consume; the explicit CLI
    flags (`deepmap tune --export-prior` / `--seed-from-prior`) work without it — an explicit
    command is consent. Everything never-raises and writes only to the two local files.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

PRIOR_FILENAME = "learned_prior.json"
PRIOR_VERSION = 1
PRIOR_KIND = "deepmap_learned_prior"
DEFAULT_PRIOR_MAX_AGE_DAYS = 180
SEED_ENV = "DEEPMAP_SEED_PRIOR"

_TRUE = {"1", "true", "yes", "on"}
ARM_NAMES = ("vector", "bm25", "graph_weighted")
# The tuner's equal-anchored safety envelope (mirrors tune_weights.DEFAULT_MAX_DEVIATION /
# DEFAULT_EXPLORATION_FLOOR): a pooled weight can never leave [max(floor, 1-dev), 1+dev].
_CLAMP_LO = max(0.25, 1.0 - 0.5)
_CLAMP_HI = 1.0 + 0.5


def seed_env_enabled(env=None) -> bool:
    """True iff DEEPMAP_SEED_PRIOR is truthy (the automatic export/consume opt-in). Never raises."""
    try:
        e = env if env is not None else os.environ
        return str(e.get(SEED_ENV, "")).strip().lower() in _TRUE
    except Exception:
        return False


def home_dir(env=None) -> Path:
    """DEEPMAP_HOME (~/.deepmap by default) — the same resolution provision.py/sync.py use."""
    e = env if env is not None else os.environ
    return Path(e.get("DEEPMAP_HOME", str(Path.home() / ".deepmap")))


def prior_path(env=None) -> Path:
    return home_dir(env) / PRIOR_FILENAME


def project_key(root) -> str:
    """A stable key for a project root (sha1 of the resolved forward-slash path, 12 hex chars)."""
    try:
        p = str(Path(root).resolve()).replace("\\", "/")
    except Exception:
        p = str(root)
    return hashlib.sha1(p.encode("utf-8", "replace")).hexdigest()[:12]


def load_prior(env=None):
    """The parsed prior, or None on absent / garbage / unknown version (fail-safe: no seed).
    Never raises."""
    try:
        data = json.loads(prior_path(env).read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("version") != PRIOR_VERSION:
            return None
        if not isinstance(data.get("projects"), dict):
            return None
        return data
    except Exception:
        return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _valid_weights(w) -> bool:
    return (isinstance(w, dict) and set(w) == set(ARM_NAMES)
            and all(isinstance(w.get(a), (int, float)) and w[a] > 0 for a in ARM_NAMES))


def export_prior(root, *, result=None, env=None):
    """Merge THIS project's learned win into the user prior. Refuses (None, reason) unless the
    result (or the stored learned_fusion) has beats_equal AND significant AND valid weights —
    noise is never exported. Replaces only this project's own entry. Writes ONLY
    <DEEPMAP_HOME>/learned_prior.json. Returns (path, reason). Never raises."""
    try:
        lf = result
        if lf is None:
            try:
                inst = Path(root) / ".deepmap" / "instance.json"
                lf = json.loads(inst.read_text(encoding="utf-8")).get("learned_fusion")
            except Exception:
                lf = None
        if not isinstance(lf, dict):
            return None, "no learned weights (run `deepmap tune` first)"
        if lf.get("beats_equal") is not True or lf.get("significant") is not True:
            return None, "not a significant win (only wins are pooled — noise is never exported)"
        w = lf.get("arm_weights")
        if not _valid_weights(w):
            return None, "stored arm_weights invalid"
        prior = load_prior(env) or {"version": PRIOR_VERSION, "kind": PRIOR_KIND, "projects": {}}
        profiles = lf.get("profiles") if isinstance(lf.get("profiles"), dict) else {}
        entry = {
            "root": str(Path(root).resolve()).replace("\\", "/"),
            "project": Path(root).resolve().name,
            "arm_weights": dict(w),
            "profiles": {b: {"arm_weights": dict(r.get("arm_weights")), "n_labeled": r.get("n_labeled")}
                         for b, r in profiles.items()
                         if isinstance(r, dict) and _valid_weights(r.get("arm_weights"))},
            "n_labeled_queries": lf.get("n_labeled_queries"),
            "objective": lf.get("objective"),
            "beats_equal": True,
            "significant": True,
            "generated_utc": lf.get("generated_utc"),
            "exported_utc": _now_iso(),
        }
        prior["projects"][project_key(root)] = entry
        prior["updated_utc"] = _now_iso()
        prior["note"] = ("Local-only cross-project weight tendencies (DeepMap local learning). "
                         "Recommendation input only; never read by live ranking. Safe to delete.")
        path = prior_path(env)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(prior, indent=2, sort_keys=True), encoding="utf-8")
        return path, "exported"
    except Exception as e:
        return None, f"export failed ({type(e).__name__})"


def _parse_utc(s):
    try:
        return datetime.strptime(str(s), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _clamp(v: float) -> float:
    return min(_CLAMP_HI, max(_CLAMP_LO, float(v)))


def pooled_prior(prior, *, exclude_root=None, max_age_days: int = DEFAULT_PRIOR_MAX_AGE_DAYS,
                 now=None):
    """The cross-project tendency: an n_labeled-weighted mean per arm over FRESH, significant
    entries — EXCLUDING the consuming project (no self-echo) — clamped to the tuner's
    equal-anchored envelope. Per-bucket profiles pooled the same way. None when nothing usable.
    `now` (ISO str or datetime) exists for deterministic tests. Never raises."""
    try:
        if not isinstance(prior, dict) or prior.get("version") not in (None, PRIOR_VERSION):
            return None
        if isinstance(prior.get("version"), int) and prior["version"] != PRIOR_VERSION:
            return None
        projects = prior.get("projects")
        if not isinstance(projects, dict):
            return None
        ref = _parse_utc(now) if isinstance(now, str) else (now or datetime.now(timezone.utc))
        skip_key = project_key(exclude_root) if exclude_root is not None else None
        usable = []
        for key, ent in projects.items():
            if not isinstance(ent, dict) or key == skip_key:
                continue
            if ent.get("beats_equal") is not True or ent.get("significant") is not True:
                continue
            if not _valid_weights(ent.get("arm_weights")):
                continue
            ts = _parse_utc(ent.get("exported_utc"))
            if ts is None or ref is None or (ref - ts).days > max_age_days:
                continue   # undatable/stale -> excluded (fail-safe)
            n = ent.get("n_labeled_queries")
            n = float(n) if isinstance(n, (int, float)) and n > 0 else 1.0
            usable.append((n, ent))
        if not usable:
            return None
        total_n = sum(n for n, _ in usable)
        arm_weights = {a: _clamp(sum(n * ent["arm_weights"][a] for n, ent in usable) / total_n)
                       for a in ARM_NAMES}
        # Pool per-bucket profile rows across the entries that have them.
        buckets: dict = {}
        for n, ent in usable:
            profs = ent.get("profiles") if isinstance(ent.get("profiles"), dict) else {}
            for b, row in profs.items():
                if isinstance(row, dict) and _valid_weights(row.get("arm_weights")):
                    rn = row.get("n_labeled")
                    rn = float(rn) if isinstance(rn, (int, float)) and rn > 0 else 1.0
                    buckets.setdefault(b, []).append((rn, row["arm_weights"]))
        profiles = {}
        for b, rows in buckets.items():
            bn = sum(rn for rn, _ in rows)
            profiles[b] = {"arm_weights": {a: _clamp(sum(rn * w[a] for rn, w in rows) / bn)
                                           for a in ARM_NAMES},
                           "n_labeled_total": bn}
        return {"arm_weights": arm_weights, "profiles": profiles,
                "n_projects": len(usable), "n_labeled_total": total_n}
    except Exception:
        return None


def write_seed_recommendation(root, pooled):
    """Record the pooled prior as `learned_fusion.seed` — a RECOMMENDATION block only. It writes
    NO gate keys (arm_weights/profiles/applied stay whatever the project itself earned), so the
    live resolver can never see it. Returns the instance path, or None. Never raises."""
    try:
        if not isinstance(pooled, dict):
            return None
        import sys as _sys
        _build = str(Path(__file__).resolve().parent)
        if _build not in _sys.path:
            _sys.path.insert(0, _build)
        import instance_state as _is
        seed = {
            "arm_weights": pooled.get("arm_weights"),
            "profiles": pooled.get("profiles"),
            "source": "user_prior",
            "n_projects": pooled.get("n_projects"),
            "n_labeled_total": pooled.get("n_labeled_total"),
            "pooled_utc": _now_iso(),
            "prior_version": PRIOR_VERSION,
            "note": ("recommendation only — never read by live ranking; used as a tuner "
                     "warm-start; equal weights remain the live fallback"),
        }
        return _is.merge_learned_fusion(root, {"seed": seed})
    except Exception:
        return None


def seed_for_tuner(root, *, env=None):
    """The pooled prior for THIS project's tuner warm-start, or None. Gated on DEEPMAP_SEED_PRIOR
    (the automatic path's opt-in); the CLI's explicit --seed-from-prior calls pooled_prior
    directly. Never raises."""
    try:
        if not seed_env_enabled(env):
            return None
        prior = load_prior(env)
        if prior is None:
            return None
        return pooled_prior(prior, exclude_root=root)
    except Exception:
        return None
