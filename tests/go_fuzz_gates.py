#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Go recognizer hardening — 300-source fuzz + metamorphic + the integration MONEY GATE (no Neo4j).

Mirrors the Rails/C#/Java fuzz suites: the recognizer must NEVER raise, be deterministic, and only
ever emit its declared edge vocabulary over adversarial input; and the whole recognizer -> validate
-> merge -> resolve -> prune -> push chain must run through a FAKE driver with no Neo4j.
"""
from __future__ import annotations

import contextlib
import io
import random
import string
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build"
sys.path.insert(0, str(BUILD))

import go_edges as G              # noqa: E402
import language_recognizers as LR  # noqa: E402
import sync as S                   # noqa: E402
import framework_edges as FE       # noqa: E402

_PASS = _FAIL = 0


def check(desc: str, cond: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    ok = bool(cond)
    _PASS += ok
    _FAIL += (not ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {desc}" + ("" if ok else f"  -- {detail}"))


# ---- fuzz corpus -------------------------------------------------------------
_DSL_FRAGMENTS = [
    "package model", "type Order struct {", "type Admin struct {", "type Reader interface {",
    "\tID uint", "\tCustomerID uint", "\tCustomer Customer", "\tName string",
    "\tLineItems []LineItem `gorm:\"constraint:OnDelete:CASCADE\"`",
    "\tTags []*Tag `gorm:\"many2many:order_tags\"`", "\tAudits []Audit `gorm:\"foreignKey:OrderID\"`",
    "\tCreatedAt time.Time", "\tgorm.Model", "\tUser", "\tBaseModel", "}",
    "func NewOrderService(repo OrderRepo, log Logger) *OrderService {",
    "func (o *Order) Total() int {", "func RegisterRoutes(r *Engine) {",
    "\tr.GET(\"/orders\", GetOrders)", "\tr.POST(\"/orders\", handlers.Create)",
    "\tr.DELETE(\"/x/:id\", func(c *Context) {})", "\thttp.HandleFunc(\"/health\", HealthCheck)",
    "\treturn nil", "// a comment", "/* block */", "import \"gorm.io/gorm\"",
]
_JUNK = ["\x00\x01\xff", "日本語のテキスト", "🚀🔥", "a" * 5000, "type", "struct", "func", "{", "}",
         "`", "gorm:", "[]", "*", "r.GET(", ".POST(", "()", "type 9Bad struct {", "} } } }",
         "\r\n\r\r\n", "＜クラス＞", "-- not go --", "{\"json\": true}", "constraint:OnDelete:", "New("]


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
    return rng.choice(["\n", "\r\n", "\r"]).join(lines)


def gate_fuzz() -> None:
    print("\n-- 300 seeded adversarial sources: never raise, deterministic, vocab-clean --")
    rng = random.Random(20260714)
    go_entry = next(e for e in LR.REGISTRY if e.name == "go")
    crashed = nondet = badvocab = badshape = badnode = 0
    for i in range(300):
        src = _fuzz_source(rng)
        rel = f"pkg/f{i}.go"
        try:
            out1 = G.recognize(src, rel)
            out2 = G.recognize(src, rel)
        except Exception:
            crashed += 1
            continue
        if not (isinstance(out1, tuple) and len(out1) == 2
                and isinstance(out1[0], list) and isinstance(out1[1], list)):
            badshape += 1
            continue
        if out1 != out2:
            nondet += 1
        if any(e.edge_type not in go_entry.edge_types for e in out1[1]):
            badvocab += 1
        if len(LR.validate_supplemental(out1[0], "go")) != len(out1[0]):
            badnode += 1
    check("0/300 raised", crashed == 0, f"{crashed} crashed")
    check("0/300 wrong return shape", badshape == 0, f"{badshape}")
    check("0/300 nondeterministic", nondet == 0, f"{nondet}")
    check("0/300 emitted an undeclared edge type", badvocab == 0, f"{badvocab}")
    check("0/300 produced a validator-rejected supplemental node", badnode == 0, f"{badnode}")
    check("no IMPLEMENTS ever emitted (Go interfaces are implicit)",
          "IMPLEMENTS" not in go_entry.edge_types)


ORDER_SRC = """package model

