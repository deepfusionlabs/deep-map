#!/usr/bin/env python3
"""0.28.3 (F4) — run_breadth.sh resumes like run_g3_n4.sh: only a capture of THIS DeepMap's graph is reused.

Findings (fix-all round 2, F4 — O11's round-1 residual and its review):
  1. run_breadth.sh resumed by run-id alone (the defect class of ISS-benchmark:005): a `breadth-<repo>` whose local
     results.json said `measured` was skipped, and in loop mode ANY record.json skipped the repo — whatever graph,
     DeepMap version or engine it measured (the July-era breadth-*/depthn4-* results included). It now keys run-ids by
     RUN_TAG (default v<DeepMap version>) and asks run_benchmark.resume_state_for, the G3 driver's classifier: a stale
     capture stops the driver, an unfinished one is captured again.
  2. After a capture that exited non-zero (a fail-closed abort part-way) the driver still scored it and merged it, so a
     partial capture could be reported as measured. Now it is neither scored nor merged, and the driver exits 1.
  3. The O11 review: its documented final-mode usage (`MODE=final CLAUDE_MODELS="claude-sonnet-5"`) could never be
     scored — the default `sonnet` judge may BE the Sonnet 5 arm, and --score refuses that — and nothing let the operator
     name another judge. JUDGE_MODEL now reaches every --score, and final mode checks the judge BEFORE any capture.

Findings (fix-all round 3, completeness critic):
  4. cov-bench[2] — the resume checked the graph, not the capture's SHAPE: the two documented usage lines, run in
     order, made the final launch (Opus + claude-sonnet-5, n=2) find the loop capture (Opus only, n=1) at the same
     run-id; it only scored and merged it and exited 0, the requested arm and runs never captured. Each MODE now has
     its own default PREFIX (loop: breadth-loop, final: breadth), the driver records each capture's shape next to it
     (breadth_shape.json: runs per arm, extra Claude arms, family filter — exact, whatever runs timed out), and a
     capture of another shape (or with none recorded, or whose results.json contradicts it) stops the driver.
  5. cov-bench[7] — (a) MODE / PREFIX / RUN_TAG were checked after the driver log opened, so PREFIX=../x wrote a log
     outside benchmark/results before dying (RUNS is now checked there too: RUNS=0 silently ran n=4); (b) a capture
     moved aside kept answer paths naming the ORIGINAL run-id, so completeness_only.py / --score on the archive read
     the new capture's answers: they are re-pointed at the archive; (c) the `questions: []` fixture needed PyYAML
     (run_benchmark's stdlib fallback cannot read it): the fixture is a bare `[]`, and a scenario runs the driver with
     PyYAML hidden.

Locks: the REAL driver runs under bash in a throwaway tree (hermetic): `claude` is a no-op shim; benchmark/
clone_graph.py and benchmark/run_benchmark.py are sandbox stand-ins that RECORD every call — the run_benchmark stand-in
loads the REAL module under test for the driver's classifiers (resume_state_for, deepmap_version, the judge check) and
only emulates a capture / --score / --merge (no claude, Neo4j, judge or spend). Each scenario asserts what was built,
captured, scored and merged, the run-ids, and the exit code. Plus static checks (bash -n, shellcheck when present).
The Sandbox takes the driver's name: tests/o11_g3_driver_resume_r283_gates.py (section F) runs run_g3_n4.sh in it, so
a change here must keep that driver runnable too.

Usage: python tests/f4_breadth_resume_r283_gates.py [--root <tree>]
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True     # never drop __pycache__ into a --root snapshot

REPO = Path(__file__).resolve().parent.parent
PASSES: list[str] = []
FAILS: list[str] = []
VERSION = "0.28.9"                 # the sandbox's DeepMap version (the stand-in's deepmap_version)
TAG = f"v{VERSION}"
REPOS = {"o/alpha": "a" * 40, "o/beta": "b" * 40}
FINAL_PREFIX, LOOP_PREFIX = "breadth", "breadth-loop"   # each MODE's default run-id prefix
SHAPE_FILE = "breadth_shape.json"  # the capture shape the driver records next to each capture
RUN_TIMEOUT_S = 240                # one sandboxed driver run (a few seconds; ~15 s on a loaded Windows box)
ENV_DROP = ("REPOS", "MODE", "FAMILY", "RUNS", "CLAUDE_MODELS", "JUDGE_MODEL", "PREFIX", "RUN_TAG", "CLONES",
            "TIMEOUT_S", "KEEP_RUNNING", "SKIP_PROBE", "DO_CLONE", "DRIVER_LOG", "DEEPMAP_JUDGE_TRANSPORT",
            "DEEPMAP_JUDGE_MODEL", "DEEPMAP_SEARCH_PORT", "DEEPMAP_EMBED_PORT", "NEO4J_URI", "F4_CAPTURE_RC",
            "F4_SCORE_RC", "F4_BUILD_RC")

STUB_RUN_BENCHMARK = r'''"""Sandbox stand-in for benchmark/run_benchmark.py (tests/f4_breadth_resume_r283_gates.py). Imported: the REAL module
under test ($F4_REAL_RB) pointed at this sandbox. Run as a script: the call is recorded in $F4_CALLS and emulated."""
import importlib.util, json, os, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
_VERSION = os.environ.get("F4_VERSION") or None
_METRIC_KEYS = ("completeness_pct", "correctness_pct", "wrong_high", "tool_calls", "tokens_in", "tokens_out",
                "wall_clock_s", "cost_usd")


def _load_real():
    spec = importlib.util.spec_from_file_location("f4_real_run_benchmark", os.environ["F4_REAL_RB"])
    real = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(real)
    real.RESULTS_ROOT = HERE / "results"
    real.REPOS_YAML = HERE / "repos.yaml"
    real.QUESTIONS_YAML = HERE / "questions.yaml"
    real.deepmap_version = lambda root=None: _VERSION
    return real


def _opt(argv, flag):
    return argv[argv.index(flag) + 1] if flag in argv and argv.index(flag) + 1 < len(argv) else None


def _write(rid, rec):
    d = HERE / "results" / rid
    d.mkdir(parents=True, exist_ok=True)
    (d / "results.json").write_text(json.dumps(rec, indent=1), encoding="utf-8")


def _read(rid):
    return json.loads((HERE / "results" / rid / "results.json").read_text(encoding="utf-8"))


def _emulate(argv):
    with open(os.environ["F4_CALLS"], "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"prog": "run_benchmark", "argv": argv}) + "\n")
    if "--probe" in argv:
        return 0
    if "--repo" in argv:                        # a capture: never a real claude run
        repo, rid, clones = _opt(argv, "--repo"), _opt(argv, "--run-id"), _opt(argv, "--clone-root")
        rc = int(os.environ.get("F4_CAPTURE_RC") or 0)
        runs = int(_opt(argv, "--runs-per-arm") or 4)          # run_benchmark's default (RUNS_PER_ARM)
        models = [argv[i + 1] for i, a in enumerate(argv[:-1]) if a == "--claude-model"]
        arms = [a + sfx for sfx in [""] + ["@" + m for m in models] for a in ("deepmap_on", "deepmap_off")]
        try:
            manifest = json.loads((Path(clones) / repo.replace("/", "__") / ".deepmap" / "graph_build.json")
                                  .read_text(encoding="utf-8"))
        except (OSError, ValueError):
            manifest = None
        cards = [{"repo": repo, "question_id": "q1", "arm": arm, "run": 1, "exit_code": 0,
                  "metrics": {k: None for k in _METRIC_KEYS}, "scored": False,
                  "answer_text_path": f"results/{rid}/{repo.replace('/', '__')}/q1/{arm}/run-1/stdout.json"}
                 for arm in arms]
        if rc:
            cards = cards[:1]                   # an abort part-way keeps what was spent
        run_dir = HERE / "results" / rid / repo.replace("/", "__") / "q1" / "deepmap_on" / "run-1"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "record.json").write_text(json.dumps(cards[0]), encoding="utf-8")
        _write(rid, {"run_id": rid, "machine_spec": {"os": "sandbox", "deepmap_version": _VERSION},
                     "graph_builds": {repo: manifest} if isinstance(manifest, dict) else {},
                     "runs_per_arm": runs, "scorecards": cards, "measured": False, "capture_complete": rc == 0})
        return rc
    if "--score" in argv:
        rid = _opt(argv, "--score")
        rc = int(os.environ.get("F4_SCORE_RC") or 0)
        if rc == 0:
            rec = _read(rid)
            for c in rec.get("scorecards") or []:
                c["scored"] = True
                c["metrics"] = dict(c.get("metrics") or {}, completeness_pct=50.0, correctness_pct=90.0, wrong_high=0)
            rec["measured"] = True
            _write(rid, rec)
        return rc
    if "--merge" in argv:
        cards = []
        for rid in [s for s in (_opt(argv, "--merge") or "").split(",") if s]:
            try:
                cards += _read(rid).get("scorecards") or []
            except (OSError, ValueError):
                pass
        measured = bool(cards) and all(c.get("scored") is True and (c.get("metrics") or {}).get("completeness_pct")
                                       is not None for c in cards)
        _write(_opt(argv, "--run-id"), {"scorecards": cards, "measured": measured})
        return 0
    return 3


if __name__ == "__main__":
    sys.exit(_emulate(sys.argv[1:]))
else:                                           # imported by the driver's classifiers: expose the REAL module
    R = _load_real()
    for _name in dir(R):
        if not _name.startswith("__") and _name not in globals():
            globals()[_name] = getattr(R, _name)
'''

STUB_CLONE_GRAPH = r'''"""Sandbox stand-in for benchmark/clone_graph.py (tests/f4_breadth_resume_r283_gates.py): records, never builds."""
import json, os, sys
with open(os.environ["F4_CALLS"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps({"prog": "clone_graph", "argv": sys.argv[1:]}) + "\n")
cmd = sys.argv[1] if len(sys.argv) > 1 else ""
if cmd == "ports":
    print("9101 9102")
sys.exit(int(os.environ.get("F4_BUILD_RC") or 0) if cmd == "build" else 0)
'''


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSES if ok else FAILS).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok or not detail else f" — {detail}"))


def _bash():
    """A POSIX bash that can run the driver (Git Bash on Windows; never the WSL launcher in System32)."""
    cands = [shutil.which("bash")]
    if os.name == "nt":
        cands += [r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files\Git\usr\bin\bash.exe"]
    for c in cands:
        if c and Path(c).is_file() and "system32" not in c.lower():
            return c
    return None


def _manifest(pin: str, version=VERSION, **extra) -> dict:
    m = {"built_at_sha": pin, "built_at_utc": "2026-09-30T10:00:00Z", "extractor": "graphify",
         "graphify_version": "0.8.51", "dirty": False, "built_at_dirty": False, "provenance": "git"}
    if version:
        m["deepmap_version"] = version
    m.update(extra)
    return m


def _cards(repo: str, scored: bool) -> list:
    metrics = {"completeness_pct": 50.0, "correctness_pct": 90.0, "wrong_high": 0} if scored else {}
    return [{"repo": repo, "question_id": "q1", "arm": arm, "run": 1, "exit_code": 0,
             "metrics": dict({"completeness_pct": None}, **metrics), "scored": scored}
            for arm in ("deepmap_on", "deepmap_off")]


def _provenanced(repo: str, *, scored: bool, version=VERSION, complete=True, graph=None) -> dict:
    rec = {"machine_spec": {"os": "t", "deepmap_version": version},
           "graph_builds": {repo: graph or _manifest(REPOS[repo])}, "scorecards": _cards(repo, scored),
           "measured": scored}
    if complete is not None:
        rec["capture_complete"] = complete
    return rec


def _july(repo: str) -> dict:
    return {"machine_spec": {"os": "t", "deepmap_commit": "6e307e356666"}, "scorecards": _cards(repo, True),
            "measured": True}


def _shape(runs: int, models=(), family=()) -> dict:
    """A capture shape as the driver records it (breadth_shape.json): sorted, de-duplicated."""
    return {"claude_models": sorted(set(models)), "family": sorted(set(family)), "runs_per_arm": runs}


# The questions.yaml fixture: a bare empty list, which PyYAML AND run_benchmark's stdlib fallback both read as "no
# questions". `questions: []` needed PyYAML (the fallback reads `[]` after a key as a string, and load_config refuses it).
QUESTIONS_FIXTURE = "[]\n"


class Sandbox:
    """<td>/tree (the driver + stand-ins + repos.yaml + results), <td>/clones (pinned clones with graph manifests),
    <td>/bin (python + claude shims), <td>/noyaml (a `yaml` module that fails to import: PYTHONPATH=<it> hides PyYAML),
    <td>/calls.jsonl. `pycache` (shared by every scenario) is PYTHONPYCACHEPREFIX: the module under test compiles once,
    and never into the tree under test. `driver` is the script under test (benchmark/<driver>, copied from `root`)."""

    def __init__(self, root: Path, td: Path, pycache: Path, driver: str = "run_breadth.sh"):
        self.td, self.root, self.pycache, self.driver = td, root, pycache, driver
        self.tree, self.clones, self.bin = td / "tree", td / "clones", td / "bin"
        self.results = self.tree / "benchmark" / "results"
        self.calls_file = td / "calls.jsonl"
        self.noyaml = td / "noyaml"
        (self.tree / "benchmark").mkdir(parents=True)
        self.results.mkdir()
        src = (root / "benchmark" / driver).read_bytes().replace(b"\r\n", b"\n")
        (self.tree / "benchmark" / driver).write_bytes(src)
        (self.tree / "benchmark" / "run_benchmark.py").write_text(STUB_RUN_BENCHMARK, encoding="utf-8", newline="\n")
        (self.tree / "benchmark" / "clone_graph.py").write_text(STUB_CLONE_GRAPH, encoding="utf-8", newline="\n")
        yaml = "repos:\n" + "".join(
            f"  - name: {n}\n    url: https://example.invalid/{n}.git\n    pinned_sha: \"{p}\"\n    license: MIT\n"
            f"    stack: go\n" for n, p in REPOS.items())
        (self.tree / "benchmark" / "repos.yaml").write_text(yaml, encoding="utf-8", newline="\n")
        (self.tree / "benchmark" / "questions.yaml").write_text(QUESTIONS_FIXTURE, encoding="utf-8", newline="\n")
        self.noyaml.mkdir()
        (self.noyaml / "yaml.py").write_text('raise ImportError("PyYAML is hidden by the gate sandbox")\n',
                                             encoding="utf-8", newline="\n")
        for name, pin in REPOS.items():
            self.clone_manifest(name, _manifest(pin))
            (self.clones / name.replace("/", "__") / ".git").mkdir(parents=True, exist_ok=True)
        self.bin.mkdir()
        exe = Path(sys.executable).as_posix()
        # The python shim answers the clone_graph.py stand-in itself (records the call; `ports` prints the pair) — the
        # same contract as STUB_CLONE_GRAPH without a Python start per call (slow on Windows); anything else is Python.
        shim = ("#!/bin/sh\n"
                "if [ \"$1\" = \"benchmark/clone_graph.py\" ]; then\n"
                "  printf '{\"prog\": \"clone_graph\", \"argv\": [\"%s\", \"%s\"]}\\n' \"$2\" \"$3\" >> \"$F4_CALLS\"\n"
                "  [ \"$2\" = \"ports\" ] && echo \"9101 9102\"\n"
                "  [ \"$2\" = \"build\" ] && exit \"${F4_BUILD_RC:-0}\"\n"
                "  exit 0\n"
                "fi\n"
                f"exec '{exe}' \"$@\"\n")
        (self.bin / "python").write_text(shim, encoding="utf-8", newline="\n")
        (self.bin / "claude").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8", newline="\n")
        for f in ("python", "claude"):
            os.chmod(self.bin / f, 0o755)

    def clone_manifest(self, name: str, manifest: dict) -> None:
        d = self.clones / name.replace("/", "__") / ".deepmap"
        d.mkdir(parents=True, exist_ok=True)
        (d / "graph_build.json").write_text(json.dumps(manifest), encoding="utf-8")

    def put(self, rid: str, record: "dict | None" = None, *, record_json: bool = True,
            shape: "dict | None" = None) -> None:
        """A prior capture at `rid`: its results.json (`record`; each card gets an answer path into this run dir, as
        run_benchmark writes it), per-run record.json files — one whose answer path is Windows-style (run_one's
        str(Path) on Windows), one whose answer path names a SIBLING dir that only starts with the run-id (a re-point
        must leave it alone) — and, with `shape`, the capture shape the driver records (breadth_shape.json)."""
        d = self.results / rid
        d.mkdir(parents=True, exist_ok=True)
        if record is not None:
            record = json.loads(json.dumps(record))
            for c in record.get("scorecards") or []:
                c.setdefault("answer_text_path", f"results/{rid}/o__x/q1/{c.get('arm')}/run-1/stdout.json")
            (d / "results.json").write_text(json.dumps(record), encoding="utf-8")
        if record_json:
            for arm, path in (("deepmap_on", f"results\\{rid}\\o__x\\q1\\deepmap_on\\run-1\\stdout.json"),
                              ("deepmap_off", f"results/{rid}-other/o__x/q1/deepmap_off/run-1/stdout.json")):
                rd = d / "o__x" / "q1" / arm / "run-1"
                rd.mkdir(parents=True, exist_ok=True)
                (rd / "record.json").write_text(json.dumps({"arm": arm, "answer_text_path": path}), encoding="utf-8")
        if shape is not None:
            (d / SHAPE_FILE).write_text(json.dumps(shape, sort_keys=True), encoding="utf-8")

    def run(self, bash: str, repos=("o/alpha",), **env_over) -> "tuple[int, str, list]":
        env = {k: v for k, v in os.environ.items() if k not in ENV_DROP and k != "PYTHONDONTWRITEBYTECODE"}
        env.update(PATH=str(self.bin) + os.pathsep + os.environ.get("PATH", ""), NEO4J_PASSWORD="sandbox",
                   CLONES=self.clones.as_posix(), F4_REAL_RB=str(self.root / "benchmark" / "run_benchmark.py"),
                   F4_VERSION=VERSION, F4_CALLS=str(self.calls_file), PYTHONPYCACHEPREFIX=str(self.pycache),
                   SKIP_PROBE="1", REPOS=" ".join(repos))
        env.update({k: str(v) for k, v in env_over.items()})
        try:
            p = subprocess.run([bash, str(self.tree / "benchmark" / self.driver)], cwd=str(self.tree), env=env,
                               stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=RUN_TIMEOUT_S)
            rc, out = p.returncode, (p.stdout or "") + (p.stderr or "")
        except subprocess.TimeoutExpired as exc:   # a stalled run fails ITS checks; the other scenarios still run
            part = [b.decode("utf-8", "replace") if isinstance(b, bytes) else (b or "") for b in (exc.stdout, exc.stderr)]
            rc, out = 124, "".join(part) + f"\n[gate] the driver run did not finish within {RUN_TIMEOUT_S} s\n"
        calls = []
        if self.calls_file.is_file():
            calls = [json.loads(ln) for ln in self.calls_file.read_text(encoding="utf-8").splitlines() if ln.strip()]
        return rc, out, calls


def _rb(calls, flag):
    return [c["argv"] for c in calls if c["prog"] == "run_benchmark" and flag in c["argv"]]


def _opt(argv, flag):
    return argv[argv.index(flag) + 1] if flag in argv and argv.index(flag) + 1 < len(argv) else None


def _builds(calls):
    return [c["argv"] for c in calls if c["prog"] == "clone_graph" and c["argv"][:1] == ["build"]]


def _rid(name: str, tag: str = TAG, prefix: str = FINAL_PREFIX) -> str:
    return f"{prefix}-{tag}-{name.replace('/', '__')}"


def _both(sb: Sandbox, name: str, record=None, *, record_json: bool = True, prefix: str = FINAL_PREFIX,
          shape: "dict | None" = None) -> None:
    """Put the same prior capture where the driver looks: the tagged run-id AND the old untagged one."""
    sb.put(_rid(name, prefix=prefix), record, record_json=record_json, shape=shape)
    sb.put(f"{prefix}-{name.replace('/', '__')}", record, record_json=record_json, shape=shape)


def _archive_paths(sb: Sandbox, rid: str) -> "tuple[list, dict]":
    """The `<rid>.unfinished-*` archives, and {file relative to the archive: [answer paths]} of the one archive."""
    arch = sorted(p for p in sb.results.glob(rid + ".unfinished-*") if p.is_dir())
    paths: dict = {}
    if len(arch) == 1:
        for f in sorted(arch[0].rglob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            cards = data.get("scorecards") if f.name == "results.json" else [data]
            got = [c.get("answer_text_path") for c in cards or [] if isinstance(c, dict) and "answer_text_path" in c]
            if got:
                paths[f.relative_to(arch[0]).as_posix()] = got
    return arch, paths


def _repointed(sb: Sandbox, rid: str) -> "tuple[bool, str]":
    """Every answer path the archive held under results/<rid>/ now names the archive (both separators); the sibling
    results/<rid>-other/ path is untouched. -> (ok, detail)"""
    arch, paths = _archive_paths(sb, rid)
    if len(arch) != 1:
        return False, f"archives={[p.name for p in arch]}"
    new = arch[0].name
    flat = [p for ps in paths.values() for p in ps]
    inside = [p for p in flat if "-other" not in p]
    ok = (bool(inside) and all(p.replace("\\", "/").startswith(f"results/{new}/") for p in inside)
          and f"results/{rid}-other/o__x/q1/deepmap_off/run-1/stdout.json" in flat
          and any("\\" in p for p in inside))
    return ok, json.dumps(paths)[:500]


def _no_driver_log(sb: Sandbox) -> "list[str]":
    """Every *-driver.log in the sandbox tree (a refused launch must write none)."""
    return sorted(p.relative_to(sb.tree).as_posix() for p in sb.tree.rglob("*-driver.log"))


def scenarios(root: Path, bash: str) -> None:
    with tempfile.TemporaryDirectory() as cache_td:
        _scenarios(root, bash, Path(cache_td))


def _scenarios(root: Path, bash: str, cache: Path) -> None:
    both = ("o/alpha", "o/beta")

    def fresh():
        return tempfile.TemporaryDirectory()

    print("\n-- 1. nothing captured (final): capture + blind score + merge, every run-id tagged --")
    with fresh() as td:
        sb = Sandbox(root, Path(td), cache)
        rc, out, calls = sb.run(bash, MODE="final")
        caps = [_opt(a, "--run-id") for a in _rb(calls, "--repo")]
        scores = _rb(calls, "--score")
        merges = _rb(calls, "--merge")
        check("rc 0; the repo's OWN graph is built (clone_graph.py build <clone>)",
              rc == 0 and len(_builds(calls)) == 1, f"rc={rc} builds={_builds(calls)}\n{out[-800:]}")
        check("the capture run-id carries RUN_TAG (default v<DeepMap version>): breadth-v0.28.9-<owner__name>",
              caps == [_rid("o/alpha")], str(caps))
        check("the capture is blind-scored with --clone-root (the judge reads the pinned clones)",
              [_opt(a, "--score") for a in scores] == [_rid("o/alpha")]
              and all("--clone-root" in a for a in scores), str(scores))
        check("the merge target carries the tag (breadth-v0.28.9-combined) and the close says 'measured'",
              [_opt(a, "--run-id") for a in merges] == [f"breadth-{TAG}-combined"] and "DONE — measured" in out,
              f"{merges} / {out[-300:]}")
        try:
            recorded = json.loads((sb.results / _rid("o/alpha") / SHAPE_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            recorded = repr(exc)
        check("the capture's shape is recorded next to it (breadth_shape.json: n=2, no extra arm, every family)",
              recorded == _shape(2), str(recorded))

    print("\n-- 2. a pre-0.28.3 capture (no graph provenance) at the run-id: STOP, never reused --")
    with fresh() as td:
        sb = Sandbox(root, Path(td), cache)
        _both(sb, "o/alpha", _july("o/alpha"))
        rc, out, calls = sb.run(bash, MODE="final")
        check("the driver stops (rc != 0) and says why: it cannot be reused (no graph provenance)",
              rc != 0 and "cannot be reused" in out and "provenance" in out, f"rc={rc}\n{out[-600:]}")
        check("...having captured, scored and merged NOTHING (no skip-as-measured, no silent overwrite)",
              not _rb(calls, "--repo") and not _rb(calls, "--score") and not _rb(calls, "--merge"),
              json.dumps(calls)[:400])

    print("\n-- 3. loop mode: a capture by ANOTHER DeepMap version at the run-id: STOP --")
    with fresh() as td:
        sb = Sandbox(root, Path(td), cache)
        _both(sb, "o/alpha", _provenanced("o/alpha", scored=False, version="0.27.4",
                                          graph=_manifest(REPOS["o/alpha"], version="0.27.4")),
              prefix=LOOP_PREFIX, shape=_shape(1))
        rc, out, calls = sb.run(bash, MODE="loop")
        check("loop mode no longer skips any run-id holding a record.json: another version's capture stops it",
              rc != 0 and "0.27.4" in out and not _rb(calls, "--repo"), f"rc={rc}\n{out[-600:]}")

    print("\n-- 4. the clone's graph was rebuilt by ANOTHER DeepMap since the capture: STOP --")
    with fresh() as td:
        sb = Sandbox(root, Path(td), cache)
        _both(sb, "o/alpha", _provenanced("o/alpha", scored=True))
        sb.clone_manifest("o/alpha", _manifest(REPOS["o/alpha"], version="0.29.0"))
        rc, out, calls = sb.run(bash, MODE="final")
        check("a measured capture whose clone graph another DeepMap rebuilt since is not skipped as measured "
              "(the graph manifest's builder version decides) -> stop",
              rc != 0 and "0.29.0" in out and not _rb(calls, "--merge"), f"rc={rc}\n{out[-600:]}")

    print("\n-- 5. measured on THIS graph: skipped; captured-unscored: scored only --")
    with fresh() as td:
        sb = Sandbox(root, Path(td), cache)
        _both(sb, "o/alpha", _provenanced("o/alpha", scored=True), shape=_shape(2))
        _both(sb, "o/beta", _provenanced("o/beta", scored=False), shape=_shape(2))
        rc, out, calls = sb.run(bash, repos=both, MODE="final")
        caps = [_opt(a, "--repo") for a in _rb(calls, "--repo")]
        scored = [_opt(a, "--score") for a in _rb(calls, "--score")]
        check("rc 0; neither repo is built or captured again (no re-spend)",
              rc == 0 and not caps and not _builds(calls), f"rc={rc} caps={caps}\n{out[-600:]}")
        check("only the captured-but-unscored repo is scored; both are merged",
              scored == [_rid("o/beta")] and _rb(calls, "--merge")
              and _opt(_rb(calls, "--merge")[0], "--merge") == f"{_rid('o/alpha')},{_rid('o/beta')}", str(scored))

    print("\n-- 6. loop mode: a capture of THIS graph is skipped (no re-spend) --")
    with fresh() as td:
        sb = Sandbox(root, Path(td), cache)
        _both(sb, "o/alpha", _provenanced("o/alpha", scored=False), prefix=LOOP_PREFIX, shape=_shape(1))
        rc, out, calls = sb.run(bash, repos=both, MODE="loop")
        caps = [_opt(a, "--repo") for a in _rb(calls, "--repo")]
        check("rc 0; o/alpha skipped, o/beta captured; nothing scored or merged in loop mode",
              rc == 0 and caps == ["o/beta"] and not _rb(calls, "--score") and not _rb(calls, "--merge")
              and "LOOP DONE" in out, f"rc={rc} caps={caps}\n{out[-600:]}")
        check("loop mode's own run-id space: breadth-loop-v0.28.9-<owner__name>, n=1",
              [_opt(a, "--run-id") for a in _rb(calls, "--repo")] == [_rid("o/beta", prefix=LOOP_PREFIX)]
              and all(_opt(a, "--runs-per-arm") == "1" for a in _rb(calls, "--repo")), str(_rb(calls, "--repo")))

    print("\n-- 7. an UNFINISHED capture of this graph (capture_complete=false) is captured again, not reused --")
    with fresh() as td:
        sb = Sandbox(root, Path(td), cache)
        _both(sb, "o/alpha", _provenanced("o/alpha", scored=True, complete=False))
        rc, out, calls = sb.run(bash, MODE="final")
        caps = [_opt(a, "--repo") for a in _rb(calls, "--repo")]
        check("even with every kept card scored, the partial capture is re-captured (then scored), never skipped "
              "as measured", rc == 0 and "o/alpha" in caps and _rid("o/alpha") in
              [_opt(a, "--score") for a in _rb(calls, "--score")], f"rc={rc} caps={caps}\n{out[-600:]}")
        aside = sorted(p.name for p in sb.results.glob(_rid("o/alpha") + ".unfinished-*"))
        check("...and the unfinished capture was moved aside (kept, never deleted or mixed into the new one)",
              len(aside) == 1 and (sb.results / aside[0] / "results.json").is_file()
              and not (sb.results / _rid("o/alpha") / "o__x").exists(), f"aside={aside}")
        ok, detail = _repointed(sb, _rid("o/alpha"))
        check("...and the archive's answer paths (results.json cards + per-run records, / and \\ alike) now name the "
              "archive, not the run-id the new capture took over (a sibling results/<run-id>-other/ path is left "
              "alone)", ok, detail)

    print("\n-- 8. loop mode: a killed capture (per-run files, no results.json) is captured again --")
    with fresh() as td:
        sb = Sandbox(root, Path(td), cache)
        _both(sb, "o/alpha", None, record_json=True, prefix=LOOP_PREFIX)
        rid = _rid("o/alpha", prefix=LOOP_PREFIX)
        rc, out, calls = sb.run(bash, MODE="loop")
        caps = [_opt(a, "--repo") for a in _rb(calls, "--repo")]
        check("the run-id's leftover per-run files no longer mean 'done': it is re-captured, and the log says so",
              rc == 0 and "o/alpha" in caps and "RECAPTURE" in out, f"rc={rc} caps={caps}\n{out[-600:]}")
        left = [p for p in (sb.results / rid).rglob("record.json")]
        check("the new capture's dir holds only its own per-run files (completeness_only.py reads every record.json "
              "there); the old ones are kept aside", left and all("o__x" not in p.parts for p in left)
              and len(list(sb.results.glob(rid + ".unfinished-*"))) == 1, str(left))
        ok, detail = _repointed(sb, rid)
        check("...and the archive's per-run answer paths name the archive", ok, detail)

    print("\n-- 8b. a COMPLETE capture whose results.json is unreadable (truncated) is moved aside, and the log says "
          "why --")
    with fresh() as td:
        sb = Sandbox(root, Path(td), cache)
        _both(sb, "o/alpha", _provenanced("o/alpha", scored=True), shape=_shape(2))
        (sb.results / _rid("o/alpha") / "results.json").write_text('{"scorecards": [', encoding="utf-8")
        rc, out, calls = sb.run(bash, MODE="final")
        arch, _paths = _archive_paths(sb, _rid("o/alpha"))
        check("re-captured and scored (rc 0); the RECAPTURE line gives the reason (unreadable results.json); the old "
              "files are kept aside, and a WARN says the unreadable file's paths could not be re-pointed",
              rc == 0 and any("RECAPTURE" in ln and "unreadable" in ln for ln in out.splitlines()) and len(arch) == 1
              and any("WARN" in ln and "unreadable" in ln for ln in out.splitlines())
              and _rid("o/alpha") in [_opt(a, "--score") for a in _rb(calls, "--score")],
              f"rc={rc} arch={[p.name for p in arch]}\n{out[-700:]}")

    print("\n-- 9. a capture that exits non-zero is never scored or merged; the driver exits 1 --")
    with fresh() as td:
        sb = Sandbox(root, Path(td), cache)
        rc, out, calls = sb.run(bash, repos=both, MODE="final", F4_CAPTURE_RC=2)
        check("both captures ran and failed -> no --score, no --merge, rc 1, and the log names the incomplete repos",
              len(_rb(calls, "--repo")) == 2 and not _rb(calls, "--score") and not _rb(calls, "--merge")
              and rc == 1 and "INCOMPLETE" in out, f"rc={rc}\n{out[-600:]}")

    print("\n-- 10. final mode checks the blind judge BEFORE any capture --")
    with fresh() as td:
        sb = Sandbox(root, Path(td), cache)
        rc, out, calls = sb.run(bash, MODE="final", CLAUDE_MODELS="claude-sonnet-5")
        check("the default `sonnet` judge over a claude-sonnet-5 arm -> stop before building or capturing anything, "
              "naming JUDGE_MODEL", rc != 0 and not _builds(calls) and not _rb(calls, "--repo")
              and "JUDGE_MODEL" in out and "claude-sonnet-5" in out, f"rc={rc}\n{out[-600:]}")
    with fresh() as td:
        sb = Sandbox(root, Path(td), cache)
        rc, out, calls = sb.run(bash, MODE="final", CLAUDE_MODELS="claude-sonnet-5", JUDGE_MODEL="haiku")
        caps = _rb(calls, "--repo")
        scores = _rb(calls, "--score")
        check("JUDGE_MODEL=haiku: the run proceeds, the capture carries --claude-model claude-sonnet-5, and every "
              "--score passes --judge-model haiku (+ --clone-root)",
              rc == 0 and caps and all(_opt(a, "--claude-model") == "claude-sonnet-5" for a in caps)
              and scores and all(_opt(a, "--judge-model") == "haiku" and "--clone-root" in a for a in scores),
              f"rc={rc} scores={scores}\n{out[-400:]}")
    with fresh() as td:
        sb = Sandbox(root, Path(td), cache)
        rc, out, calls = sb.run(bash, MODE="final", RUN_TAG="custom-1")
        caps = [_opt(a, "--run-id") for a in _rb(calls, "--repo")]
        check("RUN_TAG=<tag> keys a fresh run (breadth-custom-1-<repo>, breadth-custom-1-combined)",
              rc == 0 and caps == [_rid("o/alpha", "custom-1")]
              and [_opt(a, "--run-id") for a in _rb(calls, "--merge")] == ["breadth-custom-1-combined"], str(caps))

    print("\n-- 11. the two documented launches in order (MODE=loop, then MODE=final CLAUDE_MODELS=claude-sonnet-5 "
          "JUDGE_MODEL=haiku): the final launch captures ITS matrix, never the loop capture --")
    with fresh() as td:
        sb = Sandbox(root, Path(td), cache)
        rc1, out1, calls1 = sb.run(bash, MODE="loop")
        rc2, out2, calls = sb.run(bash, MODE="final", CLAUDE_MODELS="claude-sonnet-5", JUDGE_MODEL="haiku")
        calls2 = calls[len(calls1):]
        caps1, caps2 = _rb(calls1, "--repo"), _rb(calls2, "--repo")
        check("the loop launch captures under loop mode's own run-id (breadth-loop-v0.28.9-o__alpha, n=1)",
              rc1 == 0 and [_opt(a, "--run-id") for a in caps1] == [_rid("o/alpha", prefix=LOOP_PREFIX)]
              and all(_opt(a, "--runs-per-arm") == "1" for a in caps1), f"rc={rc1} caps={caps1}\n{out1[-400:]}")
        check("the final launch captures n=2 with --claude-model claude-sonnet-5 under ITS run-id "
              "(breadth-v0.28.9-o__alpha), then scores and merges it: measured, rc 0",
              rc2 == 0 and [_opt(a, "--run-id") for a in caps2] == [_rid("o/alpha")]
              and all(_opt(a, "--runs-per-arm") == "2" and _opt(a, "--claude-model") == "claude-sonnet-5" for a in caps2)
              and [_opt(a, "--score") for a in _rb(calls2, "--score")] == [_rid("o/alpha")]
              and "DONE — measured" in out2, f"rc={rc2} caps={caps2}\n{out2[-500:]}")
        check("...and the loop capture is left where it was (completeness_only.py still reads it)",
              (sb.results / _rid("o/alpha", prefix=LOOP_PREFIX) / "results.json").is_file(), "")

    print("\n-- 12. a capture of THIS graph in ANOTHER shape at the run-id: STOP — never reused, scored or merged --")
    # (the run count is the shared-PREFIX case below: a loop capture is n=1, a final launch n=2)
    for label, recorded, env in (
            ("another arm set (Opus -> Opus + claude-sonnet-5)", _shape(2),
             {"CLAUDE_MODELS": "claude-sonnet-5", "JUDGE_MODEL": "haiku"}),
            ("another family filter (every family -> multi_hop)", _shape(2), {"FAMILY": "multi_hop"})):
        with fresh() as td:
            sb = Sandbox(root, Path(td), cache)
            _both(sb, "o/alpha", _provenanced("o/alpha", scored=True), shape=recorded)
            rc, out, calls = sb.run(bash, MODE="final", **env)
            check(f"{label}: stop (rc != 0), naming the capture's shape and this launch's; nothing built, captured, "
                  f"scored or merged", rc != 0 and "another shape" in out and "this launch" in out
                  and not _builds(calls) and not _rb(calls, "--repo") and not _rb(calls, "--score")
                  and not _rb(calls, "--merge"), f"rc={rc}\n{out[-600:]}")
    with fresh() as td:
        sb = Sandbox(root, Path(td), cache)
        rc1, _out1, calls1 = sb.run(bash, MODE="loop", PREFIX="shared")
        rc2, out2, calls = sb.run(bash, MODE="final", PREFIX="shared")
        calls2 = calls[len(calls1):]
        check("an explicit PREFIX shared by a loop and a final launch: the final launch stops at the loop capture "
              "(n=1 vs n=2) — nothing captured, scored or merged",
              rc1 == 0 and rc2 != 0 and "another shape" in out2 and "n=1" in out2 and not _rb(calls2, "--repo")
              and not _rb(calls2, "--score") and not _rb(calls2, "--merge"), f"rc={rc1}/{rc2}\n{out2[-600:]}")

    print("\n-- 13. a capture with NO recorded shape (made outside this driver, e.g. run_benchmark.py by hand): STOP --")
    with fresh() as td:
        sb = Sandbox(root, Path(td), cache)
        _both(sb, "o/alpha", _provenanced("o/alpha", scored=False))
        rc, out, calls = sb.run(bash, MODE="final")
        check("stop naming breadth_shape.json: nothing shows the capture is this launch's matrix; nothing captured, "
              "scored or merged", rc != 0 and SHAPE_FILE in out and not _rb(calls, "--repo")
              and not _rb(calls, "--score") and not _rb(calls, "--merge"), f"rc={rc}\n{out[-600:]}")

    print("\n-- 14. a results.json that contradicts its recorded shape (re-captured into the run-id by hand): STOP --")
    extra_arm = _provenanced("o/alpha", scored=True)
    extra_arm["scorecards"].append(dict(extra_arm["scorecards"][0], arm="deepmap_on@claude-x"))
    for label, rec in (("results.json holds n=4, the shape record n=2",
                        dict(_provenanced("o/alpha", scored=True), runs_per_arm=4)),
                       ("results.json holds an arm of a model this launch does not run (claude-x)", extra_arm)):
        with fresh() as td:
            sb = Sandbox(root, Path(td), cache)
            _both(sb, "o/alpha", rec, shape=_shape(2))
            rc, out, calls = sb.run(bash, MODE="final")
            check(f"{label}: stop; nothing scored or merged", rc != 0 and "another shape" in out
                  and not _rb(calls, "--score") and not _rb(calls, "--merge"), f"rc={rc}\n{out[-600:]}")

    print("\n-- 15. MODE / PREFIX / RUN_TAG / RUNS are checked BEFORE the driver log opens: a refused launch writes "
          "no file --")
    for label, env in (("PREFIX=../escape (the log path is built from PREFIX)", {"MODE": "final", "PREFIX": "../escape"}),
                       ("MODE=bogus", {"MODE": "bogus"}),
                       ("RUN_TAG='bad tag'", {"MODE": "final", "RUN_TAG": "bad tag"}),
                       ("RUNS=0 (run_benchmark reads --runs-per-arm 0 as its default n=4)", {"MODE": "final", "RUNS": "0"}),
                       ("RUNS=two", {"MODE": "loop", "RUNS": "two"})):
        with fresh() as td:
            sb = Sandbox(root, Path(td), cache)
            rc, out, calls = sb.run(bash, **env)
            logs = _no_driver_log(sb)
            check(f"{label}: refused (rc != 0, the reason printed), no driver log anywhere, nothing run",
                  rc != 0 and "ERROR" in out and not logs and not calls, f"rc={rc} logs={logs}\n{out[-300:]}")

    print("\n-- 16. without PyYAML: run_benchmark's stdlib fallback reads the sandbox's repos/questions fixtures --")
    with fresh() as td:
        sb = Sandbox(root, Path(td), cache)
        probe = subprocess.run([sys.executable, "-c", "import yaml"], capture_output=True, text=True, timeout=120,
                               env=dict(os.environ, PYTHONPATH=str(sb.noyaml)))
        check("(premise) the sandbox hides PyYAML: `import yaml` fails under its PYTHONPATH", probe.returncode != 0,
              (probe.stdout + probe.stderr)[-200:])
        rc, out, calls = sb.run(bash, MODE="final", PYTHONPATH=str(sb.noyaml))
        check("a fresh final launch with PyYAML hidden is classified, captured, scored and merged (rc 0, measured)",
              rc == 0 and _rb(calls, "--repo") and _rb(calls, "--score") and _rb(calls, "--merge")
              and "DONE — measured" in out, f"rc={rc}\n{out[-600:]}")


def static_checks(root: Path, bash: "str | None") -> None:
    print("\n-- static: syntax, lint, header --")
    path = root / "benchmark" / "run_breadth.sh"
    text = path.read_text(encoding="utf-8")
    code = "\n".join(ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#"))
    if bash:
        p = subprocess.run([bash, "-n", str(path)], capture_output=True, text=True, timeout=60)
        check("bash -n (syntax)", p.returncode == 0, p.stderr.strip()[:200])
    sc = shutil.which("shellcheck")
    if sc:
        p = subprocess.run([sc, "-S", "warning", str(path)], capture_output=True, text=True, timeout=120)
        check("shellcheck -S warning: clean", p.returncode == 0, (p.stdout or p.stderr).strip()[:400])
    else:
        print("  [skip] shellcheck not installed")
    check("resumption is decided by run_benchmark.resume_state_for (the G3 driver's classifier)",
          "resume_state_for" in code and "has_captures" not in code, "")
    check("the header no longer says it orchestrates build/deepmap_cli.py (it runs clone_graph.py + run_benchmark.py)",
          "orchestrates the existing entrypoints (build/deepmap_cli.py" not in text, "")
    check("the header documents RUN_TAG and JUDGE_MODEL", re.search(r"^#\s+RUN_TAG=", text, re.M) is not None
          and re.search(r"^#\s+JUDGE_MODEL=", text, re.M) is not None, "")
    header = "\n".join(ln for ln in text.splitlines() if ln.startswith("#"))
    check("the header documents each MODE's own default PREFIX and the recorded capture shape (breadth_shape.json)",
          "breadth-loop" in header and SHAPE_FILE in header, "")


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    root = REPO
    if "--root" in sys.argv:
        root = Path(sys.argv[sys.argv.index("--root") + 1]).resolve()
    print("=" * 72)
    print(f"DeepMap — run_breadth.sh: provenance- and shape-checked resume, tagged runs, honest judge + failures "
          f"(root: {root})")
    print("=" * 72)
    bash = _bash()
    static_checks(root, bash)
    if bash:
        scenarios(root, bash)
    else:
        check("a POSIX bash is available to run the driver (Git Bash on Windows)", False, "no bash found")

    total = len(PASSES) + len(FAILS)
    print("\n" + "=" * 72)
    if FAILS:
        print(f"RESULT: RED — {len(PASSES)}/{total} gates passed.")
        return 1
    print(f"RESULT: GREEN — {len(PASSES)}/{total} gates passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
