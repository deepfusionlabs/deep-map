# DeepMap: contributor context

DeepMap is a Claude Code plugin: code RAG that reasons, not just retrieves. It builds a per-project code graph in a
local Neo4j database (typed nodes, `:CALLS`, `:IMPORTS` and 12 kinds of framework link, every link labelled
`EXTRACTED`, `INFERRED` or `AMBIGUOUS`), embeds a one-line intent summary of every function, method and class, and
serves Graph + Vector + FTS search, fused with Reciprocal Rank Fusion, over streamable-HTTP MCP: `deepmap_search`,
`deepmap_impact`, `deepmap_cypher` (read-only) and `deepmap_embed`. Around that: a provisioner, a CLI, eight
`/deep-map:*` commands, a reasoning skill and default-on hooks, including a background refresh of the graph at
session start. 25 programming languages are parsed, 8 of them with their own end-to-end test suites, and framework
wiring is read in 6 ecosystems.

Read `CONTRIBUTING.md` first (the quality bar, Conventional Commits, licensing of contributions).

Version numbers in code comments, test docstrings and gate names name internal pre-releases, not public versions:
the internal line had its own numbers, some of them at or above 0.5.0. The public line starts at 0.5.0, and its
versions are the ones in `CHANGELOG.md`. Round, finding and `ISS-` ids, and file names such as `*_r283_gates.py`,
name internal fix rounds. Keep them: they tie a check to the defect it locks.

## Layout

- `build/`: all runtime code. The provisioner (`provision.py`, `deps_bootstrap.py`), routing and MCP
  (`port_registry.py`, `_mcp_transport.py`, `mcp_bounds.py`, `deepmap_search_server.py`, `deepmap_embed_server.py`,
  `cypher_readonly.py`), sync (`sync.py`, `sync_status.py`, `auto_sync.py`, `indexes.py`, `embeddings.py`),
  extraction and the verdict (`graphify_extract.py`, `graphify_case_shim.py`, `verdict.py`), retrieval and the CLI
  (`retrieval.py`, `deepmap_cli.py`), typed edges (`framework_edges.py`, `edge_resolve.py`,
  `language_recognizers.py`, the `*_edges.py` readers), enrichment (`enrich.py`) and the opt-in call linker
  (`typed_calls.py`, `receiver_facts_*.py`, `csharp_msbuild.py`, `py_import_resolver.py`). Detail: `build/CLAUDE.md`.
- `hooks/`: `hooks.json` and the hook scripts; `deepmap_hook_state.py` is the module they share.
- `commands/`: the `/deep-map:*` slash commands. `skills/deep-map/SKILL.md`: the reasoning skill.
- `tests/`: the gate suites (`*_gates.py`), fanned out by `tests/run_all.py`; frozen ordering oracles under
  `tests/oracles/`; opt-in live suites. Detail: `tests/CLAUDE.md`.
- `benchmark/`: the with-vs-without harness (repos, questions, blind two-stage scoring, multi-LLM arms) and the
  call-linking pre-registration records under `benchmark/call_linking/`. Detail: `benchmark/CLAUDE.md`.
- `scripts/ml_stack_selfcheck.py`: the environment self-check setup and the installers run.
- `docs/`: `known-limitations.md`, `multi-language-support.md`, `testing.md`, `PLAYBOOK.md`, `RM1-ENABLE.md`.
- `.claude-plugin/` (`plugin.json`, `marketplace.json`), `install.sh` / `install.ps1`, `requirements.txt`, and the
  licence records `NOTICE`, `THIRD-PARTY-LICENSES.md`, `sbom.cdx.json`.

## Running the tests

- `python tests/run_all.py` runs the whole suite: no Neo4j, no network, no spend. It takes no flags (`--help` starts
  the whole suite). It must be GREEN before a pull request.
- One suite: `python tests/<name>_gates.py`. Each prints `[PASS]` / `[FAIL]` lines and exits non-zero on a failure.
- Python 3.10 or newer, with the packages in `requirements.txt`. Suites that run the real parsing engine skip when
  it is not installed.
- Live suites are opt-in (`DEEPMAP_LIVE_TEST=1`) and need a real Neo4j (Java 17 or 21). The STAGE-0 scripts WIPE
  the Neo4j they are pointed at: use `python tests/stage0_refreeze.py --work <fresh dir> --check`, which makes a
  throwaway instance. The committed oracles pin a commit of DeepMap's development history; in a clone without
  that commit, re-freeze them on a commit you have first (`tests/oracles/README.md`).
- The engine's output shape changes inside the pinned `graphifyy` range, so an engine-sensitive change must pass on
  both ends of it (the oldest and the newest release `requirements.txt` allows). CI runs the newest.
- `docs/testing.md` says what the suite and the live runs cover.

## Rules for tests

- Register every new `tests/*_gates.py` in `tests/run_all.py`. An unregistered gate turns the suite RED, and so does
  a registered gate whose file was deleted.
- RED before GREEN: a fix starts as a check that fails on the old code. Write plain `check(label, cond)` suites like
  the nearest existing one.
