#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j gates for Track C — framework/runtime relationship edges.

Two halves, both pure (no Neo4j / model / network / spend):
  1. framework_edges.edges_from_module — per-framework AST recognizers (inline source fixtures, the
     confidence_gates house style): inheritance, route->handler (+method/path), FastAPI Depends,
     Flask/FastAPI hooks, click commands, ORM relations, Celery tasks, signals, reflection.
  2. edge_resolve — name resolution + typed-edge push over a FAKE session: prune-then-recreate per type,
     confidence grading (EXTRACTED same-file / INFERRED cross-file / AMBIGUOUS multi + DYNAMIC),
     self-edge exclusion, attrs carried, whitelist enforcement, :IMPORTS reconciliation, CALLS
     byte-compat (build_name_index == the inline loop), and extractor-independence (ast vs graphify keys).
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

BUILD = Path(__file__).resolve().parent.parent / "build"
sys.path.insert(0, str(BUILD))

_PASS = 0
_FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {label}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}{(' — ' + detail) if detail else ''}")


def _load(name, fname):
    spec = importlib.util.spec_from_file_location(name, BUILD / fname)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _edges(FE, src: str, rel: str = "m.py"):
    return FE.edges_from_module(ast.parse(src), rel)


def _has(edges, src_name, dst_name, edge_type, **attrs) -> bool:
    for e in edges:
        if e.src_name == src_name and e.dst_name == dst_name and e.edge_type == edge_type:
            if all(e.attrs.get(k) == v for k, v in attrs.items()):
                return True
    return False


def _types(edges):
    return sorted({e.edge_type for e in edges})


# ---- fake Neo4j session (captures Cypher + params; returns nothing) ----------
class _Sess:
    def __init__(self, log):
        self._log = log

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, cypher, **p):
        for row in p.get("rows") or [p]:   # the push batches with UNWIND $rows: one entry per row
            self._log.append((cypher, row))
        return []


# =============================== recognizers =================================

def gate_inherits(FE) -> None:
    print("\n-- STAGE 1: :INHERITS / :IMPLEMENTS (class bases) --")
    src = (
        "class A: pass\n"
        "class B(A): pass\n"
        "class M(mixins.Base): pass\n"
        "class P(Protocol): pass\n"
        "class Q(BaseInterface): pass\n"
        "class G(Generic[T]): pass\n"
    )
    e = _edges(FE, src)
    check("B(A) -> INHERITS B->A", _has(e, "B", "A", "INHERITS"), str(e))
    check("dotted base mixins.Base -> INHERITS M->Base (trailing id)", _has(e, "M", "Base", "INHERITS"), str(e))
    check("P(Protocol) -> IMPLEMENTS", _has(e, "P", "Protocol", "IMPLEMENTS"), str(e))
    check("BaseInterface base -> IMPLEMENTS", _has(e, "Q", "BaseInterface", "IMPLEMENTS"), str(e))
    check("Generic[T] subscript base -> no edge (skipped)", not any(x.src_name == "G" for x in e), str(e))
    check("no self-edge, no A->object", not _has(e, "A", "A", "INHERITS"), str(e))


def gate_routes_flask(FE) -> None:
    print("\n-- STAGE 1: :ROUTES_TO (Flask decorators, method + path) --")
    src = (
        "@app.route('/x', methods=['POST', 'PUT'])\n"
        "def create(): pass\n"
        "@app.get('/y')\n"
        "def show(): pass\n"
        "@bp.route('/z')\n"
        "def z(): pass\n"
    )
    e = _edges(FE, src)
    check("@app.route + methods -> ROUTES_TO m->create {POST,PUT}",
          _has(e, "m", "create", "ROUTES_TO", method="POST,PUT", path="/x"), str(e))
    check("@app.get -> ROUTES_TO m->show {GET,/y}",
          _has(e, "m", "show", "ROUTES_TO", method="GET", path="/y"), str(e))
    check("@bp.route no methods -> method '*'",
          _has(e, "m", "z", "ROUTES_TO", method="*", path="/z"), str(e))


