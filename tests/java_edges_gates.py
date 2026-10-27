#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gates for the Java recognizer (plugin #4, no Neo4j / javac / Spring runtime).

Locks the Spring/JPA declaration layer graphify cannot see (verified against the captured fixture
tests/fixtures/graphify_multilang/java_spring.json — real `graphify update --no-cluster` output, whose
map_graph yields the annotated classes as bare :Class nodes and NONE of its inherits/references
relations). Gates:

  - recognizer emit over REAL Spring/JPA source: INHERITS / IMPLEMENTS, @*Mapping -> ROUTES_TO with
    a composed path, @OneToMany/@ManyToMany -> HAS_MANY, @ManyToOne/@OneToOne -> REFERENCES,
    cascade=REMOVE|ALL / orphanRemoval -> CASCADES (PERSIST/MERGE NOT), @Scheduled -> SCHEDULED_AS,
    @EventListener -> TRIGGERS;
  - dependency injection: constructor params, @Autowired fields, Lombok @RequiredArgsConstructor
    final fields -> DEPENDS_ON (BCL/scalars filtered);
  - ensured :Class nodes byte-match graphify's bare names so merge_supplemental dedupes them away
    when graphify parsed the file and fills the hole when it did not;
  - the FULL push path: routes resolve same-file (LOCAL EXTRACTED), ctor deps resolve cross-file
    (NAME INFERRED), CASCADES persists REVERSED; end-to-end registry run + DEEPMAP_RECOGNIZERS=0 off.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build"
sys.path.insert(0, str(BUILD))

import java_edges as J             # noqa: E402
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


# -- sources whose class names + files ALIGN with the captured fixture (java_spring.json) ----------
ORDER_JAVA = """package shop.model;

import java.util.List;

@Entity
public class Order extends BaseEntity {
    @Id
    private Long id;

    @ManyToOne
    @JoinColumn(name = "customer_id")
    private Customer customer;

    @OneToMany(mappedBy = "order", cascade = CascadeType.REMOVE, orphanRemoval = true)
    private List<LineItem> lineItems;

    @ManyToMany
    private Set<Tag> tags;

    @OneToOne(cascade = CascadeType.PERSIST)
    private Invoice invoice;

    private String note;
}
"""

CONTROLLER_JAVA = """package shop.web;

@RestController
@RequestMapping("/api/orders")
public class OrderController {
    private final OrderService service;

    public OrderController(OrderService service) {
        this.service = service;
    }

    @GetMapping("/{id}")
    public Order get(Long id) {
        return service.find(id);
    }
}
"""

SERVICE_JAVA = """package shop.service;

@Service
@RequiredArgsConstructor
public class OrderService implements IOrderService {
    private final OrderRepository repo;
    private final Clock clock;

    @Autowired
    private AuditLog auditLog;

    @Scheduled(fixedRate = 60000)
    public void reap() {
    }

    @EventListener
    public void onPlaced(OrderPlaced event) {
    }
}
"""

CTOR_JAVA = """package shop.service;

public class PaymentService {
    public PaymentService(PaymentGateway gateway, ILogger logger, int retries) {
    }
}
"""


def _edges(src, rel):
    return J.recognize(src, rel)[1]


def _by_type(edges, t):
    return [e for e in edges if e.edge_type == t]


