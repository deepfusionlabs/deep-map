# benchmark — Agent Context (nested; the root CLAUDE.md also applies)

The with-vs-without harness: 9 repos × 49 questions (`repos.yaml`, `questions.yaml`), each pinned clone built as its
own DeepMap project, two-stage blind scoring, multi-LLM arms, the learned-vs-equal weight A/B bridge.

## Critical to know (non-obvious, load-bearing)
- `run_g3_n4.sh` runs the signed-off G3 set (5 repos; a `REPOS=` override is labelled "NOT the G3 set") under run-ids
  tagged `RUN_TAG` (default `v<DeepMap version>`). Resume is provenance-checked (`run_benchmark.resume_state_for`: the
  capture's recorded DeepMap version + the clone's graph manifest); a capture that cannot be reused — or has no recorded
  provenance — STOPS the driver, never reused or overwritten: start a new `RUN_TAG`;
  an unfinished or unreadable capture is moved aside (`<run-id>.unfinished-<utc>`) and captured again.
  `run_breadth.sh` follows the same rule, with a default PREFIX per MODE (`breadth-loop` / `breadth`) and a
  capture-shape check (`breadth_shape.json`: runs, extra arms, family filter). — `benchmark/run_g3_n4.sh:102-103`, `:135-139`, `:145-161`;
  `benchmark/run_benchmark.py:2640-2660`; `benchmark/run_breadth.sh:20-35`
- Build clones only with `benchmark/clone_graph.py build <clone>` (a plain `deepmap_cli.py sync <clone>` is refused),
  then `clone_graph.py ports`, and pass `DEEPMAP_SEARCH_PORT` / `DEEPMAP_EMBED_PORT` per command — an exported pair
  overrides the next clone's setup. Exit 4 from `build` means setup kept the previous graph: never measure it.
  — `benchmark/clone_graph.py:13-19`, `:446-452`; `build/provision.py:2929`
- `do_run` fails closed before spending when the clone's HEAD ≠ pin or has tracked changes, the graph manifest's SHA ≠
  pin or is dirty/unknown, or the ON arm's MCP pair is not the clone's registered pair. — `benchmark/run_benchmark.py:1893-1910`
- `--clone-root` is the probe's repo dir, the root of the `<owner__name>` clones for `--run`, and for `--score` the same
  clones root: the cli judge reads each card's OWN pinned clone, refused unless pristine at the pin (both drivers pass
  it). — `benchmark/run_benchmark.py:1723`, `:1846`, `:2316-2321`, `:2432-2454`; `benchmark/run_g3_n4.sh:220-224`
- The blind judge is `effective_judge_model` — `DEEPMAP_JUDGE_MODEL` under `DEEPMAP_JUDGE_TRANSPORT=openrouter`
  (`--judge-model` is then unused) — the one value both the must-differ-from-the-arm guard and every card's
  `_judge_model` use; the guard also maps each `claude --model` selector (family aliases, `best`, `opusplan`,
  `default` or blank) to the families it can run. — `benchmark/run_benchmark.py:2166-2178`, `:2394-2402`
- Both arms run `--strict-mcp-config` (the ON arm loads only the clone's own servers) with every DeepMap hook off in
  the `claude -p` child, so neither arm sees an ambient MCP server or a heal/nudge. — `benchmark/run_benchmark.py:817-832`, `:885-893`
- `--ab-weights` / `--enrich-ab` / `--ab-rerank` / `--ab-graph-signal` are PLAN ONLY: they print a procedure and stop
  (no capture, no spend) unless an explicit mode is also given. The A/B evidence comes from `weight_ab` / `enrich_ab`
  called directly (`staged_learned_weights` → capture → `restore_learned_weights` → `build_g4_evidence`), reading each
  question's `answer_key.ground_truth_set`. — `benchmark/run_benchmark.py:2885-2920`; `benchmark/weight_ab.py:254`, `:276-283`, `:348`
- Spend is refused without `--confirm-spend` after the projected cost is printed; the blind judge also needs the
  real-engine flag. — `benchmark/run_benchmark.py:1417-1427`, `:2380`
