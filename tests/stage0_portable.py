#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""STAGE-0 PORTABLE core — repo-agnostic helpers shared by the oracle builder/freezer/gate.

The STAGE-0 no-regression oracle was welded to THIS repo: relpaths were computed against the repo
root, the frozen queries + output path were hard-coded, and there was no way to gate an arbitrary
*external* clone. This module is the pure, no-Neo4j core that decouples it so the same harness can
validate any cloned repo:

  * stable_key(r)              — the ONE canonical re-sync-stable node identity (relpath::name:line),
                                 OS-normalized; freeze / gate / capture all import it (no drift),
  * relpath_for(py, base, pfx) — a PORTABLE forward-slash relpath of a source file under a repo root
                                 (+ optional prefix) so keys stay relpath::name:line for ANY clone,
  * load_queries(path)         — read a repo's query set from a .json array or a .txt (one per line),
  * a tiny ORACLE REGISTRY     — map a repo_id -> {repo_dir, oracle_path, queries_file, pinned_sha}
                                 so the gate can find any repo's frozen oracle by id.

No Neo4j, no ML stack, no product imports — pure stdlib, so it is unit-testable with no spend.
(`resolve_target` imports build/port_registry lazily, only for `--project`; every helper takes the
resolver / git runner as an injectable so the gates stay hermetic.)

  * resolve_target(...)        — WHICH Neo4j a live harness script talks to: an explicit --uri, a
                                 --project's OWN registered DeepMap instance, or an explicit NEO4J_URI —
                                 never an implicit bolt 7687 (on a real machine that is another project's
                                 database; the builder below wipes whatever it points at),
  * wipe_problem(...)          — the guard the DESTRUCTIVE builders (`MATCH (n) DETACH DELETE n`) apply
                                 before wiping: only an empty graph, a STAGE-0 harness graph, or the graph
                                 of the --project the operator named,
  * corpus / materialize_corpus — a frozen oracle is only meaningful against the corpus it was frozen on,
                                 so each registry entry pins its corpus to a COMMIT (`git archive <sha>`):
                                 editing build/ no longer silently invalidates the oracle's
                                 relpath::name:line keys (the 2026-09 finding: 19/50 and 13/50 keys stale,
                                 so the gate failed whatever the ranking did).
