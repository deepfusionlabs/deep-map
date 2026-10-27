#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build -> freeze (or check) the STAGE-0 oracles on a THROWAWAY Neo4j instance, then remove it.  [NEO4J]

The two committed no-regression oracles are frozen top-K orderings of `relpath::name:line` keys, so they
only mean something against the exact corpus they were frozen on. Before 2026-09 that corpus was the LIVE
work tree (every edit to build/ silently invalidated them: the live validation found 19/50 and 13/50 keys
still valid) and the harness talked to whatever sat on bolt 7687 (on that box: another project's Docker
Neo4j). This driver makes the whole round trip reproducible and safe:

  1. materializes each oracle's corpus from a pinned COMMIT (`git archive`), never the work tree;
  2. provisions a throwaway DeepMap project + its OWN Neo4j instance under --work (outside this repo),
     pre-allocating a non-base port pair so it can never adopt the shared base store 7687;
  3. builds each oracle's graph exactly as its `builder` says:
       graphify-default-sync -> the PRODUCT's own `deepmap sync` with every DEEPMAP_* knob unset (the
                                current default graph: graphify engine, self-call re-binding, ...);
       stage0-ast            -> tests/stage0_real_graph.py (the harness's own stdlib-ast builder);
  4. --freeze: re-pins the corpus (--sha, default HEAD) and re-freezes + registers the oracle via
     tests/stage0_freeze_oracle.py; --check (default): gates the committed oracle with
     tests/stage0_regression_check.py against the freshly rebuilt graph (proves a rebuild reproduces it);
  5. ALWAYS cleans up (unless --keep): provision stop, instance-rm --key <key> --purge --yes, and the
     --work tree.

Run (NEO4J_PASSWORD defaults to the dev password `deepmap`; the ML stack + the graphify engine are needed):
    python tests/stage0_refreeze.py --work C:/tmp/s0 --check                 # verify both oracles
    python tests/stage0_refreeze.py --work C:/tmp/s0 --freeze [--sha <40hex>] # re-pin + re-freeze both
    python tests/stage0_refreeze.py --work C:/tmp/s0 --check --repo-id self-graphify-build
    python tests/stage0_refreeze.py --work C:/tmp/s0 --check --env DEEPMAP_TYPED_CALLS=1 --env DEEPMAP_CALLS_AUDIT=1

--env KEY=VAL (repeatable; --check only) runs every step with that DEEPMAP_* graph flag set on top of the stripped
default environment — the S layer of the call-linking measurement checks the committed oracles against a
VARIANT's graph (benchmark/call_linking/PREREG.md §8.4 S). Only DEEPMAP_* keys are accepted, never the home /
interpreter (KEEP_ENV): those come from the caller's environment. An oracle is only ever FROZEN on the default
graph, so --env with --freeze is refused.

`plan()` is pure (the hermetic gates in tests/stage0_harness_gates.py lock it); `main()` executes it.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

TESTS = Path(__file__).resolve().parent
REPO = TESTS.parent
BUILD = REPO / "build"
sys.path.insert(0, str(TESTS))
import stage0_portable as P  # noqa: E402

# Each oracle's RECIPE: what its corpus is, what builds its graph, where it is written.
RECIPES: Dict[str, Dict[str, Any]] = {
    "self-graphify-build": {
        "builder": "graphify-default-sync", "tree": "build", "paths": None,
        "oracle_path": "tests/oracles/self-graphify-build.json",
        "queries_file": "tests/oracles/queries_graphify_build.json", "repo_dir": "build",
        "note": ("build/ at the pinned commit, graphed by the product's own DEFAULT sync (graphify engine, "
                 "every DEEPMAP_* knob unset) on a throwaway instance — tests/stage0_refreeze.py."),
    },
    "self": {
        "builder": "stage0-ast", "tree": None, "paths": ["build", "scripts", "tests"],
        "oracle_path": "tests/stage0_real_oracle.json", "queries_file": None, "repo_dir": ".",
        "note": ("build/ + scripts/ + tests/ at the pinned commit, graphed by the harness's own stdlib-ast "
                 "builder (tests/stage0_real_graph.py) on a throwaway instance — tests/stage0_refreeze.py."),
    },
}
ORDER = ("self-graphify-build", "self")   # the product sync first (it stamps the owner), then the ast wipe
KEEP_ENV = ("DEEPMAP_HOME", "DEEPMAP_PYTHON")
# Reproducibility of the product sync. Until round-4 F5, embeddings.generate_embeddings `random.shuffle`d
# the pending nodes (HNSW insert-order quality) with the process-global, UNSEEDED RNG, which changed both the
# vector-index insert order and the encode CHUNKS — and a batched encode is not bit-identical to encoding the
# same text in another batch (measured: 0/50 bit-equal batch vs single; 119/571 embeddings differed between
# two fresh builds of the same corpus), so near-tied ranks 4-10 reshuffled between builds. F5 made that
# order a pure function of the corpus (a PRIVATE RNG seeded from the sorted content hashes; the global
# `random` is no longer consumed), so the product sync now reproduces on its own. The harness still runs the
# UNCHANGED product sync with Python's `random` seeded (+ PYTHONHASHSEED=0) as defence in depth against any
# other process-global RNG / hash-order dependence. F5's order differs from the old seeded shuffle, so the
# graphify oracle was re-frozen after it (tests/oracles/README.md).
SEEDED_RUN = ("import os, random, runpy, sys; random.seed(0); sys.argv = sys.argv[1:]; "
              "sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[0]))); "
              "runpy.run_path(sys.argv[0], run_name='__main__')")


def child_env(base=None, extra=None) -> Dict[str, str]:
    """The environment every step runs with: no NEO4J_URI (routing goes through the throwaway project's
    OWN registry entry — an operator escape-hatch URI would disable per-project instancing), no DEEPMAP_*
    knob except the home/interpreter (the DEFAULT pipeline), UTF-8, offline model cache. `extra` (from
    --env, already validated by parse_env_args) is set on top of the stripped environment."""
    e = dict(os.environ if base is None else base)
    for k in list(e):
        if k == "NEO4J_URI" or (k.startswith("DEEPMAP_") and k not in KEEP_ENV):
            e.pop(k)
    e.setdefault("NEO4J_PASSWORD", "deepmap")
    e["PYTHONUTF8"] = "1"
    e["PYTHONHASHSEED"] = "0"
    e.setdefault("HF_HUB_OFFLINE", "1")
    for k, v in (extra or {}).items():
        e[k] = v
    return e


def parse_env_args(items) -> "tuple[Dict[str, str], Optional[str]]":
    """--env KEY=VAL items -> ({key: val}, None), or ({}, why not). Only a DEEPMAP_* graph flag is accepted:
    a key matching DEEPMAP_[A-Z0-9_]+ that is not the home / interpreter (KEEP_ENV)."""
    out: Dict[str, str] = {}
    for item in items or []:
        if "=" not in item:
            return {}, f"--env expects KEY=VAL (got {item!r})"
        k, v = item.split("=", 1)
        if not k.startswith("DEEPMAP_") or not k[len("DEEPMAP_"):] or \
                any(not (c.isupper() or c.isdigit() or c == "_") for c in k):
            return {}, f"--env takes DEEPMAP_* graph flags only (got {k!r})"
        if k in KEEP_ENV:
            return {}, f"--env cannot set {k} (the home / interpreter come from your environment)"
        out[k] = v
    return out, None


def check_work_dir(work: Path, repo: Path = REPO) -> Optional[str]:
    """None when `work` is a safe throwaway location, else why not: it must be absolute, outside this
    repository, and empty (or not exist yet)."""
    if not work.is_absolute():
        return f"--work must be an absolute path (got {work})"
    try:
        w = Path(os.path.normcase(str(work.resolve())))
        r = Path(os.path.normcase(str(repo.resolve())))
        if w == r or r in w.parents or w in r.parents:
            return f"--work {work} must be OUTSIDE this repository ({repo}) and not contain it"
    except Exception as exc:  # noqa: BLE001
        return f"--work {work} could not be resolved ({exc})"
    if work.exists() and any(work.iterdir()):
        return f"--work {work} is not empty — give a fresh directory (it is deleted afterwards)"
    return None


def plan(work: Path, repo_ids: List[str], mode: str, corpora: Dict[str, Dict[str, Any]],
         key: str = "<key>", py: str = sys.executable) -> List[Dict[str, Any]]:
    """The ordered steps (pure). Each: {name, argv | action, timeout, cleanup?}. `corpora` maps repo_id
    -> its corpus record (from the registry for --check, freshly made for --freeze)."""
    proj = work / "proj"
    steps: List[Dict[str, Any]] = []
    ids = [r for r in ORDER if r in repo_ids]
    for rid in ids:
        rec = RECIPES[rid]
        dest = proj if rec["builder"] == "graphify-default-sync" else work / "ast"
        steps.append({"name": f"materialize {rid} corpus", "action": "materialize", "repo_id": rid,
                      "dest": dest, "corpus": corpora[rid]})
    steps.append({"name": "pre-allocate a NON-base Neo4j pair", "action": "allocate", "project": proj})
    steps.append({"name": "provision the throwaway instance", "timeout": 600,
                  "argv": [py, str(BUILD / "provision.py"), "ensure", str(proj)]})
    steps.append({"name": "verify the route is the throwaway's own instance", "action": "verify_route",
                  "project": proj})
    for rid in ids:
        rec, corpus = RECIPES[rid], corpora[rid]
        if rec["builder"] == "graphify-default-sync":
            steps.append({"name": f"{rid}: product default sync", "timeout": 3600,
                          "argv": [py, "-c", SEEDED_RUN, str(BUILD / "deepmap_cli.py"), "sync", str(proj)]})
            steps.append({"name": f"{rid}: the sync resolved the graphify engine", "action": "verify_engine",
                          "project": proj})
        else:
            steps.append({"name": f"{rid}: harness ast graph", "timeout": 7200,
                          "argv": [py, str(TESTS / "stage0_real_graph.py"), "--project", str(proj),
                                   "--repo-dir", str(work / "ast"), "--require-real-embeddings"]})
        if mode == "freeze":
            argv = [py, str(TESTS / "stage0_freeze_oracle.py"), "--project", str(proj),
                    "--repo-id", rid, "--repo-dir", rec["repo_dir"], "--oracle-path", rec["oracle_path"],
                    "--corpus-sha", corpus["sha"], "--builder", rec["builder"], "--register"]
            argv += (["--corpus-tree", rec["tree"]] if rec["tree"] else ["--corpus-paths", ",".join(rec["paths"])])
            if rec["queries_file"]:
                argv += ["--queries-file", rec["queries_file"]]
            steps.append({"name": f"{rid}: freeze + register", "timeout": 3600, "argv": argv})
            steps.append({"name": f"{rid}: registry note", "action": "note", "repo_id": rid})
        else:
            steps.append({"name": f"{rid}: regression gate", "timeout": 3600,
                          "argv": [py, str(TESTS / "stage0_regression_check.py"), "--repo-id", rid,
                                   "--project", str(proj)]})
    # cleanup — always
    steps.append({"name": "stop the throwaway Neo4j", "cleanup": True, "timeout": 300,
                  "argv": [py, str(BUILD / "provision.py"), "stop", str(proj)]})
    steps.append({"name": "instance-rm --purge the throwaway", "cleanup": True, "timeout": 300,
                  "argv": [py, str(BUILD / "provision.py"), "instance-rm", "--key", key, "--purge", "--yes"]})
    steps.append({"name": "delete the work tree", "cleanup": True, "action": "rmtree", "path": work})
    return steps


def _registry() -> Dict[str, Any]:
    return P.load_registry(REPO / P.REGISTRY_RELPATH)


def _head_sha() -> str:
    p = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=60)
    return p.stdout.strip().lower()


