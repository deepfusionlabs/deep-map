#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j gates for the STAGE-0 no-regression oracle HARNESS (T0/T4).

The live ordering gate (stage0_regression_check.py) needs a Neo4j instance, so its
own logic — the stable-key derivation, the two-shape oracle reader, the diff — and
the committed oracle's STRUCTURE were previously uncovered in CI. This suite locks
them with pure, no-Neo4j checks so a refactor can't silently:

  * re-introduce graph-instance elementIds into the committed oracle (the T0/T4 bug),
  * let the three stable_key() helpers (freeze / gate / capture) drift apart,
  * break the two-shape oracle reader, or
  * weaken the divergence diff.

None of these connect to Neo4j: the harness modules import the neo4j driver only
inside main(), so they load cleanly here for function-level testing.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parent
REPO = TESTS.parent
ORACLE = TESTS / "stage0_real_oracle.json"

# elementId form: "4:<uuid-with-dashes>:<n>" — the re-sync-fragile id the fix removed.
_ELEMENT_ID = re.compile(r"^\d+:[0-9a-fA-F]{8}-[0-9a-fA-F-]+:\d+$")
# stable-key form: "<relpath>::<name>:<line>" — has the '::' delimiter, ends in :<digits>.
_STABLE_KEY = re.compile(r"^.+::.+:\d+$")

_PASS = 0
_FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {label}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}{(' — ' + detail) if detail else ''}")