"""
from __future__ import annotations

import io
import json
import os
import re
import subprocess
import tarfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

# The canonical re-sync-stable key format (NOT graph-instance elementIds). Kept identical across the
# freeze / gate / capture producers by importing stable_key from HERE.
KEY_FORMAT = "relpath::name:line"

# Default on-disk locations (relative to the tests/ dir / repo root).
REGISTRY_RELPATH = "tests/oracles/registry.json"
ORACLE_DIR_RELPATH = "tests/oracles"


def stable_key(r: dict) -> str:
    """Re-sync-stable node identity: 'relpath::name:line'. Derived from source-stable fields
    (file/name/line), so it survives a DETACH-DELETE + re-MERGE; a graph elementId ('4:<uuid>:N')
    does not. The path separator is normalized to '/' so the key is OS-portable (a Windows
    backslash source_file and a POSIX slash one fold to the same key). This is THE shared helper —
    freeze, gate, and capture all import it so they can never drift apart."""
    f = str(r.get("file") or "").replace("\\", "/")
    return f"{f}::{r.get('name')}:{r.get('line')}"


def relpath_for(py_path, repo_dir, prefix: str = "") -> str:
    """A PORTABLE relpath string for a source file: forward-slash, relative to `repo_dir`, with an
    optional `prefix` prepended. This is what keeps keys `relpath::name:line` for ANY clone — the
    old builder computed relpaths against THIS repo, so an external clone produced absolute,
    non-portable keys. A file outside `repo_dir` falls back to its absolute forward-slash path
    (never crashes). `prefix` lets several repos share one graph without key collisions."""
    p = Path(py_path)
    base = Path(repo_dir)
    try:
        rel = p.resolve().relative_to(base.resolve())
        rel_str = rel.as_posix()
    except (ValueError, OSError):
        rel_str = p.as_posix()
    pfx = (prefix or "").strip().strip("/")
    return f"{pfx}/{rel_str}" if pfx else rel_str


def load_queries(path) -> List[str]:
    """Load a repo's query set. Accepts a `.json` array of strings, or a text file with one query
    per line (blank lines and `#`-comments ignored). Returns a list of non-empty query strings.
    Raises ValueError on a malformed JSON shape (never silently yields an empty set from bad input)."""
    p = Path(path)
    # utf-8-sig strips a leading BOM (editors add one): without it a BOM corrupts the first .txt query
    # ('﻿alpha') and makes json.loads reject the .json with "Unexpected UTF-8 BOM".
    text = p.read_text(encoding="utf-8-sig")
    if p.suffix.lower() == ".json":
        data = json.loads(text)
        if not isinstance(data, list) or not all(isinstance(x, str) for x in data):
            raise ValueError(f"{p}: a .json queries file must be a JSON array of strings")
        return [q.strip() for q in data if q and q.strip()]
    out: List[str] = []
    for line in text.splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            out.append(s)
    return out


# --------------------------------------------------------------------------- #
# Oracle registry — map a repo_id to its frozen oracle (so the gate finds any repo's oracle by id).
# Paths are stored RELATIVE to the repo root for portability; resolve_* joins them back.
# --------------------------------------------------------------------------- #

def empty_registry() -> Dict[str, Any]:
    return {"_meta": {"note": ("STAGE-0 oracle registry — maps a repo_id to its frozen "
                               "no-regression oracle so the gate can validate any cloned repo."),
                      "key_format": KEY_FORMAT},
            "oracles": {}}


def load_registry(path) -> Dict[str, Any]:
    """Load the registry, or an empty one if the file does not exist. Always returns the
    {_meta, oracles} shape (a malformed file is treated as empty rather than crashing the gate)."""
    p = Path(path)
    if not p.is_file():
        return empty_registry()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return empty_registry()
    if not isinstance(data, dict) or not isinstance(data.get("oracles"), dict):
        return empty_registry()
    data.setdefault("_meta", empty_registry()["_meta"])
    return data


def register_oracle(path, repo_id: str, *, repo_dir: str, oracle_path: str,
                    queries_file: Optional[str] = None, pinned_sha: Optional[str] = None,
                    note: Optional[str] = None,
                    corpus: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Add/replace `repo_id` in the registry at `path` and persist it. Stores the given (ideally
    repo-relative) paths verbatim; `corpus` (see make_corpus) pins what the oracle was frozen on. An
    existing entry's `note` / `corpus` are kept unless new ones are given. Returns the written dict."""
    if not repo_id or not repo_id.strip():
        raise ValueError("repo_id must be a non-empty string")
    reg = load_registry(path)
    prev = reg["oracles"].get(repo_id.strip()) or {}
    entry: Dict[str, Any] = {"repo_dir": repo_dir, "oracle_path": oracle_path,
                             "queries_file": queries_file, "pinned_sha": pinned_sha}
    note = note or prev.get("note")
    corpus = corpus or prev.get("corpus")
    if note:
        entry["note"] = note
    if corpus:
        entry["corpus"] = corpus
    reg["oracles"][repo_id.strip()] = entry
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(reg, indent=2, sort_keys=True), encoding="utf-8")
    return reg


def lookup_oracle(path, repo_id: str) -> Optional[Dict[str, Any]]:
    """Return the registry entry for `repo_id`, or None if absent."""
    return load_registry(path)["oracles"].get((repo_id or "").strip())


def resolve_oracle_path(entry: Dict[str, Any], repo_root) -> Path:
    """Resolve an entry's (repo-relative or absolute) `oracle_path` against the repo root."""
    op = Path(entry.get("oracle_path", ""))
    return op if op.is_absolute() else (Path(repo_root) / op)


def default_oracle_path(repo_id: str, repo_root) -> Path:
    """The conventional per-repo oracle location: <repo_root>/tests/oracles/<repo_id>.json."""
    safe = "".join(c if (c.isalnum() or c in "-_.") else "_" for c in (repo_id or "repo"))
    return Path(repo_root) / ORACLE_DIR_RELPATH / f"{safe}.json"


# --------------------------------------------------------------------------- #
# WHICH Neo4j — an explicit target, never an implicit bolt 7687.
# --------------------------------------------------------------------------- #

NO_TARGET_PROBLEM = (
    "no target Neo4j given: pass --project <dir> (that project's OWN DeepMap instance, looked up in the "
    "port registry) or --uri bolt://host:port. The STAGE-0 harness never defaults to bolt 7687 — on a "
    "real machine that is another project's database, and the graph builder WIPES its target. Get a "
    "throwaway instance with tests/stage0_refreeze.py (or `python build/provision.py ensure <dir>`).")

OWN_INSTANCE_SOURCES = ("registry", "instance-conf")


def _default_route_resolver(project):
    """port_registry.resolve_neo4j_route(project, honor_env=False) — the product's own D1 routing rule
    (imported lazily: the pure helpers above must load without build/ on the path)."""
    import sys
    build = Path(__file__).resolve().parent.parent / "build"
    if str(build) not in sys.path:
        sys.path.insert(0, str(build))
    import port_registry as PR   # noqa: E402
    return PR.resolve_neo4j_route(project, honor_env=False)


