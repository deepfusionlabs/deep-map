#!/usr/bin/env python3
"""F2 r283 — a Go belongs-to principal resolves in its PACKAGE; every typed row is anchored at its emitted src.

Fix-all round 2, follow-ups of O7 (round 1, ISS-build:014: a GORM belongs-to `constraint:OnDelete` cascade is emitted
PRINCIPAL-first — TypedEdge(src = the referenced type, src_file = the dependent's file, dst = the dependent struct)).

  RESIDUAL (O7 not_done #1). edge_resolve resolved that principal package-BLIND: not in the dependent's file -> the
     single same-language :Class of that name anywhere in the sync (GLOBAL_SRC_FALLBACK), else drop. Go resolves a
     bare name in its PACKAGE (its directory), so:
       * a principal name two packages declare (model/Company + billing/Company, both in the sync) dropped the
         belongs-to cascade although the dependent's own package declares exactly one Company (lost recall);
       * a bare principal its own package does not node-ify (a type alias / defined type, generated code not on
         disk) bound ANOTHER package's single same-named :Class at INFERRED — a cross-package guess, fabricated
         unless that type really is the one meant (a same-name alias, a dot import: those now lose a true edge —
         recall, never a wrong bind);
       * that fabricated anchor also lived outside the dependent's directory, so a subdir sync of it pruned a row
         only a sync covering the declaring file re-creates.
     An edge carrying a Go scope now resolves its SRC's global fallback in the declaring file's package only — the
     rule _scoped_targets already applies to a bare dst: exact-name :Class candidates in the src file's directory,
     single candidate only, judged over all nodes before the live filter, graded at most INFERRED. Every other
     language's fallback (the EF Core third-file shape) is unchanged. NOT closed here: a package-QUALIFIED
     belongs-to (`Org models.Org`) is still not emitted by go_edges (the recognizer's decision, not edge_resolve's).
  COMMENTS (O7 routed request). A row's anchor is its EMITTED src — for a REVERSED type the principal, which is not
     always the declaring node (a GORM belongs-to, an EF HasOne…WithMany / third-file chain). edge_resolve.py and
     sync.py said "declaring node / declaring side / DECLARING endpoint" throughout; the stale phrases must be gone,
     and the anchoring truth they now state is checked behaviourally (the row IS anchored at a principal in another
     file, and the disabled-recognizer prune still reaches it: a Go principal lives in a .go file).
  PACKAGE = DIRECTORY, EXACTLY (fix-all round 3, cov-build[3] — gate teeth). The round-2 checks let three wrong
     versions of the rule through: the directory matched by PREFIX, or by its last segment (the package NAME), or
     the live filter applied before the single-candidate check. Now a ROOT-level package (directory "", a prefix of
     every directory) binds its own Company and drops when only billing/ declares one; a sub-package (model/sub) and
     a same-NAMED package elsewhere (legacy/model) never stand in for model/; and a same-package twin outside the
     live set still makes the name ambiguous (ambiguity is judged over every node before the live filter). Each
     wrong version goes RED here.
  DOCSTRINGS (fix-all round 3, cov-build[1] / docs[2] / docs[12]). The code's own text states the src rule that
     edge_resolve applies: go_edges' module and emit_type docstrings, the TypedEdge.scope docstring, go_edges_gates'
     package-safety text (checked when the tree under test holds tests/go_edges_gates.py) and language_recognizers'
     Python-vocabulary comment (it names every ALLOWED type Python does not emit; "CASCADES is Ruby's" is gone).

Every check drives the REAL go_edges recognizer and the REAL edge_resolve over a recording fake session (no Neo4j).
Usage: python tests/f2_go_principal_scope_r283_gates.py [--root <tree> | --root=<tree>]   (--root: load build/ from
       another tree — the RED-first run against a pre-fix or mutant copy; a missing value, a tree without build/ or
       any other argument is a usage error, exit 2)
"""
from __future__ import annotations

import contextlib
import os
import re
import sys
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PASSES: list = []
FAILS: list = []


def check(name: str, ok: bool, detail="") -> None:
    (PASSES if ok else FAILS).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok or not detail else f" — {str(detail)[:700]}"))


