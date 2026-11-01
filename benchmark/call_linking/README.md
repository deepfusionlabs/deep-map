# Call-linking measurement kit

The tools that measure typed-receiver call linking (`DEEPMAP_TYPED_CALLS`), the untyped-receiver audit
(`DEEPMAP_CALLS_AUDIT`), family-scoped precise calls (`DEEPMAP_PRECISE_CALLS=<families>`) and the cross-family
filter (`DEEPMAP_CALLS_FAMILY_FILTER`) against the pre-registered decisions in [PREREG.md](PREREG.md).
Measurement code only: nothing under `build/` imports it, and the reference resolvers it drives (jedi,
TypeScript) are in no shipped list (`tests/call_linking_harness_gates.py` checks both).

**Read PREREG.md first.** Its §8 is the protocol, copied verbatim and hash-locked: thresholds are never edited
after data, and RESULTS.md / DECISION.md (slice S6, in this folder) are written once, against it. (S0 to S6
name the internal work slices; PREREG.md and PRE_RC_DECISIONS.md stay verbatim as registered.) Changes made
before the first RC run are dated amendments with their reasons ("Amendments before RC", just above §8). A decision that near-RC pilot data force without changing a criterion takes the
DECISION.md route instead: it is recorded before the first RC run in [PRE_RC_DECISIONS.md](PRE_RC_DECISIONS.md)
(PD-1: two engine-specific zod typed tuples accepted as true edges, a list, not a rule) and copied verbatim into
DECISION.md.

## Layers and tools

