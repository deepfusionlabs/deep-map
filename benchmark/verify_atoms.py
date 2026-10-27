#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DeepMap benchmark — DETERMINISTIC ground-truth atom locator / re-verification.

Independent, reproducible re-verification of the pinned `answer_key.ground_truth_set` atoms in
`benchmark/questions.yaml` against the pinned clones. For EVERY atom of the form
`<clone-relative-path>:<line> — <description>` this:

  * resolves `<clone-root>/<path>` at the pinned SHA,
  * reads the ACTUAL source line at `<line>` (+/- a little context),
  * checks the leading symbol named in the description actually occurs on/near that line
    (a drift tripwire — reported, not fatal),
  * classifies the atom FOUND / SYMBOL_DRIFT / LINE_OUT_OF_RANGE / FILE_MISSING / UNPARSED.

It runs with NO model, NO network, NO Neo4j, NO plan spend — a pure file read — so it is fully
reproducible and its output is a stable artifact an independent judge (human or LLM) can re-derive.
This is the DETERMINISTIC half of the independent re-verification; the semantic "does this line
actually implement the described behavior" judgment is a separate LLM/agent pass. Bare-symbol atoms
(no `file:line`) are located by a repo-wide ripgrep-style scan and reported with their hit count.

Usage:
    python benchmark/verify_atoms.py [--clones c:/tmp/deepmap-bench-clones]
                                     [--repo owner/name ...] [--breadth-only]
                                     [--out benchmark/results/ATOM_VERIFICATION]
Writes `<out>.md` (human/LLM-readable) and `<out>.json` (machine-readable). Exits 0 always
(a locator miss is a finding to record, not a crash).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import run_benchmark as RB  # noqa: E402  (import-safe; main() is __main__-guarded)

# Atom shape: "<file>:<line>[-<line2>] — <description>"  (em-dash OR ' -- ' OR ' - ' separator).
_SEP_RE = re.compile(r"\s+(?:—|--|-)\s+")
_FILELINE_RE = re.compile(r"^(?P<file>.+?):(?P<line>\d+)(?:-\d+)?$")
# A plausible leading symbol in the description: dotted/qualified identifier, method, or Type.
_SYMBOL_RE = re.compile(r"[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*")
CONTEXT = 2  # lines of context each side


def clone_dir_for(repo: str, clones: Path) -> Path:
    return clones / repo.replace("/", "__")


def _read_lines(path: Path) -> Optional[List[str]]:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None


def _leading_symbol(desc: str) -> Optional[str]:
    m = _SYMBOL_RE.search(desc or "")
    return m.group(0) if m else None


# Ultra-common words that appear on many lines / in prose — never a discriminating on-topic signal.
_STOP = {"self", "this", "that", "from", "with", "call", "calls", "called", "return", "returns",
         "line", "true", "false", "the", "and", "for", "def", "func",
         "class", "public", "static", "const", "async", "await", "then", "else", "into"}


def _desc_identifiers(desc: str) -> List[str]:
    """Discriminating code identifiers named in a description (its embedded statement) — EVERY
    dotted segment (not just the tail, so `_cv_app.set` yields `_cv_app` and `schema._zod.run`
    yields `_zod`), length >= 4, minus ultra-common words."""
    out: List[str] = []
    for m in _SYMBOL_RE.findall(desc or ""):
        for seg in m.split("."):
            if len(seg) >= 4 and seg.lower() not in _STOP and seg not in out:
                out.append(seg)
    return out


def _on_topic(desc: str, lines: List[str], idx0: int, radius: int = 1) -> bool:
    """Is the atom's line plausibly the statement the description names? True if any discriminating
    identifier from the description occurs on the exact line or +/- `radius` (a statement can wrap
    across lines). The left boundary excludes only alphanumerics (NOT `_`/`.`), so `result_callback`
    still matches `self._result_callback`. Descriptions embed the actual code, so this catches the
    function-granularity case (description names the enclosing fn AND its internal statement)."""
    ids = _desc_identifiers(desc)
    if not ids:
        return True  # nothing discriminating to check -> don't flag
    lo, hi = max(0, idx0 - radius), min(len(lines), idx0 + radius + 1)
    window = "\n".join(lines[lo:hi])
    return any(re.search(rf"(?<![A-Za-z0-9]){re.escape(t)}\b", window) for t in ids)


