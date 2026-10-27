#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fix round r283 (ISS-build:015 / D23, and D25) — `.connect(h)` / `.on(evt, h)` TRIGGERS registrations get the
variable guard; the Python/Ruby recognizers state no stale typed-edge count.

Finding (DeepInit refresh 2026-09-29, reproduced end to end): the round-7 `_callable_target` guard — a bare name that
is a VARIABLE (a parameter, a local, a module-level non-def assignment) names no symbol of its own, so it yields no
edge — was applied to APScheduler `add_job` and the requests/httpx hooks, but NOT to the signal registrations
`.connect(x)` / `.on(evt, x)`, which took the argument's trailing identifier verbatim. `.connect` also matched ANY
receiver, so `def open_db(path): return sqlite3.connect(path)` became `TRIGGERS db -> path`, which name-resolves to an
unrelated `Request.path` graded INFERRED — and TRIGGERS is in the default `deepmap_impact` walk.

The rule now (build/framework_edges.py):
  A  a `.connect` / `.on` handler goes through `_callable_target` like every other registration: a parameter / local /
     loop variable / module variable / call result / partial(...) / nested def / literal / lambda -> NO edge;
  B  a MODULE's `.connect(...)` (a name bound by `import sqlite3` / `import psycopg2 as pg`, or the dotted path of
     `import mysql.connector`, wherever the import sits) is PEP 249's connection constructor, never a signal
     registration -> NO edge, whatever the argument (`settings.DB_PATH`, `self.dsn`, ...);
  C  a PROPERTY (@property / @cached_property / @x.setter ...) is read as a VALUE, never the callable a registration
     wires -> NO edge for any registration kind (it used to mint an EXTRACTED HOOKS edge to the getter, and routing
     `.connect(self.prop)` through the guard would otherwise have upgraded that false edge INFERRED -> EXTRACTED);
  C2 an attribute the class itself ASSIGNS (`self.address = ...`, a class-level `address = ...`, a dataclass field)
     is a variable as well -> NO edge (`result.connect(self.address)`, `self.ftp.connect(self.host, ...)` — the
     false shapes the stdlib / tornado keep after A-C); an own method stays LOCAL, an inherited one (never
     assigned by the class — Qt's `self.close`) a bare NAME;
  D  true signal registrations stay (Django / blinker / Flask / PyDispatcher / Qt / pyee shapes, incl. every shape
     tests/framework_edges_gates.py and tests/typed_edges_r7_gates.py lock), now graded like the siblings: a
     same-module def or an own-class method -> LOCAL (`self.m` -> `Class.m`); an import / `mod.f` / an inherited
     `self.m` -> NAME;
  E  end to end through edge_resolve: the validator's repro persists no TRIGGERS row; a same-file handler grades
     EXTRACTED;
  F  the registry (what sync runs) == the legacy shell, and it carries the fix;
  G  D25: a "<N> typed edges" phrase in the Python / Ruby recognizers must state len(ALLOWED_EDGE_TYPES) (it said 12
     while there are 13).

Hermetic: pure AST + in-memory resolution; section F writes only a TemporaryDirectory. No Neo4j, no network, no
~/.deepmap, no env left changed.
Usage: python tests/o8_trigger_guard_r283_gates.py [--root <tree>]
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
PASSES: list = []
FAILS: list = []


def check(name: str, ok, detail="") -> None:
    ok = bool(ok)
    (PASSES if ok else FAILS).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok or not detail else f" — {str(detail)[:700]}"))


def _load(root: Path):
    """The recognizer modules of `root` (a working tree or the pre-fix snapshot)."""
    sys.path.insert(0, str(root / "build"))
    import framework_edges as FE          # noqa: E402
    import edge_resolve as ER             # noqa: E402
    import language_recognizers as LR     # noqa: E402
    return FE, ER, LR


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


def _edges(FE, src: str, rel: str = "db.py"):
    return FE.edges_from_module(ast.parse(src), rel)


def _typed(FE, src: str, rel: str = "db.py", types=("TRIGGERS",)):
    return [e for e in _edges(FE, src, rel) if e.edge_type in types]


def _tuples(edges):
    return sorted((e.src_name, e.dst_name, e.conf_hint, tuple(sorted((e.attrs or {}).items()))) for e in edges)


