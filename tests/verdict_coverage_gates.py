#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gates for the per-language / per-directory COVERAGE honesty layer (Ph0 of the Rails field-report
fix). Pure: no Neo4j, no network, no spend.

The field failure this locks out: a Rails repo whose `app/models/` produced 0 nodes (declarative
ActiveRecord DSL is invisible to the generic extractor) still classified READY, because
`verdict.classify` reasons only from GLOBAL counts. These gates lock:

  - `verdict.build_coverage(root, nodes)`: cross-references extracted nodes against the on-disk
    census -> {"languages": {lang: {files, nodes}}, "dirs": {conventional-dir: {files, nodes}}},
    bounded + never-raises,
  - `classify(..., coverage=...)`: a conventional dir with source files but ~no nodes -> DEGRADED
    with the dir NAMED in the reason (the primary detector); a census language with many files but
    ~0 nodes -> DEGRADED (the coarse fallback),
  - back-compat: `classify` WITHOUT coverage behaves exactly as before (existing verdict_gates
    stay green unedited),
  - the manifest carries verdict/reasons/coverage (best-effort, additive — sync_manifest_gates'
    existing key assertions stay green),
  - the DEEPMAP_RECOGNIZERS=0 replay contract: a Rails-shaped tree with a dead model layer must
    classify DEGRADED naming app/models (proves the honesty layer would have caught the original
    field failure).
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build"

sys.path.insert(0, str(BUILD))
import verdict as V  # noqa: E402

_PASS = _FAIL = 0


