#!/usr/bin/env bash
# The G layer of the call-linking measurement (design §8.4 G + E), offline and deterministic: for each corpus x
# engine x variant, the :CALLS dump TWICE (byte-identical or VOID), the reference truth once per corpus, and the
# scores, deltas, drift, engine parity and impact checks. No Neo4j, no network except cloning the two TS/JS
# corpora from GitHub when they are not in CL_CLONES. Everything is written under CL_WORK (a scratch dir).
#
#   CL_WORK=<scratch> CL_PY51=<graphifyy==0.8.51 venv python> CL_JEDI_PY=<jedi 0.20.1 venv python> \
#   CL_TSENV=<dir with node_modules/typescript@5.6.3> bash benchmark/call_linking/run_matrix.sh
#
# Optional: CL_CORPORA="psf__requests pallets__flask ..." (default: every G corpus), CL_VARIANTS="V0 VT ..."
#   (default: every fixed §8.3 variant; V0 always runs first), CL_ENGINES="g39 g51", CL_PY39 (default python),
#   CL_BUILD (the build/ to run; default a SNAPSHOT of this repo's build/*.py taken into CL_WORK/build_snapshot
#   when that dir is absent — the working tree may change mid-matrix, and every dump must measure one code;
#   each dump stamps its code hash), CL_CLONES (default C:/tmp/deepmap-bench-clones — read-only:
#   copied with git clone --no-hardlinks), CL_DMHOME (the scratch DEEPMAP_HOME; default CL_WORK/dmhome — keep it
#   short: the engine's cache paths run 100+ characters past it, and a path over Windows MAX_PATH makes `auto`
#   fall back to ast, which every dump here refuses as VOID via --expect-engine graphify), CL_DRY=1 (print the
#   commands, run nothing).
# The `self` corpus is this repository's working tree (the RC: there is no commit): a git clone --no-hardlinks of
# the repo with the working tree copied over it and `git add -A` run INSIDE THAT THROWAWAY CLONE ONLY (the
# engine's skipped-dir rescue reads `git ls-files --cached`, so new files must be in the clone's index).
# The `stage0_build` corpus is the REGISTERED STAGE-0 corpus (tests/oracles/registry.json, self-graphify-build),
# materialized by `offline_calls.py stage0-corpus` with tests/stage0_portable.py's own materialize_corpus, exactly
# as tests/stage0_refreeze.py builds it: `git archive <sha>:build` — build/'s CONTENTS at the root, a non-git tree
# (a `build/` directory would be skipped by graphify as a noise dir, and a non-git tree has no rescue).
# Dump exit codes: 0 data; 3 VOID (VOID.txt: an engine fallback, or an inert variant — offline_calls.py's
# engine_void / variant_void); 4 INVARIANT (INVARIANT.txt: cross-file EXTRACTED edges — still data, and the
# variant FAILS). Anything else is VOID.
# Never writes into this repository or the read-only clones; deletes nothing outside CL_WORK.
set -u
# pwd -W (Git Bash) gives C:/... so `--opt=<path>` arguments reach Windows Pythons intact; plain pwd elsewhere.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && { pwd -W 2>/dev/null || pwd; })"
REPO="$(cd "$HERE/../.." && { pwd -W 2>/dev/null || pwd; })"
: "${CL_WORK:?set CL_WORK to a scratch directory}"
CL_WORK="$(cygpath -m "$CL_WORK" 2>/dev/null || echo "$CL_WORK")"   # C:/... under Git Bash, unchanged elsewhere
PY39="${CL_PY39:-python}"
PY51="${CL_PY51:-}"
JEDI_PY="${CL_JEDI_PY:-}"
TSENV="${CL_TSENV:-}"
BUILD="${CL_BUILD:-$CL_WORK/build_snapshot}"
CLONES="${CL_CLONES:-C:/tmp/deepmap-bench-clones}"
ENGINES="${CL_ENGINES:-g39 g51}"
DRY="${CL_DRY:-}"
OUT="$CL_WORK/out"
CORP="$CL_WORK/corp"
HOME_DM="$(cygpath -m "${CL_DMHOME:-$CL_WORK/dmhome}" 2>/dev/null || echo "${CL_DMHOME:-$CL_WORK/dmhome}")"
export PYTHONUTF8=1 PYTHONHASHSEED=0