def _node(key, label, name, sf, line=1):
    return {"key": key, "label": label, "name": name, "source_file": sf, "start_line": line,
            "documented": False, "body_summary": f"{name}: x"}


# =====================================================================================================
# A — the variable guard on .connect / .on
# =====================================================================================================
A_CASES = (
    ("the validator's repro: a PARAMETER handed to a DB connect — def open_db(path): sqlite3.connect(path)",
     "import sqlite3\ndef open_db(path):\n    return sqlite3.connect(path)\n"),
    ("the validator's second repro: a PARAMETER handler — def wire(ee, cb): ee.on('evt', cb)",
     "def wire(ee, cb):\n    ee.on('evt', cb)\n"),
    ("a PARAMETER handler on a signal — def attach(sig, handler): sig.connect(handler)",
     "def attach(sig, handler):\n    sig.connect(handler)\n"),
    ("a LOCAL assignment — cb = make(); ee.on('ready', cb)",
     "def boot():\n    cb = make()\n    ee.on('ready', cb)\n"),
    ("a LOOP variable — for h in handlers: sig.connect(h)",
     "def wire_all(handlers):\n    for h in handlers:\n        sig.connect(h)\n"),
    ("a MODULE-level variable on a non-module receiver — DB = 'x.db'; client.connect(DB)",
     "DB = 'x.db'\nconn = client.connect(DB)\n"),
    ("a module-level partial — handler = partial(on_evt, 1); sig.connect(handler)",
     "def on_evt(x): pass\nhandler = partial(on_evt, 1)\nsig.connect(handler)\n"),
    ("a CALL RESULT — sig.connect(make_handler()) / ee.on('evt', build()) (the returned closure is not the factory)",
     "sig.connect(make_handler())\nee.on('evt', build())\n"),
    ("an inline partial(...) — sig.connect(partial(on_evt, 1)) names functools.partial, not a handler",
     "def on_evt(x): pass\nsig.connect(partial(on_evt, 1))\n"),
    ("a NESTED def (never node-ified) — the flask test-suite shape flask.template_rendered.connect(record, app)",
     "import flask\ndef test_render(app):\n    def record(sender, **kw): pass\n    flask.template_rendered.connect(record, app)\n"),
    ("a literal / tuple / lambda / starred argument",
     "sock.connect(('h', 80))\nclient.connect('broker', 1883)\nsig.connect(lambda s: None)\nsig.connect(*hs)\n"
     "ee.on('e', lambda: 1)\n"),
)


def gate_a_variable_guard(FE) -> None:
    print("\n-- A: a .connect / .on handler that is a VARIABLE, a call result or a literal names no symbol --")
    for label, src in A_CASES:
        got = _typed(FE, src)
        check(f"{label} -> no TRIGGERS edge", not got, _tuples(got))


# =====================================================================================================
# B — a MODULE's .connect(...) is PEP 249's connection constructor
# =====================================================================================================
B_CASES = (
    ("import sqlite3; sqlite3.connect(settings.DB_PATH) (an attribute argument the variable guard keeps)",
     "import sqlite3\nconn = sqlite3.connect(settings.DB_PATH)\n"),
    ("import psycopg2 as pg; pg.connect(self.dsn) in a method (an import alias + an instance attribute)",
     "import psycopg2 as pg\nclass Db:\n    def open(self):\n        return pg.connect(self.dsn)\n"),
    ("import mysql.connector; mysql.connector.connect(cfg.host) (the dotted path of the import)",
     "import mysql.connector\nconn = mysql.connector.connect(cfg.host)\n"),
    ("a function-level import — def open_db(): import sqlite3; return sqlite3.connect(DB_FILE)",
     "from config import DB_FILE\ndef open_db():\n    import sqlite3\n    return sqlite3.connect(DB_FILE)\n"),
    ("an import placed AFTER the def that uses it (order-independent)",
     "def open_db():\n    return duckdb.connect(settings.path)\nimport duckdb\n"),
    ("an async driver — await asyncpg.connect(self.dsn)",
     "import asyncpg\nclass Pool:\n    async def go(self):\n        return await asyncpg.connect(self.dsn)\n"),
)


def gate_b_module_receiver(FE) -> None:
    print("\n-- B: <module>.connect(...) (sqlite3 / psycopg2 / mysql.connector ...) is never a signal registration --")
    for label, src in B_CASES:
        got = _typed(FE, src)
        check(f"{label} -> no TRIGGERS edge", not got, _tuples(got))


