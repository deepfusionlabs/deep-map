# Scenario A: the head-to-head number (turnkey runbook)

Scenario A is **config-complete**: 33 adversarially verified questions across 5 Python repos at real
pinned SHAs, the "G3 set": 5 of the 9 repos in `repos.yaml` (the other 4, Go/Rust/TypeScript, are the
multi-language breadth wing that `benchmark/run_breadth.sh` runs; `questions.yaml` holds their 16
questions too). This runbook is the exact end-to-end sequence that produces the **head-to-head number**
(Opus 4.8 arm, with vs without DeepMap, blind judge).

> **Honesty gates (do not skip):** a run stays *captured, unscored* until the **blind** Stage-2 score
> runs; only then does it become `measured`. The `--probe` MUST pass (exit 0) before the matrix. No result
> is published yet: a published number comes only from a run on the current release, published with its
> raw outputs. Earlier internal runs measured older graphs and are not published.

## One-command n=4 run (the resumable driver)

For the full pass (n=4 over the G3 set, about 15 hours) use the driver `benchmark/run_g3_n4.sh`
instead of running §3–§4 by hand. The G3 set is the five Python repos the driver names as `G3_REPOS`
(`REPOS=` overrides it, and the driver then says the run is not the G3 set). It wraps the exact sequence
below at the methodology default n=4 (it **omits** `--runs-per-arm`), and is **resumable**: each repo's
run-id is `g3n4-<RUN_TAG>-<owner__name>` (`RUN_TAG` defaults to `v<DeepMap version>`, so a new version
starts a fresh run), and a re-launch reuses only captures of THIS DeepMap's graph
(`run_benchmark.resume_state_for`). A repo measured on it is skipped; one captured but not yet scored
re-runs only the blind judge; an unfinished capture (or one whose `results.json` is unreadable) is moved
aside to `<run-id>.unfinished-<utc>`, its answer paths re-pointed there, and captured again. A capture
that exits non-zero stops the driver after its clone's servers + Neo4j are stopped (nothing scored or
merged). A capture of any other graph under that run-id (another DeepMap version, a clone graph rebuilt
since from another SHA or engine, or no recorded provenance: a capture by an older harness) **stops the
driver** instead of being reused (run under a new `RUN_TAG=<tag>`, or move that dir aside). Older untagged
captures (`g3n4-<owner__name>`) never share a run-id with a tagged run, so they are never reused.

```bash
export NEO4J_PASSWORD=deepmap
# one-time: the prerequisites in §0; clone the 5 G3 repos (§1) or pass DO_CLONE=1
bash benchmark/run_g3_n4.sh            # foreground …
# or detached for the long run:
nohup bash benchmark/run_g3_n4.sh >/dev/null 2>&1 &
tail -f benchmark/results/g3n4-driver.log
```

