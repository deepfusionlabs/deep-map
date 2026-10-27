# How DeepMap is tested

DeepMap is tested two ways: an automated suite that needs no database and runs on every push, and live test runs on
real Neo4j instances, the real MCP servers and the real embedding model. This page says what each one covers, where
it ran and what was measured, and what is not covered yet. What DeepMap itself does not do yet is in
[`known-limitations.md`](known-limitations.md).

## The automated suite

- `python tests/run_all.py` runs every test suite under [`tests/`](../tests/), more than 200 of them, with no Neo4j,
  no network and no spend. It also compiles the Python sources, checks that both installers parse, scans the public
  copy for claims the code does not back ([`tests/copy_honesty_scan.py`](../tests/copy_honesty_scan.py)) and
  dry-runs the benchmark harness. A suite file that `run_all.py` does not list fails the run, so no test is skipped
  by accident.
- GitHub Actions runs it on every push and pull request: Linux, the newest Python 3, and the newest release of the
  parsing engine that DeepMap allows.
- It is also run by hand on Windows 11 (Python 3.13, the oldest engine release DeepMap allows) and in Linux
  containers (Python 3.10, Python 3.11 and an image with only `python3`). The engine's output changes between the
  releases DeepMap allows, and some failures show up only on one operating system or Python version.
- Each fix starts as a test that fails on the old code. Many extraction tests run the real parsing engine on real
  source files; the others are built from its real, captured output.
- The fuzz tests feed 300 generated source files to each of the Rails, C#, Java, Go and TypeScript framework
  readers and check that a reader never crashes, gives the same answer for the same input and emits only the link
  types it declares.
- The ranking functions are locked by a recorded hash, so they can't change unnoticed. A frozen ordering check
  ([`tests/oracles/`](../tests/oracles/)), run by hand on a live database, runs a fixed set of queries and confirms
  that the top 10 results keep their order. The committed oracles pin a commit of the development history, so in
  a fresh clone re-freeze them on a local commit first ([`tests/oracles/README.md`](../tests/oracles/README.md)).
- Opt-in live suites drive real Neo4j instances when `DEEPMAP_LIVE_TEST=1` is set: per-project isolation
  ([`tests/live_per_project_isolation.py`](../tests/live_per_project_isolation.py)) and a macOS end-to-end check
  ([`tests/live_macos_e2e.py`](../tests/live_macos_e2e.py)).

## The live test campaign

In September 2026, AI test agents ran 317 live test cases in 10 areas on Windows 11, on real Neo4j 5.26 instances
(one per project), the real MCP servers over HTTP and the real embedding model:

| Area | Live test cases |
|---|--:|
| Command line and health checks (`status`, `doctor`, `sync`, `search`, `impact`) | 43 |
| Search on DeepMap's own repository (65 queries) | 28 |
| Impact analysis, checked against the callers read from the source | 37 |
| Search and impact on five Python repositories at pinned commits (requests, flask, click, werkzeug, fastapi) | 20 |
| Search and impact on four repositories in other languages (httprouter, serde-json, gin, zod) | 29 |
| Lifecycle and per-project isolation (setup, stop, heal, restart, two starts at once) | 26 |
| Sync and refresh | 27 |
| Framework readers and typed links (Python, Rails, C#, Java, Go, Delphi) | 39 |
| Hooks | 33 |
| Failure modes and robustness | 35 |
| **Total** | **317** |

How the findings were handled:

- Each area had its own agent. It recorded pass, warn or fail for every case, with the evidence, and wrote up each
  bug with a way to reproduce it.
- Two more AI reviewers re-checked each bug from the main runs, each on its own; a bug counted as confirmed only
  when both found it real. Findings from repeat runs of four areas were checked one by one.
- Each fix started from a test that failed on the old code, and reviewers that had not written the fix reviewed it.
  What was not fixed is documented in [`known-limitations.md`](known-limitations.md). A maintainer decided what
  shipped ([`AI_POLICY.md`](../AI_POLICY.md)).

After the fixes, the core of the campaign ran again on the finished code:

- **Windows 11:** setup of DeepMap's own repository (verdict READY; 6,113 symbols, every one embedded; 13 of 13
  indexes online); the 65 search queries over MCP, with no errors; impact spot checks against the callers read from
  the source; a project set up by an earlier version, whose servers the new hooks restarted exactly once onto the
  new code; two projects side by side, each answering only from its own graph (a stopped database was reported as an
  error, never as an empty answer, while the other project kept serving); and the frozen ordering check, with no
  ordering changed.
