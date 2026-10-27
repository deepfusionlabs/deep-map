#!/usr/bin/env python3
"""r283 F3 — framework_edges.py follow-ups: the route-prefix post-pass honours sync's exclusion rule, and the
ALLOWED_EDGE_TYPES CASCADES comment names every producer and the real orientation.

1. Route-prefix finalize (O5's cross-owner residual of ISS-build:022). `compose_route_prefixes`, the Python registry
   entry's `finalize`, found router mounts by reading every *.py under the root through its own `_iter_py`, which
   skipped only __pycache__/.venv. That included an in-tree venv/, a gitignored build/, a .worktrees/ checkout,
   dist/ and vendor/. Round 1 made the registry WALK honour sync's exclusion rule (sync._recognizer_exclusion), and
   language_recognizers.run hands that predicate to any finalize that declares an `exclude` keyword. This finalize
   declared none, so a sync still read the excluded trees (and parsed those holding a mount word), and they still
   took part in import resolution. That changed PROJECT results, not just I/O:
     * a `.worktrees/<branch>/app/main.py` that mounts the project's router through an absolute import added its
       own prefix to the project's route (`paths` gained '/v2/users/{uid}');
     * a gitignored `build/lib/` copy of a src-layout project made `from app.routers import users` ambiguous (two
       modules end in `.app.routers.users`), so the project's own mount was dropped and its route lost its '/v1'.
   Now `compose_route_prefixes(root, edges, exclude=None)` skips an excluded file before reading it, so the file is
   neither a mount site nor an import target. A predicate that raises excludes nothing (the post-pass never raises).
   With exclude=None (the legacy shell, a direct run(root)) only the static skip set applies, so registry/legacy
   parity without a predicate is unchanged.
2. Comment (from O7). The CASCADES comment named only Rails `dependent:` and EF OnDelete and read "persisted
   REVERSED: (owned)->(owner)". That is wrong for a GORM belongs-to, whose declaring struct is the dependent. It now
   names all four producers (Rails `dependent:`, EF `OnDelete`, JPA `CascadeType.REMOVE`, GORM `constraint:OnDelete`)
   and says each is emitted principal-first (a GORM belongs-to's principal is the type it references) and persisted
   (dependent)->(principal). Truth checks drive each producer and edge_resolve's writer.
3. Two citations in framework_edges.py's docstrings pointed at build/sync.py line numbers (:196, :233) that had
   drifted. They now name the symbols (`_rebuild_code`, `_iter_ast_files`), which are checked to exist.
4. Round 3 (completeness critic cov-build[0]): Claude Code keeps its worktrees at `.claude/worktrees/<name>/`, a full
   checkout of another branch. graphify prunes a `worktrees/` directory directly inside a dotted one, but sync's rule
   knew only the `.worktrees` name, so such a checkout still fed the walk and this post-pass: its main.py, mounting the
   project's router through an absolute import, added '/v2/users/{uid}' to the project's route, and its own copy of the
   router added a second ROUTES_TO. sync._recognizer_exclusion now mirrors the engine's rule, so that scenario composes
   exactly like the clean project, and DEEPMAP_INCLUDE_VENDOR=1 does not re-admit it.

Hermetic: temp trees; the real recognizers, sync's seam and edge_resolve's writer (against a recording fake session).
No Neo4j, no network, no ~/.deepmap.
Usage: python tests/f3_framework_edges_r283_gates.py [--root <tree>]
"""
from __future__ import annotations

import ast
import contextlib
import inspect
import io
import os
import pathlib
import re
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PASSES: list[str] = []
FAILS: list[str] = []

# A FastAPI project whose route gets its '/v1' from a mount in ANOTHER module, through an absolute import.
_USERS = ("from fastapi import APIRouter\n"
          "router = APIRouter(prefix='/users')\n"
          "@router.get('/{uid}')\n"
          "def get_user(uid): pass\n")
_MAIN = ("from fastapi import FastAPI\n"
         "from app.routers import users\n"
         "app = FastAPI()\n"
         "app.include_router(users.router, prefix='/v1')\n")
PROJECT = {"app/__init__.py": "", "app/routers/__init__.py": "", "app/routers/users.py": _USERS,
           "app/main.py": _MAIN}
SRC_PROJECT = {".gitignore": "# build output\nbuild/\n", **{f"src/{rel}": text for rel, text in PROJECT.items()}}
WANT = {"method": "GET", "path": "/v1/users/{uid}"}

