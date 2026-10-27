#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ISS-build:016 + ISS-build:028 (fix round r283, owner O6) — build/graphify_extract.py: the G5 self-call re-binder
reads lines the way the ENGINE numbers them, and extract()'s docstring states the contract the code keeps.

ISS-build:016 (validated C2). graphify stamps a call with the line tree-sitter reports, and tree-sitter's row advances
on '\\n' ONLY; `_Source` splits text the same way ("so numbering matches the engine's"). The G5 re-binder's `line_text`
used str.splitlines(), which ALSO breaks on a form feed (the `^L` page break of older / GNU-style Python), \\x0b,
\\x1c-\\x1e, \\x85, U+2028 / U+2029 and a lone '\\r'. After one such character every later call line and class header was
read from the wrong line, so G5 silently skipped or mis-applied its correction:
  * `self.invoke()` in `class C(A)` (graphify's pick: the LAST `invoke`, B.invoke) was read as the `ctx.invoke()` line
    above it and became an AMBIGUOUS name fan-out instead of A.invoke;
  * on the REAL engine the shifted read found no call on the line at all, so graphify's wrong pick B.invoke was KEPT —
    one page break switched G5 off for the rest of the file;
  * the class-header read (`class T(Base[int]):`, a base the engine emits no inherits edge for) started on the wrong
    line, so a pick the caller's own header vouches for was replaced by the name fan-out.
The fix: both of the re-binder's reads (map_graph's shared `_Source`, and its no-source fallback) use the engine's
'\\n'-only rule.

ISS-build:028 (validated C3). extract()'s docstring promised None on a NON-ZERO engine exit; the code deliberately maps
a graph.json this run wrote whatever the exit code (F3e: graphify exits non-zero on benign per-file warnings —
tests/graphify_freshness_gates.py locks that), and a run that mapped 0 code nodes returns an EMPTY result, not None.
The docstrings (extract()'s and the module's) now state that contract; section E checks each claim against the code.

