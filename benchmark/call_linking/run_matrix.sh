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
#
# D3-cs (benchmark/call_linking/PREREG_CS.md): CL_MATRIX=cs CL_PHASE=<phase> runs the C# matrix instead, from its own
# tables (cs_corpus_row, CS_C_CORPORA, CS_N_CORPORA below — apart from corpus_row, which harness gate H reads for
# PREREG.md; the 12 pinned N corpora are materialized through corpus_row exactly as PREREG's matrix does). Use a
# CL_WORK of its own. The phases follow PREREG_CS §8.1's order; each refuses to start before its inputs exist, and a
# step starts only after the previous one is recorded in RESULTS_CS.md (the operator's part).
# AC7: `self-snapshot` runs from THIS working tree, once; EVERY other phase runs FROM the snapshot it made — the
# snapshot's own copy of this script, `bash $CL_SELF_RC/benchmark/call_linking/run_matrix.sh` (it refuses to be any
# other copy), measuring the snapshot's build/ (CL_BUILD, if set, must name it) with the snapshot's kit, truth_roslyn
# and tests/. The first such phase records the snapshot's identity in CL_WORK/out/rc_identity.json (`offline_calls.py
# kithash --record`: build/*.py's code hash, the kit hash, the reference tool identity) and every later phase refuses
# a different one. PYTHONDONTWRITEBYTECODE=1 keeps __pycache__ out of the snapshot.
#   truth          copy the C# corpora (jellyfin__jellyfin, dotnet__eShop) from their read-only sources (CL_CS_SOURCES,
#                  default C:/tmp/deepmap-cs-d3) with §8.2's clone + checks (HEAD = the pinned SHA, a clean status,
#                  every .cs file w/lf; any other copy is VOID), then truth_roslyn.py on each copy TWICE with the
#                  registered options (CL_ROSLYN_ARGS): byte-identical or VOID; <id>_truth.sha256 records the hash
#   v0             the V0 dumps of both C# corpora on both engines, twice each (the first is the first RC run)
#   spots          impact_spot.py substitute on the V0 dumps and the truth: <id>/spots_final.json (§8.3's rule)
#   rest           needs spots_final.json: every other fixed arm of variants.py list-cs on the C# corpora (VT-cs-ifs /
#                  VT-cs-if0 only when the code under test names DEEPMAP_TYPED_CALLS_IFACE), V0 and VT-cs on the 13 N
#                  corpora; then the scores (whole graph + library prefixes), the per-corpus and pooled --family=cs
#                  receiver-form recall, the deltas against V0 and delta(VT-cs-ifs, VT-cs), the spots (impact_spot.py
#                  --corpus=<id> --final=<id>/spots_final.json: AC12, the FINAL list), the drift, engdiff, the N1 / N2
#                  identity checks (offline_calls.py identity), the two audit samples per C# corpus x engine (V0 ->
#                  VT-cs; the dispatch stratum VT-cs-ifs -> VT-cs, AC9: implementer AND override edges, or the
#                  --impl-fallback stratum) and LAST the engine-parity gate exactly as AC13 names it — the snapshot's
#                  `python tests/typed_calls_engine_parity_gates.py --corpora --only jellyfin__jellyfin,dotnet__eShop
#                  --py39 <CL_PY39> --py51 <CL_PY51>` (CALL_LINKING_CS_SOURCES = CL_CS_SOURCES) -> out/engine_parity_cs.log;
#                  only in this phase, only on both C# corpora (never with CL_CS_CORPORA narrowing them), never in a dry run
#   audit          after the blind review (§8.6 A): needs every audit dir's key.json and each reviewer's filled sheet
#                  (CL_SHEETS, default "sheet_r1.csv sheet_r2.csv", inside each dir); scores each sample
#                  (audit_sample.py score -> <dir>/score.json), the reference error rate per corpus (AC11:
#                  audit_sample.py refrate over both engines' main and dispatch-stratum samples -> <id>/refrate.json)
#                  and the pooled added-bucket precision per engine (G3a: main samples; G3b: dispatch-stratum samples)
#   gates          G5a (AC7: the G5a gates run from the snapshot): every gate of CS_G5A_GATES under each engine's Python
#                  (CL_PY39 / CL_PY51) and the full no-Neo4j suite (tests/run_all.py) under CL_PY39, the snapshot's
#                  tests/; one log per run under out/gates/<engine>/ with its exit code (out/gates/SUMMARY.txt). The
#                  Linux-container run of the suite on 0.8.51 is the operator's. The logs go to cs/gates/ for G5b
#   manifest       G5b (AC15), once the files to commit are placed under benchmark/call_linking/cs/: writes cs/.gitattributes
#                  (`* -text`) and cs/MANIFEST.sha256 there (offline_calls.py manifest "$CL_CS_DIR" --write; CL_CS_DIR
#                  names that folder, explicitly: the only phase that writes into a repository) and prints the line
#                  DECISION_CS.md records
#   self-snapshot  make CL_SELF_RC, AC7's ONE full-tree RC snapshot of this repository (from this working tree; once, at
#                  the RC suite run; an existing CL_SELF_RC is never touched): a clone of the repo with the working
#                  tree's tracked and untracked-not-ignored files copied over it, every file the working tree no longer
#                  holds (a deleted tracked file, staged or not) removed from the clone, and `git add -A` run in that new
#                  clone only; its index must then list exactly the working tree's files, else it stays at .partial
# CL_SELF_RC is required by every C# phase (never the moving working tree) and is read-only to every dump; the `self`
# N corpus is a copy of its .git plus exactly the files its index lists (no build output a run left in it).
# Every dump runs with CALL_LINKING_READONLY_ROOTS = CL_CLONES + CL_CS_SOURCES + CL_SELF_RC and
# DEEPMAP_GRAPHIFY_TIMEOUT=3600 (§8.2). Optional: CL_CS_VARIANTS (default every fixed arm of `variants.py list-cs`),
# CL_CS_CORPORA / CL_N_CORPORA (default CS_C_CORPORA / CS_N_CORPORA). The live layers (S, P, R) are not run here: their
# commands, from the snapshot (its tests/stage0_refreeze.py, benchmark/clone_graph.py and build/deepmap_cli.py), are in
# README.md.
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
# The C# matrix measures the RC snapshot's own build/ (AC7): this script runs from that snapshot (cs_guard)
[ "${CL_MATRIX:-}" = cs ] && BUILD="${CL_BUILD:-$REPO/build}"
CLONES="${CL_CLONES:-C:/tmp/deepmap-bench-clones}"
ENGINES="${CL_ENGINES:-g39 g51}"
DRY="${CL_DRY:-}"
OUT="$CL_WORK/out"
CORP="$CL_WORK/corp"
HOME_DM="$(cygpath -m "${CL_DMHOME:-$CL_WORK/dmhome}" 2>/dev/null || echo "${CL_DMHOME:-$CL_WORK/dmhome}")"
export PYTHONUTF8=1 PYTHONHASHSEED=0
# The C# matrix runs inside the RC snapshot (AC7): no __pycache__ is written into it, from the first command on
[ "${CL_MATRIX:-}" = cs ] && export PYTHONDONTWRITEBYTECODE=1

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

