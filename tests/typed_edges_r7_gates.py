#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Round-7 W7 gates — recognizer coverage gaps + phantom typed edges of a disabled recognizer. No Neo4j.

Each section is a sweep item that was NOT_FIXED at the end of round 6 (scratchpad sweep_result.json):

  A  T8d-X1  APScheduler: @sched.scheduled_job(...) and sched.add_job(func, ...) -> :SCHEDULED_AS.
  B  T4d-G1  requests-style hooks: r.register_hook("response", self.handle_401), hooks={...} keyword,
             s.hooks["response"].append(f) -> :HOOKS (event attr; self.m -> Class.m, EXTRACTED).
  C  T8d-X1  route-prefix composition inside ONE module: APIRouter(prefix=) / Blueprint(url_prefix=)
             and a same-module include_router / register_blueprint (Flask's override rule).
  D  T8d-X1  route-prefix composition ACROSS modules (import-resolved mounts, nested routers, a Flask
             app factory with function-level imports) through the registry AND the legacy shell,
             which must stay byte-equal to each other (the registry parity contract).
  E  T8d-B11 C# fluent HasMany(i => i.Lines) / HasOne(o => o.Buyer): the target comes from the entity's
             navigation-property TYPE (List<InvoiceLine> Lines), not a singularized guess ("Line"),
             so the declared cascade is no longer dropped; the CASCADES edge follows.
  F  T8#9    a recognizer DISABLED by DEEPMAP_RECOGNIZERS leaves no phantom typed edges on its orphan
             nodes: the push prunes that family's typed edges (never :IMPORTS/:CALLS, bounded, and no
             statement at all when nothing is disabled -> default byte-identical).
  G  no-regression: shapes the round did not target keep their exact edges / attrs. The `.connect` / `.on`
     lock follows the variable guard ISS-build:015 extended to them (2026-09-29): a name the module never
     binds stays an exact NAME edge; a parameter handler or a module's connect() emits nothing
     (tests/o8_trigger_guard_r283_gates.py covers the full rule).