def _root_arg(argv: list) -> Path:
    """The tree whose build/ to load: this checkout, or `--root <tree>` / `--root=<tree>`. A mistyped flag must never
    fall back to this checkout (a RED-first run would silently report GREEN), so a missing value, a tree without
    build/ or any other argument is a usage error (exit 2) — the rule tests/go_edges_gates.py applies."""
    root, it, problem = None, iter(argv), ""
    for arg in it:
        if arg == "--root":
            root = next(it, "")
        elif arg.startswith("--root="):
            root = arg[len("--root="):]
        else:
            problem = f"unknown argument {arg!r}"
            break
        if not root or not (Path(root) / "build").is_dir():
            problem = f"--root needs a directory that holds build/ (got {root!r})"
            break
    if problem:
        print(f"usage: python tests/f2_go_principal_scope_r283_gates.py [--root <tree> | --root=<tree>]  ({problem})",
              file=sys.stderr)
        raise SystemExit(2)
    return Path(root).resolve() if root else REPO


TREE = _root_arg(sys.argv[1:])
sys.path.insert(0, str(TREE / "build"))
import edge_resolve as ER          # noqa: E402
import framework_edges as FE       # noqa: E402
import go_edges as G               # noqa: E402
import language_recognizers as LR  # noqa: E402

_CASCADE = '`gorm:"constraint:OnDelete:CASCADE;"`'
COMPANY_MODEL = "package model\n\ntype Company struct {\n\tID   uint\n\tName string\n}\n"
COMPANY_BILLING = "package billing\n\ntype Company struct {\n\tID   uint\n\tPlan string\n}\n"
USER_MODEL = ("package model\n\ntype User struct {\n\tID        uint\n\tCompanyID uint\n"
              f"\tCompany   Company {_CASCADE}\n}}\n")
ALIAS_MODEL = 'package model\n\nimport "shop/billing"\n\ntype Company = billing.Company\n'
# Round 3 (gate teeth): the root-level package, a sub-package, and build-tagged twins of one package.
USER_ROOT = USER_MODEL.replace("package model", "package shop", 1)
COMPANY_ROOT = COMPANY_MODEL.replace("package model", "package shop", 1)
COMPANY_SUB = COMPANY_MODEL.replace("package model", "package sub", 1)
TWIN_LINUX = "//go:build linux\n\npackage model\n\ntype Company struct {\n\tID uint\n}\n"
TWIN_WINDOWS = "//go:build windows\n\npackage model\n\ntype Company struct {\n\tID uint\n}\n"

M_COMPANY = "model/company.go::Company:3"
B_COMPANY = "billing/company.go::Company:3"
M_USER = "model/user.go::User:3"


class _Sess:
    """Records every statement; an UNWIND batch is logged once per row."""

    def __init__(self):
        self.log: list = []

    def run(self, cypher, **params):
        for row in params.get("rows") or [params]:
            self.log.append((cypher, row))


def _nodes(files: dict) -> list:
    """graphify-shaped nodes for {rel: Go source}: the recognizer's ensured :Class per struct (bare-named, exactly as
    graphify names Go struct types) + one file :Module per file (graphify names it after the file stem)."""
    out = []
    for rel, src in files.items():
        out.extend(LR.validate_supplemental(G.recognize(src, rel)[0], "go"))
        stem = Path(rel).stem
        out.append({"key": f"{rel}::{stem}:1", "label": "Module", "name": stem, "source_file": rel,
                    "start_line": 1, "documented": False, "body_summary": f"{stem}: Go file"})
    return out


def _typed(files: dict) -> list:
    return [e for rel, src in files.items() for e in G.recognize(src, rel)[1]]


def _resolve(files: dict, live=None, extra_nodes=()):
    nodes = _nodes(files) + list(extra_nodes)
    keys = [n["key"] for n in nodes]
    resolver = ER.Resolver(nodes)
    rows = ER.resolve_typed_edges(_typed(files), resolver, keys if live is None else live)
    return rows, resolver, keys


def _cascades(rows) -> list:
    return [(r[1], r[2], r[3]) for r in rows if r[0] == "CASCADES"]


