# DeepMap Benchmark: With vs Without (head-to-head)

> ## STATUS: no benchmark result is published yet
>
> The harness, the pinned repo set, the questions and the answer key are complete. The answer key was
> independently re-verified at every `file:line` (334 of 334 atoms; see `benchmark/CURATION_PROVENANCE.md`).
> The harness has been run internally on earlier versions of the graph, but the default graph has changed
> since, so any published number will come from a re-run on the current release
> (`benchmark/run_g3_n4.sh` for the five Python repos; `benchmark/run_breadth.sh` for the other languages),
> published with its raw outputs. Until then no number from this harness appears in `README.md`, on the
> product page or in marketing.

---

## 1. What this harness proves (and how, honestly)

It runs Claude Code headless (`claude -p`, model `claude-opus-4-8`) over a set of public OSS repos and answers each repo's hard, multi-hop questions **twice**: once **WITH** the DeepMap MCP servers loaded, and once **WITHOUT** them. The two answers are then blind-scored against a pinned ground-truth answer key.

It proves **one** thing, framed precisely:

> On the class of questions a string search **fundamentally cannot reconstruct** (multi-hop, transitive, runtime-edge, concept to implementation), does a DeepMap-equipped agent answer **more completely and more correctly** than the same agent armed only with grep + file reads?

That class is the value definition: *value = what grep fundamentally can't do.* The harness does **not** try to prove DeepMap is cheaper or faster. Token, tool-call, and wall-clock figures are **reported** but are **never** the headline. It leads with completeness/correctness, never token savings, because token advantages erode as base models improve and Opus 4.8 greps efficiently.

**Oracle.** A real-world known-issue oracle: public repos with knowable structure, hard multi-hop questions with pinned answers, DeepMap-with vs DeepMap-without.

---

## 2. The fair-baseline design (the honesty load-bearer)

A "win" here must be a win over a **real, capable** grep-agent, never a strawman.

- **Arm A, DeepMap ON.** `claude -p "<question>" --model opus --strict-mcp-config --mcp-config <the clone's servers>` with the DeepMap MCP servers loaded (`deepmap_search`, `deepmap_impact` and `deepmap_cypher` from the search server, and `deepmap_embed`), **plus built-in Read / Grep / Bash available**.
- **Arm B, DeepMap OFF.** The *identical* invocation, `--strict-mcp-config` included, but with **no `--mcp-config`**, and **with built-in Read / Grep / Bash still available**.

> **`--strict-mcp-config` on BOTH arms: only the servers `--mcp-config` names load (zero for Arm B, the empty baseline).** Per the live `claude` CLI, `--strict-mcp-config` means *"Only use MCP servers from `--mcp-config`, ignoring all other MCP configurations."* It is a **modifier on `--mcp-config`**, not a flag that points at a config file. Arm A passes it **with** `--mcp-config` for the measured clone's own server pair, so no ambient user- or plugin-scoped server (for example another project's DeepMap servers) loads next to them. Arm B passes `--strict-mcp-config` with **no `--mcp-config` at all**, which yields the empty set of MCP servers: no DeepMap server, no Neo4j tool. This also hardens the OFF-arm isolation probe (below): a leaked *user-level* MCP config can't sneak a tool into Arm B.

> **Grep is a first-class tool for the baseline.** Arm B is a strong agent that can grep and read the whole repo, so the baseline is measured on its own terms. A DeepMap advantage is only credible if it shows up *against* a grep-capable Opus 4.8, which is exactly the bar that erodes the token-savings claim, and exactly why tokens are not the hero.

