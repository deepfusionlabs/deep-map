# DeepMap Playbook

Reusable engineering patterns from building DeepMap. Each entry: the problem, the pattern, and an
apply-elsewhere recipe. Portability tag: **[DeepMap]** specific · **[Portable]** reusable anywhere.

---

## 1. Re-sync-survivable regression oracle (ordering gate) **[Portable]**

**Problem.** A no-regression gate that freezes "top-K result ordering" against a live datastore is
tempting to key by the store's internal row id (Neo4j `elementId`, a DB autoincrement, a UUID). Those
ids change on every rebuild/re-sync, so the oracle is welded to one ephemeral instance and the gate
can never run in CI or after a re-index.

**Pattern.** Freeze the ordering by a **stable, source-derived key** (for code entities,
`relpath::name:line`), never the store id. Derive the key IDENTICALLY at freeze time and at gate time
(one shared helper). Normalize OS-specific bits (path separator → `/`) so the oracle is cross-OS
portable. Keep the score OUT of the diff (it is per-query / per-engine and not comparable). Freeze
AFTER all fixture-source edits are final: the gate WILL (correctly) flag a node that moved lines, so
re-freeze whenever fixture source changes.

**Recipe.** (1) Pick a key from immutable source facts. (2) `stable_key(row)` used by both producer
and checker. (3) Store `_meta.key_format` in the oracle and have the gate refuse a stale format.
(4) Prove it: freeze → tear down + rebuild the datastore → gate must still PASS.
See `tests/stage0_freeze_oracle.py`, `tests/stage0_regression_check.py`.

## 2. Lock the test harness's own logic, no datastore needed **[Portable]**

**Problem.** A live-DB gate's *own* code (key derivation, file parsing, the diff) and the committed
oracle's *shape* are invisible to CI if CI can't reach the DB, so a refactor can silently break them
or re-introduce the very bug you fixed (e.g. an elementId creeping back into the oracle).

**Pattern.** A separate **no-dependency gate** that imports the harness module (its heavy imports must
be function-local so it loads bare) and unit-tests the pure helpers + asserts invariants on the
committed artifact (regex: every key matches the stable format, none match the forbidden one). Make
the regexes discriminating and prove they are (match a real bad value, reject a good one).
See `tests/stage0_harness_gates.py`.

## 3. "Green locally, red in CI" → reproduce CI's minimal env in a throwaway venv **[Portable]**

**Problem.** A "no-DB" gate passes locally (you have every dep installed) but dies in CI on
`ImportError`, because CI installs a minimal set and the gate imports product modules that need a
runtime dep at import time. The local "GREEN N/N" was never the CI truth.

**Pattern.** Before trusting a CI-deps fix, **build a fresh venv with exactly CI's intended deps**
(nothing else) and run the suite there. It reproduces the failure and proves the fix without waiting
for a push. Confirm the heavy deps are genuinely absent (`importlib.util.find_spec`).

**Recipe.** `python -m venv tmp && tmp/Scripts/pip install <ci-deps> && tmp/Scripts/python run_all.py`.
In DeepMap, CI had installed only `pyyaml`; the fix added `neo4j` and `mcp` (the heavy ML stack stays lazy).

## 4. Keep heavy deps lazy so a "no-DB" gate needs only light ones **[Portable]**

**Problem.** Importing a module transitively pulls a multi-GB ML stack (torch), making CI slow or
forcing it to skip the gate.

**Pattern.** Import heavy deps **inside the function that uses them**, never at module top. Then a CI
gate that only exercises non-model paths imports the module with just the light deps installed. Verify
with a col-0 grep: `^(import|from) (torch|sentence_transformers|...)` should return nothing.
See `build/embeddings.py` (`_get_model` imports inside), `build/retrieval.py` (neo4j inside the
exception classifier).

## 5. Double-checked lazy-init for a long-lived, concurrent server **[Portable]**

**Problem.** `if _singleton is None: _singleton = build()` in a long-lived server (MCP/HTTP) lets two
concurrent first-requests both build it, double-loading a model, or opening a **second DB driver that
is never closed** (a real connection/fd leak).

**Pattern.** Module-level `threading.Lock` + double-check: `if x is None: with lock: if x is None: x =
build()`. Keep one-time side effects (e.g. `torch.set_num_threads(1)`) inside the lock.

**Recipe: prove it.** A RED-before-GREEN concurrency gate: patch the expensive factory with a slow,
counting stub; release N threads through a `Barrier`; assert the stub ran **exactly once**. RED (count
== N) before the lock, GREEN (count == 1) after. See `tests/lazy_init_gates.py`.

## 6. Close the parent's fd when handing a log file to a detached child **[Portable]**

**Problem.** `logf = open(...); Popen(..., stdout=logf)` leaks the parent's fd: `close_fds=True` only
affects the child, and the child gets its own dup. Harmless for a one-shot CLI; a real leak from a
long-lived "start servers" path.

