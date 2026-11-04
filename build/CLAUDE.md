# build — Agent Context (nested; the root CLAUDE.md also applies)

The DeepMap product: 46 modules here + `hooks/` (~83.4k lines). The call-linking modules (`typed_calls.py`,
`receiver_facts_py.py`, `receiver_facts_ts.py`, `receiver_facts_csharp.py` with its `csharp_msbuild.py`,
`py_import_resolver.py`; ~17.5k lines) belong to S3 sync and `sql_reader.py` (DeepMap's own `.sql` reader, ~1.4k
lines) to S4.
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
- Adding a source extension takes up to six coordinated edits (root `CLAUDE.md`). The ones gates compare here:
  `graphify_extract.SOURCE_CODE_EXTS` or `EXCLUDED_BY_DESIGN_EXTS` (every suffix the engine parses), a census
  language in `verdict._SOURCE_EXTS`, and exactly one way this install turns it into nodes: an in-range engine
  dispatches it; or its grammar module goes in `graphify_extract.OPTIONAL_PARSERS` (the engine parses it only with an
  optional package DeepMap does not install: `.sql`, `.dm`); or it goes in `ENGINE_UNPARSED_EXTS` (no in-range engine
  parses it: `.pyi .cjs .pl .pm .r`); or in `ENGINE_EXT_SINCE` (only a later in-range engine does); or DeepMap reads
  it itself (`sql_reader.EXTS`). `tests/unparsed_census_gates.py` E2 locks `OPTIONAL_PARSERS` to the engine's extras
  and E3 locks every suffix to exactly one of these cases, on both engine ends; `tests/delphi_pascal_gates.py` F-H
  lock the lists against each other. `not_parsed_here()` is THE rule for which suffixes are uncounted and why
  (`package`, `unusable`, `no-parser`, and `off` for `.sql` while the reader is off and no engine grammar is usable):
  the verdict leaves them out of every coverage count (`verdict._uncounted_exts`), writes the manifest's
  `coverage.not_parsed`, prints one doctor / status line each, and the hooks relay them from the manifest alone
  (`deepmap_hook_state.not_parsed_gaps`; its `_GAP_LANGS` mirrors `_SOURCE_EXTS`' names, locked by
  `tests/sessionstart_notify_gates.py`). `engine_parseable_exts()` is what is left, for the zero-node rule. The
  grammar probe (`find_spec`, then `tree_sitter.Language(mod.language())`) runs once per process in `_parser_state`;
  never probe a grammar anywhere else. See `build/graphify_extract.py:152`, `:170`, `:4280`, `:4401-4410`, `:4497`,
  `:4529`; `build/verdict.py:40`, `:213`, `:1122`; `hooks/deepmap_hook_state.py:286`, `:357`
