#!/usr/bin/env python3
"""ISS-build:022 (D9) + D25 — vendored / third-party trees never reach the recognizer layer; edge_resolve's docs
say what the code does.

Finding (DeepInit refresh 2026-09-29, validated with an in-memory reproduction): sync's default-ON vendor /
.gitignore exclusion ran only on the ENGINE's output. `sync._apply_recognizers` filtered recognizer output for
test files only (DEEPMAP_EXCLUDE_TESTS), and the Python recognizer walked every *.py except __pycache__/.venv —
an in-tree `venv/`, `env/`, `vendor/`, `node_modules/`, gitignored dirs. graphify never extracts those
(it skips the virtualenv family; sync drops vendored nodes), so a declaring class there has no node of its
own and the GLOBAL_SRC_FALLBACK types (HAS_MANY / REFERENCES / CASCADES) resolved it onto a same-named class of
the PROJECT: a site-packages Django `Group.permissions = ManyToManyField(Permission)` became
(repo Group)-[:HAS_MANY]->(repo Permission), INFERRED — while edge_resolve's own comment said "the fallback
never fires" for Python/Rails. Every sync also ast-parsed the whole virtualenv.

Now sync hands the registry ONE exclusion rule (sync._recognizer_exclusion): sync's vendor rule — exactly what
it drops from the engine's output (vendor dirs, minified suffixes, the repo .gitignore's bare dirs), honouring
DEEPMAP_INCLUDE_VENDOR — plus the directories DeepMap never treats as project source whatever the flags
(graphify_extract._never_rescue: the virtualenv/env family incl. site-packages, dist/, caches, VCS and
engine-output dirs). The walk never reads such a file (so the Python walk no longer parses a virtualenv), and
supplemental nodes AND typed edges attributed to one are dropped from the output too, mirroring the test filter.
The Python-only path stays byte-identical (same nodes object, the legacy framework edges).
D25: edge_resolve's module docstring said push_to_neo4j calls push_typed_edges + push_imports; sync uses the
anchor-group API (edge_rows -> rows_by_anchor -> rewrite_anchored_edges / write_edge_rows). Also corrected:
the GLOBAL_SRC_FALLBACK comment, and _BROAD_IGNORE's drifted "verdict's ignore list" comment (a drift lock now
proves the production walk skips everything the verdict census ignores).

Round 3 (completeness critic cov-build[0]): graphify also prunes a `worktrees/` directory that sits DIRECTLY inside a
dotted one (detect._is_noise_dir's one parent-aware rule). That is where Claude Code keeps its worktrees —
`.claude/worktrees/<name>/`, a full checkout of another branch — but the seam's rule knew only the `.worktrees` NAME,
so those copies still reached every recognizer: the worktree's `Group.permissions` became (repo Group)-[:HAS_MANY]->
(repo Permission), INFERRED, through the global fallback, and the Ruby recognizer merged a second `Order` :Class
shell plus its edges. sync._recognizer_exclusion now mirrors that rule (at any depth, under any dotted parent,
case-insensitive, root-relative, whatever DEEPMAP_INCLUDE_VENDOR says), drift-locked against the installed engine.
cov-build[6]: the seam's docstrings said "the engine never extracts" the excluded dirs; the Python-only ast fallback
does walk them (their .py files are graph nodes there, yet never recognizer input), and now they say so.

Hermetic: temp trees, the real recognizers + edge_resolve, no Neo4j / network.
Usage: python tests/o5_recognizer_vendor_seam_r283_gates.py [--root <tree>]
"""
from __future__ import annotations

import contextlib
import io
import os
import pathlib
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PASSES: list[str] = []
FAILS: list[str] = []

_REPO_MODELS = '''from django.db import models


class Group(models.Model):
    name = models.CharField(max_length=80)


class Permission(models.Model):
    codename = models.CharField(max_length=100)


class ContentType(models.Model):
    model = models.CharField(max_length=100)
'''

