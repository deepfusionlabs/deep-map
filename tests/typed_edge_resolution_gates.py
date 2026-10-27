#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gates for typed-edge NAME RESOLUTION — the 2026-09 live-validation findings (no Neo4j).

Every check drives the real recognizers + the real edge_resolve.push_typed_edges over a recording
fake session, so a pass here is the exact MERGE set sync would persist.

  T1 (#57) Module/Class same-name collision: on graphify's file-per-class shape (Order.cs -> a Module
     AND a Class `Order`, Java constructor Method `X.X`) every declared relationship must persist ONCE,
     anchored on the :Class, graded INFERRED/EXTRACTED — never fanned out to the file node or the
     constructor, never AMBIGUOUS. Built on a REAL captured graphify graph
     (tests/fixtures/graphify_multilang/csharp_java_collision.json).
  T2 (#61) language-blind resolution: a Rails association never binds to a Go/Java/Python type of
     the same name, and the real Rails edges keep their INFERRED grade in a polyglot repo.
  T3 (#60) Rails `resources ... only:/except:`: no ROUTES_TO to an action the resource does not
     route; an unparseable option grades DYNAMIC; `resource :x` is served by the PLURAL controller.
  T4 (#59/#102) Go route handlers: package-qualified, method-value and same-package-other-file
     handlers resolve (scoped to the right package), a route src is never duplicated onto `func main`.
  T5 (#103) Go type references: a stdlib-qualified type (`http.Handler`) never binds to a same-named
     repo METHOD (or anything else); a type-level edge never lands on a callable.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build"
sys.path.insert(0, str(BUILD))

import edge_resolve as ER            # noqa: E402
import framework_edges as FE         # noqa: E402
import graphify_extract as GX        # noqa: E402
import language_recognizers as LR    # noqa: E402
import csharp_edges as CS            # noqa: E402
import java_edges as JV              # noqa: E402
import go_edges as GO                # noqa: E402
import ruby_rails_edges as RR        # noqa: E402

_PASS = _FAIL = 0


def check(desc: str, cond: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    ok = bool(cond)
    _PASS += ok
    _FAIL += (not ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {desc}" + ("" if ok else f"  -- {detail}"))


class _Sess:
    def __init__(self):
        self.log: list = []

    def run(self, cypher, **params):
        for row in params.get("rows") or [params]:   # the push batches with UNWIND $rows: one entry per row
            self.log.append((cypher, row))


def _push(nodes, edges):
    """-> [(edge_type, src_key, dst_key, confidence)] in DECLARING orientation (src = a)."""
    s = _Sess()
    ER.push_typed_edges(s, edges, ER.Resolver(nodes), [n["key"] for n in nodes])
    out = []
    for c, p in s.log:
        if "MERGE" in c:
            et = c.split("[r:", 1)[1].split("]", 1)[0]
            out.append((et, p["a"], p["b"], p["c"]))
    return out


def _te(src, src_file, dst, et, hint, attrs, scope=""):
    """A TypedEdge, with a resolution scope when this build's TypedEdge carries one."""
    if "scope" in FE.TypedEdge._fields:
        return FE.TypedEdge(src, src_file, dst, et, hint, attrs, scope)
    return FE.TypedEdge(src, src_file, dst, et, hint, attrs)


def _fam(path: str) -> str:
    """The gate's own (independent) language-family oracle for the cross-language check."""
    ext = Path(path).suffix.lower()
    return {".rb": "ruby", ".erb": "ruby", ".go": "go", ".java": "jvm", ".kt": "jvm",
            ".py": "python", ".cs": "dotnet"}.get(ext, ext)


def _node(key, label, name, sf, line=1):
    return {"key": key, "label": label, "name": name, "source_file": sf, "start_line": line,
            "documented": False, "body_summary": f"{name}: {label.lower()}"}


def _recognize_tree(files: dict) -> tuple:
    """{rel: source} -> (supplemental_nodes, typed_edges) through each language's recognizer."""
    supp, edges = [], []
    by_ext = {".cs": CS, ".java": JV, ".go": GO, ".rb": RR, ".erb": RR}
    for rel, src in files.items():
        mod = by_ext.get(Path(rel).suffix.lower())
        if mod is None:
            continue
        n, e = mod.recognize(src, rel)
        supp.extend(LR.validate_supplemental(n, "gate"))
        edges.extend(e)
    return supp, edges


# ============================================================================================
# T1 — the file-Module / Class / constructor collision (real graphify graph)
# ============================================================================================
COLLISION_SRC = {
    "cs/Controllers/InvoicesController.cs": """using Billing.Models;
using Billing.Services;
using Microsoft.AspNetCore.Mvc;

namespace Billing.Controllers;

[ApiController]
[Route("api/[controller]")]
public class InvoicesController : ControllerBase
{
    private readonly IInvoiceService _service;

    public InvoicesController(IInvoiceService service)
    {
        _service = service;
    }

    [HttpGet("{id}")]
    public ActionResult<Invoice> GetInvoice(int id)
    {
        return _service.Find(id);
    }
}
""",
    "cs/Data/BillingContext.cs": """using Billing.Models;
using Microsoft.EntityFrameworkCore;

namespace Billing.Data;

public class BillingContext : DbContext
{
    public BillingContext(DbContextOptions<BillingContext> options) : base(options) { }

    public DbSet<Invoice> Invoices { get; set; }
}
""",
    "cs/Models/Invoice.cs": """namespace Billing.Models;

public class Invoice
{
    public int Id { get; set; }
    public decimal Total { get; set; }
}
""",
    "cs/Services/InvoiceService.cs": """using Billing.Data;
using Billing.Models;

namespace Billing.Services;

public interface IInvoiceService
{
    Invoice Find(int id);
}

public class InvoiceService : IInvoiceService
{
    private readonly BillingContext _db;

    public InvoiceService(BillingContext db)
    {
        _db = db;
    }

    public Invoice Find(int id)
    {
        return _db.Invoices.Find(id);
    }
}
""",
    "java/src/main/java/com/lib/Book.java": """package com.lib;

public class Book extends CatalogItem {
    private String title;

    public String getTitle() {
        return title;
    }
}
""",
    "java/src/main/java/com/lib/BookController.java": """package com.lib;

import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/books")
public class BookController {
    private final BookService bookService;

    public BookController(BookService bookService) {
        this.bookService = bookService;
    }

    @GetMapping("/{id}")
    public Book getBook(@PathVariable Long id) {
        return bookService.find(id);
    }
}
""",
    "java/src/main/java/com/lib/BookRepository.java": """package com.lib;

import org.springframework.data.jpa.repository.JpaRepository;

public interface BookRepository extends JpaRepository<Book, Long> {
}
""",
    "java/src/main/java/com/lib/BookService.java": """package com.lib;

import org.springframework.stereotype.Service;

@Service
public class BookService {
    private final BookRepository repository;

    public BookService(BookRepository repository) {
        this.repository = repository;
    }

    public Book find(Long id) {
        return repository.findById(id).orElseThrow();
    }
}
""",
    "java/src/main/java/com/lib/CatalogItem.java": """package com.lib;

public abstract class CatalogItem {
    protected String sku;

    public String getSku() {
        return sku;
    }
}
""",
    "java/src/main/java/com/lib/FlatPricingPolicy.java": """package com.lib;

public class FlatPricingPolicy implements PricingPolicy {
    public long priceCents(Book book) {
        return 1999L;
    }
}
""",
    "java/src/main/java/com/lib/PricingPolicy.java": """package com.lib;

public interface PricingPolicy {
    long priceCents(Book book);
}
""",
}


def gate_t1_collision() -> None:
    print("\n-- T1 (#57): file-Module/Class/ctor collision -> ONE :Class-anchored edge per fact --")
    fx = json.loads((REPO / "tests" / "fixtures" / "graphify_multilang" / "csharp_java_collision.json")
                    .read_text(encoding="utf-8"))
    base, _, _ = GX.map_graph(fx)
    by_key = {n["key"]: n for n in base}
    collide = {(n["name"], n["source_file"]) for n in base if n["label"] == "Module"} & \
              {(n["name"], n["source_file"]) for n in base if n["label"] == "Class"}
    check("FIXTURE ground truth: graphify emits a Module AND a Class for every file-per-class type",
          len(collide) >= 9, str(sorted(collide)))
    check("FIXTURE ground truth: Java constructors are Methods named X.X",
          {"BookController.BookController", "BookService.BookService"}
          <= {n["name"] for n in base if n["label"] == "Method"})

    supp, edges = _recognize_tree(COLLISION_SRC)
    nodes = LR.merge_supplemental(base, supp)
    rows = _push(nodes, edges)

    def lab(k):
        return by_key.get(k, {}).get("label")

    def name(k):
        return by_key.get(k, {}).get("name")

    def facts(et):
        return sorted((name(a), name(b), c) for t, a, b, c in rows if t == et)

    check("no typed edge is AMBIGUOUS (every relationship here names exactly one type)",
          not [r for r in rows if r[3] == "AMBIGUOUS"],
          str([(r[0], name(r[1]), name(r[2])) for r in rows if r[3] == "AMBIGUOUS"]))
    type_level = {"DEPENDS_ON", "INHERITS", "IMPLEMENTS", "HAS_MANY", "REFERENCES", "CASCADES"}
    check("every type-level edge joins :Class -> :Class (never the file Module, never a ctor Method)",
          all(lab(a) == "Class" and lab(b) == "Class" for t, a, b, _ in rows if t in type_level),
          str([(t, lab(a), lab(b)) for t, a, b, _ in rows if t in type_level
               and not (lab(a) == lab(b) == "Class")]))
    check("DEPENDS_ON: exactly the 4 declared constructor injections, once each, INFERRED",
          facts("DEPENDS_ON") == [("BookController", "BookService", "INFERRED"),
                                  ("BookService", "BookRepository", "INFERRED"),
                                  ("InvoiceService", "BillingContext", "INFERRED"),
                                  ("InvoicesController", "IInvoiceService", "INFERRED")],
          str(facts("DEPENDS_ON")))
    check("INHERITS: Book -> CatalogItem once (INFERRED); external bases (ControllerBase/DbContext) drop",
          facts("INHERITS") == [("Book", "CatalogItem", "INFERRED")], str(facts("INHERITS")))
    check("IMPLEMENTS: FlatPricingPolicy -> PricingPolicy (INFERRED) + same-file "
          "InvoiceService -> IInvoiceService (EXTRACTED), once each",
          facts("IMPLEMENTS") == [("FlatPricingPolicy", "PricingPolicy", "INFERRED"),
                                  ("InvoiceService", "IInvoiceService", "EXTRACTED")],
          str(facts("IMPLEMENTS")))
    check("HAS_MANY: DbSet<Invoice> -> BillingContext -> Invoice once, Class -> Class",
          facts("HAS_MANY") == [("BillingContext", "Invoice", "INFERRED")], str(facts("HAS_MANY")))
    routes = [(lab(a), name(a), name(b), c) for t, a, b, c in rows if t == "ROUTES_TO"]
    check("ROUTES_TO: one edge per action, owned by the controller :Class (not its Module, "
          "not the constructor) and EXTRACTED",
          sorted(routes) == [("Class", "BookController", "BookController.getBook", "EXTRACTED"),
                             ("Class", "InvoicesController", "InvoicesController.GetInvoice",
                              "EXTRACTED")], str(routes))
    check("a constructor Method is never an edge endpoint",
          not [r for r in rows if name(r[1]) in ("BookController.BookController",
                                                 "BookService.BookService")
               or name(r[2]) in ("BookController.BookController", "BookService.BookService")],
          str(rows))

    # The same owner-preference rule removes the Go `main.go` duplicate: Module 'main' + func main.
    gn = [_node("m", "Module", "main", "go/main.go"), _node("f", "Function", "main", "go/main.go", 9),
          _node("h", "Function", "health", "go/main.go", 20)]
    ge = [FE.TypedEdge("main", "go/main.go", "health", "ROUTES_TO", "LOCAL", {"method": "GET"})]
    r2 = _push(gn, ge)
    check("a Go route in main.go is owned by the file Module only (not also by `func main`)",
          r2 == [("ROUTES_TO", "m", "h", "EXTRACTED")], str(r2))

    # Python is untouched: FastAPI DEPENDS_ON is function-level (handler -> provider function).
    pn = [_node("pm", "Module", "api", "app/api.py"),
          _node("ph", "Function", "read_items", "app/api.py", 5),
          _node("pp", "Function", "get_db", "app/deps.py", 3)]
    pe = [FE.TypedEdge("read_items", "app/api.py", "get_db", "DEPENDS_ON", "LOCAL", {})]
    r3 = _push(pn, pe)
    check("Python DEPENDS_ON (handler function -> provider function) still resolves (not type-level)",
          r3 == [("DEPENDS_ON", "ph", "pp", "INFERRED")], str(r3))


# ============================================================================================
# T2 — language family
# ============================================================================================
RAILS_MODELS = {
    "app/models/account.rb": "class Account < ApplicationRecord\n"
                             "  has_many :orders, dependent: :destroy\nend\n",
    "app/models/order.rb": "class Order < ApplicationRecord\n  belongs_to :account\n"
                           "  has_many :line_items, dependent: :destroy\nend\n",
    "app/models/line_item.rb": "class LineItem < ApplicationRecord\n  belongs_to :order\nend\n",
    "app/models/application_record.rb": "class ApplicationRecord < ActiveRecord::Base\nend\n",
}
POLYGLOT_EXTRA = {
    "go/models/line_item.go": "package models\n\ntype LineItem struct {\n\tID  uint\n\tSku string\n}\n",
    "java/src/main/java/com/shop/Order.java": "package com.shop;\n\n@Entity\npublic class Order {\n"
                                              "    private Long id;\n}\n",
}


def gate_t2_language_family() -> None:
    print("\n-- T2 (#61): typed-edge resolution stays inside the source's language family --")
    lf = getattr(ER, "lang_family", None)
    check("lang_family: .rb/.erb/.RB are ruby; .java+.kt one jvm family; .go go; unknown -> None",
          lf is not None and lf("a/b.rb") == lf("v/x.html.erb") == lf("M/ORDER.RB") == "ruby"
          and lf("A.java") == lf("B.kt") == "jvm" and lf("x.go") == "go"
          and lf("README") is None and lf("data.xyz") is None)

    ctrl_supp, ctrl_edges = _recognize_tree(RAILS_MODELS)
    ctrl = sorted((t, a, b, c) for t, a, b, c in _push(ctrl_supp, ctrl_edges))
    files = dict(RAILS_MODELS)
    files.update(POLYGLOT_EXTRA)
    poly_supp, poly_edges = _recognize_tree(files)
    # + a Python `Base` (the `ActiveRecord::Base` INHERITS variant seen live)
    poly_nodes = poly_supp + [_node("py_base", "Class", "Base", "pyapp/service.py", 3)]
    poly = sorted(_push(poly_nodes, poly_edges))
    fam = {n["key"]: _fam(n["source_file"]) for n in poly_nodes}
    cross = [r for r in poly if fam.get(r[1]) != fam.get(r[2])]
    check("no typed edge crosses a language boundary (Rails -> Go/Java/Python)", not cross, str(cross))
    check("the polyglot repo's Rails edges are EXACTLY the Rails-only control's (same grades)",
          poly == ctrl, f"poly={poly}\nctrl={ctrl}")
    check("control sanity: the Rails edges are all INFERRED and include the two cascades",
          ctrl and all(r[3] == "INFERRED" for r in ctrl)
          and sum(1 for r in ctrl if r[0] == "CASCADES") == 2, str(ctrl))

    # EF third-file global-src fallback: a Java class of the same name must not make the C# src
    # "ambiguous" (dropping the cascade) — the fallback is judged inside the C# family.
    nodes = [_node("cs_o", "Class", "Order", "Models/Order.cs", 3),
             _node("cs_l", "Class", "LineItem", "Models/LineItem.cs", 3),
             _node("cs_ctx", "Class", "ShopContext", "Data/ShopContext.cs", 6),
             _node("jv_o", "Class", "Order", "java/Order.java", 4)]
    e = FE.TypedEdge("Order", "Data/ShopContext.cs", "LineItem", "CASCADES", "NAME", {"via": "fluent"})
    r = _push(nodes, [e])
    check("C# fluent cascade still resolves its global src when a Java `Order` also exists",
          r == [("CASCADES", "cs_o", "cs_l", "INFERRED")], str(r))


# ============================================================================================
# T3 — Rails resources only:/except:
# ============================================================================================
ROUTES_OE = """Rails.application.routes.draw do
  resources :orders, only: [:index, :show, :create]
  resources :invoices, except: [:destroy, :edit]
  resource :profile, only: :show
  resources :widgets, only: [] do
    member do
      get :publish
    end
  end
  resources :legacy_items, :only => %i[index show]
  resources :reports, only: [
    :index,
    :show,
  ]
  resources :exports, only: EXPORT_ACTIONS
  resources :payments, controller: 'billing/charges', only: [:create]
end
"""
CONTROLLERS = {
    "OrdersController": ("index", "show", "create", "destroy"),
    "InvoicesController": ("index", "show", "new", "create", "edit", "update", "destroy"),
    "ProfilesController": ("show", "edit"),
    "WidgetsController": ("publish", "destroy"),
    "LegacyItemsController": ("index", "show", "destroy"),
    "ReportsController": ("index", "show", "destroy"),
    "ExportsController": ("index", "destroy"),
    "ChargesController": ("create", "index"),
}


def gate_t3_rails_only_except() -> None:
    print("\n-- T3 (#60): Rails resources only:/except: never route an excluded action --")
    rnodes, redges = RR.recognize(ROUTES_OE, "config/routes.rb")
    ro = [e for e in redges if e.edge_type == "ROUTES_TO"]
    emitted = {e.dst_name for e in ro}
    check("only: [:index, :show, :create] -> exactly those 3 OrdersController actions",
          {d for d in emitted if d.startswith("OrdersController.")}
          == {"OrdersController.index", "OrdersController.show", "OrdersController.create"},
          str(sorted(emitted)))
    check("except: [:destroy, :edit] -> the other 5 InvoicesController actions",
          {d for d in emitted if d.startswith("InvoicesController.")}
          == {f"InvoicesController.{a}" for a in ("index", "show", "new", "create", "update")},
          str(sorted(emitted)))
    check("singular `resource :profile, only: :show` -> ProfilesController.show (the PLURAL controller)",
          {d for d in emitted if "Profile" in d} == {"ProfilesController.show"}, str(sorted(emitted)))
    check("`resources :widgets, only: [] do member ... end` -> only the member route, no REST actions",
          {d for d in emitted if d.startswith("WidgetsController.")} == {"WidgetsController.publish"},
          str(sorted(emitted)))
    check("legacy `:only => %i[index show]` form is honored",
          {d for d in emitted if d.startswith("LegacyItemsController.")}
          == {"LegacyItemsController.index", "LegacyItemsController.show"}, str(sorted(emitted)))
    check("a multi-line `only: [` list is honored",
          {d for d in emitted if d.startswith("ReportsController.")}
          == {"ReportsController.index", "ReportsController.show"}, str(sorted(emitted)))
    dyn = [e for e in ro if e.dst_name.startswith("ExportsController.")]
    check("an unparseable `only: CONSTANT` keeps the actions but grades them DYNAMIC (-> AMBIGUOUS)",
          len(dyn) == 7 and all(e.conf_hint == "DYNAMIC" for e in dyn), str(dyn[:2]))
    check("`controller: 'billing/charges'` routes to ChargesController, not PaymentsController",
          {d for d in emitted if "Charges" in d or "Payments" in d} == {"ChargesController.create"},
          str(sorted(emitted)))

    ctrl_nodes = [_node(f"ctl:{c}.{a}", "Method", f"{c}.{a}",
                        f"app/controllers/{c.lower()}.rb", 2)
                  for c, acts in CONTROLLERS.items() for a in acts]
    nodes = LR.validate_supplemental(rnodes, "ruby") + ctrl_nodes
    rows = _push(nodes, redges)
    by_key = {n["key"]: n for n in nodes}
    made = {by_key[b]["name"]: c for t, a, b, c in rows if t == "ROUTES_TO"}
    for bad in ("OrdersController.destroy", "InvoicesController.destroy", "InvoicesController.edit",
                "WidgetsController.destroy", "LegacyItemsController.destroy",
                "ReportsController.destroy", "ProfilesController.edit", "ChargesController.index"):
        check(f"no fabricated route to the unrouted {bad}", bad not in made, str(made.get(bad)))
    check("the routed actions resolve INFERRED (index/show/create, publish, ProfilesController.show)",
          all(made.get(k) == "INFERRED" for k in ("OrdersController.index", "OrdersController.create",
                                                  "WidgetsController.publish",
                                                  "ProfilesController.show",
                                                  "ChargesController.create")), str(made))
    check("the unparseable-option routes persist AMBIGUOUS (verify by reading)",
          made.get("ExportsController.index") == "AMBIGUOUS"
          and made.get("ExportsController.destroy") == "AMBIGUOUS", str(made))


# ============================================================================================
# T4 — Go handlers: qualified, method-value, other-file same-package
# ============================================================================================
GO_MAIN = """package main

import (
\t"net/http"

\t"github.com/gin-gonic/gin"
\t"polyshop/go/handlers"
)

func main() {
\tr := gin.Default()
\th := handlers.NewCustomerHandler(&handlers.CustomerStore{})
\tr.GET("/customers", handlers.ListCustomers)
\tr.GET("/customers/:id", h.GetCustomer)
\tr.POST("/customers", createCustomer)
\tr.GET("/users", listUsers)
\tr.GET("/any", x.Handle)
\tr.GET("/missing", http.NotFound)
\tr.Run(":8080")
}

func createCustomer(c *gin.Context) {
}
"""


def _go_nodes():
    return [
        _node("mod_main", "Module", "main", "go/main.go"),
        _node("fn_main", "Function", "main", "go/main.go", 10),
        _node("fn_create", "Function", "createCustomer", "go/main.go", 22),
        _node("fn_listusers", "Function", "listUsers", "go/users.go", 5),       # same package, other file
        _node("mod_ch", "Module", "customer_handler", "go/handlers/customer_handler.go"),
        _node("cls_store", "Class", "CustomerStore", "go/handlers/customer_handler.go", 11),
        _node("cls_ch", "Class", "CustomerHandler", "go/handlers/customer_handler.go", 18),
        _node("fn_new", "Function", "NewCustomerHandler", "go/handlers/customer_handler.go", 23),
        _node("fn_list", "Function", "ListCustomers", "go/handlers/customer_handler.go", 28),
        _node("m_get", "Method", "CustomerHandler.GetCustomer", "go/handlers/customer_handler.go", 33),
        # decoys: same names in ANOTHER package / as methods — must never be picked
        _node("fn_list_admin", "Function", "ListCustomers", "go/admin/customers.go", 7),
        _node("fn_listusers_admin", "Function", "listUsers", "go/admin/users.go", 4),
        _node("m_list_other", "Method", "Report.ListCustomers", "go/handlers/report.go", 9),
        _node("m_handle_a", "Method", "Mux.Handle", "go/web/mux.go", 12),
        _node("m_handle_b", "Method", "Router.Handle", "go/web/router.go", 30),
        _node("py_get", "Method", "Api.GetCustomer", "pyapp/api.py", 4),
    ]


def gate_t4_go_handlers() -> None:
    print("\n-- T4 (#59/#102): Go route handlers resolve by PACKAGE (qualified / method value / sibling) --")
    _, edges = GO.recognize(GO_MAIN, "go/main.go")
    nodes = _go_nodes()
    rows = _push(nodes, edges)
    got = {(b, c) for t, a, b, c in rows if t == "ROUTES_TO"}
    check("handlers.ListCustomers -> the imported package's func ListCustomers (INFERRED)",
          ("fn_list", "INFERRED") in got, str(sorted(got)))
    check("h.GetCustomer (h := handlers.NewCustomerHandler(...)) -> CustomerHandler.GetCustomer",
          ("m_get", "INFERRED") in got, str(sorted(got)))
    check("same-file bare handler createCustomer stays EXTRACTED", ("fn_create", "EXTRACTED") in got,
          str(sorted(got)))
    check("bare handler listUsers defined in a SIBLING file of the package resolves (INFERRED)",
          ("fn_listusers", "INFERRED") in got, str(sorted(got)))
    check("never another package's same-named func, a same-named method, or a Python method",
          not ({b for b, _ in got} & {"fn_list_admin", "fn_listusers_admin", "m_list_other",
                                      "py_get"}), str(sorted(got)))
    check("unknown receiver x.Handle -> the Go *.Handle methods only, graded AMBIGUOUS",
          {("m_handle_a", "AMBIGUOUS"), ("m_handle_b", "AMBIGUOUS")} <= got, str(sorted(got)))
    check("a stdlib handler (http.NotFound) emits no edge at all",
          not any(e.attrs.get("handler") == "http.NotFound" for e in edges), str(edges))
    check("every Go route is owned once, by the file Module (never also by `func main`)",
          {a for t, a, b, c in rows if t == "ROUTES_TO"} == {"mod_main"},
          str({a for t, a, b, c in rows}))
    check("the raw qualified handler is kept in the edge attrs for the reader",
          any(e.attrs.get("handler") == "handlers.ListCustomers" for e in edges))

    # gin@34dac209 shape: routes_test.go registers handlerTest1, defined once in gin_test.go.
    rt = ('package gin\n\nfunc TestRoutes(t *testing.T) {\n\trouter := New()\n'
          '\trouter.GET("/metrics", handlerTest1)\n}\n')
    _, e2 = GO.recognize(rt, "routes_test.go")
    n2 = [_node("gt", "Module", "gin_test", "gin_test.go"),
          _node("rtm", "Module", "routes_test", "routes_test.go"),
          _node("h1", "Function", "handlerTest1", "gin_test.go", 990)]
    r2 = _push(n2, e2)
    check("gin: a route in routes_test.go reaches handlerTest1 in gin_test.go (same package)",
          r2 == [("ROUTES_TO", "rtm", "h1", "INFERRED")], str(r2))


# ============================================================================================
# T5 — Go stdlib-qualified / cross-package types
# ============================================================================================
GO_ROUTER = """package httprouter

import (
\t"context"
\t"net/http"
)

type Router struct {
\tGlobalOPTIONS    http.Handler
\tNotFound         http.Handler
\tMethodNotAllowed http.Handler
\tPanicHandler     func(http.ResponseWriter, *http.Request, interface{})
}

func (r *Router) Handler(method, path string, handler http.Handler) {
}

func NewServer(h http.Handler, ctx context.Context) *Server {
\treturn nil
}

type Server struct {
\thttp.Handler
}
"""
GO_ORDER = """package models

import "polyshop/go/billing"

type Order struct {
\tCustomer  Customer
\tInvoice   billing.Invoice
\tLineItems []LineItem
}
"""


def gate_t5_go_type_refs() -> None:
    print("\n-- T5 (#103): Go type refs never bind a stdlib type to a repo symbol; never to a callable --")
    _, edges = GO.recognize(GO_ROUTER, "router.go")
    check("the recognizer emits NO edge for a stdlib-qualified type (http.Handler fields/params/embed)",
          not [e for e in edges if e.dst_name in ("Handler", "ResponseWriter", "Request")],
          str(edges))
    nodes = [_node("cls_router", "Class", "Router", "router.go", 8),
             _node("m_handler", "Method", "Router.Handler", "router.go", 15),
             _node("cls_server", "Class", "Server", "router.go", 22),
             _node("mux_handler", "Method", "Mux.Handler", "mux.go", 3)]
    rows = _push(nodes, edges)
    check("httprouter: no REFERENCES/DEPENDS_ON/INHERITS lands on the Router.Handler method",
          not [r for r in rows if r[2] in ("m_handler", "mux_handler")], str(rows))
    # A snippet with no import block: `http` is still recognized as the stdlib package.
    _, e_noimp = GO.recognize("package x\n\ntype A struct {\n\tH http.Handler\n}\n", "a.go")
    check("no import block: a stdlib package NAME (http.Handler) is still never emitted",
          not e_noimp, str(e_noimp))

    # A type-level edge never binds a callable, even when it is the only same-named symbol.
    tnodes = [_node("srv", "Class", "Server", "store/server.go", 3),
              _node("cache_store", "Method", "Cache.Store", "store/server.go", 20)]
    te = [_te("Server", "store/server.go", "Store", "REFERENCES", "NAME", {"field": "Store"},
              "package")]
    check("a bare Go type whose only same-named symbol is a Method -> dropped (never a callable)",
          _push(tnodes, te) == [], str(_push(tnodes, te)))
    cs_nodes = [_node("c1", "Class", "Holder", "Models/Holder.cs", 3),
                _node("c2", "Method", "Util.Payment", "Util/Util.cs", 9)]
    ce = [FE.TypedEdge("Holder", "Models/Holder.cs", "Payment", "REFERENCES", "NAME",
                       {"property": "Payment"})]
    check("any language: a REFERENCES whose only candidate is a Method is dropped (type-level role)",
          _push(cs_nodes, ce) == [], str(_push(cs_nodes, ce)))

    # Package-qualified repo types resolve into the imported package, never a same-named decoy.
    _, oe = GO.recognize(GO_ORDER, "go/models/order.go")
    onodes = [_node("ord", "Class", "Order", "go/models/order.go", 5),
              _node("cust", "Class", "Customer", "go/models/customer.go", 3),
              _node("li", "Class", "LineItem", "go/models/line_item.go", 3),
              _node("inv", "Class", "Invoice", "go/billing/invoice.go", 3),
              _node("inv_decoy", "Class", "Invoice", "go/legacy/invoice.go", 3),
              _node("cust_decoy", "Class", "Customer", "go/crm/customer.go", 3)]
    orows = sorted((t, b, c) for t, a, b, c in _push(onodes, oe))
    check("bare Customer/LineItem resolve in the SAME package; billing.Invoice in the imported one",
          orows == [("HAS_MANY", "li", "INFERRED"), ("REFERENCES", "cust", "INFERRED"),
                    ("REFERENCES", "inv", "INFERRED")], str(orows))


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 72)
    print("DeepMap — typed-edge resolution gates (T1-T5, 2026-09 live validation; no Neo4j)")
    print("=" * 72)
    for gate in (gate_t1_collision, gate_t2_language_family, gate_t3_rails_only_except,
                 gate_t4_go_handlers, gate_t5_go_type_refs):
        try:
            gate()
        except Exception as exc:  # a crash on the pre-fix code is a RED result, not a harness error
            check(f"{gate.__name__} ran without raising", False, f"{type(exc).__name__}: {exc}")
    print("\n" + "=" * 72)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
