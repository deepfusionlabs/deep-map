# tests: Agent Context (nested; the root CLAUDE.md also applies)

Every `*_gates.py` suite is fanned out by `tests/run_all.py` (each gate its own subprocess, 300 s budget); STAGE-0
oracles and opt-in live suites run outside it.

## Critical to know (non-obvious, load-bearing)
- Register every new `tests/*_gates.py` in `tests/run_all.py` (a literal `run()` line or the round tuple list): an
  unregistered gate turns the suite RED, and so does a registered gate whose file was deleted (only a gate listed in
  `INTERNAL_ONLY` is skipped when this checkout lacks it). See `tests/run_all.py:57`, `:76-83`, `:879`, `:1026-1032`
- `tests/mcp_http_smoketest.py` is product runtime: `provision.verify_mcp` runs it at setup and parses its exit codes
  (2 = deadline, 3 = wrong embedding size) and its `NOTE:` / error wording: keep that output contract.
  See `build/provision.py:7321-7413` (`verify_mcp`); `tests/mcp_http_smoketest.py:71-118`
- Never read "pre-fix" text with `git show HEAD:` (it becomes the fixed text once committed; CI is a depth-1 checkout):
  pin it as a committed fixture. The durable locks are content hashes: the 20-symbol sha256 table for the ranking
  functions (re-lock a symbol only with an AST-equality proof, as `DeepMapRetriever.impact` was) and the whole-file
  sha256 of `build/learned_fusion.py`. See `tests/fts_r7_gates.py:586-600`; `tests/o12_learned_fusion_lock_r283_gates.py`
- Real-graphify sections skip silently when graphify is not installed, and the spawn/case trap only shows above the
  engine's 20-file parallel threshold, which is why the gate runs the real engine on 25 files; a stub or a small sample
  passes either way. See `tests/case_insensitive_ext_gates.py:373-431`
- Engine-sensitive expectations are written per measured engine version with dual-end fixtures: CI installs graphify at
  the top of the pinned range (0.8.51) while the dev box runs 0.8.39.
  See `tests/g5_member_targets_r11c_gates.py:371-411`; `.github/workflows/validate.yml:66-67`
- STAGE-0 live scripts need an explicit Neo4j target and WIPE it; use `tests/stage0_refreeze.py --work <fresh dir>
  --check` (throwaway instance, pinned corpus). A graph holding < 50 % of the frozen keys exits 2 (wrong graph), not 1.
  The committed oracles pin a commit of the development history: in a clone without it, `--check` cannot rebuild the
  corpus (re-freeze with `--freeze --sha <commit>` first) and `stage0_harness_gates` skips its corpus key check.
  See `tests/stage0_portable.py:198-278`; `tests/stage0_regression_check.py:239-256`; `tests/oracles/README.md`
- Gates that drive provision/setup/heal stay hermetic: temp `DEEPMAP_HOME`, stub `apply_ports_for` / `write_mcp_json`
  and the port/Bolt probes, and never call a real kill (on Windows `os.kill(pid, 0)` terminates the process); reuse
  `_Sandbox` / `_Linux`. See `tests/provision_round4_gates.py:217-257`, `:327-342`; `tests/sessionstart_heal_gates.py:37-43`
- The plugin ships NO root `.mcp.json` (gitignored; untracked with `git rm --cached .mcp.json`): setup's per-project
  `provision.write_mcp_json` is the only MCP wiring (gates that drive setup stub it: it writes into the project).
  `plugin_manifest_gates.gate_no_shipped_mcp_json` locks it (FAIL "run: git rm --cached .mcp.json" while git tracks
  one), and `mcp_transport_gates` / `port_registry_gates` / `benchmark_wiring_gates` lock the content
  `provision.write_mcp_json` writes, in a throwaway project. See `tests/plugin_manifest_gates.py`
  (`shipped_mcp_json_status`); `tests/mcp_transport_gates.py`; `tests/port_registry_gates.py`;
  `tests/benchmark_wiring_gates.py`
