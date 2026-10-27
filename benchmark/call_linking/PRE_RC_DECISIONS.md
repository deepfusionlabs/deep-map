# Call linking — decisions recorded before RC (the DECISION.md route)

PREREG.md's header sets the rules: thresholds are never edited after data; a change after the first RC run is
recorded in DECISION.md with its reason; RESULTS.md and DECISION.md (slice S6, under
`benchmark/results/call-linking-<date>/`) are written once, against PREREG. When pilot data near the RC show that a
registered criterion will fail on something already understood, the answer is not a second amendment of that
criterion. The decision is recorded here, before the first RC run, and S6 copies each entry verbatim into
DECISION.md under its id. An entry here:

- changes no criterion and no threshold of PREREG (§8 stays verbatim and hash-locked; the amendments stay A1 and A2);
- names exactly what it covers — a list, never a rule — in the machine block at the end, which
  `engdiff.py` `typed_tuple_verdict` applies mechanically (`tests/call_linking_harness_gates.py` locks the list);
- is not decision data: the RC runs are. Whatever the RC data show that an entry does not list is judged by the
  registered criterion. No entry is added or widened after the first RC run.

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
