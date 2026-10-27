---
description: One-command DeepMap setup. It installs DeepMap's Python packages when this Python lacks them, ensures Neo4j is running, builds the code graph (sync + indexes + embeddings), starts the MCP search/embed servers, and verifies end-to-end.
argument-hint: "[target-dir]  (default: current repo)"
allowed-tools: Bash(NEO4J_PASSWORD=*), Bash(python ${CLAUDE_PLUGIN_ROOT}/build/provision.py:*), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/build/provision.py:*)
---

Run the DeepMap provisioner to stand the product up end-to-end for this project.

Execute (Bash tool; POSIX sh) **in the background**: call the Bash tool with `run_in_background: true`
and, where the tool takes one, its longest background `timeout` (`7200000` ms, two hours), then wait for
the command to exit (you are told when it does) and read its exit code and full output before you report
anything:

```bash
TARGET="$ARGUMENTS"; [ -n "$TARGET" ] || TARGET="."
NEO4J_PASSWORD=deepmap PYTHONUTF8=1 HF_HUB_OFFLINE=1 \
  python "${CLAUDE_PLUGIN_ROOT}/build/provision.py" setup "$TARGET"
```

Why the background: setup can take many minutes (a first run on a machine installs DeepMap's Python
packages and the embedding model, then downloads Neo4j; the graph build of a large repository alone can
take longer; the first search loads the embedding model), and a foreground Bash call is cut off at the
tool's own time limit (2 minutes by default, 10 at most), and setup then never reports its outcome. A first
run on a slow network can also outlast the Bash tool's default limit for a background command (30
minutes), hence the longer `timeout`. Where you cannot run it in the background, ask the user to run the
block in a terminal and share the output. If a run was cut off anyway (the Bash tool says it timed out or
stopped it), setup did not finish: re-run it in the background (it is idempotent and keeps what it already
installed); do not report success, or a DeepMap failure. If the tool moved it to the background instead
of stopping it, it is still running: wait for it rather than starting another.

Use `python3` where `python` is absent (stock Debian, Ubuntu and macOS ship only `python3`); the command is
otherwise the same.

