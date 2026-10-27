# Call linking — RESULTS (S6, the release-candidate run)

Written once on 2026-10-25 by the S6 decision lane, against [PREREG.md](PREREG.md) (sha256 `d1a7959b53b5…`, the
registered file, unchanged since registration) and its verbatim §8. The decisions are in [DECISION.md](DECISION.md);
this file holds the measurements they read. Nothing here is pilot data. Every number below was recomputed by this lane
with the registered kit's own scorers (`compare_calls.py`, `delta.py`, `engdiff.py`, `impact_spot.py`,
`impact_offline.py`, `indegree_drift.py`, `audit_sample.py`, `score_m.py`, `measure_self.py`, `run_variant.py`) from
the lanes' raw artifacts; where a lane reported the same number, the two agree.

**Reconciled 2026-10-25.** A second, independent derivation (`<work>/derive-indep2/out/`; `<work>/` is the measurement run's local work
directory, and its raw artifacts are not shipped) recomputed the
G, A, E, R and S layers from the same raw artifacts.
- Every number it shares with this file agrees.
- The places where the two derivations reported different figures or had different evidence were checked against the
  artifacts. The results are in §0, §3.5, §4.3, §6, §7 and §9.
- No measurement was redone.
- How the decisions read these numbers is in DECISION.md ("Readings", "Reconciliation").

Engines are written g39 (graphify 0.8.39, the dev end) and g51 (graphify 0.8.51, the CI end). "py" is the Python
family (requests, flask, click, werkzeug, fastapi, the STAGE-0 build/ corpus, this repository = "self"); "js" is
TypeScript / JavaScript (zod, nestjs-realworld = "nestjs-rw", node-express-boilerplate = "node-express").

## 0. What counts as data

- **RC code.** `build/*.py` sha256 `304c3f11bfcff6d128d391f6a6557fa62bbdfd10aab9f6475e6cbc471ddb404e` (43 files,
  PREREG §1). Every G / E / A dump used here stamps it. The live R and S layers ran from the RC runtime tree
  `<work>/prep/rc` (566 files, tree sha256 `52cee616…`, kit sha256 `e3f30d61…`), re-verified by both
  live lanes after their runs. `offline_calls.py codehash --build prep/rc/build` re-computes `304c3f11…`.
- **Kit hash ordering.** The kit hash `e3f30d61…` is computed under the scheme recorded in `prep/kit_hash.json`: a
  sha256 over the kit's files, each contributing its relpath, a NUL, its bytes and a NUL.
  - The recorded value is reproduced when relpaths are sorted case-insensitively, as Windows `Path` ordering does.
  - Byte-order sorting gives `0c820153…`, the value live-0851 reported as irreproducible.
  - All 21 per-file hashes match, so the kit's identity holds.
  - Between the RC kit and `kitfix/kit`, only `offline_calls.py` differs; every scorer is byte-identical.
- **RC drift (ruling 2).** After §1 was filled, the maintainer's working tree changed `build/deepmap_cli.py`,
  `build/port_registry.py` and `build/provision.py` (the NEO4J_URI Bolt-only scheme rule and wording; current
  `build/*.py` hash `14e3b01e…`). `kitfix/rc_attestation.json`: **GRAPH-IDENTICAL** — requests and zod, V0 and VT,
  both engines, two runs each, 8/8 pairs byte-identical and 32/32 comparisons with the RC data dumps identical
  (wall clock, `code` stamp and `corpus.root` removed). The RC dumps stand for the current tree's graph.
- **Determinism (ruling 1).** `kitfix/determinism.json`: 572 a/b dump pairs over lanes g1/g2/g3; 562 DETERMINISTIC
  and data (RC code, exit 0, the pinned engine); 458 of them differ only in `call_linking.seconds` (the typed pass's
  own wall time, P-layer data); 10 VOID. 95 extra a~c comparisons and 128 cells measured in two or more lane dirs
  all agree on graph content.
- **The 10 VOID pairs (not used anywhere below).** All are engine fallbacks to ast: graphify hit its 3600 s timeout
  in a run that spanned the Modern Standby of 15:56–17:57 (ruling 3). Each is superseded by a DETERMINISTIC redone
  pair: click VF g39 (g1/ck void_first), flask VF g39 (g1/fl void_first), requests VT+t g39 (g1/rq void_first),
  STAGE-0 VA g39 and g51 (g2/m39a, g2/m51a), fastapi V0 g39 and g51 (g2/m39b, g2/m51b), gin VF g39 (g3/sm),
  zod V0 g39 and g51 (g3/z39, g3/z51). Also not data: g3's first `self` V0 attempt (stopped during the standby),
  g2's aborted first STAGE-0 VA redo and its parallel P set A.
- **The decision dumps.** For each of 13 corpora × 9 decision variants (V0, VF, VA, VT, VT−a, VP, VPA, VPT, V9) × 2
  engines, run a of a DETERMINISTIC data pair, wall clock stripped (`offline_calls.strip_wall_clock`):
  234 dumps, `<work>/s6d/dumps/` + `manifest.json` (every pair of a cell agreeing on nodes, edges and
  counts; every run asserted RC code, graphify of its engine end, no `void`, no INVARIANT).
- **Cross-file EXTRACTED (design §4).** 0 on every decision dump: `compare_calls` counts 0 on all 180 scored dumps,
  and every data run of every corpus exited 0, never 4 (INVARIANT).
- **Audit keys.** All 94 audit keys were re-drawn by `audit_sample.py sample` from the decision dumps (seed 20261022,
  n 40, label = corpus id): 94/94 identical to the lanes' keys, so the blind reviews apply to data. The second
  derivation re-drew the same 94 with `audit_sample.draw`, in two runs: 84 keys, then the 10 `self` keys. Every key
  matched in rows, buckets, grades, resolver verdicts and strata.
- **Live layers.** R: 6 question corpora + `self` (two fresh replicas) per engine, every variant an edge-only
  re-sync in matched state (§5.1). S: `tests/stage0_refreeze.py --check --env <flags>` per candidate on both engines.
- **Machine.** 12 logical CPUs. The G lanes ran at 57–100% CPU (G, E and A are deterministic); the R lanes in matched
  state; the P layer was re-measured by this lane at low load (§7), with the load sampled before and after every run.

## 1. Reference truth (PREREG §2)

| Corpus | Resolver | Files | Call sites | Sites with an in-corpus definition | Parse / resolver errors | Truth pairs g39 / g51 (library prefix) |
|---|---|---|---|---|---|---|
| requests | jedi 0.20.1 | 37 | 2,687 | 1,272 | 0 / 0 | 896 / 896 (254) |
| flask | jedi 0.20.1 | 83 | 3,966 | 1,283 | 0 / 0 | 895 / 895 (252) |
| click | jedi 0.20.1 | 63 | 6,134 | 3,037 | 0 / 0 | 2,198 / 2,198 (533) |
| werkzeug | jedi 0.20.1 | 138 | 8,504 | 4,133 | 0 / 0 | 2,521 / 2,521 (902) |
| fastapi | jedi 0.20.1 | 1,129 | 16,681 | 5,230 | 0 / 19 | 4,488 / 4,488 (457) |
| STAGE-0 | jedi 0.20.1 | 34 | 5,144 | 1,109 | 0 / 0 | 815 / 815 (whole corpus) |
| self | jedi 0.20.1 | 349 | 121,984 | 45,343 | 0 / 1 | 17,154 / 17,154 (5,551) |
| zod | TypeScript 5.6.3 (`--conditions=@zod/source`, packages/zod/src) | 286 | 33,237 | 16,859 | – | 2,656 / 2,695 |
| nestjs-rw | TypeScript 5.6.3 | 35 | 360 | 51 | – | 38 / 38 |
| node-express | TypeScript 5.6.3 `--js` | 38 | 592 | 113 | – | 49 / 49 |

The 19 fastapi and 1 self resolver errors are sites jedi raised on (left unresolved). Truth files:
g1/{rq,fl,ck}/out/<corpus>/<corpus>_truth.json, g2/truth/, g3/tr/.

## 2. G — the graph, offline (PREREG §8.4 G)

### 2.1 Per corpus × engine × variant (`compare_calls.py`, whole graph; library receiver-form = the D3-G1 forms in the library prefix)

`refuted share` = refuted / edges; `AMBIGUOUS share` over all edges; `lib receiver-form` = hits / truth pairs of
the receiver forms (py: `name_attr` + `recv`; js: `name_attr` + `recv` + `this`) whose caller is in the library prefix.
Evidence: `<work>/s6d/score/<corpus>/score_<V>_<eng>_{all,src}.json`, `s6d/gtable.json`.

