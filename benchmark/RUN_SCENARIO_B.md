# Scenario B/C: cross-model generalization (multi-LLM turnkey runbook)

Scenario A answers *"does DeepMap help **Claude**?"*. Scenario B/C answer *"does DeepMap help **other
AIs too**?"*, by adding **GPT-5** and **Gemini 2.5 Pro** as benchmark ARMS (and, in C, as independent
graders).

> **What it is:** the **OpenRouter arm runner** (`benchmark/arm_openrouter.py`) is the agentic tool-use
> loop that lets a non-Claude model be an arm. It synthesizes a byte-compatible envelope so the **FROZEN**
> blind scorer grades it exactly like a Claude card (the parity keystone). Parallel blind scoring
> (`--score-workers`) and the pre-matrix spend guardrail come with it. All of it is gated with no
> key/network/spend (`tests/benchmark_arm_runner_gates.py`); `build/retrieval.py` ranking is untouched.
> **Running it needs** an `OPENROUTER_API_KEY` and real money: the harness prints a projected cost
> (`cost_estimate.py`) and refuses to spend without an explicit `--confirm-spend`. Run **Scenario A
> first** (the core number); B/C are the breadth story on top of it.

> **Honesty gates (do not skip):** every state rule from Scenario A still holds. A run stays
> *captured, unscored* until the **blind** Stage-2 score; `measured ≠ publishable`; no result is published
> yet, and a number reaches the README or the product page only from a run on the current release,
> published with its raw outputs. B/C are labelled **cross-model INDICATIVE** until their own ground-truth
> sign-off. The blind judge must be a DIFFERENT family than the arm it grades (no self-preference), which
> is why B/C judge with **non-Claude** models.

## 0. Prerequisites (in addition to Scenario A §0)

```bash
# Everything from RUN_SCENARIO_A.md §0 (Java + the Python deps, claude on PATH), PLUS an OpenRouter key:
# it is what pays for the non-Claude arms AND the non-Claude judge. Keep it in the gitignored env file:
printf 'OPENROUTER_API_KEY=sk-or-...\nDEEPMAP_JUDGE_MODEL=deepseek/deepseek-r1\n' > benchmark/.env.local
set -a; . benchmark/.env.local; set +a
```

The arm models are OpenRouter ids (`cost_estimate.py:PRICES` has the pinned rows):
`openai/gpt-5`, `google/gemini-2.5-pro`. An UNPRICED model still costs honestly (a default rate) and is
flagged; add its row to `PRICES` before trusting the dollar total.

## 1–2. Clones + MCP servers (identical to Scenario A §1–§2)