# corpus | pinned SHA | library prefix (D3-G1 src-only) | truth kind | truth subdir | truth extra args | origin
corpus_row() {
  case "$1" in
    psf__requests)     echo "23953c0c875219a715f081cf3de7c149a7629ccf|src/requests/|py|||clone" ;;
    pallets__flask)    echo "36e4a824f340fdee7ed50937ba8e7f6bc7d17f81|src/flask/|py|||clone" ;;
    pallets__click)    echo "679a7a0eccbdded7a6e85680bdaaf08003765e01|src/click/|py|||clone" ;;
    pallets__werkzeug) echo "1b00618e787f40dfb21eba29caf8f8be7c8e1d93|src/werkzeug/|py|||clone" ;;
    fastapi__fastapi)  echo "702fea83bb3ef0b558104835d7936af49650fb2d|fastapi/|py|||clone" ;;
    colinhacks__zod)   echo "912f0f51b0ced654d0069741e7160834dca742ee|packages/zod/src/|ts|packages/zod/src|--conditions=@zod/source|clone" ;;
    lujakob__nestjs-realworld-example-app) echo "c1c2cc4e448b279ff083272df1ac50d20c3304fa|src/|ts|src||https://github.com/lujakob/nestjs-realworld-example-app" ;;
    hagopj13__node-express-boilerplate)    echo "179ae84efec61b14206d0305d941daed6c6d07f9|src/|ts|src|--js|https://github.com/hagopj13/node-express-boilerplate" ;;
    stage0_build)      echo "9c8d50bceab96288f83877c5306167b54b6d918a||py|||stage0" ;;
    self)              echo "WORKTREE|build/|py|||worktree" ;;
    gin-gonic__gin)    echo "34dac209ffb6ef85cc78c5d217bbb7ad001d68fd||none|||clone" ;;
    julienschmidt__httprouter) echo "484018016424d215c0b87c42f4c9b57d980fbd00||none|||clone" ;;
    serde-rs__json)    echo "827a315bf2198558f0325b07bcc1e2cd973aba2f||none|||clone" ;;
    *) return 1 ;;
  esac
}
ALL_CORPORA="psf__requests pallets__flask pallets__click pallets__werkzeug fastapi__fastapi colinhacks__zod lujakob__nestjs-realworld-example-app hagopj13__node-express-boilerplate stage0_build self gin-gonic__gin julienschmidt__httprouter serde-rs__json"
CORPORA="${CL_CORPORA:-$ALL_CORPORA}"
FIXED_VARIANTS="$("$PY39" "$HERE/variants.py" list | awk '$1 != "VRC" {print $1}' | tr '\n' ' ')"
VARIANTS="V0 $(echo "${CL_VARIANTS:-$FIXED_VARIANTS}" | tr ' ' '\n' | grep -v '^V0$' | tr '\n' ' ')"

run() { if [ -n "$DRY" ]; then { printf '%q ' "$@"; echo; } >&2; else "$@"; fi; }
slug_of() { "$PY39" "$HERE/variants.py" slug "$1"; }
log() { echo "[run_matrix] $*"; }

materialize() {  # <name> -> $CORP/<name> at its pinned SHA
  # Built in <dest>.partial and renamed only when complete, so a failed clone / checkout (e.g. a path over Windows
  # MAX_PATH: clones run with core.longpaths=true) never leaves a half tree that a later run would take as present.
  local name="$1" row sha origin dest part rc
  row="$(corpus_row "$name")" || { log "unknown corpus $name"; return 1; }
  sha="${row%%|*}"; origin="${row##*|}"; dest="$CORP/$name"; part="$dest.partial"
  [ -d "$dest" ] && { log "$name: present ($dest)"; return 0; }
  if [ -e "$part" ]; then
    log "$name: a failed earlier materialization is left at $part — delete that directory, then re-run"; return 1
  fi
  case "$origin" in
    clone)    run git -c core.longpaths=true clone -q --no-hardlinks "$CLONES/$name" "$part" \
                && run git -C "$part" -c core.longpaths=true checkout -q --detach "$sha" ;;
    stage0)   run "$PY39" "$HERE/offline_calls.py" stage0-corpus "$part" --repo-id self-graphify-build ;;
    worktree) run git -c core.longpaths=true clone -q --no-hardlinks "$REPO" "$part" && \
              { if [ -n "$DRY" ]; then echo "(copy the working tree of $REPO over $part; git -C $part add -A)"; else
                  (cd "$REPO" && git ls-files -z --cached --others --exclude-standard | tar --null --ignore-failed-read -T - -cf -) | tar -xf - -C "$part" \
                  && git -C "$part" add -A; fi; } ;;
    http*)    run git -c core.longpaths=true clone -q "$origin" "$part" \
                && run git -C "$part" -c core.longpaths=true checkout -q --detach "$sha" ;;
  esac
  rc=$?
  if [ "$rc" != 0 ]; then log "$name: materialization FAILED (exit $rc) — nothing measured for it; see above"; return 1; fi
  run mv "$part" "$dest"
}

