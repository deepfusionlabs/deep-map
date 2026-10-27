#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# run_breadth.sh — RESUMABLE driver for the MULTI-LANGUAGE breadth benchmark
# (and, via FAMILY= / PREFIX=, the Python depth re-run). A sibling of run_g3_n4.sh: it only
# orchestrates the existing entrypoints (benchmark/clone_graph.py, benchmark/run_benchmark.py);
# build/retrieval.py is never touched, nothing is fabricated or published.
#
# Two modes:
#   MODE=loop  (default) — Opus anchor only, n=1, CAPTURE-ONLY (no paid judge).
#                          The fast improvement-loop pass: read the FREE completeness
#                          signal afterwards with `python benchmark/completeness_only.py <ids>`.
#   MODE=final           — Opus anchor + any $CLAUDE_MODELS, n=$RUNS (default 2), then
#                          the blind Stage-2 --score (parallel) and a --merge.
#
# SPENDS: the Claude arm(s) + (final only) the blind judge run via `claude -p` under your
# own Claude login (usage counts against that account's limits) — no OpenRouter unless you
# pick the independent OpenRouter judge (DEEPMAP_JUDGE_TRANSPORT=openrouter). Launch it on
# purpose.
#
# RESUMABLE, like run_g3_n4.sh: each repo is keyed by a run-id carrying RUN_TAG (default
# v<DeepMap version>): <PREFIX>-<RUN_TAG>-<owner__name>, so a new DeepMap version starts a fresh
# run; each MODE has its own default PREFIX (loop: breadth-loop, final: breadth), so a final launch
# never finds a loop capture at its run-ids. A re-launch reuses only what was captured on THIS
# DeepMap's graph (run_benchmark.resume_state_for — the DeepMap version and the clone's graph
# manifest a capture records) in THIS launch's SHAPE (breadth_shape.json, which the driver writes
# next to each capture before it starts: runs per arm, the extra Claude arms, the family filter):
# a repo measured on it is skipped, one captured but unscored is only scored (final; loop skips
# it), an UNFINISHED capture (or one whose results.json is unreadable) is moved aside
# (<run-id>.unfinished-<utc>, its answer paths re-pointed there so the archive reads its OWN
# answers) and captured again. A capture of ANY other graph — another DeepMap version, a clone
# rebuilt since with another engine or by another DeepMap, or no recorded provenance (a capture
# by an older harness, untagged breadth-*/depthn4-* runs included) — or of another shape (another
# RUNS, CLAUDE_MODELS or FAMILY under the same PREFIX + RUN_TAG, or no recorded shape: a capture
# made outside this driver) stops the driver instead of being reused or overwritten. A capture that
# exits non-zero is never scored or merged; the driver finishes the other repos and exits 1.
#
# Usage:
#   export NEO4J_PASSWORD=deepmap
#   MODE=loop  bash benchmark/run_breadth.sh                      # multi-lang, Opus n=1, free signal
#   MODE=final CLAUDE_MODELS="claude-sonnet-5" JUDGE_MODEL=haiku bash benchmark/run_breadth.sh
#       # its own run-ids (breadth-<RUN_TAG>-*; the loop pass above wrote breadth-loop-<RUN_TAG>-*).
#       # the default blind judge (`sonnet`) may BE the claude-sonnet-5 arm, and --score refuses a
#       # judge that is (or may resolve to) an arm of the run: name one that is none of the arms
#       # (JUDGE_MODEL=<claude model>, on the plan) or the independent OpenRouter judge
#       # (DEEPMAP_JUDGE_TRANSPORT=openrouter DEEPMAP_JUDGE_MODEL=<id>). Checked BEFORE any capture.
#   REPOS="pallets/flask pallets/werkzeug pallets/click fastapi/fastapi" \
#     FAMILY="runtime_edge,multi_hop" RUNS=4 MODE=final PREFIX=depthn4 \
#     bash benchmark/run_breadth.sh                               # the Python DEPTH n=4 re-run
#
# Env (all optional):
#   REPOS="a/b c/d"     repos to run (default: the 4 breadth repos). Names as in repos.yaml.
#   MODE=loop|final     see above (default loop).
#   FAMILY=f1,f2        restrict to these question families (passed to run_benchmark --family).
#   RUNS=<n>            runs/arm, a positive whole number (default: loop=1, final=2).
#   CLAUDE_MODELS="..." extra Claude arms (space-separated model ids; meant for final mode, and loop
#                       mode captures them too).
#   JUDGE_MODEL=<id>    the blind judge's --judge-model (final mode; default run_benchmark's `sonnet`).
#                       Under DEEPMAP_JUDGE_TRANSPORT=openrouter the judge is DEEPMAP_JUDGE_MODEL.
#   PREFIX=<str>        run-id prefix (default: breadth-loop in loop mode, breadth in final mode).
#                       run-id = <PREFIX>-<RUN_TAG>-<owner__name>. A capture under it is reused only in
#                       the same shape (RUNS, CLAUDE_MODELS, FAMILY): another shape needs another PREFIX
#                       or RUN_TAG. MODE, PREFIX, RUN_TAG and RUNS are checked before the log opens.
#   RUN_TAG=<tag>       run-id tag (default v<DeepMap version>); a new tag starts a fresh run.
#   CLONES=<dir>        pinned-clone root (default /tmp/deepmap-bench-clones).
#   TIMEOUT_S=<sec>     per-run claude timeout (default 180; caps a hang).
#   KEEP_RUNNING=1      leave each clone's MCP servers + Neo4j running after its capture.
#   SKIP_PROBE=1        skip the pre-flight --probe (only if it already passed this session).
#   DO_CLONE=1          clone/checkout any missing repo at its pinned SHA.
#   DRIVER_LOG=<path>   driver logfile (default benchmark/results/<PREFIX>-driver.log).
#
# GRAPH PER CLONE (DeepMap's single routing rule): each clone is built as its OWN DeepMap project —
# `python benchmark/clone_graph.py build <clone>` (its own Neo4j instance + graph + MCP servers) — and the
# ON arm is pointed at THAT clone's port pair per command. The old shared-graph flow
# (`deepmap_cli.py sync <clone> --switch-project`) is refused under that rule, and it piped the sync through
# `| tail -3` without `set -e`, so a refused sync went unnoticed and the capture ran on a stale graph.
# Clean up afterwards with `python benchmark/clone_graph.py rm <clone>` per clone.
# ---------------------------------------------------------------------------
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT" || { printf '[breadth ERROR] cannot cd to %s\n' "$REPO_ROOT" >&2; exit 1; }   # no `set -e` here
log() { printf '[breadth %s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }
die() { printf '[breadth ERROR] %s\n' "$*" >&2; exit 1; }

CLONES="${CLONES:-/tmp/deepmap-bench-clones}"
RESULTS="benchmark/results"
MODE="${MODE:-loop}"
TIMEOUT_S="${TIMEOUT_S:-180}"
FAMILY="${FAMILY:-}"
CLAUDE_MODELS="${CLAUDE_MODELS:-}"
JUDGE_MODEL="${JUDGE_MODEL:-}"
DEFAULT_BREADTH="julienschmidt/httprouter serde-rs/json gin-gonic/gin colinhacks/zod"
REPOS="${REPOS:-$DEFAULT_BREADTH}"
# Checked BEFORE the driver log opens — its default path is built from PREFIX — so a refused launch writes no file.
case "$MODE" in loop|final) ;; *) die "MODE must be loop or final (got '$MODE')" ;; esac
# Each MODE has its own default run-id space: a final launch never finds a loop capture (Opus only, n=1) at its
# run-ids. An explicit PREFIX shared by both is caught by the capture-shape check (classify_run).
if [ "$MODE" = "final" ]; then PREFIX="${PREFIX:-breadth}"; RUNS="${RUNS:-2}"
else PREFIX="${PREFIX:-breadth-loop}"; RUNS="${RUNS:-1}"; fi
[[ "$PREFIX" =~ ^[A-Za-z0-9._-]+$ ]] || die "PREFIX '$PREFIX' may hold only letters, digits, '.', '_' and '-'"
[ -z "${RUN_TAG:-}" ] || [[ "$RUN_TAG" =~ ^[A-Za-z0-9._-]+$ ]] \
  || die "RUN_TAG '$RUN_TAG' may hold only letters, digits, '.', '_' and '-'"
