---
description: DeepMap health check of Neo4j reachability, graph/index/embedding state, MCP server liveness, and the ML stack, with OK/WARN/FAIL per check and next-step hints.
allowed-tools: Bash(NEO4J_PASSWORD=*), Bash(python ${CLAUDE_PLUGIN_ROOT}/build/deepmap_cli.py:*), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/build/deepmap_cli.py:*)
---

Diagnose why DeepMap isn't working (or confirm it is).

Execute (Bash tool; POSIX sh):

```bash
NEO4J_PASSWORD=deepmap PYTHONUTF8=1 python "${CLAUDE_PLUGIN_ROOT}/build/deepmap_cli.py" doctor
```

Use `python3` where `python` is absent (stock Debian, Ubuntu and macOS ship only `python3`); the command is
otherwise the same.

It probes, in isolation (never crashing): Neo4j reachability (THIS project's own instance; it refuses
to report another project's graph as this one's); the graph census (nodes / edges / embedded); which
project **owns** the graph (each project has its OWN Neo4j instance by default, so no
switching is needed: the owner check + `sync --switch-project` apply only on the explicit `NEO4J_URI`
escape hatch where several projects share one instance); how many `deepmap_*` indexes are ONLINE;
whether THIS project's `deepmap_search` / `deepmap_embed` MCP servers are UP (its own port pair); the
extractor the last sync actually used (a WARN if it fell back automatically, with the reason); the
graph's freshness (built at which commit, and whether that build included uncommitted changes, in which case
edits made after it may be missing); whether the ML stack (torch + sentence-transformers) is
importable; and an informational **auto-sync** line (DeepMap's background refresh: off and why,
running, or how its last run ended; its text says what to do; a `WARN` there never changes the
verdict or the exit code). Each line is `OK` / `WARN` / `FAIL`. After the instance table it may print
one `Neo4j <version>: patch release <newer> is available …` line: informational (a store built on an
older 5.26 patch release); it says whether `/deep-map:setup` will upgrade the store (checked against
the current graph, rolled back on failure) and gives the `provision.py neo4j-upgrade` command.

Summarize the result and act on the hints: a `FAIL` on Neo4j, or "no DeepMap instance of its own" →
recommend `/deep-map:setup`; a down MCP server → re-run `/deep-map:setup` (it restarts them); a stale
graph → `/deep-map:refresh`; `0 embedded` or a missing ML stack → search is running FTS-only, so
recommend `/deep-map:setup` (it installs or repairs DeepMap's own Python environment) / `/deep-map:refresh`;
a `WARN` auto-sync line → relay its reason and the command it suggests (usually `/deep-map:refresh`).
Exit code is 0 only when the core (Neo4j + a populated graph) is healthy.

The exception: a Neo4j `FAIL` worded **driver error talking to …** is a client-side failure: an operator
`NEO4J_URI` the driver can't use (the Neo4j Browser's `http://` address, a user name inside the URI), or a
`neo4j` package that is missing or outside the range DeepMap supports. Nothing showed the instance to be
down, so do not treat it as a stopped one (starting or healing it will not help): relay the line and act
on its own remedy: fix `NEO4J_URI` (or unset it; `/deep-map:setup` then gives this project its own
instance). For a `neo4j` package the Python running DeepMap lacks, or holds outside that range, the line
names `/deep-map:setup`: it installs DeepMap's packages into DeepMap's own environment (`~/.deepmap/venv`),
never into the user's Python. It prints a pinned `-m pip install --upgrade "neo4j…"` command for that Python
instead only when the package is installed but will not import, or when `DEEPMAP_MANAGED_VENV=0` says the
operator manages that Python. A `NEO4J_URI` host that doesn't resolve is reported as **unreachable (ServiceUnavailable)**
instead, with the driver's `Failed to DNS resolve address …` text and a remedy to check that host name: it
is not a stopped instance either: fix the host in `NEO4J_URI`, or unset it as above.

A `WARN` MCP line saying **no MCP port pair is registered for this project** → `/deep-map:setup` (the
default ports it would fall back to belong to another project, or to nothing). A `WARN` owner line
saying **NOT this project** means this Neo4j holds another project's graph (on a shared operator
`NEO4J_URI`, or through a base-store claim an older DeepMap build wrote by mistake): search/impact here
would answer about that project, so say so and do not present its results as this project's. A repository
nested inside another project, or the non-git folder around a repository, is a project of its own here:
its graph is never read as this one's. A line saying this project is registered on the base Neo4j (bolt
7687) but **that store belongs to project X** is such an old claim: recommend `/deep-map:setup`, which
re-checks the claim and gives this project its own instance while leaving X's graph alone. With that
refusal, a `WARN` MCP line saying the server is **UP — but it was started on this project's base-store
claim refused above** means that server answers deepmap_search/deepmap_impact from that store, i.e.
another project's graph: do not use or present those results as this project's, and recommend
`/deep-map:setup` (it gives this project its own instance and restarts its servers there).
Replacing that graph
(`NEO4J_PASSWORD=deepmap PYTHONUTF8=1 python "${CLAUDE_PLUGIN_ROOT}/build/deepmap_cli.py" sync . --switch-project`)
deletes the other project's graph: run it only on the user's explicit request. It is a full graph build, so
run it with the Bash tool's `run_in_background: true` (a foreground call is cut off after 10 minutes at
most), then wait for it to exit and read its exit code and output before reporting, as `/deep-map:refresh`
does.

After upgrading from an older DeepMap, a project whose path has non-ASCII characters may print one
line `registry key migrated <old> -> <new> … one-time upgrade from a pre-UTF-8 build`: expected,
nothing to do. A `… DEFERRED (its Neo4j instance is running)` line means the same move finishes by
itself after that instance next stops (hibernation, a reboot); everything keeps working meanwhile.