**DeepMap's Python packages need no installer.** When the `python` here lacks them, or holds one outside
the version range DeepMap supports, setup never installs into it. It first checks Java (17 or 21) and free
disk, failing in seconds with a `PREFLIGHT FAILED` line, then creates DeepMap's own virtual environment at
`<DEEPMAP_HOME>/venv` (`~/.deepmap/venv`), installs the packages there (PyTorch's CPU build included),
downloads the embedding model, runs a self-check and continues under that environment. Its progress lines
are numbered (`[1/7] creating DeepMap's virtual environment ...`); that first run downloads about 1 GB, and
on a busy Windows test machine the environment took 8.5-11 minutes, the whole first setup 12-15. Later
runs reuse the environment and repair it when a package is missing or out of range. Every `/deep-map:*`
command then runs under it by itself. A Python that already has every package inside the ranges DeepMap pins
(the terminal installers put them there) is used as is. If it fails, relay its
`setup verdict: FAILED — …` line: it names the fix (on Debian / Ubuntu without the venv module,
`sudo apt install python3.X-venv`; for a network, proxy or index problem, check it and re-run
`/deep-map:setup`, which keeps what is already installed) and the log, `~/.deepmap/venv-setup.log`.

The Python that ends up running setup matters beyond this run: a setup that ends with its servers up records
that interpreter (DeepMap's own environment, when setup used it) in DeepMap's own state (under
`<DEEPMAP_HOME>/run/`, never in the project), and after a reboot the session-start heal revives this project
with it (a usable `DEEPMAP_PYTHON` takes precedence).

This single command (idempotent, so safe to re-run):

1. **Ensures this project's OWN Neo4j instance** is reachable (auth `neo4j/deepmap`): `bolt://127.0.0.1:7687`
   for the first project, the next free port pair (7689, 7691, …) for others, or when something that
   isn't DeepMap already holds 7687 (e.g. a Neo4j you run in Docker, which is left untouched). If absent it
   downloads Neo4j Community 5.26, sets `dbms.security.auth_minimum_password_length` so DeepMap's
   7-character default password is accepted, turns off Neo4j's usage telemetry, sets the initial
   password, and starts it.
   A store an earlier DeepMap built on an older 5.26 patch release (5.26.0) is moved to 5.26.32 here, once:
   the new release must answer with the same graph, otherwise the store is put back on 5.26.0 and setup does
   not try that release again for a week (set DEEPMAP_NEO4J_AUTO_UPGRADE=0 to skip it; `provision.py
   neo4j-upgrade` does it on request, at once).
   A project whose graph an older DeepMap build put on the shared `7687` store keeps that store when it
   is provably its own: setup reads whose graph the store holds, and if the store is down (e.g. after a
   reboot) with 7687/7474 free, it starts it just to read that and stops it again when the graph is not
   this project's. When the graph is another project's (or unowned but populated), or 7687 is held by
   another program, this project gets its own instance and its graph is rebuilt there; the old store is
   left untouched for its owner.
2. **Syncs** the target directory into the typed graph (`:Function/:Method/:Class/:Module/:Route`
   joined by `:CALLS`, `:IMPORTS`, and the typed framework/runtime edges), creates the 13 indexes
   (6 vector + 2 FTS + 5 node-key write-path), and embeds intent summaries.
3. **Starts the MCP servers** (`deepmap_search`, `deepmap_embed`) over **streamable-HTTP**,
   detached, on this project's port pair (`127.0.0.1:8787` / `:8788` for the first project), the
   Windows-safe transport (stdio hangs under Claude Code on Windows). A server of this project's that
   is already running (e.g. revived at session start) is restarted so it picks up the current config,
   including one an older DeepMap build started on this project's registered port (it has no owner
   record, so it may still be wired to a store setup just moved this project off).
4. **Verifies** the search path via the same MCP client protocol Claude Code uses.

After it succeeds, the job is **NOT done until the user starts a new session**: tell them, as an
explicit ACTION REQUIRED (not a passing note), to **start a new Claude Code session (approve the two
deepmap servers if Claude Code asks)**. `deepmap_search` / `deepmap_impact` / `deepmap_embed` will NOT
appear as tools until they do, even though setup succeeded: setup writes this project's own `.mcp.json`
(the plugin ships none), and Claude Code reads it and connects its two HTTP servers when a session
starts. The detached servers keep running across sessions. If the tools still don't appear in the new session
(Claude Code did not ask, or the user declined), tell them to run `claude mcp reset-project-choices` in the
project and start another session. Report the node/edge/embedding counts and
the two server URLs from the output. If the output prints a `setup verdict: DEGRADED` / `FAILED` line or LOW COVERAGE lines, relay
them honestly: do not summarize a DEGRADED setup as plain success. If the final banner carries a
WARNING that the end-to-end verify did not finish, the servers are usually still starting up (the
first search loads the embedding model, which can take a minute or two on a cold machine); say so
and suggest trying a search again shortly, rather than calling DeepMap broken. If it prints
`setup verdict: FAILED — this project's MCP port(s) … are still held by a DeepMap server setup could not
restart`, the graph was built but the old server on that port may answer from another project's graph:
relay that line and the ERROR above it (it names the process), have the user stop that process and
re-run `/deep-map:setup`, and do NOT tell them to reload or use deepmap_search/deepmap_impact until setup
succeeds. DeepMap remembers that FAILED verdict: until a later `/deep-map:setup` succeeds, each new
session is told at start-up that the tools must not be used for this project (the old server still
listens, so the start-up check alone cannot clear it). If a step fails or
setup refuses (e.g. the port registry is unreadable, or no free port pair could be allocated), relay
the one-line reason it prints (it names the fix) and surface the exact log path it prints
(`~/.deepmap/*.log`) rather than guessing.

**Exit 4 (`setup verdict: DEGRADED — the graph was NOT rebuilt`)**: setup finished and the tools are
up, but its sync pushed nothing, so they answer from the PREVIOUS graph build (it may be stale). Relay
that line and the sync's reason above it; do not describe the graph as freshly built. The usual cause
is graphify running out of time on a large repository or a busy machine ("graphify timed out after N s
…"). Then re-run just the graph build with a larger time budget, in seconds (about twice the one it
names, accepted 60–86400), by running this block, again in the background, with `2400` replaced by that
budget (`NEO4J_PASSWORD=deepmap` stays first so the command's allowed-tools rule still matches it):

```bash
TARGET="$ARGUMENTS"; [ -n "$TARGET" ] || TARGET="."
NEO4J_PASSWORD=deepmap DEEPMAP_GRAPHIFY_TIMEOUT=2400 PYTHONUTF8=1 HF_HUB_OFFLINE=1 \
  python "${CLAUDE_PLUGIN_ROOT}/build/deepmap_cli.py" sync "$TARGET" --stale-cleanup
```

It is `/deep-map:refresh`'s sync with the larger budget (its notes explain the outcome lines); the
servers setup started answer from the new graph as soon as it is pushed (they query it live), so it
needs no second setup and no server restart.
A larger budget only helps when the sync itself is not time-limited (in the background or a terminal):
a foreground Bash call is cut off after 10 minutes at most, before such a budget is used. When the
reason instead says another sync held the lock, run `/deep-map:refresh` once that sync finishes.