For each repo it builds that clone as its **own DeepMap project** (`benchmark/clone_graph.py build`: its own
Neo4j instance, graph and MCP servers, see §3), runs the `--probe` against the first clone's servers
(fail-closed), captures n=4 with the ON arm pointed at that clone's own servers, stops them, blind-scores
(`--score <run_id> --clone-root "$CLONES"`, §4), then `--merge`s everything into
`benchmark/results/g3n4-<RUN_TAG>-combined/results.md`. It only calls the existing entrypoints
(`build/retrieval.py` is untouched), and it **does not publish** anything.
Useful env: `CLONES=<root>`, `DO_CLONE=1` (clone missing repos at their pinned SHA), `SKIP_PROBE=1`,
`KEEP_RUNNING=1` (leave each clone's servers + Neo4j up), `RUN_TAG=<tag>`, `REPOS="a/b c/d"`. The manual
§0–§6 below remain the reference for a single-repo or debugging run.

## 0. Prerequisites (one-time)

```bash
# Java 17/21 + the pinned Python deps (requirements.txt / install.ps1 / install.sh); claude on PATH.
# No shared Neo4j to set up: each clone gets its OWN instance when §3 builds it (the first build fetches
# the Neo4j 5.26 binary once and applies the same setup fixes a user's /deep-map:setup does).
export NEO4J_PASSWORD=deepmap

# The blind judge runs via `claude -p` under your own Claude login by default (DEEPMAP_JUDGE_TRANSPORT=cli):
# no key needed. (The judge prompt is piped via stdin.)
#
# OPTIONAL: for the strongest blind credibility use a NON-Claude (independent) judge instead, so a Claude
# judge isn't grading a Claude arm. Put an OpenRouter key in benchmark/.env.local (gitignored):
#   printf 'OPENROUTER_API_KEY=sk-or-...\nDEEPMAP_JUDGE_MODEL=deepseek/deepseek-r1\n' > benchmark/.env.local
```

## 1. Clone the 5 G3 repos at their pinned SHAs (measurement only, never vendored)

```bash
export CLONES=/tmp/deepmap-bench-clones   # OUTSIDE this repo (methodology [CLONE]); the snippet reads it
mkdir -p "$CLONES"
python - <<'PY'
import yaml, subprocess, os
root=os.environ["CLONES"]
G3={"psf/requests","pallets/flask","pallets/click","pallets/werkzeug","fastapi/fastapi"}   # run_g3_n4.sh G3_REPOS
for r in yaml.safe_load(open("benchmark/repos.yaml", encoding="utf-8"))["repos"]:
    if r["name"] not in G3:
        continue                                  # the breadth wing (Go/Rust/TypeScript): run_breadth.sh
    d=os.path.join(root, r["name"].replace("/","__"))
    subprocess.run(["git","clone",r["url"],d], check=True)
    subprocess.run(["git","-C",d,"checkout",r["pinned_sha"]], check=True)   # exact pin
    print("pinned", r["name"], "->", r["pinned_sha"][:12])
PY
```

## 2. Build the first clone + confirm its servers load (the ON-arm wiring)

Every clone is its **own DeepMap project**: `benchmark/clone_graph.py build <clone>` runs
`build/provision.py setup <clone>`: its own Neo4j instance, its own graph (the product's default sync at
the pinned SHA, which writes the `.deepmap/graph_build.json` SHA manifest) and its own MCP (search, embed)
servers, exactly what a user gets. Under DeepMap's single Neo4j routing rule a clone that is NOT its own
project has no instance to sync into while another project owns bolt 7687, so an older shared-graph step
(`deepmap_cli.py sync <clone>` into the dev repo's graph) is refused; it also left the ON arm on servers
that describe themselves as the dev repo's. A clone never takes the base Neo4j pair (bolt 7687): `build`
claims a free non-base pair for it before setup runs, so on a box where DeepMap was never set up the first
clone still gets an instance that `rm` can remove (`instance-rm` never removes the base store).

```bash
D="$CLONES/psf__requests"
python benchmark/clone_graph.py build "$D"                          # own instance + graph + MCP servers
read -r SP EP <<< "$(python benchmark/clone_graph.py ports "$D" | tr -d '\r')"   # ITS MCP pair
# probe against THAT clone's servers (cheap, ~2 calls); the ports go on the command, never `export`:
DEEPMAP_SEARCH_PORT=$SP DEEPMAP_EMBED_PORT=$EP DEEPMAP_REAL_ENGINE=1 \
  python benchmark/run_benchmark.py --probe --clone-root "$D"       # MUST exit 0 (ON loads, OFF isolated)
```

> Why per command: `provision.py setup` honours an exported `DEEPMAP_SEARCH_PORT`/`DEEPMAP_EMBED_PORT` as a
> port override, so an export left over from one clone would wire the NEXT clone's servers onto it.

## 3. Per-repo: build that repo's graph + servers, then run ONLY its questions (SPENDS: the Opus arm)

Build each clone right before its questions, point the ON arm at ITS servers, then stop them (RAM). The
runner fails closed before spending when the graph isn't at the pinned SHA (`assert_graph_at_pinned_sha`)
or when the ON arm's MCP pair isn't the clone's own (`assert_on_arm_serves_repo`;
`DEEPMAP_BENCH_VERIFY_ON_ARM=1` also refuses a clone with no servers of its own). Use `--repo` to scope.

```bash
set -a; . benchmark/.env.local; set +a            # OPENROUTER_API_KEY + DEEPMAP_JUDGE_MODEL (optional judge)
python - <<'PY'
import yaml; print("\n".join(r["name"] for r in yaml.safe_load(open("benchmark/repos.yaml", encoding="utf-8"))["repos"]))
PY
# ^ every repos.yaml entry (9); Scenario A runs only the 5 G3 repos below (the rest: run_breadth.sh). For EACH one:
for NAME in psf/requests pallets/flask pallets/click pallets/werkzeug fastapi/fastapi; do
  D="$CLONES/${NAME/\//__}"
  python benchmark/clone_graph.py build "$D" || break             # ITS instance + graph + servers
  read -r SP EP <<< "$(python benchmark/clone_graph.py ports "$D" | tr -d '\r')"
  DEEPMAP_SEARCH_PORT=$SP DEEPMAP_EMBED_PORT=$EP DEEPMAP_BENCH_VERIFY_ON_ARM=1 DEEPMAP_REAL_ENGINE=1 \
    python benchmark/run_benchmark.py \
    --repo "$NAME" --clone-root "$CLONES" --max-budget-usd 1.0     # run only this repo's questions (6-8)
  python benchmark/clone_graph.py stop "$D"                        # free the RAM; the graph stays on disk
done
# Each -> benchmark/results/<run_id>/  (status: captured, UNSCORED; NO headline yet)
```

When the benchmark is done, remove the per-clone instances: `python benchmark/clone_graph.py rm "$D"` for
each clone (stops its servers + Neo4j and runs `provision.py instance-rm --purge` for it). A clone built by
an older `clone_graph.py` that holds the base pair is released instead (servers + Neo4j stopped, registry
entry and extraction output dirs removed); `rm` then prints the base store's path, which it leaves in place.
`rm` (and `stop`) work whether the clone is still running or was already stopped by the loop above:
"nothing to stop" is success. They judge the live state, not `provision.py stop`'s exit code (1 whenever
it signalled nothing): a clone's claim is kept only while something of it still runs (its Neo4j, or one of
its MCP ports still listening), and the message names what and how to find it. A process that was just
stopped can hold its port for a moment, so while something still looks up they re-check for up to 10 s
before saying so (`DEEPMAP_BENCH_STOP_GRACE_S=<seconds>` changes that; `0` judges once).

