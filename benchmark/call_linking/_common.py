"""Shared helpers for the call-linking measurement kit (benchmark/call_linking/). Stdlib only.

Measurement code, never product code: nothing under build/ imports this, and nothing here is shipped
(requirements.txt / sbom / NOTICE never name the reference resolvers this kit drives — jedi, TypeScript).

The dump format (what offline_calls.py writes and every scorer here reads):
    {"schema": "call-linking/offline-calls/1", "nodes": [{key, label, name, source_file, start_line,
     end_line}], "edges": [[caller_key, callee_key, grade], ...], ...}
The truth format (truth_jedi.py / truth_ts.js):
    {"schema": "call-linking/truth/1", "resolver": {...}, "rows": [{file, line, col, form, name,
     defstack: [[qualname, line, [alt lines]]...], defs: [{file, line, alt_lines, type, name}]}]}
A DISPATCH truth (truth_roslyn.py, D3-cs: benchmark/call_linking/PREREG_CS.md §8.2) is that row plus a per-row
`dispatch` list, entries of exactly the `defs` shape: the site's runtime targets beyond its static binding
(implementations of an interface member, overrides of a virtual / abstract member). `defs` stays the STATIC binding.
has_dispatch() tells the two apart; a truth without `dispatch` is scored exactly as before (truth_pairs /
classify_pair), and a dispatch truth adds T_d and the §8.5 classification (truth_pairs_cs / classify_pair_cs).
The row fields the C# scoring reads (PREREG_CS AC5): every row carries `dispatch` (a list, empty when there is none);
a row with dispatch targets carries `dispatch_rule` (`interface` or `override`); every def / dispatch entry carries
`interface` (true when it is an interface member). G4c and the fallback stratum read `dispatch_rule` for a row with
dispatch targets, else the defs' `interface` (_static_iface, static_dispatchable).
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
# D3-G1's receiver forms per family (design §8.5): py = jedi name_attr + recv; js = tsc name_attr + recv + this;
# cs = Roslyn name_attr + recv + this (PREREG_CS §8.5: js's set — C# has `this.` like TypeScript, and `base.`, the
# kit's `super`, stays out as js leaves `super` out). FORM_ORDER already holds C#'s `ctor` and `other`.
RECEIVER_FORMS = {"py": ("name_attr", "recv"), "js": ("name_attr", "recv", "this"),
                  "cs": ("name_attr", "recv", "this")}
# The verdict of a graph edge that is a dispatch pair (T_d) of a dispatch truth (PREREG_CS §8.5).
DISPATCH_CONFIRMED = "dispatch-confirmed"


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


def load_truth(path):
    """A truth file for a CLI: load_json + check_truth_shape; a malformed truth exits 2 with the reason."""
    truth = load_json(path)
    try:
        check_truth_shape(truth)
    except ValueError as exc:
        import sys
        print(f"[call_linking] REFUSED {path}: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
    return truth


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


def is_cs_path(source_file) -> bool:
    """A C# source file: the name ends in `.cs`, compared case-insensitively (PREREG_CS §8.6 N: "does not end in
    `.cs` (case-insensitive)")."""
    return norm_path(source_file).lower().endswith(".cs")


def caller_class(source_file) -> str:
    """'test' | 'nonprod' | 'prod' — the §2.6 caller policy classes (test wins over nonprod).

    A `.cs` file is classified by the product's own rules and nothing else (PREREG_CS §8.5 "A non-test caller"), read
    from build/ at call time, never copied here: sync._is_test_path has no C# FILE-name convention, so a C# file is test
    code only by a DIRECTORY component — a test-directory name (`tests/`, `test/`, `testing/` ... in any letter case),
    or a .NET test-project directory whose name ENDS with one of sync._DOTNET_TEST_DIR_SUFFIXES (`Shop.Tests/`,
    `Ordering.UnitTests/`, `Api.IntegrationTests/` ..., case-insensitive; both C# corpora keep their test projects
    under `tests/`, so the two rules agree there) — and non-production by typed_calls.is_nonprod_path's directory rule.
    A `FooTests.cs` outside such a directory is production here exactly as it is for the product (the typed-calls
    caller policy, the dispatch truth's test-file exclusion and G2d all read this one rule; if the product's rule moves,
    this follows it with no edit)."""
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