Hermetic: temp dirs only, no Neo4j, no network, no ~/.deepmap access.
"""
from __future__ import annotations

import ast
import contextlib
import io
import os
import re
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build"
sys.path.insert(0, str(BUILD))

import framework_edges as FE            # noqa: E402
import edge_resolve as ER               # noqa: E402
import language_recognizers as LR       # noqa: E402
import csharp_edges as CS               # noqa: E402

_PASS = _FAIL = 0


def check(desc: str, cond, detail="") -> None:
    global _PASS, _FAIL
    ok = bool(cond)
    _PASS += ok
    _FAIL += (not ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {desc}" + ("" if ok else f"  -- {str(detail)[:600]}"))


def _safe(fn, *a, **k):
    """Run a gate body; an AttributeError/TypeError on the pre-fix code is a FAIL, never a crash."""
    try:
        return fn(*a, **k)
    except Exception as exc:  # noqa: BLE001
        check(f"{fn.__name__} ran without raising", False, f"{type(exc).__name__}: {exc}")
        return None


def _edges(src: str, rel: str = "m.py"):
    return FE.edges_from_module(ast.parse(src), rel)


def _find(edges, et, src=None, dst=None):
    return [e for e in edges if e.edge_type == et and (src is None or e.src_name == src)
            and (dst is None or e.dst_name == dst)]


def _write_tree(root: Path, files: dict) -> None:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")


@contextlib.contextmanager
def _env(**kv):
    saved = {k: os.environ.get(k) for k in kv}
    try:
        for k, v in kv.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _node(key, label, name, sf, line=1):
    return {"key": key, "label": label, "name": name, "source_file": sf, "start_line": line,
            "documented": False, "body_summary": f"{name}: x"}


# =====================================================================================================
# A — APScheduler
# =====================================================================================================
def gate_a_apscheduler() -> None:
    print("\n-- A (T8d-X1): APScheduler scheduled_job / add_job -> :SCHEDULED_AS --")
    src = (
        "from apscheduler.schedulers.background import BackgroundScheduler\n"
        "sched = BackgroundScheduler()\n"
        "@sched.scheduled_job('interval', minutes=3)\n"
        "def poll(): pass\n"
        "@sched.scheduled_job(trigger='cron', hour=2)\n"
        "def nightly(): pass\n"
        "def cleanup(): pass\n"
        "sched.add_job(cleanup, 'interval', hours=1)\n"
        "sched.add_job(func=nightly, trigger=CronTrigger(hour=3))\n"
        "sched.add_job('app.jobs:rebuild_index', 'date')\n"
        "sched.add_job(tasks.send_digest, 'cron', day_of_week='mon')\n"
        "class Worker:\n"
        "    def start(self):\n"
        "        self.sched.add_job(self.tick, 'interval', seconds=5)\n"
        "    def tick(self): pass\n"
    )
    e = _edges(src)
    sa = _find(e, "SCHEDULED_AS")
    check("@sched.scheduled_job('interval') -> SCHEDULED_AS m->poll (LOCAL, trigger=interval)",
          any(x.src_name == "m" and x.dst_name == "poll" and x.conf_hint == "LOCAL"
              and x.attrs.get("trigger") == "interval" for x in sa), sa)
    check("@sched.scheduled_job(trigger='cron') -> SCHEDULED_AS m->nightly (trigger=cron)",
          any(x.dst_name == "nightly" and x.attrs.get("trigger") == "cron" and x.conf_hint == "LOCAL"
              for x in sa), sa)
    check("sched.add_job(cleanup, 'interval') -> SCHEDULED_AS m->cleanup (trigger=interval)",
          any(x.src_name == "m" and x.dst_name == "cleanup" and x.attrs.get("trigger") == "interval"
              for x in sa), sa)
    check("add_job(func=nightly, trigger=CronTrigger(...)) -> SCHEDULED_AS m->nightly (trigger=CronTrigger)",
          any(x.dst_name == "nightly" and x.attrs.get("trigger") == "CronTrigger" for x in sa), sa)
    check("add_job('app.jobs:rebuild_index') textual reference -> SCHEDULED_AS m->rebuild_index",
          any(x.dst_name == "rebuild_index" for x in sa), sa)
    check("add_job(tasks.send_digest, ...) -> SCHEDULED_AS m->send_digest (NAME, cross-module)",
          any(x.dst_name == "send_digest" and x.conf_hint == "NAME" for x in sa), sa)
    check("self.sched.add_job(self.tick) in a method -> SCHEDULED_AS Worker->Worker.tick (LOCAL)",
          any(x.src_name == "Worker" and x.dst_name == "Worker.tick" and x.conf_hint == "LOCAL"
              for x in sa), sa)
    neg = _edges("q.add_job()\nq.add_job(42)\nsched.add_job(lambda: None, 'interval')\n")
    check("add_job with no / non-callable / lambda target emits nothing", not _find(neg, "SCHEDULED_AS"), neg)
    var = _edges("def schedule(sched, job):\n    sched.add_job(job, 'interval')\n"
                 "def cleanup(): pass\n"
                 "handler = partial(cleanup, 1)\nsched.add_job(handler)\n"
                 "def boot():\n    local_fn = make()\n    sched.add_job(local_fn)\n"
                 "    from app.jobs import rebuild\n    sched.add_job(rebuild, 'cron')\n")
    vs = _find(var, "SCHEDULED_AS")
    check("a PARAMETER / local / module-VARIABLE job names no symbol -> no edge (never bound by spelling)",
          not any(x.dst_name in ("job", "handler", "local_fn") for x in vs), vs)
    check("a function-level IMPORTED job is still a real cross-module NAME target",
          any(x.dst_name == "rebuild" and x.conf_hint == "NAME" and x.attrs == {"trigger": "cron"}
              for x in vs), vs)
    celery = _edges("@shared_task\ndef send(): pass\n@app.task\ndef crunch(): pass\n")
    check("Celery tasks unchanged (attrs exactly {'task': handler})",
          sorted((x.dst_name, tuple(sorted(x.attrs.items()))) for x in celery)
          == [("crunch", (("task", "crunch"),)), ("send", (("task", "send"),))], celery)

    # Resolution: a same-file decorated job grades EXTRACTED; the method job resolves to its Method.
    nodes = [_node("m.py::m:1", "Module", "m", "m.py"),
             _node("m.py::poll:4", "Function", "poll", "m.py", 4),
             _node("m.py::cleanup:7", "Function", "cleanup", "m.py", 7),
             _node("m.py::Worker:12", "Class", "Worker", "m.py", 12),
             _node("m.py::Worker.tick:15", "Method", "Worker.tick", "m.py", 15)]
    rows = ER.resolve_typed_edges(e, ER.Resolver(nodes), [n["key"] for n in nodes])
    got = {(r[1], r[2], r[3]) for r in rows if r[0] == "SCHEDULED_AS"}
    check("resolved: m -> poll EXTRACTED (decorator, same file)",
          ("m.py::m:1", "m.py::poll:4", "EXTRACTED") in got, got)
    check("resolved: m -> cleanup (add_job, same-file function) persists",
          any(r[0] == "m.py::m:1" and r[1] == "m.py::cleanup:7" for r in got), got)
    check("resolved: Worker -> Worker.tick EXTRACTED (self.m in its own class)",
          ("m.py::Worker:12", "m.py::Worker.tick:15", "EXTRACTED") in got, got)


# =====================================================================================================
# B — requests-style hooks
# =====================================================================================================
_REQUESTS_AUTH = (
    "class HTTPDigestAuth(AuthBase):\n"
    "    def handle_401(self, r, **kwargs):\n"
    "        return r\n"
    "    def handle_redirect(self, r, **kwargs):\n"
    "        pass\n"
    "    def __call__(self, r):\n"
    "        r.register_hook('response', self.handle_401)\n"
    "        r.register_hook('response', self.handle_redirect)\n"
    "        r.register_hook('response', self.inherited_hook)\n"
    "        return r\n"
)


def gate_b_register_hook() -> None:
    print("\n-- B (T4d-G1): requests register_hook / hooks= / hooks[...].append -> :HOOKS --")
    e = _edges(_REQUESTS_AUTH, "requests/auth.py")
    hk = _find(e, "HOOKS")
    check("r.register_hook('response', self.handle_401) -> HOOKS HTTPDigestAuth->HTTPDigestAuth.handle_401",
          any(x.src_name == "HTTPDigestAuth" and x.dst_name == "HTTPDigestAuth.handle_401"
              and x.attrs == {"event": "response"} and x.conf_hint == "LOCAL" for x in hk), hk)
    check("the second hook (handle_redirect) is recognised too",
          any(x.dst_name == "HTTPDigestAuth.handle_redirect" for x in hk), hk)
    check("self.m NOT defined in the class (inherited) -> bare NAME target, never Class.m",
          any(x.dst_name == "inherited_hook" and x.conf_hint == "NAME" for x in hk)
          and not any(x.dst_name == "HTTPDigestAuth.inherited_hook" for x in hk), hk)
    src2 = (
        "import requests\n"
        "def log_it(r, *a, **k): pass\n"
        "def check_it(r, *a, **k): pass\n"
        "requests.get('http://x', hooks={'response': [log_it, check_it]})\n"
        "requests.post('http://x', hooks=dict(response=audit.record))\n"
        "s = requests.Session()\n"
        "s.hooks['response'].append(log_it)\n"
    )
    e2 = _edges(src2)
    h2 = _find(e2, "HOOKS")
    check("hooks={'response': [log_it, check_it]} -> HOOKS m->log_it and m->check_it {event: response}",
          {("log_it", "response"), ("check_it", "response")}
          <= {(x.dst_name, x.attrs.get("event")) for x in h2 if x.src_name == "m"}, h2)
    check("hooks=dict(response=audit.record) -> HOOKS m->record",
          any(x.dst_name == "record" and x.attrs.get("event") == "response" for x in h2), h2)
    check("s.hooks['response'].append(log_it) -> HOOKS m->log_it",
          sum(1 for x in h2 if x.dst_name == "log_it") >= 2, h2)
    neg = _edges("t.register_hook(fn)\nm.register_forward_hook(fn)\nx.register_hook(EVENT, fn)\n"
                 "f(hooks=None)\nf(hooks=make_hooks())\n")
    check("torch-style one-arg register_hook / non-literal event / non-dict hooks= -> no edge",
          not _find(neg, "HOOKS"), neg)

    # End-to-end resolution: the benchmark's missing edge now exists and grades EXTRACTED.
    rel = "requests/auth.py"
    nodes = [_node(f"{rel}::auth:1", "Module", "auth", rel),
             _node(f"{rel}::HTTPDigestAuth:1", "Class", "HTTPDigestAuth", rel),
             _node(f"{rel}::HTTPDigestAuth.handle_401:2", "Method", "HTTPDigestAuth.handle_401", rel, 2),
             _node(f"{rel}::HTTPDigestAuth.handle_redirect:4", "Method", "HTTPDigestAuth.handle_redirect",
                   rel, 4),
             _node(f"{rel}::HTTPDigestAuth.__call__:6", "Method", "HTTPDigestAuth.__call__", rel, 6)]
    rows = ER.resolve_typed_edges(e, ER.Resolver(nodes), [n["key"] for n in nodes])
    got = {(r[1], r[2], r[3]) for r in rows if r[0] == "HOOKS"}
    check("resolved: HTTPDigestAuth -[:HOOKS {EXTRACTED}]-> HTTPDigestAuth.handle_401",
          (f"{rel}::HTTPDigestAuth:1", f"{rel}::HTTPDigestAuth.handle_401:2", "EXTRACTED") in got, got)
    check("HOOKS is in the Python entry's vocabulary AND its impact walk (no retrieval change needed)",
          "HOOKS" in LR.PYTHON.edge_types and "HOOKS" in LR.PYTHON.impact_edge_types)


# =====================================================================================================
# C — route prefixes inside one module
# =====================================================================================================
def _route(edges, handler):
    r = [x for x in edges if x.edge_type == "ROUTES_TO" and x.dst_name == handler]
    return r[0] if len(r) == 1 else None


def gate_c_prefix_local() -> None:
    print("\n-- C (T8d-X1): APIRouter(prefix=) / Blueprint(url_prefix=) composed inside one module --")
    fa = (
        "from fastapi import APIRouter, FastAPI\n"
        "router = APIRouter(prefix='/billing', tags=['billing'])\n"
        "@router.get('/invoices')\n"
        "def list_invoices(): pass\n"
        "@router.post('')\n"
        "def create_invoice(): pass\n"
        "@router.websocket('/ws')\n"
        "async def feed(ws): pass\n"
    )
    e = _edges(fa)
    r = _route(e, "list_invoices")
    check("APIRouter(prefix='/billing') + @router.get('/invoices') -> path '/billing/invoices'",
          r is not None and r.attrs.get("path") == "/billing/invoices" and r.attrs.get("method") == "GET", r)
    r = _route(e, "create_invoice")
    check("@router.post('') -> path '/billing' (FastAPI concatenation)",
          r is not None and r.attrs.get("path") == "/billing", r)
    r = _route(e, "feed")
    check("@router.websocket('/ws') -> '/billing/ws' (method WS)",
          r is not None and r.attrs.get("path") == "/billing/ws" and r.attrs.get("method") == "WS", r)

    fa2 = fa + "app = FastAPI()\napp.include_router(router, prefix='/api/v1')\n"
    r = _route(_edges(fa2), "list_invoices")
    check("same-module app.include_router(router, prefix='/api/v1') -> '/api/v1/billing/invoices'",
          r is not None and r.attrs.get("path") == "/api/v1/billing/invoices", r)

    fl = (
        "from flask import Blueprint, Flask\n"
        "bp = Blueprint('admin', __name__, url_prefix='/admin')\n"
        "@bp.route('/users', methods=['GET'])\n"
        "def users(): pass\n"
        "@bp.get('/')\n"
        "def home(): pass\n"
        "pos = Blueprint('pos', __name__, None, None, None, '/pos')\n"
        "@pos.route('/x')\n"
        "def px(): pass\n"
    )
    e = _edges(fl)
    r = _route(e, "users")
    check("Blueprint(url_prefix='/admin') + @bp.route('/users') -> '/admin/users'",
          r is not None and r.attrs.get("path") == "/admin/users" and r.attrs.get("method") == "GET", r)
    r = _route(e, "home")
    check("@bp.get('/') -> '/admin/' (Flask's rstrip/lstrip join)",
          r is not None and r.attrs.get("path") == "/admin/", r)
    r = _route(e, "px")
    check("Blueprint's 6th positional url_prefix is honoured -> '/pos/x'",
          r is not None and r.attrs.get("path") == "/pos/x", r)
    fl2 = fl + "app = Flask(__name__)\napp.register_blueprint(bp, url_prefix='/staff')\n"
    r = _route(_edges(fl2), "users")
    check("register_blueprint(bp, url_prefix='/staff') OVERRIDES the blueprint's own prefix -> '/staff/users'",
          r is not None and r.attrs.get("path") == "/staff/users", r)
    fl3 = fl + "app = Flask(__name__)\napp.register_blueprint(bp)\n"
    r = _route(_edges(fl3), "users")
    check("register_blueprint(bp) with no url_prefix keeps '/admin/users'",
          r is not None and r.attrs.get("path") == "/admin/users", r)

    dyn = ("router = APIRouter(prefix=settings.API_PREFIX)\n"
           "@router.get('/items')\ndef items(): pass\n")
    r = _route(_edges(dyn), "items")
    check("non-literal prefix: known part kept + attrs.prefix_unresolved names the expression",
          r is not None and r.attrs.get("path") == "/items"
          and r.attrs.get("prefix_unresolved") == "settings.API_PREFIX", r)

    multi = ("router = APIRouter(prefix='/b')\n@router.get('/i')\ndef i(): pass\n"
             "app = FastAPI()\napp.include_router(router, prefix='/v2')\n"
             "app.include_router(router, prefix='/v1')\n")
    r = _route(_edges(multi), "i")
    check("a router mounted twice -> ONE edge, path = first sorted, paths = both (sorted)",
          r is not None and r.attrs.get("path") == "/v1/b/i"
          and r.attrs.get("paths") == ["/v1/b/i", "/v2/b/i"], r)

    cyc = ("a = APIRouter(prefix='/a')\nb = APIRouter(prefix='/b')\n"
           "a.include_router(b)\nb.include_router(a)\n@a.get('/x')\ndef ax(): pass\n")
    r = _safe(lambda: _route(_edges(cyc), "ax"))
    check("a mount CYCLE terminates and still yields a route edge", r is not None, r)
    plain = ("router = APIRouter()\n@router.get('/a')\ndef a(): pass\n"
             "bp = Blueprint('b', __name__)\n@bp.route('/b')\ndef b(): pass\n")
    got = sorted((x.dst_name, tuple(sorted(x.attrs.items()))) for x in _edges(plain))
    check("prefix-LESS APIRouter()/Blueprint() routes keep attrs exactly {method, path}",
          got == [("a", (("method", "GET"), ("path", "/a"))), ("b", (("method", "*"), ("path", "/b")))], got)
    conflict = ("router = APIRouter(prefix='/x')\nrouter = APIRouter(prefix='/y')\n"
                "@router.get('/i')\ndef i(): pass\n")
    r = _route(_edges(conflict), "i")
    check("a router name bound to TWO declarations is never guessed (path stays '/i')",
          r is not None and r.attrs == {"method": "GET", "path": "/i"}, r)


# =====================================================================================================
# D — route prefixes across modules (registry + legacy shell parity)
# =====================================================================================================
FASTAPI_APP = {
    "app/__init__.py": "",
    "app/routers/__init__.py": "",
    "app/routers/billing.py": (
        "from fastapi import APIRouter\n"
        "router = APIRouter(prefix='/billing')\n"
        "@router.get('/invoices')\n"
        "def list_invoices(): pass\n"),
    "app/routers/users.py": (
        "from fastapi import APIRouter\n"
        "router = APIRouter(prefix='/users')\n"
        "@router.get('/{uid}')\n"
        "def get_user(uid): pass\n"),
    "app/api.py": (
        "from fastapi import APIRouter\n"
        "from app.routers import users\n"
        "api_router = APIRouter()\n"
        "api_router.include_router(users.router, prefix='/v2')\n"),
    "app/main.py": (
        "from fastapi import FastAPI\n"
        "from .routers import billing\n"
        "from .api import api_router\n"
        "from .routers.users import router as users_router\n"
        "app = FastAPI()\n"
        "app.include_router(billing.router, prefix='/api')\n"
        "app.include_router(api_router, prefix='/api')\n"
        "@app.get('/health')\n"
        "def health(): pass\n"),
}
FLASK_APP = {
    "web/__init__.py": (
        "from flask import Flask\n"
        "def create_app():\n"
        "    app = Flask(__name__)\n"
        "    from . import auth\n"
        "    app.register_blueprint(auth.bp)\n"
        "    from .admin import admin_bp\n"
        "    from .child import child\n"
        "    admin_bp.register_blueprint(child)\n"
        "    app.register_blueprint(admin_bp, url_prefix='/admin')\n"
        "    return app\n"),
    "web/auth.py": (
        "from flask import Blueprint\n"
        "bp = Blueprint('auth', __name__, url_prefix='/auth')\n"
        "@bp.route('/login', methods=['GET', 'POST'])\n"
        "def login(): pass\n"),
    "web/admin.py": (
        "from flask import Blueprint\n"
        "admin_bp = Blueprint('admin', __name__)\n"
        "@admin_bp.route('/users')\n"
        "def users(): pass\n"),
    "web/child.py": (
        "from flask import Blueprint\n"
        "child = Blueprint('child', __name__, url_prefix='/child')\n"
        "@child.route('/leaf')\n"
        "def leaf(): pass\n"),
}


def gate_d_prefix_cross_module() -> None:
    print("\n-- D (T8d-X1): include_router / register_blueprint mounts composed ACROSS modules --")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write_tree(root, {**FASTAPI_APP, **FLASK_APP})
        with _env(DEEPMAP_RECOGNIZERS=None):
            supp, edges = LR.run(root)
        legacy = FE.extract_framework_edges(root)
        check("registry run() == legacy extract_framework_edges() (byte-equal, same order)",
              edges == legacy, f"{len(edges)} vs {len(legacy)}")
        check("Python still contributes ZERO supplemental nodes", supp == [], supp[:2])

        def path(h):
            r = _route(edges, h)
            return (r.attrs.get("path"), r.attrs.get("paths")) if r else None

        check("billing.router (prefix /billing) mounted at /api from main.py -> '/api/billing/invoices'",
              path("list_invoices") == ("/api/billing/invoices", None), path("list_invoices"))
        check("nested: users.router -> api_router (/v2) -> app (/api) -> '/api/v2/users/{uid}'",
              path("get_user") == ("/api/v2/users/{uid}", None), path("get_user"))
        check("@app.get on the FastAPI app itself is unchanged -> '/health'",
              path("health") == ("/health", None), path("health"))
        check("Flask factory: register_blueprint(auth.bp) (function-level `from . import auth`) -> '/auth/login'",
              path("login") == ("/auth/login", None), path("login"))
        check("Flask: register_blueprint(admin_bp, url_prefix='/admin') -> '/admin/users'",
              path("users") == ("/admin/users", None), path("users"))
        check("Flask nested blueprint: child registered on admin_bp -> '/admin/child/leaf'",
              path("leaf") == ("/admin/child/leaf", None), path("leaf"))
        check("the edge's owner/handler are unchanged (only attrs move): m=main? no — the route module",
              all(r.src_name == Path(r.src_file).stem for r in edges if r.edge_type == "ROUTES_TO"))

        # the legacy per-module core stays per-module: it composes only what ONE file can see
        solo = FE.edges_from_module(ast.parse(FASTAPI_APP["app/routers/billing.py"]),
                                    "app/routers/billing.py")
        r = _route(solo, "list_invoices")
        check("edges_from_module(billing.py) alone -> '/billing/invoices' (module-local composition)",
              r is not None and r.attrs.get("path") == "/billing/invoices", r)

        # sync's seam: typed edges from _apply_recognizers carry the composed paths
        import importlib.util
        spec = importlib.util.spec_from_file_location("dm_sync_r7_w7", BUILD / "sync.py")
        S = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(S)
        base = [_node("x", "Class", "X", "app/main.py")]
        with contextlib.redirect_stderr(io.StringIO()), _env(DEEPMAP_RECOGNIZERS=None):
            _m, typed = S._apply_recognizers(root, base)
        r = _route(typed, "get_user")
        check("sync._apply_recognizers hands the push the composed path",
              r is not None and r.attrs.get("path") == "/api/v2/users/{uid}", r)

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write_tree(root, {"svc/a.py": "router = APIRouter(prefix='/p')\n@router.get('/x')\ndef x(): pass\n"})
        base = FE.edges_from_module(ast.parse((root / "svc/a.py").read_text()), "svc/a.py")
        check("compose_route_prefixes returns the ORIGINAL list object when no module mounts anything",
              FE.compose_route_prefixes(root, base) is base)
        check("... and an edge list with no ROUTES_TO at all comes back unchanged",
              FE.compose_route_prefixes(root, []) == [])

    # an unresolvable mount (import of a module outside the tree) never guesses
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write_tree(root, {
            "svc/routes.py": ("router = APIRouter(prefix='/r')\n@router.get('/x')\ndef x(): pass\n"),
            "svc/main.py": ("from elsewhere import thing\napp = FastAPI()\n"
                            "app.include_router(thing.router, prefix='/zzz')\n"),
        })
        with _env(DEEPMAP_RECOGNIZERS=None):
            _s, edges = LR.run(root)
        r = _route(edges, "x")
        check("an include of an UNRESOLVABLE module leaves the router's own composition ('/r/x')",
              r is not None and r.attrs.get("path") == "/r/x", r)


# =====================================================================================================
# E — C# fluent navigation-property targets
# =====================================================================================================
POLYSHOP_CS = {
    "csharp/Data/BillingContext.cs": (
        "using Billing.Models;\nusing Microsoft.EntityFrameworkCore;\n\nnamespace Billing.Data;\n\n"
        "public class BillingContext : DbContext\n{\n"
        "    public BillingContext(DbContextOptions<BillingContext> options) : base(options) { }\n\n"
        "    public DbSet<Invoice> Invoices { get; set; }\n"
        "    public DbSet<InvoiceLine> InvoiceLines { get; set; }\n"
        "    public DbSet<Payment> Payments { get; set; }\n\n"
        "    protected override void OnModelCreating(ModelBuilder modelBuilder)\n    {\n"
        "        modelBuilder.Entity<Invoice>()\n            .HasMany(i => i.Lines)\n"
        "            .WithOne(l => l.Invoice)\n            .HasForeignKey(l => l.InvoiceId)\n"
        "            .OnDelete(DeleteBehavior.Cascade);\n\n"
        "        modelBuilder.Entity<Invoice>()\n            .HasMany(i => i.Payments)\n"
        "            .WithOne(p => p.Invoice)\n            .HasForeignKey(p => p.InvoiceId)\n"
        "            .OnDelete(DeleteBehavior.Cascade);\n\n"
        "        modelBuilder.Entity<Invoice>()\n            .HasOne(i => i.TaxProfile)\n"
        "            .WithMany()\n            .OnDelete(DeleteBehavior.Restrict);\n"
        "        modelBuilder.Entity<Order>()\n            .HasOne(o => o.Buyer)\n"
        "            .WithMany(c => c.Orders)\n            .OnDelete(DeleteBehavior.Cascade);\n"
        "        modelBuilder.Entity<CreditNote>()\n            .HasMany(n => n.Lines)\n"
        "            .WithOne()\n            .OnDelete(DeleteBehavior.Cascade);\n"
        "    }\n}\n"),
    "csharp/Models/Invoice.cs": (
        "using System;\nusing System.Collections.Generic;\n\nnamespace Billing.Models;\n\n"
        "public class Invoice\n{\n    public int Id { get; set; }\n"
        "    public string Number { get; set; } = \"\";\n    public DateTime IssuedAt { get; set; }\n"
        "    public List<InvoiceLine> Lines { get; set; } = new();\n"
        "    public List<Payment> Payments { get; set; } = new();\n"
        "    public TaxProfile TaxProfile { get; set; } = null!;\n}\n"),
    "csharp/Models/InvoiceLine.cs": (
        "namespace Billing.Models;\n\npublic class InvoiceLine\n{\n    public int Id { get; set; }\n"
        "    public int InvoiceId { get; set; }\n    public Invoice Invoice { get; set; } = null!;\n}\n"),
    "csharp/Models/Payment.cs": (
        "namespace Billing.Models;\n\npublic class Payment\n{\n    public int Id { get; set; }\n"
        "    public int InvoiceId { get; set; }\n    public Invoice Invoice { get; set; } = null!;\n}\n\n"
        "public class TaxProfile\n{\n    public int Id { get; set; }\n}\n"),
    "csharp/Models/Order.cs": (
        "namespace Billing.Models;\n\npublic class Order\n{\n    public int Id { get; set; }\n"
        "    public Customer Buyer { get; set; } = null!;\n}\n\n"
        "public class Customer\n{\n    public List<Order> Orders { get; set; } = new();\n}\n"),
    # an inherited navigation: CreditNote : Document, Document declares Lines : List<DocLine>
    "csharp/Models/Document.cs": (
        "namespace Billing.Models;\n\npublic abstract class Document\n{\n"
        "    public ICollection<DocLine> Lines { get; set; } = new List<DocLine>();\n}\n\n"
        "public class CreditNote : Document\n{\n}\n\npublic class DocLine\n{\n    public int Id { get; set; }\n}\n"),
}


def gate_e_csharp_navigation() -> None:
    print("\n-- E (T8d-B11): C# fluent HasMany/HasOne target = the navigation property's TYPE --")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write_tree(root, POLYSHOP_CS)
        with _env(DEEPMAP_RECOGNIZERS="csharp"):
            supp, edges = LR.run(root)
        fl = [e for e in edges if (e.attrs or {}).get("via") == "fluent"]
        cas = {(e.src_name, e.dst_name) for e in edges if e.edge_type == "CASCADES"}
        hm = {(e.src_name, e.dst_name) for e in fl if e.edge_type == "HAS_MANY"}
        ref = {(e.src_name, e.dst_name) for e in fl if e.edge_type == "REFERENCES"}
        check("HasMany(i => i.Lines) on Invoice -> HAS_MANY Invoice->InvoiceLine (List<InvoiceLine> Lines)",
              ("Invoice", "InvoiceLine") in hm and ("Invoice", "Line") not in hm, sorted(hm))
        check("its OnDelete(Cascade) -> CASCADES Invoice->InvoiceLine (was dropped as 'Line')",
              ("Invoice", "InvoiceLine") in cas and ("Invoice", "Line") not in cas, sorted(cas))
        check("HasMany(i => i.Payments) unchanged (singular guess already right) -> Invoice->Payment",
              ("Invoice", "Payment") in cas and ("Invoice", "Payment") in hm, sorted(cas))
        check("HasOne(o => o.Buyer) where Buyer : Customer -> REFERENCES Order->Customer",
              ("Order", "Customer") in ref and ("Order", "Buyer") not in ref, sorted(ref))
        check("HasOne+WithMany cascade inverts onto the nav TYPE -> CASCADES Customer->Order",
              ("Customer", "Order") in cas and ("Buyer", "Order") not in cas, sorted(cas))
        check("an INHERITED navigation (CreditNote : Document, Lines : ICollection<DocLine>) -> DocLine",
              ("CreditNote", "DocLine") in cas and ("CreditNote", "DocLine") in hm, sorted(cas))
        check("Restrict still emits no cascade (HasOne TaxProfile)",
              not any("TaxProfile" in pair for pair in cas), sorted(cas))
        check("the fluent attrs are unchanged ({'via': 'fluent', 'property': 'Lines'})",
              any(e.attrs == {"via": "fluent", "property": "Lines"} and e.dst_name == "InvoiceLine"
                  for e in fl), [e.attrs for e in fl])

        # end-to-end resolution over the recognizer's own :Class nodes (the graphify-less C# shape)
        live = [n["key"] for n in supp]
        rows = ER.resolve_typed_edges(edges, ER.Resolver(supp), live)
        by_key = {n["key"]: n["name"] for n in supp}
        got = {(r[0], by_key.get(r[1]), by_key.get(r[2])) for r in rows}
        check("resolved: (CASCADES, Invoice, InvoiceLine) persists — declared cascade no longer dropped",
              ("CASCADES", "Invoice", "InvoiceLine") in got, sorted(x for x in got if x[0] == "CASCADES"))

    # an AMBIGUOUS navigation (two Invoice types disagree on Lines) keeps the old drop-never-guess rule
    amb = dict(POLYSHOP_CS)
    amb["csharp/Legacy/Invoice.cs"] = ("namespace Legacy;\n\npublic class Invoice\n{\n"
                                       "    public List<LegacyLine> Lines { get; set; }\n}\n")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write_tree(root, amb)
        with _env(DEEPMAP_RECOGNIZERS="csharp"):
            _s, edges = LR.run(root)
        cas = {(e.src_name, e.dst_name) for e in edges if e.edge_type == "CASCADES"}
        check("conflicting navigation types -> no rewrite (never pick one of two)",
              ("Invoice", "InvoiceLine") not in cas and ("Invoice", "LegacyLine") not in cas, sorted(cas))
    # the per-file core is unchanged (its own gates lock the singularizer contract)
    _n, per_file = CS.recognize(POLYSHOP_CS["csharp/Data/BillingContext.cs"], "csharp/Data/BillingContext.cs")
    check("csharp_edges.recognize (per-file, no cross-file knowledge) is unchanged",
          ("Invoice", "Line") in {(e.src_name, e.dst_name) for e in per_file if e.edge_type == "CASCADES"})
    _n, inv = CS.recognize(POLYSHOP_CS["csharp/Models/Invoice.cs"], "csharp/Models/Invoice.cs")
    check("resolve_navigation_targets returns the ORIGINAL list when there is nothing to rewrite",
          CS.resolve_navigation_targets(inv) is inv)
    gen_ctx = ("public class Ctx : DbContext\n{\n    protected override void OnModelCreating(ModelBuilder b)\n"
               "    {\n        b.Entity<Invoice>().HasMany<Payment>(i => i.Lines).WithOne()"
               ".OnDelete(DeleteBehavior.Cascade);\n    }\n}\n")
    _n, ctx_edges = CS.recognize(gen_ctx, "Ctx.cs")
    out = CS.resolve_navigation_targets(inv + ctx_edges)
    cas = {(e.src_name, e.dst_name) for e in out if e.edge_type == "CASCADES"}
    check("an explicit HasMany<Payment>(...) generic is never overridden by the navigation type",
          ("Invoice", "Payment") in cas and ("Invoice", "InvoiceLine") not in cas, sorted(cas))


# =====================================================================================================
# F — a DISABLED recognizer leaves no phantom typed edges (T8#9)
# =====================================================================================================
_NODE_UPSERT = re.compile(r"^UNWIND \$rows AS row MERGE \(x:`?(\w+)`? \{key:row\.key\}\)")
_PRUNE_FWD = re.compile(r"^MATCH \(a\)-\[r:(\w+)\]->\(\) WHERE a\.key IN \$keys DELETE r$")
_PRUNE_REV = re.compile(r"^MATCH \(\)-\[r:(\w+)\]->\(a\) WHERE a\.key IN \$keys DELETE r$")
_MERGE_REL = re.compile(r"MERGE \((a|b)\)-\[r:(\w+)\]->\((a|b)\)")
_ENDPOINT = re.compile(r"\((a|b)(?::`?(\w+)`?)? \{key:row\.(a|b)\}\)")
_DISABLED = re.compile(r"^MATCH (\(a\)-\[r:([\w|]+)\]->\(\)|\(\)-\[r:([\w|]+)\]->\(a\)) WHERE ")


class _Res:
    def __init__(self, d):
        self._d = d

    def single(self):
        return {"d": self._d}

    def consume(self):
        return None

    def __iter__(self):
        return iter(())


class Mini:
    """In-memory interpreter for the push's statement shapes + the disabled-layer prune."""

    def __init__(self):
        self.nodes: dict = {}            # key -> {"label", "sf"}
        self.rels: dict = {}             # (type, a_key, b_key) -> props
        self.stmts: list = []

    def run(self, cypher, params):
        c = " ".join(str(cypher).split())
        self.stmts.append((c, params))
        m = _NODE_UPSERT.match(c)
        if m:
            for row in params["rows"]:
                self.nodes[row["key"]] = {"label": m.group(1), "sf": row.get("sf")}
            return None
        m = _PRUNE_FWD.match(c) or _PRUNE_REV.match(c)
        if m:
            keys, et, side = set(params["keys"]), m.group(1), (1 if m.re is _PRUNE_FWD else 2)
            for rk in [rk for rk in self.rels if rk[0] == et and rk[side] in keys]:
                del self.rels[rk]
            return None
        m = _DISABLED.match(c)
        if m and "ENDS WITH" in c:
            fwd = m.group(2) is not None
            types = set((m.group(2) or m.group(3)).split("|"))
            exts = [str(x).lower() for x in params.get("exts", [])]
            limit = int(params.get("n", 10 ** 9))
            gone = 0
            for rk in list(self.rels):
                if gone >= limit:
                    break
                if rk[0] not in types:
                    continue
                anchor = rk[1] if fwd else rk[2]
                sf = str(self.nodes.get(anchor, {}).get("sf") or "").lower()
                if any(sf.endswith(x) for x in exts):
                    del self.rels[rk]
                    gone += 1
            return _Res(gone)
        mm = _MERGE_REL.search(c)
        ends = _ENDPOINT.findall(c)
        if mm and len(ends) == 2 and c.startswith("UNWIND $rows AS row "):
            frm, et, to = mm.groups()
            for row in params["rows"]:
                ka, kb = row[frm], row[to]
                if ka in self.nodes and kb in self.nodes:
                    self.rels[(et, ka, kb)] = {"confidence": row.get("c")}
            return None
        return None


