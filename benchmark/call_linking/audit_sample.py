#!/usr/bin/env python3
"""The blind edge audit (design §8.4 A).

sample: from the delta of a variant against its base (delta.py), draw a SEEDED sample per bucket
          added | removed | demoted | EXTRACTED          (--n each, default 40; --seed 20261022)
        plus removed_confirmed — EVERY removed edge the reference resolver confirms, exhaustively (not sampled).
        Writes into --out:
          sheet.md   the blind review sheet: per row the caller, the callee and the caller's call-site lines.
                     It names no variant, bucket, grade or resolver verdict (the gate checks); rows are shuffled.
          sheet.csv  the answer template (id,verdict,note) — each reviewer fills a copy with TRUE / FALSE / UNSURE
                     ("does this caller call this callee at one of these sites?")
          key.json   id -> buckets, grades, resolver verdict (kept from the reviewers until scoring)
score:  joins the key with two (or more) filled sheets. Per bucket, with Wilson 95% bounds:
          added / EXTRACTED / demoted   share audited TRUE by every reviewer (a dispute or UNSURE is not TRUE)
          removed                       the SAMPLED stratum (removals the resolver did not confirm): share audited
                                        FALSE by every reviewer (a correct removal)
          removed_confirmed             the EXHAUSTIVE stratum (every resolver-confirmed removal): same share
        losses: TOP LEVEL, the D3-G3 / §8.7 count. EVERY removed row (either stratum) audited TRUE by every
          reviewer, with its id; zero allowed. No per-bucket share stands in for it.
        removed_all: the correct-removal share over BOTH strata. The sampled stratum's share weighted by its
          population (key.json `strata`) plus the exhaustive stratum's exact share; its lower bound combines the
          sampled stratum's Wilson LB with the exhaustive share the same way (the exhaustive stratum has no
          sampling error). This, not a per-stratum share, is the "removed audited FALSE >= 95% (LB >= 90%)"
          number. A key without `strata` (an older sample) gets removed_all = null.
        and the reference resolver's error rate on the rows it judged (confirmed / refuted): >= 10% voids that
        corpus's G confirmed/refuted deltas, and the audit decides (design §8.4 A). This is ONE sample's figure
        (unchanged, so the closed decisions recompute identically); D3-cs judges a corpus by `refrate` (AC11).

D3-cs (PREREG_CS §8.6 A), with a dispatch truth (truth_roslyn.py) or a C# caller:
  sample  the sheet adds CS_SHEET_SENTENCE (verbatim) to its instructions, and a C# caller's call-site lines also
          match `M<...>(` (type arguments) beside `M(` and `?.M(`; a removed edge the reference confirms by dispatch
          (verdict dispatch-confirmed) joins the exhaustive stratum like a confirmed one (AC10: "every removed edge the
          reference confirms" includes the dispatch-confirmed ones). The dispatch stratum (AC9; G3b) is the same
          command with --base=<VT-cs-ifs>: the added bucket of delta(VT-cs-ifs, VT-cs) holds the implementer AND
          override edges (the product's DEEPMAP_TYPED_CALLS_IFACE governs interface and virtual dispatch). Only for an
          RC tree without DEEPMAP_TYPED_CALLS_IFACE, --impl-fallback draws §8.5's fallback as AC9 widens it: the ADDED
          edges (a, b) of delta(V0, VT-cs) where a holds a truth site of b's last name whose static target is an
          interface, virtual, abstract or override member other than b (read through AC5's fields:
          _common.static_dispatchable — the row's `dispatch_rule` when it has dispatch targets, else the defs'
          `interface`), sampled like a bucket.
  score   also reports `removed_all_exact` beside the unchanged `removed_all`: the same population-weighted share
          over both removal strata, but each stratum's lower bound is its exact share when it was audited in full
          (sampled == population: a census has no sampling error) and its Wilson lower bound otherwise; `exact`:
          that bound per bucket (population from key.json); `EXTRACTED_split`: the EXTRACTED bucket split into rows
          new to the variant and rows already EXTRACTED in the base (G4b: the latter are not typed edges). The
          reference's judged rows include dispatch-confirmed (predicting TRUE).
  pool    the population-weighted precision of a bucket over several corpora (G3a / G3b "pooled per family": both
          C# corpora on one engine): each corpus's population times its share, and times its lower bound (exact
          share for a census, else Wilson), over the total population; Wilson 95% on the summed rows beside it,
          never decisive.
  refrate the reference error rate of ONE corpus (PREREG_CS §8.6 A as AC11 fixes it): per engine, over the UNION of
          that engine's main sample (label <id>_<eng>) and dispatch-stratum sample (<id>_<eng>_impl), a (caller,
          callee) pair audited in both counted once; the rows the reference judged confirmed, dispatch-confirmed or
          refuted, wrong when the agreed audit verdict contradicts it (confirmed / dispatch-confirmed predict TRUE,
          refuted FALSE; a pair the reviewers agreed TRUE in one sample and FALSE in the other counts once, as wrong:
          the strict reading). The corpus is unreliable when EITHER engine's rate is 10% or more; reliable only when
          both engines' main and dispatch-stratum samples are given and both rates are below 10%.

usage:
  python audit_sample.py sample --corpus=<root> --base=<V0.json> --variant=<VX.json> [--truth=<truth.json>]
         --label=<corpus id> [--seed=20261022] [--n=40] --out=<dir> [--impl-fallback]
  python audit_sample.py score --key=<dir>/key.json --sheet=<r1.csv> --sheet=<r2.csv> [--json=out.json]
  python audit_sample.py pool --score=<a.json> --score=<b.json> [--bucket=added] [--json=out.json]
  python audit_sample.py refrate --dir=<audit dir> [--dir=<audit dir> ...] [--sheets=sheet_r1.csv,sheet_r2.csv]
         [--json=out.json]
         (one --dir per audit sample of ONE corpus — run_matrix.sh's audit_<eng> and audit_<eng>_impl, both engines:
         the dir's key.json and every reviewer's filled sheet, named by --sheets (file names inside each dir, at
         least two: the blind review has two reviewers). Exit 0 computed, 3 not computable exactly (an engine or a
         sample missing, no judged row), 2 refused)
"""
from __future__ import annotations