# =====================================================================================================================
# D3-cs (PREREG_CS.md): the C# matrix's own tables, apart from corpus_row (harness gate H reads that one for PREREG.md)
# =====================================================================================================================
# corpus | pinned SHA | library prefixes (G1, comma-separated; a pair belongs by its CALLER's file) | truth kind |
# read-only source (a directory of CL_CS_SOURCES) | origin
cs_corpus_row() {
  case "$1" in
    jellyfin__jellyfin) printf '%s\n' "dd42a121c43721c8984ba0026d6fbed4a526d01f|Emby.Naming/,Emby.Photos/,Emby.Server.Implementations/,Jellyfin.Api/,Jellyfin.Data/,Jellyfin.Server/,Jellyfin.Server.Implementations/,MediaBrowser.Common/,MediaBrowser.Controller/,MediaBrowser.LocalMetadata/,MediaBrowser.MediaEncoding/,MediaBrowser.Model/,MediaBrowser.Providers/,MediaBrowser.XbmcMetadata/,src/|roslyn|jellyfin|readonly" ;;
    dotnet__eShop)      printf '%s\n' "913acea66708a59595351174cee9b29f37179d29|src/|roslyn|eshop|readonly" ;;
    self)               printf '%s\n' "RC_SNAPSHOT||none||snapshot" ;;
    *) return 1 ;;
  esac
}
CS_C_CORPORA="jellyfin__jellyfin dotnet__eShop"
# The 13 N corpora (PREREG_CS §8.2): PREREG.md §3's twelve pinned ones through corpus_row (as PREREG's matrix
# materializes them), and `self`, this repository at the RC, copied from CL_SELF_RC.
CS_N_CORPORA="psf__requests pallets__flask pallets__click pallets__werkzeug fastapi__fastapi stage0_build colinhacks__zod lujakob__nestjs-realworld-example-app hagopj13__node-express-boilerplate gin-gonic__gin julienschmidt__httprouter serde-rs__json self"
CS_SOURCES="$(cygpath -m "${CL_CS_SOURCES:-C:/tmp/deepmap-cs-d3}" 2>/dev/null || echo "${CL_CS_SOURCES:-C:/tmp/deepmap-cs-d3}")"
SELF_RC="${CL_SELF_RC:+$(cygpath -m "$CL_SELF_RC" 2>/dev/null || echo "$CL_SELF_RC")}"
ROSLYN_ARGS="${CL_ROSLYN_ARGS:-}"
CS_SEED=20261022
CS_AUDIT_N=40
# PREREG_CS §8.7 G5a's gates, in its order (the N3 gate of AC14 is typed_calls_push_gates's csharp identity section and
# typed_calls_cs_gates section G, both in this list); `gates` runs each under both engines' Pythons, from the snapshot
CS_G5A_GATES="typed_calls_core_gates typed_calls_py_gates typed_calls_ts_gates typed_calls_ts_fuzz_gates typed_calls_push_gates typed_calls_shapes_gates typed_calls_engine_parity_gates typed_calls_cs_gates typed_calls_cs_fuzz_gates csharp_members_gates calls_family_filter_gates precise_fixes_gates precise_family_gates call_linking_harness_gates"

run_to() {  # <out file> <command ...>: the command's stdout into the file (CL_DRY: printed, nothing written)
  local f="$1"; shift
  if [ -n "$DRY" ]; then { printf '%q ' "$@"; echo "> $f"; } >&2; else "$@" > "$f"; fi
}