**Pattern.** `logf.close()` in the parent immediately after `Popen` (the child keeps its dup'd fd).
See `build/provision.py` `start_neo4j` / `start_mcp_server`.

## 7. A contract stub must flip to real wiring once the artifact exists **[DeepMap]**

**Problem.** A generator written when a file was "absent" keeps emitting an "absent / TBD" placeholder
long after the file lands, and a pre-flight `--probe` that hard-refuses "absent" lies.

**Pattern.** Make the generator **presence-aware**: detect the artifact; emit real wiring when present
(mirror the canonical config, here `.mcp.json`'s `type:http` URLs), keep the honest placeholder only
as a fallback. Make the probe do the **runnable, no-spend** checks (file presence, TCP reachability)
and gate the genuinely-paid parts (a `claude` call) behind an explicit env guard. A gate that hasn't run
its real assertions must NOT return 0 (success); return non-zero so an exit-code check can't read
"unverified" as "passed". **Then actually implement the gated assertions** rather than leaving the stub:
here a tiny **sentinel protocol** (one cheap headless `claude` turn per arm, prompted to reply with a
single distinctive token) makes the spend minimal and keeps the PASS/FAIL logic in **pure, unit-testable
functions** (`assert_on_arm_loaded` / `assert_off_arm_isolated` / `assert_capture_path`), so the whole
gate is locked no-spend by tests that monkeypatch only the one spending helper (`_probe_call`). The
pass-path test should also assert the spend *actually happened* (a call counter), so a future
early-return-0 refactor can't silently turn the gate into a rubber stamp. See `benchmark/run_benchmark.py`
`write_on_mcp_config` / `do_probe` + `tests/benchmark_probe_gates.py`.

## 8. Escape user input before a query-language string (FTS/Lucene/SQL/regex) **[Portable]**

**Problem.** Building a query-language string from user words by interpolation (`f"{token}~2"` for a
Lucene fuzzy query) lets a code-shaped token (`foo(bar)`, `Foo::bar`, `caret^boost`, `wild*`) produce
an **invalid** query. Worse, the backend's parse error can be **misclassified as an outage** and crash
the whole request (DeepMap's FTS arm crashed `search()` on any query with `( ) ^ : *`).

**Pattern.** Escape the language's reserved characters per token before composing (Lucene:
`+ - && || ! ( ) { } [ ] ^ " ~ * ? : \ /`), drop tokens that become empty, and treat a **parse error**
as a *benign per-component* failure (degrade), distinct from a real outage (fail loud). Keep the
escape so a CLEAN token is byte-identical, preserving any frozen ordering oracle.
See `build/retrieval.py` `_build_fts_query` / `_is_fts_parse_error` / `_arm_fulltext`.

## 9. Prove a change to ranking/ordering code is no-regression by OLD-vs-NEW on one graph **[Portable]**

**Problem.** You must change a file that owns ranking/ordering (here `retrieval.py`, otherwise "frozen
byte-identical"), but need to PROVE the change doesn't shift results. A frozen oracle only helps if it
was captured from the OLD code on the SAME data, and fixture source drifts between captures.

**Pattern.** As a one-off local proof: `git show HEAD:path > old.py`; load BOTH old and new modules via
importlib; run them through the SAME live datastore + encoder on the frozen query set; assert identical
ordering. This is the gold-standard no-regression proof for an in-place change to ranking code,
independent of any committed oracle. (DeepMap: old==new top-10 on all 5 frozen clean queries → the FTS fix
shipped.) Keep it a manual proof: never put a `HEAD` read in a committed gate, because the gate flips once
the change is committed and a shallow CI clone has no history. To lock the result, assert a recorded hash
of the ranking symbols instead (see #36).

## 10. Safe archive extraction: guard zip-slip / tar-slip **[Portable]**

**Problem.** `zipfile.extractall` / `tarfile.extractall` with no member validation is CWE-22: a member
named `../../x` (or an absolute path, or a symlink target) escapes the extraction dir. The tarfile
hardening `filter=` only exists on Python 3.12+.

**Pattern.** Before `extractall`, validate every member (and tar symlink/hardlink target) resolves
inside the target: `(target / name).resolve()` must be `target` or under it. Add `filter='data'` on
3.12+ as belt-and-suspenders. Lock it with a crafted-traversal-member test that asserts rejection +
nothing escaped. See `build/provision.py` `_within` / `extract_neo4j`.

## 11. Adversarially review the FIXES, not just the original bugs **[Portable]**

**Problem.** A batch of rapid fixes (12 here) can each introduce a NEW bug, or a docstring that
over-claims what the fix achieves (DeepMap's `set_initial_password` claimed `& ^ < >` survive cmd.exe;
they don't). Verifying the *findings* isn't enough; the *fixes* need their own adversarial pass.

**Pattern.** After applying a fix batch, run a focused review that diffs `HEAD` and asks only "what NEW
defect did the fix introduce?"; empty findings is the expected, valuable result. It caught a false
docstring claim that no test would ever flag. Pair with a no-regression proof (#9) for ranking code.

## 12. A "parseable response" is not a "successful response": check the error flag, not just the schema **[Portable]**

**Problem.** A subprocess/API that returns a structured envelope often returns a **well-formed** one
*even on failure*. `claude --output-format json` emits valid JSON with `is_error:true` (and a `subtype`
like `error_max_turns` / budget-abort) that still carries `total_cost_usd` + `num_turns`. A gate that
trusted "the envelope parsed AND has the fields" would read an *errored* run as a clean success, and if
that run's answer text happened to contain the expected sentinel, the whole gate falsely passes. (Same
shape for HTTP 200-with-error-body, `git --porcelain` on a half-broken repo, a CLI that prints JSON then
exits non-zero.) An adversarial review of the *new code* (#11) is what surfaced this: the happy-path
tests were all well-formed envelopes, so they'd have stayed green past the bug.

**Pattern.** Distinguish three states, not two: **unparseable**, **parsed-but-errored**, **parsed-and-clean**.
Surface the error signal in the parser (`is_error`, `subtype`, the process exit code) and have the
"clean capture" assertion **veto** a parsed-but-errored run. Add the error envelope as an explicit test
fixture (RED-before-GREEN), a value the happy-path fixtures never construct, so the regression is
locked. For a spend/irreversible gate, fail **closed**: an unexpected/errored/ambiguous result returns
non-zero. See `parse_envelope` (`_is_error`/`_subtype`) + `assert_capture_path` in
`benchmark/run_benchmark.py`, locked by `gate_parse_envelope_error_state`.

## 13. Pin a downloaded dependency's hash OUT-OF-BAND, at build time **[Portable]**

**Problem.** Fetching a third-party artifact (a DB, a toolchain, a model) and only checking it
*decompresses* leaves a supply-chain hole: a tampered/corrupt/wrong-version download still installs. The
tempting "fetch the `.sha256` next to the artifact at download time" adds little: an attacker who can
swap the artifact can swap its sibling hash too (same host, same moment).

**Pattern.** Pin the hash **at build time, from a TLS-authenticated source, into version control**. Fetch
the official checksum once over HTTPS, bake it into source as a `{filename: sha256}` table (now
code-reviewed + diffable), and verify EVERY download *and every cache hit* against it; a mismatch **fails
loud** (refuse the artifact). Make it graceful at the edges: an operator env override (`DEEPMAP_NEO4J_SHA256`)
to pin a version you didn't bake, and an *unknown* version that **warns + falls back** to the structural
check rather than hard-failing a future release. The verify logic (`hash(bytes) == pinned?`) is pure; lock
it with a no-network test (known vector + match/mismatch/unpinned). See `KNOWN_SHA256` / `expected_sha256`
/ `verify_checksum` / `download_neo4j` in `build/provision.py`, locked by `tests/provision_checksum_gates.py`.

## 14. Multi-agent adversarial bug-hunt: find → 2-skeptic refute → both-confirm → fix RED-first **[Portable]**

**Problem.** A single-pass review (even a thorough one) misses defects and also raises false positives that
waste fix-time. You want broad coverage AND high precision: every reported defect should be real.

**Pattern.** Fan out finders over a `(module × lens)` matrix (security / resource / concurrency /
cross-platform / data-integrity / error-handling / API-contract), each grounded in real `file:line` and
told the project's FROZEN invariants so it doesn't propose forbidden changes. Then **adversarially verify
every candidate with ≥2 perspective-diverse skeptics (correctness + reproduction), and keep only the ones
BOTH confirm** (default-to-refuted → precision). In DeepMap: 38 candidates, 19 confirmed and 19 rejected.
Fix each confirmed defect **RED-before-GREEN** (a test that fails against the old code), grouped by file.
Two load-bearing lessons: (a) **a test/oracle that carries its OWN copy of the code under test won't catch
changes to the production module**: the frozen ordering oracle (`tests/oracles/`) has its own extractor,
so the `build/sync.py`/`embeddings.py` fixes had to be validated by unit tests + a live round-trip, NOT
the oracle; (b) for a change to FROZEN ranking code, the cheapest conclusive no-regression proof is that
the ranking symbols are *textually untouched* (the change is confined to error/impact paths), which is
stronger and faster than re-running the ranker. DeepMap locks it with a recorded hash of each ranking
symbol's source (`tests/fts_r7_gates.py`). See the per-fix gates added across `tests/`.

## 15. Validate plugin packaging against the OFFICIAL `claude plugin validate`, not your own schema sense **[Portable]**

**Problem.** A Claude Code plugin can *appear* installable yet be subtly broken in ways your own eyes
miss. Two real defects shipped here undetected: (1) `plugin.json` lived at the **repo root**, but plugin
discovery only reads `.claude-plugin/plugin.json`; a root manifest is **silently ignored**, so the
plugin still installed (components auto-discovered + the marketplace entry) while all its richer metadata
(keywords, author, license, full description) was dead; (2) both manifests carried a `_note` "comment"
key at the root, which the **official validator rejects** as an unrecognized key (`✘ root: Unrecognized
key "_note"`), and that exact check runs on every community-marketplace submission, so the plugin could
not pass review. Neither is visible from "it installs and works locally."

**Pattern.** Treat `claude plugin validate <path>` as ground truth and run it against **both** manifests
(`claude plugin validate .` for the marketplace, `… .claude-plugin/plugin.json` for the plugin). It has
reliable exit codes (0 pass even with warnings, 1 on a schema error), so wire it into the green gate as
an **optional best-effort step that skips where `claude` is absent** (CI). Then **mirror its load-bearing
rules offline** so CI without the CLI still catches regressions: manifest is at `.claude-plugin/plugin.json`
and there is **no root-level shadow**; **no `_`-prefixed / unrecognized root keys**; `author` is the
schema's object form (`{name: …}`), not a bare string; and **ONE coherent version** across `plugin.json`,
`.claude-plugin/marketplace.json`, and the latest released `CHANGELOG` heading. Put the changelog prose in
`CHANGELOG.md`, never in a manifest comment key.

**Recipe.** (1) `claude plugin validate .` and `claude plugin validate .claude-plugin/plugin.json`; fix
to clean. (2) A no-Neo4j gate (`tests/plugin_manifest_gates.py`) asserting location + no-`_`-keys +
author-object + version coherence, with hermetic mutation self-checks so it can't rot into a
tautology. (3) Wire both into `run_all.py`. Applies to any Claude Code plugin. See
`tests/plugin_manifest_gates.py` + the `1d)` block in `tests/run_all.py`.

## 16. Test a producer→consumer contract with ONE real round-trip, not two unit tests **[Portable]**

**Problem.** When module A writes data a separate module B reads (here: `sync` writes a
`.deepmap/graph_build.json` SHA sidecar that the benchmark's `assert_graph_at_pinned_sha` reads back),
two independent unit tests ("A writes the right shape", "B reads a hand-written fixture") both pass
even after the two sides silently drift on a field name (`built_at_sha` vs `sha`). Neither test sees the
seam.

**Pattern.** Add one test that runs the REAL producer, then feeds its REAL output to the REAL consumer,
and asserts the consumer got what the producer meant. The fixture is generated by the producer, so a
field rename breaks the round-trip immediately. Keep the per-side unit tests too (they localize a
failure); the round-trip is the one that proves the *contract*.

**Recipe.** In a hermetic temp dir: `write_graph_build_manifest(repo)` → `read_graph_build_sha(repo)` →
assert equal, then `assert_graph_at_pinned_sha(repo, sha) is None` and `(…, wrong) is not None`. See
`tests/sync_manifest_gates.py` (the "producer->consumer round-trip" check). Applies to any writer/reader
pair across a module or process boundary (config emit/parse, cache write/read, serialize/deserialize).

## 17. Make a gate BEHAVIORAL, never `"literal" in source_code` **[Portable]**

**Problem.** A gate that asserts `"embeddings skipped" in sync_src` passes as long as that *string*
exists: it survives any behavioral break (a too-broad `except`, a removed continue) as long as the
comment stays. It is a tautology dressed as a test: green tells you nothing.

**Pattern.** Exercise the real function and assert the *outcome*. Stub only the heavy, irrelevant
collaborators (extract/push/index), then drive the one variable under test (the embedder) through its
states and assert what the contract promises: model-unavailable (`ImportError`/`OSError`) → still
returns success (degrade); a non-model error (`RuntimeError`) → propagates (FAILS LOUD, proving the
`except` is narrow); a 0-node extraction → the wipe is never called. Each assertion fails under the
mutation it targets; that is the definition of a non-tautological test.

**Recipe.** Load the module, monkeypatch its module-level collaborators to `SimpleNamespace` stubs,
call the real entrypoint in a temp dir, assert the return/side-effect. See `tests/degradation_gates.py`
(the "BEHAVIORAL" sync-degradation block). Applies anywhere you were tempted to grep the source.

## 18. A spending CLI: ENV-guard the spend, INJECT the call_fn so it's unit-testable for free **[Portable]**

**Problem.** A CLI step that calls a paid model (the blind-scoring judge) can't be exercised in CI
without spending, so it ships untested, and the honesty logic around it (score only when the judge
succeeds; never fabricate on failure) is exactly what most needs a test.

**Pattern.** Two-layer it. (1) **ENV-guard** the real spend: the command refuses to run unless an
explicit flag is set (`DEEPMAP_REAL_ENGINE=1`), so it is inert by default and can never spend by
accident. (2) **Inject** the spending call as a parameter (`judge_call_fn`, `read_answer_fn`) into a
pure orchestrator, and have the thin CLI wire the real (spending) implementations. Now the orchestrator,
including every honesty branch (judge raises → card stays UNSCORED → run can't read `measured`), is
unit-tested with trivial mocks and zero spend; only the un-testable last mile (the actual subprocess)
is left, and that's just argv construction, which is itself a pure, tested function.

**Recipe.** `score_captured_run(results_dir, repos, questions, judge_call_fn, read_answer_fn)` is pure;
`do_score` env-guards + injects the real `_judge_call_factory`. Test build_judge_argv (asserts a
DIFFERENT model + isolation), the orchestrator with a mocked judge (measured), a boom-judge (stays
unscored), and the env-guard (returns 2, no spend). See `tests/benchmark_score_cli_gates.py`. Applies to
any metered/irreversible CLI step (LLM calls, deploys, sends).

## 19. Make a repo-coupled tool repo-PARAMETRIC, and fail-closed on out-of-band state **[Portable]**

**Problem.** A validation harness welded to its own repo can't validate anything else: it computed file
keys relative to its OWN root, so an external clone produced absolute, non-portable keys. And one side of
a fairness comparison (the ON-arm Neo4j graph) was built *out-of-band*: the harness verified the clone's
SHA but blindly trusted the graph's, so a stale graph silently biased every result.

**Pattern.** (a) **Parametrize the base, not the tool**: take an explicit `--repo-dir`/`--relpath-base`
and key everything as a forward-slash relpath under it, so two clones at different absolute paths yield
identical keys (unit-test exactly that). Add a small **registry** (id → {repo_dir, oracle_path, …}) so a
gate can find any repo's frozen baseline by id. (b) **Verify out-of-band state, fail closed**: don't
assume the externally-built artifact matches; read its provenance (a sidecar the builder stamps, or an
operator-declared env) and refuse to proceed (FAIL CLOSED) on a mismatch *or* when it can't be verified
at all (unverifiable ≠ free pass).

**Recipe.** `tests/stage0_portable.py` (`relpath_for`, registry), repo-parametric `stage0_real_graph.py`
and `--oracle-path`/`--repo-id` on the gate; `assert_graph_at_pinned_sha` reads the sync-stamped
`.deepmap/graph_build.json` (or `DEEPMAP_GRAPH_SHA`) and fails closed on stale/unknown. See
`tests/stage0_external_gates.py` + `tests/benchmark_fairness_gates.py`. Applies to any "validate an
arbitrary target" harness and any comparison that depends on separately-built inputs.

## 20. Per-ITEM isolation must wrap the WHOLE item body, not just the risky-looking first step **[Portable]**

**Problem.** A per-file loop guarded the parse step (`try: tree = ast.parse(...) except: skip`) but did
the rest of the work (visit the tree, summarize, append) OUTSIDE the `try`. A *post-parse* failure
(a `RecursionError` on pathological nesting that survived parsing) then propagated out of the loop and
aborted the ENTIRE batch, losing every remaining item. The guard looked like per-item isolation but
only covered the one step the author imagined could fail.

**Pattern.** When the contract is "one bad item is skipped, the rest succeed," the `try` must cover the
ENTIRE per-item body (every stage that runs for that item) and commit the item's results
**atomically** (build into locals; extend the shared output only if the whole item succeeds, so a
mid-item failure leaves no half-added partial). Then prove it: inject a failure into a *post-first-step*
stage (not just the obvious one) and assert the OTHER items still come through and the failed one is
fully absent. A test that only fails the parse step would never have caught this.

**Recipe.** `build/sync.py` `_rebuild_code_ast` widened its per-file `try` to cover parse→visit→summarize
and commits `file_nodes` atomically; `tests/confidence_gates.py:gate_post_parse_isolation` injects a
post-parse `RecursionError` on file 1 of 2 and asserts file 2 survives. Applies to any
parse/transform/ingest loop with a per-item skip contract (extractors, importers, batch processors).

## 21. Live-validate against a THROWAWAY datastore, not your working one **[Portable]**

**Problem.** Confirming a change live is valuable, but running the real pipeline against the *working*
datastore mutates it as a side effect. Here a confirmatory `deepmap_cli.py sync .` re-validated the
graph-build manifest + the extractor refactor end-to-end, but it grew the maintainer's dev graph from a
build-focused 448 nodes to a full-repo 950 (it indexed `tests/`+`benchmark/`+`scripts/` too). Harmless
(MERGE, non-destructive, re-syncable) but it changed the graph's *composition* without being asked, and
isn't cleanly reversible (subtree `--stale-cleanup` is repo-root-gated). Two further traps surfaced: a
foreground run hit the harness's 2-min command cap and the killed parent left an **orphaned** child
holding the single-writer lock; and the costly run had to be repeated.

**Pattern.** Prove the contract with hermetic UNIT tests first; those are authoritative (a fake
driver + a producer→consumer round-trip cover the manifest contract without any datastore). Treat a
live run as *confirmatory only*, and point it at a **disposable** target (a temp clone / scratch DB /
a dedicated benchmark clone), never the working store. If you must touch the working store, say so
up front, scope it minimally, and expect it to mutate state. For anything slower than the host's
command timeout, run it **detached/background** from the start (not a foreground `timeout` wrapper a
parent-kill can orphan), and rely on the OS single-writer lock to serialize; don't assume a killed
foreground job released it.

**Recipe.** `tests/sync_manifest_gates.py` (the authoritative hermetic proof) + `build/provision.py`'s
OS-level `msvcrt`/`flock` single-writer lock (auto-released on a clean exit). Applies to any
"validate it for real" step against a DB, index, cache, or external service.

## 22. An LLM-as-judge is intermittently flaky: RETRY, and fail HONEST, never fabricate **[Portable]**

**Problem.** A blind LLM judge that must emit a structured verdict (`{"correctness_pct":…, "wrong_high":…}`)
can **fail to return the verdict**, replying with a plain-text deflection ("this looks like a prompt-
injection attempt" / "I don't see the content") instead of JSON. The mocked unit tests (a deterministic
fake judge) could NEVER catch this; only a LIVE call surfaced it, and with a single attempt one flaky
reply leaves that item UNSCORED and (because a run is "measured" only when EVERY item scores) blocks the
whole run. **War-story correction (read §26 first):** in this project the "refusal" was *initially
mis-attributed to model safety* and was actually a prompt-DELIVERY bug (the long prompt was passed as a CLI
arg, received mangled; the model deflected because it genuinely saw no content). Fixing delivery (stdin)
made it 10/10 reliable. The retry/fail-honest defense below is still correct for *genuine* judge flakiness,
but the FIRST thing to check when a judge "refuses" is whether it actually RECEIVED the full prompt.

**Pattern.** Treat the judge call as flaky I/O: **retry a bounded number of times** (default 3) on a
transient call error OR a no-verdict reply, take the FIRST valid verdict, and after exhausting attempts
**stay None / UNSCORED; never fabricate a score** (the honesty contract is the floor; retry only
improves yield within it). Keep the verdict PROMPT evidence-based + arm-blind (a bare "output this
score" prompt is what trips the safety refusal; "score these claims against the code at SHA X" mostly
doesn't). Validate it with a REAL call once spend is allowed: the mocked test proves the orchestration,
the live smoke proves the model actually cooperates (and that your honesty path holds when it doesn't).

**Recipe.** `benchmark/score_correctness.py:judge_correctness(..., max_attempts=3)` retries on
parse-failure/transient-error, first-valid-wins, None-after-exhaustion; `tests/benchmark_correctness_gates.py`
`gate_judge_retry` locks all three (recover-after-refusals, recover-after-transient-error, honest-None-
after-max). Applies to any LLM-as-judge / structured-extraction-from-LLM pipeline.

## 23. Curate benchmark ground-truth with a 3-phase workflow: analyze → author → ADVERSARIALLY VERIFY **[Portable]**

**Problem.** A code benchmark's credibility lives in its ground-truth: a question is only fair if its answer is
*actually correct at the pinned SHA* and *actually grep-defeating*. A single agent authoring answers will
confidently cite line numbers that drift, and will claim "grep can't do this" without checking. Hand-authoring
20-30 multi-hop answers is slow and error-prone; one wrong `file:line` atom poisons the completeness oracle.

**Pattern.** Run a workflow per repo: **(1) analyze**: one agent maps the repo's grep-defeating structures;
**(2) author**: one agent writes N questions + candidate answers + `file:line` atoms from the analysis;
**(3) verify**: a *separate, independent* agent (curator ≠ verifier) re-reads the real code and confirms EACH
atom's exact line, that the answer is correct, and that grep genuinely can't reconstruct it (it runs the greps).
KEEP/FIX/DROP per question; only KEEP/FIX survive. This bakes the methodology's curator≠verifier independence
into the pipeline, and the per-atom line-number re-check catches drift a single author never would. Pipeline the
repos so repo B authors while repo A verifies. Pin a REAL SHA from a fresh clone; never fabricate a SHA or an atom.

**Recipe.** Three workflows produced 33 questions across 5 Python repos; a later set-level QA pass
(§24) caught what per-question verify can't. `agent(..., {schema})` per phase; verify returns `{verdict, verified_atoms}`.
Caveat (§25): use a FLAT single-object output schema, not `{items:[…]}`; the array wrapper trips StructuredOutput.

## 24. Per-item verification ≠ set-level quality: run a SEPARATE balance/fairness review **[Portable]**

**Problem.** Every question can be individually correct and the SET still be skewed: a benchmark where 0/30
questions exercise the product's *headline* feature (here: reverse-reachability / blast-radius, exactly what
`deepmap_impact` is for) looks thorough but tests the wrong thing, and a skeptic will say it was steered.
Per-question adversarial verify (§23) cannot see this: it never asks "what's missing across the whole set?"

**Pattern.** After per-item curation, run a **set-level QA review with diverse lenses** (balance/coverage,
claim-validity with live spot-checks, fairness-to-the-baseline) over the WHOLE set, each returning a score +
findings, then synthesize a verdict. Treat its HIGH findings as real work. The most dangerous finding is a
*coverage gap on your own headline claim*; the second is a *baseline that's too weak* (§…): if the question's
"grep can't do this" baseline models a naive prose-grep instead of a smart agent grepping the obvious
identifier, every "win" is an artifact. Reframe each baseline to the honest agentic floor (concede what
grep+read recovers; pin the edge to the transitive/cross-library/runtime residue); then a win is defensible.

**Recipe.** A 3-lens review workflow (verdict *ship-with-minor-fixes*) caught 0 multi_hop coverage + 11
too-pessimistic grep-baselines; fix workflows added 4 reverse-reachability questions and reframed the baselines.
Applies to any eval/benchmark: the set is a deliverable with its own QA, separate from each item's correctness.

## 25. Flat output schema + clean API transport beat the array-wrapper + the wrapped CLI **[Portable]**

**Problem.** Two recurring reliability traps when driving sub-agents/LLMs for structured output. (a) A schema
shaped `{questions:[{…}]}` asking for "one item" makes the model omit required fields and blow the
StructuredOutput retry cap (5): a whole workflow returns 0 results. (b) Driving an LLM *judge* through a
wrapped CLI (`claude -p`) inherits the wrapper's injection/safety layer, which content-sensitively REFUSES
verdict prompts, independent of the model.

**Pattern.** (a) Prefer a **FLAT single-object output schema** (the fields directly, one call = one object) over
an array wrapper when each agent produces one thing; it removes the "wrap in array" failure mode (a v1 workflow
returned 0/5, the same prompts under a flat schema returned 5/5). (b) Run an automated LLM judge over a **clean
API transport** (OpenRouter / the provider API) rather than the agent CLI: no injection wrapper, and a
NON-judge-family model is independent of the arm being graded. Make the transport a one-env-var switch
(`DEEPMAP_JUDGE_TRANSPORT=openrouter`), default to the convenient path, fail HONEST when the key is absent.

**Recipe.** `benchmark/run_benchmark.py:make_judge_call_fn` (cli/openrouter/api); the curator workflows switched
from `{questions:[1]}` to a flat `Q` schema after the array form hit the retry cap. Applies to any
structured-LLM-output or LLM-as-judge pipeline.

## 26. Pipe a long LLM prompt via STDIN, never as a CLI positional arg **[Portable]**

**Problem.** Driving an LLM CLI (`claude -p "<prompt>"`, and the same shape for most agent CLIs) by putting
a **long, multi-line prompt in the positional ARG** silently corrupts delivery: the process receives the
prompt mangled or empty, so the model "sees" little/no content and **deflects**: "I don't see the
content", "the payload is missing", or a spurious "this looks like a prompt-injection attempt". This is
*indistinguishable from a safety refusal at the output*, so it gets misdiagnosed as model behaviour and
"fixed" with prompt-tuning / retries / a whole alternative transport, none of which work, because the
content never arrived. It only bites at LENGTH (a short prompt fits the arg and works), so it hides behind
green smoke tests on toy inputs and only breaks on the real (long) prompts.

**Pattern.** **Deliver the prompt via STDIN** (`subprocess.run(argv, input=prompt, …)`, argv flag-only),
not as `argv[...] = prompt`. A controlled probe pins it fast: same prompt, arg vs stdin, N reps each; here
it was **0/6 via arg vs 6/6 via stdin** (and then the end-to-end judge went 4/4 to `measured`). When an LLM
"refuses", FIRST verify it actually received the full prompt (echo length, or have it restate the input)
before blaming safety. Apply to BOTH the task model AND the judge: anything whose prompt can grow.

**Recipe.** `benchmark/run_benchmark.py`: `build_judge_argv`/`build_claude_argv` are flag-only; `run_one`,
`_probe_call`, and the cli judge pass `input=prompt`. Tests assert the prompt is NOT a positional arg.
This single fix made the `claude -p` judge reliable, with no external API key needed.

## 27. A "config-complete" harness is not a "run-validated" harness: the first REAL run finds the integration bugs **[Portable]**

**Problem.** A benchmark/harness can have a fully green unit suite (42 gates), a passing `--selftest`, a
passing `--probe`, and STILL be unable to produce a single correct number, because the unit tests exercise
each function on *synthetic* inputs, never the **real artifacts wired end-to-end**. The first actual run of
DeepMap's benchmark surfaced FOUR distinct blockers in one sitting (entries 28–31), none of which any
existing green gate caught, because each lived in the *seam* between two components (sync↔pin-check,
curator-format↔scorer, capture-path↔scorer, code↔console).

**Pattern.** Treat "config-complete" and "run-validated" as different milestones. Before trusting a harness,
**run ONE real slice end-to-end** (here: one repo, one question, both arms → capture → score → `measured`)
and read the actual output. Each seam bug you hit becomes a RED-before-GREEN gate on the *real* shape (a
real curated atom, a real clone with sidecars, a real second question, a real non-ASCII judge reply). The
calibration slice is cheap insurance against discovering these after committing hours of paid runs.

**Recipe.** (1) Calibrate on the smallest real slice first; print timings + a peek at the captured artifact.
(2) For every seam bug, add a gate keyed to the REAL format, not a toy. (3) Only then scale to the matrix.
See entries 28–31: all found on the first real `run_benchmark.py` invocation, all fixed RED→GREEN.

## 28. A deterministic matcher must accept the curator's REAL atom format, not an idealized one **[Portable]**

**Problem.** The HERO completeness metric matched an answer against `ground_truth_set` atoms of clean shapes
(`file:line`, `symbol`, `symbol@loc`, `edge`). But the curator authored all 234 real atoms as RICH strings:
`src/requests/sessions.py:784 — Session.send (adapter.send call: r = adapter.send(...))`. A rich atom matched
none of the clean shapes, fell through to a whole-string word match, and scored **0% for every good answer**,
silently turning the headline metric into garbage. Both the scorer's tests and the curator's authoring were
internally "correct"; they just disagreed on the format, and nothing checked them *against each other*.

**Pattern.** When a deterministic scorer consumes human-authored data, **pin the contract to a real sample of
that data**, and make the matcher parse the structured prefix out of a descriptive atom (here: the LEADING
`file:line` is the locator; the trailing prose is documentation that may itself contain `->`, `:`, parens).
Keep it ADDITIVE: fire the new path only when the atom truly is `file:line <space> description` (and not a
`symbol@loc`), so every existing clean-atom guarantee (word-bounding, right-bounded line numbers) is untouched.

**Recipe.** `benchmark/score_completeness.py::_atom_covered`: if `first_token` is a pure `file:line` (no `@`)
and there is trailing text → credit on the leading file:line. Gate it with a REAL curated atom + a 4-atom
partial-credit set + the `->`-in-description case (`tests/benchmark_scoring_gates.py::gate_rich_curated_atom_format`).

## 29. Per-item output paths must be keyed by the ITEM id, or concurrent items silently overwrite each other **[Portable]**

**Problem.** The capture wrote each run's raw output (the answer the scorer later reads) to
`<repo>/<arm>/run-<n>/`, keyed by repo, arm, run, but **NOT the question id**. Every question in a repo
therefore wrote to the SAME path, overwriting the previous one; only the LAST question's answer per
(arm,run) survived on disk, and EVERY scorecard's `answer_text_path` pointed at it. The in-memory metrics
looked fine (distinct per question), so nothing flagged it until you noticed the on-disk record count was
stuck at 4 while 24 runs "completed". Scoring would have graded all 6 questions against question-6's answer.

**Pattern.** Any path that persists per-item artifacts must include EVERY dimension that distinguishes an
item: here the **question id** alongside repo/arm/run. Extract the path construction into a PURE helper and
unit-test that two different items yield two different paths. Watch for the tell: a "completed N items" log
while the artifact count plateaus far below N.

**Recipe.** `benchmark/run_benchmark.py::run_output_dir(out, question, arm, run)` →
`<repo>/<question_id>/<arm>/run-<n>`; gate asserts different question ids ⇒ different dirs
(`tests/benchmark_run_scale_gates.py::gate_run_output_dir_unique_per_question`).

## 30. The git "dirty" pre-flight must ignore the harness's OWN untracked sidecars **[Portable]**

**Problem.** A fairness gate refused to run on a clone whose worktree was "dirty" (`git status --porcelain`
non-empty). But the harness itself drops UNTRACKED sidecars into that clone: `.deepmap/graph_build.json`
(written by sync, and REQUIRED by the graph-SHA check) and `.oss-kit/` (a Stop-hook artifact of running an
agent CLI in the clone). A third-party clone doesn't gitignore them, so the dirty check and the graph-SHA
check became mutually exclusive: a deadlock that made every run fail closed.

**Pattern.** "Dirty" for a measurement gate means **a modified TRACKED file** (what a grep of the pinned
source would see), not "any untracked file". Filter `git status --porcelain` to lines NOT starting with `??`;
let the per-run pristine reset (`git clean -fdx --exclude=.deepmap`) handle untracked leakage. Keep the
modified-tracked-file case still failing closed (that IS contamination).

**Recipe.** `benchmark/run_benchmark.py::assert_pinned_clean` keeps only non-`??` porcelain lines; gate proves
an untracked `.deepmap/`+`.oss-kit/` is clean while a modified tracked file still fails
(`tests/benchmark_honesty_gates.py::gate_assert_pinned_clean_ignores_untracked_sidecars`).

## 31. Make the CLI's OWN console UTF-8-safe; don't depend on the caller's PYTHONIOENCODING **[Portable]**

**Problem.** Status prints carrying glyphs (`—`, `→`) crashed with `UnicodeEncodeError` on a legacy Windows
codepage (cp1252/cp1255) the moment the tool was run on a plain console without `PYTHONIOENCODING=utf-8`. The
crash came AFTER the real work (`--merge` had already written its files), so the tool exited non-zero on a
successful operation, and an orchestrator reads that as FAILED. The result FILES were always utf-8; only the
console echo blew up.

**Pattern.** A CLI must make its own stdout/stderr encoding-safe at startup, not rely on the environment.
`sys.stdout.reconfigure(encoding="utf-8", errors="replace")` (best-effort, idempotent) at `main()` entry
down-converts the console echo so a display codepage can never abort the process; the written files stay
strictly utf-8 (explicit `encoding=` on every write).

**Recipe.** `benchmark/run_benchmark.py::_ensure_console_utf8()` called first thing in `main()`; gate simulates
a cp1255-strict stream and asserts printing `→`/`—` after the safeguard does not raise
(`tests/benchmark_run_scale_gates.py::gate_console_encoding_safe`).

## 32. Add a new backend by matching the consumer's EXACT input shape: the "parity keystone" + "money gate" **[Portable]**

**Problem.** You need to feed a NEW producer (a non-Claude LLM arm) into an existing, well-tested, *frozen*
consumer pipeline (a blind scorer + aggregator). The tempting move, teaching the consumer about the new
producer (a branch, an adapter inside the scorer, a new code path), spreads producer-specific logic across
the frozen code, risks regressing the validated path, and needs the whole downstream re-tested for the new
shape. It also blurs the audit story ("is the new arm graded the SAME way as the old one?").

**Pattern.** Identify the ONE data structure the consumer actually reads (here: a `claude --output-format
json` envelope persisted as `stdout.json`, keyed off `parse_envelope`), and make the new producer synthesize
a **byte-compatible** instance of it: same keys, same types, same semantics (final-vs-error rides the same
`is_error`/`subtype`). Then EVERY downstream consumer works with ZERO edits, and the new backend is graded
*identically* by construction. Prove it with a **"money gate"**: a test that runs the new producer, then
feeds its real output through the UNMODIFIED downstream (the actual scorer, not a mock of it), and asserts it
scores exactly like the old backend's card. That single test is the parity contract; if it passes, the frozen
code is provably untouched-and-compatible.

**Recipe.** `benchmark/arm_openrouter.py::build_openrouter_envelope` mirrors the Claude envelope; `run_one_openrouter`
is a drop-in twin of `run_one` (same record key set, same per-run files, same `answer_text_path`). The money
gate (`tests/benchmark_arm_runner_gates.py::gate_money`) runs an OpenRouter card through the FROZEN
`score_run.score_records` + `score_completeness` and asserts it scores as a Claude card would. `build/retrieval.py`
and the scorers stayed byte-unchanged. Namespace the new backend's ids (`deepmap_on@<model>`) so it groups
alongside (not on top of) the old one.

## 33. Unit-test an agentic tool-use loop by INJECTING both transports: scripted fakes, no key/network/spend **[Portable]**

**Problem.** An agentic loop (model ↔ tools, multi-turn) is exactly the code that "can only be tested live":
it calls a paid LLM API and hits real tool backends (here: HTTP MCP servers over Neo4j). So it ships
untested, and its load-bearing HONESTY paths (a hard cap must end with `is_error=True` + an EMPTY answer,
never a fabricated one; a single tool failure must not crash the run) are precisely the ones never exercised
until they fail in production.

**Pattern.** Make the loop take its two side-effecting dependencies as **injected callables**: the network
`transport(messages, tools)` and the tool `deepmap_transport(name, args)`. Tests pass a *scripted* transport
(a queue of canned response dicts that also records the messages it was handed, so you can assert the loop
appended tool results before the next turn) and a fake tool transport. Now every branch is hermetic and free:
multi-tool-call turns, malformed tool arguments, each HARD cap (max_turns / budget / max_output_tokens), a
tool that raises. Drive the caps by scripting extreme token/cost usage rather than real spend. The real
transports are thin factories built only when no fake is injected, so production wires the network, tests
never do.

**Recipe.** `benchmark/arm_openrouter.py::run_agentic_loop(*, transport, deepmap_transport, …)`: both
injected; `run_one_openrouter` builds the real ones (`_openrouter_chat_transport`, `default_deepmap_transport`)
only when `transport is None`. `tests/benchmark_arm_runner_gates.py` proves the happy path, the three caps
(is_error + empty answer), multi-tool turns, malformed args, an empty-choices response, AND the deepmap MCP
wire protocol via a mocked urlopen: 107 checks, no key/network/Neo4j/spend. Pair with entry #18 (ENV-guard
the spend, inject the call_fn) and #22 (fail honest).

## 34. A cost/size figure hardcoded in prose DRIFTS when its input config changes: cite the live tool, and re-derive before you quote it **[Portable]**

**Problem.** A spend estimate was written into a runbook, a design doc and a status page while the config
had 9 questions; the shipped config later grew to 33. Cost scales with the question count, so the estimate
became a **3-4x understatement**, sitting on the exact doc that gates whether someone spends real money.
Nothing was "wrong" when written; the number silently rotted because it was a
SNAPSHOT of a computed value, copied into prose, disconnected from the config it derived from. The same trap
hits any figure computed from a moving input: test counts, model prices, row counts, benchmark sizes.

**Pattern.** Before quoting a derived figure, **re-run the tool that computes it against the CURRENT input**
and use that number; never trust a figure already in the docs. Where the figure gates a decision (spend,
capacity), name the LIVE tool as the source of truth ("`cost_estimate.py` prints the live figure") and, best
of all, make the RUNTIME print the freshly-computed projection at the decision point so a stale doc can never
mislead: the doc is a convenience, the tool is the contract. When you do quote a number, tie it to the input
it assumes ("~$X for the **N-question** config") so the next reader can tell if it still applies.

**Recipe.** `run_benchmark.py::do_run` prints the `cost_estimate.estimate_costs` projection from
the live `repos.yaml`/`questions.yaml` before any spend and refuses without `--confirm-spend`; the docs cite
that tool and tag the figure with the config size. The drift was caught by asserting the pure `estimate_costs`
matches the printing `estimate()` on the REAL config, then reconciling every prose figure to it. The same
rule as reconciling any count with its source, applied to computed cost.

## 35. Fuzz the code that parses UNTRUSTED external output: the "it can't be that shape" branch is where it crashes **[Portable]**

**Problem.** A loop that consumes another system's output (an LLM API response, a webhook, a third-party
JSON payload) is written against the *documented* shape: `choices[0].message.tool_calls` is a list, `usage`
tokens are ints. The happy-path tests all pass. But the real source is untrusted: a model can return
`tool_calls` as a **dict** (not a list), a token field as the string `"n/a"`, `content` as `null`, or an
empty `choices`. `for tc in tool_calls` over a dict iterates its *keys* (strings) → `tc.get(...)` →
`AttributeError`, mid-loop, on live input you never unit-tested. Curated edge-case tests miss it because you
only write the malformations you *thought of*.

**Pattern.** Add a **deterministic fuzz gate**: a seeded PRNG generates hundreds of randomly-malformed
responses (mix valid + every wrong-type/missing-field variant) and asserts the consumer (a) **never raises**
and (b) always returns a **well-formed result** (required keys, non-negative counts, a valid enum for the
terminal state). Run enough iterations (here 400) that combinations surface. When it goes RED, harden the
parser: guard every external field with `isinstance` before use (`resp`/`usage`/`choices`/`message`/
`tool_calls`/each `tool_call`/`function`), coerce numerics through a `_safe_int` that returns 0 on garbage,
and coerce text through an `_as_text`. Malformed input should **degrade** (skip the entry, end at a cap as an
honest error), never crash. Seed the PRNG (no wall-clock/entropy) so the gate is reproducible.

**Recipe.** `tests/benchmark_arm_runner_gates.py::gate_loop_fuzz` (400 seeded iterations) drove the hardening
of `benchmark/arm_openrouter.py::run_agentic_loop` + `price_of` (`_safe_int`/`_as_text` + `isinstance` guards
on every response field): RED (103/400 crashed on a dict-shaped `tool_calls`) → GREEN (0 crashes). Pair with
#33 (inject the transport) so the fuzz needs no network. Kin of the scorer fuzz (`tests/scorer_fuzz_gates.py`).

## 36. Build a gated feature's FOUNDATION without activating it: the "applied:false, frozen-core" pattern **[Portable]**

**Problem.** You want to build a feature (learned ranking weights) whose *activation* is gated on a result
that hasn't landed yet (a benchmark A/B), and whose ranking core is under a hard freeze. The instinct is to
wait: "can't build it without touching the ranker." But most of the feature is the *machinery around* the
gated switch: data collection, the offline learner, persistence, safety. All of that is buildable NOW if you
keep it strictly OBSERVING, never MUTATING, the frozen path.

**Pattern.** Build the whole pipeline as **net-new modules that read but never change** the frozen core.
Persist the learned artifact with an explicit **`applied: false`** flag and a namespaced key the frozen core
does NOT read, so the artifact exists, is inspectable and is tested, but is provably inert until a
deliberate, gated switch turns it on. Gate the invariant: assert `applied` is false by default; assert a
recorded hash of the frozen symbols' source (as `tests/fts_r7_gates.py` does), never a diff against `HEAD`,
which flips once the change is committed and fails in a shallow CI clone; and assert the core reads the
artifact only through one gated seam. The feature ships "loaded but safetied"; opening the gate later is a
small, well-tested change, not a from-scratch build.

**Recipe.** DeepMap's opt-in learned fusion weights ([`RM1-ENABLE.md`](RM1-ENABLE.md)):
`build/retrieval_log.py`, `hooks/deepmap_access_hook.py`, `build/tune_weights.py` and `deepmap tune` were built
and tested while the ranking functions stayed hash-locked. The tuner writes `.deepmap/instance.json` `learned_fusion.applied=false`;
live search reads learned weights only through `build/learned_fusion.py`'s four-condition gate (an opt-in env
switch, `applied`, `beats_equal` and `significant` all true, where `applied` needs benchmark evidence bound to
the current weights), which passes them to the retriever's existing `arm_weights` parameter. Pair with #37 (the
lossless-replay trick that makes "observe, don't mutate" faithful) and #33 (inject transports so the whole
thing tests with no live backend).

## 37. If your fusion/ranking is RANK-based, log ordered id lists: you can replay it losslessly offline **[Portable]**

**Problem.** To learn better fusion weights offline you must re-run fusion under candidate weights over real
queries. The naive assumption is you must log every arm's raw *scores* (heavy, and they leak the ranker's
internals). And re-deriving the candidate set risks drifting from what production actually fused.

**Pattern.** Check whether your fusion consumes **scores** or only **ranks**. Reciprocal Rank Fusion (and
Weighted-RRF) converts each arm's scores to *ranks* internally (`1/(k+rank)`), so the raw scores are
discarded. That means logging each arm's candidate ids **in ranked order** is **fusion-lossless**: to replay,
map ordered ids → any strictly-descending synthetic scores (`{id: -index}`) and feed the SAME production
fusion function. You get identical output with a tiny, score-free log. Lock it with a **keystone gate**: for a
live query, assert that re-fusing the logged per-arm ordered ids (equal weights) reproduces the frozen
`search()`'s exact top-k. That one assertion proves the log carries everything the tuner needs AND that your
"observe" path can't silently diverge from the "rank" path.

**Recipe.** `build/retrieval_log.py::capture_search` takes returned ids straight from the frozen
`search()` and records per-arm ordered ids; `build/tune_weights.py::_fuse` re-fuses them via the REAL
`retrieval._weighted_rrf` (imported, asserted by identity, never re-implemented). Keystone:
`tests/retrieval_log_gates.py::gate_capture_search_keystone`. Because the log is score-free ordered ids, it is
also lighter and less revealing than a score dump: a privacy bonus for a local, zero-egress log.

## 38. Make an incremental cache safe by construction: content-hash gate + a shared per-unit function + an "== full" identity gate **[Portable]**

**Problem.** You want to skip re-processing unchanged inputs (re-parsing unchanged source files, re-rendering
unchanged pages, re-encoding unchanged rows) but the output MUST stay identical to a from-scratch run. The
danger is a cache that returns a stale result: a wrong output that looks fine until someone notices the graph
is subtly off. Time-/mtime-based caches are the classic trap (they miss content that changed without a newer
mtime, and invalidate content that didn't).

**Pattern.** Three moves make it safe *by construction*, not by hope: (1) **key the cache on a content hash of
the raw bytes**, so a HIT means "same input ⇒ same output"; a stale/cold/corrupt/version-mismatched cache can
then only ever cause extra *misses* (re-processing), never a wrong result. Make the loader tolerant: any
problem ⇒ empty cache ⇒ a full run. (2) **Extract ONE shared per-unit function** that BOTH the full path and
the cached path call, so a cache MISS is byte-identical to the full path (no second, drifting implementation).
Iterate both paths over the SAME deterministic unit order so the concatenation is order-identical too. (3) Prove
it with an **"incremental == full" identity gate**: build a fixture, run full → run incremental (cold then
warm), assert equal; then ADD/CHANGE/DELETE/MOVE a unit and assert equal to a fresh full run each time; and
prove the gate has TEETH by momentarily dropping the hash check and confirming it goes RED. Keep the full run as
the default; ship the cache behind a flag until it's field-proven. Guard the edge: an *unreadable* input must
not share a hash with an *empty* one (use a sentinel hash), or a later real file reuses the wrong empty result.

**Recipe.** DeepMap incremental sync: `build/extract_cache.py` hashes raw file bytes
(`hashlib.sha256[:16]`), `build/sync.py` shares `_iter_ast_files` + `_extract_ast_file` between the full walk
and the incremental driver, and `tests/incremental_sync_gates.py` locks `incremental == full` on cold/warm +
add/change/delete/move + a push-layer graph-identity capture, with a RED-under-mutation gate that drops the
hash check and confirms divergence. Behind `DEEPMAP_INCREMENTAL_SYNC` (default OFF), full rebuild is the
fallback. Scope honestly: it covers the per-file `ast` path; the whole-tree `graphify` subprocess can't be
decomposed per-file, so it always runs full (a black box you can't cache is a full run, not a guess). Pairs with
the embedder's per-node `_embed_hash` (the same content-hash idea, one layer down).
