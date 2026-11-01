---
description: Refresh the DeepMap code graph for this project (re-extract, re-index, re-embed changed symbols, and prune deleted ones) so answers track your latest code.
argument-hint: "[target-dir]  (default: current repo)"
allowed-tools: Bash(NEO4J_PASSWORD=*), Bash(python ${CLAUDE_PLUGIN_ROOT}/build/deepmap_cli.py:*), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/build/deepmap_cli.py:*)
---

Bring the DeepMap graph up to date with the current code (usually after edits). DeepMap also refreshes the graph
by itself, in the background, when an interactive Claude Code session starts (offline, never to a database on
another machine on its own, at low CPU priority; it deletes a node only when it re-read the node's file or the
file is gone, so a few stale nodes can remain until this command runs). Run this command when search/impact
must reflect edits made in THIS session, or when a DeepMap note says the background refresh did not update the
graph. Turn the background refresh off with `DEEPMAP_AUTO_SYNC=0` or `.deepmap/instance.json`
`{"auto_sync": {"enabled": false}}`. `/deep-map:setup`
does not install a git hook: a commit re-syncs automatically only where a DeepMap post-commit hook
re-syncs THIS project; otherwise run this after changes. That hook is opt-in: the provisioner's
`git-hook --install` verb adds it (setup prints the exact command to run from the project), and its
`git-hook --status` says whether this project's hook re-syncs it. The post-edit reminder only says "a
commit also re-syncs it" when the hook provably syncs this project's own root.

Execute (Bash tool; POSIX sh) **in the background**: call the Bash tool with `run_in_background: true`,
then wait for the command to exit (you are told when it does) and read its exit code and output before
you report anything:

```bash
TARGET="$ARGUMENTS"; [ -n "$TARGET" ] || TARGET="."
NEO4J_PASSWORD=deepmap PYTHONUTF8=1 HF_HUB_OFFLINE=1 \
  python "${CLAUDE_PLUGIN_ROOT}/build/deepmap_cli.py" sync "$TARGET" --stale-cleanup
```

Why the background: a refresh of a large repository can take many minutes (graphify's own budget
starts at 600 s), and a foreground Bash call is cut off at the tool's own time limit (2 minutes by
default, 10 at most), and the sync then never reports its outcome. Where you cannot run it in the
background, ask the user to run the block in a terminal and share the output. If a foreground run was
cut off anyway (the Bash tool says it timed out), the refresh did not finish: re-run it in the
background; do not report it as done, or as a DeepMap failure. If the tool moved it to the background
instead of stopping it, it is still running: wait for it rather than starting another.

Use `python3` where `python` is absent (stock Debian, Ubuntu and macOS ship only `python3`); the command is
otherwise the same.

This re-extracts the typed graph for the project, re-creates indexes if missing, (re-)embeds symbols
whose content changed (content-hash gated), and DETACH-DELETEs nodes whose source files are gone.
Neo4j must already be up: run `/deep-map:setup` first if it isn't.

How to read the outcome:
- **A target inside an already-synced project** (a subdirectory of the repo root that was synced)
  syncs that whole project root instead. It says so; that is expected (syncing only the subdirectory
  would duplicate every node under it).
- **"queued a re-run"** means another sync of this project (or a `deepmap enrich` run, which holds the
  project's lock while it extracts) was already running (the message names both; the lock does not say
  which). A running sync syncs again before it finishes, so your latest changes are included. enrich
  never syncs: if an enrich run holds the lock, run this refresh again once it finishes. Not an error.
- **"DeepMap's background refresh of this project is running … asking it to stop"**: this refresh waits (at
  most 15 minutes) for DeepMap's own background refresh to stop at a safe point, then runs itself with your
  settings and the full prune. Wait for it; do not start another. If it ends NOT PUSHED because the background
  refresh did not stop in time, run it again.
- **A non-zero exit** is a failed refresh: the sync verdict was FAILED (e.g. 0 nodes extracted) or the
  sync could not run (exit 1), or Neo4j is unreachable (exit 3). Relay the verdict/reason lines it
  printed; do not report success.
