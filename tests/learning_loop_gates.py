#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j, no-spend gates for the RM-1 LEARNING-LOOP MODE (one switch, one status line).

The mode = env DEEPMAP_LEARNING (or the durable instance flag learned_fusion.learning_enabled)
turning ON: retrieval logging + the post-sync offline re-tune + the drift guard. It NEVER implies
DEEPMAP_APPLY_LEARNED — applying learned weights to live ranking stays the separate 4-condition
gate (build/learned_fusion.py, byte-untouched).

Locks:
  - master-switch semantics: env truthy -> ON; explicit env falsy -> OFF (vetoes the instance
    flag); unset -> the instance flag; most-specific env wins (DEEPMAP_RETRIEVAL_LOG=0 still
    vetoes logging),
  - the switch implies LOGGING (retrieval_log.logging_enabled) but NEVER live application,
  - learning_loop.learning_status: an honest, never-raising status snapshot (events, labeled,
    last tune, profiles, ACTIVE/inactive + why),
  - format_status_line: the ONE line doctor + `tune --status` share,
  - source wiring: sync's _maybe_auto_tune consults learning_enabled; the doctor prints the line.
"""
from __future__ import annotations

import importlib.util
import json
import os
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


def _write_instance(root: Path, lf: dict) -> None:
    inst = root / ".deepmap" / "instance.json"
    inst.parent.mkdir(parents=True, exist_ok=True)
    inst.write_text(json.dumps({"learned_fusion": lf}), encoding="utf-8")


def _seed_log(root: Path, L, n=8):
    """A log with clear signal: bm25 surfaces the accessed 'p' first (the tune_cli_gates idiom)."""
    per_arm = {"vector": ["x", "p"], "bm25": ["p", "x"], "graph_weighted": ["x", "p"]}
    for i in range(n):
        returned = [{"id": rid, "file": f"f/{rid}.py", "name": rid, "line": 1} for rid in ("x", "p")]
        L.append_record(root, L.build_record(f"q{i}", per_arm, returned, ts=f"t{i}"))
        L.append_record(root, {"type": "access", "ts": f"t{i}b", "paths": ["f/p.py"], "symbols": [], "kind": "edit"})


QUAL = {"applied": True, "beats_equal": True, "significant": True,
        "arm_weights": {"vector": 1.25, "bm25": 0.75, "graph_weighted": 1.0}}


def gate_master_switch(L) -> None:
    print("\n-- DEEPMAP_LEARNING master switch: env truthy/falsy/unset x instance flag --")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        check("default (no env, no instance) -> OFF", L.learning_enabled(root, env={}) is False)
        check("env truthy -> ON", L.learning_enabled(root, env={"DEEPMAP_LEARNING": "1"}) is True)
        check("env 'true' -> ON", L.learning_enabled(root, env={"DEEPMAP_LEARNING": "true"}) is True)
        _write_instance(root, {"learning_enabled": True})
        check("instance flag true, env unset -> ON", L.learning_enabled(root, env={}) is True)
        check("explicit env falsy VETOES the instance flag",
              L.learning_enabled(root, env={"DEEPMAP_LEARNING": "0"}) is False)
        _write_instance(root, {"learning_enabled": False})
        check("instance flag false, env unset -> OFF", L.learning_enabled(root, env={}) is False)
        inst = root / ".deepmap" / "instance.json"
        inst.write_text("{garbage", encoding="utf-8")
        check("garbage instance -> OFF, no raise", L.learning_enabled(root, env={}) is False)
    check("root None, env unset -> OFF", L.learning_enabled(None, env={}) is False)
    raised = False
    try:
        out = L.learning_enabled("\x00::bad::", env={})
    except Exception:
        raised = True
        out = "raised"
    check("bad root -> OFF, no raise", (not raised) and out is False, str(out))


def gate_implies_logging(L) -> None:
    print("\n-- the switch implies LOGGING; DEEPMAP_RETRIEVAL_LOG=0 still vetoes (most-specific wins) --")
    saved = {k: os.environ.get(k) for k in ("DEEPMAP_LEARNING", "DEEPMAP_RETRIEVAL_LOG")}
    try:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            os.environ.pop("DEEPMAP_RETRIEVAL_LOG", None)
            os.environ.pop("DEEPMAP_LEARNING", None)
            check("baseline: logging OFF", L.logging_enabled(root) is False)
            os.environ["DEEPMAP_LEARNING"] = "1"
            check("DEEPMAP_LEARNING=1 -> logging ON", L.logging_enabled(root) is True)
            os.environ["DEEPMAP_RETRIEVAL_LOG"] = "0"
            check("DEEPMAP_RETRIEVAL_LOG=0 vetoes even the master switch", L.logging_enabled(root) is False)
            os.environ.pop("DEEPMAP_RETRIEVAL_LOG", None)
            os.environ.pop("DEEPMAP_LEARNING", None)
            _write_instance(root, {"learning_enabled": True})
            check("instance learning_enabled -> logging ON", L.logging_enabled(root) is True)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def gate_never_implies_apply(L, LF) -> None:
    print("\n-- the master switch NEVER implies live application (the 4-condition gate is separate) --")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write_instance(root, dict(QUAL, learning_enabled=True))
        env = {"DEEPMAP_LEARNING": "1"}   # learning ON, DEEPMAP_APPLY_LEARNED absent
        check("learning ON via env", L.learning_enabled(root, env=env) is True)
        got = LF.resolve_arm_weights_for_live(root, env=env)
        check("resolve is None (equal) — learning != applying", got is None, str(got))
        reasons = LF.gate_reasons(root, env=env)
        check("gate_reasons names DEEPMAP_APPLY_LEARNED as the blocker",
              any("DEEPMAP_APPLY_LEARNED" in r for r in reasons), str(reasons))


def gate_status_dict(LL, L, T) -> None:
    print("\n-- learning_status: an honest snapshot of the loop (events, labeled, last tune, gate) --")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        st = LL.learning_status(root, env={})
        check("empty root: enabled False, no source", st.get("enabled") is False and st.get("source") is None)
        check("empty root: zero counts", st.get("n_records") == 0 and st.get("n_search") == 0)
        _seed_log(root, L)
        res = T.tune_profiles(root=root, min_labeled=4)
        T.write_instance_weights(root, res)
        st = LL.learning_status(root, env={"DEEPMAP_LEARNING": "1"})
        check("enabled True, source env", st.get("enabled") is True and st.get("source") == "env")
        check("agrees with retrieval_log.learning_enabled (drift-lock)",
              st.get("enabled") == L.learning_enabled(root, env={"DEEPMAP_LEARNING": "1"}))
        check("counts: 16 records / 8 searches", st.get("n_records") == 16 and st.get("n_search") == 8,
              f"{st.get('n_records')}/{st.get('n_search')}")
        check("labeled count matches build_qrels", st.get("n_labeled") == 8, str(st.get("n_labeled")))
        check("last tune recorded", bool(st.get("last_tune_utc")) and st.get("last_reason") == res.get("reason"),
              f"{st.get('last_tune_utc')} {st.get('last_reason')}")
        check("live INACTIVE with reasons (applied:false)", st.get("active") is False
              and len(st.get("inactive_reasons") or []) >= 1, str(st.get("inactive_reasons")))
        _write_instance(root, {"learning_enabled": True})
        st = LL.learning_status(root, env={})
        check("source 'instance' when the durable flag drives it", st.get("source") == "instance")


def gate_status_never_raises(LL) -> None:
    print("\n-- learning_status never raises --")
    for label, root in (("bad root", "\x00::bad::"), ("None root", None)):
        raised = False
        try:
            st = LL.learning_status(root, env={})
        except Exception:
            raised = True
            st = None
        check(f"{label} -> a dict, no raise", (not raised) and isinstance(st, dict))
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        inst = root / ".deepmap" / "instance.json"
        inst.parent.mkdir(parents=True, exist_ok=True)
        inst.write_text("{garbage", encoding="utf-8")
        (root / ".deepmap" / "retrieval_log.jsonl").write_text("not json\n", encoding="utf-8")
        raised = False
        try:
            st = LL.learning_status(root, env={})
        except Exception:
            raised = True
            st = None
        check("garbage instance + garbage log -> a dict, no raise", (not raised) and isinstance(st, dict))


def gate_line_format(LL) -> None:
    print("\n-- format_status_line: the ONE line doctor + tune --status share --")
    state, detail = LL.format_status_line({"enabled": False, "source": None})
    check("OFF line: state OK (informational)", state == "OK", state)
    check("OFF line names BOTH switches", "DEEPMAP_LEARNING" in detail and "learning_enabled" in detail, detail)
    on = {"enabled": True, "source": "env", "n_records": 212, "n_search": 200, "n_labeled": 167,
          "last_tune_utc": "2026-07-10T18:22:00Z", "last_reason": "learned_beats_equal",
          "profiles": ["impact", "symbol_short"], "applied": False, "active": False,
          "inactive_reasons": ["env DEEPMAP_APPLY_LEARNED not set (opt-in; default equal-weight)"],
          "drift": {"verdict": "ok"}, "log_exists": True}
    state, detail = LL.format_status_line(on)
    check("ON line: state OK when drift ok", state == "OK", state)
    check("ON line carries source/events/labeled", "ON (env)" in detail and "212" in detail and "167" in detail, detail)
    check("ON line carries last tune + reason", "2026-07-10T18:22:00Z" in detail and "learned_beats_equal" in detail, detail)
    check("ON line carries profiles", "impact" in detail and "symbol_short" in detail, detail)
    check("ON line: live INACTIVE embeds the FIRST gate reason (teeth)",
          "INACTIVE" in detail and "DEEPMAP_APPLY_LEARNED" in detail, detail)
    state, detail = LL.format_status_line(dict(on, drift={"verdict": "degraded", "reverted": True}))
    check("drift degraded -> WARN + says so", state == "WARN" and "degraded" in detail, f"{state}: {detail}")
    active = dict(on, active=True, inactive_reasons=[])
    state, detail = LL.format_status_line(active)
    check("ACTIVE renders when the gate is fully open", "ACTIVE" in detail and "INACTIVE" not in detail, detail)


def gate_wiring_source() -> None:
    print("\n-- source wiring: sync consults the switch; doctor + tune --status print the line --")
    sync_src = (BUILD / "sync.py").read_text(encoding="utf-8")
    idx = sync_src.find("def _maybe_auto_tune")
    body = sync_src[idx:idx + 2400] if idx > 0 else ""
    check("_maybe_auto_tune consults learning_enabled (master switch)", "learning_enabled" in body, body[:200])
    check("rotation stays UNCONDITIONAL (before the enable check)",
          0 < body.find("rotate_log") < body.find("_rl.learning_enabled(root)"))
    cli_src = (BUILD / "deepmap_cli.py").read_text(encoding="utf-8")
    check("doctor prints the learning line", 'line(' in cli_src and '"learning"' in cli_src
          and "learning_status" in cli_src)
    check("tune --status shares format_status_line (one source of truth)",
          cli_src.count("format_status_line") >= 2, str(cli_src.count("format_status_line")))


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 68)
    print("DeepMap — learning-loop mode gates (one switch, one line; no Neo4j, no spend)")
    print("=" * 68)
    L = _load("retrieval_log", BUILD)
    if str(BUILD) not in sys.path:
        sys.path.insert(0, str(BUILD))
    LF = _load("learned_fusion", BUILD)
    T = _load("tune_weights", BUILD)
    try:
        LL = _load("learning_loop", BUILD)
    except Exception as e:
        check("build/learning_loop.py loads", False, repr(e))
        print(f"\nRESULT: RED — {_PASS}/{_PASS + _FAIL} gates passed.")
        return 1

    gate_master_switch(L)
    gate_implies_logging(L)
    gate_never_implies_apply(L, LF)
    gate_status_dict(LL, L, T)
    gate_status_never_raises(LL)
    gate_line_format(LL)
    gate_wiring_source()

    print("\n" + "=" * 68)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
