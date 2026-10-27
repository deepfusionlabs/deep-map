#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Typed-receiver call linking — the PYTHON reader gates (receiver-calls design 3.1, 5, 9 S3b; slice S3b).
No Neo4j, no network, no spend; the real-engine section runs the installed graphify under a temporary
DEEPMAP_HOME, so nothing is written to ~/.deepmap.

build/receiver_facts_py.py lowers a Python file's `ast` to typed_calls' frozen IR; build/py_import_resolver.py
resolves imports by IMPORT ROOT (never by suffix). These gates drive both through the REAL pipeline: the captured
graph.json of graphify 0.8.39 AND 0.8.51 for every fixture tree -> graphify_extract.map_graph -> sync's
test / vendor exclusion, enrich sidecar, recognizers, `_unkey_receiver_calls` -> typed_calls.link (exactly the
call slice S2 wires into sync, and benchmark/call_linking/offline_calls.py mirrors) — and check:

  A  the import-root resolver: the roots (repo root, src/, the parent of each top-level package, the importer's
     own directory ONLY outside a package), absolute / relative / namespace resolution, two different regular
     matches = ambiguous, a module on disk but not in the graph = unknown (never external); the binder's
     resolutions, and the OPEN externals: a class a project class extends and a module / class project code
     assigns attributes of are never closed external types.
  B  the reader's IR on snippets: every binding form (R2) and every poisoning form, params and SELF (methods,
     classmethods, staticmethods, closures, rebinding), annotations -> TypeRef (typing forms by their import),
     fields (R3; a class-body VALUE is never a source), classes (neutral bases, dynamic, setattr rules, overloads,
     properties, async returns), sites / receivers / argc / the census (aliased imports included) / decls /
     patches; the canary; validate_facts on every file.
  C  every design 3.1 shape gives the exact key and grade end to end, on BOTH engines' captures.
  D  every design 5 Python must-not shape: no typed edge AND no removal, each beside a positive sibling — plus
     the shapes this slice found on real code: a declared external class a project class overrides (requests'
     CookieJar), a patched external module (werkzeug's sys.stdout), a class-body descriptor, a bare call through
     an import alias of another same-named function, a getter / setter pair the engine merges into one node — and
     the review's (the "review" tree): that external base re-exported through a project module (named and
     `compat.X`), a class-level PEP 695 parameter named like a class, a name the class BODY binds (a nested class)
     in a method signature / class-body annotation / nested base, an attribute assigned through the same variable.
  O  dynamic dispatch: a project OVERRIDE of the static target stays reachable after link (`r: Base` beside
     Sub.m; a field a subclass rebinds; click's injected-field / annotated-field / `t.cast` shapes), re-emitted at
     AMBIGUOUS (never upgraded), while a same-named method of an unrelated class, an ancestor the target shadows
     and a constructed receiver's subclass are still removed (the core's merge; DISPATCH_PENDING_S3A is retired:
     these are must-keeps / must-removes).
  W  the phase-2 review (the "review2" tree): graphify's symbol pass gives an undecorated top-level def the bare
     call of an imported class inside its nested def / nested def's default / local class's method / lambda in a
     nested def, so the core keeps those tuples (no removal, nothing keyed from there) while a decorated def, a
     method and the def's own call behave as before; a `from m import *` after `from x import Order` leaves Order
     untyped (the positive sibling with the named import last is typed); a member reached through getattr /
     methodcaller / attrgetter keeps the engine's tuple of it (plain `copy.copy()` still loses its FALSE one); a
     typing.Protocol's own member is never a typed target (it never runs: the engine's tuples stay).
  P  (in C and D) the phase-2 verification (the "proxy" tree, flask @36e4a82): a class under `if TYPE_CHECKING:` is
     typing-only — `app_ctx: AppContextProxy = LocalProxy(...)` never keys `app_ctx._get_current_object()` to the
     ProxyMixin Protocol stub, nor a typing-only class's own member (`session.get_value()`, `h.run()`: that tuple
     stays), while `app_ctx.push()` still keys the REAL AppContext.push (INFERRED) and a class under
     `if not TYPE_CHECKING:` is an ordinary class.
  E  the requests `s = requests.Session(); s.send()` in a test caller beside an in-file RedirectSession.send ends
     on Session.send (default and DEEPMAP_PRECISE_CALLS=1); the flask cross-file self-MRO
     (`self.ensure_sync` -> App.ensure_sync) and the injected `self.app.ensure_sync`; flask's
     `import json; json.dumps()` inside src/flask/json/ never reaches flask.json.dumps; `LR.run()` converges on
     the module function on both engines; the import-root rules end to end.
  F  engine parity: the typed tuples are identical on version-stable keys (label:name@file:line) across the
     0.8.39 and 0.8.51 captures; the ast path (DEEPMAP_EXTRACTOR=ast) gives the same typed set as graphify.
  G  flags off: the SAME list object and no node attribute; with TYPED on for another language only, the Python
     callers are untouched; the push passes after link keep every typed key a CallKey; `_resolve_calls` with keys
     honoured binds each typed key to exactly its node.
  H  the audit (DEEPMAP_CALLS_AUDIT) on real Python receivers: an untyped `nt.notify()` among two repo-wide
     notify methods is demoted; a SELF site and a class-object site (`Map.bind(self, x)`) are not.
  R  the installed engine's real extraction of every tree equals its captured fixture.
  M  mutation teeth: each reader / resolver rule broken in a copy of the module turns its checks RED.

Every variant pins its flags (`0` where off, never "unset = off"), so a later default flip cannot move these
expectations. Python 3.10 / 3.11 / 3.13 all run every section (a PEP 695 file is only parsed on 3.12+; older
interpreters must fail that one file cleanly and type nothing in it; R needs graphify installed).

Fixtures: tests/fixtures/typed_calls/<0839|0851>/py/<tree>.json — the REAL graph.json graphify 0.8.39 / 0.8.51
wrote for TREES[<tree>] (`--capture` re-writes the installed engine's bucket; run it under both engines after
editing a tree).

Usage: python tests/typed_calls_py_gates.py [--capture[=<tree>,...] | --dump] [--root <tree>]
       (--root: load build/ from another checkout — the RED-first run)
