#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j, no-spend gates for the GATED live-wiring scaffold (build/learned_fusion.py) — the ONLY
path that can feed learned weights into live search. LOAD-BEARING no-regression proof:

  - DEFAULT-OFF: with a FULLY-qualifying instance.json but the env flag unset, load_applied_weights
    returns None (-> equal) — the shipped default is provably equal-weight,
  - 4-CONDITION AND-gate: env + applied + beats_equal + significant + valid weights all required;
    flipping ANY one to false returns None,
  - per-intent PROFILE resolution when the gate is open (impact query -> the impact profile),
  - NEVER raises (missing/garbage instance.json, bad root, bad env),
  - apply_learned is operator + benchmark gated (refuses without significant benchmark_evidence),
  - retrieval.py stays FROZEN: it references neither learned_fusion, instance.json, nor the env flag,
  - the CLI + MCP call sites are WIRED to the gated resolver.
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

QUAL = {"applied": True, "beats_equal": True, "significant": True,
        "arm_weights": {"vector": 0.5, "bm25": 1.5, "graph_weighted": 1.0}}
ON = {"DEEPMAP_APPLY_LEARNED": "1"}
OFF: dict = {}


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


def _write_instance(root: Path, lf: dict):
    d = root / ".deepmap"
    d.mkdir(parents=True, exist_ok=True)
    (d / "instance.json").write_text(json.dumps({"project": "deepmap", "learned_fusion": lf}), encoding="utf-8")


def gate_default_off(LF) -> None:
    print("\n-- DEFAULT-OFF: fully-qualifying instance.json but env unset -> None (equal) --")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write_instance(root, dict(QUAL))
        check("env unset -> load_applied_weights returns None", LF.load_applied_weights(root, env=OFF) is None)
        check("apply_enabled(off) is False", LF.apply_enabled(OFF) is False)
        check("env set + all conditions -> returns the stored weights",
              LF.load_applied_weights(root, env=ON) == QUAL["arm_weights"], str(LF.load_applied_weights(root, env=ON)))


def gate_all_conditions_required(LF) -> None:
    print("\n-- 4-condition AND-gate: flipping ANY one to false -> None --")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        # env set but applied=false / beats_equal=false / significant=false / invalid weights.
        for key, bad in (("applied", False), ("beats_equal", False), ("significant", False)):
            lf = dict(QUAL); lf[key] = bad
            _write_instance(root, lf)
            check(f"env on but {key}=false -> None", LF.load_applied_weights(root, env=ON) is None)
        lf = dict(QUAL); lf["arm_weights"] = {"vector": 0.0, "bm25": 1.0, "graph_weighted": 1.0}  # non-positive
        _write_instance(root, lf)
        check("env on but a weight is non-positive -> None", LF.load_applied_weights(root, env=ON) is None)
        lf = dict(QUAL); lf["arm_weights"] = {"vector": 1.0, "bm25": 1.0}  # missing an arm
        _write_instance(root, lf)
        check("env on but weights missing an arm -> None", LF.load_applied_weights(root, env=ON) is None)
        _write_instance(root, dict(QUAL))
        check("all-true row -> returns the dict", LF.load_applied_weights(root, env=ON) == QUAL["arm_weights"])


def gate_profile_resolution(LF) -> None:
    print("\n-- per-intent profile resolution (gate open) --")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        lf = dict(QUAL)
        lf["profiles"] = {"impact": {"arm_weights": {"vector": 0.5, "bm25": 0.5, "graph_weighted": 1.5}}}
        _write_instance(root, lf)
        impact = LF.load_applied_weights(root, env=ON, query="who calls parse_config")
        globalw = LF.load_applied_weights(root, env=ON, query="unrelated plain words here")
        check("an impact query resolves the impact PROFILE weights",
              impact == {"vector": 0.5, "bm25": 0.5, "graph_weighted": 1.5}, str(impact))
        check("a non-impact query falls back to the GLOBAL weights",
              globalw == QUAL["arm_weights"], str(globalw))


def gate_never_raises(LF) -> None:
    print("\n-- never raises (missing / garbage instance.json, bad root, bad env) --")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        check("no instance.json -> None", LF.load_applied_weights(root, env=ON) is None)
        (root / ".deepmap").mkdir(parents=True, exist_ok=True)
        (root / ".deepmap" / "instance.json").write_text("}{ not json", encoding="utf-8")
        check("garbage instance.json -> None", LF.load_applied_weights(root, env=ON) is None)
    for bad_root in ("\x00::bad::", None):
        try:
            out = LF.resolve_arm_weights_for_live(root=bad_root, env=ON)
            ok = out is None
        except Exception as exc:
            ok = False
            out = f"raised {exc}"
        check(f"resolve on a bad root -> None, no raise ({bad_root!r})", ok, str(out))


