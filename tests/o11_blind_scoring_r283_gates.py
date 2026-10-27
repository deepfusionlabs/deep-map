#!/usr/bin/env python3
"""0.28.3 (O11) — the blind Stage-2 judge reads the pinned code, is the model it records, and never leaves a stale verdict.

Findings (DeepInit refresh 2026-09-29, validated):
  * ISS-benchmark:006 (D16) — correctness / wrong_high are defined against "the code at the pinned SHA", but the drivers
    called `--score` without `--clone-root`, so the cli judge ran in the DeepMap checkout and could not read the pinned
    source; `--clone-root` itself was used as ONE cwd for every card (a clones root, not the card's clone); and a clone
    the last capture run had edited (the arms run acceptEdits) was read as if it were the pinned commit. The API
    transports send the prompt only — they cannot read code at all, and nothing said so.
  * ISS-benchmark:010 (D11) — under DEEPMAP_JUDGE_TRANSPORT=openrouter the judge called is DEEPMAP_JUDGE_MODEL, but the
    separation-of-duties guard and the recorded `_judge_model` used `--judge-model` (default 'sonnet'): a DeepSeek judge
    was recorded as 'sonnet', and an Opus judge (== the arm) was never refused.
  * ISS-benchmark:011 (D12) — a failed in-place re-score kept the PREVIOUS judge's metrics on the card (only `scored`
    flipped), and 'measured' needed only a non-None completeness per card — so the run still read 'measured' with a
    mix of two judges' verdicts, contradicting score_run's own "can NEVER read measured" contract.

Locks (hermetic: temp results dirs, temp git clones, subprocess / judge transports replaced by fakes; no claude, no
network, no spend):
  A. score_run: a re-score starts from a clean slate — every failure path leaves the card UNSCORED with no previous
     judge's metrics / model / reasoning; a success clears a stale error; _all_scored / status_tag need scored=True;
     an in-place re-score where one card's judge fails reads NOT measured.
  B. the effective judge model: openrouter records + guards DEEPMAP_JUDGE_MODEL (an Opus spelling, or a model that is
     an arm of the run, is refused before any judge call); api records the concrete id; cli keeps refusing 'opus';
     a 'sonnet' alias judging a Sonnet arm is refused.
  C. the cli judge reads each card's OWN pinned clone (<clone-root>/<owner__name>), refuses a dirty / wrong-HEAD
     clone before any judge call, warns when it has no clone, says the API transports cannot read code; both drivers
     pass --clone-root on every --score; a capture leaves each clone it ran pristine for the judge.

Usage: python tests/o11_blind_scoring_r283_gates.py [--root <tree>]
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
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
VERDICT = '{"correctness_pct": 90, "wrong_high": 0, "reasoning": "new judge"}'
JUDGE_ENV = ("DEEPMAP_REAL_ENGINE", "DEEPMAP_JUDGE_TRANSPORT", "DEEPMAP_JUDGE_MODEL", "OPENROUTER_API_KEY",
             "DEEPMAP_GRAPH_SHA")


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSES if ok else FAILS).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok or not detail else f" — {detail}"))


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@contextlib.contextmanager
def _env(**kv):
    """Set (a str) / unset (None) the judge-related env vars for one case, restoring every one afterwards."""
    saved = {k: os.environ.get(k) for k in JUDGE_ENV}
    try:
        for k in JUDGE_ENV:
            os.environ.pop(k, None)
        for k, v in kv.items():
            if v is not None:
                os.environ[k] = v
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ---------------------------------------------------------------------------------------------- fixtures
def _git_env() -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def _git(*args, cwd: Path, hooks: Path) -> str:
    p = subprocess.run(["git", "-c", f"core.hooksPath={hooks}", "-c", "user.name=o11", "-c", "user.email=o11@x.invalid",
                        "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false", *args],
                       cwd=str(cwd), capture_output=True, text=True, timeout=60, env=_git_env())
    if p.returncode != 0:
        raise RuntimeError(f"git {args}: {p.stderr[-200:]}")
    return p.stdout.strip()


def _make_clone(path: Path, hooks: Path) -> str:
    """A throwaway git checkout (one tracked file) — returns its HEAD sha."""
    path.mkdir(parents=True)
    _git("init", "-q", cwd=path, hooks=hooks)
    (path / "billing.py").write_text("def charge():\n    return 1\n", encoding="utf-8")
    _git("add", "billing.py", cwd=path, hooks=hooks)
    _git("commit", "-q", "-m", "init", cwd=path, hooks=hooks)
    return _git("rev-parse", "HEAD", cwd=path, hooks=hooks)


def _write_run(B, rid: str, cards_spec) -> None:
    """A CAPTURED results.json under B.RESULTS_ROOT/<rid>: cards_spec = [(repo, qid, arm, answer, prior)], prior =
    None (never scored) or a dict of a PREVIOUS judge's verdict already on the card."""
    run_dir = B.RESULTS_ROOT / rid
    cards = []
    for repo, qid, arm, answer, prior in cards_spec:
        d = run_dir / repo.replace("/", "__") / qid / arm.replace("/", "__").replace("@", "_") / "run-1"
        d.mkdir(parents=True, exist_ok=True)
        (d / "stdout.json").write_text(json.dumps({"result": answer, "total_cost_usd": 0.1, "num_turns": 2}),
                                       encoding="utf-8")
        metrics = {k: None for k in B.METRIC_KEYS}
        card = {"repo": repo, "question_id": qid, "family": "f", "grep_can_do": False, "arm": arm, "run": 1,
                "exit_code": 0, "started_at": "t", "metrics": metrics, "scored": False,
                "answer_text_path": str((d / "stdout.json").relative_to(B.RESULTS_ROOT.parent))}
        if prior:
            metrics.update(completeness_pct=100.0, correctness_pct=prior.get("correctness", 99.0), wrong_high=0)
            card.update(scored=True, _judge_model=prior.get("model", "old-judge"), _judge_reasoning="old reasoning")
        cards.append(card)
    rec = {"schema": B.SCHEMA, "run_id": rid, "machine_spec": {"os": "t", "python": "3"}, "runs_per_arm": 1,
           "scorecards": cards, "measured": all(c["scored"] for c in cards), "captured": True}
    (run_dir / "results.json").write_text(json.dumps(rec), encoding="utf-8")


