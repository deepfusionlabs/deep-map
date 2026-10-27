---
name: Bug report
about: Report a defect in DeepMap (NOT a security vulnerability; see below)
title: "bug: "
labels: bug
---

<!--
  SECURITY: do NOT report a vulnerability here. Use private reporting instead: see SECURITY.md
  (the repository's Security tab -> Report a vulnerability).
-->

## What happened

<!-- A clear description of the bug and what you expected instead. -->

## Steps to reproduce

1.
2.
3.

## Environment

- DeepMap version (`/deep-map:version` output) or commit:
- Claude Code version and host (CLI / VS Code / desktop app):
- OS (Windows / macOS / Linux):
- Python version:
- Neo4j version (if the live data path is involved):
- Extractor (`auto` default / `graphify` / `ast`; `/deep-map:doctor` shows the one the last sync used):

## Logs / output

<!-- Paste the relevant command output, error, or `/deep-map:doctor` output. Redact secrets. -->

## Anything else

<!-- Screenshots, a minimal repro repo, or context. Did `python tests/run_all.py` pass on your machine? -->
