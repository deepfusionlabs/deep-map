#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""C# declarations the engine drops (plan phase 3): computed properties, constructors and, on graphify 0.8.39,
record / struct / enum owners. No Neo4j, no network, no spend; the real-engine section runs the installed graphify
under a temporary DEEPMAP_HOME, so nothing is written to ~/.deepmap.

Neither engine makes a node of a C# property or a constructor (`function_types = {method_declaration}`), so the
order total a field user searched for (`ShoppingCartDto.ItemsCalculatedPrice => Items.Sum(...)`) was in no indexed
text. graphify 0.8.39 also has no owner for a record, record struct, struct or enum: their methods were owner-less
:Function nodes, two of one name in a file collapsed into one, and enums vanished (0.8.51 makes them classes).
map_graph now adds, default ON (`DEEPMAP_CSHARP_MEMBERS=0` and a missing tree-sitter-c-sharp give exactly the old
output), read with the engine's own grammar from each .cs file the engine gave a file node:
  * a computed property (an `=>` body, or an accessor with a body) -> :Method `Owner.Prop`, summary "...: property";
  * one constructor per owner name in a file -> :Method `Owner.Owner` (Java parity), summary "...: method": the FIRST
    INSTANCE constructor in source order, a static constructor only when the owner has no instance one;
  * both carry the private `call_by_name: False` (never pushed): edge_resolve's name index skips them, so `Name` /
    `Order` never fan out to them by name; a marked CallKey still binds them;
  * a record / struct / enum the engine has no node of gets the engine's own type node of that engine id in the
    file when there is one (the engine collapses same-named declarations of a file, namespaces included, into the
    first), else (0.8.39 only, then) a synthetic :Class owner, one per engine id; its methods are re-owned
    (`Owner.method` :Method nodes) and a method a collision swallowed recovered, so 0.8.39 maps like 0.8.51.

Sections:
  A  exact facts per engine bucket: the new nodes (label, name, file, line, summary), their keys and the guard flag,
     what 0.8.39 gains and loses, what must never be a node (auto / init properties, indexers, operators, events,
     destructors, positional records, enum members), no CALLS from a new node, the stats counter and its log line.
  B  DEEPMAP_CSHARP_MEMBERS=0 (every OFF word), a missing C# grammar and the pure mapping (no root) give the
     PRE-CHANGE map_graph output byte for byte, in three flag modes (frozen: tests/fixtures/csharp_members/pre.json).
  C  idempotence: 0.8.51 gets no synthetic owner; a member the engine already emits is not added again; a member
     run twice through the same reader is unchanged.
  D  the name guard through edge_resolve (build_name_index, Resolver) and sync._resolve_calls: every CALLS edge and
     grade and every typed edge is what it was without the new nodes, a cross-language `Name` / `Order` call never
     reaches them (and does without the guard: the teeth), a CallKey to one is honoured.
  E  a .cs file the engine gave no node gets nothing, and is never read.
  F  after the C# recognizer's shells are merged in, the 0.8.39 and 0.8.51 graphs hold the same C# nodes.
  G  the installed engine's extraction of the tree equals its bucket's captured fixture.
  H  one tree-sitter parse per engine .cs file per map_graph, none for any other file.
  I  same-named types and the constructor choice, per engine: one :Class per engine id of a name in a file (two
     nested `enum Status` / `struct Line` in two classes, two `struct P` in two namespaces, a struct named like an
     earlier class, a namespace named like its struct; a reference node the engine gave the file never stands in for
     a declaration, even at the declaration's own line; a function named like an enum never does either), and the
     `Owner.Owner` line (static then
     instance, static only, instance only, instance then static, and a static and an instance constructor split
     over two same-named structs of one file).

Fixtures (tests/fixtures/csharp_members/):
  0839.json / 0851.json  the REAL graph.json each engine wrote for TREE (`--capture` re-writes the installed engine's
                         bucket; run it under both engines after editing TREE).
  pre.json               map_graph of the PRE-CHANGE build/graphify_extract.py on both captures, in PRE_MODES, made
                         once with `python tests/csharp_members_gates.py --freeze <dir holding the pre-change module>`.
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build"
FX = REPO / "tests" / "fixtures" / "csharp_members"
sys.path.insert(0, str(BUILD))

import graphify_extract as GX  # noqa: E402

BUCKETS = {"0.8.39": "0839", "0.8.51": "0851"}
_HAS_ENGINE = importlib.util.find_spec("graphify") is not None

_PASS = _FAIL = 0


def check(label: str, cond: bool, detail="") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {label}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}{(' — ' + str(detail)[:900]) if detail != '' else ''}")


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
                os.environ[k] = str(v)
        yield
    finally:
        for k in [k for k in os.environ if k.startswith("DEEPMAP_")]:
            os.environ.pop(k, None)
        os.environ.update(saved)


# ================================================================================================
# The tree (captured on graphify 0.8.39 and 0.8.51)
# ================================================================================================
DTO = "Shop/Dtos/ShoppingCartDto.cs"
ORDER = "Shop/Models/Order.cs"
VALUES = "Shop/Models/Values.cs"
SVC = "Shop/Services/CartService.cs"
SVC2 = "Shop/Services/CartService.Pricing.cs"
NESTED = "Shop/Models/Nested.cs"
POINTS = "Shop/Geo/Points.cs"
PAIRS = "Shop/Models/Pairs.cs"
BOX = "Shop/Models/Boxes.cs"
UNITS = "Shop/Models/Units.cs"
CTORS = "Shop/Services/Ctors.cs"
LINKS = "Shop/Models/Links.cs"
NG = "web/src/app/cart/cart.component.ts"
PY = "tools/report.py"