def _question(qid: str, repo: str) -> dict:
    return {"id": qid, "repo": repo, "prompt": f"What calls charge() in {repo}?", "family": "f", "grep_can_do": False,
            "answer_key": {"ground_truth_set": ["A.x", "B.y"], "ground_truth_answer": "A.x and B.y"}}


class _Sub:
    """run_benchmark's `subprocess` stand-in: `git` runs for real (the clone checks are real git), every other argv
    is a claude child — recorded, answered with an envelope carrying `verdict`."""

    def __init__(self, verdict: str = VERDICT):
        self.claude: list = []
        self.verdict = verdict
        self.TimeoutExpired = subprocess.TimeoutExpired

    def run(self, argv, **kw):
        exe = Path(str(argv[0])).name.lower()
        if exe in ("git", "git.exe"):
            return subprocess.run(argv, **kw)
        self.claude.append((list(argv), dict(kw)))
        env = {"result": self.verdict, "total_cost_usd": 0.0, "num_turns": 1, "is_error": False}
        return SimpleNamespace(stdout=json.dumps(env), stderr="", returncode=0)


def _prep(B, td: Path, repos, questions, sub=None):
    B.RESULTS_ROOT = td / "results"
    B.RESULTS_ROOT.mkdir(parents=True, exist_ok=True)
    B.load_config = lambda: (repos, questions)
    git = shutil.which("git")
    B._which = lambda name: (git if name == "git" else "claude")
    B.subprocess = sub or _Sub()
    return B.subprocess


def _score(B, rid: str, *, judge_model="sonnet", clone_root=None) -> "tuple[int, str]":
    args = SimpleNamespace(score=rid, clone_root=None if clone_root is None else str(clone_root),
                           judge_model=judge_model, max_budget_usd=1.0, timeout_s=60, score_workers=None)
    err = io.StringIO()
    with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
        rc = B.do_score(args, "t")
    return rc, err.getvalue()


def _cards(B, rid: str) -> list:
    return json.loads((B.RESULTS_ROOT / rid / "results.json").read_text(encoding="utf-8"))["scorecards"]


