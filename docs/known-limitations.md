# DeepMap: known limitations

This page lists what DeepMap does not do, or does only in part, in the current release. Each entry states the
current behaviour, the reason, and the workaround where one exists. What has not been tested yet, and what the live
test campaign described in [`testing.md`](testing.md) left open, are near the end:
[Not tested yet](#not-tested-yet) and [What the live test campaign left open](#what-the-live-test-campaign-left-open).

Many of these limits also show up while you work. Every link in the graph carries a confidence label:

- `EXTRACTED`: read directly from your code (the declaration and its target are in the same file).
- `INFERRED`: matched by name to exactly one symbol. Likelier, but still a name match.
- `AMBIGUOUS`: several matches, or a dynamic link. A lead, not a fact: open the `file:line` and verify.

`deepmap_impact` says when an empty answer is not proof and when a method answer is a lower bound, and for a C#
symbol which calls the graph cannot see. Setup and refresh end with a verdict, `READY`, `DEGRADED` or `FAILED`, and
a `DEGRADED` verdict names the gap.

## What DeepMap is not

- It reports what it can trace. It is not a proof that nothing else depends on your code.
- The graph is a snapshot of your last sync, not of your working tree (see "DeepMap keeps its graph current"
  below).
- One graph per project. There are no cross-repository queries.
- Search matches names and short summaries, not code bodies. For an exact string or a regex, grep is still the
  right tool.
- There is no data-flow or taint analysis, and DeepMap never edits code.

## Platforms

- **Windows and Linux are tested; Macs are not verified yet.** Setup, search, impact, the hooks, per-project
  isolation and the background refresh ran live on Windows 11 and in Linux containers. On macOS only hermetic tests
  with simulated inputs have run. A manual workflow, `.github/workflows/macos.yml`, runs the test suite and a live
  end-to-end check (`tests/live_macos_e2e.py`) on GitHub's Apple-silicon runners; until it passes, treat macOS as
  unverified.
- **Intel Macs are not supported.** PyTorch publishes no macOS x86_64 build after 2.2.2, and the `transformers`
  release a fresh setup installs needs torch 2.4 or later. `/deep-map:setup` refuses up front, before anything is
  downloaded, and an x86_64 Python under Rosetta 2 on an Apple-silicon Mac is refused the same way.
  **Workaround:** on Apple silicon, run setup with a native arm64 Python.
- **macOS details only a real Mac can confirm.** The Java check never runs Apple's `/usr/bin/java` stub: with
  `JAVA_HOME` unset it looks for a real `java` on PATH, then a JDK 21 or 17 that `/usr/libexec/java_home` names,
  then a Homebrew `openjdk@21` / `openjdk@17`, and hands that JDK to Neo4j (a `JAVA_HOME` you set always wins).
  Process start stamps have 1 s resolution, and the `KERN_PROCARGS2` argument reader is unit-tested only. On an
  8 GB Mac the background refresh's memory floor (3 GiB free) will often skip a refresh with `low-memory`. macOS
  metadata (AppleDouble `._<name>` files, `__MACOSX/`, `.AppleDouble/`) is never treated as source.
- **Java on Windows and Linux.** Neo4j needs Java 17 or 21, and setup checks the Java Neo4j will run: a `JAVA_HOME`
  you set, otherwise the `java` on PATH. When that one is missing, another version or does not run, setup starts
  Neo4j with a JDK 17 or 21 it finds instead: for a `JAVA_HOME`, first the JDK of the `java` on PATH; then an
  installed JDK 21 or 17 (the Windows registry and the usual install folders, `/usr/lib/jvm`, `/usr/lib64/jvm`,
  SDKMAN, or a JDK unpacked into `<DEEPMAP_HOME>`). This is covered by automated tests with simulated installs; it
  has not yet run on a real machine with an unsupported Java first on PATH or in `JAVA_HOME`. Limits:
  - When setup passes over a `JAVA_HOME` you set, your `JAVA_HOME` is not changed: only Neo4j gets the other JDK,
    and setup prints a note each time. To make it permanent, point `JAVA_HOME` at a JDK 17 or 21 (or unset it)
    where it is set (system settings or your shell profile), then restart Claude Code: a running session keeps the
    value it started with. Setup refuses only when it finds no 17 or 21.
  - A JDK installed anywhere else is not found. **Workaround:** set `JAVA_HOME` to it, or unpack it into
    `<DEEPMAP_HOME>`.
  - On Windows, with `JAVA_HOME` unset and a 17 or 21 on PATH, Neo4j reads the Java registry entry before PATH. When
    that entry names a removed JDK or another version, setup hands Neo4j the PATH Java instead, but in a few cases
    (the PATH `java` is a launcher outside a JDK and no installed 17 or 21 answers in time) Neo4j may still run the
    registry's JDK. **Workaround:** set `JAVA_HOME` to a JDK 17 or 21.
  - On Linux and macOS, a `JAVACMD` in the environment overrides the Java DeepMap hands Neo4j.
- **Python 3.10 or later.** Tested on 3.10 to 3.13. When `python` is a Python 3.7 to 3.9, the hooks still start
  under it (and `python3` never gets their input), but that Python is not supported.
- **Windows: a Neo4j password containing `& | ^ < > %` is refused.** `cmd.exe` re-reads those characters in
  `neo4j-admin.bat`'s argument line, and `neo4j-admin` takes the password only as an argument. Setup refuses such
  a password with a clear message and keeps the store's current one. **Workaround:** choose a password without
  those characters (spaces and quotes work).
- **Windows: paths of 260 characters or more.** With `LongPathsEnabled=0` the graphify engine skips such files;
  DeepMap's census names them in a `DEGRADED` reason. A very long `DEEPMAP_HOME` can push the engine's own output
  past the limit, and the default `auto` extractor then falls back to the Python-only `ast` path
  (`.deepmap/graph_build.json` records the engine under `graph_engines`).
  **Workaround:** set `LongPathsEnabled=1`, or use a shorter project root and `DEEPMAP_HOME`.
- **Windows: `localhost` is slow.** A `NEO4J_URI` you set to `bolt://localhost:...` tries `::1` first, about 2 s
  per new connection. **Workaround:** write `bolt://127.0.0.1:<port>`, as DeepMap's own URIs do.
- **Windows: the Microsoft Store Python lets non-Python child processes leave the engine's job.** DeepMap runs the
  engine inside a kill-on-close job, so a killed sync leaves no engine workers behind. The engine starts only
  Python workers today, so this matters only if a future engine starts other programs.

## Neo4j

- **Only Neo4j 5.26 is tested.** New stores get 5.26.32; a store created with 5.26.0 keeps it until setup upgrades
  it (below). A bring-your-own `NEO4J_URI` endpoint (another version, Aura) was not live-tested.
- **A `NEO4J_URI` you set must be a Bolt URI.** Accepted: the schemes `bolt`, `bolt+s`, `bolt+ssc`, `neo4j`,
  `neo4j+s` and `neo4j+ssc` in any case, with no whitespace or control character and a port of 0 to 65535 when one
  is given (`port_registry.neo4j_uri_problem`). Anything else (the Neo4j Browser's `http://...:7474`, `https://`,
  no scheme, the old `bolt+routing://`, surrounding spaces) is refused before a driver exists, with one line that
  names the fix. An MCP server started by hand (not through `/deep-map:setup` or `provision.py mcp-start`) still
  hands its `NEO4J_URI` to the driver as given, and the background refresh skips a refused URI under its generic
  codes (`no-own-instance`, `remote-database`); `/deep-map:doctor` shows the rule's own line.
- **Credentials inside the URI.** The driver refuses `bolt://user:password@host`, and recent drivers quote the whole
  URI, password included, in that error. Only the `deepmap` CLI masks credentials inside a URI you set
  (`bolt://user:***@host:7687`). `provision.py` (`status`, `stop`, the setup and heal lines) and the MCP search
  server (its authentication back-off label, and driver errors it relays) print the URI as given.
  **Workaround:** keep credentials in `NEO4J_USER` / `NEO4J_PASSWORD`.
- **Neo4j versions are sticky per store.** heal and the hooks never change a store's release. `/deep-map:setup`
  moves this project's own store to a newer, checksum-pinned 5.26.x patch release once
  (`DEEPMAP_NEO4J_AUTO_UPGRADE=0` turns that off), and `provision.py neo4j-upgrade [--to X.Y.Z] [--check]` does it
  on request. The upgrade records the graph's answers, stops Neo4j, copies the store, and keeps the new release only
  when it gives the same answers; otherwise it rolls back. A crash leaves a journal that the next setup finishes or
  undoes. Residuals:
  - **Never run an older DeepMap build against a `DEEPMAP_HOME` a newer setup upgraded** (for example another
    Claude Code window still on the old plugin): it can start the old release on the upgraded store, or create a
    new empty base store and sync into it. **Workaround:** reload every window onto the new plugin before running
    `/deep-map:setup`.
  - The old binary (`<DEEPMAP_HOME>/neo4j/neo4j-community-5.26.0`, about 0.45 GB) stays; there is no clean-up verb.
    The instance table's `neo4j=` column shows which stores still use it.
  - An upgrade needs room for one copy of the store, plus 0.5 GB (0.7 GB more when the new release is not
    downloaded yet). On Windows without long paths, an upgrade whose copy would cross 260 characters is skipped.
  - A skipped upgrade (disk, path budget, no Java, a held lock, credentials a running Neo4j rejects, a stop that
    cannot be proven) prints a NOTE and keeps the old release. After a rolled-back automatic upgrade, setup waits
    7 days before trying that release again. **Workaround:** `provision.py neo4j-upgrade` retries at once.
  - The "same answers" check compares counts, the graph owner, the online `deepmap_*` indexes and one index query,
    not every property. Crash recovery is gate-tested at every journal phase but was simulated live only once.
  - `NEO4J_VERSION` blocks the upgrade; a pin that would move a store to an older release is ignored with a WARNING.
  - A rollback cut short can leave a `data.failed-<version>*` directory beside the instance's data (the setup NOTE
    names it). Delete it by hand.
- **Memory per project.** Each project runs one Neo4j JVM, its heap capped at 512m (`DEEPMAP_NEO4J_HEAP_MAX`), with
  a fixed 192m page cache, plus two MCP servers that keep the embedding model loaded (0.85 GB or more each). An active
  project holds roughly 2 to 3 GB of RAM. A changed cap applies at the store's next start, and every start rewrites
  it from the environment of the process that starts the store, so a cap set in one terminal reverts at the next
  start that lacks it (for example the session-start heal after a reboot).
  **Workaround:** set `DEEPMAP_NEO4J_HEAP_MAX` in the environment Claude Code runs in; `DEEPMAP_MCP_WARMUP=0` loads
  the model lazily; `hibernate` stops idle projects' databases and `instance-rm` removes one.
- **A long-lived Neo4j JVM can lose its Bolt event loop (upstream).** It then force-closes some new connections. The
  CLI retries once and `doctor` warns and names the remedy, but setup's "Neo4j already up" path and heal do not
  check, so setup can still report `READY`. **Workaround:** run `/deep-map:doctor` after setup; on that warning,
  `provision.py stop <root>`, then `provision.py heal <root>` (the graph is kept).
- **DeepMap's default password can reach a foreign Neo4j on 7687.** In a directory with no registry entry and no
  instance, the CLI and the provisioner still try DeepMap's default credentials on `127.0.0.1:7687`; the graph
  owner check prevents reading a foreign graph. **Workaround:** run `/deep-map:setup` first, or set `NEO4J_URI` and
  `NEO4J_PASSWORD`.

## Per-project isolation

Each project gets its own Neo4j instance (own data, ports and password store, under
`~/.deepmap/instances/<project key>/`) and its own pair of MCP ports, recorded in `~/.deepmap/registry.json`. One
extracted Neo4j binary drives every instance. The first project on a machine keeps the base ports (7687/7474 for
Neo4j, 8787/8788 for MCP); later projects get the next free pairs, and so does a project whose base ports another
program holds (for example a Neo4j you run in Docker). A `NEO4J_URI` you set turns per-project instances off: every
project then uses that endpoint, and `status` / `doctor` report `isolation: OPERATOR-MANAGED`. Limits:

- **Each instance is its own JVM** (see "Memory per project"). `deepmap hibernate` stops idle instances (default
  window 30 minutes; never the current project unless `--all`), and the session-start heal also stops other
  projects' idle instances when `DEEPMAP_HIBERNATE=1` (off by default). A hibernated instance comes back at the next
  session-start heal or `/deep-map:setup`, not on a search. `deepmap instance-rm` stops and deletes an instance; it
  never removes the first project's base store. The idle stamp is best-effort: under heavy lock contention a
  project can look idle for one window.