TREE = {
    # The field report's file: the order total is a computed property; a file-scoped namespace, attributes on a
    # property and on a multi-line constructor, auto and init properties, a getter with a block body and one with
    # an arrow body, a second constructor (only the first is a node).
    DTO: """\
using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json.Serialization;

namespace Shop.Dtos;

[Serializable]
public class ShoppingCartDto
{
    private readonly string _name;

    public List<CartItemDto> Items { get; set; } = new();
    public int Version { get; init; }

    public decimal ItemsCalculatedPrice => Items.Sum(i => i.Price * i.Quantity);

    [Obsolete("use ItemsCalculatedPrice")]
    public decimal LegacyTotal
    {
        get
        {
            return Items.Sum(i => i.Price);
        }
    }

    public string Name { get => _name; }

    [JsonConstructor]
    public ShoppingCartDto(
        string name,
        List<CartItemDto> items)
    {
        _name = name;
        Items = items;
    }

    public ShoppingCartDto() : this("cart", new List<CartItemDto>())
    {
    }

    public int CountItems() => Items.Count;
}

public class CartItemDto
{
    public decimal Price { get; set; }
    public int Quantity { get; set; }
    public decimal LineTotal => Price * Quantity;
}
""",
    # A block namespace; an interface with a default property; a static constructor before an instance one; an
    # indexer, an operator, two events and a destructor (never nodes); #if / #else (the first branch's property is
    # the node, and a method inside #if stays the engine's owner-less function); a nested class; a generic class.
    ORDER: """\
using System;

namespace Shop.Models
{
    public interface IPriced
    {
        decimal Price { get; }
        decimal PriceWithTax => Price * 1.2m;
    }

    public class Order : IPriced
    {
        private readonly decimal[] _lines = new decimal[4];
        public static readonly string Kind;

        static Order()
        {
            Kind = "order";
        }

        public Order(decimal first)
        {
            _lines[0] = first;
        }

        public decimal Price => _lines[0];

        public decimal this[int i] => _lines[i];

        public static Order operator +(Order a, Order b) => a;

        public event EventHandler Changed;

        public event EventHandler Saved { add { } remove { } }

        ~Order()
        {
        }

#if DEBUG
        public string DebugView => "order";

        public void Dump()
        {
        }
#else
        public string DebugView => "";
#endif

        public class Line
        {
            public int Qty { get; set; }
            public bool IsEmpty => Qty == 0;
            public Line(int qty) { Qty = qty; }
        }

        public decimal Total() => Price;
    }

    public class Repo<T> where T : class
    {
        public int Size => 0;

        public Repo()
        {
        }

        public T Find(int id) => default;
    }
}
""",
    # graphify 0.8.39 has no owner for these: a record and a record struct that both define Twice() (one id there),
    # a struct with a constructor, a computed property and a method calling another, an enum.
    VALUES: """\
namespace Shop.Models;

public record OrderRec(int Id)
{
    public int Twice() => Id * 2;
    public int Doubled => Id * 2;
}

public record struct Point(int X)
{
    public int Twice() => X * 2;
}

public struct Money
{
    public decimal Amount;

    public Money(decimal amount)
    {
        Amount = amount;
    }

    public decimal Half() => Amount / 2;
    public decimal Quarter() => Half() / 2;
    public bool IsZero => Amount == 0;
}

public enum Color
{
    Red,
    Green
}
""",
    # A partial class across two files: each part is its own :Class, with its own first constructor.
    SVC: """\
namespace Shop.Services;

public partial class CartService
{
    private readonly ICartHelper _helper;

    public CartService(ICartHelper helper)
    {
        _helper = helper;
    }

    public int Count => _helper.Build();
}

public interface ICartHelper
{
    int Build();
}
""",
    SVC2: """\
namespace Shop.Services;

public partial class CartService
{
    public decimal Discount => 0.1m;

    public CartService() : this(null)
    {
    }

    public decimal Apply(decimal v) => v * (1 - Discount);
}
""",
    # Same-named nested types in two classes of one file: graphify 0.8.51 makes ONE node per engine id, at the first
    # declaration (Status at its first line, Line at its first line, one Line.Double), and so must the supplement on
    # both engines. The field `Line _first` gives 0.8.39 a `references` node labelled Line in this file: it is not a
    # declaration and never stands in for the struct.
    NESTED: """\
namespace Shop.Models;

public class Basket
{
    private Line _first;

    public enum Status { Open, Closed }

    public struct Line
    {
        public int Qty;
        public int Double() => Qty * 2;
        public bool Empty => Qty == 0;
        public Line(int q) { Qty = q; }
    }
}

public class Invoice
{
    public enum Status { Draft, Paid }

    public struct Line
    {
        public decimal Amount;
        public decimal Double() => Amount * 2;
        public bool Empty => Amount == 0;
        public Line(decimal a) { Amount = a; }
    }
}
""",
    # Two `struct P` in two namespaces of one file (one engine id): one P, and its constructor node is the instance
    # constructor of the second declaration, not the static one of the first.
    POINTS: """\
namespace Shop.Geo.A
{
    public struct P
    {
        static P() { }
        public int M() => 1;
        public int Q => 2;
    }
}

namespace Shop.Geo.B
{
    public struct P
    {
        public P(int x) { }
        public int M() => 3;
        public int Q => 4;
    }
}
""",
    # A nested struct named like an earlier class of the file: 0.8.51 folds it into the class's node (Sum is the
    # class node's method), so there is no second Pair on either engine.
    PAIRS: """\
namespace Shop.Models;

public class Pair
{
    public int Left;
}

public class Holder
{
    public struct Pair
    {
        public int Sum() => 1;
        public int Right => 2;
    }
}
""",
    # A block namespace named like the struct it holds: one engine id, so the namespace's node owns the struct's
    # members on 0.8.51, and on 0.8.39 too.
    BOX: """\
namespace Box
{
    public struct Box
    {
        public int M() => 1;
        public int Q => 2;
        public Box(int x) { }
    }
}
""",
    # An enum named like a struct's method: on 0.8.39 the owner-less function `Scale()` has the id an enum Scale
    # would have, and it is a method, never the enum's node.
    UNITS: """\
namespace Shop.Models;

public struct Meter
{
    public double Value;
    public double Scale() => Value * 2;
}

public enum Scale
{
    Small,
    Large
}
""",
    # A one-line positional record whose method returns its own type: 0.8.39 gives the file a `references` node
    # labelled Item at the record's own line, which is not the record's node (the record still gets its owner).
    LINKS: """\
namespace Shop.Models;

public record Item(int Id) { public Item Copy() => this; }
""",
    # The constructor node: the first INSTANCE constructor; a static one only when it is the type's only one.
    CTORS: """\
namespace Shop.Services;

public class StaticFirst
{
    private static readonly int Seed;
    private readonly IPricer _p;

    static StaticFirst()
    {
        Seed = 1;
    }

    public StaticFirst(IPricer p)
    {
        _p = p;
    }

    public StaticFirst() : this(null)
    {
    }
}

public class StaticOnly
{
    public static int Seed;

    static StaticOnly()
    {
        Seed = 2;
    }
}

public class InstanceOnly
{
    private readonly IPricer _p;

    public InstanceOnly(IPricer p)
    {
        _p = p;
    }
}

public class InstanceThenStatic
{
    public InstanceThenStatic()
    {
    }

    static InstanceThenStatic()
    {
    }
}

public interface IPricer
{
    decimal Price(int qty);
}
""",
    # The Angular cart code the field user's search returned instead of the C# property.
    NG: """\
export class CartComponent {
  items: { price: number; quantity: number }[] = [];

  get totalPrice(): number {
    return this.items.reduce((sum, item) => sum + item.price * item.quantity, 0);
  }

  formatTotalPrice(): string {
    return this.totalPrice.toFixed(2);
  }

  refresh(): string {
    return this.formatTotalPrice();
  }
}
""",
    # Another language calling `Name()` and `Order()`: the name index must not hand those calls the C# property
    # ShoppingCartDto.Name or the constructor Order.Order.
    PY: """\
def Name():
    return "report"


def Order():
    return {}


def build():
    return Name(), Order()
""",
}


def ln(rel: str, needle: str, nth: int | None = None) -> int:
    """The 1-based line of the ONE line of TREE[rel] holding `needle` (the gate's own line oracle); with `nth`, the
    nth (0-based) of the lines holding it, for a declaration a file repeats on purpose."""
    hits = [i for i, text in enumerate(TREE[rel].split("\n"), 1) if needle in text]
    if nth is not None:
        if len(hits) <= nth:
            raise AssertionError(f"needle {needle!r} is on {len(hits)} lines of {rel}, not {nth + 1}")
        return hits[nth]
    if len(hits) != 1:
        raise AssertionError(f"needle {needle!r} is on {len(hits)} lines of {rel}")
    return hits[0]


def cs_files() -> list:
    """Every .cs file of TREE, in TREE order."""
    return [rel for rel in TREE if rel.endswith(".cs")]


