#!/usr/bin/env python3
"""DeepMap — one green entrypoint for all NO-NEO4J validations (CI-friendly).

Runs every in-session check that needs neither a live Neo4j nor the absent
product files, and returns a single PASS/FAIL + exit code. This is the local
analog of the `/oss-kit:oss-validate` fast gate.

What it runs (the AUTHORITATIVE list is the results.append(...) fan-out in main(),
NOT this header — kept count-free on purpose so it can't re-drift):
  * py_compile of every *.py under tests/, benchmark/, scripts/, build/ (syntax floor)
  * optional syntax checks when the tool is on PATH: bash -n install.sh,
    PowerShell parse install.ps1, and `claude plugin validate` on both manifests
  * every deterministic gate suite (tests/*_gates.py), each run in a subprocess —
    fusion/arm, MCP transport, the per-language recognizer registry (Python/Ruby/C#/Java/Go),
    confidence + framework/impact edges, the RM-1 learning loop, the benchmark
    scorers/harness, governance/compliance, the reliability hooks, the setup verdict,
    LLM enrichment, and more (copy_honesty_scan runs twice: --selftest + real copy)
  * benchmark/run_benchmark.py <selftest>   (harness dry-run, if present)

What it does NOT run: the live-Neo4j operator harness (stage0_capture_oracle.py,
stage0_regression_check.py) — those are operator-executed on a real instance
(see tests/oracles/README.md).

Internal-only gates (INTERNAL_ONLY below) check files that stay in the private development
repository; where such a gate's file is absent (the public repository) it is skipped with a
[skip] line. Every other registered gate that has gone missing still turns the run RED.

Usage:  python tests/run_all.py
Exit:   0 = all green; 1 = a check failed.
"""
from __future__ import annotations

import os
import py_compile
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable


def utf8():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass


_RAN_SCRIPTS: set = set()   # every script run() was handed — the unregistered-gate drift check reads it

# Registered gates that check files kept only in the private development repository (the product page and
# its copy). The public repository is published without them: there, a gate in this set whose own file is
# absent is SKIPPED with a [skip] line. Anywhere the file exists it runs as usual, and a registered gate
# NOT in this set that has gone missing still turns the run RED (no exists() guard for those).
INTERNAL_ONLY = frozenset({"tests/reliability_copy_gates.py"})


# Gates whose honest runtime on a busy Windows box exceeds the default 300 s per-gate budget (their real
# PowerShell / process probes are slow there; on Linux they finish in seconds). Measured 2026-09-29:
# code_restart_agreement_r10_gates 338 s on its own while other work ran. A timeout is reported as a FAIL,
# so without this a healthy suite goes RED on Windows under load.
_LONG_GATE_TIMEOUT_S = {"tests/code_restart_agreement_r10_gates.py": 900,
                        # ~25 sandboxed driver runs: 123-183 s on a loaded Windows box, 11 s on Linux (r283)
                        "tests/f4_breadth_resume_r283_gates.py": 600,
                        # auto-sync runner: ~70 s idle, 3-4 min alone on a loaded Windows box (real runners, waits)
                        "tests/auto_sync_runner_gates.py": 900,
                        # typed-receiver call linking: real-engine sections + captures of both engines; measured
                        # 35-160 s on a loaded Windows box
                        "tests/typed_calls_py_gates.py": 600,
                        "tests/typed_calls_push_gates.py": 600,
                        "tests/calls_family_filter_gates.py": 600,
                        "tests/typed_calls_shapes_gates.py": 600,
                        # 269-513 s under 100% CPU, 734-838 s with both engines' runs in parallel (2026-10-28)
                        "tests/call_linking_harness_gates.py": 1500,
                        # both engines' real children: 97-240 s on a loaded Windows box
                        "tests/typed_calls_engine_parity_gates.py": 600,
                        # ~55 s idle, 114-165 s at 100% CPU (sync() runs, a real-engine section, 26 mutants)
                        "tests/sync_relabel_twin_gates.py": 600,
                        # C# typed reader: real-engine sections on both captures plus 246 mutants, each a full
                        # re-run: 706-709 s on a Windows box beside two other gate lists (2026-10-31)
                        "tests/typed_calls_cs_gates.py": 1800,
                        # 67-202 s alone on a loaded Windows box (2026-10-30), 189-195 s beside 3 lanes (2026-10-31)
                        "tests/typed_calls_cs_fuzz_gates.py": 900,
                        # real git repos and sync() runs: 148-348 s alone on a loaded Windows box (2026-10-30)
                        "tests/sync_status_gates.py": 900,
                        "tests/map_graph_call_lines_gates.py": 600}


def run(label, argv, expect_zero=True):
    print(f"\n----- {label} -----\n  $ {' '.join(str(a) for a in argv)}")
    if argv:
        script = Path(str(argv[0])).as_posix()
        _RAN_SCRIPTS.add(script)
        if script in INTERNAL_ONLY and not (ROOT / script).is_file():
            print(f"  [skip] {script} is an internal-only gate (INTERNAL_ONLY) and is not in this checkout")
            return True
    # Decode the child as UTF-8 (and ask it to write UTF-8): with the locale codec (cp1255/cp1252 on
    # Windows) a gate's non-ASCII output raised UnicodeDecodeError here and its diagnostic tail was lost.
    # Auto-sync off for every gate (M12): a gate that runs a SessionStart hook for real must never spawn DeepMap's
    # background refresh. The auto-sync gates set DEEPMAP_AUTO_SYNC per case themselves.
    env = dict(os.environ, PYTHONIOENCODING="utf-8", DEEPMAP_AUTO_SYNC="0")
    try:
        p = subprocess.run([PY, *map(str, argv)], cwd=str(ROOT), env=env,
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=_LONG_GATE_TIMEOUT_S.get(Path(str(argv[0])).as_posix() if argv else "", 300))
    except Exception as exc:
        print(f"  [FAIL] could not run: {exc}")
        return False
    if p.returncode != 0 and expect_zero:
        # The tail below can miss WHICH check failed (it sits mid-output); print every FAIL line first.
        for line in [ln for ln in (p.stdout or "").splitlines() if "[FAIL]" in ln][:20]:
            print("  " + line.strip())
    tail = "\n".join((p.stdout or "").splitlines()[-6:])
    if tail:
        print("  " + tail.replace("\n", "\n  "))
    ok = (p.returncode == 0) if expect_zero else True
    print(f"  -> exit={p.returncode}  {'[PASS]' if ok else '[FAIL]'}")
    return ok


def selftest_flag(path: Path):
    """Detect which dry-run/self-test flag a script supports, if any."""
    try:
        src = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None
    for flag in ("--selftest", "--dry-run", "--self-test", "--dryrun"):
        if flag in src:
            return flag
    return None