# ---------------------------------------------------------------------------------------------- A
def gate_rescore_reset(root: Path) -> None:
    print("\n-- A. ISS-benchmark:011: a re-score starts from a clean slate; 'measured' needs every card scored --")
    B = _load(root / "benchmark" / "run_benchmark.py", "o11_rb_a")
    SR = _load(root / "benchmark" / "score_run.py", "o11_sr_a")
    q = _question("q1", "o/r")

    def prior_card():
        m = {k: None for k in B.METRIC_KEYS}
        m.update(completeness_pct=100.0, correctness_pct=95.0, wrong_high=1)
        return {"repo": "o/r", "question_id": "q1", "arm": B.ARM_ON, "run": 1, "exit_code": 0, "metrics": m,
                "scored": True, "_judge_model": "old-judge", "_judge_reasoning": "old reasoning",
                "_provisional_completeness": {"completeness_pct": 100.0, "stale": True}, "answer_text_path": "p"}

    def boom(prompt):
        raise OSError("judge unreachable")

    cases = (("the judge raises on every attempt", {"q1": q}, lambda r: "A.x only", boom),
             ("the judge returns no verdict (garbage)", {"q1": q}, lambda r: "A.x only", lambda p: "I refuse"),
             ("the question/oracle is gone", {}, lambda r: "A.x only", lambda p: VERDICT),
             ("the captured answer is empty", {"q1": q}, lambda r: "", lambda p: VERDICT))
    for label, qs, reader, judge in cases:
        rec = prior_card()
        SR.score_records([rec], qs, judge_call_fn=judge, read_answer_fn=reader, pinned_sha_by_repo={"o/r": "a" * 40},
                         judge_model="new-judge")
        m = rec["metrics"]
        stale = [k for k in ("completeness_pct", "correctness_pct", "wrong_high") if m.get(k) is not None]
        check(f"{label}: the card is UNSCORED with none of the previous judge's metrics", rec["scored"] is False
              and not stale, f"scored={rec['scored']} stale metrics={stale}")
        check(f"{label}: no previous judge model / reasoning survives",
              "_judge_model" not in rec and "_judge_reasoning" not in rec,
              f"{rec.get('_judge_model')!r} {rec.get('_judge_reasoning')!r}")
        check(f"{label}: a _score_error says why", bool(rec.get("_score_error")), str(rec.get("_score_error")))
        prov = rec.get("_provisional_completeness")
        check(f"{label}: no stale provisional completeness (only this pass's own, if it got that far)",
              prov is None or "stale" not in prov, str(prov))

    rec = prior_card()
    rec.update(scored=False, _score_error="an earlier failure")
    SR.score_records([rec], {"q1": q}, judge_call_fn=lambda p: VERDICT, read_answer_fn=lambda r: "A.x and B.y",
                     pinned_sha_by_repo={"o/r": "a" * 40}, judge_model="new-judge")
    check("a successful re-score records the NEW judge and clears the stale _score_error",
          rec["scored"] is True and rec.get("_judge_model") == "new-judge" and "_score_error" not in rec,
          f"{rec.get('_judge_model')!r} {rec.get('_score_error')!r}")

    stale_card = prior_card()
    stale_card["scored"] = False          # a failed re-score on a tree that still carries the old metrics
    fresh = prior_card()
    check("_all_scored: a card with metrics but scored=False keeps the run out of 'measured'",
          B._all_scored([fresh, stale_card]) is False and B.status_tag([fresh, stale_card]) == B.TAG_CAPTURED,
          B.status_tag([fresh, stale_card]))
    check("_all_scored: every card scored=True with metrics -> measured (unchanged)",
          B._all_scored([fresh, prior_card()]) is True)

    with tempfile.TemporaryDirectory() as td:
        B.RESULTS_ROOT = Path(td) / "results"
        B.RESULTS_ROOT.mkdir()
        rid = "rescore"
        _write_run(B, rid, [("o/r", "q1", B.ARM_ON, "ANSWER-ON A.x and B.y", {"model": "old-judge"}),
                            ("o/r", "q1", B.ARM_OFF, "ANSWER-OFF A.x", {"model": "old-judge"})])

        def flaky(prompt):
            if "ANSWER-OFF" in prompt:
                raise OSError("judge unreachable for this card")
            return VERDICT

        out = B.score_captured_run(B.RESULTS_ROOT / rid, [{"name": "o/r", "url": "u", "pinned_sha": "a" * 40,
                                                           "license": "MIT"}], [q], flaky,
                                   lambda r: B.read_answer_from_scorecard(r), judge_model="new-judge")
        off = next(c for c in out["scorecards"] if c["arm"] == B.ARM_OFF)
        check("an in-place re-score of a MEASURED run where one card's judge fails -> NOT measured",
              out["measured"] is False and out["status_tag"] == B.TAG_CAPTURED, out["status_tag"])
        check("...that card carries no metric of the previous judge", off["scored"] is False
              and off["metrics"].get("correctness_pct") is None and off.get("_judge_model") is None,
              f"{off['metrics']} {off.get('_judge_model')!r}")