def _members() -> list:
    """(label, name, file, line, summary) of every property / constructor node, on both engines."""
    m = "Method"
    return sorted([
        (m, "ShoppingCartDto.ItemsCalculatedPrice", DTO, ln(DTO, "public decimal ItemsCalculatedPrice"),
         "shopping cart dto items calculated price: property"),
        (m, "ShoppingCartDto.LegacyTotal", DTO, ln(DTO, '[Obsolete("use ItemsCalculatedPrice")]'),
         "shopping cart dto legacy total: property"),
        (m, "ShoppingCartDto.Name", DTO, ln(DTO, "public string Name { get => _name; }"),
         "shopping cart dto name: property"),
        (m, "ShoppingCartDto.ShoppingCartDto", DTO, ln(DTO, "[JsonConstructor]"),
         "shopping cart dto shopping cart dto: method"),
        (m, "CartItemDto.LineTotal", DTO, ln(DTO, "public decimal LineTotal"), "cart item dto line total: property"),
        (m, "IPriced.PriceWithTax", ORDER, ln(ORDER, "decimal PriceWithTax"), "i priced price with tax: property"),
        (m, "Order.Order", ORDER, ln(ORDER, "public Order(decimal first)"), "order order: method"),
        (m, "Order.Price", ORDER, ln(ORDER, "public decimal Price =>"), "order price: property"),
        (m, "Order.DebugView", ORDER, ln(ORDER, 'public string DebugView => "order";'), "order debug view: property"),
        (m, "Line.IsEmpty", ORDER, ln(ORDER, "public bool IsEmpty"), "line is empty: property"),
        (m, "Line.Line", ORDER, ln(ORDER, "public Line(int qty)"), "line line: method"),
        (m, "Repo.Size", ORDER, ln(ORDER, "public int Size"), "repo size: property"),
        (m, "Repo.Repo", ORDER, ln(ORDER, "public Repo()"), "repo repo: method"),
        (m, "OrderRec.Doubled", VALUES, ln(VALUES, "public int Doubled"), "order rec doubled: property"),
        (m, "Money.Money", VALUES, ln(VALUES, "public Money(decimal amount)"), "money money: method"),
        (m, "Money.IsZero", VALUES, ln(VALUES, "public bool IsZero"), "money is zero: property"),
        (m, "CartService.CartService", SVC, ln(SVC, "public CartService(ICartHelper helper)"),
         "cart service cart service: method"),
        (m, "CartService.Count", SVC, ln(SVC, "public int Count"), "cart service count: property"),
        (m, "CartService.Discount", SVC2, ln(SVC2, "public decimal Discount"), "cart service discount: property"),
        (m, "CartService.CartService", SVC2, ln(SVC2, "public CartService() : this(null)"),
         "cart service cart service: method"),
        # one owner per engine id: the first Line's members; the second Line's property and constructor are not nodes
        (m, "Line.Empty", NESTED, ln(NESTED, "public bool Empty => Qty == 0;"), "line empty: property"),
        (m, "Line.Line", NESTED, ln(NESTED, "public Line(int q)"), "line line: method"),
        (m, "P.Q", POINTS, ln(POINTS, "public int Q => 2;"), "p q: property"),
        (m, "P.P", POINTS, ln(POINTS, "public P(int x)"), "p p: method"),
        (m, "Pair.Right", PAIRS, ln(PAIRS, "public int Right"), "pair right: property"),
        (m, "Box.Q", BOX, ln(BOX, "public int Q"), "box q: property"),
        (m, "Box.Box", BOX, ln(BOX, "public Box(int x)"), "box box: method"),
        (m, "StaticFirst.StaticFirst", CTORS, ln(CTORS, "public StaticFirst(IPricer p)"),
         "static first static first: method"),
        (m, "StaticOnly.StaticOnly", CTORS, ln(CTORS, "static StaticOnly()"), "static only static only: method"),
        (m, "InstanceOnly.InstanceOnly", CTORS, ln(CTORS, "public InstanceOnly(IPricer p)"),
         "instance only instance only: method"),
        (m, "InstanceThenStatic.InstanceThenStatic", CTORS, ln(CTORS, "public InstanceThenStatic()"),
         "instance then static instance then static: method"),
    ])


def _owners_0839() -> list:
    """(label, name, file, line, summary) 0.8.39 gains beyond the members: the record / struct / enum owners (one per
    engine id of a name in a file, none where the engine already has a node of that id), the re-owned engine methods
    and the Twice() the record struct lost to the record's (one id on 0.8.39)."""
    return sorted([
        ("Class", "OrderRec", VALUES, ln(VALUES, "public record OrderRec"), "order rec: class"),
        ("Class", "Point", VALUES, ln(VALUES, "public record struct Point"), "point: class"),
        ("Class", "Money", VALUES, ln(VALUES, "public struct Money"), "money: class"),
        ("Class", "Color", VALUES, ln(VALUES, "public enum Color"), "color: class"),
        ("Method", "OrderRec.Twice", VALUES, ln(VALUES, "public int Twice() => Id * 2;"), "order rec twice: method"),
        ("Method", "Point.Twice", VALUES, ln(VALUES, "public int Twice() => X * 2;"), "point twice: method"),
        ("Method", "Money.Half", VALUES, ln(VALUES, "public decimal Half()"), "money half: method"),
        ("Method", "Money.Quarter", VALUES, ln(VALUES, "public decimal Quarter()"),
         "money quarter: method; calls money half"),
        # one owner per engine id, at the first declaration; Pair and Box have the engine's own node (a class, a
        # namespace), so only their methods are re-owned
        ("Class", "Status", NESTED, ln(NESTED, "public enum Status { Open, Closed }"), "status: class"),
        ("Class", "Line", NESTED, ln(NESTED, "public struct Line", 0), "line: class"),
        ("Method", "Line.Double", NESTED, ln(NESTED, "public int Double()"), "line double: method"),
        ("Class", "P", POINTS, ln(POINTS, "public struct P", 0), "p: class"),
        ("Method", "P.M", POINTS, ln(POINTS, "public int M() => 1;"), "p m: method"),
        ("Method", "Pair.Sum", PAIRS, ln(PAIRS, "public int Sum()"), "pair sum: method"),
        ("Method", "Box.M", BOX, ln(BOX, "public int M()"), "box m: method"),
        ("Class", "Meter", UNITS, ln(UNITS, "public struct Meter"), "meter: class"),
        ("Class", "Scale", UNITS, ln(UNITS, "public enum Scale"), "scale: class"),
        ("Method", "Meter.Scale", UNITS, ln(UNITS, "public double Scale()"), "meter scale: method"),
        ("Class", "Item", LINKS, ln(LINKS, "public record Item"), "item: class"),
        ("Method", "Item.Copy", LINKS, ln(LINKS, "public record Item"), "item copy: method"),
    ])


def _lost_0839() -> list:
    """The owner-less engine functions 0.8.39's mapping held before (replaced by the re-owned methods)."""
    return sorted([
        ("Function", "Twice", VALUES, ln(VALUES, "public int Twice() => Id * 2;"), "twice: function"),
        ("Function", "Half", VALUES, ln(VALUES, "public decimal Half()"), "half: function"),
        ("Function", "Quarter", VALUES, ln(VALUES, "public decimal Quarter()"), "quarter: function; calls half"),
        ("Function", "Double", NESTED, ln(NESTED, "public int Double()"), "double: function"),
        ("Function", "M", POINTS, ln(POINTS, "public int M() => 1;"), "m: function"),
        ("Function", "Sum", PAIRS, ln(PAIRS, "public int Sum()"), "sum: function"),
        ("Function", "M", BOX, ln(BOX, "public int M()"), "m: function"),
        ("Function", "Scale", UNITS, ln(UNITS, "public double Scale()"), "scale: function"),
        ("Function", "Copy", LINKS, ln(LINKS, "public record Item"), "copy: function"),
    ])


def _classes() -> list:
    """(name, file, line) of every :Class of the same-named-type files, on BOTH engines: one per engine id of a name
    in a file, at the engine's node (a class, a namespace) or else the first declaration."""
    return sorted([
        ("Basket", NESTED, ln(NESTED, "public class Basket")),
        ("Status", NESTED, ln(NESTED, "public enum Status { Open, Closed }")),
        ("Line", NESTED, ln(NESTED, "public struct Line", 0)),
        ("Invoice", NESTED, ln(NESTED, "public class Invoice")),
        ("Shop.Geo.A", POINTS, ln(POINTS, "namespace Shop.Geo.A")),
        ("P", POINTS, ln(POINTS, "public struct P", 0)),
        ("Shop.Geo.B", POINTS, ln(POINTS, "namespace Shop.Geo.B")),
        ("Pair", PAIRS, ln(PAIRS, "public class Pair")),
        ("Holder", PAIRS, ln(PAIRS, "public class Holder")),
        ("Box", BOX, ln(BOX, "namespace Box")),
        ("Meter", UNITS, ln(UNITS, "public struct Meter")),
        ("Scale", UNITS, ln(UNITS, "public enum Scale")),
        ("Item", LINKS, ln(LINKS, "public record Item")),
    ])