| Corpus | Eng | Var | edges | confirmed | refuted (share) | recall | AMBIGUOUS share | EXTRACTED n / refuted | lib receiver-form |
|---|---|---|---|---|---|---|---|---|---|
| requests | g39 | V0 | 551 | 373 | 17 (0.0309) | 0.4163 | 0.0000 | 0 / 0 | 4/21 |
| requests | g39 | VF | 551 | 373 | 17 (0.0309) | 0.4163 | 0.0000 | 0 / 0 | 4/21 |
| requests | g39 | VA | 551 | 373 | 17 (0.0309) | 0.4163 | 0.0544 | 0 / 0 | 4/21 |
| requests | g39 | VT | 566 | 404 | 0 (0.0000) | 0.4509 | 0.0212 | 92 / 0 | 14/21 |
| requests | g39 | VT-a | 566 | 404 | 0 (0.0000) | 0.4509 | 0.0000 | 92 / 0 | 14/21 |
| requests | g39 | VP | 551 | 373 | 17 (0.0309) | 0.4163 | 0.0000 | 170 / 17 | 4/21 |
| requests | g39 | VPA | 551 | 373 | 17 (0.0309) | 0.4163 | 0.0544 | 140 / 0 | 4/21 |
| requests | g39 | VPT | 566 | 404 | 0 (0.0000) | 0.4509 | 0.0212 | 142 / 0 | 14/21 |
| requests | g39 | V9 | 587 | 425 | 0 (0.0000) | 0.4743 | 0.0204 | 148 / 0 | 15/21 |
| requests | g51 | V0 | 565 | 388 | 17 (0.0301) | 0.4330 | 0.0000 | 0 / 0 | 4/21 |
| requests | g51 | VF | 565 | 388 | 17 (0.0301) | 0.4330 | 0.0000 | 0 / 0 | 4/21 |
| requests | g51 | VA | 565 | 388 | 17 (0.0301) | 0.4330 | 0.0513 | 0 / 0 | 4/21 |
| requests | g51 | VT | 580 | 419 | 0 (0.0000) | 0.4676 | 0.0190 | 92 / 0 | 14/21 |
| requests | g51 | VT-a | 580 | 419 | 0 (0.0000) | 0.4676 | 0.0000 | 92 / 0 | 14/21 |
| requests | g51 | VP | 565 | 388 | 17 (0.0301) | 0.4330 | 0.0000 | 169 / 17 | 4/21 |
| requests | g51 | VPA | 565 | 388 | 17 (0.0301) | 0.4330 | 0.0513 | 140 / 0 | 4/21 |
| requests | g51 | VPT | 580 | 419 | 0 (0.0000) | 0.4676 | 0.0190 | 142 / 0 | 14/21 |
| requests | g51 | V9 | 601 | 440 | 0 (0.0000) | 0.4911 | 0.0183 | 148 / 0 | 15/21 |
| flask | g39 | V0 | 330 | 270 | 18 (0.0545) | 0.3017 | 0.0788 | 0 / 0 | 9/66 |
| flask | g39 | VF | 330 | 270 | 18 (0.0545) | 0.3017 | 0.0788 | 0 / 0 | 9/66 |
| flask | g39 | VA | 330 | 270 | 18 (0.0545) | 0.3017 | 0.1606 | 0 / 0 | 9/66 |
| flask | g39 | VT | 331 | 281 | 9 (0.0272) | 0.3140 | 0.1148 | 77 / 0 | 18/66 |
| flask | g39 | VT-a | 331 | 281 | 9 (0.0272) | 0.3140 | 0.0332 | 77 / 0 | 18/66 |
| flask | g39 | VP | 330 | 270 | 18 (0.0545) | 0.3017 | 0.0788 | 183 / 1 | 9/66 |
| flask | g39 | VPA | 330 | 270 | 18 (0.0545) | 0.3017 | 0.1606 | 156 / 0 | 9/66 |
| flask | g39 | VPT | 331 | 281 | 9 (0.0272) | 0.3140 | 0.1148 | 156 / 0 | 18/66 |
| flask | g39 | V9 | 342 | 288 | 9 (0.0263) | 0.3218 | 0.1111 | 161 / 0 | 18/66 |
| flask | g51 | V0 | 329 | 269 | 18 (0.0547) | 0.3006 | 0.0790 | 0 / 0 | 10/66 |
| flask | g51 | VF | 329 | 269 | 18 (0.0547) | 0.3006 | 0.0790 | 0 / 0 | 10/66 |
| flask | g51 | VA | 329 | 269 | 18 (0.0547) | 0.3006 | 0.1611 | 0 / 0 | 10/66 |
| flask | g51 | VT | 329 | 279 | 9 (0.0274) | 0.3117 | 0.1155 | 77 / 0 | 18/66 |
| flask | g51 | VT-a | 329 | 279 | 9 (0.0274) | 0.3117 | 0.0334 | 77 / 0 | 18/66 |
| flask | g51 | VP | 329 | 269 | 18 (0.0547) | 0.3006 | 0.0790 | 183 / 1 | 10/66 |
| flask | g51 | VPA | 329 | 269 | 18 (0.0547) | 0.3006 | 0.1611 | 156 / 0 | 10/66 |
| flask | g51 | VPT | 329 | 279 | 9 (0.0274) | 0.3117 | 0.1155 | 156 / 0 | 18/66 |
| flask | g51 | V9 | 340 | 286 | 9 (0.0265) | 0.3196 | 0.1118 | 161 / 0 | 18/66 |
| click | g39 | V0 | 742 | 540 | 162 (0.2183) | 0.2457 | 0.2898 | 0 / 0 | 45/97 |
| click | g39 | VF | 742 | 540 | 162 (0.2183) | 0.2457 | 0.2898 | 0 / 0 | 45/97 |
| click | g39 | VA | 742 | 540 | 162 (0.2183) | 0.2457 | 0.3032 | 0 / 0 | 45/97 |
| click | g39 | VT | 673 | 587 | 48 (0.0713) | 0.2671 | 0.1426 | 184 / 0 | 77/97 |
| click | g39 | VT-a | 673 | 587 | 48 (0.0713) | 0.2671 | 0.1278 | 184 / 0 | 77/97 |
| click | g39 | VP | 733 | 540 | 160 (0.2183) | 0.2457 | 0.2674 | 371 / 0 | 45/97 |
| click | g39 | VPA | 733 | 540 | 160 (0.2183) | 0.2457 | 0.2810 | 361 / 0 | 45/97 |
| click | g39 | VPT | 664 | 587 | 46 (0.0693) | 0.2671 | 0.1160 | 384 / 0 | 77/97 |
| click | g39 | V9 | 685 | 604 | 46 (0.0672) | 0.2748 | 0.1124 | 401 / 0 | 77/97 |
| click | g51 | V0 | 818 | 616 | 162 (0.1980) | 0.2803 | 0.2628 | 0 / 0 | 45/97 |
| click | g51 | VF | 818 | 616 | 162 (0.1980) | 0.2803 | 0.2628 | 0 / 0 | 45/97 |
| click | g51 | VA | 818 | 616 | 162 (0.1980) | 0.2803 | 0.2751 | 0 / 0 | 45/97 |
| click | g51 | VT | 749 | 663 | 48 (0.0641) | 0.3016 | 0.1282 | 184 / 0 | 77/97 |
| click | g51 | VT-a | 749 | 663 | 48 (0.0641) | 0.3016 | 0.1148 | 184 / 0 | 77/97 |
| click | g51 | VP | 809 | 616 | 160 (0.1978) | 0.2803 | 0.2423 | 371 / 0 | 45/97 |
| click | g51 | VPA | 809 | 616 | 160 (0.1978) | 0.2803 | 0.2546 | 361 / 0 | 45/97 |
| click | g51 | VPT | 740 | 663 | 46 (0.0622) | 0.3016 | 0.1041 | 384 / 0 | 77/97 |
| click | g51 | V9 | 761 | 680 | 46 (0.0604) | 0.3094 | 0.1012 | 401 / 0 | 77/97 |
| werkzeug | g39 | V0 | 1567 | 1078 | 251 (0.1602) | 0.4276 | 0.3172 | 0 / 0 | 44/198 |
| werkzeug | g39 | VF | 1567 | 1078 | 251 (0.1602) | 0.4276 | 0.3172 | 0 / 0 | 44/198 |
| werkzeug | g39 | VA | 1567 | 1078 | 251 (0.1602) | 0.4276 | 0.3382 | 0 / 0 | 44/198 |
| werkzeug | g39 | VT | 1616 | 1201 | 180 (0.1114) | 0.4764 | 0.2679 | 314 / 0 | 120/198 |
| werkzeug | g39 | VT-a | 1616 | 1201 | 180 (0.1114) | 0.4764 | 0.2488 | 314 / 0 | 120/198 |
| werkzeug | g39 | VP | 1368 | 1078 | 53 (0.0387) | 0.4276 | 0.1601 | 609 / 8 | 44/198 |
| werkzeug | g39 | VPA | 1368 | 1078 | 53 (0.0387) | 0.4276 | 0.1842 | 576 / 0 | 44/198 |
| werkzeug | g39 | VPT | 1481 | 1201 | 46 (0.0311) | 0.4764 | 0.1634 | 590 / 0 | 120/198 |
| werkzeug | g39 | V9 | 1564 | 1255 | 46 (0.0294) | 0.4978 | 0.1547 | 630 / 0 | 120/198 |
| werkzeug | g51 | V0 | 1615 | 1132 | 251 (0.1554) | 0.4490 | 0.3053 | 0 / 0 | 59/198 |
| werkzeug | g51 | VF | 1615 | 1132 | 251 (0.1554) | 0.4490 | 0.3053 | 0 / 0 | 59/198 |
| werkzeug | g51 | VA | 1615 | 1132 | 251 (0.1554) | 0.4490 | 0.3251 | 0 / 0 | 59/198 |
| werkzeug | g51 | VT | 1654 | 1245 | 180 (0.1088) | 0.4939 | 0.2588 | 314 / 0 | 125/198 |
| werkzeug | g51 | VT-a | 1654 | 1245 | 180 (0.1088) | 0.4939 | 0.2406 | 314 / 0 | 125/198 |
| werkzeug | g51 | VP | 1416 | 1132 | 53 (0.0374) | 0.4490 | 0.1518 | 608 / 8 | 59/198 |
| werkzeug | g51 | VPA | 1416 | 1132 | 53 (0.0374) | 0.4490 | 0.1744 | 576 / 0 | 59/198 |
| werkzeug | g51 | VPT | 1519 | 1245 | 46 (0.0303) | 0.4939 | 0.1560 | 590 / 0 | 125/198 |
| werkzeug | g51 | V9 | 1602 | 1299 | 46 (0.0287) | 0.5153 | 0.1479 | 630 / 0 | 125/198 |
| fastapi | g39 | V0 | 7213 | 1374 | 5597 (0.7760) | 0.3061 | 0.8483 | 0 / 0 | 23/50 |
| fastapi | g39 | VF | 7213 | 1374 | 5597 (0.7760) | 0.3061 | 0.8483 | 0 / 0 | 23/50 |
| fastapi | g39 | VA | 7213 | 1374 | 5597 (0.7760) | 0.3061 | 0.8512 | 0 / 0 | 23/50 |
| fastapi | g39 | VT | 3638 | 1405 | 2117 (0.5819) | 0.3131 | 0.6581 | 277 / 0 | 30/50 |
| fastapi | g39 | VT-a | 3638 | 1405 | 2117 (0.5819) | 0.3131 | 0.6528 | 277 / 0 | 30/50 |
| fastapi | g39 | VP | 1709 | 1374 | 93 (0.0544) | 0.3061 | 0.1703 | 738 / 2 | 23/50 |
| fastapi | g39 | VPA | 1709 | 1374 | 93 (0.0544) | 0.3061 | 0.1826 | 717 / 0 | 23/50 |
| fastapi | g39 | VPT | 1560 | 1405 | 39 (0.0250) | 0.3131 | 0.0821 | 715 / 0 | 30/50 |
| fastapi | g39 | V9 | 1614 | 1452 | 39 (0.0242) | 0.3235 | 0.0793 | 758 / 0 | 30/50 |
| fastapi | g51 | V0 | 7214 | 1375 | 5597 (0.7759) | 0.3064 | 0.8482 | 0 / 0 | 23/50 |
| fastapi | g51 | VF | 7214 | 1375 | 5597 (0.7759) | 0.3064 | 0.8482 | 0 / 0 | 23/50 |
| fastapi | g51 | VA | 7214 | 1375 | 5597 (0.7759) | 0.3064 | 0.8511 | 0 / 0 | 23/50 |
| fastapi | g51 | VT | 3639 | 1406 | 2117 (0.5818) | 0.3133 | 0.6579 | 277 / 0 | 30/50 |
| fastapi | g51 | VT-a | 3639 | 1406 | 2117 (0.5818) | 0.3133 | 0.6527 | 277 / 0 | 30/50 |
| fastapi | g51 | VP | 1655 | 1375 | 93 (0.0562) | 0.3064 | 0.1094 | 738 / 2 | 23/50 |
| fastapi | g51 | VPA | 1655 | 1375 | 93 (0.0562) | 0.3064 | 0.1221 | 717 / 0 | 23/50 |
| fastapi | g51 | VPT | 1561 | 1406 | 39 (0.0250) | 0.3133 | 0.0820 | 715 / 0 | 30/50 |
| fastapi | g51 | V9 | 1615 | 1453 | 39 (0.0241) | 0.3238 | 0.0793 | 758 / 0 | 30/50 |
| STAGE-0 | g39 | V0 | 858 | 661 | 196 (0.2284) | 0.8110 | 0.3485 | 0 / 0 | 7/144 |
| STAGE-0 | g39 | VF | 858 | 661 | 196 (0.2284) | 0.8110 | 0.3485 | 0 / 0 | 7/144 |
| STAGE-0 | g39 | VA | 858 | 661 | 196 (0.2284) | 0.8110 | 0.3485 | 0 / 0 | 7/144 |
| STAGE-0 | g39 | VT | 927 | 736 | 190 (0.2050) | 0.9031 | 0.3128 | 40 / 0 | 81/144 |
| STAGE-0 | g39 | VT-a | 927 | 736 | 190 (0.2050) | 0.9031 | 0.3128 | 40 / 0 | 81/144 |
| STAGE-0 | g39 | VP | 662 | 661 | 0 (0.0000) | 0.8110 | 0.0015 | 651 / 0 | 7/144 |
| STAGE-0 | g39 | VPA | 662 | 661 | 0 (0.0000) | 0.8110 | 0.0015 | 651 / 0 | 7/144 |
| STAGE-0 | g39 | VPT | 737 | 736 | 0 (0.0000) | 0.9031 | 0.0014 | 651 / 0 | 81/144 |
| STAGE-0 | g39 | V9 | 756 | 755 | 0 (0.0000) | 0.9264 | 0.0013 | 667 / 0 | 84/144 |
| STAGE-0 | g51 | V0 | 858 | 661 | 196 (0.2284) | 0.8110 | 0.3485 | 0 / 0 | 7/144 |
| STAGE-0 | g51 | VF | 858 | 661 | 196 (0.2284) | 0.8110 | 0.3485 | 0 / 0 | 7/144 |
| STAGE-0 | g51 | VA | 858 | 661 | 196 (0.2284) | 0.8110 | 0.3485 | 0 / 0 | 7/144 |
| STAGE-0 | g51 | VT | 927 | 736 | 190 (0.2050) | 0.9031 | 0.3128 | 40 / 0 | 81/144 |
| STAGE-0 | g51 | VT-a | 927 | 736 | 190 (0.2050) | 0.9031 | 0.3128 | 40 / 0 | 81/144 |
| STAGE-0 | g51 | VP | 662 | 661 | 0 (0.0000) | 0.8110 | 0.0015 | 651 / 0 | 7/144 |
| STAGE-0 | g51 | VPA | 662 | 661 | 0 (0.0000) | 0.8110 | 0.0015 | 651 / 0 | 7/144 |
| STAGE-0 | g51 | VPT | 737 | 736 | 0 (0.0000) | 0.9031 | 0.0014 | 651 / 0 | 81/144 |
| STAGE-0 | g51 | V9 | 756 | 755 | 0 (0.0000) | 0.9264 | 0.0013 | 667 / 0 | 84/144 |
| self | g39 | V0 | 590219 | 15195 | 561191 (0.9508) | 0.8858 | 0.9855 | 0 / 0 | 199/599 |
| self | g39 | VF | 589732 | 15195 | 561149 (0.9515) | 0.8858 | 0.9855 | 0 / 0 | 199/599 |
| self | g39 | VA | 590219 | 15195 | 561191 (0.9508) | 0.8858 | 0.9857 | 0 / 0 | 199/599 |
| self | g39 | VT | 583236 | 15424 | 554060 (0.9500) | 0.8991 | 0.9838 | 2265 / 0 | 324/599 |
| self | g39 | VT-a | 583236 | 15424 | 554060 (0.9500) | 0.8991 | 0.9836 | 2265 / 0 | 324/599 |
| self | g39 | VP | 30410 | 15195 | 3734 (0.1228) | 0.8858 | 0.4862 | 15430 / 13 | 199/599 |
| self | g39 | VPA | 30410 | 15195 | 3734 (0.1228) | 0.8858 | 0.4928 | 15228 / 0 | 199/599 |
| self | g39 | VPT | 30474 | 15424 | 3587 (0.1177) | 0.8991 | 0.4855 | 15154 / 0 | 324/599 |
| self | g39 | V9 | 30875 | 15778 | 3587 (0.1162) | 0.9198 | 0.4792 | 15429 / 0 | 332/599 |
| self | g51 | V0 | 582492 | 15174 | 561174 (0.9634) | 0.8846 | 0.9854 | 0 / 0 | 195/599 |
| self | g51 | VF | 582031 | 15174 | 561132 (0.9641) | 0.8846 | 0.9853 | 0 / 0 | 195/599 |
| self | g51 | VA | 582492 | 15174 | 561174 (0.9634) | 0.8846 | 0.9856 | 0 / 0 | 195/599 |
| self | g51 | VT | 575525 | 15403 | 554056 (0.9627) | 0.8979 | 0.9836 | 2245 / 0 | 320/599 |
| self | g51 | VT-a | 575525 | 15403 | 554056 (0.9627) | 0.8979 | 0.9834 | 2245 / 0 | 320/599 |
| self | g51 | VP | 23071 | 15174 | 3730 (0.1617) | 0.8846 | 0.3241 | 15397 / 13 | 195/599 |
| self | g51 | VPA | 23071 | 15174 | 3730 (0.1617) | 0.8846 | 0.3324 | 15207 / 0 | 195/599 |
| self | g51 | VPT | 23135 | 15403 | 3583 (0.1549) | 0.8979 | 0.3232 | 15133 / 0 | 320/599 |
| self | g51 | V9 | 23536 | 15757 | 3583 (0.1522) | 0.9186 | 0.3177 | 15408 / 0 | 328/599 |
| zod | g39 | V0 | 564 | 261 | 191 (0.3387) | 0.0983 | 0.4716 | 0 / 0 | 96/2143 |
| zod | g39 | VF | 564 | 261 | 191 (0.3387) | 0.0983 | 0.4716 | 0 / 0 | 96/2143 |
| zod | g39 | VA | 564 | 261 | 191 (0.3387) | 0.0983 | 0.5213 | 0 / 0 | 96/2143 |
| zod | g39 | VT | 913 | 654 | 150 (0.1643) | 0.2462 | 0.3176 | 163 / 0 | 428/2143 |
| zod | g39 | VT-a | 913 | 654 | 150 (0.1643) | 0.2462 | 0.2913 | 163 / 0 | 428/2143 |
| zod | g39 | VP | 455 | 261 | 95 (0.2088) | 0.0983 | 0.2044 | 275 / 45 | 96/2143 |
| zod | g39 | VPA | 455 | 261 | 95 (0.2088) | 0.0983 | 0.2659 | 247 / 40 | 96/2143 |
| zod | g39 | VPT | 804 | 654 | 54 (0.0672) | 0.2462 | 0.1455 | 309 / 1 | 428/2143 |
| zod | g39 | V9 | 804 | 654 | 54 (0.0672) | 0.2462 | 0.1455 | 309 / 1 | 428/2143 |
| zod | g51 | V0 | 608 | 301 | 202 (0.3322) | 0.1117 | 0.4375 | 0 / 0 | 102/2159 |
| zod | g51 | VF | 608 | 301 | 202 (0.3322) | 0.1117 | 0.4375 | 0 / 0 | 102/2159 |
| zod | g51 | VA | 608 | 301 | 202 (0.3322) | 0.1117 | 0.4901 | 0 / 0 | 102/2159 |
| zod | g51 | VT | 978 | 729 | 148 (0.1513) | 0.2705 | 0.2863 | 204 / 0 | 449/2159 |
| zod | g51 | VT-a | 978 | 729 | 148 (0.1513) | 0.2705 | 0.2720 | 204 / 0 | 449/2159 |
| zod | g51 | VP | 499 | 301 | 106 (0.2124) | 0.1117 | 0.1864 | 286 / 56 | 102/2159 |
| zod | g51 | VPA | 499 | 301 | 106 (0.2124) | 0.1117 | 0.2505 | 254 / 40 | 102/2159 |
| zod | g51 | VPT | 869 | 729 | 52 (0.0598) | 0.2705 | 0.1231 | 349 / 1 | 449/2159 |
| zod | g51 | V9 | 869 | 729 | 52 (0.0598) | 0.2705 | 0.1231 | 349 / 1 | 449/2159 |
| nestjs-rw | g39 | V0 | 23 | 7 | 0 (0.0000) | 0.1842 | 0.0000 | 0 / 0 | 7/30 |
| nestjs-rw | g39 | VF | 23 | 7 | 0 (0.0000) | 0.1842 | 0.0000 | 0 / 0 | 7/30 |
| nestjs-rw | g39 | VA | 23 | 7 | 0 (0.0000) | 0.1842 | 0.6522 | 0 / 0 | 7/30 |
| nestjs-rw | g39 | VT | 49 | 33 | 0 (0.0000) | 0.8684 | 0.3061 | 7 / 0 | 29/30 |
| nestjs-rw | g39 | VT-a | 49 | 33 | 0 (0.0000) | 0.8684 | 0.0000 | 7 / 0 | 29/30 |
| nestjs-rw | g39 | VP | 23 | 7 | 0 (0.0000) | 0.1842 | 0.0000 | 23 / 0 | 7/30 |
| nestjs-rw | g39 | VPA | 23 | 7 | 0 (0.0000) | 0.1842 | 0.6522 | 8 / 0 | 7/30 |
| nestjs-rw | g39 | VPT | 49 | 33 | 0 (0.0000) | 0.8684 | 0.3061 | 8 / 0 | 29/30 |
| nestjs-rw | g39 | V9 | 49 | 33 | 0 (0.0000) | 0.8684 | 0.3061 | 8 / 0 | 29/30 |
| nestjs-rw | g51 | V0 | 23 | 7 | 0 (0.0000) | 0.1842 | 0.0000 | 0 / 0 | 7/30 |
| nestjs-rw | g51 | VF | 23 | 7 | 0 (0.0000) | 0.1842 | 0.0000 | 0 / 0 | 7/30 |
| nestjs-rw | g51 | VA | 23 | 7 | 0 (0.0000) | 0.1842 | 0.6522 | 0 / 0 | 7/30 |
| nestjs-rw | g51 | VT | 49 | 33 | 0 (0.0000) | 0.8684 | 0.3061 | 7 / 0 | 29/30 |
| nestjs-rw | g51 | VT-a | 49 | 33 | 0 (0.0000) | 0.8684 | 0.0000 | 7 / 0 | 29/30 |
| nestjs-rw | g51 | VP | 23 | 7 | 0 (0.0000) | 0.1842 | 0.0000 | 23 / 0 | 7/30 |
| nestjs-rw | g51 | VPA | 23 | 7 | 0 (0.0000) | 0.1842 | 0.6522 | 8 / 0 | 7/30 |
| nestjs-rw | g51 | VPT | 49 | 33 | 0 (0.0000) | 0.8684 | 0.3061 | 8 / 0 | 29/30 |
| nestjs-rw | g51 | V9 | 49 | 33 | 0 (0.0000) | 0.8684 | 0.3061 | 8 / 0 | 29/30 |
| node-express | g39 | V0 | 8 | 5 | 0 (0.0000) | 0.1020 | 0.0000 | 0 / 0 | 0/19 |
| node-express | g39 | VF | 8 | 5 | 0 (0.0000) | 0.1020 | 0.0000 | 0 / 0 | 0/19 |
| node-express | g39 | VA | 8 | 5 | 0 (0.0000) | 0.1020 | 0.0000 | 0 / 0 | 0/19 |
| node-express | g39 | VT | 23 | 20 | 0 (0.0000) | 0.4082 | 0.0000 | 0 / 0 | 6/19 |
| node-express | g39 | VT-a | 23 | 20 | 0 (0.0000) | 0.4082 | 0.0000 | 0 / 0 | 6/19 |
| node-express | g39 | VP | 8 | 5 | 0 (0.0000) | 0.1020 | 0.0000 | 8 / 0 | 0/19 |
| node-express | g39 | VPA | 8 | 5 | 0 (0.0000) | 0.1020 | 0.0000 | 8 / 0 | 0/19 |
| node-express | g39 | VPT | 23 | 20 | 0 (0.0000) | 0.4082 | 0.0000 | 8 / 0 | 6/19 |
| node-express | g39 | V9 | 23 | 20 | 0 (0.0000) | 0.4082 | 0.0000 | 8 / 0 | 6/19 |
| node-express | g51 | V0 | 8 | 5 | 0 (0.0000) | 0.1020 | 0.0000 | 0 / 0 | 0/19 |
| node-express | g51 | VF | 8 | 5 | 0 (0.0000) | 0.1020 | 0.0000 | 0 / 0 | 0/19 |
| node-express | g51 | VA | 8 | 5 | 0 (0.0000) | 0.1020 | 0.0000 | 0 / 0 | 0/19 |
| node-express | g51 | VT | 23 | 20 | 0 (0.0000) | 0.4082 | 0.0000 | 0 / 0 | 6/19 |
| node-express | g51 | VT-a | 23 | 20 | 0 (0.0000) | 0.4082 | 0.0000 | 0 / 0 | 6/19 |
| node-express | g51 | VP | 8 | 5 | 0 (0.0000) | 0.1020 | 0.0000 | 8 / 0 | 0/19 |
| node-express | g51 | VPA | 8 | 5 | 0 (0.0000) | 0.1020 | 0.0000 | 8 / 0 | 0/19 |
| node-express | g51 | VPT | 23 | 20 | 0 (0.0000) | 0.4082 | 0.0000 | 8 / 0 | 6/19 |
| node-express | g51 | V9 | 23 | 20 | 0 (0.0000) | 0.4082 | 0.0000 | 8 / 0 | 6/19 |

### 2.2 D3-G1: pooled library receiver-form recall (`compare_calls.py pool --family`)

| Family (corpora) | Eng | V0 | VT | VT − V0 | VP | VPT | VPT − VP | V9 |
|---|---|---|---|---|---|---|---|---|
| py (7 corpora) | g39 | 331/1175 = 0.2817 | 664/1175 = 0.5651 | **+28.34 pts** | 331/1175 = 0.2817 | 664/1175 = 0.5651 | +28.34 pts | 676/1175 = 0.5753 |
| py (7 corpora) | g51 | 343/1175 = 0.2919 | 665/1175 = 0.5660 | **+27.40 pts** | 343/1175 = 0.2919 | 665/1175 = 0.5660 | +27.40 pts | 677/1175 = 0.5762 |
| js (3 corpora) | g39 | 103/2192 = 0.0470 | 463/2192 = 0.2112 | **+16.42 pts** | 103/2192 = 0.0470 | 463/2192 = 0.2112 | +16.42 pts | 463/2192 = 0.2112 |
| js (3 corpora) | g51 | 109/2208 = 0.0494 | 484/2208 = 0.2192 | **+16.98 pts** | 109/2208 = 0.0494 | 484/2208 = 0.2192 | +16.98 pts | 484/2208 = 0.2192 |

