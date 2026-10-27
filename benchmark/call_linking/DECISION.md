# Call linking — DECISION (S6, the release-candidate run)

Written once on 2026-10-25 by the S6 decision lane, against [PREREG.md](PREREG.md) §8.5 (verbatim, hash-locked) and
its amendments A1 and A2, from the measurements in [RESULTS.md](RESULTS.md). The order is D0, D3, D1, D2; a decision
passes only when ALL its criteria hold; one release flips everything that passed; the confirmatory run (VRC) re-runs
R1, R2, S and P on the exact flip combination before anything flips. No threshold, criterion, variant, corpus or
engine was changed after data. VOID runs were redone; none is used (RESULTS.md §0).

**Reconciled 2026-10-25.** A second, independent derivation recomputed every decision from the same raw artifacts
(`<work>/derive-indep2/out/`, where `<work>/` is the measurement run's local work directory; its raw artifacts
are not shipped). Both derivations reach the same six outcomes and the same flip combination.
Where they read a criterion differently, or where either flagged an alternative reading, the maintainer's rule
decides: the strict reading wins when in doubt, and a criterion that cannot be computed exactly does not pass.

- No measurement was redone, and no threshold, criterion, variant, corpus or engine changed.
- Several criterion verdicts changed. Every changed verdict is a criterion of a decision that already failed.
- The readings applied are below. Each difference and its resolution are in "Reconciliation of the two derivations" at the end.

## Outcome

| Decision | Compares | Outcome | Criteria that do not hold |
|---|---|---|---|
| D0 | VF vs V0 | **PASS** | — |
| D3-py | VT vs V0; VPT vs VP | **FAIL** | G1 (spots); G3 (removed edges, per corpus); G5 (not established); G6 (strict reading); R1; S; P (not established) |
| D3-js | VT vs V0; VPT vs VP | **FAIL** | G5 (not established); G6 (strict reading); R1 (the measured arm); S; P (not established) |
| D1-py | VPA vs VA (the fallback: D3-py failed); VPT vs VT reported | **FAIL** | G3 (audited EXTRACTED, per corpus); G5; R2; S; P (not established) |
| D1-js | VPA vs VA (the fallback: D3-js failed); VPT vs VT reported | **FAIL** | G2; G3; S; P (not established); §8.7 audited-TRUE removals |
| D2 | V9 vs VPT | **FAIL** | G1; G3; R1; S |

**The flip combination: `DEEPMAP_CALLS_FAMILY_FILTER=1` becomes the default (D0), and nothing else** — conditional
on the VRC below. Typed-receiver call linking (`DEEPMAP_TYPED_CALLS`), the audit (`DEEPMAP_CALLS_AUDIT`), family-scoped
PRECISE (`DEEPMAP_PRECISE_CALLS`) and `DEEPMAP_GRAPHIFY_CALLS_PLUS` stay opt-in, with their failures recorded below.

## Rulings during the RC run

Recorded verbatim, as the coordinator (who led the run for the maintainer) gave them (2026-10-24):

> COORDINATOR RULINGS (2026-10-24; record them verbatim in DECISION.md under 'Rulings during the RC run'): (1) call_linking.seconds is P-layer timing, not graph content: a dump pair counts as deterministic when every byte except wall-clock fields matches (the kit fix and the re-judgement are in KITFIX below; use its determinism.json, never a pair it still marks VOID); (2) the RC tree drift is limited to routing/CLI/provision files; graph identity is attested in KITFIX rc_attestation.json — if that attestation FAILED, every affected layer is VOID; (3) dumps that fell back to ast because the machine slept (Modern Standby 15:56-17:57) were redone and only the redone pairs are data.

The kit fix and the RC-drift attestation those rulings rest on (KITFIX), recorded verbatim:

> DECISION (2026-10-24, coordinator ruling, before any RC result was read). PREREG §8.4 G's 'each dump run twice must be byte-identical' checks graph content; a timing is P-layer data. `offline_calls.make_dump` copied `typed_calls.link`'s stats verbatim into the dump's `call_linking` block, including `seconds`, the typed pass's own wall time. build/typed_calls.py documents it as the only non-deterministic value. So `run_matrix.sh`'s raw `cmp` called VOID nearly every pair of a typed variant (V9, VA, VPA, VPT, VT and its ablations), although every other byte was identical. Kit fix: the dump leaves out `WALL_CLOCK_FIELDS` (`call_linking.seconds`); `<dump>.timing.json` carries it as `call_linking_seconds`; P-layer readers take it from there or from the sync log. Dumps written before the fix are re-judged by a byte comparison with exactly that field removed (`offline_calls.strip_wall_clock`). Result over g1/g2/g3: 572 pairs, 562 DETERMINISTIC (458 of them were VOID only for `call_linking.seconds`) and 10 still VOID. All 10 are engine fallbacks (graphify timed out at 3600 s), each superseded by a deterministic redo, and none is used. No threshold, criterion, variant, corpus or engine changed. The kit change (offline_calls.py and README.md; kit hash e3f30d61… → 5dead487…) changes no graph byte. Under PREREG §1's tool-change rule it touches only `call_linking.seconds`, which this ruling takes out of the determinism check.
>
> RC drift: build/port_registry.py, build/provision.py and build/deepmap_cli.py changed after §1's hash 304c3f11… was filled (the NEO4J_URI Bolt-only scheme rule and wording; no graph code). On the current tree (14e3b01e…), offline_calls rebuilt psf/requests and colinhacks/zod, V0 and VT, on graphify 0.8.39 and 0.8.51, twice each. Every dump is byte-identical to every RC data dump of its corpus × variant × engine (32 comparisons), with `call_linking.seconds`, the `code` stamp and `corpus.root` removed. The RC dumps stand for the current tree's graph; the RC identity stays 304c3f11….

How this lane applied them: (1) every G / E / A number reads only DETERMINISTIC data pairs of
`kitfix/determinism.json` (run a, wall clock stripped; RESULTS.md §0); the P layer reads the typed pass's seconds
from the timing sidecar. (2) `kitfix/rc_attestation.json` reads GRAPH-IDENTICAL, so no layer is VOID for the drift;
the live layers ran from the RC tree `prep/rc` in any case. (3) The ten VOID pairs (each an ast fallback in a run
that spanned the standby) are listed in RESULTS.md §0 and not used; their redone pairs are.

## Pre-RC decision PD-1 (copied verbatim from PRE_RC_DECISIONS.md)

PD-1 is applied mechanically by `engdiff.typed_tuple_verdict` inside the engine-parity gate: on zod the two differing
typed tuples are exactly the listed ones (0.8.51 only, INFERRED), so E's first bullet and D3-js G6 hold with PD-1 and
fail as registered (RESULTS.md §3.1). The entry, as registered:

## PD-1 — two engine-specific typed tuples on zod, accepted as true edges (2026-10-24)

