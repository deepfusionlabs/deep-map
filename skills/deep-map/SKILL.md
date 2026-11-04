---
name: deep-map
description: >-
  Search and reason over THIS project's code by what it DOES, using the DeepMap code knowledge
  graph (Neo4j + intent-summary embeddings) via the deepmap_search MCP tool. Use when the user
  asks to find, locate, understand, or navigate code by behaviour/intent ("where is X handled",
  "what computes Y", "find the code that …"), to trace impact ("what calls / depends on Z"), or
  before refactoring (surface what a symbol does and who uses it first), or to ask the graph an ad-hoc
  structural question (list, count, group, or find paths) with read-only Cypher (deepmap_cypher).
  Routes natural-language questions to deepmap_search, cites file:line evidence, and caveats by
  confidence. It does not replace reading the code; it tells you where to read.
---

# DeepMap: reasoning over the project code graph

DeepMap is a per-project **Neo4j knowledge graph** of this codebase: every Function / Method /
Class / Module / Route is a node carrying an **intent summary** (what it *does*, not how it's
spelled), embedded for semantic search, joined by `:CALLS` edges **plus framework/runtime
relationship edges**: routes (`:ROUTES_TO`), dependency injection (`:DEPENDS_ON`), events/signals
(`:TRIGGERS`), scheduled jobs (`:SCHEDULED_AS`), lifecycle hooks (`:HOOKS`), inheritance
(`:INHERITS`/`:IMPLEMENTS`), plus `:IMPORTS`. It is
served to you over MCP. **Search by intent, then read the real code at the file:line it returns.**

## The tool

`deepmap_search(query: str, top_k: int = 15)` → ranked list of
`{id, name, type, file, line, summary, score, confidence}`.

- `type` ∈ Function / Method / Class / Module / Route.
- `summary` is the node's intent summary (a docstring first line, or a structure-derived one).
- `score` is a fused (vector + BM25 + graph-weighted, Weighted-RRF) rank score.
- `confidence` is the node's grade (`EXTRACTED` for a parsed node), the audit wedge; see below.

`deepmap_impact(symbol: str, limit: int = 30, edge_types: list[str] | None = None)` → what REACHES
`symbol`: multi-hop reverse reachability across `:CALLS` + the framework/runtime edges (routes, DI,
events, jobs, inheritance, cascades, view renders), as
`{caller, caller_type, file, line, confidence, callee, hop, via, via_path}`, plus `call_line` when the
graph recorded it. A row's `file:line` is where the caller starts (its definition), not the call.
`call_line` is the line in that file of the caller's first call to the next symbol on its path (only
the first call per caller/callee pair is recorded, so there may be more calls further down). It is
absent on a graph synced before call lines were recorded and on a row whose `via` is not `CALLS`: then
look for the call inside the caller. `via` is the wiring each dependent arrives through (e.g.
`ROUTES_TO`, `CALLS`); `hop` is its distance. Use it before
refactoring or to answer "what depends on / breaks if I change X", including runtime entry points a
grep misses. `confidence` (below) is the weakest edge on the path. `limit` counts result rows (one
per dependent–callee pair). `edge_types` REPLACES the default walk rather than adding to it: pass the
default edge types too (or leave it unset) unless you really want a narrower walk; ORM associations
(`HAS_MANY`/`REFERENCES`) are outside the default walk.

