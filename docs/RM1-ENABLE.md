# DeepMap's local learning loop: runbook (opt-in, local only)

The learning loop is an **opt-in experiment, off by default and local only**. When you turn it on,
DeepMap logs your searches, notes which results you then use, and computes recommended per-arm ranking
weights for your codebase, offline. Nothing leaves your machine. It changes nothing until you enable it,
and enabling it still leaves search results unchanged: learned weights reach live ranking only when you
apply them yourself, and only after they pass the held-out checks (k-fold cross-validation and a
bootstrap significance test on queries the tuner did not train on) and a benchmark A/B that confirms
they beat equal weights. This is the single runbook: enable → observe → tune → (gated) apply, plus the
drift guard, cross-project seeding, and the offline experiments.

> **Honesty:** the learned weights are a **recommendation only** until you apply them. Live search
> reads them only through `build/learned_fusion.py`, whose four-condition gate (below) keeps equal
> weights unless every condition holds. So enabling this **collects data and computes a
> recommendation**; it does not change your search results.

## The loop at a glance

```
log searches ──► capture accesses ──► observe (`deepmap insights`) ──► tune offline
 (opt-in)         (opt-in hook)        read-only, n-honest             (applied:false)
                                                                            │
        equal weights stay live ◄── drift guard re-validates ◄── benchmark A/B
        (the ultimate fallback)      every re-tune                └─ gated apply (opt-in env)
```

One switch turns the loop on; one line (`deepmap doctor` / `deepmap tune --status`) reports it.

## What it is (four opt-in, local-only pieces)

1. **Retrieval logging** (`build/retrieval_log.py`): appends one record per search to
   `<repo>/.deepmap/retrieval_log.jsonl` (gitignored, never leaves the machine): the query (+ its intent
   features), each arm's ranked candidate ids, and the returned results. Wired into `deepmap search` (CLI)
   and the MCP `deepmap_search` tool as a **no-op unless enabled**.
2. **Access capture** (`hooks/deepmap_access_hook.py`): a PostToolUse hook that records which returned
   results you then act on → a **graded** implicit signal: a Read is a weak positive, an Edit/Write or a
   `deepmap_impact` traversal is a strong positive. It always exits 0 / prints nothing, so it can never
   disrupt a tool call.
3. **Offline tuner** (`build/tune_weights.py`, `deepmap tune`): reads the log, folds accesses into graded
   soft-positive labels (plus skip-above soft negatives), and searches per-arm weights that rank the useful
   results higher. It learns a **global** blend and, where the data supports it, **per-intent profiles**
   (a different blend for call-graph/"who calls" questions vs. symbol lookups vs. long natural-language
   queries). Default objective is **graded nDCG@10** (MAP/MRR available). Cold-start safety throughout:
   equal default, a minimum-labeled-query threshold, regularize-toward-equal, an exploration floor, and a
   **k-fold cross-validation + bootstrap significance** check: it declares a win only when the improvement
   is statistically significant, else it **reverts to equal**. Writes `<repo>/.deepmap/instance.json`
   `learned_fusion` with **`applied: false`**.
4. **Observation** (`build/insights.py`, `deepmap insights`): a LOCAL, read-only summary of the
   retrieval log. It reads the log and **writes nothing**; it changes no ranking. Honest about sample
   sizes: every metric reports n, and below the tuner's cold-start thresholds it prints counts only
   and refuses conclusions.

## Enable it: the one command

```bash
deepmap learning --enable     # flips the durable flag + prints the access-hook snippet to paste
deepmap learning --status     # ON/OFF + "X/150 labeled retrieval logs" accrual
deepmap learning --disable    # turn it back off
```

`deepmap learning --enable` does the durable half for you: it writes
`.deepmap/instance.json` `learned_fusion.learning_enabled=true` (so logging + the offline post-sync
tuner turn on without needing `DEEPMAP_LEARNING` in the env each session) and prints the exact
`PostToolUse` access-hook block to paste into your Claude Code `settings.json` (the hook is opt-in and
never auto-installed). It does **not** activate learned weights: live ranking stays equal-weight until
the separate Apply gate. The manual mechanics it wraps are below.

## Enable it: one-switch mode (under the hood)

```bash
# THE ONE SWITCH: logging + post-sync re-tune + drift guard in one go (either form):
export DEEPMAP_LEARNING=1
#   ...or persist it:  .deepmap/instance.json -> {"learned_fusion": {"learning_enabled": true}}

# This does NOT set DEEPMAP_APPLY_LEARNED. Applying learned weights to live ranking remains the
# separate 4-condition gate (see "Apply", below); the switch only observes and recommends.
```

