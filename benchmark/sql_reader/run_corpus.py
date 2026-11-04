#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""G-SQL measurement (benchmark/sql_reader/PREREG.md): DeepMap's SQL reader over the pinned corpus, and its scores.

Every threshold, seed, truth expectation and C6 target comes from PREREG.md at run time (fetch_corpus.load_amended:
the JSON blocks of its two hash-locked sections, with the hash-locked "Amendments before data" laid over them); this
file holds no second copy. Every result file, dump, sheet and capture records the sha256 of the three locked
sections, the registration it used and the identity of the tree that made it (PREREG A1: `code` = build/*.py,
`reader_gates` = tests/sql_reader_gates.py, `kit` = benchmark/sql_reader/*.py, each in the call-linking kit's
scheme: paths in byte order, each its path, a NUL, its bytes and a NUL), so `decide` refuses results measured against
another registration or on more than one identity. Every scoring command appends its result to ledger.jsonl in its
output directory (PREREG A2), and `decide` reads that ledger: a FAIL is final. --registered / --protocol FILE replace
PREREG.md's JSON (fixtures and tests/sql_prereg_gates.py only): a result made that way is never decision data.

Nothing is written inside this repository: --out (and --corpus, --tree, --clone) must lie outside it (exit 2). The
corpus is checked against its registration (fetch_corpus verify) before any reading; a source that does not verify
refuses the run (exit 2).

commands (PREREG "Protocol and criteria" names which one measures which criterion):
  truth      --corpus DIR                  the exact-truth sets from the source alone (C1's truth; no reader)
  metrics    --corpus DIR --out DIR        C1 recall and C3 time through build/sql_reader.read_file, and the C2 pool
  sample     --pool FILE --corpus DIR --out DIR --seed S [--n N] [--exclude SHEET ...]   one C2 judging sheet (the
             earlier registered sheets, and only they, excluded)
  prompt     --sheet S.json --out FILE      the judge's prompt for one sheet (PREREG A3), and its sha256
  audit      --sheet S.json [...] --verdicts V.json [...] --pool POOL --out DIR [--fixes FIXES.json]
             [--fresh S4.json --fresh-verdicts V4.json --fresh-pool POOL]   C2 (verdicts matched to sheets by sha256)
  gate       --python PY --out DIR         tests/sql_reader_gates.py under PY, recorded (C3's gate F)
  dump       --tree DIR --id ID --sql on|off --out FILE   one extraction dump, under the Python that runs it (C4, C5)
  parity     --dumps DIR --out DIR         C4 and C5 from every tree's four dumps (2 engine ends x reader on / off)
  c6-draw    --tree DIR --source ID [--check]            the seeded C6 target draw (and its check against PREREG)
  c6-capture --clone DIR --source ID --arm on|off --out DIR   live: top-15 for the registered queries (C6)
  c6-score   --off FILE --on FILE --source ID --out DIR       C6 for one repository
  decide     --results DIR [--out DIR]     the decision from every result file and the ledger (ALL pass -> FLIP)
  codehash   [--build DIR]                 the three identity hashes (PREREG A1)
Exit codes: 0 done (a score or decision that PASSES); 1 a score that fails / STAYS OPT-IN; 2 refused (nothing
written); 3 VOID (written, with its reason; never data).
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import platform
import random
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import fetch_corpus as F  # noqa: E402

REPO = F.REPO
BUILD = REPO / "build"
KIT_CL = REPO / "benchmark" / "call_linking"
READER_GATES = REPO / "tests" / "sql_reader_gates.py"
READER_GATES_REL = "tests/sql_reader_gates.py"
SCHEMA = "g-sql/result/1"
LEDGER = "ledger.jsonl"                                 # PREREG A2: one per results directory, append-only
KINDS_FROM_KEY = {"materializedview": "materialized view", "tabletype": "table type"}
DECL_LABELS = ("Class", "Function")
SSDT_KINDS = {"table", "view", "procedure", "function", "type", "table type"}
HOME_MAX = 64                                           # a longer DEEPMAP_HOME pushes graphify past Windows MAX_PATH
VERDICTS = ("TRUE", "FALSE", "UNSURE")
ITEM_KEYS = ("source", "rel", "line", "kind", "qualified", "name", "key")   # a pool item (a sheet adds "context")
# The graph-shaping switches a dump clears (only DEEPMAP_SQL_DECLARATIONS, set per arm, and the pinned engine remain).
DUMP_KEEP_ENV = ("DEEPMAP_HOME", "DEEPMAP_PYTHON", "DEEPMAP_GRAPHIFY_TIMEOUT")


def say(msg: str) -> None:
    print(f"[run_corpus] {msg}", flush=True)


# ---------------------------------------------------------------------------------------------------- provenance
def code_hash(build_dir=None) -> dict:
    """sha256 over build/*.py (sorted by name; name + NUL + bytes + NUL per file): the call-linking kit's scheme
    (benchmark/call_linking/_common.build_code_hash), so both kits name one tree the same way."""
    d = Path(build_dir or BUILD)
    h = hashlib.sha256()
    files = sorted(p for p in d.glob("*.py") if p.is_file())
    for p in files:
        h.update(p.name.encode("utf-8") + b"\0")
        h.update(p.read_bytes() + b"\0")
    return {"sha256": h.hexdigest(), "files": len(files)}


def _path_hash(root, rels) -> str:
    """The call-linking kit's scheme (benchmark/call_linking/_common._path_hash): sha256 over `rels` in byte order of
    their forward-slash paths, each contributing its path (UTF-8), a NUL, its bytes and a NUL."""
    h = hashlib.sha256()
    for rel in sorted(rels, key=lambda r: r.encode("utf-8")):
        h.update(rel.encode("utf-8") + b"\0")
        h.update((Path(root) / rel).read_bytes() + b"\0")
    return h.hexdigest()


def kit_hash(kit_dir=None) -> dict:
    """PREREG A1's kit hash: _path_hash over benchmark/sql_reader/*.py by file name (what measures and scores; PREREG
    section 1 registers it before the first decision run)."""
    d = Path(kit_dir or HERE)
    rels = [p.name for p in d.glob("*.py") if p.is_file()]
    return {"sha256": _path_hash(d, rels), "files": len(rels)}


def reader_gates_hash(path=None) -> dict:
    """PREREG A1's `reader_gates` hash: _path_hash over tests/sql_reader_gates.py (C3's gate run and C2's negative
    cases depend on it; a C2 fix changes it together with the code). sha256 None when the file is missing."""
    p = Path(path or READER_GATES)
    if not p.is_file():
        return {"sha256": None, "file": READER_GATES_REL}
    h = hashlib.sha256()
    h.update(READER_GATES_REL.encode("utf-8") + b"\0")
    h.update(p.read_bytes() + b"\0")
    return {"sha256": h.hexdigest(), "file": READER_GATES_REL}


def identity_of(rec: dict) -> tuple:
    """PREREG A1: the (code, reader_gates, kit) sha256 a record (result, dump, sheet, capture) was made at."""
    return tuple((rec.get(k) or {}).get("sha256") for k in ("code", "reader_gates", "kit"))


def prereg_hashes(path=None) -> dict:
    """sha256 of PREREG.md's three locked sections (tests/sql_prereg_gates.py's rule), None for a missing one."""
    text = Path(path or F.PREREG).read_text(encoding="utf-8")
    out = {}
    for name, (b, e) in {"protocol": (F.PROTOCOL_BEGIN, F.PROTOCOL_END),
                         "registered": (F.REGISTERED_BEGIN, F.REGISTERED_END)}.items():
        sec, _ = F.block(text, b, e)
        out[name] = hashlib.sha256(sec.encode("utf-8")).hexdigest() if sec is not None else None
    sec, _ = F.amendments_block(text)
    out["amendments"] = hashlib.sha256(sec.encode("utf-8")).hexdigest() if sec is not None else None
    return out


def provenance() -> dict:
    """The identity hashes and the interpreter of this run (every result, dump, sheet and capture records them)."""
    return {"code": code_hash(), "reader_gates": reader_gates_hash(), "kit": kit_hash(),
            "python": sys.version.split()[0], "platform": platform.platform()}


def load(args) -> "tuple[dict, dict, dict]":
    """(protocol, registered, provenance) for a command: PREREG.md's JSON with the amendments laid over it, or the
    --protocol / --registered files (a fixture registration: never amended, never decision data)."""
    prov = {"registration": "PREREG.md", "prereg": None}
    if getattr(args, "protocol", None):
        with open(args.protocol, encoding="utf-8") as fh:
            proto = json.load(fh)
        prov["registration"] = "fixture"
    else:
        proto = F.load_protocol()
    if getattr(args, "registered", None):
        with open(args.registered, encoding="utf-8") as fh:
            reg = json.load(fh)
        prov["registration"] = "fixture"
    else:
        reg = F.load_registered()
    if prov["registration"] == "PREREG.md":
        proto, reg = F.amend(proto, reg, F.load_amendments())
        prov["prereg"] = prereg_hashes()
    prov.update(provenance())
    return proto, reg, prov


# ---------------------------------------------------------------------------------------------------- ledger (A2)
def _sha_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def file_sha(path) -> str:
    with open(F._os_path(path), "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def outcomes(res: dict) -> dict:
    """The outcome (pass / fail / void; C2 also pending) of every criterion a result measures: what the ledger keeps."""
    kind = res.get("kind")
    if kind == "metrics":
        return {"C1": res.get("c1_outcome"), "C3": (res.get("c3") or {}).get("outcome")}
    if kind == "gate":
        return {f"C3-gate-{res.get('engine') or 'unknown'}": "pass" if res.get("pass") else "fail"}
    if kind == "audit":
        return {"C2": res.get("outcome")}
    if kind == "parity":
        return {"C4": res.get("c4_outcome"), "C5": res.get("c5_outcome")}
    if kind == "c6":
        return {f"C6-{res.get('source')}": res.get("outcome")}
    return {}


def ledger_line(lines: list, fname: str, sha: str, res: dict) -> str:
    """The ledger line (PREREG A2) that follows `lines` for the result `fname` (file sha256 `sha`): its sequence
    number, the sha256 of the line before it, and the result's registration, PREREG hashes, identity and outcomes."""
    entry = {"seq": len(lines) + 1, "prev": _sha_text(lines[-1]) if lines else "",
             "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "file": fname, "sha256": sha,
             "kind": res.get("kind"), "registration": res.get("registration"), "prereg": res.get("prereg"),
             "identity": list(identity_of(res)), "outcomes": outcomes(res)}
    if res.get("kind") == "c6":
        entry["fallback_applies"] = bool(res.get("fallback_applies"))
    if res.get("kind") == "audit":
        entry["bindings"] = res.get("bindings") or []
    return json.dumps(entry, sort_keys=True, ensure_ascii=False)


def read_ledger(path) -> list:
    p = F._os_path(path)
    if not os.path.isfile(p):
        return []
    with open(p, encoding="utf-8") as fh:
        return [ln.rstrip("\r\n") for ln in fh if ln.strip()]


def write_result(out_dir, fname: str, res: dict) -> None:
    """Write a scoring command's result into `out_dir` and append it to that directory's ledger (PREREG A2)."""
    path = Path(out_dir) / fname
    write_json(path, res)
    lines = read_ledger(Path(out_dir) / LEDGER)
    line = ledger_line(lines, fname, file_sha(path), res)
    with open(F._os_path(Path(out_dir) / LEDGER), "a", encoding="utf-8", newline="\n") as fh:
        fh.write(line + "\n")


def ledger_problems(lines: list, results: dict, shas: dict, current: dict, ident: tuple, registered_kit,
                    allow_fixture=False) -> list:
    """Why the ledger forbids a FLIP (PREREG A2, A3): a broken chain; a result file that is not the last line for its
    name; a line measured against other PREREG blocks, by a fixture or with another kit; a FAIL that is final (any,
    except a C6 FAIL the fallback applies to, once, at one identity other than the decision's); a sheet bound to two
    verdicts files."""
    probs, entries, prev = [], [], ""
    for k, ln in enumerate(lines, 1):
        try:
            e = json.loads(ln)
        except ValueError:
            return probs + [f"ledger line {k} is not JSON"]
        if e.get("seq") != k or e.get("prev") != prev:
            return probs + [f"ledger line {k}: the chain is broken (a line edited, removed or reordered)"]
        prev = _sha_text(ln)
        entries.append(e)
    last = {}
    for e in entries:
        last[e.get("file")] = e
    for name in sorted(results):
        e = last.get(name)
        if e is None or e.get("sha256") != shas.get(name):
            probs.append(f"{name} is not the last ledger line for it (every result is a recorded run)")
    fallback, bound = set(), {}
    for k, e in enumerate(entries, 1):
        if e.get("registration") != "PREREG.md" and not allow_fixture:
            probs.append(f"ledger line {k} is a {e.get('registration')!r} run: a results directory holds decision runs")
        if e.get("prereg") != current.get("prereg") and not allow_fixture:
            probs.append(f"ledger line {k} was measured against other PREREG blocks (a change after data)")
        if registered_kit and (e.get("identity") or [None] * 3)[2] != registered_kit:
            probs.append(f"ledger line {k} ran another kit than the registered one")
        for crit, st in sorted((e.get("outcomes") or {}).items()):
            if st != "fail":
                continue
            if crit.startswith("C6-") and e.get("fallback_applies"):
                fallback.add(tuple(e.get("identity") or ()))
                continue
            probs.append(f"ledger line {k}: {crit} FAILED ({e.get('file')}): a FAIL is final for this registration")
        for pair in e.get("bindings") or []:
            bound.setdefault(pair[0], set()).add(pair[1])
    if len(fallback) > 1:
        probs.append(f"C6 failed for the fallback at {len(fallback)} identities: 4.8 applies it once")
    elif fallback and ident in fallback:
        probs.append("a C6 FAIL the fallback applies to stands at the decision identity: the fallback was not made")
    for s, vs in sorted(bound.items()):
        if len(vs) > 1:
            probs.append(f"sheet {s[:12]} was judged by {len(vs)} verdicts files: a sheet is judged once (A3)")
    return probs


def write_json(path, obj) -> None:
    p = Path(path)
    os.makedirs(F._os_path(p.parent), exist_ok=True)
    with open(F._os_path(p), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, indent=1, sort_keys=True, ensure_ascii=False)
        fh.write("\n")


def read_json(path) -> dict:
    with open(F._os_path(path), encoding="utf-8") as fh:
        return json.load(fh)


def refuse(msg: str) -> int:
    say(f"REFUSED: {msg}")
    return 2


def _paths_problem(args, names=("out", "corpus", "tree", "clone", "dumps", "results")) -> "str | None":
    for n in names:
        v = getattr(args, n, None)
        if v and F.inside_repo(v):
            return f"--{n} {v} is inside this repository (the corpus and every result stay outside it)"
    return None


def _source(reg: dict, sid: str) -> dict:
    for s in reg.get("sources") or []:
        if s.get("id") == sid:
            return s
    raise KeyError(f"no registered source {sid!r}")


def verified_sources(corpus, reg: dict, ids=None) -> "tuple[list, list]":
    """(sources that verify, problems) for the registered sources (or `ids`)."""
    ok, probs = [], []
    for s in reg.get("sources") or []:
        if ids is not None and s["id"] not in ids:
            continue
        res = F.verify_source(corpus, s)
        if res["ok"]:
            ok.append(s)
        else:
            probs.append(f"{s['id']}: {res['problems']}")
    return ok, probs


def machine_load() -> "float | None":
    """The CPU load in percent (PREREG C3: the box is quiet): Windows' LoadPercentage, elsewhere the 1-minute load
    average over the CPU count; None when it cannot be read (C3 is then VOID)."""
    try:
        if os.name == "nt":
            r = subprocess.run(["powershell", "-NoProfile", "-Command",
                                "(Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage "
                                "-Average).Average"], capture_output=True, text=True, timeout=60)
            return float(r.stdout.strip())
        return round(100.0 * os.getloadavg()[0] / (os.cpu_count() or 1), 1)
    except Exception:  # noqa: BLE001  (a record, never a reason to stop)
        return None


# ---------------------------------------------------------------------------------------------------- truth (C1)
_PART = r'(?:"[^"\n]+"|\[[^\]\n]+\]|`[^`\n]+`|[A-Za-z_][\w$#@]*)'
TRUTH_DECL = re.compile(
    r"^CREATE(?:[ \t]+OR[ \t]+(?:REPLACE|ALTER))?"
    r"((?:[ \t]+(?:DEFINER[ \t]*=[ \t]*\S+|SQL[ \t]+SECURITY[ \t]+\w+|ALGORITHM[ \t]*=[ \t]*\w+|UNLOGGED|"
    r"MATERIALIZED))*)"
    r"[ \t]+(TABLE|VIEW|PROCEDURE|PROC|FUNCTION|TRIGGER)[ \t]+(?:IF[ \t]+NOT[ \t]+EXISTS[ \t]+)?"
    r"(" + _PART + r"(?:[ \t]*\.[ \t]*" + _PART + r"){0,3})", re.I | re.M)
TRUTH_KIND = {"TABLE": "table", "VIEW": "view", "PROCEDURE": "procedure", "PROC": "procedure",
              "FUNCTION": "function", "TRIGGER": "trigger"}


def _unquote(part: str) -> str:
    part = part.strip()
    if len(part) >= 2 and part[0] + part[-1] in ('""', "[]", "``"):
        return part[1:-1]
    return part


def qualified_of(name_text: str) -> str:
    """The parts of a written name, unquoted, joined with '.', lower-cased (C1's comparison form)."""
    parts = re.findall(_PART, name_text)
    return ".".join(_unquote(p) for p in parts).lower()


def line_start_truth(text: str) -> list:
    """(kind, qualified lower, line) of every declaration that starts a line at column 0 (C1's truth rule: a CREATE
    of a TABLE, VIEW, PROC[EDURE], FUNCTION or TRIGGER, optionally OR REPLACE / OR ALTER, DEFINER / SQL SECURITY /
    ALGORITHM, UNLOGGED or MATERIALIZED; never TEMP / TEMPORARY, never an indented line)."""
    out = []
    for m in TRUTH_DECL.finditer(text):
        kind = TRUTH_KIND[m.group(2).upper()]
        if kind == "view" and re.search(r"(?i)\bMATERIALIZED\b", m.group(1) or ""):
            kind = "materialized view"
        out.append((kind, qualified_of(m.group(3)), text.count("\n", 0, m.start()) + 1))
    return out


def _decode_for_truth(data: bytes) -> str:
    """The text a truth rule reads: a BOM or UTF-16 handled, else UTF-8, else cp1252 (never the reader's decode)."""
    if data[:3] == b"\xef\xbb\xbf":
        return data[3:].decode("utf-8", "replace")
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16", "replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252", "replace")


def truth_sets(corpus, reg: dict) -> dict:
    """{set: {"rule", "source", "truth": ...}} of every registered exact-truth set, read from the source alone."""
    out = {}
    for name, t in sorted((reg.get("truth") or {}).items()):
        src = _source(reg, t["source"])
        root = F.project_root(corpus, src)
        if t["rule"] == "line-start":
            with open(F._os_path(root / t["file"]), "rb") as fh:
                items = line_start_truth(_decode_for_truth(fh.read()))
            counts: dict = {}
            for k, _q, _l in items:
                counts[k] = counts.get(k, 0) + 1
            out[name] = {"rule": "line-start", "source": t["source"], "file": t["file"], "counts": counts,
                         "items": sorted({(k, q) for k, q, _ in items}), "expected": t["kinds"],
                         "duplicates": len(items) - len({(k, q) for k, q, _ in items})}
        elif t["rule"] == "ssdt":
            files = []
            pre = t["dir"].strip("/") + "/" if t["dir"].strip("/") else ""
            for rel, _full in F.sql_files(corpus, src):
                parts = rel[len(pre):].split("/") if rel.startswith(pre) else []
                if len(parts) == 3 and parts[1] in t["folders"]:
                    files.append((rel, parts[1], Path(parts[2]).stem))
            counts = {}
            for _r, folder, _s in files:
                counts[folder] = counts.get(folder, 0) + 1
            out[name] = {"rule": "ssdt", "source": t["source"], "files": files, "counts": counts,
                         "expected": t["folders"]}
        else:
            raise ValueError(f"unknown truth rule {t['rule']!r}")
    return out


def truth_void(ts: dict) -> "str | None":
    """Why a truth set is not the registered one (its counts differ from PREREG's): the run is VOID, not a fail."""
    if ts["counts"] != ts["expected"]:
        return f"truth counts {ts['counts']} are not the registered {ts['expected']}"
    if ts.get("duplicates"):
        return f"{ts['duplicates']} duplicate truth name(s): set equality would not be exact"
    return None


def decls_of(nodes: list) -> list:
    """(kind, qualified lower, name, line, key) of the declaration nodes read_file returned (what reaches the graph)."""
    out = []
    for n in nodes:
        if n.get("label") not in DECL_LABELS:
            continue
        tail = n["key"].split("#", 1)[1]
        kind, qual = tail.split(":", 1)
        out.append((KINDS_FROM_KEY.get(kind, kind), qual, n.get("name"), n.get("start_line"), n["key"]))
    return out


def score_c1(ts: dict, found: dict, proto: dict) -> dict:
    """C1 for one truth set. `found`: {rel: [decls_of(...)]} of the set's source. A line-start set compares every kind
    the truth rule reads (table, view, procedure, function, trigger, materialized view), a kind the set registers none
    of included (4.3: "and no other object of that kind"); a kind the rule does not read (type, package) is not
    judged."""
    void = truth_void(ts)
    if ts["rule"] == "ssdt":
        misses = []
        for rel, _folder, stem in ts["files"]:
            if not any(d[0] in SSDT_KINDS and str(d[2]).lower() == stem.lower() for d in found.get(rel, [])):
                misses.append(rel)
        n = len(ts["files"])
        recall = (n - len(misses)) / n if n else 0.0
        return {"rule": "ssdt", "files": n, "found": n - len(misses), "recall": recall, "misses": misses[:20],
                "threshold": proto["c1"]["ssdt_min_recall"], "void": void,
                "pass": void is None and n > 0 and recall >= proto["c1"]["ssdt_min_recall"]}
    judged = set(TRUTH_KIND.values()) | {"materialized view"} | set(ts["expected"])   # 4.3: every kind it reads
    got = {(d[0], d[1]) for d in found.get(ts["file"], []) if d[0] in judged}
    want = {tuple(x) for x in ts["items"]}
    per_kind = {}
    for k in sorted(judged):
        w = {q for kk, q in want if kk == k}
        g = {q for kk, q in got if kk == k}
        per_kind[k] = {"truth": len(w), "found": len(g & w), "extra": len(g - w)}
    return {"rule": "line-start", "per_kind": per_kind, "missing": sorted(want - got)[:20],
            "extra": sorted(got - want)[:20], "void": void, "pass": void is None and got == want}


# ---------------------------------------------------------------------------------------------------- metrics
def _reader():
    if str(BUILD) not in sys.path:
        sys.path.insert(0, str(BUILD))
    import sql_reader  # noqa: WPS433  (the product: build/sql_reader.py)
    return sql_reader


def read_pass(corpus, sources: list) -> dict:
    """One pass of read_file over every registered SQL file (no cache, no budget): per file its state, size, seconds
    and declaration nodes."""
    R = _reader()
    out = {}
    for s in sources:
        rows = []
        for rel, full in F.sql_files(corpus, s):
            t0 = time.perf_counter()
            nodes, info = R.read_file(full, rel)
            dt = time.perf_counter() - t0
            rows.append({"rel": rel, "bytes": os.path.getsize(full), "seconds": dt, "state": info["state"],
                         "dialect": info.get("dialect"), "encoding": info.get("encoding"),
                         "dynamic_unread": info.get("dynamic_unread", 0), "decls": decls_of(nodes),
                         "modules": sum(1 for n in nodes if n.get("label") == "Module")})
        out[s["id"]] = rows
    return out


def score_c3(passes: list, proto: dict) -> dict:
    """C3 from the passes: corpus ms/KB per pass over the files read (state "read"), its median; the slowest file over
    EVERY file whatever its state (PREREG A5: a file past the reader's own deadline comes back "slow" and is the worst
    case the 10 s bound is for); a file the reader did not read (slow, budget, too_large) fails C3."""
    c3 = proto["c3"]
    read = set(c3.get("read_states") or ["read"])
    per, not_read = [], []
    worst = (0.0, None)
    for p in passes:
        secs = kb = 0.0
        for sid, rows in p.items():
            for r in rows:
                if r["seconds"] > worst[0]:
                    worst = (r["seconds"], f"{sid}:{r['rel']}")
                if r["state"] not in read:
                    not_read.append(f"{sid}:{r['rel']} ({r['state']})")
                    continue
                secs += r["seconds"]
                kb += r["bytes"] / 1024.0
        per.append(1000.0 * secs / kb if kb else None)
    vals = [v for v in per if v is not None]
    med = statistics.median(vals) if vals else None
    return {"ms_per_kb_passes": per, "ms_per_kb": med, "statistic": c3["statistic"], "max_file_seconds": worst[0],
            "slowest": worst[1], "threshold_ms_per_kb": c3["max_ms_per_kb"], "not_read": sorted(set(not_read))[:20],
            "threshold_file_seconds": c3["max_file_seconds"], "passes": len(passes),
            "pass_time": med is not None and len(passes) == c3["passes"] and med <= c3["max_ms_per_kb"]
            and worst[0] <= c3["max_file_seconds"] and not not_read}


def pool_items(passes0: dict, proto: dict) -> list:
    """The C2 pool: every declaration node of the registered pool sources, in its fixed order."""
    items = []
    for spec in proto["c2"]["pool"]:
        for r in passes0.get(spec["source"], []):
            if spec.get("prefix") and not r["rel"].startswith(spec["prefix"]):
                continue
            for kind, qual, name, line, key in r["decls"]:
                items.append({"source": spec["source"], "rel": r["rel"], "line": line, "kind": kind,
                              "qualified": qual, "name": name, "key": key})
    items.sort(key=lambda d: (d["source"], d["rel"].encode("utf-8"), d["line"], d["kind"], d["qualified"]))
    return items


def pool_text(items: list) -> str:
    """pool.jsonl's text: one sorted-key JSON object per item, LF-terminated."""
    return "".join(json.dumps(it, sort_keys=True, ensure_ascii=False) + "\n" for it in items)


def pool_sha(items: list) -> str:
    """sha256 of pool.jsonl's bytes (PREREG A4: metrics records it, every sheet carries it, audit checks it)."""
    return _sha_text(pool_text(items))


def read_pool(path) -> list:
    with open(F._os_path(path), encoding="utf-8") as fh:
        return [json.loads(ln) for ln in fh if ln.strip()]


def _criterion(parts: list) -> str:
    """One outcome from several parts ({"pass", "void"}): fail if any part measured a fail, else void if any part is
    VOID (or there is none), else pass."""
    if any(not p.get("void") and not p.get("pass") for p in parts):
        return "fail"
    return "void" if (not parts or any(p.get("void") for p in parts)) else "pass"


def cmd_metrics(args) -> int:
    proto, reg, prov = load(args)
    srcs, probs = verified_sources(args.corpus, reg)
    if probs:
        return refuse(f"the corpus does not verify: {probs}")
    load_before = machine_load()
    passes = [read_pass(args.corpus, srcs) for _ in range(int(args.passes or proto["c3"]["passes"]))]
    p0 = passes[0]
    found = {}
    for sid, rows in p0.items():
        found[sid] = {r["rel"]: r["decls"] for r in rows}
    c1 = {}
    for name, ts in truth_sets(args.corpus, reg).items():
        c1[name] = score_c1(ts, found.get(ts["source"], {}), proto)
    c3 = score_c3(passes, proto)
    load_after = machine_load()
    loads = [load_before, load_after]
    c3["load"] = loads
    c3["void"] = (None if all(x is not None and x <= proto["c3"]["quiet_max_load"] for x in loads) else
                  f"the box was not shown quiet (CPU load before / after {loads}, at most "
                  f"{proto['c3']['quiet_max_load']} %): measure the time again")
    c3["outcome"] = "void" if c3["void"] else ("pass" if c3["pass_time"] else "fail")
    pool = pool_items(p0, proto)
    dev = dev_judged(proto)
    summary = {sid: {"files": len(rows), "bytes": sum(r["bytes"] for r in rows),
                     "states": {st: sum(1 for r in rows if r["state"] == st)
                                for st in sorted({r["state"] for r in rows})},
                     "declarations": sum(len(r["decls"]) for r in rows),
                     "dynamic_unread": sum(r["dynamic_unread"] for r in rows)} for sid, rows in p0.items()}
    res = {"schema": SCHEMA, "kind": "metrics", **prov, "sources": summary, "c1": c1,
           "c1_pass": bool(c1) and all(v["pass"] for v in c1.values()), "c1_outcome": _criterion(list(c1.values())),
           "c1_void": [k for k, v in c1.items() if v["void"]], "c3": c3, "pool": len(pool),
           "pool_after_exclusion": len(decision_pool(pool, dev)), "pool_sha256": pool_sha(pool)}
    os.makedirs(F._os_path(args.out), exist_ok=True)
    with open(F._os_path(Path(args.out) / "pool.jsonl"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(pool_text(pool))
    write_result(args.out, "metrics.json", res)
    say(f"C1 {'PASS' if res['c1_pass'] else 'FAIL'} {({k: v['pass'] for k, v in c1.items()})}; C3 median "
        f"{c3['ms_per_kb']} ms/KB, slowest file {c3['max_file_seconds']:.2f} s; pool {len(pool)} declarations "
        f"({res['pool_after_exclusion']} after A7's exclusion)")
    if res["c1_void"] or c3["void"]:
        return 3
    return 0 if res["c1_pass"] and c3["pass_time"] else 1


# ---------------------------------------------------------------------------------------------------- C2
def item_id(it: dict) -> str:
    return f"{it['source']}\t{it['rel']}\t{it['kind']}\t{it['qualified']}"


def draw(pool: list, seed: int, n: int, exclude=()) -> list:
    """C2's draw: the pool in its fixed order, shuffled by random.Random(seed), its first n items not in `exclude`."""
    ex = set(exclude)
    perm = list(pool)
    random.Random(int(seed)).shuffle(perm)
    return [it for it in perm if item_id(it) not in ex][:int(n)]


def dev_judged(proto: dict) -> set:
    """PREREG A7: the (source, path, line, kind) of every declaration development judged, from the kit's
    dev_judged.json (its sha256 locked by the amendment; a different file raises ValueError). Empty without A7."""
    spec = (proto.get("c2") or {}).get("dev_judged")
    if not spec:
        return set()
    with open(F._os_path(HERE / spec["file"]), "rb") as fh:
        data = fh.read().replace(b"\r\n", b"\n")   # LF in git; a Windows checkout (autocrlf) gives CRLF
    if hashlib.sha256(data).hexdigest() != spec["sha256"]:
        raise ValueError(f"{spec['file']} is not the file PREREG A7 locks (sha256 differs)")
    items = {tuple(x) for x in json.loads(data.decode("utf-8"))["items"]}
    if len(items) != spec.get("items"):
        raise ValueError(f"{spec['file']} holds {len(items)} identities, PREREG A7 says {spec.get('items')}")
    return items


def decision_pool(pool: list, dev) -> list:
    """The pool every C2 draw shuffles (A7): the pool without the development-judged identities, order kept."""
    return [it for it in pool if (it["source"], it["rel"], it["line"], it["kind"]) not in dev] if dev else list(pool)


def registered_draw(pool: list, proto: dict, seed: int, earlier: list) -> list:
    """The registered sheet for `seed` (4.4, A4, A7): its size (n, or fresh_n for the fresh seed) drawn from the
    decision pool, every item of the `earlier` sheets excluded."""
    c2 = proto["c2"]
    n = c2["fresh_n"] if int(seed) == c2["fresh_seed"] else c2["n"]
    return draw(decision_pool(pool, dev_judged(proto)), int(seed), n,
                [item_id(it) for sh in earlier for it in sh["items"]])


def _items(sheet_items: list) -> list:
    return [{k: it.get(k) for k in ITEM_KEYS} for it in sheet_items]


SHEET_RULE = ("Judge each item against the source text shown. TRUE when the line holds the CREATE / ALTER (or, for "
              "a package member, the PROCEDURE / FUNCTION header) that defines an object of exactly this kind with "
              "exactly this name (schema parts as written, quotes removed, case ignored), including a definition "
              "inside a string that the script executes (EXEC(...), sp_executesql, EXECUTE IMMEDIATE, a variable the "
              "same batch executes). FALSE otherwise: a comment, a message or data string, a privilege statement, a "
              "template placeholder, a temporary object, a wrong kind, a wrong name, a wrong line, an ALTER that "
              "redefines nothing. UNSURE when the text shown cannot decide it (UNSURE counts as FALSE).")


def judge_prompt(sheet: dict) -> str:
    """The C2 judge's whole prompt for one sheet (PREREG A3): the locked rule, the answer format and the sheet's items
    with their context. Deterministic; it names no identity, pool or verdict."""
    out = ["G-SQL criterion C2: judge one sheet of SQL declarations, each against the source text shown with it.",
           "", SHEET_RULE, "",
           'Answer with ONE JSON object and nothing else: {"verdicts": [{"i": <item number>, "verdict": "TRUE" or '
           '"FALSE" or "UNSURE", "why": "<one line>"}, ...]}, one entry for every item below.', "",
           f"Sheet: seed {sheet['seed']}, {len(sheet['items'])} items.", ""]
    for i, it in enumerate(sheet["items"]):
        out.append(f"Item {i}: {it['kind']} {it['qualified']} at line {it['line']} of {it['source']}: {it['rel']}")
        out += [f"    {c}" for c in it.get("context") or []]
        out.append("")
    return "\n".join(out)


def prompt_sha(sheet: dict) -> str:
    return _sha_text(judge_prompt(sheet))


def _rule_problem(proto: dict) -> "str | None":
    rule = (proto.get("c2") or {}).get("sheet_rule")
    if rule is not None and rule != SHEET_RULE:
        return "run_corpus.SHEET_RULE is not the rule PREREG A3 locks"
    return None


def cmd_sample(args) -> int:
    proto, reg, prov = load(args)
    c2 = proto["c2"]
    why = _rule_problem(proto)
    if why:
        return refuse(why)
    seed = int(args.seed)
    seeds = list(c2["seeds"])
    if seed not in seeds + [c2["fresh_seed"]]:
        return refuse(f"seed {seed} is not a registered C2 seed ({seeds}, fresh {c2['fresh_seed']})")
    n = c2["fresh_n"] if seed == c2["fresh_seed"] else c2["n"]
    if args.n is not None and int(args.n) != n:
        return refuse(f"seed {seed} draws its registered {n} items, never {args.n}")
    pool = read_pool(args.pool)
    psha = pool_sha(pool)
    earlier = [read_json(p) for p in args.exclude or []]
    want = seeds if seed == c2["fresh_seed"] else seeds[:seeds.index(seed)]
    if [s.get("seed") for s in earlier] != want:
        return refuse(f"seed {seed} excludes exactly the earlier registered sheets, seeds {want} in order "
                      f"(given {[s.get('seed') for s in earlier]})")
    if seed != c2["fresh_seed"]:
        for k, sh in enumerate(earlier):
            if sh.get("pool_sha256") != psha or _items(sh["items"]) != registered_draw(pool, proto, sh["seed"],
                                                                                          earlier[:k]):
                return refuse(f"the sheet for seed {sh.get('seed')} is not its registered draw from this pool")
    items = registered_draw(pool, proto, seed, earlier)
    R = _reader()
    srcs = {s["id"]: s for s in reg.get("sources") or []}
    for it in items:
        path = F.project_root(args.corpus, srcs[it["source"]]) / it["rel"]
        with open(F._os_path(path), "rb") as fh:
            text, _enc = R.decode(fh.read())
        lines = text.split("\n")
        a = max(0, it["line"] - 1 - c2["context_before"])
        b = min(len(lines), it["line"] + c2["context_after"])
        it["context"] = [f"{i + 1}: {lines[i].rstrip()[:240]}" for i in range(a, b)]
    sheet = {"schema": SCHEMA, "kind": "sheet", **prov, "seed": seed, "n": n, "pool": len(pool), "pool_sha256": psha,
             "excluded_sheets": [file_sha(p) for p in args.exclude or []], "rule": SHEET_RULE, "items": items}
    write_json(Path(args.out) / f"sheet_{seed}.json", sheet)
    say(f"sheet_{seed}.json: {len(items)} items from a pool of {len(pool)} (sheets {want} excluded); its judge prompt: "
        f"run_corpus.py prompt --sheet <it> --out <file>")
    return 0


def cmd_prompt(args) -> int:
    proto, _reg, _prov = load(args)
    why = _rule_problem(proto)
    if why:
        return refuse(why)
    text = judge_prompt(read_json(args.sheet))
    p = Path(args.out)
    os.makedirs(F._os_path(p.parent), exist_ok=True)
    with open(F._os_path(p), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    say(f"prompt for {Path(args.sheet).name}: {p} sha256 {_sha_text(text)} (the verdicts file records it)")
    return 0


def gate_b_text(path=None) -> str:
    """Section B of tests/sql_reader_gates.py as text (PREREG A4: where a fix's negative case must be): the source of
    every function named section_b*, and of every module-level constant those functions read."""
    p = Path(path or READER_GATES)
    src = p.read_text(encoding="utf-8")
    tree = ast.parse(src)
    funcs = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name.startswith("section_b")]
    names = {x.id for f in funcs for x in ast.walk(f) if isinstance(x, ast.Name)}
    parts = [ast.get_source_segment(src, f) or "" for f in funcs]
    for n in tree.body:
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in names for t in n.targets):
            parts.append(ast.get_source_segment(src, n) or "")
    return "\n".join(parts)


def _one_judging(sheet: dict, files: list, judge: dict, label: str, probs: list) -> "dict | None":
    """The one verdicts file for `sheet` among `files` (matched by the sheet's sha256), checked against the sheet's
    prompt and the pinned judge (A3), or None with the problem recorded."""
    mine = [v for v in files if v.get("sheet_sha256") == sheet.get("_file_sha256")]
    if len(mine) != 1:
        probs.append(f"{label}: {len(mine)} verdicts files name this sheet (one judging per sheet)")
        return None
    v = mine[0]
    if (v.get("judge") or {}).get("model") != judge.get("model"):
        probs.append(f"{label}: judged by {(v.get('judge') or {}).get('model')!r}, not {judge.get('model')!r}")
    if v.get("prompt_sha256") != prompt_sha(sheet):
        probs.append(f"{label}: the verdicts were given for another prompt than this sheet's")
    got = {int(x["i"]): x for x in v.get("verdicts") or []}
    if set(got) != set(range(len(sheet["items"]))) or len(v.get("verdicts") or []) != len(sheet["items"]):
        probs.append(f"{label}: {len(v.get('verdicts') or [])} verdicts for {len(sheet['items'])} items")
    if any(x.get("verdict") not in VERDICTS for x in got.values()):
        probs.append(f"{label}: a verdict that is not TRUE / FALSE / UNSURE")
    return v


def score_c2(sheets, verdicts, fresh=None, fresh_verdicts=None, proto=None, decision=None, *, pool=None,
             fresh_pool=None, fixes=None, b_text="") -> dict:
    """C2 (4.4 with A3, A4, A7). `sheets` and `fresh` carry `_file_sha256` (their file's sha256); `verdicts` and
    `fresh_verdicts` are verdicts files in any order, matched to their sheet by `sheet_sha256`; `pool` is the pool the
    three sheets were drawn from, `fresh_pool` the fresh sheet's; `decision` the decision identity (code, reader_gates,
    kit). Outcome: void (the sheets or verdicts are not the registered ones: never data), fail (fewer than min_true
    TRUE, or a non-TRUE item in the fresh sheet), pending (a fix or the fresh sheet still owed), pass."""
    c2 = proto["c2"]
    judge = c2.get("judge") or {}
    probs, bad = [], []
    if [s.get("seed") for s in sheets] != list(c2["seeds"]):
        probs.append(f"sheet seeds {[s.get('seed') for s in sheets]} are not the registered {c2['seeds']} (in order)")
    if pool is None or any(s.get("pool_sha256") != pool_sha(pool) for s in sheets):
        probs.append("the sheets do not come from the pool given (pool sha256)")
    else:
        for k, sh in enumerate(sheets):
            if _items(sh["items"]) != registered_draw(pool, proto, sh["seed"], sheets[:k]):
                probs.append(f"seed {sh['seed']}: the sheet is not its registered draw (A4)")
    known = {s.get("_file_sha256") for s in sheets}
    if any(v.get("sheet_sha256") not in known for v in verdicts):
        probs.append("a verdicts file names no sheet given")
    true = total = 0
    bindings = []
    for sh in sheets:
        v = _one_judging(sh, verdicts, judge, f"seed {sh.get('seed')}", probs)
        if v is None:
            continue
        bindings.append([sh.get("_file_sha256"), v.get("_file_sha256")])
        got = {int(x["i"]): x for x in v.get("verdicts") or []}
        for i, it in enumerate(sh["items"]):
            total += 1
            if (got.get(i) or {}).get("verdict") == "TRUE":
                true += 1
            else:
                bad.append({"seed": sh["seed"], "i": i, "id": item_id(it), "verdict": (got.get(i) or {}).get("verdict"),
                            "why": (got.get(i) or {}).get("why")})
    res = {"true": true, "judged": total, "min_true": c2["min_true"], "non_true": bad, "problems": probs,
           "pool_sha256": pool_sha(pool) if pool is not None else None, "fresh_pool_sha256": None,
           "sheet_identities": [list(identity_of(s)) for s in sheets], "fresh_identity": None, "fresh": None}
    first = total == c2["n"] * len(c2["seeds"]) and true >= c2["min_true"]
    res["pass_first"] = first
    moved = decision is not None and any(identity_of(s)[0] != decision[0] for s in sheets)
    res["code_moved"] = moved
    need = c2.get("fix_case_min_chars", 12)
    fx = {(int(f.get("seed", -1)), int(f.get("i", -1))): str(f.get("case") or "") for f in fixes or []}
    unfixed = [f"{b['seed']}/{b['i']}" for b in bad if len(fx.get((b["seed"], b["i"]), "").strip()) < need
               or fx[(b["seed"], b["i"])].strip() not in (b_text or "")]
    res["unfixed"] = unfixed
    if fresh is not None:
        f_probs = []
        if fresh.get("seed") != c2["fresh_seed"] or len(fresh.get("items") or []) != c2["fresh_n"]:
            f_probs.append(f"the fresh sheet is not seed {c2['fresh_seed']} with {c2['fresh_n']} items")
        if fresh_pool is None or fresh.get("pool_sha256") != pool_sha(fresh_pool):
            f_probs.append("the fresh sheet does not come from the fresh pool given (pool sha256)")
        elif _items(fresh["items"]) != registered_draw(fresh_pool, proto, fresh["seed"], sheets):
            f_probs.append("the fresh sheet is not its registered draw (A4)")
        if decision is not None and tuple(identity_of(fresh)) != tuple(decision):
            f_probs.append("the fresh sheet was not drawn at the decision identity (4.2)")
        fv = _one_judging(fresh, list(fresh_verdicts or []), judge, "fresh", f_probs)
        f_true = 0
        if fv is not None:
            got = {int(x["i"]): x for x in fv.get("verdicts") or []}
            f_true = sum(1 for i in range(len(fresh["items"])) if (got.get(i) or {}).get("verdict") == "TRUE")
            bindings.append([fresh.get("_file_sha256"), fv.get("_file_sha256")])
        probs += f_probs
        res.update(fresh={"seed": fresh.get("seed"), "n": len(fresh.get("items") or []), "true": f_true},
                   fresh_pool_sha256=fresh.get("pool_sha256"), fresh_identity=list(identity_of(fresh)))
    res["bindings"] = bindings
    if probs:
        outcome = "void"
    elif not first:
        outcome = "fail"
    elif not bad and not moved:
        outcome = "pass"
    elif fresh is None:
        outcome = "pending"
    elif res["fresh"]["true"] < c2["fresh_min_true"]:
        outcome = "fail"
    elif unfixed:
        outcome = "pending"
    else:
        outcome = "pass"
    res["outcome"] = outcome
    res["pass"] = outcome == "pass"
    if outcome == "pending":
        res["why"] = ("owed: " + ("the fresh sheet (4.4)" if fresh is None else f"a fix for {unfixed}"))
    return res


def cmd_audit(args) -> int:
    proto, _reg, prov = load(args)
    why = _rule_problem(proto)
    if why:
        return refuse(why)

    def loaded(path) -> dict:
        d = read_json(path)
        d["_file_sha256"] = file_sha(path)
        return d
    sheets = [loaded(p) for p in args.sheet]
    verdicts = [loaded(p) for p in args.verdicts]
    fresh = loaded(args.fresh) if args.fresh else None
    fresh_v = [loaded(p) for p in args.fresh_verdicts or []]
    if fresh is not None and not (fresh_v and args.fresh_pool):
        return refuse("--fresh needs --fresh-verdicts and --fresh-pool")
    fixes = read_json(args.fixes).get("fixes") if args.fixes else []
    res = score_c2(sheets, verdicts, fresh, fresh_v, proto, identity_of(prov), pool=read_pool(args.pool),
                   fresh_pool=read_pool(args.fresh_pool) if args.fresh_pool else None, fixes=fixes,
                   b_text=gate_b_text())
    res.update({"schema": SCHEMA, "kind": "audit", **prov,
                "sheet_registrations": [s.get("registration") for s in sheets]})
    write_result(args.out, "audit.json", res)
    say(f"C2 {res['outcome'].upper()}: {res['true']}/{res['judged']} TRUE (>= {res['min_true']})"
        + (f"; fresh {res['fresh']}" if res.get("fresh") else "") + (f"; {res['problems']}" if res["problems"] else "")
        + (f"; {res['why']}" if res.get("why") else ""))
    return {"pass": 0, "fail": 1, "pending": 1, "void": 3}[res["outcome"]]


# ---------------------------------------------------------------------------------------------------- gate (C3)
def cmd_gate(args) -> int:
    proto, _reg, prov = load(args)
    env = dict(os.environ, DEEPMAP_AUTO_SYNC="0", PYTHONIOENCODING="utf-8")
    t0 = time.time()
    r = subprocess.run([args.python, str(READER_GATES)], cwd=str(REPO), env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    ver = subprocess.run([args.python, "-c", "import importlib.metadata as m; print(m.version('graphifyy'))"],
                         capture_output=True, text=True).stdout.strip()
    after = reader_gates_hash()
    res = {"schema": SCHEMA, "kind": "gate", **prov, "gate": READER_GATES_REL, "engine": ver,
           "interpreter": args.python, "exit": r.returncode, "seconds": round(time.time() - t0, 1),
           "tail": (r.stdout or "")[-3000:], "pass": r.returncode == 0 and after == prov["reader_gates"]}
    if after != prov["reader_gates"]:
        res["tail"] += "\n[run_corpus] tests/sql_reader_gates.py changed during the run: not data"
    write_result(args.out, f"gate_{ver or 'unknown'}.json", res)
    say(f"tests/sql_reader_gates.py under graphifyy {ver}: exit {r.returncode}")
    return 0 if res["pass"] else 1


# ---------------------------------------------------------------------------------------------------- dump (C4, C5)
def home_problem(environ=None) -> "str | None":
    e = os.environ if environ is None else environ
    home = (e.get("DEEPMAP_HOME") or "").strip()
    if not home:
        return "DEEPMAP_HOME is unset: point it at a throwaway home with a short path (never ~/.deepmap)"
    if Path(home).resolve() == (Path.home() / ".deepmap").resolve():
        return "DEEPMAP_HOME is the operator's ~/.deepmap: use a throwaway home"
    if len(home) > HOME_MAX:
        return f"DEEPMAP_HOME is {len(home)} characters (at most {HOME_MAX}: graphify must stay under MAX_PATH)"
    if F.inside_repo(home):
        return "DEEPMAP_HOME is inside this repository"
    return None


def _canon(x) -> str:
    return json.dumps(x, sort_keys=True, ensure_ascii=False, default=str)


def is_sql_node(n: dict) -> bool:
    return str(n.get("key", "")).startswith("sql:") or str(n.get("source_file", "")).lower().endswith(".sql")


def _load_kit(name: str):
    spec = importlib.util.spec_from_file_location(f"_gsql_{name}", KIT_CL / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def cmd_dump(args) -> int:
    for k in [k for k in os.environ if k.startswith("DEEPMAP_") and k not in DUMP_KEEP_ENV]:
        os.environ.pop(k)
    os.environ.update({"DEEPMAP_SQL_DECLARATIONS": "1" if args.sql == "on" else "0",
                       "DEEPMAP_EXTRACTOR": "graphify", "DEEPMAP_AUTO_SYNC": "0", "HF_HUB_OFFLINE": "1"})
    why = home_problem()
    if why:
        return refuse(why)
    proto, _reg, prov = load(args)
    root = Path(args.tree).resolve()
    OC = _load_kit("offline_calls")
    S = OC.load_sync(str(BUILD))
    import edge_resolve  # noqa: WPS433  (on sys.path through load_sync)
    captured = {}
    orig = S._apply_recognizers

    def recorder(r, nodes):
        out = orig(r, nodes)
        captured["typed"] = out[1]
        return out
    t0 = time.perf_counter()
    extraction = S._rebuild_code(root)
    S._apply_recognizers = recorder
    try:
        nodes, _calls, edges, meta = OC.run_pipeline(S, root, extraction=extraction)
    finally:
        S._apply_recognizers = orig
    imports = extraction[2]
    live = sorted({n["key"] for n in nodes})
    rows = edge_resolve.edge_rows(captured.get("typed") or [], imports or [], edge_resolve.Resolver(nodes), live,
                                  set(live))
    info = getattr(extraction, "info", None) or {}
    sql_report = info.get("sql") if isinstance(info, dict) else None
    has_sql = bool(F.walk_sql(root))
    void = None
    if meta.get("engine") != "graphify":
        void = f"the extraction resolved to {meta.get('engine')!r}, not graphify"
    elif args.sql == "on" and has_sql and not (isinstance(sql_report, dict) and sql_report.get("state") == "read"):
        void = (f"the reader is on but the extraction carries no SQL report in state 'read' ({sql_report!r}): the "
                f"code under test does not read SQL")
    elif args.sql == "off" and isinstance(sql_report, dict) and sql_report.get("state") not in (None, "off"):
        void = f"the reader is off but the SQL report says {sql_report.get('state')!r}"
    try:
        engine_version = importlib.metadata.version("graphifyy")
    except Exception:  # noqa: BLE001
        engine_version = None
    dump = {"schema": SCHEMA, "kind": "dump", **prov, "id": args.id, "sql": args.sql, "engine": meta.get("engine"),
            "graphifyy": engine_version, "void": void, "sql_report": sql_report,
            "nodes": sorted((json.loads(_canon(n)) for n in nodes), key=_canon),
            "calls": sorted([a, b, str(c)] for (a, b), c in edges.items()),
            "rows": sorted((json.loads(_canon(list(r))) for r in rows), key=_canon),
            "seconds": round(time.perf_counter() - t0, 1)}
    write_json(args.out, dump)
    say(f"dump {args.id} sql={args.sql} graphifyy {engine_version}: {len(nodes)} nodes, {len(edges)} CALLS, "
        f"{len(rows)} typed / IMPORTS rows" + (f"; VOID: {void}" if void else ""))
    return 3 if void else 0


def _non_sql(dump: dict) -> dict:
    sql_keys = {n["key"] for n in dump["nodes"] if is_sql_node(n)}
    return {"nodes": [n for n in dump["nodes"] if not is_sql_node(n)],
            "calls": [c for c in dump["calls"] if c[0] not in sql_keys and c[1] not in sql_keys],
            "rows": [r for r in dump["rows"] if not any(isinstance(x, str) and x in sql_keys for x in r)]}


def _sql_edges(dump: dict) -> int:
    sql_keys = {n["key"] for n in dump["nodes"] if str(n.get("key", "")).startswith("sql:")}
    return (sum(1 for c in dump["calls"] if c[0] in sql_keys or c[1] in sql_keys)
            + sum(1 for r in dump["rows"] if any(isinstance(x, str) and x in sql_keys for x in r)))


REPORT_FIELDS = ("state", "files", "declarations", "modules", "too_large", "budget", "slow", "unreadable",
                 "dynamic_unread", "ignored", "engine_nodes_dropped")


def score_parity(dumps: list, proto: dict, ident=None, stamp=None) -> dict:
    """C4 (per tree: the reader-on SQL nodes and SQL report identical on both engine ends) and C5 (per tree and end:
    non-SQL nodes, CALLS, typed and IMPORTS rows identical reader on vs off; no SQL node holds an edge). A tree whose
    dumps were made at another identity than `ident` (PREREG A1), or under another registration / PREREG blocks than
    `stamp`, is VOID: re-scoring old dumps under new code is never data."""
    ends = list(proto["engines"])
    by = {}
    for d in dumps:
        by.setdefault(d["id"], {})[(d.get("graphifyy"), d["sql"])] = d
    c4, c5 = {}, {}
    for tid, ds in sorted(by.items()):
        want = {(e, a) for e in ends for a in ("on", "off")}
        miss = sorted(want - set(ds))
        voids = [f"{k}: {d['void']}" for k, d in ds.items() if d.get("void")]
        other = [str(k) for k, d in ds.items() if ident is not None and tuple(identity_of(d)) != tuple(ident)]
        foreign = [str(k) for k, d in ds.items() if stamp is not None
                   and (d.get("registration"), d.get("prereg")) != (stamp.get("registration"), stamp.get("prereg"))]
        if miss or voids or other or foreign:
            why = (f"missing dumps {miss}" if miss else f"VOID dumps {voids}" if voids else
                   f"dumps {other} were made at another identity than this parity run's (A1)" if other else
                   f"dumps {foreign} were made under another registration or other PREREG blocks")
            c4[tid] = {"pass": False, "void": why}
            c5[tid] = dict(c4[tid])
            continue
        a, b = ds[(ends[0], "on")], ds[(ends[1], "on")]
        sa = [n for n in a["nodes"] if str(n.get("key", "")).startswith("sql:")]
        sb = [n for n in b["nodes"] if str(n.get("key", "")).startswith("sql:")]
        ra = {k: (a["sql_report"] or {}).get(k) for k in REPORT_FIELDS}
        rb = {k: (b["sql_report"] or {}).get(k) for k in REPORT_FIELDS}
        c4[tid] = {"pass": sa == sb and ra == rb, "void": None, "sql_nodes": [len(sa), len(sb)],
                   "only_first": [n["key"] for n in sa if n not in sb][:10],
                   "only_second": [n["key"] for n in sb if n not in sa][:10], "report_equal": ra == rb}
        c5[tid] = {"void": None}
        ok = True
        for e in ends:
            on, off = _non_sql(ds[(e, "on")]), _non_sql(ds[(e, "off")])
            same = {k: on[k] == off[k] for k in ("nodes", "calls", "rows")}
            sql_edges = _sql_edges(ds[(e, "on")])
            c5[tid][e] = {"identical": same, "sql_edges": sql_edges,
                          "sql_nodes_added": sum(1 for n in ds[(e, "on")]["nodes"] if is_sql_node(n))
                          - sum(1 for n in ds[(e, "off")]["nodes"] if is_sql_node(n))}
            ok = ok and all(same.values()) and sql_edges == 0
        c5[tid]["pass"] = ok
    codes = sorted({str((d.get("code") or {}).get("sha256")) for d in dumps})
    idents = sorted({tuple(identity_of(d)) for d in dumps}, key=str)
    one = len(idents) == 1
    c4_out = _criterion(list(c4.values())) if one else "void"
    c5_out = _criterion(list(c5.values())) if one else "void"
    return {"c4": c4, "c5": c5, "codes": codes, "dump_identities": [list(i) for i in idents],
            "c4_outcome": c4_out, "c5_outcome": c5_out, "c4_pass": c4_out == "pass", "c5_pass": c5_out == "pass"}


def cmd_parity(args) -> int:
    proto, reg, prov = load(args)
    dumps = [read_json(p) for p in sorted(Path(args.dumps).glob("*.json"))]
    res = score_parity(dumps, proto, identity_of(prov), prov)
    trees = sorted({d["id"] for d in dumps})
    want = sorted(s["id"] for s in reg.get("sources") or [])
    res.update({"schema": SCHEMA, "kind": "parity", **prov, "trees": trees, "trees_registered": want})
    if trees != want:
        res["c4_pass"] = res["c5_pass"] = False
        res["c4_outcome"] = "fail" if res["c4_outcome"] == "fail" else "void"
        res["c5_outcome"] = "fail" if res["c5_outcome"] == "fail" else "void"
        res["void"] = f"dumps for {trees}, registered sources {want}"
    write_result(args.out, "parity.json", res)
    say(f"C4 {'PASS' if res['c4_pass'] else 'FAIL'}, C5 {'PASS' if res['c5_pass'] else 'FAIL'} over {trees}")
    return 0 if res["c4_pass"] and res["c5_pass"] else 1


# ---------------------------------------------------------------------------------------------------- C6
_GO_FUNC = re.compile(r"^func (?:\((?P<recv>[^)]*)\) )?(?P<name>[A-Z]\w*)(?:\[[^\]\n]*\])?\(", re.M)
_CS_METHOD = re.compile(r"^[ \t]*public[ \t]+(?:(?:static|async|virtual|override|sealed|new|abstract|partial|"
                        r"extern|unsafe)[ \t]+)*(?!class\b|interface\b|record\b|struct\b|enum\b|delegate\b|event\b)"
                        r"[\w<>\[\],.?]+(?:<[^\n>]*>)?[ \t]+(?P<name>[A-Z]\w*)[ \t]*(?:<[^\n>]*>)?[ \t]*\(", re.M)
GENERATED_SUFFIXES = ("_generated.go", ".pb.go", ".designer.cs", ".g.cs", ".g.i.cs", ".generated.cs")
GO_SKIP_PARTS = {"vendor", "mocks", "mock", "testdata", "testlib", "storetest", "plugintest", "apitestlib"}
CS_SKIP_PARTS = {"bin", "obj", "migrations"}


def generated(name: str, head: str) -> bool:
    """A generated source file: by its name, or a header in its first 4 KB that says so (Go's `Code generated ...` and
    `DO NOT EDIT`, on one line or two; C#'s `<auto-generated`)."""
    return (name.lower().endswith(GENERATED_SUFFIXES) or ("Code generated" in head and "DO NOT EDIT" in head)
            or "<auto-generated" in head)


def _body_lines(text: str, start: int) -> int:
    """Lines from `start` to the brace that closes the first `{` after it (rough: braces in strings count), 0 when
    the definition has no block body."""
    i = text.find("{", start)
    nl = text.find("\n", start)
    semi = text.find(";", start)
    arrow = text.find("=>", start)
    if i < 0 or (0 <= semi < i) or (0 <= arrow < i):
        return 0
    depth, j = 0, i
    while j < len(text):
        c = text[j]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text.count("\n", start, j) + 1
        j += 1
    return 0 if nl < 0 else text.count("\n", start) + 1


def product_vendor_rule(tree):
    """PREREG A6: the product's default vendor rule for the project root `tree`, a predicate on a root-relative path
    (sync._is_vendor_path with the root's .gitignore directories, sync._gitignore_dir_parts): the files DeepMap drops
    from the graph in both C6 arms, so never a C6 target."""
    if str(BUILD) not in sys.path:
        sys.path.insert(0, str(BUILD))
    import sync  # noqa: WPS433  (the product; loaded only by the commands that draw C6 targets)
    extra = sync._gitignore_dir_parts(Path(F._os_path(tree)))
    return lambda rel: bool(sync._is_vendor_path(rel, extra))


def c6_candidates(tree, lang: str, vendor=None) -> list:
    """Every exported Go function / method (lang go) or public C# method (lang csharp) of the non-test, non-generated,
    non-vendored source files under `tree`, with a block body of at least 5 lines: {file, name, line}, sorted.
    Non-vendored is the product's rule (`vendor`, default product_vendor_rule(tree); PREREG A6); a Go file is test
    code when its name or a directory name holds "test" (A6), a C# file when a directory name does."""
    out = []
    base = F._os_path(tree)
    suffix = ".go" if lang == "go" else ".cs"
    vendor = vendor or product_vendor_rule(tree)
    for d, dirs, files in os.walk(base):
        dirs[:] = sorted(x for x in dirs if x != ".git")
        for f in sorted(files):
            if not f.endswith(suffix):
                continue
            full = os.path.join(d, f)
            rel = os.path.relpath(full, base).replace("\\", "/")
            parts = [p.lower() for p in rel.split("/")]
            if vendor(rel):
                continue
            if lang == "go":
                if any("test" in p for p in parts) or set(parts[:-1]) & GO_SKIP_PARTS:
                    continue
            elif set(parts[:-1]) & CS_SKIP_PARTS or any("test" in p for p in parts[:-1]):
                continue
            with open(full, "rb") as fh:
                text = fh.read().decode("utf-8", "replace")
            if generated(f, text[:4096]):
                continue
            rx = _GO_FUNC if lang == "go" else _CS_METHOD
            for m in rx.finditer(text):
                if _body_lines(text, m.end()) >= 5:
                    out.append({"file": rel, "name": m.group("name"), "line": text.count("\n", 0, m.start()) + 1})
    return sorted(out, key=lambda c: (c["file"].encode("utf-8"), c["line"]))


def c6_draw(cands: list, seed: int, n: int) -> list:
    """C6's draw: the candidates in their fixed order, shuffled by random.Random(seed), the first n with a name not
    drawn before."""
    perm = list(cands)
    random.Random(int(seed)).shuffle(perm)
    out, names = [], set()
    for c in perm:
        if c["name"] in names:
            continue
        names.add(c["name"])
        out.append(c)
        if len(out) == n:
            break
    return out


def cmd_c6_draw(args) -> int:
    proto, reg, _prov = load(args)
    spec = proto["c6"]["sources"][args.source]
    drawn = c6_draw(c6_candidates(args.tree, spec["lang"]), spec["draw_seed"], proto["c6"]["queries"])
    if args.check:
        want = [{k: t[k] for k in ("file", "name", "line")} for t in reg["c6"][args.source]]
        same = drawn == want
        say(f"{args.source}: the draw {'equals' if same else 'DIFFERS from'} the registered targets")
        return 0 if same else 1
    print(json.dumps(drawn, indent=1))
    return 0


def norm_name(name) -> str:
    s = str(name or "").strip()
    if s.endswith("()"):
        s = s[:-2]
    s = s.split("::")[-1]
    return s.split(".")[-1].lstrip("*")


def target_hit(results: list, target: dict) -> bool:
    return any(str(r.get("source_file", "")).replace("\\", "/") == target["file"]
               and norm_name(r.get("name")) == target["name"] for r in results)


def score_c6(off: dict, on: dict, targets: list, proto: dict, ident=None) -> dict:
    """C6 for one repository from its two captures (4.8, A1, A6). VOID (never data): an arm or query mismatch, passes
    that differ, captures made at another identity than `ident`, a target with no node in the off graph, or fewer off
    hits than min_hits_off."""
    c6 = proto["c6"]
    probs = []
    caps = [list(identity_of(off)), list(identity_of(on))]
    for cap, arm in ((off, "off"), (on, "on")):
        if cap.get("arm") != arm:
            probs.append(f"the {arm} capture says arm {cap.get('arm')!r}")
        if not cap.get("deterministic"):
            probs.append(f"the {arm} capture's two passes differ")
        if [q["query"] for q in cap.get("queries") or []] != [t["query"] for t in targets]:
            probs.append(f"the {arm} capture's queries are not the registered ones, in order")
        if ident is not None and tuple(identity_of(cap)) != tuple(ident):
            probs.append(f"the {arm} capture was made at another identity than this score's (A1)")
    if not probs:
        missing = [t["id"] for t, q in zip(targets, off["queries"]) if not (q.get("target_nodes") or 0) > 0]
        if missing:
            probs.append(f"targets {missing} have no node in the off graph (A6): they can never be hit")
    if probs:
        return {"pass": False, "void": probs, "outcome": "void", "capture_identities": caps}
    k = c6["top_k"]
    per, lost, sql_counts, only_free_modules = [], [], [], True
    for t, qa, qb in zip(targets, off["queries"], on["queries"]):
        ra, rb = qa["results"][:k], qb["results"][:k]
        hit_a, hit_b = target_hit(ra, t), target_hit(rb, t)
        sql = [r for r in rb if is_sql_node(r)]
        sql_counts.append(len(sql))
        free = qb.get("sql_module_decls") or {}
        if any(not (r.get("label") == "Module" and free.get(r.get("key")) == 0) for r in sql):
            only_free_modules = False
        if hit_a and not hit_b:
            lost.append(t["id"])
        per.append({"id": t["id"], "hit_off": hit_a, "hit_on": hit_b, "sql_on": len(sql)})
    mean = sum(sql_counts) / len(sql_counts) if sql_counts else None
    ok = (len(targets) == c6["queries"] and mean is not None and mean <= c6["max_mean_sql"]
          and len(lost) <= c6["max_lost"])
    hit_off = sum(p["hit_off"] for p in per)
    void = None
    if hit_off < c6["min_hits_off"]:
        void = [f"the off arm hits {hit_off} of {len(per)} targets, fewer than {c6['min_hits_off']} (A6): the lost "
                f"count measures nothing"]
    outcome = "void" if void else ("pass" if ok else "fail")
    return {"pass": outcome == "pass", "void": void, "outcome": outcome, "capture_identities": caps,
            "mean_sql_top": mean, "lost": lost, "n_lost": len(lost), "per_query": per, "hit_off": hit_off,
            "hit_on": sum(p["hit_on"] for p in per),
            "fallback_applies": outcome == "fail" and only_free_modules and any(sql_counts),
            "thresholds": {"max_mean_sql": c6["max_mean_sql"], "max_lost": c6["max_lost"], "top_k": k,
                           "min_hits_off": c6["min_hits_off"]}}


def cmd_c6_score(args) -> int:
    proto, reg, prov = load(args)
    off, on = read_json(args.off), read_json(args.on)
    res = score_c6(off, on, reg["c6"][args.source], proto, identity_of(prov))
    if not res.get("void") and any((c.get("registration"), c.get("prereg")) != (prov["registration"], prov["prereg"])
                                   for c in (off, on)):
        res.update({"pass": False, "outcome": "void", "fallback_applies": False,
                    "void": ["a capture was made under another registration or other PREREG blocks"]})
    res.update({"schema": SCHEMA, "kind": "c6", "source": args.source, **prov,
                "builds": {"off": off.get("build"), "on": on.get("build")}})
    write_result(args.out, f"c6_{args.source}.json", res)
    shown = {k: res.get(k) for k in ("mean_sql_top", "n_lost", "void")}
    say(f"C6 {args.source}: {'PASS' if res['pass'] else 'FAIL'} {shown}")
    if res.get("void"):
        return 3
    return 0 if res["pass"] else 1


def search_row(d: dict) -> dict:
    """One retrieval.search() result (id, name, type, file, line, ...) in C6's form. A search result carries no key:
    a SQL file's :Module is keyed `sql:<its file>` (sql_reader's key), every other row keeps key None."""
    f, lab = d.get("file"), d.get("type")
    key = f"sql:{f}" if lab == "Module" and str(f or "").lower().endswith(".sql") else None
    return {"key": key, "label": lab, "name": d.get("name"), "source_file": f, "start_line": d.get("line")}


def build_problems(clone, environ=None) -> "tuple[list, dict]":
    """(why the clone's graph is not C6 data, what its build records) (PREREG 4.8, A6): DEEPMAP_EXTRACTOR=graphify
    must be set, the clone's .deepmap/graph_build.json must say graphify alone built the graph (`extractor` and
    `graph_engines`), and .deepmap/sync_last.json that its last sync pushed (outcome synced, rc 0)."""
    e = os.environ if environ is None else environ
    probs = []
    if (e.get("DEEPMAP_EXTRACTOR") or "").strip() != "graphify":
        probs.append("DEEPMAP_EXTRACTOR=graphify is not set (a fall-back to ast must be VOID, never silent)")
    state = Path(F._os_path(clone)) / ".deepmap"
    recs = {}
    for name in ("graph_build.json", "sync_last.json"):
        try:
            with open(state / name, encoding="utf-8") as fh:
                recs[name] = json.load(fh)
        except (OSError, ValueError):
            recs[name] = None
            probs.append(f"the clone has no readable .deepmap/{name}")
    gb, last = recs["graph_build.json"] or {}, recs["sync_last.json"] or {}
    if recs["graph_build.json"] is not None and (gb.get("extractor") != "graphify"
                                                 or gb.get("graph_engines") != ["graphify"]):
        probs.append(f"the graph was not built by graphify alone (extractor {gb.get('extractor')!r}, graph_engines "
                     f"{gb.get('graph_engines')!r})")
    if recs["sync_last.json"] is not None and (last.get("outcome") != "synced" or last.get("rc") != 0):
        probs.append(f"the last sync did not push (outcome {last.get('outcome')!r}, rc {last.get('rc')!r})")
    rec = {"extractor": gb.get("extractor"), "graph_engines": gb.get("graph_engines"),
           "built_at_utc": gb.get("built_at_utc"), "graphify_version": gb.get("graphify_version"),
           "nodes": gb.get("nodes"), "embedded": gb.get("embedded"), "sync_outcome": last.get("outcome"),
           "sync_rc": last.get("rc"), "sync_wall_s": last.get("wall_s")}
    return probs, rec


def cmd_c6_capture(args) -> int:  # pragma: no cover  (live: a provisioned throwaway instance)
    proto, reg, prov = load(args)
    if (os.environ.get("DEEPMAP_SQL_DECLARATIONS") or "").strip() != ("1" if args.arm == "on" else "0"):
        return refuse(f"arm {args.arm} needs DEEPMAP_SQL_DECLARATIONS={'1' if args.arm == 'on' else '0'} set for the "
                      f"build and the capture alike")
    bprobs, build = build_problems(Path(args.clone).resolve())
    if bprobs:
        return refuse("; ".join(bprobs))
    RV = _load_kit("run_variant")
    sys.path.insert(0, str(BUILD))
    import port_registry as PR  # noqa: WPS433
    from neo4j import GraphDatabase  # noqa: WPS433
    clone = Path(args.clone).resolve()
    route = PR.resolve_neo4j_route(clone, honor_env=False)
    probs = RV.safety_problems(clone, route, (7687, 7474, 7689, 7476))
    if probs:
        return refuse("; ".join(probs))
    targets = reg["c6"][args.source]
    drv = GraphDatabase.driver(route.uri, auth=("neo4j", os.environ.get("NEO4J_PASSWORD", "deepmap")),
                               notifications_min_severity="OFF")
    try:
        drv.verify_connectivity()
        cen = RV.census(drv)
        why = RV.owner_problem(clone, cen.get("owner"))
        if why:
            return refuse(why)
        with drv.session() as s:
            n_sql = s.run("MATCH (n) WHERE n.key STARTS WITH 'sql:' RETURN count(n) AS c").single()["c"]
            mods = [r["k"] for r in s.run("MATCH (m:Module) WHERE m.key STARTS WITH 'sql:' RETURN m.key AS k")]
            per = {r["m"]: r["c"] for r in s.run(
                "MATCH (d) WHERE d.key STARTS WITH 'sql:' AND d.key CONTAINS '#' "
                "RETURN split(d.key, '#')[0] AS m, count(*) AS c")}
            decls = {m: per.get(m, 0) for m in mods}     # a SQL :Module and its file's declaration count
            tnodes = {}                                  # A6: the target's nodes in this graph (file and name)
            for t in targets:
                names = [r["n"] for r in s.run("MATCH (n) WHERE n.source_file IN $fs RETURN n.name AS n",
                                               fs=[t["file"], t["file"].replace("/", "\\")])]
                tnodes[t["id"]] = sum(1 for x in names if norm_name(x) == t["name"])
        if (args.arm == "on") != (n_sql > 0):
            return refuse(f"arm {args.arm} but the graph holds {n_sql} SQL node(s)")
        import embeddings  # noqa: WPS433
        import retrieval  # noqa: WPS433
        embeddings.embed_query("warm-up")
        ret = retrieval.DeepMapRetriever(drv, embed_fn=embeddings.embed_query)
        ret.search("warm up the retriever", top_k=5)
        passes = []
        for _ in range(2):
            qs = []
            for t in targets:
                rs = [search_row(dict(r)) for r in ret.search(t["query"], top_k=proto["c6"]["top_k"])]
                qs.append({"id": t["id"], "query": t["query"], "results": rs, "target_nodes": tnodes[t["id"]],
                           "sql_module_decls": {r["key"]: decls[r["key"]] for r in rs if r["key"] in decls}})
            passes.append(qs)
    finally:
        drv.close()
    cap = {"schema": SCHEMA, "kind": "c6-capture", **prov, "source": args.source, "arm": args.arm, "build": build,
           "census": cen, "sql_nodes": n_sql, "queries": passes[0], "deterministic": passes[0] == passes[1]}
    write_json(Path(args.out) / f"capture_{args.source}_{args.arm}.json", cap)
    say(f"capture {args.source} {args.arm}: {len(targets)} queries, deterministic={cap['deterministic']}")
    return 0 if cap["deterministic"] else 3


# ---------------------------------------------------------------------------------------------------- decide
def decide(results: dict, proto: dict, reg: dict, current: dict, *, shas=None, ledger=None, registered_kit=None,
           allow_fixture=False) -> dict:
    """The decision (PREREG: ALL criteria pass -> the default may flip ON; anything else, the reader stays opt-in)
    from the result files: metrics.json (C1, C3), gate_<end>.json (C3), audit.json (C2), parity.json (C4, C5),
    c6_<source>.json (C6). Every file must name PREREG.md and the current hashes of its three locked sections; every
    identity the results rest on must be one (A1: each file's own, the dumps' parity lists, the captures' c6-score
    copied, C2's sheets or fresh sheet) and its kit the one section 1 registers; the pool C2 rests on must be the
    metrics pool; and the ledger (`ledger`: its lines; `shas`: each result file's sha256) must hold every result as
    its name's last line and no FAIL that is final (A2, A3)."""
    probs = []
    for name, r in sorted(results.items()):
        if r.get("registration") != "PREREG.md" and not allow_fixture:
            probs.append(f"{name}: registration {r.get('registration')!r} is not PREREG.md")
        if r.get("prereg") != current.get("prereg") and not allow_fixture:
            probs.append(f"{name}: measured against other PREREG blocks")
    idents = {name: tuple(identity_of(r)) for name, r in results.items()}
    ident = next(iter(idents.values())) if len(set(idents.values())) == 1 else None
    if ident is None or None in ident:
        probs.append(f"the results rest on more than one identity, or record none: {idents}")
    elif not allow_fixture and registered_kit is None:
        probs.append("the kit hash is not registered in PREREG.md section 1 (A1): no decision before it is")
    elif not allow_fixture and ident[2] != registered_kit:
        probs.append(f"the results were made by kit {ident[2]}, not the registered {registered_kit} (A1)")
    m = results.get("metrics.json") or {}
    au = results.get("audit.json") or {}
    pa = results.get("parity.json") or {}
    measured = [("parity.json's dumps", tuple(x)) for x in pa.get("dump_identities") or []]
    for s in sorted(proto["c6"]["sources"]):
        measured += [(f"c6_{s}.json's captures", tuple(x))
                     for x in (results.get(f"c6_{s}.json") or {}).get("capture_identities") or []]
    if au.get("fresh_identity") is not None:
        measured.append(("the fresh sheet", tuple(au["fresh_identity"])))
        pool_used = au.get("fresh_pool_sha256")
    else:
        pool_used = au.get("pool_sha256")
    if ident is not None:
        bad = sorted({what for what, x in measured if x != ident})
        bad += sorted({"C2's sheets" for x in au.get("sheet_identities") or []
                       if (x[0], x[2]) != (ident[0], ident[2]) and au.get("fresh_identity") is None})
        bad += sorted({"C2's sheets (kit)" for x in au.get("sheet_identities") or [] if x[2] != ident[2]})
        if bad:
            probs.append(f"measured at another identity than the results' (A1): {bad}")
    if au and pool_used != m.get("pool_sha256"):
        probs.append("C2 rests on another pool than the decision identity's metrics pool (A4)")
    probs += ledger_problems(list(ledger or []), results, shas or {}, current, ident, registered_kit, allow_fixture)
    gates = [results.get(f"gate_{e}.json") or {} for e in proto["engines"]]
    c6 = [results.get(f"c6_{s}.json") or {} for s in sorted(proto["c6"]["sources"])]
    crit = {
        "C1": bool(m.get("c1_pass")) and not m.get("c1_void"),
        "C2": bool(au.get("pass")) and au.get("outcome", "pass") == "pass",
        "C3": bool((m.get("c3") or {}).get("pass_time")) and not (m.get("c3") or {}).get("void")
        and all(g.get("pass") for g in gates),
        "C4": bool(pa.get("c4_pass")),
        "C5": bool(pa.get("c5_pass")),
        "C6": all(c.get("pass") for c in c6) and len(c6) == len(proto["c6"]["sources"]),
    }
    flip = not probs and all(crit.values())
    return {"criteria": crit, "problems": probs, "decision": "FLIP" if flip else "STAYS OPT-IN",
            "rule": "ALL criteria pass -> the default may flip ON; otherwise the reader stays opt-in"}


def cmd_decide(args) -> int:
    proto, reg, prov = load(args)
    files = [p for p in sorted(Path(args.results).glob("*.json"))
             if p.name.startswith(("metrics", "gate_", "audit", "parity", "c6_"))]
    results = {p.name: read_json(p) for p in files}
    shas = {p.name: file_sha(p) for p in files}
    kit = F.registered_kit_hash() if prov["registration"] == "PREREG.md" else None
    res = decide(results, proto, reg, prov, shas=shas, ledger=read_ledger(Path(args.results) / LEDGER),
                 registered_kit=kit)
    res.update({"schema": SCHEMA, "kind": "decision", **prov, "inputs": sorted(results), "registered_kit": kit})
    write_json(Path(args.out or args.results) / "decision.json", res)
    say(f"decision: {res['decision']} {res['criteria']}" + (f"; problems {res['problems']}" if res["problems"] else ""))
    return 0 if res["decision"] == "FLIP" else 1


def cmd_truth(args) -> int:
    _proto, reg, _prov = load(args)
    srcs, probs = verified_sources(args.corpus, reg, {t["source"] for t in (reg.get("truth") or {}).values()})
    if probs:
        return refuse(f"the corpus does not verify: {probs}")
    rc = 0
    for name, ts in truth_sets(args.corpus, reg).items():
        void = truth_void(ts)
        rc = max(rc, 3 if void else 0)
        say(f"{name}: {ts['counts']}" + (f" VOID: {void}" if void else " (the registered counts)"))
    return rc


# ---------------------------------------------------------------------------------------------------- main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, *opts):
        p = sub.add_parser(name)
        p.add_argument("--protocol")
        p.add_argument("--registered")
        for o in opts:
            p.add_argument(o)
        return p
    add("truth", "--corpus")
    p = add("metrics", "--corpus", "--out", "--passes")
    p = add("sample", "--pool", "--corpus", "--out", "--seed", "--n")
    p.add_argument("--exclude", nargs="*")
    add("prompt", "--sheet", "--out")
    p = add("audit", "--out", "--fresh", "--pool", "--fresh-pool", "--fixes")
    p.add_argument("--sheet", nargs="+", required=True)
    p.add_argument("--verdicts", nargs="+", required=True)
    p.add_argument("--fresh-verdicts", nargs="*")
    add("gate", "--python", "--out")
    p = add("dump", "--tree", "--id", "--out")
    p.add_argument("--sql", choices=("on", "off"), required=True)
    add("parity", "--dumps", "--out")
    p = add("c6-draw", "--tree", "--source")
    p.add_argument("--check", action="store_true")
    p = add("c6-capture", "--clone", "--source", "--out")
    p.add_argument("--arm", choices=("on", "off"), required=True)
    add("c6-score", "--off", "--on", "--source", "--out")
    add("decide", "--results", "--out")
    p = sub.add_parser("codehash")
    p.add_argument("--build")
    args = ap.parse_args(argv)
    if args.cmd == "codehash":
        print(json.dumps({"code": code_hash(args.build), "reader_gates": reader_gates_hash(), "kit": kit_hash()},
                         sort_keys=True))
        return 0
    why = _paths_problem(args)
    if why:
        return refuse(why)
    required = {"truth": ("corpus",), "metrics": ("corpus", "out"), "sample": ("pool", "corpus", "out", "seed"),
                "prompt": ("sheet", "out"), "audit": ("out", "pool"), "gate": ("python", "out"),
                "dump": ("tree", "id", "out"), "parity": ("dumps", "out"), "c6-draw": ("tree", "source"),
                "c6-capture": ("clone", "source", "out"), "c6-score": ("off", "on", "source", "out"),
                "decide": ("results",)}
    missing = [f"--{n.replace('_', '-')}" for n in required.get(args.cmd, ()) if not getattr(args, n, None)]
    if missing:
        return refuse(f"{args.cmd} needs {' '.join(missing)}")
    return {"truth": cmd_truth, "metrics": cmd_metrics, "sample": cmd_sample, "prompt": cmd_prompt,
            "audit": cmd_audit, "gate": cmd_gate, "dump": cmd_dump, "parity": cmd_parity, "c6-draw": cmd_c6_draw,
            "c6-capture": cmd_c6_capture, "c6-score": cmd_c6_score, "decide": cmd_decide}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