def gate_routes_fastapi_depends(FE) -> None:
    print("\n-- STAGE 1/2: FastAPI :ROUTES_TO + :DEPENDS_ON (Depends in default AND Annotated) --")
    src = (
        "def get_db(): pass\n"
        "@app.post('/items')\n"
        "def create(db=Depends(get_db)): pass\n"
        "@router.get('/a')\n"
        "async def show(svc: Annotated[Svc, Depends(get_svc)]): pass\n"
    )
    e = _edges(FE, src)
    check("@app.post -> ROUTES_TO m->create {POST,/items}",
          _has(e, "m", "create", "ROUTES_TO", method="POST", path="/items"), str(e))
    check("Depends(get_db) default -> DEPENDS_ON create->get_db",
          _has(e, "create", "get_db", "DEPENDS_ON"), str(e))
    check("Annotated[..., Depends(get_svc)] -> DEPENDS_ON show->get_svc",
          _has(e, "show", "get_svc", "DEPENDS_ON"), str(e))


def gate_hooks(FE) -> None:
    print("\n-- STAGE 2: :HOOKS (Flask/FastAPI lifecycle) --")
    src = (
        "@app.before_request\n"
        "def hook(): pass\n"
        "@app.errorhandler(404)\n"
        "def nf(): pass\n"
        "@app.on_event('startup')\n"
        "def boot(): pass\n"
    )
    e = _edges(FE, src)
    check("@app.before_request -> HOOKS m->hook {before_request}",
          _has(e, "m", "hook", "HOOKS", event="before_request"), str(e))
    check("@app.errorhandler(404) (int arg) -> event 'errorhandler'",
          _has(e, "m", "nf", "HOOKS", event="errorhandler"), str(e))
    check("@app.on_event('startup') -> event 'startup'",
          _has(e, "m", "boot", "HOOKS", event="startup"), str(e))


def gate_click(FE) -> None:
    print("\n-- STAGE 1: :ROUTES_TO (click commands) --")
    src = (
        "@click.group()\n"
        "def cli(): pass\n"
        "@cli.command()\n"
        "def build(): pass\n"
        "@cli.command('deploy')\n"
        "def deploy_cmd(): pass\n"
    )
    e = _edges(FE, src)
    check("@cli.command() -> ROUTES_TO m->build {CLI}", _has(e, "m", "build", "ROUTES_TO", method="CLI"), str(e))
    check("@cli.command('deploy') -> path 'deploy'",
          _has(e, "m", "deploy_cmd", "ROUTES_TO", method="CLI", path="deploy"), str(e))
    check("@click.group() (not 'command') -> no ROUTES_TO for cli", not _has(e, "m", "cli", "ROUTES_TO"), str(e))


def gate_orm(FE) -> None:
    print("\n-- STAGE 1: ORM :REFERENCES / :HAS_MANY (SQLAlchemy + Django) --")
    sa = (
        "class Parent:\n"
        "    kids = relationship('Child')\n"
        "class Child:\n"
        "    pid = Column(ForeignKey('parent.id'))\n"
    )
    e = _edges(FE, sa)
    check("SQLAlchemy relationship('Child') -> HAS_MANY Parent->Child", _has(e, "Parent", "Child", "HAS_MANY"), str(e))
    check("Column(ForeignKey('parent.id')) -> REFERENCES Child->Parent", _has(e, "Child", "Parent", "REFERENCES"), str(e))
    dj = (
        "class Book(models.Model):\n"
        "    author = models.ForeignKey('Author')\n"
        "    tags = models.ManyToManyField('Tag')\n"
    )
    e = _edges(FE, dj)
    check("Django ForeignKey('Author') -> REFERENCES Book->Author", _has(e, "Book", "Author", "REFERENCES"), str(e))
    check("Django ManyToManyField('Tag') -> HAS_MANY Book->Tag", _has(e, "Book", "Tag", "HAS_MANY"), str(e))