- `sql_reader.py` (stdlib + `summarize.humanize`, no engine import; I/O only in `read_file` and `Cache`) is DeepMap's
  own `.sql` reader. Only `graphify_extract._sql_supplement` runs it, AFTER the engine and only while
  `sql_reader_enabled()` (`DEEPMAP_SQL_DECLARATIONS`; default `SQL_DECLARATIONS_DEFAULT`, False until the G-SQL
  decision in `benchmark/sql_reader/` passes; it moves together with `sync_status._DEFAULT_OFF_KNOBS` /
  `_DEFAULT_ON_KNOBS`, locked by `tests/sql_reader_gates.py` L; read the switch only through that function, which
  reads it as sync_status reads a knob with that default). Its nodes: `sql:<rel>` (one `:Module` per file) and
  `sql:<rel>#<kind>:<qualified lower>` (`:Class` / `:Function`): keys with no line, so a line shift never re-keys,
  and `:` / `/` keep them apart from engine ids, `::` keys and `_supp_key`. Every one carries `call_by_name: False`
  (private, never pushed; `edge_resolve` and the typed-edge Resolver never bind it) and no call or import: SQL edges
  need their own decision record. With the switch off, and for a tree with no `.sql` file, `_sql_supplement` returns
  the very same objects (byte identity; sections G and I). Its file rule is `sql_source_files` (`git ls-files` in a
  work tree, every `.gitignore` / `.graphifyignore` level outside one, the engine's sensitive-file rule,
  `verdict._dir_ignore_rule`), the same on both engine ends: never call `detect._load_graphifyignore`, which differs
  between 0.8.39 and 0.8.51. The cache is `<graphify_out_dir(root)>/sqlcache/<sha256[:32]>-r<READER_REV>.json`
  (SqlFacts by content, `read` results only, unused entries pruned one exact name at a time): bump `READER_REV` with
  any change to `parse()` / `file_nodes()` output (still 1: no reader has shipped). The zero-node rule fails a result
  of reader nodes only when `_first_engine_source` finds a file the engine would have read, so `auto` still falls
  back for a C# repo whose engine produced nothing. With the reader on and the engine's grammar importable,
  `extract_with_report` sets `DEEPMAP_ENGINE_DROP_EXTS` (`INTERNAL_ENV`) so the case shim drops `.sql` from the
  engine's table at import time, BEFORE the odd-case aliasing; every other tree keeps the byte-identical
  `-m graphify` command. Its rules beyond the design text (the GRANT privilege-list rule, `TYPE BODY`, GO decides
  T-SQL first, dialect hints read on code only, COPY rows are data, DO blocks are read, q-quotes, placeholder schemas,
  stop words only for a bare one-part name, mysqldump view placeholders, concatenated names counted, never declared)
  are each locked by a case in its gate's sections A / B; a heuristic change needs its must-not-match shapes too
  (KL-mistake:008). See `build/sql_reader.py:73-77`, `:1262`, `:1343`, `:1414`; `build/graphify_extract.py:4406-4431`,
  `:5043`, `:5246`, `:5368`; `build/graphify_case_shim.py:104`, `:166`; `build/sync.py:6724-6727`
