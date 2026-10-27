# Contributing to DeepMap

Thanks for your interest in DeepMap, a Claude Code plugin that builds a per-project Neo4j code
knowledge graph with hybrid search over MCP. This guide covers how to get set up, the quality bar, and how
changes land. By participating, you agree to abide by our [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md).

## Ground rules (the short version)

- **Tests come with the change.** New behaviour ships with a gate; fixes ship with a regression test that
  was RED before the fix and GREEN after.
- **Keep the suite green.** `python tests/run_all.py` must pass (it needs no Neo4j and no network).
- **Honesty is not negotiable.** No fabricated benchmark numbers, no aspirational capability claims (see
  *Honesty rules* below).
- **A human owns git.** Maintainers perform commits/merges/releases. Open a PR; don't expect direct pushes.

## Development setup

DeepMap is Python. Most contributions can be validated with **no Neo4j and no network**:

```bash
python tests/run_all.py        # the full no-Neo4j gate: must be GREEN before you open a PR
```

Working on the live data path (sync/index/embed/search over a real graph) additionally needs a local Neo4j:

```bash
export NEO4J_PASSWORD=deepmap
python build/provision.py setup           # fetch/configure/start a local Neo4j (local dev only)
python build/deepmap_cli.py status        # sanity check
```

For the architecture, read [`build/CLAUDE.md`](build/CLAUDE.md) (the product code),
[`tests/CLAUDE.md`](tests/CLAUDE.md) (the test suite) and [`benchmark/CLAUDE.md`](benchmark/CLAUDE.md) (the
benchmark harness). [`docs/PLAYBOOK.md`](docs/PLAYBOOK.md) collects reusable engineering patterns worth following,
and [`docs/testing.md`](docs/testing.md) says how DeepMap is tested.

## The quality bar

1. **RED before GREEN.** Add the failing test first, then make it pass. This is how every gate in `tests/`
   is built; match that discipline.
2. **Match the surrounding code.** DeepMap's tests are plain `check(label, cond)` suites that print
   `GREEN/RED` and exit non-zero on failure; mirror the nearest existing suite rather than introducing a
   new framework.
3. **Right-size the change.** Small, reviewable PRs land faster. One concern per PR where you can.
4. **Cross-platform.** DeepMap runs on Windows and POSIX; avoid shell-specific assumptions, decode
   subprocess output as UTF-8, and resolve executables via `shutil.which`.

## Load-bearing invariants (please don't break these)

- **The ranking functions in `build/retrieval.py` are locked** by a recorded hash (`tests/fts_r7_gates.py`).
  Equal-weight Weighted-RRF reduces exactly to plain RRF (`RRF_K=60`); do **not** change the default weights,
  `RRF_K` or the fusion math until the benchmark calibrates the arm weights. The no-regression ordering check
  depends on it.
- **Never rename `import graphify`.** The PyPI package is `graphifyy`, but its import name is `graphify`;
  renaming it breaks the build.
- **Dependencies are attributed.** If you add a runtime dependency, add it to `requirements.txt`, both
  installers, `NOTICE`, `THIRD-PARTY-LICENSES.md`, and `sbom.cdx.json`; `tests/compliance_gates.py` enforces
  this, and it must stay MIT-compatible (no un-attributed or surprising-copyleft deps).

## Honesty rules

DeepMap's positioning is built on honesty, and PRs are held to it:

- **No fabricated results.** Benchmark/evaluation numbers are only ever *measured*, never invented. The
  harness is explicit about its state and no number reaches the README/landing page until it is measured and
  signed off. Don't hand-fill a score.
- **Claims match the code.** `tests/copy_honesty_scan.py` scans shipped copy for cut/aspirational phrases;
  keep marketing/docs honest to what the code actually does.

## Commits & pull requests

- **Conventional Commits.** Commit messages follow the [Conventional Commits](https://www.conventionalcommits.org/)
  style (`feat:`, `fix:`, `docs:`, `test:`, `chore:`, …).
- **Open a PR** against the default branch with a clear description of *what* changed and *why*. Link any
  related issue. Confirm `python tests/run_all.py` is GREEN.
- A maintainer reviews and merges. Please be patient and responsive to review feedback.

## Licensing of contributions

DeepMap is **MIT-licensed**. By submitting a contribution, you agree that your contribution is provided under
the MIT License and that you have the right to submit it. Don't contribute code you are not licensed to
share.

## AI-assisted contributions

You may use AI assistance. If you do, ensure you understand and can stand behind every line you submit, that
it passes the suite, and that it introduces nothing you are not licensed to contribute. See
[`AI_POLICY.md`](AI_POLICY.md).

## Reporting bugs & security issues

- **Bugs / feature ideas:** open a GitHub issue.
- **Security vulnerabilities:** do **not** open a public issue; follow [`SECURITY.md`](SECURITY.md)
  (private GitHub advisory).