def gate_celery(FE) -> None:
    print("\n-- STAGE 2: :SCHEDULED_AS (Celery tasks) --")
    src = (
        "@shared_task\n"
        "def send(): pass\n"
        "@app.task\n"
        "def crunch(): pass\n"
    )
    e = _edges(FE, src)
    check("@shared_task -> SCHEDULED_AS m->send", _has(e, "m", "send", "SCHEDULED_AS"), str(e))
    check("@app.task -> SCHEDULED_AS m->crunch", _has(e, "m", "crunch", "SCHEDULED_AS"), str(e))


def gate_signals(FE) -> None:
    print("\n-- STAGE 2: :TRIGGERS (Django @receiver, blinker .connect, emitter .on) --")
    src = (
        "@receiver(post_save, sender=User)\n"
        "def on_save(sender, **kw): pass\n"
        "user_logged_in.connect(track_login)\n"
        "ee.on('ready', boot_cb)\n"
    )
    e = _edges(FE, src)
    check("@receiver(post_save,...) -> TRIGGERS m->on_save {post_save}",
          _has(e, "m", "on_save", "TRIGGERS", signal="post_save"), str(e))
    check("sig.connect(track_login) -> TRIGGERS m->track_login",
          _has(e, "m", "track_login", "TRIGGERS"), str(e))
    check("ee.on('ready', boot_cb) -> TRIGGERS m->boot_cb {ready}",
          _has(e, "m", "boot_cb", "TRIGGERS", event="ready"), str(e))


def gate_dynamic(FE) -> None:
    print("\n-- STAGE 3: :DISPATCHES (reflection, AMBIGUOUS-only) --")
    src = (
        "def dispatch(self):\n"
        "    getattr(self, 'handle')()\n"
        "def truly_dynamic(self, name):\n"
        "    getattr(self, name)()\n"
    )
    e = _edges(FE, src)
    check("getattr(obj,'handle')() -> DISPATCHES dispatch->handle (DYNAMIC)",
          _has(e, "dispatch", "handle", "DISPATCHES") and
          any(x.conf_hint == "DYNAMIC" for x in e if x.edge_type == "DISPATCHES"), str(e))
    check("getattr(obj, VAR)() -> no edge (no string target)",
          not any(x.src_name == "truly_dynamic" for x in e), str(e))


def gate_method_naming(FE) -> None:
    print("\n-- handler names match the extractor (Class.method for methods) --")
    src = (
        "class View:\n"
        "    @app.get('/m')\n"
        "    def handler(self): pass\n"
    )
    e = _edges(FE, src)
    check("method route handler named 'View.handler'",
          _has(e, "m", "View.handler", "ROUTES_TO", method="GET", path="/m"), str(e))


def gate_shell_isolation(FE) -> None:
    """extract_framework_edges skips an unparseable file (logged) and still processes good ones."""
    import io
    import contextlib
    import tempfile
    print("\n-- extract shell: per-file isolation (bad file skipped, good file kept) --")
    with tempfile.TemporaryDirectory() as d:
        dp = Path(d)
        (dp / "good.py").write_text("class A: pass\nclass B(A): pass\n", encoding="utf-8")
        (dp / "bad.py").write_text("class Oops(:\n", encoding="utf-8")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            e = FE.extract_framework_edges(dp)
        check("good file's INHERITS edge extracted", _has(e, "B", "A", "INHERITS"), str(e))
        check("unparseable file skipped AND logged", "bad.py" in err.getvalue(), repr(err.getvalue()))


# ================================ push layer =================================

def _mk_nodes():
    """A small graph exercising every grading path (same-file single / cross-file / duplicate name)."""
    def n(key, name, sf, label="Function", line=1):
        return {"key": key, "label": label, "name": name, "source_file": sf, "start_line": line,
                "body_summary": "s"}
    return [
        n("m.py::module", "m", "m.py", "Module"),
        n("m.py::Base:1", "Base", "m.py", "Class"),
        n("m.py::B:3", "B", "m.py", "Class"),
        n("other.py::C:1", "C", "other.py", "Class"),
        n("m.py::Dup:5", "Dup", "m.py", "Class"),           # 'Dup' twice IN THE SAME FILE -> AMBIGUOUS
        n("m.py::Dup:7", "Dup", "m.py", "Class"),
        n("m.py::create:9", "create", "m.py", "Function"),
        n("m.py::get_db:11", "get_db", "m.py", "Function"),
    ]