def _ctor_lines() -> dict:
    """{(Owner.Owner, file): the line of its node}: the first INSTANCE constructor, a static one only when the owner
    name has no instance constructor in the file."""
    return {("Order.Order", ORDER): ln(ORDER, "public Order(decimal first)"),
            ("StaticFirst.StaticFirst", CTORS): ln(CTORS, "public StaticFirst(IPricer p)"),
            ("StaticOnly.StaticOnly", CTORS): ln(CTORS, "static StaticOnly()"),
            ("InstanceOnly.InstanceOnly", CTORS): ln(CTORS, "public InstanceOnly(IPricer p)"),
            ("InstanceThenStatic.InstanceThenStatic", CTORS): ln(CTORS, "public InstanceThenStatic()"),
            ("P.P", POINTS): ln(POINTS, "public P(int x)"),
            ("Line.Line", NESTED): ln(NESTED, "public Line(int q)"),
            ("Box.Box", BOX): ln(BOX, "public Box(int x)")}


# Members no engine and no supplement may make a node of (name, file): auto / init properties, indexers, operators,
# events, a destructor, a positional record's primary constructor, enum members, the second constructor.
MUST_NOT = [("ShoppingCartDto.Items", DTO), ("ShoppingCartDto.Version", DTO), ("CartItemDto.Price", DTO),
            ("CartItemDto.Quantity", DTO), ("IPriced.Price", ORDER), ("Order.Kind", ORDER), ("Order.Changed", ORDER),
            ("Order.Saved", ORDER), ("Order.this", ORDER), ("Order.Item", ORDER), ("Order.operator", ORDER),
            ("Order.~Order", ORDER), ("Line.Qty", ORDER), ("OrderRec.OrderRec", VALUES), ("OrderRec.Id", VALUES),
            ("Point.Point", VALUES), ("Point.X", VALUES), ("Money.Amount", VALUES), ("Color.Red", VALUES),
            ("Color.Green", VALUES), ("Color.Color", VALUES), ("ICartHelper.ICartHelper", SVC),
            ("CartService._helper", SVC), ("Basket._first", NESTED), ("Basket.Basket", NESTED),
            ("Invoice.Invoice", NESTED), ("Status.Open", NESTED), ("Status.Status", NESTED), ("Line.Qty", NESTED),
            ("Line.Amount", NESTED), ("Pair.Pair", PAIRS), ("Pair.Left", PAIRS), ("Holder.Holder", PAIRS),
            ("Meter.Value", UNITS), ("Meter.Meter", UNITS), ("Scale.Small", UNITS), ("Scale.Scale", UNITS),
            ("StaticFirst.Seed", CTORS), ("StaticOnly.Seed", CTORS), ("IPricer.IPricer", CTORS),
            ("Item.Item", LINKS), ("Item.Id", LINKS)]

PRE_MODES = {
    "base": {},
    "p1": {"DEEPMAP_PRECISE_CALLS": "1"},
    "plus": {"DEEPMAP_GRAPHIFY_CALLS_PLUS": "1", "DEEPMAP_SUPPLEMENTAL_TS_RUST": "1"},
}

_TMP_HOLDER: list = []


def tmp() -> Path:
    if not _TMP_HOLDER:
        _TMP_HOLDER.append(Path(tempfile.mkdtemp(prefix="dm_csm_")))
    return _TMP_HOLDER[0]


def cleanup() -> None:
    if _TMP_HOLDER:
        shutil.rmtree(_TMP_HOLDER.pop(), ignore_errors=True)    # exactly the temp dir this gate created


def tree_sha() -> str:
    return hashlib.sha256(json.dumps(TREE, sort_keys=True).encode("utf-8")).hexdigest()


def write_tree(root: Path, files: dict) -> Path:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode("utf-8"))
    return root


_ROOT: list = []


def src_root() -> Path:
    """TREE materialized once (map_graph reads the sources from here)."""
    if not _ROOT:
        _ROOT.append(write_tree(tmp() / "src", TREE))
    return _ROOT[0]


def fixture_path(bucket: str) -> Path:
    return FX / f"{bucket}.json"