> **The wiring is real.** Both DeepMap server files (`deepmap_search_server.py`, `deepmap_embed_server.py`) are in `build/`, and the ON-arm `--mcp-config` mirrors a project's real `.mcp.json` HTTP transport (`type:http`) on the measured clone's own port pair (`DEEPMAP_SEARCH_PORT`/`DEEPMAP_EMBED_PORT`, from `clone_graph.py ports`; `8787`/`8788` are only the fallback defaults). See `write_on_mcp_config` and `tests/benchmark_wiring_gates.py`. What only a live run shows is the **headless `claude` behavior**: that the servers load and respond inside a `claude -p` run, that Arm B stays isolated, and that the envelope captures cleanly. Those are exactly the three `--probe` assertions (below). They **spend**, so they run only under `DEEPMAP_REAL_ENGINE=1`. The third-party **stdio** `mcp-neo4j-cypher` server is not wired (a stdio server hangs under Claude Code on Windows, and the search server's own read-only `deepmap_cypher` covers Cypher); it is documented in a sibling key, never inside `mcpServers`.

### Pre-flight isolation gate (Arm B must be truly empty)

Before any token is spent, the harness asserts:

- **OFF-arm isolation:** under `--strict-mcp-config` with no `--mcp-config`, the agent has **no** Neo4j/DeepMap tool. A leaked DeepMap tool into Arm B invalidates *every* comparison, so this is a hard pre-flight gate (`assert_off_arm_isolated`).
- **ON-arm load check:** the DeepMap MCP servers actually load and respond in headless mode. The probe makes the ON arm *call* `deepmap_search` and confirms it returned (`assert_on_arm_loaded`).
- **Capture-path check:** both probe runs parse a well-formed, **non-error** JSON envelope with `total_cost_usd` + `num_turns` present (`assert_capture_path`). `claude --output-format json` returns a parseable envelope *even on failure* (`is_error:true`, budget abort, refusal) still carrying cost/turns, so the check explicitly vetoes an `is_error` / non-zero-exit run. Otherwise an errored ON run whose answer happened to contain the OK sentinel could falsely pass.

These three assertions are **implemented** (`do_probe` in `run_benchmark.py`; their PASS/FAIL logic is the pure `assert_*` functions, locked without spending by `tests/benchmark_probe_gates.py`). They **spend** (one cheap headless `claude` turn per arm), so `--probe` runs them only when `DEEPMAP_REAL_ENGINE=1` **and** `claude` is on PATH; otherwise it stops at the ENGINE guard without spending. `do_probe` returns `0` **only** when all three pass against real output: a leak, a missing tool, or a malformed envelope each force a non-zero exit. If any assertion fails: **stop and fix the harness. Do not run the matrix, do not fabricate.**

---

## 3. The lead metric (completeness/correctness, NOT tokens)

| Metric | Role | Source |
|---|---|---|
| **completeness %** | **THE HERO**: fraction of the question's `ground_truth_set` correctly enumerated | blind Stage-2 score |
| **correctness %** | claims in the answer not refuted by the code at the pinned SHA | blind Stage-2 score |
| **wrong_high** | cardinal-sin gate: any HIGH-confidence claim that is code-refuted (ties to DeepMap's confidence labels) | blind Stage-2 score |
| tool_calls | reported, not led: the "did it have to flail with grep" signal | JSON envelope `num_turns` |
| tokens | **reported, never the hero** | envelope `usage` |
| wall-clock | reported, environment-dependent | runner-measured |
| cost_usd | reported honestly. **HYPOTHESIS, not yet measured:** the ON arm is *expected* to cost more per question (Neo4j + MCP overhead); the delta is surfaced, not hidden | envelope `total_cost_usd` |
| winning_arm_attribution | diagnostic: which retrieval arm (vector / bm25 / graph_weighted) grounded each correct ON-arm enumeration | `deepmap_search` provenance (planned: the record's `arm_attribution_summary` is empty today) |

The lead is **completeness/correctness on hard questions**. Tokens / tool-calls / wall-clock are present in every table but are **never** the headline figure.

---

## 4. Publish-your-losses (non-negotiable)

The harness **publishes its own losses**: the question families where grep ties or wins are **reported, not hidden**. Cherry-picking discredits a benchmark on sight.

Concretely:

- **Every question, both arms, including ties and losses, appears in the results table.** A simple "where is function X defined" single-hop lookup where grep is faster, cheaper, and equally complete is one DeepMap **should not win**, and the table must show that.
- A dedicated **`where_grep_wins` / losses subsection** (see the TEMPLATE below) enumerates the families where Arm B matches or beats Arm A. Almost certainly: **single-hop lookups, string-literal searches, and cost on trivial questions** (ON carries Neo4j + MCP overhead).
- **Every question is tagged with whether it is the class grep fundamentally cannot do**: the `grep_can_do` / `family` columns. **Single-hop lookups where grep wins are counted as losses, not hidden.** A **single-hop control family** is deliberately included and reported as part of the corpus so the loss surface is visible.
- Each question carries a `grep_partial_answer` field stating, up front, the honest best a fair grep baseline can get, so a non-discriminating question is exposed rather than dressed up as a win.

> **Pre-run honesty tag (HYPOTHESIS, not characterized).** Until a blind-scored run exists, any prose about where DeepMap wins or loses is written **only** as a labeled hypothesis ("HYPOTHESIS, not yet measured"). It carries no measured numbers. The tag is removed only when blind Stage-2 scores replace the hypothesis with measured prose.

---

## 5. Methodology

- **N public OSS repos**, selected for **structural diversity, not popularity**, each large/tangled enough that multi-hop reachability genuinely matters, and each in a language the extraction engine parses. Include **at least one obscure/under-documented repo** (the less-dismissable result).
- **License + pinned SHA recorded per repo.** `benchmark/repos.yaml` records `{name, url, pinned_sha, license, stack, size_loc, graphify_parseable}`. **Clone for measurement only. Do NOT redistribute the repos' source.** `benchmark/results/` holds only questions, metrics, and captured transcripts/answers, **never** the cloned source tree.
- **SHA staleness is disqualifying:** the OFF arm greps the pinned SHA, and the ON arm's `/deep-map:setup` must build the Neo4j graph at the **same** SHA, or the comparison is unfair. **Both halves are verified, fail-closed:** the runner's `assert_pinned_clean` rejects a clone whose `HEAD` ≠ the pinned SHA or whose worktree is dirty (the OFF arm's grep target), **and** `assert_graph_at_pinned_sha` rejects a **stale ON-arm graph**. `build/sync.py` stamps `<clone>/.deepmap/graph_build.json` with the git SHA the graph was built at, and the runner refuses to spend unless that SHA equals the pinned SHA (or you declare it out-of-band via `DEEPMAP_GRAPH_SHA`). An unverifiable graph SHA is itself disqualifying, never a free pass.
- **4 runs per arm, MEDIAN taken.** Both arms are non-deterministic (the agent reasons in both), so both run counts are 4. **`min`, `max`, and `n` are reported alongside the median** to keep run-to-run variance visible and to flag any partial-survival median (a row aggregated over fewer than 4 surviving runs is marked `n⚠`). This median+min+max shape is a DeepMap choice.
- **Raw outputs + machine spec recorded:** every published number traces to a raw output published with it: the harness, the aggregator, `repos.yaml` (with pinned SHAs + licenses), `questions.yaml`, the raw `results/` JSON envelopes + answers + transcripts, and the **machine spec** (OS, CPU/RAM, `claude --version`, Neo4j Community version, extraction-engine version, model id `claude-opus-4-8`, and a **sampling-provenance block** recording the model alias and the resolved id, and that `claude -p` pins neither `--temperature` nor `--seed`, so runs vary and the medians, not any single run, are what absorb that variance).
- **Setup cost reported separately.** The one-time per-repo `/deep-map:setup` cost (index-build wall-clock + embedding spend) is recorded in a `setup_cost` block and **never folded into the per-question number**.
- **Blind scoring + separation of duties:** the runner **captures only**. Scoring is a separate pass with arm identity hidden ("Answer A / Answer B"). **Curator ≠ scorer ≠ harness author.** The judges' deliberations and scoring rationale are kept out of the published `results/`. A capture-only run is tagged *captured, unscored*, never `measured`, until that blind pass fills the HERO columns.

### Question design (what makes a question valid)

A question is valid **only** if grep returns a string match but **not the answer**: the agent has to reconstruct a graph. Families:

1. **Transitive reachability:** the full chain a request passes through before storage.
2. **Multi-hop dependency / blast-radius:** reverse reachability under a signature change.
3. **Cycle / coupling detection:** a circular dependency, and through which intermediate edges.
4. **Runtime-edge:** components wired only at runtime (registry / dynamic require / DI).
5. **Concept to implementation:** find a prose-described capability ("payload validation before persistence") via **intent-summary embeddings**; never "code-trained/code-aware" wording.

Plus a deliberately included **single-hop control family** (the loss surface, §4 above). Each question carries **exactly one** family tag; a prompt that mixes axes is tagged by its dominant axis.

---

## 6. Reproducibility: run it yourself

Every published figure will trace to a raw output published with it. An external evaluator can clone the repos at the recorded SHAs, run the harness with the shipped questions, and reproduce the numbers within run-to-run variance, **including DeepMap's losses**. PASS for the benchmark = the harness re-runs and reproduces its own numbers within run-to-run variance, and publishes its losses.

---

## 7. How to run

> The exact `claude -p` flag forms below were checked against the live `claude --version 2.1.129 --help` (all real): `-p`/`--print`, `--model`, `--output-format json`, `--permission-mode acceptEdits`, `--max-budget-usd`, `--mcp-config`, `--strict-mcp-config`. Re-confirm against the installed CLI before a run; if a flag's spelling ever proves wrong it is fixed against the live CLI, never invented.

### 7.1 Build check first: `--selftest` / `--dry-run` (spends nothing)

Before any metered run, verify the harness is wired correctly. This build check proves the harness runs against a single repo and emits the metrics table without spending a token:

```bash
# Wiring + build check only. Spends NOTHING. Safe in CI.
python ./run_benchmark.py --selftest      # validates repos.yaml, questions.yaml, record schema,
                                          #   shutil.which('claude'/'git'), the ON-arm contract config,
                                          #   and that the results TEMPLATE is empty (no fabricated cells)

python ./run_benchmark.py --dry-run       # alias for --selftest: emits the empty per-(repo,question,arm)
                                          #   metrics table WITHOUT calling claude
```

The runner is **ENV-GUARDED**: it is inert (spends nothing) unless `DEEPMAP_REAL_ENGINE=1` is set, the single on-switch. CI can call it freely; it spends nothing. `--selftest` and `--dry-run` never set that switch.

### 7.2 Probe: one cheap pre-flight run per arm

```bash
DEEPMAP_REAL_ENGINE=1 python ./run_benchmark.py --probe --clone-root <clone>
```

Runs the §2 pre-flight gates (ON-arm load, OFF-arm isolation, capture-path) on one cheap headless `claude` call per arm. If any assertion fails, **stop and fix. Do not run the matrix, do not fabricate.** The assertions are **implemented** (`do_probe`). They SPEND, so `--probe` runs them only with `DEEPMAP_REAL_ENGINE=1` **and** `claude` on PATH **and** the MCP servers reachable (build the clone and start its servers first: `python benchmark/clone_graph.py build <clone>`, see `RUN_SCENARIO_A.md` §2); otherwise it fails closed at the pre-flight without spending. `do_probe` returns `0` **only** when all three pass against real output (a leak, a missing tool, or an errored/`is_error` envelope each force a non-zero exit). The PASS/FAIL logic is locked without spending by `tests/benchmark_probe_gates.py`.

### 7.3 The full matrix (Opus 4.8)

```bash
# The matrix step (claude-opus-4-8). Omit --runs-per-arm for the methodology default n=4; a smaller
# --runs-per-arm is a faster calibration pass, recorded honestly. It is NOT run as part of the build.
# Requires DEEPMAP_REAL_ENGINE=1. RUN_SCENARIO_A.md gives the full per-repo sequence.
DEEPMAP_REAL_ENGINE=1 python ./run_benchmark.py \
    --clone-root <dir-of-fresh-clones-pinned-at-repos.yaml-sha> \
    --max-budget-usd 5.0          # hard per-run spend ceiling
```

**WITH (Arm A, DeepMap ON):** per question and run, the runner invokes:

```bash
claude -p "<question>" \
    --model opus \                        # resolves to claude-opus-4-8
    --output-format json \                # so cost parses from total_cost_usd
    --permission-mode acceptEdits \       # agent runs its tools non-interactively
    --max-budget-usd 5.0 \
    --strict-mcp-config \                 # ONLY the servers --mcp-config names: no ambient/plugin-scoped server
    --mcp-config <deepmap-servers.json>   # deepmap-search (deepmap_search, deepmap_impact, deepmap_cypher) + deepmap-embed, on the clone's own port pair
    # + built-in Read/Grep/Bash available
    # PRECONDITION: /deep-map:setup has built the Neo4j graph at the SAME pinned SHA (staleness rule)
```

**WITHOUT (Arm B, DeepMap OFF):** identical except **no** `--mcp-config` (the same `--strict-mcp-config` then loads zero MCP servers):

```bash
claude -p "<question>" \
    --model opus \
    --output-format json \
    --permission-mode acceptEdits \
    --max-budget-usd 5.0 \
    --strict-mcp-config                   # NO --mcp-config => zero MCP servers (no DeepMap, no Neo4j)
    # built-in Read/Grep/Bash STILL available: the fair, capable grep baseline (NOT a strawman)
```

> The two arms run the **same question**, and their argvs differ **only** by the `--mcp-config` pair, never by prompt. DeepMap's own plugin hooks are switched off in every `claude` child the harness starts (both arms, the probe and the cli judge): `DEEPMAP_HOOKS=0` plus each hook's own opt-out, so no SessionStart note or Grep nudge steers either arm, the grep baseline included.

**Fairness equalizers:** both arms on the same fresh clone at the same pinned SHA; `assert_pinned_clean` fails closed before spending a token. **Pristine reset between runs.** The agent runs with `--permission-mode acceptEdits`, so it CAN write to the clone; the run loop therefore restores the clone to a pristine checkout at the pinned SHA **before every run** (`reset_pristine`: `git -C <clone> reset --hard <sha>` + `git clean -fdx`), so one run's writes can never leak into the next. A reset that fails (git missing, non-repo, non-zero exit) **fails closed**: the runner refuses to spend against a tree it could not clean, rather than silently measuring a dirty one. (The command construction is unit-tested without spending in `tests/benchmark_fairness_gates.py`.) On Windows, `claude` and `git` resolve via `shutil.which` because they are `.CMD`/`.EXE` shims a bare-name spawn can't find.

### 7.4 Score (separate, blind)

The capture run (`--run`) **does not score**. Scoring is the separate blind Stage 2 (§5): the captured answers go to independent scorer/judge agents with arm identity hidden, scored against the `ground_truth_answer` + (for the cli judge given `--clone-root`, below) the code at the pinned SHA. Then aggregate to median+min+max+n, and **only then**, on `claude-opus-4-8` with the blind scores filled, does the record's status become `measured` and the results table get its HERO columns.

```bash
# Stage-2 BLIND scoring of a captured run (the correctness judge SPENDS; needs DEEPMAP_REAL_ENGINE=1).
# --clone-root: the root holding the capture's pinned clones (<clone-root>/<owner__name>).
DEEPMAP_REAL_ENGINE=1 python ./run_benchmark.py --score <RUN_ID> --clone-root <clones>
```

**`--score`** (`do_score`) keeps the honesty contract: the **completeness** half is the deterministic, no-model `score_completeness` matcher (a FROZEN word-bounded match rule: file:line / normalized symbol / edge / alias); the **correctness** half is `score_correctness`, a BLIND judge that runs a **different model** (`--judge-model`, default `sonnet`, never one of the run's arm models, below) under `--strict-mcp-config` so no DeepMap tool leaks into it, and with `--permission-mode default` (never plan mode). `score_run` flips a card to `measured` **only when BOTH halves succeed**: a judge error / missing answer keeps the card UNSCORED, so the run can never read `measured` on a fabricated number.

- **Only the cli judge checks claims in the code.** Given `--clone-root`, it runs in each card's own pinned clone (`<clone-root>/<owner__name>`), and `--score` refuses a clone that is not a pristine checkout at the pin (a capture leaves every clone it ran in pristine). Without `--clone-root` it warns that nothing is checked against the pinned code. The openrouter / api judges are sent only the question, the reference answer and the answer under review: they judge correctness / `wrong_high` against the reference answer and cannot read the code.
- **Separation of duties is checked on the model the judge actually calls**, and that model is what every card records: `--judge-model` on the cli transport (on the api transport, the concrete model id it maps to), `DEEPMAP_JUDGE_MODEL` under `DEEPMAP_JUDGE_TRANSPORT=openrouter` (`--judge-model` is not used there).
  A judge that is one of the run's arm models (provider-prefixed, dated and context-tagged spellings included), or
  a `claude --model` selector that can run one, is refused before anything is scored. That covers: `sonnet` on a
  run with a `claude-sonnet-5` arm; over the Opus anchor, `best` (the claude CLI runs Opus for it, or Fable on newer
  CLIs), `default` and a blank `--judge-model` (the account's default model, which can be Opus); `opusplan` over a
  Sonnet arm (it runs Sonnet, because the cli judge is pinned to `--permission-mode default` and never runs in plan
  mode); `fable` over a Fable arm; a Sonnet judge over an arm named by a selector (`--claude-model sonnet`,
  `--claude-model default`); and, on the cli transport, a legacy Opus id such as `claude-opus-4-1`, which the claude
  CLI re-points at its current Opus. The Opus anchor is compared as `claude-opus-4-8`, the model the anchor's
  `--model opus` must resolve to. A model re-pointed outside `--model` (an `ANTHROPIC_DEFAULT_*` variable, an
  organization default) is not followed; see `docs/known-limitations.md` ("The blind-judge guard").
- **A re-score starts clean:** each pass first clears the card's previous verdict, so a card whose re-score fails ends UNSCORED instead of keeping the earlier judge's verdict.
- **Provenance:** `results.json` records `graph_builds` (each repo's graph manifest: the graph its ON arm queried), `machine_spec.deepmap_version` and `capture_complete`; the resumable drivers (`run_g3_n4.sh`, `run_breadth.sh`) reuse a capture only when that provenance shows it measured THIS DeepMap's graph.
  `run_breadth.sh` also requires the same capture shape (runs per arm, extra Claude arms, family filter:
  `breadth_shape.json`, written before each capture), and its loop and final modes have their own default run-id
  prefixes (`breadth-loop` / `breadth`).

Like `--run`, `--score` is **ENV-guarded** (`DEEPMAP_REAL_ENGINE=1`); the deterministic completeness scorer and all the wiring are unit-tested without spending (`tests/benchmark_scoring_gates.py`, `tests/benchmark_correctness_gates.py`, `tests/benchmark_score_run_gates.py`, `tests/benchmark_score_cli_gates.py`).

> **Judge transport.** The default judge runs via `claude -p` under your own Claude login, so **no external
> API key is needed to score.** The judge prompt is piped via **stdin**: a long prompt passed as a CLI
> argument arrived mangled (which once looked like a safety refusal), and the arm had the same bug, also
> fixed. `DEEPMAP_JUDGE_TRANSPORT` is a switch:
>
> ```bash
> # DEFAULT: the cli judge under your own Claude login (no key); it reads each card's pinned clone:
> DEEPMAP_REAL_ENGINE=1 python ./run_benchmark.py --score <RUN_ID> --clone-root <clones>
> # OPTIONAL: a non-Claude INDEPENDENT judge (stronger blind credibility; a Claude judge grading a Claude
> # arm shares a family, so mild self-preference is possible). It is sent only the prompt (it cannot read
> # the code), and the judge it checks and records is DEEPMAP_JUDGE_MODEL (--judge-model is not used by
> # this transport):
> DEEPMAP_JUDGE_TRANSPORT=openrouter OPENROUTER_API_KEY=… DEEPMAP_JUDGE_MODEL=deepseek/deepseek-r1 \
>   DEEPMAP_REAL_ENGINE=1 python ./run_benchmark.py --score <RUN_ID>
> ```
>
> Honesty holds regardless: a failed judge means UNSCORED, never a fabricated number. See
> `docs/known-limitations.md` ("Blind-judge transport").

### 7.5 Cross-model arms: the multi-LLM extension

The harness above drives the arm via `claude -p` (Claude only). To benchmark **non-Claude arms** (GPT-5,
Gemini), that is *"does DeepMap help other AIs too?"*, the **OpenRouter arm runner** (`benchmark/arm_openrouter.py`)
runs an agentic tool-use loop over OpenRouter and synthesizes a **byte-compatible envelope**, so the
FROZEN blind scorer grades a non-Claude answer *exactly* like a Claude card (the parity keystone). It is
gated without a key, network, Neo4j or spend (`tests/benchmark_arm_runner_gates.py`), and
`build/retrieval.py` is untouched. Add each non-Claude arm with a repeatable `--arm-model`; the harness
prints a projected cost and refuses to spend without `--confirm-spend`; `--score-workers N` parallelizes
the judge without changing scoring logic. Runbook: **`benchmark/RUN_SCENARIO_B.md`**.

```bash
# Scenario B: Opus (anchor) + GPT-5 + Gemini, per repo (needs OPENROUTER_API_KEY):
DEEPMAP_REAL_ENGINE=1 python ./run_benchmark.py --repo <name> --clone-root <clones> \
  --arm-model openai/gpt-5 --arm-model google/gemini-2.5-pro --confirm-spend
```

### 7.6 Learned-vs-equal weights A/B: `--ab-weights`

DeepMap's opt-in local learning (`docs/RM1-ENABLE.md`) may apply learned fusion weights to live ranking
only after this benchmark shows **learned > equal** on a pre-registered slice. **`--ab-weights` is PLAN ONLY:** it
prints the A/B procedure and stops. It captures nothing, spends nothing and, without an explicit mode
(`--selftest` / `--probe` / `--score` / `--merge`), runs no ON/OFF matrix. The A/B itself is two
ON-arm captures you run on the SAME pinned clone graph plus `benchmark/weight_ab.py`, whose `ab_env`
docstring is the full ordered procedure; the plan prints its exact calls:

1. `deepmap tune <tuned root>` → a learned block with `beats_equal` and `significant` true.
2. **Equal capture:** relaunch the clone's deepmap-search server with
   `env=weight_ab.ab_env('equal', <its launch env>)` (`DEEPMAP_APPLY_LEARNED` removed) and run the ON arm → run A.
3. **Learned capture:** inside
   `with weight_ab.staged_learned_weights(<clone root>, <the tuned root's learned_fusion>) as token:`,
   relaunch the server with `env=weight_ab.ab_env('learned', <its launch env>)` (`DEEPMAP_APPLY_LEARNED=1`)
   and run the ON arm → run B. The env flag alone still serves equal weights: the server applies learned
   weights only when the project it SERVES (the clone) carries them in its own `.deepmap/instance.json`, so
   the block stages them there and restores the clone on exit. It raises rather than let an equal-weight
   "learned" capture run, e.g. for a clone that is not a synced DeepMap project (no
   `.deepmap/graph_build.json`) or weights that are the equal ones.
4. `ab = weight_ab.deterministic_ab(<answers of A>, <answers of B>, <questions>)`: the **frozen deterministic
   completeness scorer**, reading each question's `answer_key.ground_truth_set` from the shipped `questions.yaml`;
   no judge, no extra spend.
5. `evidence = weight_ab.build_g4_evidence(ab, pre_registered_slice=…, run_id_equal=A, run_id_learned=B, weights_fingerprint=token['fingerprint'])`,
   then `weight_ab.write_benchmark_evidence(<tuned root>, evidence)`.

`deepmap tune --apply` refuses to apply learned weights to live ranking until this produces a **significant**
`benchmark_evidence` block bound (`weights_fingerprint`) to the CURRENT learned weights; weights re-tuned since
the A/B need a new one. `deepmap tune --report` and `--apply`'s refusal name this same procedure, and the plan
also prints it in `--apply`'s words. Only the two captures spend (`DEEPMAP_REAL_ENGINE=1` + a live graph).

`--enrich-ab` (the enrichment A/B, scored with `benchmark/enrich_ab.py`) and the two offline-experiment flags
`--ab-rerank` / `--ab-graph-signal` (log replays; `docs/RM1-ENABLE.md`) are plan-only the same way: each prints its
procedure and stops, and an explicit mode given with it still runs after the plan.

```bash
python ./run_benchmark.py --ab-weights --ab-slice multi_hop              # the plan only: captures/spends nothing
python ./run_benchmark.py --selftest --ab-weights --ab-slice multi_hop   # the plan, then the selftest (no spend)
```

---

## 8. Cost ceiling (planning)

Per-run hard ceiling **`--max-budget-usd 5.0`**. Worst-case matrix bound: **N repos × 3–5 questions × 2 arms × 4 runs** runs at $5/run (e.g. 7 repos → ~168–280 runs → ~$840–$1,400 worst-case; the realistic figure is far lower: most questions finish well under the cap and OFF-arm grep runs are cheaper). The one-time per-repo `/deep-map:setup` cost is **separate and additional** and reported in `setup_cost`. Treat these as ceilings for planning, not predictions; the `--probe` is the cheap way to sanity-check per-run cost before committing to the matrix.

---

## 9. The gating discipline (read this last, it is the point)

- **Build now.** The build proof: *the harness runs against a single repo locally and emits the metrics table*. The `--selftest` / `--dry-run` build check and a single-repo `--probe` satisfy it without a public number.
- **Run later.** Executing across the repo set on Opus 4.8 is a runtime task you run on purpose, never part of the build.
- Until a run is executed on `claude-opus-4-8` **and blind-scored**: the harness, `repos.yaml`, `questions.yaml`, and an **empty/placeholder results table** are what ships, tagged **"designed, run pending."** A capture-only run is tagged *captured, unscored*, never `measured`. The record's `headline` stays empty; `provenance.publishable` stays `indicative`; the `where_grep_wins` prose is a labeled hypothesis and carries no measured numbers.
- **No number appears on the product page or in the README until a blind-scored run on the current release is published with its raw outputs.**

---

## 10. Results template

The results files under `benchmark/results/` are **TEMPLATES with empty cells**, never prefilled with values. The canonical template is **`benchmark/results/TEMPLATE.md`** and is reproduced below for reference.

```md
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
```