def gate_push_typed_edges(ER, FE) -> None:
    print("\n-- push_typed_edges: prune-then-recreate, grading, attrs, whitelist, self-edge --")
    TE = FE.TypedEdge
    nodes = _mk_nodes()
    live = [x["key"] for x in nodes]
    resolver = ER.Resolver(nodes)
    edges = [
        TE("B", "m.py", "Base", "INHERITS", "LOCAL", {}),          # same-file single -> EXTRACTED
        TE("B", "m.py", "C", "INHERITS", "LOCAL", {}),             # cross-file single -> INFERRED
        TE("B", "m.py", "Dup", "INHERITS", "LOCAL", {}),           # 2 targets -> AMBIGUOUS (x2)
        TE("m", "m.py", "create", "ROUTES_TO", "LOCAL", {"method": "POST", "path": "/x"}),
        TE("create", "m.py", "get_db", "DEPENDS_ON", "LOCAL", {}),
        TE("create", "m.py", "get_db", "DISPATCHES", "DYNAMIC", {}),  # DYNAMIC -> AMBIGUOUS
        TE("B", "m.py", "B", "INHERITS", "LOCAL", {}),             # self-edge -> excluded
        TE("B", "m.py", "Base", "BOGUS_TYPE", "LOCAL", {}),        # not whitelisted -> skipped
    ]
    log = []
    counts = ER.push_typed_edges(_Sess(log), edges, resolver, live)
    merges = [(c, p) for (c, p) in log if "MERGE (a)-[r:" in c]
    deletes = [(c, p) for (c, p) in log if "DELETE r" in c]

    def edge(et, a_key, b_key):
        for c, p in merges:
            if f"r:{et}]" in c and p.get("a") == a_key and p.get("b") == b_key:
                return p
        return None

    check("prune DELETE emitted for each present type",
          all(any(f":{et}]" in c for c, _ in deletes) for et in ("INHERITS", "ROUTES_TO", "DEPENDS_ON", "DISPATCHES")),
          str([c for c, _ in deletes]))
    # prune must come BEFORE any recreate of that type
    first_del_inh = next((i for i, (c, _) in enumerate(log) if "DELETE r" in c and ":INHERITS]" in c), 10**9)
    first_mrg_inh = next((i for i, (c, _) in enumerate(log) if "MERGE (a)-[r:INHERITS]" in c), -1)
    check("INHERITS pruned BEFORE recreated", first_del_inh < first_mrg_inh, f"del@{first_del_inh} mrg@{first_mrg_inh}")

    check("B->Base same-file single -> EXTRACTED",
          (edge("INHERITS", "m.py::B:3", "m.py::Base:1") or {}).get("c") == "EXTRACTED")
    check("B->C cross-file single -> INFERRED",
          (edge("INHERITS", "m.py::B:3", "other.py::C:1") or {}).get("c") == "INFERRED")
    dup_confs = [p.get("c") for c, p in merges if "r:INHERITS]" in c and p.get("a") == "m.py::B:3"
                 and p.get("b") in ("m.py::Dup:5", "m.py::Dup:7")]
    check("B->Dup (2 same-file targets) -> AMBIGUOUS x2", dup_confs == ["AMBIGUOUS", "AMBIGUOUS"], str(dup_confs))
    rt = edge("ROUTES_TO", "m.py::module", "m.py::create:9")
    check("module->create ROUTES_TO resolves module source + EXTRACTED", (rt or {}).get("c") == "EXTRACTED", str(rt))
    check("ROUTES_TO carries {method,path} attrs",
          rt is not None and rt.get("attrs", {}).get("method") == "POST" and rt["attrs"].get("path") == "/x", str(rt))
    check("DEPENDS_ON create->get_db EXTRACTED",
          (edge("DEPENDS_ON", "m.py::create:9", "m.py::get_db:11") or {}).get("c") == "EXTRACTED")
    check("DISPATCHES (DYNAMIC) single target still AMBIGUOUS",
          (edge("DISPATCHES", "m.py::create:9", "m.py::get_db:11") or {}).get("c") == "AMBIGUOUS")
    check("self-edge B->B excluded", edge("INHERITS", "m.py::B:3", "m.py::B:3") is None)
    check("unknown edge type not written", not any("BOGUS_TYPE" in c for c, _ in log))
    check("counts reflect written edges (INHERITS==4: Base+C+Dup*2)", counts.get("INHERITS") == 4, str(counts))


