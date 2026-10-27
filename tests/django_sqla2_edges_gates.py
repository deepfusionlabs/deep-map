#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Django URLconfs + SQLAlchemy 2.0 typed models + Python ORM cascades — the Python recognizer's 2026-10 track.

Before this track build/framework_edges.py read neither shape (docs: "SQLAlchemy 2.0 Mapped[...] models and Django
urls.py are not read") and no Python ORM cascade at all: a Django project's every route and a typed model's every
relation were missing from the graph, so `deepmap_impact("BookListView")` found no entry point and
`deepmap_impact("Order")` no cascade.

  A  Django URLconf, one module: path() / re_path() / url() -> ROUTES_TO module -> view (a function view, a CBV's
     CLASS via X.as_view(), through decorators and `x = View.as_view()` aliases); LOCAL for a view defined in the
     URLconf, NAME + a "spec:" import scope for an imported one; include()d inline / module-level lists compose the
     path and url namespace; += / append / extend / i18n_patterns; regex anchors dropped.
  B  shapes it must NOT match (KL-mistake:008): a pathlib / os `path`, a local def `path`, another library's url()
     / include(), Starlette Route lists, urlpatterns-like names in a def / class / string / comment, admin.site.urls,
     router.urls, ninja api.urls, lambdas, factory calls, variables, a list that only copies a pattern list.
  C  across modules (compose_route_prefixes): include('app.urls'), (module, app_name) pairs, namespace=, an imported
     pattern list, a view re-exported / star-imported / aliased in another module placed where it is DEFINED
     ("exact:module:<path>"), an external view keeps "spec:" (binds nothing); registry == legacy shell; sync's
     exclusion predicate keeps an in-tree copy (.worktrees/) from adding a path.
  D  SQLAlchemy 2.0 typed: Mapped[list[...]] / List / set / dict / WriteOnlyMapped -> HAS_MANY, Mapped["X"] /
     Optional / "X | None" -> REFERENCES, whole-string annotations, explicit target wins, so.Mapped through
     `import sqlalchemy.orm as so`, mapped_column(ForeignKey) like Column(ForeignKey), backref reverse relation,
     relationship / backref cascade, ForeignKey ondelete; __tablename__ -> class (same file, then the tree).
  E  SQLAlchemy shapes it must NOT match: another library's Mapped, a local Mapped class, SQLModel (not read),
     pydantic / dataclass fields, mapped_column without a ForeignKey, non-delete cascades, a module-level Table.
  F  Django ORM: on_delete=CASCADE / SET_NULL -> CASCADES referenced -> declaring ('app_label.Model' -> Model,
     'self' -> the class); PROTECT / RESTRICT / DO_NOTHING never; an imported model carries its import scope.
  G  push-time resolution (edge_resolve) on REAL ast-extractor nodes: grades (EXTRACTED local view, INFERRED
     imported view / model, no row for an external import), no fan-out to a same-named class in another module,
     a ForeignKey table resolved across files, CASCADES persisted (dependent)->(principal).
  H  registry: the Python entry declares CASCADES (edge_types + impact), the retrieval lockstep holds.
  I  the REAL graphify engine on the same tree (skipped when graphify is absent): the rows G expects.
  J  seeded fuzz: mutated fixtures never raise and only emit well-formed edges.
  K  the review round: a Django 'app_label.Model' binds in that app's models module (django.contrib's 'auth.Group'
     never binds a project Group); ForeignKey(models.User.id) names User; a Mapped re-exported with relationship()
     by a sibling package; an in-tree import the post-pass cannot follow (bound twice, __getattr__, a colliding
     root module), a root-level index.py and a sync root that IS the package all still resolve; a failing post-pass
     degrades imported views / models to the name rule; a non-literal route has no path; include() / mount
     cycles stay polynomial (a subprocess with a timeout, so a regression is RED, never a hung gate).