def main():
    utf8()
    print("=" * 72)
    print("DeepMap — run_all (no-Neo4j validations)")
    print("=" * 72)

    results = []

    # 1) syntax floor: compile every python under tests/, benchmark/, scripts/
    compiled_ok = True
    for d in ("tests", "benchmark", "scripts", "build"):
        dd = ROOT / d
        if not dd.exists():
            continue
        for py in sorted(dd.glob("*.py")):
            try:
                py_compile.compile(str(py), doraise=True)
            except py_compile.PyCompileError as e:
                compiled_ok = False
                print(f"  [FAIL] py_compile {py.relative_to(ROOT)}: {e.msg}")
    print(f"py_compile tests/ + benchmark/ + scripts/: {'[PASS]' if compiled_ok else '[FAIL]'}")
    results.append(("py_compile", compiled_ok))

    # 1b) shell syntax for the reconstructed installer (bash -n), if bash + file present
    sh = ROOT / "install.sh"
    if sh.exists() and __import__("shutil").which("bash"):
        try:
            p = subprocess.run(["bash", "-n", str(sh)], capture_output=True, text=True, timeout=30)
            ok = p.returncode == 0
            if not ok:
                print(f"  [FAIL] bash -n install.sh: {p.stderr.strip()}")
            print(f"bash -n install.sh: {'[PASS]' if ok else '[FAIL]'}")
            results.append(("bash -n install.sh", ok))
        except Exception as exc:
            print(f"  [skip] bash -n install.sh: {exc}")

    # 1c) PowerShell syntax check for the Windows installer (parse-only, never executes), if a
    #     PowerShell is on PATH. Mirrors `bash -n install.sh` so install.ps1 isn't the one
    #     load-bearing installer with no syntax gate. ParseFile builds the AST without running it.
    ps1 = ROOT / "install.ps1"
    ps_exe = __import__("shutil").which("pwsh") or __import__("shutil").which("powershell")
    if ps1.exists() and ps_exe:
        esc = str(ps1).replace("'", "''")
        ps_script = (
            "$tokens=$null; $errors=$null; "
            "[void][System.Management.Automation.Language.Parser]::ParseFile("
            f"'{esc}',[ref]$tokens,[ref]$errors); "
            "if ($errors.Count -gt 0) { $errors | ForEach-Object { Write-Output $_.Message }; "
            "exit 1 } else { exit 0 }"
        )
        try:
            p = subprocess.run([ps_exe, "-NoProfile", "-NonInteractive", "-Command", ps_script],
                               capture_output=True, text=True, timeout=60)
            ok = p.returncode == 0
            if not ok:
                print(f"  [FAIL] {ps_exe} parse install.ps1: {(p.stdout or p.stderr).strip()[:400]}")
            print(f"parse install.ps1 ({Path(ps_exe).name}): {'[PASS]' if ok else '[FAIL]'}")
            results.append(("parse install.ps1", ok))
        except Exception as exc:
            print(f"  [skip] parse install.ps1: {exc}")

    # 1d) Official `claude plugin validate` on BOTH manifests, if the claude CLI is on PATH. This is
    #     the exact schema check the community-marketplace submission pipeline runs (docs: "Run
    #     claude plugin validate locally before you submit"). Offline + fast; exits 0 on pass (even
    #     with warnings), 1 on a schema error. Skipped where claude is absent (e.g. CI) — the
    #     offline mirror in plugin_manifest_gates still runs there.
    claude_exe = __import__("shutil").which("claude")
    mk = ROOT / ".claude-plugin" / "marketplace.json"
    pj = ROOT / ".claude-plugin" / "plugin.json"
    if claude_exe and mk.exists() and pj.exists():
        for label, target in (("marketplace.json", str(ROOT)), ("plugin.json", str(pj))):
            try:
                p = subprocess.run([claude_exe, "plugin", "validate", target],
                                   capture_output=True, text=True, timeout=90)
                ok = p.returncode == 0
                if not ok:
                    tail = "\n".join((p.stdout or p.stderr or "").splitlines()[-6:])
                    print(f"  [FAIL] claude plugin validate {label}:\n  " + tail.replace("\n", "\n  "))
                print(f"claude plugin validate {label}: {'[PASS]' if ok else '[FAIL]'}")
                results.append((f"claude plugin validate {label}", ok))
            except Exception as exc:
                print(f"  [skip] claude plugin validate {label}: {exc}")

    # 2) the deterministic gate suites
    results.append(("stage0_insession_gates",
                    run("STAGE-0 in-session fusion gates",
                        ["tests/stage0_insession_gates.py"])))
    results.append(("copy_honesty_scan --selftest",
                    run("copy-honesty scanner selftest",
                        ["tests/copy_honesty_scan.py", "--selftest"])))
    results.append(("copy_honesty_scan (real public copy)",
                    run("copy-honesty + version-consistency scan over the REAL shipped public copy",
                        ["tests/copy_honesty_scan.py"])))
    results.append(("mcp_transport_gates",
                    run("MCP transport + provisioner gates",
                        ["tests/mcp_transport_gates.py"])))
    results.append(("summarize_gates",
                    run("intent-summary synthesis gates",
                        ["tests/summarize_gates.py"])))
    results.append(("graphify_map_gates",
                    run("graphify graph.json -> typed schema gates",
                        ["tests/graphify_map_gates.py"])))
    results.append(("graphify_freshness_gates",
                    run("graphify extract freshness (F3e: a failed graphify run must not silently reuse a "
                        "STALE graph.json; mtime guard, ast fallback, benign-warning still used)",
                        ["tests/graphify_freshness_gates.py"])))
    results.append(("precise_calls_gates",
                    run("opt-in precise CALLS resolution (P0-1: DEEPMAP_PRECISE_CALLS keeps graphify's "
                        "resolved target -> ONE EXTRACTED edge vs name fan-out; flag-off byte-identical)",
                        ["tests/precise_calls_gates.py"])))
    results.append(("graphify_multilang_gates",
                    run("graphify multi-language typed-schema gates (ts/js/go/rust/java/ruby/c#)",
                        ["tests/graphify_multilang_gates.py"])))
    results.append(("skill_gates",
                    run("reasoning skill + commands gates",
                        ["tests/skill_gates.py"])))
    results.append(("plugin_manifest_gates",
                    run("plugin packaging + version-coherence gates (manifest location, T16)",
                        ["tests/plugin_manifest_gates.py"])))
    results.append(("installer_gates",
                    run("installer git-hook sync wiring + accuracy gates (real entrypoint, no phantom)",
                        ["tests/installer_gates.py"])))
    results.append(("installer_hardening_gates",
                    run("installer hook-safety + DEEPMAP_PYTHON interpreter lever (F1: no --stale-cleanup "
                        "in the auto-hook + sentinel-wrapped create branch; F4: DEEPMAP_PYTHON override, "
                        "CLI snippet uses sys.executable, never blind python3)",
                        ["tests/installer_hardening_gates.py"])))
    results.append(("retrieval_robustness_gates",
                    run("retrieval robustness (T10) gates",
                        ["tests/retrieval_robustness_gates.py"])))
    results.append(("candidate_floor_gates",
                    run("opt-in candidate-pool floor (P1-7: DEEPMAP_CANDIDATE_FLOOR; fetch_k=max(top_k*3,"
                        "floor); default 0 == byte-identical top_k*3, arms receive the lifted pool)",
                        ["tests/candidate_floor_gates.py"])))
    results.append(("sync_lock_gates",
                    run("sync concurrency lock (T10) gates",
                        ["tests/sync_lock_gates.py"])))
    results.append(("switch_guard_gates",
                    run("project-switch safety guard (owning-root + instance lock) gates",
                        ["tests/switch_guard_gates.py"])))
    results.append(("exclude_tests_gates",
                    run("opt-in test-directory exclusion (DEEPMAP_EXCLUDE_TESTS) gates",
                        ["tests/exclude_tests_gates.py"])))
    results.append(("vendor_exclude_gates",
                    run("default-on vendor/generated path-ignore (P0-3: node_modules/vendor/public/"
                        "*.min.js + .gitignore bare-dirs; dir-component match; byte-identity + escape hatch)",
                        ["tests/vendor_exclude_gates.py"])))
    results.append(("confidence_gates",
                    run("confidence model gates",
                        ["tests/confidence_gates.py"])))
    results.append(("framework_edges_gates",
                    run("framework/runtime edge recognizers + typed-edge push gates (Track C)",
                        ["tests/framework_edges_gates.py"])))
    results.append(("impact_traversal_gates",
                    run("multi-hop impact traversal + freeze/no-ranking-change gates (Track C)",
                        ["tests/impact_traversal_gates.py"])))
    results.append(("impact_profile_gates",
                    run("opt-in impact edge_types / ORM-association profile (P1-1: per-call edge_types + "
                        "DEEPMAP_IMPACT_PROFILE=rails; frozen default stays association-free)",
                        ["tests/impact_profile_gates.py"])))
    results.append(("impact_hash_notation_gates",
                    run("impact Class#method notation (F10d: Ruby '#' -> stored '.'; query-input only, "
                        "backward-compatible, off the frozen ranking path)",
                        ["tests/impact_hash_notation_gates.py"])))
    results.append(("degradation_gates",
                    run("FTS-only graceful-degradation gates",
                        ["tests/degradation_gates.py"])))
    results.append(("embeddings_gates",
                    run("embeddings pure-function gates",
                        ["tests/embeddings_gates.py"])))
    results.append(("index_coherence_gates",
                    run("index/retrieval/embeddings coherence gates",
                        ["tests/index_coherence_gates.py"])))
    results.append(("cli_gates",
                    run("deepmap CLI parser gates",
                        ["tests/cli_gates.py"])))
    results.append(("cli_bounds_gates",
                    run("search/impact result-count bounds — single source of truth (mcp_bounds) shared by "
                        "the MCP server + CLI; clamp + clear out-of-range message",
                        ["tests/cli_bounds_gates.py"])))
    results.append(("stage0_harness_gates",
                    run("STAGE-0 oracle harness gates (stable keys, no elementIds)",
                        ["tests/stage0_harness_gates.py"])))
    results.append(("stage0_external_gates",
                    run("STAGE-0 external-repo decoupling gates (repo-parametric keys, oracle registry)",
                        ["tests/stage0_external_gates.py"])))
    results.append(("lazy_init_gates",
                    run("lazy-init concurrency gates (single model/driver under load)",
                        ["tests/lazy_init_gates.py"])))
    results.append(("audit_fix_gates",
                    run("audit-fix regression gates (path-sep normalization, conf I/O guard)",
                        ["tests/audit_fix_gates.py"])))
    results.append(("benchmark_wiring_gates",
                    run("benchmark WITH-arm wiring gates (ISS-build:002, type:http vs .mcp.json)",
                        ["tests/benchmark_wiring_gates.py"])))
    results.append(("benchmark_probe_gates",
                    run("benchmark --probe assertion gates (ON-load/OFF-isolation/capture, no spend)",
                        ["tests/benchmark_probe_gates.py"])))
    results.append(("benchmark_honesty_gates",
                    run("benchmark honesty state-machine + pin fail-closed gates (no spend)",
                        ["tests/benchmark_honesty_gates.py"])))
    results.append(("benchmark_run_scale_gates",
                    run("benchmark run-scale gates (--runs-per-arm + --merge + honest rendering, no spend)",
                        ["tests/benchmark_run_scale_gates.py"])))
    results.append(("provision_checksum_gates",
                    run("provisioner Neo4j-download SHA-256 verification gates (no network)",
                        ["tests/provision_checksum_gates.py"])))
    results.append(("benchmark_config_gates",
                    run("real benchmark-config gates (stdlib==PyYAML on the shipped yaml, no spend)",
                        ["tests/benchmark_config_gates.py"])))
    results.append(("benchmark_scoring_gates",
                    run("deterministic completeness-scorer gates (word-bounded, no over-credit)",
                        ["tests/benchmark_scoring_gates.py"])))
    results.append(("benchmark_correctness_gates",
                    run("blind correctness-judge scaffold gates (arm-blind, honest-on-failure, no spend)",
                        ["tests/benchmark_correctness_gates.py"])))
    results.append(("benchmark_score_run_gates",
                    run("Stage-2 scoring-driver gates (completeness+judge -> measured, honest-on-fail)",
                        ["tests/benchmark_score_run_gates.py"])))
    results.append(("benchmark_score_cli_gates",
                    run("Stage-2 scoring-CLI wiring gates (do_score: judge argv, reader, ENV-guard, no spend)",
                        ["tests/benchmark_score_cli_gates.py"])))
    results.append(("scorer_fuzz_gates",
                    run("scorer/judge-parser fuzz gates (3000 seeded adversarial inputs: no-crash + determinism)",
                        ["tests/scorer_fuzz_gates.py"])))
    results.append(("scorer_property_gates",
                    run("completeness-matcher metamorphic gates (order-indep, monotonic, self-credit, alias)",
                        ["tests/scorer_property_gates.py"])))
    results.append(("benchmark_fairness_gates",
                    run("run-loop fairness/reproducibility gates (pristine reset, ON-arm graph SHA, sampling)",
                        ["tests/benchmark_fairness_gates.py"])))
    results.append(("sync_manifest_gates",
                    run("sync graph-build manifest gates (provenance sidecar, producer->consumer round-trip)",
                        ["tests/sync_manifest_gates.py"])))
    results.append(("sync_prune_guard_gates",
                    run("stale-cleanup wipe guard (F3d P0: engine-downgrade skip + >50% blast-radius "
                        "backstop + --force-prune override; real sync() skips DETACH DELETE)",
                        ["tests/sync_prune_guard_gates.py"])))
    results.append(("sync_gitignore_gates",
                    run("sync auto-gitignore gates (self-ignoring .gitignore in graphify-out/ + .deepmap/; "
                        "idempotent; never clobbers a foreign .gitignore; benchmark non-regression)",
                        ["tests/sync_gitignore_gates.py"])))
    results.append(("embedded_count_gates",
                    run("true-embedded-count gates (P1-5: re-sync delta 0 must NOT fire a false DEGRADED; "
                        "verdict+manifest read the graph total, best-effort fallback, no Neo4j)",
                        ["tests/embedded_count_gates.py"])))
    results.append(("incremental_sync_gates",
                    run("incremental sync + node-key index gates (DEEPMAP_INCREMENTAL_SYNC: per-file "
                        "content-hash cache == full rebuild; add/change/delete/move; RED-under-mutation)",
                        ["tests/incremental_sync_gates.py"])))
    results.append(("benchmark_cost_estimate_gates",
                    run("benchmark cost-model gates (plan/OpenRouter split, monotonic, out-of-pocket)",
                        ["tests/benchmark_cost_estimate_gates.py"])))
    results.append(("benchmark_arm_runner_gates",
                    run("RM-2 OpenRouter arm-runner gates (envelope parity, sandbox, dispatch, Enabler A/B, no spend)",
                        ["tests/benchmark_arm_runner_gates.py"])))
    results.append(("subprocess_encoding_gates",
                    run("subprocess UTF-8 decode gates (claude/git/smoke-test, cross-platform, no spend)",
                        ["tests/subprocess_encoding_gates.py"])))
    results.append(("provision_robustness_gates",
                    run("provisioner process-mgmt robustness gates (POSIX killpg tree, early-exit, no spawn)",
                        ["tests/provision_robustness_gates.py"])))
    results.append(("compliance_gates",
                    run("license-compliance drift gates (NOTICE/SBOM vs deps, ranx-not-shipped, no spend)",
                        ["tests/compliance_gates.py"])))
    results.append(("governance_gates",
                    run("governance/go-public doc gates (SECURITY/AI_POLICY presence, honesty-backed, link integrity)",
                        ["tests/governance_gates.py"])))
    results.append(("verdict_gates",
                    run("setup verdict gates (P1/P4/P5 honest READY/DEGRADED/FAILED; census, exit-code, no Neo4j)",
                        ["tests/verdict_gates.py"])))
    results.append(("verdict_coverage_gates",
                    run("per-language/per-dir coverage honesty gates (dead app/models -> DEGRADED; manifest verdict fields)",
                        ["tests/verdict_coverage_gates.py"])))
    results.append(("verdict_vendor_erb_gates",
                    run("census vendor/ERB honesty (F9: public/*.min.js excluded like sync -> no false JS "
                        "DEGRADED; .erb->ERB language; app/views/helpers/lib tracked)",
                        ["tests/verdict_vendor_erb_gates.py"])))
    results.append(("delphi_pascal_gates",
                    run("Delphi/Object-Pascal extractor-allowlist + verdict honesty (2026-08 field "
                        "report: .pas/.dpr/.inc dropped by SOURCE_CODE_EXTS -> Delphi-free graph "
                        "reported READY; engine-drift + census/extractor drift locks)",
                        ["tests/delphi_pascal_gates.py"])))
    results.append(("case_insensitive_ext_gates",
                    run("legacy UPPERCASE source filenames (UNIT1.PAS/MAIN.DPR) still extract — the "
                        "engine's suffix table is case-sensitive; DeepMap aliases odd-case suffixes "
                        "onto the lowercase extractor ONLY when such files exist (all-lowercase repo "
                        "= byte-identical invocation), degrading to a no-op on any engine drift",
                        ["tests/case_insensitive_ext_gates.py"])))
    results.append(("recognizer_registry_gates",
                    run("language-recognizer registry gates (Python parity keystone, drift-locks, anti-phantom prune)",
                        ["tests/recognizer_registry_gates.py"])))
    results.append(("merge_summaries_gates",
                    run("opt-in recognizer summary-merge (F10b: DEEPMAP_MERGE_SUMMARIES appends the "
                        "association fact tail into a colliding base node; default OFF = byte-identical; "
                        "list-identity + idempotency + cap held)",
                        ["tests/merge_summaries_gates.py"])))
    results.append(("rails_edges_gates",
                    run("Ruby/Rails recognizer gates (ActiveRecord DSL -> typed edges; CASCADES reversed; inflector)",
                        ["tests/rails_edges_gates.py"])))
    results.append(("rails_fuzz_gates",
                    run("Rails recognizer hardening (300-source fuzz, metamorphic, sync->Cypher money gate, hook fuzz)",
                        ["tests/rails_fuzz_gates.py"])))
    results.append(("csharp_edges_gates",
                    run("C#/.NET recognizer gates (EF nav/fluent/DI/routes -> typed edges; third-file "
                        "CASCADES via the single-candidate global src fallback)",
                        ["tests/csharp_edges_gates.py"])))
    results.append(("csharp_fuzz_gates",
                    run("C# recognizer hardening (300-source fuzz, metamorphic, sync->Cypher money gate)",
                        ["tests/csharp_fuzz_gates.py"])))
    results.append(("java_edges_gates",
                    run("Java recognizer gates (Spring/JPA annotations -> typed edges; ctor/@Autowired/Lombok DI; "
                        "@*Mapping routes; cascade honesty)",
                        ["tests/java_edges_gates.py"])))
    results.append(("java_fuzz_gates",
                    run("Java recognizer hardening (300-source fuzz, metamorphic, sync->Cypher money gate)",
                        ["tests/java_fuzz_gates.py"])))
    results.append(("go_edges_gates",
                    run("Go recognizer gates (GORM relations/cascades, struct-embedding INHERITS, Gin/Chi routes, "
                        "NewX DI; no-IMPLEMENTS honesty; GORM cascade orientation (LookUpField fidelity), "
                        "package safety, linear-cost scaling)",
                        ["tests/go_edges_gates.py"])))
    results.append(("go_fuzz_gates",
                    run("Go recognizer hardening (300-source fuzz, metamorphic, sync->Cypher money gate)",
                        ["tests/go_fuzz_gates.py"])))
    results.append(("typescript_edges_gates",
                    run("TypeScript/JavaScript recognizer gates (NestJS routes/DI/jobs/events/guards, TypeORM "
                        "relations + cascades, Express/Fastify/koa/Hono routers (typed and class-based too) + "
                        "cross-file mounts, Next.js file routes, Angular routes/DI; resolved through the real "
                        "engine's output (captured 0.8.39 + the installed engine); look-alikes emit nothing; "
                        "module-scoped handlers)",
                        ["tests/typescript_edges_gates.py"])))
    results.append(("typescript_fuzz_gates",
                    run("TypeScript/JavaScript recognizer hardening (300-source fuzz, 300 mutations of the "
                        "fixture app, finalize fuzz, metamorphic, sync->Cypher money gate)",
                        ["tests/typescript_fuzz_gates.py"])))
    results.append(("call_linking_harness_gates",
                    run("call-linking S0 measurement kit + PREREG (offline pass order == sync() read from sync.py's AST; "
                        "real sync() vs offline_calls data flow on ast + graphify; frozen flask 330/26; exact seeded "
                        "scorers; stage0_refreeze --env; jedi / TypeScript never shipped)",
                        ["tests/call_linking_harness_gates.py"])))
    results.append(("typed_calls_core_gates",
                    run("typed-receiver call linking, the core (fake reader): REPLACE/ADD truth table with the textual "
                        "veto + census, candidate sets only narrow, caller policy, the audit, the EXTRACTED evidence chain, "
                        "never cross-family, flag-off identity, dynamic dispatch (subclass overrides stay reachable, never "
                        "upgraded; interface / unresolved reach -> ADD only; field subtree bound), PATCH_RULES loose (per "
                        "family, test-scoped) / star, static_split, link(sources=), 300-graph fuzz, mutation teeth",
                        ["tests/typed_calls_core_gates.py"])))
    results.append(("typed_calls_py_gates",
                    run("typed-receiver call linking: the Python reader + import-root resolver on both engines' captures "
                        "(shapes, must-nots, dynamic dispatch incl. click's shapes and a typing.Protocol receiver, audit, "
                        "parity, mutation teeth)",
                        ["tests/typed_calls_py_gates.py"])))
    results.append(("typed_calls_ts_gates",
                    run("Typed-receiver call linking, the TypeScript / JavaScript reader (receiver_facts_ts: the IR, the "
                        "binder through typescript_edges.modules_for, every design shape and must-not end to end on the "
                        "captured graphify 0.8.39 + 0.8.51 trees and the installed engine; the core's static_split / "
                        "PATCH_RULES; dynamic dispatch + patched builtin prototypes on the real engine; an absent "
                        "tree-sitter disables the languages; mutation teeth)",
                        ["tests/typed_calls_ts_gates.py"])))
    results.append(("typed_calls_ts_fuzz_gates",
                    run("Typed-receiver call linking, TS/JS reader hardening (300 seeded programs + 300 byte mutations: "
                        "never raises, no edge without a real call, no removal in a parse-error scope)",
                        ["tests/typed_calls_ts_fuzz_gates.py"])))
    results.append(("typed_calls_shapes_gates",
                    run("typed-receiver call linking, S4: every design must-not shape (Python, TS/JS, the precise fixes, "
                        "the family filter, the non-production paths) beside its positive sibling, judged on the FINAL "
                        "graph after every push pass (sync's own pipeline, order locked to _sync_pass's AST, a real sync() "
                        "pass cross-checked); the installed engine for real + the graphify 0.8.39 and 0.8.51 captures; "
                        "graph-wide invariants; the V0 -> VT effect diff",
                        ["tests/typed_calls_shapes_gates.py"])))
    results.append(("typed_calls_engine_parity_gates",
                    run("typed-receiver call linking, S4: engine parity 0.8.39 vs 0.8.51 on every typed-calls fixture "
                        "(PREREG A1: common endpoints, raw reported): production typed tuples identical under the shipped "
                        "policy, every caller's with TESTS/NONPROD=1, E via the kit's engdiff.criterion; a self-test of "
                        "the comparison (--corpora, opt-in: the 5 Python corpora + zod on both real engines)",
                        ["tests/typed_calls_engine_parity_gates.py"])))
    results.append(("django_sqla2_edges_gates",
                    run("Python recognizer: Django URLconfs (path/re_path/url -> ROUTES_TO; include() paths and "
                        "url namespaces across modules; imported views placed where defined), SQLAlchemy 2.0 typed "
                        "Mapped[...] relations, Python ORM cascades (cascade= / ondelete= / on_delete=); Django "
                        "'app_label.Model' bound in that app; imports the post-pass cannot follow keep the name rule; "
                        "look-alikes emit nothing; real ast + graphify nodes; seeded fuzz; bounded include/mount cycles",
                        ["tests/django_sqla2_edges_gates.py"])))
    results.append(("sessionstart_notify_gates",
                    run("SessionStart onboarding notify (unprovisioned surface; once-ever, root-pristine) + mcp-stop nothing-to-stop",
                        ["tests/sessionstart_notify_gates.py"])))
    results.append(("provision_preflight_gates",
                    run("provisioner preflight gates (P3: Java 17/21, ports, disk — fail fast, no Neo4j/java)",
                        ["tests/provision_preflight_gates.py"])))
    results.append(("sessionstart_heal_gates",
                    run("P2 SessionStart heal gates (heal() revive-no-resync + marker-gated non-blocking hook)",
                        ["tests/sessionstart_heal_gates.py"])))
    results.append(("port_registry_gates",
                    run("per-project MCP port registry (side-by-side projects: distinct free ports + distinct "
                        ".mcp.json wiring; single-project unchanged; per-port pidfile; never-raises)",
                        ["tests/port_registry_gates.py"])))
    results.append(("per_project_neo4j_gates",
                    run("TRUE per-project Neo4j ISOLATION (per-project bolt/http instance: owner-aware base "
                        "adoption / field-case both orders; neo4j_uri_for; resolved_instance; paths byte-"
                        "identity; conf helpers; NO-duplicate-active-key U2; 6-process alloc race; per-URI lock)",
                        ["tests/per_project_neo4j_gates.py"])))
    results.append(("foreign_base_neo4j_gates",
                    run("foreign-held base Neo4j (2026-09 field report: a Docker Neo4j on 7687/7474 -> "
                        "defer to the project's own instance; self-heal a persisted base claim; re-adopt "
                        "the .mcp.json pair; setup restarts only pid+cmdline-verified own stale servers)",
                        ["tests/foreign_base_neo4j_gates.py"])))
    # v0.28 — the 2026-09-24 live-validation fix batch (317 live cases; each gate RED-first vs the bug it locks)
    for _name, _desc in (
        ("registry_robustness_gates", "registry read/replace retry + never-guess-7687 routing + UTF-8 git identity"),
        ("hook_reliability_gates", "hooks: UTF-8 stdin, DEEPMAP_HOOKS=0 kills heal, syncflag EVERY=1, heal-status surfacing"),
        ("provision_lifecycle_gates", "lifecycle: per-port start lock, pid+creation-time identity, heal status, safe stop/status"),
        ("cli_project_honesty_gates", "CLI: doctor probes THIS project's ports, --force-prune, FAILED exit code, git-root offline cmds"),
        ("enrich_harvest_gates", "enrich --deterministic harvests the doc comment ABOVE a def; no fake 'enriched' counts"),
        ("impact_limit_honesty_gates", "impact: dedupe BEFORE limit, honest empty/truncated notes, bounded expensive walks"),
        ("fts_keyword_cap_gates", "FTS: standalone AND/OR/NOT/TO no longer kill the BM25 arm; term cap; byte-identical otherwise"),
        ("mcp_server_concurrency_gates", "MCP servers: tools off the event loop, query cap, warm-up, FTS-only degrade"),
        ("extraction_engine_gates", "extraction: short GRAPHIFY_OUT (MAX_PATH), fresh graph.json, rescued skip-dirs, honest fallback, self-call rebind"),
        ("sync_internals_gates", "sync: batched atomic edge rebuild, root-relative keys, lock-skip rerun, manifest freshness"),
        ("typed_edge_resolution_gates", "typed edges: Class narrowing, language-scoped resolution, Rails only/except, Go handlers"),
        # round 2
        ("neo4j_route_gates", "ONE Neo4j routing rule (registry / env / instance-conf / legacy-unverified) — never guess 7687"),
        ("hook_freshness_gates", "hooks: outermost manifest root, dirty/stale suffixes, stale heal status re-checked, runnable remedies"),
        ("mcp_search_degrade_gates", "MCP search: model unavailable -> keyword-only + note; Neo4j down stays loud"),
        ("mcp_project_guard_gates", "MCP project guard: roots mismatch refused, fail-open without roots"),
        ("extraction_policy_gates", "extraction policy: never-rescue dirs, minified/vendor filtered, extraction == verdict"),
        ("typed_edge_batch_gates", "typed/IMPORTS edges written in bounded UNWIND batches, transaction-agnostic"),
        ("sync_neo4j_route_gates", "sync/indexes honour the shared route; legacy owner verified; no silent 7687"),
        ("sync_edge_tx_bound_gates", "edge rebuild in bounded transactions (prune + create budget; the "
                                     "relabel-twin census + delete in the push interpreter)"),
        ("sync_manifest_freshness_gates", "manifest: built_at_sha at START, dirty flag ignores DeepMap artifacts"),
        ("provision_route_identity_gates", "provisioner: shared route + legacy owner check, pid+creation identity, server env, gout purge"),
        ("cli_route_impact_gates", "CLI: shared route + owner verify, impact notes, ImpactTooExpensive, query cap, driver timeouts"),
        # round 3
        ("key_migration_gates", "one-time registry key migration for pre-UTF-8 non-ASCII identities (move + deferred alias)"),
        ("provision_round3_gates", "provisioner r3: Linux boot_id+ticks identity, sync-root server env, proof-only legacy adoption"),
        ("mcp_project_label_gates", "MCP servers name the project they serve in tool descriptions + instructions"),
        ("mcp_auth_backoff_gates", "MCP servers back off on repeated Neo4j auth failures (no login-lock feeding)"),
        ("graphify_version_guard_gates", "graphify version outside the validated range -> warn + skip the dir rescue"),
        ("resolved_imports_gates", "graphify-resolved import target keys -> exact IMPORTS (no N^2 AMBIGUOUS fan-out)"),
        ("import_key_binding_gates", "IMPORTS consumer binds resolved keys EXTRACTED, language-family checked"),
        ("benchmark_clone_graph_gates", "benchmark per-clone graph build under the routing rule; ON-arm ownership fail-closed"),
        ("cli_round3_gates", "CLI r3: rate-limit auth classification, doctor extraction provenance, pinned remedies"),
        # round 4
        ("graphify_supported_range_gates", "graphify validated range 0.8.39..0.8.51: CUDA/Metal indexed, .psd1/.xaml excluded, spec is the single source"),
        ("sync_owner_predicate_gates", "sync uses THE owner predicate (alias/legacy-aware, nested repos are separate projects)"),
        ("provision_round4_gates", "provisioner r4: owner predicate, stale base-claim re-verify, E2 no-record/comparable-stamp paths, alias-aware MCP owners"),
        ("embeddings_determinism_gates", "embedding build is deterministic for the same corpus (seeded batch order)"),
        ("cli_round4_gates", "CLI r4: pinned graphify pip hints from SUPPORTED_GRAPHIFY_SPEC, doctor graphify version/range, CLI owner check == owner_is_this_project"),
        # round 5
        ("owner_route_round5_gates", "owner route r5: non-absolute-on-this-OS stamps are foreign, nested-repo garbled stamps, real base owner not locked out, hooks never supersede a policy refusal"),
        ("provision_round5_gates", "provisioner r5: heal policy refusals (refused/refusal_kind) + owner-verified stop of own servers"),
        ("cli_round5_gates", "CLI r5: reads refuse a mis-adopted base claim (F2 for reads); defensive neo4j exception tuple"),
        # round 6
        ("owner_route_round6_gates", "owner route r6: explicit refused:false is authoritative; allocator asks base_adopt_ok outside the registry lock; setup/doctor docs"),
        ("provision_round6_gates", "provisioner r6: pre-upgrade sidecar-less servers restarted; live owner before record; legacy single-project users never stranded"),
        # round 7
        ("hooks_r7_gates", "hooks r7: persisted setup blocker is final, same-session heal window, legacy/DEGRADED staleness, foreign .mcp.json pair, BOM .mcp.json"),
        ("provision_r7_gates", "provisioner r7: Linux pid identity vs Neo4j's own pid file, stale-pid listener blocks setup, zombies dead, stop rc, write_mcp_json BOM/empty/non-dict, opt-in git hook"),
        ("fts_r7_gates", "FTS r7: byte-identical for every query Lucene accepts, gated split-code-tokens / short-name flags"),
        ("impact_r7_gates", "impact r7: method answers are lower bounds, unknown profile/edge notes, partial-answer caveats"),
        ("mcp_r7_gates", "MCP r7: per-query arm weights (no shared mutable state), SEARCH-clause note once, embed progress"),
        ("extraction_r7_gates", "extraction r7: graphify IMPORTS resolution, false-call filter, Go module id reuse, @overload, default-OFF gated flags"),
        ("verdict_r7_gates", "verdict r7: engine-only coverage, 1-file languages with 0 engine nodes, never-rescued dirs + non-git skipped dirs flagged"),
        ("engine_child_r7_gates", "graphify child dies with a killed sync (job object / process group)"),
        ("sync_r7_gates", "sync r7: ast receiver-aware resolution, nested-def shadowing, guard before push, no forced-ast duplication, long-path walker, line-key embed reuse"),
        ("typed_edges_r7_gates", "typed edges r7: C# HasMany via nav property, APScheduler, APIRouter/Blueprint prefixes, register_hook, disabled-recognizer prune"),
        ("cli_r7_gates", "CLI r7: .mcp.json claim + setup blocker in doctor, embed-server query path, hung vs stopped wording, edge-confidence mix"),
        ("enrich_r7_gates", "enrich r7: deterministic harvest + honest counts after the round-7 span changes"),
        # round 8
        ("hooks_r8_gates", "hooks r8: 127.0.0.1 probes, git-hook claim needs a real block for THIS root, code-version stale note, zombie-aware pid"),
        ("provision_r8_gates", "provisioner r8: git-hook syncs the sync root / never a shared hook / sh-only / legacy detection; plugin-update restart; verify query; instance-rm cleanup"),
        ("cli_r8_gates", "CLI r8: 127.0.0.1 URIs, one retry on a dropped connection, dead-Bolt-loop doctor signal, core.longpaths"),
        ("impact_module_r8_gates", "impact r8: module-qualified names (provision.is_bolt_up) resolve"),
        ("mcp_uri_r8_gates", "MCP servers r8: 127.0.0.1 URIs"),
        ("embed_threads_r8_gates", "bulk embedding uses multiple threads (sync/setup only), deterministic for a fixed thread count"),
        ("extraction_r8_gates", "extraction r8: round-7 graphify fixes hold on graphifyy 0.8.39 AND 0.8.51"),
        ("sync_r8_gates", "sync r8: push-time bare-name->Method and Go non-_test->_test filters, 127.0.0.1, core.longpaths"),
        ("selfcheck_r8_gates", "installer self-check: offline-first when the model is cached, bounded online time"),
        ("local_def_calls_gates", "DEEPMAP_LOCAL_DEF_CALLS: a bare Python call binds the caller's own module-level def (default OFF, byte-identical off)"),
        # round 9
        ("provision_r9_gates", "provisioner r9: git-hook on git<2.31, tip names the sync root, tracked hooksPath refused, v0.27.4 upgrade restart, never downgrade"),
        ("hooks_r9_gates", "hooks r9: code-stale note on the first upgrade, never 'stale' toward older code, install.ps1-written hook recognised"),
        ("installer_hook_r9_gates", "installers follow the git-hook rules (sh-only, after the shebang, sync root, never a shared/tracked hook)"),
        ("mcp_label_r9_gates", "MCP project label + remedy survive on Python 3.10-3.13"),
        # round 9 stage B
        ("extraction_alias_r9b_gates", "extraction r9b: trailing-dot receiver chains + bound-method aliases keep true calls (map time)"),
        ("sync_calls_r9b_gates", "sync r9b: trailing-dot chains, bound-method aliases (push time), LOCAL_DEF_CALLS alias safety"),
        ("embed_threads_r9b_gates", "bulk embedding uses a fixed, machine-independent thread count (DEEPMAP_EMBED_THREADS)"),
        # round 10
        ("provision_r10_gates", "provisioner r10: stale owner record = no record, working-config guard on code restarts, "
                                "in-place plugin update, equal versions never restart, command-line DEEPMAP_PYTHON, exact argv"),
        ("code_restart_agreement_r10_gates", "one code-restart rule for the reconnect note + heal, hooks.json python3 "
                                             "fallback, syncflag interpreter check"),
        ("extraction_calls_r10_gates", "extraction r10: PRECISE_CALLS never keys a receiver call to a module-level def; "
                                       "CALLS_PLUS keyed + non-test callers only"),
        ("sync_r10_gates", "sync r10: receiver-call unkey at push, FTS state == fresh build after sync, stale short_name "
                           "cleanup, opt-in exact vectors (DEEPMAP_VECTOR_QUANTIZATION=0)"),
        # round 11
        ("sync_timeout_r11_gates", "sync r11: size-aware graphify budget + DEEPMAP_GRAPHIFY_TIMEOUT, NOT-PUSHED exit 4 honoured "
                                   "by every caller, try/except module aliases under CALLS_PLUS"),
        ("calls_keyed_r11_gates", "calls r11: key-ness is explicit (a name equal to a node key never binds to it); a :Module "
                                  "caller is judged by its module-level statements only"),
        ("engine_venv_r11_gates", "engine r11: a killed/timed-out sync under a Windows venv leaves no graphify workers behind"),
        ("hooks_shell_r11_gates", "hooks r11: every hooks.json command parses + runs exactly once under sh/dash/bash, cmd, "
                                  "PowerShell 5.1 and 7, with python, python3 or both"),
        ("g5_fallback_r11b_gates", "G5 self-call rebinder: dunders through another receiver are dropped, the name fall-back "
                                   "never targets a :Module / :Class (flask-shaped real-engine fixture)"),
        ("sync_timeout_r11b_gates", "graphify budget counts re-included engine-skipped dirs whole; Invoke-Setup check is "
                                    "space-safe"),
        ("command_docs_r11b_gates", "command docs: runnable units work as fresh Bash calls (target passed), exit-4 re-run "
                                    "blocks, long runs in the background"),
        ("g5_member_targets_r11c_gates", "G5 member fall-back keeps :Method + :Function targets, excludes :Module/:Class, "
                                         "never promotes a grade"),
        ("calls_plus_scope_r11c_gates", "CALLS_PLUS module scope: node-less def bodies counted, a genuine rebinding of an "
                                        "import alias shadows it"),
        ("benchmark_clone_graph_r11c_gates", "benchmark per-clone graph: the driver teeth are independent of the git HEAD "
                                             "(committed pre-D1 fixtures)"),
        # 0.28.1
        ("no_console_window_gates", "Windows: background processes (heal, Neo4j, MCP servers) use a hidden console, "
                                    "so the git/tasklist/powershell programs they run never open a window"),
        ("plugin_update_cmd_gates", "/deep-map:plugin-update performs the update through the claude CLI (any host), "
                                    "deep-map@ name, marketplace first, verified on disk; user-only, the session's own "
                                    "CLI first, a new chat loads it"),
        # 0.28.2
        ("stop_windows_r282_gates", "Windows stop: a Neo4j tree that ended on its own ('no running instance of the "
                                    "task', pid gone) is stopped, not 'stop FAILED'; no taskkill re-run by pid"),
        # 0.28.3 — fix-all round 1 (r283: the 2026-09-29 DeepInit-refresh findings, one file owner each)
        ("o1_heal_interpreter_r283_gates", "SessionStart heal starts under DEEPMAP_PYTHON, else the interpreter "
                                           "setup recorded in <DEEPMAP_HOME>/run (machine state, never a project "
                                           "file), else its own Python; hostile or foreign records ignored "
                                           "(ISS-build:013)"),
        ("o1_base_heap_conf_r283_gates", "the base/legacy Neo4j conf gets the same bounded heap as every instance: "
                                         "content-addressed, no duplicate active key, heap validated, initial heap "
                                         "never above the cap (ISS-build:020)"),
        ("o1_ensure_never_raises_r283_gates", "ensure_neo4j never raises; a Windows JVM whose pid file can't be "
                                              "written is stopped, never left unrecorded; setup / ensure print a "
                                              "FAILED verdict and exit 1 (ISS-build:023)"),
        ("o1_mcp_label_roundtrip_r283_gates", "one formatter + one parser for an MCP server's project label "
                                              "(mcp_bounds.parse_project_instructions / parse_project_label); "
                                              "provision reads labels through it (ISS-build:024)"),
        ("o1_loopback_rule_r283_gates", "provision uses THE loopback rule (port_registry.is_loopback_host, incl. "
                                        "0:0:0:0:0:0:0:1); no inline loopback tuple left (C11)"),
        ("o1_docstring_truth_r283_gates", "port_registry docstrings state the unrecognized-registry base-pair "
                                          "exception (behaviour unchanged); _base_adopt_ok names the benchmark's "
                                          "real pre-allocation (ISS-build:027, C14)"),
        ("o2_mcp_impact_r283_gates", "MCP deepmap_impact stamps the project's last_used after an answered call (no "
                                     "idle hibernation mid-session); its description says the walk stays multi-hop "
                                     "over :CALLS (ISS-build:021, ISS-build:018)"),
        ("o3_retrieval_docstrings_r283_gates", "retrieval.py docstrings match the code (arm 3 = vector score x (1 + "
                                               "log(1 + inDegree)); impact() stays multi-hop over :CALLS); the "
                                               "fts_r7 re-lock of impact() is docstring-only (ISS-build:019/018)"),
        ("o4_cli_honesty_r283_gates", "CLI honesty: doctor words the driver error kind with a route-aware remedy, "
                                      "and an operator NEO4J_URI host that doesn't resolve as unreachable with the "
                                      "driver's DNS text + a check-the-host remedy (real driver, scripted resolver); "
                                      "cmd_impact docstring vs query_impact; access-hook matcher comment; tune "
                                      "--report names the G4 procedure (ISS-build:025/018, D6, ISS-benchmark:012)"),
        ("o5_sync_graph_unknown_r283_gates", "sync: an unreadable node count is UNKNOWN — engine memory kept "
                                             "(downgrade refused, exit 4), 0-node run keeps the manifest "
                                             "(ISS-build:017)"),
        ("o5_recognizer_vendor_seam_r283_gates", "recognizer seam: vendored/virtualenv/dist files and Claude Code's "
                                                 ".claude/worktrees/ checkouts never reach a recognizer (walk + "
                                                 "output, drift-locked to graphify's own skip rule); edge_resolve "
                                                 "docs (ISS-build:022, D25)"),
        ("o6_graphify_extract_r283_gates", "G5 self-call re-binder numbers lines like the engine (newline only, so a "
                                           "form feed no longer shifts them); extract()'s docstring states the "
                                           "contract the code keeps (ISS-build:016/028)"),
        ("o6_verdict_contract_r283_gates", "verdict.py states the real exit contracts; exit_code() stays unwired "
                                           "(STRICT_DEGRADED_RC named) and no strict flag is promised anywhere on "
                                           "the product surface (ISS-build:026, D25)"),
        ("o8_trigger_guard_r283_gates", "Python TRIGGERS: .connect/.on handlers get the round-7 variable guard "
                                        "(ISS-build:015); a module's connect() (PEP 249), a property or a "
                                        "class-assigned attribute names no symbol; true signal registrations kept; "
                                        "no stale typed-edge count (D25)"),
        ("o9_enrich_rehome_lock_r283_gates", "enrich keeps a re-homed paid summary over a stale/unusable entry (no "
                                             "loss, no re-bill; D24); its extraction holds sync's per-root lock and "
                                             "refuses while a sync holds it; honest module docstring (D25)"),
        ("o10_instance_state_lock_r283_gates", "every .deepmap/instance.json writer is one locked, atomic "
                                               "read-modify-write: never wipes a non-object file, never hangs, "
                                               "concurrent merges lose nothing (D5)"),
        ("o10_g4_bridge_r283_gates", "G4 bridge: the A/B scores answer_key.ground_truth_set; staging makes the "
                                     "served clone serve the learned weights and restores it exactly; evidence is "
                                     "bound to the weights by fingerprint (ISS-benchmark:009/012, D1/D2)"),
        ("o10_tuner_fidelity_r283_gates", "offline tuner / re-ranker order == live search() on ties, judged at "
                                          "RRF_K=60; a seed moves the grid via the tie-break; logs and replays fetch "
                                          "search()'s per-arm depth (D3/D4, C4)"),
        ("o11_arm_isolation_r283_gates", "benchmark arms: the ON arm loads only the clone's pair "
                                         "(--strict-mcp-config; ON == OFF + that pair); every claude child runs with "
                                         "DEEPMAP_HOOKS=0 and every hook opt-out off (ISS-benchmark:007/008)"),
        ("o11_blind_scoring_r283_gates", "blind scoring: a re-score starts from a clean slate and 'measured' needs "
                                         "scored=True; the judge guard and the recorded judge follow the model "
                                         "actually called; the cli judge reads each card's pinned, clean clone "
                                         "(ISS-benchmark:006/010/011)"),
        ("o11_g3_driver_resume_r283_gates", "G3 driver: the default set is the G3 scope, run-ids carry RUN_TAG, "
                                            "resumption is graph-provenance-checked (stale -> stop); a capture "
                                            "records graph_builds + deepmap_version (ISS-benchmark:005); the driver "
                                            "RUNS under bash in a sandbox (every resume branch; an unfinished "
                                            "capture is logged and moved aside, a failed capture's servers are "
                                            "stopped before the driver stops)"),
        ("o11_ab_plan_flags_r283_gates", "--ab-weights / --enrich-ab are PLAN ONLY: alone they print the plan and "
                                         "return 0 without the ON/OFF matrix; an explicit mode still runs; every "
                                         "helper the plan names exists (ISS-benchmark:012)"),
        ("o11_openrouter_tool_parity_r283_gates", "OpenRouter ON-arm tool schemas == the real MCP server functions "
                                                  "(params, required, JSON types, mcp_bounds defaults/caps; no "
                                                  "impact depth); descriptions <= 1024 chars; args forwarded "
                                                  "verbatim (ISS-benchmark:013)"),
        ("o12_learned_fusion_lock_r283_gates", "build/learned_fusion.py byte-freeze: a git-independent, "
                                               "CRLF-normalised sha256 content lock a committed change cannot pass, "
                                               "with mutation teeth (D21)"),
        ("o12_ci_header_r283_gates", "validate.yml's comments say what the job runs: tests/run_all.py and every "
                                     "registered gate suite, count-free; the pwsh / claude CLI checks skip where "
                                     "absent; live suites excluded or opt-in (D20)"),
        # 0.28.3 — fix-all round 2 (r283: follow-ups and residuals of round 1)
        ("f1_cli_followups_r283_gates", "CLI follow-ups: a refused enrich exits 4, prints nothing after its "
                                        "refusal line, and its help and doc name each cause (busy lock -> wait; "
                                        "unopenable lock / missing <root> -> fix it); tune help says what rrf_k / "
                                        "the seed / --apply really do; printed NEO4J_URIs mask their userinfo under "
                                        "both readings (an '@' after the host too); a refused instance.json write "
                                        "names its cause"),
        ("f2_sync_lock_holder_r283_gates", "a sync that finds the project's lock held names both possible holders "
                                           "(another sync, or deepmap enrich, which never performs the queued "
                                           "re-run); lock, queue and exit contract unchanged"),
        ("f2_go_principal_scope_r283_gates", "a Go belongs-to principal resolves in its own package, its directory "
                                             "exactly (root package, sub-package, same-named package, non-live "
                                             "twin): no fabricated cross-package INFERRED cascade, no edge dropped on "
                                             "a name two packages declare; typed rows anchored at their emitted src; "
                                             "the Go src-rule docstrings match edge_resolve"),
        ("f2_manifest_version_r283_gates", "the graph-build manifest records deepmap_version, the DeepMap that BUILT "
                                           "the graph (read once at import; additive, never raises; an older "
                                           "manifest still works)"),
        ("f3_framework_edges_r283_gates", "route-prefix finalize honours sync's exclusion rule (an excluded file, a "
                                          ".claude/worktrees/ checkout included, is neither a mount site nor an "
                                          "import target); the CASCADES comment names all four producers, emitted "
                                          "principal-first"),
        ("f4_breadth_resume_r283_gates", "run_breadth.sh resumes like run_g3_n4.sh (RUN_TAG run-ids, "
                                         "resume_state_for, stale -> stop) and only in the same capture shape (each "
                                         "MODE its own default PREFIX; breadth_shape.json: runs, extra arms, family "
                                         "-> another shape stops it); a failed capture is never scored or merged; "
                                         "JUDGE_MODEL reaches every --score; MODE/PREFIX/RUN_TAG/RUNS checked before "
                                         "the log opens; a moved-aside capture's answer paths are re-pointed; runs "
                                         "without PyYAML"),
        ("f4_ab_plan_api_r283_gates", "every A/B / experiment flag of run_benchmark.py is plan-only (--ab-rerank / "
                                      "--ab-graph-signal no longer fall through to a matrix); the G4 plan names and "
                                      "binds the real weight_ab API"),
        ("f4_resume_provenance_r283_gates", "resume reuses a capture only of THIS DeepMap's graph (the manifest's "
                                            "deepmap_version, strict when recorded) and only when the whole planned "
                                            "matrix ran (capture_complete)"),
        ("f4_judge_spelling_r283_gates", "the blind judge's separation-of-duties guard refuses the normalised "
                                         "spellings of an arm's own model (context tag, dated snapshot, Vertex / "
                                         "Bedrock id) and the `claude --model` selectors that can run an arm's model "
                                         "family, on either side (a tagged family alias, best, opusplan, fable, "
                                         "default or a blank model, a CLI-remapped legacy Opus id); an explicit "
                                         "transport is normalised; the cli judge never runs in plan mode; no "
                                         "over-reach"),
        ("f5_test_hygiene_r283_gates", "gates that drive provision.setup() run sandboxed (writes only in a private "
                                       "temp dir; no socket, no signal; never the machine's DeepMap home or this "
                                       "repo); sync_r10 gate H + delphi_pascal labels say what is true"),
        ("f6_learned_prior_seed_doc_r283_gates", "learned_prior.py's docstring says what a cold-start seed does in "
                                                 "the tuner (coord start + the grid's equal-score tie-break anchor, "
                                                 "global fit and every CV fold), proven on the real tuner"),
        ("f6_installer_declared_specs_r283_gates", "both installers install every version-constrained "
                                                   "requirements.txt package with that exact specifier and "
                                                   "--upgrade; anyio rides in mcp's own install command"),
        ("f6_dependabot_comment_r283_gates", "dependabot.yml's pip comment matches requirements.txt's real pins "
                                             "(validated ranges + anyio's floor; the drift lock it cites exists); "
                                             "still valid YAML"),
        # 0.28.3 — fix-all round 3 (r283: the completeness critics' findings)
        ("e_anyio_attribution_r283_gates", "the compliance artifacts say how anyio is installed (both installers "
                                           "name it in mcp's own pip command; CI gets it through mcp): nothing in "
                                           "requirements.txt, NOTICE, THIRD-PARTY-LICENSES.md, the SBOM or "
                                           "compliance_gates' section-14 comment contradicts the installers or CI"),
        # /deep-map:setup installs DeepMap's own environment (no terminal step)
        ("setup_bootstrap_gates", "/deep-map:setup on a Python without DeepMap's packages: Java + disk first, then "
                                  "<DEEPMAP_HOME>/venv with requirements.txt (CPU torch), the model (the one step "
                                  "off HF_HUB_OFFLINE), the self-check, setup re-run under it; every entry point "
                                  "re-runs under it (exit codes intact, loop guard); no driver -> 'packages are "
                                  "missing', never 'Neo4j did not become reachable'"),
        # deepmap_cypher: read-only Cypher over the project graph (MCP tool + `deepmap cypher`)
        ("cypher_tool_gates", "deepmap_cypher / `deepmap cypher`: validator truth table (keywords in strings, "
                              "comments and backtick names; \\u escapes Neo4j decodes before it lexes; allowlisted "
                              "CALL / SHOW), READ transaction always rolled back, row / byte / time caps, no "
                              "embedding vectors, bounded MCP wiring + CLI parity"),
        # an operator NEO4J_URI must be a Bolt URI: DeepMap refuses anything else itself (neo4j 6.4 reads http:// as HTTP)
        ("neo4j_uri_scheme_gates", "port_registry.neo4j_uri_problem: only the six Bolt schemes, no whitespace / control "
                                   "characters, a 0-65535 port; anything else is a route problem with a pasteable fix "
                                   "(the Browser's http://...:7474 -> bolt://...:7687), password masked; every consumer "
                                   "words it and none builds a driver; driver-version independent (6.2 and 6.4)"),
        # the search server survives a restart of its Neo4j (no failed first call after stop + heal / an upgrade)
        ("search_server_reconnect_gates", "the search server survives a restart of its Neo4j: the driver liveness-checks "
                                          "a pooled connection idle >= 2 s before handing it out, and a search / impact / "
                                          "cypher call that meets a dead pooled connection is retried ONCE (never a refused "
                                          "connect, an auth rejection or a query error; never twice)"),
        # typed-receiver call linking, S1: DEEPMAP_PRECISE_CALLS scoped by language family + the two precise fixes
        ("precise_family_gates", "DEEPMAP_PRECISE_CALLS is a set of language families (unset = the empty default, "
                                 "0 = none, 1 = every file, a CSV = those families); unset / 0 byte-identical to "
                                 "the frozen pre-S1 output on every captured graph (both engines), 1 + "
                                 "DEEPMAP_PRECISE_FIXES=0 = today's opt-in output; no node dict gains a key; S1b: "
                                 "map_graph / extract_with_report / extract read through a caller's _Source "
                                 "(source=) with identical output and leave it a filled, faithful cache"),
        ("precise_fixes_gates", "precise CALLS fixes: a key to a TS type alias / interface binds the value declared "
                                "beside it (zod's 14 OK/DIRTY calls) or else the name; graphify 0.8.39 placeholder "
                                "duplicate names dropped (the probe: 9 identical tuples on both engines); must-nots "
                                "locked"),
        # typed-receiver call linking, S2: the push integration in build/sync.py + the D0 cross-family filter
        ("typed_calls_push_gates", "typed-receiver call linking, S2 push integration: flag-off byte identity vs the frozen "
                                    "pre-S2 sync.py (9 flag modes x 40 synthetic projects); a marked CallKey honoured per "
                                    "tuple whatever the flags; call_typed grades exact (no T3#6 / LOCAL_DEF re-bind; the "
                                    "family / F8b / Go drops kept); call_weak last, only lowers; a PRECISE family CSV no "
                                    "longer flips keyed mode graph-wide; the typed step's wiring (order from sync.py's AST, "
                                    "weak one-shot extraction note, shared reader as sources= + source=, one stderr line); "
                                    "the manifest's call_linking block; captured graphs of both engines; tx-bound E0-E3 "
                                    "and incremental == full with TYPED on; 19 mutants"),
        ("calls_family_filter_gates", "DEEPMAP_CALLS_FAMILY_FILTER (D0): the name branch never reaches another language "
                                       "family (.ts/.js and .erb/.rb one family; unknown extensions never filtered; "
                                       "_attach_local_defs counts the kept targets); default OFF and classified in "
                                       "sync_status; only removes, every removed pair cross-family; the captured mix graph "
                                       "on both engines; 5 mutants"),
        # Neo4j 5.26.32 for new stores; existing stores sticky, upgraded only when verified
        ("neo4j_patch_upgrade_gates", "Neo4j 5.26.32 for new stores (both archives SHA-256-pinned); an existing store "
                                      "keeps its version (heal / hooks never change it; NEO4J_VERSION never downgrades "
                                      "or orphans it) until setup or neo4j-upgrade moves it: pinned patch releases only, "
                                      "owner-proven stop, copy, same answers or rollback; every journal phase written "
                                      "first; a crashed upgrade / cut-short rollback is recovered by the next setup; a "
                                      "rolled-back release is not retried by setup for a week"),
        # auto-sync (SessionStart background catch-up) — each gate RED-first
        ("sync_status_gates", "auto-sync records: sync_running/sync_last, atomic writes, build_recipe, env drift "
                              "lock, source fingerprint + git_busy"),
        ("sync_evidence_prune_gates", "evidence-only prune subset of full prune; READY + stale_kept caveat; H4 "
                                      "re-run merge"),
        ("sync_preempt_gates", "a user sync / setup / enrich preempts the background refresh and runs under its "
                               "own env"),
        ("auto_sync_runner_gates", "auto-sync runner: enabled/attended matrix, recipe replay, freshness, waits, "
                                   "slot, outcomes, exit codes"),
        ("auto_sync_hooks_gates", "auto-sync hooks: the heal hook starts the runner after the heal (exact argv, "
                                  "offline env, hidden console + BELOW_NORMAL, append-only autosync.log / heal.log "
                                  "that keep two overlapping starts' lines); notify / nudge / tool-result follow-ups "
                                  "once per fact; the edit note promises a refresh only when the next start will run "
                                  "it (no recorded repeating skip); auto-sync off == today's texts and files"),
        ("auto_sync_cli_wiring_gates", "auto-sync wiring: `sync --unattended=session-start` hidden + dispatched to "
                                       "auto_sync.run before sync loads (exit code passed through; never with "
                                       "--stale-cleanup / --switch-project / --force-prune); status / doctor "
                                       "auto-sync line informational; freshness against edits_seen_through_utc; "
                                       "hibernate keeps an instance a live sync writes; run_all exports "
                                       "DEEPMAP_AUTO_SYNC=0"),
        # macOS, fixed before the first Mac run (.github/workflows/macos.yml + tests/live_macos_e2e.py)
        ("macos_java_intel_gates", "macOS prerequisites: the Java preflight on a Mac (Apple's /usr/bin/java stub never "
                                   "run, a registered 21/17 used and handed to Neo4j as JAVA_HOME, JAVA_HOME respected) "
                                   "and Intel / Rosetta Pythons refused before setup downloads; Windows / Linux texts "
                                   "unchanged"),
        ("auto_sync_macos_memory_gates", "auto-sync on macOS: _memory reads vm_stat's available memory (free + inactive "
                                         "+ speculative pages x the header's page size; None on anything malformed), "
                                         "so the low-memory floor and pre-embed wait fire on a Mac; Linux and Windows "
                                         "readings unchanged"),
        ("macos_appledouble_gates", "macOS metadata (AppleDouble ._ twins, __MACOSX/) is never source: the shared "
                                    "predicate and its mirrors, the ast walk / graph / incremental cache, the verdict "
                                    "census, framework_edges, the recognizer walk, graphify_extract's lists; clean "
                                    "trees unchanged"),
        # C# field report (2026-10-28): properties, constructors, call sites, honest impact answers
        ("impact_csharp_honesty_gates", "impact: C#-aware notes (extensions in the one count query, the "
                                        "typed_call_langs + typed_call_narrowed meta read, one note on a "
                                        "zero-row answer, the property clause in every C# note, MCP + CLI "
                                        "relay)"),
        ("impact_call_line_gates", "impact: call_line from :CALLS r.line (min() per caller grouping in the walk and "
                                   "query_callers, rows, the MCP description budget, CLI format with the "
                                   "hop-aware label: 'call site' at hop 1, 'first call on the path' beyond, "
                                   "mutants)"),
        ("csharp_members_gates", "C# member nodes: computed properties and the first instance constructor per "
                                 "owner as :Method, 0.8.39 record/struct/enum owners, the call_by_name name "
                                 "guard, DEEPMAP_CSHARP_MEMBERS=0 byte identity, real engine vs fixture"),
        ("map_graph_call_lines_gates", "map_graph(call_lines=True): per-caller first call lines, metadata only "
                                       "(the tuples equal the frozen call_lines=False output), both engines"),
        ("sync_call_lines_gates", "sync: _call_lines key-then-name lookup, r.line written beside r.confidence "
                                  "(never in MERGE; a line-less push sends the old params), ast lineno, cache v2"),
        ("sync_typed_meta_gates", "sync: :_DeepMapMeta.typed_call_langs after a push that wrote nodes (sorted, [] "
                                  "when none ran, removed on a failed write)"),
        ("sync_relabel_twin_gates", "sync: a push removes every node whose key this extraction gives another "
                                    "label (the 0.8.39 C# record / struct method :Function -> :Method twin) "
                                    "and every stale :Class shell of a pushed class, on every push whatever "
                                    "the prune decides; one key-only scan per label plus a confirming seek "
                                    "for the candidates only, before any write, relationships first, "
                                    "reported (stderr, sync_last.json relabeled), full-text index re-created, "
                                    "real engine both orders, census cost, mutants"),
        ("typed_calls_cs_gates", "typed-receiver call linking, the C# reader (receiver_facts_csharp): every CQRS "
                                 "shape and must-not to an exact key and grade on both engines' captures, interface "
                                 "dispatch 0/1/2-4/>4 implementers, test implementers for test callers only, "
                                 "DEEPMAP_TYPED_CALLS_IFACE, non-C# callers unchanged, mutation teeth"),
        ("typed_calls_cs_fuzz_gates", "typed-receiver call linking, C# reader hardening (seeded programs + byte "
                                      "mutations: never raises, no edge without a real call)"),
        ("test_rule_mirror_gates", "test-rule mirrors: every fallback copy of sync._is_test_path (enrich, "
                                   "graphify_extract, typescript_edges, verdict) equals the rule, and the verdict "
                                   "under DEEPMAP_EXCLUDE_TESTS drops what THE rule drops (.NET test projects)"),
    ):
        # No exists() guard: a registered gate that has gone missing must turn the run RED, not vanish.
        results.append((_name, run(_desc, [f"tests/{_name}.py"])))
    results.append(("live_per_project_isolation",
                    run("LIVE per-project Neo4j isolation (opt-in DEEPMAP_LIVE_TEST=1: 2 real instances, "
                        "isolated graphs, stop-one-serves-other, heal; SKIPs cleanly without the flag / Java)",
                        ["tests/live_per_project_isolation.py"])))
    results.append(("retrieval_log_gates",
                    run("RM-1 retrieval-log foundation (opt-in, zero-egress, fusion-lossless capture keystone)",
                        ["tests/retrieval_log_gates.py"])))
    results.append(("retrieval_access_hook_gates",
                    run("RM-1 PostToolUse access-capture hook (opt-in, never disrupts tool flow, zero-egress)",
                        ["tests/retrieval_access_hook_gates.py"])))
    results.append(("presearch_nudge_gates",
                    run("Track-D pre-search nudge hook (PreToolUse Grep|Glob; marker-gated, throttled, opt-out, never blocks)",
                        ["tests/presearch_nudge_gates.py"])))
    results.append(("postedit_syncflag_gates",
                    run("Track-D post-edit sync-flag hook (PostToolUse Edit|Write|…; marker-gated, debounced, opt-out, never blocks)",
                        ["tests/postedit_syncflag_gates.py"])))
    results.append(("reliability_copy_gates",
                    run("Track-D reliability copy honesty (product page 'seven layers' = shipped set; hook claims backed by real files)",
                        ["tests/reliability_copy_gates.py"])))
    results.append(("tune_weights_gates",
                    run("RM-1 offline weight tuner (real _weighted_rrf replay; threshold/regularize/floor/auto-revert)",
                        ["tests/tune_weights_gates.py"])))
    results.append(("tune_cli_gates",
                    run("RM-1 `deepmap tune` CLI (offline, applied:false G4-gated, no Neo4j/spend)",
                        ["tests/tune_cli_gates.py"])))
    results.append(("rm1_fuzz_gates",
                    run("RM-1 offline-pipeline fuzz (300 seeded malformed logs -> degrade, never crash)",
                        ["tests/rm1_fuzz_gates.py"])))
    results.append(("rm1_signals_gates",
                    run("RM-1 richer signals (graded relevance, skip-above negatives, intent buckets, v1 back-compat)",
                        ["tests/rm1_signals_gates.py"])))
    results.append(("tune_stats_gates",
                    run("RM-1 metrics + validation units (nDCG/MAP, negatives-aware, k-fold CV, bootstrap significance)",
                        ["tests/tune_stats_gates.py"])))
    results.append(("tune_profiles_gates",
                    run("RM-1 comprehensive tuner (per-intent profiles, significance gate, coord/grid, RRF_K; applied:false)",
                        ["tests/tune_profiles_gates.py"])))
    results.append(("learned_fusion_wiring_gates",
                    run("RM-1 gated live-wiring (default-off no-regression, 4-condition AND-gate, retrieval.py frozen)",
                        ["tests/learned_fusion_wiring_gates.py"])))
    results.append(("benchmark_weight_ab_gates",
                    run("RM-1 G4 benchmark bridge (learned-vs-equal A/B reuses score_completeness; evidence gates apply)",
                        ["tests/benchmark_weight_ab_gates.py"])))
    results.append(("rm1_operability_gates",
                    run("RM-1 operability (log rotation keeps-newest, opt-in sync auto-tune applied:false, zero-egress)",
                        ["tests/rm1_operability_gates.py"])))
    results.append(("learning_loop_gates",
                    run("RM-1 learning-loop mode (DEEPMAP_LEARNING master switch implies logging+auto-tune, "
                        "NEVER live apply; one doctor/status line)",
                        ["tests/learning_loop_gates.py"])))
    results.append(("learning_enable_gates",
                    run("RM-1 dogfood friction-reducer (one-command `learning --enable` + 'X/150 labeled "
                        "retrieval logs' accrual line in status/doctor; ranking untouched)",
                        ["tests/learning_enable_gates.py"])))
    results.append(("drift_guard_gates",
                    run("RM-1 drift guard (newest-slice re-validation of stored weights; significant "
                        "degradation auto-reverts to equal, loudly, applied true->false only)",
                        ["tests/drift_guard_gates.py"])))
    results.append(("learned_prior_gates",
                    run("RM-1 cold-start seeding (user-level prior under DEEPMAP_HOME: export wins only, "
                        "pooled+clamped at read, recommendation/warm-start only — never live)",
                        ["tests/learned_prior_gates.py"])))
    results.append(("insights_gates",
                    run("`deepmap insights` local read-only log summary (arm-win/zero-result/abandonment "
                        "definitions, n-honesty refusals 150/40, redaction, dry-run writes nothing, fuzz)",
                        ["tests/insights_gates.py"])))
    results.append(("access_prior_gates",
                    run("D1 OFFLINE access-prior re-ranker (beta=0 parity keystone, leak-gated k-fold, "
                        "circularity named, experiments.access_prior applied:false — never live)",
                        ["tests/access_prior_gates.py"])))
    results.append(("graph_signal_ab_gates",
                    run("D2 OFFLINE framework-edge graph signal (lambda=0==CALLS-only keystone, injected "
                        "query fn, replay_fidelity/qrel_coverage honesty, applied:false — never live)",
                        ["tests/graph_signal_ab_gates.py"])))
    results.append(("confidence_centrality_ab_gates",
                    run("D3 OFFLINE confidence-weighted centrality (P0-2: gamma=(1,1,1)==count(caller) "
                        "keystone, injected query fn, applied:false — live _arm_graph_weighted untouched)",
                        ["tests/confidence_centrality_ab_gates.py"])))
    results.append(("instance_state_gates",
                    run("shared .deepmap/instance.json writer (merge preserves keys; experiments.* top-level, "
                        "applied FORCED false, isolated from live ranking)",
                        ["tests/instance_state_gates.py"])))
    results.append(("claude_model_arm_gates",
                    run("--claude-model plan-based Claude arm dispatch (Sonnet-5 @ ~$0; additive, no-regression)",
                        ["tests/claude_model_arm_gates.py"])))
    results.append(("enrich_core_gates",
                    run("opt-in LLM enrichment core (span reader, content-hash keying + change-detect, "
                        "sidecar confine/resume/corrupt-tolerance, cap/spend, anti-hallucination; "
                        "scripted claude, no Neo4j/network/spend)",
                        ["tests/enrich_core_gates.py"])))
    results.append(("enrich_sync_seam_gates",
                    run("enrich extractor-agnostic body_summary override (graphify- + ast-shaped nodes; "
                        "fresh-hit vs stale-miss vs no-sidecar; opaque key untouched)",
                        ["tests/enrich_sync_seam_gates.py"])))
    results.append(("enrich_span_gates",
                    run("non-Python enrich span boundedness (F10e: def-bounded window, not fixed 80 lines; "
                        "cap honored; Python exact-ast path unchanged)",
                        ["tests/enrich_span_gates.py"])))
    results.append(("enrich_cli_gates",
                    run("`deepmap enrich` CLI (--status/--dry-run/--deterministic no-spend; LLM path "
                        "refuses without opt-in + --confirm-spend)",
                        ["tests/enrich_cli_gates.py"])))
    results.append(("enrich_ab_gates",
                    run("enrichment A/B bridge (reduces to weight_ab.deterministic_ab; ships-verdict = "
                        "concept-lift + no-regression + significant; --selftest no spend)",
                        ["tests/enrich_ab_gates.py"])))
    results.append(("set_initial_password_gates",
                    run("provisioner Windows metacharacter-password guard (fail-loud reject vs silent "
                        "corruption; dev default + spaces/quotes still run)",
                        ["tests/set_initial_password_gates.py"])))
    results.append(("cli_error_handling_gates",
                    run("CLI friendly DB-error handling (search/impact/status: Neo4j-down -> clear "
                        "message + non-zero, no traceback; scoped so a KeyError still escapes)",
                        ["tests/cli_error_handling_gates.py"])))
    results.append(("empty_query_guard_gates",
                    run("empty-query boundary guard (P2: MCP+CLI reject empty/whitespace before Neo4j; "
                        "retriever byte-frozen)",
                        ["tests/empty_query_guard_gates.py"])))
    results.append(("impact_dedup_gates",
                    run("deepmap_impact visually-identical-row de-dup (full-dict collapse, distinct rows "
                        "kept, degrade path deduped, query_impact still ranking-free)",
                        ["tests/impact_dedup_gates.py"])))
    results.append(("configure_neo4j_atomic_gates",
                    run("configure_neo4j atomic conf edit (reentrancy-proven single block under a raced "
                        "provision; idempotent + I/O-guarded)",
                        ["tests/configure_neo4j_atomic_gates.py"])))
    results.append(("sync_manifest_fallback_gates",
                    run("non-git provenance content-hash fallback (populated non-git tree -> sidecar; "
                        "deterministic + content-sensitive; git path + benchmark fail-closed unchanged)",
                        ["tests/sync_manifest_fallback_gates.py"])))

    # 3) the benchmark harness dry-run, if present + it exposes a self-test flag
    bench = ROOT / "benchmark" / "run_benchmark.py"
    if bench.exists():
        flag = selftest_flag(bench)
        if flag:
            results.append((f"benchmark dry-run ({flag})",
                            run("benchmark harness dry-run", ["benchmark/run_benchmark.py", flag])))
        else:
            print("\n----- benchmark harness -----\n  present but exposes no "
                  "--selftest/--dry-run flag; skipped (compile-checked above).")
            results.append(("benchmark dry-run", True))  # not a failure
    else:
        print("\n----- benchmark harness -----\n  benchmark/run_benchmark.py absent; skipped.")

    # drift lock: every tests/*_gates.py must be registered above (a new gate that CI never runs is
    # indistinguishable from a passing one — 2026-09 review finding)
    _unrun = sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / "tests").glob("*_gates.py")
                    if p.relative_to(ROOT).as_posix() not in _RAN_SCRIPTS)
    if _unrun:
        print("\n----- unregistered gate files -----\n  [FAIL] not run by run_all: " + ", ".join(_unrun))
    results.append(("all *_gates.py registered", not _unrun))

    # summary
    print("\n" + "=" * 72)
    passed = sum(1 for _, ok in results if ok)
    for name, ok in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    allgreen = passed == len(results)
    print(f"\nRESULT: {'GREEN' if allgreen else 'RED'} — {passed}/{len(results)} checks passed.")
    return 0 if allgreen else 1


if __name__ == "__main__":
    raise SystemExit(main())