- Never read "pre-fix" text with `git show HEAD:` in a gate: it becomes the fixed text once committed, and CI checks
  out one commit only. Pin the old text as a committed fixture instead.
- Gates that drive provision, setup or heal stay hermetic: a temp `DEEPMAP_HOME`, stubbed `apply_ports_for` /
  `write_mcp_json` and port or Bolt probes, and never a real kill (on Windows `os.kill(pid, 0)` terminates the
  process). Reuse the existing sandboxes (`tests/provision_round4_gates.py`, `tests/sessionstart_heal_gates.py`).
- Never kill a process by a recorded pid without re-checking its creation time and command line: pids are reused.
- A stub hides what the real path does. When a check guards a download, a process or a file format, add one case
  that runs the real thing, and run a first-time setup on an EMPTY `DEEPMAP_HOME` before trusting it.
- Lock a heuristic with the shapes it must NOT match too, and diff its effect on real files with it on and off.
- `tests/run_all.py` sets `DEEPMAP_AUTO_SYNC=0` for every gate (the auto-sync gates set it per case), so no gate
  starts a background refresh.
- `tests/mcp_http_smoketest.py` is product runtime, not only a test: `provision.verify_mcp` runs it during setup and
  parses its exit codes (2 = deadline, 3 = wrong embedding size) and its `NOTE:` wording. Keep that contract, and
  never delete the file (setup would skip its MCP check and call it a pass).

## Invariants you must not break

**Retrieval and ranking**
- The ranking functions are hash-locked: `tests/fts_r7_gates.py` holds a sha256 of each ranking symbol in
  `build/retrieval.py` (`_weighted_rrf`, the `_arm_*` methods, `search`, `_hydrate`, `RRF_K`, `DEFAULT_ARM_WEIGHTS`,
  `VECTOR_INDEXES` and more). The file itself is not frozen. Re-lock a symbol only with proof that its behaviour is
  unchanged (an equal AST with the docstring stripped). `build/learned_fusion.py` is locked whole
  (`tests/o12_learned_fusion_lock_r283_gates.py`).
- Fusion is `_weighted_rrf`, implemented directly (no ranx), `RRF_K = 60`. Equal default arm weights make it exactly
  plain RRF; do not change the defaults until a benchmark calibrates them.
- `_build_fts_query` lower-cases a standalone `AND`, `OR`, `NOT` or `TO` (Lucene would read them as operators), and
  validates long queries against the index only when its direct caller is `_arm_fulltext` (found with
  `sys._getframe`). A wrapper or a refactor silently changes BM25 hits.

**Sync and the graph**
- The sync exit codes are a contract: `0` synced (or a re-run queued), `1` refused, failed or verdict FAILED, `3`
  Neo4j unreachable, `4` NOT PUSHED (the previous graph is kept and may be stale). The slash commands, the git hook,
  the benchmark, provision and the background refresh all read them. `SYNC_NOT_PUSHED_RC` exists in both
  `build/deepmap_cli.py` and `build/sync.py`, locked together by `tests/sync_timeout_r11_gates.py`.
  `provision.py setup` exits `0` (READY / DEGRADED), `1` (FAILED) or `4` (graph kept).
- The hooks recognise the CLI's background-refresh contract by TEXT (`_CLI_CONTRACT` in
  `hooks/deepmap_hook_state.py`): rename `--unattended` / `--session` or the sync subparser line and the hooks
  silently stop starting the refresh.
- A `:CALLS` callee is a node key only if it is a `CallKey`: test it with `is_call_key`
  (`build/graphify_extract.py`), never with `callee in node_keys`.
- With every call-linking flag off, the graph must stay byte-identical (`tests/typed_calls_push_gates.py`,
  `tests/precise_family_gates.py`). A default moves only through a pre-registered decision in
  `benchmark/call_linking/PREREG.md`; its section 8 is hash-locked by `tests/call_linking_harness_gates.py` and never
  changes.
- Sync rewrites edges per anchor in write transactions of at most 20,000 rows, and the budget counts the edges a
  transaction deletes as well as the ones it writes (`build/sync.py`). A re-sync that shrinks the edge set would
  otherwise delete everything in one transaction and roll back every run.
- Every extension the engine parses must be in `SOURCE_CODE_EXTS` or `EXCLUDED_BY_DESIGN_EXTS`
  (`build/graphify_extract.py`), and every extracted one needs a census language in `_SOURCE_EXTS`
  (`build/verdict.py`). Otherwise a language is silently dropped, or can never raise LOW COVERAGE. One the engine
  parses only with an optional grammar package DeepMap does not install (`.sql`, `.dm`) also needs its module in
  `verdict._OPTIONAL_PARSERS` (`tests/sql_parser_census_gates.py` locks it to the engine), or it raises a false LOW
  COVERAGE.
- The verdict counts ENGINE nodes only, through `verdict.note_engine_nodes`; a caller that skips it counts
  framework-reader shells as extraction.