import ast
import csv
import hashlib
import os
import random
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as C  # noqa: E402
import delta as D  # noqa: E402

DEFAULT_SEED = 20261022
DEFAULT_N = 40
SAMPLED = ("added", "removed", "demoted", "EXTRACTED")
BLIND_FORBIDDEN = ("added", "removed", "demoted", "promoted", "EXTRACTED", "INFERRED", "AMBIGUOUS", "confirmed",
                   "refuted", "unverified", "DEEPMAP_", "variant", "bucket")
MAX_SPAN = 300
MAX_SITES = 6
# PREREG_CS §8.6 A: "The C# sheet adds one instruction, fixed now" — verbatim, one line of the sheet.
CS_SHEET_SENTENCE = ("C#: the member the compiler binds the call to is TRUE, an interface or abstract member included. "
                     "A call through an interface or a virtual or abstract member is also TRUE for a definition with a "
                     "body, declared in this corpus, that it can run at run time: an implementation or override "
                     "reachable from the receiver's declared type. A definition declared only in test code is FALSE "
                     "for a caller outside test code.")
# The verdicts that mean "the reference confirms the edge": a static pair, or (dispatch truth) a dispatch pair.
CONFIRMING = ("confirmed", C.DISPATCH_CONFIRMED)


def _rng(seed, label, what) -> random.Random:
    return random.Random(f"{seed}|{label}|{what}")


def draw(buckets: dict, seed: int, label: str, n: int) -> dict:
    """{bucket: [items]} — a seeded sample of each SAMPLED bucket (items in delta's sorted order first, so the
    draw is a pure function of (seed, label, bucket, items)), plus removed_confirmed in full."""
    out = {}
    confirmed = [it for it in buckets.get("removed", []) if it.get("verdict") in CONFIRMING]
    for b in SAMPLED:
        items = list(buckets.get(b, []))
        if b == "removed":
            items = [it for it in items if it.get("verdict") not in CONFIRMING]
        out[b] = _rng(seed, label, b).sample(items, min(n, len(items))) if items else []
    out["removed_confirmed"] = confirmed
    return out


def strata(buckets: dict, picked: dict) -> dict:
    """The two removal strata: `removed` = the removals the resolver did not confirm (population, sampled n),
    `removed_confirmed` = every confirmed removal (audited exhaustively: sampled == population)."""
    n_conf = len(picked.get("removed_confirmed") or [])
    pop = len(buckets.get("removed") or []) - n_conf
    return {"removed": {"population": pop, "sampled": len(picked.get("removed") or [])},
            "removed_confirmed": {"population": n_conf, "sampled": n_conf}}


def row_id(seed, label, caller, callee) -> str:
    return hashlib.sha1(f"{seed}|{label}|{caller}|{callee}".encode("utf-8")).hexdigest()[:12]


_SRC_CACHE: dict = {}


def _lines(root: Path, rel: str):
    k = (str(root), rel)
    if k not in _SRC_CACHE:
        try:
            _SRC_CACHE[k] = (root / rel).read_text(encoding="utf-8", errors="replace").split("\n")
        except OSError:
            _SRC_CACHE[k] = []
    return _SRC_CACHE[k]


def _py_end(root: Path, rel: str, start: int):
    try:
        tree = ast.parse("\n".join(_lines(root, rel)))
    except SyntaxError:
        return None
    best = None
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            first = min([n.lineno] + [d.lineno for d in n.decorator_list])
            if start in (n.lineno, first) and (best is None or n.end_lineno > best):
                best = n.end_lineno
    return best


def caller_span(root: Path, nodes_by_file: dict, node) -> tuple:
    rel = C.norm_path(node.get("source_file"))
    start = int(node.get("start_line") or 1)
    end = None
    if node.get("label") == "Module":
        return 1, min(len(_lines(root, rel)), MAX_SPAN * 10)
    if rel.endswith(".py"):
        end = _py_end(root, rel, start)
    if end is None:
        later = sorted(n.get("start_line") or 0 for n in nodes_by_file.get(rel, ()) if (n.get("start_line") or 0) > start)
        end = (later[0] - 1) if later else len(_lines(root, rel))
    return start, min(end, start + MAX_SPAN)