VF, VA, VP and VPA equal V0 in both pools. Evidence: `s6d/pool/{py,js}_<V>_<eng>.txt`.

**No call form's recall falls** (tolerance 0): every form (bare, self, cls, super, name_attr, recv, ctor, this), whole
graph and library prefix, every corpus and engine, for VT vs V0, VPT vs VP, VPT vs VT, VPA vs VA, V9 vs VPT and VF
vs V0: 0 falls (`s6d/scripts/forms.py` over `s6d/gtable.json`).

### 2.3 Hop-1 spots (`impact_spot.py --base`, PREREG §4 and amendment A2)

| Spot | Corpus | Reference callers | V0 hits g39 / g51 | VT hits g39 / g51 | VPT hits (VP base = V0) | Improved (VT and VPT, both engines) |
|---|---|---|---|---|---|---|
| `MapAdapter.match` | werkzeug | 52 | 3 / 3 | 3 / 3 | 3 / 3 | no |
| `Map.bind` | werkzeug | 61 | 1 / 1 | 1 / 1 | 1 / 1 | no |
| `run_wsgi_app` | werkzeug | 15 | 3 / 3 | 3 / 3 | 3 / 3 | no |
| `Depends` | fastapi | 275 | 16 / 16 | 16 / 16 | 16 / 16 | no |
| `read_records` | STAGE-0 | 8 | 0 / 0 | 8 / 8 | 8 / 8 | **yes** |
| `ZodString._addCheck` | zod | 24 | 0 / 0 | 23 / 23 | 23 / 23 | **yes** |
| `ParseStatus.mergeObjectSync` | zod | 3 | 1 / 1 | 3 / 3 | 3 / 3 | **yes** |
| `ArticleService.findAll` | nestjs-rw | 1 | 0 / 0 | 1 / 1 | 1 / 1 | **yes** |
| `UserService.generateJWT` | nestjs-rw | 2 | 1 / 1 | 2 / 2 | 2 / 2 | **yes** |
| `getUserById` | node-express | 6 | 2 / 2 | 5 / 5 | 5 / 5 | **yes** |

**py: 1 of 5 spots improve; js: 5 of 5**, the same on both engines and for VT and VPT. No spot is saturated at
V0. The four py spots that do not move have their missed reference callers in tests/, examples/ and docs_src/, which
the shipped caller policy (design §2.6: test and non-production callers get corrections only) never adds to.
Evidence: `s6d/spot/<corpus>_<V>_vs_<base>_<eng>.{txt,json}`.

### 2.4 Edge deltas (`delta.py --truth`; resolver verdict confirmed / refuted / unverified; caller class prod / test / nonprod)

| Corpus | Eng | Comparison | edges base -> variant | added (conf/ref/unv; prod/test/nonprod) | removed (conf/ref/unv; prod/test/nonprod) | demoted | promoted | variant EXTRACTED (conf/ref/unv) |
|---|---|---|---|---|---|---|---|---|
| requests | g39 | VT vs V0 | 551 -> 566 | 33 (31/0/2; 17/16/0) | 18 (0/17/1; 2/16/0) | 12 | 91 | 92 (92/0/0) |
| requests | g51 | VT vs V0 | 565 -> 580 | 33 (31/0/2; 17/16/0) | 18 (0/17/1; 2/16/0) | 11 | 91 | 92 (92/0/0) |
| flask | g39 | VT vs V0 | 330 -> 331 | 11 (11/0/0; 11/0/0) | 10 (0/9/1; 9/0/1) | 27 | 83 | 77 (77/0/0) |
| flask | g51 | VT vs V0 | 329 -> 329 | 10 (10/0/0; 10/0/0) | 10 (0/9/1; 9/0/1) | 27 | 83 | 77 (77/0/0) |
| click | g39 | VT vs V0 | 742 -> 673 | 47 (47/0/0; 47/0/0) | 116 (0/114/2; 116/0/0) | 10 | 174 | 184 (184/0/0) |
| click | g51 | VT vs V0 | 818 -> 749 | 47 (47/0/0; 47/0/0) | 116 (0/114/2; 116/0/0) | 10 | 174 | 184 (184/0/0) |
| werkzeug | g39 | VT vs V0 | 1567 -> 1616 | 123 (123/0/0; 122/0/1) | 74 (0/71/3; 16/0/58) | 32 | 314 | 314 (314/0/0) |
| werkzeug | g51 | VT vs V0 | 1615 -> 1654 | 113 (113/0/0; 112/0/1) | 74 (0/71/3; 16/0/58) | 31 | 314 | 314 (314/0/0) |
| fastapi | g39 | VT vs V0 | 7213 -> 3638 | 32 (31/0/1; 29/3/0) | 3607 (0/3480/127; 127/2826/654) | 19 | 309 | 277 (263/0/14) |
| fastapi | g51 | VT vs V0 | 7214 -> 3639 | 32 (31/0/1; 29/3/0) | 3607 (0/3480/127; 127/2826/654) | 19 | 309 | 277 (263/0/14) |
| STAGE-0 | g39 | VT vs V0 | 858 -> 927 | 75 (75/0/0; 75/0/0) | 6 (0/6/0; 6/0/0) | 0 | 40 | 40 (40/0/0) |
| STAGE-0 | g51 | VT vs V0 | 858 -> 927 | 75 (75/0/0; 75/0/0) | 6 (0/6/0; 6/0/0) | 0 | 40 | 40 (40/0/0) |
| self | g39 | VT vs V0 | 590219 -> 583236 | 235 (229/0/6; 229/6/0) | 7218 (0/7131/87; 68/7150/0) | 107 | 2277 | 2265 (2218/0/47) |
| self | g51 | VT vs V0 | 582492 -> 575525 | 235 (229/0/6; 229/6/0) | 7202 (0/7118/84; 68/7134/0) | 107 | 2257 | 2245 (2201/0/44) |
| zod | g39 | VT vs V0 | 564 -> 913 | 393 (393/0/0; 393/0/0) | 44 (0/41/3; 44/0/0) | 24 | 60 | 163 (163/0/0) |
| zod | g51 | VT vs V0 | 608 -> 978 | 428 (428/0/0; 428/0/0) | 58 (0/54/4; 58/0/0) | 14 | 66 | 204 (204/0/0) |
| nestjs-rw | g39 | VT vs V0 | 23 -> 49 | 26 (26/0/0; 26/0/0) | 0 (0/0/0; 0/0/0) | 15 | 7 | 7 (7/0/0) |
| nestjs-rw | g51 | VT vs V0 | 23 -> 49 | 26 (26/0/0; 26/0/0) | 0 (0/0/0; 0/0/0) | 15 | 7 | 7 (7/0/0) |
| node-express | g39 | VT vs V0 | 8 -> 23 | 15 (15/0/0; 15/0/0) | 0 (0/0/0; 0/0/0) | 0 | 0 | 0 (0/0/0) |
| node-express | g51 | VT vs V0 | 8 -> 23 | 15 (15/0/0; 15/0/0) | 0 (0/0/0; 0/0/0) | 0 | 0 | 0 (0/0/0) |
| requests | g39 | VPT vs VP | 551 -> 566 | 33 (31/0/2; 17/16/0) | 18 (0/17/1; 2/16/0) | 12 | 1 | 142 (138/0/4) |
| requests | g51 | VPT vs VP | 565 -> 580 | 33 (31/0/2; 17/16/0) | 18 (0/17/1; 2/16/0) | 11 | 1 | 142 (138/0/4) |
| flask | g39 | VPT vs VP | 330 -> 331 | 11 (11/0/0; 11/0/0) | 10 (0/9/1; 9/0/1) | 27 | 6 | 156 (149/0/7) |
| flask | g51 | VPT vs VP | 329 -> 329 | 10 (10/0/0; 10/0/0) | 10 (0/9/1; 9/0/1) | 27 | 6 | 156 (149/0/7) |
| click | g39 | VPT vs VP | 733 -> 664 | 47 (47/0/0; 47/0/0) | 116 (0/114/2; 116/0/0) | 12 | 15 | 384 (373/0/11) |
| click | g51 | VPT vs VP | 809 -> 740 | 47 (47/0/0; 47/0/0) | 116 (0/114/2; 116/0/0) | 12 | 15 | 384 (373/0/11) |
| werkzeug | g39 | VPT vs VP | 1368 -> 1481 | 123 (123/0/0; 122/0/1) | 10 (0/7/3; 10/0/0) | 38 | 2 | 590 (555/0/35) |
| werkzeug | g51 | VPT vs VP | 1416 -> 1519 | 113 (113/0/0; 112/0/1) | 10 (0/7/3; 10/0/0) | 37 | 2 | 590 (555/0/35) |
| fastapi | g39 | VPT vs VP | 1709 -> 1560 | 32 (31/0/1; 29/3/0) | 181 (0/54/127; 49/64/68) | 21 | 4 | 715 (690/0/25) |
| fastapi | g51 | VPT vs VP | 1655 -> 1561 | 32 (31/0/1; 29/3/0) | 126 (0/54/72; 25/33/68) | 21 | 4 | 715 (690/0/25) |
| STAGE-0 | g39 | VPT vs VP | 662 -> 737 | 75 (75/0/0; 75/0/0) | 0 (0/0/0; 0/0/0) | 0 | 0 | 651 (651/0/0) |
| STAGE-0 | g51 | VPT vs VP | 662 -> 737 | 75 (75/0/0; 75/0/0) | 0 (0/0/0; 0/0/0) | 0 | 0 | 651 (651/0/0) |
| self | g39 | VPT vs VP | 30410 -> 30474 | 235 (229/0/6; 229/6/0) | 171 (0/147/24; 36/135/0) | 274 | 19 | 15154 (14925/0/229) |
| self | g51 | VPT vs VP | 23071 -> 23135 | 235 (229/0/6; 229/6/0) | 171 (0/147/24; 36/135/0) | 262 | 19 | 15133 (14908/0/225) |
| zod | g39 | VPT vs VP | 455 -> 804 | 393 (393/0/0; 393/0/0) | 44 (0/41/3; 44/0/0) | 25 | 0 | 309 (269/1/39) |
| zod | g51 | VPT vs VP | 499 -> 869 | 428 (428/0/0; 428/0/0) | 58 (0/54/4; 58/0/0) | 17 | 0 | 349 (309/1/39) |
| nestjs-rw | g39 | VPT vs VP | 23 -> 49 | 26 (26/0/0; 26/0/0) | 0 (0/0/0; 0/0/0) | 15 | 0 | 8 (7/0/1) |
| nestjs-rw | g51 | VPT vs VP | 23 -> 49 | 26 (26/0/0; 26/0/0) | 0 (0/0/0; 0/0/0) | 15 | 0 | 8 (7/0/1) |
| node-express | g39 | VPT vs VP | 8 -> 23 | 15 (15/0/0; 15/0/0) | 0 (0/0/0; 0/0/0) | 0 | 0 | 8 (5/0/3) |
| node-express | g51 | VPT vs VP | 8 -> 23 | 15 (15/0/0; 15/0/0) | 0 (0/0/0; 0/0/0) | 0 | 0 | 8 (5/0/3) |
| requests | g39 | VPT vs VT | 566 -> 566 | 0 (0/0/0; 0/0/0) | 0 (0/0/0; 0/0/0) | 0 | 50 | 142 (138/0/4) |
| requests | g51 | VPT vs VT | 580 -> 580 | 0 (0/0/0; 0/0/0) | 0 (0/0/0; 0/0/0) | 0 | 50 | 142 (138/0/4) |
| flask | g39 | VPT vs VT | 331 -> 331 | 0 (0/0/0; 0/0/0) | 0 (0/0/0; 0/0/0) | 0 | 79 | 156 (149/0/7) |
| flask | g51 | VPT vs VT | 329 -> 329 | 0 (0/0/0; 0/0/0) | 0 (0/0/0; 0/0/0) | 0 | 79 | 156 (149/0/7) |
| click | g39 | VPT vs VT | 673 -> 664 | 0 (0/0/0; 0/0/0) | 9 (0/2/7; 9/0/0) | 0 | 205 | 384 (373/0/11) |
| click | g51 | VPT vs VT | 749 -> 740 | 0 (0/0/0; 0/0/0) | 9 (0/2/7; 9/0/0) | 0 | 205 | 384 (373/0/11) |
| werkzeug | g39 | VPT vs VT | 1616 -> 1481 | 0 (0/0/0; 0/0/0) | 135 (0/134/1; 23/1/111) | 0 | 312 | 590 (555/0/35) |
| werkzeug | g51 | VPT vs VT | 1654 -> 1519 | 0 (0/0/0; 0/0/0) | 135 (0/134/1; 23/1/111) | 0 | 312 | 590 (555/0/35) |
| fastapi | g39 | VPT vs VT | 3638 -> 1560 | 0 (0/0/0; 0/0/0) | 2078 (0/2078/0; 30/1847/201) | 0 | 549 | 715 (690/0/25) |
| fastapi | g51 | VPT vs VT | 3639 -> 1561 | 0 (0/0/0; 0/0/0) | 2078 (0/2078/0; 30/1847/201) | 0 | 549 | 715 (690/0/25) |
| STAGE-0 | g39 | VPT vs VT | 927 -> 737 | 0 (0/0/0; 0/0/0) | 190 (0/190/0; 190/0/0) | 0 | 611 | 651 (651/0/0) |
| STAGE-0 | g51 | VPT vs VT | 927 -> 737 | 0 (0/0/0; 0/0/0) | 190 (0/190/0; 190/0/0) | 0 | 611 | 651 (651/0/0) |
| self | g39 | VPT vs VT | 583236 -> 30474 | 0 (0/0/0; 0/0/0) | 552762 (0/550473/2289; 5242/547520/0) | 1 | 12889 | 15154 (14925/0/229) |
| self | g51 | VPT vs VT | 575525 -> 23135 | 0 (0/0/0; 0/0/0) | 552390 (0/550473/1917; 5242/547148/0) | 1 | 12888 | 15133 (14908/0/225) |
| zod | g39 | VPT vs VT | 913 -> 804 | 0 (0/0/0; 0/0/0) | 109 (0/96/13; 109/0/0) | 0 | 183 | 309 (269/1/39) |
| zod | g51 | VPT vs VT | 978 -> 869 | 0 (0/0/0; 0/0/0) | 109 (0/96/13; 109/0/0) | 0 | 182 | 349 (309/1/39) |
| nestjs-rw | g39 | VPT vs VT | 49 -> 49 | 0 (0/0/0; 0/0/0) | 0 (0/0/0; 0/0/0) | 0 | 1 | 8 (7/0/1) |
| nestjs-rw | g51 | VPT vs VT | 49 -> 49 | 0 (0/0/0; 0/0/0) | 0 (0/0/0; 0/0/0) | 0 | 1 | 8 (7/0/1) |
| node-express | g39 | VPT vs VT | 23 -> 23 | 0 (0/0/0; 0/0/0) | 0 (0/0/0; 0/0/0) | 0 | 8 | 8 (5/0/3) |
| node-express | g51 | VPT vs VT | 23 -> 23 | 0 (0/0/0; 0/0/0) | 0 (0/0/0; 0/0/0) | 0 | 8 | 8 (5/0/3) |
| requests | g39 | VPA vs VA | 551 -> 551 | 0 (0/0/0; 0/0/0) | 0 (0/0/0; 0/0/0) | 0 | 140 | 140 (136/0/4) |
| requests | g51 | VPA vs VA | 565 -> 565 | 0 (0/0/0; 0/0/0) | 0 (0/0/0; 0/0/0) | 0 | 140 | 140 (136/0/4) |
| flask | g39 | VPA vs VA | 330 -> 330 | 0 (0/0/0; 0/0/0) | 0 (0/0/0; 0/0/0) | 0 | 156 | 156 (149/0/7) |
| flask | g51 | VPA vs VA | 329 -> 329 | 0 (0/0/0; 0/0/0) | 0 (0/0/0; 0/0/0) | 0 | 156 | 156 (149/0/7) |
| click | g39 | VPA vs VA | 742 -> 733 | 0 (0/0/0; 0/0/0) | 9 (0/2/7; 9/0/0) | 0 | 366 | 361 (350/0/11) |
| click | g51 | VPA vs VA | 818 -> 809 | 0 (0/0/0; 0/0/0) | 9 (0/2/7; 9/0/0) | 0 | 366 | 361 (350/0/11) |
| werkzeug | g39 | VPA vs VA | 1567 -> 1368 | 0 (0/0/0; 0/0/0) | 199 (0/198/1; 29/1/169) | 0 | 632 | 576 (540/0/36) |
| werkzeug | g51 | VPA vs VA | 1615 -> 1416 | 0 (0/0/0; 0/0/0) | 199 (0/198/1; 29/1/169) | 0 | 632 | 576 (540/0/36) |
| fastapi | g39 | VPA vs VA | 7213 -> 1709 | 0 (0/0/0; 0/0/0) | 5504 (0/5504/0; 108/4609/787) | 0 | 856 | 717 (692/0/25) |
| fastapi | g51 | VPA vs VA | 7214 -> 1655 | 0 (0/0/0; 0/0/0) | 5559 (0/5504/55; 132/4640/787) | 0 | 911 | 717 (692/0/25) |
| STAGE-0 | g39 | VPA vs VA | 858 -> 662 | 0 (0/0/0; 0/0/0) | 196 (0/196/0; 196/0/0) | 0 | 651 | 651 (651/0/0) |
| STAGE-0 | g51 | VPA vs VA | 858 -> 662 | 0 (0/0/0; 0/0/0) | 196 (0/196/0; 196/0/0) | 0 | 651 | 651 (651/0/0) |
| self | g39 | VPA vs VA | 590219 -> 30410 | 0 (0/0/0; 0/0/0) | 559809 (0/557457/2352; 5274/554535/0) | 1 | 15228 | 15228 (15001/0/227) |
| self | g51 | VPA vs VA | 582492 -> 23071 | 0 (0/0/0; 0/0/0) | 559421 (0/557444/1977; 5274/554147/0) | 1 | 15207 | 15207 (14984/0/223) |
| zod | g39 | VPA vs VA | 564 -> 455 | 0 (0/0/0; 0/0/0) | 109 (0/96/13; 109/0/0) | 0 | 284 | 247 (166/40/41) |
| zod | g51 | VPA vs VA | 608 -> 499 | 0 (0/0/0; 0/0/0) | 109 (0/96/13; 109/0/0) | 0 | 291 | 254 (173/40/41) |
| nestjs-rw | g39 | VPA vs VA | 23 -> 23 | 0 (0/0/0; 0/0/0) | 0 (0/0/0; 0/0/0) | 0 | 8 | 8 (7/0/1) |
| nestjs-rw | g51 | VPA vs VA | 23 -> 23 | 0 (0/0/0; 0/0/0) | 0 (0/0/0; 0/0/0) | 0 | 8 | 8 (7/0/1) |
| node-express | g39 | VPA vs VA | 8 -> 8 | 0 (0/0/0; 0/0/0) | 0 (0/0/0; 0/0/0) | 0 | 8 | 8 (5/0/3) |
| node-express | g51 | VPA vs VA | 8 -> 8 | 0 (0/0/0; 0/0/0) | 0 (0/0/0; 0/0/0) | 0 | 8 | 8 (5/0/3) |
| requests | g39 | V9 vs VPT | 566 -> 587 | 21 (21/0/0; 21/0/0) | 0 (0/0/0; 0/0/0) | 0 | 0 | 148 (144/0/4) |
| requests | g51 | V9 vs VPT | 580 -> 601 | 21 (21/0/0; 21/0/0) | 0 (0/0/0; 0/0/0) | 0 | 0 | 148 (144/0/4) |
| flask | g39 | V9 vs VPT | 331 -> 342 | 11 (7/0/4; 10/0/1) | 0 (0/0/0; 0/0/0) | 0 | 0 | 161 (152/0/9) |
| flask | g51 | V9 vs VPT | 329 -> 340 | 11 (7/0/4; 10/0/1) | 0 (0/0/0; 0/0/0) | 0 | 0 | 161 (152/0/9) |
| click | g39 | V9 vs VPT | 664 -> 685 | 21 (17/0/4; 20/0/1) | 0 (0/0/0; 0/0/0) | 0 | 0 | 401 (387/0/14) |
| click | g51 | V9 vs VPT | 740 -> 761 | 21 (17/0/4; 20/0/1) | 0 (0/0/0; 0/0/0) | 0 | 0 | 401 (387/0/14) |
| werkzeug | g39 | V9 vs VPT | 1481 -> 1564 | 83 (54/0/29; 42/0/41) | 0 (0/0/0; 0/0/0) | 0 | 0 | 630 (577/0/53) |
| werkzeug | g51 | V9 vs VPT | 1519 -> 1602 | 83 (54/0/29; 42/0/41) | 0 (0/0/0; 0/0/0) | 0 | 0 | 630 (577/0/53) |
| fastapi | g39 | V9 vs VPT | 1560 -> 1614 | 54 (47/0/7; 40/0/14) | 0 (0/0/0; 0/0/0) | 0 | 0 | 758 (732/0/26) |
| fastapi | g51 | V9 vs VPT | 1561 -> 1615 | 54 (47/0/7; 40/0/14) | 0 (0/0/0; 0/0/0) | 0 | 0 | 758 (732/0/26) |
| STAGE-0 | g39 | V9 vs VPT | 737 -> 756 | 19 (19/0/0; 19/0/0) | 0 (0/0/0; 0/0/0) | 0 | 0 | 667 (667/0/0) |
| STAGE-0 | g51 | V9 vs VPT | 737 -> 756 | 19 (19/0/0; 19/0/0) | 0 (0/0/0; 0/0/0) | 0 | 0 | 667 (667/0/0) |
| self | g39 | V9 vs VPT | 30474 -> 30875 | 401 (354/0/47; 401/0/0) | 0 (0/0/0; 0/0/0) | 0 | 0 | 15429 (15158/0/271) |
| self | g51 | V9 vs VPT | 23135 -> 23536 | 401 (354/0/47; 401/0/0) | 0 (0/0/0; 0/0/0) | 0 | 0 | 15408 (15141/0/267) |
| zod | g39 | V9 vs VPT | 804 -> 804 | 0 (0/0/0; 0/0/0) | 0 (0/0/0; 0/0/0) | 0 | 0 | 309 (269/1/39) |
| zod | g51 | V9 vs VPT | 869 -> 869 | 0 (0/0/0; 0/0/0) | 0 (0/0/0; 0/0/0) | 0 | 0 | 349 (309/1/39) |
| nestjs-rw | g39 | V9 vs VPT | 49 -> 49 | 0 (0/0/0; 0/0/0) | 0 (0/0/0; 0/0/0) | 0 | 0 | 8 (7/0/1) |
| nestjs-rw | g51 | V9 vs VPT | 49 -> 49 | 0 (0/0/0; 0/0/0) | 0 (0/0/0; 0/0/0) | 0 | 0 | 8 (7/0/1) |
| node-express | g39 | V9 vs VPT | 23 -> 23 | 0 (0/0/0; 0/0/0) | 0 (0/0/0; 0/0/0) | 0 | 0 | 8 (5/0/3) |
| node-express | g51 | V9 vs VPT | 23 -> 23 | 0 (0/0/0; 0/0/0) | 0 (0/0/0; 0/0/0) | 0 | 0 | 8 (5/0/3) |
| requests | g39 | VF vs V0 | 551 -> 551 | 0 (0/0/0; 0/0/0) | 0 (0/0/0; 0/0/0) | 0 | 0 | 0 (0/0/0) |
| requests | g51 | VF vs V0 | 565 -> 565 | 0 (0/0/0; 0/0/0) | 0 (0/0/0; 0/0/0) | 0 | 0 | 0 (0/0/0) |
| flask | g39 | VF vs V0 | 330 -> 330 | 0 (0/0/0; 0/0/0) | 0 (0/0/0; 0/0/0) | 0 | 0 | 0 (0/0/0) |
| flask | g51 | VF vs V0 | 329 -> 329 | 0 (0/0/0; 0/0/0) | 0 (0/0/0; 0/0/0) | 0 | 0 | 0 (0/0/0) |
| click | g39 | VF vs V0 | 742 -> 742 | 0 (0/0/0; 0/0/0) | 0 (0/0/0; 0/0/0) | 0 | 0 | 0 (0/0/0) |
| click | g51 | VF vs V0 | 818 -> 818 | 0 (0/0/0; 0/0/0) | 0 (0/0/0; 0/0/0) | 0 | 0 | 0 (0/0/0) |
| werkzeug | g39 | VF vs V0 | 1567 -> 1567 | 0 (0/0/0; 0/0/0) | 0 (0/0/0; 0/0/0) | 0 | 0 | 0 (0/0/0) |
| werkzeug | g51 | VF vs V0 | 1615 -> 1615 | 0 (0/0/0; 0/0/0) | 0 (0/0/0; 0/0/0) | 0 | 0 | 0 (0/0/0) |
| fastapi | g39 | VF vs V0 | 7213 -> 7213 | 0 (0/0/0; 0/0/0) | 0 (0/0/0; 0/0/0) | 0 | 0 | 0 (0/0/0) |
| fastapi | g51 | VF vs V0 | 7214 -> 7214 | 0 (0/0/0; 0/0/0) | 0 (0/0/0; 0/0/0) | 0 | 0 | 0 (0/0/0) |
| STAGE-0 | g39 | VF vs V0 | 858 -> 858 | 0 (0/0/0; 0/0/0) | 0 (0/0/0; 0/0/0) | 0 | 0 | 0 (0/0/0) |
| STAGE-0 | g51 | VF vs V0 | 858 -> 858 | 0 (0/0/0; 0/0/0) | 0 (0/0/0; 0/0/0) | 0 | 0 | 0 (0/0/0) |
| self | g39 | VF vs V0 | 590219 -> 589732 | 0 (0/0/0; 0/0/0) | 487 (0/42/445; 407/80/0) | 0 | 6 | 0 (0/0/0) |
| self | g51 | VF vs V0 | 582492 -> 582031 | 0 (0/0/0; 0/0/0) | 461 (0/42/419; 407/54/0) | 0 | 6 | 0 (0/0/0) |
| zod | g39 | VF vs V0 | 564 -> 564 | 0 (0/0/0; 0/0/0) | 0 (0/0/0; 0/0/0) | 0 | 0 | 0 (0/0/0) |
| zod | g51 | VF vs V0 | 608 -> 608 | 0 (0/0/0; 0/0/0) | 0 (0/0/0; 0/0/0) | 0 | 0 | 0 (0/0/0) |
| nestjs-rw | g39 | VF vs V0 | 23 -> 23 | 0 (0/0/0; 0/0/0) | 0 (0/0/0; 0/0/0) | 0 | 0 | 0 (0/0/0) |
| nestjs-rw | g51 | VF vs V0 | 23 -> 23 | 0 (0/0/0; 0/0/0) | 0 (0/0/0; 0/0/0) | 0 | 0 | 0 (0/0/0) |
| node-express | g39 | VF vs V0 | 8 -> 8 | 0 (0/0/0; 0/0/0) | 0 (0/0/0; 0/0/0) | 0 | 0 | 0 (0/0/0) |
| node-express | g51 | VF vs V0 | 8 -> 8 | 0 (0/0/0; 0/0/0) | 0 (0/0/0; 0/0/0) | 0 | 0 | 0 (0/0/0) |