- A new edge type touches five lists that gates compare: `ALLOWED_EDGE_TYPES` (`build/framework_edges.py`), each
  recognizer's `edge_types` / `impact_edge_types` (`build/language_recognizers.py`),
  `retrieval.DEFAULT_IMPACT_EDGE_TYPES`, retrieval's mirror, and the `deepmap_cypher` tool description
  (`tests/cypher_tool_gates.py`).

**The parsing engine**
- Never rename `import graphify`.
- The engine runs only through `_run_engine` (`build/graphify_extract.py`), as a guarded child process. From 20
  files up it starts spawn workers that re-import it fresh, so never patch it in-process only: go through
  `build/graphify_case_shim.py`'s import-time block and an environment variable. A small sample passes either way;
  test such a change on 25 files or more.
- Corrections may read only what every engine release in the pinned range writes, or the source itself.

**Routing, processes and the database**
- ONE routing rule: every consumer finds the project's Neo4j through `port_registry.resolve_neo4j_route`. Make URIs
  with `bolt_uri` (127.0.0.1: on Windows `localhost` costs about 2 s per connection). In CLI code never open a raw
  driver; `deepmap_cli._driver` does the routing and owner checks. "Same project" is `owner_is_this_project`.
- Process identity lives only in the owner files `<DEEPMAP_HOME>/run/<name>-<port>.owner`. A pid and a command line
  prove "a DeepMap server", never "this project's".
- The rule for restarting servers onto new code exists twice (`provision._code_restart_verdict` and
  `port_registry.code_restart_verdict`): change both.
- Windows background processes use `provision.WINDOWS_BACKGROUND_FLAGS`, never `DETACHED_PROCESS` (every console
  child would flash a window).
- A Neo4j store keeps its version: `port_registry.store_neo4j_version` is the rule and only `provision.upgrade_neo4j`
  moves a store. Raising `provision.DEFAULT_VERSION` needs the new archives pinned in `KNOWN_SHA256` (old pins kept)
  and `port_registry._BASE_NEO4J_DEFAULT_VERSION` equal to it.
- Every git call DeepMap's background work makes runs with `GIT_OPTIONAL_LOCKS=0`.

**Setup**
- `build/deps_bootstrap.py` is stdlib only and runs only from the `__main__` blocks of `provision.py` and
  `deepmap_cli.py`, never on import. Never pip-install into the user's Python. A directory is DeepMap's environment
  only when it holds `venv/deepmap-venv.json`.

**MCP and read-only Cypher**
- Every MCP tool goes through `mcp_bounds.bounded_tool` (off the event loop, deadlines, auth back-off). The servers
  use streamable HTTP because stdio hangs under Claude Code on Windows.
- `build/cypher_readonly.py`'s lexical validator is the ONLY guard for `deepmap_cypher` and
  `deepmap_cli.py cypher`. Neo4j 5.26 decodes `\u` escapes before it lexes, ends a `//` comment only at `\n` or `\r`,
  and still runs LOAD CSV inside a READ transaction. Never relax the validator because the transaction is read-only.

**Hooks**
- Every `hooks/hooks.json` command has the form `python -X utf8 "<hook>" ; python3 -X utf8 "<hook>" ; exit 0`. Never
  `||` or `&&`: Claude Code's Windows hook shell can be PowerShell 5.1, which can't parse them. Every registered hook
  drains stdin first and exits when it is empty, so exactly one interpreter runs it.
- Hooks run only where `.deepmap/graph_build.json` exists, emit only `additionalContext`, and exit 0 silently on any
  error. Unknown ports mean silence.
- `deepmap_toolresult_freshness._quiet_fast` is a pre-import copy of `_quiet_entry_holds`: any new input that can
  turn "nothing to say" into a note must be a file in `_QUIET_FILES` (`hooks/deepmap_hook_state.py`;
  `tests/auto_sync_hooks_gates.py` locks the copy).

**Files and copy**
- Repository files use LF line endings. Python's `write_text` on Windows writes CRLF: write with `newline=""` or as
  bytes, and check that `git diff --numstat` shows no whole-file churn for a small change.
- Claims must match the code: `tests/copy_honesty_scan.py` scans the shipped copy. Never publish a benchmark number
  before it is measured. Describe language coverage as 25 programming languages, 8 with their own end-to-end test
  suites, framework wiring in 6 ecosystems.
- A new runtime dependency goes into `requirements.txt`, both installers, `NOTICE`, `THIRD-PARTY-LICENSES.md` and
  `sbom.cdx.json` (`tests/compliance_gates.py`).

## Where to look

- `build/CLAUDE.md`, `tests/CLAUDE.md`, `benchmark/CLAUDE.md`: more load-bearing detail for each folder.
- `docs/PLAYBOOK.md`: reusable engineering patterns from building DeepMap.
- `docs/testing.md`: how DeepMap is tested.
- `docs/known-limitations.md`: what DeepMap does not do yet, with workarounds.
- `docs/multi-language-support.md`: what each language and framework reader covers.
- `SECURITY.md` (report vulnerabilities privately), `AI_POLICY.md`, `CODE_OF_CONDUCT.md`.
