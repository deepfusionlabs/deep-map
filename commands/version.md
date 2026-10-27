---
description: Which DeepMap version is actually running right now? Prints the LOADED version, checks it against the installed version (and the newest in the marketplace catalog), and tells you whether to reload or update. No analysis.
---

Report the running DeepMap version: fast, no analysis. Do exactly this, then stop:

1. State the **loaded** version verbatim. This line ships inside the plugin markdown that is actually loaded in your session, so it is the source of truth for "what is running right now":

   **DeepMap v0.5.0** <!-- deepmap:loaded-version -->

2. Read the **installed** version, the one a new session loads (best-effort; skip what isn't there):
   - the `deep-map@<marketplace>` entry of `installed_plugins.json` under `${CLAUDE_CONFIG_DIR:-~/.claude}/plugins/` → its `version`. Each key maps to an array of installs, one per scope: take a `local`, then a `project` entry whose `projectPath` is this project (or its git root), else the `user` entry;
   - no such entry, and this session loads DeepMap from a local clone (`claude --plugin-dir`) → that clone's `.claude-plugin/plugin.json`.

   Do **not** use the marketplace catalog under `plugins/marketplaces/`, other folders in the plugin cache, or a clone of the DeepMap repository this session does not load from: a new session loads none of them. Separately, note the **newest available** version: the marketplace catalog's `.claude-plugin/plugin.json` under `${CLAUDE_CONFIG_DIR:-~/.claude}/plugins/marketplaces/<marketplace>/` (as of its last refresh; do not refresh it here).

3. Compare and advise:
   - **loaded == installed** → "You're running the installed version." Then, if the catalog shows a newer one: "vX.Y.Z is available: run `/deep-map:plugin-update`." Otherwise: "You're all set (as of the marketplace catalog's last refresh)."
   - **loaded is behind installed** → "The running plugin is STALE: a newer version is installed but not live. Claude Code loads plugin markdown **ONCE per session** and does not re-read it mid-session, so one activation step remains, and it depends on your host. **Detect your host first** (from your system context), then do only the matching one:
     - **Plain terminal / CLI** → start a **new session** (the sure path), or run `/reload-plugins` (older releases did not rebuild the slash-command index on a reload, so this canary might not flip).
     - **VS Code / JetBrains extension** → open a **new chat**: a new session loads the installed version, while the current chat keeps the version it started with until it ends. A full IDE restart also works, but it ends every running Claude session in every window.
     - **Desktop app / web** → start a new session, or restart the app.

     Then run `/deep-map:version` again to confirm the **LOADED** number flipped (it reports what's actually running, so it's the honest check that activation worked)."
   - **only the catalog or a working copy is newer** (the installed version is not) → nothing to reload: a new session would load the same installed version. Run `/deep-map:plugin-update` to install the catalog's newest.
   - **installed not found** → just report the loaded version (and the catalog's newest, if you found it).

$ARGUMENTS