# (run_benchmark reads --runs-per-arm 0 as "not given" and runs its default n=4)
[[ "$RUNS" =~ ^[1-9][0-9]*$ ]] || die "RUNS must be a positive whole number (got '$RUNS')"
LOG="${DRIVER_LOG:-$RESULTS/${PREFIX}-driver.log}"

mkdir -p "$RESULTS"
exec > >(tee -a "$LOG") 2>&1

export DEEPMAP_REAL_ENGINE=1
export PYTHONUNBUFFERED=1
export PYTHONUTF8=1
: "${NEO4J_PASSWORD:?set NEO4J_PASSWORD (dev default: deepmap)}"
command -v python >/dev/null 2>&1 || die "python not on PATH"
command -v claude >/dev/null 2>&1 || die "claude (Claude Code CLI) not on PATH — the arm(s) + judge need it"
[ -f benchmark/repos.yaml ] || die "benchmark/repos.yaml missing — run from a DeepMap checkout"

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
# tr -d '\r': Python's print() emits CRLF on Windows; a trailing \r would corrupt every path/compare.
DM_VERSION="$(dm_version | tr -d '\r')" || die "could not load benchmark/run_benchmark.py to read the DeepMap version"
[ -n "${RUN_TAG:-}" ] || [ -n "$DM_VERSION" ] \
  || die "cannot read this checkout's DeepMap version (.claude-plugin/plugin.json) — set RUN_TAG=<tag>"
