# Changelog

All notable changes to DeepMap are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and versions are 0.x: DeepMap is an early release, and features
may change between versions.

## [0.6.0] - 2026-10-28

Better C# and .NET support, and impact rows that point at the call.

### Added
- **C# computed properties and constructors are in the graph.** A property whose value is computed (`=> ...`, or a
  getter with a body) and each type's first instance constructor are now symbols you can search, so logic that lives
  in a getter is found. Auto-properties, indexers and events are not symbols. Turn it off with
  `DEEPMAP_CSHARP_MEMBERS=0`.
- **Impact rows show the call site.** A call row gives the line of the call (`call_line`) beside the line where the
  calling function starts, and the CLI prints `file:line (call site; caller starts at line N)` (from hop 2 on,
  `first call on the path`). A graph synced before this release gets call lines at its next sync.
- **Honest C# impact answers.** By default, a C# call into another file through a field, a parameter, a local or a
  type name is not linked. `deepmap_impact` now says so for a C# symbol, names what else it cannot see (constructor
  and getter bodies, MediatR dispatch, property reads), and tells Claude to grep before calling code unused.
- **An opt-in C# call linker** (`DEEPMAP_TYPED_CALLS=csharp`). It links calls by the declared type of the object,
  including calls through injected interfaces to their implementations and calls to overridden methods.
  `DEEPMAP_TYPED_CALLS_IFACE` controls the interface and override part. It is off by default until it passes its own
  checks, set in advance.

### Changed
- **.NET test projects count as test code**: a folder named like `Shop.Tests` or `Shop.UnitTests`.

### Fixed
- **No duplicate nodes after a node changes kind.** With the oldest supported engine release, the first sync after
  this release turns C# record and struct methods into methods of their type; the old copies are removed.
- **No false "missing source" with `DEEPMAP_EXCLUDE_TESTS=1`** for a build folder inside a .NET test project.

## [0.5.0] - 2026-10-26

First public release.

DeepMap is a Claude Code plugin: code RAG that reasons, not just retrieves. Graph + Vector + FTS, fused into one
ranked list, in a local Neo4j database. MIT, 100% local.

Install it inside Claude Code with `/plugin marketplace add deepfusionlabs/deep-map` and
`/plugin install deep-map@deepfusionlabs-deep-map` (in the VS Code extension, where `/plugin` isn't available: type
`/`, choose **Manage plugins**, add the marketplace `deepfusionlabs/deep-map` and install **deep-map**), then run
`/deep-map:setup` in your project.

### What DeepMap does

- **Three retrieval modes, one ranked list.** `deepmap_search` searches your code three ways at once: the graph
  (how code connects; code with more callers ranks higher), vectors (what code means: every function, method and
  class gets a one-line intent summary, embedded by a local model on your CPU) and full-text (BM25 over names and
  summaries, fuzzy within 2 edits, so typos and spelling variants still match). Reciprocal Rank Fusion (k = 60,
  equal weights by default) merges the three rankings into one list.
- **Impact analysis in one call.** `deepmap_impact` walks backwards from a symbol through calls and framework wiring:
  3 hops by default (up to 5 from the command line), 30 rows by default, with a note when the list was cut, when a
  symbol isn't in the graph, or when a method's callers are only a lower bound.
- **Read-only Cypher.** `deepmap_cypher` answers structure questions: list, count, group, find paths. Writes are
  refused, and every answer is bounded in time, rows and size, without embedding vectors. The same check backs
  `deepmap_cli.py cypher` in a terminal.
- **`deepmap_embed`** embeds any text with the same local model as search.
- **Evidence on every answer.** Every result has a `file:line`. Every link carries a confidence label: `EXTRACTED`
  (read directly from your code), `INFERRED` (matched by name to exactly one symbol) or `AMBIGUOUS` (several
  matches, or a dynamic call: a lead to verify). Setup reports a verdict too: `READY`, `DEGRADED` (naming the gap)
  or `FAILED`.
- **Framework wiring in 6 ecosystems:** Python, Ruby on Rails, C# / .NET, Java, Go and TypeScript / JavaScript.
  Routes, injected dependencies, scheduled jobs, events, hooks and callbacks, cascading deletes, model relations and
  view renders become 12 kinds of typed link in the graph. The impact walk follows them together with calls (model
  relations only on request, see below).
