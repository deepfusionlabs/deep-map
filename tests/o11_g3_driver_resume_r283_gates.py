#!/usr/bin/env python3
"""0.28.3 (O11) — the G3 driver measures the G3 set on THIS DeepMap's graph, and never reuses a capture of another graph.

Finding (DeepInit refresh 2026-09-29, validated) — ISS-benchmark:005 (D10): `benchmark/run_g3_n4.sh` is THE documented
one-command G3 run ("across all 5 pinned repos"), but it built its repo list from EVERY repos.yaml entry (9 since the
multi-language breadth wing was added) and resumed by deterministic run-id (`g3n4-<repo>`): a repo whose local
results.json said `measured` was skipped. The pending "re-run G3 on the 0.28 default graph" would therefore have
reused the five July captures (measured on a v0.9.0-era graph), then died at the first missing breadth clone or mixed
all nine into `g3n4-combined` under a banner claiming 5 repos and a ground truth "still a CURATOR DRAFT" (signed off
2026-07-08). Nothing recorded WHICH graph a capture measured, so nothing could tell.

Locks (hermetic: temp results dirs / clones, fake manifests, stubbed config; no claude, no Neo4j, no spend):
  A. the driver: the default set is exactly the G3 scope recorded in benchmark/results/G3_N4_RESULT.md (checked only
     where that private results file exists; each name in
     repos.yaml with a 40-hex pin, none from the breadth wing), REPOS= overrides it, run-ids and the combined id carry
     RUN_TAG (default: v<DeepMap version>), resumption goes through run_benchmark.resume_state_for and a `stale` state
     stops the driver, every --score reads the pinned clones, the header / banner no longer claim "all 5 repos" or a
     curator-draft ground truth; `bash -n` passes.
  B. resume_state (pure): nothing captured / no captures -> none; a capture with no recorded graph provenance (every
     pre-0.28.3 result, the July G3 captures included) -> stale; another DeepMap version -> stale; a graph built at
     another SHA -> stale; the clone's graph rebuilt with another engine / engine version since -> stale; the same
     graph -> scored (every card scored) / captured (otherwise) — a stale `measured` flag over an unscored card is
     'captured'.
  C. resume_state_for (the driver's classifier): reads results/<run-id>/results.json, repos.yaml's pin, this
     checkout's version and the clone's .deepmap/graph_build.json; never raises.
  D. a capture RECORDS its graph: do_run stores each measured clone's graph manifest (graph_builds) and the DeepMap
     version (machine_spec.deepmap_version); a re-score keeps graph_builds; a merge unions them.
  E. deepmap_version() is .claude-plugin/plugin.json's version.
  F. (fix-all round 3, cov-bench[4] + [3]) the driver RUNS: the real run_g3_n4.sh under bash in the sandbox of
     tests/f4_breadth_resume_r283_gates.py (its Sandbox + stand-ins: clone_graph.py / run_benchmark.py record every
     call, the REAL run_benchmark module classifies; no claude, Neo4j, judge or spend) — fresh, stale, captured,
     scored, unfinished, an unreadable results.json, two repos, an unpinned name, and a failing capture. Section A only
     pattern-matched the script, so a swapped branch or broken tab / repo-list parsing passed. Round 3 also fixed what
     it runs into: the `none` branch now logs its reason; per-run files already under the run-id (an unfinished,
     killed or unreadable capture) are moved aside to <run-id>.unfinished-<utc> — their answer paths re-pointed there —
     instead of being overwritten in place; and a capture that exits non-zero no longer kills the driver under
     `set -e` before its clone's servers + Neo4j are stopped: they are stopped, then the driver stops (rc 1), nothing
     scored or merged. Section F is skipped (visibly) when PyYAML is missing: the driver reads repos.yaml with it.

Usage: python tests/o11_g3_driver_resume_r283_gates.py [--root <tree>]
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.dont_write_bytecode = True     # never drop __pycache__ into a --root snapshot

REPO = Path(__file__).resolve().parent.parent
PASSES: list[str] = []
FAILS: list[str] = []
PIN = "c" * 40
MANIFEST = {"built_at_sha": PIN, "built_at_utc": "2026-09-29T10:00:00Z", "extractor": "graphify",
            "graphify_version": "0.8.51", "nodes": 1234, "calls_edges": 999, "embedded": 1234, "dirty": False,
            "built_at_dirty": False, "provenance": "git", "verdict": "READY", "note": "provenance note"}


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSES if ok else FAILS).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok or not detail else f" — {detail}"))


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _code(text: str) -> str:
    return "\n".join(ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#"))


# ---------------------------------------------------------------------------------------------- A
def gate_driver(root: Path) -> None:
    print("\n-- A. run_g3_n4.sh: the G3 set, a version-tagged run, provenance-checked resumption, honest text --")
    text = (root / "benchmark" / "run_g3_n4.sh").read_text(encoding="utf-8")
    code = _code(text)
    result_path = root / "benchmark" / "results" / "G3_N4_RESULT.md"
    m = re.search(r'^G3_REPOS="([^"]*)"', code, re.MULTILINE)
    names = m.group(1).split() if m else []
    check("the driver names its default set explicitly (G3_REPOS=...)", bool(m), "no G3_REPOS assignment")
    if result_path.is_file():
        result_md = result_path.read_text(encoding="utf-8")
        scope_line = next((ln for ln in result_md.splitlines() if "**scope:**" in ln), "")
        g3_scope = re.findall(r"([\w.-]+/[\w.-]+) \(\d+(?: cards)?\)", scope_line)
        check("(premise) G3_N4_RESULT.md records the measured G3 scope (5 repos)", len(g3_scope) == 5, scope_line)
        check("...and the driver's set is exactly the measured G3 scope", sorted(names) == sorted(g3_scope),
              f"{names} vs {g3_scope}")
    else:
        print("  [skip] benchmark/results/G3_N4_RESULT.md is not in this checkout (the measured results stay private):"
              " the 'G3_REPOS is exactly the measured G3 scope' check needs it; the rest of section A still runs")
    try:
        import yaml  # noqa: WPS433
        repos = {r["name"]: r for r in yaml.safe_load((root / "benchmark" / "repos.yaml").read_text(
            encoding="utf-8"))["repos"]}
    except Exception as exc:  # noqa: BLE001
        repos = {}
        check("(repos.yaml readable with PyYAML)", False, f"{type(exc).__name__}: {exc}")
    bad = [n for n in names if not re.fullmatch(r"[0-9a-f]{40}", str((repos.get(n) or {}).get("pinned_sha", "")))]
    check("every G3 repo is in repos.yaml with a 40-hex pin", names and not bad, str(bad))
    breadth = sorted(n for n, r in repos.items() if str(r.get("stack", "")).lower() != "python")
    check("no multi-language breadth-wing repo is in the G3 set", breadth and not (set(breadth) & set(names)),
          f"breadth={breadth}")
    check("REPOS= overrides the default set (REPOS=\"${REPOS:-$G3_REPOS}\")",
          re.search(r'REPOS="\$\{REPOS:-\$G3_REPOS\}"', code) is not None, "")
    check("the repo list is no longer every repos.yaml entry",
          "join(r['name'] for r in yaml.safe_load" not in code, "the all-entries join is still there")
    check("RUN_TAG defaults to the DeepMap version (a new version never resumes an old one's run-ids)",
          re.search(r'RUN_TAG="\$\{RUN_TAG:-v\$', code) is not None and "deepmap_version" in code, "")
    check("per-repo run-ids carry the tag (g3n4-${RUN_TAG}-<repo>)", re.search(r'g3n4-\$\{RUN_TAG\}-\$san', code)
          is not None, "")
    check("the combined id carries the tag (g3n4-${RUN_TAG}-combined)",
          re.search(r'COMBINED_ID="g3n4-\$\{RUN_TAG\}-combined"', code) is not None, "")
    check("resumption is decided by run_benchmark.resume_state_for (recorded graph provenance)",
          "resume_state_for" in code, "")
    check("a `stale` state stops the driver (never reused, never silently re-captured)",
          re.search(r"^\s*stale\)(?:[^\n]*|\s*\n\s*)\bdie\b", code, re.MULTILINE) is not None, "")
    check("every --score reads the pinned clones (--clone-root)",
          all("--clone-root" in ln for ln in code.splitlines() if "run_benchmark.py --score" in ln)
          and "run_benchmark.py --score" in code, "")
    for stale in ("all 5 pinned repos", "all 5 repos", "CURATOR DRAFT"):
        check(f"the header / banner no longer says {stale!r}", stale not in text, "")
    check("the closing report reads the combined results.json and says when it is NOT measured",
          "$COMBINED_ID/results.json" in code and "NOT measured" in text, "")
    bash = shutil.which("bash")
    if bash:
        p = subprocess.run([bash, "-n", str(root / "benchmark" / "run_g3_n4.sh")], capture_output=True, text=True,
                           timeout=60)
        check("bash -n (syntax)", p.returncode == 0, p.stderr.strip()[:200])
    else:
        print("  [skip] bash not available for a syntax check")


# ---------------------------------------------------------------------------------------------- B
def _card(B, repo="o/r", scored=True):
    m = {k: None for k in B.METRIC_KEYS}
    if scored:
        m.update(completeness_pct=50.0, correctness_pct=90.0, wrong_high=0)
    return {"repo": repo, "question_id": "q1", "arm": B.ARM_ON, "run": 1, "exit_code": 0, "metrics": m,
            "scored": scored}


def _record(B, *, version="0.28.3", manifest=None, cards=None, measured=None):
    cards = [_card(B), _card(B)] if cards is None else cards
    rec = {"machine_spec": {"os": "t", "deepmap_version": version} if version else {"os": "t"},
           "graph_builds": {"o/r": dict(MANIFEST if manifest is None else manifest)} if manifest is not False else {},
           "scorecards": cards}
    rec["measured"] = (all(c.get("scored") for c in cards) and bool(cards)) if measured is None else measured
    return rec


def gate_resume_state(B) -> None:
    print("\n-- B. resume_state: reuse a capture ONLY when it measured this DeepMap version's graph --")
    fn = getattr(B, "resume_state", None)
    check("run_benchmark.resume_state exists", callable(fn))
    if not callable(fn):
        return

    def st(record, **kw):
        kw.setdefault("repo", "o/r")
        kw.setdefault("pinned_sha", PIN)
        kw.setdefault("current_version", "0.28.3")
        kw.setdefault("clone_manifest", dict(MANIFEST))
        return fn(record, **kw)

    check("no results.json -> none", st(None)[0] == "none", str(st(None)))
    check("a results.json with no captures (a capture that failed closed) -> none",
          st(_record(B, cards=[]))[0] == "none", str(st(_record(B, cards=[]))))
    legacy = {"machine_spec": {"os": "t", "deepmap_commit": "6e307e356666"}, "scorecards": [_card(B)], "measured": True}
    s, why = st(legacy)
    check("a July-style capture (no recorded graph provenance) -> stale, never reused", s == "stale", f"{s}: {why}")
    s, why = st(_record(B, version="0.27.4"))
    check("captured with another DeepMap version -> stale (the reason names both versions)",
          s == "stale" and "0.27.4" in why and "0.28.3" in why, f"{s}: {why}")
    s, why = st(_record(B), current_version=None)
    check("this checkout's version unknown -> stale (cannot prove the same graph)", s == "stale", f"{s}: {why}")
    s, why = st(_record(B, manifest=dict(MANIFEST, built_at_sha="d" * 40)))
    check("the measured graph was built at another SHA than the pin -> stale", s == "stale", f"{s}: {why}")
    for key, val in (("graphify_version", "0.8.39"), ("extractor", "ast"), ("built_at_sha", "e" * 40)):
        s, why = st(_record(B), clone_manifest=dict(MANIFEST, **{key: val}))
        check(f"the clone's graph changed since the capture ({key}) -> stale", s == "stale" and key in why,
              f"{s}: {why}")
    s, why = st(_record(B), clone_manifest=dict(MANIFEST, built_at_utc="2026-09-30T00:00:00Z", nodes=1240))
    check("a rebuild of the SAME graph identity (same SHA / engine / engine version) is still reusable",
          s == "scored", f"{s}: {why}")
    s, why = st(_record(B), clone_manifest=None)
    check("the clone is gone (no manifest to compare) -> decided on the recorded provenance alone", s == "scored",
          f"{s}: {why}")
    s, why = st(_record(B))
    check("same version, same graph, every card scored -> scored (reused)", s == "scored", f"{s}: {why}")
    s, why = st(_record(B, cards=[_card(B), _card(B, scored=False)]))
    check("same graph, an unscored card -> captured (score only)", s == "captured", f"{s}: {why}")
    s, why = st(_record(B, cards=[_card(B), _card(B, scored=False)], measured=True))
    check("a stale 'measured' flag over an unscored card -> captured (the cards decide)", s == "captured",
          f"{s}: {why}")
    s, why = st(_record(B, cards=[_card(B, repo="other/repo")]))
    check("a results.json holding ANOTHER repo's captures -> stale (never overwritten)", s == "stale", f"{s}: {why}")


# ---------------------------------------------------------------------------------------------- C
def gate_resume_state_for(root: Path) -> None:
    print("\n-- C. resume_state_for: the driver's classifier reads results.json, the pin, the version, the clone --")
    B = _load(root / "benchmark" / "run_benchmark.py", "o11_rb_resume_c")
    fn = getattr(B, "resume_state_for", None)
    check("run_benchmark.resume_state_for exists", callable(fn))
    if not callable(fn):
        return
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        B.RESULTS_ROOT = td / "results"
        B.load_config = lambda: ([{"name": "o/r", "url": "u", "pinned_sha": PIN, "license": "MIT"}], [])
        B.deepmap_version = lambda *a, **k: "0.28.3"
        clone = td / "clones" / "o__r"
        (clone / ".deepmap").mkdir(parents=True)
        (clone / ".deepmap" / "graph_build.json").write_text(json.dumps(MANIFEST), encoding="utf-8")
        check("no results dir -> none", fn("g3n4-v0.28.3-o__r", "o/r", str(clone))[0] == "none")
        rd = B.RESULTS_ROOT / "g3n4-v0.28.3-o__r"
        rd.mkdir(parents=True)
        (rd / "results.json").write_text(json.dumps(_record(B)), encoding="utf-8")
        s, why = fn("g3n4-v0.28.3-o__r", "o/r", str(clone))
        check("a capture of this version's graph, all scored -> scored", s == "scored", f"{s}: {why}")
        (clone / ".deepmap" / "graph_build.json").write_text(json.dumps(dict(MANIFEST, graphify_version="0.8.39")),
                                                             encoding="utf-8")
        s, why = fn("g3n4-v0.28.3-o__r", "o/r", str(clone))
        check("...the clone was rebuilt with another engine version since -> stale", s == "stale", f"{s}: {why}")
        (rd / "results.json").write_text("{ truncated", encoding="utf-8")
        s, why = fn("g3n4-v0.28.3-o__r", "o/r", str(clone))
        check("an unreadable results.json -> none (capture again), with a reason", s == "none" and why, f"{s}: {why}")
        B.load_config = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        (rd / "results.json").write_text(json.dumps(_record(B)), encoding="utf-8")
        try:
            s, why = fn("g3n4-v0.28.3-o__r", "o/r", str(clone))
            raised = None
        except Exception as exc:  # noqa: BLE001
            s, why, raised = None, None, exc
        check("never raises: a broken config -> an 'error' state the driver stops on", raised is None and s == "error",
              f"{s}: {why} {raised!r}")


# ---------------------------------------------------------------------------------------------- D
def gate_capture_records_graph(root: Path) -> None:
    print("\n-- D. a capture records WHICH graph it measured; re-score and merge keep it --")
    B = _load(root / "benchmark" / "run_benchmark.py", "o11_rb_resume_d")
    repos = [{"name": "o/clone", "url": "u", "pinned_sha": PIN, "license": "MIT"}]
    qs = [{"id": "q1", "repo": "o/clone", "prompt": "p", "grep_can_do": False, "family": "f",
           "answer_key": {"ground_truth_set": ["x"]}}]
    B.load_config = lambda: (repos, qs)
    B.validate_config = lambda r, q: []
    B._which = lambda name: "claude"
    B._tool_version = lambda name: None       # never spawn `claude --version` / git from a gate
    B._repo_head_sha = lambda: None
    B._server_files_present = lambda: True
    B._unreachable_mcp_servers = lambda: []
    B.write_on_mcp_config = lambda p: True
    B.assert_pinned_clean = lambda d, s: None
    B.assert_graph_at_pinned_sha = lambda d, s: None
    B.assert_on_arm_serves_repo = lambda d: None
    B.reset_pristine = lambda d, s: None
    B.run_one = lambda arm, q, repo_dir, cfg, run_idx, out_dir, **kw: {
        "repo": q["repo"], "question_id": q["id"], "arm": arm, "run": run_idx, "exit_code": 0,
        "metrics": {k: None for k in B.METRIC_KEYS}, "scored": False}
    saved = os.environ.get(B.ENGINE_ENV)
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        B.RESULTS_ROOT = td / "results"
        clone = td / "clones" / "o__clone"
        (clone / ".deepmap").mkdir(parents=True)
        (clone / ".deepmap" / "graph_build.json").write_text(json.dumps(MANIFEST), encoding="utf-8")
        try:
            os.environ[B.ENGINE_ENV] = "1"
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                rc = B.do_run(SimpleNamespace(repo=None, clone_root=str(td / "clones"), max_budget_usd=1.0,
                                              timeout_s=60, runs_per_arm=1), "prov")
        finally:
            if saved is None:
                os.environ.pop(B.ENGINE_ENV, None)
            else:
                os.environ[B.ENGINE_ENV] = saved
        rec = json.loads((B.RESULTS_ROOT / "prov" / "results.json").read_text(encoding="utf-8"))
        gb = (rec.get("graph_builds") or {}).get("o/clone") or {}
        check("results.json records the measured clone's graph manifest (graph_builds[repo])",
              rc == 0 and gb.get("built_at_sha") == PIN and gb.get("graphify_version") == "0.8.51"
              and gb.get("extractor") == "graphify" and gb.get("built_at_utc") == MANIFEST["built_at_utc"], str(gb))
        dv = getattr(B, "deepmap_version", lambda *a, **k: None)()
        check("machine_spec records the DeepMap version that ran the capture",
              dv and (rec.get("machine_spec") or {}).get("deepmap_version") == dv,
              f"{(rec.get('machine_spec') or {}).get('deepmap_version')!r} vs {dv!r}")
        out = B.score_captured_run(B.RESULTS_ROOT / "prov", repos, qs, lambda p: '{"correctness_pct": 1, '
                                   '"wrong_high": 0}', lambda r: "x", judge_model="j")
        check("a re-score keeps graph_builds", (out.get("graph_builds") or {}).get("o/clone", {}).get("built_at_sha")
              == PIN, str(out.get("graph_builds")))
        other = dict(rec, graph_builds={"o/other": dict(MANIFEST, built_at_sha="f" * 40)}, run_id="prov2")
        (B.RESULTS_ROOT / "prov2").mkdir()
        (B.RESULTS_ROOT / "prov2" / "results.json").write_text(json.dumps(other), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            B.do_merge(SimpleNamespace(merge="prov,prov2"), "merged")
        merged = json.loads((B.RESULTS_ROOT / "merged" / "results.json").read_text(encoding="utf-8"))
        check("a merge unions every run's graph_builds", set((merged.get("graph_builds") or {})) == {"o/clone", "o/other"},
              str(sorted(merged.get("graph_builds") or {})))


# ---------------------------------------------------------------------------------------------- E
def gate_version(root: Path, B) -> None:
    print("\n-- E. deepmap_version() is the checkout's plugin version --")
    fn = getattr(B, "deepmap_version", None)
    check("run_benchmark.deepmap_version exists", callable(fn))
    if not callable(fn):
        return
    want = json.loads((root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")).get("version")
    check("deepmap_version() == .claude-plugin/plugin.json version", fn() == want, f"{fn()!r} vs {want!r}")
    with tempfile.TemporaryDirectory() as td:
        check("a tree without a readable plugin.json -> None (never raises)", fn(Path(td)) is None, str(fn(Path(td))))


# ---------------------------------------------------------------------------------------------- F
def gate_driver_runs(root: Path) -> None:
    print("\n-- F. run_g3_n4.sh RUNS under bash (sandbox): every resume branch, the failing-capture path --")
    try:
        F4 = _load(REPO / "tests" / "f4_breadth_resume_r283_gates.py", "o11_f4_sandbox")
    except Exception as exc:  # noqa: BLE001
        check("the driver sandbox of tests/f4_breadth_resume_r283_gates.py loads", False, f"{type(exc).__name__}: {exc}")
        return
    bash = F4._bash()
    if not bash:
        check("a POSIX bash is available to run the driver (Git Bash on Windows)", False, "no bash found")
        return
    try:
        import yaml  # noqa: F401,WPS433 — the driver's pinned_repo_names reads repos.yaml with PyYAML
    except ImportError:
        print("  [skip] section F: PyYAML is not importable, and run_g3_n4.sh reads repos.yaml with it "
              "(pinned_repo_names) — install the benchmark's requirements to run it")
        return
    rb, opt, builds = F4._rb, F4._opt, F4._builds

    def rid(name: str = "o/alpha") -> str:
        return f"g3n4-{F4.TAG}-{name.replace('/', '__')}"

    def lines(out: str, *words: str) -> bool:
        return any(all(w in ln for w in words) for ln in out.splitlines())

    def idx(calls: list, prog: str, head: str) -> "list[int]":
        return [i for i, c in enumerate(calls) if c["prog"] == prog and head in c["argv"][:2]]

    with tempfile.TemporaryDirectory() as cache_td:
        cache = Path(cache_td)

        def box(td: str):
            return F4.Sandbox(root, Path(td), cache, driver="run_g3_n4.sh")

        with tempfile.TemporaryDirectory() as td:
            sb = box(td)
            rc, out, calls = sb.run(bash)
            caps, scores, merges = rb(calls, "--repo"), rb(calls, "--score"), rb(calls, "--merge")
            stop_i, cap_i = idx(calls, "clone_graph", "stop"), idx(calls, "run_benchmark", "--repo")
            check("fresh: rc 0 — the clone's OWN graph is built once and captured as g3n4-<RUN_TAG>-<owner__name> at "
                  "n=4 (no --runs-per-arm) with the 600 s cap; its servers are stopped after the capture",
                  rc == 0 and len(builds(calls)) == 1 and [opt(a, "--run-id") for a in caps] == [rid()]
                  and all("--runs-per-arm" not in a and opt(a, "--timeout-s") == "600" for a in caps)
                  and bool(stop_i) and bool(cap_i) and stop_i[0] > cap_i[0], f"rc={rc} calls={calls}\n{out[-600:]}")
            check("fresh: the log says why the repo is captured (CAPTURE o/alpha — nothing captured yet)",
                  lines(out, "CAPTURE o/alpha", "nothing captured yet"), out[-600:])
            check("fresh: blind-scored with --clone-root, merged into g3n4-<RUN_TAG>-combined; the banner says measured "
                  "and that a REPOS run is not the G3 set",
                  [opt(a, "--score") for a in scores] == [rid()] and all("--clone-root" in a for a in scores)
                  and [opt(a, "--run-id") for a in merges] == [f"g3n4-{F4.TAG}-combined"]
                  and "measured (every card blind-scored)" in out and "NOT the signed-off G3 set" in out, out[-600:])

        with tempfile.TemporaryDirectory() as td:
            sb = box(td)
            sb.put(rid(), F4._july("o/alpha"))
            rc, out, calls = sb.run(bash)
            check("stale: a July-style capture (no graph provenance) at the tagged run-id stops the driver — nothing "
                  "built, captured, scored or merged", rc != 0 and "cannot be reused" in out and not builds(calls)
                  and not rb(calls, "--repo") and not rb(calls, "--score") and not rb(calls, "--merge"),
                  f"rc={rc}\n{out[-600:]}")

        with tempfile.TemporaryDirectory() as td:
            sb = box(td)
            sb.put(rid(), F4._provenanced("o/alpha", scored=False))
            rc, out, calls = sb.run(bash)
            check("captured (this graph, a card unscored): blind score only — no build, no capture — then merged",
                  rc == 0 and not builds(calls) and not rb(calls, "--repo") and lines(out, "RESUME o/alpha")
                  and [opt(a, "--score") for a in rb(calls, "--score")] == [rid()] and rb(calls, "--merge"),
                  f"rc={rc}\n{out[-600:]}")

        with tempfile.TemporaryDirectory() as td:
            sb = box(td)
            sb.put(rid(), F4._provenanced("o/alpha", scored=True))
            rc, out, calls = sb.run(bash)
            check("scored (measured on this graph): skipped — no build, capture or score — merged, measured",
                  rc == 0 and not builds(calls) and not rb(calls, "--repo") and not rb(calls, "--score")
                  and lines(out, "SKIP o/alpha") and rb(calls, "--merge")
                  and "measured (every card blind-scored)" in out, f"rc={rc}\n{out[-600:]}")

        with tempfile.TemporaryDirectory() as td:
            sb = box(td)
            sb.put(rid(), F4._provenanced("o/alpha", scored=True, complete=False))
            rc, out, calls = sb.run(bash)
            arch, _paths = F4._archive_paths(sb, rid())
            check("unfinished (capture_complete=false): the log gives the reason (RECAPTURE … UNFINISHED), then it is "
                  "captured, scored and merged", rc == 0 and lines(out, "RECAPTURE o/alpha", "UNFINISHED")
                  and [opt(a, "--run-id") for a in rb(calls, "--repo")] == [rid()]
                  and [opt(a, "--score") for a in rb(calls, "--score")] == [rid()], f"rc={rc}\n{out[-600:]}")
            check("...its old files are moved aside to <run-id>.unfinished-<utc> (kept, never overwritten in place), "
                  "and the new capture's dir holds only its own per-run files",
                  len(arch) == 1 and (arch[0] / "results.json").is_file()
                  and not (sb.results / rid() / "o__x").exists(), f"archives={[p.name for p in arch]}")
            ok, detail = F4._repointed(sb, rid())
            check("...and the archive's answer paths name the archive, not the run-id the new capture took over",
                  ok, detail)

        with tempfile.TemporaryDirectory() as td:
            sb = box(td)
            sb.put(rid(), F4._provenanced("o/alpha", scored=True))
            (sb.results / rid() / "results.json").write_text('{"scorecards": [', encoding="utf-8")
            rc, out, calls = sb.run(bash)
            arch, _paths = F4._archive_paths(sb, rid())
            check("a complete capture whose results.json is unreadable (truncated): RECAPTURE with that reason, its "
                  "files moved aside (a WARN names the unreadable file), then captured, scored and merged",
                  rc == 0 and lines(out, "RECAPTURE o/alpha", "unreadable") and lines(out, "WARN", "unreadable")
                  and len(arch) == 1 and [opt(a, "--run-id") for a in rb(calls, "--repo")] == [rid()],
                  f"rc={rc} archives={[p.name for p in arch]}\n{out[-700:]}")

        with tempfile.TemporaryDirectory() as td:
            sb = box(td)
            sb.put(rid("o/alpha"), F4._provenanced("o/alpha", scored=True))
            rc, out, calls = sb.run(bash, repos=("o/alpha", "o/beta"))
            merges = rb(calls, "--merge")
            check("two repos (alpha measured, beta new): each is classified on its own (the repo list and the "
                  "tab-separated state/reason) — beta alone is built and captured; both are merged, in order",
                  rc == 0 and [opt(a, "--repo") for a in rb(calls, "--repo")] == ["o/beta"] and len(builds(calls)) == 1
                  and merges and opt(merges[0], "--merge") == f"{rid('o/alpha')},{rid('o/beta')}",
                  f"rc={rc} merges={merges}\n{out[-600:]}")

        with tempfile.TemporaryDirectory() as td:
            sb = box(td)
            rc, out, calls = sb.run(bash, repos=("o/alpha", "o/missing"))
            check("REPOS naming a repo repos.yaml does not pin: stop before anything is built or captured",
                  rc != 0 and "REPOS must name" in out and not calls, f"rc={rc}\n{out[-400:]}")

        with tempfile.TemporaryDirectory() as td:
            sb = box(td)
            rc, out, calls = sb.run(bash, F4_CAPTURE_RC=2)
            stop_i, cap_i = idx(calls, "clone_graph", "stop"), idx(calls, "run_benchmark", "--repo")
            check("a capture that exits non-zero: its clone's servers + Neo4j are still stopped (clone_graph.py stop "
                  "after the capture — `set -e` no longer kills the driver first)",
                  bool(cap_i) and bool(stop_i) and stop_i[0] > cap_i[0], f"calls={calls}\n{out[-600:]}")
            check("...then the driver stops (rc 1) naming the capture's exit code; nothing scored or merged",
                  rc == 1 and "exited 2" in out and not rb(calls, "--score") and not rb(calls, "--merge"),
                  f"rc={rc}\n{out[-600:]}")


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
    print(f"DeepMap — G3 driver: the G3 set, this version's graph, provenance-checked resume (root: {root})")
    print("=" * 72)
    B = _load(root / "benchmark" / "run_benchmark.py", "o11_rb_resume")
    gate_driver(root)
    gate_resume_state(B)
    gate_resume_state_for(root)
    gate_capture_records_graph(root)
    gate_version(root, B)
    gate_driver_runs(root)

    total = len(PASSES) + len(FAILS)
    print("\n" + "=" * 72)
    if FAILS:
        print(f"RESULT: RED — {len(PASSES)}/{total} gates passed.")
        return 1
    print(f"RESULT: GREEN — {len(PASSES)}/{total} gates passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