engine_py() { case "$1" in g39) echo "$PY39" ;; g51) echo "$PY51" ;; esac; }

dump() {  # <corpus> <variant> <engine>: two runs, byte-identical or VOID
  local c="$1" v="$2" e="$3" slug py base
  slug="$(slug_of "$v")"
  py="$(engine_py "$e")"; [ -z "$py" ] && { log "no python for $e (set CL_PY51)"; return 1; }
  base="$OUT/$c/${c}_${slug}_${e}"
  local rcs=""
  for rep in a b; do
    if [ -n "$DRY" ]; then
      run "$PY39" "$HERE/variants.py" run "$v" -- env DEEPMAP_HOME="$HOME_DM" "$py" "$HERE/offline_calls.py" build \
          "$CORP/$c" "$base.$rep.json" --build "$BUILD" --expect-engine graphify
    else
      "$PY39" "$HERE/variants.py" run "$v" -- env DEEPMAP_HOME="$HOME_DM" "$py" "$HERE/offline_calls.py" build \
          "$CORP/$c" "$base.$rep.json" --build "$BUILD" --expect-engine graphify > "$base.$rep.log" 2>&1
      rcs="$rcs$?"
    fi
  done
  if [ -z "$DRY" ]; then
    if [ "$rcs" != "00" ] && [ "$rcs" != "44" ]; then
      log "$c $v $e: VOID — a dump exited $rcs (3 = VOID: $(grep -hF '[offline_calls] VOID' "$base.a.log" "$base.b.log" 2>/dev/null | head -1 | cut -c1-300))"
      echo "VOID $c $v $e exit=$rcs" >> "$OUT/VOID.txt"
    elif cmp -s "$base.a.json" "$base.b.json"; then
      cp "$base.a.json" "$base.json"; log "$c $v $e: deterministic"
      if [ "$rcs" = "44" ]; then
        log "$c $v $e: INVARIANT BROKEN — cross-file EXTRACTED edges (the variant FAILS; see $base.a.log)"
        echo "INVARIANT $c $v $e" >> "$OUT/INVARIANT.txt"
      fi
    else log "$c $v $e: VOID — the two runs differ"; echo "VOID $c $v $e" >> "$OUT/VOID.txt"; fi
  fi
}

truth() {  # <corpus>
  local c="$1" row kind sub extra
  row="$(corpus_row "$c")"; IFS='|' read -r _sha _pfx kind sub extra _orig <<< "$row"
  [ -f "$OUT/$c/${c}_truth.json" ] && return 0
  case "$kind" in
    py) [ -n "$JEDI_PY" ] || { log "set CL_JEDI_PY for the jedi truth"; return 1; }
        run "$JEDI_PY" -I "$HERE/truth_jedi.py" "$CORP/$c" "$OUT/$c/${c}_truth.json" ;;
    ts) [ -n "$TSENV" ] || { log "set CL_TSENV for the TypeScript truth"; return 1; }
        # shellcheck disable=SC2086
        run node "$HERE/truth_ts.js" "$TSENV" "$CORP/$c" "$sub" "$OUT/$c/${c}_truth.json" $extra ;;
    none) return 0 ;;
  esac
}