def _persisted(log, et: str = "CASCADES") -> set:
    """(from key, to key) of every `et` edge MERGEd, in GRAPH direction."""
    out = set()
    for c, p in log:
        if "MERGE" in c and f"[r:{et}]" in c:
            out.add((p["b"], p["a"]) if f"MERGE (b)-[r:{et}]->(a)" in c else (p["a"], p["b"]))
    return out


# =====================================================================================================
# 1 — the O7 residual: a Go belongs-to principal resolves in the dependent's PACKAGE
# =====================================================================================================
def gate_principal_in_package() -> None:
    print("\n-- a principal name two packages declare: the belongs-to binds the dependent's OWN package's type --")
    files = {"model/company.go": COMPANY_MODEL, "model/user.go": USER_MODEL, "billing/company.go": COMPANY_BILLING}
    bt = [e for e in _typed(files) if e.edge_type == "CASCADES"]
    check("setup: go_edges emits the belongs-to principal-first (Company -> User, scoped, with its foreign key)",
          [(e.src_name, e.src_file, e.dst_name, e.scope, e.attrs.get("foreign_key")) for e in bt]
          == [("Company", "model/user.go", "User", "package", "CompanyID")], bt)
    rows, resolver, keys = _resolve(files)
    check("setup: both packages' Company are :Class nodes of the sync",
          {M_COMPANY, B_COMPANY} <= set(keys) and M_USER in keys, keys)
    check("the belongs-to CASCADES row binds model/company.go's Company -> User (it used to DROP: two Company "
          "classes in the sync)", _cascades(rows) == [(M_COMPANY, M_USER, "INFERRED")], _cascades(rows))
    check("... never billing's same-named Company", not any(B_COMPANY in r[1:3] for r in rows), rows)
    check("... graded INFERRED (another file of the package, never EXTRACTED) and it keeps its foreign_key attr",
          [r[4].get("foreign_key") for r in rows if r[0] == "CASCADES"] == ["CompanyID"], rows)

    s = _Sess()
    ER.push_typed_edges(s, _typed(files), resolver, keys)
    g = _persisted(s.log)
    check("persisted (User)-[:CASCADES]->(model Company): impact('Company') in package model lists User",
          g == {(M_USER, M_COMPANY)}, sorted(g))

    anchored = ER.rows_by_anchor(ER.edge_rows(_typed(files), [], resolver, keys, set(keys)))
    cas = [r for r in anchored.get(M_COMPANY, []) if r[0] == "CASCADES"]
    check("the row is anchored at the principal (model Company) — its emitted src", len(cas) == 1, anchored.keys())
    s2 = _Sess()
    ER.rewrite_anchored_edges(s2, [M_COMPANY], ER.edge_rows(_typed(files), [], resolver, keys, set(keys)), resolver)
    pruned = [p for c, p in s2.log if "DELETE r" in c and "()-[r:CASCADES]->(a)" in c]
    check("rewriting that anchor prunes CASCADES INTO model Company, then re-creates (User)->(model Company)",
          bool(pruned) and pruned[0].get("keys") == [M_COMPANY] and _persisted(s2.log) == {(M_USER, M_COMPANY)},
          (pruned, _persisted(s2.log)))


def gate_no_cross_package_bind() -> None:
    print("\n-- a bare principal its own package does not node-ify never binds ANOTHER package's type --")
    files = {"model/alias.go": ALIAS_MODEL, "model/user.go": USER_MODEL, "billing/company.go": COMPANY_BILLING}
    rows, _r, keys = _resolve(files)
    check("setup: model declares no Company :Class (a type alias); billing's is the only one in the sync",
          B_COMPANY in keys and not [k for k in keys if k.startswith("model/") and k.endswith("::Company:3")], keys)
    check("the belongs-to cascade DROPS instead of binding billing's Company at INFERRED, as it used to — never "
          "across packages (for this same-name alias that loses a true edge: recall, never a wrong bind)",
          _cascades(rows) == [], _cascades(rows))
    check("control: REFERENCES User -> Company drops too (the dst was already resolved in its package)",
          not [r for r in rows if r[0] == "REFERENCES"], rows)


