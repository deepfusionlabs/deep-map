#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gate: Delphi / Object Pascal must survive the extractor allowlist, and an unextracted language
must NEVER report READY (2026-08 field report). No Neo4j, no network, no graphify run required.

THE FIELD REPORT (a real Delphi/Pascal project):
  "DeepMap can technically analyse Pascal, but it has a hard-coded allowlist of permitted file
   extensions called SOURCE_CODE_EXTS. Delphi's extensions (.pas, .dpr, .dpk, .inc, .pp) are not in
   that list, so the system sees the files and then automatically rejects them because of their
   extension ... deepmap_search / deepmap_impact are searching a graph that is EMPTY of Delphi. The
   READY message they show is misleading."

Both halves were VERIFIED against source + the real upstream engine before this gate was written:

  A) THE DROP. graphify (the upstream engine DeepMap shells out to) has a first-class Pascal
     extractor wired for .pas/.pp/.dpr/.dpk/.lpr/.inc (its `_DISPATCH` -> `extract_pascal`), and on a
     real 3-file Delphi sample it emitted 10 typed nodes + a resolved `calls` edge. DeepMap's
     `_is_code_node` then dropped 100% of them on the extension check: map_graph returned
     (0 nodes, 0 calls, 0 imports). The engine was never the limit — DeepMap's own allowlist was.
     tests/fixtures/graphify_multilang/delphi_pascal.json is that REAL captured graph.

  B) THE MISLEADING VERDICT. The honesty layer (build/verdict.py) can only flag a language it can
     SEE: its census map `_SOURCE_EXTS` had no Pascal entry either, so a Delphi repo's .pas files
     counted as nothing on disk, no LOW-COVERAGE reason could fire, and a graph carrying only the
     project's few .sql/.js nodes was classified READY. The census is what makes "0 Delphi nodes"
     visible, so it must know Pascal INDEPENDENTLY of whether extraction succeeds.

Why (B) still matters after (A) is fixed — it is DEFENCE IN DEPTH, not a workaround. graphify's
dispatch is case-SENSITIVE (`_DISPATCH.get(path.suffix)`), so a legacy `UNIT1.PAS` / `MAIN.DPR` (the
common casing in older Windows Delphi trees) was skipped by the ENGINE and yielded 0 nodes. DeepMap
now closes that too, by aliasing odd-case suffixes onto the lowercase extractor at extraction time
(`build/graphify_case_shim.py`, locked by `tests/case_insensitive_ext_gates.py`) — no renaming of
anyone's repo required. The census still counts extensions case-INSENSITIVELY so that if that path
is switched off (`DEEPMAP_CASE_INSENSITIVE_EXT=0`) or a future engine changes shape, an unextracted
layer STILL reports DEGRADED with a named reason instead of a confident READY. That is the whole
point of the wedge: when we cannot extract, we must say so.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "build"))

import graphify_extract as G  # noqa: E402
import verdict as V  # noqa: E402

FIXTURE = REPO / "tests" / "fixtures" / "graphify_multilang" / "delphi_pascal.json"

_PASS = _FAIL = 0