type Order struct {
\tID        uint
\tCustomer  Customer
\tLineItems []LineItem `gorm:"constraint:OnDelete:CASCADE"`
}
"""


def gate_metamorphic() -> None:
    print("\n-- metamorphic properties --")
    rel = "model/order.go"
    n0, e0 = G.recognize(ORDER_SRC, rel)
    check("idempotent (recognize twice == same)", G.recognize(ORDER_SRC, rel) == (n0, e0))
    commented = ORDER_SRC.replace("\tCustomer  Customer", "\t// note\n\tCustomer  Customer")
    _, e1 = G.recognize(commented, rel)
    check("comment insertion is edge-invariant", e1 == e0)
    shifted = "\n\n\n" + ORDER_SRC
    n2, e2 = G.recognize(shifted, rel)
    check("blank-line prefix shifts start_lines by exactly 3; names/edges identical",
          e2 == e0 and all(a["start_line"] + 3 == b["start_line"] for a, b in zip(n0, n2)))
    no_rel = ORDER_SRC.replace(
        '\tLineItems []LineItem `gorm:"constraint:OnDelete:CASCADE"`\n', "")
    _, e3 = G.recognize(no_rel, rel)
    check("removing the slice field drops its HAS_MANY + CASCADES (no phantom edge)",
          not any(x.dst_name == "LineItem" for x in e3))


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


LINEITEM_SRC = """package model

type LineItem struct {
\tID uint
}
"""

ROUTES_SRC = """package web

func RegisterRoutes(r *Engine) {
\tr.GET("/orders", GetOrders)
}

func GetOrders(c *Context) {
}
"""


def gate_money() -> None:
    print("\n-- MONEY GATE: Go tree -> _apply_recognizers -> push_to_neo4j (fake driver) --")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        for rel, src in (("model/order.go", ORDER_SRC), ("model/lineitem.go", LINEITEM_SRC),
                         ("web/routes.go", ROUTES_SRC)):
            p = root / Path(rel)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(src, encoding="utf-8")

        merged, typed = S._apply_recognizers(root, [])
        check("recognizer fills the empty model layer (ensured :Class nodes)",
              {n["name"] for n in merged} >= {"Order", "LineItem"},
              str({n.get("name") for n in merged}))

        log: list = []
        n_nodes, _ = S.push_to_neo4j(_Drv(log), merged, [], [], typed)
        node_merges = [c for c, _ in log if "MERGE (x:Class" in c]
        check("every ensured node MERGEs with the :Class label",
              n_nodes == len(merged) and len(node_merges) == len(merged),
              f"{n_nodes} nodes, {len(node_merges)} :Class merges")

        cas = [(c, p) for c, p in log if "MERGE" in c and "CASCADES" in c]
        check("the GORM cascade persists REVERSED (b=LineItem -> a=Order: impact('Order') lists LineItem)",
              len(cas) == 1 and "MERGE (b)-[r:CASCADES]->(a)" in cas[0][0]
              and "Order:" in cas[0][1]["a"] and "LineItem:" in cas[0][1]["b"], str(cas))

        routes = [(c, p) for c, p in log if "MERGE" in c and "ROUTES_TO" in c]
        check("ROUTES_TO to a handler no extractor node-ified DROPS (SAME_FILE_STRICT LOCAL)",
              routes == [], str(routes))

        # Now graphify DID parse: the Module + the handler :Function exist -> the route resolves.
        graphify_extra = [
            {"key": "routes_mod", "label": "Module", "name": "routes",
             "source_file": "web/routes.go", "start_line": 1, "documented": False, "body_summary": "m"},
            {"key": "getorders", "label": "Function", "name": "GetOrders",
             "source_file": "web/routes.go", "start_line": 6, "documented": False, "body_summary": "m"},
        ]
        log2: list = []
        S.push_to_neo4j(_Drv(log2), merged + graphify_extra, [], [], typed)
        routes2 = [(c, p) for c, p in log2 if "MERGE" in c and "ROUTES_TO" in c]
        check("with the handler :Function present, the SAME ROUTES_TO resolves EXTRACTED",
              len(routes2) == 1 and routes2[0][1].get("c") == "EXTRACTED", str(routes2))

        pruned = {c.split(":")[1].split("]")[0] for c, _ in log if "DELETE r" in c and "[r:" in c}
        check("every whitelisted edge type pruned on this sync (anti-phantom, full vocab)",
              pruned >= (set(FE.ALLOWED_EDGE_TYPES) - {"IMPORTS"}), str(sorted(pruned)))


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 64)
    print("DeepMap — Go recognizer fuzz + metamorphic + money gates (no Neo4j)")
    print("=" * 64)
    with contextlib.redirect_stderr(io.StringIO()):
        gate_fuzz()
        gate_metamorphic()
        gate_money()
    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