- **Tool descriptions name their project.** Each MCP server names its project and path at the start of every tool
  description. The MCP roots guard cannot act over the stateless HTTP transport, so the description is the check.
  **Workaround:** use a deepmap tool only when its description names your project; otherwise run
  `/deep-map:setup` in your project and start a new session.
- **Project identity can split one repository in two.** A non-git subfolder of a non-git project is its own
  project, and on a shared `NEO4J_URI` the same repository used through WSL (`/mnt/c/...`) and from Windows
  (`C:\...`) counts as two. **Workaround:** run from the project root, or pass `--switch-project` deliberately.
- **Non-ASCII project paths.** An entry written under another Windows ANSI code page than the current one is not
  recognised, and a key migration waits while the project's instance runs.
- **A project registered on another MCP pair keeps it.** When an older setup registered a different pair than the
  project's `.mcp.json` declares, the registry wins, setup rewrites `.mcp.json`, and servers left on the old pair
  stay up, unused, until a reboot. **Workaround:**
  `DEEPMAP_SEARCH_PORT=<old> DEEPMAP_EMBED_PORT=<old+1> python <plugin folder>/build/provision.py mcp-stop`.
- **Single-project stores from before per-project instances have edge cases.** A populated store with no owner
  stamp cannot be proven, so the project gets its own instance and a full re-sync, and the old store stays on disk.
  When another program holds the base ports, that old store is never started. After the real owner re-adopts 7687,
  another project's stale claim stays until that project runs `/deep-map:setup` (its heal and CLI refuse until
  then). **Workaround:** run `/deep-map:setup` in each affected project; remove an orphaned old store by hand once
  you have checked it.
- **Process identity is strict.** DeepMap signals a process only when an owner file proves it is this project's
  (pid, creation time, project key). A pid whose creation time cannot be read is never signalled (`stop` and
  `mcp-stop` report "not stopped"), and stale `instance-<hex>.lock` files are not collected.
  **Workaround:** stop the process by hand.
- **Restarting servers onto new plugin code needs proof too.** heal and `mcp-start` restart this project's servers
  onto newer code only when they are provably this project's. An equal version from another plugin folder never
  restarts them, and an in-place update that keeps file times is not detected. **Workaround:** `/deep-map:setup`
  restarts this project's servers; `provision.py mcp-stop <root>` stops them.
- **Session start can wait up to about 3 s** while this project's servers are down, because the start note waits
  for this session's heal (`DEEPMAP_NOTIFY_HEAL_WAIT`, default 3, at most 6, 0 turns it off).
- **Stopping.** On POSIX, `stop` gives Neo4j 5 s of SIGTERM before SIGKILL; Windows force-kills. On a non-English
  Windows, `taskkill`'s report cannot be read, so a stop that raced Neo4j's own exit can report "stop FAILED"; the
  next stop clears the stale pid file.

## Extraction and languages

- **The engine and its range.** DeepMap extracts code with the graphify tree-sitter engine, pinned to
  `graphifyy>=0.8.39,<0.8.52` and tested end to end on 0.8.39 and 0.8.51 only. Inside the range: 0.8.48 is yanked;
  0.8.45 and later also skip a project directory named exactly like the engine's 12-hex output folder; TypeScript
  `const x = f<T>()` is typed `:Class`; CUDA needs 0.8.46+ and Metal 0.8.50+ (the verdict names unparsed files).
  Outside the range the skipped-directory rescue is off and the verdict is `DEGRADED`.
  **Workaround:** `"<python>" -m pip install --upgrade "graphifyy>=0.8.39,<0.8.52"`, then `/deep-map:refresh`.
- **Languages.** The engine parses 25 programming languages; 8 of them have their own end-to-end test suites
  (gated in `tests/graphify_multilang_gates.py`: Python, JavaScript, TypeScript, Go, Rust, Java, Ruby, C#), and
  search is proven end to end on 4 (Python, TypeScript, Go, Rust). The others go through the same pipeline but are
  not individually verified. See [multi-language-support.md](multi-language-support.md).
- **The `ast` fallback reads Python only.** The default extractor `auto` uses graphify and falls back to the
  Python-only `ast` extractor when graphify is missing, crashes, times out or yields no nodes; every other language
  is then absent and the verdict says so. `DEEPMAP_EXTRACTOR=graphify` never falls back.
- **Switching engines is deliberate.** A sync that resolves to `ast` over a graph graphify built refuses to push:
  the graph is kept, the verdict is `DEGRADED`, and the sync exits 4 (NOT PUSHED).
  **Workaround:** from the repo root, `deepmap_cli.py sync <root> --stale-cleanup --force-prune`.
- **Node types.** The default graph has `:Function`, `:Method`, `:Class` and `:Module` nodes. graphify exposes no
  decorator information, so a route handler is a `:Function` and routes are `ROUTES_TO` links (only the `ast` path
  makes `:Route` nodes).
- **Nested functions are not graph nodes.** A closure or decorator defined inside another function is reached only
  through its enclosing function. **Workaround:** search for the enclosing function.
- **Duplicate definitions at one scope collapse into one node (engine limit).** A function defined twice in a file
  (`if sys.platform == "win32":` / `else:`, `try:` / `except ImportError:`) is one node at the first definition's
  line, carrying the calls of both bodies. **Workaround:** read both definitions when a result lands on such a name.
- **CommonJS and prototype JavaScript yield no `:Method` nodes (engine limit).** ES-module `class { ... }` members
  become `Class.method` nodes, but `F.prototype.m = ...` / `module.exports = F` code flattens to module-level
  `:Function` nodes. Locked by `tests/graphify_multilang_gates.py` (`js_esm_class`, `js_commonjs`).
- **Summaries are short.** On the graphify path the default Python summary is the engine's docstring label, cut at
  80 characters (sometimes mid-word); the `ast` path writes clean first sentences. On the graphify path a Python
  module's own docstring is not used for its `:Module` summary. Non-Python summaries are derived from the name, so
  their embeddings sit close to a name match, and an all-non-ASCII name gets an empty fallback summary.
  `DEEPMAP_SUMMARY_NORMALIZE=1` (real first sentences), `DEEPMAP_MODULE_DOCSTRINGS=1` (module docstrings) and
  `DEEPMAP_MERGE_SUMMARIES=1` (recognizer facts folded in) are off by default because summaries feed the vector
  arm. **Workaround:** query by intent rather than docstring wording; `deepmap enrich` writes richer summaries for
  undocumented code (see "Enrichment").
- **A Ruby heredoc can truncate a file (engine limit).** tree-sitter-ruby can misparse a heredoc and drop the rest
  of that file's symbols, and the verdict cannot detect it reliably. **Workaround:** Grep for a missing Ruby symbol.
- **Pascal/Delphi.** Designer and package files (`.dfm`, `.lfm`, `.lpk`) are data and are not mapped, so
  form-to-unit wiring is not in the graph. A unit's scope node appears both as a `:Module` and a `:Class`.
- **Test code is indexed with library code,** so on large repositories search can surface test files.
  `DEEPMAP_EXCLUDE_TESTS=1` (off by default) drops test code: files under a test directory (`test`, `tests`,
  `__tests__`, `spec`, `specs`, `testing`, `e2e`, `__mocks__`, or a .NET test-project directory whose name ends with
  `.Tests`, `.Test`, `.UnitTests`, `.UnitTest`, `.IntegrationTests`, `.IntegrationTest`, `.FunctionalTests`,
  `.AcceptanceTests` or `.Specs`, such as `src/Shop.Tests/`) and the usual test filename conventions. The JUnit
  `*Test.java` rule is case-sensitive on purpose, so `Latest.java` stays, and `Shop.Testing/` (a test-support
  library) stays production. The same rule decides what counts as a test caller or a test implementation for the
  opt-in call-linking modes.
- **Some directories graphify skips are never re-included.** Git-tracked source that graphify skips is rescued by
  default, except the virtualenv family (`env`, `.env`, `venv`, `.venv`, `*_env`, `*_venv`, `site-packages`,
  `lib64`), `dist/`, and vendor and tool directories (`node_modules`, `vendor`, `public`, `coverage`, caches).
  Tracked hand-written source there makes the verdict `DEGRADED` with a rename remedy; in a non-git tree nothing is
  re-included. The framework recognizers skip the same directories, worktree checkouts (`.worktrees/`,
  `.claude/worktrees/<name>/`) and what sync's vendor filter drops (minified bundles, the `.gitignore`'s bare
  directory names), unless `DEEPMAP_INCLUDE_VENDOR=1`; with that flag, a typed link declared in vendored code can
  resolve onto a same-named project class (`INFERRED`).
  **Workaround:** rename the directory, or put a non-git project under git and commit.
- **On the `ast` path, worktrees and virtualenvs inside the project are extracted.** The `ast` extractor reads every
  `.py` except under `.venv/` and `__pycache__/`, so a worktree checkout or an un-ignored `venv/` duplicates the
  project's symbols. **Workaround:** keep graphify installed; on the `ast` path, add the bare directory name
  (`worktrees/`, `venv/`) to `.gitignore`.
- **`:IMPORTS` links fan out by name in a few cases.** On the graphify path an import binds the module the engine
  resolved (`EXTRACTED`). Name-form imports (the `ast` path, `DEEPMAP_RESOLVED_IMPORTS=0`, names the engine did not
  resolve) bind by the module's last dotted segment and can be `AMBIGUOUS`; external imports bind nothing. Imports
  are not used in ranking or in the default impact walk.
- **`ast`-path details.** Node keys are line-based, so a line shift re-keys a node (its embedding is reused). A
  nested function's calls are attributed to the enclosing function. A lone carriage return (a classic-Mac line
  ending) in a Python file can make the syntax-tree corrections read the wrong line for the rest of that file.
- **graphify still writes `<root>/graphify-out/manifest.json`** (a hard-coded path; the real output lives in
  `<DEEPMAP_HOME>/gout/<key>/`). Sync drops a self-ignoring `.gitignore` into `graphify-out/` and `.deepmap/` so
  neither shows in `git status` (`DEEPMAP_AUTO_GITIGNORE=0` turns that off), but git does not untrack files already
  committed. **Workaround:** run `git rm -r --cached graphify-out .deepmap` once.
- **Non-git projects.** `--stale-cleanup` needs a project-root marker (`.git`, `pyproject.toml`, `package.json`,
  `go.mod`, `Cargo.toml` and others), because the prune deletes graph-wide; a Delphi tree has no fixed-name
  marker. Provenance falls back to a content hash, and the git post-commit hook needs git. A stale-cleanup refused
  for a missing marker is recorded (`stale_cleanup_skipped: "no-root-marker"` in `.deepmap/sync_last.json` and
  `.deepmap/graph_build.json`); the verdict is DEGRADED ("STALE NODES KEPT") only when the graph holds more nodes
  than the sync pushed, and otherwise the sync says the stale-cleanup did not run.
  **Workaround:** run from the repository root, or add an empty `.deepmap-root` file at the project root;
  `--force-prune` cannot override this refusal.
- **Incremental extraction is opt-in and `ast`-only.** `DEEPMAP_INCREMENTAL_SYNC=1` skips re-parsing unchanged files
  on the `ast` path and builds the same graph. graphify runs as one whole-tree process, so with it every sync
  extracts the whole tree, and the push to Neo4j is always a full push.
- **SQL is read only when you switch it on.** DeepMap's own SQL reader is off by default until it passes its
  measurement on real SQL code; with it off, `deepmap_search`, `deepmap_impact` and `deepmap_cypher` never return a
  table, view, stored procedure or other SQL object. With it on (`DEEPMAP_SQL_DECLARATIONS=1`, then
  `/deep-map:refresh`), SQL objects are declarations only: there are no SQL links, so impact analysis cannot list who
  reads, writes or runs a table or procedure; SQL outside `.sql` files (strings in code, `.txt` scripts) and SQL built
  at run time are not read; the reader runs only when the graphify engine builds the graph. **Workaround:** use Grep
  for SQL and for its users.
- **Some files are not parsed at all.** R, Perl, Python stub (`.pyi`) and CommonJS (`.cjs`) files, DreamMaker
  (`.dm`) files without their optional parser, and `.sql` files while the SQL reader is off are not in the graph.
  They are not counted for coverage, so they never make the verdict `DEGRADED`; `/deep-map:status` and
  `/deep-map:doctor` list them with the reason, and the session-start note and the reminder before the session's
  first Grep or Glob tell Claude to use Grep or Read there.
- **The verdict can only flag what it can see.** A language whose files are on disk but not in the graph makes the
  verdict `DEGRADED` with a `LOW COVERAGE` reason, and the session-start note tells the agent that an empty result
  in that layer means not indexed, not absent. **Workaround:** check the per-language `N files, M nodes` table in
  `/deep-map:doctor` before concluding that code does not exist.