# FastAPI's own source, trimmed: it holds the mount word `include_router` but mounts nothing resolvable.
_FASTAPI_SRC = ("class FastAPI:\n"
                "    def include_router(self, router, prefix=''):\n"
                "        self.router.include_router(router, prefix=prefix)\n")
_WORKTREE_MAIN = _MAIN.replace("'/v1'", "'/v2'")
_BUILD_LIB = {"build/lib/app/__init__.py": "", "build/lib/app/routers/__init__.py": "",
              "build/lib/app/routers/users.py": _USERS}
# Claude Code's worktree: a FULL checkout of another branch under .claude/worktrees/<name>/ (round 3)
_CLAUDE_WORKTREE = {**{f".claude/worktrees/feat/{rel}": text for rel, text in PROJECT.items()},
                    ".claude/worktrees/feat/app/main.py": _WORKTREE_MAIN}

# (what, the project's own files, the excluded files sync's rule must keep away from the post-pass)
SCENARIOS = (
    ("an in-tree venv/ holding FastAPI's own source (a mount word, nothing the project uses)", PROJECT,
     {"venv/lib/python3.12/site-packages/fastapi/applications.py": _FASTAPI_SRC}),
    ("a .worktrees/ checkout whose main.py mounts the PROJECT's router at '/v2' (absolute import)", PROJECT,
     {".worktrees/feat/app/main.py": _WORKTREE_MAIN}),
    ("a src-layout project + a gitignored build/lib/ copy (makes `app.routers.users` ambiguous)", SRC_PROJECT,
     _BUILD_LIB),
    ("a Claude Code .claude/worktrees/<name>/ full checkout whose main.py mounts the PROJECT's router at '/v2'",
     PROJECT, _CLAUDE_WORKTREE),
)


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSES if ok else FAILS).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok or not detail else f" — {str(detail)[:700]}"))


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


def _flags(**kv):
    base = {"DEEPMAP_INCLUDE_VENDOR": None, "DEEPMAP_EXCLUDE_TESTS": None, "DEEPMAP_RECOGNIZERS": None,
            "DEEPMAP_MERGE_SUMMARIES": None}
    base.update(kv)
    return _env(**base)


def _write(root: Path, files: dict) -> None:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")


@contextlib.contextmanager
def _record_reads():
    """Record every file pathlib reads as text (the registry walk's and the post-pass's reads)."""
    own = "read_text" in pathlib.Path.__dict__
    real = pathlib.Path.read_text
    seen: list = []

    def read_text(self, *a, **k):
        seen.append(self.as_posix())
        return real(self, *a, **k)

    pathlib.Path.read_text = read_text
    try:
        yield seen
    finally:
        if own:
            pathlib.Path.read_text = real
        else:
            del pathlib.Path.read_text


def _rels(reads, root: Path) -> list:
    base = root.as_posix().rstrip("/") + "/"
    return sorted({r[len(base):] for r in reads if r.startswith(base)})


def _quiet():
    return contextlib.redirect_stderr(io.StringIO())


def _routes(edges) -> list:
    return [(e.src_file, e.dst_name, e.attrs) for e in edges if e.edge_type == "ROUTES_TO"]


def _per_file(FE, root: Path, files: dict) -> list:
    """The per-file Python edges the registry hands the post-pass for `files` (sorted walk order)."""
    out: list = []
    for rel in sorted(r for r in files if r.lower().endswith(".py")):
        out.extend(FE.edges_from_module(ast.parse((root / rel).read_text(encoding="utf-8")), rel))
    return out


def _compose(FE, root: Path, edges: list, **kw):
    """compose_route_prefixes(root, edges, **kw) -> (result | None, root-relative reads, error | None)."""
    with _record_reads() as reads, _quiet():
        try:
            out = FE.compose_route_prefixes(root, edges, **kw)
            err = None
        except Exception as exc:  # noqa: BLE001 — a TypeError on the pre-fix signature is a FAIL, never a crash
            out, err = None, f"{type(exc).__name__}: {exc}"
    return out, _rels(reads, root), err