"""
from __future__ import annotations

import ast
import contextlib
import copy
import hashlib
import importlib.util
import json
import os
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
    print(f"usage: python tests/typed_calls_py_gates.py [--capture[=<tree>,...] | --dump] [--root <tree>]  "
          f"({_USAGE})",
          file=sys.stderr)
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
RF = _try_import("receiver_facts_py")
PIR = _try_import("py_import_resolver")
_IMPORT_ERROR = "; ".join(_IMPORT_ERRORS)

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


def rf():
    """The reader module in use (sys.modules: a mutation run swaps it)."""
    return sys.modules.get("receiver_facts_py", RF)


def pir():
    return sys.modules.get("py_import_resolver", PIR)


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
# The fixture trees (each captured on graphify 0.8.39 and 0.8.51)
# ================================================================================================

SHAPES_REPO = '''\
class Repo:
    def find(self, key):
        return key

    def save(self):
        return 1


def make_repo() -> Repo:
    return Repo()
'''

SHAPES_MODELS = '''\
from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Optional, Union, cast, overload

from app.repo import Repo, make_repo
from app import repo as repo_mod
import app.repo
import app.repo as R


class Ledger:
    def post(self):
        return 2


class Order:
    store: Repo

    def __init__(self, repo: Repo):
        self.injected = repo
        self.owned = Ledger()

    def total(self) -> int:
        return 0

    def clone(self) -> Order:
        return self

    @property
    def ledger(self) -> Ledger:
        return self.owned

    @classmethod
    def create(cls) -> Order:
        return cls.blank()

    @classmethod
    def blank(cls):
        return None

    def r3_ctor_field(self):
        return self.owned.post()

    def r3_injected_field(self):
        return self.injected.save()

    def r3_class_annotation(self):
        return self.store.find(1)

    def r3_property(self):
        return self.ledger.post()

    def s_self(self):
        return self.total()

    def s_closure(self):
        def inner():
            return self.total()
        return inner


class Special(Order):
    def total(self) -> int:
        return super().total()

    def s_inherited(self):
        return self.clone()


class Special2(Order):
    def total(self) -> int:
        return super(Special2, self).total()


@dataclass
class Cart:
    ledger: Ledger

    def r3_dataclass_field(self):
        return self.ledger.post()


class Outer:
    class Inner:
        def ping(self):
            return 3


class Conv:
    @overload
    def to(self, x: int) -> int: ...

    @overload
    def to(self, x: str) -> str: ...

    def to(self, x):
        return x


def r1_param(o: Order):
    return o.total()


def r1_string_annotation(o: "Ledger"):
    return o.post()


def r1_optional(o: Optional[Ledger]):
    return o.post()


def r1_pipe_none(o: Ledger | None):
    return o.post()


def r1_annotated(o: Annotated[Ledger, "meta"]):
    return o.post()


def r1_imported(r: Repo):
    return r.save()


def r1_classof(k: type[Order]):
    return k.create()


def r2_local_ctor():
    led = Ledger()
    return led.post()


def r2_annotated_local(x):
    led: Ledger = x
    return led.post()


def r2_alias():
    led = Ledger()
    other = led
    return other.post()


def r2_none_then_ctor(flag):
    led = None
    if flag:
        led = Ledger()
    return led.post()


def r2_cast(x):
    led = cast(Ledger, x)
    return led.post()


def r2_return_call():
    r = make_repo()
    return r.save()


def r4_chain():
    return make_repo().save()


def r4_method_chain(o: Order):
    return o.clone().total()


def k_class_method():
    return Order.create()


def k_unbound_call(o):
    return Ledger.post(o)


def m_alias_module():
    return R.make_repo()


def m_from_package_module():
    return repo_mod.make_repo()


def m_dotted_package():
    return app.repo.make_repo()


def c_same_file():
    return Ledger()


def c_imported():
    return Repo()


def c_module_ctor():
    return R.Repo()


def c_nested_class():
    return Outer.Inner()


def r1_overloaded(c: Conv):
    return c.to(1)
'''

MUSTNOT_CORE = '''\
import os
import re
import threading
from typing import Any, List, TypeVar, Union

T = TypeVar("T")


class Order:
    def save(self):
        return 1

    def acquire(self):
        return 2

    def cb(self):
        return 3

    def create(self):
        return 4

    def append(self, x):
        return x

    def main(self):
        return 5

    def search(self, q):
        return q

    def join(self, other):
        return other

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None


class Other:
    def save(self):
        return 6


def make():
    return Order()


def _load(name):
    return __import__(name)


CG = _load("plugins")


def typed_sibling():
    x = Order()
    return x.save()


def mn_rebind():
    x = Order()
    x = make()
    return x.save()


def mn_del():
    x = Order()
    del x
    return x.save()


def mn_for(orders):
    o = Order()
    for o in orders:
        o.save()


def mn_with():
    lk = Order()
    with threading.Lock() as lk:
        lk.acquire()


def mn_except():
    e = Order()
    try:
        return 0
    except Exception as e:
        return e.save()


def mn_walrus(items):
    x = Order()
    if (x := items[0]):
        return x.save()


def mn_global():
    global shared
    shared = Order()
    return shared.save()


def mn_nonlocal():
    x = Order()

    def reset():
        nonlocal x
        x = make()
    reset()
    return x.save()


def mn_tuple(pair):
    h = Order()
    h, g = pair
    return h.save()


def mn_augassign():
    x = Order()
    x += 1
    return x.save()


def mn_local_import():
    x = Order()
    from app.alt import Order as x
    return x.save()


def mn_nested_def_name():
    x = Order()

    def x():
        return 0
    return x.save()


def mn_match_capture(v):
    x = Order()
    match v:
        case [x]:
            pass
    return x.save()


def mn_comprehension_shadow(items):
    x = Order()
    return [x.save() for x in items]


def cand_if_else(flag):
    if flag:
        x = Order()
    else:
        x = Other()
    return x.save()


def mn_any(x: Any):
    return x.save()


def mn_union_str(x: Union[Order, str]):
    return x.save()


def mn_pipe_str(x: "Order | str"):
    return x.save()


def mn_union_any(x: Union[Order, Any]):
    return x.save()


def mn_list(orders: List[Order]):
    return orders.append(1)


def mn_typevar(x: T):
    return x.save()


def mn_unparseable(x: "Order[["):
    return x.save()


def mn_unannotated(request):
    return request.save()


def ext_calls(p):
    re.search("a", p)
    return os.path.join(p, "x")


def mn_path_loaded():
    return CG.main()


def mn_class_alias():
    x = Order
    return x.create()


def mn_dunder(c: Order):
    return c.__enter__()


def c_python_order():
    return Order()


class Holder:
    def __init__(self, repo, cb):
        self.cb = cb
        self.repo = repo

    def mn_field_callable(self):
        return self.cb()

    def mn_untyped_injection(self):
        return self.repo.save()


class TwoClasses:
    def __init__(self):
        self.repo = Order()

    def reset(self):
        self.repo = Other()

    def mn_field_two_classes(self):
        return self.repo.save()


class WithProp:
    @property
    def save(self) -> Order:
        return Order()


def mn_property_called(w: WithProp):
    return w.save()


class Dyn:
    def __getattr__(self, name):
        return name

    def save(self):
        return 7


def mn_getattr_class(d: Dyn):
    return d.save()


class Meta(type):
    pass


class WithMeta(metaclass=Meta):
    def save(self):
        return 8


def mn_metaclass(w: WithMeta):
    return w.save()


def unknown_deco(c):
    return c


@unknown_deco
class Decorated:
    def save(self):
        return 9


def mn_decorated_class(d: Decorated):
    return d.save()


class Patched:
    def save(self):
        return 10

    def keep(self):
        return 11


Patched.save = lambda self: 0


def mn_patched(p: Patched):
    return p.save()


def patched_other_attr(p: Patched):
    return p.keep()


class ExtBase(dict):
    def own(self):
        return 12

    def mn_super_external(self):
        return super().save()


class Restored:
    def __setstate__(self, state):
        for k, v in state.items():
            setattr(self, k, v)

    def save(self):
        return 13


def setstate_still_typed(r: Restored):
    return r.save()


class Generated:
    def save(self):
        return 14

    @classmethod
    def install(cls, name, fn):
        setattr(cls, name, fn)


def mn_setattr_on_class(g: Generated):
    return g.save()


class GetSet:
    def __init__(self, other):
        self.other = other

    @property
    def value(self) -> int:
        return self.compute()

    @value.setter
    def value(self, v):
        self.other.compute()

    def compute(self):
        return 16


class Rebinder:
    def save(self):
        return 17

    def mn_self_rebound(self, other):
        self = other
        return self.save()


async def fetch() -> Order:
    return Order()


def mn_async_chain():
    return fetch().save()


def wrapper(f):
    return f


@wrapper
def wrapped() -> Order:
    return Order()


def mn_decorated_return():
    return wrapped().save()


def cand_union(x: Union[Order, Other]):
    return x.save()


class Descriptor:
    def __get__(self, obj, owner):
        return Other()

    def save(self):
        return 18


class UsesDescriptor:
    value = Descriptor()
    repo = None

    def __init__(self):
        self.repo = Order()

    def mn_descriptor_field(self):
        return self.value.save()

    def r3_class_none_then_self(self):
        return self.repo.create()
'''

MUSTNOT_MAYBE = '''\
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.core import Order
else:
    Order = object


def mn_type_checking_else(x: Order):
    return x.save()
'''

MUSTNOT_TRY = '''\
try:
    from app.core import Order
except ImportError:
    from app.alt import Order


def mn_try_two_classes(x: Order):
    return x.save()
'''

MUSTNOT_ALT = '''\
class Order:
    def save(self):
        return 15
'''

MUSTNOT_JAR = '''\
import re
from http.cookiejar import CookieJar


class ProjectJar(CookieJar):
    def set_cookie(self, cookie):
        return cookie


def mn_external_base_override(jar: CookieJar):
    return jar.set_cookie(1)


class LookupDict(dict):
    def get(self, key, default=None):
        return default


def mn_builtin_base_override(d: dict):
    return d.get(1)


class Finder:
    def search(self, q):
        return q


def ext_module_still_external(p):
    return re.search("a", p)
'''

MUSTNOT_CONSOLE = '''\
import sys


class HTMLStringO:
    def _write(self, x):
        return x


class ThreadedStream:
    @staticmethod
    def push():
        sys.stdout = HTMLStringO()


def mn_patched_external_module(tb):
    sys.stdout._write(tb)
    return 0
'''

MUSTNOT_TYPEVAR = '''\
from typing import TypeVar

Order = TypeVar("Order")


def mn_typevar_named_like_class(x: Order):
    return x.save()
'''

MUSTNOT_PEP695 = '''\
from app.core import Order


def mn_pep695[Order](x: Order):
    return x.save()


def pep695_sibling(x: Order):
    return x.save()
'''

REPO_A = '''\
class Repo:
    def find(self):
        return "a"
'''

REPO_B = '''\
class Repo:
    def find(self):
        return "b"
'''

PICK = '''\
from b.repo import Repo


def pick_b(r: Repo):
    return r.find()
'''

TS_ORDER = '''\
export class Order {
  save(): number {
    return 1;
  }
}
'''

PY_NO_ORDER = '''\
def mn_ts_order():
    return Order()
'''

AUDIT = '''\
class Notifier:
    def notify(self):
        return 1

    def helper(self):
        return 2

    def uses_helper(self, b):
        self.helper()
        return b.helper()


def audit_untyped(nt):
    return nt.notify()


class Map:
    def bind(self, x):
        return x

    def add(self, x):
        return Map.bind(self, x)
'''

MAILER = '''\
class Mailer:
    def notify(self):
        return 3

    def helper(self):
        return 4
'''

REQ_INIT = '''\
from .sessions import Session
from . import sessions
'''

REQ_SESSIONS = '''\
class SessionRedirectMixin:
    def resolve_redirects(self, resp):
        return self.send(resp)


class Session(SessionRedirectMixin):
    def __setstate__(self, state):
        for attr, value in state.items():
            setattr(self, attr, value)

    def send(self, request):
        return request

    def request(self, method):
        return self.send(method)
'''

REQ_TEST = '''\
import requests
from requests.sessions import SessionRedirectMixin


def test_send():
    s = requests.Session()
    r = s.send(1)
    return r


class RedirectSession(SessionRedirectMixin):
    def send(self, *args, **kwargs):
        return 2


def test_redirect():
    session = RedirectSession()
    return session.send(3)
'''

FLASK_INIT = '''\
from .app import Flask
'''

FLASK_SCAFFOLD = '''\
class Scaffold:
    def _check(self):
        return 0
'''

FLASK_SANSIO_APP = '''\
from .scaffold import Scaffold


class App(Scaffold):
    def ensure_sync(self, func):
        return func

    def handle(self):
        return self._check()
'''

FLASK_APP = '''\
from .sansio.app import App


class Flask(App):
    def full_dispatch_request(self, f):
        return self.ensure_sync(f)()
'''

FLASK_CTX = '''\
from __future__ import annotations

import typing as t

if t.TYPE_CHECKING:
    from .app import Flask


class AppContext:
    def __init__(self, app: Flask):
        self.app = app

    def push(self, f):
        return self.app.ensure_sync(f)
'''

FLASK_JSON_INIT = '''\
def dumps(obj):
    return str(obj)


def loads(s):
    return s
'''

FLASK_JSON_PROVIDER = '''\
import json


class DefaultJSONProvider:
    def dumps(self, obj):
        return json.dumps(obj)

    def loads(self, s):
        return json.loads(s)
'''

FLASK_JSON_TAG = '''\
import json


class TaggedJSONSerializer:
    def dumps(self, value):
        return json.dumps(value)

    def loads(self, value):
        return json.loads(value)
'''

ROOTS_RUN = '''\
import helper


def main():
    return helper.assist()
'''

ROOTS_HELPER = '''\
def assist():
    return 1
'''

ROOTS_PKG_A = '''\
import b


def use_b():
    return b.assist()
'''

ROOTS_PKG_B = '''\
def assist():
    return 2
'''

ROOTS_PKG_REL = '''\
from . import b
from .b import assist as assist_alias
from .other import assist as other_assist


def use_rel():
    b.assist()
    return assist_alias()


def mn_alias_same_name():
    b.assist()
    return other_assist()


def use_rel_only():
    return b.assist()
'''

ROOTS_PKG_OTHER = '''\
def assist():
    return 5
'''

ROOTS_CORE_UTIL = '''\
def run(x):
    return x
'''

ROOTS_CORE_USER = '''\
import corepkg.util


def use_core():
    return corepkg.util.run(1)
'''

ROOTS_LR = '''\
def run(root):
    return root
'''

ROOTS_LEGACY = '''\
class LR:
    def run(self):
        return 0
'''

ROOTS_MAIN = '''\
import tools.lr as LR


class A:
    def run(self):
        return 1


class B:
    def run(self):
        return 2


def main(root):
    return LR.run(root)
'''

ROOTS_NS_MOD = '''\
def deep():
    return 3
'''

ROOTS_NS_USER = '''\
import nsp.inner.mod
from nsp.inner import mod as m2


def use_ns():
    nsp.inner.mod.deep()
    return m2.deep()
'''

ROOTS_SHARED = '''\
def thing():
    return 4
'''

ROOTS_SHARED_USER = '''\
import shared


def use_shared():
    return shared.thing()
'''

# ---- the "review" tree: shapes the S3b review found (each a must-not beside its positive sibling)
REV_COMPAT = '''\
from http.cookiejar import CookieJar
from collections import OrderedDict as ODict
'''

REV_JAR = '''\
from collections import deque

from .compat import CookieJar


class ProjectJar2(CookieJar):
    def set_cookie(self, cookie):
        return cookie


class Bag:
    def append(self, x):
        return x


def mn_reexported_base_override(jar: CookieJar, cookie):
    return jar.set_cookie(cookie)


def ext_not_extended_stays_external(q: deque):
    return q.append(1)
'''

REV_ODICT = '''\
from . import compat


class Registry(compat.ODict):
    def move_to_end(self, key, last=True):
        return key


def mn_qualified_reexport_override(d: compat.ODict, k):
    return d.move_to_end(k)
'''

REV_PEP695 = '''\
class Order:
    def save(self):
        return 1


class Box[Order]:
    def put(self, x: Order):
        return x.save()

    def first(self) -> Order:
        return self.items[0]

    def via_return(self):
        return self.first().save()

    class Inner:
        def fill(self, x: Order):
            return x.save()


def pep695_cls_sibling(x: Order):
    return x.save()
'''

REV_NODES = '''\
class Node:
    def visit(self):
        return 1
'''

REV_TREE = '''\
from .nodes import Node


class Tree:
    class Node:
        def visit(self):
            return 2

    root: Node

    def walk(self, n: Node):
        return n.visit()

    def walk_field(self):
        return self.root.visit()

    def make(self) -> Node:
        return Tree.Node()

    def chain(self):
        return self.make().visit()

    class Leaf(Node):
        def grow(self):
            return self.visit()


def outside(n: Node):
    return n.visit()
'''

REV_SELFPKG_INIT = '''\
from . import sub as sub


def use_in_init():
    return sub.run()
'''

REV_SELFPKG_SUB = '''\
def run():
    return 1
'''

REV_USE_SELFPKG = '''\
from app import selfpkg
from app.selfpkg import sub as s2


def via_package():
    return selfpkg.sub.run()


def via_from():
    return s2.run()
'''

REV_MONKEY = '''\
class Order:
    def save(self):
        return 1

    def total(self):
        return 2


def fake():
    return 3


shared = Order()
shared.save = fake


def mn_instance_patched():
    o = Order()
    o.save = fake
    return o.save()


def mn_setattr_patched():
    o = Order()
    setattr(o, "save", fake)
    return o.save()


def mn_module_instance_patched():
    return shared.save()


def instance_patched_other_attr():
    o = Order()
    o.save = fake
    return o.total()


def positive_sibling():
    o = Order()
    return o.save()
'''

# SELF inside a lambda / comprehension of a method: graphify attributes those calls to the method (a nested DEF is
# its own boundary: its calls belong to no enclosing caller — the core's ATTRIBUTION rule, slice S3a)
REV_CLOSURE = '''\
class Ledger:
    def total(self):
        return 0

    def s_lambda(self):
        f = lambda: self.total()
        return f

    def s_comprehension(self):
        return [self.total() for _ in range(2)]
'''

# ---- the "dispatch" tree: static binding vs a project override the engine's fan-out reaches (review finding 1;
# the merge is slice S3a's — see sec_dispatch)
DISPATCH = '''\
class Base:
    def m(self):
        return 1


class Sub(Base):
    def m(self):
        return 2


class Leaf:
    def m(self):
        return 3


def mn_override_param(r: Base):
    return r.m()


def leaf_sibling(r: Leaf):
    return r.m()


class Session:
    def get(self):
        return 1


class OtherSession:
    def get(self):
        return 2


class Holder:
    def __init__(self):
        self.session = Session()

    def mn_field_rebound_in_subclass(self):
        return self.session.get()


class SubHolder(Holder):
    def __init__(self):
        super().__init__()
        self.session = OtherSession()


class Root:
    def save(self):
        return 0


class Mixin(Root):
    def save(self):
        return super().save()


class Other(Root):
    def save(self):
        return 2


class Model(Mixin, Other):
    pass
'''

# click's shapes (pallets/click core.py / types.py, re-told): a field injected through an annotated parameter
# (`Context.command`), a class-annotated field (`Parameter.type: types.ParamType`, here `param_type`) whose
# subclasses live in another file, and a `t.cast(Group, ...)` local — each reaches a subclass's override at run time; beside each, a same-named
# method of an UNRELATED class (still removed) and a constructed receiver (exact: its subclass is never reached)
DISPATCH_KINDS = '''class ParamType:
    def to_info_dict(self):
        return {}


class Choice(ParamType):
    def to_info_dict(self):
        return {"choices": 1}


class Path(ParamType):
    def to_info_dict(self):
        return {"path": 1}


class Unrelated:
    def to_info_dict(self):
        return {"other": 1}
'''

# a typing.Protocol is STRUCTURAL: SqlFinder never names Finder, yet `f: Finder` may be one — the reader marks the
# Protocol an interface and the core keeps the engine's fan-out (its reach cannot be known), adding the typed key only
DISPATCH_PROTO = '''\
from typing import Protocol


class Finder(Protocol):
    def find(self):
        ...


class SqlFinder:
    def find(self):
        return 1


def use_finder(f: Finder):
    return f.find()
'''

DISPATCH_CMDS = '''import typing as t

from . import kinds


class Command:
    def get_command(self, ctx, name):
        return None

    def list_commands(self, ctx):
        return []

    def to_info_dict(self, ctx):
        return {}


class Group(Command):
    def get_command(self, ctx, name):
        return None

    def list_commands(self, ctx):
        return []

    def to_info_dict(self, ctx):
        return {"commands": 1}


class CommandCollection(Group):
    def get_command(self, ctx, name):
        return None

    def list_commands(self, ctx):
        return []


class Lookalike:
    def get_command(self, ctx, name):
        return None


class Context:
    def __init__(self, command: Command):
        self.command = command

    def to_info_dict(self):
        return self.command.to_info_dict(self)


class Parameter:
    def __init__(self, param_type: kinds.ParamType):
        self.param_type: kinds.ParamType = param_type

    def info_dict(self):
        return self.param_type.to_info_dict()


def complete_visible_commands(ctx: Context, incomplete):
    multi = t.cast(Group, ctx.command)
    for name in multi.list_commands(ctx):
        return multi.get_command(ctx, name)


def made_exact(ctx):
    group = Group()
    return group.get_command(ctx, "x")


class Trailer:
    def to_info_dict(self):
        return {"trailer": 1}
'''

# --- the phase-2 review ("review2" tree) -------------------------------------------------------------------------
# graphify's symbol-resolution pass walks the WHOLE body of an undecorated module-level def / async def and gives it
# every bare call of a from-imported project name — nested defs, their defaults, local classes' methods and lambdas
# in them included (click's termui.prompt -> Abort; fastapi's get_request_handler -> RequestValidationError): the
# core must keep those engine tuples (an uncertain site), never REPLACE them. A decorated def and a method are never
# walked (no tuple to keep there); a direct call in the def's own body is typed and REPLACEd as usual.
REV2_EXC = '''\
class Abort(Exception):
    pass


class AsyncAbort(Exception):
    pass


class DefaultAbort(Exception):
    pass


class LocalAbort(Exception):
    pass


class LamAbort(Exception):
    pass


class DecoAbort(Exception):
    pass


class MethAbort(Exception):
    pass
'''

REV2_NEST = '''\
from app.exc import Abort, AsyncAbort, DefaultAbort, LocalAbort, LamAbort, DecoAbort, MethAbort


def deco(f):
    return f


def outer(flag):
    def inner(text):
        try:
            return text.strip()
        except KeyError:
            raise Abort() from None

    return inner(flag)


async def outer_async():
    def inner():
        raise AsyncAbort()

    return inner


def outer_default():
    def inner(x=DefaultAbort()):
        return x

    return inner


def outer_local_cls():
    class Local:
        def run(self):
            raise LocalAbort()

    return Local


def outer_lam():
    def inner():
        return lambda: LamAbort()

    return inner


@deco
def outer_deco():
    def inner():
        raise DecoAbort()

    return inner


class Holder:
    def meth(self):
        def inner():
            raise MethAbort()

        return inner


def own():
    raise Abort()
'''

# a `from m import *` AFTER `from app.models import Order` may rebind Order at run time (app/other.py exports its own
# Order): never typed; the positive sibling binds Order last with the named import
REV2_MODELS = '''\
class Order:
    def save(self):
        return 1


class Item:
    def save(self):
        return 2
'''

REV2_OTHER = '''\
class Order:
    def save(self):
        return 3


__all__ = ["Order"]
'''

REV2_STAR = '''\
from app.models import Order
from app.other import *


def use_star():
    o = Order()
    return o.save()
'''

REV2_STAR_FIRST = '''\
from app.other import *
from app.models import Order


def use_named_last():
    o = Order()
    return o.save()
'''

# a member reached WITHOUT naming it in a call (requests' `getattr(jar, "copy", None)()`): the engine's `copy` tuple
# (from the external `copy.copy(...)`) is the run-time call — counted in the census, never REPLACEd; `plain_copy`'s is
# FALSE and goes
REV2_JAR = '''\
import copy
import operator


class Jar:
    def copy(self):
        return Jar()

    def clear(self):
        return None


def copy_jar(jar):
    if jar is None:
        return None
    if copy_method := getattr(jar, "copy", None):
        return copy_method()
    new_jar = copy.copy(jar)
    return new_jar


def copy_by_caller(jar):
    jar2 = copy.copy(jar)
    operator.methodcaller("copy")(jar2)
    return jar2


def copy_by_getter(jar):
    jar2 = copy.copy(jar)
    return operator.attrgetter("copy")(jar2)()


def plain_copy(jar):
    return copy.copy(jar)
'''

# a typing.Protocol receiver (fastapi's `orjson = cast(_OrjsonModule, importlib.import_module("orjson"))`): the call
# runs the module's function, never the Protocol's stub — the typed key is added (no REPLACE: veto dispatch) and never
# EXTRACTED
REV2_PROTO = '''\
import importlib
from typing import Protocol, cast


class _Json(Protocol):
    def dumps(self, obj) -> str: ...


js = cast(_Json, importlib.import_module("json"))


def render(x):
    return js.dumps(x)


class Other:
    def dumps(self, obj):
        return ""
'''

# the phase-2 verification (flask @36e4a82): a class written under `if TYPE_CHECKING:` exists for type checkers only.
# flask's `app_ctx: AppContextProxy = LocalProxy(...)  # type: ignore[assignment]` runs werkzeug's
# LocalProxy._get_current_object (external), never the Protocol stub ProxyMixin._get_current_object — 8 production
# callers gained that FALSE typed key. A member of an interface / Protocol is never a typed target; a typing-only
# class is an interface (no key to its own members), while the REAL class it inherits from still resolves.
PROXY_CTX = '''\
class AppContext:
    def push(self):
        return None

    def pop(self):
        return None
'''

PROXY_GLOBALS = '''\
from __future__ import annotations

import typing as t
from contextvars import ContextVar

from werkzeug.local import LocalProxy

if t.TYPE_CHECKING:  # pragma: no cover
    from .ctx import AppContext

    T = t.TypeVar("T", covariant=True)

    class ProxyMixin(t.Protocol[T]):
        def _get_current_object(self) -> T: ...

    class AppContextProxy(ProxyMixin[AppContext], AppContext): ...

    class SessionStub:
        def get_value(self, key): ...


_cv_app: ContextVar[AppContext] = ContextVar("flask.app_ctx")
app_ctx: AppContextProxy = LocalProxy(  # type: ignore[assignment]
    _cv_app, unbound_message="no app"
)
session: SessionStub = LocalProxy(_cv_app, "session")  # type: ignore[assignment]
'''

PROXY_HELPERS = '''\
from .globals import app_ctx
from .globals import session


def flash(message):
    ctx = app_ctx._get_current_object()
    return ctx


def push_ctx():
    return app_ctx.push()


def read_session():
    return session.get_value("k")
'''

PROXY_TYPING_ONLY = '''\
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    class Hidden:
        def run(self):
            ...

if not TYPE_CHECKING:
    class Visible:
        def run(self):
            return 1


def use_hidden(h: Hidden):
    return h.run()


def use_visible(v: Visible):
    return v.run()
'''

TREES = {
    "shapes": {
        "app/__init__.py": "",
        "app/repo.py": SHAPES_REPO,
        "app/models.py": SHAPES_MODELS,
    },
    "mustnot": {
        "app/__init__.py": "",
        "app/core.py": MUSTNOT_CORE,
        "app/maybe.py": MUSTNOT_MAYBE,
        "app/tryimp.py": MUSTNOT_TRY,
        "app/alt.py": MUSTNOT_ALT,
        "app/jar.py": MUSTNOT_JAR,
        "app/console.py": MUSTNOT_CONSOLE,
        "app/tvar.py": MUSTNOT_TYPEVAR,
        "app/pep695.py": MUSTNOT_PEP695,
        "app/audit.py": AUDIT,
        "app/mailer.py": MAILER,
        "a/repo.py": REPO_A,
        "b/repo.py": REPO_B,
        "app/pick.py": PICK,
        "web/order.ts": TS_ORDER,
        "app/no_order.py": PY_NO_ORDER,
    },
    "requests": {
        "src/requests/__init__.py": REQ_INIT,
        "src/requests/sessions.py": REQ_SESSIONS,
        "tests/test_requests.py": REQ_TEST,
    },
    "flask": {
        "src/flask/__init__.py": FLASK_INIT,
        "src/flask/sansio/__init__.py": "",
        "src/flask/sansio/scaffold.py": FLASK_SCAFFOLD,
        "src/flask/sansio/app.py": FLASK_SANSIO_APP,
        "src/flask/app.py": FLASK_APP,
        "src/flask/ctx.py": FLASK_CTX,
        "src/flask/json/__init__.py": FLASK_JSON_INIT,
        "src/flask/json/provider.py": FLASK_JSON_PROVIDER,
        "src/flask/json/tag.py": FLASK_JSON_TAG,
    },
    "roots": {
        "scripts/run.py": ROOTS_RUN,
        "scripts/helper.py": ROOTS_HELPER,
        "pkg/__init__.py": "",
        "pkg/a.py": ROOTS_PKG_A,
        "pkg/b.py": ROOTS_PKG_B,
        "pkg/rel.py": ROOTS_PKG_REL,
        "pkg/other.py": ROOTS_PKG_OTHER,
        "libs/core/corepkg/__init__.py": "",
        "libs/core/corepkg/util.py": ROOTS_CORE_UTIL,
        "libs/core/corepkg/user.py": ROOTS_CORE_USER,
        "tools/__init__.py": "",
        "tools/lr.py": ROOTS_LR,
        "tools/legacy.py": ROOTS_LEGACY,
        "tools/main.py": ROOTS_MAIN,
        "nsp/inner/mod.py": ROOTS_NS_MOD,
        "pkg/ns_user.py": ROOTS_NS_USER,
        "shared.py": ROOTS_SHARED,
        "src/shared.py": ROOTS_SHARED,
        "roots_user.py": ROOTS_SHARED_USER,
    },
    "review": {
        "app/__init__.py": "",
        "app/compat.py": REV_COMPAT,
        "app/jar.py": REV_JAR,
        "app/odict.py": REV_ODICT,
        "app/pep695cls.py": REV_PEP695,
        "app/nodes.py": REV_NODES,
        "app/tree.py": REV_TREE,
        "app/selfpkg/__init__.py": REV_SELFPKG_INIT,
        "app/selfpkg/sub.py": REV_SELFPKG_SUB,
        "app/use_selfpkg.py": REV_USE_SELFPKG,
        "app/monkey.py": REV_MONKEY,
        "app/closure.py": REV_CLOSURE,
    },
    "dispatch": {
        "app/__init__.py": "",
        "app/dispatch.py": DISPATCH,
        "app/kinds.py": DISPATCH_KINDS,
        "app/cmds.py": DISPATCH_CMDS,
        "app/proto.py": DISPATCH_PROTO,
    },
    "review2": {
        "app/__init__.py": "",
        "app/exc.py": REV2_EXC,
        "app/nest.py": REV2_NEST,
        "app/models.py": REV2_MODELS,
        "app/other.py": REV2_OTHER,
        "app/star.py": REV2_STAR,
        "app/star_first.py": REV2_STAR_FIRST,
        "app/jar.py": REV2_JAR,
        "app/proto.py": REV2_PROTO,
    },
    "proxy": {
        "src/flask/__init__.py": "",
        "src/flask/ctx.py": PROXY_CTX,
        "src/flask/globals.py": PROXY_GLOBALS,
        "src/flask/helpers.py": PROXY_HELPERS,
        "src/flask/typing_only.py": PROXY_TYPING_ONLY,
    },
}
# the files only Python 3.12+ parses (PEP 695): older interpreters must fail them cleanly (no facts)
PEP695_FILES = {("mustnot", "app/pep695.py"), ("review", "app/pep695cls.py")}

# ================================================================================================
# Materialized trees, captured graphs, the pipeline
# ================================================================================================

_TMP: list = []


def tmp() -> Path:
    if not _TMP:
        _TMP.append(Path(tempfile.mkdtemp(prefix="dm_s3b_")))
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
    return FX / bucket / "py" / f"{name}.json"


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


def run_pipeline(graph, root: Path, env: dict, *, ast_path=False) -> Run:
    """The push-time passes up to and including typed_calls.link, exactly as sync runs them (design 2.8 steps
    1-2, the call slice S2 wires in and offline_calls.py mirrors): map_graph (or the ast extractor) -> test /
    vendor exclusion -> enrich sidecar -> recognizers -> _unkey_receiver_calls -> link."""
    with clean_env(**env):
        if ast_path:
            nodes, calls, imports = S._rebuild_code_ast(root)
        else:
            nodes, calls, imports = GX.map_graph(copy.deepcopy(graph), root=root)
        nodes, calls, imports = S._maybe_exclude_vendor(root, *S._maybe_exclude_tests(nodes, calls, imports))
        S.enrich.apply_sidecar(root, nodes)
        nodes, _typed = S._apply_recognizers(root, nodes)
        calls, _n = S._unkey_receiver_calls(root, nodes, calls)
        before = list(calls)
        flags = {k: v for k, v in os.environ.items() if k.startswith("DEEPMAP_")}
        out, stats = TC.link(str(root), nodes, calls, is_test=S._is_test_path, env=flags)
    return Run(nodes, calls, before, out, stats)


def stable(nodes) -> dict:
    """node key -> its version-stable identity `label:name@file:line`."""
    return {n["key"]: f"{n.get('label')}:{n.get('name')}@{n.get('source_file')}:{n.get('start_line')}"
            for n in nodes}


def ident(n) -> str:
    return f"{n.get('source_file')}::{n.get('name')}"


def typed_view(nodes, *, form="stable") -> dict:
    """{caller: {target: grade}} for every caller the pass keyed — by stable id, or by `file::name` (form="name";
    a name shared by two keyed callers / two targets of one caller is reported as a collision)."""
    if form == "name":
        ids = {n["key"]: ident(n) for n in nodes}
    else:
        ids = stable(nodes)

    def name(k):
        return ids.get(k, k)
    out = {}
    for n in nodes:
        if not n.get("call_typed"):
            continue
        c = name(n["key"])
        tg = {}
        for t, g in sorted(n["call_typed"].items()):
            tn = name(t)
            tg[tn if tn not in tg else tn + "#collision"] = g
        out[c if c not in out else c + "#collision"] = tg
    return out


def weak_view(nodes) -> dict:
    by = {n["key"]: n for n in nodes}
    return {ident(n): sorted(ident(by[t]) for t in n["call_weak"] if t in by) for n in nodes if n.get("call_weak")}


def key_of(nodes, ref: str):
    """The key of the (non-Module) node `file::name` (None when absent or not unique)."""
    hits = [n["key"] for n in nodes if ident(n) == ref and n.get("label") != "Module"]
    return hits[0] if len(hits) == 1 else None


def tuples_of(run: Run, calls, caller_ref: str, member: str) -> list:
    """The (callee, is-key) tuples of caller `file::name` in `calls` whose callee denotes `member`."""
    ck = key_of(run.nodes, caller_ref)
    name_of = {n["key"]: n.get("name") for n in run.nodes}
    return [(str(c[1]), GX.is_call_key(c[1])) for c in calls
            if c[0] == ck and TC.callee_matches(c[1], member, name_of, TC._is_call_key)]


# every flag the variants touch is PINNED (never "unset = off"), so a later default flip (slice S8) cannot move
# these expectations: TYPED on for Python, the audit and the family filter off, PRECISE off / on
_OFF = {"DEEPMAP_TYPED_CALLS": "0", "DEEPMAP_CALLS_AUDIT": "0", "DEEPMAP_PRECISE_CALLS": "0",
        "DEEPMAP_CALLS_FAMILY_FILTER": "0"}
MODES = {"default": dict(_OFF, DEEPMAP_TYPED_CALLS="python"),
         "precise": dict(_OFF, DEEPMAP_TYPED_CALLS="python", DEEPMAP_PRECISE_CALLS="1")}


def buckets_present() -> list:
    return [b for b in sorted(BUCKETS.values()) if all(fixture_path(b, t).is_file() for t in TREES)]


_RUNS: dict = {}


def run_for(bucket: str, tree: str, mode: str) -> Run:
    """The pipeline on the captured graph of (bucket, tree) under MODES[mode] (memoised; a mutation run clears
    the memo)."""
    k = (bucket, tree, mode)
    if k not in _RUNS:
        _RUNS[k] = run_pipeline(captured(bucket, tree)["graph"], root_for(tree), MODES[mode])
    return _RUNS[k]


# ================================================================================================
# A: the import-root resolver
# ================================================================================================

def sec_resolver() -> None:
    print("\n-- A: py_import_resolver: import roots, never suffixes --")
    R = pir()
    if R is None:
        check("A py_import_resolver imports", False, _IMPORT_ERROR)
        return
    files = ["src/requests/__init__.py", "src/requests/sessions.py", "src/requests/api.py",
             "tests/test_requests.py", "tests/helpers.py", "libs/core/corepkg/__init__.py",
             "libs/core/corepkg/util.py", "scripts/run.py", "scripts/helper.py", "pkg/__init__.py", "pkg/a.py",
             "pkg/b.py", "nsp/inner/mod.py", "shared.py", "src/shared.py", "dual/b.py", "dual/b/__init__.py",
             "stub/s.pyi", "both/t.py", "both/t.pyi", "src/flask/__init__.py", "src/flask/json/__init__.py",
             "src/flask/json/provider.py", "pkg/sub/__init__.py", "pkg/sub/c.py", "dual/__init__.py",
             "stub/__init__.py", "both/__init__.py"]
    r = R.ImportRoots(files)
    rs = r.roots("src/requests/sessions.py")
    check("A roots: the repository root, src/ and the parent of each top-level package",
          rs == ("", "libs/core", "src"), rs)
    check("A roots: a module INSIDE a package never adds its own directory (Python 3 has no implicit relative "
          "import)", r.roots("pkg/a.py") == rs and r.roots("src/flask/json/provider.py") == rs,
          (r.roots("pkg/a.py"), r.roots("src/flask/json/provider.py")))
    check("A roots: a script outside any package adds its own directory (sys.path[0])",
          r.roots("scripts/run.py") == ("", "libs/core", "scripts", "src") and "tests" in r.roots(
              "tests/test_requests.py"), (r.roots("scripts/run.py"), r.roots("tests/test_requests.py")))
    m = r.module
    check("A `import requests` from a test resolves through src/ to the package",
          m("requests", "tests/test_requests.py") == R.Target("file", "src/requests/__init__.py"))
    check("A `import requests.sessions` -> src/requests/sessions.py",
          m("requests.sessions", "src/requests/api.py") == R.Target("file", "src/requests/sessions.py"))
    check("A `import helper` from scripts/run.py -> scripts/helper.py (script semantics)",
          m("helper", "scripts/run.py") == R.Target("file", "scripts/helper.py"))
    check("A `import b` inside package pkg does NOT bind pkg/b.py (no implicit relative import)",
          m("b", "pkg/a.py").kind == "none", m("b", "pkg/a.py"))
    check("A `import json` inside src/flask/json/ is NOT flask's json package (stdlib shadowing)",
          m("json", "src/flask/json/provider.py").kind == "none", m("json", "src/flask/json/provider.py"))
    check("A `import corepkg.util` -> the package-parent root libs/core",
          m("corepkg.util", "pkg/a.py") == R.Target("file", "libs/core/corepkg/util.py"))
    check("A relative `from .sessions import x` -> the importer's package path",
          m("sessions", "src/requests/api.py", 1) == R.Target("file", "src/requests/sessions.py"))
    check("A relative `from . import x` names the package itself",
          m("", "src/requests/api.py", 1) == R.Target("file", "src/requests/__init__.py"))
    check("A relative `from ..sub import c` climbs one package",
          m("sub.c", "pkg/sub/c.py", 2) == R.Target("file", "pkg/sub/c.py"))
    check("A a relative import above the repository root is unknown",
          m("x", "pkg/a.py", 3).kind == "unknown", m("x", "pkg/a.py", 3))
    ns = m("nsp", "pkg/a.py")
    check("A a directory without __init__.py is a namespace package (PEP 420)",
          ns == R.Target("namespace", dirs=("nsp",)), ns)
    inner = r.submodule(ns, "inner")
    check("A ...whose submodules resolve by path: nsp.inner (namespace) -> nsp.inner.mod (file)",
          inner == R.Target("namespace", dirs=("nsp/inner",))
          and r.submodule(inner, "mod") == R.Target("file", "nsp/inner/mod.py"), (inner, r.submodule(inner, "mod")))
    check("A two DIFFERENT regular matches across roots (shared.py, src/shared.py) are ambiguous, never a guess",
          m("shared", "pkg/a.py").kind == "ambiguous", m("shared", "pkg/a.py"))
    check("A within one root a package directory wins over a module of the same name (dual/b/)",
          m("dual.b", "pkg/a.py") == R.Target("file", "dual/b/__init__.py"), m("dual.b", "pkg/a.py"))
    check("A a stub-only module resolves to its .pyi; a .py beside a .pyi wins",
          m("stub.s", "pkg/a.py") == R.Target("file", "stub/s.pyi")
          and m("both.t", "pkg/a.py") == R.Target("file", "both/t.py"), (m("stub.s", "pkg/a.py"),
                                                                         m("both.t", "pkg/a.py")))
    check("A a plain module has no submodules; a package does",
          r.submodule(R.Target("file", "pkg/b.py"), "x").kind == "none"
          and r.submodule(R.Target("file", "pkg/sub/__init__.py"), "c") == R.Target("file", "pkg/sub/c.py"))
    check("A an absolute import nothing holds is `none` (the caller's external test decides)",
          m("os.path", "pkg/a.py").kind == "none" and R.top_name("os.path") == "os")
    check("A a malformed module name is unknown", m("a-b", "pkg/a.py").kind == "unknown"
          and m("", "pkg/a.py").kind == "unknown")
    # disk: a real module the graph does not hold is UNKNOWN (never external); an empty __init__ on disk counts
    d = tmp() / "resolver_disk"
    write_tree(d, {"extra/mod.py": "x = 1\n", "lib/pk/__init__.py": "", "lib/pk/m.py": "y = 2\n"})
    rd = R.ImportRoots(["lib/pk/m.py", "app.py"], root=d)
    check("A a module on disk but not in the graph is unknown (root given), none without a root",
          rd.module("extra.mod", "app.py").kind == "unknown"
          and R.ImportRoots(["lib/pk/m.py", "app.py"]).module("extra.mod", "app.py").kind == "none",
          rd.module("extra.mod", "app.py"))
    check("A an __init__.py on disk (not in the graph) still makes a package: its parent is a root",
          "lib" in rd.base_roots and rd.module("pk.m", "app.py").kind == "file", (rd.base_roots,
                                                                                 rd.module("pk.m", "app.py")))
    # ---- the binder (receiver_facts_py.PyBinder) over the resolver
    F = rf()
    if F is None or TC is None:
        check("A receiver_facts_py imports (the binder)", False, _IMPORT_ERROR)
        return
    srcs = {"pkg/__init__.py": "from .models import Order\n",
            "pkg/models.py": "class Order:\n    pass\n",
            "pkg/jar.py": "import http.cookiejar as cookielib\nfrom collections.abc import MutableMapping\n\n\n"
                          "class Jar(cookielib.CookieJar, MutableMapping):\n    pass\n\n\nclass D(dict):\n    pass\n",
            "nsp/inner/mod.py": "def deep():\n    return 1\n",
            "app.py": "import os\n"}
    facts = {r: F.file_facts(r, ast.parse(t), t.split("\n"), TC) for r, t in srcs.items()}
    ctx = TC.BinderContext(root=str(tmp() / "no_such_root"), lang="python", rels=tuple(srcs), facts=facts,
                           graph_files=frozenset(srcs))
    bd = F.make_reader(TC.LANGUAGES["python"]).binder(ctx)
    I = TC.Import
    got = {
        "named": bd.resolve_import("app.py", I("Order", "named", "pkg", "Order")),
        "relative": bd.resolve_import("pkg/models.py", I("Order", "named", ".models", "Order")),
        "module": bd.resolve_import("app.py", I("m", "module", "pkg.models")),
        "package": bd.resolve_import("app.py", I("pkg", "package", "pkg.models")),
        "namespace": bd.resolve_import("app.py", I("nsp", "package", "nsp.inner.mod")),
        "external": bd.resolve_import("app.py", I("os", "module", "os")),
        "ext_named": bd.resolve_import("app.py", I("join", "named", "os.path", "join")),
        "extended_named": bd.resolve_import("app.py", I("CookieJar", "named", "http.cookiejar", "CookieJar")),
        "extended_module": bd.resolve_import("app.py", I("cookielib", "module", "http.cookiejar")),
        "extended_abc": bd.resolve_import("app.py", I("MutableMapping", "named", "collections.abc", "MutableMapping")),
        "bad_relative": bd.resolve_import("app.py", I("x", "named", "...up", "x")),
    }
    want = {
        "named": TC.Resolution("symbol", rel="pkg/__init__.py", name="Order"),
        "relative": TC.Resolution("symbol", rel="pkg/models.py", name="Order"),
        "module": TC.Resolution("module", rel="pkg/models.py"),
        "package": TC.Resolution("module", rel="pkg/__init__.py"),
        "external": TC.Resolution("external", stems=("os",)),
        "ext_named": TC.Resolution("external", stems=("os",)),
        "extended_named": TC.RES_UNKNOWN, "extended_module": TC.RES_UNKNOWN, "extended_abc": TC.RES_UNKNOWN,
        "bad_relative": TC.RES_UNKNOWN,
    }
    bad = {k: (got[k], v) for k, v in want.items() if got[k] != v}
    check("A the binder: named / relative / module / package -> the project file; an absolute import nothing holds "
          "-> external (stems: its top-level name); an invalid relative import -> unknown", not bad, bad)
    check("A the binder: a namespace package resolves to an opaque token whose submodules resolve by path",
          got["namespace"].kind == "namespace"
          and bd.submodule(bd.submodule(got["namespace"].name, "inner").name, "mod")
          == TC.Resolution("module", rel="nsp/inner/mod.py"), got["namespace"])
    check("A the binder: a class a PROJECT class extends is never a closed external type (`class Jar(cookielib."
          "CookieJar)`: `CookieJar` / `import http.cookiejar` / `MutableMapping` unknown; `class D(dict)`: the "
          "builtin dict unknown; an un-extended builtin stays the core's)",
          F.extended_externals(facts) == (frozenset({("http.cookiejar", "CookieJar"), ("collections.abc",
                                                                                       "MutableMapping"),
                                                     ("builtins", "dict")}), frozenset())
          and bd.resolve_free("app.py", "dict") == TC.RES_UNKNOWN and bd.resolve_free("app.py", "str") is None,
          F.extended_externals(facts))
    psrc = ("import sys\nimport os\nimport builtins\nfrom lib import Thing\n\n\ndef f(s):\n    sys.stdout = s\n"
            "    setattr(os, 'sep', '/')\n    Thing.attr = 1\n    builtins.open = s\n\n\ndef t(monkeypatch):\n"
            "    monkeypatch.setattr('json.dumps', None)\n")
    pf = {"x.py": F.file_facts("x.py", ast.parse(psrc), psrc.split("\n"), TC), "app.py": facts["app.py"]}
    pairs, mods = F.patched_externals(pf)
    check("A patched externals: `sys.stdout = s`, `setattr(os, 'sep', ...)`, `Thing.attr = 1` (from lib), "
          "`builtins.open = f`, `monkeypatch.setattr('json.dumps', ...)`",
          {("sys", "stdout"), ("os", "sep"), ("lib", "Thing"), ("lib.Thing", "attr"), ("builtins", "open"),
           ("json", "dumps")} <= pairs and {"sys", "os", "lib.Thing", "builtins", "json"} <= mods, (pairs, mods))
    bp = F.make_reader(TC.LANGUAGES["python"]).binder(
        TC.BinderContext(root=str(tmp() / "no_such_root"), lang="python", rels=tuple(pf), facts=pf,
                         graph_files=frozenset(pf)))
    check("A the binder: a PATCHED external module / class is never closed (`import sys`, `from json import "
          "loads`, `from lib import Thing`, the builtin `open`); an unpatched one stays external (`import re`)",
          bp.resolve_import("app.py", I("sys", "module", "sys")) == TC.RES_UNKNOWN
          and bp.resolve_import("app.py", I("loads", "named", "json", "loads")) == TC.RES_UNKNOWN
          and bp.resolve_import("app.py", I("Thing", "named", "lib", "Thing")) == TC.RES_UNKNOWN
          and bp.resolve_free("app.py", "open") == TC.RES_UNKNOWN
          and bp.resolve_import("app.py", I("re", "module", "re")) == TC.Resolution("external", stems=("re",)),
          (bp.resolve_import("app.py", I("sys", "module", "sys")), bp.resolve_import("app.py", I("re", "module",
                                                                                                "re"))))
    # ---- an external base reached through a project RE-EXPORT (review finding: requests' compat idiom)
    rsrc = {"lib/__init__.py": "",
            "lib/compat.py": "from http.cookiejar import CookieJar\nfrom collections import OrderedDict as ODict\n"
                             "from os import *\nAlias = ODict\n",
            "lib/jar2.py": "from .compat import CookieJar\n\n\nclass J2(CookieJar):\n    pass\n",
            "lib/odict.py": "from . import compat\n\n\nclass R(compat.ODict):\n    pass\n",
            "lib/deep.py": "import lib.compat\n\n\nclass D(lib.compat.Alias):\n    pass\n",
            "lib/star.py": "from .compat import *\n\n\nclass S(PathLike):\n    pass\n",
            "lib/odd.py": "from .gone import Thing\n\n\nclass O(Thing):\n    pass\n",
            "lib/proj.py": "from .jar2 import J2\n\n\nclass P(J2):\n    pass\n",
            "app.py": "x = 1\n"}
    rfacts = {r: F.file_facts(r, ast.parse(t), t.split("\n"), TC) for r, t in rsrc.items()}
    got_ext = F.extended_externals(rfacts)
    want_ext = (frozenset({("http.cookiejar", "CookieJar"), ("collections", "OrderedDict"), ("os", "PathLike"),
                           ("builtins", "PathLike")}), frozenset({"Thing"}))
    check("A extended externals follow project RE-EXPORTS to the external class: `from .compat import CookieJar` "
          "(named), `compat.ODict` after `from . import compat` (a package's submodule), `lib.compat.Alias` with "
          "`Alias = ODict` (a module-level alias), a star import (`os.PathLike` or the builtin); a base that cannot be "
          "followed opens its NAME; a project base (P(J2)) opens nothing", got_ext == want_ext, got_ext)
    br = F.make_reader(TC.LANGUAGES["python"]).binder(
        TC.BinderContext(root=str(tmp() / "no_such_root"), lang="python", rels=tuple(rsrc), facts=rfacts,
                         graph_files=frozenset(rsrc)))
    rs = {"reexported": br.resolve_import("lib/compat.py", I("CookieJar", "named", "http.cookiejar", "CookieJar")),
          "qualified": br.resolve_import("lib/compat.py", I("ODict", "named", "collections", "OrderedDict")),
          "sibling": br.resolve_import("app.py", I("deque", "named", "collections", "deque")),
          "open_name": br.resolve_import("app.py", I("Thing", "named", "vendorlib", "Thing")),
          "other_name": br.resolve_import("app.py", I("Other", "named", "vendorlib", "Other"))}
    check("A the binder: the re-exported externals are never closed (CookieJar, OrderedDict -> unknown); an "
          "un-extended class of the same module stays external (collections.deque); an unfollowable base opens "
          "only its own name (vendorlib.Thing unknown, vendorlib.Other external)",
          rs["reexported"] == TC.RES_UNKNOWN and rs["qualified"] == TC.RES_UNKNOWN
          and rs["sibling"] == TC.Resolution("external", stems=("collections",))
          and rs["open_name"] == TC.RES_UNKNOWN
          and rs["other_name"] == TC.Resolution("external", stems=("vendorlib",)), rs)
    # ---- `from . import sub` inside the package's OWN __init__ binds the submodule (flask's `from . import json`)
    ssrc = {"pkg2/__init__.py": "from . import sub\nfrom . import sub as alias\nfrom pkg2 import sub as again\n",
            "pkg2/sub.py": "def run():\n    return 1\n",
            "app.py": "x = 1\n"}
    sfacts = {r: F.file_facts(r, ast.parse(t), t.split("\n"), TC) for r, t in ssrc.items()}
    bs = F.make_reader(TC.LANGUAGES["python"]).binder(
        TC.BinderContext(root=str(tmp() / "no_such_root"), lang="python", rels=tuple(ssrc), facts=sfacts,
                         graph_files=frozenset(ssrc)))
    mod = TC.Resolution("module", rel="pkg2/sub.py")
    sr = {"rel": bs.resolve_import("pkg2/__init__.py", I("sub", "named", ".", "sub")),
          "alias": bs.resolve_import("pkg2/__init__.py", I("alias", "named", ".", "sub")),
          "absolute": bs.resolve_import("pkg2/__init__.py", I("again", "named", "pkg2", "sub")),
          "missing": bs.resolve_import("pkg2/__init__.py", I("nope", "named", ".", "nope")),
          "outside": bs.resolve_import("app.py", I("sub", "named", "pkg2", "sub"))}
    check("A the binder: `from . import sub` (also `as alias`, also `from pkg2 import sub`) inside pkg2/__init__.py "
          "is the SUBMODULE pkg2/sub.py; with no such submodule it stays the package's own symbol; from another file "
          "it is the package's symbol (the core follows it)",
          sr["rel"] == mod and sr["alias"] == mod and sr["absolute"] == mod
          and sr["missing"] == TC.Resolution("symbol", rel="pkg2/__init__.py", name="nope")
          and sr["outside"] == TC.Resolution("symbol", rel="pkg2/__init__.py", name="sub"), sr)


# ================================================================================================
# B: the reader's IR on snippets
# ================================================================================================

def facts_of(src: str, rel: str = "pkg/m.py"):
    tree = ast.parse(src)
    lines = src.split("\n")
    f = rf().file_facts(rel, tree, lines, TC)
    return f


def sc(f, name, nth=0):
    hits = [s for s in f.scopes if s.name == name]
    return hits[nth] if len(hits) > nth else None


def cls_of(f, name):
    hits = [c for c in f.classes if c.name == name]
    return hits[0] if hits else None


B_BINDINGS = '''\
import typing
from typing import cast, Optional
import app.models as M
from app.models import Order


def bindings(p: Order, *args, q=None, **kw):
    a = Order()
    b = M.Order()
    c: Order = make()
    d = cast(Order, p)
    e = p.repo
    f = p
    g = None
    h = "text"
    i = [1, 2]
    j = p if q else None
    k = typing.cast("Order", p)
    return a, b, c, d, e, f, g, h, i, j, k


def poisons(items, ctx):
    for x1 in items:
        pass
    async_ok = 1
    with ctx as x2:
        pass
    try:
        pass
    except Exception as x3:
        pass
    x4 = 1
    x4 += 1
    x5, x6 = items
    [x7, *x8] = items
    del x9
    import os as x10
    from os import path as x11

    def x12():
        return 0

    class x13:
        pass
    if (x14 := items):
        pass
    global x15
    match items:
        case [x16, *x17]:
            pass
        case {"k": x18, **x19}:
            pass
        case Order(total=x20):
            pass
    return [y for y in items], [(w := z) for z in items]


def outer():
    v = Order()

    def inner():
        nonlocal v
        v = 1
    return v
'''


def sec_reader() -> None:
    print("\n-- B: the reader's IR (receiver_facts_py) --")
    R = rf()
    if R is None or TC is None:
        check("B receiver_facts_py imports", False, _IMPORT_ERROR)
        return
    rd = R.make_reader(TC.LANGUAGES["python"])
    check("B make_reader -> a reader whose canary passes and whose version is stamped on its facts",
          rd.canary() is True and rd.version == R.READER_VERSION and facts_of("x = 1\n").reader_version
          == R.READER_VERSION)
    f = facts_of(B_BINDINGS)
    check("B the binding snippet is a valid FileFacts", TC.validate_facts(f) == "", TC.validate_facts(f))
    s = sc(f, "bindings")
    loc = s.locals
    want = {
        "a": ("call", TC.r_call(None, "Order")),
        "b": ("call", TC.r_call(TC.r_module("M"), "Order")),
        "e": ("field", TC.r_field(TC.r_ident("p"), "repo")),
        "f": ("field", TC.r_ident("p")),
        "h": ("field", TC.r_literal("str")),
        "i": ("field", TC.r_literal("list")),
    }
    for n, (kind, val) in want.items():
        got = loc.get(n, [None])[0]
        check(f"B R2 `{n} = ...` is Binding({kind!r}, {val})",
              got is not None and got.kind == kind and got.value == val, got)
    check("B `c: Order = make()` is an annotation binding (the declared type wins)",
          loc["c"][0].kind == "ann" and loc["c"][0].type == TC.t_name("Order"), loc["c"])
    check("B `cast(Order, p)` / `typing.cast(\"Order\", p)` bind the cast type (typing.cast only)",
          loc["d"][0] == TC.Binding("ann", TC.t_name("Order"), line=loc["d"][0].line, scope=loc["d"][0].scope)
          and loc["k"][0].kind == "ann" and loc["k"][0].type == TC.t_name("Order"), (loc["d"], loc["k"]))
    check("B `g = None` is the nullable binding; a conditional expression is unknown",
          loc["g"][0].kind == "none" and loc["j"][0].kind == "unknown", (loc["g"], loc["j"]))
    check("B every local binding carries its scope (the names its expression uses)",
          all(b.scope == f.scopes.index(s) for bl in loc.values() for b in bl))
    check("B params: annotated -> the TypeRef; *args / **kw / an unannotated default -> no type",
          s.params["p"].type == TC.t_name("Order") and s.params["args"].type is None
          and s.params["kw"].type is None and s.params["q"].type is None, s.params)
    p = sc(f, "poisons")
    names = {f"x{i}" for i in range(1, 21) if i not in (9, 15)} | {"x9", "x15"}
    missing = sorted(n for n in names if n not in p.poisoned)
    check("B POISONED: for / with / except / augassign / tuple / starred / del / local import / nested def & "
          "class / walrus / global / match captures (sequence, star, mapping rest, class keyword)",
          not missing, missing)
    check("B ...and no poisoned name is also a typed local", not (set(p.locals) & set(p.poisoned)),
          set(p.locals) & set(p.poisoned))
    check("B a walrus inside a comprehension poisons the ENCLOSING def (PEP 572)", "w" in p.poisoned, p.poisoned)
    comps = [x for x in f.scopes if x.kind == "comprehension" and x.parent == f.scopes.index(p)]
    check("B a comprehension is its own scope whose targets are poisoned (they shadow outer names)",
          len(comps) == 2 and "y" in comps[0].poisoned and "z" in comps[1].poisoned, [c.poisoned for c in comps])
    check("B `global x` adds an unknown module-level binding of x",
          any(b.kind == "unknown" for b in f.globals.get("x15", [])), f.globals.get("x15"))
    o = sc(f, "outer")
    check("B `nonlocal v` in a nested def poisons v in every enclosing def", "v" in o.poisoned, o.poisoned)

    # ---- SELF, closures, super(), classmethods, staticmethods
    src = '''\
class Base:
    def m(self):
        return 1


class C(Base):
    def inst(self, other):
        self.m()
        super().m()
        super(C, self).m()
        super(Base, self).m()
        f = lambda: self.m()
        g = [self.m() for _ in range(2)]

        def nested():
            return self.m()

        def shadow(self):
            return self.m()
        return f, g, nested, shadow

    @classmethod
    def make(cls):
        return cls.m()

    @staticmethod
    def stat(self):
        return self.m()

    def rebound(self, other):
        self = other
        return self.m()

    def __new__(cls):
        return cls.m()
'''
    f = facts_of(src)
    check("B the SELF snippet is a valid FileFacts", TC.validate_facts(f) == "", TC.validate_facts(f))
    ci = [c.name for c in f.classes].index("C")
    inst = sc(f, "inst")
    kinds = [(x.member, x.recv.kind if x.recv else None) for x in inst.sites if x.kind == "member"]
    check("B a method's first parameter is SELF (inst); zero-arg super() and super(C, self) are SUPER; "
          "super(Base, self) is not", kinds[:4] == [("m", "SELF"), ("m", "SUPER"), ("m", "SUPER"), ("m", "CALL")]
          and inst.self_kind == "inst" and inst.cls == ci, (kinds, inst.self_kind, inst.cls))
    lam, comp = sc(f, "<lambda>"), [x for x in f.scopes if x.kind == "comprehension"][0]
    nested, shadow = sc(f, "nested"), sc(f, "shadow")
    check("B a lambda, a comprehension and a nested def that do not rebind it inherit SELF (cls + self_kind)",
          all(x.cls == ci and x.self_kind == "inst" and x.sites[0].recv == TC.R_SELF for x in (lam, comp, nested)),
          [(x.name, x.cls, x.self_kind, x.sites[0].recv) for x in (lam, comp, nested)])
    check("B a nested def whose own parameter is named self has no inherited SELF (its self is unknown)",
          shadow.self_kind == "" and shadow.cls == -1 and shadow.sites[0].recv == TC.r_ident("self"),
          (shadow.cls, shadow.self_kind, shadow.sites[0].recv))
    mk, st, rb, new = sc(f, "make"), sc(f, "stat"), sc(f, "rebound"), sc(f, "__new__")
    check("B @classmethod and __new__: self_kind `class` (cls.m() is a SELF class-object call)",
          mk.self_kind == "class" and mk.sites[0].recv == TC.R_SELF and mk.params["cls"].type
          == TC.t_classof(TC.t_self()) and new.self_kind == "class", (mk.self_kind, mk.params, new.self_kind))
    check("B @staticmethod: no SELF (its first parameter is an ordinary one)",
          st.self_kind == "" and st.sites[0].recv == TC.r_ident("self"), (st.self_kind, st.sites[0].recv))
    check("B a method that rebinds self loses SELF (`self = other; self.m()` is an IDENT)",
          rb.self_kind == "" and rb.sites[0].recv == TC.r_ident("self") and rb.params["self"].type is None,
          (rb.self_kind, rb.sites[0].recv, rb.params["self"]))
    fn = facts_of("class C:\n    def m(self):\n        def inner():\n            return super().m()\n"
                  "        return inner\n")
    check("B super() in a nested def is never SUPER (zero-arg super() needs the method's own first argument)",
          sc(fn, "inner").sites[-1].recv != TC.R_SUPER, sc(fn, "inner").sites)

    # ---- annotations
    src = '''\
from __future__ import annotations
import typing as t
from typing import Annotated, Any, ClassVar, Final, List, Literal, Optional, Self, Type, Union
from app.models import Order


class Mine:
    pass


def anns(a: Optional[Order], b: Order | None, c: Union[Order, Mine], d: Annotated[Order, "x"],
         e: Type[Order], f: type[Order], g: "Order", h: "Order[[", i: List[Order], j: Any, k: t.Optional[Order],
         l: list[Order], m: Literal["x"], n: ClassVar[Order], o: Final[Order], p: Mine[int], q: t.Self,
         r: "Optional['Order']", s: app.models.Order, u: Order | str):
    return 0
'''
    f = facts_of(src)
    P = sc(f, "anns").params
    T = TC
    want = {
        "a": T.t_nullable(T.t_name("Order")), "b": T.t_union(T.t_name("Order"), T.t_name("None")),
        "c": T.t_union(T.t_name("Order"), T.t_name("Mine")), "d": T.t_name("Order"),
        "e": T.t_classof(T.t_name("Order")), "f": T.t_classof(T.t_name("Order")), "g": T.t_name("Order"),
        "h": T.T_UNKNOWN, "i": T.T_UNKNOWN, "j": T.T_UNKNOWN, "k": T.t_nullable(T.t_name("Order")),
        "l": T.T_UNKNOWN, "m": T.T_UNKNOWN, "n": T.t_name("Order"), "o": T.t_name("Order"),
        "p": T.t_generic(T.t_name("Mine"), T.t_name("int")), "q": T.t_self(),
        "r": T.t_nullable(T.t_name("Order")), "s": T.t_qual("app", "models", "Order"),
        "u": T.t_union(T.t_name("Order"), T.t_name("str")),
    }
    bad = {k: P[k].type for k, v in want.items() if P[k].type != v}
    check("B annotations -> TypeRef: Optional / X | None / Union / Annotated / Type / type / strings / ClassVar / "
          "Final / project generics / Self / qualified; containers, Any, Literal, unparseable -> unknown", not bad, bad)
    src2 = '''\
class Optional:
    pass


def own(a: Optional[int], b: Self):
    return 0
'''
    P2 = sc(facts_of(src2), "own").params
    check("B typing names are recognised by their IMPORT, never by spelling (a project `Optional`, an unimported "
          "`Self`)", P2["a"].type == TC.t_generic(TC.t_name("Optional"), TC.t_name("int"))
          and P2["b"].type == TC.t_name("Self"), (P2["a"].type, P2["b"].type))

    # ---- classes
    src = '''\
import abc
from abc import ABC, ABCMeta
from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar, overload
from collections import namedtuple
import functools

T = TypeVar("T")


def deco(c):
    return c


class Plain(object):
    pass


class Gen(Generic[T], Plain):
    pass


class Proto(Protocol):
    pass


class Abstract(ABC, metaclass=ABCMeta):
    @abc.abstractmethod
    def run(self) -> "Plain":
        ...


class NT(namedtuple("NT", "a b")):
    pass


class GA:
    def __getattr__(self, name):
        return name


class WithMeta(metaclass=type):
    pass


@dataclass(frozen=True)
class DC:
    x: Plain
    y: int = 0


@deco
class Deco:
    pass


@functools.total_ordering
class Ordered:
    def __lt__(self, other):
        return True


class Methods:
    flag = None

    def __init__(self, dep: Plain, other):
        self.dep = dep
        self.made = Plain()
        self.ann: Plain = other
        self.pair, self.rest = other
        self.count = 0
        self.count += 1
        setattr(self, "lit", 1)
        setattr(self, other, 2)

    @property
    def prop(self) -> Plain:
        return self.made

    @prop.setter
    def prop(self, v):
        self.made = v

    @functools.cached_property
    def cprop(self) -> Plain:
        return Plain()

    @staticmethod
    def smeth(a, b):
        return a

    @classmethod
    def cmeth(cls, a) -> "Methods":
        return cls()

    async def ameth(self) -> Plain:
        return Plain()

    @deco
    def dmeth(self) -> Plain:
        return Plain()

    @functools.lru_cache()
    def cached(self, a, b=1) -> Plain:
        return Plain()

    @overload
    def ov(self, x: int) -> int: ...

    @overload
    def ov(self, x: str) -> str: ...

    def ov(self, x, *rest):
        def closure():
            self.via_closure = Plain()
        return closure


class Builder:
    @classmethod
    def install(cls, name, fn):
        setattr(cls, name, fn)


class Typer:
    def install(self, name, fn):
        setattr(type(self), name, fn)


class Later:
    def m(self):
        return 0


class LaterLit:
    def m(self):
        return 0


setattr(Later, compute_name(), 1)
setattr(LaterLit, "m", 1)
LaterLit.k = 2
del LaterLit.j
'''
    f = facts_of(src)
    check("B the class snippet is a valid FileFacts", TC.validate_facts(f) == "", TC.validate_facts(f))
    C = {c.name: c for c in f.classes}
    check("B neutral bases are dropped by their import (object, Generic[T], Protocol, ABC); others kept as written",
          C["Plain"].bases == () and C["Gen"].bases == (TC.t_name("Plain"),) and C["Proto"].bases == ()
          and C["Abstract"].bases == (), (C["Plain"].bases, C["Gen"].bases, C["Proto"].bases, C["Abstract"].bases))
    check("B a typing.Protocol class is kind 'interface' (structural: its implementers are no subclasses); the "
          "others are 'class'", C["Proto"].kind == "interface" and all(C[n].kind == "class" for n in (
              "Plain", "Gen", "Abstract", "NT", "GA")), {n: C[n].kind for n in C})
    tc_src = '''\
import typing as t
import sys
from typing import TYPE_CHECKING

if t.TYPE_CHECKING:
    class TcAttr:
        class TcNested:
            pass

if TYPE_CHECKING:
    class TcName:
        pass
else:
    class TcElse:
        pass

if TYPE_CHECKING and sys.version_info >= (3, 11):
    class TcAnd:
        pass

if not TYPE_CHECKING:
    class TcNot:
        pass

if TYPE_CHECKING or sys.platform == "win32":
    class TcOr:
        pass

try:
    if TYPE_CHECKING:
        class TcInTry:
            pass
except ImportError:
    pass


def fn():
    if TYPE_CHECKING:
        class TcLocal:
            pass
    return 0


class Real:
    if TYPE_CHECKING:
        class TcInBody:
            pass


class Plain:
    pass
'''
    tf = facts_of(tc_src)
    TK = {c.name: c.kind for c in tf.classes}
    check("B a class written under `if TYPE_CHECKING:` (typing.TYPE_CHECKING / t.TYPE_CHECKING / `TYPE_CHECKING and "
          "...`; at module level, in a try, a def or a class body; nested in such a class) is TYPING-ONLY: kind "
          "'interface' (it never exists at run time, so its own members never run and its values' run-time class "
          "is unknowable); the else branch, `if not TYPE_CHECKING:`, `TYPE_CHECKING or ...` and the rest stay 'class'",
          all(TK[n] == "interface" for n in ("TcAttr", "TcNested", "TcName", "TcAnd", "TcInTry", "TcLocal",
                                              "TcInBody"))
          and all(TK[n] == "class" for n in ("TcElse", "TcNot", "TcOr", "Real", "Plain"))
          and TC.validate_facts(tf) == "", TK)
    check("B a base that is a call (`namedtuple(...)`) is an unknown TypeRef (no typing past it)",
          C["NT"].bases == (TC.T_UNKNOWN,), C["NT"].bases)
    dyn = {n: c.dynamic for n, c in C.items()}
    check("B DYNAMIC: __getattr__, a metaclass other than ABCMeta, an unknown class decorator, setattr(cls, ...), "
          "setattr(type(self), ...), a module-level setattr(C, <not a literal>, ...)",
          all(dyn[n] for n in ("GA", "WithMeta", "Deco", "Builder", "Typer", "Later")), dyn)
    check("B NOT dynamic: ABCMeta, @dataclass(frozen=True), functools.total_ordering, setattr(self, ...) "
          "(instance data), a literal module-level setattr (a patch)",
          not any(dyn[n] for n in ("Abstract", "DC", "Ordered", "Methods", "LaterLit")), dyn)
    pt = {(p[0].name, p[1]) for p in f.patches}
    check("B patches: setattr(C, \"m\", v), C.k = v and del C.j at module level (that attribute only)",
          {("LaterLit", "m"), ("LaterLit", "k"), ("LaterLit", "j")} <= pt, pt)
    M = C["Methods"]
    fk = {n: [b.kind for b in bl] for n, bl in M.fields.items()}
    check("B R3 fields: self.f = p (field, the method's scope) / self.f = T() (call) / self.f: T (ann) / "
          "class-body f = None (none); tuple targets and augassign unknown; setattr(self, \"lit\", ...) unknown; "
          "a closure's self.f binds the class too",
          fk.get("dep") == ["field"] and fk.get("made") == ["call", "field"] and fk.get("ann") == ["ann"]
          and fk.get("flag") == ["none"] and fk.get("pair") == ["unknown"] and fk.get("rest") == ["unknown"]
          and fk.get("count") == ["field", "unknown"] and fk.get("lit") == ["unknown"]
          and fk.get("via_closure") == ["call"] and M.fields["dep"][0].scope == f.scopes.index(sc(f, "__init__")),
          fk)
    check("B DC fields: dataclass class-body annotations", [b.kind for b in C["DC"].fields["x"]] == ["ann"]
          and C["DC"].fields["x"][0].type == TC.t_name("Plain"), C["DC"].fields)
    fv = facts_of("class Desc:\n    def __get__(self, o, t):\n        return 1\n\n\nclass K:\n    d = Desc()\n"
                  "    n = 0\n    z = None\n    a = d\n")
    kf = {n: [b.kind for b in bl] for n, bl in cls_of(fv, "K").fields.items()}
    check("B a class-body VALUE is never an R3 source (`d = Desc()` may be a descriptor): unknown; `n = 0` a "
          "literal; `z = None` nullable", kf == {"d": ["unknown"], "n": ["field"], "z": ["none"], "a": ["unknown"]},
          kf)
    mf = {n: v for n, v in M.methods.items()}
    check("B method flags: property (getter + setter), cached_property, static, class, abstract",
          all("property" in m.flags for m in mf["prop"]) and len(mf["prop"]) == 2
          and "property" in mf["cprop"][0].flags and mf["smeth"][0].flags == frozenset({"static"})
          and mf["cmeth"][0].flags == frozenset({"class"}) and "abstract" in C["Abstract"].methods["run"][0].flags,
          {n: [sorted(m.flags) for m in v] for n, v in mf.items()})
    check("B returns: a property's / classmethod's / lru_cache'd annotation kept; an async def's and an unknown "
          "decorator's dropped (a coroutine / a wrapper is returned)",
          mf["prop"][0].returns == TC.t_name("Plain") and mf["cmeth"][0].returns == TC.t_name("Methods")
          and mf["cached"][0].returns == TC.t_name("Plain") and mf["ameth"][0].returns is None
          and mf["dmeth"][0].returns is None, {n: [m.returns for m in v] for n, v in mf.items()})
    check("B @overload stubs are dropped when the implementation exists (one MethodFacts at its def line)",
          [m.line for m in mf["ov"]] == [sc(f, "ov", 2).line], [m.line for m in mf["ov"]])
    check("B arity: (0, positional + 1) for an instance method (`Base.m(self, ...)` passes the instance), "
          "(0, n) static / class, (0, -1) under *args or an unknown decorator",
          mf["cached"][0].arity == (0, 3) and mf["smeth"][0].arity == (0, 2) and mf["cmeth"][0].arity == (0, 1)
          and mf["ov"][0].arity == (0, -1) and mf["dmeth"][0].arity == (0, -1),
          {n: [m.arity for m in v] for n, v in mf.items()})
    getter, setter = sc(f, "prop", 0), sc(f, "prop", 1)
    check("B same-named defs of one container list each other's lines (the engine merges them into ONE node, so "
          "the core trusts neither as a caller)", setter.line in getter.lines and getter.line in setter.lines,
          (getter.lines, setter.lines))

    # ---- sites, receivers, census, decls
    src = '''\
import os
import json as j2
from .x import helper as hp
from .y import other


def deco(*args):
    return lambda f: f


@deco()
def sites(a, *rest, d=other()):
    os.path.join(a)
    j2.dumps(a)
    "x".join(a)
    [1].append(a)
    a[0].m()
    a.b.c.m()
    f(a)(a)
    a.m(*rest)
    a.m(1, 2, k=3)
    hp(a)
    other()
    (lambda z=other(): z.q())()

    def inner(os):
        return os.m()
    return inner


def ünï(é):
    return é.m()


async def coro(x):
    return x


class K:
    async def am(self):
        return 0
'''
    f = facts_of(src)
    check("B the site snippet is a valid FileFacts", TC.validate_facts(f) == "", TC.validate_facts(f))
    s = sc(f, "sites")
    by = [(x.member, x.kind, x.recv, x.argc) for x in s.sites]
    want = [
        ("join", "member", TC.r_field(TC.r_module("os"), "path"), 1),
        ("dumps", "member", TC.r_module("j2"), 1),
        ("join", "member", TC.r_literal("str"), 1),
        ("append", "member", TC.r_literal("list"), 1),
        ("m", "member", TC.R_OTHER, 0),
        ("m", "member", TC.r_field(TC.r_field(TC.r_ident("a"), "b"), "c"), 0),
        ("f", "bare", None, 1),
        ("m", "member", TC.r_ident("a"), -1),
        ("m", "member", TC.r_ident("a"), 2),
        ("hp", "bare", None, 1),
        ("other", "bare", None, 0),
    ]
    check("B sites: bare / member, receivers (MODULE only for a module import, FIELD chains, LITERAL types, OTHER), "
          "argc (positional as written; -1 with a * argument)", by[:len(want)] == want, by)
    inner = sc(f, "inner")
    check("B a parameter shadowing a module import makes it an IDENT, never MODULE",
          inner.sites[0].recv == TC.r_ident("os"), inner.sites[0].recv)
    check("B a decorator / default argument call runs in the ENCLOSING scope (not a site of the def) but counts in "
          "the def's census (its span starts at the first decorator)",
          "deco" not in [x.member for x in s.sites] and s.all_names.get("deco") == 1
          and s.all_names.get("other") == 3, s.all_names)
    lam = next(x for x in f.scopes if x.kind == "lambda" and x.parent == f.scopes.index(s))
    check("B a lambda's default runs in the enclosing def; its body is the lambda's own scope",
          [x.member for x in lam.sites] == ["q"] and sum(1 for x in s.sites if x.member == "other") == 2,
          ([x.member for x in lam.sites], [x.member for x in s.sites]))
    check("B the census counts nested scopes: all_names[m] over the FULL span",
          s.all_names.get("m") == 5 and s.all_names.get("q") == 1, s.all_names)
    check("B a bare call through an import alias counts in the census of the IMPORTED name (`hp(...)` -> helper)",
          s.all_names.get("helper") == 1 and s.all_names.get("hp") == 1, s.all_names)
    # the walk reaches every call wherever the expression puts it (keyword / starred / subscript / slice / f-string
    # field and format spec / await / conditional / unary / binary / compare / dict / comprehension / yield)
    walk_src = '''\
async def walk(a, xs, k):
    a.m(key=k1(), other=a.n(k2()))
    a.b(k3()).c[k4()].d(*[k5()], **{"z": k6()})
    v = f"{k7()} {a.e(k8()):>{k9()}}"
    await a.f(k10())
    w = (k11() if a.g(k12()) else not k13()) + -k14() * k15()
    u = {k16(): [k17(), (k18(),)], **k19()}
    t = a.h[k20():k21()]
    s = k22() < k23() <= a.i(k24())
    q = [k26() for _ in xs if k27()]
    return k28(), a.j(k29()), v, w, u, t, s, q


def gen(a):
    r = (yield k25())
    return r
'''
    from collections import Counter as _C
    fw = facts_of(walk_src)
    ws = sc(fw, "walk")
    got_w = _C((x.member, x.kind) for x in ws.sites)
    want_w = _C([(f"k{i}", "bare") for i in range(1, 30) if i not in (25, 26, 27)]
                + [(m, "member") for m in "mnbdefgij"])
    check("B the walk keys every call of an expression (keyword, starred, subscript, slice, f-string field / format "
          "spec, await, conditional, unary, binary, compare, dict) as a site of its scope; the comprehension's own "
          "calls are its scope's; a yield's call too", got_w == want_w
          and _C(x.member for x in sc(fw, "gen").sites) == _C(["k25"])
          and sorted(x.member for x in fw.scopes[[s2.kind for s2 in fw.scopes].index("comprehension")].sites)
          == ["k26", "k27"] and ws.all_names.get("k26") == 1 and ws.all_names.get("k1") == 1,
          {"missing": got_w - want_w, "extra": want_w - got_w})
    lines = src.split("\n")
    want_decls = set()
    for i, ln in enumerate(lines, 1):
        for kw in ("def ", "class "):
            k = ln.find(kw)
            if k >= 0 and ln[:k].strip() in ("", "async"):
                want_decls.add((i, len(ln[:k + len(kw)].encode("utf-8"))))
    check("B decls: every def / async def / class NAME at its UTF-8 byte column (the textual veto skips them)",
          set(f.decls) == want_decls, (sorted(f.decls), sorted(want_decls)))
    check("B an async def's return annotation is never used (its call returns a coroutine)",
          f.functions["coro"][0].returns is None)
    deep = "def deep(a):\n    return a" + ".b" * 60 + ".m()\n"
    fd = facts_of(deep)
    check("B a 60-deep attribute chain stays a valid FileFacts (deeper receivers are OTHER)",
          TC.validate_facts(fd) == "" and fd.scopes[0].sites[0].member == "m", TC.validate_facts(fd))
    deep_ann = "def deep(a: " + "Optional[" * 40 + "X" + "]" * 40 + "):\n    return a.m()\n"
    fa = facts_of("from typing import Optional\n" + deep_ann)
    check("B a 40-deep annotation stays a valid FileFacts", TC.validate_facts(fa) == "", TC.validate_facts(fa))
    if sys.version_info >= (3, 12):
        f = facts_of("def g[T](x: T) -> T:\n    return x.m()\n\n\nclass Box[U]:\n    def get(self, y: U):\n"
                     "        return y.m()\n\n\ntype Alias = int\n")
        check("B PEP 695: type parameters are in the scope's / class's type_params; `type X = ...` binds X",
              sc(f, "g").type_params == frozenset({"T"}) and f.classes[0].type_params == frozenset({"U"})
              and any(b.kind == "unknown" for b in f.globals.get("Alias", [])), (sc(f, "g").type_params,
                                                                                f.classes[0].type_params))
        f = facts_of("class Order:\n    pass\n\n\nclass Box[Order]:\n    def put(self, x: Order) -> Order:\n"
                     "        return x.save()\n\n    class Inner:\n        y: Order\n\n"
                     "        def fill(self, z: Order):\n"
                     "            return z.save()\n\n\ndef g(x: Order) -> Order:\n    return x\n")
        put, fill, inner = sc(f, "put"), sc(f, "fill"), cls_of(f, "Inner")
        check("B PEP 695 class parameters reach the class's methods and nested classes: `class Box[Order]` -> put's / "
              "Inner.fill's type_params hold Order, put's `x: Order` / `-> Order` and Inner's `y: Order` are unknown; "
              "a module-level `g(x: Order)` is the class",
              "Order" in put.type_params and "Order" in fill.type_params and inner.type_params == {"Order"}
              and put.params["x"].type == TC.T_UNKNOWN and fill.params["z"].type == TC.T_UNKNOWN
              and cls_of(f, "Box").methods["put"][0].returns == TC.T_UNKNOWN
              and inner.fields["y"][0].type == TC.T_UNKNOWN and sc(f, "g").params["x"].type == TC.t_name("Order")
              and f.functions["g"][0].returns == TC.t_name("Order"),
              (put.type_params, fill.type_params, inner.type_params, put.params["x"], inner.fields["y"]))
        f = facts_of("class Order:\n    pass\n\n\ndef make[Order]() -> Order:\n    return 0\n")
        check("B a function's own PEP 695 parameter named like a class: its return annotation is unknown (the core "
              "reads a function's return at file level)", f.functions["make"][0].returns == TC.T_UNKNOWN,
              f.functions["make"][0].returns)
    else:
        src_tree = GX._Source(tmp())
        p = tmp() / "pep695_only.py"
        p.write_bytes(b"def g[T](x: T):\n    return x.m()\n")
        check(f"B PEP 695 on Python {sys.version_info[0]}.{sys.version_info[1]}: the file does not parse, so the "
              f"reader returns no facts (files_failed), never a wrong one",
              rd.facts("pep695_only.py", src_tree) is None)

    # ---- names the class BODY binds shadow annotations evaluated in the class namespace (review finding)
    src = '''\
from typing import Optional
from .nodes import Node, Leaf


class Tree:
    class Node:
        pass

    root: Node
    Leaf = Leaf

    def walk(self, n: Node, m: "Node", k: Optional[Node], z: Leaf, q: "app.Other") -> Node:
        def g(w: Node):
            return w
        return g

    class Sub(Node):
        pass


def outside(n: Node) -> Node:
    return n
'''
    f = facts_of(src)
    w, g, tree = sc(f, "walk"), sc(f, "g"), cls_of(f, "Tree")
    T = TC
    want = {"n": T.T_UNKNOWN, "m": T.T_UNKNOWN, "k": T.t_nullable(T.T_UNKNOWN), "z": T.T_UNKNOWN,
            "q": T.t_qual("app", "Other")}
    bad = {k: w.params[k].type for k, v in want.items() if w.params[k].type != v}
    check("B a name the class body binds (a nested class, a class attribute) is unknown in a method's signature "
          "(plain, string, Optional), its return, a class-body annotation and a nested class's base; a nested def "
          "inside the method (its own scope) and a module-level function still read the module's Node",
          not bad and tree.methods["walk"][0].returns == T.T_UNKNOWN and tree.fields["root"][0].type == T.T_UNKNOWN
          and cls_of(f, "Sub").bases == (T.T_UNKNOWN,) and g.params["w"].type == T.t_name("Node")
          and sc(f, "outside").params["n"].type == T.t_name("Node")
          and f.functions["outside"][0].returns == T.t_name("Node"),
          (bad, tree.methods["walk"][0].returns, tree.fields["root"], cls_of(f, "Sub").bases, g.params["w"]))

    # ---- an attribute assigned through the same variable is never read back typed (review finding)
    src = '''\
class Order:
    def save(self):
        return 1


def fake():
    return 0


shared = Order()
shared.save = fake


def f():
    o = Order()
    p = Order()
    o.save = fake
    setattr(p, "total", fake)
    o.save()
    o.total()
    p.total()
    p.save()
    o.save.__call__()
    alias = o.save

    def inner():
        return o.save()

    def other():
        o = Order()
        return o.save()
    return alias, inner, other


def g():
    return shared.save()


def h(shared):
    return shared.save()
'''
    f = facts_of(src)
    by = [(x.member, x.recv) for x in sc(f, "f").sites if x.kind == "member"]
    want = [("save", TC.R_OTHER), ("total", TC.r_ident("o")), ("total", TC.R_OTHER), ("save", TC.r_ident("p")),
            ("__call__", TC.R_OTHER)]
    check("B `o.save = fake` / `setattr(p, \"total\", f)`: `o.save()`, `p.total()` and `o.save.__call__()` get an "
          "OTHER receiver (never typed); `o.total()` / `p.save()` (other attributes) stay IDENT; `alias = o.save` is "
          "unknown", by[:len(want)] == want and sc(f, "f").locals["alias"][0].kind == "unknown",
          (by, sc(f, "f").locals.get("alias")))
    check("B ...a closure reading the same variable is OTHER too; a nested def's own `o` is another variable",
          sc(f, "inner").sites[0].recv == TC.R_OTHER and sc(f, "other").sites[-1].recv == TC.r_ident("o"),
          (sc(f, "inner").sites, sc(f, "other").sites))
    check("B a module-level `shared.save = fake`: `shared.save()` in a function is OTHER; a parameter named shared "
          "is another variable", sc(f, "g").sites[0].recv == TC.R_OTHER
          and sc(f, "h").sites[0].recv == TC.r_ident("shared"), (sc(f, "g").sites, sc(f, "h").sites))

    # ---- Phase A cost: string annotations are parsed once per text (a plain dotted name never), the cyclic GC is
    # paused only while a file is read
    parsed = []
    orig = ast.parse

    def counting(src_, *a, **k):
        if k.get("mode") == "eval":
            parsed.append(src_)
        return orig(src_, *a, **k)
    params = ", ".join(f"a{i}: \"Optional['A']\", b{i}: \"A\", c{i}: \"app.models.A\"" for i in range(30))
    src = f"from typing import Optional\n\n\nclass A:\n    pass\n\n\ndef many({params}):\n    return 0\n"
    ast.parse = counting
    try:
        f = facts_of(src)
    finally:
        ast.parse = orig
    P = sc(f, "many").params
    check("B 90 string annotations: one parse for the 30 equal `Optional['A']`, none for plain dotted names; the "
          "same TypeRefs as a full parse", parsed == ["Optional['A']"]
          and all(P[f"a{i}"].type == TC.t_nullable(TC.t_name("A")) and P[f"b{i}"].type == TC.t_name("A")
                  and P[f"c{i}"].type == TC.t_qual("app", "models", "A") for i in range(30)), parsed)
    import gc  # noqa: WPS433
    was = gc.isenabled()
    try:
        gc.enable()
        facts_of("x = 1\n")
        on = gc.isenabled()
        gc.disable()
        facts_of("x = 1\n")
        off = not gc.isenabled()
    finally:
        (gc.enable if was else gc.disable)()
    check("B file_facts restores the GC state it found (enabled stays enabled, disabled stays disabled)", on and off,
          (on, off))


# ================================================================================================
# C / D / E: the fixture trees end to end, both engines' captures, default and PRECISE
# ================================================================================================

def _m(f, n):
    return f"{f}::{n}"


MODELS, REPO_PY = "app/models.py", "app/repo.py"
EXPECT = {
    "shapes": {
        _m(MODELS, "Order.__init__"): {_m(MODELS, "Ledger"): "EXTRACTED"},
        _m(MODELS, "Order.create"): {_m(MODELS, "Order.blank"): "EXTRACTED"},
        _m(MODELS, "Order.r3_ctor_field"): {_m(MODELS, "Ledger.post"): "EXTRACTED"},
        _m(MODELS, "Order.r3_injected_field"): {_m(REPO_PY, "Repo.save"): "INFERRED"},
        _m(MODELS, "Order.r3_class_annotation"): {_m(REPO_PY, "Repo.find"): "INFERRED"},
        _m(MODELS, "Order.r3_property"): {_m(MODELS, "Ledger.post"): "INFERRED"},
        _m(MODELS, "Order.s_self"): {_m(MODELS, "Order.total"): "EXTRACTED"},
        # (Order.s_closure: `self.total()` inside a nested DEF — graphify attributes a nested def's calls to no
        # caller, so the core keys nothing there (ATTRIBUTION, slice S3a); the reader's SELF inside it is section B's,
        # and the attributed lambda / comprehension forms are the review tree's Ledger.s_lambda / s_comprehension)
        _m(MODELS, "Special.total"): {_m(MODELS, "Order.total"): "EXTRACTED"},
        _m(MODELS, "Special.s_inherited"): {_m(MODELS, "Order.clone"): "EXTRACTED"},
        _m(MODELS, "Special2.total"): {_m(MODELS, "Order.total"): "EXTRACTED"},
        _m(MODELS, "Cart.r3_dataclass_field"): {_m(MODELS, "Ledger.post"): "EXTRACTED"},
        _m(MODELS, "r1_param"): {_m(MODELS, "Order.total"): "EXTRACTED"},
        _m(MODELS, "r1_string_annotation"): {_m(MODELS, "Ledger.post"): "EXTRACTED"},
        _m(MODELS, "r1_optional"): {_m(MODELS, "Ledger.post"): "EXTRACTED"},
        _m(MODELS, "r1_pipe_none"): {_m(MODELS, "Ledger.post"): "EXTRACTED"},
        _m(MODELS, "r1_annotated"): {_m(MODELS, "Ledger.post"): "EXTRACTED"},
        _m(MODELS, "r1_imported"): {_m(REPO_PY, "Repo.save"): "INFERRED"},
        _m(MODELS, "r1_overloaded"): {_m(MODELS, "Conv.to"): "EXTRACTED"},
        _m(MODELS, "r2_local_ctor"): {_m(MODELS, "Ledger"): "EXTRACTED", _m(MODELS, "Ledger.post"): "EXTRACTED"},
        _m(MODELS, "r2_annotated_local"): {_m(MODELS, "Ledger.post"): "EXTRACTED"},
        _m(MODELS, "r2_alias"): {_m(MODELS, "Ledger"): "EXTRACTED", _m(MODELS, "Ledger.post"): "EXTRACTED"},
        _m(MODELS, "r2_none_then_ctor"): {_m(MODELS, "Ledger"): "EXTRACTED",
                                          _m(MODELS, "Ledger.post"): "EXTRACTED"},
        _m(MODELS, "r2_cast"): {_m(MODELS, "Ledger.post"): "EXTRACTED"},
        _m(MODELS, "r2_return_call"): {_m(REPO_PY, "Repo.save"): "INFERRED"},
        _m(MODELS, "r4_chain"): {_m(REPO_PY, "Repo.save"): "INFERRED"},
        _m(MODELS, "r4_method_chain"): {_m(MODELS, "Order.clone"): "EXTRACTED", _m(MODELS, "Order.total"): "INFERRED"},
        _m(MODELS, "k_class_method"): {_m(MODELS, "Order.create"): "EXTRACTED"},
        _m(MODELS, "k_unbound_call"): {_m(MODELS, "Ledger.post"): "EXTRACTED"},
        _m(MODELS, "m_alias_module"): {_m(REPO_PY, "make_repo"): "INFERRED"},
        _m(MODELS, "m_from_package_module"): {_m(REPO_PY, "make_repo"): "INFERRED"},
        _m(MODELS, "m_dotted_package"): {_m(REPO_PY, "make_repo"): "INFERRED"},
        _m(MODELS, "c_same_file"): {_m(MODELS, "Ledger"): "EXTRACTED"},
        _m(MODELS, "c_imported"): {_m(REPO_PY, "Repo"): "INFERRED"},
        _m(MODELS, "c_module_ctor"): {_m(REPO_PY, "Repo"): "INFERRED"},
        _m(MODELS, "c_nested_class"): {_m(MODELS, "Inner"): "EXTRACTED"},
        _m(REPO_PY, "make_repo"): {_m(REPO_PY, "Repo"): "EXTRACTED"},
    },
    "mustnot": {
        **{_m("app/core.py", n): {_m("app/core.py", "Order"): "EXTRACTED"}
           for n in ("c_python_order", "make", "fetch", "wrapped", "mn_rebind", "mn_del", "mn_walrus", "mn_global",
                     "mn_nonlocal", "mn_comprehension_shadow", "mn_for", "mn_with", "mn_except", "mn_tuple",
                     "mn_augassign", "mn_local_import", "mn_nested_def_name", "mn_match_capture",
                     "TwoClasses.__init__", "WithProp.save", "UsesDescriptor.__init__")},
        _m("app/core.py", "Descriptor.__get__"): {_m("app/core.py", "Other"): "EXTRACTED"},
        _m("app/console.py", "ThreadedStream.push"): {_m("app/console.py", "HTMLStringO"): "EXTRACTED"},
        _m("app/core.py", "UsesDescriptor.r3_class_none_then_self"): {_m("app/core.py", "Order.create"): "EXTRACTED"},
        _m("app/core.py", "TwoClasses.reset"): {_m("app/core.py", "Other"): "EXTRACTED"},
        _m("app/core.py", "typed_sibling"): {_m("app/core.py", "Order"): "EXTRACTED",
                                             _m("app/core.py", "Order.save"): "EXTRACTED"},
        _m("app/core.py", "cand_if_else"): {_m("app/core.py", "Order"): "EXTRACTED",
                                            _m("app/core.py", "Other"): "EXTRACTED",
                                            _m("app/core.py", "Order.save"): "AMBIGUOUS",
                                            _m("app/core.py", "Other.save"): "AMBIGUOUS"},
        _m("app/core.py", "cand_union"): {_m("app/core.py", "Order.save"): "AMBIGUOUS",
                                          _m("app/core.py", "Other.save"): "AMBIGUOUS"},
        _m("app/core.py", "patched_other_attr"): {_m("app/core.py", "Patched.keep"): "EXTRACTED"},
        _m("app/core.py", "setstate_still_typed"): {_m("app/core.py", "Restored.save"): "EXTRACTED"},
        _m("app/pep695.py", "pep695_sibling"): {_m("app/core.py", "Order.save"): "INFERRED"},
        _m("app/pick.py", "pick_b"): {_m("b/repo.py", "Repo.find"): "INFERRED"},
        _m("app/audit.py", "Map.add"): {_m("app/audit.py", "Map.bind"): "EXTRACTED"},
        _m("app/audit.py", "Notifier.uses_helper"): {_m("app/audit.py", "Notifier.helper"): "EXTRACTED"},
    },
    "requests": {
        _m("tests/test_requests.py", "test_send"): {_m("src/requests/sessions.py", "Session.send"): "INFERRED"},
        _m("tests/test_requests.py", "test_redirect"): {
            _m("tests/test_requests.py", "RedirectSession"): "EXTRACTED",
            _m("tests/test_requests.py", "RedirectSession.send"): "EXTRACTED"},
        _m("src/requests/sessions.py", "Session.request"): {
            _m("src/requests/sessions.py", "Session.send"): "EXTRACTED"},
    },
    "flask": {
        _m("src/flask/sansio/app.py", "App.handle"): {
            _m("src/flask/sansio/scaffold.py", "Scaffold._check"): "INFERRED"},
        _m("src/flask/app.py", "Flask.full_dispatch_request"): {
            _m("src/flask/sansio/app.py", "App.ensure_sync"): "INFERRED"},
        _m("src/flask/ctx.py", "AppContext.push"): {_m("src/flask/sansio/app.py", "App.ensure_sync"): "INFERRED"},
    },
    "roots": {
        _m("scripts/run.py", "main"): {_m("scripts/helper.py", "assist"): "INFERRED"},
        _m("tools/main.py", "main"): {_m("tools/lr.py", "run"): "INFERRED"},
        _m("libs/core/corepkg/user.py", "use_core"): {_m("libs/core/corepkg/util.py", "run"): "INFERRED"},
        _m("pkg/ns_user.py", "use_ns"): {_m("nsp/inner/mod.py", "deep"): "INFERRED"},
        _m("pkg/rel.py", "use_rel"): {_m("pkg/b.py", "assist"): "INFERRED"},
        _m("pkg/rel.py", "use_rel_only"): {_m("pkg/b.py", "assist"): "INFERRED"},
        _m("pkg/rel.py", "mn_alias_same_name"): {_m("pkg/b.py", "assist"): "INFERRED"},
    },
    "review": {
        _m("app/monkey.py", "instance_patched_other_attr"): {_m("app/monkey.py", "Order"): "EXTRACTED",
                                                             _m("app/monkey.py", "Order.total"): "EXTRACTED"},
        _m("app/monkey.py", "mn_instance_patched"): {_m("app/monkey.py", "Order"): "EXTRACTED"},
        _m("app/monkey.py", "mn_setattr_patched"): {_m("app/monkey.py", "Order"): "EXTRACTED"},
        _m("app/monkey.py", "positive_sibling"): {_m("app/monkey.py", "Order"): "EXTRACTED",
                                                  _m("app/monkey.py", "Order.save"): "EXTRACTED"},
        _m("app/pep695cls.py", "Box.via_return"): {_m("app/pep695cls.py", "Box.first"): "EXTRACTED"},
        _m("app/pep695cls.py", "pep695_cls_sibling"): {_m("app/pep695cls.py", "Order.save"): "EXTRACTED"},
        _m("app/selfpkg/__init__.py", "use_in_init"): {_m("app/selfpkg/sub.py", "run"): "INFERRED"},
        _m("app/use_selfpkg.py", "via_package"): {_m("app/selfpkg/sub.py", "run"): "INFERRED"},
        _m("app/use_selfpkg.py", "via_from"): {_m("app/selfpkg/sub.py", "run"): "INFERRED"},
        _m("app/tree.py", "Tree.chain"): {_m("app/tree.py", "Tree.make"): "EXTRACTED"},
        _m("app/tree.py", "Tree.make"): {_m("app/tree.py", "Node"): "EXTRACTED"},       # `Tree.Node()`: the nested one
        _m("app/tree.py", "outside"): {_m("app/nodes.py", "Node.visit"): "INFERRED"},
        _m("app/closure.py", "Ledger.s_lambda"): {_m("app/closure.py", "Ledger.total"): "EXTRACTED"},
        _m("app/closure.py", "Ledger.s_comprehension"): {_m("app/closure.py", "Ledger.total"): "EXTRACTED"},
    },
    "dispatch": {
        _m("app/dispatch.py", "leaf_sibling"): {_m("app/dispatch.py", "Leaf.m"): "EXTRACTED"},
        _m("app/dispatch.py", "Holder.__init__"): {_m("app/dispatch.py", "Session"): "EXTRACTED"},
        _m("app/dispatch.py", "SubHolder.__init__"): {_m("app/dispatch.py", "OtherSession"): "EXTRACTED"},
        # dynamic dispatch (the core keeps a project override reachable): the static target at its typed grade, and
        # the override the engine's removed name fan-out reached re-emitted as a key at AMBIGUOUS (never upgraded)
        _m("app/dispatch.py", "mn_override_param"): {_m("app/dispatch.py", "Base.m"): "EXTRACTED",
                                                     _m("app/dispatch.py", "Sub.m"): "AMBIGUOUS"},
        _m("app/dispatch.py", "Mixin.save"): {_m("app/dispatch.py", "Root.save"): "EXTRACTED"},
        # (Holder.mn_field_rebound_in_subclass: SubHolder rebinds the field to another class -> unknown, untyped)
        _m("app/cmds.py", "Context.to_info_dict"): {_m("app/cmds.py", "Command.to_info_dict"): "INFERRED",
                                                    _m("app/cmds.py", "Group.to_info_dict"): "AMBIGUOUS"},
        _m("app/cmds.py", "Parameter.info_dict"): {_m("app/kinds.py", "ParamType.to_info_dict"): "INFERRED",
                                                   _m("app/kinds.py", "Choice.to_info_dict"): "AMBIGUOUS",
                                                   _m("app/kinds.py", "Path.to_info_dict"): "AMBIGUOUS"},
        _m("app/cmds.py", "complete_visible_commands"): {
            _m("app/cmds.py", "Group.get_command"): "EXTRACTED", _m("app/cmds.py", "Group.list_commands"): "EXTRACTED",
            _m("app/cmds.py", "CommandCollection.get_command"): "AMBIGUOUS",
            _m("app/cmds.py", "CommandCollection.list_commands"): "AMBIGUOUS"},
        _m("app/cmds.py", "made_exact"): {_m("app/cmds.py", "Group"): "EXTRACTED",
                                          _m("app/cmds.py", "Group.get_command"): "EXTRACTED"},
        # (use_finder: `f: Finder` — a typing.Protocol — reaches the Protocol's own declared `find`, a member that
        # never runs (its structural implementers need not inherit it): no typed key, the engine's tuples stay; the
        # phase-2 verification, after fastapi's `orjson = cast(_OrjsonModule, ...)` and flask's ProxyMixin stubs)
    },
    "review2": {
        _m("app/nest.py", "own"): {_m("app/exc.py", "Abort"): "INFERRED"},      # the def's OWN call: typed as usual
        _m("app/star_first.py", "use_named_last"): {_m("app/models.py", "Order"): "INFERRED",
                                                   _m("app/models.py", "Order.save"): "INFERRED"},
        _m("app/jar.py", "Jar.copy"): {_m("app/jar.py", "Jar"): "EXTRACTED"},
        # (render: `js.dumps()` on a Protocol-typed module object runs the module's function, never `_Json.dumps`: no
        # typed key — a must-not below)
    },
    "proxy": {
        # flask's LocalProxy globals: the Protocol stub `_get_current_object` and a typing-only class's own member
        # get no key (must-nots below); the REAL class the typing-only proxy inherits from still resolves, INFERRED
        # (another file; a typing-only receiver is an interface: never EXTRACTED, its dispatch reach unknowable)
        _m("src/flask/helpers.py", "push_ctx"): {_m("src/flask/ctx.py", "AppContext.push"): "INFERRED"},
        # the positive sibling of `if TYPE_CHECKING: class Hidden`: a class under `if not TYPE_CHECKING:` is real
        _m("src/flask/typing_only.py", "use_visible"): {_m("src/flask/typing_only.py", "Visible.run"): "EXTRACTED"},
    },
}
# callers whose C comparison is left to another section (none since the core keeps overrides: section O and the
# exact EXPECT entries above both lock the dispatch tree)
EXPECT_OPEN: dict = {}

_CORE = "app/core.py"
# (tree, caller, member, kind, prior): kind "none" = no typed key for the member AND no removal of its tuples;
# "keep" = no removal (a typed key is fine). prior = the engines' captures hold a tuple of it (the no-removal
# check has teeth there).
MUSTNOT = [("mustnot", _m(_CORE, n), m, "none", True) for n, m in (
    ("mn_rebind", "save"), ("mn_del", "save"), ("mn_for", "save"), ("mn_with", "acquire"), ("mn_except", "save"),
    ("mn_walrus", "save"), ("mn_global", "save"), ("mn_nonlocal", "save"), ("mn_tuple", "save"),
    ("mn_augassign", "save"), ("mn_local_import", "save"), ("mn_nested_def_name", "save"),
    ("mn_match_capture", "save"), ("mn_comprehension_shadow", "save"), ("mn_any", "save"),
    ("mn_union_str", "save"), ("mn_pipe_str", "save"), ("mn_union_any", "save"), ("mn_list", "append"),
    ("mn_typevar", "save"), ("mn_unparseable", "save"), ("mn_unannotated", "save"), ("mn_class_alias", "create"),
    ("mn_dunder", "__enter__"), ("Holder.mn_field_callable", "cb"), ("Holder.mn_untyped_injection", "save"),
    ("TwoClasses.mn_field_two_classes", "save"), ("mn_property_called", "save"), ("mn_getattr_class", "save"),
    ("mn_metaclass", "save"), ("mn_decorated_class", "save"), ("mn_patched", "save"),
    ("ExtBase.mn_super_external", "save"), ("mn_setattr_on_class", "save"), ("GetSet.value", "compute"),
    ("Rebinder.mn_self_rebound", "save"), ("mn_async_chain", "save"), ("mn_decorated_return", "save"))] + [
    ("mustnot", _m(_CORE, "mn_path_loaded"), "main", "none", False),
    ("mustnot", _m("app/maybe.py", "mn_type_checking_else"), "save", "none", False),
    ("mustnot", _m("app/tryimp.py", "mn_try_two_classes"), "save", "none", False),
    ("mustnot", _m("app/tvar.py", "mn_typevar_named_like_class"), "save", "none", False),
    ("mustnot", _m("app/pep695.py", "mn_pep695"), "save", "none", False),
    ("mustnot", _m("app/no_order.py", "mn_ts_order"), "Order", "none", False),
    ("mustnot", _m("app/audit.py", "audit_untyped"), "notify", "none", True),
    ("requests", _m("src/requests/sessions.py", "SessionRedirectMixin.resolve_redirects"), "send", "none", True),
    ("flask", _m("src/flask/json/provider.py", "DefaultJSONProvider.dumps"), "dumps", "none", False),
    ("flask", _m("src/flask/json/provider.py", "DefaultJSONProvider.loads"), "loads", "none", False),
    ("flask", _m("src/flask/json/tag.py", "TaggedJSONSerializer.dumps"), "dumps", "none", False),
    ("flask", _m("src/flask/json/tag.py", "TaggedJSONSerializer.loads"), "loads", "none", False),
    ("roots", _m("pkg/a.py", "use_b"), "assist", "none", False),
    ("roots", _m("roots_user.py", "use_shared"), "thing", "none", False),
    ("roots", _m("pkg/rel.py", "mn_alias_same_name"), "assist", "keep", True),
    ("mustnot", _m("app/jar.py", "mn_external_base_override"), "set_cookie", "none", True),
    ("mustnot", _m("app/jar.py", "mn_builtin_base_override"), "get", "none", True),
    ("mustnot", _m(_CORE, "UsesDescriptor.mn_descriptor_field"), "save", "none", True),
    ("mustnot", _m("app/console.py", "mn_patched_external_module"), "_write", "none", True),
    # review findings: an external base re-exported through a project module (named and `compat.X`); a class-level
    # PEP 695 parameter named like a class; a name the class BODY binds (a nested class) in a method signature /
    # class-body annotation / nested base; an attribute assigned through the same variable
    ("review", _m("app/jar.py", "mn_reexported_base_override"), "set_cookie", "none", True),
    ("review", _m("app/odict.py", "mn_qualified_reexport_override"), "move_to_end", "none", True),
    ("review", _m("app/pep695cls.py", "Box.put"), "save", "none", True),
    ("review", _m("app/pep695cls.py", "Box.via_return"), "save", "none", True),
    ("review", _m("app/pep695cls.py", "Inner.fill"), "save", "none", True),
    ("review", _m("app/tree.py", "Tree.walk"), "visit", "none", True),
    ("review", _m("app/tree.py", "Tree.walk_field"), "visit", "none", True),
    ("review", _m("app/tree.py", "Tree.chain"), "visit", "none", True),
    ("review", _m("app/tree.py", "Leaf.grow"), "visit", "none", True),
    ("review", _m("app/monkey.py", "mn_instance_patched"), "save", "none", True),
    ("review", _m("app/monkey.py", "mn_setattr_patched"), "save", "none", True),
    ("review", _m("app/monkey.py", "mn_module_instance_patched"), "save", "none", True),
    # the phase-2 review: graphify's symbol pass gave each undecorated top-level def the bare call of an imported class
    # inside its nested def / nested def's default / local class's method / lambda in a nested def (both engines)
    ("review2", _m("app/nest.py", "outer"), "Abort", "none", True),
    ("review2", _m("app/nest.py", "outer_async"), "AsyncAbort", "none", True),
    ("review2", _m("app/nest.py", "outer_default"), "DefaultAbort", "none", True),
    ("review2", _m("app/nest.py", "outer_local_cls"), "LocalAbort", "none", True),
    ("review2", _m("app/nest.py", "outer_lam"), "LamAbort", "none", True),
    # a star import after the named import may rebind Order: never typed, the engine's tuple stays
    ("review2", _m("app/star.py", "use_star"), "Order", "none", True),
    ("review2", _m("app/star.py", "use_star"), "save", "none", False),
    # a member reached through getattr / methodcaller / attrgetter: the engine's `copy` tuple stays
    ("review2", _m("app/jar.py", "copy_jar"), "copy", "none", True),
    ("review2", _m("app/jar.py", "copy_by_caller"), "copy", "none", True),
    ("review2", _m("app/jar.py", "copy_by_getter"), "copy", "none", True),
    # a Protocol receiver: its own member never runs -> no typed key, and the engine's tuples stay (fastapi's
    # `orjson = cast(_OrjsonModule, import_module("orjson")); orjson.dumps()`, `f: Finder; f.find()`)
    ("review2", _m("app/proto.py", "render"), "dumps", "none", True),
    ("dispatch", _m("app/proto.py", "use_finder"), "find", "none", True),
    # the phase-2 verification (flask @36e4a82): `app_ctx: AppContextProxy = LocalProxy(...)` with AppContextProxy /
    # ProxyMixin(Protocol) written under `if t.TYPE_CHECKING:` -> `app_ctx._get_current_object()` gets no typed key
    # (8 FALSE flask edges); a typing-only class's own member neither (`session: SessionStub`; `h: Hidden`, whose
    # engine tuple also stays: its run-time class is unknowable, so no REPLACE)
    ("proxy", _m("src/flask/helpers.py", "flash"), "_get_current_object", "none", False),
    ("proxy", _m("src/flask/helpers.py", "read_session"), "get_value", "none", False),
    ("proxy", _m("src/flask/typing_only.py", "use_hidden"), "run", "none", True),
]
_PEP695_OK = sys.version_info >= (3, 12)
if not _PEP695_OK:      # a PEP 695 file does not parse before 3.12: the reader returns no facts for it
    EXPECT["mustnot"].pop(_m("app/pep695.py", "pep695_sibling"))
    for _k in [k for k in EXPECT["review"] if k.startswith("app/pep695cls.py::")]:
        EXPECT["review"].pop(_k)


def sec_shapes(buckets) -> None:
    print("\n-- C: every design 3.1 shape gives the exact key and grade, end to end (both engines, default + "
          "PRECISE) --")
    for b in buckets:
        for tree in TREES:
            for mode in MODES:
                run = run_for(b, tree, mode)
                st = run.stats
                n_py = len([r for r in TREES[tree] if r.endswith(".py")])
                n_bad = 0 if _PEP695_OK else sum(1 for t, _r in PEP695_FILES if t == tree)
                check(f"C {b}/{tree} [{mode}]: every Python file read ({n_py - n_bad}), "
                      f"{n_bad} unparseable here, no error",
                      st["error"] is None and st["files_failed"] == n_bad and st["files"] == n_py - n_bad
                      and "python" not in st["langs_disabled"],
                      (st["error"], st["files_failed"], st["files"], st["langs_disabled"]))
                got = typed_view(run.nodes, form="name")
                want = EXPECT[tree]
                open_ = EXPECT_OPEN.get(tree, set())
                diff = {k: (got.get(k), want.get(k)) for k in sorted(set(got) | set(want))
                        if got.get(k) != want.get(k) and k not in open_}
                check(f"C {b}/{tree} [{mode}]: the typed keys and grades are exactly the expected ones "
                      f"({len(want)} callers)", not diff, diff)
                grades = {g for v in got.values() for g in v.values()}
                check(f"C {b}/{tree} [{mode}]: only the three grade strings",
                      grades <= {"EXTRACTED", "INFERRED", "AMBIGUOUS"}, grades)
                bad = [(c, str(t)) for c, t in run.out if GX.is_call_key(t) and t not in {n["key"] for n in run.nodes}]
                check(f"C {b}/{tree} [{mode}]: every keyed callee names a node of the graph", not bad, bad[:5])
    if buckets:
        run = run_for(buckets[0], "shapes", "default")
        c = next((n for n in run.nodes if ident(n) == _m(MODELS, "r1_classof")), None)
        check("C (core rule, slice S3a) `k: type[Order]; k.create()` is NOT typed: typed_calls never types a "
              "parameter bound to a class object (the reader emits classof(Order))",
              c is not None and not c.get("call_typed"), c and c.get("call_typed"))


def sec_mustnot(buckets) -> None:
    print("\n-- D: every design 5 Python must-not shape: no typed edge AND no removal (both engines, both modes) --")
    for b in buckets:
        for mode in MODES:
            missing_prior = []
            for tree, caller, member, kind, prior in MUSTNOT:
                run = run_for(b, tree, mode)
                ck = key_of(run.nodes, caller)
                if ck is None:
                    check(f"D {b} [{mode}] {caller}: the caller is a node", False)
                    continue
                node = next(n for n in run.nodes if n["key"] == ck)
                typed = [t for t in (node.get("call_typed") or {})
                         if TC.name_seg(next((n.get("name") for n in run.nodes if n["key"] == t), "")) == member]
                pre = tuples_of(run, run.before, caller, member)
                post = tuples_of(run, run.out, caller, member)
                lost = [t for t in pre if t not in post]
                if kind == "none":
                    check(f"D {b} [{mode}] {caller} `.{member}()`: no typed key, no removal", not typed and not lost,
                          (typed, lost))
                else:
                    check(f"D {b} [{mode}] {caller} `{member}`: no removal (a typed key may be added)", not lost, lost)
                if prior and not pre:
                    missing_prior.append(caller)
            check(f"D {b} [{mode}]: every must-not marked `prior` has an engine tuple of its member (the check has "
                  f"teeth)", not missing_prior, missing_prior)


_D = "app/dispatch.py"
_K = "app/cmds.py"
_KI = "app/kinds.py"
# (caller, member, a project OVERRIDE the static target has): after link the caller's tuples of `member` must still
# REACH the override (a kept engine tuple whose name fans out to it, or a key to it) — dynamic dispatch reaches it at
# run time. The engine's name fan-out reaches it in both captures (checked: the check has teeth). Review finding 1:
# `r: Base; r.m()` beside a Sub.m override; a field a subclass rebinds (`self.session = OtherSession()` in
# SubHolder); click's shapes (a field injected through an annotated parameter, a class-annotated field whose
# subclasses live in another file, a `t.cast(Group, ...)` local) — the click corpus lost 7 such production edges
# before the core kept overrides. (The fixture's mixin diamond — `super().save()` in Mixin reaches Other.save inside
# Model(Mixin, Other) — has no check here: DeepMap's super() re-bind already narrows the engine's tuple to Root.save
# before link runs; the core gate locks the next-definer rule.)
DISPATCH_KEEP = [
    (_m(_D, "mn_override_param"), "m", _m(_D, "Sub.m")),
    (_m(_D, "Holder.mn_field_rebound_in_subclass"), "get", _m(_D, "OtherSession.get")),
    (_m(_K, "Context.to_info_dict"), "to_info_dict", _m(_K, "Group.to_info_dict")),
    (_m(_K, "Parameter.info_dict"), "to_info_dict", _m(_KI, "Choice.to_info_dict")),
    (_m(_K, "Parameter.info_dict"), "to_info_dict", _m(_KI, "Path.to_info_dict")),
    (_m(_K, "complete_visible_commands"), "get_command", _m(_K, "CommandCollection.get_command")),
    (_m(_K, "complete_visible_commands"), "list_commands", _m(_K, "CommandCollection.list_commands")),
    (_m("app/proto.py", "use_finder"), "find", _m("app/proto.py", "SqlFinder.find")),     # a Protocol's implementer
]
# (caller, member, a same-named method dispatch can NOT reach): the engine's fan-out reached it before link and the
# REPLACE removes it — an UNRELATED class's method, an ancestor's the static target shadows, a subclass's override
# behind a CONSTRUCTED receiver (exactly that class), a Command override behind a ParamType receiver
DISPATCH_GONE = [
    (_m(_D, "mn_override_param"), "m", _m(_D, "Leaf.m")),
    (_m(_K, "Context.to_info_dict"), "to_info_dict", _m(_KI, "Unrelated.to_info_dict")),
    (_m(_K, "Context.to_info_dict"), "to_info_dict", _m(_K, "Trailer.to_info_dict")),
    (_m(_K, "Context.to_info_dict"), "to_info_dict", _m(_KI, "Choice.to_info_dict")),
    (_m(_K, "Parameter.info_dict"), "to_info_dict", _m(_KI, "Unrelated.to_info_dict")),
    (_m(_K, "Parameter.info_dict"), "to_info_dict", _m(_K, "Group.to_info_dict")),
    (_m(_K, "complete_visible_commands"), "get_command", _m(_K, "Lookalike.get_command")),
    (_m(_K, "complete_visible_commands"), "get_command", _m(_K, "Command.get_command")),
    (_m(_K, "made_exact"), "get_command", _m(_K, "CommandCollection.get_command")),
]


def reach(run: Run, calls, caller_ref: str, member: str) -> set:
    """`file::name` of every node the caller's tuples of `member` in `calls` can bind at the push (a key: itself;
    a name: its fan-out through the push's name index)."""
    import edge_resolve as ER  # noqa: WPS433
    ck = key_of(run.nodes, caller_ref)
    idx = ER.build_name_index([{"key": n["key"], "name": n.get("name")} for n in run.nodes])
    ids = {n["key"]: ident(n) for n in run.nodes}
    name_of = {n["key"]: n.get("name") for n in run.nodes}
    out = set()
    for c, t in calls:
        if c != ck or not TC.callee_matches(t, member, name_of, TC._is_call_key):
            continue
        keys = [str(t)] if GX.is_call_key(t) else [k for k in idx.get(t, ()) if k != ck]
        out.update(ids.get(k, k) for k in keys)
    return out


