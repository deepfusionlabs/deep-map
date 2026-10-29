#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""C1 gates (plan phase 2, call-site lines): map_graph(..., call_lines=True) records each caller's FIRST call-site line.
No Neo4j, no network, no spend; the real-engine checks run the installed graphify under a temporary DEEPMAP_HOME.

graphify records the line of the first call per (caller, callee) pair on each `calls` link (`source_location`
"L<n>"), and map_graph used to drop it: impact rows could only point at the caller's first line. With call_lines=True
(extract_with_report always passes it) every caller with at least one call whose line is known gets the private
`call_lines` {"key": {target key: line}, "name": {callee name: line}} — the lowest line of the engine links behind
each of its FINAL tuples (a key tuple also under its target's name) — which sync._call_lines turns into r.line on the
pushed :CALLS edges. It is never pushed itself, and call_lines=False (the default) leaves the output exactly as it was.

Sections:
  A  the switch: map_graph takes `call_lines` (default False); extract_with_report passes True (a faked engine run
     maps exactly map_graph(call_lines=True) of its graph.json), and on the installed engine every recorded line of a
     small real tree is a source line that calls that name.
  B  identity on EVERY captured graph tests/precise_family_gates.entries() holds (both engine buckets): map_graph()
     is still the frozen pre-S1 record (precise_family_gates B's modes), map_graph(call_lines=True) minus the new
     attribute is map_graph() in every mode, the attribute's shape (plain str -> positive int, only on callers), and
     the multilang graphs (no source_location on their links) get none.
  C  the line oracle, independent of the recording code: map each engine call link ALONE (every other call link
     removed) and attribute the line to the final tuples it produces; the recorded lines equal that oracle's minimum
     on every captured graph with lines (defaults and PRECISE=1, which keys calls and runs the TS re-key) and on the
     C# members captures.
  D  engine parity: the precise and r8 trees, captured on graphify 0.8.39 AND 0.8.51, record the same lines (callers
     and targets compared by name, file and line, not by engine id).
  E  synthetic cases: the minimum over repeated links and same-named targets, a key and its name, unknown / zero
     lines recorded nowhere, keys and names are plain str (never a CallKey).
  F  mutation teeth: recording with call_lines=False, the minimum dropped (last line wins) and extract_with_report
     not asking for lines each turn a section RED.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build"
sys.path.insert(0, str(BUILD))
sys.path.insert(0, str(REPO / "tests"))

import graphify_extract as GX  # noqa: E402
import precise_family_gates as PF  # noqa: E402

_HAS_ENGINE = importlib.util.find_spec("graphify") is not None
_PASS = _FAIL = 0
_QUIET: list = []          # while a mutant runs, checks are counted here instead of printed


def check(label: str, cond: bool, detail="") -> None:
    global _PASS, _FAIL
    if _QUIET:
        _QUIET[-1].append(bool(cond))
        return
    if cond:
        _PASS += 1
        print(f"  [PASS] {label}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}{(' — ' + str(detail)[:700]) if detail != '' else ''}")


@contextlib.contextmanager
def quiet():
    """Run checks without printing or counting them; yields the list of their outcomes (a mutant's run)."""
    box: list = []
    _QUIET.append(box)
    try:
        yield box
    finally:
        _QUIET.pop()


_LINE = re.compile(r"(?:^|[^A-Za-z])L(\d+)")       # the engine writes "L<n>" (the oracle's own reader)


def link_line(link):
    m = _LINE.search(str(link.get("source_location") or ""))
    return int(m.group(1)) if m else None


def run(gx, graph: dict, root, env: dict, *, call_lines: bool = False, source=None):
    """map_graph of module `gx` on a COPY of `graph` under exactly `env` (call_lines passed only when True, so the
    default-argument path is the one exercised otherwise)."""
    kw = {"call_lines": True} if call_lines else {}
    if source is not None:
        kw["source"] = source
    with PF.clean_env(**env):
        return gx.map_graph(json.loads(json.dumps(graph)), root=root, **kw)


def strip(nodes) -> list:
    out = []
    for n in nodes:
        n = dict(n)
        n.pop("call_lines", None)
        out.append(n)
    return out


def recorded(nodes) -> dict:
    return {n["key"]: n["call_lines"] for n in nodes if "call_lines" in n}


def has_lines(graph: dict) -> bool:
    return any(l.get("relation") == "calls" and link_line(l) for l in graph.get("links", []))


# ================================================================================================
# A — the switch
# ================================================================================================
def fake_extract(gx, graph: dict, files: dict, env: dict, name: str):
    """extract_with_report of module `gx` on `files` with the engine faked: its graph.json is `graph`."""
    root = PF.write_tree(PF.tmp() / f"cl_fake_{name}", files)
    out = PF.tmp() / f"cl_fake_out_{name}"
    out.mkdir(parents=True, exist_ok=True)

    class _P:
        returncode, stdout, stderr = 0, "", ""

    def fake_engine(cmd, *, cwd, env, timeout):
        (out / "graph.json").write_text(json.dumps(graph), encoding="utf-8")
        return _P()

    real_find_spec = importlib.util.find_spec

    def find_spec(n, *a, **k):
        return object() if n == "graphify" else real_find_spec(n, *a, **k)

    saved = (gx._run_engine, gx.graphify_out_dir, gx.graphify_version)
    try:
        gx._run_engine = fake_engine
        gx.graphify_out_dir = lambda r: out
        gx.graphify_version = lambda: "0.8.39"
        importlib.util.find_spec = find_spec
        with PF.clean_env(DEEPMAP_HOME=str(PF.tmp() / "cl_home"), DEEPMAP_RESCUE_SKIPPED_DIRS="0", **env), \
                contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            res, _rep = gx.extract_with_report(root)
    finally:
        gx._run_engine, gx.graphify_out_dir, gx.graphify_version = saved
        importlib.util.find_spec = real_find_spec
    return res, root.resolve()


def section_a(gx, *, real: bool = True) -> None:
    import inspect
    p = inspect.signature(gx.map_graph).parameters.get("call_lines")
    check("A map_graph takes a keyword `call_lines`, default False", p is not None and p.default is False
          and p.kind is inspect.Parameter.KEYWORD_ONLY)
    graph = PF.precise_graph("0839", "probe_py")
    files = PF.TREES["probe_py"]
    for env in ({}, {"DEEPMAP_PRECISE_CALLS": "1"}):
        res, root = fake_extract(gx, graph, files, env, "probe" + ("_p1" if env else ""))
        want = run(gx, graph, root, env, call_lines=True)
        check(f"A extract_with_report (engine faked{', PRECISE=1' if env else ''}) maps map_graph(call_lines=True) of "
              f"its graph.json: the call lines ride along", res is not None and list(res[0]) == list(want[0])
              and bool(recorded(res[0])), (None if res is None else len(recorded(res[0]))))
    if not real:
        return
    if not _HAS_ENGINE:
        print("  [SKIP] graphify is not installed — the real-engine line check is not run")
        return
    root = PF.write_tree(PF.tmp() / "cl_real_probe", files).resolve()
    with PF.clean_env(DEEPMAP_HOME=str(PF.tmp() / "cl_real_home")), contextlib.redirect_stderr(io.StringIO()), \
            contextlib.redirect_stdout(io.StringIO()):
        res, rep = gx.extract_with_report(root)
    if res is None:
        check("A the installed engine extracted the probe tree", False, rep.get("cause"))
        return
    bad, n = [], 0
    for nd in res[0]:
        for name, ln in ((nd.get("call_lines") or {}).get("name") or {}).items():
            n += 1
            whole = files.get(nd["source_file"]) or ""
            text = whole.split("\n")
            seg = name.rsplit(".", 1)[-1]
            # the name itself, or an import alias of it (`from helpers import format_money as fm` ... `fm(3)`)
            called = [seg] + re.findall(r"\b" + re.escape(seg) + r"\s+as\s+(\w+)", whole)
            if not (1 <= ln <= len(text) and any(re.search(r"\b" + re.escape(c) + r"\s*\(", text[ln - 1])
                                                 for c in called)):
                bad.append((nd["name"], name, ln))
    check(f"A the installed engine ({gx.graphify_version()}): each of the {n} recorded lines of the probe tree is a "
          f"source line that calls that name (or its import alias)", n >= 5 and not bad, bad)


# ================================================================================================
# B — identity on every captured graph
# ================================================================================================
B_MODES = {"base": {}, "plus": PF.FROZEN_MODES["plus"],
           "p1": {"DEEPMAP_PRECISE_CALLS": "1", "DEEPMAP_PRECISE_FIXES": "0"},
           "p1all": {**PF.FROZEN_MODES["p1all"], "DEEPMAP_PRECISE_FIXES": "0"}}
LINE_MODES = {**B_MODES, "p1fix": {"DEEPMAP_PRECISE_CALLS": "1"}}


def _shape_ok(cl) -> bool:
    if not isinstance(cl, dict) or not cl or set(cl) - {"key", "name"}:
        return False
    for sub in cl.values():
        if not isinstance(sub, dict) or not sub:
            return False
        for k, v in sub.items():
            if type(k) is not str or not k or type(v) is not int or v <= 0:
                return False
    return True


def section_b(gx, entries, frozen) -> None:
    n = n_lines = 0
    bad_frozen, bad_strip, bad_shape, bad_caller, bad_multi = [], [], [], [], []
    for b, eid, graph, files in entries:
        rec = frozen[b]["entries"].get(eid)
        root = PF.root_for(b, eid, files)
        for mode, env in LINE_MODES.items():
            plain = run(gx, graph, root, env)
            if mode in B_MODES and rec is not None:
                if PF.canon(gx, *plain) != {k: rec[mode][k] for k in ("nodes_sha", "node_keys", "calls", "imports")}:
                    bad_frozen.append(f"{b}/{eid}/{mode}")
            if any("call_lines" in nd for nd in plain[0]):
                bad_frozen.append(f"{b}/{eid}/{mode}: call_lines without asking")
            withl = run(gx, graph, root, env, call_lines=True)
            if (strip(withl[0]), withl[1], withl[2]) != (plain[0], plain[1], plain[2]):
                bad_strip.append(f"{b}/{eid}/{mode}")
            got = recorded(withl[0])
            callers = {s for s, _c in withl[1]}
            bad_shape += [f"{b}/{eid}/{mode}/{k}" for k, v in got.items() if not _shape_ok(v)]
            bad_caller += [f"{b}/{eid}/{mode}/{k}" for k in got if k not in callers]
            if eid.startswith("multilang/") and got:
                bad_multi.append(f"{eid}/{mode}")
            n += 1
            n_lines += bool(got)
    missing = [f"{b}/{eid}" for b, eid, _g, _f in entries if frozen[b]["entries"].get(eid) is None]
    check("B every captured graph has its frozen pre-S1 record", not missing, missing)
    check(f"B map_graph() (call_lines not asked) is still the frozen pre-S1 output on every graph, in "
          f"precise_family_gates B's modes, with no call_lines attribute anywhere", not bad_frozen, bad_frozen[:8])
    check("B map_graph(call_lines=True) minus call_lines == map_graph(): nodes, calls (with their key marks) and imports, "
          "in every mode (also PRECISE=1 with the fixes on)", not bad_strip, bad_strip[:8])
    check("B call_lines is {'key'|'name': {plain str: positive int}} with no empty half", not bad_shape, bad_shape[:8])
    check("B ...and only a node that makes a CALLS tuple carries it", not bad_caller, bad_caller[:8])
    check("B the multilang graphs (their links carry no source_location) record no line", not bad_multi, bad_multi)
    check(f"B ran on {n} (graph, mode) pairs, {n_lines} with lines recorded", n >= 200 and n_lines >= 100, (n, n_lines))


# ================================================================================================
# C — the per-link isolation oracle
# ================================================================================================
def oracle(gx, graph: dict, root, env: dict) -> tuple:
    """(recorded call_lines, the oracle's): the oracle maps each engine call link ALONE (every other call link removed)
    and gives its line to each tuple of the full mapping that link produces by itself; a key tuple counts for its key
    and for its target's name; the minimum wins. It never reads the recording code's state."""
    source = GX._Source(root) if root is not None else None
    links = graph.get("links", [])
    rest = [l for l in links if l.get("relation") != "calls"]

    def tuples(sub):
        nodes, calls, _imp = run(gx, {**graph, "links": sub}, root, env, source=source)
        return {(s, str(c), bool(gx.is_call_key(c))) for s, c in calls}

    base = tuples(rest)
    full = run(gx, graph, root, env, call_lines=True, source=source)
    final = {(s, str(c), bool(gx.is_call_key(c))) for s, c in full[1]}
    name_of = {n["key"]: n["name"] for n in full[0]}
    want: dict = {}
    for l in links:
        ln = link_line(l) if l.get("relation") == "calls" else None
        if not ln:
            continue
        for s, c, is_key in (tuples(rest + [l]) - base) & final:
            for sub, t in ((("key", c), ("name", name_of.get(c, ""))) if is_key else (("name", c),)):
                d = want.setdefault(s, {}).setdefault(sub, {})
                d[t] = min(d.get(t, ln), ln)
    return recorded(full[0]), want


def section_c(gx, entries, members_graphs) -> None:
    bad, n_graphs, n_callers = [], 0, 0
    for b, eid, graph, files in entries:
        if not has_lines(graph):
            continue
        root = PF.root_for(b, eid, files)
        for mode, env in (("base", {}), ("p1", {"DEEPMAP_PRECISE_CALLS": "1"})):
            got, want = oracle(gx, graph, root, env)
            n_graphs += 1
            n_callers += len(want)
            if got != want:
                diff = {k: (got.get(k), want.get(k)) for k in set(got) | set(want) if got.get(k) != want.get(k)}
                bad.append((f"{b}/{eid}/{mode}", dict(list(diff.items())[:2])))
    check(f"C on every captured graph with lines ({n_graphs} graph/mode pairs, {n_callers} callers): each recorded "
          f"line is the oracle's lowest line of the links behind that tuple, and nothing else is recorded",
          not bad and n_graphs >= 40 and n_callers >= 150, (bad[:3], n_graphs, n_callers))
    for b, (graph, root) in members_graphs.items():
        got, want = oracle(gx, graph, root, {})
        check(f"C the C# members capture ({b}): recorded lines == the oracle's (C# callers included)",
              got == want and any(str(k).startswith("models_values") for k in want), (got, want))


# ================================================================================================
# D — engine parity
# ================================================================================================
def _canon_lines(nodes) -> dict:
    """{(caller name, file, line, "key"|"name", target): line} — engine ids replaced by the target's name, file, line."""
    ident = {n["key"]: (n["name"], n["source_file"], n["start_line"]) for n in nodes}
    out: dict = {}
    for n in nodes:
        for sub, d in (n.get("call_lines") or {}).items():
            for t, v in d.items():
                out[ident[n["key"]] + (sub, ident.get(t) if sub == "key" else t)] = v
    return out


def section_d(gx, entries) -> None:
    by = {}
    for b, eid, graph, files in entries:
        if eid.startswith(("precise/", "r8/")):
            by.setdefault(eid, {})[b] = (graph, files)
    pairs = {eid: v for eid, v in by.items() if set(v) == {"0839", "0851"}}
    bad, n_common, n_one = [], 0, 0
    for eid, v in sorted(pairs.items()):
        for mode, env in (("base", {}), ("p1", {"DEEPMAP_PRECISE_CALLS": "1"})):
            c = {b: _canon_lines(run(gx, g, PF.root_for(b, eid, f), env, call_lines=True)[0]) for b, (g, f) in v.items()}
            common = set(c["0839"]) & set(c["0851"])
            n_common += len(common)
            n_one += len(set(c["0839"]) ^ set(c["0851"]))
            bad += [(eid, mode, k, c["0839"][k], c["0851"][k]) for k in sorted(common, key=str)
                    if c["0839"][k] != c["0851"][k]]
    # The engines' tuples differ in places (0.8.39 also links a call to a placeholder of an annotation's name — the
    # P1b case); every call BOTH engines turn into a tuple must carry the same line.
    print(f"  ({n_one} entries recorded by one engine only: their tuples differ, not their lines)")
    check(f"D graphify 0.8.39 and 0.8.51 record the same line for every one of the {n_common} entries both record, on "
          f"the {len(pairs)} trees captured on both (precise + r8; defaults and PRECISE=1)",
          len(pairs) >= 9 and n_common >= 100 and not bad, (len(pairs), n_common, bad[:5]))


# ================================================================================================
# E — synthetic cases
# ================================================================================================
def _node(i, label, sf, line):
    return {"id": i, "label": label, "file_type": "code", "source_file": sf, "source_location": f"L{line}"}


SYN = {"nodes": [_node("a", "a.py", "a.py", 1), _node("b", "b.py", "b.py", 1), _node("c", "c.py", "c.py", 1),
                 _node("a_run", "run()", "a.py", 3), _node("a_tool", "tool()", "a.py", 20),
                 _node("b_helper", "helper()", "b.py", 1), _node("c_helper", "helper()", "c.py", 1)],
       "links": [{"relation": "contains", "source": "a", "target": "a_run", "source_location": "L3"},
                 {"relation": "contains", "source": "a", "target": "a_tool", "source_location": "L20"},
                 {"relation": "contains", "source": "b", "target": "b_helper", "source_location": "L1"},
                 {"relation": "contains", "source": "c", "target": "c_helper", "source_location": "L1"},
                 {"relation": "calls", "source": "a_run", "target": "b_helper", "source_location": "L5"},
                 {"relation": "calls", "source": "a_run", "target": "c_helper", "source_location": "L2"},
                 {"relation": "calls", "source": "a_run", "target": "b_helper", "source_location": "L8"},
                 {"relation": "calls", "source": "a_run", "target": "a_tool", "source_location": "L0"},
                 {"relation": "calls", "source": "a_run", "target": "a_tool"}]}


def section_e(gx) -> None:
    nodes, calls, _imp = run(gx, SYN, None, {}, call_lines=True)
    got = recorded(nodes)
    check("E defaults: three links to two same-named `helper`s (L5, L2, L8) -> name 'helper' at the LOWEST, L2; `tool`, "
          "whose links say L0 or nothing, records nothing", got == {"a_run": {"name": {"helper": 2}}}, got)
    check("E ...and the tuples are what they were", calls == run(gx, SYN, None, {})[1], calls)
    nodes, calls, _imp = run(gx, SYN, None, {"DEEPMAP_PRECISE_CALLS": "1"}, call_lines=True)
    got = recorded(nodes)
    check("E PRECISE=1: each key at its own lowest line (b_helper L5 of L5 / L8, c_helper L2) and the shared name at "
          "the lowest of all; the key tuple to tool (no known line) records nothing",
          got == {"a_run": {"key": {"b_helper": 5, "c_helper": 2}, "name": {"helper": 2}}}, got)
    keys = [k for cl in got.values() for sub in cl.values() for k in sub]
    check("E every recorded key and name is a plain str (never a CallKey) and every line a plain int",
          all(type(k) is str for k in keys) and all(type(v) is int for cl in got.values() for sub in cl.values()
                                                    for v in sub.values()), [type(k) for k in keys])
    plain = run(gx, SYN, None, {"DEEPMAP_PRECISE_CALLS": "1"})
    check("E without call_lines no node carries the attribute", not recorded(plain[0]))


# ================================================================================================
# F — mutation teeth
# ================================================================================================
MUTANTS = [
    ("recording with call_lines=False", "lines_of: dict | None = {} if call_lines else None",
     "lines_of: dict | None = {}", ("B", "E")),
    ("the minimum dropped (the last line wins)", "d[t] = ln if t not in d else min(d[t], ln)", "d[t] = ln",
     ("C", "E")),
    ("extract_with_report not asking for lines", "call_lines=True)", "call_lines=False)", ("A",)),
]


def section_f(entries, frozen, members_graphs) -> None:
    text = (BUILD / "graphify_extract.py").read_text(encoding="utf-8")
    mdir = Path(tempfile.mkdtemp(prefix="dm_cl_mut_"))
    try:
        for i, (what, old, new, sections) in enumerate(MUTANTS):
            if text.count(old) != 1:
                check(f"F mutant '{what}': its anchor appears exactly once in graphify_extract.py", False, text.count(old))
                continue
            p = mdir / f"m{i}" / "graphify_extract.py"
            p.parent.mkdir(parents=True)
            p.write_bytes(text.replace(old, new).encode("utf-8"))
            spec = importlib.util.spec_from_file_location(f"gx_cl_mutant_{i}", str(p))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            reds = []
            for s in sections:
                with quiet() as box:
                    if s == "A":
                        section_a(mod, real=False)
                    elif s == "B":
                        section_b(mod, entries[:12], frozen)
                    elif s == "C":
                        section_c(mod, [e for e in entries if e[1].startswith("precise/")][:6], members_graphs)
                    elif s == "E":
                        section_e(mod)
                if not all(box):
                    reds.append(s)
            check(f"F mutant '{what}' is caught (section {'/'.join(reds) or 'none'} goes RED)", set(reds) == set(sections),
                  {"expected": sections, "red": reds})
    finally:
        shutil.rmtree(str(mdir), ignore_errors=True)      # exactly the temp dir this section created


def members_graphs() -> dict:
    """The C# members captures with their tree materialized (tests/csharp_members_gates)."""
    import csharp_members_gates as CM
    out = {}
    for b in ("0839", "0851"):
        g = CM.graph_of(b)
        if g is not None:
            out[b] = (g, PF.write_tree(PF.tmp() / f"cl_members_{b}", CM.TREE))
    return out


def main(argv) -> int:
    try:
        print("map_graph_call_lines_gates — C1: each caller's first call-site lines (map_graph call_lines=True)")
        entries = PF.entries()
        frozen = {b: PF.frozen(b) for b in PF.BUCKETS.values()}
        mg = members_graphs()
        check("the precise_family_gates frozen records and the C# members captures are present",
              all(frozen.values()) and set(mg) == {"0839", "0851"})
        for name, fn in (("A", lambda: section_a(GX)), ("B", lambda: section_b(GX, entries, frozen)),
                         ("C", lambda: section_c(GX, entries, mg)), ("D", lambda: section_d(GX, entries)),
                         ("E", lambda: section_e(GX)), ("F", lambda: section_f(entries, frozen, mg))):
            print(f"\n-- {name} --")
            try:
                fn()
            except Exception as e:  # noqa: BLE001 — a crashing section is a RED check, not a crashed run
                import traceback
                traceback.print_exc()
                check(f"section {name} ran without raising", False, f"{type(e).__name__}: {e}")
    finally:
        PF.cleanup()
    total = _PASS + _FAIL
    if _FAIL:
        print(f"\nRESULT: RED — {_FAIL} of {total} checks failed.")
        return 1
    print(f"\nRESULT: GREEN — {total}/{total} checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