# ---- PREREG_CS §1 / AC7: the kit hash and the reference tool identity ------------------------------------------------
# What the kit hash leaves out (PREREG_CS §1: "`__pycache__/`, build outputs, RESULTS_CS.md, DECISION_CS.md and `cs/`
# excluded"), by forward-slash path relative to benchmark/call_linking/: any `__pycache__` directory component; the
# .NET tool's build outputs truth_roslyn/bin/ and truth_roslyn/obj/; the two written-once records; the G5b folder cs/.
KIT_HASH_EXCLUDED_DIRS = ("truth_roslyn/bin", "truth_roslyn/obj", "cs")
KIT_HASH_EXCLUDED_FILES = ("RESULTS_CS.md", "DECISION_CS.md")
# AC7's reference tool identity: the §1 scheme over these files (`truth_roslyn.py identity`), plus the versions a
# truth's `resolver` block prints (REFERENCE_VERSION_FIELDS).
REFERENCE_IDENTITY_FILES = ("truth_roslyn.py", "truth_roslyn/Program.cs", "truth_roslyn/TruthRoslyn.csproj",
                            "truth_roslyn/global.json", "truth_roslyn/nuget.config")
REFERENCE_VERSION_FIELDS = ("name", "microsoft_codeanalysis", "microsoft_codeanalysis_csharp", "packages", "sdk",
                            "runtime", "language_version", "tool_sources_sha256")


def _path_hash(root: Path, rels) -> str:
    """§1's scheme: sha256 over `rels` in byte order of their forward-slash relative paths, each contributing its
    path (UTF-8), a NUL, its bytes and a NUL."""
    h = hashlib.sha256()
    for rel in sorted(rels, key=lambda r: r.encode("utf-8")):
        h.update(rel.encode("utf-8") + b"\0")
        h.update((root / rel).read_bytes() + b"\0")
    return h.hexdigest()


def kit_hash_excluded(rel: str) -> "str | None":
    """Why the kit-relative forward-slash path `rel` is outside the kit hash (PREREG_CS §1 / AC7), or None."""
    parts = rel.split("/")
    if "__pycache__" in parts[:-1]:
        return "__pycache__/"
    for d in KIT_HASH_EXCLUDED_DIRS:
        if rel == d or rel.startswith(d + "/"):
            return d + "/"
    if rel in KIT_HASH_EXCLUDED_FILES:
        return rel
    return None


def kit_files(kit_dir=None) -> "tuple[list, dict]":
    """(the kit-relative forward-slash paths the kit hash covers, {exclusion: count}) — every regular file under the kit
    directory, sub-directories included, minus kit_hash_excluded. A symbolic link is refused (ValueError): its bytes
    would be whatever it points at."""
    root = Path(kit_dir or KIT)
    out, excluded = [], Counter()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        here = Path(dirpath)
        for dn in list(dirnames):
            if (here / dn).is_symlink():
                raise ValueError(f"the kit holds a symbolic link {(here / dn).relative_to(root).as_posix()!r}")
        for fn in filenames:
            p = here / fn
            rel = p.relative_to(root).as_posix()
            if p.is_symlink():
                raise ValueError(f"the kit holds a symbolic link {rel!r}")
            why = kit_hash_excluded(rel)
            if why:
                excluded[why] += 1
            elif p.is_file():
                out.append(rel)
    return sorted(out, key=lambda r: r.encode("utf-8")), dict(sorted(excluded.items()))


def kit_hash(kit_dir=None) -> dict:
    """PREREG_CS §1 "Kit hash" (AC7: `offline_calls.py kithash`): sha256 over every file of benchmark/call_linking/
    (truth_roslyn/ included; __pycache__/, truth_roslyn/bin/, truth_roslyn/obj/, RESULTS_CS.md, DECISION_CS.md and
    cs/ excluded), in byte order of their forward-slash relative paths, each contributing its relative path, a NUL,
    its bytes and a NUL. Run it on the RC snapshot: PREREG_CS.md is a kit file, so filling §1's slots in the
    repository's copy changes that copy's hash, never the snapshot's."""
    root = Path(kit_dir or KIT)
    files, excluded = kit_files(root)
    return {"sha256": _path_hash(root, files), "files": len(files), "excluded": excluded}