RUN_TAG="${RUN_TAG:-v$DM_VERSION}"
[[ "$RUN_TAG" =~ ^[A-Za-z0-9._-]+$ ]] || die "RUN_TAG '$RUN_TAG' may hold only letters, digits, '.', '_' and '-'"
COMBINED_ID="${PREFIX}-${RUN_TAG}-combined"

# The capture SHAPE this launch asks for, as JSON — runs per arm, the extra Claude arms, the family filter, normalised
# (sorted, de-duplicated) the way run_benchmark reads them. Written next to each capture BEFORE it starts
# ($RESULTS/<run-id>/breadth_shape.json) and required of any capture a re-launch reuses (classify_run): the graph
# check alone let a final launch score and merge a loop capture's Opus-only n=1 matrix as its own.
launch_shape() {
  python - "$RUNS" "$CLAUDE_MODELS" "$FAMILY" <<'PY'
import json, sys
runs, models, family = sys.argv[1:4]
print(json.dumps({"runs_per_arm": int(runs), "claude_models": sorted(set(models.split())),
                  "family": sorted({f.strip() for f in family.split(",") if f.strip()})}, sort_keys=True))
PY
}
SHAPE_JSON="$(launch_shape | tr -d '\r')" || die "could not compute this launch's capture shape"

sanitize() { printf '%s' "${1//\//__}"; }

