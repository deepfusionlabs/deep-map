#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DeepMap RM-1 — the learning-loop STATUS surface (pure, read-only, no Neo4j, zero-egress).

One honest snapshot of the offline learning loop, shared by `deepmap doctor` (one line) and
`deepmap tune --status` (the same line as its header) so there is a single source of truth for
"is the loop on, what has it seen, and why is live ranking still equal-weight".

The loop itself = env DEEPMAP_LEARNING / instance learned_fusion.learning_enabled turning on:
retrieval logging + the post-sync offline re-tune + the drift guard (build/sync.py). It NEVER
implies DEEPMAP_APPLY_LEARNED — the 4-condition live gate (build/learned_fusion.py) is separate
and byte-untouched; `active` / `inactive_reasons` below simply REPORT that gate via
learned_fusion.gate_reasons. Everything here READS; nothing here writes or changes ranking.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_BUILD = Path(__file__).resolve().parent
if str(_BUILD) not in sys.path:
    sys.path.insert(0, str(_BUILD))
import retrieval_log as _rl      # noqa: E402
import instance_state as _is     # noqa: E402
import learned_fusion as _lf     # noqa: E402

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}

DEFAULT_TARGET_LABELED = 150   # mirror of tune_weights.DEFAULT_MIN_LABELED (the cold-start threshold)


def labeled_fragment(n_labeled, target: int = DEFAULT_TARGET_LABELED) -> str:
    """The shared 'X/150 labeled retrieval logs' accrual wording — used by the doctor/`tune --status`
    line AND `deepmap status`. `n_labeled` None (tuner unavailable) renders '?/150'. Pure; never raises."""
    n = n_labeled if n_labeled is not None else "?"
    return f"{n}/{target} labeled retrieval logs"


def _source(root, e) -> str | None:
    """Where the enabled state comes from: 'env' | 'instance' | None (off/default)."""
    v = str(e.get(_rl.LEARNING_ENV, "")).strip().lower()
    if v in _TRUE or v in _FALSE:
        return "env"
    try:
        lf = _is.read_instance(root).get("learned_fusion")
        if isinstance(lf, dict) and lf.get("learning_enabled") is True:
            return "instance"
    except Exception:
        pass
    return None


def learning_status(root, *, env=None) -> dict:
    """A never-raising snapshot: {enabled, source, n_records, n_search, n_access, n_labeled,
    last_tune_utc, last_reason, arm_weights, profiles(list), applied, beats_equal, significant,
    active, inactive_reasons(list), drift, seed, log_exists}. `n_labeled` is None when the tuner
    is unavailable (unknown, not zero). Pure read — no Neo4j, no writes."""
    st = {"enabled": False, "source": None, "n_records": 0, "n_search": 0, "n_access": 0,
          "n_labeled": 0, "target_labeled": DEFAULT_TARGET_LABELED, "last_tune_utc": None,
          "last_reason": None, "arm_weights": None,
          "profiles": [], "applied": None, "beats_equal": None, "significant": None,
          "active": False, "inactive_reasons": [], "drift": None, "seed": None,
          "log_exists": False, "vetoed": False, "instance_flag": None}
    try:
        e = env if env is not None else os.environ
        st["enabled"] = _rl.learning_enabled(root, env=e)
        st["source"] = _source(root, e) if st["enabled"] else (
            "env" if str(e.get(_rl.LEARNING_ENV, "")).strip().lower() in _FALSE else None)
        # An explicit falsy DEEPMAP_LEARNING vetoes the durable project flag: say so (and whether the
        # flag is on) instead of advising `--enable`, which cannot help while the env veto stands.
        st["vetoed"] = (not st["enabled"]) and st["source"] == "env"
        try:
            _flag = (_is.read_instance(root).get("learned_fusion") or {}).get("learning_enabled") \
                if root is not None else None
            st["instance_flag"] = _flag if isinstance(_flag, bool) else None
        except Exception:
            st["instance_flag"] = None
        if root is not None:
            try:
                st["log_exists"] = _rl.log_path(root).is_file()
            except Exception:
                pass
        recs = _rl.read_records(root) if root is not None else []
        st["n_records"] = len(recs)
        st["n_search"] = sum(1 for r in recs if r.get("type") == "search")
        st["n_access"] = sum(1 for r in recs if r.get("type") == "access")
        try:
            import tune_weights as _tw
            st["n_labeled"] = len(_tw.build_qrels(recs))
            st["target_labeled"] = _tw.DEFAULT_MIN_LABELED
        except Exception:
            st["n_labeled"] = None
        lf = _is.read_instance(root).get("learned_fusion") if root is not None else None
        if isinstance(lf, dict):
            st["last_tune_utc"] = lf.get("generated_utc")
            st["last_reason"] = lf.get("reason")
            st["arm_weights"] = lf.get("arm_weights") if isinstance(lf.get("arm_weights"), dict) else None
            st["applied"] = lf.get("applied")
            st["beats_equal"] = lf.get("beats_equal")
            st["significant"] = lf.get("significant")
            prof = lf.get("profiles")
            st["profiles"] = sorted(prof.keys()) if isinstance(prof, dict) else []
            drift = lf.get("drift")
            if isinstance(drift, dict):
                st["drift"] = {"verdict": drift.get("verdict"),
                               "checked_utc": drift.get("checked_utc"),
                               "reverted": drift.get("reverted")}
            seed = lf.get("seed")
            if isinstance(seed, dict):
                st["seed"] = {"present": True, "n_projects": seed.get("n_projects")}
        try:
            reasons = _lf.gate_reasons(root, env=e) if root is not None else ["no project root"]
        except Exception:
            reasons = ["gate state unreadable"]
        if root is not None and isinstance(lf, dict) and not _is.has_tune_result(lf):
            # A `learning --enable/--disable` block is not a tune: its absent fields would read as
            # "beats_equal=false / arm_weights invalid" — i.e. a tune that ran and lost. Say "no tune".
            reasons = [r for r in reasons if _lf.APPLY_ENV in str(r)] + [
                f"no tune yet (run {_cli('tune')} after collecting labeled queries)"]
        st["inactive_reasons"] = list(reasons)
        st["active"] = not reasons
    except Exception:
        pass
    return st


