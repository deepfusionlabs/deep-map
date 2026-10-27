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
        corpus's G confirmed/refuted deltas, and the audit decides (design §8.4 A).

usage:
  python audit_sample.py sample --corpus=<root> --base=<V0.json> --variant=<VX.json> [--truth=<truth.json>]
         --label=<corpus id> [--seed=20261022] [--n=40] --out=<dir>
  python audit_sample.py score --key=<dir>/key.json --sheet=<r1.csv> --sheet=<r2.csv> [--json=out.json]
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


def _rng(seed, label, what) -> random.Random:
    return random.Random(f"{seed}|{label}|{what}")


def draw(buckets: dict, seed: int, label: str, n: int) -> dict:
    """{bucket: [items]} — a seeded sample of each SAMPLED bucket (items in delta's sorted order first, so the
    draw is a pure function of (seed, label, bucket, items)), plus removed_confirmed in full."""
    out = {}
    confirmed = [it for it in buckets.get("removed", []) if it.get("verdict") == "confirmed"]
    for b in SAMPLED:
        items = list(buckets.get(b, []))
        if b == "removed":
            items = [it for it in items if it.get("verdict") != "confirmed"]
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
    lines = _lines(root, rel)
    out = []
    for i in range(span[0], min(span[1], len(lines)) + 1):
        if rx.search(lines[i - 1]):
            out.append((i, lines[i - 1].rstrip()))
            if len(out) >= MAX_SITES:
                break
    return out


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


def render_sheet(label: str, rows: list) -> str:
    out = [f"# Call-edge audit sheet — {label}", "",
           "For each row: does the CALLER call the CALLEE (this exact definition) at one of the listed sites?",
           "Answer TRUE / FALSE / UNSURE in your copy of sheet.csv (id,verdict,note). Read the source when the",
           "sites are not enough; judge only from the code.", ""]
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
    truth = C.load_json(opts["--truth"]) if opts.get("--truth") else None
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
    picked = draw(d["items"], seed, label, n)
    rows, key = build_rows(Path(opts["--corpus"]), var, base, picked, seed, label)
    out = Path(opts["--out"])
    out.mkdir(parents=True, exist_ok=True)
    (out / "sheet.md").write_text(render_sheet(label, rows), encoding="utf-8", newline="\n")
    with open(out / "sheet.csv", "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["id", "verdict", "note"])
        for r in rows:
            w.writerow([r["id"], "", ""])
    C.write_json(out / "key.json", {"label": label, "seed": seed, "n": n, "base": os.path.basename(opts["--base"]),
                                    "variant": os.path.basename(opts["--variant"]), "rows": key,
                                    "bucket_sizes": {b: len(d["items"].get(b, [])) for b in SAMPLED},
                                    "sampled": {b: len(v) for b, v in picked.items()},
                                    "strata": strata(d["items"], picked)})
    print(f"[audit_sample] {label}: {len(rows)} rows "
          + " ".join(f"{b}={len(v)}/{len(d['items'].get(b, [])) if b in d['items'] else len(v)}" for b, v in picked.items()))
    return 0


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
        if agreed and k.get("verdict") in ("confirmed", "refuted"):
            res_judged += 1
            if (k["verdict"] == "confirmed") != (agreed == "TRUE"):
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
    rate = res_wrong / res_judged if res_judged else None
    out["resolver"] = {"judged": res_judged, "wrong": res_wrong,
                       "error_rate": None if rate is None else round(rate, 4),
                       "voids_g_deltas": rate is not None and rate >= 0.10}
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
    print(f"LOSSES (removed rows audited TRUE by every reviewer, both strata): {res['losses']['n']}")
    for x in res["losses"]["rows"]:
        print(f"   {x['id']} {x['caller']} -> {x['callee']} ({x['stratum']})")
    r = res["resolver"]
    print(f"reference resolver: wrong on {r['wrong']}/{r['judged']} judged rows (error rate {r['error_rate']})"
          + (" -> VOIDS this corpus's G confirmed/refuted deltas" if r["voids_g_deltas"] else ""))
    if opts.get("--json"):
        C.write_json(opts["--json"], res)
    return 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    opts, args = C.parse_cli(argv, ("--corpus", "--base", "--variant", "--truth", "--label", "--seed", "--n", "--out",
                                    "--key", "--json"), multi_opts=("--sheet",))
    sheets = opts.pop("--sheet", [])
    if args[:1] == ["sample"] and all(opts.get(k) for k in ("--corpus", "--base", "--variant", "--label", "--out")):
        return cmd_sample(opts)
    if args[:1] == ["score"] and opts.get("--key") and sheets:
        return cmd_score(opts, sheets)
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    C.utf8_stdio()
    raise SystemExit(main())
