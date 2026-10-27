#!/usr/bin/env python3
"""Phase M scorer — the T4 recall rules applied to one run_variant.py raw capture, plus the R1 / R3 comparison
of two scored arms (design §8.5).

STRICT hit : a returned node is in the atom's file AND (its qualified name has the atom symbol as a dot-boundary
             suffix  OR  it denotes the innermost def/class enclosing the atom line). Module nodes never count.
SPEC hit   : same file AND (last name component == the atom symbol's last component  OR  the atom line lies within
             the node's def span [decorators .. end; Module nodes excluded]).  (== T4 'lenient')
search = the top-15 search rows; impact = the impact(limit=30) rows of the top-3 hits (graph families only);
union = search ∪ impact.

Atom symbols may carry `$` (zod's `$ZodError`, `$ZodString`). Def spans come from the language's own parser:
Python `ast` for .py; tree-sitter for TS/JS (.ts/.tsx/.js/.jsx/.mjs/.cjs, through build/graphify_extract.py's
`_Source.ts_tree()` — the grammars graphify itself parses with), where a def is a function / class / method /
interface / type alias / enum, a variable declarator whose value is a function or class, or a top-level
declarator (`export const $ZodString = $constructor(...)`). Without the grammars a TS file has no spans and only
the name rule applies (reported in the scored output as `spans: none`).

usage:
  python score_m.py score <raw_V.json> --clone=<corpus> [--out=scored_V.json]
  python score_m.py r1 <scored_V0.json> <scored_VX.json> [--json=out.json]
      R1: no question loses a STRICT or SPEC search∪impact atom (threshold 0) and the STRICT∪ macro >= V0;
      R3: impact STRICT atoms on the multi_hop questions >= V0. Exit 1 when either fails.
"""
from __future__ import annotations

import ast
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as C  # noqa: E402

ATOM_RE = re.compile(r"^(?P<path>[^\s:]+):(?P<line>\d+)\s*(?:[—–-]+)\s*(?P<rest>.*)$")
IDENT_RE = re.compile(r"^(?:class\s+|def\s+)?(?P<sym>[A-Za-z_$][\w$.]*)")
TESTDOC_RE = re.compile(r"(^|/)(tests?|docs|docs_src|examples|scripts)(/|$)")

_span_cache: dict = {}


def _is_overload(n):
    for d in n.decorator_list:
        t = d.func if isinstance(d, ast.Call) else d
        if (getattr(t, "id", None) or getattr(t, "attr", None)) == "overload":
            return True
    return False


TS_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
_TS_FUNC = frozenset({"arrow_function", "function_expression", "function", "generator_function", "class"})
_TS_DECL = {"function_declaration": "def", "generator_function_declaration": "def", "method_definition": "def",
            "class_declaration": "class", "abstract_class_declaration": "class", "interface_declaration": "class",
            "type_alias_declaration": "class", "enum_declaration": "class"}
_ts_sources: dict = {}


def _ts_source(clone: Path):
    k = str(clone)
    if k not in _ts_sources:
        _ts_sources[k] = None
        try:
            if str(C.BUILD) not in sys.path:
                sys.path.insert(0, str(C.BUILD))
            import graphify_extract  # noqa: WPS433 — lazy: tree-sitter is graphify's dependency
            _ts_sources[k] = graphify_extract._Source(clone)
        except Exception:  # noqa: BLE001 — no grammars: TS files get no spans (name rule only)
            _ts_sources[k] = None
    return _ts_sources[k]