def load_json(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def graph_of(bucket: str) -> dict | None:
    p = fixture_path(bucket)
    return load_json(p)["graph"] if p.is_file() else None


def mapped(gx, graph: dict, root, env: dict, *, stats: dict | None = None, source=None) -> tuple:
    """map_graph of module `gx` on a COPY of `graph` under exactly `env`."""
    with clean_env(**env):
        kw = {} if source is None else {"source": source}
        return gx.map_graph(json.loads(json.dumps(graph)), root=root, stats=stats, **kw)


def canon(gx, nodes, calls, imports) -> dict:
    return {"nodes_sha": hashlib.sha256(json.dumps(nodes, sort_keys=True).encode("utf-8")).hexdigest(),
            "node_keys": sorted({k for n in nodes for k in n}),
            "calls": [[str(s), str(c), bool(gx.is_call_key(c))] for s, c in calls],
            "imports": [[str(s), str(t)] for s, t in imports]}


def facts(nodes, *, cs_only: bool = True) -> list:
    return sorted((n["label"], n["name"], n["source_file"], n["start_line"], n["body_summary"]) for n in nodes
                  if not cs_only or str(n["source_file"]).lower().endswith(".cs"))


def module_key(nodes, sf: str):
    return next((n["key"] for n in nodes if n["label"] == "Module" and n["source_file"] == sf), None)


def guarded(nodes) -> list:
    return [n for n in nodes if n.get("call_by_name") is False]


@contextlib.contextmanager
def no_cs_grammar():
    """`import tree_sitter_c_sharp` fails inside the block (as on an install without the grammar)."""
    saved = sys.modules.get("tree_sitter_c_sharp", "<absent>")
    sys.modules["tree_sitter_c_sharp"] = None
    try:
        yield
    finally:
        if saved == "<absent>":
            sys.modules.pop("tree_sitter_c_sharp", None)
        else:
            sys.modules["tree_sitter_c_sharp"] = saved


def _buckets():
    out = []
    for ver, b in BUCKETS.items():
        g = graph_of(b)
        check(f"{ver}: the fixture {fixture_path(b).relative_to(REPO)} exists", g is not None)
        if g is not None:
            out.append((ver, b, g))
    return out


# ================================================================================================
# A — exact facts per bucket
# ================================================================================================
def gate_a_facts() -> None:
    print("\n-- A: exact facts of the new C# nodes, per engine bucket --")
    check("graphify_extract.cs_ctor_name(owner) == 'Owner.Owner'",
          callable(getattr(GX, "cs_ctor_name", None)) and GX.cs_ctor_name("Order") == "Order.Order"
          and GX.cs_ctor_name("Repo") == "Repo.Repo")
    root = src_root()
    members = _members()
    for ver, b, graph in _buckets():
        st_on, st_off = {}, {}
        on = mapped(GX, graph, root, {}, stats=st_on)
        off = mapped(GX, graph, root, {"DEEPMAP_CSHARP_MEMBERS": "0"}, stats=st_off)
        f_on, f_off = facts(on[0]), facts(off[0])
        added = sorted(set(f_on) - set(f_off))
        removed = sorted(set(f_off) - set(f_on))
        want_added = sorted(members + (_owners_0839() if b == "0839" else []))
        check(f"{ver}: the nodes added are exactly the properties and constructors"
              f"{' + the record / struct / enum owners and their methods' if b == '0839' else ''}",
              added == want_added, {"missing": sorted(set(want_added) - set(added)),
                                    "extra": sorted(set(added) - set(want_added))})
        want_removed = _lost_0839() if b == "0839" else []
        what = "the owner-less functions of record / struct / enum bodies (Twice, Half, Quarter, ...)"
        check(f"{ver}: the nodes replaced are exactly {what if want_removed else 'none'}",
              removed == want_removed, removed)
        check(f"{ver}: no C# fact appears twice (a (name, file, line) is one node)",
              len(f_on) == len({(x[1], x[2], x[3]) for x in f_on}), len(f_on))
        g = guarded(on[0])
        check(f"{ver}: call_by_name False is set on exactly the properties and constructors",
              facts(g) == members, {"guarded": facts(g)})
        check(f"{ver}: ...and only as the boolean False (never pushed: the push writes fixed fields)",
              all(n["call_by_name"] is False for n in g) and not any("call_by_name" in n for n in on[0]
                                                                      if n not in g))
        bad_keys = [(n["name"], n["key"]) for n in g
                    if n["key"] != GX._supp_key(module_key(on[0], n["source_file"]), n["name"], n["start_line"])]
        check(f"{ver}: each member's key is _supp_key(<the file's engine node id>, name, line) (engine independent)",
              not bad_keys, bad_keys)
        check(f"{ver}: members are undocumented (no docstring was read) and carry no other private metadata",
              all(n["documented"] is False and set(n) == {"key", "label", "name", "source_file", "start_line",
                                                          "documented", "body_summary", "call_by_name"} for n in g),
              [sorted(n) for n in g][:3])
        gkeys = {n["key"] for n in g}
        check(f"{ver}: no CALLS tuple starts at a property or constructor (their bodies are not read)",
              not any(s in gkeys for s, _c in on[1]), [c for c in on[1] if c[0] in gkeys])
        names = {(n["name"], n["source_file"]) for n in on[0]}
        check(f"{ver}: never a node: auto / init properties, indexers, operators, events, destructors, primary "
              f"constructors, enum members, a second constructor", not (set(MUST_NOT) & names),
              sorted(set(MUST_NOT) & names))
        ctor_dup = [k for k in {(x[1], x[2]) for x in members if x[1].split(".")[0] == x[1].split(".")[-1]}
                    if sum(1 for n in on[0] if (n["name"], n["source_file"]) == k) != 1]
        check(f"{ver}: one constructor node per owner and file (the first instance one: Order's, not its static one)",
              not ctor_dup, ctor_dup)
        dump = [x for x in f_on if x[1] in ("Dump", "Order.Dump")]
        check(f"{ver}: a method inside #if stays the engine's owner-less function (the re-owning is for record / "
              f"struct / enum members only)", dump == [("Function", "Dump", ORDER, ln(ORDER, "public void Dump()"),
                                                        "dump: function")], dump)
        n_cls = sum(1 for x in f_on if x[0] == "Class" and x[1] in ("OrderRec", "Point", "Money", "Color"))
        check(f"{ver}: OrderRec, Point, Money and Color are :Class nodes", n_cls == 4, n_cls)
        if b == "0839":
            rename = {"OrderRec.Twice": "Twice", "Money.Half": "Half", "Money.Quarter": "Quarter"}
            key_of = {n["key"]: n["name"] for n in on[0]}
            got = [(s, rename.get(str(c), str(c))) for s, c in on[1]]
            check(f"{ver}: the CALLS tuples are the old ones, a re-owned method's name now Owner.method (the 0.8.51 "
                  f"name)", got == [(s, str(c)) for s, c in off[1]] and ("Money.Half" in {str(c) for _s, c in on[1]}),
                  (on[1], off[1]))
            check(f"{ver}: the re-owned methods keep their engine keys", all(
                k in {n["key"] for n in off[0]} for k, nm in key_of.items() if nm in rename))
        else:
            check(f"{ver}: the CALLS tuples are unchanged (the supplement adds no call)",
                  [(s, str(c)) for s, c in on[1]] == [(s, str(c)) for s, c in off[1]])
            check(f"{ver}: no synthetic owner (the engine has its own record / struct / enum classes)",
                  not [n for n in on[0] if n["label"] == "Class" and "@" in n["key"]])
        check(f"{ver}: imports unchanged", on[2] == off[2])
        n_new = len(added) - len(removed)        # a re-owned method replaces a node; every other one is new
        check(f"{ver}: stats supplemental_nodes counts the recovered declarations ({n_new} more than with =0)",
              int(st_on.get("supplemental_nodes") or 0) - int(st_off.get("supplemental_nodes") or 0) == n_new,
              (st_on.get("supplemental_nodes"), st_off.get("supplemental_nodes"), n_new))
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        GX._log_round7_counters({"supplemental_nodes": 5})
    check("the sync log's supplemental line names the C# declarations",
          "C#" in err.getvalue() and "propert" in err.getvalue() and "constructor" in err.getvalue(), err.getvalue())


# ================================================================================================
# B — the opt-out, a missing grammar and the pure mapping are the pre-change output
# ================================================================================================
def gate_b_identity() -> None:
    print("\n-- B: DEEPMAP_CSHARP_MEMBERS=0 / no C# grammar / no root == the pre-change map_graph, byte for byte --")
    pre_p = FX / "pre.json"
    check("the pre-change freeze exists", pre_p.is_file())
    if not pre_p.is_file():
        return
    pre = load_json(pre_p)
    check("the freeze holds both buckets in PRE_MODES (pure + with sources)",
          pre.get("modes") == PRE_MODES and set(pre.get("entries", {})) == set(BUCKETS.values()))
    root = src_root()
    for ver, b, graph in _buckets():
        rec = pre["entries"].get(b) or {}
        check(f"{ver}: the freeze was made from THIS capture",
              rec.get("graph_sha256") == hashlib.sha256(json.dumps(graph, sort_keys=True).encode("utf-8")).hexdigest())
        for mode, env in PRE_MODES.items():
            want = rec.get(mode)
            bad = [v for v in ("0", "false", "no", "off", " OFF ")
                   if canon(GX, *mapped(GX, graph, root, {**env, "DEEPMAP_CSHARP_MEMBERS": v})) != want]
            check(f"{ver}/{mode}: DEEPMAP_CSHARP_MEMBERS=0 (every OFF word) == the pre-change output", not bad, bad)
            with no_cs_grammar():
                got = canon(GX, *mapped(GX, graph, root, env))
            check(f"{ver}/{mode}: no tree-sitter-c-sharp == the pre-change output", got == want)
            check(f"{ver}/{mode}: no root (the pure mapping) == the pre-change pure output",
                  canon(GX, *mapped(GX, graph, None, env)) == rec.get(f"{mode}_pure"))
            on = canon(GX, *mapped(GX, graph, root, env))
            check(f"{ver}/{mode}: ...and the default (ON) does change the graph (the comparison has teeth)",
                  on["nodes_sha"] != (want or {}).get("nodes_sha"))
        for v in ("1", "yes", "maybe", ""):
            got = canon(GX, *mapped(GX, graph, root, {"DEEPMAP_CSHARP_MEMBERS": v}))
            if got != canon(GX, *mapped(GX, graph, root, {})):
                check(f"{ver}: DEEPMAP_CSHARP_MEMBERS={v!r} is the default (ON)", False)


# ================================================================================================
# C — idempotence
# ================================================================================================
def gate_c_idempotence() -> None:
    print("\n-- C: idempotent: no duplicate of a declaration the engine (or a first pass) already made --")
    root = src_root()
    got = {b: g for _v, b, g in _buckets()}
    if "0851" in got:
        st: dict = {}
        nodes = mapped(GX, got["0851"], root, {}, stats=st)[0]
        check("0.8.51: no synthetic owner key (every record / struct / enum is the engine's :Class)",
              not [n for n in nodes if n["label"] == "Class" and "@" in n["key"]])
    for b, graph in got.items():
        # an engine that emits a C# property itself: a `.ItemsCalculatedPrice()` method node of ShoppingCartDto at the
        # property's line (what a later graphify may do) is not doubled by the supplement
        g = json.loads(json.dumps(graph))
        owner = next(n["id"] for n in g["nodes"] if n.get("label") == "ShoppingCartDto")
        line = ln(DTO, "public decimal ItemsCalculatedPrice")
        g["nodes"].append({"id": owner + "_itemscalculatedprice", "label": ".ItemsCalculatedPrice()",
                           "file_type": "code", "source_file": DTO, "source_location": f"L{line}"})
        g["links"].append({"relation": "method", "source": owner, "target": owner + "_itemscalculatedprice",
                           "source_file": DTO, "source_location": f"L{line}", "confidence": "EXTRACTED"})
        nodes = mapped(GX, g, root, {})[0]
        same = [n for n in nodes if (n["name"], n["source_file"]) == ("ShoppingCartDto.ItemsCalculatedPrice", DTO)]
        check(f"{b}: a property the engine already emits is not added again (one node, the engine's)",
              len(same) == 1 and same[0]["key"] == owner + "_itemscalculatedprice", same)
        # the same reader twice: the second mapping is the first one
        shared = GX._Source(root)
        first = canon(GX, *mapped(GX, graph, root, {}, source=shared))
        second = canon(GX, *mapped(GX, graph, root, {}, source=shared))
        check(f"{b}: mapping twice through one reader gives the same graph (no state carried over)", first == second)
        nodes = mapped(GX, graph, root, {})[0]
        twice = [n for n in nodes if n["name"] == "OrderRec.Twice"]
        check(f"{b}: OrderRec.Twice is ONE node (a re-owned engine method is never recovered again)", len(twice) == 1,
              twice)


# ================================================================================================
# D — the name guard
# ================================================================================================
def gate_d_name_guard() -> None:
    print("\n-- D: a property / constructor is never a NAME target; every existing edge and grade is unchanged --")
    import edge_resolve as ER
    import language_recognizers as LR
    import sync as SY
    root = src_root()
    for ver, b, graph in _buckets():
        on = mapped(GX, graph, root, {})
        off = mapped(GX, graph, root, {"DEEPMAP_CSHARP_MEMBERS": "0"})
        gkeys = {n["key"] for n in guarded(on[0])}
        idx = ER.build_name_index(on[0])
        check(f"{ver}: build_name_index lists no property / constructor under any name",
              not any(k in gkeys for ks in idx.values() for k in ks))
        idx_off = ER.build_name_index(off[0])
        reowned = {x[1] for x in _lost_0839()}            # 0.8.39: these functions are Owner.method now
        synth = {n["key"] for n in on[0] if n["label"] == "Class" and "@" in n["key"]}   # 0.8.39's new owners
        check(f"{ver}: ...and lists every other node exactly as before (0.8.51: the whole index is the old one; "
              f"0.8.39: plus its synthetic owners)",
              (idx == idx_off) if b == "0851" else all([k for k in idx.get(n, []) if k not in synth] == ks
                                                       for n, ks in idx_off.items() if n not in reowned))
        res = ER.Resolver(on[0])
        check(f"{ver}: Resolver: no property / constructor in the (file, name) index or behind targets('Name')",
              not any(k in gkeys for ks in res._by_file.values() for k in ks)
              and not (set(res.targets("Name")) | set(res.targets("Order"))) & gkeys)
        check(f"{ver}: ...while the Resolver still knows them by key (a CallKey binds them)",
              all(res.label_of(k) == "Method" and res.name_of(k) for k in gkeys))
        e_on, e_off = SY._resolve_calls(on[0], on[1]), SY._resolve_calls(off[0], off[1])
        check(f"{ver}: sync._resolve_calls: every CALLS edge and grade (and their order) is what it was",
              list(e_on.items()) == list(e_off.items()), (e_on, e_off))
        py = {n["name"]: n["key"] for n in on[0] if n["source_file"] == PY}
        prop = next(n["key"] for n in on[0] if n["name"] == "ShoppingCartDto.Name")
        ctor = next(n["key"] for n in on[0] if (n["name"], n["source_file"]) == ("Order.Order", ORDER))
        build_edges = {t: c for (s, t), c in e_on.items() if s == py.get("build")}
        check(f"{ver}: the Python build() -> Name() call stays ONE INFERRED edge (never the C# property)",
              build_edges.get(py.get("Name")) == "INFERRED" and prop not in build_edges, build_edges)
        check(f"{ver}: ...and build() -> Order() never reaches the C# constructor Order.Order",
              ctor not in build_edges and build_edges.get(py.get("Order")) is not None, build_edges)
        bare = [dict(n) for n in on[0]]
        for n in bare:
            n.pop("call_by_name", None)
        e_bare = SY._resolve_calls(bare, on[1])
        bare_build = {t: c for (s, t), c in e_bare.items() if s == py.get("build")}
        check(f"{ver}: teeth: WITHOUT the flag the same calls fan out to the property and the constructor "
              f"(Name() would turn AMBIGUOUS)", prop in bare_build and ctor in bare_build
              and bare_build.get(py.get("Name")) == "AMBIGUOUS", bare_build)
        # a marked key binds a property: the push honours it whatever the name index says
        caller = next(n["key"] for n in on[0] if n["name"] == "ShoppingCartDto.CountItems")
        price = next(n["key"] for n in on[0] if n["name"] == "ShoppingCartDto.ItemsCalculatedPrice")
        got = SY._resolve_calls(on[0], [(caller, GX.call_key(price))])
        check(f"{ver}: a CallKey to a property is honoured (one EXTRACTED same-file edge)",
              got == {(caller, price): "EXTRACTED"}, got)
        typed_on = [dict(n) for n in on[0]]
        for n in typed_on:
            if n["key"] == caller:
                n["call_typed"] = {price: "INFERRED"}
        got = SY._resolve_calls(typed_on, [(caller, GX.call_key(price))])
        check(f"{ver}: ...and a typed key's own grade wins (call_typed)", got == {(caller, price): "INFERRED"}, got)
        got = SY._resolve_calls(on[0], [(caller, "ItemsCalculatedPrice"), (caller, "ShoppingCartDto.ItemsCalculatedPrice")])
        check(f"{ver}: a NAME tuple never binds one (by its own name or its last segment)", got == {}, got)
        # typed edges (the C# recognizer's IMPLEMENTS / DEPENDS_ON / ...) resolve as before
        edges, supp = [], []
        for rel in cs_files():
            n_i, e_i = LR.CSHARP.recognize(TREE[rel], rel)
            supp.extend(LR.validate_supplemental(n_i, "csharp"))
            edges.extend(e_i)
        m_on, m_off = LR.merge_supplemental(on[0], supp), LR.merge_supplemental(off[0], supp)
        r_on = ER.resolve_typed_edges(edges, ER.Resolver(m_on), [n["key"] for n in m_on])
        r_off = ER.resolve_typed_edges(edges, ER.Resolver(m_off), [n["key"] for n in m_off])
        check(f"{ver}: the C# recognizer's {len(edges)} typed edges resolve to rows none of which touch a property or "
              f"constructor", bool(edges) and not any(r[1] in gkeys or r[2] in gkeys for r in r_on), r_on)
        if b == "0851":
            check(f"{ver}: ...and to exactly the rows they resolved to without the new nodes", r_on == r_off,
                  (r_on, r_off))
        else:
            name_on = sorted((r[0], ER.Resolver(m_on).name_of(r[1]), ER.Resolver(m_on).name_of(r[2]), r[3]) for r in r_on)
            name_off = sorted((r[0], ER.Resolver(m_off).name_of(r[1]), ER.Resolver(m_off).name_of(r[2]), r[3])
                              for r in r_off)
            check(f"{ver}: ...and to the same (type, src name, dst name, grade) rows as without them (a record's row "
                  f"now anchors on its owner instead of the recognizer's shell)", name_on == name_off,
                  (name_on, name_off))


# ================================================================================================
# E — a .cs file with no engine node
# ================================================================================================
def gate_e_no_engine_node() -> None:
    print("\n-- E: a .cs file the engine gave no node gets nothing, and is never read --")
    root = src_root()
    for ver, b, graph in _buckets():
        g = json.loads(json.dumps(graph))
        drop = {n["id"] for n in g["nodes"] if str(n.get("source_file") or "").replace("\\", "/") == SVC2}
        g["nodes"] = [n for n in g["nodes"] if n["id"] not in drop]
        g["links"] = [l for l in g["links"] if l.get("source") not in drop and l.get("target") not in drop]
        shared = GX._Source(root)
        nodes = mapped(GX, g, root, {}, source=shared)[0]
        check(f"{ver}: no node of {SVC2} (the file the engine kept nothing of)",
              not [n for n in nodes if n["source_file"] == SVC2] and bool(drop), len(drop))
        check(f"{ver}: ...and it was never read", SVC2 not in shared._bytes, sorted(shared._bytes))
        check(f"{ver}: the other part of the partial class still gets its members",
              {("CartService.Count", SVC), ("CartService.CartService", SVC)}
              <= {(n["name"], n["source_file"]) for n in nodes})
        new_files = {n["source_file"] for n in guarded(nodes)}
        engine_files = {n["source_file"] for n in nodes if n["label"] == "Module"}
        check(f"{ver}: every new node is in a file the engine has a :Module for (coverage stays honest)",
              new_files <= engine_files, sorted(new_files - engine_files))


# ================================================================================================
# F — engine parity after the recognizer merge
# ================================================================================================
def gate_f_parity() -> None:
    print("\n-- F: after the C# recognizer's shells merge in, 0.8.39 and 0.8.51 hold the same C# nodes --")
    import language_recognizers as LR
    root = src_root()
    got = {b: g for _v, b, g in _buckets()}
    if set(got) != set(BUCKETS.values()):
        return
    supp = []
    for rel in cs_files():
        supp.extend(LR.validate_supplemental(LR.CSHARP.recognize(TREE[rel], rel)[0], "csharp"))
    merged = {b: LR.merge_supplemental(mapped(GX, g, root, {})[0], supp) for b, g in got.items()}
    f39, f51 = facts(merged["0839"]), facts(merged["0851"])
    check("the C# node facts (label, name, file, line, summary) are equal on 0.8.39 and 0.8.51",
          f39 == f51, {"0839 only": sorted(set(f39) - set(f51)), "0851 only": sorted(set(f51) - set(f39))})
    g39 = sorted((n["name"], n["source_file"]) for n in guarded(merged["0839"]))
    g51 = sorted((n["name"], n["source_file"]) for n in guarded(merged["0851"]))
    check("...and the same nodes carry the name guard", g39 == g51 and len(g39) == len(_members()))
    k39 = sorted(n["key"] for n in guarded(merged["0839"]))
    k51 = sorted(n["key"] for n in guarded(merged["0851"]))
    check("...with the same keys (a member's key comes from its file's id, which both engines share)", k39 == k51,
          (k39, k51))
    off39 = facts(LR.merge_supplemental(mapped(GX, got["0839"], root, {"DEEPMAP_CSHARP_MEMBERS": "0"})[0], supp))
    check("teeth: with DEEPMAP_CSHARP_MEMBERS=0 the two engines differ (0.8.39 has no record / struct / enum owner)",
          off39 != f51)


# ================================================================================================
# G — the installed engine equals its fixture
# ================================================================================================
def real_graph():
    home = tmp() / "home_real"
    root = write_tree(tmp() / "real", TREE)
    with clean_env(DEEPMAP_HOME=str(home)), contextlib.redirect_stderr(io.StringIO()), \
            contextlib.redirect_stdout(io.StringIO()):
        res, rep = GX.extract_with_report(root)
        gp = GX.graphify_out_dir(root) / "graph.json"
        g = load_json(gp) if (res is not None and gp.is_file()) else None
    if g is None:
        print(f"  (engine failed: {rep.get('cause')})")
    return g, root


def gate_g_real_engine() -> None:
    print("\n-- G: the installed engine's extraction of the tree == its captured fixture --")
    if not _HAS_ENGINE:
        print("  [SKIP] graphify is not installed")
        return
    ver = GX.graphify_version()
    b = BUCKETS.get(ver)
    print(f"  (graphify {ver})")
    if b is None:
        print(f"  (no capture for graphify {ver}: only 0.8.39 and 0.8.51 are captured; comparison skipped)")
        return
    doc = load_json(fixture_path(b)) if fixture_path(b).is_file() else None
    check(f"{ver}: the fixture was captured from THIS tree (re-capture after editing TREE)",
          doc is not None and doc.get("tree_sha256") == tree_sha())
    if doc is None:
        return
    g, _r = real_graph()
    check(f"{ver}: the engine produced a graph", g is not None)
    if g is None:
        return
    root = src_root()
    for env in ({}, {"DEEPMAP_CSHARP_MEMBERS": "0"}, {"DEEPMAP_PRECISE_CALLS": "1"}):
        a = mapped(GX, g, root, env)
        f = mapped(GX, doc["graph"], root, env)
        same = (facts(a[0], cs_only=False) == facts(f[0], cs_only=False)
                and sorted(n["key"] for n in a[0]) == sorted(n["key"] for n in f[0])
                and sorted((s, str(c)) for s, c in a[1]) == sorted((s, str(c)) for s, c in f[1]))
        check(f"{ver}: same nodes and calls as the fixture under {env or 'the defaults'}", same)


# ================================================================================================
# H — one parse per file
# ================================================================================================
def gate_h_one_parse() -> None:
    print("\n-- H: one tree-sitter parse per engine .cs file, none for another file --")
    try:
        import tree_sitter as ts
    except Exception as e:  # noqa: BLE001
        check("tree_sitter is importable (the engine's own dependency)", False, e)
        return
    root = src_root()
    by_bytes = {(root / rel).read_bytes(): rel for rel in TREE}
    real = ts.Parser
    parsed: list = []

    class Spy:
        def __init__(self, *a, **k):
            self._p = real(*a, **k)

        def parse(self, data, *a, **k):
            parsed.append(by_bytes.get(bytes(data), "<other>"))
            return self._p.parse(data, *a, **k)

    for ver, b, graph in _buckets():
        parsed.clear()
        ts.Parser = Spy
        try:
            mapped(GX, graph, root, {"DEEPMAP_PRECISE_CALLS": "1"})
        finally:
            ts.Parser = real
        cs = sorted(p for p in parsed if p.endswith(".cs"))
        check(f"{ver}: each of the {len(cs_files())} .cs files is parsed exactly once in one map_graph",
              cs == sorted(cs_files()), cs)
        parsed.clear()
        ts.Parser = Spy
        try:
            mapped(GX, graph, root, {"DEEPMAP_CSHARP_MEMBERS": "0"})
        finally:
            ts.Parser = real
        check(f"{ver}: with DEEPMAP_CSHARP_MEMBERS=0 no .cs file is parsed",
              not [p for p in parsed if p.endswith(".cs")], parsed)


# ================================================================================================
# I — same-named types and the constructor choice
# ================================================================================================
SAME_NAMED = (NESTED, POINTS, PAIRS, BOX, UNITS, LINKS)


def _defined_ids(graph: dict) -> set:
    return {l.get("target") for l in graph.get("links", []) if l.get("relation") in ("contains", "method")}


def gate_i_same_named_and_ctors() -> None:
    print("\n-- I: one :Class per engine id of a name in a file; Owner.Owner is the first instance constructor --")
    root = src_root()
    want_cls = _classes()
    per_bucket: dict = {}
    for ver, b, graph in _buckets():
        nodes = mapped(GX, graph, root, {})[0]
        per_bucket[b] = nodes
        cls = sorted((n["name"], n["source_file"], n["start_line"]) for n in nodes
                     if n["label"] == "Class" and n["source_file"] in SAME_NAMED)
        check(f"{ver}: the :Class nodes of the same-named-type files are one per engine id, at the engine's node or "
              f"the first declaration (no owner for a later Status / Line / P, no second Pair or Box)",
              cls == want_cls, {"missing": sorted(set(want_cls) - set(cls)), "extra": sorted(set(cls) - set(want_cls))})
        if b == "0851":
            synth = [(n["name"], n["source_file"], n["start_line"]) for n in nodes if n["label"] == "Class"
                     and "@" in n["key"]]
            check(f"{ver}: no synthetic :Class anywhere (the engine's own node stands for every declaration it "
                  f"collapsed)", not synth, synth)
        owned = {(n["name"], n["source_file"]): n["start_line"] for n in nodes if n["source_file"] in SAME_NAMED
                 and n["label"] == "Method"}
        want_owned = {("Line.Double", NESTED): ln(NESTED, "public int Double()"),
                      ("Line.Empty", NESTED): ln(NESTED, "public bool Empty => Qty == 0;"),
                      ("P.M", POINTS): ln(POINTS, "public int M() => 1;"),
                      ("P.Q", POINTS): ln(POINTS, "public int Q => 2;"),
                      ("Pair.Sum", PAIRS): ln(PAIRS, "public int Sum()"),
                      ("Pair.Right", PAIRS): ln(PAIRS, "public int Right"),
                      ("Box.M", BOX): ln(BOX, "public int M()"), ("Box.Q", BOX): ln(BOX, "public int Q"),
                      ("Meter.Scale", UNITS): ln(UNITS, "public double Scale()"),
                      ("Item.Copy", LINKS): ln(LINKS, "public record Item")}
        check(f"{ver}: a collapsed type's members are named after the one owner and sit at the first declaration",
              all(owned.get(k) == v for k, v in want_owned.items()),
              {k: (owned.get(k), v) for k, v in want_owned.items() if owned.get(k) != v})
        if b == "0839":
            # these guards have teeth only while the capture holds the shapes they guard against (each checks it)
            defined = _defined_ids(graph)
            ref = [n["id"] for n in graph["nodes"] if n.get("label") == "Line" and n.get("source_file") == NESTED
                   and n["id"] not in defined]
            line_cls = [n for n in nodes if (n["label"], n["name"], n["source_file"]) == ("Class", "Line", NESTED)]
            check(f"{ver}: the engine gave {NESTED} a `references` node labelled Line (the field `Line _first`), and "
                  f"the struct's owner is NOT that node but a :Class at the struct's own line",
                  bool(ref) and len(line_cls) == 1 and line_cls[0]["key"] not in ref
                  and line_cls[0]["start_line"] == ln(NESTED, "public struct Line", 0), (ref, line_cls))
            fn = [n["id"] for n in graph["nodes"] if n.get("label") == "Scale()" and n.get("source_file") == UNITS]
            scale = [n for n in nodes if (n["label"], n["name"], n["source_file"]) == ("Class", "Scale", UNITS)]
            check(f"{ver}: the function Scale() (the id an enum Scale would have) never stands in for the enum: Scale "
                  f"is its own :Class at the enum's line, Scale() is Meter.Scale",
                  bool(fn) and GX._engine_id(GX._bare("Scale()")) == GX._engine_id("Scale") and len(scale) == 1
                  and scale[0]["key"] not in fn and scale[0]["start_line"] == ln(UNITS, "public enum Scale"),
                  (fn, scale))
            line_i = ln(LINKS, "public record Item")
            ref_i = [n["id"] for n in graph["nodes"] if n.get("label") == "Item" and n.get("source_file") == LINKS
                     and GX._line_of(n) == line_i and n["id"] not in defined]
            item = [n for n in nodes if (n["label"], n["name"], n["source_file"]) == ("Class", "Item", LINKS)]
            check(f"{ver}: a `references` node labelled Item at the record's own line (Copy's return type) is not "
                  f"the record's node: Item still gets its :Class and Copy() is Item.Copy",
                  bool(ref_i) and len(item) == 1 and item[0]["key"] not in ref_i and item[0]["start_line"] == line_i,
                  (ref_i, item))
        got_ctor = {}
        for n in nodes:
            k = (n["name"], n["source_file"])
            if k in _ctor_lines():
                got_ctor.setdefault(k, []).append(n["start_line"])
        bad = {k: (got_ctor.get(k), v) for k, v in _ctor_lines().items() if got_ctor.get(k) != [v]}
        check(f"{ver}: each Owner.Owner node is the first INSTANCE constructor (static first, then instance: the "
              f"instance one; static only: the static one; split over two same-named structs: the instance one)",
              not bad, bad)
    if set(per_bucket) == set(BUCKETS.values()):
        f39 = [x for x in facts(per_bucket["0839"]) if x[2] in SAME_NAMED + (CTORS,)]
        f51 = [x for x in facts(per_bucket["0851"]) if x[2] in SAME_NAMED + (CTORS,)]
        check("the same-named-type and constructor files map to the same facts on 0.8.39 and 0.8.51 (before any "
              "recognizer shell)", f39 == f51, {"0839 only": sorted(set(f39) - set(f51)),
                                                "0851 only": sorted(set(f51) - set(f39))})


# ================================================================================================
# --capture / --freeze
# ================================================================================================
def _relative(graph: dict, root: Path) -> dict:
    """`graph` with every string field that holds an absolute path under `root` made root-relative."""
    prefixes = [str(root) + os.sep, str(root).replace("\\", "/") + "/"]

    def fix(v):
        if isinstance(v, str):
            for p in prefixes:
                if v.lower().startswith(p.lower()):
                    return v[len(p):].replace("\\", "/")
        return v

    return {k: ([{kk: fix(vv) for kk, vv in item.items()} if isinstance(item, dict) else item for item in v]
                if isinstance(v, list) else v) for k, v in graph.items()}


def do_capture() -> int:
    ver = GX.graphify_version()
    b = BUCKETS.get(ver)
    if b is None:
        print(f"--capture: graphify {ver} is not one of {sorted(BUCKETS)}")
        return 1
    g, root = real_graph()
    if g is None:
        print("--capture: the engine failed")
        return 1
    FX.mkdir(parents=True, exist_ok=True)
    doc = {"_about": (f"REAL `graphify update --no-cluster` output (graphify {ver}) of tests/csharp_members_gates.TREE, "
                      "written by extract_with_report (absolute paths made root-relative); re-capture with "
                      "`python tests/csharp_members_gates.py --capture` under this engine."),
           "engine": ver, "tree_sha256": tree_sha(), "graph": _relative(g, root)}
    fixture_path(b).write_bytes((json.dumps(doc, indent=1, sort_keys=True) + "\n").encode("utf-8"))
    print(f"--capture: wrote {fixture_path(b).relative_to(REPO)} ({len(g.get('nodes', []))} nodes, "
          f"{len(g.get('links', []))} links)")
    return 0


def do_freeze(pre_dir: str) -> int:
    path = Path(pre_dir) / "graphify_extract.py"
    spec = importlib.util.spec_from_file_location("gx_pre_csharp_members", str(path))
    gx = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gx)
    if hasattr(gx, "cs_ctor_name"):
        print("--freeze: that module already has cs_ctor_name — not the pre-change module")
        return 1
    root = src_root()
    ents: dict = {}
    for ver, b in BUCKETS.items():
        g = graph_of(b)
        if g is None:
            print(f"--freeze: capture {b} first (--capture under graphify {ver})")
            return 1
        rec = {"graph_sha256": hashlib.sha256(json.dumps(g, sort_keys=True).encode("utf-8")).hexdigest()}
        for mode, env in PRE_MODES.items():
            rec[mode] = canon(gx, *mapped(gx, g, root, env))
            rec[f"{mode}_pure"] = canon(gx, *mapped(gx, g, None, env))
        ents[b] = rec
    doc = {"_about": ("map_graph of the PRE-CHANGE build/graphify_extract.py (sha256 below) on both captures, in "
                      "PRE_MODES (with the tree's sources, and pure as `<mode>_pure`); calls are [caller, callee, "
                      "is_call_key]. Made once, before the C# member supplement: "
                      "`python tests/csharp_members_gates.py --freeze <dir holding the pre-change module>`."),
           "module_sha256": hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest(),
           "modes": PRE_MODES, "entries": ents}
    p = FX / "pre.json"
    p.write_bytes((json.dumps(doc, indent=1, sort_keys=True) + "\n").encode("utf-8"))
    print(f"--freeze: wrote {p.relative_to(REPO)}")
    return 0


def main(argv) -> int:
    try:
        if "--capture" in argv:
            return do_capture()
        if "--freeze" in argv:
            return do_freeze(argv[argv.index("--freeze") + 1])
        print("csharp_members_gates — C# computed properties, constructors and (0.8.39) record / struct / enum owners")
        for g in (gate_a_facts, gate_b_identity, gate_c_idempotence, gate_d_name_guard, gate_e_no_engine_node,
                  gate_f_parity, gate_g_real_engine, gate_h_one_parse, gate_i_same_named_and_ctors):
            try:
                g()
            except Exception as e:  # noqa: BLE001 — a crashing section is a RED check, not a crashed run
                import traceback
                traceback.print_exc()
                check(f"{g.__name__} ran without raising", False, f"{type(e).__name__}: {e}")
    finally:
        cleanup()
    total = _PASS + _FAIL
    if _FAIL:
        print(f"\nRESULT: RED — {_FAIL} of {total} checks failed.")
        return 1
    print(f"\nRESULT: GREEN — {total}/{total} checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