def reference_identity(kit_dir=None) -> dict:
    """AC7's reference tool identity: the §1 scheme over REFERENCE_IDENTITY_FILES (truth_roslyn.py and
    truth_roslyn/{Program.cs, TruthRoslyn.csproj, global.json, nuget.config}), relative to the kit directory — the
    hash `truth_roslyn.py identity` prints. The versions are a truth's resolver block (reference_versions)."""
    root = Path(kit_dir or KIT)
    missing = [r for r in REFERENCE_IDENTITY_FILES if not (root / r).is_file()]
    if missing:
        raise FileNotFoundError(f"the reference tool's sources are missing: {missing}")
    return {"sha256": _path_hash(root, REFERENCE_IDENTITY_FILES), "files": list(REFERENCE_IDENTITY_FILES)}


def reference_versions(truth) -> dict:
    """The versions a truth's `resolver` block records (AC7: "plus the versions its `resolver` block prints"): the
    Microsoft.CodeAnalysis versions and restored packages, the SDK, the runtime, the language version, the reference
    packs (pack, version, sha256) and the tool-source hash the run was built from."""
    res = (truth or {}).get("resolver") or {}
    out = {k: res.get(k) for k in REFERENCE_VERSION_FIELDS}
    out["packs"] = [{k: p.get(k) for k in ("pack", "version", "sha256")} for p in (res.get("packs") or [])
                    if isinstance(p, dict)]
    return out


def typed_default_langs(build_dir=None) -> list:
    """The RC tree's build/typed_calls._TYPED_DEFAULT_LANGS (lower-cased, sorted), imported from `build_dir` (default
    BUILD) under a private module name with that directory first on sys.path, both restored after — whatever
    expression defines it is what counts. Raises when the module or the name cannot be read (fail closed)."""
    import importlib.util
    import sys
    d = Path(build_dir or BUILD).resolve()
    name = f"_cl_typed_calls_defaults_{os.getpid()}_{id(d)}"
    spec = importlib.util.spec_from_file_location(name, d / "typed_calls.py")
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {d / 'typed_calls.py'}")
    mod = importlib.util.module_from_spec(spec)
    saved, saved_dwb = list(sys.path), sys.dont_write_bytecode
    sys.modules[name] = mod
    try:
        sys.path.insert(0, str(d))
        sys.dont_write_bytecode = True        # a read of the RC tree writes nothing into it (no __pycache__)
        spec.loader.exec_module(mod)
        return sorted({str(x).strip().lower() for x in mod._TYPED_DEFAULT_LANGS})
    finally:
        sys.path[:] = saved
        sys.dont_write_bytecode = saved_dwb
        sys.modules.pop(name, None)


# ---- PREREG_CS §8.7 G5b / AC15: the committed effect diff's manifest -------------------------------------------------
G5B_GITATTRIBUTES = "* -text\n"          # AC15: cs/.gitattributes, listed in the manifest (a Windows checkout keeps bytes)
TRUTH_OR_DUMP_SCHEMAS = ("call-linking/truth/1", "call-linking/offline-calls/1", "call-linking/roslyn-sites/2")


