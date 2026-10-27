#!/usr/bin/env python3
"""0.28.3 (F4) — every A/B / experiment flag of run_benchmark.py is plan-only, and the G4 plan names the REAL API.

Findings (fix-all round 2, F4 — O11's round-1 residual, O10's routed request, the O11 review):
  1. `--ab-rerank` / `--ab-graph-signal` printed their offline-experiment plan and then FELL THROUGH to do_run: with the
     engine flag set, a plain ON/OFF matrix nobody asked for ran (plan quota spent), although their own help said "no
     capture/spend". `--ab-weights` / `--enrich-ab` were made plan-only in round 1 (ISS-benchmark:012); these two now
     behave the same, and the re-rank plan no longer implies `--ab-weights` performs a twin capture.
  2. The `--ab-weights` plan did not name the API benchmark/weight_ab.py actually has (O10, round 1): it printed
     `write_benchmark_evidence(<tuned root>)` (the real call needs the evidence too), `ab_env('equal')` (without a base
     env that is the flag and NOTHING else — no PATH, no Neo4j settings), `weights_fingerprint=<the stage token's>`,
     and never named stage_learned_weights / restore_learned_weights (a lost token's recovery) or the procedure text
     `deepmap tune --apply` prints (tune_weights._G4_PROCEDURE).

Locks (hermetic: do_run / do_selftest / do_merge replaced by recorders; no claude, no Neo4j, no spend):
  A. `--ab-rerank` / `--ab-graph-signal`: help says PLAN ONLY (captures nothing, no matrix); alone they print the plan
     and return 0 WITHOUT do_run; an explicit mode still runs; the [plan] line names the flag(s); the re-rank plan
     says --ab-weights PRINTS the G4 procedure; every API the two plans name exists (no phantom pointer).
  B. `--ab-weights`: every weight_ab call the plan prints is a real function and BINDS to its real signature;
     ab_env always gets a launch env; build_g4_evidence binds weights_fingerprint=token['fingerprint'] from the
     staged_learned_weights(...) token; stage_learned_weights and restore_learned_weights (the lost-token recovery)
     are named; `deepmap tune` opens it and `deepmap tune --apply` closes it; the steps follow the order of
     tune_weights._G4_PROCEDURE and the plan prints that text verbatim; --ab-slice reaches build_g4_evidence; the help
     names build_g4_evidence(weights_fingerprint=...); with weight_ab unimportable the plan still prints every step.

Usage: python tests/f4_ab_plan_api_r283_gates.py [--root <tree>]
"""
from __future__ import annotations

import ast
import contextlib
import importlib.util
import inspect
import io
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True     # never drop __pycache__ into a --root snapshot

REPO = Path(__file__).resolve().parent.parent
PASSES: list[str] = []
FAILS: list[str] = []
WAB_API = ("ab_arm_key", "ab_env", "stage_learned_weights", "restore_learned_weights", "staged_learned_weights",
           "score_answer_set", "deterministic_ab", "build_g4_evidence", "write_benchmark_evidence")
G4_ORDER = ("stage_learned_weights", "deterministic_ab", "build_g4_evidence", "write_benchmark_evidence")


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSES if ok else FAILS).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok or not detail else f" — {detail}"))


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _main(root: Path, argv: list, tag: str) -> "tuple[object, str, list]":
    """run_benchmark.main(argv) with do_run / do_selftest / do_merge recorded (never executed) -> (rc, stdout, modes)."""
    B = _load(root / "benchmark" / "run_benchmark.py", f"f4_rb_{tag}")
    ran: list = []
    B.do_run = lambda args, run_id: ran.append("do_run") or 0
    B.do_selftest = lambda run_id: ran.append("do_selftest") or 0
    B.do_merge = lambda args, run_id: ran.append("do_merge") or 0
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        try:
            rc = B.main(argv)
        except SystemExit as exc:          # --help exits
            rc = exc.code
    return rc, out.getvalue(), ran