Clone the 5 repos at their pinned SHAs OUTSIDE this repo, build each clone as its OWN DeepMap project
(`benchmark/clone_graph.py build`: its own Neo4j instance, graph and MCP servers; see
`RUN_SCENARIO_A.md` §2 for why a shared-graph sync is refused under DeepMap's single routing rule), and pass
`--probe` against a clone's servers (must exit 0). The OpenRouter **ON** arm reaches
`deepmap_search/impact/cypher/embed` over the SAME per-clone HTTP MCP servers the Claude ON arm uses, so
ranking is identical and the pin / graph-SHA / ON-arm-ownership fail-closed checks apply unchanged.

```bash
D="$CLONES/psf__requests"
python benchmark/clone_graph.py build "$D"
read -r SP EP <<< "$(python benchmark/clone_graph.py ports "$D" | tr -d '\r')"
DEEPMAP_SEARCH_PORT=$SP DEEPMAP_EMBED_PORT=$EP DEEPMAP_REAL_ENGINE=1 \
  python benchmark/run_benchmark.py --probe --clone-root "$D"          # MUST exit 0
```

## 3. Preview the cost, then run the matrix (SPENDS on OpenRouter; `--confirm-spend` gates it)

Add each non-Claude arm with a repeatable `--arm-model`. The Claude Opus arm stays the anchor
automatically (a Scenario-B run is Opus + each `--arm-model`). The harness prints the projected cost and
REFUSES to spend until you pass `--confirm-spend`:

```bash
set -a; . benchmark/.env.local; set +a

# DRY the guardrail first (no key needed to SEE the projection is printed; it refuses without --confirm-spend):
DEEPMAP_REAL_ENGINE=1 python benchmark/run_benchmark.py \
  --arm-model openai/gpt-5 --arm-model google/gemini-2.5-pro \
  --clone-root "$CLONES"                                  # -> prints "[run] PROJECTED cost: $lo–$hi" then REFUSES

# Per-repo: build the clone as its own project, point the ON arm at ITS servers (per command), then stop them:
for NAME in psf/requests pallets/flask pallets/click pallets/werkzeug fastapi/fastapi; do
  D="$CLONES/${NAME/\//__}"
  python benchmark/clone_graph.py build "$D" || break                   # own instance + graph (SHA manifest) + servers
  read -r SP EP <<< "$(python benchmark/clone_graph.py ports "$D" | tr -d '\r')"
  DEEPMAP_SEARCH_PORT=$SP DEEPMAP_EMBED_PORT=$EP DEEPMAP_BENCH_VERIFY_ON_ARM=1 DEEPMAP_REAL_ENGINE=1 \
    python benchmark/run_benchmark.py \
    --repo "$NAME" --clone-root "$CLONES" --max-budget-usd 1.0 \
    --arm-model openai/gpt-5 --arm-model google/gemini-2.5-pro \
    --confirm-spend                                                     # <- the explicit go-ahead
  python benchmark/clone_graph.py stop "$D"                             # free the RAM; the graph stays on disk
done
# afterwards, per clone: python benchmark/clone_graph.py rm "$D"   (removes its instance + registry entry;
# works on a clone the loop already stopped: "nothing to stop" is success, see RUN_SCENARIO_A.md §3)
```

Each arm writes a distinct results subtree; the record `arm` is namespaced (`deepmap_on@openai/gpt-5`) so
`aggregate` groups per (repo, question, arm-model) without collision. A run that hits a HARD cap
(max_turns / budget / max_output_tokens) or a transport failure is recorded `is_error=True` with an
EMPTY answer and is EXCLUDED from the medians: a cap NEVER fabricates an answer.

## 4. Blind Stage-2 score → `measured` (`--score-workers`: parallelize the judge bottleneck)

The judge (about 1.5 min per call) is the bottleneck across n × 5 repos × 33 q × up to 3 arms.
`--score-workers N` runs the INDEPENDENT judge calls concurrently; the scoring LOGIC is unchanged (same
per-card verdicts, order-independent; proven in the gate):

```bash
set -a; . benchmark/.env.local; set +a
# Scenario B: a single INDEPENDENT (non-Claude) DeepSeek-R1 judge, 6 parallel workers. Under the openrouter
# transport the judge that --score checks (it must be none of the run's arm models) and records on every card
# is DEEPMAP_JUDGE_MODEL; no --judge-model needed (it is not used). It is sent only the prompt and cannot
# read the code: correctness / wrong_high are judged against the reference answer.
DEEPMAP_JUDGE_TRANSPORT=openrouter DEEPMAP_JUDGE_MODEL=deepseek/deepseek-r1 \
  DEEPMAP_REAL_ENGINE=1 python benchmark/run_benchmark.py --score <run_id> --score-workers 6
```

For **Scenario C** (max rigor), score each captured run with a **3-judge non-Claude panel**
(DeepSeek + Gemini + GPT-5) by re-scoring under each `DEEPMAP_JUDGE_MODEL` and reporting the panel;
`cost_estimate.py`'s Scenario-C block projects that cost. Each pass records its own `DEEPMAP_JUDGE_MODEL`
as the judge and REPLACES the run's previous verdicts in place, so snapshot each judge's pass (e.g.
`--merge` it under a per-judge run id) before the next one. `--score` also refuses a judge that is one of
the run's arm models (it would grade its own answers): a run that holds the GPT-5 or Gemini arm cannot be
re-scored by that same model.

## 5. Merge + read

```bash
python benchmark/run_benchmark.py --merge <id1>,<id2>,... --run-id sb-combined   # spends nothing
```

`benchmark/results/sb-combined/results.md` holds the cross-model head-to-head. Completeness stays the HERO
metric; `where_grep_wins` still publishes the families where grep ties/wins. The headline stays empty
until captured **and** blind-scored, and no number is published until a run on the current release is
(above).

## What each flag does (quick reference)

| flag | effect | gate |
|---|---|---|
| `--arm-model <id>` (repeatable) | add a non-Claude OpenRouter arm (ON+OFF) | needs `OPENROUTER_API_KEY` |
| `--claude-model <id>` (repeatable) | add another CLAUDE arm (e.g. `claude-sonnet-5`) via `claude -p` under your own Claude login | no OpenRouter key or `--confirm-spend` |
| `--confirm-spend` | the explicit go-ahead after seeing the projection | required for any OpenRouter spend |
| `--score-workers N` | N parallel blind-judge workers; logic unchanged | `--score` only |
| `DEEPMAP_JUDGE_TRANSPORT=openrouter` + `DEEPMAP_JUDGE_MODEL` | the independent non-Claude judge | needs the key |

## Cross-CLAUDE-model breadth (`--claude-model`): no OpenRouter key needed

If you only want *cross-model* breadth (does DeepMap help across Claude generations, e.g. **Opus 4.8 vs
Sonnet 5**) rather than cross-*vendor* (GPT-5/Gemini), you don't need OpenRouter at all. `--claude-model`
adds a second Claude arm run via `claude -p --model <id>` under your own Claude account; usage counts
against that account's limits, so check your plan's current terms before a long run. The record is
namespaced `deepmap_on@claude-sonnet-5`, so the SAME frozen blind scorer + aggregate group it per model.

```bash
export NEO4J_PASSWORD=deepmap DEEPMAP_REAL_ENGINE=1
D="$CLONES/psf__requests"
python benchmark/clone_graph.py build "$D"                                  # its own graph + servers
read -r SP EP <<< "$(python benchmark/clone_graph.py ports "$D" | tr -d '\r')"
DEEPMAP_SEARCH_PORT=$SP DEEPMAP_EMBED_PORT=$EP DEEPMAP_BENCH_VERIFY_ON_ARM=1 \
  python benchmark/run_benchmark.py --repo psf/requests --clone-root "$CLONES" \
  --claude-model claude-sonnet-5                                             # Opus anchor + Sonnet 5
python benchmark/clone_graph.py stop "$D"
python benchmark/run_benchmark.py --score <run_id> --clone-root "$CLONES" \
  --judge-model haiku                                                        # blind judge via claude -p
```

The judge must be none of the run's arm models, so the default `sonnet` judge is REFUSED here: as an alias
it may resolve to the `claude-sonnet-5` arm. (`best`, `default` and a blank `--judge-model` are refused on
every run, since they can run the Opus anchor's model; `opusplan` is refused here too, since it runs
Sonnet.) Pass `--judge-model` with a model none of the arms is (e.g.
`haiku`, as above), or use the independent openrouter judge (§4). `--clone-root` lets the cli judge read
each card's pinned clone (refused unless it is a pristine checkout at the pin). The breadth driver's final
mode takes the judge as `JUDGE_MODEL=<model>` (`MODE=final CLAUDE_MODELS="claude-sonnet-5" JUDGE_MODEL=haiku
bash benchmark/run_breadth.sh`; its run-ids are `breadth-<RUN_TAG>-*`, and a `MODE=loop` pass writes
`breadth-loop-<RUN_TAG>-*`, so the two never share a capture) and checks it before any capture.

**Caveats:** (1) a long run counts against your Claude account's usage limits and can be throttled by
them; (2) it shares those limits with any interactive Claude Code session on the same account; (3) with
`ANTHROPIC_API_KEY` set, `claude -p` may authenticate with that key and bill it per token instead.
