# build — Agent Context (nested; the root CLAUDE.md also applies)

The DeepMap product: 43 modules here + `hooks/` (~68.2k lines). The four call-linking modules (`typed_calls.py`,
`receiver_facts_py.py`, `receiver_facts_ts.py`, `py_import_resolver.py`; ~8.5k lines) belong to S3 sync.
The modules fall into seven slices:
S1 provision (`provision.py`, `deps_bootstrap.py`) · S2 routing + MCP (`port_registry.py`, `_mcp_transport.py`, `mcp_bounds.py`, the two
servers, `cypher_readonly.py`) · S3 sync (`sync.py`, `sync_status.py`, `auto_sync.py`, `extract_cache.py`, `indexes.py`,
`embeddings.py`, `summarize.py`) · S4 extraction +
honesty (`graphify_extract.py`, `graphify_case_shim.py`, `verdict.py`) · S5 retrieval + CLI (`retrieval.py`,
`deepmap_cli.py`) · S6 typed edges + enrichment (`framework_edges.py`, `edge_resolve.py`, `language_recognizers.py`,
the five `*_edges.py` recognizers: ruby_rails, csharp, java, go, typescript; `enrich.py`) · S7 hooks + RM-1 (`hooks/*`, `retrieval_log.py`, `tune_weights.py`,
`learned_fusion.py`, `learning_loop.py`, `instance_state.py`, `access_prior.py`, `graph_signal_ab.py`,
`learned_prior.py`, `insights.py`, `confidence_centrality_ab.py`).

## Critical to know (non-obvious; in addition to the root file)
- In provision, read `NEO4J_URI` only AFTER `_resolve_ports` / `apply_ports_for` has run: the operator escape hatch is
  `_USER_SET_NEO4J_URI`, captured once at import, and a project with no route gets `bolt://127.0.0.1:0` — check
  `neo4j_route_problem()` first. — `build/provision.py:102-115`, `:2990`
- Indexes are created BEFORE the push and awaited (120 s) AFTER it; a timeout returns False after nodes and edges were
  already written and skips embeddings and the manifest. — `build/sync.py:4789-4799`, `:4893-4895`
- Every edge is ANCHORED at one endpoint (`:CALLS` at the caller, a typed edge at its emitted src, `:IMPORTS` at the
  importer) and sync prunes + re-creates per anchor group. `CASCADES` (the only `REVERSED_EDGE_TYPES` entry) is stored
  dependent → principal and anchored at its TARGET, the principal — not always the declaring node (a GORM belongs-to is
  emitted principal-first) — so "a node's outgoing edges" is the wrong mental model for it.
  — `build/edge_resolve.py:73`, `:351-364`, `:681-683`, `:777-783`; `build/sync.py:974-976`
- `enrich.apply_sidecar` runs before the recognizer merge, so recognizer-supplied nodes are never enriched. `enrich` and
  `--dry-run` re-run the full `_rebuild_code` (a real graphify extraction) while holding the project's per-root sync
  lock; a held or unopenable lock is a REFUSAL (nothing extracted or spent; the CLI exits 4), and a sync that finds enrich holding
  it queues a re-run. — `build/sync.py:4715-4721`; `build/enrich.py:544-561`; `build/deepmap_cli.py:2063`
- The CLI retries a read exactly once, only on a dropped connection, never for `sync`; `deepmap search` first asks this
  project's embed server for the query vector (`DEEPMAP_CLI_EMBED_SERVER=0` disables), falling back to an in-process
  model. — `build/deepmap_cli.py:2586-2655`, `:1208-1341`
- A cold-start seed prior (`--seed-from-prior` / `DEEPMAP_SEED_PRIOR`) is coordinate descent's start AND the tuner's
  tie-break anchor — among equally-scoring candidates the one closest to the seed wins, which is how the default GRID
  optimizer honours it; it never out-scores the data. `tune --apply` accepts benchmark evidence only when its
  `weights_fingerprint` matches the current learned weights. — `build/tune_weights.py:335-341`, `:573-579`, `:642-646`, `:753-759`
- `.deepmap/instance.json` has one writer path: `instance_state.update_instance` (cross-process lock + atomic replace;
  an unparseable file is REFUSED, never wiped). Never read-modify-write it directly. — `build/instance_state.py:234-241`