score() {  # <corpus>
  local c="$1" row pfx kind e v slug f
  row="$(corpus_row "$c")"; IFS='|' read -r _sha pfx kind _sub _extra _orig <<< "$row"
  for e in $ENGINES; do
    for v in $VARIANTS; do
      slug="$(slug_of "$v")"
      f="$OUT/$c/${c}_${slug}_${e}.json"
      [ -n "$DRY" ] || [ -f "$f" ] || continue
      if [ "$kind" != none ]; then
        run "$PY39" "$HERE/compare_calls.py" "$OUT/$c/${c}_truth.json" "$f" "--json=$OUT/$c/score_${slug}_${e}_all.json" > /dev/null
        [ -n "$pfx" ] && run "$PY39" "$HERE/compare_calls.py" "$OUT/$c/${c}_truth.json" "$f" "--src-prefix=$pfx" \
            "--json=$OUT/$c/score_${slug}_${e}_src.json" > /dev/null
      fi
      if [ "$v" != V0 ]; then
        local tr=""; [ "$kind" != none ] && tr="--truth=$OUT/$c/${c}_truth.json"
        run "$PY39" "$HERE/delta.py" "$OUT/$c/${c}_V0_${e}.json" "$f" $tr "--json=$OUT/$c/delta_${slug}_${e}.json" > /dev/null
        run "$PY39" "$HERE/indegree_drift.py" "$OUT/$c/${c}_V0_${e}.json" "$f" "--json=$OUT/$c/drift_${slug}_${e}.json" > /dev/null
      fi
      if [ "$kind" != none ]; then
        run "$PY39" "$HERE/impact_spot.py" "--truth=$OUT/$c/${c}_truth.json" "--corpus=$c" "$f" \
            "--base=$OUT/$c/${c}_V0_${e}.json" "--json=$OUT/$c/spot_${slug}_${e}.json" > /dev/null 2>&1 || true
      fi
    done
  done
  if [ "$ENGINES" = "g39 g51" ]; then
    for v in $VARIANTS; do
      slug="$(slug_of "$v")"
      run "$PY39" "$HERE/engdiff.py" "$OUT/$c/${c}_${slug}_g39.json" "$OUT/$c/${c}_${slug}_g51.json" \
          "--base39=$OUT/$c/${c}_V0_g39.json" "--base51=$OUT/$c/${c}_V0_g51.json" "--json=$OUT/$c/engdiff_${slug}.json" > /dev/null
    done
  fi
}

log "work=$CL_WORK build=$BUILD engines=[$ENGINES] corpora=[$CORPORA]"
if [ "${#HOME_DM}" -gt 100 ]; then
  # Measured on requests: the engine's cache files under <DEEPMAP_HOME>/gout/<key>/ ran 106 characters past a
  # 142-character home (248 in all) and a 146-character home failed (MAX_PATH 260): `auto` then falls back to ast
  # and the dump is VOID. Deeper corpora need more room, so keep the home well under 100 characters.
  log "WARNING: DEEPMAP_HOME ($HOME_DM) is ${#HOME_DM} characters — set CL_DMHOME to a short directory"
fi
log "variants=[$VARIANTS]"
run mkdir -p "$OUT" "$CORP" "$HOME_DM"
if [ -z "${CL_BUILD:-}" ] && [ ! -d "$BUILD" ]; then
  run mkdir -p "$BUILD"
  if [ -n "$DRY" ]; then echo "cp $REPO/build/*.py $BUILD/" >&2; else cp "$REPO"/build/*.py "$BUILD"/; fi
fi
[ -n "$DRY" ] || log "code under test: $BUILD $("$PY39" "$HERE/offline_calls.py" codehash --build "$BUILD")"
for c in $CORPORA; do
  run mkdir -p "$OUT/$c"
  materialize "$c" || continue
  truth "$c"
  for e in $ENGINES; do for v in $VARIANTS; do dump "$c" "$v" "$e"; done; done
  score "$c"
done
# The oracle-seeded impact check over the four multi_hop questions (pooled per variant and engine).
for e in $ENGINES; do
  for v in $VARIANTS; do
    slug="$(slug_of "$v")"
    args=()
    for c in pallets__werkzeug pallets__flask pallets__click fastapi__fastapi; do
      case " $CORPORA " in *" $c "*) ;; *) continue ;; esac
      repo="$(echo "$c" | sed 's/__/\//')"
      if [ -z "$DRY" ] && [ ! -f "$OUT/$c/${c}_${slug}_${e}.json" ]; then
        log "impact_offline: no $c $v $e dump (VOID or not materialized) — left out of the pooled check"; continue
      fi
      args+=(--case "$repo" "$CORP/$c" "$OUT/$c/${c}_${slug}_${e}.json")
    done
    [ ${#args[@]} -gt 0 ] && run "$PY39" "$HERE/impact_offline.py" --questions "$REPO/benchmark/questions.yaml" "${args[@]}" \
        --json "$OUT/impact_offline_${slug}_${e}.json"
  done
done
log "done — VOID runs: $( [ -f "$OUT/VOID.txt" ] && wc -l < "$OUT/VOID.txt" || echo 0 ); INVARIANT failures: $( [ -f "$OUT/INVARIANT.txt" ] && wc -l < "$OUT/INVARIANT.txt" || echo 0 )"
