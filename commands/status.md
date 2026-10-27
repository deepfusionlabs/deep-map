---
description: Show DeepMap status (graph node/edge/embedding census, index health, and MCP server liveness).
allowed-tools: Bash(NEO4J_PASSWORD=*), Bash(python ${CLAUDE_PLUGIN_ROOT}/build/provision.py:*), Bash(python ${CLAUDE_PLUGIN_ROOT}/build/deepmap_cli.py:*), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/build/provision.py:*), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/build/deepmap_cli.py:*)
---

Report the current DeepMap state for this project.

Execute all three (Bash tool; POSIX sh):

```bash
NEO4J_PASSWORD=deepmap PYTHONUTF8=1 python "${CLAUDE_PLUGIN_ROOT}/build/provision.py" status
NEO4J_PASSWORD=deepmap PYTHONUTF8=1 python "${CLAUDE_PLUGIN_ROOT}/build/provision.py" mcp-status
NEO4J_PASSWORD=deepmap PYTHONUTF8=1 python "${CLAUDE_PLUGIN_ROOT}/build/deepmap_cli.py" status
```

Use `python3` where `python` is absent (stock Debian, Ubuntu and macOS ship only `python3`); the commands
are otherwise the same.

The first prints the graph census (nodes, edges, embedded count) and the 13 indexes with their
state (all should be `ONLINE`), then the per-project **isolation line** and an **instance table**
(each project's bolt URI · running/hibernated/absent · data-dir size · last-used · `neo4j=<version>`).
The second prints the two MCP server URLs and whether each is `UP`. The third is the graph-side view
of the same instance: which project **owns** the graph, the per-language **coverage** table from the
last sync (`N files, M nodes`; `M ≈ 0` against a large `N` means that language/layer is not indexed),
the graph's **freshness** (the commit it was built at vs HEAD, and whether that build included
uncommitted changes), the opt-in learning log's accrual line, and the **auto-sync** line: whether
DeepMap's background refresh is on (if not, why), whether it is running, or how its last run ended, with
`auto-sync: [WARN] …` when that run failed or was not pushed, when it is paused, when the next session
start will not refresh, or when an off-switch file is not understood. The learning and auto-sync lines
print even when the database is down; the census then prints a short reason instead and exits non-zero
(3), like the first.

When this project's store runs an older Neo4j patch release than new stores get (a store an earlier
DeepMap built on 5.26.0), the first and third commands print one line
`Neo4j <version>: patch release <newer> is available (security fixes, same store format) — …`. It is
informational, not a fault: it says whether `/deep-map:setup` will upgrade the store (checked against
the current graph, rolled back on failure) or why not (`DEEPMAP_NEO4J_AUTO_UPGRADE=0`, or an attempt
rolled back recently), and gives the `provision.py neo4j-upgrade` command that does it at once.

When this project's Neo4j is down, hibernated, rejects the password, or does not exist, `status`
prints a one-line reason instead of the census and exits non-zero (3); the isolation line and the
instance table still follow. A project with no Neo4j allocation of its own shows as `(no instance)` /
unallocated: recommend `/deep-map:setup` (it never reports another project's graph as this one's).

Summarize for the user: is the graph populated and embedded, are all indexes ONLINE, are both
MCP servers UP, does the coverage table show every language the project uses, and is the graph fresh?
A stale graph (HEAD moved since the build) → recommend `/deep-map:refresh`, unless the auto-sync line
says a background refresh is running (then answers still come from the previous build until it ends);
an `auto-sync: [WARN]` line → relay its reason and the command it suggests; a language with files but
~0 nodes → say that DeepMap does not cover it (search/impact answers for it come back empty because it
is not indexed, not because the code is absent). If the graph is empty or there is no instance, recommend `/deep-map:setup`. If a
server is `down`, recommend re-running `/deep-map:setup` (it restarts the MCP servers): users
shouldn't have to manage servers directly. If the database is down or hibernated, `/deep-map:setup`
(or simply a new session: the SessionStart heal revives it) brings it back.
If several project instances are shown and RAM is tight, mention hibernation (spins OTHER idle
instances down; each project's next session start revives its own) with
`NEO4J_PASSWORD=deepmap PYTHONUTF8=1 python "${CLAUDE_PLUGIN_ROOT}/build/provision.py" hibernate`,
and removing ANOTHER project's per-project instance for good (its data is deleted; that project's
next `/deep-map:setup` rebuilds it) with
`NEO4J_PASSWORD=deepmap PYTHONUTF8=1 python "${CLAUDE_PLUGIN_ROOT}/build/provision.py" instance-rm <that-project-dir>`.
It refuses the shared base/legacy store (bolt 7687) and, without `--yes`, the current project, so
offer it only for a project whose row shows its own port (not 7687).
There is no installed `deepmap` executable; always give the full command.

In the `mcp-status` output, a port marked `(held by project X's server, NOT this project's)` is
another project's server: this project's own server is not running there, so recommend
`/deep-map:setup` and never present that server's answers as this project's. Likewise a census line
`owner=X  (NOT this project — …)` means this Neo4j holds project X's graph (on a shared operator
`NEO4J_URI`, or through a base-store claim an older DeepMap build wrote by mistake): a repository nested
inside another project, or the non-git folder around a repository, is a project of its own, so say so and
never present that graph's answers as this project's. A line saying this project is registered on the base
Neo4j (bolt 7687) but **that store belongs to project X** is such an old claim: recommend
`/deep-map:setup`, which re-checks the claim and gives this project its own instance while leaving X's
graph alone.

After upgrading from an older DeepMap, a project whose path has non-ASCII characters may print one
line `registry key migrated <old> -> <new> … one-time upgrade from a pre-UTF-8 build`: expected,
nothing to do. A `… DEFERRED (its Neo4j instance is running)` line means the same move finishes by
itself after that instance next stops (hibernation, a reboot); everything keeps working meanwhile.