**Criterion it concerns.** §8.4 E, first bullet (the typed tuples identical on version-stable keys, compared on the
edges whose both endpoints exist on both engines by amendment A1), and through it D3-js G6 ("E holds; the
witness/parity report shows no engine-specific typed edge"). Not changed: A1 stays the only amendment of E, and E
keeps its tolerance of 0.

**Data (PILOT, not decision data).** S4's corpora run, `tests/typed_calls_engine_parity_gates.py --corpora`, on the
working tree of 2026-10-23 (slices S1-S4 landed; not the RC: PREREG §1's hash is still unfilled), graphify 0.8.39
and 0.8.51, VT against V0:

- zod: the production callers' typed tuples (VT, the shipped policy) number 454 on 0.8.39 and 456 on 0.8.51 on the
  common endpoints; every caller's (VT with `DEEPMAP_TYPED_CALLS_TESTS=1` and `DEEPMAP_TYPED_CALLS_NONPROD=1`)
  512 and 514. Both differences are the same two tuples, both on 0.8.51 only.
- requests, flask, click, werkzeug and fastapi: identical (124, 104, 249, 425 and 148 production tuples).
- E's second bullet held on all six corpora; zod reads 32 <= 36 on the common endpoints (raw 89 vs 74, reported).

**The two tuples** (version-stable keys `label:name@file:line`):

| Caller | Target | Grade | Only on |
|---|---|---|---|
| `Method:ZodFunction.args@packages/zod/src/v3/types.ts:3912` | `Method:ZodTuple.rest@packages/zod/src/v3/types.ts:3456` | INFERRED | 0.8.51 |
| `Method:ZodFunction.create@packages/zod/src/v3/types.ts:3952` | `Method:ZodTuple.rest@packages/zod/src/v3/types.ts:3456` | INFERRED | 0.8.51 |

**Why they are true.** The TypeScript 5.6.3 reference (S0's zod truth, `truth_ts.js`) resolves the `.rest(...)`
calls at types.ts:3917 (in `args`) and types.ts:3954 (in `create`), receiver form `recv`, to `ZodTuple.rest`
(types.ts:3456).

**Why on one engine only.** An evidence chain through a node only one engine makes. Both calls read
`ZodTuple.create(...).rest(ZodUnknown.create())`: the receiver is typed through the declared return type of
`ZodTuple.create` (`ZodTuple<Items, null>`), and `ZodTuple.create` is `static create = <Items extends ...>(...) =>`
(types.ts:3463), one of the 20 generic static arrow-field members graphify 0.8.39 makes no node for (A1's reason).
On 0.8.39 the chain reaches a member without a node, the site stays unknown, and no typed tuple is made. Both
endpoints exist on both engines, so A1's endpoint rule does not exclude the tuples.

**What the graph holds.** The pushed `:CALLS` edges are the same on both engines: `args -> rest` and
`create -> rest` are INFERRED under V0 and under VT, on 0.8.39 and on 0.8.51 (the name `rest` has one project
candidate). Only the typed pass's own claim (`call_typed`) differs, so engdiff's final-graph comparison (E's second
bullet, G6's engine-specific changes) does not contain them.

**Decision.** Accepted as engine-specific true edges. At RC, on zod, a typed tuple that differs between the engines
on the common endpoints is accepted only if it is one of these two, on 0.8.51, graded INFERRED. When every
differing tuple is accepted, E's first bullet and D3-js G6 hold on zod, and RESULTS.md reports E as registered
beside it (`typed_tuple_verdict`: `holds_as_registered` and `holds`, with the decision id).

**Not covered.** Any other differing typed tuple on any corpus fails E and G6 as registered: one with the same
cause, one of these two on 0.8.39, or with another grade. The cause explains these two tuples; it is not a rule.

## Machine-readable list

```json
{
  "schema": "call-linking/pre-rc-decisions/1",
  "decisions": [
    {
      "id": "PD-1",
      "date": "2026-10-24",
      "corpus": "colinhacks__zod",
      "criteria": ["§8.4 E (typed tuples identical on version-stable keys)", "D3-js G6"],
      "tuples": [
        {"caller": "Method:ZodFunction.args@packages/zod/src/v3/types.ts:3912",
         "target": "Method:ZodTuple.rest@packages/zod/src/v3/types.ts:3456", "grade": "INFERRED", "only_on": "0.8.51"},
        {"caller": "Method:ZodFunction.create@packages/zod/src/v3/types.ts:3952",
         "target": "Method:ZodTuple.rest@packages/zod/src/v3/types.ts:3456", "grade": "INFERRED", "only_on": "0.8.51"}
      ]
    }
  ]
}
```

## Readings applied where §8.5 leaves a choice (reconciled 2026-10-25)

None of these changes a threshold. Where the two derivations differed, or where either flagged an alternative, the
strict reading is applied, and a criterion that was not computed exactly does not hold. The alternative readings and
their effect are in "Reconciliation of the two derivations".

**No single alternative reading changes an outcome.** D3-js is the only decision that any combination of readings
could pass, and only if all five of its failing criteria were read the lenient way at once. Both derivations rejected
one of those five, the sample-based reading of S. See "Robustness".

1. **Scope: which measurements a family's decision reads.**
   - A criterion that names its family is judged for that family. These are "pooled per family" (D3-G1, D3-G3 first
     bullet), R1's "js:" bullet, and D1's "py only" G4 and R2.
   - A G / A / E criterion stated per corpus is judged on the family's own corpora. These per-corpus numbers measure the
     family's own effect exactly: the js corpora hold only TypeScript / JavaScript, and the py corpora are Python,
     except fastapi's JS callers, whose only VT change is 14 grade-only promotions.
   - The live layers exist only for the registered combined arms (VT = TYPED on python,typescript,javascript; VP, VPA
     and VPT = PRECISE on python,js). These layers are R1's first two bullets, R2, R3, the drift rule, S and P. No
     family-only arm was registered or measured, and its live result cannot be computed from the combined arm exactly,
     so these criteria are judged on the arm as measured.
   - This is the strict reading of R1 for D3-js: its measured arm VT loses Python atoms. The per-family reading of
     R1 is in the Reconciliation.
2. **G3 granularity.**
   - A G3 bullet that names no pooling is judged per corpus × engine, with the registered tool's own figure.
     `audit_sample.py`'s docstring defines its per-key `removed_all` share and bound as "the 'removed audited FALSE
     >= 95% (LB >= 90%)' number". D1-G3's audited EXTRACTED precision is likewise judged per key.
   - G3's first bullet ("pooled per family (Wilson LB ≥ 90%), and ≥ 90% per corpus") pools the family's audited rows on
     one engine, with Wilson 95% on the sum. The population-weighted bound is reported beside it; both hold.
3. **R2 hit@15.** "Drops by at most 1 query" is the net drop that the registered `measure_self.compare` computes
   (`hit15_net_drop <= 1`). VT loses A-C22 and B-S05 and gains A-C14, so hit@15 goes from 41 to 40 of 65.
4. **D1 R2 base.** "Self MRR ≥ V0 + 0.017" is read literally, against V0. The reading against the non-precise graph
   (VA / VT) is reported beside it, and both give the same outcome.
5. **S attribution.** An edge change counts as "audited-true" only when both blind reviewers judged that edge itself:
   an added edge TRUE, a removed edge FALSE. Resolver confirmation, or a sample's precision, is not an audit of the
   edge.
6. **G6.** The criterion has two parts:
   - **"E holds"** means E's second bullet (A1) holds on every corpus, and E's first bullet holds through the
     engine-parity gate's C1 / C2 comparison with PD-1 applied.
   - **"The witness/parity report shows no engine-specific typed edge"** is read strictly. Two reports must each show
     none:
     - engdiff's engine-specific changes. These are A1's G6 count. engdiff's docstring requires that "the variant's
       own changes (vs its base) must agree across the engines".
     - the parity gate's shipped-policy comparison, which covers the typed tuples VT actually produces, test and
       non-production callers included.
   - PD-1 accepts exactly its two tuples. PD-1 itself says any other differing typed tuple fails G6 as registered
     ("Not covered").
7. **G5.** Both halves must be established when the decision is made:
   - the §5 gates are GREEN on both engines;
   - the on/off effect diff on two real corpora per family is reviewed AND committed.

   S6 commits nothing (the maintainer commits). The edge-level effect diffs and the audit sheets lie outside the
   repository (`<work>/s6d/delta/` and the `audit-*` directories). So the second half is not established.
8. **P.**
   - The registered P is "from the log". §8.4 P: sync wall time and the pass's own seconds, from the sync log, on
     fastapi and this repository, median of 3, both engines. No lane measured that.
   - The offline timings PREREG §6 names (`<dump>.timing.json`) leave out the Neo4j push, index and embedding phases
     of a sync. They are reported as supporting data (RESULTS.md §7) and do not establish P.
   - D1-P's map step is not timed on its own; it sits inside extraction. Its offline figure is an upper bound under
     noise, not a measurement.
9. **D1's candidate.** Design §7.1 (pre-registered) makes the primary D1 candidate VPT = PRECISE[f] + TYPED[f] + audit
   only if D3-f passed. Otherwise the pre-registered fallback is VPA = PRECISE[f] + audit (§8.7: "If D3 fails but the
   D1 fallback (VPA) passes, PRECISE + audit ships alone"). This rule selects the arm; it is not one of D1's criteria.
   Both D3 decisions failed, so D1 is decided on VPA vs VA, and VPT vs VT is reported.
10. **D2 G1's "4 of 5 corpora"** are the five Python benchmark corpora of §8.2 (requests, flask, click, werkzeug and
    fastapi). Any choice among the ten corpora gives at most 3: only requests, werkzeug and fastapi reach +3%.

## D0 — cross-family filter (VF vs V0)

| # | Criterion | Threshold | Measured | Holds | Evidence |
|---|---|---|---|---|---|
| 1 | Every removed pair is cross-family (asserted) | every removed (caller, callee) of different language families (edge_resolve.lang_family); every corpus x engine | removals only on self: 487 (g39) and 461 (g51), 948 of 948 cross-family; 0 removals on the other 9 G corpora | yes | s6d/delta/<corpus>_VF_vs_V0_<eng>.json; s6d/delta_summary.json |
| 2 | 0 confirmed pairs lost on any corpus or engine | 0 | 0 (self removals: 42 refuted + 445 / 419 unverified; confirmed pairs equal to V0 on every corpus x engine) | yes | s6d/delta_summary.json; s6d/gtable.json |
| 3 | R1 holds | no STRICT or SPEC search-union-impact atom lost (threshold 0); 33-question STRICT-union macro >= V0; no zod-wing atom loss | 0 atoms lost on the 33 Python and the 4 zod-wing questions on both engines; macro 0.5300 = 0.5300 (g39), 0.5127 = 0.5127 (g51); matched state, exact round trips | yes | s6d/R/r1_all.json; s6d/R/rt_matched.json |
| 4 | self-graphify-build unchanged, with attribution if it moves | stage0_refreeze --check PASS | VF: self-graphify-build PASS and self PASS on g39 and on g51 | yes | live-0839/out/_summary/S.md; live-0851/out/s0/div_VF.txt |

**D0 PASSES.** No §8.7 item applies: no audited-TRUE removal (VF removes only cross-family self edges, none
resolver-confirmed), no R1 atom loss, engine parity holds on every corpus (RESULTS.md §3.2), the `self` oracle does
not move, the drift void rule does not fire, and every VF VOID pair was made exact by a redo.

## D3-py — typed-receiver linking, Python (VT vs V0; VPT vs VP)

| # | Criterion | Threshold | Measured | Holds | Evidence |
|---|---|---|---|---|---|
| 1 | G1: library (src-only) receiver-form recall up >= 10 points absolute, pooled per family, both engine ends (py: jedi name_attr + recv) | >= +10.00 points on g39 and g51 | VT vs V0: 0.2817 -> 0.5651 (+28.34) g39, 0.2919 -> 0.5660 (+27.40) g51; VPT vs VP identical | yes | s6d/pool/py_<V>_<eng>.txt |
| 2 | G1: no call form's recall falls on any corpus | 0 falls (tolerance 0) | 0 falls (every form, whole graph and library prefix, 7 py corpora x 2 engines; VT vs V0 and VPT vs VP) | yes | s6d/gtable.json; s6d/scripts/forms.py |
| 3 | G1: hop-1 recall improves on at least 3 of 5 spot symbols | >= 3 of 5 (py: MapAdapter.match, Map.bind, run_wsgi_app, Depends, read_records) | 1 of 5 on both engines, VT and VPT (read_records 0/8 -> 8/8; MapAdapter.match 3/52, Map.bind 1/61, run_wsgi_app 3/15, Depends 16/275 unchanged) | **no** | s6d/spot/*_VT_vs_V0_<eng>.txt, *_VPT_vs_VP_<eng>.txt |
| 4 | G2: confirmed pairs >= V0 on every corpus x engine | tolerance 0 | holds on all 7 py corpora x 2 engines (e.g. requests 373 -> 404, click 540 -> 587, self 15195 -> 15424 on g39); VPT >= VP likewise | yes | s6d/gtable.json |
| 5 | G2: whole-graph refuted share not higher anywhere | variant <= V0 | lower or equal everywhere (e.g. fastapi 0.7760 -> 0.5819, self 0.9508 -> 0.9500 g39, 0.9634 -> 0.9627 g51); VPT <= VP likewise | yes | s6d/gtable.json |
| 6 | G2: AMBIGUOUS share not higher, measured on VT-a | VT-a <= V0 | lower or equal everywhere (requests 0 = 0; flask 0.0788 -> 0.0332; click 0.2898 -> 0.1278; werkzeug 0.3172 -> 0.2488; fastapi 0.8483 -> 0.6528; STAGE-0 0.3485 -> 0.3128; self 0.9855 -> 0.9836; g51 alike) | yes | s6d/gtable.json |
| 7 | G3: audited precision of added edges >= 95% pooled per family (Wilson LB >= 90%), and >= 90% per corpus | >= 0.95, LB >= 0.90 pooled; >= 0.90 per corpus | VT vs V0: 236/236 (LB 0.984) g39, 235/235 (LB 0.984) g51; every corpus 100%; VPT vs VP identical | yes | s6d/audit/pooled.json; RESULTS.md 4.3-4.4 |
| 8 | G3: removed edges audited FALSE >= 95% (LB >= 90%) | >= 0.95, LB >= 0.90 per corpus x engine (`audit_sample` `removed_all`; reading 2) | every audited removal is FALSE (VT vs V0 194/194 pooled, LB 0.981), but the census LB of three small removal sets is below 0.90 on both engines: requests 18/18 (LB 0.824), flask 10/10 (LB 0.723), STAGE-0 6/6 (LB 0.610); click, werkzeug, fastapi and self LB 0.912 | **no** | s6d/audit/scores.json (`removed_all`); RESULTS.md 4.3 |
| 9 | G3: zero audited-TRUE removals on any corpus x engine | 0 | 0 (VT vs V0 and VPT vs VP, every py corpus, both engines) | yes | s6d/audit/scores.json |
| 10 | G4: EXTRACTED refuted share <= 1% on every corpus (VT and VPT) | <= 0.01 | 0 on every py corpus, both engines, VT and VPT (e.g. self VT 0/2265, VPT 0/15154) | yes | s6d/gtable.json |
| 11 | G4: 0 FALSE in the typed-EXTRACTED audit sample | 0 | 0: VT vs V0 EXTRACTED 280/280 TRUE on each engine. The 2 (g39) / 3 (g51) FALSE EXTRACTED rows of VPT vs VP are PRECISE keys already EXTRACTED in the VP base (requests `__setstate__ → update`, `_copy_cookie_jar → set_cookie`; self, one test caller), not typed edges | yes | s6d/audit/pooled.json; s6d/audit/scores.json (`base_grade`) |
| 12 | G5: the section-5 gates GREEN on 0.8.39 and 0.8.51; the on/off effect diff on 2 real corpora per family reviewed and committed | gates GREEN on both engines; the effect diff of 2 py corpora reviewed and committed (reading 7) | **Gates:** 10 gates GREEN on both engines on the RC tree (prep/rc, build 304c3f11): core 482, py 943, ts 559, ts_fuzz 18, push 111, shapes 401, engine_parity 121, calls_family_filter 26, precise_fixes 266, precise_family 434. **Effect diff:** the requests and flask VT vs V0 diffs were blind-reviewed by the audit (rq-01/02, fl-02/05: every added, removed and demoted edge; 40 of the 92 / 77 EXTRACTED edges). They are **not committed**: the edge-level diffs are in s6d/delta/ outside the repository, RESULTS.md holds only their counts, and S6 commits nothing. Not established | **no** | s6d/gates/; s6d/delta/; RESULTS.md 2.6, 3.4 |
| 13 | G6: E holds; the witness/parity report shows no engine-specific typed edge | E (A1) <= V0 on every corpus; typed tuples identical (PD-1 list only); no engine-specific typed edge (reading 6) | **E holds:** VT symdiff on every py corpus: requests 16<=16, flask 12<=13, click 76<=76, werkzeug 56<=66, fastapi 1<=1, STAGE-0 0<=0, self 7711<=7727. C1 and C2 typed tuples are identical on the 5 gate corpora. **Engine-specific typed edges remain:** engdiff's engine-specific changes are requests 1, flask 1, werkzeug 11 and self 36 (none created by VT: each is carried from V0's own engine difference or converges). The shipped-policy typed tuples of test and non-production callers differ between the engines (g39-only / g51-only): requests 0/15, flask 7/4, click 0/76, werkzeug 2/31, fastapi 0/1 | **no** | s6d/engdiff/<corpus>_VT.json; g1/par/summary.json; g2/par/parity_summary.json; RESULTS.md 3.3, 3.5 |
| 14 | R1: no question loses a STRICT or SPEC search-union-impact atom (matched state, threshold 0); the 33-question STRICT-union macro >= V0 | 0 atoms lost; macro >= V0 | VT vs V0: 7 atoms lost (g39), 4 (g51); macro 0.5300 -> 0.5306, 0.5127 -> 0.5161; VPT vs VP: 6 lost on each engine | **no** | s6d/R/r1_all.json; RESULTS.md 5.2-5.3 |
| 15 | R2: self MRR >= V0 (tolerance 0) on both replicated pairs; hit@15 drops by at most 1 query | MRR >= V0; hit@15 net drop <= 1 | VT 0.3823 -> 0.3928 (g39), 0.3898 -> 0.4047 (g51), both replicas identical; hit@15 lost A-C22, B-S05, gained A-C14 (net -1) | yes | s6d/R/self/summary.json |
| 16 | R3: live impact(limit=30) STRICT atoms on the 4 multi_hop questions >= V0 | >= V0 | 24 -> 26 on both engines (VT and VPT) | yes | s6d/R/r1_all.json |
| 17 | Drift void rule: void if a node whose arm-3 multiplier rose >= 2x becomes newly fused-top-3 for >= 3 questions | not fired | not fired on any question corpus, either engine (VT, VPT) | yes | s6d/drift/ |
| 18 | S: the `self` oracle passes unchanged; `self-graphify-build` passes, or diverges only at rank >= 3 with every divergence attributed to audited-true edge changes in build/ | self oracle PASS; no rank-1/2 divergence; every in-degree-changing edge change in build/ audited true (added TRUE / removed FALSE) | self oracle PASS on g39 and g51; self-graphify-build REGRESSION in 5/5 queries, first divergent rank 3 (never 1 or 2) on both engines; attribution NOT established: of the 81 in-degree changes VT makes in build/ (75 added, all jedi-confirmed; 6 removed, all refuted) the audit judged 46 (40 added TRUE, 6 removed FALSE) and never judged 35 additions, among them 6 of the 7 added edges on the diverging nodes' own in-degree; the lock-query and RRF-query swaps move nodes whose in-degree did not change | **no** | live-0839/out/_summary/S.md, S_attribution.md; live-0851/out/s0/div_VT*.txt; RESULTS.md section 6 |
| 19 | P: sync wall time +<= 20% on fastapi and this repo; the pass alone <= 15 s on this repo, both engines | sync wall and the pass's seconds from the sync log, median of 3, both engines (reading 8) | **Not established.** No lane took the registered sync-log measurement. The offline figures (PREREG §6 `timing.json`; median of 3; load median 12.5–21.5%) are within budget and support the criterion but do not establish it: offline sync wall fastapi +0.8% (g39) / −4.0% (g51), self +7.3% / +6.6%; the typed pass on self 7.73 / 9.56 s | **no** | s6d/p/p_summary.json, s6d/p/out/<eng>/<corpus>/loads.jsonl; RESULTS.md 7 |

**D3-py FAILS.** The criteria that do not hold:
- G1's spot bullet (1 of 5).
- G3, removed edges per corpus: small censuses, every audited removal correct.
- G5: the effect diff is not committed.
- G6: engine-specific typed edges, under the strict reading.
- R1: atoms lost on both engines.
- S: attribution not established.
- P: not established.

§8.7 "Any R1 atom loss" applies: DO NOT SHIP.

The four py spots that do not move have their missed reference callers in tests/, examples/ and docs_src/, which the
shipped caller policy never adds to. The lost atoms are click and werkzeug symbols that sat at V0 search ranks 9–15.

## D3-js — typed-receiver linking, TypeScript / JavaScript (VT vs V0; VPT vs VP)

| # | Criterion | Threshold | Measured | Holds | Evidence |
|---|---|---|---|---|---|
| 1 | G1: library (src-only) receiver-form recall up >= 10 points absolute, pooled per family, both engine ends (js: tsc name_attr + recv + this) | >= +10.00 points on g39 and g51 | VT vs V0: 0.0470 -> 0.2112 (+16.42) g39, 0.0494 -> 0.2192 (+16.98) g51; VPT vs VP identical | yes | s6d/pool/js_<V>_<eng>.txt |
| 2 | G1: no call form's recall falls on any corpus | 0 falls | 0 falls (zod, nestjs-rw, node-express, both engines; VT vs V0 and VPT vs VP) | yes | s6d/gtable.json |
| 3 | G1: hop-1 recall improves on at least 3 of 5 spot symbols | >= 3 of 5 | 5 of 5 on both engines, VT and VPT (ZodString._addCheck 0/24 -> 23/24, ParseStatus.mergeObjectSync 1/3 -> 3/3, ArticleService.findAll 0/1 -> 1/1, UserService.generateJWT 1/2 -> 2/2, getUserById 2/6 -> 5/6) | yes | s6d/spot/ |
| 4 | G2: confirmed pairs >= V0 on every corpus x engine | tolerance 0 | zod 261 -> 654 / 301 -> 729, nestjs-rw 7 -> 33, node-express 5 -> 20; VPT = VT | yes | s6d/gtable.json |
| 5 | G2: whole-graph refuted share not higher anywhere | variant <= V0 | zod 0.3387 -> 0.1643 (g39), 0.3322 -> 0.1513 (g51); nestjs-rw and node-express 0 -> 0; VPT <= VP | yes | s6d/gtable.json |
| 6 | G2: AMBIGUOUS share not higher, measured on VT-a | VT-a <= V0 | zod 0.4716 -> 0.2913, 0.4375 -> 0.2720; nestjs-rw 0 -> 0; node-express 0 -> 0 | yes | s6d/gtable.json |
| 7 | G3: audited precision of added edges >= 95% pooled per family (Wilson LB >= 90%), and >= 90% per corpus | >= 0.95, LB >= 0.90; >= 0.90 per corpus | VT vs V0: 81/81 (LB 0.955) on each engine; zod 40/40, nestjs-rw 26/26, node-express 15/15; VPT vs VP identical | yes | s6d/audit/pooled.json |
| 8 | G3: removed edges audited FALSE >= 95% (LB >= 90%) | >= 0.95, LB >= 0.90 per corpus x engine (`removed_all`; reading 2) | VT vs V0: zod 40/40 FALSE, `removed_all` 1.000 / LB 0.912 over 44 (g39) and 58 (g51) removals; nestjs-rw and node-express remove nothing; VPT vs VP 40/40 | yes | s6d/audit/scores.json |
| 9 | G3: zero audited-TRUE removals on any corpus x engine | 0 | 0 (VT vs V0 and VPT vs VP) | yes | s6d/audit/scores.json |
| 10 | G4: EXTRACTED refuted share <= 1% on every corpus (VT and VPT) | <= 0.01 | VT: zod 0/163, 0/204, nestjs-rw 0/7, node-express none; VPT: zod 1/309 (0.32%), 1/349 (0.29%), nestjs-rw 0/8, node-express 0/8 | yes | s6d/gtable.json |
| 11 | G4: 0 FALSE in the typed-EXTRACTED audit sample | 0 | 0: VT vs V0 EXTRACTED 47/47 TRUE on each engine | yes | s6d/audit/pooled.json |
| 12 | G5: the section-5 gates GREEN on 0.8.39 and 0.8.51; the on/off effect diff on 2 real corpora per family reviewed and committed | gates GREEN on both engines; the effect diff of 2 js corpora reviewed and committed (reading 7) | **Gates:** the same 10 gates GREEN on both engines on the RC tree. **Effect diff:** the nestjs-rw and node-express VT vs V0 diffs were reviewed in full by the blind audit (every changed edge: Sdf90d3ba, Sa6373c20, S4102f2b4, Sc55c0dbd); zod was sampled (S9cb36c5c, S9beb2383). None is **committed**: the edge-level diffs are outside the repository and S6 commits nothing. Not established | **no** | s6d/gates/; s6d/delta/; RESULTS.md 2.6 |
| 13 | G6: E holds; the witness/parity report shows no engine-specific typed edge | E (A1) <= V0; differing typed tuples only those PD-1 lists; no engine-specific typed edge (reading 6) | **E holds:** zod 32 <= 36 (raw 89 vs 74); nestjs-rw and node-express 0 <= 0. zod's C1 tuples are 454 vs 456 and its C2 tuples 512 vs 514: this fails as registered and holds with PD-1. **Engine-specific typed edges remain on zod:** engdiff counts 23 engine-specific changes (0 created by VT, 4 converged, 18 carried). Among them are 6 test-caller `Mocker.* → Mocker.pick` edges regraded INFERRED → EXTRACTED on 0.8.51 only. The shipped-policy typed tuples differ by 8 (0.8.51 only): PD-1's 2 plus those 6, which PD-1 does not list | **no** | g3/par/summary.json (`shipped_policy_only51`); s6d/engdiff/colinhacks__zod_VT.json; RESULTS.md 3.3, 3.5 |
| 14 | R1: no question loses a STRICT or SPEC search-union-impact atom on any corpus (matched state, threshold 0); the 33-question STRICT-union macro >= V0; js: no zod-wing atom loss | judged on the measured arm VT (reading 1) | **zod wing:** 0 atoms lost on both engines, VT vs V0 and VPT vs VP (macro 0.2232 = 0.2232). **33 Python questions:** the measured arm VT loses 7 atoms (g39) and 4 (g51); macro 0.5300 -> 0.5306, 0.5127 -> 0.5161 | **no** | s6d/R/r1_all.json; RESULTS.md 5.2-5.3 |
| 15 | R2: self MRR >= V0 (tolerance 0) on both replicated pairs; hit@15 drops by at most 1 query | MRR >= V0; hit@15 net drop <= 1 | VT 0.3823 -> 0.3928 (g39), 0.3898 -> 0.4047 (g51); hit@15 net -1 | yes | s6d/R/self/summary.json |
| 16 | R3: live impact(limit=30) STRICT atoms on the 4 multi_hop questions >= V0 | >= V0 | 24 -> 26 on both engines | yes | s6d/R/r1_all.json |
| 17 | Drift void rule | not fired | not fired (zod VT: 124 / 144 nodes changed, max x5.85, 79 / 87 candidates; no candidate newly fused-top-3 for 3 questions) | yes | s6d/drift/colinhacks__zod_VT_<eng>.txt |
| 18 | S: the `self` oracle passes unchanged; `self-graphify-build` passes, or diverges only at rank >= 3 with every divergence attributed to audited-true edge changes in build/ | self oracle PASS; no rank-1/2 divergence; every in-degree-changing edge change in build/ audited true (added TRUE / removed FALSE) | self oracle PASS on g39 and g51; self-graphify-build REGRESSION in 5/5 queries, first divergent rank 3 (never 1 or 2) on both engines; attribution NOT established: of the 81 in-degree changes VT makes in build/ (75 added, all jedi-confirmed; 6 removed, all refuted) the audit judged 46 (40 added TRUE, 6 removed FALSE) and never judged 35 additions, among them 6 of the 7 added edges on the diverging nodes' own in-degree; the lock-query and RRF-query swaps move nodes whose in-degree did not change | **no** | live-0839/out/_summary/S.md, S_attribution.md; live-0851/out/s0/div_VT*.txt; RESULTS.md section 6 |
| 19 | P: sync wall time +<= 20% on fastapi and this repo; the pass alone <= 15 s on this repo, both engines | sync wall and the pass's seconds from the sync log, median of 3, both engines (reading 8) | **Not established.** The registered sync-log measurement was not made. Offline (supporting): sync wall fastapi +0.8% / −4.0%, self +7.3% / +6.6%; the typed pass on self 7.73 / 9.56 s | **no** | s6d/p/p_summary.json; RESULTS.md 7 |

**D3-js FAILS.** The criteria that do not hold:
- G5: the effect diff is not committed.
- G6: zod's engine-specific test-caller typed edges, under the strict reading.
- R1: the measured arm VT loses Python atoms.
- S: attribution not established on VT.
- P: not established.

Every js-specific criterion holds: G1, G2, G3, G4, the zod wing of R1, R2, R3 and the drift void rule. S and R1 were
measured only on VT, which links Python, TypeScript and JavaScript together. The STAGE-0 corpus is Python only and
VT's changes there are all Python-caller edges, 35 of whose 75 additions were never audited. Every R1 loss is a
Python-question atom.

§8.7 "Any R1 atom loss" applies to the measured arm: DO NOT SHIP. No other §8.7 item fires for the js family: no
rank-1/2 divergence, no audited-TRUE removal, EXTRACTED refuted ≤ 1%, D3-G1 +16.4 / +17.0 points, and E's
symmetric difference not worse than V0.

## D1-py — family-scoped PRECISE, Python (VPA vs VA, the fallback; VPT vs VT reported)

| # | Criterion | Threshold | Measured | Holds | Evidence |
|---|---|---|---|---|---|
| 1 | Candidate selection (design 7.1; §8.7 fallback sentence; reading 9 — selects the arm, not a criterion) | VPT if D3-py passed, else the pre-registered fallback VPA | D3-py failed, so D1-py is decided on VPA vs VA; VPT vs VT is reported for the record | selection: VPA | DECISION.md D3-py |
| 2 | G1 (VPA vs VA): confirmed pairs >= the non-precise graph on every corpus x engine | tolerance 0 | VPA = VA on every py corpus x engine (VPT = VT likewise) | yes | s6d/gtable.json |
| 3 | G2 (VPA vs VA): refuted share not higher anywhere; pooled share down >= 50% relative to V0 | not higher; pooled <= 0.5 x V0 | not higher anywhere (click 0.21828 vs 0.21833 g39, 0.1978 vs 0.1980 g51; requests and flask equal); pooled py 0.9434 -> 0.1139 (g39), 0.9554 -> 0.1428 (g51), down 88% / 85% | yes | s6d/gtable.json |
| 4 | G3 (VPA): EXTRACTED refuted <= 1% on every corpus; audited EXTRACTED precision >= 95% (LB >= 90%) | <= 0.01 on every py corpus; >= 0.95, LB >= 0.90 per corpus x engine (reading 2) | EXTRACTED refuted 0 on every py corpus (self 0/15228), so the first half holds. The audited EXTRACTED precision fails its LB per corpus on both engines: requests 38/40 (LB 0.835), click 38/40 (LB 0.835), self 39/40 (LB 0.871). flask, werkzeug, fastapi and STAGE-0 are 40/40 (LB 0.912). Pooled it would hold (275/280 = 0.982, LB 0.959) | **no** | s6d/gtable.json; s6d/audit/scores.json; RESULTS.md 4.3 |
| 5 | G4 (VPA, py): AMBIGUOUS share down >= 25 points on fastapi and this repo | >= 25.0 points vs VA | fastapi 0.8512 -> 0.1826 (-66.9) g39, 0.8511 -> 0.1221 (-72.9) g51; self 0.9857 -> 0.4928 (-49.3), 0.9856 -> 0.3324 (-65.3) | yes | s6d/gtable.json |
| 6 | G5 (VPA): P1b makes the Python probe fixture identical on both engines; the symmetric difference is <= V0's | probe identical; E (A1) <= V0 on every corpus | probe tree: the identical 9 tuples on 0.8.39 and 0.8.51 (precise_fixes_gates H, GREEN on both); symmetric difference FAILS on fastapi: 56 > 1 (110 engine-specific changes created by PRECISE: pathlib.Path() calls in scripts/ and tests/ bound INFERRED to fastapi params.Path on 0.8.51 only) | **no** | s6d/gates/precise_fixes_gates_py*.txt; s6d/engdiff/fastapi__fastapi_VPA.txt |
| 7 | R1 (VPA vs VA, as in D3) | 0 atoms lost; macro >= base | 0 lost on both engines; macro 0.5300 = 0.5300 (g39), 0.5127 -> 0.5188 (g51, 4 gains) | yes | s6d/R/r1_all.json |
| 8 | R2 (VPA, py): self MRR >= V0 + 0.017 in matched state, replicated; hit@15 drops by at most 1 query | MRR >= V0 + 0.017 on both replicas, both engines | g39 0.3823 -> 0.4010 (+0.0187) holds on both replicas; g51 0.3898 -> 0.3994 (+0.0096) FAILS on both replicas; hit@15 net 0 | **no** | s6d/R/self/summary.json |
| 9 | S: the `self` oracle passes unchanged; `self-graphify-build` passes, or diverges only at rank >= 3 with every divergence attributed to audited-true edge changes in build/ | self oracle PASS; no rank-1/2 divergence; every in-degree-changing edge change in build/ audited true (added TRUE / removed FALSE) | self oracle PASS on both engines; self-graphify-build diverges in 5/5 queries, first divergent rank 3 (g39) and ranks 4, 10, 8, 9, 3 (g51), never 1 or 2; attribution NOT established: VPA removes 196 edges in build/ (all refuted); the audit judged 73 (all FALSE = correct removals) and never judged 123 | **no** | live-0839/out/_summary/S.md; live-0851/out/s0/div_VPA*.txt; RESULTS.md section 6 |
| 10 | P: map step plus push passes <= 1.5x the precise-off time | map step plus push passes, VPA vs VA, from the sync log, fastapi and self, both engines (reading 8) | **Not established.** The map step is not timed on its own, and no sync-log measurement was made. The offline upper bound (supporting) is x1.26 / x1.09 (g39, fastapi / self) and x1.08 / x1.05 (g51); the offline totals are x0.97–x1.04 | **no** | s6d/p/p_summary.json; RESULTS.md 7 |

**D1-py FAILS** on the fallback VPA. The criteria that do not hold:
- G3: the audited EXTRACTED LB fails per corpus.
- G5: engine parity is worse than V0 on fastapi (56 > 1).
- R2: self MRR is below V0 + 0.017 on 0.8.51 (+0.0096, both replicas).
- S: attribution not established.
- P: not established.

§8.7 "Engine-parity worse than V0" and "Self MRR … below V0 + 0.017 (D1-py)" apply: DO NOT SHIP. **PRECISE stays
opt-in for Python and the failure is recorded** (§8.7's last rule).

The primary VPT vs VT, reported for the record:
- G1, G2 and G4 hold: confirmed pairs equal, refuted share lower, AMBIGUOUS down 57.6 / 57.6 points on fastapi and
  49.8 / 66.0 on self.
- G3: EXTRACTED refuted is 0 and the pooled audited EXTRACTED precision is 278/280 and 277/280. Per corpus, requests
  (38/40, LB 0.835) and self on g51 (39/40, LB 0.871) fall below LB 0.90.
- E holds (fastapi 1 ≤ 1).
- R1 holds: VPT vs VT loses 0 atoms.
- R2 holds against V0 (+0.0298 / +0.0225) but not against VT on 0.8.51 (+0.0076).
- S is not established.
- VPT cannot be D1's candidate without D3-py.

## D1-js — family-scoped PRECISE, TypeScript / JavaScript (VPA vs VA, the fallback; VPT vs VT reported)

| # | Criterion | Threshold | Measured | Holds | Evidence |
|---|---|---|---|---|---|
| 1 | Candidate selection (design 7.1; §8.7 fallback sentence; reading 9 — selects the arm, not a criterion) | VPT if D3-js passed, else the fallback VPA = PRECISE[js] + audit (+ the TS re-key) | D3-js failed, so D1-js is decided on VPA vs VA; VPT vs VT is reported for the record | selection: VPA | DECISION.md D3-js |
| 2 | G1 (VPA vs VA): confirmed pairs >= the non-precise graph; zod recovers the 14 OK/DIRTY edges | tolerance 0; 14 edges | VPA = VA on zod, nestjs-rw, node-express; zod: the 14 OK/DIRTY call sites hold exactly 14 INFERRED edges to the value nodes (const OK :166, const DIRTY :163) on both engines (VPT likewise) | yes | s6d/gtable.json; s6d/dumps/colinhacks__zod_VPA_<eng>.json |
| 3 | G2 (VPA vs VA): refuted share not higher anywhere; pooled share down >= 50% relative to V0 | not higher; pooled <= 0.5 x V0 | not higher (zod 0.3387 -> 0.2088, 0.3322 -> 0.2124); pooled js 0.3210 -> 0.1955 (-39%) g39, 0.3161 -> 0.2000 (-37%) g51: FAILS the 50% reduction (VPT: -81% / -83%) | **no** | s6d/gtable.json |
| 4 | G3 (VPA): EXTRACTED refuted <= 1% on every corpus; audited EXTRACTED precision >= 95% (LB >= 90%) | <= 0.01 on every js corpus; >= 0.95, LB >= 0.90 per corpus x engine (reading 2) | zod EXTRACTED refuted 40/247 = 16.2% (g39), 40/254 = 15.7% (g51). Audited EXTRACTED precision on zod is 34/40 (LB 0.709) and 33/40 (LB 0.681); pooled js it is 50/56 = 0.893 (LB 0.785) and 49/56 = 0.875 (LB 0.764). VPT for comparison: refuted 1/309 and 1/349; audited 54/56 (LB 0.879) and 55/56 (LB 0.906) | **no** | s6d/gtable.json; s6d/audit/scores.json; s6d/audit/pooled.json |
| 5 | G5 (VPA): the symmetric difference is <= V0's | E (A1) <= V0 on the js corpora (reading 1) | zod 36 <= 36; nestjs-rw, node-express 0 <= 0. The arm's fastapi failure (56 > 1) is a Python-caller effect on a py corpus and counts under D1-py | yes | s6d/engdiff/ |
| 6 | R1 (as in D3; judged on the measured arm, reading 1) | 0 atoms lost; macro >= base; js: no zod-wing atom loss | VPA vs VA: 0 atoms lost on the 33 Python questions and the 4 zod-wing questions, both engines; macro 0.5300 = 0.5300 (g39), 0.5127 -> 0.5188 (g51) | yes | s6d/R/r1_all.json |
| 7 | S: the `self` oracle passes unchanged; `self-graphify-build` passes, or diverges only at rank >= 3 with every divergence attributed to audited-true edge changes in build/ | self oracle PASS; no rank-1/2 divergence; every in-degree-changing edge change in build/ audited true (added TRUE / removed FALSE) | self oracle PASS on both engines; self-graphify-build diverges in 5/5 queries, first divergent rank 3 (g39) and ranks 4, 10, 8, 9, 3 (g51), never 1 or 2; attribution NOT established: VPA removes 196 edges in build/ (all refuted); the audit judged 73 (all FALSE = correct removals) and never judged 123 | **no** | live-0839/out/_summary/S.md; live-0851/out/s0/div_VPA*.txt; RESULTS.md section 6 |
| 8 | Section 8.7: any audited-TRUE removal | 0 | 5 on zod in VPA vs VA (and in VPT vs VT), both engines: the type-alias half of the OK fan-out (`_parse` -> `OK` :Class at parseUtil.ts:165), audited TRUE by both reviewers | **no** | s6d/audit/scores.json (S377850c6, Sf11af230, S57615359, Se94029a8) |
| 9 | P: map step plus push passes <= 1.5x the precise-off time | map step plus push passes, VPA vs VA, from the sync log, fastapi and self, both engines (reading 8) | **Not established.** The map step is not timed and no sync-log measurement was made. The offline upper bound (supporting) is x1.05–x1.26 | **no** | s6d/p/p_summary.json; RESULTS.md 7 |

**D1-js FAILS** on the fallback VPA. The criteria that do not hold:
- G2: the pooled refuted share falls only 39% / 37%.
- G3: EXTRACTED refuted is 16.2% / 15.7% on zod, and the audited EXTRACTED precision is 0.893 / 0.875 (per key
  34/40 and 33/40).
- S: attribution not established.
- P: not established.
- Five audited-TRUE removals on zod.

§8.7 "Any audited-TRUE removal" and "EXTRACTED refuted above 1% on any corpus" apply: DO NOT SHIP. **PRECISE stays
opt-in for TypeScript / JavaScript and the failure is recorded.** The primary VPT vs VT also has the five audited-TRUE removals
and an audited EXTRACTED LB of 0.879 on 0.8.39, and cannot ship without D3-js. The five removals are the type-alias
half of zod's `OK` fan-out (RESULTS.md §4.6): PRECISE keeps each call site's edge to the `const OK` value node (the
14 OK / DIRTY edges G1 asks for are recovered), and the reviewers judged the removed edge to the merged
`type OK` declaration TRUE; the audit decides, so they count.

## D2 — CALLS_PLUS on top of PRECISE + typed (V9 vs VPT)

| # | Criterion | Threshold | Measured | Holds | Evidence |
|---|---|---|---|---|---|
| 1 | G1: confirmed pairs +3% relative on 4 of 5 corpora, both engines | >= +3% on >= 4 of requests, flask, click, werkzeug, fastapi, each engine | 3 of 5 on both engines: requests +5.20% / +5.01%, werkzeug +4.50% / +4.34%, fastapi +3.35% / +3.34%; flask +2.49% / +2.51%, click +2.90% / +2.56% (STAGE-0 +2.58%, self +2.30%) | **no** | s6d/gtable.json |
| 2 | G2: refuted share not up | V9 <= VPT | not up anywhere | yes | s6d/gtable.json |
| 3 | G3: audited addition precision >= 90% (LB >= 85%), :Module and :Class callers reported separately; additions from non-production callers = 0 | >= 0.90, LB >= 0.85; 0 non-production additions | py pooled 77/93 = 0.828 (LB 0.739) on each engine (click 15/21, self 31/40, requests 20/21, flask 11/11; :Module/:Class callers 31 TRUE / 16 FALSE, function/method callers 46/0); non-production additions 57 (flask 1, click 1, werkzeug 41, fastapi 14) on both engines | **no** | s6d/audit/pooled.json; s6d/delta_summary.json |
| 4 | R1: no family loses an atom; macro >= V0 | 0 lost; macro >= V0 | py: 16 lost (g39), 7 (g51) vs V0; macro 0.5300 -> 0.5143, 0.5127 -> 0.5093; js: 0 | **no** | s6d/R/r1_all.json |
| 5 | R2: self MRR >= V0 | >= V0 | 0.3823 -> 0.4252 (g39), 0.3898 -> 0.4331 (g51), both replicas | yes | s6d/R/self/summary.json |
| 6 | S: the `self` oracle passes unchanged; `self-graphify-build` passes, or diverges only at rank >= 3 with every divergence attributed to audited-true edge changes in build/ | self oracle PASS; no rank-1/2 divergence; every in-degree-changing edge change in build/ audited true (added TRUE / removed FALSE) | self oracle PASS on both engines; self-graphify-build diverges in 5/5 queries, first divergent rank 3 (never 1 or 2); attribution NOT established: V9 changes 290 edges in build/; the audit judged 113 and never judged 177 | **no** | live-0839/out/_summary/S.md; live-0851/out/s0/div_V9*.txt; RESULTS.md section 6 |

**D2 FAILS** (G1, G3, R1, S). `DEEPMAP_GRAPHIFY_CALLS_PLUS` stays off, as design §7.2 expected.

## §8.7 — results that mean DO NOT SHIP, applied

| §8.7 item | Where it fires |
|---|---|
| Any audited-TRUE removal | D1-js (5 on zod, VPA vs VA and VPT vs VT, both engines). Not D0, D3 (VT vs V0, VPT vs VP: 0), D1-py, D2 |
| Any R1 atom loss | D3-py and D3-js on the measured arm VT (7 / 4; VPT vs VP 6 / 6; all Python-question atoms, the zod wing loses none); D2 (V9 16 / 7). Not D0; not D1 (VPA vs VA, VPT vs VT: 0) |
| Self MRR below V0 (D3), or below V0 + 0.017 (D1-py) | D1-py VPA on 0.8.51 (+0.0096). Not D3 (VT ≥ V0 on both engines) |
| EXTRACTED refuted above 1% on any corpus | D1-js VPA (zod 16.2% / 15.7%). Not VT or VPT anywhere (≤ 0.32%) |
| D3-G1 below +10 points | none (py +28.3 / +27.4, js +16.4 / +17.0) |
| Engine-parity worse than V0 | D1-py VPA (fastapi 56 > 1). Not VF, VT, VPT, V9 |
| A voided run that cannot be made exact | none: all ten VOID pairs were redone deterministically |
| The `self` oracle moving | none (PASS for every variant on both engines) |
| A rank-1 or rank-2 `self-graphify-build` divergence not attributed to a true edge change | none (every divergence is at rank ≥ 3) |
| The drift void rule firing | none |
| P budgets exceeded | not established either way. The registered sync-log P was not measured, so P does not hold for D3 or D1 (reading 8). No measurement exceeds a budget: offline, median of 3, low load, the sync wall is fastapi +0.8% / −4.0% and self +7.3% / +6.6%, and the typed pass on self takes 7.73 / 9.56 s |
| A grammar canary failing on either CI image for a wave-1 language | not measured by S6 (S5's Linux container suites); D0's filter reads file extensions only and does not depend on a grammar |
| G3 failing on audit when the reference resolver is unreliable | none: no corpus reaches a 10% resolver error rate (max 6.9% on one zod key; RESULTS.md §4.5), so no G delta is voided |

## The fallback rules, applied

- D3-py fails and the D1-py fallback (VPA) fails: PRECISE + audit does not ship for Python; PRECISE stays opt-in,
  the failure is recorded (this file).
- D3-js fails and the D1-js fallback (VPA) fails: the same for TypeScript / JavaScript.
- D2 fails: CALLS_PLUS stays off.

## The flip combination and the confirmatory run (VRC)

**Flip (S8, after the VRC passes):** `DEEPMAP_CALLS_FAMILY_FILTER` on by default — `build/sync.py`
`_CALLS_FAMILY_FILTER_DEFAULT = True`, and the knob moves from `sync_status._DEFAULT_OFF_KNOBS` to
`_DEFAULT_ON_KNOBS` (`calls_family_filter_gates` section A locks the two together). Unchanged:
`typed_calls._TYPED_DEFAULT_LANGS` and `_AUDIT_DEFAULT_LANGS` stay empty, `graphify_extract._PRECISE_DEFAULT_FAMILIES`
stays empty, `DEEPMAP_GRAPHIFY_CALLS_PLUS` stays off.

**VRC = `DEEPMAP_CALLS_FAMILY_FILTER=1`** (`variants.py run VRC --set DEEPMAP_CALLS_FAMILY_FILTER=1 -- …`). It runs
once, on the RC tree, on both engines, and re-runs four layers:
- **R1:** the 33 Python and 4 zod-wing questions, matched state, with a round trip back to V0 after the VRC state.
- **R2:** `self`, two fresh replicas; MRR ≥ V0 and hit@15 drop ≤ 1.
- **S:** `stage0_refreeze --check --env DEEPMAP_CALLS_FAMILY_FILTER=1`.
- **P, as registered:** sync wall time from the sync log against V0 on fastapi and self, median of 3, both engines, with
  the machine load recorded. The filter has no pass of its own, so the "pass alone ≤ 15 s" bullet has nothing to time.

If the VRC fails, nothing flips: attribute and re-register.

The VRC flags equal VF's. The VF arm already holds R1 and S on both engines, and R2 on 0.8.39 (MRR equal to V0,
hit@15 unchanged). R2 on 0.8.51 and P have not been measured for these flags; the VRC must establish them.

**Release of 2026-10-25:** the VRC was NOT run for this release (time), so nothing flipped:
`_CALLS_FAMILY_FILTER_DEFAULT` stays `False` and every call-linking knob ships opt-in. D0's pass stands as recorded; the
flip waits for this VRC on a later release.

## What would change these outcomes (recorded failures; not decisions)

- **D3-js** fails on five criteria:
  - R1 and S, measured only on the combined VT;
  - G5, the effect diffs are not committed;
  - G6, the shipped policy keys zod's test callers differently on the two engines;
  - P, not measured from the sync log.

  This DECISION is final for this RC. Any route forward is a new registration, and it needs all of the following:
  - a registered js-only typed variant (TYPED and AUDIT on typescript,javascript). It makes no edge change in build/
    and so cannot move `self-graphify-build`. Measure it on R, S and P from the sync log.
  - the effect diffs and audit sheets placed in the repository for the maintainer to commit;
  - a G6 outcome on the shipped policy's test-caller typed tuples: either they agree across the engines, or a reading
    registered before data.
- **D3-py**: the spot bullet needs test / example / docs callers that the caller policy deliberately never adds; the
  R1 losses (click `Choice.convert`, `Parameter.value_from_envvar`, `Group.parse_args`; werkzeug
  `Accept.best_match`, `LanguageAccept.best_match`) are arm-3 rank shifts at V0 ranks 9–15.
- **D1-py**: fastapi's 55 `pathlib.Path(...)` call sites bind to `fastapi.params.Path` on 0.8.51 only under
  PRECISE (VPT avoids it by removing the calls as external); self MRR +0.0096 on 0.8.51.
- **D1-js**: zod's PRECISE EXTRACTED keys without typed linking (40 refuted), and the type-alias `OK` removals.

## Reconciliation of the two derivations (2026-10-25)

Two derivations reached these decisions independently:
- **The first** wrote this file and RESULTS.md. Its evidence is `<work>/s6d/`.
- **The second** worked from the same raw artifacts without reading the first's files. Its evidence is
  `<work>/derive-indep2/out/`.

Both reached the same outcome for every decision and the same flip combination. Every difference between them was
checked against the raw artifacts and is resolved below.

### Criterion verdicts that differed

1. **D3-js R1 (scope).**
   - First derivation: read per family (the zod wing only), so R1 holds.
   - Second derivation: read on the measured arm VT, which loses 7 Python atoms on 0.8.39 and 4 on 0.8.51, so R1
     fails.
   - Resolved: **fails** (reading 1). This is the strict reading. It is also the only exact computation, since no
     js-only arm was measured. And it treats R1 the way both derivations already treated S, which is likewise
     measured only on VT.
2. **D3 G5.**
   - First derivation: holds.
   - Second derivation: not established, because it had no gate record.
   - Verified here: the ten gate logs in `s6d/gates/` ran on `prep/rc/build`, whose code hash was re-computed here
     as 304c3f11…. They ran under Python 3.13.14 with graphify 0.8.39 and with 0.8.51, and all are GREEN.
   - The effect-diff half is not established, because the diffs are not committed (reading 7).
   - Resolved: **not established** for D3-py and D3-js.
3. **D3 P and D1 P.**
   - First derivation: holds, measured offline at low load (`s6d/p/`).
   - Second derivation: not measured as registered. It had not seen `s6d/p/`.
   - Verified here: the offline set is a median of 3 with the load recorded (median 12.5–21.5%). It is not the
     sync-log measurement §8.4 registers, and the D1 map step is not timed on its own.
   - Resolved: **not established** (reading 8). The offline numbers stay as supporting data. No outcome depends on P.
4. **D1 candidate rule.**
   - First derivation: a criterion that does not hold.
   - Second derivation: a selection that holds.
   - Resolved: **a selection, not a criterion** (reading 9). D1 is decided on VPA vs VA for both families.
5. **D1-js S and P.** The second derivation's D1-js list left them out. Resolved: both apply. S fails and P is not
   established.

### Flagged readings resolved by the strict rule

In each of these, both derivations had taken the lenient reading as their verdict.

6. **D3 G6.**
   - Both derivations: holds, through PD-1 or because VT creates no divergence. Both flagged the list reading as the
     stricter alternative.
   - Resolved: **fails** for D3-py and D3-js (reading 6). PD-1's "Not covered" paragraph agrees: any differing typed
     tuple other than its two fails G6 as registered.
   - engdiff's engine-specific changes for VT on common endpoints: requests 1, flask 1, werkzeug 11, self 36, zod 23.
     The other corpora have none.
   - Shipped-policy typed tuples on one engine only (g39-only / g51-only): requests 0/15, flask 7/4, click 0/76,
     werkzeug 2/31, fastapi 0/1, zod 0/8. zod's 8 include PD-1's 2.
   - The second derivation's "click 50" is the length of the parity gate's list, which is capped at 50 entries. The
     gate's own count (`typed_all_shipped.diff`) is 76.
7. **D3 G3, removed edges.**
   - Both derivations: holds, pooled (py 194/194, LB 0.981). The first flagged the per-corpus reading.
   - Resolved: per corpus × engine, with the registered `removed_all` (reading 2).
   - D3-py **fails**: requests LB 0.824, flask 0.723, STAGE-0 0.610, every audited removal FALSE.
   - D3-js holds: zod 0.912.
8. **D1-py G3, audited EXTRACTED precision.**
   - Both derivations: holds, pooled (275/280, LB 0.959). The second flagged a population-weighted LB of 0.875.
   - Resolved: per corpus × engine (reading 2), so it **fails**: requests 38/40 (LB 0.835), click 38/40 (LB 0.835),
     self 39/40 (LB 0.871).
9. **R2 hit@15.**
   - Both derivations: the net drop, so R2 holds.
   - Kept. The registered `measure_self.compare` computes the net drop (`hit15_net_drop <= 1`), so the reading comes
     from the registered tool and is not in doubt.
   - Read gross (VT loses 2 queries), R2 would fail for D3. D3 fails regardless.
10. **S attribution.**
    - Both derivations: per edge, so S fails. Kept.
    - The sample-based reading would let S hold for VT. In build/, VT vs V0 has 40/40 audited additions TRUE (LB
      0.912) and 6/6 removals FALSE, and all 75 additions are jedi-confirmed.
    - Even under that reading, D3-js would still fail on R1, G5, G6 and P.

### Factual differences, checked against the artifacts

11. **The harness gate is registered.** `tests/call_linking_harness_gates.py` is registered in `tests/run_all.py`
    (line 444, with a 600 s timeout entry at line 65). KITFIX's "still not registered" note predates that edit; the
    first derivation repeated it.
12. **Kit hash.**
    - `e3f30d61…` is what the scheme recorded in `prep/kit_hash.json` gives when relpaths are sorted case-insensitively,
      as Windows `Path` ordering does.
    - Byte order gives `0c820153…`. That is the figure live-0851 reported as irreproducible.
    - Every per-file hash matches, so the RC kit's identity holds and no run is affected.
13. **Audit keys.** There are 94 keys, and both derivations re-drew all of them identically. The second derivation's
    "84" is its non-self run; its 10 self keys ran separately.
14. **Live state counts.**
    - On 0.8.39, 48 live exports (42 variant states plus 6 V0 states) equal the offline dumps.
    - On 0.8.51, 48 of 48 graded fingerprints match (the lane's xcheck).
    - All round trips are exact: 54 for the corpora, 18 for `self`.
    - The 0.8.51 lane returned to V0 once per chain of variants rather than after each variant. Each state's own
      matched state is verified (0 nodes re-embedded, no FTS or vector-index re-create, live `:CALLS` equal to its
      offline dump), and every chain's round trip reproduced V0's capture exactly. So §8.4 R's VOID condition is not
      met.
15. **D3-G1, py, 0.8.51.** The gain is +27.40 points exactly (322/1175). The second derivation's +27.41 subtracted
    rounded shares.
16. **D3-G4 under VPT vs VP.** Every FALSE row in the EXTRACTED sample is a PRECISE key that was already EXTRACTED in
    the VP base: requests 2 per engine, self 1 on 0.8.51, zod 2 / 1. None is a typed edge.
17. **The 0.8.39 lane's `S.md`.** It shows the env of VT, VPT, VPA and V9 as `()`. That is a rendering artifact:
    `logs/S_<V>.log` shows each variant's flags applied (`[refreeze] --env: …`).

### Robustness: which readings could change an outcome

| Decision | Under the strict readings (this file) | Under any alternative reading |
|---|---|---|
| D0 | PASS | PASS: every criterion holds under every reading considered |
| D3-py | FAIL | FAIL: G1's spots (1 of 5) and R1 (7 / 4 atoms) fail under every reading |
| D3-js | FAIL | FAIL unless ALL FIVE of R1, G5, G6, S and P are read leniently at once (R1 per family, the effect diff counted as committed with RESULTS.md, G6 through PD-1, S from the audit sample, P offline). Both derivations rejected the sample-based S |
| D1-py | FAIL | FAIL: G5 (fastapi 56 > 1) and R2 (+0.0096 on 0.8.51) fail under every reading |
| D1-js | FAIL | FAIL: G3 (EXTRACTED refuted 16.2% / 15.7% on zod) and the 5 audited-TRUE removals fail under every reading. G2 (−39% / −37%) fails with the family's own pool |
| D2 | FAIL | FAIL: G1 (3 of 5), G3 (0.828; 57 non-production additions) and R1 fail under every reading |

The flip combination is therefore `DEEPMAP_CALLS_FAMILY_FILTER=1` alone, under every reading except the all-lenient
D3-js combination. Even under that combination, TYPED and AUDIT for typescript,javascript would first need a VRC with
exactly those flags, which has never run.

## Open points (not affecting an outcome)

1. **The zod OK / DIRTY losses (D1-js).**
   - Both reviewers judged the five sampled removals `_parse -> OK` TRUE. The target is the `type OK<T>` :Class node
     at parseUtil.ts:165, read as one merged declaration with `const OK` (:166). The TypeScript resolver graded them
     refuted.
   - PRECISE keeps every call site's edge to the value node.
   - The audit decides (§8.4 A), so the five removals count. D1-js also fails on EXTRACTED refuted 16% and on the
     pooled refuted reduction.
2. **D1 R2 base.**
   - Against V0 (the literal reading): VPT holds on both engines (+0.0298, +0.0225); VPA fails on 0.8.51 (+0.0096).
   - Against the non-precise graph: VPT fails on 0.8.51 (+0.0076 vs VT). VPA vs VA cannot be computed on 0.8.51,
     because VA was not captured on self there.
   - The outcome is the same either way.
3. **Reviewer independence.**
   - The two reviewers of each group agreed on every TRUE / FALSE item: 2,558 distinct items. The one exception is
     click `echo → PacifyFlushWrapper.flush` (`480e347aa8b1`), which both marked UNSURE on 4 sheets.
   - Their reasons are worded independently: identical reason strings in g1 57 of 2,700, g2 4 of 2,375, g3 0 of
     2,443.
   - Every key re-draws identically from the data dumps. Independence beyond that cannot be checked from the
     artifacts.
4. **The kit changed after the first RC run** (kitfix: offline_calls.py and README.md; kit e3f30d61 -> 5dead487).
   - Ruling 1 takes `call_linking.seconds` out of the determinism check, and the change touches nothing else, so no
     run is voided.
   - Between the RC kit (`prep/rc`) and `kitfix/kit`, only offline_calls.py differs; every scorer is byte-identical.
5. **gt_self65.json A-C05.** In the RC, `build/sync.py::_os_lock` is an alias; the def is `sync_status.py::os_lock`.
   The entry was used as registered. Both live lanes also scored a re-baselined copy, and every metric is identical.
6. **Coverage gaps.**
   - On `self`, VF, VA and VP were not captured on 0.8.51.
   - On 0.8.51, VA and VP were not run through S.
   - Lane g2 ran no ablations and drew no V9 audit sheets for werkzeug, fastapi or STAGE-0, so D2's audited precision
     pools requests, flask, click and self. D2 fails on the audited corpora regardless.
7. **The grammar canary.** A grammar canary on the CI images (§8.7) is not an S6 measurement; S5's Linux container
   suites own it. D0's filter reads file extensions only.
8. **Unused directories.** `<work>/audit-A` and `audit-B` at the work-root level are not part of the
   audit input and were not used.
9. **Housekeeping.**
   - Both derivations kept to their own directories under `<work>/`.
   - Of the repository's files, only RESULTS.md and DECISION.md were written. The first derivation's imports of the
     kit rewrote untracked bytecode in `benchmark/call_linking/__pycache__`.
