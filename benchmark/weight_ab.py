#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DeepMap RM-1 — the G4 evidence bridge: learned-vs-equal A/B on the gold benchmark.

The offline tuner validates learned weights on the retrieval log's OWN held-out slice (k-fold CV +
bootstrap CI). That is necessary but not sufficient for the G4 claim gate — G4 requires that learned
weights beat equal weights on the REAL benchmark (a pre-registered held-out slice). This module hosts
that A/B, and it REUSES the frozen deterministic scorer (benchmark.score_completeness) — it never
re-implements scoring and never calls a spend-y judge.

Two variants of the ON arm are captured on the SAME pinned graph / SAME questions / SAME OFF arm:
  * `deepmap_on@equal`   — the ON arm with the DEFAULT equal weights (DEEPMAP_APPLY_LEARNED unset),
  * `deepmap_on@learned` — the ON arm with learned weights: DEEPMAP_APPLY_LEARNED=1 in the served MCP
    server's env AND the learned block staged with applied:true in the SERVED project's (the benchmark
    clone's) .deepmap/instance.json — `stage_learned_weights` / `staged_learned_weights`, restored after.
Only that server env + the staged block differ (retrieval.py, the scorer, and the OFF arm are
untouched), so the comparison is clean. `ab_env` / `ab_arm_key` / the stage helpers are that seam, and
`ab_env`'s docstring is the full, ordered procedure.

`deterministic_ab` scores both captures with score_completeness — the ground truth read from
`answer_key.ground_truth_set`, the shipped questions.yaml schema — and returns overall + per-slice
completeness deltas + a bootstrap significance verdict. `build_g4_evidence` shapes it into the
`benchmark_evidence` block that `deepmap tune --apply` requires before it will flip `applied:true`,
bound by `weights_fingerprint` to the weights the learned capture served (a re-tune voids it).
"""
from __future__ import annotations

import contextlib
import copy
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
_BUILD = _REPO / "build"
for _p in (str(_HERE), str(_BUILD)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import score_completeness as _sc          # noqa: E402  (the FROZEN deterministic scorer — reused, not re-impl)
import tune_weights as _tw                # noqa: E402  (bootstrap_ci + seeded RNG + fingerprint — reused)
import instance_state as _is              # noqa: E402  (the ONE locked, atomic instance.json writer)

APPLY_ENV = "DEEPMAP_APPLY_LEARNED"
_GATE_KEYS = ("arm_weights", "profiles", "applied", "beats_equal", "significant")   # what the live gate reads
AB_STAGE_KEY = "ab_staged"   # the in-file marker of a staged learned capture (a lost token restores from it)


def ab_arm_key(variant: str) -> str:
    """Namespaced ON-arm key for the A/B ('equal' | 'learned') so the blind scorer grades each
    independently, exactly like any other arm."""
    return f"deepmap_on@{'learned' if str(variant).lower() == 'learned' else 'equal'}"


def ab_env(variant: str, base_env: dict | None = None) -> dict:
    """The MCP-SERVER launch ENV for a variant. The ON arm talks to the long-running deepmap-search
    server (a separate process); its deepmap_search tool checks apply_enabled() from ITS OWN env per
    request, so the twin-capture RELAUNCHES that server per variant with this env: 'learned' -> base +
    DEEPMAP_APPLY_LEARNED=1; 'equal' -> base with the flag REMOVED (equal-weight default).

    The env is only HALF of the learned variant. The server resolves learned weights only when the
    project it SERVES — its DEEPMAP_PROJECT_ROOT, i.e. the benchmark clone, not the project you tuned —
    also has learned_fusion.applied / beats_equal / significant:true in its .deepmap/instance.json
    (build/learned_fusion.py's 4-condition gate). stage_learned_weights writes exactly that for the
    capture and restore_learned_weights undoes it; `deepmap tune --apply` cannot be used for it (it needs
    the very evidence this A/B produces). The claude client argv, the OFF arm, retrieval.py and the
    scorer are untouched.

    The full G4 procedure (nothing in this module spends; the two ON-arm captures do — operator-run):
      1. `deepmap tune <tuned root>` produces a learned block (beats_equal + significant true).
      2. EQUAL capture: relaunch the clone's deepmap-search server with ab_env("equal", base) and run the
         ON arm on the gold questions -> run id A.
      3. LEARNED capture:
             with staged_learned_weights(<clone root>, <the tuned root's learned_fusion>) as token:
                 relaunch the server with ab_env("learned", base) (DEEPMAP_PROJECT_ROOT=<clone root>)
                 and run the ON arm -> run id B
         The block restores the clone's instance.json on exit (also on an exception) and raises
         ValueError instead of letting an equal-weight "learned" capture run. If
         token["restore"]["changed_during_capture"] is True another writer touched the file mid-capture:
         treat run B as suspect. A lost token (crashed driver): restore_learned_weights(<clone root>).
      4. ab = deterministic_ab(<answers of A>, <answers of B>, <questions>)  — {qid: answer_text} each;
         questions as shipped (answer_key.ground_truth_set).
      5. evidence = build_g4_evidence(ab, pre_registered_slice=..., run_id_equal=A, run_id_learned=B,
                                      weights_fingerprint=token["fingerprint"])
      6. write_benchmark_evidence(<tuned root>, evidence); then `deepmap tune --apply <tuned root>` flips
         applied:true ONLY while the tuned block's weights still match that weights_fingerprint (a re-tune
         since the A/B -> refused: re-run the A/B)."""
    env = dict(base_env if base_env is not None else {})
    if str(variant).lower() == "learned":
        env[APPLY_ENV] = "1"
    else:
        env.pop(APPLY_ENV, None)
    return env


# ---- the learned capture's SERVED-project state: stage -> capture -> restore (D1) -------------------
def _staged_values(learned_fusion):
    """(the gate values to stage, their weights fingerprint, None) — or (None, None, reason) when the
    candidate block cannot serve a meaningful learned capture."""
    if not isinstance(learned_fusion, dict):
        return None, None, "no learned_fusion block to stage (pass the tuned project's learned_fusion)"
    gw = learned_fusion.get("arm_weights")
    if not _tw._valid_weights(gw):
        return None, None, "the learned block has no valid arm_weights (all 3 arms, positive numbers)"
    prof = learned_fusion.get("profiles")
    profiles = ({str(b): copy.deepcopy(r) for b, r in prof.items() if isinstance(r, dict)}
                if isinstance(prof, dict) else {})
    staged = {"arm_weights": {a: float(gw[a]) for a in _tw.ARM_NAMES}, "profiles": profiles,
              "applied": True, "beats_equal": True, "significant": True}
    served = [staged["arm_weights"]] + [r["arm_weights"] for r in profiles.values()
                                        if _tw._valid_weights(r.get("arm_weights"))]
    if all(float(w[a]) == 1.0 for w in served for a in _tw.ARM_NAMES):
        return None, None, ("the learned weights ARE the equal weights (the tuner kept equal) — a 'learned' "
                            "capture would compare equal with equal; nothing to A/B")
    return staged, _tw.learned_weights_fingerprint(staged), None


def stage_learned_weights(served_root, learned_fusion) -> dict:
    """Stage a CANDIDATE learned block (the tuned project's learned_fusion) into the SERVED project's
    <served_root>/.deepmap/instance.json for the LEARNED capture — see ab_env for why the env flag alone
    serves equal weights. Writes the block's arm_weights + profiles with applied / beats_equal /
    significant true, keeps every other key, and records the values it replaced in an in-file
    `ab_staged` marker — ONE locked, atomic write (instance_state). Returns the token for
    restore_learned_weights: {ok, reason, path, fingerprint (of the weights the capture serves — pass it to
    build_g4_evidence), stage_id, ...}; keep it in memory (it holds the previous file's bytes). Refuses
    (ok False, the file untouched) equal-only or invalid weights, a root that is not a SYNCED DeepMap
    project (no .deepmap/graph_build.json — no MCP server can be serving it: a wrong root would leave the
    real clone serving equal weights), a clone where a capture is already staged, and an unreadable /
    non-object instance.json. Never raises."""
    token = {"ok": False, "reason": None, "served_root": str(served_root), "path": None,
             "fingerprint": None, "stage_id": None, "existed": False, "previous_bytes": None,
             "staged_bytes": None}
    try:
        staged, fingerprint, why = _staged_values(learned_fusion)
        if why:
            token["reason"] = why
            return token
        if not (Path(served_root) / ".deepmap" / "graph_build.json").is_file():
            token["reason"] = (f"{served_root} is not a synced DeepMap project (no .deepmap/graph_build.json), so "
                               "no MCP server serves it — pass the benchmark clone's sync root (the served "
                               "DEEPMAP_PROJECT_ROOT)")
            return token
        stage_id = os.urandom(8).hex()
        with _is.instance_lock(served_root) as path:
            if path is None:
                token["reason"] = "could not take the write lock of <served root>/.deepmap/instance.json"
                return token
            status, data, raw = _is.read_locked(path)
            if status in ("unreadable", "unparseable"):
                token["reason"] = f"the served instance.json is {status} — left untouched"
                return token
            cur = data.get("learned_fusion")
            lf = dict(cur) if isinstance(cur, dict) else {}
            if isinstance(lf.get(AB_STAGE_KEY), dict):
                token["reason"] = ("a learned capture is already staged here (stage_id "
                                   f"{lf[AB_STAGE_KEY].get('stage_id')}) — run restore_learned_weights"
                                   "(<served root>) first")
                return token
            lf[AB_STAGE_KEY] = {"stage_id": stage_id, "fingerprint": fingerprint, "staged_utc": _now_iso(),
                                "file_existed": raw is not None, "had_learned_fusion": isinstance(cur, dict),
                                "previous": {k: copy.deepcopy(lf[k]) for k in _GATE_KEYS if k in lf},
                                "staged": copy.deepcopy(staged)}
            lf.update(staged)
            data["learned_fusion"] = lf
            if not _is.replace_locked(path, data):
                token["reason"] = f"could not write {path}"
                return token
            _status, _data, now = _is.read_locked(path)
            token.update(ok=True, path=str(path), fingerprint=fingerprint, stage_id=stage_id,
                         existed=raw is not None, previous_bytes=raw, staged_bytes=now)
            return token
    except Exception as exc:
        token["reason"] = f"stage failed: {exc!r}"
        return token


def _undo_keys(data: dict, marker: dict) -> dict:
    """KEY-level undo of a stage: each gate key that still holds its staged value goes back to its previous
    value (or is removed when it had none); a key another writer changed is left alone; the marker goes."""
    lf = data.get("learned_fusion")
    lf = dict(lf) if isinstance(lf, dict) else {}
    staged = marker.get("staged") if isinstance(marker.get("staged"), dict) else {}
    prev = marker.get("previous") if isinstance(marker.get("previous"), dict) else {}
    for key in _GATE_KEYS:
        if key in lf and key in staged and lf[key] == staged[key]:
            if key in prev:
                lf[key] = prev[key]
            else:
                lf.pop(key)
    lf.pop(AB_STAGE_KEY, None)
    if lf or marker.get("had_learned_fusion"):
        data["learned_fusion"] = lf
    else:
        data.pop("learned_fusion", None)
    return data


def restore_learned_weights(token_or_root) -> dict:
    """Undo stage_learned_weights. With the stage TOKEN: when the served instance.json is still exactly
    what was staged, the previous file comes back BYTE-FOR-BYTE (or is removed when there was none);
    when another writer changed it during the capture, only the staged gate keys that still hold their
    staged values are undone (the other writer's changes are kept) and changed_during_capture is True —
    treat the learned capture as suspect. With the served ROOT instead (a lost token, a crashed driver):
    the in-file `ab_staged` marker drives the same key-level undo. One locked, atomic write. Returns
    {ok, restored: 'exact' | 'keys' | 'none', changed_during_capture, reason}. Never raises."""
    out = {"ok": False, "restored": "none", "changed_during_capture": None, "reason": None}
    try:
        token = token_or_root if isinstance(token_or_root, dict) else None
        if token is not None and not token.get("ok"):
            out.update(ok=True, reason="nothing was staged (the stage was refused)")
            return out
        root = token.get("served_root") if token is not None else token_or_root
        inst = _is._instance_path(root)
        if inst is None or not inst.parent.is_dir():   # never create .deepmap/ state just to find nothing
            out["reason"] = "no .deepmap/ under the served root — nothing staged there"
            return out
        with _is.instance_lock(root) as path:
            if path is None:
                out["reason"] = "could not take the write lock of <served root>/.deepmap/instance.json"
                return out
            status, data, raw = _is.read_locked(path)
            if token is not None and raw is not None and raw == token.get("staged_bytes"):
                done = (_is.replace_locked(path, token.get("previous_bytes") or b"") if token.get("existed")
                        else _is.remove_locked(path))
                out.update(ok=bool(done), restored="exact" if done else "none", changed_during_capture=False,
                           reason=None if done else f"could not restore {path}")
                return out
            changed = True if token is not None else None
            out["changed_during_capture"] = changed
            if status != "ok":
                out["reason"] = f"the served instance.json is {status} — the staged block could not be undone"
                return out
            lf = data.get("learned_fusion")
            marker = lf.get(AB_STAGE_KEY) if isinstance(lf, dict) else None
            if not isinstance(marker, dict) or (token is not None and marker.get("stage_id") != token.get("stage_id")):
                out["reason"] = "no staged learned capture found in the served instance.json (already restored?)"
                return out
            new = _undo_keys(data, marker)
            if not new and not marker.get("file_existed"):
                done = _is.remove_locked(path)
            else:
                done = _is.replace_locked(path, new)
            out.update(ok=bool(done), restored="keys" if done else "none",
                       reason=None if done else f"could not restore {path}")
            return out
    except Exception as exc:
        out["reason"] = f"restore failed: {exc!r}"
        return out


@contextlib.contextmanager
def staged_learned_weights(served_root, learned_fusion):
    """`with staged_learned_weights(<clone root>, <tuned learned_fusion>) as token:` — stage for the
    learned capture and ALWAYS restore on exit (an exception included); token["restore"] then holds
    restore_learned_weights()'s result and token["fingerprint"] the value for build_g4_evidence. RAISES
    ValueError when the stage is refused: a "learned" capture that silently served equal weights is the
    equal-vs-equal A/B this helper exists to prevent."""
    token = stage_learned_weights(served_root, learned_fusion)
    if not token.get("ok"):
        raise ValueError(f"learned weights NOT staged: {token.get('reason')}")
    try:
        yield token
    finally:
        token["restore"] = restore_learned_weights(token)


def _pct(answer_text, gts) -> float:
    try:
        return float(_sc.score_completeness(answer_text or "", gts or [])["completeness_pct"])
    except Exception:
        return 0.0


def _ground_truth_set(q: dict) -> list:
    """The question's completeness oracle: `answer_key.ground_truth_set` — the shipped questions.yaml
    schema, the field score_run / completeness_only / verify_atoms read — else a top-level
    `ground_truth_set` (a flattened per-repo fixture, questions.yaml's emit_mapping form). [] if neither."""
    ak = q.get("answer_key")
    if isinstance(ak, dict) and isinstance(ak.get("ground_truth_set"), (list, tuple)):
        return list(ak["ground_truth_set"])
    gts = q.get("ground_truth_set")
    return list(gts) if isinstance(gts, (list, tuple)) else []


def score_answer_set(answers: dict, questions: list) -> dict:
    """{qid: completeness_pct} for one arm's answers, via the FROZEN deterministic scorer.
    `answers` = {qid: answer_text}; `questions` = [{id, answer_key: {ground_truth_set, ...}, ...}] as
    shipped (a flattened top-level ground_truth_set is accepted too)."""
    out = {}
    for q in questions or []:
        if not isinstance(q, dict):
            continue
        qid = q.get("id")
        out[qid] = _pct((answers or {}).get(qid, ""), _ground_truth_set(q))
    return out


def deterministic_ab(equal_answers: dict, learned_answers: dict, questions: list, *,
                     slice_key: str = "family", iters: int = 1000) -> dict:
    """A/B learned vs equal ON-arm answers on the gold questions using ONLY the deterministic
    completeness scorer (no judge, no spend). Returns overall + per-slice deltas + a bootstrap
    significance verdict (CI lower bound > 0 on the per-question completeness deltas). Deterministic."""
    qs = [q for q in (questions or []) if isinstance(q, dict) and q.get("id") is not None]
    eq = score_answer_set(equal_answers, qs)
    le = score_answer_set(learned_answers, qs)
    deltas = []
    per_slice: dict = {}
    for q in qs:
        qid = q.get("id")
        d = le.get(qid, 0.0) - eq.get(qid, 0.0)
        deltas.append(d)
        bucket = q.get(slice_key) or "unspecified"
        per_slice.setdefault(bucket, {"deltas": [], "equal": [], "learned": []})
        per_slice[bucket]["deltas"].append(d)
        per_slice[bucket]["equal"].append(eq.get(qid, 0.0))
        per_slice[bucket]["learned"].append(le.get(qid, 0.0))
    n = len(deltas)
    overall = (sum(deltas) / n) if n else 0.0
    seed = _tw._seed_from_qids([q.get("id") for q in qs]) if qs else 1
    ci = _tw.bootstrap_ci(deltas, iters=iters, seed=seed) if deltas else {"lo": 0.0, "hi": 0.0, "mean": 0.0, "iters": 0}
    slices = {}
    for bucket, d in per_slice.items():
        ds = d["deltas"]
        slices[bucket] = {
            "n": len(ds),
            "delta": round(sum(ds) / len(ds), 4) if ds else 0.0,
            "equal_mean": round(sum(d["equal"]) / len(d["equal"]), 2) if d["equal"] else 0.0,
            "learned_mean": round(sum(d["learned"]) / len(d["learned"]), 2) if d["learned"] else 0.0,
        }
    return {
        "n_questions": n,
        "overall_completeness_delta": round(overall, 4),
        "equal_mean": round(sum(eq.values()) / len(eq), 2) if eq else 0.0,
        "learned_mean": round(sum(le.values()) / len(le), 2) if le else 0.0,
        "bootstrap": {"ci_lo": round(ci["lo"], 4), "ci_hi": round(ci["hi"], 4), "iters": ci["iters"]},
        "significant": bool(overall > 0 and ci["lo"] > 0.0),
        "per_slice": slices,
        "scorer": "benchmark.score_completeness (deterministic; no judge, no spend)",
    }


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_g4_evidence(ab: dict, *, pre_registered_slice: str | None = None,
                      run_id_equal: str | None = None, run_id_learned: str | None = None,
                      generated_utc: str | None = None, weights_fingerprint: str | None = None) -> dict:
    """Shape a deterministic_ab result into the `benchmark_evidence` block that `deepmap tune --apply`
    requires. apply_learned checks `present:true` + `significant` AND that `weights_fingerprint` — the
    fingerprint of the weights the LEARNED capture served (stage_learned_weights' token["fingerprint"], =
    tune_weights.learned_weights_fingerprint of that block) — equals the CURRENT learned weights' one;
    evidence without it (None) can never apply."""
    ab = ab or {}
    slice_delta = None
    if pre_registered_slice and isinstance(ab.get("per_slice"), dict):
        slice_delta = ab["per_slice"].get(pre_registered_slice, {}).get("delta")
    return {
        "present": True,
        "overall_completeness_delta": ab.get("overall_completeness_delta", 0.0),
        "significant": bool(ab.get("significant", False)),
        "pre_registered_slice": pre_registered_slice,
        "slice_delta": slice_delta,
        "n_questions": ab.get("n_questions", 0),
        "bootstrap": ab.get("bootstrap"),
        "per_slice": ab.get("per_slice"),
        "run_id_equal": run_id_equal,
        "run_id_learned": run_id_learned,
        "weights_fingerprint": weights_fingerprint,
        "scorer": ab.get("scorer"),
        "generated_utc": generated_utc or _now_iso(),
    }


def write_benchmark_evidence(root, evidence: dict):
    """Merge `benchmark_evidence` into <root>/.deepmap/instance.json learned_fusion — one locked, atomic
    read-modify-write (instance_state.merge_learned_fusion; an existing non-object file is left
    untouched, never wiped). Zero-egress, confined, never raises. Returns the path or None."""
    try:
        return _is.merge_learned_fusion(root, {"benchmark_evidence": evidence})
    except Exception:
        return None