def _help_for(help_text: str, flag: str) -> str:
    """One option's help paragraph (argparse wraps it over lines indented deeper than an option line)."""
    lines = help_text.splitlines()

    def is_opt(ln: str) -> bool:
        return ln.lstrip().startswith("-") and (len(ln) - len(ln.lstrip())) <= 4

    start = next((i for i, ln in enumerate(lines) if is_opt(ln) and ln.strip().startswith(flag)), None)
    if start is None:
        return ""
    block = [lines[start]]
    for ln in lines[start + 1:]:
        if is_opt(ln) or not ln.strip():
            break
        block.append(ln)
    return " ".join(" ".join(block).split())


def _calls(text: str, names) -> list:
    """Every `name(args)` in `text` for `names` -> [(name, [top-level args], source)]; ( [ { nest, <...> is an atom."""
    out = []
    for m in re.finditer(r"\b(" + "|".join(sorted(names, key=len, reverse=True)) + r")\(", text):
        i = j = m.end()
        depth = 1
        while j < len(text) and depth:
            depth += 1 if text[j] in "([{" else (-1 if text[j] in ")]}" else 0)
            j += 1
        args, buf, d = [], "", 0
        for ch in text[i:j - 1]:
            d += 1 if ch in "([{" else (-1 if ch in ")]}" else 0)
            if ch == "," and d == 0:
                args.append(buf.strip())
                buf = ""
            else:
                buf += ch
        if buf.strip():
            args.append(buf.strip())
        out.append((m.group(1), args, text[m.start():j]))
    return out


def _binds(fn, args: list) -> "str | None":
    """None when a call with these (placeholder) args binds to fn's signature, else why not."""
    pos = [a for a in args if not re.match(r"^[A-Za-z_]\w*\s*=", a)]
    kws = {a.split("=", 1)[0].strip(): None for a in args if re.match(r"^[A-Za-z_]\w*\s*=", a)}
    try:
        inspect.signature(fn).bind(*[None] * len(pos), **kws)
        return None
    except TypeError as exc:
        return str(exc)


# ---------------------------------------------------------------------------------------------- A
def gate_experiment_flags(root: Path) -> None:
    print("\n-- A. --ab-rerank / --ab-graph-signal are PLAN ONLY, like --ab-weights / --enrich-ab --")
    _rc, help_text, _ran = _main(root, ["--help"], "help")
    for flag in ("--ab-rerank", "--ab-graph-signal"):
        h = _help_for(help_text, flag).lower()
        check(f"{flag}: help says PLAN ONLY, captures nothing, and runs no ON/OFF matrix",
              "plan only" in h and "captures nothing" in h and "matrix" in h, h)
        rc, out, ran = _main(root, [flag], flag.strip("-").replace("-", "_"))
        check(f"{flag} alone: prints its plan and returns 0 WITHOUT running the ON/OFF matrix (do_run not called)",
              rc == 0 and not ran, f"rc={rc} ran={ran}")
        head = out.splitlines()[0].lower() if out.strip() else ""
        check(f"{flag}: the plan says PLAN ONLY and that it captures / spends nothing",
              "plan only" in head and "captures nothing" in head, head)
        check(f"{flag}: the closing [plan] line names the flag and says the matrix was NOT run",
              any(ln.startswith("[plan]") and flag in ln and "not" in ln.lower() for ln in out.splitlines()),
              out[-300:])
        rc, out, ran = _main(root, [flag, "--selftest"], flag.strip("-").replace("-", "_") + "_st")
        check(f"{flag} --selftest: an explicit mode still runs after the plan", ran == ["do_selftest"], str(ran))
    rc, out, ran = _main(root, ["--ab-rerank", "--ab-graph-signal", "--repo", "o/r"], "both")
    check("both flags + --repo, no explicit mode: still no matrix; the [plan] line names both flags",
          rc == 0 and not ran and any("--ab-rerank" in ln and "--ab-graph-signal" in ln
                                      for ln in out.splitlines() if ln.startswith("[plan]")), f"rc={rc} ran={ran}")
    rc, out, ran = _main(root, ["--ab-graph-signal", "--merge", "a,b"], "gs_merge")
    check("--ab-graph-signal --merge: the explicit mode still runs", ran == ["do_merge"], str(ran))
    rc, out, ran = _main(root, ["--repo", "o/r"], "plain")
    check("no plan flag -> the ordinary capture run is unchanged (do_run)", ran == ["do_run"], str(ran))

    rc, out, _ran = _main(root, ["--ab-rerank"], "rerank_text")
    B = _load(root / "benchmark" / "run_benchmark.py", "f4_rb_rerank_doc")
    doc = inspect.getdoc(getattr(B, "_print_ab_rerank_plan", None)) or ""
    check("the re-rank plan no longer implies --ab-weights performs a twin capture (it PRINTS the G4 procedure)",
          "--ab-weights G4 discipline" not in out and "unlike --ab-weights" not in doc.lower()
          and "--ab-weights prints" in out, out[-400:])

    def defs(path: Path) -> "tuple[set, bool]":
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
        main_block = any(isinstance(n, ast.If) and "__main__" in ast.dump(n.test) for n in tree.body)
        return names, main_block

    ap, ap_main = defs(root / "build" / "access_prior.py")
    gs, _gs_main = defs(root / "build" / "graph_signal_ab.py")
    rc, rerank, _ = _main(root, ["--ab-rerank"], "rerank_api")
    rc, gsig, _ = _main(root, ["--ab-graph-signal"], "gsig_api")
    check("the re-rank plan names only real entry points (python build/access_prior.py <root> — a __main__ block — and "
          "access_prior.evaluate_and_record)",
          "evaluate_and_record" in rerank and "evaluate_and_record" in ap and ap_main
          and "build/access_prior.py <root>" in rerank, f"defs={sorted(ap)[:8]} main={ap_main}")
    check("the graph-signal plan names only real entry points (graph_signal_ab.replay_and_record, neo4j_query_fn)",
          "replay_and_record" in gsig and "neo4j_query_fn" in gsig and {"replay_and_record", "neo4j_query_fn"} <= gs,
          f"defs={sorted(gs)[:8]}")