def check(desc: str, cond: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    ok = bool(cond)
    _PASS += ok
    _FAIL += (not ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {desc}" + ("" if ok else f"  -- {detail}"))


# --- A. the reported defect: a real captured Delphi graph must map to typed nodes ----------------
def gate_real_delphi_graph_maps() -> None:
    print("\n-- A. REAL captured graphify Delphi graph -> typed DeepMap nodes (the reported drop) --")
    graph = json.loads(FIXTURE.read_text(encoding="utf-8"))
    nodes, calls, imports = G.map_graph(graph)

    check("the captured Delphi graph is non-trivial (guards a hollow fixture)",
          len(graph.get("nodes", [])) >= 10 and len(graph.get("links", [])) >= 10,
          f"{len(graph.get('nodes', []))} nodes / {len(graph.get('links', []))} links")
    # The regression the field report is about: this was EXACTLY 0 before the fix.
    check("Delphi nodes are NOT dropped by the extension allowlist", len(nodes) > 0,
          "map_graph returned 0 nodes — .pas/.dpr/.inc still rejected by SOURCE_CODE_EXTS")

    by_name = {n["name"]: n for n in nodes}
    for name, label in (("TOrderService", "Class"),
                        ("TOrderService.PlaceOrder", "Method"),
                        ("TOrderService.Compute", "Method"),
                        ("Checkout", "Function"),
                        ("DoubleIt", "Function")):
        got = by_name.get(name, {}).get("label")
        check(f"{name} types as :{label}", got == label, f"got {got!r}")

    mods = {n["name"] for n in nodes if n["label"] == "Module"}
    check(".pas unit file -> :Module", "OrderService" in mods, sorted(mods))
    check(".dpr program file -> :Module", "Main" in mods, sorted(mods))
    check(".inc include file -> :Module", "Helpers" in mods, sorted(mods))

    check("the intra-unit CALLS edge resolves (PlaceOrder -> Compute)",
          ("src_orderservice_torderservice_placeorder", "TOrderService.Compute") in calls,
          calls)
    check("every node carries a real source_file + line",
          all(n["source_file"].endswith((".pas", ".dpr", ".inc")) and n["start_line"] >= 1
              for n in nodes),
          [(n["name"], n["source_file"], n["start_line"]) for n in nodes][:4])
    check("source_file separators are posix-normalized (OS-portable)",
          all("\\" not in n["source_file"] for n in nodes))


# --- B. allowlist semantics: code in, designer/manifest data out ---------------------------------
def gate_extension_allowlist() -> None:
    print("\n-- B. SOURCE_CODE_EXTS: Pascal code accepted; Delphi DESIGNER/manifest data still cut --")
    for ext in (".pas", ".pp", ".dpr", ".dpk", ".lpr", ".inc"):
        check(f"{ext} is an accepted source extension",
              G._is_code_node({"file_type": "code", "source_file": f"src/Unit1{ext}"}),
              "rejected")
    # The allowlist EXISTS to keep data/markup out (a JSON key would otherwise become a bogus Class).
    # Delphi/Lazarus form + package files are designer DATA, not code — they must stay excluded.
    for ext in (".dfm", ".lfm", ".lpk"):
        check(f"{ext} (Delphi/Lazarus designer/package data) stays EXCLUDED",
              not G._is_code_node({"file_type": "code", "source_file": f"src/Unit1{ext}"}),
              "leaked in as code")
    for ext in (".json", ".md", ".yaml", ".dfm"):
        check(f"{ext} still produces no Module node from a file-shaped label",
              not G._looks_like_file(f"config{ext}"), "treated as a source file")
    check("a rationale node is still dropped regardless of extension",
          not G._is_code_node({"file_type": "rationale", "source_file": "src/Unit1.pas"}))
    check("pre-existing languages are untouched (.py/.ts/.rb/.cs/.go)",
          all(G._is_code_node({"file_type": "code", "source_file": f"a{e}"})
              for e in (".py", ".ts", ".rb", ".cs", ".go")))


def gate_case_insensitive() -> None:
    print("\n-- C. extension matching is case-insensitive (legacy Windows Delphi: UNIT1.PAS) --")
    for sf in ("src/UNIT1.PAS", "src/MAIN.DPR", "src/Helpers.Inc"):
        check(f"_is_code_node accepts {sf}",
              G._is_code_node({"file_type": "code", "source_file": sf}), "rejected")
    for label in ("UNIT1.PAS", "MAIN.DPR", "Unit1.Pas"):
        check(f"_looks_like_file accepts {label} (file -> :Module, not a bogus Class)",
              G._looks_like_file(label), "not recognized as a file label")


# --- D. the honesty wedge: an unextracted language must not report READY -------------------------
def _delphi_tree(root: Path, *, n_pas: int = 14, uppercase: bool = False) -> None:
    src = root / "src"
    src.mkdir(parents=True, exist_ok=True)
    ext = ".PAS" if uppercase else ".pas"
    for i in range(n_pas):
        (src / f"Unit{i}{ext}").write_text("unit U;\ninterface\nimplementation\nend.\n",
                                           encoding="utf-8")
    (src / ("MAIN.DPR" if uppercase else "Main.dpr")).write_text("program Main;\nbegin\nend.\n",
                                                                 encoding="utf-8")
    (root / "tools").mkdir(exist_ok=True)
    (root / "tools" / "deploy.py").write_text("def go():\n    return 1\n", encoding="utf-8")


def gate_census_sees_pascal() -> None:
    print("\n-- D. verdict census sees Delphi (so the honesty layer can flag it) --")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _delphi_tree(root)
        census = V.extension_census(root)
        check("the census counts .pas/.dpr files as a Pascal/Delphi language",
              census.get("Pascal/Delphi", 0) >= 15, census)
        check("the census still counts the Python tooling file", census.get("Python", 0) == 1, census)

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _delphi_tree(root, uppercase=True)
        census = V.extension_census(root)
        # The engine's own dispatch cannot parse UPPERCASE extensions; DeepMap now aliases them in
        # (graphify_case_shim), but the census must see them regardless — it is the backstop that
        # keeps the verdict honest if that aliasing is disabled or the engine's table moves.
        check("the census counts UPPERCASE .PAS/.DPR too (the case graphify cannot extract)",
              census.get("Pascal/Delphi", 0) >= 15, census)


def gate_verdict_not_ready_when_delphi_invisible() -> None:
    print("\n-- E. THE REPORTED HARM: a Delphi graph with no Delphi nodes must NOT say READY --")
    census = {"Pascal/Delphi": 120, "SQL": 4}
    # The field shape: the graph is NOT empty (a few .sql/.js nodes got in), so every global-count
    # check passes and the pre-fix verdict was a confident READY over a Delphi-free graph.
    nodes = [{"name": "usp_get_order", "label": "Function", "source_file": "db/procs.sql"},
             {"name": "usp_put_order", "label": "Function", "source_file": "db/procs2.sql"}]
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _delphi_tree(root, n_pas=120)
        coverage = V.build_coverage(root, nodes, census=census)
        v, reasons = V.classify(neo4j_ok=True, n_nodes=len(nodes), n_embedded=len(nodes),
                                census=census, extractor="auto", ml_ok=True, mcp_ok=True,
                                coverage=coverage)
    check("verdict is DEGRADED, not READY", v == V.DEGRADED, f"{v}: {reasons}")
    check("a reason NAMES Pascal/Delphi as the invisible layer",
          any("Pascal/Delphi" in r for r in reasons), reasons)
    check("the reason states the practical consequence (invisible to search/impact)",
          any("invisible" in r.lower() for r in reasons), reasons)
    check("DEGRADED exits 0 (search still works); the unwired exit_code helper maps strict=True to 3 — no "
          "command has a --strict flag",
          V.exit_code(v, strict=True) == 3 and V.exit_code(v) == 0)

    # ... and once extraction WORKS, the same repo must go back to READY (no permanent false alarm).
    good = [{"name": f"U{i}", "label": "Module", "source_file": f"src/Unit{i}.pas"} for i in range(120)]
    cov2 = V.build_coverage(REPO, good, census={"Pascal/Delphi": 120})
    v2, r2 = V.classify(neo4j_ok=True, n_nodes=len(good), n_embedded=len(good),
                        census={"Pascal/Delphi": 120}, extractor="auto", ml_ok=True, mcp_ok=True,
                        coverage=cov2)
    check("a WELL-extracted Delphi repo is READY again (no permanent false DEGRADED)",
          v2 == V.READY, f"{v2}: {r2}")

    # A pure-Delphi repo that extracted nothing at all must FAIL, and must not blame a missing path.
    v3, r3 = V.classify(neo4j_ok=True, n_nodes=0, n_embedded=0, census={"Pascal/Delphi": 40},
                        extractor="auto")
    check("a 0-node Delphi repo is FAILED", v3 == V.FAILED, f"{v3}: {r3}")
    check("the 0-node reason does NOT claim 'no supported source files were found'",
          not any("no supported source files" in r for r in r3), r3)


# --- F. drift lock: the allowlist must not silently fall behind the engine again -----------------
def gate_no_silent_engine_drift() -> None:
    print("\n-- F. drift lock: every extension the ENGINE parses is a decided case, not an oversight --")
    check("SOURCE_CODE_EXTS entries are lowercase + dot-prefixed (matching is lowercased)",
          all(e.startswith(".") and e == e.lower() for e in G.SOURCE_CODE_EXTS),
          [e for e in G.SOURCE_CODE_EXTS if not e.startswith(".") or e != e.lower()])
    excluded = getattr(G, "EXCLUDED_BY_DESIGN_EXTS", None)
    check("the deliberate-exclusion set is documented in code",
          isinstance(excluded, frozenset) and len(excluded) > 0, repr(excluded)[:80])
    excluded = excluded if isinstance(excluded, frozenset) else frozenset()
    check("no extension is both allowed and excluded",
          not (set(G.SOURCE_CODE_EXTS) & excluded),
          sorted(set(G.SOURCE_CODE_EXTS) & excluded))
    check("the Delphi family is on the ALLOWED side of that decision",
          {".pas", ".pp", ".dpr", ".dpk", ".lpr", ".inc"} <= set(G.SOURCE_CODE_EXTS))
    check("Delphi/Lazarus designer files are on the EXCLUDED side",
          {".dfm", ".lfm", ".lpk"} <= excluded)

    # The real engine, when installed: nothing it parses may be UNCLASSIFIED. This is the check that
    # would have caught the reported bug the day graphify shipped extract_pascal.
    try:
        import importlib.util
        if importlib.util.find_spec("graphify") is None:
            print("     (graphify not installed — engine-drift comparison skipped)")
            return
        import re
        import graphify.extract as GX  # noqa: F401
        src = Path(GX.__file__).read_text(encoding="utf-8", errors="replace")
        m = re.search(r"_DISPATCH: dict\[str, Any\] = \{(.*?)\n\}", src, re.S)
        if not m:
            print("     (graphify _DISPATCH table not found — comparison skipped)")
            return
        parsed = {e.lower() for e in re.findall(r"\"(\.[A-Za-z0-9]+)\"", m.group(1))}
    except Exception as e:  # noqa: BLE001 — an engine probe must never break the gate
        print(f"     (engine probe unavailable: {type(e).__name__} — skipped)")
        return

    decided = {e.lower() for e in G.SOURCE_CODE_EXTS} | {e.lower() for e in excluded}
    undecided = sorted(parsed - decided)
    check("every extension graphify parses is either allowed or deliberately excluded",
          not undecided,
          f"UNDECIDED (engine parses, DeepMap silently drops): {undecided} — add each to "
          f"SOURCE_CODE_EXTS or EXCLUDED_BY_DESIGN_EXTS")
    check("the engine really does parse the Delphi family (the report's premise)",
          {".pas", ".dpr", ".inc"} <= parsed, sorted(parsed)[:20])


def gate_census_covers_extractor() -> None:
    print("\n-- G. census/extractor drift lock: an extracted language must be a COUNTABLE language --")
    missing = sorted(e for e in G.SOURCE_CODE_EXTS if e not in V._SOURCE_EXTS)
    check("every extension DeepMap extracts has a verdict-census language",
          not missing,
          f"{missing} — extracted but invisible to the census, so ~0 nodes for them can never "
          f"raise a LOW-COVERAGE reason (this is the exact defect the field report hit)")


def gate_decided_lists() -> None:
    print("\n-- H. the 2026-11 lists are decided cases of the allowlist, never a third way to drop a language --")
    import sql_reader
    allowed = set(G.SOURCE_CODE_EXTS)
    excluded = set(getattr(G, "EXCLUDED_BY_DESIGN_EXTS", ()) or ())
    unparsed = set(getattr(G, "ENGINE_UNPARSED_EXTS", ()) or ())
    optional = dict(getattr(G, "OPTIONAL_PARSERS", {}) or {})
    check("DeepMap's own reader's suffixes (sql_reader.EXTS) are allowed and are the census language SQL",
          set(sql_reader.EXTS) <= allowed and all(V._SOURCE_EXTS.get(e) == "SQL" for e in sql_reader.EXTS),
          sql_reader.EXTS)
    check("ENGINE_UNPARSED_EXTS and OPTIONAL_PARSERS name allowed suffixes only (never an excluded one)",
          bool(unparsed) and unparsed | set(optional) <= allowed and not ((unparsed | set(optional)) & excluded),
          sorted((unparsed | set(optional)) - allowed))
    check("not_parsed_here() names allowed suffixes only", set(G.not_parsed_here()) <= allowed,
          sorted(set(G.not_parsed_here()) - allowed))
    check("engine_parseable_exts() is a subset of the allowlist and keeps the Delphi family",
          set(G.engine_parseable_exts()) <= allowed
          and {".pas", ".pp", ".dpr", ".dpk", ".lpr", ".inc"} <= set(G.engine_parseable_exts()))


def main() -> int:
    print("=" * 78)
    print("DELPHI / OBJECT PASCAL GATES — extractor allowlist + verdict honesty (no Neo4j)")
    print("=" * 78)
    gate_real_delphi_graph_maps()
    gate_extension_allowlist()
    gate_case_insensitive()
    gate_census_sees_pascal()
    gate_verdict_not_ready_when_delphi_invisible()
    gate_no_silent_engine_drift()
    gate_census_covers_extractor()
    gate_decided_lists()
    print("\n" + "=" * 78)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if not _FAIL else 'RED'} — {_PASS}/{total} checks passed.")
    return 0 if not _FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