sha_of() { "$PY39" -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$1"; }

cs_check_copy() {  # <dir> <sha>: PREREG_CS §8.2's checks of a corpus copy (any other state is VOID)
  local d="$1" sha="$2" head st eol
  head="$(git -C "$d" rev-parse HEAD 2>/dev/null)"
  [ "$head" = "$sha" ] || { log "copy check: HEAD is '$head', not the pinned $sha"; return 1; }
  st="$(git -C "$d" status --short 2>&1)"
  [ -z "$st" ] || { log "copy check: git status --short is not empty: $(echo "$st" | head -3 | tr '\n' ' ')"; return 1; }
  eol="$(git -C "$d" ls-files --eol -- '*.cs' | awk '{print $2}' | sort | uniq -c)"
  [ -n "$eol" ] || { log "copy check: no .cs file"; return 1; }
  if echo "$eol" | awk '$2 != "w/lf" {bad=1} END {exit bad ? 0 : 1}'; then
    log "copy check: .cs files not all w/lf: $(echo "$eol" | tr '\n' ' ')"; return 1
  fi
  log "copy check: HEAD $sha, clean, .cs line endings: $(echo "$eol" | tr '\n' ' ')"
}

cs_copy_snapshot() {  # <snapshot> <dest>: the snapshot's .git plus exactly the files its index lists
  local src="$1" dest="$2"
  mkdir -p "$dest" && cp -a "$src/.git" "$dest/.git" || return 1
  (cd "$src" && git ls-files -z --cached | tar --null -T - -cf -) | tar -xf - -C "$dest"
  local st=("${PIPESTATUS[@]}")
  [ "${st[0]}" = 0 ] && [ "${st[1]}" = 0 ]
}

cs_materialize() {  # <name> -> $CORP/<name>: a C# corpus (read-only source + checks) or `self` (CL_SELF_RC); any
  # other N corpus through materialize (corpus_row)
  local name="$1" row sha pfx kind src origin dest part rc
  row="$(cs_corpus_row "$name")" || { materialize "$name"; return $?; }
  IFS='|' read -r sha pfx kind src origin <<< "$row"
  dest="$CORP/$name"; part="$dest.partial"
  [ -d "$dest" ] && { log "$name: present ($dest)"; return 0; }
  if [ -e "$part" ]; then
    log "$name: a failed earlier materialization is left at $part — delete that directory, then re-run"; return 1
  fi
  case "$origin" in
    readonly) run git -c core.longpaths=true clone -q --no-hardlinks -c core.autocrlf=false -c core.eol=lf \
                  "$CS_SOURCES/$src" "$part" && run git -C "$part" -c core.longpaths=true checkout -q --detach "$sha" ;;
    snapshot) if [ -z "$SELF_RC" ] || { [ -z "$DRY" ] && [ ! -d "$SELF_RC" ]; }; then
                log "$name: set CL_SELF_RC to the RC snapshot of this repository (CL_PHASE=self-snapshot makes it once);" \
                    "the C# matrix never measures the moving working tree"; return 1
              fi
              # `self` IS the snapshot (AC7): its .git plus exactly the files its index lists — never a build output a
              # run from the snapshot left in it (truth_roslyn/bin/, obj/: generated .cs files)
              if [ -n "$DRY" ]; then echo "(copy $SELF_RC/.git and exactly the files its index lists into $part)" >&2
              else cs_copy_snapshot "$SELF_RC" "$part"; fi ;;
  esac
  rc=$?
  if [ "$rc" != 0 ]; then log "$name: materialization FAILED (exit $rc) — nothing measured for it; see above"; return 1; fi
  if [ "$origin" = readonly ] && [ -z "$DRY" ] && ! cs_check_copy "$part" "$sha"; then
    log "$name: the copy is not in the registered state — VOID, nothing measured (left at $part)"
    echo "VOID $name copy" >> "$OUT/VOID.txt"; return 1
  fi
  run mv "$part" "$dest"
}

cs_truth() {  # <C# corpus>: truth_roslyn.py TWICE on the copy -> <id>_truth.json when byte-identical (+ its sha256)
  local c="$1" t="$OUT/$1/$1_truth" rcs="" rep
  [ -f "$t.json" ] && { log "$c: truth present ($t.json, sha256 $(cut -d' ' -f1 "$t.sha256" 2>/dev/null))"; return 0; }
  for rep in a b; do
    if [ -n "$DRY" ]; then
      # shellcheck disable=SC2086
      run "$PY39" "$HERE/truth_roslyn.py" "$CORP/$c" "$t.$rep.json" $ROSLYN_ARGS
    else
      # shellcheck disable=SC2086
      "$PY39" "$HERE/truth_roslyn.py" "$CORP/$c" "$t.$rep.json" $ROSLYN_ARGS > "$t.$rep.log" 2>&1
      rcs="$rcs$?"
    fi
  done
  [ -n "$DRY" ] && return 0
  if [ "$rcs" != "00" ]; then
    log "$c: truth VOID — truth_roslyn.py exited $rcs (see $t.a.log)"; echo "VOID $c truth exit=$rcs" >> "$OUT/VOID.txt"; return 1
  fi
  if cmp -s "$t.a.json" "$t.b.json"; then
    cp "$t.a.json" "$t.json"
    echo "$(sha_of "$t.json")  ${c}_truth.json" > "$t.sha256"
    log "$c: truth deterministic (two runs byte-identical), sha256 $(cut -d' ' -f1 "$t.sha256")"
  else
    log "$c: truth VOID — the two runs differ"; echo "VOID $c truth differ" >> "$OUT/VOID.txt"; return 1
  fi
}

