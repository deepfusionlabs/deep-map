#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gates for the Go recognizer (plugin #5, no Neo4j / go toolchain).

Locks the Gin/GORM declaration layer graphify cannot see (verified against the captured fixture
tests/fixtures/graphify_multilang/go_gorm.json — real `graphify update --no-cluster` output whose
map_graph yields the struct types as bare :Class nodes and same-file handlers as :Function, and NONE
of the GORM/route/embedding relations). Gates:

  - recognizer emit over REAL Go source: slice field -> HAS_MANY, struct field -> REFERENCES,
    `gorm:"constraint:OnDelete:CASCADE"` -> CASCADES (a bare FK is NOT), embedded field -> INHERITS,
    router.GET("/x", h) -> ROUTES_TO, func NewX(dep) *X -> DEPENDS_ON; scalar/stdlib fields skipped;
  - the honesty boundary: Go interface satisfaction is IMPLICIT -> IMPLEMENTS is NEVER emitted;
  - ensured :Class nodes byte-match graphify's bare struct names (dedupe / fill-the-hole);
  - the FULL push path: a same-file route resolves EXTRACTED, a cross-file ctor dep resolves INFERRED,
    CASCADES persists REVERSED; end-to-end registry run + DEEPMAP_RECOGNIZERS=0 off;
  - GORM cascade ORIENTATION (ISS-build:014, fix round r283): `constraint:OnDelete` is a DB foreign key,
    so its principal is the table the key REFERENCES. A has-many slice / has-one field (key on the
    element) stays owner -> element; a BELONGS-TO field (`CompanyID` beside `Company Company`, a
    `foreignKey:` naming a declaring-struct field or its DB column, `<Field>Id`, `references:`, a key
    declared after the field, a multi-name declaration, a key renamed with `column:`, a key promoted from
    a same-file anonymous or `gorm:"embedded"` struct) is emitted element -> owner, so it persists
    (User)-[:CASCADES]->(Company) and impact("Company") lists User. The pre-fix recognizer emitted
    User -> Company for every one of them (persisted backwards). `polymorphic:` and the
    `polymorphicType:`+`polymorphicId:` pair are has-one whatever the declaring struct holds;
  - belongs-to PACKAGE SAFETY (ISS-build:014 repair round 1): edge_resolve resolves a Go src only in the
    declaring file's package (edge_resolve._src_in_package, fix-all r283 F2), where a package-QUALIFIED
    belongs-to principal (`Org models.Org`) does not live, so it emits no cascade (its REFERENCES stays) —
    gated through the real edge_resolve on the out-of-repo `ext.Org`, subdir-sync DTO and full-sync
    shapes, where round 0 bound another package's same-named :Class at INFERRED;
  - GORM fidelity (ISS-build:014 repair round 2): keys are looked up EXACTLY, as GORM's LookUpField does
    (DB column — `column:` or GORM's default naming — then Go name; round 1 compared case- and
    underscore-blind), and a `references:` field the owner provably lacks skips the has-one guess, as GORM
    finds no primary field then (gorm.Model's fields count as visible);
  - SCALING (ISS-build:014 repair round 2): the recognizer's work grows linearly with the file — a
    deterministic count of the lines it executes, no clock. Round 1 re-walked every same-named declaration
    and embed for every cascade field: quadratic, minutes on one valid Go file.

Usage: python tests/go_edges_gates.py [--root <tree> | --root=<tree>]   (--root: run against another
       checkout's build/; a missing value, a tree without build/ or any other argument is a usage error,
       exit 2)
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _parse_root(args: list) -> tuple:
    """(the tree whose build/ to load, None) or (None, the usage problem). `--root <tree>` / `--root=<tree>`
    loads build/ from another checkout (the RED-first run against a pre-fix snapshot); the captured fixtures
    are data and always come from this checkout. A mistyped --root must never fall back to this checkout's
    build/ (a RED-first run would silently report GREEN), so a missing value, a tree without build/ or any
    other argument is a usage error."""
    root, it = None, iter(args)
    for arg in it:
        if arg == "--root":
            root = next(it, "")
        elif arg.startswith("--root="):
            root = arg[len("--root="):]
        else:
            return None, f"unknown argument {arg!r}"
        if not root or not (Path(root) / "build").is_dir():
            return None, f"--root needs a directory that holds build/ (got {root!r})"
    return (Path(root).resolve() if root else REPO), None


ROOT, _USAGE = _parse_root(sys.argv[1:])
if _USAGE:
    print(f"usage: python tests/go_edges_gates.py [--root <tree> | --root=<tree>]  ({_USAGE})", file=sys.stderr)
    raise SystemExit(2)
BUILD = ROOT / "build"
sys.path.insert(0, str(BUILD))

import go_edges as G              # noqa: E402
import language_recognizers as LR  # noqa: E402
import edge_resolve as ER          # noqa: E402
import graphify_extract as GX      # noqa: E402
import framework_edges as FE       # noqa: E402

_PASS = _FAIL = 0


def check(desc: str, cond: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    ok = bool(cond)
    _PASS += ok
    _FAIL += (not ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {desc}" + ("" if ok else f"  -- {detail}"))


# -- sources whose struct names + files ALIGN with the captured fixture (go_gorm.json) -------------
ORDER_GO = """package model

type Order struct {
\tID         uint
\tCustomerID uint
\tCustomer   Customer
\tLineItems  []LineItem `gorm:"foreignKey:OrderID;constraint:OnDelete:CASCADE"`
\tTags       []*Tag `gorm:"many2many:order_tags"`
\tAudits     []Audit `gorm:"foreignKey:OrderID"`
\tNote       string
\tCreatedAt  time.Time
}

type LineItem struct {
\tID      uint
\tOrderID uint
}
"""

ROUTES_GO = """package web

func RegisterRoutes(r *Engine) {
\tr.GET("/orders", GetOrders)
\tr.POST("/orders", handlers.Create)
\tr.DELETE("/orders/:id", func(c *Context) {})
\thttp.HandleFunc("/health", HealthCheck)
}

func GetOrders(c *Context) {
}

func NewOrderService(repo OrderRepo, log Logger, ctx context.Context) *OrderService {
\treturn &OrderService{}
}

type OrderService struct {
\trepo OrderRepo
}
"""

EMBED_GO = """package model

type Admin struct {
\tUser
\tgorm.Model
\tLevel int
}

type Reader interface {
\tRead() int
}

type FileReader struct {
\tpath string
}

func (f *FileReader) Read() int {
\treturn 0
}
"""


def _edges(src, rel):
    return G.recognize(src, rel)[1]


def _by_type(edges, t):
    return [e for e in edges if e.edge_type == t]


def gate_recognizer_emit() -> None:
    print("\n-- recognizer emit: GORM relations / cascades / embedding / routes / DI --")
    e = _edges(ORDER_GO, "model/order.go")
    hm = {x.dst_name for x in _by_type(e, "HAS_MANY")}
    check("slice field []LineItem -> HAS_MANY LineItem", "LineItem" in hm, str(hm))
    check("slice of pointers []*Tag -> HAS_MANY Tag", "Tag" in hm, str(hm))
    refs = {x.dst_name for x in _by_type(e, "REFERENCES")}
    check("struct field Customer -> REFERENCES Customer", "Customer" in refs, str(refs))
    cas = {x.dst_name: x for x in _by_type(e, "CASCADES")}
    check("gorm constraint:OnDelete:CASCADE -> CASCADES Order -> LineItem",
          "LineItem" in cas and cas["LineItem"].src_name == "Order", str(list(cas)))
    check("a bare foreignKey (Audits, no OnDelete) is NOT a cascade", "Audit" not in cas, str(list(cas)))
    check("scalar / stdlib fields (Note string, CreatedAt time.Time, uint IDs) emit nothing",
          "Note" not in {x.dst_name for x in e} and "Time" not in refs and "uint" not in refs)

    ee = _edges(EMBED_GO, "model/admin.go")
    inh = {x.dst_name for x in _by_type(ee, "INHERITS")}
    check("embedded field User -> INHERITS", "User" in inh, str(inh))
    check("embedded qualified gorm.Model -> INHERITS Model (demodulized)", "Model" in inh, str(inh))

    e3 = _edges(ROUTES_GO, "web/routes.go")
    ro = {x.dst_name: x for x in _by_type(e3, "ROUTES_TO")}
    check("r.GET('/orders', GetOrders) -> ROUTES_TO to the bare handler (LOCAL, same-file)",
          "GetOrders" in ro and ro["GetOrders"].conf_hint == "LOCAL"
          and ro["GetOrders"].attrs.get("method") == "GET", str(sorted(ro)))
    # A qualified handler resolves by its last segment in a Go scope (the raw form stays in attrs);
    # `handlers` is not imported by this snippet, so it is a receiver variable -> a method value.
    check("a qualified handler handlers.Create -> NAME (cross-file), raw form kept in attrs",
          "Create" in ro and ro["Create"].conf_hint == "NAME"
          and ro["Create"].attrs.get("handler") == "handlers.Create", str(sorted(ro)))
    check("http.HandleFunc('/health', HealthCheck) -> ROUTES_TO HealthCheck", "HealthCheck" in ro)
    check("an inline func(...) {} handler emits NO route (anonymous, no node)",
          not any(x.attrs.get("path") == "/orders/:id" for x in e3), str([x.attrs for x in e3]))
    dep = {x.dst_name for x in _by_type(e3, "DEPENDS_ON")}
    check("func NewOrderService(repo OrderRepo, log Logger) -> DEPENDS_ON OrderService -> {OrderRepo, Logger}",
          {"OrderRepo", "Logger"} <= dep, str(dep))
    check("a stdlib ctor param (ctx context.Context) is NOT a dependency", "Context" not in dep, str(dep))
    check("every emitted edge type is within ALLOWED_EDGE_TYPES",
          all(x.edge_type in FE.ALLOWED_EDGE_TYPES for x in e + ee + e3))


def gate_no_implements() -> None:
    print("\n-- honesty: Go interface satisfaction is IMPLICIT -> IMPLEMENTS is NEVER emitted --")
    e = _edges(EMBED_GO, "model/admin.go")
    check("no IMPLEMENTS edge for a struct that satisfies an interface (not statically knowable)",
          not _by_type(e, "IMPLEMENTS"), str([x.edge_type for x in e]))
    check("an unexported field (path string) and an interface method emit nothing",
          not any(x.dst_name in ("path", "Read", "string") for x in e))


def gate_ensured_nodes_match_graphify() -> None:
    print("\n-- ensured :Class nodes byte-match graphify's bare struct names (captured fixture) --")
    fx = json.loads((REPO / "tests" / "fixtures" / "graphify_multilang" / "go_gorm.json")
                    .read_text(encoding="utf-8"))
    mapped, _, _ = GX.map_graph(fx)
    g_classes = {(n["name"], n["source_file"]) for n in mapped if n["label"] == "Class"}
    check("FIXTURE ground truth: graphify node-ifies the Go struct types as bare :Class",
          {("Order", "model/order.go"), ("LineItem", "model/order.go")} <= g_classes, str(g_classes))
    check("FIXTURE ground truth: a same-file handler func is a :Function (route resolves same-file)",
          any(n["label"] == "Function" and n["name"] == "GetOrders" for n in mapped))

    n_order, _ = G.recognize(ORDER_GO, "model/order.go")
    names = {n["name"] for n in n_order}
    check("recognizer reproduces graphify's bare struct names (Order, LineItem)",
          names == {"Order", "LineItem"}, str(names))
    check("ensured nodes carry the full push contract",
          all(n["label"] == "Class" and n["documented"] is False
              and all(k in n and n[k] not in (None, "") for k in
                      ("key", "label", "name", "source_file", "start_line", "body_summary"))
              for n in n_order))
    merged = LR.merge_supplemental(mapped, LR.validate_supplemental(n_order, "go"))
    check("graphify parsed the file -> ensured nodes dedupe away (base wins)", merged is mapped)
    merged2 = LR.merge_supplemental([], LR.validate_supplemental(n_order, "go"))
    check("graphify missed the repo -> ensured nodes fill the model layer", len(merged2) == 2)


class _Sess:
    def __init__(self):
        self.log: list = []

    def run(self, cypher, **params):
        for row in params.get("rows") or [params]:   # the push batches with UNWIND $rows: one entry per row
            self.log.append((cypher, row))


def gate_resolution() -> None:
    print("\n-- full push path: same-file route resolves EXTRACTED, cross-file ctor dep INFERRED --")
    nodes = [
        {"key": "web/routes.go::routes:1", "label": "Module", "name": "routes",
         "source_file": "web/routes.go"},
        {"key": "web/routes.go::GetOrders:10", "label": "Function", "name": "GetOrders",
         "source_file": "web/routes.go"},
        {"key": "web/routes.go::OrderService:18", "label": "Class", "name": "OrderService",
         "source_file": "web/routes.go"},
        # A bare Go type is declared in the SAME package (directory) — another file of it here.
        {"key": "web/repo.go::OrderRepo:3", "label": "Class", "name": "OrderRepo",
         "source_file": "web/repo.go"},
    ]
    resolver = ER.Resolver(nodes)
    live = [n["key"] for n in nodes]
    s = _Sess()
    counts = ER.push_typed_edges(s, _edges(ROUTES_GO, "web/routes.go"), resolver, live)
    merges = [(c, p) for c, p in s.log if "MERGE" in c]
    route = [(c, p) for c, p in merges if "ROUTES_TO" in c and p.get("b") == nodes[1]["key"]]
    dep = [(c, p) for c, p in merges if "DEPENDS_ON" in c and p.get("b") == nodes[3]["key"]]
    check("same-file route (routes -> GetOrders) resolves EXTRACTED (LOCAL literal, same file)",
          len(route) == 1 and route[0][1].get("c") == "EXTRACTED", str([p.get("c") for _, p in route]))
    check("cross-file ctor dep (OrderService -> OrderRepo) resolves INFERRED",
          len(dep) == 1 and dep[0][1].get("c") == "INFERRED", str([p.get("c") for _, p in dep]))
    check("the qualified handler handlers.Create DROPS (no node anywhere, no fabrication)",
          not any("handlers" in str(p) for _, p in merges), str(counts))


def gate_cascade_reversed() -> None:
    print("\n-- CASCADES persists REVERSED so impact(Order) surfaces LineItem --")
    nodes = [
        {"key": "o.go::Order:1", "label": "Class", "name": "Order", "source_file": "o.go"},
        {"key": "l.go::LineItem:1", "label": "Class", "name": "LineItem", "source_file": "l.go"},
    ]
    edge = FE.TypedEdge("Order", "o.go", "LineItem", "CASCADES", "NAME", {"via": "gorm"})
    s = _Sess()
    ER.push_typed_edges(s, [edge], ER.Resolver(nodes), [n["key"] for n in nodes])
    m = [c for c, _ in s.log if "MERGE" in c and "CASCADES" in c]
    check("persisted (b)-[:CASCADES]->(a): OWNED LineItem -> OWNER Order",
          m and "MERGE (b)-[r:CASCADES]->(a)" in m[0], str(m))


# -- GORM cascade ORIENTATION (ISS-build:014) ------------------------------------------------------------
# `constraint:OnDelete` is a DB foreign key, so its principal is the table the key REFERENCES. Every edge
# below is (principal, dependent) as the recognizer emits it; edge_resolve persists it dependent->principal.
_CONSTRAINT = '`gorm:"constraint:OnUpdate:CASCADE,OnDelete:SET NULL;"`'


def _struct(name: str, *fields: str, head: str = "", pkg: str = "model") -> str:
    body = "".join(f"\t{f}\n" for f in fields)
    return f"package {pkg}\n\n{head}type {name} struct {{\n{body}}}\n"


def _cascades(src: str, rel: str = "model/user.go") -> set:
    return {(x.src_name, x.dst_name) for x in _by_type(_edges(src, rel), "CASCADES")}


# GORM's documented belongs-to (Company: the key CompanyID is on User) + has-one (CreditCard: the key is
# on the element) + has-many (Accounts) in one model — the shape of the ISS-build:014 report.
GORM_USER_GO = _struct(
    "User", "gorm.Model", "Name       string", "CompanyID  int", f"Company    Company {_CONSTRAINT}",
    f"CreditCard CreditCard {_CONSTRAINT}", 'Accounts   []Account `gorm:"constraint:OnDelete:CASCADE"`')


def gate_gorm_cascade_orientation() -> None:
    print("\n-- GORM cascade orientation (ISS-build:014): the principal is the type the key REFERENCES --")
    e = _edges(GORM_USER_GO, "model/user.go")
    cas = {(x.src_name, x.dst_name): x for x in _by_type(e, "CASCADES")}
    check("belongs-to (CompanyID on User): CASCADES Company -> User (a Company delete reaches its Users)",
          ("Company", "User") in cas and ("User", "Company") not in cas, str(sorted(cas)))
    bt = cas.get(("Company", "User"))
    check("...the belongs-to edge names its foreign key; the dependent (User, this file) is the dst",
          bt is not None and bt.attrs == {"via": "gorm", "field": "Company", "foreign_key": "CompanyID"}
          and bt.src_file == "model/user.go" and bt.scope == "package", str(bt))
    check("has-one (no CreditCardID on User -> the key is on CreditCard): stays User -> CreditCard, "
          "attrs unchanged", ("User", "CreditCard") in cas
          and cas[("User", "CreditCard")].attrs == {"via": "gorm", "field": "CreditCard"}, str(sorted(cas)))
    check("has-many slice: stays User -> Account", ("User", "Account") in cas, str(sorted(cas)))
    check("REFERENCES keeps holder -> referenced type for the belongs-to field (only CASCADES turns)",
          ("User", "Company") in {(x.src_name, x.dst_name) for x in _by_type(e, "REFERENCES")})
    summary = G.recognize(GORM_USER_GO, "model/user.go")[0][0]["body_summary"]
    check("the ensured node's summary states each cascade principal -> dependent",
          "cascade Company -> User" in summary and "cascade User -> CreditCard" in summary
          and "cascade User -> Company" not in summary, summary)

    cases = [
        ("the key is named after the FIELD, not its type (Owner User / OwnerID)",
         _struct("Account", "OwnerID uint", f"Owner User {_CONSTRAINT}"), ("User", "Account")),
        ("the key declared AFTER the association field, on a pointer (*Employer / EmployerID *uint)",
         _struct("User", f"Employer *Employer {_CONSTRAINT}", "EmployerID *uint"), ("Employer", "User")),
        ("GORM's `<Field>Id` spelling (TeamId)",
         _struct("User", "TeamId uint", f"Team Team {_CONSTRAINT}"), ("Team", "User")),
        ("...which is a Go-name match of its own: a TeamId renamed with `column:tid` is still the key",
         _struct("User", 'TeamId uint `gorm:"column:tid"`', f"Team Team {_CONSTRAINT}"), ("Team", "User")),
        ("explicit foreignKey naming a field of the declaring struct (ManagerRef)",
         _struct("User", "ManagerRef uint",
                 'Manager Manager `gorm:"foreignKey:ManagerRef;constraint:OnDelete:SET NULL"`'),
         ("Manager", "User")),
        ("the tag key is case-insensitive (`foreignkey:`), as in GORM",
         _struct("User", "ManagerRef uint",
                 'Manager Manager `gorm:"foreignkey:ManagerRef;constraint:OnDelete:CASCADE"`'),
         ("Manager", "User")),
        ("`references:Code` -> the key is <Field>Code (RegionCode on User)",
         _struct("User", "RegionCode string",
                 'Region Region `gorm:"references:Code;constraint:OnDelete:CASCADE"`'), ("Region", "User")),
        ("a multi-name declaration holds the key (CarrierID, DepotID uint)",
         _struct("Shipment", "CarrierID, DepotID uint", f"Carrier Carrier {_CONSTRAINT}"),
         ("Carrier", "Shipment")),
        ("a key promoted from a same-file embedded struct (Tenancy.TenantID embedded in Invoice)",
         _struct("Tenancy", "TenantID uint") + "\n"
         + _struct("Invoice", "Tenancy", f"Tenant Tenant {_CONSTRAINT}").split("\n", 2)[2],
         ("Tenant", "Invoice")),
        ("explicit foreignKey the declaring struct does NOT hold (UserName on Profile) -> has-one",
         _struct("User", 'Profile Profile `gorm:"foreignKey:UserName;constraint:OnDelete:CASCADE"`'),
         ("User", "Profile")),
        ("a slice never turns, even beside a <Field>ID-named field",
         _struct("User", "AccountsID uint", 'Accounts []*Account `gorm:"constraint:OnDelete:CASCADE"`'),
         ("User", "Account")),
        ("polymorphic is always has-one (a coincidental AvatarID does not make it belongs-to)",
         _struct("User", "AvatarID uint",
                 'Avatar Avatar `gorm:"polymorphic:Owner;constraint:OnDelete:CASCADE"`'), ("User", "Avatar")),
        ("both keys visible in one file -> GORM guesses has-one FIRST (Passport.PersonID wins)",
         _struct("Person", "PassportID uint", f"Passport Passport {_CONSTRAINT}") + "\n"
         + _struct("Passport", "PersonID uint").split("\n", 2)[2], ("Person", "Passport")),
        ("the element in the same file WITHOUT the has-one key -> belongs-to",
         _struct("Person", "PassportID uint", f"Passport Passport {_CONSTRAINT}") + "\n"
         + _struct("Passport", "Number string").split("\n", 2)[2], ("Passport", "Person")),
        # GORM looks a key up by Go field name OR DB column (LookUpField), and flattens embedded structs.
        ("polymorphicType + polymorphicId (no `polymorphic:`) is polymorphic too -> has-one, AvatarID or not",
         _struct("User", "AvatarID uint",
                 'Avatar Avatar `gorm:"polymorphicType:Kind;polymorphicId:OwnerID;constraint:OnDelete:CASCADE"`'),
         ("User", "Avatar")),
        ("a foreignKey naming the DB column (company_id) of a declaring-struct field",
         _struct("User", "CompanyID uint",
                 'Company Company `gorm:"foreignKey:company_id;constraint:OnDelete:CASCADE"`'), ("Company", "User")),
        ("the key renamed with `column:` is found by its DB column (OwnerRef column:company_id)",
         _struct("User", 'OwnerRef uint `gorm:"column:company_id"`', f"Company Company {_CONSTRAINT}"),
         ("Company", "User")),
        ('a key inside a named `gorm:"embedded"` field (Ref.CompanyID) is a column of the declaring struct',
         _struct("Ref", "CompanyID uint") + "\n"
         + _struct("User", 'Ref Ref `gorm:"embedded"`', f"Company Company {_CONSTRAINT}").split("\n", 2)[2],
         ("Company", "User")),
        ("...with `embeddedPrefix:ref_`, a foreignKey naming the prefixed column (ref_company_id)",
         _struct("Ref", "CompanyID uint") + "\n"
         + _struct("User", 'Ref Ref `gorm:"embedded;embeddedPrefix:ref_"`',
                   'Company Company `gorm:"foreignKey:ref_company_id;constraint:OnDelete:CASCADE"`')
         .split("\n", 2)[2], ("Company", "User")),
        ("has-one first finds the element's key by DB column too (Passport.Holder column:person_id)",
         _struct("Person", "PassportID uint", f"Passport Passport {_CONSTRAINT}") + "\n"
         + _struct("Passport", 'Holder uint `gorm:"column:person_id"`').split("\n", 2)[2],
         ("Person", "Passport")),
        # Repair round 2 — two sub-rules no case isolated (each survived a mutation run of round 1):
        ("`polymorphicType:` WITHOUT `polymorphicId:` is not polymorphic -> the AvatarID makes it belongs-to",
         _struct("User", "AvatarID uint",
                 'Avatar Avatar `gorm:"polymorphicType:Kind;constraint:OnDelete:CASCADE"`'), ("Avatar", "User")),
        ("a composite foreignKey the declaring struct holds only PART of (no CompanyTenant) -> has-one",
         _struct("User", "CompanyID uint",
                 'Company Company `gorm:"foreignKey:CompanyID,CompanyTenant;constraint:OnDelete:CASCADE"`'),
         ("User", "Company")),
        # GORM fidelity (repair round 2): LookUpField is EXACT — the DB column, then the Go name.
        ("`foreignKey:CardID` is not Org's column card_id (ProfileRef) -> the owner's CardID makes it belongs-to",
         _struct("Owner", "CardID uint", 'Org *Org `gorm:"foreignKey:CardID;constraint:OnDelete:CASCADE"`')
         + "\n" + _struct("Org", "ID uint", 'ProfileRef uint `gorm:"column:card_id"`').split("\n", 2)[2],
         ("Org", "Owner")),
        ("`foreignKey:LeadID` is not Member's own LeadId (case differs) -> Team's LeadID makes it belongs-to",
         _struct("Team", "LeadID uint", 'Lead Member `gorm:"foreignKey:LeadID;constraint:OnDelete:SET NULL"`')
         + "\n" + _struct("Member", "ID uint", "LeadId uint").split("\n", 2)[2], ("Member", "Team")),
        ("an implicit key is one of GORM's exact names: `Companyid` is neither CompanyID / CompanyId nor the "
         "column company_id -> has-one", _struct("User", "Companyid uint", f"Company Company {_CONSTRAINT}"),
         ("User", "Company")),
        ("a foreignKey DB column follows GORM's default naming, initialisms included (APIKeyID -> api_key_id)",
         _struct("Client", "APIKeyID uint",
                 'APIKey APIKey `gorm:"foreignKey:api_key_id;constraint:OnDelete:CASCADE"`'), ("APIKey", "Client")),
        # A `references:` field the owner PROVABLY lacks leaves GORM no primary field for the has-one guess.
        ("`references:Code` the owner lacks skips the has-one guess (Card.UserID) -> belongs-to on CardCode",
         _struct("User", "gorm.Model", "CardCode string",
                 'Card Card `gorm:"references:Code;constraint:OnDelete:CASCADE"`') + "\n"
         + _struct("Card", "ID uint", "Code string", "UserID uint").split("\n", 2)[2], ("Card", "User")),
        ("...`references:ID` names gorm.Model's ID, so the has-one guess stands (Profile.UserID)",
         _struct("User", "gorm.Model", "ProfileID uint",
                 'Profile Profile `gorm:"references:ID;constraint:OnDelete:CASCADE"`') + "\n"
         + _struct("Profile", "ID uint", "UserID uint").split("\n", 2)[2], ("User", "Profile")),
        ("...gorm.Model is known through an aliased import too (g \"gorm.io/gorm\")",
         _struct("User", "g.Model", "CardCode string",
                 'Card Card `gorm:"references:Code;constraint:OnDelete:CASCADE"`', head='import g "gorm.io/gorm"\n\n')
         + "\n" + _struct("Card", "ID uint", "Code string", "UserID uint").split("\n", 2)[2], ("Card", "User")),
        ("...but a `gorm.Model` imported from ANOTHER package is not gorm's: its fields are unknown, so the "
         "has-one guess stands", _struct("User", "gorm.Model", "CardCode string",
                                         'Card Card `gorm:"references:Code;constraint:OnDelete:CASCADE"`',
                                         head='import gorm "example.com/orm"\n\n')
         + "\n" + _struct("Card", "ID uint", "Code string", "UserID uint").split("\n", 2)[2], ("User", "Card")),
        ("...a `references:` field behind an embed declared elsewhere (Base) may exist: taken as written, "
         "the has-one guess stands", _struct("User", "Base", "CardCode string",
                                             'Card Card `gorm:"references:Code;constraint:OnDelete:CASCADE"`')
         + "\n" + _struct("Card", "ID uint", "Code string", "UserID uint").split("\n", 2)[2], ("User", "Card")),
        ("a `references:` given as the element's DB column (code) names its Go field (Code): the key is "
         "RegionCode", _struct("User", "RegionCode string",
                               'Region Region `gorm:"references:code;constraint:OnDelete:CASCADE"`')
         + "\n" + _struct("Region", "ID uint", "Code string").split("\n", 2)[2], ("Region", "User")),
    ]
    for desc, src, want in cases:
        got = _cascades(src)
        check(f"{desc}: CASCADES {want[0]} -> {want[1]}", got == {want}, str(sorted(got)))
    renamed = _by_type(_edges(_struct("User", 'OwnerRef uint `gorm:"column:company_id"`',
                                      f"Company Company {_CONSTRAINT}"), "model/user.go"), "CASCADES")
    check("...the belongs-to edge's foreign_key names the Go field that holds the key (OwnerRef)",
          [x.attrs.get("foreign_key") for x in renamed] == ["OwnerRef"], str([x.attrs for x in renamed]))

    # A belongs-to principal is the edge's SRC, which edge_resolve resolves only in the declaring file's
    # package (this file, else the single same-named :Class of its directory — _src_in_package). A `pkg.T`
    # principal lives in another package and would lose the only evidence of which one, so no cascade is
    # emitted for it; its REFERENCES keeps the import scope.
    models = 'import "shop/internal/models"\n\n'
    qe = _edges(_struct("Membership", "OrgID uint", f"Org models.Org {_CONSTRAINT}", pkg="api", head=models),
                "api/membership.go")
    check("a package-qualified belongs-to principal (models.Org) emits NO cascade (a src resolves only in its "
          "declaring file's package)",
          not _by_type(qe, "CASCADES"), str([(x.edge_type, x.src_name, x.dst_name, x.scope) for x in qe]))
    check("...its REFERENCES Membership -> Org still goes out, scoped to the imported package",
          [(x.src_name, x.dst_name, x.scope) for x in _by_type(qe, "REFERENCES")]
          == [("Membership", "Org", "import:shop/internal/models")],
          str([(x.edge_type, x.src_name, x.dst_name, x.scope) for x in qe]))
    qh = _edges(_struct("Membership", f"Profile models.Profile {_CONSTRAINT}", pkg="api", head=models),
                "api/membership.go")
    check("a package-qualified HAS-ONE (no ProfileID on Membership) keeps owner -> element, import-scoped",
          [(x.src_name, x.dst_name, x.scope) for x in _by_type(qh, "CASCADES")]
          == [("Membership", "Profile", "import:shop/internal/models")],
          str([(x.edge_type, x.src_name, x.dst_name, x.scope) for x in qh]))

    stdlib = _cascades(_struct("User", "ConnID uint", f"Conn sql.Conn {_CONSTRAINT}"))
    check("a stdlib-qualified type is still never an edge (belongs-to or not)", stdlib == set(), str(stdlib))
    shadow = _struct("Org", "Name string", pkg="api", head=models) + "\n" \
        + _struct("Seat", "OrgID uint", f"Org models.Org {_CONSTRAINT}").split("\n", 2)[2]
    se = _edges(shadow, "api/seat.go")
    check("a qualified principal whose bare name THIS file also declares emits no cascade either "
          "(same-file resolution would have bound the local Org); REFERENCES Seat -> Org stays",
          not _by_type(se, "CASCADES")
          and ("Seat", "Org") in {(x.src_name, x.dst_name) for x in _by_type(se, "REFERENCES")},
          str([(x.edge_type, x.src_name, x.dst_name, x.scope) for x in se]))
    shadow_key = _struct("Org", "ID uint", "SeatID uint", pkg="api", head=models) + "\n" \
        + _struct("Seat", "OrgID uint", f"Org models.Org {_CONSTRAINT}").split("\n", 2)[2]
    sk = _edges(shadow_key, "api/seat.go")
    check("...even when that local Org holds the has-one key (SeatID): a `pkg.T` element is never looked up "
          "in this file, so no has-one cascade appears; REFERENCES Seat -> Org stays import-scoped",
          not _by_type(sk, "CASCADES") and [(x.src_name, x.dst_name, x.scope) for x in _by_type(sk, "REFERENCES")]
          == [("Seat", "Org", "import:shop/internal/models")],
          str([(x.edge_type, x.src_name, x.dst_name, x.scope) for x in sk]))


def gate_gorm_column_naming() -> None:
    print("\n-- GORM's default DB column naming (a `foreignKey:` / `column:` key is compared to it exactly) --")
    # Rows of GORM's own TestToDBName (schema/naming_test.go).
    table = {"": "", "X": "x", "userRestrictions": "user_restrictions", "ThisIsATest": "this_is_a_test",
             "PFAndESI": "pf_and_esi", "AbcAndJkl": "abc_and_jkl", "EmployeeID": "employee_id",
             "SKU_ID": "sku_id", "FieldX": "field_x", "HTTPAndSMTP": "http_and_smtp",
             "HTTPServerHandlerForURLID": "http_server_handler_for_url_id", "UUID": "uuid",
             "HTTPURL": "http_url", "HTTP_URL": "http_url", "SHA256Hash": "sha256_hash",
             "SHA256HASH": "sha256_hash"}
    got = {name: G._db_name(name) for name in table}
    check("_db_name reproduces GORM's TestToDBName table", got == table,
          str({k: v for k, v in got.items() if v != table[k]}))
    check("...and the key spellings: CompanyID / CompanyId -> company_id, APIKeyID -> api_key_id, "
          "Companyid -> companyid", [G._db_name(n) for n in ("CompanyID", "CompanyId", "APIKeyID", "Companyid")]
          == ["company_id", "company_id", "api_key_id", "companyid"])


def _persisted(log, et: str = "CASCADES") -> set:
    """(from key, to key) of every `et` edge the push MERGEd, in GRAPH direction."""
    out = set()
    for c, p in log:
        if "MERGE" in c and f"[r:{et}]" in c:
            out.add((p["b"], p["a"]) if f"MERGE (b)-[r:{et}]->(a)" in c else (p["a"], p["b"]))
    return out


def gate_gorm_cascade_persisted() -> None:
    print("\n-- GORM cascades persisted: impact(Company) lists User; the row is anchored at the principal --")
    nodes = [
        {"key": "model/user.go::User:3", "label": "Class", "name": "User", "source_file": "model/user.go"},
        {"key": "model/company.go::Company:3", "label": "Class", "name": "Company",
         "source_file": "model/company.go"},
        {"key": "model/credit_card.go::CreditCard:3", "label": "Class", "name": "CreditCard",
         "source_file": "model/credit_card.go"},
        {"key": "model/account.go::Account:3", "label": "Class", "name": "Account",
         "source_file": "model/account.go"},
    ]
    user, company, card, account = (n["key"] for n in nodes)
    live = [n["key"] for n in nodes]
    edges = _edges(GORM_USER_GO, "model/user.go")
    s = _Sess()
    ER.push_typed_edges(s, edges, ER.Resolver(nodes), live)
    graph = _persisted(s.log)
    check("persisted (User)-[:CASCADES]->(Company), never (Company)-[:CASCADES]->(User)",
          (user, company) in graph and (company, user) not in graph, str(sorted(graph)))
    check("has-one / has-many persist (CreditCard)->(User) and (Account)->(User), as before",
          {(card, user), (account, user)} <= graph, str(sorted(graph)))

    def impact(key):                       # deepmap_impact is reverse reachability: what points INTO key
        return {a for a, b in graph if b == key}
    check("impact('Company') lists User; impact('User') does not list Company",
          user in impact(company) and company not in impact(user), f"{impact(company)} / {impact(user)}")
    check("impact('User') lists its has-one + has-many dependents (CreditCard, Account)",
          {card, account} <= impact(user), str(impact(user)))
    bt = [p for c, p in s.log if "MERGE" in c and "[r:CASCADES]" in c and p.get("a") == company]
    check("the belongs-to row (principal from another file of the package) grades INFERRED",
          len(bt) == 1 and bt[0].get("c") == "INFERRED", str(bt))

    # The bounded-transaction path anchors a row at its emitted src: here the PRINCIPAL, whose group
    # prunes the CASCADES pointing INTO it and re-creates the row in the same transaction.
    resolver = ER.Resolver(nodes)
    rows = ER.edge_rows(edges, [], resolver, live, set(live))
    anchored = [r for r in ER.rows_by_anchor(rows).get(company, []) if r[0] == "CASCADES"]
    check("the belongs-to CASCADES row is anchored at the principal Company",
          [(r[1], r[2]) for r in anchored] == [(company, user)], str(anchored))
    s2 = _Sess()
    ER.rewrite_anchored_edges(s2, [company], rows, resolver)
    pruned = [p for c, p in s2.log if "DELETE r" in c and "()-[r:CASCADES]->(a)" in c]
    check("rewriting the Company anchor prunes CASCADES INTO Company, then re-creates (User)->(Company)",
          pruned and pruned[0].get("keys") == [company] and _persisted(s2.log) == {(user, company)},
          f"{pruned} / {_persisted(s2.log)}")

    # A principal name two Go packages declare, BOTH in this sync: edge_resolve resolves a Go src in the
    # declaring file's PACKAGE (fix-all r283, F2 — it used to drop the row), so the row binds model's own
    # Company, never billing's. A package-qualified principal is not emitted at all
    # (gate_gorm_cascade_package_safety).
    decoy = {"key": "billing/company.go::Company:3", "label": "Class", "name": "Company",
             "source_file": "billing/company.go"}
    s3 = _Sess()
    ER.push_typed_edges(s3, edges, ER.Resolver(nodes + [decoy]), live + [decoy["key"]])
    g3 = _persisted(s3.log)
    check("a principal name two packages declare (both in the sync) binds the dependent's own package's "
          "Company — never the other package's", (user, company) in g3
          and not any(decoy["key"] in pair for pair in g3), str(sorted(g3)))


def _cls(key: str) -> dict:
    """A :Class node from its key `file::Name:line`."""
    path, rest = key.split("::")
    return {"key": key, "label": "Class", "name": rest.split(":")[0], "source_file": path}


def _push_graph(src: str, rel: str, nodes: list) -> tuple:
    """(CASCADES, REFERENCES) the real push persists, in GRAPH direction, for one recognized file."""
    s = _Sess()
    ER.push_typed_edges(s, _edges(src, rel), ER.Resolver(nodes), [n["key"] for n in nodes])
    return _persisted(s.log), _persisted(s.log, "REFERENCES")


def gate_gorm_cascade_package_safety() -> None:
    print("\n-- belongs-to package safety: a package-qualified principal never binds another package's type --")
    member = "api/membership.go::Membership:5"
    ext = _struct("Membership", "OrgID uint", f"Org ext.Org {_CONSTRAINT}", pkg="api",
                  head='import ext "github.com/vendor/extlib/models"\n\n')
    cas, refs = _push_graph(ext, "api/membership.go", [_cls(member), _cls("internal/cache/org.go::Org:3")])
    check("an out-of-repo principal (ext.Org) binds nothing — not the repo's unrelated internal/cache Org",
          not cas and not refs, f"{sorted(cas)} / {sorted(refs)}")

    src = _struct("Membership", "OrgID uint", f"Org models.Org {_CONSTRAINT}", pkg="api",
                  head='import "shop/internal/models"\n\n')
    sub = [_cls(member), _cls("api/org.go::Org:3")]     # a subdir sync of api/: internal/models is left out
    cas, refs = _push_graph(src, "api/membership.go", sub)
    check("a subdir sync of api/ binds models.Org to nothing — not the same-named api/org.go DTO",
          not cas and not refs, f"{sorted(cas)} / {sorted(refs)}")
    models_org = "internal/models/org.go::Org:3"
    cas, refs = _push_graph(src, "api/membership.go", sub + [_cls(models_org)])
    check("the full sync binds REFERENCES to internal/models' Org (import scope) and persists no cascade",
          refs == {(member, models_org)} and not cas, f"{sorted(cas)} / {sorted(refs)}")


def _work(src: str) -> int:
    """The lines go_edges.py executes while recognizing `src` — a deterministic count of the recognizer's own
    work (a clock would make the gate flaky; this count is the same on every run of one interpreter)."""
    mine, count = G.recognize.__code__.co_filename, 0

    def local(frame, event, arg):
        nonlocal count
        if event == "line":
            count += 1
        return local

    def enter(frame, event, arg):
        return local if frame.f_code.co_filename == mine else None

    prev = sys.gettrace()
    sys.settrace(enter)
    try:
        G.recognize(src, "model/scale_test.go")
    finally:
        sys.settrace(prev)
    return count


def _scale_testfile(d: int) -> str:
    """d test functions, each declaring its own local Company + User with 3 belongs-to keys (GORM's own test
    style): 3*d cascades over names declared d times."""
    out = ["package model_test", ""]
    for t in range(d):
        out += [f"func TestCase{t}(t *testing.T) {{", "\ttype Company struct {", "\t\tID uint", "\t}",
                "\ttype User struct {", "\t\tID uint"]
        for j in range(3):
            out += [f"\t\tRef{j}ID uint",
                    f'\t\tRef{j} Company `gorm:"foreignKey:Ref{j}ID;constraint:OnDelete:CASCADE"`']
        out += ["\t}", "}", ""]
    return "\n".join(out)


def _scale_selfembed(d: int) -> str:
    """d functions, each declaring a struct that embeds a pointer to ITSELF with an embeddedPrefix (a prefix
    chain that never ends) and holds 20 cascades."""
    out = ["package model", ""]
    for t in range(d):
        out += [f"func T{t}() {{", "\ttype A struct {", '\t\t*A `gorm:"embeddedPrefix:p_"`']
        out += [f'\t\tC{j} C `gorm:"constraint:OnDelete:CASCADE"`' for j in range(20)]
        out += ["\t}", "}"]
    return "\n".join(out)


def _scale_bigembed(n: int) -> str:
    """n structs, each embedding one n-field struct and holding one belongs-to cascade."""
    out = ["package model", "", "type B struct {"] + [f"\tF{j} uint" for j in range(n)] + ["}"]
    for s in range(n):
        out += [f"type S{s} struct {{", "\tB", "\tCompanyID uint",
                '\tCompany Company `gorm:"constraint:OnDelete:CASCADE"`', "}"]
    return "\n".join(out)


def _scale_chain(n: int) -> str:
    """n structs, each embedding the next and holding one HAS-ONE cascade: every key lookup misses, so an
    unbounded walk would follow the whole chain from every struct."""
    out = ["package model", ""]
    for s in range(n):
        out += [f"type S{s} struct {{"] + ([f"\tS{s + 1}"] if s + 1 < n else []) \
            + [f'\tC{s} C{s} `gorm:"constraint:OnDelete:CASCADE"`', "}"]
    return "\n".join(out)


def _scale_prefixes(d: int) -> str:
    """d functions, each redeclaring struct A with its OWN embed + embeddedPrefix and a cascade of its own
    name: the merged A holds d distinct prefixes, which every one of its d key lookups must weigh."""
    out = ["package model", ""]
    for t in range(d):
        out += [f"func T{t}() {{", "\ttype A struct {", f'\t\tE{t} E{t} `gorm:"embedded;embeddedPrefix:p{t}_"`',
                f'\t\tC{t} C{t} `gorm:"constraint:OnDelete:CASCADE"`', "\t}", "}"]
    return "\n".join(out)


def gate_gorm_cascade_scaling() -> None:
    print("\n-- scaling (ISS-build:014 repair round 2): the work grows LINEARLY with the file (lines run, no clock) --")
    got = _cascades(_scale_testfile(4), "model/scale_test.go")
    check("the scaled test-file shape exercises the belongs-to path (every cascade Company -> User)",
          got == {("Company", "User")}
          and len(_by_type(_edges(_scale_testfile(4), "model/scale_test.go"), "CASCADES")) == 12, str(got))
    for desc, shape, n in (
            ("a test file of function-local models (Company/User per test, 3 belongs-to keys each)",
             _scale_testfile, 20),
            ("a self-embedding struct with an embeddedPrefix, redeclared per function, 20 cascades each",
             _scale_selfembed, 1),
            ("many structs embedding one wide struct, one belongs-to cascade each", _scale_bigembed, 50),
            ("a chain of structs each embedding the next, one has-one cascade each (every lookup misses)",
             _scale_chain, 256),
            ("one struct redeclared per function, each with its own embeddedPrefix and cascade",
             _scale_prefixes, 64)):
        small, large = _work(shape(n)), _work(shape(4 * n))
        ratio = large / max(small, 1)
        check(f"{desc}: 4x the input costs at most 5x the work (linear is ~4x)", ratio <= 5.0,
              f"lines run {small} -> {large} = x{ratio:.2f}")


def gate_registry_end_to_end() -> None:
    print("\n-- registry end-to-end: LR.run over a Go tree; =0 turns the layer off --")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "model").mkdir()
        (root / "web").mkdir()
        (root / "model" / "order.go").write_text(ORDER_GO, encoding="utf-8")
        (root / "web" / "routes.go").write_text(ROUTES_GO, encoding="utf-8")
        (root / "vendor").mkdir()
        (root / "vendor" / "dep.go").write_text(ORDER_GO, encoding="utf-8")
        supp, edges = LR.run(root)
        check("go entry runs: ensured nodes + typed edges emitted",
              len(supp) >= 3 and any(x.edge_type == "CASCADES" for x in edges),
              f"supp={len(supp)} edges={len(edges)}")
        check("vendored code never contributes", all("vendor" not in n["source_file"] for n in supp)
              and all("vendor" not in x.src_file for x in edges))
        saved = os.environ.get("DEEPMAP_RECOGNIZERS")
        try:
            os.environ["DEEPMAP_RECOGNIZERS"] = "0"
            check("DEEPMAP_RECOGNIZERS=0 -> the whole layer is off ([], [])", LR.run(root) == ([], []))
            os.environ["DEEPMAP_RECOGNIZERS"] = "go"
            supp_g, edges_g = LR.run(root)
            check("CSV 'go' runs only this entry", len(supp_g) >= 3 and len(edges_g) >= 3)
        finally:
            if saved is None:
                os.environ.pop("DEEPMAP_RECOGNIZERS", None)
            else:
                os.environ["DEEPMAP_RECOGNIZERS"] = saved


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 64)
    print("DeepMap — Go recognizer gates (plugin #5, no Neo4j / go toolchain)")
    print("=" * 64)
    gate_recognizer_emit()
    gate_no_implements()
    gate_ensured_nodes_match_graphify()
    gate_resolution()
    gate_cascade_reversed()
    for gate in (gate_gorm_cascade_orientation, gate_gorm_column_naming, gate_gorm_cascade_persisted,
                 gate_gorm_cascade_package_safety, gate_gorm_cascade_scaling):
        try:
            gate()
        except Exception as exc:  # noqa: BLE001 — a crash on a pre-fix tree is a RED result, not a harness error
            check(f"{gate.__name__} ran without raising", False, f"{type(exc).__name__}: {exc}")
    gate_registry_end_to_end()
    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
