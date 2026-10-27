#!/usr/bin/env bash
# DeepMap installer (macOS/Linux): the ML stack, the extraction engine, the provisioner and the optional
# post-commit auto-sync hook. The same steps as install.ps1 (Windows).
#
# Steps: the CPU ML stack + the embedding model pre-download + a self-check that fails loudly on any gap;
# the graphify extraction engine; the provisioner (build/provision.py setup: fetch and start Neo4j, build
# the graph, start the MCP servers) under a provisioning lock that is released on success and failure
# alike; then the post-commit auto-sync, written by `build/provision.py git-hook --install` (never chained
# blindly onto an existing hook).
# Set SKIP_PROVISION=1 to install the ML stack/hook only and skip standing up Neo4j;
# SKIP_GIT_HOOK=1 to write no git hook.
# ranx is intentionally NOT installed: the retriever fuses its rankings itself (build/retrieval.py).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SELFCHECK="$SCRIPT_DIR/scripts/ml_stack_selfcheck.py"
MODEL="nomic-ai/modernbert-embed-base"   # the embedding model (768-dim, general text)
IMPORTS_ONLY="${IMPORTS_ONLY:-0}"
SKIP_MODEL="${SKIP_MODEL:-0}"
SKIP_PROVISION="${SKIP_PROVISION:-0}"
SKIP_GIT_HOOK="${SKIP_GIT_HOOK:-0}"
NEO4J_PASSWORD="${NEO4J_PASSWORD:-deepmap}"; export NEO4J_PASSWORD
REPO_ROOT="${REPO_ROOT:-$(pwd)}"

py() { python3 "$@"; }

resolve_python() {
  if ! command -v python3 >/dev/null 2>&1; then
    echo "Python 3 not found on PATH. Install Python 3.10+ and re-run." >&2; exit 1
  fi
  echo "  python: $(python3 --version 2>&1)"
}

install_ml_stack() {
  echo "[ml-stack] Installing the CPU ML stack ..."
  py -m pip install --upgrade pip
  py -m pip install --index-url https://download.pytorch.org/whl/cpu torch
  py -m pip install sentence-transformers transformers
  # Runtime deps imported at module load by the provisioner + the MCP servers (and the benchmark):
  # without these the very next step (build/provision.py setup) dies on `import neo4j` / `import mcp`.
  # PINNED to the validated ranges (the same ranges as requirements.txt / install.ps1 / CI, locked by
  # tests/compliance_gates.py; the evidence for both ends is in requirements.txt): mcp 2.x removed
  # mcp.server.fastmcp, which both MCP servers import; an older mcp / neo4j breaks the servers / the CLI.
  # anyio: DeepMap imports it directly (build/mcp_bounds.py), so it is installed with requirements.txt's floor (mcp's
  # own), in mcp's command: --upgrade then lifts it only as far as mcp's own dependencies allow.
  # --upgrade: an older copy that already satisfies a spec would otherwise be kept as "already satisfied".
  # Quoted, or the shell would read `<` / `>` as redirections.
  py -m pip install --upgrade "neo4j>=6.0.1,<7" "mcp>=1.24,<2" "anyio>=4.5" pyyaml
  if [ "$SKIP_MODEL" != "1" ]; then
    # Skip the ONNX / OpenVINO exports (~1.9 GB) sentence-transformers never loads: the same skip list as
    # build/deps_bootstrap.py MODEL_IGNORE (tests/installer_gates.py keeps the three equal).
    echo "[ml-stack] Pre-downloading $MODEL (about 0.6 GB) ..."
    py -c "from huggingface_hub import snapshot_download; snapshot_download('$MODEL', ignore_patterns=['onnx/*', 'openvino/*'])"
  fi
}

install_extractor_engine() {
  # The multi-language extraction engine (import graphify, MIT). Installed BARE (NO extras),
  # so no LLM SDK (openai/anthropic/bedrock/…) comes along: those are optional graphify extras.
  # DeepMap only ever runs `graphify update --no-cluster` (NO LLM, NO clustering, NO network), so
  # multi-language extraction is zero-egress by construction. The base wheel pulls ~25 tree-sitter
  # grammars. NEVER install graphifyy[all]. The import name is `graphify` (never rename it).
  # PINNED to exactly the validated releases (build/graphify_extract.py SUPPORTED_GRAPHIFY_SPEC,
  # which warns at runtime outside it); --upgrade lifts an older in-range copy (0.8.39 has no CUDA/Metal
  # parser) to the top of the range.
  echo "[extractor] Installing the graphify multi-language engine (bare, no LLM extras) ..."
  py -m pip install --upgrade "graphifyy>=0.8.39,<0.8.52"
}