def g5b_manifest(cs_dir, virtual=None) -> "tuple[str, list]":
    """(the manifest text, problems) for the folder benchmark/call_linking/cs/ (PREREG_CS §8.7 G5b, AC15): one
    `<sha256>  <path relative to cs/>` line per file, forward slashes, in byte order of the paths' UTF-8 bytes, LF,
    never the manifest itself or any `sheet.md`. `virtual` ({relative path: bytes}) lists files not written yet (the
    .gitattributes a dry run would place), each hashed as given. Problems (the manifest is then not to be written): a
    sheet.md in the folder (it quotes corpus source and is never committed), a dump or truth file (never committed:
    their sha256 go in RESULTS_CS.md), a symbolic link, cs/.gitattributes holding anything but `* -text`, or no file
    at all."""
    cs = Path(cs_dir)
    problems, rels = [], []
    virtual = dict(virtual or {})
    if not cs.is_dir():
        return "", [f"{cs} is not a directory"]
    for dirpath, dirnames, filenames in os.walk(cs, followlinks=False):
        here = Path(dirpath)
        for dn in dirnames:
            if (here / dn).is_symlink():
                problems.append(f"{(here / dn).relative_to(cs).as_posix()} is a symbolic link")
        for fn in filenames:
            p = here / fn
            rel = p.relative_to(cs).as_posix()
            if p.is_symlink():
                problems.append(f"{rel} is a symbolic link")
                continue
            if rel == "MANIFEST.sha256":
                continue
            if any(ch in rel for ch in "\r\n\\"):
                problems.append(f"{rel!r}: a path holding a line break or a backslash cannot be a manifest line")
                continue
            if fn.lower() == "sheet.md":
                problems.append(f"{rel} is a sheet.md (never committed: it quotes corpus source; it re-creates from "
                                f"the seed and the committed inputs)")
                continue
            if fn.lower().endswith(".json"):
                try:
                    schema = json.loads(p.read_text(encoding="utf-8")).get("schema")
                except Exception:  # noqa: BLE001 — not JSON / not an object: not a dump or a truth
                    schema = None
                if schema in TRUTH_OR_DUMP_SCHEMAS:
                    problems.append(f"{rel} is a {schema} file (dumps and truth files are never committed: their "
                                    f"sha256 go in RESULTS_CS.md)")
                    continue
            rels.append(rel)
    for rel in virtual:
        if rel not in rels:
            rels.append(rel)
    ga = cs / ".gitattributes"
    ga_bytes = virtual.get(".gitattributes", ga.read_bytes() if ga.is_file() else None)
    if ga_bytes is not None and ga_bytes != G5B_GITATTRIBUTES.encode("ascii"):
        problems.append(f"cs/.gitattributes holds {ga_bytes[:80]!r}, not {G5B_GITATTRIBUTES!r}")
    if not [r for r in rels if r != ".gitattributes"]:
        problems.append("cs/ holds no file to list (only the manifest's own .gitattributes)")
    rels.sort(key=lambda r: r.encode("utf-8"))

    def digest(r):
        return hashlib.sha256(virtual[r] if r in virtual else (cs / r).read_bytes()).hexdigest()
    text = "".join(f"{digest(r)}  {r}\n" for r in rels)
    return text, problems


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
        """The nodes of the row's STATIC binding (`defs`)."""
        return self.def_nodes(row.get("defs"))

    def dispatch_nodes(self, row):
        """The nodes of the row's dispatch targets (`dispatch`, a dispatch truth's entries of the `defs` shape)."""
        return self.def_nodes(row.get("dispatch"))

    def def_nodes(self, defs):
        """Each definition mapped to the node on its line or one of its alt lines (in order, deduplicated)."""
        out = []
        for d in defs or []:
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
    check_truth_shape(truth)
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
    NAME, all bound to other nodes), else 'unverified'. A "resolved site" is one bound to an in-repo definition that
    MAPS TO A GRAPH NODE (by_cn holds only mapped nodes): a site bound outside the corpus, or to a definition with no
    node, resolves nothing, so an edge from its caller is unverified, never refuted (PREREG_CS AC6 states the same rule
    for the dispatch truth, "exactly as for jedi and tsc")."""
    if pair in T:
        return "confirmed"
    a, b = pair
    got = by_cn.get((a, last_segment(name_of.get(b))))
    return "refuted" if got and b not in got else "unverified"


# ---- the dispatch truth (D3-cs, PREREG_CS §8.5) --------------------------------------------------------------------
def has_dispatch(truth) -> bool:
    """True for a dispatch truth: some row carries a `dispatch` key (truth_roslyn.py writes one on every row, an
    empty list for a site without dispatch targets). jedi and tsc truths carry none and are scored as before."""
    return any(isinstance(r, dict) and "dispatch" in r for r in ((truth or {}).get("rows") or []))


def check_truth_shape(truth) -> None:
    """Refuse (ValueError) a truth the kit would score by the wrong rules: C# call sites without the per-row
    `dispatch` lists of the D3-cs contract (they would be scored static-only, silently), or a dispatch truth whose
    rows do not ALL carry `dispatch` (a row without it would read as "no dispatch target"). A jedi / tsc truth has
    no `.cs` row and no `dispatch` key, and passes unchanged."""
    rows = [r for r in ((truth or {}).get("rows") or []) if isinstance(r, dict)]
    with_d = sum(1 for r in rows if "dispatch" in r)
    if with_d and with_d != len(rows):
        raise ValueError(f"a dispatch truth must carry `dispatch` on every row: {len(rows) - with_d} of {len(rows)} "
                         f"rows have none")
    if not with_d and any(is_cs_path(r.get("file")) for r in rows):
        raise ValueError("a C# truth without per-row `dispatch` lists (PREREG_CS §8.2's contract): it would be scored "
                         "by the static-only rules")


# The dispatch rules a row with dispatch targets names in `dispatch_rule` (PREREG_CS §8.2 `dispatch`, AC5): the
# interface rule (S a non-static interface member) and the override rule (S a virtual, abstract or override class
# member called through any receiver but `base.`).
DISPATCH_RULES = ("interface", "override")


def _defs_interface(row) -> "bool | None":
    """The static defs' `interface` field (AC5: every def carries it, true for an interface member): True if any def
    says so, False if every def says not, None when the row has no def (an external static target: the reference
    gives a symbol declared outside the corpus no def) or a def lacks the boolean field."""
    defs = [d for d in (row.get("defs") or []) if isinstance(d, dict)]
    if not defs:
        return None
    flags = [d.get("interface") for d in defs]
    if any(f is True for f in flags):
        return True
    return False if all(isinstance(f, bool) for f in flags) else None


def _static_iface(row) -> "bool | None":
    """Whether the site's STATIC binding is an interface member (G4c: "a's site of that name binds statically to an
    interface member"), read through PREREG_CS AC5's fields: a row WITH dispatch targets answers by its own
    `dispatch_rule` (`interface` -> True, `override` -> False; any other value -> None: the row breaks AC5), so a
    static target declared outside the corpus (IDisposable.Dispose: no def) still says which rule applied; a row
    without dispatch targets answers by its defs' `interface` (_defs_interface; None without a def)."""
    if row.get("dispatch"):
        rule = row.get("dispatch_rule")
        return (rule == "interface") if rule in DISPATCH_RULES else None
    return _defs_interface(row)