- The recognizers honour sync's exclusion predicate (vendor/generated dirs, `.gitignore`, and — like graphify — any
  `worktrees/` directly inside a dotted dir, e.g. Claude Code's `.claude/worktrees/`): every walk gets it, and a
  finalize pass receives it when it declares an `exclude` keyword (and the walk's file list when it declares `files`);
  the walk (`walk_files`) prunes ignored dirs, and a `"/name"` ignore entry applies only in the root or a package
  root — a new recognizer that reads files itself must honour the predicate too.
  — `build/language_recognizers.py:276-338`, `:367-384`
- The SessionStart heal starts DeepMap under `DEEPMAP_PYTHON`, else the interpreter setup recorded in
  `<DEEPMAP_HOME>/run/setup-python-<root id>.json`, else the hook's own Python. The hook mirrors port_registry's
  setup-python functions (no port_registry import on that path) — change both; a gate locks the mirror.
  — `hooks/deepmap_hook_state.py:544-568`; `build/port_registry.py:2157-2159`
- The git post-commit block never passes `--stale-cleanup`, so commit-triggered syncs never prune deleted nodes (setup
  syncs with `cleanup_stale=True`); setup never installs the hook, the installers do by default.
  — `build/provision.py:6834-6850`, `:6321`
- Adding an ORM/framework edge type or recognizer touches lockstep lists that gates compare (`ALLOWED_EDGE_TYPES`, each
  entry's `edge_types` / `impact_edge_types`, `retrieval.DEFAULT_IMPACT_EDGE_TYPES`, retrieval's mirror), and a new
  edge type must also be named in `deepmap_cypher`'s description (`tests/cypher_tool_gates.py`).
  — `build/framework_edges.py:77-89`; `build/language_recognizers.py:110-115`; `build/retrieval.py:567-570`;
  `build/deepmap_search_server.py:531-548`
- `/deep-map:setup`'s own environment (`deps_bootstrap.py`, stdlib only) runs only from the `__main__` blocks of
  `provision.py` and `deepmap_cli.py`, never on import, so in-process callers and hermetic gates never trigger it.
  Remedies for a package the running Python lacks come from ONE rule, `deps_bootstrap.setup_remedy` (doctor, verdict,
  graphify_extract): they say `/deep-map:setup`, and a gate asserting the pip form must set `DEEPMAP_MANAGED_VENV=0`
  (CI installs no ML stack). Never `--clear` the shared venv in place: on Windows it half-deletes an environment a
  running server holds; replacement happens beside it, only while `_processes_using` shows nothing running from it.
  — `build/deps_bootstrap.py` (`ensure_env`, `_replace`, `_settle_failed`, `setup_remedy`, `_interpreter_present`)
- TypeScript resolution rides on `TypedEdge.scope` and the recognizer's post-pass: `typescript_edges` emits `module:`
  / `spec:` import scopes (+ `#` notes; a `spec:` specifier percent-encodes its own `%` and `#`); its `finalize`
  places each import in the tree (`_Modules` / `_rescope` / `_place`: tsconfig paths / baseUrl, package.json
  `imports`, workspace packages, barrels, default exports) and rewrites it to `exact:` / `pref:` / `modsuffix:` with
  a line hint, or drops the edge; `edge_resolve` reads them and never binds an import-scoped dst in the declaring file.
  A refactor that drops the scope or skips the post-pass brings back the monorepo AMBIGUOUS fan-out (nestjs/nest
  samples: 718 rows; zod: 166) and binds package types to same-named repo classes. — `build/typescript_edges.py`
  (`_Modules`, `_rescope`, `finalize`); `build/edge_resolve.py` (`_pref_keys`, `_line_hint`, `_at_line`)
- `cypher_readonly.py` is stdlib only and side-effect-free on import (the neo4j driver is imported lazily), so the MCP
  server, the CLI parser and the hermetic gates load it freely. Its allowlists are the safety surface: the statement
  must START with a `LEADING` clause, every CALL names a `PROCEDURES` entry (or opens a `CALL {…}` subquery), SHOW
  lists only `_SHOW_OBJECTS`, a namespaced function only `_FUNCTION_NAMESPACES`, and later clauses are checked against
  `DENIED` after NFKC + case folding. Add a procedure only if it is read-only by itself (READ mode lets IO through).
  The lexer must agree with Neo4j 5.26 on where every string and comment ends (`_ESCAPE_RE`, `_ODD_ESCAPE_RE`,
  `_ODD_BREAKS`): a reader that disagrees is itself the bypass. The caps (`LIMIT_MAX`, `MAX_BYTES` measured by `_size`
  as FastMCP sends a row, `VECTOR_MIN_LEN`) live here so the tool and the CLI never drift.
  — `build/cypher_readonly.py:55-125`, `:391`, `:570-593`, `:656-673`
- `sync_status.py` is THE module for "who holds `.deepmap/sync.lock` and is it alive" (`sync_running.json`, pid +
  start stamp), "how did the last pass end" (`sync_last.json`, rc = what `sync._main` / `cmd_sync` return), preempt
  records, and the graph-shaping env lists. Every `DEEPMAP_*` name read by a build/ module that sync / enrich /
  sync_status reach (imports at any level, importlib / `_load` / `_lazy`, or run by path) must be in exactly one of
  `GRAPH_SHAPING_ENV` / `NON_SHAPING_ENV` / `INTERNAL_ENV` / `ROUTE_ENV` — `tests/sync_status_gates.py` section G derives
  that set and stops only at its named `_NOT_PIPELINE` modules; a new shaping knob also needs its canonical rule in
  `canonical_env_value`. A background refresh never claims a user's queued request (`sync._peek_rerun_request` /
  `_is_user_request`; outcome `user-request-pending`, rc 4), records an enrich-only yield as busy-enrich
  (`sync_status.yield_fields`, THE rule), and refuses an unowned non-empty graph (refusal_kind `unowned`). A config
  file a recognizer starts reading must be added to `sync._GRAPH_CONFIG_NAMES` (section L2 locks the `*.json`
  literals). — `build/sync_status.py:110`, `:158-195`, `:671`; `build/sync.py:4441-4444`, `:5246-5259`