def sec_dispatch(buckets) -> None:
    print("\n-- O: dynamic dispatch: a project override of the static target stays reachable, an unrelated same-named "
          "method does not (review finding 1; the core) --")
    for b in buckets:
        for mode in MODES:
            run = run_for(b, "dispatch", mode)
            ids = {n["key"]: ident(n) for n in run.nodes}
            for caller, member, override in DISPATCH_KEEP:
                pre = reach(run, run.before, caller, member)
                post = reach(run, run.out, caller, member)
                check(f"O {b} [{mode}] {caller}: the engine's `{member}` tuple reaches the override {override} "
                      f"before link (the check has teeth)", override in pre, sorted(pre))
                check(f"O {b} [{mode}] {caller} `.{member}()`: the override {override} stays reachable after link",
                      override in post, sorted(post))
                ck = key_of(run.nodes, caller)
                node = next((n for n in run.nodes if n["key"] == ck), {})
                got = {ids.get(t, t): g for t, g in (node.get("call_typed") or {}).items()}
                was_key = any(GX.is_call_key(t) and ids.get(str(t)) == override for c, t in run.before if c == ck)
                check(f"O {b} [{mode}] {caller}: a re-emitted override carries AMBIGUOUS (a fan-out's grade, never "
                      f"upgraded); a kept engine key, no typed grade", got.get(override) in (
                          (None,) if was_key else ("AMBIGUOUS", None)), got.get(override))
            for caller, member, gone in DISPATCH_GONE:
                pre = reach(run, run.before, caller, member)
                post = reach(run, run.out, caller, member)
                check(f"O {b} [{mode}] {caller} `.{member}()`: {gone} (dispatch cannot reach it) was reached before "
                      f"link and is removed by the REPLACE", gone in pre and gone not in post,
                      (sorted(pre), sorted(post)))
            post = reach(run, run.out, _m(_D, "leaf_sibling"), "m")
            check(f"O {b} [{mode}] positive sibling: `r: Leaf; r.m()` (no override anywhere) ends on Leaf.m only",
                  post == {_m(_D, "Leaf.m")}, sorted(post))
            st = run.stats
            check(f"O {b} [{mode}] the stats count the re-emitted overrides (override_keyed); the Protocol "
                  f"receiver's site (typed to the Protocol's own member: it never runs) is unknown for the merge "
                  f"(skipped_interface 1), so its group falls back before any dispatch judgement (veto dispatch 0)",
                  st.get("override_keyed", 0) >= 6 and st.get("skipped_interface") == 1
                  and (st.get("vetoes") or {}).get("dispatch", 0) == 0,
                  (st.get("override_keyed"), st.get("skipped_interface"), st.get("vetoes")))
            pre = tuples_of(run, run.before, _m("app/proto.py", "use_finder"), "find")
            post = tuples_of(run, run.out, _m("app/proto.py", "use_finder"), "find")
            check(f"O {b} [{mode}] `f: Finder` (a typing.Protocol): the engine's `find` tuples all stay (no REPLACE: "
                  f"a structural implementer may be the runtime class)", pre and all(t in post for t in pre),
                  (pre, post))


