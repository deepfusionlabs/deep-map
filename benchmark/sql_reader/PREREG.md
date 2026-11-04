# SQL declarations (G-SQL): pre-registration

Registered 2026-11-02, before any decision measurement of DeepMap's SQL reader. It records how the decision of design
`.ai/design/2026-11/sql/design.md` section 9 is made: whether `graphify_extract.SQL_DECLARATIONS_DEFAULT` (today
False: the reader runs only with `DEEPMAP_SQL_DECLARATIONS=1`) may become True. Two blocks below are hash-locked by
`tests/sql_prereg_gates.py`: "Protocol and criteria" (the method, the thresholds, the decision rule, with their
JSON) and "Registered corpus and queries" (the pinned sources, the truth expectations, the C6 targets and queries,
with their JSON). The scripts read every number from those JSON blocks at run time; they hold no second copy.

Rules. Thresholds, seeds, pins, truth expectations and queries are never edited after data. RESULTS.md and DECISION.md
(both in this folder) are written once, against this file. Exploratory numbers (section 2) are never decision data. A
change made BEFORE the first decision run is an amendment: dated, with its reason, recorded under "Amendments before
data" (that section is hash-locked too); the two blocks stay verbatim and an amendment governs the items it names.
After the first decision run nothing here changes: a different decision needs a new registration.

## 1. Code, engines and environment

