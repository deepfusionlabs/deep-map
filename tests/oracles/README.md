# STAGE-0 oracle registry: pinned corpora, throwaway instances, external repos

The STAGE-0 no-regression gate proves `build/retrieval.py`'s top-K **ordering** hasn't changed
(score values are ignored: ordering is the contract). This dir holds the frozen oracles +
the registry that maps a `repo_id` to its oracle **and the exact corpus it was frozen on**.

> Building/freezing/gating an oracle is **`[NEO4J]`** (operator-run on a live instance). The shared logic is
> pure and unit-locked no-Neo4j in `tests/stage0_harness_gates.py` + `tests/stage0_external_gates.py`.

## Files
- `registry.json`: `{repo_id → {repo_dir, oracle_path, queries_file, pinned_sha, corpus}}`. Paths are
  repo-root-relative. `corpus` = `{sha, builder, tree | paths}`: the COMMIT the graph is built from, what
  to take from it (`tree` = one subdirectory whose contents become the corpus root; `paths` = repo paths
  kept in place) and the builder (`graphify-default-sync` = the product's own default `deepmap sync`;
  `stage0-ast` = `tests/stage0_real_graph.py`).
- `self-graphify-build.json`: `build/` graphed by the product's DEFAULT sync (graphify engine, every
  `DEEPMAP_*` knob unset). `../stage0_real_oracle.json` (`self`): `build/ + scripts/ + tests/` graphed by
  the harness's stdlib-ast builder.
- `<repo_id>.json`: a frozen oracle for an external repo (`tests/stage0_portable.default_oracle_path`).

## Why the corpus is pinned (2026-09 re-freeze)
The keys are `relpath::name:line`, so an oracle only means something against the corpus it was frozen
on. Both committed oracles used to be frozen on the LIVE work tree: every later edit to `build/` moved
line numbers, and the 2026-09 live validation found 19/50 and 13/50 keys still valid, so the gate failed
whatever the ranking did. Its default target was also `bolt://localhost:7687`, which on that box was
another project's Neo4j (and `stage0_real_graph.py` wipes its target). Since then:

- the corpus is materialized from the pinned commit (`git archive`), never the work tree;
- every live script takes an EXPLICIT target, `--project <dir>` (that project's own DeepMap instance,
  from the port registry) or `--uri bolt://host:port` (or an exported `NEO4J_URI`), and exits 2 without
  one; the builders (`stage0_real_graph.py`, `stage0_live_smoketest.py`) wipe only an empty graph, a
  graph the harness itself made (`:_Stage0Harness` marker) or `--project`'s own (else `--allow-wipe`);
- `stage0_regression_check.py` exits 2 (config error), not 1 (regression), when the target graph holds
  fewer than half of the oracle's keys: a graph of some other corpus is not an ordering regression.
- the product sync is reproducible: `build/embeddings.py` used to shuffle the nodes it embeds with the
  process-global, UNSEEDED RNG (HNSW insert order), which also changed the encode chunks, and a batched
  encode is not bit-identical to the same text in another batch (measured 0/50 bit-equal; 119/571
  embeddings differed between two fresh builds of the same corpus), so a rebuild reshuffled near-tied
  ranks 4-10. Since a later fix that order is a pure function of the corpus (a private RNG seeded from the
  sorted content hashes). The harness still runs the UNCHANGED product sync with Python's `random` seeded
  (+ `PYTHONHASHSEED=0`) as defence in depth. The `stage0-ast` builder is deterministic by construction
  (one text per encode).

