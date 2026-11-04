#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""G-SQL pre-registration gate: benchmark/sql_reader/PREREG.md and its kit (fetch_corpus.py, run_corpus.py).

The SQL reader's default flips ON only when every criterion PREREG.md registers passes on its pinned corpus (design
.ai/design/2026-11/sql/design.md section 9). A protocol edited after data, a corpus that drifts or a kit that writes
into this repository would decide wrongly, so this gate locks:

  A  the kit: PREREG.md, fetch_corpus.py and run_corpus.py exist and parse; at module level both scripts import only
     the standard library and each other (the product is loaded inside the commands that measure it); nothing under
     build/ or hooks/ names benchmark/sql_reader or a kit module; no .sql file under the kit (a committed .sql would
     enter this repository's own graph, design supply minor 4)
  B  PREREG.md's "Protocol and criteria" block (the lines strictly between its one BEGIN and one END marker line,
     LF-joined plus a final LF: the call-linking rule) is the registered one verbatim (sha256 locked: thresholds are
     never edited after data); its JSON holds the design's thresholds (C1 0.99 / equal sets, C2 3 x 60 with >= 177
     TRUE and a fresh 60 / 60, C3 <= 1.0 ms/KB and <= 10 s per file over 3 passes, C6 top 15, 20 queries, mean SQL
     <= 2.0, <= 2 lost) and fresh seeds (none of the exploratory ones the disclosure names)
  C  its "Registered corpus and queries" block likewise (sha256 locked), its JSON well formed (six sources, 40-hex
     commits, 64-hex manifests, raw files with their sha256, the D3-cs corpora absent; the truth expectations; 20 C6
     targets per C6 source, each query plain words of at most 20 words without its target's own name) and the table
     above it agreeing with it; and the "Amendments before data" section (sha256 locked)
  K  the amendments (A1-A7): their one JSON block, laid over the two blocks, changes no design threshold; the judge
     is pinned and run_corpus.SHEET_RULE is the locked rule verbatim; dev_judged.json is the locked file (sha256, 195
     unique identities); the amended Mattermost targets follow C's query rules, lie outside the product's vendor
     directories and Go test code, under a fresh draw seed; section 1's kit-hash slot is unfilled or the kit's hash
  D  the scripts read PREREG.md: fetch_corpus.load_protocol / load_registered return the gate's own parse and
     load_amended the amended one, run_corpus.prereg_hashes the gate's three hashes; run_corpus.code_hash,
     kit_hash and reader_gates_hash are the call-linking kit's scheme
  E  refusals, offline: every command whose output, corpus or tree lies inside this repository exits 2 and creates
     nothing; fetch refuses before any network use (every proxy points at a dead port); dump refuses an unset, long,
     in-repository or operator DEEPMAP_HOME; fetch --register needs --registered
  F  fetch_corpus on a fake local corpus (a temp git repository, file:// fetches): the sql-set layout writes exactly
     the selected .sql blobs (bytes of the blob, a CRLF one too) and a git work tree; the clone layout checks out the
     commit; verify passes, a changed byte fails (exit 1, nothing deleted), fetch of a verified source is a no-op
  G  run_corpus scoring on synthetic inputs: the truth rule (column-0 declarations, quoted / bracketed / backticked
     names, modifiers; TEMPORARY, indented and comment lines excluded), C1 (set equality per kind over every kind the
     truth rule reads, SSDT recall at its boundary, a truth count that differs from PREREG is VOID), C2 (the draws
     recomputed from the pool, the 177 boundary, UNSURE counts as FALSE, the fresh sheet, one fix per non-TRUE item
     naming a section B case, verdicts bound to their sheet, prompt and judge), C3 (median of the passes, every file's
     time whatever its state, a file not read fails), C4 / C5 (parity, an SQL edge fails C5, a missing dump or one
     made at another identity is VOID), C6 (mean and lost boundaries, VOID captures: other identity, a target not in
     the off graph, too few off hits; the fallback attribution), decide (ALL pass on one identity with the registered
     kit and a clean ledger -> FLIP; any fail, a final FAIL earlier in the ledger, a fixture registration, two
     identities, an unregistered kit, a sheet judged twice -> STAYS OPT-IN), the C6 draw (deterministic, unique names,
     generated / test / vendored files left out), the capture's build checks
  H  run_corpus metrics end to end on ONE synthetic fixture corpus (a temp dir and a fixture registration): the
     product reader runs; metrics.json, pool.jsonl and the ledger are written outside the repository and say
     "fixture"; sample refuses an unregistered seed or size and a foreign exclusion; prompt writes the judge's prompt
  I  dump on a tiny temp tree through the real engine (SKIPPED without graphify): reader off is data; reader on is
     data when graphify_extract has the SQL step, else VOID (exit 3) naming why; with both data, C5 holds on it
  J  opt-in, GSQL_CORPUS=<the fetched corpus dir>: every source verifies and every C6 draw equals its registered
     targets as amended (else SKIPPED)

Hermetic except I (the real engine, when installed) and the opt-in J: temp dirs and a temp DEEPMAP_HOME, proxies at a
dead port, no Neo4j, no network, never ~/.deepmap. --kit DIR loads PREREG.md and the scripts from another directory
(the RED-first run and mutation tests).
Usage: python tests/sql_prereg_gates.py [--kit DIR]
"""
from __future__ import annotations

import ast
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build"
KIT_DEFAULT = REPO / "benchmark" / "sql_reader"
PASSES: list = []
FAILS: list = []
SKIPS: list = []

KIT_FILES = ("PREREG.md", "fetch_corpus.py", "run_corpus.py")
PROTOCOL_BEGIN = "<!-- G-SQL-PROTOCOL BEGIN"
PROTOCOL_END = "<!-- G-SQL-PROTOCOL END"
REGISTERED_BEGIN = "<!-- G-SQL-REGISTERED BEGIN"
REGISTERED_END = "<!-- G-SQL-REGISTERED END"
AMENDMENTS_HEADING = "## Amendments before data"
# sha256 of the three locked sections (each: the lines strictly between its markers, LF-joined plus one final LF).
# A change to any of them after the first decision run is never made here: it is a new registration. Before data, an
# amendment is dated and reasoned in "Amendments before data" and the hashes below change in the same edit.
PROTOCOL_SHA256 = "439cc94089b2a150c2f8b16ad2eb6ab7070a7a95b12b296d0ed48dabfa4eff73"
REGISTERED_SHA256 = "ff4448a2d80d67988590bd8d0e2823267f07d48d31c4a6646237535f3c2919d4"
# A1-A7 (2026-11-02, before any decision run): the review round's amendments.
AMENDMENTS_SHA256 = "53f01565572e9ca8017cf54ec947b88ed6e5cde7cc36be8cad0328db0b30d9f8"
JUDGE_MODEL = "claude-opus-5-5"                         # A3: the pinned C2 judge
DEV_JUDGED_ITEMS = 195                                  # A7: the development-judged identities left out of C2 draws
KIT_SLOT = re.compile(r"^- \*\*Kit hash:\*\* `([^`]*)`", re.M)
# The design's numbers (section 9), which the protocol JSON must hold.
DESIGN = {"c1": {"ssdt_min_recall": 0.99, "line_start": "equal"},
          "c2": {"n": 60, "min_true": 177, "fresh_n": 60, "fresh_min_true": 60},
          "c3": {"passes": 3, "max_ms_per_kb": 1.0, "max_file_seconds": 10.0},
          "c6": {"top_k": 15, "queries": 20, "max_mean_sql": 2.0, "max_lost": 2}}
ENGINES = ["0.8.39", "0.8.51"]
EXPLORATORY_SEEDS = {20261101, 7, 99, 2026, 20261102, 20261022}
D3CS = re.compile(r"jellyfin|eshop", re.I)
DEAD_PROXY = "http://127.0.0.1:9"


def rmtree(path) -> None:
    """Remove a temp tree this gate made, git's read-only object files included (Windows refuses to delete a read-only
    file, and shutil.rmtree(ignore_errors=True) then leaves the tree behind silently)."""
    def writable(func, p, _exc):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except OSError:
            pass
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=writable)
    elif os.path.exists(path):
        shutil.rmtree(path, onerror=writable)


def check(name: str, ok: bool, detail="") -> None:
    (PASSES if ok else FAILS).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok or detail == "" else f": {str(detail)[:900]}"))


def skip(name: str, why: str) -> None:
    SKIPS.append(name)
    print(f"  [SKIP] {name}: {why}")


def section(text: str, begin: str, end: str) -> "tuple[str | None, str | None]":
    lines = text.replace("\r\n", "\n").split("\n")
    b = [i for i, ln in enumerate(lines) if ln.startswith(begin)]
    e = [i for i, ln in enumerate(lines) if ln.startswith(end)]
    if len(b) != 1 or len(e) != 1 or b[0] >= e[0]:
        return None, f"want exactly one {begin!r} line before exactly one {end!r} line (at {b}, {e})"
    return "\n".join(lines[b[0] + 1:e[0]]) + "\n", None


def amendments(text: str) -> "tuple[str | None, str | None]":
    lines = text.replace("\r\n", "\n").split("\n")
    h = [i for i, ln in enumerate(lines) if ln == AMENDMENTS_HEADING]
    b = [i for i, ln in enumerate(lines) if ln.startswith(PROTOCOL_BEGIN)]
    if len(h) != 1 or len(b) != 1 or h[0] >= b[0]:
        return None, f"want one {AMENDMENTS_HEADING!r} heading line before the protocol BEGIN line (at {h}, {b})"
    return "\n".join(lines[h[0] + 1:b[0]]) + "\n", None


def json_of(sec: str):
    found = re.findall(r"^```json\n(.*?)^```$", sec or "", re.S | re.M)
    if len(found) != 1:
        return None
    try:
        return json.loads(found[0])
    except ValueError:
        return None


def sha(text) -> "str | None":
    return hashlib.sha256(text.encode("utf-8")).hexdigest() if text is not None else None


def attempt(name: str, fn) -> None:
    """check(name, fn()) where fn() returns (ok, detail); an exception is a FAIL with its traceback (a kit that lacks
    the function under test is RED, never a crash of the gate)."""
    try:
        ok, detail = fn()
    except Exception:  # noqa: BLE001
        ok, detail = False, traceback.format_exc()[-900:]
    check(name, ok, detail)


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    with contextlib.redirect_stdout(io.StringIO()):
        spec.loader.exec_module(mod)
    return mod


@contextlib.contextmanager
def env(**kv):
    saved = {k: os.environ.get(k) for k in kv}
    try:
        for k, v in kv.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def offline_env(**extra) -> dict:
    e = dict(os.environ, PYTHONIOENCODING="utf-8", DEEPMAP_AUTO_SYNC="0", HF_HUB_OFFLINE="1",
             HTTP_PROXY=DEAD_PROXY, HTTPS_PROXY=DEAD_PROXY, ALL_PROXY=DEAD_PROXY, http_proxy=DEAD_PROXY,
             https_proxy=DEAD_PROXY, all_proxy=DEAD_PROXY, NO_PROXY="", no_proxy="", GIT_TERMINAL_PROMPT="0")
    e.update(extra)
    return e


def run(script: Path, *args, extra_env=None, timeout=600) -> "tuple[int, str]":
    r = subprocess.run([sys.executable, str(script), *map(str, args)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=offline_env(**(extra_env or {})), timeout=timeout,
                       cwd=str(REPO))
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def git(cwd, *args) -> str:
    r = subprocess.run(["git", "-c", "core.autocrlf=false", *args], cwd=str(cwd), capture_output=True, text=True,
                       env=offline_env())
    if r.returncode:
        raise RuntimeError(f"git {args}: {r.stderr.strip()[:300]}")
    return r.stdout


# =====================================================================================================================
# A: the kit
# =====================================================================================================================
STDLIB = set(getattr(sys, "stdlib_module_names", ())) | {"__future__"}


def gate_a(kit: Path) -> None:
    print("\n-- A: the kit (files, module-level imports, isolation from the product, no committed .sql) --")
    for f in KIT_FILES:
        check(f"{f} exists in the kit", (kit / f).is_file())
    for f in ("fetch_corpus.py", "run_corpus.py"):
        p = kit / f
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except (OSError, SyntaxError) as exc:
            check(f"{f} parses", False, repr(exc))
            continue
        check(f"{f} parses", True)
        mods = []
        for node in tree.body:
            if isinstance(node, ast.Import):
                mods += [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                mods.append((node.module or "").split(".")[0])
        bad = [m for m in mods if m not in STDLIB and m not in ("fetch_corpus",)]
        check(f"{f} imports only the standard library and its sibling at module level", not bad, bad)
        text = p.read_text(encoding="utf-8")
        check(f"{f} holds no em / en dash and no line over 120 characters",
              "\u2014" not in text and "\u2013" not in text and max(map(len, text.split("\n"))) <= 120)
    hits = []
    for top in ("build", "hooks"):
        for p in sorted((REPO / top).rglob("*")):
            if not p.is_file() or "__pycache__" in p.parts or p.suffix not in (".py", ".json", ".md", ".txt", ""):
                continue
            try:
                t = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if re.search(r"(?m)^\s*(?:import|from)\s+(?:fetch_corpus|run_corpus)\b|sql_reader[/\\](?:fetch|run)_corpus",
                         t):
                hits.append(str(p.relative_to(REPO)))
    check("nothing under build/ or hooks/ imports or loads fetch_corpus / run_corpus (the product never depends on "
          "the measurement kit; naming its DECISION.md in a comment is fine)", not hits, hits)
    sqls = [str(p.relative_to(kit)) for p in kit.rglob("*") if p.is_file() and p.suffix.lower() == ".sql"]
    check("no .sql file in the kit (the corpus is fetched outside the repository, never committed)", not sqls, sqls)


# =====================================================================================================================
# B / C: PREREG.md
# =====================================================================================================================
def gate_b(kit: Path) -> "tuple[dict | None, dict | None, str]":
    print("\n-- B: PREREG.md's protocol block (sha256 locked) and its thresholds --")
    try:
        text = (kit / "PREREG.md").read_text(encoding="utf-8")
    except OSError as exc:
        check("PREREG.md is readable", False, repr(exc))
        return None, None, ""
    sec, why = section(text, PROTOCOL_BEGIN, PROTOCOL_END)
    crlf = text.replace("\r\n", "\n").replace("\n", "\r\n")
    check("the hash rule reads lines, not bytes: a CRLF checkout of PREREG.md (core.autocrlf) gives the same three "
          "hashes", sec is not None and sha(section(crlf, PROTOCOL_BEGIN, PROTOCOL_END)[0]) == sha(sec)
          and sha(section(crlf, REGISTERED_BEGIN, REGISTERED_END)[0])
          == sha(section(text, REGISTERED_BEGIN, REGISTERED_END)[0]) and sha(amendments(crlf)[0])
          == sha(amendments(text)[0]))
    got = sha(sec)
    check("the protocol block is the registered one verbatim (sha256 locked: thresholds never edited after data)",
          sec is not None and got == PROTOCOL_SHA256, why or got)
    proto = json_of(sec)
    check("the protocol block holds exactly one ```json block that parses", isinstance(proto, dict))
    if not isinstance(proto, dict):
        return None, None, text
    check("the protocol names the switch DEEPMAP_SQL_DECLARATIONS and the two engine ends 0.8.39 / 0.8.51",
          proto.get("switch") == "DEEPMAP_SQL_DECLARATIONS" and proto.get("engines") == ENGINES, proto.get("engines"))
    for c, want in DESIGN.items():
        have = proto.get(c) or {}
        diff = {k: (have.get(k), v) for k, v in want.items() if have.get(k) != v}
        check(f"{c.upper()}'s thresholds are the design's (section 9)", not diff, diff)
    c2 = proto.get("c2") or {}
    seeds = list(c2.get("seeds") or []) + [c2.get("fresh_seed")]
    check("C2 has three seeds and a fresh seed, all distinct whole numbers", len(c2.get("seeds") or []) == 3
          and len(set(seeds)) == 4 and all(type(s) is int for s in seeds), seeds)
    c6 = proto.get("c6") or {}
    draw = [v.get("draw_seed") for v in (c6.get("sources") or {}).values()]
    check("no decision or draw seed is one the exploratory runs used (fresh seeds)",
          not (set(seeds) | set(draw)) & EXPLORATORY_SEEDS, sorted((set(seeds) | set(draw)) & EXPLORATORY_SEEDS))
    check("C2's pool is features + Oracle + Mattermost + the EF repository (design section 9)",
          [p.get("source") for p in c2.get("pool") or []][:3] == ["sqlserver-samples", "oracle-samples", "mattermost"]
          and (c2.get("pool") or [{}])[0].get("prefix") == "samples/features/" and len(c2.get("pool") or []) == 4,
          c2.get("pool"))
    check("C6 runs on Mattermost and the EF repository, each with a language and a draw seed",
          len(c6.get("sources") or {}) == 2 and "mattermost" in (c6.get("sources") or {})
          and all(v.get("lang") in ("go", "csharp") and type(v.get("draw_seed")) is int
                  for v in (c6.get("sources") or {}).values()), c6.get("sources"))
    words = sec or ""
    for w in ("ALL", "stays opt-in", "DEEPMAP_SQL_DECLARATIONS=1", "VOID", "exploratory", "fallback",
              "never edited after data"):
        check(f"the protocol text states {w!r}", w in words)
    return proto, None, text


def gate_c(text: str, proto: "dict | None") -> "dict | None":
    print("\n-- C: the registered corpus and queries (sha256 locked), and the amendments --")
    sec, why = section(text, REGISTERED_BEGIN, REGISTERED_END)
    got = sha(sec)
    check("the registered block is the registered one verbatim (sha256 locked)",
          sec is not None and got == REGISTERED_SHA256, why or got)
    am, why_a = amendments(text)
    got_a = sha(am)
    check("the 'Amendments before data' section (heading to the protocol BEGIN line) is the registered one (sha256 "
          "locked)", am is not None and got_a == AMENDMENTS_SHA256, why_a or got_a)
    reg = json_of(sec)
    check("the registered block holds exactly one ```json block that parses", isinstance(reg, dict))
    if not isinstance(reg, dict):
        return None
    srcs = reg.get("sources") or []
    ids = [s.get("id") for s in srcs]
    check("six sources: the design's five plus the EF repository", len(srcs) == 6 and ids[:5] == [
        "sqlserver-samples", "oracle-samples", "mattermost", "gitlab-structure", "sakila"], ids)
    bad = []
    for s in srcs:
        if not re.fullmatch(r"[0-9a-f]{40}", str(s.get("commit"))):
            bad.append((s.get("id"), "commit"))
        if not re.fullmatch(r"[0-9a-f]{64}", str(s.get("sql_manifest_sha256"))):
            bad.append((s.get("id"), "manifest"))
        if not (type(s.get("sql_files")) is int and s["sql_files"] > 0 and type(s.get("sql_bytes")) is int):
            bad.append((s.get("id"), "counts"))
        if not str(s.get("url", "")).startswith("https://") or s.get("commit", "") not in str(s.get("url")) \
                and s.get("layout") == "raw":
            bad.append((s.get("id"), "url"))
        if s.get("layout") == "raw" and not re.fullmatch(r"[0-9a-f]{64}", str(s.get("sha256"))):
            bad.append((s.get("id"), "raw sha256"))
        if D3CS.search(str(s.get("url"))):
            bad.append((s.get("id"), "a D3-cs corpus"))
    check("every source pins a 40-hex commit (a raw file's URL names it), a 64-hex manifest, its counts; none is a "
          "D3-cs corpus", not bad, bad)
    pins = {"sqlserver-samples": "3c71696a3942879f0481f3bd65fe112a40023f18",
            "oracle-samples": "6660bad68c07bd143430ace58565b3f727e17263",
            "mattermost": "2b3fb21fe39e6d8178625aabd8b9176596ad4cf5"}
    check("the design's three git pins are the registered commits (design section 3.2)",
          all(any(s.get("id") == k and s.get("commit") == v for s in srcs) for k, v in pins.items()))
    table = sec.split("```json")[0]
    missing = [s.get("id") for s in srcs if f"`{s.get('commit')}`" not in table or f"`{s.get('id')}`" not in table]
    check("the table above the JSON names every source with its commit", not missing, missing)
    truth = reg.get("truth") or {}
    check("the truth expectations: WWI SSDT 231 files (54 / 26 / 135 / 12 / 4), Northwind 36, Sakila 32, GitLab "
          "2,222 (each kind counted)",
          sum((truth.get("wwi-ssdt") or {}).get("folders", {}).values()) == 231
          and sum((truth.get("northwind") or {}).get("kinds", {}).values()) == 36
          and sum((truth.get("sakila") or {}).get("kinds", {}).values()) == 32
          and sum((truth.get("gitlab") or {}).get("kinds", {}).values()) == 2222, truth.keys())
    c6 = reg.get("c6") or {}
    c6p = (proto or {}).get("c6") or {}
    check("C6 targets for exactly the protocol's C6 sources", set(c6) == set(c6p.get("sources") or {}),
          (sorted(c6), sorted(c6p.get("sources") or {})))
    probs = [p for src, targets in c6.items() for p in target_problems(src, targets)]
    check("every C6 target has id, file, name, line and one plain query of at most 20 words that never names the "
          "target itself (search by meaning, not by the identifier)", not probs, probs[:10])
    return reg


def target_problems(src: str, targets: list) -> list:
    """C's rules for one source's C6 targets: 20 of them, unique ids, complete, one plain query of at most 20 words that
    never names its target, no dash, no SQL file."""
    probs = []
    if len(targets) != DESIGN["c6"]["queries"]:
        probs.append(f"{src}: {len(targets)} targets")
    if len({t.get("id") for t in targets}) != len(targets):
        probs.append(f"{src}: ids not unique")
    for t in targets:
        q = str(t.get("query", ""))
        if not (t.get("file") and t.get("name") and type(t.get("line")) is int and q):
            probs.append(f"{src}/{t.get('id')}: incomplete")
        if t.get("name", "\0").lower() in q.lower():
            probs.append(f"{src}/{t.get('id')}: the query names its target {t.get('name')!r}")
        if len(q.split()) > 20 or "\u2014" in q or "\u2013" in q:
            probs.append(f"{src}/{t.get('id')}: over 20 words or a dash")
        if t.get("file", "").lower().endswith(".sql"):
            probs.append(f"{src}/{t.get('id')}: a SQL target (C6 queries are non-SQL)")
    return probs


# =====================================================================================================================
# K: the amendments (A1-A7)
# =====================================================================================================================
def gate_k(kit: Path, text: str, proto: "dict | None", reg: "dict | None") -> "dict | None":
    print("\n-- K: the amendments before data (A1-A7): their JSON, the judge, dev_judged.json, the redraw --")
    am_sec, _why = amendments(text)
    am = json_of(am_sec)
    check("the amendments section holds exactly one ```json block that parses (schema g-sql/amendments/1)",
          isinstance(am, dict) and am.get("schema") == "g-sql/amendments/1", type(am))
    if not isinstance(am, dict):
        return None
    for w in ("A1.", "A2.", "A3.", "A4.", "A5.", "A6.", "A7.", "FAIL", "final", "STAYS OPT-IN", "withdrawn",
              "ledger.jsonl", "public/", "71103", "dev_judged.json"):
        check(f"the amendments state {w!r}", w in am_sec)
    p, r = am.get("protocol") or {}, am.get("registered") or {}
    c2 = p.get("c2") or {}
    check("A3: the C2 judge is pinned (one model, one judging per sheet) and the sheet rule is locked here",
          (c2.get("judge") or {}).get("model") == JUDGE_MODEL and (c2.get("judge") or {}).get("judgings_per_sheet") == 1
          and isinstance(c2.get("sheet_rule"), str) and len(c2.get("sheet_rule")) > 200, c2.get("judge"))
    dev = c2.get("dev_judged") or {}
    try:
        data = (kit / str(dev.get("file"))).read_bytes().replace(b"\r\n", b"\n")   # LF in git, any checkout
        items = json.loads(data.decode("utf-8")).get("items") or []
    except (OSError, ValueError, AttributeError) as exc:
        data, items = b"", [repr(exc)]
    check("A7: dev_judged.json is the locked file (its sha256 in the amendment), 195 unique (source, path, line, kind) "
          "identities of the pool sources",
          hashlib.sha256(data).hexdigest() == dev.get("sha256") and len(items) == DEV_JUDGED_ITEMS == dev.get("items")
          and len({tuple(x) for x in items if isinstance(x, list)}) == len(items)
          and all(isinstance(x, list) and len(x) == 4 and x[0] in ("sqlserver-samples", "oracle-samples", "mattermost",
                                                                    "teams-samples") and type(x[2]) is int
                  for x in items), (len(items), dev))
    check("A4: a fix names a section B case of at least 12 characters", c2.get("fix_case_min_chars") == 12)
    check("A5: C3 counts only state 'read' as read", (p.get("c3") or {}).get("read_states") == ["read"])
    c6 = p.get("c6") or {}
    check("A6: an off-arm hit floor between 1 and 20 (5)", c6.get("min_hits_off") == 5)
    seeds = set()
    if isinstance(proto, dict):
        seeds = set((proto.get("c2") or {}).get("seeds") or []) | {(proto.get("c2") or {}).get("fresh_seed")} | {
            v.get("draw_seed") for v in ((proto.get("c6") or {}).get("sources") or {}).values()}
    new_seed = ((c6.get("sources") or {}).get("mattermost") or {}).get("draw_seed")
    check("A6: Mattermost's new draw seed is fresh (no registered, decision or exploratory seed)",
          type(new_seed) is int and new_seed not in seeds | EXPLORATORY_SEEDS, new_seed)
    mm = (r.get("c6") or {}).get("mattermost") or []
    probs = target_problems("mattermost (amended)", mm)
    old_ids = {t.get("id") for t in ((reg or {}).get("c6") or {}).get("mattermost") or []}
    if {t.get("id") for t in mm} & old_ids:
        probs.append("an amended target reuses a registered id")
    check("A6: the amended Mattermost targets follow C's rules, with ids of their own", not probs, probs[:10])
    if str(BUILD) not in sys.path:
        sys.path.insert(0, str(BUILD))
    try:
        import sync  # noqa: WPS433  (the product's vendor rule)
        vend = [t.get("id") for t in mm if sync._is_vendor_path(t.get("file"))]
        old_vend = [t.get("id") for t in ((reg or {}).get("c6") or {}).get("mattermost") or []
                    if sync._is_vendor_path(t.get("file"))]
    except Exception as exc:  # noqa: BLE001
        vend, old_vend = [repr(exc)], []
    testish = [t.get("id") for t in mm if any("test" in part.lower() for part in str(t.get("file")).split("/"))]
    check("A6: no amended target lies in a directory the product's vendor rule drops, or in Go test code (the seven "
          "registered public/ targets were)", not vend and not testish and len(old_vend) == 7,
          (vend, testish, old_vend))
    slots = KIT_SLOT.findall(text.split(AMENDMENTS_HEADING)[0])
    check("section 1 holds one kit-hash slot, `<to be filled>` or a 64-hex hash (A1)",
          len(slots) == 1 and (slots[0] == "<to be filled>" or re.fullmatch(r"[0-9a-f]{64}", slots[0]) is not None),
          slots)
    return am


# =====================================================================================================================
# D: the scripts read PREREG.md
# =====================================================================================================================
def merged(base, over):
    """`over` laid over `base` the way the amendments are (dicts merged key by key, anything else replaced)."""
    if isinstance(base, dict) and isinstance(over, dict):
        out = dict(base)
        for k, v in over.items():
            out[k] = merged(base.get(k), v)
        return out
    return json.loads(json.dumps(over))


def gate_d(kit: Path, proto, reg, text: str, am=None) -> "tuple[object, object]":
    print("\n-- D: the scripts read PREREG.md (one copy of every number) --")
    try:
        FC = load_module(kit / "fetch_corpus.py", "fetch_corpus")
        RC = load_module(kit / "run_corpus.py", "run_corpus")
    except Exception as exc:  # noqa: BLE001
        check("fetch_corpus.py and run_corpus.py load", False, repr(exc))
        return None, None
    check("fetch_corpus.py and run_corpus.py load", True)
    try:
        check("fetch_corpus.load_protocol() / load_registered() return the gate's parse of PREREG.md",
              FC.load_protocol(kit / "PREREG.md") == proto and FC.load_registered(kit / "PREREG.md") == reg)
    except Exception as exc:  # noqa: BLE001
        check("fetch_corpus.load_protocol() / load_registered() return the gate's parse of PREREG.md", False, repr(exc))
    want = {"protocol": sha(section(text, PROTOCOL_BEGIN, PROTOCOL_END)[0]),
            "registered": sha(section(text, REGISTERED_BEGIN, REGISTERED_END)[0]),
            "amendments": sha(amendments(text)[0])}
    attempt("run_corpus.prereg_hashes() records the gate's three hashes, the amendments' too (what decide compares)",
            lambda: (RC.prereg_hashes(kit / "PREREG.md") == want, (RC.prereg_hashes(kit / "PREREG.md"), want)))
    am = am or {}

    def amended():
        p, r, a = FC.load_amended(kit / "PREREG.md")
        wp, wr = merged(proto, am.get("protocol") or {}), merged(reg, am.get("registered") or {})
        keep = all(p.get(c, {}).get(k) == v for c, want_c in DESIGN.items() for k, v in want_c.items())
        return (p == wp and r == wr and a == am and keep and r["c6"]["teams-samples"] == reg["c6"]["teams-samples"]
                and r["c6"]["mattermost"] == (am.get("registered") or {}).get("c6", {}).get("mattermost")
                and p["c6"]["sources"]["mattermost"]["draw_seed"] == 71103, (p.get("c6"), keep))
    attempt("fetch_corpus.load_amended() lays the amendments over both blocks: the Mattermost targets and draw seed "
            "replaced, teams-samples and every design threshold unchanged", amended)
    attempt("run_corpus.SHEET_RULE is the rule the amendments lock, verbatim (A3)",
            lambda: (RC.SHEET_RULE == ((am.get("protocol") or {}).get("c2") or {}).get("sheet_rule"), RC.SHEET_RULE))
    tmp = Path(tempfile.mkdtemp(prefix="dm_sqlp_"))
    try:
        (tmp / "a.py").write_bytes(b"x = 1\n")
        (tmp / "b.py").write_bytes(b"y = 2\r\n")
        (tmp / "c.txt").write_bytes(b"not part of the kit hash\n")
        cl = load_module(REPO / "benchmark" / "call_linking" / "_common.py", "_gsql_cl_common")
        check("run_corpus.code_hash is the call-linking kit's build/*.py scheme",
              RC.code_hash(tmp) == cl.build_code_hash(tmp))
        attempt("run_corpus.kit_hash is the call-linking scheme over the kit's *.py files by name (A1)",
                lambda: (RC.kit_hash(tmp) == {"sha256": cl._path_hash(tmp, ["a.py", "b.py"]), "files": 2},
                         RC.kit_hash(tmp)))
        attempt("run_corpus.reader_gates_hash is the same scheme over tests/sql_reader_gates.py (A1)",
                lambda: (RC.reader_gates_hash()["sha256"] == cl._path_hash(REPO, ["tests/sql_reader_gates.py"]),
                         RC.reader_gates_hash()))
        attempt("run_corpus.identity_of reads (code, reader_gates, kit) from a record (A1)",
                lambda: (RC.identity_of({"code": {"sha256": "c"}, "reader_gates": {"sha256": "g"},
                                         "kit": {"sha256": "k"}}) == ("c", "g", "k"), None))
        slot = KIT_SLOT.findall(text.split(AMENDMENTS_HEADING)[0])
        attempt("fetch_corpus.registered_kit_hash() reads section 1's slot (None while `<to be filled>`), and a filled "
                "slot is the kit's own hash: the kit never changes after it is registered",
                lambda: ((FC.registered_kit_hash(kit / "PREREG.md") is None) == (slot == ["<to be filled>"])
                         and (slot == ["<to be filled>"] or slot == [RC.kit_hash(kit)["sha256"]]), slot))
    finally:
        rmtree(tmp)
    return FC, RC


# =====================================================================================================================
# E: refusals
# =====================================================================================================================
def gate_e(kit: Path) -> None:
    print("\n-- E: refusals (inside the repository, DEEPMAP_HOME), offline --")
    fc, rc = kit / "fetch_corpus.py", kit / "run_corpus.py"
    inside = REPO / "benchmark" / "sql_reader" / "_gsql_must_not_exist"
    tmp = Path(tempfile.mkdtemp(prefix="dm_sqlp_"))
    try:
        fake = tmp / "fake.json"                        # a broken refusal would have only this to fetch: offline
        fake.write_text(json.dumps({"sources": [{"id": "fake", "type": "git", "layout": "sql-set", "select": [],
                                                 "url": "https://example.invalid/fake.git", "commit": "0" * 40,
                                                 "sql_files": 1, "sql_bytes": 1, "sql_manifest_sha256": "0" * 64}]}),
                        encoding="utf-8")
        cases = [(fc, ["fetch", "--out", inside, "--registered", fake]),
                 (fc, ["verify", "--out", inside, "--registered", fake]),
                 (fc, ["select-ef", "--out", inside]), (rc, ["metrics", "--corpus", tmp, "--out", inside]),
                 (rc, ["metrics", "--corpus", inside, "--out", tmp]),
                 (rc, ["truth", "--corpus", inside]), (rc, ["parity", "--dumps", tmp, "--out", inside]),
                 (rc, ["c6-score", "--off", "x", "--on", "y", "--source", "mattermost", "--out", inside]),
                 (rc, ["dump", "--tree", inside, "--id", "t", "--sql", "off", "--out", tmp / "d.json"]),
                 (rc, ["decide", "--results", inside]), (rc, ["sample", "--pool", "p", "--corpus", tmp, "--out", inside,
                                                              "--seed", "1"]),
                 (rc, ["prompt", "--sheet", "s", "--out", inside / "p.txt"]),
                 (rc, ["audit", "--sheet", "s", "--verdicts", "v", "--pool", "p", "--out", inside])]
        for script, args in cases:
            code, out = run(script, *args, timeout=120)
            made = inside.exists()
            if made:                                    # a broken refusal wrote there: this gate's own directory,
                rmtree(inside)   # removed by its exact path so later cases stay meaningful
            check(f"{script.name} {args[0]} with a path inside the repository exits 2 and creates nothing",
                  code == 2 and "REFUSED" in out and not made, (code, made, out[-300:]))
        code, out = run(fc, "fetch", "--out", tmp / "c", "--register", timeout=60)
        check("fetch --register without --registered is refused (PREREG.md's registration is never rewritten)",
              code == 2 and not (tmp / "c").exists(), (code, out[-300:]))
        tree = tmp / "tree"
        tree.mkdir()
        for label, home in (("unset", None), ("long", "C:/" + "x" * 80 if os.name == "nt" else "/" + "x" * 80),
                            ("the operator's", str(Path.home() / ".deepmap")),
                            ("in-repository", str(REPO / "benchmark" / "x"))):
            e = {k: v for k, v in os.environ.items() if k != "DEEPMAP_HOME"}
            if home is not None:
                e["DEEPMAP_HOME"] = home
            r = subprocess.run([sys.executable, str(rc), "dump", "--tree", str(tree), "--id", "t", "--sql", "off",
                                "--out", str(tmp / "d.json")], capture_output=True, text=True, encoding="utf-8",
                               errors="replace", env=dict(offline_env(), **e) if home else
                               {k: v for k, v in offline_env().items() if k != "DEEPMAP_HOME"}, timeout=120)
            check(f"dump refuses an {label} DEEPMAP_HOME (exit 2, no dump)",
                  r.returncode == 2 and not (tmp / "d.json").exists(), (r.returncode, r.stdout[-300:]))
    finally:
        rmtree(tmp)


# =====================================================================================================================
# F: fetch_corpus on a fake local corpus
# =====================================================================================================================
def gate_f(FC, kit: Path) -> None:
    print("\n-- F: fetch_corpus on a fake local corpus (file:// fetches, the three layouts) --")
    tmp = Path(tempfile.mkdtemp(prefix="dm_sqlp_"))
    try:
        up = tmp / "up"
        (up / "db" / "x").mkdir(parents=True)
        (up / "docs").mkdir()
        files = {"db/a.sql": b"CREATE TABLE a (id int);\n", "db/x/B.SQL": b"CREATE VIEW b AS SELECT 1;\r\nGO\r\n",
                 "db/x/notes.txt": b"no\n", "docs/c.sql": b"CREATE TABLE c (id int);\n", "app.cs": b"class A {}\n"}
        for rel, data in files.items():
            (up / rel).write_bytes(data)
        (up / ".gitattributes").write_bytes(b"*.sql text eol=crlf\n")    # a checkout would rewrite the bytes
        git(up, "init", "-q")
        git(up, "config", "user.email", "g@example.invalid")
        git(up, "config", "user.name", "gate")
        git(up, "config", "uploadpack.allowFilter", "true")
        git(up, "config", "uploadpack.allowAnySHA1InWant", "true")
        git(up, "add", "-A")
        git(up, "commit", "-q", "-m", "fixture")
        commit = git(up, "rev-parse", "HEAD").strip()
        blobs = {rel: subprocess.run(["git", "cat-file", "blob", f"HEAD:{rel}"], cwd=str(up), capture_output=True,
                                     env=offline_env()).stdout for rel in files}
        url = "file:///" + str(up).replace("\\", "/").lstrip("/")
        out = tmp / "out"
        want = FC.manifest_of([(r, blobs[r]) for r in ("db/a.sql", "db/x/B.SQL")])
        src = {"id": "fake-set", "type": "git", "layout": "sql-set", "url": url, "commit": commit, "select": ["db"],
               **want}
        with env(HTTP_PROXY=DEAD_PROXY, HTTPS_PROXY=DEAD_PROXY, ALL_PROXY=DEAD_PROXY):
            FC.fetch_sql_set(out, src)
        tree = out / "fake-set" / "tree"
        got = sorted(p.relative_to(tree).as_posix() for p in tree.rglob("*") if p.is_file() and ".git" not in p.parts)
        check("sql-set writes exactly the selected .sql files (any case), nothing else", got == ["db/a.sql",
                                                                                                "db/x/B.SQL"], got)
        check("sql-set writes the blob's bytes (never a checkout's: the .gitattributes eol=crlf is not applied)",
              all((tree / r).read_bytes() == blobs[r] for r in got))
        check("sql-set makes the tree a git work tree with no commit (the corpus as a git tree, PREREG C4)",
              (tree / ".git").is_dir() and subprocess.run(["git", "rev-parse", "--verify", "HEAD"], cwd=str(tree),
                                                          capture_output=True).returncode != 0)
        res = FC.verify_source(out, src)
        check("verify passes on the fetched sql-set", res["ok"], res["problems"])
        reg = tmp / "reg.json"
        reg.write_text(json.dumps({"sources": [dict(src, url="https://example.invalid/fake.git")]}), encoding="utf-8")
        code, msg = run(kit / "fetch_corpus.py", "fetch", "--out", out, "--registered", reg, timeout=120)
        check("fetch of a present, verified source is a no-op (exit 0, no network)", code == 0
              and "present and verified" in msg, (code, msg[-300:]))
        (tree / "db" / "a.sql").write_bytes(b"CREATE TABLE a (id bigint);\n")
        code, msg = run(kit / "fetch_corpus.py", "verify", "--out", out, "--registered", reg, timeout=120)
        check("verify fails (exit 1) on a changed byte", code == 1 and "FAILED" in msg, (code, msg[-300:]))
        code, msg = run(kit / "fetch_corpus.py", "fetch", "--out", out, "--registered", reg, timeout=120)
        check("fetch of a present source that fails verification refuses (exit 1) and deletes nothing",
              code == 1 and (tree / "db" / "a.sql").is_file() and "by hand" in msg, (code, msg[-300:]))
        csrc = {"id": "fake-clone", "type": "git", "layout": "clone", "url": url, "commit": commit, "root": "db",
                **FC.manifest_of([("a.sql", blobs["db/a.sql"]), ("x/B.SQL", blobs["db/x/B.SQL"])])}
        FC.fetch_clone(out, csrc)
        res = FC.verify_source(out, csrc)
        check("clone checks out the pinned commit and its SQL set (the .sql files under its root) verifies, the eol "
              "attribute's rewrite undone from the blobs", res["ok"], res["problems"])
        raw_dir = out / "fake-raw" / "tree" / "db"
        raw_dir.mkdir(parents=True)
        (raw_dir / "s.sql").write_bytes(blobs["db/a.sql"])
        rsrc = {"id": "fake-raw", "type": "raw", "layout": "raw", "url": f"https://example.invalid/{commit}/db/s.sql",
                "commit": commit, "path": "db/s.sql", "sha256": hashlib.sha256(blobs["db/a.sql"]).hexdigest(),
                "bytes": len(blobs["db/a.sql"]), **FC.manifest_of([("db/s.sql", blobs["db/a.sql"])])}
        check("a raw source verifies by its file's sha256 and manifest", FC.verify_source(out, rsrc)["ok"])
        lines = FC.manifest_lines([("b/x.sql", b"1"), ("a/y.sql", b"2")])
        check("the manifest is `<sha256>  <path>` lines in the byte order of the paths",
              [ln.split("  ")[1] for ln in lines] == ["a/y.sql\n", "b/x.sql\n"]
              and lines[0].split("  ")[0] == hashlib.sha256(b"2").hexdigest())
    except Exception:  # noqa: BLE001
        check("section F ran", False, traceback.format_exc()[-1500:])
    finally:
        rmtree(tmp)
    check("section F leaves no temp tree behind (its git repositories' read-only objects included)", not tmp.exists())


# =====================================================================================================================
# G: scoring on synthetic inputs
# =====================================================================================================================
PROTO_FIX = {"schema": "g-sql/protocol/1", "switch": "DEEPMAP_SQL_DECLARATIONS", "engines": ENGINES,
             "c1": {"ssdt_min_recall": 0.99, "line_start": "equal"},
             "c2": {"pool": [{"source": "s"}], "seeds": [11, 12, 13], "n": 60, "min_true": 177, "fresh_seed": 14,
                    "fresh_n": 60, "fresh_min_true": 60, "context_before": 2, "context_after": 3,
                    "judge": {"model": JUDGE_MODEL, "judgings_per_sheet": 1}, "fix_case_min_chars": 12},
             "c3": {"passes": 3, "max_ms_per_kb": 1.0, "max_file_seconds": 10.0, "statistic": "median",
                    "quiet_max_load": 30, "read_states": ["read"]},
             "c6": {"sources": {"go-repo": {"lang": "go", "draw_seed": 5}, "cs-repo": {"lang": "csharp",
                                                                                    "draw_seed": 6}},
                    "top_k": 15, "queries": 20, "max_mean_sql": 2.0, "max_lost": 2, "min_hits_off": 5}}
IDENT = {"code": {"sha256": "c"}, "reader_gates": {"sha256": "g"}, "kit": {"sha256": "k"}}
B_TEXT = 'check("T-SQL negatives: a GRANT CREATE VIEW TO hr privilege list is no view", got == want)\n'


def jsha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode("utf-8")).hexdigest()


def gate_g(RC) -> None:
    print("\n-- G: run_corpus scoring on synthetic inputs --")
    text = ("CREATE TABLE \"Order Details\" (\n  x int);\ncreate view [dbo].[V 1] AS SELECT 1\nCREATE PROC p AS\n"
            "  CREATE TABLE inner_t (x int);\nCREATE TEMPORARY TABLE tmp (x int);\n-- CREATE TABLE commented (x int);\n"
            "CREATE OR REPLACE FUNCTION public.f() RETURNS int\nCREATE DEFINER=CURRENT_USER SQL SECURITY INVOKER VIEW "
            "`actor_info` AS\nCREATE MATERIALIZED VIEW mv AS SELECT 1;\nCREATE UNLOGGED TABLE u (x int);\n"
            "CREATE TRIGGER t1 AFTER INSERT ON a\nCREATE INDEX i ON a (x);\n")
    got = RC.line_start_truth(text)
    want = [("table", "order details", 1), ("view", "dbo.v 1", 3), ("procedure", "p", 4),
            ("function", "public.f", 8), ("view", "actor_info", 9), ("materialized view", "mv", 10),
            ("table", "u", 11), ("trigger", "t1", 12)]
    check("the truth rule: column-0 CREATE of a table / view / procedure / function / trigger, names unquoted and "
          "lower-cased, modifiers read; TEMPORARY, indented, comment and INDEX lines left out", got == want, got)
    attempt("C2 draws: deterministic for a seed, 60 items, a later sample excludes earlier ones, and the development-"
            "judged identities (A7) never enter a draw", lambda: g_draws(RC))
    for part in (gate_g_c1, gate_g_c2, gate_g_c3, gate_g_parity, gate_g_c6, gate_g_decide, gate_g_candidates):
        try:
            part(RC)
        except Exception:  # noqa: BLE001  (a kit that lacks what a part calls is RED, never a crash of the gate)
            check(f"section G's {part.__name__} ran", False, traceback.format_exc()[-1200:])
    attempt("c6-capture's build checks (A6): DEEPMAP_EXTRACTOR=graphify, a graph built by graphify alone and a sync "
            "that pushed; anything else is refused", lambda: g_build(RC))


def gate_g_c1(RC) -> None:
    ts = {"rule": "line-start", "source": "s", "file": "f.sql", "counts": {"table": 2, "view": 1},
          "items": [("table", "a"), ("table", "b"), ("view", "v")], "expected": {"table": 2, "view": 1},
          "duplicates": 0}

    def d(kind, q, name=None):
        return (kind, q, name or q, 1, f"sql:f.sql#{kind}:{q}")
    ok = RC.score_c1(ts, {"f.sql": [d("table", "a"), d("table", "b"), d("view", "v"), d("type", "zz")]}, PROTO_FIX)
    miss = RC.score_c1(ts, {"f.sql": [d("table", "a"), d("view", "v")]}, PROTO_FIX)
    extra = RC.score_c1(ts, {"f.sql": [d("table", "a"), d("table", "b"), d("table", "c"), d("view", "v")]}, PROTO_FIX)
    void = RC.score_c1(dict(ts, counts={"table": 3, "view": 1}), {"f.sql": []}, PROTO_FIX)
    check("C1 line-start: the per-kind sets must be equal (a miss fails, an extra of a truth kind fails, a kind the "
          "truth rule does not read is not judged); truth counts other than PREREG's make it VOID",
          ok["pass"] and not miss["pass"] and not extra["pass"] and void["void"] and not void["pass"],
          (ok["pass"], miss["pass"], extra["pass"], void["void"]))
    other = [RC.score_c1(ts, {"f.sql": [d("table", "a"), d("table", "b"), d("view", "v"), d(k, "bogus")]}, PROTO_FIX)
             for k in ("procedure", "trigger", "function", "materialized view")]
    check("C1 line-start (4.3: 'no other object of that kind'): a procedure, trigger, function or materialized view "
          "the set registers none of is an extra declaration and fails", not any(o["pass"] for o in other),
          [o["pass"] for o in other])
    files = [(f"S/Tables/T{i}.sql", "Tables", f"T{i}") for i in range(100)]
    ss = {"rule": "ssdt", "source": "s", "files": files, "counts": {"Tables": 100}, "expected": {"Tables": 100}}
    found = {r: [d("table", f"s.{st.lower()}", st)] for r, _f, st in files}
    one_miss = dict(found)
    one_miss.pop(files[0][0])
    two_miss = dict(one_miss)
    two_miss.pop(files[1][0])
    wrong_kind = dict(found, **{files[0][0]: [d("trigger", "s.t0", "T0")]})
    check("C1 SSDT: an object named after the file stem, of a declaring kind; 99 of 100 passes 0.99, 98 fails, a "
          "trigger named like the file is no hit",
          RC.score_c1(ss, found, PROTO_FIX)["pass"] and RC.score_c1(ss, one_miss, PROTO_FIX)["pass"]
          and not RC.score_c1(ss, two_miss, PROTO_FIX)["pass"]
          and RC.score_c1(ss, wrong_kind, PROTO_FIX)["found"] == 99)


def g_pool(n=400) -> list:
    return [{"source": "s", "rel": f"f{i:03d}.sql", "line": 1, "kind": "table", "qualified": f"t{i}", "name": f"t{i}",
             "key": f"sql:f{i:03d}.sql#table:t{i}"} for i in range(n)]


def g_draws(RC):
    pool = g_pool()
    s1 = RC.draw(pool, 11, 60)
    s2 = RC.draw(pool, 12, 60, [RC.item_id(x) for x in s1])
    dev = {("s", pool[i]["rel"], 1, "table") for i in range(0, 400, 2)}
    kept = RC.decision_pool(pool, dev)
    real = RC.dev_judged(RC.F.load_amended()[0])
    return (s1 == RC.draw(pool, 11, 60) and len(s1) == 60
            and not {RC.item_id(x) for x in s1} & {RC.item_id(x) for x in s2}
            and len(kept) == 200 and all(int(x["rel"][1:4]) % 2 for x in kept)
            and len(real) == DEV_JUDGED_ITEMS and RC.decision_pool(pool, set()) == pool, (len(kept), len(real)))


def mk_sheet(RC, pool, seed, n, seen, code="c", kit="k", dev=frozenset()):
    items = RC.draw(RC.decision_pool(pool, set(dev)), seed, n, seen)
    sh = {"kind": "sheet", "seed": seed, "n": n, "items": [dict(it, context=[f"{it['line']}: CREATE TABLE x"])
                                                            for it in items],
          "pool_sha256": RC.pool_sha(pool), "code": {"sha256": code}, "reader_gates": {"sha256": "g"},
          "kit": {"sha256": kit}}
    sh["_file_sha256"] = jsha(sh)
    return sh


def mk_verdicts(RC, sheet, bad=(), unsure=(), model=JUDGE_MODEL, sheet_sha=None, prompt=None):
    v = [{"i": i, "verdict": "FALSE" if i in bad else "UNSURE" if i in unsure else "TRUE", "why": "w"}
         for i in range(len(sheet["items"]))]
    vd = {"sheet_sha256": sheet_sha or sheet["_file_sha256"], "judge": {"model": model, "settings": "default"},
          "prompt_sha256": prompt or hashlib.sha256(RC.judge_prompt(sheet).encode("utf-8")).hexdigest(),
          "verdicts": v}
    vd["_file_sha256"] = jsha(vd)
    return vd


def gate_g_c2(RC) -> None:
    try:
        pool = g_pool()
        sheets, seen = [], []
        for s in (11, 12, 13):
            sheets.append(mk_sheet(RC, pool, s, 60, seen))
            seen += [RC.item_id(x) for x in sheets[-1]["items"]]
        fresh = mk_sheet(RC, pool, 14, 60, seen)
        ok_v = [mk_verdicts(RC, sh) for sh in sheets]
        bad3 = [mk_verdicts(RC, sheets[0], bad=(0, 1)), mk_verdicts(RC, sheets[1], bad=(5,)), ok_v[2]]
        fixes = [{"seed": 11, "i": 0, "case": "GRANT CREATE VIEW TO hr"},
                 {"seed": 11, "i": 1, "case": "T-SQL negatives"},
                 {"seed": 12, "i": 5, "case": "privilege list is no view"}]
        fv_ok, fv_bad = mk_verdicts(RC, fresh), mk_verdicts(RC, fresh, bad=(7,))
        dec = ("c", "g", "k")

        def c2(sh, vd, fr=None, frv=None, fx=None, decision=dec, b=B_TEXT, pl=None, fpl=None):
            return RC.score_c2(sh, vd, fr, frv, PROTO_FIX, decision, pool=pool if pl is None else pl,
                               fresh_pool=(pool if fpl is None else fpl) if fr is not None else None,
                               fixes=fx or [], b_text=b)
        r = {"all": c2(sheets, ok_v), "fixed": c2(sheets, bad3, fresh, [fv_ok], fixes),
             "no_fresh": c2(sheets, bad3, fx=fixes), "fresh_false": c2(sheets, bad3, fresh, [fv_bad], fixes),
             "no_fixes": c2(sheets, bad3, fresh, [fv_ok]),
             "fix_not_in_b": c2(sheets, bad3, fresh, [fv_ok], fixes[:2] + [{"seed": 12, "i": 5, "case": "no such case "
                                                                          "anywhere in section B"}]),
             "fix_short": c2(sheets, bad3, fresh, [fv_ok], fixes[:2] + [{"seed": 12, "i": 5, "case": "view"}]),
             "unsure": c2(sheets, [mk_verdicts(RC, sheets[0], bad=(0, 1)), mk_verdicts(RC, sheets[1], bad=(5,)),
                                   mk_verdicts(RC, sheets[2], unsure=(9,))], fresh, [fv_ok],
                          fixes + [{"seed": 13, "i": 9, "case": "T-SQL negatives"}])}
        want = {"all": "pass", "fixed": "pass", "no_fresh": "pending", "fresh_false": "fail", "no_fixes": "pending",
                "fix_not_in_b": "pending", "fix_short": "pending", "unsure": "fail"}
        have = {k: v.get("outcome") for k, v in r.items()}
        check("C2: 180 / 180 passes; 177 with one fix per non-TRUE item (a section B case of 12+ characters the gate "
              "file holds) and a fresh 60 / 60 passes; without the fresh sheet or a fix it is pending; a FALSE in the "
              "fresh sheet fails; an UNSURE counts as FALSE (176 fails)",
              have == want and r["all"]["pass"] and r["fixed"]["pass"]
              and not any(r[k]["pass"] for k in want if want[k] != "pass"), (have, want))
        hand = [dict(sh, items=pool[k * 60:(k + 1) * 60]) for k, sh in enumerate(sheets)]
        hand = [dict(sh, _file_sha256=jsha(sh)) for sh in hand]
        junk = list(seen[:5])
        redrawn = mk_sheet(RC, pool, 11, 60, junk)
        void = {"hand_picked": c2(hand, [mk_verdicts(RC, sh) for sh in hand]),
                "foreign_exclusion": c2([redrawn] + sheets[1:], [mk_verdicts(RC, redrawn)] + ok_v[1:]),
                "other_pool": c2(sheets, ok_v, pl=g_pool(401)),
                "other_sheet": c2(sheets, [mk_verdicts(RC, sheets[0], sheet_sha="0" * 64)] + ok_v[1:]),
                "other_judge": c2(sheets, [mk_verdicts(RC, sheets[0], model="another-model")] + ok_v[1:]),
                "other_prompt": c2(sheets, [mk_verdicts(RC, sheets[0], prompt="1" * 64)] + ok_v[1:]),
                "judged_twice": c2(sheets, ok_v + [mk_verdicts(RC, sheets[0], bad=(3,))]),
                "seed_order": c2([sheets[1], sheets[0], sheets[2]], [ok_v[1], ok_v[0], ok_v[2]])}
        check("C2 is VOID (never data) for hand-picked sheets, a sheet drawn with a foreign exclusion, another pool, "
              "verdicts for another sheet, another judge or another prompt, a sheet judged twice, sheets out of the "
              "registered seed order (A3, A4)",
              all(v.get("outcome") == "void" and not v.get("pass") and v.get("problems") for v in void.values()),
              {k: (v.get("outcome"), (v.get("problems") or [""])[0][:80]) for k, v in void.items()})
        old = []
        for s in (11, 12, 13):
            old.append(mk_sheet(RC, pool, s, 60, [x for sh in old for x in map(RC.item_id, sh["items"])], code="old"))
        old_v = [mk_verdicts(RC, sh) for sh in old]
        oseen = [RC.item_id(x) for sh in old for x in sh["items"]]
        fresh_new, fresh_old = mk_sheet(RC, pool, 14, 60, oseen), mk_sheet(RC, pool, 14, 60, oseen, code="old")
        moved = {"no_fresh": c2(old, old_v), "fresh": c2(old, old_v, fresh_new, [mk_verdicts(RC, fresh_new)]),
                 "stale_fresh": c2(old, old_v, fresh_old, [mk_verdicts(RC, fresh_old)]),
                 "same_code": c2(old, old_v, decision=("old", "g", "k"))}
        check("C2: sheets drawn from other code than the decision identity need the fresh sheet, drawn at the decision "
              "identity and judged 60 / 60 (no fixes needed when every item was TRUE); a stale fresh sheet is VOID; "
              "the same code needs none",
              [moved[k].get("outcome") for k in ("no_fresh", "fresh", "stale_fresh", "same_code")]
              == ["pending", "pass", "void", "pass"], {k: v.get("outcome") for k, v in moved.items()})
        prompt = RC.judge_prompt(sheets[0])
        check("the judge's prompt (A3): deterministic, the locked rule verbatim, every item with its context, the "
              "answer format; the sheet's identity is not in it",
              prompt == RC.judge_prompt(json.loads(json.dumps(sheets[0]))) and RC.SHEET_RULE in prompt
              and all(it["qualified"] in prompt for it in sheets[0]["items"]) and '"verdicts"' in prompt
              and "pool_sha256" not in prompt and sheets[0]["_file_sha256"] not in prompt)
        b = RC.gate_b_text(REPO / "tests" / "sql_reader_gates.py")
        check("gate_b_text: the text of tests/sql_reader_gates.py's section B functions and the fixtures they read",
              "T-SQL negatives" in b and "def section_c" not in b and len(b) > 1000, len(b))
    except Exception:  # noqa: BLE001
        check("C2 scoring ran", False, traceback.format_exc()[-1500:])


def gate_g_c3(RC) -> None:
    def row(sec, kb, state="read"):
        return {"rel": "r", "bytes": int(kb * 1024), "seconds": sec, "state": state}
    passes = [{"s": [row(0.9, 1000)]}, {"s": [row(1.2, 1000)]}, {"s": [row(1.0, 1000)]}]
    c3 = RC.score_c3(passes, PROTO_FIX)
    slow = RC.score_c3([{"s": [row(10.5, 20000)]}] * 3, PROTO_FIX)
    check("C3: the median of the passes over the files read (1.0 ms/KB passes), a file over 10 s fails, two passes "
          "where three are registered fail",
          c3["pass_time"] and abs(c3["ms_per_kb"] - 1.0) < 1e-9 and not slow["pass_time"]
          and not RC.score_c3(passes[:2], PROTO_FIX)["pass_time"], (c3["ms_per_kb"], slow["max_file_seconds"]))
    late = RC.score_c3([{"s": [row(0.05, 100)] * 50 + [row(75.0, 2048, "slow")]}] * 3, PROTO_FIX)
    big = RC.score_c3([{"s": [row(0.5, 1000), row(0.0, 9000, "too_large")]}] * 3, PROTO_FIX)
    budget = RC.score_c3([{"s": [row(0.5, 1000), row(0.0, 10, "budget")]}] * 3, PROTO_FIX)
    check("C3 (A5): the 10 s bound counts every file whatever its state (a file past the reader's own deadline, state "
          "slow, is its slowest), and a file the reader did not read (slow, too_large, budget) fails C3",
          not late["pass_time"] and late["max_file_seconds"] >= 75.0 and not big["pass_time"]
          and not budget["pass_time"] and late.get("not_read"),
          (late["pass_time"], late["max_file_seconds"], big["pass_time"], budget["pass_time"]))


def g_dump(e, arm, nodes, calls=(), rows=(), code="c"):
    return {"id": "t", "graphifyy": e, "sql": arm, "void": None, "code": {"sha256": code},
            "reader_gates": {"sha256": "g"}, "kit": {"sha256": "k"},
            "sql_report": {"state": "read" if arm == "on" else "off", "files": 1, "declarations": 1},
            "nodes": nodes, "calls": [list(c) for c in calls], "rows": [list(r) for r in rows]}


def gate_g_parity(RC) -> None:
    py = {"key": "a.py::f", "source_file": "a.py"}
    sq = {"key": "sql:s.sql", "source_file": "s.sql", "label": "Module"}
    base = [g_dump(e, a, [py] + ([sq] if a == "on" else []), [("a.py::f", "a.py::f", "EXTRACTED")])
            for e in ENGINES for a in ("on", "off")]
    dec = ("c", "g", "k")
    good = RC.score_parity(base, PROTO_FIX, dec)
    edge = [dict(x, calls=x["calls"] + [["a.py::f", "sql:s.sql", "AMBIGUOUS"]]) if x["sql"] == "on" else x
            for x in base]
    drift = [dict(x, nodes=x["nodes"] + [{"key": "a.py::g", "source_file": "a.py"}])
             if x["sql"] == "on" and x["graphifyy"] == ENGINES[1] else x for x in base]
    engdiff = [dict(x, nodes=[py, dict(sq, name="z")]) if x["sql"] == "on" and x["graphifyy"] == ENGINES[1] else x
               for x in base]
    check("C4 / C5: identical on both ends passes; an edge to a SQL node fails C5; a non-SQL node only with the "
          "reader on fails C5; SQL nodes that differ between the ends fail C4; a missing dump is VOID",
          good["c4_pass"] and good["c5_pass"] and not RC.score_parity(edge, PROTO_FIX, dec)["c5_pass"]
          and not RC.score_parity(drift, PROTO_FIX, dec)["c5_pass"]
          and not RC.score_parity(engdiff, PROTO_FIX, dec)["c4_pass"]
          and RC.score_parity(base[:3], PROTO_FIX, dec)["c4"]["t"]["void"],
          (good["c4_pass"], good["c5_pass"]))
    old = RC.score_parity([dict(x, code={"sha256": "x"}) for x in base], PROTO_FIX, dec)
    one = RC.score_parity([dict(x, code={"sha256": "x"}) if i == 0 else x for i, x in enumerate(base)], PROTO_FIX, dec)
    check("C4 / C5 (A1): dumps made at another identity than the parity run's are VOID, never data (outcomes void), "
          "and the result lists the dumps' identities",
          not old["c4_pass"] and not old["c5_pass"] and old.get("c4_outcome") == old.get("c5_outcome") == "void"
          and not one["c4_pass"] and good.get("dump_identities") == [list(dec)]
          and good.get("c4_outcome") == good.get("c5_outcome") == "pass",
          (old.get("c4_outcome"), old.get("c5_outcome"), good.get("dump_identities")))


def g_targets():
    return [{"id": f"q{i}", "file": f"f{i}.go", "name": f"F{i}", "line": 1, "query": f"query {i}"} for i in range(20)]


def g_cap(arm, sql_per_q=0, lost=0, free=True, code="c", hits=20, nodes=1):
    qs = []
    for i, t in enumerate(g_targets()):
        res = [{"key": f"k{i}", "label": "Function", "name": t["name"], "source_file": t["file"]}]
        if (arm == "on" and i < lost) or i >= hits:
            res = []
        for j in range(sql_per_q):
            res.append({"key": f"sql:m{j}.sql", "label": "Module", "name": "m", "source_file": f"m{j}.sql"})
        res += [{"key": f"o{j}", "label": "Function", "name": "o", "source_file": "o.go"} for j in range(20)]
        qs.append({"id": t["id"], "query": t["query"], "results": res, "target_nodes": nodes,
                   "sql_module_decls": {f"sql:m{j}.sql": 0 if free else 2 for j in range(sql_per_q)}})
    return dict(IDENT, arm=arm, deterministic=True, queries=qs, code={"sha256": code})


def gate_g_c6(RC) -> None:
    targets, dec = g_targets(), ("c", "g", "k")
    a = RC.score_c6(g_cap("off"), g_cap("on", 2, 2), targets, PROTO_FIX, dec)
    b = RC.score_c6(g_cap("off"), g_cap("on", 3, 0), targets, PROTO_FIX, dec)
    c = RC.score_c6(g_cap("off"), g_cap("on", 0, 3), targets, PROTO_FIX, dec)
    dd = RC.score_c6(g_cap("off"), dict(g_cap("on"), deterministic=False), targets, PROTO_FIX, dec)
    e = RC.score_c6(g_cap("off"), g_cap("on", 3, 0, free=False), targets, PROTO_FIX, dec)
    check("C6: mean 2.0 SQL nodes in the top 15 and 2 lost targets pass; 3.0 fails; 3 lost fails; a capture whose "
          "passes differ is VOID; the fallback applies only when every SQL node shown is a declaration-free :Module",
          a["pass"] and not b["pass"] and not c["pass"] and dd["void"] and b["fallback_applies"]
          and not e["fallback_applies"] and not c["fallback_applies"],
          (a.get("mean_sql_top"), a.get("n_lost"), b["pass"], c["pass"], b.get("fallback_applies")))
    voids = {"other_identity": RC.score_c6(g_cap("off", code="x"), g_cap("on", code="x"), targets, PROTO_FIX, dec),
             "target_not_in_graph": RC.score_c6(g_cap("off", nodes=0), g_cap("on"), targets, PROTO_FIX, dec),
             "too_few_off_hits": RC.score_c6(g_cap("off", hits=4), g_cap("on", hits=4), targets, PROTO_FIX, dec)}
    floor = RC.score_c6(g_cap("off", hits=5), g_cap("on", hits=5), targets, PROTO_FIX, dec)
    check("C6 (A1, A6): VOID when the captures were made at another identity, when a target has no node in the off "
          "graph, or when the off arm hits fewer than 5 targets (5 is data); the result copies the captures' "
          "identities",
          all(v["void"] and not v["pass"] and v.get("outcome") == "void" for v in voids.values())
          and not floor["void"] and floor["pass"] and a.get("capture_identities") == [list(dec), list(dec)]
          and a.get("outcome") == "pass" and b.get("outcome") == "fail",
          ({k: v["void"] for k, v in voids.items()}, floor["void"], a.get("capture_identities")))
    row_m = RC.search_row({"id": "4:x", "name": "m", "type": "Module", "file": "db/m.SQL", "line": 1, "score": 1})
    row_f = RC.search_row({"id": "4:y", "name": "F", "type": "Function", "file": "a.go", "line": 3})
    want_m = {"key": "sql:db/m.SQL", "label": "Module", "name": "m", "source_file": "db/m.SQL", "start_line": 1}
    check("C6 capture rows: a search result's type / file / line mapped; a SQL file's :Module keyed sql:<file>, "
          "counted as a SQL node; a Go function is not", row_m == want_m and RC.is_sql_node(row_m)
          and not RC.is_sql_node(row_f) and row_f["key"] is None, (row_m, row_f))


def g_results(prereg) -> dict:
    ident = dict(IDENT, registration="PREREG.md", prereg=prereg)
    dec = ["c", "g", "k"]
    return {"metrics.json": dict(ident, kind="metrics", c1_pass=True, c1_void=[], c1_outcome="pass",
                                 c3={"pass_time": True, "void": None, "outcome": "pass"}, pool_sha256="P"),
            "gate_0.8.39.json": dict(ident, kind="gate", engine="0.8.39", **{"pass": True}),
            "gate_0.8.51.json": dict(ident, kind="gate", engine="0.8.51", **{"pass": True}),
            "audit.json": dict(ident, kind="audit", outcome="pass", pool_sha256="P", fresh=None,
                               fresh_identity=None, sheet_identities=[dec] * 3, fresh_pool_sha256=None,
                               bindings=[["s1", "v1"], ["s2", "v2"], ["s3", "v3"]], **{"pass": True}),
            "parity.json": dict(ident, kind="parity", c4_pass=True, c5_pass=True, c4_outcome="pass",
                                c5_outcome="pass", dump_identities=[dec]),
            "c6_cs-repo.json": dict(ident, kind="c6", source="cs-repo", outcome="pass", capture_identities=[dec, dec],
                                    fallback_applies=False, **{"pass": True}),
            "c6_go-repo.json": dict(ident, kind="c6", source="go-repo", outcome="pass", capture_identities=[dec, dec],
                                    fallback_applies=False, **{"pass": True})}


def g_ledger(RC, results, before=()):
    """The ledger a results directory would hold: the runs in `before` (name, result), then every result file."""
    lines, shas = [], {}
    for name, res in list(before) + sorted(results.items()):
        shas[name] = jsha(res)
        lines.append(RC.ledger_line(lines, name, shas[name], res))
    return lines, shas


def gate_g_decide(RC) -> None:
    try:
        cur = {"prereg": {"protocol": "p", "registered": "r", "amendments": "a"}}

        def dec(results, before=(), kit="k", lines=None, shas=None):
            ln, sh = g_ledger(RC, results, before)
            return RC.decide(results, PROTO_FIX, {}, cur, shas=sh if shas is None else shas,
                             ledger=ln if lines is None else lines, registered_kit=kit)["decision"]
        base = g_results(cur["prereg"])
        cp = json.loads(json.dumps(base))
        flip = dec(base)

        def variant(path, value):
            v = json.loads(json.dumps(base))
            name, *keys = path
            tgt = v[name]
            for k in keys[:-1]:
                tgt = tgt[k]
            tgt[keys[-1]] = value
            return v
        gone = json.loads(json.dumps(base))
        gone.pop("gate_0.8.51.json")
        mixed = variant(("parity.json", "code"), {"sha256": "other"})
        stays = {"one_c6_fails": dec(variant(("c6_go-repo.json", "pass"), False)),
                 "fixture": dec(variant(("audit.json", "registration"), "fixture")),
                 "two_identities": dec(mixed), "gate_missing": dec(gone),
                 "busy_box": dec(variant(("metrics.json", "c3", "void"), "not quiet")),
                 "kit_unregistered": dec(base, kit=None), "kit_other": dec(base, kit="k2"),
                 "dumps_other_identity": dec(variant(("parity.json", "dump_identities"), [["x", "g", "k"]])),
                 "captures_other_identity": dec(variant(("c6_go-repo.json", "capture_identities"),
                                                        [["x", "g", "k"], ["c", "g", "k"]])),
                 "sheets_other_code": dec(variant(("audit.json", "sheet_identities"),
                                                  [["x", "g", "k"]] * 3)),
                 "pool_not_metrics": dec(variant(("audit.json", "pool_sha256"), "Q"))}
        old_fail = dict(base["metrics.json"], code={"sha256": "x"}, c1_pass=False, c1_outcome="fail")
        old_c6 = dict(base["c6_go-repo.json"], code={"sha256": "x"}, outcome="fail", fallback_applies=True,
                      **{"pass": False})
        old_c6b = dict(old_c6, code={"sha256": "y"})
        now_c6 = dict(old_c6, code={"sha256": "c"})
        old_void = dict(base["metrics.json"], code={"sha256": "x"}, c3={"pass_time": False, "void": "busy",
                                                                       "outcome": "void"})
        old_pending = dict(base["audit.json"], code={"sha256": "x"}, outcome="pending", **{"pass": False})
        twice = dict(base["audit.json"], bindings=[["s1", "v9"]])
        stays.update({
            "earlier_fail_is_final": dec(base, before=[("metrics.json", old_fail)]),
            "fallback_at_two_identities": dec(base, before=[("c6_go-repo.json", old_c6),
                                                            ("c6_go-repo.json", old_c6b)]),
            "fallback_not_made": dec(base, before=[("c6_go-repo.json", now_c6)]),
            "sheet_judged_twice": dec(base, before=[("audit.json", twice)]),
            "result_not_in_ledger": dec(base, shas=dict(g_ledger(RC, base)[1], **{"parity.json": "0" * 64})),
            "ledger_edited": dec(base, lines=[ln.replace('"pass"', '"PASS"', 1) if i == 2 else ln
                                              for i, ln in enumerate(g_ledger(RC, base)[0])]),
            "ledger_missing": dec(base, lines=[])})
        flips = {"fallback_once": dec(base, before=[("c6_go-repo.json", old_c6)]),
                 "earlier_void": dec(base, before=[("metrics.json", old_void)]),
                 "earlier_pending": dec(base, before=[("audit.json", old_pending)])}
        check("decide: every criterion passing on one identity, with the registered kit and a clean ledger -> FLIP; "
              "an earlier VOID, an earlier pending C2 and one C6 fallback do not block it (A2)",
              flip == "FLIP" and all(v == "FLIP" for v in flips.values()) and base == cp, (flip, flips))
        check("decide -> STAYS OPT-IN: a failing C6, a fixture registration, two identities, a missing gate run, C3 on "
              "a busy box, an unregistered or other kit, dumps / captures / sheets / pool from another identity "
              "(A1); a FAIL earlier in the ledger, the fallback twice or not made, a sheet judged twice, a result "
              "not in the ledger, an edited or missing ledger (A2, A3)",
              all(v == "STAYS OPT-IN" for v in stays.values()), {k: v for k, v in stays.items() if v != "STAYS OPT-IN"})
        ln, sh = g_ledger(RC, base)
        why = " ".join(RC.decide(base, PROTO_FIX, {}, cur, shas=sh, ledger=ln, registered_kit=None)["problems"])
        check("decide names an unfilled kit slot as the reason (A1: no decision before the kit is registered)",
              "not registered" in why, why[:300])
    except Exception:  # noqa: BLE001
        check("decide ran", False, traceback.format_exc()[-1500:])


def gate_g_candidates(RC) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="dm_sqlp_"))
    try:
        for d in ("pkg", "vendor", "public/model", "gen", "plugin_api_tests/p"):
            (tmp / d).mkdir(parents=True)
        body = "{\n\ta := 1\n\tb := 2\n\t_ = a\n\t_ = b\n}\n"
        (tmp / "pkg" / "a.go").write_text("package p\n\nfunc Alpha() " + body + "\nfunc (s *S) Beta(x int) " + body
                                          + "\nfunc gamma() " + body + "\nfunc Short() {}\n", encoding="utf-8")
        (tmp / "pkg" / "a_test.go").write_text("package p\n\nfunc TestX() " + body, encoding="utf-8")
        (tmp / "pkg" / "gen.go").write_text("// Code generated by \"make x\"\n// DO NOT EDIT\npackage p\n\nfunc Gen() "
                                            + body, encoding="utf-8")
        (tmp / "vendor" / "v.go").write_text("package v\n\nfunc Vend() " + body, encoding="utf-8")
        cands = RC.c6_candidates(tmp, "go")
        check("C6 candidates: exported Go functions and methods with a block of 5+ lines; tests, a two-line generated "
              "header, vendor/ and short or unexported ones left out",
              [(c["name"], c["line"]) for c in cands] == [("Alpha", 3), ("Beta", 10)], cands)
        (tmp / "public" / "model" / "m.go").write_text("package model\n\nfunc Pub() " + body, encoding="utf-8")
        (tmp / "gen" / "g.go").write_text("package gen\n\nfunc Ignored() " + body, encoding="utf-8")
        (tmp / ".gitignore").write_text("# build output\ngen/\n", encoding="utf-8")
        (tmp / "pkg" / "apitestlib.go").write_text("package p\n\nfunc Helper() " + body, encoding="utf-8")
        (tmp / "plugin_api_tests" / "p" / "main.go").write_text("package main\n\nfunc Plug() " + body,
                                                                 encoding="utf-8")
        attempt("C6 candidates (A6): a file the product's vendor rule drops (public/, a directory the root's "
                ".gitignore names) and Go test support code (a name or directory holding 'test') are left out",
                lambda: ([(c["name"], c["line"]) for c in RC.c6_candidates(tmp, "go")] == [("Alpha", 3), ("Beta", 10)],
                         RC.c6_candidates(tmp, "go")))
        many = [{"file": f"f{i % 7}.go", "name": f"N{i % 30}", "line": i} for i in range(200)]
        dr = RC.c6_draw(many, 5, 20)
        check("C6 draw: deterministic for its seed, 20 targets, no name twice", dr == RC.c6_draw(many, 5, 20)
              and len(dr) == 20 and len({x["name"] for x in dr}) == 20)
    finally:
        rmtree(tmp)


def g_build(RC):
    tmp = Path(tempfile.mkdtemp(prefix="dm_sqlp_"))
    try:
        (tmp / ".deepmap").mkdir()
        good_gb = {"extractor": "graphify", "graph_engines": ["graphify"], "built_at_utc": "x"}
        good_last = {"outcome": "synced", "rc": 0}
        env_ok = {"DEEPMAP_EXTRACTOR": "graphify"}

        def run_case(gb, last, environ):
            for name, obj in (("graph_build.json", gb), ("sync_last.json", last)):
                p = tmp / ".deepmap" / name
                if p.exists():
                    p.unlink()
                if obj is not None:
                    p.write_text(json.dumps(obj), encoding="utf-8")
            return RC.build_problems(tmp, environ)
        ok_p, ok_rec = run_case(good_gb, good_last, env_ok)
        bad = [run_case(good_gb, good_last, {})[0],
               run_case(dict(good_gb, extractor="ast"), good_last, env_ok)[0],
               run_case(dict(good_gb, graph_engines=["ast", "graphify"]), good_last, env_ok)[0],
               run_case(good_gb, dict(good_last, outcome="not-pushed", rc=4), env_ok)[0],
               run_case(good_gb, None, env_ok)[0], run_case(None, good_last, env_ok)[0]]
        return (not ok_p and ok_rec.get("graph_engines") == ["graphify"] and ok_rec.get("sync_rc") == 0
                and all(bad), (ok_p, ok_rec, [bool(b) for b in bad]))
    finally:
        rmtree(tmp)


# =====================================================================================================================
# H: metrics end to end on one synthetic fixture corpus
# =====================================================================================================================
def gate_h(FC, RC, kit: Path) -> None:
    print("\n-- H: run_corpus metrics on ONE synthetic fixture corpus (the product reader runs) --")
    tmp = Path(tempfile.mkdtemp(prefix="dm_sqlp_"))
    try:
        corpus, out = tmp / "corpus", tmp / "out"
        tree = corpus / "fx" / "tree"
        (tree / "S" / "Tables").mkdir(parents=True)
        files = {"schema.sql": b"CREATE TABLE a (id int);\nCREATE VIEW v AS SELECT id FROM a;\n",
                 "S/Tables/T1.sql": b"CREATE TABLE [S].[T1] ([Id] INT NOT NULL);\nGO\n",
                 "proc.sql": b"CREATE PROCEDURE p AS\nBEGIN\n  SELECT 1 FROM a;\nEND\nGO\n"}
        for rel, data in files.items():
            (tree / rel).write_bytes(data)
        man = FC.manifest_of(sorted(files.items()))
        reg = {"sources": [{"id": "fx", "type": "git", "layout": "sql-set", "url": "https://example.invalid/fx.git",
                            "commit": "0" * 40, "select": [], **man}],
               "truth": {"fx-lines": {"source": "fx", "rule": "line-start", "file": "schema.sql",
                                      "kinds": {"table": 1, "view": 1}},
                         "fx-ssdt": {"source": "fx", "rule": "ssdt", "dir": "", "folders": {"Tables": 1}}}}
        proto = json.loads(json.dumps(PROTO_FIX))
        proto["c2"]["pool"] = [{"source": "fx"}]
        (tmp / "reg.json").write_text(json.dumps(reg), encoding="utf-8")
        (tmp / "proto.json").write_text(json.dumps(proto), encoding="utf-8")
        code, msg = run(kit / "run_corpus.py", "metrics", "--corpus", corpus, "--out", out, "--registered",
                        tmp / "reg.json", "--protocol", tmp / "proto.json", timeout=300)
        m = json.loads((out / "metrics.json").read_text(encoding="utf-8")) if (out / "metrics.json").is_file() else {}
        pool = (out / "pool.jsonl").read_text(encoding="utf-8").splitlines() if (out / "pool.jsonl").is_file() else []
        busy = code == 3 and not m.get("c1_void") and bool((m.get("c3") or {}).get("void"))
        check("metrics on the fixture: a score (exit 0 / 1; exit 3 only for C3's quiet-box rule on a busy box), "
              "metrics.json and pool.jsonl written, registration 'fixture' (never decision data)",
              (code in (0, 1) or busy) and m.get("registration") == "fixture" and m.get("prereg") is None,
              (code, msg[-600:]))
        check("C1 on the fixture: the line-start set and the SSDT file found by the product reader",
              (m.get("c1") or {}).get("fx-lines", {}).get("pass")
              and (m.get("c1") or {}).get("fx-ssdt", {}).get("pass"),
              m.get("c1"))
        check("C3 on the fixture: three passes timed, every file read; the pool holds the four declarations",
              len((m.get("c3") or {}).get("ms_per_kb_passes") or []) == 3
              and (m.get("sources") or {}).get("fx", {}).get("states") == {"read": 3} and len(pool) == 4,
              (m.get("c3"), m.get("sources"), len(pool)))
        check("the result records the code identity of build/*.py", (m.get("code") or {}) == RC.code_hash())
        attempt("the result records all three identity hashes (A1) and the pool's sha256 (A4)",
                lambda: (m.get("reader_gates") == RC.reader_gates_hash() and m.get("kit") == RC.kit_hash()
                         and m.get("pool_sha256") == RC.pool_sha([json.loads(x) for x in pool]),
                         {k: m.get(k) for k in ("reader_gates", "kit", "pool_sha256")}))
        led = (out / "ledger.jsonl").read_text(encoding="utf-8").splitlines() if (out / "ledger.jsonl").is_file() \
            else []
        entry = json.loads(led[0]) if led else {}
        check("metrics appends its run to the ledger (A2): one line naming metrics.json, its sha256, its identity and "
              "the C1 / C3 outcomes, chained from nothing",
              len(led) == 1 and entry.get("file") == "metrics.json" and entry.get("seq") == 1
              and entry.get("prev") == ""
              and entry.get("sha256") == hashlib.sha256((out / "metrics.json").read_bytes()).hexdigest()
              and set(entry.get("outcomes") or {}) == {"C1", "C3"} and entry.get("identity") == list(
                  RC.identity_of(m) if hasattr(RC, "identity_of") else ()), (led[:1], m.get("kit")))
        gate_h_sample(RC, kit, tmp, corpus, out)
        (tree / "schema.sql").write_bytes(b"CREATE TABLE a (id bigint);\n")
        code, msg = run(kit / "run_corpus.py", "metrics", "--corpus", corpus, "--out", tmp / "out2", "--registered",
                        tmp / "reg.json", "--protocol", tmp / "proto.json", timeout=300)
        check("metrics refuses a corpus that does not verify (exit 2, nothing written)",
              code == 2 and not (tmp / "out2").exists(), (code, msg[-300:]))
    except Exception:  # noqa: BLE001
        check("section H ran", False, traceback.format_exc()[-1500:])
    finally:
        rmtree(tmp)


def gate_h_sample(RC, kit: Path, tmp: Path, corpus: Path, out: Path) -> None:
    """sample and prompt on the fixture's pool (A3, A4): a registered seed at its registered size, the earlier
    registered sheets excluded and nothing else; the judge's prompt written outside the repository."""
    rc = kit / "run_corpus.py"
    fx = ("--registered", tmp / "reg.json", "--protocol", tmp / "proto.json")
    pool = out / "pool.jsonl"

    def sample(seed, *extra, where="s"):
        return run(rc, "sample", "--pool", pool, "--corpus", corpus, "--out", tmp / where, "--seed", seed, *extra, *fx,
                   timeout=120)
    refused = {"unregistered seed": sample(99, where="r1"), "other size": sample(11, "--n", 5, where="r2"),
               "earlier sheet not excluded": sample(12, where="r3")}
    first = sample(11)
    sheet = tmp / "s" / "sheet_11.json"
    junk = tmp / "junk.json"
    if sheet.is_file():
        data = json.loads(sheet.read_text(encoding="utf-8"))
        junk.write_text(json.dumps(dict(data, seed=99)), encoding="utf-8")
        refused["a foreign sheet excluded"] = sample(12, "--exclude", junk, where="r4")
        refused["the sheet itself excluded twice"] = sample(13, "--exclude", sheet, sheet, where="r5")
    check("sample refuses (exit 2, nothing written) an unregistered seed, another size, a seed whose earlier "
          "registered sheets are not excluded, and a foreign or doubled exclusion (A4)",
          all(code == 2 and "REFUSED" in msg for code, msg in refused.values())
          and not any((tmp / w).exists() for w in ("r1", "r2", "r3", "r4", "r5")),
          {k: (c, m[-200:]) for k, (c, m) in refused.items() if c != 2})
    second = sample(12, "--exclude", sheet)
    s12 = json.loads((tmp / "s" / "sheet_12.json").read_text(encoding="utf-8")) \
        if (tmp / "s" / "sheet_12.json").is_file() else {}
    check("sample writes the registered draw with the pool's sha256 and its identity; the next seed with the earlier "
          "sheet excluded", first[0] == 0 and second[0] == 0 and sheet.is_file()
          and json.loads(sheet.read_text(encoding="utf-8")).get("pool_sha256")
          == hashlib.sha256(pool.read_bytes()).hexdigest() and s12.get("kit") == RC.kit_hash()
          and s12.get("items") == [],
          (first, second))
    code, msg = run(rc, "prompt", "--sheet", sheet, "--out", tmp / "prompt.txt", *fx, timeout=120)
    text = (tmp / "prompt.txt").read_text(encoding="utf-8") if (tmp / "prompt.txt").is_file() else ""
    check("prompt writes the judge's prompt for a sheet (exactly run_corpus.judge_prompt) and prints its sha256 (A3)",
          code == 0 and sheet.is_file() and text == RC.judge_prompt(json.loads(sheet.read_text(encoding="utf-8")))
          and hashlib.sha256(text.encode("utf-8")).hexdigest() in msg, (code, msg[-300:]))


# =====================================================================================================================
# I: dump through the real engine
# =====================================================================================================================
def gate_i(RC, kit: Path) -> None:
    print("\n-- I: dump on a tiny tree through the real engine --")
    if importlib.util.find_spec("graphify") is None:
        skip("dump through the real engine", "graphify is not installed")
        return
    tmp = Path(tempfile.mkdtemp(prefix="dm_sqlp_"))
    try:
        tree, home = tmp / "t", tmp / "h"
        tree.mkdir()
        (tree / "app.py").write_bytes(b"def helper(x):\n    return x + 1\n\n\ndef main():\n    return helper(2)\n")
        (tree / "schema.sql").write_bytes(b"CREATE TABLE orders (id int);\nCREATE VIEW v AS SELECT id FROM orders;\n")
        dumps = tmp / "dumps"
        res = {}
        for arm in ("off", "on"):
            code, msg = run(kit / "run_corpus.py", "dump", "--tree", tree, "--id", "t", "--sql", arm, "--out",
                            dumps / f"t_{arm}.json", extra_env={"DEEPMAP_HOME": str(home)}, timeout=600)
            res[arm] = (code, msg)
        check("dump with the reader off is data (exit 0) through graphify", res["off"][0] == 0, res["off"][1][-600:])
        sys.path.insert(0, str(BUILD))
        import graphify_extract  # noqa: WPS433
        reads = callable(getattr(graphify_extract, "_sql_supplement", None))
        if reads:
            check("dump with the reader on is data (exit 0): graphify_extract has the SQL step", res["on"][0] == 0,
                  res["on"][1][-600:])
            on = json.loads((dumps / "t_on.json").read_text(encoding="utf-8"))
            off = json.loads((dumps / "t_off.json").read_text(encoding="utf-8"))
            check("on the tiny tree the reader-on dump holds the SQL nodes and the non-SQL graph is identical (C5's "
                  "comparison)", any(n["key"].startswith("sql:") for n in on["nodes"])
                  and RC._non_sql(on) == RC._non_sql(off) and RC._sql_edges(on) == 0)
        else:
            check("dump with the reader on is VOID (exit 3) while the product does not read SQL in extraction, and "
                  "says why", res["on"][0] == 3 and "does not read SQL" in res["on"][1], res["on"][1][-600:])
    except Exception:  # noqa: BLE001
        check("section I ran", False, traceback.format_exc()[-1500:])
    finally:
        rmtree(tmp)


# =====================================================================================================================
# J: opt-in, the fetched corpus
# =====================================================================================================================
def gate_j(FC, RC, kit: Path, proto, reg) -> None:
    print("\n-- J: opt-in: the fetched corpus (GSQL_CORPUS) --")
    corpus = os.environ.get("GSQL_CORPUS")
    if not corpus:
        skip("the fetched corpus verifies and the C6 draws equal the registered targets", "GSQL_CORPUS is not set")
        return
    for s in reg.get("sources") or []:
        res = FC.verify_source(corpus, s)
        check(f"{s['id']} verifies against its registration", res["ok"], res["problems"])
    try:
        proto, reg, _am = FC.load_amended(kit / "PREREG.md")
    except Exception as exc:  # noqa: BLE001
        check("the amended registration loads", False, repr(exc))
        return
    for sid, spec in (proto.get("c6") or {}).get("sources", {}).items():
        src = next(s for s in reg["sources"] if s["id"] == sid)
        drawn = RC.c6_draw(RC.c6_candidates(FC.project_root(corpus, src), spec["lang"]), spec["draw_seed"],
                           proto["c6"]["queries"])
        want = [{k: t[k] for k in ("file", "name", "line")} for t in reg["c6"][sid]]
        check(f"{sid}: the seeded draw (A6's candidates) equals the C6 targets as amended", drawn == want)


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    kit = Path(argv[argv.index("--kit") + 1]).resolve() if "--kit" in argv else KIT_DEFAULT
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(f"G-SQL pre-registration gate (kit: {kit})")
    gate_a(kit)
    proto, _, text = gate_b(kit)
    reg = gate_c(text, proto) if text else None
    am = gate_k(kit, text, proto, reg) if text else None
    FC = RC = None
    if proto is not None and reg is not None:
        FC, RC = gate_d(kit, proto, reg, text, am)
    else:
        check("the scripts can be checked against PREREG.md", False, "PREREG.md's blocks did not parse")
    if (kit / "fetch_corpus.py").is_file() and (kit / "run_corpus.py").is_file():
        gate_e(kit)
    if FC is not None and RC is not None:
        gate_f(FC, kit)
        gate_g(RC)
        gate_h(FC, RC, kit)
        gate_i(RC, kit)
        gate_j(FC, RC, kit, proto, reg)
    total = len(PASSES) + len(FAILS)
    if FAILS:
        print(f"\nRESULT: RED: {len(FAILS)} of {total} gates failed ({len(SKIPS)} skipped):")
        for name in FAILS:
            print(f"  - {name}")
        return 1
    print(f"\nRESULT: GREEN: {len(PASSES)}/{total} gates passed ({len(SKIPS)} skipped).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