def check(desc: str, cond: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    ok = bool(cond)
    _PASS += ok
    _FAIL += (not ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {desc}" + ("" if ok else f"  -- {detail}"))


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"dmvc_{name}", BUILD / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _rails_tree(root: Path, *, model_files: int = 4) -> None:
    """A Rails-shaped source tree: models (DSL-heavy), controllers, and a service."""
    (root / "app" / "models").mkdir(parents=True)
    (root / "app" / "controllers").mkdir(parents=True)
    (root / "app" / "services").mkdir(parents=True)
    for i in range(model_files):
        (root / "app" / "models" / f"model_{i}.rb").write_text(
            "class Model%d < ApplicationRecord\n  has_many :things, dependent: :destroy\nend\n" % i,
            encoding="utf-8")
    (root / "app" / "controllers" / "orders_controller.rb").write_text(
        "class OrdersController < ApplicationController\n  def index\n  end\nend\n", encoding="utf-8")
    (root / "app" / "services" / "checkout.rb").write_text(
        "class Checkout\n  def call\n  end\nend\n", encoding="utf-8")


def _node(name: str, source_file: str, label: str = "Class") -> dict:
    return {"key": f"{source_file}::{name}:1", "label": label, "name": name,
            "source_file": source_file, "start_line": 1, "documented": False,
            "body_summary": f"{name}: x"}


def gate_build_coverage() -> None:
    print("\n-- build_coverage(): per-language + per-conventional-dir {files, nodes} --")
    fn = getattr(V, "build_coverage", None)
    check("build_coverage exists", callable(fn))
    if not callable(fn):
        return
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _rails_tree(root, model_files=4)
        # Extracted nodes: controllers + service got nodes; app/models got NOTHING (the field state).
        nodes = [
            _node("OrdersController", "app/controllers/orders_controller.rb"),
            _node("OrdersController.index", "app/controllers/orders_controller.rb", "Method"),
            _node("Checkout", "app/services/checkout.rb"),
        ]
        cov = fn(root, nodes)
        check("returns a dict with languages + dirs", isinstance(cov, dict)
              and isinstance(cov.get("languages"), dict) and isinstance(cov.get("dirs"), dict),
              str(type(cov)))
        langs = cov.get("languages", {})
        check("Ruby census: 6 files", langs.get("Ruby", {}).get("files") == 6, str(langs))
        check("Ruby nodes: 3 attributed", langs.get("Ruby", {}).get("nodes") == 3, str(langs))
        dirs = cov.get("dirs", {})
        check("app/models tracked as a conventional dir", "app/models" in dirs, str(dirs))
        check("app/models: 4 files, 0 nodes",
              dirs.get("app/models", {}).get("files") == 4
              and dirs.get("app/models", {}).get("nodes") == 0, str(dirs))
        check("app/controllers: 1 file, 2 nodes",
              dirs.get("app/controllers", {}).get("files") == 1
              and dirs.get("app/controllers", {}).get("nodes") == 2, str(dirs))
        # Windows-shaped source_file paths still attribute (OS-normalized).
        cov2 = fn(root, [_node("M0", "app\\models\\model_0.rb")])
        check("backslash source_file still attributes to app/models",
              cov2.get("dirs", {}).get("app/models", {}).get("nodes") == 1, str(cov2.get("dirs")))
        # Never raises on junk nodes.
        cov3 = fn(root, [{"nope": 1}, {"source_file": None}, "junk", None])
        check("junk node entries degrade, never raise", isinstance(cov3, dict))
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "src").mkdir()
        (root / "src" / "a.py").write_text("x=1", encoding="utf-8")
        cov = V.build_coverage(root, [_node("a", "src/a.py", "Module")])
        check("non-Rails tree: no conventional dirs reported", cov.get("dirs") == {}, str(cov))


def gate_classify_dir_coverage() -> None:
    print("\n-- classify(coverage=...): dead conventional dir -> DEGRADED naming the dir --")
    # The exact field state: plenty of nodes overall (controllers/services), dead app/models.
    coverage = {"languages": {"Ruby": {"files": 40, "nodes": 30}},
                "dirs": {"app/models": {"files": 12, "nodes": 0},
                         "app/controllers": {"files": 20, "nodes": 24}}}
    v, r = V.classify(neo4j_ok=True, n_nodes=30, n_embedded=30, census={"Ruby": 40},
                      extractor="graphify", ml_ok=True, coverage=coverage)
    check("dead app/models -> DEGRADED (was READY: the field lie)", v == V.DEGRADED, f"{v} {r}")
    check("reason NAMES app/models", any("app/models" in x for x in r), str(r))
    check("healthy dirs not flagged", not any("app/controllers" in x for x in r), str(r))

    # Density floor, not strict zero: 12 files, 1 node is still a dead layer.
    coverage["dirs"]["app/models"] = {"files": 12, "nodes": 1}
    v, r = V.classify(neo4j_ok=True, n_nodes=31, n_embedded=31, census={"Ruby": 40},
                      extractor="graphify", ml_ok=True, coverage=coverage)
    check("1 stray node in 12 model files still -> DEGRADED (density, not zero/nonzero)",
          v == V.DEGRADED and any("app/models" in x for x in r), f"{v} {r}")

    # Healthy density -> not flagged.
    coverage["dirs"]["app/models"] = {"files": 12, "nodes": 10}
    v, r = V.classify(neo4j_ok=True, n_nodes=40, n_embedded=40, census={"Ruby": 40},
                      extractor="graphify", ml_ok=True, coverage=coverage)
    check("healthy model dir -> READY", v == V.READY, f"{v} {r}")

    # Tiny dirs don't fire (below the minimum-files floor).
    coverage["dirs"]["app/models"] = {"files": 2, "nodes": 0}
    v, r = V.classify(neo4j_ok=True, n_nodes=40, n_embedded=40, census={"Ruby": 40},
                      extractor="graphify", ml_ok=True, coverage=coverage)
    check("2-file dir below the floor -> not flagged", v == V.READY, f"{v} {r}")


def gate_classify_language_coverage() -> None:
    print("\n-- classify(coverage=...): ~0-node language -> DEGRADED (coarse fallback) --")
    coverage = {"languages": {"Ruby": {"files": 50, "nodes": 0},
                              "Python": {"files": 8, "nodes": 40}},
                "dirs": {}}
    v, r = V.classify(neo4j_ok=True, n_nodes=40, n_embedded=40,
                      census={"Ruby": 50, "Python": 8}, extractor="graphify", ml_ok=True,
                      coverage=coverage)
    check("50 Ruby files, 0 Ruby nodes -> DEGRADED", v == V.DEGRADED, f"{v} {r}")
    check("reason names Ruby", any("Ruby" in x for x in r), str(r))
    check("healthy Python not flagged", not any("Python" in x and "COVERAGE" in x for x in r), str(r))

    # A few unextracted files of a minor language don't fire (below the min-files floor).
    coverage = {"languages": {"Ruby": {"files": 3, "nodes": 0},
                              "Python": {"files": 100, "nodes": 400}}, "dirs": {}}
    v, r = V.classify(neo4j_ok=True, n_nodes=400, n_embedded=400,
                      census={"Ruby": 3, "Python": 100}, extractor="graphify", ml_ok=True,
                      coverage=coverage)
    check("3 stray .rb files -> not flagged (min-files floor)", v == V.READY, f"{v} {r}")


def gate_classify_backcompat() -> None:
    print("\n-- classify() without coverage: byte-compatible with today --")
    v, r = V.classify(neo4j_ok=True, n_nodes=1000, n_embedded=1000,
                      census={"Python": 50}, extractor="ast", ml_ok=True, mcp_ok=True)
    check("healthy python, no coverage arg -> READY (unchanged)", v == V.READY and r == [], f"{v} {r}")
    v, r = V.classify(neo4j_ok=True, n_nodes=1000, n_embedded=1000,
                      census={"Python": 50}, extractor="ast", ml_ok=True, mcp_ok=True,
                      coverage=None)
    check("coverage=None -> READY (explicit None is a no-op)", v == V.READY and r == [], f"{v} {r}")
    v, r = V.classify(neo4j_ok=True, n_nodes=10, n_embedded=10, census={"Python": 5},
                      coverage={"languages": "junk", "dirs": ["nope"]})
    check("malformed coverage degrades to no-coverage behavior, never raises",
          v == V.READY, f"{v} {r}")


def gate_format_coverage() -> None:
    print("\n-- format_coverage(): human lines for doctor/status --")
    fn = getattr(V, "format_coverage", None)
    check("format_coverage exists", callable(fn))
    if not callable(fn):
        return
    lines = fn({"languages": {"Ruby": {"files": 320, "nodes": 4}, "Python": {"files": 2, "nodes": 9}},
                "dirs": {"app/models": {"files": 12, "nodes": 0}}})
    joined = "\n".join(lines)
    check("returns a list of strings", isinstance(lines, list)
          and all(isinstance(x, str) for x in lines), str(type(lines)))
    check("shows Ruby files+nodes", "Ruby" in joined and "320" in joined and "4" in joined, joined)
    check("shows the flagged dir", "app/models" in joined, joined)
    check("never raises on junk", isinstance(fn(None), list) and isinstance(fn({"x": 1}), list))


def gate_manifest_carries_verdict() -> None:
    print("\n-- write_graph_build_manifest: optional verdict/reasons/coverage (additive) --")
    if not shutil.which("git"):
        check("(git on PATH for manifest checks)", True, "skipped — git absent")
        return
    S = _load("sync")
    with tempfile.TemporaryDirectory() as td:
        rd = Path(td)
        subprocess.run(["git", "init", "-q"], cwd=td, capture_output=True, timeout=60)
        subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=td, capture_output=True, timeout=60)
        subprocess.run(["git", "config", "user.name", "t"], cwd=td, capture_output=True, timeout=60)
        subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=td, capture_output=True, timeout=60)
        (rd / "a.py").write_text("x=1", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=td, capture_output=True, timeout=60)
        subprocess.run(["git", "commit", "-q", "-m", "i"], cwd=td, capture_output=True, timeout=60)

        cov = {"languages": {"Ruby": {"files": 6, "nodes": 0}}, "dirs": {"app/models": {"files": 4, "nodes": 0}}}
        out = S.write_graph_build_manifest(rd, n_nodes=3, n_edges=1, n_embedded=3,
                                           extractor="graphify", verdict="DEGRADED",
                                           reasons=["LOW COVERAGE in app/models/"], coverage=cov)
        check("manifest written with verdict kwargs", out is not None, str(out))
        data = json.loads((rd / ".deepmap" / "graph_build.json").read_text(encoding="utf-8"))
        check("manifest carries verdict", data.get("verdict") == "DEGRADED", str(data.get("verdict")))
        check("manifest carries reasons", data.get("reasons") == ["LOW COVERAGE in app/models/"],
              str(data.get("reasons")))
        check("manifest carries coverage", data.get("coverage") == cov, str(data.get("coverage")))
        check("existing keys intact (additive)", data.get("nodes") == 3
              and data.get("extractor") == "graphify" and "built_at_sha" in data, str(data.keys()))

        # Back-compat: the old call shape (no verdict kwargs) still writes, with the fields ABSENT.
        out2 = S.write_graph_build_manifest(rd, n_nodes=3, n_edges=1, n_embedded=3, extractor="graphify")
        check("old call shape still writes", out2 is not None)
        data2 = json.loads((rd / ".deepmap" / "graph_build.json").read_text(encoding="utf-8"))
        check("verdict/reasons/coverage ABSENT when not supplied",
              "verdict" not in data2 and "reasons" not in data2 and "coverage" not in data2,
              str(sorted(data2.keys())))