`deepmap_cypher(query: str, params: dict | None = None, limit: int = 100)` → the rows of ONE read-only
Cypher query over the same graph, for ad-hoc **structure** questions the other two don't answer:
listing ("every function in `src/api/`"), counting and grouping ("which classes have the most
subclasses", "how many AMBIGUOUS calls per file"), and paths between two symbols
(`shortestPath(... [:CALLS*..6] ...)`). Its tool description carries the schema (labels, the
relationship types, the `key` / `name` / `source_file` / `start_line` / `confidence` properties) and
examples. Pass values as `params` (`$name`), not pasted into the query. It is read-only: writes,
`LOAD CSV`, `USE`, admin commands, procedures outside a read-only allowlist and several statements
are rejected before the query reaches Neo4j, and the query runs in a READ transaction that is rolled
back. At most `limit` rows (max 500) and about 64 KB of text come back, without embedding properties or
lists of 256+ floats (a shorter slice of a vector is not left out); a trailing `{"note": ...}` is
DeepMap's own remark (rows cut, none matched; a column you named `note` comes back as `note_`). Prefer `deepmap_search` to find code by
what it does and `deepmap_impact` for blast radius: they rank, grade and caveat for you; reach for
`deepmap_cypher` when the question is about the SHAPE of the graph. Every link carries
`confidence`: a count or path that runs through `AMBIGUOUS` links (a name matched several symbols)
is unverified: say so, filter them out (`WHERE r.confidence <> 'AMBIGUOUS'`) or read the code. A
method's `name` is `Class.method`; an empty answer is not proof of absence (see coverage below).

There is also `deepmap_embed(text)` → a 768-d query vector, for pairing/advanced use; you rarely
need it directly: `deepmap_search` embeds the query for you.

## When to reach for it (vs. plain grep/read)

Use `deepmap_search` when the question is about **what code does or where behaviour lives** and the
literal tokens are unknown:

- "Where do we validate payments / handle auth / parse the config?"
- "What computes the fused score?" → finds `_weighted_rrf` even if the user never says "rrf".
- "Find the retry logic", "what talks to Neo4j", "where's the embedding model loaded".
- Impact / structure: "what calls `search`?", "what would break if I change this?".
- **Before a refactor:** look up the symbol's summary AND its callers first, so you don't change
  behaviour blind.
- Shape of the graph (use `deepmap_cypher`): "list the routes and their handlers", "which modules
  import X", "count callers per file", "is there a call path from A to B".

Prefer **grep/ripgrep** when you already know the exact identifier or string: DeepMap is for
*intent → location*, grep is for *known-token → occurrences*. They compose: DeepMap to find the
entry point, grep/read to trace the details.

## How to query well

- **Phrase the intent, not keywords.** The index is over what code *does*: query
  "send a confirmation prompt to the user" rather than "confirm()". Verbs + objects beat
  identifiers.
- **One concept per query.** Decompose a compound question ("how does sync push then index then
  embed?") into separate searches and synthesise; don't hope one query covers all of it.
- Start with the default `top_k`. If the top hits miss the concept, widen it, but keep the earlier
  top hits in view: the fused ranking shifts with `top_k`, so a wider list is not simply the old list
  plus extras.

## Reasoning patterns

1. **Search-by-intent** (the default): translate the NL question to an intent phrase, search, then
   open the top hits at their `file:line`.
2. **Evidence-chain**: never assert a location from the summary alone. Cite `name` + `file:line`,
   then READ that span to confirm before you act or answer. The summary points; the code proves.
3. **Impact / structural**: for "what depends on X" or before a refactor, call `deepmap_impact(X)`
   to get the callers with edge confidence; verify `AMBIGUOUS` edges by reading the call site (at
   `call_line` when the row has one; otherwise inside the caller that starts at `file:line`).
4. **Rationale-first**: before editing a symbol, retrieve and read its summary and callers so the
   change preserves intent and you know the blast radius.
5. **Confidence caveat**: see below.

## Reliability: do not fabricate

- **`score` is NOT comparable across queries.** It ranks results *within one query*; a 0.04 here and
  a 0.04 there mean nothing relative to each other. Use it for ordering within a result set only.
- **Zero results or an error is a real answer.** If `deepmap_search` returns nothing, or the graph
  is empty / Neo4j is unreachable, say so and fall back to grep/read; **do not invent a file:line**.
  An empty graph means the project hasn't been synced (`/deep-map:setup` or `/deep-map:refresh`).
- **Empty ≠ absent: check COVERAGE before concluding the code doesn't exist.** The graph can be
  healthy overall and still be missing an entire language or layer (a language the extractor didn't
  reach, a declarative/DSL layer, a partial sync). In that state `deepmap_search` returns nothing for
  code that is plainly on disk. So if you get zero results for something you have reason to believe
  exists (the user names it, or you can see the files), **do not report "there is no such code."**
  Run `/deep-map:doctor` (or `/deep-map:status`): it prints a per-language `N files, M nodes` coverage
  table and the last-sync verdict. `M ≈ 0` against a large `N` means *that layer is not indexed*:
  use Grep/Read for it, say plainly that DeepMap doesn't cover it, and suggest `/deep-map:refresh`.
  A `DEGRADED` verdict names the affected layer in its reasons. Treat coverage as part of the answer:
  "DeepMap indexes X but not Y, so I checked Y by reading" is a correct, honest response.
  The SessionStart note and the first Grep/Glob reminder also name source files that are not in the graph at
  all, with the reason (for example `.r` or `.pl` files no supported parser reads, or `.sql` files while
  DeepMap's SQL reader is off); for those use Grep/Read. SQL objects are declarations only, and SQL outside
  `.sql` files (`.txt` scripts, SQL strings in code, SQL built at run time) is not in the graph: Grep for a
  table or procedure's users and for schema changes kept elsewhere.
- **A "background refresh is running" note means answers still come from the previous build.** DeepMap refreshes
  the graph by itself in the background when a session starts; until a later DeepMap note says it finished, code
  changed since the last build may be missing and, while it writes, results may briefly mix old and new code.
  Confirm recently changed code by reading it, and do not start `/deep-map:refresh` just because of that note:
  a later note says how the refresh ended (and suggests `/deep-map:refresh` when it did not update the graph). A
  note that the graph "still holds N node(s) for code that may have been removed" means a result naming a symbol
  you cannot find in its file is stale. Never describe DeepMap's graph as real-time.
- **An empty or partial `deepmap_impact` answer is not proof that nothing (else) depends on the
  symbol.** The extractor does not resolve every call (method/receiver calls through variables,
  dynamic dispatch, reflection), so grep for the name before calling a symbol a leaf or a list of
  callers complete, and relay any note the tool attaches (a result cut at `limit`, a walk that fell
  back to one hop). C# is the clearest case: by default a call from another file through an object (a
  field, parameter or local), a type name, an interface or a mediator (MediatR `Send`) is not linked,
  so C# dependents are a lower bound and an empty C# answer says little: grep the name, and relay the
  C# note the tool attaches.
- **The summary can be stale or thin** for undocumented code (it's synthesised from structure by
  default; if the optional opt-in enrichment tier is enabled it may instead be an LLM-written intent
  summary). Either way treat it as a hint at lower confidence than a real docstring; the source at
  `file:line` is ground truth: read it.
- **Caveat by confidence.** Each result carries a `confidence` field, and CALLS edges in the graph
  are tagged: `EXTRACTED` (directly parsed) · `INFERRED` (matched by name to exactly one symbol in
  the graph; likelier, but still name-based: a same-named library call can land on it) · `AMBIGUOUS`
  (the callee name maps to several symbols, so the true target is uncertain). When an
  impact/"what calls X" answer rests on `AMBIGUOUS` edges, say so and verify by reading the call
  site: `call_line` when the row has one, else the caller's body (an impact row's `file:line` is
  where the caller starts). This is the audit wedge: you can always cite the node, its `file:line`,
  and how sure the graph is. DeepMap does not learn from your usage; by default it uploads nothing.
  The one opt-in exception is summary enrichment (off by default), which sends undocumented code to
  your existing Claude session to write better summaries, nowhere you aren't already sending code via
  Claude Code.

## Setup (only if the tool is unavailable)

If `deepmap_search` isn't responding, the graph may not be built or the MCP servers may be down.
Run `/deep-map:doctor` for a full health check (or `/deep-map:status`), `/deep-map:setup` to stand
everything up, or `/deep-map:refresh` after code changes. The servers run locally over HTTP; by default
nothing leaves the machine (the optional opt-in enrichment tier, if you enable it, uses your existing
Claude session).

If the deepmap tools are missing from your tool list or fail with a connection error early in a session
(the servers are revived in the background at session start, often after Claude Code has already tried
to connect), ask the user to run `/mcp` and reconnect `deepmap-search` and `deepmap-embed` (or reload).
The same applies right after a DeepMap plugin update: the project's servers still run the previous
version's code until they are restarted on the new one (the session-start heal does it in the background;
a note at session start, or at your first search after the restart, says so), and the client's
connection drops when they are replaced: reconnect via `/mcp`; `/deep-map:setup` restarts them at once.
If they still fail after that, fall back to grep/read and suggest `/deep-map:setup`. A session-start note
saying DeepMap must NOT be used for this project (its database or MCP port serves another project's
graph) means exactly that until `/deep-map:setup` succeeds: do not use those tools for this project.

Each project gets its **OWN Neo4j instance** by default (its own data dir + bolt port), so
projects run side by side without switching. The **one-active-project switch guard** applies ONLY on the
explicit `NEO4J_URI` escape hatch (several projects pointed at one shared instance): there, if a refresh is
refused because the graph belongs to a different project, that's the switch guard, not a failure: re-run the
sync with `--switch-project` to rebuild cleanly for the current project (syncing the same project or a
subdirectory is always fine).

**Which graph is which** (don't conflate them): DeepMap's queryable graph is the **live Neo4j**
served via `deepmap_search`/`deepmap_impact`; it is not a JSON file. A `graphify-out/graph.json`
in the workspace is only DeepMap's raw extractor output (a build input consumed by sync). A
`graph_enriched.json` or `.ai/` directory belongs to **DeepInit, a different plugin entirely**;
checking that file's freshness says nothing about the DeepMap graph (use `/deep-map:status` for that).