def _load(path: Path):
    spec = importlib.util.spec_from_file_location(f"dm_{path.stem}", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def gate_stable_key(GATE) -> None:
    print("\n-- stable_key: relpath::name:line, OS-normalized --")
    sk = GATE.stable_key
    check("posix path -> relpath::name:line",
          sk({"file": "build/retrieval.py", "name": "search", "line": 168})
          == "build/retrieval.py::search:168")
    check("windows backslashes normalized to '/'",
          sk({"file": "build\\retrieval.py", "name": "search", "line": 168})
          == "build/retrieval.py::search:168",
          "separator not normalized -> oracle would be OS-specific")
    check("qualified method name preserved",
          sk({"file": "build/retrieval.py", "name": "DeepMapRetriever.search", "line": 168})
          == "build/retrieval.py::DeepMapRetriever.search:168")
    check("same name, different line -> distinct keys (module vs fn)",
          sk({"file": "build/sync.py", "name": "sync", "line": 1})
          != sk({"file": "build/sync.py", "name": "sync", "line": 247}))
    check("missing file does not crash",
          isinstance(sk({"name": "x", "line": 1}), str))
    check("a stable_key is NOT an elementId",
          not _ELEMENT_ID.match(sk({"file": "a.py", "name": "f", "line": 9})))


def gate_helper_consistency() -> None:
    """The three duplicated stable_key() helpers MUST agree byte-for-byte — else the
    freeze writes keys the gate can't match. This is the guard against silent drift."""
    print("\n-- stable_key identical across freeze / gate / capture --")
    mods = {
        "freeze": _load(TESTS / "stage0_freeze_oracle.py"),
        "gate": _load(TESTS / "stage0_regression_check.py"),
        "capture": _load(TESTS / "stage0_capture_oracle.py"),
    }
    rows = [
        {"file": "build\\retrieval.py", "name": "DeepMapRetriever.search", "line": 168},
        {"file": "tests/run_all.py", "name": "main", "line": 68},
        {"name": "x", "line": 1},
    ]
    for row in rows:
        outs = {who: m.stable_key(dict(row)) for who, m in mods.items()}
        check(f"agree on {row.get('name')!r}: {outs['gate']}",
              len(set(outs.values())) == 1, f"DRIFT: {outs}")


def gate_two_shape_reader(GATE) -> None:
    print("\n-- _entry_query_and_order: tolerates both producer shapes --")
    f = GATE._entry_query_and_order
    # freeze shape: keyed by query TEXT, no 'query' field
    q, order = f("compute rrf", {"order": ["a.py::f:1", "b.py::g:2"], "named": []})
    check("freeze shape: key IS the query text", q == "compute rrf")
    check("freeze shape: order passes through", order == ["a.py::f:1", "b.py::g:2"])
    # capture shape: keyed by qid, explicit 'query' field
    q, order = f("Q1", {"query": "compute rrf", "order": ["a.py::f:1"]})
    check("capture shape: 'query' field wins over the key", q == "compute rrf")
    check("capture shape: order passes through", order == ["a.py::f:1"])
    # order coerced to str
    _, order = f("Q", {"order": [123]})
    check("order entries coerced to str", order == ["123"])


def gate_canonical_sequence(GATE) -> None:
    print("\n-- _canonical_sequence: stable keys, in order, top_k-sliced --")
    rows = [
        {"file": "a.py", "name": "f", "line": 1, "score": 0.9, "id": "4:uuid:0"},
        {"file": "b.py", "name": "g", "line": 2, "score": 0.5, "id": "4:uuid:1"},
        {"file": "c.py", "name": "h", "line": 3, "score": 0.1, "id": "4:uuid:2"},
    ]
    seq = GATE._canonical_sequence(rows, 2)
    check("respects top_k slice", seq == ["a.py::f:1", "b.py::g:2"])
    # Discriminating: the rows carry decoy elementIds ("4:uuid:N"); if _canonical_sequence
    # returned r["id"] instead of stable_key(r), this would catch it (seq would be the 4:uuid ids).
    check("returns stable keys, NOT the decoy elementIds",
          all(not s.startswith("4:") for s in seq) and "4:uuid:0" not in seq, str(seq))
    check("preserves returned order", GATE._canonical_sequence(rows, 3)[-1] == "c.py::h:3")


def gate_diff(GATE) -> None:
    print("\n-- _diff: marks divergence --")
    same = GATE._diff("q", ["a.py::f:1"], ["a.py::f:1"])
    check("identical sequences -> no DIVERGES marker", "DIVERGES" not in same)
    diff = GATE._diff("q", ["a.py::f:1"], ["a.py::f:2"])
    check("differing sequences -> DIVERGES marker", "DIVERGES" in diff)


def gate_committed_oracle() -> None:
    print("\n-- committed oracle: stable-key shape, NO elementIds --")
    check("oracle file exists", ORACLE.is_file(), str(ORACLE))
    if not ORACLE.is_file():
        return
    try:
        data = json.loads(ORACLE.read_text(encoding="utf-8"))
    except Exception as exc:
        check("oracle is valid JSON", False, str(exc))
        return
    check("oracle is valid JSON", True)

    meta = data.get("_meta", {})
    check("_meta.key_format == 'relpath::name:line'",
          meta.get("key_format") == "relpath::name:line",
          f"got {meta.get('key_format')!r}")
    top_k = meta.get("top_k")
    check("_meta.top_k is a positive int", isinstance(top_k, int) and top_k > 0)

    queries = data.get("queries", {})
    check("oracle has at least one query", len(queries) > 0)

    bad_elem, bad_shape, over_k = [], [], []
    for qkey, entry in queries.items():
        order = entry.get("order", [])
        if not order:
            bad_shape.append(f"{qkey}: empty order")
        if top_k and len(order) > top_k:
            over_k.append(f"{qkey}: {len(order)} > top_k {top_k}")
        for k in order:
            if _ELEMENT_ID.match(str(k)):
                bad_elem.append(k)
            elif not _STABLE_KEY.match(str(k)):
                bad_shape.append(f"{qkey}: {k!r}")
    check("NO elementId-form keys remain (the T0/T4 fix)", not bad_elem,
          f"{len(bad_elem)} elementId(s): {bad_elem[:2]}")
    check("every order key matches relpath::name:line", not bad_shape,
          "; ".join(bad_shape[:3]))
    check("no order longer than top_k", not over_k, "; ".join(over_k[:3]))


# --------------------------------------------------------------------------------------------------- #
# ROUND-3 W8: the harness targets an EXPLICIT instance (never an implicit bolt 7687), the destructive
# builder refuses to wipe a graph it did not make, and every oracle is pinned to the COMMIT it was
# frozen on (the 2026-09 live run found both oracles stale — 19/50 and 13/50 keys still valid — so the
# gate failed whatever the ranking did, and its default target was another project's Docker Neo4j).
# --------------------------------------------------------------------------------------------------- #
LIVE_SCRIPTS = ("stage0_regression_check.py", "stage0_freeze_oracle.py", "stage0_real_graph.py",
                "stage0_live_smoketest.py")


class _Route:
    def __init__(self, uri, source, problem=None):
        self.uri, self.source, self.problem = uri, source, problem


def gate_resolve_target(P) -> None:
    print("\n-- resolve_target: --uri / --project's OWN instance / explicit NEO4J_URI — never an implicit 7687 --")
    fn = getattr(P, "resolve_target", None)
    check("stage0_portable.resolve_target exists", callable(fn))
    if not callable(fn):
        return
    uri, problem, how = fn(env={})
    check("no --uri / --project / NEO4J_URI -> NO target (None) + a problem naming --project/--uri",
          uri is None and problem and "--project" in problem and "--uri" in problem, f"{uri!r} {problem!r}")
    check("...and the problem never offers bolt 7687 as the answer", "7687" not in str(uri))
    check("--uri wins verbatim", fn(uri="bolt://localhost:7801", project="x", env={"NEO4J_URI": "bolt://e:1"})[0]
          == "bolt://localhost:7801")
    reg = lambda p: _Route("bolt://localhost:7695", "registry")               # noqa: E731
    check("--project with its own registered instance -> that bolt URI",
          fn(project="proj", env={}, resolver=reg)[:2] == ("bolt://localhost:7695", None))
    conf = lambda p: _Route("bolt://localhost:7697", "instance-conf")         # noqa: E731
    check("--project whose registry entry was lost but its own instance conf remains -> used",
          fn(project="proj", env={}, resolver=conf)[0] == "bolt://localhost:7697")
    legacy = lambda p: _Route("bolt://localhost:7687", "legacy-unverified")   # noqa: E731
    u, prob, _ = fn(project="proj", env={}, resolver=legacy)
    check("--project with NO instance of its own (legacy base 7687 only) -> REFUSED, not 7687",
          u is None and prob and "no DeepMap Neo4j instance of its own" in prob, f"{u!r} {prob!r}")
    none = lambda p: _Route(None, "none", "bolt 7687 is project X's instance, not this one's")  # noqa: E731
    u, prob, _ = fn(project="proj", env={}, resolver=none)
    check("--project with no route -> REFUSED with the resolver's own reason",
          u is None and "project X" in (prob or ""), f"{u!r} {prob!r}")
    boom = lambda p: (_ for _ in ()).throw(RuntimeError("registry unreadable"))  # noqa: E731
    u, prob, _ = fn(project="proj", env={}, resolver=boom)
    check("a resolver error is a refusal (never a guessed URI)", u is None and "registry unreadable" in (prob or ""))
    check("--project beats an exported NEO4J_URI (the CLI flag is the explicit choice)",
          fn(project="proj", env={"NEO4J_URI": "bolt://elsewhere:1"}, resolver=reg)[0] == "bolt://localhost:7695")
    check("an explicitly exported NEO4J_URI is honoured when nothing else is given",
          fn(env={"NEO4J_URI": "bolt://localhost:7699"})[0] == "bolt://localhost:7699")


def gate_live_scripts_no_implicit_default() -> None:
    print("\n-- the live harness scripts: no hard-coded bolt 7687 default; they resolve an explicit target --")
    for name in LIVE_SCRIPTS:
        src = (TESTS / name).read_text(encoding="utf-8")
        check(f"{name}: no `NEO4J_URI` default of bolt://localhost:7687",
              re.search(r"NEO4J_URI[\"']\s*,\s*[\"']bolt://localhost:7687", src) is None)
        check(f"{name}: resolves its target through stage0_portable.resolve_target",
              "resolve_target(" in src)
    # Behavioural: with no target the scripts exit 2 BEFORE loading the model or touching Neo4j. The child
    # runs behind a SOCKET TRIPWIRE (a sitecustomize that refuses every connect): a regressed script that
    # defaults to 7687 again must fail this gate WITHOUT ever reaching a real database — the builder
    # wipes its target, and on a developer box 7687 is typically another project's Neo4j.
    import os
    import subprocess
    import tempfile
    tripwire = (
        "import socket\n"
        "def _refuse(*a, **k):\n"
        "    raise ConnectionRefusedError('stage0 gate tripwire: network connections are disabled')\n"
        "socket.socket.connect = _refuse\n"
        "socket.socket.connect_ex = lambda *a, **k: 111\n"
        "socket.create_connection = _refuse\n")
    with tempfile.TemporaryDirectory() as tw:
        (Path(tw) / "sitecustomize.py").write_text(tripwire, encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if k not in ("NEO4J_URI",)}
        env["PYTHONUTF8"] = "1"
        env["HF_HUB_OFFLINE"] = "1"
        env["PYTHONPATH"] = tw + os.pathsep + env.get("PYTHONPATH", "")
        probe = subprocess.run([sys.executable, "-c", "import socket\ntry:\n socket.create_connection(("
                                "'127.0.0.1', 9), 1)\nexcept ConnectionRefusedError as e:\n print('TRIPWIRE', e)"],
                               env=env, capture_output=True, text=True, timeout=60)
        check("the socket tripwire is active in the child (no real connection possible)",
              "TRIPWIRE" in probe.stdout, (probe.stdout + probe.stderr)[-200:])
        for name, argv in (("stage0_regression_check.py", []), ("stage0_real_graph.py", []),
                           ("stage0_freeze_oracle.py", []), ("stage0_live_smoketest.py", [])):
            try:
                p = subprocess.run([sys.executable, str(TESTS / name), *argv], cwd=str(REPO), env=env,
                                   capture_output=True, text=True, encoding="utf-8", errors="replace",
                                   timeout=180)
            except subprocess.TimeoutExpired:
                check(f"{name} with no target exits fast", False, "timed out (did it load the model?)")
                continue
            out = (p.stdout or "") + (p.stderr or "")
            check(f"{name} with no --uri/--project/NEO4J_URI -> exit 2 + the no-target reason (nothing touched)",
                  p.returncode == 2 and "--project" in out and "never defaults to bolt 7687" in out,
                  f"rc={p.returncode} tail={out.strip()[-300:]!r}")


def gate_wipe_guard(P) -> None:
    print("\n-- wipe_problem: the destructive builder wipes only an empty / harness / named-project graph --")
    fn = getattr(P, "wipe_problem", None)
    check("stage0_portable.wipe_problem exists", callable(fn))
    if not callable(fn):
        return
    ident = lambda p: str(p)   # noqa: E731
    check("empty graph -> may wipe", fn(None, False, False) is None)
    check("a STAGE-0 harness graph (marker, no owner) -> may wipe", fn(None, True, True) is None)
    check("an unowned NON-empty graph without the harness marker (a stranger's database) -> REFUSED",
          fn(None, True, False) is not None)
    check("another DeepMap project's graph -> REFUSED (names the owner)",
          "C:/proj/other" in (fn("C:/proj/other", True, False, project="C:/proj/mine", identity=ident) or ""))
    check("a DeepMap graph owned by a project, no --project given -> REFUSED",
          fn("C:/proj/mine", True, False) is not None)
    with __import__("tempfile").TemporaryDirectory() as td:
        check("the graph of the very --project the operator named -> may wipe",
              fn(td, True, False, project=td) is None)
    check("unknown state (could not count) -> REFUSED", fn(None, None, False) is not None)
    check("--allow-wipe overrides every refusal", fn("C:/proj/other", True, False, allow=True) is None)


def _git(cwd, *args):
    import subprocess
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=60)