def _run(step: Dict[str, Any], env: Dict[str, str]) -> int:
    t0 = time.time()
    print(f"\n[refreeze] >> {step['name']}: {' '.join(step['argv'])}", flush=True)
    try:
        p = subprocess.run(step["argv"], cwd=str(REPO), env=env, timeout=step.get("timeout", 600))
        rc = p.returncode
    except subprocess.TimeoutExpired:
        rc = 124
    print(f"[refreeze] << {step['name']}: rc={rc} ({time.time() - t0:.0f}s)", flush=True)
    return rc


def _load_build(name: str):
    if str(BUILD) not in sys.path:
        sys.path.insert(0, str(BUILD))
    return __import__(name)


def main(argv=None) -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="STAGE-0 oracle build+freeze/check on a throwaway instance.")
    ap.add_argument("--work", required=True, help="a fresh absolute directory OUTSIDE this repo (deleted after).")
    ap.add_argument("--repo-id", default="all", choices=("all",) + ORDER)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="gate the committed oracles (default).")
    mode.add_argument("--freeze", action="store_true", help="re-pin the corpus and re-freeze the oracles.")
    ap.add_argument("--sha", default=None, help="--freeze: the commit to pin (default: HEAD).")
    ap.add_argument("--keep", action="store_true", help="leave the instance + work tree for inspection.")
    ap.add_argument("--env", action="append", default=[], metavar="KEY=VAL",
                    help="--check only: run with this DEEPMAP_* graph flag set (repeatable).")
    args = ap.parse_args(argv)

    extra_env, env_problem = parse_env_args(args.env)
    if env_problem is None and extra_env and args.freeze:
        env_problem = "--env is for --check only: an oracle is frozen on the default graph"
    if env_problem:
        print(f"[refreeze] FATAL: {env_problem}", file=sys.stderr)
        return 2
    work = Path(args.work)
    problem = check_work_dir(work)
    if problem:
        print(f"[refreeze] FATAL: {problem}", file=sys.stderr)
        return 2
    repo_ids = list(ORDER) if args.repo_id == "all" else [args.repo_id]
    run_mode = "freeze" if args.freeze else "check"
    corpora: Dict[str, Dict[str, Any]] = {}
    for rid in repo_ids:
        rec = RECIPES[rid]
        if run_mode == "freeze":
            sha = (args.sha or _head_sha()).strip().lower()
            corpora[rid] = P.make_corpus(sha, builder=rec["builder"], tree=rec["tree"], paths=rec["paths"])
        else:
            entry = (_registry().get("oracles") or {}).get(rid) or {}
            if not entry.get("corpus"):
                print(f"[refreeze] FATAL: registry entry {rid!r} has no pinned corpus — run --freeze first",
                      file=sys.stderr)
                return 2
            corpora[rid] = entry["corpus"]

    env = child_env(extra=extra_env)
    if extra_env:
        print("[refreeze] --env: " + " ".join(f"{k}={v}" for k, v in sorted(extra_env.items())))
    work.mkdir(parents=True, exist_ok=True)
    PR = _load_build("port_registry")
    proj = work / "proj"
    proj.mkdir(parents=True, exist_ok=True)
    key = PR.project_key(proj)
    steps = plan(work, repo_ids, run_mode, corpora, key=key)
    failed: Optional[str] = None
    rc_final = 0
    try:
        for step in [s for s in steps if not s.get("cleanup")]:
            action = step.get("action")
            if action == "materialize":
                n = P.materialize_corpus(REPO, step["corpus"], step["dest"])
                print(f"[refreeze] {step['name']}: {n} files @ {step['corpus']['sha'][:12]} -> {step['dest']}")
                if n == 0:
                    failed = step["name"]
                    break
            elif action == "allocate":
                pair = PR.allocate_neo4j_ports(step["project"], base_adopt_ok=lambda: False)
                print(f"[refreeze] {step['name']}: {pair}")
                if not pair or tuple(pair) == (PR.BASE_BOLT, PR.BASE_HTTP):
                    failed = step["name"]
                    break
            elif action == "verify_route":
                route = PR.resolve_neo4j_route(step["project"], honor_env=False)
                print(f"[refreeze] {step['name']}: {route!r}")
                if route.source not in P.OWN_INSTANCE_SOURCES or route.bolt == PR.BASE_BOLT:
                    failed = step["name"]
                    break
            elif action == "verify_engine":
                try:
                    m = json.loads((step["project"] / ".deepmap" / "graph_build.json").read_text(encoding="utf-8"))
                except Exception as exc:  # noqa: BLE001
                    m = {"_error": str(exc)}
                eng = str(m.get("extractor") or m.get("extractor_resolved") or "")
                print(f"[refreeze] {step['name']}: extractor={eng!r} verdict={m.get('verdict')!r} "
                      f"nodes={m.get('nodes') or m.get('node_count')}")
                if "graphify" not in eng:
                    failed = step["name"] + " (the default sync did not run graphify)"
                    break
            elif action == "note":
                reg_path = REPO / P.REGISTRY_RELPATH
                reg = P.load_registry(reg_path)
                ent = reg["oracles"].get(step["repo_id"])
                if ent is not None:
                    ent["note"] = RECIPES[step["repo_id"]]["note"]
                    reg_path.write_text(json.dumps(reg, indent=2, sort_keys=True), encoding="utf-8")
            else:
                rc = _run(step, env)
                if rc != 0:
                    failed = f"{step['name']} (rc={rc})"
                    rc_final = rc if rc in (1, 2, 3) else 1
                    break
    finally:
        if args.keep:
            print(f"\n[refreeze] --keep: left {work} and the instance (key {key}) in place — remove them with "
                  f"`python build/provision.py stop {proj}` + `python build/provision.py instance-rm --key {key} "
                  f"--purge --yes`.")
        else:
            for step in [s for s in steps if s.get("cleanup")]:
                if step.get("action") == "rmtree":
                    for attempt in range(5):
                        shutil.rmtree(step["path"], ignore_errors=True)
                        if not Path(step["path"]).exists():
                            break
                        time.sleep(2)
                    print(f"[refreeze] {step['name']}: {'done' if not Path(step['path']).exists() else 'LEFT BEHIND'}")
                else:
                    _run(step, env)
    if failed:
        print(f"\n[refreeze] FAILED at: {failed}", file=sys.stderr)
        return rc_final or 1
    print(f"\n[refreeze] OK — {run_mode} of {', '.join(repo_ids)} passed on a throwaway instance "
          f"(corpus {', '.join(sorted({c['sha'][:12] for c in corpora.values()}))}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