Hermetic: temp dirs created by this gate (removed by exact path), no Neo4j, no network, no ~/.deepmap.
Usage: python tests/django_sqla2_edges_gates.py [--build <build dir>]   (--build: run against another tree's build/)
"""
from __future__ import annotations

import ast
import contextlib
import io
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build"
if "--build" in sys.argv:
    BUILD = Path(sys.argv[sys.argv.index("--build") + 1]).resolve()
sys.path.insert(0, str(BUILD))

import edge_resolve as ER            # noqa: E402
import framework_edges as FE         # noqa: E402
import language_recognizers as LR    # noqa: E402

_TMP = Path(tempfile.mkdtemp(prefix="dm_dj_sqla2_")).resolve()     # created by THIS gate, removed by exact path
_PASS = _FAIL = 0


def check(desc: str, cond, detail="") -> None:
    global _PASS, _FAIL
    ok = bool(cond)
    _PASS += ok
    _FAIL += (not ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {desc}" + ("" if ok else f"  -- {str(detail)[:700]}"))


@contextlib.contextmanager
def _env(**kv):
    saved = {k: os.environ.get(k) for k in kv}
    try:
        for k, v in kv.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = str(v)
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _flags(**kv):
    base = {"DEEPMAP_RECOGNIZERS": None, "DEEPMAP_INCLUDE_VENDOR": None, "DEEPMAP_EXCLUDE_TESTS": None,
            "DEEPMAP_EXTRACTOR": None, "DEEPMAP_PRECISE_CALLS": None, "DEEPMAP_GRAPHIFY_CALLS_PLUS": None}
    base.update(kv)
    return _env(**base)


def _edges(src: str, rel: str = "m.py") -> list:
    try:
        return FE.edges_from_module(ast.parse(src), rel)
    except Exception as exc:  # noqa: BLE001 — a crash on the tree under test is a RED result, not an abort
        check(f"edges_from_module({rel}) ran without raising", False, f"{type(exc).__name__}: {exc}")
        return []


def _of(edges, et, src=None, dst=None) -> list:
    return [e for e in edges if e.edge_type == et and (src is None or e.src_name == src)
            and (dst is None or e.dst_name == dst)]


def _route(edges, dst):
    got = [e for e in edges if e.edge_type == "ROUTES_TO" and e.dst_name == dst]
    return got[0] if got else None


def _scope(e) -> str:
    return getattr(e, "scope", "") if e is not None else None


def _write(root: Path, files: dict) -> Path:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return root


def _fresh(name: str) -> Path:
    d = _TMP / name
    d.mkdir(parents=True, exist_ok=False)
    return d


def _safe(fn):
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 — a crash on the tree under test is a RED result
        import traceback
        traceback.print_exc()
        check(f"{fn.__name__} ran without raising", False, f"{type(exc).__name__}: {exc}")


# =====================================================================================================
# A — one Django URLconf
# =====================================================================================================
POLLS_URLS = '''"""Polls URLs. Example in a docstring: path('docstring/', views.not_a_route)"""
from django.urls import path, re_path, include
from django.conf.urls import url
from django.contrib.auth.decorators import login_required
from django.views.decorators.cache import cache_page
from . import views
from .views import ResultsView as RV, vote
from .api import api_patterns

app_name = "polls"
detail = views.DetailView.as_view()
# path('comment/', views.not_a_route_either)


def ping(request):
    return None


urlpatterns = [
    path("", views.index, name="index"),
    path("<int:pk>/", detail, name="detail"),
    path("<int:pk>/results/", RV.as_view(), name="results"),
    path("<int:question_id>/vote/", vote, name="vote"),
    path("ping/", ping),
    path("secret/", login_required(views.secret), name="secret"),
    path("cached/", cache_page(60 * 15)(views.cached)),
    re_path(r"^archive/(?P<year>[0-9]{4})/$", views.year_archive, name="archive"),
    url(r"^legacy/$", views.legacy),
    path("inline/", include([path("a/", views.inline_a, name="a"),
                             path("b/", include(([path("c/", views.inline_c, name="c")], "deep")))])),
    path("api/", include(api_patterns)),
]
urlpatterns += [path("plus/", views.plus)]
urlpatterns.append(path("appended/", views.appended))
local_patterns = [path("loc/", views.local_one, name="loc")]
urlpatterns += [path("nested/", include((local_patterns, "nest")))]
'''


def gate_a_urlconf() -> None:
    print("\n-- A: one Django URLconf -> ROUTES_TO module -> view (LOCAL here, NAME + 'spec:' when imported) --")
    e = _edges(POLLS_URLS, "polls/urls.py")
    routes = [x for x in e if x.edge_type == "ROUTES_TO"]
    check("every route edge's src is the URLconf module (the stem, as every Python route)",
          routes and all(x.src_name == "urls" and x.src_file == "polls/urls.py" for x in routes), routes[:3])
    check("every Django route carries framework 'django' and method '*'",
          routes and all(x.attrs.get("framework") == "django" and x.attrs.get("method") == "*" for x in routes),
          [x.attrs for x in routes][:3])
    r = _route(e, "index")
    check("path('', views.index, name='index') -> index, NAME, scope 'spec:.views', path '/', url_name 'index'",
          r is not None and r.conf_hint == "NAME" and _scope(r) == "spec:.views"
          and r.attrs.get("path") == "/" and r.attrs.get("url_name") == "index", r)
    r = _route(e, "DetailView")
    check("`detail = views.DetailView.as_view()` alias -> the CBV CLASS DetailView ('spec:.views')",
          r is not None and _scope(r) == "spec:.views" and r.attrs.get("path") == "/<int:pk>/", r)
    r = _route(e, "ResultsView")
    check("`from .views import ResultsView as RV` + RV.as_view() -> ResultsView (the imported name, not the alias)",
          r is not None and _scope(r) == "spec:.views" and _route(e, "RV") is None, r)
    r = _route(e, "vote")
    check("a from-imported function view -> vote ('spec:.views')", r is not None and _scope(r) == "spec:.views", r)
    r = _route(e, "ping")
    check("a view DEFINED in the URLconf -> LOCAL, no scope (EXTRACTED at push)",
          r is not None and r.conf_hint == "LOCAL" and _scope(r) == "" and r.attrs.get("path") == "/ping/", r)
    r = _route(e, "secret")
    check("login_required(views.secret) -> the wrapped view secret", r is not None and r.attrs.get("path") == "/secret/", r)
    r = _route(e, "cached")
    check("cache_page(60 * 15)(views.cached) -> the wrapped view cached", r is not None, routes)
    r = _route(e, "year_archive")
    check("re_path(r'^archive/(?P<year>[0-9]{4})/$') -> path '/archive/(?P<year>[0-9]{4})/' (anchors dropped)",
          r is not None and r.attrs.get("path") == "/archive/(?P<year>[0-9]{4})/", r)
    r = _route(e, "legacy")
    check("django.conf.urls url(r'^legacy/$') -> path '/legacy/'", r is not None and r.attrs.get("path") == "/legacy/", r)
    r = _route(e, "inline_a")
    check("include([path('a/', ...)]) under 'inline/' -> '/inline/a/', url_name 'a' (an inline list has no namespace)",
          r is not None and r.attrs.get("path") == "/inline/a/" and r.attrs.get("url_name") == "a", r)
    r = _route(e, "inline_c")
    check("nested include(([...], 'deep')) -> '/inline/b/c/', url_name 'deep:c'",
          r is not None and r.attrs.get("path") == "/inline/b/c/" and r.attrs.get("url_name") == "deep:c", r)
    check("urlpatterns += [...] and urlpatterns.append(path(...)) are read",
          getattr(_route(e, "plus"), "attrs", {}).get("path") == "/plus/"
          and getattr(_route(e, "appended"), "attrs", {}).get("path") == "/appended/", routes)
    r = _route(e, "local_one")
    check("a module-level list include((local_patterns, 'nest'))d under 'nested/' -> ONLY '/nested/loc/', 'nest:loc'",
          r is not None and r.attrs.get("path") == "/nested/loc/" and "paths" not in r.attrs
          and r.attrs.get("url_name") == "nest:loc", r)
    check("no edge from a docstring or a comment that spells a route",
          not _route(e, "not_a_route") and not _route(e, "not_a_route_either"), routes)
    check("include(api_patterns) of an imported list adds no edge here (compose_route_prefixes follows it)",
          len(routes) == 14, [(x.dst_name, x.attrs.get("path")) for x in routes])
    mixed = ("from fastapi import APIRouter\nfrom django.urls import path\nfrom . import views\n"
             "router = APIRouter(prefix='/r')\n@router.get('/x')\ndef x(): pass\n"
             "urlpatterns = [path('d/', views.d)]\n")
    got = {x.dst_name: x.attrs.get("path") for x in _edges(mixed, "app/mixed.py") if x.edge_type == "ROUTES_TO"}
    check("a module with a FastAPI router AND urlpatterns: the router route still composes ('/r/x'), the Django one "
          "keeps its own path (the route facts stay aligned 1:1)", got == {"x": "/r/x", "d": "/d/"}, got)

    dup = ('from django.urls import path\nfrom . import views\n'
           'urlpatterns = [path("a/", views.index, name="a"), path("b/", views.index, name="b")]\n')
    got = [x for x in _edges(dup, "app/urls.py") if x.edge_type == "ROUTES_TO"]
    check("one view routed twice -> both edges carry every path and url name (they collapse into one at push)",
          len(got) == 2 and all(x.attrs.get("paths") == ["/a/", "/b/"] and x.attrs.get("url_names") == ["a", "b"]
                                for x in got), got)
    alias = ('import django.urls\nfrom django import urls as dj\nfrom . import views\n'
             'urlpatterns = [django.urls.path("x/", views.x), dj.re_path(r"^y/$", views.y)]\n')
    got = {x.dst_name for x in _edges(alias, "app/urls.py") if x.edge_type == "ROUTES_TO"}
    check("django.urls.path(...) / `from django import urls as dj` + dj.re_path(...) are Django's", got == {"x", "y"}, got)
    i18n = ('from django.conf.urls.i18n import i18n_patterns\nfrom django.urls import path\nfrom . import views\n'
            'urlpatterns = i18n_patterns(path("about/", views.about))\n')
    r = _route(_edges(i18n, "app/urls.py"), "about")
    check("i18n_patterns(path('about/', ...)) -> path '/about/' + prefix_unresolved 'i18n_patterns' (never guessed)",
          r is not None and r.attrs.get("path") == "/about/" and r.attrs.get("prefix_unresolved") == "i18n_patterns", r)
    dyn = ('from django.urls import path\nfrom django.conf import settings\nfrom . import views\n'
           'urlpatterns = [path(settings.ADMIN_URL, views.admin_home)]\n')
    r = _route(_edges(dyn, "app/urls.py"), "admin_home")
    check("a non-literal route -> NO path (never '/', the site root; a Flask non-literal rule has none either) + "
          "prefix_unresolved names the expression",
          r is not None and "path" not in r.attrs and "paths" not in r.attrs
          and r.attrs.get("prefix_unresolved") == "settings.ADMIN_URL", r)


# =====================================================================================================
# B — Django shapes that must NOT become routes
# =====================================================================================================
NOT_ROUTES = {
    "a pathlib / os `path` (from os import path)": (
        "from os import path\nfrom pathlib import Path\nfrom . import views\n"
        "urlpatterns = [path('x/', views.x)]\n"),
    "a local def named path": (
        "from . import views\ndef path(route, view, name=None):\n    return (route, view)\n"
        "urlpatterns = [path('x/', views.x)]\n"),
    "another library's url() / include()": (
        "from mylib.routing import url, include\nfrom . import views\n"
        "urlpatterns = [url('x/', views.x), url('y/', include('polls.urls'))]\n"),
    "a Starlette Route / Mount list named urlpatterns": (
        "from starlette.routing import Route, Mount\nfrom . import views\n"
        "urlpatterns = [Route('/x', views.x), Mount('/m', routes=[])]\n"),
    "path() lists inside a def, a class body, a string; urlpatterns of strings": (
        "from django.urls import path, reverse\nfrom . import views\n"
        "def get_urls():\n    urlpatterns = [path('fn/', views.in_function)]\n    return urlpatterns\n"
        "class Conf:\n    urlpatterns = [path('cls/', views.in_class)]\n"
        "urlpatterns = ['a', 'b']\nDOC = \"path('doc/', views.in_string)\"\n"),
    "only `reverse` imported from django.urls (path is unbound)": (
        "from django.urls import reverse\nfrom . import views\nurlpatterns = [path('x/', views.x)]\n"),
    "URLconf objects and non-views (admin.site.urls, router.urls, ninja api.urls, lambda, factory, variable)": (
        "import functools\nfrom django.contrib import admin\nfrom django.urls import include, path\n"
        "from rest_framework.routers import DefaultRouter\nfrom ninja import NinjaAPI\nfrom . import views\n"
        "router = DefaultRouter()\napi = NinjaAPI()\nv = views.Factory()\n"
        "urlpatterns = [\n    path('admin/', admin.site.urls),\n    path('ninja/', api.urls),\n"
        "    path('drf/', include(router.urls)),\n    path('lambda/', lambda request: None),\n"
        "    path('factory/', make_view(Order)),\n    path('var/', v),\n"
        "    path('partial/', functools.partial(views.x, a=1)),\n]\n"),
    "a view variable a module-level for / with / unpacking / except / walrus binds (a value, not a def)": (
        "from django.urls import path\nfrom . import cbv\nurlpatterns = []\n"
        "for slug, handler in [('a', cbv.a)]:\n    urlpatterns.append(path(slug + '/', handler))\n"
        "with open('x') as fh:\n    urlpatterns.append(path('h/', fh))\n"
        "first, *rest = cbv.views\nurlpatterns += [path('f/', first), path('r/', rest)]\n"
        "try:\n    import missing\nexcept ImportError as err:\n    urlpatterns.append(path('e/', err))\n"
        "if (chosen := cbv.pick()):\n    urlpatterns.append(path('w/', chosen))\n"),
}


def gate_b_not_routes() -> None:
    print("\n-- B: shapes that must NOT become Django routes (KL-mistake:008) --")
    for what, src in NOT_ROUTES.items():
        got = [x for x in _edges(src, "app/urls.py") if x.edge_type == "ROUTES_TO"]
        check(f"no ROUTES_TO from {what}", got == [], got)
    copy = ("from django.urls import include, path\nfrom . import views\n"
            "api_patterns = [path('users/', views.users)]\n"
            "urlpatterns = [path('api/', include(api_patterns))]\n"
            "debug_copy = list(api_patterns)\nalso = sorted(api_patterns)\n")
    r = _route(_edges(copy, "app/urls.py"), "users")
    check("a module-level name that only copies a pattern list adds no root path ('/api/users/' only)",
          r is not None and r.attrs.get("path") == "/api/users/" and "paths" not in r.attrs, r)
    flask = ("from flask import Flask\napp = Flask(__name__)\n@app.route('/x')\ndef x(): pass\n")
    r = _route(_edges(flask, "app.py"), "x")
    check("control: a Flask route keeps exactly {method, path} (no Django attrs)",
          r is not None and r.attrs == {"method": "*", "path": "/x"}, r)


# =====================================================================================================
# C — across modules: include() composition, view placement, parity, exclusion
# =====================================================================================================
SITE = {
    "mysite/__init__.py": "",
    "mysite/urls.py": (
        "from django.contrib import admin\nfrom django.urls import include, path\n"
        "from django.views.generic import TemplateView\nfrom shop import views as shop_views\n"
        "urlpatterns = [\n"
        "    path('admin/', admin.site.urls),\n"
        "    path('polls/', include('polls.urls')),\n"
        "    path('api/', include(('api.urls', 'api'), namespace='v1')),\n"
        "    path('shop/', shop_views.index, name='shop'),\n"
        "    path('', TemplateView.as_view(template_name='home.html'), name='home'),\n"
        "    path('accounts/', include('django.contrib.auth.urls')),\n"
        "]\n"),
    "polls/__init__.py": "",
    "polls/urls.py": POLLS_URLS,
    "polls/views.py": (
        "from django.views import generic\nfrom django.contrib.auth.views import LoginView\n"
        "from .more import vote\n"
        "def index(request): pass\nclass DetailView(generic.DetailView): pass\n"
        "class ResultsView(generic.DetailView): pass\n"
        "def secret(request): pass\ndef cached(request): pass\ndef year_archive(request, year): pass\n"
        "def legacy(request): pass\ndef inline_a(request): pass\ndef inline_c(request): pass\n"
        "def plus(request): pass\ndef appended(request): pass\ndef local_one(request): pass\n"),
    "polls/more.py": "def vote(request, question_id): pass\n",
    "polls/api.py": ("from django.urls import path\nfrom .api_views import *\n"
                     "api_patterns = [path('stats/', stats, name='stats')]\n"),
    "polls/api_views.py": "def stats(request): pass\n",
    "api/__init__.py": "",
    "api/urls.py": ("from django.urls import path\nfrom api.views import UserList\napp_name = 'api'\n"
                    "urlpatterns = [path('users/', UserList.as_view(), name='users')]\n"),
    "api/views.py": "class UserList: pass\n",
    "shop/__init__.py": "",
    "shop/views.py": "def index(request): pass\n",
    "other/__init__.py": "",
    "other/views.py": "def index(request): pass\nclass DetailView: pass\ndef vote(request): pass\n",
}
WORKTREE = {".worktrees/feat/mysite/urls.py": ("from django.urls import include, path\n"
                                               "urlpatterns = [path('v2/', include('polls.urls'))]\n")}


_SYNC: list = []


def _sync():
    """build/sync.py of the tree under test, loaded once (it imports the same framework_edges module)."""
    if not _SYNC:
        import importlib.util
        spec = importlib.util.spec_from_file_location("dm_sync_dj_sqla2", BUILD / "sync.py")
        S = importlib.util.module_from_spec(spec)
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            spec.loader.exec_module(S)
        _SYNC.append(S)
    return _SYNC[0]


def _sync_exclusion(root: Path):
    S = _sync()
    return S, S._recognizer_exclusion(root)


def gate_c_cross_module() -> None:
    print("\n-- C: include() across modules, imported views placed where DEFINED, parity, exclusion --")
    root = _write(_fresh("site"), {**SITE, **WORKTREE})
    S, excluded = _sync_exclusion(root)
    with _flags(), contextlib.redirect_stderr(io.StringIO()):
        _supp, edges = LR.run(root, exclude=excluded)
    routes = {(x.src_file, x.dst_name): x for x in edges if x.edge_type == "ROUTES_TO"}

    def at(rel, dst):
        return routes.get((rel, dst))
    r = at("polls/urls.py", "index")
    check("include('polls.urls') under 'polls/' -> '/polls/'; url_name 'polls:index' (the included app_name)",
          r is not None and r.attrs.get("path") == "/polls/" and r.attrs.get("url_name") == "polls:index", r)
    check("... and the imported view is placed in the module that defines it: 'exact:module:polls/views', NAME",
          r is not None and _scope(r) == "exact:module:polls/views" and r.conf_hint == "NAME", r)
    r = at("polls/urls.py", "vote")
    check("a view re-exported by polls/views.py (`from .more import vote`) -> 'exact:module:polls/more'",
          r is not None and _scope(r) == "exact:module:polls/more", r)
    r = at("polls/urls.py", "DetailView")
    check("the `detail = views.DetailView.as_view()` alias -> DetailView in polls/views",
          r is not None and _scope(r) == "exact:module:polls/views"
          and r.attrs.get("path") == "/polls/<int:pk>/" and r.attrs.get("url_name") == "polls:detail", r)
    r = at("polls/urls.py", "inline_c")
    check("nested inline namespaces compose under the module's: 'polls:deep:c' at '/polls/inline/b/c/'",
          r is not None and r.attrs.get("url_name") == "polls:deep:c" and r.attrs.get("path") == "/polls/inline/b/c/", r)
    r = at("polls/urls.py", "ping")
    check("a URLconf's own view stays LOCAL (no scope) and gets the composed path '/polls/ping/'",
          r is not None and r.conf_hint == "LOCAL" and _scope(r) == "" and r.attrs.get("path") == "/polls/ping/", r)
    r = at("polls/api.py", "stats")
    check("include(api_patterns) of a list imported from polls/api.py -> '/polls/api/stats/', 'polls:stats'",
          r is not None and r.attrs.get("path") == "/polls/api/stats/" and r.attrs.get("url_name") == "polls:stats", r)
    check("... and its star-imported view (`from .api_views import *`) is placed in polls/api_views",
          r is not None and _scope(r) == "exact:module:polls/api_views" and r.conf_hint == "NAME", r)
    r = at("api/urls.py", "UserList")
    check("include(('api.urls', 'api'), namespace='v1') -> '/api/users/', url_name 'v1:users' (namespace= wins)",
          r is not None and r.attrs.get("path") == "/api/users/" and r.attrs.get("url_name") == "v1:users"
          and _scope(r) == "exact:module:api/views", r)
    r = at("mysite/urls.py", "index")
    check("`from shop import views as shop_views` + shop_views.index -> placed in shop/views (not other/views)",
          r is not None and _scope(r) == "exact:module:shop/views" and r.attrs.get("path") == "/shop/", r)
    r = at("mysite/urls.py", "TemplateView")
    check("a view imported from OUTSIDE the tree keeps its 'spec:' scope (binds nothing at push)",
          r is not None and _scope(r) == "spec:django.views.generic", r)
    check("include('django.contrib.auth.urls') (outside the tree) and admin.site.urls add no edge",
          not any(k[1] in ("urls", "site", "admin") for k in routes), sorted(routes))
    check("sync's exclusion rule: the .worktrees/ copy that mounts polls.urls at 'v2/' adds no path",
          all("/v2/" not in str(x.attrs) for x in routes.values())
          and not any(k[0].startswith(".worktrees/") for k in routes), sorted(routes))
    with _flags(), contextlib.redirect_stderr(io.StringIO()):
        _s, unexcluded = LR.run(root)
    r = next((x for x in unexcluded if x.edge_type == "ROUTES_TO" and x.src_file == "polls/urls.py"
              and x.dst_name == "index"), None)
    check("control: without the predicate the in-tree copy IS read and adds '/v2/' (the predicate is what keeps it out)",
          r is not None and "/v2/" in (r.attrs.get("paths") or []), r)

    root2 = _write(_fresh("site_noworktree"), SITE)
    with _flags(), contextlib.redirect_stderr(io.StringIO()):
        _supp, reg = LR.run(root2)
        legacy = FE.extract_framework_edges(root2)
    check("registry run() == legacy extract_framework_edges() on a Django tree (same edges, same order)",
          reg == legacy, f"{len(reg)} vs {len(legacy)}")

    twice = dict(SITE)
    twice["mysite/urls.py"] = SITE["mysite/urls.py"].replace(
        "    path('shop/',", "    path('again/', include('polls.urls', namespace='again')),\n    path('shop/',")
    root3 = _write(_fresh("site_twice"), twice)
    with _flags(), contextlib.redirect_stderr(io.StringIO()):
        _s, e3 = LR.run(root3)
    r = next((x for x in e3 if x.edge_type == "ROUTES_TO" and x.src_file == "polls/urls.py"
              and x.dst_name == "ping"), None)
    check("a URLconf included twice -> paths ['/again/ping/', '/polls/ping/'] (path = the first)",
          r is not None and r.attrs.get("paths") == ["/again/ping/", "/polls/ping/"]
          and r.attrs.get("path") == "/again/ping/", r)
    r = next((x for x in e3 if x.edge_type == "ROUTES_TO" and x.src_file == "polls/urls.py"
              and x.dst_name == "index"), None)
    check("... and its url names under both namespaces ('again:index', 'polls:index')",
          r is not None and r.attrs.get("url_names") == ["again:index", "polls:index"], r)

    cyc = {"a/__init__.py": "", "a/urls.py": ("from django.urls import include, path\nfrom . import views\n"
                                              "urlpatterns = [path('b/', include('b.urls')), path('x/', views.x)]\n"),
           "a/views.py": "def x(r): pass\n",
           "b/__init__.py": "", "b/urls.py": ("from django.urls import include, path\n"
                                              "urlpatterns = [path('a/', include('a.urls'))]\n")}
    root4 = _write(_fresh("cycle"), cyc)
    with _flags(), contextlib.redirect_stderr(io.StringIO()):
        _s, e4 = LR.run(root4)
    check("an include() CYCLE terminates and still yields the route", any(
        x.edge_type == "ROUTES_TO" and x.dst_name == "x" for x in e4), e4)


# =====================================================================================================
# D — SQLAlchemy 2.0 typed declarations
# =====================================================================================================
SQLA = '''from __future__ import annotations
from typing import TYPE_CHECKING, List, Optional
from sqlalchemy import ForeignKey, JSON, String
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import DeclarativeBase, Mapped, WriteOnlyMapped, backref, mapped_column, relationship
if TYPE_CHECKING:
    from shop.customer import Customer
    from shop.tag import Tag as TagModel


class Base(DeclarativeBase):
    pass


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"))
    customer: Mapped["Customer"] = relationship(back_populates="orders")
    items: Mapped[list["LineItem"]] = relationship(back_populates="order", cascade="all, delete-orphan")
    notes: Mapped[List[Note]] = relationship(cascade="save-update, merge")
    tags: Mapped[set[TagModel]] = relationship(secondary="order_tags")
    coupon: Mapped[Optional["Coupon"]] = relationship()
    voucher: Mapped["Voucher | None"] = relationship()
    events: WriteOnlyMapped["Event"] = relationship()
    by_code: Mapped[dict[str, "Discount"]] = relationship()
    whole: "Mapped[list[Shipment]]" = relationship()
    legacy: Mapped["Anything"] = relationship("Invoice")
    created: Mapped[Optional[datetime]] = mapped_column()
    data: Mapped[dict[str, Any]] = mapped_column(JSON)
    codes: Mapped[list[str]] = mapped_column(ARRAY(String))


class LineItem(Base):
    __tablename__ = "line_items"
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"))
    order: Mapped[Order] = relationship(back_populates="items")
    product_id = mapped_column(ForeignKey("products.id", ondelete="SET NULL"))
    owner: Mapped["Owner"] = relationship(backref=backref("line_items", cascade="all, delete-orphan"))
    batch: Mapped["Batch"] = relationship(backref="line_items")
    shelf: Mapped[list["Shelf"]] = relationship(backref="line_item")
'''


def _has(edges, et, src, dst, scope=None, **attrs) -> bool:
    for e in edges:
        if e.edge_type == et and e.src_name == src and e.dst_name == dst \
                and (scope is None or _scope(e) == scope) and all((e.attrs or {}).get(k) == v for k, v in attrs.items()):
            return True
    return False


def gate_d_sqla_typed() -> None:
    print("\n-- D: SQLAlchemy 2.0 typed declarations (Mapped[...]) -> HAS_MANY / REFERENCES / CASCADES --")
    e = _edges(SQLA, "shop/models.py")
    check("Mapped[list['LineItem']] + relationship() -> HAS_MANY Order -> LineItem (same-file: no scope)",
          _has(e, "HAS_MANY", "Order", "LineItem", scope=""), _of(e, "HAS_MANY", "Order"))
    check("... cascade='all, delete-orphan' -> CASCADES Order -> LineItem (principal-first), strategy + property",
          _has(e, "CASCADES", "Order", "LineItem", strategy="all, delete-orphan", via="sqlalchemy", property="items"),
          _of(e, "CASCADES"))
    check("Mapped[List[Note]] -> HAS_MANY Order -> Note; cascade='save-update, merge' deletes nothing -> no CASCADES",
          _has(e, "HAS_MANY", "Order", "Note") and not _of(e, "CASCADES", "Order", "Note"), _of(e, "CASCADES"))
    check("Mapped[set[TagModel]] (`from shop.tag import Tag as TagModel`) -> HAS_MANY Order -> Tag, 'spec:shop.tag'",
          _has(e, "HAS_MANY", "Order", "Tag", scope="spec:shop.tag"), _of(e, "HAS_MANY", "Order"))
    check("Mapped['Customer'] (a TYPE_CHECKING import) -> REFERENCES Order -> Customer, 'spec:shop.customer'",
          _has(e, "REFERENCES", "Order", "Customer", scope="spec:shop.customer"), _of(e, "REFERENCES", "Order"))
    check("Mapped[Optional['Coupon']] -> REFERENCES Order -> Coupon", _has(e, "REFERENCES", "Order", "Coupon"))
    check("Mapped['Voucher | None'] -> REFERENCES Order -> Voucher", _has(e, "REFERENCES", "Order", "Voucher"))
    check("WriteOnlyMapped['Event'] (always a collection) -> HAS_MANY Order -> Event",
          _has(e, "HAS_MANY", "Order", "Event"))
    check("Mapped[dict[str, 'Discount']] (a keyed collection) -> HAS_MANY Order -> Discount",
          _has(e, "HAS_MANY", "Order", "Discount"))
    check("a whole-string annotation 'Mapped[list[Shipment]]' -> HAS_MANY Order -> Shipment",
          _has(e, "HAS_MANY", "Order", "Shipment"))
    check("relationship('Invoice') names the target; the scalar annotation the cardinality -> REFERENCES Order -> Invoice",
          _has(e, "REFERENCES", "Order", "Invoice") and not _of(e, "REFERENCES", "Order", "Anything"),
          _of(e, "REFERENCES", "Order"))
    check("mapped_column(ForeignKey('customers.id')) -> REFERENCES Order -> Customers (no class here maps the table)",
          _has(e, "REFERENCES", "Order", "Customers", foreign_key="customers.id"), _of(e, "REFERENCES", "Order"))
    check("... ondelete='CASCADE' -> CASCADES Customers -> Order (the referenced table is the principal)",
          _has(e, "CASCADES", "Customers", "Order", strategy="CASCADE", via="sqlalchemy", property="customer_id",
               foreign_key="customers.id"), _of(e, "CASCADES"))
    check("ForeignKey('orders.id') -> Order (its __tablename__ in this module), REFERENCES LineItem -> Order",
          _has(e, "REFERENCES", "LineItem", "Order", foreign_key="orders.id"), _of(e, "REFERENCES", "LineItem"))
    check("... ondelete='CASCADE' -> CASCADES Order -> LineItem (property order_id)",
          _has(e, "CASCADES", "Order", "LineItem", strategy="CASCADE", property="order_id"), _of(e, "CASCADES"))
    check("an un-annotated mapped_column(ForeignKey(..., ondelete='SET NULL')) -> REFERENCES + CASCADES 'SET NULL'",
          _has(e, "REFERENCES", "LineItem", "Products") and _has(e, "CASCADES", "Products", "LineItem",
                                                                   strategy="SET NULL"), _of(e, "CASCADES"))
    check("backref=backref('line_items', cascade='all, delete-orphan') -> CASCADES Owner -> LineItem (the target's "
          "collection cascades) + the reverse HAS_MANY Owner -> LineItem",
          _has(e, "CASCADES", "Owner", "LineItem", strategy="all, delete-orphan", property="line_items")
          and _has(e, "HAS_MANY", "Owner", "LineItem") and _has(e, "REFERENCES", "LineItem", "Owner"),
          [x for x in e if "Owner" in (x.src_name, x.dst_name)])
    check("backref='line_items' on a scalar -> the reverse HAS_MANY Batch -> LineItem, and no CASCADES",
          _has(e, "HAS_MANY", "Batch", "LineItem") and not _of(e, "CASCADES", "Batch"), _of(e, "HAS_MANY", "Batch"))
    check("backref='line_item' on a one-to-many -> the reverse REFERENCES Shelf -> LineItem",
          _has(e, "HAS_MANY", "LineItem", "Shelf") and _has(e, "REFERENCES", "Shelf", "LineItem"),
          [x for x in e if "Shelf" in (x.src_name, x.dst_name)])
    check("mapped_column() without a ForeignKey (Optional[datetime], dict[str, Any], list[str]) emits nothing",
          not any(x.dst_name in ("datetime", "Any", "str", "dict", "list") for x in e), e)

    so = ("import sqlalchemy as sa\nimport sqlalchemy.orm as so\nfrom app import db\n\n"
          "class User(db.Model):\n    posts: so.WriteOnlyMapped['Post'] = so.relationship(back_populates='author')\n\n"
          "class Post(db.Model):\n    user_id: so.Mapped[int] = so.mapped_column(sa.ForeignKey(User.id), index=True)\n"
          "    author: so.Mapped[User] = so.relationship(back_populates='posts')\n")
    e = _edges(so, "app/models.py")
    check("`import sqlalchemy.orm as so`: so.WriteOnlyMapped['Post'] -> HAS_MANY User -> Post",
          _has(e, "HAS_MANY", "User", "Post"), e)
    check("so.Mapped[User] -> REFERENCES Post -> User; sa.ForeignKey(User.id) -> User too (the class, not 'id')",
          _has(e, "REFERENCES", "Post", "User") and not _of(e, "REFERENCES", dst="id"), e)

    classic = ("from sqlalchemy import Column, ForeignKey, Integer\nfrom sqlalchemy.orm import relationship\n"
               "class Parent(Base):\n    kids = relationship('Child', cascade='all, delete-orphan')\n"
               "    pets = relationship('Pet', backref='owner')\n"
               "class Child(Base):\n    pid = Column(Integer, ForeignKey('parent.id', ondelete='CASCADE'))\n"
               "    gid = Column(ForeignKey('group_member.id'))\n")
    e = _edges(classic, "m.py")
    check("classic relationship('Child') is still HAS_MANY Parent -> Child with attrs {} (unchanged)",
          any(x.edge_type == "HAS_MANY" and x.src_name == "Parent" and x.dst_name == "Child" and x.attrs == {}
              for x in e), e)
    check("classic relationship(..., cascade='all, delete-orphan') -> CASCADES Parent -> Child",
          _has(e, "CASCADES", "Parent", "Child", strategy="all, delete-orphan"), _of(e, "CASCADES"))
    check("classic backref='owner' adds no reverse edge (the classic side's cardinality is unknown)",
          not _of(e, "REFERENCES", "Pet") and not _of(e, "HAS_MANY", "Pet"), e)
    check("classic Column(ForeignKey('parent.id', ondelete='CASCADE')) -> REFERENCES Child -> Parent + CASCADES",
          _has(e, "REFERENCES", "Child", "Parent") and _has(e, "CASCADES", "Parent", "Child", strategy="CASCADE"), e)
    check("a snake_case table with no __tablename__ in view -> its CamelCased class: 'group_member' -> GroupMember",
          _has(e, "REFERENCES", "Child", "GroupMember"), _of(e, "REFERENCES", "Child"))


# =====================================================================================================
# E — SQLAlchemy shapes that must NOT be read as typed relations
# =====================================================================================================
NOT_SQLA = {
    "another library's Mapped + a relationship() naming no target": (
        "from mylib.typing import Mapped\nfrom neomodel import relationship\n"
        "class Person:\n    friends: Mapped[list['Person']] = relationship()\n"
        "    boss: Mapped['Boss'] = relationship()\n"),
    "a Mapped class this module defines": (
        "from typing import Generic, TypeVar\nfrom sqlalchemy.orm import relationship\nT = TypeVar('T')\n"
        "class Mapped(Generic[T]):\n    pass\nclass A:\n    bs: Mapped[list['B']] = relationship()\n"),
    "SQLModel Relationship() / Field() (another library; not read)": (
        "from sqlmodel import Field, Relationship, SQLModel\n"
        "class Team(SQLModel, table=True):\n"
        "    heroes: list['Hero'] = Relationship(back_populates='team', cascade_delete=True)\n"
        "class Hero(SQLModel, table=True):\n"
        "    team_id: int | None = Field(default=None, foreign_key='team.id', ondelete='CASCADE')\n"
        "    team: Team | None = Relationship(back_populates='heroes')\n"),
    "pydantic / dataclass fields": (
        "from dataclasses import dataclass, field\nfrom pydantic import BaseModel, Field\n"
        "class OrderOut(BaseModel):\n    items: list['ItemOut'] = Field(default_factory=list)\n"
        "    customer: 'CustomerOut'\n"
        "@dataclass\nclass Cart:\n    lines: list['Line'] = field(default_factory=list)\n"),
    "a module-level Table() with a ForeignKey (not a model class)": (
        "import sqlalchemy as sa\nfollowers = sa.Table('followers', db.metadata,\n"
        "    sa.Column('follower_id', sa.ForeignKey('user.id', ondelete='CASCADE')))\n"),
}


def gate_e_not_sqla() -> None:
    print("\n-- E: SQLAlchemy shapes that must NOT become typed relations / cascades (KL-mistake:008) --")
    for what, src in NOT_SQLA.items():
        got = [x for x in _edges(src, "m.py") if x.edge_type in ("HAS_MANY", "REFERENCES", "CASCADES")]
        check(f"no ORM edge from {what}", got == [], got)
    keep = ("from sqlalchemy import Column, ForeignKey, Integer\n"
            "from sqlalchemy.orm import Mapped, backref, mapped_column, relationship\n"
            "class A(Base):\n"
            "    bs: Mapped[list['B']] = relationship(cascade='save-update, merge, refresh-expire')\n"
            "    c_id: Mapped[int] = mapped_column(ForeignKey('c.id', ondelete='RESTRICT'))\n"
            "    d_id = Column(Integer, ForeignKey('d.id', onupdate='CASCADE'))\n"
            "    e: Mapped['E'] = relationship(backref=backref('as_', cascade='save-update'))\n")
    e = _edges(keep, "m.py")
    check("non-deleting cascades / ondelete='RESTRICT' / onupdate='CASCADE' -> no CASCADES at all",
          not _of(e, "CASCADES"), _of(e, "CASCADES"))
    check("... while the relations themselves are read (HAS_MANY A->B, REFERENCES A->C / A->D / A->E)",
          _has(e, "HAS_MANY", "A", "B") and _has(e, "REFERENCES", "A", "C") and _has(e, "REFERENCES", "A", "D")
          and _has(e, "REFERENCES", "A", "E"), e)


# =====================================================================================================
# F — Django ORM cascades
# =====================================================================================================
DJ_MODELS = '''from django.conf import settings
from django.contrib.auth.models import User
from django.db import models
from django.db.models import CASCADE, SET_NULL
from library.models import Publisher


class Author(models.Model):
    name = models.CharField(max_length=100)


class Book(models.Model):
    author = models.ForeignKey(Author, on_delete=models.CASCADE)
    editor = models.ForeignKey("library.Editor", on_delete=SET_NULL, null=True)
    publisher = models.ForeignKey(Publisher, models.CASCADE)
    owner = models.ForeignKey(User, on_delete=models.CASCADE)
    parent = models.ForeignKey("self", on_delete=models.CASCADE, null=True)
    series = models.ForeignKey("Series", on_delete=models.PROTECT)
    translator = models.ForeignKey("Translator", on_delete=models.DO_NOTHING)
    cover = models.OneToOneField("Cover", on_delete=CASCADE)
    tags = models.ManyToManyField("Tag")
'''


def gate_f_django_orm() -> None:
    print("\n-- F: Django on_delete -> CASCADES referenced -> declaring; lazy 'app_label.Model' references --")
    e = _edges(DJ_MODELS, "library/books.py")
    check("ForeignKey(Author, on_delete=models.CASCADE) -> REFERENCES Book -> Author + CASCADES Author -> Book",
          _has(e, "REFERENCES", "Book", "Author") and _has(e, "CASCADES", "Author", "Book", strategy="CASCADE",
                                                           via="django", property="author"), _of(e, "CASCADES"))
    check("ForeignKey('library.Editor', on_delete=SET_NULL) -> Editor (the model, not 'library.Editor'), SET_NULL",
          _has(e, "REFERENCES", "Book", "Editor") and _has(e, "CASCADES", "Editor", "Book", strategy="SET_NULL")
          and not _of(e, "REFERENCES", dst="library.Editor"), _of(e, "REFERENCES", "Book"))
    check("a positional on_delete (ForeignKey(Publisher, models.CASCADE)) + the model's import scope on both edges",
          _has(e, "REFERENCES", "Book", "Publisher", scope="spec:library.models")
          and _has(e, "CASCADES", "Publisher", "Book", scope="spec:library.models"), _of(e, "CASCADES"))
    check("an imported auth User carries 'spec:django.contrib.auth.models' (outside a tree: binds nothing)",
          _has(e, "REFERENCES", "Book", "User", scope="spec:django.contrib.auth.models"), _of(e, "REFERENCES", "Book"))
    check("'self' -> the declaring class (a self-edge, dropped at push)", _has(e, "REFERENCES", "Book", "Book"), e)
    check("on_delete=PROTECT / DO_NOTHING -> no CASCADES", not _of(e, "CASCADES", "Series")
          and not _of(e, "CASCADES", "Translator"), _of(e, "CASCADES"))
    check("OneToOneField('Cover', on_delete=CASCADE) -> CASCADES Cover -> Book",
          _has(e, "CASCADES", "Cover", "Book"), _of(e, "CASCADES"))
    check("ManyToManyField('Tag') stays HAS_MANY Book -> Tag (no cascade)",
          _has(e, "HAS_MANY", "Book", "Tag") and not _of(e, "CASCADES", "Tag"), e)


# =====================================================================================================
# G — push-time resolution on real ast-extractor nodes
# =====================================================================================================
ORM_TREE = {
    "shop/__init__.py": "",
    "shop/models.py": SQLA,
    "shop/customer.py": ("from sqlalchemy.orm import Mapped, mapped_column\n"
                         "class Customer(Base):\n    __tablename__ = 'customers'\n"
                         "    id: Mapped[int] = mapped_column(primary_key=True)\n"),
    "shop/tag.py": "class Tag(Base):\n    __tablename__ = 'tags'\n",
    "schemas/__init__.py": "",
    "schemas/order.py": ("from pydantic import BaseModel\nclass Customer(BaseModel):\n    name: str\n"
                         "class LineItem(BaseModel):\n    qty: int\nclass Tag(BaseModel):\n    label: str\n"),
    "library/__init__.py": "",
    "library/books.py": DJ_MODELS,
    "library/models.py": "class Publisher(models.Model):\n    pass\nclass Editor(models.Model):\n    pass\n",
    "accounts/__init__.py": "",
    "accounts/models.py": "class User(models.Model):\n    pass\n",
}
STAR = {"ext/__init__.py": "",
        "ext/urls.py": ("from django.urls import path\nfrom external_pkg.views import *\n"
                        "urlpatterns = [path('h/', helper), path('u/', unique_view)]\n"),
        "ext/a.py": "def helper(r): pass\n", "ext/b.py": "def helper(r): pass\n",
        "ext/c.py": "def unique_view(r): pass\n"}


def _resolved(root: Path) -> tuple:
    """(rows as readable tuples, the resolver) — sync's ast extraction + sync's recognizer seam + edge_resolve."""
    S = _sync()
    with contextlib.redirect_stderr(io.StringIO()):
        nodes, _calls, _imports = S._rebuild_code_ast(root)
        merged, typed = S._apply_recognizers(root, nodes)
    r = ER.Resolver(merged)
    rows = ER.resolve_typed_edges(typed, r, [n["key"] for n in merged])
    readable = [(et, r.name_of(a), r.file_of(a), r.name_of(b), r.file_of(b), c) for et, a, b, c, _at in rows]
    return readable, r, rows


def _rows(rows, et, src=None, dst=None, dst_file=None, src_file=None) -> list:
    return [x for x in rows if x[0] == et and (src is None or x[1] == src) and (dst is None or x[3] == dst)
            and (dst_file is None or x[4] == dst_file) and (src_file is None or x[2] == src_file)]


def gate_g_resolution() -> None:
    print("\n-- G: push-time resolution (edge_resolve) on real ast-extractor nodes: grades + no fan-out --")
    root = _write(_fresh("resolve"), {**SITE, **ORM_TREE, **STAR})
    with _flags(DEEPMAP_EXTRACTOR="ast"):
        rows, resolver, raw = _resolved(root)
    got = _rows(rows, "ROUTES_TO", "urls", "ping")
    check("a view defined in the URLconf -> EXTRACTED (declaration and target in one file)",
          [x[5] for x in got] == ["EXTRACTED"], got)
    got = _rows(rows, "ROUTES_TO", "urls", "index", src_file="polls/urls.py")
    check("an imported view -> INFERRED, bound to polls/views.py ONLY (other/views.py defines an `index` too)",
          [(x[4], x[5]) for x in got] == [("polls/views.py", "INFERRED")], got)
    got = _rows(rows, "ROUTES_TO", "urls", "DetailView")
    check("a CBV -> its :Class in polls/views.py, INFERRED (other/views.py's DetailView is not bound)",
          [(x[4], x[5]) for x in got] == [("polls/views.py", "INFERRED")], got)
    got = _rows(rows, "ROUTES_TO", "urls", "vote")
    check("a re-exported view -> where it is DEFINED (polls/more.py), not the re-exporting module or other/views.py",
          [(x[4], x[5]) for x in got] == [("polls/more.py", "INFERRED")], got)
    check("a view imported from outside the tree (TemplateView) -> no row",
          not _rows(rows, "ROUTES_TO", dst="TemplateView"), _rows(rows, "ROUTES_TO", src_file="mysite/urls.py"))
    got = _rows(rows, "ROUTES_TO", "urls", "helper")
    check("a name bound nowhere (an external star import) -> edge_resolve's global rule: 2 candidates -> AMBIGUOUS x2",
          sorted(x[5] for x in got) == ["AMBIGUOUS", "AMBIGUOUS"], got)
    got = _rows(rows, "ROUTES_TO", "urls", "unique_view")
    check("... 1 candidate -> INFERRED", [x[5] for x in got] == ["INFERRED"], got)

    got = _rows(rows, "REFERENCES", "Order", "Customer")
    check("Mapped['Customer'] (TYPE_CHECKING import) -> shop/customer.py's Customer ONLY, INFERRED "
          "(the pydantic schemas/order.py Customer is not bound)",
          sorted((x[4], x[5]) for x in got) == [("shop/customer.py", "INFERRED")], got)
    got = _rows(rows, "HAS_MANY", "Order", "Tag")
    check("Mapped[set[TagModel]] -> shop/tag.py's Tag ONLY, INFERRED", [(x[4], x[5]) for x in got]
          == [("shop/tag.py", "INFERRED")], got)
    got = _rows(rows, "HAS_MANY", "Order", "LineItem")
    check("a same-file forward reference ('LineItem') -> shop/models.py's LineItem, INFERRED (ORM edges are NAME)",
          [(x[4], x[5]) for x in got] == [("shop/models.py", "INFERRED")], got)
    got = _rows(rows, "CASCADES", "Customer", "Order")
    check("ForeignKey('customers.id', ondelete='CASCADE') -> the tree's Customer (its __tablename__, another file): "
          "CASCADES Customer -> Order, INFERRED", [(x[2], x[5]) for x in got] == [("shop/customer.py", "INFERRED")], got)
    got = _rows(rows, "REFERENCES", "Order", "Customer")
    check("... and REFERENCES Order -> Customer via the same table (no 'Customers' left unresolved)",
          got and not _rows(rows, "REFERENCES", "Order", "Customers")
          and all(x[4] == "shop/customer.py" for x in got), got)
    got = _rows(rows, "CASCADES", "Order", "LineItem")
    check("relationship cascade + ForeignKey ondelete on the same pair -> ONE row Order -> LineItem",
          len(got) == 1, got)
    got = _rows(rows, "CASCADES", "Publisher", "Book")
    check("Django ForeignKey(Publisher, models.CASCADE) with `from library.models import Publisher` -> "
          "CASCADES anchored at library/models.py's Publisher", [(x[2], x[5]) for x in got]
          == [("library/models.py", "INFERRED")], got)
    check("Django ForeignKey(User) imported from django.contrib.auth -> NO row, although accounts/models.py has a User",
          not _rows(rows, "REFERENCES", "Book", "User") and not _rows(rows, "CASCADES", "User", "Book"),
          _rows(rows, "REFERENCES", "Book"))
    check("Django 'library.Editor' -> library/models.py's Editor (REFERENCES + SET_NULL CASCADES)",
          _rows(rows, "REFERENCES", "Book", "Editor", dst_file="library/models.py")
          and _rows(rows, "CASCADES", "Editor", "Book"), _rows(rows, "REFERENCES", "Book"))

    class _Sess:
        def __init__(self):
            self.calls: list = []

        def run(self, cypher, **params):
            self.calls.append((cypher, params))
    sess = _Sess()
    casc = [row for row in raw if row[0] == "CASCADES" and resolver.name_of(row[1]) == "Author"]
    ER.write_edge_rows(sess, casc, resolver)
    ok = False
    for cypher, params in sess.calls:
        m = re.search(r"MERGE \((\w+)\)-\[\w*:CASCADES\]->\((\w+)\)", cypher)
        if m:
            row = params["rows"][0]
            ok = resolver.name_of(row[m.group(1)]) == "Book" and resolver.name_of(row[m.group(2)]) == "Author"
    check("CASCADES Author -> Book is persisted (Book)-[:CASCADES]->(Author): impact('Author') lists Book",
          len(casc) == 1 and ok, (casc, sess.calls))


FLAT = {   # a source root below the repo root: `from models import User` names backend/models.py, but the tree
    # also holds backend/billing/models.py, so the import is in the tree yet not placeable by its path
    "backend/models.py": "class User(Base):\n    __tablename__ = 'users'\n",
    "backend/billing/__init__.py": "",
    "backend/billing/models.py": "class Invoice(Base):\n    __tablename__ = 'invoices'\n",
    "backend/orders.py": ("from django.db import models\nfrom models import User\n"
                          "class Order(models.Model):\n    buyer = models.ForeignKey(User, on_delete=models.CASCADE)\n"),
    "backend/urls.py": ("from django.urls import path\nfrom views import index\n"
                        "urlpatterns = [path('', index)]\n"),
    "backend/views.py": "def index(request): pass\n",
    "backend/admin/views.py": "def other(request): pass\n",
    "backend/star.py": ("from sqlalchemy.orm import *\n"
                        "class Cart(Base):\n    lines: Mapped[list['Line']] = relationship()\n"),
}


def gate_g2_unplaceable_import() -> None:
    print("\n-- G2: an import the tree holds under several paths falls back to the global rule (never dropped) --")
    root = _write(_fresh("flat"), FLAT)
    with _flags(DEEPMAP_EXTRACTOR="ast"):
        rows, _r, _raw = _resolved(root)
    got = _rows(rows, "CASCADES", "User", "Order")
    check("`from models import User` (two models.py in the tree) -> the global rule binds the one User: CASCADES "
          "User -> Order, INFERRED", [(x[2], x[5]) for x in got] == [("backend/models.py", "INFERRED")], got)
    got = _rows(rows, "ROUTES_TO", "urls", "index")
    check("`from views import index` (two views.py in the tree) -> the global rule: ROUTES_TO -> index, INFERRED",
          [(x[4], x[5]) for x in got] == [("backend/views.py", "INFERRED")], got)
    e = _edges(FLAT["backend/star.py"], "backend/star.py")
    check("`from sqlalchemy.orm import *` is SQLAlchemy evidence for an unbound Mapped -> HAS_MANY Cart -> Line",
          _has(e, "HAS_MANY", "Cart", "Line"), e)


# =====================================================================================================
# H — registry
# =====================================================================================================
def gate_h_registry() -> None:
    print("\n-- H: the Python registry entry declares CASCADES; the retrieval lockstep holds --")
    check("Python entry edge_types include CASCADES", "CASCADES" in LR.PYTHON.edge_types, LR.PYTHON.edge_types)
    check("Python entry impact_edge_types include CASCADES (blast radius)", "CASCADES" in LR.PYTHON.impact_edge_types,
          LR.PYTHON.impact_edge_types)
    check("Python entry still emits no new edge type (edge_types ⊆ ALLOWED_EDGE_TYPES)",
          set(LR.PYTHON.edge_types) <= set(FE.ALLOWED_EDGE_TYPES), set(LR.PYTHON.edge_types) - set(FE.ALLOWED_EDGE_TYPES))
    import importlib.util
    spec = importlib.util.spec_from_file_location("dm_retrieval_dj_sqla2", BUILD / "retrieval.py")
    R = importlib.util.module_from_spec(spec)
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            spec.loader.exec_module(R)
        union = {"CALLS"} | {t for entry in LR.REGISTRY for t in entry.impact_edge_types}
        check("retrieval.DEFAULT_IMPACT_EDGE_TYPES == {CALLS} ∪ registry impact types (unchanged by this track)",
              set(R.DEFAULT_IMPACT_EDGE_TYPES) == union, (sorted(R.DEFAULT_IMPACT_EDGE_TYPES), sorted(union)))
    except Exception as exc:  # noqa: BLE001
        print(f"  [SKIP] retrieval.py not importable here ({type(exc).__name__}) — recognizer_registry_gates locks it")
    for path in ("polls/views.py", "polls/views/__init__.py", "views.py", "app/index.py", "a\\b\\c.py"):
        check(f"framework_edges' module scope of {path!r} == edge_resolve._module_path (lockstep)",
              getattr(FE, "_scope_module", lambda p: None)(path) == ER._module_path(path),
              (getattr(FE, "_scope_module", lambda p: None)(path), ER._module_path(path)))
    cmt = " ".join(Path(BUILD / "framework_edges.py").read_text(encoding="utf-8").split("\"CASCADES\",", 1)[-1]
                   .split("\"RENDERS\"", 1)[0].split())
    check("the ALLOWED_EDGE_TYPES CASCADES comment names the Python producers (SQLAlchemy cascade= / ondelete=, "
          "Django on_delete=)", "on_delete=" in cmt and "ondelete=" in cmt and "framework_edges" in cmt, cmt)


# =====================================================================================================
# I — the real graphify engine
# =====================================================================================================
def gate_i_real_engine() -> None:
    print("\n-- I: the REAL graphify engine on the same tree (extractor-independent resolution) --")
    try:
        import graphify  # noqa: F401
        import graphify_extract as GX
    except Exception:
        print("  [SKIP] graphify is not installed in this interpreter — the real-engine section is not run")
        return
    print(f"  [INFO] graphify {GX.graphify_version() or '(unknown)'}")
    root = _write(_fresh("engine"), {**SITE, **ORM_TREE})
    S = _sync()
    with _flags(DEEPMAP_HOME=str(_TMP / "home")), contextlib.redirect_stderr(io.StringIO()), \
            contextlib.redirect_stdout(io.StringIO()):
        res, rep = GX.extract_with_report(root)
        if not res or not res[0]:
            check("the real engine produced a graph", False, rep.get("cause") if isinstance(rep, dict) else rep)
            return
        merged, typed = S._apply_recognizers(root, res[0])
    r = ER.Resolver(merged)
    rows = [(et, r.name_of(a), r.file_of(a), r.name_of(b), r.file_of(b), c)
            for et, a, b, c, _at in ER.resolve_typed_edges(typed, r, [n["key"] for n in merged])]
    for what, et, src, dst, src_file, dst_file, grade in (
            ("a URLconf's own view (EXTRACTED)", "ROUTES_TO", "urls", "ping", "polls/urls.py", "polls/urls.py",
             "EXTRACTED"),
            ("an imported function view", "ROUTES_TO", "urls", "index", "polls/urls.py", "polls/views.py", "INFERRED"),
            ("a CBV class with no methods of its own", "ROUTES_TO", "urls", "ResultsView", "polls/urls.py",
             "polls/views.py", "INFERRED"),
            ("a re-exported view", "ROUTES_TO", "urls", "vote", "polls/urls.py", "polls/more.py", "INFERRED"),
            ("a typed relation to an imported model", "REFERENCES", "Order", "Customer", "shop/models.py",
             "shop/customer.py", "INFERRED"),
            ("a typed collection", "HAS_MANY", "Order", "LineItem", "shop/models.py", "shop/models.py", "INFERRED"),
            ("a cross-file ForeignKey ondelete cascade", "CASCADES", "Customer", "Order", "shop/customer.py",
             "shop/models.py", "INFERRED"),
            ("a Django on_delete cascade", "CASCADES", "Author", "Book", "library/books.py", "library/books.py",
             "INFERRED")):
        got = [x for x in rows if x[0] == et and x[1] == src and x[3] == dst and x[2] == src_file]
        check(f"graphify nodes: {what} -> {et} {src} -> {dst} ({dst_file}), {grade}",
              [(x[2], x[4], x[5]) for x in got] == [(src_file, dst_file, grade)], got)


# =====================================================================================================
# J — fuzz: mutated fixtures never raise and only ever emit well-formed edges
# =====================================================================================================
def gate_j_fuzz() -> None:
    print("\n-- J: 300 line-deletion / line-swap mutants of the fixtures (seeded): no raise, well-formed edges --")
    import random
    rnd = random.Random(20261022)
    sources = [("polls/urls.py", POLLS_URLS), ("shop/models.py", SQLA), ("library/books.py", DJ_MODELS),
               ("mysite/urls.py", SITE["mysite/urls.py"])] + [("app/urls.py", s) for s in NOT_ROUTES.values()]
    parsed = crashed = bad = 0
    detail: list = []
    for i in range(300):
        rel, src = sources[i % len(sources)]
        lines = src.splitlines()
        for _ in range(rnd.randint(1, 4)):
            if len(lines) < 2:
                break
            a, b = rnd.randrange(len(lines)), rnd.randrange(len(lines))
            if rnd.random() < 0.5:
                del lines[a]
            else:
                lines[a], lines[b] = lines[b], lines[a]
        text = "\n".join(lines) + "\n"
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        parsed += 1
        try:
            edges = FE.edges_from_module(tree, rel)
        except Exception as exc:  # noqa: BLE001
            crashed += 1
            detail.append((i, type(exc).__name__, str(exc)[:80]))
            continue
        for e in edges:
            if e.edge_type not in FE.ALLOWED_EDGE_TYPES or not isinstance(e.dst_name, str) or not e.dst_name \
                    or not isinstance(e.attrs, dict) or not isinstance(getattr(e, "scope", ""), str):
                bad += 1
                detail.append((i, e))
    check(f"every parseable mutant ({parsed} of 300) ran without raising", parsed > 50 and crashed == 0, detail[:3])
    check("every edge a mutant produced is well-formed (an ALLOWED type, a dst name, dict attrs, str scope)",
          bad == 0, detail[:3])
    root = _write(_fresh("fuzz_tree"), {**SITE, "polls/urls.py": POLLS_URLS.replace("include(api_patterns)",
                                                                                   "include(missing.urls)")})
    with _flags(), contextlib.redirect_stderr(io.StringIO()) as err:
        _s, edges = LR.run(root)
    check("an include() of an unbound name keeps the tree's other routes and logs no post-pass skip",
          any(e.dst_name == "index" for e in edges) and "skipped" not in err.getvalue(), err.getvalue()[:300])


# =====================================================================================================
# K — the review round: precision of Django lazy references, imports the post-pass cannot follow, degradation
# =====================================================================================================
K_TREES = {
    "authlabel": {   # django.contrib models named by 'app_label.Model' beside same-named project classes
        "accounts/__init__.py": "",
        "accounts/models.py": ("from django.db import models\nclass Group(models.Model):\n    pass\n"
                               "class Site(models.Model):\n    pass\n"),
        "core/__init__.py": "",
        "core/graphql/__init__.py": "",
        "core/graphql/types.py": "class ContentType:\n    pass\nclass Permission:\n    pass\n",
        "users/__init__.py": "",
        "users/models.py": "from django.db import models\nclass User(models.Model):\n    pass\n",
        "extras/__init__.py": "",
        "extras/tests/test_dashboard.py": "class User:\n    pass\n",
        "library/__init__.py": "",
        "library/books.py": ("from django.db import models\nclass Book(models.Model):\n"
                             "    group = models.ForeignKey('auth.Group', on_delete=models.CASCADE)\n"
                             "    site = models.ForeignKey('sites.Site', on_delete=models.CASCADE)\n"
                             "    kind = models.ForeignKey('contenttypes.ContentType', on_delete=models.CASCADE)\n"
                             "    perms = models.ManyToManyField('auth.Permission')\n"
                             "    owner = models.ForeignKey('users.User', on_delete=models.CASCADE)\n"),
    },
    "fkmodattr": {   # ForeignKey(models.User.id): the class through a module import, never the module
        "models.py": "class User(Base):\n    __tablename__ = 'users'\n",
        "app/__init__.py": "",
        "app/posts.py": ("import sqlalchemy as sa\nimport models\nclass Post(Base):\n"
                         "    uid = sa.Column(sa.Integer, sa.ForeignKey(models.User.id, ondelete='CASCADE'))\n"),
    },
    "reexport_mapped": {   # Mapped re-exported by a sibling project package with relationship()
        "core/__init__.py": "",
        "core/db.py": ("from sqlalchemy.orm import DeclarativeBase, Mapped, relationship\n"
                       "class Base(DeclarativeBase):\n    pass\n"),
        "models/__init__.py": "",
        "models/item.py": "from core.db import Base\nclass Item(Base):\n    __tablename__ = 'items'\n",
        "models/order.py": ("from core.db import Base, Mapped, relationship\nfrom models.item import Item\n"
                            "class Order(Base):\n    items: Mapped[list['Item']] = relationship(back_populates='order')\n"),
    },
    "twobind": {     # a re-export bound twice (try / except ImportError): the module is found, the def is not
        "shop/__init__.py": "",
        "shop/models/__init__.py": "try:\n    from .order import Order\nexcept ImportError:\n    Order = None\n",
        "shop/models/order.py": "class Order(Base):\n    __tablename__ = 'orders'\n",
        "shop/cart.py": ("from sqlalchemy.orm import relationship\nfrom shop.models import Order\n"
                         "class Cart(Base):\n    orders = relationship('Order')\n"),
    },
    "lazy": {        # a PEP 562 __getattr__ package
        "shop/__init__.py": "",
        "shop/models/__init__.py": ("import importlib\ndef __getattr__(name):\n"
                                    "    return getattr(importlib.import_module('.order', __name__), name)\n"),
        "shop/models/order.py": "class Order(Base):\n    __tablename__ = 'orders'\n",
        "shop/cart.py": ("from sqlalchemy.orm import relationship\nfrom shop.models import Order\n"
                         "class Cart(Base):\n    orders = relationship(Order)\n"),
    },
    "rootcollide": {  # a flat import that exact-matches a DIFFERENT in-tree module (the root models.py)
        "models.py": "class Settings:\n    pass\n",
        "svc/app.py": "",
        "svc/models.py": "class Order(Base):\n    __tablename__ = 'orders'\n",
        "svc/cart.py": ("from sqlalchemy.orm import relationship\nfrom models import Order\n"
                        "class Cart(Base):\n    orders = relationship('Order')\n"),
    },
    "djview_twobind": {
        "polls/__init__.py": "",
        "polls/views/__init__.py": "try:\n    from .fast import index\nexcept ImportError:\n    from .slow import index\n",
        "polls/views/fast.py": "def index(request): pass\n",
        "polls/urls.py": "from django.urls import path\nfrom polls.views import index\nurlpatterns = [path('', index)]\n",
    },
    "rootindex": {   # a root-level index.py: edge_resolve's module path for it is '' (the JS index rule)
        "index.py": "class Order(Base):\n    __tablename__ = 'orders'\ndef home(request): pass\n",
        "cart.py": ("from sqlalchemy.orm import relationship\nfrom index import Order\n"
                    "class Cart(Base):\n    orders = relationship(Order)\n"),
        "urls.py": "from django.urls import path\nfrom index import home\nurlpatterns = [path('', home)]\n",
    },
    "mypkg": {       # the sync root IS the package: its imports name it absolutely
        "__init__.py": "",
        "models.py": ("from django.db import models\nclass User(models.Model):\n    pass\n"
                      "class Order(models.Model):\n    pass\n"),
        "posts.py": ("from django.db import models\nfrom mypkg.models import User\n"
                     "from sqlalchemy.orm import Mapped, relationship\nfrom mypkg.models import Order\n"
                     "class Post(models.Model):\n    author = models.ForeignKey(User, on_delete=models.CASCADE)\n"
                     "class Line(Base):\n    order: Mapped[Order] = relationship()\n"),
        "urls.py": "from django.urls import path\nfrom mypkg.views import index\nurlpatterns = [path('', index)]\n",
        "views.py": "def index(request):\n    return None\n",
    },
    "postpass": {    # imported models and a view, for the post-pass failure checks
        "shop/__init__.py": "",
        "shop/models.py": "class Order(Base):\n    __tablename__ = 'orders'\n",
        "shop/cart.py": ("from sqlalchemy.orm import relationship\nfrom shop.models import Order\n"
                         "class Cart(Base):\n    orders = relationship(Order)\n"),
        "lib/__init__.py": "",
        "lib/models.py": "from django.db import models\nclass Author(models.Model):\n    pass\n",
        "lib/books.py": ("from django.db import models\nfrom lib.models import Author\n"
                         "class Book(models.Model):\n    author = models.ForeignKey(Author, on_delete=models.CASCADE)\n"),
        "polls/__init__.py": "",
        "polls/views.py": "def index(request): pass\n",
        "polls/urls.py": "from django.urls import path\nfrom polls.views import index\nurlpatterns = [path('', index)]\n",
    },
}

_CYCLE_PROBE = r'''
import ast, sys, time
sys.path.insert(0, sys.argv[1])
import framework_edges as FE
n = int(sys.argv[2])
names = [f"l{i}" for i in range(n)]
dj = ["from django.urls import path, include", "def v(r): pass"] + [f"{a} = []" for a in names]
for i, a in enumerate(names):
    dj.append(f"{a} += [path('p{i}/', v)" + "".join(f", path('{b}/', include({b}))" for b in names if b != a) + "]")
dj.append("urlpatterns = [path('', include(l0))]")
fa = ["from fastapi import APIRouter"] + [f"r{i} = APIRouter(prefix='/r{i}')" for i in range(n)]
fa += [f"@r{i}.get('/x')\ndef h{i}(): pass" for i in range(n)]
fa += [f"r{i}.include_router(r{j}, prefix='/m{j}')" for i in range(n) for j in range(n) if i != j]
out = []
for src in ("\n".join(dj), "\n".join(fa)):
    t = time.time()
    edges = FE.edges_from_module(ast.parse(src), "app/urls.py")
    out.append((round(time.time() - t, 2), sum(e.edge_type == "ROUTES_TO" for e in edges)))
print(out)
'''


def _k_rows(name: str) -> list:
    root = _write(_fresh(name if name == "mypkg" else "k_" + name), K_TREES[name])
    with _flags(DEEPMAP_EXTRACTOR="ast"):
        rows, _r, _raw = _resolved(root)
    return rows


def gate_k_review_round() -> None:
    print("\n-- K: Django 'app_label.Model' precision, imports the post-pass cannot follow, degradation, bounds --")
    e = _edges("from django.db import models\nfrom sqlalchemy.orm import relationship\n"
               "class Author(models.Model):\n    pass\n"
               "class Book(models.Model):\n    author = models.ForeignKey('library.Author', on_delete=models.CASCADE)\n"
               "    group = models.ForeignKey('auth.Group', on_delete=models.CASCADE)\n"
               "    perms = models.ManyToManyField('auth.Permission')\n"
               "class Cart(Base):\n    orders = relationship('app.Order')\n", "library/models.py")
    check("Django 'auth.Group' -> Group in the 'auth' app's models module ('spec:auth.models'), never a bare name",
          _has(e, "REFERENCES", "Book", "Group", scope="spec:auth.models")
          and _has(e, "CASCADES", "Group", "Book", scope="spec:auth.models")
          and _has(e, "HAS_MANY", "Book", "Permission", scope="spec:auth.models"), e)
    check("'library.Author' from library/models.py, which defines Author -> same-file (no scope)",
          _has(e, "REFERENCES", "Book", "Author", scope="") and _has(e, "CASCADES", "Author", "Book", scope=""), e)
    check("must NOT: a SQLAlchemy relationship('app.Order') (a module path, not an app label) keeps the name rule",
          _has(e, "HAS_MANY", "Cart", "Order", scope=""), _of(e, "HAS_MANY", "Cart"))
    e = _edges("from django.db import models\nclass Group(models.Model):\n    pass\n"
               "class Member(models.Model):\n    group = models.ForeignKey('auth.Group', on_delete=models.CASCADE)\n",
               "accounts/models.py")
    check("must NOT: 'auth.Group' from accounts/models.py, which defines its own Group -> still the auth app's "
          "('spec:auth.models'), never the local class",
          _has(e, "REFERENCES", "Member", "Group", scope="spec:auth.models") and not _has(e, "REFERENCES", "Member",
                                                                                        "Group", scope=""), e)
    rows = _k_rows("authlabel")
    check("'auth.Group' / 'sites.Site' / 'contenttypes.ContentType' / 'auth.Permission' (django.contrib) beside "
          "same-named project classes -> NO row, no false CASCADES",
          not [x for x in rows if x[3] in ("Group", "Site", "ContentType", "Permission")
               or x[1] in ("Group", "Site", "ContentType")], rows)
    check("'users.User' (a second User in a test file) -> users/models.py's User ONLY, INFERRED",
          [(x[4], x[5]) for x in _rows(rows, "REFERENCES", "Book", "User")] == [("users/models.py", "INFERRED")],
          _rows(rows, "REFERENCES", "Book"))
    check("... and its CASCADES User -> Book resolves (it was lost to the AMBIGUOUS fan-out)",
          [(x[2], x[5]) for x in _rows(rows, "CASCADES", "User", "Book")] == [("users/models.py", "INFERRED")],
          _rows(rows, "CASCADES"))

    e = _edges("import sqlalchemy as sa\nimport models\nimport app.models\n"
               "class Post(Base):\n    uid = sa.Column(sa.ForeignKey(models.User.id, ondelete='CASCADE'))\n"
               "    gid = sa.Column(sa.ForeignKey(app.models.Group.__table__.c.id))\n", "app/posts.py")
    check("ForeignKey(models.User.id) -> User through `import models` ('spec:models'), never the module 'models'",
          _has(e, "REFERENCES", "Post", "User", scope="spec:models")
          and _has(e, "CASCADES", "User", "Post", scope="spec:models") and not _of(e, "REFERENCES", dst="models"), e)
    check("ForeignKey(app.models.Group.__table__.c.id) -> Group ('spec:app.models'), never 'app'",
          _has(e, "REFERENCES", "Post", "Group", scope="spec:app.models") and not _of(e, "REFERENCES", dst="app"), e)
    rows = _k_rows("fkmodattr")
    check("... resolved: REFERENCES Post -> models.py's User + CASCADES User -> Post, both INFERRED; no :Module dst",
          [(x[4], x[5]) for x in _rows(rows, "REFERENCES", "Post")] == [("models.py", "INFERRED")]
          and _rows(rows, "REFERENCES", "Post", "User") and _rows(rows, "CASCADES", "User", "Post"), rows)

    for what, src, want in (
            ("Mapped + relationship re-exported together by a sibling project package (core.db)",
             "from core.db import Base, Mapped, relationship\n", True),
            ("a project Mapped with SQLAlchemy's own relationship()",
             "from core.typing import Mapped\nfrom sqlalchemy.orm import relationship\n", True),
            ("must NOT: a Mapped and a relationship() from two other libraries",
             "from mylib import Mapped\nfrom otherlib import relationship\n", False),
            ("must NOT: a project Mapped with neomodel's relationship()",
             "from core.db import Mapped\nfrom neomodel import relationship\n", False)):
        e = _edges(src + "class Order(Base):\n    items: Mapped[list['Item']] = relationship(back_populates='order')\n",
                   "models/order.py")
        check(f"{what} -> {'HAS_MANY Order -> Item' if want else 'no ORM edge'}",
              _has(e, "HAS_MANY", "Order", "Item") if want else not _of(e, "HAS_MANY"), e)
    rows = _k_rows("reexport_mapped")
    check("... resolved: HAS_MANY Order -> models/item.py's Item, INFERRED",
          [(x[4], x[5]) for x in _rows(rows, "HAS_MANY", "Order", "Item")] == [("models/item.py", "INFERRED")], rows)

    for name, et, src, dst, dst_file in (
            ("twobind", "HAS_MANY", "Cart", "Order", "shop/models/order.py"),
            ("lazy", "HAS_MANY", "Cart", "Order", "shop/models/order.py"),
            ("rootcollide", "HAS_MANY", "Cart", "Order", "svc/models.py"),
            ("djview_twobind", "ROUTES_TO", "urls", "index", "polls/views/fast.py")):
        rows = _k_rows(name)
        check(f"{name}: an in-tree module whose definition the post-pass cannot follow -> 'pref:' (that module, "
              f"else the name rule): {et} {src} -> {dst} ({dst_file}) still resolves, INFERRED",
              [(x[4], x[5]) for x in _rows(rows, et, src, dst)] == [(dst_file, "INFERRED")], rows)
    rows = _k_rows("rootindex")
    check("a model and a view imported from a root-level index.py still bind (INFERRED): its module scope is ''",
          [(x[4], x[5]) for x in _rows(rows, "HAS_MANY", "Cart", "Order")] == [("index.py", "INFERRED")]
          and [(x[4], x[5]) for x in _rows(rows, "ROUTES_TO", "urls", "home")] == [("index.py", "INFERRED")], rows)
    rows = _k_rows("mypkg")
    check("the sync root is the package (`from mypkg.models import User` in mypkg/): REFERENCES, CASCADES, the typed "
          "REFERENCES and the route all resolve, INFERRED",
          [(x[4], x[5]) for x in _rows(rows, "REFERENCES", "Post", "User")] == [("models.py", "INFERRED")]
          and [(x[2], x[5]) for x in _rows(rows, "CASCADES", "User", "Post")] == [("models.py", "INFERRED")]
          and [(x[4], x[5]) for x in _rows(rows, "REFERENCES", "Line", "Order")] == [("models.py", "INFERRED")]
          and [(x[4], x[5]) for x in _rows(rows, "ROUTES_TO", "urls", "index")] == [("views.py", "INFERRED")], rows)

    want = {("HAS_MANY", "Cart", "Order"), ("REFERENCES", "Book", "Author"), ("CASCADES", "Author", "Book"),
            ("ROUTES_TO", "urls", "index")}
    for what, attr, needle in (("its prologue (the tree walk) raises", "_iter_py", "Python post-pass"),
                               ("its ORM part raises", "_compose_orm", "ORM model placement skipped"),
                               ("its Django part raises", "_compose_django", "Django URL composition skipped")):
        saved = getattr(FE, attr)

        def boom(*_a, **_k):
            raise OSError("simulated")
        setattr(FE, attr, boom)
        err = io.StringIO()
        try:
            root = _write(_fresh("k_postpass_" + attr), K_TREES["postpass"])
            S = _sync()
            with _flags(DEEPMAP_EXTRACTOR="ast"), contextlib.redirect_stderr(err):
                nodes, _c, _i = S._rebuild_code_ast(root)
                merged, typed = S._apply_recognizers(root, nodes)
            r = ER.Resolver(merged)
            got = {(et, r.name_of(a), r.name_of(b)): c
                   for et, a, b, c, _at in ER.resolve_typed_edges(typed, r, [n["key"] for n in merged])}
        finally:
            setattr(FE, attr, saved)
        check(f"when the Python post-pass {what}: imported models and views degrade to the name rule (INFERRED), "
              f"never to an edge that binds nothing; stderr says '{needle}'",
              all(got.get(k) == "INFERRED" for k in want) and needle in err.getvalue(),
              (sorted(got.items()), err.getvalue()[-300:]))

    dj = _edges("from django.urls import path\nfrom . import views\nurlpatterns = [path('a/', views.a)]\n",
                "app/urls.py")
    orm = _edges("from shop.models import Order\nclass Cart(Base):\n    orders = relationship(Order)\n", "shop/cart.py")
    blind = FE._UrlWorld(lambda r: None, {})
    got_dj = FE._compose_django(dj, blind, [])
    got_orm = FE._compose_orm(orm, FE._UrlWorld(lambda r: None, {}), [])
    check("a module whose Django facts the post-pass cannot read (or that do not line up) -> its imported views "
          "degrade to the name rule (scope ''), never stay 'spec:'",
          _scope(_route(dj, "a")) == "spec:.views" and _scope(_route(got_dj, "a")) == "", (dj, got_dj))
    check("an imported model whose declaring module the post-pass cannot read -> the name rule (scope '')",
          _has(orm, "HAS_MANY", "Cart", "Order", scope="spec:shop.models")
          and _has(got_orm, "HAS_MANY", "Cart", "Order", scope=""), (orm, got_orm))

    e = _edges("from django.urls import include, path\nfrom django.conf import settings\nfrom . import views\n"
               "inner = [path('x/', views.x), path(settings.Y, views.y)]\n"
               "urlpatterns = [path(settings.API, include(inner))]\n", "app/urls.py")
    rx, ry = _route(e, "x"), _route(e, "y")
    check("a literal route under a non-literal include() prefix keeps its known part ('/x/') + prefix_unresolved",
          rx is not None and rx.attrs.get("path") == "/x/" and rx.attrs.get("prefix_unresolved") == "settings.API", rx)
    check("a non-literal route under it has NO path, both expressions in prefix_unresolved",
          ry is not None and "path" not in ry.attrs
          and ry.attrs.get("prefix_unresolved") == "settings.API, settings.Y", ry)

    small_fa = ("from fastapi import APIRouter, FastAPI\n"
                "def test_live():\n    parent_router = APIRouter()\n    child_router = APIRouter()\n"
                "    app = FastAPI()\n    app.include_router(parent_router, prefix='/api')\n"
                "    parent_router.include_router(child_router, prefix='/child')\n"
                "    @child_router.get('/items')\n    def read_items(): pass\n"
                "def test_cycle():\n    parent_router = APIRouter()\n    child_router = APIRouter()\n"
                "    parent_router.include_router(child_router, prefix='/child')\n"
                "    child_router.include_router(parent_router, prefix='/parent')\n"
                "    @parent_router.get('/p')\n    def read_p(): pass\n")
    got = {x.dst_name: x.attrs for x in _edges(small_fa, "tests/test_routers.py") if x.edge_type == "ROUTES_TO"}
    check("must NOT change: a small cycle the name-level mount graph implies (one router variable in two test "
          "functions, fastapi's own tests) keeps its exact paths ('/api/p' AND '/parent/p'; '/api/child/items')",
          got.get("read_p", {}).get("paths") == ["/api/p", "/parent/p"]
          and got.get("read_items", {}).get("path") == "/api/child/items", got)
    small_dj = ("from django.urls import include, path\ndef v(r): pass\ndef w(r): pass\n"
                "a = [path('va/', v)]\nb = [path('wb/', w)]\na += [path('b/', include(b))]\nb += [path('a/', include(a))]\n"
                "urlpatterns = [path('ra/', include(a)), path('rb/', include(b))]\n")
    got = {x.dst_name: x.attrs.get("paths") for x in _edges(small_dj, "app/urls.py") if x.edge_type == "ROUTES_TO"}
    check("must NOT change: two mutually include()d lists keep every exact path (v: '/ra/va/', '/rb/a/va/'; "
          "w: '/ra/b/wb/', '/rb/wb/')",
          got == {"v": ["/ra/va/", "/rb/a/va/"], "w": ["/ra/b/wb/", "/rb/wb/"]}, got)

    import subprocess
    import time
    t0 = time.time()
    try:
        p = subprocess.run([sys.executable, "-I", "-c", _CYCLE_PROBE, str(BUILD), "12"], capture_output=True,
                           text=True, timeout=120)
        got = p.stdout.strip() or p.stderr.strip()[-300:]
    except subprocess.TimeoutExpired:
        got = "timed out after 120 s"
    secs = round(time.time() - t0, 1)
    ok = got.startswith("[") and all(s < 5 and n == 12 for s, n in ast.literal_eval(got))
    check(f"12 mutually include()ing Django lists / 12 mutually mounted FastAPI routers: each module in < 5 s, every "
          f"route kept (whole probe {secs} s; the pre-fix walk enumerated every simple path: hours)", ok, got)


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 72)
    print(f"DeepMap — Django URLconfs, SQLAlchemy 2.0 typed models, Python ORM cascades (build: {BUILD})")
    print("=" * 72)
    try:
        for gate in (gate_a_urlconf, gate_b_not_routes, gate_c_cross_module, gate_d_sqla_typed, gate_e_not_sqla,
                     gate_f_django_orm, gate_g_resolution, gate_g2_unplaceable_import, gate_h_registry,
                     gate_i_real_engine, gate_j_fuzz, gate_k_review_round):
            _safe(gate)
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
    print("\n" + "=" * 72)
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{_PASS + _FAIL} checks passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