# (caller, member, a node the engine's tuple reached that the REPLACE must remove, modes where the engine reaches it)
_R2 = "app/nest.py"
REV2_GONE = [
    (_m("app/jar.py", "plain_copy"), "copy", _m("app/jar.py", "Jar.copy"), ("default", "precise")),
    (_m("app/star_first.py", "use_named_last"), "Order", _m("app/other.py", "Order"), ("default",)),
]


def sec_review2(buckets) -> None:
    print("\n-- W: the phase-2 review — graphify's symbol pass (nested bare calls of imported names), a star import "
          "after a binding, getattr / methodcaller / attrgetter, a Protocol receiver's grade --")
    for b in buckets:
        for mode in MODES:
            run = run_for(b, "review2", mode)
            for caller, member, gone, modes in REV2_GONE:
                if mode not in modes:
                    continue
                pre = reach(run, run.before, caller, member)
                post = reach(run, run.out, caller, member)
                check(f"W {b} [{mode}] positive sibling {caller} `{member}`: {gone} (reached before link) is removed "
                      f"by the REPLACE", gone in pre and gone not in post, (sorted(pre), sorted(post)))
            for caller, member in ((_m(_R2, "outer_deco"), "DecoAbort"), (_m(_R2, "Holder.meth"), "MethAbort")):
                pre = tuples_of(run, run.before, caller, member)
                ck = key_of(run.nodes, caller)
                node = next((n for n in run.nodes if n["key"] == ck), {})
                check(f"W {b} [{mode}] {caller}: graphify walks no decorated def / method (no engine tuple of "
                      f"`{member}`), and the core keys nothing from its nested def", ck is not None and not pre
                      and not node.get("call_typed"), (ck, pre, node.get("call_typed")))
            pre = tuples_of(run, run.before, _m(_R2, "own"), "Abort")
            post = tuples_of(run, run.out, _m(_R2, "own"), "Abort")
            check(f"W {b} [{mode}] positive sibling: the def's OWN `raise Abort()` is typed — the engine's tuple ends "
                  f"as exactly the typed key", pre and len(post) == 1 and post[0][1], (pre, post))
            st = run.stats
            check(f"W {b} [{mode}] stats: the five walked defs' groups fall back (veto nested) and the three indirect "
                  f"`copy` callers (veto census)", st["vetoes"]["nested"] == 5 and st["vetoes"]["census"] == 3,
                  st["vetoes"])
            pre = tuples_of(run, run.before, _m("app/proto.py", "render"), "dumps")
            post = tuples_of(run, run.out, _m("app/proto.py", "render"), "dumps")
            check(f"W {b} [{mode}] a Protocol-typed module object (`js = cast(_Json, import_module(...)); "
                  f"js.dumps()`): no typed key to the Protocol's stub, the engine's `dumps` tuples stay exactly "
                  f"(skipped_interface 1)", pre and post == pre and st.get("skipped_interface") == 1,
                  (pre, post, st.get("skipped_interface")))