# A third-party copy of django.contrib.auth.models, trimmed: the declarations the fallback mis-resolved.
_DJANGO_AUTH = '''from django.db import models
from django.contrib.contenttypes.models import ContentType


class Permission(models.Model):
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)


class Group(models.Model):
    permissions = models.ManyToManyField(Permission, blank=True)
'''

# Files the seam must never hand a recognizer, and why (the rule's two parts).
_EXCLUDED_PY = {
    "venv/lib/python3.12/site-packages/django/contrib/auth/models.py": "virtualenv (never project source)",
    "env/models.py": "env-family dir (never project source)",
    "tools_venv/models.py": "*_venv dir (never project source)",
    "build_env/models.py": "*_env dir (never project source)",
    "dist/pkg/models.py": "dist/ build output (never project source)",
    "vendor/acme/models.py": "vendor rule (sync._VENDOR_DIR_PARTS)",
    "node_modules/node-gyp/gyp/models.py": "vendor rule (sync._VENDOR_DIR_PARTS)",
    "third_party/lib/models.py": "vendor rule (a bare dir in the repo .gitignore)",
}
_FLAG_GOVERNED = {"vendor/acme/models.py", "node_modules/node-gyp/gyp/models.py", "third_party/lib/models.py"}


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


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _repo_nodes() -> list:
    """The ENGINE's nodes for the repo's own app/models.py (_REPO_MODELS)."""
    sf = "app/models.py"
    return [{"key": f"{sf}::module", "label": "Module", "name": "models", "source_file": sf, "start_line": 1,
             "documented": False, "body_summary": "models"}] + [
        {"key": f"{sf}::{name}:{line}", "label": "Class", "name": name, "source_file": sf, "start_line": line,
         "documented": False, "body_summary": name}
        for name, line in (("Group", 4), ("Permission", 8), ("ContentType", 12))]


def _django_tree(root: Path) -> list:
    """The repo's own models + third-party copies in every excluded location. Returns the ENGINE's nodes
    (only app/models.py: graphify never extracts the rest, and sync's vendor filter drops vendored files)."""
    _write(root, ".gitignore", "# local deps\nthird_party/\n")
    _write(root, "app/__init__.py", "")
    _write(root, "app/models.py", _REPO_MODELS)
    for rel in _EXCLUDED_PY:
        _write(root, rel, _DJANGO_AUTH)
    return _repo_nodes()


@contextlib.contextmanager
def _registry(LR, entries):
    saved = LR.REGISTRY
    LR.REGISTRY = tuple(entries)
    try:
        yield
    finally:
        LR.REGISTRY = saved


@contextlib.contextmanager
def _record_reads():
    """Record every file pathlib reads as text (the registry walker's read)."""
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


def _quiet():
    return contextlib.redirect_stderr(io.StringIO())


def _rel_hits(paths, rels) -> list:
    return sorted(r for r in rels if any(p.endswith("/" + r) or p == r for p in paths))