- **Decision code identity:** the content hash of `build/*.py` of the tree the decision measures
  (`python benchmark/sql_reader/run_corpus.py codehash`: sorted by name, each file its name, a NUL, its bytes and a
  NUL; the call-linking kit's scheme). It is not fixed here: the decision runs only after the reader is wired into
  extraction (design Phase B; a dump of a tree that does not read SQL is VOID). Every result file records it, and
  `run_corpus.py decide` refuses results with more than one.
- **Kit hash:** `<to be filled>`: the `kit` hash `python benchmark/sql_reader/run_corpus.py codehash` prints (sha256
  over `benchmark/sql_reader/*.py` in byte order of their names, each contributing its name, a NUL, its bytes and a
  NUL; amendment A1). Filled once, before the first decision run; filling it is not an amendment, and after it the
  kit never changes for this registration. `decide` refuses any other kit, and an unfilled slot means STAYS OPT-IN.
- **Engine ends:** graphifyy 0.8.39 on the dev Python 3.13.14 (`python`: tree-sitter 0.25.2, networkx 3.6.1, neo4j
  6.2.0) and graphifyy 0.8.51 in a scratch venv on Python 3.13.14 (tree-sitter 0.25.2, networkx 3.7, neo4j 6.4.1):
  the two ends of the pinned range `graphifyy>=0.8.39,<0.8.52`. Any other build is not decision data.
- **Environment:** `DEEPMAP_AUTO_SYNC=0`, `HF_HUB_OFFLINE=1`; the engine pinned (`DEEPMAP_EXTRACTOR=graphify`) for
  every run through extraction, so a fall-back to `ast` is VOID, never silent; a throwaway `DEEPMAP_HOME` of at most
  64 characters (`dump` refuses a longer one, an unset one and `~/.deepmap`); never the 7687 / 7474 pair.
- **Corpus:** fetched by `benchmark/sql_reader/fetch_corpus.py fetch --out <dir>` into a directory outside this
  repository whose path is at most 32 characters (the longest path below it, in `teams-samples`, is 210 characters,
  so every file stays under Windows' 248 when the engine walks it), and `fetch_corpus.py verify --out <dir>` passes
  on every source before any run. `run_corpus.py` refuses a corpus that does not verify, and every output inside this
  repository.

## 2. Exploratory data (disclosed; never decision data)

The design's prototype 2 was explored on parts of this corpus before registration (design sections 3.1 to 3.3):
`microsoft/sql-server-samples` (databases and features) at the registered commit, the Oracle sample schemas and the
Mattermost PostgreSQL migrations (478 of the 495 registered `.sql` files) at the registered commits, Northwind and
Sakila (byte-identical to the registered files), and GitLab's `db/structure.sql` from master on 2026-11-01 (sha256
`95527f703e3165d330649a433fcbf47f468fed5f336f4f80667457d367d1d44f`, 2,983,928 bytes; the registered file, at the
2026-11-02 head, is a later version with the same line-start counts). Reported there: WWI SSDT 231/231, Northwind
36/36, Sakila 32/32, GitLab 2,222/2,222; precision audits of 60 declarations each with seeds 20261101 (57/60), 7
(58/60), 99 (60/60) and 2026 (60/60), every error shape fixed; linear time on the adversarial shapes. The product
reader (`build/sql_reader.py`) was then run on the same local copy during its development round (2026-11-02: 1,574
files, 23.8 MB, the exact-truth sets 36 / 32 / 2,222, and a 40-declaration read with seed 20261102). None of those
seeds is a decision seed; the EF repository, the C6 targets and the C6 queries were chosen and written before any
DeepMap run on those trees. The decision is re-earned on the decision code identity.

## 3. Files and commands

- `fetch_corpus.py`: `fetch` / `verify` (section 5's sources, byte-verified), `select-ef` (section 5.2's choice).
- `run_corpus.py`: `truth`, `metrics` (C1, C3, the C2 pool), `sample` / `audit` (C2), `gate` (C3's gate run), `dump`
  / `parity` (C4, C5), `c6-draw` / `c6-capture` / `c6-score` (C6), `decide` (section 4.9), `codehash`.
- `tests/sql_prereg_gates.py`: the hash locks and the kit's own checks (offline).
- Never committed: the corpus, sheets and verdicts (they quote corpus source), dumps, captures. RESULTS.md records the
  numbers, item identities and verdicts; DECISION.md the decision.

## Amendments before data

Registered 2026-11-02 with none. A1 to A7 were made the same day, before any decision run, after an adversarial
review of the kit; none rests on decision data, and none changes a threshold. Each governs the items it names; the two
locked blocks stay verbatim. The JSON at the end of this section holds every value they add (`protocol` and
`registered` are laid over the two blocks' JSON, a list replacing a list), and the kit reads it at run time.

### A1. One measured identity, and the kit hash (4.2, 4.9)
- A run is identified by three hashes, each in the call-linking scheme (paths in byte order, each its path, a NUL,
  its bytes and a NUL): `code` (build/*.py, section 1), `reader_gates` (tests/sql_reader_gates.py: C3's gate run and
  C2's negative cases depend on it, and a C2 fix changes it together with the code) and `kit`
  (benchmark/sql_reader/*.py, what measures and scores). `run_corpus.py codehash` prints the three.
- Every result file, dump, sheet and capture records the three hashes of the tree that made it, so a result is
  never stamped only at scoring time. `parity` makes a tree VOID whose dumps were made at another identity than its
  own; `c6-score` makes a repository's C6 VOID whose captures were; `audit` checks the sheets (4.4, A4).
- `decide` FLIPs only when every identity the results rest on is one: each result file's own, the dumps' that
  `parity` lists, the captures' that `c6-score` copies, and C2's (the fresh sheet's when 4.4 needs one, else the
  sheets' code and kit). The kit is registered in section 1 before the first decision run (PREREG_CS.md's pattern);
  `decide` refuses another kit, and an unfilled slot means STAYS OPT-IN.

### A2. Every run is kept, and a FAIL is final (4.2, 4.10)
- Every scoring command (`metrics`, `gate`, `audit`, `parity`, `c6-score`) appends one line to `ledger.jsonl` in its
  output directory: the result file's name and sha256, its identity and registration, and the outcome of each
  criterion it measures (pass, fail or void; C2 "pending" while the route of 4.4 is open: a fix or the fresh sheet
  still owed). Each line holds the sha256 of the line before it. Every result of this registration is written to one
  directory, and RESULTS.md lists every ledger line.
- A criterion that FAILs on any identity ends the registration: STAYS OPT-IN, and a run on a later identity never
  replaces it. The re-runs of 4.2 and 4.10 after a product change apply only while nothing has failed. Two
  exceptions, no other: C2 items that are not TRUE do not fail C2 while at least 177 of 180 are TRUE and their fixes
  and the fresh sheet are owed (those fixes re-run the other criteria and never reopen a FAIL measured before them);
  and a C6 FAIL for which 4.8's fallback applies is replaced by the run after the fallback, once, at one identity.
- `decide` reads the ledger. A broken chain, a result file that is not the last line for its name, a line measured
  against other PREREG blocks or with another kit, or any FAIL that is final means STAYS OPT-IN. VOID is never a FAIL.

### A3. The C2 judge (4.4, 4.10)
- The judge is the model `claude-opus-5-5` (the id the judging session reports). Each sheet is judged once, in a fresh
  context that holds only the prompt `run_corpus.py prompt` writes for that sheet: the rule below verbatim, the answer
  format, and the sheet's items with their context. Sampling settings are the interface's own (temperature 0 where
  it offers one) and are recorded.
- A verdicts file records the sheet's sha256, the prompt's sha256, the model and the settings; `audit` refuses one for
  another sheet, prompt or model. The ledger binds each sheet to the one verdicts file first audited for it: a
  second verdicts file for a sheet means STAYS OPT-IN.
- 4.10's fourth bullet (a sheet judged again on an error the operator finds) is withdrawn: no sheet is judged twice.
- The rule (`run_corpus.SHEET_RULE`, which `tests/sql_prereg_gates.py` checks against this text) is the JSON's
  `protocol.c2.sheet_rule`.

### A4. The C2 draws are checked (4.4)
- `metrics` records the sha256 of `pool.jsonl`, and every sheet records the pool's sha256 and its identity. `sample`
  takes only a registered seed at its registered size, and it excludes exactly the earlier registered sheets (all
  three for the fresh seed 61104) and the declarations of A7, nothing else.
- `audit` takes the pool the sheets were drawn from (and, with the fresh sheet, the decision identity's pool), checks
  their sha256 and draws every sheet again: a sheet that is not the registered draw makes C2 VOID. `decide` checks
  that the pool C2 rests on is the decision identity's `metrics` pool.
- Each item that is not TRUE needs its own entry in the fixes file `audit --fixes` reads: its seed and index, and a
  section B case of `tests/sql_reader_gates.py` (at least 12 characters of a check's text or of a fixture section B
  reads) that the file holds. Until every such item has its entry, C2 is pending, never a pass.

### A5. C3 judges every file (4.5)
The 10 s bound is judged over every file in every pass, whatever its state, and a file the reader did not read
(state `slow`, `budget` or `too_large`; the corpus holds no file over 8 MB and C3 gives no budget) fails C3. ms/KB is
still taken over the files read.

### A6. C6: the Mattermost targets, and what a capture must show (4.8, 5.4)
- Seven of the twenty registered Mattermost targets (mm01, mm05, mm10, mm13, mm14, mm16, mm19) lie under `public/`,
  which DeepMap's default vendor rule (`sync._is_vendor_path`) drops from the graph in both arms: they could never be
  hit, so never lost. The candidates of section 5.4 now leave out every file the product's vendor rule drops (with
  the project root's `.gitignore` directories, `sync._gitignore_dir_parts`), and, for Go, every file whose name or
  directory names hold `test` (test support code the registered rule meant to leave out, such as `apitestlib.go` and
  `plugin_api_tests/`). The C# rule is unchanged, and the teams-samples draw is the same under the new candidates.
- Mattermost is drawn again with the new seed 71103. A first draw with that seed under the vendor rule alone was set
  aside on its file names (two test support files) before the Go rule above was added. The twenty targets and their
  queries, written from the targets' code before any DeepMap run, are the JSON's `registered.c6.mattermost`; they
  replace section 5.4's Mattermost table. `ma15` is `mm12`'s function, with `mm12`'s query.
- `c6-capture` refuses unless `DEEPMAP_EXTRACTOR=graphify` is set, the clone's `.deepmap/graph_build.json` says the
  graph was built by graphify alone (`extractor` and `graph_engines`), and `.deepmap/sync_last.json` says its sync
  pushed (`outcome` synced, `rc` 0). It records them, and per query the number of the graph's nodes in the target's
  file with the target's name.
- `c6-score`: a repository's C6 is VOID when a target has no node in the off graph, or when the off arm hits fewer
  than `protocol.c6.min_hits_off` (5) of the 20 targets: a lost-hit count over so few hits measures nothing.

### A7. Section 2's disclosure, completed; the declarations development judged
- Section 2 leaves out the product reader's timing on the design's local copy of this corpus: 2026-11-02 00:15, box
  loaded, 1.08 ms/KB overall (features 1.59, Mattermost 1.59, databases 1.21, Oracle 0.96); an earlier try at 00:04
  on a busier box read 2.6 to 5.3 ms/KB and stopped at an unreadable path. And the fix round's A/B at 03:32 (the
  reader before and after a fix, over the same 1,574 files, 23.8 MB): 0.799 then 0.789 ms/KB, declarations 3,539 then
  3,584; the 10 files whose declarations changed were read and judged. C3's 1.0 ms/KB lies between those figures.
- Development judged declarations of C2's pool sources: prototype 2's four samples of 60 (seeds 20261101, 7, 99 and
  2026, from a pool of 579, then 575) and the product's 40 from a pool of 558 (seed 20261102); about 235 distinct
  declarations, some 40 % of the pool, whose error shapes were fixed. The 195 identities the saved printouts give
  (source, path, line, kind; `dev_judged.json`, sha256 in the JSON) are left out of every C2 draw. The sample with seed
  20261101 was not saved: about 40 of its declarations stay in the pool, so about 19 of the 180 decision items are
  expected to be ones development judged. RESULTS.md reports the pool before and after the exclusion.
- C1 (4.3) needs no amendment: `score_c1` now compares every kind the line-start rule reads, as 4.3 already says.

```json
{
 "schema": "g-sql/amendments/1",
 "amended": "2026-11-02",
 "protocol": {
  "identity": {
   "hashes": [
    "code",
    "reader_gates",
    "kit"
   ],
   "reader_gates": "tests/sql_reader_gates.py",
   "kit": "benchmark/sql_reader/*.py"
  },
  "ledger": "ledger.jsonl",
  "c2": {
   "judge": {
    "model": "claude-opus-5-5",
    "judgings_per_sheet": 1
   },
   "sheet_rule": "Judge each item against the source text shown. TRUE when the line holds the CREATE / ALTER (or, for a package member, the PROCEDURE / FUNCTION header) that defines an object of exactly this kind with exactly this name (schema parts as written, quotes removed, case ignored), including a definition inside a string that the script executes (EXEC(...), sp_executesql, EXECUTE IMMEDIATE, a variable the same batch executes). FALSE otherwise: a comment, a message or data string, a privilege statement, a template placeholder, a temporary object, a wrong kind, a wrong name, a wrong line, an ALTER that redefines nothing. UNSURE when the text shown cannot decide it (UNSURE counts as FALSE).",
   "dev_judged": {
    "file": "dev_judged.json",
    "sha256": "77daeb57feb9abdf764ac0b30505fdb086dce4b79b170025ffd3758741e561bb",
    "items": 195
   },
   "fix_case_min_chars": 12
  },
  "c3": {
   "read_states": [
    "read"
   ]
  },
  "c6": {
   "sources": {
    "mattermost": {
     "draw_seed": 71103
    }
   },
   "candidates": {
    "vendor": "sync._is_vendor_path with sync._gitignore_dir_parts(the project root)",
    "go_test": "a .go file whose name or directory names hold test"
   },
   "min_hits_off": 5
  }
 },
 "registered": {
  "c6": {
   "mattermost": [
     {
      "file": "channels/app/imports/import_validators.go",
      "name": "ValidateUserChannelsImportData",
      "line": 426,
      "id": "ma01",
      "query": "check each channel entry of a bulk import user record: channel name present, roles and notification levels valid"
     },
     {
      "file": "channels/store/sqlstore/team_store.go",
      "name": "SearchAllPaged",
      "line": 592,
      "id": "ma02",
      "query": "find teams matching a search term and also return the total number of teams that match"
     },
     {
      "file": "channels/store/localcachelayer/role_layer.go",
      "name": "Save",
      "line": 39,
      "id": "ma03",
      "query": "store a role, then invalidate the cached role and clear the cached role permissions across the cluster"
     },
     {
      "file": "channels/store/sqlstore/user_store.go",
      "name": "GetUsersBatchForIndexing",
      "line": 2005,
      "id": "ma04",
      "query": "load the next batch of users created after a cursor, with their team and channel ids, for the search index"
     },
     {
      "file": "platform/shared/healthcheck/rule.go",
      "name": "Single",
      "line": 69,
      "id": "ma05",
      "query": "wrap a health check that yields one result so it fits a rule's evaluation, which expects a list"
     },
     {
      "file": "channels/app/audit.go",
      "name": "GetAudits",
      "line": 44,
      "id": "ma06",
      "query": "fetch a user's audit records up to a limit, reporting an out of bounds limit as a bad request"
     },
     {
      "file": "channels/app/auto_responder.go",
      "name": "SendAutoResponseIfNecessary",
      "line": 24,
      "id": "ma07",
      "query": "send the receiver's automatic reply to a direct message from a non-bot user, unless one was already sent today"
     },
     {
      "file": "channels/store/sqlstore/group_store.go",
      "name": "PermanentDeleteMembersByUser",
      "line": 695,
      "id": "ma08",
      "query": "remove every group membership row of one user from the database for good"
     },
     {
      "file": "channels/store/localcachelayer/channel_layer.go",
      "name": "GetMany",
      "line": 264,
      "id": "ma09",
      "query": "fetch several channels by id, taking cached ones from the local cache and querying the store for the rest"
     },
     {
      "file": "platform/services/sharedchannel/sync_send.go",
      "name": "NotifyChannelChanged",
      "line": 74,
      "id": "ma10",
      "query": "schedule a sync of a modified shared channel to the remote clusters after a short delay"
     },
     {
      "file": "channels/app/authorization.go",
      "name": "HasPermissionToReadChannel",
      "line": 466,
      "id": "ma11",
      "query": "decide whether a user may read a channel's content, falling back to team permission for public channels without compliance"
     },
     {
      "file": "platform/shared/healthcheck/accessors.go",
      "name": "LicenseFeature",
      "line": 180,
      "id": "ma12",
      "query": "read one feature flag from the server license in a health check snapshot, unknown when the license predates it"
     },
     {
      "file": "channels/store/sqlstore/shared_channel_store.go",
      "name": "UpdateUserLastSyncAt",
      "line": 842,
      "id": "ma13",
      "query": "advance the last sync time of a shared channel user from the user's update and picture timestamps in one query"
     },
     {
      "file": "enterprise/metrics/histogram.go",
      "name": "Observe",
      "line": 60,
      "id": "ma14",
      "query": "record a histogram value and log a warning when it exceeds the highest bucket, with the metric's labels"
     },
     {
      "file": "channels/app/plugin_db_driver.go",
      "name": "RowsClose",
      "line": 304,
      "id": "ma15",
      "query": "close the result rows a plugin's database driver holds under an id and forget them"
     },
     {
      "file": "channels/app/property_field.go",
      "name": "SearchPropertyFields",
      "line": 306,
      "id": "ma16",
      "query": "app layer search of a group's property fields that turns property service errors into app errors"
     },
     {
      "file": "platform/shared/healthcheck/accessors.go",
      "name": "DataSource",
      "line": 102,
      "id": "ma17",
      "query": "read the database connection string from the configuration held in a health check snapshot"
     },
     {
      "file": "channels/store/sqlstore/post_store.go",
      "name": "GetPostReminders",
      "line": 3441,
      "id": "ma18",
      "query": "take the post reminders that are due by a given time, deleting them as they are returned"
     },
     {
      "file": "enterprise/elasticsearch/elasticsearch/elasticsearch.go",
      "name": "SearchUsersInTeam",
      "line": 1573,
      "id": "ma19",
      "query": "Elasticsearch autocomplete of users in a team, or only in given channels, returning at most the limit of user ids"
     },
     {
      "file": "channels/api4/data_retention.go",
      "name": "InitDataRetention",
      "line": 14,
      "id": "ma20",
      "query": "register the REST routes for data retention policies and the teams and channels they apply to"
     }
    ]
  }
 }
}
```

<!-- G-SQL-PROTOCOL BEGIN (sha256 of the lines between this marker and its END is locked by tests/sql_prereg_gates.py) -->
## 4. Protocol and criteria

### 4.1 What is decided
Whether the SQL reader is read by default: `graphify_extract.SQL_DECLARATIONS_DEFAULT` becomes True and
`DEEPMAP_SQL_DECLARATIONS` moves to `sync_status`'s default-ON knobs in the same change. Nothing else depends on this
decision: the "not parsed here" census with its agent relay (design Phase A) and the graph revision (Phase R) ship
whatever it says. Pilot or exploratory numbers (section 2) are never decision data. Thresholds, seeds, pins, truth
expectations and queries are never edited after data.

### 4.2 Code identity, re-runs and VOID
- Every criterion is measured on one decision code identity (section 1). A code change after a measurement re-runs
  every criterion its change can move on the new identity: C1, C3, C4, C5 and C6 always; C2's 180 verdicts stand for
  the identity their sheets were drawn from, and when that is not the decision identity (any reader change after the
  sheets, the C6 fallback of 4.8 included) C2 needs its fresh sample (4.4), drawn from the decision identity.
- VOID is not a result: a criterion whose run is VOID neither passes nor fails, and the decision cannot be FLIP while
  any criterion is VOID. VOID: a truth count other than section 5.3's; a dump whose extraction is not graphify, or
  whose reader state is not the arm's; a capture whose two passes differ or whose graph is not the arm's; a box not
  shown quiet (4.5); result files that name other PREREG blocks, a fixture registration or two code identities.

### 4.3 C1: recall on the exact-truth sets (`run_corpus.py metrics`)
The reader is `build/sql_reader.read_file` (no cache, no budget); a file's declarations are the `:Class` /
`:Function` nodes it returns (what reaches the graph), each as (kind, qualified name, lower-cased).
- **WWI SSDT:** each of the 231 files directly under `<schema>/<folder>/` of
  `samples/databases/wide-world-importers/wwi-ssdt/wwi-ssdt/`, folder Tables, Views, Stored Procedures, Functions or
  User Defined Types (one object per file), is found when its nodes hold a declaration of kind table, view,
  procedure, function, type or table type whose name equals the file's stem (case ignored). Recall = found / 231
  >= 0.99 (at least 229).
- **Northwind** (`instnwnd.sql`), **Sakila** (`mysql-sakila-schema.sql`), **GitLab** (`db/structure.sql`): the truth
  is the line-start rule, applied to the source alone: every line that starts at column 0 with `CREATE`, optionally
  `OR REPLACE` / `OR ALTER`, `DEFINER=...`, `SQL SECURITY ...`, `ALGORITHM=...`, `UNLOGGED` or `MATERIALIZED`, then
  TABLE, VIEW, PROC[EDURE], FUNCTION or TRIGGER and a name (parts quoted with `""`, `[]` or backticks, unquoted and
  lower-cased; never TEMP / TEMPORARY, never an indented line). Per kind, the reader's set of (kind, qualified name)
  for that file must EQUAL the truth set: every truth object found, and no other object of that kind.

### 4.4 C2: precision on fresh seeded samples (`run_corpus.py sample`, `audit`)
- **Pool:** every declaration node `read_file` returns for the `samples/features/` files of `sqlserver-samples`, for
  `oracle-samples`, `mattermost` and the EF repository; fixed order (source, path bytes, line, kind, qualified).
- **Samples:** seeds 61101, 61102 and 61103, in this order, 60 items each, each sample excluding every item an earlier
  one drew (identity: source, path, kind, qualified): 180 distinct declarations.
- **Judge:** an LLM judge (the operator's standing rule: the LLM audit is the acceptance) reads each sheet (the line
  with 8 lines before and 30 after, decoded as the reader decodes) and records TRUE, FALSE or UNSURE with a one-line
  reason per item, under the sheet's rule (`run_corpus.SHEET_RULE`: TRUE when that line holds the CREATE / ALTER, or
  a package member's header, that defines an object of exactly this kind and name, a definition inside a string the
  script executes included). UNSURE counts as FALSE. The judge sees no other output of the reader.
- **Pass:** at least 177 of the 180 TRUE. When any item is not TRUE, each error shape is fixed in
  `build/sql_reader.py` and locked as a negative case in `tests/sql_reader_gates.py` section B (named in the verdicts
  file's `fixes`). When any item is not TRUE, or the code changed after the sheets were drawn, a fresh sample (seed
  61104, 60 items, from the decision identity's pool, excluding the 180) must be judged 60 of 60 TRUE. With every
  item TRUE on the decision identity no fresh sample is drawn.

### 4.5 C3: time (`run_corpus.py metrics`, `gate`)
- The whole corpus (every registered SQL file of the six sources) read by `read_file`, no cache, no budget, three
  passes in one process; ms/KB = 1000 x (seconds) / (KB), over the files whose state is "read".
- **Pass:** the median of the three passes <= 1.0 ms/KB; no file over 10 s in any pass (the worst-case bound); and
  `tests/sql_reader_gates.py` GREEN (its section F: linear time on large adversarial inputs) under both engine ends
  at the decision identity.
- A quiet box: no other suite, publish verify or benchmark running; the CPU load (Windows LoadPercentage, elsewhere
  the load average over the CPU count) is read before and after; above 30 %, or unreadable, the timing is VOID and
  is measured again.

### 4.6 C4: engine parity (`run_corpus.py dump`, `parity`)
For each of the six registered source trees (the project root `fetch_corpus.project_root` names, a git work tree),
`dump --sql on` under each engine end: the extraction pass of `deepmap sync` offline (the call-linking kit's
`offline_calls.run_pipeline`, source-locked to `sync()` by `tests/call_linking_harness_gates.py`). **Pass:** on every
tree the SQL nodes (key `sql:...`, every field) and the SQL report's counts are identical on 0.8.39 and 0.8.51.

### 4.7 C5: non-SQL identity (`run_corpus.py dump`, `parity`)
For each tree and engine end, `dump --sql on` against `dump --sql off`. **Pass:** the non-SQL nodes (every field),
the `:CALLS` edges between them (pair and grade) and the typed and `:IMPORTS` rows between them
(`edge_resolve.edge_rows`, what sync writes) are identical, and with the reader on no `:CALLS` edge, typed or
`:IMPORTS` row has a SQL node as an endpoint. Reported, not judged: the SQL nodes added per tree.

### 4.8 C6: live search crowding (`clone_graph.py build`, `run_corpus.py c6-capture`, `c6-score`)
- **Repositories:** Mattermost (project root `server/`: Go and 495 `.sql` files) and the EF repository (its root).
- **Per repository and arm (off, then on):** a copy of the fetched tree at a fresh path; a throwaway `DEEPMAP_HOME`;
  `NEO4J_URI` unset; `DEEPMAP_SQL_DECLARATIONS=0` (off) or `=1` (on), `DEEPMAP_EXTRACTOR=graphify`, every other
  `DEEPMAP_*` unset; `python benchmark/clone_graph.py build <root>` (a fresh instance, the product's default setup;
  exit 4 or a graph not built by graphify is VOID); `run_corpus.py c6-capture` (the 20 registered queries through the
  in-process `DeepMapRetriever`, top 15, the default arm weights, two passes that must be identical); then
  `clone_graph.py rm <root>`. Engine end 0.8.39.
- A target is hit when a result lies in the target's file (path relative to the project root) and its name, without
  a trailing `()` and any qualifier, equals the target's name. A SQL node is a result whose file ends in `.sql`.
- **Pass, per repository:** the mean number of SQL nodes in the reader-on top 15 over the 20 queries <= 2.0, and the
  target's hit@15 lost (in the off top 15, not in the on top 15) on at most 2 of the 20 queries.
- **Pre-registered fallback, applied at most once:** when C6 fails for a repository and every SQL node in that
  repository's reader-on top-15 lists is a `:Module` of a file with no declarations, the reader gets one change ("a
  `.sql` file with no declarations gets no `:Module`; coverage still counts it as read through `coverage.sql.read`")
  and C6 is re-run on the new identity (with every other criterion 4.2 names). No other change is a fallback.
- Reported, not judged: sync wall time, node and embedded counts and hit@15 per arm.

### 4.9 Decision (`run_corpus.py decide`; DECISION.md)
ALL of C1 to C6 PASS, none VOID, on one decision code identity: FLIP, the default may flip ON (the default constant
and the knob's set together, locked by `tests/sql_reader_gates.py` section L), and design section 10's copy may ship.
Anything else: the reader stays opt-in (`DEEPMAP_SQL_DECLARATIONS=1`), Phases A and R ship as they are, and the
public language count stays 25. DECISION.md records the decision derived from the criteria (never argued), and which
registered corpora, and which `benchmark/call_linking/PREREG.md` corpora, hold `.sql` files.

### 4.10 What would change the outcome
- Nothing in this block or the registered block after data: a FAIL is final for this registration. A new decision
  needs a new registration (fresh seeds, a dated reason, the old results kept beside it).
- A source that can no longer be fetched or verified (an upstream deletion or force-push) makes every criterion that
  reads it VOID: the decision is then STAYS OPT-IN until a new registration replaces that source.
- A product change made for any reason after a measurement re-runs the criteria 4.2 names.
- A judge error the operator finds in the C2 verdicts: that sheet is judged again, the reason recorded in DECISION.md.

### 4.11 Results that mean the default does NOT flip
A missing or extra declaration on a line-start set; WWI SSDT recall below 0.99; fewer than 177 of 180 TRUE; a FALSE or
UNSURE in the fresh sample, a non-TRUE item without its fix, or a missing fresh sample where 4.4 requires one; a
median above 1.0 ms/KB, a file over 10 s, or `tests/sql_reader_gates.py` RED on either end; SQL nodes that differ
between the engine ends; any non-SQL difference with the reader on; an edge to a SQL node; a C6 mean above 2.0 or more
than 2 lost hits on either repository after the fallback; any criterion VOID.

```json
{
 "schema": "g-sql/protocol/1",
 "switch": "DEEPMAP_SQL_DECLARATIONS",
 "engines": [
  "0.8.39",
  "0.8.51"
 ],
 "c1": {
  "ssdt_min_recall": 0.99,
  "line_start": "equal"
 },
 "c2": {
  "pool": [
   {
    "source": "sqlserver-samples",
    "prefix": "samples/features/"
   },
   {
    "source": "oracle-samples"
   },
   {
    "source": "mattermost"
   },
   {
    "source": "teams-samples"
   }
  ],
  "seeds": [
   61101,
   61102,
   61103
  ],
  "n": 60,
  "min_true": 177,
  "fresh_seed": 61104,
  "fresh_n": 60,
  "fresh_min_true": 60,
  "context_before": 8,
  "context_after": 30
 },
 "c3": {
  "passes": 3,
  "statistic": "median",
  "max_ms_per_kb": 1.0,
  "max_file_seconds": 10.0,
  "quiet_max_load": 30
 },
 "c6": {
  "sources": {
   "mattermost": {
    "lang": "go",
    "draw_seed": 71101
   },
   "teams-samples": {
    "lang": "csharp",
    "draw_seed": 71102
   }
  },
  "top_k": 15,
  "queries": 20,
  "max_mean_sql": 2.0,
  "max_lost": 2
 }
}
```
<!-- G-SQL-PROTOCOL END -->

<!-- G-SQL-REGISTERED BEGIN (sha256 of the lines between this marker and its END is locked by tests/sql_prereg_gates.py) -->
## 5. Registered corpus and queries

### 5.1 Sources (pinned)

Each source's SQL set is the manifest `fetch_corpus.py` verifies: one line `<sha256>  <path>` per `.sql` file (path
relative to the source's project root, byte order), hashed; a raw file also registers its own sha256.

| Source | What | Commit | SQL files | Bytes | Manifest sha256 (raw: and the file's) |
|---|---|---|---|---|---|
| `sqlserver-samples` | microsoft/sql-server-samples: `samples/databases/` and `samples/features/` (WWI SSDT, Northwind, the feature scripts) | `3c71696a3942879f0481f3bd65fe112a40023f18` | 1,013 | 14,815,813 | `eea23acce26bf50383ef5c4b6f7d88fa812b86c3beea60d9e40ab1ea0870338a` |
| `oracle-samples` | oracle-samples/db-sample-schemas: every `.sql` file | `6660bad68c07bd143430ace58565b3f727e17263` | 89 | 5,047,890 | `7465004ca339f16dece9ede6c5c61b80ced4e542553d46eee950f12b733ef6a3` |
| `mattermost` | mattermost/mattermost: the `server/` tree (Go, PostgreSQL migrations), project root `server/` | `2b3fb21fe39e6d8178625aabd8b9176596ad4cf5` | 495 | 338,051 | `00ef80952c16df76e1d9b135ec46a422f3549eca86c8f359adc7574d0ef7c21f` |
| `gitlab-structure` | gitlab-org/gitlab `db/structure.sql` (pg_dump), the master head at registration | `aaa15e4b4b53b076c89777109d77fae52b8910d5` | 1 | 2,984,609 | `bd1c12fffe200fa9904bcb5e52405c51b254e0b462d0c214c3f768b57106a502`; file `2109af230135a3011da6db1e82644d4f6198daeb7c56fa11d9be60ab10319776` |
| `sakila` | jOOQ/sakila `mysql-sakila-db/mysql-sakila-schema.sql`, the main head at registration | `e089a5b1ec9af0df7a9c6a5d47d49fa1736a4e84` | 1 | 23,088 | `ff5f24191eebe88ac35feaae74ce1886ba702a8bf8598b77b9804f435e0a5a42`; file `f2c41c3bf6d6c239941b4f98fb37afad21f6be12b82bf586202529a793ccc2ee` |
| `teams-samples` | OfficeDev/Microsoft-Teams-Samples: the whole repository (section 5.2), project root its root | `b9aedfea98e3505acbcf68a3931be05a780d6149` | 5 | 15,928 | `a11feddb47548822bb195be6818a77566b0a9573b82d67eb7ae2d04a33ac437f` |

### 5.2 The EF repository (design section 9: chosen by metadata only, before any DeepMap run)

Rule (`fetch_corpus.py select-ef`, run 2026-11-02T03:31:17Z with `gh` signed in): every hit of the GitHub code search
`__EFMigrationsHistory "IF NOT EXISTS" extension:sql`, enumerated in 13 file-size partitions of at most 1,000 hits
each (7,138 distinct files in 4,006 repositories; the index covers default branches and files under 384 KB;
incomplete_results false on every partition's first page; a page can return fewer hits than its total says, which is
recorded); the repositories ranked by stars (ties by name); the first that is not a fork, not archived, not private,
not a D3-cs corpus (jellyfin/jellyfin, dotnet/eShop), with at least 100 `.cs` files at its default branch head and a
`.sql` file there holding the SQL Server idempotent guard `IF NOT EXISTS(SELECT * FROM [__EFMigrationsHistory] WHERE
[MigrationId] = N'` (the search's hit files read first, at most 60 files). Candidates in star order:
aliasvault/aliasvault (3,387 stars: no guard in its 14 .sql file(s)); Nethereum/Nethereum (2,259 stars: no guard in
its 3 .sql file(s)); dolthub/doltgresql (2,194 stars: 1 .cs files (< 100)); zanfranceschi/rinha-de-backend-2024-q1
(1,829 stars: 0 .cs files (< 100)); zanfranceschi/rinha-de-backend-2023-q3 (1,705 stars: 0 .cs files (< 100));
DuendeSoftware/products (1,613 stars: no guard in its 16 .sql file(s)); **OfficeDev/Microsoft-Teams-Samples (1,410
stars): selected**. Selected: head `b9aedfea98e3505acbcf68a3931be05a780d6149` (2026-10-26), 1,521 `.cs` files, the
guard in `samples/TeamsJS/msteams-application-qbot/Deployment/sql/Migrations.sql`. A first run of the same command
earlier the same day read only the search's hit files, at most 5 per repository, and selected the same repository at
the same head; the rule was widened to every `.sql` file at the head before this run (a cap on hit files could pass
over a qualifying repository). No DeepMap run on any candidate.

What the rule gave, stated before any data: a collection of Teams samples (16,800 files, 848 MB, mostly images; 3,645
code files in C#, JavaScript, TypeScript and Python), not one application, and a small SQL layer: 5 `.sql` files, of
which the EF Core idempotent script (376 lines) creates 11 tables inside its 43 migration guards and runs 5 statements
through `EXEC(N'...')`, none a view, procedure or function. So on this repository C2 adds guarded tables to the pool
and C6 measures crowding by a small SQL layer in a large code base; the EXEC-wrapped view / procedure definitions of
design section 4.6 reach C2 only where the feature and Oracle scripts hold them (and are locked by
`tests/sql_reader_gates.py` section A). Mattermost (495 `.sql` files) is the strong crowding test, and this
repository's C6 build is the heavier one.

### 5.3 Exact-truth sets (C1; counted from the source by section 4.3's rules at registration)

| Set | Source | File or folder | Truth |
|---|---|---|---|
| `wwi-ssdt` | `sqlserver-samples` | `samples/databases/wide-world-importers/wwi-ssdt/wwi-ssdt/` | 231 files: Tables 54, Views 26, Stored Procedures 135, Functions 12, User Defined Types 4 |
| `northwind` | `sqlserver-samples` | `samples/databases/northwind-pubs/instnwnd.sql` | 36: table 13, view 16, procedure 7 |
| `sakila` | `sakila` | `mysql-sakila-db/mysql-sakila-schema.sql` | 32: table 16, view 7, procedure 3, function 3, trigger 3 |
| `gitlab` | `gitlab-structure` | `db/structure.sql` | 2,222: table 1,475, view 15, function 339, trigger 393 |

### 5.4 C6 targets and queries

Drawn by `run_corpus.py c6-draw` (the candidates: exported Go functions and methods, or public C# methods, with a
block body of at least 5 lines, in the non-test, non-generated, non-vendored source files of the project root, in
path and line order; shuffled with the source's draw seed; the first 20 with a name not drawn before), then each query
written from the target's code alone: plain words, at most 20, never the target's own name.

**mattermost** (draw seed 71101):

| Id | Target (file:line) | Name | Query |
|---|---|---|---|
| mm01 | `public/model/property_permissions_conversion.go:159` | `ProjectLegacyPermissionsWithTemplate` | convert a property field's permission restrictions into its legacy permission attributes, using the stricter access mode of its template |
| mm02 | `channels/app/server.go:198` | `SetStore` | replace the data store that the server's platform service uses |
| mm03 | `channels/store/sqlstore/autotranslation_store.go:75` | `GetUserLanguage` | look up a user's locale for auto-translation in a channel that has auto-translation enabled |
| mm04 | `channels/app/platform/status.go:278` | `UpdateLastActivityAtIfNeeded` | record a session's latest activity time once the activity timeout has passed, and refresh the cached session |
| mm05 | `public/model/property_field.go:895` | `IsValid` | check that property field search options are consistent: object types, channel or team scope versus targets, cursor |
| mm06 | `channels/store/sqlstore/post_store.go:777` | `Get` | fetch one post by id from the database with its thread and its reply count |
| mm07 | `channels/store/sqlstore/channel_store.go:462` | `ToModel` | convert database rows of channel members with team and scheme roles into model objects |
| mm08 | `channels/app/imports/import_report.go:73` | `Add` | record a user that could not be matched during an import, under a lock |
| mm09 | `channels/app/properties/property_field.go:1722` | `SearchPropertyFields` | search the property fields of a group and run the hooks that follow reading them |
| mm10 | `public/model/client4.go:3342` | `ViewChannel` | REST client call that marks a channel as viewed by a user |
| mm11 | `channels/store/sqlstore/read_receipt_store.go:155` | `GetUnreadCountForPost` | count the channel members who have not read a post yet, leaving out its author |
| mm12 | `channels/app/plugin_db_driver.go:304` | `RowsClose` | close the result rows a plugin's database driver holds under an id and forget them |
| mm13 | `public/model/client4.go:2726` | `InvalidateEmailInvites` | REST client call that cancels the email invitations to teams that were not accepted |
| mm14 | `public/pluginapi/team.go:202` | `CreateMembers` | plugin API helper that adds several users to a team at once |
| mm15 | `platform/shared/filestore/azurestore_rangereader.go:110` | `ReadAt` | read bytes at an offset of an Azure blob with a ranged download |
| mm16 | `public/model/client4.go:3990` | `GetContentFlaggedPost` | REST client call that fetches a post that was reported through content flagging |
| mm17 | `channels/jobs/jobs.go:38` | `CreateJob` | create a background job of a given type and save it to the store |
| mm18 | `channels/store/localcachelayer/user_layer.go:143` | `GetAllProfilesInChannel` | all user profiles in a channel, answered from the local cache when that is allowed |
| mm19 | `public/model/config.go:5815` | `SanitizeDataSource` | hide the username and password in a PostgreSQL connection URL |
| mm20 | `channels/store/sqlstore/user_store.go:228` | `DeactivateMagicLinkGuests` | deactivate every guest user who signs in with a magic link and return their ids |

**teams-samples** (draw seed 71102):

| Id | Target (file:line) | Name | Query |
|---|---|---|---|
| ef01 | `samples/TeamsSDK/Archived/app-virtual-assistant/csharp/Extension/ActivityExtensions.cs:11` | `GetSkillId` | read the skill id from a card action's data on a message or invoke activity |
| ef02 | `samples/TeamsSDK/Archived/account-linking/csharp/Source/Services/UserTokenStore/InMemory/InMemoryUserTokenStore.cs:42` | `SetTokenAsync` | store a user's token in memory, keyed by tenant and user |
| ef03 | `samples/TeamsJS/msteams-application-qbot/Source/Microsoft.Teams.Apps.QBot.Infrastructure/TeamsService/TeamsMessageService.cs:497` | `UpdateSelectAnswerMessageAsync` | update the original question message in a Teams channel once an answer has been selected |
| ef04 | `samples/TeamsSDK/Archived/account-linking/csharp/Source/Services/UserTokenStore/InMemory/InMemoryUserTokenStore.cs:23` | `DeleteTokenAsync` | remove a user's token from the in-memory token store for a tenant |
| ef05 | `samples/TeamsJS/graph-app-installation-lifecycle/csharp/AppInstallation/Controllers/BaseController.cs:40` | `GetAuthenticatedClient` | create a Microsoft Graph client that authenticates with a given bearer token |
| ef06 | `samples/TeamsJS/tab-channel-group-sso-quickstart/csharp_dotnetcore/Helper/SSOAuthHelper.cs:127` | `GetAccessTokenOnBehalfUserAsync` | exchange the caller's id token for an app access token on behalf of the user |
| ef07 | `samples/TeamsSDK/Archived/msgext-search-sso-config/csharp/Bots/TeamsMessagingExtensionsSearchBot.cs:334` | `FetchAdaptive` | load an adaptive card from a JSON file as a message attachment |
| ef08 | `samples/TeamsJS/msteams-application-resourcehub/Source/microsoft-teams-apps-selfhelp/Helper/MessageService.cs:323` | `SendWelcomeCardToUser` | build the welcome adaptive card for a user from a template, with a link to learn more |
| ef09 | `samples/TeamsSDK/Archived/bot-conversation-sso-quickstart/csharp_dotnetcore/BotConversationSsoQuickstart/Bots/DialogBot.cs:54` | `OnTurnAsync` | handle an incoming bot activity, then save the conversation and user state |
| ef10 | `samples/TeamsJS/graph-rsc/csharp/RSCDemo/Controllers/HomeController.cs:219` | `SendNotification` | send an activity feed notification to a user through the Graph API |
| ef11 | `samples/TeamsSDK/Archived/bot-calling-meeting/csharp/Source/CallingBotSample/AdaptiveCards/AdaptiveCardFactory.cs:20` | `CreateWelcomeCard` | build the welcome card attachment, optionally with a join meeting button |
| ef12 | `samples/TeamsSDK/Archived/tab-sso/csharp/TeamsTabSSO/Helper/SSOAuthHelper.cs:92` | `AudienceValidator` | check that a token's audiences include one of the valid audiences |
| ef13 | `samples/TeamsJS/graph-teams-tag/csharp/GraphTeamsTag/Helper/GraphHelper.cs:197` | `GetTeamworkTagMembersAsync` | list every member of a team tag through Microsoft Graph, following the result pages |
| ef14 | `samples/TeamsJS/graph-pinned-messages/csharp/GraphPinnedMessage/SimpleGraphClient.cs:25` | `GetGraphClientforApp` | create a Graph client that authenticates as the application with its id, password and tenant |
| ef15 | `samples/TeamsJS/bot-tab-conversations/csharp/Source/ConversationalTabs.Web/Exceptions/ApiArgumentException.cs:78` | `ThrowIfNullOrEmpty` | fail with an argument error when a string parameter is missing or blank |
| ef16 | `samples/TeamsJS/graph-activity-feed/csharp/ActivityFeedNotification/Helpers/ChatMessageHelper.cs:146` | `CreatePendingFinanceRequestCard` | build a Teams chat message with an adaptive card showing the details of a pending task |
| ef17 | `samples/TeamsSDK/Archived/app-HR-talent/csharp/src/Services/InvokeActivityHandler.cs:218` | `HandleMessagingExtensionOnCardButtonClickedAsync` | handle a button on a messaging extension card: leave a comment on a candidate or schedule an interview |
| ef18 | `samples/TeamsJS/graph-app-installation-lifecycle/csharp/AppInstallation/Controllers/HomeController.cs:193` | `UpgradeUserApp` | upgrade an app installed for a user through Microsoft Graph |
| ef19 | `samples/TeamsJS/meetings-share-to-stage-signing/csharp/Source/MeetingSigning.Infrastructure/Data/Repositories/InMemory/SignatureRepository.cs:63` | `UpdateSignature` | record a signature on a document in memory, refusing one that is missing or already signed |
| ef20 | `samples/TeamsSDK/Archived/bot-formatting-cards/csharp/BotFormattingCards/Cards/AllCards.cs:172` | `SendPersonaCardSetIcons` | adaptive card that shows several people as a set of persona icons |

```json
{
 "schema": "g-sql/registered/1",
 "registered": "2026-11-02",
 "sources": [
  {
   "id": "sqlserver-samples",
   "type": "git",
   "layout": "sql-set",
   "url": "https://github.com/microsoft/sql-server-samples.git",
   "commit": "3c71696a3942879f0481f3bd65fe112a40023f18",
   "select": [
    "samples/databases",
    "samples/features"
   ],
   "sql_files": 1013,
   "sql_bytes": 14815813,
   "sql_manifest_sha256": "eea23acce26bf50383ef5c4b6f7d88fa812b86c3beea60d9e40ab1ea0870338a"
  },
  {
   "id": "oracle-samples",
   "type": "git",
   "layout": "sql-set",
   "url": "https://github.com/oracle-samples/db-sample-schemas.git",
   "commit": "6660bad68c07bd143430ace58565b3f727e17263",
   "select": [],
   "sql_files": 89,
   "sql_bytes": 5047890,
   "sql_manifest_sha256": "7465004ca339f16dece9ede6c5c61b80ced4e542553d46eee950f12b733ef6a3"
  },
  {
   "id": "mattermost",
   "type": "git",
   "layout": "clone",
   "url": "https://github.com/mattermost/mattermost.git",
   "commit": "2b3fb21fe39e6d8178625aabd8b9176596ad4cf5",
   "sparse": [
    "server"
   ],
   "root": "server",
   "sql_files": 495,
   "sql_bytes": 338051,
   "sql_manifest_sha256": "00ef80952c16df76e1d9b135ec46a422f3549eca86c8f359adc7574d0ef7c21f"
  },
  {
   "id": "gitlab-structure",
   "type": "raw",
   "layout": "raw",
   "url": "https://gitlab.com/gitlab-org/gitlab/-/raw/aaa15e4b4b53b076c89777109d77fae52b8910d5/db/structure.sql",
   "commit": "aaa15e4b4b53b076c89777109d77fae52b8910d5",
   "path": "db/structure.sql",
   "sha256": "2109af230135a3011da6db1e82644d4f6198daeb7c56fa11d9be60ab10319776",
   "bytes": 2984609,
   "sql_files": 1,
   "sql_bytes": 2984609,
   "sql_manifest_sha256": "bd1c12fffe200fa9904bcb5e52405c51b254e0b462d0c214c3f768b57106a502"
  },
  {
   "id": "sakila",
   "type": "raw",
   "layout": "raw",
   "url": "https://raw.githubusercontent.com/jOOQ/sakila/e089a5b1ec9af0df7a9c6a5d47d49fa1736a4e84/mysql-sakila-db/mysql-sakila-schema.sql",
   "commit": "e089a5b1ec9af0df7a9c6a5d47d49fa1736a4e84",
   "path": "mysql-sakila-db/mysql-sakila-schema.sql",
   "sha256": "f2c41c3bf6d6c239941b4f98fb37afad21f6be12b82bf586202529a793ccc2ee",
   "bytes": 23088,
   "sql_files": 1,
   "sql_bytes": 23088,
   "sql_manifest_sha256": "ff5f24191eebe88ac35feaae74ce1886ba702a8bf8598b77b9804f435e0a5a42"
  },
  {
   "id": "teams-samples",
   "type": "git",
   "layout": "clone",
   "url": "https://github.com/OfficeDev/Microsoft-Teams-Samples.git",
   "commit": "b9aedfea98e3505acbcf68a3931be05a780d6149",
   "root": "",
   "sql_files": 5,
   "sql_bytes": 15928,
   "sql_manifest_sha256": "a11feddb47548822bb195be6818a77566b0a9573b82d67eb7ae2d04a33ac437f"
  }
 ],
 "truth": {
  "wwi-ssdt": {
   "source": "sqlserver-samples",
   "rule": "ssdt",
   "dir": "samples/databases/wide-world-importers/wwi-ssdt/wwi-ssdt",
   "folders": {
    "Tables": 54,
    "Views": 26,
    "Stored Procedures": 135,
    "Functions": 12,
    "User Defined Types": 4
   }
  },
  "northwind": {
   "source": "sqlserver-samples",
   "rule": "line-start",
   "file": "samples/databases/northwind-pubs/instnwnd.sql",
   "kinds": {
    "table": 13,
    "view": 16,
    "procedure": 7
   }
  },
  "sakila": {
   "source": "sakila",
   "rule": "line-start",
   "file": "mysql-sakila-db/mysql-sakila-schema.sql",
   "kinds": {
    "table": 16,
    "view": 7,
    "procedure": 3,
    "function": 3,
    "trigger": 3
   }
  },
  "gitlab": {
   "source": "gitlab-structure",
   "rule": "line-start",
   "file": "db/structure.sql",
   "kinds": {
    "table": 1475,
    "view": 15,
    "function": 339,
    "trigger": 393
   }
  }
 },
 "c6": {
  "mattermost": [
   {
    "file": "public/model/property_permissions_conversion.go",
    "name": "ProjectLegacyPermissionsWithTemplate",
    "line": 159,
    "id": "mm01",
    "query": "convert a property field's permission restrictions into its legacy permission attributes, using the stricter access mode of its template"
   },
   {
    "file": "channels/app/server.go",
    "name": "SetStore",
    "line": 198,
    "id": "mm02",
    "query": "replace the data store that the server's platform service uses"
   },
   {
    "file": "channels/store/sqlstore/autotranslation_store.go",
    "name": "GetUserLanguage",
    "line": 75,
    "id": "mm03",
    "query": "look up a user's locale for auto-translation in a channel that has auto-translation enabled"
   },
   {
    "file": "channels/app/platform/status.go",
    "name": "UpdateLastActivityAtIfNeeded",
    "line": 278,
    "id": "mm04",
    "query": "record a session's latest activity time once the activity timeout has passed, and refresh the cached session"
   },
   {
    "file": "public/model/property_field.go",
    "name": "IsValid",
    "line": 895,
    "id": "mm05",
    "query": "check that property field search options are consistent: object types, channel or team scope versus targets, cursor"
   },
   {
    "file": "channels/store/sqlstore/post_store.go",
    "name": "Get",
    "line": 777,
    "id": "mm06",
    "query": "fetch one post by id from the database with its thread and its reply count"
   },
   {
    "file": "channels/store/sqlstore/channel_store.go",
    "name": "ToModel",
    "line": 462,
    "id": "mm07",
    "query": "convert database rows of channel members with team and scheme roles into model objects"
   },
   {
    "file": "channels/app/imports/import_report.go",
    "name": "Add",
    "line": 73,
    "id": "mm08",
    "query": "record a user that could not be matched during an import, under a lock"
   },
   {
    "file": "channels/app/properties/property_field.go",
    "name": "SearchPropertyFields",
    "line": 1722,
    "id": "mm09",
    "query": "search the property fields of a group and run the hooks that follow reading them"
   },
   {
    "file": "public/model/client4.go",
    "name": "ViewChannel",
    "line": 3342,
    "id": "mm10",
    "query": "REST client call that marks a channel as viewed by a user"
   },
   {
    "file": "channels/store/sqlstore/read_receipt_store.go",
    "name": "GetUnreadCountForPost",
    "line": 155,
    "id": "mm11",
    "query": "count the channel members who have not read a post yet, leaving out its author"
   },
   {
    "file": "channels/app/plugin_db_driver.go",
    "name": "RowsClose",
    "line": 304,
    "id": "mm12",
    "query": "close the result rows a plugin's database driver holds under an id and forget them"
   },
   {
    "file": "public/model/client4.go",
    "name": "InvalidateEmailInvites",
    "line": 2726,
    "id": "mm13",
    "query": "REST client call that cancels the email invitations to teams that were not accepted"
   },
   {
    "file": "public/pluginapi/team.go",
    "name": "CreateMembers",
    "line": 202,
    "id": "mm14",
    "query": "plugin API helper that adds several users to a team at once"
   },
   {
    "file": "platform/shared/filestore/azurestore_rangereader.go",
    "name": "ReadAt",
    "line": 110,
    "id": "mm15",
    "query": "read bytes at an offset of an Azure blob with a ranged download"
   },
   {
    "file": "public/model/client4.go",
    "name": "GetContentFlaggedPost",
    "line": 3990,
    "id": "mm16",
    "query": "REST client call that fetches a post that was reported through content flagging"
   },
   {
    "file": "channels/jobs/jobs.go",
    "name": "CreateJob",
    "line": 38,
    "id": "mm17",
    "query": "create a background job of a given type and save it to the store"
   },
   {
    "file": "channels/store/localcachelayer/user_layer.go",
    "name": "GetAllProfilesInChannel",
    "line": 143,
    "id": "mm18",
    "query": "all user profiles in a channel, answered from the local cache when that is allowed"
   },
   {
    "file": "public/model/config.go",
    "name": "SanitizeDataSource",
    "line": 5815,
    "id": "mm19",
    "query": "hide the username and password in a PostgreSQL connection URL"
   },
   {
    "file": "channels/store/sqlstore/user_store.go",
    "name": "DeactivateMagicLinkGuests",
    "line": 228,
    "id": "mm20",
    "query": "deactivate every guest user who signs in with a magic link and return their ids"
   }
  ],
  "teams-samples": [
   {
    "file": "samples/TeamsSDK/Archived/app-virtual-assistant/csharp/Extension/ActivityExtensions.cs",
    "name": "GetSkillId",
    "line": 11,
    "id": "ef01",
    "query": "read the skill id from a card action's data on a message or invoke activity"
   },
   {
    "file": "samples/TeamsSDK/Archived/account-linking/csharp/Source/Services/UserTokenStore/InMemory/InMemoryUserTokenStore.cs",
    "name": "SetTokenAsync",
    "line": 42,
    "id": "ef02",
    "query": "store a user's token in memory, keyed by tenant and user"
   },
   {
    "file": "samples/TeamsJS/msteams-application-qbot/Source/Microsoft.Teams.Apps.QBot.Infrastructure/TeamsService/TeamsMessageService.cs",
    "name": "UpdateSelectAnswerMessageAsync",
    "line": 497,
    "id": "ef03",
    "query": "update the original question message in a Teams channel once an answer has been selected"
   },
   {
    "file": "samples/TeamsSDK/Archived/account-linking/csharp/Source/Services/UserTokenStore/InMemory/InMemoryUserTokenStore.cs",
    "name": "DeleteTokenAsync",
    "line": 23,
    "id": "ef04",
    "query": "remove a user's token from the in-memory token store for a tenant"
   },
   {
    "file": "samples/TeamsJS/graph-app-installation-lifecycle/csharp/AppInstallation/Controllers/BaseController.cs",
    "name": "GetAuthenticatedClient",
    "line": 40,
    "id": "ef05",
    "query": "create a Microsoft Graph client that authenticates with a given bearer token"
   },
   {
    "file": "samples/TeamsJS/tab-channel-group-sso-quickstart/csharp_dotnetcore/Helper/SSOAuthHelper.cs",
    "name": "GetAccessTokenOnBehalfUserAsync",
    "line": 127,
    "id": "ef06",
    "query": "exchange the caller's id token for an app access token on behalf of the user"
   },
   {
    "file": "samples/TeamsSDK/Archived/msgext-search-sso-config/csharp/Bots/TeamsMessagingExtensionsSearchBot.cs",
    "name": "FetchAdaptive",
    "line": 334,
    "id": "ef07",
    "query": "load an adaptive card from a JSON file as a message attachment"
   },
   {
    "file": "samples/TeamsJS/msteams-application-resourcehub/Source/microsoft-teams-apps-selfhelp/Helper/MessageService.cs",
    "name": "SendWelcomeCardToUser",
    "line": 323,
    "id": "ef08",
    "query": "build the welcome adaptive card for a user from a template, with a link to learn more"
   },
   {
    "file": "samples/TeamsSDK/Archived/bot-conversation-sso-quickstart/csharp_dotnetcore/BotConversationSsoQuickstart/Bots/DialogBot.cs",
    "name": "OnTurnAsync",
    "line": 54,
    "id": "ef09",
    "query": "handle an incoming bot activity, then save the conversation and user state"
   },
   {
    "file": "samples/TeamsJS/graph-rsc/csharp/RSCDemo/Controllers/HomeController.cs",
    "name": "SendNotification",
    "line": 219,
    "id": "ef10",
    "query": "send an activity feed notification to a user through the Graph API"
   },
   {
    "file": "samples/TeamsSDK/Archived/bot-calling-meeting/csharp/Source/CallingBotSample/AdaptiveCards/AdaptiveCardFactory.cs",
    "name": "CreateWelcomeCard",
    "line": 20,
    "id": "ef11",
    "query": "build the welcome card attachment, optionally with a join meeting button"
   },
   {
    "file": "samples/TeamsSDK/Archived/tab-sso/csharp/TeamsTabSSO/Helper/SSOAuthHelper.cs",
    "name": "AudienceValidator",
    "line": 92,
    "id": "ef12",
    "query": "check that a token's audiences include one of the valid audiences"
   },
   {
    "file": "samples/TeamsJS/graph-teams-tag/csharp/GraphTeamsTag/Helper/GraphHelper.cs",
    "name": "GetTeamworkTagMembersAsync",
    "line": 197,
    "id": "ef13",
    "query": "list every member of a team tag through Microsoft Graph, following the result pages"
   },
   {
    "file": "samples/TeamsJS/graph-pinned-messages/csharp/GraphPinnedMessage/SimpleGraphClient.cs",
    "name": "GetGraphClientforApp",
    "line": 25,
    "id": "ef14",
    "query": "create a Graph client that authenticates as the application with its id, password and tenant"
   },
   {
    "file": "samples/TeamsJS/bot-tab-conversations/csharp/Source/ConversationalTabs.Web/Exceptions/ApiArgumentException.cs",
    "name": "ThrowIfNullOrEmpty",
    "line": 78,
    "id": "ef15",
    "query": "fail with an argument error when a string parameter is missing or blank"
   },
   {
    "file": "samples/TeamsJS/graph-activity-feed/csharp/ActivityFeedNotification/Helpers/ChatMessageHelper.cs",
    "name": "CreatePendingFinanceRequestCard",
    "line": 146,
    "id": "ef16",
    "query": "build a Teams chat message with an adaptive card showing the details of a pending task"
   },
   {
    "file": "samples/TeamsSDK/Archived/app-HR-talent/csharp/src/Services/InvokeActivityHandler.cs",
    "name": "HandleMessagingExtensionOnCardButtonClickedAsync",
    "line": 218,
    "id": "ef17",
    "query": "handle a button on a messaging extension card: leave a comment on a candidate or schedule an interview"
   },
   {
    "file": "samples/TeamsJS/graph-app-installation-lifecycle/csharp/AppInstallation/Controllers/HomeController.cs",
    "name": "UpgradeUserApp",
    "line": 193,
    "id": "ef18",
    "query": "upgrade an app installed for a user through Microsoft Graph"
   },
   {
    "file": "samples/TeamsJS/meetings-share-to-stage-signing/csharp/Source/MeetingSigning.Infrastructure/Data/Repositories/InMemory/SignatureRepository.cs",
    "name": "UpdateSignature",
    "line": 63,
    "id": "ef19",
    "query": "record a signature on a document in memory, refusing one that is missing or already signed"
   },
   {
    "file": "samples/TeamsSDK/Archived/bot-formatting-cards/csharp/BotFormattingCards/Cards/AllCards.cs",
    "name": "SendPersonaCardSetIcons",
    "line": 172,
    "id": "ef20",
    "query": "adaptive card that shows several people as a set of persona icons"
   }
  ]
 },
 "ef_selection": {
  "query": "__EFMigrationsHistory \"IF NOT EXISTS\" extension:sql",
  "guard": "IF\\s+NOT\\s+EXISTS\\s*\\(\\s*SELECT\\s+\\*\\s+FROM\\s+\\[__EFMigrationsHistory\\]\\s+WHERE\\s+\\[MigrationId\\]\\s*=\\s*N'",
  "searched_at": "2026-11-02T03:31:17Z",
  "hits": 7138,
  "repositories": 4006,
  "partitions": 13,
  "incomplete_results": false,
  "min_cs_files": 100,
  "excluded": [
   "jellyfin/jellyfin",
   "dotnet/eshop"
  ],
  "evaluated": [
   {
    "repo": "aliasvault/aliasvault",
    "stars": 3387,
    "ok": false,
    "why": "no idempotent guard in the 14 .sql file(s) read at the head",
    "cs_files": 546,
    "sql_files": 14,
    "head": "0509f76b3c9a5fda122bd95dc45a25a3e3a0b9b0"
   },
   {
    "repo": "Nethereum/Nethereum",
    "stars": 2259,
    "ok": false,
    "why": "no idempotent guard in the 3 .sql file(s) read at the head",
    "cs_files": 8023,
    "sql_files": 3,
    "head": "229f2782561b4588a594336295b2ec00fd726b46"
   },
   {
    "repo": "dolthub/doltgresql",
    "stars": 2194,
    "ok": false,
    "why": "1 .cs files (< 100)",
    "cs_files": 1,
    "sql_files": 338,
    "head": "60e8c04156784d9c32451202b54f4ac8349c9f6b"
   },
   {
    "repo": "zanfranceschi/rinha-de-backend-2024-q1",
    "stars": 1829,
    "ok": false,
    "why": "0 .cs files (< 100)",
    "cs_files": 0,
    "sql_files": 695,
    "head": "aad5e21f697ca89b11653fb7903fa4f1d369df5b"
   },
   {
    "repo": "zanfranceschi/rinha-de-backend-2023-q3",
    "stars": 1705,
    "ok": false,
    "why": "0 .cs files (< 100)",
    "cs_files": 0,
    "sql_files": 52,
    "head": "da99faf563c23dca43a5afa2ac31a34ca8cc39e6"
   },
   {
    "repo": "DuendeSoftware/products",
    "stars": 1613,
    "ok": false,
    "why": "no idempotent guard in the 16 .sql file(s) read at the head",
    "cs_files": 4382,
    "sql_files": 16,
    "head": "222118c7a0563a3cb4727fae5233f7ee98676e40"
   },
   {
    "repo": "OfficeDev/Microsoft-Teams-Samples",
    "stars": 1410,
    "ok": true,
    "why": null,
    "cs_files": 1521,
    "sql_files": 5,
    "head": "b9aedfea98e3505acbcf68a3931be05a780d6149"
   }
  ],
  "selected": {
   "repo": "OfficeDev/Microsoft-Teams-Samples",
   "stars": 1410,
   "head": "b9aedfea98e3505acbcf68a3931be05a780d6149",
   "cs_files": 1521,
   "sql_files": 5,
   "guard_file": "samples/TeamsJS/msteams-application-qbot/Deployment/sql/Migrations.sql"
  }
 }
}
```
<!-- G-SQL-REGISTERED END -->
