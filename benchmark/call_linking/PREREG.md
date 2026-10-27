# Call linking — pre-registration (PREREG)

Registered 2026-10-23 by slice S0, before any release-candidate (RC) data exists. It records the measurement
protocol (§8 of the typed-receiver call-linking design, copied verbatim below and hash-locked by
`tests/call_linking_harness_gates.py`), the RC code identity, the engine builds, the corpora with their pinned
SHAs and the spot symbols. Rules: thresholds are never edited after data; RESULTS.md and DECISION.md are
written once, against this file; pilot numbers are labelled PILOT and are never decision data. Any change to
this file after the first RC run is recorded in DECISION.md with its reason. A change made BEFORE the first RC
run is an amendment: it is dated, gives its reason and is recorded in "Amendments before RC" (just above §8);
§8 itself stays verbatim, and an amendment governs the §8 items it names.

## 1. Release-candidate code identity

- **RC code SHA:** the working tree as of the S5 suite run (no commit: the operator commits).
- **Content hash of build/\*.py:** `304c3f11bfcff6d128d391f6a6557fa62bbdfd10aab9f6475e6cbc471ddb404e` (43 files;
  filled by S6 on 2026-10-24, before the first RC run) — the output of
  `python benchmark/call_linking/offline_calls.py codehash` on the S5 tree (sha256 over `build/*.py` in name
  order, each file contributing its name, a NUL, its bytes and a NUL; plus the file count). Every RC dump
  stamps the same hash in its `code` field; a dump with another hash is not RC data.
- The tools of this kit as of that tree measure it; a tool change after the first RC run voids the runs it touches.

## 2. Engine builds and reference resolvers

| Role | Build |
|---|---|
| Engine, dev end | graphifyy 0.8.39 on the dev Python 3.13.14 (tree-sitter 0.25.2, tree-sitter-python 0.25.0, tree-sitter-typescript 0.23.2, tree-sitter-javascript 0.25.0, networkx 3.6.1) |
| Engine, CI end | graphifyy 0.8.51 in a scratch `uv venv`, Python 3.13.14 (tree-sitter 0.25.2, tree-sitter-python 0.25.0, tree-sitter-typescript 0.23.2, tree-sitter-javascript 0.25.0, networkx 3.7) |
| Python reference | jedi 0.20.1 + parso 0.8.7 in an isolated `uv venv` (Python 3.13), run with `-I` (`truth_jedi.py`) |
| TS/JS reference | TypeScript 5.6.3 (`npm install --save-exact`), Node v22.16.0 (`truth_ts.js`; `--js` = allowJs + checkJs) |
| Environment | `PYTHONUTF8=1`, `PYTHONHASHSEED=0`; `DEEPMAP_GRAPHIFY_TIMEOUT=3600` for this repository |

The two engine ends are the ends of the pinned range `graphifyy>=0.8.39,<0.8.52`; any other build is not RC data.
A G-layer dump whose extraction did not resolve to graphify (the default `auto` falls back to ast when graphify
fails, e.g. a path over Windows MAX_PATH under DEEPMAP_HOME) is VOID: every RC dump runs with
`offline_calls.py --expect-engine graphify`. `offline_calls.py` also VOIDs (exit 3) a dump whose variant was inert on
the code under test (§5), and exits 4 (INVARIANT) for a dump holding an EXTRACTED edge across files (design §4: the
variant fails). `run_matrix.sh` measures one snapshot of `build/*.py` per matrix (`CL_WORK/build_snapshot`, or
`CL_BUILD`): the working tree moves while other work lands, and every dump stamps the code hash it measured.

## 3. Corpora (pinned)