def gate_python_walk(S, LR) -> None:
    print("\n-- the Python walk (as sync drives it) never reads — let alone parses — an excluded tree --")
    reached: list = []

    def recording(source, rel):
        reached.append(rel)
        return LR.PYTHON.recognize(source, rel)

    entry = LR.PYTHON._replace(recognize=recording)
    with tempfile.TemporaryDirectory() as td, _flags(), _registry(LR, [entry]):
        root = Path(td) / "proj"
        base = _django_tree(root)
        with _record_reads() as reads, _quiet():
            S._apply_recognizers(root, base)
        check("the repo's own app/models.py still reaches the Python recognizer", "app/models.py" in reached,
              reached)
        for rel, why in _EXCLUDED_PY.items():
            check(f"never handed to the recognizer: {rel} — {why}", rel not in reached, reached)
        check("... and none of those files is even read (the walk skips them before reading)",
              _rel_hits(reads, _EXCLUDED_PY) == [], _rel_hits(reads, _EXCLUDED_PY))

    reached.clear()
    with tempfile.TemporaryDirectory() as td, _flags(DEEPMAP_INCLUDE_VENDOR="1"), _registry(LR, [entry]):
        root = Path(td) / "proj"
        base = _django_tree(root)
        with _quiet():
            S._apply_recognizers(root, base)
        for rel in sorted(_EXCLUDED_PY):
            if rel in _FLAG_GOVERNED:
                check(f"DEEPMAP_INCLUDE_VENDOR=1 is honoured: {rel} is walked again (the engine keeps it too)",
                      rel in reached, reached)
            else:
                check(f"DEEPMAP_INCLUDE_VENDOR=1 does not re-admit never-project-source dirs: {rel}",
                      rel not in reached, reached)

    reached.clear()
    with tempfile.TemporaryDirectory() as td, _flags(), _registry(LR, [entry]):
        root = Path(td) / "Site-Packages" / "venv" / "vendor" / "proj"   # excluded names ABOVE the repo root
        _django_tree(root)
        with _quiet():
            S._apply_recognizers(root, [])
        check("directory names ABOVE the repo root never exclude it (components are matched root-relative)",
              "app/models.py" in reached, reached)

    reached.clear()
    with tempfile.TemporaryDirectory() as td, _flags(), _registry(LR, [entry]):
        root = Path(td) / "proj"
        _write(root, "app/models.py", _REPO_MODELS)
        for rel in ("Lib/Site-Packages/pkg/models.py", "VENDOR/x/models.py", "My_VENV/models.py"):
            _write(root, rel, _DJANGO_AUTH)
        with _quiet():
            S._apply_recognizers(root, [])
        check("case-insensitive: Lib/Site-Packages/, VENDOR/ and My_VENV/ are excluded too",
              sorted(reached) == ["app/models.py"], sorted(reached))


def gate_no_fabricated_orm_edges(S, LR, ER, FE) -> None:
    print("\n-- the reported reproduction: no ORM edge is resolved onto the repo's classes from a third-party copy --")
    with tempfile.TemporaryDirectory() as td, _flags():
        root = Path(td) / "proj"
        base = _django_tree(root)
        with _quiet():
            merged, typed = S._apply_recognizers(root, base)
        bad = sorted({e.src_file for e in typed if not e.src_file.startswith("app/")})
        check("no typed edge declared in an excluded file reaches the resolver", bad == [], bad)
        live = [n["key"] for n in merged]
        rows = ER.edge_rows(typed, [], ER.Resolver(merged), live, set(live))
        orm = [r[:4] for r in rows if r[0] in ("HAS_MANY", "REFERENCES", "CASCADES")]
        check("no fabricated (repo Group)-[:HAS_MANY]->(repo Permission) / REFERENCES row (the repo declares none)",
              orm == [], orm)
        check("the Python-only merge keeps the SAME nodes object (byte-identity: no supplemental nodes)",
              merged is base)

    # control: a tree with nothing to exclude — typed edges == the legacy framework edges, same nodes object
    with tempfile.TemporaryDirectory() as td, _flags():
        root = Path(td) / "proj"
        _write(root, "app/models.py", _REPO_MODELS)
        _write(root, "app/api.py", "from flask import Flask\napp = Flask(__name__)\n\n\n"
                                   "@app.get('/items')\ndef read_items():\n    return []\n")
        base = [{"key": "k", "label": "Class", "name": "Group", "source_file": "app/models.py",
                 "start_line": 4, "documented": False, "body_summary": "Group"}]
        with _quiet():
            merged, typed = S._apply_recognizers(root, base)
        check("control: nothing excluded -> typed edges == framework_edges.extract_framework_edges(root)",
              typed == FE.extract_framework_edges(root) and len(typed) >= 2, len(typed))
        check("control: ... and the SAME nodes object comes back", merged is base)


def _rb_entry(LR, FE, recognize):
    return LR.RecognizerEntry(name="rbtest", extensions=frozenset({".rb"}), edge_types=frozenset({"HAS_MANY"}),
                              impact_edge_types=(), recognize=recognize, ignore_parts=LR._BROAD_IGNORE)