def static_dispatchable(row) -> "bool | None":
    """PREREG_CS §8.5's fallback stratum as AC9 widens it: whether the site's static target is "an interface, virtual,
    abstract or override member", read through AC5's fields exactly as _static_iface reads G4c's: a row WITH dispatch
    targets answers True for either dispatch rule (`interface` or `override`; any other value -> None), a row without
    dispatch targets answers by its defs' `interface`. A virtual member with no override in the corpus therefore
    reads as not dispatchable (AC5 names no virtual field; the row's dispatch list is empty, so no implementer or
    override edge can be dispatch-confirmed from it)."""
    if row.get("dispatch"):
        return True if row.get("dispatch_rule") in DISPATCH_RULES else None
    return _defs_interface(row)


def truth_pairs_cs(truth, nodes, prefixes=None) -> dict:
    """PREREG_CS §8.5 over a dispatch truth, mapped by the same GraphLocator as truth_pairs (the innermost defstack
    entry that has a node is the caller, else the file's :Module; each def maps to the node on its line or an alt
    line). Returns a dict:
      Ts            {(caller, callee): set(forms)} from each row's `defs` — exactly truth_pairs' T (the static pairs);
      Td            {(caller, callee): set(forms)} from each row's `dispatch`, MINUS Ts (implementer / override pairs);
      by_cn         {(caller, last name): set(callee keys)} over each resolved site's static AND dispatch targets (the
                    §8.5 refuted test: "a has resolved sites (static or dispatch) of b's last name segment"). AC6:
                    a site is resolved when it is bound, statically or by dispatch, to a definition that MAPS TO A
                    GRAPH NODE — only mapped nodes enter by_cn, so a site bound only to a symbol outside the corpus
                    or to a definition with no node adds nothing, and an edge from its caller stays unverified,
                    never refuted, exactly as for jedi and tsc;
      by_cn_static  the same over the static targets only — truth_pairs' by_cn (the static-only classification);
      td_iface      {pair in Td: True | False | None}: whether a site that produced the dispatch pair binds statically
                    to an interface member (G4c, read through AC5's fields by _static_iface: the producing row's
                    `dispatch_rule`; True wins over False, None when no producing site says);
      iface_sites   {(caller, last name)} with at least one site of that name binding statically to an interface member
                    (_static_iface True: the row's `dispatch_rule` interface, else a def's `interface`), whether or not
                    that site produced any pair — AC24's count "over any same-named site of a", never decisive;
      stats         truth_pairs' counters (identical on the same rows) plus dispatch_sites (rows with a non-empty
                    `dispatch`), dispatch_defs (entries), dispatch_unmapped (such rows none of whose dispatch
                    entries maps to a node), dispatch_static_external (such rows without a static def: an interface
                    member declared outside the corpus that corpus types implement).
    A form is the row's (a :Class target is "ctor"), as truth_pairs assigns it; a pair counts under its first form in
    FORM_ORDER (best_form)."""
    check_truth_shape(truth)
    loc = GraphLocator(nodes)
    Ts, Dall = defaultdict(set), defaultdict(set)
    by_cn, by_s = defaultdict(set), defaultdict(set)
    iface, iface_sites = {}, set()
    stats = Counter()
    for r in truth.get("rows") or []:
        if not in_prefixes(r.get("file"), prefixes):
            continue
        stats["sites"] += 1
        caller = loc.caller_of(r)
        if caller is None:
            stats["caller_unmapped"] += 1
            continue
        ck, nm = caller["key"], last_segment(r.get("name"))
        if _static_iface(r) is True:
            iface_sites.add((ck, nm))
        disp = r.get("dispatch") or []
        if not r.get("defs"):
            stats["unresolved_or_external"] += 1
            if disp:
                stats["dispatch_static_external"] += 1
        else:
            stats["resolved_in_repo"] += 1
            tgts = loc.callee_nodes(r)
            if not tgts:
                stats["callee_unmapped"] += 1
            for n in tgts:
                Ts[(ck, n["key"])].add("ctor" if n.get("label") == "Class" else (r.get("form") or "other"))
                by_s[(ck, nm)].add(n["key"])
                by_cn[(ck, nm)].add(n["key"])
        if not disp:
            continue
        stats["dispatch_sites"] += 1
        stats["dispatch_defs"] += len(disp)
        dn = loc.dispatch_nodes(r)
        if not dn:
            stats["dispatch_unmapped"] += 1
        si = _static_iface(r)
        for n in dn:
            p = (ck, n["key"])
            Dall[p].add("ctor" if n.get("label") == "Class" else (r.get("form") or "other"))
            by_cn[(ck, nm)].add(n["key"])
            prev = iface.get(p, "unset")
            iface[p] = si if prev == "unset" else (True if True in (prev, si) else
                                                   (None if None in (prev, si) else False))
    Td = {p: f for p, f in Dall.items() if p not in Ts}
    return {"Ts": Ts, "Td": Td, "by_cn": by_cn, "by_cn_static": by_s,
            "td_iface": {p: iface.get(p) for p in Td}, "iface_sites": iface_sites, "stats": stats}