## Call linking and confidence

- **Calls are linked by name by default.** The default push resolves a callee by name across the workspace, not by
  scope or imports, so a call to a common name (`run`, `check`) links to every symbol of that name. Default `CALLS`
  links are therefore `INFERRED` or `AMBIGUOUS`, never `EXTRACTED`, and most are `AMBIGUOUS` (83 to 98% in three
  measured codebases). The rule is also language-blind: a Python call can bind to a same-named function in a Ruby
  file. The graph-weighted search arm counts this inflated in-degree.
  **Workaround:** read the call site behind an `AMBIGUOUS` link; for an exact symbol, use Grep or `deepmap_impact`
  on the qualified name.
- **Calls on objects are mostly missing.** The engine emits no call through a parameter (`def f(inv): inv.m()`), a
  field (`_service.Find`), a Go, Rust or TypeScript receiver, a trait or a namespace, and misses calls inside
  nested closures, module-level calls, and `alias.f()` / `module.f()` in Python. Calls on `self`, `cls`, `super()`
  and `this` are kept when the method is in the same file. On graphify 0.8.47 and later, `alias.func()` gets no link
  at all in a file that has two or more methods of that name. C# has its own entry: "C# members and calls" below.
  **Workaround:** Grep for the callee name before calling a symbol a leaf.
- **A receiver-blind name can bind the wrong method.** A compiled regex's `pattern.search(...)` binds by bare name
  to a project method named `search`, graded `INFERRED` when only one exists. **Workaround:** read the call site
  behind an `INFERRED` receiver call.
- **The `self.m()` re-bind is an approximation.** A call through `self` or `super()` binds to the method of the
  caller's class. When that class cannot be read in the file, the call becomes a bare name resolving to `:Method`
  and module-level `:Function` nodes, graded as a name fan-out. A call through an imported module receiver
  (`subprocess.run(...)` in a file whose classes define `run`) still fans out to project symbols named `run`. When
  one caller makes two such calls on consecutive lines (`Base.__init__(self)` then `Mixin.__init__(self)`) only the
  first is bound, and both are dropped when they share a line. A dunder called through an instance attribute is
  dropped. `DEEPMAP_SELF_CALL_REBIND=0` restores the engine's own binding.
- **A bare call can land on a `:Module` node.** A module is named by its file stem, so `summarize()` matches both
  the function and the `summarize` module, which inflates the module's in-degree and adds an `AMBIGUOUS` impact row.
- **The bare-call rules keep some calls they could drop.** A `/* */` comment ending the previous line is not looked
  through, Go dot-imports are not considered, and in JavaScript and TypeScript any import-line mention (even
  `import { type get }`) counts as a value. Non-Python text spans end at the next sibling node, so a file's last node
  runs to the end of the file.
- **Default-on corrections you can switch off:** `DEEPMAP_RESCUE_SKIPPED_DIRS=0`, `DEEPMAP_SELF_CALL_REBIND=0`,
  `DEEPMAP_FALSE_CALL_FILTER=0`, `DEEPMAP_CLEAN_OWNER_NAMES=0` (slice, tuple and fn-pointer owners such as `[u32]`
  keep their raw labels even when it is on), `DEEPMAP_RESOLVED_IMPORTS=0` and `DEEPMAP_CASE_INSENSITIVE_EXT=0`.
- **Opt-in call-linking modes, all off by default.** None has passed the project's pre-registered checks for a
  default change (`benchmark/call_linking/PREREG.md`, `DECISION.md`).
  - `DEEPMAP_PRECISE_CALLS=1` (or per language family, `python,js`) keeps the engine's resolved target and removes
    most `AMBIGUOUS` links. Its receiver rule judges Python, Go, Rust and JS/TS callers only, `:Class` targets are
    not judged, and it drops a true call that the file also makes through a renamed import. On a virtual or
    interface call, the target it keeps is one method that can run, not necessarily the only one.
  - `DEEPMAP_GRAPHIFY_CALLS_PLUS=1` (Python) recovers `module.f()`, `alias.f()`, module-level and nested-closure
    calls, skipping test callers unless `DEEPMAP_GRAPHIFY_CALLS_PLUS_TESTS=1` (callers in `examples/` and `docs/`
    count as production code). A def the engine merged has its calls attributed to the enclosing `:Module`.
  - `DEEPMAP_CALLS_FAMILY_FILTER=1` drops name matches across language families.
  - `DEEPMAP_DROP_APP_TO_TEST_CALLS=1` drops links from production code into test-only helpers.
  - With these modes on, a module-level rebinding of an imported name (`h = Wrapper()`, or a `del` of the alias)
    makes every receiver call through it a fan-out, even one that runs before the rebinding.

### Typed-receiver call linking (opt-in)

`DEEPMAP_TYPED_CALLS=python,typescript,javascript` links calls on objects (`order.save()`, `self.repo.find()`,
`this.repo.find()`, `mod.f()`, `new Order()`) by the receiver's declared or constructed type, read from the caller's
own source (`build/typed_calls.py`); `DEEPMAP_CALLS_AUDIT=1` is its grade-only companion. Both are off by default:
on 10 codebases no added link was refuted by the reference resolvers (jedi and the TypeScript checker), but it did
not pass every pre-registered check (`benchmark/call_linking/DECISION.md`). With every call-linking flag unset, the
graph is byte-identical to a build without them. **To use it:** set the variables in the environment Claude Code
runs in, then `/deep-map:refresh`.

It reads a parameter's declared type (Python and TypeScript), a local, a field, a declared return type, `self` /
`cls` / `this` / `super()`, a class, a module, and a construction. Python reads `Optional[X]`, `X | None` and unions
of up to 4 classes; TypeScript and JavaScript only `T | null | undefined`. What it never does (such calls keep the
default name match, or stay missing):

- Untyped parameters (every JavaScript parameter), `Any` / `object` / `any` / `unknown`, and a call on whatever an
  external call returns (`re.compile(...).search()`) give no type.
- No override or interface fan-out in Python, TypeScript and JavaScript (the C# reader has its own dispatch rules,
  below): a call through a TypeScript interface, a `typing.Protocol` or a class declared under `if TYPE_CHECKING:`
  is never keyed, and a typed call of more than 4 targets is not linked.
- No flow narrowing: `isinstance`, `instanceof`, type guards and `as` casts do not type a name, and a name bound by a
  loop, `with`, `except`, a walrus, tuple unpacking or `del` (in TypeScript and JavaScript, any reassignment) is
  untyped. A module loaded at run time (`importlib.import_module(...)`, a computed `require()`) stays unknown.
- Only functions, methods and routes are callers; module-level and class-body calls are not linked.
- Callers in tests, examples or docs get corrections only, unless `DEEPMAP_TYPED_CALLS_TESTS=1` /
  `DEEPMAP_TYPED_CALLS_NONPROD=1`.
- A name's existing name-matched links are replaced only when every call of that name in the caller is accounted
  for and the caller spans at most 5,000 lines; otherwise the typed link is added beside them.

Go, Java, Rust and Ruby have no reader yet: naming one changes nothing (the manifest records `no reader`). C# has
its own opt-in reader (see "C# members and calls" below). With the flags on, sync takes a few percent longer, and
two typed links on the zod codebase differ between engine 0.8.39 and 0.8.51. The flags shape the graph, so the
background refresh skips (`recipe:settings`) a session whose value differs from the last build until
`/deep-map:refresh`.

### C# members and calls

- **Computed properties and constructors are nodes; nothing links calls into them.** The engine makes no node for a
  C# property or constructor and reads calls only inside method bodies. DeepMap adds them from one declaration-only
  parse of each `.cs` file the engine kept (on by default): a computed property (an expression body `=> ...`, or an
  accessor with a body) is a `:Method Owner.Prop` node, and one constructor per type is `:Method Owner.Owner`, the
  way Java constructors appear: the type's first instance constructor, or its static constructor when it has no
  instance one. Its other constructors get no node, and the calls inside them are never linked. The nodes are found
  by search, but reading a property is never a link, a bare call never binds to them by name, and `new Order()` is
  not linked to the constructor node, so `deepmap_impact` on a property or a constructor is always empty. By default
  the calls made inside them are not linked either.
  **Workaround:** grep the property name to find its readers.
- **`DEEPMAP_CSHARP_MEMBERS=0`** turns this off (and the record and struct owners below): each `.cs` file is then
  mapped exactly as the engine left it. After the next sync an existing graph has that shape apart from stale
  nodes: a node whose key changed label is replaced (see below), and the property, constructor and owner nodes the
  step added are stale then, which only a sync that prunes (`/deep-map:refresh`) removes.
- **Not nodes:** auto-properties (`{ get; set; }`, `{ get; init; }`), indexers, operators, events, destructors,
  primary constructors, a type's other constructors and local functions. Calls inside indexers, operators, event
  accessors and destructors are not linked.
- **Calls into another file through a receiver or a type name are never linked by default (engine limit, on
  graphify 0.8.39 and 0.8.51).** The engine treats every dotted C# call (`_service.Find()`, `Helper.Build()`,
  `this.Save()`, `x?.M()`) as a member call and resolves member calls only inside their own file, by name whatever
  the receiver. A bare call (`Validate(x)`) links only when no other symbol in the project has that name, and a
  generic call (`Get<Order>()`) never links. So a handler that reaches its services through injected fields shows no
  outgoing calls, and those services show no callers from it.
  **Workaround:** grep the method name, or opt in to the C# reader below.
- **`deepmap_impact` says so.** When the queried name matches a C# symbol, the answer carries a C# note: an empty
  answer is not proof and the note names what the graph cannot see; a non-empty answer is marked as a lower bound.
  When the last sync ran the C# reader, the note describes that reader instead: calls through a receiver or a type
  name are linked when the source states the receiver's type, but some can still be missing, and the note names the
  main cases (an awaited value, a cast, an untyped lambda parameter, a static field in a static method, `dynamic`,
  reflection, delegates, a mediator, Razor files, test, example and docs callers with the flag that links each,
  overloaded callers, methods holding `#if`, a type's other constructors, overloads chosen by argument type, more
  than 4 implementations, property reads). When the graph was built with `DEEPMAP_TYPED_CALLS_SHAPES` or
  `DEEPMAP_TYPED_CALLS_IFACE` narrowing the reader, the note adds that more C# calls are unlinked than it lists. The
  full list is the reader's entry below. When every match is a computed property, the note says instead that
  reading a property is never a link, with or without the reader, so the code that reads it is not listed (a
  property whose summary was enriched, or a name that is also a method elsewhere, keeps the call wording).
- **MediatR `Send` to `Handle` is not linked,** with or without the reader, so a handler dispatched through a
  mediator has no callers in the graph. **Workaround:** grep the request type to find who sends it.
