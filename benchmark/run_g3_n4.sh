#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# run_g3_n4.sh — one-command, RESUMABLE driver for the G3 gold-standard run.
#
# Produces the Scenario-A head-to-head number: Opus 4.8, WITH vs WITHOUT DeepMap, across
# the G3 repo set (the five Python repos named in G3_REPOS below), at the methodology
# default n=4 (this driver OMITS --runs-per-arm). repos.yaml also holds the
# multi-language breadth wing (benchmark/run_breadth.sh runs it); it is NOT part of G3.
#
# It only orchestrates the EXISTING entrypoints (benchmark/clone_graph.py,
# benchmark/run_benchmark.py) — no new scoring logic, and build/retrieval.py is
# never touched. The blind Stage-2 judge and the honesty state machine are the
# harness's; this script does not fabricate or publish anything.
#
# WHY a driver: RUN_SCENARIO_A.md is a correct but manual multi-step sequence,
# and a full n=4 run is a long (~15h) job. This wraps it so one launch survives
# restarts:
# each repo is keyed by a run-id carrying RUN_TAG (default v<DeepMap version>, e.g.
# g3n4-v<version>-psf__requests), and a re-launch reuses only what was done on THIS
# DeepMap's graph (run_benchmark.resume_state): a repo measured on it is skipped, one
# captured but unscored is only scored, and one to capture again — an UNFINISHED capture,
# or one whose results.json is unreadable — has its per-run files moved aside first
# (<run-id>.unfinished-<utc>, never deleted; their answer paths re-pointed there, so the
# archive reads its OWN answers), never overwritten in place. A capture of ANY other graph —
# another DeepMap version, a clone rebuilt since with another engine, or no recorded graph
# provenance (a capture by an older harness) — stops the
# driver instead of being reused or silently overwritten. A capture that exits non-zero
# stops the driver too (rc 1, nothing scored or merged), after its clone's servers + Neo4j
# are stopped; re-launch to capture it again.
#
# SPENDS: yes — the Opus arm + the blind judge run via `claude -p` under your own Claude
# login (usage counts against that account's limits), plus a cheap ~2-call --probe. It does
# NOT spend OpenRouter. Launch it on purpose (never auto-run in a session).
#
# GRAPH PER CLONE (DeepMap's single routing rule): every pinned clone is built as its OWN DeepMap project
# — `python benchmark/clone_graph.py build <clone>` = `provision.py setup <clone>`: its own Neo4j instance,
# its own graph (product default sync at the pinned SHA), its own MCP servers — and the ON arm is pointed
# at THAT clone's port pair for its capture (passed per command, never exported). The old flow (one
# shared graph: `deepmap_cli.py sync <clone>` against the dev repo's instance) is refused under that rule for a
# clone with no instance of its own, and its servers would be named after the dev repo, not the clone.
# After each capture — one that fails included — its servers + Neo4j are stopped (RAM; KEEP_RUNNING=1 keeps
# them); its graph stays on disk for a re-run.
# Remove them all afterwards with `python benchmark/clone_graph.py rm <clone>` per clone.
#
# Usage:
#   export NEO4J_PASSWORD=deepmap
#   # one-time prereqs (see RUN_SCENARIO_A.md §0): Java 17/21 + the Python deps; clone the repos
#   bash benchmark/run_g3_n4.sh                 # foreground
#   nohup bash benchmark/run_g3_n4.sh >/dev/null 2>&1 &   # detached (~15h)
#   tail -f benchmark/results/g3n4-driver.log   # watch either way
#
# Resume: just re-run the same command — a repo already measured on this DeepMap's graph
# is skipped, a captured-but-unscored one is only scored, an unfinished one is moved aside and
# captured again; results land in benchmark/results/g3n4-<RUN_TAG>-<owner__name>, merged into
# g3n4-<RUN_TAG>-combined.
#
# Env (all optional):
#   REPOS="a/b c/d"                    repos to run (default: the G3 set, G3_REPOS). A REPOS run is NOT the G3 set.
#   RUN_TAG=<tag>                      run-id tag (default v<DeepMap version>); a new tag starts a fresh run
#   CLONES=/tmp/deepmap-bench-clones   root holding the pinned clones (OUTSIDE this repo)
#   DO_CLONE=1                         clone/checkout any missing repo at its pinned SHA
#   SKIP_PROBE=1                       skip the pre-flight --probe (only if it already passed this session)
#   KEEP_RUNNING=1                     leave each clone's MCP servers + Neo4j running after its capture
#   DRIVER_LOG=<path>                  driver logfile (default benchmark/results/g3n4-driver.log)
#   TIMEOUT_S=<sec>                    per-run claude timeout (default 600; caps a hang — the 1800 default stalled flask ~10h)
# ---------------------------------------------------------------------------
set -euo pipefail