- **Linux** (a `python:3.11` container on Debian 13, Java 21, not root): the `install.sh` path; setup with a real
  Neo4j 5.26 download (checksum verified), extraction, embeddings, every index and both MCP servers (verdict READY);
  the command line and MCP gave identical search and impact results; every hook ran exactly once, with and without
  `python` on the PATH; after `stop`, the command line reported the database unreachable, and the session-start heal
  revived it with the graph intact; the optional git hook synced a real commit; two projects stayed isolated.

Later live checks:

- A first setup from a Python without DeepMap's packages, on Windows 11 and in a Debian 12 container (its system
  Python 3.11): setup built DeepMap's own environment, then the graph.
- Neo4j 5.26.32 for new databases, and moving an existing database to it, including forced rollbacks: on Windows 11
  and in a `python:3.12` container with Temurin 21.

## What was measured

| Measure | Result | Machine |
|---|---|---|
| Search over MCP, 65 queries on DeepMap's own repository | median 464 ms, 95th percentile 744 ms; 555 ms and 884 ms while a full test run loaded the machine | Windows 11 |
| Hooks (every command in `hooks/hooks.json`, September 2026) | 0.3 to 1.4 seconds per run; up to 4.0 seconds when the session-start note waits for that session's heal (the limit is 10 seconds) | Windows 11 |
| The hook after each deepmap tool call (medians) | no measurable cost on top of a hook's start-up when it has nothing to say; about 0.25 to 0.31 seconds more when it has to check a background refresh | Windows 11, Git Bash and PowerShell 5.1 |
| First setup on a machine: DeepMap's own Python environment (packages, model, self-check) | 10 min 22 s on a busy machine; 3 min 00 s in a Debian 12 container; about 1 GB downloaded | Windows 11, Linux |
| Setup of projects of 732 to 1,276 symbols | 710 to 829 seconds (12 to 14 minutes) on a busy machine | Windows 11 |
| Setup of the largest project tested, fastapi: 6,315 symbols, 92% of them in tests and docs | 68 minutes on a busy machine, with the embedding on one thread (setup now embeds on eight threads) | Windows 11 |

Search quality is measured by the with-vs-without benchmark under [`benchmark/`](../benchmark/). No with-vs-without
benchmark result is published yet; the benchmark will be run on the current graph before any number is published.
The call-linking decision records under [`benchmark/call_linking/`](../benchmark/call_linking/) include the
pre-registered measurements (link precision against jedi and the TypeScript checker, and a 65-query search check on
DeepMap's own code) behind keeping those modes off by default.

## Not covered yet

- **macOS.** Not tested live yet. Apple-silicon Macs are the target: a manual workflow,
  [`.github/workflows/macos.yml`](../.github/workflows/macos.yml), runs the automated suite and the live end-to-end
  check on GitHub's macOS runners. Intel Macs are not supported (PyTorch publishes no current build for them).
- **Other Neo4j versions.** Only Neo4j 5.26 is tested. A database you point DeepMap at yourself with `NEO4J_URI`
  (another version, or Aura) was not tested live.
- **Continuous integration** runs the test suite in one Linux job, plus a gitleaks secret scan on every push and
  pull request and a dependency review on pull requests. Windows, Python 3.10 to 3.12 and the oldest engine release
  are run by hand.