def call_sites(root: Path, rel: str, span: tuple, name: str) -> list:
    rx = re.compile(r"(?<![\w$])" + re.escape(name) + r"\s*\(")
    match = (lambda text: _cs_call_in(text, name)) if C.is_cs_path(rel) else rx.search
    lines = _lines(root, rel)
    out = []
    for i in range(span[0], min(span[1], len(lines)) + 1):
        if match(lines[i - 1]):
            out.append((i, lines[i - 1].rstrip()))
            if len(out) >= MAX_SITES:
                break
    return out


# Characters a C# type-argument list may hold between `<` and `>` (names, `.`, `,`, `?`, arrays, tuples, `global::`);
# anything else (`&&`, `=`, `;`, `"`, an operator) ends the scan, so `M < x && F(y) > (z)` is no call of M.
_CS_TYPE_ARG_CHARS = re.compile(r"[\w\s.,?\[\]():<>]")


def _cs_call_in(text: str, name: str) -> bool:
    """True when `text` holds a C# call of `name` in a shape PREREG_CS §8.6 A lists: `M(`, `M<...>(` (type
    arguments, nested ones and tuples included) and `?.M(` (the name is not preceded by a word character, `$` or `@`,
    so `x.M(`, `x?.M(` and a bare `M(` all match, `FooM(` does not)."""
    for m in re.finditer(r"(?<![\w$@])" + re.escape(name) + r"(?!\w)", text):
        i = m.end()
        while i < len(text) and text[i].isspace():
            i += 1
        if i < len(text) and text[i] == "(":
            return True
        if i < len(text) and text[i] == "<":
            depth, j = 0, i
            while j < len(text) and _CS_TYPE_ARG_CHARS.match(text[j]):
                if text[j] == "<":
                    depth += 1
                elif text[j] == ">":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            if depth == 0 and j < len(text) and text[j] == ">":
                k = j + 1
                while k < len(text) and text[k].isspace():
                    k += 1
                if k < len(text) and text[k] == "(":
                    return True
    return False


def build_rows(root: Path, variant: dict, base: dict, picked: dict, seed: int, label: str) -> tuple:
    nodes = {**C.node_index(base), **C.node_index(variant)}
    by_file = {}
    for n in nodes.values():
        by_file.setdefault(C.norm_path(n.get("source_file")), []).append(n)
    rows, key = {}, {}
    for bucket in list(SAMPLED) + ["removed_confirmed"]:
        for it in picked.get(bucket, []):
            rid = row_id(seed, label, it["caller"], it["callee"])
            k = key.setdefault(rid, {"caller": it["caller"], "callee": it["callee"], "buckets": [],
                                     "base_grade": it.get("base"), "variant_grade": it.get("variant"),
                                     "verdict": it.get("verdict"), "caller_class": it.get("caller_class")})
            for extra in ("verdict_static", "callee_class"):     # a dispatch truth's delta items (D3-cs) carry both
                if extra in it:
                    k.setdefault(extra, it[extra])
            if bucket not in k["buckets"]:
                k["buckets"].append(bucket)
            if rid in rows:
                continue
            cn, tn = nodes.get(it["caller"], {}), nodes.get(it["callee"], {})
            span = caller_span(root, by_file, cn)
            callee_name = C.last_segment(tn.get("name"))
            sites = call_sites(root, C.norm_path(cn.get("source_file")), span, callee_name)
            rows[rid] = {"id": rid, "caller": f"{cn.get('name')} ({cn.get('label')}) @ {cn.get('source_file')}:{cn.get('start_line')}",
                         "callee": f"{tn.get('name')} ({tn.get('label')}) @ {tn.get('source_file')}:{tn.get('start_line')}",
                         "span": f"{cn.get('source_file')}:{span[0]}-{span[1]}", "sites": sites, "name": callee_name}
    order = sorted(rows)
    _rng(seed, label, "order").shuffle(order)
    return [rows[r] for r in order], key


def render_sheet(label: str, rows: list, cs: bool = False) -> str:
    """The blind sheet. `cs` (a C# sample, PREREG_CS §8.6 A) adds CS_SHEET_SENTENCE, verbatim, to the instructions."""
    out = [f"# Call-edge audit sheet — {label}", "",
           "For each row: does the CALLER call the CALLEE (this exact definition) at one of the listed sites?",
           "Answer TRUE / FALSE / UNSURE in your copy of sheet.csv (id,verdict,note). Read the source when the",
           "sites are not enough; judge only from the code.", ""]
    if cs:
        out += [CS_SHEET_SENTENCE, ""]
    for i, r in enumerate(rows, 1):
        out.append(f"## {i}. `{r['id']}`")
        out.append(f"- caller: `{r['caller']}`")
        out.append(f"- callee: `{r['callee']}`")
        out.append(f"- caller span: `{r['span']}`; sites of `{r['name']}(`:")
        if r["sites"]:
            out += [f"    {ln:6d}  {txt}" for ln, txt in r["sites"]]
        else:
            out.append("    (no textual site found in the span — read the caller)")
        out.append("")
    return "\n".join(out) + "\n"