def gate_controls() -> None:
    print("\n-- controls: what package scoping must NOT change --")
    same_file = ("package model\n\ntype Company struct {\n\tID uint\n}\n\ntype User struct {\n\tID        uint\n"
                 f"\tCompanyID uint\n\tCompany   Company {_CASCADE}\n}}\n")
    rows, _r, keys = _resolve({"model/user.go": same_file, "billing/company.go": COMPANY_BILLING})
    sf_company = [k for k in keys if k.startswith("model/user.go::Company:")]
    sf_user = [k for k in keys if k.startswith("model/user.go::User:")]
    check("a principal declared in the dependent's own file binds that same-file :Class (unchanged)",
          len(sf_company) == len(sf_user) == 1
          and _cascades(rows) == [(sf_company[0], sf_user[0], "INFERRED")], (_cascades(rows), keys))

    test_pkg = "package model_test\n\ntype Company struct {\n\tID uint\n}\n"
    rows, _r, _k = _resolve({"model/company.go": COMPANY_MODEL, "model/company_test.go": test_pkg,
                             "model/user.go": USER_MODEL})
    check("two same-named :Class in the dependent's directory stay AMBIGUOUS for a src -> the row drops, never "
          "fans out", _cascades(rows) == [], _cascades(rows))

    files = {"model/company.go": COMPANY_MODEL, "model/user.go": USER_MODEL, "billing/company.go": COMPANY_BILLING}
    nodes_keys = [n["key"] for n in _nodes(files)]
    live = [k for k in nodes_keys if k != M_COMPANY]
    rows, _r, _k = _resolve(files, live=live)
    check("the package's principal out of the live set -> drops (another package's LIVE Company never stands in)",
          _cascades(rows) == [], _cascades(rows))

    rows, _r, _k = _resolve({"model/company.go": COMPANY_MODEL, "model/user.go": USER_MODEL})
    check("one package only (a sync without billing): binds exactly as before",
          _cascades(rows) == [(M_COMPANY, M_USER, "INFERRED")], _cascades(rows))

    has_many = ("package model\n\ntype User struct {\n\tID     uint\n"
                f"\tOrders []Order {_CASCADE}\n}}\n")
    order_m = "package model\n\ntype Order struct {\n\tID     uint\n\tUserID uint\n}\n"
    order_b = "package billing\n\ntype Order struct {\n\tID uint\n}\n"
    rows, _r, _k = _resolve({"model/user.go": has_many, "model/order.go": order_m, "billing/order.go": order_b})
    check("a has-many cascade stays owner -> element, bound in the package (unchanged)",
          _cascades(rows) == [("model/user.go::User:3", "model/order.go::Order:3", "INFERRED")], _cascades(rows))

    # The EF Core third-file shape (C#): the fluent cascade is declared in a DbContext where NEITHER type lives —
    # its src still falls back to the single same-language :Class anywhere in the sync (no scope, no package rule).
    cs_nodes = [{"key": "Models/Order.cs::Order:5", "label": "Class", "name": "Order", "source_file": "Models/Order.cs"},
                {"key": "Models/LineItem.cs::LineItem:5", "label": "Class", "name": "LineItem",
                 "source_file": "Models/LineItem.cs"}]
    te = FE.TypedEdge("Order", "Data/ShopContext.cs", "LineItem", "CASCADES", "NAME", {"via": "fluent"})
    rows = ER.resolve_typed_edges([te], ER.Resolver(cs_nodes), [n["key"] for n in cs_nodes])
    check("the EF third-file cascade (no scope) still resolves its src globally, INFERRED (unchanged)",
          _cascades(rows) == [("Models/Order.cs::Order:5", "Models/LineItem.cs::LineItem:5", "INFERRED")],
          _cascades(rows))

    # A LOCAL-hinted scoped edge whose src comes from the package fallback is capped at INFERRED (never EXTRACTED).
    nodes = _nodes({"model/company.go": COMPANY_MODEL, "model/user.go": USER_MODEL,
                    "billing/company.go": COMPANY_BILLING})
    te = FE.TypedEdge("Company", "model/user.go", "User", "CASCADES", "LOCAL", {"via": "gorm"}, "package")
    rows = ER.resolve_typed_edges([te], ER.Resolver(nodes), [n["key"] for n in nodes])
    check("a package-resolved src is graded at most INFERRED even for a LOCAL hint with a same-file dst",
          _cascades(rows) == [(M_COMPANY, M_USER, "INFERRED")], _cascades(rows))