Evidence: `s6d/delta/<corpus>_<V>_vs_<base>_<eng>.json`, `s6d/delta_summary.json`.

### 2.5 D0: what the cross-family filter removes

VF removes no edge on requests, flask, click, werkzeug, fastapi, STAGE-0, zod, nestjs-rw or node-express (both
engines). On `self` it removes 487 edges (g39) and 461 (g51); every one is cross-family by the product's own
`edge_resolve.lang_family` (948 of 948), and none is resolver-confirmed (g39: 42 refuted, 445 unverified; g51: 42
refuted, 419 unverified). Confirmed pairs are equal to V0 on every corpus and engine.

### 2.6 The effect diffs on two real corpora per family (D3-G5)

The on/off (VT vs V0) effect diffs of requests and flask (py) and zod and nestjs-rw (js) are the VT vs V0 rows of
§2.4; their blind reviews are sheets rq-01 / rq-02, fl-02 / fl-05 (py), S9cb36c5c / S9beb2383 and
Sdf90d3ba / Sa6373c20 (js) in §4. Summary, g39 / g51:

| Corpus | Edges V0 → VT | Added (all resolver-confirmed unless noted) | Removed | Demoted (audit) | EXTRACTED (all confirmed) |
|---|---|---|---|---|---|
| requests | 551 → 566 / 565 → 580 | 33 (31 confirmed, 2 unverified; 16 are test-caller corrections) | 18 (17 refuted, 1 unverified) | 12 / 11 | 92 |
| flask | 330 → 331 / 329 → 329 | 11 / 10, all confirmed | 10 (9 refuted, 1 unverified) | 27 | 77 |
| zod | 564 → 913 / 608 → 978 | 393 / 428, all confirmed, all production | 44 / 58 (41 / 54 refuted, 3 / 4 unverified) | 24 / 14 | 163 / 204 |
| nestjs-rw | 23 → 49 | 26, all confirmed, all production | 0 | 15 | 7 |

### 2.7 V9 (D2): additions by caller class and caller label

| Corpus | V9 − VPT added (both engines) | from non-production callers | from `:Module` / `:Class` callers | from function / method callers |
|---|---|---|---|---|
| requests | 21 (21 confirmed) | 0 | 7 | 14 |
| flask | 11 (7 confirmed, 4 unverified) | 1 (`examples/celery` `make_celery` → `create_app`) | 8 | 3 |
| click | 21 (17 confirmed, 4 unverified) | 1 (`examples/validation` `validation` → `URL`, EXTRACTED) | 14 | 7 |
| werkzeug | 83 (54 confirmed, 29 unverified) | 41 | 65 | 18 |
| fastapi | 54 (47 confirmed, 7 unverified) | 14 | 46 | 8 |
| STAGE-0 | 19 (19 confirmed) | 0 | 13 | 6 |
| self | 401 (354 confirmed, 47 unverified) | 0 | 170 | 231 |
| zod, nestjs-rw, node-express | 0 | 0 | 0 | 0 |

Confirmed-pair gain V9 vs VPT (D2-G1): requests +5.20% / +5.01%, flask +2.49% / +2.51%, click +2.90% / +2.56%,
werkzeug +4.50% / +4.34%, fastapi +3.35% / +3.34%, STAGE-0 +2.58% / +2.58%, self +2.30% / +2.30% (g39 / g51).

### 2.8 Oracle-seeded 3-hop impact (`impact_offline.py`, the four multi_hop questions, 49 atoms)

| Variant | STRICT g39 | SPEC g39 | STRICT g51 | SPEC g51 |
|---|---|---|---|---|
| V0, VF, VA, VP, VPA | 25/49 | 28/49 | 25/49 | 28/49 |
| VT, VPT, V9 | 27/49 | 30/49 | 27/49 | 30/49 |
| Reference resolver's pairs (ceiling, g39) | 30/49 | 37/49 | | |

Per question: flask-blast-1 STRICT 9/16 for every variant; click-blast-1 5/11 → 7/11 under VT, VPT, V9;
werkzeug-blast-1 4/9 and fastapi-blast-1 7/13 for every variant. Evidence: `s6d/impact/<V>_<eng>.txt`,
`ceiling_g39.txt`.

### 2.9 Arm-3 in-degree drift (`indegree_drift.py`, offline; the void rule with the live captures is in §5.5)

| Corpus | VT g39: nodes changed / max ratio / ≥2× candidates | VT g51 | VPT g39 | V9 g39 |
|---|---|---|---|---|
| requests | 19 / 2.30 / 2 | 19 / 2.30 / 2 | 19 / 2.30 / 2 | 30 / 3.30 / 4 |
| flask | 14 / 2.39 / 1 | 13 / 2.39 / 1 | 14 / 2.39 / 1 | 24 / 2.39 / 2 |
| click | 72 / 2.39 / 7 | 72 / 2.39 / 7 | 78 / 2.39 / 7 | 96 / 2.39 / 10 |
| werkzeug | 75 / 3.40 / 12 | 75 / 3.40 / 7 | 100 / 3.40 / 12 | 160 / 3.40 / 16 |
| fastapi | 225 / 2.39 / 4 | 225 / 2.39 / 4 | 468 / 2.39 / 4 | 505 / 2.39 / 5 |
| STAGE-0 | 44 / 3.20 / 5 | 44 / 3.20 / 5 | 100 / 3.20 / 5 | 114 / 3.20 / 8 |
| self | 487 / 3.64 / 20 | 485 / 3.64 / 20 | 2,460 / 3.64 / 20 | 2,716 / 3.64 / 47 |
| zod | 124 / 5.85 / 79 | 144 / 5.85 / 87 | 177 / 5.85 / 79 | 177 / 5.85 / 79 |

Evidence: `s6d/drift/<corpus>_<V>_<eng>.{txt,json}` (top-20 lists inside).

### 2.10 Smoke corpora (gin, httprouter, serde-json)

Every variant on both engines is a DETERMINISTIC data pair (no crash, typed pass without errors, 0 cross-file
EXTRACTED). The typed and precise variants leave the Go / Rust edge sets unchanged. Edges: gin 1,782 (g39) / 1,693
(g51), httprouter 102, serde-json 1,023. E holds on all three (gin 101 ≤ 101; the others 0 ≤ 0). Longest single
offline dump (lane g3, about 100% CPU, not P data): gin 145 / 179 s, httprouter 18 / 8 s, serde-json 53 / 31 s.

### 2.11 Ablations (reported, not decision data)

VT+t, VT+np and VT[shape] (R1, R2, R3, R4, S, K, M, C) are DETERMINISTIC data pairs on requests, flask, click,
self, zod, nestjs-rw, node-express and the smoke corpora (lanes g1 and g3; lane g2 did not run them on werkzeug,
fastapi and STAGE-0). Their per-corpus numbers are in `g1/extra/numbers_table.txt` and `g3/res/table.txt`; no
decision reads them.

## 3. E — engine parity (PREREG §8.4 E, amendment A1, PD-1)

### 3.1 Typed tuples (`tests/typed_calls_engine_parity_gates.py --corpora` on the RC tree)