How the switches compose (most-specific wins):

| env | scope | interaction |
|---|---|---|
| `DEEPMAP_LEARNING` | master: logging + auto-tune + drift guard | truthy → ON; explicit `0` vetoes the instance flag; unset → the instance flag decides |
| `learned_fusion.learning_enabled` (instance.json) | durable master | same effect as the env, survives shells |
| `DEEPMAP_RETRIEVAL_LOG` | logging only | explicit `0` vetoes logging even under the master switch |
| `DEEPMAP_AUTO_TUNE` | the post-sync tune leg only | truthy → ON regardless of master; explicit `0` vetoes the tune leg (logging stays on) |
| `DEEPMAP_APPLY_LEARNED` | live activation | **never implied by anything above**; see the gated Apply section |
| `DEEPMAP_SEED_PRIOR` | cross-project seeding | independent opt-in (see Seeding) |

```bash
# Register the access hook (PostToolUse) in your Claude Code settings.json (NOT auto-installed):
#    (matches Read/Edit/Write + the deepmap_impact tool. The `.*deepmap_impact` alternative matches
#     BOTH the project-scoped name `mcp__deepmap-search__deepmap_impact` and the plugin-scoped
#     `mcp__plugin_deepmap_deepmap-search__deepmap_impact`.)
```
```jsonc
{
  "hooks": {
    "PostToolUse": [
      { "matcher": "Read|Edit|Write|.*deepmap_impact",
        "hooks": [ { "type": "command",
                     "command": "python <repo>/hooks/deepmap_access_hook.py" } ] }
    ]
  }
}
```

Now use DeepMap normally. Each `deepmap search` logs a record; each Read/Edit of a returned file
(or a `deepmap_impact` on a returned symbol) credits that result; each `deepmap sync` rotates the
log, runs the drift guard, and re-tunes (all offline, all `applied:false`). Expect roughly 1
labeled query per 2–3 searches; the tuner starts concluding at **150 labeled** (≈300–450 searches).

## Watch it

```bash
deepmap insights                   # the read-only log summary (see below)
deepmap insights --json            # machine-readable
deepmap insights --since 2026-07-01   # window by ISO date/time prefix (string compare)
deepmap doctor                     # one `learning:` line: ON/OFF, events, "X/150 labeled retrieval
                                   # logs", last tune, profiles, drift verdict, live ACTIVE/INACTIVE + why
deepmap status                     # a `learning: X/150 labeled retrieval logs` accrual line (offline;
                                   # printed before the Neo4j connect, so it shows even if the DB is down)
```

`deepmap insights` sections: **query intents** (bucket mix + examples), **zero-result searches**,
**searches whose results were never used** (with an honesty guard: no access events at all reads
as "usage not captured: hook not registered", never as abandonment; pre-hook searches are
excluded; always an upper bound), **which arm first surfaced the result you used** (best-graded
target, fractional ties), and **what the tuner would produce today** (a dry-run; nothing
written). Below **150 labeled / 40 per intent bucket** it prints counts only and refuses
conclusions; queries stored under `DEEPMAP_LOG_REDACT` are never displayed.

## Tune (offline: no Neo4j, no network, no spend)

```bash
deepmap tune                       # (or: python build/deepmap_cli.py tune <repo>)  tune + write instance.json
deepmap tune --status              # the learning line + weights + profiles + seed + coverage
deepmap tune --report              # the benchmark-evidence view + WHY learned weights are / aren't applied live
deepmap tune --dry-run             # compute + print, write nothing (skips the drift guard too)
deepmap tune --min-queries 150     # global cold-start threshold (default 150; below it, equal stands)
deepmap tune --objective ndcg      # ndcg (default) | map | mrr
deepmap tune --optimizer grid      # grid (default, exhaustive/auditable) | coord (finer / with --tune-rrf-k;
                                   #   single-arm, pairwise and all-arm joint moves)
deepmap tune --tune-rrf-k          # also search the RRF smoothing constant, recorded as a recommendation only:
                                   #   live search and every learned-vs-equal judgement use k=60
deepmap tune --no-profiles         # global weights only (skip per-intent profiles)
deepmap tune --min-queries-bucket 40   # a per-intent profile needs >= this many in-bucket queries
deepmap tune --export-prior        # pool THIS project's significant win into ~/.deepmap (see Seeding)
deepmap tune --seed-from-prior     # warm-start from the user prior: among equally-scoring weights the one
                                   #   closest to the prior wins (grid tie-break; coord starts there); see Seeding
```

