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
     substitution stays one (Store<Order>.Save; bound, held, when an Order argument is PROVEN not to fit the hit:
     section X's g2); `T x = new T()` / `T x = new()` never rebound is exact (no
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
     type, an MSBuild <Using> alias is its target (one under a condition the pass cannot read: unknown, nothing
     removed); a private middle method neither breaks nor continues a slot, an `internal new virtual` of the same
     project starts another; an override's rival between it and its slot's declarer leaves the call unknown; a
     subclass closing the generic receiver class with other type arguments is no target; two unqualified external
     names are one type only under usings that resolve them alike (the same, a subset, or both importing a namespace
     known to hold the name; another file's, another namespace block's, a nearer block's using: unknown), a project's
     `global using` and an enclosing namespace prove one, another project's never; the reader's tpkind / galias /
     cvis tables and the binder's `global using` aliases.
  U  the residuals round's using shapes (TREES["usings"], Roslyn 4.8's bindings with their run-time dispatch), on the
     typed keys AND the pushed edges: a project's MSBuild implicit usings (ImplicitUsings in the project, in
     Directory.Build.props, turned off again by Directory.Build.targets), a <Using Remove>, an ImplicitUsings the pass
     cannot read (never compared across .csproj groups); usings at different levels (SA1200: inside the namespace
     block) proven alike through a known holder (U1) or a coarser level (U2), never past a nearer external using; a
     nearer using's project type beats an outer alias, a block's alias applies only inside its block, a `global using`
     alias past a block's project using (an external one first: unknown); the members of an enclosing namespace an
     external assembly may declare types in (System.Timers.Timer from `namespace System.Timers.Custom`, ILogger from
     `namespace Microsoft.Extensions.Logging`); an alias the pass cannot see (a linked file's `global using`) stays
     a name of its own, never "two types". csharp_msbuild.read_group on in-memory trees (SDK lists, the last
     assignment props -> project -> targets, <Using> Remove / Alias / Static, $(P), conditions, every unreadable form
     incomplete with its reason, two projects, malformed XML, never raises) and its SDK lists equal to the kit
     reference's (benchmark/call_linking/truth_roslyn/Program.cs, read as data).
  X  the residuals round's mapping shapes (TREES["resid"]): an explicit interface implementation is the implementer
     (never a subclass override of a public twin; another interface's never implements this one), only a public
     method maps implicitly (a private / internal `new` one: the base's public member), a default interface member
     beside a private method (the implementer unknown: nothing removed); `Cv8<int?>` is no `Cv8<int>`, a class's `?`
     an annotation, a struct's not, `Cv8<T?>` unknown; receiver type arguments substituted into overload resolution
     (Repo11<Order>.Save(T) is Save(Order): no rival) and the rival bound when the hit is PROVEN inapplicable (held:
     no REPLACE), never through a user-defined conversion or a type-parameter argument, never to a base overload the
     call site may not see (private, protected, no modifier: C# skips it) nor past a candidate in another partial
     part (another node); an internal / protected internal one of the caller's project binds. S's StoreUser / LastUser
     .SaveOrder and RepoUser.RemoveId, N's SUser.Go, V's StoreUser.Go and W's StoreUser.SaveOrder (a base list's
     substitution: `CartStore : Store<Order>`) are bound so too (Roslyn's bindings).
  Y  the soundness round's shapes (TREES["sound"], Roslyn 4.8's bindings with their run-time dispatch), on the typed
     keys AND the pushed edges, each beside a positive sibling and with the engine's true tuple as teeth: a project
     type does not make a namespace project-only (System.Timers.Timer from a project's `namespace System.Timers`, past
     a block's using and past a compilation unit's alias); a package id's ROOT may fill the project's namespaces
     (`using Acme.Core;`, an enclosing Acme.Sub); a name no level may supply proves nothing; an x tuple and a relative
     spelling are unknown even against themselves (one alias name of two targets, a generic alias target or a qualifier
     in two namespaces, two groups' unreadable aliases, cs_same_sig), one alias of one target in one lookup scope is one
     type; a multi-line <ImplicitUsings> is not `enable`; a wildcard <Using Remove>; a file linked into a project
     (no project's directory, another project's directory, a shared project); one generic interface by two paths and
     an explicit specifier's type arguments; System.Net.Http outside .NET Framework only; Directory.Packages.props as
     an import point (and costing nothing when it holds central-package items only); `ref readonly`; a base-list head
     through the alias walk; member lookup skipping what the call site cannot see (an explicit-only implementation, a
     private, protected or other project's internal `new` member, a friend assembly's kept) with the grade of the
     base's definition; Nullable<int> / Int32 closing a generic receiver.
  L  the last soundness round's shapes (TREES["final"], Roslyn 4.8's bindings with their run-time dispatch; for an
     MSBuild rule, the usings real MSBuild (SDK 8.0.202) generates under `dotnet build`'s Debug), on the typed keys
     AND the pushed edges, each beside a positive sibling and with the engine's true tuple as teeth: a method's own
     generic arity and a `params` array are part of its signature (interface mapping, a virtual slot, an overload
     rival); a block's `using static` supplies its type's nested types before the compilation unit's usings; a
     directive inside an #if branch is possible (a `global using` too); <Compile Remove> and default items off take a
     file's `global using` out of its group; the same-file rule binds only the same or an enclosing namespace
     declaration's class; a capable enclosing namespace stops the lookup; a `file` type is seen in its own file only; a
     block's usings apply inside that block only; `ResultG` is no `ResultG<T>`; partial parts merge per compiling
     project; transparency needs a root a project owns and no package id shares (by prefix too); a <Using Remove>
     written as a path, a boolean-like comparison, a Directory.Build.rsp, `%3B` split first, a package's hidden
     usings (two package sets compare only when equal), a $(Configuration) condition. The reader's, the binder's and
     csharp_msbuild's new tables and the core's comparisons of them; R33-R39's read_group rows sit with U msbuild,
     each against real MSBuild. Also the round's fixes: an inner block's alias target resolved with the outer
     blocks' usings, a nested type of another arity (`Entry` / `Entry<T>`), a part two projects compile left open, an
     enclosing namespace proving a listed name (`CancellationToken` in System.Threading), a `global::` <Using>, and a
     block's using of a namespace external code may fill hiding a project type (or alias) found further out, an
     enclosing namespace's or the same file's (a project namespace still answers: a qualified base list stays a proof).
     Decisions that turn older proofs into unknowns (recall only, nothing removed, each Roslyn-checked) flip their rows
     to gaps: R27 / R32 in section Y (Tick1b, Tick1d, TickZ), R25 (TickN keyed nothing), R23 in L (S4, User3); older
     trees whose namespace roots no project owned now own them through a RootNamespace / AssemblyName (no binding
     changes), so their rows keep testing what they were written for; section A's tree has its project file (and the
     same tree without one: no key, nothing removed).
  Z  the closing round's shapes (TREES["close"]: R42 / R43 / R47, TREES["closeu"]: R44 / R45, apart because a class
     with an unresolved base opens the interface dispatch sets of its tree; Roslyn 4.8's bindings with their run-time
     dispatch; for R45, the usings real MSBuild (SDK 8.0.202) generates for the kit's c6 package shapes k01 / k02), on
     the typed keys AND the pushed edges, each beside a positive sibling and with the engine's true tuple as teeth: an
     UNRESOLVED base (an alias of a root no project owns naming the interface itself, a project type behind a namespace
     declaration's non-transparent using, under interface and virtual dispatch, a generic and a qualified spelling;
     an inherited member, a base class and interface both through aliases, a re-implementation through an alias
     listing, an inherited property hiding a type, an alias of a closed generic interface) leaves the dispatch set
     open and the overload rival walk unknown (R42), as far as the project types it may be reach WITH their chains
     (L1: an interface on a candidate's chain, an alias of an alias, a candidate class holding a member that hides a
     type); a generic, array or nullable type whose head or element the
     binder could not place is never proven another shape (R43: `int?` / an unplaced `Nullable<int>`, `int?[]` /
     `Nullable<int>[]`); a using, `using static` or alias target written inside a namespace declaration means what C#
     resolves from that declaration outward (relative, after a file-scoped namespace, in feature folders, in nested
     blocks), `global::` and a compilation unit's directive stay absolute, a capable namespace's candidate makes it
     unknown, and the core compares using levels by those meanings (R44: CsBinder.frame_meanings / using_meaning /
     decl_meanings); two projects' MSBuild readings compare only when neither has a package, directly or through a
     ProjectReference (R45); a bare call a `using static` could supply is unknown where an enclosing type may hold
     the name (declared, inherited, a field) and sees only the directives of its own blocks (L2: R29 applied to
     `using static`). The R45 decision turns L's TickY2 (one package in both projects) into a gap: Roslyn binds
     TickerY2.Tick, the pass keys the interface member alone, nothing removed. R42 as narrowed by L1 (an unresolved
     base named nm may be a project type named nm or an alias target of that name, with their chains; an unread base
     or a candidate the pass cannot read: any type) turns the interface REPLACE of sound's _Y_PROVEN rows and R20's
     LongCounter (UC's unresolved `Order` has a candidate the pass cannot read) and recheck's JobUser.UseI
     (OddPlugin's unread base) into gaps: the keys unchanged, the engine's tuple kept. R47 completes R42's audit: a
     class whose own base list holds an interface whose chain may list the called interface again (an unresolved
     entry that may reach it, or the interface written past an external or unresolved entry) may re-implement its
     member, so where that class, or a class from it up to the listing class, defines the member the dispatch set
     is unknown (the interface member alone, the engine's tuple kept: an alias listing, a block `using System;`
     under SA1200, `IDisposable` first, `new` and explicit re-implementations); an unresolved entry that can reach
     nothing changes nothing (L1: the REPLACE stays).
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
# of an external and of a project type, an MSBuild <Using> alias (one under an unreadable condition: unknown)), a private middle
# method, an override among the candidates, a generic subclass of other type arguments, two unqualified external
# names by their scopes' usings. Every call is legal C# except NoUsing/Drawers.cs, whose `Point` no using of its
# project supplies (a must-not: nothing proven); N_EXPECT names the binding Roslyn 4.8 gives each, with its run-time
# dispatch (AC1).
RND2 = {
    # (R2.csproj owns the roots its files declare: R2 by its name, Pb by its RootNamespace, Pa by its AssemblyName;
    # R32 reads a root no project owns as a package's)
    "src/R2/R2.csproj": '<Project Sdk="Microsoft.NET.Sdk">\n  <PropertyGroup>\n    <RootNamespace>Pb</RootNamespace>\n'
                        "    <AssemblyName>Pa</AssemblyName>\n  </PropertyGroup>\n</Project>\n",
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
    # N2: aliases an MSBuild <Using> item declares (read since the residuals round: a slot, a `new virtual` in the
    # middle, an interface implementer, an interface member's second overload); one under a condition the pass cannot
    # read (Tk3: an environment property) stays unknown, never "two types" (Aliases3.cs)
    "src/Sg3/Sg3.csproj": """<Project Sdk="Microsoft.NET.Sdk">
  <ItemGroup>
    <Using Include="System.Threading.CancellationToken" Alias="Tk2" />
    <Using Include="System.Int32" Alias="I32" />
    <Using Include="System.Threading.CancellationToken" Alias="Tk3" Condition="'$(CI)' != 'true'" />
  </ItemGroup>
</Project>
""",
    "src/Sg3/Aliases3.cs": """using System.Threading;

namespace Sg3;

public class A13
{
    public virtual void Run13(CancellationToken ct)
    {
    }
}

public class B13 : A13
{
    public override void Run13(Tk3 ct)
    {
    }
}

public class Alias3User
{
    private readonly A13 _a;

    public void Go(CancellationToken ct)
    {
        _a.Run13(ct);
    }
}
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
_NET8 = """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
  </PropertyGroup>
</Project>
"""
_IMPLICIT = """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
    <ImplicitUsings>enable</ImplicitUsings>
  </PropertyGroup>
</Project>
"""
# The reader residuals round, part 1 (section U): a project's MSBuild implicit usings, <Using> items and
# Directory.Build.props / .targets read (the SDK's lists), usings at different levels (SA1200: inside the namespace
# block), a using alias by the namespace declaration that holds it (a nearer using's project type beats an outer alias;
# a block's alias applies only inside its block), the members of an enclosing external namespace. Each bug shape sits
# beside a positive sibling and a must-not; U_EXPECT names the binding Roslyn 4.8 gives each call (the kit's reference,
# run on this exact tree), with its run-time dispatch (AC1).
USINGS = {
    # a1 (SA1200 + implicit usings), a2 (both usings at the top), a3 (a nearer external using first): CancellationToken
    "src/Ua/Ua.csproj": _IMPLICIT,
    "src/Ua/Contracts/IPut.cs": """namespace Ua.Contracts;

public interface IPut
{
    void Put(CancellationToken ct);
}
""",
    "src/Ua/Contracts/IPutA.cs": """using System.Threading;

namespace Ua.Contracts;

public interface IPutA
{
    void Put(CancellationToken ct);
}
""",
    "src/Ua/Contracts/IPut3.cs": """namespace Ua.Contracts;

public interface IPut3
{
    void Put(CancellationToken ct);
}
""",
    "src/Ua/Impl/Putter.cs": """namespace Ua.Impl
{
    using System.Threading;
    using Ua.Contracts;

    public class Putter : IPut
    {
        public void Put(CancellationToken ct)
        {
        }
    }
}
""",
    "src/Ua/Impl/PutterA.cs": """using System.Threading;
using Ua.Contracts;

namespace Ua.Impl;

public class PutterA : IPutA
{
    public void Put(CancellationToken ct)
    {
    }
}
""",
    "src/Ua/Impl3/Putter3.cs": """using System.Threading;

namespace Ua.Impl3
{
    using Ext.Tokens;
    using Ua.Contracts;

    public class Putter3 : IPut3
    {
        public void Put(CancellationToken ct)
        {
        }
    }
}
""",
    "src/Ua/Use/PutUser.cs": """using Ua.Contracts;

namespace Ua.Use;

public class PutUser
{
    private readonly IPut _p;
    private readonly IPutA _pa;
    private readonly IPut3 _p3;

    public void Go(CancellationToken ct)
    {
        _p.Put(ct);
    }

    public void GoA(CancellationToken ct)
    {
        _pa.Put(ct);
    }

    public void Go3(CancellationToken ct)
    {
        _p3.Put(ct);
    }
}
""",
    # `global using` aliases in a file of another directory that Ul.csproj compiles through <Compile Include> (a link;
    # the binder folds what a linked file says: Tk4 / I4 are aliases of unknown target, never "two types" (the slot,
    # the interface's second overload); section Y's Zu keeps an alias the pass cannot see at all)
    "src/Shared/Aliases.cs": """global using Tk4 = System.Threading.CancellationToken;
global using I4 = System.Int32;
""",
    "src/Ul/Ul.csproj": """<Project Sdk="Microsoft.NET.Sdk">
  <ItemGroup>
    <Compile Include="..\\Shared\\Aliases.cs" />
  </ItemGroup>
</Project>
""",
    "src/Ul/Slots.cs": """using System.Threading;

namespace Ul;

public class A14
{
    public virtual void Run14(CancellationToken ct)
    {
    }

    public virtual void Num14(int x)
    {
    }
}

public class B14 : A14
{
    public override void Run14(Tk4 ct)
    {
    }

    public override void Num14(I4 x)
    {
    }
}

public interface IOv16
{
    void Put16(int x, int y);

    void Put16(Tk4 ct);
}

public class Impl16 : IOv16
{
    public void Put16(int x, int y)
    {
    }

    public void Put16(CancellationToken ct)
    {
    }
}

public class LinkUser
{
    private readonly A14 _a;
    private readonly IOv16 _o;

    public void Go(CancellationToken ct)
    {
        _a.Run14(ct);
    }

    public void Go2()
    {
        _a.Num14(1);
    }

    public void Go3()
    {
        _o.Put16(1, 2);
    }
}
""",
    # U2: a name no table lists (IConfiguration): the interface file's using at the top, the implementer's inside
    # its namespace block
    "src/Um/Um.csproj": CSPROJ,
    "src/Um/Contracts/IHandle.cs": """using Microsoft.Extensions.Configuration;

namespace Um.Contracts;

public interface IHandle
{
    void Handle(IConfiguration c);
}
""",
    "src/Um/Impl/Handler.cs": """namespace Um.Impl
{
    using Microsoft.Extensions.Configuration;
    using Um.Contracts;

    public class Handler : IHandle
    {
        public void Handle(IConfiguration c)
        {
        }
    }
}
""",
    "src/Um/Use/HandleUser.cs": """using Microsoft.Extensions.Configuration;
using Um.Contracts;

namespace Um.Use;

public class HandleUser
{
    private readonly IHandle _h;

    public void Go(IConfiguration c)
    {
        _h.Handle(c);
    }
}
""",
    # a4: System.Timers.Timer (a file using, a project without implicit usings) against an implicit-usings project's
    # Timer (System.Threading.Timer): an overload, the public base member implements the interface
    "src/Ut1/Ut1.csproj": CSPROJ,
    "src/Ut1/ITick.cs": """using System.Timers;

namespace Ut1;

public interface ITick
{
    void Tick(Timer t);
}

public class TickBase
{
    public void Tick(System.Timers.Timer t)
    {
    }
}

public class TickUser
{
    private readonly ITick _t;

    public void Go(System.Timers.Timer x)
    {
        _t.Tick(x);
    }
}
""",
    # a8's interface: the same shape, implemented in a project whose ImplicitUsings the pass cannot read
    "src/Ut1/ITickQ.cs": """using System.Timers;

namespace Ut1;

public interface ITickQ
{
    void Tick(Timer t);
}

public class TickBaseQ
{
    public void Tick(System.Timers.Timer t)
    {
    }
}

public class TickUserQ
{
    private readonly ITickQ _t;

    public void Go(System.Timers.Timer x)
    {
        _t.Tick(x);
    }
}
""",
    "src/Ut2/Ut2.csproj": """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
    <ImplicitUsings>enable</ImplicitUsings>
  </PropertyGroup>
  <ItemGroup>
    <ProjectReference Include="..\\Ut1\\Ut1.csproj" />
  </ItemGroup>
</Project>
""",
    "src/Ut2/Ticker.cs": """namespace Ut2;

public class Ticker : Ut1.TickBase, Ut1.ITick
{
    public void Tick(Timer t)
    {
    }
}
""",
    "src/Uq/Uq.csproj": """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
    <ImplicitUsings Condition="'$(CI)' == 'true'">enable</ImplicitUsings>
  </PropertyGroup>
  <ItemGroup>
    <ProjectReference Include="..\\Ut1\\Ut1.csproj" />
  </ItemGroup>
</Project>
""",
    "src/Uq/TickerQ.cs": """namespace Uq;

public class TickerQ : Ut1.TickBaseQ, Ut1.ITickQ
{
    public void Tick(Timer t)
    {
    }
}
""",
    # a6: `<Using Remove="System.Threading"/>` (IRem.cs compiles only because it applies), implemented from a project
    # that keeps System.Threading
    "src/Ur/Ur.csproj": """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
    <ImplicitUsings>enable</ImplicitUsings>
  </PropertyGroup>
  <ItemGroup>
    <Using Remove="System.Threading" />
  </ItemGroup>
</Project>
""",
    "src/Ur/IRem.cs": """using System.Timers;

namespace Ur;

public interface IRem
{
    void Tick(Timer t);
}

public class RemBase
{
    public void Tick(System.Timers.Timer t)
    {
    }
}

public class RemUser
{
    private readonly IRem _r;

    public void Go(System.Timers.Timer x)
    {
        _r.Tick(x);
    }
}
""",
    "src/Un/Un.csproj": """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
    <ImplicitUsings>enable</ImplicitUsings>
  </PropertyGroup>
  <ItemGroup>
    <ProjectReference Include="..\\Ur\\Ur.csproj" />
    <ProjectReference Include="..\\Upt\\Upt.csproj" />
  </ItemGroup>
</Project>
""",
    "src/Un/RemTicker.cs": """namespace Un;

public class RemTicker : Ur.RemBase, Ur.IRem
{
    public void Tick(Timer t)
    {
    }
}
""",
    "src/Un/Ticker7.cs": """namespace Un;

public class Ticker7 : Upt.Contracts.Tick7Base, Upt.Contracts.ITick7
{
    public void Tick(Timer t)
    {
    }
}
""",
    # a7: ImplicitUsings from Directory.Build.props (the project file does not mention it)
    "src/Up/Directory.Build.props": """<Project>
  <PropertyGroup>
    <ImplicitUsings>enable</ImplicitUsings>
  </PropertyGroup>
</Project>
""",
    "src/Up/Up.csproj": _NET8,
    "src/Up/Contracts/IPut7.cs": """namespace Up.Contracts;

public interface IPut7
{
    void Put(CancellationToken ct);
}
""",
    "src/Up/Impl/Putter7.cs": """namespace Up.Impl
{
    using System.Threading;
    using Up.Contracts;

    public class Putter7 : IPut7
    {
        public void Put(CancellationToken ct)
        {
        }
    }
}
""",
    "src/Up/Use/Put7User.cs": """using Up.Contracts;

namespace Up.Use;

public class Put7User
{
    private readonly IPut7 _p;

    public void Go(CancellationToken ct)
    {
        _p.Put(ct);
    }
}
""",
    # a7b / a7c: Directory.Build.props enables, Directory.Build.targets disables (the last assignment wins)
    "src/Upt/Directory.Build.props": """<Project>
  <PropertyGroup>
    <ImplicitUsings>enable</ImplicitUsings>
  </PropertyGroup>
</Project>
""",
    "src/Upt/Directory.Build.targets": """<Project>
  <PropertyGroup>
    <ImplicitUsings>disable</ImplicitUsings>
  </PropertyGroup>
</Project>
""",
    "src/Upt/Upt.csproj": _NET8,
    "src/Upt/Contracts/IPut7b.cs": """using System.Threading;

namespace Upt.Contracts;

public interface IPut7b
{
    void Put(CancellationToken ct);
}
""",
    "src/Upt/Contracts/ITick7.cs": """using System.Timers;

namespace Upt.Contracts;

public interface ITick7
{
    void Tick(Timer t);
}

public class Tick7Base
{
    public void Tick(System.Timers.Timer t)
    {
    }
}

public class Tick7User
{
    private readonly ITick7 _t;

    public void Go(System.Timers.Timer x)
    {
        _t.Tick(x);
    }
}
""",
    "src/Upt/Impl/Putter7b.cs": """namespace Upt.Impl
{
    using System.Threading;
    using Upt.Contracts;

    public class Putter7b : IPut7b
    {
        public void Put(CancellationToken ct)
        {
        }
    }
}
""",
    "src/Upt/Use/Put7bUser.cs": """using System.Threading;
using Upt.Contracts;

namespace Upt.Use;

public class Put7bUser
{
    private readonly IPut7b _p;

    public void Go(CancellationToken ct)
    {
        _p.Put(ct);
    }
}
""",
    # b1-b5: aliases by the namespace declaration that holds them
    "src/Ba/Ba.csproj": CSPROJ,
    "src/Ba/GlobalUsings.cs": """global using Tok = System.Threading.CancellationToken;
""",
    "src/Ba/Models/Order.cs": """namespace Ba.Models;

public class Order
{
    public void Ship()
    {
    }
}
""",
    "src/Ba/Legacy/Order.cs": """namespace Ba.Legacy;

public class Order
{
    public void Ship()
    {
    }
}
""",
    "src/Ba/Use/OrderUser.cs": """using Order = Ba.Legacy.Order;

namespace Ba.Use
{
    using Ba.Models;

    public class OrderUser
    {
        private readonly Order _o;

        public void Go()
        {
            _o.Ship();
        }
    }
}
""",
    "src/Ba/Use/OrderUser2.cs": """using Order = Ba.Legacy.Order;

namespace Ba.Use
{
    public class OrderUser2
    {
        private readonly Order _o;

        public void Go()
        {
            _o.Ship();
        }
    }
}
""",
    "src/Ba/C/Inner.cs": """using Ba.Models;

namespace Ba.C
{
    using Order = Ba.Legacy.Order;

    public class InnerUser
    {
        private readonly Order _o;

        public void Go()
        {
            _o.Ship();
        }
    }
}
""",
    "src/Ba/Data/SqlRepo.cs": """namespace Ba.Data;

public class SqlRepo
{
    public void Save()
    {
    }
}
""",
    "src/Ba/B/Repo.cs": """namespace Ba.B;

public class Repo
{
    public void Save()
    {
    }
}
""",
    "src/Ba/Blocks.cs": """namespace Ba.A
{
    using Repo = Ba.Data.SqlRepo;

    public class UA
    {
        private readonly Repo _r;

        public void Go()
        {
            _r.Save();
        }
    }
}

namespace Ba.B
{
    public class UB
    {
        private readonly Repo _r;

        public void Go()
        {
            _r.Save();
        }
    }
}
""",
    # b3b: a sibling block finds Repo only through the compilation unit's using (the other block's alias never leaks
    # there, where it would beat that using)
    "src/Ba/Data2/Repo.cs": """namespace Ba.Data2;

public class Repo
{
    public void Save()
    {
    }
}
""",
    "src/Ba/Blocks2.cs": """using Ba.Data2;

namespace Ba.E
{
    using Repo = Ba.Data.SqlRepo;

    public class UE
    {
        private readonly Repo _r;

        public void Go()
        {
            _r.Save();
        }
    }
}

namespace Ba.F
{
    public class UF
    {
        private readonly Repo _r;

        public void Go()
        {
            _r.Save();
        }
    }
}
""",
    "src/Ba/Sig/A5.cs": """using System.Threading;

namespace Ba.Sig;

public class A5
{
    public virtual void Run(CancellationToken ct)
    {
    }
}

public class A5x
{
    public virtual void Run(CancellationToken ct)
    {
    }
}

public class SigUser
{
    private readonly A5 _a;
    private readonly A5x _ax;

    public void Go(CancellationToken ct)
    {
        _a.Run(ct);
    }

    public void GoX(CancellationToken ct)
    {
        _ax.Run(ct);
    }
}
""",
    "src/Ba/Sig/B5.cs": """namespace Ba.Sig
{
    using Ba.Models;

    public class B5 : A5
    {
        public override void Run(Tok t)
        {
        }
    }
}
""",
    "src/Ba/Sig/B5x.cs": """namespace Ba.Sig
{
    using Ext.Stuff;

    public class B5x : A5x
    {
        public override void Run(Tok t)
        {
        }
    }
}
""",
    # c1-c3b: the members of an enclosing namespace an external assembly declares types in
    "src/Uc/Uc.csproj": CSPROJ,
    "src/Uc/Timers.cs": """using System.Threading;

namespace System.Timers.Custom;

public interface ITimed
{
    void Use(Timer t);
}

public class TimedBase
{
    public void Use(System.Timers.Timer t)
    {
    }
}

public class TimedUser
{
    private readonly ITimed _t;

    public void Go(System.Timers.Timer x)
    {
        _t.Use(x);
    }
}
""",
    "src/Uc/Timed.cs": """using System.Threading;
using System.Timers.Custom;

namespace Uc;

public class Timed : TimedBase, ITimed
{
    public void Use(Timer t)
    {
    }
}
""",
    "src/Uc/Sink.cs": """using System.Text;

namespace Microsoft.Extensions.Logging;

public interface ILogSink
{
    void Write(ILogger l);
}
""",
    "src/Uc/Sink3.cs": """using System.Text;

namespace Microsoft.Extensions.Logging.Custom;

public interface ILogSink3
{
    void Write(ILogger l);
}
""",
    "src/Uc/Sink3b.cs": """using Microsoft.Extensions.Logging;

namespace Microsoft.Extensions.Custom;

public interface ILogSink3b
{
    void Write(ILogger l);
}
""",
    "src/Uc/SinkImpl.cs": """using Microsoft.Extensions.Logging;

namespace Uc;

public class Sink : ILogSink
{
    public void Write(ILogger l)
    {
    }
}
""",
    "src/Uc/Sink3Impl.cs": """using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Logging.Custom;

namespace Uc;

public class Sink3 : ILogSink3
{
    public void Write(ILogger l)
    {
    }
}
""",
    "src/Uc/Sink3bImpl.cs": """using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Custom;

namespace Uc;

public class Sink3b : ILogSink3b
{
    public void Write(ILogger l)
    {
    }
}
""",
    "src/Uc/SinkUser.cs": """using Microsoft.Extensions.Logging;

namespace Uc;

public class SinkUser
{
    private readonly ILogSink _s;
    private readonly Microsoft.Extensions.Logging.Custom.ILogSink3 _s3;
    private readonly Microsoft.Extensions.Custom.ILogSink3b _s3b;

    public void Go(ILogger l)
    {
        _s.Write(l);
    }

    public void Go3(ILogger l)
    {
        _s3.Write(l);
    }

    public void Go3b(ILogger l)
    {
        _s3b.Write(l);
    }
}
""",
}
# The reader residuals round, part 2 (section X): an explicit interface implementation (never overridden; another
# interface's never implements this one), a non-public same-signature method (never an implicit implementer), a
# nullable closed type argument (`Cv8<int?>` is no `Cv8<int>`; a class's `?` an annotation), receiver type arguments
# substituted into overload resolution (and the rival bound when the hit is PROVEN inapplicable, never through a
# user-defined conversion). X_EXPECT names the binding Roslyn 4.8 gives each call, with its run-time dispatch (AC1).
RESID = {
    "src/Xd/Xd.csproj": CSPROJ,
    "src/Xd/Runs.cs": """namespace Xd;

public interface IRunD
{
    void Run();
}

public class RunA : IRunD
{
    void IRunD.Run()
    {
    }

    public virtual void Run()
    {
    }
}

public class RunB : RunA
{
    public override void Run()
    {
    }
}

public class RunUser
{
    private readonly IRunD _r;

    public void Go()
    {
        _r.Run();
    }
}
""",
    "src/Xd/Runs2.cs": """namespace Xd;

public interface IRunD2
{
    void Run();
}

public class RunA2 : IRunD2
{
    public virtual void Run()
    {
    }
}

public class RunB2 : RunA2
{
    public override void Run()
    {
    }
}

public class RunUser2
{
    private readonly IRunD2 _r;

    public void Go()
    {
        _r.Run();
    }
}
""",
    "src/Xd/Runs3.cs": """namespace Xd;

public interface IRunD3
{
    void Run();
}

public interface IOther3
{
    void Run();
}

public class RunBase3
{
    public void Run()
    {
    }
}

public class RunC3 : RunBase3, IRunD3, IOther3
{
    void IOther3.Run()
    {
    }
}

public class RunUser3
{
    private readonly IRunD3 _r;

    public void Go()
    {
        _r.Run();
    }
}
""",
    "src/Xe/Xe.csproj": CSPROJ,
    "src/Xe/Runs.cs": """namespace Xe;

public interface IRunE
{
    void Run();
}

public class PubBase
{
    public void Run()
    {
    }
}

public class PrivImpl : PubBase, IRunE
{
    private new void Run()
    {
    }
}

public class IntImpl : PubBase, IRunE
{
    internal new void Run()
    {
    }
}

public class RunUserE
{
    private readonly IRunE _r;

    public void Go()
    {
        _r.Run();
    }
}
""",
    "src/Xe/Runs2.cs": """namespace Xe;

public interface IRunE2
{
    void Run();
}

public class PubImpl : PubBase, IRunE2
{
    public new void Run()
    {
    }
}

public class RunUserE2
{
    private readonly IRunE2 _r;

    public void Go()
    {
        _r.Run();
    }
}
""",
    "src/Xe/Runs3.cs": """namespace Xe;

public interface IRunE3
{
    void Run()
    {
    }
}

public class PrivOnly : IRunE3
{
    private void Run()
    {
    }
}

public class RunUserE3
{
    private readonly IRunE3 _r;

    public void Go()
    {
        _r.Run();
    }
}
""",
    "src/Xf/Xf.csproj": """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <Nullable>enable</Nullable>
  </PropertyGroup>
</Project>
""",
    "src/Xf/Cv8.cs": """namespace Xf;

public class Ord9
{
}

public struct S9
{
}

public class Cv8<T>
{
    public virtual void M8()
    {
    }
}

public class Cv8Int : Cv8<int>
{
    public override void M8()
    {
    }
}

public class Cv8Nul : Cv8<int?>
{
    public override void M8()
    {
    }
}

public class Cv8Ord : Cv8<Ord9>
{
    public override void M8()
    {
    }
}

public class Cv8S : Cv8<S9>
{
    public override void M8()
    {
    }
}

public class Cv8User
{
    private readonly Cv8<int?> _c;
    private readonly Cv8<Ord9?> _co;
    private readonly Cv8<S9?> _cs;

    public void Go()
    {
        _c.M8();
    }

    public void GoOrd()
    {
        _co.M8();
    }

    public void GoS()
    {
        _cs.M8();
    }
}

public class Holder<T>
{
    private readonly Cv8<T?> _c;

    public void Go()
    {
        _c.M8();
    }
}
""",
    "src/Xf/Repo9.cs": """namespace Xf;

public interface IRepo9<T>
{
    T Get();
}

public class IntRepo9 : IRepo9<int>
{
    public int Get()
    {
        return 0;
    }
}

public class NulRepo9 : IRepo9<int?>
{
    public int? Get()
    {
        return null;
    }
}

public class Repo9User
{
    private readonly IRepo9<int?> _r;

    public void Go()
    {
        _r.Get();
    }
}
""",
    "src/Xg/Xg.csproj": CSPROJ,
    "src/Xg/Saves.cs": """namespace Xg;

public class Order
{
}

public class Cart
{
}

public class OrderC
{
    public static implicit operator Cart(OrderC o)
    {
        return new Cart();
    }
}

public class SaveBase
{
    public void Save(Order o)
    {
    }
}

public class Repo11<T> : SaveBase
{
    public void Save(T item)
    {
    }
}

public class G1User
{
    private readonly Repo11<Order> _r;
    private readonly Repo11<Cart> _rc;

    public void Go(Order o)
    {
        _r.Save(o);
    }

    public void GoCart(Order o)
    {
        _rc.Save(o);
    }
}
""",
    "src/Xg/Stores.cs": """namespace Xg;

public class Store11<T>
{
    public void Save(T item)
    {
    }
}

public class CartStore11<T> : Store11<T>
{
    public void Save(Cart c)
    {
    }
}

public class G2User
{
    private readonly CartStore11<OrderC> _s;
    private readonly CartStore11<Order> _so;

    public void Go(OrderC oc)
    {
        _s.Save(oc);
    }

    public void GoOrder(Order o)
    {
        _so.Save(o);
    }
}

public class H11<TX>
{
    private readonly CartStore11<TX> _s;

    public void GoT(TX x)
    {
        _s.Save(x);
    }

    public void GoCart(Cart c)
    {
        _s.Save(c);
    }
}
""",
    # g2 and accessibility: C#'s member lookup keeps ACCESSIBLE members only (spec 12.5), so a private / protected /
    # default-private base overload the arguments fit is skipped and Root12's Put(object) binds; an internal /
    # protected internal / public one of the caller's project binds. A partial class whose other part (another
    # node) holds a better overload the pass cannot judge (Put(IFormattable) for an int) decides nothing.
    "src/Xg/Access.cs": """namespace Xg;

public class Root12
{
    public void Put(object o)
    {
    }
}

public class Hid12 : Root12
{
    private void Put(Order o)
    {
    }
}

public class Mid12 : Hid12
{
    public void Put(Cart c)
    {
    }
}

public class Prot12 : Root12
{
    protected void Put(Order o)
    {
    }
}

public class MidP12 : Prot12
{
    public void Put(Cart c)
    {
    }
}

public class Int12 : Root12
{
    internal void Put(Order o)
    {
    }
}

public class MidI12 : Int12
{
    public void Put(Cart c)
    {
    }
}

public class PI12 : Root12
{
    protected internal void Put(Order o)
    {
    }
}

public class MidPI12 : PI12
{
    public void Put(Cart c)
    {
    }
}

public class Dflt12 : Root12
{
    void Put(Order o)
    {
    }
}

public class MidD12 : Dflt12
{
    public void Put(Cart c)
    {
    }
}

public class Pub12 : Root12
{
    public void Put(Order o)
    {
    }
}

public class MidB12 : Pub12
{
    public void Put(Cart c)
    {
    }
}

public partial class Part13
{
    public void Put(object o)
    {
    }
}

public class MidQ13 : Part13
{
    public void Put(Cart c)
    {
    }
}

public class G3User
{
    private readonly Mid12 _m;
    private readonly MidP12 _p;
    private readonly MidI12 _i;
    private readonly MidPI12 _pi;
    private readonly MidD12 _d;
    private readonly MidB12 _b;
    private readonly MidQ13 _q;

    public void GoPriv(Order o)
    {
        _m.Put(o);
    }

    public void GoProt(Order o)
    {
        _p.Put(o);
    }

    public void GoInt(Order o)
    {
        _i.Put(o);
    }

    public void GoProtInt(Order o)
    {
        _pi.Put(o);
    }

    public void GoDefault(Order o)
    {
        _d.Put(o);
    }

    public void GoPub(Order o)
    {
        _b.Put(o);
    }

    public void GoPart(int n)
    {
        _q.Put(n);
    }

    public void GoPartOrder(Order o)
    {
        _q.Put(o);
    }
}
""",
    "src/Xg/Access2.cs": """namespace Xg;

public partial class Part13
{
    public void Put(System.IFormattable f)
    {
    }
}
""",
}
# The soundness round (section Y): a wrong True is what REMOVES a true engine tuple, so each decision that reaches a
# tuple has its bug shape here (the teeth: the engine's true same-file tuple), beside a positive sibling where the
# proof stands. Roslyn 4.8's bindings with their run-time dispatch (the kit's reference, on this exact tree) are the
# expectation. A vendor/ directory stands in for a NuGet package or an unread assembly: DeepMap never reads it, the
# reference compiles it. Every shape has its own namespace root (package roots and used_ext fold over the whole tree).
def RN(root: str) -> str:
    """A RootNamespace property: the project owns that namespace root (R32)."""
    return f"<RootNamespace>{root}</RootNamespace>"


def _np(props=(), items=(), tf="net8.0", sdk="Microsoft.NET.Sdk") -> str:
    """A .csproj: its target framework, `props` in one PropertyGroup, `items` in one ItemGroup."""
    pg = ([f"<TargetFramework>{tf}</TargetFramework>"] if tf else []) + list(props)
    out = [f'<Project Sdk="{sdk}">', "  <PropertyGroup>"] + [f"    {p}" for p in pg] + ["  </PropertyGroup>"]
    if items:
        out += ["  <ItemGroup>"] + [f"    {i}" for i in items] + ["  </ItemGroup>"]
    return "\n".join(out + ["</Project>"]) + "\n"


_IU = "<ImplicitUsings>enable</ImplicitUsings>"


def _tick(ns, n, param="Timer", usings="", base_param="System.Threading.Timer", go_param=None):
    """A file-scoped namespace's interface, the public base member that implements it for a class that does not, and
    the interface call (the engine's same-file tuple to the base member is the true one)."""
    return f"""{usings}namespace {ns};

public interface ITick{n}
{{
    void Tick({param} t);
}}

public class TickBase{n}
{{
    public void Tick({base_param} t)
    {{
    }}
}}

public class TickUser{n}
{{
    private readonly ITick{n} _t;

    public void Go({go_param or base_param} x)
    {{
        _t.Tick(x);
    }}
}}
"""


def _ivt(ns, n):
    """A project's public base member and its `internal new` one: from another assembly C# binds the base's, unless the
    caller's assembly is a friend."""
    return f"""namespace {ns};

public class BaseV{n}
{{
    public void Intl(int o)
    {{
    }}
}}

public class DerV{n} : BaseV{n}
{{
    internal new void Intl(int o)
    {{
    }}
}}
"""


def _ivt_user(ns, lib, n):
    return f"""namespace {ns};

public class FriendV{n}
{{
    private readonly {lib}.DerV{n} _d = new {lib}.DerV{n}();

    public void Go()
    {{
        _d.Intl(1);
    }}
}}
"""


_IVT_ATTR = 'using System.Runtime.CompilerServices;\n\n[assembly: InternalsVisibleTo("{}")]\n'
_VERSION_CS = '[assembly: System.Reflection.AssemblyMetadata("v", "1")]\n'


def _msb(*lines):
    """A bare MSBuild file."""
    return "<Project>\n" + "".join(f"  {ln}\n" for ln in lines) + "</Project>\n"


def _proj_ref(lib):
    return f'<ProjectReference Include="../{lib}/{lib}.csproj" />'


def _ticker(ns, n, lib, param="Timer"):
    """A class of another project with its own `Tick`: the interface call runs it only if `param` is the interface's."""
    return f"""namespace {ns};

public class Ticker{n} : {lib}.TickBase{n}, {lib}.ITick{n}
{{
    public void Tick({param} t)
    {{
    }}
}}
"""


SOUND = {
    # ---- R1 (S1): a project type does not make a namespace project-only. sh1: `namespace System.Timers` holds a
    # project class, and System.Timers.Timer, a member of that enclosing namespace, beats the block's using
    "src/Ya/Ya.csproj": _np(),
    "src/Ya/Tick1.cs": _tick("Ya", "1", usings="using System.Threading;\n\n"),
    "src/Ya/Ticker1.cs": """namespace System.Timers
{
    using System.Threading;
    using Ya;

    public class Ticker1 : TickBase1, ITick1
    {
        public void Tick(Timer t)
        {
        }
    }
}
""",
    # the positive sibling: the same class spelling System.Threading.Timer: the proof stands
    "src/Ya/Tick1b.cs": _tick("Ya", "1b", usings="using System.Threading;\n\n"),
    "src/Ya/Ticker1b.cs": """namespace System.Timers
{
    using Ya;

    public class Ticker1b : TickBase1b, ITick1b
    {
        public void Tick(System.Threading.Timer t)
        {
        }
    }
}
""",
    # the class's own namespace is the qualifier: its Timer IS System.Timers.Timer (an enclosing namespace proves it)
    "src/Ya/Tick1d.cs": _tick("Ya", "1d", param="System.Timers.Timer", base_param="System.Timers.Timer"),
    "src/Ya/Ticker1d.cs": """namespace System.Timers
{
    public class Ticker1d : Ya.TickBase1d, Ya.ITick1d
    {
        public void Tick(Timer t)
        {
        }
    }
}
""",
    # the binder's members() walk: the same namespace's Timer beats the compilation unit's alias of a project class
    "src/Ya/Tick1c.cs": _tick("Ya", "1c", param="ProjTimer", base_param="ProjTimer") + """
public class ProjTimer
{
}
""",
    "src/Ya/Ticker1c.cs": """using Timer = Ya.ProjTimer;

namespace System.Timers
{
    public class Ticker1c : Ya.TickBase1c, Ya.ITick1c
    {
        public void Tick(Timer t)
        {
        }
    }
}
""",
    # ---- R2 (S2): package ids. tr1: a NuGet package fills the project's own namespace Acme.Core (vendor/ stands in
    # for the package: DeepMap never reads it, the reference compiles it); `using Acme.Core;` may supply Timer
    "src/Yb/Yb.csproj": _np(items=['<PackageReference Include="Acme.Core" Version="1.0.0" />']),
    "src/Yb/vendor/AcmeCore.cs": """namespace Acme.Core
{
    public class Timer
    {
    }
}

namespace Acme.Ext
{
    public class Timer
    {
    }
}

namespace Acme.Sub
{
    public class Timer
    {
    }
}
""",
    "src/Yb/Core/CoreExt.cs": """namespace Acme.Core
{
    public static class CoreExt
    {
        public static int Twice(this int x)
        {
            return x * 2;
        }
    }
}
""",
    "src/Yb/Web/TickT.cs": _tick("Acme.Web", "T", usings="using System.Threading;\n\n"),
    "src/Yb/Web/TickerT.cs": """using System.Threading;

namespace Acme.Web.Jobs
{
    using Acme.Core;

    public class TickerT : Acme.Web.TickBaseT, Acme.Web.ITickT
    {
        public void Tick(Timer t)
        {
        }
    }
}
""",
    # the root decides, not the exact id: Acme.Ext is no package id, its root is
    "src/Yb/Core/ExtHelp.cs": """namespace Acme.Ext
{
    public static class ExtHelp
    {
        public static int Thrice(this int x)
        {
            return x * 3;
        }
    }
}
""",
    "src/Yb/Web/TickE.cs": _tick("Acme.Web", "E", usings="using System.Threading;\n\n"),
    "src/Yb/Web/TickerE.cs": """using System.Threading;

namespace Acme.Web.JobsE
{
    using Acme.Ext;

    public class TickerE : Acme.Web.TickBaseE, Acme.Web.ITickE
    {
        public void Tick(Timer t)
        {
        }
    }
}
""",
    # R1 with R2: a package root's namespace encloses the class: Acme.Sub.Timer before the block's using
    "src/Yb/Web/TickS.cs": _tick("Acme.Web", "S", usings="using System.Threading;\n\n"),
    "src/Yb/Web/TickerS.cs": """namespace Acme.Sub
{
    using System.Threading;

    public class TickerS : Acme.Web.TickBaseS, Acme.Web.ITickS
    {
        public void Tick(Timer t)
        {
        }
    }
}
""",
    # the positive sibling: no package anywhere under the root Zeta: `using Zeta.Core;` supplies project types only
    "src/Yb2/Yb2.csproj": _np(),
    "src/Yb2/Core/CoreExt.cs": """namespace Zeta.Core
{
    public static class CoreExtZ
    {
        public static int Twice(this int x)
        {
            return x * 2;
        }
    }
}
""",
    "src/Yb2/Web/TickZ.cs": _tick("Zeta.Web", "Z", usings="using System.Threading;\n\n"),
    "src/Yb2/Web/TickerZ.cs": """using System.Threading;

namespace Zeta.Web.Jobs
{
    using Zeta.Core;

    public class TickerZ : Zeta.Web.TickBaseZ, Zeta.Web.ITickZ
    {
        public void Tick(Timer t)
        {
        }
    }
}
""",
    # R2's second half: an external name where NO level may supply it (every eff empty, no capable level) proves the
    # model wrong: two namespaces of an unread assembly (vendor/) each hold a Clock
    "src/Yc/Yc.csproj": _np(),
    "src/Yc/vendor/Clocks.cs": """namespace Yc.A
{
    public class Clock
    {
    }
}

namespace Yc.B
{
    public class Clock
    {
    }
}
""",
    "src/Yc/A/Clk.cs": """namespace Yc.A;

public interface IClk
{
    void Tick(Clock c);
}

public class ClkBase
{
    public void Tick(Clock c)
    {
    }
}

public class ClkUser
{
    private readonly IClk _c;

    public void Go(Clock x)
    {
        _c.Tick(x);
    }
}
""",
    "src/Yc/B/ClkImpl.cs": """namespace Yc.B;

public class ClkImpl : Yc.A.ClkBase, Yc.A.IClk
{
    public void Tick(Clock c)
    {
    }
}
""",
    # ---- R3 (S3): an x tuple is unknown, never "the same". x2: one alias name of two targets, both unknown to the
    # alias walk (a nearer external using may hold the name)
    "src/Yd/Yd.csproj": _np(),
    "src/Yd/vendor/Ext.cs": """namespace ExtD.One
{
    public class Marker1
    {
    }
}

namespace ExtD.Two
{
    public class Marker2
    {
    }
}

namespace Yd.Q.A.Core
{
    public class Tok
    {
    }
}

namespace Yd.Q.B.Core
{
    public class Tok
    {
    }
}

namespace Yd.R.A.Core
{
    public class Tok
    {
    }
}

namespace Yd.R.B.Core
{
    public class Tok
    {
    }
}

namespace Yd.G.A.Core
{
    public class Box<T>
    {
    }
}

namespace Yd.G.B.Core
{
    public class Box<T>
    {
    }
}
""",
    "src/Yd/IRun.cs": """using Tok = System.Threading.CancellationToken;

namespace Yd
{
    using ExtD.One;

    public interface IRun
    {
        void Run(Tok t);
    }

    public class RunBase
    {
        public void Run(System.Threading.CancellationToken t)
        {
        }
    }

    public class RunUser
    {
        private readonly IRun _r;

        public void Go(System.Threading.CancellationToken t)
        {
            _r.Run(t);
        }
    }
}
""",
    "src/Yd/Impl.cs": """using Tok = System.Timers.Timer;

namespace Yd
{
    using ExtD.Two;

    public class Impl : RunBase, IRun
    {
        public void Run(Tok t)
        {
        }
    }
}
""",
    # the positive sibling: one alias of one target at both compilation units, nothing nearer
    "src/Yd/IRun2.cs": """using Tok = System.Threading.CancellationToken;

namespace Yd.P;

public interface IRun2
{
    void Run(Tok t);
}

public class RunBase2
{
    public void Run(System.Threading.CancellationToken t)
    {
    }
}

public class RunUser2
{
    private readonly IRun2 _r;

    public void Go(System.Threading.CancellationToken t)
    {
        _r.Run(t);
    }
}
""",
    "src/Yd/Impl2.cs": """using Tok = System.Threading.CancellationToken;

namespace Yd.P;

public class Impl2 : RunBase2, IRun2
{
    public void Run(Tok t)
    {
    }
}
""",
    # x1: two namespace blocks of one file each declare an Order: a name the binder cannot place is unknown
    "src/Yd/Blocks.cs": """namespace Yd.X.A
{
    public class Order
    {
    }

    public interface IUse
    {
        void M(Order o);
    }

    public class BaseU
    {
        public void M(Order o)
        {
        }
    }

    public class UseUser
    {
        private readonly IUse _u;

        public void Go(Order x)
        {
            _u.M(x);
        }
    }
}

namespace Yd.X.B
{
    public class Order
    {
    }

    public class ImplX : Yd.X.A.BaseU, Yd.X.A.IUse
    {
        public void M(Order o)
        {
        }
    }
}
""",
    # a generic alias target: the same text in two namespace blocks names two types (it is relative to its block) ...
    "src/Yd/GenA.cs": """namespace Yd.G.A
{
    using Bx = Core.Box<int>;

    public interface IBx
    {
        void Put(Bx b);
    }

    public class BxBase
    {
        public void Put(Core.Box<int> b)
        {
        }
    }

    public class BxUser
    {
        private readonly IBx _b;

        public void Go(Core.Box<int> x)
        {
            _b.Put(x);
        }
    }
}
""",
    "src/Yd/GenB.cs": """namespace Yd.G.B
{
    using Bx = Core.Box<int>;

    public class BxImpl : Yd.G.A.BxBase, Yd.G.A.IBx
    {
        public void Put(Bx b)
        {
        }
    }
}
""",
    # ... and in one lookup scope one type: the positive sibling (two compilation units' aliases of one closed type)
    "src/Yd/GenC.cs": """using Li = System.Collections.Generic.List<int>;

namespace Yd.G.C;

public interface ILi
{
    void Put(Li b);
}

public class LiBase
{
    public void Put(System.Collections.Generic.List<int> b)
    {
    }
}

public class LiUser
{
    private readonly ILi _b;

    public void Go(System.Collections.Generic.List<int> x)
    {
        _b.Put(x);
    }
}
""",
    "src/Yd/GenD.cs": """using Li = System.Collections.Generic.List<int>;

namespace Yd.G.C;

public class LiImpl : LiBase, ILi
{
    public void Put(Li b)
    {
    }
}
""",
    # a qualifier is relative too: `Core.Tok` written in two namespaces names two types, spelled so or through a block
    # alias of that text
    "src/Yd/QualA.cs": """namespace Yd.Q.A
{
    public interface IQq
    {
        void Put(Core.Tok t);
    }

    public class QqBase
    {
        public void Put(Core.Tok t)
        {
        }
    }

    public class QqUser
    {
        private readonly IQq _q;

        public void Go(Core.Tok x)
        {
            _q.Put(x);
        }
    }
}
""",
    "src/Yd/QualB.cs": """namespace Yd.Q.B
{
    public class QqImpl : Yd.Q.A.QqBase, Yd.Q.A.IQq
    {
        public void Put(Core.Tok t)
        {
        }
    }
}
""",
    "src/Yd/RelA.cs": """namespace Yd.R.A
{
    using Tk = Core.Tok;

    public interface IQa
    {
        void Put(Tk t);
    }

    public class QaBase
    {
        public void Put(Core.Tok t)
        {
        }
    }

    public class QaUser
    {
        private readonly IQa _q;

        public void Go(Core.Tok x)
        {
            _q.Put(x);
        }
    }
}
""",
    "src/Yd/RelB.cs": """namespace Yd.R.B
{
    using Tk = Core.Tok;

    public class QaImpl : Yd.R.A.QaBase, Yd.R.A.IQa
    {
        public void Put(Tk t)
        {
        }
    }
}
""",
    # cs_same_sig reaches the same rule: the derived class's Save(Tok) is no overload of the base's "proven" alike
    "src/Yd/SaveBase.cs": """using Tok = System.Threading.CancellationToken;

namespace Yd.S
{
    using ExtD.One;

    public class SaveBase
    {
        public void Save(Tok t)
        {
        }
    }
}
""",
    "src/Yd/SaveDer.cs": """using Tok = System.Timers.Timer;

namespace Yd.S
{
    using ExtD.Two;

    public class SaveDer : SaveBase
    {
        public void Save(Tok t)
        {
        }
    }

    public class SaveUser
    {
        private readonly SaveDer _d;

        public void Go(System.Threading.CancellationToken t)
        {
            _d.Save(t);
        }
    }
}
""",
    # two groups' unreadable MSBuild aliases of one name, two targets
    "src/Ye1/Ye1.csproj": _np(items=[
        '<Using Include="System.Threading.CancellationToken" Alias="Tk3" Condition="\'$(DeepMapCi)\' != \'true\'" />']),
    "src/Ye1/Tk.cs": _tick("Ye1", "K", param="Tk3", base_param="System.Threading.CancellationToken"),
    "src/Ye2/Ye2.csproj": _np(items=[
        '<Using Include="System.Timers.Timer" Alias="Tk3" Condition="\'$(DeepMapCi)\' != \'true\'" />',
        '<ProjectReference Include="..\\Ye1\\Ye1.csproj" />']),
    "src/Ye2/TickerK.cs": """namespace Ye2;

public class TickerK : Ye1.TickBaseK, Ye1.ITickK
{
    public void Tick(Tk3 t)
    {
    }
}
""",
    # ---- R4 (S4): MSBuild keeps a property's white space: a multi-line <ImplicitUsings> is not `enable` (ws2)
    "src/Yf1/Yf1.csproj": _np(["<ImplicitUsings>\n      enable\n    </ImplicitUsings>"]),
    "src/Yf1/TickW.cs": _tick("Yf1", "W", usings="using System.Timers;\n\n", base_param="System.Timers.Timer"),
    "src/Yf2/Yf2.csproj": _np(items=['<ProjectReference Include="..\\Yf1\\Yf1.csproj" />']),
    "src/Yf2/TickerW.cs": """namespace Yf2
{
    using System.Threading;

    public class TickerW : Yf1.TickBaseW, Yf1.ITickW
    {
        public void Tick(Timer t)
        {
        }
    }
}
""",
    # the positive sibling: `Enable` / `TRUE` compare case-insensitively: both groups hold System.Threading
    "src/Yf3/Yf3.csproj": _np(["<ImplicitUsings>Enable</ImplicitUsings>"]),
    "src/Yf3/TickV.cs": _tick("Yf3", "V"),
    "src/Yf4/Yf4.csproj": _np(["<ImplicitUsings>TRUE</ImplicitUsings>"],
                              ['<ProjectReference Include="..\\Yf3\\Yf3.csproj" />']),
    "src/Yf4/TickerV.cs": """namespace Yf4;

public class TickerV : Yf3.TickBaseV, Yf3.ITickV
{
    public void Tick(Timer t)
    {
    }
}
""",
    # ---- R5 (S5): a wildcard <Using Remove> is a glob on item identities (rm2)
    "src/Yg1/Yg1.csproj": _np([_IU], ['<Using Remove="System.Thread*" />']),
    "src/Yg1/TickG.cs": _tick("Yg1", "G", usings="using System.Timers;\n\n", base_param="System.Timers.Timer"),
    "src/Yg2/Yg2.csproj": _np(items=['<ProjectReference Include="..\\Yg1\\Yg1.csproj" />']),
    "src/Yg2/TickerG.cs": """namespace Yg2
{
    using System.Threading;

    public class TickerG : Yg1.TickBaseG, Yg1.ITickG
    {
        public void Tick(Timer t)
        {
        }
    }
}
""",
    # the positive sibling: inside that group the unseen part is one set: an interface's using at the top, the
    # implementer's in its block
    "src/Yg1/TickH.cs": _tick("Yg1", "H", usings="using System.Timers;\n\n", base_param="System.Timers.Timer"),
    "src/Yg1/Sub/TickerH.cs": """namespace Yg1.Sub
{
    using System.Timers;

    public class TickerH : Yg1.TickBaseH, Yg1.ITickH
    {
        public void Tick(Timer t)
        {
        }
    }
}
""",
    # ---- R6 (S6): a file MSBuild links into a project. lnk: src/Yh/Shared holds no .csproj; App compiles it with its
    # implicit usings
    # (each Yh project's RootNamespace owns the root its files declare: R32 reads a root no project owns as a
    # package's, which would hide every shape here behind an unknown)
    "src/Yh/App/App.csproj": _np([_IU, RN("YhShared")], ['<Compile Include="../Shared/**/*.cs" />']),
    "src/Yh/Shared/TickL.cs": _tick("YhShared", "L", usings="using System.Text;\n\n"),
    "src/Yh/Lib/Lib.csproj": _np([RN("YhLib")], ['<ProjectReference Include="../App/App.csproj" />']),
    "src/Yh/Lib/TickerL.cs": """using System.Text;
using System.Timers;

namespace YhLib;

public class TickerL : YhShared.TickBaseL, YhShared.ITickL
{
    public void Tick(Timer t)
    {
    }
}
""",
    # the positive sibling: both files of one group (the same unseen part), the same using
    "src/Yh/Shared/TickM.cs": _tick("YhShared", "M", usings="using System.Threading;\n\n"),
    "src/Yh/Shared/Sub/TickerM.cs": """using System.Threading;

namespace YhShared.Sub;

public class TickerM : YhShared.TickBaseM, YhShared.ITickM
{
    public void Tick(Timer t)
    {
    }
}
""",
    # two files in no project's directory, linked by two projects (one with implicit usings): no "one group" holds
    "src/Yh/AppO/AppO.csproj": _np([_IU, RN("YhSa")], ['<Compile Include="../SharedA/*.cs" />']),
    "src/Yh/SharedA/TickO.cs": _tick("YhSa", "O", usings="using System.Text;\n\n"),
    "src/Yh/LibO/LibO.csproj": _np([RN("YhSb")], ['<Compile Include="../SharedB/*.cs" />',
                                                  '<ProjectReference Include="../AppO/AppO.csproj" />']),
    "src/Yh/SharedB/TickerO.cs": """using System.Text;
using System.Timers;

namespace YhSb;

public class TickerO : YhSa.TickBaseO, YhSa.ITickO
{
    public void Tick(Timer t)
    {
    }
}
""",
    # a file of ANOTHER project's directory linked in: Core2 removes it from its own compilation, App2 compiles it
    "src/Yh/Core2/Core2.csproj": _np([RN("YhCore2")], ['<Compile Remove="Linked/**" />']),
    "src/Yh/Core2/Own.cs": """namespace YhCore2;

public class Own
{
}
""",
    "src/Yh/Core2/Linked/TickN.cs": _tick("YhCore2", "N", usings="using System.Text;\n\n"),
    "src/Yh/App2/App2.csproj": _np([_IU], ['<Compile Include="../Core2/Linked/*.cs" />']),
    "src/Yh/Lib2/Lib2.csproj": _np([RN("YhLib2")], ['<ProjectReference Include="../App2/App2.csproj" />']),
    "src/Yh/Lib2/TickerN.cs": """using System.Text;
using System.Timers;

namespace YhLib2;

public class TickerN : YhCore2.TickBaseN, YhCore2.ITickN
{
    public void Tick(Timer t)
    {
    }
}
""",
    # a shared project (.projitems): its files sit in no project's directory, and App3 imports them
    "src/Yh/App3/App3.csproj": _np([_IU, RN("YhSh3")]).replace(
        "</Project>", '  <Import Project="..\\Sh3\\Sh3.projitems" Label="Shared" />\n</Project>'),
    "src/Yh/Sh3/Sh3.projitems": """<Project>
  <ItemGroup>
    <Compile Include="$(MSBuildThisFileDirectory)TickP.cs" />
  </ItemGroup>
</Project>
""",
    "src/Yh/Sh3/TickP.cs": _tick("YhSh3", "P", usings="using System.Text;\n\n"),
    "src/Yh/Lib3/Lib3.csproj": _np([RN("YhLib3")], ['<ProjectReference Include="../App3/App3.csproj" />']),
    "src/Yh/Lib3/TickerP.cs": """using System.Text;
using System.Timers;

namespace YhLib3;

public class TickerP : YhSh3.TickBaseP, YhSh3.ITickP
{
    public void Tick(Timer t)
    {
    }
}
""",
    # ---- R7 (S11): one generic interface reached by two paths; an explicit specifier's type arguments (expgen2)
    "src/Yi/Yi.csproj": _np(),
    "src/Yi/Repo.cs": """namespace Yi;

public class Order
{
}

public class Cart
{
}

public interface IRepo<T>
{
    void Save(T item);
}

public class Both : IRepo<Order>, IRepo<Cart>
{
    void IRepo<Order>.Save(Order o)
    {
    }

    void IRepo<Cart>.Save(Cart c)
    {
    }
}

public class OneE : IRepo<Order>
{
    void IRepo<Order>.Save(Order o)
    {
    }

    public void Save(Cart c)
    {
    }
}

public class OneP : IRepo<Order>
{
    public void Save(Order o)
    {
    }
}

public class SubOne : OneE, IRepo<Cart>
{
    public new void Save(Cart c)
    {
    }
}

public class EgUser
{
    private readonly IRepo<Order> _r;
    private readonly IRepo<Cart> _c;

    public void Go(Order o)
    {
        _r.Save(o);
    }

    public void GoCart(Cart c)
    {
        _c.Save(c);
    }
}
""",
    # the positive sibling: no class reaches the interface twice: each closing proves its implementers, an explicit
    # specifier's type arguments among them
    "src/Yi/RepoP.cs": """namespace Yi;

public interface IRepoP<T>
{
    void Save(T item);
}

public class OneEP : IRepoP<Order>
{
    void IRepoP<Order>.Save(Order o)
    {
    }
}

public class OnePP : IRepoP<Order>
{
    public void Save(Order o)
    {
    }
}

public class CartP : IRepoP<Cart>
{
    public void Save(Cart c)
    {
    }
}

public class UserP
{
    private readonly IRepoP<Order> _r;
    private readonly IRepoP<Cart> _c;

    public void Go(Order o)
    {
        _r.Save(o);
    }

    public void GoCart(Cart c)
    {
        _c.Save(c);
    }
}
""",
    # a class holding two constructions by two explicit specifiers, in a member whose signature names no T: each
    # specifier answers for its own construction
    "src/Yi/RepoS.cs": """namespace Yi;

public interface IRepoS<T>
{
    void Run();
}

public class BothS : IRepoS<Order>, IRepoS<Cart>
{
    void IRepoS<Order>.Run()
    {
    }

    void IRepoS<Cart>.Run()
    {
    }
}

public class UserS
{
    private readonly IRepoS<Cart> _c;

    public void Go()
    {
        _c.Run();
    }
}
""",
    # an explicit implementation of the OTHER closing: IRepoQ<Cart> is BaseQ's public member, never TwoE's specifier
    "src/Yi/RepoQ.cs": """namespace Yi;

public interface IRepoQ<T>
{
    void Save(T item);
}

public class TwoE : BaseQ, IRepoQ<Order>, IRepoQ<Cart>
{
    void IRepoQ<Order>.Save(Order o)
    {
    }
}

public class BaseQ
{
    public void Save(Cart c)
    {
    }
}

public class UserQ
{
    private readonly IRepoQ<Cart> _c;

    public void GoCart(Cart c)
    {
        _c.Save(c);
    }
}
""",
    # ---- R8 (S7): System.Net.Http is implicit only outside .NET Framework. net48: HttpClient is the vendor's
    "src/Yj1/Yj1.csproj": _np([_IU, "<LangVersion>latest</LangVersion>"], tf="net48"),
    "src/Yj1/vendor/Http.cs": """namespace Yjv
{
    public class HttpClient
    {
    }
}
""",
    "src/Yj1/Send.cs": """using Yjv;

namespace Yj1;

public interface ISend
{
    void Send(HttpClient c);
}

public class SendBase
{
    public void Send(Yjv.HttpClient c)
    {
    }
}

public class SendUser
{
    private readonly ISend _s;

    public void Go(Yjv.HttpClient x)
    {
        _s.Send(x);
    }
}
""",
    "src/Yj2/Yj2.csproj": _np([_IU], ['<ProjectReference Include="..\\Yj1\\Yj1.csproj" />',
                                      '<ProjectReference Include="..\\Yj5\\Yj5.csproj" />']),
    "src/Yj2/Sender.cs": """namespace Yj2;

public class Sender : Yj1.SendBase, Yj1.ISend
{
    public void Send(HttpClient c)
    {
    }
}
""",
    "src/Yj2/Sender5.cs": """namespace Yj2;

public class Sender5 : Yj5.SendBase5, Yj5.ISend5
{
    public void Send(HttpClient c)
    {
    }
}
""",
    # multi-target with a mix: possible only (the reference compiles the first framework)
    "src/Yj5/Yj5.csproj": _np([_IU, "<TargetFrameworks>net48;net8.0</TargetFrameworks>",
                               "<LangVersion>latest</LangVersion>"], tf=None),
    "src/Yj5/vendor/Http5.cs": """namespace Yjw
{
    public class HttpClient
    {
    }
}
""",
    "src/Yj5/Send5.cs": """using Yjw;

namespace Yj5;

public interface ISend5
{
    void Send(HttpClient c);
}

public class SendBase5
{
    public void Send(Yjw.HttpClient c)
    {
    }
}

public class SendUser5
{
    private readonly ISend5 _s;

    public void Go(Yjw.HttpClient x)
    {
        _s.Send(x);
    }
}
""",
    # the positive sibling: two .NET groups, System.Net.Http certain in both
    "src/Yj3/Yj3.csproj": _np([_IU]),
    "src/Yj3/Send3.cs": """namespace Yj3;

public interface ISend3
{
    void Send(HttpClient c);
}

public class SendBase3
{
    public void Send(System.Net.Http.HttpClient c)
    {
    }
}

public class SendUser3
{
    private readonly ISend3 _s;

    public void Go(System.Net.Http.HttpClient x)
    {
        _s.Send(x);
    }
}
""",
    "src/Yj4/Yj4.csproj": _np([_IU], ['<ProjectReference Include="..\\Yj3\\Yj3.csproj" />']),
    "src/Yj4/Sender3.cs": """namespace Yj4;

public class Sender3 : Yj3.SendBase3, Yj3.ISend3
{
    public void Send(HttpClient c)
    {
    }
}
""",
    # ---- R9 (S8): Directory.Packages.props is an import point. Its ImplicitUsings beats Directory.Build.props'
    "src/Yk/Directory.Build.props": "<Project>\n  <PropertyGroup>\n    <ImplicitUsings>enable</ImplicitUsings>\n"
                                    "  </PropertyGroup>\n</Project>\n",
    "src/Yk/Directory.Packages.props": "<Project>\n  <PropertyGroup>\n    <ImplicitUsings>disable</ImplicitUsings>\n"
                                       "  </PropertyGroup>\n</Project>\n",
    "src/Yk/A/Yka.csproj": _np(),
    "src/Yk/A/TickQ.cs": _tick("Yka", "Q", usings="using System.Timers;\n\n", base_param="System.Timers.Timer"),
    "src/Yk/B/Ykb.csproj": _np(items=['<ProjectReference Include="..\\A\\Yka.csproj" />']),
    "src/Yk/B/TickerQ.cs": """namespace Ykb
{
    using System.Threading;

    public class TickerQ : Yka.TickBaseQ, Yka.ITickQ
    {
        public void Tick(Timer t)
        {
        }
    }
}
""",
    # a <Using Include> in it (no implicit usings): Timer is System.Timers' though no source file says so
    "src/Yk3/A/Directory.Packages.props": "<Project>\n  <ItemGroup>\n    <Using Include=\"System.Timers\" />\n"
                                        "  </ItemGroup>\n</Project>\n",
    "src/Yk3/A/Yk3a.csproj": _np(),
    "src/Yk3/A/TickR.cs": _tick("Yk3a", "R", base_param="System.Timers.Timer"),
    "src/Yk3/B/Yk3b.csproj": _np(items=['<ProjectReference Include="..\\A\\Yk3a.csproj" />']),
    "src/Yk3/B/TickerR.cs": """using System.Threading;

namespace Yk3b;

public class TickerR : Yk3a.TickBaseR, Yk3a.ITickR
{
    public void Tick(Timer t)
    {
    }
}
""",
    # the positive sibling: a central-package file costs nothing: both groups complete, a using at the top against
    # one in a block, a name no table lists (U2 across two groups needs both readings complete)
    "src/Yk2/Directory.Packages.props": """<Project>
  <PropertyGroup>
    <ManagePackageVersionsCentrally>true</ManagePackageVersionsCentrally>
  </PropertyGroup>
  <ItemGroup>
    <PackageVersion Include="Some.Package" Version="1.0.0" />
    <GlobalPackageReference Include="Some.Analyzer" Version="1.0.0" />
  </ItemGroup>
</Project>
""",
    "src/Yk2/A/Yk2a.csproj": _np(),
    "src/Yk2/A/Handle.cs": """using Microsoft.Extensions.Configuration;

namespace Yk2a;

public interface IHandleK
{
    void Handle(IConfiguration c);
}

public class HandleKBase
{
    public void Handle(Microsoft.Extensions.Configuration.IConfiguration c)
    {
    }
}

public class HandleKUser
{
    private readonly IHandleK _h;

    public void Go(IConfiguration c)
    {
        _h.Handle(c);
    }
}
""",
    "src/Yk2/B/Yk2b.csproj": _np(items=['<ProjectReference Include="..\\A\\Yk2a.csproj" />']),
    "src/Yk2/B/HandlerK.cs": """namespace Yk2b
{
    using Microsoft.Extensions.Configuration;

    public class HandlerK : Yk2a.HandleKBase, Yk2a.IHandleK
    {
        public void Handle(IConfiguration c)
        {
        }
    }
}
""",
    # ---- R10 (S9): C# 12's `ref readonly` takes a value argument (rr); plain `ref` does not (the positive sibling)
    "src/Yl/Yl.csproj": _np(),
    "src/Yl/Store.cs": """namespace Yl;

public class Order
{
}

public class Store
{
    public void Save(Order o)
    {
    }
}

public class Store2
{
    public void Save(Order o)
    {
    }
}

public class Store3
{
    public void Save(Order o)
    {
    }
}
""",
    "src/Yl/Rr.cs": """namespace Yl;

public class CartStore : Store
{
    public void Save(ref readonly Order o)
    {
    }
}

public class RrUser
{
    private readonly CartStore _s;

    public void Go(Order o)
    {
        _s.Save(o);
    }
}
""",
    "src/Yl/Rc.cs": """namespace Yl;

public class Cart
{
}

public class OrderC
{
    public static implicit operator Cart(OrderC o)
    {
        return null;
    }
}

public class Store4
{
    public void Save(OrderC o)
    {
    }
}

public class CartStore4 : Store4
{
    public void Save(ref readonly Cart c)
    {
    }
}

public class RcUser
{
    private readonly CartStore4 _s;

    public void Go(OrderC o)
    {
        _s.Save(o);
    }
}
""",
    "src/Yl/Rf.cs": """namespace Yl;

public class CartStore2 : Store2
{
    public void Save(ref Order o)
    {
    }
}

public class RfUser
{
    private readonly CartStore2 _s;

    public void Go(Order o)
    {
        _s.Save(o);
    }
}
""",
    "src/Yl/In.cs": """namespace Yl;

public class CartStore3 : Store3
{
    public void Save(in Order o)
    {
    }
}

public class InUser
{
    private readonly CartStore3 _s;

    public void Go(Order o)
    {
        _s.Save(o);
    }
}
""",
    # ---- R11 (S10): a base-list head goes through the alias walk (bl2)
    "src/Ym/Ym.csproj": _np(),
    "src/Ym/Data/SqlRepo.cs": """namespace Ym.Data
{
    public class SqlRepo
    {
        public void Save()
        {
        }
    }
}
""",
    "src/Ym/B/Repo.cs": """namespace Ym.B
{
    public class Repo
    {
        public void Save()
        {
        }
    }
}
""",
    # one file: an alias of a block, an alias of the compilation unit a nearer using beats, the true target beside
    "src/Ym/Blocks.cs": """using Order = Ym.Data.SqlRepo;
using Order2 = Ym.Data.SqlRepo;

namespace Ym.Models
{
    public class Order
    {
        public void Save()
        {
        }
    }
}

namespace Ym.A
{
    using Repo = Ym.Data.SqlRepo;

    public class UA : Repo
    {
    }

    public class AUser
    {
        private readonly UA _a;

        public void Go()
        {
            _a.Save();
        }
    }
}

namespace Ym.B
{
    public class UB : Repo
    {
    }

    public class BUser
    {
        private readonly UB _b;

        public void Go()
        {
            _b.Save();
        }
    }
}

namespace Ym.C
{
    using Ym.Models;

    public class UC : Order
    {
    }

    public class CUser
    {
        private readonly UC _c;

        public void Go()
        {
            _c.Save();
        }
    }
}

namespace Ym.D
{
    using Ym.Models2;

    public class UD : Order2
    {
    }

    public class DUser
    {
        private readonly UD _d;

        public void Go()
        {
            _d.Save();
        }
    }
}
""",
    "src/Ym/Models2/Order2.cs": """namespace Ym.Models2
{
    public class Order2
    {
        public void Save()
        {
        }
    }
}
""",
    # ---- R12 (S12): member lookup skips what the call site cannot see (hid, hid2)
    "src/Yn/Yn.csproj": _np(items=['<InternalsVisibleTo Include="Yn4" />', '<InternalsVisibleTo Include="FriendAsm" />']),
    "src/Yn/Types.cs": """namespace Yn;

public class Order
{
}

public interface ISv
{
    void Save(Order o);
}

public class BaseS
{
    public void Save(Order o)
    {
    }

    public void Keep(Order o)
    {
    }

    public void Prot(Order o)
    {
    }

    public void Intl(Order o)
    {
    }

    public void Plain(Order o)
    {
    }
}

public class DerE : BaseS, ISv
{
    void ISv.Save(Order o)
    {
    }
}

public class DerP : BaseS
{
    private new void Keep(Order o)
    {
    }

    public void In(Order o)
    {
        Keep(o);
    }

    public class Nest
    {
        private readonly DerP _p;

        public void Go(Order o)
        {
            _p.Keep(o);
        }
    }
}

public class DerG : BaseS
{
    protected new void Prot(Order o)
    {
    }
}

public class SubG : DerG
{
    public void In(Order o)
    {
        Prot(o);
    }
}

public class DerI : BaseS
{
    internal new void Intl(Order o)
    {
    }
}

public class DerN : BaseS
{
    public new void Plain(Order o)
    {
    }
}

public class SubG2 : DerG
{
    public void InOther(DerG other, Order o)
    {
        other.Prot(o);
    }
}

public class HdUser
{
    private readonly DerE _e;
    private readonly DerP _p;
    private readonly DerG _g;
    private readonly DerI _i;
    private readonly DerN _n;

    public void GoE(Order o)
    {
        _e.Save(o);
    }

    public void GoP(Order o)
    {
        _p.Keep(o);
    }

    public void GoG(Order o)
    {
        _g.Prot(o);
    }

    public void GoI(Order o)
    {
        _i.Intl(o);
    }

    public void GoN(Order o)
    {
        _n.Plain(o);
    }
}
""",
    # the skip's grade: the base is in another file (never EXTRACTED)
    "src/Yn/BaseFar.cs": """namespace Yn;

public class BaseFar
{
    public void Far(Order o)
    {
    }
}
""",
    "src/Yn/DerFar.cs": """namespace Yn;

public class DerFar : BaseFar
{
    private new void Far(Order o)
    {
    }
}

public class FarUser
{
    private readonly DerFar _f;

    public void Go(Order o)
    {
        _f.Far(o);
    }
}

public class DerOnly : ISv
{
    void ISv.Save(Order o)
    {
    }
}

public static class OnlyExt
{
    public static void Save(this DerOnly d, Order o)
    {
    }
}

public class OnlyUser
{
    private readonly DerOnly _d;

    public void Go(Order o)
    {
        _d.Save(o);
    }
}
""",
    # another .csproj group: `internal` is not the caller's. The base in that group's own file, the grade follows it
    "src/Yn2/Yn2.csproj": _np(items=['<ProjectReference Include="..\\Yn\\Yn.csproj" />']),
    "src/Yn2/Remote.cs": """namespace Yn2;

public class RemoteUser
{
    private readonly Yn.DerI _i;

    public void Go(Yn.Order o)
    {
        _i.Intl(o);
    }
}
""",
    # a friend assembly sees `internal`: never proven inaccessible
    "src/Yn3/Yn3.csproj": _np(items=['<ProjectReference Include="..\\Yn\\Yn.csproj" />']),
    "src/Yn/Friends.cs": """using System.Runtime.CompilerServices;

[assembly: InternalsVisibleTo("Yn3")]
""",
    "src/Yn3/Friend.cs": """namespace Yn3;

public class FriendUser
{
    private readonly Yn.DerI _i;

    public void Go(Yn.Order o)
    {
        _i.Intl(o);
    }
}
""",
    # ... named by an MSBuild item of the declaring project, or by the friend's <AssemblyName>, not its file's name
    "src/Yn4/Yn4.csproj": _np(items=['<ProjectReference Include="..\\Yn\\Yn.csproj" />']),
    "src/Yn4/Friend.cs": """namespace Yn4;

public class FriendUser4
{
    private readonly Yn.DerI _i;

    public void Go(Yn.Order o)
    {
        _i.Intl(o);
    }
}
""",
    "src/Yn5/Yn5.csproj": _np(["<AssemblyName>FriendAsm</AssemblyName>"],
                              ['<ProjectReference Include="..\\Yn\\Yn.csproj" />']),
    "src/Yn5/Friend.cs": """namespace Yn5;

public class FriendUser5
{
    private readonly Yn.DerI _i;

    public void Go(Yn.Order o)
    {
        _i.Intl(o);
    }
}
""",
    # ---- R20: `Nullable<int>` / `Int32` closing a generic receiver read like `int?` / `int`
    "src/Yo/Yo.csproj": _np(),
    "src/Yo/Closed.cs": """using System;

namespace Yo;

public class Cv0<T>
{
    public virtual void M0()
    {
    }
}

public class Cv0Int : Cv0<int>
{
    public override void M0()
    {
    }
}

public class Cv0Nul : Cv0<int?>
{
    public override void M0()
    {
    }
}

public class Cv0User
{
    private readonly Cv0<Nullable<int>> _a;
    private readonly Cv0<System.Nullable<int>> _b;
    private readonly Cv0<int?> _c;

    public void GoA()
    {
        _a.M0();
    }

    public void GoB()
    {
        _b.M0();
    }

    public void GoC()
    {
        _c.M0();
    }
}

public interface ICounter<T>
{
    T Next();
}

public class IntCounter : ICounter<int>
{
    public int Next()
    {
        return 0;
    }
}

public class LongCounter : ICounter<long>
{
    public long Next()
    {
        return 0;
    }
}

public class RepoUsers
{
    private readonly ICounter<Int32> _i;
    private readonly ICounter<System.Int32> _j;
    private readonly ICounter<int> _k;

    public void GoI()
    {
        _i.Next();
    }

    public void GoJ()
    {
        _j.Next();
    }

    public void GoK()
    {
        _k.Next();
    }
}
""",
    # ---- R12: what grants `internal` is read wherever MSBuild reads it, or the skip is no proof. Za: a friend named in
    # the Directory.Packages.props above both projects
    "src/Za/Directory.Packages.props": _msb('<ItemGroup><InternalsVisibleTo Include="ZaApp" /></ItemGroup>'),
    "src/Za/ZaLib/ZaLib.csproj": _np(),
    "src/Za/ZaLib/Der.cs": _ivt("ZaLib", "A"),
    "src/Za/ZaApp/ZaApp.csproj": _np(items=[_proj_ref("ZaLib")]),
    "src/Za/ZaApp/User.cs": _ivt_user("ZaApp", "ZaLib", "A"),
    # Zb: the caller's assembly name sits in a file its project imports
    "src/Zb/common/name.props": _msb("<PropertyGroup><AssemblyName>ZbFriendly</AssemblyName></PropertyGroup>"),
    "src/Zb/ZbLib/ZbLib.csproj": _np(),
    "src/Zb/ZbLib/Attr.cs": _IVT_ATTR.format("ZbFriendly"),
    "src/Zb/ZbLib/Der.cs": _ivt("ZbLib", "B"),
    "src/Zb/ZbApp/ZbApp.csproj": _np(items=[_proj_ref("ZbLib")]).replace(
        "</Project>", '  <Import Project="../common/name.props" />\n</Project>'),
    "src/Zb/ZbApp/User.cs": _ivt_user("ZbApp", "ZbLib", "B"),
    # Zc: a friend in the central-packages file DirectoryPackagesPropsPath names
    "src/Zc/Directory.Build.props": _msb(
        "<PropertyGroup><DirectoryPackagesPropsPath>$(MSBuildThisFileDirectory)shared/Central.props"
        "</DirectoryPackagesPropsPath></PropertyGroup>"),
    "src/Zc/shared/Central.props": _msb('<ItemGroup><InternalsVisibleTo Include="ZcApp" /></ItemGroup>'),
    "src/Zc/ZcLib/ZcLib.csproj": _np(),
    "src/Zc/ZcLib/Der.cs": _ivt("ZcLib", "C"),
    "src/Zc/ZcApp/ZcApp.csproj": _np(items=[_proj_ref("ZcLib")]),
    "src/Zc/ZcApp/User.cs": _ivt_user("ZcApp", "ZcLib", "C"),
    # Zd: the friend attribute in a file the project links in (a SharedAssemblyInfo.cs)
    "src/Zd/Common/SharedInfo.cs": _IVT_ATTR.format("ZdApp"),
    "src/Zd/ZdLib/ZdLib.csproj": _np(items=['<Compile Include="../Common/SharedInfo.cs" />']),
    "src/Zd/ZdLib/Der.cs": _ivt("ZdLib", "D"),
    "src/Zd/ZdApp/ZdApp.csproj": _np(items=[_proj_ref("ZdLib")]),
    "src/Zd/ZdApp/User.cs": _ivt_user("ZdApp", "ZdLib", "D"),
    # Ze, the positive sibling: a linked SharedVersion.cs says nothing of friends: `internal` stays proven out of reach
    "src/Ze/Common/SharedVersion.cs": _VERSION_CS,
    "src/Ze/ZeLib/ZeLib.csproj": _np(items=['<Compile Include="../Common/SharedVersion.cs" />']),
    "src/Ze/ZeLib/Der.cs": _ivt("ZeLib", "E"),
    "src/Ze/ZeApp/ZeApp.csproj": _np(items=[_proj_ref("ZeLib")]),
    "src/Ze/ZeApp/User.cs": _ivt_user("ZeApp", "ZeLib", "E"),
    # Zh: a friend name built by concatenation: "Zh" + "App"
    "src/Zh/ZhLib/ZhLib.csproj": _np(),
    "src/Zh/ZhLib/Attr.cs": """using System.Runtime.CompilerServices;

[assembly: InternalsVisibleTo("Zh" + ZhLib.Tail.Text)]

namespace ZhLib;

internal static class Tail
{
    public const string Text = "App";
}
""",
    "src/Zh/ZhLib/Der.cs": _ivt("ZhLib", "H"),
    "src/Zh/ZhApp/ZhApp.csproj": _np(items=[_proj_ref("ZhLib")]),
    "src/Zh/ZhApp/User.cs": _ivt_user("ZhApp", "ZhLib", "H"),
    # ---- what a project links in is its own (S6's other half). These Common/ files sit in no project's directory:
    # group "" is no compilation, so their `global using`s must not reach the R6 shapes' shared files (TickL, TickO,
    # TickP: each compiled only by the project whose link reaches it). Zf: a `global using` in a linked file: ZfA's
    # Timer is System.Timers.Timer, ZfB's System.Threading.Timer, though ZfA's own files and project read as a subset
    # of ZfB's
    "src/Zf/Common/Globals.cs": "global using System.Timers;\n",
    "src/Zf/ZfA/ZfA.csproj": _np([_IU], ['<Using Remove="System.Threading" />',
                                         '<Compile Include="../Common/Globals.cs" />']),
    "src/Zf/ZfA/TickX.cs": _tick("ZfA", "X", base_param="Timer"),
    "src/Zf/ZfB/ZfB.csproj": _np([_IU], ['<Using Remove="System.Threading" />', _proj_ref("ZfA")]),
    "src/Zf/ZfB/Glob.cs": "global using System.Threading;\n",
    "src/Zf/ZfB/TickerX.cs": _ticker("ZfB", "X", "ZfA"),
    # Zi: the same through a wildcard, a directory link
    "src/Zi/Common/More/Globals.cs": "global using System.Timers;\n",
    "src/Zi/ZiA/ZiA.csproj": _np([_IU], ['<Using Remove="System.Threading" />',
                                         '<Compile Include="../Common/**/*.cs" />']),
    "src/Zi/ZiA/TickI.cs": _tick("ZiA", "I", base_param="Timer"),
    "src/Zi/ZiB/ZiB.csproj": _np([_IU], ['<Using Remove="System.Threading" />', _proj_ref("ZiA")]),
    "src/Zi/ZiB/Glob.cs": "global using System.Threading;\n",
    "src/Zi/ZiB/TickerI.cs": _ticker("ZiB", "I", "ZiA"),
    # Zj: an alias in a linked file beats the implicit `using System.Threading;` at that level
    "src/Zj/Common/Globals.cs": "global using Timer = System.Timers.Timer;\n",
    "src/Zj/ZjA/ZjA.csproj": _np([_IU], ['<Compile Include="../Common/Globals.cs" />']),
    "src/Zj/ZjA/TickJ.cs": _tick("ZjA", "J", base_param="Timer"),
    "src/Zj/ZjB/ZjB.csproj": _np(items=[_proj_ref("ZjA")]),
    "src/Zj/ZjB/TickerJ.cs": _ticker("ZjB", "J", "ZjA", param="System.Threading.Timer"),
    # Zs: a file in no project's directory, linked by ZsApp only: what Zf / Zi's Common files say (`global using
    # System.Timers;`, group "") is no part of its compilation: its Timer is ZsApp's implicit System.Threading.Timer,
    # so TickerZs's System.Timers.Timer is an overload (typed_calls' tables for such a file: cs_file_tables)
    "src/Zs/ZsApp/ZsApp.csproj": _np([_IU, RN("ZsShared")], ['<Compile Include="../Shared/*.cs" />']),
    "src/Zs/Shared/TickZs.cs": _tick("ZsShared", "Zs"),
    "src/Zs/ZsB/ZsB.csproj": _np(items=[_proj_ref("ZsApp")]),
    "src/Zs/ZsB/TickerZs.cs": _ticker("ZsB", "Zs", "ZsShared", param="System.Timers.Timer"),
    # Zm: a link to a file the tree does not hold (generated before the compile): what it says is unknown, so ZmA's
    # reading is incomplete and `BinaryReader` (no name the known table decides) is not proven ZmB's
    "src/Zm/ZmA/ZmA.csproj": _np([_IU], ['<Compile Include="../Gen/Globals.g.cs" />']),
    "src/Zm/ZmA/TickZm.cs": _tick("ZmA", "Zm", param="BinaryReader", base_param="System.IO.BinaryReader"),
    "src/Zm/ZmB/ZmB.csproj": _np([_IU], [_proj_ref("ZmA")]),
    "src/Zm/ZmB/TickerZm.cs": _ticker("ZmB", "Zm", "ZmA", param="BinaryReader"),
    # Zv: the linked alias sits in a vendor/ file the sync does not extract: the binder reads its text
    "src/Zv/vendor/Globals.cs": "global using Timer = System.Timers.Timer;\n",
    "src/Zv/ZvA/ZvA.csproj": _np([_IU], ['<Compile Include="../vendor/Globals.cs" />']),
    "src/Zv/ZvA/TickZv.cs": _tick("ZvA", "Zv", base_param="Timer"),
    "src/Zv/ZvB/ZvB.csproj": _np(items=[_proj_ref("ZvA")]),
    "src/Zv/ZvB/TickerZv.cs": _ticker("ZvB", "Zv", "ZvA", param="System.Threading.Timer"),
    # Zu: an alias the pass cannot see (N2): a `global using` alias in a vendor/ file of the project's own directory,
    # which the SDK's default glob compiles and the sync does not extract (the binder's assumption 2). Tk5 / I5 stay
    # names of another last segment than CancellationToken / int: unknown, never two types, so B15's overrides stay
    "src/Zu/Zu.csproj": _np(),
    "src/Zu/vendor/Aliases.cs": "global using Tk5 = System.Threading.CancellationToken;\nglobal using I5 = System.Int32;\n",
    "src/Zu/Slots.cs": """using System.Threading;

namespace Zu;

public class A15
{
    public virtual void Run15(CancellationToken ct)
    {
    }

    public virtual void Num15(int x)
    {
    }
}

public class B15 : A15
{
    public override void Run15(Tk5 ct)
    {
    }

    public override void Num15(I5 x)
    {
    }
}

public class LinkUser5
{
    private readonly A15 _a;

    public void Go(CancellationToken ct)
    {
        _a.Run15(ct);
    }

    public void Go2()
    {
        _a.Num15(1);
    }
}
""",
    # Zg, the positive sibling: a linked SharedVersion.cs holds no `global using`: the two readings still compare
    "src/Zg/Common/SharedVersion.cs": _VERSION_CS,
    "src/Zg/ZgA/ZgA.csproj": _np([_IU], ['<Using Remove="System.Threading" />',
                                         '<Compile Include="../Common/SharedVersion.cs" />']),
    "src/Zg/ZgA/Glob.cs": "global using System.Timers;\n",
    "src/Zg/ZgA/TickY.cs": _tick("ZgA", "Y", base_param="Timer"),
    "src/Zg/ZgB/ZgB.csproj": _np([_IU], ['<Using Remove="System.Threading" />', _proj_ref("ZgA")]),
    "src/Zg/ZgB/Glob.cs": "global using System.Timers;\n",
    "src/Zg/ZgB/TickerY.cs": _ticker("ZgB", "Y", "ZgA"),
}

def _fcls(ns, body, usings=""):
    """A file-scoped namespace holding `body` (top-level declarations)."""
    return f"{usings}namespace {ns};\n\n{body}"


def _fuser(n, field, call, params="", ctor=True):
    """A class holding one field, its constructor (ctor) and Go(params) making `call`."""
    typ, name = field.rsplit(" ", 1)
    c = (f"    public {n}({typ} v)\n    {{\n        {name} = v;\n    }}\n\n") if ctor else ""
    return (f"public class {n}\n{{\n    private readonly {field};\n\n{c}    public void Go({params})\n    {{\n"
            f"        {call}\n    }}\n}}\n")


def _fimpl(n, bases, member, param, extra=""):
    """A class `n : bases` declaring `public void member(param)` (plus `extra` members)."""
    return (f"public class {n} : {bases}\n{{\n    public void {member}({param})\n    {{\n    }}\n{extra}}}\n")


def _fsave(n, virt=""):
    return f"public class {n}\n{{\n    public {virt}void Save()\n    {{\n    }}\n}}\n"


_FLIB = '<ProjectReference Include="../A/{}.csproj" />'

FINAL = {
    # ---- R22 (ar2-ar4, ar6): a method's own generic arity and a `params` array are part of its signature. ar2:
    # Repo2.Save<T>(OrderA) is another signature than IRepo2.Save(OrderA): RepoBase2.Save implements it
    "src/La/La.csproj": _np(),
    "src/La/Shapes.cs": _fcls("La", "public class OrderA\n{\n}\n"),
    "src/La/IRepo2.cs": _fcls("La", "public interface IRepo2\n{\n    void Save(OrderA o);\n}\n\n"
                                   "public class RepoBase2\n{\n    public void Save(OrderA o)\n    {\n    }\n}\n\n"
                                   + _fuser("RepoUser2", "IRepo2 _r", "_r.Save(o);", "OrderA o")),
    "src/La/Repo2.cs": _fcls("La", "public class Repo2 : RepoBase2, IRepo2\n{\n    public void Save<T>(OrderA o)\n"
                                  "    {\n    }\n}\n"),
    # the positive sibling: a class declaring Save(OrderA) AND Save<T>(OrderA): the non-generic one implements it
    "src/La/IRepo2b.cs": _fcls("La", "public interface IRepo2b\n{\n    void Save(OrderA o);\n}\n\n"
                                    "public class RepoBase2b\n{\n    public void Save(OrderA o)\n    {\n    }\n}\n\n"
                                    + _fuser("RepoUser2b", "IRepo2b _r", "_r.Save(o);", "OrderA o")),
    "src/La/Repo2b.cs": _fcls("La", "public class Repo2b : RepoBase2b, IRepo2b\n{\n    public void Save(OrderA o)\n"
                                   "    {\n    }\n\n    public void Save<T>(OrderA o)\n    {\n    }\n}\n"),
    # ar4: Repo4.Save(OrderA, params int[]) has two parameters: RepoBase4.Save implements IRepo4.Save(OrderA)
    "src/La/IRepo4.cs": _fcls("La", "public interface IRepo4\n{\n    void Save(OrderA o);\n}\n\n"
                                   "public class RepoBase4\n{\n    public void Save(OrderA o)\n    {\n    }\n}\n\n"
                                   + _fuser("RepoUser4", "IRepo4 _r", "_r.Save(o);", "OrderA o")),
    "src/La/Repo4.cs": _fcls("La", "public class Repo4 : RepoBase4, IRepo4\n{\n"
                                  "    public void Save(OrderA o, params int[] xs)\n    {\n    }\n}\n"),
    # the positive sibling: `params int[]` and `int[]` are one signature: Repo4b.Save(OrderA, int[]) implements
    # IRepo4b.Save(OrderA, params int[])
    "src/La/IRepo4b.cs": _fcls("La", "public interface IRepo4b\n{\n    void Save(OrderA o, params int[] xs);\n}\n\n"
                                    "public class RepoBase4b\n{\n    public void Save(OrderA o, params int[] xs)\n"
                                    "    {\n    }\n}\n\n"
                                    + _fuser("RepoUser4b", "IRepo4b _r", "_r.Save(o, xs);", "OrderA o, int[] xs")),
    "src/La/Repo4b.cs": _fcls("La", "public class Repo4b : RepoBase4b, IRepo4b\n{\n"
                                   "    public void Save(OrderA o, int[] xs)\n    {\n    }\n}\n"),
    # ar3: Der3.Save<T>(OrderA) neither hides nor overrides Base3.Save(OrderA): a call through Der3 binds Base3.Save
    # (T cannot be inferred), whose override Leaf3.Save runs; through Base3 the slot reaches Leaf3.Save
    "src/La/Base3.cs": _fcls("La", "public class Base3\n{\n    public virtual void Save(OrderA o)\n    {\n    }\n}\n"),
    "src/La/Der3.cs": _fcls("La", "public class Der3 : Base3\n{\n    public void Save<T>(OrderA o)\n    {\n    }\n}\n\n"
                                 "public class Leaf3 : Der3\n{\n    public override void Save(OrderA o)\n    {\n    }\n}\n\n"
                                 + _fuser("DerUser3", "Der3 _d", "_d.Save(o);", "OrderA o") + "\n"
                                 + _fuser("BaseUser3", "Base3 _b", "_b.Save(o);", "OrderA o")),
    # ar6: Der6's `new` Save(OrderA, params int[]) is another signature: it does not break Base6.Save's slot, whose
    # override Leaf6.Save runs
    "src/La/Base6.cs": _fcls("La", "public class Base6\n{\n    public virtual void Save(OrderA o)\n    {\n    }\n}\n"),
    "src/La/Der6.cs": _fcls("La", "public class Der6 : Base6\n{\n    public new void Save(OrderA o, params int[] xs)\n"
                                 "    {\n    }\n}\n"),
    "src/La/User6.cs": _fcls("La", "public class Leaf6 : Der6\n{\n    public override void Save(OrderA o)\n    {\n    }\n}"
                                  "\n\n" + _fuser("BaseUser6", "Base6 _b", "_b.Save(o);", "OrderA o")),
    # the positive sibling: a `new virtual` Save(OrderA) of the same signature starts another slot: Leaf6b.Save never
    # runs for a Base6b call (the engine's tuple to it dropped)
    "src/La/Base6b.cs": _fcls("La", "public class Base6b\n{\n    public virtual void Save(OrderA o)\n    {\n    }\n}\n"),
    "src/La/Der6b.cs": _fcls("La", "public class Der6b : Base6b\n{\n    public new virtual void Save(OrderA o)\n"
                                  "    {\n    }\n}\n"),
    "src/La/User6b.cs": _fcls("La", "public class Leaf6b : Der6b\n{\n    public override void Save(OrderA o)\n    {\n"
                                   "    }\n}\n\n" + _fuser("BaseUser6b", "Base6b _b", "_b.Save(o);", "OrderA o")),
    # ---- R23 (st1-st3): `using static T` in a namespace block supplies T's nested types before the compilation
    # unit's usings. st1: a project Holder's nested Timer, so ImplS1.Run(Timer) is no implementation
    "src/Lb/Lb.csproj": _np(),
    "src/Lb/Lib/Holder.cs": "namespace Lb.Lib\n{\n    public static class Holder\n    {\n        public class Timer\n"
                            "        {\n        }\n    }\n\n    public static class Util4\n    {\n"
                            "        public static int Twice(int x)\n        {\n            return x * 2;\n        }\n"
                            "    }\n\n    public static class Holder3\n    {\n        public class Order3\n        {\n"
                            "            public void Save()\n            {\n            }\n        }\n    }\n}\n",
    "src/Lb/vendor/Holder2.cs": "namespace LbExt.Lib\n{\n    public static class Holder2\n    {\n"
                                "        public class Timer\n        {\n        }\n    }\n}\n",
    **{f"src/Lb/Api/IRunS{n}.cs": "using System.Threading;\n\nnamespace Lb.Api\n{\n" + (
        f"    public interface IRunS{n}\n    {{\n        void Run(Timer t);\n    }}\n\n"
        f"    public class RunBaseS{n}\n    {{\n        public void Run(System.Threading.Timer t)\n        {{\n"
        f"        }}\n    }}\n\n"
        f"    public class RunUserS{n}\n    {{\n        private readonly IRunS{n} _r;\n\n"
        f"        public RunUserS{n}(IRunS{n} r)\n        {{\n            _r = r;\n        }}\n\n"
        f"        public void Go(System.Threading.Timer t)\n        {{\n            _r.Run(t);\n        }}\n    }}\n"
        ) + "}\n" for n in (1, 2, 4)},
    "src/Lb/Jobs/ImplS1.cs": "using System.Threading;\n\nnamespace Lb.Jobs\n{\n    using static Lb.Lib.Holder;\n\n"
                             "    public class ImplS1 : Lb.Api.RunBaseS1, Lb.Api.IRunS1\n    {\n"
                             "        public void Run(Timer t)\n        {\n        }\n    }\n}\n",
    # st2: the holder is a package's (vendor/: never read): that level is unknown for type names
    "src/Lb/Jobs/ImplS2.cs": "using System.Threading;\n\nnamespace Lb.Jobs\n{\n    using static LbExt.Lib.Holder2;\n\n"
                             "    public class ImplS2 : Lb.Api.RunBaseS2, Lb.Api.IRunS2\n    {\n"
                             "        public void Run(Timer t)\n        {\n        }\n    }\n}\n",
    # the positive sibling: a project static class with no nested Timer passes the name on: System.Threading's Timer
    "src/Lb/Jobs/ImplS4.cs": "using System.Threading;\n\nnamespace Lb.Jobs\n{\n    using static Lb.Lib.Util4;\n\n"
                             "    public class ImplS4 : Lb.Api.RunBaseS4, Lb.Api.IRunS4\n    {\n"
                             "        public void Run(Timer t)\n        {\n        }\n    }\n}\n",
    # the positive sibling of R23's levels: one `using static Util4` level in both scopes (a level like any other,
    # compared as one): the same Timer, System.Threading's
    **{f"src/Lb/{d}/{f}.cs": f"using System.Threading;\n\nnamespace Lb.{d}\n{{\n    using static Lb.Lib.Util4;\n\n"
       + body + "}\n" for d, f, body in (
        ("Api", "IRunS5", "    public interface IRunS5\n    {\n        void Run(Timer t);\n    }\n\n"
                          "    public class RunBaseS5\n    {\n        public void Run(System.Threading.Timer t)\n"
                          "        {\n        }\n    }\n\n    public class RunUserS5\n    {\n"
                          "        private readonly IRunS5 _r;\n\n        public RunUserS5(IRunS5 r)\n        {\n"
                          "            _r = r;\n        }\n\n        public void Go(System.Threading.Timer t)\n"
                          "        {\n            _r.Run(t);\n        }\n    }\n"),
        ("Jobs", "ImplS5", "    public class ImplS5 : Lb.Api.RunBaseS5, Lb.Api.IRunS5\n    {\n"
                           "        public void Run(Timer t)\n        {\n        }\n    }\n"))},
    # st3: the block's `using static Holder3` supplies Holder3.Order3 before the compilation unit's using Lb.Models
    "src/Lb/Models/Order3.cs": "namespace Lb.Models\n{\n    public class Order3\n    {\n        public void Save()\n"
                               "        {\n        }\n    }\n}\n",
    "src/Lb/Jobs/User3.cs": "using Lb.Models;\n\nnamespace Lb.Jobs\n{\n    using static Lb.Lib.Holder3;\n\n"
                            "    public class User3\n    {\n        private readonly Order3 _o;\n\n"
                            "        public User3(Order3 o)\n        {\n            _o = o;\n        }\n\n"
                            "        public void Go()\n        {\n            _o.Save();\n        }\n    }\n}\n",
    # ---- R24 (pp1): a using inside an #if branch is possible, never certain. On net8.0 TickerP1's Timer is
    # System.Timers'
    "src/Lc/Lc.csproj": _np(),
    "src/Lc/TickP1.cs": _tick("Lc", "P1", usings="using System.Threading;\n\n"),
    "src/Lc/TickerP1.cs": "#if NETFRAMEWORK\nusing System.Threading;\n#else\nusing System.Timers;\n#endif\n\n"
                          + _fcls("Lc", _fimpl("TickerP1", "TickBaseP1, ITickP1", "Tick", "Timer t")),
    # the positive sibling: an #if region elsewhere in the file costs nothing
    "src/Lc/TickP3.cs": _tick("Lc", "P3", usings="using System.Threading;\n\n"),
    "src/Lc/TickerP3.cs": _fcls("Lc", "public class TickerP3 : TickBaseP3, ITickP3\n{\n#if NETFRAMEWORK\n"
                                     "    public void Legacy()\n    {\n    }\n#endif\n\n    public void Tick(Timer t)\n"
                                     "    {\n    }\n}\n", usings="using System.Threading;\n\n"),
    # a `global using` inside #if: never certain for its group (Ld2's Timer is System.Timers')
    "src/Ld1/Ld1.csproj": _np(),
    "src/Ld1/TickP2.cs": _tick("Ld1", "P2", usings="using System.Threading;\n\n"),
    "src/Ld2/Ld2.csproj": _np(items=['<ProjectReference Include="../Ld1/Ld1.csproj" />']),
    "src/Ld2/Glob.cs": "#if NETFRAMEWORK\nglobal using System.Threading;\n#endif\n",
    "src/Ld2/TickerP2.cs": _fcls("Ld2", _fimpl("TickerP2", "Ld1.TickBaseP2, Ld1.ITickP2", "Tick", "Timer t"),
                                usings="using System.Timers;\n\n"),
    # ---- R25 (cr1): <Compile Remove> takes a file out of the group: its `global using` counts for nothing
    "src/Le/Le.csproj": _np(items=['<Compile Remove="TestData/**" />']),
    "src/Le/TestData/Sample.cs": "global using System.Threading;\n\nnamespace LeSample;\n\npublic class Input\n{\n}\n",
    "src/Le/TickR1.cs": _tick("Le", "R1", usings="using System.Threading;\n\n"),
    "src/Le/TickerR1.cs": _fcls("Le", _fimpl("TickerR1", "TickBaseR1, ITickR1", "Tick", "Timer t"),
                               usings="using System.Timers;\n\n"),
    # EnableDefaultCompileItems=false: a file the project does not list is not compiled
    "src/Lf/Lf.csproj": _np(["<EnableDefaultCompileItems>false</EnableDefaultCompileItems>"],
                            ['<Compile Include="TickR2.cs" />', '<Compile Include="TickerR2.cs" />']),
    "src/Lf/Junk.cs": "global using System.Threading;\n",
    "src/Lf/TickR2.cs": _tick("Lf", "R2", usings="using System.Threading;\n\n"),
    "src/Lf/TickerR2.cs": _fcls("Lf", _fimpl("TickerR2", "TickBaseR2, ITickR2", "Tick", "Timer t"),
                               usings="using System.Timers;\n\n"),
    # a Remove through a path property
    "src/Lg/Lg.csproj": _np(items=['<Compile Remove="$(MSBuildProjectDirectory)/Data/**" />']),
    "src/Lg/Data/Sample.cs": "global using System.Threading;\n\nnamespace LgSample;\n\npublic class Input\n{\n}\n",
    "src/Lg/TickR3.cs": _tick("Lg", "R3", usings="using System.Threading;\n\n"),
    "src/Lg/TickerR3.cs": _fcls("Lg", _fimpl("TickerR3", "TickBaseR3, ITickR3", "Tick", "Timer t"),
                               usings="using System.Timers;\n\n"),
    # the positive sibling: the removed file's `global using` alias of Timer is no alias of the group
    "src/Lh/Lh.csproj": _np(items=['<Compile Remove="Junk/**" />']),
    "src/Lh/Junk/Bad.cs": "global using Timer = LhJunk.Clock;\n\nnamespace LhJunk;\n\npublic class Clock\n{\n}\n",
    "src/Lh/TickR4.cs": _tick("Lh", "R4", param="System.Threading.Timer"),
    "src/Lh/TickerR4.cs": _fcls("Lh", _fimpl("TickerR4", "TickBaseR4, ITickR4", "Tick", "Timer t"),
                               usings="using System.Threading;\n\n"),
    # ---- R26 (sf1, sf2): the same-file rule binds only a class of the same or an enclosing namespace declaration
    "src/Li/Li.csproj": _np(),
    "src/Li/B/OrderF.cs": "namespace Li.B\n{\n    public class OrderF\n    {\n        public void Save()\n        {\n"
                          "        }\n    }\n\n    public class OrderG\n    {\n    }\n}\n",
    "src/Li/UserF.cs": "namespace Li.A\n{\n    public class OrderF\n    {\n        public void Save()\n        {\n"
                       "        }\n    }\n}\n\nnamespace Li.B\n{\n    public class UserF\n    {\n"
                       "        private readonly OrderF _o;\n\n        public UserF(OrderF o)\n        {\n"
                       "            _o = o;\n        }\n\n        public void Go()\n        {\n            _o.Save();\n"
                       "        }\n    }\n}\n",
    "src/Li/IRunG.cs": "namespace Li.A\n{\n    public class OrderG\n    {\n    }\n\n    public interface IRunG\n    {\n"
                       "        void Run(OrderG o);\n    }\n\n    public class RunBaseG\n    {\n"
                       "        public void Run(OrderG o)\n        {\n        }\n    }\n\n    public class RunUserG\n"
                       "    {\n        private readonly IRunG _r;\n\n        public RunUserG(IRunG r)\n        {\n"
                       "            _r = r;\n        }\n\n        public void Go(OrderG o)\n        {\n"
                       "            _r.Run(o);\n        }\n    }\n}\n\nnamespace Li.B\n{\n"
                       "    public class ImplG : Li.A.RunBaseG, Li.A.IRunG\n    {\n        public void Run(OrderG o)\n"
                       "        {\n        }\n    }\n}\n",
    # the positive siblings: the same declaration's class, an enclosing declaration's class (EXTRACTED)
    "src/Li/D/OrderH.cs": "namespace Li.D\n{\n    public class OrderH\n    {\n        public void Save()\n        {\n"
                          "        }\n    }\n\n    public class OrderJ\n    {\n        public void Save()\n        {\n"
                          "        }\n    }\n}\n",
    "src/Li/UserH.cs": "namespace Li.C\n{\n    public class OrderH\n    {\n        public void Save()\n        {\n"
                       "        }\n    }\n\n    public class UserH\n    {\n        private readonly OrderH _o;\n\n"
                       "        public UserH(OrderH o)\n        {\n            _o = o;\n        }\n\n"
                       "        public void Go()\n        {\n            _o.Save();\n        }\n    }\n}\n",
    "src/Li/UserJ.cs": "namespace Li.E\n{\n    public class OrderJ\n    {\n        public void Save()\n        {\n"
                       "        }\n    }\n\n    namespace Inner\n    {\n        public class UserJ\n        {\n"
                       "            private readonly OrderJ _o;\n\n            public UserJ(OrderJ o)\n            {\n"
                       "                _o = o;\n            }\n\n            public void Go()\n            {\n"
                       "                _o.Save();\n            }\n        }\n    }\n}\n",
    # ---- R27 (cap1): at a capable enclosing namespace (System.Threading.Jobs) a name the project does not declare
    # there is unknown: C# finds System.Threading.Timer before the compilation unit's using
    "src/Lj/Lj.csproj": _np(),
    "src/Lj/Model/Timer.cs": "namespace Lj.Model\n{\n    public class Timer\n    {\n    }\n}\n",
    **{f"src/Lj/Api/IRunK{n}.cs": "namespace Lj.Api\n{\n    using Lj.Model;\n\n" + (
        f"    public interface IRunK{n}\n    {{\n        void Run(Timer t);\n    }}\n\n"
        f"    public class RunBaseK{n}\n    {{\n        public void Run(Lj.Model.Timer t)\n        {{\n        }}\n"
        f"    }}\n\n    public class RunUserK{n}\n    {{\n        private readonly IRunK{n} _r;\n\n"
        f"        public RunUserK{n}(IRunK{n} r)\n        {{\n            _r = r;\n        }}\n\n"
        f"        public void Go(Lj.Model.Timer t)\n        {{\n            _r.Run(t);\n        }}\n    }}\n"
        ) + "}\n" for n in (1, 2)},
    "src/Lj/Jobs/ImplK1.cs": "using Lj.Model;\n\nnamespace System.Threading.Jobs\n{\n"
                             "    public class ImplK1 : Lj.Api.RunBaseK1, Lj.Api.IRunK1\n    {\n"
                             "        public void Run(Timer t)\n        {\n        }\n    }\n}\n",
    # the positive sibling: under a namespace no external assembly fills, the using decides: Lj.Model.Timer
    "src/Lj/Jobs/ImplK2.cs": "using Lj.Model;\n\nnamespace Lj.Jobs2\n{\n"
                             "    public class ImplK2 : Lj.Api.RunBaseK2, Lj.Api.IRunK2\n    {\n"
                             "        public void Run(Timer t)\n        {\n        }\n    }\n}\n",
    # ---- R28 (fl1, fl2): a `file` type is visible only in its own file
    "src/Lk/Lk.csproj": _np(),
    "src/Lk/Local.cs": _fcls("Lk.N", "file class OrderL\n{\n    public void Save()\n    {\n    }\n}\n\n"
                                    "file class LocalUserL\n{\n    public void Use()\n    {\n"
                                    "        new OrderL().Save();\n    }\n}\n"),
    "src/Lk/M/OrderL.cs": _fcls("Lk.M", _fsave("OrderL")),
    "src/Lk/UserL.cs": _fcls("Lk.N", _fuser("UserL", "OrderL _o", "_o.Save();"), usings="using Lk.M;\n\n"),
    "src/Lk/M2/OrderM.cs": _fcls("Lk.M2", "public class OrderM\n{\n}\n"),
    "src/Lk/IRunM.cs": _fcls("Lk.N2", "public interface IRunM\n{\n    void Run(OrderM o);\n}\n\n"
                                     "public class RunBaseM\n{\n    public void Run(Lk.M2.OrderM o)\n    {\n    }\n}\n\n"
                                     + _fuser("RunUserM", "IRunM _r", "_r.Run(o);", "Lk.M2.OrderM o"),
                            usings="using Lk.M2;\n\n"),
    "src/Lk/ImplM.cs": _fcls("Lk.N2", "file class OrderM\n{\n}\n\nfile class ImplM : RunBaseM, IRunM\n{\n"
                                     "    public void Run(OrderM o)\n    {\n    }\n}\n\n"
                                     "public static class ImplMFactory\n{\n    public static IRunM Make()\n    {\n"
                                     "        return new ImplM();\n    }\n}\n"),
    # ---- R29 (nb3, nb4): a namespace block's usings apply only inside it and its nested blocks
    "src/Ll/Ll.csproj": _np(),
    "src/Ll/X/OrderN.cs": "namespace Ll.X\n{\n" + "\n".join("    " + ln if ln else "" for ln in
                                                         _fsave("OrderN").split("\n")).rstrip() + "\n}\n",
    "src/Ll/Y/OrderN.cs": "namespace Ll.Y\n{\n" + "\n".join("    " + ln if ln else "" for ln in
                                                         _fsave("OrderN").split("\n")).rstrip() + "\n}\n",
    "src/Ll/UserN3.cs": "using Ll.Y;\n\nnamespace Ll.A\n{\n    using Ll.X;\n\n    public class OtherN3\n    {\n    }\n}\n"
                        "\nnamespace Ll.A\n{\n    public class UserN3\n    {\n        private readonly OrderN _o;\n\n"
                        "        public UserN3(OrderN o)\n        {\n            _o = o;\n        }\n\n"
                        "        public void Go()\n        {\n            _o.Save();\n        }\n    }\n}\n",
    "src/Ll/UserN4.cs": "using Ll.Y;\n\nnamespace Ll\n{\n    using Ll.X;\n\n    public class OtherN4\n    {\n    }\n}\n"
                        "\nnamespace Ll.A\n{\n    public class UserN4\n    {\n        private readonly OrderN _o;\n\n"
                        "        public UserN4(OrderN o)\n        {\n            _o = o;\n        }\n\n"
                        "        public void Go()\n        {\n            _o.Save();\n        }\n    }\n}\n",
    # the positive siblings: inside the block, and inside a block nested in it, the block's using wins
    "src/Ll/UserN5.cs": "using Ll.Y;\n\nnamespace Ll.B\n{\n    using Ll.X;\n\n    public class UserN5\n    {\n"
                        "        private readonly OrderN _o;\n\n        public UserN5(OrderN o)\n        {\n"
                        "            _o = o;\n        }\n\n        public void Go()\n        {\n            _o.Save();\n"
                        "        }\n    }\n}\n",
    "src/Ll/UserN6.cs": "using Ll.Y;\n\nnamespace Ll\n{\n    using Ll.X;\n\n    namespace C\n    {\n"
                        "        public class UserN6\n        {\n            private readonly OrderN _o;\n\n"
                        "            public UserN6(OrderN o)\n            {\n                _o = o;\n            }\n\n"
                        "            public void Go()\n            {\n                _o.Save();\n            }\n"
                        "        }\n    }\n}\n",
    # ---- R30 (ga1): `ResultG` and `ResultG<T>` are two types: a non-generic spelling never binds the project's
    # generic class (nor a generic one the project's non-generic class)
    "src/Lm/Lm.csproj": _np(),
    "src/Lm/vendor/Ext.cs": "namespace LmExt\n{\n    public class ResultG\n    {\n        public void Save()\n"
                            "        {\n        }\n    }\n\n    public class ReplyG<T>\n    {\n        public void Save()\n"
                            "        {\n        }\n    }\n}\n",
    "src/Lm/Common/ResultG.cs": "namespace Lm.Common\n{\n    public class ResultG<T>\n    {\n        public void Save()\n"
                                "        {\n        }\n    }\n\n    public class ReplyG\n    {\n        public void Save()\n"
                                "        {\n        }\n    }\n}\n",
    "src/Lm/App/UserG.cs": "using LmExt;\nusing Lm.Common;\n\nnamespace Lm.App\n{\n    public class UserG\n    {\n"
                           "        private readonly ResultG _r;\n        private readonly ResultG<int> _g;\n"
                           "        private readonly ReplyG<int> _p;\n        private readonly ReplyG _q;\n\n"
                           "        public UserG(ResultG r, ResultG<int> g, ReplyG<int> p, ReplyG q)\n        {\n"
                           "            _r = r;\n            _g = g;\n            _p = p;\n            _q = q;\n"
                           "        }\n\n        public void Go()\n        {\n            _r.Save();\n        }\n\n"
                           "        public void GoG()\n        {\n            _g.Save();\n        }\n\n"
                           "        public void GoP()\n        {\n            _p.Save();\n        }\n\n"
                           "        public void GoQ()\n        {\n            _q.Save();\n        }\n    }\n}\n",
    # ---- R31 (pl1): partial parts merge per compiling project: App compiles the linked part, whose `new` Save
    # hides BaseP.Save
    "src/Ln/App/App.csproj": _np(items=['<Compile Include="../Shared/OrderP.Part.cs" />']),
    "src/Ln/App/BaseP.cs": _fcls("LnApp", _fsave("BaseP")),
    "src/Ln/App/OrderP.cs": _fcls("LnApp", "public partial class OrderP : BaseP\n{\n}\n"),
    "src/Ln/Shared/OrderP.Part.cs": _fcls("LnApp", "public partial class OrderP\n{\n    public new void Save()\n"
                                                  "    {\n    }\n}\n"),
    "src/Ln/App/UserP.cs": _fcls("LnApp", _fuser("UserP", "OrderP _o", "_o.Save();")),
    # the must-not sibling: a part only ANOTHER project links is no part of App2's class
    "src/Ln/App2/App2.csproj": _np(),
    "src/Ln/App2/BaseQ.cs": _fcls("LnApp2", _fsave("BaseQ")),
    "src/Ln/App2/OrderQ.cs": _fcls("LnApp2", "public partial class OrderQ : BaseQ\n{\n}\n"),
    "src/Ln/App2/UserQ.cs": _fcls("LnApp2", _fuser("UserQ", "OrderQ _o", "_o.Save();")),
    "src/Ln/Other/Other.csproj": _np(items=['<Compile Include="../Shared2/OrderQ.Part.cs" />']),
    "src/Ln/Other/Dummy.cs": _fcls("LnOther", "public class Dummy\n{\n}\n"),
    "src/Ln/Shared2/OrderQ.Part.cs": _fcls("LnApp2", "public partial class OrderQ\n{\n    public new void Save()\n"
                                                    "    {\n    }\n}\n"),
    # the must-not where TWO projects compile one part (each into its own OrderS of another base): in App4 the part's
    # `new` Save hides BaseS4.Save (Roslyn); a part two projects compile is open (unmerged): never BaseS4.Save
    **{f"src/Ln/App{n}/App{n}.csproj": _np(items=['<Compile Include="../Shared3/OrderS.Part.cs" />']) for n in (3, 4)},
    **{f"src/Ln/App{n}/BaseS{n}.cs": _fcls("LnS", _fsave(f"BaseS{n}")) for n in (3, 4)},
    **{f"src/Ln/App{n}/OrderS.cs": _fcls("LnS", f"public partial class OrderS : BaseS{n}\n{{\n}}\n") for n in (3, 4)},
    "src/Ln/Shared3/OrderS.Part.cs": _fcls("LnS", "public partial class OrderS\n{\n    public new void Save()\n"
                                                 "    {\n    }\n}\n"),
    "src/Ln/App4/UserS.cs": _fcls("LnS", _fuser("UserS", "OrderS _o", "_o.Save();")),
    # ---- R32 (tr2b): transparent(u) needs u's root OWNED (an AssemblyName, RootNamespace or project file name
    # starting with it) and UNSHARED (no package id whose first segment starts with the root or is a prefix of it).
    # Acme: owned (Acme.Web.csproj) but AcmeCorp.Core shares it; Bolt: owned by nothing; Cobalt: owned, `Co.Timers`
    # shares it. The vendor/ code is the package's (never read)
    "src/Lo/Acme.Web.csproj": _np(items=['<PackageReference Include="AcmeCorp.Core" Version="1.0.0" />']),
    "src/Lo/vendor/AcmeCore.cs": "namespace Acme.Core\n{\n    public class Timer\n    {\n    }\n}\n",
    "src/Lo/Core/CoreExt.cs": "namespace Acme.Core\n{\n    public static class CoreExtA\n    {\n"
                              "        public static int Twice(this int x)\n        {\n            return x * 2;\n"
                              "        }\n    }\n}\n",
    "src/Lo/Web/TickT1.cs": _tick("Acme.Web", "T1", usings="using System.Threading;\n\n"),
    "src/Lo/Web/TickerT1.cs": "using System.Threading;\n\nnamespace Acme.Web.Jobs\n{\n    using Acme.Core;\n\n"
                              "    public class TickerT1 : Acme.Web.TickBaseT1, Acme.Web.ITickT1\n    {\n"
                              "        public void Tick(Timer t)\n        {\n        }\n    }\n}\n",
    "src/Lp/Lp.csproj": _np(),
    "src/Lp/vendor/BoltCore.cs": "namespace Bolt.Core\n{\n    public class Timer\n    {\n    }\n}\n",
    "src/Lp/Core/CoreExt.cs": "namespace Bolt.Core\n{\n    public static class CoreExtB\n    {\n"
                              "        public static int Twice(this int x)\n        {\n            return x * 2;\n"
                              "        }\n    }\n}\n",
    "src/Lp/Web/TickT2.cs": _tick("Bolt.Web", "T2", usings="using System.Threading;\n\n"),
    "src/Lp/Web/TickerT2.cs": "using System.Threading;\n\nnamespace Bolt.Web.Jobs\n{\n    using Bolt.Core;\n\n"
                              "    public class TickerT2 : Bolt.Web.TickBaseT2, Bolt.Web.ITickT2\n    {\n"
                              "        public void Tick(Timer t)\n        {\n        }\n    }\n}\n",
    "src/Lq/Cobalt.App.csproj": _np(items=['<PackageReference Include="Co.Timers" Version="1.0.0" />']),
    "src/Lq/vendor/CobaltCore.cs": "namespace Cobalt.Core\n{\n    public class Timer\n    {\n    }\n}\n",
    "src/Lq/Core/CoreExt.cs": "namespace Cobalt.Core\n{\n    public static class CoreExtC\n    {\n"
                              "        public static int Twice(this int x)\n        {\n            return x * 2;\n"
                              "        }\n    }\n}\n",
    "src/Lq/Web/TickT3.cs": _tick("Cobalt.Web", "T3", usings="using System.Threading;\n\n"),
    "src/Lq/Web/TickerT3.cs": "using System.Threading;\n\nnamespace Cobalt.Web.Jobs\n{\n    using Cobalt.Core;\n\n"
                              "    public class TickerT3 : Cobalt.Web.TickBaseT3, Cobalt.Web.ITickT3\n    {\n"
                              "        public void Tick(Timer t)\n        {\n        }\n    }\n}\n",
    # the positive siblings: a root owned by a project file name (with an unrelated package), by an AssemblyName, by a
    # RootNamespace, shared by no id: `using X.Core;` supplies project types only: System.Threading's Timer
    **{k: v for r, root, proj, props, items in (
        ("Lr", "Delta", "Delta.Web", [], ['<PackageReference Include="Zulu.Tools" Version="1.0.0" />']),
        ("Ls", "Echo", "Ls", ["<AssemblyName>Echo.App</AssemblyName>"], []),
        ("Lt", "Foxtrot", "Lt", ["<RootNamespace>Foxtrot</RootNamespace>"], []))
       for n in ({"Lr": "4", "Ls": "5", "Lt": "6"}[r],)
       for k, v in {
           f"src/{r}/{proj}.csproj": _np(props, items),
           f"src/{r}/Core/CoreExt.cs": f"namespace {root}.Core\n{{\n    public static class CoreExt{n}\n    {{\n"
                                       "        public static int Twice(this int x)\n        {\n"
                                       "            return x * 2;\n        }\n    }\n}\n",
           f"src/{r}/Web/TickT{n}.cs": _tick(f"{root}.Web", f"T{n}", usings="using System.Threading;\n\n"),
           f"src/{r}/Web/TickerT{n}.cs": f"using System.Threading;\n\nnamespace {root}.Web.Jobs\n{{\n"
                                         f"    using {root}.Core;\n\n"
                                         f"    public class TickerT{n} : {root}.Web.TickBaseT{n}, {root}.Web.ITickT{n}\n"
                                         "    {\n        public void Tick(Timer t)\n        {\n        }\n    }\n}\n",
       }.items()},
    # ---- R33 (D1): a <Using Remove> spec written as a path (`./System.Threading`) matches the item as MSBuild
    # matches paths: LuB has no System.Threading, its Timer is System.Timers'
    "src/Lu/A/LuA.csproj": _np(),
    "src/Lu/A/TickU1.cs": _tick("LuA", "U1", usings="using System.Threading;\n\n"),
    "src/Lu/A/TickU2.cs": _tick("LuA", "U2", usings="using System.Threading;\n\n"),
    "src/Lu/B/LuB.csproj": _np([_IU], ['<Using Remove="./System.Threading" />', _FLIB.format("LuA")]),
    "src/Lu/B/TickerU1.cs": _fcls("LuB", _fimpl("TickerU1", "LuA.TickBaseU1, LuA.ITickU1", "Tick", "Timer t"),
                                 usings="using System.Timers;\n\n"),
    # the positive sibling: a plain <Using Remove> of another namespace: LuC's implicit System.Threading stands
    "src/Lu/C/LuC.csproj": _np([_IU], ['<Using Remove="System.IO" />', _FLIB.format("LuA")]),
    "src/Lu/C/TickerU2.cs": _fcls("LuC", _fimpl("TickerU2", "LuA.TickBaseU2, LuA.ITickU2", "Tick", "Timer t")),
    # ---- R34 (D2): MSBuild compares two boolean-like operands as booleans: `yes` == 'true' removes System.Threading
    "src/Lv/A/LvA.csproj": _np(),
    "src/Lv/A/TickV1.cs": _tick("LvA", "V1", usings="using System.Threading;\n\n"),
    "src/Lv/A/TickV2.cs": _tick("LvA", "V2", usings="using System.Threading;\n\n"),
    "src/Lv/B/LvB.csproj": '<Project Sdk="Microsoft.NET.Sdk">\n  <PropertyGroup>\n'
                           "    <TargetFramework>net8.0</TargetFramework>\n    <ImplicitUsings>enable</ImplicitUsings>\n"
                           "    <Trim>yes</Trim>\n  </PropertyGroup>\n  <ItemGroup Condition=\"'$(Trim)' == 'true'\">\n"
                           '    <Using Remove="System.Threading" />\n  </ItemGroup>\n  <ItemGroup>\n'
                           '    <ProjectReference Include="../A/LvA.csproj" />\n  </ItemGroup>\n</Project>\n',
    "src/Lv/B/TickerV1.cs": _fcls("LvB", _fimpl("TickerV1", "LvA.TickBaseV1, LvA.ITickV1", "Tick", "Timer t"),
                                 usings="using System.Timers;\n\n"),
    # the positive sibling: ImplicitUsings `yes` is on (the SDK's condition compares it with 'true' as a boolean)
    "src/Lv/C/LvC.csproj": _np(["<ImplicitUsings>yes</ImplicitUsings>"], [_FLIB.format("LvA")]),
    "src/Lv/C/TickerV2.cs": _fcls("LvC", _fimpl("TickerV2", "LvA.TickBaseV2, LvA.ITickV2", "Tick", "Timer t")),
    # ---- R35 (D3): a Directory.Build.rsp's -p: switches are global properties: LwB has no implicit usings
    "src/Lw/A/LwA.csproj": _np(),
    "src/Lw/A/TickW1.cs": _tick("LwA", "W1", usings="using System.Threading;\n\n"),
    "src/Lw/B/Directory.Build.rsp": "-p:ImplicitUsings=disable\n",
    "src/Lw/B/LwB.csproj": _np([_IU], [_FLIB.format("LwA")]),
    "src/Lw/B/TickerW1.cs": _fcls("LwB", _fimpl("TickerW1", "LwA.TickBaseW1, LwA.ITickW1", "Tick", "Timer t"),
                                 usings="using System.Timers;\n\n"),
    # ---- R36 (D6): MSBuild splits on `;` before it decodes %3B: `System.IO%3BSystem.Linq` removes nothing, so LxB's
    # Stream is System.IO's, LxA's the package's (vendor/)
    "src/Lx/A/LxA.csproj": _np([_IU], ['<Using Remove="System.IO" />', '<Using Remove="System.Linq" />']),
    "src/Lx/A/vendor/Ext.cs": "namespace LxExt\n{\n    public class Stream\n    {\n    }\n}\n",
    "src/Lx/A/StoreX1.cs": _fcls("LxA", "public interface IStoreX1\n{\n    void Put(Stream s);\n}\n\n"
                                       "public class StoreBaseX1\n{\n    public void Put(LxExt.Stream s)\n    {\n    }\n"
                                       "}\n\n" + _fuser("StoreUserX1", "IStoreX1 _s", "_s.Put(x);", "LxExt.Stream x"),
                                usings="using LxExt;\n\n"),
    "src/Lx/B/LxB.csproj": _np([_IU], ['<Using Remove="System.IO%3BSystem.Linq" />', _FLIB.format("LxA")]),
    "src/Lx/B/StorerX1.cs": _fcls("LxB", _fimpl("StorerX1", "LxA.StoreBaseX1, LxA.IStoreX1", "Put", "Stream s")),
    # ---- R37 (D7): a package's props add usings the pass cannot see (vendor/Glob.cs stands for them): two readings
    # compare only when their package sets (over the ProjectReference closure) are equal
    "src/Ly/A/LyA.csproj": _np(items=['<PackageReference Include="Pya.Threads" Version="1.0.0" />']),
    "src/Ly/A/vendor/Glob.cs": "global using System.Threading;\n",
    "src/Ly/A/TickY1.cs": _tick("LyA", "Y1", usings="using System.Collections.Generic;\n\n"),
    "src/Ly/B/LyB.csproj": _np(items=['<PackageReference Include="Pyb.Timers" Version="1.0.0" />',
                                      _FLIB.format("LyA")]),
    "src/Ly/B/vendor/Glob.cs": "global using System.Timers;\n",
    "src/Ly/B/TickerY1.cs": _fcls("LyB", _fimpl("TickerY1", "LyA.TickBaseY1, LyA.ITickY1", "Tick", "Timer t"),
                                 usings="using System.Collections.Generic;\n\n"),
    # R37's positive sibling (the same package in both): since R45 (two projects' readings compare only when neither
    # has a package: build vs buildTransitive assets, PrivateAssets, TFM-specific assets) a gap, nothing removed
    "src/Ly/C/LyC.csproj": _np(items=['<PackageReference Include="Pyc.Threads" Version="1.0.0" />']),
    "src/Ly/C/vendor/Glob.cs": "global using System.Threading;\n",
    "src/Ly/C/TickY2.cs": _tick("LyC", "Y2", usings="using System.Collections.Generic;\n\n"),
    "src/Ly/D/LyD.csproj": _np(items=['<PackageReference Include="Pyc.Threads" Version="1.0.0" />',
                                      '<ProjectReference Include="../C/LyC.csproj" />']),
    "src/Ly/D/vendor/Glob.cs": "global using System.Threading;\n",
    "src/Ly/D/TickerY2.cs": _fcls("LyD", _fimpl("TickerY2", "LyC.TickBaseY2, LyC.ITickY2", "Tick", "Timer t"),
                                 usings="using System.Collections.Generic;\n\n"),
    # ---- R38: a condition on $(Configuration) is possible, never certain (`dotnet build` builds Debug: LzB's Timer is
    # System.Timers'; only a Release build gives System.Threading's)
    "src/Lz/A/LzA.csproj": _np(),
    "src/Lz/A/TickZ1.cs": _tick("LzA", "Z1", usings="using System.Threading;\n\n"),
    "src/Lz/B/LzB.csproj": '<Project Sdk="Microsoft.NET.Sdk">\n  <PropertyGroup>\n'
                           "    <TargetFramework>net8.0</TargetFramework>\n  </PropertyGroup>\n"
                           "  <ItemGroup Condition=\"'$(Configuration)' == 'Release'\">\n"
                           '    <Using Include="System.Threading" />\n  </ItemGroup>\n'
                           "  <ItemGroup Condition=\"'$(Configuration)' != 'Release'\">\n"
                           '    <Using Include="System.Timers" />\n  </ItemGroup>\n  <ItemGroup>\n'
                           '    <ProjectReference Include="../A/LzA.csproj" />\n  </ItemGroup>\n</Project>\n',
    "src/Lz/B/TickerZ1.cs": _fcls("LzB", _fimpl("TickerZ1", "LzA.TickBaseZ1, LzA.ITickZ1", "Tick", "Timer t")),
    # ---- an alias target in an INNER namespace block is resolved with the OUTER blocks' usings in effect (a
    # fix of this round, no decision; the reader's case ao1): User1's X is Lib.Foo.Bar (block App's `using Lib;`
    # supplies the type Foo before the global namespace Foo), User2's X (no outer block directive) the global
    # Foo.Bar
    "src/Na/App.csproj": """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
  </PropertyGroup>
</Project>
""",
    "src/Na/Foo/Bar.cs": """namespace Foo
{
    public class Bar
    {
        public void Run()
        {
        }
    }
}
""",
    "src/Na/Lib/Holder.cs": """namespace Lib
{
    public class Foo
    {
        public class Bar
        {
            public void Run()
            {
            }
        }
    }
}
""",
    "src/Na/Use1.cs": """namespace App
{
    using Lib;

    namespace B
    {
        using X = Foo.Bar;

        public class User1
        {
            private readonly X _x;

            public User1(X x)
            {
                _x = x;
            }

            public void Go()
            {
                _x.Run();
            }
        }
    }
}
""",
    "src/Na/Use2.cs": """namespace App.C
{
    using X = Foo.Bar;

    public class User2
    {
        private readonly X _x;

        public User2(X x)
        {
            _x = x;
        }

        public void Go()
        {
            _x.Run();
        }
    }
}
""",
    # ---- R30 in the core's nested-type lookup (the core's cases ng1 / ng2): `Entry` written in a class holding a
    # nested Entry<T> is the outer Ng.Entry; `Entry<int>` the nested one
    "src/Nb/Ng.csproj": """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
  </PropertyGroup>
</Project>
""",
    "src/Nb/Entry.cs": """namespace Ng;

public class Entry
{
    public void Run()
    {
    }
}
""",
    "src/Nb/Repo.cs": """namespace Ng;

public class Repo
{
    public class Entry<T>
    {
        public void Run()
        {
        }
    }

    private readonly Entry _e;
    private readonly Entry<int> _g;

    public Repo(Entry e, Entry<int> g)
    {
        _e = e;
        _g = g;
    }

    public void Go()
    {
        _e.Run();
    }

    public void GoG()
    {
        _g.Run();
    }
}
""",
    # `Cell<int>` in a class holding a non-generic nested Cell is the outer Ng2.Cell<T>; `Box.Item` / `Box.Item<int>`
    # the nested type of their own arity
    "src/Nc/Ng2.csproj": """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
  </PropertyGroup>
</Project>
""",
    # ---- an enclosing namespace proves a qualified name (the D4d rule, the core's case dx1): `CancellationToken`
    # written inside namespace System.Threading is that namespace's listed member, so DxWaiter.Wait(System.Threading
    # .CancellationToken) implements IDxWait.Wait; inside a capable namespace R27 resolves only its own members, so
    # the interface, the base and the caller live in System.Threading and the implementer outside it
    "src/Nd/Dx.csproj": """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
  </PropertyGroup>
</Project>
""",
    "src/Nd/Api.cs": """namespace System.Threading
{
    public interface IDxWait
    {
        void Wait(CancellationToken t);
    }

    public class DxWaitBase
    {
        public void Wait(CancellationToken t)
        {
        }
    }

    public class DxWaitUser
    {
        private readonly IDxWait _w;

        public DxWaitUser(IDxWait w)
        {
            _w = w;
        }

        public void Go(CancellationToken t)
        {
            _w.Wait(t);
        }
    }
}
""",
    "src/Nd/Impl/Waiter.cs": """namespace Dx.Impl
{
    public class DxWaiter : System.Threading.DxWaitBase, System.Threading.IDxWait
    {
        public void Wait(System.Threading.CancellationToken t)
        {
        }
    }
}
""",
    "src/Nc/Cell.cs": """namespace Ng2;

public class Cell<T>
{
    public void Run()
    {
    }
}
""",
    "src/Nc/Holder.cs": """namespace Ng2;

public class Holder
{
    public class Cell
    {
        public void Run()
        {
        }
    }

    private readonly Cell<int> _c;
    private readonly Cell _d;

    public Holder(Cell<int> c, Cell d)
    {
        _c = c;
        _d = d;
    }

    public void Go()
    {
        _c.Run();
    }

    public void GoD()
    {
        _d.Run();
    }
}
""",
    "src/Nc/Box.cs": """namespace Ng2;

public class Box
{
    public class Item
    {
        public void Run()
        {
        }
    }

    public class Item<T>
    {
        public void Run()
        {
        }
    }
}

public class User
{
    private readonly Box.Item _a;
    private readonly Box.Item<int> _b;

    public User(Box.Item a, Box.Item<int> b)
    {
        _a = a;
        _b = b;
    }

    public void GoA()
    {
        _a.Run();
    }

    public void GoB()
    {
        _b.Run();
    }
}
""",
    # ---- a namespace declaration's using of a namespace external code may fill imports a TYPE of the name before
    # anything further out (the integrator's shadow rule; the reader's review cases ex1 / ex2): in a block with `using
    # System.Timers;` `Timer` is System.Timers.Timer, never the parent namespace's Ne.Timer. JobE1 keys nothing
    # (JobE2, no block using: Ne.Timer.Start); ImplE1.Run(Timer) implements nothing of IRunE1, whose call runs
    # RunBaseE1.Run (the engine's same-file tuple to it stays); ImplE2.Run(Timer), no block using, is the implementer.
    # The qualified base lists (`Ne.Api.RunBaseE1`) stay proofs: a project NAMESPACE found further out still answers
    "src/Ne/Ne.csproj": """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
  </PropertyGroup>
</Project>
""",
    "src/Ne/Timer.cs": """namespace Ne
{
    public class Timer
    {
        public void Start()
        {
        }
    }
}
""",
    "src/Ne/Jobs/JobE1.cs": """namespace Ne.Jobs
{
    using System.Timers;

    public class JobE1
    {
        private readonly Timer _t;

        public JobE1(Timer t)
        {
            _t = t;
        }

        public void Go()
        {
            _t.Start();
        }
    }
}
""",
    "src/Ne/Jobs/JobE2.cs": """namespace Ne.Jobs
{
    public class JobE2
    {
        private readonly Timer _t;

        public JobE2(Timer t)
        {
            _t = t;
        }

        public void Go()
        {
            _t.Start();
        }
    }
}
""",
    "src/Ne/Api/IRunE1.cs": """namespace Ne.Api
{
    public interface IRunE1
    {
        void Run(Timer t);
    }

    public class RunBaseE1
    {
        public void Run(Ne.Timer t)
        {
        }
    }

    public class RunUserE1
    {
        private readonly IRunE1 _r;

        public RunUserE1(IRunE1 r)
        {
            _r = r;
        }

        public void Go(Ne.Timer t)
        {
            _r.Run(t);
        }
    }
}
""",
    "src/Ne/Jobs/ImplE1.cs": """namespace Ne.Jobs
{
    using System.Timers;

    public class ImplE1 : Ne.Api.RunBaseE1, Ne.Api.IRunE1
    {
        public void Run(Timer t)
        {
        }
    }
}
""",
    "src/Ne/Api/IRunE2.cs": """namespace Ne.Api
{
    public interface IRunE2
    {
        void Run(Timer t);
    }

    public class RunBaseE2
    {
        public void Run(Ne.Timer t)
        {
        }
    }

    public class RunUserE2
    {
        private readonly IRunE2 _r;

        public RunUserE2(IRunE2 r)
        {
            _r = r;
        }

        public void Go(Ne.Timer t)
        {
            _r.Run(t);
        }
    }
}
""",
    # ---- the shadow rule against the same-file rule (R26; the integrator's case ex6): JobH1's `Timer` is written in a
    # block that imports System.Timers, so it is System.Timers.Timer although the same file declares Nh.Timer in the
    # enclosing namespace (the engine's name tuple to Timer.Start stays; no typed key); JobH2's block has no using:
    # Nh.Timer (EXTRACTED, the same file)
    "src/Nh/Nh.csproj": """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
  </PropertyGroup>
</Project>
""",
    "src/Nh/All.cs": """namespace Nh
{
    public class Timer
    {
        public void Start()
        {
        }
    }

    namespace Jobs
    {
        using System.Timers;

        public class JobH1
        {
            private readonly Timer _t;

            public JobH1(Timer t)
            {
                _t = t;
            }

            public void Go()
            {
                _t.Start();
            }
        }
    }

    namespace Jobs2
    {
        public class JobH2
        {
            private readonly Timer _t;

            public JobH2(Timer t)
            {
                _t = t;
            }

            public void Go()
            {
                _t.Start();
            }
        }
    }
}
""",
    "src/Ne/Jobs/ImplE2.cs": """namespace Ne.Jobs
{
    public class ImplE2 : Ne.Api.RunBaseE2, Ne.Api.IRunE2
    {
        public void Run(Timer t)
        {
        }
    }
}
""",
}

# W5 (R42-R45): the closing round's shapes, each Roslyn-referenced beside its positive sibling (section Z)
CLOSE = {
    # ================= R42: an UNRESOLVED base may be any project class or interface =================
    # s07: AliImpl7's only base is the alias AliasR of IAli7, written where the root Xs7 is owned by no project (R32:
    # capable, R27: the alias is unknown): bases() holds ("unres", "AliasR"), which may be IAli7 itself. Roslyn: the
    # interface call runs AliImpl7.Al7; the engine's same-file tuple to it must stay
    "src/Qa/Qa.csproj": _np(),
    "src/Qa/S07.cs": """using AliasR = Xs7.IAli7;

namespace Xs7;

public interface IAli7
{
    void Al7();
}

public class AliImpl7 : AliasR
{
    public void Al7()
    {
    }
}

public class U07
{
    private readonly IAli7 _a;

    public U07(IAli7 a)
    {
        _a = a;
    }

    public void Go()
    {
        _a.Al7();
    }
}
""",
    # the positive sibling: the same under the owned root Qa: the alias resolves, AliImpl7b implements IAli7b
    "src/Qa/S07b.cs": """using AliasQ = Qa.S7.IAli7b;

namespace Qa.S7;

public interface IAli7b
{
    void Al7b();
}

public class AliImpl7b : AliasQ
{
    public void Al7b()
    {
    }
}

public class U07b
{
    private readonly IAli7b _a;

    public U07b(IAli7b a)
    {
        _a = a;
    }

    public void Go()
    {
        _a.Al7b();
    }
}
""",
    # ub4: an INTERFACE receiver; Impl4's base Base4 is written in a block that imports System.Timers (the shadow
    # rule: a project type found further out is unknown), so Impl4's override is seen under an unresolved base
    "src/Qb/Qb.csproj": _np(),
    "src/Qb/All4.cs": """namespace Qb
{
    public interface IAli4
    {
        void Al4();
    }

    public class Base4 : IAli4
    {
        public virtual void Al4()
        {
        }
    }
}

namespace Qb.Jobs
{
    using System.Timers;

    public class Impl4 : Base4
    {
        public override void Al4()
        {
        }
    }
}

namespace Qb
{
    public class User4
    {
        private readonly IAli4 _a;

        public User4(IAli4 a)
        {
            _a = a;
        }

        public void Go()
        {
            _a.Al4();
        }
    }
}
""",
    # the positive sibling: no block using: Base4p resolves, Impl4p's override is in the dispatch set
    "src/Qb/All4p.cs": """namespace Qb
{
    public interface IAli4p
    {
        void Al4p();
    }

    public class Base4p : IAli4p
    {
        public virtual void Al4p()
        {
        }
    }
}

namespace Qb.Jobs
{
    public class Impl4p : Base4p
    {
        public override void Al4p()
        {
        }
    }
}

namespace Qb
{
    public class User4p
    {
        private readonly IAli4p _a;

        public User4p(IAli4p a)
        {
            _a = a;
        }

        public void Go()
        {
            _a.Al4p();
        }
    }
}
""",
    # ub6: a CLASS receiver, virtual dispatch; the block's `using System;` (StyleCop SA1200) makes Impl6's base unknown
    "src/Qc/Qc.csproj": _np(),
    "src/Qc/All6.cs": """namespace Qc
{
    public class Base6
    {
        public virtual void Al6()
        {
        }
    }
}

namespace Qc.Jobs
{
    using System;

    public class Impl6 : Base6
    {
        public override void Al6()
        {
        }
    }
}

namespace Qc
{
    public class User6
    {
        private readonly Base6 _a;

        public User6(Base6 a)
        {
            _a = a;
        }

        public void Go()
        {
            _a.Al6();
        }
    }
}
""",
    # the positive sibling (ub5pos): no block using: Impl6p's override is proven
    "src/Qc/All6p.cs": """namespace Qc
{
    public class Base6p
    {
        public virtual void Al6p()
        {
        }
    }
}

namespace Qc.Jobs
{
    public class Impl6p : Base6p
    {
        public override void Al6p()
        {
        }
    }
}

namespace Qc
{
    public class User6p
    {
        private readonly Base6p _a;

        public User6p(Base6p a)
        {
            _a = a;
        }

        public void Go()
        {
            _a.Al6p();
        }
    }
}
""",
    # the GENERIC spelling: ImplG6's base `BaseG6<int>` under the same block using
    "src/Qc/AllG.cs": """namespace Qc
{
    public class BaseG6<T>
    {
        public virtual void AlG(T x)
        {
        }
    }
}

namespace Qc.Jobs
{
    using System;

    public class ImplG6 : BaseG6<int>
    {
        public override void AlG(int x)
        {
        }
    }
}

namespace Qc
{
    public class UserG6
    {
        private readonly BaseG6<int> _b;

        public UserG6(BaseG6<int> b)
        {
            _b = b;
        }

        public void Go()
        {
            _b.AlG(1);
        }
    }
}
""",
    # the QUALIFIED spelling: ImplQ6's base `Xq6.BaseQ6` under the root Xq6 no project owns (R32 / R27: unresolved),
    # while the receiver's `BaseQ6`, written inside namespace Xq6, is that namespace's member
    "src/Qc/AllQ.cs": """namespace Xq6
{
    public class BaseQ6
    {
        public virtual void AlQ()
        {
        }
    }

    public class UserQ6
    {
        private readonly BaseQ6 _b;

        public UserQ6(BaseQ6 b)
        {
            _b = b;
        }

        public void Go()
        {
            _b.AlQ();
        }
    }
}

namespace Xr6
{
    public class ImplQ6 : Xq6.BaseQ6
    {
        public override void AlQ()
        {
        }
    }
}
""",
    # e3: the overload rival walk: TDerE3's base `Xe3a.TBaseE3` is unresolved (root Xe3a owned by no project). C#
    # binds the base's ArmE3(System.Threading.Timer), the derived ArmE3 takes System.Timers.Timer: the engine's tuple
    # to TBaseE3.ArmE3 (one file with the caller) must stay, and TDerE3.ArmE3 is never keyed
    "src/Qf/Qf.csproj": _np(),
    "src/Qf/Base.cs": """namespace Xe3a
{
    using System.Threading;

    public class TBaseE3
    {
        public void ArmE3(Timer t)
        {
        }
    }
}

namespace Xe3b
{
    public class TUserE3
    {
        private readonly TDerE3 _d;

        public TUserE3(TDerE3 d)
        {
            _d = d;
        }

        public void Go(System.Threading.Timer t)
        {
            _d.ArmE3(t);
        }
    }
}
""",
    "src/Qf/Der.cs": """namespace Xe3b
{
    using System.Timers;

    public class TDerE3 : Xe3a.TBaseE3
    {
        public void ArmE3(Timer t)
        {
        }
    }
}
""",
    # the positive sibling: a resolved base (the owned root Qf) that holds no ArmE4: the derived method is the binding
    "src/Qf/Base4.cs": """namespace Qf.A
{
    public class TBaseE4
    {
        public void Other()
        {
        }
    }
}

namespace Qf.B
{
    public class TUserE4
    {
        private readonly TDerE4 _d;

        public TUserE4(TDerE4 d)
        {
            _d = d;
        }

        public void Go(System.Threading.Timer t)
        {
            _d.ArmE4(t);
        }
    }
}
""",
    "src/Qf/Der4.cs": """namespace Qf.B
{
    public class TDerE4 : Qf.A.TBaseE4
    {
        public void ArmE4(System.Threading.Timer t)
        {
        }
    }
}
""",
    # ---- R42, the core's audit sites (its cases ub8, ub9, ri1, mo1, ax1): each killer under the root Xd* no project
    # owns (its alias unknown), each sibling under the owned root Qd (the alias resolves)
    "src/Qd/Qd.csproj": _np(),
    **{f"src/Qd/S8{t}.cs": f"""using AliasR8 = {ns}.IAli8{t};

namespace {ns};

public interface IAli8{t}
{{
    void Al8{t}();
}}

public class Base8{t}
{{
    public void Al8{t}()
    {{
    }}
}}

public class Impl8{t} : Base8{t}, AliasR8
{{
}}

public class U08{t}
{{
    private readonly IAli8{t} _a;

    public U08{t}(IAli8{t} a)
    {{
        _a = a;
    }}

    public void Go()
    {{
        _a.Al8{t}();
    }}
}}
""" for t, ns in (("", "Xd8"), ("b", "Qd.S8"))},
    # ub9: the base class AND the interface come only through aliases: Base9.Al9 runs for an Impl9
    **{f"src/Qd/S9{t}.cs": f"""using AliasB9 = {ns}.Base9{t};
using AliasR9 = {ns}.IAli9{t};

namespace {ns};

public interface IAli9{t}
{{
    void Al9{t}();
}}

public class Base9{t}
{{
    public void Al9{t}()
    {{
    }}
}}

public class Impl9{t} : AliasB9, AliasR9
{{
}}

public class U09{t}
{{
    private readonly IAli9{t} _a;

    public U09{t}(IAli9{t} a)
    {{
        _a = a;
    }}

    public void Go()
    {{
        _a.Al9{t}();
    }}
}}
""" for t, ns in (("", "Xd9"), ("b", "Qd.S9"))},
    # ri1: Mid1 re-lists the interface through an alias: for a Mid1 its `public new` AlR runs, for a Base1 Base1's
    **{f"src/Qd/R1{t}.cs": f"""using AliasR1 = {ns}.IAli1{t};

namespace {ns};

public interface IAli1{t}
{{
    void AlR{t}();
}}

public class Base1{t} : IAli1{t}
{{
    public virtual void AlR{t}()
    {{
    }}
}}

public class Mid1{t} : Base1{t}, AliasR1
{{
    public new void AlR{t}()
    {{
    }}
}}

public class U1{t}
{{
    private readonly IAli1{t} _a;

    public U1{t}(IAli1{t} a)
    {{
        _a = a;
    }}

    public void Go()
    {{
        _a.AlR{t}();
    }}
}}
""" for t, ns in (("", "Xd1"), ("b", "Qd.R1"))},
    # mo1: CtlM's base comes through an interface-named alias: C# finds the inherited property Helper (a HelperX)
    # before the namespace's class Helper: HelperX.RunM runs
    **{f"src/Qd/M1{t}.cs": f"""using IBaseAliasM = {ns}.BaseClsM{t};

namespace {ns};

public class Helper
{{
    public static void RunM{t}()
    {{
    }}
}}

public class HelperX
{{
    public void RunM{t}()
    {{
    }}
}}

public class BaseClsM{t}
{{
    public HelperX Helper {{ get; }} = new HelperX();
}}

public class CtlM{t} : IBaseAliasM
{{
    public void Get()
    {{
        Helper.RunM{t}();
    }}
}}
""" for t, ns in (("", "Xdm"), ("b", "Qd.M1"))},
    # ax1: Both lists IRepoA<CartA> and, through an alias, IRepoA<OrderA>: a receiver typed IRepoA<OrderA> may hold a
    # Both, whose SaveA(OrderA) runs (the closed-argument rule must not exclude it)
    **{f"src/Qd/A1{t}.cs": f"""using AliasRA = {ns}.IRepoA<{ns}.OrderA>;

namespace {ns};

public class OrderA
{{
}}

public class CartA
{{
}}

public interface IRepoA<T>
{{
    void SaveA{t}(T x);
}}

public class Both{t} : IRepoA<CartA>, AliasRA
{{
    public void SaveA{t}(CartA x)
    {{
    }}

    public void SaveA{t}(OrderA x)
    {{
    }}
}}

public class UA{t}
{{
    private readonly IRepoA<OrderA> _r;

    public UA{t}(IRepoA<OrderA> r)
    {{
        _r = r;
    }}

    public void Go(OrderA o)
    {{
        _r.SaveA{t}(o);
    }}
}}
""" for t, ns in (("", "Xda"), ("b", "Qd.A1"))},
    # ---- L1 (R42 narrowed: an unresolved base named nm brings exactly the project types of that name and the
    # targets of aliases of that name, WITH their own chains; the core's cases l1chain, l1chainv, l1alias2, l1mok):
    # each killer beside an owned sibling where the base resolves. ImplC's base BaseC is unresolved (a block's
    # `using System.Timers;` may hold a BaseC): its candidate BaseC does not list IAliC, IAliC is on its CHAIN (BaseC :
    # MidC : IAliC), so a call through IAliC or MidC may run ImplC.AlC
    **{f"src/Qd/C1{t}.cs": f"""namespace {ns}
{{
    public interface IAliC{t}
    {{
        void AlC{t}();
    }}

    public class MidC{t} : IAliC{t}
    {{
        public virtual void AlC{t}()
        {{
        }}
    }}

    public class BaseC{t} : MidC{t}
    {{
    }}
}}

namespace {ns}.Jobs
{{
{use}
    public class ImplC{t} : BaseC{t}
    {{
        public override void AlC{t}()
        {{
        }}
    }}
}}

namespace {ns}
{{
    public class UserC{t}
    {{
        private readonly IAliC{t} _a;

        public UserC{t}(IAliC{t} a)
        {{
            _a = a;
        }}

        public void Go()
        {{
            _a.AlC{t}();
        }}
    }}

    public class UserCv{t}
    {{
        private readonly MidC{t} _m;

        public UserCv{t}(MidC{t} m)
        {{
            _m = m;
        }}

        public void Go()
        {{
            _m.AlC{t}();
        }}
    }}
}}
""" for t, ns, use in (("", "Qd.C1", "    using System.Timers;\n"), ("b", "Qd.C1b", ""))},
    # the alias A2 inside a namespace body names the compilation unit's alias A1 (C# resolves an alias target with
    # the outer directives): under the root Xda2 no project owns both are unknown, and BaseA.AlA runs for an ImplA
    # (C# maps IAliA.AlA to the inherited member); the owned sibling's A2 names its target `global::` (absolute): it
    # resolves and the pass proves BaseAb.AlAb
    **{f"src/Qd/A2{t}.cs": f"""using A1 = {ns}.IAliA{t};

namespace {ns}
{{
    public interface IAliA{t}
    {{
        void AlA{t}();
    }}

    public class BaseA{t}
    {{
        public void AlA{t}()
        {{
        }}
    }}
}}

namespace {ns}.Jobs
{{
    using A2 = {a2};

    public class ImplA{t} : {ns}.BaseA{t}, A2
    {{
    }}
}}

namespace {ns}
{{
    public class UserA{t}
    {{
        private readonly IAliA{t} _a;

        public UserA{t}(IAliA{t} a)
        {{
            _a = a;
        }}

        public void Go()
        {{
            _a.AlA{t}();
        }}
    }}
}}
""" for t, ns, a2 in (("", "Xda2", "A1"), ("b", "Qd.A2", "global::Qd.A2.IAliAb"))},
    # mo1 with the alias target named like an interface: CtlK's base comes through IBaseAliasK = IHostK, a CLASS that
    # holds the property HelperK (a HelperXK): C# finds that inherited member first, so HelperXK.RunK runs, never the
    # namespace's class HelperK (the killer under the root Xdk no project owns; the owned sibling proves it)
    **{f"src/Qd/MK{t}.cs": f"""using IBaseAliasK = {ns}.IHostK{t};

namespace {ns};

public class HelperK
{{
    public static void RunK{t}()
    {{
    }}
}}

public class HelperXK
{{
    public void RunK{t}()
    {{
    }}
}}

public class IHostK{t}
{{
    public HelperXK HelperK {{ get; }} = new HelperXK();
}}

public class CtlK{t} : IBaseAliasK
{{
    public void Get()
    {{
        HelperK.RunK{t}();
    }}
}}
""" for t, ns in (("", "Xdk"), ("b", "Qd.MK"))},
    # ================= R43: an unknown head or element is never proven another shape =================
    # s5: `using System;` under the root Xn5 no project owns: IntPtr, Nullable and ValueTuple are unknown names, so
    # B5's overrides read ('g', ('x', 'Nullable', ...), int) etc. Roslyn: the virtual calls run B5's overrides; the
    # engine's same-file tuples to them must stay
    "src/Qn/Qn.csproj": _np(),
    "src/Qn/S5.cs": """using System;

namespace Xn5;

public class A5
{
    public virtual void Run5(nint p)
    {
    }

    public virtual void Open5(int? x)
    {
    }

    public virtual void Pick5((int, string) t)
    {
    }

    public virtual void Arr5(int?[] x)
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

    public override void Pick5(ValueTuple<int, string> t)
    {
    }

    public override void Arr5(Nullable<int>[] x)
    {
    }
}

public class S5User
{
    private readonly A5 _a;

    public S5User(A5 a)
    {
        _a = a;
    }

    public void Go(nint p)
    {
        _a.Run5(p);
    }

    public void Go2(int? x)
    {
        _a.Open5(x);
    }

    public void Go3((int, string) t)
    {
        _a.Pick5(t);
    }

    public void Go4(int?[] x)
    {
        _a.Arr5(x);
    }
}
""",
    # the positive sibling (s5own): the owned root Qn: System's names resolve, every override is the slot's
    "src/Qn/S5b.cs": """using System;

namespace Qn.S5b;

public class A5b
{
    public virtual void Open5b(int? x)
    {
    }

    public virtual void Arr5b(int?[] x)
    {
    }
}

public class B5b : A5b
{
    public override void Open5b(Nullable<int> x)
    {
    }

    public override void Arr5b(Nullable<int>[] x)
    {
    }
}

public class S5bUser
{
    private readonly A5b _a;

    public S5bUser(A5b a)
    {
        _a = a;
    }

    public void Go2(int? x)
    {
        _a.Open5b(x);
    }

    public void Go4(int?[] x)
    {
        _a.Arr5b(x);
    }
}
""",
    # ================= R47: an interface's chain read past an opaque entry may re-list an interface =================
    # A class listing an interface whose chain holds an unresolved entry that may reach IAli (or lists IAli past
    # an external or unresolved entry) may re-implement IAli's member: from it up to the listing class, a class
    # defining the member leaves the dispatch set unknown (ADD-only). Each shape in a file of its own, so the
    # engine links the bare-name tuple (the teeth); the roots Xr47a / Xr47d / Xr47g are owned by no project
    "src/Qri/Qri.csproj": _np(),
    # ri2: Sub47a : Base47a, IX47a, and IX47a lists IAli47a only through the alias IAli47aAlias of a root no
    # project owns (unresolved): IX47a may re-list IAli47a, so Sub47a's `new` Al47a may implement it. Roslyn:
    # the interface call runs Sub47a.Al47a for a Sub47a; the engine's same-file tuple to it must stay
    "src/Qri/R2.cs": """using IAli47aAlias = Xr47a.IAli47a;

namespace Xr47a;

public interface IAli47a
{
    void Al47a();
}

public class Base47a : IAli47a
{
    public void Al47a()
    {
    }
}

public interface IX47a : IAli47aAlias
{
}

public class Sub47a : Base47a, IX47a
{
    public new void Al47a()
    {
    }
}

public class User47a
{
    private readonly IAli47a _a;

    public User47a(IAli47a a)
    {
        _a = a;
    }

    public void Go()
    {
        _a.Al47a();
    }
}
""",
    # ri3es: IX47b's base IAliChild47b is a parent namespace's name, unresolved behind the block's `using
    # System;` (SA1200); Sub47b re-implements IAli47b.Al47b explicitly. Roslyn: Sub47b's member runs for a Sub47b
    "src/Qri/R3.cs": """namespace Qri
{
    public interface IAli47b
    {
        void Al47b();
    }

    public interface IAliChild47b : IAli47b
    {
    }

    public class Base47b : IAli47b
    {
        public void Al47b()
        {
        }
    }

    public class User47b
    {
        private readonly IAli47b _a;

        public User47b(IAli47b a)
        {
            _a = a;
        }

        public void Go()
        {
            _a.Al47b();
        }
    }
}

namespace Qri.Contracts
{
    using System;

    public interface IX47b : IAliChild47b
    {
    }
}

namespace Qri
{
    public class Sub47b : Base47b, Contracts.IX47b
    {
        void IAli47b.Al47b()
        {
        }
    }
}
""",
    # xa2: IX47c : IDisposable, IAli47c lists IAli47c PAST an external entry (the proven reading stops at
    # IDisposable); Sub47c's `new` Al47c implements it. Roslyn: Sub47c.Al47c runs for a Sub47c
    "src/Qri/X2.cs": """using System;

namespace Qri.Xd;

public interface IAli47c
{
    void Al47c();
}

public class Base47c : IAli47c
{
    public void Al47c()
    {
    }
}

public interface IX47c : IDisposable, IAli47c
{
}

public class Sub47c : Base47c, IX47c
{
    public new void Al47c()
    {
    }

    public void Dispose()
    {
    }
}

public class User47c
{
    private readonly IAli47c _a;

    public User47c(IAli47c a)
    {
        _a = a;
    }

    public void Go()
    {
        _a.Al47c();
    }
}
""",
    # xa1e: IX47d lists IAli47d past an unresolved alias that can reach nothing (L1 alone would not open it),
    # and Sub47d re-implements IAli47d.Al47d explicitly. Roslyn: Sub47d's member runs for a Sub47d
    "src/Qri/X1.cs": """using IUnk47dAlias = Xr47d.IUnk47d;

namespace Xr47d;

public interface IAli47d
{
    void Al47d();
}

public interface IUnk47d
{
}

public class Base47d : IAli47d
{
    public void Al47d()
    {
    }
}

public interface IX47d : IUnk47dAlias, IAli47d
{
}

public class Sub47d : Base47d, IX47d
{
    void IAli47d.Al47d()
    {
    }
}

public class User47d
{
    private readonly IAli47d _a;

    public User47d(IAli47d a)
    {
        _a = a;
    }

    public void Go()
    {
        _a.Al47d();
    }
}
""",
    # the siblings: R3's shape without the block `using System;` (IAliChild47e resolves: Sub47e's re-listing
    # proven, every implementer keyed)
    "src/Qri/R3p.cs": """namespace Qri
{
    public interface IAli47e
    {
        void Al47e();
    }

    public interface IAliChild47e : IAli47e
    {
    }

    public class Base47e : IAli47e
    {
        public void Al47e()
        {
        }
    }

    public class User47e
    {
        private readonly IAli47e _a;

        public User47e(IAli47e a)
        {
            _a = a;
        }

        public void Go()
        {
            _a.Al47e();
        }
    }
}

namespace Qri.Contracts
{
    public interface IX47e : IAliChild47e
    {
    }
}

namespace Qri
{
    public class Sub47e : Base47e, Contracts.IX47e
    {
        public new void Al47e()
        {
        }
    }
}
""",
    # ... IAli47f listed BEFORE the external IDisposable (proven: Sub47f's member keyed)
    "src/Qri/X2p.cs": """using System;

namespace Qri.Xf;

public interface IAli47f
{
    void Al47f();
}

public class Base47f : IAli47f
{
    public void Al47f()
    {
    }
}

public interface IX47f : IAli47f, IDisposable
{
}

public class Sub47f : Base47f, IX47f
{
    public new void Al47f()
    {
    }

    public void Dispose()
    {
    }
}

public class User47f
{
    private readonly IAli47f _a;

    public User47f(IAli47f a)
    {
        _a = a;
    }

    public void Go()
    {
        _a.Al47f();
    }
}
""",
    # ... and L1's positive: IX47g's unresolved alias can reach only IUnk47g, which lists nothing, so IX47g
    # never re-lists IAli47g; Sub47g's `new` Al47g implements nothing (Roslyn: Base47g.Al47g runs for a Sub47g),
    # the dispatch set stays known and the engine's false tuple to Sub47g.Al47g is replaced
    "src/Qri/X0.cs": """using IUnk47gAlias = Xr47g.IUnk47g;

namespace Xr47g;

public interface IAli47g
{
    void Al47g();
}

public interface IUnk47g
{
}

public class Base47g : IAli47g
{
    public void Al47g()
    {
    }
}

public interface IX47g : IUnk47gAlias
{
}

public class Sub47g : Base47g, IX47g
{
    public new void Al47g()
    {
    }
}

public class User47g
{
    private readonly IAli47g _a;

    public User47g(IAli47g a)
    {
        _a = a;
    }

    public void Go()
    {
        _a.Al47g();
    }
}
""",
}

# the closing round's usings and packages (R44 / R45; section Z): no class here has a base the pass cannot resolve
CLOSEU = {
    # ============ R44: a using written inside a namespace declaration is resolved from it outward ============
    # c26b: `using Kg;` inside `namespace Qg` means Qg.Kg (it exists, in the same file), never the global Kg
    "src/Qg/Qg.csproj": _np([RN("Qg")]),
    "src/Qg/Shared/Order.cs": """namespace Kg
{
    public class Order
    {
        public void SaveG()
        {
        }
    }
}
""",
    "src/Qg/Api/User.cs": """namespace Qg.Kg
{
    public class Order
    {
        public void SaveG()
        {
        }
    }
}

namespace Qg
{
    using Kg;

    public class UserG
    {
        private readonly Order _o;

        public UserG(Order o)
        {
            _o = o;
        }

        public void Go()
        {
            _o.SaveG();
        }
    }
}
""",
    # c26relusing: the same with Qh.Kh in another file
    "src/Qh/Qh.csproj": _np([RN("Qh")]),
    "src/Qh/Kh/Order.cs": """namespace Qh.Kh
{
    public class Order
    {
        public void SaveH()
        {
        }
    }
}
""",
    "src/Qh/Shared/Order.cs": """namespace Kh
{
    public class Order
    {
        public void SaveH()
        {
        }
    }
}
""",
    "src/Qh/Api/User.cs": """namespace Qh
{
    using Kh;

    public class UserH
    {
        private readonly Order _o;

        public UserH(Order o)
        {
            _o = o;
        }

        public void Go()
        {
            _o.SaveH();
        }
    }
}
""",
    # c28relusing2: a file-scoped namespace followed by `using Ki;` (SA1200): Qi.Api.Ki does not exist, Qi.Ki does
    "src/Qi/Qi.csproj": _np([RN("Qi")]),
    "src/Qi/Ki/Order.cs": """namespace Qi.Ki
{
    public class Order
    {
        public void SaveI()
        {
        }
    }
}
""",
    "src/Qi/Shared/Order.cs": """namespace Ki
{
    public class Order
    {
        public void SaveI()
        {
        }
    }
}
""",
    "src/Qi/Api/User.cs": """namespace Qi.Api;

using Ki;

public class UserI
{
    private readonly Order _o;

    public UserI(Order o)
    {
        _o = o;
    }

    public void Go()
    {
        _o.SaveI();
    }
}
""",
    # the must-not of the other side: a using OUTSIDE every namespace declaration stays absolute: `using Ko;` at the
    # top of the file is the global Ko, although Qo.Ko exists and the class sits in namespace Qo
    "src/Qo/Qo.csproj": _np([RN("Qo")]),
    "src/Qo/Ko/Order.cs": """namespace Qo.Ko
{
    public class Order
    {
        public void SaveO()
        {
        }
    }
}
""",
    "src/Qo/Api/User.cs": """using Ko;

namespace Ko
{
    public class Order
    {
        public void SaveO()
        {
        }
    }
}

namespace Qo
{
    public class UserO
    {
        private readonly Order _o;

        public UserO(Order o)
        {
            _o = o;
        }

        public void Go()
        {
            _o.SaveO();
        }
    }
}
""",
    # c29relmap: interface mapping. `using Kj;` in Qj.A means Qj.A.Kj, in Qj.B Qj.B.Kj: two Timer types, so Impl's
    # Run(Timer) implements nothing and RunBase.Run (one file with the caller) implements IRunJ.Run
    "src/Qj/Qj.csproj": _np([RN("Qj")]),
    "src/Qj/A/Kj/Timer.cs": """namespace Qj.A.Kj
{
    public class Timer
    {
    }
}
""",
    "src/Qj/B/Kj/Timer.cs": """namespace Qj.B.Kj
{
    public class Timer
    {
    }
}
""",
    "src/Qj/A/IRunJ.cs": """namespace Qj.A
{
    using Kj;

    public interface IRunJ
    {
        void Run(Timer t);
    }

    public class RunBaseJ
    {
        public void Run(Timer t)
        {
        }
    }

    public class RunUserJ
    {
        private readonly IRunJ _r;

        public RunUserJ(IRunJ r)
        {
            _r = r;
        }

        public void Go(Timer t)
        {
            _r.Run(t);
        }
    }
}
""",
    "src/Qj/B/ImplJ.cs": """namespace Qj.B
{
    using Kj;

    public class ImplJ : Qj.A.RunBaseJ, Qj.A.IRunJ
    {
        public void Run(Timer t)
        {
        }
    }
}
""",
    # c30fsrel (feature folders, SA1200 after a file-scoped namespace): `using Models;` is Qk.Orders.Models in one
    # file and Qk.Carts.Models in the other: CartHandler.Handle implements nothing, HandlerBase.Handle implements
    "src/Qk/Qk.csproj": _np([RN("Qk")]),
    "src/Qk/Orders/Models/Item.cs": """namespace Qk.Orders.Models;

public class Item
{
}
""",
    "src/Qk/Carts/Models/Item.cs": """namespace Qk.Carts.Models;

public class Item
{
}
""",
    "src/Qk/Orders/IHandler.cs": """namespace Qk.Orders;

using Models;

public interface IHandler
{
    void Handle(Item i);
}

public class HandlerBase
{
    public void Handle(Item i)
    {
    }
}

public class Caller
{
    private readonly IHandler _h;

    public Caller(IHandler h)
    {
        _h = h;
    }

    public void Go(Item i)
    {
        _h.Handle(i);
    }
}
""",
    "src/Qk/Carts/CartHandler.cs": """namespace Qk.Carts;

using Models;

public class CartHandler : Qk.Orders.HandlerBase, Qk.Orders.IHandler
{
    public void Handle(Item i)
    {
    }
}
""",
    # the positive sibling: the same `using Models;` in Qk.Orders and in Qk.Orders.Sub resolves to one namespace
    # (Qk.Orders.Sub.Models does not exist): one Item, OrderHandler2.Handle implements IHandler2.Handle
    "src/Qk/Orders/IHandler2.cs": """namespace Qk.Orders;

using Models;

public interface IHandler2
{
    void Handle2(Item i);
}

public class HandlerBase2
{
    public void Handle2(Item i)
    {
    }
}

public class Caller2
{
    private readonly IHandler2 _h;

    public Caller2(IHandler2 h)
    {
        _h = h;
    }

    public void Go(Item i)
    {
        _h.Handle2(i);
    }
}
""",
    "src/Qk/Orders/Sub/OrderHandler2.cs": """namespace Qk.Orders.Sub;

using Models;

public class OrderHandler2 : Qk.Orders.HandlerBase2, Qk.Orders.IHandler2
{
    public void Handle2(Item i)
    {
    }
}
""",
    # c27b: `global::` is absolute: `using Ord = global::Kl.Order;` inside `namespace Ql` is the global Kl.Order (the
    # same file), never Ql.Kl.Order
    "src/Ql/Ql.csproj": _np([RN("Ql")]),
    "src/Ql/Kl/Order.cs": """namespace Ql.Kl
{
    public class Order
    {
        public void SaveL()
        {
        }
    }
}
""",
    "src/Ql/Api/User.cs": """namespace Kl
{
    public class Order
    {
        public void SaveL()
        {
        }
    }
}

namespace Ql
{
    using Ord = global::Kl.Order;

    public class UserL
    {
        private readonly Ord _o;

        public UserL(Ord o)
        {
            _o = o;
        }

        public void Go()
        {
            _o.SaveL();
        }
    }
}
""",
    # c27globalalias: the same with the global Km.Order in another file
    "src/Qm/Qm.csproj": _np([RN("Qm")]),
    "src/Qm/Km/Order.cs": """namespace Qm.Km
{
    public class Order
    {
        public void SaveM()
        {
        }
    }
}
""",
    "src/Qm/Shared/Order.cs": """namespace Km
{
    public class Order
    {
        public void SaveM()
        {
        }
    }
}
""",
    "src/Qm/Api/User.cs": """namespace Qm
{
    using Ord = global::Km.Order;

    public class UserM
    {
        private readonly Ord _o;

        public UserM(Ord o)
        {
            _o = o;
        }

        public void Go()
        {
            _o.SaveM();
        }
    }
}
""",
    # the unknown meaning: `using Ku;` inside `namespace Xu`, a namespace no project owns (R32: capable): Xu.Ku may be
    # an external namespace that holds an Order, so the directive's meaning is unknown (Roslyn: the global Ku.Order,
    # the same file; the engine's tuple to it stays, no key)
    "src/Qu/Qu.csproj": _np(),
    "src/Qu/User.cs": """namespace Ku
{
    public class Order
    {
        public void SaveU()
        {
        }
    }
}

namespace Xu
{
    using Ku;

    public class UserU
    {
        private readonly Order _o;

        public UserU(Order o)
        {
            _o = o;
        }

        public void Go()
        {
            _o.SaveU();
        }
    }
}
""",
    # ---- R44 on the core side (the reader's r44ulev / r44ulevb): using levels compare MEANINGS, never texts. The same
    # text `using Ev;` means Qv.Orders.Ev in Qv.Orders (a project namespace without Item: Item comes from the
    # compilation unit's `using Lv;`) and the global Ev in Qv.Carts (vendor/: an external stand-in, never read): two
    # Item types, so CartHandlerV.HandleV implements nothing and HandlerBaseV.HandleV runs
    "src/Qv/Qv.csproj": _np([RN("Qv")]),
    "src/Qv/vendor/Ext.cs": "namespace Ev\n{\n    public class Item\n    {\n    }\n}\n\nnamespace Lv\n{\n"
                            "    public class Item\n    {\n    }\n}\n",
    "src/Qv/Orders/Ev/Marker.cs": "namespace Qv.Orders.Ev\n{\n    public class Marker\n    {\n    }\n}\n",
    "src/Qv/Orders/IHandler.cs": """using Lv;

namespace Qv.Orders
{
    using Ev;

    public interface IHandlerV
    {
        void HandleV(Item i);
    }

    public class HandlerBaseV
    {
        public void HandleV(Item i)
        {
        }
    }

    public class CallerV
    {
        private readonly IHandlerV _h;

        public CallerV(IHandlerV h)
        {
            _h = h;
        }

        public void Go(Item i)
        {
            _h.HandleV(i);
        }
    }
}
""",
    "src/Qv/Carts/CartHandler.cs": """using Lv;

namespace Qv.Carts
{
    using Ev;

    public class CartHandlerV : Qv.Orders.HandlerBaseV, Qv.Orders.IHandlerV
    {
        public void HandleV(Item i)
        {
        }
    }
}
""",
    # the positive sibling: no Qw.Orders.Ew, so both `using Ew;` mean the global Ew: one Item, CartHandlerW implements
    "src/Qw/Qw.csproj": _np([RN("Qw")]),
    "src/Qw/vendor/Ext.cs": "namespace Ew\n{\n    public class Item\n    {\n    }\n}\n\nnamespace Lw\n{\n"
                            "    public class Item\n    {\n    }\n}\n",
    "src/Qw/Orders/IHandler.cs": """using Lw;

namespace Qw.Orders
{
    using Ew;

    public interface IHandlerW
    {
        void HandleW(Item i);
    }

    public class HandlerBaseW
    {
        public void HandleW(Item i)
        {
        }
    }

    public class CallerW
    {
        private readonly IHandlerW _h;

        public CallerW(IHandlerW h)
        {
            _h = h;
        }

        public void Go(Item i)
        {
            _h.HandleW(i);
        }
    }
}
""",
    "src/Qw/Carts/CartHandler.cs": """using Lw;

namespace Qw.Carts
{
    using Ew;

    public class CartHandlerW : Qw.Orders.HandlerBaseW, Qw.Orders.IHandlerW
    {
        public void HandleW(Item i)
        {
        }
    }
}
""",
    # ---- R44 in NESTED blocks (the reader's r44n2): `namespace Qx { namespace Web { using Kx; } }` means Qx.Web.Kx
    # (the nearest level first), never Qx.Kx nor the global Kx
    "src/Qx/Qx.csproj": _np([RN("Qx")]),
    **{f"src/Qx/{d}/Order.cs": "namespace " + ns + "\n{\n    public class Order\n    {\n        public void SaveX()\n"
       "        {\n        }\n    }\n}\n" for d, ns in (("Kx", "Qx.Kx"), ("Shared", "Kx"), ("Web/Kx", "Qx.Web.Kx"))},
    "src/Qx/Web/User.cs": """namespace Qx
{
    namespace Web
    {
        using Kx;

        public class UserX
        {
            private readonly Order _o;

            public UserX(Order o)
            {
                _o = o;
            }

            public void Go()
            {
                _o.SaveX();
            }
        }
    }
}
""",
    # ---- R44 for `using static` (the reader's r44st1 / r44st2): inside namespace Qs, `using static Us.HelpersS;` means
    # Qs.Us.HelpersS (it exists); inside Qy, where Qy.Uy does not exist, `using static Uy.HelpersY;` means the global
    # Uy.HelpersY (vendor/: external), never Oy.Uy.HelpersY of another namespace block of the same file
    "src/Qs/Qs.csproj": _np([RN("Qs")]),
    **{f"src/Qs/{d}/Helpers.cs": "namespace " + ns + "\n{\n    public static class HelpersS\n    {\n"
       "        public static void RunS()\n        {\n        }\n    }\n}\n"
       for d, ns in (("Us", "Qs.Us"), ("Shared", "Us"))},
    "src/Qs/Web/User.cs": "namespace Qs\n{\n    using static Us.HelpersS;\n\n    public class UserS\n    {\n"
                          "        public void Go()\n        {\n            RunS();\n        }\n    }\n}\n",
    "src/Qy/Qy.csproj": _np([RN("Qy")]),
    "src/Qy/vendor/Util.cs": "namespace Uy\n{\n    public static class HelpersY\n    {\n"
                             "        public static void RunY()\n"
                             "        {\n        }\n    }\n}\n",
    "src/Qy/Other/Helpers.cs": "namespace Oy.Uy\n{\n    public static class HelpersY\n    {\n"
                               "        public static void RunY()\n        {\n        }\n    }\n}\n",
    "src/Qy/Web/User.cs": "namespace Oy\n{\n    public class MarkerY\n    {\n    }\n}\n\nnamespace Qy\n{\n"
                          "    using static Uy.HelpersY;\n\n    public class UserY\n    {\n        public void Go()\n"
                          "        {\n            RunY();\n        }\n    }\n}\n",
    # ---- R44 x R27 (the reader's r44cap / r44capb): Qz is capable (the imported Qz.Legacy holds no project type:
    # external code fills it), so `using Kz;` in Qz.Web may mean an external Qz.Kz (it does: vendor/): unknown, never
    # the project's global Kz.Order; with nothing making Qr capable, `using Kq;` in Qr.Web is the global Kq
    "src/Qz/Qz.csproj": _np([RN("Qz")]),
    "src/Qz/vendor/Legacy.cs": "namespace Qz.Legacy\n{\n    public class Thing\n    {\n    }\n}\n\n"
                               "namespace Qz.Kz\n{\n    public class Order\n    {\n        public void SaveZ()\n"
                               "        {\n        }\n    }\n}\n",
    "src/Qz/Shared/Order.cs": "namespace Kz\n{\n    public class Order\n    {\n        public void SaveZ()\n        {\n"
                              "        }\n    }\n}\n",
    "src/Qz/Tools/Use.cs": "using Qz.Legacy;\n\nnamespace Qz.Tools\n{\n    public class Use\n    {\n"
                           "        private readonly Thing _t;\n\n        public Use(Thing t)\n        {\n"
                           "            _t = t;\n        }\n    }\n}\n",
    "src/Qz/Web/User.cs": "namespace Qz.Web\n{\n    using Kz;\n\n    public class UserZ\n    {\n"
                          "        private readonly Order _o;\n\n        public UserZ(Order o)\n        {\n"
                          "            _o = o;\n        }\n\n        public void Go()\n        {\n"
                          "            _o.SaveZ();\n        }\n    }\n}\n",
    "src/Qr/Qr.csproj": _np([RN("Qr")]),
    "src/Qr/Shared/Order.cs": "namespace Kq\n{\n    public class Order\n    {\n        public void SaveR()\n        {\n"
                              "        }\n    }\n}\n",
    "src/Qr/Tools/Use.cs": "namespace Qr.Tools\n{\n    public class Use\n    {\n    }\n}\n",
    "src/Qr/Web/User.cs": "namespace Qr.Web\n{\n    using Kq;\n\n    public class UserR\n    {\n"
                          "        private readonly Order _o;\n\n        public UserR(Order o)\n        {\n"
                          "            _o = o;\n        }\n\n        public void Go()\n        {\n"
                          "            _o.SaveR();\n        }\n    }\n}\n",
    # ---- L2, R29 applied to `using static` (the reader's r44sty / r44stx): C# member lookup walks the enclosing
    # types (their inherited members, every member kind) before a namespace declaration's `using static`, and a block's
    # `using static` is in effect in that block only. InnerT.Go2 binds its enclosing OuterT.RunT, InnerT3.Go4 the
    # enclosing class's inherited OuterBaseT.RunT, InnerT4.Go5 the enclosing class's delegate field; UserT.Go and
    # InnerT2.Go3 (no enclosing RunT) the imported HelpersT.RunT. In Qe the `using static` sits in another block:
    # InnerE.Go2 binds OuterE.RunE, PlainE.Go3 the compilation unit's HelpersE3.RunE, UserE2.Go its own block's
    # HelpersE.RunE (nearer than the compilation unit's)
    "src/Qt/Qt.csproj": _np([RN("Qt")]),
    "src/Qt/Util/Helpers.cs": "namespace Qt.Util\n{\n    public static class HelpersT\n    {\n"
                              "        public static void RunT()\n        {\n        }\n    }\n}\n",
    "src/Qt/Web/User.cs": """namespace Qt
{
    using static Util.HelpersT;

    public class UserT
    {
        public void Go()
        {
            RunT();
        }
    }

    public class OuterT
    {
        public static void RunT()
        {
        }

        public class InnerT
        {
            public void Go2()
            {
                RunT();
            }
        }
    }
}
""",
    "src/Qt/Web/Nested.cs": """namespace Qt
{
    using static Util.HelpersT;

    public class OuterT2
    {
        public class InnerT2
        {
            public void Go3()
            {
                RunT();
            }
        }
    }
}
""",
    "src/Qt/Web/Inherited.cs": """namespace Qt
{
    using static Util.HelpersT;

    public class OuterBaseT
    {
        public static void RunT()
        {
        }
    }

    public class OuterT3 : OuterBaseT
    {
        public class InnerT3
        {
            public void Go4()
            {
                RunT();
            }
        }
    }
}
""",
    "src/Qt/Web/Field.cs": """namespace Qt
{
    using static Util.HelpersT;

    public class OuterT4
    {
        public static System.Action RunT = () => { };

        public class InnerT4
        {
            public void Go5()
            {
                RunT();
            }
        }
    }
}
""",
    "src/Qe/Qe.csproj": _np([RN("Qe")]),
    "src/Qe/Util/Helpers.cs": "namespace Qe.Util\n{\n    public static class HelpersE\n    {\n"
                              "        public static void RunE()\n        {\n        }\n    }\n}\n",
    "src/Qe/Lib/Helpers3.cs": "namespace Qe.Lib\n{\n    public static class HelpersE3\n    {\n"
                              "        public static void RunE()\n        {\n        }\n    }\n}\n",
    "src/Qe/Web/User.cs": """namespace Qe
{
    using static Util.HelpersE;

    public class UserE
    {
        public void Go()
        {
            RunE();
        }
    }
}

namespace Qe.Other
{
    public class OuterE
    {
        public static void RunE()
        {
        }

        public class InnerE
        {
            public void Go2()
            {
                RunE();
            }
        }
    }
}
""",
    "src/Qe/Web/Plain.cs": """using static Qe.Lib.HelpersE3;

namespace Qe
{
    using static Util.HelpersE;

    public class UserE2
    {
        public void Go()
        {
            RunE();
        }
    }
}

namespace Qe.Other
{
    public class PlainE
    {
        public void Go3()
        {
            RunE();
        }
    }
}
""",
    # ================= R45: two projects' readings compare only when neither has a package =================
    # k01 (real MSBuild: a package's build/ props reach the project that references the package, never one that
    # references a project that does): QpA references Pk.Build, whose props add a `global using System.Timers;`
    # (vendor/Glob.cs stands for it: never read); QpB reaches Pk.Build only through QpC's ProjectReference, so its
    # Timer is its own `using System.Threading;`'s. Equal package sets, other usings: TickerQ1.Tick(Timer) implements
    # nothing of ITickQ1 (System.Timers.Timer), TickBaseQ1.Tick does
    "src/Qp/A/QpA.csproj": _np(items=['<PackageReference Include="Pk.Build" Version="1.0.0" />']),
    "src/Qp/A/vendor/Glob.cs": "global using System.Timers;\n",
    "src/Qp/A/TickQ1.cs": _tick("QpA", "Q1", usings="using System.Collections.Generic;\n\n",
                                  base_param="System.Timers.Timer"),
    "src/Qp/C/QpC.csproj": _np(items=['<PackageReference Include="Pk.Build" Version="1.0.0" />']),
    "src/Qp/C/vendor/Glob.cs": "global using System.Timers;\n",
    "src/Qp/C/C.cs": "namespace QpC;\n\npublic class Unused\n{\n}\n",
    "src/Qp/B/QpB.csproj": _np(items=['<ProjectReference Include="../C/QpC.csproj" />',
                                      '<ProjectReference Include="../A/QpA.csproj" />']),
    "src/Qp/B/TickerQ1.cs": _fcls("QpB", _fimpl("TickerQ1", "QpA.TickBaseQ1, QpA.ITickQ1", "Tick", "Timer t"),
                                    usings="using System.Collections.Generic;\nusing System.Threading;\n\n"),
    # the positive sibling: no package anywhere: the visible usings are all (System.Threading's Timer in both)
    "src/Qq/A/QqA.csproj": _np(),
    "src/Qq/A/TickQ2.cs": _tick("QqA", "Q2", usings="using System.Threading;\n\n"),
    "src/Qq/B/QqB.csproj": _np(items=['<ProjectReference Include="../A/QqA.csproj" />']),
    "src/Qq/B/TickerQ2.cs": _fcls("QqB", _fimpl("TickerQ2", "QqA.TickBaseQ2, QqA.ITickQ2", "Tick", "Timer t"),
                                    usings="using System.Collections.Generic;\nusing System.Threading;\n\n"),
}

TREES = {"cqrs": CQRS, "review": REVIEW, "recheck": RECHECK, "sigs": SIGS, "rnd2": RND2, "usings": USINGS,
         "resid": RESID, "sound": SOUND, "final": FINAL,
         "close": CLOSE, "closeu": CLOSEU}


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
    # the field report's repository has a project file; the fixture holds none: written beside the sources (the
    # captured graph is unaffected). Billing.csproj owns the root Billing (R32), so `using Billing.Services;` supplies
    # project types only
    proj = {"cs/Billing.csproj": '<Project Sdk="Microsoft.NET.Sdk">\n  <PropertyGroup>\n'
                                 "    <TargetFramework>net8.0</TargetFramework>\n  </PropertyGroup>\n</Project>\n"}
    bare = write_tree(tmp() / "collision_bare", src)
    for mode in MODES:
        run = run_pipeline(graph, bare, MODES[mode])
        got = typed_view(run.nodes, form="name")
        lost = [t for t in run.before if t not in run.out]
        check(f"A [{mode}] the same tree WITHOUT a project file: no typed key, nothing removed (R32: a tree with no "
              ".csproj owns no namespace root, so every enclosing namespace is capable and R27 leaves the lookups "
              "unknown: ADD-only, the engine's tuples stay)",
              got == {} and not lost and run.stats["error"] is None, (got, lost[:4], run.stats.get("error")))
    root = write_tree(tmp() / "collision", {**src, **proj})
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
    sec_reader_resid(rd)


B_EXPLICIT = """namespace Ns;

public interface IRun
{
    void Run();
}

public class K : IRun, IRepo<Order>, Ns.IRun2
{
    void IRun.Run()
    {
    }

    void IRepo<Order>.Save(Order o)
    {
    }

    void Ns.IRun2.Run2()
    {
    }

    public void Pub()
    {
    }

    private void Priv()
    {
    }

    public static implicit operator int(K k) => 0;
}

public class L
{
    public static explicit operator K(L l) => null;
}

public class M
{
    public static M operator +(M a, M b) => a;
}
"""

B_ALIASES = """using Order = Ba.Legacy.Order;

namespace Ba.Use
{
    using Ba.Models;
    using Repo = Ba.Data.SqlRepo;

    public class U1
    {
    }
}

namespace Ba.B
{
    using Zed = Ba.Data.Zed;
    using Abc = Ba.Data.Abc;

    public class U2
    {
    }
}
"""

B_ALIASES2 = """global using Tok = System.Threading.CancellationToken;
using Order = Ba.Legacy.Order;

namespace Ba.Fs;

using Repo = Ba.Data.SqlRepo;

public class U3
{
}
"""


def _tup(v):
    """Lists and tuples compared alike, recursively (the design writes the tables in either notation)."""
    return tuple(_tup(x) for x in v) if isinstance(v, (list, tuple)) else v


def sec_reader_resid(rd) -> None:
    """The residuals round's reader tables (design §3 / §5 / §7): `_cs["explicit"]` {(method, line): the explicit
    interface specifier's TypeRef}, `_cs["convops"]` (the class indexes declaring a conversion operator),
    `_cs["aliases"]` [(local, spec, frame index, global?)] and `_cs["cvalias"]` {class idx: per cvis frame, the
    frame's ((local, spec), ...) sorted}; every alias still a named import."""
    T = TC
    f = facts_of(B_EXPLICIT)
    cs = (getattr(f, "_cs", None) or {}) if f is not None else {}
    L = lambda needle: _line_of(B_EXPLICIT, needle)    # noqa: E731
    ex = cs.get("explicit")
    want = {("Run", L("void IRun.Run()")): T.t_name("IRun"),
            ("Save", L("void IRepo<Order>.Save")): T.t_generic(T.t_name("IRepo"), T.t_name("Order")),
            ("Run2", L("void Ns.IRun2.Run2()")): T.t_qual("Ns", "IRun2")}
    check("B reader: _cs explicit: `void IRun.Run()`, `void IRepo<Order>.Save(Order o)`, `void Ns.IRun2.Run2()` map "
          "(member, line) to their specifier's TypeRef; a public / private method has no entry",
          isinstance(ex, dict) and ex == want, ex)
    ci = {c.name: i for i, c in enumerate(f.classes)} if f is not None else {}
    conv = cs.get("convops")
    check("B reader: _cs convops: the classes declaring an implicit / explicit conversion operator (K, L), never one "
          "declaring only another operator (M)",
          conv is not None and {ci.get("K"), ci.get("L")} <= set(conv) and ci.get("M") not in set(conv), (conv, ci))
    fa = facts_of(B_ALIASES)
    ca = (getattr(fa, "_cs", None) or {}) if fa is not None else {}
    ai = {c.name: i for i, c in enumerate(fa.classes)} if fa is not None else {}
    check("B reader: _cs aliases: each alias with its namespace declaration's frame (0 = the compilation unit), in "
          "source order",
          _tup(ca.get("aliases")) == (("Order", "Ba.Legacy.Order", 0, False), ("Repo", "Ba.Data.SqlRepo", 1, False),
                                      ("Zed", "Ba.Data.Zed", 2, False), ("Abc", "Ba.Data.Abc", 2, False)),
          ca.get("aliases"))
    cv = ca.get("cvalias") or {}
    check("B reader: _cs cvalias: per class, per cvis frame, that frame's aliases sorted (a block's alias never "
          "another block's); cvis keeps its shape",
          _tup(cv.get(ai.get("U1"))) == ((("Order", "Ba.Legacy.Order"),), (("Repo", "Ba.Data.SqlRepo"),))
          and _tup(cv.get(ai.get("U2"))) == ((("Order", "Ba.Legacy.Order"),),
                                              (("Abc", "Ba.Data.Abc"), ("Zed", "Ba.Data.Zed")))
          and ca.get("cvis", {}).get(ai.get("U1")) == (("", ()), ("Ba.Use", ("Ba.Models",)))
          and ca.get("cvis", {}).get(ai.get("U2")) == (("", ()), ("Ba.B", ())), (cv, ca.get("cvis")))
    check("B reader: every alias is still a named import of the file",
          fa is not None and all([(i.kind, i.spec) for i in fa.imports.get(n, ())] == [("named", s)]
                                 for n, s in (("Order", "Ba.Legacy.Order"), ("Repo", "Ba.Data.SqlRepo"),
                                              ("Zed", "Ba.Data.Zed"), ("Abc", "Ba.Data.Abc"))),
          fa and fa.imports)
    fb = facts_of(B_ALIASES2)
    cb = (getattr(fb, "_cs", None) or {}) if fb is not None else {}
    bi = {c.name: i for i, c in enumerate(fb.classes)} if fb is not None else {}
    cvb = _tup((cb.get("cvalias") or {}).get(bi.get("U3")))
    check("B reader: _cs aliases: a `global using` alias flagged global (frame 0), a file-scoped namespace's alias in "
          "its frame; cvalias puts it there",
          _tup(cb.get("aliases")) == (("Tok", "System.Threading.CancellationToken", 0, True),
                                      ("Order", "Ba.Legacy.Order", 0, False), ("Repo", "Ba.Data.SqlRepo", 1, False))
          and isinstance(cvb, tuple) and len(cvb) == 2 and ("Order", "Ba.Legacy.Order") in cvb[0]
          and cvb[1] == (("Repo", "Ba.Data.SqlRepo"),), (cb.get("aliases"), cvb))


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
    # fit the hit -> INFERRED, never EXTRACTED; not known to fit -> nothing (UseString: a must-not); a virtual /
    # override pair of one signature is no rival. StoreUser.Go (the residuals round's g2): OrderStore.Save(Order) is
    # PROVEN inapplicable to a Cart (two project classes, neither on the other's chain, no conversion operator), so C#
    # binds the base's Save(Cart): INFERRED, held (the engine's tuple kept)
    _m(R_WID, "Painter.UseInt"): {_m(R_WID, "Widget.Paint"): I},
    _m(R_WID, "Painter.UseShort"): {_m(R_WID, "Widget.Paint"): I},
    _m(R_WID, "Painter.UseIntLiteral"): {_m(R_WID, "Widget.Tint"): I},
    _m(R_STORE, "StoreUser.Go"): {_m(R_STORE, "StoreBase.Save"): I},
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
    (_m(R_STORE, "StoreUser.Go"), "Save", None, "keep"),                           # g2: held, nothing removed
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
             (_m(R_STORE, "StoreUser.Go"), _m(R_STORE, "StoreBase.Save")),
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
    # SaveNext (Store<T>.Save binds; not proven) is a must-not. g2 (the residuals round): CartStore.Save(Cart) is
    # PROVEN inapplicable to an Order (two project classes, neither on the other's chain, no conversion operator) and
    # Store<T>'s Save(T) reads Save(Order) through `CartStore : Store<Order>`, so SaveOrder binds the base's Save, as
    # Roslyn does (`Rc.Gen.Store<T>.Save`, non-virtual): the rival bound, INFERRED, held (the engine's tuple kept)
    _m(W_GST, "StoreUser.SaveOrder"): {_m(W_GST, "Store.Save"): I},
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
    ("recheck", _m(W_GST, "StoreUser.SaveOrder"), "Save", "keep"),     # g2 bound Store.Save: held, never REPLACEs
    ("recheck", _m(W_GST, "StoreUser.SaveNext"), "Save", "none"),      # Store<Order>.Save binds; not proven
    ("recheck", _m(W_PLUG, "PluginHost.Start"), "Load", "keep"),       # an implementer the pass cannot see
]

# (tree, caller, target): after the WHOLE push (link, the bare-call and local-def passes, _resolve_calls) the caller
# has NO :CALLS edge to target, while the same push with typed linking off has one (the engine's tuple: teeth)
W_NO_EDGE = [
    ("recheck", _m(W_FRESH, "RepoUser.TypedNew"), _m(W_FRESH, "CachedRepo.Save")),     # rcReader 2
    ("recheck", _m(W_FRESH, "RepoUser.TargetNew"), _m(W_FRESH, "CachedRepo.Save")),
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
    # R42 as narrowed by L1 (W5, FLIPPED from W_NO_EDGE: recall only, the keys unchanged): OddPlugin's base
    # `global::Rc.Plug.IPlugin` is one the reader cannot read (an unresolved base named ''), so it may be ANY project
    # type and every interface dispatch set of the tree is open: JobUser.UseI keys JobBase.Run / FastJob.Run (Roslyn's
    # dispatch) and REPLACEs nothing, the engine's tuple to UnderNew.Run (another slot: never run) stays, a gap
    ("recheck", _m(W_JOBS, "JobUser.UseI"), _m(W_JOBS, "UnderNew.Run"), None),
    ("recheck", _m(W_GST, "StoreUser.SaveOrder"), _m(W_GST, "Store.Save"), I),     # g2: the rival bound (Roslyn's)
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
            # the known sets are counted on cqrs (W5, R42 / L1: OddPlugin's unread base leaves every interface
            # dispatch set of recheck open), the static member on recheck
            ds = (st["by_lang"].get("csharp") or {}).get("dispatch", {})
            dq = (run_for(b, "cqrs", mode).stats["by_lang"].get("csharp") or {}).get("dispatch", {})
            check(f"W {b} [{mode}]: dispatch stats count the known sets (known, cqrs) and the static member "
                  f"(recheck)", dq.get("known", 0) >= 1 and ds.get("static_member", 0) >= 1, (dq, ds))
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
# exact (DeepMap's documented narrowing). Every other caller of the tree is keyed NOTHING: CartBox.Go (Roslyn: the
# base's Fill(T); the argument's type is a type parameter: no proof either way), DrawUser.DrawIt / DrawUser2.DrawIt
# (Drawer.Draw: another Point).
S_EXPECT = {
    # D1: the hit's own type parameter is its own type: Save(Cart) beside Store<T>.Save(T) keeps a rival (typed only
    # when the argument fits, never EXTRACTED), while Keep(T) overriding Store<T>.Keep(T) is the same slot
    _m(S_SU, "StoreUser.SaveCart"): {_m(S_STO, "CartStore.Save"): I},
    _m(S_SU, "StoreUser.KeepOrder"): {_m(S_STO, "CartStore.Keep"): I},
    _m(S_LB, "LastUser.SaveCart"): {_m(S_LB, "CartStoreX.Save"): I},
    # g2 (the residuals round): CartStore<Order>.Save(Cart) is PROVEN inapplicable to an Order (two project classes,
    # neither on the other's chain, no conversion operator), so overload resolution takes the base's Save(T = Order):
    # the rival bound, INFERRED (never EXTRACTED) and held (no REPLACE: LastUser's engine tuple, the true one, kept)
    _m(S_SU, "StoreUser.SaveOrder"): {_m(S_STO, "Store.Save"): I},
    _m(S_LB, "LastUser.SaveOrder"): {_m(S_LB, "StoreX.Save"): I},
    # ... and Repository<Entity>.Remove(Entity) is PROVEN inapplicable to an `int` (a predefined value type converts
    # to a project class only through a conversion operator it declares: none): RepoBase.Remove(object), held
    _m(S_REP, "RepoUser.RemoveId"): {_m(S_REP, "RepoBase.Remove"): I},
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
    (_m(S_STO, "CartBox.Go"), "Fill", True),           # D1 O03: Roslyn Box<T>.Fill (a type-parameter argument)
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
    (_m(S_LB, "LastUser.SaveOrder"), _m(S_LB, "StoreX.Save"), None),               # D1, the base last (g2: held)
    (_m(S_SU, "StoreUser.SaveOrder"), _m(S_STO, "Store.Save"), I),                 # g2: the rival bound
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
    ] + _s_tcmp_flagged()


def _s_tcmp_flagged():
    """The residuals round's lookup-order entries (_Linker.cs_scope_levels, the design's F3): ("m", ns, ext) where ext
    = an external assembly may declare types in ns (capable); ("u", usings, eff) where eff = the usings that may supply
    an external type (a namespace holding project types only, not under System / Microsoft, is transparent and left
    out); the compilation unit's ("u", usings, eff, (group, complete, possible)). Each row's answer is what C# looks
    up (the shapes of TREES["usings"], their Roslyn bindings): True only where both scopes PROVABLY reach one type."""
    SD = ("System", "Drawing")
    B7 = frozenset(_B7)
    nil = frozenset()

    def cu(usings, group="src/G", complete=True, possible=nil, eff=None):
        u = frozenset(usings)
        return ("u", u, u if eff is None else frozenset(eff), (group, complete, frozenset(possible)))
    enc_f = (("m", "Shop.Drawing.Gfx", False), ("m", "Shop.Drawing", True), ("m", "Shop", True),
             ("m", "", False), cu(()))
    enc_c = (("m", "System.Drawing.Gfx", True), ("m", "System.Drawing", True), ("m", "System", True),
             ("m", "", False), cu(()))
    trn = (("m", "Shop", False), ("u", frozenset({"Shop.Models"}), nil), ("m", "", False), cu({"System.Drawing"}))
    ext = (("m", "Shop", False), ("u", frozenset({"Ext.Gfx"}), frozenset({"Ext.Gfx"})), ("m", "", False),
           cu({"System.Drawing"}))
    # a1 / a3: CancellationToken from the project's implicit usings vs a block's `using System.Threading;` (or a
    # nearer external using first)
    ia = (("m", "Ua.Contracts", False), ("m", "Ua", False), ("m", "", False), cu(B7, "src/Ua"))
    ib = (("m", "Ua.Impl", False), ("u", frozenset({"System.Threading", "Ua.Contracts"}), frozenset({"System.Threading"})),
          ("m", "Ua", False), ("m", "", False), cu(B7, "src/Ua"))
    ic = (("m", "Ua.Impl3", False), ("u", frozenset({"Ext.Tokens", "Ua.Contracts"}), frozenset({"Ext.Tokens"})),
          ("m", "Ua", False), ("m", "", False), cu(B7, "src/Ua"))
    # a8 (U3): an empty CU set only because the group's ImplicitUsings is unreadable vs another group's System.Timers
    qa = (("m", "Uq", False), ("m", "", False), cu((), "src/Uq", False, B7))
    qb = (("m", "Ut1", False), ("m", "", False), cu({"System.Timers"}, "src/Ut1"))
    qc = (("m", "Uq", False), ("m", "", False), cu({"System.Timers"}, "src/Uq", False, B7))
    # U2: IConfiguration (no table lists it): the interface's CU using covers the block using + an empty CU
    ha = (("m", "Um.Contracts", False), ("m", "Um", False), ("m", "", False),
          cu({"Microsoft.Extensions.Configuration"}, "src/Um"))
    hb = (("m", "Um.Impl", False), ("u", frozenset({"Microsoft.Extensions.Configuration", "Um.Contracts"}),
                                    frozenset({"Microsoft.Extensions.Configuration"})),
          ("m", "Um", False), ("m", "", False), cu((), "src/Um"))
    hx = (("m", "Um.Impl", False), ("u", frozenset({"Microsoft.Extensions.Primitives"}),
                                    frozenset({"Microsoft.Extensions.Primitives"})),
          ("m", "Um", False), ("m", "", False), cu({"Microsoft.Extensions.Configuration"}, "src/Um"))
    # c1: System.Timers (capable: no project type, under System) encloses the interface's file before its CU using
    ta = (("m", "System.Timers.Custom", True), ("m", "System.Timers", True), ("m", "System", True), ("m", "", False),
          cu({"System.Threading"}, "src/Uc"))
    tb = (("m", "Uc", False), ("m", "", False), cu({"System.Threading", "System.Timers.Custom"}, "src/Uc"))
    # c2 / c3b: ILogger a known member of the enclosing Microsoft.Extensions.Logging; the capable Microsoft.Extensions
    # first (ILogger not listed there): open
    sa = (("m", "Microsoft.Extensions.Logging", True), ("m", "Microsoft.Extensions", True), ("m", "Microsoft", True),
          ("m", "", False), cu({"System.Text"}, "src/Uc"))
    sb = (("m", "Uc", False), ("m", "", False), cu({"Microsoft.Extensions.Logging"}, "src/Uc"))
    xa = (("m", "Microsoft.Extensions.Custom", True), ("m", "Microsoft.Extensions", True), ("m", "Microsoft", True),
          ("m", "", False), cu({"Microsoft.Extensions.Logging"}, "src/Uc"))
    xb = (("m", "Uc", False), ("m", "", False),
          cu({"Microsoft.Extensions.Logging", "Microsoft.Extensions.Custom"}, "src/Uc"))
    return [
        ("flagged: a non-capable enclosing namespace (a project-only root) skipped, then the qualifier's namespace: "
         "one type", ("n", "Point", None, enc_f), ("n", "Point", ("Shop", "Drawing"), None), True),
        ("flagged (c4, flipped by R1): a project type does not close System.Drawing.Gfx, before the qualifier's: "
         "unknown", ("n", "Point", None, enc_c), ("n", "Point", SD, None), None),
        ("flagged (a): a using level of transparent namespaces only does not stop the walk: one type",
         ("n", "Point", None, trn), ("n", "Point", SD, None), True),
        ("flagged (a): a nearer level importing an external namespace stops it: unknown",
         ("n", "Point", None, ext), ("n", "Point", SD, None), None),
        ("U1 (a1): implicit usings vs a block's `using System.Threading;`: both decide System.Threading, one type",
         ("n", "CancellationToken", None, ia), ("n", "CancellationToken", None, ib), True),
        ("U1 (a3): a nearer external using first: open, unknown",
         ("n", "CancellationToken", None, ia), ("n", "CancellationToken", None, ic), None),
        ("U3 (a8): a CU set with an unseen part (an unreadable ImplicitUsings) against another group's: unknown",
         ("n", "Timer", None, qa), ("n", "Timer", None, qb), None),
        ("U3: the same group's unseen part is the same set: the subset clause holds, one type",
         ("n", "Timer", None, qa), ("n", "Timer", None, qc), True),
        ("U2: a name no table lists, the interface's CU using covers the block using + an empty CU: one type",
         ("n", "IConfiguration", None, ha), ("n", "IConfiguration", None, hb), True),
        ("U2: a block importing another external namespace first: unknown",
         ("n", "IConfiguration", None, ha), ("n", "IConfiguration", None, hx), None),
        ("(c1): System.Timers enclosing one file before its `using System.Threading;`: unknown (never one type)",
         ("n", "Timer", None, ta), ("n", "Timer", None, tb), None),
        ("(c2): ILogger a known member of the enclosing Microsoft.Extensions.Logging vs its using: one type",
         ("n", "ILogger", None, sa), ("n", "ILogger", None, sb), True),
        ("(c3b): the capable Microsoft.Extensions encloses first, ILogger not listed there: unknown",
         ("n", "ILogger", None, xa), ("n", "ILogger", None, xb), None),
    ] + _y_tcmp_rows(cu)


def _y_tcmp_rows(cu):
    """The soundness round's rows (R2, R3): an x tuple is what the pass cannot name, so it is unknown, never "the
    same", unless one text in one lookup scope (or keywords alone) proves one type; no level may supply the name: the
    model is wrong, unknown."""
    lev = (("m", "Yd.G.C", False), ("m", "Yd.G", False), ("m", "Yd", False), ("m", "", False), cu(()))
    lvb = (("m", "Yd.G.A", False), ("m", "Yd.G", False), ("m", "Yd", False), ("m", "", False), cu(()))
    nil = (("m", "Yc.A", False), ("m", "Yc", False), ("m", "", False), cu(()))
    nib = (("m", "Yc.B", False), ("m", "Yc", False), ("m", "", False), cu(()))
    use = (("m", "Yc.A", False), ("m", "Yc", False), ("m", "", False), cu({"Ext.Clocks"}))
    usb = (("m", "Yc.B", False), ("m", "Yc", False), ("m", "", False), cu({"Ext.Clocks"}))
    blk = (("m", "Yc.B", False), ("u", frozenset({"Ext.Clocks"}), frozenset({"Ext.Clocks"})), ("m", "Yc", False),
           ("m", "", False), cu(()))
    li = "System.Collections.Generic.List<Order>"
    return [
        ("R3 an alias the walk cannot read: unknown even against itself", ("x", "Tok", "alias?"), ("x", "Tok", "alias?"),
         None),
        ("R3 a project name the binder cannot place: unknown even against itself", ("x", "Order", "project?"),
         ("x", "Order", "project?"), None),
        ("R3 two aliases of one name: unknown even against itself", ("x", "Tok", "alias"), ("x", "Tok", "alias"), None),
        ("R3 an unreadable alias target (two groups' MSBuild aliases): unknown even against itself",
         ("x", "Tk3", "", lev), ("x", "Tk3", "", lev), None),
        ("R3 one alias, one target text, one lookup scope: one type", ("x", "L", li, lev), ("x", "L", li, lev), True),
        ("R3 a target with no scope: unknown (nothing proves where its names are looked up)", ("x", "L", li),
         ("x", "L", li), None),
        ("R3 one target text in two lookup scopes: unknown (the text is relative to its declaration)",
         ("x", "L", li, lev), ("x", "L", li, lvb), None),
        ("R3 another target text: unknown", ("x", "L", li, lev), ("x", "L", "Other.List<Order>", lev), None),
        ("R3 a type read as text, keywords only: one type", ("x", "(int, string)"), ("x", "(int, string)"), True),
        ("R3 a type read as text naming a type: unknown (the name is relative to where it is written)",
         ("x", "(Order, int)"), ("x", "(Order, int)"), None),
        ("R3 an empty text: unknown", ("x", ""), ("x", ""), None),
        ("R2 no level may supply the name (every eff empty, no capable level): unknown, whatever the levels",
         ("n", "Clock", None, nil), ("n", "Clock", None, nib), None),
        ("R2 ... the same levels too", ("n", "Clock", None, nil), ("n", "Clock", None, nil), None),
        ("R2 evidence in both: the same compilation-unit using, one type", ("n", "Clock", None, use),
         ("n", "Clock", None, usb), True),
        ("R2 one scope with no evidence: unknown", ("n", "Clock", None, nil), ("n", "Clock", None, usb), None),
        ("R2 U2 with evidence: a coarser level, one type", ("n", "Clock", None, use), ("n", "Clock", None, blk), True),
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
        def cmp(x, y):
            try:
                return fn(x, y)
            except Exception as exc:  # noqa: BLE001 — a raise is a wrong answer, never a crash of the gate
                return f"raised {exc!r}"
        bad = [(lab, cmp(x, y), cmp(y, x), want) for lab, x, y, want in _s_tcmp_table() if cmp(x, y) is not want
               or cmp(y, x) is not want]
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
N_AL3 = "src/Sg3/Aliases3.cs"
N_NS, N_LO, N_PB = f"{N2}/Nest/Outer.cs", f"{N2}/Locals/Locals.cs", f"{N2}/Pb/Shop.cs"
N_TK, N_UN = f"{N2}/Ext/TokDer.cs", f"{N2}/Ext/Unity.cs"
_N_REBOUND = {_m(N_LO, "Repo"): E, _m(N_LO, "CachedRepo"): E, _m(N_LO, "Repo.Keep"): I,
              _m(N_LO, "CachedRepo.Keep"): I}


def _n_conv(target):
    return {_m(N_CV, "Converter.Format"): I, _m(N_CV, target): I}


# {caller: {target: grade}}: EXACTLY these callers of the tree are keyed, each target the binding Roslyn 4.8 gives the
# call with its run-time dispatch (AC1); a never-rebound construction is exact (DeepMap's documented narrowing).
# Every other caller of the tree is keyed NOTHING (N_MUSTNOT: MidUser.Go, TUserE1.Go, TUserE2.Go / Go3, TUserE5.Go,
# DrawUser3.DrawIt, UseUser.Go).
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
    # g2 (the residuals round): SDer.Save(Pb.Order) is PROVEN inapplicable to a Pa.Order (two project classes, neither
    # on the other's chain, no conversion operator): the base's SBase.Save(Pa.Order), INFERRED and held. (Pa and Pb are
    # owned by R2.csproj: with a root no project owns, R32 + R27 leave both callers unknown)
    _m(N_PB, "SUser.Go"): {_m(N_PB, "SBase.Save"): I},
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
    # N2 / a5 (the residuals round): an alias an MSBuild <Using> item declares is read (Tk2 = CancellationToken,
    # I32 = int): the override slot, the interface implementer and the interface's second overload are proven
    _m(N_AL, "AliasUser.Go"): {_m(N_AL, "A3.Run3"): I, _m(N_AL, "B3.Run3"): I},
    _m(N_AL, "AliasUser.Go2"): {_m(N_AL, "A3.Num3"): I, _m(N_AL, "B3.Num3"): I},
    _m(N_AL, "AliasUser.Go4"): {_m(N_AL, "A4.Run4"): I},       # B4's `new virtual Run4(Tk2)` starts another slot
    _m(N_AL, "AliasUser.Go5"): {_m(N_AL, "IRun5.Run5"): I, _m(N_AL, "Q5.Run5"): I},   # Q5's Run5(Tk2) implements it
    _m(N_AL, "AliasUser.Go9"): {_m(N_AL, "IOv9.Put9"): I, _m(N_AL, "Impl9.Put9"): I},
    # ... while an alias item under a condition the pass cannot read (Tk3: an environment property) stays unknown: the
    # declared member only, the engine's tuple to the override that runs kept (Roslyn: B13.Run13)
    _m(N_AL3, "Alias3User.Go"): {_m(N_AL3, "A13.Run13"): I},
}

# (caller, member, engine tuple?): no typed key of the member AND no removal of its tuples
N_MUSTNOT = [
    (_m(N_OV, "MidUser.Go"), "M7", True),          # R3: Roslyn Mid7.M7(double), not Der7's override
    (_m(N_TD, "TUserE1.Go"), "ArmE1", True),       # R1 E1: another file's `using System.Timers` (Roslyn TBaseE1)
    (_m(N_BL, "TUserE2.Go"), "ArmE2", True),       # R1 E2: another namespace block's using (Roslyn TBaseE2)
    (_m(N_BL, "TUserE2.Go3"), "ArmE3", True),      # R1 E3: qualified vs this block's System.Timers
    (_m(N_BL2, "TUserE5.Go"), "ArmE3", True),      # R1: the block's System.Timers before the file's System.Threading
    (_m(N_NU, "DrawUser3.DrawIt"), "Draw", True),  # another .csproj's `global using System.Drawing` is never seen
    (_m(N_UN, "UseUser.Go"), "Use", True),          # UnityEngine.Object is no `object`
    # c4 (flipped by R1, a decision: recall loss, nothing removed; Roslyn GDrawer2.Draw on this tree, the engine's
    # same-file tuple to it kept): a project type no longer closes System.Drawing.Gfx, so its Point is unknown
    (_m(N_GF, "DrawUser2.DrawIt"), "Draw", True),
]

# (caller, target): after the WHOLE push the caller has NO :CALLS edge to target, while the push with typed linking
# off has one (the engine's tuple: teeth)
N_NO_EDGE = [
    (_m(N_SL, "SlotUser.Go8"), _m(N_SL, "C2.Run3")),   # an override of B2's `new virtual Run3`: another slot
    (_m(N_SL, "SlotUser.Go9"), _m(N_SL, "C2.Run4")),   # ... of B2's `internal new virtual Run4` (one project)
    (_m(N_LO, "LocalUser.Fresh"), _m(N_LO, "CachedRepo.Keep")),  # `Repo b = new Repo()`, never rebound
    (_m(N_AL, "AliasUser.Go4"), _m(N_AL, "C4.Run4")),  # a5: an override of B4's `new virtual Run4(Tk2)`: another slot
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
    (_m(N_AL3, "Alias3User.Go"), _m(N_AL3, "B13.Run13"), None),  # a5 must-not: the slot unknown, the engine's tuple
    (_m(N_PB, "SUser.Go"), _m(N_PB, "SBase.Save"), I),          # g2: the rival bound
    (_m(N_PB, "SUser.Go"), _m(N_PB, "SDer.Save"), None),        # ... held: the engine's tuple kept (no REPLACE)
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
        got = _rfree(bd, "src/P/U/Use.cs", "Ord")
        other = _rfree(bd, "src/Q/Use.cs", "Ord")
    check("N binder: a `global using Ord = P.M.Order;` resolves Ord to the class in another file of its project, and "
          "to nothing in another project",
          getattr(got, "kind", "") == "symbol" and getattr(got, "rel", "") == "src/P/M/Order.cs" and other is None,
          (got, other))


def _rfree(bd, rel, name, arity=0):
    """bd.resolve_free(rel, name) for a spelling with `arity` written type arguments (R30: the binder takes the arity;
    without it every hit is unknown); a binder of an older round takes no arity."""
    try:
        return bd.resolve_free(rel, name, arity=arity)
    except TypeError:
        return bd.resolve_free(rel, name)


# ================================================================================================
# U / X: the reader residuals round (tests TREES["usings"] / TREES["resid"]), on the typed keys AND the pushed
# :CALLS edges; csharp_msbuild on in-memory trees; its SDK lists against the kit reference's
# ================================================================================================

def _sec_tree(tag, tree, buckets, env_of, expect, mustnot, no_edge, absent, edge, free=()) -> None:
    """One residuals tree, the N section's way: EXACTLY `expect` keyed (each target the binding Roslyn 4.8 gives the
    call, with its run-time dispatch); `mustnot` (caller, member, kind, teeth): "none" = no typed key of the member
    and no removal of its tuples, "keep" = no removal (typed keys allowed), teeth = the engine has a tuple of it;
    `no_edge` (caller, target): no pushed edge after the WHOLE push (link, the bare-call and local-def passes,
    _resolve_calls) while typed linking off has one; `absent`: no edge either way; `edge` (caller, target, grade):
    pushed with that grade (None: any grade, the engine's own tuple kept); `free`: callers outside the exact comparison
    (the caller checks them). A file under vendor/ is never read (a stand-in for a package: section Y)."""
    src = TREES[tree]
    n_cs = len([r for r in src if r.endswith(".cs") and "/vendor/" not in r])
    for b in buckets:
        for mode in MODES:
            k = (b, tree, mode, "tree")
            if k not in _RUNS:
                _RUNS[k] = run_pipeline(captured(b, tree)["graph"], root_for(tree), env_of(mode))
            run = _RUNS[k]
            st = run.stats
            check(f"{tag} {b}/{tree} [{mode}]: every C# file read ({n_cs}), none failed, no error",
                  st["error"] is None and st["files_failed"] == 0 and st["files"] == n_cs,
                  (st["error"], st["files_failed"], st["files"]))
            got = typed_view(run.nodes, form="name")
            diff = {c: (got.get(c), expect.get(c)) for c in sorted(set(got) | set(expect))
                    if got.get(c) != expect.get(c) and c not in free}
            check(f"{tag} {b}/{tree} [{mode}]: the typed keys and grades are exactly Roslyn's bindings and dispatch "
                  f"({len(expect)} callers)", not diff, diff)
            name_of = {n["key"]: n.get("name") for n in run.nodes}
            for caller, member, kind, teeth in mustnot:
                node = node_of(run.nodes, caller)
                if node is None:
                    check(f"{tag} {b} [{mode}] {caller}: the caller is a node", False)
                    continue
                typed = [t for t in (node.get("call_typed") or {}) if tc().name_seg(name_of.get(t, "")) == member]
                pre, post = tuples_of(run, run.before, caller, member), tuples_of(run, run.out, caller, member)
                lost = [t for t in pre if t not in post]
                if kind == "none":
                    check(f"{tag} {b} [{mode}] {caller.split('::')[-1]} `{member}`: no typed key, no removal (the "
                          f"engine has {len(pre)} tuple(s) of it)", bool(pre) == teeth and not typed and not lost,
                          (pre, typed, lost))
                else:
                    check(f"{tag} {b} [{mode}] {caller.split('::')[-1]} `{member}`: no removal (the engine has "
                          f"{len(pre)} tuple(s) of it)", bool(pre) == teeth and not lost, (pre, post, lost))
            edges = push_edges(run, root_for(tree), env_of(mode))
            r0 = run_pipeline(captured(b, tree)["graph"], root_for(tree), dict(env_of(mode), DEEPMAP_TYPED_CALLS="0"))
            off = push_edges(r0, root_for(tree), dict(env_of(mode), DEEPMAP_TYPED_CALLS="0"))
            for caller, target in no_edge:
                ck, tk = key_of(run.nodes, caller), key_of(run.nodes, target)
                check(f"{tag} {b} [{mode}] {caller.split('::')[-1]}: no pushed edge to {target} (typed linking off: "
                      f"{off.get((ck, tk))})", ck is not None and tk is not None and (ck, tk) not in edges
                      and (ck, tk) in off, (ck, tk, edges.get((ck, tk)), off.get((ck, tk))))
            for caller, target in absent:
                ck, tk = key_of(run.nodes, caller), key_of(run.nodes, target)
                check(f"{tag} {b} [{mode}] {caller.split('::')[-1]}: no edge to {target}, typed linking on or off",
                      ck is not None and tk is not None and (ck, tk) not in edges and (ck, tk) not in off,
                      (ck, tk, edges.get((ck, tk)), off.get((ck, tk))))
            for caller, target, grade in edge:
                ck, tk = key_of(run.nodes, caller), key_of(run.nodes, target)
                g = edges.get((ck, tk))
                check(f"{tag} {b} [{mode}] {caller.split('::')[-1]} -> {target}: pushed "
                      f"{grade or 'kept'}{'' if grade else ' (the engine has it: ' + str(off.get((ck, tk))) + ')'}",
                      g is not None and (grade is not None and g == grade or grade is None and (ck, tk) in off), g)
    if len(buckets) == 2:
        for mode in MODES:
            a = typed_view(_RUNS[(buckets[0], tree, mode, "tree")].nodes)
            z = typed_view(_RUNS[(buckets[1], tree, mode, "tree")].nodes)
            diff = {c: (a.get(c), z.get(c)) for c in sorted(set(a) | set(z)) if a.get(c) != z.get(c)}
            check(f"{tag} {tree} [{mode}]: identical typed tuples on version-stable keys, 0.8.39 vs 0.8.51",
                  not diff and bool(a), diff)


UA_, UT1, UT2, UQ, UR, UN, UP, UPT = "src/Ua", "src/Ut1", "src/Ut2", "src/Uq", "src/Ur", "src/Un", "src/Up", "src/Upt"
U_IP, U_IPA, U_IP3 = f"{UA_}/Contracts/IPut.cs", f"{UA_}/Contracts/IPutA.cs", f"{UA_}/Contracts/IPut3.cs"
U_PT, U_PTA, U_PT3 = f"{UA_}/Impl/Putter.cs", f"{UA_}/Impl/PutterA.cs", f"{UA_}/Impl3/Putter3.cs"
U_PU, U_HI, U_HH, U_HU = f"{UA_}/Use/PutUser.cs", "src/Um/Contracts/IHandle.cs", "src/Um/Impl/Handler.cs", \
    "src/Um/Use/HandleUser.cs"
U_TK, U_TKQ, U_TKR, U_TKQR = f"{UT1}/ITick.cs", f"{UT1}/ITickQ.cs", f"{UT2}/Ticker.cs", f"{UQ}/TickerQ.cs"
U_REM, U_REMT, U_T7R = f"{UR}/IRem.cs", f"{UN}/RemTicker.cs", f"{UN}/Ticker7.cs"
U_IP7, U_PT7, U_P7U = f"{UP}/Contracts/IPut7.cs", f"{UP}/Impl/Putter7.cs", f"{UP}/Use/Put7User.cs"
U_IP7B, U_TK7, U_PT7B, U_P7BU = f"{UPT}/Contracts/IPut7b.cs", f"{UPT}/Contracts/ITick7.cs", \
    f"{UPT}/Impl/Putter7b.cs", f"{UPT}/Use/Put7bUser.cs"
U_MO, U_LO, U_OU, U_OU2, U_IN = "src/Ba/Models/Order.cs", "src/Ba/Legacy/Order.cs", "src/Ba/Use/OrderUser.cs", \
    "src/Ba/Use/OrderUser2.cs", "src/Ba/C/Inner.cs"
U_SQL, U_BR, U_BL, U_A5, U_B5 = "src/Ba/Data/SqlRepo.cs", "src/Ba/B/Repo.cs", "src/Ba/Blocks.cs", "src/Ba/Sig/A5.cs", \
    "src/Ba/Sig/B5.cs"
U_TM, U_TMD, U_SK, U_SK3, U_SK3B = "src/Uc/Timers.cs", "src/Uc/Timed.cs", "src/Uc/Sink.cs", "src/Uc/Sink3.cs", \
    "src/Uc/Sink3b.cs"
U_SKI, U_SK3I, U_SK3BI, U_SKU = "src/Uc/SinkImpl.cs", "src/Uc/Sink3Impl.cs", "src/Uc/Sink3bImpl.cs", \
    "src/Uc/SinkUser.cs"
U_BL2, U_D2R, U_LK = "src/Ba/Blocks2.cs", "src/Ba/Data2/Repo.cs", "src/Ul/Slots.cs"

# {caller: {target: grade}}: EXACTLY these callers of the tree are keyed, each target the binding Roslyn 4.8 gives the
# call (the kit's reference run on this exact tree), with its run-time dispatch (AC1); a "gap" names a binding Roslyn
# makes that the pass leaves unknown on purpose (ADD-only: the interface member alone, nothing removed).
U_EXPECT = {
    # a1: the interface relies on the project's implicit `global using System.Threading;`, the implementer's using
    # sits inside its namespace block (SA1200): both decide System.Threading (U1). a2: both at the top. a3: a nearer
    # external using (Ext.Tokens) first: the implementer unknown (gap: Roslyn binds Putter3.Put)
    _m(U_PU, "PutUser.Go"): {_m(U_IP, "IPut.Put"): I, _m(U_PT, "Putter.Put"): I},
    _m(U_PU, "PutUser.GoA"): {_m(U_IPA, "IPutA.Put"): I, _m(U_PTA, "PutterA.Put"): I},
    _m(U_PU, "PutUser.Go3"): {_m(U_IP3, "IPut3.Put"): I},
    # U2: a name no table lists (IConfiguration): the interface's file-level using covers the implementer's block
    # using and its (empty) compilation-unit level
    _m(U_HU, "HandleUser.Go"): {_m(U_HI, "IHandle.Handle"): I, _m(U_HH, "Handler.Handle"): I},
    # a4 / a6 / a7c / a8: System.Timers.Timer against an implicit-usings project's System.Threading.Timer: an
    # overload; Roslyn maps the interface member to the public base member (TickBase.Tick ...), never the
    # implementer's own Tick. a6: the project's <Using Remove="System.Threading"/> read; a7c: Directory.Build.targets'
    # `disable` read (the last assignment); a8: an ImplicitUsings the pass cannot read compares with no other group
    _m(U_TK, "TickUser.Go"): {_m(U_TK, "ITick.Tick"): I},
    _m(U_TKQ, "TickUserQ.Go"): {_m(U_TKQ, "ITickQ.Tick"): I},
    _m(U_REM, "RemUser.Go"): {_m(U_REM, "IRem.Tick"): I},
    _m(U_TK7, "Tick7User.Go"): {_m(U_TK7, "ITick7.Tick"): I},
    # a7: ImplicitUsings from Directory.Build.props; a7b: props `enable`, targets `disable`, the interface's own using
    _m(U_P7U, "Put7User.Go"): {_m(U_IP7, "IPut7.Put"): I, _m(U_PT7, "Putter7.Put"): I},
    _m(U_P7BU, "Put7bUser.Go"): {_m(U_IP7B, "IPut7b.Put"): I, _m(U_PT7B, "Putter7b.Put"): I},
    # b1: the block's `using Ba.Models;` beats the compilation unit's `using Order = Ba.Legacy.Order;`; b2: without
    # it the alias; b3: a block's alias applies only inside its block (UB: Ba.B.Repo); b4: an inner block alias beats
    # the outer using
    _m(U_OU, "OrderUser.Go"): {_m(U_MO, "Order.Ship"): I},
    _m(U_OU2, "OrderUser2.Go"): {_m(U_LO, "Order.Ship"): I},
    _m(U_BL, "UA.Go"): {_m(U_SQL, "SqlRepo.Save"): I},
    _m(U_BL, "UB.Go"): {_m(U_BR, "Repo.Save"): I},
    # b3b: the block alias beats the compilation unit's `using Ba.Data2;` inside its block only; the sibling block
    # finds Ba.Data2.Repo through that using
    _m(U_BL2, "UE.Go"): {_m(U_SQL, "SqlRepo.Save"): I},
    _m(U_BL2, "UF.Go"): {_m(U_D2R, "Repo.Save"): I},
    _m(U_IN, "InnerUser.Go"): {_m(U_LO, "Order.Ship"): I},
    # b5: the group's `global using Tok = ...CancellationToken;` in effect past a block's transparent project using
    # (the slot proven); B5x's block imports an external namespace first: the slot unknown (gap: Roslyn B5x.Run)
    _m(U_A5, "SigUser.Go"): {_m(U_A5, "A5.Run"): I, _m(U_B5, "B5.Run"): I},
    _m(U_A5, "SigUser.GoX"): {_m(U_A5, "A5x.Run"): I},
    # c1: `namespace System.Timers.Custom;` finds System.Timers.Timer as a member of the enclosing System.Timers,
    # before its file's `using System.Threading;` (Roslyn: TimedBase.Use implements it, never Timed.Use)
    _m(U_TM, "TimedUser.Go"): {_m(U_TM, "ITimed.Use"): I},
    # c2: ILogger a member of the enclosing Microsoft.Extensions.Logging (the project declares ILogSink there): a
    # listed member still decides at its level. c3 (flipped by R1, a decision: recall loss, nothing removed; Roslyn
    # ILogSink3.Write + Sink3.Write on this tree): a project type no longer closes Microsoft.Extensions.Logging.Custom,
    # and ILogger is not listed there: unknown. c3b: the capable ancestor Microsoft.Extensions first: unknown (gap:
    # Roslyn Sink3b.Write)
    _m(U_SKU, "SinkUser.Go"): {_m(U_SK, "ILogSink.Write"): I, _m(U_SKI, "Sink.Write"): I},
    _m(U_SKU, "SinkUser.Go3"): {_m(U_SK3, "ILogSink3.Write"): I},
    _m(U_SKU, "SinkUser.Go3b"): {_m(U_SK3B, "ILogSink3b.Write"): I},
    # an alias the pass cannot see (a `global using` of a linked file, <Compile Include>): Tk4 / I4 are names of
    # another last segment, unknown, never two types: the declared member only, nothing removed (gap: Roslyn binds
    # B14.Run14 / B14.Num14 / Impl16.Put16 through them)
    _m(U_LK, "LinkUser.Go"): {_m(U_LK, "A14.Run14"): I},
    _m(U_LK, "LinkUser.Go2"): {_m(U_LK, "A14.Num14"): I},
    _m(U_LK, "LinkUser.Go3"): {_m(U_LK, "IOv16.Put16"): I},
}

U_MUSTNOT = [
    (_m(U_TK, "TickUser.Go"), "Tick", "keep", True),       # a4: the engine's TickBase.Tick tuple (the true one)
    (_m(U_TKQ, "TickUserQ.Go"), "Tick", "keep", True),     # a8
    (_m(U_REM, "RemUser.Go"), "Tick", "keep", True),       # a6
    (_m(U_TK7, "Tick7User.Go"), "Tick", "keep", True),     # a7c
    (_m(U_TM, "TimedUser.Go"), "Use", "keep", True),       # c1
    (_m(U_A5, "SigUser.GoX"), "Run", "keep", True),        # b5x: the slot unknown
    (_m(U_PU, "PutUser.Go3"), "Put", "keep", False),       # a3 (cross-file: the engine has no tuple)
    (_m(U_SKU, "SinkUser.Go3"), "Write", "keep", False),   # c3 (R1)
    (_m(U_SKU, "SinkUser.Go3b"), "Write", "keep", False),  # c3b
    (_m(U_LK, "LinkUser.Go"), "Run14", "keep", True),      # an unseen alias: the engine's true tuples kept
    (_m(U_LK, "LinkUser.Go2"), "Num14", "keep", True),
    (_m(U_LK, "LinkUser.Go3"), "Put16", "keep", True),
]

U_NO_EDGE = [
    (_m(U_A5, "SigUser.Go"), _m(U_A5, "A5x.Run")),          # b5: the engine's same-file A5x.Run (another class)
]

U_ABSENT = [
    (_m(U_OU, "OrderUser.Go"), _m(U_LO, "Order.Ship")),     # b1: the outer alias' target
    (_m(U_BL, "UB.Go"), _m(U_SQL, "SqlRepo.Save")),         # b3: another block's alias
    (_m(U_BL2, "UF.Go"), _m(U_SQL, "SqlRepo.Save")),        # b3b: another block's alias, never a CU one
    (_m(U_TK, "TickUser.Go"), _m(U_TKR, "Ticker.Tick")),    # a4: an overload, no implementer
    (_m(U_TKQ, "TickUserQ.Go"), _m(U_TKQR, "TickerQ.Tick")),  # a8
    (_m(U_REM, "RemUser.Go"), _m(U_REMT, "RemTicker.Tick")),  # a6
    (_m(U_TK7, "Tick7User.Go"), _m(U_T7R, "Ticker7.Tick")),   # a7c
    (_m(U_TM, "TimedUser.Go"), _m(U_TMD, "Timed.Use")),     # c1
]

U_EDGE = [
    (_m(U_TK, "TickUser.Go"), _m(U_TK, "TickBase.Tick"), None),       # a4: the engine's true tuple kept
    (_m(U_TKQ, "TickUserQ.Go"), _m(U_TKQ, "TickBaseQ.Tick"), None),   # a8
    (_m(U_REM, "RemUser.Go"), _m(U_REM, "RemBase.Tick"), None),       # a6
    (_m(U_TK7, "Tick7User.Go"), _m(U_TK7, "Tick7Base.Tick"), None),   # a7c
    (_m(U_TM, "TimedUser.Go"), _m(U_TM, "TimedBase.Use"), None),      # c1
    (_m(U_A5, "SigUser.GoX"), _m(U_A5, "A5x.Run"), I),
    (_m(U_PU, "PutUser.Go"), _m(U_PT, "Putter.Put"), I),              # a1
    (_m(U_HU, "HandleUser.Go"), _m(U_HH, "Handler.Handle"), I),       # U2
    (_m(U_P7U, "Put7User.Go"), _m(U_PT7, "Putter7.Put"), I),          # a7
    (_m(U_P7BU, "Put7bUser.Go"), _m(U_PT7B, "Putter7b.Put"), I),      # a7b
    (_m(U_OU, "OrderUser.Go"), _m(U_MO, "Order.Ship"), I),            # b1
    (_m(U_BL, "UB.Go"), _m(U_BR, "Repo.Save"), I),                    # b3
    (_m(U_BL2, "UF.Go"), _m(U_D2R, "Repo.Save"), I),                  # b3b
    (_m(U_A5, "SigUser.Go"), _m(U_B5, "B5.Run"), I),                  # b5
    (_m(U_SKU, "SinkUser.Go"), _m(U_SKI, "Sink.Write"), I),           # c2
    (_m(U_LK, "LinkUser.Go"), _m(U_LK, "B14.Run14"), None),           # an unseen alias: the override that runs
    (_m(U_LK, "LinkUser.Go2"), _m(U_LK, "B14.Num14"), None),
    (_m(U_LK, "LinkUser.Go3"), _m(U_LK, "Impl16.Put16"), None),       # ... the implementer that runs
]

XD, XE, XF, XG = "src/Xd", "src/Xe", "src/Xf", "src/Xg"
X_D1, X_D2, X_D3 = f"{XD}/Runs.cs", f"{XD}/Runs2.cs", f"{XD}/Runs3.cs"
X_E1, X_E2, X_E3 = f"{XE}/Runs.cs", f"{XE}/Runs2.cs", f"{XE}/Runs3.cs"
X_CV, X_R9, X_SV, X_ST = f"{XF}/Cv8.cs", f"{XF}/Repo9.cs", f"{XG}/Saves.cs", f"{XG}/Stores.cs"
X_AC = f"{XG}/Access.cs"

X_EXPECT = {
    # d1: IRunD.Run maps to RunA's EXPLICIT implementation, which RunB's override of the public twin never overrides
    _m(X_D1, "RunUser.Go"): {_m(X_D1, "IRunD.Run"): I, _m(X_D1, "RunA.Run"): I},
    # d2: no explicit implementation: each type runs its own (AC1)
    _m(X_D2, "RunUser2.Go"): {_m(X_D2, "IRunD2.Run"): I, _m(X_D2, "RunA2.Run"): A, _m(X_D2, "RunB2.Run"): A},
    # d3: an explicit implementation of ANOTHER interface implements nothing of this one: the public base member
    _m(X_D3, "RunUser3.Go"): {_m(X_D3, "IRunD3.Run"): I, _m(X_D3, "RunBase3.Run"): I},
    # e1: a private / internal `new` method is no implicit implementer (only a public one): PubBase.Run for both
    _m(X_E1, "RunUserE.Go"): {_m(X_E1, "IRunE.Run"): I, _m(X_E1, "PubBase.Run"): I},
    _m(X_E2, "RunUserE2.Go"): {_m(X_E2, "IRunE2.Run"): I, _m(X_E2, "PubImpl.Run"): I},
    # e3: a default interface member and a private same-signature method: the interface's body runs (the
    # implementer unknown to the pass: the interface member only, nothing removed)
    _m(X_E3, "RunUserE3.Go"): {_m(X_E3, "IRunE3.Run"): I},
    # f1: Cv8<int?> is no Cv8<int> (Nullable<int>); f4: a class's `?` an annotation (Cv8<Ord9?> is Cv8<Ord9>), a
    # struct's not (Cv8<S9?> is no Cv8<S9>: no override runs); f5: Cv8<T?> unknown (every override)
    _m(X_CV, "Cv8User.Go"): {_m(X_CV, "Cv8.M8"): I, _m(X_CV, "Cv8Nul.M8"): I},
    _m(X_CV, "Cv8User.GoOrd"): {_m(X_CV, "Cv8.M8"): I, _m(X_CV, "Cv8Ord.M8"): I},
    _m(X_CV, "Cv8User.GoS"): {_m(X_CV, "Cv8.M8"): I},
    _m(X_CV, "Holder.Go"): {_m(X_CV, "Cv8.M8"): I, _m(X_CV, "Cv8Int.M8"): A, _m(X_CV, "Cv8Nul.M8"): A,
                            _m(X_CV, "Cv8Ord.M8"): A, _m(X_CV, "Cv8S.M8"): A},
    # f3: an IRepo9<int?> receiver skips IntRepo9 (an IRepo9<int>)
    _m(X_R9, "Repo9User.Go"): {_m(X_R9, "IRepo9.Get"): I, _m(X_R9, "NulRepo9.Get"): I},
    # g1: Repo11<Order>.Save(T) is Save(Order), SaveBase.Save(Order)'s signature: no rival, the hit exact in its file
    _m(X_SV, "G1User.Go"): {_m(X_SV, "Repo11.Save"): E},
    # g2: Repo11<Cart>.Save(Cart) PROVEN inapplicable to an Order: the base's Save(Order) (INFERRED, held)
    _m(X_SV, "G1User.GoCart"): {_m(X_SV, "SaveBase.Save"): I},
    _m(X_ST, "G2User.GoOrder"): {_m(X_ST, "Store11.Save"): I},
    # a hit the argument fits (its rival the base's Save(TX)): typed, never EXTRACTED. G2User.Go (an OrderC: a
    # user-defined conversion to Cart, Roslyn CartStore11.Save) and H11.GoT (a TX argument): nothing
    _m(X_ST, "H11.GoCart"): {_m(X_ST, "CartStore11.Save"): I},
    # g2 and accessibility (the integration round, Roslyn on this tree): an internal / protected internal / public
    # base overload of the caller's project binds; a private / protected / default one is skipped by C# (Root12.Put
    # binds: not proven here, nothing); a partial class whose other part holds Put(IFormattable): nothing
    _m(X_AC, "G3User.GoInt"): {_m(X_AC, "Int12.Put"): I},
    _m(X_AC, "G3User.GoProtInt"): {_m(X_AC, "PI12.Put"): I},
    _m(X_AC, "G3User.GoPub"): {_m(X_AC, "Pub12.Put"): I},
}

X_MUSTNOT = [
    (_m(X_E3, "RunUserE3.Go"), "Run", "keep", True),       # e3: implementer unknown: nothing removed
    (_m(X_CV, "Cv8User.Go"), "M8", "keep", True),          # f1: virtual dispatch: a skipped override's tuple kept
    (_m(X_CV, "Cv8User.GoOrd"), "M8", "keep", True),
    (_m(X_CV, "Cv8User.GoS"), "M8", "keep", True),
    (_m(X_SV, "G1User.GoCart"), "Save", "keep", True),     # g2: a rival binding is held (no REPLACE)
    (_m(X_ST, "G2User.GoOrder"), "Save", "keep", True),
    (_m(X_ST, "G2User.Go"), "Save", "none", True),         # g2 must-not: a conversion operator: no proof
    (_m(X_ST, "H11.GoT"), "Save", "none", True),           # a type-parameter argument: no proof
    (_m(X_AC, "G3User.GoPriv"), "Put", "none", True),      # g2 must-not: a private base overload (Roslyn Root12)
    (_m(X_AC, "G3User.GoProt"), "Put", "none", True),      # ... a protected one, the caller no subclass
    (_m(X_AC, "G3User.GoDefault"), "Put", "none", True),   # ... no modifier: private
    (_m(X_AC, "G3User.GoPart"), "Put", "none", True),      # ... Roslyn Part13.Put(IFormattable), another node
]

X_NO_EDGE = [
    (_m(X_D1, "RunUser.Go"), _m(X_D1, "RunB.Run")),        # d1: the override of the public twin
    (_m(X_D3, "RunUser3.Go"), _m(X_D3, "RunC3.Run")),      # d3: another interface's explicit implementation
    (_m(X_E1, "RunUserE.Go"), _m(X_E1, "IntImpl.Run")),    # e1: an internal `new` method
]

X_ABSENT = [
    (_m(X_E1, "RunUserE.Go"), _m(X_E1, "PrivImpl.Run")),   # e1: a private `new` method
    (_m(X_R9, "Repo9User.Go"), _m(X_R9, "IntRepo9.Get")),  # f3: an IRepo9<int>
]

X_EDGE = [
    (_m(X_D1, "RunUser.Go"), _m(X_D1, "RunA.Run"), I),
    (_m(X_D3, "RunUser3.Go"), _m(X_D3, "RunBase3.Run"), I),
    (_m(X_E1, "RunUserE.Go"), _m(X_E1, "PubBase.Run"), I),
    (_m(X_E2, "RunUserE2.Go"), _m(X_E2, "PubImpl.Run"), I),
    (_m(X_CV, "Cv8User.Go"), _m(X_CV, "Cv8Nul.M8"), I),
    (_m(X_CV, "Cv8User.Go"), _m(X_CV, "Cv8S.M8"), None),          # virtual: the skipped override's tuple kept
    (_m(X_CV, "Cv8User.GoOrd"), _m(X_CV, "Cv8Ord.M8"), I),
    (_m(X_CV, "Cv8User.GoS"), _m(X_CV, "Cv8S.M8"), None),
    (_m(X_R9, "Repo9User.Go"), _m(X_R9, "NulRepo9.Get"), I),
    (_m(X_SV, "G1User.Go"), _m(X_SV, "Repo11.Save"), E),
    (_m(X_SV, "G1User.GoCart"), _m(X_SV, "SaveBase.Save"), I),
    (_m(X_SV, "G1User.GoCart"), _m(X_SV, "Repo11.Save"), None),    # held: the engine's tuple kept
    (_m(X_ST, "G2User.GoOrder"), _m(X_ST, "Store11.Save"), I),
    (_m(X_ST, "G2User.GoOrder"), _m(X_ST, "CartStore11.Save"), None),
    (_m(X_ST, "G2User.Go"), _m(X_ST, "CartStore11.Save"), None),    # Roslyn's binding (a conversion): kept
    (_m(X_AC, "G3User.GoInt"), _m(X_AC, "Int12.Put"), I),            # g2: an accessible base overload
    (_m(X_AC, "G3User.GoPub"), _m(X_AC, "Pub12.Put"), I),
]


def _x_env(mode) -> dict:
    """MODES[mode] with graphify_extract's C# members ON (as N: the struct S9's owner node on 0.8.39 too)."""
    return dict(MODES[mode], DEEPMAP_CSHARP_MEMBERS="1")


# csharp_msbuild.read_group on in-memory trees ({rel: text}; a key "../x" is a file above root): (label, files, group,
# certain (None: not checked), possible must hold, aliases, alias_unknown must hold, statics, complete, reason)
_B7 = ("System", "System.Collections.Generic", "System.IO", "System.Linq", "System.Net.Http", "System.Threading",
       "System.Threading.Tasks")
_WEB9 = ("System.Net.Http.Json", "Microsoft.AspNetCore.Builder", "Microsoft.AspNetCore.Hosting",
         "Microsoft.AspNetCore.Http", "Microsoft.AspNetCore.Routing", "Microsoft.Extensions.Configuration",
         "Microsoft.Extensions.DependencyInjection", "Microsoft.Extensions.Hosting", "Microsoft.Extensions.Logging")
_WRK4 = ("Microsoft.Extensions.Configuration", "Microsoft.Extensions.DependencyInjection",
         "Microsoft.Extensions.Hosting", "Microsoft.Extensions.Logging")
_BWA3 = ("Microsoft.Extensions.Configuration", "Microsoft.Extensions.DependencyInjection",
         "Microsoft.Extensions.Logging")


def _proj(body: str = "", sdk: str = "Microsoft.NET.Sdk", iu: "str | None" = "enable", tf: "str | None" = "net8.0") -> str:
    head = f'<Project Sdk="{sdk}">' if sdk else '<Project ToolsVersion="15.0">'
    ln = ([f"<TargetFramework>{tf}</TargetFramework>"] if tf else []) + (
        [f"<ImplicitUsings>{iu}</ImplicitUsings>"] if iu is not None else [])
    pg = _pg(*ln) if ln else ""
    return f"{head}\n{pg}{body}</Project>\n"


def _props(body: str) -> str:
    return f"<Project>\n{body}</Project>\n"


def _items(*lines: str) -> str:
    return "  <ItemGroup>\n" + "".join(f"    {ln}\n" for ln in lines) + "  </ItemGroup>\n"


def _pg(*lines: str) -> str:
    return "  <PropertyGroup>\n" + "".join(f"    {ln}\n" for ln in lines) + "  </PropertyGroup>\n"


def _msb_rows() -> list:
    """(label, files, group, want) with want a dict over MsbUsings fields: certain / aliases / statics / complete are
    compared exactly; possible_has / alias_unknown_has / reason are 'must hold'; not_certain: names that must not be
    certain. Each row's answer is what MSBuild (SDK 8.0.202's props files, as the kit's reference evaluates them)
    adds to the compilation, read only where every input that decides it was read."""
    P = "src/P"
    b7, web, wrk, bwa = set(_B7), set(_WEB9), set(_WRK4), set(_BWA3)
    rows = [
        # the SDK lists
        ("Microsoft.NET.Sdk + ImplicitUsings enable: the base seven", {f"{P}/P.csproj": _proj()}, P,
         dict(certain=b7, aliases={}, statics=(), complete=True)),
        ("Microsoft.NET.Sdk.Web: the base seven + the web nine", {f"{P}/P.csproj": _proj(sdk="Microsoft.NET.Sdk.Web")},
         P, dict(certain=b7 | web, complete=True)),
        ("Microsoft.NET.Sdk.Worker: the base seven + the worker four",
         {f"{P}/P.csproj": _proj(sdk="Microsoft.NET.Sdk.Worker")}, P, dict(certain=b7 | wrk, complete=True)),
        ("Microsoft.NET.Sdk.BlazorWebAssembly: the base seven + its three",
         {f"{P}/P.csproj": _proj(sdk="Microsoft.NET.Sdk.BlazorWebAssembly")}, P, dict(certain=b7 | bwa, complete=True)),
        ("Microsoft.NET.Sdk.Razor: the base seven only", {f"{P}/P.csproj": _proj(sdk="Microsoft.NET.Sdk.Razor")}, P,
         dict(certain=b7, complete=True)),
        ("an SDK with a version (`Microsoft.NET.Sdk.Web/8.0.0`) is that SDK",
         {f"{P}/P.csproj": _proj(sdk="Microsoft.NET.Sdk.Web/8.0.0")}, P, dict(certain=b7 | web, complete=True)),
        ("<Sdk Name=...> names the SDK", {f"{P}/P.csproj": '<Project>\n  <Sdk Name="Microsoft.NET.Sdk.Worker" />\n'
                                                           + _pg("<TargetFramework>net8.0</TargetFramework>",
                                                                 "<ImplicitUsings>enable</ImplicitUsings>")
                                                           + "</Project>\n"}, P,
         dict(certain=b7 | wrk, complete=True)),
        ("no ImplicitUsings: nothing (complete)", {f"{P}/P.csproj": _proj(iu=None)}, P,
         dict(certain=set(), aliases={}, statics=(), complete=True)),
        ("an unknown SDK (MSTest.Sdk): the base seven only possible (it may add its own)",
         {f"{P}/P.csproj": _proj(sdk="MSTest.Sdk/3.6.0")}, P,
         dict(certain=set(), possible_has=b7, complete=False, reason="sdk_unknown")),
        ("an old-style project (no SDK: no GenerateGlobalUsings target): nothing, <Using> items inert, complete",
         {f"{P}/P.csproj": _proj(_items('<Using Include="Inert.Ns" />'), sdk="")}, P,
         dict(certain=set(), aliases={}, statics=(), complete=True)),
        ("UseMaui=true: incomplete (the MAUI workload adds its own)",
         {f"{P}/P.csproj": _proj(_pg("<UseMaui>true</UseMaui>"))}, P, dict(complete=False, reason="maui")),
        ("a group without a .csproj: nothing, complete", {f"{P}/A.cs": "class A { }\n"}, P,
         dict(certain=set(), possible=set(), aliases={}, statics=(), complete=True)),
        # ImplicitUsings spellings (MSBuild compares case-insensitively), the last assignment, props -> project ->
        # targets (AC21)
        ("ImplicitUsings `Enable` / `TRUE` / `true` are on",
         {f"{P}/A/A.csproj": _proj(iu="Enable"), f"{P}/B/B.csproj": _proj(iu="TRUE"), f"{P}/C/C.csproj": _proj(iu="true")},
         [f"{P}/A", f"{P}/B", f"{P}/C"], dict(certain=b7, complete=True)),
        ("ImplicitUsings `disable` / `false` / empty are off",
         {f"{P}/A/A.csproj": _proj(iu="disable"), f"{P}/B/B.csproj": _proj(iu="false"), f"{P}/C/C.csproj": _proj(iu="")},
         [f"{P}/A", f"{P}/B", f"{P}/C"], dict(certain=set(), complete=True)),
        ("the last assignment in the project wins",
         {f"{P}/P.csproj": _proj(_pg("<ImplicitUsings>disable</ImplicitUsings>"))}, P,
         dict(certain=set(), complete=True)),
        ("Directory.Build.props enables, the project does not mention it",
         {"src/Directory.Build.props": _props(_pg("<ImplicitUsings>enable</ImplicitUsings>")),
          f"{P}/P.csproj": _proj(iu=None)}, P, dict(certain=b7, complete=True)),
        ("Directory.Build.props enables, the project disables: off",
         {"src/Directory.Build.props": _props(_pg("<ImplicitUsings>enable</ImplicitUsings>")),
          f"{P}/P.csproj": _proj(iu="disable")}, P, dict(certain=set(), complete=True)),
        ("the project enables, Directory.Build.targets disables: off (the targets file counts)",
         {f"{P}/Directory.Build.targets": _props(_pg("<ImplicitUsings>disable</ImplicitUsings>")),
          f"{P}/P.csproj": _proj()}, P, dict(certain=set(), complete=True)),
        ("R39 Directory.Build.props found only in another letter case: a case-sensitive file system does not import "
         "it: the SDK list only possible",
         {f"{P}/directory.build.PROPS": _props(_pg("<ImplicitUsings>enable</ImplicitUsings>")),
          f"{P}/P.csproj": _proj(iu=None)}, P, dict(certain=set(), possible_has=b7, complete=False)),
        ("only the NEAREST Directory.Build.props is imported (an outer one is not, unless the nearer imports it)",
         {"src/Directory.Build.props": _props(_items('<Using Include="Outer.Ns" />')),
          f"{P}/Directory.Build.props": _props(_items('<Using Include="Inner.Ns" />')),
          f"{P}/P.csproj": _proj()}, P, dict(certain=b7 | {"Inner.Ns"}, complete=True)),
        ("the nearer props imports the outer one through GetPathOfFileAbove: both read",
         {"src/Directory.Build.props": _props(_items('<Using Include="Outer.Ns" />')),
          f"{P}/Directory.Build.props": _props(
              "  <Import Project=\"$([MSBuild]::GetPathOfFileAbove('Directory.Build.props', "
              "'$(MSBuildThisFileDirectory)../'))\" />\n" + _items('<Using Include="Inner.Ns" />')),
          f"{P}/P.csproj": _proj()}, P, dict(certain=b7 | {"Inner.Ns", "Outer.Ns"}, complete=True)),
        ("an <Import> of a literal relative path inside the tree is read",
         {"src/Shared.props": _props(_items('<Using Include="Shared.Ns" />')),
          f"{P}/P.csproj": _proj('  <Import Project="..\\Shared.props" />\n')}, P,
         dict(certain=b7 | {"Shared.Ns"}, complete=True)),
        ("an <Import> through $(MSBuildThisFileDirectory) is read",
         {f"{P}/common.props": _props(_items('<Using Include="Common.Ns" />')),
          f"{P}/P.csproj": _proj('  <Import Project="$(MSBuildThisFileDirectory)common.props" />\n')}, P,
         dict(certain=b7 | {"Common.Ns"}, complete=True)),
        ("an <Import> of a path the tree cannot resolve (an environment property): incomplete",
         {f"{P}/P.csproj": _proj('  <Import Project="$(SharedDir)\\x.props" />\n')}, P,
         dict(complete=False, reason="import_unread")),
        ("a Directory.Build.props only above root: incomplete",
         {"../Directory.Build.props": _props(_pg("<ImplicitUsings>enable</ImplicitUsings>")),
          f"{P}/P.csproj": _proj(iu=None)}, P, dict(complete=False, reason="props_above_root")),
        # <Using> items: Include lists, Remove before / after the SDK's items, Alias / Static as attributes and as
        # child elements
        ("<Using Include> adds its namespaces; a `;` list splits",
         {f"{P}/P.csproj": _proj(_items('<Using Include="A.One;B.Two" />'), iu=None)}, P,
         dict(certain={"A.One", "B.Two"}, complete=True)),
        ("a <Using Remove> in the project removes the SDK's item",
         {f"{P}/P.csproj": _proj(_items('<Using Remove="System.IO" />'))}, P,
         dict(certain=b7 - {"System.IO"}, complete=True)),
        ("a <Using Remove> in Directory.Build.props precedes the SDK's items: removes nothing of theirs",
         {f"{P}/Directory.Build.props": _props(_items('<Using Remove="System.IO" />')), f"{P}/P.csproj": _proj()}, P,
         dict(certain=b7, complete=True)),
        ("a <Using Remove> in Directory.Build.targets removes the SDK's item",
         {f"{P}/Directory.Build.targets": _props(_items('<Using Remove="System.IO" />')), f"{P}/P.csproj": _proj()},
         P, dict(certain=b7 - {"System.IO"}, complete=True)),
        ("Alias= as an attribute: an alias, never a namespace import",
         {f"{P}/P.csproj": _proj(_items('<Using Include="System.Threading.CancellationToken" Alias="Tk" />'),
                                 iu=None)}, P,
         dict(certain=set(), aliases={"Tk": "System.Threading.CancellationToken"}, complete=True)),
        ("Alias as a child element",
         {f"{P}/P.csproj": _proj(_items('<Using Include="System.Int32">\n      <Alias>I32</Alias>\n    </Using>'),
                                 iu=None)}, P,
         dict(certain=set(), aliases={"I32": "System.Int32"}, complete=True)),
        ("Static=\"true\" as an attribute and as a child element: `global using static`",
         {f"{P}/P.csproj": _proj(_items('<Using Include="System.Math" Static="true" />',
                                        '<Using Include="System.Console">\n      <Static>true</Static>\n    </Using>'),
                                 iu=None)}, P,
         dict(certain=set(), statics=("System.Math", "System.Console"), complete=True)),
        # $(P) and conditions
        ("$(P) a file sets expands (ImplicitUsings and a <Using> item)",
         {f"{P}/P.csproj": _proj(_pg("<Iu>enable</Iu>", "<Ns>My.Ns</Ns>", "<ImplicitUsings>$(Iu)</ImplicitUsings>")
                                 + _items('<Using Include="$(Ns)" />'), iu=None)}, P,
         dict(certain=b7 | {"My.Ns"}, complete=True)),
        ("R38 conditions on $(Configuration) / $(Platform) (case-insensitive, and / or / parentheses): only possible "
         "(`dotnet build` builds Debug; Release is the evaluation's model only)",
         {f"{P}/P.csproj": _proj(_items(
             '<Using Include="Rel.Ns" Condition="\'$(Configuration)\' == \'release\'" />',
             '<Using Include="Dbg.Ns" Condition="\'$(Configuration)\' == \'Debug\'" />',
             '<Using Include="Both.Ns" Condition="(\'$(Configuration)\' == \'Release\' and \'$(Platform)\' == '
             '\'AnyCPU\') or \'$(Platform)\' == \'x86\'" />'), iu=None)}, P,
         dict(certain=set(), possible_has={"Rel.Ns", "Dbg.Ns", "Both.Ns"}, complete=False)),
        ("TargetFramework is the first of TargetFrameworks (MSBuild's inner build)",
         {f"{P}/P.csproj": _proj(_pg("<TargetFrameworks>net8.0;net6.0</TargetFrameworks>") + _items(
             '<Using Include="Tf8.Ns" Condition="\'$(TargetFramework)\' == \'net8.0\'" />',
             '<Using Include="Tf6.Ns" Condition="\'$(TargetFramework)\' == \'net6.0\'" />'), iu=None, tf=None)}, P,
         dict(certain={"Tf8.Ns"}, complete=True)),
        # what the pass cannot read: never certain
        ("an ImplicitUsings under a condition on an environment property: the SDK list only possible",
         {f"{P}/P.csproj": _proj(_pg('<ImplicitUsings Condition="\'$(CI)\' == \'true\'">enable</ImplicitUsings>'),
                                 iu=None)}, P,
         dict(certain=set(), possible_has=b7, complete=False)),
        ("an ImplicitUsings set by a property function: the SDK list only possible",
         {f"{P}/P.csproj": _proj(iu="$([System.String]::Copy('enable'))")}, P,
         dict(certain=set(), possible_has=b7, complete=False)),
        ("a <Using Include> under Exists(): only possible",
         {f"{P}/P.csproj": _proj(_items('<Using Include="Maybe.Ns" Condition="Exists(\'x.txt\')" />'), iu=None)}, P,
         dict(certain=set(), possible_has={"Maybe.Ns"}, complete=False)),
        ("an alias item under a condition the pass cannot read: its name unknown, never an alias",
         {f"{P}/P.csproj": _proj(_items('<Using Include="System.Threading.CancellationToken" Alias="Tk3" '
                                        'Condition="\'$(CI)\' != \'true\'" />'), iu=None)}, P,
         dict(aliases={}, alias_unknown_has={"Tk3"}, complete=False)),
        ("a <Using Remove> under a condition the pass cannot read: the namespace only possible",
         {f"{P}/P.csproj": _proj(_items('<Using Remove="System.IO" Condition="Exists(\'x.txt\')" />'))}, P,
         dict(certain=b7 - {"System.IO"}, possible_has={"System.IO"}, complete=False, reason="remove_unread")),
        ("<Using Update>: incomplete",
         {f"{P}/P.csproj": _proj(_items('<Using Update="System.IO" Alias="Io" />'))}, P,
         dict(complete=False, reason="update_unread")),
        ("two .csproj in one directory: what both add is certain, the rest possible",
         {f"{P}/A.csproj": _proj(_items('<Using Include="Only.A" />')), f"{P}/B.csproj": _proj()}, P,
         dict(certain=b7, possible_has={"Only.A"}, complete=False, reason="two_projects")),
        ("a malformed project file: nothing certain, incomplete",
         {f"{P}/P.csproj": '<Project Sdk="Microsoft.NET.Sdk">\n  <PropertyGroup>\n'}, P,
         dict(certain=set(), complete=False, reason="xml")),
    ] + _msb_rows_sound(P, b7) + _msb_rows_final(P, b7)
    return rows


def _msb_rows_sound(P: str, b7: set) -> list:
    """The soundness round's readings, each what real MSBuild (SDK 8.0.202, `dotnet msbuild -getItem:Using`) evaluates
    (R2 packages, R4 a value kept verbatim, R5 wildcards, R6 links, R8 System.Net.Http, R9 the import points, R16 a
    value's size)."""
    http = "System.Net.Http"
    sc = _pg("<ImplicitUsings>enable</ImplicitUsings>")
    cpm = _props(_pg("<ManagePackageVersionsCentrally>true</ManagePackageVersionsCentrally>")
                 + _items('<PackageVersion Include="Pk.One" Version="1.0.0" />',
                          '<GlobalPackageReference Include="Pk.Two" Version="1.0.0" />'))
    grow = _pg(*(["<Big>ab</Big>"] + ["<Big>$(Big)$(Big)</Big>"] * 24))
    return [
        # R2: package ids, from every file read for the group and a packages.config of the group's directory
        ("R2 a PackageReference, a <Reference> up to its first comma: their ids, the reading stays complete",
         {f"{P}/P.csproj": _proj(_items('<PackageReference Include="Acme.Core" Version="1.0.0" />',
                                        '<Reference Include="Beta.Lib, Version=1.0.0.0, Culture=neutral" />'))}, P,
         dict(certain=b7, packages={"Acme.Core", "Beta.Lib"}, complete=True)),
        ("R2 PackageVersion / GlobalPackageReference of Directory.Packages.props count, a `;` list splits",
         {"src/Directory.Packages.props": cpm,
          f"{P}/P.csproj": _proj(_items('<PackageReference Include="Pa.One;Pa.Two" />'))}, P,
         dict(certain=b7, packages={"Pk.One", "Pk.Two", "Pa.One", "Pa.Two"}, complete=True)),
        ("R2 Directory.Build.props and Directory.Build.targets count",
         {f"{P}/Directory.Build.props": _props(_items('<PackageReference Include="Dp.One" Version="1" />')),
          f"{P}/Directory.Build.targets": _props(_items('<PackageReference Include="Dt.One" Version="1" />')),
          f"{P}/P.csproj": _proj()}, P, dict(certain=b7, packages={"Dp.One", "Dt.One"}, complete=True)),
        ("R2 a packages.config in the group's directory counts",
         {f"{P}/P.csproj": _proj(),
          f"{P}/packages.config": '<?xml version="1.0" encoding="utf-8"?>\n<packages>\n'
                                  '  <package id="Old.Style" version="1.0.0" targetFramework="net48" />\n</packages>\n'},
         P, dict(certain=b7, packages={"Old.Style"}, complete=True)),
        ("R2 regardless of conditions (an id only costs a proof); an unexpanded value is skipped",
         {f"{P}/P.csproj": _proj(_items(
             '<PackageReference Include="Cond.Pkg" Version="1" Condition="\'$(Nope)\' == \'1\'" />',
             '<PackageReference Include="$(PkgName)" Version="1" />'))}, P,
         dict(certain=b7, packages={"Cond.Pkg"}, complete=True)),
        ("R2 a group without a package: no id", {f"{P}/P.csproj": _proj()}, P,
         dict(certain=b7, packages=set(), links=set(), complete=True)),
        # R4: MSBuild keeps a property's white space, and the SDK's condition compares it verbatim
        ("R4 a multi-line <ImplicitUsings> is not `enable`: nothing added, complete",
         {f"{P}/P.csproj": _proj(iu="\n      enable\n    ")}, P, dict(certain=set(), complete=True)),
        ("R4 ` enable ` (spaces) is not `enable`", {f"{P}/P.csproj": _proj(iu=" enable ")}, P,
         dict(certain=set(), complete=True)),
        ("R4 ... through $(P) too",
         {f"{P}/P.csproj": _proj(_pg("<Iu> true </Iu>", "<ImplicitUsings>$(Iu)</ImplicitUsings>"), iu=None)}, P,
         dict(certain=set(), complete=True)),
        ("R4 `ENABLE` compares case-insensitively", {f"{P}/P.csproj": _proj(iu="ENABLE")}, P,
         dict(certain=b7, complete=True)),
        # R5: a wildcard is a glob on item identities (Remove), on files (Include)
        ("R5 a wildcard <Using Remove>: each namespace it may match only possible",
         {f"{P}/P.csproj": _proj(_items('<Using Remove="System.Thread*" />'))}, P,
         dict(certain=b7 - {"System.Threading", "System.Threading.Tasks"}, complete=False, reason="remove_unread",
              possible_has={"System.Threading", "System.Threading.Tasks"})),
        ("R5 `?` too", {f"{P}/P.csproj": _proj(_items('<Using Remove="System.I?" />'))}, P,
         dict(not_certain={"System.IO"}, possible_has={"System.IO"}, complete=False, reason="remove_unread")),
        ("R5 `*` alone: every namespace only possible",
         {f"{P}/P.csproj": _proj(_items('<Using Remove="*" />'))}, P,
         dict(certain=set(), possible_has=b7, complete=False, reason="remove_unread")),
        ("R5 a wildcard <Using Include>: never certain",
         {f"{P}/P.csproj": _proj(_items('<Using Include="System.Timer?" />'), iu=None)}, P,
         dict(not_certain={"System.Timer?", "System.Timers"}, complete=False, reason="using_unread")),
        # R6: <Compile Include> reaching outside the project's directory
        ("R6 a glob of another directory: its prefix, the project's own items none",
         {f"{P}/P.csproj": _proj(_items('<Compile Include="../Shared/**/*.cs" />', '<Compile Include="Sub/*.cs" />'))},
         P, dict(certain=b7, links={"src/Shared/"}, complete=True)),
        ("R6 one file of another directory, backslashes",
         {f"{P}/P.csproj": _proj(_items('<Compile Include="..\\Core\\Linked.cs" />'))}, P,
         dict(certain=b7, links={"src/Core/Linked.cs"}, complete=True)),
        ("R6 an absolute path: anywhere",
         {f"{P}/P.csproj": _proj(_items('<Compile Include="/opt/shared/A.cs" />'))}, P,
         dict(certain=b7, links={""}, complete=True)),
        ("R6 an unreadable $(...): anywhere",
         {f"{P}/P.csproj": _proj(_items('<Compile Include="$(SharedDir)/A.cs" />'))}, P,
         dict(certain=b7, links={""}, complete=True)),
        ("R6 a path that leaves the tree: '..' (no file DeepMap sees; the project links what it cannot read)",
         {f"{P}/P.csproj": _proj(_items('<Compile Include="../../../../Outside.cs" />'))}, P,
         dict(certain=b7, links={".."}, complete=True)),
        ("R6 a .projitems shared project is an <Import>: its Compile items, relative to the project",
         {"src/Sh/Sh.projitems": _props(_items('<Compile Include="$(MSBuildThisFileDirectory)T.cs" />')),
          f"{P}/P.csproj": _proj('  <Import Project="..\\Sh\\Sh.projitems" Label="Shared" />\n')}, P,
         dict(certain=b7, links={"src/Sh/T.cs"}, complete=True)),
        # R8: System.Net.Http carries the SDK's condition (Microsoft.NET.Sdk.CSharp.props:31)
        ("R8 net48: no System.Net.Http, complete", {f"{P}/P.csproj": _proj(tf="net48")}, P,
         dict(certain=b7 - {http}, possible=set(), complete=True)),
        ("R8 net472 + the Web SDK: the web nine, no System.Net.Http",
         {f"{P}/P.csproj": _proj(sdk="Microsoft.NET.Sdk.Web", tf="net472")}, P,
         dict(certain=(b7 | set(_WEB9)) - {http}, complete=True)),
        ("R8 netstandard2.0, netcoreapp3.1, net6.0: certain",
         {f"{P}/A/A.csproj": _proj(tf="netstandard2.0"), f"{P}/B/B.csproj": _proj(tf="netcoreapp3.1"),
          f"{P}/C/C.csproj": _proj(tf="net6.0")}, [f"{P}/A", f"{P}/B", f"{P}/C"], dict(certain=b7, complete=True)),
        ("R8 no framework at all: only possible",
         {f"{P}/P.csproj": _proj(tf=None)}, P,
         dict(certain=b7 - {http}, possible_has={http}, complete=False, reason="framework_unread")),
        ("R8 a framework the pass cannot read: only possible",
         {f"{P}/P.csproj": _proj(tf="$(Tfm)")}, P,
         dict(certain=b7 - {http}, possible_has={http}, complete=False, reason="framework_unread")),
        ("R8 multi-target with a mix: only possible (compiled once per framework)",
         {f"{P}/P.csproj": _proj(_pg("<TargetFrameworks>net48;net8.0</TargetFrameworks>"), tf=None)}, P,
         dict(certain=b7 - {http}, possible_has={http}, complete=False, reason="framework_unread")),
        ("R8 multi-target, none of them .NET Framework: certain",
         {f"{P}/P.csproj": _proj(_pg("<TargetFrameworks>net8.0;net6.0</TargetFrameworks>"), tf=None)}, P,
         dict(certain=b7, complete=True)),
        ("R8 multi-target, all of them .NET Framework: dropped",
         {f"{P}/P.csproj": _proj(_pg("<TargetFrameworks>net48;net472</TargetFrameworks>"), tf=None)}, P,
         dict(certain=b7 - {http}, possible=set(), complete=True)),
        # R9: the import points MSBuild has and a project's text does not show
        ("R9 a central-package Directory.Packages.props costs nothing",
         {"src/Directory.Packages.props": cpm, f"{P}/P.csproj": _proj()}, P, dict(certain=b7, complete=True)),
        ("R9 its ImplicitUsings beats Directory.Build.props'",
         {"src/Directory.Build.props": _props(sc), "src/Directory.Packages.props": _props(
             _pg("<ImplicitUsings>disable</ImplicitUsings>")), f"{P}/P.csproj": _proj(iu=None)}, P,
         dict(certain=set(), complete=True)),
        ("R9 ... whatever ManagePackageVersionsCentrally says (NuGet.props does not read it)",
         {"src/Directory.Build.props": _props(_pg("<ImplicitUsings>enable</ImplicitUsings>",
                                                  "<ManagePackageVersionsCentrally>false</ManagePackageVersionsCentrally>")),
          "src/Directory.Packages.props": _props(_pg("<ImplicitUsings>disable</ImplicitUsings>")),
          f"{P}/P.csproj": _proj(iu=None)}, P, dict(certain=set(), complete=True)),
        ("R9 ImportDirectoryPackagesProps=false: the file is never imported",
         {"src/Directory.Build.props": _props(_pg("<ImportDirectoryPackagesProps>false</ImportDirectoryPackagesProps>")),
          "src/Directory.Packages.props": _props(_pg("<ImplicitUsings>disable</ImplicitUsings>")),
          f"{P}/P.csproj": _proj()}, P, dict(certain=b7, complete=True)),
        ("R9 DirectoryPackagesPropsPath moves the file: nothing certain",
         {"src/Directory.Build.props": _props(_pg("<DirectoryPackagesPropsPath>x.props</DirectoryPackagesPropsPath>")),
          "src/Directory.Packages.props": _props(_pg("<ImplicitUsings>disable</ImplicitUsings>")),
          f"{P}/P.csproj": _proj()}, P, dict(certain=set(), complete=False)),
        ("R9 a <Using Include> in it is read",
         {"src/Directory.Packages.props": _props(_items('<Using Include="System.Timers" />')),
          f"{P}/P.csproj": _proj(iu=None)}, P, dict(certain={"System.Timers"}, complete=True)),
        ("R9 a <Using Remove> in it precedes the SDK's items: removes nothing of theirs",
         {"src/Directory.Packages.props": _props(_items('<Using Remove="System.IO" />')), f"{P}/P.csproj": _proj()}, P,
         dict(certain=b7, complete=True)),
        ("R9 an <Import> it cannot resolve: incomplete",
         {"src/Directory.Packages.props": _props('  <Import Project="$(Elsewhere)/x.props" />\n'),
          f"{P}/P.csproj": _proj()}, P, dict(complete=False, reason="import_unread")),
        *[(f"R9 {name} set: nothing certain",
           {"src/Directory.Build.props": _props(_pg(f"<{name}>$(MSBuildThisFileDirectory)x.file</{name}>")),
            f"{P}/P.csproj": _proj()}, P, dict(certain=set(), complete=False))
          for name in ("CustomBeforeMicrosoftCommonProps", "CustomAfterMicrosoftCommonProps",
                       "CustomBeforeMicrosoftCommonTargets", "CustomAfterMicrosoftCommonTargets",
                       "CustomBeforeMicrosoftCSharpProps", "CustomAfterMicrosoftCSharpProps",
                       "CustomBeforeMicrosoftCSharpTargets", "CustomAfterMicrosoftCSharpTargets")],
        ("R9 <Import Sdk.targets> with no Sdk.props: the SDK's items never come: incomplete",
         {f"{P}/P.csproj": '<Project>\n' + _pg("<TargetFramework>net8.0</TargetFramework>",
                                               "<ImplicitUsings>enable</ImplicitUsings>")
          + '  <Import Project="Sdk.targets" Sdk="Microsoft.NET.Sdk" />\n</Project>\n'}, P,
         dict(certain=set(), possible_has=b7, complete=False, reason="import_unread")),
        ("R9 both halves imported: that SDK",
         {f"{P}/P.csproj": '<Project>\n  <Import Project="Sdk.props" Sdk="Microsoft.NET.Sdk" />\n'
          + _pg("<TargetFramework>net8.0</TargetFramework>", "<ImplicitUsings>enable</ImplicitUsings>")
          + '  <Import Project="Sdk.targets" Sdk="Microsoft.NET.Sdk" />\n</Project>\n'}, P,
         dict(certain=b7, complete=True)),
        # R16: a value over 64 KB is unreadable, grown by doubling lines or written out
        ("R16 a property doubled past 64 KB: unreadable (a 419-byte file, about 32 MB otherwise)",
         {f"{P}/P.csproj": _proj(grow + _items('<Using Include="$(Big)" />'), iu=None)}, P,
         dict(certain=set(), complete=False, reason="value_cap")),
        ("R16 a literal value over 64 KB: unreadable",
         {f"{P}/P.csproj": _proj(_pg("<ImplicitUsings>" + "a" * 70000 + "</ImplicitUsings>"), iu=None)}, P,
         dict(certain=set(), possible_has=b7, complete=False, reason="value_cap")),
    ]


def _msb_rows_final(P: str, b7: set) -> list:
    """The last round's readings (R33-R39), each what real MSBuild (SDK 8.0.202 / MSBuild 17.9, `dotnet msbuild
    -t:GenerateGlobalUsings` under `dotnet build`'s default Debug AND under Release / AnyCPU) evaluates: certain only
    what both give, and where the pass leaves an input unread (a path-form spec, a response file, a project-local
    global property, an SDK an imported file names, a Configuration / Platform condition, a name matched only in
    another letter case) never certain, the reading incomplete."""
    path_specs = ("./System.IO", ".\\System.IO", "System.IO.", "x/../System.IO", "$(MSBuildProjectDirectory)/System.IO",
                  "../p/System.IO", "System.IO/", ".System.IO", "System.IO\\")
    return [
        # R33 (D1): MSBuild matches a Remove / Exclude / Update spec as a path; written as one, it is unread
        ("R33 a <Using Remove> spec written as a path (./ .\\ a trailing or leading dot, .., $(P)/, ../p/, a trailing "
         "separator): its namespace never certain, the reading incomplete (real MSBuild removes System.IO and keeps "
         "the other six; the pass does not model paths, so any item the spec may match is only possible: every SDK "
         "using possible, none certain, is as sound)",
         {f"{P}/{n}/p.csproj": _proj(_items(f'<Using Remove="{s}" />')) for n, s in enumerate(path_specs)},
         [f"{P}/{n}" for n in range(len(path_specs))],
         dict(not_certain={"System.IO"}, possible_has={"System.IO"}, complete=False)),
        ("R33 an Exclude written as a path: the namespace it may exclude never certain",
         {f"{P}/P.csproj": _proj(_items('<Using Include="System.Text;System.Timers" Exclude="./System.Text" />'),
                                 iu=None)}, P,
         dict(not_certain={"System.Text"}, complete=False)),
        ("R33 an Update written as a path: the item it may update never certain",
         {f"{P}/P.csproj": _proj(_items('<Using Include="System.Timers" />',
                                        '<Using Update="./System.Timers" Alias="Tm" />'), iu=None)}, P,
         dict(not_certain={"System.Timers"}, aliases={}, complete=False)),
        ("R33 a plain <Using Remove> stays read",
         {f"{P}/P.csproj": _proj(_items('<Using Remove="System.IO" />'))}, P,
         dict(certain=b7 - {"System.IO"}, complete=True)),
        # R34 (D2): two boolean-like operands compare as booleans; ImplicitUsings under the SDK's own condition
        ("R34 ImplicitUsings yes / on / !false / !no / !off / YES / On: on (the SDK's `== 'true'` compares booleans)",
         {f"{P}/{n}/p.csproj": _proj(iu=v) for n, v in enumerate(("yes", "on", "!false", "!no", "!off", "YES", "On"))},
         [f"{P}/{n}" for n in range(7)], dict(certain=b7, complete=True)),
        ("R34 ImplicitUsings no / off / !true / !on / !yes: off",
         {f"{P}/{n}/p.csproj": _proj(iu=v) for n, v in enumerate(("no", "off", "!true", "!on", "!yes"))},
         [f"{P}/{n}" for n in range(5)], dict(certain=set(), complete=True)),
        ("R34 `'$(Trim)' == 'true'` with Trim=yes holds: its <Using Remove> applies",
         {f"{P}/P.csproj": _proj(_pg("<Trim>yes</Trim>") + "  <ItemGroup Condition=\"'$(Trim)' == 'true'\">\n"
                                 '    <Using Remove="System.IO" />\n  </ItemGroup>\n')}, P,
         dict(certain=b7 - {"System.IO"}, complete=True)),
        ("R34 `'$(X)' != 'false'` with X=no fails: its <Using Include> is not added",
         {f"{P}/P.csproj": _proj(_pg("<X>no</X>") + _items(
             '<Using Include="System.Timers" Condition="\'$(X)\' != \'false\'" />'), iu=None)}, P,
         dict(certain=set(), complete=True)),
        ("R34 `'$(Y)' == 'false'` with Y=!true holds",
         {f"{P}/P.csproj": _proj(_pg("<Y>!true</Y>") + _items(
             '<Using Include="System.Timers" Condition="\'$(Y)\' == \'false\'" />'), iu=None)}, P,
         dict(certain={"System.Timers"}, complete=True)),
        ("R34 a word that is not boolean-like compares as text: `enable` is not 'true'",
         {f"{P}/P.csproj": _proj(_pg("<Z>enable</Z>") + _items(
             '<Using Include="System.Timers" Condition="\'$(Z)\' == \'true\'" />'), iu=None)}, P,
         dict(certain=set(), complete=True)),
        # R35 (D3-D5): inputs a project's text does not show
        ("R35 a Directory.Build.rsp in the project's directory: incomplete (its -p: switches are global properties)",
         {f"{P}/Directory.Build.rsp": "-p:ImplicitUsings=disable\n", f"{P}/P.csproj": _proj()}, P,
         dict(not_certain=b7, complete=False)),
        ("R35 ... or above it",
         {"src/Directory.Build.rsp": "-p:ImplicitUsings=disable\n", f"{P}/P.csproj": _proj()}, P,
         dict(not_certain=b7, complete=False)),
        ("R35 TreatAsLocalProperty on <Project>: incomplete",
         {f"{P}/P.csproj": _proj(_pg("<Configuration>Debug</Configuration>")).replace(
             '<Project Sdk="Microsoft.NET.Sdk">', '<Project Sdk="Microsoft.NET.Sdk" TreatAsLocalProperty="Configuration">')},
         P, dict(complete=False)),
        ("R35 an Sdk attribute in an imported Directory.Build.props: incomplete (that SDK's usings come too)",
         {f"{P}/Directory.Build.props": '<Project Sdk="Microsoft.NET.Sdk.Web">\n</Project>\n', f"{P}/P.csproj": _proj()},
         P, dict(complete=False)),
        ("R35 an <Sdk> element in an imported file: incomplete",
         {f"{P}/w.props": '<Project>\n  <Sdk Name="Microsoft.NET.Sdk.Worker" />\n</Project>\n',
          f"{P}/P.csproj": _proj('  <Import Project="w.props" />\n')}, P, dict(complete=False)),
        # R36 (D6): `;` splits before %XX decodes
        ("R36 `System.IO%3BSystem.Linq` is ONE spec after the split: it removes nothing",
         {f"{P}/P.csproj": _proj(_items('<Using Remove="System.IO%3BSystem.Linq" />'))}, P,
         dict(certain=b7)),
        # R38: `dotnet build` builds Debug; the evaluation's Release is a model only
        ("R38 a <Using Remove> under a $(Configuration) condition: possible, the reading incomplete",
         {f"{P}/P.csproj": _proj(_items('<Using Remove="System.IO" Condition="\'$(Configuration)\' == \'Release\'" />'))},
         P, dict(certain=b7 - {"System.IO"}, not_certain={"System.IO"}, possible_has={"System.IO"}, complete=False)),
        ("R38 ImplicitUsings under a $(Configuration) condition: the SDK list only possible",
         {f"{P}/P.csproj": _proj(_pg('<ImplicitUsings Condition="\'$(Configuration)\' == \'Debug\'">enable'
                                     '</ImplicitUsings>'), iu=None)}, P,
         dict(certain=set(), possible_has=b7, complete=False)),
        ("R38 a <Using Include> under a $(Platform) condition: only possible",
         {f"{P}/P.csproj": _proj(_items('<Using Include="Plat.Ns" Condition="\'$(Platform)\' == \'AnyCPU\'" />'),
                                 iu=None)}, P,
         dict(certain=set(), possible_has={"Plat.Ns"}, complete=False)),
        # R39: letter case on a case-sensitive file system
        ("R39 a <Using Remove> matching only in another letter case: the namespace only possible",
         {f"{P}/P.csproj": _proj(_items('<Using Remove="system.io" />'))}, P,
         dict(certain=b7 - {"System.IO"}, not_certain={"System.IO"}, possible_has={"System.IO"}, complete=False)),
        ("R39 a Directory.Build.targets / Directory.Build.rsp found only in another letter case: incomplete",
         {f"{P}/A/directory.build.targets": _props(_pg("<ImplicitUsings>disable</ImplicitUsings>")),
          f"{P}/A/A.csproj": _proj(),
          f"{P}/C/directory.build.rsp": "-p:ImplicitUsings=disable\n", f"{P}/C/C.csproj": _proj()},
         [f"{P}/A", f"{P}/C"], dict(not_certain=b7, complete=False)),
        ("R39 a Directory.Packages.props found only in another letter case: its <Using> never certain, incomplete",
         {f"{P}/DIRECTORY.PACKAGES.PROPS": _props(_items('<Using Include="Pk.Ns" />')), f"{P}/P.csproj": _proj(iu=None)},
         P, dict(not_certain={"Pk.Ns"}, complete=False)),
        ("R39 the exact names stay read",
         {f"{P}/A/Directory.Build.targets": _props(_pg("<ImplicitUsings>disable</ImplicitUsings>")),
          f"{P}/A/A.csproj": _proj(),
          f"{P}/B/Directory.Packages.props": _props(_items('<Using Include="Pk.Ns" />')),
          f"{P}/B/B.csproj": _proj(iu=None)}, [f"{P}/A"], dict(certain=set(), complete=True)),
        ("R39 ... Directory.Packages.props",
         {f"{P}/B/Directory.Packages.props": _props(_items('<Using Include="Pk.Ns" />')),
          f"{P}/B/B.csproj": _proj(iu=None)}, f"{P}/B", dict(certain={"Pk.Ns"}, complete=True)),
        # a fix of this round (no decision; the c1 differential's t218): the GenerateGlobalUsings task writes
        # `global using global::<Include>;`, so `global::N` imports `global::global::N` (no namespace N, a compile
        # error): never N, alias and static included (real MSBuild, the reader's shapes gp_*)
        ("a `global::` prefix on a <Using Include>: N never certain (the task writes global::global::N)",
         {f"{P}/P.csproj": _proj(_items('<Using Include="global::System.Timers" />'))}, P,
         dict(certain=b7, not_certain={"System.Timers"}, complete=False, reason="using_unread")),
        ("... with an Alias: no alias",
         {f"{P}/P.csproj": _proj(_items('<Using Include="global::System.Timers" Alias="Tm" />'))}, P,
         dict(aliases={}, not_certain={"System.Timers"}, complete=False)),
        ("... with Static=\"true\": no static",
         {f"{P}/P.csproj": _proj(_items('<Using Include="global::System.Math" Static="true" />'))}, P,
         dict(statics=(), complete=False)),
        ("... and the plain spelling stays read",
         {f"{P}/P.csproj": _proj(_items('<Using Include="System.Timers" />'))}, P,
         dict(certain=b7 | {"System.Timers"}, complete=True)),
    ]


def sec_msbuild() -> None:
    """a9 / a10: csharp_msbuild.read_group on in-memory trees (its read= seam), and its SDK lists equal the kit
    reference's (benchmark/call_linking/truth_roslyn/Program.cs, read as data: a test may name the reference; build/
    may not)."""
    print("\n-- U msbuild: csharp_msbuild.read_group on in-memory trees; the SDK lists against the kit reference's --")
    mod = _try_import("csharp_msbuild")
    if mod is None or not callable(getattr(mod, "read_group", None)):
        check("U msbuild: build/csharp_msbuild.py imports and has read_group", False, _IMPORT_ERRORS[-1:])
        return
    fields = ("certain", "possible", "aliases", "alias_unknown", "statics", "complete", "reasons")
    for label, files, group, want in _msb_rows():
        groups = group if isinstance(group, list) else [group]
        bad = []
        for g in groups:
            try:
                r = mod.read_group("/nonexistent-dm-root", g, read=dict(files))
            except Exception as exc:  # noqa: BLE001 — a raise is a failure, never a crash of the gate
                bad.append((g, f"raised {exc!r}"))
                continue
            if not all(hasattr(r, f) for f in fields):
                bad.append((g, f"not an MsbUsings: {r!r}"))
                continue
            for f in ("certain", "possible"):
                if f in want and set(getattr(r, f)) != set(want[f]):
                    bad.append((g, f, sorted(getattr(r, f))))
            if "aliases" in want and dict(r.aliases) != want["aliases"]:
                bad.append((g, "aliases", r.aliases))
            if "statics" in want and tuple(r.statics) != tuple(want["statics"]):
                bad.append((g, "statics", r.statics))
            if "complete" in want and bool(r.complete) is not want["complete"]:
                bad.append((g, "complete", r.complete, r.reasons))
            if "possible_has" in want and not set(want["possible_has"]) <= set(r.possible):
                bad.append((g, "possible", sorted(r.possible)))
            if "alias_unknown_has" in want and not set(want["alias_unknown_has"]) <= set(r.alias_unknown):
                bad.append((g, "alias_unknown", sorted(r.alias_unknown)))
            for f in ("packages", "links"):                # the fields the soundness round adds
                if f in want and set(getattr(r, f, ("<no such field>",))) != set(want[f]):
                    bad.append((g, f, sorted(getattr(r, f, ("<no such field>",)))))
            if "not_certain" in want and set(want["not_certain"]) & set(r.certain):
                bad.append((g, "certain", sorted(r.certain)))
            if want.get("complete") is False:
                if "reason" in want and want["reason"] not in tuple(r.reasons):
                    bad.append((g, "reasons", r.reasons))
                if not tuple(r.reasons):
                    bad.append((g, "no reason given", r.reasons))
            if want.get("complete") is True and tuple(r.reasons):
                bad.append((g, "reasons on a complete reading", r.reasons))
        check(f"U msbuild: {label}", not bad, bad)
    # never raises: a reader / a listing that fails, an absurd group
    outs = []

    def boom(_rel):
        raise OSError("boom")
    for kw in (dict(read=boom, listdir=lambda _d: ["P.csproj"]), dict(read=lambda _r: b"<Project", listdir=boom),
               dict(read={"P.csproj": b"\xff\xfe<\x00"})):
        try:
            outs.append(mod.read_group("/nonexistent-dm-root", "", **kw))
        except Exception as exc:  # noqa: BLE001
            outs.append(exc)
    try:
        outs.append(mod.read_group(None, None))
    except Exception as exc:  # noqa: BLE001
        outs.append(exc)
    check("U msbuild: read_group never raises (a failing read, a failing listing, an undecodable file, no root): "
          "an incomplete reading, nothing certain",
          all(hasattr(o, "complete") for o in outs) and all(not o.complete and not o.certain for o in outs[:3]), outs)
    # a10: the four SDK lists equal the reference's UsingsBase / UsingsWeb / UsingsWorker / UsingsBlazorWasm
    prog = REPO / "benchmark" / "call_linking" / "truth_roslyn" / "Program.cs"
    ref = {}
    if prog.is_file():
        text = prog.read_text(encoding="utf-8")
        for nm in ("UsingsBase", "UsingsWeb", "UsingsWorker", "UsingsBlazorWasm"):
            mt = re.search(r"\bstring\[\]\s+" + nm + r"\s*=\s*\{(.*?)\};", text, re.S)
            ref[nm] = tuple(re.findall(r'"([^"]*)"', mt.group(1))) if mt else None
    check("U msbuild: the kit reference's four SDK lists parse (Program.cs, as data)",
          len(ref) == 4 and all(ref.values()), ref)
    if len(ref) != 4 or not all(ref.values()):
        return
    own = {nm: tuple(getattr(mod, attr, ()) or ()) for nm, attr in (
        ("UsingsBase", "USINGS_BASE"), ("UsingsWeb", "USINGS_WEB"), ("UsingsWorker", "USINGS_WORKER"),
        ("UsingsBlazorWasm", "USINGS_BLAZOR_WASM"))}
    check("U msbuild: csharp_msbuild's SDK lists equal the reference's, in order (SDK 8.0.202's props files)",
          own == ref, {k: (own[k], ref[k]) for k in ref if own[k] != ref[k]})
    got = {}
    for sdk, extra in (("Microsoft.NET.Sdk", ()), ("Microsoft.NET.Sdk.Web", ref["UsingsWeb"]),
                       ("Microsoft.NET.Sdk.Worker", ref["UsingsWorker"]),
                       ("Microsoft.NET.Sdk.BlazorWebAssembly", ref["UsingsBlazorWasm"]),
                       ("Microsoft.NET.Sdk.Razor", ())):
        r = mod.read_group("/nonexistent-dm-root", "P", read={"P/P.csproj": _proj(sdk=sdk)})
        got[sdk] = set(r.certain) == set(ref["UsingsBase"]) | set(extra) and r.complete
    check("U msbuild: read_group adds exactly the reference's lists per SDK (base + Web / Worker / BlazorWebAssembly; "
          "Razor none)", all(got.values()), got)


def sec_usings(buckets) -> None:
    print("\n-- U: the residuals round's using shapes (MSBuild implicit usings / <Using> items / Directory.Build "
          "files, usings at other levels, aliases by namespace declaration, an enclosing external namespace), on the "
          "typed keys AND the pushed edges (both engines, both modes) --")
    _sec_tree("U", "usings", buckets, lambda mode: dict(MODES[mode]), U_EXPECT, U_MUSTNOT, U_NO_EDGE, U_ABSENT,
              U_EDGE)
    sec_msbuild()


def sec_resid(buckets) -> None:
    print("\n-- X: the residuals round's mapping shapes (explicit implementations, non-public implementers, nullable "
          "closed type arguments, receiver type arguments in overload resolution), on the typed keys AND the pushed "
          "edges (both engines, both modes) --")
    _sec_tree("X", "resid", buckets, _x_env, X_EXPECT, X_MUSTNOT, X_NO_EDGE, X_ABSENT, X_EDGE)


# ================================================================================================
# Y: the soundness round (tests TREES["sound"]): every decision that reaches a tuple, each shape beside its positive
# sibling and its must-not with the engine's true tuple as teeth, on the typed keys AND the edges of the WHOLE push
# ================================================================================================

# (file, interface, base class, user, member, the file of the class that is NOT an implementer, that class): the
# interface call runs the base member (Roslyn's dispatch set: the base's), the class's own member of that name is an
# overload. The pass cannot prove the two signatures alike, so it adds the interface member, keys nothing else and
# removes nothing: the engine's true same-file tuple to the base member stays.
_Y_ADD = [
    ("src/Ya/Tick1.cs", "ITick1", "TickBase1", "TickUser1", "Tick", "src/Ya/Ticker1.cs", "Ticker1"),       # R1 sh1
    ("src/Ya/Tick1c.cs", "ITick1c", "TickBase1c", "TickUser1c", "Tick", "src/Ya/Ticker1c.cs", "Ticker1c"),  # R1 walk
    ("src/Yb/Web/TickT.cs", "ITickT", "TickBaseT", "TickUserT", "Tick", "src/Yb/Web/TickerT.cs", "TickerT"),  # R2 tr1
    ("src/Yb/Web/TickE.cs", "ITickE", "TickBaseE", "TickUserE", "Tick", "src/Yb/Web/TickerE.cs", "TickerE"),  # root
    ("src/Yb/Web/TickS.cs", "ITickS", "TickBaseS", "TickUserS", "Tick", "src/Yb/Web/TickerS.cs", "TickerS"),  # R1+R2
    ("src/Yc/A/Clk.cs", "IClk", "ClkBase", "ClkUser", "Tick", "src/Yc/B/ClkImpl.cs", "ClkImpl"),           # R2 no eff
    ("src/Yd/IRun.cs", "IRun", "RunBase", "RunUser", "Run", "src/Yd/Impl.cs", "Impl"),                     # R3 x2
    ("src/Yd/GenA.cs", "IBx", "BxBase", "BxUser", "Put", "src/Yd/GenB.cs", "BxImpl"),                      # R3 target
    ("src/Yd/QualA.cs", "IQq", "QqBase", "QqUser", "Put", "src/Yd/QualB.cs", "QqImpl"),                    # relative
    ("src/Yd/RelA.cs", "IQa", "QaBase", "QaUser", "Put", "src/Yd/RelB.cs", "QaImpl"),                      # R3 alias
    ("src/Ye1/Tk.cs", "ITickK", "TickBaseK", "TickUserK", "Tick", "src/Ye2/TickerK.cs", "TickerK"),        # R3 msbuild
    ("src/Yf1/TickW.cs", "ITickW", "TickBaseW", "TickUserW", "Tick", "src/Yf2/TickerW.cs", "TickerW"),     # R4 ws2
    ("src/Yg1/TickG.cs", "ITickG", "TickBaseG", "TickUserG", "Tick", "src/Yg2/TickerG.cs", "TickerG"),     # R5 rm2
    ("src/Yh/Shared/TickL.cs", "ITickL", "TickBaseL", "TickUserL", "Tick", "src/Yh/Lib/TickerL.cs", "TickerL"),  # R6
    ("src/Yh/SharedA/TickO.cs", "ITickO", "TickBaseO", "TickUserO", "Tick", "src/Yh/SharedB/TickerO.cs", "TickerO"),
    ("src/Yh/Sh3/TickP.cs", "ITickP", "TickBaseP", "TickUserP", "Tick", "src/Yh/Lib3/TickerP.cs", "TickerP"),
    ("src/Yj1/Send.cs", "ISend", "SendBase", "SendUser", "Send", "src/Yj2/Sender.cs", "Sender"),           # R8 net48
    ("src/Yj5/Send5.cs", "ISend5", "SendBase5", "SendUser5", "Send", "src/Yj2/Sender5.cs", "Sender5"),     # R8 mix
    ("src/Yk/A/TickQ.cs", "ITickQ", "TickBaseQ", "TickUserQ", "Tick", "src/Yk/B/TickerQ.cs", "TickerQ"),   # R9
    ("src/Yk3/A/TickR.cs", "ITickR", "TickBaseR", "TickUserR", "Tick", "src/Yk3/B/TickerR.cs", "TickerR"),
]
_Y_ADD += [
    ("src/Zf/ZfA/TickX.cs", "ITickX", "TickBaseX", "TickUserX", "Tick", "src/Zf/ZfB/TickerX.cs", "TickerX"),  # linked
    ("src/Zi/ZiA/TickI.cs", "ITickI", "TickBaseI", "TickUserI", "Tick", "src/Zi/ZiB/TickerI.cs", "TickerI"),  # a glob
    ("src/Zj/ZjA/TickJ.cs", "ITickJ", "TickBaseJ", "TickUserJ", "Tick", "src/Zj/ZjB/TickerJ.cs", "TickerJ"),  # alias
    ("src/Zs/Shared/TickZs.cs", "ITickZs", "TickBaseZs", "TickUserZs", "Tick", "src/Zs/ZsB/TickerZs.cs", "TickerZs"),
    ("src/Zv/ZvA/TickZv.cs", "ITickZv", "TickBaseZv", "TickUserZv", "Tick", "src/Zv/ZvB/TickerZv.cs", "TickerZv"),
]
# the positive siblings: the same shape PROVEN: the class implements the member (Roslyn's dispatch set), keyed beside
# the interface member. R42 as narrowed by L1 (W5, FLIPPED: recall only, the keys unchanged): UC's base `Order` does
# not resolve (R11) and one of its candidates is a type the pass cannot read (Yd/Blocks.cs declares an Order in each of
# two blocks: one node, the second part keyless), so UC may be ANY project type and every interface dispatch set of the
# tree is open: nothing is REPLACEd, the engine's tuple to the base member (which implements nothing here) stays, a gap
_Y_PROVEN = [
    ("src/Zg/ZgA/TickY.cs", "ITickY", "TickBaseY", "TickUserY", "Tick", "src/Zg/ZgB/TickerY.cs", "TickerY"),  # linked
    ("src/Yd/IRun2.cs", "IRun2", "RunBase2", "RunUser2", "Run", "src/Yd/Impl2.cs", "Impl2"),               # R3
    ("src/Yd/GenC.cs", "ILi", "LiBase", "LiUser", "Put", "src/Yd/GenD.cs", "LiImpl"),                      # R3 target
    ("src/Yf3/TickV.cs", "ITickV", "TickBaseV", "TickUserV", "Tick", "src/Yf4/TickerV.cs", "TickerV"),     # R4
    ("src/Yg1/TickH.cs", "ITickH", "TickBaseH", "TickUserH", "Tick", "src/Yg1/Sub/TickerH.cs", "TickerH"),  # R5
    ("src/Yh/Shared/TickM.cs", "ITickM", "TickBaseM", "TickUserM", "Tick", "src/Yh/Shared/Sub/TickerM.cs", "TickerM"),
    ("src/Yj3/Send3.cs", "ISend3", "SendBase3", "SendUser3", "Send", "src/Yj4/Sender3.cs", "Sender3"),     # R8
]
# positive siblings of W3 a W4 decision turns into gaps (FLIPPED: recall loss, nothing removed; Roslyn on this tree
# binds the class's member, the engine's same-file tuple to the base member stays): Tick1b, R27 (`System.Threading
# .Timer` written inside namespace System.Timers: an external System.Timers.System may exist); Tick1d, R27 (`Timer`
# written inside namespace System.Timers, an external name the binder leaves unknown there: no signature rule proves a
# parameter of it, F4); TickZ, R32 (Zeta: a root no project owns); HandleK, R45 (W5: Yk2's Directory.Packages.props
# holds a GlobalPackageReference, so both projects have a package and their usings no longer compare)
_Y_GAP = [
    ("src/Ya/Tick1b.cs", "ITick1b", "TickBase1b", "TickUser1b", "Tick", "src/Ya/Ticker1b.cs", "Ticker1b"),  # R27
    ("src/Ya/Tick1d.cs", "ITick1d", "TickBase1d", "TickUser1d", "Tick", "src/Ya/Ticker1d.cs", "Ticker1d"),  # R27
    ("src/Yb2/Web/TickZ.cs", "ITickZ", "TickBaseZ", "TickUserZ", "Tick", "src/Yb2/Web/TickerZ.cs", "TickerZ"),  # R32
    ("src/Yk2/A/Handle.cs", "IHandleK", "HandleKBase", "HandleKUser", "Handle", "src/Yk2/B/HandlerK.cs", "HandlerK"),
]
# R25 (FLIPPED, a decision of the core: recall loss, nothing removed): TickN.cs is a file its own project (Core2)
# removes and App2 links; every class of a file its group's <Compile Remove> takes out is read as open (compiled only
# where a link reaches it, or nowhere), so not even the interface member is keyed; the engine's tuple stays
_Y_NOKEY = [
    ("src/Yh/Core2/Linked/TickN.cs", "ITickN", "TickBaseN", "TickUserN", "Tick", "src/Yh/Lib2/TickerN.cs", "TickerN"),
]
Y_RP, Y_BL, Y_TY, Y_FR, Y_CL = "src/Yi/Repo.cs", "src/Ym/Blocks.cs", "src/Yn/Types.cs", "src/Yn/DerFar.cs", \
    "src/Yo/Closed.cs"
Y_XB, Y_SD, Y_RR, Y_RF, Y_IN = "src/Yd/Blocks.cs", "src/Yd/SaveDer.cs", "src/Yl/Rr.cs", "src/Yl/Rf.cs", "src/Yl/In.cs"
Y_RPP, Y_RPQ = "src/Yi/RepoP.cs", "src/Yi/RepoQ.cs"

# {caller: {target: grade}}: EXACTLY these callers of the tree are keyed, each target in Roslyn 4.8's binding or its
# run-time dispatch set on this exact tree (the expectation comes from there, never from the product); Y_FREE: callers
# whose keys are checked by Y_NEVER instead (Roslyn's binding or nothing).
Y_EXPECT = {
    **{_m(f, f"{u}.Go"): {_m(f, f"{i}.{m}"): I} for f, i, _b, u, m, _f2, _k in _Y_ADD + _Y_GAP},
    **{_m(f, f"{u}.Go"): {_m(f, f"{i}.{m}"): I, _m(f2, f"{k}.{m}"): I} for f, i, _b, u, m, f2, k in _Y_PROVEN},
    # R3 x1: two blocks of one file each declare an Order, ImplX.M's is unknown: Roslyn dispatches to BaseU.M
    _m(Y_XB, "UseUser.Go"): {_m(Y_XB, "IUse.M"): I},
    # R7: Both and SubOne reach IRepo by two paths with other type arguments: unknown, so the interface member alone
    # (ADD-only, gap: Roslyn adds Both.Save, OneE.Save, OneP.Save / Both.Save, SubOne.Save), nothing removed; never
    # OneE.Save for IRepo<Cart>
    _m(Y_RP, "EgUser.Go"): {_m(Y_RP, "IRepo.Save"): I},
    _m(Y_RP, "EgUser.GoCart"): {_m(Y_RP, "IRepo.Save"): I},
    # R7's positive sibling: an explicit specifier of the called closing implements it, each closing its own classes
    _m(Y_RPP, "UserP.Go"): {_m(Y_RPP, "IRepoP.Save"): I, _m(Y_RPP, "OneEP.Save"): A, _m(Y_RPP, "OnePP.Save"): A},
    _m(Y_RPP, "UserP.GoCart"): {_m(Y_RPP, "IRepoP.Save"): I, _m(Y_RPP, "CartP.Save"): I},
    _m("src/Yi/RepoS.cs", "UserS.Go"): {_m("src/Yi/RepoS.cs", "IRepoS.Run"): I, _m("src/Yi/RepoS.cs", "BothS.Run"): I},
    # R7: TwoE reaches IRepoQ twice: unknown (gap: Roslyn BaseQ.Save), never TwoE's `IRepoQ<Order>.Save`
    _m(Y_RPQ, "UserQ.GoCart"): {_m(Y_RPQ, "IRepoQ.Save"): I},
    # R10: `ref readonly` takes a value argument: the derived hit (beside its rival: never EXTRACTED); a plain `ref`
    # is proven inapplicable to one (the base bound, held); `in` applies
    _m(Y_RR, "RrUser.Go"): {_m(Y_RR, "CartStore.Save"): I},
    _m(Y_RF, "RfUser.Go"): {_m("src/Yl/Store.cs", "Store2.Save"): I},
    _m(Y_IN, "InUser.Go"): {_m(Y_IN, "CartStore3.Save"): I},
    # R11: the alias of A's own block; B's Repo is its namespace's class in another file (another block's alias is not
    # in effect); D's using beats the compilation unit's alias. CUser.Go has no key (gap: Roslyn Order.Save, the same
    # file's class of the alias's name): nothing typed, its true tuple kept
    _m(Y_BL, "AUser.Go"): {_m("src/Ym/Data/SqlRepo.cs", "SqlRepo.Save"): I},
    _m(Y_BL, "BUser.Go"): {_m("src/Ym/B/Repo.cs", "Repo.Save"): I},
    _m(Y_BL, "DUser.Go"): {_m("src/Ym/Models2/Order2.cs", "Order2.Save"): I},
    # R12: an explicit-only implementation, a private and a protected `new` member seen from outside: the base's, in
    # its own file exact; DerI.Intl from its own project, DerN's public one, and each hidden member from where C#
    # sees it (the class itself, a nested type, a subclass through `this`) stay the hit
    _m(Y_TY, "HdUser.GoE"): {_m(Y_TY, "BaseS.Save"): E},
    _m(Y_TY, "HdUser.GoP"): {_m(Y_TY, "BaseS.Keep"): E},
    _m(Y_TY, "HdUser.GoG"): {_m(Y_TY, "BaseS.Prot"): E},
    _m(Y_TY, "HdUser.GoI"): {_m(Y_TY, "DerI.Intl"): E},
    _m(Y_TY, "HdUser.GoN"): {_m(Y_TY, "DerN.Plain"): E},
    _m(Y_TY, "DerP.In"): {_m(Y_TY, "DerP.Keep"): E},
    _m(Y_TY, "Nest.Go"): {_m(Y_TY, "DerP.Keep"): E},
    _m(Y_TY, "SubG.In"): {_m(Y_TY, "DerG.Prot"): E},
    # R12: the skip's grade follows the base's definition, here in another file; `internal` from another project: the
    # base; through a friend assembly it is DerI's (InternalsVisibleTo: never proven inaccessible)
    _m(Y_FR, "FarUser.Go"): {_m("src/Yn/BaseFar.cs", "BaseFar.Far"): I},
    _m("src/Yn2/Remote.cs", "RemoteUser.Go"): {_m(Y_TY, "BaseS.Intl"): I},
    _m("src/Yn3/Friend.cs", "FriendUser.Go"): {_m(Y_TY, "DerI.Intl"): I},
    _m("src/Yn4/Friend.cs", "FriendUser4.Go"): {_m(Y_TY, "DerI.Intl"): I},
    _m("src/Yn5/Friend.cs", "FriendUser5.Go"): {_m(Y_TY, "DerI.Intl"): I},
    # R12: a friend wherever it is written stays unknown: the hit, Roslyn's binding on each; with none, the base's
    **{_m(f"src/{r}/{r}App/User.cs", f"FriendV{n}.Go"): {_m(f"src/{r}/{r}Lib/Der.cs", f"DerV{n}.Intl"): I}
       for r, n in (("Za", "A"), ("Zb", "B"), ("Zc", "C"), ("Zd", "D"), ("Zh", "H"))},
    _m("src/Ze/ZeApp/User.cs", "FriendVE.Go"): {_m("src/Ze/ZeLib/Der.cs", "BaseVE.Intl"): I},
    # a link to a missing (generated) file: unknown, the interface member alone (gap: Roslyn, which cannot read that
    # file either, adds TickerZm.Tick); the engine's tuple stays
    _m("src/Zm/ZmA/TickZm.cs", "TickUserZm.Go"): {_m("src/Zm/ZmA/TickZm.cs", "ITickZm.Tick"): I},
    # an alias the pass cannot see (vendor/): the declared member only (gap: Roslyn adds B15's overrides)
    _m("src/Zu/Slots.cs", "LinkUser5.Go"): {_m("src/Zu/Slots.cs", "A15.Run15"): I},
    _m("src/Zu/Slots.cs", "LinkUser5.Go2"): {_m("src/Zu/Slots.cs", "A15.Num15"): I},
    # R20: Nullable<int> / System.Nullable<int> close like int?, Int32 / System.Int32 like int (no fan-out)
    **{_m(Y_CL, f"Cv0User.{g}"): {_m(Y_CL, "Cv0.M0"): I, _m(Y_CL, "Cv0Nul.M0"): I} for g in ("GoA", "GoB", "GoC")},
    **{_m(Y_CL, f"RepoUsers.{g}"): {_m(Y_CL, "ICounter.Next"): I, _m(Y_CL, "IntCounter.Next"): I}
       for g in ("GoI", "GoJ", "GoK")},
}

# keyed or not, never these targets (no exact row: C# binds elsewhere and the pass may stay unknown)
Y_FREE = (_m(Y_TY, "SubG2.InOther"), _m(Y_FR, "OnlyUser.Go"))
Y_NEVER = [
    (_m(Y_TY, "SubG2.InOther"), _m(Y_TY, "DerG.Prot")),              # R12: protected through a DerG: Roslyn BaseS.Prot
    (_m(Y_FR, "OnlyUser.Go"), _m(Y_FR, "DerOnly.Save")),             # R12: an explicit-only member: the extension
]

Y_MUSTNOT = [(_m(f, f"{u}.Go"), m, "keep", True) for f, _i, _b, u, m, _f2, _k in _Y_ADD + _Y_GAP] + [
    (_m(f, f"{u}.Go"), m, "none", True) for f, _i, _b, u, m, _f2, _k in _Y_NOKEY] + [
    (_m(Y_XB, "UseUser.Go"), "M", "keep", True),               # R3 x1
    (_m(Y_SD, "SaveUser.Go"), "Save", "none", True),           # R3 cs_same_sig: Roslyn SaveBase.Save, no proof
    (_m(Y_RP, "EgUser.Go"), "Save", "keep", True),             # R7: SubOne unknown: nothing removed
    (_m(Y_RP, "EgUser.GoCart"), "Save", "keep", True),         # R7: the true SubOne.Save tuple stays
    (_m(Y_RPQ, "UserQ.GoCart"), "Save", "keep", True),         # R7: the engine's tuple stays
    (_m(Y_RF, "RfUser.Go"), "Save", "keep", True),             # R10: a rival binding is held
    (_m("src/Yl/Rc.cs", "RcUser.Go"), "Save", "none", True),   # R10: never proven inapplicable (Roslyn: a conversion
                                                               # to `ref readonly Cart`, CartStore4.Save, the true tuple)
    (_m(Y_BL, "CUser.Go"), "Save", "keep", True),              # R11: the true Order.Save tuple stays
    (_m(Y_FR, "OnlyUser.Go"), "Save", "keep", True),           # R12: the true extension tuple stays
    (_m("src/Zu/Slots.cs", "LinkUser5.Go"), "Run15", "keep", True),     # N2: an unseen alias, the true tuples kept
    (_m("src/Zu/Slots.cs", "LinkUser5.Go2"), "Num15", "keep", True),
]

Y_NO_EDGE = [
    (_m(Y_BL, "AUser.Go"), _m(Y_BL, "Order.Save")),            # R11: the proven alias target REPLACEs
    (_m(Y_BL, "BUser.Go"), _m(Y_BL, "Order.Save")),
    (_m(Y_BL, "DUser.Go"), _m(Y_BL, "Order.Save")),
    (_m(Y_TY, "HdUser.GoE"), _m(Y_TY, "DerE.Save")),           # R12: the skipped members
    (_m(Y_TY, "HdUser.GoP"), _m(Y_TY, "DerP.Keep")),
    (_m(Y_TY, "HdUser.GoG"), _m(Y_TY, "DerG.Prot")),
    (_m(Y_FR, "FarUser.Go"), _m(Y_FR, "DerFar.Far")),
]

Y_ABSENT = [(_m(f, f"{u}.Go"), _m(f2, f"{k}.{m}")) for f, _i, _b, u, m, f2, k in _Y_ADD + _Y_NOKEY] + [
    (_m(Y_BL, "BUser.Go"), _m("src/Ym/Data/SqlRepo.cs", "SqlRepo.Save")),    # R11: another block's alias
    (_m(Y_BL, "CUser.Go"), _m("src/Ym/Data/SqlRepo.cs", "SqlRepo.Save")),    # R11: a nearer using beats the alias
    (_m(Y_BL, "DUser.Go"), _m("src/Ym/Data/SqlRepo.cs", "SqlRepo.Save")),
    (_m(Y_RR, "RrUser.Go"), _m("src/Yl/Store.cs", "Store.Save")),            # R10
    (_m("src/Yn2/Remote.cs", "RemoteUser.Go"), _m(Y_TY, "DerI.Intl")),       # R12: `internal` of another project
    (_m("src/Ze/ZeApp/User.cs", "FriendVE.Go"), _m("src/Ze/ZeLib/Der.cs", "DerVE.Intl")),
    *[(_m(f"src/{r}/{r}App/User.cs", f"FriendV{n}.Go"), _m(f"src/{r}/{r}Lib/Der.cs", f"BaseV{n}.Intl"))
      for r, n in (("Za", "A"), ("Zb", "B"), ("Zc", "C"), ("Zd", "D"), ("Zh", "H"))],
    (_m(Y_CL, "Cv0User.GoA"), _m(Y_CL, "Cv0Int.M0")),                        # R20
    (_m(Y_CL, "Cv0User.GoB"), _m(Y_CL, "Cv0Int.M0")),
]

Y_EDGE = [(_m(f, f"{u}.Go"), _m(f, f"{b}.{m}"), None)
          for f, _i, b, u, m, _f2, _k in _Y_ADD + _Y_GAP + _Y_NOKEY + _Y_PROVEN] + [
    # R20: no fan-out (never the closed group's other members keyed); R42 / L1 (W5, FLIPPED from Y_NO_EDGE, as
    # _Y_PROVEN): the open dispatch set REPLACEs nothing, LongCounter.Next's tuple stays, a gap
    (_m(Y_CL, "RepoUsers.GoI"), _m(Y_CL, "LongCounter.Next"), None),
    (_m(Y_CL, "RepoUsers.GoJ"), _m(Y_CL, "LongCounter.Next"), None)] + [
    (_m(f, f"{u}.Go"), _m(f2, f"{k}.{m}"), I) for f, _i, _b, u, m, f2, k in _Y_PROVEN] + [
    (_m(Y_RP, "EgUser.GoCart"), _m(Y_RP, "SubOne.Save"), None),      # R7: the run-time target stays
    (_m("src/Zm/ZmA/TickZm.cs", "TickUserZm.Go"), _m("src/Zm/ZmA/TickZm.cs", "TickBaseZm.Tick"), None),  # unknown
    (_m("src/Zu/Slots.cs", "LinkUser5.Go"), _m("src/Zu/Slots.cs", "B15.Run15"), None),     # N2: the override that runs
    (_m("src/Zu/Slots.cs", "LinkUser5.Go2"), _m("src/Zu/Slots.cs", "B15.Num15"), None),
    (_m(Y_RR, "RrUser.Go"), _m(Y_RR, "CartStore.Save"), None),       # R10: Roslyn's binding stays
    (_m(Y_FR, "OnlyUser.Go"), _m(Y_FR, "OnlyExt.Save"), None),       # R12
    (_m(Y_BL, "CUser.Go"), _m(Y_BL, "Order.Save"), None),            # R11: the true tuple, no alias REPLACE
    (_m(Y_TY, "HdUser.GoE"), _m(Y_TY, "BaseS.Save"), E),             # R12: no false EXTRACTED
    (_m(Y_TY, "HdUser.GoP"), _m(Y_TY, "BaseS.Keep"), E),
    (_m(Y_FR, "FarUser.Go"), _m("src/Yn/BaseFar.cs", "BaseFar.Far"), I),
    (_m("src/Yn3/Friend.cs", "FriendUser.Go"), _m(Y_TY, "DerI.Intl"), I),
]


def _y_units() -> None:
    """The reader's and the binder's own facts behind section Y: R10's passing word, R1 / R2's capable / transparent /
    package roots, R6's cu_reading."""
    src = """namespace Yl;

public class K
{
    public void A(ref readonly Order o)
    {
    }

    public void B(ref Order o)
    {
    }

    public void C(in Order o)
    {
    }

    public void D(out Order o)
    {
        o = null;
    }

    public void E(scoped ref readonly Order o)
    {
    }
}
"""
    nil, one = frozenset(), frozenset({"Ext.Clocks"})
    co = getattr(tc(), "_cs_coarsens", None)
    check("Y core: R2 _cs_coarsens: a sequence of empty levels proves nothing; with a level to find the name in it does",
          co is not None and co((nil,), (nil,)) is False and co((nil,), (nil, nil)) is False
          and co((one,), (one, nil)) is True, co)
    pf = getattr(getattr(tc(), "_Linker", None), "cs_project_files", None)
    with tempfile.TemporaryDirectory(prefix="dm_tccs_y_") as td:
        write_tree(Path(td), {"src/P/P.csproj": _proj(iu=None), "src/Directory.Packages.props": _props(""),
                              "src/Directory.Build.targets": _props(""), "Directory.Build.props": _props("")})
        got = sorted(p.relative_to(td).as_posix() for p, _t in pf(type("L", (), {"root": td})(), "src/P")) if pf else None
    check("Y core: R12 cs_project_files reads the Directory.Packages.props above the project too (MSBuild imports it)",
          got == ["Directory.Build.props", "src/Directory.Build.targets", "src/Directory.Packages.props",
                  "src/P/P.csproj"], got)
    imp = getattr(tc(), "_cs_imports", None)
    check("Y core: R12 _cs_imports: an <Import>, and the file DirectoryPackagesPropsPath names, leave the text unproven",
          imp is not None and imp('<Import Project="x" />') and imp("<DirectoryPackagesPropsPath>x<")
          and not imp("<AssemblyName>x</AssemblyName>"), imp)
    f = facts_of(src)
    mp = (getattr(f, "_cs", None) or {}).get("mparams") or {}
    words = {k[0]: (v[0][0][1] if v and v[0] else None) for k, v in mp.items()}
    check("Y reader: R10 _cs mparams: `ref readonly` (`scoped` too) is its own word, `ref` / `in` / `out` unchanged",
          words == {"A": "ref readonly", "B": "ref", "C": "in", "D": "out", "E": "ref readonly"}, words)
    if rcs() is None:
        return
    rd = rcs().make_reader(tc().LANGUAGES["csharp"])
    tree = {"src/P/P.csproj": _proj(_items('<PackageReference Include="Acme.Core" Version="1.0.0" />',
                                           '<Reference Include="beta.lib, Version=1.0.0.0" />'), iu=None),
            "src/P/packages.config": '<packages>\n  <package id="Gamma.Pkg" version="1.0.0" />\n</packages>\n',
            "src/P/Acme.cs": "namespace Acme.Web;\npublic class W\n{\n}\n",
            "src/P/Acme2.cs": "namespace Acme.Core;\npublic class C\n{\n}\n",
            "src/P/Beta.cs": "namespace Beta.Web;\npublic class B\n{\n}\n",
            "src/P/Gamma.cs": "namespace Gamma.Web;\npublic class G\n{\n}\n",
            "src/P/Sys.cs": "namespace System.Timers;\npublic class T\n{\n}\n",
            # Q's RootNamespace owns the root Zeta (R32: an unowned root reads as a package's, capable and never
            # transparent; section L holds that sibling)
            "src/Q/Q.csproj": _proj(_pg("<RootNamespace>Zeta</RootNamespace>"), iu=None),
            "src/Q/Zeta.cs": "namespace Zeta.Core;\npublic class Z\n{\n}\n",
            "src/Q/Zeta2.cs": "namespace Zeta.Web;\npublic class Y\n{\n}\n",
            "src/L/L.csproj": _proj(_items('<Compile Include="../Q/Linked.cs" />', '<Compile Include="../R/*.cs" />')),
            "src/Q/Linked.cs": "namespace Zeta.Link;\npublic class L1\n{\n}\n",
            "src/R/Free.cs": "namespace Zeta.Free;\npublic class F1\n{\n}\n",
            "src/Other/Own.cs": "namespace Zeta.Own;\npublic class O1\n{\n}\n"}
    no_proj = {"src/A.cs": "namespace Na;\npublic class A\n{\n}\n"}
    linked = {"src/A/A.csproj": _proj(_items('<Compile Include="../C/One.cs" />', '<Compile Include="../D/**/*.cs" />',
                                             '<Compile Include="Own.cs" />')),
              "src/A/Own.cs": "namespace La;\npublic class O\n{\n}\n",
              "src/C/One.cs": "namespace La;\npublic class P\n{\n}\n",
              "src/C/Two.cs": "namespace La;\npublic class Q\n{\n}\n",
              "src/D/Sub/Deep.cs": "namespace La;\npublic class R\n{\n}\n",
              "src/D/obj/Gen.cs": "global using System.Timers;\n",
              "src/D/bin/Out.cs": "namespace La;\npublic class S\n{\n}\n",
              "src/D/Notes.txt": "not C#\n",
              "src/B/B.csproj": _proj(_items('<Compile Include="$(Elsewhere)/x.cs" />')),
              "src/B/B.cs": "namespace Lb;\npublic class B\n{\n}\n",
              "src/E/E.csproj": _proj(_items('<Compile Include="../C/Missing.cs" />')),
              "src/E/E.cs": "namespace Le;\npublic class E\n{\n}\n",
              "src/F/F.csproj": _proj(_items('<Compile Include="../../../../../Outside.cs" />')),
              "src/F/F.cs": "namespace Lf;\npublic class F\n{\n}\n"}

    def binder_on(td, files):
        write_tree(Path(td), files)
        facts = {r: rd.facts(r, Src(files)) for r in files if r.endswith(".cs")}
        return rd.binder(type("Ctx", (), {"root": td, "lang": "csharp", "facts": facts})())
    with tempfile.TemporaryDirectory(prefix="dm_tccs_y_") as td:
        b = binder_on(td, linked)
        li = getattr(b, "linked_in", None)
        got = {g: li(g) for g in ("src/A", "src/B", "src/E", "src/F")} if li else None
        gl = getattr(b, "_globals_of", None)
        Path(td, "src/C/Raw.cs").write_text("global using X.Y;\nglobal using static X.Z;\nglobal using Al = X.W;\n"
                                            "global using global::Q.R;\n", encoding="utf-8")
        raw = gl("src/C/Raw.cs") if gl else None
    check("Y binder: linked_in: a project's links outside its directory (a file, what a directory holds, bin / obj "
          "skipped), and False where a link names no place, no file or a file out of the tree (it may compile "
          "anything)",
          got == {"src/A": (("src/C/One.cs", "src/D/Sub/Deep.cs"), True), "src/B": ((), False),
                  "src/E": ((), False), "src/F": ((), False)}, got)
    check("Y binder: _globals_of reads the `global using`s of a file the reader did not parse from its text: "
          "namespaces, a static, alias names",
          raw == (frozenset({"X.Y", "Q.R"}), True, ["Al"]), raw)
    with tempfile.TemporaryDirectory(prefix="dm_tccs_y_") as td, tempfile.TemporaryDirectory(prefix="dm_tccs_y_") as te:
        bd, bn = binder_on(td, tree), binder_on(te, no_proj)
        cap = {n: bd.capable(n) for n in ("System.Timers", "Microsoft.Extensions", "Acme.Web", "Acme.Other", "Beta.Web",
                                          "Gamma.Web", "Zeta.Web", "")}
        trn = {n: bd.transparent(n) for n in ("Acme.Core", "Acme.Web", "Beta.Web", "Gamma.Web", "Zeta.Core", "System.Timers")}
        info = bd.ns_info()
        read = {r: bd.cu_reading(r) for r in ("src/P/Acme.cs", "src/Q/Zeta.cs", "src/Q/Linked.cs", "src/R/Free.cs",
                                              "src/Other/Own.cs")} if hasattr(bd, "cu_reading") else {}
        one = bn.cu_reading("src/A.cs") if hasattr(bn, "cu_reading") else None
    check("Y binder: R1 a namespace holding a project type stays capable under System / Microsoft, under a package id's "
          "first segment (any letter case) and never without one; the global namespace never",
          cap == {"System.Timers": True, "Microsoft.Extensions": True, "Acme.Web": True, "Acme.Other": True,
                  "Beta.Web": True, "Gamma.Web": True, "Zeta.Web": False, "": False}, cap)
    check("Y binder: R2 a using under a package id's first segment is never transparent (the root decides, not the exact "
          "id; a <Reference>, a packages.config, another group's project all count); a project-only root stays so",
          trn == {"Acme.Core": False, "Acme.Web": False, "Beta.Web": False, "Gamma.Web": False, "Zeta.Core": True,
                  "System.Timers": False}, trn)
    check("Y binder: R2 ns_info's third item: the lower-cased first segments of every id of every group",
          len(info) == 3 and set(info[2]) == {"acme", "beta", "gamma"}, info[2:])
    tk = rcs()._NS_SEP if hasattr(rcs(), "_NS_SEP") else None
    check("Y binder: R6 cu_reading: an ordinary file its group, whole; a file another project links, a file in no "
          "project's directory (the tree holds a .csproj): a token of that FILE, not whole",
          read == {"src/P/Acme.cs": ("src/P", True), "src/Q/Zeta.cs": ("src/Q", True),
                   "src/Q/Linked.cs": (f"{tk}src/Q/Linked.cs", False), "src/R/Free.cs": (f"{tk}src/R/Free.cs", False),
                   "src/Other/Own.cs": (f"{tk}src/Other/Own.cs", False)}, read)
    check("Y binder: R6 a tree with no .csproj keeps today's reading (group \"\", whole)", one == ("", True), one)


def sec_sound(buckets) -> None:
    print("\n-- Y: the soundness round's shapes (a wrong True removes a true engine tuple: unknown stays ADD-only), each "
          "decision that reaches a tuple beside its positive sibling, on the typed keys AND the pushed edges (both "
          "engines, both modes) --")
    _y_units()
    _sec_tree("Y", "sound", buckets, _x_env, Y_EXPECT, Y_MUSTNOT, Y_NO_EDGE, Y_ABSENT, Y_EDGE, free=Y_FREE)
    for b in buckets:
        for mode in MODES:
            run = _RUNS.get((b, "sound", mode, "tree"))
            if run is None:
                continue
            for caller, target in Y_NEVER:
                ck, tk = key_of(run.nodes, caller), key_of(run.nodes, target)
                n = node_of(run.nodes, caller)
                check(f"Y {b} [{mode}] {caller.split('::')[-1]}: no typed key to {target} (C# binds elsewhere)",
                      n is not None and tk is not None and tk not in (n.get("call_typed") or {}),
                      (ck, tk, n and n.get("call_typed")))


# ================================================================================================
# L: the last soundness round (tests TREES["final"]): R22-R38, every decision that reaches a tuple, each shape beside
# its positive sibling and its must-not with the engine's true tuple as teeth, on the typed keys AND the edges of the
# WHOLE push. Every expectation is Roslyn 4.8's binding with its run-time dispatch on this exact tree (the kit's
# reference); where the reference models MSBuild otherwise than real MSBuild (R34's boolean-like comparison, R35's
# Directory.Build.rsp, R38's `dotnet build` default of Debug), on the tree whose MSBuild usings are the ones SDK
# 8.0.202's `dotnet msbuild -t:GenerateGlobalUsings` writes, as plain `global using` lines
# ================================================================================================

# (file of the interface, its base class and the user; interface; base class; user; member; the file of the class
# that only overloads the member; that class): the interface call runs the base member (Roslyn's dispatch set), the
# class's own member of that name has another parameter type there. The pass cannot prove it: the interface member
# alone (ADD-only), nothing removed: the engine's true same-file tuple to the base member stays
_L_ADD = [
    ("src/Lb/Api/IRunS1.cs", "IRunS1", "RunBaseS1", "RunUserS1", "Run", "src/Lb/Jobs/ImplS1.cs", "ImplS1"),  # R23 st1
    ("src/Lb/Api/IRunS2.cs", "IRunS2", "RunBaseS2", "RunUserS2", "Run", "src/Lb/Jobs/ImplS2.cs", "ImplS2"),  # R23 st2
    ("src/Lc/TickP1.cs", "ITickP1", "TickBaseP1", "TickUserP1", "Tick", "src/Lc/TickerP1.cs", "TickerP1"),  # R24 pp1
    ("src/Ld1/TickP2.cs", "ITickP2", "TickBaseP2", "TickUserP2", "Tick", "src/Ld2/TickerP2.cs", "TickerP2"),  # #if global
    ("src/Le/TickR1.cs", "ITickR1", "TickBaseR1", "TickUserR1", "Tick", "src/Le/TickerR1.cs", "TickerR1"),  # R25 cr1
    ("src/Lf/TickR2.cs", "ITickR2", "TickBaseR2", "TickUserR2", "Tick", "src/Lf/TickerR2.cs", "TickerR2"),  # R25 items off
    ("src/Lg/TickR3.cs", "ITickR3", "TickBaseR3", "TickUserR3", "Tick", "src/Lg/TickerR3.cs", "TickerR3"),  # R25 $(P)
    ("src/Lj/Api/IRunK1.cs", "IRunK1", "RunBaseK1", "RunUserK1", "Run", "src/Lj/Jobs/ImplK1.cs", "ImplK1"),  # R27 cap1
    ("src/Lo/Web/TickT1.cs", "ITickT1", "TickBaseT1", "TickUserT1", "Tick", "src/Lo/Web/TickerT1.cs", "TickerT1"),  # R32
    ("src/Lp/Web/TickT2.cs", "ITickT2", "TickBaseT2", "TickUserT2", "Tick", "src/Lp/Web/TickerT2.cs", "TickerT2"),
    ("src/Lq/Web/TickT3.cs", "ITickT3", "TickBaseT3", "TickUserT3", "Tick", "src/Lq/Web/TickerT3.cs", "TickerT3"),
    ("src/Lu/A/TickU1.cs", "ITickU1", "TickBaseU1", "TickUserU1", "Tick", "src/Lu/B/TickerU1.cs", "TickerU1"),  # R33
    ("src/Lv/A/TickV1.cs", "ITickV1", "TickBaseV1", "TickUserV1", "Tick", "src/Lv/B/TickerV1.cs", "TickerV1"),  # R34
    ("src/Lw/A/TickW1.cs", "ITickW1", "TickBaseW1", "TickUserW1", "Tick", "src/Lw/B/TickerW1.cs", "TickerW1"),  # R35
    ("src/Lx/A/StoreX1.cs", "IStoreX1", "StoreBaseX1", "StoreUserX1", "Put", "src/Lx/B/StorerX1.cs", "StorerX1"),  # R36
    ("src/Ly/A/TickY1.cs", "ITickY1", "TickBaseY1", "TickUserY1", "Tick", "src/Ly/B/TickerY1.cs", "TickerY1"),  # R37
    ("src/Lz/A/TickZ1.cs", "ITickZ1", "TickBaseZ1", "TickUserZ1", "Tick", "src/Lz/B/TickerZ1.cs", "TickerZ1"),  # R38
    ("src/Ne/Api/IRunE1.cs", "IRunE1", "RunBaseE1", "RunUserE1", "Run", "src/Ne/Jobs/ImplE1.cs", "ImplE1"),  # shadow
]
# the positive siblings: the class implements the member (Roslyn's dispatch set), the REPLACE drops the engine's tuple
# to the base member, which implements nothing here
_L_PROVEN = [
    ("src/La/IRepo2b.cs", "IRepo2b", "RepoBase2b", "RepoUser2b", "Save", "src/La/Repo2b.cs", "Repo2b"),     # R22 arity
    ("src/La/IRepo4b.cs", "IRepo4b", "RepoBase4b", "RepoUser4b", "Save", "src/La/Repo4b.cs", "Repo4b"),     # R22 params
    ("src/Lb/Api/IRunS5.cs", "IRunS5", "RunBaseS5", "RunUserS5", "Run", "src/Lb/Jobs/ImplS5.cs", "ImplS5"),  # R23
    ("src/Lc/TickP3.cs", "ITickP3", "TickBaseP3", "TickUserP3", "Tick", "src/Lc/TickerP3.cs", "TickerP3"),  # R24
    ("src/Lh/TickR4.cs", "ITickR4", "TickBaseR4", "TickUserR4", "Tick", "src/Lh/TickerR4.cs", "TickerR4"),  # R25
    ("src/Lj/Api/IRunK2.cs", "IRunK2", "RunBaseK2", "RunUserK2", "Run", "src/Lj/Jobs/ImplK2.cs", "ImplK2"),  # R27
    ("src/Lr/Web/TickT4.cs", "ITickT4", "TickBaseT4", "TickUserT4", "Tick", "src/Lr/Web/TickerT4.cs", "TickerT4"),  # R32
    ("src/Ls/Web/TickT5.cs", "ITickT5", "TickBaseT5", "TickUserT5", "Tick", "src/Ls/Web/TickerT5.cs", "TickerT5"),
    ("src/Lt/Web/TickT6.cs", "ITickT6", "TickBaseT6", "TickUserT6", "Tick", "src/Lt/Web/TickerT6.cs", "TickerT6"),
    ("src/Lu/A/TickU2.cs", "ITickU2", "TickBaseU2", "TickUserU2", "Tick", "src/Lu/C/TickerU2.cs", "TickerU2"),  # R33
    ("src/Lv/A/TickV2.cs", "ITickV2", "TickBaseV2", "TickUserV2", "Tick", "src/Lv/C/TickerV2.cs", "TickerV2"),  # R34
    ("src/Ne/Api/IRunE2.cs", "IRunE2", "RunBaseE2", "RunUserE2", "Run", "src/Ne/Jobs/ImplE2.cs", "ImplE2"),  # shadow
]
# the class's own member is PROVEN another signature / parameter type: the public base member it inherits implements
# the interface member and is keyed beside it (Roslyn's dispatch set); the class's member never
_L_BASE = [
    ("src/La/IRepo2.cs", "IRepo2", "RepoBase2", "RepoUser2", "Save", "src/La/Repo2.cs", "Repo2"),          # R22 ar2
    ("src/La/IRepo4.cs", "IRepo4", "RepoBase4", "RepoUser4", "Save", "src/La/Repo4.cs", "Repo4"),          # R22 ar4
    ("src/Li/IRunG.cs", "IRunG", "RunBaseG", "RunUserG", "Run", "src/Li/IRunG.cs", "ImplG"),               # R26 sf1
    ("src/Lk/IRunM.cs", "IRunM", "RunBaseM", "RunUserM", "Run", "src/Lk/ImplM.cs", "ImplM"),               # R28 fl2
]
# the class implements the member (Roslyn's dispatch set) but the pass stays unknown by a decision (a gap: the
# interface member alone, nothing removed, the engine's same-file tuple to the base member kept): R23 keeps a block's
# `using static Util4` as a level of its own even where Util4 nests nothing, so ImplS4's lookup order is not
# IRunS4's (S5, the same level in both scopes, is proven); R45 compares two projects' MSBuild readings only when neither
# has a package, so LyC's and LyD's (one package in both: R37's proven sibling until W5) no longer compare
_L_GAP = [
    ("src/Lb/Api/IRunS4.cs", "IRunS4", "RunBaseS4", "RunUserS4", "Run", "src/Lb/Jobs/ImplS4.cs", "ImplS4"),  # R23
    ("src/Ly/C/TickY2.cs", "ITickY2", "TickBaseY2", "TickUserY2", "Tick", "src/Ly/D/TickerY2.cs", "TickerY2"),  # R45
]
L_D3, L_U3, L_LB, L_LI, L_LK, L_LL = "src/La/Der3.cs", "src/Lb/Jobs/User3.cs", "src/Lb/Lib/Holder.cs", "src/Li", \
    "src/Lk", "src/Ll"
L_UG, L_RG = "src/Lm/App/UserG.cs", "src/Lm/Common/ResultG.cs"
L_NA1, L_NA2, L_NB, L_NC, L_NX = "src/Na/Use1.cs", "src/Na/Use2.cs", "src/Nb/Repo.cs", "src/Nc/Holder.cs", "src/Nc/Box.cs"
L_ND, L_NDI = "src/Nd/Api.cs", "src/Nd/Impl/Waiter.cs"
L_NE1, L_NE2, L_NET = "src/Ne/Jobs/JobE1.cs", "src/Ne/Jobs/JobE2.cs", "src/Ne/Timer.cs"
L_NH = "src/Nh/All.cs"

# {caller: {target: grade}}: EXACTLY these callers of the tree are keyed, each target in Roslyn 4.8's binding or its
# run-time dispatch set on this exact tree (never from the product); L_FREE: callers checked by L_NEVER instead
L_EXPECT = {
    **{_m(f, f"{u}.Go"): {_m(f, f"{i}.{m}"): I} for f, i, _b, u, m, _f2, _k in _L_ADD + _L_GAP},
    **{_m(f, f"{u}.Go"): {_m(f, f"{i}.{m}"): I, _m(f2, f"{k}.{m}"): I} for f, i, _b, u, m, f2, k in _L_PROVEN},
    **{_m(f, f"{u}.Go"): {_m(f, f"{i}.{m}"): I, _m(f, f"{b}.{m}"): I} for f, i, b, u, m, _f2, _k in _L_BASE},
    # R22: Der3.Save<T> and Der6's Save(OrderA, params int[]) are other signatures: neither breaks Base3 / Base6.Save's
    # slot, whose override runs (1 override: INFERRED); a `new virtual` Save(OrderA) starts another slot: Base6b.Save
    # alone (Leaf6b.Save never runs for it)
    _m(L_D3, "BaseUser3.Go"): {_m("src/La/Base3.cs", "Base3.Save"): I, _m(L_D3, "Leaf3.Save"): I},
    _m("src/La/User6.cs", "BaseUser6.Go"): {_m("src/La/Base6.cs", "Base6.Save"): I, _m("src/La/User6.cs", "Leaf6.Save"): I},
    _m("src/La/User6b.cs", "BaseUser6b.Go"): {_m("src/La/Base6b.cs", "Base6b.Save"): I},
    # R23 st3: the block's `using static Holder3` supplies its nested Order3 before the compilation unit's using
    # (Roslyn: Holder3.Order3.Save): a nested type found through a `using static` has no Resolution form (never a
    # proof): unknown, User3.Go keyed nothing (L_MUSTNOT), never Lb.Models' Order3 (L_ABSENT)
    # R26 sf2: Li.B's Order (another file) beats the same file's Li.A one; the same declaration's class and an
    # enclosing declaration's class are bound (EXTRACTED)
    _m(f"{L_LI}/UserF.cs", "UserF.Go"): {_m(f"{L_LI}/B/OrderF.cs", "OrderF.Save"): I},
    _m(f"{L_LI}/UserH.cs", "UserH.Go"): {_m(f"{L_LI}/UserH.cs", "OrderH.Save"): E},
    _m(f"{L_LI}/UserJ.cs", "UserJ.Go"): {_m(f"{L_LI}/UserJ.cs", "OrderJ.Save"): E},
    # R28: a `file` class is seen in its own file only: UserL's OrderL is Lk.M's; LocalUserL's and ImplM's are theirs
    _m(f"{L_LK}/UserL.cs", "UserL.Go"): {_m(f"{L_LK}/M/OrderL.cs", "OrderL.Save"): I},
    _m(f"{L_LK}/Local.cs", "LocalUserL.Use"): {_m(f"{L_LK}/Local.cs", "OrderL"): E, _m(f"{L_LK}/Local.cs", "OrderL.Save"): E},
    _m(f"{L_LK}/ImplM.cs", "ImplMFactory.Make"): {_m(f"{L_LK}/ImplM.cs", "ImplM"): E},
    # R29: another declaration's using (of the same namespace, or of an enclosing one declared apart) is not in effect:
    # the compilation unit's Ll.Y; inside the block and a block nested in it, the block's Ll.X
    **{_m(f"{L_LL}/UserN{n}.cs", f"UserN{n}.Go"): {_m(f"{L_LL}/{d}/OrderN.cs", "OrderN.Save"): I}
       for n, d in ((3, "Y"), (4, "Y"), (5, "X"), (6, "X"))},
    # R30: `ResultG` is LmExt's (a package's: no key), `ResultG<int>` the project's; `ReplyG<int>` LmExt's, `ReplyG`
    # the project's
    _m(L_UG, "UserG.GoG"): {_m(L_RG, "ResultG.Save"): I},
    _m(L_UG, "UserG.GoQ"): {_m(L_RG, "ReplyG.Save"): I},
    # R31: App compiles the linked part: its `new` Save hides BaseP.Save; App2 does not compile Shared2's part
    _m("src/Ln/App/UserP.cs", "UserP.Go"): {_m("src/Ln/Shared/OrderP.Part.cs", "OrderP.Save"): I},
    _m("src/Ln/App2/UserQ.cs", "UserQ.Go"): {_m("src/Ln/App2/BaseQ.cs", "BaseQ.Save"): I},
    # an inner block's alias target sees the outer blocks' usings: User1's X is Lib.Foo.Bar (no key: the engine
    # links nothing here, L_MUSTNOT), User2's (no outer directive) the global Foo.Bar
    _m(L_NA2, "User2.Go"): {_m("src/Na/Foo/Bar.cs", "Bar.Run"): I},
    # R30 in the nested-type lookup: `Entry` is the outer Ng.Entry, `Entry<int>` the nested Repo.Entry<T>; `Cell<int>`
    # the outer Ng2.Cell<T>, `Cell` the nested Holder.Cell
    _m(L_NB, "Repo.Go"): {_m("src/Nb/Entry.cs", "Entry.Run"): I},
    _m(L_NB, "Repo.GoG"): {_m(L_NB, "Entry.Run"): E},
    _m(L_NC, "Holder.Go"): {_m("src/Nc/Cell.cs", "Cell.Run"): I},
    _m(L_NC, "Holder.GoD"): {_m(L_NC, "Cell.Run"): E},
    # D4d: the enclosing namespace System.Threading proves `CancellationToken`: DxWaiter.Wait implements IDxWait.Wait
    _m(L_ND, "DxWaitUser.Go"): {_m(L_ND, "IDxWait.Wait"): I, _m(L_NDI, "DxWaiter.Wait"): I},
    # the shadow rule: no block using, `Timer` is the parent namespace's Ne.Timer (JobE1, with one, keys nothing)
    _m(L_NE2, "JobE2.Go"): {_m(L_NET, "Timer.Start"): I},
    # the shadow rule against the same-file rule: JobH2's block has no using, the same file's Nh.Timer (JobH1: none)
    _m(L_NH, "JobH2.Go"): {_m(L_NH, "Timer.Start"): E},
}
# keyed or not, never these targets (no exact row: C# binds elsewhere and the pass may stay unknown)
L_FREE = (_m(L_D3, "DerUser3.Go"),)
L_NEVER = [
    (_m(L_D3, "DerUser3.Go"), _m(L_D3, "Der3.Save")),       # R22 ar3: T of Save<T> cannot be inferred: Base3.Save
]

L_MUSTNOT = [(_m(f, f"{u}.Go"), m, "keep", True) for f, _i, _b, u, m, _f2, _k in _L_ADD + _L_GAP] + [
    (_m(L_U3, "User3.Go"), "Save", "none", False),          # R23 st3: no key (the engine links no Save here)
    (_m(L_D3, "DerUser3.Go"), "Save", "keep", True),        # R22 ar3: the true Leaf3.Save tuple stays
    (_m(L_UG, "UserG.Go"), "Save", "none", False),          # R30: a package's ResultG
    (_m(L_UG, "UserG.GoP"), "Save", "none", False),         # R30: a package's ReplyG<T>
    (_m(L_NA1, "User1.Go"), "Run", "none", False),          # the inner alias X is Lib.Foo.Bar: never the global one
    (_m(L_NX, "User.GoB"), "Run", "none", True),            # R30: Box.Item<int> is no Box.Item (the engine merged the
                                                            # two: its only Run node is Item's; that tuple stays)
    # R31: a part TWO projects compile (Shared3's OrderS part, in App3's and App4's OrderS) is open: no key (Roslyn:
    # the part's `new` Save; merged into one type, or left out of App4's OrderS, it would bind BaseS3 / BaseS4.Save)
    (_m("src/Ln/App4/UserS.cs", "UserS.Go"), "Save", "none", False),
    (_m(L_NE1, "JobE1.Go"), "Start", "none", False),       # shadow: System.Timers.Timer (the engine links no Start)
    (_m(L_NH, "JobH1.Go"), "Start", "none", True),          # shadow over the same file's class: no key, tuple kept
]

L_NO_EDGE = [(_m(f, f"{u}.Go"), _m(f, f"{b}.{m}")) for f, _i, b, u, m, _f2, _k in _L_PROVEN] + [
    (_m("src/Li/IRunG.cs", "RunUserG.Go"), _m("src/Li/IRunG.cs", "ImplG.Run")),     # R26 sf1: the same file's ImplG
    (_m("src/La/User6b.cs", "BaseUser6b.Go"), _m("src/La/User6b.cs", "Leaf6b.Save")),  # R22: another slot's override
    (_m(f"{L_LI}/UserF.cs", "UserF.Go"), _m(f"{L_LI}/UserF.cs", "OrderF.Save")),    # R26 sf2: Li.A's Order
    (_m(L_NB, "Repo.Go"), _m(L_NB, "Entry.Run")),                                   # R30: never the nested Entry<T>
    (_m(L_NC, "Holder.Go"), _m(L_NC, "Cell.Run")),                                  # R30: never the nested Cell
    (_m(L_ND, "DxWaitUser.Go"), _m(L_ND, "DxWaitBase.Wait")),                       # D4d: no implementer here
]
# the engine's receiver-blind AMBIGUOUS tuples a typed EXTRACTED key replaces: the engine has them in the default mode
# only (DEEPMAP_PRECISE_CALLS keeps its exact same-file target instead)
L_NO_EDGE_DEFAULT = [
    (_m(f"{L_LI}/UserH.cs", "UserH.Go"), _m(f"{L_LI}/D/OrderH.cs", "OrderH.Save")),  # R26: the same declaration's
    (_m(f"{L_LI}/UserJ.cs", "UserJ.Go"), _m(f"{L_LI}/D/OrderH.cs", "OrderJ.Save")),  # class / an enclosing one's
    (_m(f"{L_LK}/Local.cs", "LocalUserL.Use"), _m(f"{L_LK}/M/OrderL.cs", "OrderL.Save")),  # R28: the file's own
    (_m(L_NB, "Repo.GoG"), _m("src/Nb/Entry.cs", "Entry.Run")),                     # R30: the nested Entry<T>'s
    (_m(L_NC, "Holder.GoD"), _m("src/Nc/Cell.cs", "Cell.Run")),                     # R30: the nested Cell's
]

L_ABSENT = [(_m(f, f"{u}.Go"), _m(f2, f"{k}.{m}")) for f, _i, _b, u, m, f2, k in _L_ADD] + [
    (_m(f, f"{u}.Go"), _m(f2, f"{k}.{m}")) for f, _i, _b, u, m, f2, k in _L_BASE if f2 != f] + [
    (_m(L_D3, "DerUser3.Go"), _m(L_D3, "Der3.Save")),                               # R22 ar3
    (_m(L_U3, "User3.Go"), _m("src/Lb/Models/Order3.cs", "Order3.Save")),           # R23 st3
    (_m(f"{L_LK}/UserL.cs", "UserL.Go"), _m(f"{L_LK}/Local.cs", "OrderL.Save")),    # R28 fl1
    *[(_m(f"{L_LL}/UserN{n}.cs", f"UserN{n}.Go"), _m(f"{L_LL}/{d}/OrderN.cs", "OrderN.Save"))   # R29
      for n, d in ((3, "X"), (4, "X"), (5, "Y"), (6, "Y"))],
    (_m(L_UG, "UserG.Go"), _m(L_RG, "ResultG.Save")),                               # R30
    (_m(L_UG, "UserG.GoP"), _m(L_RG, "ReplyG.Save")),
    (_m("src/Ln/App/UserP.cs", "UserP.Go"), _m("src/Ln/App/BaseP.cs", "BaseP.Save")),          # R31
    (_m("src/Ln/App2/UserQ.cs", "UserQ.Go"), _m("src/Ln/Shared2/OrderQ.Part.cs", "OrderQ.Save")),
    (_m(L_NA1, "User1.Go"), _m("src/Na/Foo/Bar.cs", "Bar.Run")),                    # the inner alias: Lib.Foo.Bar
    (_m("src/Ln/App4/UserS.cs", "UserS.Go"), _m("src/Ln/App4/BaseS4.cs", "BaseS4.Save")),  # R31: the part hides it
    (_m(L_NE1, "JobE1.Go"), _m(L_NET, "Timer.Start")),                              # shadow: never Ne.Timer's
]

L_EDGE = [(_m(f, f"{u}.Go"), _m(f, f"{b}.{m}"), None) for f, _i, b, u, m, _f2, _k in _L_ADD + _L_GAP] + [
    (_m(f, f"{u}.Go"), _m(f2, f"{k}.{m}"), I) for f, _i, _b, u, m, f2, k in _L_PROVEN] + [
    (_m(f, f"{u}.Go"), _m(f, f"{b}.{m}"), I) for f, _i, b, u, m, _f2, _k in _L_BASE] + [
    (_m(L_D3, "DerUser3.Go"), _m(L_D3, "Leaf3.Save"), None),                        # R22: the override that runs
    (_m(L_D3, "BaseUser3.Go"), _m(L_D3, "Leaf3.Save"), I),
    (_m("src/La/User6.cs", "BaseUser6.Go"), _m("src/La/User6.cs", "Leaf6.Save"), I),
    (_m(f"{L_LI}/UserF.cs", "UserF.Go"), _m(f"{L_LI}/B/OrderF.cs", "OrderF.Save"), I),  # R26 sf2: the true tuple
    (_m(f"{L_LI}/UserH.cs", "UserH.Go"), _m(f"{L_LI}/UserH.cs", "OrderH.Save"), E),
    (_m(f"{L_LK}/UserL.cs", "UserL.Go"), _m(f"{L_LK}/M/OrderL.cs", "OrderL.Save"), I),  # R28 fl1
    (_m(f"{L_LL}/UserN3.cs", "UserN3.Go"), _m(f"{L_LL}/Y/OrderN.cs", "OrderN.Save"), I),  # R29
    (_m("src/Ln/App/UserP.cs", "UserP.Go"), _m("src/Ln/Shared/OrderP.Part.cs", "OrderP.Save"), I),  # R31
    (_m(L_NA2, "User2.Go"), _m("src/Na/Foo/Bar.cs", "Bar.Run"), I),                 # the global Foo.Bar
    (_m(L_NB, "Repo.Go"), _m("src/Nb/Entry.cs", "Entry.Run"), I),                    # R30
    (_m(L_NB, "Repo.GoG"), _m(L_NB, "Entry.Run"), E),
    (_m(L_NC, "Holder.Go"), _m("src/Nc/Cell.cs", "Cell.Run"), I),
    (_m(L_NC, "Holder.GoD"), _m(L_NC, "Cell.Run"), E),
    (_m(L_ND, "DxWaitUser.Go"), _m(L_NDI, "DxWaiter.Wait"), I),                     # D4d
    (_m(L_NE2, "JobE2.Go"), _m(L_NET, "Timer.Start"), I),                           # shadow: no block using
    (_m(L_NH, "JobH1.Go"), _m(L_NH, "Timer.Start"), None),                          # shadow: the engine's own tuple
    (_m(L_NH, "JobH2.Go"), _m(L_NH, "Timer.Start"), E),                             # the same file's class
]


def sec_final(buckets) -> None:
    print("\n-- L: the last soundness round's shapes (R22-R38: a wrong True removes a true engine tuple: unknown stays "
          "ADD-only), each decision that reaches a tuple beside its positive sibling, on the typed keys AND the pushed "
          "edges (both engines, both modes) --")
    _l_units()
    _sec_tree("L", "final", buckets, _x_env, L_EXPECT, L_MUSTNOT, L_NO_EDGE, L_ABSENT, L_EDGE, free=L_FREE)
    for b in buckets:
        for mode in MODES:
            run = _RUNS.get((b, "final", mode, "tree"))
            if run is None:
                continue
            for caller, target in L_NEVER:
                ck, tk = key_of(run.nodes, caller), key_of(run.nodes, target)
                n = node_of(run.nodes, caller)
                check(f"L {b} [{mode}] {caller.split('::')[-1]}: no typed key to {target} (C# binds elsewhere)",
                      n is not None and tk is not None and tk not in (n.get("call_typed") or {}),
                      (ck, tk, n and n.get("call_typed")))
            if mode != "default":
                continue
            edges = push_edges(run, root_for("final"), _x_env(mode))
            r0 = run_pipeline(captured(b, "final")["graph"], root_for("final"), dict(_x_env(mode), DEEPMAP_TYPED_CALLS="0"))
            off = push_edges(r0, root_for("final"), dict(_x_env(mode), DEEPMAP_TYPED_CALLS="0"))
            for caller, target in L_NO_EDGE_DEFAULT:
                ck, tk = key_of(run.nodes, caller), key_of(run.nodes, target)
                check(f"L {b} [{mode}] {caller.split('::')[-1]}: no pushed edge to {target} (typed linking off: "
                      f"{off.get((ck, tk))})", ck is not None and tk is not None and (ck, tk) not in edges
                      and (ck, tk) in off, (ck, tk, edges.get((ck, tk)), off.get((ck, tk))))


def _l_units() -> None:
    """The reader's, the binder's and csharp_msbuild's own facts behind section L, each the C# / MSBuild rule's answer:
    R22's signature tables, R24's branch marks, R28's file-local classes, R29's namespace declarations; the binder's
    arity-keyed lookup (R30), `using static` levels (R23), the capable stop (R27), file-local and per-declaration
    lookup (R28, R29), in_group (R25), partial parts per compiling project (R31), owned and shared roots (R32); the
    MsbUsings fields names / compile / pkgsig and compile_state; and the core's comparisons of the new inputs."""
    src = """namespace La;

public class K
{
    public void A(Order o)
    {
    }

    public void B<T>(Order o)
    {
    }

    public void C<T, U>(Order o, params int[] xs)
    {
    }

    public void D(params string[] names)
    {
    }
}
"""
    f = facts_of(src)
    cs = getattr(f, "_cs", None) or {}
    ar = {k: v for k, v in (cs.get("marity") or {}).items()}
    want_l = {n: _line_of(src, f"void {n}") for n in "ABCD"}
    check("L reader: R22 marity: a method's own type-parameter count, per (method, declaration line)",
          ar == {("A", want_l["A"]): 0, ("B", want_l["B"]): 1, ("C", want_l["C"]): 2, ("D", want_l["D"]): 0}, ar)
    sp = {k[0]: v for k, v in (cs.get("msigp") or {}).items()}
    check("L reader: R22 msigp: the `params` parameter's written type (int[], string[]), () without one",
          sp == {"A": (), "B": (), "C": (("[]", ("b", "int"), "[]"),), "D": (("[]", ("b", "string"), "[]"),)}, sp)
    mp = cs.get("mparamsp") or {}
    check("L reader: R22 mparamsp: the `params` parameter as one more parameter, passing '' (no mode), not optional",
          {k[0]: len(v) for k, v in mp.items()} == {"A": 0, "B": 0, "C": 1, "D": 1}
          and all(e[1] == "" and e[2] is False for v in mp.values() for e in v), mp)
    k = f.classes[0] if f.classes else None
    arity = {n: k.methods[n][0].arity for n in "ACD"} if k is not None and all(n in k.methods for n in "ACD") else None
    check("L reader: R22 a method's call arity counts the `params` parameter: (required, unbounded)",
          arity == {"A": (1, 1), "C": (1, -1), "D": (0, -1)}, arity)
    src = """#if DEBUG
global using System.Linq;
#endif
global using System.IO;
using System.Text;
#if NETFRAMEWORK
using System.Threading;
using static System.Math;
using Tm = System.Threading.Timer;
#elif NET8_0
using System.Buffers;
#else
using System.Timers;
#endif

namespace Lp
{
#if X
    using Inner.Ns;
#endif
    using Plain.Ns;

    public class K
    {
    }

    file class F
    {
    }

    public class J
    {
        public class Nested
        {
        }
    }
}

namespace Lp
{
    using Other.Ns;

    public class Q
    {
    }

    namespace Sub
    {
        public class R
        {
        }
    }
}
"""
    f = facts_of(src)
    cs = getattr(f, "_cs", None) or {}
    us = cs.get("usings") or []
    pp = {us[i][0] for i in (cs.get("pp_usings") or ()) if i < len(us)}
    check("L reader: R24 pp_usings: every using inside an #if / #elif / #else branch (a global one too), no other",
          pp == {"System.Linq", "System.Threading", "System.Buffers", "System.Timers", "Inner.Ns"}, (pp, us))
    st, al = cs.get("statics") or [], cs.get("aliases") or []
    check("L reader: R24 pp_statics / pp_aliases mark the branch's `using static` and alias",
          {st[i][0] for i in (cs.get("pp_statics") or ()) if i < len(st)} == {"System.Math"}
          and {al[i][0] for i in (cs.get("pp_aliases") or ()) if i < len(al)} == {"Tm"}, (st, al, cs.get("pp_statics"),
                                                                                       cs.get("pp_aliases")))
    names = [c.name for c in f.classes]
    cvpp = {names[i]: v for i, v in (cs.get("cvpp") or {}).items() if i < len(names)}
    check("L reader: R24 cvpp: per class, which declarations of its chain (the compilation unit first) hold a branch "
          "directive", cvpp.get("K") == (True, True) and cvpp.get("Q") == (True, False), cvpp)
    fl = sorted(names[i] for i in (cs.get("filelocal") or ()) if i < len(names))
    check("L reader: R28 filelocal: the classes declared `file`", fl == ["F"], fl)
    fr = cs.get("frames") or []
    cf = {names[i]: v for i, v in (cs.get("cframe") or {}).items() if i < len(names)}
    shape = [(x[0], x[1], x[2]) for x in fr]
    check("L reader: R29 frames: one per namespace DECLARATION (the compilation unit first), each with its own usings; "
          "cframe: a class's directly enclosing declaration (a nested class: its outer class's)",
          shape == [("", -1, ("System.Text",)), ("Lp", 0, ("Plain.Ns",)), ("Lp", 0, ("Other.Ns",)), ("Lp.Sub", 2, ())]
          and cf == {"K": 1, "F": 1, "J": 1, "Nested": 1, "Q": 2, "R": 3}, (fr, cf))
    if rcs() is None:
        return
    rd = rcs().make_reader(tc().LANGUAGES["csharp"])

    def binder_on(td, files):
        write_tree(Path(td), files)
        facts = {r: rd.facts(r, Src(files)) for r in files if r.endswith(".cs")}
        return rd.binder(type("Ctx", (), {"root": td, "lang": "csharp", "facts": facts})())
    unk = tc().RES_UNKNOWN
    tree = {"src/Ga/Ga.csproj": _proj(iu=None),
            "src/Ga/Res.cs": "namespace Ga.Common;\n\npublic class Result<T>\n{\n}\n\npublic class Plain\n{\n}\n",
            "src/Ga/Use.cs": "using Ga.Common;\n\nnamespace Ga.App;\n\npublic class U\n{\n}\n",
            "src/Ga/Local.cs": "namespace Ga.App;\n\nfile class Local\n{\n}\n",
            "src/Ga/Hold.cs": "namespace Ga.Lib;\n\npublic static class Holder\n{\n    public class Timer\n    {\n    }\n}\n\n"
                              "public static class Util\n{\n}\n",
            "src/Ga/St.cs": "using System.Threading;\n\nnamespace Ga.Jobs\n{\n    using static Ga.Lib.Holder;\n\n"
                            "    public class S1\n    {\n    }\n}\n",
            "src/Ga/St2.cs": "using System.Threading;\n\nnamespace Ga.Jobs2\n{\n    using static Ga.Lib.Util;\n\n"
                             "    public class S2\n    {\n    }\n}\n",
            "src/Ga/Cap.cs": "using Ga.Common;\n\nnamespace System.Threading.Jobs\n{\n    public class C1\n    {\n    }\n}\n",
            "src/Ga/X.cs": "namespace Ga.X\n{\n    public class Ord\n    {\n    }\n}\n",
            "src/Ga/Y.cs": "namespace Ga.Y\n{\n    public class Ord\n    {\n    }\n}\n",
            "src/Ga/Nb.cs": "using Ga.Y;\n\nnamespace Ga.A\n{\n    using Ga.X;\n\n    public class In1\n    {\n    }\n}\n\n"
                            "namespace Ga.A\n{\n    public class In2\n    {\n    }\n}\n",
            # R25 / R31: a Compile Remove; a partial part linked from outside, another one two projects link
            "src/Gb/Gb.csproj": _proj(_items('<Compile Remove="Junk/**" />', '<Compile Include="../Sh/P.Part.cs" />',
                                             '<Compile Include="../Sh2/Q.Part.cs" />'), iu=None),
            "src/Gb/Junk/J.cs": "global using System.Threading;\n",
            "src/Gb/Kept.cs": "namespace Gb;\n\npublic partial class P\n{\n}\n\npublic partial class Q\n{\n}\n",
            "src/Sh/P.Part.cs": "namespace Gb;\n\npublic partial class P\n{\n    public void Save()\n    {\n    }\n}\n",
            "src/Gc/Gc.csproj": _proj(_items('<Compile Include="../Sh2/Q.Part.cs" />',
                                             '<Compile Remove="$(MSBuildProjectDirectory)/Maybe/**" />'), iu=None),
            "src/Gc/Maybe/M.cs": "namespace Gc;\n\npublic class M\n{\n}\n",
            "src/Gc/Q.cs": "namespace Gb;\n\npublic partial class Q\n{\n}\n",
            "src/Sh2/Q.Part.cs": "namespace Gb;\n\npublic partial class Q\n{\n    public void Run()\n    {\n    }\n}\n",
            "src/Gd/Gd.csproj": _proj(_pg("<EnableDefaultCompileItems>false</EnableDefaultCompileItems>")
                                      + _items('<Compile Include="In.cs" />'), iu=None),
            "src/Gd/In.cs": "namespace Gd;\n\npublic class In\n{\n}\n",
            "src/Gd/Out.cs": "namespace Gd;\n\npublic class Out\n{\n}\n",
            # R32: roots owned by a project file name, an AssemblyName, a RootNamespace; shared by a package id
            "src/Ow/Acme.Web.csproj": _proj(_items('<PackageReference Include="AcmeCorp.Core" Version="1.0.0" />'),
                                            iu=None),
            "src/Ow/Acme.cs": "namespace Acme.Web;\n\npublic class W\n{\n}\n",
            "src/Ox/Ox.csproj": _proj(_pg("<AssemblyName>Echo.App</AssemblyName>")
                                      + _items('<PackageReference Include="Fo.Tools" Version="1.0.0" />'), iu=None),
            "src/Ox/Echo.cs": "namespace Echo.Web;\n\npublic class E\n{\n}\n",
            "src/Oy/Oy.csproj": _proj(_pg("<RootNamespace>Foxtrot</RootNamespace>"), iu=None),
            "src/Oy/Fox.cs": "namespace Foxtrot.Web;\n\npublic class F\n{\n}\n\nnamespace Golf.Web2;\n"}
    tree["src/Oy/Fox.cs"] = "namespace Foxtrot.Web\n{\n    public class F\n    {\n    }\n}\n\nnamespace Golf.Web2\n{\n" \
                            "    public class G\n    {\n    }\n}\n"
    with tempfile.TemporaryDirectory(prefix="dm_tccs_l_") as td:
        b = binder_on(td, tree)

        def rf(rel, name, arity):
            fn = getattr(b, "resolve_free", None)
            try:
                return fn(rel, name, arity=arity) if fn else None
            except TypeError:
                return None
        got = {"Result<1>": rf("src/Ga/Use.cs", "Result", 1), "Result<0>": rf("src/Ga/Use.cs", "Result", 0),
               "Result<?>": rf("src/Ga/Use.cs", "Result", None), "Plain<0>": rf("src/Ga/Use.cs", "Plain", 0)}
        sym = lambda r, rel: r is not None and getattr(r, "kind", None) == "symbol" and getattr(r, "rel", "") == rel
        check("L binder: R30 the type index carries generic arity: `Result<int>` is Result<T>, `Result` is no project "
              "type, a spelling of unknown arity unknown",
              sym(got["Result<1>"], "src/Ga/Res.cs") and not sym(got["Result<0>"], "src/Ga/Res.cs")
              and got["Result<?>"] == unk and sym(got["Plain<0>"], "src/Ga/Res.cs"), got)
        got = {"Local@Use": rf("src/Ga/Use.cs", "Local", 0), "Local@Local": rf("src/Ga/Local.cs", "Local", 0)}
        check("L binder: R28 a `file` class is found from its own file only",
              not sym(got["Local@Use"], "src/Ga/Local.cs") and sym(got["Local@Local"], "src/Ga/Local.cs"), got)
        got = {"S1": rf("src/Ga/St.cs", "Timer", 0), "S2": rf("src/Ga/St2.cs", "Timer", 0)}
        check("L binder: R23 a block's `using static` of a project type nesting the name: unknown (never the outer "
              "using's type); one nesting no such name passes it on",
              got["S1"] == unk and got["S2"] != unk and not sym(got["S2"], "src/Ga/Hold.cs"), got)
        got = rf("src/Ga/Cap.cs", "Plain", 0)
        check("L binder: R27 at a capable enclosing namespace (System.Threading.Jobs) a name the project does not "
              "declare there is unknown, never the compilation unit's using's class", got == unk, got)
        ord1, ord2 = rf("src/Ga/Nb.cs", "Ord", 0), None
        rs = getattr(b, "resolve_scoped", None)
        try:
            ci = [c.name for c in rd.facts("src/Ga/Nb.cs", Src(tree)).classes]
            ord1 = rs("src/Ga/Nb.cs", ci.index("In1"), "Ord", arity=0) if rs else None
            ord2 = rs("src/Ga/Nb.cs", ci.index("In2"), "Ord", arity=0) if rs else None
        except Exception as exc:  # noqa: BLE001
            ord1 = ord2 = repr(exc)
        check("L binder: R29 a block's using applies inside that block only: In1's Ord is Ga.X's, In2's (another "
              "block of the same namespace) the compilation unit's Ga.Y's",
              sym(ord1, "src/Ga/X.cs") and sym(ord2, "src/Ga/Y.cs"), (ord1, ord2))
        got = {"two blocks": rf("src/Ga/Nb.cs", "Ord", 0), "one block": rf("src/Ga/Use.cs", "Plain", 0)}
        check("L binder: R29 resolve_free of a file whose namespace declarations answer differently (Nb.cs: Ga.X's Ord "
              "in one, Ga.Y's in the other): unknown; a file of one declaration answers with its own lookup",
              got["two blocks"] == unk and sym(got["one block"], "src/Ga/Res.cs"), got)
        ig = getattr(b, "in_group", None)
        got = {r: ig(r) for r in ("src/Gb/Junk/J.cs", "src/Gb/Kept.cs", "src/Gc/Maybe/M.cs", "src/Gc/Q.cs",
                                  "src/Gd/In.cs", "src/Gd/Out.cs", "src/Sh/P.Part.cs")} if ig else None
        check("L binder: R25 in_group: a readable Remove takes a file out (False); default items off: an unlisted file "
              "maybe (None); a Remove through a path property: out or maybe, never True; a listed file, a file in no "
              "project's directory: True",
              got is not None and got.pop("src/Gc/Maybe/M.cs") in (False, None)
              and got == {"src/Gb/Junk/J.cs": False, "src/Gb/Kept.cs": True, "src/Gc/Q.cs": True, "src/Gd/In.cs": True,
                          "src/Gd/Out.cs": None, "src/Sh/P.Part.cs": True}, got)
        try:
            parts = [sorted(p) for p in b.partials()]
            un = sorted(b.partials_unmerged())
        except Exception as exc:  # noqa: BLE001
            parts, un = repr(exc), None
        p_ok = isinstance(parts, list) and [("src/Gb/Kept.cs", 0), ("src/Sh/P.Part.cs", 0)] in parts
        check("L binder: R31 partial parts merge per compiling project: Gb's P with the part it links; Q's linked part, "
              "which two projects compile into their own Q, joins no group (unmerged)",
              p_ok and ("src/Sh2/Q.Part.cs", 0) in (un or ()), (parts, un))
        sh = {"src/Gs/Gs.csproj": _proj(iu=None),
              "src/Gs/Timer.cs": "namespace Gs\n{\n    public class Timer\n    {\n    }\n}\n",
              "src/Gs/Models/Clock.cs": "namespace Gs.Models\n{\n    public class Clock\n    {\n    }\n}\n",
              "src/Gs/Jobs/J1.cs": "namespace Gs.Jobs\n{\n    using System.Timers;\n\n    public class J1\n    {\n    }\n}\n",
              "src/Gs/Jobs/J2.cs": "namespace Gs.Jobs\n{\n    public class J2\n    {\n    }\n}\n",
              "src/Gs/Jobs/J3.cs": "using Tm = Gs.Models.Clock;\n\nnamespace Gs.Jobs\n{\n    using System.Threading;\n\n"
                                   "    public class J3\n    {\n    }\n}\n",
              "src/Gs/Jobs/J4.cs": "using Tm = Gs.Models.Clock;\n\nnamespace Gs.Jobs\n{\n    public class J4\n    {\n    }\n}\n",
              "src/Gs/Jobs/J5.cs": "namespace Gs\n{\n    using Tm = Gs.Models.Clock;\n\n    namespace Jobs\n    {\n"
                                   "        using System.Threading;\n\n        public class J5\n        {\n        }\n    }\n}\n"}
        with tempfile.TemporaryDirectory(prefix="dm_tccs_s_") as td2:
            bs = binder_on(td2, sh)

            def sf(rel, name):
                fn = getattr(bs, "resolve_free", None)
                try:
                    return fn(rel, name, arity=0) if fn else None
                except TypeError:
                    return None

            def ss(rel, name, kind=None):
                fn = getattr(bs, "resolve_scoped" if kind is None else "alias_in_effect", None)
                try:
                    return fn(rel, 0, name, arity=0) if fn else None
                except TypeError:
                    return None
            isns = lambda r: getattr(r, "kind", None) == "namespace"
            got = {"free J1": sf("src/Gs/Jobs/J1.cs", "Timer"), "scoped J1": ss("src/Gs/Jobs/J1.cs", "Timer"),
                   "alias J1": ss("src/Gs/Jobs/J1.cs", "Timer", "a"), "free J2": sf("src/Gs/Jobs/J2.cs", "Timer"),
                   "scoped J2": ss("src/Gs/Jobs/J2.cs", "Timer")}
            check("L binder: shadow: a block's `using System.Timers;` may import a TYPE Timer, which C# finds before "
                  "the parent namespace's project Timer: unknown (resolve_free, resolve_scoped, alias_in_effect); the "
                  "same block without it: Gs.Timer",
                  got["free J1"] == unk and got["scoped J1"] == unk and got["alias J1"] == ("unknown",)
                  and sym(got["free J2"], "src/Gs/Timer.cs") and sym(got["scoped J2"], "src/Gs/Timer.cs"), got)
            got = {"free": sf("src/Gs/Jobs/J1.cs", "Gs"), "scoped": ss("src/Gs/Jobs/J1.cs", "Gs")}
            check("L binder: shadow: a project NAMESPACE found past that using still answers (the head of a qualified "
                  "name `Gs.Models.Clock`: an external type Gs would have to nest the rest)",
                  isns(got["free"]) and isns(got["scoped"]), got)
            got = {"J3": sf("src/Gs/Jobs/J3.cs", "Tm"), "J4": sf("src/Gs/Jobs/J4.cs", "Tm"),
                   "J5": sf("src/Gs/Jobs/J5.cs", "Tm")}
            check("L binder: shadow: an alias further out (the compilation unit's J3, an enclosing block's J5) behind "
                  "a block's `using System.Threading;`: resolve_free unknown (that using may import a type Tm); with "
                  "no block using (J4) the alias's Clock",
                  got["J3"] == unk and got["J5"] == unk and sym(got["J4"], "src/Gs/Models/Clock.cs"), got)
        own = getattr(b, "owned_roots", None)
        ext = getattr(b, "ext_root", None)
        got = ({"owned": sorted(own()), **{n: ext(n) for n in ("Acme.Web", "Echo.Web", "Foxtrot.Web", "Golf.Web2",
                                                                 "Gb", "System.X", "Microsoft.Y")}}
               if own and ext else None)
        check("L binder: R32 owned roots (a project file name, an AssemblyName, a RootNamespace: exact first segments); "
              "ext_root: System / Microsoft, an unowned root, a root a package id's first segment starts with "
              "(AcmeCorp: Acme) or is a prefix of (Fo: Foxtrot)",
              got is not None and {"Acme", "Echo", "Foxtrot", "Ga", "Gb", "Gc", "Gd", "Ox", "Oy"} <= set(got["owned"])
              and got["Acme.Web"] is True and got["Echo.Web"] is False and got["Foxtrot.Web"] is True
              and got["Golf.Web2"] is True and got["Gb"] is False and got["System.X"] is True
              and got["Microsoft.Y"] is True, got)
        cap = {n: b.capable(n) for n in ("Acme.Web", "Echo.Web", "Golf.Web2")}
        trn = {n: b.transparent(n) for n in ("Acme.Web", "Echo.Web", "Golf.Web2")}
        check("L binder: R32 capable / transparent read the one rule: an ext root capable and never transparent, an "
              "owned unshared root with two project namespaces transparent",
              cap["Acme.Web"] and cap["Golf.Web2"] and not trn["Acme.Web"] and not trn["Golf.Web2"]
              and not cap["Echo.Web"], (cap, trn))
    # csharp_msbuild's new fields, through the read= seam
    mod = _try_import("csharp_msbuild")
    if mod is None:
        return
    rg = lambda files, g: mod.read_group("/nonexistent-dm-root", g, read=dict(files))
    r = rg({"src/P/Acme.Web.csproj": _proj(_pg("<AssemblyName>Acme.App</AssemblyName>",
                                               "<RootNamespace>Acme.Root</RootNamespace>"), iu=None)}, "src/P")
    check("L msbuild: R32 names: the AssemblyName, the RootNamespace and the project file's stem",
          set(getattr(r, "names", ())) == {"Acme.App", "Acme.Root", "Acme.Web"}, getattr(r, "names", None))
    r = rg({"src/P/P.csproj": _proj(_items('<Compile Remove="Junk/**" />', '<Compile Include="Gen/*.cs" />'),
                                    iu=None)}, "src/P")
    cmp_ = tuple(c for c in getattr(r, "compile", ()) if c[0] in ("rem", "inc"))
    check("L msbuild: R25 compile: the in-directory Compile items in order (after the default glob), root-relative "
          "globs, certain",
          [(c[0], c[2]) for c in cmp_] == [("rem", True), ("inc", True)] and "Junk" in cmp_[0][1]
          and getattr(r, "compile_unread", None) is False, (cmp_, getattr(r, "compile_unread", None)))
    cst = getattr(mod, "compile_state", None)
    st = {x: cst(r, "src/P", x) for x in ("src/P/Junk/a.cs", "src/P/Junk/Sub/b.cs", "src/P/Main.cs")} if cst else None
    check("L msbuild: R25 compile_state: a file a readable Remove matches is out (False), another one in (True)",
          st == {"src/P/Junk/a.cs": False, "src/P/Junk/Sub/b.cs": False, "src/P/Main.cs": True}, st)
    for label, files in (("EnableDefaultCompileItems=false", {"src/P/P.csproj": _proj(_pg(
            "<EnableDefaultCompileItems>false</EnableDefaultCompileItems>"), iu=None)}),
                         ("EnableDefaultItems=false", {"src/P/P.csproj": _proj(_pg(
                             "<EnableDefaultItems>false</EnableDefaultItems>"), iu=None)}),
                         ("a Remove through a path property", {"src/P/P.csproj": _proj(_items(
                             '<Compile Remove="$(MSBuildProjectDirectory)/Junk/**" />'), iu=None)})):
        r = rg(files, "src/P")
        got = cst(r, "src/P", "src/P/Junk/a.cs") if cst else "no compile_state"
        check(f"L msbuild: R25 {label}: a file under it is maybe compiled (None, the Compile reading unread) or, "
              "for a Remove read as a path, out (False): never certain", got is False
              or getattr(r, "compile_unread", None) is True and got is None, (getattr(r, "compile_unread", None), got))
    files = {"src/A/A.csproj": _proj(_items('<PackageReference Include="Pk.One" Version="1.0.0" />'), iu=None),
             "src/B/B.csproj": _proj(_items('<PackageReference Include="Pk.Two" Version="2.0.0" />',
                                            '<ProjectReference Include="../A/A.csproj" />'), iu=None),
             "src/C/C.csproj": _proj(iu=None),
             "src/D/D.csproj": _proj(_items('<ProjectReference Include="$(Elsewhere)/X.csproj" />'), iu=None)}
    ps = {g: getattr(rg(files, f"src/{g}"), "pkgsig", "<none>") for g in "ABCD"}
    ids = {g: sorted(x[0] for x in v) if isinstance(v, frozenset) else v for g, v in ps.items()}
    check("L msbuild: R37 pkgsig: the packages over the readable ProjectReference closure (ids lower-cased, with "
          "versions); none: an empty set",
          ids.get("A") == ["pk.one"] and ids.get("B") == ["pk.one", "pk.two"] and ps.get("C") == frozenset()
          and all(len(x) == 3 for x in ps.get("B") or ()), ps)
    r = rg(files, "src/D")
    check("L msbuild: R37 an unreadable ProjectReference: the reading incomplete",
          r.complete is False and tuple(r.reasons), (r.complete, r.reasons))
    # the core's comparisons of the new inputs
    T = tc()
    sc = getattr(getattr(T, "_Linker", None), "cs_sig_cmp", None)
    ar0, ar1 = (("#arity", 0), "#"), (("#arity", 1), "#")
    o = (("c", "La.Order"), "")
    got = {"same": sc((o, ar0), (o, ar0)) if sc else None, "arity": sc((o, ar0), (o, ar1)) if sc else None,
           "one side": sc((o, ar0), (o,)) if sc else None}
    check("L core: R22 cs_sig_cmp: another own arity is another signature (False); one side without the arity "
          "element: unknown", got == {"same": True, "arity": False, "one side": None}, got)
    cu = getattr(T, "_cs_cu_comparable", None)

    def lev(meta):
        return (("u", frozenset({"System.Collections.Generic"}), frozenset({"System.Collections.Generic"}), meta),)
    a, b2, c, d = (lev(("src/A", True, frozenset(), frozenset({("pk.one", "1.0.0", "")}))),
                   lev(("src/B", True, frozenset(), frozenset({("pk.two", "1.0.0", "")}))),
                   lev(("src/C", True, frozenset(), frozenset({("pk.one", "1.0.0", "")}))),
                   lev(("src/D", True, frozenset(), None)))
    e1, e2 = (lev(("src/E", True, frozenset(), frozenset())), lev(("src/F", True, frozenset(), frozenset())))
    got = {"equal": cu(a, c), "other": cu(a, b2), "unreadable": cu(a, d), "one group": cu(a, a),
           "no package": cu(e1, e2), "one package": cu(e1, a)} if cu else None
    check("L core: R45 (replacing R37's equal signatures) two complete readings of two groups compare only when "
          "neither has a package (equal signatures no longer suffice; an unreadable one never); one group always",
          got == {"equal": False, "other": False, "unreadable": False, "one group": True, "no package": True,
                  "one package": False}, got)
    lv = getattr(T, "_cs_lev_ok", None)
    good = (("m", "Lp", False), ("u", frozenset({"System.Text"})))
    bad = (("m", "Lp", False), ("p", frozenset({"System.Threading"})), ("u", frozenset({"System.Text"})))
    check("L core: R24 a lookup order holding a preprocessor-branch level is no order to compare (_cs_lev_ok False)",
          lv is not None and lv(good) is True and lv(bad) is False, lv and (lv(good), lv(bad)))
    ul = getattr(T, "_cs_ulevels", None)
    lv_s = (("u", frozenset(), frozenset({"Ga.Lib.Holder"})), ("u", frozenset({"System.Threading"})))
    got = ul(lv_s) if ul else None
    check("L core: R23 _cs_ulevels keeps a level that holds only a `using static` (its type is a level of its own)",
          got is not None and len(got) == 2 and got[-1] == frozenset({"System.Threading"}) and bool(got[0]), got)


# ================================================================================================
# Z: the closing round (tests TREES["close"]: R42 / R43 / R47, TREES["closeu"]: R44 / R45 / L2), each decision's
# killer beside its positive sibling and its must-not with the engine's true tuple as teeth, on the typed keys AND the
# edges of the WHOLE push (link, the bare-call and local-def passes, _resolve_calls). Every expectation is Roslyn 4.8's
# binding with its run-time dispatch on this exact tree (the kit's reference); for R45, real MSBuild's (SDK 8.0.202)
# usings of the c6 package shapes k01 / k02, read through csharp_msbuild's read= seam
# ================================================================================================

Z_A, Z_AB, Z_B4, Z_B4P = "src/Qa/S07.cs", "src/Qa/S07b.cs", "src/Qb/All4.cs", "src/Qb/All4p.cs"
Z_C6, Z_C6P, Z_CG, Z_CQ = "src/Qc/All6.cs", "src/Qc/All6p.cs", "src/Qc/AllG.cs", "src/Qc/AllQ.cs"
Z_FB, Z_FD, Z_FB4, Z_FD4 = "src/Qf/Base.cs", "src/Qf/Der.cs", "src/Qf/Base4.cs", "src/Qf/Der4.cs"
Z_N, Z_NB = "src/Qn/S5.cs", "src/Qn/S5b.cs"
Z_GU, Z_GS, Z_HU, Z_HK, Z_HS = "src/Qg/Api/User.cs", "src/Qg/Shared/Order.cs", "src/Qh/Api/User.cs", \
    "src/Qh/Kh/Order.cs", "src/Qh/Shared/Order.cs"
Z_IU, Z_IK, Z_IS, Z_OU, Z_OK = "src/Qi/Api/User.cs", "src/Qi/Ki/Order.cs", "src/Qi/Shared/Order.cs", \
    "src/Qo/Api/User.cs", "src/Qo/Ko/Order.cs"
Z_JI, Z_JB, Z_KI, Z_KC, Z_KI2, Z_KO2 = "src/Qj/A/IRunJ.cs", "src/Qj/B/ImplJ.cs", "src/Qk/Orders/IHandler.cs", \
    "src/Qk/Carts/CartHandler.cs", "src/Qk/Orders/IHandler2.cs", "src/Qk/Orders/Sub/OrderHandler2.cs"
Z_LU, Z_LK, Z_MU, Z_MK, Z_MS, Z_UU = "src/Ql/Api/User.cs", "src/Ql/Kl/Order.cs", "src/Qm/Api/User.cs", \
    "src/Qm/Km/Order.cs", "src/Qm/Shared/Order.cs", "src/Qu/User.cs"
Z_P1, Z_PT, Z_Q2, Z_QT = "src/Qp/A/TickQ1.cs", "src/Qp/B/TickerQ1.cs", "src/Qq/A/TickQ2.cs", "src/Qq/B/TickerQ2.cs"
Z_D8, Z_D8B, Z_D9, Z_D9B, Z_D1, Z_D1B = "src/Qd/S8.cs", "src/Qd/S8b.cs", "src/Qd/S9.cs", "src/Qd/S9b.cs", \
    "src/Qd/R1.cs", "src/Qd/R1b.cs"
Z_DM, Z_DMB, Z_DA, Z_DAB = "src/Qd/M1.cs", "src/Qd/M1b.cs", "src/Qd/A1.cs", "src/Qd/A1b.cs"
Z_DC, Z_DCB, Z_DA2, Z_DA2B, Z_DK, Z_DKB = "src/Qd/C1.cs", "src/Qd/C1b.cs", "src/Qd/A2.cs", "src/Qd/A2b.cs", \
    "src/Qd/MK.cs", "src/Qd/MKb.cs"
Z_47A, Z_47B, Z_47C, Z_47D = "src/Qri/R2.cs", "src/Qri/R3.cs", "src/Qri/X2.cs", "src/Qri/X1.cs"
Z_47E, Z_47F, Z_47G = "src/Qri/R3p.cs", "src/Qri/X2p.cs", "src/Qri/X0.cs"
Z_VI, Z_VC, Z_WI, Z_WC = "src/Qv/Orders/IHandler.cs", "src/Qv/Carts/CartHandler.cs", "src/Qw/Orders/IHandler.cs", \
    "src/Qw/Carts/CartHandler.cs"
Z_XU, Z_SU, Z_YU, Z_ZU, Z_RU = "src/Qx/Web/User.cs", "src/Qs/Web/User.cs", "src/Qy/Web/User.cs", "src/Qz/Web/User.cs", \
    "src/Qr/Web/User.cs"
Z_TU, Z_TN, Z_TI, Z_TF, Z_TH = "src/Qt/Web/User.cs", "src/Qt/Web/Nested.cs", "src/Qt/Web/Inherited.cs", \
    "src/Qt/Web/Field.cs", "src/Qt/Util/Helpers.cs"
Z_EU, Z_EP, Z_EH, Z_E3 = "src/Qe/Web/User.cs", "src/Qe/Web/Plain.cs", "src/Qe/Util/Helpers.cs", "src/Qe/Lib/Helpers3.cs"

# {caller: {target: grade}}: EXACTLY these callers of the tree are keyed, each target in Roslyn 4.8's binding or its
# run-time dispatch set on this exact tree (never from the product). TUserE3.Go and UserU.Go: no key (Z_MUSTNOT)
Z_EXPECT = {
    # R42: an unresolved base may be any project class or interface: the dispatch set is open, nothing is removed
    # (the interface or virtual member, and an implementer the pass proves, ADD-only); its siblings resolve the base
    _m(Z_A, "U07.Go"): {_m(Z_A, "IAli7.Al7"): I},
    _m(Z_AB, "U07b.Go"): {_m(Z_AB, "IAli7b.Al7b"): I, _m(Z_AB, "AliImpl7b.Al7b"): I},
    _m(Z_B4, "User4.Go"): {_m(Z_B4, "IAli4.Al4"): I, _m(Z_B4, "Base4.Al4"): I},
    _m(Z_B4P, "User4p.Go"): {_m(Z_B4P, "IAli4p.Al4p"): I, _m(Z_B4P, "Base4p.Al4p"): A, _m(Z_B4P, "Impl4p.Al4p"): A},
    _m(Z_C6, "User6.Go"): {_m(Z_C6, "Base6.Al6"): I},
    _m(Z_C6P, "User6p.Go"): {_m(Z_C6P, "Base6p.Al6p"): I, _m(Z_C6P, "Impl6p.Al6p"): I},
    _m(Z_CG, "UserG6.Go"): {_m(Z_CG, "BaseG6.AlG"): I},
    _m(Z_CQ, "UserQ6.Go"): {_m(Z_CQ, "BaseQ6.AlQ"): I},
    _m(Z_FB4, "TUserE4.Go"): {_m(Z_FD4, "TDerE4.ArmE4"): I},      # a resolved base without the name: the hit binds
    # the core's audit sites (its cases ub8, ub9, ri1, mo1, ax1): the interface member alone where the listing is an
    # unresolved alias (an inherited Base8 / Base9 member, a re-implementing Mid1, an alias of IRepoA<OrderA>), no
    # key where an inherited member may hide a type (CtlM: C# reads the property Helper); the owned siblings proven
    _m(Z_D8, "U08.Go"): {_m(Z_D8, "IAli8.Al8"): I},
    _m(Z_D8B, "U08b.Go"): {_m(Z_D8B, "IAli8b.Al8b"): I, _m(Z_D8B, "Base8b.Al8b"): I},
    _m(Z_D9, "U09.Go"): {_m(Z_D9, "IAli9.Al9"): I},
    _m(Z_D9B, "U09b.Go"): {_m(Z_D9B, "IAli9b.Al9b"): I, _m(Z_D9B, "Base9b.Al9b"): I},
    _m(Z_D1, "U1.Go"): {_m(Z_D1, "IAli1.AlR"): I},
    _m(Z_D1B, "U1b.Go"): {_m(Z_D1B, "IAli1b.AlRb"): I, _m(Z_D1B, "Base1b.AlRb"): A, _m(Z_D1B, "Mid1b.AlRb"): A},
    _m(Z_DMB, "CtlMb.Get"): {_m(Z_DMB, "HelperX.RunMb"): E},
    _m(Z_DA, "UA.Go"): {_m(Z_DA, "IRepoA.SaveA"): I},
    _m(Z_DAB, "UAb.Go"): {_m(Z_DAB, "IRepoA.SaveAb"): I},
    # L1 (the core's l1chain, l1chainv, l1alias2, l1mok): an unresolved base's candidates WITH their chains (ImplC
    # may be an IAliC and a MidC through BaseC: the interface and the base member keyed, ImplC.AlC's tuple kept),
    # alias targets followed to a fixed point (A2 = A1 = IAliA: the interface member alone), a candidate CLASS's
    # members read by member_open (CtlK: no key); the owned siblings proven
    _m(Z_DC, "UserC.Go"): {_m(Z_DC, "IAliC.AlC"): I, _m(Z_DC, "MidC.AlC"): I},
    _m(Z_DC, "UserCv.Go"): {_m(Z_DC, "MidC.AlC"): I},
    _m(Z_DCB, "UserCb.Go"): {_m(Z_DCB, "IAliCb.AlCb"): I, _m(Z_DCB, "MidCb.AlCb"): A, _m(Z_DCB, "ImplCb.AlCb"): A},
    _m(Z_DCB, "UserCvb.Go"): {_m(Z_DCB, "MidCb.AlCb"): I, _m(Z_DCB, "ImplCb.AlCb"): I},
    _m(Z_DA2, "UserA.Go"): {_m(Z_DA2, "IAliA.AlA"): I},
    _m(Z_DA2B, "UserAb.Go"): {_m(Z_DA2B, "IAliAb.AlAb"): I, _m(Z_DA2B, "BaseAb.AlAb"): I},
    _m(Z_DKB, "CtlKb.Get"): {_m(Z_DKB, "HelperXK.RunKb"): E},
    # R47 (the reviewer's ri2, ri3es, the core's xa2, xa1e): a class from the re-lister up to the listing class
    # defines the member where an interface on the re-lister's base list may list IAli47* again (through an
    # unresolved alias or name, or past an external or unresolved entry): the interface member alone; the siblings
    # whose listing the pass proves keep every implementer, and L1's X0 (an alias that can reach nothing) keeps the
    # known set {IAli47g.Al47g, Base47g.Al47g}
    _m(Z_47A, "User47a.Go"): {_m(Z_47A, "IAli47a.Al47a"): I},
    _m(Z_47B, "User47b.Go"): {_m(Z_47B, "IAli47b.Al47b"): I},
    _m(Z_47C, "User47c.Go"): {_m(Z_47C, "IAli47c.Al47c"): I},
    _m(Z_47D, "User47d.Go"): {_m(Z_47D, "IAli47d.Al47d"): I},
    _m(Z_47E, "User47e.Go"): {_m(Z_47E, "IAli47e.Al47e"): I, _m(Z_47E, "Base47e.Al47e"): A,
                              _m(Z_47E, "Sub47e.Al47e"): A},
    _m(Z_47F, "User47f.Go"): {_m(Z_47F, "IAli47f.Al47f"): I, _m(Z_47F, "Base47f.Al47f"): A,
                              _m(Z_47F, "Sub47f.Al47f"): A},
    _m(Z_47G, "User47g.Go"): {_m(Z_47G, "IAli47g.Al47g"): I, _m(Z_47G, "Base47g.Al47g"): I},
    # R43: B5's overrides are spelled with names the binder cannot place (the root Xn5 no project owns): their slot is
    # unknown, ADD-only (the base member keyed, the engine's tuple to the override kept); the owned sibling proves it
    _m(Z_N, "S5User.Go"): {_m(Z_N, "A5.Run5"): I},
    _m(Z_N, "S5User.Go2"): {_m(Z_N, "A5.Open5"): I},
    _m(Z_N, "S5User.Go3"): {_m(Z_N, "A5.Pick5"): I},
    _m(Z_N, "S5User.Go4"): {_m(Z_N, "A5.Arr5"): I},
    _m(Z_NB, "S5bUser.Go2"): {_m(Z_NB, "A5b.Open5b"): I, _m(Z_NB, "B5b.Open5b"): I},
    _m(Z_NB, "S5bUser.Go4"): {_m(Z_NB, "A5b.Arr5b"): I, _m(Z_NB, "B5b.Arr5b"): I},
    # R44: a using written inside a namespace declaration means what C# resolves from that declaration outward;
    # `global::` and a directive outside every declaration are absolute
    _m(Z_GU, "UserG.Go"): {_m(Z_GU, "Order.SaveG"): E},          # c26b: Qg.Kg (the same file), never the global Kg
    _m(Z_HU, "UserH.Go"): {_m(Z_HK, "Order.SaveH"): I},          # c26relusing: Qh.Kh
    _m(Z_IU, "UserI.Go"): {_m(Z_IK, "Order.SaveI"): I},          # c28relusing2: after a file-scoped namespace
    _m(Z_OU, "UserO.Go"): {_m(Z_OU, "Order.SaveO"): E},          # a CU using stays absolute: the global Ko
    _m(Z_JI, "RunUserJ.Go"): {_m(Z_JI, "IRunJ.Run"): I, _m(Z_JI, "RunBaseJ.Run"): I},          # c29relmap
    _m(Z_KI, "Caller.Go"): {_m(Z_KI, "IHandler.Handle"): I, _m(Z_KI, "HandlerBase.Handle"): I},  # c30fsrel
    _m(Z_KI2, "Caller2.Go"): {_m(Z_KI2, "IHandler2.Handle2"): I, _m(Z_KO2, "OrderHandler2.Handle2"): I},
    _m(Z_LU, "UserL.Go"): {_m(Z_LU, "Order.SaveL"): E},          # c27b: global::Kl.Order (the same file)
    _m(Z_MU, "UserM.Go"): {_m(Z_MS, "Order.SaveM"): I},          # c27globalalias: global::Km.Order
    # the core side: one text `using Ev;` of two meanings (Qv.Orders.Ev / the global Ev): the interface member alone;
    # one meaning (the global Ew) in both: CartHandlerW implements (the reader's r44ulev / r44ulevb)
    _m(Z_VI, "CallerV.Go"): {_m(Z_VI, "IHandlerV.HandleV"): I},
    _m(Z_WI, "CallerW.Go"): {_m(Z_WI, "IHandlerW.HandleW"): I, _m(Z_WC, "CartHandlerW.HandleW"): I},
    _m(Z_XU, "UserX.Go"): {_m("src/Qx/Web/Kx/Order.cs", "Order.SaveX"): I},     # nested blocks: Qx.Web.Kx
    _m(Z_SU, "UserS.Go"): {_m("src/Qs/Us/Helpers.cs", "HelpersS.RunS"): I},     # a relative `using static`
    _m(Z_RU, "UserR.Go"): {_m("src/Qr/Shared/Order.cs", "Order.SaveR"): I},     # nothing capable: the global Kq
    # UserY.Go (the global, external Uy.HelpersY) and UserZ.Go (Qz capable: an external Qz.Kz): no key
    # L2 (R29 for `using static`): the imported member only where no enclosing type may hold the name (UserT.Go,
    # InnerT2.Go3, UserE.Go), and only in the block that writes it (PlainE.Go3: the compilation unit's HelpersE3).
    # InnerT.Go2, InnerT3.Go4, InnerT4.Go5 (an enclosing member: declared, inherited, a field), InnerE.Go2 and
    # UserE2.Go (its block's and the compilation unit's `using static` both supply RunE: the nearer wins): no key
    _m(Z_TU, "UserT.Go"): {_m(Z_TH, "HelpersT.RunT"): I},
    _m(Z_TN, "InnerT2.Go3"): {_m(Z_TH, "HelpersT.RunT"): I},
    _m(Z_EU, "UserE.Go"): {_m(Z_EH, "HelpersE.RunE"): I},
    _m(Z_EP, "PlainE.Go3"): {_m(Z_E3, "HelpersE3.RunE"): I},
    # R45: QpA / QpB both reach Pk.Build (QpB through a ProjectReference), the package's build/ props reach QpA only
    # (real MSBuild, c6 k01): the pass cannot compare their usings (the interface member alone, nothing removed); no
    # package anywhere: QqA's and QqB's usings compare (TickerQ2 implements ITickQ2.Tick)
    _m(Z_P1, "TickUserQ1.Go"): {_m(Z_P1, "ITickQ1.Tick"): I},
    _m(Z_Q2, "TickUserQ2.Go"): {_m(Z_Q2, "ITickQ2.Tick"): I, _m(Z_QT, "TickerQ2.Tick"): I},
}

# (caller, member, kind, teeth): "keep" no removal of the member's tuples; "none" no typed key either
Z_MUSTNOT = [
    (_m(Z_A, "U07.Go"), "Al7", "keep", True),                 # R42 s07: the alias base may be IAli7 itself
    (_m(Z_B4, "User4.Go"), "Al4", "keep", True),              # R42 ub4: interface dispatch past an unresolved base
    (_m(Z_C6, "User6.Go"), "Al6", "keep", True),              # R42 ub6: SA1200 `using System;`, virtual dispatch
    (_m(Z_CG, "UserG6.Go"), "AlG", "keep", True),             # R42: a generic spelling
    (_m(Z_CQ, "UserQ6.Go"), "AlQ", "keep", True),             # R42: a qualified spelling
    (_m(Z_FB, "TUserE3.Go"), "ArmE3", "none", True),          # R42 e3: the rival walk stops unknown at an 'unres'
    (_m(Z_D8, "U08.Go"), "Al8", "keep", True),                # R42 ub8: Base8.Al8 runs for an Impl8
    (_m(Z_D9, "U09.Go"), "Al9", "keep", True),                # R42 ub9: Base9.Al9 runs for an Impl9
    (_m(Z_D1, "U1.Go"), "AlR", "keep", True),                 # R42 ri1: Mid1.AlR runs for a Mid1
    (_m(Z_DM, "CtlM.Get"), "RunM", "none", True),             # R42 mo1: the inherited property Helper: HelperX.RunM
    (_m(Z_DA, "UA.Go"), "SaveA", "keep", True),               # R42 ax1: Both.SaveA(OrderA) runs for a Both
    (_m(Z_DC, "UserC.Go"), "AlC", "keep", True),              # L1 l1chain: ImplC.AlC runs for an ImplC
    (_m(Z_DC, "UserCv.Go"), "AlC", "keep", True),             # L1 l1chainv: ... through a MidC receiver too
    (_m(Z_DA2, "UserA.Go"), "AlA", "keep", True),             # L1 l1alias2: BaseA.AlA runs for an ImplA
    (_m(Z_DK, "CtlK.Get"), "RunK", "none", True),             # L1 l1mok: the inherited property HelperK
    (_m(Z_47A, "User47a.Go"), "Al47a", "keep", True),         # R47 ri2: IX47a re-lists IAli47a through an alias
    (_m(Z_47B, "User47b.Go"), "Al47b", "keep", True),         # R47 ri3es: SA1200, an explicit re-implementation
    (_m(Z_47C, "User47c.Go"), "Al47c", "keep", True),         # R47 xa2: IAli47c past IDisposable
    (_m(Z_47D, "User47d.Go"), "Al47d", "keep", True),         # R47 xa1e: past an unresolved alias, explicit
    (_m(Z_N, "S5User.Go"), "Run5", "keep", True),             # R43: nint / an unplaced IntPtr
    (_m(Z_N, "S5User.Go2"), "Open5", "keep", True),           # R43 s5: int? / an unplaced Nullable<int>
    (_m(Z_N, "S5User.Go3"), "Pick5", "keep", True),           # R43: a tuple / an unplaced ValueTuple<int, string>
    (_m(Z_N, "S5User.Go4"), "Arr5", "keep", True),            # R43: int?[] / an unplaced Nullable<int>[]
    (_m(Z_UU, "UserU.Go"), "SaveU", "none", True),            # R44: `using Ku;` in Xu (capable): unknown
    (_m(Z_VI, "CallerV.Go"), "HandleV", "keep", True),        # R44 (core): HandlerBaseV.HandleV implements it
    (_m(Z_P1, "TickUserQ1.Go"), "Tick", "keep", True),        # R45 k01: TickBaseQ1.Tick implements ITickQ1.Tick
    (_m(Z_TU, "InnerT.Go2"), "RunT", "none", True),           # L2 r44sty: the enclosing OuterT.RunT
    (_m(Z_TI, "InnerT3.Go4"), "RunT", "none", True),          # L2: the enclosing class's inherited OuterBaseT.RunT
    (_m(Z_TF, "InnerT4.Go5"), "RunT", "none", False),         # L2: the enclosing class's delegate field RunT
    (_m(Z_EU, "InnerE.Go2"), "RunE", "none", True),           # L2 r44stx: another block's `using static`
]

Z_NO_EDGE = [
    (_m(Z_47G, "User47g.Go"), _m(Z_47G, "Sub47g.Al47g")),           # R47 x L1: IX47g reaches only IUnk47g
    (_m(Z_KI2, "Caller2.Go"), _m(Z_KI2, "HandlerBase2.Handle2")),   # R44: one Models, OrderHandler2 implements
    (_m(Z_Q2, "TickUserQ2.Go"), _m(Z_Q2, "TickBaseQ2.Tick")),       # R45: no package, TickerQ2 implements
    (_m(Z_WI, "CallerW.Go"), _m(Z_WI, "HandlerBaseW.HandleW")),     # R44 (core): one Ew.Item, CartHandlerW implements
    (_m(Z_TU, "UserT.Go"), _m(Z_TU, "OuterT.RunT")),                # L2: no enclosing RunT, the `using static` applies
    (_m(Z_EU, "UserE.Go"), _m(Z_EU, "OuterE.RunE")),                # L2: its own block's `using static`
]
# the engine's receiver-blind AMBIGUOUS tuples a typed EXTRACTED key replaces (default mode only: DEEPMAP_PRECISE_CALLS
# keeps the engine's exact same-file target instead)
Z_NO_EDGE_DEFAULT = [
    (_m(Z_GU, "UserG.Go"), _m(Z_GS, "Order.SaveG")),               # R44 c26b: never the global Kg's
    (_m(Z_OU, "UserO.Go"), _m(Z_OK, "Order.SaveO")),               # a CU using: never Qo.Ko's
    (_m(Z_LU, "UserL.Go"), _m(Z_LK, "Order.SaveL")),               # R44 c27b: never Ql.Kl's
]

Z_ABSENT = [
    (_m(Z_FB, "TUserE3.Go"), _m(Z_FD, "TDerE3.ArmE3")),            # R42 e3: C# binds the base's ArmE3
    (_m(Z_DM, "CtlM.Get"), _m(Z_DM, "Helper.RunM")),               # R42 mo1: never the namespace's class Helper
    (_m(Z_DK, "CtlK.Get"), _m(Z_DK, "HelperK.RunK")),              # L1 l1mok: never the namespace's class HelperK
    (_m(Z_HU, "UserH.Go"), _m(Z_HS, "Order.SaveH")),               # R44 c26relusing: never the global Kh's
    (_m(Z_IU, "UserI.Go"), _m(Z_IS, "Order.SaveI")),               # R44 c28relusing2: never the global Ki's
    (_m(Z_JI, "RunUserJ.Go"), _m(Z_JB, "ImplJ.Run")),              # R44 c29relmap: another Kj.Timer
    (_m(Z_KI, "Caller.Go"), _m(Z_KC, "CartHandler.Handle")),       # R44 c30fsrel: another Models.Item
    (_m(Z_MU, "UserM.Go"), _m(Z_MK, "Order.SaveM")),               # R44 c27globalalias: never Qm.Km's
    (_m(Z_P1, "TickUserQ1.Go"), _m(Z_PT, "TickerQ1.Tick")),        # R45 k01: another Timer
    (_m(Z_VI, "CallerV.Go"), _m(Z_VC, "CartHandlerV.HandleV")),    # R44 (core): Ev.Item is no Lv.Item
    (_m(Z_XU, "UserX.Go"), _m("src/Qx/Shared/Order.cs", "Order.SaveX")),    # R44: never the global Kx's
    (_m(Z_XU, "UserX.Go"), _m("src/Qx/Kx/Order.cs", "Order.SaveX")),        # ... nor Qx.Kx's (Qx.Web.Kx is nearer)
    (_m(Z_SU, "UserS.Go"), _m("src/Qs/Shared/Helpers.cs", "HelpersS.RunS")),  # R44: never the global Us's
    (_m(Z_YU, "UserY.Go"), _m("src/Qy/Other/Helpers.cs", "HelpersY.RunY")),   # R44: never another block's Oy.Uy
    (_m(Z_ZU, "UserZ.Go"), _m("src/Qz/Shared/Order.cs", "Order.SaveZ")),      # R44 x R27: maybe Qz.Kz (it is)
    (_m(Z_TU, "InnerT.Go2"), _m(Z_TH, "HelpersT.RunT")),           # L2: never the `using static`'s member past an
    (_m(Z_TI, "InnerT3.Go4"), _m(Z_TH, "HelpersT.RunT")),          # enclosing type's RunT (declared, inherited, a
    (_m(Z_TF, "InnerT4.Go5"), _m(Z_TH, "HelpersT.RunT")),          # field)
    (_m(Z_EU, "InnerE.Go2"), _m(Z_EH, "HelpersE.RunE")),           # L2: never another block's `using static`
    (_m(Z_EP, "PlainE.Go3"), _m(Z_EH, "HelpersE.RunE")),
    (_m(Z_EP, "UserE2.Go"), _m(Z_E3, "HelpersE3.RunE")),           # L2: never the farther compilation unit's
]

Z_EDGE = [
    (_m(Z_A, "U07.Go"), _m(Z_A, "AliImpl7.Al7"), None),           # R42: the engine's true tuples, kept
    (_m(Z_B4, "User4.Go"), _m(Z_B4, "Impl4.Al4"), None),
    (_m(Z_C6, "User6.Go"), _m(Z_C6, "Impl6.Al6"), None),
    (_m(Z_CG, "UserG6.Go"), _m(Z_CG, "ImplG6.AlG"), None),
    (_m(Z_CQ, "UserQ6.Go"), _m(Z_CQ, "ImplQ6.AlQ"), None),
    (_m(Z_FB, "TUserE3.Go"), _m(Z_FB, "TBaseE3.ArmE3"), None),
    (_m(Z_D8, "U08.Go"), _m(Z_D8, "Base8.Al8"), None),
    (_m(Z_D9, "U09.Go"), _m(Z_D9, "Base9.Al9"), None),
    (_m(Z_D1, "U1.Go"), _m(Z_D1, "Mid1.AlR"), None),
    (_m(Z_DM, "CtlM.Get"), _m(Z_DM, "HelperX.RunM"), None),
    (_m(Z_DA, "UA.Go"), _m(Z_DA, "Both.SaveA"), None),
    (_m(Z_DC, "UserC.Go"), _m(Z_DC, "ImplC.AlC"), None),             # L1: the engine's true tuples, kept
    (_m(Z_DC, "UserCv.Go"), _m(Z_DC, "ImplC.AlC"), None),
    (_m(Z_DA2, "UserA.Go"), _m(Z_DA2, "BaseA.AlA"), None),
    (_m(Z_DK, "CtlK.Get"), _m(Z_DK, "HelperXK.RunK"), None),
    (_m(Z_DCB, "UserCvb.Go"), _m(Z_DCB, "ImplCb.AlCb"), I),          # ... and the siblings' proofs
    (_m(Z_DA2B, "UserAb.Go"), _m(Z_DA2B, "BaseAb.AlAb"), I),
    (_m(Z_DKB, "CtlKb.Get"), _m(Z_DKB, "HelperXK.RunKb"), E),
    (_m(Z_47A, "User47a.Go"), _m(Z_47A, "Sub47a.Al47a"), None),      # R47: the engine's true tuples, kept
    (_m(Z_47B, "User47b.Go"), _m(Z_47B, "Sub47b.Al47b"), None),
    (_m(Z_47C, "User47c.Go"), _m(Z_47C, "Sub47c.Al47c"), None),
    (_m(Z_47D, "User47d.Go"), _m(Z_47D, "Sub47d.Al47d"), None),
    (_m(Z_47E, "User47e.Go"), _m(Z_47E, "Sub47e.Al47e"), A),         # ... and the siblings' proofs
    (_m(Z_47F, "User47f.Go"), _m(Z_47F, "Sub47f.Al47f"), A),
    (_m(Z_47G, "User47g.Go"), _m(Z_47G, "Base47g.Al47g"), I),
    (_m(Z_D8B, "U08b.Go"), _m(Z_D8B, "Base8b.Al8b"), I),
    (_m(Z_D9B, "U09b.Go"), _m(Z_D9B, "Base9b.Al9b"), I),
    (_m(Z_D1B, "U1b.Go"), _m(Z_D1B, "Mid1b.AlRb"), A),
    (_m(Z_DMB, "CtlMb.Get"), _m(Z_DMB, "HelperX.RunMb"), E),
    (_m(Z_AB, "U07b.Go"), _m(Z_AB, "AliImpl7b.Al7b"), I),         # ... and the siblings' proofs
    (_m(Z_B4P, "User4p.Go"), _m(Z_B4P, "Impl4p.Al4p"), A),
    (_m(Z_C6P, "User6p.Go"), _m(Z_C6P, "Impl6p.Al6p"), I),
    (_m(Z_FB4, "TUserE4.Go"), _m(Z_FD4, "TDerE4.ArmE4"), I),
    *[(_m(Z_N, f"S5User.{g}"), _m(Z_N, f"B5.{m}"), None)          # R43: the engine's true tuples, kept
      for g, m in (("Go", "Run5"), ("Go2", "Open5"), ("Go3", "Pick5"), ("Go4", "Arr5"))],
    (_m(Z_NB, "S5bUser.Go2"), _m(Z_NB, "B5b.Open5b"), I),
    (_m(Z_NB, "S5bUser.Go4"), _m(Z_NB, "B5b.Arr5b"), I),
    (_m(Z_GU, "UserG.Go"), _m(Z_GU, "Order.SaveG"), E),           # R44: the true targets
    (_m(Z_HU, "UserH.Go"), _m(Z_HK, "Order.SaveH"), I),
    (_m(Z_IU, "UserI.Go"), _m(Z_IK, "Order.SaveI"), I),
    (_m(Z_OU, "UserO.Go"), _m(Z_OU, "Order.SaveO"), E),
    (_m(Z_JI, "RunUserJ.Go"), _m(Z_JI, "RunBaseJ.Run"), I),       # c29relmap / c30fsrel: the engine's true tuple,
    (_m(Z_KI, "Caller.Go"), _m(Z_KI, "HandlerBase.Handle"), I),   # proven and keyed (the class's member never)
    (_m(Z_KI2, "Caller2.Go"), _m(Z_KO2, "OrderHandler2.Handle2"), I),
    (_m(Z_LU, "UserL.Go"), _m(Z_LU, "Order.SaveL"), E),
    (_m(Z_MU, "UserM.Go"), _m(Z_MS, "Order.SaveM"), I),
    (_m(Z_UU, "UserU.Go"), _m(Z_UU, "Order.SaveU"), None),
    (_m(Z_P1, "TickUserQ1.Go"), _m(Z_P1, "TickBaseQ1.Tick"), None),  # R45: the engine's true tuple, kept
    (_m(Z_Q2, "TickUserQ2.Go"), _m(Z_QT, "TickerQ2.Tick"), I),
    (_m(Z_VI, "CallerV.Go"), _m(Z_VI, "HandlerBaseV.HandleV"), None),    # R44 (core): the engine's true tuple, kept
    (_m(Z_WI, "CallerW.Go"), _m(Z_WC, "CartHandlerW.HandleW"), I),
    (_m(Z_XU, "UserX.Go"), _m("src/Qx/Web/Kx/Order.cs", "Order.SaveX"), I),
    (_m(Z_SU, "UserS.Go"), _m("src/Qs/Us/Helpers.cs", "HelpersS.RunS"), I),
    (_m(Z_RU, "UserR.Go"), _m("src/Qr/Shared/Order.cs", "Order.SaveR"), I),
    (_m(Z_TU, "InnerT.Go2"), _m(Z_TU, "OuterT.RunT"), None),       # L2: the engine's true tuples, kept
    (_m(Z_TI, "InnerT3.Go4"), _m(Z_TI, "OuterBaseT.RunT"), None),
    (_m(Z_EU, "InnerE.Go2"), _m(Z_EU, "OuterE.RunE"), None),
    (_m(Z_TU, "UserT.Go"), _m(Z_TH, "HelpersT.RunT"), I),          # ... and the siblings' proofs
    (_m(Z_TN, "InnerT2.Go3"), _m(Z_TH, "HelpersT.RunT"), I),
    (_m(Z_EU, "UserE.Go"), _m(Z_EH, "HelpersE.RunE"), I),
    (_m(Z_EP, "PlainE.Go3"), _m(Z_E3, "HelpersE3.RunE"), I),
]


def sec_close(buckets) -> None:
    print("\n-- Z: the closing round's shapes (R42-R45: an unresolved base, an unknown head or element, a using inside "
          "a namespace declaration, a package: unknown stays ADD-only), each beside its positive sibling, on the typed "
          "keys AND the pushed edges (both engines, both modes) --")
    _z_units()
    for tree in ("close", "closeu"):
        files = TREES[tree]

        def mine(rows):
            return [r for r in rows if r[0].split("::")[0] in files]
        _sec_tree("Z", tree, buckets, _x_env, {c: t for c, t in Z_EXPECT.items() if c.split("::")[0] in files},
                  mine(Z_MUSTNOT), mine(Z_NO_EDGE), mine(Z_ABSENT), mine(Z_EDGE))
    for b in buckets:
        run = _RUNS.get((b, "closeu", "default", "tree"))
        if run is None:
            continue
        env = _x_env("default")
        edges = push_edges(run, root_for("closeu"), env)
        r0 = run_pipeline(captured(b, "closeu")["graph"], root_for("closeu"), dict(env, DEEPMAP_TYPED_CALLS="0"))
        off = push_edges(r0, root_for("closeu"), dict(env, DEEPMAP_TYPED_CALLS="0"))
        for caller, target in Z_NO_EDGE_DEFAULT:
            ck, tk = key_of(run.nodes, caller), key_of(run.nodes, target)
            check(f"Z {b} [default] {caller.split('::')[-1]}: no pushed edge to {target} (typed linking off: "
                  f"{off.get((ck, tk))})", ck is not None and tk is not None and (ck, tk) not in edges
                  and (ck, tk) in off, (ck, tk, edges.get((ck, tk)), off.get((ck, tk))))


# real MSBuild (SDK 8.0.202, `dotnet msbuild -t:GenerateGlobalUsings`, Release and Debug alike) on the c6 package
# shapes, with a local feed: Pk.Build ships build/Pk.Build.props (<Using Include="Pkg.BuildProps" />), Pk.Trans
# buildTransitive/Pk.Trans.props (<Using Include="Pkg.TransProps" />)
_Z_K01 = {
    "a/a.csproj": ('<Project Sdk="Microsoft.NET.Sdk">\n  <PropertyGroup><TargetFramework>net8.0</TargetFramework>'
                   '<ImplicitUsings>enable</ImplicitUsings></PropertyGroup>\n  <ItemGroup><PackageReference '
                   'Include="Pk.Build" Version="1.0.0" /></ItemGroup>\n</Project>\n'),
    "b/b.csproj": ('<Project Sdk="Microsoft.NET.Sdk">\n  <PropertyGroup><TargetFramework>net8.0</TargetFramework>'
                   '<ImplicitUsings>enable</ImplicitUsings></PropertyGroup>\n  <ItemGroup><ProjectReference '
                   'Include="../c/c.csproj" /></ItemGroup>\n</Project>\n'),
    "c/c.csproj": ('<Project Sdk="Microsoft.NET.Sdk">\n  <PropertyGroup><TargetFramework>net8.0</TargetFramework>'
                   '<ImplicitUsings>enable</ImplicitUsings></PropertyGroup>\n  <ItemGroup><PackageReference '
                   'Include="Pk.Build" Version="1.0.0" /></ItemGroup>\n</Project>\n'),
}
_Z_K02 = {
    "a/a.csproj": _Z_K01["a/a.csproj"].replace('Include="Pk.Build" Version="1.0.0"',
                                               'Include="Pk.Trans" Version="1.0.0" PrivateAssets="all"'),
    "b/b.csproj": _Z_K01["b/b.csproj"],
    "c/c.csproj": _Z_K01["c/c.csproj"].replace('Include="Pk.Build" Version="1.0.0"',
                                               'Include="Pk.Trans" Version="1.0.0" PrivateAssets="all"'),
}
_Z_REAL = {   # the usings real MSBuild generates for each project (c6/real6.json)
    "k01": {"a": frozenset(_B7) | {"Pkg.BuildProps"}, "b": frozenset(_B7), "c": frozenset(_B7) | {"Pkg.BuildProps"}},
    "k02": {"a": frozenset(_B7) | {"Pkg.TransProps"}, "b": frozenset(_B7), "c": frozenset(_B7) | {"Pkg.TransProps"}},
}


def _z_units() -> None:
    """The decisions' own rules: R43's _cs_tcmp on an unknown head or element (never False against another shape; a
    proven difference stays False); R44's directive meanings (CsBinder.frame_meanings / using_meaning) and the lookup
    they feed (resolve_free); R45's comparison of two projects' readings (_cs_cu_comparable) on real MSBuild's k01 /
    k02 package shapes through csharp_msbuild's read= seam, beside a pair with no package."""
    T = tc()
    tcmp = getattr(T, "_cs_tcmp", None)
    i32, ni = ("b", "int"), ("?", ("b", "int"), True)
    unp = ("x", "Nullable", "project?")
    rows = [
        ("R43 int? against a Nullable<int> whose head the binder could not place: unknown (it may be System's)",
         ni, ("g", unp, i32), None),
        ("R43 ... the other way round", ("g", unp, i32), ni, None),
        ("R43 int?[] against Nullable<int>[] of an unplaced head: unknown (the element decides nothing)",
         ("[]", ni, "[]"), ("[]", ("g", unp, i32), "[]"), None),
        ("R43 sibling: int? against a project generic class Box<int>: two types (a project class is no Nullable)",
         ni, ("g", ("c", "box", "Box"), i32), False),
        ("R43 sibling: int? against int: two types", ni, i32, False),
        ("R43 sibling: int?[] against int[]: two types", ("[]", ni, "[]"), ("[]", i32, "[]"), False),
    ]
    for label, a, b, want in rows:
        got = tcmp(a, b) if tcmp else "no _cs_tcmp"
        check(f"Z core: {label}", got is want, got)
    _z_binder()
    _z_msbuild()


def _z_binder() -> None:
    """R44: what a directive written inside a namespace declaration means (CsBinder.frame_meanings, using_meaning),
    each the namespace C# resolves (Roslyn's binding of the name the directive supplies, in TREES["close"]'s shapes)."""
    if rcs() is None:
        return
    rd = rcs().make_reader(tc().LANGUAGES["csharp"])
    tree = {
        "src/Qg/Qg.csproj": _np([RN("Qg")]),
        "src/Qg/Shared/Order.cs": CLOSEU[Z_GS],
        "src/Qg/Api/User.cs": CLOSEU[Z_GU],                                   # c26b: `using Kg;` in namespace Qg
        "src/Qg/Fs/User2.cs": "namespace Qg.Fs;\n\nusing Kg;\n\npublic class U2\n{\n}\n",   # file-scoped (SA1200)
        "src/Qg/Gl/User3.cs": "namespace Qg.Gl\n{\n    using global::Kg;\n    using Ord = global::Kg.Order;\n\n"
                              "    public class U3\n    {\n    }\n}\n",       # global:: absolute
        "src/Qg/Top/User4.cs": "using Kg;\n\nnamespace Qg.Top\n{\n    public class U4\n    {\n    }\n}\n",  # the CU
        "src/Qg/In/User5.cs": "namespace Qg.In\n{\n    using Kz;\n\n    public class U5\n    {\n    }\n}\n",  # no Qg.Kz
        "src/Qg/Kz/Z.cs": "namespace Kz\n{\n    public class Z\n    {\n    }\n}\n",
        # R27 / R32: Xu is a root no project owns (capable): Xu.Ku may be an external namespace
        "src/Qg/Xu/User6.cs": "namespace Xu\n{\n    using Ku;\n\n    public class U6\n    {\n    }\n}\n",
        "src/Qg/Ku/K.cs": "namespace Ku\n{\n    public class Order\n    {\n    }\n}\n",
        # feature folders after a file-scoped namespace (c30fsrel): `using Models;` per feature
        "src/Qg/Orders/Models/Item.cs": "namespace Qg.Orders.Models;\n\npublic class Item\n{\n}\n",
        "src/Qg/Carts/Models/Item.cs": "namespace Qg.Carts.Models;\n\npublic class Item\n{\n}\n",
        "src/Qg/Orders/H.cs": "namespace Qg.Orders;\n\nusing Models;\n\npublic class H1\n{\n}\n",
        "src/Qg/Carts/H.cs": "namespace Qg.Carts;\n\nusing Models;\n\npublic class H2\n{\n}\n",
        # a nested block (r44n1): Qg.Nst.Kg does not exist, Qg.Kg does; a relative `using static` (r44st1)
        "src/Qg/Nst/U7.cs": "namespace Qg\n{\n    namespace Nst\n    {\n        using Kg;\n\n        public class U7\n"
                            "        {\n        }\n    }\n}\n",
        "src/Qg/Hs/Helpers.cs": "namespace Qg.Hs\n{\n    public static class Helpers\n    {\n    }\n}\n",
        "src/Qg/St/U8.cs": "namespace Qg\n{\n    using static Hs.Helpers;\n\n    public class U8\n    {\n    }\n}\n",
    }
    with tempfile.TemporaryDirectory(prefix="dm_tccs_z_") as td:
        write_tree(Path(td), tree)
        facts = {r: rd.facts(r, Src(tree)) for r in tree if r.endswith(".cs")}
        bd = rd.binder(type("Ctx", (), {"root": td, "lang": "csharp", "facts": facts})())
        fm = getattr(bd, "frame_meanings", None)
        um = getattr(bd, "using_meaning", None)

        def meanings(rel, f=1):
            try:
                got = fm(rel, f) if fm else None
            except Exception as exc:  # noqa: BLE001 — a raising binder is a failure, reported as data
                return f"raised {exc!r}"
            return got

        def one(rel, f, text, kind="u"):
            try:
                return um(rel, f, text, kind) if um else "no using_meaning"
            except Exception as exc:  # noqa: BLE001
                return f"raised {exc!r}"
        g = meanings(Z_GU, 2)
        check("Z binder: R44 c26b: `using Kg;` inside `namespace Qg` means Qg.Kg (it exists), never the global Kg",
              isinstance(g, tuple) and len(g) == 7 and g[0] == "Qg" and tuple(g[1]) == ("Qg.Kg",), g)
        got = {"file-scoped": one("src/Qg/Fs/User2.cs", 1, "Kg"),
               "global::": one("src/Qg/Gl/User3.cs", 1, "global::Kg"),
               "global:: alias": one("src/Qg/Gl/User3.cs", 1, "global::Kg.Order", "a"),
               "the CU": one("src/Qg/Top/User4.cs", 0, "Kg"), "no Qg.In.Kz / Qg.Kz": one("src/Qg/In/User5.cs", 1, "Kz"),
               "capable Xu": one("src/Qg/Xu/User6.cs", 1, "Ku")}
        check("Z binder: R44 using_meaning: after a file-scoped namespace Qg.Kg; `global::Kg` and a `global::` alias "
              "target absolute; a CU using absolute; Kz (no Qg.In.Kz, no Qg.Kz) the global one; `using Ku;` inside the "
              "capable Xu unknown (Xu.Ku may be external)",
              got == {"file-scoped": "Qg.Kg", "global::": "Kg", "global:: alias": "Kg.Order", "the CU": "Kg",
                      "no Qg.In.Kz / Qg.Kz": "Kz", "capable Xu": None}, got)
        fo, fc = meanings("src/Qg/Orders/H.cs"), meanings("src/Qg/Carts/H.cs")
        nst, st = one("src/Qg/Nst/U7.cs", 2, "Kg"), meanings("src/Qg/St/U8.cs")
        check("Z binder: R44 frame_meanings: one text of two meanings in two features (`using Models;` after a "
              "file-scoped namespace: Qg.Orders.Models, Qg.Carts.Models); a nested block's using from its own "
              "namespace outward (Qg.Nst.Kg absent: Qg.Kg); a relative `using static` (Qg.Hs.Helpers)",
              fo == ("Qg.Orders", ("Qg.Orders.Models",), (), (), (), (), ())
              and fc == ("Qg.Carts", ("Qg.Carts.Models",), (), (), (), (), ())
              and nst == "Qg.Kg" and isinstance(st, tuple) and len(st) == 7 and tuple(st[5]) == ("Qg.Hs.Helpers",),
              (fo, fc, nst, st))
        dm = getattr(bd, "decl_meanings", None)
        ci = {c.name: i for i, c in enumerate(getattr(facts.get(Z_GU), "classes", None) or [])}
        try:
            chain = dm(Z_GU, ci.get("UserG")) if dm else None
        except Exception as exc:  # noqa: BLE001
            chain = f"raised {exc!r}"
        check("Z binder: R44 decl_meanings: the compilation unit first, then each declaration enclosing the class "
              "(c26b's UserG: the CU, then `namespace Qg` with Qg.Kg)",
              isinstance(chain, list) and len(chain) == 2 and chain[0] == meanings(Z_GU, 0) and chain[1] == g,
              (chain, ci))
        hit = _rfree(bd, Z_GU, "Order")
        top = _rfree(bd, "src/Qg/Top/User4.cs", "Order")
        check("Z binder: R44 resolve_free reads the meanings: c26b's Order is Qg.Kg.Order (the same file), a CU "
              "`using Kg;` gives the global Kg.Order",
              getattr(hit, "kind", "") == "symbol" and getattr(hit, "rel", "") == Z_GU
              and getattr(top, "kind", "") == "symbol" and getattr(top, "rel", "") == Z_GS, (hit, top))
        cap = _rfree(bd, "src/Qg/Xu/User6.cs", "Order")
        check("Z binder: R44 `using Ku;` inside the capable Xu: Order unknown (never Ku.Order proven)",
              cap is tc().RES_UNKNOWN or cap is None, cap)


def _z_msbuild() -> None:
    """R45 through csharp_msbuild's read= seam: k01 (a package's build/ props reach the project referencing the
    package, not one referencing a project that does) and k02 (PrivateAssets="all": the same) read as complete with
    equal visible usings, while real MSBuild gives a and b other usings: two readings of DIFFERENT projects with a
    package (directly or in the ProjectReference closure) never compare; two without one do."""
    mod = _try_import("csharp_msbuild")
    T = tc()
    cu = getattr(T, "_cs_cu_comparable", None)
    lk = getattr(T, "_Linker", None)
    if mod is None or cu is None or lk is None:
        check("Z msbuild: csharp_msbuild and _cs_cu_comparable exist", False)
        return

    def lev(g, r):
        meta = (g, r.complete is True, frozenset(getattr(r, "possible", ()) or ()), lk.cs_pkgsig(r))
        return (("u", frozenset(_B7), frozenset(_B7), meta),)
    for shape, files in (("k01", _Z_K01), ("k02", _Z_K02)):
        rs = {p: mod.read_group("/nonexistent-dm-root", f"{shape}/{p}", read={f"{shape}/{k}": v
                                                                              for k, v in files.items()})
              for p in "abc"}
        real = _Z_REAL[shape]
        got = {pair: cu(lev(f"{shape}/{pair[0]}", rs[pair[0]]), lev(f"{shape}/{pair[1]}", rs[pair[1]]))
               for pair in ("ab", "ac", "bc")}
        check(f"Z msbuild: R45 {shape}: real MSBuild gives a and b other usings ({sorted(real['a'] - real['b'])} in a "
              f"only), while the readings look alike (complete {[rs[p].complete for p in 'abc']}): no pair of the "
              "three projects compares (each has a package, b through c)",
              real["a"] != real["b"] and got == {"ab": False, "ac": False, "bc": False}, got)
    plain = {"a/a.csproj": _Z_K01["b/b.csproj"].replace('<ProjectReference Include="../c/c.csproj" />', ""),
             "b/b.csproj": _Z_K01["b/b.csproj"],
             "c/c.csproj": _Z_K01["b/b.csproj"].replace('<ProjectReference Include="../c/c.csproj" />', "")}
    rs = {p: mod.read_group("/nonexistent-dm-root", f"pl/{p}", read={f"pl/{k}": v for k, v in plain.items()})
          for p in "abc"}
    got = {pair: cu(lev(f"pl/{pair[0]}", rs[pair[0]]), lev(f"pl/{pair[1]}", rs[pair[1]])) for pair in ("ab", "ac")}
    check("Z msbuild: R45 sibling: two complete readings of projects with no package (none in the ProjectReference "
          "closure) compare", got == {"ab": True, "ac": True}, (got, [(rs[p].complete, rs[p].reasons) for p in "abc"]))


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
_MS = "csharp_msbuild"
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
     ['        if res is not None:\n            return ("member", res)'],
     ['        if res is not None and not ns:\n            return ("member", res)'],
     ["shapes"]),
    (_RF, "binder: the caller's own .csproj no longer wins a duplicated full name",
     ["        own = [e for e in ents if e[0] == grp]"], ["        own = []"], ["shapes"]),
    (_RF, "binder: two usings answering one name resolve to the first",
     ['        if len(found) > 1:\n            return ("unknown",)'],
     ['        if False:\n            return ("unknown",)'], ["mustnot"]),
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
     ["                or any(self.cs_iface_unresolved(k, m, caller_test, known) for k in keys):\n"],
     ["                or False:\n"],
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
     ['            here = {a[1] for a in al if a[0] == name} | set(gal.get(name, ()))'],
     ['            here = {a[1] for a in al if a[0] == name}'], ["rnd2"]),
    (_RF, "N2: a `global using` alias not recorded (_cs galias)",
     ["                self.cs[\"galias\"].append((local, spec))  # every file of the project sees it\n"],
     ["                pass\n"], ["rnd2"]),
    (_TC, "N2: two names of another last segment read as two types (an alias the pass cannot read)",
     ["            return True if a == b else None    # another last segment: either may be an alias\n"],
     ["            return True if a == b else False\n"], ["rnd2", "usings", "sound"]),
    (_TC, "N2: a name against a keyword / a project class read as two types",
     ["            return False if o[0] in (\"tpv\", \"mtp\") else None\n"],
     ["            return False\n"], ["rnd2", "usings", "sound"]),
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
     ["        if any(seg in _CS_KNOWN_NS.get(ns, ()) for ns in ua[-1] & ub[-1]):\n            return True\n"
      "        return None\n"],
     ["        if ua[-1] & ub[-1]:\n            return True\n        return None\n"], ["rnd2"]),
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
     ["        for sig in islot[:1]:                      # each signature the interface declares m with\n"],
     ["rnd2", "usings"]),
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
    (_TC, "D4d (sweep): an enclosing namespace no longer proves a qualified name (since R27: final's dx1 shape)",
     ["            if val == ns:\n"
      "                return True\n"],
     ["            if False:\n"
      "                return True\n"], ["sound", "final"]),
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
    # --- the residuals round (sections U / X, the MSBuild reading, the reader's new tables): each killed by a shape
    # of TREES["usings"] / TREES["resid"] / rnd2 or a csharp_msbuild row
    # --- (a) MSBuild (csharp_msbuild, read through the binder)
    (_MS, "a: the SDK's implicit usings never added (an ImplicitUsings project reads like one without)",
     ["            lists = [(USINGS_BASE, bool(known), \"sdk_unknown\")] + [(SDK_USINGS[s], True, \"\") for s in known]\n"],
     ["            lists = []\n"], ["usings", "msbuild"]),
    (_MS, "a: a <Using Remove> ignored (System.Threading kept for Ur: RemTicker.Tick read as IRem's implementer)",
     ['                    items[:] = keep\n'],
     ['                    pass\n'], ["usings", "msbuild"]),
    (_MS, "a: Directory.Build.targets not read (its `disable` lost: Ticker7.Tick read as ITick7's implementer)",
     ["    targets, above_t = _nearest(files, p.dir, _TARGETS)\n"], ["    targets, above_t = None, False\n"],
     ["usings", "msbuild"]),
    (_MS, "a5: an alias item under a condition the pass cannot read read as certain (Tk3: B13's slot proven)",
     ["        if len(targets) == 1 and None not in targets and all(c for _ns, c in defs):\n"],
     ["        if len(targets) == 1 and None not in targets:\n"], ["msbuild", "rnd2"]),
    # --- (a) the core's using levels
    (_TC, "U3 off (a compilation-unit set with an unseen part compared across .csproj groups: TickerQ.Tick)",
     ["    comparable = _cs_cu_comparable(la, lb)\n"], ["    comparable = True\n"], ["usings"]),
    (_TC, "U1 off (a known holder no longer decides levels that differ: ILogger in Microsoft.Extensions.Logging)",
     ["        if ha is not None and ha == hb:\n            return True\n"],
     ["        if False:\n            return True\n"], ["usings"]),
    (_TC, "U2 off (an interface's file-level using no longer covers a block using: Handler.Handle unknown)",
     ["    if (len(ea) <= len(eb) and _cs_coarsens(ea, eb)) or (len(eb) <= len(ea) and _cs_coarsens(eb, ea)):\n"
      "        return True\n"],
     ["    if False:\n        return True\n"], ["usings"]),
    (_TC, "a: transparent project namespaces kept in eff (a block's `using Um.Contracts;` blocks the coarsening)",
     ["        if not u:\n            return False\n        mk = (\"cstr\", u)\n"],
     ["        if True:\n            return False\n        mk = (\"cstr\", u)\n"], ["usings"]),
    # --- (c) enclosing external namespaces
    (_TC, "c: capable enclosing namespaces dropped from the using levels again (System.Timers.Timer read as "
          "System.Threading's: Timed.Use an implementer)",
     ["        elif _cs_capable(ent):\n            out.append(frozenset({ent[1]}))\n"],
     ["        elif False:\n            out.append(frozenset({ent[1]}))\n"], ["usings"]),
    (_TC, "c: a known member of an enclosing namespace no longer decides (ILogger in Microsoft.Extensions.Logging)",
     ["            if seg in _CS_KNOWN_NS.get(str(ent[1]), ()):\n                return str(ent[1])\n"],
     ["            if False:\n                return str(ent[1])\n"], ["usings"]),
    # --- (b) aliases by namespace declaration (the binder's one walk)
    (_RF, "b: a namespace block's alias read as the whole file's (UF finds the other block's alias before its "
          "compilation unit's using)",
     ["                self.falias[self.fstack[-1]].append((local, spec))"],
     ["                self.falias[0].append((local, spec))"], ["usings"]),
    (_RF, "b: a nearer block's usings never beat an outer alias (OrderUser's Ba.Models.Order read as the alias)",
     ['            got = usings(us, st, ppu, ppst, k - 1)\n            if got is not None:\n                return got, ""\n'],
     ['            got = None\n            if got is not None:\n                return got, ""\n'], ["usings"]),
    (_RF, "b: an external nearer using ignored by the alias walk (B5x's `using Ext.Stuff;` passed: the slot proven)",
     ['                if outer(k):\n                    return ("unknown",)                 # an external type'],
     ['                if False:\n                    return ("unknown",)                 # an external type'], ["usings"]),
    # --- (d) / (e) interface mapping
    (_RF, "d: explicit interface specifiers not recorded (RunA's `void IRunD.Run()` read as a private method)",
     ["            self.note_member(\"explicit\", name, line, self.typeref(next(iter(spec_n.named_children), None), "
      "mtps))\n"],
     ["            pass\n"], ["reader", "resid"]),
    (_TC, "d: explicit implementations read as ordinary members (RunB's override of the public twin picked)",
     ["        if ex is False:\n            mods = self.cs_member(x, mf, m, \"mods\")\n"],
     ["        if True:\n            mods = self.cs_member(x, mf, m, \"mods\")\n"], ["resid"]),
    (_TC, "d: an explicit implementation of another interface counted (RunC3's IOther3.Run)",
     ["            if f not in fam:\n                return (True, False)\n"],
     ["            if False:\n                return (True, False)\n"], ["resid"]),
    (_TC, "e: accessibility ignored again (a private / internal `new` Run read as IRunE's implementer)",
     ["            return (False, \"public\" in mods)\n"], ["            return (False, True)\n"], ["resid"]),
    # --- (f) nullable closed type arguments
    (_TC, "f: a nullable closed type argument read as unknown again (Cv8<int?> fans out to Cv8Int)",
     ["                if inner[1] in _CS_VALUE_KW:\n                    return (\"?\", inner)\n"],
     ["                if inner[1] in _CS_VALUE_KW:\n                    return None\n"], ["resid"]),
    (_TC, "f: a class's `?` read as Nullable (Cv8<Ord9?> skips Cv8Ord)",
     ["            if kinds <= {\"class\", \"abstract\", \"interface\"}:\n                return inner"],
     ["            if kinds <= {\"class\", \"abstract\", \"interface\"}:\n                return (\"?\", inner)"],
     ["resid"]),
    # --- (g) receiver type arguments
    (_TC, "g1: receiver type arguments never substituted (Repo11<Order>.Save(T) keeps SaveBase.Save as a rival)",
     ["        if t is not None and t.kind == \"generic\" and order and len(t.args) - 1 == len(order):\n"],
     ["        if False:\n"], ["resid"]),
    (_RF, "g2: conversion operators not recorded (an OrderC argument read as PROVEN inapplicable to Save(Cart))",
     ["                    self.cs[\"convops\"].append(ci)"], ["                    pass"], ["reader", "resid"]),
    (_TC, "g2: a chain declaring a conversion operator read as closed (OrderC's implicit Cart ignored: Store11.Save "
          "bound)",
     ["        return sup if closed and not (sup & conv) else None\n"], ["        return sup if closed else None\n"],
     ["resid"]),
    (_TC, "g2: the rival never bound (Repo11<Cart>.Save(Cart) inapplicable to an Order: SaveBase.Save unknown)",
     ["            if ovs == [\"rival\"]:                   # (g2) C# binds a base class's overload: typed to it, "
      "ADD only\n"],
     ["            if False:\n"], ["resid"]),
    (_TC, "g2: a rival binding REPLACEs (the hold lifted: the engine's Repo11.Save tuple removed)",
     ["                if vd.kind == \"typed\" and vd.why == \"rival\":\n"
      "                    virt_hold.append(\"rival\")"],
     ["                if False:\n                    virt_hold.append(\"rival\")"], ["resid"]),
    # --- the integration round: g2 binds only an overload the call site surely sees, of one node
    (_TC, "g2: an inaccessible base overload bound (Hid12's private / Prot12's protected / Dflt12's Put(Order) taken "
          "for Root12's Put(object), Roslyn's)",
     ["                acc = mods & _CS_ACCESS\n                if acc != {\"public\"} and not"],
     ["                acc = frozenset({\"public\"})\n                if acc != {\"public\"} and not"], ["resid"]),
    (_TC, "g2: a candidate in another node ignored (Part13's Put(object) bound for an int: Roslyn Put(IFormattable), "
          "the other partial part)",
     ["            if self.method_nodes(c, m, [mf for mf in defs if not self.cs_inapplicable(c, mf, m, descs)])[0] "
      "!= keys:\n"],
     ["            if False:\n"], ["resid"]),
    # --- the soundness round (section Y, the read_group rows): one per decision
    (_RF, "R1: a project type closes a namespace again (System.Timers.Timer unseen from `namespace System.Timers`)",
     ["            return self._ext_root(ns) or self._ext_related(ns)\n"],
     ["            return ns not in self.ns_info()[0] and (self._ext_root(ns) or self._ext_related(ns))\n"], ["sound"]),
    (_RF, "R2: package ids ignored (`using Acme.Core;` transparent though a package fills the namespace)",
     ["                self._nsinfo = (frozenset(type_ns), ext, False, frozenset(r for r in roots if r))\n"],
     ["                self._nsinfo = (frozenset(type_ns), ext, False, frozenset())\n"], ["sound"]),
    (_MS, "R2: package ids not read from the project files",
     ["        return out._replace(packages=ids) if ids else out\n"], ["        return out\n"], ["msbuild"]),
    (_TC, "R2: an external name no level may supply read as resolved alike (Yc.A.Clock / Yc.B.Clock one type)",
     ["    if not _cs_can_supply(la) or not _cs_can_supply(lb):\n        return None\n"],
     ["    if False:\n        return None\n"], ["sound"]),
    (_TC, "R2: U2's coarsening on empty levels again (a[-1] empty: a subset of anything)",
     ["    if not 0 < k <= n or not any(a) or not any(b):\n        return False\n"],
     ["    if not 0 < k <= n:\n        return False\n"], ["sound"]),
    (_TC, "R3: two equal x tuples are the same type again (Impl.Run proven from one alias name of two targets)",
     ["    if a != b:\n        return False\n    if len(a) == 4:\n"],
     ["    if a == b:\n        return True\n    if len(a) == 4:\n"], ["sound"]),
    (_TC, "R3: an alias target's text compared without its lookup scope (Core.Box<int> of two blocks one type)",
     ["                    return (\"x\", \".\".join(parts), target, self.cs_scope_levels(rel, scope))    # an alias of a "
      "generic /\n"],
     ["                    return (\"x\", \".\".join(parts), target, ((\"u\", frozenset()),))    # an alias of a "
      "generic /\n"], ["sound"]),
    (_TC, "R3: a qualifier that may be relative compared as one (Core.Tok of two namespaces one type)",
     ["            if a[3] is not None or b[3] is not None:"], ["            if False:"], ["sound"]),
    (_MS, "R4: a property value trimmed again (a multi-line <ImplicitUsings> read as `enable`)",
     ['        implicit = None if iu is None else (isinstance(iu, str) and (iu.lower() == "enable" or _boolish(iu) is True))\n'],
     ['        implicit = None if iu is None else (isinstance(iu, str) and (iu.strip().lower() == "enable" or _boolish(iu.strip()) is True))\n'],
     ["sound"]),
    (_MS, "R5: a wildcard <Using Remove> compared literally (System.Threading stays certain)",
     ['                if _WILD.search(raw):\n                    ops.append(("glob", _glob_head(_unesc(raw).lower())))'],
     ['                if False:\n                    ops.append(("glob", _glob_head(_unesc(raw).lower())))'], ["sound"]),
    (_MS, "R5: a wildcard <Using Include> read as a namespace",
     ["                if _WILD.search(ns):                 # a glob on files: what it adds depends on the directory\n"],
     ["                if False:                            # a glob on files: what it adds depends on the directory\n"],
     ["msbuild"]),
    (_RF, "R6: a file in no project's directory reads complete (TickerL.Tick proven against the linked interface)",
     ["        if not grp and projects and \"\" not in projects:\n            return True\n"],
     ["        if False:\n            return True\n"], ["sound"]),
    (_RF, "R6: a link into another project's directory ignored (TickerN.Tick proven)",
     ["        return bool(owners - {grp})\n"], ["        return False\n"], ["sound"]),
    (_MS, "R6: a <Compile Include> outside the project not recorded",
     ["            self.links.add(\"\")\n"], ["            pass\n"], ["msbuild"]),
    (_TC, "R7: two base-list paths: the first path's type arguments again (OneE.Save keyed for IRepo<Cart>)",
     ["                out = subst if out is None else {n: t for n, t in out.items() if n in subst and (\n"
      "                    t == subst[n] or _cs_tcmp(t, subst[n]) is True)}\n"],
     ["                out = subst if out is None else out\n"], ["sound"]),
    (_TC, "R7: an explicit specifier's type arguments unread (BothS.Run unknown for IRepoS<Cart>)",
     ["                    if r is False and not (i < len(variance) and variance[i]):\n"
      "                        return False\n"],
     ["                    if False:\n                        return False\n"], ["sound"]),
    (_MS, "R8: System.Net.Http added on .NET Framework (net48: HttpClient read as System.Net.Http's)",
     ["                    if ns == _NET_HTTP and fw == \"fx\":\n"], ["                    if False:\n"], ["sound"]),
    (_MS, "R8: System.Net.Http certain for an unknown framework (multi-target with a mix)",
     ["                    unknown = ns == _NET_HTTP and fw is None\n"], ["                    unknown = False\n"],
     ["sound"]),
    (_MS, "R9: Directory.Packages.props not walked (its `disable` lost: TickerQ.Tick proven)",
     ["    pkgs, above_k = _nearest(files, p.dir, _PACKAGES)\n"], ["    pkgs, above_k = None, False\n"], ["sound"]),
    (_MS, "R9: a Custom*Microsoft* import hook ignored (its file may remove any item)",
     ["any(k in _DIR_BUILD_SWITCHES or _IMPORT_HOOK.match(k) for k in self.v)"],
     ["any(k in _DIR_BUILD_SWITCHES for k in self.v)"], ["msbuild"]),
    (_RF, "R10: `ref readonly` read as `ref` (a value argument proven inapplicable: Store.Save bound)",
     ["            if how == \"ref\" and \"readonly\" in mods:\n"], ["            if False:\n"], ["sound"]),
    (_TC, "R10: `ref readonly` proven inapplicable (RcUser: Store4.Save for a conversion to its Cart)",
     ["            if p[1] == \"ref readonly\":\n                continue\n"
      "            if self.cs_not_fits(a, self.cs_param_type(ci, rel_d, p[0]) or self.cs_tp_param(ci, mf, m, i)):\n"],
     ["            if p[1] == \"ref readonly\":\n                return True\n"
      "            if self.cs_not_fits(a, self.cs_param_type(ci, rel_d, p[0]) or self.cs_tp_param(ci, mf, m, i)):\n"],
     ["sound"]),
    (_TC, "R11: a base-list head outside the alias walk again (UB : Repo read as another block's alias)",
     ["                               extra_shadow=ci.facts.type_params, ctx=self.outer_key(key), akey=key)\n"],
     ["                               extra_shadow=ci.facts.type_params, ctx=self.outer_key(key))\n"], ["sound"]),
    (_TC, "R12: an explicit-only implementation a member of the lookup again (DerE.Save for `_e.Save(o)`)",
     ["        if ex is not False:\n            return True\n        mods = self.cs_member(ci, mf, m, \"mods\")\n"
      "        if not isinstance(mods, frozenset):\n            return False\n        acc = mods & _CS_ACCESS\n"
      "        if \"public\" in acc"],
     ["        if ex is not False:\n            return False\n        mods = self.cs_member(ci, mf, m, \"mods\")\n"
      "        if not isinstance(mods, frozenset):\n            return False\n        acc = mods & _CS_ACCESS\n"
      "        if \"public\" in acc"], ["sound"]),
    (_TC, "R12: a private member seen from outside its class (DerP.Keep for `_p.Keep(o)`)",
     ["        if acc <= {\"private\"}:\n            return True\n"], ["        if acc <= {\"private\"}:\n            return False\n"],
     ["sound"]),
    (_TC, "R12: a private member skipped inside its own class too (DerP.Keep from DerP.In)",
     ["        if ci.key in place:\n            return False\n        if acc <= {\"private\"}:\n"],
     ["        if False:\n            return False\n        if acc <= {\"private\"}:\n"], ["sound"]),
    (_TC, "R12: protected read as public (DerG.Prot from an unrelated class)",
     ["        if acc == {\"protected\"}:\n            return out_of_reach\n"],
     ["        if acc == {\"protected\"}:\n            return False\n"], ["sound"]),
    (_TC, "R12: internal read as public (DerI.Intl from another project)",
     ["        if acc == {\"internal\"}:\n            return self.cs_other_assembly(rel, ci, mf)\n"],
     ["        if acc == {\"internal\"}:\n            return False\n"], ["sound"]),
    (_TC, "R12: the Directory.Packages.props above a project unread (a friend named there)",
     ['                                                                "Directory.Packages.props")) if p.is_file())\n'],
     ['                                                                )) if p.is_file())\n'], ["sound"]),
    (_TC, "R12: a project file that imports another still proves the assembly's name (FriendVB.Go: BaseVB.Intl)",
     ['                if t is None or _cs_imports(t) or any("$" in v or not v.strip() for v in written):\n'],
     ['                if t is None or any("$" in v or not v.strip() for v in written):\n'], ["sound"]),
    (_TC, "R12: the file DirectoryPackagesPropsPath names read as no import (FriendVC.Go: BaseVC.Intl)",
     ['    return "<Import" in text or "DirectoryPackagesPropsPath" in text\n'],
     ['    return "<Import" in text\n'], ["sound"]),
    (_TC, "R12: the files a project links in hold no friend (FriendVD.Go: BaseVD.Intl)",
     ['            texts.extend(self.cs_linked_texts(rel))\n'], ['            pass\n'], ["sound"]),
    (_TC, "R12: a friend name built by concatenation read as its first literal (FriendVH.Go: BaseVH.Intl)",
     ['                            or (tail.startswith("+") and "," not in name.group(1)):   # "Yn" + Tail: the name '
      'goes on\n'],
     ['                            or False:\n'], ["sound"]),
    (_TC, "a `global using` in a linked file leaves the reading complete (TickerX proven, TickBaseX's tuple removed)",
     ['                if t is None or _CS_GLOBAL_USING.search(t):\n                    complete = False                # ('],
     ['                if t is None:\n                    complete = False                # ('], ["sound"]),
    (_TC, "a link to a file the tree does not hold read as saying nothing (TickerZm proven, TickBaseZm's tuple "
          "removed)",
     ['                if t is None or _CS_GLOBAL_USING.search(t):\n                    complete = False  '],
     ['                if t is not None and _CS_GLOBAL_USING.search(t):\n                    complete = False  '],
     ["sound"]),
    (_RF, "a linked file the reader did not parse read as saying nothing (vendor/: TickerZv proven)",
     ['        hits = _GLOBAL_USING.findall(text)\n'], ['        hits = []\n'], ["sound"]),
    (_RF, "cu_globals: a file some other project may compile sees its group's `global using`s as certain (group \"\" "
          "pools Zf / Zi / Zj's Common files: TickerL / TickerO / TickerP proven)",
     ['        if not self._compiled_elsewhere(key, grp):\n'], ['        if True:\n'], ["sound"]),
    (_RF, "cu_globals: what a project links in not folded into its group (ZjA's linked alias: TickerJ proven)",
     ['                for r in self.linked_in(g)[0]:          # a file it links in (maybe under a condition): what '
      'its\n'],
     ['                for r in ():\n'], ["sound"]),
    (_RF, "cu_globals: the projects whose links reach a file not read (TickM's App: TickerM no longer proven)",
     ['        views = [view(g) for g in sorted(owners)] or [view(grp)]\n'
      '        sure = frozenset.intersection(*[v[0] for v in views]) if owners else frozenset()\n'],
     ['        owners = frozenset()\n        views = [view(grp)]\n        sure = frozenset()\n'], ["sound"]),
    (_TC, "cs_file_tables: a file some other project may compile reads its group's tables (TickerL / O / P proven)",
     ['        if not self.cs_cu_reading(rel)[1]:\n            return self.cs_file_tables(rel, self.memo[mk])\n'],
     ['        if False:\n            return self.cs_file_tables(rel, self.memo[mk])\n'], ["sound"]),
    (_RF, "linked_in: a directory link holds no file (TickerI proven)",
     ['                        for cur, dirs, names in os.walk(path):\n'],
     ['                        for cur, dirs, names in ():\n'], ["sound"]),
    (_RF, "linked_in: a build's output is walked (the graph would follow whether the tree was built)",
     ['                            dirs[:] = sorted(d for d in dirs if d.lower() not in _NO_WALK_DIRS)\n'],
     ['                            dirs.sort()\n'], ["sound"]),
    (_RF, "linked_in: a link with no place reads as whole",
     ['                    if not lk:\n                        whole = False\n'],
     ['                    if not lk:\n                        continue\n'], ["sound"]),
    (_RF, "linked_in: a link to no file reads as whole",
     ['                        found.add(lk)\n                    else:\n                        whole = False\n'],
     ['                        found.add(lk)\n'], ["sound"]),
    (_MS, "R6 a link out of the tree not recorded (the project then reads as whole)",
     ['            return ".."                       # out of the tree: a file DeepMap never sees (CsBinder.linked_in)\n'],
     ['            return None\n'], ["msbuild", "sound"]),
    (_TC, "R12: a friend assembly's internal read as out of reach (FriendUser.Go: BaseS.Intl)",
     ["        return friends is not None and bool(names) and not friends & names\n"],
     ["        return True\n"], ["sound"]),
    (_MS, "R16: no cap on a property value (a 70 KB literal read as a value)",
     ["_MAX_VALUE = 64 * 1024            # a property value's length"],
     ["_MAX_VALUE = 1 << 40              # a property value's length"], ["msbuild"]),
    (_TC, "R20: Nullable<int> closing a receiver unread (Cv0<Nullable<int>> fans out to Cv0Int)",
     ["                and _cs_is_nullable(self.cs_bcl_name(t.args[0], rel, si, ctx, akey)):\n"],
     ["                and False:\n"], ["sound"]),
    (_TC, "R20: Int32 closing a receiver unread (ICounter<Int32> fans out to LongCounter)",
     ["        n = self.cs_bcl_name(t, rel, si, ctx, akey)\n"], ["        n = None\n"], ["sound"]),
    # ---- W4 (the last soundness round): one mutant per decision R22-R39 (and the round's fixes), each verified RED
    # on a GREEN build before it joined the table (section L, the U msbuild rows, Y for the rooted-qualifier rule)
    (_TC, "R22 a method's own generic arity left out of its signature (Save<T>(Order) reads as Save(Order))",
     ['        return sp, ((("#arity", ar), "#"),)'],
     ['        return sp, ()'], ['final']),
    (_TC, 'R22 the `params` parameter left out of the signature',
     ['            (self.cs_sig_norm(t, rel_d, ci.key, subst, scope=ci.key), "") for t in tail[0]) + tail[1]'],
     ['            (self.cs_sig_norm(t, rel_d, ci.key, subst, scope=ci.key), "") for t in ()) + tail[1]'], ['final']),
    (_TC, 'R22 a generic definition read as known to fit a call (its type arguments may not be inferable)',
     ['        return ar is _NOINFO or (ar == 0 and not isinstance(ar, bool))'],
     ['        return True'], ['final']),
    (_TC, "R23 a level's `using static` types dropped from the using levels compared",
     ['    return us | frozenset(("static", s) for s in _cs_eff(ent) - us)'],
     ['    return us'], ['final']),
    (_TC, 'R24 a lookup order holding a preprocessor-branch level compared as complete',
     ['        and lev[-1][0] == "u" and not any(e[0] == "p" and e[1] for e in lev)'],
     ['        and lev[-1][0] == "u"'], ['final']),
    (_TC, 'R24 a `global using` in a branch read as certain for its group',
     ['                    if ok and (ing is None or ppu is None or j in ppu):'],
     ['                    if ok and False:'], ['final']),
    (_TC, "R25 a file its project's <Compile Remove> takes out still counted for the group",
     ['                if ing is False:                   # none of its compilations; one it may take out: possible / unknown'],
     ['                if False:'], ['final']),
    (_TC, 'R26 the same-file rule binds a class of any namespace declaration of the file',
     ['            cls_idx = [i for i in cls_idx if self.cs_same_decl(rel, i, place)]'],
     ['            cls_idx = cls_idx'], ['final']),
    (_TC, 'R30 no arity passed to the binder (every hit unknown: a recall mutant)',
     ['        kw = {"arity": arity} if arity is not _NOINFO and _takes_arity(fn) else {}'],
     ['        kw = {}'], ['final']),
    (_TC, 'R31 a partial part two projects compile merged as one type (never marked open)',
     ['                    self.classes[k].ambiguous = True'],
     ['                    pass'], ['final']),
    (_TC, "R37 / R45 two complete readings of two groups compared whatever their package sets",
     ["    return isinstance(pa, frozenset) and isinstance(pb, frozenset) and not pa and not pb"],
     ["    return True"], ["final"]),
    (_RF, 'R32 a package id sharing the root only by prefix no longer shares it',
     ['        return any(p.startswith(low) or low.startswith(p) for p in self._nsinfo[3])'],
     ['        return any(p == low for p in self._nsinfo[3])'], ['final']),
    (_RF, "R32 a root no project owns read as the project's own",
     ['        if self._nsinfo[2] or root not in self._owned:'],
     ['        if self._nsinfo[2]:'], ['final']),
    (_RF, "R23 a project type's nested type found through a block's `using static` read as absent (passed on outward)",
     ['                            return "found"'],
     ['                            return "none"'], ['final']),
    (_RF, 'R24 a directive inside an #if / #elif / #else branch read as certain',
     ['                self.in_pp += 1                          # R24: a directive here is possible, never certain'],
     ['                self.in_pp += 0'], ['final']),
    (_RF, 'R27 a capable enclosing namespace passes a name it does not declare on to the outer usings',
     ['        if ns and self.capable(ns):'],
     ['        if False:'], ['final']),
    (_RF, 'R28 a `file` type visible from every file',
     ['        local = [e for e in ents if e[4]]'],
     ['        local = []'], ['final']),
    (_RF, "R29 one namespace declaration's lookup answers for every declaration of the file",
     ['        return first if all(a == first for a in answers) else self.T.RES_UNKNOWN'],
     ['        return first'], ['final']),
    (_RF, 'R30 the type index ignores generic arity (`Result` finds `Result<T>`)',
     ['        ents = idx[0].get((fq, arity))'],
     ['        ents = [e for (n, _a), es in idx[0].items() if n == fq for e in es]'], ['final']),
    (_RF, 'R31 partial parts grouped by directory, not by the compiling project',
     ['                            for g in sorted(self._compilers(rel)):'],
     ['                            for g in [self.group_of(rel)]:'], ['final']),
    (_MS, 'R33 a Remove / Exclude / Update spec written as a path read as a name',
     ['    if _PATH_PROPS.search(str(raw or "")):', '    for one in [val] + _items(val):'],
     ['    if False:', '    for one in ():'], ['msbuild', 'final']),
    (_MS, 'R34 two boolean-like operands compared as text',
     ['        if ba is not None and bb is not None:         # R34: two boolean-like operands compare as booleans'],
     ['        if False:'], ['msbuild', 'final']),
    (_MS, 'R35 a Directory.Build.rsp never read as a taint',
     ['        hooks = {"import_unread": hooked_imp, "rsp": rsp, "local_property": self.local_prop,'],
     ['        hooks = {"import_unread": hooked_imp, "rsp": False, "local_property": self.local_prop,'], ['msbuild', 'final']),
    (_MS, 'R36 %XX decoded before the split on `;`',
     ['            for raw in _split(spec):\n                if _WILD.search(raw):\n'],
     ['            for raw in _split(_unesc(spec)):\n                if _WILD.search(raw):\n'], ['msbuild']),
    (_MS, 'R38 Configuration / Platform pinned to Release / AnyCPU (the old model)',
     ['_GLOBALS = {"configuration": None, "platform": None}'],
     ['_GLOBALS = {"configuration": "Release", "platform": "AnyCPU"}'], ['msbuild', 'final']),
    (_MS, 'R39 a Directory.Build.* found only in another letter case read as found',
     ['        return [hit if posixpath.basename(hit) == _SPELLED[lname] else _CASE]'],
     ['        return [hit]'], ['msbuild']),
    (_MS, 'R39 a <Using Remove> matching only in another letter case removes the item (certain)',
     ['                        if x[0] is not None and x[0] in names:'],
     ['                        if x[0] is not None and x[0].lower() in low:'], ['msbuild']),
    (_MS, 'R37 the package signature left empty (two package sets always equal)',
     ['            out.add((pid.lower(), v, assets))'],
     ['            pass'], ['msbuild', 'final']),
    (_RF, "R22 the reader records every method's own generic arity as 0",
     ['        self.note_member("marity", name, line, len(morder))'],
     ['        self.note_member("marity", name, line, 0)'], ['final']),
    (_RF, 'R22 the reader drops the `params` parameter from the signature table',
     ['        self.note_member("msigp", name, line, () if tail is None else (self.sigt(tail[0], tps, morder),))'],
     ['        self.note_member("msigp", name, line, ())'], ['final']),
    (_RF, "R25 in_group: a file its project's readable <Compile Remove> takes out read as compiled",
     ['                    got = _sibling("csharp_msbuild").compile_state(self.msbuild_raw(grp), grp, key)'],
     ['                    got = True'], ['final']),
    (_RF, "an inner block's alias target resolved without the outer blocks' usings (the global Foo.Bar)",
     ["            if not level or level in outer:         # an enclosing declaration's directives may supply `head`"],
     ['            if not level:'], ['final']),
    (_MS, 'a `global::` prefix on a <Using Include> read as the plain namespace',
     ['                if ns.startswith("global::"):        # the task writes'],
     ['                if False:        # the task writes'], ['msbuild']),
    (_TC, 'R32 x the rooted-qualifier rule: every capable qualifier read as rooted (`Core.Tok` in two blocks: one type)',
     ['and not self.cs_rooted(rel, ".".join(got[2])):'],
     ['and not self.cs_capable(rel, ".".join(got[2])):'], ['sound']),
    (_TC, 'R30 in the nested-type lookup: a nested type of another arity matches (`Entry` binds Repo.Entry<T>)',
     ['                ok = True if arity is _NOINFO else self.cs_arity_ok(rel, j, arity)'],
     ['                ok = True'], ['final']),
    # the integrator's shadow rule (a namespace declaration's non-transparent using may import a TYPE of the name)
    (_RF, "shadow: the alias walk passes a block's non-transparent using (ImplE1.Run(Timer) proven, RunBaseE1.Run gone)",
     ['                shadow.append(k)                        # ... and any project type further out'],
     ['                pass'], ['final']),
    (_RF, "shadow: resolve_free passes a block's non-transparent using (J1's Timer read as the parent's Gs.Timer)",
     ['            if not shadow and any(not self.transparent(u) for u in list(us) + list(ppu)):\n'
      '                shadow.append(ns)'],
     ['            if False:\n                shadow.append(ns)'], ['final']),
    (_RF, "shadow: the alias walk hides a project NAMESPACE too (qualified base lists in SA1200 blocks: no proof)",
     ['            return (got is not None and got[0] == "member" and bool(shadow)\n'
      '                    and getattr(got[1], "kind", "") == "symbol")'],
     ['            return got is not None and got[0] == "member" and bool(shadow)'], ['sound', 'rnd2']),
    (_RF, "shadow: resolve_free hides a project NAMESPACE too (J1's `Gs` qualifier unknown)",
     ['                return T.RES_UNKNOWN if shadow and getattr(got[1], "kind", "") == "symbol" else got[1]'],
     ['                return T.RES_UNKNOWN if shadow else got[1]'], ['final']),
    (_RF, "shadow: resolve_free keeps an enclosing block's alias behind a block's non-transparent using (J5's Tm)",
     ['                    if len(specs) != 1 or shadow:'],
     ['                    if len(specs) != 1:'], ['final']),
    (_RF, "shadow: resolve_free keeps the compilation unit's alias behind a block's non-transparent using (J3's Tm)",
     ['            if any(a[0] == name for a in ppa) or (here and (arity is None or shadow)):'],
     ['            if any(a[0] == name for a in ppa) or (here and arity is None):'], ['final']),
    # the closing round (W5: R42-R45), each verified RED before insertion
    (_TC, "R42 a class with an unresolved base never read as a possible implementer / override (cs_unres_chains "
          "empty)",
     ['                if any(e[0] == "unres" and self.cs_unres_proj(e[1]) for e in ents):\n'
      '                    out[k] = tuple(e[1] for e in ents if e[0] == "class")'],
     ['                if False:\n'
      '                    out[k] = tuple(e[1] for e in ents if e[0] == "class")'], ["close"]),
    (_TC, "R42 the overload rival walk passes an unresolved base like an external one (TDerE3.ArmE3 bound)",
     ['                return "rival"                     # R42: an unresolved base may be any project class: '
      'unknown'],
     ["                break"], ["close"]),
    (_TC, "R43 a cross-kind pair with an unplaced head or element proven two types (int? / an unplaced "
          "Nullable<int>)",
     ["        if _cs_has_unknown(a) or _cs_has_unknown(b):"], ["        if False:"], ["close"]),
    (_RF, "R44 every using inside a namespace declaration read as absolute (c26b's Kg the global one)",
     ["        if fidx == 0 or absolute:\n            return t, frozenset({t})"],
     ["        if True:\n            return t, frozenset({t})"], ["close"]),
    (_RF, "R44 `global::` stripped inside a namespace declaration (c27b's alias read relative: Ql.Kl.Order)",
     ['        if spec.startswith("global::") and (glob or f == 0):\n'],
     ['        if spec.startswith("global::"):\n'], ["close"]),
    (_RF, "R44 a capable level read as holding no external namespace (`using Ku;` in Xu proven the global Ku)",
     ["            if self.capable(level):\n                unknown = True"],
     ["            if False:\n                unknown = True"], ["close"]),
    (_TC, "R44 (core) using levels read as the written texts (`using Ev;` of two meanings read as one: "
          "CartHandlerV keyed)",
     ["            decl = self.cs_meant_decls(rel, tb, idxs, decl)"], ["            pass"], ["close"]),
    (_TC, "R45 two readings with equal non-empty package signatures compared (R37's rule, measured false: k01 / "
          "k02)",
     ["and not pa and not pb"], ["and pa == pb"], ["final", "close"]),
    # the LEAD decisions L1 / L2 of the closing round, each verified RED before insertion
    (_TC, "L2 an enclosing type's member (declared, inherited, a field) ignored: the `using static` member "
          "REPLACEs (InnerT.Go2's OuterT.RunT removed)",
     ["        if isinstance(cix, int) and self.cs_outer_holds(rel, cix, site.member):\n"],
     ["        if False:\n"], ["close"]),
    (_TC, "L2 every `using static` of the file read wherever the call stands (PlainE.Go3 also sees the first "
          "block's HelpersE: unknown)",
     ["                    imports = list(fn(rel, cix) or ())     # L2: the directives in effect at the "
      "caller's class\n"],
     ["                    imports = list(fn(rel) or ())\n"], ["close"]),
    (_TC, "L1 every unresolved base's candidates read as ANY project type (the final tree's interface REPLACE "
          "lost)",
     ["            if self.cs_unres_types(nm) is None:\n                out = None\n"],
     ["            if True:\n                out = None\n"], ["final"]),
    (_TC, "L1 an unresolved base's candidates without their chains (UserC.Go: IAliC is on BaseC's chain, "
          "ImplC.AlC removed)",
     ['                    if e[0] == "class":\n                        out.add(e[1])\n'
      '                    elif e[0] == "unres" and self.cs_unres_proj(e[1]) and str(e[1] or "") not in names:\n'],
     ["                    if False:\n                        out.add(e[1])\n                    elif False:\n"],
     ["close"]),
    (_TC, "L1 alias targets never followed (UserA.Go: A2 = A1 = IAliA, BaseA.AlA removed)",
     ["                    work.append(seg)\n"], ["                    pass\n"], ["close"]),
    (_TC, "L1 member_open never reads a candidate class's members (CtlK.Get: the namespace's HelperK.RunK keyed)",
     ["                if x in c.facts.fields or x in c.facts.methods or self.cs_nested(c.key, x) is not None:\n"
      "                    return True\n            if self.member_open(ck, x):\n"],
     ["                if False:\n                    return True\n            if self.member_open(ck, x):\n"],
     ["close"]),
    (_TC, "L1 a candidate's overloads never read as rivals (TUserE3.Go: the derived ArmE3 bound)",
     ["                    continue                       # another arity, or PROVEN the hit's own signature "
      "(hidden by it)\n                return True\n"],
     ["                    continue                       # another arity, or PROVEN the hit's own signature "
      "(hidden by it)\n                continue\n"], ["close"]),
    (_RF, "L2 (binder) static_imports ignores the class index: every `using static` of the file (PlainE.Go3 also "
          "sees the first block's HelpersE: unknown)",
     ["                    chain = got or chain\n"], ["                    pass\n"], ["close"]),
    # R47 (W5 fix: R42's audit completed), each verified RED before insertion
    (_TC, "R47 an interface a class lists never read past cs_lists (User47a-d: Base47*.Al47* keyed, Sub47*.Al47* "
          "removed)",
     ["                    and self.cs_relist_through(b[1], key):\n"],
     ["                    and False:\n"], ["close"]),
    (_TC, "R47 (b) an interface listed past an external or unresolved entry never read (User47c / User47d: "
          "Sub47*.Al47* removed)",
     ["            elif opaque and e[1] == key:\n"],
     ["            elif False:\n"], ["close"]),
    (_TC, "R47 (a) an unresolved entry on a listed interface's chain never read as reaching key (User47a / "
          "User47b: Sub47*.Al47* removed)",
     ['                if e[0] == "unres" and self.cs_unres_may_reach(e[1], key):\n'
      "                    return True\n            elif opaque"],
     ["                if False:\n                    return True\n            elif opaque"], ["close"]),
]


def _run_sections(names, buckets):
    _RUNS.clear()
    for n in names:
        {"collision": sec_collision, "reader": sec_reader, "shapes": lambda: sec_shapes(buckets),
         "mustnot": lambda: sec_mustnot(buckets), "members": lambda: sec_members(buckets),
         "review": lambda: sec_review(buckets), "recheck": lambda: sec_recheck(buckets),
         "sigs": lambda: sec_sigs(buckets),
         "rnd2": lambda: sec_rnd2(buckets),
         "usings": lambda: sec_usings(buckets), "resid": lambda: sec_resid(buckets), "msbuild": sec_msbuild,
         "sound": lambda: sec_sound(buckets),
         "final": lambda: sec_final(buckets),
         "close": lambda: sec_close(buckets),
         "ctors": lambda: sec_ctor_scopes(buckets),
         "flags": lambda: sec_flags(buckets)}[n]()
    _RUNS.clear()


@contextlib.contextmanager
def mutant(modname: str, olds: list, news: list):
    """Module `modname` replaced in sys.modules by a copy of its source with each `old` -> `new` (each must occur
    exactly once); restored after. A module the tree lacks (build/csharp_msbuild.py before the residuals round) is a
    mutation that does not apply, never a crash."""
    path = BUILD / f"{modname}.py"
    if not path.is_file():
        raise AssertionError(f"mutation target build/{modname}.py does not exist")
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
            sec_usings(buckets)
            sec_resid(buckets)
            sec_sound(buckets)
            sec_final(buckets)
            sec_close(buckets)
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