def gate_corpus(P) -> None:
    print("\n-- pinned corpus: make_corpus / git-archive materialization (a real temp git repo) --")
    import shutil
    import tempfile
    mk = getattr(P, "make_corpus", None)
    mat = getattr(P, "materialize_corpus", None)
    check("make_corpus + materialize_corpus exist", callable(mk) and callable(mat))
    if not (callable(mk) and callable(mat)):
        return
    for bad, kw in (("abc", {"builder": "stage0-ast", "tree": "build"}),
                    ("a" * 40, {"builder": "nope", "tree": "build"}),
                    ("a" * 40, {"builder": "stage0-ast"}),
                    ("a" * 40, {"builder": "stage0-ast", "tree": "build", "paths": ["x"]})):
        try:
            mk(bad, **kw)
            ok = False
        except ValueError:
            ok = True
        check(f"make_corpus rejects {bad[:6]!r} {kw}", ok)
    if not shutil.which("git"):
        print("  [skip] git not on PATH — materialization not exercised")
        return
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td) / "r"
        (repo / "build").mkdir(parents=True)
        (repo / "tests").mkdir()
        (repo / "build" / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        (repo / "tests" / "t.py").write_text("def g():\n    return 2\n", encoding="utf-8")
        _git(repo, "init", "-q")
        _git(repo, "add", "-A")
        _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "c1")
        sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
        (repo / "build" / "mod.py").write_text("# edited after the pin\n\ndef f():\n    return 9\n", encoding="utf-8")
        c_tree = mk(sha, builder="graphify-default-sync", tree="build")
        argv = P.corpus_archive_argv(repo, c_tree)
        check("archive argv = git archive --format=tar <sha>:<tree>", argv[-1] == f"{sha}:build" and
              argv[:2] == ["git", "-C"] and "archive" in argv, str(argv))
        d1 = Path(td) / "c1"
        n = mat(repo, c_tree, d1)
        check("tree corpus: the subdirectory's CONTENTS become the root (keys relative to it)",
              n == 1 and (d1 / "mod.py").is_file() and not (d1 / "build").exists(), str(sorted(d1.rglob("*"))))
        check("the corpus is the PINNED commit, not the edited work tree",
              (d1 / "mod.py").read_text(encoding="utf-8").startswith("def f():"))
        d2 = Path(td) / "c2"
        n2 = mat(repo, mk(sha, builder="stage0-ast", paths=["build", "tests"]), d2)
        check("paths corpus: files keep their repo-relative place",
              n2 == 2 and (d2 / "build" / "mod.py").is_file() and (d2 / "tests" / "t.py").is_file())
        try:
            mat(repo, mk("f" * 40, builder="stage0-ast", tree="build"), Path(td) / "c3")
            raised = False
        except RuntimeError:
            raised = True
        check("an unknown sha -> RuntimeError (never a silently empty corpus)", raised)
        try:
            mat(repo, c_tree, d1)
            raised = False
        except RuntimeError:
            raised = True
        check("a non-empty destination -> RuntimeError", raised)