def gate_apply_gated(LF, T) -> None:
    print("\n-- apply_learned: operator + benchmark gated; a plain re-tune resets applied:false --")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        # beats + significant, but NO benchmark evidence -> refuse.
        _write_instance(root, dict(QUAL, applied=False))
        ok, reason = T.apply_learned(root, require_benchmark=True)
        check("refuses to apply without benchmark evidence", ok is False and "benchmark" in reason.lower(), reason)
        lf = json.loads((root / ".deepmap" / "instance.json").read_text(encoding="utf-8"))["learned_fusion"]
        check("applied stays false after a refused apply", lf.get("applied") is False)
        # With significant benchmark evidence -> applies.
        lf2 = dict(QUAL, applied=False, benchmark_evidence={"present": True, "significant": True,
                                                            "weights_fingerprint": T.learned_weights_fingerprint(QUAL)})
        _write_instance(root, lf2)
        ok, reason = T.apply_learned(root, require_benchmark=True)
        check("applies with significant benchmark_evidence", ok is True, reason)
        lf3 = json.loads((root / ".deepmap" / "instance.json").read_text(encoding="utf-8"))["learned_fusion"]
        check("applied flipped to true", lf3.get("applied") is True)
        # not-significant -> refuse.
        _write_instance(root, dict(QUAL, applied=False, significant=False,
                                   benchmark_evidence={"present": True, "significant": True}))
        ok, reason = T.apply_learned(root, require_benchmark=True)
        check("refuses when not statistically significant", ok is False, reason)


def gate_gate_reasons(LF) -> None:
    print("\n-- gate_reasons: non-empty when blocked, empty when it WOULD apply --")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write_instance(root, dict(QUAL))
        r_off = LF.gate_reasons(root, env=OFF)
        r_on = LF.gate_reasons(root, env=ON)
        check("env off -> a reason mentions the env flag", any("DEEPMAP_APPLY_LEARNED" in x for x in r_off), str(r_off))
        check("env on + all conditions -> empty (would apply)", r_on == [], str(r_on))


def gate_retrieval_frozen() -> None:
    print("\n-- retrieval.py stays FROZEN: no learned-weight read path in it --")
    src = (BUILD / "retrieval.py").read_text(encoding="utf-8")
    check("retrieval.py does not reference learned_fusion", "learned_fusion" not in src)
    check("retrieval.py does not read instance.json", "instance.json" not in src)
    check("retrieval.py does not reference the apply env flag", "DEEPMAP_APPLY_LEARNED" not in src)
    check("retrieval.py references NONE of this session's learning surfaces "
          "(DEEPMAP_LEARNING / learned_prior / DEEPMAP_SEED_PRIOR / experiments / "
          "access_prior / graph_signal)",
          all(tok not in src for tok in ("DEEPMAP_LEARNING", "learned_prior", "DEEPMAP_SEED_PRIOR",
                                         "experiments", "access_prior", "graph_signal")))


def gate_call_sites_wired() -> None:
    print("\n-- the CLI + MCP call sites are WIRED to the gated resolver --")
    cli = (BUILD / "deepmap_cli.py").read_text(encoding="utf-8")
    srv = (BUILD / "deepmap_search_server.py").read_text(encoding="utf-8")
    check("cmd_search resolves gated weights + passes arm_weights",
          "resolve_arm_weights_for_live" in cli and "arm_weights=arm_weights" in cli)
    check("MCP guards on apply_enabled before touching arm_weights",
          "apply_enabled" in srv and "resolve_arm_weights_for_live" in srv)


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 68)
    print("DeepMap — RM-1 gated live-wiring gates (default-off no-regression; no Neo4j, no spend)")
    print("=" * 68)
    LF = _load("learned_fusion", BUILD)
    T = _load("tune_weights", BUILD)

    gate_default_off(LF)
    gate_all_conditions_required(LF)
    gate_profile_resolution(LF)
    gate_never_raises(LF)
    gate_apply_gated(LF, T)
    gate_gate_reasons(LF)
    gate_retrieval_frozen()
    gate_call_sites_wired()

    print("\n" + "=" * 68)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