- `sync_status.GRAPH_REV` numbers changes to what DeepMap builds from UNCHANGED sources, which the source fingerprint
  cannot see. Raise it by one, with a scope, in the change that alters the default graph or the coverage verdict for
  existing graphs: `GRAPH_REV_SCOPE[r](manifest)` decides from the manifest alone whether revision r changes that
  graph (never a mass rebuild), and `GRAPH_REV_CHANGES[r]` says what changed in plain words (status, doctor and the
  runner's log quote it). Never lower it or reuse a number; an entry above `GRAPH_REV` is pre-registered and inert.
  `make_build_recipe` records it as `build_recipe.graph_rev` (absent or malformed reads as 0). `graph_rev_due` is THE
  rule (never raises; a raising scope means not due), read by `auto_sync.freshness` (stale `graph-rev` where the
  sources say fresh), `auto_sync.status_line` and `deepmap_cli._graph_rev_line` (status; doctor prints it before its
  coverage table). The background refresh rebuilds once per revision: a graph-rev pass that ends `synced` records
  `.deepmap/autosync.json` `graph_rev_rebuilt {rev, finished_epoch}` (lock holder only), and a graph due again for a
  revision already rebuilt (an older DeepMap rewrote the manifest, e.g. a stale post-commit hook) is the recorded skip
  `skipped:graph-rev-again` (rc 4, no backoff, deliberately not in `_REPEATING_SKIPS`), never a rebuild per commit; a
  second runner that finds the lock held queues no re-run for a graph-rev-only staleness. Revision 1 (the not-parsed
  census) is live; the G-SQL flip needs revision 2, scoped to "the tree holds `.sql` files". A gate fixture whose
  manifest has neither `build_recipe.graph_rev` nor a coverage block reads as due (revision 1 covers "unknown"): give
  it `"graph_rev": GRAPH_REV`. See `build/sync_status.py:946-1060`, `:1085`; `build/auto_sync.py:118`, `:242`,
  `:1294`, `:1321`, `:1628`, `:2078`, `:2380`; `build/deepmap_cli.py:969`; `tests/graph_rev_gates.py`
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
  literals; section L3 locks every MSBuild file name `csharp_msbuild` spells and every file it opens, matched by
  `_GRAPH_CONFIG_SUFFIXES`). — `build/sync_status.py:110`, `:158-195`, `:671`; `build/sync.py:4441-4444`, `:5246-5259`
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
- `typed_calls.py` (the typed-receiver core) and its readers `receiver_facts_py.py` / `receiver_facts_ts.py` /
  `receiver_facts_csharp.py` (+ `py_import_resolver.py`) import only the stdlib at module level: `graphify_extract`,
  `edge_resolve`, `sync`, `typescript_edges` and the readers load lazily, so the core stays outside build/'s
  lazy-import cycle. tree-sitter is OPTIONAL (graphifyy's dependency, never DeepMap's) and loaded lazily: without a
  grammar that reader's canary fails and only its language is disabled (`langs_disabled`), its tuples unchanged.
  The IR (`FileFacts`, `Scope`, `Site`, `Binding`, `TypeRef`, ...; `IR_VERSION`) is FROZEN: Phase A,
  `Reader.facts(rel, source)`, is a pure function of one file and says only what the source writes; Phase B,
  `_Linker`, makes every verdict, merge and grade.
  `link(..., sources={rel: _Source})` takes the extraction's own reader (sync hands it over through the one-shot
  `_TypedNote`), so no file is read or parsed twice; without it the pass reads the files itself (same result,
  slower). Every CallKey of the pass is made in `_call_key` (→ `graphify_extract.call_key`); a reader never makes one,
  and nothing of the pass goes through `extract_cache` (a cached CallKey comes back a plain str). A new language is a
  `LanguageSpec` entry plus a `receiver_facts_<lang>.py` exposing `make_reader(spec)`; the wave-2 entries go, java,
  rust and ruby name readers that do not exist yet (`no reader`), csharp has one (next entry), and each needs its own
  measured decision before any default. — `build/typed_calls.py:1-256`, `:548`, `:674-718`, `:1944`, `:5608`;
  `build/receiver_facts_ts.py:2565`; `build/sync.py:3079`, `:6290`
