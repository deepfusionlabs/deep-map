"""Shared helpers for the call-linking measurement kit (benchmark/call_linking/). Stdlib only.

Measurement code, never product code: nothing under build/ imports this, and nothing here is shipped
(requirements.txt / sbom / NOTICE never name the reference resolvers this kit drives — jedi, TypeScript).

The dump format (what offline_calls.py writes and every scorer here reads):
    {"schema": "call-linking/offline-calls/1", "nodes": [{key, label, name, source_file, start_line,
     end_line}], "edges": [[caller_key, callee_key, grade], ...], ...}
The truth format (truth_jedi.py / truth_ts.js):
    {"schema": "call-linking/truth/1", "resolver": {...}, "rows": [{file, line, col, form, name,
     defstack: [[qualname, line, [alt lines]]...], defs: [{file, line, alt_lines, type, name}]}]}
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from collections import Counter, defaultdict
from pathlib import Path

KIT = Path(__file__).resolve().parent
# The repository the kit measures (its benchmark/questions.yaml, tests/oracles/registry.json): this one, or
# CALL_LINKING_REPO for a copy of the kit run from elsewhere (the harness gate's mutation runs).
REPO = Path(os.environ.get("CALL_LINKING_REPO") or KIT.parent.parent)
# The product code the kit measures: this repository's build/, or CALL_LINKING_BUILD (a snapshot, or the real
# build/ for a copy of the kit run from elsewhere — the harness gate's mutation runs).
BUILD = Path(os.environ.get("CALL_LINKING_BUILD") or (REPO / "build"))

DUMP_SCHEMA = "call-linking/offline-calls/1"
TRUTH_SCHEMA = "call-linking/truth/1"
GRADES = ("EXTRACTED", "INFERRED", "AMBIGUOUS")

# Design §2.6: a caller is NON-PRODUCTION when a path COMPONENT (never a substring) is one of these.
# `examples.py`, `src/docs_parser.py` and `mydocs/` are production (§5 "Non-production path").
NONPROD_PARTS = frozenset({"examples", "example", "docs", "doc", "docs_src", "samples", "sample", "demo", "demos"})

# Call forms the reference resolvers emit, in the order compare_calls attributes a pair to one form.
FORM_ORDER = ("bare", "self", "this", "cls", "super", "name_attr", "recv", "ctor", "other")
# D3-G1's receiver forms per family (design §8.5): py = jedi name_attr + recv; js = tsc name_attr + recv + this.
RECEIVER_FORMS = {"py": ("name_attr", "recv"), "js": ("name_attr", "recv", "this")}


def parse_cli(argv, value_opts=(), multi_opts=()):
    """(opts, positional) for the kit's CLIs: `--opt=value` or `--opt value` for the options in `value_opts`
    (`multi_opts` repeat and collect a list); any other `--flag` maps to '' (and `--x=y` keeps y)."""
    opts, pos = {}, []
    i = 0
    argv = list(argv)
    while i < len(argv):
        a = argv[i]
        if a.startswith("--"):
            if "=" in a:
                k, v = a.split("=", 1)
            elif a in value_opts or a in multi_opts:
                if i + 1 >= len(argv):
                    raise SystemExit(f"{a} needs a value")
                k, v = a, argv[i + 1]
                i += 1
            else:
                k, v = a, ""
            if k in multi_opts:
                opts.setdefault(k, []).append(v)
            else:
                opts[k] = v
        else:
            pos.append(a)
        i += 1
    return opts, pos


def utf8_stdio() -> None:
    """Console output as UTF-8 (a Windows console code page cannot print the tools' messages otherwise)."""
    import sys
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass


def load_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def dump_json(obj) -> str:
    """The kit's one serialisation: sorted keys, compact, UTF-8, trailing newline (byte-stable)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"


def write_json(path, obj) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(dump_json(obj))


def norm_path(p) -> str:
    return str(p or "").replace("\\", "/")


def last_segment(name) -> str:
    s = str(name or "")
    s = s.split("::")[-1]
    return s.split(".")[-1]


def local_is_nonprod_path(source_file) -> bool:
    """§2.6: a DIRECTORY component in NONPROD_PARTS (case-insensitive); the file name itself never counts."""
    parts = norm_path(source_file).split("/")
    return any(p.lower() in NONPROD_PARTS for p in parts[:-1])


_NONPROD_RULE = None


def is_nonprod_path(source_file) -> bool:
    """The product's rule (build/typed_calls.py `is_nonprod_path`) when that module exists, else the §2.6 rule
    above — the harness gate checks the two agree, so the kit never classifies callers by a private copy."""
    global _NONPROD_RULE
    if _NONPROD_RULE is None:
        import sys
        if str(BUILD) not in sys.path:
            sys.path.insert(0, str(BUILD))
        try:
            import typed_calls  # noqa: WPS433 — optional (slice S3a)
            fn = getattr(typed_calls, "is_nonprod_path", None)
            _NONPROD_RULE = fn if callable(fn) else local_is_nonprod_path
        except Exception:  # noqa: BLE001 — absent or broken: the documented rule
            _NONPROD_RULE = local_is_nonprod_path
    return bool(_NONPROD_RULE(norm_path(source_file)))


_TEST_RULE = None


def is_test_path(source_file) -> bool:
    """The product's own rule (build/sync.py `_is_test_path`), loaded lazily — one rule, never a copy."""
    global _TEST_RULE
    if _TEST_RULE is None:
        import sys
        if str(BUILD) not in sys.path:
            sys.path.insert(0, str(BUILD))
        import sync  # noqa: WPS433 — measurement tooling reads the product rule directly
        _TEST_RULE = sync._is_test_path
    return bool(_TEST_RULE(norm_path(source_file)))


def caller_class(source_file) -> str:
    """'test' | 'nonprod' | 'prod' — the §2.6 caller policy classes (test wins over nonprod)."""
    if is_test_path(source_file):
        return "test"
    if is_nonprod_path(source_file):
        return "nonprod"
    return "prod"


def in_prefixes(source_file, prefixes) -> bool:
    if not prefixes:
        return True
    f = norm_path(source_file)
    return any(f.startswith(p) for p in prefixes)


def parse_prefixes(value):
    if not value:
        return None
    out = [norm_path(p.strip()) for p in str(value).split(",") if p.strip()]
    return out or None


def stable_key(node) -> str:
    """The engine-version-stable identity of a node: label:name@file:line (design §8.4 E)."""
    return f"{node.get('label')}:{node.get('name')}@{norm_path(node.get('source_file'))}:{node.get('start_line')}"


def edges_of(dump) -> dict:
    """{(caller_key, callee_key): grade} of a dump."""
    return {(a, b): c for a, b, c in dump.get("edges") or []}


def node_index(dump) -> dict:
    return {n["key"]: n for n in dump.get("nodes") or []}


def arm3_multiplier(in_degree: int) -> float:
    """retrieval.py arm 3: score × (1 + log(1 + inDegree)), inDegree = incoming :CALLS edges."""
    return 1.0 + math.log(1.0 + float(in_degree))


def wilson(successes: int, n: int, z: float = 1.959963984540054):
    """(point, lower, upper) Wilson score interval; (None, None, None) for n == 0."""
    if n <= 0:
        return (None, None, None)
    p = successes / n
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (p, max(0.0, centre - margin), min(1.0, centre + margin))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_code_hash(build_dir=None) -> dict:
    """sha256 over build/*.py (sorted by name; name + NUL + bytes + NUL per file) — the RC code identity
    PREREG.md records when there is no commit to name."""
    d = Path(build_dir or BUILD)
    h = hashlib.sha256()
    files = sorted(p for p in d.glob("*.py") if p.is_file())
    for p in files:
        h.update(p.name.encode("utf-8") + b"\0")
        h.update(p.read_bytes() + b"\0")
    return {"sha256": h.hexdigest(), "files": len(files)}


# ---- truth -> graph pairs (shared by compare_calls, delta, impact_spot, impact_offline, audit_sample) ----
def _line_candidates(entry_line, alt_lines):
    out = []
    for x in [entry_line] + list(alt_lines or []):
        if isinstance(x, int) and x not in out:
            out.append(x)
    return out


class GraphLocator:
    """Maps a truth row's caller / callee definitions onto the nodes of one dump.

    A definition at (file, line) maps to the non-Module node(s) whose start_line is that line or one of the
    row's alternative lines (decorators, a TS name line), preferring a node whose last name segment equals the
    definition's name and, for a callee, the label kind the resolver reported (class -> :Class)."""

    def __init__(self, nodes):
        self.at = defaultdict(list)
        self.module = {}
        for n in nodes:
            f = norm_path(n.get("source_file"))
            if n.get("label") == "Module":
                self.module.setdefault(f, n)
                continue
            self.at[(f, n.get("start_line"))].append(n)

    def _pick(self, cands, name, want_class=None):
        if not cands:
            return None
        last = last_segment(name)
        ranked = sorted(cands, key=lambda n: (
            0 if last and last_segment(n.get("name")) == last else 1,
            0 if want_class is None else (0 if (n.get("label") == "Class") == want_class else 1),
            str(n.get("key"))))
        return ranked[0]

    def node_at(self, file, lines, name=None, want_class=None):
        f = norm_path(file)
        for line in lines:
            hit = self._pick(self.at.get((f, line)), name, want_class)
            if hit is not None:
                return hit
        return None

    def caller_of(self, row):
        """The innermost defstack entry that has a node; else the file's :Module node; else None."""
        f = row.get("file")
        for entry in reversed(row.get("defstack") or []):
            name, line = entry[0], entry[1]
            alts = entry[2] if len(entry) > 2 else []
            n = self.node_at(f, _line_candidates(line, alts), name=name, want_class=False)
            if n is not None:
                return n
        return self.module.get(norm_path(f))

    def callee_nodes(self, row):
        out = []
        for d in row.get("defs") or []:
            if d.get("type") not in ("function", "class"):
                continue
            n = self.node_at(d.get("file"), _line_candidates(d.get("line"), d.get("alt_lines")),
                             name=d.get("name"), want_class=(d.get("type") == "class"))
            if n is not None and n not in out:
                out.append(n)
        return out


def best_form(forms) -> str:
    for f in FORM_ORDER:
        if f in forms:
            return f
    return "other"


def truth_pairs(truth, nodes, prefixes=None):
    """(T, by_caller_name, stats): T = {(caller_key, callee_key): set(forms)} for every in-scope call site the
    resolver bound to an in-repo function/class with a graph node; by_caller_name = {(caller_key, last name):
    set(callee keys)} over the same resolved sites (the refuted test); stats = a Counter of what was dropped."""
    loc = GraphLocator(nodes)
    T = defaultdict(set)
    by_cn = defaultdict(set)
    stats = Counter()
    for r in truth.get("rows") or []:
        if not in_prefixes(r.get("file"), prefixes):
            continue
        stats["sites"] += 1
        caller = loc.caller_of(r)
        if caller is None:
            stats["caller_unmapped"] += 1
            continue
        if not r.get("defs"):
            stats["unresolved_or_external"] += 1
            continue
        stats["resolved_in_repo"] += 1
        tgts = loc.callee_nodes(r)
        if not tgts:
            stats["callee_unmapped"] += 1
            continue
        for n in tgts:
            form ="ctor" if n.get("label") == "Class" else (r.get("form") or "other")
            T[(caller["key"], n["key"])].add(form)
            by_cn[(caller["key"], last_segment(r.get("name")))].add(n["key"])
    return T, by_cn, stats


def classify_pair(pair, T, by_cn, name_of) -> str:
    """'confirmed' (the resolver produced the pair), 'refuted' (the caller has resolved sites of that callee
    NAME, all bound to other nodes), else 'unverified'."""
    if pair in T:
        return "confirmed"
    a, b = pair
    got = by_cn.get((a, last_segment(name_of.get(b))))
    return "refuted" if got and b not in got else "unverified"


def truth_as_dump(truth, dump) -> dict:
    """The resolver's own pairs as a dump over `dump`'s nodes (grade 'TRUTH') — the reference graph for
    impact_spot.py and the ceiling for impact_offline.py."""
    T, _b, _s = truth_pairs(truth, dump.get("nodes") or [])
    return {"schema": DUMP_SCHEMA, "nodes": dump.get("nodes") or [],
            "edges": sorted([a, b, "TRUTH"] for (a, b) in T), "flags": {"truth": (truth.get("resolver") or {})}}


def env_flags(environ=None) -> dict:
    e = os.environ if environ is None else environ
    return {k: v for k, v in sorted(e.items()) if k.startswith("DEEPMAP_") and k not in RUN_SETTINGS}


# DEEPMAP_* variables that locate or pace a run but never change the graph (kept by variants.child_env).
RUN_SETTINGS = ("DEEPMAP_HOME", "DEEPMAP_PYTHON", "DEEPMAP_GRAPHIFY_TIMEOUT")