# =====================================================================================================
# C — a property is a value, never a registered callable (every registration kind)
# =====================================================================================================
C_CASES = (
    ("sock.connect(self.address) where address is a @property (graphify node-ifies the getter as a :Method)",
     "class Conn:\n    @property\n    def address(self): return ('h', 1)\n"
     "    def open(self, sock):\n        sock.connect(self.address)\n", ("TRIGGERS",)),
    ("ee.on('evt', self.handler) where handler is a @functools.cached_property",
     "import functools\nclass Bus:\n    @functools.cached_property\n    def handler(self): return print\n"
     "    def wire(self, ee):\n        ee.on('evt', self.handler)\n", ("TRIGGERS",)),
    ("r.register_hook('response', self.cb) where cb is a @property (the sibling HOOKS path)",
     "class Auth:\n    @property\n    def cb(self): return None\n"
     "    def __call__(self, r):\n        r.register_hook('response', self.cb)\n", ("HOOKS",)),
    ("sched.add_job(self.job) where job is a @cached_property (the sibling SCHEDULED_AS path)",
     "class W:\n    @cached_property\n    def job(self): return None\n"
     "    def start(self):\n        self.sched.add_job(self.job, 'interval')\n", ("SCHEDULED_AS",)),
    ("hooks={'response': Klass.p} where p is a module-level class's property (+ its @p.setter)",
     "class Klass:\n    @property\n    def p(self): return 1\n    @p.setter\n    def p(self, v): pass\n"
     "requests.get('u', hooks={'response': Klass.p})\n", ("HOOKS",)),
)


def gate_c_property(FE) -> None:
    print("\n-- C: a PROPERTY is read as a value -> no registration edge of any kind --")
    for label, src, types in C_CASES:
        got = _typed(FE, src, types=types)
        check(f"{label} -> no {'/'.join(types)} edge", not got, _tuples(got))
    ctl = _typed(FE, "class W:\n    @property\n    def p(self): return 1\n    def tick(self): pass\n"
                     "    def start(self):\n        self.sched.add_job(self.tick, 'interval')\n"
                     "        r.register_hook('response', self.tick)\n        self.clicked.connect(self.tick)\n",
                 types=("TRIGGERS", "HOOKS", "SCHEDULED_AS"))
    check("control: a PLAIN method beside a property still binds Class.m, LOCAL, for all three kinds",
          sorted((e.edge_type, e.dst_name, e.conf_hint) for e in ctl)
          == [("HOOKS", "W.tick", "LOCAL"), ("SCHEDULED_AS", "W.tick", "LOCAL"), ("TRIGGERS", "W.tick", "LOCAL")],
          _tuples(ctl))


# =====================================================================================================
# C2 — an attribute the class itself ASSIGNS is a variable too (the shapes real code leaves after A-C)
# =====================================================================================================
C2_CASES = (
    ("result.connect(self.address) with self.address = (host, port) in __init__ (stdlib logging.handlers)",
     "class SocketHandler:\n    def __init__(self, host, port):\n        self.address = (host, port)\n"
     "    def make_socket(self, result):\n        result.connect(self.address)\n", ("TRIGGERS",)),
    ("self.ftp.connect(self.host, self.port) with self.host = host (stdlib urllib.request)",
     "class ftpwrapper:\n    def __init__(self, host, port):\n        self.host, self.port = host, port\n"
     "    def init(self):\n        self.ftp.connect(self.host, self.port)\n", ("TRIGGERS",)),
    ("stream.connect(self.address) with a CLASS-level address = '/tmp/s' (tornado's test shape)",
     "class UnixSocketTest:\n    address = '/tmp/s'\n    def test(self, stream):\n        stream.connect(self.address)\n",
     ("TRIGGERS",)),
    ("client.connect(self.host) where host is a dataclass field (host: str)",
     "@dataclass\nclass Cfg:\n    host: str\n    def open(self, client):\n        client.connect(self.host)\n",
     ("TRIGGERS",)),
    ("sig.connect(self.callback) with self.callback = cb (a runtime callback, never a symbol named callback)",
     "class Relay:\n    def __init__(self, cb):\n        self.callback = cb\n    def wire(self, sig):\n"
     "        sig.connect(self.callback)\n        ee.on('evt', self.callback)\n", ("TRIGGERS",)),
    ("the siblings: sched.add_job(self.job_fn) / r.register_hook('response', self.hook) with both assigned",
     "class Runner:\n    def __init__(self, job_fn, hook):\n        self.job_fn = job_fn\n        self.hook = hook\n"
     "    def start(self, r):\n        self.sched.add_job(self.job_fn, 'interval')\n"
     "        r.register_hook('response', self.hook)\n", ("SCHEDULED_AS", "HOOKS")),
    ("Klass.DEFAULT (a module-level class's class-level constant) handed to ee.on",
     "class Klass:\n    DEFAULT = None\nee.on('evt', Klass.DEFAULT)\n", ("TRIGGERS",)),
)


