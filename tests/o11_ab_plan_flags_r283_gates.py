#!/usr/bin/env python3
"""0.28.3 (O11) — `--ab-weights` / `--enrich-ab` say what they are: a printed plan, never a capture.

Finding (DeepInit refresh 2026-09-29, validated) — ISS-benchmark:012 (D13): both flags were documented (argparse help,
benchmark/README.md §7.6, docs/RM1-ENABLE.md, and `deepmap tune --apply`'s refusal) as "capture the ON arm TWICE"
(equal vs learned weights / deterministic vs enriched summaries), but the code only PRINTED a plan and then fell
through to the default mode — with no mode flag, the ordinary ON/OFF matrix. An operator following the docs with
the engine flag set spent a plain matrix's plan quota and got no equal/learned pair, and nothing said so. The capture
is NOT implemented here on purpose (it spends; the procedure is operator-run): the flags are plan-only.

Locks (hermetic: do_run / do_selftest replaced by recorders; no claude, no spend):
  - the help text of both flags says PLAN ONLY / captures nothing and no longer promises a twin capture,
  - `--ab-weights` (and `--enrich-ab`) alone print the plan and RETURN 0 without running the ON/OFF matrix,
  - an explicit mode still runs after the plan (`--ab-weights --selftest` -> the selftest) and a plain run is
    unchanged (no plan flag -> do_run),
  - the printed plan names the REAL procedure, and every helper it names exists: weight_ab.ab_env /
    staged_learned_weights / deterministic_ab / build_g4_evidence / write_benchmark_evidence, and
    enrich_ab.enrich_ab / enrich_verdict / build_enrich_evidence / write_enrich_evidence.

Usage: python tests/o11_ab_plan_flags_r283_gates.py [--root <tree>]
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import sys
from pathlib import Path

sys.dont_write_bytecode = True     # never drop __pycache__ into a --root snapshot

REPO = Path(__file__).resolve().parent.parent
PASSES: list[str] = []
FAILS: list[str] = []
WAB_HELPERS = ("ab_env", "staged_learned_weights", "deterministic_ab", "build_g4_evidence", "write_benchmark_evidence")
EAB_HELPERS = ("enrich_ab", "enrich_verdict", "build_enrich_evidence", "write_enrich_evidence")


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSES if ok else FAILS).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok or not detail else f" — {detail}"))


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _main(root: Path, argv: list) -> "tuple[object, str, list]":
    """Run run_benchmark.main(argv) with do_run / do_selftest recorded (never executed) -> (rc, stdout, modes run)."""
    B = _load(root / "benchmark" / "run_benchmark.py", f"o11_rb_plan_{len(argv)}")
    ran: list = []
    B.do_run = lambda args, run_id: ran.append("do_run") or 0
    B.do_selftest = lambda run_id: ran.append("do_selftest") or 0
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        try:
            rc = B.main(argv)
        except SystemExit as exc:          # --help exits
            rc = exc.code
    return rc, out.getvalue(), ran


def _is_option_line(ln: str) -> bool:
    """An argparse option line starts with a '-' at a SHALLOW indent (2 spaces); a wrapped continuation line of a
    help text is indented deep (the help column), even when it happens to begin with '--merge,'."""
    return ln.lstrip().startswith("-") and (len(ln) - len(ln.lstrip())) <= 4


def _help_for(help_text: str, flag: str) -> str:
    """The help paragraph of one option (argparse wraps it over indented lines)."""
    lines = help_text.splitlines()
    start = next((i for i, ln in enumerate(lines) if _is_option_line(ln) and ln.strip().startswith(flag)), None)
    if start is None:
        return ""
    block = [lines[start]]
    for ln in lines[start + 1:]:
        if _is_option_line(ln) or not ln.strip():
            break
        block.append(ln)
    return " ".join(" ".join(block).split())


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
    print(f"DeepMap — --ab-weights / --enrich-ab are plan-only (root: {root})")
    print("=" * 72)

    print("\n-- the help says what the flags do --")
    _rc, help_text, _ran = _main(root, ["--help"])
    for flag in ("--ab-weights", "--enrich-ab"):
        h = _help_for(help_text, flag)
        check(f"{flag}: help says PLAN ONLY", "plan only" in h.lower(), h)
        check(f"{flag}: help says it captures nothing / runs no matrix",
              "captures nothing" in h.lower() and "matrix" in h.lower(), h)
        check(f"{flag}: help no longer promises a twin capture ('capture the ON arm TWICE')",
              "capture the on arm twice" not in h.lower(), h)

    print("\n-- the flags alone print the plan and stop: no ON/OFF matrix under an A/B flag --")
    WAB = _load(root / "benchmark" / "weight_ab.py", "o11_wab")
    EAB = _load(root / "benchmark" / "enrich_ab.py", "o11_eab")
    for flag, helpers, mod in (("--ab-weights", WAB_HELPERS, WAB), ("--enrich-ab", EAB_HELPERS, EAB)):
        rc, out, ran = _main(root, [flag, "--ab-slice", "multi_hop"] if flag == "--ab-weights" else [flag])
        check(f"{flag}: returns 0 WITHOUT running the ON/OFF matrix (do_run not called)", rc == 0 and not ran,
              f"rc={rc} ran={ran}")
        check(f"{flag}: the printed plan says PLAN ONLY and that nothing was captured",
              "plan only" in out.lower() and "nothing" in out.lower(), out[-600:])
        missing = [h for h in helpers if h not in out]
        check(f"{flag}: the plan names the real procedure ({', '.join(helpers)})", not missing, f"missing {missing}")
        phantom = [h for h in helpers if not callable(getattr(mod, h, None))]
        check(f"{flag}: every helper it names exists in {Path(mod.__file__).name} (no phantom pointer)", not phantom,
              str(phantom))

    rc, out, ran = _main(root, ["--ab-weights", "--selftest"])
    check("an explicit mode still runs after the plan (--ab-weights --selftest -> the selftest, no matrix)",
          ran == ["do_selftest"], str(ran))
    rc, out, ran = _main(root, ["--repo", "psf/requests"])
    check("no plan flag -> the ordinary capture run is unchanged (do_run)", ran == ["do_run"], str(ran))

    total = len(PASSES) + len(FAILS)
    print("\n" + "=" * 72)
    if FAILS:
        print(f"RESULT: RED — {len(PASSES)}/{total} gates passed.")
        return 1
    print(f"RESULT: GREEN — {len(PASSES)}/{total} gates passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