def _supp(name, sf):
    return {"key": f"{sf}::{name}:1", "label": "Class", "name": name, "source_file": sf, "start_line": 1,
            "documented": False, "body_summary": f"{name}: model"}


def gate_supplemental_and_output(S, LR, FE, V) -> None:
    print("\n-- supplemental nodes AND typed edges from excluded files are dropped (all recognizers) --")
    reached: list = []

    def per_file(source, rel):
        reached.append(rel)
        return [_supp("Order", rel)], [FE.TypedEdge("Order", rel, "LineItem", "HAS_MANY", "NAME", {})]

    files = ("app/models/order.rb", "public/legacy/order.rb", "coverage/lcov/order.rb",
             "bower_components/x/order.rb", "env/order.rb", ".worktrees/feat/app/models/order.rb")
    with tempfile.TemporaryDirectory() as td, _flags(), _registry(LR, [_rb_entry(LR, FE, per_file)]):
        root = Path(td) / "proj"
        for rel in files:
            _write(root, rel, "class Order\nend\n")
        with _quiet():
            merged, typed = S._apply_recognizers(root, [])
        check("only app/models/order.rb is walked (public/, coverage/, bower_components/, env/, .worktrees/ are not)",
              reached == ["app/models/order.rb"], reached)
        check("... so only its supplemental node is merged", [n["source_file"] for n in merged]
              == ["app/models/order.rb"], [n["source_file"] for n in merged])
        check("... and only its typed edge survives", [e.src_file for e in typed] == ["app/models/order.rb"],
              [e.src_file for e in typed])

    reached.clear()
    with tempfile.TemporaryDirectory() as td, _flags(DEEPMAP_INCLUDE_VENDOR="1"), \
            _registry(LR, [_rb_entry(LR, FE, per_file)]):
        root = Path(td) / "proj"
        for rel in files:
            _write(root, rel, "class Order\nend\n")
        with _quiet():
            S._apply_recognizers(root, [])
        check("DEEPMAP_INCLUDE_VENDOR=1: public/, coverage/, bower_components/ are walked again; env/ and "
              ".worktrees/ are not", sorted(reached) == sorted(files[:4]), sorted(reached))

    # the OUTPUT filter on its own (mirrors the test filter): a recognizer attributing a node / edge to an
    # excluded file while walking a project file
    def attributing(source, rel):
        return ([_supp("Shim", "vendor/shim/x.rb"), _supp("Order", rel)],
                [FE.TypedEdge("Shim", "vendor/shim/x.rb", "Order", "HAS_MANY", "NAME", {}),
                 FE.TypedEdge("Order", rel, "LineItem", "HAS_MANY", "NAME", {})])

    with tempfile.TemporaryDirectory() as td, _registry(LR, [_rb_entry(LR, FE, attributing)]):
        root = Path(td) / "proj"
        _write(root, "app/x.rb", "class Order\nend\n")
        for flag, expect in ((None, ["app/x.rb"]), ("1", ["app/x.rb", "vendor/shim/x.rb"])):
            with _flags(DEEPMAP_INCLUDE_VENDOR=flag), _quiet():
                merged, typed = S._apply_recognizers(root, [])
            what = "default" if flag is None else "DEEPMAP_INCLUDE_VENDOR=1"
            check(f"output filter ({what}): supplemental nodes -> {expect}",
                  sorted(n["source_file"] for n in merged) == expect, sorted(n["source_file"] for n in merged))
            check(f"output filter ({what}): typed edges -> {expect}",
                  sorted(e.src_file for e in typed) == expect, sorted(e.src_file for e in typed))

    # control: the DEEPMAP_EXCLUDE_TESTS output filter is unchanged
    def tests_too(source, rel):
        return [_supp("OrderSpec", "spec/models/order_spec.rb"), _supp("Order", rel)], []

    with tempfile.TemporaryDirectory() as td, _flags(DEEPMAP_EXCLUDE_TESTS="1"), \
            _registry(LR, [_rb_entry(LR, FE, tests_too)]):
        root = Path(td) / "proj"
        _write(root, "app/models/order.rb", "class Order\nend\n")
        with _quiet():
            merged, _ = S._apply_recognizers(root, [])
        check("control: DEEPMAP_EXCLUDE_TESTS still drops a supplemental test node",
              [n["name"] for n in merged] == ["Order"], [n["name"] for n in merged])

    # drift lock for the corrected _BROAD_IGNORE comment: in production (sync's seam, default flags) every
    # non-Python entry skips every directory the verdict census ignores, whether through its static floor
    # (_BROAD_IGNORE) or sync's exclusion rule.
    reached.clear()
    ignored = sorted(V._IGNORE_PARTS)
    with tempfile.TemporaryDirectory() as td, _flags(), _registry(LR, [_rb_entry(LR, FE, per_file)]):
        root = Path(td) / "proj"
        _write(root, "app/models/order.rb", "class Order\nend\n")
        for part in ignored:
            _write(root, f"{part}/sub/order.rb", "class Order\nend\n")
        with _quiet():
            S._apply_recognizers(root, [])
        leaked = sorted({r.split("/")[0] for r in reached} - {"app"})
        check(f"drift lock: the production walk skips all {len(ignored)} dirs verdict._IGNORE_PARTS ignores",
              leaked == [], leaked)