def _key(keys, rel: str, name: str = "Company") -> str:
    """The one :Class key `rel` declares for `name` (go_edges keys a struct `<rel>::<Name>:<line>`)."""
    found = [k for k in keys if k.startswith(f"{rel}::{name}:")]
    return found[0] if len(found) == 1 else f"<{len(found)} keys for {rel}::{name}>"


def gate_package_is_the_directory() -> None:
    print("\n-- a package is its directory EXACTLY: root package, sub-package, same-named package, non-live twin --")
    # The ROOT package's directory is "" — a prefix of every directory, so only equality keeps billing/ out.
    files = {"user.go": USER_ROOT, "company.go": COMPANY_ROOT, "billing/company.go": COMPANY_BILLING}
    rows, resolver, keys = _resolve(files)
    r_user, r_company = _key(keys, "user.go", "User"), _key(keys, "company.go")
    check("root-level package: the belongs-to binds the root Company -> User, never billing's (the root directory "
          "\"\" is a prefix of billing/)", r_company in keys and r_user in keys
          and _cascades(rows) == [(r_company, r_user, "INFERRED")], (_cascades(rows), keys))
    s = _Sess()
    ER.push_typed_edges(s, _typed(files), resolver, keys)
    check("... persisted (User)-[:CASCADES]->(root Company) and nothing else",
          _persisted(s.log) == {(r_user, r_company)}, sorted(_persisted(s.log)))
    rows, _r, _k = _resolve({"user.go": USER_ROOT, "billing/company.go": COMPANY_BILLING})
    check("a root-level package that declares no Company: the row DROPS — billing's Company never stands in",
          _cascades(rows) == [], _cascades(rows))

    # A SUB-package is another package: model/sub is not model.
    sub = {"model/user.go": USER_MODEL, "model/sub/company.go": COMPANY_SUB}
    rows, _r, keys = _resolve(sub)
    check("a sub-package decoy (model/sub/company.go) as the only Company: the row DROPS, never binds it",
          _key(keys, "model/sub/company.go") in keys and _cascades(rows) == [], (_cascades(rows), keys))
    rows, _r, _k = _resolve({**sub, "model/company.go": COMPANY_MODEL})
    check("... beside model's own Company it neither binds nor makes the name ambiguous: model's Company only",
          _cascades(rows) == [(M_COMPANY, M_USER, "INFERRED")], _cascades(rows))

    # A same-NAMED package in another directory (legacy/model) is another package too.
    legacy = {"model/user.go": USER_MODEL, "legacy/model/company.go": COMPANY_MODEL}
    rows, _r, keys = _resolve(legacy)
    check("a same-NAMED package elsewhere (legacy/model/company.go) as the only Company: the row DROPS",
          _key(keys, "legacy/model/company.go") in keys and _cascades(rows) == [], (_cascades(rows), keys))
    rows, _r, _k = _resolve({**legacy, "model/company.go": COMPANY_MODEL})
    check("... beside model's own Company: binds model's Company only, never legacy/model's",
          _cascades(rows) == [(M_COMPANY, M_USER, "INFERRED")], _cascades(rows))

    # Build-tagged twins of ONE package, one of them outside the live set: ambiguity is judged over every node BEFORE
    # the live filter, so the out-of-scope twin never silently disambiguates the name.
    twins = {"model/company_linux.go": TWIN_LINUX, "model/company_windows.go": TWIN_WINDOWS,
             "model/user.go": USER_MODEL}
    all_keys = [n["key"] for n in _nodes(twins)]
    win = _key(all_keys, "model/company_windows.go")
    rows, _r, _k = _resolve(twins, live=[k for k in all_keys if k != win])
    check("a same-package twin (build-tagged company_windows.go) outside the live set still makes Company AMBIGUOUS "
          "-> the row DROPS, never binds the live twin alone", win in all_keys and _cascades(rows) == [],
          (_cascades(rows), all_keys))
    rows, _r, _k = _resolve(twins)
    check("control: both twins live -> AMBIGUOUS -> the row drops", _cascades(rows) == [], _cascades(rows))