def gate_push_empty_and_nolive(ER, FE) -> None:
    print("\n-- push_typed_edges: empty / no-live are safe no-ops --")
    log = []
    check("no typed edges -> {} and no Cypher", ER.push_typed_edges(_Sess(log), [], ER.Resolver(_mk_nodes()), ["m.py::B:3"]) == {} and not log)
    TE = FE.TypedEdge
    log2 = []
    out = ER.push_typed_edges(_Sess(log2), [TE("B", "m.py", "Base", "INHERITS", "LOCAL", {})], ER.Resolver(_mk_nodes()), [])
    check("no live keys -> {} and no Cypher", out == {} and not log2)


def gate_imports_reconcile(ER) -> None:
    print("\n-- :IMPORTS reconciliation (ast tuple vs graphify tuple) --")
    nodes = [
        {"key": "svc.py::module", "label": "Module", "name": "svc", "source_file": "svc.py", "start_line": 1, "body_summary": "s"},
        {"key": "helpers.py::module", "label": "Module", "name": "helpers", "source_file": "helpers.py", "start_line": 1, "body_summary": "s"},
        {"key": "gfx_svc_mod", "label": "Module", "name": "svc", "source_file": "svc.py", "start_line": 1, "body_summary": "s"},
    ]
    node_keys = {n["key"] for n in nodes}
    resolver = ER.Resolver(nodes)
    # ast shape: (source_file, dotted module). graphify shape: (src_key, target_name). external -> dropped.
    imports = [
        ("svc.py", "pkg.helpers"),        # ast: svc module -> resolve last seg 'helpers' -> helpers module
        ("svc.py", "os"),                 # ast: external -> no node -> dropped
        ("gfx_svc_mod", "helpers"),       # graphify: src already a key -> straight through
        (123, "bad"),                     # malformed -> dropped
    ]
    norm = ER.normalize_imports(imports, resolver, node_keys)
    check("ast (svc.py,'pkg.helpers') -> (svc key, 'helpers')", ("svc.py::module", "helpers") in norm, str(norm))
    check("graphify (gfx_svc_mod,'helpers') passes through", ("gfx_svc_mod", "helpers") in norm, str(norm))
    check("ast 'os' shape-mapped to (svc key,'os') (drop happens at push, not normalize)",
          ("svc.py::module", "os") in norm, str(norm))
    check("malformed (123,'bad') dropped by normalize", all(n[0] != 123 for n in norm), str(norm))
    log = []
    made = ER.push_imports(_Sess(log), imports, resolver, [n["key"] for n in nodes], node_keys)
    merges = [(c, p) for c, p in log if "MERGE" in c]
    check("push_imports prunes :IMPORTS on live keys first", any("DELETE r" in c and ":IMPORTS]" in c for c, _ in log))
    check("external 'os' -> no node -> NO import edge (only 2 helpers edges)", made == 2, str(made))
    check("every import edge points at the resolvable helpers module",
          bool(merges) and all(p.get("b") == "helpers.py::module" for c, p in merges), str([p for c, p in merges]))
    check("import edge confidence EXTRACTED (literal)", all(p.get("c") == "EXTRACTED" for c, p in merges))