def sec_cases(buckets) -> None:
    print("\n-- E: requests, flask, LR.run, import roots --")
    T = "tests/test_requests.py"
    for b in buckets:
        for mode in MODES:
            run = run_for(b, "requests", mode)
            send = key_of(run.nodes, _m("src/requests/sessions.py", "Session.send"))
            post = tuples_of(run, run.out, _m(T, "test_send"), "send")
            pre = tuples_of(run, run.before, _m(T, "test_send"), "send")
            check(f"E {b} [{mode}] requests: the test caller's `s.send()` had the engine's in-file pick "
                  f"(RedirectSession.send) and ends on Session.send ONLY (a REPLACE: corrections are allowed for "
                  f"a test caller)", pre and post == [(send, True)], (pre, post))
            with clean_env(DEEPMAP_PRECISE_CALLS="1"):
                edges = S._resolve_calls(run.nodes, list(run.out))
            ck = key_of(run.nodes, _m(T, "test_send"))
            name_of = {n["key"]: n.get("name") for n in run.nodes}
            to_send = {t: g for (c, t), g in edges.items() if c == ck and TC.name_seg(name_of.get(t, "")) == "send"}
            check(f"E {b} [{mode}] requests: the push (keys honoured) gives test_send exactly one `send` edge, to "
                  f"Session.send", set(to_send) == {send}, to_send)
            fl = run_for(b, "flask", mode)
            for c in ("src/flask/json/provider.py::DefaultJSONProvider.dumps",
                      "src/flask/json/tag.py::TaggedJSONSerializer.loads"):
                n = next(x for x in fl.nodes if ident(x) == c)
                check(f"E {b} [{mode}] flask: `import json; json.{c.rsplit('.', 1)[-1]}()` inside src/flask/json/ "
                      f"gets no typed key (stdlib json, not flask.json)", not n.get("call_typed"), n.get("call_typed"))
            mn = run_for(b, "mustnot", mode)
            ext = [(m, tuples_of(mn, mn.before, _m(_CORE, "ext_calls"), m),
                    tuples_of(mn, mn.out, _m(_CORE, "ext_calls"), m)) for m in ("search", "join")]
            node = next(n for n in mn.nodes if ident(n) == _m(_CORE, "ext_calls"))
            check(f"E {b} [{mode}] `re.search()` / `os.path.join()` are EXTERNAL: the engine's false "
                  f"Order.search / Order.join tuples are removed and nothing is typed",
                  all(pre and not post for _m_, pre, post in ext) and not node.get("call_typed"), ext)
            pre = tuples_of(mn, mn.before, _m("app/jar.py", "ext_module_still_external"), "search")
            post = tuples_of(mn, mn.out, _m("app/jar.py", "ext_module_still_external"), "search")
            check(f"E {b} [{mode}] ...and `re.search()` stays external beside project classes that extend OTHER "
                  f"external classes (only those become unknown)", pre and not post, (pre, post))
            rv = run_for(b, "review", mode)
            pre = tuples_of(rv, rv.before, _m("app/jar.py", "ext_not_extended_stays_external"), "append")
            post = tuples_of(rv, rv.out, _m("app/jar.py", "ext_not_extended_stays_external"), "append")
            check(f"E {b} [{mode}] ...and `q: deque; q.append()` stays external although collections.OrderedDict is "
                  f"extended through a re-export (the engine's in-file Bag.append pick is removed)", pre and not post,
                  (pre, post))
            ro = run_for(b, "roots", mode)
            lr = key_of(ro.nodes, _m("tools/lr.py", "run"))
            post = tuples_of(ro, ro.out, _m("tools/main.py", "main"), "run")
            pre = tuples_of(ro, ro.before, _m("tools/main.py", "main"), "run")
            check(f"E {b} [{mode}] `import tools.lr as LR; LR.run(root)` converges on the module function (the "
                  f"engine's LR.run / class-method picks replaced)", pre and post == [(lr, True)], (pre, post))
    if len(buckets) == 2:
        for mode in MODES:
            a = [tuples_of(run_for(b, "roots", mode), run_for(b, "roots", mode).out, _m("tools/main.py", "main"), "run")
                 for b in buckets]
            st = [[stable(run_for(b, "roots", mode).nodes).get(t[0]) for t in x] for b, x in zip(buckets, a)]
            check(f"E [{mode}] LR.run: both engines end on the SAME tuple (version-stable key)", st[0] == st[1], st)