def ts_defs(clone: Path, rel: str):
    """The def spans of a TS/JS file (1-based lines) from tree-sitter, or None when the grammar is missing."""
    src = _ts_source(Path(clone))
    got = src.ts_tree(rel) if src is not None else None
    if not got:
        return None
    tree, data = got
    out = []

    def text(n):
        return data[n.start_byte:n.end_byte].decode("utf-8", errors="replace")

    def add(node, name_node, kind, parent, span_node=None):
        sp = span_node or node
        out.append(dict(start=sp.start_point[0] + 1, line=(name_node or node).start_point[0] + 1,
                        end=sp.end_point[0] + 1, name=text(name_node) if name_node is not None else "",
                        kind=kind, parent=parent, overload=False))

    def walk(node, parent, top):
        for ch in node.children:
            t = ch.type
            nxt = parent
            if t in _TS_DECL:
                nm = ch.child_by_field_name("name")
                if nm is not None:
                    decos = [d for d in ch.children if d.type == "decorator"]
                    add(ch, nm, _TS_DECL[t], parent)
                    if decos:
                        out[-1]["start"] = min(out[-1]["start"], decos[0].start_point[0] + 1)
                    nxt = id(ch)
            elif t == "variable_declarator":
                nm, val = ch.child_by_field_name("name"), ch.child_by_field_name("value")
                if nm is not None and nm.type == "identifier" and (top or (val is not None and val.type in _TS_FUNC)):
                    stmt = ch.parent if ch.parent is not None and ch.parent.type in (
                        "lexical_declaration", "variable_declaration") else ch
                    add(ch, nm, "class" if val is not None and val.type == "class" else "def", parent, span_node=stmt)
                    nxt = id(ch)
            elif t in ("public_field_definition", "field_definition"):
                nm, val = ch.child_by_field_name("name"), ch.child_by_field_name("value")
                if nm is not None and val is not None and val.type in _TS_FUNC:
                    add(ch, nm, "def", parent)
                    nxt = id(ch)
            child_top = top and t in ("program", "export_statement", "lexical_declaration", "variable_declaration")
            walk(ch, nxt, child_top)

    walk(tree.root_node, 0, True)
    return out


def defs_for(clone: Path, rel: str):
    key = (str(clone), rel)
    if key in _span_cache:
        return _span_cache[key]
    if str(rel).lower().endswith(TS_EXTS):
        got = ts_defs(clone, rel)
        _span_cache[key] = got or []
        return _span_cache[key]
    out = []
    try:
        tree = ast.parse((Path(clone) / rel).read_bytes())

        def walk(node, parent):
            for ch in ast.iter_child_nodes(node):
                if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    out.append(dict(start=min([ch.lineno] + [d.lineno for d in ch.decorator_list]),
                                    line=ch.lineno, end=ch.end_lineno, name=ch.name,
                                    kind="class" if isinstance(ch, ast.ClassDef) else "def",
                                    parent=id(parent),
                                    overload=(not isinstance(ch, ast.ClassDef)) and _is_overload(ch)))
                    walk(ch, ch)
                else:
                    walk(ch, parent)
        walk(tree, tree)
    except Exception:  # noqa: BLE001
        pass
    _span_cache[key] = out
    return out


def resolve_node(clone: Path, file: str, line, name: str):
    if not file or line is None:
        return None
    last = (name or "").split(".")[-1]
    ds = defs_for(clone, file)
    cands = [d for d in ds if d["line"] == line or d["start"] == line]
    if not cands:
        return None
    named = [d for d in cands if d["name"] == last]
    d0 = (named or cands)[0]
    end = d0["end"]
    if d0["overload"]:
        sib = sorted([d for d in ds if d["parent"] == d0["parent"] and d["name"] == d0["name"]
                      and d["start"] >= d0["start"]], key=lambda d: d["start"])
        for d in sib:
            end = max(end, d["end"])
            if not d["overload"]:
                break
    return dict(start=d0["start"], end=end, name=d0["name"])


def innermost(clone: Path, file: str, L: int):
    best = None
    for d in defs_for(clone, file):
        if d["start"] <= L <= d["end"] and (best is None or d["start"] > best["start"]):
            best = d
    return best


def parse_atom(a: str):
    m = ATOM_RE.match(a.strip())
    if not m:
        return None
    rest = m.group("rest")
    sm = IDENT_RE.match(rest)
    sym = sm.group("sym") if sm else None
    if sym and (sym.startswith("self.") or sym == "self"):
        sym = None
    return {"raw": a, "file": m.group("path"), "line": int(m.group("line")), "sym": sym,
            "last": sym.split(".")[-1] if sym else None}


def qsuffix(node_name: str, sym) -> bool:
    if not sym or not node_name:
        return False
    return node_name == sym or node_name.endswith("." + sym)


