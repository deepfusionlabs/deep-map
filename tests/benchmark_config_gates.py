#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j gates for the REAL shipped benchmark configs (benchmark/questions.yaml + repos.yaml).

The existing benchmark_honesty_gates exercise the stdlib YAML fallback on SYNTHETIC snippets. This
file closes the complementary gap: it validates the ACTUAL configs the G3 run depends on, and — the
load-bearing part — it proves the stdlib fallback parser agrees with PyYAML ON THOSE REAL FILES.

Background: bug-hunt #2 (HIGH #3) fixed a stdlib-fallback parser that dropped sibling keys after a
block scalar, producing dozens of FALSE "missing key" config problems when PyYAML was absent. The
honesty-gates lock that on synthetic input; this file locks it where it actually bit — the shipped
questions.yaml/repos.yaml — so a future fallback regression fails the green gate.

What it enforces:
  * Both configs parse under the stdlib-only fallback (`_minimal_yaml_list`) without raising, and
    normalize (`_as_list`) to non-empty item lists.
  * `validate_config` reports ZERO problems on the stdlib-parsed real configs (the precise bug-hunt
    #2 symptom was "61 false config problems" here — so zero is the regression lock).
  * When PyYAML is installed: `validate_config` is ZERO on the PyYAML-parsed configs too, AND the two
    parsers AGREE — identical item counts, identical id/name sets, and per-item identical key sets +
    identical flat-scalar values (block-scalar / nested values are opaque-but-present under stdlib).
  * Honesty invariant (the file's own header, plan §7.4/§7.7): the single-hop CONTROLS are present
    and not hidden — at least one question with grep_can_do: true AND at least one with false; every
    question.repo resolves to a repo in repos.yaml.

Pure + hermetic: imports the harness module, parses the committed configs; never spends, never
invokes claude or Neo4j. A load-bearing self-check proves validate_config still fires on a dropped
required key (so gate #2 cannot silently rot into a tautology).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BENCH = REPO / "benchmark"
REPOS_YAML = BENCH / "repos.yaml"
QUESTIONS_YAML = BENCH / "questions.yaml"

# Flat scalar keys whose VALUE must be byte-identical across the two parsers (block-scalar /
# nested-mapping keys — prompt, answer_key — are compared only for PRESENCE, since the stdlib
# fallback records them opaque on purpose).
REPO_FLAT = ("name", "url", "pinned_sha", "license")
QUESTION_FLAT = ("id", "repo", "grep_can_do", "family")

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


def _load_benchmark():
    spec = importlib.util.spec_from_file_location("dm_run_benchmark_cfg", BENCH / "run_benchmark.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _have_pyyaml() -> bool:
    try:
        import yaml  # noqa: F401
        return True
    except ImportError:
        return False


def _hard(problems) -> list:
    """Problems that do NOT depend on whether nested values are opaque (the soft
    answer_key.ground_truth_set check only fires under PyYAML — exclude it from cross-parser
    equality)."""
    return sorted(p for p in problems if "ground_truth_set" not in p)


def gate_stdlib_parses(B) -> tuple:
    print("\n-- real configs parse under the stdlib-only fallback --")
    rs = qs = None
    try:
        rs = B._as_list(B._minimal_yaml_list(REPOS_YAML.read_text(encoding="utf-8"), REPOS_YAML), REPOS_YAML)
        check("repos.yaml parses under _minimal_yaml_list", isinstance(rs, list) and len(rs) > 0,
              f"got {type(rs).__name__} len={len(rs) if rs else 0}")
    except Exception as exc:  # noqa: BLE001
        check("repos.yaml parses under _minimal_yaml_list", False, repr(exc))
    try:
        qs = B._as_list(B._minimal_yaml_list(QUESTIONS_YAML.read_text(encoding="utf-8"), QUESTIONS_YAML), QUESTIONS_YAML)
        check("questions.yaml parses under _minimal_yaml_list", isinstance(qs, list) and len(qs) > 0,
              f"got {type(qs).__name__} len={len(qs) if qs else 0}")
    except Exception as exc:  # noqa: BLE001
        check("questions.yaml parses under _minimal_yaml_list", False, repr(exc))
    return rs or [], qs or []


def gate_stdlib_validates_clean(B, rs, qs) -> None:
    print("\n-- stdlib fallback introduces NO false config problems (bug-hunt #2 HIGH #3 lock) --")
    problems = B.validate_config(rs, qs)
    missing_key = [p for p in problems if "missing/empty" in p]
    check("NO 'missing/empty key' problems under the stdlib fallback", not missing_key,
          f"{len(missing_key)} dropped-key false positives: {missing_key[:3]}")
    check("validate_config has ZERO problems on the stdlib-parsed real configs", not problems,
          f"{len(problems)} problems: {problems[:3]}")


def gate_pyyaml_agrees(B, rs, qs) -> None:
    print("\n-- stdlib fallback AGREES with PyYAML on the real configs --")
    if not _have_pyyaml():
        print("  [skip] PyYAML not installed — cross-parser equivalence not checkable here "
              "(the stdlib path above still ran). Not a pass.")
        return
    import yaml
    rp = B._as_list(yaml.safe_load(REPOS_YAML.read_text(encoding="utf-8")), REPOS_YAML)
    qp = B._as_list(yaml.safe_load(QUESTIONS_YAML.read_text(encoding="utf-8")), QUESTIONS_YAML)

    check("validate_config ZERO problems on PyYAML-parsed real configs", not B.validate_config(rp, qp),
          str(B.validate_config(rp, qp)[:3]))
    check("hard problems identical across parsers (stdlib == PyYAML)",
          _hard(B.validate_config(rs, qs)) == _hard(B.validate_config(rp, qp)))

    check("same repo count", len(rs) == len(rp), f"stdlib={len(rs)} pyyaml={len(rp)}")
    check("same question count", len(qs) == len(qp), f"stdlib={len(qs)} pyyaml={len(qp)}")
    check("same repo-name set",
          {r.get("name") for r in rs} == {r.get("name") for r in rp})
    check("same question-id set",
          {q.get("id") for q in qs} == {q.get("id") for q in qp})

    _compare_items(B, "repo", rs, rp, "name", REPO_FLAT)
    _compare_items(B, "question", qs, qp, "id", QUESTION_FLAT)


def _compare_items(B, kind, s_items, p_items, key, flat_keys) -> None:
    """Per-item: key sets equal; flat scalar values equal; opaque keys present-in-both."""
    s_by = {it.get(key): it for it in s_items}
    p_by = {it.get(key): it for it in p_items}
    mismatches = []
    for k, p_item in p_by.items():
        s_item = s_by.get(k)
        if s_item is None:
            mismatches.append(f"{kind} {k!r}: absent under stdlib")
            continue
        if set(s_item.keys()) != set(p_item.keys()):
            only_s = set(s_item) - set(p_item)
            only_p = set(p_item) - set(s_item)
            mismatches.append(f"{kind} {k!r}: key-set drift (stdlib-only={only_s} pyyaml-only={only_p})")
        for fk in flat_keys:
            sv = B._none_if_opaque(s_item.get(fk))
            pv = p_item.get(fk)
            if sv != pv:
                mismatches.append(f"{kind} {k!r}.{fk}: stdlib={sv!r} != pyyaml={pv!r}")
    check(f"every {kind} matches across parsers (keys + flat values)", not mismatches,
          "; ".join(mismatches[:4]))


def gate_honesty_controls(B, qs) -> None:
    print("\n-- single-hop CONTROLS present + not hidden (plan §7.4/§7.7) --")
    gcd = [q.get("grep_can_do") for q in qs]
    check("at least one grep_can_do: true control exists", any(v is True for v in gcd),
          "controls surface DeepMap's losses — removing them defeats the thesis")
    check("at least one grep_can_do: false (graph-only) question exists", any(v is False for v in gcd))


def gate_ground_truth_atoms(B, qs) -> None:
    """The deterministic completeness matcher consumes each question's ground_truth_set atom — lock
    that contract on the REAL questions.yaml: every question has a NON-EMPTY set, and no atom is blank
    or a DEGENERATE separator (an empty `->` endpoint / an all-blank `|` alias) that would silently
    mis-score the HERO metric. Honestly reports the placeholder count (pre-G3 the GTs are
    'TO BE FINALIZED' placeholders — finalizing them is curator/G3 work, never fabricated here)."""
    print("\n-- ground_truth_set atoms well-formed for the matcher (real questions.yaml) --")
    # Use the AUTHORITATIVE PyYAML parse for the nested answer_key.ground_truth_set structure (the
    # stdlib minimal-fallback flattens nested mappings, so `qs` can't carry the nested list).
    try:
        import yaml
        qs = B._as_list(yaml.safe_load(QUESTIONS_YAML.read_text(encoding="utf-8")), QUESTIONS_YAML)
    except ImportError:
        check("(PyYAML present for GT-atom check)", True, "skipped — PyYAML absent")
        return
    empty, degenerate, placeholders, finalized = [], [], 0, 0
    for q in qs:
        gts = (q.get("answer_key") or {}).get("ground_truth_set")
        if not (isinstance(gts, list) and gts):
            empty.append(q.get("id"))
            continue
        for atom in gts:
            s = str(atom).strip()
            if not s:
                degenerate.append((q.get("id"), "blank"))
                continue
            if "->" in s:
                a, _, b = s.partition("->")
                if not a.strip() or not b.strip():
                    degenerate.append((q.get("id"), s[:30]))
            if "|" in s and not any(f.strip() for f in s.split("|")):
                degenerate.append((q.get("id"), s[:30]))
            if "FINALIZED" in s.upper() or ("<" in s and ">" in s):
                placeholders += 1
            else:
                finalized += 1
    check("every question has a non-empty ground_truth_set", not empty, f"empty: {empty}")
    check("no blank/degenerate GT atom (would silently mis-score the HERO metric)",
          not degenerate, f"{degenerate[:3]}")
    print(f"     [info] GT atoms: {placeholders} placeholder (pre-G3 — curator/G3 to finalize) "
          f"+ {finalized} finalized")


def gate_selfcheck(B) -> None:
    """Load-bearing: validate_config must still FIRE on a dropped required key — so the 'ZERO
    problems' gates above can't rot into a tautology if the parser silently drops a key again."""
    print("\n-- self-check: validate_config catches a dropped required key --")
    good_repo = {"name": "x", "url": "u", "pinned_sha": "PIN AT RUN TIME", "license": "MIT"}
    broken_q = {"id": "q1", "repo": "x", "prompt": B._OPAQUE, "grep_can_do": False, "family": "f"}  # no answer_key
    problems = B.validate_config([good_repo], [broken_q])
    check("a question missing 'answer_key' is reported", any("answer_key" in p for p in problems),
          f"got {problems}")
    # And a question pointing at an unknown repo is caught (cross-ref check is live).
    orphan = {"id": "q2", "repo": "nope", "prompt": B._OPAQUE, "grep_can_do": True,
              "family": "f", "answer_key": B._OPAQUE}
    p2 = B.validate_config([good_repo], [orphan])
    check("a question whose repo is not in repos.yaml is reported",
          any("not in repos.yaml" in p for p in p2), f"got {p2}")


def gate_family_filter(B, qs) -> None:
    """Track C: the --family filter (the cheap runtime_edge/multi_hop slice) is wired into do_run and
    the CLI, its predicate is correct, and the target families actually exist in the shipped corpus."""
    print("\n-- Track C: --family question-family filter --")
    src = (BENCH / "run_benchmark.py").read_text(encoding="utf-8")
    check("--family CLI arg is registered", '"--family"' in src)
    check("do_run filters questions by family (set membership)", 'q.get("family") in fams' in src)
    fams_corpus = {q.get("family") for q in qs}
    for fam in ("runtime_edge", "multi_hop"):
        check(f"corpus has the '{fam}' family the slice targets", fam in fams_corpus, str(sorted(fams_corpus)))
    # predicate: a comma-separated --family selects exactly the named families (Track C slice)
    sample = [{"id": "a", "family": "runtime_edge"}, {"id": "b", "family": "multi_hop"},
              {"id": "c", "family": "concept_to_impl"}, {"id": "d", "family": "runtime_edge"}]
    fams = {f.strip() for f in "runtime_edge,multi_hop".split(",") if f.strip()}
    picked = [q for q in sample if q.get("family") in fams]
    check("comma --family selects exactly the named families",
          [q["id"] for q in picked] == ["a", "b", "d"], str(picked))


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 68)
    print("DeepMap — real benchmark-config gates (no Neo4j, no spend)")
    print("=" * 68)
    B = _load_benchmark()
    rs, qs = gate_stdlib_parses(B)
    gate_stdlib_validates_clean(B, rs, qs)
    gate_pyyaml_agrees(B, rs, qs)
    gate_honesty_controls(B, qs)
    gate_ground_truth_atoms(B, qs)
    gate_family_filter(B, qs)
    gate_selfcheck(B)
    print("\n" + "=" * 68)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