# --- Run from the repo root (this script lives in benchmark/) ----------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

CLONES="${CLONES:-/tmp/deepmap-bench-clones}"
RESULTS="benchmark/results"
LOG="${DRIVER_LOG:-$RESULTS/g3n4-driver.log}"
TIMEOUT_S="${TIMEOUT_S:-600}"   # per-run claude timeout; caps a hang at 10min (the 1800s default stalled flask ~10h)

mkdir -p "$RESULTS"
# Tee everything to the logfile so a detached run is watchable.
exec > >(tee -a "$LOG") 2>&1

log() { printf '[g3n4 %s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }
die() { printf '[g3n4 ERROR] %s\n' "$*" >&2; exit 1; }

# --- Preconditions (fail-closed — never a silent bad run) --------------------
export DEEPMAP_REAL_ENGINE=1
export PYTHONUNBUFFERED=1   # flush subprocess [run] progress live (block-buffering made a slow flask look stalled)
export PYTHONUTF8=1         # repos.yaml carries non-ASCII text; never decode it in a legacy Windows codepage
: "${NEO4J_PASSWORD:?set NEO4J_PASSWORD (dev default: deepmap)}"
command -v python >/dev/null 2>&1 || die "python not on PATH"
command -v claude >/dev/null 2>&1 || die "claude (Claude Code CLI) not on PATH — the arm + judge need it"
[ -f benchmark/repos.yaml ] || die "benchmark/repos.yaml missing — run from a DeepMap checkout"

# The G3 set: the five Python repos of the depth wing, whose questions the signed-off ground truth covers
# (benchmark/CURATION_PROVENANCE.md). repos.yaml has no set/wing field (only `stack`), and a filter on it would
# silently pull any Python repo added later into a signed-off gold-standard scope, so the set is NAMED here and
# each name is checked against repos.yaml (it must be there with a 40-hex pin). REPOS="a/b c/d" overrides it
# (the closing report then says the run is not the G3 set).
G3_REPOS="psf/requests pallets/flask pallets/click pallets/werkzeug fastapi/fastapi"
REPOS="${REPOS:-$G3_REPOS}"
pinned_repo_names() {  # <name>... -> each name, in order; exits non-zero on a name repos.yaml does not pin
  python - "$@" <<'PY'
import sys, yaml
repos = yaml.safe_load(open("benchmark/repos.yaml", encoding="utf-8"))["repos"]
pins = {r["name"]: str(r.get("pinned_sha") or "").strip().lower() for r in repos}
for name in sys.argv[1:]:
    sha = pins.get(name, "")
    if len(sha) != 40 or any(c not in "0123456789abcdef" for c in sha):
        sys.exit(f"{name}: not in benchmark/repos.yaml with a 40-hex pinned_sha")
    print(name)
PY
}
# tr -d '\r': Python's print() emits CRLF on Windows; a trailing \r would corrupt every path/compare.
# REPOS is a space-separated name list, word-split on purpose:
# shellcheck disable=SC2086
names="$(pinned_repo_names $REPOS | tr -d '\r')" || die "REPOS must name repos.yaml repos with a pinned SHA ($REPOS)"
mapfile -t REPO_NAMES <<< "$names"
[ -n "$names" ] && [ "${#REPO_NAMES[@]}" -gt 0 ] || die "no repos to run (REPOS='$REPOS')"

# The run tag: every run-id carries it, so a new DeepMap version starts a FRESH run and never resumes (or merges)
# another version's captures. Default: v<this checkout's DeepMap version> (run_benchmark.deepmap_version).
dm_version() {
  python - <<'PY'
import importlib.util
spec = importlib.util.spec_from_file_location("dm_run_benchmark_version", "benchmark/run_benchmark.py")
B = importlib.util.module_from_spec(spec)
spec.loader.exec_module(B)
print(B.deepmap_version() or "")
PY
}
DM_VERSION="$(dm_version | tr -d '\r')" || die "could not load benchmark/run_benchmark.py to read the DeepMap version"
[ -n "${RUN_TAG:-}" ] || [ -n "$DM_VERSION" ] \
  || die "cannot read this checkout's DeepMap version (.claude-plugin/plugin.json) — set RUN_TAG=<tag>"
RUN_TAG="${RUN_TAG:-v$DM_VERSION}"
[[ "$RUN_TAG" =~ ^[A-Za-z0-9._-]+$ ]] || die "RUN_TAG '$RUN_TAG' may hold only letters, digits, '.', '_' and '-'"
COMBINED_ID="g3n4-${RUN_TAG}-combined"
log "repos: ${REPO_NAMES[*]}"
log "run tag: $RUN_TAG (DeepMap ${DM_VERSION:-unknown})   clone root: $CLONES   log: $LOG"

sanitize() { printf '%s' "${1//\//__}"; }   # psf/requests -> psf__requests

# classify_run <run_id> <repo> <clone dir> -> "<state><TAB><reason>", from run_benchmark.resume_state_for — the
# provenance a capture records (the DeepMap version + the clone's graph manifest) against this checkout's version
# and the clone's current graph:
#   none     nothing captured yet, an UNFINISHED capture of this graph, or an unreadable results.json (a dir holding
#            per-run files is moved aside to <run-id>.unfinished-<utc> first) -> build + capture + blind score
#   captured captured on THIS graph, a card unscored    -> blind score only (no re-spend on the capture)
#   scored   measured on THIS graph                     -> skip
#   stale    a capture of ANOTHER graph (another DeepMap version, a clone rebuilt with another engine since, or
#            no recorded provenance: a capture by an older harness) -> STOP: never reused, never overwritten
#   error    the classifier failed                      -> STOP
classify_run() {
  python - "$1" "$2" "$3" <<'PY'
import importlib.util, sys
spec = importlib.util.spec_from_file_location("dm_run_benchmark_resume", "benchmark/run_benchmark.py")
B = importlib.util.module_from_spec(spec)
spec.loader.exec_module(B)
state, why = B.resume_state_for(sys.argv[1], sys.argv[2], sys.argv[3])
print(state + "\t" + " ".join(str(why).split()))
PY
}
# does the run dir already hold per-run capture files? (a capture that was killed leaves them without a results.json)
has_run_files() { [ -n "$(find "$RESULTS/$1" -name record.json -print -quit 2>/dev/null)" ]; }
# reroot_archive <archive dir> <run-id> -> "<files rewritten><TAB><files unreadable>". A card's answer_text_path names
# its run dir (results/<run-id>/…, relative to benchmark/, with / or \ as the capture's OS wrote it), so after the
# move-aside it would name the NEW capture's answers (or nothing): re-point every one in the archive's results.json and
# per-run record.json files at the archive, so completeness_only.py / --score on it read ITS answers. Only paths whose
# second part IS <run-id> change; each rewrite is atomic; an unreadable file is left as it is and counted.
reroot_archive() {
  python - "$1" "$2" <<'PY'
import json, os, re, sys
from pathlib import Path
arch, rid = Path(sys.argv[1]), sys.argv[2]


def moved(path):
    parts = re.split(r"([\\/])", path) if isinstance(path, str) else []
    if len(parts) > 2 and parts[0] == "results" and parts[2] == rid:
        parts[2] = arch.name
        return "".join(parts)
    return path


done = bad = 0
for f in [arch / "results.json", *sorted(arch.rglob("record.json"))]:
    if not f.is_file():
        continue
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        bad += 1
        continue
    cards = (data.get("scorecards") if f.name == "results.json" else [data]) if isinstance(data, dict) else None
    hit = False
    for c in cards or []:
        if isinstance(c, dict) and moved(c.get("answer_text_path")) != c.get("answer_text_path"):
            c["answer_text_path"], hit = moved(c["answer_text_path"]), True
    if hit:
        tmp = f.with_name(f.name + ".reroot-tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, f)
        done += 1
print(f"{done}\t{bad}")
PY
}
reroot_note() {  # <archive dir> <run-id> — re-point the archive's answer paths; say what could not be
  local rr n_ok n_bad
  rr="$(reroot_archive "$1" "$2" | tr -d '\r')" || rr=""
  IFS=$'\t' read -r n_ok n_bad <<< "$rr"
  if ! [[ "${n_ok:-}" =~ ^[0-9]+$ && "${n_bad:-}" =~ ^[0-9]+$ ]]; then
    log "WARN: could not re-point the answer paths in $1 at it — they still name $2 (${rr:-no answer})"
  elif [ "$n_bad" -gt 0 ]; then
    log "WARN: $n_bad file(s) in $1 are unreadable, so any answer path in them still names $2"
  fi
}

# score_repo <run_id> — the blind Stage-2 score. --clone-root: the cli judge reads each card's OWN pinned clone
# (<clone-root>/<owner__name>; refused unless it is pristine at the pin — the capture leaves it so), since
# correctness / wrong_high are claims checked against the code at the pinned SHA.
score_repo() {
  python benchmark/run_benchmark.py --score "$1" --clone-root "$CLONES"
}

clone_if_needed() {  # <name>
  local name="$1" san url sha dir
  san="$(sanitize "$name")"; dir="$CLONES/$san"
  if [ -d "$dir/.git" ]; then return 0; fi
  if [ "${DO_CLONE:-0}" != "1" ]; then
    die "clone missing: $dir  (run RUN_SCENARIO_A.md §1, or re-run with DO_CLONE=1)"
  fi
  read -r url sha < <(python -c "import yaml,sys
n=sys.argv[1]
for r in yaml.safe_load(open('benchmark/repos.yaml'))['repos']:
    if r['name']==n: print(r['url'], r['pinned_sha']); break" "$name" | tr -d '\r')
  [ -n "$url" ] && [ -n "$sha" ] || die "no url/pinned_sha for $name in repos.yaml"
  mkdir -p "$CLONES"
  log "clone $name -> $dir @ ${sha:0:12}"
  git clone "$url" "$dir"
  git -C "$dir" checkout "$sha"
}

# --- Per-clone graph + servers (each clone is its own DeepMap project) ---
# build_clone <dir> -> sets SP/EP to the clone's OWN MCP (search, embed) pair. The ON arm gets them PER
# COMMAND (DEEPMAP_SEARCH_PORT=… on the run_benchmark line): an exported pair would leak into the next
# clone's `provision.py setup`, which honours DEEPMAP_*_PORT as an override.
PROBED=0
build_clone() {  # <dir>
  local dir="$1" ports
  python benchmark/clone_graph.py build "$dir" || die "graph/server build FAILED for $dir — not measuring it"
  ports="$(python benchmark/clone_graph.py ports "$dir" | tr -d '\r')" || die "no MCP ports registered for $dir"
  read -r SP EP <<< "$ports"
  [ -n "${SP:-}" ] && [ -n "${EP:-}" ] || die "could not read $dir's MCP ports ('$ports')"
  log "clone graph + servers ready: $dir on MCP :$SP/:$EP"
  if [ "${SKIP_PROBE:-0}" != "1" ] && [ "$PROBED" = "0" ]; then
    log "pre-flight --probe against this clone's servers (ON loads / OFF isolated / capture-path) — MUST pass…"
    DEEPMAP_SEARCH_PORT="$SP" DEEPMAP_EMBED_PORT="$EP" python benchmark/run_benchmark.py --probe --clone-root "$dir" \
      || die "--probe failed (exit != 0): fix the MCP wiring before the matrix"
    PROBED=1; log "probe OK"
  fi
}
release_clone() {  # <dir> — stop its servers + Neo4j (RAM); the graph stays for a re-run
  [ "${KEEP_RUNNING:-0}" = "1" ] || python benchmark/clone_graph.py stop "$1" || log "WARN: could not stop $1's servers/Neo4j"
}

# --- Per-repo: build ITS graph, capture n=4, blind-score → measured -----------
RUN_IDS=()
for name in "${REPO_NAMES[@]}"; do
  san="$(sanitize "$name")"; rid="g3n4-${RUN_TAG}-$san"; dir="$CLONES/$san"
  RUN_IDS+=("$rid")
  line="$(classify_run "$rid" "$name" "$dir" | tr -d '\r')" || die "could not classify $rid"
  state="${line%%$'\t'*}"; why="${line#*$'\t'}"
  case "$state" in
    scored)
      log "SKIP $name — $why ($rid)"; continue ;;
    captured)
      log "RESUME $name — $why: blind score only ($rid)"
      clone_if_needed "$name"   # the cli judge reads this clone at its pin (DO_CLONE=1 re-creates a removed one)
      score_repo "$rid"
      log "scored $name"; continue ;;
    none)
      if has_run_files "$rid"; then
        # An unfinished (killed, or unreadable) capture's per-run files: moved aside, never deleted and never mixed
        # into — or overwritten by — the new capture (completeness_only.py reads every record.json under the run dir).
        # Its answer paths are re-pointed at the archive (reroot_note).
        aside="$RESULTS/$rid.unfinished-$(date -u +%Y%m%dT%H%M%SZ)"
        mv "$RESULTS/$rid" "$aside" || die "could not move the earlier capture $RESULTS/$rid aside (to $aside)"
        log "RECAPTURE $name — $why; the earlier capture was moved aside to $aside ($rid)"
        reroot_note "$aside" "$rid"
      else
        log "CAPTURE $name — $why ($rid)"
      fi
      clone_if_needed "$name"
      log "build $name's OWN graph + MCP servers at its pinned SHA (per-clone DeepMap instance)…"
      build_clone "$dir"
      log "capture $name (n=4; --runs-per-arm OMITTED = methodology default) on MCP :$SP/:$EP…"
      # `|| cap_rc=$?`: a failing capture must not end the driver (set -e) before its clone's servers + Neo4j stop.
      cap_rc=0
      DEEPMAP_SEARCH_PORT="$SP" DEEPMAP_EMBED_PORT="$EP" DEEPMAP_BENCH_VERIFY_ON_ARM=1 \
        python benchmark/run_benchmark.py --repo "$name" --clone-root "$CLONES" --run-id "$rid" --timeout-s "$TIMEOUT_S" \
        || cap_rc=$?
      release_clone "$dir"
      # A capture that exits non-zero stopped part-way (a fail-closed abort) or never started: its results.json records
      # capture_complete=false (or no card), so it is neither scored nor merged, and a re-launch captures it again.
      [ "$cap_rc" -eq 0 ] || die "the capture of $name exited $cap_rc (see [run] above) — it is NOT scored or merged, and its servers + Neo4j were stopped (unless KEEP_RUNNING=1). Fix the cause and re-launch this driver to capture $name again (its unfinished capture's files are moved aside first) ($rid)."
      log "blind Stage-2 score $name…"
      score_repo "$rid"
      log "scored $name ($rid) — the [score] line above says whether every card is scored" ;;
    stale)
      die "$rid cannot be reused: $why. It is not a measurement of this DeepMap's graph — run under a new tag (RUN_TAG=<tag> bash benchmark/run_g3_n4.sh) or move $RESULTS/$rid aside to capture $name again." ;;
    *)
      die "could not classify $rid: ${line:-no answer}" ;;
  esac