def cmd_sample(opts) -> int:
    base, var = C.load_json(opts["--base"]), C.load_json(opts["--variant"])
    truth = C.load_truth(opts["--truth"]) if opts.get("--truth") else None
    seed = int(opts.get("--seed") or DEFAULT_SEED)
    n = int(opts.get("--n") or DEFAULT_N)
    label = opts["--label"]
    d = D.delta(base, var, truth)
    if not d["node_set_identical"]:
        print(f"[audit_sample] REFUSED: the node sets differ ({d['n_node_mismatch']} nodes) — not an edge-only pair",
              file=sys.stderr)
        return 3
    if d["void"]:
        print(f"[audit_sample] REFUSED: the pair is VOID — {'; '.join(d['void'])}", file=sys.stderr)
        return 3
    items = d["items"]
    stratum = None
    if "--impl-fallback" in opts:
        if truth is None or not C.has_dispatch(truth):
            print("[audit_sample] REFUSED: --impl-fallback needs a dispatch truth (--truth, truth_roslyn.py)",
                  file=sys.stderr)
            return 2
        added, why = impl_fallback(items.get("added") or [], truth, var.get("nodes") or [])
        if why:
            print(f"[audit_sample] REFUSED: --impl-fallback cannot be computed exactly: {why}", file=sys.stderr)
            return 3
        items = {"added": added, "removed": [], "demoted": [], "EXTRACTED": []}
        stratum = "impl-fallback"
    picked = draw(items, seed, label, n)
    rows, key = build_rows(Path(opts["--corpus"]), var, base, picked, seed, label)
    cs = (truth is not None and C.has_dispatch(truth)) or any(
        C.is_cs_path(it.get("caller_file")) for v in picked.values() for it in v)
    out = Path(opts["--out"])
    out.mkdir(parents=True, exist_ok=True)
    (out / "sheet.md").write_text(render_sheet(label, rows, cs=cs), encoding="utf-8", newline="\n")
    with open(out / "sheet.csv", "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["id", "verdict", "note"])
        for r in rows:
            w.writerow([r["id"], "", ""])
    kd = {"label": label, "seed": seed, "n": n, "base": os.path.basename(opts["--base"]),
          "variant": os.path.basename(opts["--variant"]), "rows": key,
          "bucket_sizes": {b: len(items.get(b, [])) for b in SAMPLED},
          "sampled": {b: len(v) for b, v in picked.items()},
          "strata": strata(items, picked)}
    if cs:
        kd["cs"] = True
    if stratum:
        kd["stratum"] = stratum
    C.write_json(out / "key.json", kd)
    print(f"[audit_sample] {label}: {len(rows)} rows "
          + " ".join(f"{b}={len(v)}/{len(items.get(b, [])) if b in items else len(v)}" for b, v in picked.items()))
    return 0


def impl_fallback(added: list, truth: dict, nodes: list) -> tuple:
    """PREREG_CS §8.5's fallback dispatch stratum as AC9 widens it (only for an RC tree without
    DEEPMAP_TYPED_CALLS_IFACE): the added items (a, b) of delta(V0, VT-cs) where a holds a truth site of b's last name
    whose static target is an interface, virtual, abstract or override member other than b — read through AC5's
    fields (_common.static_dispatchable: the row's `dispatch_rule` when it has dispatch targets, else the defs'
    `interface`). Returns (items, problem): problem names why it cannot be computed exactly (a site of that name
    whose row does not answer through AC5's fields), else None."""
    loc = C.GraphLocator(nodes)
    sites = {}                       # (caller key, last name) -> [(dispatchable?, static target keys), ...]
    for r in truth.get("rows") or []:
        caller = loc.caller_of(r)
        if caller is None or not (r.get("defs") or r.get("dispatch")):
            continue                 # an unresolved site binds nothing; a dispatch-only site's static target is external
        sites.setdefault((caller["key"], C.last_segment(r.get("name"))), []).append(
            (C.static_dispatchable(r), {n["key"] for n in loc.callee_nodes(r)}))
    out, unknown = [], []
    for it in added:
        got = sites.get((it["caller"], C.last_segment(it.get("callee_name")))) or []
        if any(fl is True and it["callee"] not in keys for fl, keys in got):
            out.append(it)
        elif any(fl is None for fl, _k in got):
            unknown.append([it["caller"], it["callee"]])
    if unknown:
        return out, (f"{len(unknown)} added edge(s) have a site of that name whose row does not say whether its "
                     f"static target is an interface, virtual, abstract or override member (AC5: a `dispatch_rule` "
                     f"interface / override for a row with dispatch targets, else the defs' `interface`), e.g. "
                     f"{unknown[:3]}")
    return out, None


def read_sheet(path) -> dict:
    out = {}
    with open(path, encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            v = (r.get("verdict") or "").strip().upper()
            out[(r.get("id") or "").strip()] = v if v in ("TRUE", "FALSE", "UNSURE") else ""
    return out


def score(key: dict, sheets: list) -> dict:
    rows = key["rows"]
    per_bucket = {}
    res_judged = res_wrong = 0
    losses = []
    for rid, k in sorted(rows.items()):
        votes = [s.get(rid, "") for s in sheets]
        agreed = votes[0] if votes and all(v == votes[0] and v in ("TRUE", "FALSE") for v in votes) else None
        if agreed == "TRUE" and any(b in ("removed", "removed_confirmed") for b in k["buckets"]):
            losses.append({"id": rid, "caller": k.get("caller"), "callee": k.get("callee"),
                           "stratum": "removed_confirmed" if "removed_confirmed" in k["buckets"] else "removed",
                           "verdict": k.get("verdict")})
        for b in k["buckets"]:
            e = per_bucket.setdefault(b, {"n": 0, "true": 0, "false": 0, "disputed": 0})
            e["n"] += 1
            if agreed == "TRUE":
                e["true"] += 1
            elif agreed == "FALSE":
                e["false"] += 1
            else:
                e["disputed"] += 1
        if agreed and k.get("verdict") in CONFIRMING + ("refuted",):
            res_judged += 1
            if (k["verdict"] in CONFIRMING) != (agreed == "TRUE"):
                res_wrong += 1
    out = {"label": key.get("label"), "reviewers": len(sheets), "buckets": {},
           "losses": {"n": len(losses), "rows": losses}}
    for b, e in sorted(per_bucket.items()):
        good = e["false"] if b == "removed" else e["true"]
        p, lo, hi = C.wilson(good, e["n"])
        out["buckets"][b] = dict(e, measure=("correct_removal" if b == "removed" else
                                             "losses" if b == "removed_confirmed" else "precision"),
                                 share=None if p is None else round(p, 4),
                                 wilson_lb=None if lo is None else round(lo, 4),
                                 wilson_ub=None if hi is None else round(hi, 4))
    out["removed_all"] = removed_all(out["buckets"], key.get("strata"))
    out["removed_all_exact"] = removed_all_exact(out["buckets"], key.get("strata"))
    out["exact"] = exact_bounds(out["buckets"], key)
    out["EXTRACTED_split"] = extracted_split(rows, sheets)
    rate = res_wrong / res_judged if res_judged else None
    out["resolver"] = {"judged": res_judged, "wrong": res_wrong,
                       "error_rate": None if rate is None else round(rate, 4),
                       "voids_g_deltas": rate is not None and rate >= 0.10}
    return out


def _agreed(votes) -> "str | None":
    return votes[0] if votes and all(v == votes[0] and v in ("TRUE", "FALSE") for v in votes) else None


def extracted_split(rows: dict, sheets: list) -> dict:
    """The EXTRACTED bucket split by the base's grade (PREREG_CS G4b): `typed` = rows the variant made EXTRACTED,
    `in_base` = rows already EXTRACTED in the base (not a typed edge; reported separately). Each {n, true, false,
    disputed}."""
    out = {"typed": {"n": 0, "true": 0, "false": 0, "disputed": 0},
           "in_base": {"n": 0, "true": 0, "false": 0, "disputed": 0}}
    for rid, k in sorted(rows.items()):
        if "EXTRACTED" not in (k.get("buckets") or []):
            continue
        e = out["in_base" if k.get("base_grade") == "EXTRACTED" else "typed"]
        e["n"] += 1
        a = _agreed([s.get(rid, "") for s in sheets])
        e["true" if a == "TRUE" else "false" if a == "FALSE" else "disputed"] += 1
    return out


def _population(b: str, key: dict) -> "int | None":
    st = key.get("strata") or {}
    if b in ("removed", "removed_confirmed"):
        v = (st.get(b) or {}).get("population")
    else:
        v = (key.get("bucket_sizes") or {}).get(b)
    return int(v) if isinstance(v, int) else None


def _sampled(b: str, key: dict) -> "int | None":
    st = key.get("strata") or {}
    v = (st.get(b) or {}).get("sampled") if b in ("removed", "removed_confirmed") else (key.get("sampled") or {}).get(b)
    return int(v) if isinstance(v, int) else None


def exact_bounds(buckets: dict, key: dict) -> dict:
    """Per bucket, the G3 lower bound of PREREG_CS §8.6 A for one stratum: its exact share when it was audited in full
    (sampled == population: a census), else its Wilson 95% lower bound; with the population (key.json) a pool weights
    it by. The share is the bucket's measure (precision; for the removal strata the correct-removal share). A bucket
    whose population key.json does not record gets population None."""
    out = {}
    for b, e in sorted(buckets.items()):
        pop, smp = _population(b, key), _sampled(b, key)
        good = e["false"] if b in ("removed", "removed_confirmed") else e["true"]
        share = good / e["n"] if e.get("n") else None
        census = pop is not None and smp is not None and smp >= pop
        lb = share if census else e.get("wilson_lb")
        out[b] = {"population": pop, "sampled": smp, "audited": e.get("n"), "good": good,
                  "share": None if share is None else round(share, 4),
                  "lower_bound": None if lb is None else round(lb, 4), "census": census}
    return out


def removed_all(buckets: dict, st) -> "dict | None":
    """The correct-removal (audited FALSE) share over both removal strata, population-weighted (see the module
    doc), or None without strata. A stratum with no audited rows contributes nothing (and no population)."""
    if not isinstance(st, dict):
        return None
    num = den = num_lb = 0.0
    parts = {}
    for b in ("removed", "removed_confirmed"):
        e = buckets.get(b)
        pop = int(((st.get(b) or {}).get("population")) or 0)
        if not e or not e.get("n") or pop <= 0:
            continue
        share = e["false"] / e["n"]
        lb = share if b == "removed_confirmed" else (e.get("wilson_lb") or 0.0)
        num += pop * share
        num_lb += pop * lb
        den += pop
        parts[b] = {"population": pop, "audited": e["n"], "false": e["false"], "share": round(share, 4),
                    "lb_used": round(lb, 4)}
    if not den:
        return {"population": 0, "share": None, "lower_bound": None, "strata": parts}
    return {"population": int(den), "share": round(num / den, 4), "lower_bound": round(num_lb / den, 4),
            "strata": parts}


def removed_all_exact(buckets: dict, st) -> "dict | None":
    """PREREG_CS §8.6 A / reading 2 (G3c): removed_all with each stratum's lower bound taken as its EXACT share when
    it was audited in full (sampled == population, a census: no sampling error) and its Wilson 95% lower bound when
    it was sampled below its population. removed_all keeps its old computation (the sampled stratum always at its
    Wilson bound), so the closed decisions recompute identically. None without strata."""
    if not isinstance(st, dict):
        return None
    num = den = num_lb = 0.0
    parts = {}
    for b in ("removed", "removed_confirmed"):
        e = buckets.get(b)
        s = st.get(b) or {}
        pop = int(s.get("population") or 0)
        if not e or not e.get("n") or pop <= 0:
            continue
        share = e["false"] / e["n"]
        census = int(s.get("sampled") or 0) >= pop
        lb = share if census else (e.get("wilson_lb") or 0.0)
        num += pop * share
        num_lb += pop * lb
        den += pop
        parts[b] = {"population": pop, "sampled": int(s.get("sampled") or 0), "audited": e["n"], "false": e["false"],
                    "share": round(share, 4), "lb_used": round(lb, 4), "census": census}
    if not den:
        return {"population": 0, "share": None, "lower_bound": None, "strata": parts}
    return {"population": int(den), "share": round(num / den, 4), "lower_bound": round(num_lb / den, 4),
            "strata": parts}


def pool_scores(named: list, bucket: str = "added") -> dict:
    """PREREG_CS §8.6 A, a pooled bound (G3a / G3b "pooled per family": both C# corpora on one engine): over the
    scores' `exact` entries for `bucket`, the population-weighted share and lower bound — each corpus's population
    times its share / its lower bound (exact share for a census, Wilson LB otherwise), over the total population —
    and, beside it and never decisive, Wilson 95% on the summed audited rows. A score without a population for the
    bucket cannot be pooled (KeyError); a corpus with nothing in the bucket contributes nothing."""
    parts, num, num_lb, den, good, n = [], 0.0, 0.0, 0, 0, 0
    for path, s in named:
        e = (s.get("exact") or {}).get(bucket)
        if e is None:
            parts.append({"score": path, "label": s.get("label"), "population": 0, "audited": 0})
            continue
        if e.get("population") is None:
            raise KeyError(f"{path}: the {bucket} bucket has no population (an older key.json): it cannot be weighted")
        pop = int(e["population"])
        parts.append({"score": path, "label": s.get("label"), "population": pop, "audited": e.get("audited"),
                      "share": e.get("share"), "lower_bound": e.get("lower_bound"), "census": e.get("census")})
        if pop <= 0 or not e.get("audited"):
            continue
        num += pop * (e["good"] / e["audited"])
        num_lb += pop * float(e["lower_bound"] or 0.0)
        den += pop
        good += int(e["good"])
        n += int(e["audited"])
    p, lo, hi = C.wilson(good, n)
    return {"bucket": bucket, "population": den,
            "share": round(num / den, 4) if den else None, "lower_bound": round(num_lb / den, 4) if den else None,
            "summed_rows": {"audited": n, "good": good, "share": None if p is None else round(p, 4),
                            "wilson_lb": None if lo is None else round(lo, 4),
                            "wilson_ub": None if hi is None else round(hi, 4), "decisive": False},
            "parts": parts}


# A sample's label as run_matrix.sh draws it (PREREG_CS §8.6 A): <corpus id>_<engine> for the main sample (V0 ->
# VT-cs), <corpus id>_<engine>_impl for the dispatch stratum (VT-cs-ifs -> VT-cs, or the --impl-fallback stratum).
_SAMPLE_LABEL = re.compile(r"^(?P<corpus>.+)_(?P<engine>g39|g51)(?P<impl>_impl)?$")
REFRATE_THRESHOLD = 0.10          # §8.6 A: "A reference error rate of 10% or more ... voids that corpus's G deltas"
ENGINES = ("g39", "g51")
JUDGED = CONFIRMING + ("refuted",)


def reference_error_rate(samples: list) -> dict:
    """PREREG_CS §8.6 A's reference error rate of ONE corpus, as AC11 fixes it (pure). `samples` = [(key dict, [sheet
    dicts], name)], every audit sample of the corpus: per engine one main sample and one dispatch-stratum sample
    (their labels say which, _SAMPLE_LABEL).

    Per engine, the rows of its samples are united by (caller, callee) pair — a pair audited in both samples counts
    once. A pair is judged when the reference judged it (key verdict confirmed, dispatch-confirmed or refuted) and the
    reviewers agreed on TRUE or FALSE in at least one of its samples (score's rule: a dispute or an UNSURE is neither);
    it is wrong when the agreed verdict contradicts the reference (confirmed / dispatch-confirmed predict TRUE, refuted
    FALSE). A pair agreed TRUE in one sample and FALSE in the other counts once, as wrong (the strict reading:
    either way one audit contradicts the reference), and is listed in `conflicts`. The engine's rate is wrong /
    judged; the corpus is unreliable when EITHER engine's rate is >= 10% (`unreliable` True), reliable only when both
    engines have both samples, judged rows and rates below 10% (False), else not computable exactly (None, `why`).
    Raises ValueError for samples that cannot be one corpus's (an unknown label, two corpora, a sample given twice,
    the same pair with two different reference verdicts)."""
    by_engine = {e: {} for e in ENGINES}
    corpora = set()
    for key, sheets, name in samples:
        label = str((key or {}).get("label") or "")
        m = _SAMPLE_LABEL.match(label)
        if not m:
            raise ValueError(f"{name}: label {label!r} is not <corpus>_<g39|g51>[_impl] (run_matrix.sh's audit labels)")
        corpora.add(m.group("corpus"))
        kind = "impl" if m.group("impl") else "main"
        if kind in by_engine[m.group("engine")]:
            raise ValueError(f"{name}: a second {kind} sample for {m.group('engine')} (label {label})")
        by_engine[m.group("engine")][kind] = (key, sheets, name)
    if len(corpora) > 1:
        raise ValueError(f"the samples belong to several corpora ({sorted(corpora)}): the rate is per corpus")
    out = {"corpus": next(iter(corpora), None), "threshold": REFRATE_THRESHOLD, "engines": {}}
    for eng in ENGINES:
        got = by_engine[eng]
        pairs = {}
        for kind in ("main", "impl"):
            if kind not in got:
                continue
            key, sheets, _name = got[kind]
            for rid, k in sorted((key.get("rows") or {}).items()):
                p = (k.get("caller"), k.get("callee"))
                e = pairs.setdefault(p, {"verdicts": set(), "agreed": [], "samples": []})
                e["verdicts"].add(k.get("verdict"))
                e["samples"].append(f"{kind}:{rid}")
                a = _agreed([s.get(rid, "") for s in sheets])
                if a:
                    e["agreed"].append(a)
        mixed = sorted([list(p), sorted(map(str, e["verdicts"]))] for p, e in pairs.items() if len(e["verdicts"]) > 1)
        if mixed:
            raise ValueError(f"{eng}: the same pair carries different reference verdicts in its samples (not one "
                             f"truth and one variant): {mixed[:3]}")
        judged = wrong = 0
        conflicts, wrong_rows = [], []
        for p, e in sorted(pairs.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1]))):
            verdict = next(iter(e["verdicts"]))
            if verdict not in JUDGED or not e["agreed"]:
                continue
            judged += 1
            agreed = set(e["agreed"])
            if len(agreed) > 1:
                conflicts.append({"caller": p[0], "callee": p[1], "verdict": verdict, "samples": e["samples"]})
                bad = True
            else:
                bad = (verdict in CONFIRMING) != (agreed == {"TRUE"})
            if bad:
                wrong += 1
                wrong_rows.append({"caller": p[0], "callee": p[1], "verdict": verdict, "audit": sorted(agreed)})
        rate = wrong / judged if judged else None
        out["engines"][eng] = {
            "samples": {k: v[2] for k, v in sorted(got.items())}, "pairs": len(pairs),
            "pairs_in_both": sum(1 for e in pairs.values() if any(s.startswith("main:") for s in e["samples"])
                                 and any(s.startswith("impl:") for s in e["samples"])),
            "judged": judged, "wrong": wrong, "error_rate": None if rate is None else round(rate, 4),
            # exact: wrong / judged >= 10% compared in integers (never a float rounding at the threshold)
            "unreliable": None if rate is None else wrong * 10 >= judged,
            "complete": set(got) == {"main", "impl"}, "conflicts": conflicts, "wrong_rows": wrong_rows[:50]}
    eng = out["engines"]
    if any(v["unreliable"] is True for v in eng.values()):
        out["unreliable"], out["why"] = True, ("an engine's reference error rate is 10% or more: the corpus's G "
                                               "confirmed/refuted deltas are VOID and the audit decides (§8.6 A)")
    elif all(v["complete"] and v["unreliable"] is False for v in eng.values()):
        out["unreliable"], out["why"] = False, "both engines' rates (main + dispatch-stratum samples) are below 10%"
    else:
        missing = [f"{e}: " + ("no judged row" if v["complete"] else
                               "missing " + " and ".join(k for k in ("main", "impl") if k not in v["samples"]))
                   for e, v in eng.items() if not (v["complete"] and v["unreliable"] is not None)]
        out["unreliable"], out["why"] = None, "not computable exactly (" + "; ".join(missing) + ")"
    return out