cs_dump() {  # <corpus> <variant> <engine>: dump (twice) unless the deterministic dump exists — never re-made
  local f; f="$OUT/$1/$1_$(slug_of "$2")_$3.json"
  if [ -z "$DRY" ] && [ -f "$f" ]; then log "$1 $2 $3: present ($f)"; return 0; fi
  dump "$1" "$2" "$3"
}

cs_need() {  # <file> <what>: refuse the phase when an input of PREREG_CS §8.1's previous step is missing
  [ -n "$DRY" ] && return 0
  [ -f "$1" ] && return 0
  log "REFUSED: $2 is missing ($1) — run the previous phase first (PREREG_CS §8.1's order)"; return 1
}

cs_score() {  # <C# corpus>: scores, pools, deltas, spots, drift, N2, the audit samples, engine parity
  local c="$1" row sha pfx kind src origin e v slug f k b0 vt ifs t="$OUT/$1/$1_truth.json" fin="$OUT/$1/spots_final.json"
  row="$(cs_corpus_row "$c")"; IFS='|' read -r sha pfx kind src origin <<< "$row"
  for e in $ENGINES; do
    b0="$OUT/$c/${c}_V0_${e}.json"; vt="$OUT/$c/${c}_VT-cs_${e}.json"; ifs="$OUT/$c/${c}_VT-cs-ifs_${e}.json"
    for v in V0 $CS_ARMS; do
      slug="$(slug_of "$v")"; f="$OUT/$c/${c}_${slug}_${e}.json"
      [ -n "$DRY" ] || [ -f "$f" ] || continue
      run "$PY39" "$HERE/compare_calls.py" "$t" "$f" "--json=$OUT/$c/score_${slug}_${e}_all.json" > /dev/null
      run "$PY39" "$HERE/compare_calls.py" "$t" "$f" "--src-prefix=$pfx" "--json=$OUT/$c/score_${slug}_${e}_src.json" > /dev/null
      for k in all src; do
        run_to "$OUT/$c/pool_${slug}_${e}_${k}.json" "$PY39" "$HERE/compare_calls.py" pool \
            "$OUT/$c/score_${slug}_${e}_${k}.json" --family=cs
      done
      [ "$v" = V0 ] && continue
      run "$PY39" "$HERE/delta.py" "$b0" "$f" "--truth=$t" "--json=$OUT/$c/delta_${slug}_${e}.json" > /dev/null
      # AC12: the corpus's FINAL spot list from step 3 (impact_spot refuses a C# --corpus without it)
      run "$PY39" "$HERE/impact_spot.py" "--truth=$t" "--corpus=$c" "--final=$fin" "--base=$b0" "$f" \
          "--json=$OUT/$c/spot_${slug}_${e}.json" > /dev/null || true
    done
    { [ -n "$DRY" ] || [ -f "$vt" ]; } || { log "$c $e: no VT-cs dump — drift, N2 and the audit samples skipped"; continue; }
    run "$PY39" "$HERE/indegree_drift.py" "$b0" "$vt" "--json=$OUT/$c/drift_VT-cs_${e}.json" > /dev/null
    cs_identity "$c" "$e" N2
    run "$PY39" "$HERE/audit_sample.py" sample "--corpus=$CORP/$c" "--base=$b0" "--variant=$vt" "--truth=$t" \
        "--label=${c}_${e}" --seed "$CS_SEED" --n "$CS_AUDIT_N" "--out=$OUT/$c/audit_${e}"
    if [ -n "$HAS_IFACE" ] && { [ -n "$DRY" ] || [ -f "$ifs" ]; }; then
      run "$PY39" "$HERE/delta.py" "$ifs" "$vt" "--truth=$t" "--json=$OUT/$c/delta_VT-cs-ifs_to_VT-cs_${e}.json" > /dev/null
      run "$PY39" "$HERE/audit_sample.py" sample "--corpus=$CORP/$c" "--base=$ifs" "--variant=$vt" "--truth=$t" \
          "--label=${c}_${e}_impl" --seed "$CS_SEED" --n "$CS_AUDIT_N" "--out=$OUT/$c/audit_${e}_impl"
    elif [ -z "$HAS_IFACE" ]; then   # PREREG_CS §8.5's fallback dispatch stratum, AC9 (no DEEPMAP_TYPED_CALLS_IFACE)
      run "$PY39" "$HERE/audit_sample.py" sample "--corpus=$CORP/$c" "--base=$b0" "--variant=$vt" "--truth=$t" \
          "--label=${c}_${e}_impl" --seed "$CS_SEED" --n "$CS_AUDIT_N" "--out=$OUT/$c/audit_${e}_impl" --impl-fallback
    else
      log "$c $e: no VT-cs-ifs dump — the dispatch stratum (AC9: implementer and override edges) is not drawn"
    fi
  done
  if [ "$ENGINES" = "g39 g51" ]; then
    for v in V0 $CS_ARMS; do
      slug="$(slug_of "$v")"
      run "$PY39" "$HERE/engdiff.py" "$OUT/$c/${c}_${slug}_g39.json" "$OUT/$c/${c}_${slug}_g51.json" \
          "--base39=$OUT/$c/${c}_V0_g39.json" "--base51=$OUT/$c/${c}_V0_g51.json" "--json=$OUT/$c/engdiff_${slug}.json" > /dev/null
    done
  fi
}