# =====================================================================================================
# A — the production seam (sync._apply_recognizers): excluded files are never read and change nothing
# =====================================================================================================
def gate_a_seam(S, FE) -> None:
    print("\n-- A: through sync's seam, an excluded tree is never read and never changes a project route --")
    for what, project, excluded in SCENARIOS:
        with tempfile.TemporaryDirectory() as td, _flags():
            clean, dirty = Path(td) / "clean", Path(td) / "dirty"
            _write(clean, project)
            _write(dirty, {**project, **excluded})
            with _quiet():
                clean_typed = S._apply_recognizers(clean, [])[1]
            with _record_reads() as reads, _quiet():
                dirty_typed = S._apply_recognizers(dirty, [])[1]
            read = _rels(reads, dirty)
            want = [(r, "get_user", WANT) for r in project if r.endswith("routers/users.py")]
            check(f"control ({what}): the clean project composes its cross-module '/v1' "
                  f"(== the legacy shell)", _routes(clean_typed) == want
                  and clean_typed == FE.extract_framework_edges(clean), _routes(clean_typed))
            check(f"{what}: no excluded file is read (neither the walk nor the post-pass)",
                  not set(excluded) & set(read), sorted(set(excluded) & set(read)))
            check(f"{what}: the project's route keeps exactly {WANT}", _routes(dirty_typed) == want,
                  _routes(dirty_typed))
            check(f"{what}: the typed edges equal the clean project's, edge for edge", dirty_typed == clean_typed,
                  f"{len(dirty_typed)} vs {len(clean_typed)}")

    # the post-pass follows the SAME rule as the walk, flags included: DEEPMAP_INCLUDE_VENDOR=1 re-admits a
    # gitignored dir (the engine keeps it then), never a dir DeepMap never treats as project source
    with tempfile.TemporaryDirectory() as td, _flags(DEEPMAP_INCLUDE_VENDOR="1"):
        root = Path(td) / "proj"
        files = {**SRC_PROJECT, **_BUILD_LIB, ".worktrees/feat/src/app/main.py": _WORKTREE_MAIN,
                 ".claude/worktrees/feat/src/app/main.py": _WORKTREE_MAIN}
        _write(root, files)
        pred = S._recognizer_exclusion(root)
        _out, read, err = _compose(FE, root, _per_file(FE, root, SRC_PROJECT), exclude=pred)
        check("DEEPMAP_INCLUDE_VENDOR=1: the post-pass reads the gitignored build/lib/ copy again, like the walk",
              err is None and "build/lib/app/routers/users.py" in read, err or read)
        check("... and still never reads .worktrees/ (never project source, whatever the flag)",
              err is None and ".worktrees/feat/src/app/main.py" not in read, err or read)
        check("... nor a Claude Code .claude/worktrees/ checkout (round 3)",
              err is None and ".claude/worktrees/feat/src/app/main.py" not in read, err or read)