DEFAULT_SHEETS = ("sheet_r1.csv", "sheet_r2.csv")


def cmd_refrate(opts, dirs) -> int:
    names = [x.strip() for x in str(opts.get("--sheets") or ",".join(DEFAULT_SHEETS)).split(",") if x.strip()]
    if len(names) < 2 or any("/" in n or "\\" in n for n in names):
        print(f"[audit_sample] refrate: --sheets names at least two sheet FILE NAMES inside each audit dir (the blind "
              f"review has two reviewers), got {names}", file=sys.stderr)
        return 2
    samples = []
    for d in dirs:
        dp = Path(d)
        missing = [x for x in ["key.json"] + names if not (dp / x).is_file()]
        if missing:
            print(f"[audit_sample] refrate: {d} lacks {missing} (key.json and every reviewer's filled sheet)",
                  file=sys.stderr)
            return 2
        try:
            samples.append((C.load_json(dp / "key.json"), [read_sheet(dp / x) for x in names], C.norm_path(dp)))
        except (OSError, ValueError) as exc:
            print(f"[audit_sample] refrate: {d}: {exc}", file=sys.stderr)
            return 2
    try:
        res = reference_error_rate(samples)
    except ValueError as exc:
        print(f"[audit_sample] refrate REFUSED: {exc}", file=sys.stderr)
        return 2
    for e, v in res["engines"].items():
        print(f"{res['corpus']} {e}: samples {v['samples'] or '(none)'}; {v['pairs']} pair(s) ({v['pairs_in_both']} in "
              f"both samples, counted once); reference wrong on {v['wrong']}/{v['judged']} judged -> error rate "
              f"{v['error_rate']}" + (" (>= 10%)" if v["unreliable"] else "")
              + (f"; {len(v['conflicts'])} pair(s) agreed TRUE in one sample and FALSE in the other (counted wrong)"
                 if v["conflicts"] else ""))
    print(f"{res['corpus']}: reference {'UNRELIABLE' if res['unreliable'] else 'reliable' if res['unreliable'] is False else 'NOT COMPUTABLE'}"
          f" — {res['why']}")
    if opts.get("--json"):
        C.write_json(opts["--json"], res)
    return 3 if res["unreliable"] is None else 0


