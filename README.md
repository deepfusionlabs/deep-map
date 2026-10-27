# DeepMap

**Code RAG that reasons, not just retrieves. Graph + Vector + FTS. 100% local.**

DeepMap is a Claude Code plugin that searches your code three ways at once: **graph** (how code connects),
**vector** (what code means) and **full-text** (names and keywords, with fuzzy matching for typos and spelling).
It fuses the three into one ranked list. Then Claude reasons over it, with a `file:line` on every result and a
confidence label on every link.

- **Impact analysis in one call:** callers, routes, jobs and cascades, up to 3 hops out.
- **Multi-language:** 25 programming languages parsed, 8 of them with their own end-to-end test suites, plus
  framework wiring for Python, Rails, .NET, Java, Go and TypeScript / JavaScript.
- **MIT, 100% local:** no cloud, no API key, no Docker.

RAG is retrieval-augmented generation: Claude looks up your code before it reasons and answers.

> DeepMap is an early release, and features may change. [`CHANGELOG.md`](CHANGELOG.md) lists what changed.

**[Install](#install)** · [How it works](#how-it-works) · [Known limits](#known-limits)

## The whole idea, in three lines

1. **Retrieves three ways at once.** Graph + Vector + FTS, fused into one ranked list.
2. **Reasons before it answers.** A bundled skill tells Claude to follow the links and read the cited code.
3. **Backs every answer with evidence.** A `file:line` on every result, a confidence label on every link.

## Three retrieval modes. One ranked answer.

One call to `deepmap_search` runs all three at once and merges their rankings. You never pick a mode.

| Mode | What it does | Example |
|---|---|---|
| **Graph**: how your code connects | Your code as nodes and links in Neo4j, a graph database. In search, code with more callers ranks higher. For "what breaks", DeepMap walks the links backwards, up to 3 hops, across calls and framework wiring. | "Who calls this? What depends on the User model?" |
| **Vector**: what your code means | Each function, method and class gets a one-line intent summary. A local model on your CPU turns it into an embedding, a list of numbers that captures meaning, so vector search finds code by what it does even when your words don't appear in it. | "Find the code that retries failed uploads." |
| **FTS**: names, keywords and fuzzy matches | Full-text search (FTS) matches your words against names and summaries, the way a search engine does, and ranks the hits by relevance. It is fuzzy: a typo or a different spelling still finds the name. | `retry_faild_charges` still finds `retry_failed_charges` |

**Fused with RRF.** Reciprocal Rank Fusion, a standard way to combine ranked lists, merges the three rankings into
one. A result that several modes agree on rises to the top.

<details>
<summary>The ranking, exactly</summary>

- **Vector:** similarity between your question and each symbol's name plus its one-line summary (a 768-dimension
  local model).
- **Full-text:** BM25 relevance ranking over names and summaries. Every word of three or more characters is matched
  fuzzily, within 2 edits, so typos and spelling variants still hit. A method is indexed under its `Class.method`
  name.
- **Graph:** the vector score boosted by the number of call links pointing at the code:
  `score × (1 + log(1 + callers))`. Calls are matched by name, so common names get a lift too.
- **Fusion:** Reciprocal Rank Fusion with k = 60, implemented directly in `build/retrieval.py`. By default the
  three modes carry equal, fixed weights, which is exactly plain RRF.

</details>

## Your agent greps. Grep can't see how your code connects.

Text search is perfect for finding a name. But "what breaks if I change this?" is about connections, and many
never appear as text. Vector search alone finds similar code; it cannot follow a call chain.

- **"What breaks if I change this function?"** Grep finds the lines that mention it, and finding the callers of
  those callers takes more searches. DeepMap walks up to 3 hops out in one call and labels how sure it is about
  each result.
- **"What's wired to this: routes, jobs, injected services?"** Grep sees decorators and config, not the
  connections they create. DeepMap stores each one as a typed link: a route to its handler, a job to its function.
- **"What gets deleted along with a Customer?"** Grep finds each declaration; following the chain is on you.
  DeepMap follows declared cascades hop by hop in Rails, Django, SQLAlchemy, EF Core, JPA, GORM and TypeORM.