- **Opt-in: the C# typed-receiver reader.** Name `csharp` in `DEEPMAP_TYPED_CALLS` (for example
  `python,typescript,javascript,csharp`; `=1` selects Python, TypeScript and JavaScript only), then `/deep-map:refresh`.
  It types each receiver from the caller's own `.cs` source (MSBuild's project files decide a file's usings, see below):
  a field, property or primary-constructor parameter by its declared type, a parameter, a local, `this.` and `base.`
  (into another part of a partial class, or a base class in another file), `x?.M()`, `M<T>()`, a static `Type.M()`,
  `using static` and extension methods. It links the calls inside a computed property's accessors and inside the one
  constructor that has a node. It follows C# name lookup where that decides the target: a local binds only inside its
  own block; a type name means a nested type of the enclosing class, its base classes in the project or an outer class
  before a namespace-level type; a `using X = ...` alias holds only inside its own namespace declaration and loses to a
  nearer namespace's member or using; a namespace block's usings apply inside that block only, and a `using` or alias
  written there names what C# finds from that block's namespace outward (`global::` is absolute); a block's `using
  static` holds inside that block only, and a bare call is not typed through it when a type enclosing the caller
  declares or inherits a member of that name, since C# looks there first, or when `using static` directives at two
  levels both supply the name; a type's generic arity is part of its name (`Result` and `Result<T>` are two types); a
  `file` class is seen in its own file only; and a method proven inaccessible at the call site, or an explicit interface
  implementation, gives way to the base's method. `InternalsVisibleTo` counts wherever MSBuild and the compiler read it
  (the project's C# files and the files it links in, its project files, `Directory.Build.*` and
  `Directory.Packages.props`); where the reader cannot be sure, the method stays the target.
  - **Interface dispatch:** a call through an interface links the interface member (`INFERRED`) plus the
    implementations in the project: one `INFERRED`, 2 to 4 `AMBIGUOUS` each, more than 4 none. An implementation is
    the method the class runs for that member, by C#'s mapping rules: a `new` method of the same name in a subclass
    that does not list the interface again is not one, an explicit implementation wins inside its class and nothing
    overrides it, and a member that is not public never implements implicitly. A static interface member has none
    (one with a body is an exact call). A receiver typed `IRepo<Order>` skips a class that implements only
    `IRepo<User>`, and `Box<int?>` skips `: Box<int>` (`Nullable<int>` reads as `int?`, `Int32` as `int`). A class
    that reaches one generic interface by two paths with different type arguments is unknown: the links are added and
    nothing is replaced.
  - **Virtual dispatch:** a call of a virtual, abstract or override method (not sealed) through a typed receiver or
    `this` links the method it names (`INFERRED`, never `EXTRACTED`) plus the overrides with a body in project
    subclasses, counted the same way. `base.M()`, `new T().M()`, a `var x = new T()` / `T x = new()` local never
    reassigned, a struct and a sealed or static class are exact, with no dispatch.
  - When the whole set is known (at most 4, every implementing class read), these links replace the engine's own
    name match for that call: a same-named method that does not implement the member loses its link. Otherwise they
    are added beside it.
  - An implementation or override in test code counts only for a test caller (test code as `DEEPMAP_EXCLUDE_TESTS`
    defines it, .NET test-project directories such as `Shop.Tests/` included).
  - `DEEPMAP_TYPED_CALLS_IFACE` selects what both kinds of dispatch link: unset or `1` / `true` / `yes` / `on` =
    everything; `static` = the interface member or the named virtual method only; `impl` = the implementations or
    overrides only; `static,impl` = both; `0` / `false` / `no` / `off` = neither. Unknown words are ignored (and
    listed as `unknown_iface` in `.deepmap/graph_build.json`); a value that names no mode selects both.

  Its gaps:
  - **Unproven means added, never replaced.** Parameter types count as one type, or as two, only when that is proven (a
    method's own generic arity and a `params` parameter are part of its signature). Anything unproven leaves the call's
    typed links added beside the engine's match: a call may stay untyped, and only a proof replaces a link. An alias or
    a name the reader cannot follow is unknown even against itself, and so is a generic, array or nullable type whose
    name the reader cannot place.
  - **More than 4** implementations or overrides link the named member only.
  - **Overloads.** A call whose method has a rival overload the compiler may pick instead (a base-class method of
    that name and argument count, or an extension method) is linked only when the argument types are plain to read
    (literals, values declared with a built-in type, project class instances), and never `EXTRACTED`. The receiver's
    generic arguments count here (`Repo<Order> _r; _r.Save(o)` reads `Save(T)` as `Save(Order)`), but not for override
    slots or interface mappings. When every overload up to a base class is proven not to apply and the base has one
    the arguments fit, the call links that one, as `INFERRED` and always beside the engine's match, never replacing
    it. Nothing is proven for `null`, a lambda, a method group, a `params` element, a numeric literal, `dynamic`, a
    `ref readonly` parameter or a type with a conversion operator; those calls stay as they were.
  - **External type names** are compared the way C# looks them up: each enclosing namespace, its usings, and last the
    file's usings with the project's global ones and what MSBuild adds. A namespace may hold external types when its
    first segment is `System`, `Microsoft`, the first segment of a package a project file names, or a first segment no
    project is named after (no project's AssemblyName, RootNamespace or file name starts with it), even if the project
    declares types in it. That costs recall on purpose, and only the interface member is linked, nothing removed:
    - in `namespace Microsoft.Extensions.Logging.Custom`, or under `Acme.*` with a package `Acme.Core`, an unqualified
      name the project does not declare there is unknown (a short built-in list of framework types still decides at
      its own namespace);
    - in a tree with no `.csproj`, or one whose project files are named after something other than its namespaces,
      nothing that rests on a namespace is proven;
    - with usings inside the namespace block (StyleCop's SA1200 style), a block that imports such a namespace
      (`namespace App.Jobs { using System.Timers; ... }`) makes a project type of an enclosing namespace (`App.Timer`)
      unknown there, since the import may hold a type of that name, which C# would pick first;
    - a `using`, `using static` or alias inside a namespace block whose meaning the reader cannot settle (a nearer
      namespace external code may fill, or a `using static` whose first segment is not surely a top-level namespace such
      as `System` or `Microsoft`) leaves the names it could supply unknown;
    - a `using static` at a level of its own, a `using` inside `#if`, a class in a file its own project removes, and a
      partial class part that two projects compile stay unknown.

    Not seen: a package whose namespaces do not start with its id's first segment, a package only another package
    pulls in, an external type in the global namespace, a project `delegate` type, and compiled source the reader does
    not see (vendored code in a skipped directory, generated code, a source generator's output) that declares a
    namespace the project also declares, when no package id names it. A qualified name such as `Acme.Core.Order` is
    read as the project's even past a block's using of external code (it compiles otherwise only if that code nests
    the whole name). There a wrong match could replace a true link.
  - **A base the reader cannot resolve** (an alias it cannot follow, a qualified or generic name) may be any project
    type of that name, or the target of an alias of that name, with its own bases. Where such a type could implement the
    interface, override the method, supply the member a bare name reaches or add a rival overload, the call keeps the
    engine's match and its typed links are only added, or it stays untyped. The same goes for a class that lists an
    interface whose own base the reader cannot resolve, when that base could lead to the interface being called: the
    class may implement that interface again, so if it, or a class between it and the base class that implements the
    interface, declares the method, the call keeps the engine's match. A base the reader cannot read at all (a
    `global::`-qualified one), or one that may be a type the reader cannot read (two same-named types in one file, a
    partial class it cannot merge), lets the class derive from any project type: then no interface call in the
    repository whose member another project type also defines replaces the engine's match, and neither does a virtual
    call whose method that class may override. The reader assumes that an external base named like an interface
    (`IThing`) is one, so it brings no member a bare name could reach.
  - **What MSBuild adds** is read per `.csproj`, only with this reader on: the project, the nearest
    `Directory.Build.props`, `Directory.Packages.props` and `Directory.Build.targets`, plain `<Import>`s,
    `ImplicitUsings`, `<Using>` items, simple conditions (two boolean-like values compared as booleans, as MSBuild
    does), the implicit-using lists of the `Microsoft.NET.Sdk` family, package ids (`PackageReference` and its siblings,
    `packages.config`), `<Compile Include>` links and `<Compile Remove>` items. Two projects' readings are compared only
    when neither uses a package (in its project file, its imports or the projects it references): a package can add or
    remove usings unseen, even when two projects use the same ones. A `GlobalPackageReference` gives every project a
    package. Whatever it cannot read makes that project's reading incomplete, and the names it would decide stay
    unknown: another SDK (Aspire, MSTest, a workload), MAUI, Windows Forms or WPF, a computed, missing or out-of-tree
    `<Import>`, a `Custom*` import hook, `Exists()` or a property function in a condition, an environment or SDK-defined
    property, a wildcard in a `<Using>`, a `<Using>` inside a `<Target>`, a Remove, Exclude or Update written as a path,
    a `global::` prefix on a `<Using Include>`, a `Directory.Build.rsp`, `TreatAsLocalProperty`, an SDK named in an
    imported file, a `Directory.Build.*` file found only in another letter case, and two projects in one directory. A
    condition on `$(Configuration)` or `$(Platform)` counts as possible, never certain, since `dotnet build` builds
    Debug by default. A file in no project's directory reads incomplete too when the tree holds any `.csproj`, and so
    does a file another project also compiles through a link; such a file counts as certain only what every project
    compiling it gives. A `global using` in a file a project links in, or a link to a file that is not in the tree
    (generated, or outside it), leaves that project's reading incomplete. A `<Compile Include>` the reader cannot place
    counts as reaching everywhere: the other projects' files then keep their links added, never replaced. Never read:
    global usings a NuGet package's own `build/` props add (xunit-style), a project's default `**/*.cs` glob reaching a
    project nested under it, and project references.
  - **Other limits.** Calls in a type's other constructors and in Razor files (`.razor`, `.cshtml`) are not linked;
    callers in tests, examples or docs get corrections only unless `DEEPMAP_TYPED_CALLS_TESTS=1` /
    `DEEPMAP_TYPED_CALLS_NONPROD=1`; `dynamic`, reflection, delegates and a mediator are not followed; an overloaded
    caller is never typed; the result of `await`, a cast or `as` is untyped, and so is a static field read from a
    static method; an external base class's members, overloads and nested types are not read.
  - **Wrong links it can still give (rare).** Each of these can add a wrong link and, where that wrong proof replaces
    the engine's match, remove a right one:
    - a C# 12 primary-constructor parameter hidden by a base member of the same name (compiler warning CS9179);
    - a derived class's less accessible member read from outside, where C# picks the base's;
    - an explicit interface property read as a member of its class;
    - a C# 11 `file` type that gives one alias text two meanings;
    - types and members declared inside an inactive `#if` branch, read as present;
    - a type from a project the caller does not reference, or from a `.cs` file in no project (a docs snippet), hiding
      an imported type; a namespace only such code declares counts as existing too;
    - a type in a file the build may not compile;
    - `obj/` read as source when it is not gitignored;
    - a `<Reference>` written as a path;
    - a package's `.targets` that removes usings or turns `ImplicitUsings` off;
    - a disabled `#if` region that starts a verbatim or raw string, which hides the rest of the file;
    - a `.csproj.user` file (Visual Studio's per-user project file), which MSBuild reads and the reader does not.
      Measured with the .NET 8 SDK: a `<Using>`, a package, an `ImplicitUsings` setting or a `<Using Remove>` written
      there changed the build's global usings, so the reader can miss a using, treat a project whose only package is
      listed there as one without packages, or count a removed using as certain;
    - an `extern alias` read as a namespace.

    A better non-virtual base overload beside a derived override of another overload can add a wrong weak link, with
    nothing removed. A verbatim identifier (`@event`) in a namespace name only costs links. A base the reader cannot
    resolve also counts as possibly implementing an interface when a project type of its name implements it with other
    type arguments (`IRepo<Cart>` where the call is on `IRepo<Order>`), although the other thing it could be, a type
    from outside the project, cannot implement this project's interfaces at all; such a call keeps the engine's match
    where a tighter rule could replace it. It adds no wrong link of its own.

  It stays off by default until its own pre-registered decision, measured against Roslyn, passes; with it unset the
  graph is identical to a build without it.
- **Records and structs on graphify 0.8.39.** On 0.8.39, records, record structs, structs and enums have no class
  node; DeepMap adds one per type name and file and names their methods `Owner.method`, as 0.8.51 does by itself.
  Two same-named methods in one file that 0.8.39 merged get separate nodes, but the calls inside the second stay
  attributed to the first. A record, struct or enum declared before a same-named class of its file folds into that
  class's node.
- **A node whose key changes label is replaced.** On 0.8.39 those methods keep their keys but move from `:Function`
  to `:Method` (and back with `DEEPMAP_CSHARP_MEMBERS=0`). Every sync removes such an old node before it writes,
  whatever it decides about stale nodes, re-creates the full-text index and prints how many it removed
  (`relabeled` in `.deepmap/sync_last.json`); the replacing node is embedded once. The same pass removes a stale
  class node that duplicates a class this sync wrote under another key (same file, name and line). On 0.8.51 no key
  changes label.

## Framework wiring per ecosystem

DeepMap's own recognizers add typed links (routes, dependency injection, ORM relations and cascades, hooks,
schedules, events, inheritance) for Python, Ruby/Rails, C#/.NET, Java, Go and TypeScript/JavaScript. They read
source with patterns, not a compiler, and drop a link they cannot resolve rather than invent one.

### Rules for every recognizer

- **No link across language families.** Java and Kotlin, C# and VB, and JavaScript and TypeScript each count as one
  family.
- **One link per (source, target, type).** A handler routed several times keeps the first route's method and path,
  or the routes fold into one link with a `routes` / `paths` list. Two relations from one entity to the same entity
  (`author` and `reviewer`, both to `User`) are one `REFERENCES` link.
- **A typed link whose source is not a node in its declaring file is dropped**, except `CASCADES`, `HAS_MANY` and
  `REFERENCES`, which may resolve to a single same-family `:Class`. A type falls back to its file's `:Module` only
  when no `:Class` node exists.
- **ORM associations are not in the default impact walk.** `HAS_MANY` and `REFERENCES` are in the graph, but only
  `CASCADES` joins the default walk; `DEEPMAP_IMPACT_PROFILE=rails` (or `orm`, `associations`; the MCP tool honours
  it too) adds them. A set-null rule is recorded as a cascade link, so open the declaration to check which it is.