def _grep_count(symbol: str, clone: Path, exts: Tuple[str, ...]) -> int:
    """Repo-wide word-bounded occurrence count of a bare-symbol atom (test dirs excluded)."""
    tail = symbol.split(".")[-1]
    pat = re.compile(rf"\b{re.escape(tail)}\b")
    n = 0
    for p in clone.rglob("*"):
        if not p.is_file() or p.suffix not in exts:
            continue
        parts = {x.lower() for x in p.parts}
        if parts & {"test", "tests", "__tests__", "spec", "node_modules", ".git"}:
            continue
        lines = _read_lines(p)
        if lines:
            n += sum(1 for ln in lines if pat.search(ln))
    return n


def verify_atom(atom: str, clone: Path) -> Dict[str, Any]:
    raw = atom.strip()
    parts = _SEP_RE.split(raw, maxsplit=1)
    locus = parts[0].strip()
    desc = parts[1].strip() if len(parts) > 1 else ""
    m = _FILELINE_RE.match(locus)
    if not m:
        # bare-symbol atom (no file:line) — locate by repo scan
        sym = _leading_symbol(locus) or locus
        cnt = _grep_count(sym, clone, (".py", ".go", ".rs", ".ts", ".tsx", ".js"))
        return {"atom": raw, "kind": "symbol", "symbol": sym,
                "status": "FOUND" if cnt > 0 else "SYMBOL_MISSING",
                "hits": cnt, "line_text": "", "file": "", "line": None}
    rel, line = m.group("file"), int(m.group("line"))
    fpath = clone / rel
    lines = _read_lines(fpath)
    out = {"atom": raw, "kind": "file:line", "file": rel, "line": line,
           "symbol": _leading_symbol(desc) or "", "line_text": "", "context": []}
    if lines is None:
        # basename fallback to report drift candidates
        cands = [str(p.relative_to(clone)).replace("\\", "/")
                 for p in clone.rglob(Path(rel).name)][:5]
        out["status"] = "FILE_MISSING"
        out["candidates"] = cands
        return out
    if not (1 <= line <= len(lines)):
        out["status"] = "LINE_OUT_OF_RANGE"
        out["file_lines"] = len(lines)
        return out
    idx0 = line - 1
    out["line_text"] = lines[idx0].strip()
    lo, hi = max(0, idx0 - CONTEXT), min(len(lines), idx0 + CONTEXT + 1)
    out["context"] = [f"{lo+i+1}: {lines[lo+i]}" for i in range(hi - lo)]
    out["status"] = "FOUND" if _on_topic(desc, lines, idx0) else "SYMBOL_DRIFT"
    return out


def run(clones: Path, repo_filter: Optional[set], breadth_only: bool) -> Dict[str, Any]:
    _repos, questions = RB.load_config()
    breadth_ids = {"julienschmidt/httprouter", "serde-rs/json", "gin-gonic/gin", "colinhacks/zod"}
    results: List[Dict[str, Any]] = []
    for q in questions:
        repo = q.get("repo")
        if repo_filter and repo not in repo_filter:
            continue
        if breadth_only and repo not in breadth_ids:
            continue
        ak = q.get("answer_key") or {}
        gts = ak.get("ground_truth_set") or []
        if not isinstance(gts, list) or not gts:
            continue
        clone = clone_dir_for(repo, clones)
        atoms = [verify_atom(a, clone) for a in gts if isinstance(a, str)]
        results.append({"qid": q.get("id"), "repo": repo, "family": q.get("family"),
                        "grep_can_do": q.get("grep_can_do"), "clone_exists": clone.is_dir(),
                        "n_atoms": len(atoms), "atoms": atoms})
    return {"clones": str(clones), "questions": results}


def _summary(report: Dict[str, Any]) -> Dict[str, int]:
    c: Dict[str, int] = {}
    for q in report["questions"]:
        for a in q["atoms"]:
            c[a["status"]] = c.get(a["status"], 0) + 1
    c["ATOMS_TOTAL"] = sum(v for k, v in c.items() if k != "ATOMS_TOTAL")
    return c