`deepmap tune` prints the chosen weights, `beats_equal`, `significant`, the k-fold CV mean delta + 95% CI,
and any per-intent profiles, then writes `.deepmap/instance.json`. Below the labeled-query threshold, or
when the improvement isn't statistically significant, it **reverts to equal**, so you can't make ranking
worse by running it. A `--tune-rrf-k` k is printed and stored as a recommendation: the CV significance, the
per-slice and per-profile judgements and the drift guard are all evaluated at k=60, the k live search uses.

## Drift guard (automatic honesty check)

On every loop re-tune (and every manual `deepmap tune`), the weights that are **currently
standing**, including ones applied live, are re-validated against equal on the **newest slice**
of labeled queries (the last `max(20, 25%)` in log order), with the same seeded-bootstrap
significance the tuner uses, mirrored: a degradation whose confidence interval sits entirely
below zero **auto-reverts to equal and says so**: in the sync/tune output, under
`learned_fusion.drift` (with the full pre-revert state kept in `drift.previous`), and in the
doctor/status learning line (`WARN … drift degraded (REVERTED to equal)`). A stale profile is
never silently kept; the only `applied` transition the guard ever makes is **true → false**.

## Seed a new project from your other projects (optional, local-only)

An optional user-level prior at `~/.deepmap/learned_prior.json` (`DEEPMAP_HOME`) pools per-arm
weight tendencies across THIS machine's projects; **only significant wins are ever exported**
(noise is never pooled), entries expire after 180 days, and the pooled vector is clamped to the
tuner's equal-anchored envelope.

```bash
deepmap tune --export-prior        # explicit export of this project's win (refused unless significant)
deepmap tune --seed-from-prior     # explicit consume: warm-start the tuner from the pooled prior
export DEEPMAP_SEED_PRIOR=1        # automatic both ways inside the learning loop (export wins,
                                   # warm-start + record a seed recommendation on cold projects)
```

Honesty properties: the consuming project's own entry is excluded (no self-echo); the seed is a
**recommendation only**: it is written as `learned_fusion.seed`, warm-starts the tuner's search
(never the safety envelope), and is **never read by live ranking**; the project's own learned
state always shadows it; **equal weights remain the ultimate fallback**. How it warm-starts: on the
default grid optimizer, among equally-scoring weight vectors the one closest to the seed wins (the
tie-break; the k-fold CV folds use the same seeded procedure); on `coord` it is also the starting
point. It never out-scores the data, and below the cold-start threshold equal stands regardless. Delete
`~/.deepmap/learned_prior.json` any time.

## Apply learned weights to live search (gated)

Applying learned weights is **off by default** and guarded so the shipped behavior stays equal-weight.
It turns on only when **all** hold: the `DEEPMAP_APPLY_LEARNED` env is set **and** the tuner found a
significant win **and** a benchmark A/B confirms learned > equal on the gold set (`deepmap tune --apply`
refuses without that evidence, or with evidence not bound to the current learned weights).
The ranking code itself is unchanged by this feature: the CLI/MCP layer asks `build/learned_fusion.py` for the
weights and passes them to the retriever's existing `arm_weights` parameter, per-intent profiles resolving per
query. `build/learned_fusion.py` returns learned weights only when all four of its conditions hold
(`DEEPMAP_APPLY_LEARNED` is set, `applied` is true, `beats_equal` is true, `significant` is true), and
equal weights otherwise.

The benchmark A/B is that evidence, and you run it yourself (the two ON-arm captures spend). `python
benchmark/run_benchmark.py --ab-weights --ab-slice multi_hop` only PRINTS the procedure: it captures and
spends nothing. The procedure itself is `benchmark/weight_ab.py` (its `ab_env` docstring;
`benchmark/README.md`), the same one `deepmap tune --report` and `--apply`'s refusal name:

```python
import weight_ab   # benchmark/weight_ab.py
# 1. EQUAL capture: relaunch the clone's deepmap-search server with
#    env=weight_ab.ab_env("equal", <its launch env>) and run the ON arm on the gold questions -> run A
# 2. LEARNED capture: the env flag alone serves equal weights, so stage the tuned block into the
#    SERVED clone (restored on exit; it raises instead of letting an equal-weight "learned" capture run):
with weight_ab.staged_learned_weights(<clone sync root>, <this project's tuned learned_fusion>) as token:
    ...  # relaunch the server with env=weight_ab.ab_env("learned", <its launch env>); ON arm -> run B
# 3. score both captures with the frozen deterministic scorer (no judge, no spend)
ab = weight_ab.deterministic_ab(<answers of A>, <answers of B>, <questions>)
# 4. record the evidence, bound to the weights the learned capture served
evidence = weight_ab.build_g4_evidence(ab, pre_registered_slice="multi_hop", run_id_equal=A,
                                       run_id_learned=B, weights_fingerprint=token["fingerprint"])
weight_ab.write_benchmark_evidence(<this project's root>, evidence)
```

