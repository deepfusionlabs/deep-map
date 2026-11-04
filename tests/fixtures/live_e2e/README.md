# Recorded `claude -p` stream-json samples

Inputs for `tests/live_e2e_gates.py`, which checks the parser in `tests/live_claude.py` against what a real
Claude Code CLI prints. Each file is one unauthenticated `claude -p noop --output-format stream-json --verbose
--include-hook-events` session (Claude Code 2.1.314 on Windows, DeepMap 0.6.3 installed from the public
marketplace). Not logged in, so every session ends with "Not logged in" and costs $0.

The files:

- `claude_p_noauth_onboarding.jsonl`: a project not set up. Both SessionStart hooks run, and the notify hook prints
  the onboarding note.
- `claude_p_mcp_connected.jsonl`: a project whose `.mcp.json` names a live streamable-HTTP server. It reads
  `connected` and its tool is registered.
- `claude_p_mcp_failed.jsonl`: the same `.mcp.json` pointing at a dead port. It reads `failed`, with no tool.
- `claude_p_no_project_mcp.jsonl`: the plugin loaded with `--plugin-dir` (`deep-map@inline`), no `.mcp.json`.

Scrubbed before they were added: local paths are `C:\a\...`, every id is a fixed fake UUID, the timestamp, the
PowerShell path and the messaging socket name are placeholders. Event order, keys and values are otherwise as
recorded.