| Corpus | Pinned SHA | Library prefix (D3-G1) | Truth |
|---|---|---|---|
| psf/requests | 23953c0c875219a715f081cf3de7c149a7629ccf | src/requests/ | jedi |
| pallets/flask | 36e4a824f340fdee7ed50937ba8e7f6bc7d17f81 | src/flask/ | jedi |
| pallets/click | 679a7a0eccbdded7a6e85680bdaaf08003765e01 | src/click/ | jedi |
| pallets/werkzeug | 1b00618e787f40dfb21eba29caf8f8be7c8e1d93 | src/werkzeug/ | jedi |
| fastapi/fastapi | 702fea83bb3ef0b558104835d7936af49650fb2d | fastapi/ | jedi |
| this repository | the RC (§1) | build/ | jedi |
| STAGE-0 build/ corpus | 9c8d50bceab96288f83877c5306167b54b6d918a | the whole corpus (build/'s contents at the root) | jedi |
| colinhacks/zod | 912f0f51b0ced654d0069741e7160834dca742ee | packages/zod/src/ | tsc (`--conditions=@zod/source`) |
| **TS application:** lujakob/nestjs-realworld-example-app | c1c2cc4e448b279ff083272df1ac50d20c3304fa (committed 2021-01-18) | src/ | tsc |
| **JS repository:** hagopj13/node-express-boilerplate | 179ae84efec61b14206d0305d941daed6c6d07f9 (committed 2023-08-12) | src/ | tsc `--js` |
| smoke only: gin-gonic/gin | 34dac209ffb6ef85cc78c5d217bbb7ad001d68fd | — | none |
| smoke only: julienschmidt/httprouter | 484018016424d215c0b87c42f4c9b57d980fbd00 | — | none |
| smoke only: serde-rs/json | 827a315bf2198558f0325b07bcc1e2cd973aba2f | — | none |

The STAGE-0 corpus is the REGISTERED `self-graphify-build` corpus (tests/oracles/registry.json: `tree: build`),
materialized by `offline_calls.py stage0-corpus` with tests/stage0_portable.py's own `materialize_corpus` — the
function tests/stage0_refreeze.py runs: `git archive <sha>:build`, build/'s 34 files at the root of a non-git tree,
keyed as the oracle keys them. (S0's first cut extracted `<sha> build` into a `build/` directory: graphify skips
`build/` as a noise dir and a non-git tree gets no skipped-dir rescue, so every dump fell back to ast and was VOID.)

The TS application and the JS repository are the two named in CHANGELOG.md:200-205 (the TypeScript / JavaScript
recognizer was measured on them on both engine ends); their SHAs are the upstream default-branch heads on
2026-10-23. `benchmark/call_linking/run_matrix.sh` holds the same table (the harness gate checks they agree).

## 4. Spot symbols (hop-1 impact, §8.4 G / D3-G1)

The five TS/JS spot symbols, chosen by structure (a hub reached through a receiver shape typed linking targets)
before any typed-linking output existed for these corpora, under one rule: **every spot's V0 hop-1 recall is below 1
on both engines** (a spot V0 already saturates can never "improve", so D3-G1's "at least 3 of 5" would silently be a
count over fewer spots), preferring the most reference callers within the shape. The counts are the S0 pilot's V0
(reference = TypeScript 5.6.3; hits = reference callers V0 also links); the harness gate checks every TS/JS row is
unsaturated, and `impact_spot.py --base` flags a saturated spot at RC time (exit 1).

| # | Symbol | Corpus | Definition | Shape it exercises | Reference hop-1 callers | V0 hits (0.8.39) | V0 hits (0.8.51) |
|---|---|---|---|---|---|---|---|
| 1 | `ZodString._addCheck` | zod | packages/zod/src/v3/types.ts:1050 | S — `this._addCheck({...})` from ZodString's own check methods: the in-file last-definition bind of design §1 G-b (`ZodNumber.multipleOf` -> `ZodDate._addCheck`) | 24 | 0 | 0 |
| 2 | `ParseStatus.mergeObjectSync` | zod | packages/zod/src/v3/helpers/parseUtil.ts:128 | K — static `ParseStatus.mergeObjectSync(...)`, cross-file | 3 | 1 | 1 |
| 3 | `ArticleService.findAll` | nestjs-realworld | src/article/article.service.ts:26 | R3 — `this.articleService.findAll(...)` through a constructor parameter property; `findAll` is defined in six classes (every R3 service method of this app has one caller: a binary spot) | 1 | 0 | 0 |
| 4 | `UserService.generateJWT` | nestjs-realworld | src/user/user.service.ts:103 | R3, cross-file — `this.userService.generateJWT(...)` in src/user/user.controller.ts, beside the in-class `this.generateJWT(...)` (S) V0 already links | 2 | 1 | 1 |
| 5 | `getUserById` | node-express-boilerplate | src/services/user.service.js:36 | M — `userService.getUserById(...)` through the `require('../services')` barrel, beside two in-file bare calls | 6 | 2 | 2 |

Replaced in the S0 review round, before any RC data: `ZodType._processInputParams` (V0 17/17 on both engines —
saturated) by `ZodString._addCheck` (V0 0/24); `UserService.findById` (one reference caller) by
`UserService.generateJWT` (two).

The Python spot symbols are §8.4's (design-given; the reference is jedi 0.20.1), with `_mk_project` replaced
before RC by `read_records` (amendment A2); the TS/JS rule above holds for all five (gate-locked):

| Symbol | Corpus | Definition | Reference hop-1 callers | V0 hits (0.8.39) | V0 hits (0.8.51) | Note |
|---|---|---|---|---|---|---|
| `MapAdapter.match` | werkzeug | src/werkzeug/routing/map.py | 52 | 3 | 3 | V0 recall < 1 on both engines |
| `Map.bind` | werkzeug | src/werkzeug/routing/map.py | 61 | 1 | 1 | V0 recall < 1 on both engines |
| `run_wsgi_app` | werkzeug | src/werkzeug/test.py | 15 | 3 | 3 | V0 recall < 1 on both engines |
| `Depends` | fastapi | fastapi/param_functions.py | 275 | 16 | 16 | V0 recall < 1 on both engines |
| `read_records` | STAGE-0 build/ corpus (9c8d50bc) | retrieval_log.py:314 | 8 | 0 | 0 | M — `_rl.read_records(root)` through a module-level `import retrieval_log as _rl`, from 8 production callers in 6 modules; V0 recall < 1 on both engines; amendment A2 |

Replaced before RC (amendment A2, 2026-10-23): `_mk_project` (this repository, tests/: 8 same-named module-level
defs; SATURATED at V0 — every one of its 108 V0 callers already had the edge to its own file's def, inside an
8-way AMBIGUOUS fan-out of 864 edges) by `read_records`.

`impact_spot.py` registers both lists (gate-locked).

## 5. Variants

`benchmark/call_linking/variants.py` spells every §8.3 variant (`python variants.py list`); the harness gate
locks it to the §8.3 table below. Ids with characters a file name should not carry have a slug (VT+t -> VTpt,
VT[R1] -> VT_R1). VRC has no fixed flags: DECISION.md names the combination and it runs with `--set`.
A variant whose dumps' `effective` block equals V0's ran on code that ignored its flags: it is VOID. This is
enforced in code, never left to the reader: `offline_calls.py` `variant_void` VOIDs a dump (exit 3, `void` recorded,
`run_matrix.sh` writes VOID.txt) whose flag no `build/*.py` file names; whose `DEEPMAP_TYPED_CALLS` /
`DEEPMAP_CALLS_AUDIT` met no `_typed_calls_link` step, a step that returned no stats or a step whose stats carry an
`error` (typed_calls.link then returns its input unchanged); whose requested languages the product's predicates did
not turn on; whose `DEEPMAP_PRECISE_CALLS` family list turned on no family (`effective.precise_families` empty); or
whose requested switch the product's own predicate left off (`DEEPMAP_CALLS_FAMILY_FILTER`,
`DEEPMAP_TYPED_CALLS_TESTS`, `DEEPMAP_TYPED_CALLS_NONPROD`, `DEEPMAP_GRAPHIFY_CALLS_PLUS` or `DEEPMAP_PRECISE_CALLS`
on while its switch is not; `DEEPMAP_TYPED_CALLS_REPLACE=0` while the product still replaces; a
`DEEPMAP_TYPED_CALLS_SHAPES` shape it did not turn on). The `effective` block reports, read through the product,
every switch a §8.3 variant sets: the precise, keyed and CALLS_PLUS switches, the cross-family filter, the typed and
audit languages and the typed-calls configuration (shapes, replace, tests, non-production, max files). So every two
variants with different flags have different blocks (gate-locked), and an ablation compared against VT is never an
inert pair. `delta.py` (and so `audit_sample.py`) VOIDs a pair holding a VOID dump, or whose flags differ while the
`effective` blocks are identical.