def gate_field_replay() -> None:
    print("\n-- END-TO-END field replay: Rails tree + dead model layer -> DEGRADED naming app/models --")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _rails_tree(root, model_files=4)
        nodes = [
            _node("OrdersController", "app/controllers/orders_controller.rb"),
            _node("OrdersController.index", "app/controllers/orders_controller.rb", "Method"),
            _node("Checkout", "app/services/checkout.rb"),
        ]
        census = V.extension_census(root)
        cov = V.build_coverage(root, nodes, census=census)
        v, r = V.classify(neo4j_ok=True, n_nodes=len(nodes), n_embedded=len(nodes),
                          census=census, extractor="graphify", ml_ok=True, coverage=cov)
        check("the original field state now classifies DEGRADED (was READY)",
              v == V.DEGRADED, f"{v} {r}")
        check("with app/models named in a reason", any("app/models" in x for x in r), str(r))


def gate_recognizer_scope_nodes_lift_coverage() -> None:
    print("\n-- A1: model-scope :Method nodes lift app/models coverage density (verdict improves) --")
    sys.path.insert(0, str(BUILD))
    import ruby_rails_edges as RR  # noqa: E402
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        models = root / "app" / "models"
        models.mkdir(parents=True)
        n_files = 12
        scope_nodes = []
        for i in range(n_files):
            rel = f"app/models/model_{i}.rb"
            src = ("class Model%d < ApplicationRecord\n"
                   "  scope :active, -> { where(active: true) }\n"
                   "  belongs_to :parent\nend\n" % i)
            (models / f"model_{i}.rb").write_text(src, encoding="utf-8")
            # A1's REAL output for this file: an ensured :Class node + a scope :Method node.
            scope_nodes += [n for n in RR.recognize(src, rel)[0] if n["label"] == "Method"]
        check("A1 emits one scope :Method node per model file",
              len(scope_nodes) == n_files and all(n["name"].endswith(".active") for n in scope_nodes),
              str(len(scope_nodes)))

        # Partial graphify extraction (a REAL failure mode): only 4 of 12 model classes parsed ->
        # app/models density 4/12 < 0.5 -> the dead-layer reason fires (DEGRADED).
        partial = [_node(f"Model{i}", f"app/models/model_{i}.rb") for i in range(4)]
        cov_partial = V.build_coverage(root, partial)
        r_partial = V._coverage_reasons(cov_partial)
        check("partial extraction (4 classes / 12 files) -> app/models flagged LOW COVERAGE",
              any("app/models/" in x for x in r_partial),
              str(cov_partial.get("dirs", {}).get("app/models")))

        # Add ONLY A1's scope :Method nodes (isolating this session's contribution): the count rises
        # and the density crosses the floor -> the LOW COVERAGE reason CLEARS. The verdict improves.
        cov_lifted = V.build_coverage(root, partial + scope_nodes)
        r_lifted = V._coverage_reasons(cov_lifted)
        d0 = cov_partial["dirs"]["app/models"]["nodes"]
        d1 = cov_lifted["dirs"]["app/models"]["nodes"]
        check("A1 scope :Method nodes add counted app/models density (the lever)",
              d1 == d0 + n_files, f"{d0} -> {d1}")
        check("with A1 scope nodes the app/models LOW COVERAGE reason CLEARS",
              not any("app/models/" in x for x in r_lifted), str(r_lifted))

        # classify() flips DEGRADED -> READY on the strength of A1's nodes alone (all else healthy).
        census = V.extension_census(root)
        v0, _ = V.classify(neo4j_ok=True, n_nodes=len(partial), n_embedded=len(partial),
                           census=census, extractor="graphify", ml_ok=True, coverage=cov_partial)
        lifted_n = len(partial) + len(scope_nodes)
        v1, _ = V.classify(neo4j_ok=True, n_nodes=lifted_n, n_embedded=lifted_n, census=census,
                           extractor="graphify", ml_ok=True, coverage=cov_lifted)
        check("classify(): partial -> DEGRADED, +A1 scope nodes -> READY (the coverage verdict improves)",
              v0 == V.DEGRADED and v1 == V.READY, f"{v0} -> {v1}")


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    os.environ.setdefault("GIT_CONFIG_NOSYSTEM", "1")
    print("=" * 64)
    print("DeepMap — verdict per-language/per-dir coverage gates (no Neo4j)")
    print("=" * 64)
    gate_build_coverage()
    gate_classify_dir_coverage()
    gate_classify_language_coverage()
    gate_classify_backcompat()
    gate_format_coverage()
    gate_manifest_carries_verdict()
    gate_field_replay()
    gate_recognizer_scope_nodes_lift_coverage()
    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