test_ml_stack() {
  echo "[ml-stack] Running ML-stack self-check (fails loudly on any gap) ..."
  [ -f "$SELFCHECK" ] || { echo "self-check script missing: $SELFCHECK" >&2; exit 1; }
  if [ "$IMPORTS_ONLY" = "1" ]; then py "$SELFCHECK" --imports-only; else py "$SELFCHECK"; fi
  echo "[ml-stack] ML stack verified."
}

install_git_hooks() {
  # The post-commit auto-sync. ONE implementation writes DeepMap's git hook:
  # `provision.py git-hook --install`; the installer delegates to it instead of
  # writing its own. The installer's own block used to (a) be APPENDED to any existing hook, so a
  # Python / Node hook failed every commit (sh appended to a script git runs with another
  # interpreter) and a hook ending in `exec` / `exit` never reached it; (b) sync the directory the
  # installer ran in, never the project's SYNC ROOT (a sub-project got no hook at all); (c) go into
  # .git/hooks even where git runs core.hooksPath (a dead hook), unscoped for linked worktrees;
  # (d) also chain post-checkout; and (e) overwrite .deepmap/instance.json (the learned-fusion
  # state) with a hook-ownership map. provision's --install writes ONE block right after the
  # shebang, scoped to this work tree, syncing the sync root; replaces an old installer's block in
  # place (and its destructive / dead post-checkout line); and REFUSES (rc 2, printing the line to
  # add by hand) a hooks file outside the repository or a husky-managed / git-tracked / non-sh /
  # binary / disabled hook. The block runs the sync DETACHED (`nohup … &`, `|| true`: a commit never
  # waits on it or fails because of it), never with --stale-cleanup, with DEEPMAP_PYTHON if set,
  # else the python3 that runs provision.py here (it has DeepMap's dependencies). A refusal or
  # a failure here never fails the install: /deep-map:refresh keeps the graph current by hand.
  local provision="$SCRIPT_DIR/build/provision.py"
  if [ "${SKIP_GIT_HOOK:-0}" = "1" ]; then
    echo "[git-hook] SKIP_GIT_HOOK=1: no git hook written (opt in later: python3 \"$provision\" git-hook --install \"$REPO_ROOT\")"
    return 0
  fi
  local repo_real self_real
  if ! repo_real="$(cd "$REPO_ROOT" 2>/dev/null && pwd -P)"; then
    echo "[git-hook] $REPO_ROOT is not a directory: no git hook written" >&2
    return 0
  fi
  self_real="$(cd "$SCRIPT_DIR" && pwd -P)"
  if [ "$repo_real" = "$self_real" ]; then
    # Run from DeepMap's own install dir (the deps-only install): that is not a project to keep in sync.
    echo "[git-hook] $REPO_ROOT is DeepMap's own install directory, not your project: no git hook written."
    echo "     To keep a project's graph current on every commit, from that project run:"
    echo "     python3 \"$provision\" git-hook --install \"<your project>\""
    return 0
  fi
  # provision writes DEEPMAP_PYTHON into the hook as ONE quoted word, so a command-line value
  # (`py -3`, `python3 -X utf8`: not a file) would name a command that does not exist there; hand it
  # the concrete interpreter that command runs instead (empty: provision uses the Python running it).
  local hook_py="${DEEPMAP_PYTHON:-}"
  if [ -n "$hook_py" ] && [ ! -e "$hook_py" ]; then
    case "$hook_py" in
      *[[:space:]]*)
        local resolved=""
        # shellcheck disable=SC2086  # word-split on purpose: here DEEPMAP_PYTHON is a command line
        resolved="$($hook_py -c 'import sys; print(sys.executable)' 2>/dev/null)" || resolved=""
        if [ -n "$resolved" ] && [ -e "$resolved" ]; then
          hook_py="$resolved"
        else
          echo "[git-hook] WARNING: DEEPMAP_PYTHON='$hook_py' did not run; the hook uses the Python running this installer" >&2
          hook_py=""
        fi ;;
    esac
  fi
  local rc=0
  DEEPMAP_PYTHON="$hook_py" py "$provision" git-hook --install "$REPO_ROOT" || rc=$?
  case "$rc" in
    0) ;;
    2) echo "[git-hook] no git hook written (the reason is above); the install continues. Run /deep-map:refresh after changes." ;;
    *) echo "[git-hook] WARNING: the git-hook step failed (exit $rc); the install continues. Run /deep-map:refresh after changes." >&2 ;;
  esac
  return 0
}