# =====================================================================================================
# 2 — the anchoring truth the comments state, and the stale "declaring" phrases gone
# =====================================================================================================
def _flat(path: Path) -> str:
    """The source text with every line break (and a following comment `#`) collapsed to one space, so a phrase a
    comment or docstring wraps across lines is still found."""
    text = path.read_text(encoding="utf-8")
    return re.sub(r"[ \t]*\r?\n[ \t]*(?:#[ \t]?)?", " ", text)


STALE = {
    "edge_resolve.py": (
        "groups them by declaring node",                    # module docstring (rows_by_anchor)
        "one group of declaring nodes",                     # module docstring (bounded transactions)
        "DECLARATION-anchored",                             # REVERSED_EDGE_TYPES comment
        "anchor at the DECLARING side",                     # REVERSED_EDGE_TYPES comment + _prune_types
        "at its target (the declaring side)",               # _prune_types docstring
        "`src_key` is the DECLARING endpoint",              # resolve_typed_edges docstring
        "src = the DECLARING endpoint",                     # write_edge_rows docstring
        "the DECLARING symbol (a, e.g. Order) becomes the edge TARGET",   # write_edge_rows comment
        "ANCHORED at its declaring endpoint",               # bounded-transaction API comment
        "the declaring node in both cases",                 # bounded-transaction API comment
        "(the declaring endpoint, row[1])",                 # rows_by_anchor docstring
        "declaring node's source_file",                     # disabled-recognizer comment
        "at its target — the declaring node —",             # prune_disabled_recognizer_edges docstring
    ),
    "sync.py": (
        "a typed edge at its declaring side",               # push_to_neo4j block comment
        "rows anchored at their declaring node",            # push_to_neo4j docstring
    ),
}


def gate_anchor_comments() -> None:
    print("\n-- comments: a row is anchored at its EMITTED src (the principal for a reversed type) --")
    for name, phrases in STALE.items():
        flat = _flat(TREE / "build" / name)
        left = [p for p in phrases if p in flat]
        check(f"build/{name}: none of the stale 'declaring-anchored' phrases is left", not left, left)
    er = _flat(TREE / "build" / "edge_resolve.py")
    check("edge_resolve.py states the anchor rule: a row's anchor is its emitted src (the principal for a "
          "reversed type)", "emitted src" in er and "principal" in er)
    check("sync.py names the anchor as the emitted src", "emitted src" in _flat(TREE / "build" / "sync.py"))
    doc = ER.rows_by_anchor.__doc__ or ""
    check("rows_by_anchor's docstring names the emitted src (row[1]), not the declaring endpoint",
          "emitted src" in doc and "declaring endpoint" not in doc, doc)

    # The truth behind the wording: one package, no decoy — the row resolves before and after this fix.
    files = {"model/company.go": COMPANY_MODEL, "model/user.go": USER_MODEL}
    rows, resolver, keys = _resolve(files)
    anchors = {a: rs for a, rs in ER.rows_by_anchor(rows).items() if any(r[0] == "CASCADES" for r in rs)}
    decl_file = [e.src_file for e in _typed(files) if e.edge_type == "CASCADES"]
    check("truth: the belongs-to row's anchor is the principal, in ANOTHER file than the declaring struct",
          list(anchors) == [M_COMPANY] and decl_file == ["model/user.go"]
          and resolver.file_of(M_COMPANY) != decl_file[0], (list(anchors), decl_file))
    with _env(DEEPMAP_RECOGNIZERS="python,ruby,csharp,java"):
        fwd, rev, exts = ER.disabled_recognizer_scope()
    check("truth: with the Go recognizer disabled, its reversed CASCADES prune still reaches a principal anchor "
          "(the anchor is a .go node)", "CASCADES" in rev and ".go" in exts
          and resolver.file_of(M_COMPANY).lower().endswith(tuple(exts)), (rev, exts))