def sec_parity(buckets) -> None:
    print("\n-- F: engine parity, and the ast path gives the same typed set --")
    if len(buckets) == 2:
        for tree in TREES:
            for mode in MODES:
                a = typed_view(run_for(buckets[0], tree, mode).nodes)
                b = typed_view(run_for(buckets[1], tree, mode).nodes)
                diff = {k: (a.get(k), b.get(k)) for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)}
                check(f"F {tree} [{mode}]: identical typed tuples on version-stable keys, 0.8.39 vs 0.8.51", not diff,
                      diff)
    else:
        check("F both engines' captures are present (tests/fixtures/typed_calls/{0839,0851}/py)", False, buckets)
    if not buckets:
        return
    # the caller policy (tests / non-production: corrections only) depends on the ENGINE's prior tuples, which
    # the two extractors do not share; the comparison therefore lets every caller add (the typing is compared)
    every = dict(MODES["default"], DEEPMAP_TYPED_CALLS_TESTS="1", DEEPMAP_TYPED_CALLS_NONPROD="1")
    for tree in TREES:
        ast_run = run_pipeline(None, root_for(tree), every, ast_path=True)
        g_run = run_pipeline(captured(buckets[0], tree)["graph"], root_for(tree), every)
        gfy = typed_view(g_run.nodes, form="name")
        av = typed_view(ast_run.nodes, form="name")
        gnames = {ident(n) for n in g_run.nodes}
        same = {k: (gfy.get(k), av.get(k)) for k in gfy if gfy.get(k) != av.get(k)}
        extra = sorted(k for k in av if k not in gfy and k in gnames)
        check(f"F {tree}: the ast path (DEEPMAP_EXTRACTOR=ast) types every graphify caller identically", not same,
              same)
        check(f"F {tree}: ...and adds only nested defs (nodes graphify does not make)", not extra, extra)