def gate_key_coverage(P) -> None:
    print("\n-- graph_key_coverage: a WRONG graph (other corpus) is told apart from an ordering regression --")
    fn = getattr(P, "graph_key_coverage", None)
    check("graph_key_coverage exists", callable(fn))
    if not callable(fn):
        return
    oracle = {"queries": {"q1": {"order": ["a.py::f:1", "b.py::g:2"]}, "q2": {"order": ["a.py::f:1", "c.py::h:3"]}}}
    check("all keys present -> (3, 3, [])", fn(oracle, {"a.py::f:1", "b.py::g:2", "c.py::h:3", "x"}) == (3, 3, []))
    got = fn(oracle, {"a.py::f:1"})
    check("missing keys counted once + sampled", got[:2] == (1, 3) and got[2] == ["b.py::g:2", "c.py::h:3"], str(got))


def _corpus_key_ok(lines, name: str, line: int) -> bool:
    if line == 1:
        return True
    short = name.split(".")[-1]
    window = lines[max(0, line - 1): line + 5]
    return any(re.search(rf"\b(def|class)\s+{re.escape(short)}\b", ln) for ln in window)


# The root commit of the history the committed oracles were frozen in (the private development repository). A
# checkout whose history has a different root (the public repository, published from a curated copy) cannot hold
# the pinned corpus commit, so the corpus key check is skipped there; inside this history a missing pin still FAILs.
FROZEN_HISTORY_ROOT = "4a5135337be2595f59b4cc5987683fe0b843d46b"