@contextlib.contextmanager
def _env(**kv):
    saved = {k: os.environ.get(k) for k in kv}
    try:
        for k, v in kv.items():
            os.environ[k] = v
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# =====================================================================================================
# 3 — the code's own words about the Go src rule (fix-all round 3: cov-build[1] / docs[2] / docs[12])
# =====================================================================================================
GO_SRC_STALE = {
    "build/go_edges.py": (
        "A SRC gets NO package scope",                            # module docstring, package scoping
        "a name another package also declares drops the edge",    # module docstring (it binds its own package's)
        "can still bind another package's single same-named",     # module docstring residual (it drops now)
        "known residual — a belongs-to principal its package does not node-ify",   # module docstring, honesty model
        "gives a SRC no package scope",                           # emit_type docstring
        "of that name in the sync's node set",                    # module + emit_type docstrings (package-blind)
    ),
    "build/language_recognizers.py": (
        "CASCADES is Ruby's",                                     # Python vocabulary comment (C#/Java/Go emit it)
    ),
    "tests/go_edges_gates.py": (
        "resolves a src with no package scope",                   # module docstring, package safety
        "resolves with NO package scope",                         # the package-qualified principal comment
        "(a src has no package scope)",                           # ... and its check label
    ),
}


def gate_src_rule_docstrings() -> None:
    print("\n-- docstrings: the code states the Go src rule edge_resolve applies (_src_in_package) --")
    for rel, phrases in GO_SRC_STALE.items():
        path = TREE / rel
        if not path.is_file():
            print(f"  [SKIP] {rel} is not in the tree under test")
            continue
        left = [p for p in phrases if p in _flat(path)]
        check(f"{rel}: none of the stale package-blind src phrases is left", not left, left)
    mod = " ".join((G.__doc__ or "").split())
    check("go_edges module docstring names the rule: a Go src falls back inside its own package (_src_in_package)",
          "_src_in_package" in mod, mod[:300])
    go_src = (TREE / "build" / "go_edges.py").read_text(encoding="utf-8")
    m = re.search(r'def emit_type\(.*?\).*?:\s*"""(.*?)"""', go_src, re.S)
    emit_doc = " ".join(m.group(1).split()) if m else ""
    check("go_edges emit_type docstring: a SRC resolves in this file, else in this file's package (_src_in_package)",
          "_src_in_package" in emit_doc and "no package scope" not in emit_doc, emit_doc or "emit_type not found")
    scope_doc = " ".join((FE.TypedEdge.__doc__ or "").split())
    check("TypedEdge docstring: a non-empty scope also confines a Go SRC's fallback to its package (_src_in_package)",
          "_src_in_package" in scope_doc and "SRC" in scope_doc, scope_doc[-500:])

    extra = sorted(set(FE.ALLOWED_EDGE_TYPES) - set(LR.PYTHON.edge_types))
    others = {t for r in LR.REGISTRY if r is not LR.PYTHON for t in r.edge_types}
    check("truth: every ALLOWED type the Python recognizer does not emit comes from another recognizer — except "
          "IMPORTS, which extraction emits", bool(extra) and all((t in others) != (t == "IMPORTS") for t in extra),
          (extra, sorted(others)))
    c = re.search(r"NOT derived from ALLOWED(.*?)edge_types=", _flat(TREE / "build" / "language_recognizers.py"))
    check(f"language_recognizers' Python-vocabulary comment names every ALLOWED type Python does not emit "
          f"({', '.join(extra)})", bool(c) and all(t in c.group(1) for t in extra),
          c.group(0) if c else "comment not found")


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 72)
    print(f"DeepMap — Go belongs-to principal resolves in its package; rows anchored at their emitted src "
          f"(root: {TREE})")
    print("=" * 72)
    check("setup: edge_resolve / go_edges were loaded from the tree under test",
          Path(ER.__file__).resolve().parent == (TREE / "build").resolve()
          and Path(G.__file__).resolve().parent == (TREE / "build").resolve(), (ER.__file__, G.__file__))
    for gate in (gate_principal_in_package, gate_no_cross_package_bind, gate_controls, gate_package_is_the_directory,
                 gate_anchor_comments, gate_src_rule_docstrings):
        try:
            gate()
        except Exception as exc:  # noqa: BLE001 — a crash on the tree under test is a RED result
            traceback.print_exc()
            check(f"{gate.__name__} ran without raising", False, f"{type(exc).__name__}: {exc}")
    total = len(PASSES) + len(FAILS)
    print("\n" + "=" * 72)
    if FAILS:
        print(f"RESULT: RED — {len(PASSES)}/{total} gates passed.")
        return 1
    print(f"RESULT: GREEN — {len(PASSES)}/{total} gates passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