spawn_instance() {
  # The provisioning lock says "an installer is provisioning this project RIGHT NOW":
  # held only while the provisioner runs and ALWAYS released afterwards. On SUCCESS too (a lock
  # left behind made every re-run of the installer die "lock already present"), and on FAILURE even
  # under `set -e`, which leaves the shell without running a RETURN trap (the old trap leaked the lock
  # on exactly the failed spawn it was meant to clean). The lock holds the installer's pid; one whose
  # pid is gone (an installer killed mid-provision, or the empty lock an older installer left) is
  # stale and reclaimed. Created atomically (noclobber), so two installers can't both take it.
  # Returns the provisioner's exit code.
  local lock="$1"; shift
  if [ -e "$lock" ]; then
    local holder
    holder="$(tr -dc '0-9' 2>/dev/null < "$lock" || true)"
    # alive: kill -0 (ours), or ps -p (another user's installer: kill -0 says EPERM for it)
    if [ -n "$holder" ] && { kill -0 "$holder" 2>/dev/null || ps -p "$holder" >/dev/null 2>&1; }; then
      echo "lock already present: $lock (installer pid $holder is provisioning this project right now)" >&2
      return 1
    fi
    echo "[lock] removing a stale lock $lock (${holder:+its installer, pid $holder, is gone}${holder:-left by an earlier installer})" >&2
    rm -f "$lock"
  fi
  if ! ( set -C; echo "$$" > "$lock" ) 2>/dev/null; then
    echo "lock already present: $lock (another installer took it just now)" >&2
    return 1
  fi
  local rc=0
  "$@" || rc=$?     # PROVISIONER (real installer): the actual Neo4j/MCP spawn; errexit can't skip the release
  rm -f "$lock"
  if [ "$rc" -ne 0 ]; then echo "[lock] the provisioner exited $rc; released the lock $lock" >&2; fi
  return "$rc"
}

setup_outcome() {
  # What `provision.py setup`'s exit code means for the install: 0 = set up; 4 = set up, but its sync
  # PUSHED NOTHING, so the tools answer from the PREVIOUS graph (e.g. graphify ran out of time): a WARNING, the
  # install carries on (the post-commit hook still re-syncs on each commit); anything else = setup failed, stop.
  case "$1" in
    0) return 0 ;;
    4) echo "[provision] WARNING: setup finished, but the graph was NOT rebuilt (exit 4: its sync pushed nothing;" \
            "see the setup verdict above). The tools answer from the previous graph; fix the cause it names (graphify" \
            "ran out of time: set DEEPMAP_GRAPHIFY_TIMEOUT=<seconds>) and run /deep-map:refresh." >&2
       return 0 ;;
    *) return 1 ;;
  esac
}

main() {
  echo "DeepMap installer (macOS/Linux)"
  resolve_python
  install_ml_stack
  install_extractor_engine
  test_ml_stack
  # The provisioner: Neo4j fetch + sync + MCP-server start, wrapped via spawn_instance so the lock
  # is released whatever happens. Skippable for an ML-stack-only install.
  if [ "$SKIP_PROVISION" != "1" ]; then
    echo "[provision] standing up Neo4j + graph + MCP servers (build/provision.py setup) ..."
    mkdir -p "$REPO_ROOT/.deepmap"
    local rc=0
    spawn_instance "$REPO_ROOT/.deepmap/provision.lock" \
      python3 "$SCRIPT_DIR/build/provision.py" setup "$REPO_ROOT" || rc=$?
    setup_outcome "$rc" || exit "$rc"     # exit 4 (graph NOT rebuilt) warns and carries on; others stop
  fi
  # The git hook AFTER setup: the hook syncs the sync root setup has just built (a failed setup stops above).
  install_git_hooks
  if [ "$SKIP_PROVISION" != "1" ]; then
    echo "Done. Start a new Claude Code session to connect deepmap_search / deepmap_embed (approve the two deepmap servers if Claude Code asks)."
  else
    echo "Done (ML stack + hook only; SKIP_PROVISION=1). When ready, run /deep-map:setup in your project"
    echo "(or: python3 \"$SCRIPT_DIR/build/provision.py\" setup \"<your project>\") to stand up Neo4j + the MCP servers."
  fi
}
main "$@"