## 4. Blind Stage-2 score → `measured` (SPENDS: the judge)

```bash
# Default: the cli judge via `claude -p` under your own Claude login (no key).
# --clone-root: it reads each card's pinned clone (<clone-root>/<owner__name>) to check claims in the code,
# and refuses a clone that is not a pristine checkout at the pin (the capture leaves each clone pristine).
DEEPMAP_REAL_ENGINE=1 python benchmark/run_benchmark.py --score <run_id> --clone-root "$CLONES"   # per run_id

# OR, for an independent non-Claude judge (optional, stronger credibility). It is sent only the prompt and
# cannot read the code: correctness / wrong_high are judged against the reference answer. The judge it
# checks and records is DEEPMAP_JUDGE_MODEL (from .env.local; --judge-model is not used by this transport):
#   set -a; . benchmark/.env.local; set +a
#   DEEPMAP_JUDGE_TRANSPORT=openrouter DEEPMAP_REAL_ENGINE=1 python benchmark/run_benchmark.py --score <run_id>

# completeness is DETERMINISTIC (no model); only the correctness judge spends. A failed/abstaining
# judge keeps that card UNSCORED (never fabricated). status becomes "measured" only when both halves land.
```

## 6. Read the result

`benchmark/results/<run_id>/results.md` holds the head-to-head table (completeness is the HERO metric;
tokens/tool-calls/cost are reported but never led with). Single-hop controls that ON doesn't at least
tie at lower-or-equal cost are published as **losses** in `where_grep_wins` (the thesis demands it).

## Scenario B/C (multi-model generalization): see `RUN_SCENARIO_B.md`

Benchmarking **non-Claude arms** (GPT-5, Gemini) uses the OpenRouter **arm runner**
(`benchmark/arm_openrouter.py`), an agentic tool-calling loop that synthesizes a byte-compatible envelope
so the same blind scorer grades it. It is built and gated without spending. Running it needs an
`OPENROUTER_API_KEY`; the harness prints a projected cost (`cost_estimate.py`) and refuses to spend without
`--confirm-spend`. Run **Scenario A first** (the core number), then follow **`RUN_SCENARIO_B.md`** for the
cross-model story.