```bash
deepmap tune --apply    # flips applied:true ONLY with significant benchmark evidence bound to the CURRENT
                        # learned weights; with unbound evidence, or weights re-tuned since the A/B, it refuses
export DEEPMAP_APPLY_LEARNED=1   # opt in at query time (still 100% local)
```

**Revert:** unset `DEEPMAP_APPLY_LEARNED` (instant), or set `learned_fusion.applied:false` in
`.deepmap/instance.json` (persistent), or delete `instance.json` (regenerated by `deepmap tune`).
The drift guard also reverts automatically on a significant newest-slice degradation. A hand edit must
leave the file a JSON object: every writer (`deepmap tune`, `deepmap learning`, the drift guard, the
benchmark-evidence writers) REFUSES to overwrite an `instance.json` holding anything else (an empty file is
fine), so a typo makes those writes fail until you fix or delete the file. Writes are atomic and
cross-process locked (the sidecar `.deepmap/instance.json.lock`, a bounded 5 s wait; a writer that cannot
take it skips its write, never hangs).

## Offline experiments (never live)

Two further levers exist as OFFLINE experiments only. Each persists under instance.json
`experiments.<name>` with `applied` **forced false**, a key the live resolver provably ignores:

- **Access-prior re-rank** (`build/access_prior.py`; `run_benchmark.py --ab-rerank` prints the plan only:
  it captures, spends and runs nothing): a post-fusion re-rank by "the nodes you actually work in"
  (recency-decayed, grade-weighted), A/B'd by log replay with leak-gated query-level folds. Its
  circularity (the prior AND the labels both come from accesses) is named in the persisted block;
  `prior_hit_rate` is the exposure statistic. Log-replay evidence only; activation would
  additionally require the benchmark twin-capture A/B.
- **Framework-edge graph signal** (`build/graph_signal_ab.py`; `run_benchmark.py --ab-graph-signal`
  prints the plan only: it captures, spends and runs nothing): the graph arm's in-degree recomputed
  to also count the typed framework/runtime edges (λ-discounted; λ=0 ≡ CALLS-only), replayed against
  a live local graph. Records `replay_fidelity` (a rebuilt graph surfaces as drift) and
  `qrel_coverage` (the log-bias against the variant: a win is conservative, a loss is not
  conclusive). It is the measurement behind any later decision to fold those edges into live ranking,
  which needs the benchmark first; the live graph arm counts `:CALLS` only.

## Privacy & housekeeping (optional)

```bash
export DEEPMAP_LOG_REDACT=1   # store a hash instead of the raw query text in the log (still local)
export DEEPMAP_AUTO_TUNE=1    # the tune leg alone (without the full master switch)
```
The log self-caps (oldest entries rotate out on every sync); redaction leaves tuning unchanged (it
keys on a stable id) and `deepmap insights` never displays redacted text.

## Guarantees

- **Zero-egress.** The only artifacts are under `<repo>/.deepmap/` and (for the optional prior)
  `~/.deepmap/`, both local; nothing is uploaded.
- **Opt-in.** Off by default; a no-op until you flip the switch and register the hook.
- **Read-only observation.** `deepmap insights` and the doctor learning line never write and never
  change ranking.
- **Safe.** `applied: false` everywhere by default: learned weights reach live ranking only after
  `deepmap tune --apply` (which needs significant benchmark evidence bound to the current weights);
  equal weights, the non-regressing default, stand until then. Even after `--apply`, live ranking only
  changes when you set `DEEPMAP_APPLY_LEARNED`. The drift guard auto-reverts a stale profile, loudly.
- **Honest.** A win is claimed only when it is statistically significant (CV + bootstrap CI) and
  confirmed on the benchmark, never on the log's own numbers alone. Below the cold-start
  thresholds every surface refuses conclusions and says why.
- **Deletable.** Remove `.deepmap/retrieval_log.jsonl` / `.deepmap/instance.json` /
  `~/.deepmap/learned_prior.json` any time; they regenerate.

## Turn it off

```bash
unset DEEPMAP_LEARNING DEEPMAP_RETRIEVAL_LOG DEEPMAP_AUTO_TUNE DEEPMAP_SEED_PRIOR DEEPMAP_APPLY_LEARNED
#   (and remove learning_enabled / logging_enabled from .deepmap/instance.json)
rm -f .deepmap/retrieval_log.jsonl        # delete the accrued log
rm -f ~/.deepmap/learned_prior.json       # delete the cross-project prior
# and remove the PostToolUse hook entry from settings.json
```