# ---------------------------------------------------------------------------------------------- B
def gate_ab_weights_plan(root: Path) -> None:
    print("\n-- B. the --ab-weights plan names weight_ab's REAL API, in _G4_PROCEDURE's order --")
    WAB = _load(root / "benchmark" / "weight_ab.py", "f4_wab")
    TW = sys.modules.get("tune_weights") or _load(root / "build" / "tune_weights.py", "f4_tw")
    g4 = getattr(TW, "_G4_PROCEDURE", "")
    check("(premise) tune_weights._G4_PROCEDURE exists and names the G4 steps in order",
          isinstance(g4, str) and all(n in g4 for n in G4_ORDER)
          and [g4.find(n) for n in G4_ORDER] == sorted(g4.find(n) for n in G4_ORDER), repr(g4)[:300])
    rc, out, ran = _main(root, ["--ab-weights", "--ab-slice", "multi_hop"], "abw")
    check("(premise) --ab-weights alone prints the plan and runs no matrix", rc == 0 and not ran, f"rc={rc} ran={ran}")
    steps = out.replace(g4, "") if g4 else out           # the numbered steps, without _G4_PROCEDURE's own shorthand
    calls = _calls(steps, WAB_API)
    named = {n for n, _a, _s in calls}
    missing = [n for n in ("ab_env", "staged_learned_weights", "restore_learned_weights", "deterministic_ab",
                           "build_g4_evidence", "write_benchmark_evidence") if n not in named]
    check("the plan CALLS ab_env / staged_learned_weights / restore_learned_weights / deterministic_ab / "
          "build_g4_evidence / write_benchmark_evidence", not missing, f"missing {missing}")
    check("...and names stage_learned_weights (what the staged block runs; _G4_PROCEDURE's first step)",
          re.search(r"\bstage_learned_weights\b", steps) is not None, "")
    phantom = [s for n, _a, s in calls if not callable(getattr(WAB, n, None))]
    check("every weight_ab call it prints is a real function (no phantom)", not phantom, str(phantom))
    unbound = [(s, why) for n, a, s in calls if callable(getattr(WAB, n, None))
               for why in [_binds(getattr(WAB, n), a)] if why]
    check("every weight_ab call it prints BINDS to the real signature (e.g. write_benchmark_evidence(root, evidence))",
          not unbound, str(unbound))
    env_calls = [(a, s) for n, a, s in calls if n == "ab_env"]
    check("the flag alone is not a launch env: ab_env('learned') == {'DEEPMAP_APPLY_LEARNED': '1'} (premise)",
          WAB.ab_env("learned") == {"DEEPMAP_APPLY_LEARNED": "1"}, str(WAB.ab_env("learned")))
    check("so every ab_env call in the plan passes the server's launch env (ab_env(variant, <env>)) for BOTH variants",
          len(env_calls) >= 2 and all(len(a) == 2 for a, _s in env_calls)
          and {a[0].strip("'\"") for a, _s in env_calls} >= {"equal", "learned"}, str([s for _a, s in env_calls]))
    token = re.search(r"staged_learned_weights\([^)]*\)\s+as\s+token\b", steps)
    fp = re.search(r"build_g4_evidence\([^)]*weights_fingerprint\s*=\s*token\[\s*['\"]fingerprint['\"]\s*\]", steps)
    check("build_g4_evidence binds weights_fingerprint=token['fingerprint'] from `staged_learned_weights(...) as "
          "token` (the binding `deepmap tune --apply` checks)", bool(token) and bool(fp), "")
    check("the lost-token recovery is named: weight_ab.restore_learned_weights(<clone root>)",
          any(n == "restore_learned_weights" and len(a) == 1 for n, a, _s in calls), "")
    pos = [steps.find(n) for n in G4_ORDER]
    check("the steps follow _G4_PROCEDURE's order (stage -> deterministic_ab -> build_g4_evidence -> "
          "write_benchmark_evidence)", all(p >= 0 for p in pos) and pos == sorted(pos), str(dict(zip(G4_ORDER, pos))))
    tune_at, apply_at = steps.find("`deepmap tune <"), steps.find("deepmap tune --apply")
    check("`deepmap tune <tuned root>` opens the procedure and `deepmap tune --apply` closes it",
          0 <= tune_at < steps.find("staged_learned_weights") and apply_at > steps.find("write_benchmark_evidence(")
          >= 0, f"tune@{tune_at} apply@{apply_at}")
    check("the plan prints tune_weights._G4_PROCEDURE verbatim (the text `deepmap tune --apply`'s refusal prints)",
          bool(g4) and g4 in out, "")
    check("--ab-slice reaches the evidence call (pre_registered_slice='multi_hop')",
          re.search(r"build_g4_evidence\([^)]*pre_registered_slice\s*=\s*'multi_hop'", steps) is not None, "")

    _rc, help_text, _ = _main(root, ["--help"], "abw_help")
    h = _help_for(help_text, "--ab-weights")
    check("--ab-weights help names staged_learned_weights and build_g4_evidence(weights_fingerprint=...) and stays "
          "PLAN ONLY", "staged_learned_weights" in h and "weights_fingerprint" in h and "plan only" in h.lower(), h)

    saved = sys.modules.get("weight_ab", KeyError)
    sys.modules["weight_ab"] = None            # `import weight_ab` now raises ImportError
    try:
        rc, fallback, _ = _main(root, ["--ab-weights"], "abw_noimport")
    finally:
        if saved is KeyError:
            sys.modules.pop("weight_ab", None)
        else:
            sys.modules["weight_ab"] = saved
    check("with weight_ab unimportable the plan still prints PLAN ONLY, every numbered step and a G4 line naming "
          "weight_ab.py", "PLAN ONLY" in fallback and all(f"  {i}. " in fallback for i in range(6))
          and "tune --apply`'s words" in fallback and "weight_ab.py" in fallback, fallback[-500:])


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
    print(f"DeepMap — A/B + experiment flags are plan-only; the G4 plan names the real API (root: {root})")
    print("=" * 72)
    gate_experiment_flags(root)
    gate_ab_weights_plan(root)

    total = len(PASSES) + len(FAILS)
    print("\n" + "=" * 72)
    if FAILS:
        print(f"RESULT: RED — {len(PASSES)}/{total} gates passed.")
        return 1
    print(f"RESULT: GREEN — {len(PASSES)}/{total} gates passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