def gate_name_index_compat(ER) -> None:
    print("\n-- build_name_index == the inline CALLS loop (byte-compat) --")
    nodes = _mk_nodes()
    inline: dict = {}
    for n in nodes:
        for nm in {n["name"], n["name"].split(".")[-1]}:
            inline.setdefault(nm, []).append(n["key"])
    check("build_name_index reproduces the inline index exactly", ER.build_name_index(nodes) == inline)


def gate_extractor_independence(ER, FE) -> None:
    print("\n-- resolution is extractor-INDEPENDENT (ast keys vs graphify keys) --")
    TE = FE.TypedEdge
    edge = [TE("B", "m.py", "Base", "INHERITS", "LOCAL", {})]
    ast_nodes = [
        {"key": "m.py::B:3", "label": "Class", "name": "B", "source_file": "m.py", "start_line": 3, "body_summary": "s"},
        {"key": "m.py::Base:1", "label": "Class", "name": "Base", "source_file": "m.py", "start_line": 1, "body_summary": "s"},
    ]
    gfx_nodes = [   # SAME name+source_file, graphify-style opaque keys
        {"key": "pkg_m_B", "label": "Class", "name": "B", "source_file": "m.py", "start_line": 3, "body_summary": "s"},
        {"key": "pkg_m_Base", "label": "Class", "name": "Base", "source_file": "m.py", "start_line": 1, "body_summary": "s"},
    ]
    for tag, nodes, exp in (("ast", ast_nodes, ("m.py::B:3", "m.py::Base:1")),
                            ("graphify", gfx_nodes, ("pkg_m_B", "pkg_m_Base"))):
        log = []
        ER.push_typed_edges(_Sess(log), edge, ER.Resolver(nodes), [n["key"] for n in nodes])
        made = [(p.get("a"), p.get("b")) for c, p in log if "MERGE (a)-[r:INHERITS]" in c]
        check(f"{tag}: INHERITS resolves to that extractor's keys {exp}", made == [exp], str(made))


