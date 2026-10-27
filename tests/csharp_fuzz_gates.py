#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Hardening gates for the C#/.NET recognizer: seeded fuzz + metamorphic properties + the
integration MONEY GATE (sync-entry -> Cypher-out on a fake driver). No Neo4j, no Roslyn/dotnet.

The csharp_edges_gates suite proves the recognizer is CORRECT on conventional C#/EF source; this
suite proves it is SAFE on everything else and WIRED correctly end-to-end:

  - FUZZ (300 seeded adversarial sources — attributes, generics, fluent fragments, unicode, binary
    junk, truncated declarations, unbalanced comments, huge lines, CRLF/CR, DSL-in-strings):
    recognize() NEVER raises, always returns (list, list), every edge is a TypedEdge with an
    edge_type the csharp entry declared, every node either survives the validator or is dropped
    silently — and the whole pipeline is DETERMINISTIC (same input -> byte-equal output);
  - METAMORPHIC properties: comment insertion is edge-invariant; a blank-line prefix shifts node
    start_lines by exactly N and changes nothing else; recognize() is idempotent; deleting a
    navigation-property line removes exactly its edges;
  - the MONEY GATE: the real EF sample tree through sync._apply_recognizers -> sync.push_to_neo4j
    on a fake driver (the confidence_gates idiom) in the WORST field state (base extractor returned
    NOTHING — exactly the graphify-less C# repo, since the ast fallback is Python-only): ensured
    :Class nodes MERGE with the label whitelist; the fluent CASCADES declared in ShopContext.cs —
    where NEITHER endpoint is defined — persists REVERSED via the single-candidate GLOBAL src
    fallback ((LineItem)-[:CASCADES]->(Order), INFERRED); a REFERENCES to an undeclared type DROPS;
    ROUTES_TO to a method no extractor node-ified DROPS (SAME_FILE_STRICT); top-level-statement DI
    DROPS without a Module node and RESOLVES once graphify's Module/Method nodes are present
    (ROUTES_TO then grades EXTRACTED) — proving the full
    recognizer->validator->merge->resolve->prune->push chain without Neo4j.
"""
from __future__ import annotations

import io
import random
import string
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build"
sys.path.insert(0, str(BUILD))

import csharp_edges as CS           # noqa: E402
import language_recognizers as LR   # noqa: E402
import sync as S                    # noqa: E402
import framework_edges as FE        # noqa: E402

_PASS = _FAIL = 0


def check(desc: str, cond: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    ok = bool(cond)
    _PASS += ok
    _FAIL += (not ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {desc}" + ("" if ok else f"  -- {detail}"))


# ---- fuzz corpus -------------------------------------------------------------
_DSL_FRAGMENTS = [
    "public class Order : ApplicationEntity", "public class Order", "public interface IThing",
    "public record Invoice(int Id) : IEntity", "public struct Point",
    "public DbSet<Order> Orders { get; set; }", "public List<LineItem> LineItems { get; set; } = new();",
    "public Customer Customer { get; set; }", "public int Id { get; set; }",
    "public string Name { get; set; }", "public virtual ICollection<Tag> Tags { get; set; }",
    "modelBuilder.Entity<Order>()", ".HasMany(o => o.LineItems)", ".WithOne(li => li.Order)",
    ".HasForeignKey(li => li.OrderId)", ".OnDelete(DeleteBehavior.Cascade);",
    ".OnDelete(DeleteBehavior.Restrict);", ".HasOne(o => o.Customer).WithMany(c => c.Orders)",
    "b.Entity<X>().HasMany(x => x.Ys).WithOne().OnDelete(DeleteBehavior.SetNull);",
    "[HttpGet(\"{id}\")]", "[HttpPost]", "[Route(\"api/[controller]\")]", "[ApiController]",
    "public Order GetOrder(int id)", "public void CreateOrder()",
    "services.AddScoped<IOrderService, OrderService>();",
    "builder.Services.AddSingleton(typeof(IRepo<>), typeof(Repo<>));",
    "// public DbSet<Ghost> Ghosts { get; set; }", "/* .OnDelete(DeleteBehavior.Cascade); */",
    "namespace Shop.Models;", "namespace Shop.Legacy", "{", "}", "using Microsoft.EntityFrameworkCore;",
    'var s = "public class Fake : X";', "protected override void OnModelCreating(ModelBuilder b)",
    # v0.21 additions: minimal-API routes, ctor injection, multi-statement Entity lambda blocks.
    "app.MapGet(\"/orders\", GetAllOrders);", "app.MapPost(\"/orders\", OrderEndpoints.Create);",
    "app.MapDelete(\"/o/{id}\", DeleteOrder);", "app.MapGet(\"/health\", () => \"ok\");",
    "public OrderService(IOrderRepo repo, ILogger<OrderService> logger, int retries)",
    "public InvoiceService(IInvoiceRepo repo, IClock clock)",
    "modelBuilder.Entity<Order>(entity =>", "entity.HasOne(o => o.Customer).WithMany(c => c.Orders).OnDelete(DeleteBehavior.Cascade);",
    "});", "Order GetAllOrders() => null;",
    # v0.24 additions: route-GROUP prefixes (var form + inline chain).
    "var api = app.MapGroup(\"/api\");", "api.MapGet(\"/orders\", GetGroupedOrders);",
    "app.MapGroup(\"/v2\").MapGet(\"/things\", GetThings);", "var admin = api.MapGroup(\"/admin\");",
]
_JUNK = ["\x00\x01\xff", "日本語のテキスト", "🚀🔥", "a" * 5000, "class", "<", ">", ":", ";",
         "public", "DbSet<", "List<>", "[Route(", "[", "]", "=>", "public class 9Bad : X",
         "class  :  ", "} } } }", "\r\n\r\r\n", "＜クラス＞", "-- not csharp --",
         "{\"json\": true}", "/*", "*/", "public Order", ".OnDelete(", "DeleteBehavior.",
         "app.Map", ".MapGet(", "public (", "entity =>", "Entity<", "(", ")"]


def _fuzz_source(rng: random.Random) -> str:
    n = rng.randint(0, 40)
    lines = []
    for _ in range(n):
        r = rng.random()
        if r < 0.55:
            lines.append(rng.choice(_DSL_FRAGMENTS))
        elif r < 0.75:
            lines.append(rng.choice(_JUNK))
        else:
            lines.append("".join(rng.choice(string.printable) for _ in range(rng.randint(0, 80))))
    eol = rng.choice(["\n", "\r\n", "\r"])
    return eol.join(lines)


def gate_fuzz() -> None:
    print("\n-- 300 seeded adversarial sources: never raise, deterministic, vocab-clean --")
    rng = random.Random(20260711)
    cs_entry = next(e for e in LR.REGISTRY if e.name == "csharp")
    crashed = nondet = badvocab = badshape = 0
    for i in range(300):
        src = _fuzz_source(rng)
        rel = f"Models/F{i}.cs"
        try:
            out1 = CS.recognize(src, rel)
            out2 = CS.recognize(src, rel)
        except Exception:
            crashed += 1
            continue
        if not (isinstance(out1, tuple) and len(out1) == 2
                and isinstance(out1[0], list) and isinstance(out1[1], list)):
            badshape += 1
            continue
        if out1 != out2:
            nondet += 1
        if any(e.edge_type not in cs_entry.edge_types for e in out1[1]):
            badvocab += 1
        LR.validate_supplemental(out1[0], "csharp")   # must also never raise
    check("0/300 raised", crashed == 0, f"{crashed} crashed")
    check("0/300 wrong return shape", badshape == 0, f"{badshape}")
    check("0/300 nondeterministic", nondet == 0, f"{nondet}")
    check("0/300 emitted an undeclared edge type", badvocab == 0, f"{badvocab}")


ORDER_SRC = """namespace Shop.Models;

public class Order
{
    public int Id { get; set; }
    public Customer Customer { get; set; }
    public List<LineItem> LineItems { get; set; } = new();
}
"""


def gate_metamorphic() -> None:
    print("\n-- metamorphic properties --")
    rel = "Models/Order.cs"
    n0, e0 = CS.recognize(ORDER_SRC, rel)

    # Idempotence.
    check("idempotent (recognize twice == same)", CS.recognize(ORDER_SRC, rel) == (n0, e0))

    # Comment insertion: edges invariant.
    commented = ORDER_SRC.replace("    public Customer Customer { get; set; }",
                                  "    // a comment\n    public Customer Customer { get; set; }")
    _, e1 = CS.recognize(commented, rel)
    check("comment insertion is edge-invariant", e1 == e0)

    # Blank-line prefix: node start_lines shift by exactly N; names/edges identical.
    N = 3
    n2, e2 = CS.recognize(("\n" * N) + ORDER_SRC, rel)
    check("prefix of N blank lines shifts node start_line by exactly N",
          [(n["name"], n["start_line"]) for n in n2]
          == [(n["name"], n["start_line"] + N) for n in n0],
          f"{[(n['name'], n['start_line']) for n in n2]}")
    check("prefix of N blank lines leaves edges identical", e2 == e0)

    # Deleting the collection-nav line removes EXACTLY its HAS_MANY edge.
    no_hm = "\n".join(l for l in ORDER_SRC.splitlines() if "LineItems" not in l)
    _, e3 = CS.recognize(no_hm, rel)
    gone = [e for e in e0 if e not in e3]
    check("deleting the nav property removes exactly its HAS_MANY edge",
          [e.edge_type for e in gone] == ["HAS_MANY"]
          and all(e.dst_name == "LineItem" for e in gone), str(gone))
    check("everything else survives", all(e in e3 for e in e0 if e.dst_name != "LineItem"))


class _Sess:
    def __init__(self, log):
        self.log = log

    def run(self, cypher, **params):
        for row in params.get("rows") or [params]:   # the push batches with UNWIND $rows: one entry per row
            self.log.append((cypher, row))

    def begin_transaction(self):                     # the edge rebuild runs inside a write transaction
        return self

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Drv:
    def __init__(self, log):
        self._log = log

    def session(self):
        return _Sess(self._log)


LINEITEM_SRC = """namespace Shop.Models;

public class LineItem
{
    public int Id { get; set; }
    public Order Order { get; set; }
}
"""

CONTEXT_SRC = """using Microsoft.EntityFrameworkCore;

namespace Shop.Data;

public class ShopContext : DbContext
{
    public DbSet<Order> Orders { get; set; }

    protected override void OnModelCreating(ModelBuilder b)
    {
        b.Entity<Order>()
            .HasMany(o => o.LineItems)
            .WithOne(li => li.Order)
            .OnDelete(DeleteBehavior.Cascade);
    }
}
"""

CONTROLLER_SRC = """using Microsoft.AspNetCore.Mvc;

namespace Shop.Controllers;

[Route("api/[controller]")]
public class OrdersController : ControllerBase
{
    [HttpGet("{id}")]
    public Order GetOrder(int id)
    {
        return null;
    }
}
"""

PROGRAM_SRC = """using Shop.Services;

var builder = WebApplication.CreateBuilder(args);
builder.Services.AddScoped<IOrderService, OrderService>();
"""

SERVICE_SRC = """namespace Shop.Services;

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


def gate_money() -> None:
    print("\n-- MONEY GATE: C#/EF tree -> _apply_recognizers -> push_to_neo4j (fake driver) --")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        for rel, src in (("Models/Order.cs", ORDER_SRC), ("Models/LineItem.cs", LINEITEM_SRC),
                         ("Data/ShopContext.cs", CONTEXT_SRC),
                         ("Controllers/OrdersController.cs", CONTROLLER_SRC),
                         ("Services/OrderService.cs", SERVICE_SRC), ("Program.cs", PROGRAM_SRC)):
            p = root / Path(rel)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(src, encoding="utf-8")

        # THE WORST FIELD STATE: the base extractor returned NOTHING (a graphify-less C# repo —
        # the ast fallback is Python-only, so there are NO base nodes at all).
        merged, typed = S._apply_recognizers(root, [])
        check("recognizer fills the empty entity layer (ensured :Class nodes)",
              {n["name"] for n in merged} >= {"Order", "LineItem", "ShopContext",
                                              "OrdersController", "IOrderService", "OrderService"},
              str({n.get("name") for n in merged}))

        log: list = []
        n_nodes, _ = S.push_to_neo4j(_Drv(log), merged, [], [], typed)
        node_merges = [c for c, _ in log if "MERGE (x:Class" in c]
        check("every ensured node MERGEs with the :Class label",
              n_nodes == len(merged) and len(node_merges) == len(merged),
              f"{n_nodes} nodes, {len(node_merges)} :Class merges")

        cas = [(c, p) for c, p in log if "MERGE" in c and "CASCADES" in c]
        check("the EF THIRD-FILE cascade persists REVERSED via the single-candidate global src "
              "fallback (b=LineItem -> a=Order: impact('Order') will list LineItem)",
              len(cas) == 1 and "MERGE (b)-[r:CASCADES]->(a)" in cas[0][0]
              and "Order:3" in cas[0][1]["a"] and "LineItem:3" in cas[0][1]["b"], str(cas))
        check("that cascade grades INFERRED (a globally-resolved src is never EXTRACTED)",
              cas[0][1].get("c") == "INFERRED", str(cas))

        refs = [(c, p) for c, p in log if "MERGE" in c and "[r:REFERENCES]" in c]
        check("REFERENCES to the UNDECLARED Customer DROPS (unresolvable target, no fabrication)",
              not any("Customer" in str(p) for _, p in refs), str(refs))

        routes = [(c, p) for c, p in log if "MERGE" in c and "ROUTES_TO" in c]
        check("ROUTES_TO to a method no extractor node-ified DROPS (SAME_FILE_STRICT)",
              routes == [], str(routes))
        deps = [(c, p) for c, p in log if "MERGE" in c and "DEPENDS_ON" in c]
        check("top-level-statement DI DROPS without a Module node (no fabricated anchor)",
              deps == [], str(deps))

        # Now graphify DID parse the repo: its Method + Module nodes exist -> the SAME edges resolve.
        graphify_extra = [
            {"key": "ctl_getorder", "label": "Method", "name": "OrdersController.GetOrder",
             "source_file": "Controllers/OrdersController.cs", "start_line": 9,
             "documented": False, "body_summary": "m"},
            {"key": "program_mod", "label": "Module", "name": "Program",
             "source_file": "Program.cs", "start_line": 1,
             "documented": False, "body_summary": "m"},
        ]
        log2: list = []
        S.push_to_neo4j(_Drv(log2), merged + graphify_extra, [], [], typed)
        routes2 = [(c, p) for c, p in log2 if "MERGE" in c and "ROUTES_TO" in c]
        check("with the Method node present, the SAME ROUTES_TO edge resolves EXTRACTED",
              len(routes2) == 1 and routes2[0][1].get("c") == "EXTRACTED", str(routes2))
        deps2 = [(c, p) for c, p in log2 if "MERGE" in c and "DEPENDS_ON" in c]
        check("with the Module node present, the SAME DI edges resolve (service + implementation)",
              len(deps2) == 2 and {p.get("a") for _, p in deps2} == {"program_mod"}, str(deps2))

        # The anti-phantom prune ran for the full vocabulary (present + stale types).
        pruned = {c.split(":")[1].split("]")[0] for c, _ in log if "DELETE r" in c and "[r:" in c}
        check("every whitelisted edge type pruned on this sync (anti-phantom, full vocab)",
              pruned >= (set(FE.ALLOWED_EDGE_TYPES) - {"IMPORTS"}), str(sorted(pruned)))
        rev_prunes = [c for c, _ in log if "DELETE r" in c and "CASCADES" in c]
        check("CASCADES prunes are declaring-side-anchored",
              all("()-[r:CASCADES]->(a)" in c for c in rev_prunes) and rev_prunes, str(rev_prunes))


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 64)
    print("DeepMap — C# recognizer fuzz + metamorphic + money gates (no Neo4j)")
    print("=" * 64)
    import contextlib
    with contextlib.redirect_stderr(io.StringIO()):   # validator/skip noise stays out of CI logs
        gate_fuzz()
        gate_metamorphic()
        gate_money()
    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