# classify_run <run_id> <repo> <clone dir> -> "<state><TAB><reason>", from run_benchmark.resume_state_for — the SAME
# provenance check run_g3_n4.sh uses: the DeepMap version and graph manifest a capture records against this checkout's
# version and the clone's current graph — then, for a capture it would reuse, this launch's capture shape:
#   none     nothing captured yet, an UNFINISHED capture of this graph or an unreadable results.json (a dir holding
#            per-run files is moved aside to <run-id>.unfinished-<utc>) -> build + capture (+ blind score in final)
#   captured captured on THIS graph in THIS shape, a card unscored -> final: blind score only (no re-spend); loop: skip
#   scored   measured on THIS graph in THIS shape     -> skip
#   stale    a capture of ANOTHER graph (another DeepMap version, a clone rebuilt since with another engine or by
#            another DeepMap, or no recorded provenance: a capture by an older harness) -> STOP: never reused,
#            never overwritten
#   shape    a capture of this graph in ANOTHER shape: its breadth_shape.json is not this launch's, or its results.json
#            contradicts it (another runs per arm, an arm of a model this launch does not run), or it records none (made
#            outside this driver) -> STOP: never reused, never overwritten
#   error    the classifier failed                    -> STOP
classify_run() {
  python - "$1" "$2" "$3" "$SHAPE_JSON" <<'PY'
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location("dm_run_benchmark_resume", "benchmark/run_benchmark.py")
B = importlib.util.module_from_spec(spec)
spec.loader.exec_module(B)
rid, want = sys.argv[1], json.loads(sys.argv[4])
state, why = B.resume_state_for(rid, sys.argv[2], sys.argv[3])


def words(shape):
    fams = shape.get("family") or []
    return (f"n={shape.get('runs_per_arm')}, Opus" + "".join(f" + {m}" for m in shape.get("claude_models") or [])
            + (f", families {','.join(fams)}" if fams else ", all families"))


def read(name):
    try:
        return json.loads((B.RESULTS_ROOT / rid / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


if state in ("captured", "scored"):
    got, rec = read("breadth_shape.json"), read("results.json")
    rec = rec if isinstance(rec, dict) else {}
    runs = rec.get("runs_per_arm")
    extra = sorted({str(c.get("arm")).split("@", 1)[1] for c in rec.get("scorecards") or []
                    if isinstance(c, dict) and "@" in str(c.get("arm") or "")} - set(want["claude_models"]))
    if not isinstance(got, dict):
        state, why = "shape", (f"it records no capture shape (breadth_shape.json), so nothing shows it is this launch's "
                               f"{words(want)}: it was not captured by this driver")
    elif any(got.get(k) != want[k] for k in ("runs_per_arm", "claude_models", "family")):
        state, why = "shape", f"it is a capture of {words(got)}; this launch asks for {words(want)}"
    elif (isinstance(runs, int) and runs != want["runs_per_arm"]) or extra:
        state, why = "shape", (f"its results.json holds n={runs}" + "".join(f" + {m}" for m in extra)
                               + f", not the {words(want)} its breadth_shape.json records (this launch's)")
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

# judge_check -> "ok|clash<TAB><the judge --score will call><TAB><the arm it is, or may run the same model as>". The
# blind judge must be none of the run's arm models (run_benchmark --score refuses one that is an arm's model, or that
# can run one through a `claude --model` selector on either side, such as sonnet, best, opusplan, fable or default;
# an alias-named arm in CLAUDE_MODELS counts as its family), so final mode checks it BEFORE spending hours on a
# capture it could not score.
judge_check() {
  # CLAUDE_MODELS is a space-separated id list, word-split on purpose:
  # shellcheck disable=SC2086
  python - "$JUDGE_MODEL" $CLAUDE_MODELS <<'PY'
import importlib.util, sys
spec = importlib.util.spec_from_file_location("dm_run_benchmark_judge", "benchmark/run_benchmark.py")
B = importlib.util.module_from_spec(spec)
spec.loader.exec_module(B)
judge = B.effective_judge_model(sys.argv[1] or B.JUDGE_MODEL_ALIAS)
clash = B.judge_arm_clash(judge, [B.MODEL] + sys.argv[2:])
print(("clash" if clash else "ok") + "\t" + " ".join(str(judge).split()) + "\t" + " ".join(str(clash or "").split()))
PY
}

clone_if_needed() {  # <name>
  local name="$1" san url sha dir
  san="$(sanitize "$name")"; dir="$CLONES/$san"
  [ -d "$dir/.git" ] && return 0
  [ "${DO_CLONE:-0}" = "1" ] || die "clone missing: $dir (clone it at its pinned SHA, or re-run with DO_CLONE=1)"
  read -r url sha < <(python -c "import yaml,sys
n=sys.argv[1]
for r in yaml.safe_load(open('benchmark/repos.yaml'))['repos']:
    if r['name']==n: print(r['url'], r['pinned_sha']); break" "$name" | tr -d '\r')
  [ -n "$url" ] && [ -n "$sha" ] || die "no url/pinned_sha for $name in repos.yaml"
  mkdir -p "$CLONES"; log "clone $name -> $dir @ ${sha:0:12}"
  git clone "$url" "$dir" && git -C "$dir" checkout "$sha"
}

# --- Per-clone graph + servers (each clone is its own DeepMap project) ---
# build_clone <dir> -> sets SP/EP to the clone's OWN MCP (search, embed) pair; the ON arm gets them PER
# COMMAND (an exported pair would leak into the next clone's `provision.py setup` as a port override).
PROBED=0
build_clone() {  # <dir>
  local dir="$1" ports
  python benchmark/clone_graph.py build "$dir" || die "graph/server build FAILED for $dir — not measuring it"
  ports="$(python benchmark/clone_graph.py ports "$dir" | tr -d '\r')" || die "no MCP ports registered for $dir"
  read -r SP EP <<< "$ports"
  [ -n "${SP:-}" ] && [ -n "${EP:-}" ] || die "could not read $dir's MCP ports ('$ports')"
  log "clone graph + servers ready: $dir on MCP :$SP/:$EP"
  if [ "${SKIP_PROBE:-0}" != "1" ] && [ "$PROBED" = "0" ]; then
    log "pre-flight --probe against this clone's servers (ON loads / OFF isolated / capture-path)…"
    DEEPMAP_SEARCH_PORT="$SP" DEEPMAP_EMBED_PORT="$EP" python benchmark/run_benchmark.py --probe --clone-root "$dir" \
      || die "--probe failed — fix MCP wiring before the matrix"
    PROBED=1; log "probe OK"
  fi
}
release_clone() {  # <dir> — stop its servers + Neo4j (RAM); the graph stays for a re-run
  [ "${KEEP_RUNNING:-0}" = "1" ] || python benchmark/clone_graph.py stop "$1" || log "WARN: could not stop $1's servers/Neo4j"
}

# score_repo <run_id> — the blind Stage-2 score (final mode). --clone-root: the cli judge reads each card's OWN
# pinned clone (<clone-root>/<owner__name>; refused unless it is pristine at the pin — the capture leaves it so),
# since correctness / wrong_high are claims checked against the code at the pinned SHA. JUDGE_MODEL, when set, is
# the judge's --judge-model (the one judge_check cleared).
JUDGE_ARGS=(); [ -n "$JUDGE_MODEL" ] && JUDGE_ARGS+=(--judge-model "$JUDGE_MODEL")
score_repo() {
  python benchmark/run_benchmark.py --score "$1" --clone-root "$CLONES" --score-workers 6 ${JUDGE_ARGS[@]+"${JUDGE_ARGS[@]}"}
}

# --- extra Claude arms (final mode) -----------------------------------------
# (${arr[@]+"${arr[@]}"}: an empty array expands to nothing, also under `set -u` on a bash older than 4.4)
MODEL_ARGS=(); for m in $CLAUDE_MODELS; do MODEL_ARGS+=(--claude-model "$m"); done
FAM_ARGS=(); [ -n "$FAMILY" ] && FAM_ARGS+=(--family "$FAMILY")

log "MODE=$MODE  RUNS=$RUNS  PREFIX=$PREFIX  RUN_TAG=$RUN_TAG (DeepMap ${DM_VERSION:-unknown})  FAMILY='${FAMILY:-*}'  arms=Opus${CLAUDE_MODELS:+ + $CLAUDE_MODELS}"
log "repos: $REPOS"

if [ "$MODE" = "final" ]; then
  jline="$(judge_check | tr -d '\r')" || die "could not load benchmark/run_benchmark.py to check the blind judge"
  jstate="${jline%%$'\t'*}"; jrest="${jline#*$'\t'}"; JUDGE="${jrest%%$'\t'*}"; jclash="${jrest#*$'\t'}"
  case "$jstate" in
    ok)    log "blind judge: $JUDGE (none of the arms)" ;;
    clash) die "the blind judge '$JUDGE' is — or, through a claude --model selector on either side, may run the same model as — '$jclash', an arm of this run: run_benchmark --score would refuse to score it after the capture. Set JUDGE_MODEL=<a model that is none of the arms> (e.g. JUDGE_MODEL=haiku) or use the independent judge (DEEPMAP_JUDGE_TRANSPORT=openrouter DEEPMAP_JUDGE_MODEL=<id>). Nothing was captured." ;;
    *)     die "could not check the blind judge: ${jline:-no answer}" ;;
  esac
fi

RUN_IDS=()
INCOMPLETE=()   # repos whose capture exited non-zero this launch: never scored, never merged
for name in $REPOS; do
  san="$(sanitize "$name")"; rid="${PREFIX}-${RUN_TAG}-$san"; dir="$CLONES/$san"
  RUN_IDS+=("$rid")
  line="$(classify_run "$rid" "$name" "$dir" | tr -d '\r')" || die "could not classify $rid"
  state="${line%%$'\t'*}"; why="${line#*$'\t'}"
  case "$state" in
    scored)
      log "SKIP $name — $why ($rid)"; continue ;;
    captured)
      if [ "$MODE" = "loop" ]; then log "SKIP $name — $why; loop mode only captures ($rid)"; continue; fi
      log "RESUME $name — $why: blind score only ($rid)"
      clone_if_needed "$name"   # the cli judge reads this clone at its pin (DO_CLONE=1 re-creates a removed one)
      if score_repo "$rid"; then log "scored $name ($rid) — the [score] line above says whether every card is scored"
      else log "WARN: the blind score of $rid failed (see [score] above) — it stays unscored"; fi
      continue ;;
    none)
      if has_run_files "$rid"; then
        # An unfinished (killed, or unreadable) capture's per-run files: moved aside, never deleted and never mixed into
        # the new capture — completeness_only.py reads every record.json under the run dir, and a run that fails again
        # would otherwise leave its old files there. Its answer paths are re-pointed at the archive (reroot_note).
        aside="$RESULTS/$rid.unfinished-$(date -u +%Y%m%dT%H%M%SZ)"
        mv "$RESULTS/$rid" "$aside" || die "could not move the earlier capture $RESULTS/$rid aside (to $aside)"
        log "RECAPTURE $name — $why; the earlier capture was moved aside to $aside ($rid)"
        reroot_note "$aside" "$rid"
      else
        log "CAPTURE $name — $why ($rid)"
      fi ;;
    stale)
      die "$rid cannot be reused: $why. It is not a measurement of this DeepMap's graph — run under a new tag (RUN_TAG=<tag> bash benchmark/run_breadth.sh) or move $RESULTS/$rid aside to capture $name again." ;;
    shape)
      die "$rid holds a capture of another shape: $why. It is not the matrix this launch asks for, so it is neither reused nor overwritten — keep each shape under its own PREFIX or RUN_TAG (PREFIX=<str> / RUN_TAG=<tag> bash benchmark/run_breadth.sh; each MODE has its own default PREFIX), or move $RESULTS/$rid aside to capture $name again." ;;
    *)
      die "could not classify $rid: ${line:-no answer}" ;;
  esac

  clone_if_needed "$name"
  log "build $name's OWN graph + MCP servers at its pinned SHA (per-clone DeepMap instance)…"
  build_clone "$dir"
  # the shape this capture is asked for, recorded next to it before it starts (a re-launch reuses it only in this shape)
  { mkdir -p "$RESULTS/$rid" && printf '%s\n' "$SHAPE_JSON" > "$RESULTS/$rid/breadth_shape.json"; } \
    || die "could not record the capture shape in $RESULTS/$rid/breadth_shape.json"
  log "capture $name (n=$RUNS, MODE=$MODE) on MCP :$SP/:$EP…"
  DEEPMAP_SEARCH_PORT="$SP" DEEPMAP_EMBED_PORT="$EP" DEEPMAP_BENCH_VERIFY_ON_ARM=1 \
    python benchmark/run_benchmark.py --repo "$name" --clone-root "$CLONES" --run-id "$rid" \
    --runs-per-arm "$RUNS" --timeout-s "$TIMEOUT_S" ${FAM_ARGS[@]+"${FAM_ARGS[@]}"} ${MODEL_ARGS[@]+"${MODEL_ARGS[@]}"}
  cap_rc=$?
  release_clone "$dir"
  if [ "$cap_rc" -ne 0 ]; then
    # A capture that exits non-zero stopped part-way (a fail-closed abort) or never started: its results.json records
    # capture_complete=false (or no card), so it is neither scored nor merged now, and the next launch captures it again.
    log "WARN: the capture of $name exited $cap_rc (see [run] above) — it is NOT scored or merged; re-launch this driver to capture it again ($rid)"
    INCOMPLETE+=("$name")
    continue
  fi
  if [ "$MODE" = "final" ]; then
    log "blind Stage-2 score $name…"
    if score_repo "$rid"; then log "scored $name ($rid) — the [score] line above says whether every card is scored"
    else log "WARN: the blind score of $rid failed (see [score] above) — it stays unscored"; fi
  else
    log "captured $name ($rid) — read FREE completeness with: python benchmark/completeness_only.py $rid"
  fi