def gate_c2_assigned_attribute(FE) -> None:
    print("\n-- C2: an attribute the class ASSIGNS (self.x = ..., class-level x = ..., a field) names no symbol --")
    for label, src, types in C2_CASES:
        got = _typed(FE, src, types=types)
        check(f"{label} -> no {'/'.join(types)} edge", not got, _tuples(got))
    ctl = _typed(FE, "class W(QWidget):\n    def on_click(self): pass\n    def setup(self):\n"
                     "        self.on_click = functools.partial(self.on_click)\n"
                     "        self.clicked.connect(self.on_click)\n        self.closed.connect(self.close)\n")
    check("control: an own METHOD the class also re-assigns stays Class.m LOCAL; an INHERITED one (never assigned "
          "here) stays a bare NAME",
          _tuples(ctl) == [("db", "W.on_click", "LOCAL", (("signal", "clicked"),)),
                           ("db", "close", "NAME", (("signal", "closed"),))], _tuples(ctl))


# =====================================================================================================
# D — true signal registrations stay, graded like the sibling registrations
# =====================================================================================================
D_SRC = (
    "from django.db.models.signals import post_save\n"
    "from django.db.models import signals\n"
    "from pydispatch import dispatcher\n"
    "from .handlers import on_login\n"
    "import flask\n"
    "import signal\n"
    "def create_profile(sender, **kw): pass\n"
    "def record(sender, **kw): pass\n"
    "def on_evt(*a): pass\n"
    "@receiver(post_save, sender=User)\n"
    "def on_save(sender, **kw): pass\n"
    "post_save.connect(create_profile, sender=User)\n"
    "signals.pre_delete.connect(create_profile)\n"
    "user_logged_in.connect(on_login)\n"
    "user_logged_in.connect(track_login)\n"
    "flask.template_rendered.connect(record)\n"
    "dispatcher.connect(on_evt, signal=SIG)\n"
    "def register(signal):\n"
    "    signal.connect(on_evt)\n"
    "ee.on('ready', boot_cb)\n"
    "ee.on('tick', handlers.on_tick)\n"
    "class Window:\n"
    "    def on_click(self): pass\n"
    "    def setup(self, sio):\n"
    "        self.clicked.connect(self.on_click)\n"
    "        self.closed.connect(self.close)\n"
    "        sio.on('connect', self.on_click)\n"
)

D_WANT = sorted([
    ("sig", "on_save", "NAME", (("signal", "post_save"),)),                 # @receiver: unchanged
    ("sig", "create_profile", "LOCAL", (("signal", "post_save"),)),         # a same-module def
    ("sig", "create_profile", "LOCAL", (("signal", "pre_delete"),)),        # from-imported signals module's signal
    ("sig", "on_login", "NAME", (("signal", "user_logged_in"),)),           # an imported handler
    ("sig", "track_login", "NAME", (("signal", "user_logged_in"),)),        # a name the module never binds
    ("sig", "record", "LOCAL", (("signal", "template_rendered"),)),         # an ATTRIBUTE of a module is a signal
    ("sig", "on_evt", "LOCAL", (("signal", "dispatcher"),)),                # PyDispatcher (from-imported receiver)
    ("sig", "on_evt", "LOCAL", (("signal", "signal"),)),                    # a param shadowing `import signal`
    ("sig", "boot_cb", "NAME", (("event", "ready"),)),
    ("sig", "on_tick", "NAME", (("event", "tick"),)),                       # mod.f -> NAME
    ("sig", "Window.on_click", "LOCAL", (("signal", "clicked"),)),          # own-class method
    ("sig", "close", "NAME", (("signal", "closed"),)),                      # inherited self.m -> bare NAME
    ("sig", "Window.on_click", "LOCAL", (("event", "connect"),)),
])