# --- round 3 (cov-build[0]): Claude Code's worktree checkouts ---------------------------------------------------------
_CLAUDE_WT = ".claude/worktrees/feat"
_RB_ORDER = "class Order < ApplicationRecord\n  has_many :line_items, dependent: :destroy\n  belongs_to :customer\nend\n"

# (root-relative path, excluded?, why). graphify prunes `worktrees` when its DIRECT parent's name starts with '.'.
_WORKTREE_RULE = (
    (f"{_CLAUDE_WT}/app/models.py", True, "Claude Code's worktree checkout"),
    ("src/.claude/worktrees/feat/app/models.py", True, "at any depth (a nested package's own .claude/)"),
    (".cursor/worktrees/feat/app/models.py", True, "under any dotted dir, like graphify's rule (not .claude only)"),
    (".claude/Worktrees/feat/app/models.py", True, "case-insensitive, like the rest of the rule"),
    (".git/worktrees/feat/app/models.py", True, "control: excluded before too (.git)"),
    (".worktrees/feat/app/models.py", True, "control: excluded before too (.worktrees, by name)"),
    ("worktrees/feat/app/models.py", False, "a top-level worktrees/ has no dotted parent: project source"),
    ("app/worktrees/models.py", False, "a project package named worktrees/ under a plain dir"),
    ("claude/worktrees/feat/models.py", False, "an undotted parent"),
    (".claude/tools/worktrees/feat/models.py", False, "the dotted dir must be the DIRECT parent (graphify's rule)"),
    ("./worktrees/feat/models.py", False, "a '.' path component is not a dotted directory"),
    (".claude/worktrees.py", False, "a FILE named worktrees.py is no directory component"),
    (".claude/hooks/models.py", False, "the rest of .claude/ stays walked (graphify walks it too)"),
)