done

# --- Merge the per-repo runs into one head-to-head table ---------------------
log "merge → $RESULTS/$COMBINED_ID/results.md"
python benchmark/run_benchmark.py --merge "$(IFS=,; echo "${RUN_IDS[*]}")" --run-id "$COMBINED_ID"
MEASURED="$(python -c "import json,sys; print(json.load(open(sys.argv[1], encoding='utf-8')).get('measured') is True)" \
  "$RESULTS/$COMBINED_ID/results.json" | tr -d '\r')" || MEASURED="False"
if [ "$MEASURED" = "True" ]; then
  STATUS="measured (every card blind-scored)"
else
  STATUS="NOT measured: a card is unscored (see the [score] lines above); re-run this driver (it only scores what is captured) before citing anything"
fi
SCOPE="the G3 set"
[ "$REPOS" = "$G3_REPOS" ] || SCOPE="a REPOS override, NOT the signed-off G3 set"

log "DONE. Combined table: $RESULTS/$COMBINED_ID/results.md"
cat <<BANNER
------------------------------------------------------------------------------
 G3 gold-standard run ${RUN_TAG} (DeepMap ${DM_VERSION:-unknown}): n=4, ${#REPO_NAMES[@]} repos (${SCOPE}):
   ${REPO_NAMES[*]}
 Combined result: ${STATUS}.
 Not a published number: a figure reaches the README or the product page only when it is published
   with its raw outputs, and only for questions the signed-off ground truth covers
   (benchmark/CURATION_PROVENANCE.md).
------------------------------------------------------------------------------
BANNER