## 6. Tools per layer

G: `offline_calls.py`, `truth_jedi.py`, `truth_ts.js`, `compare_calls.py`, `delta.py`, `indegree_drift.py`,
`impact_offline.py`, `impact_spot.py`, `run_matrix.sh` · E: `engdiff.py` · A: `audit_sample.py` ·
R: `run_variant.py`, `score_m.py`, `measure_self.py` + `gt_self65.json` · S: `tests/stage0_refreeze.py --env` ·
P: the sync log (and `<dump>.timing.json` offline). See README.md.

## 7. PILOT — the S0 V0 baseline (kit validation; NOT decision data)

Measured 2026-10-22/23 by S0 to prove the tools, on a snapshot of the working tree before slices S1-S3 landed
(`build/*.py` sha256 `32b899ecb6fe73b0836e28c0af00bdf9197dc0e80387f736164f4a6ae30649cb`, 37 files), V0 only.
RC numbers are re-earned in S6 on the RC tree.

| Corpus | Engine | Nodes | CALLS | AMBIGUOUS share | Truth pairs | Confirmed | Recall | Refuted (share) | Library recall | Library receiver-form recall |
|---|---|---|---|---|---|---|---|---|---|---|
| psf/requests | 0.8.39 | 732 | 551 | 0.000 | 896 | 373 | 0.416 | 17 (0.031) | 0.831 | 4/21 = 0.191 |
| psf/requests | 0.8.51 | 732 | 565 | 0.000 | 896 | 388 | 0.433 | 17 (0.030) | 0.831 | 4/21 = 0.191 |
| pallets/flask | 0.8.39 | 985 | 330 | 0.079 | 895 | 270 | 0.302 | 18 (0.054) | 0.643 | 9/66 = 0.136 |
| pallets/flask | 0.8.51 | 985 | 329 | 0.079 | 895 | 269 | 0.301 | 18 (0.055) | 0.647 | 10/66 = 0.151 |
| colinhacks/zod | 0.8.39 | 3819 | 564 | 0.472 | 2656 | 261 | 0.098 | 191 (0.339) | 0.098 | 96/2143 = 0.045 |
| colinhacks/zod | 0.8.51 | 3839 | 608 | 0.438 | 2695 | 301 | 0.112 | 202 (0.332) | 0.112 | 102/2159 = 0.047 |

Pooled Python library receiver-form recall (requests + flask, jedi `name_attr` + `recv`), 0.8.39: 13/87 = 0.149.
Pooled Python library receiver-form recall (requests + flask, jedi `name_attr` + `recv`), 0.8.51: 14/87 = 0.161.

- **Determinism:** each of the 12 dumps (3 corpora x 2 engines x 2 runs) was byte-identical to its re-run.
- **Frozen flask V0 reproduced:** 330 CALLS / 26 AMBIGUOUS on 0.8.39 (329 / 26 on 0.8.51) — the harness gate's
  flask section re-checks it whenever the benchmark clone and an in-range engine are present.
- **Engine parity (V0, `engdiff.py`):** symmetric difference requests 16, flask 13, zod 74 (grade disagreements 0)
  — raw, over all edges. Amendment A1 compares the edges whose both endpoints exist on both engines: zod's V0
  reads 36 there on the phase-1 verification's V0 dumps (requests and flask unchanged: same node sets).
- **zod TS spots (V0):** `ZodType._processInputParams` hop-1 recall 17/17 on both engines;
  `ParseStatus.mergeObjectSync` 1/3 on both.
- **flask oracle-seeded impact (V0, 3 hops, `impact_offline.py`):** STRICT 9/16, SPEC 9/16 on both engines;
  the jedi ceiling 12/16 STRICT, 14/16 SPEC.
- **Truth:** jedi 0.20.1 — requests 37 files / 2,687 sites / 1,272 resolved in-corpus (86.8 s), flask 83 files /
  3,966 / 1,283 (231.7 s); TypeScript 5.6.3 — zod `packages/zod/src` 286 files / 33,237 / 16,859.