def gate_claude_worktrees(S, LR, ER, GX) -> None:
    print("\n-- round 3: Claude Code's .claude/worktrees/<name>/ checkouts never reach a recognizer --")
    with tempfile.TemporaryDirectory() as td, _flags():
        pred = S._recognizer_exclusion(Path(td) / "proj")
        for rel, want, why in _WORKTREE_RULE:
            got = pred(rel)
            check(f"rule: {rel} is {'excluded' if want else 'kept'} — {why}", got == want, got)
    with tempfile.TemporaryDirectory() as td, _flags(DEEPMAP_INCLUDE_VENDOR="1"):
        got = S._recognizer_exclusion(Path(td) / "proj")(f"{_CLAUDE_WT}/app/models.py")
        check("DEEPMAP_INCLUDE_VENDOR=1 does not re-admit it (another checkout, never project source)", got is True, got)

    # the model edge (Python): another branch's models.py declares Group.permissions; the engine extracted only the
    # project's app/models.py, so the worktree's declaration had no node of its own
    with tempfile.TemporaryDirectory() as td, _flags():
        root = Path(td) / "proj"
        worktree_models = f"{_CLAUDE_WT}/app/models.py"
        for rel, text in (("app/__init__.py", ""), ("app/models.py", _REPO_MODELS),
                          (f"{_CLAUDE_WT}/app/__init__.py", ""), (worktree_models, _DJANGO_AUTH)):
            _write(root, rel, text)
        base = _repo_nodes()
        with _record_reads() as reads, _quiet():
            merged, typed = S._apply_recognizers(root, base)
        live = [n["key"] for n in merged]
        rows = ER.edge_rows(typed, [], ER.Resolver(merged), live, set(live))
        orm = [r[:4] for r in rows if r[0] in ("HAS_MANY", "REFERENCES", "CASCADES")]
        check("model edge: no (repo Group)-[:HAS_MANY]->(repo Permission) / REFERENCES row from the worktree's "
              "models.py", orm == [], orm)
        leaked = sorted({e.src_file for e in typed if e.src_file.startswith(".claude/")})
        check("... no typed edge declared under .claude/worktrees/ reaches the resolver", leaked == [], leaked)
        check("... the worktree copy is never even read", _rel_hits(reads, [worktree_models]) == [],
              _rel_hits(reads, [worktree_models]))
        check("... and the Python-only merge keeps the SAME nodes object", merged is base)

    # the model layer (the real Ruby recognizer; C#/Java/Go add supplemental nodes the same way): a full-copy model
    # under the worktree added a second Order :Class shell plus its INHERITS/HAS_MANY/CASCADES/REFERENCES edges
    with tempfile.TemporaryDirectory() as td, _flags(), _registry(LR, [LR.RUBY]):
        root = Path(td) / "proj"
        for rel in ("app/models/order.rb", f"{_CLAUDE_WT}/app/models/order.rb"):
            _write(root, rel, _RB_ORDER)
        with _quiet():
            merged, typed = S._apply_recognizers(root, [])
        got = [(n["source_file"], n["name"]) for n in merged]
        check("model layer (Ruby): the only supplemental node is the project's own Order",
              got == [("app/models/order.rb", "Order")], got)
        srcs = sorted({e.src_file for e in typed})
        check("... and every typed edge comes from app/models/order.rb (INHERITS/HAS_MANY/CASCADES/REFERENCES)",
              srcs == ["app/models/order.rb"] and len(typed) >= 4, (srcs, len(typed)))

    # drift lock: a lowercase worktrees/ below the root is excluded exactly where the installed engine's own skip rule
    # prunes it, and the rest of .claude/ is walked by both
    noise = GX._engine_noise_dir()
    if noise is None:
        print("     (graphify not installed — the drift lock against the engine's own skip rule needs it)")
        return
    with tempfile.TemporaryDirectory() as td, _flags():
        root = Path(td) / "proj"
        pred = S._recognizer_exclusion(root)
        disagree = []
        for parent in ("", ".claude", ".cursor", ".x.y", "src/.claude", ".git", "claude", "src", ".claude/tools"):
            rel = "/".join(p for p in (parent, "worktrees/feat/app/models.py") if p)
            engine = GX._is_engine_noise(noise, "worktrees", root.joinpath(*parent.split("/")) if parent else root)
            if pred(rel) != engine:
                disagree.append((rel, pred(rel), engine))
        check("drift lock: the seam excludes worktrees/ exactly where graphify's own skip rule prunes it",
              disagree == [], disagree)
        check("drift lock: graphify walks the rest of .claude/, and so does the seam",
              not GX._is_engine_noise(noise, ".claude", root) and not pred(".claude/hooks/models.py"))


