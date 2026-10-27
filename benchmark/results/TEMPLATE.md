# DeepMap Benchmark Results: <repo or "all">: RESULTS TEMPLATE

> STATUS: DESIGNED, RUN PENDING (not yet executed on Opus 4.8 / claude-opus-4-8).
> NO CELL IN THIS FILE IS MEASURED. Every value below is an EMPTY placeholder.
> This file is filled ONLY by the blind Stage-2 scoring pass after the matrix runs.
> A capture-only run is tagged captured and UNSCORED, NOT `measured`, until that pass.
> Do NOT hand-type a number here. NEVER prefill a value. (Rules: benchmark/README.md §3-§5 and §9.)

## Run provenance (fill at run time; empty until then)

| field | value |
|---|---|
| schema | deepmap-validation/fd11-head-to-head/v1 |
| date | <empty: fill at run> |
| model | claude-opus-4-8 |
| claude_version | <empty> |
| machine_spec (OS / CPU / RAM) | <empty> |
| neo4j_community_version | <empty> |
| graphify_version | <empty> |
| runs_per_arm | 4 |
| aggregate | median (min / max / n reported alongside) |
| status_tag | designed, run pending (NOT YET EXECUTED) |
| provenance.publishable | indicative |
| publishable_figure_ready | false |

## Setup cost (reported SEPARATELY, never folded into per-question numbers)

| repo | index_build_wall_sec | nodes | edges | embedding_cost_usd |
|---|---|---|---|---|
| <repo> | <empty> | <empty> | <empty> | <empty> |

## Per-question metrics (median of 4 runs/arm; empty until a scored run)

Columns: repo · question · family · grep_can_do · arm · n ·
completeness_median (HERO) · correctness_median · tool_calls_median ·
tokens_median · wall_clock_median_s · raw_output_path

| repo | question | family | grep_can_do | arm | n | completeness_median | correctness_median | tool_calls_median | tokens_median | wall_clock_median_s | raw_output_path |
|---|---|---|---|---|---|---|---|---|---|---|---|
| <repo> | <question_id> | transitive_reachability | no | deepmap_on  | <empty> | <empty> | <empty> | <empty> | <empty> | <empty> | results/<repo>/deepmap_on/run-*/  |
| <repo> | <question_id> | transitive_reachability | no | deepmap_off | <empty> | <empty> | <empty> | <empty> | <empty> | <empty> | results/<repo>/deepmap_off/run-*/ |
| <repo> | <question_id> | multi_hop_blast_radius  | no | deepmap_on  | <empty> | <empty> | <empty> | <empty> | <empty> | <empty> | results/<repo>/deepmap_on/run-*/  |
| <repo> | <question_id> | multi_hop_blast_radius  | no | deepmap_off | <empty> | <empty> | <empty> | <empty> | <empty> | <empty> | results/<repo>/deepmap_off/run-*/ |
| <repo> | <question_id> | cycle_coupling          | no | deepmap_on  | <empty> | <empty> | <empty> | <empty> | <empty> | <empty> | results/<repo>/deepmap_on/run-*/  |
| <repo> | <question_id> | cycle_coupling          | no | deepmap_off | <empty> | <empty> | <empty> | <empty> | <empty> | <empty> | results/<repo>/deepmap_off/run-*/ |
| <repo> | <question_id> | runtime_edge            | no | deepmap_on  | <empty> | <empty> | <empty> | <empty> | <empty> | <empty> | results/<repo>/deepmap_on/run-*/  |
| <repo> | <question_id> | runtime_edge            | no | deepmap_off | <empty> | <empty> | <empty> | <empty> | <empty> | <empty> | results/<repo>/deepmap_off/run-*/ |
| <repo> | <question_id> | concept_to_impl         | no | deepmap_on  | <empty> | <empty> | <empty> | <empty> | <empty> | <empty> | results/<repo>/deepmap_on/run-*/  |
| <repo> | <question_id> | concept_to_impl         | no | deepmap_off | <empty> | <empty> | <empty> | <empty> | <empty> | <empty> | results/<repo>/deepmap_off/run-*/ |
| <repo> | <question_id> | single_hop_control      | yes| deepmap_on  | <empty> | <empty> | <empty> | <empty> | <empty> | <empty> | results/<repo>/deepmap_on/run-*/  |
| <repo> | <question_id> | single_hop_control      | yes| deepmap_off | <empty> | <empty> | <empty> | <empty> | <empty> | <empty> | results/<repo>/deepmap_off/run-*/ |

Notes on the columns:
- family: one of transitive_reachability, multi_hop_blast_radius, cycle_coupling,
  runtime_edge, concept_to_impl, single_hop_control.
- grep_can_do: "yes" marks the single-hop control class grep can answer; a DeepMap loss on a
  grep_can_do=yes row is EXPECTED and is reported as a loss, not hidden.
- n: number of surviving runs the median was taken over; n<4 (e.g. a timeout) is flagged so a
  partial-survival median is never read as a clean 4-run median.
- completeness_median is THE HERO column. tool_calls / tokens / wall_clock are REPORTED, never led
  with.
- Also captured per run (omitted here for width, present in results/<repo>/<arm>/run-N/):
  correctness_pct, wrong_high, cost_usd, winning_arm_attribution {vector,bm25,graph_weighted}.

## Aggregate by arm (median / min / max; empty until a scored run)

| arm | completeness_median | completeness_min | completeness_max | correctness_median | wrong_high_total | cost_usd_median |
|---|---|---|---|---|---|---|
| deepmap_on  | <empty> | <empty> | <empty> | <empty> | <empty> | <empty> |
| deepmap_off | <empty> | <empty> | <empty> | <empty> | <empty> | <empty> |

## Arm-attribution summary (over-bias diagnostic; empty until a scored run)

| arm grounding source | count |
|---|---|
| vector | <empty> |
| bm25 | <empty> |
| graph_weighted | <empty> |

## Losses: where_grep_wins (HYPOTHESIS, not yet measured; carries NO numbers until a scored run)

> Until a scored run exists, this section is HYPOTHESIS ONLY and contains NO measured values.
> The harness PUBLISHES ITS OWN LOSSES: families where Arm B (grep) ties or beats Arm A
> appear HERE, in full, and are never hidden.

| family | grep_can_do | hypothesis (pre-run, no numbers) | measured outcome (fill after scoring) |
|---|---|---|---|
| single_hop_control          | yes | HYPOTHESIS: grep likely ties/wins: faster, cheaper, equally complete | <empty> |
| string_literal_search        | yes | HYPOTHESIS: grep likely wins on exact-string lookups | <empty> |
| cost_on_trivial_questions    | n/a | HYPOTHESIS: ON arm likely costs MORE (Neo4j + MCP overhead); delta surfaced, not hidden | <empty> |

Per-repo honest caveat (fill after scoring):

| repo | honest_caveat (pre-run HYPOTHESIS) |
|---|---|
| <repo> | HYPOTHESIS: on a small/flat-import-graph repo grep may reconstruct the chain too; the DeepMap advantage should show on the tangled L-tier repos. |

## Overall caveat

INDICATIVE: N repos, small-n, single machine, one model run. Not a generalizable headline.

## Headline

<EMPTY until a run on claude-opus-4-8 has its blind Stage-2 scores filled; never hand-typed>