def gate_local_same_file_resolution(ER, FE) -> None:
    """A LOCAL edge (route/DI/inheritance) whose TARGET NAME also exists in other files must resolve
    ONLY to the same-file target — never fan out to every same-named symbol repo-wide. This is the
    fastapi docs_src explosion (handler names like 'read_item'/'main' repeat across hundreds of
    tutorials → 21,750 phantom cross-file ROUTES_TO on the live graph before the fix)."""
    print("\n-- LOCAL edges resolve same-file-first (no cross-file name explosion) --")
    TE = FE.TypedEdge
    nodes = [
        {"key": "a.py::module", "label": "Module", "name": "a", "source_file": "a.py", "start_line": 1, "body_summary": "s"},
        {"key": "a.py::read_item:5", "label": "Function", "name": "read_item", "source_file": "a.py", "start_line": 5, "body_summary": "s"},
        {"key": "b.py::read_item:5", "label": "Function", "name": "read_item", "source_file": "b.py", "start_line": 5, "body_summary": "s"},
        {"key": "c.py::read_item:5", "label": "Function", "name": "read_item", "source_file": "c.py", "start_line": 5, "body_summary": "s"},
    ]
    resolver = ER.Resolver(nodes)
    log = []
    ER.push_typed_edges(_Sess(log), [TE("a", "a.py", "read_item", "ROUTES_TO", "LOCAL", {"method": "GET", "path": "/x"})],
                        resolver, [n["key"] for n in nodes])
    merged = [(p.get("a"), p.get("b"), p.get("c")) for c, p in log if "MERGE (a)-[r:ROUTES_TO]" in c]
    check("route resolves ONLY to the same-file handler (not b.py/c.py) -> EXTRACTED",
          merged == [("a.py::module", "a.py::read_item:5", "EXTRACTED")], str(merged))
    # a genuinely cross-file LOCAL target (a base class defined only in another file) still resolves
    # globally — the same-file preference must not drop legitimate cross-file relationships.
    nodes2 = [
        {"key": "sub.py::Svc:3", "label": "Class", "name": "Svc", "source_file": "sub.py", "start_line": 3, "body_summary": "s"},
        {"key": "base.py::Base:1", "label": "Class", "name": "Base", "source_file": "base.py", "start_line": 1, "body_summary": "s"},
    ]
    log2 = []
    ER.push_typed_edges(_Sess(log2), [TE("Svc", "sub.py", "Base", "INHERITS", "LOCAL", {})],
                        ER.Resolver(nodes2), [n["key"] for n in nodes2])
    m2 = [(p.get("a"), p.get("b"), p.get("c")) for c, p in log2 if "MERGE (a)-[r:INHERITS]" in c]
    check("cross-file base still resolves globally (single candidate -> INFERRED)",
          m2 == [("sub.py::Svc:3", "base.py::Base:1", "INFERRED")], str(m2))
    # a DECORATOR-anchored edge (ROUTES_TO) whose handler is NOT a node in the src file — a route
    # handler nested inside a test function, which the extractor never node-ifies — must be DROPPED,
    # never resolved to a same-named handler in another file (the fastapi tests-vs-docs_src phantoms).
    nodes3 = [
        {"key": "t.py::module", "label": "Module", "name": "t", "source_file": "t.py", "start_line": 1, "body_summary": "s"},
        {"key": "other.py::get_item:5", "label": "Function", "name": "get_item", "source_file": "other.py", "start_line": 5, "body_summary": "s"},
    ]
    log3 = []
    ER.push_typed_edges(_Sess(log3), [TE("t", "t.py", "get_item", "ROUTES_TO", "LOCAL", {"method": "GET", "path": "/x"})],
                        ER.Resolver(nodes3), [n["key"] for n in nodes3])
    check("decorator route to a NON-same-file handler is DROPPED (no cross-file phantom)",
          not any("MERGE (a)-[r:ROUTES_TO]" in c for c, _ in log3), str([c for c, _ in log3]))
    # but a NON-strict edge (DI) to a provider imported from another module DOES resolve cross-file.
    nodes4 = [
        {"key": "h.py::handler:3", "label": "Function", "name": "handler", "source_file": "h.py", "start_line": 3, "body_summary": "s"},
        {"key": "deps.py::get_db:1", "label": "Function", "name": "get_db", "source_file": "deps.py", "start_line": 1, "body_summary": "s"},
    ]
    log4 = []
    ER.push_typed_edges(_Sess(log4), [TE("handler", "h.py", "get_db", "DEPENDS_ON", "LOCAL", {})],
                        ER.Resolver(nodes4), [n["key"] for n in nodes4])
    m4 = [(p.get("a"), p.get("b"), p.get("c")) for c, p in log4 if "MERGE (a)-[r:DEPENDS_ON]" in c]
    check("DI to an imported (cross-file) provider still resolves (INFERRED)",
          m4 == [("h.py::handler:3", "deps.py::get_db:1", "INFERRED")], str(m4))


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 64)
    print("DeepMap — framework/runtime edge gates (Track C; no Neo4j, no spend)")
    print("=" * 64)

    FE = _load("dm_framework_edges", "framework_edges.py")
    ER = _load("dm_edge_resolve", "edge_resolve.py")

    gate_inherits(FE)
    gate_routes_flask(FE)
    gate_routes_fastapi_depends(FE)
    gate_hooks(FE)
    gate_click(FE)
    gate_orm(FE)
    gate_celery(FE)
    gate_signals(FE)
    gate_dynamic(FE)
    gate_method_naming(FE)
    gate_shell_isolation(FE)

    gate_push_typed_edges(ER, FE)
    gate_push_empty_and_nolive(ER, FE)
    gate_imports_reconcile(ER)
    gate_name_index_compat(ER)
    gate_extractor_independence(ER, FE)
    gate_local_same_file_resolution(ER, FE)

    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
