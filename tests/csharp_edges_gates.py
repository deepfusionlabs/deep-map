#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gates for the C#/.NET recognizer (plugin #3 of the language-recognizer registry). No Neo4j, no
Roslyn/dotnet — the recognizer is a stdlib-only heuristic line/regex reader, like the Ruby plugin.

The field failure this locks out: on a .NET project (two EF Core DbContexts) the declarative wiring
was invisible to `deepmap_impact` — EF navigation properties, the fluent API (`HasMany/WithOne/
OnDelete`), attribute routes, and DI registrations are all *declarations*, not calls, so graphify
yields no relationship for them (ground truth frozen in tests/fixtures/graphify_multilang/
csharp_ef.json: real captured `graphify update --no-cluster` output — its links carry NO `calls`,
and map_graph reads none of graphify's `inherits`/`implements`/`references` relations). These gates
lock:

  - recognizer emit correctness over REAL C#/EF source: INHERITS / IMPLEMENTS (I-convention split;
    interface bases are INHERITS), DbSet<T> + collection/reference navigation properties ->
    HAS_MANY / REFERENCES (BCL/scalar property types are NEVER emitted), the fluent API ->
    HAS_MANY / REFERENCES / CASCADES incl. the HasOne/WithMany principal inversion and the
    HasForeignKey<T> one-to-one disambiguation, DeleteBehavior semantics (Cascade/ClientCascade/
    SetNull/ClientSetNull yes; Restrict/NoAction NO), attribute routes -> ROUTES_TO (same-file
    strict) with [controller]/[action] substitution, AddScoped/AddSingleton/AddTransient ->
    DEPENDS_ON, // and /* */ comments skipped, per-construct resilience;
  - the PascalCase singularizer (fluent lambda property -> element type) and the failure mode:
    a wrong guess -> unresolvable name -> DROPPED edge, never a fabricated one;
  - ensured :Class nodes byte-match graphify's C# name shapes (BARE names under BOTH namespace
    forms — verified against the CAPTURED csharp_ef.json), so merge_supplemental dedupes them when
    graphify parsed the file and they fill the hole when it didn't (the ast fallback is
    Python-only, so a graphify-less C# repo has NO base nodes at all);
  - the EF third-file shape: the fluent relationship for Order<->LineItem is declared in
    ShopContext.cs where NEITHER endpoint is a node — edge_resolve's new SINGLE-CANDIDATE global
    src fallback (GLOBAL_SRC_FALLBACK, literal-locked) resolves it at INFERRED-at-best; an
    ambiguous global src DROPS (never fan out, never fabricate); non-fallback types keep the
    same-file-strict src discipline byte-unchanged;
  - CASCADES persists REVERSED — (dependent)-[:CASCADES]->(principal) — so impact("Order") lists
    LineItem (the field user's exact question), via the SAME REVERSED_EDGE_TYPES mechanics as Ruby;
  - end-to-end registry: LR.run over a C# tree; bin/obj/vendored dirs never contribute;
    DEEPMAP_RECOGNIZERS=0 replays the pre-plugin field state.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build"
sys.path.insert(0, str(BUILD))

import csharp_edges as CS           # noqa: E402
import language_recognizers as LR   # noqa: E402
import edge_resolve as ER           # noqa: E402
import graphify_extract as GX       # noqa: E402
import framework_edges as FE        # noqa: E402

_PASS = _FAIL = 0


def check(desc: str, cond: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    ok = bool(cond)
    _PASS += ok
    _FAIL += (not ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {desc}" + ("" if ok else f"  -- {detail}"))


# ---- the EXACT sources of the frozen capture (line numbers align with csharp_ef.json) ----------
ORDER_CS = """namespace Shop.Models;

public class Order
{
    public int Id { get; set; }
    public int CustomerId { get; set; }
    public Customer Customer { get; set; }
    public List<LineItem> LineItems { get; set; } = new();
}
"""

LINEITEM_CS = """namespace Shop.Models;

public class LineItem
{
    public int Id { get; set; }
    public int OrderId { get; set; }
    public Order Order { get; set; }

    public decimal TotalPrice()
    {
        return 0m;
    }
}
"""

LEGACY_CS = """using System.Collections.Generic;

namespace Shop.Legacy
{
    public class AuditEntry
    {
        public int Id { get; set; }
        public string Action { get; set; }
    }

    public class Customer
    {
        public int Id { get; set; }
        public List<Order> Orders { get; set; }
    }
}
"""

SHOPCONTEXT_CS = """using Microsoft.EntityFrameworkCore;
using Shop.Models;

namespace Shop.Data;

public class ShopContext : DbContext
{
    public DbSet<Order> Orders { get; set; }
    public DbSet<LineItem> LineItems { get; set; }

    protected override void OnModelCreating(ModelBuilder modelBuilder)
    {
        modelBuilder.Entity<Order>()
            .HasMany(o => o.LineItems)
            .WithOne(li => li.Order)
            .HasForeignKey(li => li.OrderId)
            .OnDelete(DeleteBehavior.Cascade);
    }
}
"""

CONTROLLER_CS = """using Microsoft.AspNetCore.Mvc;
using Shop.Models;

namespace Shop.Controllers;

[ApiController]
[Route("api/[controller]")]
public class OrdersController : ControllerBase
{
    [HttpGet("{id}")]
    public Order GetOrder(int id)
    {
        return null;
    }

    [HttpPost]
    public void CreateOrder()
    {
    }
}
"""

SERVICE_CS = """namespace Shop.Services;

public interface IOrderService
{
    void Place(int orderId);
}

public class OrderService : IOrderService
{
    public void Place(int orderId)
    {
    }
}
"""

PROGRAM_CS = """using Shop.Services;

var builder = WebApplication.CreateBuilder(args);
builder.Services.AddScoped<IOrderService, OrderService>();
var app = builder.Build();
app.Run();
"""

FLUENT_ONELINE_CS = """using Microsoft.EntityFrameworkCore;

namespace Shop.Data;

public class InlineContext : DbContext
{
    protected override void OnModelCreating(ModelBuilder b)
    {
        b.Entity<Order>().HasMany(o => o.LineItems).WithOne(li => li.Order).HasForeignKey(li => li.OrderId).OnDelete(DeleteBehavior.Cascade);
        b.Entity<Order>().HasOne(o => o.Customer).WithMany(c => c.Orders).OnDelete(DeleteBehavior.Cascade);
        b.Entity<Invoice>().HasOne(i => i.Order).WithOne(o => o.Invoice).HasForeignKey<Invoice>(i => i.OrderId).OnDelete(DeleteBehavior.Cascade);
        b.Entity<Order>().HasMany(o => o.AuditEntries).WithOne().OnDelete(DeleteBehavior.Restrict);
        b.Entity<Order>().HasMany(o => o.Payments).WithOne().OnDelete(DeleteBehavior.SetNull);
    }
}
"""


MINIMAL_API_CS = """using Shop.Services;

var builder = WebApplication.CreateBuilder(args);
builder.Services.AddScoped<IOrderService, OrderService>();
var app = builder.Build();

app.MapGet("/orders", GetAllOrders);
app.MapPost("/orders", OrderEndpoints.Create);
app.MapDelete("/orders/{id}", DeleteOrder);
app.MapGet("/health", () => "ok");

var api = app.MapGroup("/api");
api.MapGet("/orders", GetGroupedOrders);
app.MapGroup("/v2").MapGet("/things", GetThings);

Order GetAllOrders() => null;
void DeleteOrder(int id) { }

app.Run();
"""

LAMBDA_BLOCK_CS = """using Microsoft.EntityFrameworkCore;

namespace Shop.Data;

public class BlockContext : DbContext
{
    protected override void OnModelCreating(ModelBuilder modelBuilder)
    {
        modelBuilder.Entity<Order>(entity =>
        {
            entity.HasMany(o => o.LineItems).WithOne(li => li.Order).OnDelete(DeleteBehavior.Cascade);
            entity.HasOne(o => o.Customer).WithMany(c => c.Orders).OnDelete(DeleteBehavior.Cascade);
        });
    }
}
"""

CTOR_DI_CS = """namespace Shop.Services;

public class OrderService
{
    private readonly IOrderRepo _repo;
    private readonly ILogger<OrderService> _logger;

    public OrderService(IOrderRepo repo, ILogger<OrderService> logger, int retries)
    {
        _repo = repo;
        _logger = logger;
    }

    public void Place(int orderId)
    {
    }
}
"""

PRIMARY_CTOR_DI_CS = """namespace Shop.Services;

public class InvoiceService(IInvoiceRepo repo, IClock clock)
{
    public void Issue() { }
}
"""


def _edges(src, rel):
    return CS.recognize(src, rel)[1]


def _by_type(edges, t):
    return [e for e in edges if e.edge_type == t]


def gate_recognizer_emit() -> None:
    print("\n-- recognizer emit: nav props / DbSet / fluent / routes / DI / inheritance --")

    # Entity file: collection + reference navigation; scalar/BCL props NEVER emitted.
    _, e_order = CS.recognize(ORDER_CS, "Models/Order.cs")
    hm = _by_type(e_order, "HAS_MANY")
    check("collection nav List<LineItem> -> HAS_MANY Order -> LineItem",
          len(hm) == 1 and hm[0].src_name == "Order" and hm[0].dst_name == "LineItem"
          and hm[0].attrs.get("property") == "LineItems", str(hm))
    refs = _by_type(e_order, "REFERENCES")
    check("reference nav Customer -> REFERENCES Order -> Customer",
          len(refs) == 1 and refs[0].dst_name == "Customer"
          and refs[0].attrs.get("property") == "Customer", str(refs))
    check("scalar/BCL props (int Id, int CustomerId) emit NOTHING",
          all(e.dst_name not in ("Id", "CustomerId", "int") for e in e_order), str(e_order))
    _, e_legacy = CS.recognize(LEGACY_CS, "Models/Legacy.cs")
    check("string prop emits NOTHING; List<Order> in a block namespace still HAS_MANY",
          not any(e.dst_name == "string" for e in e_legacy)
          and any(e.edge_type == "HAS_MANY" and e.src_name == "Customer"
                  and e.dst_name == "Order" for e in e_legacy), str(e_legacy))

    # DbContext: DbSet + the multi-line fluent chain.
    _, e_ctx = CS.recognize(SHOPCONTEXT_CS, "Data/ShopContext.cs")
    dbsets = [e for e in _by_type(e_ctx, "HAS_MANY") if e.attrs.get("via") == "DbSet"]
    check("DbSet<T> props -> HAS_MANY ShopContext -> Order/LineItem (via DbSet)",
          {(e.src_name, e.dst_name) for e in dbsets}
          == {("ShopContext", "Order"), ("ShopContext", "LineItem")}, str(dbsets))
    fl_hm = [e for e in _by_type(e_ctx, "HAS_MANY") if e.attrs.get("via") == "fluent"]
    check("fluent HasMany(o => o.LineItems) -> HAS_MANY Order -> LineItem (singularized lambda prop)",
          any(e.src_name == "Order" and e.dst_name == "LineItem" for e in fl_hm), str(fl_hm))
    cas = _by_type(e_ctx, "CASCADES")
    check("multi-line fluent chain: OnDelete(DeleteBehavior.Cascade) -> CASCADES Order -> LineItem",
          len(cas) == 1 and cas[0].src_name == "Order" and cas[0].dst_name == "LineItem"
          and cas[0].attrs.get("strategy") == "Cascade", str(cas))
    check("CASCADES emitted PRINCIPAL-anchored with the DECLARING file (the EF third-file shape)",
          cas[0].src_file == "Data/ShopContext.cs")
    check("ShopContext INHERITS DbContext (NAME — external, will drop at push)",
          any(e.edge_type == "INHERITS" and e.src_name == "ShopContext"
              and e.dst_name == "DbContext" and e.conf_hint == "NAME" for e in e_ctx), str(e_ctx))

    # One-line fluent chains + principal/dependent orientation.
    _, e_fl = CS.recognize(FLUENT_ONELINE_CS, "Data/InlineContext.cs")
    cmap = {(e.src_name, e.dst_name): e.attrs.get("strategy") for e in _by_type(e_fl, "CASCADES")}
    check("one-line chain: CASCADES Order -> LineItem (Cascade)",
          cmap.get(("Order", "LineItem")) == "Cascade", str(cmap))
    check("HasOne(...).WithMany(...) INVERTS: principal Customer -> dependent Order",
          cmap.get(("Customer", "Order")) == "Cascade", str(cmap))
    check("one-to-one via HasForeignKey<Invoice>: principal Order -> dependent Invoice",
          cmap.get(("Order", "Invoice")) == "Cascade", str(cmap))
    check("DeleteBehavior.Restrict is NOT a cascade (prevents deletion)",
          ("Order", "AuditEntry") not in cmap, str(cmap))
    check("DeleteBehavior.SetNull IS a cascade edge (strategy attr, mirrors Rails :nullify)",
          cmap.get(("Order", "Payment")) == "SetNull", str(cmap))

    # IEntityTypeConfiguration<T> — the dominant fluent style in larger codebases: chains in
    # Configure() carry NO Entity<T>() anchor; the interface's generic arg is the principal.
    config_cls = ("using Microsoft.EntityFrameworkCore;\n\n"
                  "public class OrderConfiguration : IEntityTypeConfiguration<Order>\n{\n"
                  "    public void Configure(EntityTypeBuilder<Order> builder)\n    {\n"
                  "        builder.HasMany(o => o.LineItems)\n"
                  "            .WithOne(li => li.Order)\n"
                  "            .OnDelete(DeleteBehavior.Cascade);\n    }\n}\n")
    _, e_cfg = CS.recognize(config_cls, "Data/OrderConfiguration.cs")
    cfg_cas = _by_type(e_cfg, "CASCADES")
    check("IEntityTypeConfiguration<Order>: anchor-less fluent chain -> CASCADES Order -> LineItem",
          len(cfg_cas) == 1 and cfg_cas[0].src_name == "Order"
          and cfg_cas[0].dst_name == "LineItem", str(cfg_cas))
    check("config-class chain also emits HAS_MANY Order -> LineItem",
          any(e.edge_type == "HAS_MANY" and e.src_name == "Order" and e.dst_name == "LineItem"
              for e in e_cfg), str(e_cfg))

    # Attribute routes.
    _, e_ctl = CS.recognize(CONTROLLER_CS, "Controllers/OrdersController.cs")
    routes = _by_type(e_ctl, "ROUTES_TO")
    rmap = {e.dst_name: (e.attrs.get("method"), e.attrs.get("path")) for e in routes}
    check("[HttpGet(template)] -> ROUTES_TO OrdersController.GetOrder with a composed path",
          rmap.get("OrdersController.GetOrder") == ("GET", "api/Orders/{id}"), str(rmap))
    check("[HttpPost] (no template) -> ROUTES_TO OrdersController.CreateOrder at the class route",
          rmap.get("OrdersController.CreateOrder") == ("POST", "api/Orders"), str(rmap))
    check("route src = the controller class; conf LOCAL (SAME_FILE_STRICT resolve-or-drop)",
          all(e.src_name == "OrdersController" and e.conf_hint == "LOCAL" for e in routes))
    check("controller INHERITS ControllerBase",
          any(e.edge_type == "INHERITS" and e.dst_name == "ControllerBase" for e in e_ctl))

    # Interface / implementation split.
    _, e_svc = CS.recognize(SERVICE_CS, "Services/OrderService.cs")
    impl = _by_type(e_svc, "IMPLEMENTS")
    check("I-convention base -> IMPLEMENTS OrderService -> IOrderService, LOCAL (declared same-file)",
          len(impl) == 1 and impl[0].src_name == "OrderService"
          and impl[0].dst_name == "IOrderService" and impl[0].conf_hint == "LOCAL", str(impl))
    _, e_iface = CS.recognize("public interface IAudited : IEntity\n{\n}\n", "a.cs")
    check("interface base list is INHERITS (interface inheritance), not IMPLEMENTS",
          any(e.edge_type == "INHERITS" and e.dst_name == "IEntity" for e in e_iface)
          and not _by_type(e_iface, "IMPLEMENTS"), str(e_iface))

    # DI registrations.
    _, e_prog = CS.recognize(PROGRAM_CS, "Program.cs")
    dep = _by_type(e_prog, "DEPENDS_ON")
    dmap = {e.dst_name: e.attrs.get("lifetime") for e in dep}
    check("AddScoped<IOrderService, OrderService> -> DEPENDS_ON on both the service and the impl",
          dmap.get("IOrderService") == "scoped" and dmap.get("OrderService") == "scoped", str(dmap))
    check("top-level-statement DI anchors on the file stem (Program — the Module node's name)",
          all(e.src_name == "Program" and e.src_file == "Program.cs" for e in dep), str(dep))
    startup = ("public class Startup\n{\n    public void ConfigureServices(IServiceCollection s)\n"
               "    {\n        s.AddSingleton<ICache, MemoryCache>();\n"
               "        s.AddTransient(typeof(IRepo<>), typeof(Repo<>));\n    }\n}\n")
    _, e_st = CS.recognize(startup, "Startup.cs")
    dep_st = _by_type(e_st, "DEPENDS_ON")
    check("DI inside a class anchors on the class; typeof(...) + open-generic forms parse",
          {(e.src_name, e.dst_name, e.attrs.get("lifetime")) for e in dep_st}
          == {("Startup", "ICache", "singleton"), ("Startup", "MemoryCache", "singleton"),
              ("Startup", "IRepo", "transient"), ("Startup", "Repo", "transient")}, str(dep_st))

    # Comment discipline + resilience.
    commented = ("public class X\n{\n    // public List<Ghost> Ghosts { get; set; }\n"
                 "    /* public DbSet<Phantom> Phantoms { get; set; }\n"
                 "       .OnDelete(DeleteBehavior.Cascade); */\n"
                 "    public List<Item> Items { get; set; }\n}\n\x00garbage")
    n2, e2 = CS.recognize(commented, "x.cs")
    check("// and /* */ commented DSL ignored; malformed bytes never raise",
          {e.dst_name for e in e2 if e.edge_type == "HAS_MANY"} == {"Item"}
          and len(n2) == 1, f"{[(e.edge_type, e.dst_name) for e in e2]}")


def gate_comment_string_honesty() -> None:
    print("\n-- comment/string honesty: comment text must never fabricate; banners must not blank --")
    ctx_body = ("using Microsoft.EntityFrameworkCore;\n\npublic class ShopContext : DbContext\n{\n"
                "    public DbSet<Order> Orders { get; set; }\n\n"
                "    protected override void OnModelCreating(ModelBuilder b)\n    {\n"
                "        b.Entity<Order>().HasMany(o => o.Items).WithOne(i => i.Order)"
                ".OnDelete(DeleteBehavior.Cascade);\n    }\n}\n")
    banner = "//" + "*" * 40 + "\n"
    n_b, e_b = CS.recognize(banner + ctx_body, "Data/ShopContext.cs")
    check("a '//***' banner line does NOT blank the rest of the file (nodes + cascade survive)",
          len(n_b) == 1 and any(e.edge_type == "CASCADES" for e in e_b),
          f"nodes={len(n_b)} edges={[(e.edge_type, e.dst_name) for e in e_b]}")

    todo = ("public class C : DbContext\n{\n"
            "    protected override void OnModelCreating(ModelBuilder b)\n    {\n"
            "        b.Entity<Order>().HasMany(o => o.Items).WithOne(i => i.Order); "
            "// TODO: add .OnDelete(DeleteBehavior.Cascade) once soft-delete lands\n    }\n}\n")
    _, e_t = CS.recognize(todo, "c.cs")
    check("'.OnDelete(...)' inside a trailing // comment fabricates NO cascade",
          not _by_type(e_t, "CASCADES"), str(_by_type(e_t, "CASCADES")))

    di = ("public class Startup\n{\n    public void C(IServiceCollection services)\n    {\n"
          "        services.AddSingleton<ICache, MemoryCache>(); "
          "// was: services.AddScoped<IOrderRepo, LegacyOrderRepo>()\n    }\n}\n")
    _, e_d = CS.recognize(di, "s.cs")
    dep = {e.dst_name for e in _by_type(e_d, "DEPENDS_ON")}
    check("a commented-out DI registration fabricates NOTHING (live one still emitted)",
          dep == {"ICache", "MemoryCache"}, str(dep))

    leak = ("public class C : DbContext\n{\n"
            "    protected override void OnModelCreating(ModelBuilder b)\n    {\n"
            "        b.Entity<Order>().HasMany(o => o.Items).WithOne(i => i.Order); // no cascade\n"
            "        b.Entity<Invoice>().HasOne(i => i.Order).WithMany(o => o.Invoices)"
            ".OnDelete(DeleteBehavior.Cascade);\n    }\n}\n")
    _, e_l = CS.recognize(leak, "c.cs")
    lcas = {(e.src_name, e.dst_name) for e in _by_type(e_l, "CASCADES")}
    check("a trailing comment after ';' does NOT leak fluent state to the next statement",
          lcas == {("Order", "Invoice")}, str(lcas))

    twostmt = ("public class C : DbContext\n{\n"
               "    protected override void OnModelCreating(ModelBuilder b)\n    {\n"
               "        b.Entity<A>().HasMany(x => x.Bees).WithOne(); "
               "b.Entity<C>().HasMany(x => x.Dees).WithOne().OnDelete(DeleteBehavior.Cascade);\n"
               "    }\n}\n")
    _, e_2 = CS.recognize(twostmt, "c.cs")
    cas2 = {(e.src_name, e.dst_name) for e in _by_type(e_2, "CASCADES")}
    check("two fluent statements on ONE line: the OnDelete binds to the SECOND chain only",
          cas2 == {("C", "Dee")}, str(cas2))

    strline = ('public class Startup\n{\n    public void C(IServiceCollection services)\n    {\n'
               '        var url = "https://example.test"; '
               'services.AddScoped<IReal, Real>();\n    }\n}\n')
    _, e_s = CS.recognize(strline, "s.cs")
    check("'//' inside a string literal is NOT a comment (the DI after it still emits)",
          {e.dst_name for e in _by_type(e_s, "DEPENDS_ON")} == {"IReal", "Real"},
          str(_by_type(e_s, "DEPENDS_ON")))


def gate_declaration_shapes() -> None:
    print("\n-- declaration shapes: where-clauses, records, primary ctors, same-line attributes --")
    n_w, _ = CS.recognize("public class Cache<T> where T : notnull\n{\n}\n", "w.cs")
    check("generic constraint WITHOUT a base list still yields the node",
          [n["name"] for n in n_w] == ["Cache"], str(n_w))
    _, e_w = CS.recognize("public class Repo<T> : IRepo<T> where T : class\n{\n}\n", "w.cs")
    check("base list + where-clause: IMPLEMENTS survives, constraint tokens don't leak",
          [(e.edge_type, e.dst_name) for e in e_w] == [("IMPLEMENTS", "IRepo")], str(e_w))

    n_r, e_r = CS.recognize("public record Money(decimal Amount) : IComparable;\n", "m.cs")
    check("semicolon-bodied record: node + IMPLEMENTS both emitted",
          [n["name"] for n in n_r] == ["Money"]
          and any(e.edge_type == "IMPLEMENTS" and e.dst_name == "IComparable" for e in e_r),
          f"{n_r} {e_r}")

    _, e_p = CS.recognize("public class Foo(int x) : Bar(x)\n{\n}\n", "p.cs")
    check("C#12 primary-constructor base invocation ': Bar(x)' -> INHERITS Foo -> Bar",
          any(e.edge_type == "INHERITS" and e.dst_name == "Bar" for e in e_p), str(e_p))

    inline = ("[ApiController] [Route(\"api/[controller]\")] public class InlineController : ControllerBase\n"
              "{\n    [HttpGet(\"{id}\")] public Order Get(int id)\n    {\n        return null;\n    }\n}\n")
    n_i, e_i = CS.recognize(inline, "i.cs")
    routes = _by_type(e_i, "ROUTES_TO")
    check("attributes on the SAME line as the declaration: class node + INHERITS + the route",
          [n["name"] for n in n_i] == ["InlineController"]
          and any(e.edge_type == "INHERITS" for e in e_i)
          and len(routes) == 1 and routes[0].dst_name == "InlineController.Get"
          and routes[0].attrs.get("path") == "api/Inline/{id}", f"{n_i} {e_i}")

    qual = "public class Order\n{\n}\n\npublic class Foo : Legacy.Order\n{\n}\n"
    _, e_q = CS.recognize(qual, "q.cs")
    inh = [e for e in _by_type(e_q, "INHERITS") if e.src_name == "Foo"]
    check("a QUALIFIED base (Legacy.Order) never grades LOCAL off a same-file bare-name collision",
          len(inh) == 1 and inh[0].conf_hint == "NAME"
          and inh[0].attrs.get("raw_parent") == "Legacy.Order", str(inh))

    scal = ("public class Doc\n{\n    public DateTime CreatedAt { get; set; }\n"
            "    public Guid Id { get; set; }\n    public DbSet<DateTime> Times { get; set; }\n}\n")
    _, e_sc = CS.recognize(scal, "d.cs")
    check("BCL value types (DateTime/Guid) never emit REFERENCES or HAS_MANY",
          not _by_type(e_sc, "REFERENCES") and not _by_type(e_sc, "HAS_MANY"), str(e_sc))


def gate_fluent_orientation() -> None:
    print("\n-- fluent orientation: HasOne singular props; the FK<T>==counterpart branch --")
    st = ("public class C : DbContext\n{\n"
          "    protected override void OnModelCreating(ModelBuilder b)\n    {\n"
          "        b.Entity<Ticket>().HasOne(t => t.Status).WithMany(s => s.Tickets)"
          ".OnDelete(DeleteBehavior.Cascade);\n    }\n}\n")
    _, e_st = CS.recognize(st, "c.cs")
    check("HasOne property is NOT singularized (Status stays Status, never 'Statu')",
          any(e.edge_type == "REFERENCES" and e.dst_name == "Status" for e in e_st)
          and any(e.edge_type == "CASCADES" and e.src_name == "Status"
                  and e.dst_name == "Ticket" for e in e_st),
          str(e_st))

    fk2 = ("public class C : DbContext\n{\n"
           "    protected override void OnModelCreating(ModelBuilder b)\n    {\n"
           "        b.Entity<Invoice>().HasOne(i => i.Order).WithOne(o => o.Invoice)"
           ".HasForeignKey<Order>(o => o.InvoiceId).OnDelete(DeleteBehavior.Cascade);\n    }\n}\n")
    _, e_fk = CS.recognize(fk2, "c.cs")
    cas = {(e.src_name, e.dst_name) for e in _by_type(e_fk, "CASCADES")}
    check("one-to-one HasForeignKey<T> where T == the HasOne counterpart: principal INVERTS",
          cas == {("Invoice", "Order")}, str(cas))


def gate_singularizer() -> None:
    print("\n-- PascalCase singularizer + demodulize; miss == dropped edge (never fabricated) --")
    cases = {"Orders": "Order", "LineItems": "LineItem", "Categories": "Category",
             "AuditEntries": "AuditEntry", "People": "Person", "Statuses": "Status",
             "Boxes": "Box", "Addresses": "Address", "Children": "Child", "Order": "Order"}
    for plural, want in cases.items():
        got = CS.singularize_pascal(plural)
        check(f"singularize {plural} -> {want}", got == want, got)
    check("demodulize Shop.Models.Order -> Order", CS.demodulize("Shop.Models.Order") == "Order")
    # The honesty contract: an unresolvable (mis-singularized) target DROPS at push.
    nodes = [{"key": "k1", "label": "Class", "name": "Order", "source_file": "o.cs"}]
    resolver = ER.Resolver(nodes)
    edge = FE.TypedEdge("Order", "o.cs", "NoSuchEntity", "HAS_MANY", "NAME", {})
    log: list = []

    class _S:
        def run(self, c, **p):
            for row in p.get("rows") or [p]:   # the push batches with UNWIND $rows: one entry per row
                log.append((c, row))
    counts = ER.push_typed_edges(_S(), [edge], resolver, ["k1"])
    check("unresolvable target -> edge DROPPED (0 created), never fabricated",
          counts.get("HAS_MANY", 0) == 0, str(counts))


def gate_ensured_nodes_match_graphify() -> None:
    print("\n-- ensured nodes byte-match graphify's C# name shapes (captured fixture) --")
    fx = json.loads((REPO / "tests" / "fixtures" / "graphify_multilang" / "csharp_ef.json")
                    .read_text(encoding="utf-8"))
    mapped_nodes, mapped_calls, _ = GX.map_graph(fx)
    g_names = {(n["name"], n["source_file"]) for n in mapped_nodes if n["label"] == "Class"}
    check("FIXTURE ground truth: graphify DOES node-ify a property-only EF entity",
          ("Order", "Models/Order.cs") in g_names, str(g_names))
    check("fixture: file-scoped namespace -> BARE class name (ShopContext, OrdersController)",
          ("ShopContext", "Data/ShopContext.cs") in g_names
          and ("OrdersController", "Controllers/OrdersController.cs") in g_names)
    check("fixture: block namespace -> classes still BARE (AuditEntry, Customer)",
          ("AuditEntry", "Models/Legacy.cs") in g_names
          and ("Customer", "Models/Legacy.cs") in g_names)
    check("fixture: interfaces node-ify too (IOrderService)",
          ("IOrderService", "Services/OrderService.cs") in g_names)
    check("FIXTURE ground truth: the EF/route/DI wiring yields ZERO relationships (the field state)",
          mapped_calls == [], str(mapped_calls))

    # The recognizer's ensured nodes must reproduce those EXACT (name, file, line) shapes.
    for src, rel, want in (
        (ORDER_CS, "Models/Order.cs", {("Order", 3)}),
        (LEGACY_CS, "Models/Legacy.cs", {("AuditEntry", 5), ("Customer", 11)}),
        (SERVICE_CS, "Services/OrderService.cs", {("IOrderService", 3), ("OrderService", 8)}),
    ):
        nodes, _ = CS.recognize(src, rel)
        got = {(n["name"], n["start_line"]) for n in nodes}
        check(f"recognizer reproduces graphify's shapes for {rel}", got == want, str(got))
        check(f"ensured nodes for {rel} carry the full push contract",
              all(all(k in n and n[k] not in (None, "") for k in
                      ("key", "label", "name", "source_file", "start_line", "body_summary"))
                  and n["label"] == "Class" and n["documented"] is False for n in nodes))

    # Dedupe: graphify already has these nodes -> the ensured copies are dropped (base wins).
    n_order, _ = CS.recognize(ORDER_CS, "Models/Order.cs")
    merged = LR.merge_supplemental(mapped_nodes, LR.validate_supplemental(n_order, "csharp"))
    check("graphify parsed the file -> ensured nodes dedupe away (base wins)",
          merged is mapped_nodes)
    merged2 = LR.merge_supplemental([], LR.validate_supplemental(n_order, "csharp"))
    check("graphify missed the repo (Python-only ast fallback) -> ensured nodes fill the layer",
          len(merged2) == 1 and merged2[0]["name"] == "Order")

    n_ctx, _ = CS.recognize(SHOPCONTEXT_CS, "Data/ShopContext.cs")
    summ = next(n["body_summary"] for n in n_ctx if n["name"] == "ShopContext")
    check("DSL-derived deterministic summary (DbContext kind + DbSet facts)",
          "DbContext" in summ and "Orders" in summ, summ)


class _Sess:
    def __init__(self):
        self.log: list = []

    def run(self, cypher, **params):
        for row in params.get("rows") or [params]:   # the push batches with UNWIND $rows: one entry per row
            self.log.append((cypher, row))


def gate_global_src_fallback() -> None:
    print("\n-- edge_resolve GLOBAL_SRC_FALLBACK: the EF third-file shape (single-candidate only) --")
    check("GLOBAL_SRC_FALLBACK is exactly {CASCADES, HAS_MANY, REFERENCES} (literal-locked)",
          ER.GLOBAL_SRC_FALLBACK == {"CASCADES", "HAS_MANY", "REFERENCES"},
          str(ER.GLOBAL_SRC_FALLBACK))
    nodes = [
        {"key": "Models/Order.cs::Order:3", "label": "Class", "name": "Order",
         "source_file": "Models/Order.cs"},
        {"key": "Models/LineItem.cs::LineItem:3", "label": "Class", "name": "LineItem",
         "source_file": "Models/LineItem.cs"},
        {"key": "Data/ShopContext.cs::ShopContext:6", "label": "Class", "name": "ShopContext",
         "source_file": "Data/ShopContext.cs"},
    ]
    resolver = ER.Resolver(nodes)
    live = [n["key"] for n in nodes]
    # The fluent CASCADES: declared in ShopContext.cs, where NEITHER endpoint is a node.
    edge = FE.TypedEdge("Order", "Data/ShopContext.cs", "LineItem", "CASCADES", "NAME",
                        {"strategy": "Cascade"})
    s = _Sess()
    counts = ER.push_typed_edges(s, [edge], resolver, live)
    merges = [(c, p) for c, p in s.log if "MERGE" in c and "CASCADES" in c]
    check("cross-file src resolves via the single-candidate global fallback (edge created)",
          counts.get("CASCADES") == 1, str(counts))
    check("persisted REVERSED: (LineItem)-[:CASCADES]->(Order) — impact('Order') lists LineItem",
          len(merges) == 1 and "MERGE (b)-[r:CASCADES]->(a)" in merges[0][0]
          and merges[0][1].get("a") == "Models/Order.cs::Order:3"
          and merges[0][1].get("b") == "Models/LineItem.cs::LineItem:3", str(merges))
    check("global-src resolution grades INFERRED (never EXTRACTED)",
          merges[0][1].get("c") == "INFERRED", str(merges))

    # Ambiguous global src -> DROP (never fan out, never fabricate).
    dup = nodes + [{"key": "Other/Order.cs::Order:1", "label": "Class", "name": "Order",
                    "source_file": "Other/Order.cs"}]
    s2 = _Sess()
    counts2 = ER.push_typed_edges(s2, [edge], ER.Resolver(dup), live + ["Other/Order.cs::Order:1"])
    check("AMBIGUOUS global src (two Order classes) -> edge DROPPED (0 created)",
          counts2.get("CASCADES", 0) == 0, str(counts2))

    # THE DOGFOOD FINDING (live-proven on Neo4j 5.26): graphify names the file-Module after its
    # stem, and the dominant C# convention names the file after the class — so 'Order' resolves to
    # a Module AND a Class in EVERY conventional repo. A type-level relationship must narrow to the
    # :Class candidates on BOTH endpoints, or the flagship cascade silently drops (src) / lands on
    # the FILE node (dst).
    coll = nodes + [
        {"key": "models_order_mod", "label": "Module", "name": "Order",
         "source_file": "Models/Order.cs"},
        {"key": "models_lineitem_mod", "label": "Module", "name": "LineItem",
         "source_file": "Models/LineItem.cs"},
    ]
    s5 = _Sess()
    counts5 = ER.push_typed_edges(s5, [edge], ER.Resolver(coll),
                                  [n["key"] for n in nodes] + ["models_order_mod",
                                                               "models_lineitem_mod"])
    m5 = [(c, p) for c, p in s5.log if "MERGE" in c and "CASCADES" in c]
    check("file-Module/Class name collision: src narrows to the :Class node (edge NOT dropped)",
          counts5.get("CASCADES") == 1, str(counts5))
    check("dst narrows to the :Class node too (a FILE does not cascade) — single INFERRED edge",
          len(m5) == 1 and m5[0][1].get("a") == "Models/Order.cs::Order:3"
          and m5[0][1].get("b") == "Models/LineItem.cs::LineItem:3"
          and m5[0][1].get("c") == "INFERRED", str(m5))

    # The INFERRED cap must be LOAD-BEARING: a LOCAL-hint edge whose dst IS in the declaring file
    # (same_file grade would be EXTRACTED) but whose src resolved globally must still cap at
    # INFERRED — removing the cap line must turn this gate RED.
    nodes_cap = nodes + [{"key": "Data/ShopContext.cs::LineItem:9", "label": "Class",
                          "name": "LineItem", "source_file": "Data/ShopContext.cs"}]
    s_cap = _Sess()
    ER.push_typed_edges(
        s_cap, [FE.TypedEdge("Order", "Data/ShopContext.cs", "LineItem", "CASCADES", "LOCAL", {})],
        ER.Resolver(nodes_cap), [n["key"] for n in nodes_cap])
    m_cap = [(c, p) for c, p in s_cap.log if "MERGE" in c and "CASCADES" in c]
    check("INFERRED cap is load-bearing: LOCAL hint + same-file dst + GLOBAL src -> INFERRED",
          len(m_cap) == 1 and m_cap[0][1].get("c") == "INFERRED", str(m_cap))

    # Ambiguity is judged over ALL nodes, not just live ones: an out-of-scope duplicate must not
    # silently disambiguate a subdir sync.
    dup2 = nodes + [{"key": "Other/Order.cs::Order:1", "label": "Class", "name": "Order",
                     "source_file": "Other/Order.cs"}]
    s_pre = _Sess()
    counts_pre = ER.push_typed_edges(s_pre, [edge], ER.Resolver(dup2), live)  # dup NOT live
    check("global-src ambiguity counted BEFORE the live filter (out-of-scope dup still DROPS)",
          counts_pre.get("CASCADES", 0) == 0, str(counts_pre))

    # Non-fallback types keep the same-file-strict src discipline byte-unchanged.
    s3 = _Sess()
    counts3 = ER.push_typed_edges(
        s3, [FE.TypedEdge("Order", "Data/ShopContext.cs", "LineItem", "DEPENDS_ON", "NAME", {})],
        resolver, live)
    check("non-fallback type (DEPENDS_ON) with a cross-file src still DROPS (discipline unchanged)",
          counts3.get("DEPENDS_ON", 0) == 0, str(counts3))

    # Same-file src still wins first (the fallback never fires when the anchor exists).
    s4 = _Sess()
    counts4 = ER.push_typed_edges(
        s4, [FE.TypedEdge("ShopContext", "Data/ShopContext.cs", "Order", "HAS_MANY", "NAME", {})],
        resolver, live)
    m4 = [(c, p) for c, p in s4.log if "MERGE" in c and "HAS_MANY" in c]
    check("same-file src resolves first (DbSet HAS_MANY anchors on ShopContext, INFERRED)",
          counts4.get("HAS_MANY") == 1
          and m4[0][1].get("a") == "Data/ShopContext.cs::ShopContext:6", str(m4))


def gate_reversed_prune_anchoring() -> None:
    print("\n-- CASCADES prune anchoring unchanged (declaring side) for the C# path --")
    s = _Sess()
    ER.prune_stale_edge_types(s, ["k1"], present_types=set())
    cas_prunes = [c for c, _ in s.log if "CASCADES" in c]
    check("stale-type prune for CASCADES anchors at the DECLARING side: MATCH ()-[r:CASCADES]->(a)",
          len(cas_prunes) == 1 and "()-[r:CASCADES]->(a)" in cas_prunes[0], str(cas_prunes))


def gate_exclude_tests_seam() -> None:
    print("\n-- DEEPMAP_EXCLUDE_TESTS also filters typed edges (no global-fallback re-anchoring) --")
    import os
    import sync as S
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "Models").mkdir()
        (root / "Tests").mkdir()
        (root / "Models" / "Order.cs").write_text(ORDER_CS, encoding="utf-8")
        (root / "Tests" / "OrderTests.cs").write_text(
            "public class OrderTests : DbContext\n{\n"
            "    protected override void OnModelCreating(ModelBuilder b)\n    {\n"
            "        b.Entity<Order>().HasMany(o => o.Ghosts).WithOne()"
            ".OnDelete(DeleteBehavior.Cascade);\n    }\n}\n", encoding="utf-8")
        saved = os.environ.get("DEEPMAP_EXCLUDE_TESTS")
        try:
            os.environ["DEEPMAP_EXCLUDE_TESTS"] = "1"
            _, typed = S._apply_recognizers(root, [])
            check("flag ON: typed edges from excluded test files are FILTERED at the seam "
                  "(a test-declared cascade can no longer re-anchor onto production nodes)",
                  all(not e.src_file.startswith("Tests/") for e in typed),
                  str([(e.edge_type, e.src_file) for e in typed if e.src_file.startswith("Tests/")]))
            os.environ.pop("DEEPMAP_EXCLUDE_TESTS", None)
            _, typed_off = S._apply_recognizers(root, [])
            check("flag OFF: test-file edges still emitted (strict no-op contract)",
                  any(e.src_file.startswith("Tests/") for e in typed_off), str(len(typed_off)))
        finally:
            if saved is None:
                os.environ.pop("DEEPMAP_EXCLUDE_TESTS", None)
            else:
                os.environ["DEEPMAP_EXCLUDE_TESTS"] = saved


def gate_registry_end_to_end() -> None:
    print("\n-- registry end-to-end: LR.run over a C# tree; =0 replays the field state --")
    import os
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "Models").mkdir()
        (root / "Data").mkdir()
        (root / "Models" / "Order.cs").write_text(ORDER_CS, encoding="utf-8")
        (root / "Data" / "ShopContext.cs").write_text(SHOPCONTEXT_CS, encoding="utf-8")
        for junk in ("bin/Debug", "obj/Debug", "packages/Vendored"):
            d = root / Path(junk)
            d.mkdir(parents=True)
        (root / "bin" / "Debug" / "gen.cs").write_text(ORDER_CS, encoding="utf-8")
        (root / "obj" / "Debug" / "gen.cs").write_text(SHOPCONTEXT_CS, encoding="utf-8")
        supp, edges = LR.run(root)
        check("csharp entry runs: ensured nodes + typed edges emitted",
              {n["name"] for n in supp} >= {"Order", "ShopContext"}
              and any(e.edge_type == "CASCADES" for e in edges),
              f"supp={[n['name'] for n in supp]} edges={len(edges)}")
        check("bin/ + obj/ build output never contributes",
              all(not n["source_file"].startswith(("bin/", "obj/")) for n in supp)
              and all(not e.src_file.startswith(("bin/", "obj/")) for e in edges))
        saved = os.environ.get("DEEPMAP_RECOGNIZERS")
        try:
            os.environ["DEEPMAP_RECOGNIZERS"] = "0"
            check("DEEPMAP_RECOGNIZERS=0 replays the pre-plugin field state (no nodes, no edges)",
                  LR.run(root) == ([], []))
            os.environ["DEEPMAP_RECOGNIZERS"] = "csharp"
            supp_c, edges_c = LR.run(root)
            check("CSV 'csharp' runs only this entry", len(supp_c) >= 2 and len(edges_c) >= 3)
        finally:
            if saved is None:
                os.environ.pop("DEEPMAP_RECOGNIZERS", None)
            else:
                os.environ["DEEPMAP_RECOGNIZERS"] = saved


def gate_minimal_api_routes() -> None:
    print("\n-- minimal-API routes: app.MapGet/MapPost/... -> ROUTES_TO (inline lambdas skipped) --")
    nodes, edges = CS.recognize(MINIMAL_API_CS, "Program.cs")
    ro = _by_type(edges, "ROUTES_TO")
    by_dst = {e.dst_name: e for e in ro}
    check("MapGet('/orders', GetAllOrders) -> ROUTES_TO to the bare handler",
          "GetAllOrders" in by_dst and by_dst["GetAllOrders"].attrs.get("method") == "GET",
          str(sorted(by_dst)))
    check("a same-file bare handler is LOCAL (resolve-or-drop same-file)",
          by_dst.get("GetAllOrders") is not None and by_dst["GetAllOrders"].conf_hint == "LOCAL")
    check("MapPost('/orders', OrderEndpoints.Create) -> a dotted method group is NAME (cross-file)",
          "OrderEndpoints.Create" in by_dst
          and by_dst["OrderEndpoints.Create"].conf_hint == "NAME"
          and by_dst["OrderEndpoints.Create"].attrs.get("method") == "POST", str(sorted(by_dst)))
    check("MapDelete -> DeleteOrder with the DELETE verb + path attr",
          "DeleteOrder" in by_dst and by_dst["DeleteOrder"].attrs.get("method") == "DELETE"
          and by_dst["DeleteOrder"].attrs.get("path") == "/orders/{id}", str(by_dst.get("DeleteOrder")))
    check("an INLINE lambda handler (() => ...) emits NO route edge (anonymous, no node)",
          not any(e.attrs.get("path") == "/health" for e in ro), str([e.attrs for e in ro]))
    # A2: route-GROUP prefix composition (MapGroup). var form + inline chain compose the prefix.
    check("ungrouped app.MapGet path convention preserved (raw, leading slash — regression guard)",
          by_dst.get("GetAllOrders") is not None
          and by_dst["GetAllOrders"].attrs.get("path") == "/orders", str(by_dst.get("GetAllOrders")))
    check("var api = app.MapGroup('/api'); api.MapGet('/orders', H) composes -> '/api/orders'",
          "GetGroupedOrders" in by_dst
          and by_dst["GetGroupedOrders"].attrs.get("path") == "/api/orders",
          str(by_dst.get("GetGroupedOrders")))
    check("inline app.MapGroup('/v2').MapGet('/things', H) composes -> '/v2/things'",
          "GetThings" in by_dst and by_dst["GetThings"].attrs.get("path") == "/v2/things",
          str(by_dst.get("GetThings")))
    check("minimal-API routes are anchored on the file stem (top-level statements)",
          all(e.src_name == "Program" for e in ro), str({e.src_name for e in ro}))
    check("the AddScoped DI edge still fires alongside (no regression)",
          any(e.edge_type == "DEPENDS_ON" for e in edges))
    check("every emitted edge type stays within the C# vocabulary",
          all(e.edge_type in FE.ALLOWED_EDGE_TYPES for e in edges))


def gate_multi_statement_lambda() -> None:
    print("\n-- multi-statement Entity<X>(e => { ...; ...; }) lambda: EVERY statement's cascade emitted --")
    edges = _edges(LAMBDA_BLOCK_CS, "Data/BlockContext.cs")
    cas = _by_type(edges, "CASCADES")
    pairs = {(e.src_name, e.dst_name) for e in cas}
    check("statement 1 (HasMany+OnDelete) -> CASCADES Order -> LineItem",
          ("Order", "LineItem") in pairs, str(pairs))
    check("statement 2 (HasOne+WithMany+OnDelete) survives the ';' -> CASCADES Customer -> Order "
          "(the anchor persists across the lambda block)", ("Customer", "Order") in pairs, str(pairs))
    check("BOTH statements' cascades are present (the pre-fix drop is closed)", len(pairs) == 2, str(pairs))
    # the relationship edges are also anchored to the lambda entity
    hm = {(e.src_name, e.dst_name) for e in _by_type(edges, "HAS_MANY")}
    refs = {(e.src_name, e.dst_name) for e in _by_type(edges, "REFERENCES")}
    check("HAS_MANY Order -> LineItem anchored on the lambda entity", ("Order", "LineItem") in hm, str(hm))
    check("REFERENCES Order -> Customer anchored on the lambda entity", ("Order", "Customer") in refs, str(refs))


def gate_constructor_di() -> None:
    print("\n-- constructor-injection DI: ctor param types -> DEPENDS_ON (BCL/scalars filtered) --")
    edges = _edges(CTOR_DI_CS, "Services/OrderService.cs")
    dep = _by_type(edges, "DEPENDS_ON")
    deps = {e.dst_name: e for e in dep}
    check("ctor param IOrderRepo -> DEPENDS_ON OrderService -> IOrderRepo",
          "IOrderRepo" in deps and deps["IOrderRepo"].src_name == "OrderService", str(sorted(deps)))
    check("generic ctor param ILogger<OrderService> -> DEPENDS_ON to the bare ILogger",
          "ILogger" in deps, str(sorted(deps)))
    check("a scalar ctor param (int retries) is NOT a dependency", "int" not in deps and "Int32" not in deps)
    check("ctor DEPENDS_ON carries via=ctor + the param name",
          deps.get("IOrderRepo") is not None and deps["IOrderRepo"].attrs.get("via") == "ctor"
          and deps["IOrderRepo"].attrs.get("param") == "repo", str(deps.get("IOrderRepo")))
    check("a non-ctor method (Place) contributes NO DEPENDS_ON", "orderId" not in deps and len(deps) == 2,
          str(sorted(deps)))

    # primary constructor (C# 12): class Foo(IBar bar) -> DEPENDS_ON too
    pedges = _edges(PRIMARY_CTOR_DI_CS, "Services/InvoiceService.cs")
    pdeps = {e.dst_name for e in _by_type(pedges, "DEPENDS_ON")}
    check("primary-constructor params -> DEPENDS_ON InvoiceService -> IInvoiceRepo, IClock",
          {"IInvoiceRepo", "IClock"} <= pdeps, str(pdeps))
    check("primary-ctor DEPENDS_ON src is the declaring class",
          all(e.src_name == "InvoiceService" for e in _by_type(pedges, "DEPENDS_ON")))


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 64)
    print("DeepMap — C#/.NET recognizer gates (plugin #3, no Neo4j / Roslyn)")
    print("=" * 64)
    gate_recognizer_emit()
    gate_comment_string_honesty()
    gate_declaration_shapes()
    gate_fluent_orientation()
    gate_singularizer()
    gate_ensured_nodes_match_graphify()
    gate_global_src_fallback()
    gate_reversed_prune_anchoring()
    gate_exclude_tests_seam()
    gate_registry_end_to_end()
    gate_minimal_api_routes()
    gate_multi_statement_lambda()
    gate_constructor_di()
    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