def _history_roots() -> set | None:
    """The root commits of this checkout's HEAD history, or None when git cannot list them."""
    got = _git(REPO, "rev-list", "--max-parents=0", "HEAD")
    if got.returncode != 0:
        return None
    return {ln.strip() for ln in got.stdout.splitlines() if ln.strip()}


def gate_committed_oracles_pinned(P) -> None:
    print("\n-- committed oracles: each is PINNED to a commit, and every frozen key exists in that corpus --")
    import shutil
    reg = P.load_registry(REPO / P.REGISTRY_RELPATH)
    entries = reg.get("oracles", {})
    check("the registry lists the self + self-graphify-build oracles", {"self", "self-graphify-build"} <= set(entries),
          str(sorted(entries)))
    git_ok = bool(shutil.which("git")) and (REPO / ".git").exists()
    if git_ok and _git(REPO, "cat-file", "-e", f"{FROZEN_HISTORY_ROOT}^{{commit}}").returncode == 0:
        roots = _history_roots()
        check(f"FROZEN_HISTORY_ROOT {FROZEN_HISTORY_ROOT[:12]} is a root commit of this history (the other-history "
              "skip below keys on it)", roots is not None and FROZEN_HISTORY_ROOT in roots, str(sorted(roots or ())))
    for rid, entry in sorted(entries.items()):
        corpus = entry.get("corpus") or {}
        check(f"[{rid}] has a pinned corpus {{sha, builder, tree|paths}}",
              bool(corpus.get("sha")) and corpus.get("builder") in getattr(P, "BUILDERS", ())
              and bool(corpus.get("tree")) != bool(corpus.get("paths")), str(corpus))
        check(f"[{rid}] pinned_sha == corpus.sha", entry.get("pinned_sha") == corpus.get("sha"))
        opath = P.resolve_oracle_path(entry, REPO)
        try:
            oracle = json.loads(Path(opath).read_text(encoding="utf-8"))
        except Exception as exc:
            check(f"[{rid}] oracle file readable", False, f"{opath}: {exc}")
            continue
        check(f"[{rid}] the oracle's _meta.corpus == the registry's corpus",
              (oracle.get("_meta") or {}).get("corpus") == corpus, str((oracle.get("_meta") or {}).get("corpus")))
        if not (git_ok and corpus.get("sha")):
            print(f"  [skip] [{rid}] corpus key check ({'no pinned corpus' if git_ok else 'no git checkout here'})")
            continue
        if Path(str(entry.get("repo_dir") or ".")).is_absolute():
            print(f"  [skip] [{rid}] corpus key check (an external clone's corpus — not in this repo's history)")
            continue
        if _git(REPO, "cat-file", "-e", f"{corpus['sha']}^{{commit}}").returncode != 0:
            if _git(REPO, "rev-parse", "--is-shallow-repository").stdout.strip() == "true":
                print(f"  [skip] [{rid}] pinned commit {corpus['sha'][:12]} is outside this SHALLOW clone's history "
                      "(CI checkout depth 1) — the corpus key check needs the full history")
                continue
            roots = _history_roots()
            if roots and FROZEN_HISTORY_ROOT not in roots:
                print(f"  [skip] [{rid}] pinned commit {corpus['sha'][:12]} belongs to the history the oracles were "
                      f"frozen in (root {FROZEN_HISTORY_ROOT[:12]}); this checkout has another history (root "
                      f"{sorted(roots)[0][:12]}), so the corpus key check cannot run here. Re-freeze the oracles "
                      "at a commit of this history to enable it.")
                continue
            check(f"[{rid}] pinned commit {corpus['sha'][:12]} exists in this repo's history", False)
            continue
        prefix = (corpus.get("tree").strip("/") + "/") if corpus.get("tree") else ""
        cache, total, bad = {}, 0, []
        for entry_q in (oracle.get("queries") or {}).values():
            for k in entry_q.get("order", []):
                f, rest = str(k).split("::", 1)
                name, ln = rest.rsplit(":", 1)
                path = prefix + f
                if path not in cache:
                    shown = _git(REPO, "show", f"{corpus['sha']}:{path}")
                    cache[path] = shown.stdout.splitlines() if shown.returncode == 0 else None
                total += 1
                lines = cache[path]
                if lines is None or not _corpus_key_ok(lines, name, int(ln)):
                    bad.append(k)
        check(f"[{rid}] every frozen key exists at its line in the pinned corpus ({total - len(bad)}/{total})",
              total > 0 and not bad, f"stale: {bad[:4]}")