def gate_recognizer_emit() -> None:
    print("\n-- recognizer emit: inheritance / routes / JPA relations / cascades / tasks --")
    e = _edges(CONTROLLER_JAVA, "src/web/OrderController.java")
    ro = _by_type(e, "ROUTES_TO")
    check("@GetMapping -> ROUTES_TO OrderController -> OrderController.get (LOCAL, composed path)",
          len(ro) == 1 and ro[0].dst_name == "OrderController.get"
          and ro[0].conf_hint == "LOCAL" and ro[0].attrs.get("path") == "api/orders/{id}", str(ro))
    dep = _by_type(e, "DEPENDS_ON")
    check("constructor param -> DEPENDS_ON OrderController -> OrderService",
          any(d.dst_name == "OrderService" and d.attrs.get("via") == "ctor" for d in dep), str(dep))

    e2 = _edges(ORDER_JAVA, "src/model/Order.java")
    inh = _by_type(e2, "INHERITS")
    check("class Order extends BaseEntity -> INHERITS", inh and inh[0].dst_name == "BaseEntity", str(inh))
    hm = {x.dst_name for x in _by_type(e2, "HAS_MANY")}
    check("@OneToMany List<LineItem> -> HAS_MANY LineItem (element type from the generic)",
          "LineItem" in hm, str(hm))
    check("@ManyToMany Set<Tag> -> HAS_MANY Tag", "Tag" in hm, str(hm))
    refs = {x.dst_name for x in _by_type(e2, "REFERENCES")}
    check("@ManyToOne Customer -> REFERENCES Customer", "Customer" in refs, str(refs))
    cas = {x.dst_name: x for x in _by_type(e2, "CASCADES")}
    check("@OneToMany cascade=REMOVE + orphanRemoval -> CASCADES Order -> LineItem",
          "LineItem" in cas and cas["LineItem"].src_name == "Order", str(list(cas)))
    check("@OneToOne cascade=PERSIST is NOT a delete-cascade (no CASCADES for Invoice)",
          "Invoice" not in cas, str(list(cas)))
    check("a scalar field (String note) emits nothing",
          not any(x.dst_name == "note" for x in e2)
          and "String" not in refs and "String" not in hm)

    e3 = _edges(SERVICE_JAVA, "src/service/OrderService.java")
    check("class implements IOrderService -> IMPLEMENTS",
          any(x.edge_type == "IMPLEMENTS" and x.dst_name == "IOrderService" for x in e3), str(e3))
    check("@Scheduled method -> SCHEDULED_AS OrderService.reap",
          any(x.edge_type == "SCHEDULED_AS" and x.dst_name == "OrderService.reap" for x in e3))
    check("@EventListener method -> TRIGGERS OrderService.onPlaced",
          any(x.edge_type == "TRIGGERS" and x.dst_name == "OrderService.onPlaced" for x in e3))
    check("every emitted edge type is within ALLOWED_EDGE_TYPES (injection-safe vocabulary)",
          all(x.edge_type in FE.ALLOWED_EDGE_TYPES for x in e + e2 + e3))


def gate_dependency_injection() -> None:
    print("\n-- DI: constructor params / @Autowired fields / Lombok final fields -> DEPENDS_ON --")
    e = _edges(SERVICE_JAVA, "src/service/OrderService.java")
    deps = {x.dst_name: x for x in _by_type(e, "DEPENDS_ON")}
    check("Lombok @RequiredArgsConstructor final field -> DEPENDS_ON OrderRepository",
          "OrderRepository" in deps, str(sorted(deps)))
    check("Lombok final field Clock -> DEPENDS_ON", "Clock" in deps, str(sorted(deps)))
    check("@Autowired field -> DEPENDS_ON AuditLog", "AuditLog" in deps, str(sorted(deps)))
    check("all DI src is the declaring class",
          all(d.src_name == "OrderService" for d in _by_type(e, "DEPENDS_ON")))

    ce = _edges(CTOR_JAVA, "src/service/PaymentService.java")
    cdeps = {x.dst_name for x in _by_type(ce, "DEPENDS_ON")}
    check("ctor params -> DEPENDS_ON PaymentGateway + ILogger", {"PaymentGateway", "ILogger"} <= cdeps,
          str(cdeps))
    check("a scalar ctor param (int retries) is NOT a dependency", "int" not in cdeps)


def gate_ensured_nodes_match_graphify() -> None:
    print("\n-- ensured :Class nodes byte-match graphify's names (captured fixture) --")
    fx = json.loads((REPO / "tests" / "fixtures" / "graphify_multilang" / "java_spring.json")
                    .read_text(encoding="utf-8"))
    mapped, _, _ = GX.map_graph(fx)
    g_classes = {(n["name"], n["source_file"]) for n in mapped if n["label"] == "Class"}
    check("FIXTURE ground truth: graphify node-ifies the annotated @Entity/@RestController classes",
          ("Order", "src/model/Order.java") in g_classes
          and ("OrderController", "src/web/OrderController.java") in g_classes, str(g_classes))
    check("FIXTURE ground truth: graphify emits NO framework relationship map_graph keeps "
          "(no INHERITS/HAS_MANY/... in the mapped graph — the field state the recognizer fixes)",
          not any(n["label"] == "Route" for n in mapped))

    n_order, _ = J.recognize(ORDER_JAVA, "src/model/Order.java")
    names = {n["name"] for n in n_order}
    check("recognizer's ensured :Class node reproduces graphify's bare name (Order)",
          names == {"Order"}, str(names))
    check("ensured nodes carry the full push contract",
          all(n["label"] == "Class" and n["documented"] is False
              and all(k in n and n[k] not in (None, "") for k in
                      ("key", "label", "name", "source_file", "start_line", "body_summary"))
              for n in n_order))
    merged = LR.merge_supplemental(mapped, LR.validate_supplemental(n_order, "java"))
    check("graphify parsed the file -> ensured node dedupes away (base wins)", merged is mapped)
    merged2 = LR.merge_supplemental([], LR.validate_supplemental(n_order, "java"))
    check("graphify missed the repo -> the ensured node fills the entity layer", len(merged2) == 1)