Here is "What breaks if I change `charge_card()`?" on a six-file example app with a checkout API and a background
job. Grep needs five searches, and only finds the job and the route if you know to look for the decorators.
DeepMap answers in one call:

```
/deep-map:impact "charge_card"

6 dependent(s) of 'charge_card' (reverse reachability; hop · via · confidence):
  [INFERRED ]  1h via CALLS        place_order                      -> charge_card            shop/cart.py:5
  [INFERRED ]  1h via CALLS        retry_failed_charges             -> charge_card            jobs/retries.py:10
  [INFERRED ]  1h via CALLS        test_charge_declined             -> charge_card            tests/test_payments.py:4
  [INFERRED ]  2h via SCHEDULED_AS retries                          -> charge_card            jobs/retries.py:1
  [AMBIGUOUS]  2h via CALLS        create_order                     -> charge_card            api/orders.py:10
  [AMBIGUOUS]  3h via ROUTES_TO    orders                           -> charge_card            api/orders.py:1

  Note: 2 edge(s) are AMBIGUOUS (the name maps to multiple symbols, or a reflective/dynamic hop) — verify by reading the source.
```

Each row: confidence label, hops away (`1h` is a direct caller), kind of link, dependent → what you asked about,
`file:line`. `retries` is the file that registers the Celery task, and `orders` the file that declares
`POST /orders`. A row shows the weakest label on its path. An `AMBIGUOUS` row is a lead to confirm: Claude is told
to open the `file:line` and verify. Computed by DeepMap, not drawn: its own extraction and link grading produced
these rows on the example app.

**Language servers and other code-graph tools.** Use a language server too: it resolves calls by type, which
DeepMap's default graph doesn't, and Claude Code can reach it through LSP plugins.
Many tools now give AI agents a call graph, and some also combine graph, vector and keyword search. DeepMap's focus
is the wiring frameworks hide from a call graph: routes, injected services, jobs, events, callbacks and cascading
deletes, read in six ecosystems and walked together with calls in one impact query, with a confidence label on every
link. It all lives in a real database on your machine, under the MIT license.

## What you get

A code graph, 4 tools, a reasoning skill, hooks and 8 commands. The tools are served over MCP, the standard way
Claude Code plugs in tools, here over HTTP on localhost:

- **`deepmap_search`:** graph + vector + full-text, fused into one ranked list. About half a second per search
  (median, [measured](docs/testing.md#what-was-measured)).
- **`deepmap_impact`:** what depends on a symbol. 3 hops by default, up to 5 from the command line; 30 rows by
  default.
- **`deepmap_cypher`:** read-only Cypher, Neo4j's query language, for structure questions: list, count, group, find
  paths. Writes are refused, and each answer is bounded in time, rows and size, without embedding vectors. From a
  terminal: `python <plugin folder>/build/deepmap_cli.py cypher "<query>"`.
- **`deepmap_embed`:** embeds any text with the same local model as search.

| Command | What it does |
|---|---|
| `/deep-map:setup` | Set up DeepMap for this project, or repair it. Safe to re-run. |
| `/deep-map:refresh` | Update the graph after code changes. Re-embeds only what changed and removes deleted code. |
| `/deep-map:impact "<symbol>"` | The impact report on demand. |
| `/deep-map:status` | What's in the graph, whether it is behind your latest commit, and whether the servers are up. |
| `/deep-map:doctor` | A full health check with a next step for anything that isn't OK. |
| `/deep-map:enrich` | Opt-in: better summaries for undocumented code, written by Claude on your own plan. |
| `/deep-map:version` | Which version is loaded right now. |
| `/deep-map:plugin-update` | Updates DeepMap for you, then tells you how to load the new version. |

You normally just ask. The bundled skill ([`skills/deep-map/SKILL.md`](skills/deep-map/SKILL.md)) routes a code
question to the right tool: search to find code, impact to see what breaks, read-only Cypher to list and count. It
tells Claude to open the cited `file:line` before answering, to trace the impact before a refactor, to say how sure
each link is, and to fall back to Grep and Read when the graph is empty, unreachable or doesn't cover a language.

**What happens when your code changes.** When an interactive Claude Code session starts and your code changed since
the last build, DeepMap refreshes the graph in the background. Claude is told that it is running and how it ended.

- It runs offline, at low CPU priority, at most once every 15 minutes for the same commit.
- It skips a project whose sync takes more than 20 minutes (`DEEPMAP_AUTO_SYNC_MAX_S` raises the limit).
  `/deep-map:status` shows how the last one ended, or why it was skipped.
- It is not a file watcher, and it removes deleted code only when it has re-read the file or the file is gone. So
  `/deep-map:refresh` is still the full clean-up, and it brings in this session's edits at once. A refresh you start
  takes over from the background one.
- The optional git hook also re-syncs after each commit, but only adds and updates.
- `DEEPMAP_AUTO_SYNC=0` turns it off. `claude -p` and SDK sessions start it only with `DEEPMAP_AUTO_SYNC=1`.

## Every answer shows its evidence

Every result points to a `file:line` you can open. Every link in the graph carries one of three confidence labels:

| Label | Meaning |
|---|---|
| `EXTRACTED` | Read directly from your code: the declaration and its target are in the same file, like a route decorator above its handler. Import lines too. |
| `INFERRED` | Matched by name to exactly one symbol: only one function, method or class has that name. Likelier, but still a name match: a call on another object or library with that name can land on it. |
| `AMBIGUOUS` | Several matches, or a dynamic call. A lead, not a fact: open the `file:line` and verify. |

Setup reports a verdict too: `READY`, `DEGRADED` or `FAILED`, and `DEGRADED` names the gap. `/deep-map:status`
compares the graph with your latest commit.

## Framework wiring

A URL runs a handler. A service is injected. A job is scheduled. A delete cascades. None of that is written as a
call. DeepMap reads these declarations in 6 ecosystems and stores each as a typed link Claude can follow. This layer
is DeepMap's own, added on top of the parsed code graph; every other language still gets calls and imports.

The 12 kinds of framework link: route → handler `ROUTES_TO`, injected dependency `DEPENDS_ON`, scheduled job
`SCHEDULED_AS`, event → listener `TRIGGERS`, hook or callback `HOOKS`, cascading delete `CASCADES`, view → partial
`RENDERS`, inheritance `INHERITS`, interface `IMPLEMENTS`, model relations `HAS_MANY` and `REFERENCES`, and
reflective call `DISPATCHES`.

| Ecosystem | Routes | Injected dependencies | Jobs & schedules | Events & signals | Hooks & callbacks | Cascading deletes | Model relations | View renders |
|---|---|---|---|---|---|---|---|---|
| Python | Flask, FastAPI, Django URLconfs, click commands | FastAPI `Depends` | Celery, APScheduler | Django signals | Flask and FastAPI hooks | SQLAlchemy `cascade`, `ondelete`; Django `on_delete` | SQLAlchemy (classic and `Mapped[...]`), Django | — |
| Ruby on Rails | `routes.rb` | — | — | — | model callbacks | `dependent:` | ActiveRecord | `.erb` views and partials |
| C# / .NET | ASP.NET Core attributes, minimal APIs (named handlers) | constructor parameters, service registrations | — | — | — | EF Core `OnDelete` | EF Core | — |
| Java | Spring MVC | constructor, `@Autowired` | `@Scheduled` | `@EventListener` | — | JPA `cascade` | JPA | — |
| Go | Gin, Echo, Fiber, Chi, net/http | constructor functions | — | — | — | GORM `OnDelete` | GORM | — |
| TypeScript / JavaScript | NestJS, Express, Fastify, koa-router, Hono, Next.js route handlers, Angular (named handlers) | NestJS and Angular constructors, `@Inject`; tsyringe, InversifyJS, TypeDI | NestJS `@Cron`, `@Interval`, `@Timeout`; Bull `@Process` | NestJS `@OnEvent`, microservice and WebSocket handlers | NestJS guards, interceptors, pipes, filters, middleware; TypeORM entity listeners; Angular route guards | TypeORM `onDelete`, MikroORM `deleteRule`, a remove `cascade` | TypeORM, MikroORM | — |

A dash marks a link DeepMap doesn't read yet. **Not read yet:** in Python, Django REST framework routers and SQLModel
models; in TypeScript and JavaScript, GraphQL resolvers, Prisma and Sequelize models, Next.js middleware and server
actions, and Vue or React Router tables; and any framework wiring in Rust.

The readers are pattern-based, each with committed tests. A link a reader can't resolve is dropped, never invented.
Cascades work when they are declared in model code (EF Core: an explicit `OnDelete` only); a set-null rule is
recorded as a cascade link too, so open the declaration to check. `ON DELETE` rules that live only in migrations or
the database are not read yet. What each reader reads, and what it misses:
[`docs/multi-language-support.md`](docs/multi-language-support.md) and
[`docs/known-limitations.md`](docs/known-limitations.md).

## Languages

A multi-language tree-sitter engine parses all 25 programming languages into the same code graph (functions,
classes, calls, imports), and all three search modes cover every one. Of these, 8 also have their own end-to-end
test suites, built from real extractor output.

| Language | Graph + Vector + FTS | Own end-to-end tests | Search, proven end-to-end | Framework wiring |
|---|:-:|:-:|:-:|---|
| **8 languages with their own end-to-end test suites** | | | | |
| Python | ✓ | ✓ | ✓ | Flask, FastAPI, Celery; Django URLs, signals and models; SQLAlchemy |
| JavaScript | ✓ | ✓ | — | Express, Fastify, koa-router, Hono, Next.js |
| TypeScript | ✓ | ✓ | ✓ | NestJS, Express, Fastify, koa-router, Hono, Next.js, TypeORM, MikroORM, Angular |
| Go | ✓ | ✓ | ✓ | Gin, Echo, Fiber, Chi, net/http, GORM |
| Rust | ✓ | ✓ | ✓ | — |
| Java | ✓ | ✓ | — | Spring MVC, JPA |
| Ruby | ✓ | ✓ | — | Rails, ActiveRecord, `.erb` views |
| C# | ✓ | ✓ | — | ASP.NET Core, EF Core |
| **17 more, same engine and pipeline (Pascal/Delphi also has its own test suite)** | | | | |
| C | ✓ | — | — | — |
| C++ | ✓ | — | — | — |
| PHP | ✓ | — | — | — |
| Kotlin | ✓ | — | — | — |
| Scala | ✓ | — | — | — |
| Swift | ✓ | — | — | — |
| Lua | ✓ | — | — | — |
| Elixir | ✓ | — | — | — |
| Objective-C | ✓ | — | — | — |
| Julia | ✓ | — | — | — |
| Bash | ✓ | — | — | — |
| PowerShell | ✓ | — | — | — |
| Groovy | ✓ | — | — | — |
| Zig | ✓ | — | — | — |
| Fortran | ✓ | — | — | — |
| Verilog | ✓ | — | — | — |
| Pascal/Delphi | ✓ | ✓ | — | — |

Call coverage varies. Calls on objects (`order.save()`) are mostly missing in every language tested, and so are
Python `module.func()` calls (see [Known limits](#known-limits)).

## Install

Three steps, all inside Claude Code. No terminal, no API key, no Docker.

Needs Claude Code, Python 3.10+ (as `python` or `python3`) and Java 17 or 21 (for Neo4j). Tested on Windows 11 and
Linux. Macs with Apple silicon aren't verified yet, and Intel Macs aren't supported.

1. **Add the plugin** from its public GitHub repository, `deepfusionlabs/deep-map`. In Claude Code:

   ```
   /plugin marketplace add deepfusionlabs/deep-map
   /plugin install deep-map@deepfusionlabs-deep-map
   ```

2. **Run `/deep-map:setup` in your project.** The first run installs DeepMap's own Python environment (never into
   your Python), downloads the embedding model and Neo4j, builds the graph and starts the local MCP servers. It
   checks Java and free disk before it downloads anything.

3. **Start a new Claude Code session** (approve the two deepmap servers if Claude Code asks) so
   `deepmap_search`, `deepmap_impact`, `deepmap_cypher` and `deepmap_embed` connect. Then ask in plain English:
   "What breaks if I change `charge_card()`?", "Find the code that retries failed uploads.", "Which classes get
   `UserRepository` injected?"

   If the tools don't appear (Claude Code did not ask, or you declined), approve the two servers yourself: add
   `"enabledMcpjsonServers": ["deepmap-search", "deepmap-embed"]` to the project's `.claude/settings.local.json`
   and start a new session, or run `claude mcp reset-project-choices` in the project to be asked again. `claude -p`
   and Agent SDK sessions load them without asking.

The first setup on a machine downloads about 1 GB once; in our runs DeepMap's environment took 3 to 11 minutes. On
a busy machine, setup took 12 to 14 minutes on projects of about 700 to 1,300 symbols, and about 70 minutes on one
of about 6,300, the largest tested ([measurements](docs/testing.md#what-was-measured)). To add another project, run
`/deep-map:setup` inside it.

<details>
<summary>Install details</summary>

- **Downloads:** DeepMap's Python packages (PyTorch's CPU build included), the embedding model (about 0.6 GB) and
  Neo4j Community from its official source, checksum-verified and never bundled. About 2.5 GB on disk afterwards
  (about 3.2 GB on Linux); keep about 4 GB free while it installs. Later runs reuse all of it; other projects reuse
  the environment, the model and the Neo4j download, and each gets a Neo4j store of its own.
- **While it runs:** about 2 to 3 GB of RAM per project: a Neo4j process (Java heap capped at 512 MB by default;
  `DEEPMAP_NEO4J_HEAP_MAX` changes it) and two local servers that keep the embedding model loaded.
  `python <plugin folder>/build/deepmap_cli.py hibernate` stops idle projects' databases; the plugin folder is
  Claude Code's copy of DeepMap (`~/.claude/plugins/cache/deepfusionlabs-deep-map/deep-map/<version>`).
- **Which Python runs DeepMap:** when your Python lacks DeepMap's packages, setup builds DeepMap's own environment at
  `~/.deepmap/venv`, checks it with [`scripts/ml_stack_selfcheck.py`](scripts/ml_stack_selfcheck.py), and every
  `/deep-map:*` command then runs under it by itself. A Python that already has every package inside the ranges
  [`requirements.txt`](requirements.txt) pins (for example, from the installers below) is used as is; one holding a
  package outside them is left alone, and DeepMap runs from its own environment instead.
- **Debian and Ubuntu:** setup needs Python's venv module (`sudo apt install python3-venv`) and names the command
  when it is missing.
- **The git hook:** with `/deep-map:setup` alone there is no hook until you add one. Setup prints the command,
  `python <plugin folder>/build/provision.py git-hook --install <project>`, which adds a git post-commit hook that
  re-syncs the graph after each commit, on top of the refresh at session start.
- **One Neo4j of your own:** setting `NEO4J_URI` points DeepMap at it instead of a per-project instance; that
  database then holds one project's graph at a time.

</details>

<details>
<summary>Terminal alternative: install.sh and install.ps1</summary>

[`install.sh`](install.sh) / [`install.ps1`](install.ps1) install the packages into the first Python they find
(`python3`; on Windows `py -3`, else `python`), pre-download the model (about 0.6 GB, the same files
setup downloads), run the same self-check, run setup and add the git post-commit hook. Run one **from your project's
directory**: the directory it starts in is the project it sets up (or name the project explicitly):

```bash
# macOS / Linux
cd /path/to/your/project
bash /path/to/deep-map/install.sh        # or: REPO_ROOT=/path/to/your/project bash install.sh
# Windows (PowerShell)
cd C:\path\to\your\project
& C:\path\to\deep-map\install.ps1        # or: .\install.ps1 -RepoRoot C:\path\to\your\project
```

Running it from the DeepMap folder itself would set up DeepMap's own source as a project; add `SKIP_PROVISION=1` /
`-SkipProvision` to install only the ML stack and run `/deep-map:setup` in your project afterwards. Skip the git
hook with `SKIP_GIT_HOOK=1` / `-SkipGitHook`.

</details>

<details>
<summary>Long syncs and exit codes</summary>

A first build of a large repository can take many minutes, so the slash commands run it in the background. The
parsing engine's time budget grows with the repository; [`commands/refresh.md`](commands/refresh.md) shows how to
raise it. When the engine is missing, crashes or runs out of time, a Python-only fallback indexes the Python code
instead (verdict `DEGRADED`); over an existing graph that fallback is refused and the previous graph is kept.

A sync exits `0` (synced, or queued behind a sync of the same project that is already running), `1` (refused,
failed, or verdict FAILED), `3` (Neo4j unreachable, or a database error) or `4` (NOT PUSHED: the sync ran but pushed
nothing, so the previous graph still answers and may be stale). `provision.py setup` also exits `4` in that case,
and the installers treat it as a warning.

</details>

**Updating.**

1. Run [`/deep-map:plugin-update`](commands/plugin-update.md). It updates DeepMap for you through the `claude` CLI,
   checks that the new version landed, and tells you how to load it (in the VS Code extension, open a new chat).
   [`/deep-map:version`](commands/version.md) shows what's loaded right now.
2. Load the new version in every open Claude Code window. An older DeepMap left open in another window can misread a
   database the new setup has upgraded.
3. Run `/deep-map:setup` once in each project. It re-syncs the graph and restarts the project's MCP servers onto the
   new code. When the new version pins a newer Neo4j patch release, setup also moves the project's database to it:
   the move copies the database, checks that the new release gives the same answers, and otherwise
   puts the old one back. When disk or Java is short, setup says so and keeps the old release.
   `DEEPMAP_NEO4J_AUTO_UPGRADE=0` skips the move.

<details>
<summary>Removing DeepMap</summary>

There's no uninstall command yet, so removal is manual. In each project, stop its servers and database and remove its
git hook with DeepMap's provisioner, run from the project: `python <plugin folder>/build/provision.py mcp-stop`,
then the same with `stop` and with `git-hook --uninstall` (or ask Claude to). Then remove the plugin in Claude
Code's `/plugin` menu and delete `.deepmap/` and the two DeepMap entries in the project's `.mcp.json`. DeepMap's
Python environment, the Neo4j download and every graph stay under `~/.deepmap`, and the model in your Hugging Face
cache, until you delete them.

</details>

## How it works

Parse. Index. Query. Reason. Proven open-source parts in one local pipeline:

```
your code ──▶ Parse: a multi-language tree-sitter engine + DeepMap's 6 framework readers (no network, no LLM by default)
          ──▶ Graph: Neo4j Community 5.26, one instance per project, localhost only; Function, Method, Class and Module
                     nodes; 14 kinds of link, each with a confidence label
          ──▶ Index: vector (768-dimension embeddings of the intent summaries, on your CPU) + full-text (BM25, fuzzy)
          ──▶ Query: deepmap_search (graph + vector + full-text, fused with RRF), deepmap_impact (the backwards walk),
                     deepmap_cypher (read-only Cypher) and deepmap_embed, over MCP
          ──▶ Reason: Claude Code, with the reasoning skill, the hooks and the /deep-map commands
```

Every function, method and class gets a one-line intent summary. In Python it is the opening of the docstring;
otherwise it is built from the name and what the code calls. ModernBERT-embed by Nomic AI, a general-text model (not
a code-trained one), embeds the summaries, not raw code. Both indexes live in the same Neo4j instance as the graph.

<details>
<summary>How the impact walk works</summary>

- It walks backwards from the symbol through calls and the framework wiring: routes, injected dependencies, jobs,
  events, hooks, inheritance, cascading deletes and view renders. Model relations (`HAS_MANY`, `REFERENCES`) join
  the walk with `DEEPMAP_IMPACT_PROFILE=orm`.
- 3 hops for Claude and the slash command; from a terminal,
  `python <plugin folder>/build/deepmap_cli.py impact "<symbol>" --max-hops 5` goes up to 5. The default limit is
  30 rows.
- Notes travel with the answer: when the list was cut at the limit, when a very widely used name was cut back to one
  hop, when a symbol isn't in the graph, and when a method's callers are only a lower bound.

</details>

<details>
<summary>Open the graph yourself</summary>

- Every project gets its own Neo4j instance, with its own data folder and ports, all run from one shared Neo4j
  install. The first project normally uses the standard ports; others get the next free ones, and a Neo4j that isn't
  DeepMap's is left alone.
- It is a real Neo4j database. Neo4j Browser runs on the instance's HTTP port (http://127.0.0.1:7474 for your first
  project). Sign in as `neo4j` with the password `deepmap`, a local development password on a localhost-only port,
  and run your own Cypher, such as `MATCH (a)-[r:ROUTES_TO]->(h) RETURN a, r, h LIMIT 25`.
- Claude can run read-only Cypher too, through `deepmap_cypher` ([What you get](#what-you-get)).
- `/deep-map:status` lists every instance: running or stopped, its size, and when it was last used.

</details>

## Seven defensive layers. No single point of failure.

Claude decides when to use a tool, and that decision can fail. A graph can fall behind your code. An outage can
look like an empty answer. Seven layers cover these:

1. **Self-heal at session start:** after a reboot, a hook revives this project's database and servers and tells
   Claude whether the graph is ready, stale or incomplete. If the code changed, it also refreshes the graph in the
   background.
2. **Pre-search nudge:** before a text search, a once-per-session reminder that the tools exist. It never blocks the
   search.
3. **Post-edit sync flag:** after an edit, a once-per-session note that the graph may be stale. It never blocks the
   edit.
4. **Git post-commit sync** (optional): each commit re-syncs the graph in the background, so commits never wait. A
   sync that could not rebuild the graph shows up as a stale graph in `/deep-map:status` (its log is
   `~/.deepmap/sync.log`).
5. **Skill guidance:** the skill tells Claude to read the cited code, caveat by confidence label and fall back to Grep
   and Read.
6. **Fail loud on outage:** an outage is reported as an error, never as an empty result. If the embedding model can't
   load, search falls back to keywords and says so.
7. **No-regression ranking check:** a test fails if the ranking functions change, so the ranking formula can't change
   unnoticed between releases.

The hooks run with `python` and fall back to `python3` where `python` is absent. The pre-search and post-edit hooks
took 0.3 to 1.4 seconds per run ([measured](docs/testing.md#what-was-measured)); another runs after each search,
impact or Cypher call and passes on how a background refresh went. `DEEPMAP_NUDGE=0`, `DEEPMAP_SYNC_FLAG=0` and `DEEPMAP_FRESHNESS_NOTE=0` silence them;
`DEEPMAP_HOOKS=0` turns off every DeepMap hook, the session-start self-heal and the background refresh included.

## How it's tested

DeepMap is tested end to end on live database instances, not only with mocks: 317 live test cases across 10 areas,
from setup and search to hooks and failure modes. More than 200 automated test suites run with
`python tests/run_all.py` on every push, and each fix starts as a test that fails on the old code. 300 generated
source files fuzz each of the Rails, C#, Java, Go and TypeScript framework readers. AI test agents ran the live cases,
and two independent AI reviewers re-checked the findings of the main test runs; a maintainer decides what ships
([`AI_POLICY.md`](AI_POLICY.md)). What was tested, where, and what was measured: [`docs/testing.md`](docs/testing.md).

## Privacy

- **Runs on your machine.** By default nothing DeepMap builds leaves your machine. The database listens only on
  localhost.
- **No cloud, no telemetry.** DeepMap collects no telemetry, and setup switches off Neo4j's own usage reporting.
- **No API key, no new subscription.** By default no LLM parses or summarizes your code. The embedding model runs on
  your CPU.
- **The one opt-in exception** is enrichment (`/deep-map:enrich`, off by default), which sends undocumented functions
  to Claude under your own Claude Code login: the same place Claude Code already sends your code.
- **One network call you may see:** a sync started by the git hook lets the model library ask huggingface.co whether
  its files are current. It sends nothing about your code, and `HF_HUB_OFFLINE=1` turns it off.

## Known limits

- Calls are linked by function name, not by the object's type.
  A plain call to a common name, such as `run()`, links to every function called `run`, so in a real codebase most
  call links are `AMBIGUOUS` (83 to 98% in three measured codebases), and Claude is told to open the `file:line` and
  verify. A stricter mode, `DEEPMAP_PRECISE_CALLS=1`, keeps the parser's exact target instead; it is opt-in.
- Calls on an object, such as `order.save()`, are mostly missing from the graph, even when the code declares
  the object's type; calls on `self` or `this` are kept. We saw this in Python, TypeScript, Go, Rust and
  C#. In Python, `module.func()` calls and calls at module level are missing too. An opt-in, type-based call linker
  for Python, TypeScript and JavaScript (`DEEPMAP_TYPED_CALLS=python,typescript,javascript`) links many of these
  calls by the object's declared type. It is off by default: in our measurement it added no wrong links, but it has
  not yet passed every check we set for it in advance.
- It reports what it can trace. It is not a proof that nothing else depends on your code.
- The graph is a snapshot of your last sync, not of your working tree.
- One graph per project. No cross-repository queries.
- Search matches names and one-line summaries, not code bodies. For an exact string or a regex, grep is still the
  right tool.
- No data-flow or taint analysis. DeepMap never edits code.
- Early release: Macs with Apple silicon aren't verified yet and Intel Macs aren't supported (PyTorch has no current
  build for them), only Neo4j 5.26 is tested, and the largest project tested has about 6,300 symbols
  ([`docs/testing.md`](docs/testing.md)).

The full list, with the reasons and the workarounds: [`docs/known-limitations.md`](docs/known-limitations.md).

## Licensing and credits

DeepMap is made by [Deep Fusion Labs](https://deepfusionlabs.ai) and is MIT-licensed. It runs on a local Neo4j
Community Edition instance (GPLv3), which DeepMap downloads from the official source but does not redistribute or
modify, and accesses over the standard Bolt protocol. DeepMap's own MIT-licensed code is independent of Neo4j's
license. If you use DeepMap commercially, review Neo4j's licensing terms
(or consult counsel) for your use of Neo4j Community.

DeepMap is built on open foundations: the graphify multi-language tree-sitter parsing engine (MIT), Neo4j Community
Edition (GPLv3, fetched at install and never bundled), the ModernBERT-embed model by Nomic AI (Apache-2.0) and the
Model Context Protocol. Third-party attributions are in [`NOTICE`](NOTICE) and
[`THIRD-PARTY-LICENSES.md`](THIRD-PARTY-LICENSES.md); a CycloneDX SBOM is in [`sbom.cdx.json`](sbom.cdx.json). Full
license text: [`LICENSE`](LICENSE).

Neo4j is a trademark of Neo4j, Inc. DeepMap is an independent project, not affiliated with or endorsed by Neo4j, Inc.

## Contributing & community

Contributions are welcome. Start with [`CONTRIBUTING.md`](CONTRIBUTING.md) for the dev setup, the RED-before-GREEN
test discipline (`python tests/run_all.py`), and the load-bearing invariants. By taking part you agree to our
[`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md).

- **Found a bug or have an idea?** Open a GitHub issue (templates provided).
- **Found a security vulnerability?** Please do **not** open a public issue; report it privately per
  [`SECURITY.md`](SECURITY.md).
- **Curious how AI is used to build DeepMap?** See [`AI_POLICY.md`](AI_POLICY.md).

## Status of this repository

DeepMap is an early release, versioned 0.x: features and defaults can change from one release to the next, and
[`CHANGELOG.md`](CHANGELOG.md) lists what changed in each one.

This repository holds the whole plugin: the product under [`build/`](build/) (provisioner, sync, indexes,
embeddings, the retriever, the framework readers and the two MCP servers), the [`/deep-map`](commands/) commands,
the reasoning [skill](skills/deep-map/SKILL.md), the hooks under [`hooks/`](hooks/), the test suite under
[`tests/`](tests/) ([how DeepMap is tested](docs/testing.md)) and the with-vs-without benchmark harness under
[`benchmark/`](benchmark/). No with-vs-without benchmark result is published yet; the benchmark will be run on the
current graph before any number is published. The call-linking decision records under
[`benchmark/call_linking/`](benchmark/call_linking/) include the pre-registered measurements (link precision against
jedi and the TypeScript checker, and a 65-query search check on DeepMap's own code) behind keeping those modes off by
default.

Report a bug or an idea in a [GitHub issue](https://github.com/deepfusionlabs/deep-map/issues). Report a security
problem privately, as [`SECURITY.md`](SECURITY.md) describes, never in a public issue.