- **Disabling a recognizer** (`DEEPMAP_RECOGNIZERS`) prunes its typed links on the next sync, and an unknown name
  there prunes every recognizer's links. Leftover nodes stay until `--stale-cleanup` (`/deep-map:refresh`).
- **Text in string literals can still match** in the line-based recognizers (Ruby, C#, Java, Go): comments are
  stripped first, but string contents are not. A wrong match usually resolves to nothing and drops.

### Python

Flask and FastAPI routes and mounts, FastAPI dependencies, lifecycle hooks, Celery tasks, Django and blinker
signals, inheritance, Django URLconfs, SQLAlchemy and Django ORM relations and cascades.

- **Signal handlers name a symbol only when the recognizer can see it.** A `.connect(h)` / `.on(evt, h)` /
  `register_hook` / `add_job` handler that is a parameter, a local, a module variable, a call result or a literal
  gives no link, and a module's `connect()` (`sqlite3.connect(path)`) is never a signal. A module-level signal API
  called on an imported module (`louie.connect(...)`, `import pydispatch.dispatcher as dispatcher`) is read as
  PEP 249's `connect`; the usual `from pydispatch import dispatcher` form is recognized. A handler passed by keyword
  (`sig.connect(receiver=h)`) is not recognized.
- **Mounts.** A router mounted into a router in an excluded file (a vendored package, an in-tree virtualenv) does
  not inherit that parent's prefix. A mount declared in a test file can add to a production route's `paths`, and
  can become its primary `path`, even with `DEEPMAP_EXCLUDE_TESTS=1` (the TypeScript / JavaScript recognizer
  ignores mounts in test files).

### Django URLconfs, SQLAlchemy 2.0 typed models and Python ORM cascades

- **Django routes.** Only module-level pattern lists are read; lists built in a function or a comprehension, or
  returned by a helper (`ModelAdmin.get_urls()`), are not. Not read: Django REST framework routers, Django Ninja,
  Wagtail and CMS URL hooks, and pre-1.10 string views. `ROOT_URLCONF` is not read, so a URLconf nothing includes
  counts as a root, and the `i18n_patterns` language prefix is named in `prefix_unresolved`. Every Django route
  records method `*`, and a class-based view's route binds the class, not its `get` / `post`. A `re_path()` regex
  is kept without its `^` / `$` anchors; a non-literal pattern records no `path`. `path`, `re_path`, `url` and
  `include` count only when imported from `django.urls` / `django.conf.urls`. View decorators are a fixed list
  (`login_required`, `cache_page`, `csrf_exempt` and similar); any other wrapping call drops the route. An imported
  view whose definition cannot be followed (a name bound twice, a PEP 562 `__getattr__`, more than six re-export
  hops) falls back to the name rule.
- **SQLAlchemy.** A typed relation needs SQLAlchemy's `Mapped` (imported from SQLAlchemy or re-exported by a project
  module). SQLModel's `Relationship()` / `Field(foreign_key=..., ondelete=...)` are not read. A classic
  `relationship()` stays `HAS_MANY` whatever its direction, and `backref=` adds no reverse link. A
  `ForeignKey("table.col")` names the class whose literal `__tablename__` is that table; computed table names,
  `__table__ = Table(...)`, `ForeignKeyConstraint` in `__table_args__`, module-level `Table()` objects and
  `declared_attr` relationships on mixins are not read.
- **Django ORM.** `on_delete=CASCADE` / `SET_NULL` are cascades; `PROTECT`, `RESTRICT`, `DO_NOTHING`, `SET_DEFAULT`
  and `SET(...)` are not. A lazy `'app_label.Model'` binds in `<app_label>/models.py`, found by directory name, so an
  app whose `AppConfig.label` differs from its directory binds nothing. `settings.AUTH_USER_MODEL` and an inline
  `get_user_model()` name no model; a module-level `User = get_user_model()` resolves by name. `GenericForeignKey`
  and modelcluster's `ParentalKey` are not read.
- **Imports from outside the tree bind nothing.** A model or view imported from outside the tree (Django's own
  `auth.Group`, `TemplateView`) gives no link, never a same-named project class. `ON DELETE` rules that live only in
  migrations or the database are not read. If the recognizer's post-pass fails (stderr: "Python post-pass ...
  skipped"), every import it did not place falls back to the name rule.

### Ruby and Rails

Associations (`HAS_MANY` / `REFERENCES`), `dependent:` cascades, callbacks (`HOOKS`), inheritance,
`config/routes.rb` routes, model-level `scope`s as `:Method` nodes, and `.erb` views (`RENDERS`). There is no Ruby
runtime.

- The inflector is a small rules-and-irregulars table; a miss drops the link (`class_name:` always overrides).
- DSL inside metaprogramming (`class_eval`, concerns included at run time) is invisible.
- `config/routes.rb` reads `resources` / `resource` (with `only:`, `except:`, `controller:`), `verb 'ctrl#action'`
  and `member` / `collection` actions. Active Scaffold's convention-generated routes are not emitted; a dynamic
  `verb 'ctrl/action'` route, a demodulized name that collides across namespaces (`Admin::Order` and `Order`) and
  non-literal options are `AMBIGUOUS`. `with_options only:` is not parsed.
- Attribution uses the most recent class, so DSL after a class's `end` in a multi-class file can be attributed to
  the wrong class.

### C# and .NET

EF Core relations and explicit `OnDelete(...)` cascades, ASP.NET Core attribute routes and minimal-API routes
(`MapGet`, `MapGroup` prefixes), DI registrations and constructor / primary-constructor injection, inheritance and
interfaces. Patterns, not Roslyn.

- **EF's by-convention cascade is not emitted;** only explicit `OnDelete(...)` configuration is read.
- **Fluent `HasMany` / `HasOne`** resolve through the navigation property's declared type; when it cannot be read,
  the property name is singularized (`LineItems` to `LineItem`), and a wrong guess drops the link. Partial classes
  (any name declared as `:Class` in more than one file) make a fluent cascade's source ambiguous, so it drops.
- **Route groups:** `var api = app.MapGroup(...)` and a single inline chain compose their prefix; a double inline
  chain composes only the innermost one, and a reference out of declaration order stays uncomposed. An inline
  `() =>` handler is anonymous and skipped.
- **A subdirectory sync** can prune a fluent cascade without recreating it, or leave a removed one stale, because
  the link is anchored in another directory. A full-repository sync (the default) is always correct.

### Java (Spring and JPA)

Spring MVC routes (`@GetMapping` and siblings, with a class-level `@RequestMapping` prefix), JPA / Hibernate
relations and cascades (`cascade = CascadeType.REMOVE` or `ALL`, `orphanRemoval = true`), constructor, `@Autowired`
and Lombok constructor injection, `@Scheduled`, `@EventListener`, inheritance and interfaces. Patterns, not javac.

- `PERSIST`, `MERGE`, `REFRESH` and `DETACH` cascades do not delete dependents, so they are not cascade links.
- Types are demodulized (`com.shop.Order` to `Order`), so same-named classes in different packages make a link
  `AMBIGUOUS`.
- Kotlin has no recognizer (nodes and calls only), and wiring declared outside annotations (XML configuration,
  functional route builders) is not read.

### Go

Gin, Echo, Fiber and Chi routes and `http.HandleFunc`, GORM associations and `constraint:OnDelete` cascades, struct
embedding (`INHERITS`), and `NewXxx(dep ...)` constructors (`DEPENDS_ON`). Patterns, not `go/types`.

- Go interface satisfaction is implicit, so `IMPLEMENTS` is never emitted.
- A route needs a path starting with `/` and a named handler; an inline `func(...) {}` handler is skipped.
- **GORM cascades follow the foreign key as far as one file shows it.** A has-many slice or has-one field cascades
  owner to element; a belongs-to field (the key on the declaring struct) makes the referenced type the principal.
  A principal declared in another file resolves in the dependent's own package (its directory); one that package
  does not turn into a node (a type alias, a dot import, generated code not on disk) drops the link, and a
  package-qualified belongs-to (`Org models.Org`) emits no cascade. Not modelled: a primary key other than `ID`
  without `references:`, a non-default naming strategy, keys held by an embed declared in another file. A
  `many2many` slice's `constraint:OnDelete` is read like a has-many cascade, although GORM puts it on the join
  table. These rules replay GORM's schema guesses; they were not checked against a running GORM.
- Go dot-imports, and the repository-root package imported from a subpackage, are not resolved.

### The TypeScript / JavaScript recognizer

One recognizer for `.ts .tsx .js .jsx .mjs .cjs` (`.d.ts`, minified, bundled and compiled files skipped) reads,
without tsc or Node: NestJS controllers, injection, schedules, events, guards, interceptors, pipes and middleware;
Express, Fastify, koa-router and Hono routes with mounts composed across files; Next.js route handlers; TypeORM and
MikroORM relations, cascades and listeners; Angular routes, guards and DI; tsyringe, InversifyJS and TypeDI
constructors; `extends` / `implements`. Imports are placed the way TypeScript does (relative paths, tsconfig /
jsconfig `paths` and `baseUrl`, package.json `imports`, workspace packages, barrels and default exports, up to six
hops), so a link binds in the module that defines the name, and a name from a package the tree does not hold binds
nothing. Comments, strings, template literals and regex bodies are blanked before matching. Limits:

- **(a) Only routers DeepMap can identify count:** one built by `express()`, `Router()`, `new Router()`,
  `new Hono()` or `Fastify()`; one typed with the framework's router type (`r: Router`, `srv: FastifyInstance`); or
  a parameter named `app`, `router`, `server`, `fastify` or `instance` in a file that uses one of those frameworks.
  An untyped router under another name, one from an untyped factory, or one imported through a bare specifier
  (`import router from 'lib/router'`) emits nothing.