- The C# reader (`DEEPMAP_TYPED_CALLS=csharp`, opt-in, `.cs` only, D3-cs pending in
  `benchmark/call_linking/PREREG_CS.md`) keeps what the frozen IR cannot hold in a private `ff._cs` table (modifiers,
  parameter types, call arguments, variance, reassigned names, sealed classes, usings), and the core's C#-only rules
  (`LanguageSpec.lookup_rules`: `nested_types`, `overloads`, `virtual_dispatch`, `iface_dispatch`, `member_scope`, ...)
  run only for a spec naming them and read `_cs` through `cs_table` / `cs_member`: a reader without the table (the
  core gates' fake) keeps the earlier rules byte for byte. A scope maps to a node only at its own lines (`node_at`):
  overloads carry the first overload's line, so the two-scopes-one-node rule keeps them untyped; constructor scopes
  keep their own line, so only the one at the `Owner.Owner` node (`graphify_extract._cs_members`: the first instance
  constructor, a static one only when it is the only one) is a typed caller. Never give constructors the overload
  line (that drops the first constructor's calls too) or fold their bodies into one scope (it merges parameter
  lists). A weak verdict (an overload that fits a rival, a virtual or body-less abstract target) is never `EXTRACTED`.
  Signatures (overload rivals, override slots, interface mappings) compare through `_cs_tcmp`: only a PROVEN "one
  type" is the same signature and only a PROVEN "two types" (two keywords, two project classes, keyword vs project
  class, generic arity / array rank, by-ref, a type parameter vs anything else, a known value type's `X?` vs `X`) is
  another slot or a non-implementer; anything else is unknown, and unknown means ADD-only (no fan-out, no REPLACE).
  Never turn an unproven difference into False: the slot and interface rules treat False as proof and remove the
  engine's true tuple. One type: `System.Int32` / `int`, `nint` / `IntPtr`, `int?` / `System.Nullable<int>`, an
  alias and its target (the file's own `using X = ...` and the .csproj group's `global using X = ...`). A class type
  parameter's `T?` is `T` (an annotation) unless constrained `struct` / `unmanaged`. A base's type parameters take
  what the base list writes; the class's own stay open (never equal to a concrete type); the receiver's declared
  type arguments are not substituted. Two unqualified external names are one type only when their scopes' using
  levels prove it (`cs_scope_levels`, subset rule, the short `_CS_KNOWN_NS` table); a qualified one equals an
  unqualified one only when its namespace encloses or is imported by the scope. A private middle method never breaks
  an override slot; an override hit with a compatible-arity rival between it and the slot's declarer is unknown (C#
  drops overrides from the candidates). `T x = new T()` / `T x = new()` never rebound (no assignment, deconstruction
  element, ref / out argument, ref alias or `++` / `--`) is exact, like `var`. A static interface member with a body
  is an exact call. An interface or virtual call whose whole dispatch set is known REPLACEs the engine's tuples (a
  virtual one keeps only the unbroken slot's overrides; a production caller's drops a test-file implementer); above 4
  implementers, an unknown reach, or a class that may implement the interface unseen, it stays ADD-only.
  `DEEPMAP_TYPED_CALLS_IFACE` governs interface AND virtual dispatch and is read only through `_iface_read` /
  `_iface_modes` (an ON word = every mode, unknown tokens ignored and reported as `unknown_iface`): `sync_status`
  canonicalises the knob and `sync._typed_call_narrowed` stamps the meta node through them, so never re-parse it.
  Test implementations and overrides count only for test callers through `test_file` → `sync._is_test_path`. The
  D3-cs measurement kit keeps its own copy of that rule and refuses to run when the two differ: when the rule moves,
  change the kit's copy too (`tests/call_linking_harness_gates.py` L), and `tests/test_rule_mirror_gates.py` locks
  every fallback copy under `build/` (enrich, graphify_extract, typescript_edges, verdict).
  — `build/receiver_facts_csharp.py:1-110`, `:653-708`, `:1128`, `:1416`; `build/typed_calls.py:799-900` (`_CS_KNOWN_NS`,
  `_cs_tcmp`, `_cs_unq_same`), `:3616` (`cs_sig_norm`), `:3744`, `:3951`, `:4861`, `:4956`, `:5174`; `build/sync.py:718-726`
- A push replaces a node whose key the extraction now gives only OTHER labels (`_relabel_twins`, then
  `_delete_relabel_twins`, both before `_upsert_nodes`): MERGE matches label AND key while the stale prune matches key
  only, so a re-labelled key (graphify 0.8.39 C# record / struct methods moving `:Function` ↔ `:Method` with
  `DEEPMAP_CSHARP_MEMBERS`) would otherwise leave a twin no prune removes. The census is one key-only scan per label
  of `indexes.KEY_INDEX_LABELS`, diffed client side, then a confirming seek for the candidates only (never a
  label-less `MATCH (n {key})`, which scans every node per row). The same pass removes class shells: a stale `:Class`
  with the same file, name and line as a pushed `:Class` under another key (the 0.8.39 → member-owner upgrade). It
  runs before any write and whatever the prune decision, marks the full-text rebuild before deleting, and reports the
  push result's `.relabeled` (all twins; → `sync_last.json` `relabeled`) and `.shells` (the shell count).
  — `build/sync.py:3667-3700`, `:3765`, `:3813`, `:3842-3884`, `:3921`, `:6514-6531`