# ---------------------------------------------------------------------------------------------- B
def gate_effective_judge(root: Path) -> None:
    print("\n-- B. ISS-benchmark:010: the guard and the record follow the model the judge ACTUALLY calls --")
    repos = [{"name": "o/r", "url": "u", "pinned_sha": "a" * 40, "license": "MIT"}]
    questions = [_question("q1", "o/r")]

    def run_case(transport, env_model, judge_model, arms=None):
        """-> (rc, stderr, cards, models the fake transports were built for, judge calls made)"""
        B = _load(root / "benchmark" / "run_benchmark.py", "o11_rb_b")
        built: list = []
        calls: list = []

        def fake_call(prompt):
            calls.append(prompt)
            return VERDICT

        def fake_or(**kw):
            built.append(("openrouter", kw.get("model")))
            return fake_call

        def fake_api(**kw):
            built.append(("api", kw.get("model_alias")))
            return fake_call

        B._openrouter_judge_call_factory = fake_or
        B._api_judge_call_factory = fake_api
        with tempfile.TemporaryDirectory() as td:
            sub = _prep(B, Path(td), repos, questions)
            _write_run(B, "r", [("o/r", "q1", a, "A.x and B.y", None) for a in (arms or [B.ARM_ON, B.ARM_OFF])])
            with _env(DEEPMAP_REAL_ENGINE="1", DEEPMAP_JUDGE_TRANSPORT=transport, DEEPMAP_JUDGE_MODEL=env_model):
                rc, err = _score(B, "r", judge_model=judge_model)
            cards = _cards(B, "r")
        return rc, err, cards, built, calls + [c for c in sub.claude]

    rc, err, cards, built, calls = run_case("openrouter", "deepseek/deepseek-chat", "sonnet")
    check("openrouter: the judge is built for DEEPMAP_JUDGE_MODEL", ("openrouter", "deepseek/deepseek-chat") in built,
          str(built))
    check("openrouter: every scored card RECORDS deepseek/deepseek-chat (not the unused --judge-model 'sonnet')",
          rc == 0 and cards and all(c.get("scored") and c.get("_judge_model") == "deepseek/deepseek-chat"
                                    for c in cards), f"rc={rc} {[c.get('_judge_model') for c in cards]}")

    for env_model in ("anthropic/claude-opus-4.8", "claude-opus-4-8", "Anthropic/Claude-Opus-4.8:thinking"):
        rc, err, cards, built, calls = run_case("openrouter", env_model, "sonnet")
        check(f"openrouter judge {env_model!r} (== the Opus arm) -> refused (rc 2) before any judge call",
              rc == 2 and not calls and not any(c.get("scored") for c in cards), f"rc={rc} calls={len(calls)}")

    B0 = _load(root / "benchmark" / "run_benchmark.py", "o11_rb_b0")
    arms = [B0.ARM_ON + "@openai/gpt-5", B0.ARM_OFF + "@openai/gpt-5"]
    rc, err, cards, built, calls = run_case("openrouter", "openai/gpt-5", "sonnet", arms=arms)
    check("openrouter judge == an arm model of THIS run (openai/gpt-5) -> refused before any judge call",
          rc == 2 and not calls, f"rc={rc} calls={len(calls)}")
    rc, err, cards, built, calls = run_case("openrouter", "deepseek/deepseek-chat", "sonnet", arms=arms)
    check("...a different openrouter judge for that run scores normally", rc == 0 and all(c.get("scored") for c in cards),
          f"rc={rc}")

    rc, err, cards, built, calls = run_case("api", None, "sonnet")
    check("api: the judge model recorded is the concrete id the API is called with (claude-sonnet-4-6)",
          rc == 0 and cards and all(c.get("_judge_model") == "claude-sonnet-4-6" for c in cards),
          f"rc={rc} {[c.get('_judge_model') for c in cards]}")

    rc, err, cards, built, calls = run_case(None, None, "opus")
    check("cli: --judge-model opus is still refused before any judge call", rc == 2 and not calls, f"rc={rc}")
    rc, err, cards, built, calls = run_case(None, None, "sonnet", arms=[B0.ARM_ON + "@claude-sonnet-5",
                                                                        B0.ARM_OFF + "@claude-sonnet-5"])
    check("cli: the 'sonnet' ALIAS judging a claude-sonnet-5 arm (it may resolve to that very model) -> refused",
          rc == 2 and not calls, f"rc={rc} calls={len(calls)}")
    rc, err, cards, built, calls = run_case(None, None, "sonnet")
    check("cli: --judge-model sonnet on the Opus anchor run scores normally and records 'sonnet'",
          rc == 0 and cards and all(c.get("_judge_model") == "sonnet" for c in cards),
          f"rc={rc} {[c.get('_judge_model') for c in cards]}")