def hits(atom, nodes, clone: Path):
    """dict(spec, strict, orig, fr_spec, fr_strict)"""
    inner = innermost(clone, atom["file"], atom["line"])
    spec = strict = orig = False
    fr_spec = fr_strict = None
    for rank, n in enumerate(nodes, 1):
        f = (n.get("file") or "").replace("\\", "/")
        if f != atom["file"]:
            continue
        name = n.get("name") or ""
        typ = n.get("type") or ""
        line = n.get("line")
        res = None if typ == "Module" else resolve_node(clone, f, line, name)
        name_ok = bool(atom["last"]) and name.split(".")[-1] == atom["last"]
        in_span = bool(res) and res["start"] <= atom["line"] <= res["end"]
        if name_ok or in_span:
            spec = True
            fr_spec = fr_spec or rank
        denotes_inner = (inner is not None and res is not None and res["name"] == inner["name"]
                         and res["start"] <= inner["line"] <= res["end"])
        if typ != "Module" and (qsuffix(name, atom["sym"]) or denotes_inner):
            strict = True
            fr_strict = fr_strict or rank
        if typ != "Module" and (line == atom["line"] or denotes_inner):
            orig = True
    return dict(spec=spec, strict=strict, orig=orig, fr_spec=fr_spec, fr_strict=fr_strict)


def score_q(q, clone: Path, atoms_raw):
    atoms = [a for a in (parse_atom(x) for x in atoms_raw) if a]
    s_nodes = [{"name": r.get("name"), "type": r.get("type"), "file": r.get("file"), "line": r.get("line")}
               for r in q.get("search") or [] if isinstance(r, dict) and "note" not in r]
    i_nodes = []
    for v in (q.get("impact") or {}).values():
        for r in v.get("rows") or []:
            if isinstance(r, dict) and "note" not in r:
                i_nodes.append({"name": r.get("caller"), "type": r.get("caller_type"),
                                "file": r.get("file"), "line": r.get("line")})
    u_nodes = s_nodes + i_nodes
    res = {"id": q["id"], "family": q["family"], "n_atoms": len(atoms), "n_search": len(s_nodes),
           "n_impact_rows": len(i_nodes), "search_ms": q.get("search_ms"),
           "search_error": q.get("search_error"),
           "impact_errors": [k for k, v in (q.get("impact") or {}).items() if v.get("error")]}
    per_atom = []
    for a in atoms:
        hs = hits(a, s_nodes, clone)
        hi = hits(a, i_nodes, clone)
        hu = hits(a, u_nodes, clone)
        per_atom.append({"atom": a["raw"], "sym": a["sym"],
                         "module_level": innermost(clone, a["file"], a["line"]) is None,
                         "search_spec": hs["spec"], "search_strict": hs["strict"], "search_t4orig": hs["orig"],
                         "impact_spec": hi["spec"], "impact_strict": hi["strict"],
                         "union_spec": hu["spec"], "union_strict": hu["strict"], "union_t4orig": hu["orig"],
                         "rank_strict": hs["fr_strict"], "rank_spec": hs["fr_spec"]})
    n = max(1, len(atoms))
    for k in ("search_spec", "search_strict", "search_t4orig", "impact_spec", "impact_strict", "union_spec",
              "union_strict", "union_t4orig"):
        res[k] = sum(p[k] for p in per_atom) / n
        res[k + "_hits"] = sum(p[k] for p in per_atom)
    afiles = {a["file"] for a in atoms}
    sfiles = {(x["file"] or "").replace("\\", "/") for x in s_nodes}
    ufiles = sfiles | {(x["file"] or "").replace("\\", "/") for x in i_nodes}
    res["file_search"] = len(afiles & sfiles) / max(1, len(afiles))
    res["file_union"] = len(afiles & ufiles) / max(1, len(afiles))
    res["testdoc_share"] = (sum(bool(TESTDOC_RE.search((x["file"] or "").replace("\\", "/"))) for x in s_nodes)
                            / max(1, len(s_nodes)))
    res["atoms"] = per_atom
    res["top15"] = [f"{x['type']} {x['name']} @ {x['file']}:{x['line']}" for x in s_nodes]
    return res


METRICS = ["search_strict", "union_strict", "search_spec", "union_spec", "search_t4orig", "union_t4orig",
           "file_search", "file_union", "testdoc_share"]