def resolve_target(uri: Optional[str] = None, project=None, env=None, *,
                   resolver: Optional[Callable] = None) -> Tuple[Optional[str], Optional[str], str]:
    """(uri, problem, how) for a live harness script. In order:
      1. --uri               -> used verbatim (an explicit operator choice);
      2. --project <dir>     -> that project's OWN registered DeepMap instance (registry pair, or its own
                                instance conf); a project with no instance of its own — only the legacy
                                base 7687, or nothing — is REFUSED, never guessed;
      3. an explicit NEO4J_URI in the environment;
      4. otherwise           -> no target: (None, NO_TARGET_PROBLEM, "none"). Never bolt 7687 by default."""
    if uri and str(uri).strip():
        return str(uri).strip(), None, "--uri"
    if project:
        try:
            route = (resolver or _default_route_resolver)(project)
        except Exception as exc:  # noqa: BLE001 — a resolver failure is a refusal, never a guess
            return None, f"could not resolve {project}'s DeepMap instance ({type(exc).__name__}: {exc})", "none"
        source = getattr(route, "source", None)
        if source in OWN_INSTANCE_SOURCES and getattr(route, "uri", None):
            return route.uri, None, f"--project ({source})"
        if source == "legacy-unverified":
            return None, (f"{project} has no DeepMap Neo4j instance of its own (it would only reach the "
                          f"legacy base {getattr(route, 'uri', 'bolt 7687')}, which is never a harness "
                          f"target) — give it one: python build/provision.py ensure {project}"), "none"
        return None, (getattr(route, "problem", None)
                      or f"{project} has no DeepMap Neo4j instance of its own"), "none"
    e = os.environ if env is None else env
    env_uri = (e.get("NEO4J_URI") or "").strip()
    if env_uri:
        return env_uri, None, "NEO4J_URI"
    return None, NO_TARGET_PROBLEM, "none"


def add_target_args(parser, *, destructive: bool = False) -> None:
    """--uri / --project (and, for a builder that wipes its target, --allow-wipe) on an argparse parser."""
    parser.add_argument("--uri", default=None,
                        help="the Neo4j to use (bolt://host:port). Never defaulted — see --project.")
    parser.add_argument("--project", default=None,
                        help="use THIS project's own DeepMap Neo4j instance (from the port registry).")
    if destructive:
        parser.add_argument("--allow-wipe", action="store_true",
                            help="wipe the target even when it holds a graph that is not a STAGE-0 harness "
                                 "graph or --project's own (DANGEROUS — for a throwaway instance only).")


HARNESS_MARKER_LABEL = "_Stage0Harness"   # a wipeable graph the STAGE-0 builder made (singleton node)


def _same_dir(a, b) -> bool:
    try:
        return os.path.normcase(str(Path(a).resolve())) == os.path.normcase(str(Path(b).resolve()))
    except Exception:
        return False


def wipe_problem(owner: Optional[str], has_nodes: Optional[bool], harness_marker: bool, *,
                 project=None, allow: bool = False, identity: Optional[Callable] = None) -> Optional[str]:
    """None when a DESTRUCTIVE harness build may wipe the target graph, else why not. Allowed: an EMPTY
    graph; a graph the STAGE-0 builder itself made (its _Stage0Harness marker) that no DeepMap project
    has claimed since; or the graph of the very --project the operator named (a throwaway instance they
    provisioned). Everything else — another project's graph, an unowned non-empty graph (a stranger's
    database), an unknown state — is refused unless allow=True. `identity` maps a project dir to the
    owner string sync stamps (port_registry.project_identity; injectable for the gates)."""
    if allow:
        return None
    if owner:
        if project is not None:
            try:
                ident = (identity or (lambda p: str(Path(p).resolve())))(project)
            except Exception:
                ident = None
            if ident and _same_dir(owner, ident):
                return None
        return (f"the target graph belongs to DeepMap project {owner} — refusing to wipe it. Point the "
                f"harness at a throwaway instance (--project <the throwaway dir>), or pass --allow-wipe.")
    if has_nodes is False or (has_nodes and harness_marker):
        return None
    if has_nodes is None:
        return "could not tell whether the target graph is empty — refusing to wipe it (--allow-wipe overrides)."
    return ("the target graph is NOT empty and was not built by the STAGE-0 harness (no owner, no harness "
            "marker) — refusing to wipe a database this harness did not make. Use a throwaway instance, "
            "or pass --allow-wipe.")


def read_graph_state(driver) -> Tuple[Optional[str], Optional[bool], bool]:
    """(owner, has_nodes, harness_marker) of the graph behind `driver` — what wipe_problem needs.
    owner = the :_DeepMapMeta owning_root; has_nodes = any node other than the two markers."""
    with driver.session() as s:
        owners = [r["o"] for r in s.run("MATCH (m:_DeepMapMeta) RETURN m.owning_root AS o LIMIT 1")]
        n = [r["c"] for r in s.run(f"MATCH (n) WHERE NOT n:_DeepMapMeta AND NOT n:{HARNESS_MARKER_LABEL} "
                                   "RETURN count(n) AS c")]
        marker = [r["c"] for r in s.run(f"MATCH (m:{HARNESS_MARKER_LABEL}) RETURN count(m) AS c")]
    return (owners[0] if owners and owners[0] else None, bool(n and n[0] > 0), bool(marker and marker[0] > 0))