# =====================================================================================================
# B — the post-pass API: exclude=None, the registry hands it the predicate, never raises, reads nothing excluded
# =====================================================================================================
def gate_b_api(S, FE, LR) -> None:
    print("\n-- B: compose_route_prefixes(root, edges, exclude=None) --")
    params = inspect.signature(FE.compose_route_prefixes).parameters
    check("compose_route_prefixes declares `exclude`, default None",
          "exclude" in params and params["exclude"].default is None, list(params))
    pred = lambda rel: rel.startswith(".worktrees/")  # noqa: E731
    check("control: the registry's Python entry finalizes with compose_route_prefixes",
          LR.PYTHON.finalize is FE.compose_route_prefixes, LR.PYTHON.finalize)
    handed = LR._finalize_kwargs(FE.compose_route_prefixes, pred)
    check("language_recognizers.run hands it the caller's predicate (_finalize_kwargs -> {'exclude': pred})",
          handed == {"exclude": pred}, handed)

    with tempfile.TemporaryDirectory() as td, _flags():
        root = Path(td) / "proj"
        excluded = {".worktrees/feat/app/main.py": _WORKTREE_MAIN,
                    "venv/lib/python3.12/site-packages/fastapi/applications.py": _FASTAPI_SRC}
        _write(root, {**PROJECT, **excluded})
        edges = _per_file(FE, root, PROJECT)
        project_py = sorted(r for r in PROJECT if r.endswith(".py"))
        all_py = sorted(project_py + list(excluded))

        out, read, err = _compose(FE, root, edges, exclude=S._recognizer_exclusion(root))
        check("direct call with sync's predicate: the route keeps exactly its own composition",
              err is None and _routes(out) == [("app/routers/users.py", "get_user", WANT)], err or _routes(out))
        check("... and it reads exactly the project's *.py (every one, nothing excluded)",
              err is None and read == project_py, err or read)

        seen: list = []

        def spy(rel):
            seen.append(rel)
            return False

        out_spy, read_spy, err = _compose(FE, root, edges, exclude=spy)
        check("the predicate is asked about every *.py, as root-relative posix paths (the walk's spelling)",
              err is None and sorted(set(seen)) == all_py, err or sorted(set(seen)))
        base, read_base, _e = _compose(FE, root, edges)
        check("a predicate that excludes nothing gives exactly the no-predicate result (same reads)",
              err is None and out_spy == base and read_spy == read_base, err or (_routes(out_spy), _routes(base)))
        check("control: without a predicate only the static skip set applies, so the .worktrees/ mount IS seen "
              "(the checks here really exercise the exclusion)",
              _routes(base) and _routes(base)[0][2].get("paths") == ["/v1/users/{uid}", "/v2/users/{uid}"],
              _routes(base))
        out_none, _r, err = _compose(FE, root, edges, exclude=None)
        check("exclude=None is byte-identical to the two-argument call", err is None and out_none == base,
              err or _routes(out_none))

        def boom(rel):
            raise ValueError("predicate crash")

        out_boom, read_boom, err = _compose(FE, root, edges, exclude=boom)
        check("a predicate that raises excludes nothing and never raises (composition is not skipped)",
              err is None and out_boom == base and read_boom == read_base, err or _routes(out_boom))

    with tempfile.TemporaryDirectory() as td, _flags():
        root = Path(td) / "proj"
        # the only mount word lives in an excluded file: nothing to compose -> the ORIGINAL list object
        _write(root, {"svc/a.py": "router = APIRouter(prefix='/p')\n@router.get('/x')\ndef x(): pass\n",
                      "venv/lib/fastapi/applications.py": _FASTAPI_SRC})
        edges = _per_file(FE, root, {"svc/a.py": ""})
        out, read, err = _compose(FE, root, edges, exclude=S._recognizer_exclusion(root))
        check("control: the only mount word is in an excluded file -> the ORIGINAL list object comes back",
              err is None and out is edges, err or type(out))
        check("... without reading the excluded file", err is None and "venv/lib/fastapi/applications.py" not in read,
              err or read)
        out, _read, err = _compose(FE, root, [], exclude=S._recognizer_exclusion(root))
        check("control: no edges -> [] back, with a predicate too", err is None and out == [], err or out)


# =====================================================================================================
# C — parity WITHOUT a predicate is unchanged (the registry/legacy contract is a property of run(root))
# =====================================================================================================
def gate_c_parity(FE, LR) -> None:
    print("\n-- C: without a predicate, the registry still equals the legacy shell --")
    for what, project, excluded in SCENARIOS:
        with tempfile.TemporaryDirectory() as td, _flags():
            root = Path(td) / "proj"
            _write(root, {**project, **excluded})
            with _quiet():
                supp, edges = LR.run(root)
                legacy = FE.extract_framework_edges(root)
            check(f"run(root) == extract_framework_edges(root), byte-equal ({what})",
                  edges == legacy and supp == [], f"{len(edges)} vs {len(legacy)}")


# =====================================================================================================
# D — the CASCADES vocabulary comment names every producer and the real orientation (+ truth checks)
# =====================================================================================================
_RUBY = "class Order < ApplicationRecord\n  has_many :line_items, dependent: :destroy\nend\n"
_CSHARP = ("public class ShopContext : DbContext\n{\n"
           "    protected override void OnModelCreating(ModelBuilder modelBuilder)\n    {\n"
           "        modelBuilder.Entity<Order>()\n            .HasMany(o => o.Lines)\n"
           "            .WithOne(l => l.Order)\n            .OnDelete(DeleteBehavior.Cascade);\n    }\n}\n")
_JAVA = ("package shop;\nimport jakarta.persistence.*;\nimport java.util.List;\n@Entity\npublic class Order {\n"
         "    @OneToMany(mappedBy = \"order\", cascade = CascadeType.REMOVE)\n    private List<LineItem> items;\n}\n")