def gate_d_true_positives(FE) -> None:
    print("\n-- D: real signal registrations are kept (Django / blinker / Flask / PyDispatcher / Qt / pyee) --")
    got = _tuples(_typed(FE, D_SRC, "sig.py"))
    check("every true registration is emitted exactly once, graded like the siblings (def / own method -> LOCAL, "
          "import / mod.f / inherited -> NAME)", got == D_WANT,
          f"missing={[t for t in D_WANT if t not in got]} extra={[t for t in got if t not in D_WANT]}")
    lock = _typed(FE, "user_logged_in.connect(track_login)\nee.on('ready', boot_cb)\n", "m.py")
    check("the shapes tests/framework_edges_gates.py + typed_edges_r7_gates.py lock keep their exact NAME edges",
          _tuples(lock) == [("m", "boot_cb", "NAME", (("event", "ready"),)),
                            ("m", "track_login", "NAME", (("signal", "user_logged_in"),))], _tuples(lock))


# =====================================================================================================
# E — end to end through edge_resolve
# =====================================================================================================
def gate_e_resolution(FE, ER) -> None:
    print("\n-- E: resolution — no fabricated TRIGGERS row; a literal same-file handler grades EXTRACTED --")
    src = "import sqlite3\ndef open_db(path):\n    return sqlite3.connect(path)\n"
    nodes = [_node("db.py::db:1", "Module", "db", "db.py"),
             _node("db.py::open_db:2", "Function", "open_db", "db.py", 2),
             _node("req.py::Request.path:9", "Method", "Request.path", "req.py", 9)]
    rows = ER.resolve_typed_edges(_edges(FE, src), ER.Resolver(nodes), [n["key"] for n in nodes])
    check("the validator's repro persists NO (TRIGGERS, db, Request.path) row (it did, graded INFERRED)",
          not [r for r in rows if r[0] == "TRIGGERS"], rows)

    src = ("class Conn:\n    @property\n    def address(self): return ('h', 1)\n"
           "    def open(self, sock):\n        sock.connect(self.address)\n")
    nodes = [_node("conn.py::conn:1", "Module", "conn", "conn.py"),
             _node("conn.py::Conn:1", "Class", "Conn", "conn.py"),
             _node("conn.py::Conn.address:3", "Method", "Conn.address", "conn.py", 3),
             _node("conn.py::Conn.open:4", "Method", "Conn.open", "conn.py", 4)]
    rows = ER.resolve_typed_edges(_edges(FE, src, "conn.py"), ER.Resolver(nodes), [n["key"] for n in nodes])
    check("a property argument (graphify's :Method getter node) persists NO TRIGGERS row — never EXTRACTED",
          not [r for r in rows if r[0] == "TRIGGERS"], rows)

    src = ("def create_profile(sender, **kw): pass\n"
           "post_save.connect(create_profile, sender=User)\n"
           "class Window:\n"
           "    def on_click(self): pass\n"
           "    def other_click(self): pass\n"
           "    def setup(self):\n"
           "        self.clicked.connect(self.on_click)\n"
           "class Dialog:\n"
           "    def on_click(self): pass\n")
    nodes = [_node("sig.py::sig:1", "Module", "sig", "sig.py"),
             _node("sig.py::create_profile:1", "Function", "create_profile", "sig.py", 1),
             _node("sig.py::Window:3", "Class", "Window", "sig.py", 3),
             _node("sig.py::Window.on_click:4", "Method", "Window.on_click", "sig.py", 4),
             _node("sig.py::Window.other_click:5", "Method", "Window.other_click", "sig.py", 5),
             _node("sig.py::Window.setup:6", "Method", "Window.setup", "sig.py", 6),
             _node("sig.py::Dialog:8", "Class", "Dialog", "sig.py", 8),
             _node("sig.py::Dialog.on_click:9", "Method", "Dialog.on_click", "sig.py", 9)]
    rows = ER.resolve_typed_edges(_edges(FE, src, "sig.py"), ER.Resolver(nodes), [n["key"] for n in nodes])
    got = {(r[1], r[2], r[3]) for r in rows if r[0] == "TRIGGERS"}
    check("post_save.connect(<same-file def>) -> (TRIGGERS, sig, create_profile) EXTRACTED",
          ("sig.py::sig:1", "sig.py::create_profile:1", "EXTRACTED") in got, sorted(got))
    check("self.clicked.connect(self.on_click) -> ONLY Window.on_click, EXTRACTED (the bare name was AMBIGUOUS "
          "with Dialog.on_click)",
          {t for t in got if "on_click" in t[1]} == {("sig.py::sig:1", "sig.py::Window.on_click:4", "EXTRACTED")},
          sorted(got))


