#!/usr/bin/env python3
"""0.28.3 (F4, round 3) — the blind judge's separation-of-duties guard refuses a judge that is an arm's own model, or
that can run it through a `claude --model` selector.

What the guard covers (run_benchmark.judge_arm_clash) — and what these gates lock:
  * the spellings of ONE model that run_benchmark._norm_model_id folds together: a provider prefix, a variant suffix,
    a context-window tag, a dated snapshot, Vertex's '@<date>' and Bedrock's 'us.anthropic.' / '-v<n>:0' forms;
  * the `claude --model` aliases the installed CLIs define (read read-only from 2.1.129 on PATH and from the IDE
    extension's bundled 2.1.282-2.1.285): opus, sonnet, haiku, fable, best, opusplan, with or without a context tag;
    and 'default' and a blank model, which run the account's default model (Opus or Sonnet, by plan and provider).
    Each stands for the model families it can run, on the judge or the arm side ('best' runs Opus, or Fable on
    2.1.28x; 'opusplan' runs Sonnet, because no benchmark claude runs in plan mode);
  * the legacy Opus ids the CLI re-points at its current Opus ('claude-opus-4-1' ...): an arm's always (its capture
    env is unknown), the judge's on the cli transport unless CLAUDE_CODE_DISABLE_LEGACY_MODEL_REMAP is truthy to the
    CLI (1 / true / yes / on). Deliberately conservative: the CLI re-points them only on first-party-style providers.
Not covered (stated in judge_arm_clash's docstring): the anchor arm is compared as MODEL, the model G3 requires its
`--model opus` to resolve to, so another concrete Opus id is allowed over it; and anything that re-points a model
outside the `--model` value is not followed, such as an ANTHROPIC_DEFAULT_MODEL or ANTHROPIC_DEFAULT_<FAMILY>_MODEL
variable, an organization's default model or availableModels allowlist, or a provider-side alias.

Findings: round 2 (F4, the O11 review of ISS-benchmark:010) — 'opus[1m]', 'claude-opus-4-8[1m]',
'claude-opus-4-8-20260101', 'claude-opus-4-8@20260101', 'us.anthropic.claude-opus-4-8-v1:0', and 'sonnet[1m]' over a
claude-sonnet-* arm passed the guard. Round 3 (the completeness critic, cov-bench[1]) — the CLI's own 'best' alias
runs the Opus arm's model and passed; so did 'fable' over a Fable arm, 'opusplan' over a Sonnet arm, a legacy Opus id
on the cli transport, and a judge of the family an alias-named arm (`--claude-model sonnet`) runs. Round 3 review —
'default' and a blank --judge-model, which run the account's default model (Opus on some plans), passed; the arm side
read the remap opt-out from the scoring env, not the unknown capture env; an explicit transport= was not normalised.

Locks (hermetic: no claude, no judge call, no spend; the judge-transport / remap env is cleared for the run):
  A. the normalised spellings of the Opus arm are refused, and 'sonnet[1m]' over a claude-sonnet-5 arm.
  B. no over-reach: a different model (a Sonnet 4.6 judge over Opus + Sonnet 5 arms, haiku, a non-Claude judge, a
     'sonnet' judge on an Opus-only run) is not refused.
  C. do_score refuses `--judge-model opus[1m]` BEFORE any judge call (rc 2, the refusal names it).
  D. the CLI selectors: 'best' over the Opus anchor (and a Fable arm), 'fable' over a Fable arm, 'opusplan' over a
     Sonnet arm, 'default' (any spelling) and a blank model over the Opus anchor or a Sonnet arm are refused; 'fable' /
     'opusplan' over the Opus anchor, 'best' over a Sonnet arm and 'default' over a Haiku or Fable arm are not.
  E. a legacy Opus id is refused on the cli transport (the default, and transport='cli'), not on api / openrouter or
     with CLAUDE_CODE_DISABLE_LEGACY_MODEL_REMAP truthy (1 / true / yes / on, any case); a falsy or other value keeps
     it refused. An arm named by one is the CLI's current Opus on any transport, whatever the scoring env says.
  F. an arm named by an alias is the families it can run: a Sonnet judge over a 'sonnet' arm, 'sonnet' over an
     'opusplan' arm, a Fable judge over a 'best' arm, Sonnet and Opus judges over a 'default' arm are refused; haiku
     / Mythos judges are not.
  G. the cli judge's argv pins `--permission-mode default` (never plan mode, where 'opusplan' runs Opus and 'haiku'
     runs Sonnet).
  H. do_score refuses `--judge-model best`, `default`, a blank one and (cli) `claude-opus-4-1` before any judge call,
     and lets `claude-opus-4-1` through on the api transport. On a captured run with a `--claude-model sonnet` (or
     `default`) arm, it refuses `--judge-model claude-sonnet-4-6` before the config is loaded.
  I. an explicit transport= is read the way DEEPMAP_JUDGE_TRANSPORT is: 'or' is openrouter, 'anthropic' is api (any
     case), anything else is the cli.

Usage: python tests/f4_judge_spelling_r283_gates.py [--root <tree>]
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.dont_write_bytecode = True     # never drop __pycache__ into a --root snapshot

REPO = Path(__file__).resolve().parent.parent
PASSES: list[str] = []
FAILS: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSES if ok else FAILS).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok or not detail else f" — {detail}"))


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


TRANSPORT_ENV = "DEEPMAP_JUDGE_TRANSPORT"
REMAP_OFF_ENV = "CLAUDE_CODE_DISABLE_LEGACY_MODEL_REMAP"      # the claude CLI's own opt-out of its legacy-id remap


@contextlib.contextmanager
def _env(**values):
    """Set (a str) or unset (None) env vars for the block, restoring each one afterwards."""
    saved = {k: os.environ.get(k) for k in values}
    try:
        for k, v in values.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _clash_kw(B, judge: str, arms: list, transport: str):
    """judge_arm_clash with an explicit transport= (the round-3 signature); a TypeError on an older tree is reported."""
    try:
        return B.judge_arm_clash(judge, arms, transport=transport)
    except TypeError as exc:
        return f"TypeError: {exc}"


def _do_score(B, judge_model: str, transport=None) -> tuple:
    """do_score with `claude` off PATH: a judge the guard lets through stops at "not on PATH", never at a judge call."""
    B._which = lambda name: None
    err = io.StringIO()
    with _env(**{B.ENGINE_ENV: "1", TRANSPORT_ENV: transport, B.JUDGE_MODEL_ENV: None}):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            rc = B.do_score(SimpleNamespace(score="none", judge_model=judge_model, clone_root=None, max_budget_usd=1.0,
                                            timeout_s=60, score_workers=None), "none")
    return rc, err.getvalue()


def _do_score_captured(B, arm_model: str, judge_model: str) -> tuple:
    """do_score on a captured run whose extra arm is `--claude-model <arm_model>` (key deepmap_on@<arm_model>).
    load_config is a sentinel: a judge the guard lets through raises it before the config is read."""
    def _past_the_guard():
        raise RuntimeError("SENTINEL: do_score went past the separation-of-duties guard")

    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td) / "results" / "alias-arm"
        run_dir.mkdir(parents=True)
        (run_dir / "results.json").write_text(json.dumps({"run_id": "alias-arm", "scorecards": [
            {"repo": "o/r", "question_id": "q1", "arm": "deepmap_on"},
            {"repo": "o/r", "question_id": "q1", "arm": f"deepmap_on@{arm_model}"}]}), encoding="utf-8")
        saved_root, saved_which, saved_cfg = B.RESULTS_ROOT, B._which, B.load_config
        B.RESULTS_ROOT, B._which, B.load_config = Path(td) / "results", (lambda name: "claude"), _past_the_guard
        err = io.StringIO()
        try:
            with _env(**{B.ENGINE_ENV: "1", TRANSPORT_ENV: None, B.JUDGE_MODEL_ENV: None}):
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
                    rc = B.do_score(SimpleNamespace(score="alias-arm", judge_model=judge_model, clone_root=None,
                                                    max_budget_usd=1.0, timeout_s=60, score_workers=None), "alias-arm")
        except RuntimeError as exc:
            rc = None
            err.write(str(exc))
        finally:
            B.RESULTS_ROOT, B._which, B.load_config = saved_root, saved_which, saved_cfg
    return rc, err.getvalue()


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    root = REPO
    if "--root" in sys.argv:
        root = Path(sys.argv[sys.argv.index("--root") + 1]).resolve()
    with _env(**{TRANSPORT_ENV: None, REMAP_OFF_ENV: None}):   # the operator's judge transport and remap opt-out
        return _run(root)                                     # must not decide these results


def _run(root: Path) -> int:
    print("=" * 72)
    print(f"DeepMap — the blind judge guard refuses an arm's model and CLI selectors that can run it (root: {root})")
    print("=" * 72)
    B = _load(root / "benchmark" / "run_benchmark.py", "f4_rb_judge")
    clash = B.judge_arm_clash
    sonnet5 = [B.MODEL, "claude-sonnet-5"]
    fable = [B.MODEL, "claude-fable-5-1"]

    print("\n-- A. the normalised spellings of the arm's own model are refused --")
    for spelling in ("opus[1m]", "OPUS[1M]", "claude-opus-4-8[1m]", "claude-opus-4-8-20260101",
                     "claude-opus-4-8@20260101", "us.anthropic.claude-opus-4-8-v1:0",
                     "anthropic.claude-opus-4-8-20260101-v1:0", "anthropic/claude-opus-4.8[1m]"):
        check(f"judge {spelling!r} over the Opus arm -> refused", clash(spelling, [B.MODEL]) is not None,
              str(clash(spelling, [B.MODEL])))
    check("judge 'sonnet[1m]' over a claude-sonnet-5 arm -> refused (the alias may resolve to it)",
          clash("sonnet[1m]", sonnet5) is not None, str(clash("sonnet[1m]", sonnet5)))
    check("judge 'claude-sonnet-5-20260630' (a dated snapshot) over a claude-sonnet-5 arm -> refused",
          clash("claude-sonnet-5-20260630", sonnet5) is not None, "")
    for spelling in ("opus", "OPUS", "claude-opus-4-8", "anthropic/claude-opus-4.8",
                     "Anthropic/Claude-Opus-4.8:thinking"):
        check(f"(round 1, still) judge {spelling!r} over the Opus arm -> refused",
              clash(spelling, [B.MODEL]) is not None)
    check("(round 1, still) 'sonnet' over a claude-sonnet-5 arm -> refused", clash("sonnet", sonnet5) is not None)

    print("\n-- B. no over-reach: a DIFFERENT model is not refused --")
    for judge, arms in (("claude-sonnet-4-6", sonnet5), ("haiku", sonnet5), ("haiku[1m]", sonnet5),
                        ("deepseek/deepseek-chat", sonnet5), ("openai/gpt-4o", [B.MODEL, "openai/gpt-5"]),
                        ("sonnet", [B.MODEL]), ("claude-sonnet-5-1", sonnet5)):
        check(f"judge {judge!r} over {arms} -> allowed", clash(judge, arms) is None, str(clash(judge, arms)))

    print("\n-- C. do_score refuses `--judge-model opus[1m]` before any judge call --")
    saved = {k: os.environ.get(k) for k in (B.ENGINE_ENV, B.JUDGE_TRANSPORT_ENV, B.JUDGE_MODEL_ENV)}
    B._which = lambda name: None          # were the guard to let it through, do_score stops at "claude not on PATH"
    err = io.StringIO()
    try:
        os.environ[B.ENGINE_ENV] = "1"
        os.environ.pop(B.JUDGE_TRANSPORT_ENV, None)
        os.environ.pop(B.JUDGE_MODEL_ENV, None)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            rc = B.do_score(SimpleNamespace(score="none", judge_model="opus[1m]", clone_root=None, max_budget_usd=1.0,
                                            timeout_s=60, score_workers=None), "none")
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    text = err.getvalue()
    check("rc 2 with the separation-of-duties refusal naming 'opus[1m]' (not the later claude-on-PATH stop)",
          rc == 2 and "REFUSING" in text and "opus[1m]" in text and "not on PATH" not in text, text[-300:])

    print("\n-- D. the claude CLI's own aliases: refused where they can run an arm's model family --")
    for spelling in ("best", "BEST", "best[1m]", " Best "):
        check(f"judge {spelling!r} over the Opus anchor -> refused ('best' runs the CLI's Opus model)",
              clash(spelling, [B.MODEL]) is not None, str(clash(spelling, [B.MODEL])))
    check("judge 'best' over a claude-fable-5-1 arm -> refused (on 2.1.28x 'best' can run Fable)",
          clash("best", ["claude-fable-5-1"]) == "claude-fable-5-1", str(clash("best", ["claude-fable-5-1"])))
    for spelling in ("fable", "fable[1m]"):
        check(f"judge {spelling!r} over a claude-fable-5-1 arm -> refused (the alias runs the CLI's Fable model)",
              clash(spelling, fable) == "claude-fable-5-1", str(clash(spelling, fable)))
    for spelling in ("opusplan", "opusplan[1m]"):
        check(f"judge {spelling!r} over a claude-sonnet-5 arm -> refused (outside plan mode it runs Sonnet)",
              clash(spelling, sonnet5) == "claude-sonnet-5", str(clash(spelling, sonnet5)))
    for spelling in ("default", "DEFAULT", "default[1m]", " Default ", "", "  "):
        check(f"judge {spelling!r} over the Opus anchor -> refused ('default', or a blank model, runs the account's "
              f"default model: Opus on some plans and providers)",
              clash(spelling, [B.MODEL]) is not None, str(clash(spelling, [B.MODEL])))
    for spelling in ("default", ""):
        check(f"judge {spelling!r} over a claude-sonnet-5 arm -> refused (the default model is Sonnet elsewhere)",
              clash(spelling, ["claude-sonnet-5"]) == "claude-sonnet-5", str(clash(spelling, ["claude-sonnet-5"])))
    for judge, arms, why in (("fable", [B.MODEL], "Fable is not the Opus anchor"),
                             ("fable", sonnet5, "nor a Sonnet arm"),
                             ("opusplan", [B.MODEL], "the judge never runs in plan mode, so 'opusplan' runs Sonnet"),
                             ("best", ["claude-sonnet-5"], "'best' never runs Sonnet"),
                             ("default", ["claude-haiku-4-5"], "the default model is Opus or Sonnet, not Haiku"),
                             ("default", ["claude-fable-5-1"], "nor Fable")):
        check(f"judge {judge!r} over {arms} -> allowed ({why})", clash(judge, arms) is None, str(clash(judge, arms)))

    print("\n-- E. a legacy Opus id the CLI re-points at its current Opus, per transport --")
    legacy = ("claude-opus-4-1", "claude-opus-4-1-20250805", "claude-opus-4-0", "claude-opus-4-20250514",
              "Claude-Opus-4-1[1m]")
    for spelling in legacy:           # the default transport (DEEPMAP_JUDGE_TRANSPORT unset) is the cli
        check(f"cli (default transport): judge {spelling!r} over the Opus anchor -> refused",
              clash(spelling, [B.MODEL]) is not None, str(clash(spelling, [B.MODEL])))
    got = _clash_kw(B, "claude-opus-4-1", [B.MODEL], "cli")
    check("transport='cli' (do_score's explicit argument): judge 'claude-opus-4-1' -> refused",
          got is not None and not str(got).startswith("TypeError"), str(got))
    with _env(**{TRANSPORT_ENV: "api"}):
        got = clash("claude-opus-4-1", [B.MODEL])
    check("api transport: judge 'claude-opus-4-1' -> allowed (the API calls that older Opus itself)", got is None,
          str(got))
    got = _clash_kw(B, "claude-opus-4-1", [B.MODEL], "openrouter")
    check("transport='openrouter': judge 'claude-opus-4-1' -> allowed", got is None, str(got))
    with _env(**{REMAP_OFF_ENV: "1"}):
        got = clash("claude-opus-4-1", [B.MODEL])
    check(f"cli with {REMAP_OFF_ENV}=1: judge 'claude-opus-4-1' -> allowed (the CLI no longer re-points it)",
          got is None, str(got))
    for value in ("true", "yes", "on", " TRUE ", "On"):      # the CLI's truthy parse: trimmed, any case
        with _env(**{REMAP_OFF_ENV: value}):
            got = clash("claude-opus-4-1", [B.MODEL])
        check(f"cli with {REMAP_OFF_ENV}={value!r}: judge 'claude-opus-4-1' -> allowed (truthy to the CLI)",
              got is None, str(got))
    for value in ("0", "false", "no", "off", "", "2"):       # not truthy to the CLI: it still re-points the id
        with _env(**{REMAP_OFF_ENV: value}):
            got = clash("claude-opus-4-1", [B.MODEL])
        check(f"cli with {REMAP_OFF_ENV}={value!r}: judge 'claude-opus-4-1' -> refused (not truthy to the CLI)",
              got is not None, str(got))
    with _env(**{TRANSPORT_ENV: "api"}):
        got = clash("claude-opus-4-8", ["claude-opus-4-1"])
    check("an arm named 'claude-opus-4-1' (it ran through the CLI) is its current Opus, whatever the judge "
          "transport: an api judge 'claude-opus-4-8' over it -> refused", got == "claude-opus-4-1", str(got))
    with _env(**{REMAP_OFF_ENV: "1"}):
        got = _clash_kw(B, "claude-opus-4-8", ["claude-opus-4-1"], "api")
    check(f"...even with {REMAP_OFF_ENV}=1 at scoring time (the arm's capture env is unknown): an api judge "
          f"'claude-opus-4-8' over an arm named 'claude-opus-4-1' -> refused", got == "claude-opus-4-1", str(got))

    print("\n-- F. an arm named by an alias is every model of the families it can run --")
    for judge, arms, why in (("claude-sonnet-4-6", [B.MODEL, "sonnet"], "a 'sonnet' arm runs the CLI's Sonnet"),
                             ("sonnet", [B.MODEL, "opusplan"], "an 'opusplan' arm (acceptEdits) runs Sonnet"),
                             ("claude-fable-5-1", [B.MODEL, "best"], "a 'best' arm can run Fable"),
                             ("claude-sonnet-4-6", [B.MODEL, "default"], "a 'default' arm can run Sonnet"),
                             ("claude-opus-4-7", ["default"], "or Opus")):
        check(f"judge {judge!r} over {arms} -> refused ({why})", clash(judge, arms) == arms[-1],
              str(clash(judge, arms)))
    for judge, arms in (("haiku", [B.MODEL, "opusplan"]), ("claude-mythos-5", ["best"]),
                        ("claude-haiku-4-5", [B.MODEL, "sonnet"]), ("claude-haiku-4-5", [B.MODEL, "default"]),
                        ("haiku", [B.MODEL, "default"])):
        check(f"judge {judge!r} over {arms} -> allowed", clash(judge, arms) is None, str(clash(judge, arms)))

    print("\n-- G. the cli judge never runs in plan mode --")
    argv = [str(a) for a in B.build_judge_argv(max_budget_usd=0.5)]
    mode = argv[argv.index("--permission-mode") + 1] if "--permission-mode" in argv[:-1] else None
    check("build_judge_argv pins `--permission-mode default` (a settings defaultMode of plan would run 'opusplan' "
          "as Opus and 'haiku' as Sonnet)", mode == "default", " ".join(argv))

    print("\n-- H. do_score, end to end: refused before any judge call --")
    for judge_model in ("best", "claude-opus-4-1", "default", ""):
        rc, text = _do_score(B, judge_model)
        check(f"cli: `--judge-model {judge_model!r}` -> rc 2 with the separation-of-duties refusal naming it (not "
              f"the later claude-on-PATH stop)",
              rc == 2 and "REFUSING" in text and repr(judge_model) in text and "not on PATH" not in text, text[-300:])
    rc, text = _do_score(B, "claude-opus-4-1", transport="api")
    check("api: `--judge-model claude-opus-4-1` passes the guard (stops later at claude-not-on-PATH)",
          rc == 2 and "not on PATH" in text and "REFUSING" not in text, text[-300:])
    for arm_model in ("sonnet", "default"):
        rc, text = _do_score_captured(B, arm_model, "claude-sonnet-4-6")
        check(f"a run with a `--claude-model {arm_model}` arm: `--judge-model claude-sonnet-4-6` -> rc 2, refused "
              f"naming the {arm_model!r} arm, before the config is even loaded",
              rc == 2 and "REFUSING" in text and repr(arm_model) in text and "SENTINEL" not in text, text[-300:])

    print("\n-- I. an explicit transport= is read the way DEEPMAP_JUDGE_TRANSPORT is --")
    for spelling in ("OR", " OpenRouter ", "anthropic", "API"):
        got = _clash_kw(B, "claude-opus-4-1", [B.MODEL], spelling)
        check(f"transport={spelling!r} (not the cli): judge 'claude-opus-4-1' -> allowed", got is None, str(got))
    for spelling in ("bogus", "CLI", ""):
        got = _clash_kw(B, "claude-opus-4-1", [B.MODEL], spelling)
        check(f"transport={spelling!r} (the cli, as an unknown DEEPMAP_JUDGE_TRANSPORT is): judge 'claude-opus-4-1' "
              f"-> refused", got is not None and not str(got).startswith("TypeError"), str(got))
    for value, want in (("or", "openrouter"), ("Anthropic", "api"), (" API ", "api"), ("bogus", "cli"),
                        ("", "cli"), (None, "cli")):
        with _env(**{TRANSPORT_ENV: value}):
            got = B._judge_transport()
        check(f"_judge_transport() with {TRANSPORT_ENV}={value!r} -> {want!r} (unchanged)", got == want, str(got))

    total = len(PASSES) + len(FAILS)
    print("\n" + "=" * 72)
    if FAILS:
        print(f"RESULT: RED — {len(PASSES)}/{total} gates passed.")
        return 1
    print(f"RESULT: GREEN — {len(PASSES)}/{total} gates passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