# ---------------------------------------------------------------------------------------------- C
def gate_judge_reads_pinned_clone(root: Path) -> None:
    print("\n-- C. ISS-benchmark:006: the cli judge reads each card's OWN pinned clone — or says it cannot --")
    if not shutil.which("git"):
        check("(git on PATH for the pinned-clone checks)", True, "skipped — git not installed")
        return
    with tempfile.TemporaryDirectory() as tdn:
        td = Path(tdn)
        hooks = td / "no-hooks"
        hooks.mkdir()
        clones = td / "clones"
        sha1 = _make_clone(clones / "o__r1", hooks)
        sha2 = _make_clone(clones / "o__r2", hooks)
        repos = [{"name": "o/r1", "url": "u", "pinned_sha": sha1, "license": "MIT"},
                 {"name": "o/r2", "url": "u", "pinned_sha": sha2, "license": "MIT"}]
        questions = [_question("q1", "o/r1"), _question("q2", "o/r2")]
        spec = [("o/r1", "q1", "deepmap_on", "A.x and B.y", None), ("o/r2", "q2", "deepmap_on", "A.x", None)]

        B = _load(root / "benchmark" / "run_benchmark.py", "o11_rb_c1")
        sub = _prep(B, td / "w1", repos, questions)
        _write_run(B, "two", spec)
        with _env(DEEPMAP_REAL_ENGINE="1"):
            rc, err = _score(B, "two", clone_root=clones)
        cwd = {}
        for argv, kw in sub.claude:
            for repo in ("o/r1", "o/r2"):
                if repo in (kw.get("input") or ""):
                    cwd[repo] = Path(str(kw.get("cwd"))).resolve() if kw.get("cwd") else None
        check("--clone-root <clones root>: each card's judge runs in ITS clone (<root>/<owner__name>)",
              rc == 0 and cwd.get("o/r1") == (clones / "o__r1").resolve()
              and cwd.get("o/r2") == (clones / "o__r2").resolve(), f"rc={rc} cwd={cwd} err={err[-300:]}")

        B = _load(root / "benchmark" / "run_benchmark.py", "o11_rb_c2")
        sub = _prep(B, td / "w2", repos, questions)
        _write_run(B, "one", spec[:1])
        with _env(DEEPMAP_REAL_ENGINE="1"):
            rc, err = _score(B, "one", clone_root=clones / "o__r1")
        got = [Path(str(kw.get("cwd"))).resolve() for _a, kw in sub.claude if kw.get("cwd")]
        check("--clone-root <the clone itself> (a single-repo run) still runs the judge in that clone",
              rc == 0 and got and all(g == (clones / "o__r1").resolve() for g in got), f"rc={rc} {got}")

        (clones / "o__r2" / "billing.py").write_text("def charge():\n    return 2   # an arm's acceptEdits edit\n",
                                                    encoding="utf-8")
        B = _load(root / "benchmark" / "run_benchmark.py", "o11_rb_c3")
        sub = _prep(B, td / "w3", repos, questions)
        _write_run(B, "dirty", spec)
        with _env(DEEPMAP_REAL_ENGINE="1"):
            rc, err = _score(B, "dirty", clone_root=clones)
        check("a clone with an uncommitted TRACKED edit -> refused (rc 2) before ANY judge call",
              rc == 2 and not sub.claude, f"rc={rc} judge calls={len(sub.claude)}")
        check("...and the refusal names the clone and how to reset it", "o__r2" in err and "reset" in err.lower(),
              err[-400:])
        (clones / "o__r2" / "billing.py").write_text("def charge():\n    return 1\n", encoding="utf-8")

        wrong = [dict(repos[0]), dict(repos[1], pinned_sha="b" * 40)]
        B = _load(root / "benchmark" / "run_benchmark.py", "o11_rb_c4")
        sub = _prep(B, td / "w4", wrong, questions)
        _write_run(B, "wronghead", spec)
        with _env(DEEPMAP_REAL_ENGINE="1"):
            rc, err = _score(B, "wronghead", clone_root=clones)
        check("a clone whose HEAD is not the pinned SHA -> refused before any judge call", rc == 2 and not sub.claude,
              f"rc={rc} judge calls={len(sub.claude)}")

        B = _load(root / "benchmark" / "run_benchmark.py", "o11_rb_c5")
        sub = _prep(B, td / "w5", repos, questions)
        _write_run(B, "noroot", spec)
        with _env(DEEPMAP_REAL_ENGINE="1"):
            rc, err = _score(B, "noroot")
        check("cli judge with NO --clone-root -> a warning that it cannot read the pinned clones",
              rc == 0 and "--clone-root" in err and "warning" in err.lower(), err[-300:])

        B = _load(root / "benchmark" / "run_benchmark.py", "o11_rb_c6")
        B._openrouter_judge_call_factory = lambda **kw: (lambda prompt: VERDICT)
        sub = _prep(B, td / "w6", repos, questions)
        _write_run(B, "api", spec)
        with _env(DEEPMAP_REAL_ENGINE="1", DEEPMAP_JUDGE_TRANSPORT="openrouter",
                  DEEPMAP_JUDGE_MODEL="deepseek/deepseek-chat"):
            rc, err = _score(B, "api", clone_root=clones)
        check("openrouter judge: says it cannot read the pinned source (prompt only), scores against the reference",
              rc == 0 and "cannot read" in err.lower() and "pinned" in err.lower(), err[-300:])

    for rel in ("benchmark/run_g3_n4.sh", "benchmark/run_breadth.sh"):
        text = (root / rel).read_text(encoding="utf-8")
        code = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
        score = [ln for ln in code if "run_benchmark.py --score" in ln]
        check(f"{rel}: every --score call passes --clone-root (the judge's code)", bool(score)
              and all("--clone-root" in ln for ln in score), "\n".join(score))

    B = _load(root / "benchmark" / "run_benchmark.py", "o11_rb_c7")
    timeline: list = []
    repos1 = [{"name": "o/clone", "url": "u", "pinned_sha": "a" * 40, "license": "MIT"}]
    B.load_config = lambda: (repos1, [_question("q1", "o/clone")])
    B.validate_config = lambda r, q: []
    B._which = lambda name: "claude"
    B.machine_spec = lambda: {"os": "t", "python": "3"}   # never spawn a `claude --version` / git from a gate
    B._server_files_present = lambda: True
    B._unreachable_mcp_servers = lambda: []
    B.write_on_mcp_config = lambda p: True
    B.assert_pinned_clean = lambda d, s: None
    B.assert_graph_at_pinned_sha = lambda d, s: None
    B.assert_on_arm_serves_repo = lambda d: None
    B.reset_pristine = lambda d, s: timeline.append(("reset", Path(d).name)) or None

    def fake_run_one(arm, q, repo_dir, cfg, run_idx, out_dir, **kw):
        timeline.append(("run", arm))
        return {"repo": q["repo"], "question_id": q["id"], "arm": arm, "run": run_idx, "exit_code": 0,
                "metrics": {k: None for k in B.METRIC_KEYS}, "scored": False}

    B.run_one = fake_run_one
    with tempfile.TemporaryDirectory() as td:
        B.RESULTS_ROOT = Path(td) / "results"
        (Path(td) / "clones" / "o__clone").mkdir(parents=True)
        with _env(DEEPMAP_REAL_ENGINE="1"), contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            rc = B.do_run(SimpleNamespace(repo=None, clone_root=str(Path(td) / "clones"), max_budget_usd=1.0,
                                          timeout_s=60, runs_per_arm=1), "final-reset")
    check("a capture leaves the clone it ran pristine for the judge (its last step is a pristine reset)",
          rc == 0 and timeline and timeline[-1] == ("reset", "o__clone")
          and sum(1 for e in timeline if e[0] == "run") == 2, str(timeline))


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
    print(f"DeepMap — benchmark blind scoring: pinned-code judge, effective judge model, no stale verdicts (root: {root})")
    print("=" * 72)
    gate_rescore_reset(root)
    gate_effective_judge(root)
    gate_judge_reads_pinned_clone(root)

    total = len(PASSES) + len(FAILS)
    print("\n" + "=" * 72)
    if FAILS:
        print(f"RESULT: RED — {len(PASSES)}/{total} gates passed.")
        return 1
    print(f"RESULT: GREEN — {len(PASSES)}/{total} gates passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
