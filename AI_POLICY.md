# AI Policy

Deep Fusion Labs builds developer tools **with** AI assistance, and we believe in being transparent about
it. This document states how AI is used in DeepMap, and, just as importantly, where we draw hard lines so
that AI never undermines correctness or honesty.

## AI is used to build DeepMap

DeepMap is developed with substantial help from AI coding assistants (Anthropic's Claude, via Claude Code).
AI assists with implementation, tests, refactoring, documentation, and review. This is disclosed here rather
than hidden.

## Humans own the result

AI assistance does not change accountability:

- **Every change is human-owned and reviewed.** A maintainer decides what ships; AI is a tool, not an author
  of record. Version-control writes (commits, merges, releases) are performed by a human.
- **Correctness is verified, not assumed.** DeepMap is gated by a RED-before-GREEN test suite
  (`python tests/run_all.py`) plus targeted and adversarial review. A change is not "done" because an AI
  produced it; it is done when it is tested, reviewed, and green.
- **We fix what we ship.** Bugs are ours to own regardless of how the code was written.

## Honesty guarantees (the line AI does not cross)

DeepMap's positioning is built on honesty, and that constraint governs any AI-assisted work:

- **No fabricated results.** Benchmark and evaluation numbers are only ever *measured*, never invented. The
  harness is explicit about its state (`designed` → `captured, unscored` → `measured`), scoring is blind and
  separated from authoring, and no number reaches the README or landing page until it is measured and
  independently signed off. AI is never used to "fill in" a plausible-looking result.
- **No fabricated capabilities.** Marketing copy is scanned for cut/aspirational phrases
  (`tests/copy_honesty_scan.py`); claims must match what the code actually does.
- **Attribution is real.** Third-party dependencies and licenses are tracked and gated
  (`NOTICE` / `THIRD-PARTY-LICENSES.md` / `sbom.cdx.json`, locked by `tests/compliance_gates.py`).

## Data & privacy

- **By default the shipped product sends nothing to an external LLM.** DeepMap indexes your code into a
  **local** Neo4j graph and answers over local, streamable-HTTP MCP servers. It does not transmit your
  source to a third-party model during normal use.
- **One optional, off-by-default feature, summary enrichment, is the exception.** When you enable it,
  it uses your **existing Claude session** (`claude -p`) to write intent summaries for undocumented code;
  it introduces no new service, no API key, and no telemetry, and sends undocumented source spans nowhere
  you aren't already sending code via Claude Code.
- The AI assistance described above operates during **development of DeepMap itself**, on this repository,
  not on your codebase when you use the tool.
- The `benchmark/` harness is a separate, opt-in evaluation tool. It can call external model APIs, but **only**
  when you explicitly provide a key and confirm the projected spend; it is never invoked by the product.

## Contributions

Contributors may use AI assistance. If you do, please still ensure you understand and can stand behind every
line you submit, that it passes the test suite, and that it does not introduce content you are not licensed
to contribute. See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the workflow.

## Questions

Open an issue if you have questions about how AI is used in DeepMap. Security concerns go through
[`SECURITY.md`](SECURITY.md).