def write_md(report: Dict[str, Any], summ: Dict[str, int], out_md: Path) -> None:
    lines: List[str] = []
    ok = summ.get("FOUND", 0)
    tot = summ.get("ATOMS_TOTAL", 0)
    lines.append("# DeepMap benchmark — deterministic ground-truth atom re-verification\n")
    lines.append("> Reproducible locator: every `answer_key.ground_truth_set` atom resolved against the")
    lines.append("> pinned clones (no model, no network). Re-run: `python benchmark/verify_atoms.py`. This is")
    lines.append("> the DETERMINISTIC half of the independent re-verification; semantic behavior-match is a")
    lines.append("> separate LLM/agent judgment. Statuses: FOUND (line exists + named symbol on/near it),")
    lines.append("> SYMBOL_DRIFT (line exists but the named symbol isn't within +/-3 lines — review),")
    lines.append("> LINE_OUT_OF_RANGE / FILE_MISSING (locator miss), SYMBOL_MISSING (bare-symbol not found).\n")
    lines.append(f"**Headline: {ok}/{tot} file:line+symbol atoms located exactly.** Status tally: "
                 + ", ".join(f"`{k}`={v}" for k, v in sorted(summ.items()) if k != "ATOMS_TOTAL") + "\n")
    # flag section first (what a reviewer must look at)
    flagged = [(q, a) for q in report["questions"] for a in q["atoms"]
               if a["status"] not in ("FOUND",)]
    lines.append(f"## Atoms needing review ({len(flagged)})\n")
    if not flagged:
        lines.append("_None — every atom located exactly with its named symbol present._\n")
    for q, a in flagged:
        extra = ""
        if a["status"] == "SYMBOL_DRIFT":
            extra = f" — symbol `{a.get('symbol')}` not within ±3 lines; line reads: `{a.get('line_text')}`"
        elif a["status"] == "FILE_MISSING":
            extra = f" — candidates: {a.get('candidates')}"
        elif a["status"] == "LINE_OUT_OF_RANGE":
            extra = f" — file has {a.get('file_lines')} lines"
        elif a["status"] == "SYMBOL_MISSING":
            extra = f" — 0 repo hits for `{a.get('symbol')}`"
        lines.append(f"- **{a['status']}** · {q['qid']} · `{a['atom'][:120]}`{extra}")
    lines.append("")
    # full per-question detail
    lines.append("## Full atom-by-atom detail\n")
    for q in sorted(report["questions"], key=lambda x: (x["repo"], x["qid"])):
        flag = "" if q["clone_exists"] else "  ⚠ CLONE MISSING"
        lines.append(f"### {q['qid']} · {q['repo']} · {q['family']} · grep_can_do={q['grep_can_do']}"
                     f" · {q['n_atoms']} atoms{flag}")
        for a in q["atoms"]:
            if a["kind"] == "file:line":
                lines.append(f"- `{a['status']}` **{a['file']}:{a['line']}** → `{a['line_text']}`")
            else:
                lines.append(f"- `{a['status']}` (symbol `{a['symbol']}`, {a.get('hits')} hits) · `{a['atom'][:90]}`")
        lines.append("")
    out_md.write_text("\n".join(lines), encoding="utf-8")


def main(argv: List[str]) -> int:
    RB._ensure_console_utf8()
    ap = argparse.ArgumentParser(description="Deterministic ground-truth atom re-verification.")
    ap.add_argument("--clones", default=os.environ.get("CLONES", "c:/tmp/deepmap-bench-clones"))
    ap.add_argument("--repo", action="append", default=None, help="restrict to repo(s) owner/name")
    ap.add_argument("--breadth-only", action="store_true", help="only the 4 multi-language breadth repos")
    ap.add_argument("--out", default=str(RB.RESULTS_ROOT / "ATOM_VERIFICATION"))
    args = ap.parse_args(argv)
    clones = Path(args.clones)
    report = run(clones, set(args.repo) if args.repo else None, args.breadth_only)
    summ = _summary(report)
    out_base = Path(args.out)
    out_base.parent.mkdir(parents=True, exist_ok=True)
    (out_base.with_suffix(".json")).write_text(
        json.dumps({"summary": summ, **report}, indent=2), encoding="utf-8")
    write_md(report, summ, out_base.with_suffix(".md"))
    print(f"[verify_atoms] {summ.get('FOUND',0)}/{summ.get('ATOMS_TOTAL',0)} atoms FOUND exactly; "
          f"tally={ {k:v for k,v in summ.items() if k!='ATOMS_TOTAL'} }")
    print(f"[verify_atoms] wrote {out_base.with_suffix('.md')} and .json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