def sec_flags(buckets) -> None:
    print("\n-- G: flags off, other languages only, the push passes after link --")
    if not buckets:
        return
    b = buckets[0]
    for tree in TREES:
        run = run_pipeline(captured(b, tree)["graph"], root_for(tree), _OFF)
        check(f"G {tree}: DEEPMAP_TYPED_CALLS=0 and DEEPMAP_CALLS_AUDIT=0 -> the SAME list object, no file read, no "
              f"node attribute", run.out is run.calls_in and run.stats["files"] == 0
              and not any(n.get("call_typed") or n.get("call_weak") for n in run.nodes), run.stats["files"])
        run = run_pipeline(captured(b, tree)["graph"], root_for(tree), dict(_OFF, DEEPMAP_TYPED_CALLS="typescript"))
        py_keyed = [n["key"] for n in run.nodes if str(n.get("source_file", "")).endswith(".py")
                    and (n.get("call_typed") or n.get("call_weak"))]
        same = [(c, str(t), GX.is_call_key(t)) for c, t in run.out] == [(c, str(t), GX.is_call_key(t))
                                                                       for c, t in run.before]
        check(f"G {tree}: TYPED on for typescript only -> no Python caller keyed, the CALLS unchanged",
              not py_keyed and same, py_keyed[:5])
        for mode in MODES:
            run = run_pipeline(captured(b, tree)["graph"], root_for(tree), MODES[mode])
            calls = run.out
            with clean_env(**MODES[mode]):
                S._attach_bare_calls(root_for(tree), run.nodes, calls)
                S._attach_local_defs(root_for(tree), run.nodes, calls)
                S._attach_local_def_calls(root_for(tree), run.nodes, calls)
            typed_pairs = {(n["key"], t) for n in run.nodes for t in (n.get("call_typed") or {})}
            kept = {(c, str(t)) for c, t in calls if GX.is_call_key(t)}
            check(f"G {tree} [{mode}]: after _attach_bare_calls / _attach_local_defs / _attach_local_def_calls every "
                  f"typed tuple is still there as a CallKey", typed_pairs <= kept, sorted(typed_pairs - kept)[:5])
            with clean_env(DEEPMAP_PRECISE_CALLS="1"):
                edges = S._resolve_calls(run.nodes, list(calls))
            miss = sorted(p for p in typed_pairs if p not in edges)
            check(f"G {tree} [{mode}]: _resolve_calls with keys honoured binds every typed key to exactly its node",
                  not miss, miss[:5])


