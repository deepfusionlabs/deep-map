#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j, no-spend gates for build/instance_state.py — the SHARED .deepmap/instance.json
read/merge helper used by every NEW learning-loop writer (drift guard, seed recommendation,
offline experiments). One confinement implementation instead of a fifth/sixth copy.

Locks:
  - read_instance: {} on absent/garbage/non-dict/bad-root; a valid file round-trips,
  - merge_learned_fusion: creates the file, MERGES (unrelated top-level keys + unrelated
    learned_fusion sub-keys like logging_enabled/benchmark_evidence survive), never raises,
  - write_experiment: writes under a TOP-LEVEL `experiments.<name>` key (a sibling of
    learned_fusion, never inside it) and UNCONDITIONALLY forces applied:false,
  - THE ISOLATION INVARIANT: no experiments block — however qualifying-looking — can ever
    reach live ranking: learned_fusion.resolve_arm_weights_for_live ignores experiments.*
    entirely (and learned_fusion.py's source never mentions "experiments"),
  - zero-egress: the only write target is <root>/.deepmap/instance.json.
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


QUALIFYING_LF = {
    "applied": True, "beats_equal": True, "significant": True,
    "arm_weights": {"vector": 1.5, "bm25": 0.75, "graph_weighted": 1.0},
}
# A maximally qualifying-LOOKING experiment block — must still never reach live ranking.
ADVERSARIAL_EXPERIMENT = {
    "applied": True, "beats_equal": True, "significant": True,
    "arm_weights": {"vector": 9.0, "bm25": 9.0, "graph_weighted": 9.0},
}


def gate_read_semantics(S) -> None:
    print("\n-- read_instance: {} on absent/garbage; valid round-trips; never raises --")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        check("absent file -> {}", S.read_instance(root) == {})
        inst = root / ".deepmap" / "instance.json"
        inst.parent.mkdir(parents=True, exist_ok=True)
        inst.write_text("{not json", encoding="utf-8")
        check("garbage JSON -> {}", S.read_instance(root) == {})
        inst.write_text("[1, 2]", encoding="utf-8")
        check("non-dict JSON -> {}", S.read_instance(root) == {})
        inst.write_text(json.dumps({"a": 1, "learned_fusion": {"x": 2}}), encoding="utf-8")
        check("valid file round-trips", S.read_instance(root) == {"a": 1, "learned_fusion": {"x": 2}})
    raised = False
    try:
        out = S.read_instance("\x00::bad::")
    except Exception:
        raised = True
        out = "raised"
    check("bad root -> {} , no raise", (not raised) and out == {}, str(out))


def gate_merge_learned_fusion(S) -> None:
    print("\n-- merge_learned_fusion: creates, MERGES, preserves unrelated keys, never raises --")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        out = S.merge_learned_fusion(root, {"drift": {"verdict": "ok"}})
        check("creates instance.json when absent", out is not None and Path(out).is_file(), str(out))
        data = json.loads((root / ".deepmap" / "instance.json").read_text(encoding="utf-8"))
        check("merge actually lands the key (teeth)", data.get("learned_fusion", {}).get("drift", {}).get("verdict") == "ok")
        # Pre-seed unrelated top-level + lf sub-keys; a merge must preserve them all.
        inst = root / ".deepmap" / "instance.json"
        inst.write_text(json.dumps({
            "enrichment": {"enabled": True},
            "learned_fusion": {"logging_enabled": True,
                               "benchmark_evidence": {"present": True, "significant": True},
                               "arm_weights": {"vector": 1.0, "bm25": 1.0, "graph_weighted": 1.0}},
        }), encoding="utf-8")
        S.merge_learned_fusion(root, {"drift": {"verdict": "degraded"}, "applied": False})
        data = json.loads(inst.read_text(encoding="utf-8"))
        lf = data.get("learned_fusion", {})
        check("unrelated TOP-LEVEL key survives (enrichment)", data.get("enrichment") == {"enabled": True})
        check("unrelated lf sub-key survives (logging_enabled)", lf.get("logging_enabled") is True)
        check("unrelated lf sub-key survives (benchmark_evidence)",
              lf.get("benchmark_evidence") == {"present": True, "significant": True})
        check("the update landed (drift + applied)",
              lf.get("drift", {}).get("verdict") == "degraded" and lf.get("applied") is False)
    raised = False
    try:
        out = S.merge_learned_fusion("\x00::bad::", {"x": 1})
    except Exception:
        raised = True
        out = "raised"
    check("bad root -> None, no raise", (not raised) and out is None, str(out))


def gate_write_experiment(S) -> None:
    print("\n-- write_experiment: TOP-LEVEL experiments.<name>, applied FORCED false, siblings kept --")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        inst = root / ".deepmap" / "instance.json"
        inst.parent.mkdir(parents=True, exist_ok=True)
        inst.write_text(json.dumps({"learned_fusion": dict(QUALIFYING_LF)}), encoding="utf-8")
        lf_before = json.dumps(json.loads(inst.read_text(encoding="utf-8"))["learned_fusion"], sort_keys=True)
        out = S.write_experiment(root, "access_prior", dict(ADVERSARIAL_EXPERIMENT))
        check("write_experiment returns the path", out is not None and Path(out).is_file(), str(out))
        data = json.loads(inst.read_text(encoding="utf-8"))
        exp = data.get("experiments", {}).get("access_prior", {})
        check("experiments.<name> is TOP-LEVEL (sibling of learned_fusion)",
              "experiments" in data and "experiments" not in data.get("learned_fusion", {}))
        check("applied FORCED to false even when handed true", exp.get("applied") is False, str(exp.get("applied")))
        check("the block's other fields land", exp.get("beats_equal") is True and "arm_weights" in exp)
        lf_after = json.dumps(data["learned_fusion"], sort_keys=True)
        check("learned_fusion byte-content untouched by an experiment write", lf_before == lf_after)
        # A second experiment must not clobber the first.
        S.write_experiment(root, "graph_signal", {"verdict": "insufficient_data"})
        data = json.loads(inst.read_text(encoding="utf-8"))
        check("sibling experiments coexist",
              "access_prior" in data.get("experiments", {}) and "graph_signal" in data.get("experiments", {}))
        check("read_experiments round-trips", set(S.read_experiments(root)) == {"access_prior", "graph_signal"})
    raised = False
    try:
        out = S.write_experiment("\x00::bad::", "x", {})
    except Exception:
        raised = True
        out = "raised"
    check("bad root -> None, no raise", (not raised) and out is None, str(out))
    check("read_experiments on a bad root -> {}", S.read_experiments("\x00::bad::") == {})


def gate_isolation_invariant(S, LF) -> None:
    print("\n-- ISOLATION: no experiments block can ever reach live ranking --")
    env_on = {"DEEPMAP_APPLY_LEARNED": "1"}
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        # Qualifying-LOOKING experiment, NO learned_fusion -> resolve must be None (equal).
        S.write_experiment(root, "access_prior", dict(ADVERSARIAL_EXPERIMENT))
        # write_experiment forces applied:false; make the block maximally adversarial by hand too.
        inst = root / ".deepmap" / "instance.json"
        data = json.loads(inst.read_text(encoding="utf-8"))
        data["experiments"]["access_prior"]["applied"] = True   # hand-forged
        inst.write_text(json.dumps(data), encoding="utf-8")
        got = LF.resolve_arm_weights_for_live(root, query="who calls foo", env=env_on)
        check("experiments-only instance -> resolve None (equal)", got is None, str(got))
        # Qualifying lf + adversarial experiments junk -> exactly the lf weights, never the junk.
        S.merge_learned_fusion(root, dict(QUALIFYING_LF))
        got = LF.resolve_arm_weights_for_live(root, env=env_on)
        check("qualifying lf wins; experiment junk ignored",
              got == QUALIFYING_LF["arm_weights"], str(got))
    src = (BUILD / "learned_fusion.py").read_text(encoding="utf-8")
    check('source contract: "experiments" never appears in learned_fusion.py', "experiments" not in src)


def gate_zero_egress(S) -> None:
    print("\n-- zero-egress: the only write target is <root>/.deepmap/instance.json --")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        S.merge_learned_fusion(root, {"a": 1})
        S.write_experiment(root, "x", {"b": 2})
        files = [p for p in root.rglob("*") if p.is_file()]
        check("only instance.json (+ its write-lock sidecar) exists under the root, inside .deepmap/",
              sorted(p.name for p in files) == ["instance.json", "instance.json.lock"]
              and all(p.parent == root / ".deepmap" for p in files), str(files))


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 68)
    print("DeepMap — instance_state gates (shared .deepmap state writer; no Neo4j, no spend)")
    print("=" * 68)
    try:
        S = _load("instance_state", BUILD)
    except Exception as e:
        check("build/instance_state.py loads", False, repr(e))
        print(f"\nRESULT: RED — {_PASS}/{_PASS + _FAIL} gates passed.")
        return 1
    LF = _load("learned_fusion", BUILD)

    gate_read_semantics(S)
    gate_merge_learned_fusion(S)
    gate_write_experiment(S)
    gate_isolation_invariant(S, LF)
    gate_zero_egress(S)

    print("\n" + "=" * 68)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