def _cli(sub: str) -> str:
    """A RUNNABLE command for a hint (the absolute deepmap_cli.py path — no `deepmap` executable is
    installed). Never raises."""
    try:
        return _rl.cli_command(sub)
    except Exception:
        return f"deepmap_cli.py {sub}"


def format_status_line(status: dict) -> tuple:
    """(state, detail) for a doctor-style `line(state, "learning", detail)`. Pure string builder;
    never raises. WARN only when the loop is ON and the latest drift verdict is 'degraded' — the
    line is otherwise informational and never flips the doctor verdict."""
    try:
        if isinstance(status, dict) and not status.get("enabled") and status.get("vetoed"):
            return ("OK", "OFF — DEEPMAP_LEARNING is set falsy in the environment, which vetoes "
                          + ("the project's ENABLED flag" if status.get("instance_flag") is True
                             else "the project flag")
                          + "; unset it to let instance.json learned_fusion.learning_enabled decide "
                            "(docs/RM1-ENABLE.md)")
        if not isinstance(status, dict) or not status.get("enabled"):
            return ("OK", "OFF (opt-in) — set DEEPMAP_LEARNING=1 or instance.json "
                          "learned_fusion.learning_enabled:true to turn on the offline learning "
                          "loop (docs/RM1-ENABLE.md)")
        drift = status.get("drift") if isinstance(status.get("drift"), dict) else {}
        state = "WARN" if drift.get("verdict") == "degraded" else "OK"
        parts = [f"ON ({status.get('source') or '?'})"]
        n_lab = status.get("n_labeled")
        # A LITERAL 150 fallback — never import tune_weights here (this builder is bare-try-wrapped;
        # a raise would collapse the whole line to "status unavailable").
        target = status.get("target_labeled") or DEFAULT_TARGET_LABELED
        parts.append(f"events {status.get('n_records', 0)}")
        parts.append(labeled_fragment(n_lab, target))
        if status.get("last_tune_utc"):
            parts.append(f"last tune {status['last_tune_utc']} reason={status.get('last_reason')}")
        else:
            parts.append("no tune yet")
        profiles = status.get("profiles") or []
        parts.append("profiles " + (",".join(profiles) if profiles else "none"))
        if drift:
            d = f"drift {drift.get('verdict')}"
            if drift.get("reverted"):
                d += " (REVERTED to equal)"
            parts.append(d)
        if status.get("active"):
            parts.append("live ACTIVE (all gate conditions met)")
        else:
            reasons = status.get("inactive_reasons") or []
            parts.append("live INACTIVE — " + (reasons[0] if reasons else "gate closed"))
        if status.get("log_exists"):
            parts.append(f"summarize: {_cli('insights')}")
        return (state, "; ".join(parts))
    except Exception:
        return ("OK", "status unavailable")


def accrual_line(root, *, env=None) -> str:
    """One honest 'X/150 labeled retrieval logs' line for `deepmap status` (and the enable command).
    Reads the local log via learning_status; never raises — returns '0/150 …' when the log is
    absent/off. No Neo4j, zero-egress."""
    try:
        st = learning_status(root, env=env)
        return labeled_fragment(st.get("n_labeled"), st.get("target_labeled") or DEFAULT_TARGET_LABELED)
    except Exception:
        return labeled_fragment(0)