- `sync.sync(prune_policy=None|'none'|'evidence'|'full', unattended=UnattendedControl|None)`: a claimed re-run request
  max-merges the policy (none < evidence < full). An unattended request always writes `cleanup_stale` false, because an
  older holder ORs only that field. The evidence prune (`_plan_prune_evidence`) is a subset of `_plan_prune` and never
  touches the verdict. A user's sync finding a live background holder preempts it (`sync_status.preempt_wait`); any
  other holder gets the old queue path, byte for byte. — `build/sync.py:3877`, `:4000`, `:5196-5220`, `:5550`
- `auto_sync.py` is stdlib-only at module level because the hooks import it on deepmap tool calls: `sync_status`,
  `sync` (only after R1/R5 set the offline env and replayed the recipe), `port_registry`, `graphify_extract`,
  `instance_state` and `hooks/deepmap_hook_state.py` load lazily, and it never calls `deps_bootstrap`. A run is the
  R1–R17 state machine in its docstring; `_map` turns each sync outcome into one autosync outcome
  (`user-request-pending` → `skipped:user-request`, an `unowned` refusal → `skipped:unowned`, neither backed off; a
  `git-busy` or `recipe:*` pre-write veto → `not-pushed:git-busy` / `not-pushed:recipe:*`, the recipe one backed off;
  any other veto → `not-pushed:veto`) and `_WHY` holds each skip's words and suggestion — a new sync outcome needs
  both, plus an `auto_sync_runner_gates` case. — `build/auto_sync.py:1-75`, `:157`, `:1886-1960`
- `typed_calls.py` (the typed-receiver core) and its readers `receiver_facts_py.py` / `receiver_facts_ts.py` (+
  `py_import_resolver.py`) import only the stdlib at module level: `graphify_extract`, `edge_resolve`, `sync`,
  `typescript_edges` and the readers load lazily, so the core stays outside build/'s lazy-import cycle. tree-sitter is
  OPTIONAL (graphifyy's dependency, never DeepMap's) and loaded lazily: without a grammar the TS/JS reader's canary
  fails and only that language is disabled (`langs_disabled`), its tuples unchanged. The IR (`FileFacts`, `Scope`,
  `Site`, `Binding`, `TypeRef`, ...; `IR_VERSION`) is FROZEN: Phase A, `Reader.facts(rel, source)`, is a pure
  function of one file and says only what the source writes; Phase B, `_Linker`, makes every verdict, merge and grade.
  `link(..., sources={rel: _Source})` takes the extraction's own reader (sync hands it over through the one-shot
  `_TypedNote`), so no file is read or parsed twice; without it the pass reads the files itself (same result,
  slower). Every CallKey of the pass is made in `_call_key` (→ `graphify_extract.call_key`); a reader never makes one,
  and nothing of the pass goes through `extract_cache` (a cached CallKey comes back a plain str). A new language is a
  `LanguageSpec` entry plus a `receiver_facts_<lang>.py` exposing `make_reader(spec)`; the wave-2 entries (go, java,
  csharp, rust, ruby) name readers that do not exist yet (`no reader`), and each needs its own measured decision before
  any default. — `build/typed_calls.py:1-220`, `:627-654`, `:1703`, `:3731`; `build/receiver_facts_ts.py:202-243`;
  `build/sync.py:2853-2884`, `:2979`