| Layer (PREREG §8.4) | Tool | What it does |
|---|---|---|
| G, graph | `offline_calls.py build <corpus> <out.json>` | The exact `:CALLS` set `deepmap sync` would push, with no Neo4j. Mirrors `build/sync.py` `_sync_pass` step for step (the typed-call step only when sync has it); deterministic output (run twice, `cmp`): the dump holds graph content and run identity only, never a wall-clock value (`WALL_CLOCK_FIELDS`: the typed pass's `call_linking.seconds` is left out), and every timing goes to `<out.json>.timing.json` (`t_extract_s`, `t_typed_s`, `t_total_s`, and `call_linking_seconds`, the typed pass's own seconds as `build/typed_calls.py` measures them). Records the flags, the *effective* switches the code turned on (read through the product: precise / keyed / CALLS_PLUS, the cross-family filter, the typed and audit languages, the typed-calls shapes / replace / tests / non-production / max files), the engine, a sha256 of `build/*.py` and `counts.extracted_cross_file`. Exit 3 = VOID (an engine fallback, or an inert variant: `variant_void`), exit 4 = INVARIANT (an EXTRACTED edge across files: data, and the variant fails). `stage0-corpus <dest>` materializes the registered STAGE-0 corpus. |
| G | `variants.py` | The §8.3 variant table as data (`list`, `env`, `run <id> -- cmd`, `slug`). Every variant runs in a clean environment: every `DEEPMAP_*` removed except `DEEPMAP_HOME` / `DEEPMAP_PYTHON` / `DEEPMAP_GRAPHIFY_TIMEOUT`, `NEO4J_URI` removed. |
| G, truth | `truth_jedi.py` (jedi 0.20.1, isolated venv, `-I`), `truth_ts.js` (TypeScript 5.6.3; `--js` for JavaScript) | One row per call site with the in-corpus definitions the reference resolver binds. `truth_jedi` reads a file as the interpreter does (BOM, PEP 263 cookie), hands jedi CHARACTER columns, and names every file that does not parse (`parse_errors`). |
| G | `compare_calls.py` | Recall, confirmed / refuted / unverified, per grade and per call form; `--src-prefix` for library code (a pair belongs to the prefix by its CALLER's file); `pool` for the pooled receiver-form recall of D3-G1 (one dump's score per file, one variant on one engine: a several-dump `--json`, a non-score or mixed variants / engines are refused, exit 2; a VOID score exits 3); the cross-file EXTRACTED count; exit 3 on a VOID dump. |
| G | `delta.py` | added / removed / demoted / promoted / EXTRACTED (every EXTRACTED edge of the variant, kept ones included) between a base and a variant, by caller class (prod / test / nonprod) and verdict. VOID (exit 3): a differing node set, a VOID dump on either side, or a variant whose flags differ from the base's while its effective switches are identical. |
| G | `indegree_drift.py` | The arm-3 multiplier drift (count, distribution, max, top-20, the 2x candidates) and, with two live captures, the drift void rule. |
| E, parity | `engdiff.py` | 0.8.39 vs 0.8.51 on version-stable identities (`label:name@file:line`). The E criterion against V0's parity is decided on the edges whose BOTH endpoints exist on both engines (PREREG amendment A1: graphify 0.8.39 makes no node for some TS static arrow-field members); the raw symmetric difference, the nodes on one engine only and the raw engine-specific count are reported beside it. Engine-specific changes; the label-set projection (diagnostic only); VOID (exit 3) on a VOID dump or a variant whose node set differs from its base's. `typed_tuple_verdict` judges §8.4 E's first bullet for one corpus from the differing typed tuples (which the engine-parity gate computes): as registered, and with the tuples PRE_RC_DECISIONS.md accepted (exactly those, on that engine and grade). |
| G | `impact_offline.py` | Oracle-seeded 3-hop reverse reachability for the four multi_hop questions, STRICT / SPEC atoms (`--case-truth` gives the resolver ceiling). |
| G | `impact_spot.py` | Hop-1 recall / precision on the registered spot symbols (5 Python, 5 TS/JS, each on a pinned corpus; `read_records` on the STAGE-0 corpus replaced the saturated `_mk_project`, PREREG amendment A2); `--base` counts improvements and flags a spot SATURATED at the base (recall already 1, or no reference caller: it can never improve; exit 1). |
| A, audit | `audit_sample.py sample` / `score` | Seeded blind sample per bucket (+ every resolver-confirmed removal), the reviewer sheets, Wilson 95% bounds, the resolver error rate (>= 10% voids that corpus's G deltas). `score` reports the top-level LOSSES (every removed row of either stratum audited TRUE, by id) and the correct-removal share over both removal strata (population-weighted, `removed_all`). |
| R, retrieval | `run_variant.py capture` / `roundtrip` / `matched` | Live in-process capture on a throwaway instance (search top-15 + impact(30) on graph-family top-3), two passes; the exact round-trip and matched-state VOID checks. The owner stamp is read with sync's own `_read_owning_root` and judged by `owner_problem` (sync's predicate). |
| R | `score_m.py score` / `r1` | The STRICT / SPEC atom scorer (Python spans from `ast`, TS/JS spans from tree-sitter, `$` symbols such as `$ZodError`); R1 (no atom lost, macro >= V0) and R3 (multi_hop impact STRICT >= V0). |
| R | `measure_self.py` + `gt_self65.json` | The self 65-query set (recovered with provenance): capture, hit@1/5/15 + MRR, R2 compare, and `check` (which expected symbols still exist). `compare`'s `mrr_ok` / `r2_holds` read the 4-decimal MRRs (the closed decisions D3 and D1-py, kept byte for byte); beside them the exact fields from the integer ranks (`mrr_base_fraction` / `mrr_variant_fraction`, `mrr_*_exact`, `min_gain_fraction` read from --min-gain's decimal string, `mrr_ok_exact`, `r2_holds_exact`), which D3-cs reads (PREREG_CS §8.7 R, AC23, AC29, §8.10); the CLI prints both lines, and `--exact` makes its exit code follow `r2_holds_exact`. |
| S, STAGE-0 | `tests/stage0_refreeze.py --check --env KEY=VAL` | The committed oracles against a variant's graph (DEEPMAP_* keys only; `--freeze` refuses `--env`). |
| all G/E | `run_matrix.sh` | corpus x engine x variant orchestration (`CL_DRY=1` prints the plan). Measures a snapshot of `build/*.py` (`CL_WORK/build_snapshot`) unless `CL_BUILD` names one; VOID runs go to `VOID.txt`, invariant failures to `INVARIANT.txt`. |

## D3-cs: C# typed linking ([PREREG_CS.md](PREREG_CS.md))

A second registration, apart from PREREG.md: whether `DEEPMAP_TYPED_CALLS=csharp` joins the default graph. Its
protocol block is hash-locked like PREREG §8; the kit keeps every C# table APART from the ones harness gate H reads
for PREREG.md, and a jedi / tsc truth is scored exactly as before (the closed decisions recompute identically).