| Corpus | Production tuples g39 / g51 (shipped policy) | Every caller (TESTS / NONPROD = 1) | Verdict |
|---|---|---|---|
| requests | 124 / 124, identical | 647 / 647, identical | holds as registered |
| flask | 96 / 96, identical | 372 / 372, identical | holds as registered |
| click | 249 / 249, identical | 1,371 / 1,371, identical | holds as registered |
| werkzeug | 425 / 425, identical | 1,511 / 1,511, identical | holds as registered |
| fastapi | 146 / 146, identical | 1,089 / 1,089, identical | holds as registered |
| zod | 454 / 456 | 512 / 514 | fails as registered; **holds with PD-1** (the two differing tuples are exactly PD-1's, on 0.8.51, INFERRED; 0 unaccepted) |

Gate runs: g1/logs/parity_gate.log (137 passed), g2/logs/parity.log (132 passed), g3/par/gate.log (127 passed), all
GREEN on the RC build. The STAGE-0 corpus, nestjs-rw and node-express are not in the gate's corpus set; their final
graphs are identical on both engines for every variant (0 engine-specific changes, symmetric difference 0). `self`
is not in it either; its final-graph E holds (§3.2) and its engine-specific changes are classified in §3.3.

### 3.2 Final graph (`engdiff.py`, A1: edges whose both endpoints exist on both engines; the raw figure beside it)

Variant symmetric difference vs V0's, on the common endpoints (raw over all edges in brackets):

| Corpus | VF | VA | VT | VT−a | VP | VPA | VPT | V9 |
|---|---|---|---|---|---|---|---|---|
| requests | 16 ≤ 16 | 16 ≤ 16 | 16 ≤ 16 | 16 ≤ 16 | 16 ≤ 16 | 16 ≤ 16 | 16 ≤ 16 | 16 ≤ 16 |
| flask | 13 ≤ 13 | 13 ≤ 13 | 12 ≤ 13 | 12 ≤ 13 | 13 ≤ 13 | 13 ≤ 13 | 12 ≤ 13 | 12 ≤ 13 |
| click | 76 ≤ 76 | 76 ≤ 76 | 76 ≤ 76 | 76 ≤ 76 | 76 ≤ 76 | 76 ≤ 76 | 76 ≤ 76 | 76 ≤ 76 |
| werkzeug | 66 ≤ 66 | 66 ≤ 66 | 56 ≤ 66 | 56 ≤ 66 | 66 ≤ 66 | 66 ≤ 66 | 56 ≤ 66 | 56 ≤ 66 |
| fastapi | 1 ≤ 1 | 1 ≤ 1 | 1 ≤ 1 | 1 ≤ 1 | **56 > 1 FAILS** | **56 > 1 FAILS** | 1 ≤ 1 | 1 ≤ 1 |
| STAGE-0 | 0 ≤ 0 | 0 ≤ 0 | 0 ≤ 0 | 0 ≤ 0 | 0 ≤ 0 | 0 ≤ 0 | 0 ≤ 0 | 0 ≤ 0 |
| self | 7,701 ≤ 7,727 | 7,727 ≤ 7,727 | 7,711 ≤ 7,727 | 7,711 ≤ 7,727 | 7,339 ≤ 7,727 | 7,339 ≤ 7,727 | 7,339 ≤ 7,727 | 7,339 ≤ 7,727 |
| zod | 36 ≤ 36 (74/74) | 36 ≤ 36 (74/74) | 32 ≤ 36 (89/74) | 32 ≤ 36 (89/74) | 36 ≤ 36 (74/74) | 36 ≤ 36 (74/74) | 32 ≤ 36 (89/74) | 32 ≤ 36 (89/74) |
| nestjs-rw, node-express, httprouter, serde-json | 0 ≤ 0 | 0 ≤ 0 | 0 ≤ 0 | 0 ≤ 0 | 0 ≤ 0 | 0 ≤ 0 | 0 ≤ 0 | 0 ≤ 0 |
| gin | 101 ≤ 101 | 101 ≤ 101 | 101 ≤ 101 | 101 ≤ 101 | 101 ≤ 101 | 101 ≤ 101 | 101 ≤ 101 | 101 ≤ 101 |

Only zod has nodes on one engine only (20 generic static arrow-field members, 0.8.51 only: A1's reason), so raw and
common-endpoint figures coincide everywhere else. The fastapi VP / VPA failure: 110 engine-specific changes, all on
55 `pathlib.Path(...)` call sites in scripts/ and tests/, which 0.8.51 binds INFERRED to `fastapi/params.py` `Path`
under PRECISE while 0.8.39 keeps the two-way AMBIGUOUS fan-out; VPT and V9 remove these calls as external and hold.
Evidence: `s6d/engdiff/<corpus>_<V>.{txt,json}`.

### 3.3 Engine-specific changes, classified

An engine-specific change (A1) is an edge between common endpoints that the variant adds, removes or regrades against
V0 on one engine only. Classified by the four grades (V0 / variant × g39 / g51): *created* = V0 agrees across the
engines and the variant's final graphs do not (a divergence the variant introduces); *converged* = V0 disagrees and the
variant agrees; *carried* = both disagree (`s6d/scripts/es_classify.py`, `s6d/engdiff/es_classified_*.json`).

| Corpus | Variant | Changes (edges) | created | converged | carried |
|---|---|---|---|---|---|
| requests | VT, VPT, V9, VA, VP, VPA | 1 (1) | 0 | 0 | 1 (`should_strip_auth → Session.get`, an edge only 0.8.39's V0 has; regraded) |
| flask | VT, VT−a, VPT, V9 | 1 (1) | 0 | 1 (`Flask.request_context → AppContext.from_environ`, true; 0.8.51's V0 already had it) | 0 |
| werkzeug | VT, VPT, V9 | 11 (11) | 0 | 10 (e.g. `get_content_length → HeaderSet.from_header`, `Request.accept_encodings → Accept.from_header`: 0.8.51's V0 already had them) | 1 (`BadRequestKeyError.description → HTTPException.__str__`, only on 0.8.39; regraded) |
| werkzeug | VT−a | 10 (10) | 0 | 10 | 0 |
| werkzeug | VA, VP, VPA | 1 (1) | 0 | 0 | 1 |
| fastapi | VP, VPA | 110 (110) | **110** | 0 | 0 |
| zod | VT | 23 (22) | 0 | 4 | 18 |
| zod | VPT, V9 | 26 (25) | **2** (the PD-1 pairs `ZodFunction.args` / `ZodFunction.create` → `ZodTuple.rest`: EXTRACTED on g39 (precise key), INFERRED on g51 (PD-1's typed tuple)) | 4 | 19 |
| zod | VT−a | 11 (10) | 0 | 4 | 6 |
| zod | VP, VPA | 23 (23) | 0 | 0 | 23 |
| zod | VA | 15 (15) | 0 | 0 | 15 |
| click, STAGE-0, nestjs-rw, node-express | every variant | 0 | 0 | 0 | 0 |

Self, classified the same way:

| Corpus | Variant | Changes (edges) | created | converged | carried |
|---|---|---|---|---|---|
| self | VT | 36 (36) | 0 | 16 | 20 |
| self | VPT | 409 (409) | 0 | 388 | 21 |
| self | VPA | 409 (409) | 0 | 388 | 21 |
| self | VP | 421 (421) | 0 | 388 | 33 |
| self | VT-a | 36 (36) | 0 | 16 | 20 |
| self | V9 | 409 (409) | 0 | 388 | 21 |

### 3.4 Gates (D3-G5, D1-G5), run by this lane on the RC tree (`prep/rc`), both engine ends

| Gate | 0.8.39 (dev Python) | 0.8.51 (prep/v51) |
|---|---|---|
| typed_calls_core_gates | GREEN 482 | GREEN 482 |
| typed_calls_py_gates | GREEN 943 | GREEN 943 |
| typed_calls_ts_gates | GREEN 559 | GREEN 559 |
| typed_calls_ts_fuzz_gates | GREEN 18 | GREEN 18 |
| typed_calls_push_gates | GREEN 111 | GREEN 111 |
| typed_calls_shapes_gates (the design §5 must-nots, final graph) | GREEN 401 | GREEN 401 |
| typed_calls_engine_parity_gates (fixture mode; `--corpora` is §3.1) | GREEN 121 (+1 opt-in skip) | GREEN 121 (+1 opt-in skip) |
| calls_family_filter_gates | GREEN 26 | GREEN 26 |
| precise_fixes_gates (incl. H: the P1b probe tree gives the identical 9 tuples on 0.8.39 and 0.8.51) | GREEN 266 | GREEN 266 |
| precise_family_gates | GREEN 434 | GREEN 434 |

Evidence: `s6d/gates/<gate>_py39.txt`, `<gate>_py51.txt`, `log_py39.txt`, `log_py51.txt` (machine load at each start).

Re-checked during reconciliation:
- Every log names `build <work>\prep\rc\build`, whose code hash is `304c3f11…`.
- The 0.8.51 logs report "graphify 0.8.51".
- Every gate exits 0 with the counts above.

These gates are the first half of D3-G5. The second half is "the on/off effect diff on 2 real corpora per family
reviewed and committed". The effect diffs are the VT vs V0 rows of §2.4 and §2.6, and the blind audit judged them
(§4.2):
- requests and flask: every added, removed and demoted edge, and 40 of the 92 / 77 EXTRACTED edges;
- nestjs-rw and node-express: every changed edge;
- zod: a sample.

The edge-level diffs (`s6d/delta/`) and the audit sheets lie outside the repository, this file holds only their
counts, and S6 commits nothing. DECISION.md reading 7 therefore records the second half as not established.

### 3.5 G6 read strictly: engine-specific typed edges under VT

Neither part below is a final-graph divergence that VT creates: §3.3 counts 0 "created" for VT on every corpus.
DECISION.md reading 6 counts both parts as engine-specific typed edges.

**engdiff's engine-specific changes.** These are A1's G6 count: VT's changes against V0 on one engine only, on common
endpoints (`s6d/engdiff/<corpus>_VT.json`).

| Corpus | Count |
|---|---|
| requests | 1 |
| flask | 1 |
| click | 0 |
| werkzeug | 11 |
| fastapi | 0 |
| STAGE-0 | 0 |
| self | 36 (all test callers) |
| zod | 23 (raw 75) |
| nestjs-rw | 0 |
| node-express | 0 |

zod's 23 break down as follows:
- 12 audit regrades INFERRED → AMBIGUOUS of 0.8.39-only misbinds to `ZodPipeline.create`;
- `ZodType.pipe → ZodPipeline.create`, made EXTRACTED on both engines (it converges);
- `custom`, `ZodRecord.create` → `ZodPipeline.create` and `ZodError.flatten → ZodError.create`, removed on one engine;
- 6 test-caller edges `Mocker.{string,number,boolean}{Optional,Nullable} → Mocker.pick`, regraded INFERRED → EXTRACTED
  on 0.8.51 only.

**The parity gate's shipped-policy comparison.** These are the typed tuples VT produces under the shipped policy, all
callers, on one engine only (`g1/par/summary.json`, `g2/par/parity_summary.json`, `g3/par/summary.json`:
`typed_all_shipped`, `shipped_policy_only39` / `shipped_policy_only51`):

| Corpus | Tuples g39 / g51 | Only on g39 | Only on g51 |
|---|---|---|---|
| requests | 205 / 220 | 0 | 15 |
| flask | 141 / 138 | 7 | 4 |
| click | 295 / 371 | 0 | 76 |
| werkzeug | 622 / 651 | 2 | 31 |
| fastapi | 701 / 702 | 0 | 1 |
| zod | 454 / 462 | 0 | 8 |

zod's 8 are PD-1's 2 production tuples plus the 6 `Mocker` test-caller tuples, which PD-1 does not list.

The gate prints at most 50 entries per list: click's `shipped_policy_only51` shows 50, and the gate's own
`typed_all_shipped.diff` count is 76.

Under the shipped policy, test and non-production callers are keyed only where the engine already linked a tuple, so
these keys depend on the engine. The decisive E comparisons do not include them:
- C1: production callers;
- C2: every caller with TESTS / NONPROD = 1.

Both C1 and C2 hold on every corpus, zod through PD-1 (§3.1).

## 4. A — the blind audit (PREREG §8.4 A)

### 4.1 Method

94 seeded sheets (`audit_sample.py`, seed 20261022, n 40 per bucket, the variant hidden; label = corpus id, so one
edge has one row id on every sheet of its corpus): lane g1 30 (+3 union sheets), g2 24 (+3 union), g3 40. Two
reviewers per lane group (A and B) judged every row TRUE / FALSE / UNSURE from the source, without the keys. An item
is TRUE or FALSE when both reviewers agree, UNSURE otherwise (`audit_sample.score`, run here with both filled sheets;
g3-B's verdicts were turned into its sheet.csv files from its judgments.jsonl). Precision is reported with Wilson 95%
bounds. The exhaustive stratum (every resolver-confirmed removal) is empty for every key: the reference resolvers
confirmed 0 removed edges anywhere.

**Agreement.** g1: 2,126 rows agree, 4 rows UNSURE / UNSURE (one item, click `echo → PacifyFlushWrapper.flush`, a
demotion, on four sheets); g2: 2,375 / 2,375 agree; g3: 2,443 / 2,443 agree. The reviewers wrote their reasons in
their own words (identical reason strings: g1 57 of 2,700, g2 4 of 2,375, g3 0 of 2,443).

### 4.2 Per sheet (reconciled; T / F / U = TRUE / FALSE / UNSURE, n in brackets)

| Sheet | Corpus | Comparison | Eng | added T/F/U (n) | removed T/F/U (n) | demoted T/F/U (n) | EXTRACTED T/F/U (n) | losses | resolver wrong/judged |
|---|---|---|---|---|---|---|---|---|---|
| rq-07 | requests | V9 vs VPT | g39 | 20/1/0 (21) | – | – | 40/0/0 (40) | 0 | 1/60 |
| rq-05 | requests | V9 vs VPT | g51 | 20/1/0 (21) | – | – | 40/0/0 (40) | 0 | 1/60 |
| rq-10 | requests | VPA vs VA | g39 | – | – | – | 38/2/0 (40) | 0 | 0/38 |
| rq-08 | requests | VPA vs VA | g51 | – | – | – | 38/2/0 (40) | 0 | 0/38 |
| rq-06 | requests | VPT vs VP | g39 | 33/0/0 (33) | 0/18/0 (18) | 1/11/0 (12) | 38/2/0 (40) | 0 | 0/85 |
| rq-03 | requests | VPT vs VP | g51 | 33/0/0 (33) | 0/18/0 (18) | 1/10/0 (11) | 38/2/0 (40) | 0 | 0/85 |
| rq-04 | requests | VPT vs VT | g39 | – | – | – | 38/2/0 (40) | 0 | 0/38 |
| rq-09 | requests | VPT vs VT | g51 | – | – | – | 38/2/0 (40) | 0 | 0/38 |
| rq-01 | requests | VT vs V0 | g39 | 33/0/0 (33) | 0/18/0 (18) | 1/11/0 (12) | 40/0/0 (40) | 0 | 0/88 |
| rq-02 | requests | VT vs V0 | g51 | 33/0/0 (33) | 0/18/0 (18) | 1/10/0 (11) | 40/0/0 (40) | 0 | 0/88 |
| fl-04 | flask | V9 vs VPT | g39 | 11/0/0 (11) | – | – | 40/0/0 (40) | 0 | 0/45 |
| fl-10 | flask | V9 vs VPT | g51 | 11/0/0 (11) | – | – | 40/0/0 (40) | 0 | 0/45 |
| fl-06 | flask | VPA vs VA | g39 | – | – | – | 40/0/0 (40) | 0 | 0/40 |
| fl-07 | flask | VPA vs VA | g51 | – | – | – | 40/0/0 (40) | 0 | 0/40 |
| fl-03 | flask | VPT vs VP | g39 | 11/0/0 (11) | 0/10/0 (10) | 2/25/0 (27) | 40/0/0 (40) | 0 | 1/62 |
| fl-09 | flask | VPT vs VP | g51 | 10/0/0 (10) | 0/10/0 (10) | 2/25/0 (27) | 40/0/0 (40) | 0 | 1/61 |
| fl-01 | flask | VPT vs VT | g39 | – | – | – | 40/0/0 (40) | 0 | 0/40 |
| fl-08 | flask | VPT vs VT | g51 | – | – | – | 40/0/0 (40) | 0 | 0/40 |
| fl-02 | flask | VT vs V0 | g39 | 11/0/0 (11) | 0/10/0 (10) | 2/25/0 (27) | 40/0/0 (40) | 0 | 1/62 |
| fl-05 | flask | VT vs V0 | g51 | 10/0/0 (10) | 0/10/0 (10) | 2/25/0 (27) | 40/0/0 (40) | 0 | 1/61 |
| ck-02 | click | V9 vs VPT | g39 | 15/6/0 (21) | – | – | 39/1/0 (40) | 0 | 3/54 |
| ck-04 | click | V9 vs VPT | g51 | 15/6/0 (21) | – | – | 39/1/0 (40) | 0 | 3/54 |
| ck-08 | click | VPA vs VA | g39 | – | 0/9/0 (9) | – | 38/2/0 (40) | 0 | 0/39 |
| ck-09 | click | VPA vs VA | g51 | – | 0/9/0 (9) | – | 38/2/0 (40) | 0 | 0/39 |
| ck-05 | click | VPT vs VP | g39 | 40/0/0 (40) | 0/40/0 (40) | 4/7/1 (12) | 40/0/0 (40) | 0 | 0/121 |
| ck-07 | click | VPT vs VP | g51 | 40/0/0 (40) | 0/40/0 (40) | 4/7/1 (12) | 40/0/0 (40) | 0 | 0/121 |
| ck-06 | click | VPT vs VT | g39 | – | 0/9/0 (9) | – | 40/0/0 (40) | 0 | 0/41 |
| ck-10 | click | VPT vs VT | g51 | – | 0/9/0 (9) | – | 40/0/0 (40) | 0 | 0/41 |
| ck-01 | click | VT vs V0 | g39 | 40/0/0 (40) | 0/40/0 (40) | 2/7/1 (10) | 40/0/0 (40) | 0 | 0/118 |
| ck-03 | click | VT vs V0 | g51 | 40/0/0 (40) | 0/40/0 (40) | 2/7/1 (10) | 40/0/0 (40) | 0 | 0/118 |
| e3a4ad799b | werkzeug | VPA vs VA | g39 | – | 0/40/0 (40) | – | 40/0/0 (40) | 0 | 0/79 |
| dcef89f37d | werkzeug | VPA vs VA | g51 | – | 0/40/0 (40) | – | 40/0/0 (40) | 0 | 0/79 |
| 6066a87a0f | werkzeug | VPT vs VP | g39 | 40/0/0 (40) | 0/10/0 (10) | 13/25/0 (38) | 40/0/0 (40) | 0 | 2/105 |
| ac628ba455 | werkzeug | VPT vs VP | g51 | 40/0/0 (40) | 0/10/0 (10) | 13/24/0 (37) | 40/0/0 (40) | 0 | 2/106 |
| 98472b678a | werkzeug | VPT vs VT | g39 | – | 0/40/0 (40) | – | 40/0/0 (40) | 0 | 0/80 |
| c1cfb3489a | werkzeug | VPT vs VT | g51 | – | 0/40/0 (40) | – | 40/0/0 (40) | 0 | 0/80 |
| db93584716 | werkzeug | VT vs V0 | g39 | 40/0/0 (40) | 0/40/0 (40) | 7/25/0 (32) | 40/0/0 (40) | 0 | 2/129 |
| d2e44507cc | werkzeug | VT vs V0 | g51 | 40/0/0 (40) | 0/40/0 (40) | 7/24/0 (31) | 40/0/0 (40) | 0 | 2/129 |
| 1d6a555ca4 | fastapi | VPA vs VA | g39 | – | 0/40/0 (40) | – | 40/0/0 (40) | 0 | 0/78 |
| 4a7eedcba7 | fastapi | VPA vs VA | g51 | – | 0/40/0 (40) | – | 40/0/0 (40) | 0 | 0/78 |
| 66a01a7fa3 | fastapi | VPT vs VP | g39 | 32/0/0 (32) | 0/40/0 (40) | 2/19/0 (21) | 40/0/0 (40) | 0 | 0/85 |
| 3950253e60 | fastapi | VPT vs VP | g51 | 32/0/0 (32) | 0/40/0 (40) | 2/19/0 (21) | 40/0/0 (40) | 0 | 0/88 |
| 19c757a77a | fastapi | VPT vs VT | g39 | – | 0/40/0 (40) | – | 40/0/0 (40) | 0 | 0/78 |
| 9d2aa5e20e | fastapi | VPT vs VT | g51 | – | 0/40/0 (40) | – | 40/0/0 (40) | 0 | 0/78 |
| 24807d010c | fastapi | VT vs V0 | g39 | 32/0/0 (32) | 0/40/0 (40) | 0/19/0 (19) | 40/0/0 (40) | 0 | 0/109 |
| 8faaeddabc | fastapi | VT vs V0 | g51 | 32/0/0 (32) | 0/40/0 (40) | 0/19/0 (19) | 40/0/0 (40) | 0 | 0/109 |
| 48338e2e1f | STAGE-0 | VPA vs VA | g39 | – | 0/40/0 (40) | – | 40/0/0 (40) | 0 | 0/80 |
| a191adc41c | STAGE-0 | VPA vs VA | g51 | – | 0/40/0 (40) | – | 40/0/0 (40) | 0 | 0/80 |
| 0682842b8c | STAGE-0 | VPT vs VP | g39 | 40/0/0 (40) | – | – | 40/0/0 (40) | 0 | 0/80 |
| e87c1c088c | STAGE-0 | VPT vs VP | g51 | 40/0/0 (40) | – | – | 40/0/0 (40) | 0 | 0/80 |
| 2b55917f5f | STAGE-0 | VPT vs VT | g39 | – | 0/40/0 (40) | – | 40/0/0 (40) | 0 | 0/80 |
| 8498d6ce89 | STAGE-0 | VPT vs VT | g51 | – | 0/40/0 (40) | – | 40/0/0 (40) | 0 | 0/80 |
| 2466120451 | STAGE-0 | VT vs V0 | g39 | 40/0/0 (40) | 0/6/0 (6) | – | 40/0/0 (40) | 0 | 0/86 |
| b03e3e9fd9 | STAGE-0 | VT vs V0 | g51 | 40/0/0 (40) | 0/6/0 (6) | – | 40/0/0 (40) | 0 | 0/86 |
| Sc62d5fa0 | self | V9 vs VPT | g39 | 31/9/0 (40) | – | – | 40/0/0 (40) | 0 | 5/76 |
| S9fa1cb27 | self | V9 vs VPT | g51 | 31/9/0 (40) | – | – | 40/0/0 (40) | 0 | 5/76 |
| S5edd8f2c | self | VPA vs VA | g39 | – | 0/40/0 (40) | 0/1/0 (1) | 39/1/0 (40) | 0 | 0/77 |
| Sb96add25 | self | VPA vs VA | g51 | – | 0/40/0 (40) | 0/1/0 (1) | 39/1/0 (40) | 0 | 0/78 |
| Sa088c961 | self | VPT vs VP | g39 | 40/0/0 (40) | 0/40/0 (40) | 29/11/0 (40) | 40/0/0 (40) | 0 | 0/142 |
| S0a0d0129 | self | VPT vs VP | g51 | 40/0/0 (40) | 0/40/0 (40) | 28/12/0 (40) | 39/1/0 (40) | 0 | 0/143 |
| Sf98b32c2 | self | VPT vs VT | g39 | – | 0/40/0 (40) | 0/1/0 (1) | 40/0/0 (40) | 0 | 0/80 |
| S46e3c885 | self | VPT vs VT | g51 | – | 0/40/0 (40) | 0/1/0 (1) | 39/1/0 (40) | 0 | 0/79 |
| Sa41c7842 | self | VT vs V0 | g39 | 40/0/0 (40) | 0/40/0 (40) | 21/19/0 (40) | 40/0/0 (40) | 0 | 0/137 |
| S61fc6123 | self | VT vs V0 | g51 | 40/0/0 (40) | 0/40/0 (40) | 21/19/0 (40) | 40/0/0 (40) | 0 | 0/136 |
| S18105b08 | zod | V9 vs VPT | g39 | – | – | – | 38/2/0 (40) | 0 | 0/36 |
| S283f2825 | zod | V9 vs VPT | g51 | – | – | – | 39/1/0 (40) | 0 | 0/36 |
| S377850c6 | zod | VPA vs VA | g39 | – | 5/35/0 (40) | – | 34/6/0 (40) | 5 | 5/73 |
| Sf11af230 | zod | VPA vs VA | g51 | – | 5/35/0 (40) | – | 33/7/0 (40) | 5 | 5/72 |
| S269b565d | zod | VPT vs VP | g39 | 40/0/0 (40) | 0/40/0 (40) | 10/15/0 (25) | 38/2/0 (40) | 0 | 0/116 |
| S86bac676 | zod | VPT vs VP | g51 | 40/0/0 (40) | 0/40/0 (40) | 12/5/0 (17) | 39/1/0 (40) | 0 | 0/114 |
| S57615359 | zod | VPT vs VT | g39 | – | 5/35/0 (40) | – | 38/2/0 (40) | 5 | 5/74 |
| Se94029a8 | zod | VPT vs VT | g51 | – | 5/35/0 (40) | – | 39/1/0 (40) | 5 | 5/74 |
| S9cb36c5c | zod | VT vs V0 | g39 | 40/0/0 (40) | 0/40/0 (40) | 9/15/0 (24) | 40/0/0 (40) | 0 | 0/119 |
| S9beb2383 | zod | VT vs V0 | g51 | 40/0/0 (40) | 0/40/0 (40) | 9/5/0 (14) | 40/0/0 (40) | 0 | 0/116 |
| S637180a9 | nestjs-rw | V9 vs VPT | g39 | – | – | – | 8/0/0 (8) | 0 | 0/7 |
| Sb9333b61 | nestjs-rw | V9 vs VPT | g51 | – | – | – | 8/0/0 (8) | 0 | 0/7 |
| S3ac2ba99 | nestjs-rw | VPA vs VA | g39 | – | – | – | 8/0/0 (8) | 0 | 0/7 |
| Sb794a2b5 | nestjs-rw | VPA vs VA | g51 | – | – | – | 8/0/0 (8) | 0 | 0/7 |
| Sdcb87096 | nestjs-rw | VPT vs VP | g39 | 26/0/0 (26) | – | 0/15/0 (15) | 8/0/0 (8) | 0 | 0/33 |
| S9f4c95b7 | nestjs-rw | VPT vs VP | g51 | 26/0/0 (26) | – | 0/15/0 (15) | 8/0/0 (8) | 0 | 0/33 |
| S785e6750 | nestjs-rw | VPT vs VT | g39 | – | – | – | 8/0/0 (8) | 0 | 0/7 |
| Sf15bd711 | nestjs-rw | VPT vs VT | g51 | – | – | – | 8/0/0 (8) | 0 | 0/7 |
| Sdf90d3ba | nestjs-rw | VT vs V0 | g39 | 26/0/0 (26) | – | 0/15/0 (15) | 7/0/0 (7) | 0 | 0/33 |
| Sa6373c20 | nestjs-rw | VT vs V0 | g51 | 26/0/0 (26) | – | 0/15/0 (15) | 7/0/0 (7) | 0 | 0/33 |
| Sea652b93 | node-express | V9 vs VPT | g39 | – | – | – | 8/0/0 (8) | 0 | 0/5 |
| Scb06ba74 | node-express | V9 vs VPT | g51 | – | – | – | 8/0/0 (8) | 0 | 0/5 |
| S22a60822 | node-express | VPA vs VA | g39 | – | – | – | 8/0/0 (8) | 0 | 0/5 |
| Seb25a719 | node-express | VPA vs VA | g51 | – | – | – | 8/0/0 (8) | 0 | 0/5 |
| S1444914b | node-express | VPT vs VP | g39 | 15/0/0 (15) | – | – | 8/0/0 (8) | 0 | 0/20 |
| Sa199e715 | node-express | VPT vs VP | g51 | 15/0/0 (15) | – | – | 8/0/0 (8) | 0 | 0/20 |
| S977a24c9 | node-express | VPT vs VT | g39 | – | – | – | 8/0/0 (8) | 0 | 0/5 |
| Scbe69723 | node-express | VPT vs VT | g51 | – | – | – | 8/0/0 (8) | 0 | 0/5 |
| S4102f2b4 | node-express | VT vs V0 | g39 | 15/0/0 (15) | – | – | – | 0 | 0/15 |
| Sc55c0dbd | node-express | VT vs V0 | g51 | 15/0/0 (15) | – | – | – | 0 | 0/15 |

### 4.3 Per corpus: precision of additions, correct removals, EXTRACTED precision

`removed_all` is `audit_sample`'s population-weighted correct-removal share and its lower bound over the whole
removal population (the sampled stratum's Wilson LB; a census of a small population still carries the Wilson LB).

`audit_sample.py` documents this per-key figure as "the 'removed audited FALSE >= 95% (LB >= 90%)' number". DECISION.md
reading 2 therefore judges that bullet, and D1-G3's audited EXTRACTED precision, per corpus × engine on this table.
Every audited removal under VT vs V0 is FALSE. Even so, three small censuses have a bound below 0.90 on both engines:
requests (18, LB 0.824), flask (10, LB 0.723) and STAGE-0 (6, LB 0.610). VPA vs VA's EXTRACTED precision is below
LB 0.90 on requests (38/40, 0.835), click (38/40, 0.835) and self (39/40, 0.871). zod's (34/40, 33/40) is below it
too, and that failure holds under any reading. §4.4 gives the pooled figures beside them.

| Comparison | Corpus | Eng | added precision (TRUE/n, Wilson LB) | removed audited FALSE (FALSE/n; removed_all share / LB over population) | EXTRACTED precision (TRUE/n, LB) | losses |
|---|---|---|---|---|---|---|
| VT vs V0 | requests | g39 | 33/33 = 1.000 (LB 0.896) | 18/18; 1.000 / 0.824 over 18 | 40/40 = 1.000 (LB 0.912) | 0 |
| VT vs V0 | requests | g51 | 33/33 = 1.000 (LB 0.896) | 18/18; 1.000 / 0.824 over 18 | 40/40 = 1.000 (LB 0.912) | 0 |
| VT vs V0 | flask | g39 | 11/11 = 1.000 (LB 0.741) | 10/10; 1.000 / 0.723 over 10 | 40/40 = 1.000 (LB 0.912) | 0 |
| VT vs V0 | flask | g51 | 10/10 = 1.000 (LB 0.722) | 10/10; 1.000 / 0.723 over 10 | 40/40 = 1.000 (LB 0.912) | 0 |
| VT vs V0 | click | g39 | 40/40 = 1.000 (LB 0.912) | 40/40; 1.000 / 0.912 over 116 | 40/40 = 1.000 (LB 0.912) | 0 |
| VT vs V0 | click | g51 | 40/40 = 1.000 (LB 0.912) | 40/40; 1.000 / 0.912 over 116 | 40/40 = 1.000 (LB 0.912) | 0 |
| VT vs V0 | werkzeug | g39 | 40/40 = 1.000 (LB 0.912) | 40/40; 1.000 / 0.912 over 74 | 40/40 = 1.000 (LB 0.912) | 0 |
| VT vs V0 | werkzeug | g51 | 40/40 = 1.000 (LB 0.912) | 40/40; 1.000 / 0.912 over 74 | 40/40 = 1.000 (LB 0.912) | 0 |
| VT vs V0 | fastapi | g39 | 32/32 = 1.000 (LB 0.893) | 40/40; 1.000 / 0.912 over 3607 | 40/40 = 1.000 (LB 0.912) | 0 |
| VT vs V0 | fastapi | g51 | 32/32 = 1.000 (LB 0.893) | 40/40; 1.000 / 0.912 over 3607 | 40/40 = 1.000 (LB 0.912) | 0 |
| VT vs V0 | STAGE-0 | g39 | 40/40 = 1.000 (LB 0.912) | 6/6; 1.000 / 0.610 over 6 | 40/40 = 1.000 (LB 0.912) | 0 |
| VT vs V0 | STAGE-0 | g51 | 40/40 = 1.000 (LB 0.912) | 6/6; 1.000 / 0.610 over 6 | 40/40 = 1.000 (LB 0.912) | 0 |
| VT vs V0 | self | g39 | 40/40 = 1.000 (LB 0.912) | 40/40; 1.000 / 0.912 over 7218 | 40/40 = 1.000 (LB 0.912) | 0 |
| VT vs V0 | self | g51 | 40/40 = 1.000 (LB 0.912) | 40/40; 1.000 / 0.912 over 7202 | 40/40 = 1.000 (LB 0.912) | 0 |
| VT vs V0 | zod | g39 | 40/40 = 1.000 (LB 0.912) | 40/40; 1.000 / 0.912 over 44 | 40/40 = 1.000 (LB 0.912) | 0 |
| VT vs V0 | zod | g51 | 40/40 = 1.000 (LB 0.912) | 40/40; 1.000 / 0.912 over 58 | 40/40 = 1.000 (LB 0.912) | 0 |
| VT vs V0 | nestjs-rw | g39 | 26/26 = 1.000 (LB 0.871) | – | 7/7 = 1.000 (LB 0.646) | 0 |
| VT vs V0 | nestjs-rw | g51 | 26/26 = 1.000 (LB 0.871) | – | 7/7 = 1.000 (LB 0.646) | 0 |
| VT vs V0 | node-express | g39 | 15/15 = 1.000 (LB 0.796) | – | – | 0 |
| VT vs V0 | node-express | g51 | 15/15 = 1.000 (LB 0.796) | – | – | 0 |
| VPT vs VP | requests | g39 | 33/33 = 1.000 (LB 0.896) | 18/18; 1.000 / 0.824 over 18 | 38/40 = 0.950 (LB 0.835) | 0 |
| VPT vs VP | requests | g51 | 33/33 = 1.000 (LB 0.896) | 18/18; 1.000 / 0.824 over 18 | 38/40 = 0.950 (LB 0.835) | 0 |
| VPT vs VP | flask | g39 | 11/11 = 1.000 (LB 0.741) | 10/10; 1.000 / 0.723 over 10 | 40/40 = 1.000 (LB 0.912) | 0 |
| VPT vs VP | flask | g51 | 10/10 = 1.000 (LB 0.722) | 10/10; 1.000 / 0.723 over 10 | 40/40 = 1.000 (LB 0.912) | 0 |
| VPT vs VP | click | g39 | 40/40 = 1.000 (LB 0.912) | 40/40; 1.000 / 0.912 over 116 | 40/40 = 1.000 (LB 0.912) | 0 |
| VPT vs VP | click | g51 | 40/40 = 1.000 (LB 0.912) | 40/40; 1.000 / 0.912 over 116 | 40/40 = 1.000 (LB 0.912) | 0 |
| VPT vs VP | werkzeug | g39 | 40/40 = 1.000 (LB 0.912) | 10/10; 1.000 / 0.723 over 10 | 40/40 = 1.000 (LB 0.912) | 0 |
| VPT vs VP | werkzeug | g51 | 40/40 = 1.000 (LB 0.912) | 10/10; 1.000 / 0.723 over 10 | 40/40 = 1.000 (LB 0.912) | 0 |
| VPT vs VP | fastapi | g39 | 32/32 = 1.000 (LB 0.893) | 40/40; 1.000 / 0.912 over 181 | 40/40 = 1.000 (LB 0.912) | 0 |
| VPT vs VP | fastapi | g51 | 32/32 = 1.000 (LB 0.893) | 40/40; 1.000 / 0.912 over 126 | 40/40 = 1.000 (LB 0.912) | 0 |
| VPT vs VP | STAGE-0 | g39 | 40/40 = 1.000 (LB 0.912) | – | 40/40 = 1.000 (LB 0.912) | 0 |
| VPT vs VP | STAGE-0 | g51 | 40/40 = 1.000 (LB 0.912) | – | 40/40 = 1.000 (LB 0.912) | 0 |
| VPT vs VP | self | g39 | 40/40 = 1.000 (LB 0.912) | 40/40; 1.000 / 0.912 over 171 | 40/40 = 1.000 (LB 0.912) | 0 |
| VPT vs VP | self | g51 | 40/40 = 1.000 (LB 0.912) | 40/40; 1.000 / 0.912 over 171 | 39/40 = 0.975 (LB 0.871) | 0 |
| VPT vs VP | zod | g39 | 40/40 = 1.000 (LB 0.912) | 40/40; 1.000 / 0.912 over 44 | 38/40 = 0.950 (LB 0.835) | 0 |
| VPT vs VP | zod | g51 | 40/40 = 1.000 (LB 0.912) | 40/40; 1.000 / 0.912 over 58 | 39/40 = 0.975 (LB 0.871) | 0 |
| VPT vs VP | nestjs-rw | g39 | 26/26 = 1.000 (LB 0.871) | – | 8/8 = 1.000 (LB 0.676) | 0 |
| VPT vs VP | nestjs-rw | g51 | 26/26 = 1.000 (LB 0.871) | – | 8/8 = 1.000 (LB 0.676) | 0 |
| VPT vs VP | node-express | g39 | 15/15 = 1.000 (LB 0.796) | – | 8/8 = 1.000 (LB 0.676) | 0 |
| VPT vs VP | node-express | g51 | 15/15 = 1.000 (LB 0.796) | – | 8/8 = 1.000 (LB 0.676) | 0 |
| VPT vs VT | requests | g39 | – | – | 38/40 = 0.950 (LB 0.835) | 0 |
| VPT vs VT | requests | g51 | – | – | 38/40 = 0.950 (LB 0.835) | 0 |
| VPT vs VT | flask | g39 | – | – | 40/40 = 1.000 (LB 0.912) | 0 |
| VPT vs VT | flask | g51 | – | – | 40/40 = 1.000 (LB 0.912) | 0 |
| VPT vs VT | click | g39 | – | 9/9; 1.000 / 0.701 over 9 | 40/40 = 1.000 (LB 0.912) | 0 |
| VPT vs VT | click | g51 | – | 9/9; 1.000 / 0.701 over 9 | 40/40 = 1.000 (LB 0.912) | 0 |
| VPT vs VT | werkzeug | g39 | – | 40/40; 1.000 / 0.912 over 135 | 40/40 = 1.000 (LB 0.912) | 0 |
| VPT vs VT | werkzeug | g51 | – | 40/40; 1.000 / 0.912 over 135 | 40/40 = 1.000 (LB 0.912) | 0 |
| VPT vs VT | fastapi | g39 | – | 40/40; 1.000 / 0.912 over 2078 | 40/40 = 1.000 (LB 0.912) | 0 |
| VPT vs VT | fastapi | g51 | – | 40/40; 1.000 / 0.912 over 2078 | 40/40 = 1.000 (LB 0.912) | 0 |
| VPT vs VT | STAGE-0 | g39 | – | 40/40; 1.000 / 0.912 over 190 | 40/40 = 1.000 (LB 0.912) | 0 |
| VPT vs VT | STAGE-0 | g51 | – | 40/40; 1.000 / 0.912 over 190 | 40/40 = 1.000 (LB 0.912) | 0 |
| VPT vs VT | self | g39 | – | 40/40; 1.000 / 0.912 over 552762 | 40/40 = 1.000 (LB 0.912) | 0 |
| VPT vs VT | self | g51 | – | 40/40; 1.000 / 0.912 over 552390 | 39/40 = 0.975 (LB 0.871) | 0 |
| VPT vs VT | zod | g39 | – | 35/40; 0.875 / 0.739 over 109 | 38/40 = 0.950 (LB 0.835) | 5 |
| VPT vs VT | zod | g51 | – | 35/40; 0.875 / 0.739 over 109 | 39/40 = 0.975 (LB 0.871) | 5 |
| VPT vs VT | nestjs-rw | g39 | – | – | 8/8 = 1.000 (LB 0.676) | 0 |
| VPT vs VT | nestjs-rw | g51 | – | – | 8/8 = 1.000 (LB 0.676) | 0 |
| VPT vs VT | node-express | g39 | – | – | 8/8 = 1.000 (LB 0.676) | 0 |
| VPT vs VT | node-express | g51 | – | – | 8/8 = 1.000 (LB 0.676) | 0 |
| VPA vs VA | requests | g39 | – | – | 38/40 = 0.950 (LB 0.835) | 0 |
| VPA vs VA | requests | g51 | – | – | 38/40 = 0.950 (LB 0.835) | 0 |
| VPA vs VA | flask | g39 | – | – | 40/40 = 1.000 (LB 0.912) | 0 |
| VPA vs VA | flask | g51 | – | – | 40/40 = 1.000 (LB 0.912) | 0 |
| VPA vs VA | click | g39 | – | 9/9; 1.000 / 0.701 over 9 | 38/40 = 0.950 (LB 0.835) | 0 |
| VPA vs VA | click | g51 | – | 9/9; 1.000 / 0.701 over 9 | 38/40 = 0.950 (LB 0.835) | 0 |
| VPA vs VA | werkzeug | g39 | – | 40/40; 1.000 / 0.912 over 199 | 40/40 = 1.000 (LB 0.912) | 0 |
| VPA vs VA | werkzeug | g51 | – | 40/40; 1.000 / 0.912 over 199 | 40/40 = 1.000 (LB 0.912) | 0 |
| VPA vs VA | fastapi | g39 | – | 40/40; 1.000 / 0.912 over 5504 | 40/40 = 1.000 (LB 0.912) | 0 |
| VPA vs VA | fastapi | g51 | – | 40/40; 1.000 / 0.912 over 5559 | 40/40 = 1.000 (LB 0.912) | 0 |
| VPA vs VA | STAGE-0 | g39 | – | 40/40; 1.000 / 0.912 over 196 | 40/40 = 1.000 (LB 0.912) | 0 |
| VPA vs VA | STAGE-0 | g51 | – | 40/40; 1.000 / 0.912 over 196 | 40/40 = 1.000 (LB 0.912) | 0 |
| VPA vs VA | self | g39 | – | 40/40; 1.000 / 0.912 over 559809 | 39/40 = 0.975 (LB 0.871) | 0 |
| VPA vs VA | self | g51 | – | 40/40; 1.000 / 0.912 over 559421 | 39/40 = 0.975 (LB 0.871) | 0 |
| VPA vs VA | zod | g39 | – | 35/40; 0.875 / 0.739 over 109 | 34/40 = 0.850 (LB 0.709) | 5 |
| VPA vs VA | zod | g51 | – | 35/40; 0.875 / 0.739 over 109 | 33/40 = 0.825 (LB 0.681) | 5 |
| VPA vs VA | nestjs-rw | g39 | – | – | 8/8 = 1.000 (LB 0.676) | 0 |
| VPA vs VA | nestjs-rw | g51 | – | – | 8/8 = 1.000 (LB 0.676) | 0 |
| VPA vs VA | node-express | g39 | – | – | 8/8 = 1.000 (LB 0.676) | 0 |
| VPA vs VA | node-express | g51 | – | – | 8/8 = 1.000 (LB 0.676) | 0 |
| V9 vs VPT | requests | g39 | 20/21 = 0.952 (LB 0.773) | – | 40/40 = 1.000 (LB 0.912) | 0 |
| V9 vs VPT | requests | g51 | 20/21 = 0.952 (LB 0.773) | – | 40/40 = 1.000 (LB 0.912) | 0 |
| V9 vs VPT | flask | g39 | 11/11 = 1.000 (LB 0.741) | – | 40/40 = 1.000 (LB 0.912) | 0 |
| V9 vs VPT | flask | g51 | 11/11 = 1.000 (LB 0.741) | – | 40/40 = 1.000 (LB 0.912) | 0 |
| V9 vs VPT | click | g39 | 15/21 = 0.714 (LB 0.500) | – | 39/40 = 0.975 (LB 0.871) | 0 |
| V9 vs VPT | click | g51 | 15/21 = 0.714 (LB 0.500) | – | 39/40 = 0.975 (LB 0.871) | 0 |
| V9 vs VPT | self | g39 | 31/40 = 0.775 (LB 0.625) | – | 40/40 = 1.000 (LB 0.912) | 0 |
| V9 vs VPT | self | g51 | 31/40 = 0.775 (LB 0.625) | – | 40/40 = 1.000 (LB 0.912) | 0 |
| V9 vs VPT | zod | g39 | – | – | 38/40 = 0.950 (LB 0.835) | 0 |
| V9 vs VPT | zod | g51 | – | – | 39/40 = 0.975 (LB 0.871) | 0 |
| V9 vs VPT | nestjs-rw | g39 | – | – | 8/8 = 1.000 (LB 0.676) | 0 |
| V9 vs VPT | nestjs-rw | g51 | – | – | 8/8 = 1.000 (LB 0.676) | 0 |
| V9 vs VPT | node-express | g39 | – | – | 8/8 = 1.000 (LB 0.676) | 0 |
| V9 vs VPT | node-express | g51 | – | – | 8/8 = 1.000 (LB 0.676) | 0 |

### 4.4 Pooled per family and engine (sum of the corpora's audited rows; Wilson 95%)

| Comparison | Family | Eng | Added: TRUE / n (LB) | Removed: FALSE / n (LB) | EXTRACTED: TRUE / n (LB) | Losses (removed audited TRUE) |
|---|---|---|---|---|---|---|
| VT vs V0 | py | g39 | 236/236 = 1.000 (0.984) | 194/194 = 1.000 (0.981) | 280/280 = 1.000 (0.986) | 0 |
| VT vs V0 | py | g51 | 235/235 = 1.000 (0.984) | 194/194 = 1.000 (0.981) | 280/280 = 1.000 (0.986) | 0 |
| VT vs V0 | js | g39 | 81/81 = 1.000 (0.955) | 40/40 = 1.000 (0.912) | 47/47 = 1.000 (0.924) | 0 |
| VT vs V0 | js | g51 | 81/81 = 1.000 (0.955) | 40/40 = 1.000 (0.912) | 47/47 = 1.000 (0.924) | 0 |
| VPT vs VP | py | g39 | 236/236 = 1.000 (0.984) | 158/158 = 1.000 (0.976) | 278/280 = 0.993 (0.974) | 0 |
| VPT vs VP | py | g51 | 235/235 = 1.000 (0.984) | 158/158 = 1.000 (0.976) | 277/280 = 0.989 (0.969) | 0 |
| VPT vs VP | js | g39 | 81/81 = 1.000 (0.955) | 40/40 = 1.000 (0.912) | 54/56 = 0.964 (0.879) | 0 |
| VPT vs VP | js | g51 | 81/81 = 1.000 (0.955) | 40/40 = 1.000 (0.912) | 55/56 = 0.982 (0.906) | 0 |
| VPT vs VT | py | g39 | – | 169/169 = 1.000 (0.978) | 278/280 = 0.993 (0.974) | 0 |
| VPT vs VT | py | g51 | – | 169/169 = 1.000 (0.978) | 277/280 = 0.989 (0.969) | 0 |
| VPT vs VT | js | g39 | – | 35/40 = 0.875 (0.739) | 54/56 = 0.964 (0.879) | **5** |
| VPT vs VT | js | g51 | – | 35/40 = 0.875 (0.739) | 55/56 = 0.982 (0.906) | **5** |
| VPA vs VA | py | g39 | – | 169/169 = 1.000 (0.978) | 275/280 = 0.982 (0.959) | 0 |
| VPA vs VA | py | g51 | – | 169/169 = 1.000 (0.978) | 275/280 = 0.982 (0.959) | 0 |
| VPA vs VA | js | g39 | – | 35/40 = 0.875 (0.739) | 50/56 = 0.893 (0.785) | **5** |
| VPA vs VA | js | g51 | – | 35/40 = 0.875 (0.739) | 49/56 = 0.875 (0.764) | **5** |
| V9 vs VPT | py | g39 | 77/93 = 0.828 (0.739) | – | 159/160 = 0.994 (0.966) | 0 |
| V9 vs VPT | py | g51 | 77/93 = 0.828 (0.739) | – | 159/160 = 0.994 (0.966) | 0 |
| V9 vs VPT | js | g39 | (no additions) | – | 54/56 = 0.964 (0.879) | 0 |
| V9 vs VPT | js | g51 | (no additions) | – | 55/56 = 0.982 (0.906) | 0 |

The V9 vs VPT py pool has no werkzeug, fastapi or STAGE-0 rows: lane g2 drew no V9 sheets. Its audited additions
split by caller label: `:Module` / `:Class` callers 31 TRUE / 16 FALSE (66%), function / method callers 46 TRUE / 0
FALSE, on each engine. Demotions (the audit's grade-only changes; a demoted edge audited FALSE is a correct demotion)
are in §4.2.

### 4.5 Reference-resolver error rate

On the audited rows the resolver judged (confirmed / refuted) and both reviewers agreed on: per key at most 6.94%
(zod VPA vs VA g51, 5 of 72); per corpus over its unique audited rows: requests 1/164, flask 1/111, click 3/223,
werkzeug 2/280, fastapi 0/313, STAGE-0 0/191, self 5/647, zod 5/292, nestjs-rw 0/33, node-express 0/20. **No key or
corpus reaches 10%**, so no corpus's G confirmed / refuted deltas are voided (§8.4 A).

### 4.6 Every audited-TRUE removal (losses) and every audited-FALSE addition or EXTRACTED edge

- **Losses: 5, on zod, both engines, in VPA vs VA and VPT vs VT (the PRECISE removals).** All five are
  `ZodBoolean._parse`, `ZodPromise._parse`, `ZodVoid._parse`, `ZodUnknown._parse`, `ZodAny._parse` →
  `OK` (`:Class` node at packages/zod/src/v3/helpers/parseUtil.ts:165, the `export type OK<T>` declaration). The
  resolver graded them refuted; both reviewers judged them TRUE ("`OK` imported from parseUtil, type :165 merged
  with const :166"). PRECISE's TS value-over-type re-key moves each of the 14 OK / DIRTY call sites onto the value
  node (`const OK` at :166 / `const DIRTY` at :163): VP, VPA and VPT hold exactly 14 INFERRED edges to the value
  nodes on both engines, where V0 / VA / VT hold 14 AMBIGUOUS pairs to type + value. VPT vs VP removes none of them
  (0 losses).
- **Audited-FALSE EXTRACTED edges** — none in any VT vs V0 sample (the typed-EXTRACTED sample). All the others are
  PRECISE's same-file keys: requests `RequestsCookieJar.__setstate__ → update`, `_copy_cookie_jar →
  RequestsCookieJar.set_cookie` (VPA, VPT); click `FDCapture.start → NamedTextIOWrapper.fileno`, `Editor.edit_files →
  ProgressBar.update` (VPA) and `FDCapture.stop → EchoingStdin.read` (V9); zod `ZodString._parse → toLowerCase /
  endsWith` (VPT, V9; EXTRACTED already under VP), and under VPA `ZodString.endsWith / ulid / cuid` and
  `ZodNumber.nonnegative → ZodDate._addCheck`, `ZodNumber.gte / gt / lt / lte → ZodBigInt.setLimit`,
  `ZodRegistry.remove → get`, `ZodFunction._parse → ZodType.catch`, `ZodObject._getCached → shape`,
  `ZodString._parse → trim` (zod's EXTRACTED refuted count is 40 under VPA and 1 under VPT); self, test callers:
  `f4_ab_plan_api_r283_gates.main → gate_experiment_flags / gate_ab_weights_plan`,
  `f4_breadth_resume_r283_gates.scenarios → check`.
- **Audited-FALSE additions** (all under V9 vs VPT): requests `sessions` (`:Module`) → `Session`; click `Context`
  (`:Class`) → `Exit`, `Context.close`; click `_termui_impl` (`:Module`) → `raw_terminal`, `get_best_encoding`,
  `_translate_ch_to_exc`, `isatty`; self `enrich` (`:Module`) → `sidecar_path`, `load_sidecar`; `provision`
  (`:Module`) → `disk_free_gb`, `win_long_paths_enabled`, `free_sibling`; `typed_calls._Linker` (`:Class`) →
  `spec_of`, `eval_recv`, `descendants`, `agree`. VT vs V0 and VPT vs VP have no audited-FALSE addition.

## 5. R — retrieval, live, matched state (PREREG §8.4 R)

### 5.1 Matched state

Every V0 build: `clone_graph.py build` with `DEEPMAP_VECTOR_QUANTIZATION=0`, graphify of its engine end, READY.
Every variant: an edge-only re-sync on the same instance. Re-checked here with `run_variant.py`: **matched state OK
for all 84 variant states** (6 corpora × 7 variants × 2 engines: 0 nodes re-embedded, no FTS re-create, identical
node set and text), and **every round trip back to V0 EXACT**: 0.8.39 after every variant (42), 0.8.51 after VT and
after V9 on each instance (12; the 0.8.51 lane brackets VF→VA→VT and VP→VPA→VPT→V9 with one round trip each).
`self`: 0.8.39 round trips after every variant on both replicas (14), 0.8.51 rt1 / rt2 on both replicas (4): all
exact. No sync log shows a vector-index or FTS re-create. Both lanes passed `--set DEEPMAP_VECTOR_QUANTIZATION=0`
(and 0.8.39 `DEEPMAP_MANAGED_VENV=0`) to every arm, V0 included, because `variants.py run` strips every other
`DEEPMAP_*`. The live graded `:CALLS` equal the offline decision dumps edge for edge in every state: 0.8.39 48 of 48
(6 corpora × 8 variants, re-checked here from the lane's live exports), 0.8.51 48 of 48 (the lane's graded-fingerprint
cross-check, `live-0851/out/<c>/xcheck.txt`). Every capture is deterministic (pass 1 = pass 2), 0 errors.

The second derivation's checks agree:
- It counts the 0.8.39 states as 42, the variant states without V0's 6.
- It finds all 54 corpus round trips and all 18 `self` round trips exact under `run_variant.roundtrip`.
- In all 178 variant and round-trip sync logs: 0 nodes re-embedded, no FTS re-create, no vector-index re-create.

On 0.8.51, the lane returned to V0 once per chain of variants (after VT, after V9) rather than after each variant. Each
state in the chain is still verified matched on its own: the sync log shows 0 nodes re-embedded and no FTS or
vector-index re-create, and the live `:CALLS` equal the state's offline dump. Each chain's round trip reproduced V0's
capture exactly. So §8.4 R's VOID condition ("it must reproduce V0's capture exactly") is not met for any run.
Evidence: `s6d/R/rt_matched.json`, `s6d/R/score_index.json`.

### 5.2 R1 and R3 (`score_m.py`, STRICT / SPEC search ∪ impact; 33 Python questions, 4 zod-wing questions)

| Comparison | Eng | 33 py: atoms lost / gained | 33 py STRICT∪ macro | zod wing: atoms lost | R3 multi_hop impact STRICT |
|---|---|---|---|---|---|
| VF vs V0 | g39 | 0 / 0 | 0.5300 → 0.5300 | 0 | 24 → 24 |
| VF vs V0 | g51 | 0 / 0 | 0.5127 → 0.5127 | 0 | 24 → 24 |
| VA vs V0 | g39 / g51 | 0 / 0 | equal | 0 | 24 → 24 |
| VT vs V0 | g39 | **7** / 5 | 0.5300 → 0.5306 | 0 | 24 → 26 |
| VT vs V0 | g51 | **4** / 5 | 0.5127 → 0.5161 | 0 | 24 → 26 |
| VP vs V0 | g39 | 0 / 0 | 0.5300 → 0.5300 | 0 | 24 → 24 |
| VP vs V0 | g51 | 0 / 4 | 0.5127 → 0.5188 | 0 | 24 → 24 |
| VPA vs V0 | g39 | 0 / 0 | 0.5300 → 0.5300 | 0 | 24 → 24 |
| VPA vs V0 | g51 | 0 / 4 | 0.5127 → 0.5188 | 0 | 24 → 24 |
| VPA vs VA | g39 | 0 / 0 | 0.5300 → 0.5300 | 0 | 24 → 24 |
| VPA vs VA | g51 | 0 / 4 | 0.5127 → 0.5188 | 0 | 24 → 24 |
| VPT vs V0 | g39 | **6** / 5 | 0.5300 → 0.5306 | 0 | 24 → 26 |
| VPT vs V0 | g51 | **4** / 7 | 0.5127 → 0.5188 | 0 | 24 → 26 |
| VPT vs VP | g39 | **6** / 5 | 0.5300 → 0.5306 | 0 | 24 → 26 |
| VPT vs VP | g51 | **6** / 5 | 0.5188 → 0.5188 | 0 | 24 → 26 |
| VPT vs VT | g39 | 0 / 1 | 0.5306 → 0.5306 | 0 | 26 → 26 |
| VPT vs VT | g51 | 0 / 2 | 0.5161 → 0.5188 | 0 | 26 → 26 |
| V9 vs V0 | g39 | **16** / 5 | 0.5300 → **0.5143** | 0 | 24 → 26 |
| V9 vs V0 | g51 | **7** / 5 | 0.5127 → **0.5093** | 0 | 24 → 26 |
| V9 vs VPT | g39 | **10** / 0 | 0.5306 → 0.5143 | 0 | 26 → 26 |
| V9 vs VPT | g51 | **5** / 0 | 0.5188 → 0.5093 | 0 | 26 → 26 |

zod-wing STRICT∪ macro 0.2232 for every variant on both engines. Evidence: `s6d/R/<eng>/<corpus>/scored_<V>.json`,
`s6d/R/r1_all.json`.

### 5.3 The lost atoms (all in matched state, exact round trips)

- **VT vs V0, g39 (7):** click-concept-1 SPEC `Choice.convert` (types.py:396); click-concept-2 STRICT + SPEC
  `Parameter.value_from_envvar` (core.py:2661); click-group-dispatch-1 STRICT `Group.parse_args` (core.py:1946);
  werkzeug-concept-1 STRICT + SPEC `Accept.best_match` (accept.py:210), SPEC `LanguageAccept.best_match`
  (accept.py:336).
- **VT vs V0, g51 (4):** the four click losses above.
- **VPT vs VP:** g39 the VT losses without click-concept-1 (6); g51 the four click losses plus werkzeug-blast-1
  STRICT + SPEC `StateMachineMatcher.match` (matcher.py:75) (6).
- **V9 vs V0, g39 (16):** the 7 VT losses plus requests-digest-hooks-3 STRICT `HTTPDigestAuth.__call__`;
  werkzeug-blast-1 STRICT + SPEC `Map.bind_to_environ` and `StateMachineMatcher.match`; werkzeug-concept-1 STRICT +
  SPEC `Request.accept_languages`; werkzeug-concept-2 STRICT + SPEC `Request.make_form_data_parser`.
- **V9 vs V0, g51 (7):** the four click losses plus requests-digest-hooks-3 STRICT and werkzeug-concept-2 STRICT +
  SPEC.

At V0 the atoms VT loses sat at search ranks 12–15 (click) and 9 (werkzeug-concept-1, g39); none was an impact hit.
Every VT edge change on the five Python R corpora is a Python-caller change except 14 grade-only promotions of JS
callers on fastapi (no edge added or removed); fastapi's questions move under no variant.

### 5.4 R2: self, `gt_self65.json` as registered (two fresh replicas per engine, identical)

| Variant | MRR g39 | hit@1 / 5 / 15 g39 | MRR g51 | hit@1 / 5 / 15 g51 | hit@15 lost / gained vs V0 |
|---|---|---|---|---|---|
| V0 | 0.3823 | 0.277 / 0.508 / 0.631 | 0.3898 | 0.292 / 0.508 / 0.631 | – |
| VF | 0.3823 | 0.277 / 0.508 / 0.631 | not run | | none (g39) |
| VA | 0.3823 | 0.277 / 0.508 / 0.631 | not run | | none (g39) |
| VT | 0.3928 | 0.308 / 0.492 / 0.615 | 0.4047 | 0.323 / 0.492 / 0.615 | A-C22, B-S05 / A-C14 |
| VP | 0.4010 | 0.292 / 0.508 / 0.631 | not run | | B-S05 / B-C15 |
| VPA | 0.4010 | 0.292 / 0.508 / 0.631 | 0.3994 | 0.292 / 0.508 / 0.631 | B-S05 / B-C15 |
| VPT | 0.4121 | 0.308 / 0.538 / 0.646 | 0.4123 | 0.308 / 0.538 / 0.646 | B-S05 / A-C14, B-C15 |
| V9 | 0.4252 | 0.323 / 0.554 / 0.646 | 0.4331 | 0.338 / 0.554 / 0.646 | – / A-C14 |

`measure_self.py compare` (MRR ≥ base + min-gain, hit@15 net drop ≤ 1):

| Comparison | min gain | g39 (both replicas) | g51 (both replicas) |
|---|---|---|---|
| VT vs V0 | 0 | holds (+0.0105) | holds (+0.0149) |
| VPT vs V0 | 0.017 | holds (+0.0298) | holds (+0.0225) |
| VPT vs VT | 0.017 | holds (+0.0193) | **fails (+0.0076)** |
| VPA vs V0 | 0.017 | holds (+0.0187) | **fails (+0.0096)** |
| VPA vs VA | 0.017 | holds (+0.0187) | VA not run on g51 |
| V9 vs V0 | 0 | holds (+0.0429) | holds (+0.0433) |
| VF vs V0 | 0 | holds (+0.0000) | not run |

The open item on `gt_self65.json` A-C05 (`build/sync.py::_os_lock` is an alias in the RC; the def is
`sync_status.py::os_lock`): both lanes also scored a lane-local re-baselined copy; every metric is identical, so R2
does not depend on it. Evidence: `s6d/R/self/`.

### 5.5 Drift void rule (`indegree_drift.py` with the live captures)

Not fired for any variant on any of the six question corpora on either engine (`s6d/drift/<corpus>_<V>_<eng>.txt`).
Candidates in a question's atom file (informational): requests `Session.send` (VT ×2.30); flask
`_CollectErrors.raise_any` (VT ×2.39); click `ParamType.split_envvar_value`, `ParamType.get_metavar`; werkzeug
`Accept.from_header` (g39); zod 3 nodes.

## 6. S — STAGE-0 (`tests/stage0_refreeze.py --check --env <flags>`)

| Variant | Engine | `self` oracle | `self-graphify-build` |
|---|---|---|---|
| V0 (control), VF, VA | g39 | PASS | PASS |
| V0 (control), VF | g51 | PASS | PASS |
| VT, VPT, V9 | g39 | PASS (`--repo-id self`) | REGRESSION in 5 of 5 queries; first divergent rank 3; none at rank 1 or 2 |
| VP, VPA | g39 | PASS | REGRESSION in 5 of 5; first divergent rank 3 |
| VT, VPT, V9 | g51 | PASS | REGRESSION in 5 of 5; first divergent ranks 4, 10, 3, 3, 3 |
| VPA | g51 | PASS | REGRESSION in 5 of 5; first divergent ranks 4, 10, 8, 9, 3 |

(`stage0_refreeze` stops at its first failing gate, so the `self` oracle of every regressing candidate was checked
in a separate `--repo-id self` run. A second fresh 0.8.51 VT run reproduced the divergence exactly.)

**Attribution coverage.** In matched state (identical nodes, text and embeddings) the only thing a variant changes
in the STAGE-0 graph is its `:CALLS` edges, and only additions and removals move the arm-3 in-degree (grades do not
enter ranking). The edge changes behind the divergences, and how many the blind audit judged:

| Variant vs V0 | Added (resolver) | Added audited TRUE | Removed (resolver) | Removed audited FALSE | In-degree changes not audited |
|---|---|---|---|---|---|
| VT | 75 (75 confirmed) | 40 | 6 (6 refuted) | 6 | 35 additions |
| VPA | 0 | – | 196 (196 refuted) | 73 | 123 removals |
| VPT | 75 (75 confirmed) | 40 | 196 (196 refuted) | 73 | 35 additions + 123 removals |
| V9 | 94 (94 confirmed) | 40 | 196 (196 refuted) | 73 | 54 additions + 123 removals |

Every audited change in build/ is true (added TRUE, removed FALSE; 0 resolver errors on the 191 audited STAGE-0
rows), but the audit is a sample: under VT, of the seven added edges on the diverging ranks' own in-degree
(`_apply_recognizers → merge_supplemental`, `_apply_recognizers → run`, `sync → generate_embeddings`,
`_python_recognize → edges_from_module`, `_rebuild_code → extract`, `push_to_neo4j → push_typed_edges`,
`push_to_neo4j → prune_stale_edge_types`) only the last was audited; the rank swaps of the lock and the RRF queries
involve nodes whose in-degree did not change and are moved by other candidates' arm-3 ranks, which the S artifacts
do not record. Evidence: `live-0839/out/_summary/S.md`, `S_attribution.md`, `live-0851/out/s0/`, §2.4, §4.

The second derivation looked at each node on the diverging ranks instead (`derive-indep2/out/s_attr.json`). Each
entry is a node at a diverging rank of one query; for each it asked how the node's in-degree moved. The two views agree
that attribution is not established for any candidate.

| Variant vs V0 | Diverging-node entries | No in-degree change | In-degree moved by edges the audit never judged | Every in-degree change audited correct |
|---|---|---|---|---|
| VT | 32 | 24 | 7 | 1 |
| VPT | 33 | 21 | 9 | 3 |
| V9 | 34 | 21 | 10 | 3 |
| VPA | 18 | 15 | 1 (`java_edges.demodulize` 8 → 1 through 3 unaudited removals) | 2 |

Examples of unaudited edges behind a moved node:
- `sync._apply_recognizers → language_recognizers.merge_supplemental`;
- `sync.push_to_neo4j → edge_resolve.push_typed_edges`.

Under VT, the node swaps of the lock query and the RRF query involve nodes whose in-degree did not change. Two
examples are `_os_lock` (3 → 3) and `_acquire_instance_lock` (1 → 1).

The 0.8.39 lane's `S.md` shows the env of VT, VPT, VPA and V9 as `()`. That is a rendering artifact: each
`live-0839/logs/S_<V>.log` shows the variant's flags applied (`[refreeze] --env: …`).

## 7. P — performance (offline; supporting data)

**What these numbers are.** §8.4 P registers "Sync wall time and the pass's own seconds, from the log, on fastapi and
this repo; median of 3; both engines". No lane took that sync-log measurement.

The figures below are the offline timings PREREG §6 names (`<dump>.timing.json`).
- The offline sync wall leaves out a sync's Neo4j push, index and embedding phases.
- The map step is not timed on its own.

So they support P but do not establish it, and DECISION.md (reading 8) records P as not established for D3 and D1. No
figure here exceeds a budget. The second derivation found no other P data, apart from lane g2's earlier set pB at
higher load (below).

Measured by this lane on 2026-10-25, after every other lane had finished, strictly one run at a time: `offline_calls.py build` of the fixed kit (`kitfix/kit`, so the typed pass's own seconds are in the sidecar as `call_linking_seconds`) on the RC build snapshot, `--expect-engine graphify`, one discarded warm-up run, then three interleaved repetitions of V0, VA, VT, VPA and VPT per corpus and engine. Machine load (`prep/scripts/load.sh`: CPU % over 3 s, free RAM, running containers) was sampled before and after every run; the only container running was one unrelated, idle Neo4j container. Sync wall = `t_total_s` (extraction plus every push-side pass, offline: the Neo4j push, index and embedding phases are not in it); the pass alone = `call_linking_seconds`. Medians of 3, seconds.

| Corpus | Eng | V0 wall | VA wall | VT wall | VPA wall | VPT wall | VT vs V0 | typed pass VT / VPT / VA / VPA | extraction V0 / VT / VPT | post-extraction V0 / VA / VT / VPA / VPT | load % median (min–max, n) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| self | g39 | 99.2 | 103.5 | 106.4 | 105.3 | 105.2 | +7.3% | 7.73 / 6.14 / 6.44 / 4.98 | 75.8 / 75.0 / 72.7 | 23.4 / 31.0 / 32.5 / 32.3 / 32.5 | 21.5 (6.7–42.3, 30) |
| fastapi | g39 | 69.5 | 70.7 | 70.1 | 73.4 | 70.7 | +0.8% | 2.30 / 2.20 / 2.09 / 2.37 | 58.9 / 58.4 / 58.6 | 10.5 / 12.1 / 11.8 / 13.0 / 12.1 | 17.7 (0.7–53.7, 30) |
| self | g51 | 114.2 | 123.8 | 121.8 | 125.6 | 126.9 | +6.6% | 9.56 / 7.09 / 7.07 / 5.11 | 86.9 / 83.2 / 88.3 | 27.3 / 36.9 / 39.1 / 38.8 / 40.4 | 12.5 (1.3–40.0, 30) |
| fastapi | g51 | 84.9 | 84.6 | 81.5 | 81.7 | 82.7 | -4.0% | 2.94 / 2.50 / 2.32 / 2.67 | 74.2 / 66.6 / 68.6 | 10.6 / 13.8 / 14.9 / 14.9 / 14.0 | 13.8 (3.0–37.3, 30) |

- **D3-P** (VT vs V0 sync wall ≤ +20% on fastapi and self; the pass ≤ 15 s on self; both engines). Offline, within budget; not the registered sync-log measurement: fastapi g39: sync wall 69.49 -> 70.08 s (+0.8%), pass 2.30 s; self g39: sync wall 99.16 -> 106.40 s (+7.3%), pass 7.73 s; fastapi g51: sync wall 84.86 -> 81.48 s (-4.0%), pass 2.94 s; self g51: sync wall 114.19 -> 121.78 s (+6.6%), pass 9.56 s (median of 3, offline, low load: see the load column).
- **D1-P** (map step plus push passes ≤ 1.5× the precise-off time). An offline upper bound, `(max(0, Δt_extract) + post-extraction_candidate) / post-extraction_base`; the map step sits inside `t_extract_s` and is not timed on its own, so noise in the engine subprocess can hide part of it. VPT vs VT within budget: fastapi g39: post-extraction 11.80 -> 12.13 s, extraction delta +0.17 s, bound x1.04, total x1.01; self g39: post-extraction 32.55 -> 32.47 s, extraction delta -2.26 s, bound x1.00, total x0.99; fastapi g51: post-extraction 14.87 -> 14.03 s, extraction delta +2.03 s, bound x1.08, total x1.01; self g51: post-extraction 39.13 -> 40.37 s, extraction delta +5.16 s, bound x1.16, total x1.04; VPA vs VA within budget: fastapi g39: post-extraction 12.07 -> 13.05 s, extraction delta +2.15 s, bound x1.26, total x1.04; self g39: post-extraction 31.01 -> 32.35 s, extraction delta +1.36 s, bound x1.09, total x1.02; fastapi g51: post-extraction 13.84 -> 14.94 s, extraction delta -3.72 s, bound x1.08, total x0.97; self g51: post-extraction 36.93 -> 38.81 s, extraction delta -0.10 s, bound x1.05, total x1.01.
- **Earlier, at higher load (supporting only).** Lane g2's sequential set pB (load median 21.7%, 1–50%): VT vs V0 sync wall self +3.6% / +5.0%, fastapi +4.1% / +5.9%; typed pass self 6.26 / 7.29 s, fastapi 2.27 / 2.92 s (`g2/out/p_summary.md`). The live sync logs (edge-only re-syncs, loaded 5–100%): self VT pass 9.0 s (g39, `live-0839/out/_summary/calls_census.md`) and 8.9 s (g51, `live-0851/logs`). Lane g3's self timings at 86–100% CPU (typed pass 25–26 s) are not P data.
- Evidence: `s6d/p/out/<eng>/<corpus>/` (dumps, `*.timing.json`, `loads.jsonl`, logs), `s6d/p/p_summary.json`.

## 8. Evidence index

- This lane: `<work>/s6d/` — `dumps/` + `manifest.json` (decision dumps), `score/`, `gtable.json`,
  `pool/`, `spot/`, `delta/`, `delta_summary.json`, `engdiff/`, `impact/`, `drift/`, `audit/` (`index.json`,
  `regen/`, `regen_check.json`, `scores.json`, `pooled.json`, `g3B_sheets/`), `R/` (re-scored captures, `r1_all.json`,
  `rt_matched.json`, `self/`), `gates/`, `p/`, `scripts/`.
- Kit fix and re-judgement: `<work>/kitfix/` (`determinism.json`, `rc_attestation.json`).
- G lanes: `<work>/g1`, `g2`, `g3`; reviewers: `audit-g1-A`, `audit-g1-B`, `audit-g2-A`, `audit-g2-B`,
  `audit-g3-A`, `audit-g3-B`; live lanes: `live-0839`, `live-0851`; RC tree and engines: `prep/`.
- The second, independent derivation: `<work>/derive-indep2/` (`out/index.json`, `analysis.json`,
  `audit_*.json`, `audit_pool.json`, `g6_*.json`, `r_layer.json`, `r_checks.json`, `drift_live.json`, `s_attr.json`,
  `tables.md`). `<work>/audit-A` and `audit-B` are not audit input and were not used.

## 9. Reconciliation: figures checked against the artifacts

Each item below is a place where the two derivations reported different figures or had different evidence. Each was
checked against the artifacts.

- **D3-G1, py, g51.** The gain is +27.40 points exactly (343/1175 → 665/1175, a difference of 322/1175). +27.41 is
  the difference of the rounded shares.
- **D3-G4 under VPT vs VP.** Every FALSE row in the EXTRACTED sample has `base_grade` EXTRACTED in VP: requests
  `RequestsCookieJar.__setstate__ → update` and `_copy_cookie_jar → set_cookie` (both engines), self
  `f4_ab_plan_api_r283_gates.main → gate_ab_weights_plan` (g51), and zod `ZodString._parse → toLowerCase / endsWith`
  (2 on g39, 1 on g51). These are PRECISE keys VP already held, not typed edges (`s6d/audit/scores.json`).
- **Shipped-policy one-engine typed tuples on click.** The count is 76 (`typed_all_shipped.diff`). The 50 entries in
  `shipped_policy_only51` are the gate's capped list (§3.5).
- **`tests/call_linking_harness_gates.py`.** It is registered in `tests/run_all.py` (line 444), with a 600 s entry in
  `_LONG_GATE_TIMEOUT_S` (line 65). KITFIX's "still not registered" note predates that edit.
- **Reviewer agreement.** It is complete over 2,558 distinct items (g1 570, g2 879, g3 1,109). The one item not
  agreed TRUE / FALSE is click `echo → PacifyFlushWrapper.flush` (`480e347aa8b1`, a demotion), which both reviewers
  marked UNSURE on ck-01, ck-03, ck-05 and ck-07. No reviewer gave one item two different verdicts.
- **P.** The second derivation had not seen this lane's low-load set (`s6d/p/`). It cited lane g2's set pB as the
  only P data. Both sets are offline (§7).
- **G5's gates.** The second derivation had not seen `s6d/gates/` and recorded G5 as having no gate evidence. §3.4
  re-checks those logs against the RC tree.