AUDIT_WEAK = {
    _m("app/audit.py", "audit_untyped"): [_m("app/audit.py", "Notifier.notify")],
    _m(_CORE, "Rebinder.mn_self_rebound"): [_m(_CORE, "Rebinder.save")],
    # (the engine's own in-file pick for these two is the save defined last before them: Descriptor.save)
    _m(_CORE, "mn_async_chain"): [_m(_CORE, "Descriptor.save")],
    _m(_CORE, "mn_decorated_return"): [_m(_CORE, "Descriptor.save")],
}
AUDIT_WEAK_EXTERNAL = {
    _m(_CORE, "ext_calls"): [_m(_CORE, "Order.search")],
    _m("app/jar.py", "ext_module_still_external"): [_m("app/jar.py", "Finder.search")],
}


def sec_audit(buckets) -> None:
    print("\n-- H: the audit (DEEPMAP_CALLS_AUDIT) on real Python receivers --")
    for b in buckets:
        for name, env in (("audit-only", dict(_OFF, DEEPMAP_CALLS_AUDIT="python")),
                          ("audit-only PRECISE", dict(_OFF, DEEPMAP_CALLS_AUDIT="python", DEEPMAP_PRECISE_CALLS="1")),
                          ("TYPED + audit PRECISE", dict(_OFF, DEEPMAP_CALLS_AUDIT="python",
                                                         DEEPMAP_TYPED_CALLS="python", DEEPMAP_PRECISE_CALLS="1"))):
            run = run_pipeline(captured(b, "mustnot")["graph"], root_for("mustnot"), env)
            wv = weak_view(run.nodes)
            want = dict(AUDIT_WEAK)
            if "TYPED" not in name:      # without TYPED the engine's `re.search()` picks stay -> demoted, not removed
                want.update(AUDIT_WEAK_EXTERNAL)
            check(f"H {b} [{name}]: the engine's in-file picks on UNTYPED receivers are demoted (`nt.notify()` among "
                  f"two repo-wide notify methods; `self = other; self.save()`; `fetch().save()` of an async def; "
                  f"`wrapped().save()` through an unknown decorator; audit-only: `re.search()` on a module)",
                  wv == want, wv)
            check(f"H {b} [{name}]: a SELF site (`self.helper(); b.helper()`) and a class-object site "
                  f"(`Map.bind(self, x)`) are never demoted",
                  _m("app/audit.py", "Notifier.uses_helper") not in wv and _m("app/audit.py", "Map.add") not in wv)
            if "TYPED" not in name:
                check(f"H {b} [{name}]: audit-only emits and removes nothing (the SAME list)", run.out is run.calls_in)


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
# M: mutation teeth (each rule broken in a copy of the module -> its checks go RED)
# ================================================================================================

MUTANTS = [
    ("receiver_facts_py", "a typing.Protocol is a plain class (its structural implementers' edges get removed)",
     ['                kind="interface" if protocol or id(n) in typing_only else "class", bases=tuple(bases),'],
     ['                kind="interface" if id(n) in typing_only else "class", bases=tuple(bases),'],
     ("reader", "dispatch")),
    # the phase-2 verification: flask's `if t.TYPE_CHECKING: class AppContextProxy(ProxyMixin[...], AppContext)`
    ("receiver_facts_py", "a class written under `if TYPE_CHECKING:` is a plain class (its own members get typed "
     "keys: `session.get_value()`, `h.run()` REPLACEs the engine's tuple)",
     ['                kind="interface" if protocol or id(n) in typing_only else "class", bases=tuple(bases),'],
     ['                kind="interface" if protocol else "class", bases=tuple(bases),'], ("reader", "mustnot")),
    ("receiver_facts_py", "an `if TYPE_CHECKING:` ELSE branch is read as typing-only too",
     ['                    stack.append((st.orelse, inside))\n'],
     ['                    stack.append((st.orelse, inside or _is_type_checking(st.test)))\n'], ("reader",)),
    ("receiver_facts_py", "`TYPE_CHECKING and ...` is not read as TYPE_CHECKING",
     ['    if isinstance(e, ast.BoolOp) and isinstance(e.op, ast.And):\n'],
     ['    if False:\n'], ("reader",)),
    ("py_import_resolver", "the importer's own directory is a root even INSIDE a package",
     ["        if d in self.base_roots or self.is_package(d):\n            return self.base_roots\n"],
     ["        if d in self.base_roots:\n            return self.base_roots\n"], ("resolver", "mustnot")),
    ("py_import_resolver", "an unresolved absolute import falls back to a SUFFIX match (the old resolver)",
     ["            hits = [h for h in (self._at(_join(r, *parts)) for r in self.roots(importer)) if h]\n"],
     ["            hits = [h for h in (self._at(_join(r, *parts)) for r in self.roots(importer)) if h]\n"
      "            if not hits:\n"
      "                hits = [('file', f) for f in sorted(self.files)\n"
      "                        if f.endswith('/' + '/'.join(parts) + '/__init__.py')][:1]\n"], ("resolver", "mustnot")),
    ("py_import_resolver", "two different regular matches pick the first instead of ambiguous",
     ["        if len(regular) > 1:\n            return T_AMBIGUOUS\n"], [""], ("resolver", "mustnot")),
    ("receiver_facts_py", "a for / with / except / augassign / tuple / walrus / match target is NOT poisoned",
     ["            elif kind == \"unknown\":\n                self.scopes[ctx.scope].poisoned.add(target.id)\n",
      "        self.scopes[si].poisoned.update(names)\n"],
     ["            elif kind == \"unknown\":\n                pass\n", "        pass\n"], ("reader", "mustnot")),
    ("receiver_facts_py", "a local import does not poison its name",
     ["        self._poison(ctx, [imp.local for imp in entries if imp.local != \"*\"])"],
     ["        pass"], ("mustnot",)),
    ("receiver_facts_py", "Union[...] takes its first member",
     ["                    return T.t_union(*[self.ann(x, depth + 1, hide) or T.T_UNKNOWN for x in elts])\n"],
     ["                    return self.ann(elts[0], depth + 1, hide)\n"], ("reader", "mustnot")),
    ("receiver_facts_py", "a typing container List[X] is read as X",
     ["                return T.T_UNKNOWN                            "
      "# List / Dict / Callable / Literal / Iterator ...\n"],
     ["                return self.ann(elts[-1], depth + 1)\n"], ("reader", "mustnot")),
    ("receiver_facts_py", "an unknown class decorator does not make the class dynamic",
     ["            if o not in _OK_CLASS_DECORATORS:\n                c.dynamic = True\n"],
     ["            pass\n"], ("reader", "mustnot")),
    ("receiver_facts_py", "setattr(cls, ...) does not make the class dynamic",
     ["                owner = self.classes[cls_idx]            # `setattr(cls, ...)` in a classmethod\n"],
     ["                owner = None\n"], ("reader", "mustnot")),
    ("receiver_facts_py", "an async def keeps its return annotation",
     ["                    ret = None if (is_async or not transparent or node.returns is None) \\\n",
      "                ret = None if (is_async or not transparent or node.returns is None) \\\n"],
     ["                    ret = None if (not transparent or node.returns is None) \\\n",
      "                ret = None if (node.returns is None) \\\n"], ("reader", "mustnot")),
    ("receiver_facts_py", "a property is not flagged",
     ["            if is_prop:\n                flags.add(\"property\")          "
      "# its return annotation IS the attribute's type (R3)\n"],
     ["            if False:\n                pass\n"], ("reader", "mustnot")),
    ("receiver_facts_py", "an aliased bare call does not count in the imported name's census",
     ["        self._alias_census()\n"], ["        pass\n"], ("reader", "mustnot")),
    ("receiver_facts_py", "same-named defs do not share their lines (getter / setter trusted as callers)",
     ["            extra.update(self._same_name_lines(defs))\n"], ["            pass\n"], ("reader", "mustnot")),
    ("receiver_facts_py", "SUPER is emitted in any scope (a nested def's super())",
     ["        if not sc.is_method or not self._self_valid(sc) or call.keywords:\n"],
     ["        if call.keywords:\n"], ("reader",)),
    ("receiver_facts_py", "an external / builtin class a project class extends is still a closed external type",
     ["        self.extended, self.open_names = extended_externals(facts, self.roots)\n"],
     ["        self.extended, self.open_names = frozenset(), frozenset()\n"], ("resolver", "mustnot")),
    ("receiver_facts_py", "an external module project code ASSIGNS attributes of (`sys.stdout = s`) stays closed",
     ["        p_pairs, self.patched_mods = patched_externals(facts)\n"],
     ["        p_pairs, self.patched_mods = frozenset(), frozenset()\n"], ("resolver", "mustnot")),
    ("receiver_facts_py", "a class-body VALUE (`value = Descriptor()`) is an R3 source",
     ["                    kind = \"classvalue\"      # design 3.1 R3 reads class-body ANNOTATIONS, not values "
      "(descriptors)\n"], ["                    pass\n"], ("reader", "mustnot")),
    ("receiver_facts_py", "a module import shadowed by a parameter is still MODULE",
     ["        if self._bound_in_chain(si, name) or name in self.other_bound or name in self.from_origin:\n"],
     ["        if name in self.other_bound or name in self.from_origin:\n"], ("reader",)),
    # review findings
    ("receiver_facts_py", "names the class body binds do not shadow class-namespace annotations (nested class Node)",
     ["        if hide:\n            head = e.value if isinstance(e, ast.Subscript) else e\n"],
     ["        if False:\n            head = e.value if isinstance(e, ast.Subscript) else e\n"], ("reader", "mustnot")),
    ("receiver_facts_py", "an external base re-exported through a project module is not followed",
     ["        if tgt.kind in (\"file\", \"namespace\"):\n"
      "            return self._attr(tgt, (imp.name,) + rest, depth, seen, importer=rel)\n"],
     ["        if tgt.kind in (\"file\", \"namespace\"):\n            return frozenset(), True\n"],
     ("resolver", "mustnot")),
    ("receiver_facts_py", "a base that cannot be followed does not open its name",
     ["                        or self._open_module(dotted + \".\" + imp.name) or imp.name in self.open_names:\n"],
     ["                        or self._open_module(dotted + \".\" + imp.name):\n"], ("resolver",)),
    ("receiver_facts_py", "`from . import sub` in the package's own __init__ binds the importer's symbol",
     ["            if tgt.kind == \"file\" and tgt.rel == _norm_rel(rel):\n"], ["            if False:\n"],
     ("resolver", "shapes")),
    ("receiver_facts_py", "an attribute assigned through the same variable is read back typed (`o.save = f; "
     "o.save()`)", ["        if attr not in self._lpatch_attrs:\n            return False\n"],
     ["        if True:\n            return False\n"], ("reader", "mustnot")),
    ("receiver_facts_py", "string annotations are parsed again for every occurrence",
     ["        if text not in self._str_ann:\n"], ["        if True:\n"], ("reader",)),
    # the phase-2 review
    ("receiver_facts_py", "a member reached through getattr / methodcaller / attrgetter is not counted in the census "
     "(requests' `getattr(jar, \"copy\")()` edge is REPLACEd away)",
     ["            if nm in _INDIRECT and ctx.span:\n"], ["            if False:\n"], ("mustnot", "review2")),
    ("receiver_facts_py", "the walk's fast path drops a Call's keyword arguments",
     ["                for k in reversed(x.keywords):\n                    push(k)\n"],
     ["                for k in ():\n                    push(k)\n"], ("reader",)),
    ("receiver_facts_py", "the walk's fast path drops an Attribute's value",
     ["                if v is not None and tv is not _NAME and tv is not _CONST:\n                    push(v)\n"],
     ["                if False:\n                    push(v)\n"], ("reader", "shapes")),
]
if _PEP695_OK:     # the class-level PEP 695 shapes only parse on 3.12+
    MUTANTS.append(
        ("receiver_facts_py", "a class's PEP 695 parameters do not reach its methods / nested classes",
         ["        c.type_params = _type_param_names(node) | (self.classes[ctx.klass].type_params if ctx.klass >= 0\n"
          "                                                   else frozenset())\n"],
         ["        c.type_params = frozenset()\n"], ("reader", "mustnot")))


def _run_sections(names, buckets):
    _RUNS.clear()
    for n in names:
        {"resolver": sec_resolver, "reader": sec_reader, "shapes": lambda: sec_shapes(buckets),
         "mustnot": lambda: sec_mustnot(buckets), "roots": lambda: sec_cases(buckets),
         "dispatch": lambda: sec_dispatch(buckets), "review2": lambda: sec_review2(buckets)}[n]()
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
    try:
        yield mod
    finally:
        if saved is not None:
            sys.modules[modname] = saved
        else:
            sys.modules.pop(modname, None)


def sec_mutations(buckets) -> None:
    print("\n-- M: mutation teeth (each rule broken in a copy of the module -> its checks go RED) --")
    for modname, desc, olds, news, sections in MUTANTS:
        try:
            with mutant(modname, olds, news):
                with recording(Rec(loud=False)) as rec:
                    _run_sections(sections, buckets)
        except AssertionError as exc:
            check(f"M {modname}: {desc} -> the mutation applies", False, exc)
            continue
        check(f"M {modname}: {desc} -> RED ({rec.failed} failing check(s))", rec.failed > 0,
              f"{rec.passed} passed, 0 failed")
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
    with clean_env(DEEPMAP_HOME=str(home)):
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
                          f"tests/typed_calls_py_gates.TREES[{name!r}], written by extract_with_report (absolute "
                          "paths made root-relative; map_graph reads none of them); re-capture with "
                          "`python tests/typed_calls_py_gates.py --capture` under this engine."),
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
                for c, t in sorted(typed_view(run.nodes).items()):
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
        print(f"typed_calls_py_gates (S3b): Python {sys.version.split()[0]}, graphify "
              f"{GX.graphify_version() if _HAS_ENGINE else '(not installed)'}, build {BUILD}")
        if TC is None or RF is None or PIR is None:
            check("the S3b modules import (typed_calls, receiver_facts_py, py_import_resolver)", False, _IMPORT_ERROR)
        buckets = buckets_present()
        check("both engines' captures of every tree are present (tests/fixtures/typed_calls/{0839,0851}/py)",
              buckets == ["0839", "0851"], buckets)
        if TC is not None:
            sec_resolver()
            sec_reader()
            sec_shapes(buckets)
            sec_mustnot(buckets)
            sec_dispatch(buckets)
            sec_review2(buckets)
            sec_cases(buckets)
            sec_parity(buckets)
            sec_flags(buckets)
            sec_audit(buckets)
            sec_real_engine()
            if RF is not None and PIR is not None:
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