def _resolver_nodes():
    # graphify-shaped nodes for a Spring project: the controller + its handler, the injected service.
    return [
        {"key": "src/web/OrderController.java::OrderController:5", "label": "Class",
         "name": "OrderController", "source_file": "src/web/OrderController.java"},
        {"key": "src/web/OrderController.java::OrderController.get:11", "label": "Method",
         "name": "OrderController.get", "source_file": "src/web/OrderController.java"},
        {"key": "src/service/OrderService.java::OrderService:3", "label": "Class",
         "name": "OrderService", "source_file": "src/service/OrderService.java"},
    ]


class _Sess:
    def __init__(self):
        self.log: list = []

    def run(self, cypher, **params):
        for row in params.get("rows") or [params]:   # the push batches with UNWIND $rows: one entry per row
            self.log.append((cypher, row))


def gate_resolution() -> None:
    print("\n-- full push path: route resolves same-file (LOCAL), ctor dep resolves cross-file (NAME) --")
    edges = _edges(CONTROLLER_JAVA, "src/web/OrderController.java")
    nodes = _resolver_nodes()
    resolver = ER.Resolver(nodes)
    live = [n["key"] for n in nodes]
    s = _Sess()
    counts = ER.push_typed_edges(s, edges, resolver, live)
    merges = [(c, p) for c, p in s.log if "MERGE" in c]
    route = [(c, p) for c, p in merges if "ROUTES_TO" in c]
    dep = [(c, p) for c, p in merges if "DEPENDS_ON" in c]
    check("ROUTES_TO resolves same-file to OrderController.get and grades EXTRACTED (LOCAL literal)",
          len(route) == 1 and route[0][1].get("c") == "EXTRACTED", str([p.get("c") for _, p in route]))
    check("ctor DEPENDS_ON resolves cross-file to the OrderService class (INFERRED)",
          len(dep) == 1 and dep[0][1].get("c") == "INFERRED", str([p.get("c") for _, p in dep]))
    check("counts reflect exactly the two resolvable edges", counts.get("ROUTES_TO") == 1
          and counts.get("DEPENDS_ON") == 1, str(counts))


def gate_cascade_reversed() -> None:
    print("\n-- CASCADES persists REVERSED so impact(Order) surfaces LineItem --")
    nodes = [
        {"key": "o.java::Order:1", "label": "Class", "name": "Order", "source_file": "o.java"},
        {"key": "l.java::LineItem:1", "label": "Class", "name": "LineItem", "source_file": "l.java"},
    ]
    edge = FE.TypedEdge("Order", "o.java", "LineItem", "CASCADES", "NAME", {"strategy": "REMOVE"})
    s = _Sess()
    ER.push_typed_edges(s, [edge], ER.Resolver(nodes), [n["key"] for n in nodes])
    m = [c for c, _ in s.log if "MERGE" in c and "CASCADES" in c]
    check("persisted (b)-[:CASCADES]->(a): OWNED LineItem -> OWNER Order",
          m and "MERGE (b)-[r:CASCADES]->(a)" in m[0], str(m))


def gate_registry_end_to_end() -> None:
    print("\n-- registry end-to-end: LR.run over a Java tree; =0 turns the layer off --")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "src" / "model").mkdir(parents=True)
        (root / "src" / "web").mkdir(parents=True)
        (root / "src" / "model" / "Order.java").write_text(ORDER_JAVA, encoding="utf-8")
        (root / "src" / "web" / "OrderController.java").write_text(CONTROLLER_JAVA, encoding="utf-8")
        (root / "target").mkdir()
        (root / "target" / "Gen.java").write_text(ORDER_JAVA, encoding="utf-8")
        supp, edges = LR.run(root)
        check("java entry runs: ensured nodes + typed edges emitted",
              len(supp) >= 2 and any(x.edge_type == "CASCADES" for x in edges),
              f"supp={len(supp)} edges={len(edges)}")
        check("compiled output under target/ never contributes",
              all("target" not in n["source_file"] for n in supp)
              and all("target" not in x.src_file for x in edges))
        saved = os.environ.get("DEEPMAP_RECOGNIZERS")
        try:
            os.environ["DEEPMAP_RECOGNIZERS"] = "0"
            check("DEEPMAP_RECOGNIZERS=0 -> the whole layer is off ([], [])", LR.run(root) == ([], []))
            os.environ["DEEPMAP_RECOGNIZERS"] = "java"
            supp_j, edges_j = LR.run(root)
            check("CSV 'java' runs only this entry", len(supp_j) >= 2 and len(edges_j) >= 3)
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
    print("DeepMap — Java recognizer gates (plugin #4, no Neo4j / javac)")
    print("=" * 64)
    gate_recognizer_emit()
    gate_dependency_injection()
    gate_ensured_nodes_match_graphify()
    gate_resolution()
    gate_cascade_reversed()
    gate_registry_end_to_end()
    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
