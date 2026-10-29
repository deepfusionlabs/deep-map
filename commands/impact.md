---
description: Blast radius of a symbol (who calls it and what breaks if you change it). DeepMap's flagship, the multi-hop, confidence-tagged answer a string search can't reconstruct. AMBIGUOUS edges must be verified by reading the call site.
argument-hint: "\"<symbol>\"  (bare name like _weighted_rrf, Class.method, Class#method, .method / #method, or Owner::method)"
allowed-tools: Bash(NEO4J_PASSWORD=*), Bash(python ${CLAUDE_PLUGIN_ROOT}/build/deepmap_cli.py:*), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/build/deepmap_cli.py:*)
---

See the reverse-CALLS blast radius of a symbol before you refactor it: DeepMap's differentiating
capability (the multi-hop "what breaks if I change this" that grep fundamentally can't answer).

You can also just ask Claude Code *"what calls X, and what breaks if I change it?"* and this fires
automatically via the skill. Use the command when you want the report **on demand and
deterministically**: a named, discoverable form of the one thing DeepMap does that a string search
can't.

Execute (Bash tool; POSIX sh):

```bash
NEO4J_PASSWORD=deepmap PYTHONUTF8=1 python "${CLAUDE_PLUGIN_ROOT}/build/deepmap_cli.py" impact "$ARGUMENTS" --limit 30
```

Use `python3` where `python` is absent (stock Debian, Ubuntu and macOS ship only `python3`); the command is
otherwise the same.

The symbol may be a bare name (`save`), a method suffix (`.save`, `#save`), or owner-qualified
(`User.save`, `User#save`, `Owner::save`; generic owners like `Repo<T>::save` are tolerated).

Prints one line per dependent, `[confidence] <hop>h via <EDGE> caller -> callee file:line`, ordered
trust-first. That `file:line` is where the caller starts (its definition), not the call. When the graph
recorded the call site, the line ends `file:<call line> (call site; caller starts at line <line>)`
instead: the caller's first call to the symbol (only the first call per caller/callee pair is recorded,
so there may be more calls further down). A row at hop 2 or more prints
`(first call on the path; caller starts at line <line>)`: its call line is the caller's first call to
the next symbol on its path, not a call to the queried symbol itself. A graph synced before call lines were
recorded, and a row whose edge is not `CALLS`, print the caller's start line only: look for the call
inside that caller. `--limit` counts result rows (one per DISTINCT dependent–callee pair, so a caller
that reaches several same-named callees takes one row per callee) and defaults to 30 (clamped 1..100).
The confidence label is the audit wedge: `EXTRACTED` = read directly from the source (a literal
same-file relationship, e.g. a route handler or base class you can see);
`INFERRED` = the callee name matched exactly one symbol in the graph: likelier, but still resolved by
name (a same-named library call or a dynamic call can land on it); `AMBIGUOUS` = the name maps to >1
symbol, so the edge MAY be wrong: open the call site (the printed call line, or the call inside the
caller that starts at the printed `file:line`) and confirm the real binding before relying on it. A
trailing note counts the AMBIGUOUS edges.

**Relay the notes it prints: they change what the answer means.** The CLI says when the result was
cut at `--limit` (more dependents exist), when the walk fell back to one hop or ran at a reduced depth
(the answer is then NOT the full multi-hop blast radius), and whether an empty answer means the symbol
is NOT in the graph (run `/deep-map:refresh` if you expected it) or it IS in the graph with no resolved
dependents. Even the second does not prove that nothing depends on it: the extractor does not resolve
every call (method/receiver calls through variables, dynamic dispatch), so grep for the name before
calling it a leaf. For C# this is the rule, not the exception: by default a call from another file
through an object (a field, parameter or local), a type name, an interface or a mediator (MediatR
`Send`) is not linked, so a C# answer is a lower bound and its note says so: grep the name. If it
reports the walk as too expensive (a hub name such as `__init__` or `get`), even a one-hop walk ran
out of Neo4j's transaction memory, so a shallower walk will not help: re-query
with an owner-qualified `Class.method` name instead (the message suggests one). A larger heap also
raises that limit for this project's Neo4j (its own instance, or the base store on bolt 7687):
`DEEPMAP_NEO4J_HEAP_MAX` (default `512m`, e.g. `1g`),
set where every DeepMap process sees it (e.g. the `env` block of Claude Code's settings; each start
rewrites the instance's conf from it), then restart the instance with
`NEO4J_PASSWORD=deepmap PYTHONUTF8=1 python "${CLAUDE_PLUGIN_ROOT}/build/provision.py" stop` followed
by `/deep-map:setup`. It costs RAM, so offer it to the user rather than doing it unasked. It also
says when it clamped `--max-hops` or ignored an unknown `--edges` type. It exits 0 when it answered
(an empty answer included) and exits 3 when it could not (a database error or a too-expensive walk;
the one-line reason says which). If it refuses because this project has no DeepMap
Neo4j instance of its own (or the database is not reachable), recommend `/deep-map:setup`, and never read
another project's graph as this one's. Likewise when it refuses because this project is registered on the
base Neo4j (bolt 7687) but **that store belongs to project X** (a claim an older DeepMap build wrote by
mistake): recommend `/deep-map:setup`, which re-checks the claim and gives this project its own instance.
Neo4j must be up.

**Rails / ORM associations.** By default the walk follows `:CALLS` plus the runtime-wiring edges
(routes, DI, jobs, inheritance, cascades, view renders), but **not** model associations
(`has_many`/`belongs_to` → `HAS_MANY`/`REFERENCES`), which are held out pending the benchmark gate.
On an ORM app "what breaks if I change this model" *is* the association graph, so opt them in,
preferably by exporting `DEEPMAP_IMPACT_PROFILE=rails`, which ADDS the two association edges to the
default walk (also honored by the `deepmap_impact` MCP tool). `--edges` (like the MCP tool's
`edge_types` argument and `DEEPMAP_IMPACT_EDGES=<CSV>`) REPLACES the default set instead, so list the
defaults too or the walk loses routes, cascades and renders:
`--edges CALLS,ROUTES_TO,DEPENDS_ON,TRIGGERS,SCHEDULED_AS,HOOKS,INHERITS,CASCADES,RENDERS,HAS_MANY,REFERENCES`.