- **Languages.** A multi-language tree-sitter engine parses 25 programming languages into the same code graph, and
  all three search modes cover every one of them. 8 of these languages have their own end-to-end test suites:
  Python, JavaScript, TypeScript, Go, Rust, Java, Ruby and C#.
- **One database per project.** Each project gets its own Neo4j Community 5.26 instance, on localhost only, run
  from one shared Neo4j install. Setting `NEO4J_URI` points DeepMap at a Neo4j of your own instead.
- **Eight commands:** `/deep-map:setup`, `/deep-map:refresh`, `/deep-map:impact`, `/deep-map:status`,
  `/deep-map:doctor`, `/deep-map:enrich`, `/deep-map:version` and `/deep-map:plugin-update`.
- **A reasoning skill** routes a code question to the right tool, tells Claude to open the cited `file:line` before
  answering and to say how sure each link is, and falls back to Grep and Read when the graph is empty, unreachable
  or doesn't cover a language.
- **Hooks.** At session start, DeepMap revives this project's database and servers and tells Claude whether the
  graph is ready, stale or incomplete. When an interactive session starts and the code changed, it also refreshes
  the graph in the background: offline, at low CPU priority, at most once every 15 minutes for the same commit, and
  a refresh you start takes over from it (`DEEPMAP_AUTO_SYNC=0` turns it off). A once-per-session note before a
  text search and after an edit, and a note after a search, impact or Cypher call on how a background refresh went,
  complete the set. No hook ever blocks a tool call; `DEEPMAP_HOOKS=0` turns every DeepMap hook off.
- **Setup builds its own Python environment.** `/deep-map:setup` checks Java 17 or 21 and free disk first, then
  installs DeepMap's own environment under `~/.deepmap/venv` (never into your Python), downloads the embedding
  model and Neo4j Community from its official source (checksum-verified, never bundled), builds the graph and
  starts the local MCP servers. Safe to re-run; it also repairs. After setup, start a new Claude Code session and
  approve the two deepmap servers if Claude Code asks: setup wires them into the project's own `.mcp.json`, and
  the plugin ships none.
- **A sync exit contract:** `0` synced, `1` refused or failed, `3` Neo4j unreachable, `4` not pushed (the previous
  graph is kept and may be stale). `provision.py setup` exits `4` in that case too.
- **Privacy.** No telemetry, no cloud, no API key. By default nothing DeepMap builds leaves your machine, and no LLM
  parses or summarizes your code.

### Opt-in, off by default

- `/deep-map:enrich`: better intent summaries for undocumented code, written by Claude under your own Claude Code
  login. It is the only feature that sends code anywhere, and it asks before it uses your plan quota. A free,
  local `--deterministic` pass is the alternative.
- `DEEPMAP_PRECISE_CALLS`: keeps the parser's exact call target instead of linking by name.
- `DEEPMAP_TYPED_CALLS=python,typescript,javascript`: links calls on objects by the object's declared type.
- `DEEPMAP_GRAPHIFY_CALLS_PLUS` (more Python calls, such as `module.func()`) and `DEEPMAP_CALLS_FAMILY_FILTER`
  (drops name matches between different language families).
- `DEEPMAP_IMPACT_PROFILE=orm`: model relations join the impact walk.
- `DEEPMAP_INCREMENTAL_SYNC=1`: re-parses only changed files on the Python-only fallback path; the graph is the
  same as a full rebuild.
- A git post-commit hook that re-syncs after each commit: `provision.py git-hook --install` (the terminal installers
  add it by default).

### Known limits

- Calls are linked by function name, not by the object's type, so in a real codebase most call links are
  `AMBIGUOUS`. Claude is told to open the `file:line` and verify.
- Calls on an object, such as `order.save()`, are mostly missing from the default graph; calls on `self` or `this`
  are kept. In Python, `module.func()` calls and calls at module level are missing too.
- Macs with Apple silicon are not verified yet, and Intel Macs are not supported.
- Only Neo4j 5.26 is tested.
- No with-vs-without benchmark result is published yet.
- The graph is a snapshot of your last sync, and search matches names and one-line summaries, not code bodies.

The full list, with the reasons and the workarounds, is in
[`docs/known-limitations.md`](docs/known-limitations.md).

Earlier versions were internal pre-releases and are not listed.