def classify_pair_cs(pair, Ts, Td, by_cn, name_of) -> str:
    """PREREG_CS §8.5: 'confirmed' (in T_s), 'dispatch-confirmed' (in T_d), 'refuted' (the caller has resolved sites
    — static or dispatch — of the callee's last name segment, and the pair is in neither set), else 'unverified'.
    `by_cn` is truth_pairs_cs' (static and dispatch targets). "Resolved" is AC6's: bound, statically or by dispatch,
    to a definition that maps to a graph node (by_cn holds mapped nodes only); a caller whose sites of that name are
    bound only outside the corpus, or only to definitions with no node, has no resolved site: unverified."""
    if pair in Ts:
        return "confirmed"
    if pair in Td:
        return DISPATCH_CONFIRMED
    a, b = pair
    return "refuted" if by_cn.get((a, last_segment(name_of.get(b)))) else "unverified"


def truth_as_dump(truth, dump) -> dict:
    """The resolver's own pairs as a dump over `dump`'s nodes (grade 'TRUTH') — the reference graph for
    impact_spot.py and the ceiling for impact_offline.py. For a dispatch truth the reference is T_s ∪ T_d
    (PREREG_CS §8.3: a spot's reference hop-1 callers hold a static or a dispatch pair); a T_d pair has grade
    'TRUTH_DISPATCH'."""
    nodes = dump.get("nodes") or []
    if has_dispatch(truth):
        P = truth_pairs_cs(truth, nodes)
        edges = sorted([[a, b, "TRUTH"] for (a, b) in P["Ts"]] + [[a, b, "TRUTH_DISPATCH"] for (a, b) in P["Td"]])
    else:
        T, _b, _s = truth_pairs(truth, nodes)
        edges = sorted([a, b, "TRUTH"] for (a, b) in T)
    return {"schema": DUMP_SCHEMA, "nodes": nodes, "edges": edges, "flags": {"truth": (truth.get("resolver") or {})}}


def env_flags(environ=None) -> dict:
    e = os.environ if environ is None else environ
    return {k: v for k, v in sorted(e.items()) if k.startswith("DEEPMAP_") and k not in RUN_SETTINGS}


# DEEPMAP_* variables that locate or pace a run but never change the graph (kept by variants.child_env).
RUN_SETTINGS = ("DEEPMAP_HOME", "DEEPMAP_PYTHON", "DEEPMAP_GRAPHIFY_TIMEOUT")