# =====================================================================================================
# F — the registry (what sync runs) == the legacy shell, and carries the fix
# =====================================================================================================
def gate_f_registry(FE, LR) -> None:
    print("\n-- F: LR.run (the sync path) == extract_framework_edges, both without the fabricated edges --")
    files = {
        "app/db.py": "import sqlite3\ndef open_db(path):\n    return sqlite3.connect(path)\n"
                     "def wire(ee, cb):\n    ee.on('evt', cb)\n",
        "app/sig.py": D_SRC,
    }
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        for rel, text in files.items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        with contextlib.redirect_stderr(io.StringIO()), _env(DEEPMAP_RECOGNIZERS=None):
            _supp, edges = LR.run(root)
            legacy = FE.extract_framework_edges(root)
    check("registry run() == legacy extract_framework_edges() (byte-equal, same order)", edges == legacy,
          f"{len(edges)} vs {len(legacy)}")
    db = [e for e in edges if e.edge_type == "TRIGGERS" and e.src_file == "app/db.py"]
    check("app/db.py (sqlite3.connect(path) + ee.on('evt', cb)) contributes no TRIGGERS edge", not db, _tuples(db))
    sig = _tuples([e for e in edges if e.edge_type == "TRIGGERS" and e.src_file == "app/sig.py"])
    check("app/sig.py keeps all 13 true registrations through the registry", sig == D_WANT,
          f"missing={[t for t in D_WANT if t not in sig]} extra={[t for t in sig if t not in D_WANT]}")


# =====================================================================================================
# G — D25: no stale typed-edge count in the Python / Ruby recognizers
# =====================================================================================================
_COUNT_RE = re.compile(r"\b(\d+|eleven|twelve|thirteen|fourteen|fifteen)[ -]+typed[ -]+edges?\b", re.IGNORECASE)
_WORDS = {"eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15}


def gate_g_count_drift(FE, root: Path) -> None:
    print("\n-- G (D25): a typed-edge count stated in the Python/Ruby recognizers matches ALLOWED_EDGE_TYPES --")
    n = len(FE.ALLOWED_EDGE_TYPES)
    for rel in ("build/framework_edges.py", "build/ruby_rails_edges.py"):
        text = (root / rel).read_text(encoding="utf-8")
        bad = []
        for i, line in enumerate(text.splitlines(), 1):
            for m in _COUNT_RE.finditer(line):
                tok = m.group(1).lower()
                if (int(tok) if tok.isdigit() else _WORDS[tok]) != n:
                    bad.append(f"{rel}:{i}: {line.strip()}")
        check(f"{rel}: every '<N> typed edges' phrase states {n} (len(ALLOWED_EDGE_TYPES))", not bad, bad)


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    root = REPO
    if "--root" in sys.argv:
        root = Path(sys.argv[sys.argv.index("--root") + 1]).resolve()
    print("=" * 72)
    print(f"DeepMap — .connect / .on TRIGGERS get the variable guard; no stale edge count (root: {root})")
    print("=" * 72)
    FE, ER, LR = _load(root)
    for gate, args in ((gate_a_variable_guard, (FE,)), (gate_b_module_receiver, (FE,)), (gate_c_property, (FE,)),
                       (gate_c2_assigned_attribute, (FE,)), (gate_d_true_positives, (FE,)),
                       (gate_e_resolution, (FE, ER)), (gate_f_registry, (FE, LR)), (gate_g_count_drift, (FE, root))):
        try:
            gate(*args)
        except Exception as exc:  # noqa: BLE001 — a crash on the pre-fix tree is a FAIL, never an abort
            check(f"{gate.__name__} ran without raising", False, f"{type(exc).__name__}: {exc}")
    total = len(PASSES) + len(FAILS)
    print("\n" + "=" * 72)
    if FAILS:
        print(f"RESULT: RED — {len(PASSES)}/{total} gates passed.")
        return 1
    print(f"RESULT: GREEN — {len(PASSES)}/{total} gates passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
