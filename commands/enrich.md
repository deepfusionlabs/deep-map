---
description: (Opt-in, off by default) Use your existing Claude session to write real intent summaries for UNDOCUMENTED code, improving concept→implementation search. Local sidecar, deletable; uses your Claude plan (no new service, no API key).
argument-hint: "[target-dir]  (default: current repo)"
allowed-tools: Bash(DEEPMAP_ENRICH=*), Bash(python ${CLAUDE_PLUGIN_ROOT}/build/deepmap_cli.py:*), Bash(PYTHONUTF8=1 python ${CLAUDE_PLUGIN_ROOT}/build/deepmap_cli.py:*), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/build/deepmap_cli.py:*), Bash(PYTHONUTF8=1 python3 ${CLAUDE_PLUGIN_ROOT}/build/deepmap_cli.py:*)
---

Enrich DeepMap's intent summaries for **undocumented** code in this project. This is **opt-in and off by
default**. It uses your **existing Claude Code plan** (`claude -p`: no new service, no API key, nothing
sent anywhere you aren't already sending code via Claude Code) to write a one-sentence behavioral summary
for each undocumented function/method/class, cached in a gitignored, deletable sidecar
(`.deepmap/summaries.jsonl`) that the next sync embeds. It **consumes some plan quota**, so confirm first.

Run every command below **in the background**: call the Bash tool with `run_in_background: true`, then
wait for the command to exit (you are told when it does) and read its exit code and output before you
report or move on. Each one first extracts the whole project (and the LLM pass then calls `claude -p`
many times), so on a large repository it can take many minutes, and a foreground Bash call is cut off
at the tool's own time limit (2 minutes by default, 10 at most), and it then never reports its outcome.
Where you cannot run it in the background, ask the user to run the block in a terminal and share the
output. If a foreground run was cut off anyway (the Bash tool says it timed out), it did not finish:
re-run it in the background (the LLM pass is resumable: it keeps what it already wrote). If the tool
moved it to the background instead of stopping it, it is still running: wait for it rather than
starting another.

Use `python3` where `python` is absent (stock Debian, Ubuntu and macOS ship only `python3`); the
commands are otherwise the same. Each block is complete on its own: every Bash call starts a fresh
shell, so run a whole block, never a single line of it.

Do this in order:

1. **Preview (no spend).** Show the user what would be enriched:

   ```bash
   TARGET="$ARGUMENTS"; [ -n "$TARGET" ] || TARGET="."
   PYTHONUTF8=1 python "${CLAUDE_PLUGIN_ROOT}/build/deepmap_cli.py" enrich "$TARGET" --dry-run
   ```

   Report the candidate count (undocumented nodes) and roughly how many `claude -p` calls that implies
   (~1 per ~20 nodes). Mention the free alternative: `--deterministic` (richer LOCAL summaries, no LLM, no
   spend, no egress).

2. **Confirm before spending.** Ask the user whether to proceed with the LLM enrichment (it uses their
   plan quota) or run the free `--deterministic` pass instead. Do NOT spend without their go-ahead.

3. **Run the chosen pass** (in the background, as above).
   - LLM tier (spends plan quota; optionally add `--model <id>`, `--max-nodes N`, `--max-total-usd F`; it
     is resumable: re-run to continue a large repo):

     ```bash
     TARGET="$ARGUMENTS"; [ -n "$TARGET" ] || TARGET="."
     DEEPMAP_ENRICH=1 PYTHONUTF8=1 python "${CLAUDE_PLUGIN_ROOT}/build/deepmap_cli.py" enrich "$TARGET" --confirm-spend
     ```

   - Free tier:

     ```bash
     TARGET="$ARGUMENTS"; [ -n "$TARGET" ] || TARGET="."
     PYTHONUTF8=1 python "${CLAUDE_PLUGIN_ROOT}/build/deepmap_cli.py" enrich "$TARGET" --deterministic
     ```

4. **Embed the new summaries.** Enrichment only writes the sidecar: run `/deep-map:refresh` (a sync)
   afterwards so the updated `body_summary` values are re-embedded and searchable.

**Read each block's exit code** (the preview, the LLM tier and the free tier alike):
- `0` means it ran: report the counts it printed.
- `2` means it refused before any work: the LLM tier without its opt-in (`DEEPMAP_ENRICH=1`) or
  `--confirm-spend`, or a bad argument (e.g. a target directory that does not exist). Relay its one-line
  reason.
- `4` means enrich REFUSED before extracting: it did NOTHING (nothing was extracted, sent, spent or written,
  and no counts are printed), and its stderr line names the cause:
  - `[deepmap enrich] REFUSED — a sync (or another enrich) of this project is running …`: tell the
    user, wait for that run to finish, then run the same block again;
  - `[deepmap enrich] REFUSED — cannot open this project's sync lock (…)` (e.g. `.deepmap` is a file, or
    permission is denied), or `[deepmap enrich] REFUSED — <target> is not a directory …` (it vanished
    after the CLI checked it): waiting will not help; relay the line, fix what it names, then run the
    block again.

If enrich says a sync queued a re-run while enrich held the project's lock, that sync's changes are not
in the graph yet: enrich never syncs, so run `/deep-map:refresh` (step 4) to include them.

Report enriched / already-done / remaining counts and the approximate spend. Enriched summaries are a
**hint** at lower confidence than a real docstring; the source at `file:line` is always ground truth.
The sidecar is fully deletable (`rm .deepmap/summaries.jsonl`); deleting it reverts to the deterministic
summaries on the next sync.