def aggregate(scored, key=lambda r: "ALL"):
    g = defaultdict(list)
    for r in scored:
        g[key(r)].append(r)
    out = {}
    for k, rs in sorted(g.items()):
        n_at = sum(r["n_atoms"] for r in rs)
        d = {"n_q": len(rs), "n_atoms": n_at}
        for c in METRICS:
            d[c] = sum(r[c] for r in rs) / len(rs)                         # macro over questions
        for c in ("search_strict", "union_strict", "search_spec", "union_spec"):
            d[c + "_pooled"] = sum(r[c + "_hits"] for r in rs) / max(1, n_at)  # pooled over atoms
        out[k] = d
    return out


def r1_compare(base_scored, var_scored) -> dict:
    """R1 + R3 between two scored arms of the SAME questions (pure)."""
    b = {r["id"]: r for r in base_scored}
    v = {r["id"]: r for r in var_scored}
    losses, gains = [], []
    for qid in sorted(set(b) & set(v)):
        ba = {a["atom"]: a for a in b[qid]["atoms"]}
        va = {a["atom"]: a for a in v[qid]["atoms"]}
        for atom in sorted(ba):
            for measure in ("union_strict", "union_spec"):
                was, now = bool(ba[atom][measure]), bool((va.get(atom) or {}).get(measure))
                if was and not now:
                    losses.append({"question": qid, "atom": atom, "measure": measure})
                elif now and not was:
                    gains.append({"question": qid, "atom": atom, "measure": measure})
    common = sorted(set(b) & set(v))
    macro_b = sum(b[q]["union_strict"] for q in common) / max(1, len(common))
    macro_v = sum(v[q]["union_strict"] for q in common) / max(1, len(common))
    mh = [q for q in common if b[q]["family"] == "multi_hop"]
    r3_b = sum(b[q]["impact_strict_hits"] for q in mh)
    r3_v = sum(v[q]["impact_strict_hits"] for q in mh)
    return {"questions": len(common), "missing_in_variant": sorted(set(b) - set(v)),
            "atom_losses": losses, "atom_gains": gains,
            "macro_union_strict_base": round(macro_b, 4), "macro_union_strict_variant": round(macro_v, 4),
            "r1_holds": not losses and macro_v >= macro_b and not (set(b) - set(v)),
            "r3_multi_hop_impact_strict_base": r3_b, "r3_multi_hop_impact_strict_variant": r3_v,
            "r3_holds": r3_v >= r3_b}


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    opts, args = C.parse_cli(argv, ('--clone', '--out', '--json'))
    if not args or args[0] not in ("score", "r1"):
        print(__doc__, file=sys.stderr)
        return 2
    if args[0] == "score":
        raw = C.load_json(args[1])
        clone = Path(opts.get("--clone") or raw.get("clone") or ".")
        atoms = raw.get("atoms") or {}
        scored = [score_q(q, clone, atoms.get(q["id"]) or q.get("atoms") or []) for q in raw.get("questions") or []]
        agg = aggregate(scored)
        print(C.dump_json({"overall": agg.get("ALL"), "per_family": aggregate(scored, key=lambda r: r["family"])}),
              end="")
        if opts.get("--out"):
            C.write_json(opts["--out"], scored)
        return 0
    res = r1_compare(C.load_json(args[1]), C.load_json(args[2]))
    print(f"R1 {'HOLDS' if res['r1_holds'] else 'FAILS'}: {len(res['atom_losses'])} atom loss(es), "
          f"{len(res['atom_gains'])} gain(s); STRICT∪ macro {res['macro_union_strict_base']} -> "
          f"{res['macro_union_strict_variant']}.  R3 {'HOLDS' if res['r3_holds'] else 'FAILS'}: multi_hop impact "
          f"STRICT {res['r3_multi_hop_impact_strict_base']} -> {res['r3_multi_hop_impact_strict_variant']}")
    for x in res["atom_losses"]:
        print(f"   LOSS {x['question']} [{x['measure']}] {x['atom']}")
    if opts.get("--json"):
        C.write_json(opts["--json"], res)
    return 0 if res["r1_holds"] and res["r3_holds"] else 1


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
    raise SystemExit(main())