| What (PREREG_CS) | Where |
|---|---|
| The reference (§8.2): `truth_roslyn.py` + `truth_roslyn/` | a DISPATCH truth: the `call-linking/truth/1` row plus a per-row `dispatch` list, entries of exactly the `defs` shape (`_common.has_dispatch`) |
| T_s / T_d and the mapping (§8.5) | `_common.truth_pairs_cs` (T_s = each row's `defs`, exactly `truth_pairs`' T; T_d = each row's `dispatch` minus T_s; the same GraphLocator: the innermost defstack entry that has a node, else the file's Module; a def maps to the node on its line or an alt line) and `classify_pair_cs` (confirmed / dispatch-confirmed / refuted / unverified; AC6: a site is resolved when it is bound, statically or by dispatch, to a definition that maps to a graph node, so a caller whose sites of that name bind only outside the corpus is unverified, never refuted); `RECEIVER_FORMS["cs"]` = name_attr + recv + this; `caller_class` reads a `.cs` path by the product's rule only (`sync._is_test_path`, its .NET test-project directory suffixes included) |
| Row fields (AC5) | every row's `dispatch` (a list), a row with dispatch targets' `dispatch_rule` (`interface` / `override`), every def's `interface`: `_common._static_iface` (G4c: the row's `dispatch_rule` when it has dispatch targets, else the defs' `interface`, so an external static target such as IDisposable.Dispose still answers) and `_common.static_dispatchable` (AC9's fallback: either dispatch rule, else the defs' `interface`) |
| G scores | `compare_calls.py` on a dispatch truth: the §8.5 classification, recall over T_s (overall, per form), dispatch recall over T_d, the AMBIGUOUS and EXTRACTED-refuted shares, G4c (`g4c`, through AC5's fields; `exact` False only for a producing row that names no dispatch rule; beside it, never decisive, `extracted_dispatch_iface_any_site`: AC24's count over ANY same-named site of the caller), the static-only classification beside it (`static_only`, never decisive); `pool --family=cs` (dispatch-truth scores only, all whole-graph or all `--src-prefix`) |
| Deltas | `delta.py`: the verdict covers dispatch-confirmed, `verdict_static` beside it, and a `cs` block with G2d (added edges from a non-test caller to a node in a test file) |
| Spots (§8.3) | `impact_spot.py` `CS_SPOTS` / `CS_RESERVES` (ids J1..E5, J-r1..E-r3), reference T_s ∪ T_d, the exactly-one-node check; `substitute --base39 --base51` applies the substitution rule and writes the final list; AC12: `--corpus=<id> --final=<id>/spots_final.json` measures exactly that FINAL list (a C# `--corpus` without `--final`, with or without an ad-hoc `--symbols` list, a final list of another corpus or one `substitute` cannot have written is refused, exit 2) and prints the G1c count; AC29: `improved` and SATURATED compare the integers `hit` / `ref` (`improved`: exact fractions, a spot without reference callers reads 0), the 4-decimal `recall` is display only |
| Audit (§8.6 A) | `audit_sample.py`: `CS_SHEET_SENTENCE` (§8.6 A's instruction, then the sentence AC25 adds, verbatim) on a C# sheet, the call-site shapes `M<...>(` and `?.M(`, dispatch-confirmed removals in the exhaustive stratum (AC10), `removed_all_exact` beside the unchanged `removed_all`, `exact` (per-bucket bounds, a census at its exact share), `EXTRACTED_split` (G4b), `pool` (population-weighted over corpora, Wilson on the summed rows beside it); AC29: `exact`, `removed_all_exact` and `pool` recompute every share and lower bound from the integers (good, audited, population, sampled) at full precision — `share_fraction`, `share_exact`, `lower_bound_exact`, and `lower_bound_fraction` when every stratum is a census — and the resolver's `voids_g_deltas` is wrong × 10 ≥ judged; every 4-decimal field is display only; the dispatch stratum (AC9, G3b) = `sample --base=<VT-cs-ifs>` (implementer AND override edges), `--impl-fallback` only without `DEEPMAP_TYPED_CALLS_IFACE` (interface, virtual, abstract or override static targets); `refrate --dir=... --sheets=...` (AC11: the reference error rate per corpus × engine over the union of the main and dispatch-stratum samples, a pair once; unreliable when either engine is at 10% or more; AC26: a pair agreed in no sample is outside the rate, one agreed in one sample is judged by it, one agreed TRUE in one and FALSE in another counts once, as wrong) |
| Variants (§8.4) | `variants.py` `CS_VARIANTS` (`list-cs`): V0, VT-cs, VT-cs[shape], VT-cs+t, VT-cs+np, VT-cs-ifs (`DEEPMAP_TYPED_CALLS_IFACE=static`: the statically bound member only, interface or virtual), VT-cs-if0 (`=0`: neither dispatch rule), VRC-cs (`--set`; AC16: `variants.py vrc-cs` prints `DEEPMAP_TYPED_CALLS=<every language in the RC tree's _TYPED_DEFAULT_LANGS>,csharp`, and `env` / `run VRC-cs` refuse a value that leaves one out) |
| Dumps | `offline_calls.py`: `effective.typed_iface` (the interface-dispatch modes, through `typed_calls._config`), VOID when a requested `DEEPMAP_TYPED_CALLS_IFACE` was not turned on exactly, the C# VOID rule (`csharp_void`: csharp disabled for any reason, or no C# file read, on a corpus holding `.cs` nodes); `identity` (N1 / N2) |
| RC identity (§1, AC7) | `offline_calls.py codehash --build <snapshot>/build` (build/*.py); `offline_calls.py kithash --kit <snapshot>/benchmark/call_linking [--truth <truth.json>]`: the kit hash (every kit file, `__pycache__/`, `truth_roslyn/bin/`, `truth_roslyn/obj/`, RESULTS_CS.md, DECISION_CS.md and `cs/` excluded) and the reference tool identity (checked equal to `truth_roslyn.py identity`), plus the truth's resolver versions; `--build ... --record <file>` records / checks the three together (run_matrix.sh's `out/rc_identity.json`) |
| G5b manifest (AC15) | `offline_calls.py manifest <repo>/benchmark/call_linking/cs [--write]`: `cs/.gitattributes` (`* -text`) and `cs/MANIFEST.sha256` (`<sha256>  <path relative to cs/>`, byte order, LF, never itself or a sheet.md); a dry run without `--write`; refuses a sheet.md, a dump or truth file, a symbolic link, an existing different manifest; prints the line DECISION_CS.md records |
| Orchestration | `run_matrix.sh` with `CL_MATRIX=cs CL_PHASE=self-snapshot \| truth \| v0 \| spots \| rest \| audit \| gates \| manifest` (`cs_corpus_row`, `CS_C_CORPORA`, `CS_N_CORPORA`): §8.1's order, each phase refusing to start before its inputs exist; every phase but `self-snapshot` runs FROM the snapshot (AC7) and checks its identity against `out/rc_identity.json`; `rest` ends with the engine-parity gate exactly as AC13 names it, its full output (stdout and stderr) in `out/engine_parity_cs.log` (`run_log`: written as `.partial`, the exit code appended, renamed when the command ends); `gates` runs G5a's gates (`CS_G5A_GATES`) under both engines' Pythons and the no-Neo4j suite on 0.8.39, logs under `out/gates/`; G5a's Linux-container run of the suite (by hand) copies `$CL_SELF_RC`, the RC snapshot, never this repository (AC29) |

```bash
W=<scratch>   # a CL_WORK of its own, outside this repository; CL_DMHOME short (MAX_PATH)
export CL_WORK=$W CL_DMHOME=<short dir> CL_PY51=C:/tmp/cl6-842925/prep/v51/Scripts/python.exe CL_MATRIX=cs \
       CL_SELF_RC=$W/self_rc
CL_PHASE=self-snapshot bash benchmark/call_linking/run_matrix.sh     # ONCE, from this working tree, at the RC suite run
RM=$CL_SELF_RC/benchmark/call_linking/run_matrix.sh                  # AC7: every other phase runs the snapshot's copy
python $CL_SELF_RC/benchmark/call_linking/offline_calls.py codehash --build $CL_SELF_RC/build      # §1's slots
python $CL_SELF_RC/benchmark/call_linking/offline_calls.py kithash --kit $CL_SELF_RC/benchmark/call_linking
CL_PHASE=truth CL_ROSLYN_ARGS="<registered options>" bash $RM   # record, then:
CL_PHASE=v0    bash $RM                                         # record, then:
CL_PHASE=spots bash $RM                                         # record the final list, then:
CL_PHASE=rest  bash $RM                                         # ... and the engine-parity gate (AC13), then the review:
CL_PHASE=audit bash $RM                                         # the reviewers' sheet_r1.csv / sheet_r2.csv in each audit dir
CL_PHASE=gates bash $RM                                         # G5a from the snapshot (the Linux-container suite: by hand,
                                                                # on a copy of $CL_SELF_RC, never this repository: AC29)
CL_PHASE=manifest CL_CS_DIR=<this repo>/benchmark/call_linking/cs bash $RM   # G5b, once the files are placed there
```

**AC29: where RESULTS_CS reads its numbers.** C1 to C4 of the engine-parity gate come from its full log,
`out/engine_parity_cs.log` (stdout and stderr; harness gates J and O lock the path and the redirect). G5a's
Linux-container run copies `$CL_SELF_RC`, the RC snapshot, never this repository. Every threshold reads the
integers the kit's outputs carry, never a 4-decimal display: `measure_self.compare`'s `r2_holds_exact` (R2 on
`self`, AC23, and in VRC-cs), `audit_sample`'s `exact` / `removed_all_exact` / `pool` exact fields and the
resolver's wrong x 10 >= judged, `impact_spot`'s `hit` / `ref`. One 4-decimal comparison stays, on the record:
`indegree_drift.py`'s 2x candidate test reads the ratio rounded to 4 decimals. That is the strict direction
(rounding can only add a candidate), the closed D3 drift rule shares it, and for D3-cs the void rule it feeds
cannot fire (PREREG_CS §8.7: the C# corpora have no questions, and the question repositories are judged
through N1).

The live layers run from the same snapshot (AC7), never from this working tree: S is
`python $CL_SELF_RC/tests/stage0_refreeze.py --work <fresh> --check --env DEEPMAP_TYPED_CALLS=csharp` on each engine's
Python; P is `<engine python> $CL_SELF_RC/benchmark/clone_graph.py build <clone>` (its `BUILD` is the snapshot's
build/), then the six alternating `<engine python> $CL_SELF_RC/build/deepmap_cli.py sync <clone> --stale-cleanup`
re-syncs of §8.6 P under each arm's `variants.py run <arm> --` environment; R (VRC-cs only) is
`$CL_SELF_RC/benchmark/call_linking/run_variant.py` / `measure_self.py`, with
`variants.py run VRC-cs --set $(python $CL_SELF_RC/benchmark/call_linking/variants.py vrc-cs) -- ...` (AC16); R2 there,
and on `self` when D3-cs measures it live (AC23), is read from `measure_self.py compare --exact` (`r2_holds_exact`,
AC29).

## Environment (PREREG §8.2)

```bash
W=<scratch>                                    # never inside this repository
uv venv $W/venv-jedi && uv pip install --python $W/venv-jedi/Scripts/python.exe "jedi==0.20.1" "parso==0.8.7"
uv venv $W/venv51   && uv pip install --python $W/venv51/Scripts/python.exe "graphifyy==0.8.51"
npm install --prefix $W/tsenv --save-exact typescript@5.6.3
CL_WORK=$W CL_DMHOME=<a short dir> CL_PY51=$W/venv51/Scripts/python.exe CL_JEDI_PY=$W/venv-jedi/Scripts/python.exe \
  CL_TSENV=$W/tsenv CL_CORPORA="psf__requests pallets__flask colinhacks__zod" CL_VARIANTS="V0" \
  bash benchmark/call_linking/run_matrix.sh
```

Rules the tools enforce: `DEEPMAP_HOME` must be a scratch home (never `~/.deepmap`); the read-only benchmark
clones (`CALL_LINKING_READONLY_ROOTS`, default `C:/tmp/deepmap-bench-clones`) are copied with
`git clone --no-hardlinks` before anything runs on them; a live capture refuses `NEO4J_URI`, the base bolt 7687
and the dev pairs, and checks the graph's owner stamp.

## Traps

- **Determinism is about graph content; a timing is P-layer data** (decided 2026-10-24).
  `typed_calls.link`'s stats carry the pass's own wall time `seconds` ("the only non-deterministic value"). Until
  2026-10-24 `make_dump` copied the stats verbatim into the dump's `call_linking` block, so the two runs of almost
  every typed variant differed in that one value and `run_matrix.sh`'s `cmp` called the pair VOID. The dump now
  leaves it out (`offline_calls.WALL_CLOCK_FIELDS`), and the timing sidecar `<dump>.timing.json` carries it as
  `call_linking_seconds`. P-layer readers take the pass's seconds from the sidecar (or from the sync log), never from
  a dump. A dump written before the fix is re-judged with `offline_calls.strip_wall_clock`, which removes exactly
  those fields; `_common.dump_json` of the result is the byte string the fixed kit writes. Every other difference
  between two runs is still VOID. `tests/call_linking_harness_gates.py` section C locks this: no wall-clock key in
  a typed dump, two in-process typed runs under two different fake clocks give identical bytes, and the CLI's two
  typed runs are byte-identical with the seconds in the sidecar.
- **A silent engine fallback is not data.** The default extractor `auto` falls back to the Python-only ast engine
  when graphify fails, and on Windows it fails when a path under `<DEEPMAP_HOME>/gout/` passes MAX_PATH (measured:
  the engine's cache paths ran 106 characters past a 142-character home; a 146-character home failed on requests).
  `offline_calls.py --expect-engine graphify` (which `run_matrix.sh` always passes) makes such a dump VOID (exit 3);
  every dump records `extraction` (requested / resolved / fallback_reason). Keep `CL_DMHOME` short.
- **An inert variant equals V0.** A flag the code under test does not understand is silently off (before slice
  S1, `DEEPMAP_PRECISE_CALLS=python,js` is OFF; before slice S2, sync has no typed-calls step). This is enforced in
  code, not left to the reader: `offline_calls.py` VOIDs a dump (exit 3) whose flag no `build/*.py` file names,
  whose `DEEPMAP_TYPED_CALLS` / `DEEPMAP_CALLS_AUDIT` met no `_typed_calls_link` step, a step without stats or a
  step that erred (`typed_calls.link` then returns its input unchanged), whose requested languages the product did
  not turn on, whose `DEEPMAP_PRECISE_CALLS` family list turned on no family, or whose requested switch the product
  left off (the cross-family filter, TESTS / NONPROD, CALLS_PLUS, REPLACE=0, a SHAPES arm); `delta.py` VOIDs a pair
  whose flags differ while the `effective` blocks are identical. The `effective` block reports every switch a
  §8.3 variant sets, so VF against V0 and an ablation against VT are measurements, not inert pairs (before
  2026-10-24 it did not report the filter or the typed-calls configuration, and every VF delta was VOID).
  `run_matrix.sh` records each in `VOID.txt`.
- **The working tree moves.** Other work edits `build/` while a matrix runs; two runs of one dump on different
  code are "not byte-identical" and VOID. `run_matrix.sh` therefore measures a snapshot (`CL_WORK/build_snapshot`,
  or `CL_BUILD`), and every dump stamps the code hash it measured.
- **EXTRACTED never crosses files.** Design §4 allows EXTRACTED only with the whole evidence chain in the caller's
  file; `counts.extracted_cross_file` checks it exactly on every dump (exit 4, `INVARIANT.txt`).
- **The STAGE-0 corpus is build/'s CONTENTS.** `stage0_build` is the registered `self-graphify-build` corpus,
  materialized by `offline_calls.py stage0-corpus` exactly as `tests/stage0_refreeze.py` does (`git archive
  <sha>:build`): graphify skips a directory named `build/`, and a non-git tree gets no skipped-dir rescue.
- **Node sets must match.** Every §8.3 variant is edge-only; `delta.py` / `audit_sample.py` refuse a pair whose
  node sets differ. Compare engines only through `engdiff.py` (node keys differ by engine).
- **Git Bash and Windows Python.** Pass paths as separate arguments or as `--opt=C:/...`; a comma-joined path
  list is rewritten by MSYS before Python sees it (`impact_offline.py --case REPO ROOT DUMP`).
- **The `self` corpus is the working tree** (there is no RC commit): `run_matrix.sh` clones the repository into the
  scratch dir, copies the working tree over it and runs `git add -A` in that throwaway clone only (the engine's
  skipped-dir rescue reads `git ls-files --cached`). The C# matrix never does that mid-run: its `self` is copied from
  `CL_SELF_RC`, one snapshot made the same way at the RC suite run (`CL_PHASE=self-snapshot`; AC7: the working tree's
  deleted tracked files are removed from the clone first, and the snapshot's index must list exactly the working
  tree's files), read-only to every dump; `self` is its `.git` plus exactly the files its index lists. Every other C#
  phase runs the snapshot's own `run_matrix.sh` on the snapshot's `build/` (it refuses any other copy).
- **A dispatch truth is not a jedi / tsc truth.** `compare_calls`, `delta`, `impact_spot` and `audit_sample` switch to
  PREREG_CS §8.5's rules only when the truth's rows carry `dispatch` (`_common.has_dispatch`); the decisive fields of a
  C# score are its top-level ones, and `static_only` (dispatch ignored) is reported beside them, never decisive.
- **Pilot numbers are pilot data.** PREREG §7 holds the S0 V0 baseline that proved the kit; decisions use only
  the RC runs of S6.