done

if [ "${#INCOMPLETE[@]}" -gt 0 ]; then
  not_merged=""; [ "$MODE" = "final" ] && not_merged=", and NOTHING was merged"
  log "INCOMPLETE: the capture of ${INCOMPLETE[*]} did not finish (see the WARN lines above) — it was not scored${not_merged}."
  log "Fix the cause and re-launch this driver: it skips what is done and captures those repos again."
  exit 1
fi
if [ "$MODE" = "final" ]; then
  log "merge → $RESULTS/$COMBINED_ID/results.md"
  python benchmark/run_benchmark.py --merge "$(IFS=,; echo "${RUN_IDS[*]}")" --run-id "$COMBINED_ID" \
    || die "the merge into $COMBINED_ID failed (see [merge] above)"
  MEASURED="$(python -c "import json,sys; print(json.load(open(sys.argv[1], encoding='utf-8')).get('measured') is True)" \
    "$RESULTS/$COMBINED_ID/results.json" | tr -d '\r')" || MEASURED="False"
  if [ "$MEASURED" = "True" ]; then
    log "DONE — measured (every card blind-scored). Combined table: $RESULTS/$COMBINED_ID/results.md"
  else
    log "DONE — NOT measured: a card is unscored (see the [score] lines above); re-run this driver (it only scores what is captured) before citing anything. Combined table: $RESULTS/$COMBINED_ID/results.md"
  fi
else
  log "LOOP DONE. Free completeness across all: python benchmark/completeness_only.py ${RUN_IDS[*]}"
fi