class _Tx:
    def __init__(self, g):
        self.g = g

    def run(self, cypher, **params):
        return self.g.run(cypher, params)

    def commit(self):
        pass

    def rollback(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Sess(_Tx):
    def begin_transaction(self, *a, **k):
        return _Tx(self.g)


class _Drv:
    def __init__(self, g):
        self.g = g

    def session(self, *a, **k):
        return _Sess(self.g)

    def close(self):
        pass


def _seed_prior_graph(g: Mini) -> None:
    """The graph a previous sync left: Rails + C# recognizer shells carrying typed edges (their
    recognizers are about to be disabled), a live Python layer, and an engine :IMPORTS edge."""
    for key, label, sf in (
            ("app/models/order.rb::Order:1", "Class", "app/models/order.rb"),
            ("app/models/line_item.rb::LineItem:1", "Class", "app/models/line_item.rb"),
            ("app/views/orders/show.html.erb::show:1", "Module", "app/views/orders/show.html.erb"),
            ("app/views/orders/_row.html.erb::_row:1", "Module", "app/views/orders/_row.html.erb"),
            ("config/routes.rb::admin:1", "Method", "config/routes.rb"),
            ("app/controllers/orders_controller.rb::OrdersController.index:2", "Method",
             "app/controllers/orders_controller.rb"),
            ("Data/ShopContext.cs::Invoice:1", "Class", "Models/Invoice.CS"),
            ("Data/ShopContext.cs::InvoiceLine:1", "Class", "Models/InvoiceLine.cs"),
            ("svc.py::Base:1", "Class", "svc.py"),
            ("svc.py::Svc:4", "Class", "svc.py"),
            ("svc.py::svc:1", "Module", "svc.py")):
        g.nodes[key] = {"label": label, "sf": sf}
    g.rels[("CASCADES", "app/models/line_item.rb::LineItem:1", "app/models/order.rb::Order:1")] = {}
    g.rels[("RENDERS", "app/views/orders/show.html.erb::show:1",
            "app/views/orders/_row.html.erb::_row:1")] = {}
    g.rels[("ROUTES_TO", "config/routes.rb::admin:1",
            "app/controllers/orders_controller.rb::OrdersController.index:2")] = {}
    g.rels[("CASCADES", "Data/ShopContext.cs::InvoiceLine:1", "Data/ShopContext.cs::Invoice:1")] = {}
    g.rels[("INHERITS", "svc.py::Svc:4", "svc.py::Base:1")] = {}
    g.rels[("IMPORTS", "app/views/orders/show.html.erb::show:1", "app/models/order.rb::Order:1")] = {}
    g.rels[("CALLS", "app/models/order.rb::Order:1", "app/models/line_item.rb::LineItem:1")] = {}


def _live_python_nodes():
    return [_node("svc.py::svc:1", "Module", "svc", "svc.py"),
            _node("svc.py::Base:1", "Class", "Base", "svc.py"),
            _node("svc.py::Svc:4", "Class", "Svc", "svc.py", 4)]


def gate_f_disabled_recognizer() -> None:
    print("\n-- F (T8#9): a DISABLED recognizer's typed edges are pruned (orphan anchors included) --")
    import importlib.util
    spec = importlib.util.spec_from_file_location("dm_sync_r7_w7f", BUILD / "sync.py")
    S = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(S)
    py_typed = [FE.TypedEdge("Svc", "svc.py", "Base", "INHERITS", "LOCAL", {})]

    # F1 — default (every recognizer enabled): NO disabled-layer statement at all (byte-identical)
    g0 = Mini()
    _seed_prior_graph(g0)
    with _env(DEEPMAP_RECOGNIZERS=None):
        S.push_to_neo4j(_Drv(g0), _live_python_nodes(), [], [], py_typed)
    check("default env: no disabled-layer prune statement is sent (default push byte-identical)",
          not any("ENDS WITH" in c for c, _p in g0.stmts), [c for c, _p in g0.stmts if "ENDS WITH" in c][:1])
    check("default env: orphan-anchored typed edges are left to --stale-cleanup (unchanged design)",
          ("CASCADES", "app/models/line_item.rb::LineItem:1", "app/models/order.rb::Order:1") in g0.rels)

    # F2 — DEEPMAP_RECOGNIZERS=python: the Ruby/C# layers are OFF -> their phantom typed edges go
    g = Mini()
    _seed_prior_graph(g)
    with _env(DEEPMAP_RECOGNIZERS="python"):
        S.push_to_neo4j(_Drv(g), _live_python_nodes(), [], [], py_typed)
    typed_left = {rk[0] for rk in g.rels if rk[0] not in ("IMPORTS", "CALLS")
                  and not rk[1].startswith("svc.py")}
    check("Ruby CASCADES / RENDERS / ROUTES_TO on orphan recognizer nodes are pruned",
          not typed_left, sorted(g.rels))
    check("the C# CASCADES (reversed; anchored at the declaring target, UPPERCASE .CS file) is pruned",
          ("CASCADES", "Data/ShopContext.cs::InvoiceLine:1", "Data/ShopContext.cs::Invoice:1") not in g.rels)
    check("the enabled Python layer is rebuilt (INHERITS Svc->Base persists)",
          ("INHERITS", "svc.py::Svc:4", "svc.py::Base:1") in g.rels, sorted(g.rels))
    check(":IMPORTS (engine-owned) and :CALLS are never touched by the disabled-layer prune",
          ("IMPORTS", "app/views/orders/show.html.erb::show:1", "app/models/order.rb::Order:1") in g.rels
          and ("CALLS", "app/models/order.rb::Order:1", "app/models/line_item.rb::LineItem:1") in g.rels)
    dstmts = [c for c, _p in g.stmts if "ENDS WITH" in c]
    check("the prune statements interpolate only whitelisted typed edge types (never IMPORTS/CALLS)",
          dstmts and all(set(t for t in re.findall(r"\[r:([\w|]+)\]", c)[0].split("|"))
                         <= (set(FE.ALLOWED_EDGE_TYPES) - {"IMPORTS"}) for c in dstmts), dstmts)
    check("the prune is BOUNDED (a LIMIT per statement)",
          dstmts and all("LIMIT $n" in c for c in dstmts), dstmts)
    exts = next((p.get("exts") for c, p in g.stmts if "ENDS WITH" in c), None) or []
    check("the prune names the disabled families' extensions only (never .py while python is enabled)",
          ".rb" in exts and ".erb" in exts and ".cs" in exts and ".py" not in exts, exts)

    # F3 — DEEPMAP_RECOGNIZERS=0: the whole layer is off -> every recognizer family pruned
    g3 = Mini()
    _seed_prior_graph(g3)
    with _env(DEEPMAP_RECOGNIZERS="0"):
        S.push_to_neo4j(_Drv(g3), _live_python_nodes(), [], [], [])
    left = sorted(rk for rk in g3.rels if rk[0] not in ("IMPORTS", "CALLS"))
    check("DEEPMAP_RECOGNIZERS=0: no typed edge survives anywhere (live ones pruned, orphans pruned)",
          not left, left)

    # F4 — bounded batches converge; the public API reports completion
    g4 = Mini()
    for i in range(7):
        a, b = f"x/o{i}.rb::A{i}:1", f"x/o{i}.rb::B{i}:1"
        g4.nodes[a] = {"label": "Class", "sf": f"x/o{i}.rb"}
        g4.nodes[b] = {"label": "Class", "sf": f"x/o{i}.rb"}
        g4.rels[("HAS_MANY", a, b)] = {}
    with _env(DEEPMAP_RECOGNIZERS="python"):
        tx = _Tx(g4)
        done1 = _safe(ER.prune_disabled_recognizer_edges, tx, limit=3)
        left1 = len(g4.rels)
        done2 = _safe(ER.prune_disabled_recognizer_edges, tx, limit=3)
        done3 = _safe(ER.prune_disabled_recognizer_edges, tx, limit=3)
    check("limit=3: the first call deletes exactly 3 and reports NOT done", done1 is False and left1 == 4,
          (done1, left1))
    check("repeated calls finish the job and report done", done3 is True and not g4.rels,
          (done2, done3, len(g4.rels)))
    with _env(DEEPMAP_RECOGNIZERS=None):
        log: list = []

        class _Log:
            def run(self, c, **p):
                log.append(c)

        done = _safe(ER.prune_disabled_recognizer_edges, _Log())
    check("nothing disabled -> the public prune sends NO statement and reports done", done is True and not log,
          (done, log))
    check("disabled_entries(): DEEPMAP_RECOGNIZERS=ruby,go disables python/csharp/java/typescript",
          _safe(lambda: [e.name for e in _with_env_disabled("ruby,go")]) == ["python", "csharp", "java",
                                                                            "typescript"])

    # F5 — inside the push each anchor-group transaction runs ONE bounded round until a round is done
    g5 = Mini()
    for i in range(5):
        a, b = f"x/p{i}.rb::A{i}:1", f"x/p{i}.rb::B{i}:1"
        g5.nodes[a] = {"label": "Class", "sf": f"x/p{i}.rb"}
        g5.nodes[b] = {"label": "Class", "sf": f"x/p{i}.rb"}
        g5.rels[("REFERENCES", a, b)] = {}
    live = _live_python_nodes()
    for n in live:
        g5.nodes[n["key"]] = {"label": n["label"], "sf": n["source_file"]}
    resolver = ER.Resolver(live)
    saved = ER._DISABLED_PRUNE_BATCH
    rounds = []
    try:
        ER._DISABLED_PRUNE_BATCH = 2
        with _env(DEEPMAP_RECOGNIZERS="python"):
            for k in [n["key"] for n in live] + ["svc.py::svc:1"]:
                before = len([c for c, _p in g5.stmts if "ENDS WITH" in c])
                _safe(ER.rewrite_anchored_edges, _Tx(g5), [k], [], resolver)
                rounds.append(len([c for c, _p in g5.stmts if "ENDS WITH" in c]) - before)
    finally:
        ER._DISABLED_PRUNE_BATCH = saved
    left5 = [rk for rk in g5.rels if rk[0] == "REFERENCES"]
    check("rounds of <=2 per statement continue group after group until all 5 phantoms are gone",
          not left5, left5)
    check("once a round reports done, later groups send no disabled-layer statement",
          bool(rounds) and rounds[-1] == 0 and rounds[0] > 0, rounds)


def _with_env_disabled(val):
    with _env(DEEPMAP_RECOGNIZERS=val):
        return LR.disabled_entries()


# =====================================================================================================
# G — no-regression on shapes this round did not target
# =====================================================================================================
def gate_g_no_regression() -> None:
    print("\n-- G: untouched shapes keep their exact edges --")
    src = ("@app.route('/x', methods=['POST', 'PUT'])\ndef create(): pass\n"
           "@bp.route('/z')\ndef z(): pass\n"
           "@router.get('/a')\ndef show(): pass\n"
           "user_logged_in.connect(track_login)\nee.on('ready', boot_cb)\n"
           "@cli.command('deploy')\ndef deploy_cmd(): pass\n"
           "@app.before_request\ndef hook(): pass\n")
    e = _edges(src)
    rt = sorted((x.dst_name, tuple(sorted(x.attrs.items()))) for x in e if x.edge_type == "ROUTES_TO")
    check("routes on UNDECLARED routers/apps keep attrs exactly {method, path}",
          rt == [("create", (("method", "POST,PUT"), ("path", "/x"))),
                 ("deploy_cmd", (("method", "CLI"), ("path", "deploy"))),
                 ("show", (("method", "GET"), ("path", "/a"))),
                 ("z", (("method", "*"), ("path", "/z")))], rt)
    trg = sorted((x.src_name, x.dst_name, x.conf_hint, tuple(sorted(x.attrs.items())))
                 for x in _find(e, "TRIGGERS"))
    check("TRIGGERS .connect / .on of a name the module never binds keep their exact NAME edges under the "
          "variable guard (ISS-build:015); decorator HOOKS unchanged",
          trg == [("m", "boot_cb", "NAME", (("event", "ready"),)),
                  ("m", "track_login", "NAME", (("signal", "user_logged_in"),))]
          and [x.attrs for x in _find(e, "HOOKS")] == [{"event": "before_request"}], (trg, e))
    guarded = _edges("import sqlite3\ndef open_db(path):\n    return sqlite3.connect(path)\n"
                     "def wire(ee, cb):\n    ee.on('evt', cb)\n", "db.py")
    check("... while a PARAMETER handler and a MODULE's connect() (sqlite3.connect(path)) emit no TRIGGERS "
          "(the section A/B variable guard, extended to .connect / .on by ISS-build:015)",
          not _find(guarded, "TRIGGERS"), guarded)
    # the registry output for a repo with no router/hook/scheduler shapes is edge-for-edge the per-module core
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write_tree(root, {"a.py": src, "b.py": "class B(A): pass\n"})
        with _env(DEEPMAP_RECOGNIZERS=None):
            _s, edges = LR.run(root)
        per_module = _edges(src, "a.py") + _edges("class B(A): pass\n", "b.py")
        check("registry == concatenated per-module edges when no cross-module mount exists",
              edges == per_module, f"{len(edges)} vs {len(per_module)}")
    check("RecognizerEntry stays constructible with the 6 original positional fields",
          _safe(lambda: LR.RecognizerEntry("t", frozenset({".x"}), frozenset(), (), lambda s, r: ([], []),
                                           frozenset())) is not None)
    one = FE.TypedEdge("A", "a.zz", "B", "INHERITS", "LOCAL", {})

    def boom(root, edges):
        raise RuntimeError("post-pass bug")

    fake = LR.RecognizerEntry("zz", frozenset({".zz"}), frozenset({"INHERITS"}), (),
                              lambda s, r: ([], [one]), frozenset(), boom)
    saved_reg = LR.REGISTRY
    try:
        LR.REGISTRY = (fake,)
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "a.zz").write_text("x", encoding="utf-8")
            err = io.StringIO()
            with contextlib.redirect_stderr(err), _env(DEEPMAP_RECOGNIZERS=None):
                _s, got = LR.run(td)
        check("a crashing finalize keeps the entry's per-file edges (and says so on stderr)",
              got == [one] and "post-pass skipped" in err.getvalue(), (got, err.getvalue()))
    finally:
        LR.REGISTRY = saved_reg


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 72)
    print("DeepMap — round-7 W7 typed-edge gates (recognizer coverage + disabled-layer prune)")
    print("=" * 72)
    for g in (gate_a_apscheduler, gate_b_register_hook, gate_c_prefix_local, gate_d_prefix_cross_module,
              gate_e_csharp_navigation, gate_f_disabled_recognizer, gate_g_no_regression):
        _safe(g)
    total = _PASS + _FAIL
    print("\n" + "=" * 72)
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} checks passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