_GO_BELONGS_TO = ("package models\n\ntype Company struct {\n\tID   uint\n\tName string\n}\n\n"
                  "type User struct {\n\tID        uint\n\tCompanyID uint\n"
                  "\tCompany   Company `gorm:\"constraint:OnDelete:CASCADE;\"`\n}\n")
_GO_HAS_MANY = ("package models\n\ntype Company struct {\n\tID    uint\n"
                "\tUsers []User `gorm:\"constraint:OnDelete:CASCADE;\"`\n}\n\n"
                "type User struct {\n\tID        uint\n\tCompanyID uint\n}\n")


def _cascades_comment(src: str) -> str:
    """The comment attached to the "CASCADES" entry of the ALLOWED_EDGE_TYPES literal: comment lines directly
    above it, its own trailing comment, and the comment lines that continue it (whitespace-normalized)."""
    lines = src.splitlines()
    try:
        start = next(i for i, ln in enumerate(lines) if ln.startswith("ALLOWED_EDGE_TYPES"))
        end = next(i for i in range(start, len(lines)) if lines[i].strip().startswith("}"))
        at = next(i for i in range(start, end) if lines[i].strip().startswith('"CASCADES"'))
    except StopIteration:
        return ""
    parts: list = []
    i = at - 1
    while i > start and lines[i].strip().startswith("#"):
        parts.insert(0, lines[i].strip()[1:])
        i -= 1
    own = lines[at].split("#", 1)
    parts.append(own[1] if len(own) == 2 else "")
    i = at + 1
    while i < end and lines[i].strip().startswith("#"):
        parts.append(lines[i].strip()[1:])
        i += 1
    return " ".join(" ".join(parts).split())


def gate_d_cascades_comment(build: Path, FE) -> None:
    print("\n-- D: the ALLOWED_EDGE_TYPES CASCADES comment says what the producers and the writer do --")
    text = _cascades_comment((build / "framework_edges.py").read_text(encoding="utf-8"))
    low = text.lower()
    for producer, words in (("Rails `dependent:` (ruby_rails_edges)", ("dependent:", "ruby_rails_edges")),
                            ("EF `OnDelete` (csharp_edges)", ("ondelete", "csharp_edges")),
                            ("JPA `CascadeType.REMOVE` (java_edges)", ("cascadetype.remove", "java_edges")),
                            ("GORM `constraint:OnDelete` (go_edges)", ("constraint:ondelete", "go_edges"))):
        check(f"the comment names {producer}", all(w in low for w in words), text)
    check("it states the persisted orientation (dependent)->(principal), no longer '(owned)->(owner)'",
          "(dependent)->(principal)" in low and "(owned)->(owner)" not in low, text)
    check("it says every producer emits principal-first, and a GORM belongs-to's principal is the referenced type",
          "principal-first" in low and "belongs-to" in low and "references" in low, text)
    check("control: the vocabulary itself is unchanged (13 types, CASCADES among them)",
          FE.ALLOWED_EDGE_TYPES == {"INHERITS", "IMPLEMENTS", "ROUTES_TO", "REFERENCES", "HAS_MANY", "DEPENDS_ON",
                                    "HOOKS", "SCHEDULED_AS", "TRIGGERS", "DISPATCHES", "IMPORTS", "CASCADES",
                                    "RENDERS"}, sorted(FE.ALLOWED_EDGE_TYPES))

    # truth: every named producer emits CASCADES with src = the principal (the model whose delete cascades)
    import csharp_edges as CS            # noqa: E402
    import edge_resolve as ER            # noqa: E402
    import go_edges as GO                # noqa: E402
    import java_edges as JV              # noqa: E402
    import ruby_rails_edges as RB        # noqa: E402

    def cascades(mod, source, rel):
        try:
            return [(e.src_name, e.dst_name) for e in mod.recognize(source, rel)[1] if e.edge_type == "CASCADES"]
        except Exception as exc:  # noqa: BLE001
            return f"{type(exc).__name__}: {exc}"

    for what, mod, source, rel, want_src in (
            ("Rails has_many ..., dependent: :destroy", RB, _RUBY, "app/models/order.rb", "Order"),
            ("EF Entity<Order>().HasMany(...).OnDelete(Cascade)", CS, _CSHARP, "Data/ShopContext.cs", "Order"),
            ("JPA @OneToMany(cascade = CascadeType.REMOVE)", JV, _JAVA, "src/main/java/shop/Order.java", "Order"),
            ("GORM has-many []User with constraint:OnDelete", GO, _GO_HAS_MANY, "models/company.go", "Company")):
        got = cascades(mod, source, rel)
        check(f"truth: {what} emits CASCADES principal-first (src {want_src})",
              isinstance(got, list) and len(got) == 1 and got[0][0] == want_src, got)
    got = cascades(GO, _GO_BELONGS_TO, "models/user.go")
    check("truth: a GORM belongs-to (User.Company with constraint:OnDelete) emits Company -> User: the principal is "
          "the referenced type, not the declaring struct", got == [("Company", "User")], got)

    # truth: edge_resolve persists a CASCADES row (src = principal) as (dependent)-[:CASCADES]->(principal)
    class _Session:
        def __init__(self):
            self.calls: list = []

        def run(self, cypher, **params):
            self.calls.append((cypher, params))

    nodes = [{"key": "models/user.go::Company:3", "label": "Class", "name": "Company",
              "source_file": "models/user.go", "start_line": 3},
             {"key": "models/user.go::User:8", "label": "Class", "name": "User", "source_file": "models/user.go",
              "start_line": 8}]
    sess = _Session()
    detail = ""
    ok = False
    try:
        ER.write_edge_rows(sess, [("CASCADES", nodes[0]["key"], nodes[1]["key"], "INFERRED", {})], ER.Resolver(nodes))
        (cypher, params), = sess.calls
        m = re.search(r"MERGE \((\w+)\)-\[\w*:CASCADES\]->\((\w+)\)", cypher)
        row = params["rows"][0]
        ok = bool(m) and row[m.group(1)] == nodes[1]["key"] and row[m.group(2)] == nodes[0]["key"]
        detail = cypher
    except Exception as exc:  # noqa: BLE001
        detail = f"{type(exc).__name__}: {exc} {sess.calls}"
    check("truth: edge_resolve.write_edge_rows persists (User)-[:CASCADES]->(Company): (dependent)->(principal)",
          ok, detail)
    check("truth: CASCADES is edge_resolve's only REVERSED type", ER.REVERSED_EDGE_TYPES == {"CASCADES"},
          ER.REVERSED_EDGE_TYPES)