- **Against the design's §8.8 pilot** (prototype tools): requests recall .427 → .416 here, flask .332 → .302, zod
  .098 → .098. The kit maps a definition to its graph node on the decorator line as well as the `def` line, so
  more resolved sites become truth pairs (flask |T| 802 → 895); the edges themselves are the same 330.
- **Offline wall time per dump:** requests 17-37 s, flask 23-29 s, zod 60-83 s (both engines, shared machine).
- **TS application and JS repository (V0, 0.8.39; tool check only):** nestjs-realworld 23 CALLS, TypeScript 5.6.3
  resolved 51 of 360 sites, library recall 7/38; node-express-boilerplate 8 CALLS, `--js` resolved 113 of 592 sites
  (through the CommonJS `require('../services')` barrel), library recall 5/49. Every TS/JS spot symbol of §4 maps to
  exactly one graph node; reference hop-1 callers: `ArticleService.findAll` 1, `UserService.findById` 1,
  `getUserById` 6 (V0 finds 2 of the 6).
- **`run_matrix.sh` end to end** (requests, both engines, V0) reproduced the numbers above exactly. Under a longer
  scratch DEEPMAP_HOME (146 characters) graphify failed on Windows MAX_PATH and `auto` fell back to ast; that run is
  why every dump is now engine-pinned (§2).

**S0 review round (2026-10-23; PILOT, NOT decision data).** V0 on a snapshot of the working tree (`build/*.py`
sha256 `2e17fa926c6908b186278bbc3a8c6d017eb2651f1822f3390e62e20c8cf183f6`, 43 files; slices S1 / S3 partly
landed, sync without `_typed_calls_link`), both engines, each dump run twice and byte-identical, every extraction
resolved to graphify, through `run_matrix.sh` (0 VOID, 0 INVARIANT):

| Corpus | Engine | Nodes | CALLS | AMBIGUOUS share | Truth pairs | Confirmed | Recall | Refuted (share) | Library receiver-form recall |
|---|---|---|---|---|---|---|---|---|---|
| STAGE-0 corpus (registered layout) | 0.8.39 | 571 | 858 | 0.349 | 815 | 661 | 0.811 | 196 (0.228) | 7/144 = 0.049 |
| STAGE-0 corpus (registered layout) | 0.8.51 | 571 | 858 | 0.349 | 815 | 661 | 0.811 | 196 (0.228) | 7/144 = 0.049 |
| colinhacks/zod | 0.8.39 | 3819 | 564 | 0.472 | 2656 | 261 | 0.098 | 191 (0.339) | 96/2143 = 0.045 |
| colinhacks/zod | 0.8.51 | 3839 | 608 | 0.438 | 2695 | 301 | 0.112 | 202 (0.332) | 102/2159 = 0.047 |
| nestjs-realworld | both | 146 | 23 | 0.000 | 38 | 7 | 0.184 | 0 | 7/30 = 0.233 |
| node-express-boilerplate | both | 262 | 8 | 0.000 | 49 | 5 | 0.102 | 0 | 0/19 = 0.000 |
| pallets/werkzeug | 0.8.39 | 2220 | 1567 | 0.317 | 2521 | 1078 | 0.428 | 251 (0.160) | 44/198 = 0.222 |
| pallets/werkzeug | 0.8.51 | 2220 | 1615 | 0.305 | 2521 | 1132 | 0.449 | 251 (0.155) | 59/198 = 0.298 |
| fastapi/fastapi | 0.8.39 | 6312 | 7209 | 0.848 | 4488 | 1373 | 0.306 | 5596 (0.776) | 23/50 = 0.460 |
| fastapi/fastapi | 0.8.51 | 6312 | 7210 | 0.848 | 4488 | 1374 | 0.306 | 5596 (0.776) | 23/50 = 0.460 |

- **STAGE-0 corpus engine parity:** symmetric difference 0 (858 = 858 edges, 0 grade disagreements). jedi 0.20.1:
  34 files / 5,144 sites / 1,109 resolved in-corpus, 0 parse errors.
- **Cross-file EXTRACTED (`counts.extracted_cross_file`):** 0 on every dump (V0 grades nothing EXTRACTED).
- **Truth runs (jedi 0.20.1):** werkzeug 138 files / 8,504 sites / 4,133 resolved in-corpus (252 s); fastapi
  1,128 files / 16,669 / 5,230 (3,687 s), one file named in `parse_errors` — a path past Windows MAX_PATH under the
  scratch work dir (`scripts/tests/test_translation_fixer/...`, outside the library prefix): keep CL_WORK short
  for the RC run too (the corpus clones now run with `core.longpaths=true`, and a failed materialization leaves only
  `<corpus>.partial`, never a half tree).
- **Oracle-seeded 3-hop impact (V0, `impact_offline.py`):** werkzeug-blast-1 STRICT 4/9, fastapi-blast-1 STRICT
  7/13 / SPEC 9/13, on both engines.
- **Spot saturation, measured:** `impact_spot.py --base` on the zod V0 dumps flags `ZodType._processInputParams`
  SATURATED (17/17, both engines) — the reason it was replaced (§4).