**Re-frozen 2026-09-25** at corpus commit `9c8d50bceab9` with `tests/stage0_refreeze.py --freeze`
on throwaway instances (never the repo's own instance). Independent fresh `--check` rebuilds then gated
both oracles PASS (5/5 queries each), and `tests/stage0_regression_check_redtest.py` (a flipped fused sort
in a COPY of the retriever) turned the gate RED: it is load-bearing. Re-run `--check` after any change to
the extraction pipeline; the `self-graphify-build` oracle tracks the product's default graph, so an
intended extraction change means a `--freeze` (same `--sha`) with the reason in the commit.

**`self-graphify-build` re-frozen 2026-09-26** (same corpus commit `9c8d50bceab9`) after that fix made
the embedding order corpus-derived: a fresh `--check` against the 2026-09-25 freeze diverged ONLY in
near-tied ranks 4-10 (the top 3 of every query unchanged; same 571 nodes): the new, deterministic
embed order changes the encode chunks, exactly the batch effect above. After the re-freeze two
independent fresh `--check` rebuilds (and the `self` oracle's) PASSED 5/5 queries each and the redtest
turned the gate RED again. The freeze reflects the product tree of that day; if a later change to the
extraction / embedding / FTS path moves near-tied ranks, re-run `--check` and, for an intended change,
`--freeze --sha 9c8d50bceab96288f83877c5306167b54b6d918a --repo-id self-graphify-build`.

The committed oracles are pinned to a commit of DeepMap's development history; in a clone that lacks it,
`--check` cannot rebuild the corpus (and `tests/stage0_harness_gates.py` skips its corpus key check with a
`[skip]` line). Re-freeze on a commit you have (`--freeze --sha <commit>`) to get a local baseline.

## Check / re-freeze (operator runbook, `[NEO4J]`)
Needs Java 17/21, the ML stack and the graphify engine; `NEO4J_PASSWORD` defaults to `deepmap`.

```bash
# GATE both committed oracles against a fresh throwaway rebuild of their pinned corpora:
python tests/stage0_refreeze.py --work C:/tmp/s0 --check
# RE-PIN + RE-FREEZE (after a deliberate extraction / retriever change; default --sha = HEAD):
python tests/stage0_refreeze.py --work C:/tmp/s0 --freeze [--sha <40-hex>] [--repo-id self-graphify-build]
```

`--work` must be a fresh absolute directory outside this repo. The driver pre-allocates a NON-base port
pair (it can never adopt the shared base store 7687), provisions the throwaway project, builds, freezes or
gates, and ALWAYS cleans up (`provision.py stop`, `instance-rm --key <key> --purge --yes`, the work tree)
unless `--keep`. A `--check` failure after an intended extraction change means: re-freeze, and say why in
the commit.

## Live smoke test (operator runbook, `[NEO4J]`)
`tests/stage0_live_smoketest.py` runs the retriever end to end on a small synthetic graph (random
vectors) to prove the live execution path: the SEARCH clause or its procedure fallback, an empty arm,
the HNSW index options and elementId hydration. It says nothing about retrieval quality.

```bash
python tests/stage0_live_smoketest.py --project <throwaway dir>   # or --uri bolt://host:port
```

The target is never defaulted and its graph is wiped, so point it at a throwaway project or an empty
graph.

## Validate an external clone (operator runbook, `[NEO4J]`)
Give the clone its own DeepMap instance first (`python build/provision.py ensure <clone>`, or a full
`/deep-map:setup` there), then:

```bash
# 1) BUILD the harness graph for the clone (keys relative to the clone root -> portable; WIPES the target):
python tests/stage0_real_graph.py --project /clones/owner__repo --repo-dir /clones/owner__repo \
       --queries-file /path/to/owner__repo.queries.json
# 2) FREEZE + REGISTER its oracle (asserts determinism: same ordering twice) with its pinned corpus:
python tests/stage0_freeze_oracle.py --project /clones/owner__repo --repo-id owner__repo \
       --repo-dir /clones/owner__repo --queries-file /path/to/owner__repo.queries.json \
       --corpus-sha <40-hex> --corpus-paths . --builder stage0-ast --register
# 3) GATE any time against the registered oracle:
python tests/stage0_regression_check.py --repo-id owner__repo --project /clones/owner__repo
```

A `--repo-id` that isn't in the registry **fails loud** (never gates a phantom oracle). Queries load from a
`.json` array or a `.txt` (one per line, `#`-comments ignored), see `tests/stage0_portable.load_queries`.

## Gated (do NOT fabricate)
Freezing the benchmark repos' oracles + authoring their query/ground-truth sets is curator work gated on
live clones at a pinned SHA. Never fabricate a SHA or a query set; register an oracle only after
building it on a real clone.