# =====================================================================================================
# E — framework_edges.py cites sync.py by symbol (its two line citations had gone stale)
# =====================================================================================================
def _function_source(src: str, name: str):
    """The source of the module-level function `name` in `src`, or None."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(src, node)
    return None


def gate_e_citations(build: Path) -> None:
    print("\n-- E: framework_edges.py points into sync.py by symbol, not by line number --")
    fe = (build / "framework_edges.py").read_text(encoding="utf-8")
    sync_src = (build / "sync.py").read_text(encoding="utf-8")
    stale = re.findall(r"build/sync\.py:\d+", fe)
    check("no `build/sync.py:<line>` citation is left in framework_edges.py (they drifted: :196, :233)",
          stale == [], stale)
    for sym, what in (("_rebuild_code", "DEEPMAP_EXTRACTOR"), ("_iter_ast_files", '{"__pycache__", ".venv"}')):
        body = _function_source(sync_src, sym)
        check(f"it names sync.{sym}, which exists and holds what it is cited for ({what})",
              f"`{sym}`" in fe and body is not None and what in body, "missing" if body is None else sym)


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    root = REPO
    if "--root" in sys.argv:
        root = Path(sys.argv[sys.argv.index("--root") + 1]).resolve()
    build = root / "build"
    sys.path.insert(0, str(build))
    import framework_edges as FE         # noqa: E402
    import language_recognizers as LR    # noqa: E402
    import sync as S                     # noqa: E402
    print("=" * 72)
    print(f"DeepMap — r283 F3: the route-prefix post-pass honours sync's exclusion; CASCADES comment (root: {root})")
    print("=" * 72)
    gate_a_seam(S, FE)
    gate_b_api(S, FE, LR)
    gate_c_parity(FE, LR)
    gate_d_cascades_comment(build, FE)
    gate_e_citations(build)
    total = len(PASSES) + len(FAILS)
    print("\n" + "=" * 72)
    if FAILS:
        print(f"RESULT: RED — {len(PASSES)}/{total} gates passed.")
        return 1
    print(f"RESULT: GREEN — {len(PASSES)}/{total} gates passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