def cmd_score(opts, sheets) -> int:
    key = C.load_json(opts["--key"])
    res = score(key, [read_sheet(s) for s in sheets])
    for b, e in res["buckets"].items():
        print(f"{b:18s} n={e['n']:3d} TRUE={e['true']:3d} FALSE={e['false']:3d} disputed={e['disputed']:3d} "
              f"{e['measure']}={e['share']} (Wilson 95% {e['wilson_lb']}..{e['wilson_ub']})")
    ra = res["removed_all"]
    if ra is not None:
        print(f"removed (both strata) correct-removal share={ra['share']} (lower bound {ra['lower_bound']}) over "
              f"{ra['population']} removals")
    rx = res.get("removed_all_exact")
    if rx is not None:
        print(f"removed_all_exact (a census at its exact share) share={rx['share']} (lower bound {rx['lower_bound']}) "
              f"over {rx['population']} removals")
    for b, e in (res.get("exact") or {}).items():
        print(f"   {b:18s} population={e['population']} audited={e['audited']} share={e['share']} lower bound "
              f"{e['lower_bound']}" + (" (census: exact)" if e["census"] else " (Wilson 95%)"))
    xs = res.get("EXTRACTED_split") or {}
    if (xs.get("typed") or {}).get("n") or (xs.get("in_base") or {}).get("n"):
        print(f"EXTRACTED rows new to the variant: {xs['typed']}; already EXTRACTED in the base: {xs['in_base']}")
    print(f"LOSSES (removed rows audited TRUE by every reviewer, both strata): {res['losses']['n']}")
    for x in res["losses"]["rows"]:
        print(f"   {x['id']} {x['caller']} -> {x['callee']} ({x['stratum']})")
    r = res["resolver"]
    print(f"reference resolver: wrong on {r['wrong']}/{r['judged']} judged rows (error rate {r['error_rate']})"
          + (" -> VOIDS this corpus's G confirmed/refuted deltas" if r["voids_g_deltas"] else ""))
    if opts.get("--json"):
        C.write_json(opts["--json"], res)
    return 0