- **(b) Handlers.** An inline handler (`(req, res) => ...`) is anonymous, and one held in a variable or a parameter
  is skipped. `this.<field>.<handler>` binds the class the field's type or initializer names. A member of a module
  object (`users.login` through `require()` or `import * as`) binds what that module exports under the name (an
  `exports.login = ...`, an entry of its exported object, or a method of the class or instance it exports, through
  an `extends` chain of up to eight hops), never a private function of the same name. A base the walk cannot follow
  (through a CommonJS object, a mixin call, a package's class) drops the route. An object holds only the keys
  written out (a spread or `Object.assign` adds none).
- **(c) Mounts DeepMap cannot read keep the router-relative path:** a prefix computed at run time (named in
  `prefix_unresolved`), a loop over anything but a literal `[{ path, route }]` array, a mount through a function
  call (`require('./routes')(app)`), an alias several modules match, a NestJS `RouterModule.register` tree. URI
  versioning is recorded as a `version` attribute. A NestJS global prefix applies only to its own app: with several
  bootstrap files, a library outside every app gets none, and two bootstrap files in one app (`main.ts` plus a
  serverless handler) count as two apps. A route on a class with no `@Controller` keeps the method's own path.
- **(d) CommonJS handlers depend on the engine version.** An `exports.x = function ...` handler is a node on
  graphify 0.8.51 but not on 0.8.39, and on neither when wrapped (`exports.x = catchAsync(async ...)`), so routes to
  it drop on the older engine; a known wrapper around the module's own function (`catchAsync(x)`) binds that
  function on both. A `list: async (req, res) => {}` property is a node on neither; a class property holding a
  handler gets the recognizer's own `Class.prop` node. 0.8.51 merges a module's same-named functions, so a route to
  the second can drop.
- **(e) Name resolution.** An alias only a bundler maps (webpack or Vite `resolve.alias`) cannot narrow several
  same-named candidates (`AMBIGUOUS`). A module DeepMap cannot read (a `.vue` or `.json` import, `export =`,
  compiled output, a chain past six hops) falls back to the name rule. A tsconfig `extends` of an npm package,
  project `references` and the `include` / `files` lists are not read. A TypeORM `onDelete` cascade whose principal
  stays ambiguous is dropped.
- **(f) Not read:** Next.js `middleware.ts`, server actions and `next.config.js` rewrites; GraphQL `@Query` /
  `@Mutation` resolvers; TypeORM `EntitySchema` entities, `orphanedRowAction`, an `onDelete` on `@OneToMany` /
  `@ManyToMany` or on a `@OneToOne` without its `@JoinColumn`; sequelize-typescript, Prisma schemas, Vue and React
  Router tables; Angular `loadChildren` / `loadComponent` lazy routes.
- **(g) The engine is needed.** Recognizer nodes cover top-level classes, interfaces and handler properties; real
  methods and functions come from the engine, so without it most route links drop.
- **(h) What is read.** `node_modules/`, `dist/`, `coverage/` and the JS tool directories (`.next/`, `.nuxt/`,
  `.turbo/`, `.svelte-kit/`, `.angular/` and others) are skipped at any depth, but `build/`, `out/`, `bin/`,
  `obj/`, `target/`, `tmp/` and `log/` only in the repository root or a package root. Compiled JavaScript is skipped
  by its markers (tsc and Babel helpers, bundler runtimes, a `//# sourceMappingURL=` trailer); compiled output with
  none of them is read like source.
- **(i) The lexer** reads a `/` as JavaScript does and treats a backtick in JSX text as text in the common shapes.
  A stray backtick in JSX text that spans lines or follows punctuation, a tag attribute holding a `<`, or a division
  right after an object literal's `}` can still hide code up to the next backtick.

### Pascal/Delphi and other languages

Delphi and every language without a recognizer get nodes, `:CALLS` and `:IMPORTS` only, so `deepmap_impact` walks
calls only there. There is no Rust framework recognizer.

## Search and ranking

- **`score` is per query.** Fusion is rank-based, so a score cannot be compared across queries.
- **Equal-weight fusion favours what several arms agree on.** The three arms (vector, fuzzy BM25 full-text,
  graph-weighted vector) are fused by Reciprocal Rank Fusion with equal weights. The graph-weighted arm scores
  `score x (1 + ln(1 + in-degree))` over all `CALLS`, wherever the callers live, so a class constructed often in
  tests and docs can rank high on unrelated questions, and a symbol only one arm finds can fuse below the top 15.
  Fused results can rank below the vector arm alone, and typed links are not used in ranking.
  **Workaround:** for exact symbols, use Grep or `deepmap_impact`.
- **Search is not top-k-monotonic.** Each arm fetches `top_k x 3` candidates, so the top 3 of a `top_k=3` query can
  differ from the top 3 of `top_k=8`; results are deterministic for a fixed `top_k`.
  **Workaround:** pin `top_k` when comparing; `DEEPMAP_CANDIDATE_FLOOR` gives a stable prefix.
- **There is no relevance floor.** A nonsense query still returns low-scored results. An empty or whitespace query
  is refused at the MCP and CLI boundaries.
- **The BM25 analyzer keeps some names out of reach.** `Class.method` is one token, so a method's bare name has no
  keyword hit, and a pasted call with punctuation (`reset_pristine(repo_dir, sha)`) gets none either. The opt-in
  `DEEPMAP_FTS_SPLIT_CODE_TOKENS=1` and `DEEPMAP_FTS_SHORT_NAME=1` cover both but stay off by default.
  **Workaround:** query by intent or by the qualified name, or use `deepmap_impact`.
- **The BM25 query builder bounds only strings Lucene rejects.** Queries over 10 terms are checked first with one
  extra probe (about 0.1 to 0.3 s; `DEEPMAP_FTS_PROBE=0` skips it). A rejected string gets a bounded variant: later
  terms lose typo tolerance, at most about 250 exact tokens are kept, and a long CJK paragraph contributes only its
  first ~250 characters. A standalone uppercase `AND` / `NOT` is lower-cased.
- **Queries are capped at 2,000 characters**, and concurrent searches share one query encoder, so a long query
  delays the searches behind it (about 2 s idle, up to about 9 s under load).
- **Without the embedding model, search is keyword-only;** results end with a note, and the load is retried every
  300 s (`DEEPMAP_MCP_MODEL_RETRY_S`). **Workaround:** `/deep-map:doctor`; restart the servers with
  `provision.py mcp-stop <root>` and `/deep-map:setup`.
- **A missing index is skipped; an outage fails loud,** never as an invented empty result. An exotic error whose
  type is not a driver error and whose message has no connectivity keyword could be read as benign.
- **A timed-out MCP call keeps its database connection for a while:** after the 60 s tool deadline, the abandoned
  worker keeps its pooled connection until the server's 120 s receive timeout.
- **Queries containing `${jndi:` over MCP** can be reset by a local security filter on loopback HTTP; the same query
  through the CLI works. This is not DeepMap behaviour.

### Measurement noise: order is deterministic per index state, not across index histories

Identical searches return the same list, but the same graph can rank differently after a different build or update
history. Compare recall and top-15 membership, not exact order, and compare fresh builds when you test a change.

- **Vector indexes are int8-quantized by default.** An index score deviates from the exact cosine score by about the
  gap between neighbours, enough to swap them while recall stays the same. **Workaround:**
  `DEEPMAP_VECTOR_QUANTIZATION=0` and a re-sync re-create the vector indexes with exact scores.
- **An HNSW index misses a few true neighbours** (about 3% of the exact top 45 per label), and which ones depends on
  insertion order. DeepMap inserts in a corpus-seeded order, so a build is reproducible, not miss-free.
- **BM25 statistics depend on the index's update history.** Sync re-creates the code full-text index after any push
  that rewrote indexed text or deleted nodes, so a synced state equals a fresh build's; the keyword arm sits out the
  few seconds of the rebuild. Deletions made outside sync (manual Cypher) are not tracked.
- **Incremental re-embedding is not bit-identical to a fresh build** (changed nodes are embedded in other batches).
- **Vectors depend on the CPU instruction set and torch build.** Bulk embedding uses a fixed 8 threads everywhere,
  but another instruction set (AVX-512, AVX2, ARM) or `DEEPMAP_EMBED_THREADS` can move a vector by float rounding.

### Read-only Cypher (`deepmap_cypher` / `deepmap cypher`)

The MCP tool and the CLI verb share `build/cypher_readonly.py`: a lexical validator first, then a READ transaction
that is always rolled back, a server-side timeout (`DEEPMAP_CYPHER_TIMEOUT_S`, 20 s), 100 rows by default and 500 at
most, and about 64 KB of text per answer. The CLI exits 0 (answered), 2 (rejected) or 3 (a Neo4j error).

- **The validator is lexical, not a Cypher parser, and errs strict.** A variable or alias spelled like a write or
  admin keyword in a keyword position is rejected (`RETURN set`, `MATCH (load)`); rename it or quote it in
  backticks. A `\u` escape that spells an ASCII character, or is not followed by exactly 4 hex digits, is rejected
  (pass such values as `$params`). VT, FF, NEL, U+2028 and similar characters are rejected outside strings, because
  Neo4j does not end a `//` comment at them. Functions are limited to plain functions and the core namespaces, and
  `CALL` to a short read-only list (`db.labels` and siblings, `db.schema.*`, the index queries, `dbms.components`).
- **Only the validator stops LOAD CSV.** A READ transaction still fetches a `file:///` or `http://` URL.
- **The caps bound each answer, not Neo4j's work.** A query can use transaction memory up to the instance's pool
  before it is stopped, and an expensive read can slow sync or search meanwhile. FastMCP also sends a compact
  structured copy, so the bytes on the wire roughly double.
- A relationship's `start` / `end` keys appear only when the query also returns the endpoint nodes, and path nodes
  are compact. `CALLS` counts are mostly name matches: filter on `r.confidence`.
- Embedding vectors are left out by name (`embedding*`) and by shape (a list of 256+ floats); a shorter slice still
  comes back. A column named `note` is renamed `note_`, so a `{"note": ...}` entry is always DeepMap's own.
- Already-running MCP servers advertise the old tool list until they restart onto new code (heal, `mcp-start`,
  `/deep-map:setup`), and the client must reconnect (`/mcp`).

## Impact

- **The walk.** `deepmap_impact` walks `:CALLS` plus `ROUTES_TO`, `DEPENDS_ON`, `TRIGGERS`, `SCHEDULED_AS`, `HOOKS`,
  `INHERITS`, `CASCADES` and `RENDERS` up to 3 hops (the CLI's `--max-hops` or `DEEPMAP_IMPACT_MAX_HOPS`, up to 5).
  Each row shows the weakest label of one representative path. `IMPORTS` and `IMPLEMENTS` are not in the default
  walk.
- **The MCP tool has no `max_hops` parameter.** A hub name degrades to a 1-hop answer with a note, or raises
  `ImpactTooExpensive`. **Workaround:** query `Class.method`; use the CLI's `--max-hops`; raise
  `DEEPMAP_NEO4J_HEAP_MAX` or `DEEPMAP_IMPACT_TIMEOUT_S`.
- **`--edges`, the MCP `edge_types` argument and `DEEPMAP_IMPACT_EDGES` replace the default set** (`CALLS` is always
  kept), so list the defaults too or the walk loses routes, cascades and renders:
  `--edges CALLS,ROUTES_TO,DEPENDS_ON,TRIGGERS,SCHEDULED_AS,HOOKS,INHERITS,CASCADES,RENDERS,HAS_MANY,REFERENCES`.
  `DEEPMAP_IMPACT_PROFILE=rails` adds the ORM associations instead.
- **Hops 2 and beyond expand through `AMBIGUOUS` links,** so a deep answer can include name-matched chains.
  **Workaround:** read `AMBIGUOUS` rows at the call site.
- **Lines.** A row's `file:line` is where the dependent starts (its definition). The optional `call_line` is the
  line of its call, and the CLI prints `file:<call_line> (call site; caller starts at line <line>)` (from hop 2 on,
  `first call on the path` instead of `call site`). Only the first
  call of each (caller, callee) pair is recorded; from hop 2 on it is the call into the next symbol of the path, not
  into the queried one; and it is absent for a row whose own edge is not `CALLS`, for calls the opt-in
  `DEEPMAP_GRAPHIFY_CALLS_PLUS` or `DEEPMAP_SUPPLEMENTAL_TS_RUST` adds, and on a graph synced before call lines were
  recorded (until the next sync). **Workaround:** without a `call_line`, look for the call inside the caller.
- **Counting.** `--limit` counts distinct (dependent, callee) rows, so a caller that reaches several same-named
  callees uses several rows; rows identical in every field are collapsed. The CLI clamps `--top-k` to 1 to 50 and
  `--limit` to 1 to 100, like the MCP tools.
- **Names.** The "lower bound" note on a method answer is a heuristic (a qualified symbol or a dotted callee). A
  module-qualified name (`provision.is_bolt_up`) is read as module plus symbol only when it matches no symbol
  directly; `Class#method` and a leading `.` are accepted. Rust owner matching is best effort.

## MCP servers and hooks

- **Connecting the tools.** `/deep-map:setup` starts the project's two MCP servers, `deepmap-search` and
  `deepmap-embed` (streamable HTTP on local ports), and writes the project's `.mcp.json`. The plugin ships no MCP
  servers of its own, so a project has no deepmap tools until setup has run in it, and the tools appear only in a
  session started after setup. In that new session Claude Code may first ask you to approve the two project
  servers; approve both. A declined approval leaves the tools off, and `claude mcp reset-project-choices` asks
  again. The servers run detached and keep running after Claude Code closes (`provision.py mcp-stop <root>` stops
  them). Sync does not ignore `.mcp.json`, so a team can commit it.
- **Each hook can start a second interpreter.** A `hooks.json` command is
  `python -X utf8 "<hook>" ; python3 -X utf8 "<hook>" ; exit 0`, which works under PowerShell 5.1 and POSIX shells.
  The interpreter that gets the input runs the hook and the second exits at once, at the cost of one extra
  interpreter start per hook event (about 150 ms on Windows, longer on a loaded machine). Under `cmd.exe` only
  `python` is tried, and a `python` that reads the input and then fails does not fall back to `python3`.
- **The session-start heal picks its Python:** `DEEPMAP_PYTHON` when it names a startable interpreter (an absolute
  path, or a command whose program is in an absolute `PATH` directory; never a relative path, because the hook runs
  in the project and Windows searches the working directory first); else the interpreter the project's last
  successful setup recorded (`<DEEPMAP_HOME>/run/setup-python-<root id>.json`, ignored when it lies inside the
  project's own tree); else the hook's own Python. `heal.log` names the choice.
- **Hook cost per deepmap tool call.** A tool-result hook runs after every `deepmap_search`, `deepmap_impact` and
  `deepmap_cypher` call. With nothing new to say it exits on a per-session stamp before importing DeepMap code; when
  a background-refresh record changed it takes about 0.3 s more. `DEEPMAP_FRESHNESS_NOTE=0` silences its notes.
- **The search server survives a database restart.** It checks a pooled connection idle for 2 s or more before use
  and retries a call that meets a dead pooled connection once (never a refused connect, an authentication error or
  a query error, never twice).

## DeepMap keeps its graph current (background refresh at session start)

When an interactive Claude Code session starts, the session-start heal hook checks whether the project's code
changed since the graph was built and, if so, refreshes the graph in the background
(`deepmap_cli.py sync <root> --unattended=session-start`, `build/auto_sync.py`). The session is told a refresh is
running, and later how it ended. It is offline, runs at low CPU priority with an evidence-only prune, and gives way
to any sync you start. Off switches: `DEEPMAP_AUTO_SYNC=0`, `.deepmap/instance.json`
`{"auto_sync": {"enabled": false}}` (this project), or `<DEEPMAP_HOME>/auto-sync.json` `{"enabled": false}` (this
machine), which `python build/auto_sync.py enable|disable [--machine]` writes. `deepmap status` and `doctor` show an
auto-sync line. Limits:

- **Not real-time.** There is no file watcher and, by default, no commit trigger. An edit made during a session is
  not in the graph until the next session start or a `/deep-map:refresh`.
- **At most once every 15 minutes for the same commit,** even after edits (outside git the 15 minutes count alone).
  **Workaround:** `/deep-map:refresh`.
- **Off in sessions nobody watches.** `claude -p`, the SDKs, and a `claude` started by a script, the Bash tool or
  another agent never start it unless `DEEPMAP_AUTO_SYNC=1`. Claude Code marks those sessions with
  `CLAUDE_CODE_SESSION_ATTENDED=0` (read from the Claude Code binary, not observed live); without that variable an
  SDK, `mcp`, GitHub Action or remote-trigger entrypoint counts as unattended.
- **The background prune is evidence-only.** It deletes a node only when its file was re-read or is gone. Stale
  nodes stay (the verdict stays `READY`, and the count reaches the session as a note) when a node's file exists but
  was not re-read, a test or vendor filter excludes it, a re-read file lost more than half its symbols (a
  half-written or conflicted file), or the engine failed, fell back, timed out or changed.
  **Workaround:** `/deep-map:refresh` runs the full prune.
- **While it writes,** search may briefly mix old and new code, and the keyword arm sits out the full-text rebuild.
- **One background refresh per machine.** Another project's waits up to 30 minutes, then skips (`machine-busy`).
- **A memory floor.** It starts only when 25% of physical memory is free, counted as at least 3 GiB and at most
  6 GiB. If memory stays low for 2 minutes before the embedding model loads, the embeddings are postponed, so vector
  search misses the changed code until the next refresh while keyword and graph results include it.
- **Too slow for the background.** It skips (`too-slow`) a project whose last foreground sync took longer than
  `DEEPMAP_AUTO_SYNC_MAX_S` (default 1200 s), which also caps the background engine budget. A project with more
  than 1,500 source files and no recorded sync time skips (`needs-timing`) until one `/deep-map:refresh`.
  **Workaround:** set `DEEPMAP_AUTO_SYNC_MAX_S=<seconds>` in the environment Claude Code runs in (`0` removes both
  time checks).
- **Low priority means CPU priority:** below-normal on Windows, nice +10 on POSIX, plus best-effort I/O on Linux.
  Neo4j's own work (writes, index updates) runs at normal priority.
- **It never starts Neo4j;** it waits up to 3 minutes for this project's database. With `DEEPMAP_HEAL=0` nothing
  brings the database up, so with it down the refresh ends at once; set `DEEPMAP_AUTO_SYNC=0` as well to keep session
  start free of background work.
- **Never to a database on another machine on its own.** With a `NEO4J_URI` whose host is not a loopback address
  it skips (`remote-database`) unless `DEEPMAP_AUTO_SYNC=1`. A graph whose manifest does not record its database is
  not refreshed while `NEO4J_URI` is set (`recipe:route`), and a graph with no recorded owner is never adopted
  (`unowned`), until one `/deep-map:refresh`.
- **It rebuilds only what the last build would.** It replays the recorded graph-shaping settings and skips, saying
  why, when the session's settings, the Python version, `DEEPMAP_HOME`, the database, the embedding model or the
  engine differ, or when newer DeepMap code built the graph. A graph built before DeepMap recorded its settings is
  refreshed once with the session's own settings. **Workaround:** `/deep-map:refresh` rebuilds with the session's
  settings.
- **A sync you start wins.** `/deep-map:refresh`, `/deep-map:setup` and `deepmap enrich` ask a running background
  refresh to stop and wait up to 15 minutes (once it writes, its pass finishes first). Where a process start stamp
  cannot be read, your sync queues behind it instead.
- **Failures back off.** Each failure starts a 6-hour backoff; three in a row, or one engine timeout, pause it until
  a refresh of any kind succeeds. The off-switch files fail closed (a value DeepMap does not understand means off).
  After a skip that would repeat, the session note suggests `/deep-map:refresh` instead. `status` and `doctor` read
  the shell's environment, which can differ from Claude Code's.
- **What "is the graph current?" covers:** source files, `.gitignore` / `.graphifyignore`, the TypeScript and
  JavaScript project files (tsconfig / jsconfig, package.json, nest-cli / angular / workspace / project.json,
  `next.config.*`), the MSBuild files the C# reader reads (`.csproj`, `.props`, `.targets`, `.projitems`,
  `packages.config`, `Directory.Build.rsp`), a root marker, submodules and nested repositories. The MSBuild names count
  with the reader on or off, so a repository holding such files gets one extra refresh after an upgrade and one on each
  change to them; with the reader off, that refresh leaves the graph as it was. Not covered: a tsconfig `extends` target
  with another name, an MSBuild `<Import>` of a file with another extension (read at each sync, but a change to it alone
  starts no refresh), files git ignores through an anchored pattern that the `ast` fallback still reads, submodules set
  to `ignore=dirty|all`, and, outside git, the `build/`, `dist/`, `bin/` directories graphify skips. The note after an
  edit and the `status` / `doctor` staleness line count source files only, so they do not mention an uncommitted edit to
  one of these project files; the next refresh sees it. It never refreshes during a git rebase, merge, cherry-pick,
  revert or bisect.
- **What the agent is told.** The session-start note announces a refresh only when one will run; later notes arrive
  on the next Grep, Glob or deepmap tool call, each fact once per session. A session that started while auto-sync
  was off gets none.

## Sync, the git hook and the installers

- **Exit codes.** `deepmap sync` exits 0 (synced, or a re-run queued), 1 (refused, failed, or verdict `FAILED`), 3
  (Neo4j unreachable) or 4 (NOT PUSHED: the previous graph is kept and may be stale). A subdirectory sync syncs the
  outermost already-synced ancestor.
- **The prune guard.** A re-sync's stale-node prune is skipped when it would delete more than half of the keyed
  nodes, or when the engine downgraded since the last build; the push still happens. `--force-prune` overrides it.
- **The engine's time budget is an estimate.** Unset, `DEEPMAP_GRAPHIFY_TIMEOUT` is `max(600 s, 1.5 s x source
  files)`, capped at 4 h. It does not count the `.md` / `.json` files the engine also reads, so a docs-heavy
  repository or an overloaded machine may need more. **Workaround:** `DEEPMAP_GRAPHIFY_TIMEOUT=<seconds>` (60 to
  86,400).
- **A first sync that times out still pushes.** With no earlier graphify graph to protect, the `ast` fallback's graph
  is pushed, the verdict is `DEGRADED` and the exit code is 0.
- **The caller's own time limit still applies.** A foreground Bash tool call (2 minutes by default, 10 at most) ends
  a sync at that limit, so the slash commands run long syncs in the background.
- **Edge rebuilds are atomic per anchor only up to 20,000 rows.** Larger anchors are rewritten in bounded,
  non-atomic pieces: a reader may briefly see one partly rebuilt, and a killed sync leaves it partial (an interrupted
  prune leaves orphans) until the next sync. **Workaround:** re-run the sync.
- **The git post-commit sync is opt-in.** `/deep-map:setup` never installs a git hook; the terminal installers
  (`install.sh`, `install.ps1`) install it through `provision.py git-hook --install <root>` (`SKIP_GIT_HOOK=1` /
  `-SkipGitHook` opts out). Setup's tip says the hook is "never installed automatically", even inside the installer
  flow that then installs it. **Workaround:** `/deep-map:refresh` after changes, or
  `provision.py git-hook --install <root>`.
- **The hook's sync runs detached and never prunes.** Its exit 4 shows only in `<DEEPMAP_HOME>/sync.log` and the
  `status` / `doctor` freshness line, and deleted code stays until a `/deep-map:refresh`. It lets the model library
  ask huggingface.co whether its files are current (it sends nothing about your code; `HF_HUB_OFFLINE=1` turns it
  off). The post-edit note's "a commit also re-syncs it" does not account for a hook sync that pushed nothing.
- **Hooks DeepMap will not modify:** non-sh, binary, disabled, tracked, husky and outside-the-repository hooks, and an
  in-repository `core.hooksPath` the team commits; DeepMap prints the line to add by hand. Hand-written hook lines are
  read by position, so a hand-written `sync --stale-cleanup <root>` (flag first) is not recognized as destructive.
  An old unscoped post-checkout line can remain; `git-hook --status` names it.
- **git 2.13 or later.** Before 2.31 DeepMap retries without `--path-format=absolute`; before 2.13 the hook command
  prints a misleading "not a git work tree".
- **`DEEPMAP_PYTHON` must name a runnable interpreter.** A command line such as `py -3` is resolved to its
  interpreter; `git-hook --install` refuses a value that cannot run, and `--status` reports a block whose
  interpreter no longer runs as stale. The post-commit block takes a relative value as given.
- **Running an installer from the DeepMap folder sets up DeepMap's own source as a project,** because both
  installers default the project to the current directory. Nothing breaks, but it costs a JVM, minutes of
  embedding, and a `.mcp.json` and `.deepmap/` in the plugin folder. **Workaround:** run the installer from your
  project, pass `REPO_ROOT=` / `-RepoRoot`, or set `SKIP_PROVISION=1` / `-SkipProvision`.

## Setup, disk and time

### `/deep-map:setup` installs DeepMap's own environment

When the Python that runs setup lacks one of DeepMap's packages, or holds one outside the range `requirements.txt`
pins, setup never installs into that Python. It checks Java and free disk, creates `<DEEPMAP_HOME>/venv`, installs
the packages there (PyTorch's CPU build), downloads the embedding model, runs a self-check, and re-runs itself under
that environment; every other command then runs under it too (`build/deps_bootstrap.py`). A Python that already
has every package in range runs DeepMap itself.

- **Downloads and disk.** The first setup on a machine downloads about 1 GB (packages, the embedding model at about
  0.6 GB, Neo4j) and leaves about 2.5 GB on disk (about 3.2 GB on Linux); keep about 4 GB free while it installs.
  Building the environment took 3 to 11 minutes in our runs.
- **The first run needs the network:** PyPI, download.pytorch.org (torch's CPU build bypasses a `PIP_INDEX_URL`
  mirror), huggingface.co and dist.neo4j.org; pip and huggingface_hub honour `HTTPS_PROXY`. A failure names its step
  and prints the end of `<DEEPMAP_HOME>/venv-setup.log`, and a re-run keeps what is installed. pip's configuration
  files still apply, so a `target`, `prefix` or `root` set there would point pip away from DeepMap's environment.
- **Debian and Ubuntu need Python's venv module.** Without `python3-venv` (or `python3.X-venv`) setup fails in
  seconds, naming the package to install.
- **One environment per machine, no uninstall verb.** Every project shares `<DEEPMAP_HOME>/venv`. To remove it, stop
  DeepMap's servers first (`provision.py mcp-stop` in each project; closing Claude Code does not stop them), then
  delete the directory; pip's download cache is pip's own (`pip cache purge`). On Windows, deleting it while servers
  run leaves files they hold open, and setup then refuses that remnant, naming the processes still using it.
- **A broken environment is rebuilt only while nothing runs from it.** One whose Python or pip no longer works, or
  whose base Python moved to another minor version, is moved aside and rebuilt, never cleared in place, and only
  while the OS process list shows nothing running from it; otherwise setup refuses and names each process and the
  `mcp-stop` that stops it. A directory DeepMap did not make is never touched. A failed repair keeps a working
  environment in use, and the next setup retries.
- **A plugin update that changes `requirements.txt`** reaches the environment at the next `/deep-map:setup`; until
  then each command prints one note naming what is missing or out of range.
- **`DEEPMAP_PYTHON` does not steer this step.** With `DEEPMAP_PYTHON` naming a Python that has the packages while
  the slash commands' `python` lacks them, setup still builds and records DeepMap's environment.
  **Workaround:** run setup with that interpreter, in a terminal.
- **A Python with an out-of-range package is left alone** (for example an mcp 2.x installed for another tool).
- **Process details.** On Windows the first interpreter waits while the re-run works (on Linux and macOS it is
  replaced); output and the exit code pass through. Two setups at once build the environment one after the other.
  `DEEPMAP_MANAGED_VENV=0` never creates or uses the environment.

### Time

- **The first graph build is the long step on large, test-heavy repositories,** mostly embedding. On a busy machine,
  setup took 12 to 14 minutes on projects of about 700 to 1,300 symbols, and about 70 minutes on one of about 6,300,
  the largest tested. **Workaround:** `DEEPMAP_EXCLUDE_TESTS=1`; progress lines print every 30 s
  (`DEEPMAP_EMBED_PROGRESS_S`).
- **`doctor` is slow and machine-wide.** It imports torch and sentence-transformers to prove the ML stack (about 30
  to 50 s cold), prints every registered project's instance row, and waits up to 30 s on a hung instance.
  **Workaround:** `/deep-map:status` for a quick check.

## Enrichment (opt-in)

`/deep-map:enrich` writes intent summaries for undocumented code. It is off by default and is DeepMap's only path
that sends code anywhere: the LLM tier needs `DEEPMAP_ENRICH=1` (or the instance flag) and `--confirm-spend`, and
sends undocumented functions to your own `claude -p`, under your Claude Code login. `--deterministic` is a free,
local alternative (leading comments and Rails / DSL macros, no LLM).

- Enrichment only writes a sidecar (`.deepmap/summaries.jsonl`); the next sync embeds it.
- Non-Python spans end at the next definition, so a summary sees one definition, not the rest of the file.
- **It holds the sync lock while it extracts, and never syncs.** A sync that starts meanwhile queues a re-run and
  exits 0, and the next `/deep-map:refresh` performs it. A sync (or another enrich) you started that holds the lock
  makes enrich refuse with exit 4, having sent and spent nothing; a background refresh is asked to stop instead.
  Two enrich runs that both got past extraction can overlap while writing the sidecar, and one can drop the other's
  new summaries. **Workaround:** run one enrich at a time, then `/deep-map:refresh`.

## The opt-in learning loop

DeepMap can record, locally, which search results you act on and compute recommended arm weights from them
(`docs/RM1-ENABLE.md`). It is off by default (`deepmap learning --enable`, or `DEEPMAP_LEARNING=1`; an explicit
`DEEPMAP_LEARNING=0` vetoes it) and sends nothing anywhere.

- **It does not change your search results by default.** The tuner writes its weights with `applied: false`, and
  live ranking keeps equal weights. Applying learned weights is a separate opt-in (`DEEPMAP_APPLY_LEARNED`) behind
  further checks, and only arm weights ever reach ranking. No benchmark has yet shown learned weights beating equal
  weights.
- **The access hook is not installed for you.** `deepmap learning --enable` prints a PostToolUse snippet to paste
  into Claude Code's `settings.json`; its hook path is the current plugin version's copy, so re-run the command after
  a plugin update. Under Windows PowerShell 5.1 the snippet fails when the interpreter path holds a space.
  **Workaround:** use an interpreter path without spaces, or install Git Bash.
- **`.deepmap/instance.json` must stay a JSON object.** Every writer of the learning state refuses to overwrite a
  file that is not a JSON object, so a hand-edit typo makes those writes fail until the file is fixed.
  **Workaround:** fix the JSON, or delete the file (its learning state goes with it).

## Not tested yet

[`testing.md`](testing.md) describes the automated suite and the live test runs. These parts have not run live, or
run only by hand:

- **macOS.** No live run on a Mac has passed yet; only hermetic tests with simulated inputs cover the macOS code
  paths. The manual workflow `.github/workflows/macos.yml` runs the test suite on macOS 15 and macOS 26 and a live
  end-to-end check on GitHub's Apple-silicon runners. Until its live job passes, treat macOS as unverified. Intel
  Macs are not supported (see [Platforms](#platforms)).
- **Neo4j versions.** Only Neo4j 5.26 ran live: 5.26.0, 5.26.32 and the upgrade from 5.26.0 to 5.26.32, on
  Windows 11 and in a Linux container. A database you point DeepMap at with `NEO4J_URI` (another version, or Aura)
  was not tested live.
- **Continuous integration covers one configuration.** GitHub Actions runs the suite that needs no database on
  every push and pull request: Linux, the newest Python 3 and the newest engine release DeepMap allows (graphify
  0.8.51). Windows, Python 3.10 to 3.12, the oldest allowed engine release (0.8.39), an image with only `python3`
  and the live suites on real Neo4j run by hand, so a change can break one of them without CI noticing. macOS runs
  only when someone starts the manual workflow.
- **The installed plugin on a clean Claude Code profile.** The live campaign ran DeepMap from its source folder; a
  copy of the plugin's files ran setup, every slash command and every hook once, partway through the fixes.
  Installing from the public marketplace, and the first session after setup on a clean Claude Code profile
  (including the server approval prompt described under [MCP servers and hooks](#mcp-servers-and-hooks)), have not
  been run live yet.
- **Larger projects.** The largest project set up in testing had about 6,300 symbols (see [Time](#time)); setup
  time and memory beyond that are not measured.

## What the live test campaign left open

The live test campaign of September 2026 ([`testing.md`](testing.md#the-live-test-campaign)) confirmed bugs in 10
areas, and each fix came with a test that failed on the old code. The findings below were not fixed on the default
path. Most of them would change the default graph or the ranking, and DeepMap changes those only after a
measurement set up in advance shows that the change helps (see
[Benchmark status](#benchmark-status-no-published-results) and `benchmark/call_linking/`). Each item names the
section of this page that has the details and the workaround.

**Fixed behind a setting that is off by default.** The call-linking, keyword and summary settings did not pass the
measurement a default change needs; the last two items are open decisions.

- Calls are linked by name, so most default `CALLS` links are `AMBIGUOUS`: `DEEPMAP_PRECISE_CALLS`
  ([Call linking and confidence](#call-linking-and-confidence)).
- Python calls the engine does not emit (`module.f()`, `alias.f()`, module-level and nested-closure calls):
  `DEEPMAP_GRAPHIFY_CALLS_PLUS` (same section).
- Keyword search misses code-shaped tokens and bare method names: `DEEPMAP_FTS_SPLIT_CODE_TOKENS` and
  `DEEPMAP_FTS_SHORT_NAME` ([Search and ranking](#search-and-ranking)).
- Summaries cut at 80 characters, and Python modules whose own docstring is not used: `DEEPMAP_SUMMARY_NORMALIZE`
  and `DEEPMAP_MODULE_DOCSTRINGS` ([Extraction and languages](#extraction-and-languages)).
- Approximate vector order from int8 quantization: `DEEPMAP_VECTOR_QUANTIZATION=0`
  ([Measurement noise](#measurement-noise-order-is-deterministic-per-index-state-not-across-index-histories)).
- A sync after every commit: the git post-commit hook, installed only on request
  ([Sync, the git hook and the installers](#sync-the-git-hook-and-the-installers)).

**Left as they are, waiting for a measurement or a decision.**

- The graph-weighted arm counts name-matched and test callers, so a class that tests construct often can rank high
  on unrelated questions, and a fused list can rank an answer lower than the vector arm alone does
  ([Search and ranking](#search-and-ranking)).
- Search is not top-k-monotonic (same section).
- `deepmap_impact` expands hops 2 and beyond through `AMBIGUOUS` links; whether to cap or skip them is not decided
  ([Impact](#impact)).
- The `self.m()` re-bind drops a dunder called through an instance attribute and binds only the first of two calls
  one caller makes on consecutive lines, and a bare call can land on a `:Module` node of the same name
  ([Call linking and confidence](#call-linking-and-confidence)).

**Limits of the engine or of Neo4j.** DeepMap's answers say when they are partial.

- Calls on objects (parameters, fields, Go, Rust and TypeScript receivers, traits, namespaces) are not emitted, so
  `deepmap_impact` can miss callers: an empty answer says it is not proof, and a method answer says it is a lower
  bound ([Call linking and confidence](#call-linking-and-confidence)). The opt-in typed-receiver call linking covers
  part of this for Python, TypeScript and JavaScript.
- On graphify 0.8.47 and later, `alias.func()` gets no link in a file with two or more methods of that name (same
  section).
- Duplicate definitions at one scope collapse into one node
  ([Extraction and languages](#extraction-and-languages)).
- A long-lived Neo4j can lose its Bolt event loop and force-close some new connections ([Neo4j](#neo4j)).

**Not fixed yet.**

- Setup's "Neo4j already up" path and heal do not check for that dead event loop, so setup can report `READY` while
  `/deep-map:doctor` warns ([Neo4j](#neo4j)).
- In a folder with no registered project, the CLI and the provisioner try DeepMap's default password on
  `127.0.0.1:7687` (same section).
- Running an installer from the DeepMap folder sets up DeepMap's own source as a project, and setup's git-hook tip
  says "never installed automatically" inside the installer flow that installs the hook
  ([Sync, the git hook and the installers](#sync-the-git-hook-and-the-installers)).
- With the opt-in call-linking modes on, a module-level `del` of an import alias counts as a rebinding
  ([Call linking and confidence](#call-linking-and-confidence)).
- The learning loop's access-hook snippet fails under Windows PowerShell 5.1 when the interpreter path holds a space
  ([The opt-in learning loop](#the-opt-in-learning-loop)).
- In the test suite, a few gates are load-sensitive, `tests/sync_r8_gates.py` needs a `git` binary, and
  engine-sensitive gates must run on both ends of the engine range
  ([For contributors: tests and code](#for-contributors-tests-and-code)).

## Benchmark status: no published results

No with-versus-without benchmark result is published yet. The call-linking decision records in
`benchmark/call_linking/` do include the pre-registered measurements behind keeping those modes off by default.
The with-versus-without harness, its questions and runbooks ship in
`benchmark/`, and the call-linking decisions in `benchmark/call_linking/`. Earlier internal runs measured an older
default graph; results will be published only after a re-run on the current graph. Read any number you produce with
the harness as a measurement of your graph, your model and your machine.

### For contributors: running the benchmark harness

#### Benchmark blind judge: transport

- **Blind-judge transport.** The scoring judge runs through `claude -p` by default, under your own Claude account,
  with the prompt piped on stdin (a long prompt passed as an argument arrives mangled). `DEEPMAP_JUDGE_TRANSPORT`
  switches it to `openrouter` or `api`. The default judge is a Claude model grading a Claude arm, so a same-family
  preference is possible; for the most independent scoring, use a non-Claude judge. A failed judge leaves a card
  UNSCORED, never an invented number.
- **The blind-judge guard trusts `--model opus` to mean `claude-opus-4-8`.** A judge that is another concrete Opus id
  passes it. The guard does not follow anything that re-points a model outside the `--model` value (an
  `ANTHROPIC_DEFAULT_*` variable, an organization default model or allowlist, a provider alias), and different
  Claude CLI versions resolve `--model opus` to different models. Nothing checks a capture's `modelUsage`.
  **Workaround:** confirm the resolved model before a run.

#### Other harness limits

- **DeepMap's skill still loads in both arms** when the plugin is enabled on the benchmarking machine: every
  `claude -p` child runs with DeepMap's hooks off, but no per-invocation flag disables a plugin's skill.
- **A resume sees the DeepMap version, not the code.** `run_g3_n4.sh` and `run_breadth.sh` reuse a capture only when
  it measured the same DeepMap version, pinned SHA, engine and engine version. A code change without a version bump,
  a graph-shaping opt-in flag, or an edited `questions.yaml` is not part of that identity.
  **Workaround:** use a new `RUN_TAG` after such a change.
- **The breadth driver's judge preflight does not check `OPENROUTER_API_KEY`;** a missing key surfaces only when
  scoring starts. **Workaround:** export the key before launching.

## For contributors: tests and code

- **Run engine-sensitive gates on both ends of the engine range** (graphify 0.8.39 and 0.8.51): output shapes change
  inside the range, and CI installs the top of it.
- **The test suite runs in one Linux CI job** (newest Python 3, the top of the engine range, the no-Neo4j suite);
  gitleaks runs on every push and pull request, dependency review on pull requests. Windows, Python 3.10
  to 3.12, graphify 0.8.39 and the live suites run by hand; macOS runs only through the manual workflow.
- **Load-sensitive gates.** A few gates time real processes (a concurrent key-migration race, a 10 s timeout around
  a real server, a cold start against a short deadline, `git rev-parse` inside a hook's 5 s budget). Re-run such a
  gate alone before treating a failure as a regression.
- **`tests/run_all.py` stops a gate after 300 s,** and a full Windows run takes about 25 to 35 minutes on a quiet
  machine (over an hour under load). `tests/sync_r8_gates.py` needs a `git` binary. Live suites are opt-in
  (`DEEPMAP_LIVE_TEST=1`).
- **Hand-built `CALLS` tuples** for a keyed mode must mark engine-resolved targets with
  `graphify_extract.call_key(k)`; an unmarked string is always a name at the push.
- **`install.ps1` must stay pure ASCII:** Windows PowerShell 5.1 misreads UTF-8 punctuation in a file without a BOM.