def gate_walker_api(LR) -> None:
    print("\n-- language_recognizers: the walker takes the caller's exclusion (and never raises on it) --")
    seen: list = []
    entry = LR.RecognizerEntry(name="t", extensions=frozenset({".rb"}), edge_types=frozenset(),
                               impact_edge_types=(), recognize=lambda s, r: ([], []), ignore_parts=frozenset())
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write(root, "a/x.rb", "x")
        _write(root, "skip/y.rb", "y")
        try:
            got = [rel for rel, _ in LR.iter_source(root, entry, exclude=lambda rel: rel.startswith("skip/"))]
            check("iter_source(..., exclude=) yields only what the predicate keeps", got == ["a/x.rb"], got)
        except TypeError as exc:
            check("iter_source(..., exclude=) yields only what the predicate keeps", False, repr(exc))

        def boom(rel):
            raise ValueError("predicate crash")

        try:
            got = [rel for rel, _ in LR.iter_source(root, entry, exclude=boom)]
            check("a crashing exclusion predicate excludes nothing (the walk never raises)",
                  got == ["a/x.rb", "skip/y.rb"], got)
        except Exception as exc:  # noqa: BLE001
            check("a crashing exclusion predicate excludes nothing (the walk never raises)", False, repr(exc))

        def rec(source, rel):
            seen.append(rel)
            return [], []

        with _registry(LR, [entry._replace(recognize=rec)]), _flags():
            try:
                LR.run(root, exclude=lambda rel: rel.startswith("skip/"))
                check("run(root, exclude=) passes the predicate to the walk", seen == ["a/x.rb"], seen)
            except TypeError as exc:
                check("run(root, exclude=) passes the predicate to the walk", False, repr(exc))
            seen.clear()
            LR.run(root)
            check("run(root) with no predicate is unchanged (every file)", seen == ["a/x.rb", "skip/y.rb"], seen)

    # a finalize post-pass that reads the tree itself gets the same predicate when it declares `exclude`;
    # one that does not is called exactly as before (no TypeError, its edges kept)
    import framework_edges as FE
    handed: list = []

    def one_edge(source, rel):
        return [], [FE.TypedEdge("A", rel, "B", "INHERITS", "LOCAL", {})]

    def fin_kw(root, edges, exclude=None):
        handed.append(exclude)
        return edges

    def fin_plain(root, edges):
        handed.append("plain")
        return edges

    pred = lambda rel: rel.startswith("skip/")  # noqa: E731
    with tempfile.TemporaryDirectory() as td, _flags():
        root = Path(td)
        _write(root, "a/x.rb", "x")
        for fin, name in ((fin_kw, "declares exclude"), (fin_plain, "no exclude parameter")):
            handed.clear()
            with _registry(LR, [entry._replace(recognize=one_edge, finalize=fin)]):
                try:
                    _s, edges = LR.run(root, exclude=pred)
                except TypeError as exc:
                    edges = repr(exc)
            want = [pred] if fin is fin_kw else ["plain"]
            check(f"finalize ({name}) is called with {'the predicate' if fin is fin_kw else 'no extra argument'} "
                  f"and keeps its edges", handed == want and isinstance(edges, list) and len(edges) == 1,
                  (handed, edges))
        handed.clear()
        with _registry(LR, [entry._replace(recognize=one_edge, finalize=fin_kw)]):
            LR.run(root)
        check("run(root) with no predicate hands a finalize nothing (its default applies)", handed == [None],
              handed)