def gate_refreeze_plan() -> None:
    print("\n-- stage0_refreeze: a throwaway instance, the pinned corpus, and ALWAYS a full cleanup --")
    import tempfile
    try:
        RF = _load(TESTS / "stage0_refreeze.py")
    except Exception as exc:
        check("tests/stage0_refreeze.py loads", False, repr(exc))
        return
    check("check_work_dir: a relative path is refused", RF.check_work_dir(Path("rel/dir")) is not None)
    check("check_work_dir: a dir INSIDE this repo is refused", RF.check_work_dir(REPO / "tmp_s0") is not None)
    check("check_work_dir: a dir CONTAINING this repo is refused", RF.check_work_dir(REPO.parent) is not None)
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / "junk.txt").write_text("x", encoding="utf-8")
        check("check_work_dir: a non-empty dir is refused", RF.check_work_dir(Path(td)) is not None)
        fresh = Path(td) / "fresh"
        check("check_work_dir: a fresh absolute dir outside the repo is accepted", RF.check_work_dir(fresh) is None,
              str(RF.check_work_dir(fresh)))
    env = RF.child_env({"NEO4J_URI": "bolt://localhost:7687", "DEEPMAP_EXTRACTOR": "ast",
                        "DEEPMAP_SELF_CALL_REBIND": "0", "DEEPMAP_HOME": "/h", "PATH": "/bin"})
    check("child_env drops NEO4J_URI (the escape hatch would disable per-project instancing)",
          "NEO4J_URI" not in env)
    check("child_env pins PYTHONHASHSEED=0 (set/dict order cannot perturb a rebuild)",
          env.get("PYTHONHASHSEED") == "0")
    check("child_env drops every DEEPMAP_* knob except the home (the DEFAULT pipeline) + keeps PATH",
          "DEEPMAP_EXTRACTOR" not in env and "DEEPMAP_SELF_CALL_REBIND" not in env and env.get("DEEPMAP_HOME") == "/h"
          and env.get("PATH") == "/bin", str(sorted(env)))
    work = Path(tempfile.gettempdir()) / "s0plan"
    corpora = {"self-graphify-build": {"sha": "a" * 40, "builder": "graphify-default-sync", "tree": "build",
                                       "paths": None},
               "self": {"sha": "a" * 40, "builder": "stage0-ast", "tree": None, "paths": ["build", "scripts", "tests"]}}
    for mode in ("check", "freeze"):
        steps = RF.plan(work, ["self", "self-graphify-build"], mode, corpora, key="abc123", py="python")
        argvs = [s["argv"] for s in steps if "argv" in s]
        flat = [" ".join(a) for a in argvs]
        proj = str(work / "proj")
        neo_steps = [a for a in argvs if any(x.endswith(("stage0_regression_check.py", "stage0_freeze_oracle.py",
                                                         "stage0_real_graph.py")) for x in a)]
        check(f"[{mode}] every Neo4j-touching harness step targets --project <the throwaway> (never --uri)",
              neo_steps and all("--project" in a and a[a.index("--project") + 1] == proj and "--uri" not in a
                                for a in neo_steps), str(neo_steps))
        check(f"[{mode}] nothing names bolt 7687 and nothing passes --allow-wipe",
              not any("7687" in f or "--allow-wipe" in f for f in flat))
        names = [s["name"] for s in steps]
        tail = [s for s in steps if s.get("cleanup")]
        check(f"[{mode}] the plan ENDS with the cleanup: stop -> instance-rm --key <key> --purge --yes -> rmtree",
              steps[-len(tail):] == tail and len(tail) == 3 and tail[0]["argv"][2] == "stop"
              and tail[1]["argv"][2:] == ["instance-rm", "--key", "abc123", "--purge", "--yes"]
              and tail[2].get("action") == "rmtree", str(names[-3:]))
        check(f"[{mode}] a NON-base pair is pre-allocated + the route verified before any build",
              names.index("pre-allocate a NON-base Neo4j pair") < names.index("provision the throwaway instance")
              < names.index("verify the route is the throwaway's own instance")
              < min(i for i, n in enumerate(names) if n.endswith(("product default sync", "harness ast graph"))))
        check(f"[{mode}] the product sync (it stamps the owner) runs BEFORE the ast wipe",
              names.index("self-graphify-build: product default sync") < names.index("self: harness ast graph"))
        sync_step = next(s for s in steps if s["name"] == "self-graphify-build: product default sync")
        check(f"[{mode}] the UNCHANGED product sync runs with Python's random SEEDED (defence in depth — "
              "F5 made the embedding order corpus-derived; any other global-RNG use would reorder near-ties)",
              sync_step["argv"][1] == "-c" and "random.seed(0)" in sync_step["argv"][2]
              and sync_step["argv"][3].endswith("deepmap_cli.py") and sync_step["argv"][4:] == ["sync", proj],
              str(sync_step["argv"][:5]))
        ast_step = next(s for s in steps if s["name"] == "self: harness ast graph")
        check(f"[{mode}] the ast build refuses the STUB encoder (--require-real-embeddings)",
              "--require-real-embeddings" in ast_step["argv"])
        if mode == "check":
            check("[check] never registers / re-freezes (it gates the committed oracles)",
                  not any("--register" in a or a[1].endswith("stage0_freeze_oracle.py") for a in argvs))
        else:
            fr = [a for a in argvs if a[1].endswith("stage0_freeze_oracle.py")]
            check("[freeze] records the pinned corpus (sha + builder + tree|paths) and registers it",
                  len(fr) == 2 and all("--corpus-sha" in a and "--builder" in a and "--register" in a for a in fr)
                  and any("--corpus-tree" in a for a in fr) and any("--corpus-paths" in a for a in fr))


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 64)
    print("DeepMap — STAGE-0 oracle harness gates (no Neo4j)")
    print("=" * 64)

    GATE = _load(TESTS / "stage0_regression_check.py")
    P = _load(TESTS / "stage0_portable.py")

    gate_stable_key(GATE)
    gate_helper_consistency()
    gate_two_shape_reader(GATE)
    gate_canonical_sequence(GATE)
    gate_diff(GATE)
    gate_committed_oracle()
    gate_resolve_target(P)
    gate_live_scripts_no_implicit_default()
    gate_wipe_guard(P)
    gate_corpus(P)
    gate_key_coverage(P)
    gate_refreeze_plan()
    gate_committed_oracles_pinned(P)

    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