- **Exit 4 ("NOT PUSHED")** means the sync ran but pushed nothing: the graph is still the PREVIOUS
  build, unchanged (search and impact keep answering from it, but it may be stale). Do not report the
  refresh as done; relay the reason line. The usual cause is graphify running out of time on a large
  repository or a busy machine ("graphify timed out after N s …"): the sync then fell back to the
  Python-only extractor and the engine-downgrade guard refused to push its nodes over the graphify
  graph. Re-run it with a larger time budget, in seconds, about twice the one it names (accepted
  60–86400; the default grows with the number of source files, from 600 s): run this block, again in
  the background, with `2400` replaced by that budget. It is the first command block above with
  `DEEPMAP_GRAPHIFY_TIMEOUT=<seconds>` added right after `NEO4J_PASSWORD=deepmap`, which stays first so
  the command's allowed-tools rule still matches it:

  ```bash
  TARGET="$ARGUMENTS"; [ -n "$TARGET" ] || TARGET="."
  NEO4J_PASSWORD=deepmap DEEPMAP_GRAPHIFY_TIMEOUT=2400 PYTHONUTF8=1 HF_HUB_OFFLINE=1 \
    python "${CLAUDE_PLUGIN_ROOT}/build/deepmap_cli.py" sync "$TARGET" --stale-cleanup
  ```

  A larger budget only helps when the sync itself is not time-limited (in the background or a
  terminal): a foreground Bash call is cut off after 10 minutes at most, before such a budget is used.
  Exit 4 also follows a run skipped because another sync or a `deepmap enrich` run held the lock without
  queuing this one; re-run the first command block once that run finishes.
- **A refused prune** (its wipe guard: an unusually large deletion, or an extractor downgrade): re-run
  the first command block above, in the background, with `--force-prune` added after `--stale-cleanup`,
  but only when those deletions are expected (e.g. a big refactor).
- **"stale-cleanup SKIPPED: ... no repo-root marker"**, or a `DEGRADED` verdict with "STALE NODES KEPT"
  naming `no-root-marker`: a third kind of refused prune, and `--force-prune` cannot override it. Without
  a root marker (`.git`, `pyproject.toml`, `package.json`, ...) DeepMap cannot tell this folder is the
  whole project. Run the refresh from the repository root; if this folder is the whole project, add a
  `.deepmap-root` file to it and run the first command block above again.
- **"no DeepMap Neo4j instance of its own" / an unreadable port registry**: the refresh refuses rather
  than write into another project's graph; run `/deep-map:setup` in this project.
- **"REFUSING: this Neo4j holds project X"** (with no `NEO4J_URI` set): the Neo4j this project is
  registered on holds project X's graph: a claim left behind by an older DeepMap build, or X is a
  separate project nested in or around this one (a repository inside another one, or the non-git
  folder around a repository, is a project of its own). Run `/deep-map:setup` in this project: it
  re-checks that claim and gives this project its own instance, leaving X's graph alone. Do NOT add
  the `--switch-project` the message mentions: it deletes X's graph and rebuilds this project in its
  place, so use it only on the user's explicit request to replace X.

**Projects run side by side: each has its OWN Neo4j instance.** DeepMap gives every project its own
Neo4j instance (own data dir + bolt port, one shared binary), so refreshing one project never touches
another's graph: no switching, no cross-project mixing. `--stale-cleanup` above only prunes
deleted-file nodes for *this* project. (If you set `NEO4J_URI` to point every project at one shared
instance instead, that single instance still holds one project at a time and sync refuses a foreign
owner unless you pass `--switch-project` (the escape-hatch behaviour). A graph synced from another
operating system records that system's path, so the same repository used through WSL (`/mnt/c/…`) and
from Windows (`C:\…`) counts as a different project on such a shared instance: give each its own
instance, or switch deliberately.)

Report the resulting node/edge/embedding counts. The running MCP servers pick up the refreshed
graph automatically (they query Neo4j live); no restart needed.