def cmd_pool(opts, scores) -> int:
    named = []
    for p in scores:
        s = C.load_json(p)
        if not isinstance(s, dict) or not isinstance(s.get("buckets"), dict):
            print(f"[audit_sample] pool: {p} is not an audit_sample score", file=sys.stderr)
            return 2
        named.append((p, s))
    engines = {m.group(1) for _p, s in named for m in [re.search(r"_(g39|g51)(?:_|$)", str(s.get("label") or ""))]
               if m}
    if len(engines) > 1:
        print(f"[audit_sample] pool: the scores come from different engines ({sorted(engines)}): pool one engine at a "
              f"time", file=sys.stderr)
        return 2
    try:
        res = pool_scores(named, opts.get("--bucket") or "added")
    except KeyError as exc:
        print(f"[audit_sample] pool: {exc}", file=sys.stderr)
        return 2
    print(f"pooled {res['bucket']} over {len(named)} score(s), population {res['population']}: share={res['share']} "
          f"lower bound {res['lower_bound']} (population-weighted); summed rows {res['summed_rows']['good']}/"
          f"{res['summed_rows']['audited']} Wilson 95% {res['summed_rows']['wilson_lb']}..{res['summed_rows']['wilson_ub']}"
          f" (never decisive)")
    if opts.get("--json"):
        C.write_json(opts["--json"], res)
    return 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    opts, args = C.parse_cli(argv, ("--corpus", "--base", "--variant", "--truth", "--label", "--seed", "--n", "--out",
                                    "--key", "--json", "--bucket", "--sheets"),
                             multi_opts=("--sheet", "--score", "--dir"))
    sheets = opts.pop("--sheet", [])
    scores = opts.pop("--score", [])
    dirs = opts.pop("--dir", [])
    if args[:1] == ["refrate"] and dirs:
        return cmd_refrate(opts, dirs)
    if args[:1] == ["sample"] and all(opts.get(k) for k in ("--corpus", "--base", "--variant", "--label", "--out")):
        return cmd_sample(opts)
    if args[:1] == ["score"] and opts.get("--key") and sheets:
        return cmd_score(opts, sheets)
    if args[:1] == ["pool"] and scores:
        return cmd_pool(opts, scores)
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    C.utf8_stdio()
    raise SystemExit(main())
