<!-- Thanks for contributing to DeepMap! Please read CONTRIBUTING.md first. -->

## What & why

<!-- What does this change do, and why? Link any related issue (e.g. "Closes #123"). -->

## Type of change

- [ ] Bug fix
- [ ] New feature
- [ ] Docs
- [ ] Test / tooling
- [ ] Refactor / chore

## Checklist

- [ ] `python tests/run_all.py` is **GREEN** (no-Neo4j gate)
- [ ] New behaviour has a gate; a fix has a regression test that was RED before and GREEN after
- [ ] I did **not** change the ranking functions in `build/retrieval.py` (hash-locked by `tests/fts_r7_gates.py`),
      or I explain why below
- [ ] Any new dependency is attributed (`requirements.txt`, both installers, `NOTICE`,
      `THIRD-PARTY-LICENSES.md`, `sbom.cdx.json`) and MIT-compatible
- [ ] No fabricated benchmark numbers or aspirational claims (honesty rules)
- [ ] Commit messages follow Conventional Commits

## Notes for the reviewer

<!-- Anything that helps review: trade-offs, follow-ups, screenshots, or a "why not X". -->