cs_pool() {  # G1a pooled over both C# corpora, per engine x arm (whole graph and library prefixes)
  local e v slug k c f files
  for e in $ENGINES; do
    for v in V0 $CS_ARMS; do
      slug="$(slug_of "$v")"
      for k in all src; do
        files=()
        for c in $CS_C; do
          f="$OUT/$c/score_${slug}_${e}_${k}.json"
          { [ -n "$DRY" ] || [ -f "$f" ]; } && files+=("$f")
        done
        [ ${#files[@]} -gt 1 ] || { log "pool $v $e $k: fewer than two C# scores — not pooled"; continue; }
        run_to "$OUT/pool_${slug}_${e}_${k}.json" "$PY39" "$HERE/compare_calls.py" pool "${files[@]}" --family=cs
      done
    done
  done
}

cs_identity() {  # <corpus> <engine> [N1|N2|auto]: offline_calls.py identity V0 vs VT-cs -> N_IDENTITY.txt
  local c="$1" e="$2" rule="${3:-auto}" rc b0="$OUT/$1/$1_V0_$2.json" vt="$OUT/$1/$1_VT-cs_$2.json"
  if [ -n "$DRY" ]; then
    run "$PY39" "$HERE/offline_calls.py" identity "$b0" "$vt" "--corpus=$CORP/$c" "--rule=$rule" \
        "--json=$OUT/$c/identity_VT-cs_${e}.json"
    return 0
  fi
  if [ ! -f "$b0" ] || [ ! -f "$vt" ]; then
    log "$c $e: a V0 / VT-cs dump is missing (VOID or not made) — N not computable"
    echo "NOT COMPUTABLE $c $e (a dump is missing)" >> "$OUT/N_IDENTITY.txt"; return 0
  fi
  "$PY39" "$HERE/offline_calls.py" identity "$b0" "$vt" "--corpus=$CORP/$c" "--rule=$rule" \
      "--json=$OUT/$c/identity_VT-cs_${e}.json" > "$OUT/$c/identity_VT-cs_${e}.log" 2>&1
  rc=$?
  echo "$( [ "$rc" = 0 ] && echo HOLDS || echo "FAILS(exit $rc)") $c $e $(head -1 "$OUT/$c/identity_VT-cs_${e}.log" | cut -c1-240)" \
      >> "$OUT/N_IDENTITY.txt"
}

cs_n_corpus() {  # <N corpus>: V0 and VT-cs on both engines, then N1 (or N2 for a corpus holding a .cs file)
  local c="$1" e
  run mkdir -p "$OUT/$c"
  cs_materialize "$c" || return 0
  for e in $ENGINES; do cs_dump "$c" V0 "$e"; cs_dump "$c" VT-cs "$e"; done
  for e in $ENGINES; do cs_identity "$c" "$e" auto; done
}

cs_snapshot_files() {  # prune|check <repo> <clone>: the working tree's file list = (tracked + untracked-not-ignored)
  # minus the deleted tracked files. prune: remove from the clone every file its index lists that is not in that list
  # (a tracked file the working tree deleted, staged or not; exact paths from the clone's own index). check: the
  # clone's index must list exactly that list.
  "$PY39" - "$@" <<'PY'
import os
import subprocess
import sys

mode, repo, clone = sys.argv[1:4]


def ls(root, *args):
    out = subprocess.run(["git", "-C", root, "ls-files", "-z", *args], capture_output=True, check=True,
                         env=dict(os.environ, GIT_OPTIONAL_LOCKS="0")).stdout
    return {x for x in out.decode("utf-8", "surrogateescape").split("\0") if x}


want = ls(repo, "--cached", "--others", "--exclude-standard") - ls(repo, "--deleted")
have = ls(clone)
if mode == "prune":
    stale = sorted(have - want)
    for rel in stale:
        p = os.path.join(clone, *rel.split("/"))
        if os.path.lexists(p) and not os.path.isdir(p):
            os.remove(p)
    print(f"[run_matrix] self snapshot: {len(stale)} file(s) the working tree no longer holds removed from the clone"
          + (f": {stale[:10]}" if stale else ""), file=sys.stderr)
    sys.exit(0)
extra, missing = sorted(have - want), sorted(want - have)
if extra or missing:
    print(f"[run_matrix] self snapshot: its index lists {len(extra)} file(s) the working tree does not hold "
          f"{extra[:10]} and misses {len(missing)} it holds {missing[:10]}", file=sys.stderr)
    sys.exit(1)
print(f"[run_matrix] self snapshot: its index lists exactly the working tree's {len(want)} file(s)", file=sys.stderr)
PY
}

cs_self_snapshot() {  # CL_SELF_RC: the RC snapshot of this repository for the `self` N corpus (made once)
  local part
  [ -n "$SELF_RC" ] || { log "set CL_SELF_RC to the snapshot's directory (a new one, outside this repository)"; return 2; }
  case "$SELF_RC/" in "$REPO"/*) log "REFUSED: CL_SELF_RC ($SELF_RC) is inside this repository"; return 2 ;; esac
  [ -e "$SELF_RC" ] && { log "REFUSED: CL_SELF_RC ($SELF_RC) exists — a snapshot is made once and never touched"; return 1; }
  part="$SELF_RC.partial"
  [ -e "$part" ] && { log "a failed earlier snapshot is left at $part — delete that directory, then re-run"; return 1; }
  run git -c core.longpaths=true clone -q --no-hardlinks "$REPO" "$part" || return 1
  if [ -n "$DRY" ]; then
    echo "(copy the working tree's tracked and untracked-not-ignored files of $REPO over $part; remove from $part every" \
         "file the working tree no longer holds; git -C $part add -A; check $part's index lists exactly those files)" >&2
  else
    (cd "$REPO" && git ls-files -z --cached --others --exclude-standard | tar --null --ignore-failed-read -T - -cf -) \
      | tar -xf - -C "$part" || { log "the snapshot FAILED: the copy (left at $part)"; return 1; }
    # AC7 (K4): the working tree's deleted tracked files are removed — the clone checked them out from HEAD
    cs_snapshot_files prune "$REPO" "$part" || { log "the snapshot FAILED: removing deleted files (left at $part)"; return 1; }
    git -C "$part" -c core.longpaths=true add -A || { log "the snapshot FAILED: git add -A (left at $part)"; return 1; }
    cs_snapshot_files check "$REPO" "$part" || { log "the snapshot FAILED: its index is not the working tree's file list" \
                                                    "(left at $part)"; return 1; }
  fi
  run mv "$part" "$SELF_RC"
  [ -n "$DRY" ] || log "self snapshot $SELF_RC: $(git -C "$SELF_RC" ls-files | wc -l) files; .cs: $(git -C "$SELF_RC" ls-files -- '*.cs' '*.CS' | tr '\n' ' ')"
}

cs_guard() {  # AC7: every C# phase but self-snapshot runs FROM the RC snapshot, on the snapshot's build/
  if [ -z "$SELF_RC" ] || [ ! -d "$SELF_RC" ]; then
    log "REFUSED: CL_SELF_RC (${SELF_RC:-unset}) must name the RC snapshot (CL_PHASE=self-snapshot makes it, from the" \
        "working tree), and every other phase runs that snapshot's own copy of this script (AC7)"; return 1
  fi
  if ! [ "$REPO" -ef "$SELF_RC" ]; then
    log "REFUSED: this run_matrix.sh ($HERE) is not the RC snapshot's own copy — AC7: every RC command runs from the" \
        "snapshot; run: bash $SELF_RC/benchmark/call_linking/run_matrix.sh"; return 1
  fi
  if ! [ "$BUILD" -ef "$REPO/build" ]; then
    log "REFUSED: CL_BUILD ($BUILD) is not the snapshot's build/ ($REPO/build): the RC measures the snapshot's code"
    return 1
  fi
}

cs_rc_identity() {  # <phase>: out/rc_identity.json records the snapshot's identity once; a later phase must match it
  local rec="$OUT/rc_identity.json"
  if [ -n "$DRY" ]; then
    run "$PY39" "$HERE/offline_calls.py" kithash "--kit=$HERE" "--build=$BUILD" "--record=$rec"; return 0
  fi
  if "$PY39" "$HERE/offline_calls.py" kithash "--kit=$HERE" "--build=$BUILD" "--record=$rec" \
       > "$OUT/rc_identity.$1.json" 2> "$OUT/rc_identity.$1.log"; then
    log "RC identity ($1): $(grep -h 'kit hash\|RC identity' "$OUT/rc_identity.$1.log" | tr '\n' ' ' | cut -c1-400)"
    return 0
  fi
  log "REFUSED ($1): the snapshot's identity is not the one $rec records — the code, the kit or the reference tool" \
      "changed since the first phase (AC7: the snapshot is never edited): $(tail -3 "$OUT/rc_identity.$1.log" | tr '\n' ' ')"
  return 1
}

cs_engine_parity() {  # AC13: the engine-parity gate's C# corpora mode, EXACTLY as AC13 names it, from the snapshot
  local lg="$OUT/engine_parity_cs.log" rc
  if [ "$CS_C" != "$CS_C_CORPORA" ]; then
    log "engine parity (AC13): not run — it runs on both C# corpora only (CL_CS_CORPORA narrows them to [$CS_C])"
    return 0
  fi
  [ -n "$PY51" ] || { log "engine parity (AC13): set CL_PY51 to the 0.8.51 Python"; return 1; }
  if [ -n "$DRY" ]; then
    run env CALL_LINKING_CS_SOURCES="$CS_SOURCES" "$PY39" "$REPO/tests/typed_calls_engine_parity_gates.py" --corpora \
        --only jellyfin__jellyfin,dotnet__eShop --py39 "$PY39" --py51 "$PY51"
    return 0
  fi
  [ -f "$lg" ] && { log "engine parity (AC13): present ($lg) — never re-run"; return 0; }
  env CALL_LINKING_CS_SOURCES="$CS_SOURCES" "$PY39" "$REPO/tests/typed_calls_engine_parity_gates.py" --corpora \
      --only jellyfin__jellyfin,dotnet__eShop --py39 "$PY39" --py51 "$PY51" > "$lg.partial" 2>&1
  rc=$?
  echo "[run_matrix] exit $rc" >> "$lg.partial"
  mv "$lg.partial" "$lg"
  log "engine parity (AC13): exit $rc — $(grep -h '^RESULT:' "$lg" | head -1) (log: $lg)"
}

cs_audit() {  # after the blind review: each sample's score, the reference error rate per corpus (AC11), the pools
  local c e k d s
  local -a sargs dirs pools
  local sheets_csv; sheets_csv="$(echo $SHEETS | tr ' ' ',')"
  for c in $CS_C; do
    for e in $ENGINES; do
      for k in "" _impl; do
        d="$OUT/$c/audit_${e}${k}"
        cs_need "$d/key.json" "the audit sample $d (phase rest)" || return 1
        for s in $SHEETS; do cs_need "$d/$s" "a reviewer's filled sheet $d/$s (CL_SHEETS)" || return 1; done
      done
    done
  done
  for c in $CS_C; do
    dirs=()
    for e in $ENGINES; do
      for k in "" _impl; do
        d="$OUT/$c/audit_${e}${k}"
        sargs=(); for s in $SHEETS; do sargs+=("--sheet=$d/$s"); done
        run_to "$d/score.log" "$PY39" "$HERE/audit_sample.py" score "--key=$d/key.json" "${sargs[@]}" \
            "--json=$d/score.json"
        dirs+=("--dir=$d")
      done
    done
    # AC11: per engine over the union of the main and dispatch-stratum samples (a pair once); unreliable if either engine >= 10%
    run "$PY39" "$HERE/audit_sample.py" refrate "${dirs[@]}" "--sheets=$sheets_csv" "--json=$OUT/$c/refrate.json"
  done
  for e in $ENGINES; do   # G3a (main samples) / G3b (dispatch-stratum samples): pooled per family, both C# corpora on one engine
    for k in "" _impl; do
      pools=(); for c in $CS_C; do pools+=("--score=$OUT/$c/audit_${e}${k}/score.json"); done
      [ ${#pools[@]} -gt 1 ] || { log "audit pool $e${k:-_main}: fewer than two C# corpora — not pooled (G3a / G3b pool both)"; continue; }
      run "$PY39" "$HERE/audit_sample.py" pool "${pools[@]}" --bucket=added "--json=$OUT/audit_pool_added_${e}${k:-_main}.json"
    done
  done
  return 0
}

cs_gates() {  # G5a: each gate under each engine's Python, and the no-Neo4j suite on the dev end, from the snapshot
  local e py g lg rc
  run mkdir -p "$OUT/gates"
  for e in $ENGINES; do
    py="$(engine_py "$e")"; [ -n "$py" ] || { log "gates: no python for $e (set CL_PY51)"; return 1; }
    run mkdir -p "$OUT/gates/$e"
    for g in $CS_G5A_GATES; do
      lg="$OUT/gates/$e/$g.log"
      if [ ! -f "$REPO/tests/$g.py" ]; then   # G5a: "A listed gate that does not exist at RC does not pass"
        log "gates: $g does not exist in the snapshot — it does not pass (G5a)"
        [ -n "$DRY" ] || echo "MISSING $e $g" >> "$OUT/gates/SUMMARY.txt"; continue
      fi
      if [ -n "$DRY" ]; then run "$PY39" "$HERE/variants.py" run V0 -- "$py" "$REPO/tests/$g.py"; continue; fi
      "$PY39" "$HERE/variants.py" run V0 -- "$py" "$REPO/tests/$g.py" > "$lg" 2>&1; rc=$?
      echo "[run_matrix] exit $rc" >> "$lg"
      echo "$( [ "$rc" = 0 ] && echo GREEN || echo "RED(exit $rc)") $e $g" >> "$OUT/gates/SUMMARY.txt"
      log "gates: $e $g exit $rc"
    done
  done
  lg="$OUT/gates/g39/run_all.log"; run mkdir -p "$OUT/gates/g39"
  if [ -n "$DRY" ]; then run "$PY39" "$HERE/variants.py" run V0 -- "$PY39" "$REPO/tests/run_all.py"; return 0; fi
  "$PY39" "$HERE/variants.py" run V0 -- "$PY39" "$REPO/tests/run_all.py" > "$lg" 2>&1; rc=$?
  echo "[run_matrix] exit $rc" >> "$lg"
  echo "$( [ "$rc" = 0 ] && echo GREEN || echo "RED(exit $rc)") g39 run_all" >> "$OUT/gates/SUMMARY.txt"
  log "gates: the no-Neo4j suite (g39) exit $rc; summary $OUT/gates/SUMMARY.txt"
}

cs_main() {
  local phase="${CL_PHASE:-}" sep ro code c e v arms
  case "$phase" in
    truth|v0|spots|rest|audit|gates|manifest|self-snapshot) ;;
    *) log "CL_MATRIX=cs needs CL_PHASE=truth|v0|spots|rest|audit|gates|manifest|self-snapshot (PREREG_CS §8.1's order)"
       return 2 ;;
  esac
  [ "$phase" = self-snapshot ] && { cs_self_snapshot; return $?; }
  export PYTHONDONTWRITEBYTECODE=1
  cs_rc_identity "$phase" || return 1
  sep="$("$PY39" -c 'import os; print(os.pathsep)')"
  ro="$CLONES$sep$CS_SOURCES"; [ -n "$SELF_RC" ] && ro="$ro$sep$SELF_RC"
  export CALL_LINKING_READONLY_ROOTS="$ro" DEEPMAP_GRAPHIFY_TIMEOUT=3600
  CS_C="${CL_CS_CORPORA:-$CS_C_CORPORA}"; CS_N="${CL_N_CORPORA:-$CS_N_CORPORA}"
  arms="$("$PY39" "$HERE/variants.py" list-cs | awk '$1 != "VRC-cs" && $1 != "V0" {print $1}' | tr '\n' ' ')"
  CS_ARMS="$(echo "${CL_CS_VARIANTS:-$arms}" | tr ' ' '\n' | grep -v '^V0$' | grep -v '^$' | tr '\n' ' ')"
  code="$BUILD"; [ -d "$code" ] || code="$REPO/build"
  if grep -q "DEEPMAP_TYPED_CALLS_IFACE" "$code"/*.py 2>/dev/null; then HAS_IFACE=1; else
    HAS_IFACE=""
    CS_ARMS="$(echo "$CS_ARMS" | tr ' ' '\n' | grep -v '^VT-cs-if[s0]$' | tr '\n' ' ')"
    log "the code under test does not name DEEPMAP_TYPED_CALLS_IFACE: VT-cs-ifs and VT-cs-if0 are not run, and the" \
        "dispatch stratum (AC9) is PREREG_CS §8.5's fallback"
  fi
  log "D3-cs phase=$phase work=$CL_WORK build=$BUILD engines=[$ENGINES] C#=[$CS_C] N=[$CS_N] arms=[$CS_ARMS]"
  log "read-only roots: $CALL_LINKING_READONLY_ROOTS; self snapshot: ${SELF_RC:-(unset)}"
  SHEETS="${CL_SHEETS:-sheet_r1.csv sheet_r2.csv}"
  case "$phase" in
    truth)
      run_to "$OUT/truth_options.txt" printf '%s\n' "$ROSLYN_ARGS"
      for c in $CS_C; do run mkdir -p "$OUT/$c"; cs_materialize "$c" || continue; cs_truth "$c"; done ;;
    v0)
      for c in $CS_C; do cs_need "$OUT/$c/${c}_truth.json" "the $c reference truth (phase truth)" || return 1; done
      for c in $CS_C; do cs_materialize "$c" || continue; for e in $ENGINES; do cs_dump "$c" V0 "$e"; done; done ;;
    spots)
      [ "$ENGINES" = "g39 g51" ] || { log "REFUSED: the spot check reads the V0 dumps of BOTH engines (CL_ENGINES=\"g39 g51\")"; return 1; }
      for c in $CS_C; do
        for e in g39 g51; do cs_need "$OUT/$c/${c}_V0_${e}.json" "the $c V0 dump on $e (phase v0)" || return 1; done
      done
      for c in $CS_C; do
        run "$PY39" "$HERE/impact_spot.py" substitute "--truth=$OUT/$c/${c}_truth.json" "--corpus=$c" \
            "--base39=$OUT/$c/${c}_V0_g39.json" "--base51=$OUT/$c/${c}_V0_g51.json" "--json=$OUT/$c/spots_final.json"
      done ;;
    rest)
      for c in $CS_C; do cs_need "$OUT/$c/spots_final.json" "the $c final spot list (phase spots)" || return 1; done
      for c in $CS_C; do
        cs_materialize "$c" || continue
        for e in $ENGINES; do for v in $CS_ARMS; do cs_dump "$c" "$v" "$e"; done; done
      done
      for c in $CS_N; do cs_n_corpus "$c"; done
      for c in $CS_C; do cs_score "$c"; done
      cs_pool
      cs_engine_parity ;;
    audit)
      cs_audit || return 1 ;;
    gates)
      cs_gates || return 1 ;;
    manifest)
      [ -n "${CL_CS_DIR:-}" ] || { log "REFUSED: CL_PHASE=manifest writes cs/.gitattributes and cs/MANIFEST.sha256 (G5b," \
                                       "AC15) into CL_CS_DIR: set it to the repository's benchmark/call_linking/cs/," \
                                       "once the files to commit are placed there"; return 2; }
      run "$PY39" "$HERE/offline_calls.py" manifest "$(cygpath -m "$CL_CS_DIR" 2>/dev/null || echo "$CL_CS_DIR")" --write ;;
  esac
  log "done ($phase) — VOID runs: $( [ -f "$OUT/VOID.txt" ] && wc -l < "$OUT/VOID.txt" || echo 0 ); INVARIANT failures:" \
      "$( [ -f "$OUT/INVARIANT.txt" ] && wc -l < "$OUT/INVARIANT.txt" || echo 0 ); N identity: $( [ -f "$OUT/N_IDENTITY.txt" ] && grep -c . "$OUT/N_IDENTITY.txt" || echo 0 ) line(s)"
}

[ "${CL_MATRIX:-}" = cs ] || log "work=$CL_WORK build=$BUILD engines=[$ENGINES] corpora=[$CORPORA]"
if [ "${#HOME_DM}" -gt 100 ]; then
  # Measured on requests: the engine's cache files under <DEEPMAP_HOME>/gout/<key>/ ran 106 characters past a
  # 142-character home (248 in all) and a 146-character home failed (MAX_PATH 260): `auto` then falls back to ast
  # and the dump is VOID. Deeper corpora need more room, so keep the home well under 100 characters.
  log "WARNING: DEEPMAP_HOME ($HOME_DM) is ${#HOME_DM} characters — set CL_DMHOME to a short directory"
fi
[ "${CL_MATRIX:-}" = cs ] || log "variants=[$VARIANTS]"
if [ "${CL_MATRIX:-}" = cs ] && [ "${CL_PHASE:-}" = self-snapshot ]; then cs_main; exit $?; fi
if [ "${CL_MATRIX:-}" = cs ]; then cs_guard || exit 2; fi
run mkdir -p "$OUT" "$CORP" "$HOME_DM"
if [ -z "${CL_BUILD:-}" ] && [ ! -d "$BUILD" ]; then
  run mkdir -p "$BUILD"
  if [ -n "$DRY" ]; then echo "cp $REPO/build/*.py $BUILD/" >&2; else cp "$REPO"/build/*.py "$BUILD"/; fi
fi
[ -n "$DRY" ] || log "code under test: $BUILD $("$PY39" "$HERE/offline_calls.py" codehash --build "$BUILD")"
if [ "${CL_MATRIX:-}" = cs ]; then cs_main; exit $?; fi
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