# --------------------------------------------------------------------------- #
# A PINNED CORPUS per oracle — `git archive <sha>` materializes exactly what it was frozen on.
# --------------------------------------------------------------------------- #

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
BUILDERS = ("graphify-default-sync", "stage0-ast")


def make_corpus(sha: str, *, builder: str, tree: Optional[str] = None,
                paths: Optional[List[str]] = None) -> Dict[str, Any]:
    """The registry's `corpus` record: the COMMIT the oracle's graph is built from, what to take from it
    (`tree` = one subdirectory whose CONTENTS become the corpus root — keys relative to it; or `paths` =
    repo-relative paths kept at their place — keys relative to the repo root), and the builder that turns
    it into a graph ("graphify-default-sync" = the product's own default sync; "stage0-ast" =
    tests/stage0_real_graph.py)."""
    sha = (sha or "").strip().lower()
    if not _SHA_RE.match(sha):
        raise ValueError(f"corpus sha must be a full 40-hex commit, got {sha!r}")
    if builder not in BUILDERS:
        raise ValueError(f"builder must be one of {BUILDERS}, got {builder!r}")
    if bool(tree) == bool(paths):
        raise ValueError("give exactly one of tree= (a subdirectory as the corpus root) or paths=")
    return {"sha": sha, "builder": builder, "tree": tree or None, "paths": list(paths) if paths else None}


def corpus_archive_argv(repo_root, corpus: Dict[str, Any]) -> List[str]:
    """`git -C <repo> archive --format=tar <sha>[:<tree>] [-- <paths>...]` — PURE (constructs only)."""
    sha = corpus["sha"]
    treeish = f"{sha}:{corpus['tree']}" if corpus.get("tree") else sha
    argv = ["git", "-C", str(repo_root), "archive", "--format=tar", treeish]
    if corpus.get("paths"):
        argv += ["--", *corpus["paths"]]
    return argv


def materialize_corpus(repo_root, corpus: Dict[str, Any], dest, *, run: Optional[Callable] = None) -> int:
    """Extract the pinned corpus into `dest` (created; must be empty). Returns the number of files.
    Raises RuntimeError when git fails (unknown sha / path) — never a silently empty corpus. Archive
    members that would escape `dest` (absolute paths, `..`, links) are rejected."""
    dest = Path(dest)
    if dest.exists() and any(dest.iterdir()):
        raise RuntimeError(f"corpus destination {dest} is not empty")
    dest.mkdir(parents=True, exist_ok=True)
    runner = run or (lambda argv: subprocess.run(argv, capture_output=True, timeout=300))
    p = runner(corpus_archive_argv(repo_root, corpus))
    if getattr(p, "returncode", 1) != 0 or not getattr(p, "stdout", b""):
        err = getattr(p, "stderr", b"") or b""
        raise RuntimeError(f"git archive failed for corpus {corpus.get('sha')}: "
                           f"{err.decode('utf-8', 'replace').strip() or 'no output'}")
    n = 0
    root = dest.resolve()
    with tarfile.open(fileobj=io.BytesIO(p.stdout), mode="r:") as tf:
        for m in tf.getmembers():
            if m.issym() or m.islnk() or m.isdev():
                raise RuntimeError(f"corpus archive member {m.name!r} is a link/device — refusing")
            target = (root / m.name).resolve()
            if os.path.isabs(m.name) or ".." in Path(m.name).parts or not str(target).startswith(str(root)):
                raise RuntimeError(f"corpus archive member {m.name!r} escapes {dest} — refusing")
            if m.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif m.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                with tf.extractfile(m) as src:
                    target.write_bytes(src.read())
                n += 1
    return n


def graph_key_coverage(oracle: Dict[str, Any], present_keys) -> Tuple[int, int, List[str]]:
    """(present, total, missing_sample): how many of the oracle's frozen keys exist in the target graph.
    A graph built from the oracle's pinned corpus holds (nearly) all of them; a graph of some other
    corpus holds almost none — that is a WRONG GRAPH (config error), not an ordering regression."""
    keys = []
    for entry in (oracle.get("queries") or {}).values():
        for k in entry.get("order", []):
            if k not in keys:
                keys.append(str(k))
    present = set(present_keys)
    missing = [k for k in keys if k not in present]
    return len(keys) - len(missing), len(keys), missing[:5]
