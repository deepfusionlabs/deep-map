#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DeepMap RM-1 — the GATED live-wiring read side (opt-in, OFF by default, zero-egress).

This is the ONLY place that can feed learned weights into live search — and it is designed so the
shipped default is provably equal-weight (no-regression preserved) and `build/retrieval.py` stays
byte-frozen. `DeepMapRetriever.__init__` already accepts `arm_weights`; the CLI/MCP layer calls
`resolve_arm_weights_for_live(...)` and passes the result (None -> equal). retrieval.py is untouched.

Learned weights are applied to live ranking ONLY when ALL of these hold (a 4-condition AND-gate):
  1. env DEEPMAP_APPLY_LEARNED is truthy,
  2. .deepmap/instance.json learned_fusion.applied is True   (flipped only by `deepmap tune --apply`),
  3. learned_fusion.beats_equal is True,
  4. learned_fusion.significant is True                      (k-fold CV + bootstrap CI, design §1),
and the stored weights parse to a valid {arm: positive float} over exactly the 3 arms.

Any doubt -> None -> equal. The function NEVER raises. Because the env flag is unset in every shipped
config/installer/CI, the default is condition-1-false -> None -> equal -> identical to plain RRF.

This wiring is inert until gate G4 opens (learned > equal, significant, on the benchmark). Until then
`applied` stays false and this always returns None.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

_BUILD = Path(__file__).resolve().parent
import sys
if str(_BUILD) not in sys.path:
    sys.path.insert(0, str(_BUILD))
import retrieval_log as _rl  # noqa: E402  (ARM_NAMES, instance path, query features)

APPLY_ENV = "DEEPMAP_APPLY_LEARNED"
_TRUE = {"1", "true", "yes", "on"}
ARM_NAMES = tuple(_rl.ARM_NAMES)
EQUAL_WEIGHTS = {a: 1.0 for a in ARM_NAMES}


def apply_enabled(env=None) -> bool:
    """True iff the operator explicitly opted into applying learned weights (env flag). Never raises."""
    try:
        e = env if env is not None else os.environ
        return str(e.get(APPLY_ENV, "")).strip().lower() in _TRUE
    except Exception:
        return False


def _read_learned_fusion(root):
    try:
        path = Path(root) / ".deepmap" / _rl.INSTANCE_FILENAME
        data = json.loads(path.read_text(encoding="utf-8"))
        lf = data.get("learned_fusion")
        return lf if isinstance(lf, dict) else None
    except Exception:
        return None


def _valid_weights(w) -> bool:
    return (isinstance(w, dict) and set(w) == set(ARM_NAMES)
            and all(isinstance(w[a], (int, float)) and w[a] > 0 for a in ARM_NAMES))


def _select_weights(lf: dict, query):
    """The weights to use: a per-intent PROFILE row when `query` maps to a bucket that earned one,
    else the GLOBAL learned vector (the layered ladder: profile -> global). Returns a plain dict."""
    global_w = lf.get("arm_weights")
    profiles = lf.get("profiles") if isinstance(lf.get("profiles"), dict) else {}
    if query is not None and profiles:
        try:
            bucket = _rl.intent_bucket(_rl.query_features(query))
            row = profiles.get(bucket)
            if isinstance(row, dict) and _valid_weights(row.get("arm_weights")):
                return dict(row["arm_weights"])
        except Exception:
            pass
    return dict(global_w) if isinstance(global_w, dict) else None


def gate_reasons(root, *, env=None) -> list:
    """Human-readable reasons the learned weights are NOT applied (for --status / --report). An EMPTY
    list means all conditions are met (weights WOULD be applied when live). Never raises."""
    reasons = []
    if not apply_enabled(env):
        reasons.append(f"env {APPLY_ENV} not set (opt-in; default equal-weight)")
    lf = _read_learned_fusion(root)
    if not lf:
        reasons.append("no learned_fusion in .deepmap/instance.json (run `deepmap tune`)")
        return reasons
    if lf.get("applied") is not True:
        reasons.append("applied=false (run `deepmap tune --apply` after benchmark evidence)")
    if lf.get("beats_equal") is not True:
        reasons.append("beats_equal=false (learned did not beat equal)")
    if lf.get("significant") is not True:
        reasons.append("significant=false (improvement not statistically significant)")
    if not _valid_weights(lf.get("arm_weights")):
        reasons.append("stored arm_weights invalid")
    return reasons


def load_applied_weights(root, *, query=None, env=None):
    """The learned per-arm weights to apply to LIVE search, or None (-> equal). Returns a dict ONLY
    when the full 4-condition AND-gate passes AND the stored weights are valid. Never raises."""
    try:
        if not apply_enabled(env):
            return None
        lf = _read_learned_fusion(root)
        if not lf:
            return None
        if not (lf.get("applied") is True and lf.get("beats_equal") is True
                and lf.get("significant") is True):
            return None
        weights = _select_weights(lf, query)
        return weights if _valid_weights(weights) else None
    except Exception:
        return None


def resolve_arm_weights_for_live(root=None, *, query=None, env=None):
    """The CLI/MCP entry: resolve the live arm_weights for `query`, defaulting the root to the
    project root. None on any doubt (-> the retriever uses its equal-weight default). Never raises."""
    try:
        if root is None:
            root = _rl.default_root()
        if root is None:
            return None
        return load_applied_weights(root, query=query, env=env)
    except Exception:
        return None