def gate_docs(build: Path, ER, S) -> None:
    print("\n-- D25 + the corrected comments say what the code does --")
    # round 3 (cov-build[6]): "the engine never extracts" those dirs is false for the Python-only ast fallback
    excl_doc = " ".join((S._recognizer_exclusion.__doc__ or "").split())
    apply_doc = " ".join((S._apply_recognizers.__doc__ or "").split())
    check("sync._recognizer_exclusion's docstring no longer says 'The engine never extracts them' and names the ast "
          "fallback that does walk them", "engine never extracts" not in excl_doc and "ast fallback" in excl_doc,
          excl_doc[:900])
    check("... nor does _apply_recognizers' docstring ('the engine never extracted them')",
          "engine never extracted" not in apply_doc, apply_doc[:700])
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "proj"
        walks = ["app/models.py", "env/models.py", "dist/pkg/models.py", ".worktrees/feat/app/models.py",
                 f"{_CLAUDE_WT}/app/models.py"]
        for rel in walks + [".venv/lib/models.py", "app/__pycache__/models.py"]:
            _write(root, rel, "x = 1\n")
        with _quiet():
            walked = sorted(rel for _py, rel in S._iter_ast_files(root))
        check("truth: the ast fallback does walk those dirs (all but .venv/ and __pycache__/), as the docstring says",
              walked == sorted(walks), walked)
    doc = " ".join((ER.__doc__ or "").split())
    check("edge_resolve's docstring no longer says push_to_neo4j calls push_typed_edges + push_imports",
          "calls push_typed_edges + push_imports" not in doc, doc[:400])
    check("... it names sync's real path: edge_rows -> rows_by_anchor -> rewrite_anchored_edges",
          all(w in doc for w in ("edge_rows", "rows_by_anchor", "rewrite_anchored_edges")), doc[:600])
    sync_src = (build / "sync.py").read_text(encoding="utf-8")
    check("truth: sync.py never calls push_typed_edges( / push_imports( and does call rewrite_anchored_edges(",
          "push_typed_edges(" not in sync_src and "push_imports(" not in sync_src
          and "rewrite_anchored_edges(" in sync_src)
    er_src = (build / "edge_resolve.py").read_text(encoding="utf-8")
    check("edge_resolve's GLOBAL_SRC_FALLBACK comment no longer claims the fallback never fires for Rails/Python",
          "the fallback never fires for them" not in er_src)
    lr_src = " ".join((build / "language_recognizers.py").read_text(encoding="utf-8").replace("#", " ").split())
    check("_BROAD_IGNORE's comment no longer claims it is 'verdict's ignore list + Rails' tmp/ + log/' (it drifted)",
          "the broad set (verdict's ignore list" not in lr_src)

    # truth behind the corrected GLOBAL_SRC_FALLBACK comment: a Python HAS_MANY declared in a file with no
    # node of its own DOES fall back onto a same-named class — which is why the seam keeps such files away.
    import framework_edges as FE
    nodes = [{"key": f"app/models.py::{n}:1", "label": "Class", "name": n, "source_file": "app/models.py",
              "start_line": 1} for n in ("Group", "Permission")]
    edge = FE.TypedEdge("Group", "venv/site-packages/auth/models.py", "Permission", "HAS_MANY", "NAME", {})
    rows = ER.resolve_typed_edges([edge], ER.Resolver(nodes), [n["key"] for n in nodes])
    check("truth: the fallback fires for a Python declaration whose file has no node (INFERRED onto the repo class)",
          [r[:4] for r in rows] == [("HAS_MANY", "app/models.py::Group:1", "app/models.py::Permission:1",
                                     "INFERRED")], rows)


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
    import edge_resolve as ER            # noqa: E402
    import framework_edges as FE         # noqa: E402
    import graphify_extract as GX        # noqa: E402
    import language_recognizers as LR    # noqa: E402
    import sync as S                     # noqa: E402
    import verdict as V                  # noqa: E402
    print("=" * 72)
    print(f"DeepMap — ISS-build:022 + D25: the recognizer seam applies sync's exclusion rule (root: {root})")
    print("=" * 72)
    gate_python_walk(S, LR)
    gate_no_fabricated_orm_edges(S, LR, ER, FE)
    gate_supplemental_and_output(S, LR, FE, V)
    gate_claude_worktrees(S, LR, ER, GX)
    gate_walker_api(LR)
    gate_docs(build, ER, S)
    total = len(PASSES) + len(FAILS)
    print("\n" + "=" * 72)
    if FAILS:
        print(f"RESULT: RED — {len(PASSES)}/{total} gates passed.")
        return 1
    print(f"RESULT: GREEN — {len(PASSES)}/{total} gates passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