Sections: A the premise, on tree-sitter itself (skipped without the grammar) · B the real `_self_call_rebinder` reading
through map_graph's `_Source`, in memory · C its no-source fallback (a temp dir) · D the REAL engine on a page-broken
file (skipped without graphify; a temp DEEPMAP_HOME) · E extract()'s documented contract vs its behaviour (a fake
engine, a temp DEEPMAP_HOME). Hermetic: no Neo4j, no network, nothing written outside the temp dirs this gate creates.
Usage: python tests/o6_graphify_extract_r283_gates.py [--root <tree>]
"""
from __future__ import annotations

import ast
import contextlib
import importlib.util
import inspect
import io
import json
import os
import re
import sys
import tempfile
import textwrap
import time
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PASSES: list[str] = []
FAILS: list[str] = []

# Every character str.splitlines() treats as a line boundary while tree-sitter (the engine) does not: its row
# advances on '\n' only. A lone '\r' is one of them (a CRLF pair is not — both rules see one break there).
SEPARATORS = (("form feed", "\x0c"), ("vertical tab", "\x0b"), ("file separator", "\x1c"),
              ("group separator", "\x1d"), ("record separator", "\x1e"), ("NEL U+0085", "\x85"),
              ("LINE SEPARATOR U+2028", "\u2028"), ("PARAGRAPH SEPARATOR U+2029", "\u2029"), ("lone CR", "\r"))

# Extraction flags cleared for the real-engine run (the defaults are what is under test).
_FLAGS = ("DEEPMAP_PRECISE_CALLS", "DEEPMAP_GRAPHIFY_CALLS_PLUS", "DEEPMAP_GRAPHIFY_CALLS_PLUS_TESTS",
          "DEEPMAP_LOCAL_DEF_CALLS", "DEEPMAP_FALSE_CALL_FILTER", "DEEPMAP_DROP_APP_TO_TEST_CALLS",
          "DEEPMAP_SELF_CALL_REBIND", "DEEPMAP_EXTRACTOR", "DEEPMAP_EXCLUDE_TESTS", "DEEPMAP_INCLUDE_VENDOR",
          "DEEPMAP_RESOLVED_IMPORTS", "DEEPMAP_SUPPLEMENTAL_TS_RUST", "DEEPMAP_MODULE_DOCSTRINGS",
          "DEEPMAP_SUMMARY_NORMALIZE", "DEEPMAP_CASE_INSENSITIVE_EXT", "DEEPMAP_RESCUE_SKIPPED_DIRS",
          "DEEPMAP_CLEAN_OWNER_NAMES", "DEEPMAP_GRAPHIFY_TIMEOUT")


def check(name: str, ok: bool, detail="") -> None:
    (PASSES if ok else FAILS).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok or detail == "" else f" — {str(detail)[:600]}"))


def _load(root: Path):
    build = root / "build"
    sys.path.insert(0, str(build))       # its siblings (summarize, sync) load from the same tree
    spec = importlib.util.spec_from_file_location("_o6_gx_r283", build / "graphify_extract.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@contextlib.contextmanager
def _env(**kv):
    saved = {k: os.environ.get(k) for k in kv}
    try:
        for k, v in kv.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = str(v)
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ---- the two in-memory scenarios (a graphify-shaped graph + the source file it came from) -----------------------
SF = "pkg/pages.py"


def _node(label: str, line: int) -> dict:
    return {"label": label, "source_file": SF, "source_location": f"L{line}"}


def _s1_bytes(sep_line: str, eol: str = "\n") -> bytes:
    """The call line. `self.invoke()` (L12) in C(A).run: graphify's pick is the LAST `invoke` (B.invoke), the caller's
    MRO says A.invoke. `sep_line` is L8, between the classes; L11 is the `ctx.invoke()` a shifted read lands on."""
    return eol.join(["class A:", "    def invoke(self):", "        return 1", "",
                     "class B:", "    def invoke(self):", "        return 2", sep_line,
                     "class C(A):", "    def run(self, ctx):", "        ctx.invoke()",
                     "        return self.invoke()", ""]).encode("utf-8")


S1 = {"by_id": {"A": _node("A", 1), "A.invoke": _node(".invoke()", 2), "B": _node("B", 5),
                "B.invoke": _node(".invoke()", 6), "C": _node("C", 9), "C.run": _node(".run()", 10)},
      "kinds": {"A": "Class", "B": "Class", "C": "Class", "A.invoke": "Method", "B.invoke": "Method",
                "C.run": "Method"},
      "owner": {"A.invoke": "A", "B.invoke": "B", "C.run": "C"},
      "links": [{"relation": "inherits", "source": "C", "target": "A"},
                {"relation": "calls", "source": "C.run", "target": "B.invoke", "source_location": "L12"}],
      "call": ("C.run", "B.invoke"), "want": ("key", "A.invoke"), "bytes": _s1_bytes}


def _s2_bytes(sep_line: str, eol: str = "\n") -> bytes:
    """The class-header window. T(Base[int]) — the engine emits no inherits edge for a subscripted base — calls
    `self.load()` (L15); graphify's pick is the LAST `load` (Base.load) and T's own header names Base, so G5 keeps it.
    Two `sep_line`s (L4, L8) shift a splitlines() read by two lines: the call read still sees `self.load()` (L13-L15
    all make that call), but the header read starts on L9, a line ending ':', and never reaches `class T(Base[int]):`."""
    return eol.join(["class Other:", "    def load(self):", "        return 2", sep_line,
                     "class Base:", "    def load(self):", "        return 1", sep_line,
                     'ALIASES = {"load":', '           "fetch"}',
                     "class T(Base[int]):", "    def go(self):", "        self.load()", "        self.load()",
                     "        return self.load()", ""]).encode("utf-8")


S2 = {"by_id": {"Other": _node("Other", 1), "Other.load": _node(".load()", 2), "Base": _node("Base", 5),
                "Base.load": _node(".load()", 6), "T": _node("T", 11), "T.go": _node(".go()", 12)},
      "kinds": {"Other": "Class", "Base": "Class", "T": "Class", "Other.load": "Method", "Base.load": "Method",
                "T.go": "Method"},
      "owner": {"Other.load": "Other", "Base.load": "Base", "T.go": "T"},
      "links": [{"relation": "calls", "source": "T.go", "target": "Base.load", "source_location": "L15"}],
      "call": ("T.go", "Base.load"), "want": None, "bytes": _s2_bytes}


def _rebind(GX, sc: dict, data: bytes, *, via_source: bool):
    """The REAL `_self_call_rebinder` on scenario `sc` whose file holds `data`: reading through map_graph's `_Source`
    (in memory — a `_Source` whose reader hands over these bytes; nothing is read from disk) or through its no-source
    fallback (the bytes written to a temp dir this gate creates and removes)."""
    if via_source:
        class _MemSource(GX._Source):
            def raw(self, sf):
                return data if sf == SF else None

        root = Path(tempfile.gettempdir()) / "o6_r283_never_read"      # never touched: raw() serves the bytes
        rb = GX._self_call_rebinder(root, sc["links"], sc["by_id"], sc["owner"], sc["kinds"], source=_MemSource(root))
        return rb(sc["links"][-1], *sc["call"])
    with tempfile.TemporaryDirectory(prefix="o6_r283_fb_") as td:
        (Path(td) / "pkg").mkdir()
        (Path(td) / SF).write_bytes(data)
        rb = GX._self_call_rebinder(Path(td), sc["links"], sc["by_id"], sc["owner"], sc["kinds"])
        return rb(sc["links"][-1], *sc["call"])


def _variants():
    """(label, sep_line) per separator — inside a comment line — plus the classic `^L` alone on its line."""
    out = [(f"{name} in a comment", f"# ---- page{ch}break ----") for name, ch in SEPARATORS]
    out.insert(1, ("form feed alone on its line (the classic ^L page break)", "\x0c"))
    return out


# ---- A: the premise, on tree-sitter itself --------------------------------------------------------------------------
def _ts_parser():
    try:
        import tree_sitter as ts
        import tree_sitter_python as tsp
        lang = ts.Language(tsp.language())
        try:
            return ts.Parser(lang)
        except TypeError:
            p = ts.Parser()
            p.set_language(lang)
            return p
    except Exception as e:  # noqa: BLE001
        print(f"  [SKIP] tree-sitter's Python grammar is not usable here ({type(e).__name__}: {e})")
        return None


def gate_a_premise() -> None:
    print("\n-- A: the premise — the engine (tree-sitter) numbers rows on '\\n' only; splitlines() does not --")
    parser = _ts_parser()
    if parser is None:
        return
    for name, ch in SEPARATORS:
        data = _s1_bytes(f"# ---- page{ch}break ----")
        rows, stack = [], [parser.parse(data).root_node]
        while stack:
            n = stack.pop()
            if n.type == "call" and data[n.start_byte:n.end_byte] == b"self.invoke()":
                rows.append(n.start_point[0] + 1)
            stack.extend(n.children)
        text = data.decode("utf-8")
        check(f"{name}: tree-sitter puts `self.invoke()` on L12 — the '\\n'-only numbering (splitlines() says "
              f"L{next((i + 1 for i, s in enumerate(text.splitlines()) if 'self.invoke()' in s), '?')})",
              rows == [12] and "self.invoke()" not in text.splitlines()[11], rows)


# ---- B / C: the real re-binder ----------------------------------------------------------------------------------------
def gate_bc_rebinder(GX, *, via_source: bool) -> None:
    how = "map_graph's shared _Source (in memory)" if via_source else "the no-source fallback (read from disk)"
    tag = "B" if via_source else "C"
    print(f"\n-- {tag}: the real _self_call_rebinder reading through {how} --")
    for label, sc in (("call line", S1), ("class-header window", S2)):
        for eol_name, sep_line, eol in (("a blank line", "", "\n"), ("a CRLF file", "", "\r\n")):
            got = _rebind(GX, sc, sc["bytes"](sep_line, eol), via_source=via_source)
            check(f"{tag} {label}, control ({eol_name}): {sc['want']!r}", got == sc["want"], got)
        for name, sep_line in _variants():
            got = _rebind(GX, sc, sc["bytes"](sep_line), via_source=via_source)
            check(f"{tag} {label}, {name}: still {sc['want']!r} (the engine's line, not a shifted one)",
                  got == sc["want"], got)
    if via_source:
        tree = ast.parse(textwrap.dedent(inspect.getsource(GX._self_call_rebinder)))
        calls = [n.lineno for n in ast.walk(tree) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute) and n.func.attr == "splitlines"]
        check("B the re-binder calls no str.splitlines() (a rule the engine does not number lines by)",
              not calls, f"splitlines() call(s) at relative line(s) {calls}")


# ---- D: the REAL engine -------------------------------------------------------------------------------------------------
ENGINE_SRC = ("class A:\n"
              "    def invoke(self):\n"
              "        return 1\n"
              "\x0c\n"
              "class B:\n"
              "    def invoke(self):\n"
              "        return 2\n"
              "\x0c\n"
              "class C(A):\n"
              "    def run(self):\n"
              "        return self.invoke()\n")


def gate_d_real_engine(GX) -> None:
    print("\n-- D: the REAL engine on a file with ^L page breaks --")
    if importlib.util.find_spec("graphify") is None:
        print("  [SKIP] graphify is not installed in this interpreter — the real-engine section is not run")
        return
    with tempfile.TemporaryDirectory(prefix="o6_r283_eng_") as td:
        tmp = Path(td).resolve()
        root = tmp / "proj"
        (root / "pkg").mkdir(parents=True)
        (root / SF).write_bytes(ENGINE_SRC.encode("utf-8"))
        results = {}
        for mode, extra in (("default", {}), ("precise", {"DEEPMAP_PRECISE_CALLS": "1"})):
            env = {f: None for f in _FLAGS}
            env.update(extra, DEEPMAP_HOME=str(tmp / "home"))
            with _env(**env), contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
                res, rep = GX.extract_with_report(root)
            if not res:
                check(f"D real engine ({mode}) produced a graph", False, rep.get("cause"))
                return
            results[mode] = (res, rep)
        res, rep = results["default"]
        graph = json.loads((Path(rep["graphify_out"]) / "graph.json").read_text(encoding="utf-8"))
        ids = {n.get("id"): n for n in graph.get("nodes", [])}
        text = ENGINE_SRC
        run_id = next((i for i, n in ids.items() if str(n.get("label", "")).strip(".") == "run()"), None)
        links = [l for l in graph.get("links", []) if l.get("relation") == "calls" and l.get("source") == run_id
                 and str((ids.get(l.get("target")) or {}).get("label", "")).strip(".") == "invoke()"]
        if not links:
            print("  [SKIP] this engine emitted no C.run -> invoke call for the fixture — nothing to re-bind here")
            return
        src = GX._Source(root)
        for l in links:
            n = GX._line_of(l)
            check(f"D the engine's L{n} for C.run -> invoke is the `self.invoke()` line under the '\\n'-only rule "
                  f"(and not under splitlines(), which the page breaks shift)",
                  "self.invoke()" in (src.line(SF, n) or "")
                  and "self.invoke()" not in (text.splitlines()[n - 1] if n <= len(text.splitlines()) else ""),
                  (n, src.line(SF, n)))
        nodes, calls, _imports = res
        by_name = {nd.get("name"): nd.get("key") for nd in nodes}
        caller, a_key, b_key = by_name.get("C.run"), by_name.get("A.invoke"), by_name.get("B.invoke")
        mine = sorted(str(c) for s, c in calls if s == caller)
        check("D default flags: C.run's `self.invoke()` lands on the caller's base A.invoke, never on graphify's "
              "last-definition pick B.invoke", "A.invoke" in mine and "B.invoke" not in mine, mine)
        if any(str((ids.get(l.get("target")) or {}).get("source_location", "")) == "L6" for l in links):
            check("D the report counts the re-bound call (this engine picked B.invoke, defined on L6)",
                  int(rep.get("self_calls_rebound") or 0) >= 1, rep.get("self_calls_rebound"))
        pres, _prep = results["precise"]
        pby = {nd.get("name"): nd.get("key") for nd in pres[0]}
        pmine = [c for s, c in pres[1] if s == pby.get("C.run")]
        check("D DEEPMAP_PRECISE_CALLS=1: C.run's call is keyed to A.invoke's node, not B.invoke's",
              pby.get("A.invoke") in pmine and pby.get("B.invoke") not in pmine, (pmine, a_key, b_key))


# ---- E: extract()'s documented contract --------------------------------------------------------------------------------
_FRESH = {"nodes": [{"id": "n1", "label": "foo()", "file_type": "code", "source_file": "a.py",
                     "source_location": "L1"}], "links": []}
_NO_CODE = {"nodes": [{"id": "d1", "label": "README", "file_type": "document", "source_file": "README.md",
                       "source_location": "L1"}], "links": []}


def _fake_engine_run(GX, tmp: Path, *, rc: int, write, stale: bool = False):
    """extract(root) with the engine faked through _run_engine's documented test seam (a replaced subprocess.run) and
    a fake `graphify` module (so find_spec passes). `write` = the graph the fake engine leaves in graph.json (None:
    none); `stale` = that graph.json was NOT rewritten by this run (its mtime is an hour old — what an engine leaves
    behind when the clearing of the previous file failed). Returns (extract() result, extract_with_report() report,
    whether the fake engine was called)."""
    root = tmp / f"proj_{rc}_{bool(write)}_{stale}"
    root.mkdir()
    env = {f: None for f in _FLAGS}
    env.update(DEEPMAP_HOME=str(tmp / "home"), DEEPMAP_RESCUE_SKIPPED_DIRS="0")
    engine_calls: list = []
    with _env(**env):
        gpath = GX.graphify_out_dir(root) / "graph.json"
        gpath.parent.mkdir(parents=True, exist_ok=True)

        def fake_run(cmd, **_kw):
            engine_calls.append(list(map(str, cmd)))
            if write is not None:
                gpath.write_text(json.dumps(write), encoding="utf-8")
                if stale:
                    old = time.time() - 3600
                    os.utime(gpath, (old, old))
            return types.SimpleNamespace(returncode=rc, stdout="",
                                         stderr="graphify: warning: skipped 1 file" if rc else "")

        saved_mod, saved_run = sys.modules.get("graphify"), GX.subprocess.run
        fake = types.ModuleType("graphify")
        fake.__spec__ = importlib.util.spec_from_loader("graphify", loader=None)
        sys.modules["graphify"] = fake
        GX.subprocess.run = fake_run
        try:
            with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
                out = GX.extract_with_report(root)
                res = GX.extract(root)
        finally:
            GX.subprocess.run = saved_run
            if saved_mod is None:
                sys.modules.pop("graphify", None)
            else:
                sys.modules["graphify"] = saved_mod
    ran = any("graphify" in " ".join(c) for c in engine_calls)
    return res, out[1], ran


def gate_e_extract_contract(GX) -> None:
    print("\n-- E: extract()'s docstring states the contract the code keeps (ISS-build:028) --")
    doc = " ".join((GX.extract.__doc__ or "").split())
    mod_doc = " ".join((GX.__doc__ or "").split())
    check("E the module docstring no longer says extract() returns None on ANY failure",
          not re.search(r"None on any failure", mod_doc, re.I),
          re.findall(r"[^.;]*None on any failure[^.;]*", mod_doc, re.I))
    m = re.search(r"\bNone\b(.*?)fall back", doc, re.S)
    clause = m.group(1) if m else doc
    check("E extract(): the list of None cases names no non-zero exit (a non-zero exit is not one of them)",
          m is not None and not re.search(r"non-?zero|return ?code|\brc\b", clause, re.I), clause)
    near = [doc[i.start():i.start() + 200] for i in re.finditer(r"non-?zero", doc, re.I)]
    check("E extract(): says a non-zero exit with a graph.json THIS run wrote is still mapped",
          any(re.search(r"this run wrote|freshly written|written by this run", s, re.I) and re.search(r"mapped", s, re.I)
              for s in near), near)
    check("E extract(): says a mapped result can be PARTIAL or hold 0 code nodes (an empty result, not None)",
          re.search(r"partial", doc, re.I) is not None and re.search(r"\b0 code nodes\b", doc) is not None, doc)

    with tempfile.TemporaryDirectory(prefix="o6_r283_ext_") as td:
        tmp = Path(td).resolve()
        res, rep, ran = _fake_engine_run(GX, tmp, rc=1, write=_FRESH)
        check("E behaviour: a NON-ZERO exit + a graph.json this run wrote -> a mapped result (not None)",
              ran and isinstance(res, tuple) and any(n.get("name") == "foo" for n in res[0]), (ran, res, rep.get("cause")))
        res, rep, ran = _fake_engine_run(GX, tmp, rc=0, write=_NO_CODE)
        check("E behaviour: a fresh graph with 0 code nodes -> an EMPTY result (not None) whose report names the cause",
              ran and isinstance(res, tuple) and res[0] == [] and "0 code nodes" in str(rep.get("cause")),
              (ran, res, rep.get("cause")))
        res, rep, ran = _fake_engine_run(GX, tmp, rc=1, write=None)
        check("E behaviour: graph.json missing -> None", ran and res is None, (ran, res, rep.get("cause")))
        res, rep, ran = _fake_engine_run(GX, tmp, rc=0, write=_FRESH, stale=True)
        check("E behaviour: a STALE graph.json (not rewritten by this run) -> None, even on exit 0",
              ran and res is None and "STALE" in str(rep.get("cause")), (ran, res, rep.get("cause")))


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    root = REPO
    if "--root" in sys.argv:
        root = Path(sys.argv[sys.argv.index("--root") + 1]).resolve()
    print("=" * 76)
    print(f"DeepMap — O6 r283: G5 reads the engine's lines; extract()'s documented contract (root: {root})")
    print("=" * 76)
    GX = _load(root)
    gate_a_premise()
    gate_bc_rebinder(GX, via_source=True)
    gate_bc_rebinder(GX, via_source=False)
    gate_d_real_engine(GX)
    gate_e_extract_contract(GX)
    total = len(PASSES) + len(FAILS)
    print("\n" + "=" * 76)
    if FAILS:
        print(f"RESULT: RED — {len(PASSES)}/{total} gates passed.")
        return 1
    print(f"RESULT: GREEN — {len(PASSES)}/{total} gates passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
