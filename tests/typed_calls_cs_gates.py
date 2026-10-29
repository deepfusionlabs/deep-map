#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Typed-receiver call linking: the C# reader gates (DEEPMAP_TYPED_CALLS=csharp, opt-in). No Neo4j, no network, no
spend; the real-engine section runs the installed graphify under a temporary DEEPMAP_HOME, so nothing is written to
~/.deepmap.

build/receiver_facts_csharp.py lowers a .cs file's tree-sitter tree to typed_calls' frozen IR, and its CsBinder
resolves C# names (the namespace chain, then usings, grouped by the nearest .csproj). build/typed_calls.py applies
the C#-only LanguageSpec.lookup_rules (interface dispatch, partial classes, member scope, using static, extension
methods). These gates drive both through the REAL pipeline: the captured graph.json of graphify 0.8.39 AND 0.8.51
for every fixture tree -> graphify_extract.map_graph -> sync's test / vendor exclusion, enrich sidecar, recognizers,
`_unkey_receiver_calls` -> typed_calls.link, and check:

  A  the field report, reproduced: tests/fixtures/graphify_multilang/csharp_java_collision.json (a REAL capture with
     0 `calls` although `return _service.Find(id);` is in its source) gives InvoicesController.GetInvoice ->
     {IInvoiceService.Find: INFERRED, InvoiceService.Find: INFERRED} and nothing from `_db.Invoices.Find(id)` (an
     external DbSet); before the reader existed it gave langs_disabled={'csharp': 'no reader'}.
  B  the reader's IR on snippets: scopes (method / ctor / accessor / local_function / lambda / anonymous_method),
     scope lines from the first attribute line, overloads sharing the first overload's line, fields from field,
     property and primary-constructor declarations, receivers (`this`, `base`, `x?.M`, `M<T>`, casts, `as`,
     `await`), locals (`var x = new T()`, `T x = new()`, `T x = ...`), poisoned names, interfaces, abstract and
     partial classes, extension methods as patches, the private `_cs` table (namespaces, usings, using static,
     global usings, aliases as imports), `#if` regions and parse errors (no site, census raised), BOM + CRLF, the
     canary; validate_facts on every fixture file.
  C  every reported shape gives an exact key and grade end to end, on BOTH engines' captures: an interface field
     assigned in a multi-line constructor, a primary constructor, a static `Type.M()`, `this.` into a partial part in
     another file (and that part's use of a field the other part declares), `base.` into a base class in another
     file, `var` / `T` / `new()` locals, parameters, `?.`, `Get<Order>()`, a bare `Gen<int>()` (the engine never
     links a generic call), `using static`, an extension method, attribute-line callers, interface dispatch (one
     implementer INFERRED, two AMBIGUOUS each, five: only the interface member), the engine's receiver-blind
     same-file tuple replaced.
  D  must-nots beside positive siblings, each with no typed key AND no removal: an `await` result, an extension on a
     builtin type (the engine's correct same-file tuple stays), an inherited external member named like a project
     type (`Request.Log()` in a ControllerBase subclass), a delegate field call, a type name ambiguous between two
     usings, an overloaded caller (one engine node for two scopes), an `#if` region, five implementers (nothing
     removed); and, in C's exact expectations,
     Color Color, an instance method beating an extension, the namespace chain beating a using, the caller's own
     .csproj winning a duplicated full name, BOM + CRLF, and a test fake seen only by a test caller.
  V  the review round's shapes (TREES["review"]), each must-not beside its positive sibling, the expectations the
     bindings Roslyn 4.8 gives (its dispatch set for a virtual / interface call): a local of an inner block (if,
     foreach, catch, for) binds only inside it, after it the name is the field (no EXTRACTED upgrade to the local's
     type); a type name means a nested type of the class, an outer class or a base class before the namespace's; a
     member hit with a rival of its name (an extension of another signature, a base overload) is typed only when the
     argument types are known to fit it, and then never EXTRACTED; a virtual / abstract class member is dispatch (the
     declared member INFERRED, never EXTRACTED, plus the overrides: 1 INFERRED, 2..4 AMBIGUOUS, more none and no
     REPLACE; a `new virtual` starts another slot; a sealed override, a sealed class or struct receiver and an
     unrebound construction are exact; a
     test-file override unseen by a production caller); interface implementers only through a definition that
     implements the member (never a `new` method of a subclass that does not re-list the interface); a static
     interface member is an exact call with no implementers (EXTRACTED in its own file); a closed generic receiver
     (`IRepo<Order>`) skips an implementer of other concrete arguments, a covariant one keeps a subtype's; the new
     stats counters.
  W  the re-check round's shapes (TREES["recheck"], Roslyn 4.8's bindings), on the typed keys AND the edges the
     whole push gives (link, the bare-call and local-def passes, _resolve_calls): an override of a GENERIC base
     member (one and two hops) and a parameter type spelled with a namespace, `System.Int32`, an alias or a nullable
     annotation have the base's signature (no rival), while a base overload of another signature after
     substitution stays one (Store<Order>.Save); `T x = new T()` / `T x = new()` never rebound is exact (no
     override fan-out); a C# interface group whose dispatch set is fully known REPLACEs (the review tree's same-file
     HidingRunner / UserRepo / Factory.Make tuples are gone, the true implementers stay; the cqrs NotifyHandler),
     unless a class the pass cannot resolve may implement it (`: global::Ns.IPlugin`); a virtual REPLACE drops an
     override of another slot (UnderNew under a `new virtual`); the reader's msig / tporder / bsig tables.
  S  the fix round's shapes (TREES["sigs"], Roslyn 4.8's bindings with their run-time dispatch), on the typed keys AND
     the pushed edges: a class type parameter is its own type (`CartStore<T>.Save(Cart)` beside `Store<T>.Save(T)`
     stays a rival, the hit's own `Save(T)` / `Fill(T)` too, a generic `Remove(T)` beside `RepoBase.Remove(object)`:
     the engine's true tuple kept), while `Keep(T)` overriding `Store<T>.Keep(T)` is the same slot; override slots
     and interface mappings compared by signature (another signature in the middle of a chain or beside the override
     neither breaks nor continues the slot; an override of another signature is another slot's: the engine's tuple
     to it removed); a constructed local rebound by a deconstruction (nested, the `var` form), a ref local or a
     user-defined `++` dispatches, one never rebound stays exact; two external types of one simple name are two
     types (both qualified differently, or unqualified under another namespace's using), one written qualified, under
     its using or through a namespace alias is one; the three-valued type comparison; the reader's rebound names.
  N  fix round 2's shapes (TREES["rnd2"], Roslyn 4.8's bindings with their run-time dispatch), on the typed keys AND
     the pushed edges: an unconstrained `T?` closed by int / a struct / an enum is T (the override is the slot's, the
     interface implementer the class's own `PutM1(int)`), a `where T : struct` one is Nullable<T>; nint / IntPtr,
     Nullable<int> / int? and a `global using` alias (of CancellationToken, of a project class, of IntPtr) are one
     type, an MSBuild <Using> alias the pass cannot read is unknown (nothing removed); a private middle method
     neither breaks nor continues a slot, an `internal new virtual` of the same project starts another; an
     override's rival between it and its slot's declarer leaves the call unknown; a subclass closing the generic
     receiver class with other type arguments is no target; two unqualified external names are one type only under
     usings that resolve them alike (the same, a subset, or both importing a namespace known to hold the name;
     another file's, another namespace block's, a nearer block's using: unknown), a project's `global using` and an
     enclosing namespace prove one, another project's never; the reader's tpkind / galias / cvis tables and the
     binder's `global using` aliases.
  E  constructor and computed-property scopes map to the members graphify_extract adds (C5: `Owner.Owner`,
     `Owner.Prop` :Method nodes at the declaration's first line), tested with synthetic nodes shaped per C5 (and
     against the real nodes when graphify_extract makes them); without those nodes they emit nothing. The product's
     constructor rule (CR-1): ONE node per type, at its first INSTANCE constructor (a static one only when it is the
     sole one), so only that constructor's calls are typed (static then instance; two instance; static only).
  F  engine parity: identical typed tuples on version-stable keys across the 0.8.39 and 0.8.51 captures, records
     allowlisted (0.8.39 gives a record no owner when graphify_extract's C# members are off).
  G  flags: DEEPMAP_TYPED_CALLS=0, =1 and =python give the SAME list object and leave every C# caller untouched;
     =csharp leaves the .py caller untouched; DEEPMAP_TYPED_CALLS_IFACE=static / impl, an ON word = every mode, an
     unknown token ignored and reported (stats unknown_iface), an OFF word = none, on interface AND virtual dispatch;
     no C# grammar -> the canary fails and only csharp is disabled.
  H  call_lines: every typed key the pass emits gets call_lines["key"][key] = the first call line of it in the
     caller (the minimum with a line already there; the engine's "name" entries kept), rolled back on an error.
  R  the installed engine's real extraction of every tree equals its captured fixture.
  M  mutation teeth: each reader / binder rule broken in a copy of the module turns its checks RED.

Every variant pins its flags (`0` where off, never "unset = off"), so a later default flip cannot move these
expectations; DEEPMAP_CSHARP_MEMBERS is pinned 0 except in E.

Fixtures: tests/fixtures/typed_calls/<0839|0851>/cs/<tree>.json, the REAL graph.json graphify 0.8.39 / 0.8.51
wrote for TREES[<tree>] (`--capture` re-writes the installed engine's bucket; run it under both engines after
editing a tree).

Usage: python tests/typed_calls_cs_gates.py [--capture[=<tree>,...] | --dump] [--root <tree>]
       (--root: load build/ from another checkout, the RED-first run)
"""
from __future__ import annotations

import ast
import contextlib
import copy
import hashlib
import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FX = REPO / "tests" / "fixtures" / "typed_calls"
BUCKETS = {"0.8.39": "0839", "0.8.51": "0851"}
_ONLY: list = []                 # `--capture=<tree>,<tree>`: re-capture only those trees


def _parse_args(argv: list) -> tuple:
    root, mode, it = None, "", iter(argv)
    for arg in it:
        if arg == "--root":
            root = next(it, "")
        elif arg.startswith("--root="):
            root = arg[len("--root="):]
        elif arg in ("--capture", "--dump"):
            mode = arg[2:]
        elif arg.startswith("--capture="):
            mode = "capture"
            _ONLY.extend(t for t in arg[len("--capture="):].split(",") if t)
        else:
            return None, None, f"unknown argument {arg!r}"
        if root is not None and (not root or not (Path(root) / "build").is_dir()):
            return None, None, f"--root needs a directory that holds build/ (got {root!r})"
    return (Path(root).resolve() if root else REPO), mode, None


ROOT, MODE, _USAGE = _parse_args(sys.argv[1:]) if __name__ == "__main__" else (REPO, "", None)
if _USAGE:
    print(f"usage: python tests/typed_calls_cs_gates.py [--capture[=<tree>,...] | --dump] [--root <tree>]  "
          f"({_USAGE})", file=sys.stderr)
    raise SystemExit(2)
BUILD = ROOT / "build"
sys.path.insert(0, str(BUILD))

import graphify_extract as GX  # noqa: E402
import sync as S               # noqa: E402

_IMPORT_ERRORS: list = []


def _try_import(name):
    """A build/ module, or None on the pre-feature tree (RED, never a crash)."""
    try:
        return importlib.import_module(name)
    except Exception as exc:  # noqa: BLE001
        _IMPORT_ERRORS.append(f"{name}: {exc!r}")
        return None


TC = _try_import("typed_calls")
RCS = _try_import("receiver_facts_csharp")
_HAS_ENGINE = importlib.util.find_spec("graphify") is not None


class Rec:
    """Check recorder. loud=True prints every check (the real run); loud=False only counts (mutation runs)."""

    def __init__(self, loud: bool = True):
        self.loud, self.passed, self.failed, self.fails = loud, 0, 0, []

    def check(self, label: str, cond, detail="") -> bool:
        ok = bool(cond)
        if ok:
            self.passed += 1
        else:
            self.failed += 1
            self.fails.append(label)
        if self.loud:
            print(f"  [{'PASS' if ok else 'FAIL'}] {label}"
                  + ("" if ok or detail == "" else f" -- {str(detail)[:900]}"))
        return ok


_REC = Rec()


def check(label: str, cond, detail="") -> bool:
    return _REC.check(label, cond, detail)


@contextlib.contextmanager
def recording(rec: Rec):
    global _REC
    old, _REC = _REC, rec
    try:
        yield rec
    finally:
        _REC = old


def rcs():
    """The reader module in use (sys.modules: a mutation run swaps it)."""
    return sys.modules.get("receiver_facts_csharp", RCS)


def tc():
    """The core module in use (sys.modules: a mutation run swaps it, and the reader builds its IR with it)."""
    return sys.modules.get("typed_calls", TC)


@contextlib.contextmanager
def clean_env(**kv):
    """Every DEEPMAP_* variable except DEEPMAP_HOME removed, then `kv` applied (None = unset); restored after."""
    saved = {k: v for k, v in os.environ.items() if k.startswith("DEEPMAP_")}
    try:
        for k in list(saved):
            if k != "DEEPMAP_HOME":
                os.environ.pop(k, None)
        for k, v in kv.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k in [k for k in os.environ if k.startswith("DEEPMAP_")]:
            os.environ.pop(k, None)
        os.environ.update(saved)


# ================================================================================================
# The fixture trees
# ================================================================================================

CSPROJ = '<Project Sdk="Microsoft.NET.Sdk">\n</Project>\n'
CQRS = {
    "src/Shop/Shop.csproj": CSPROJ,
    "src/Shop/Carts/Cart.cs": """namespace Shop.Carts;

public class Cart
{
    public int UserId { get; set; }

    public List<CartItem> Items { get; set; } = new();

    public void Recalculate()
    {
    }
}

public class CartItem
{
    public decimal Price { get; set; }
}
""",
    "src/Shop/Carts/IShoppingCartHelper.cs": """namespace Shop.Carts;

public interface IShoppingCartHelper
{
    Task<Cart> CreateShoppingCartAsync(int userId);

    void SaveCartItemsAsync(Cart cart);
}
""",
    "src/Shop/Carts/ShoppingCartHelper.cs": """namespace Shop.Carts;

public class ShoppingCartHelper : IShoppingCartHelper
{
    public Task<Cart> CreateShoppingCartAsync(int userId)
    {
        var cart = new Cart();
        cart.Recalculate();
        return Task.FromResult(cart);
    }

    public void SaveCartItemsAsync(Cart cart)
    {
    }

    public static List<CartItem> BuildCartItems(Cart cart)
    {
        return cart.Items;
    }
}
""",
    "src/Shop/Orders/Order.cs": """namespace Shop.Orders;

public class Order
{
    public int Id { get; set; }

    public decimal Total { get; set; }

    public void Validate()
    {
    }
}
""",
    "src/Shop/Orders/OrderLine.cs": """namespace Shop.Orders;

public record OrderLine(int Qty, decimal Price)
{
    public void Check(Order order)
    {
        order.Validate();
    }
}
""",
    "src/Shop/Orders/OrderService.cs": """namespace Shop.Orders;

public class OrderService
{
    public void Ship(int id)
    {
    }

    public void Cancel(int id)
    {
    }
}
""",
    "src/Shop/Orders/IOrderRepository.cs": """using Shop.Carts;

namespace Shop.Orders;

public interface IOrderRepository
{
    T Get<T>(int id);

    void Add(Cart cart);
}

public class OrderRepository : IOrderRepository
{
    public T Get<T>(int id) => default;

    public void Add(Cart cart)
    {
    }
}
""",
    "src/Shop/Orders/CreateOrderHandler.cs": """using Shop.Carts;

namespace Shop.Orders;

public partial class CreateOrderHandler : IRequestHandler<CreateOrderCommand, OrderResult>
{
    private readonly IShoppingCartHelper _cartHelper;
    private readonly IOrderRepository _repo;

    public CreateOrderHandler(
        IShoppingCartHelper cartHelper,
        IOrderRepository repo)
    {
        _cartHelper = cartHelper;
        _repo = repo;
    }

    [Obsolete]
    [HttpPost]
    public async Task<OrderResult> Handle(CreateOrderCommand request, CancellationToken ct)
    {
        var cart = await _cartHelper.CreateShoppingCartAsync(request.UserId);
        var items = ShoppingCartHelper.BuildCartItems(cart);
        _cartHelper.SaveCartItemsAsync(cart);
        cart.Recalculate();
        this.Persist(cart);
        var order = _repo.Get<Order>(request.Id);
        return new OrderResult();
    }
}
""",
    "src/Shop/Orders/CreateOrderHandler.Persist.cs": """using Shop.Carts;

namespace Shop.Orders;

public partial class CreateOrderHandler
{
    private void Persist(Cart cart)
    {
        _repo.Add(cart);
    }
}
""",
    "src/Shop/Orders/CancelOrderHandler.cs": """using Shop.Carts;

namespace Shop.Orders;

public class CancelOrderHandler(IShoppingCartHelper helper, OrderService svc)
{
    public void Handle(CancelOrderCommand cmd)
    {
        helper.SaveCartItemsAsync(cmd.Cart);
        svc.Cancel(cmd.Id);
        ShoppingCartHelper.BuildCartItems(cmd.Cart);
    }
}
""",
    "src/Shop/Orders/OrderHandlerBase.cs": """namespace Shop.Orders;

public abstract class OrderHandlerBase
{
    protected virtual void Audit(string what)
    {
    }
}
""",
    "src/Shop/Orders/ShipOrderHandler.cs": """namespace Shop.Orders;

public class ShipOrderHandler : OrderHandlerBase
{
    protected override void Audit(string what)
    {
        base.Audit(what);
    }

    public void Handle(ShipOrderCommand cmd, OrderService svc)
    {
        svc.Ship(cmd.Id);
        Audit("ship");
        var o = new Order();
        o.Validate();
        Order p = new();
        p.Validate();
        OrderService s2 = svc;
        s2?.Ship(cmd.Id);
        Gen<int>();
    }

    private T Gen<T>() => default;
}

public class ShipAudit
{
    public void Validate()
    {
    }
}
""",
    "src/Shop/Members/CartTotals.cs": """namespace Shop.Members;

public class CartTotals
{
    private readonly OrderPricer _pricer;

    [Inject]
    public CartTotals(OrderPricer pricer)
    {
        _pricer = pricer;
        _pricer.Warm();
    }

    public CartTotals(OrderPricer pricer, int scale)
    {
        _pricer = pricer;
        _pricer.Warm();
    }

    [JsonIgnore]
    public decimal ItemsCalculatedPrice => _pricer.Price(1);

    public decimal Discount
    {
        get { return _pricer.Price(2); }
    }

    public decimal Plain { get; set; }
}

public class OrderPricer
{
    public void Warm()
    {
    }

    public decimal Price(int n) => n;
}
""",
    "src/Shop/Pricing/PriceMath.cs": """using Shop.Orders;

namespace Shop.Pricing;

public static class PriceMath
{
    public static decimal RoundPrice(decimal p)
    {
        return p;
    }

    public static decimal Discounted(this Order o, decimal pct)
    {
        return o.Total * pct;
    }

    public static void Validate(this Order o)
    {
    }

    public static decimal ApplyTax(this decimal p, decimal rate)
    {
        return p * rate;
    }

    public static decimal Gross(decimal p)
    {
        return p.ApplyTax(1.2m);
    }
}
""",
    "src/Shop/Pricing/PricingHandler.cs": """using static Shop.Pricing.PriceMath;
using Shop.Orders;

namespace Shop.Pricing;

public class PricingHandler
{
    public decimal Handle(Order order)
    {
        var rounded = RoundPrice(order.Total);
        var cut = order.Discounted(10m);
        order.Validate();
        return rounded + cut;
    }
}
""",
    "src/Shop/Notify/INotifier.cs": """namespace Shop.Notify;

public interface INotifier
{
    void Notify(string message);
}

public class EmailNotifier : INotifier
{
    public void Notify(string message)
    {
    }
}

public class SmsNotifier : INotifier
{
    public void Notify(string message)
    {
    }
}

public class NotifyHandler
{
    private readonly INotifier _notifier;

    public void Handle(string message)
    {
        _notifier.Notify(message);
    }
}
""",
    "src/Shop/Steps/IStep.cs": """namespace Shop.Steps;

public interface IStep
{
    void Execute();
}

public class StepA : IStep
{
    public void Execute()
    {
    }
}

public class StepB : IStep
{
    public void Execute()
    {
    }
}

public class StepC : IStep
{
    public void Execute()
    {
    }
}

public class StepD : IStep
{
    public void Execute()
    {
    }
}

public class StepE : IStep
{
    public void Execute()
    {
    }
}

public class StepRunner
{
    public void Run(IStep step)
    {
        step.Execute();
    }
}
""",
    "src/Shop/Web/Request.cs": """namespace Shop.Web;

public class Request
{
    public static void Log()
    {
    }
}
""",
    "src/Shop/Web/User.cs": """namespace Shop.Web;

public class User
{
    public static void Find()
    {
    }
}
""",
    "src/Shop/Web/OrdersController.cs": """using Microsoft.AspNetCore.Mvc;

namespace Shop.Web;

public class OrdersController : ControllerBase
{
    public void Get()
    {
        Request.Log();
        User.Find();
    }
}

public class PlainController
{
    public void Get()
    {
        User.Find();
    }
}
""",
    "src/Shop/Paint/Color.cs": """namespace Shop.Paint;

public class Color
{
    public static Color FromName(string name)
    {
        return new Color();
    }

    public string Hex()
    {
        return "";
    }
}

public class Status
{
    public string Hex()
    {
        return "";
    }
}
""",
    "src/Shop/Paint/Canvas.cs": """namespace Shop.Paint;

public class Canvas
{
    public Color Color { get; set; }

    public void Apply()
    {
        Color.Hex();
        Color.FromName("red");
    }
}

public class Badge
{
    public Status Color { get; set; }

    public void Apply()
    {
        Color.Hex();
    }
}
""",
    "src/Shop/Billing/Invoice.cs": """namespace Shop.Billing;

public class Invoice
{
    public void Send()
    {
    }
}
""",
    "src/Shop/Legacy/Invoice.cs": """namespace Shop.Legacy;

public class Invoice
{
    public void Send()
    {
    }
}
""",
    "src/Shop/Billing/InvoiceJob.cs": """using Shop.Legacy;

namespace Shop.Billing;

public class InvoiceJob
{
    public void Run(Invoice invoice)
    {
        invoice.Send();
    }
}
""",
    "src/Shop/Reports/ReportJob.cs": """using Shop.Billing;
using Shop.Legacy;

namespace Shop.Reports;

public class ReportJob
{
    public void Run(Invoice invoice)
    {
        invoice.Send();
    }
}
""",
    "src/Shop/Misc/AlertSender.cs": """namespace Shop.Misc;

public class Mailer
{
    public void Send(string message)
    {
    }
}

public class AlertSender
{
    private readonly Action<string> _send;
    private readonly Mailer _mailer;

    public void Alert(string message)
    {
        _send(message);
        _mailer.Send(message);
    }
}
""",
    "src/Shop/Misc/Dispatcher.cs": """using Shop.Orders;

namespace Shop.Misc;

public class Dispatcher
{
    private readonly OrderService _svc;

    public void Run(int id)
    {
        _svc.Ship(id);
    }

    public void Run(string name)
    {
        _svc.Cancel(1);
    }

    public void Once()
    {
        _svc.Ship(2);
    }
}
""",
    "src/Shop/Misc/Toggle.cs": """using Shop.Orders;

namespace Shop.Misc;

public class Toggle
{
    private readonly OrderService _svc;

    public void Run()
    {
#if DEBUG
        _svc.Ship(1);
#else
        _svc.Cancel(1);
#endif
    }

    public void Plain()
    {
        _svc.Ship(2);
    }
}
""",
    "src/Shop/Misc/Windowsy.cs": ("﻿using Shop.Orders;\r\n\r\nnamespace Shop.Misc;\r\n\r\npublic class Windowsy\r\n{\r\n"
                                  "    private readonly OrderService _svc;\r\n\r\n    public void Run()\r\n    {\r\n"
                                  "        _svc.Ship(3);\r\n    }\r\n}\r\n"),
    "src/Admin/Admin.csproj": CSPROJ,
    "src/Admin/Models/Order.cs": """namespace Shop.Orders;

public class Order
{
    public void Validate()
    {
    }
}
""",
    "src/Admin/AdminJob.cs": """using Shop.Orders;

namespace Shop.Admin;

public class AdminJob
{
    public void Run(Order order)
    {
        order.Validate();
    }
}
""",
    "tests/Shop.Tests/Shop.Tests.csproj": CSPROJ,
    "tests/Shop.Tests/FakeCartHelper.cs": """using Shop.Carts;

namespace Shop.Tests;

public class FakeCartHelper : IShoppingCartHelper
{
    public Task<Cart> CreateShoppingCartAsync(int userId)
    {
        return Task.FromResult(new Cart());
    }

    public void SaveCartItemsAsync(Cart cart)
    {
    }
}

public class CartTests
{
    private readonly IShoppingCartHelper _helper;

    public void Creates()
    {
        _helper.CreateShoppingCartAsync(1);
    }
}
""",
    "tools/report.py": """def helper():
    return 1


def build():
    return helper()
""",
}
# The review round's shapes (section V), each must-not beside its positive sibling. Every call is legal C#, and
# V_EXPECT names, per caller, the binding Roslyn gives it (the dispatch set for a virtual / interface call).
REVIEW = {
    "src/Rv/Rv.csproj": CSPROJ,
    # cs-block-scoped-local-leak: a local of an inner block binds only inside it
    "src/Rv/Scope/Router.cs": """namespace Rv.Scope;

public class Mailer
{
    public void Flush()
    {
    }
}

public class Texter
{
    public void Flush()
    {
    }
}

public class Router
{
    private readonly Mailer sender = new Mailer();

    public void Route(bool a)
    {
        if (a)
        {
            Texter sender = new Texter();
        }
        sender.Flush();
    }

    public void RouteEach(Texter[] all)
    {
        foreach (Texter sender in all)
        {
        }
        sender.Flush();
    }

    public void RouteCatch()
    {
        try
        {
        }
        catch (TexterError sender)
        {
        }
        sender.Flush();
    }

    public void RouteFor()
    {
        for (Texter sender = null; ; )
        {
            break;
        }
        sender.Flush();
    }

    public void Inside(bool a)
    {
        if (a)
        {
            Texter sender = new Texter();
            sender.Flush();
        }
    }

    public void Pattern(object o)
    {
        if (!(o is Texter t))
        {
            return;
        }
        t.Flush();
    }

    public void Sections(int k)
    {
        switch (k)
        {
            case 1:
                Texter s = new Texter();
                s.Flush();
                break;
            default:
                s = new Texter();
                s.Flush();
                break;
        }
    }
}

public class TexterError : System.Exception
{
    public void Flush()
    {
    }
}
""",
    # cs-nested-type-shadow: a nested type before a namespace-level one of that name
    "src/Rv/Nest/Node.cs": """namespace Rv.Nest;

public class Node
{
    public void Visit()
    {
    }
}

public class Leaf
{
    public void Grow()
    {
    }
}
""",
    "src/Rv/Nest/Tree.cs": """namespace Rv.Nest;

public class Tree
{
    public class Node
    {
        public void Visit()
        {
        }
    }

    public class Walker
    {
        public void Go()
        {
            Node n = new Node();
            n.Visit();
        }
    }

    private readonly Node _root = new Node();

    public void Walk()
    {
        Node n = new Node();
        n.Visit();
    }

    public void Walk2()
    {
        var n = new Node();
        n.Visit();
    }

    public void Walk3()
    {
        _root.Visit();
    }

    public void Walk4(Node p)
    {
        p.Visit();
    }
}

public class TreeBase
{
    public class Leaf
    {
        public void Grow()
        {
        }
    }
}

public class Forest : TreeBase
{
    public void Plant()
    {
        Leaf l = new Leaf();
        l.Grow();
    }
}

public class Garden
{
    public void Plant()
    {
        Leaf l = new Leaf();
        l.Grow();
    }
}
""",
    "src/Rv/Nest/NodeController.cs": """using Microsoft.AspNetCore.Mvc;

namespace Rv.Nest;

public class NodeController : ControllerBase
{
    private readonly Node _node = new Node();

    public void Get()
    {
        _node.Visit();
    }
}
""",
    # cs-arg-type-applicability: an extension method / a base overload the arguments fit instead of the hit
    "src/Rv/Args/Widget.cs": """namespace Rv.Args;

public class Widget
{
    public void Paint(int color)
    {
    }

    public void Tint(int level)
    {
    }
}

public class Painter
{
    private readonly Widget _w = new Widget();

    public void UseString()
    {
        _w.Paint("red");
    }

    public void UseInt()
    {
        _w.Paint(3);
    }

    public void UseShort(short c)
    {
        _w.Paint(c);
    }

    public void UseLongLiteral()
    {
        _w.Tint(5L);
    }

    public void UseIntLiteral()
    {
        _w.Tint(5);
    }
}
""",
    "src/Rv/Args/WidgetExt.cs": """namespace Rv.Args;

public static class WidgetExt
{
    public static void Paint(this Widget w, string colorName)
    {
    }

    public static void Tint(this Widget w, long level)
    {
    }
}
""",
    "src/Rv/Args/Store.cs": """namespace Rv.Args;

public class Cart
{
}

public class Order
{
}

public class StoreBase
{
    public void Save(Cart c)
    {
    }

    public virtual void Keep(Cart c)
    {
    }
}

public class OrderStore : StoreBase
{
    public void Save(Order o)
    {
    }

    public override void Keep(Cart c)
    {
    }
}

public class StoreUser
{
    private readonly OrderStore _s = new OrderStore();

    public void Go(Cart c)
    {
        _s.Save(c);
    }

    public void GoOrder(Order o)
    {
        _s.Save(o);
    }

    public void GoKeep(Cart c)
    {
        _s.Keep(c);
    }
}
""",
    # cs-typed-note-virtual-dispatch: a virtual / abstract class member is dispatch
    "src/Rv/Virt/HandlerBase.cs": """namespace Rv.Virt;

public abstract class HandlerBase
{
    public void Run()
    {
        HandleCore();
    }

    protected abstract void HandleCore();
}
""",
    "src/Rv/Virt/OrderHandler.cs": """namespace Rv.Virt;

public class OrderHandler : HandlerBase
{
    protected override void HandleCore()
    {
    }
}
""",
    "src/Rv/Virt/Notifiers.cs": """namespace Rv.Virt;

public abstract class NotifierBase
{
    public abstract void Send(string message);
}

public class MailNotifier : NotifierBase
{
    public override void Send(string message)
    {
    }
}

public class SmsNotifier : NotifierBase
{
    public override void Send(string message)
    {
    }
}

public class NotifySvc
{
    private readonly NotifierBase _n;

    public NotifySvc(NotifierBase n)
    {
        _n = n;
    }

    public void Notify()
    {
        _n.Send("x");
    }
}
""",
    "src/Rv/Virt/Shapes.cs": """namespace Rv.Virt;

public class Shape
{
    public virtual double Area()
    {
        return 0;
    }
}

public class Circle : Shape
{
    public sealed override double Area()
    {
        return 1;
    }
}

public class ShapeUser
{
    public double Any(Shape s)
    {
        return s.Area();
    }

    public double Round(Circle c)
    {
        return c.Area();
    }

    public double Fresh()
    {
        var s = new Shape();
        return s.Area();
    }

    public double Again(Shape other)
    {
        var s = new Shape();
        s = other;
        return s.Area();
    }
}
""",
    "src/Rv/Virt/Slots.cs": """namespace Rv.Virt;

public class Base
{
    public virtual void Go()
    {
    }
}

public class Mid : Base
{
    public new virtual void Go()
    {
    }
}

public class Low : Mid
{
    public override void Go()
    {
    }
}

public class GoUser
{
    public void Use(Base b)
    {
        b.Go();
    }

    public void UseMid(Mid m)
    {
        m.Go();
    }
}
""",
    "src/Rv/Virt/Jobs.cs": """namespace Rv.Virt;

public abstract class Job
{
    public abstract void Exec();
}

public class Job1 : Job
{
    public override void Exec()
    {
    }
}

public class Job2 : Job
{
    public override void Exec()
    {
    }
}

public class Job3 : Job
{
    public override void Exec()
    {
    }
}

public class Job4 : Job
{
    public override void Exec()
    {
    }
}

public class Job5 : Job
{
    public override void Exec()
    {
    }
}

public class JobRunner
{
    public void Run(Job j)
    {
        j.Exec();
    }
}

public class Shell
{
    public void Exec()
    {
    }
}
""",
    "src/Rv/Virt/Panels.cs": """namespace Rv.Virt;

public class Panel
{
    public virtual void Draw()
    {
    }
}

public sealed class GlassPanel : Panel
{
    public override void Draw()
    {
    }
}

public struct Money
{
    public override string ToString()
    {
        return "";
    }

    public string Show()
    {
        return ToString();
    }
}

public class PanelUser
{
    public void Any(Panel p)
    {
        p.Draw();
    }

    public void Glass(GlassPanel g)
    {
        g.Draw();
    }
}
""",
    # cs-iface-impl-new-hidden / an override / a re-listed interface
    "src/Rv/Iface/Runners.cs": """namespace Rv.Iface;

public interface IRunner
{
    void Run();
}

public class BaseRunner : IRunner
{
    public void Run()
    {
    }
}

public class HidingRunner : BaseRunner
{
    public new void Run()
    {
    }
}

public class UseRunner
{
    private readonly IRunner _runner;

    public UseRunner(IRunner runner)
    {
        _runner = runner;
    }

    public void Go()
    {
        _runner.Run();
    }
}

public interface IWalker
{
    void Walk();
}

public class BaseWalker : IWalker
{
    public virtual void Walk()
    {
    }
}

public class FastWalker : BaseWalker
{
    public override void Walk()
    {
    }
}

public class UseWalker
{
    public void Go(IWalker w)
    {
        w.Walk();
    }
}

public interface IPinger
{
    void Ping();
}

public class BasePinger : IPinger
{
    public void Ping()
    {
    }
}

public class LoudPinger : BasePinger, IPinger
{
    public new void Ping()
    {
    }
}

public class UsePinger
{
    public void Go(IPinger p)
    {
        p.Ping();
    }
}
""",
    # cs-iface-static-member: a static interface member has no implementers
    "src/Rv/Iface/Factory.cs": """namespace Rv.Iface;

public interface IFactory
{
    static IFactory Make()
    {
        return null;
    }

    void Build();
}

public class Factory : IFactory
{
    public IFactory Make()
    {
        return this;
    }

    public void Build()
    {
    }
}

public class FactoryUser
{
    public void Use(IFactory g)
    {
        var f = IFactory.Make();
        g.Build();
    }
}
""",
    # cs-iface-generic-closed-args: a closed generic receiver, a type-parameter receiver, a variant interface
    "src/Rv/Iface/Repos.cs": """namespace Rv.Iface;

public class Order
{
}

public class User
{
}

public interface IRepo<T>
{
    T Find(int id);
}

public class OrderRepo : IRepo<Order>
{
    public Order Find(int id)
    {
        return null;
    }
}

public class UserRepo : IRepo<User>
{
    public User Find(int id)
    {
        return null;
    }
}

public class RepoUser
{
    private readonly IRepo<Order> _orders;

    public RepoUser(IRepo<Order> orders)
    {
        _orders = orders;
    }

    public void Use()
    {
        _orders.Find(1);
    }
}

public class AnyRepoUser<T>
{
    private readonly IRepo<T> _repo;

    public AnyRepoUser(IRepo<T> repo)
    {
        _repo = repo;
    }

    public void Use()
    {
        _repo.Find(2);
    }
}

public interface ISource<out T>
{
    T Get();
}

public class OrderSource : ISource<Order>
{
    public Order Get()
    {
        return null;
    }
}

public class UserSource : ISource<User>
{
    public User Get()
    {
        return null;
    }
}

public class SourceUser
{
    public void Use(ISource<Order> s)
    {
        s.Get();
    }
}

public class Animal
{
}

public class Dog : Animal
{
}

public class AnimalSource : ISource<Animal>
{
    public Animal Get()
    {
        return null;
    }
}

public class DogSource : ISource<Dog>
{
    public Dog Get()
    {
        return null;
    }
}

public class PetUser
{
    public void Use(ISource<Animal> s)
    {
        s.Get();
    }
}
""",
    # CR-1: constructor scopes (static first then instance; two instance constructors; static only)
    "src/Rv/Ctors/Carts.cs": """namespace Rv.Ctors;

public class Pricer
{
    public decimal Price(int n)
    {
        return n;
    }

    public void Warm()
    {
    }

    public static void Boot()
    {
    }

    public static void Touch()
    {
    }
}

public class CartA
{
    private readonly Pricer _p = new Pricer();

    static CartA()
    {
        Pricer.Touch();
    }

    public CartA()
    {
        _p.Price(0);
    }
}

public class CartB
{
    private readonly Pricer _p = new Pricer();

    public CartB()
    {
        _p.Price(1);
    }

    public CartB(int s)
    {
        _p.Warm();
    }
}

public class CartC
{
    static CartC()
    {
        Pricer.Boot();
    }
}
""",
    # a test-file override and a test-file implementer: production callers never see them
    "tests/Rv.Tests/Rv.Tests.csproj": CSPROJ,
    "tests/Rv.Tests/Fakes.cs": """using Rv.Virt;
using Rv.Iface;

namespace Rv.Tests;

public class FakeHandler : HandlerBase
{
    protected override void HandleCore()
    {
    }
}

public class FakeRepo : IRepo<Order>
{
    public Order Find(int id)
    {
        return null;
    }
}
""",
}
# The re-check round's shapes (section W): an override of a generic base member and parameter types spelled
# differently, a construction of a local's declared type, a static interface member, interface / virtual calls
# where `new` methods start other slots, an implementer the pass cannot see. Every call is legal C#; W_EXPECT
# names the binding Roslyn 4.8 gives it.
RECHECK = {
    "src/Rc/Rc.csproj": CSPROJ,
    # rcReader new defect 1: an override of a GENERIC base member (one hop and two hops) is no rival
    "src/Rc/Gen/Handlers.cs": """using System.Threading;
using System.Threading.Tasks;

namespace Rc.Gen;

public class CreateOrder
{
}

public abstract class BaseHandler<TReq, TRes>
{
    public abstract Task<TRes> Handle(TReq request, CancellationToken ct);

    public virtual void Audit(TReq request)
    {
    }
}

public class OrderHandler : BaseHandler<CreateOrder, int>
{
    public override Task<int> Handle(CreateOrder request, CancellationToken ct)
    {
        return Task.FromResult(1);
    }

    public override void Audit(CreateOrder request)
    {
    }
}

public abstract class MidHandler<T> : BaseHandler<T, int>
{
}

public class LowHandler : MidHandler<CreateOrder>
{
    public override Task<int> Handle(CreateOrder request, CancellationToken ct)
    {
        return Task.FromResult(2);
    }
}
""",
    "src/Rc/Gen/Api.cs": """using System.Threading;
using System.Threading.Tasks;

namespace Rc.Gen;

public class Api
{
    private readonly OrderHandler _handler = new OrderHandler();
    private readonly LowHandler _low = new LowHandler();

    public CreateOrder Make()
    {
        return null;
    }

    public Task<int> Post(CreateOrder cmd, CancellationToken ct)
    {
        return _handler.Handle(cmd, ct);
    }

    public Task<int> PostNone()
    {
        return _handler.Handle(Make(), CancellationToken.None);
    }

    public void AuditUnknown()
    {
        _handler.Audit(Make());
    }

    public Task<int> PostLow(CreateOrder cmd, CancellationToken ct)
    {
        return _low.Handle(cmd, ct);
    }
}
""",
    # ... while a base overload of ANOTHER signature after substitution stays a rival (Store<Order>.Save(Order))
    "src/Rc/Gen/Stores.cs": """namespace Rc.Gen;

public class Cart
{
}

public class Order
{
}

public class Store<T>
{
    public void Save(T item)
    {
    }
}

public class CartStore : Store<Order>
{
    public void Save(Cart c)
    {
    }
}

public class StoreUser
{
    private readonly CartStore _s = new CartStore();

    public Order Next()
    {
        return null;
    }

    public void SaveOrder(Order o)
    {
        _s.Save(o);
    }

    public void SaveCart(Cart c)
    {
        _s.Save(c);
    }

    public void SaveNext()
    {
        _s.Save(Next());
    }
}
""",
    # a parameter type spelled with a namespace, a System name, an alias, a nullable annotation: one signature
    "src/Rc/Qual/Stores.cs": """using System.Threading;
using System.Threading.Tasks;
using CT = System.Threading.CancellationToken;

namespace Rc.Qual;

public class Cart
{
}

public class StoreBase
{
    public virtual void Save(Cart c)
    {
    }

    public virtual Task Send(CancellationToken ct)
    {
        return null;
    }

    public virtual void Count(int n)
    {
    }

    public virtual Task Stop(CancellationToken ct)
    {
        return null;
    }

    public virtual void Note(string text)
    {
    }
}

public class QualStore : StoreBase
{
    public override void Save(Rc.Qual.Cart c)
    {
    }

    public override Task Send(System.Threading.CancellationToken ct)
    {
        return null;
    }

    public override void Count(System.Int32 n)
    {
    }

    public override Task Stop(CT ct)
    {
        return null;
    }

    public override void Note(string? text)
    {
    }
}

public class QualUser
{
    private readonly QualStore _qual = new QualStore();

    public Cart GetCart()
    {
        return null;
    }

    public int Size()
    {
        return 1;
    }

    public string Text()
    {
        return "";
    }

    public void SaveUnknown()
    {
        _qual.Save(GetCart());
    }

    public void SendDefault()
    {
        _qual.Send(default);
    }

    public void CountUnknown()
    {
        _qual.Count(Size());
    }

    public void StopDefault()
    {
        _qual.Stop(default);
    }

    public void NoteUnknown()
    {
        _qual.Note(Text());
    }
}
""",
    # rcReader new defect 2: a declared local that constructs exactly its declared type is exact
    "src/Rc/Fresh/Repos.cs": """namespace Rc.Fresh;

public class Repo
{
    public virtual void Save()
    {
    }
}

public class CachedRepo : Repo
{
    public override void Save()
    {
    }
}

public class RepoUser
{
    public void TypedNew()
    {
        Repo b = new Repo();
        b.Save();
    }

    public void TargetNew()
    {
        Repo c = new();
        c.Save();
    }

    public void Declared(Repo p)
    {
        p.Save();
    }

    public void BaseNew()
    {
        Repo d = new CachedRepo();
        d.Save();
    }

    public void Rebound(Repo other)
    {
        Repo e = new Repo();
        e = other;
        e.Save();
    }
}
""",
    # rcReader new defects 3 + 4: an interface call and a virtual call where `new` methods start other slots
    "src/Rc/Slots/Jobs.cs": """namespace Rc.Slots;

public interface IJob
{
    void Run();
}

public class JobBase : IJob
{
    public virtual void Run()
    {
    }
}

public class FastJob : JobBase
{
    public override void Run()
    {
    }
}

public class HideJob : FastJob
{
    public new void Run()
    {
    }
}

public class NewVirt : FastJob
{
    public new virtual void Run()
    {
    }
}

public class UnderNew : NewVirt
{
    public override void Run()
    {
    }
}

public class JobUser
{
    private readonly IJob _j = new JobBase();
    private readonly JobBase _jb = new FastJob();
    private readonly FastJob _fj = new HideJob();
    private readonly NewVirt _nv = new UnderNew();

    public void UseI()
    {
        _j.Run();
    }

    public void UseBase()
    {
        _jb.Run();
    }

    public void UseFast()
    {
        _fj.Run();
    }

    public void UseNv()
    {
        _nv.Run();
    }
}
""",
    # rcReader new defect 3: a static interface member is an exact call, here from another file
    "src/Rc/Iface/Factory.cs": """namespace Rc.Iface;

public interface IFactory
{
    static IFactory Make()
    {
        return null;
    }
}

public class Factory : IFactory
{
    public IFactory Make()
    {
        return this;
    }
}
""",
    "src/Rc/Iface/FactoryUser.cs": """namespace Rc.Iface;

public class FactoryUser
{
    public void Use()
    {
        var f = IFactory.Make();
    }
}
""",
    # the REPLACE guard: a class whose base the pass cannot resolve (`global::`) may implement the interface unseen
    "src/Rc/Plug/Plugins.cs": """namespace Rc.Plug;

public interface IPlugin
{
    void Load();
}

public class CorePlugin : IPlugin
{
    public void Load()
    {
    }
}

public class OddPlugin : global::Rc.Plug.IPlugin
{
    public void Load()
    {
    }
}

public class PluginHost
{
    private readonly IPlugin _p = new CorePlugin();

    public void Start()
    {
        _p.Load();
    }
}
""",
}
# The fix round's shapes (section S): an unfixed class type parameter is its own type (never the signature of a
# concrete overload), a same-named definition of another signature neither breaks nor continues an override slot, a
# deconstruction / ref local rebinds a constructed local, and two external types of one simple name are two types.
# Every call is legal C# (an unread external namespace aside); S_EXPECT names the binding Roslyn 4.8 gives it, with
# its run-time dispatch (AC1: the override that runs).
SIGS = {
    "src/Sg/Sg.csproj": CSPROJ,
    # D1: Store<T>.Save(T) is no signature of CartStore<T>.Save(Cart) (a rival), while CartStore<T>.Keep(T) overrides
    # Store<T>.Keep(T) (the same slot: no rival); Box<T>.Fill(T) against CartBox<T>.Fill(Cart) seen from CartBox itself
    "src/Sg/Open/Stores.cs": """namespace Sg.Open;

public class Order
{
}

public class Cart
{
}

public class Store<T>
{
    public void Save(T item)
    {
    }

    public virtual void Keep(T item)
    {
    }
}

public class CartStore<T> : Store<T>
{
    public void Save(Cart c)
    {
    }

    public override void Keep(T item)
    {
    }
}

public class Box<T>
{
    public void Fill(T item)
    {
    }
}

public class CartBox<T> : Box<T>
{
    public void Fill(Cart c)
    {
    }

    public void Go(T x)
    {
        Fill(x);
    }

    public void GoCart(Cart c)
    {
        Fill(c);
    }
}
""",
    "src/Sg/Open/StoreUser.cs": """namespace Sg.Open;

public class StoreUser
{
    private readonly CartStore<Order> _s;

    public void SaveOrder(Order o)
    {
        _s.Save(o);
    }

    public void SaveCart(Cart c)
    {
        _s.Save(c);
    }

    public void KeepOrder(Order o)
    {
        _s.Keep(o);
    }
}
""",
    # D1, the base class last in its file: the engine's tuple (the file's last Save) is the TRUE binding
    "src/Sg/Open/LastBase.cs": """namespace Sg.Open;

public class CartStoreX<T> : StoreX<T>
{
    public void Save(Cart c)
    {
    }
}

public class StoreX<T>
{
    public void Save(T item)
    {
    }
}

public class LastUser
{
    private readonly CartStoreX<Order> _s;

    public void SaveOrder(Order o)
    {
        _s.Save(o);
    }

    public void SaveCart(Cart c)
    {
        _s.Save(c);
    }
}
""",
    # D1: a generic repository over a non-generic base: Remove(T) is no signature of RepoBase.Remove(object)
    "src/Sg/Repos/Repos.cs": """namespace Sg.Repos;

public class Entity
{
}

public class Repository<T> : RepoBase
{
    public void Remove(T entity)
    {
    }

    public void Add(T entity)
    {
    }
}

public class RepoBase
{
    public void Remove(object id)
    {
    }
}

public class RepoUser
{
    private readonly Repository<Entity> _r;

    public void RemoveId(int id)
    {
        _r.Remove(id);
    }

    public void AddOne(Entity e)
    {
        _r.Add(e);
    }
}
""",
    # D2: a same-named definition of ANOTHER signature in the middle of an override chain (VM1, VM2) or beside the
    # override (VS3, VS4) neither breaks nor continues the slot; an override of another signature (VS6, VS7) overrides
    # another slot: it is no target of the call
    "src/Sg/Slots/Slots.cs": """namespace Sg.Slots;

public class VB1
{
    public virtual void Run1()
    {
    }
}

public class VM1 : VB1
{
    public void Run1(int x)
    {
    }
}

public class VS1 : VM1
{
    public override void Run1()
    {
    }
}

public interface IRun2
{
    void Run2();
}

public class VB2 : IRun2
{
    public virtual void Run2()
    {
    }
}

public class VM2 : VB2
{
    public void Run2(int x)
    {
    }
}

public class VS2 : VM2
{
    public override void Run2()
    {
    }
}

public interface IRun3
{
    void Run3();
}

public class VB3 : IRun3
{
    public virtual void Run3()
    {
    }
}

public class VS3 : VB3
{
    public void Run3(int x)
    {
    }

    public override void Run3()
    {
    }
}

public class VB4
{
    public virtual void Run4()
    {
    }
}

public class VS4 : VB4
{
    public void Run4(int x)
    {
    }

    public override void Run4()
    {
    }
}

public class VB6
{
    public virtual void Run6(int x)
    {
    }
}

public class VM6 : VB6
{
    public virtual void Run6()
    {
    }
}

public class VS6 : VM6
{
    public override void Run6(int x)
    {
    }
}

public interface IRun7
{
    void Run7();
}

public class VB7 : IRun7
{
    public virtual void Run7()
    {
    }

    public virtual void Run7(int x)
    {
    }
}

public class VS7 : VB7
{
    public override void Run7(int x)
    {
    }
}

public class SlotUser
{
    private readonly VB1 _b1;
    private readonly IRun2 _r2;
    private readonly IRun3 _r3;
    private readonly VB4 _b4;
    private readonly VM6 _m6;
    private readonly IRun7 _r7;

    public void Use1()
    {
        _b1.Run1();
    }

    public void Use2()
    {
        _r2.Run2();
    }

    public void Use3()
    {
        _r3.Run3();
    }

    public void Use4()
    {
        _b4.Run4();
    }

    public void Use6()
    {
        _m6.Run6();
    }

    public void Use7()
    {
        _r7.Run7();
    }
}
""",
    # D2, the common real shape: an implementer that overrides SaveAsync(o) and also defines SaveAsync(o, ct), and a
    # subclass overriding it again through that middle class
    "src/Sg/Slots/Persist.cs": """using System.Threading;
using System.Threading.Tasks;

namespace Sg.Slots;

public class Order
{
}

public interface IOrderStore
{
    Task SaveAsync(Order o);
}

public abstract class OrderStoreBase : IOrderStore
{
    public abstract Task SaveAsync(Order o);
}

public class CachedOrderStore : OrderStoreBase
{
    public override Task SaveAsync(Order o)
    {
        return Task.CompletedTask;
    }

    public Task SaveAsync(Order o, CancellationToken ct)
    {
        return Task.CompletedTask;
    }
}

public class TracingOrderStore : CachedOrderStore
{
    public override Task SaveAsync(Order o)
    {
        return Task.CompletedTask;
    }
}

public class PersistUser
{
    private readonly IOrderStore _store;
    private readonly OrderStoreBase _base;

    public Task Persist(Order o)
    {
        return _store.SaveAsync(o);
    }

    public Task PersistBase(Order o)
    {
        return _base.SaveAsync(o);
    }
}
""",
    # D3: a constructed local rebound by a deconstruction (also nested, also the `var` form), through a ref local or by
    # a user-defined `++` dispatches; one whose deconstruction rebinds only other locals stays exact
    "src/Sg/Locals/Locals.cs": """namespace Sg.Locals;

public class Repo
{
    public virtual void Keep()
    {
    }

    public static Repo operator ++(Repo r)
    {
        return r;
    }
}

public class CachedRepo : Repo
{
    public override void Keep()
    {
    }
}

public class LocalUser
{
    public void Deconstruct()
    {
        Repo b = new Repo();
        int y;
        (b, y) = (new CachedRepo(), 1);
        b.Keep();
    }

    public void DeconstructVar()
    {
        var b = new Repo();
        int y;
        (b, y) = (new CachedRepo(), 1);
        b.Keep();
    }

    public void RefLocal()
    {
        Repo b = new();
        ref Repo rb = ref b;
        rb = new CachedRepo();
        b.Keep();
    }

    public void Nested()
    {
        Repo b = new Repo();
        int y;
        int z;
        ((b, y), z) = ((new CachedRepo(), 1), 2);
        b.Keep();
    }

    public void Fresh()
    {
        Repo b = new Repo();
        int y;
        int z;
        (y, z) = (1, 2);
        b.Keep();
    }

    public void Increment()
    {
        Repo b = new Repo();
        b++;
        b.Keep();
    }
}
""",
    # D3, the subclass first in its file (the engine's tuple goes to the base's Keep4)
    "src/Sg/Locals/Reordered.cs": """namespace Sg.Locals;

public class CachedRepo4 : Repo4
{
    public override void Keep4()
    {
    }
}

public class Repo4
{
    public virtual void Keep4()
    {
    }
}

public class LocalUser4
{
    public void Deconstruct()
    {
        Repo4 b = new Repo4();
        int y;
        (b, y) = (new CachedRepo4(), 1);
        b.Keep4();
    }
}
""",
    # D4: two external types of one simple name are two types (Draw); the same qualified type hides the base (Mark)
    "src/Sg/Ext/Drawers.cs": """namespace Sg.Ext;

public class Drawer
{
    public void Draw(System.Drawing.Point p)
    {
    }

    public void Mark(System.Drawing.Point p)
    {
    }
}

public class FancyDrawer : Drawer
{
    public void Draw(Windows.Foundation.Point p)
    {
    }

    public void Mark(System.Drawing.Point p)
    {
    }
}

public class DrawUser
{
    private readonly FancyDrawer _d;

    public void DrawIt(System.Drawing.Point p)
    {
        _d.Draw(p);
    }

    public void MarkIt(System.Drawing.Point p)
    {
        _d.Mark(p);
    }
}
""",
    # D4: an unqualified Point under `using Windows.Foundation;` is not proven System.Drawing.Point
    "src/Sg/Ext/Drawers2.cs": """using Windows.Foundation;

namespace Sg.Ext;

public class FancyDrawer2 : Drawer
{
    public void Draw(Point p)
    {
    }
}

public class DrawUser2
{
    private readonly FancyDrawer2 _d;

    public void DrawIt(System.Drawing.Point p)
    {
        _d.Draw(p);
    }
}
""",
    # ... while under `using System.Drawing;` (and through a namespace alias) it is
    "src/Sg/Ext/Drawers3.cs": """using System.Drawing;
using Drw = System.Drawing;

namespace Sg.Ext;

public class PlainDrawer : Drawer
{
    public void Mark(Point p)
    {
    }
}

public class AliasDrawer : Drawer
{
    public void Mark(Drw.Point p)
    {
    }
}

public class DrawUser3
{
    private readonly PlainDrawer _plain;
    private readonly AliasDrawer _alias;

    public void MarkPlain(Point p)
    {
        _plain.Mark(p);
    }

    public void MarkAlias(Drw.Point p)
    {
        _alias.Mark(p);
    }
}
""",
}
# Fix round 2's shapes (section N): a class type parameter's `T?` read by its constraint (an annotation unless
# `struct` / `unmanaged`), one type of several spellings (nint / IntPtr, Nullable<int> / int?, a `global using` alias
# of an external and of a project type, an MSBuild <Using> alias the pass cannot read: unknown), a private middle
# method, an override among the candidates, a generic subclass of other type arguments, two unqualified external
# names by their scopes' usings. Every call is legal C# except NoUsing/Drawers.cs, whose `Point` no using of its
# project supplies (a must-not: nothing proven); N_EXPECT names the binding Roslyn 4.8 gives each, with its run-time
# dispatch (AC1).
RND2 = {
    "src/R2/R2.csproj": CSPROJ,
    "src/R2/GlobalUsings.cs": """global using Tk = System.Threading.CancellationToken;
global using Ord = R2.Slots.Order6;
global using Ptr = System.IntPtr;
""",
    # N1: an unconstrained `T?` closed by a value type is T (C# 9+); a `where T : struct` one is Nullable<T>
    "src/R2/Conv/Converters.cs": """namespace R2.Conv;

public struct Money
{
}

public enum Color
{
    Red,
}

public class Order
{
}

public abstract class Converter<T>
{
    public abstract string Format(T? value);
}

public class IntConverter : Converter<int>
{
    public override string Format(int value)
    {
        return "";
    }
}

public class MoneyConverter : Converter<Money>
{
    public override string Format(Money value)
    {
        return "";
    }
}

public class ColorConverter : Converter<Color>
{
    public override string Format(Color value)
    {
        return "";
    }
}

public class StringConverter : Converter<string>
{
    public override string Format(string? value)
    {
        return "";
    }
}

public class OrderConverter : Converter<Order>
{
    public override string Format(Order? value)
    {
        return "";
    }
}

public class Parser<T> where T : struct
{
    public virtual string Parse(T? value)
    {
        return "";
    }
}

public class IntParser : Parser<int>
{
    public override string Parse(int? value)
    {
        return "";
    }
}

public class Pair<T, U> where T : U
{
    public virtual string Set(T? value)
    {
        return "";
    }
}

public class IntPair : Pair<int, int>
{
    public override string Set(int value)
    {
        return "";
    }
}

public interface IPair<T, U> where T : U
{
    void Set2(T? value);
}

public class PairBase
{
    public void Set2(int? value)
    {
    }
}

public class PairImpl : PairBase, IPair<int, int>
{
    public void Set2(int value)
    {
    }
}

public class ConvUser
{
    private readonly Pair<int, int> _pair;
    private readonly IPair<int, int> _ipair;
    private readonly Converter<int> _i;
    private readonly Converter<Money> _m;
    private readonly Converter<Color> _c;
    private readonly Converter<string> _s;
    private readonly Converter<Order> _o;
    private readonly Parser<int> _p;

    public string A(int v)
    {
        return _i.Format(v);
    }

    public string B(Money v)
    {
        return _m.Format(v);
    }

    public string C(Color v)
    {
        return _c.Format(v);
    }

    public string D(string v)
    {
        return _s.Format(v);
    }

    public string E(Order v)
    {
        return _o.Format(v);
    }

    public string F(int? v)
    {
        return _p.Parse(v);
    }

    public string G(int v)
    {
        return _pair.Set(v);
    }

    public void H(int v)
    {
        _ipair.Set2(v);
    }
}
""",
    # N1 in an interface mapping: IRepoM1<int>.PutM1(T?) is PutM1(int); the base's PutM1(int?) implements nothing
    "src/R2/Maps/Repos.cs": """using System;
using System.Threading;

namespace R2.Maps;

public interface IRepoM1<T>
{
    void PutM1(T? x);
}

public class PBaseM1
{
    public void PutM1(int? x)
    {
    }
}

public class PImplM1 : PBaseM1, IRepoM1<int>
{
    public void PutM1(int x)
    {
    }
}

public class Order
{
}

public interface IStoreM2
{
    void PutM2(Order o, CancellationToken ct);
}

public class SBaseM2
{
    public void PutM2(Order o, CancellationToken ct)
    {
    }
}

public class SImplM2 : SBaseM2, IStoreM2
{
    public new void PutM2(Order o, Tk ct)
    {
    }
}

public interface IPtrM3
{
    void UseM3(nint p);
}

public class PtrBaseM3
{
    public void UseM3(nint p)
    {
    }
}

public class PtrImplM3 : PtrBaseM3, IPtrM3
{
    public new void UseM3(IntPtr p)
    {
    }
}

public class MapUser
{
    private readonly IRepoM1<int> _r;
    private readonly IStoreM2 _s;
    private readonly IPtrM3 _p;

    public void Go1(int v)
    {
        _r.PutM1(v);
    }

    public void Go2(Order o, CancellationToken ct)
    {
        _s.PutM2(o, ct);
    }

    public void Go3(nint p)
    {
        _p.UseM3(p);
    }
}
""",
    # N2: one type spelled two ways (nint / IntPtr, int? / Nullable<int>, a global using alias of an external and of a
    # project type) is one slot; R4: a private same-signature middle method neither breaks nor continues it, a
    # `new virtual` one starts another slot, and so does an `internal new virtual` one of the same project; D4: an
    # overload taking int against `virtual Open7(int?)` and an override `Fill7(int[]?)` of `Fill7(int[])`
    "src/R2/Slots/Slots.cs": """using System;
using System.Threading;

namespace R2.Slots;

public class Order6
{
}

public class A5
{
    public virtual void Run5(nint p)
    {
    }

    public virtual void Open5(int? x)
    {
    }

    public virtual void Save6(Order6 o, CancellationToken ct)
    {
    }

    public virtual void Keep6(Order6 o)
    {
    }

    public virtual void Use6(IntPtr p)
    {
    }
}

public class B5 : A5
{
    public override void Run5(IntPtr p)
    {
    }

    public override void Open5(Nullable<int> x)
    {
    }

    public override void Save6(Order6 o, Tk ct)
    {
    }

    public override void Keep6(Ord o)
    {
    }

    public override void Use6(Ptr p)
    {
    }
}

public class A7
{
    public virtual void Open7(int? x)
    {
    }

    public virtual void Fill7(int[] xs)
    {
    }
}

public class B7 : A7
{
    public void Open7(int x)
    {
    }

    public override void Fill7(int[]? xs)
    {
    }
}

public class C7 : B7
{
    public override void Open7(int? x)
    {
    }
}

public interface IStore6
{
    void Put6(Order6 o, CancellationToken ct);
}

public class StoreBase6 : IStore6
{
    public virtual void Put6(Order6 o, CancellationToken ct)
    {
    }
}

public class FastStore6 : StoreBase6
{
    public override void Put6(Order6 o, Tk ct)
    {
    }
}

public class A2
{
    public virtual void Run2()
    {
    }

    public virtual void Run3()
    {
    }

    public virtual void Run4()
    {
    }
}

public class B2 : A2
{
    private new void Run2()
    {
    }

    public new virtual void Run3()
    {
    }

    internal new virtual void Run4()
    {
    }
}

public class C2 : B2
{
    public override void Run2()
    {
    }

    public override void Run3()
    {
    }

    internal override void Run4()
    {
    }
}

public class SlotUser
{
    private readonly A5 _a;
    private readonly IStore6 _s;
    private readonly A2 _b;
    private readonly A7 _a7;

    public void Go1(nint p)
    {
        _a.Run5(p);
    }

    public void Go2(int? x)
    {
        _a.Open5(x);
    }

    public void Go3(Order6 o, CancellationToken ct)
    {
        _a.Save6(o, ct);
    }

    public void Go4(Order6 o)
    {
        _a.Keep6(o);
    }

    public void Go5(Order6 o, CancellationToken ct)
    {
        _s.Put6(o, ct);
    }

    public void Go6(IntPtr p)
    {
        _a.Use6(p);
    }

    public void Go7()
    {
        _b.Run2();
    }

    public void Go8()
    {
        _b.Run3();
    }

    public void Go9()
    {
        _b.Run4();
    }

    public void Go10()
    {
        _a7.Open7(null);
    }

    public void Go11()
    {
        _a7.Fill7(null);
    }
}
""",
    # R3: C# drops an override from the candidates (its virtual stands in), then every candidate of a less derived
    # class than an applicable one: Mid7.M7(double) beats Der7's `override M7(int)`; Der7's `new M9(int)` is a
    # candidate itself, and Der10's override binds as written (its rival sits above Mid10, the slot's declarer)
    "src/R2/Ovl/Mids.cs": """namespace R2.Ovl;

public class Base7
{
    public virtual void M7(int x)
    {
    }

    public virtual void M9(int x)
    {
    }
}

public class Mid7 : Base7
{
    public void M7(double d)
    {
    }

    public void M9(double d)
    {
    }
}

public class Der7 : Mid7
{
    public override void M7(int x)
    {
    }

    public new void M9(int x)
    {
    }
}

public class Base10
{
    public void M10(double d)
    {
    }
}

public class Mid10 : Base10
{
    public virtual void M10(int x)
    {
    }
}

public class Der10 : Mid10
{
    public override void M10(int x)
    {
    }
}

public class MidUser
{
    private readonly Der7 _d;
    private readonly Der10 _e;

    public void Go()
    {
        _d.M7(5);
    }

    public void Go9()
    {
        _d.M9(5);
    }

    public void Go10()
    {
        _e.M10(5);
    }
}
""",
    # R2: a subclass closing the generic class with other concrete arguments is never the receiver's run-time class
    "src/R2/Gen/Boxes.cs": """namespace R2.Gen;

public class Order
{
}

public class Cart
{
}

public class VB6<T>
{
    public virtual void R6(T x)
    {
    }
}

public class VS6 : VB6<Order>
{
    public override void R6(Order x)
    {
    }
}

public class VO6 : VB6<Cart>
{
    public override void R6(Cart x)
    {
    }
}

public class GenUser
{
    private readonly VB6<Order> _b;

    public void Go(Order o)
    {
        _b.R6(o);
    }
}
""",
    # D1: a type parameter of an OUTER class no base list fixes is unknown (MidI's Put(T) / Put2(List<T>) beside
    # BaseI's Put(object) / Put2(List<object>): the slot unknown, SubI's tuple kept); D2: a member a base interface
    # declares, called through a derived one
    "src/R2/Nest/Outer.cs": """using System.Collections.Generic;

namespace R2.Nest;

public class Outer<T>
{
    public class BaseI
    {
        public virtual void Put(object o)
        {
        }

        public virtual void Put2(List<object> xs)
        {
        }
    }

    public class MidI : BaseI
    {
        public void Put(T x)
        {
        }

        public void Put2(List<T> xs)
        {
        }
    }

    public class SubI : MidI
    {
        public override void Put(object o)
        {
        }

        public override void Put2(List<object> xs)
        {
        }
    }

    public class NestUser
    {
        private readonly BaseI _b;

        public void Go()
        {
            _b.Put(null);
        }

        public void Go2()
        {
            _b.Put2(null);
        }
    }
}

public interface IBase7
{
    void Run7();
}

public interface IDer7 : IBase7
{
}

public class Impl7 : IDer7
{
    public void Run7()
    {
    }
}

public class IfaceUser
{
    private readonly IDer7 _d;

    public void Go()
    {
        _d.Run7();
    }
}
""",
    # D3: a constructed local rebound through parentheses, a parenthesized ref argument, a ref alias of a parenthesized
    "src/R2/Locals/Locals.cs": """namespace R2.Locals;

public class Repo
{
    public virtual void Keep()
    {
    }
}

public class CachedRepo : Repo
{
    public override void Keep()
    {
    }
}

public class LocalUser
{
    private static void Swap(ref Repo r)
    {
    }

    public void Paren()
    {
        Repo b = new Repo();
        (b) = new CachedRepo();
        b.Keep();
    }

    public void RefParen()
    {
        Repo b = new Repo();
        Swap(ref (b));
        b.Keep();
    }

    public void RefAlias()
    {
        Repo b = new Repo();
        ref Repo r = ref (b);
        r = new CachedRepo();
        b.Keep();
    }

    public void Fresh()
    {
        Repo b = new Repo();
        b.Keep();
    }
}
""",
    # D4: two project classes of one simple name are two types
    "src/R2/Pa/Order.cs": """namespace Pa;

public class Order
{
}
""",
    "src/R2/Pb/Shop.cs": """namespace Pb;

public class Order
{
}

public class SBase
{
    public void Save(Pa.Order o)
    {
    }

    public void Keep(Order o)
    {
    }
}

public class SDer : SBase
{
    public void Save(Order o)
    {
    }

    public void Keep(Pb.Order o)
    {
    }
}

public class SUser
{
    private readonly SDer _d;

    public void Go(Pa.Order o)
    {
        _d.Save(o);
    }

    public void Go2(Order o)
    {
        _d.Keep(o);
    }
}
""",
    # R1: two unqualified spellings of one simple name are one type only when their usings resolve it alike
    "src/R2/Ext/TimerBase.cs": """using System.Text;
using System.Threading;

namespace R2.Ext;

public class TBaseE1
{
    public void ArmE1(Timer t)
    {
    }

    public void ArmE7(Timer t)
    {
    }
}
""",
    "src/R2/Ext/TimerDer.cs": """using System.Text;
using System.Timers;

namespace R2.Ext;

public class TDerE1 : TBaseE1
{
    public void ArmE1(Timer t)
    {
    }
}

public class TUserE1
{
    private readonly TDerE1 _d;

    public void Go(System.Threading.Timer t)
    {
        _d.ArmE1(t);
    }
}
""",
    "src/R2/Ext/TimerSub.cs": """using System.Text;
using System.Threading;

namespace R2.Ext.Sub;

public class TDerE7 : TBaseE1
{
    public void ArmE7(Timer t)
    {
    }
}

public class TUserE7
{
    private readonly TDerE7 _d;

    public void Go(Timer t)
    {
        _d.ArmE7(t);
    }
}
""",
    "src/R2/Ext/TokBase.cs": """using System.Threading;
using R2.Gen;

namespace R2.Tok;

public class TokBase
{
    public virtual void Run(CancellationToken ct)
    {
    }

    public void Hide(CancellationToken ct)
    {
    }
}
""",
    "src/R2/Ext/TokDer.cs": """using System.Threading;
using R2.Ovl;

namespace R2.Tok;

public class TokDer : TokBase
{
    public override void Run(CancellationToken ct)
    {
    }

    public void Hide(CancellationToken ct)
    {
    }
}

public class TokUser
{
    private readonly TokBase _b;
    private readonly TokDer _d;

    public void Go(CancellationToken ct)
    {
        _b.Run(ct);
    }

    public void Go2(CancellationToken ct)
    {
        _d.Hide(ct);
    }
}
""",
    # D4: a System name under another qualifier (UnityEngine.Object) is no keyword; System.Object is object
    "src/R2/Ext/Unity.cs": """namespace R2.Uni;

public class UseBase
{
    public void Use(object o)
    {
    }
}

public class UseDer : UseBase
{
    public void Use(UnityEngine.Object o)
    {
    }
}

public class UseDer2 : UseBase
{
    public void Use(System.Object o)
    {
    }
}

public class UseUser
{
    private readonly UseDer _d;
    private readonly UseDer2 _d2;

    public void Go(object o)
    {
        _d.Use(o);
    }

    public void Go2(object o)
    {
        _d2.Use(o);
    }
}
""",
    "src/R2/Ext/Blocks.cs": """namespace R2.E2A
{
    using System.Threading;

    public class TBaseE2
    {
        public void ArmE2(Timer t)
        {
        }

        public void ArmE3(System.Threading.Timer t)
        {
        }
    }
}

namespace R2.E2B
{
    using System.Timers;

    public class TDerE2 : R2.E2A.TBaseE2
    {
        public void ArmE2(Timer t)
        {
        }

        public void ArmE3(Timer t)
        {
        }
    }

    public class TUserE2
    {
        private readonly TDerE2 _d;

        public void Go(System.Threading.Timer t)
        {
            _d.ArmE2(t);
        }

        public void Go3(System.Threading.Timer t)
        {
            _d.ArmE3(t);
        }
    }
}

namespace R2.E2C
{
    using System.Threading;

    public class TDerE4 : R2.E2A.TBaseE2
    {
        public void ArmE2(Timer t)
        {
        }

        public void ArmE3(Timer t)
        {
        }
    }

    public class TUserE4
    {
        private readonly TDerE4 _d;

        public void Go(Timer t)
        {
            _d.ArmE2(t);
        }

        public void Go3(Timer t)
        {
            _d.ArmE3(t);
        }
    }
}
""",
    "src/R2/Ext/Blocks2.cs": """using System.Threading;

namespace R2.E5
{
    using System.Timers;

    public class TDerE5 : R2.E2A.TBaseE2
    {
        public void ArmE3(Timer t)
        {
        }
    }

    public class TUserE5
    {
        private readonly TDerE5 _d;

        public void Go(System.Threading.Timer t)
        {
            _d.ArmE3(t);
        }
    }
}
""",
    # N2: aliases an MSBuild <Using> item declares (the pass does not read them): unknown, never "two types" (a slot,
    # a `new virtual` in the middle, an interface implementer, an interface member's second overload)
    "src/Sg3/Sg3.csproj": """<Project Sdk="Microsoft.NET.Sdk">
  <ItemGroup>
    <Using Include="System.Threading.CancellationToken" Alias="Tk2" />
    <Using Include="System.Int32" Alias="I32" />
  </ItemGroup>
</Project>
""",
    "src/Sg3/Aliases.cs": """using System.Threading;

namespace Sg3;

public class A3
{
    public virtual void Run3(CancellationToken ct)
    {
    }

    public virtual void Num3(int x)
    {
    }
}

public class B3 : A3
{
    public override void Run3(Tk2 ct)
    {
    }

    public override void Num3(I32 x)
    {
    }
}

public class A4
{
    public virtual void Run4(CancellationToken ct)
    {
    }
}

public class B4 : A4
{
    public new virtual void Run4(Tk2 ct)
    {
    }
}

public class C4 : B4
{
    public override void Run4(CancellationToken ct)
    {
    }
}

public interface IRun5
{
    void Run5(CancellationToken ct);
}

public class P5
{
    public void Run5(CancellationToken ct)
    {
    }
}

public class Q5 : P5, IRun5
{
    public new void Run5(Tk2 ct)
    {
    }
}

public interface IOv9
{
    void Put9(int x, int y);

    void Put9(Tk2 ct);
}

public class Impl9 : IOv9
{
    public void Put9(int x, int y)
    {
    }

    public void Put9(CancellationToken ct)
    {
    }
}

public class AliasUser
{
    private readonly A3 _a;
    private readonly A4 _a4;
    private readonly IRun5 _r5;
    private readonly IOv9 _o9;

    public void Go(CancellationToken ct)
    {
        _a.Run3(ct);
    }

    public void Go2()
    {
        _a.Num3(1);
    }

    public void Go4(CancellationToken ct)
    {
        _a4.Run4(ct);
    }

    public void Go5(CancellationToken ct)
    {
        _r5.Run5(ct);
    }

    public void Go9()
    {
        _o9.Put9(1, 2);
    }
}
""",
    # p_names2: a `global using` of the project (another .csproj's is never seen), an enclosing namespace's member
    "src/Sg2/Sg2.csproj": CSPROJ,
    "src/Sg2/Glob.cs": """global using System.Drawing;
""",
    "src/Sg2/Drawers.cs": """namespace Sg2;

public class Drawer
{
    public void Draw(System.Drawing.Point p)
    {
    }
}

public class GDrawer : Drawer
{
    public void Draw(Point p)
    {
    }
}

public class DrawUser
{
    private readonly GDrawer _g;

    public void DrawIt(System.Drawing.Point p)
    {
        _g.Draw(p);
    }
}
""",
    "src/R2/Gfx/Gfx.cs": """namespace System.Drawing.Gfx;

public class Drawer2
{
    public void Draw(System.Drawing.Size s)
    {
    }
}

public class GDrawer2 : Drawer2
{
    public void Draw(Size s)
    {
    }
}

public class DrawUser2
{
    private readonly GDrawer2 _g;

    public void DrawIt(System.Drawing.Size s)
    {
        _g.Draw(s);
    }
}
""",
    "src/R2/NoUsing/Drawers.cs": """namespace R2.NoUsing;

public class Drawer3
{
    public void Draw(System.Drawing.Point p)
    {
    }
}

public class GDrawer3 : Drawer3
{
    public void Draw(Point p)
    {
    }
}

public class DrawUser3
{
    private readonly GDrawer3 _g;

    public void DrawIt(System.Drawing.Point p)
    {
        _g.Draw(p);
    }
}
""",
}
TREES = {"cqrs": CQRS, "review": REVIEW, "recheck": RECHECK, "sigs": SIGS, "rnd2": RND2}


# ================================================================================================
# Materialized trees, captured graphs, the pipeline
# ================================================================================================

_TMP: list = []


def tmp() -> Path:
    if not _TMP:
        _TMP.append(Path(tempfile.mkdtemp(prefix="dm_tccs_")))
    return _TMP[0]


def cleanup() -> None:
    while _TMP:
        shutil.rmtree(_TMP.pop(), ignore_errors=True)


def tree_sha(name: str) -> str:
    return hashlib.sha256(json.dumps(TREES[name], sort_keys=True).encode("utf-8")).hexdigest()


def write_tree(root: Path, files: dict) -> Path:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode("utf-8"))
    return root


_ROOTS: dict = {}


def root_for(name: str) -> Path:
    """TREES[name] materialized once under the gate's temp dir (git-free: no .gitignore rules apply)."""
    if name not in _ROOTS:
        _ROOTS[name] = write_tree(tmp() / "src" / name, TREES[name])
    return _ROOTS[name]


def fixture_path(bucket: str, name: str) -> Path:
    return FX / bucket / "cs" / f"{name}.json"


def load_json(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def captured(bucket: str, name: str):
    p = fixture_path(bucket, name)
    return load_json(p) if p.is_file() else None


class Run:
    """One pipeline run: nodes, the CALLS list handed to link (`calls_in`, the object), a copy of it taken before
    link (`before`), link's output (`out`) and its stats."""
    __slots__ = ("nodes", "calls_in", "before", "out", "stats")

    def __init__(self, nodes, calls_in, before, out, stats):
        self.nodes, self.calls_in, self.before, self.out, self.stats = nodes, calls_in, before, out, stats


def run_pipeline(graph, root: Path, env: dict, *, extra_nodes=None, prelink=None) -> Run:
    """The push-time passes up to and including typed_calls.link, exactly as sync runs them: map_graph -> test /
    vendor exclusion -> enrich sidecar -> recognizers -> _unkey_receiver_calls -> link. `extra_nodes(nodes)` returns
    nodes to append before link (section E's synthetic C5 members); `prelink(nodes)` may edit the nodes in place
    (section H)."""
    with clean_env(**env):
        nodes, calls, imports = GX.map_graph(copy.deepcopy(graph), root=root)
        nodes, calls, imports = S._maybe_exclude_vendor(root, *S._maybe_exclude_tests(nodes, calls, imports))
        S.enrich.apply_sidecar(root, nodes)
        nodes, _typed = S._apply_recognizers(root, nodes)
        calls, _n = S._unkey_receiver_calls(root, nodes, calls)
        if extra_nodes is not None:
            nodes = list(nodes) + list(extra_nodes(nodes))
        if prelink is not None:
            prelink(nodes)
        before = list(calls)
        flags = {k: v for k, v in os.environ.items() if k.startswith("DEEPMAP_")}
        out, stats = tc().link(str(root), nodes, calls, is_test=S._is_test_path, env=flags)
    return Run(nodes, calls, before, out, stats)


def stable(nodes) -> dict:
    """node key -> its version-stable identity `label:name@file:line`."""
    return {n["key"]: f"{n.get('label')}:{n.get('name')}@{n.get('source_file')}:{n.get('start_line')}"
            for n in nodes}


def ident(n) -> str:
    return f"{n.get('source_file')}::{n.get('name')}"


def typed_view(nodes, *, form="stable") -> dict:
    """{caller: {target: grade}} for every caller the pass keyed, by stable id or by `file::name` (form="name")."""
    ids = {n["key"]: ident(n) for n in nodes} if form == "name" else stable(nodes)
    out = {}
    for n in nodes:
        if not n.get("call_typed"):
            continue
        c = ids.get(n["key"], n["key"])
        tg = {}
        for t, g in sorted(n["call_typed"].items()):
            tn = ids.get(t, t)
            tg[tn if tn not in tg else tn + "#collision"] = g
        out[c if c not in out else c + "#collision"] = tg
    return out


def key_of(nodes, ref: str):
    """The key of the (non-Module) node `file::name` (None when absent or not unique)."""
    hits = [n["key"] for n in nodes if ident(n) == ref and n.get("label") != "Module"]
    return hits[0] if len(hits) == 1 else None


def node_of(nodes, ref: str):
    k = key_of(nodes, ref)
    return next((n for n in nodes if n["key"] == k), None) if k is not None else None


def tuples_of(run: Run, calls, caller_ref: str, member: str) -> list:
    """The (callee, is-key) tuples of caller `file::name` in `calls` whose callee denotes `member`."""
    ck = key_of(run.nodes, caller_ref)
    name_of = {n["key"]: n.get("name") for n in run.nodes}
    return [(str(c[1]), GX.is_call_key(c[1])) for c in calls
            if c[0] == ck and TC.callee_matches(c[1], member, name_of, TC._is_call_key)]


# every flag the variants touch is PINNED (never "unset = off"), so a later default flip cannot move these
# expectations: TYPED on for C#, the audit, PRECISE and the family filter off, graphify_extract's C# members off
_OFF = {"DEEPMAP_TYPED_CALLS": "0", "DEEPMAP_CALLS_AUDIT": "0", "DEEPMAP_PRECISE_CALLS": "0",
        "DEEPMAP_CALLS_FAMILY_FILTER": "0", "DEEPMAP_CSHARP_MEMBERS": "0"}
MODES = {"default": dict(_OFF, DEEPMAP_TYPED_CALLS="csharp"),
         "precise": dict(_OFF, DEEPMAP_TYPED_CALLS="csharp", DEEPMAP_PRECISE_CALLS="1")}


def buckets_present() -> list:
    return [b for b in sorted(BUCKETS.values()) if all(fixture_path(b, t).is_file() for t in TREES)]


_RUNS: dict = {}


def run_for(bucket: str, tree: str, mode: str) -> Run:
    """The pipeline on the captured graph of (bucket, tree) under MODES[mode] (memoised; a mutation run clears it)."""
    k = (bucket, tree, mode)
    if k not in _RUNS:
        _RUNS[k] = run_pipeline(captured(bucket, tree)["graph"], root_for(tree), MODES[mode])
    return _RUNS[k]


def _line_of(text: str, needle: str, nth: int = 0) -> int:
    hits = [i for i, ln in enumerate(text.split("\n"), 1) if needle in ln]
    return hits[nth]


class Src:
    """An in-memory graphify_extract._Source (raw / lines; no trees: the reader parses raw bytes itself)."""

    def __init__(self, files: dict):
        self.files = files

    def raw(self, rel):
        t = self.files.get(rel)
        return None if t is None else t.encode("utf-8")

    def lines(self, rel):
        t = self.files.get(rel)
        return None if t is None else GX._engine_lines(t.encode("utf-8"))

    def tree(self, rel):
        return None

    def ts_tree(self, rel):
        return None


# ================================================================================================
# A: the field report, reproduced on the REAL captured collision graph
# ================================================================================================

def _collision_src() -> dict:
    """tests/typed_edge_resolution_gates.COLLISION_SRC read as a literal (never imported: no side effects)."""
    tree = ast.parse((REPO / "tests" / "typed_edge_resolution_gates.py").read_text(encoding="utf-8"))
    for st in tree.body:
        if isinstance(st, ast.Assign) and any(getattr(t, "id", "") == "COLLISION_SRC" for t in st.targets):
            return ast.literal_eval(st.value)
    return {}


_CTL = "cs/Controllers/InvoicesController.cs"
_SVC = "cs/Services/InvoiceService.cs"


def sec_collision() -> None:
    print("\n-- A: the field report, reproduced (csharp_java_collision.json, a REAL graphify 0.8.39 graph) --")
    graph = load_json(REPO / "tests" / "fixtures" / "graphify_multilang" / "csharp_java_collision.json")
    src = _collision_src()
    check("A the collision fixture has 0 `calls` links although its source calls `_service.Find(id)` (the gap)",
          not [lk for lk in graph.get("links", []) if lk.get("relation") == "calls"]
          and "return _service.Find(id);" in src.get(_CTL, ""))
    root = write_tree(tmp() / "collision", src)
    for mode in MODES:
        run = run_pipeline(graph, root, MODES[mode])
        got = typed_view(run.nodes, form="name")
        want = {f"{_CTL}::InvoicesController.GetInvoice": {f"{_SVC}::IInvoiceService.Find": "INFERRED",
                                                           f"{_SVC}::InvoiceService.Find": "INFERRED"}}
        check(f"A [{mode}] InvoicesController.GetInvoice -> {{IInvoiceService.Find: INFERRED, InvoiceService.Find: "
              f"INFERRED}}, and nothing from `_db.Invoices.Find(id)` (an external DbSet)", got == want,
              (got, run.stats.get("langs_disabled"), run.stats.get("error")))
        check(f"A [{mode}] csharp is not disabled and no C# file failed",
              "csharp" not in run.stats["langs_disabled"] and run.stats["files_failed"] == 0
              and run.stats["error"] is None, (run.stats["langs_disabled"], run.stats["files_failed"]))
        ck = key_of(run.nodes, f"{_CTL}::InvoicesController.GetInvoice")
        keys = [str(t) for c, t in run.out if c == ck and GX.is_call_key(t)]
        check(f"A [{mode}] both targets are CallKeys appended to the CALLS list", len(keys) == 2, keys)


# ================================================================================================
# B: the reader's IR on snippets
# ================================================================================================

def facts_of(src: str, rel: str = "src/A.cs"):
    rd = rcs().make_reader(TC.LANGUAGES["csharp"])
    return rd.facts(rel, Src({rel: src}))


def sc_of(f, name, nth=0):
    hits = [s for s in f.scopes if s.name == name]
    return hits[nth] if len(hits) > nth else None


def cls_of(f, name):
    return next((c for c in f.classes if c.name == name), None)


B_SNIPPET = """using System;
using static Shop.Pricing.PriceMath;
global using Shop.Common;
global using static Shop.Common.Guard;
using Alias = Shop.Billing.Invoice;

namespace Shop.Orders;

[Attr]
public partial class Handler(IRepo repo, int x) : Base<int>, IHandler
{
    private readonly ICart _cart, _other;
    public decimal Total => Items.Sum(i => i.Price);
    public int Count { get { return Compute(); } set { _x = value; } }
    public IFoo Svc { get; set; } = new Foo();

    [Inject]
    public Handler(
        ICart cart,
        IRepo r) : base(r)
    {
        _cart = cart;
    }

    [HttpGet]
    [Route("x")]
    public async Task<int> Handle<T>(Cmd c, CancellationToken ct = default) where T : class
    {
        var o = new Order();
        Order p = new();
        IFoo q = Get<Order>(1);
        _cart?.Save(c);
        this.Run();
        base.Run();
        Helper.Build(x);
        var w = await _cart.SaveAsync();
        ((IFoo)q).M();
        (q as IFoo).M();
        q!.Bang();
        int Local(int z) => z + Gen<int>();
        Action a = delegate (int k) { Do(k); };
        foreach (var it in o.Items) { it.Go(); }
        repo.Load();
        return 1;
    }

    public void Run() { }
    public void Run(int a) { }
    void IHandler.Explicit() { }
    public static int Ext(this Order o, int y) => 0;
}

public interface IH
{
    void M();
    void D() { M(); }
}

public abstract class AB
{
    public abstract void Must();
}

public record Rec(int Id, string Name);
"""


def sec_reader() -> None:
    print("\n-- B: the reader's IR on snippets --")
    if rcs() is None:
        check("B receiver_facts_csharp imports", False, _IMPORT_ERRORS)
        return
    R, T = rcs(), TC
    rd = R.make_reader(T.LANGUAGES["csharp"])
    check("B the canary passes with the grammar installed", rd.canary() is True)
    check("B the reader's version is a str", isinstance(getattr(rd, "version", None), str))
    f = facts_of(B_SNIPPET)
    check("B validate_facts accepts the snippet's facts", f is not None and not T.validate_facts(f),
          f and T.validate_facts(f))
    if f is None:
        return
    L = lambda needle, nth=0: _line_of(B_SNIPPET, needle, nth)    # noqa: E731
    h = sc_of(f, "Handle")
    check("B a method scope: kind method, line = its FIRST attribute line, lines hold the name line",
          h is not None and h.kind == "method" and h.line == L("[HttpGet]") and L("public async Task<int>") in h.lines,
          h and (h.kind, h.line, h.lines))
    ct = sc_of(f, "Handler")
    check("B a constructor scope: kind ctor, named after its class, line = its attribute line, the multi-line "
          "parameter list read", ct is not None and ct.kind == "ctor" and ct.line == L("[Inject]")
          and set(ct.params) == {"cart", "r"} and ct.params["cart"].type == T.t_name("ICart"),
          ct and (ct.kind, ct.line, sorted(ct.params)))
    tot, cnt = sc_of(f, "Total"), sc_of(f, "Count")
    check("B computed properties: ONE accessor scope each (expression body; get / set bodies), at the declaration "
          "line; an auto-property has none", tot is not None and tot.kind == "accessor" and tot.line == L("Total =>")
          and cnt is not None and cnt.kind == "accessor" and sc_of(f, "Svc") is None and sc_of(f, "Count", 1) is None,
          [(s.name, s.kind, s.line) for s in f.scopes])
    loc, lam = sc_of(f, "Local"), [s for s in f.scopes if s.kind == "lambda"]
    anon = [s for s in f.scopes if s.kind == "anonymous_method"]
    hi = f.scopes.index(h) if h is not None else -2
    check("B a local function / lambda / anonymous method are nested scopes of their kinds, parented to Handle",
          loc is not None and loc.kind == "local_function" and loc.parent == hi and anon and anon[0].parent == hi
          and any(s.parent == f.scopes.index(tot) for s in lam if tot is not None),
          [(s.name, s.kind, s.parent) for s in f.scopes])
    check("B a local function's name is poisoned in its enclosing scope (a bare call of it never types)",
          h is not None and "Local" in h.poisoned, h and sorted(h.poisoned))
    runs = [s for s in f.scopes if s.name == "Run"]
    check("B overloads: every overload scope's lines hold the FIRST overload's line (the engine's one node)",
          len(runs) == 2 and all(L("public void Run() { }") in (s.line,) + tuple(s.lines) for s in runs),
          [(s.line, s.lines) for s in runs])
    k = cls_of(f, "Handler")
    check("B a partial class is open; its bases are every base-list entry (the generic head kept as a generic)",
          k is not None and k.open and len(k.bases) == 2 and k.bases[1] == T.t_name("IHandler")
          and k.bases[0].kind == "generic", k and (k.open, k.bases))
    check("B fields: a field declaration's every declarator, properties and primary-constructor parameters, each "
          "with its declared type", k is not None and {"_cart", "_other", "Total", "Count", "Svc", "repo", "x"}
          <= set(k.fields) and [b.type for b in k.fields["_other"]] == [T.t_name("ICart")]
          and [b.type for b in k.fields["repo"]] == [T.t_name("IRepo")], k and sorted(k.fields))
    check("B methods: overloads under one name (two MethodFacts), an explicit interface implementation under its "
          "member name, an extension method static",
          k is not None and len(k.methods.get("Run", ())) == 2 and "Explicit" in k.methods
          and "static" in k.methods["Ext"][0].flags, k and sorted(k.methods))
    check("B an extension method is a patch (this-type, name)", (T.t_name("Order"), "Ext") in f.patches, f.patches)
    sites = {(s.member, s.recv) for s in h.sites} if h is not None else set()
    want_sites = [("Save", T.r_field(T.R_SELF, "_cart")), ("Run", T.R_SELF), ("Run", T.R_SUPER),
                  ("Build", T.r_ident("Helper")), ("SaveAsync", T.r_field(T.R_SELF, "_cart")), ("M", T.R_OTHER),
                  ("Bang", T.r_ident("q")), ("Load", T.r_field(T.R_SELF, "repo")), ("Get", None)]
    check("B receivers: `x?.M` -> x, a field -> FIELD(SELF, x), a primary-ctor parameter -> FIELD(SELF, x), "
          "`this` -> SELF, `base` -> SUPER, a cast / `as` -> OTHER, `x!` -> x, `M<T>()` -> bare M",
          all(w in sites for w in want_sites), sorted((m, str(r)) for m, r in sites))
    ctor_sites = [(s.member, s.recv) for s in h.sites if s.kind == "ctor"] if h is not None else []
    check("B `new Order()` and a target-typed `Order p = new()` are ctor sites of Order",
          ctor_sites.count(("Order", T.r_classref(T.t_name("Order")))) == 2, ctor_sites)
    lc = h.locals if h is not None else {}
    check("B locals: `var o = new T()` is ctor, `T p = new()` ctor (a construction of exactly its declared type), "
          "`IFoo q = Get()` ann, `var w = await ...` unknown (an await result is never typed)",
          [b.kind for b in lc.get("o", ())] == ["ctor"] and [b.kind for b in lc.get("p", ())] == ["ctor"]
          and [(b.kind, b.type) for b in lc.get("q", ())] == [("ann", T.t_name("IFoo"))]
          and [b.kind for b in lc.get("w", ())] == ["unknown"], {k_: [b.kind for b in v] for k_, v in lc.items()})
    check("B a foreach variable is poisoned", h is not None and "it" in h.poisoned, h and sorted(h.poisoned))
    check("B the census counts every call over the full span (nested scopes too)",
          h is not None and h.all_names is not None and h.all_names.get("Gen") == 1 and h.all_names.get("Do") == 1
          and h.all_names.get("M") == 2, h and h.all_names)
    ih, ab = cls_of(f, "IH"), cls_of(f, "AB")
    check("B an interface is kind interface; a bodiless member is abstract, a default member is not",
          ih is not None and ih.kind == "interface" and "abstract" in ih.methods["M"][0].flags
          and "abstract" not in ih.methods["D"][0].flags, ih and (ih.kind, ih.methods))
    check("B an abstract class is kind abstract and its abstract member is flagged",
          ab is not None and ab.kind == "abstract" and "abstract" in ab.methods["Must"][0].flags, ab and ab.kind)
    rec = cls_of(f, "Rec")
    check("B a positional record's parameters are fields", rec is not None and {"Id", "Name"} <= set(rec.fields),
          rec and sorted(rec.fields))
    cs = getattr(f, "_cs", None) or {}
    check("B the private _cs table: the namespace, plain / static / global usings",
          cs.get("namespaces") == ["Shop.Orders"] and "System" in [u[0] for u in cs.get("usings", ())]
          and "Shop.Pricing.PriceMath" in [u[0] for u in cs.get("statics", ())]
          and ("Shop.Common", True) in [(u[0], u[1]) for u in cs.get("usings", ())]
          and ("Shop.Common.Guard", True) in [(u[0], u[1]) for u in cs.get("statics", ())], cs)
    check("B a using alias is a named import", [(i.kind, i.spec) for i in f.imports.get("Alias", ())]
          == [("named", "Shop.Billing.Invoice")], f.imports)
    decl_lines = {d[0] for d in f.decls}
    check("B decls: the method, constructor and local function names (the textual veto skips them)",
          {L("public async Task<int> Handle"), L("    public Handler("), L("int Local(")} <= decl_lines,
          sorted(decl_lines))
    # #if and parse errors
    pre = ("namespace N;\npublic class K\n{\n    private readonly S _s;\n    public void Run()\n    {\n#if DEBUG\n"
           "        _s.A(1);\n#else\n        _s.B(1);\n#endif\n        System.Action a = () => _s.A(3);\n    }\n"
           "    public void Plain()\n    {\n        _s.A(2);\n    }\n}\n")
    g = facts_of(pre)
    run_, plain = sc_of(g, "Run"), sc_of(g, "Plain")
    inner = [s for s in g.scopes if s.kind == "lambda"]
    check("B a scope holding an #if region keeps no site and its census is above its sites (no REPLACE there); a "
          "scope nested in it keeps none either",
          run_ is not None and not run_.sites and run_.all_names and run_.all_names.get("A", 0) >= 3
          and inner and not inner[0].sites and plain is not None and [s.member for s in plain.sites] == ["A"],
          run_ and (run_.sites, run_.all_names, [s.sites for s in inner]))
    bad = "namespace N;\npublic class K\n{\n    public void Run()\n    {\n        _s.A(1;\n    }\n    public void Ok() { _s.A(2); }\n}\n"
    g2 = facts_of(bad)
    r2 = sc_of(g2, "Run") if g2 is not None else None
    check("B a scope overlapping a parse error keeps no site; the file still gives facts",
          g2 is not None and not T.validate_facts(g2) and r2 is not None and not r2.sites,
          g2 and r2 and r2.sites)
    bom = "﻿namespace N;\r\npublic class K\r\n{\r\n    private readonly S _s;\r\n    public void Run()\r\n    {\r\n        _s.A(1);\r\n    }\r\n}\r\n"
    g3 = facts_of(bom)
    r3 = sc_of(g3, "Run") if g3 is not None else None
    check("B a BOM + CRLF file: lines numbered like the engine, a site's column (its invocation's start) in UTF-8 "
          "bytes of the BOM-less text",
          r3 is not None and r3.line == 5 and [(s.member, s.line, s.col) for s in r3.sites] == [("A", 7, 8)],
          r3 and [(s.member, s.line, s.col) for s in r3.sites])
    allfiles = [rel for rel in CQRS if rel.endswith(".cs")]
    badf = []
    for rel in allfiles:
        ff = rd.facts(rel, Src(CQRS))
        if ff is None or T.validate_facts(ff):
            badf.append((rel, ff and T.validate_facts(ff)))
    check(f"B every .cs file of the fixture tree gives facts that validate ({len(allfiles)})", not badf, badf)


# ================================================================================================
# C / D: the fixture trees end to end, both engines' captures, default and PRECISE
# ================================================================================================

def _m(f, n):
    return f"{f}::{n}"


CART, ISCH, SCH = "src/Shop/Carts/Cart.cs", "src/Shop/Carts/IShoppingCartHelper.cs", "src/Shop/Carts/ShoppingCartHelper.cs"
ORDER, OLINE, OSVC = "src/Shop/Orders/Order.cs", "src/Shop/Orders/OrderLine.cs", "src/Shop/Orders/OrderService.cs"
IREPO, COH = "src/Shop/Orders/IOrderRepository.cs", "src/Shop/Orders/CreateOrderHandler.cs"
COHP, CAN = "src/Shop/Orders/CreateOrderHandler.Persist.cs", "src/Shop/Orders/CancelOrderHandler.cs"
BASE, SHIP, TOT = "src/Shop/Orders/OrderHandlerBase.cs", "src/Shop/Orders/ShipOrderHandler.cs", "src/Shop/Members/CartTotals.cs"
PM, PH = "src/Shop/Pricing/PriceMath.cs", "src/Shop/Pricing/PricingHandler.cs"
NOT, STEP = "src/Shop/Notify/INotifier.cs", "src/Shop/Steps/IStep.cs"
USR, CTL = "src/Shop/Web/User.cs", "src/Shop/Web/OrdersController.cs"
COL, CANV = "src/Shop/Paint/Color.cs", "src/Shop/Paint/Canvas.cs"
INVB, JOB, REP = "src/Shop/Billing/Invoice.cs", "src/Shop/Billing/InvoiceJob.cs", "src/Shop/Reports/ReportJob.cs"
ALERT, DISP, TOG, WIN = ("src/Shop/Misc/AlertSender.cs", "src/Shop/Misc/Dispatcher.cs", "src/Shop/Misc/Toggle.cs",
                         "src/Shop/Misc/Windowsy.cs")
AORD, AJOB, FAKE = "src/Admin/Models/Order.cs", "src/Admin/AdminJob.cs", "tests/Shop.Tests/FakeCartHelper.cs"
I, A, E = "INFERRED", "AMBIGUOUS", "EXTRACTED"


def expect(bucket: str) -> dict:
    """{caller: {target: grade}} (file::name) for the cqrs tree: EXACTLY these callers are keyed, with these
    targets (graphify_extract's C# members off). 0.8.39 gives the record OrderLine no owner: its method is the
    :Function `Check` there (the records allowlist of F)."""
    rec_caller = _m(OLINE, "Check") if bucket == "0839" else _m(OLINE, "OrderLine.Check")
    return {
        _m(SCH, "ShoppingCartHelper.CreateShoppingCartAsync"): {_m(CART, "Cart"): I, _m(CART, "Cart.Recalculate"): I},
        rec_caller: {_m(ORDER, "Order.Validate"): I},
        # the field report's handler: an interface field assigned in a multi-line constructor (the interface member
        # and its sole PRODUCTION implementer, the test fake unseen), a static helper, `this.` into the partial part
        # in another file, a generic interface member; the `await` result `cart` stays untyped (D)
        _m(COH, "CreateOrderHandler.Handle"): {
            _m(ISCH, "IShoppingCartHelper.CreateShoppingCartAsync"): I,
            _m(SCH, "ShoppingCartHelper.CreateShoppingCartAsync"): I,
            _m(SCH, "ShoppingCartHelper.BuildCartItems"): I,
            _m(ISCH, "IShoppingCartHelper.SaveCartItemsAsync"): I,
            _m(SCH, "ShoppingCartHelper.SaveCartItemsAsync"): I,
            _m(COHP, "CreateOrderHandler.Persist"): I,
            _m(IREPO, "IOrderRepository.Get"): I, _m(IREPO, "OrderRepository.Get"): I},
        # the partial part uses a field the OTHER part declares
        _m(COHP, "CreateOrderHandler.Persist"): {_m(IREPO, "IOrderRepository.Add"): I,
                                                 _m(IREPO, "OrderRepository.Add"): I},
        # a primary constructor's parameters
        _m(CAN, "CancelOrderHandler.Handle"): {
            _m(ISCH, "IShoppingCartHelper.SaveCartItemsAsync"): I, _m(SCH, "ShoppingCartHelper.SaveCartItemsAsync"): I,
            _m(OSVC, "OrderService.Cancel"): I, _m(SCH, "ShoppingCartHelper.BuildCartItems"): I},
        _m(SHIP, "ShipOrderHandler.Audit"): {_m(BASE, "OrderHandlerBase.Audit"): I},
        # a parameter, a bare implicit-this call, `var` / `T x = new()` / `T x = y` locals, `?.`, a bare generic;
        # Audit is an `override` (virtual) called through an implicit `this`: virtual dispatch, so INFERRED, never
        # EXTRACTED (no subclass of ShipOrderHandler overrides it); the private Gen is exact
        _m(SHIP, "ShipOrderHandler.Handle"): {
            _m(OSVC, "OrderService.Ship"): I, _m(SHIP, "ShipOrderHandler.Audit"): I, _m(ORDER, "Order"): I,
            _m(ORDER, "Order.Validate"): I, _m(SHIP, "ShipOrderHandler.Gen"): E},
        # using static; an extension method; an instance method beats a same-named extension
        _m(PH, "PricingHandler.Handle"): {_m(PM, "PriceMath.RoundPrice"): I, _m(PM, "PriceMath.Discounted"): I,
                                          _m(ORDER, "Order.Validate"): I},
        # interface dispatch: two implementers AMBIGUOUS each, five: only the interface member
        _m(NOT, "NotifyHandler.Handle"): {_m(NOT, "INotifier.Notify"): I, _m(NOT, "EmailNotifier.Notify"): A,
                                          _m(NOT, "SmsNotifier.Notify"): A},
        _m(STEP, "StepRunner.Run"): {_m(STEP, "IStep.Execute"): I},
        _m(CTL, "PlainController.Get"): {_m(USR, "User.Find"): I},
        _m(COL, "Color.FromName"): {_m(COL, "Color"): E},
        # Color Color: the property and the type agree; a property of ANOTHER type named Color wins over the type
        _m(CANV, "Canvas.Apply"): {_m(COL, "Color.Hex"): I, _m(COL, "Color.FromName"): I},
        _m(CANV, "Badge.Apply"): {_m(COL, "Status.Hex"): I},
        # the namespace chain beats a using; the caller's own .csproj wins a duplicated full name
        _m(JOB, "InvoiceJob.Run"): {_m(INVB, "Invoice.Send"): I},
        _m(AJOB, "AdminJob.Run"): {_m(AORD, "Order.Validate"): I},
        _m(ALERT, "AlertSender.Alert"): {_m(ALERT, "Mailer.Send"): E},
        _m(DISP, "Dispatcher.Once"): {_m(OSVC, "OrderService.Ship"): I},
        _m(TOG, "Toggle.Plain"): {_m(OSVC, "OrderService.Ship"): I},
        _m(WIN, "Windowsy.Run"): {_m(OSVC, "OrderService.Ship"): I},
        # a TEST caller with the engine's tuple of the name: the test fake counts as an implementer for it (two
        # implementers: AMBIGUOUS each)
        _m(FAKE, "CartTests.Creates"): {_m(ISCH, "IShoppingCartHelper.CreateShoppingCartAsync"): I,
                                        _m(SCH, "ShoppingCartHelper.CreateShoppingCartAsync"): A,
                                        _m(FAKE, "FakeCartHelper.CreateShoppingCartAsync"): A},
    }


# (caller, member, kind, prior): kind "none" = no typed key for the member AND no removal of its tuples. prior = the
# engines' captures hold a tuple of it (the no-removal check has teeth there).
MUSTNOT = [
    (_m(COH, "CreateOrderHandler.Handle"), "Recalculate", "none", False),     # an `await` result
    (_m(PM, "PriceMath.Gross"), "ApplyTax", "none", True),                    # an extension on a builtin type
    (_m(CTL, "OrdersController.Get"), "Log", "none", False),                  # an inherited external member
    (_m(CTL, "OrdersController.Get"), "Find", "none", False),
    (_m(REP, "ReportJob.Run"), "Send", "none", False),                        # ambiguous between two usings
    (_m(ALERT, "AlertSender.Alert"), "_send", "none", False),                 # a delegate field
    (_m(DISP, "Dispatcher.Run"), "Ship", "none", False),                      # an overloaded caller
    (_m(DISP, "Dispatcher.Run"), "Cancel", "none", False),
    (_m(TOG, "Toggle.Run"), "Ship", "none", False),                           # an #if region
    (_m(TOG, "Toggle.Run"), "Cancel", "none", False),
    (_m(STEP, "StepRunner.Run"), "Execute", "keep", True),                    # five implementers: nothing removed
    # (NotifyHandler.Handle's `Notify`, two implementers, all in one file: its dispatch set is fully known, so the
    # group REPLACEs the engine's tuple since the re-check round; section W locks that)
]


def sec_shapes(buckets) -> None:
    print("\n-- C: every reported shape gives the exact key and grade, end to end (both engines, default + "
          "PRECISE) --")
    n_cs = len([r for r in CQRS if r.endswith(".cs")])
    for b in buckets:
        for mode in MODES:
            run = run_for(b, "cqrs", mode)
            st = run.stats
            check(f"C {b}/cqrs [{mode}]: every C# file read ({n_cs}), none failed, no error",
                  st["error"] is None and st["files_failed"] == 0 and st["files"] == n_cs
                  and "csharp" not in st["langs_disabled"],
                  (st["error"], st["files_failed"], st["files"], st["langs_disabled"]))
            got = typed_view(run.nodes, form="name")
            want = expect(b)
            diff = {k: (got.get(k), want.get(k)) for k in sorted(set(got) | set(want)) if got.get(k) != want.get(k)}
            check(f"C {b}/cqrs [{mode}]: the typed keys and grades are exactly the expected ones ({len(want)} callers)",
                  not diff, diff)
            bad = [(c, str(t)) for c, t in run.out if GX.is_call_key(t) and t not in {n["key"] for n in run.nodes}]
            check(f"C {b}/cqrs [{mode}]: every keyed callee names a node of the graph", not bad, bad[:5])
            rep = tuples_of(run, run.out, _m(SHIP, "ShipOrderHandler.Handle"), "Validate")
            pre = tuples_of(run, run.before, _m(SHIP, "ShipOrderHandler.Handle"), "Validate")
            sid = stable(run.nodes)
            # (default: the engine's name tuple `ShipAudit.Validate`; PRECISE: its same-file key of that node)
            blind = [c for c, k in pre if (sid.get(c, "") if k else c).split("@")[0].endswith("ShipAudit.Validate")]
            check(f"C {b}/cqrs [{mode}]: the engine's receiver-blind same-file tuple Handle -> ShipAudit.Validate "
                  f"is REPLACED by the typed Order.Validate key",
                  bool(blind) and len(rep) == 1 and rep[0][1] and sid.get(rep[0][0], "").startswith(
                      f"Method:Order.Validate@{ORDER}:"), (pre, rep))


def sec_mustnot(buckets) -> None:
    print("\n-- D: must-nots: no typed edge AND no removal (both engines, both modes) --")
    for b in buckets:
        for mode in MODES:
            run = run_for(b, "cqrs", mode)
            name_of = {n["key"]: n.get("name") for n in run.nodes}
            missing_prior = []
            for caller, member, kind, prior in MUSTNOT:
                node = node_of(run.nodes, caller)
                if node is None:
                    check(f"D {b} [{mode}] {caller}: the caller is a node", False)
                    continue
                typed = [t for t in (node.get("call_typed") or {}) if TC.name_seg(name_of.get(t, "")) == member]
                pre = tuples_of(run, run.before, caller, member)
                post = tuples_of(run, run.out, caller, member)
                lost = [t for t in pre if t not in post]
                if kind == "none":
                    check(f"D {b} [{mode}] {caller} `{member}`: no typed key, no removal", not typed and not lost,
                          (typed, lost))
                else:
                    check(f"D {b} [{mode}] {caller} `{member}`: no removal (a typed key may be added)", not lost, lost)
                if prior and not pre:
                    missing_prior.append((caller, member))
            check(f"D {b} [{mode}]: every must-not marked `prior` has an engine tuple of its member (the check has "
                  f"teeth)", not missing_prior, missing_prior)
            fake_prod = [c for c, tg in typed_view(run.nodes, form="name").items()
                         if not c.startswith("tests/") and any(t.startswith(FAKE) for t in tg)]
            check(f"D {b} [{mode}]: no production caller reaches the test fake FakeCartHelper", not fake_prod, fake_prod)


# ================================================================================================
# V: the review round's shapes (tests TREES["review"]), every must-not beside its positive sibling
# ================================================================================================

RV = "src/Rv"
R_SCOPE, R_NODE, R_TREE, R_NCTL = f"{RV}/Scope/Router.cs", f"{RV}/Nest/Node.cs", f"{RV}/Nest/Tree.cs", \
    f"{RV}/Nest/NodeController.cs"
R_WID, R_STORE, R_HB, R_OH = f"{RV}/Args/Widget.cs", f"{RV}/Args/Store.cs", f"{RV}/Virt/HandlerBase.cs", \
    f"{RV}/Virt/OrderHandler.cs"
R_NOT, R_SHP, R_SLOT, R_JOB = f"{RV}/Virt/Notifiers.cs", f"{RV}/Virt/Shapes.cs", f"{RV}/Virt/Slots.cs", \
    f"{RV}/Virt/Jobs.cs"
R_RUN, R_FAC, R_REPO, R_CART = f"{RV}/Iface/Runners.cs", f"{RV}/Iface/Factory.cs", f"{RV}/Iface/Repos.cs", \
    f"{RV}/Ctors/Carts.cs"
R_FAKE, R_PAN = "tests/Rv.Tests/Fakes.cs", f"{RV}/Virt/Panels.cs"

# {caller: {target: grade}}: EXACTLY these callers are keyed. Each target is the binding Roslyn 4.8 gives the call
# (for a virtual / interface call: its dispatch set); a comment names the review finding each group answers.
V_EXPECT = {
    # cs-block-scoped-local-leak: after the block (if, foreach, catch, for) the name is the FIELD (a Mailer);
    # inside the block, a pattern variable in the enclosing statement list, a switch section's local: the local
    _m(R_SCOPE, "Router.Route"): {_m(R_SCOPE, "Mailer.Flush"): E, _m(R_SCOPE, "Texter"): E},
    _m(R_SCOPE, "Router.RouteEach"): {_m(R_SCOPE, "Mailer.Flush"): E},
    _m(R_SCOPE, "Router.RouteCatch"): {_m(R_SCOPE, "Mailer.Flush"): E},
    _m(R_SCOPE, "Router.RouteFor"): {_m(R_SCOPE, "Mailer.Flush"): E},
    _m(R_SCOPE, "Router.Inside"): {_m(R_SCOPE, "Texter"): E, _m(R_SCOPE, "Texter.Flush"): E},
    _m(R_SCOPE, "Router.Pattern"): {_m(R_SCOPE, "Texter.Flush"): E},
    _m(R_SCOPE, "Router.Sections"): {_m(R_SCOPE, "Texter"): E, _m(R_SCOPE, "Texter.Flush"): E},
    # cs-nested-type-shadow: the class's nested type, an outer class's, a base class's — before the namespace's; a
    # class without one (Garden) and one whose base is external (NodeController) still bind the namespace's
    _m(R_TREE, "Tree.Walk"): {_m(R_TREE, "Node"): E, _m(R_TREE, "Node.Visit"): E},
    _m(R_TREE, "Tree.Walk2"): {_m(R_TREE, "Node"): E, _m(R_TREE, "Node.Visit"): E},
    _m(R_TREE, "Tree.Walk3"): {_m(R_TREE, "Node.Visit"): E},
    _m(R_TREE, "Tree.Walk4"): {_m(R_TREE, "Node.Visit"): E},
    _m(R_TREE, "Walker.Go"): {_m(R_TREE, "Node"): E, _m(R_TREE, "Node.Visit"): E},
    _m(R_TREE, "Forest.Plant"): {_m(R_TREE, "Leaf"): E, _m(R_TREE, "Leaf.Grow"): E},
    _m(R_TREE, "Garden.Plant"): {_m(R_NODE, "Leaf"): I, _m(R_NODE, "Leaf.Grow"): I},
    _m(R_NCTL, "NodeController.Get"): {_m(R_NODE, "Node.Visit"): I},
    # cs-arg-type-applicability: a rival (an extension of another signature, a base overload) and arguments known to
    # fit the hit -> INFERRED, never EXTRACTED; not known to fit -> nothing (UseString, Go: must-nots); a
    # virtual / override pair of one signature is no rival
    _m(R_WID, "Painter.UseInt"): {_m(R_WID, "Widget.Paint"): I},
    _m(R_WID, "Painter.UseShort"): {_m(R_WID, "Widget.Paint"): I},
    _m(R_WID, "Painter.UseIntLiteral"): {_m(R_WID, "Widget.Tint"): I},
    _m(R_STORE, "StoreUser.GoOrder"): {_m(R_STORE, "OrderStore.Save"): I},
    _m(R_STORE, "StoreUser.GoKeep"): {_m(R_STORE, "OrderStore.Keep"): I},
    # cs-typed-note-virtual-dispatch: the declared member INFERRED (an abstract one never EXTRACTED) + its overrides
    # (1: INFERRED, 2..4: AMBIGUOUS, 5: none); the test-file override unseen by a production caller; a `new virtual`
    # starts another slot; a sealed override and an unrebound construction are exact
    _m(R_HB, "HandlerBase.Run"): {_m(R_HB, "HandlerBase.HandleCore"): I, _m(R_OH, "OrderHandler.HandleCore"): I},
    _m(R_NOT, "NotifySvc.Notify"): {_m(R_NOT, "NotifierBase.Send"): I, _m(R_NOT, "MailNotifier.Send"): A,
                                    _m(R_NOT, "SmsNotifier.Send"): A},
    _m(R_JOB, "JobRunner.Run"): {_m(R_JOB, "Job.Exec"): I},
    _m(R_SHP, "ShapeUser.Any"): {_m(R_SHP, "Shape.Area"): I, _m(R_SHP, "Circle.Area"): I},
    _m(R_SHP, "ShapeUser.Round"): {_m(R_SHP, "Circle.Area"): E},
    _m(R_SHP, "ShapeUser.Fresh"): {_m(R_SHP, "Shape"): E, _m(R_SHP, "Shape.Area"): E},
    _m(R_SHP, "ShapeUser.Again"): {_m(R_SHP, "Shape"): E, _m(R_SHP, "Shape.Area"): I, _m(R_SHP, "Circle.Area"): I},
    _m(R_SLOT, "GoUser.Use"): {_m(R_SLOT, "Base.Go"): I},
    _m(R_SLOT, "GoUser.UseMid"): {_m(R_SLOT, "Mid.Go"): I, _m(R_SLOT, "Low.Go"): I},
    # a sealed class or a struct receiver is exact: no subclass can exist (EXTRACTED stays possible)
    _m(R_PAN, "PanelUser.Any"): {_m(R_PAN, "Panel.Draw"): I, _m(R_PAN, "GlassPanel.Draw"): I},
    _m(R_PAN, "PanelUser.Glass"): {_m(R_PAN, "GlassPanel.Draw"): E},
    # cs-iface-impl-new-hidden: HidingRunner's `new Run` implements nothing; an override of the mapped virtual and a
    # class re-listing the interface do
    _m(R_RUN, "UseRunner.Go"): {_m(R_RUN, "IRunner.Run"): I, _m(R_RUN, "BaseRunner.Run"): I},
    _m(R_RUN, "UseWalker.Go"): {_m(R_RUN, "IWalker.Walk"): I, _m(R_RUN, "BaseWalker.Walk"): A,
                                _m(R_RUN, "FastWalker.Walk"): A},
    _m(R_RUN, "UsePinger.Go"): {_m(R_RUN, "IPinger.Ping"): I, _m(R_RUN, "BasePinger.Ping"): A,
                                _m(R_RUN, "LoudPinger.Ping"): A},
    # cs-iface-static-member: the static member only, an exact call (EXTRACTED: caller and member in one file); an
    # instance member through the same interface dispatches
    _m(R_FAC, "FactoryUser.Use"): {_m(R_FAC, "IFactory.Make"): E, _m(R_FAC, "IFactory.Build"): I,
                                   _m(R_FAC, "Factory.Build"): I},
    # cs-iface-generic-closed-args: IRepo<Order> never reaches UserRepo (nor the test FakeRepo); IRepo<T> reaches
    # both; ISource<out T>: no reference conversion between User and Order, so UserSource is out too
    _m(R_REPO, "RepoUser.Use"): {_m(R_REPO, "IRepo.Find"): I, _m(R_REPO, "OrderRepo.Find"): I},
    _m(R_REPO, "AnyRepoUser.Use"): {_m(R_REPO, "IRepo.Find"): I, _m(R_REPO, "OrderRepo.Find"): A,
                                    _m(R_REPO, "UserRepo.Find"): A},
    _m(R_REPO, "SourceUser.Use"): {_m(R_REPO, "ISource.Get"): I, _m(R_REPO, "OrderSource.Get"): I},
    # ... while DogSource (Dog : Animal) is an ISource<Animal> through the covariant `out T`
    _m(R_REPO, "PetUser.Use"): {_m(R_REPO, "ISource.Get"): I, _m(R_REPO, "AnimalSource.Get"): A,
                                _m(R_REPO, "DogSource.Get"): A},
}

def v_expect(bucket: str) -> dict:
    """V_EXPECT plus the struct receiver's exact call (Money.Show -> Money.ToString, EXTRACTED: a struct has no
    subclass), named per engine: 0.8.39 gives a struct no owner while graphify_extract's C# members are off (the
    records allowlist of F), so its methods are the :Function `Show` / `ToString` there."""
    own = bucket != "0839"
    return dict(V_EXPECT, **{_m(R_PAN, "Money.Show" if own else "Show"):
                             {_m(R_PAN, "Money.ToString" if own else "ToString"): E}})


# (caller, member, forbidden target or None, kind): "none" = no typed key of the member AND no removal of its tuples;
# "not" = never a key to the forbidden target (and never EXTRACTED for the member when grade-only is the point).
V_MUSTNOT = [
    (_m(R_SCOPE, "Router.Route"), "Flush", _m(R_SCOPE, "Texter.Flush"), "not"),   # the EXTRACTED upgrade
    (_m(R_SCOPE, "Router.RouteEach"), "Flush", _m(R_SCOPE, "Texter.Flush"), "not"),
    (_m(R_SCOPE, "Router.RouteCatch"), "Flush", _m(R_SCOPE, "TexterError.Flush"), "not"),
    (_m(R_SCOPE, "Router.RouteFor"), "Flush", _m(R_SCOPE, "Texter.Flush"), "not"),
    (_m(R_TREE, "Tree.Walk"), "Visit", _m(R_NODE, "Node.Visit"), "not"),
    (_m(R_TREE, "Tree.Walk2"), "Visit", _m(R_NODE, "Node.Visit"), "not"),
    (_m(R_TREE, "Walker.Go"), "Visit", _m(R_NODE, "Node.Visit"), "not"),
    (_m(R_TREE, "Forest.Plant"), "Grow", _m(R_NODE, "Leaf.Grow"), "not"),
    (_m(R_WID, "Painter.UseString"), "Paint", None, "none"),                       # WidgetExt.Paint binds
    (_m(R_WID, "Painter.UseLongLiteral"), "Tint", None, "none"),                   # `5L`: WidgetExt.Tint binds
    (_m(R_STORE, "StoreUser.Go"), "Save", None, "none"),                           # StoreBase.Save binds
    (_m(R_HB, "HandlerBase.Run"), "HandleCore", _m(R_FAKE, "FakeHandler.HandleCore"), "not"),
    (_m(R_JOB, "JobRunner.Run"), "Exec", None, "keep"),                            # 5 overrides: nothing removed
    (_m(R_SLOT, "GoUser.Use"), "Go", _m(R_SLOT, "Low.Go"), "not"),
    (_m(R_RUN, "UseRunner.Go"), "Run", _m(R_RUN, "HidingRunner.Run"), "not"),
    (_m(R_FAC, "FactoryUser.Use"), "Make", _m(R_FAC, "Factory.Make"), "not"),
    (_m(R_REPO, "RepoUser.Use"), "Find", _m(R_REPO, "UserRepo.Find"), "not"),
    (_m(R_REPO, "RepoUser.Use"), "Find", _m(R_FAKE, "FakeRepo.Find"), "not"),
    (_m(R_REPO, "SourceUser.Use"), "Get", _m(R_REPO, "UserSource.Get"), "not"),
]

# never EXTRACTED where the review found an upgrade to a binding the compiler does not make, or a body-less member
V_NEVER_E = [(_m(R_HB, "HandlerBase.Run"), _m(R_HB, "HandlerBase.HandleCore")),
             (_m(R_WID, "Painter.UseInt"), _m(R_WID, "Widget.Paint")),
             (_m(R_STORE, "StoreUser.GoOrder"), _m(R_STORE, "OrderStore.Save")),
             (_m(R_NOT, "NotifySvc.Notify"), _m(R_NOT, "NotifierBase.Send"))]


def sec_review(buckets) -> None:
    print("\n-- V: the review round's shapes, each must-not beside its positive sibling (both engines, both modes) --")
    n_cs = len([r for r in REVIEW if r.endswith(".cs")])
    for b in buckets:
        for mode in MODES:
            run = run_for(b, "review", mode)
            st = run.stats
            check(f"V {b}/review [{mode}]: every C# file read ({n_cs}), none failed, no error",
                  st["error"] is None and st["files_failed"] == 0 and st["files"] == n_cs,
                  (st["error"], st["files_failed"], st["files"]))
            got = typed_view(run.nodes, form="name")
            want = v_expect(b)
            diff = {k: (got.get(k), want.get(k)) for k in sorted(set(got) | set(want)) if got.get(k) != want.get(k)}
            check(f"V {b}/review [{mode}]: the typed keys and grades are exactly Roslyn's bindings "
                  f"({len(want)} callers)", not diff, diff)
            for caller, member, bad, kind in V_MUSTNOT:
                node = node_of(run.nodes, caller)
                if node is None:
                    check(f"V {b} [{mode}] {caller}: the caller is a node", False)
                    continue
                name_of = {n["key"]: n.get("name") for n in run.nodes}
                typed = {t: g for t, g in (node.get("call_typed") or {}).items()
                         if TC.name_seg(name_of.get(t, "")) == member}
                pre = tuples_of(run, run.before, caller, member)
                post = tuples_of(run, run.out, caller, member)
                lost = [t for t in pre if t not in post]
                if kind == "none":
                    check(f"V {b} [{mode}] {caller} `{member}`: no typed key, no removal", not typed and not lost,
                          (typed, lost))
                elif kind == "keep":
                    check(f"V {b} [{mode}] {caller} `{member}`: no removal (more overrides than MAX_TARGETS)",
                          not lost and bool(pre), (pre, post))
                else:
                    bk = key_of(run.nodes, bad)
                    check(f"V {b} [{mode}] {caller} `{member}`: never keyed to {bad.split('::')[-1]} "
                          f"({bad.split('::')[0].rsplit('/', 1)[-1]})", bk is not None and bk not in typed,
                          (bk, typed))
            for caller, target in V_NEVER_E:
                node, tk = node_of(run.nodes, caller), key_of(run.nodes, target)
                g = ((node or {}).get("call_typed") or {}).get(tk)
                check(f"V {b} [{mode}] {caller} -> {target.split('::')[-1]}: linked, never EXTRACTED",
                      g in ("INFERRED", "AMBIGUOUS"), g)
            # the nested-type REPLACE re-emits the engine's correct same-file target (no tuple of Visit is lost to
            # another file's Node)
            post = tuples_of(run, run.out, _m(R_TREE, "Tree.Walk"), "Visit")
            sid = stable(run.nodes)
            check(f"V {b} [{mode}] Tree.Walk: every `Visit` tuple left names the nested Tree.Node.Visit",
                  bool(post) and all(k and sid.get(c, "").startswith(f"Method:Node.Visit@{R_TREE}:") for c, k in post),
                  post)
            dsp = (st["by_lang"].get("csharp") or {})
            check(f"V {b} [{mode}]: the C# stats report the new rules (virtual, overloads; dispatch's new counters)",
                  {"sites", "static", "overrides", "fanout", "too_many", "unknown", "mixed"}
                  <= set(dsp.get("virtual", {}))
                  and dsp["virtual"]["too_many"] >= 1 and dsp.get("overloads", {}).get("unknown", 0) >= 2
                  and dsp.get("overloads", {}).get("fit", 0) >= 3
                  and dsp.get("dispatch", {}).get("static_member", 0) >= 1
                  and dsp.get("dispatch", {}).get("closed_args", 0) >= 1, {k: dsp.get(k) for k in
                                                                         ("virtual", "overloads", "dispatch")})


# ================================================================================================
# W: the re-check round (tests TREES["recheck"], plus the review / cqrs trees' same-file layouts), judged on the
# PUSHED :CALLS edges as well as the typed keys
# ================================================================================================

RC = "src/Rc"
W_GEN, W_API, W_GST, W_QUAL = f"{RC}/Gen/Handlers.cs", f"{RC}/Gen/Api.cs", f"{RC}/Gen/Stores.cs", f"{RC}/Qual/Stores.cs"
W_FRESH, W_JOBS, W_FAC, W_FUSE = f"{RC}/Fresh/Repos.cs", f"{RC}/Slots/Jobs.cs", f"{RC}/Iface/Factory.cs", \
    f"{RC}/Iface/FactoryUser.cs"
W_PLUG = f"{RC}/Plug/Plugins.cs"

# {caller: {target: grade}}: EXACTLY these callers are keyed, each target the binding Roslyn 4.8 gives the call; a
# virtual / interface call's set is narrowed to what runs, as DeepMap documents (a never-rebound construction is
# exact; the override that runs is linked, not the overridden member Roslyn lists)
W_EXPECT = {
    # rcReader 1: an override of a generic base's member (one hop, two hops) has the base's signature under the
    # derived class's base list: no rival (INFERRED: a virtual member through a field)
    _m(W_API, "Api.Post"): {_m(W_GEN, "OrderHandler.Handle"): I},
    _m(W_API, "Api.PostNone"): {_m(W_GEN, "OrderHandler.Handle"): I, _m(W_API, "Api.Make"): E},
    _m(W_API, "Api.AuditUnknown"): {_m(W_GEN, "OrderHandler.Audit"): I, _m(W_API, "Api.Make"): E},
    _m(W_API, "Api.PostLow"): {_m(W_GEN, "LowHandler.Handle"): I},
    # ... while Store<Order>.Save(Order) is a rival of another signature: an argument that fits the hit -> INFERRED;
    # SaveOrder (Store<T>.Save binds) and SaveNext (not proven) are must-nots
    _m(W_GST, "StoreUser.SaveCart"): {_m(W_GST, "CartStore.Save"): I},
    _m(W_GST, "StoreUser.SaveNext"): {_m(W_GST, "StoreUser.Next"): E},
    # a parameter type spelled with a namespace, `System.Int32`, an alias, a nullable reference annotation: one
    # signature with the base's
    _m(W_QUAL, "QualUser.SaveUnknown"): {_m(W_QUAL, "QualStore.Save"): I, _m(W_QUAL, "QualUser.GetCart"): E},
    _m(W_QUAL, "QualUser.SendDefault"): {_m(W_QUAL, "QualStore.Send"): I},
    _m(W_QUAL, "QualUser.CountUnknown"): {_m(W_QUAL, "QualStore.Count"): I, _m(W_QUAL, "QualUser.Size"): E},
    _m(W_QUAL, "QualUser.StopDefault"): {_m(W_QUAL, "QualStore.Stop"): I},
    _m(W_QUAL, "QualUser.NoteUnknown"): {_m(W_QUAL, "QualStore.Note"): I, _m(W_QUAL, "QualUser.Text"): E},
    # rcReader 2: `Repo b = new Repo()` / `Repo c = new()` never rebound: exactly Repo (EXTRACTED in its file, no
    # override); a parameter, `Repo d = new CachedRepo()` and a rebound local still dispatch
    _m(W_FRESH, "RepoUser.TypedNew"): {_m(W_FRESH, "Repo"): E, _m(W_FRESH, "Repo.Save"): E},
    _m(W_FRESH, "RepoUser.TargetNew"): {_m(W_FRESH, "Repo"): E, _m(W_FRESH, "Repo.Save"): E},
    _m(W_FRESH, "RepoUser.Declared"): {_m(W_FRESH, "Repo.Save"): I, _m(W_FRESH, "CachedRepo.Save"): I},
    _m(W_FRESH, "RepoUser.BaseNew"): {_m(W_FRESH, "CachedRepo"): E, _m(W_FRESH, "Repo.Save"): I,
                                      _m(W_FRESH, "CachedRepo.Save"): I},
    _m(W_FRESH, "RepoUser.Rebound"): {_m(W_FRESH, "Repo"): E, _m(W_FRESH, "Repo.Save"): I,
                                      _m(W_FRESH, "CachedRepo.Save"): I},
    # rcReader 3 + 4: interface and virtual calls where `new` methods start other slots (UnderNew overrides
    # NewVirt's `new virtual` slot only)
    _m(W_JOBS, "JobUser.UseI"): {_m(W_JOBS, "IJob.Run"): I, _m(W_JOBS, "JobBase.Run"): A,
                                 _m(W_JOBS, "FastJob.Run"): A},
    _m(W_JOBS, "JobUser.UseBase"): {_m(W_JOBS, "JobBase.Run"): I, _m(W_JOBS, "FastJob.Run"): I},
    _m(W_JOBS, "JobUser.UseFast"): {_m(W_JOBS, "FastJob.Run"): I},
    _m(W_JOBS, "JobUser.UseNv"): {_m(W_JOBS, "NewVirt.Run"): I, _m(W_JOBS, "UnderNew.Run"): I},
    # a static interface member is an exact call (INFERRED from another file)
    _m(W_FUSE, "FactoryUser.Use"): {_m(W_FAC, "IFactory.Make"): I},
    # OddPlugin (`: global::Rc.Plug.IPlugin`) implements IPlugin unseen: the interface member + the seen implementer
    _m(W_PLUG, "PluginHost.Start"): {_m(W_PLUG, "IPlugin.Load"): I, _m(W_PLUG, "CorePlugin.Load"): I},
}

# (tree, caller, member, kind): "none" = no typed key of the member AND no removal; "keep" = no removal (and the
# engine has a tuple of it: the check has teeth)
W_MUSTNOT = [
    ("recheck", _m(W_GST, "StoreUser.SaveOrder"), "Save", "none"),     # Store<Order>.Save binds: a rival stays one
    ("recheck", _m(W_GST, "StoreUser.SaveNext"), "Save", "none"),      # Store<Order>.Save binds; not proven
    ("recheck", _m(W_PLUG, "PluginHost.Start"), "Load", "keep"),       # an implementer the pass cannot see
]

# (tree, caller, target): after the WHOLE push (link, the bare-call and local-def passes, _resolve_calls) the caller
# has NO :CALLS edge to target, while the same push with typed linking off has one (the engine's tuple: teeth)
W_NO_EDGE = [
    ("recheck", _m(W_FRESH, "RepoUser.TypedNew"), _m(W_FRESH, "CachedRepo.Save")),     # rcReader 2
    ("recheck", _m(W_FRESH, "RepoUser.TargetNew"), _m(W_FRESH, "CachedRepo.Save")),
    ("recheck", _m(W_JOBS, "JobUser.UseI"), _m(W_JOBS, "UnderNew.Run")),                # rcReader 3: interface
    ("recheck", _m(W_JOBS, "JobUser.UseBase"), _m(W_JOBS, "UnderNew.Run")),             # rcReader 4: another slot
    ("recheck", _m(W_JOBS, "JobUser.UseFast"), _m(W_JOBS, "UnderNew.Run")),
    ("review", _m(R_RUN, "UseRunner.Go"), _m(R_RUN, "HidingRunner.Run")),               # rcReader 3: same file
    ("review", _m(R_FAC, "FactoryUser.Use"), _m(R_FAC, "Factory.Make")),
    ("review", _m(R_REPO, "RepoUser.Use"), _m(R_REPO, "UserRepo.Find")),
]

# (tree, caller, target, grade): pushed edges that must stay (the true implementers / overrides, at that grade;
# None: any grade — the engine's own tuple kept)
W_EDGE = [
    ("review", _m(R_RUN, "UseRunner.Go"), _m(R_RUN, "IRunner.Run"), I),
    ("review", _m(R_RUN, "UseRunner.Go"), _m(R_RUN, "BaseRunner.Run"), I),
    ("review", _m(R_FAC, "FactoryUser.Use"), _m(R_FAC, "IFactory.Make"), E),
    ("review", _m(R_REPO, "RepoUser.Use"), _m(R_REPO, "IRepo.Find"), I),
    ("review", _m(R_REPO, "RepoUser.Use"), _m(R_REPO, "OrderRepo.Find"), I),
    ("recheck", _m(W_JOBS, "JobUser.UseI"), _m(W_JOBS, "JobBase.Run"), A),
    ("recheck", _m(W_JOBS, "JobUser.UseI"), _m(W_JOBS, "FastJob.Run"), A),
    ("recheck", _m(W_JOBS, "JobUser.UseNv"), _m(W_JOBS, "UnderNew.Run"), I),
    ("recheck", _m(W_FRESH, "RepoUser.TypedNew"), _m(W_FRESH, "Repo.Save"), E),
    ("recheck", _m(W_PLUG, "PluginHost.Start"), _m(W_PLUG, "OddPlugin.Load"), None),
    ("cqrs", _m(NOT, "NotifyHandler.Handle"), _m(NOT, "INotifier.Notify"), I),
    ("cqrs", _m(NOT, "NotifyHandler.Handle"), _m(NOT, "EmailNotifier.Notify"), A),
    ("cqrs", _m(NOT, "NotifyHandler.Handle"), _m(NOT, "SmsNotifier.Notify"), A),
]


def push_edges(run: Run, root: Path, env: dict) -> dict:
    """{(caller key, callee key): grade}: the rest of the push after link, exactly as sync runs it (the bare-call
    pass, the local-def scope data and calls, _resolve_calls), on copies of the run's nodes and CALLS."""
    nodes, calls = copy.deepcopy(run.nodes), list(run.out)
    with clean_env(**env):
        S._attach_bare_calls(root, nodes, calls)
        S._attach_local_defs(root, nodes, calls)
        S._attach_local_def_calls(root, nodes, calls)
        return S._resolve_calls(nodes, calls)


def sec_recheck(buckets) -> None:
    print("\n-- W: the re-check round's shapes (overloads across a generic base list, constructed locals, interface "
          "and virtual REPLACE), on the typed keys AND the pushed edges (both engines, both modes) --")
    n_cs = len([r for r in RECHECK if r.endswith(".cs")])
    for b in buckets:
        for mode in MODES:
            run = run_for(b, "recheck", mode)
            st = run.stats
            check(f"W {b}/recheck [{mode}]: every C# file read ({n_cs}), none failed, no error",
                  st["error"] is None and st["files_failed"] == 0 and st["files"] == n_cs,
                  (st["error"], st["files_failed"], st["files"]))
            got = typed_view(run.nodes, form="name")
            diff = {k: (got.get(k), W_EXPECT.get(k)) for k in sorted(set(got) | set(W_EXPECT))
                    if got.get(k) != W_EXPECT.get(k)}
            check(f"W {b}/recheck [{mode}]: the typed keys and grades are exactly Roslyn's bindings "
                  f"({len(W_EXPECT)} callers)", not diff, diff)
            for tree, caller, member, kind in W_MUSTNOT:
                r = run_for(b, tree, mode)
                node = node_of(r.nodes, caller)
                if node is None:
                    check(f"W {b} [{mode}] {caller}: the caller is a node", False)
                    continue
                name_of = {n["key"]: n.get("name") for n in r.nodes}
                typed = [t for t in (node.get("call_typed") or {}) if tc().name_seg(name_of.get(t, "")) == member]
                pre, post = tuples_of(r, r.before, caller, member), tuples_of(r, r.out, caller, member)
                lost = [t for t in pre if t not in post]
                if kind == "none":
                    check(f"W {b} [{mode}] {caller} `{member}`: no typed key, no removal", not typed and not lost,
                          (typed, lost))
                else:
                    check(f"W {b} [{mode}] {caller} `{member}`: no removal (the engine's tuples kept)",
                          bool(pre) and not lost, (pre, lost))
            edges, off = {}, {}
            for tree in ("recheck", "review", "cqrs"):
                r = run_for(b, tree, mode)
                edges[tree] = push_edges(r, root_for(tree), MODES[mode])
                r0 = run_pipeline(captured(b, tree)["graph"], root_for(tree), dict(MODES[mode], DEEPMAP_TYPED_CALLS="0"))
                off[tree] = push_edges(r0, root_for(tree), dict(MODES[mode], DEEPMAP_TYPED_CALLS="0"))
            for tree, caller, target in W_NO_EDGE:
                r = run_for(b, tree, mode)
                ck, tk = key_of(r.nodes, caller), key_of(r.nodes, target)
                check(f"W {b} [{mode}] {caller.split('::')[-1]}: no pushed edge to {target.split('::')[-1]} (the "
                      f"engine's tuple of it removed; typed linking off: {off[tree].get((ck, tk))})",
                      ck is not None and tk is not None and (ck, tk) not in edges[tree] and (ck, tk) in off[tree],
                      (ck, tk, edges[tree].get((ck, tk)), off[tree].get((ck, tk))))
            for tree, caller, target, grade in W_EDGE:
                r = run_for(b, tree, mode)
                ck, tk = key_of(r.nodes, caller), key_of(r.nodes, target)
                g = edges[tree].get((ck, tk))
                check(f"W {b} [{mode}] {caller.split('::')[-1]} -> {target.split('::')[-1]}: pushed "
                      f"{grade or 'kept'}", g is not None and (grade is None or g == grade), g)
            # the cqrs interface group REPLACEs now that its whole dispatch set is known (2 implementers): what is left
            # of Notify is exactly the typed keys, each once (the engine's own tuple, a name or under PRECISE its
            # same-file key, is gone)
            r = run_for(b, "cqrs", mode)
            post = tuples_of(r, r.out, _m(NOT, "NotifyHandler.Handle"), "Notify")
            pre = tuples_of(r, r.before, _m(NOT, "NotifyHandler.Handle"), "Notify")
            nh = node_of(r.nodes, _m(NOT, "NotifyHandler.Handle")) or {}
            name_of = {n["key"]: n.get("name") for n in r.nodes}
            typed = sorted(str(t) for t in (nh.get("call_typed") or {}) if tc().name_seg(name_of.get(t, "")) == "Notify")
            check(f"W {b} [{mode}] NotifyHandler.Handle: the engine's tuple of Notify is REPLACED by the typed keys of "
                  f"the known dispatch set (each once)", bool(pre) and len(typed) == 3 and all(k for _c, k in post)
                  and sorted(c for c, _k in post) == typed, (pre, post, typed))
            ds = (st["by_lang"].get("csharp") or {}).get("dispatch", {})
            check(f"W {b} [{mode}]: dispatch stats count the known sets (known) and the static member",
                  ds.get("known", 0) >= 1 and ds.get("static_member", 0) >= 1, ds)
    if len(buckets) == 2:
        for mode in MODES:
            a = typed_view(run_for(buckets[0], "recheck", mode).nodes)
            z = typed_view(run_for(buckets[1], "recheck", mode).nodes)
            diff = {k: (a.get(k), z.get(k)) for k in sorted(set(a) | set(z)) if a.get(k) != z.get(k)}
            check(f"W recheck [{mode}]: identical typed tuples on version-stable keys, 0.8.39 vs 0.8.51",
                  not diff and bool(a), diff)
    # the reader's facts the rules read
    if rcs() is None:
        return
    rd = rcs().make_reader(tc().LANGUAGES["csharp"])
    ff = rd.facts(W_FRESH, Src(RECHECK))
    if ff is None:
        check("W the reader gives facts for Fresh/Repos.cs", False)
        return
    lc = {s.name: s.locals for s in ff.scopes}
    kinds = {x: [bd.kind for bd in lc.get(sn, {}).get(x, ())] for sn, x in (("TypedNew", "b"), ("TargetNew", "c"),
                                                                             ("BaseNew", "d"), ("Rebound", "e"))}
    check("W reader: `Repo b = new Repo()` and `Repo c = new()` are ctor (exactly Repo), `Repo d = new CachedRepo()` "
          "stays ann (a declaration), a rebound `Repo e = new Repo()` is ctor (the core reads its reassignment)",
          kinds == {"b": ["ctor"], "c": ["ctor"], "d": ["ann"], "e": ["ctor"]}, kinds)
    fg = rd.facts(W_GEN, Src(RECHECK))
    cs = getattr(fg, "_cs", None) or {}
    ci = {c.name: i for i, c in enumerate(fg.classes)} if fg is not None else {}
    bh = fg.classes[ci["BaseHandler"]] if "BaseHandler" in ci else None
    hl = bh.methods["Handle"][0].line if bh is not None and bh.methods.get("Handle") else -1
    check("W reader: _cs tporder / bsig / msig: BaseHandler's type parameters in order, OrderHandler's and MidHandler's "
          "base lists with their type arguments, the generic Handle's parameters tagged",
          cs.get("tporder", {}).get(ci.get("BaseHandler")) == ("TReq", "TRes")
          and cs.get("bsig", {}).get(ci.get("OrderHandler")) == (("g", ("n", ("BaseHandler",)), ("n", ("CreateOrder",)),
                                                                   ("b", "int")),)
          and cs.get("bsig", {}).get(ci.get("MidHandler")) == (("g", ("n", ("BaseHandler",)), ("tp", "T"), ("b", "int")),)
          and cs.get("msig", {}).get(("Handle", hl)) == (("tp", "TReq"), ("n", ("CancellationToken",))),
          {k: cs.get(k) for k in ("tporder", "bsig", "msig")})


# ================================================================================================
# S: the fix round (tests TREES["sigs"]): class type parameters, override slots and interface mappings compared by
# signature, rebinding of a constructed local, external names of one simple name — on the typed keys AND the pushed
# :CALLS edges
# ================================================================================================

SG = "src/Sg"
S_STO, S_SU, S_LB, S_REP = f"{SG}/Open/Stores.cs", f"{SG}/Open/StoreUser.cs", f"{SG}/Open/LastBase.cs", \
    f"{SG}/Repos/Repos.cs"
S_SL, S_PER, S_LOC, S_LO4 = f"{SG}/Slots/Slots.cs", f"{SG}/Slots/Persist.cs", f"{SG}/Locals/Locals.cs", \
    f"{SG}/Locals/Reordered.cs"
S_DR, S_DR2, S_DR3 = f"{SG}/Ext/Drawers.cs", f"{SG}/Ext/Drawers2.cs", f"{SG}/Ext/Drawers3.cs"
_S_REBOUND = {_m(S_LOC, "Repo"): E, _m(S_LOC, "CachedRepo"): E, _m(S_LOC, "Repo.Keep"): I,
              _m(S_LOC, "CachedRepo.Keep"): I}

# {caller: {target: grade}}: EXACTLY these callers of the tree are keyed, each target the binding Roslyn 4.8 gives the
# call with its run-time dispatch (AC1: the override an object of each type runs); a never-rebound construction is
# exact (DeepMap's documented narrowing). Every other caller of the tree is keyed NOTHING: StoreUser.SaveOrder,
# LastUser.SaveOrder, CartBox.Go (Roslyn: the base's Save(T) / Fill(T), a rival the arguments are not known to fit),
# RepoUser.RemoveId (RepoBase.Remove(object)), DrawUser.DrawIt / DrawUser2.DrawIt (Drawer.Draw: another Point).
S_EXPECT = {
    # D1: the hit's own type parameter is its own type: Save(Cart) beside Store<T>.Save(T) keeps a rival (typed only
    # when the argument fits, never EXTRACTED), while Keep(T) overriding Store<T>.Keep(T) is the same slot
    _m(S_SU, "StoreUser.SaveCart"): {_m(S_STO, "CartStore.Save"): I},
    _m(S_SU, "StoreUser.KeepOrder"): {_m(S_STO, "CartStore.Keep"): I},
    _m(S_LB, "LastUser.SaveCart"): {_m(S_LB, "CartStoreX.Save"): I},
    _m(S_STO, "CartBox.GoCart"): {_m(S_STO, "CartBox.Fill"): I},
    _m(S_REP, "RepoUser.AddOne"): {_m(S_REP, "Repository.Add"): E},
    # D2: another signature in the middle (VM1, VM2) or beside the override (VS3, VS4) leaves the slot whole; an
    # override of another signature (VS6, VS7) is another slot's
    _m(S_SL, "SlotUser.Use1"): {_m(S_SL, "VB1.Run1"): I, _m(S_SL, "VS1.Run1"): I},
    _m(S_SL, "SlotUser.Use2"): {_m(S_SL, "IRun2.Run2"): I, _m(S_SL, "VB2.Run2"): A, _m(S_SL, "VS2.Run2"): A},
    _m(S_SL, "SlotUser.Use3"): {_m(S_SL, "IRun3.Run3"): I, _m(S_SL, "VB3.Run3"): A, _m(S_SL, "VS3.Run3"): A},
    _m(S_SL, "SlotUser.Use4"): {_m(S_SL, "VB4.Run4"): I, _m(S_SL, "VS4.Run4"): I},
    _m(S_SL, "SlotUser.Use6"): {_m(S_SL, "VM6.Run6"): I},
    _m(S_SL, "SlotUser.Use7"): {_m(S_SL, "IRun7.Run7"): I, _m(S_SL, "VB7.Run7"): I},
    _m(S_PER, "PersistUser.Persist"): {_m(S_PER, "IOrderStore.SaveAsync"): I,
                                       _m(S_PER, "CachedOrderStore.SaveAsync"): A,
                                       _m(S_PER, "TracingOrderStore.SaveAsync"): A},
    _m(S_PER, "PersistUser.PersistBase"): {_m(S_PER, "OrderStoreBase.SaveAsync"): I,
                                           _m(S_PER, "CachedOrderStore.SaveAsync"): A,
                                           _m(S_PER, "TracingOrderStore.SaveAsync"): A},
    # D3: rebound by a deconstruction (the `T x = new T()` and the `var` form, nested), a ref local, a user-defined ++
    _m(S_LOC, "LocalUser.Deconstruct"): _S_REBOUND,
    _m(S_LOC, "LocalUser.DeconstructVar"): _S_REBOUND,
    _m(S_LOC, "LocalUser.RefLocal"): _S_REBOUND,
    _m(S_LOC, "LocalUser.Nested"): _S_REBOUND,
    _m(S_LOC, "LocalUser.Increment"): {_m(S_LOC, "Repo"): E, _m(S_LOC, "Repo.Keep"): I,
                                       _m(S_LOC, "CachedRepo.Keep"): I},
    _m(S_LOC, "LocalUser.Fresh"): {_m(S_LOC, "Repo"): E, _m(S_LOC, "Repo.Keep"): E},
    _m(S_LO4, "LocalUser4.Deconstruct"): {_m(S_LO4, "Repo4"): E, _m(S_LO4, "CachedRepo4"): E,
                                          _m(S_LO4, "Repo4.Keep4"): I, _m(S_LO4, "CachedRepo4.Keep4"): I},
    # D4: the same qualified type (as written, under `using System.Drawing;`, through `Drw = System.Drawing`) hides
    _m(S_DR, "DrawUser.MarkIt"): {_m(S_DR, "FancyDrawer.Mark"): E},
    _m(S_DR3, "DrawUser3.MarkPlain"): {_m(S_DR3, "PlainDrawer.Mark"): E},
    _m(S_DR3, "DrawUser3.MarkAlias"): {_m(S_DR3, "AliasDrawer.Mark"): E},
}

# (caller, member, engine tuple?): no typed key of the member AND no removal of its tuples (teeth: the engine HAS a
# tuple of it in its own file; a cross-file call the engine leaves untupled is judged on the typed keys alone)
S_MUSTNOT = [
    (_m(S_SU, "StoreUser.SaveOrder"), "Save", False),  # D1 O01 (cross-file): Roslyn Store<T>.Save
    (_m(S_LB, "LastUser.SaveOrder"), "Save", True),    # D1, the base last: the engine's StoreX.Save tuple is true
    (_m(S_STO, "CartBox.Go"), "Fill", True),           # D1 O03: Roslyn Box<T>.Fill
    (_m(S_REP, "RepoUser.RemoveId"), "Remove", True),  # D1 R1: Roslyn RepoBase.Remove (the engine's tuple)
    (_m(S_DR, "DrawUser.DrawIt"), "Draw", True),       # D4 O04: Roslyn Drawer.Draw
    (_m(S_DR2, "DrawUser2.DrawIt"), "Draw", True),     # D4: an unqualified Point under another namespace
]

# (caller, target): after the WHOLE push the caller has NO :CALLS edge to target, while the push with typed linking
# off has one (the engine's tuple: teeth) — a binding Roslyn does not make (or a never-rebound construction's)
S_NO_EDGE = [
    (_m(S_SL, "SlotUser.Use6"), _m(S_SL, "VS6.Run6")),             # an override of Run6(int): another slot
    (_m(S_SL, "SlotUser.Use7"), _m(S_SL, "VS7.Run7")),             # ... behind an interface
    (_m(S_LB, "LastUser.SaveCart"), _m(S_LB, "StoreX.Save")),      # Roslyn CartStoreX.Save (the argument fits)
    (_m(S_LOC, "LocalUser.Fresh"), _m(S_LOC, "CachedRepo.Keep")),  # `Repo b = new Repo()`, never rebound
    (_m(S_DR3, "DrawUser3.MarkPlain"), _m(S_DR3, "AliasDrawer.Mark")),
]

# (caller, target, grade): pushed edges that must stay (None: any grade — the engine's own true tuple kept)
S_EDGE = [
    (_m(S_SL, "SlotUser.Use1"), _m(S_SL, "VS1.Run1"), I),          # D2 V1: the override that runs
    (_m(S_SL, "SlotUser.Use2"), _m(S_SL, "VS2.Run2"), A),          # D2 V2
    (_m(S_SL, "SlotUser.Use3"), _m(S_SL, "VS3.Run3"), A),          # D2 V3
    (_m(S_SL, "SlotUser.Use4"), _m(S_SL, "VS4.Run4"), I),          # D2 V4
    (_m(S_PER, "PersistUser.Persist"), _m(S_PER, "TracingOrderStore.SaveAsync"), A),
    (_m(S_PER, "PersistUser.PersistBase"), _m(S_PER, "TracingOrderStore.SaveAsync"), A),
    (_m(S_REP, "RepoUser.RemoveId"), _m(S_REP, "RepoBase.Remove"), None),          # D1 R1
    (_m(S_LB, "LastUser.SaveOrder"), _m(S_LB, "StoreX.Save"), None),               # D1, the base last
    (_m(S_LOC, "LocalUser.Deconstruct"), _m(S_LOC, "CachedRepo.Keep"), I),         # D3
    (_m(S_LOC, "LocalUser.DeconstructVar"), _m(S_LOC, "CachedRepo.Keep"), I),
    (_m(S_LOC, "LocalUser.RefLocal"), _m(S_LOC, "CachedRepo.Keep"), I),
    (_m(S_LOC, "LocalUser.Nested"), _m(S_LOC, "CachedRepo.Keep"), I),
    (_m(S_LOC, "LocalUser.Increment"), _m(S_LOC, "CachedRepo.Keep"), I),
    (_m(S_LO4, "LocalUser4.Deconstruct"), _m(S_LO4, "Repo4.Keep4"), I),           # D3, reordered: never EXTRACTED
]


def _s_tcmp_table():
    """(label, a, b, want) for typed_calls._cs_tcmp: True one type, False two types (PROVEN), None unknown. A name's
    fourth item is its scope's lookup order (_Linker.cs_scope_levels): ("m", namespace) members, ("u", usings)."""
    U = getattr(tc(), "_CS_UNK", ("?t",))
    SD, WF = ("System", "Drawing"), ("Windows", "Foundation")
    vis = (("m", "Shop"), ("m", ""), ("u", frozenset({"System.Drawing", "Shop"})))
    more = (("m", "App"), ("m", ""), ("u", frozenset({"System.Drawing", "Shop", "System.Text"})))
    wf = (("m", "Shop"), ("m", ""), ("u", frozenset({"Windows.Foundation"})))
    blk = (("m", "A"), ("u", frozenset({"System.Timers"})), ("m", ""), ("u", frozenset({"System.Drawing"})))
    blk2 = (("m", "B"), ("u", frozenset({"System.Threading"})), ("m", ""), ("u", frozenset({"System.Drawing"})))
    enc = (("m", "System.Drawing.Gfx"), ("m", "System.Drawing"), ("m", "System"), ("m", ""), ("u", frozenset()))
    return [
        ("a class type parameter is its own type", ("tpv", "k", "T"), ("tpv", "k", "T"), True),
        ("... never a concrete type", ("tpv", "k", "T"), ("c", "cart", "Cart"), False),
        ("... nor another class's", ("tpv", "k", "T"), ("tpv", "j", "T"), False),
        ("an unfixed type parameter is unknown", U, ("c", "cart", "Cart"), None),
        ("two qualifiers: unknown (one may be relative, or a namespace alias the pass does not read)",
         ("n", "Point", SD, None), ("n", "Point", WF, None), None),
        ("one qualifier: one type", ("n", "Point", SD, None), ("n", "Point", SD, None), True),
        ("a qualifier that may be relative: unknown", ("n", "Point", ("Drawing",), None), ("n", "Point", SD, None),
         None),
        ("unqualified, its namespace imported: one type", ("n", "Point", None, vis), ("n", "Point", SD, None), True),
        ("unqualified, its namespace not imported: unknown", ("n", "Point", None, wf), ("n", "Point", SD, None), None),
        ("unqualified, a nearer using level imports another namespace first: unknown", ("n", "Point", None, blk),
         ("n", "Point", SD, None), None),
        ("unqualified, an enclosing namespace's member: one type", ("n", "Point", None, enc), ("n", "Point", SD, None),
         True),
        ("both unqualified, the same usings: one type", ("n", "Point", None, vis), ("n", "Point", None, vis), True),
        ("both unqualified, one scope's usings a subset of the other's: one type", ("n", "Point", None, vis),
         ("n", "Point", None, more), True),
        ("both unqualified, other usings: unknown", ("n", "Point", None, vis), ("n", "Point", None, wf), None),
        ("both unqualified, other namespace-block usings: unknown", ("n", "Timer", None, blk),
         ("n", "Timer", None, blk2), None),
        ("both unqualified, a scope the pass cannot read: unknown", ("n", "Point", None, None),
         ("n", "Point", None, vis), None),
        ("another simple name: unknown (either may be an alias)", ("n", "Point", None, vis), ("n", "Size", None, vis),
         None),
        ("a name against a project class of that name: unknown", ("n", "Cart", None, vis), ("c", "cart", "Cart"), None),
        ("... of another name: unknown (an alias the pass does not read)", ("n", "Point", None, vis),
         ("c", "cart", "Cart"), None),
        ("a name against a keyword: unknown", ("n", "I32", None, vis), ("b", "int"), None),
        ("a name against a type parameter: two types", ("n", "Point", None, vis), ("tpv", "k", "T"), False),
        ("two keywords: two types", ("b", "int"), ("b", "long"), False),
        ("one keyword: one type", ("b", "nint"), ("b", "nint"), True),
        ("a keyword against a project class: two types", ("b", "int"), ("c", "cart", "Cart"), False),
        ("two project classes: two types", ("c", "cart", "Cart"), ("c", "order", "Order"), False),
        ("int? against int: two types", ("?", ("b", "int"), True), ("b", "int"), False),
        ("int? against int?: one type", ("?", ("b", "int"), True), ("?", ("b", "int"), True), True),
        ("int? against a Nullable<int> not proven System's (an implicit using): unknown", ("?", ("b", "int"), True),
         ("g", ("n", "Nullable", None, wf), ("b", "int")), None),
        ("... against a Nullable<long>: two types", ("?", ("b", "int"), True),
         ("g", ("n", "Nullable", None, wf), ("b", "long")), False),
        ("X? of an unknown kind against X: unknown", ("?", ("n", "Point", None, vis), False),
         ("n", "Point", None, vis), None),
        ("a generic's argument decides", ("g", ("c", "lst", "List"), ("tpv", "k", "T")),
         ("g", ("c", "lst", "List"), ("c", "cart", "Cart")), False),
        ("another generic arity: two types", ("g", ("c", "lst", "List"), ("b", "int")),
         ("g", ("c", "lst", "List"), ("b", "int"), ("b", "int")), False),
        ("another array rank: two types", ("[]", ("b", "int"), "[]"), ("[]", ("b", "int"), "[,]"), False),
        ("an unreadable type: unknown", ("x", "(int,string)"), ("b", "int"), None),
    ]


def sec_sigs(buckets) -> None:
    print("\n-- S: the fix round's shapes (signatures across open type parameters, override slots and interface "
          "mappings by signature, rebinding a constructed local, two external types of one simple name), on the typed "
          "keys AND the pushed edges (both engines, both modes) --")
    n_cs = len([r for r in SIGS if r.endswith(".cs")])
    for b in buckets:
        for mode in MODES:
            run = run_for(b, "sigs", mode)
            st = run.stats
            check(f"S {b}/sigs [{mode}]: every C# file read ({n_cs}), none failed, no error",
                  st["error"] is None and st["files_failed"] == 0 and st["files"] == n_cs,
                  (st["error"], st["files_failed"], st["files"]))
            got = typed_view(run.nodes, form="name")
            diff = {k: (got.get(k), S_EXPECT.get(k)) for k in sorted(set(got) | set(S_EXPECT))
                    if got.get(k) != S_EXPECT.get(k)}
            check(f"S {b}/sigs [{mode}]: the typed keys and grades are exactly Roslyn's bindings and dispatch "
                  f"({len(S_EXPECT)} callers)", not diff, diff)
            name_of = {n["key"]: n.get("name") for n in run.nodes}
            for caller, member, teeth in S_MUSTNOT:
                node = node_of(run.nodes, caller)
                if node is None:
                    check(f"S {b} [{mode}] {caller}: the caller is a node", False)
                    continue
                typed = [t for t in (node.get("call_typed") or {}) if tc().name_seg(name_of.get(t, "")) == member]
                pre, post = tuples_of(run, run.before, caller, member), tuples_of(run, run.out, caller, member)
                lost = [t for t in pre if t not in post]
                check(f"S {b} [{mode}] {caller.split('::')[-1]} `{member}`: no typed key, no removal (the engine "
                      f"has {len(pre)} tuple(s) of it)", bool(pre) == teeth and not typed and not lost,
                      (pre, typed, lost))
            edges = push_edges(run, root_for("sigs"), MODES[mode])
            r0 = run_pipeline(captured(b, "sigs")["graph"], root_for("sigs"), dict(MODES[mode], DEEPMAP_TYPED_CALLS="0"))
            off = push_edges(r0, root_for("sigs"), dict(MODES[mode], DEEPMAP_TYPED_CALLS="0"))
            for caller, target in S_NO_EDGE:
                ck, tk = key_of(run.nodes, caller), key_of(run.nodes, target)
                check(f"S {b} [{mode}] {caller.split('::')[-1]}: no pushed edge to {target.split('::')[-1]} (typed "
                      f"linking off: {off.get((ck, tk))})",
                      ck is not None and tk is not None and (ck, tk) not in edges and (ck, tk) in off,
                      (ck, tk, edges.get((ck, tk)), off.get((ck, tk))))
            for caller, target, grade in S_EDGE:
                ck, tk = key_of(run.nodes, caller), key_of(run.nodes, target)
                g = edges.get((ck, tk))
                check(f"S {b} [{mode}] {caller.split('::')[-1]} -> {target.split('::')[-1]}: pushed "
                      f"{grade or 'kept'}{'' if grade else ' (the engine has it: ' + str(off.get((ck, tk))) + ')'}",
                      g is not None and (grade is not None and g == grade or grade is None and (ck, tk) in off), g)
            ov = (st["by_lang"].get("csharp") or {}).get("overloads", {})
            check(f"S {b} [{mode}]: the overload rule saw rival sites, some fitting the hit and some unknown",
                  ov.get("sites", 0) >= 6 and ov.get("fit", 0) >= 3 and ov.get("unknown", 0) >= 3, ov)
    if len(buckets) == 2:
        for mode in MODES:
            a = typed_view(run_for(buckets[0], "sigs", mode).nodes)
            z = typed_view(run_for(buckets[1], "sigs", mode).nodes)
            diff = {k: (a.get(k), z.get(k)) for k in sorted(set(a) | set(z)) if a.get(k) != z.get(k)}
            check(f"S sigs [{mode}]: identical typed tuples on version-stable keys, 0.8.39 vs 0.8.51",
                  not diff and bool(a), diff)
    # the comparison itself, on normalised types (typed_calls._cs_tcmp)
    fn = getattr(tc(), "_cs_tcmp", None)
    if fn is None:
        check("S typed_calls._cs_tcmp exists (the three-valued type comparison)", False)
    else:
        bad = [(lab, fn(x, y), want) for lab, x, y, want in _s_tcmp_table() if fn(x, y) is not want
               or fn(y, x) is not want]
        check(f"S _cs_tcmp: {len(_s_tcmp_table())} pairs compare as the C# rules say (both orders)", not bad, bad)
    # the reader's facts: what a scope's span may rebind
    if rcs() is None:
        return
    rd = rcs().make_reader(tc().LANGUAGES["csharp"])
    ff = rd.facts(S_LOC, Src(SIGS))
    if ff is None:
        check("S the reader gives facts for Locals/Locals.cs", False)
        return
    rea = (getattr(ff, "_cs", None) or {}).get("reassigned") or []
    by = {s.name: set(rea[i]) if i < len(rea) else None for i, s in enumerate(ff.scopes)}
    want = {"Deconstruct": True, "DeconstructVar": True, "RefLocal": True, "Nested": True, "Increment": True,
            "Fresh": False}
    got = {nm: ("b" in (by.get(nm) or set())) for nm in want}
    check("S reader: `b` is rebound by `(b, y) = ...`, `((b, y), z) = ...`, `ref Repo rb = ref b; rb = ...` and "
          "`b++`, never by `(y, z) = ...` (which rebinds y and z)",
          got == want and {"y", "z"} <= (by.get("Fresh") or set()) and "rb" in (by.get("RefLocal") or set()),
          {k: sorted(v) if v is not None else None for k, v in by.items()})
    snippet = """class U
{
    void M(Repo a, Repo b, Repo c, bool k)
    {
        (a, (b, c)) = (null, (null, null));
        F(ref (a));
        ref Repo r = ref (k ? ref b : ref c);
        this.a = null;
        arr[0] = null;
    }
}
"""
    fs = rd.facts("src/S.cs", Src({"src/S.cs": snippet}))
    rr = (getattr(fs, "_cs", None) or {}).get("reassigned") or [[]]
    check("S reader: a nested deconstruction, a parenthesized ref argument and a ref conditional rebind every name "
          "they write; a member / element access rebinds no local",
          fs is not None and {"a", "b", "c"} <= set(rr[0]) and not {"arr", "k", "r"} & set(rr[0]), rr)


# ================================================================================================
# N: fix round 2 (tests TREES["rnd2"]): a class type parameter's `T?` by its constraint, one type of several
# spellings (nint / IntPtr, Nullable<int> / int?, `global using` aliases, aliases the pass cannot read), a private
# middle method, an override among the candidates, a generic subclass of other type arguments, external names by the
# scope's usings — on the typed keys AND the pushed :CALLS edges
# ================================================================================================

N2 = "src/R2"
N_CV, N_MP, N_SL, N_OV = f"{N2}/Conv/Converters.cs", f"{N2}/Maps/Repos.cs", f"{N2}/Slots/Slots.cs", f"{N2}/Ovl/Mids.cs"
N_GN, N_TD, N_TS, N_BL = f"{N2}/Gen/Boxes.cs", f"{N2}/Ext/TimerDer.cs", f"{N2}/Ext/TimerSub.cs", f"{N2}/Ext/Blocks.cs"
N_BL2, N_GF, N_NU = f"{N2}/Ext/Blocks2.cs", f"{N2}/Gfx/Gfx.cs", f"{N2}/NoUsing/Drawers.cs"
N_SD, N_AL, N_GU = "src/Sg2/Drawers.cs", "src/Sg3/Aliases.cs", f"{N2}/GlobalUsings.cs"
N_NS, N_LO, N_PB = f"{N2}/Nest/Outer.cs", f"{N2}/Locals/Locals.cs", f"{N2}/Pb/Shop.cs"
N_TK, N_UN = f"{N2}/Ext/TokDer.cs", f"{N2}/Ext/Unity.cs"
_N_REBOUND = {_m(N_LO, "Repo"): E, _m(N_LO, "CachedRepo"): E, _m(N_LO, "Repo.Keep"): I,
              _m(N_LO, "CachedRepo.Keep"): I}


def _n_conv(target):
    return {_m(N_CV, "Converter.Format"): I, _m(N_CV, target): I}


# {caller: {target: grade}}: EXACTLY these callers of the tree are keyed, each target the binding Roslyn 4.8 gives the
# call with its run-time dispatch (AC1); a never-rebound construction is exact (DeepMap's documented narrowing).
# Every other caller of the tree is keyed NOTHING (N_MUSTNOT: MidUser.Go, TUserE1.Go, TUserE2.Go / Go3, TUserE5.Go,
# DrawUser3.DrawIt, SUser.Go, UseUser.Go).
N_EXPECT = {
    # N1: `T?` of an unconstrained T closed by int / a struct / an enum is T: the override is the slot's; a closing
    # by string / a class keeps its own override; `where T : struct` makes it Nullable<T> (IntParser.Parse(int?)).
    # R2: only the subclass closing the receiver's own type argument runs (no fan-out to the other converters)
    _m(N_CV, "ConvUser.A"): _n_conv("IntConverter.Format"),
    _m(N_CV, "ConvUser.B"): _n_conv("MoneyConverter.Format"),
    _m(N_CV, "ConvUser.C"): _n_conv("ColorConverter.Format"),
    _m(N_CV, "ConvUser.D"): _n_conv("StringConverter.Format"),
    _m(N_CV, "ConvUser.E"): _n_conv("OrderConverter.Format"),
    _m(N_CV, "ConvUser.F"): {_m(N_CV, "Parser.Parse"): I, _m(N_CV, "IntParser.Parse"): I},
    # `where T : U`: T's kind unknown, so `T?` is T or Nullable<T>: the slot / the implementer unknown (nothing
    # removed, PairBase's Set2(int?) never picked)
    _m(N_CV, "ConvUser.G"): {_m(N_CV, "Pair.Set"): I},
    _m(N_CV, "ConvUser.H"): {_m(N_CV, "IPair.Set2"): I},
    # N1 / N2 in an interface mapping: the implementer's own definition, never the base's other-typed one
    _m(N_MP, "MapUser.Go1"): {_m(N_MP, "IRepoM1.PutM1"): I, _m(N_MP, "PImplM1.PutM1"): I},
    _m(N_MP, "MapUser.Go2"): {_m(N_MP, "IStoreM2.PutM2"): I, _m(N_MP, "SImplM2.PutM2"): I},
    _m(N_MP, "MapUser.Go3"): {_m(N_MP, "IPtrM3.UseM3"): I, _m(N_MP, "PtrImplM3.UseM3"): I},
    # N2: nint / IntPtr, int? / Nullable<int>, a global alias of CancellationToken, of a project class and of IntPtr
    _m(N_SL, "SlotUser.Go1"): {_m(N_SL, "A5.Run5"): I, _m(N_SL, "B5.Run5"): I},
    _m(N_SL, "SlotUser.Go2"): {_m(N_SL, "A5.Open5"): I, _m(N_SL, "B5.Open5"): I},
    _m(N_SL, "SlotUser.Go3"): {_m(N_SL, "A5.Save6"): I, _m(N_SL, "B5.Save6"): I},
    _m(N_SL, "SlotUser.Go4"): {_m(N_SL, "A5.Keep6"): I, _m(N_SL, "B5.Keep6"): I},
    _m(N_SL, "SlotUser.Go5"): {_m(N_SL, "IStore6.Put6"): I, _m(N_SL, "StoreBase6.Put6"): A,
                               _m(N_SL, "FastStore6.Put6"): A},
    _m(N_SL, "SlotUser.Go6"): {_m(N_SL, "A5.Use6"): I, _m(N_SL, "B5.Use6"): I},
    # R4: a private middle method is no slot (C2.Run2 overrides A2's); a `new virtual` / an `internal new virtual` of
    # the same project starts another one
    _m(N_SL, "SlotUser.Go7"): {_m(N_SL, "A2.Run2"): I, _m(N_SL, "C2.Run2"): I},
    _m(N_SL, "SlotUser.Go8"): {_m(N_SL, "A2.Run3"): I},
    _m(N_SL, "SlotUser.Go9"): {_m(N_SL, "A2.Run4"): I},
    # an overload Open7(int) in the middle neither breaks nor continues Open7(int?)'s slot; `int[]?` is `int[]`
    _m(N_SL, "SlotUser.Go10"): {_m(N_SL, "A7.Open7"): I, _m(N_SL, "C7.Open7"): I},
    _m(N_SL, "SlotUser.Go11"): {_m(N_SL, "A7.Fill7"): I, _m(N_SL, "B7.Fill7"): I},
    # an outer class's type parameter no base list fixes: the slot unknown (the declared member only, SubI's tuple
    # kept); a member of a base interface reached through a derived one
    _m(N_NS, "NestUser.Go"): {_m(N_NS, "BaseI.Put"): I},
    _m(N_NS, "NestUser.Go2"): {_m(N_NS, "BaseI.Put2"): I},
    _m(N_NS, "IfaceUser.Go"): {_m(N_NS, "IBase7.Run7"): I, _m(N_NS, "Impl7.Run7"): I},
    # a constructed local rebound through `(b) = `, `Swap(ref (b))`, `ref Repo r = ref (b)`: dispatch; never rebound:
    # exact
    _m(N_LO, "LocalUser.Paren"): _N_REBOUND,
    _m(N_LO, "LocalUser.RefParen"): {_m(N_LO, "Repo"): E, _m(N_LO, "Repo.Keep"): I, _m(N_LO, "CachedRepo.Keep"): I,
                                     _m(N_LO, "LocalUser.Swap"): E},
    _m(N_LO, "LocalUser.RefAlias"): _N_REBOUND,
    _m(N_LO, "LocalUser.Fresh"): {_m(N_LO, "Repo"): E, _m(N_LO, "Repo.Keep"): E},
    # two project classes of one simple name: Keep(Order) hides Keep(Pb.Order), never Save(Pa.Order)
    _m(N_PB, "SUser.Go2"): {_m(N_PB, "SDer.Keep"): E},
    _m(N_UN, "UseUser.Go2"): {_m(N_UN, "UseDer2.Use"): E},       # System.Object is object: Use(object) hidden
    # R3: a non-override hit, and an override whose rival sits above its slot's declarer, bind as before
    _m(N_OV, "MidUser.Go9"): {_m(N_OV, "Der7.M9"): I},
    _m(N_OV, "MidUser.Go10"): {_m(N_OV, "Der10.M10"): I},
    # R2: VO6 (a VB6<Cart>) is never a VB6<Order>
    _m(N_GN, "GenUser.Go"): {_m(N_GN, "VB6.R6"): I, _m(N_GN, "VS6.R6"): I},
    # R1: one simple name under the same usings (a subset of them, a namespace block's alike) hides the base's; a
    # namespace the project's `global using` imports; an enclosing namespace's member
    _m(N_TS, "TUserE7.Go"): {_m(N_TS, "TDerE7.ArmE7"): E},
    # ... and two files of other usings that both import System.Threading: CancellationToken is that one type
    _m(N_TK, "TokUser.Go"): {_m(f"{N2}/Ext/TokBase.cs", "TokBase.Run"): I, _m(N_TK, "TokDer.Run"): I},
    _m(N_TK, "TokUser.Go2"): {_m(N_TK, "TokDer.Hide"): E},
    _m(N_BL, "TUserE4.Go"): {_m(N_BL, "TDerE4.ArmE2"): E},
    _m(N_BL, "TUserE4.Go3"): {_m(N_BL, "TDerE4.ArmE3"): E},
    _m(N_SD, "DrawUser.DrawIt"): {_m(N_SD, "GDrawer.Draw"): E},
    _m(N_GF, "DrawUser2.DrawIt"): {_m(N_GF, "GDrawer2.Draw"): E},
    # N2: an alias an MSBuild <Using> item declares: the slot unknown (the declared member only, nothing removed)
    _m(N_AL, "AliasUser.Go"): {_m(N_AL, "A3.Run3"): I},
    _m(N_AL, "AliasUser.Go2"): {_m(N_AL, "A3.Num3"): I},
    _m(N_AL, "AliasUser.Go4"): {_m(N_AL, "A4.Run4"): I},       # B4's `new virtual Run4(Tk2)`: the slot unknown
    _m(N_AL, "AliasUser.Go5"): {_m(N_AL, "IRun5.Run5"): I},    # Q5's Run5(Tk2): the implementer unknown
    _m(N_AL, "AliasUser.Go9"): {_m(N_AL, "IOv9.Put9"): I},     # IOv9's Put9(Tk2): its implementer unknown
}

# (caller, member, engine tuple?): no typed key of the member AND no removal of its tuples
N_MUSTNOT = [
    (_m(N_OV, "MidUser.Go"), "M7", True),          # R3: Roslyn Mid7.M7(double), not Der7's override
    (_m(N_TD, "TUserE1.Go"), "ArmE1", True),       # R1 E1: another file's `using System.Timers` (Roslyn TBaseE1)
    (_m(N_BL, "TUserE2.Go"), "ArmE2", True),       # R1 E2: another namespace block's using (Roslyn TBaseE2)
    (_m(N_BL, "TUserE2.Go3"), "ArmE3", True),      # R1 E3: qualified vs this block's System.Timers
    (_m(N_BL2, "TUserE5.Go"), "ArmE3", True),      # R1: the block's System.Timers before the file's System.Threading
    (_m(N_NU, "DrawUser3.DrawIt"), "Draw", True),  # another .csproj's `global using System.Drawing` is never seen
    (_m(N_PB, "SUser.Go"), "Save", True),           # Pb.Order is no Pa.Order (Roslyn SBase.Save)
    (_m(N_UN, "UseUser.Go"), "Use", True),          # UnityEngine.Object is no `object`
]

# (caller, target): after the WHOLE push the caller has NO :CALLS edge to target, while the push with typed linking
# off has one (the engine's tuple: teeth)
N_NO_EDGE = [
    (_m(N_SL, "SlotUser.Go8"), _m(N_SL, "C2.Run3")),   # an override of B2's `new virtual Run3`: another slot
    (_m(N_SL, "SlotUser.Go9"), _m(N_SL, "C2.Run4")),   # ... of B2's `internal new virtual Run4` (one project)
    (_m(N_LO, "LocalUser.Fresh"), _m(N_LO, "CachedRepo.Keep")),  # `Repo b = new Repo()`, never rebound
]

# (caller, target): no pushed edge either way (the false typed edges round 1 gave)
N_ABSENT = [
    (_m(N_MP, "MapUser.Go1"), _m(N_MP, "PBaseM1.PutM1")),        # N1: PutM1(int?) implements nothing
    (_m(N_MP, "MapUser.Go2"), _m(N_MP, "SBaseM2.PutM2")),        # N2: the global alias Tk is CancellationToken
    (_m(N_MP, "MapUser.Go3"), _m(N_MP, "PtrBaseM3.UseM3")),      # N2: IntPtr is nint
    (_m(N_CV, "ConvUser.A"), _m(N_CV, "StringConverter.Format")),  # R2: never a Converter<int>
    (_m(N_CV, "ConvUser.B"), _m(N_CV, "StringConverter.Format")),
    (_m(N_CV, "ConvUser.C"), _m(N_CV, "StringConverter.Format")),
    (_m(N_AL, "AliasUser.Go5"), _m(N_AL, "P5.Run5")),            # P5.Run5 is no implementer of IRun5 for Q5
    (_m(N_CV, "ConvUser.H"), _m(N_CV, "PairBase.Set2")),          # Set2(int?) is no Set2(T?) of T : U, T = int
]

# (caller, target, grade): pushed edges that must stay (None: any grade — the engine's own true tuple kept)
N_EDGE = [
    (_m(N_SL, "SlotUser.Go1"), _m(N_SL, "B5.Run5"), I),
    (_m(N_SL, "SlotUser.Go2"), _m(N_SL, "B5.Open5"), I),
    (_m(N_SL, "SlotUser.Go3"), _m(N_SL, "B5.Save6"), I),
    (_m(N_SL, "SlotUser.Go4"), _m(N_SL, "B5.Keep6"), I),
    (_m(N_SL, "SlotUser.Go5"), _m(N_SL, "FastStore6.Put6"), A),
    (_m(N_SL, "SlotUser.Go6"), _m(N_SL, "B5.Use6"), I),
    (_m(N_SL, "SlotUser.Go7"), _m(N_SL, "C2.Run2"), I),
    (_m(N_MP, "MapUser.Go1"), _m(N_MP, "PImplM1.PutM1"), I),
    (_m(N_MP, "MapUser.Go2"), _m(N_MP, "SImplM2.PutM2"), I),
    (_m(N_MP, "MapUser.Go3"), _m(N_MP, "PtrImplM3.UseM3"), I),
    (_m(N_CV, "ConvUser.A"), _m(N_CV, "IntConverter.Format"), I),
    (_m(N_CV, "ConvUser.B"), _m(N_CV, "MoneyConverter.Format"), I),
    (_m(N_CV, "ConvUser.C"), _m(N_CV, "ColorConverter.Format"), I),
    (_m(N_GN, "GenUser.Go"), _m(N_GN, "VS6.R6"), I),
    (_m(N_TK, "TokUser.Go"), _m(N_TK, "TokDer.Run"), I),
    (_m(N_AL, "AliasUser.Go"), _m(N_AL, "B3.Run3"), None),      # the engine's tuple to the override that runs kept
    (_m(N_AL, "AliasUser.Go2"), _m(N_AL, "B3.Num3"), None),
    (_m(N_AL, "AliasUser.Go5"), _m(N_AL, "Q5.Run5"), None),     # the implementer that runs (the engine's tuple)
    (_m(N_CV, "ConvUser.H"), _m(N_CV, "PairImpl.Set2"), None),
    (_m(N_AL, "AliasUser.Go9"), _m(N_AL, "Impl9.Put9"), None),
    (_m(N_NS, "NestUser.Go"), _m(N_NS, "SubI.Put"), None),       # SubI.Put overrides BaseI.Put (the engine's tuple)
    (_m(N_NS, "NestUser.Go2"), _m(N_NS, "SubI.Put2"), None),
    (_m(N_NS, "IfaceUser.Go"), _m(N_NS, "Impl7.Run7"), I),
    (_m(N_SL, "SlotUser.Go10"), _m(N_SL, "C7.Open7"), I),
    (_m(N_SL, "SlotUser.Go11"), _m(N_SL, "B7.Fill7"), I),
    (_m(N_LO, "LocalUser.Paren"), _m(N_LO, "CachedRepo.Keep"), I),
    (_m(N_LO, "LocalUser.RefParen"), _m(N_LO, "CachedRepo.Keep"), I),
    (_m(N_LO, "LocalUser.RefAlias"), _m(N_LO, "CachedRepo.Keep"), I),
]


def _n_env(mode) -> dict:
    """MODES[mode] with graphify_extract's C# members ON (the product default): 0.8.39 then gives the struct Money and
    the enum Color their owner node, as 0.8.51 does (with them off it gives none, and both stay unknown)."""
    return dict(MODES[mode], DEEPMAP_CSHARP_MEMBERS="1")


def _n_run(bucket, mode) -> Run:
    k = (bucket, "rnd2", mode, "members")
    if k not in _RUNS:
        _RUNS[k] = run_pipeline(captured(bucket, "rnd2")["graph"], root_for("rnd2"), _n_env(mode))
    return _RUNS[k]


def sec_rnd2(buckets) -> None:
    print("\n-- N: fix round 2's shapes (`T?` by its constraint, one type of several spellings, a private middle "
          "method, an override among the candidates, a generic subclass of other arguments, names by the scope's "
          "usings), on the typed keys AND the pushed edges (both engines, both modes) --")
    n_cs = len([r for r in RND2 if r.endswith(".cs")])
    for b in buckets:
        for mode in MODES:
            run = _n_run(b, mode)
            st = run.stats
            check(f"N {b}/rnd2 [{mode}]: every C# file read ({n_cs}), none failed, no error",
                  st["error"] is None and st["files_failed"] == 0 and st["files"] == n_cs,
                  (st["error"], st["files_failed"], st["files"]))
            got = typed_view(run.nodes, form="name")
            diff = {k: (got.get(k), N_EXPECT.get(k)) for k in sorted(set(got) | set(N_EXPECT))
                    if got.get(k) != N_EXPECT.get(k)}
            check(f"N {b}/rnd2 [{mode}]: the typed keys and grades are exactly Roslyn's bindings and dispatch "
                  f"({len(N_EXPECT)} callers)", not diff, diff)
            name_of = {n["key"]: n.get("name") for n in run.nodes}
            for caller, member, teeth in N_MUSTNOT:
                node = node_of(run.nodes, caller)
                if node is None:
                    check(f"N {b} [{mode}] {caller}: the caller is a node", False)
                    continue
                typed = [t for t in (node.get("call_typed") or {}) if tc().name_seg(name_of.get(t, "")) == member]
                pre, post = tuples_of(run, run.before, caller, member), tuples_of(run, run.out, caller, member)
                lost = [t for t in pre if t not in post]
                check(f"N {b} [{mode}] {caller.split('::')[-1]} `{member}`: no typed key, no removal (the engine "
                      f"has {len(pre)} tuple(s) of it)", bool(pre) == teeth and not typed and not lost,
                      (pre, typed, lost))
            edges = push_edges(run, root_for("rnd2"), _n_env(mode))
            r0 = run_pipeline(captured(b, "rnd2")["graph"], root_for("rnd2"), dict(_n_env(mode), DEEPMAP_TYPED_CALLS="0"))
            off = push_edges(r0, root_for("rnd2"), dict(_n_env(mode), DEEPMAP_TYPED_CALLS="0"))
            for caller, target in N_NO_EDGE:
                ck, tk = key_of(run.nodes, caller), key_of(run.nodes, target)
                check(f"N {b} [{mode}] {caller.split('::')[-1]}: no pushed edge to {target.split('::')[-1]} (typed "
                      f"linking off: {off.get((ck, tk))})",
                      ck is not None and tk is not None and (ck, tk) not in edges and (ck, tk) in off,
                      (ck, tk, edges.get((ck, tk)), off.get((ck, tk))))
            for caller, target in N_ABSENT:
                ck, tk = key_of(run.nodes, caller), key_of(run.nodes, target)
                check(f"N {b} [{mode}] {caller.split('::')[-1]}: no edge to {target.split('::')[-1]}, typed linking "
                      f"on or off", ck is not None and tk is not None and (ck, tk) not in edges and (ck, tk) not in off,
                      (ck, tk, edges.get((ck, tk)), off.get((ck, tk))))
            for caller, target, grade in N_EDGE:
                ck, tk = key_of(run.nodes, caller), key_of(run.nodes, target)
                g = edges.get((ck, tk))
                check(f"N {b} [{mode}] {caller.split('::')[-1]} -> {target.split('::')[-1]}: pushed "
                      f"{grade or 'kept'}{'' if grade else ' (the engine has it: ' + str(off.get((ck, tk))) + ')'}",
                      g is not None and (grade is not None and g == grade or grade is None and (ck, tk) in off), g)
            vs = (st["by_lang"].get("csharp") or {}).get("virtual", {})
            check(f"N {b} [{mode}]: the virtual rule counts the subclasses a closed generic receiver skips "
                  f"(closed_args)", vs.get("closed_args", 0) >= 1, vs)
    if len(buckets) == 2:
        for mode in MODES:
            a = typed_view(_n_run(buckets[0], mode).nodes)
            z = typed_view(_n_run(buckets[1], mode).nodes)
            diff = {k: (a.get(k), z.get(k)) for k in sorted(set(a) | set(z)) if a.get(k) != z.get(k)}
            check(f"N rnd2 [{mode}]: identical typed tuples on version-stable keys, 0.8.39 vs 0.8.51",
                  not diff and bool(a), diff)
    # the reader's facts the rules read: type parameter constraints, `global using` aliases, the usings per scope
    if rcs() is None:
        return
    rd = rcs().make_reader(tc().LANGUAGES["csharp"])

    def cs_of(rel, files):
        ff = rd.facts(rel, Src(files))
        return ff, (getattr(ff, "_cs", None) or {}) if ff is not None else {}
    ff, cs = cs_of(N_CV, RND2)
    ci = {c.name: i for i, c in enumerate(ff.classes)} if ff is not None else {}
    check("N reader: _cs tpkind: Converter<T> (no constraint) 'a', Parser<T> where T : struct 's'",
          cs.get("tpkind", {}).get(ci.get("Converter")) == {"T": "a"}
          and cs.get("tpkind", {}).get(ci.get("Parser")) == {"T": "s"}, cs.get("tpkind"))
    _f, cg = cs_of(N_GU, RND2)
    check("N reader: _cs galias: the file's `global using X = ...` aliases (name, target), in order",
          cg.get("galias") == [("Tk", "System.Threading.CancellationToken"), ("Ord", "R2.Slots.Order6"),
                               ("Ptr", "System.IntPtr")], cg.get("galias"))
    fb, cb = cs_of(N_BL, RND2)
    bi = {c.name: i for i, c in enumerate(fb.classes)} if fb is not None else {}
    check("N reader: _cs cvis: each class's namespace declarations with their usings (one block's, never another's)",
          cb.get("cvis", {}).get(bi.get("TBaseE2")) == (("", ()), ("R2.E2A", ("System.Threading",)))
          and cb.get("cvis", {}).get(bi.get("TDerE2")) == (("", ()), ("R2.E2B", ("System.Timers",)))
          and cb.get("cvis", {}).get(bi.get("TDerE4")) == (("", ()), ("R2.E2C", ("System.Threading",))),
          cb.get("cvis"))
    snippet = """using A.B;
global using G.H;
namespace N.M;
using C.D;
class K<T, U, V, W, X> where T : unmanaged where U : class, IFoo where V : W where W : notnull, new()
    where X : struct, System.Enum
{
    class Inner<Y> where Y : T
    {
    }
}
"""
    fs, cn = cs_of("src/K.cs", {"src/K.cs": snippet})
    ki = {c.name: i for i, c in enumerate(fs.classes)} if fs is not None else {}
    check("N reader: tpkind by constraint (unmanaged / struct 's', a type parameter '?', class / notnull / new() / "
          "a type 'a'), cvis after a file-scoped namespace (its usings follow it; a global using is no file level)",
          cn.get("tpkind", {}).get(ki.get("K")) == {"T": "s", "U": "a", "V": "?", "W": "a", "X": "s"}
          and cn.get("tpkind", {}).get(ki.get("Inner")) == {"Y": "?"}
          and cn.get("cvis", {}).get(ki.get("K")) == (("", ("A.B",)), ("N.M", ("C.D",)))
          and cn.get("cvis", {}).get(ki.get("Inner")) == (("", ("A.B",)), ("N.M", ("C.D",))),
          {k: cn.get(k) for k in ("tpkind", "cvis")})
    # the binder: a `global using` alias of the group names its target in every file of that group, before the
    # usings' namespaces (another group never sees it)
    b2 = {"src/P/P.csproj": CSPROJ, "src/P/G.cs": "global using Ord = P.M.Order;\n",
          "src/P/M/Order.cs": "namespace P.M;\npublic class Order\n{\n}\n",
          "src/P/U/Use.cs": "namespace P.U;\npublic class Use\n{\n}\n",
          "src/Q/Q.csproj": CSPROJ, "src/Q/Use.cs": "namespace Q;\npublic class Use2\n{\n}\n"}
    with tempfile.TemporaryDirectory(prefix="dm_tccs_b_") as td:
        write_tree(Path(td), b2)
        facts = {r: rd.facts(r, Src(b2)) for r in b2 if r.endswith(".cs")}
        ctx = type("Ctx", (), {"root": td, "lang": "csharp", "facts": facts})()
        bd = rd.binder(ctx)
        got = bd.resolve_free("src/P/U/Use.cs", "Ord")
        other = bd.resolve_free("src/Q/Use.cs", "Ord")
    check("N binder: a `global using Ord = P.M.Order;` resolves Ord to the class in another file of its project, and "
          "to nothing in another project",
          getattr(got, "kind", "") == "symbol" and getattr(got, "rel", "") == "src/P/M/Order.cs" and other is None,
          (got, other))


# ================================================================================================
# E: constructor and computed-property scopes -> graphify_extract's C# members (C5), synthetic and real
# ================================================================================================

def c5_nodes(nodes):
    """Synthetic C# member nodes shaped per C5: the FIRST constructor of CartTotals -> :Method `CartTotals.CartTotals`
    at the declaration's first (attribute) line; each computed property -> :Method `CartTotals.<Prop>` there;
    call_by_name False. Added only when the graph does not already hold them."""
    text = CQRS[TOT]
    have = {(n.get("source_file"), n.get("name")) for n in nodes}
    ctor = getattr(GX, "cs_ctor_name", None)
    ctor_name = ctor("CartTotals") if callable(ctor) else "CartTotals.CartTotals"
    want = [(ctor_name, _line_of(text, "[Inject]"), "cart totals: method"),
            ("CartTotals.ItemsCalculatedPrice", _line_of(text, "[JsonIgnore]"), "items calculated price: property"),
            ("CartTotals.Discount", _line_of(text, "public decimal Discount"), "discount: property")]
    out = []
    for name, line, summary in want:
        if (TOT, name) in have:
            continue
        out.append({"key": f"c5::{name}@{line}", "label": "Method", "name": name, "source_file": TOT,
                    "start_line": line, "body_summary": summary, "call_by_name": False})
    return out


C5_EXPECT = {
    _m(TOT, "CartTotals.CartTotals"): {_m(TOT, "OrderPricer.Warm"): E},
    _m(TOT, "CartTotals.ItemsCalculatedPrice"): {_m(TOT, "OrderPricer.Price"): E},
    _m(TOT, "CartTotals.Discount"): {_m(TOT, "OrderPricer.Price"): E},
}


def sec_members(buckets) -> None:
    print("\n-- E: constructor / computed-property scopes map to the C5 member nodes (synthetic, then real) --")
    for b in buckets:
        run = run_for(b, "cqrs", "default")
        got = {k: v for k, v in typed_view(run.nodes, form="name").items() if k.startswith(TOT)}
        check(f"E {b}: without the member nodes the constructor and accessor scopes map to nothing and emit nothing",
              not got, got)
        run = run_pipeline(captured(b, "cqrs")["graph"], root_for("cqrs"), MODES["default"], extra_nodes=c5_nodes)
        got = {k: v for k, v in typed_view(run.nodes, form="name").items() if k.startswith(TOT)}
        check(f"E {b}: with C5-shaped nodes, the FIRST constructor and each computed property's accessor scope are "
              f"typed callers (the second constructor has no node: nothing)", got == C5_EXPECT, got)
        rest = {k: v for k, v in typed_view(run.nodes, form="name").items() if not k.startswith(TOT)}
        check(f"E {b}: ...and every other caller is unchanged", rest == expect(b), {
            k: (rest.get(k), expect(b).get(k)) for k in set(rest) | set(expect(b)) if rest.get(k) != expect(b).get(k)})
    if not callable(getattr(GX, "cs_ctor_name", None)):
        print("  [SKIP] graphify_extract makes no C# member nodes yet (no cs_ctor_name): the real-node comparison "
              "waits for them")
        return
    for b in buckets:
        env = dict(MODES["default"])
        env.pop("DEEPMAP_CSHARP_MEMBERS")
        run = run_pipeline(captured(b, "cqrs")["graph"], root_for("cqrs"), dict(env, DEEPMAP_CSHARP_MEMBERS="1"))
        got = {k: v for k, v in typed_view(run.nodes, form="name").items() if k.startswith(TOT)}
        check(f"E {b}: graphify_extract's REAL C# member nodes give the same constructor / accessor callers",
              got == C5_EXPECT, got)
    sec_ctor_scopes(buckets)


def ctor_nodes(nodes):
    """Synthetic constructor nodes of the review tree's Carts.cs, placed per the product's constructor rule (C5 +
    CR-1): ONE `Owner.Owner` node per type, at its FIRST INSTANCE constructor (CartA: after a static one; CartB: the
    first of two), at the static constructor only when it is the sole one (CartC)."""
    text = REVIEW[R_CART]
    have = {(n.get("source_file"), n.get("name")) for n in nodes}
    want = [("CartA.CartA", _line_of(text, "public CartA()")), ("CartB.CartB", _line_of(text, "public CartB()")),
            ("CartC.CartC", _line_of(text, "static CartC()"))]
    return [{"key": f"c5::{name}@{line}", "label": "Method", "name": name, "source_file": R_CART, "start_line": line,
             "body_summary": "constructor: method", "call_by_name": False}
            for name, line in want if (R_CART, name) not in have]


# the first instance constructor's calls (CartA's DI constructor after its static one; CartB's first of two); the
# static constructor only when it is the type's sole one (CartC); the static CartA() and CartB(int) map to no node
CTOR_EXPECT = {_m(R_CART, "CartA.CartA"): {_m(R_CART, "Pricer.Price"): E},
               _m(R_CART, "CartB.CartB"): {_m(R_CART, "Pricer.Price"): E},
               _m(R_CART, "CartC.CartC"): {_m(R_CART, "Pricer.Boot"): E}}


def sec_ctor_scopes(buckets) -> None:
    for b in buckets:
        run = run_pipeline(captured(b, "review")["graph"], root_for("review"), MODES["default"],
                           extra_nodes=ctor_nodes)
        got = {k: v for k, v in typed_view(run.nodes, form="name").items() if k.startswith(R_CART)}
        check(f"E {b}: constructor scopes map exactly to the product's one constructor node per type (static then "
              f"instance -> the instance one; two instance -> the first; static only -> it); nothing else is typed "
              f"from a constructor", got == CTOR_EXPECT, got)
        linked = {t.split("::")[-1] for c, tg in typed_view(run.nodes, form="name").items() if c.startswith(R_CART)
                  for t in tg}
        check(f"E {b}: the static constructor beside an instance one and the second instance constructor link "
              f"nothing (Pricer.Touch, Pricer.Warm)", not ({"Pricer.Touch", "Pricer.Warm"} & linked), linked)
    for b in buckets:
        env = dict(MODES["default"], DEEPMAP_CSHARP_MEMBERS="1")
        run = run_pipeline(captured(b, "review")["graph"], root_for("review"), env)
        real = {n.get("name"): n.get("start_line") for n in run.nodes
                if n.get("source_file") == R_CART and n.get("name") in ("CartA.CartA", "CartB.CartB", "CartC.CartC")}
        if real.get("CartA.CartA") == _line_of(REVIEW[R_CART], "static CartA()"):
            print(f"  [SKIP] E {b}: graphify_extract still puts CartA's constructor node at its STATIC constructor "
                  f"(the first in source order): the real-node lock waits for its first-instance rule")
            continue
        got = {k: v for k, v in typed_view(run.nodes, form="name").items() if k.startswith(R_CART)}
        check(f"E {b}: graphify_extract's REAL constructor nodes give the same constructor callers", got == CTOR_EXPECT,
              (got, real))


# ================================================================================================
# F: engine parity
# ================================================================================================

def sec_parity(buckets) -> None:
    print("\n-- F: engine parity (records allowlisted) --")
    if len(buckets) != 2:
        check("F both engines' captures are present (tests/fixtures/typed_calls/{0839,0851}/cs)", False, buckets)
        return
    for mode in MODES:
        a = typed_view(run_for(buckets[0], "cqrs", mode).nodes)
        b = typed_view(run_for(buckets[1], "cqrs", mode).nodes)
        allowed = lambda k: f"@{OLINE}:" in k    # noqa: E731
        diff = {k: (a.get(k), b.get(k)) for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k) and not allowed(k)}
        check(f"F cqrs [{mode}]: identical typed tuples on version-stable keys, 0.8.39 vs 0.8.51", not diff, diff)
        recs = {k for k in set(a) | set(b) if allowed(k)}
        check(f"F cqrs [{mode}]: the allowlist covers only the record's caller (its owner differs on 0.8.39)",
              len(recs) <= 2 and all(OLINE in k for k in recs), recs)
        a = typed_view(run_for(buckets[0], "review", mode).nodes)
        b = typed_view(run_for(buckets[1], "review", mode).nodes)
        struct = lambda k: f"@{R_PAN}:" in k and ("Show@" in k)    # noqa: E731 — the struct's owner (0.8.39)
        diff = {k: (a.get(k), b.get(k)) for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k) and not struct(k)}
        check(f"F review [{mode}]: identical typed tuples on version-stable keys, 0.8.39 vs 0.8.51 (only the struct's "
              f"caller allowlisted)", not diff and bool(a), diff)
        sk = {k for k in set(a) | set(b) if struct(k)}
        check(f"F review [{mode}]: the allowlist covers only the struct's one caller (its owner differs on 0.8.39)",
              len(sk) == 2, sk)


# ================================================================================================
# G: flags
# ================================================================================================

def _cs_untouched(run) -> list:
    return [n["key"] for n in run.nodes if str(n.get("source_file", "")).endswith(".cs")
            and (n.get("call_typed") or n.get("call_weak"))]


def sec_flags(buckets) -> None:
    print("\n-- G: flags --")
    if not buckets:
        return
    b = buckets[0]
    g = captured(b, "cqrs")["graph"]
    for val in ("0", "1", "python"):
        run = run_pipeline(g, root_for("cqrs"), dict(_OFF, DEEPMAP_TYPED_CALLS=val))
        check(f"G DEEPMAP_TYPED_CALLS={val}: the SAME list object, no C# caller keyed",
              run.out is run.calls_in and not _cs_untouched(run) and "csharp" not in run.stats["by_lang"],
              (run.out is run.calls_in, _cs_untouched(run)[:3], sorted(run.stats["by_lang"])))
    run = run_pipeline(g, root_for("cqrs"), MODES["default"])
    py = [n for n in run.nodes if str(n.get("source_file", "")).endswith(".py")]
    same = [(c, str(t)) for c, t in run.out if any(c == n["key"] for n in py)] == \
        [(c, str(t)) for c, t in run.before if any(c == n["key"] for n in py)]
    check("G DEEPMAP_TYPED_CALLS=csharp: the .py callers are untouched (no attribute, the same tuples)",
          py and same and not any(n.get("call_typed") or n.get("call_weak") or n.get("call_lines") for n in py)
          and "python" not in run.stats["by_lang"], (len(py), same))
    every = expect(b)[_m(NOT, "NotifyHandler.Handle")]
    for iface, want, unknown in (("static", {_m(NOT, "INotifier.Notify"): I}, []),
                                 ("impl", {_m(NOT, "EmailNotifier.Notify"): A, _m(NOT, "SmsNotifier.Notify"): A}, []),
                                 # CR-3: an ON word selects every mode, like unset; an unknown token is ignored and
                                 # reported, never silently selecting nothing; an OFF word still turns dispatch off
                                 ("1", every, []), ("on", every, []), (" TRUE ", every, []), ("yes", every, []),
                                 ("statc", every, ["statc"]), ("static,statc", {_m(NOT, "INotifier.Notify"): I},
                                                               ["statc"]),
                                 ("0", None, []), ("off", None, [])):
        run = run_pipeline(g, root_for("cqrs"), dict(MODES["default"], DEEPMAP_TYPED_CALLS_IFACE=iface))
        got = typed_view(run.nodes, form="name").get(_m(NOT, "NotifyHandler.Handle"))
        check(f"G DEEPMAP_TYPED_CALLS_IFACE={iface!r}: NotifyHandler.Handle -> {sorted(want or {})}, unknown_iface "
              f"{unknown}", got == want and run.stats.get("unknown_iface") == unknown,
              (got, run.stats.get("unknown_iface")))
    # the same knob governs virtual dispatch (the review tree): static = the declared member only, impl = the
    # overrides only (the declared member is then no typed key and the engine's tuple stays), 0 = neither
    rv = captured(b, "review")["graph"]
    caller = _m(R_HB, "HandlerBase.Run")
    for iface, want in (("static", {_m(R_HB, "HandlerBase.HandleCore"): I}),
                        ("impl", {_m(R_OH, "OrderHandler.HandleCore"): I}), ("0", None)):
        run = run_pipeline(rv, root_for("review"), dict(MODES["default"], DEEPMAP_TYPED_CALLS_IFACE=iface))
        got = typed_view(run.nodes, form="name").get(caller)
        pre, post = tuples_of(run, run.before, caller, "HandleCore"), tuples_of(run, run.out, caller, "HandleCore")
        keeps = iface == "static" or not [t for t in pre if t not in post]
        check(f"G DEEPMAP_TYPED_CALLS_IFACE={iface} on virtual dispatch: HandlerBase.Run -> {sorted(want or {})}"
              + ("" if iface == "static" else ", the engine's tuple kept"), got == want and keeps, (got, pre, post))
    if rcs() is None:
        return
    saved = sys.modules.get("tree_sitter_c_sharp", "absent")
    try:
        sys.modules["tree_sitter_c_sharp"] = None          # `import tree_sitter_c_sharp` raises ImportError
        getattr(rcs(), "_LANGS", {}).clear()
        run = run_pipeline(g, root_for("cqrs"), MODES["default"])
        check("G no C# grammar: the canary fails cleanly, only csharp is disabled, the SAME list object, no error",
              run.stats["langs_disabled"] == {"csharp": "canary"} and run.out is run.calls_in
              and run.stats["error"] is None and not _cs_untouched(run), (run.stats["langs_disabled"],
                                                                          run.stats["error"]))
    finally:
        if saved == "absent":
            sys.modules.pop("tree_sitter_c_sharp", None)
        else:
            sys.modules["tree_sitter_c_sharp"] = saved
        getattr(rcs(), "_LANGS", {}).clear()


# ================================================================================================
# H: call_lines
# ================================================================================================

def _first_call_line(text: str, start: int, member: str, own: str = "") -> int:
    """The first line at or after `start` of `text` with a call-shaped `member` (`m(`, `m<..>(`, `new m(`); when
    `member` is the caller's own name `own`, its declaration (the first match) is skipped."""
    rx = re.compile(r"(?<![\w$])" + re.escape(member) + r"\s*(?:<[^<>()]*>)?\s*\(")
    skip = member == own
    for i, ln in enumerate(text.split("\n")[start - 1:], start):
        for _mt in rx.finditer(ln):
            if skip:
                skip = False
                continue
            return i
    return -1


def sec_call_lines(buckets) -> None:
    print("\n-- H: call_lines: the first call line of every typed key; merged with a line already there --")
    for b in buckets:
        run = run_for(b, "cqrs", "default")
        by = {n["key"]: n for n in run.nodes}
        bad = []
        for n in run.nodes:
            for t in n.get("call_typed") or {}:
                got = ((n.get("call_lines") or {}).get("key") or {}).get(t)
                tn = by[t]
                member = TC.name_seg(tn.get("name"))
                want = _first_call_line(CQRS[n["source_file"]].replace("\r\n", "\n"), int(n["start_line"]), member,
                                        TC.name_seg(n.get("name")))
                if not (isinstance(got, int) and not isinstance(got, bool) and got == want):
                    bad.append((ident(n), ident(tn), got, want))
        check(f"H {b}: every typed key of every caller has call_lines['key'][key] = its first call line", not bad,
              bad[:6])
        h = node_of(run.nodes, _m(COH, "CreateOrderHandler.Handle"))
        lines = (h or {}).get("call_lines", {}).get("key", {})
        text = CQRS[COH]
        want = {_m(ISCH, "IShoppingCartHelper.CreateShoppingCartAsync"): _line_of(text, "await _cartHelper."),
                _m(SCH, "ShoppingCartHelper.CreateShoppingCartAsync"): _line_of(text, "await _cartHelper."),
                _m(COHP, "CreateOrderHandler.Persist"): _line_of(text, "this.Persist"),
                _m(IREPO, "OrderRepository.Get"): _line_of(text, "_repo.Get<Order>")}
        got = {ident(by[t]): v for t, v in lines.items()}
        check(f"H {b}: CreateOrderHandler.Handle's lines are the call sites (an implementer carries the site of the "
              f"interface call), never the caller's first line", all(got.get(k) == v for k, v in want.items())
              and h is not None and h["start_line"] not in got.values(), (got, want))
        w = node_of(run.nodes, _m(WIN, "Windowsy.Run"))
        wl = list(((w or {}).get("call_lines") or {}).get("key", {}).values())
        check(f"H {b}: a BOM + CRLF file's call line is numbered like the engine", wl == [11], wl)

        # merged with a line already there: the minimum, and the engine's "name" entries kept
        def pre(nodes):
            hn = next(n for n in nodes if ident(n) == _m(SHIP, "ShipOrderHandler.Handle"))
            ship = next(n["key"] for n in nodes if ident(n) == _m(OSVC, "OrderService.Ship"))
            gen = next(n["key"] for n in nodes if ident(n) == _m(SHIP, "ShipOrderHandler.Gen"))
            hn["call_lines"] = {"key": {ship: 2, gen: 9999}, "name": {"Validate": 15}}
        run2 = run_pipeline(captured(b, "cqrs")["graph"], root_for("cqrs"), MODES["default"], prelink=pre)
        hn = node_of(run2.nodes, _m(SHIP, "ShipOrderHandler.Handle"))
        cl = (hn or {}).get("call_lines") or {}
        k2 = {ident(by2): v for by2 in run2.nodes for t, v in cl.get("key", {}).items() if by2["key"] == t}
        check(f"H {b}: a line already there is kept when smaller, replaced when larger, the 'name' entries kept",
              k2.get(_m(OSVC, "OrderService.Ship")) == 2
              and k2.get(_m(SHIP, "ShipOrderHandler.Gen")) == _line_of(CQRS[SHIP], "Gen<int>();")
              and cl.get("name") == {"Validate": 15}, cl)

    # rolled back on an error: a node that refuses call_lines -> the input list, no attribute left behind
    class Refuse(dict):
        def __setitem__(self, k, v):
            if k == "call_lines":
                raise RuntimeError("refused")
            super().__setitem__(k, v)

    if buckets:
        def refuse(nodes):
            for i, n in enumerate(nodes):
                if ident(n) == _m(WIN, "Windowsy.Run"):
                    nodes[i] = Refuse(n)
        run = run_pipeline(captured(buckets[0], "cqrs")["graph"], root_for("cqrs"), MODES["default"], prelink=refuse)
        left = [ident(n) for n in run.nodes if n.get("call_typed") or n.get("call_lines")]
        check("H an error while setting call_lines: the INPUT list back, every attribute rolled back, stats error",
              run.out is run.calls_in and not left and run.stats["error"], (left[:3], run.stats["error"]))


# ================================================================================================
# R: the installed engine == its fixture
# ================================================================================================

def sec_real_engine() -> None:
    print("\n-- R: the installed engine's real extraction of every tree == its captured fixture --")
    if not _HAS_ENGINE:
        print("  [SKIP] graphify is not installed")
        return
    ver = GX.graphify_version()
    b = BUCKETS.get(ver)
    print(f"  (graphify {ver})")
    if b is None:
        print(f"  (no capture for graphify {ver}: only 0.8.39 and 0.8.51 are captured; comparison skipped)")
        return
    real = real_graphs()
    for name in TREES:
        fx = captured(b, name)
        if real.get(name) is None or fx is None:
            check(f"R {name}: the engine produced a graph and the fixture exists", False)
            continue
        check(f"R {name}: the fixture was captured from THIS tree (re-capture after editing it)",
              fx.get("tree_sha256") == tree_sha(name))
        bad = []
        for mode in MODES:
            a = run_pipeline(real[name], root_for(name), MODES[mode])
            f = run_pipeline(fx["graph"], root_for(name), MODES[mode])
            if typed_view(a.nodes) != typed_view(f.nodes) or \
                    sorted((str(c), str(t)) for c, t in a.out) != sorted((str(c), str(t)) for c, t in f.out):
                bad.append(mode)
        check(f"R {name}: same typed keys and CALLS as the {ver} fixture (default and PRECISE)", not bad, bad)


# ================================================================================================
# M: mutation teeth (each reader / binder rule broken in a copy of the module -> its checks go RED)
# ================================================================================================

_RF = "receiver_facts_csharp"
_TC = "typed_calls"
MUTANTS = [
    # (module, what the mutation breaks, [old], [new], sections that must go RED)
    (_RF, "an interface read as a class (no dispatch: the interface member keyed as a plain method)",
     ['            kind = "interface"\n'], ['            kind = "class"\n'], ["collision", "shapes"]),
    (_RF, "a scope starts at its name line (the attribute lines dropped: graphify's node is missed)",
     ["        sc = T.Scope(name, _line(node), _end_line(node), lines=tuple(lines),"],
     ["        sc = T.Scope(name, (lines[0] if lines else _line(node)), _end_line(node), lines=tuple(lines),"],
     ["shapes"]),
    (_RF, "overloads share no line (each overload scope is a caller of the engine's one node)",
     ["        lines = (_line(nm),) + ((f0,) if f0 != line else ())"], ["        lines = (_line(nm),)"],
     ["shapes", "mustnot"]),
    (_RF, "#if regions read (a build-configuration-dependent call typed)",
     ["        if k < len(self.pp) and self.pp[k] <= hi:\n            self.errored.add(si)"],
     ["        if False:\n            self.errored.add(si)"], ["reader", "mustnot"]),
    (_RF, "parse errors read (a scope over an ERROR node keeps its sites)",
     ["        if node.has_error:\n            self.errored.add(si)"], ["        if False:\n            self.errored.add(si)"],
     ["reader"]),
    (_RF, "an errored scope's nested scopes keep their sites",
     ["            if p >= 0 and p in self.errored:\n                self.errored.add(si)"],
     ["            if False:\n                self.errored.add(si)"], ["reader"]),
    (_RF, "an `await` result typed as the awaited call",
     ['            elif val.type == "invocation_expression":\n'
      '                self.raw_binds.append((si, x, "call", None, val, _line(d)))'],
     ['            elif val.type in ("invocation_expression", "await_expression"):\n'
      '                self.raw_binds.append((si, x, "call", None, val.named_children[0] if val.type == '
      '"await_expression" else val, _line(d)))'], ["reader"]),
    (_RF, "primary-constructor parameters are no fields",
     ['                cf.fields.setdefault(pname, []).append(T.Binding("ann", ptype, line=_line(pl)))\n'
      '                self.cls_fields[idx].add(pname)'], ["                pass"], ["reader", "shapes"]),
    (_RF, "the partial modifier ignored (a part's members unseen from the other part)",
     ['        cf.open = "partial" in mods'], ["        cf.open = False"], ["shapes"]),
    (_RF, "extension methods are no patches (an extension on a builtin type reads as an external call)",
     ["            self.patches.append((ps[0][1], name))"], ["            pass"], ["reader", "mustnot"]),
    (_RF, "a generic method call keeps its type arguments in its name (`Gen<int>()` never binds)",
     ['        if t == "generic_name":\n            ident = _child(fn, "identifier")\n'
      '            return (self.text(ident), None) if ident is not None else None'],
     ['        if t == "generic_name":\n            ident = _child(fn, "identifier")\n'
      '            return (self.text(fn), None) if ident is not None else None'], ["shapes"]),
    (_RF, "a conditional access `x?.M()` is no call",
     ['        if t == "conditional_access_expression":\n            b = _child(fn, "member_binding_expression")'],
     ['        if False:\n            b = _child(fn, "member_binding_expression")'], ["reader"]),
    (_RF, "a field of the enclosing type read as a bare name",
     ["            if ci >= 0 and x in self.cls_fields[ci]:\n                return T.r_field(T.R_SELF, x)"],
     ["            if False:\n                return T.r_field(T.R_SELF, x)"], ["reader"]),
    (_RF, "a local declared with its type read as `var`",
     ['            if not implicit:\n                # `T x = new T(...)`'], ['            if False:\n                # `T x = new T(...)`'],
     ["reader", "shapes"]),
    (_RF, "a local function's name not poisoned in its enclosing scope",
     ["        self.poison(parent, name)\n"], ["        pass\n"], ["reader"]),
    (_RF, "binder: the namespace chain's types skipped (a using beats the caller's own namespace)",
     ["            got = self.resolve_fq(fq, grp)\n            if got is not None:\n                return got"],
     ["            got = self.resolve_fq(fq, grp)\n            if got is not None and not level:\n                return got"],
     ["shapes"]),
    (_RF, "binder: the caller's own .csproj no longer wins a duplicated full name",
     ["        own = [e for e in ents if e[0] == grp]"], ["        own = []"], ["shapes"]),
    (_RF, "binder: two usings answering one name resolve to the first",
     ["            if len(hits) > 1:\n                return self.T.RES_UNKNOWN"],
     ["            if False:\n                return self.T.RES_UNKNOWN"], ["mustnot"]),
    (_RF, "binder: no `using static` import",
     ["            return [self._qualified(rel, s) for s in dict.fromkeys(specs)]"], ["            return []"],
     ["shapes"]),
    (_RF, "binder: no extension method",
     ["            return list(self.index()[4].get(m, ()))"], ["            return []"], ["shapes"]),
    (_RF, "binder: no partial type grouped",
     ["            return [v for _k, v in sorted(groups.items()) if len(v) > 1]"], ["            return []"],
     ["shapes"]),
    # --- the review round (section V): the reader's facts
    (_RF, "a block-scoped local bound across the whole scope (the field after the block read as the local)",
     ["        if span is None:\n            self.names[si].add(x)"],
     ["        if True:\n            self.names[si].add(x)"], ["review"]),
    (_RF, "a catch variable bound across the whole scope",
     ["            span = self.span_of(n.parent, si, \"stmt\") if n.parent is not None else None"],
     ["            span = None"], ["review"]),
    (_RF, "no method modifiers recorded (no virtual dispatch, no override)",
     ["        self.note_member(\"mods\", name, line, frozenset(mods))\n"], ["        pass\n"], ["review"]),
    (_RF, "no parameter types recorded (every rival unjudgeable)",
     ["        self.note_member(\"mparams\", name, line, self.sig(_field(m, \"parameters\"), mtps))\n"],
     ["        pass\n"], ["review"]),
    (_RF, "no call arguments recorded (no argument ever known to fit)",
     ["                args[si].append(self.site_args(node, si))\n"], ["                args[si].append(None)\n"],
     ["review"]),
    (_RF, "a literal's suffix ignored (`5L` read as an int)",
     ["            return T.r_literal(_INT_SUFFIX.get(sfx, \"int\") if sfx else \"int\")"],
     ["            return T.r_literal(\"int\")"], ["review"]),
    (_RF, "no type-parameter variance recorded (a covariant implementer dropped)",
     ["            self.cs[\"variant\"][len(self.classes)] = var"], ["            pass"], ["review"]),
    (_RF, "no reassignment recorded (a rebound `var x = new T()` read as exactly T)",
     ["                self.reassigned[si].update(self.lvalue_names(_field(n, \"left\")))"], ["                pass"],
     ["review", "sigs"]),
    (_RF, "constructor scopes share the first constructor's line like overloads (no constructor is a caller)",
     ["        si = self.new_scope(_ctor_seg(cf.name), m, (_line(nm),) if nm is not None else (), -1, ci,"],
     ["        c0 = self.__dict__.setdefault(\"_c0\", {}).setdefault(ci, _line(m))\n"
      "        si = self.new_scope(_ctor_seg(cf.name), m, ((_line(nm),) if nm is not None else ())"
      " + ((c0,) if c0 != _line(m) else ()), -1, ci,"], ["ctors"]),
    # --- the review round: the core's C# rules (typed_calls, linked with the real reader)
    (_TC, "nested types never looked up (a type name binds the namespace's)",
     ["        nested = spec is not None and \"nested_types\" in spec.lookup_rules\n"], ["        nested = False\n"],
     ["review"]),
    (_TC, "argument applicability off (a rival never consulted)",
     ["        kind = self.cs_rival_kind(rel, key, site.member, look, site.argc, on_class, allow_ext)\n"
      "        if kind != \"rival\":"],
     ["        kind = self.cs_rival_kind(rel, key, site.member, look, site.argc, on_class, allow_ext)\n"
      "        if True:"], ["review"]),
    (_TC, "every argument list read as fitting",
     ["        descs = [self.cs_arg_type(rel, si, a) for a in args]\n"], ["        return True\n"], ["review"]),
    (_TC, "a hit with a rival graded as before (EXTRACTED kept)",
     ["            st[\"fit\"] += 1\n            return \"weak\""],
     ["            st[\"fit\"] += 1\n            return \"ok\""], ["review"]),
    (_TC, "a base overload of another signature never a rival",
     ["                if not self._cs_sig_takes(s[0], s[1], argc) or self.cs_same_sig(p, q, mine):\n"
      "                    continue"],
     ["                if True:\n                    continue"], ["review"]),
    (_TC, "virtual dispatch off",
     ["        virtual_ok = spec_c is not None and \"virtual_dispatch\" in spec_c.lookup_rules\n"],
     ["        virtual_ok = False\n"], ["review"]),
    (_TC, "overrides never added", ["            elif got and att in _ATTRIBUTED:"], ["            elif False:"],
     ["review"]),
    (_TC, "an override across a `new` slot counted",
     ["                    if not ok:\n                        continue"],
     ["                    if False:\n                        continue"], ["review", "sigs"]),
    (_TC, "a test-file override counted for a production caller",
     ["                if sc.facts.kind == \"interface\" or (not caller_test and "
      "self.test_file(sc.rel, unknown=False)):"],
     ["                if sc.facts.kind == \"interface\":"], ["review"]),
    (_TC, "more than MAX_TARGETS overrides still REPLACE (the engine's tuple removed)",
     ["            if mode == \"replace\" and virt_hold:"], ["            if False:"], ["review"]),
    (_TC, "a `new` method of a subclass that does not re-list the interface counted as an implementer",
     ["        j = next((i for i, x in enumerate(chain) if x is not None and self.cs_lists(x.key, key)), None)"],
     ["        j = 0 if chain and chain[0] is not None else None"], ["review"]),
    (_TC, "a static interface member read as interface dispatch (no exact call: the engine's tuple to an "
          "implementer's same-named method kept)",
     ["                        self.cs_static_iface.add(t)\n                        self.iface_only[t] = False\n"],
     ["                        pass\n"], ["review", "recheck"]),
    (_TC, "a closed generic receiver's type arguments ignored",
     ["        closed = self.cs_closed_args(rel, si, site.recv) if len(keys) == 1 else None"],
     ["        closed = None"], ["review"]),
    (_TC, "variance ignored (a covariant implementer excluded)",
     ["        if not variance:\n            return True"], ["        if True:\n            return True"], ["review"]),
    (_TC, "a sealed class / struct receiver read as open (its exact member demoted, overrides sought)",
     ["                or all(self.cs_final(k) for k in vd.recv_keys):"], ["                or False:"], ["review"]),
    (_RF, "sealed / static classes not recorded",
     ["            self.cs[\"sealed\"].append(len(self.classes))"], ["            pass"], ["review"]),
    (_TC, "a rebound constructed local read as exactly its class",
     ["                        and x in self.cs_reassigned(rel, si):"], ["                        and False:"],
     ["review"]),
    (_TC, "DEEPMAP_TYPED_CALLS_IFACE: an ON word read as a CSV (it selects nothing)",
     ["    if not raw or raw in _TRUE:"], ["    if not raw:"], ["flags"]),
    (_TC, "DEEPMAP_TYPED_CALLS_IFACE: a value naming no mode selects nothing",
     ["    return (known or frozenset(IFACE_MODES)), sorted("], ["    return known, sorted("], ["flags"]),
    # --- the re-check round (section W)
    (_TC, "signatures compared as raw TypeRefs (an override of a generic base member read as a rival)",
     ["                if not self._cs_sig_takes(s[0], s[1], argc) or self.cs_same_sig(p, q, mine):\n"],
     ["                if not self._cs_sig_takes(s[0], s[1], argc) or p in [x[0] for x in mine]:\n"], ["recheck"]),
    (_TC, "a base class's type parameters never substituted (any type matches them: a rival read as hidden)",
     ["                    subst = self.cs_subst(key, c.key) or {}\n"], ["                    subst = {}\n"], ["recheck"]),
    (_TC, "a qualified or System type name compared as written",
     ["            if seg in _CS_SYSTEM_ALIAS and qual in (None, (\"System\",)):\n"
      "                return (\"b\", _CS_SYSTEM_ALIAS[seg])\n"
      "            return (\"n\", seg, qual, None if qual is not None else self.cs_scope_levels(rel, scope))\n"],
     ["            return (\"n\", \".\".join(parts), None, ((\"u\", frozenset()),))\n"], ["recheck"]),
    (_TC, "a using alias compared by its own name, not its target's",
     ["            if targets:\n                target = next(iter(targets))\n"],
     ["            if False:\n                target = next(iter(targets))\n"], ["recheck", "sigs"]),
    (_TC, "a nullable reference annotation read as another type",
     ["                return inner                       # `string?`, `object?`: the same type\n"],
     ["                return (\"?\", inner)\n"], ["recheck"]),
    (_RF, "no msig recorded (signatures compared as raw TypeRefs)",
     ["        pl, morder = _field(m, \"parameters\"), self.type_param_order(m)\n"],
     ["        pl, morder = None, self.type_param_order(m)\n"], ["recheck"]),
    (_RF, "no base-list type arguments recorded (nothing substituted)",
     ["            bsig.append(self.sigt(b, tps, ()))\n"], ["            bsig.append((\"x\", \"\"))\n"], ["recheck"]),
    (_RF, "a construction of exactly the declared type read as a declaration (overrides sought)",
     ["                kind = \"ctor\" if self.constructs(typ, val) else \"ann\"\n"], ["                kind = \"ann\"\n"],
     ["recheck"]),
    (_TC, "a fully known interface dispatch set never REPLACEs (the engine's tuple to a non-implementer kept)",
     ["                    if known is not None:"], ["                    if False:"], ["recheck"]),
    (_TC, "an interface with an implementer the pass cannot see still REPLACEs",
     ["                or any(self.cs_iface_unresolved(k, m) for k in keys):\n"], ["                or False:\n"],
     ["recheck"]),
    (_TC, "the C# override reach not narrowed to the slot (another slot's override / a hiding method kept)",
     ["                if vd.dispatch == \"sub\" and ci is not None and ci.spec is not None \\\n"],
     ["                if False and ci is not None and ci.spec is not None \\\n"], ["recheck"]),
    # --- the fix round (section S)
    (_TC, "D1: a class type parameter compares as any type again (the hit's own `Save(T)` read as `Save(Cart)`)",
     ["    if not isinstance(a, tuple) or not isinstance(b, tuple) or not a or not b or a == _CS_UNK or b == _CS_UNK:\n"
      "        return None\n"],
     ["    if a == _CS_UNK or b == _CS_UNK or a[:1] == (\"tpv\",) or b[:1] == (\"tpv\",):\n        return True\n"
      "    if not isinstance(a, tuple) or not isinstance(b, tuple) or not a or not b:\n        return None\n"],
     ["sigs"]),
    (_TC, "D1: the receiver class's own type parameters not seeded (an override of `Keep(T)` read as a rival)",
     ["        out, cur, subst, seen = None, key, {nm: (\"tpv\", key, nm) for nm in (own or ())}, set()\n"],
     ["        out, cur, subst, seen = None, key, {}, set()\n"], ["sigs"]),
    (_TC, "D1: a signature the pass cannot tell compared as the same (no rival)",
     ["                if self.cs_sig_cmp(sig[skip:], qs) is True:\n"],
     ["                if self.cs_sig_cmp(sig[skip:], qs) is not False:\n"], ["sigs"]),
    (_TC, "D2: a same-named definition of another signature breaks the slot again",
     ["                if same and \"override\" not in mods:\n                    acc = mods & _CS_ACCESS\n"],
     ["                if \"override\" not in mods:\n                    return False\n                    acc = mods & _CS_ACCESS\n"],
     ["sigs"]),
    (_TC, "D2: an override of another signature counted as one of the call's slot",
     ["            return None if None in cmp else False  # an override of another slot (another signature)\n"],
     ["            pass\n"], ["sigs"]),
    (_TC, "D2: an interface member implemented by a same-named definition of another signature",
     ["                    if c:\n                        md = self.cs_member(x, mf, m, \"mods\")\n"],
     ["                    if True:\n                        md = self.cs_member(x, mf, m, \"mods\")\n"], ["sigs"]),
    (_TC, "D4: two qualifiers of one simple name read as one type",
     ["            return True if qa == qb else None  # another qualifier: relative, or a namespace alias it does not read\n"],
     ["            return True\n"], ["sigs"]),
    (_TC, "D4: an unqualified name's namespace never checked (any imported namespace may supply it)",
     ["        return _cs_unq_is(\".\".join(q), lev)\n"], ["        return True\n"], ["sigs"]),
    (_RF, "D3: a deconstruction's elements not read as rebound (only `x = ...`)",
     ["        stack = [(n, depth)]\n"],
     ["        stack = [(n, depth)] if getattr(n, \"type\", \"\") == \"identifier\" else []\n"], ["sigs"]),
    (_RF, "D3: a ref alias's write not read as rebinding its target",
     ["            elif t == \"ref_expression\":\n"], ["            elif False:\n"], ["sigs"]),
    (_RF, "D3: a user-defined ++ / -- not read as rebinding its operand",
     ["            elif t in (\"prefix_unary_expression\", \"postfix_unary_expression\") and _anon(n) & {\"++\", "
      "\"--\"}:\n"],
     ["            elif False:\n"], ["sigs"]),
    # --- fix round 2 (section N): its own rules, then the round-1 sweep's survivors (each killed by a rnd2 shape)
    (_TC, "N1: an unconstrained `T?` read as Nullable<T> again (an override of Format(T?) read as another slot)",
     ["            if tpk == \"a\":\n"
      "                return inner                       # `T?` of"
      " a T not constrained `struct`: an annotation (C# 9+)\n"],
     ["            if False:\n"
      "                return inner\n"], ["rnd2"]),
    (_TC, "N1: a `where T : struct` T? read as an annotation (IntParser.Parse(int?) read as another slot)",
     ["            if tpk == \"s\":\n"
      "                return (\"?\", inner, True)          # `where T : struct`: Nullable<T>\n"],
     ["            if tpk == \"s\":\n"
      "                return inner\n"], ["rnd2"]),
    (_RF, "N1: a `struct` / `unmanaged` constraint not recorded",
     ["                if txt in (\"struct\", \"unmanaged\"):\n"
      "                    out[nm] = \"s\"\n"],
     ["                if False:\n"
      "                    out[nm] = \"s\"\n"], ["rnd2"]),
    (_TC, "N1: `T?` of a T whose constraint is unknown (`where T : U`) read as Nullable<T>",
     ["            known = not is_tp and (\n"],
     ["            known = (\n"], ["rnd2"]),
    (_TC, "N2: IntPtr / UIntPtr not read as nint / nuint",
     ["\"Object\": \"object\", \"dynamic\": \"object\", \"IntPtr\": \"nint\", \"UIntPtr\": \"nuint\"}"],
     ["\"Object\": \"object\", \"dynamic\": \"object\"}"], ["rnd2"]),
    (_TC, "N2: System.Nullable<X> not read as X?",
     ["                return (\"?\", out[2], True)         # System.Nullable<X> is X? (X a value type)\n"],
     ["                return out\n"], ["rnd2"]),
    (_TC, "N2: a `global using` alias of the project not expanded in a signature",
     ["                targets = self.cs_group_tables(rel)[1].get(parts[0], set())\n"],
     ["                targets = set()\n"], ["rnd2"]),
    (_RF, "N2: the binder never resolves a `global using` alias (a project class's alias unknown)",
     ["            if level == \"\" and name in self.index()[5].get(grp, {}):\n"],
     ["            if False:\n"], ["rnd2"]),
    (_RF, "N2: a `global using` alias not recorded (_cs galias)",
     ["                self.cs[\"galias\"].append((local, spec))  # every file of the project sees it\n"],
     ["                pass\n"], ["rnd2"]),
    (_TC, "N2: two names of another last segment read as two types (an alias the pass cannot read)",
     ["            return True if a == b else None    # another last segment: either may be an alias\n"],
     ["            return True if a == b else False\n"], ["rnd2"]),
    (_TC, "N2: a name against a keyword / a project class read as two types",
     ["            return False if o[0] in (\"tpv\", \"mtp\") else None\n"],
     ["            return False\n"], ["rnd2"]),
    (_TC, "R4: a private middle method breaks the slot again",
     ["                    if acc <= {\"private\"}:\n"
      "                        continue                   # private"
      " (as written, or by default): no subclass sees it\n"],
     ["                    if False:\n"
      "                        continue\n"], ["rnd2"]),
    (_TC, "R4: an `internal` middle method of the subclass's own project read as unknown",
     ["                            and self.cs_group_of(self.rel_of(c, d)) != self.cs_group_of(sc.rel):\n"],
     ["                            and True:\n"], ["rnd2"]),
    (_TC, "R3: an override hit's rival between it and its slot's declarer ignored",
     ["        if idx >= 0 and not self.cs_override_shadowed(key, site.member, look.owner, site.argc, on_class) \\\n"],
     ["        if idx >= 0 and True \\\n"], ["rnd2"]),
    (_TC, "R3: the walk goes past the slot's declarer (a rival above it read as a candidate)",
     ["            if declarer:\n"
      "                return False                       # the slo"
      "t's declarer: its own overloads are the rule as before\n"],
     ["            if False:\n"
      "                return False\n"], ["rnd2"]),
    (_TC, "R2: a subclass closing the generic receiver class with other type arguments counted",
     ["                if closed is not None:\n"
      "                    lv = self.mro(s)\n"],
     ["                if False:\n"
      "                    lv = self.mro(s)\n"], ["rnd2"]),
    (_TC, "R1: two unqualified external names read as one type whatever their usings",
     ["    ua, ub = _cs_ulevels(la), _cs_ulevels(lb)\n"
      "    if ua is None or ub is None:\n"
      "        return None\n"],
     ["    return True\n"], ["rnd2"]),
    (_TC, "R1: namespace-block usings not compared (only the compilation unit's)",
     ["    if len(ua) == len(ub) and ua[:-1] == ub[:-1]:\n"],
     ["    if len(ua) == len(ub):\n"], ["rnd2"]),
    (_TC, "R1: two scopes importing a namespace known to hold the name no longer one type (TokBase / TokDer)",
     ["        if any(seg in _CS_KNOWN_NS.get(ns, ()) for ns in ua[-1] & ub[-1]):\n"],
     ["        if False:\n"], ["rnd2"]),
    (_TC, "R1: any namespace both scopes import read as holding the name (System.Text for Timer)",
     ["        if any(seg in _CS_KNOWN_NS.get(ns, ()) for ns in ua[-1] & ub[-1]):\n"],
     ["        if ua[-1] & ub[-1]:\n"], ["rnd2"]),
    (_TC, "N2: a Nullable<X> the pass cannot prove System's read as two types against X?",
     ["        if o[:1] == (\"g\",) and len(o) == 3 and o[1][:2] == (\"n\", \"Nullable\"):\n"],
     ["        if False:\n"], ["sigs"]),
    (_TC, "R1: a nearer using level no longer stops a qualified name's proof",
     ["        if val:\n"
      "            return None\n"
      "    return None\n"],
     ["        if False:\n"
      "            return None\n"
      "    return None\n"], ["rnd2"]),
    (_RF, "R1: every using read as the compilation unit's (one file-wide set, as before)",
     ["                self.frames[self.fstack[-1]][1].append(spec)\n"],
     ["                self.frames[0][1].append(spec)\n"], ["rnd2"]),
    (_TC, "R1: another .csproj's `global using` seen",
     ["                if tb is None or self.cs_group_of(r) != grp:\n"],
     ["                if tb is None:\n"], ["rnd2"]),
    (_TC, "D1a (sweep): an unfixed class type parameter normalises to object (an outer class's T in MidI.Put(T))",
     ["            return got if got is not None else _CS_UNK\n"],
     ["            return got if got is not None else (\"b\", \"object\")\n"], ["rnd2"]),
    (_TC, "D1b (sweep): a generic's unknown argument read as the same type (MidI.Put2(List<T>))",
     ["        got = [_cs_tcmp(x, y) for x, y in pairs]\n"
      "        return False if False in got else (None if None in got else True)\n"],
     ["        got = [_cs_tcmp(x, y) for x, y in pairs]\n"
      "        return False if False in got else True\n"], ["rnd2"]),
    (_TC, "D2a (sweep): slot signatures read in the target class's own frame, not the subclass's",
     ["                sig = self.cs_def_sig(ci, mf, m, s)\n"],
     ["                sig = self.cs_def_sig(ci, mf, m, ci.key)\n"], ["rnd2"]),
    (_TC, "D2b (sweep): a middle definition of an untold signature no longer makes the slot unknown",
     ["                if same is None:\n"
      "                    return None\n"],
     ["                if same is None:\n"
      "                    continue\n"], ["rnd2"]),
    (_TC, "D2c (sweep): interface mapping skips a definition it cannot place (a base's picked instead)",
     ["                    if c is None:\n"
      "                        return None                # a defin"
      "ition the pass cannot place: the implementer unknown\n"],
     ["                    if c is None:\n"
      "                        continue\n"], ["rnd2"]),
    (_TC, "D2d (sweep): interface signatures read from the receiver interface only (its bases dropped)",
     ["        for k in [key] + sorted(self.mro_class_keys(key) - {key}):\n"],
     ["        for k in [key]:\n"], ["rnd2"]),
    (_TC, "D2e (sweep): interface mapping done for the interface's first signature only",
     ["        for sig in islot:                          # each signature the interface declares m with\n"],
     ["        for sig in islot[:1]:                      # each signature the interface declares m with\n"], ["rnd2"]),
    (_RF, "D3b (sweep): a ref / out argument rebinds only a bare identifier (`Swap(ref (b))` missed)",
     ["            elif t == \"argument\" and _anon(n) & {\"ref\", \"out\"}:\n"
      "                for k in n.named_children:\n"
      "                    self.reassigned[si].update(self.lvalue_names(k))\n"],
     ["            elif t == \"argument\" and _anon(n) & {\"ref\", \"out\"}:\n"
      "                for k in n.named_children:\n"
      "                    if k.type == \"identifier\":\n"
      "                        self.reassigned[si].add(self.text(k))\n"], ["rnd2"]),
    (_RF, "D3c (sweep): lvalue_names does not look through parentheses (`(b) = ...`)",
     ["            elif k.type in (\"parenthesized_expression\", \"tup"
      "le_expression\", \"argument\", \"ref_expression\"):\n"],
     ["            elif k.type in (\"tuple_expression\", \"argument\", \"ref_expression\"):\n"], ["rnd2"]),
    (_RF, "D3f (sweep): the ref_expression walk branch reads only a bare identifier (`ref (b)` missed)",
     ["            elif t == \"ref_expression\":\n"
      "                # `ref x` outside an argument (`ref T r = re"
      "f x;`, `=> ref x`, `y = ref x`): a ref alias may write x\n"
      "                for k in n.named_children:\n"
      "                    self.reassigned[si].update(self.lvalue_names(k))\n"],
     ["            elif t == \"ref_expression\":\n"
      "                for k in n.named_children:\n"
      "                    if k.type == \"identifier\":\n"
      "                        self.reassigned[si].add(self.text(k))\n"], ["rnd2"]),
    (_TC, "D4c (sweep): the project's `global using` namespaces dropped",
     ["                        gu.add(u[0])\n"],
     ["                        pass\n"], ["rnd2"]),
    (_TC, "D4d (sweep): an enclosing namespace no longer proves a qualified name",
     ["            if val == ns:\n"
      "                return True\n"],
     ["            if False:\n"
      "                return True\n"], ["rnd2"]),
    (_TC, "D4f (sweep): a known value type's `X?` no longer proven another type than X (Open7(int) in the middle)",
     ["            return (\"?\", inner, bool(known))       # a type "
      "parameter's constraint unknown: `T?` is T or Nullable<T>\n"],
     ["            return (\"?\", inner, False)\n"], ["rnd2"]),
    (_TC, "D4g (sweep): an array's `?` no longer read as an annotation (Fill7(int[]?))",
     ["                return inner                       # an array is a reference type: its `?` is an annotation\n"],
     ["                pass\n"], ["rnd2"]),
    (_TC, "D4b (sweep): a System name under any qualifier read as the keyword (UnityEngine.Object as object)",
     ["            if seg in _CS_SYSTEM_ALIAS and qual in (None, (\"System\",)):\n"],
     ["            if seg in _CS_SYSTEM_ALIAS:\n"], ["rnd2"]),
    (_TC, "D4h (sweep): two project classes of one simple name compared as one type (Pa.Order / Pb.Order)",
     ["        return len(a) > 1 and len(b) > 1 and a[1] == b[1]\n"],
     ["        return len(a) > 2 and len(b) > 2 and a[2] == b[2]\n"], ["rnd2"]),
]


def _run_sections(names, buckets):
    _RUNS.clear()
    for n in names:
        {"collision": sec_collision, "reader": sec_reader, "shapes": lambda: sec_shapes(buckets),
         "mustnot": lambda: sec_mustnot(buckets), "members": lambda: sec_members(buckets),
         "review": lambda: sec_review(buckets), "recheck": lambda: sec_recheck(buckets),
         "sigs": lambda: sec_sigs(buckets),
         "rnd2": lambda: sec_rnd2(buckets),
         "ctors": lambda: sec_ctor_scopes(buckets),
         "flags": lambda: sec_flags(buckets)}[n]()
    _RUNS.clear()


@contextlib.contextmanager
def mutant(modname: str, olds: list, news: list):
    """Module `modname` replaced in sys.modules by a copy of its source with each `old` -> `new` (each must occur
    exactly once); restored after."""
    path = BUILD / f"{modname}.py"
    src = path.read_text(encoding="utf-8")
    for old, new in zip(olds, news):
        if src.count(old) != 1:
            raise AssertionError(f"mutation anchor not found exactly once in {modname}: {old[:60]!r}")
        src = src.replace(old, new)
    spec = importlib.util.spec_from_loader(modname, loader=None)
    mod = importlib.util.module_from_spec(spec)
    mod.__file__ = str(path)
    exec(compile(src, str(path), "exec"), mod.__dict__)
    saved = sys.modules.get(modname)
    sys.modules[modname] = mod
    reader = sys.modules.get("receiver_facts_csharp")
    ir = getattr(reader, "_T", None) if modname == "typed_calls" else None
    if modname == "typed_calls" and reader is not None:
        reader._T = mod                        # the reader builds its IR with the core it is linked by
    try:
        yield mod
    finally:
        if saved is not None:
            sys.modules[modname] = saved
        else:
            sys.modules.pop(modname, None)
        if modname == "typed_calls" and reader is not None:
            reader._T = ir


def sec_mutations(buckets) -> None:
    """Each mutant re-runs its sections on ONE engine's capture in the default mode only: a mutant only has to show
    that its rule is load-bearing (the full sections above already hold every check on both engines and both modes),
    and the light run keeps the gate inside its time budget on a loaded machine."""
    global MODES
    print("\n-- M: mutation teeth (each rule broken in a copy of the module -> its checks go RED; one engine, the "
          "default mode) --")
    check("M the mutation table is not empty", bool(MUTANTS))
    full = MODES
    MODES = {"default": full["default"]}
    try:
        for modname, desc, olds, news, sections in MUTANTS:
            try:
                with mutant(modname, olds, news):
                    with recording(Rec(loud=False)) as rec:
                        _run_sections(sections, buckets[:1])
            except AssertionError as exc:
                check(f"M {modname}: {desc} -> the mutation applies", False, exc)
                continue
            check(f"M {modname}: {desc} -> RED ({rec.failed} failing check(s))", rec.failed > 0,
                  f"{rec.passed} passed, 0 failed")
    finally:
        MODES = full
        _RUNS.clear()


# ================================================================================================
# --capture / --dump / main
# ================================================================================================

def _relative(graph: dict, root: Path) -> dict:
    prefixes = [str(root) + os.sep, str(root).replace("\\", "/") + "/"]

    def fix(v):
        if isinstance(v, str):
            for p in prefixes:
                if v.lower().startswith(p.lower()):
                    return v[len(p):].replace("\\", "/")
        return v

    return {k: ([{kk: fix(vv) for kk, vv in item.items()} if isinstance(item, dict) else item for item in v]
                if isinstance(v, list) else v) for k, v in graph.items()}


def real_graphs(names=None) -> dict:
    """{tree: graph.json} the INSTALLED engine writes for every tree (or `names`; temporary DEEPMAP_HOME)."""
    out: dict = {}
    home = tmp() / "home"
    with clean_env(DEEPMAP_HOME=str(home), DEEPMAP_EXTRACTOR="graphify"):
        for name in (names or TREES):
            root = write_tree(tmp() / "real" / name, TREES[name])
            res, rep = GX.extract_with_report(root)
            gp = GX.graphify_out_dir(root) / "graph.json"
            out[name] = load_json(gp) if (res is not None and gp.is_file()) else None
            if out[name] is None:
                print(f"  (engine failed on {name}: {rep.get('cause')})")
    return out


def do_capture() -> int:
    ver = GX.graphify_version()
    b = BUCKETS.get(ver)
    if b is None:
        print(f"--capture: graphify {ver} is not one of {sorted(BUCKETS)}")
        return 1
    unknown = [t for t in _ONLY if t not in TREES]
    if unknown:
        print(f"--capture: no such tree {unknown} (trees: {sorted(TREES)})")
        return 2
    real = real_graphs(_ONLY or None)
    for name, g in real.items():
        if g is None:
            print(f"--capture: the engine failed on {name}")
            return 1
        p = fixture_path(b, name)
        p.parent.mkdir(parents=True, exist_ok=True)
        doc = {"_about": (f"REAL `graphify update --no-cluster` output (graphify {ver}) of "
                          f"tests/typed_calls_cs_gates.TREES[{name!r}], written by extract_with_report (absolute "
                          "paths made root-relative; map_graph reads none of them); re-capture with "
                          "`python tests/typed_calls_cs_gates.py --capture` under this engine."),
               "engine": ver, "tree_sha256": tree_sha(name), "graph": _relative(g, tmp() / "real" / name)}
        p.write_bytes((json.dumps(doc, indent=1, sort_keys=True) + "\n").encode("utf-8"))
        print(f"--capture: wrote {p.relative_to(REPO)} ({len(g.get('nodes', []))} nodes, "
              f"{len(g.get('links', []))} links)")
    return 0


def do_dump() -> int:
    for b in buckets_present():
        for name in TREES:
            for mode in MODES:
                run = run_for(b, name, mode)
                sid = stable(run.nodes)
                st = run.stats
                print(f"== {b}/{name} [{mode}] files={st['files']} failed={st['files_failed']} err={st['error']} "
                      f"sites={st['sites']} removed={st['removed']} emitted={st['emitted']}")
                for c, t in sorted(typed_view(run.nodes, form="name").items()):
                    print(f"   TYPED {c} -> {t}")
                bset = [(a, str(x), GX.is_call_key(x)) for a, x in run.before]
                oset = [(a, str(x), GX.is_call_key(x)) for a, x in run.out]
                for c in bset:
                    if c not in oset:
                        print(f"   REMOVED {sid.get(c[0])} -> {c[1]}{' (key)' if c[2] else ''}")
                for c in bset:
                    print(f"   in: {sid.get(c[0])} -> {sid.get(c[1], c[1]) if c[2] else c[1]}")
    return 0


def main() -> int:
    try:
        if MODE == "capture":
            return do_capture()
        if MODE == "dump":
            return do_dump()
        print(f"typed_calls_cs_gates: Python {sys.version.split()[0]}, graphify "
              f"{GX.graphify_version() if _HAS_ENGINE else '(not installed)'}, build {BUILD}")
        if TC is None or RCS is None:
            check("the C# reader modules import (typed_calls, receiver_facts_csharp)", False, _IMPORT_ERRORS)
        buckets = buckets_present()
        check("both engines' captures of every tree are present (tests/fixtures/typed_calls/{0839,0851}/cs)",
              buckets == ["0839", "0851"], buckets)
        if TC is not None:
            sec_collision()
            sec_reader()
            sec_shapes(buckets)
            sec_mustnot(buckets)
            sec_review(buckets)
            sec_recheck(buckets)
            sec_sigs(buckets)
            sec_rnd2(buckets)
            sec_members(buckets)
            sec_parity(buckets)
            sec_flags(buckets)
            sec_call_lines(buckets)
            sec_real_engine()
            if RCS is not None:
                sec_mutations(buckets)
        print(f"\nRESULT: {_REC.passed} passed, {_REC.failed} failed")
        if _REC.failed:
            print("FAILED:\n  " + "\n  ".join(_REC.fails[:60]))
        print("GREEN" if not _REC.failed else "RED")
        return 0 if not _REC.failed else 1
    finally:
        cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