**S4 engine-parity run on the near-RC tree (2026-10-23; PILOT, NOT decision data).**
`tests/typed_calls_engine_parity_gates.py --corpora`, VT against V0, both engine ends, on the working tree with slices
S1-S4 landed: the production callers' typed tuples are identical on requests, flask, click, werkzeug and fastapi
(124, 104, 249, 425, 148); on zod they number 454 on 0.8.39 and 456 on 0.8.51 on the common endpoints (every
caller, with TESTS and NONPROD: 512 vs 514). The two extra tuples are true, and their evidence chain runs through
`ZodTuple.create`, a node only 0.8.51 makes. E is not amended for them: they go the DECISION.md route, recorded
before RC as PD-1 in PRE_RC_DECISIONS.md (exactly those two tuples; E's first bullet and D3-js G6 are unchanged).
E's second bullet held on all six corpora (zod 32 <= 36 on the common endpoints; raw 89 vs 74).

## Amendments before RC

Registered 2026-10-23, before any RC data: the §1 content hash is not filled and no RC dump exists. §8 below stays
verbatim and hash-locked; each amendment names the §8 items it changes, how and why, and governs them. A1 and A2
were prompted by the phase-1 verification runs, which are PILOT data on a scratch emulation of slice S2 (§7's rule:
never decision data).

### A1 — Engine parity (E) on the edges whose both endpoints exist on both engines

- **Changes:** §8.4 E (the typed tuples identical on version-stable keys; the final-graph symmetric difference
  between 0.8.39 and 0.8.51 ≤ V0's on every corpus), and through it D3-G6 ("E holds", the engine-specific typed
  edges) and the symmetric-difference half of D1-G5.
- **How:** every E comparison is made on the edges (typed tuples) whose BOTH endpoints exist on both engines, as
  version-stable keys `label:name@file:line`; for the criterion that is the node set present in all four dumps (the
  variant and V0, on each engine). The raw symmetric difference over all edges is reported beside it, with the
  nodes that exist on one engine only, and is never decisive. G6's engine-specific changes (an edge the variant
  adds, removes or regrades against V0 on one engine only) are counted on the same common endpoints; the raw count
  is reported. The threshold is unchanged (≤ V0's, tolerance 0), and an edge between two nodes both engines have
  that one engine holds and the other does not still counts, so an engine-specific typed edge still fails E.
  `engdiff.py` implements it: `criterion` decides `holds` on the common endpoints and reports `holds_raw`,
  `variant_symdiff_raw`, `v0_symdiff_raw`, `nodes_only_0839`, `nodes_only_0851` and `engine_specific_changes_raw`;
  it is VOID (exit 3) when a dump carries `void` or a variant's node set differs from its V0 base's on one engine
  (every §8.3 variant is edge-only, so the common-endpoint domain must be one set).
- **Why:** the two engine ends do not make the same nodes, so the raw symmetric difference measures the engines'
  node sets as well as the variant's parity. graphify 0.8.39 makes no node for a TypeScript static arrow-field
  member whose arrow function has type parameters (`static create = <El extends ZodTypeAny>(...) => ...`); 0.8.51
  does. In zod's packages/zod/src/v3/types.ts that is exactly 20 members (`ZodArray.create`,
  `ZodIntersection.create`, `ZodTuple.create`, `ZodReadonly.create`, `ZodObject.strictCreate`, ...): the whole
  difference between the engines' node sets (3,839 vs 3,819 nodes). The non-generic form
  (`static create = (params) => ...`, e.g. `ZodAny.create`) has a node on both. A typed edge to one of the 20
  exists on 0.8.51 only whatever the variant does. PILOT, VT vs V0 (phase-1 verification, scratch S2 emulation):
  the raw comparison reads zod 90 vs 74 (fails, 75 engine-specific changes); on the common endpoints it reads 33 vs
  36 (23 engine-specific changes). requests, flask and werkzeug have the same node set on both engines, so the two
  readings coincide there (16 vs 16, 12 vs 13, 56 vs 66). The alternative — making DeepMap's 0.8.39 mapping recover
  those members — would change the default graph on 0.8.39 and is not a measurement amendment.

### A2 — The Python spot `_mk_project` is replaced by `read_records`

- **Changes:** §8.4's hop-1 spot list (`impact_spot.py`) and with it D3-G1's "hop-1 recall improves on at least 3
  of 5 spot symbols" for Python; PREREG §4.
- **How:** `_mk_project` (this repository) is replaced by `read_records@retrieval_log.py` on the STAGE-0 build/
  corpus (9c8d50bceab96288f83877c5306167b54b6d918a, the registered `self-graphify-build`: this repository's own
  code). The `self` corpus keeps its G-layer role and holds no spot. Every spot now lives on a corpus
  `run_matrix.sh` pins to a SHA, so the V0 numbers §4 records are reproducible at RC; `impact_spot.py --base` still
  flags a spot SATURATED at RC time (exit 1).
- **Why:** `_mk_project` is SATURATED at V0 — its 108 V0 callers already hold the edge to their own file's def
  (inside an 8-way AMBIGUOUS fan-out, 864 edges; S0 review, 0.8.39) — so its hop-1 recall can never improve, and
  D3-G1's "3 of 5" was a count over the 4 spots that can: the defect the S0 review already fixed for the TS/JS
  spots.
