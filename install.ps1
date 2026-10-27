<#
.SYNOPSIS
  DeepMap installer (Windows): the ML stack, the extraction engine, the provisioner and the
  optional post-commit auto-sync hook.

.DESCRIPTION
  The Windows installer. It runs the same steps as install.sh (macOS/Linux):
    * ML stack: torch (CPU build), sentence-transformers and transformers, a pre-download of
      the embedding model nomic-ai/modernbert-embed-base, and a post-install self-check that
      imports each and FAILS LOUDLY (Install-MLStack + scripts/ml_stack_selfcheck.py).
    * Extraction engine: graphify, installed bare (no LLM extras) at the validated versions
      (Install-ExtractorEngine).
    * Provisioner: build/provision.py setup - fetch and start Neo4j, build the graph, and start
      the deepmap_search / deepmap_embed MCP servers (HTTP). It runs under a provisioning lock
      that is held only while the provisioner runs and released on success and failure alike;
      a stale lock (its installer is gone) is reclaimed (Invoke-InstanceSpawn). Pass
      -SkipProvision for an ML-stack-only install.
    * Git hook: the post-commit auto-sync, run AFTER setup and written by
      `build/provision.py git-hook --install` (a scoped block after the shebang that syncs the
      project's sync root; it refuses a non-sh / tracked / shared / husky hook), never chained
      blindly (Install-DeepMapGitHooks). Pass -SkipGitHook to write no git hook.

  ranx is intentionally NOT installed: the retriever fuses its rankings itself
  (build/retrieval.py).

.PARAMETER ImportsOnly
  Run the self-check in imports-only mode (skip the model download, about 0.6 GB).
.PARAMETER SkipModelDownload
  Install the libraries but do not pre-download the model.
.PARAMETER SkipProvision
  Install the ML stack only; do not stand up Neo4j / the graph / the MCP servers (run
  /deep-map:setup in your project later).
.PARAMETER SkipGitHook
  Write no git post-commit hook (opt in later: provision.py git-hook --install <project>).
.PARAMETER RepoRoot
  The project to set up and keep in sync. Defaults to the current directory.
#>
[CmdletBinding()]
param(
  [switch]$ImportsOnly,
  [switch]$SkipModelDownload,
  [switch]$SkipProvision,
  [switch]$SkipGitHook,
  [string]$RepoRoot = (Get-Location).Path
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$SelfCheck = Join-Path $ScriptDir "scripts/ml_stack_selfcheck.py"
$Model     = "nomic-ai/modernbert-embed-base"   # the embedding model (768-dim, general text)
# The interpreter lever: the command DeepMap prints for the user to run. DEEPMAP_PYTHON override,
# else `python` (Windows has NO python3.exe, so we never rewrite this to python3). The git hook's own
# interpreter is chosen by provision.py git-hook: DEEPMAP_PYTHON if set, else the python running it.
$DeepMapPy = if ($env:DEEPMAP_PYTHON) { $env:DEEPMAP_PYTHON } else { "python" }

function Resolve-Python {
  foreach ($cand in @("py -3", "python", "python3")) {
    $exe, $pre = $cand.Split(" ", 2)
    if (Get-Command $exe -ErrorAction SilentlyContinue) {
      try {
        $v = & $exe $pre --version 2>&1
        if ($LASTEXITCODE -eq 0) { Write-Host "  python: $v ($cand)"; return $cand }
      } catch { }
    }
  }
  throw "Python 3 not found on PATH. Install Python 3.10+ and re-run."
}

function Invoke-Py([string]$PyCmd, [string[]]$PyArgs) {
  $exe, $pre = $PyCmd.Split(" ", 2)
  if ($pre) { & $exe $pre @PyArgs } else { & $exe @PyArgs }
  if ($LASTEXITCODE -ne 0) { throw "command failed ($PyCmd $($PyArgs -join ' '))" }
}

function Invoke-Setup([string]$PyCmd, [string[]]$PyArgs) {
  # `provision.py setup`, where exit 4 means: set up, but its sync PUSHED NOTHING, so the tools answer
  # from the PREVIOUS graph (e.g. graphify ran out of time) - a WARNING, the install carries on (the post-commit hook
  # still re-syncs on each commit). Any other non-zero exit fails the install, as before.
  $exe, $pre = $PyCmd.Split(" ", 2)
  if ($pre) { & $exe $pre @PyArgs } else { & $exe @PyArgs }
  if ($LASTEXITCODE -eq 4) {
    Write-Warning ("[provision] setup finished, but the graph was NOT rebuilt (exit 4: its sync pushed nothing - see " +
                   "the setup verdict above). The tools answer from the previous graph; fix the cause it names (graphify " +
                   "ran out of time: set DEEPMAP_GRAPHIFY_TIMEOUT=<seconds>) and run /deep-map:refresh.")
    return
  }
  if ($LASTEXITCODE -ne 0) { throw "command failed ($PyCmd $($PyArgs -join ' '))" }
}

function Install-PipSpecs([string]$PyCmd, [string[]]$Specs) {
  # pip-install requirement specs that carry VERSION RANGES. They go through a temporary requirements
  # file, never the command line: `<` and `>` in an argument are redirection operators for cmd.exe, which
  # runs a .bat/.cmd Python shim (pyenv-win) - `mcp>=1.2,<2` would be split into a redirect and pip would
  # install an UNPINNED mcp (reproduced with both Windows PowerShell 5.1 and PowerShell 7). --upgrade:
  # an older copy that already satisfies a spec would otherwise be kept as "already satisfied" (an old
  # mcp / neo4j breaks the servers / the CLI; graphifyy 0.8.39 has no CUDA/Metal parser).
  $req = Join-Path ([IO.Path]::GetTempPath()) ("deepmap-pins-" + [guid]::NewGuid().ToString("N") + ".txt")
  try {
    [IO.File]::WriteAllLines($req, $Specs)   # UTF-8 without a BOM on both PowerShell editions
    Invoke-Py $PyCmd @("-m", "pip", "install", "--upgrade", "-r", $req)
  } finally {
    Remove-Item -LiteralPath $req -ErrorAction SilentlyContinue
  }
}

function Install-MLStack([string]$PyCmd) {
  Write-Host "[ml-stack] Installing the CPU ML stack ..."
  Invoke-Py $PyCmd @("-m", "pip", "install", "--upgrade", "pip")
  # torch CPU build from the official CPU index, then the HF stack.
  Invoke-Py $PyCmd @("-m", "pip", "install", "--index-url",
                     "https://download.pytorch.org/whl/cpu", "torch")
  Invoke-Py $PyCmd @("-m", "pip", "install", "sentence-transformers", "transformers")
  # Runtime deps imported at module load by the provisioner + the MCP servers (and the benchmark):
  # without these the next step (build/provision.py setup) dies on `import neo4j` / `import mcp`.
  # PINNED to the validated ranges (the same ranges as requirements.txt / install.sh / CI, locked by
  # tests/compliance_gates.py; the evidence for both ends is in requirements.txt): mcp 2.x removed
  # mcp.server.fastmcp, which both MCP servers import; an older mcp / neo4j breaks the servers / the CLI.
  # anyio: DeepMap imports it directly (build/mcp_bounds.py), so it is installed with requirements.txt's floor (mcp's
  # own), in mcp's spec list: --upgrade then lifts it only as far as mcp's own dependencies allow.
  Install-PipSpecs $PyCmd @("neo4j>=6.0.1,<7", "mcp>=1.24,<2", "anyio>=4.5", "pyyaml")

  if (-not $SkipModelDownload) {
    Write-Host "[ml-stack] Pre-downloading $Model (about 0.6 GB) ..."
    # snapshot the weights into the HF cache so first query is not a surprise download. Skip the ONNX /
    # OpenVINO exports (~1.9 GB) sentence-transformers never loads: the same skip list as
    # build/deps_bootstrap.py MODEL_IGNORE (tests/installer_gates.py keeps the three equal).
    $dl = "from huggingface_hub import snapshot_download; snapshot_download('$Model', ignore_patterns=['onnx/*', 'openvino/*'])"
    Invoke-Py $PyCmd @("-c", $dl)
  }
}

function Install-ExtractorEngine([string]$PyCmd) {
  # The multi-language extraction engine (import graphify, MIT). Installed BARE - NO extras -
  # so no LLM SDK (openai/anthropic/bedrock/...) comes along: those are optional graphify extras.
  # DeepMap only ever runs `graphify update --no-cluster` (NO LLM, NO clustering, NO network), so
  # multi-language extraction is zero-egress by construction. The base wheel pulls ~25 tree-sitter
  # grammars. NEVER install graphifyy[all]. The import name is `graphify` (never rename it).
  # PINNED to exactly the validated releases (build/graphify_extract.py SUPPORTED_GRAPHIFY_SPEC,
  # which warns at runtime outside it); Install-PipSpecs' --upgrade lifts an older in-range copy (0.8.39
  # has no CUDA/Metal parser) to the top of the range.
  Write-Host "[extractor] Installing the graphify multi-language engine (bare - no LLM extras) ..."
  Install-PipSpecs $PyCmd @("graphifyy>=0.8.39,<0.8.52")
}

function Test-MLStack([string]$PyCmd) {
  Write-Host "[ml-stack] Running ML-stack self-check (fails loudly on any gap) ..."
  if (-not (Test-Path $SelfCheck)) { throw "self-check script missing: $SelfCheck" }
  $selfArgs = @($SelfCheck)
  if ($ImportsOnly) { $selfArgs += "--imports-only" }
  Invoke-Py $PyCmd $selfArgs   # non-zero exit -> throw -> install fails loudly
  Write-Host "[ml-stack] ML stack verified."
}

function Test-SameDirectory([string]$A, [string]$B) {
  # Do two paths name the same directory? (full paths; case-insensitive where the file system is)
  try {
    $fa = [IO.Path]::GetFullPath($A).TrimEnd([char[]]@([char]92, [char]47))
    $fb = [IO.Path]::GetFullPath($B).TrimEnd([char[]]@([char]92, [char]47))
    $cmp = if ([IO.Path]::DirectorySeparatorChar -eq [char]92) { [StringComparison]::OrdinalIgnoreCase } `
           else { [StringComparison]::Ordinal }
    return [string]::Equals($fa, $fb, $cmp)
  } catch {
    return $false
  }
}

function Install-DeepMapGitHooks([string]$Repo, [string]$PyCmd) {
  # The post-commit auto-sync. ONE implementation writes DeepMap's git hook:
  # `provision.py git-hook --install`; the installer delegates to it instead of
  # writing its own. The installer's own block used to (a) be APPENDED to any existing hook, so a
  # Python / Node hook failed every commit (sh appended to a script git runs with another
  # interpreter) and a hook ending in `exec` / `exit` never reached it; (b) sync the directory the
  # installer ran in, never the project's SYNC ROOT (a sub-project got no hook at all), with a
  # backslash path git's sh mangles; (c) go into .git/hooks even where git runs core.hooksPath (a dead
  # hook), unscoped for linked worktrees; (d) also chain post-checkout; and (e) overwrite
  # .deepmap/instance.json (the learned-fusion state) with a hook-ownership map. provision's
  # --install writes ONE block right after the shebang, scoped to this work tree, syncing the sync
  # root with forward-slash paths; replaces an old installer's block in place (and its destructive /
  # dead post-checkout line); and REFUSES (rc 2, printing the line to add by hand) a hooks file
  # outside the repository or a husky-managed / git-tracked / non-sh / binary / disabled hook. The
  # block runs the sync DETACHED (nohup + &, `|| true`: a commit never waits on it or fails because of
  # it), never with --stale-cleanup, with DEEPMAP_PYTHON if set, else the python that runs
  # provision.py here (it has DeepMap's dependencies). A refusal or a failure here never fails the
  # install: /deep-map:refresh keeps the graph current by hand.
  $provision = Join-Path $ScriptDir "build/provision.py"
  if ($SkipGitHook) {
    Write-Host "[git-hook] -SkipGitHook: no git hook written (opt in later: $DeepMapPy `"$provision`" git-hook --install `"$Repo`")"
    return
  }
  if (-not (Test-Path -LiteralPath $Repo -PathType Container)) {
    Write-Warning "[git-hook] $Repo is not a directory: no git hook written"
    return
  }
  if (Test-SameDirectory $Repo $ScriptDir) {
    # Run from DeepMap's own install dir (the deps-only install): that is not a project to keep in sync.
    Write-Host "[git-hook] $Repo is DeepMap's own install directory, not your project: no git hook written."
    Write-Host "     To keep a project's graph current on every commit, from that project run:"
    Write-Host "     $DeepMapPy `"$provision`" git-hook --install `"<your project>`""
    return
  }
  # A native command's stderr (provision prints a refusal there) must never become a terminating error.
  $ErrorActionPreference = "Continue"
  # provision writes DEEPMAP_PYTHON into the hook as ONE quoted word, so a command-line value
  # (`py -3` - not a file) would name a command that does not exist there; hand it the concrete
  # interpreter that command runs instead (unset: provision uses the Python running it).
  $savedPy = $env:DEEPMAP_PYTHON
  $hookPy = $savedPy
  if ($hookPy -and -not (Test-Path -LiteralPath $hookPy) -and $hookPy -match '\s') {
    $words = @($hookPy -split '\s+' | Where-Object { $_ })
    $resolved = $null
    try {
      $cmdArgs = @($words | Select-Object -Skip 1) + @("-c", "import sys; print(sys.executable)")
      $resolved = (& $words[0] @cmdArgs 2>$null | Select-Object -First 1)
    } catch { $resolved = $null }
    if ($resolved -and (Test-Path -LiteralPath ([string]$resolved).Trim())) {
      $hookPy = ([string]$resolved).Trim()
    } else {
      Write-Warning "[git-hook] DEEPMAP_PYTHON='$hookPy' did not run; the hook uses the Python running this installer"
      $hookPy = $null
    }
  }
  $exe, $pre = $PyCmd.Split(" ", 2)
  $hookArgs = @($provision, "git-hook", "--install", $Repo)
  $rc = 1
  try {
    $env:DEEPMAP_PYTHON = $hookPy
    if ($pre) { & $exe $pre @hookArgs } else { & $exe @hookArgs }
    $rc = $LASTEXITCODE
  } catch {
    Write-Warning "[git-hook] could not run the git-hook step: $_"
  } finally {
    $env:DEEPMAP_PYTHON = $savedPy
  }
  if ($rc -eq 2) {
    Write-Host "[git-hook] no git hook written (the reason is above); the install continues. Run /deep-map:refresh after changes."
  } elseif ($rc -ne 0) {
    Write-Warning "[git-hook] the git-hook step failed (exit $rc); the install continues. Run /deep-map:refresh after changes."
  }
}

function Invoke-InstanceSpawn([scriptblock]$Spawn, [string]$LockPath) {
  # The provisioning lock says "an installer is provisioning this project RIGHT NOW":
  # held only while the provisioner runs and ALWAYS released afterwards - on success too (a lock left
  # behind made every re-run of the installer die "lock already present") and when the spawn throws.
  # The lock holds the installer's process id; one whose process is gone (an installer killed
  # mid-provision, or the empty lock an older installer left) is stale and reclaimed. Created with
  # CreateNew (atomic), so two installers can't both take it. A failed spawn still fails the install.
  $LockPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($LockPath)
  if (Test-Path -LiteralPath $LockPath) {
    $text = Get-Content -LiteralPath $LockPath -Raw -ErrorAction SilentlyContinue
    $holder = 0
    $alive = $false
    if ($text -and [int]::TryParse(([string]$text).Trim(), [ref]$holder) -and $holder -gt 0) {
      $alive = [bool](Get-Process -Id $holder -ErrorAction SilentlyContinue)
    }
    if ($alive) {
      throw "lock already present: $LockPath (installer process $holder is provisioning this project right now)"
    }
    Write-Warning "[lock] removing a stale lock $LockPath (the installer that took it is gone)"
    Remove-Item -LiteralPath $LockPath -Force
  }
  try {
    $fs = [IO.File]::Open($LockPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::Read)
  } catch {
    throw "lock already present: $LockPath (another installer took it just now)"
  }
  try {
    $bytes = [Text.Encoding]::ASCII.GetBytes([string]$PID)
    $fs.Write($bytes, 0, $bytes.Length)
  } finally {
    $fs.Dispose()
  }
  $ok = $false
  try {
    & $Spawn          # PROVISIONER (real installer): the actual Neo4j/MCP spawn
    $ok = $true
  } finally {
    Remove-Item -LiteralPath $LockPath -Force -ErrorAction SilentlyContinue
    if (-not $ok) { Write-Warning "[lock] spawn did not complete; released the lock $LockPath" }
  }
}

# ----------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------
Write-Host "DeepMap installer (Windows)"
# A full path (the provisioner, the lock and the hook all use it; a relative -RepoRoot resolves here).
$RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).ProviderPath
$py = Resolve-Python
Install-MLStack $py
Install-ExtractorEngine $py
Test-MLStack $py

# The provisioner: stand up Neo4j + the graph + the MCP servers, wrapped via Invoke-InstanceSpawn
# so the lock is released whatever happens. Skippable for an ML-stack-only install.
if (-not $SkipProvision) {
  if (-not $env:NEO4J_PASSWORD) { $env:NEO4J_PASSWORD = "deepmap" }
  $deepmapDir = Join-Path $RepoRoot ".deepmap"
  New-Item -ItemType Directory -Force -Path $deepmapDir | Out-Null
  $lock = Join-Path $deepmapDir "provision.lock"
  Write-Host "[provision] standing up Neo4j + graph + MCP servers (build/provision.py setup) ..."
  Invoke-InstanceSpawn { Invoke-Setup $py @("$ScriptDir/build/provision.py", "setup", $RepoRoot) } $lock
}
# The git hook AFTER setup: the hook syncs the sync root setup has just built (a failed setup stops above).
Install-DeepMapGitHooks $RepoRoot $py
if (-not $SkipProvision) {
  Write-Host "Done. Start a new Claude Code session to connect deepmap_search / deepmap_embed (approve the two deepmap servers if Claude Code asks)."
} else {
  Write-Host "Done (ML stack + hook only; -SkipProvision). When ready, run /deep-map:setup in your project"
  Write-Host "(or: $DeepMapPy `"$ScriptDir/build/provision.py`" setup `"<your project>`") to stand up Neo4j + the MCP servers."
}