- **How it was chosen (V0 data only, before any typed-linking data on it):** no typed-linking (VT) output exists
  for the STAGE-0 corpus. The phase-1 typed-link runs covered requests, flask, click, werkzeug, fastapi, zod and the
  `self` working tree, which is also why the spot is not taken from `self` (that output was not opened for this
  choice). Inputs: V0 dumps of the corpus on both engines from a snapshot of the working tree's build/*.py taken
  before sync.py gained the typed-call step (sha256
  `68ac85414ff4bf7175719b71a40e61db8b38036c4e0e8d87c168fcdbcdee2058`, 43 files; edge-for-edge identical to the S0
  review's V0 dumps, 858 CALLS on each engine), re-checked on a snapshot taken after it did (sha256
  `0ac63c99517a8a85fe5139ad3e629623e9f26f24976ccdb3547fbeba062bcd1c`, typed step present, flags off: nodes and
  edges identical, `read_records` still 0 of 8), and the S0 review's jedi 0.20.1 truth (34 files, 5,144 sites). Rule
  (the §4 TS/JS rule, applied to Python): the symbol `Name@file` maps to exactly one graph node on both engines;
  its V0 hop-1 recall is below 1 on both engines; at least one reference caller V0 misses reaches it through a
  Python receiver form (jedi `name_attr` / `recv`, D3-G1's py forms), a receiver shape typed linking targets;
  ranked by the reference callers that reach it through those forms, then by all reference callers. Of the 81
  symbols that qualify, `read_records` ranks first: 8 reference callers, all `_rl.read_records(root)` through a
  module-level `import retrieval_log as _rl` (shape M), in access_prior, confidence_centrality_ab, graph_signal_ab,
  insights, learning_loop and tune_weights (3 callers); V0 links 0 of the 8 on both engines (DeepMap's default
  CALLS miss `module.func()`). The next are `build_qrels` and `read_instance`, with 5 of their 8 reference callers
  through receiver forms.

### Other edits before RC (no criterion changed)

- **2026-10-24, §5 and the kit (a review finding).** The `effective` block did not report the cross-family
  filter or the typed-calls configuration, so `delta.py` voided VF against V0 as an inert pair although the filter
  worked, and would have voided every ablation compared against VT (VT+t, VT+np, VT[shape]). `offline_calls.py`
  now reports them through the product, and `variant_void` VOIDs a dump whose requested switch the product left
  off. No variant, criterion or threshold changed.
- **2026-10-24, §7.** S4's near-RC engine-parity run (PILOT) and the decision it led to, PD-1 in
  PRE_RC_DECISIONS.md, which takes the DECISION.md route instead of a second amendment of E.

<!-- PREREG-SECTION-8 BEGIN (verbatim from the design; sha256 locked by tests/call_linking_harness_gates.py) -->
## 8. Measurement protocol

### 8.1 Pre-registration
S0 commits benchmark/call_linking/PREREG.md BEFORE any RC data. It records:
- this section verbatim;
- the RC code SHA;
- the engine builds;
- the TS/JS corpus SHAs;
- the five TS spot symbols.

Pilot numbers (§8.8) are labelled as pilot data. Thresholds are never edited after data. RESULTS.md and DECISION.md are written once, against PREREG.

### 8.2 Environment and corpora
- **Engines:** graphify 0.8.39 (dev Python 3.13) and 0.8.51 (scratch `uv venv`, `graphifyy==0.8.51`). `PYTHONUTF8=1`, `PYTHONHASHSEED=0`. `DEEPMAP_GRAPHIFY_TIMEOUT=3600` for this repo (600 s fell back to ast under load).
- **Python corpora** (benchmark/repos.yaml; copied from C:/tmp/deepmap-bench-clones with `git clone --no-hardlinks`):
  - requests 23953c0c875219a715f081cf3de7c149a7629ccf
  - flask 36e4a824f340fdee7ed50937ba8e7f6bc7d17f81
  - click 679a7a0eccbdded7a6e85680bdaaf08003765e01
  - werkzeug 1b00618e787f40dfb21eba29caf8f8be7c8e1d93
  - fastapi 702fea83bb3ef0b558104835d7936af49650fb2d
  - this repo at the RC SHA
  - the STAGE-0 build/ corpus at 9c8d50bceab96288f83877c5306167b54b6d918a
- **TS/JS corpora:**
  - zod 912f0f51b0ced654d0069741e7160834dca742ee (packages/zod/src);
  - one TS application and one JS repo from the list at CHANGELOG.md:200-205 (nestjs-realworld, node-express-boilerplate), with SHAs pinned in PREREG.
- **Smoke only** (no crash, stats sane, time ceiling): gin 34dac209, httprouter 48401801, serde-json 827a315b.
- **Reference resolvers** (measurement only; never in requirements.txt, SBOM or NOTICE):
  - jedi 0.20.1, run in an isolated venv with `-I`;
  - TypeScript 5.6.3 checker (with `allowJs`/`checkJs` for JS);
  - wave 2: Go via docker golang + `x/tools/cmd/callgraph -algo=static`; Java via a javac Trees plugin; C# via Roslyn `GetSymbolInfo`; Rust via rust-analyzer SCIP in docker; Ruby audit-only.
- **Ground truth for retrieval:**
  - benchmark/questions.yaml (33 questions, 234 atoms, signed off 2026-07-08);
  - the zod wing questions;
  - gt_self65.json (recovered; re-baselined at the RC SHA).

### 8.3 Variants

| Id | Flags |
|---|---|
| V0 | the default |
| VF | `CALLS_FAMILY_FILTER=1` |
| VA | `CALLS_AUDIT=1` |
| VT | `TYPED_CALLS=python,typescript,javascript` + `AUDIT=1` |
| VT−a | VT without the audit (the arm for the G2 AMBIGUOUS-share criterion) |
| VT+t / VT+np | VT with test / non-production additions (ablations) |
| VT[shape] | VT with `SHAPES=` a single shape, one arm per shape |
| VP | `PRECISE=python,js` (with fixes) |
| VPA | VP + `AUDIT=1` (the D1 fallback) |
| VPT | VP + VT (the D1 primary candidate) |
| VRC | the exact combination to flip (confirmatory) |
| V9 | VPT + `CALLS_PLUS=1` (D2) |

### 8.4 Layers and commands

**G — graph, offline, deterministic.** Run twice; the outputs must be byte-identical.
- CALLS dump per corpus, engine and variant:
  ```
  DEEPMAP_HOME=<scratch>/dmhome <flags> <py39|venv51/python> benchmark/call_linking/offline_calls.py build <corpus> out/<c>_<V>_<eng>.json
  ```
  It mirrors sync.py:4708-4832 and is source-locked to `sync()` by a gate.
- Reference truth:
  ```
  <jedi-venv>/python -I benchmark/call_linking/truth_jedi.py <corpus> out/<c>_truth.json
  node benchmark/call_linking/truth_ts.js <tsenv> <corpus> <subdir> out/<c>_truth.json
  ```
- Scoring and diffs:
  ```
  python benchmark/call_linking/compare_calls.py out/<c>_truth.json out/<c>_*.json --src-prefix=<pkg>/
  ```
  plus `delta.py`, `indegree_drift.py --questions benchmark/questions.yaml`, `engdiff.py`, `impact_offline.py` (oracle-seeded 3-hop; baseline 25/49, ceiling 28/49) and `impact_spot.py` (hop-1: MapAdapter.match, Map.bind, Depends, run_wsgi_app, `_mk_project`; plus the 5 TS spot symbols registered in PREREG).

**A — audit.**
- `audit_sample.py --seed 20261022 --n 40` per corpus and bucket {added, removed, demoted, EXTRACTED}.
- Blind: the variant is hidden. Two reviewers each record TRUE / FALSE / UNSURE from the call site.
- EVERY removed edge the reference resolver confirms is audited exhaustively, not sampled.
- Precision is reported with Wilson 95% bounds.
- A reference-resolver error rate of 10% or more on the sample voids that corpus's G confirmed/refuted deltas; the audit then decides.

**R — retrieval, live, matched state.**
- **Throwaway home:**
  - DEEPMAP_HOME at most 140 characters, seeded with a copy of `~/.deepmap/cache/neo4j-community-5.26.0-windows.zip`;
  - NEO4J_URI unset; HF_HUB_OFFLINE=1;
  - check listeners first (dev pairs 7689/7476 and 8787/8788);
  - clean up with `provision.py stop` and `provision.py instance-rm --key <k> --purge --yes`;
  - never touch ~/.deepmap.
- **Procedure:**
  1. V0: `python benchmark/clone_graph.py build <clone>` with `DEEPMAP_VECTOR_QUANTIZATION=0`.
  2. Each variant: an edge-only re-sync on the same instance, `python build/deepmap_cli.py sync <clone> --stale-cleanup` with only that variant's flags.
  3. The log must show 0 nodes re-embedded and no FTS re-create, or the run is VOID. Every variant here is edge-only: `callees_by_src` does not depend on any flag.
  4. Capture with `run_variant.py`, two passes: `search(top_k=15)` plus `impact(limit=30)` on the top-3 hits for the transitive, multi_hop and runtime_edge questions.
  5. Score with `score_m.py` STRICT and SPEC, and `measure_self.py gt_self65.json` (MRR, hit@1/5/15).
  6. Round trip back to V0: it must reproduce V0's capture exactly, or the run is VOID.
  7. Self: replicate on a second fresh V0/variant pair.

**S — STAGE-0.**
- `python tests/stage0_refreeze.py --work <fresh> --check --env <variant flags>` for each candidate.

**P — performance.**
- Sync wall time and the pass's own seconds, from the log, on fastapi and this repo; median of 3; both engines.

**E — engine parity.**
- On the fixtures and the 5 corpora: typed tuples identical on version-stable keys (label:name@file:line).
- Final-graph symmetric difference between 0.8.39 and 0.8.51 ≤ V0's on every corpus.
- `engdiff.py` and the label-set projection report.

### 8.5 Decisions (ALL criteria must hold; the order is D0, D3, D1, D2; one release flips everything that passed)

**D0 (VF vs V0).**
- Every removed pair is cross-family (asserted).
- 0 confirmed pairs lost on any corpus or engine.
- R1 holds.
- `self-graphify-build` unchanged, with attribution if it moves.

**D3-<py | js> (VT vs V0; and VPT vs VP when D1 is also evaluated).**
- **G1:**
  - library-code (src-only) receiver-form recall up by at least **10 points absolute**, pooled per family, on both engine ends. py: jedi `name_attr` + `recv`; js: tsc `name_attr` + `recv` + `this`;
  - no call form's recall falls on any corpus (tolerance 0);
  - hop-1 recall improves on at least 3 of 5 spot symbols.
- **G2:**
  - confirmed pairs ≥ V0 on every corpus × engine (tolerance 0);
  - whole-graph refuted share not higher anywhere;
  - AMBIGUOUS share not higher, **measured on VT−a**, since audit demotions are honest by design and are reported separately.
- **G3:**
  - audited precision of added edges ≥ 95% pooled per family (Wilson LB ≥ 90%), and ≥ 90% per corpus;
  - removed edges audited FALSE ≥ 95% (LB ≥ 90%);
  - **zero audited-TRUE removals** (losses) on any corpus × engine.
- **G4:** EXTRACTED refuted share ≤ 1% on every corpus (VT and VPT); 0 FALSE in the typed-EXTRACTED audit sample.
- **G5:** the §5 gates GREEN on 0.8.39 and 0.8.51; the on/off effect diff on 2 real corpora per family reviewed and committed.
- **G6:** E holds; the witness/parity report shows no engine-specific typed edge.
- **R1:**
  - no question loses a STRICT or SPEC search∪impact atom on any corpus (matched state, threshold 0);
  - the 33-question STRICT∪ macro ≥ V0;
  - js: no zod-wing atom loss.
- **R2:** self MRR ≥ V0 (tolerance 0) on both replicated pairs; hit@15 drops by at most 1 query.
- **R3:** live `impact(limit=30)` STRICT atoms on the 4 multi_hop questions ≥ V0.
- **Drift void rule:** the flip is void if a node whose arm-3 multiplier rose 2× or more becomes newly fused-top-3 for 3 or more questions, unless the audit shows its new callers are true production callers. Drift (count, max multiplier, top-20) is always reported.
- **S:**
  - the `self` oracle passes unchanged;
  - `self-graphify-build` passes, or diverges only at rank ≥ 3 with every divergence attributed to audited-true edge changes in build/.
- **P:** sync wall time +≤ 20% on fastapi and this repo; the pass alone ≤ 15 s on this repo, both engines.

**D1-<py | js> (candidate vs the same graph without PRECISE: VPT vs VT, or VPA vs VA).**
- **G1:** confirmed pairs ≥ the non-precise graph on every corpus × engine (tolerance 0). zod must recover the 14 OK/DIRTY edges.
- **G2:** refuted share not higher anywhere; pooled share down at least 50% relative to V0.
- **G3:** EXTRACTED refuted ≤ 1% on every corpus; audited EXTRACTED precision ≥ 95% (LB ≥ 90%).
- **G4:** py only — AMBIGUOUS share down at least 25 points on fastapi and this repo.
- **G5:** P1b makes the Python probe fixture identical on both engines; the symmetric difference is ≤ V0's.
- **R1:** as in D3.
- **R2:** py only — self MRR ≥ V0 + 0.017 in matched state, replicated; hit@15 drops by at most 1 query.
- **S:** as in D3.
- **P:** map step plus push passes ≤ 1.5× the precise-off time; optimise before flipping otherwise.

**D2 (V9 vs VPT).**
- **G1:** confirmed pairs +3% relative on 4 of 5 corpora, both engines.
- **G2:** refuted share not up.
- **G3:** audited addition precision ≥ 90% (LB ≥ 85%), with `:Module` and `:Class` callers reported separately; additions from non-production callers = 0.
- **R1:** no family loses an atom; macro ≥ V0.
- **R2:** self MRR ≥ V0.
- **S** as in D1.

**Confirmatory run (VRC).** The exact flip combination re-runs R1, R2, S and P once. If it fails, nothing flips; attribute and re-register.

**Wave-2 languages.** Each gets its own D3 against its own reference, plus R1 on any benchmark questions in that language. Ruby is accepted on audit only: precision ≥ 97% (LB ≥ 92%) on 80 added edges, and zero audited-true removals.

### 8.6 Noise floors
- G and E are deterministic (noise 0).
- R in matched state is exact (round trip; Phase M: "an edge round trip in the same vector state is exact").
- The cross-history floors (werkzeug 7.2 points / 3 atoms; fastapi 1.5; self MRR 0.017) apply ONLY to a void run's diagnosis. They are never used as slack.
- No variant here changes node text, so no fresh-build comparison is used.

### 8.7 Results that mean DO NOT SHIP (for that decision)
- Any audited-TRUE removal.
- Any R1 atom loss.
- Self MRR below V0 (D3), or below V0 + 0.017 (D1-py).
- EXTRACTED refuted above 1% on any corpus.
- D3-G1 below +10 points.
- Engine-parity worse than V0.
- A voided run that cannot be made exact.
- The `self` oracle moving.
- A rank-1 or rank-2 `self-graphify-build` divergence not attributed to a true edge change.
- The drift void rule firing.
- P budgets exceeded (optimise, then re-measure).
- A grammar canary failing on either CI image for a wave-1 language.
- G3 failing on audit when the reference resolver is unreliable.

If D3 fails but the D1 fallback (VPA) passes, PRECISE + audit ships alone. If D1 fails, PRECISE stays opt-in and the failure is recorded.

### 8.8 Pilot data (context only; re-earned in S6)

The source-reader prototype was scored with compare_calls against jedi 0.20.1, under the policy "tests corrections-only, examples/docs allowed" (stricter than the shipped policy only for examples/docs).

| Corpus | Recall | Refuted edges | Added edges confirmed |
|---|---|---|---|
| requests | .427 → .459 | 17 → 0 | 28/33 |
| flask | .332 → .400 | 7 → 0 | 55/59 |
| click | .254 → .304 | 113 → 3 | 99/118 |
| werkzeug | .463 → .521 | — | 131/190 |
| fastapi | .311 → .320 | — | 38/62 |

- Pooled confirmed pairs 3,534 → 3,885 (+9.9%).
- 0 of 258 removed edges confirmed.
- PRECISE + TYPED: requests EXTRACTED refuted 10.0% → 0.0%. werkzeug `Map.add → Map.bind` still EXTRACTED; the audit fixes this.
- zod (default base): recall .098 → .288; 505/507 added edges confirmed.
- The TRL pilot under its own move-only default: +111 pairs (+3.1%), not the +18% all-callers figure. Its STAGE-0 "0/0 edges" prediction excluded shapes M and C and does not apply to this design.
<!-- PREREG-SECTION-8 END -->
