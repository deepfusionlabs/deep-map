#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Typed-receiver call linking, slice S4 — every design section-5 MUST-NOT shape, Python and TypeScript / JavaScript
(plus the precise fixes, the family filter and the non-production path rule), each beside a POSITIVE sibling, judged
on the FINAL graph: the edges `push_to_neo4j` writes (its first step, the pure `sync._resolve_calls`) after every
push pass. No Neo4j, no network, no spend; every engine run uses a temporary DEEPMAP_HOME (never ~/.deepmap).

The pipeline is sync's own, step for step (`_sync_pass`): `_rebuild_code` -> `enrich.apply_sidecar` ->
`_apply_recognizers` -> `_unkey_receiver_calls` -> `_typed_calls_link` -> `_attach_bare_calls` ->
`_attach_local_defs` -> `_attach_local_def_calls` -> `_resolve_calls`. Section A locks that order against
build/sync.py's AST and proves a real `sync()` pass (database faked) pushes exactly these edges. The engine runs
three ways:
  * the INSTALLED graphify, for real (`_rebuild_code` unpatched: the subprocess, the shared reader, the typed note);
  * the captured graph.json of graphify 0.8.39 AND 0.8.51 (tests/fixtures/typed_calls/negative/<0839|0851>/), fed
    to the same unpatched `_rebuild_code` through its engine subprocess (`graphify_extract._run_engine` writes the
    captured graph.json instead of running graphify — everything after the subprocess is the real code);
  so every case below is judged on the live engine AND on both engine ends. Two family-filter shapes no graphify run
  can produce (a Rails .erb view calling a helper; a caller with an extension no family knows) are injected as the
  engine's tuples right after the recognizers (`inject_mix`) and then go through every push pass the same way.

graphify (both ends) binds an in-file member call by its bare name to the last same-named definition of the file and
links almost no cross-file member call, so a must-not is written in the file of its decoys: the engine tuple there is
what a wrong REMOVAL would lose. A cross-file must-not keeps only its "no typed key" teeth (`prior=()`).

Where graphify is not installed, the captures still replay (only the engine subprocess is replaced); where the
tree-sitter TS / JS grammars are not importable (an optional dependency graphify pulls in), the TS / JS reader and the
TS re-key are off by design and their cases are SKIPPED, never failed. Python 3.10 / 3.11 fail the one PEP 695 file
cleanly (no facts: nothing typed in it).

Variants (every graph flag PINNED — `0` where off, never "unset = off" — so a later default flip cannot move them):
  V0 every flag off · VTa TYPED (python, typescript, javascript), audit off · VT TYPED + audit · VA audit only ·
  VP PRECISE (python, js; fixes on) · VPx PRECISE with DEEPMAP_PRECISE_FIXES=0 · VPT VP + VT · VF the family filter ·
  VTnp VT + DEEPMAP_TYPED_CALLS_TESTS=1 + DEEPMAP_TYPED_CALLS_NONPROD=1 · V9 VPT + DEEPMAP_GRAPHIFY_CALLS_PLUS=1.

Case kinds (per caller C and member m — the last name segment of a target):
  none   the must-not: NO typed key for m (VTa, VT, VPT) AND NO change to C's m edges — VTa == V0 and VPT == VP edge
         for edge and grade; VT only where the audit named the target (lowered to AMBIGUOUS, never added / removed).
         `prior` = V0 holds an m edge of C, so "no removal" has teeth.
  keep   no removal (every V0 m edge of C stays, at its grade unless a typed key re-grades it) and typed keys only to
         the `allow`ed targets.
  pos    the positive sibling: exactly the expected typed keys and grades for m, and C's final m edges are exactly
         `edges` (default: the typed keys) — and differ from V0's (the shape changes the graph).
  gone   external / absent (a compiler-checked .ts chain): no typed key, every m edge of C removed (V0 had some).
  weak   the audit demotes C's m edge to the target (call_weak; the final grade AMBIGUOUS; V0's was not).
  noweak the audit leaves C's m edges exactly at V0's grades (VA), and names none of them.
  never  the target is typed from NO caller anywhere in the tree (optionally: from no caller of one extension).
  noedge no final edge caller -> target in ANY variant (CALLS_PLUS included: flask's `import json; json.dumps()`).
  plus the family filter (VF), the precise fixes (VP vs VPx) and the caller policy (VT vs VTnp) cases.

Sections:
  A  the pipeline is sync's: the step order read from build/sync.py's AST; a real sync() pass (graphify path, the
     engine fed a capture, the database faked) pushes exactly the edges this gate computes.
  B  the captures: both engines' fixtures exist, were made from THIS gate's trees, and the installed engine's fresh
     extraction gives the same final graph as its capture (the replay of the subprocess is transparent).
  C  every case on every source (live engine + both captures).
  D  the graph-wide invariants on every source: every typed key reaches the push marked (CallKey) and its edge carries
     exactly its call_typed grade (AMBIGUOUS where the audit named it); no cross-file EXTRACTED typed edge; only the
     three grades; node text identical across the variants; TYPED=0 / AUDIT=0 leaves the final graph exactly V0.
  E  the on/off effect diff (V0 -> VT) per tree and source, printed (the artefact; `--diff-out <file>` writes it).
  A0 (last) every pipeline module was loaded from the build/ under test (a --root run measures that tree).

Fixtures: tests/fixtures/typed_calls/negative/<0839|0851>/<tree>.json — the REAL graph.json graphify wrote for
TREES[<tree>] (absolute paths made root-relative). `python tests/typed_calls_shapes_gates.py --capture` (re)writes
the installed engine's bucket for every tree that is missing or stale; run it under BOTH engines after editing a tree.

usage: python tests/typed_calls_shapes_gates.py [--root <tree holding build/>] [--capture | --dump]
                                                [--diff-out <file>]
       (--root: load build/ from another checkout or a seeded copy — the RED-first and mutation runs)
"""
from __future__ import annotations

import ast
import contextlib
import hashlib
import importlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FX = REPO / "tests" / "fixtures" / "typed_calls" / "negative"
BUCKETS = {"0.8.39": "0839", "0.8.51": "0851"}


def _parse_args(argv):
    root, mode, diff_out, it = None, "", None, iter(argv)
    for arg in it:
        if arg == "--root":
            root = next(it, "")
        elif arg.startswith("--root="):
            root = arg[len("--root="):]
        elif arg in ("--capture", "--dump"):
            mode = arg[2:]
        elif arg == "--diff-out":
            diff_out = next(it, "")
        elif arg.startswith("--diff-out="):
            diff_out = arg[len("--diff-out="):]
        else:
            return None, None, None, f"unknown argument {arg!r}"
    if root is not None and (not root or not (Path(root) / "build").is_dir()):
        return None, None, None, f"--root needs a directory that holds build/ (got {root!r})"
    if diff_out is not None and not diff_out:
        return None, None, None, "--diff-out needs a file path"
    return (Path(root).resolve() if root else REPO), mode, diff_out, None


ROOT, MODE, DIFF_OUT, _USAGE = (_parse_args(sys.argv[1:]) if __name__ == "__main__"
                                else (REPO, "", None, None))
if _USAGE:
    print(f"usage: python tests/typed_calls_shapes_gates.py [--root <tree>] [--capture | --dump] "
          f"[--diff-out <file>]  ({_USAGE})", file=sys.stderr)
    raise SystemExit(2)
BUILD = ROOT / "build"
sys.path.insert(0, str(BUILD))

import graphify_extract as GX  # noqa: E402
import sync as S               # noqa: E402

try:
    import typed_calls as TC   # noqa: E402
except Exception:  # noqa: BLE001 — a tree without the feature (the RED-first run): RED, never a crash
    TC = None

_HAS_ENGINE = importlib.util.find_spec("graphify") is not None

# ================================================================================================
# Recorder, environment, temp dirs
# ================================================================================================


class Rec:
    def __init__(self):
        self.passed, self.failed, self.fails, self.skipped = 0, 0, [], 0

    def check(self, label, cond, detail="") -> bool:
        ok = bool(cond)
        if ok:
            self.passed += 1
        else:
            self.failed += 1
            self.fails.append(label)
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + ("" if ok or detail == "" else f" -- {str(detail)[:1200]}"))
        return ok

    def skip(self, label, why):
        self.skipped += 1
        print(f"  [SKIP] {label} -- {why}")


REC = Rec()


def check(label, cond, detail="") -> bool:
    return REC.check(label, cond, detail)


_TMP: list = []


def tmp() -> Path:
    if not _TMP:
        _TMP.append(Path(tempfile.mkdtemp(prefix="dm_s4_")).resolve())
    return _TMP[0]


def cleanup() -> None:
    while _TMP:
        shutil.rmtree(_TMP.pop(), ignore_errors=True)


WAVE1 = "python,typescript,javascript"
# every graph flag pinned ("0" where off — never "unset = off"), so a default flip (slice S8) cannot move a case
OFF = {"DEEPMAP_TYPED_CALLS": "0", "DEEPMAP_CALLS_AUDIT": "0", "DEEPMAP_PRECISE_CALLS": "0",
       "DEEPMAP_CALLS_FAMILY_FILTER": "0", "DEEPMAP_GRAPHIFY_CALLS_PLUS": "0", "DEEPMAP_LOCAL_DEF_CALLS": "0",
       "DEEPMAP_DROP_APP_TO_TEST_CALLS": "0", "DEEPMAP_FALSE_CALL_FILTER": "1", "DEEPMAP_EXCLUDE_TESTS": "0",
       "DEEPMAP_PRECISE_FIXES": "1", "DEEPMAP_SELF_CALL_REBIND": "1", "DEEPMAP_INCREMENTAL_SYNC": "0",
       "DEEPMAP_TYPED_CALLS_REPLACE": "1", "DEEPMAP_TYPED_CALLS_TESTS": "0", "DEEPMAP_TYPED_CALLS_NONPROD": "0",
       "DEEPMAP_INCLUDE_VENDOR": "0", "DEEPMAP_MERGE_SUMMARIES": "0"}
_VT = {"DEEPMAP_TYPED_CALLS": WAVE1, "DEEPMAP_CALLS_AUDIT": WAVE1}
_VP = {"DEEPMAP_PRECISE_CALLS": "python,js"}
VARIANTS = {
    "V0": dict(OFF),
    "VTa": dict(OFF, DEEPMAP_TYPED_CALLS=WAVE1),
    "VT": dict(OFF, **_VT),
    "VA": dict(OFF, DEEPMAP_CALLS_AUDIT=WAVE1),
    "VP": dict(OFF, **_VP),
    "VPx": dict(OFF, DEEPMAP_PRECISE_FIXES="0", **_VP),
    "VPT": dict(OFF, **_VP, **_VT),
    "VF": dict(OFF, DEEPMAP_CALLS_FAMILY_FILTER="1"),
    "VTnp": dict(OFF, DEEPMAP_TYPED_CALLS_TESTS="1", DEEPMAP_TYPED_CALLS_NONPROD="1", **_VT),
    "V9": dict(OFF, DEEPMAP_GRAPHIFY_CALLS_PLUS="1", **_VP, **_VT),
}


@contextlib.contextmanager
def flags(env: dict):
    """Every DEEPMAP_* variable removed (the shell must not leak in), then `env` + the run settings (a temporary
    DEEPMAP_HOME, the graphify engine, no auto-.gitignore); all restored after."""
    saved = {k: v for k, v in os.environ.items() if k.startswith("DEEPMAP_")}
    try:
        for k in saved:
            os.environ.pop(k, None)
        os.environ.update(env)
        os.environ.update({"DEEPMAP_HOME": str(tmp() / "home"), "DEEPMAP_EXTRACTOR": "graphify",
                           "DEEPMAP_AUTO_GITIGNORE": "0", "DEEPMAP_GRAPHIFY_TIMEOUT": "900"})
        yield
    finally:
        for k in [k for k in os.environ if k.startswith("DEEPMAP_")]:
            os.environ.pop(k, None)
        os.environ.update(saved)


@contextlib.contextmanager
def patched(obj, **attrs):
    missing = object()
    saved = {k: getattr(obj, k, missing) for k in attrs}
    try:
        for k, v in attrs.items():
            setattr(obj, k, v)
        yield
    finally:
        for k, v in saved.items():
            if v is missing:
                try:
                    delattr(obj, k)
                except AttributeError:
                    pass
            else:
                setattr(obj, k, v)


@contextlib.contextmanager
def quiet():
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        yield err


# ================================================================================================
# The fixture trees (design section 5), each must-not beside its positive sibling and a decoy that a wrong
# binding or a wrong removal would show
# ================================================================================================

# graphify (both ends) binds an IN-FILE member call by its bare name to the last same-named definition of the file and
# links almost no cross-file member call; map_graph hands most of those links on as names, which the push fans out over
# every same-named node. So a must-not written in the file of its decoys has an engine tuple a wrong REMOVAL would show,
# and a cross-file must-not has only its "no typed key" teeth (`prior` False below).
PY_BIND = {
    "app/__init__.py": "",
    "app/core.py": '''\
import threading
from typing import Any, List, TypeVar, Union

T = TypeVar("T")


class Order:
    def save(self):
        return 1

    def acquire(self):
        return 2

    def append(self, item):
        return item

    def store(self):
        return 8


class Other:
    def save(self):
        return 3

    def acquire(self):
        return 4

    def append(self, item):
        return item


class Third:
    def save(self):
        return 5


def make():
    return Order()


def pos_local():
    x = Order()
    return x.save()


def pos_param(x: Order):
    return x.save()


def pos_append(o: Order):
    return o.append(1)


def pos_acquire(o: Order):
    return o.acquire()


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
    return o


def mn_with():
    lk = Order()
    with threading.Lock() as lk:
        lk.acquire()
    return lk


def mn_except():
    e = Order()
    try:
        pass
    except Exception as e:
        return e.save()
    return None


def mn_walrus(items):
    x = Order()
    if (x := items[0]):
        return x.save()
    return None


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


def mn_comprehension(items):
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


def mn_pipe_str(x: Order | str):
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
''',
    # cross-file siblings: no engine tuple, so a typed key is a pure addition; the candidate set has nothing to narrow
    "app/bind.py": '''\
from app.core import Order, Other


def pos_cross_param(x: Order):
    return x.save()


def cand_cross_if_else(flag):
    if flag:
        x = Order()
    else:
        x = Other()
    return x.save()


def mn_cross_unannotated(request):
    return request.save()
''',
    "app/tvar.py": '''\
from typing import TypeVar

Order = TypeVar("Order")


class Keeper:
    def save(self):
        return 6


def mn_typevar_named_like_class(x: Order):
    return x.save()
''',
    # PEP 695 parses only on Python 3.12+: older interpreters fail this one file (no facts -> nothing typed). Its member
    # (`store`) is its own: on 3.10 / 3.11 the pass cannot see Keeper695, and the core keeps an unseen class's
    # same-named method reachable (an AMBIGUOUS key) — a `save` here would make the `save` siblings version-dependent
    "app/pep695.py": '''\
from app.core import Order


class Keeper695:
    def store(self):
        return 7


def mn_pep695[Order](x: Order):
    return x.store()
''',
}

PY_RESOLVE = {
    "app/__init__.py": "",
    "app/models.py": '''\
class Order:
    def save(self):
        return 1

    @classmethod
    def create(cls):
        return cls()


class Other:
    def save(self):
        return 3

    @classmethod
    def create(cls):
        return cls()
''',
    "app/alt.py": '''\
class Order:
    def save(self):
        return 9
''',
    "app/maybe.py": '''\
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models import Order
else:
    Order = object


class Spare:
    def save(self):
        return 10


def mn_type_checking_else(x: Order):
    return x.save()
''',
    "app/tryimp.py": '''\
try:
    from app.models import Order
except ImportError:
    from app.alt import Order


class Spare2:
    def save(self):
        return 11


def mn_try_two_classes(x: Order):
    return x.save()


def pos_try_ctor_free():
    return Spare2().save()
''',
    "a/repo.py": '''\
class Repo:
    def find(self):
        return "a"
''',
    "b/repo.py": '''\
class Repo:
    def find(self):
        return "b"
''',
    "app/pick.py": '''\
from b.repo import Repo


class Finder:
    def find(self):
        return "decoy"


def pick_b(r: Repo):
    return r.find()
''',
    "app/mods.py": '''\
import os
import re

from app.models import Order


class Finder:
    def search(self, q):
        return q

    def join(self, other):
        return other

    def main(self):
        return 2

    def create(self):
        return 3


def _load(name):
    return __import__(name)


CG = _load("plugins")


def ext_search(p):
    return re.search("a", p)


def ext_join(p):
    return os.path.join(p, "x")


def mn_path_loaded():
    return CG.main()


def mn_class_alias():
    x = Order
    return x.create()


def pos_classref():
    return Order.create()
''',
    "tools/__init__.py": "",
    "tools/lr.py": '''\
def run(root):
    return root
''',
    "tools/legacy.py": '''\
class LR:
    def run(self, root):
        return root
''',
    "tools/main.py": '''\
import tools.lr as LR


def main(root):
    return LR.run(root)
''',
    "src/flask/__init__.py": "",
    "src/flask/json/__init__.py": '''\
def dumps(obj):
    return str(obj)


def loads(s):
    return s
''',
    "src/flask/json/provider.py": '''\
import json


class DefaultJSONProvider:
    def dumps(self, obj):
        return json.dumps(obj)

    def loads(self, s):
        return json.loads(s)
''',
    "src/flask/json/tag.py": '''\
import json


class TaggedJSONSerializer:
    def dumps(self, value):
        return json.dumps(value)

    def loads(self, value):
        return json.loads(value)
''',
    "src/flask/app.py": '''\
from . import json as fjson


class Flask:
    def respond(self, obj):
        return fjson.dumps(obj)

    def dumps(self, obj):
        return obj
''',
}

PY_CLASS = {
    "app/__init__.py": "",
    "app/models.py": '''\
class Order:
    def save(self):
        return 1


class Other:
    def save(self):
        return 3
''',
    "app/fields.py": '''\
from app.models import Order, Other


class Holder:
    def __init__(self, repo, cb):
        self.repo = repo
        self.cb = cb
        self.owned = Order()

    def mn_field_callable(self):
        return self.cb()

    def mn_untyped_injection(self):
        return self.repo.save()

    def pos_owned(self):
        return self.owned.save()


class TwoClasses:
    def __init__(self):
        self.item = Order()

    def reset(self):
        self.item = Other()

    def mn_field_two_classes(self):
        return self.item.save()


class WithProp:
    @property
    def save(self):
        return Order()


def mn_property_called(w: WithProp):
    return w.save()


class Callbacks:
    def cb(self):
        return 5
''',
    "app/open.py": '''\
class Dyn:
    def __getattr__(self, name):
        return name

    def save(self):
        return 1


def mn_getattr_class(d: Dyn):
    return d.save()


class Meta(type):
    pass


class WithMeta(metaclass=Meta):
    def save(self):
        return 2


def mn_metaclass(w: WithMeta):
    return w.save()


def unknown_deco(c):
    return c


@unknown_deco
class Decorated:
    def save(self):
        return 3


def mn_decorated_class(d: Decorated):
    return d.save()


class Patched:
    def save(self):
        return 4


Patched.save = lambda self: 0


def mn_patched(p: Patched):
    return p.save()


class ExtBase(dict):
    def mn_super_external(self):
        return super().save()


class Ctx:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None


def mn_dunder(c: Ctx):
    return c.__enter__()


class Plain:
    def save(self):
        return 5


def pos_plain(p: Plain):
    return p.save()
''',
    "app/audit.py": '''\
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
''',
    "app/mailer.py": '''\
class Mailer:
    def notify(self):
        return 3

    def helper(self):
        return 4

    def bind(self, x):
        return x
''',
}

TS_SHAPES = {
    "package.json": '{ "name": "s4app", "dependencies": { "lodash": "^4.17.21" } }\n',
    "src/m.ts": '''\
export class Repo {
  find(): void {}

  static make(): Repo {
    return new Repo();
  }
}

export class Cache {
  find(): void {}
}

export interface IRepo {
  find(): void;
}

export class SqlRepo implements IRepo {
  find(): void {}
}

export class Order {
  save(): void {}
}

export class T {
  run(): void {}
}

export class Runner {
  run(): void {}
}

export class Box<T> {
  hold(x: T): void {
    x.run();
  }
}

export function helper(): void {}

export function viaInterface(r: IRepo): void {
  r.find();
}

export function viaInterfaceMissing(r: IRepo): void {
  r.extra();
}

export function viaUnion(r: Repo | Cache): void {
  r.find();
}

export function viaCast(x: unknown): void {
  (x as Repo).find();
}

export function viaAny(a: any): void {
  a.find();
}

export function viaUnknown(u: unknown): void {
  (u as any).find();
}

export function viaDestructure({ repo }: { repo: Repo }): void {
  repo.find();
}

export function viaRebind(other: Cache): void {
  let r = new Repo();
  r = other as any;
  r.find();
}

export function viaFunctionThis(): void {
  [1].forEach(function () {
    this.find();
  });
}

export function posParam(r: Repo): void {
  r.find();
}

export function posT(x: T): void {
  x.run();
}

export class Holder {
  repo: Repo = new Repo();

  computed(): void {
    this["repo"].find();
  }

  viaFnExpr(): void {
    [1].forEach(function () {
      this.find();
    });
  }

  viaObjectMethod(): unknown {
    return {
      find(): void {},
      run(): void {
        this.find();
      },
    };
  }

  posField(): void {
    this.repo.find();
  }

  posOptional(): void {
    this.repo?.find();
  }
}

export class Caller extends Repo {
  viaCall(): void {
    this.find();
    this.find.call(this);
  }

  viaBind(): void {
    this.find();
    const f = this.find.bind(this);
    f();
  }

  viaApply(): void {
    this.find();
    this.find.apply(this, []);
  }

  posSelf(): void {
    this.find();
  }
}

export class Tail {
  find(): void {}

  extra(): void {}
}
''',
    "src/neg.ts": '''\
import { Order } from "./m";
import type { Order as OrderT } from "./m";

export function typeOnlyValue(): void {
  new OrderT();
}

export function posNew(): Order {
  return new Order();
}
''',
    "src/pos.ts": '''\
import { Repo } from "./m";
import * as m from "./m";

export function posCrossParam(r: Repo): void {
  r.find();
}

export function posModule(): void {
  m.helper();
}
''',
    "src/view.tsx": '''\
import { Order } from "./m";

export function View(): any {
  return <Order />;
}
''',
    "src/log.ts": '''\
export function Log(target: any, key: string): void {}
''',
    "src/logcls.ts": '''\
export class Log {
  write(): void {}
}
''',
    "src/deco.ts": '''\
import { Log } from "./log";

export class Svc {
  @Log
  run(): void {}
}
''',
    "src/pkg.ts": '''\
import * as lodash from "lodash";

export class Stats {
  crunch(): number {
    return 0;
  }
}

export function viaPackage(xs: number[]): number {
  return lodash.crunch(xs);
}

export function viaBuiltin(): number {
  return JSON.crunch();
}
''',
    "src/lone.ts": '''\
export class Lone {
  run(): void {
    this.save();
  }
}

export class Unrelated {
  save(): void {}
}
''',
    "src/lone.js": '''\
class LoneJs {
  run() {
    this.save();
  }
}

class UnrelatedJs {
  save() {}
}

module.exports = { LoneJs, UnrelatedJs };
''',
    "src/lib.js": '''\
class Repo {
  find() {}
}

function make() {
  return new Repo();
}

module.exports = { Repo, make };
''',
    "src/req.js": '''\
const { Repo, make } = require("./lib");

class Local {
  find() {}
}

function useLib() {
  const r = new Repo();
  r.find();
  return make();
}

/** @param {Repo} r */
function jsdoc(r) {
  r.find();
}

function objectThis() {
  const obj = {
    find() {},
    run() {
      this.find();
    },
  };
  return obj;
}

module.exports = { Local, useLib, jsdoc, objectThis };
''',
}

TS_WS = {
    "tsconfig.json": '{ "compilerOptions": { "baseUrl": ".", "paths": { "@lib/*": ["packages/b/src/*"] } } }\n',
    "packages/a/src/repo.ts": '''\
export class Repo {
  find(): void {}
}
''',
    "packages/b/src/repo.ts": '''\
export class Repo {
  find(): void {}
}
''',
    "src/use.ts": '''\
import { Repo } from "@lib/repo";
import { Repo as Unmapped } from "@other/repo";

export class Local {
  find(): void {}
}

export function pickB(r: Repo): void {
  r.find();
}

export function viaUnmapped(r: Unmapped): void {
  r.find();
}
''',
}

MIX = {
    "web/order.ts": '''\
export class Order {
  save(): void {}
}
''',
    "web/util.js": '''\
function shared() {
  return 1;
}

module.exports = { shared };
''',
    "web/use.ts": '''\
import { shared } from "./util.js";

export function useShared(): number {
  return shared();
}
''',
    "app/__init__.py": "",
    "app/no_order.py": '''\
def mn_ts_order():
    return Order()
''',
    "app/py_order.py": '''\
class Order:
    def save(self):
        return 1


def py_ctor():
    return Order()


def shared():
    return 2
''',
    "app/helpers/orders_helper.rb": '''\
module OrdersHelper
  def total_label(order)
    order.to_s
  end
end
''',
    "app/views/orders/show.html.erb": "<%= total_label(@order) %>\n",
}

NONPROD = {
    "app/__init__.py": "",
    "app/svc.py": '''\
def helper():
    return 2
''',
    "examples.py": '''\
from app import svc


def run_example():
    return svc.helper()
''',
    "examples/demo.py": '''\
from app import svc


def run_demo():
    return svc.helper()
''',
    "examples/fixed.py": '''\
from app import svc


def helper():
    return 3


def run_fixed():
    return svc.helper()
''',
    "docs/conf.py": '''\
from app import svc


def run_docs():
    return svc.helper()
''',
    "src/docs_parser.py": '''\
from app import svc


def parse_docs():
    return svc.helper()
''',
    "mydocs/guide.py": '''\
from app import svc


def run_guide():
    return svc.helper()
''',
    "tests/test_svc.py": '''\
from app import svc


def test_helper():
    return svc.helper()
''',
}

PRECISE = {
    "shop/__init__.py": "",
    "shop/models.py": '''\
class Order:
    def save(self):
        return 1


class Repo:
    def find(self):
        return 2
''',
    "legacy/__init__.py": "",
    "legacy/models.py": '''\
class Order:
    def save(self):
        return 3
''',
    "shop/svc.py": '''\
from shop.models import Order, Repo


def typed(o: Order, r: Repo) -> None:
    return None


def once():
    return Order()


def twice():
    a = Order()
    b = Order()
    return a, b
''',
    "src/result.ts": '''\
export type OK<T> = { status: "valid"; value: T };
export const OK = <T>(value: T): OK<T> => ({ status: "valid", value });

export function okOne(): OK<number> {
  return OK(1);
}
''',
    # the type alias here, the same-named VALUE in another file: the re-key never crosses files
    "src/remote.ts": '''\
export const Remote = (n: number): number => n;
''',
    "src/useremote.ts": '''\
import { Remote } from "./remote";

export type Remote = { kind: "remote" };

export function viaRemote(): number {
  return Remote(1);
}
''',
}

TREES = {"py_bind": PY_BIND, "py_resolve": PY_RESOLVE, "py_class": PY_CLASS, "ts_shapes": TS_SHAPES,
         "ts_ws": TS_WS, "mix": MIX, "nonprod": NONPROD, "precise": PRECISE}


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
    """TREES[name] materialized once under the gate's temp dir (git-free: no ignore rules, no rescue)."""
    if name not in _ROOTS:
        _ROOTS[name] = write_tree(tmp() / "t" / name, TREES[name])
    return _ROOTS[name]


def fixture_path(bucket: str, name: str) -> Path:
    return FX / bucket / f"{name}.json"


_CAPS: dict = {}


def capture(bucket: str, name: str):
    """The capture document of (bucket, tree), read once; None when absent."""
    p = fixture_path(bucket, name)
    if (bucket, name) not in _CAPS:
        _CAPS[(bucket, name)] = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None
    return _CAPS[(bucket, name)]


def _relative(graph: dict, root: Path) -> dict:
    """graph.json with every absolute path under `root` made root-relative (map_graph reads none of them)."""
    prefixes = [str(root) + os.sep, str(root).replace("\\", "/") + "/"]

    def fix(v):
        if isinstance(v, str):
            for p in prefixes:
                if v.lower().startswith(p.lower()):
                    return v[len(p):].replace("\\", "/")
        return v

    return {k: ([{kk: fix(vv) for kk, vv in item.items()} if isinstance(item, dict) else item for item in v]
                if isinstance(v, list) else v) for k, v in graph.items()}


def fake_engine(graph: dict):
    """A stand-in for graphify_extract._run_engine: writes `graph` where the engine writes graph.json and exits 0 —
    everything extract_with_report does after the subprocess (freshness, map_graph through the shared reader, the
    report) is the real code."""
    text = json.dumps(graph)

    def run_engine(cmd, *, cwd, env, timeout):
        out = Path(env["GRAPHIFY_OUT"])
        out.mkdir(parents=True, exist_ok=True)
        (out / "graph.json").write_text(text, encoding="utf-8")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")
    return run_engine


# The pipeline, step for step as build/sync.py `_sync_pass` runs it (section A locks the order from its AST).
STEPS = ("_rebuild_code", "enrich.apply_sidecar", "_apply_recognizers", "_unkey_receiver_calls",
         "_typed_calls_link", "_attach_bare_calls", "_attach_local_defs", "_attach_local_def_calls",
         "push_to_neo4j")


def label_of(n) -> str:
    return f"{n.get('label')}:{n.get('name')}@{n.get('source_file')}:{n.get('start_line')}"


def node_ids(nodes) -> dict:
    """key -> "file::name" (a :Module node "file::name#module"; a name two other nodes of one file share gets
    "@line" on both)."""
    base = {}
    for n in nodes:
        mod = "#module" if n.get("label") == "Module" else ""
        base.setdefault(n["key"], (f"{n.get('source_file')}::{n.get('name')}{mod}", n.get("start_line")))
    count: dict = {}
    for v, _ln in base.values():
        count[v] = count.get(v, 0) + 1
    return {k: (v if count[v] == 1 else f"{v}@{ln}") for k, (v, ln) in base.items()}


def member_of(name) -> str:
    s = str(name or "").split("::")[-1]
    return s.split(".")[-1]


class Result:
    """One pipeline run's outcome: the nodes and calls handed to the push, the final edges, the stats."""

    def __init__(self, nodes, calls, edges, stats, engine, err):
        self.nodes, self.calls, self.edges, self.stats, self.engine, self.err = nodes, calls, edges, stats, engine, err
        self.ids = node_ids(nodes)
        self.by_ident = {}
        for k, v in self.ids.items():
            self.by_ident.setdefault(v, k)
        self.node = {n["key"]: n for n in nodes}
        self.stable = {n["key"]: label_of(n) for n in nodes}

    def ident(self, key) -> str:
        return self.ids.get(key, f"?{key}")

    def has(self, ident: str) -> bool:
        return ident in self.by_ident

    def m_edges(self, caller: str, m: str) -> dict:
        """{target ident: grade} of the final edges of `caller` (a "file::name" ident) whose target's last name
        segment is m."""
        ck = self.by_ident.get(caller)
        out = {}
        for (a, b), g in self.edges.items():
            if a == ck and member_of(self.node.get(b, {}).get("name")) == m:
                out[self.ident(b)] = g
        return out

    def typed(self, caller: str, m: str = None) -> dict:
        n = self.node.get(self.by_ident.get(caller), {})
        return {self.ident(t): g for t, g in (n.get("call_typed") or {}).items()
                if m is None or member_of(self.node.get(t, {}).get("name")) == m}

    def weak(self, caller: str) -> set:
        n = self.node.get(self.by_ident.get(caller), {})
        return {self.ident(t) for t in (n.get("call_weak") or ())}

    def typed_targets(self) -> set:
        return {self.ident(t) for n in self.nodes for t in (n.get("call_typed") or {})}

    def canonical(self) -> dict:
        """The final graph on version-stable identities (label:name@file:line) — for comparing two sources."""
        return {"edges": sorted([self.stable.get(a, a), self.stable.get(b, b), g] for (a, b), g in self.edges.items()),
                "typed": sorted([self.stable[n["key"]], self.stable.get(t, t), g] for n in self.nodes
                                for t, g in (n.get("call_typed") or {}).items()),
                "weak": sorted([self.stable[n["key"]], self.stable.get(t, t)] for n in self.nodes
                               for t in (n.get("call_weak") or ()))}


@contextlib.contextmanager
def engine_present():
    """A capture replays through `_rebuild_code` even where graphify is not installed: extract_with_report only asks
    importlib whether a `graphify` module exists before it starts the (replaced) subprocess. Installed: a no-op."""
    if _HAS_ENGINE or "graphify" in sys.modules:
        yield
        return
    import importlib.machinery
    stub = types.ModuleType("graphify")
    stub.__spec__ = importlib.machinery.ModuleSpec("graphify", None)
    sys.modules["graphify"] = stub
    try:
        yield
    finally:
        sys.modules.pop("graphify", None)


def chain(root: Path, graph=None, *, inject=None, hide=False) -> Result:
    """The pipeline under the CURRENT environment: `graph` None = the installed engine for real, else that captured
    graph.json handed back by the engine subprocess. `inject(root, nodes, calls)` adds what an engine could emit but
    graphify does not (after the recognizers, before every push pass); `hide`: build/typed_calls.py unimportable."""
    ctxs = contextlib.ExitStack()
    if graph is not None:
        ctxs.enter_context(patched(GX, _run_engine=fake_engine(graph)))
        ctxs.enter_context(engine_present())
    if hide:
        ctxs.enter_context(patched(S, _typed_calls_module=lambda: None))
    with ctxs, quiet() as err:
        extracted = S._rebuild_code(root)                       # kept alive until the typed step (its note)
        nodes, calls, _imports, engine = extracted
        S.enrich.apply_sidecar(root, nodes)
        nodes, _typed_edges = S._apply_recognizers(root, nodes)
        if inject is not None:
            nodes, calls = inject(root, nodes, calls)
        calls, _n_unkey = S._unkey_receiver_calls(root, nodes, calls)
        step = getattr(S, "_typed_calls_link", None)
        stats = None
        if callable(step):
            calls, stats = step(root, nodes, calls)
        S._attach_bare_calls(root, nodes, calls)
        S._attach_local_defs(root, nodes, calls)
        S._attach_local_def_calls(root, nodes, calls)
        edges = S._resolve_calls(nodes, calls)
        del extracted
    return Result(nodes, calls, edges, stats, engine, err.getvalue())


def _synthetic(key, label, name, sf, line=1):
    return {"key": key, "label": label, "name": name, "source_file": sf, "start_line": line, "end_line": line,
            "body_summary": f"{name}.", "documented": False, "confidence": "EXTRACTED"}


def inject_mix(root, nodes, calls):
    """The family-filter shapes no graphify run can produce (it parses no .erb, and every extension it parses has an
    edge_resolve family): a Rails view (the recognizer's :Module node of show.html.erb) calling a helper by name, and
    a caller with an extension no family knows. The tuples go through every push pass like an engine's."""
    nodes, calls = list(nodes), list(calls)
    view = next((n["key"] for n in nodes if n.get("source_file") == "app/views/orders/show.html.erb"), None)
    if view is not None:
        calls.append((view, "total_label"))
    gen = _synthetic("tools/gen.xyz::gen_main:1", "Function", "gen_main", "tools/gen.xyz")
    nodes.append(gen)
    calls.append((gen["key"], "shared"))
    return nodes, calls


TREE_INJECT = {"mix": inject_mix}

_RUNS: dict = {}
LIVE = "live"


def run(source: str, tree: str, variant: str, *, hide=False) -> Result:
    """The pipeline of `tree` under VARIANTS[variant] on `source` — "0839" / "0851" (that engine's capture) or "live"
    (the installed engine's own graph.json of this run, read back after one real extraction). Memoised."""
    k = (source, tree, variant, hide)
    if k not in _RUNS:
        graph = live_graph(tree) if source == LIVE else capture(source, tree)["graph"]
        with flags(VARIANTS[variant]):
            _RUNS[k] = chain(root_for(tree), graph, inject=TREE_INJECT.get(tree), hide=hide)
    return _RUNS[k]


_LIVE: dict = {}


def live_graph(tree: str):
    """The installed engine's real graph.json of `tree`: one real extraction through the UNPATCHED `_rebuild_code`
    under VT (the typed note and the shared reader on); its Result is kept as _LIVE[(tree, "real")]."""
    if tree not in _LIVE:
        root = root_for(tree)
        with flags(VARIANTS["VT"]):
            res = chain(root, None, inject=TREE_INJECT.get(tree))
            gp = GX.graphify_out_dir(root) / "graph.json"
            g = json.loads(gp.read_text(encoding="utf-8")) if gp.is_file() else None
        _LIVE[tree] = g
        _LIVE[(tree, "real")] = res
    return _LIVE[tree]


def sources() -> list:
    """The captured buckets present for every tree, plus "live" when an engine is installed."""
    out = [b for b in sorted(BUCKETS.values()) if all(capture(b, t) is not None for t in TREES)]
    if _HAS_ENGINE:
        out.append(LIVE)
    return out


# ================================================================================================
# --capture / --dump
# ================================================================================================

def do_capture() -> int:
    if not _HAS_ENGINE:
        print("--capture: graphify is not installed")
        return 1
    ver = GX.graphify_version()
    b = BUCKETS.get(ver)
    if b is None:
        print(f"--capture: graphify {ver} is not one of {sorted(BUCKETS)}")
        return 1
    for name in TREES:
        cur = capture(b, name)
        if cur is not None and cur.get("tree_sha256") == tree_sha(name) and cur.get("engine") == ver:
            print(f"--capture: {fixture_path(b, name).relative_to(REPO)} is current (kept)")
            continue
        g = live_graph(name)
        if g is None or _LIVE[(name, "real")].engine != "graphify":
            print(f"--capture: the engine failed on {name}")
            return 1
        p = fixture_path(b, name)
        p.parent.mkdir(parents=True, exist_ok=True)
        doc = {"_about": (f"REAL `graphify update --no-cluster` output (graphify {ver}) of "
                          f"tests/typed_calls_shapes_gates.TREES[{name!r}] (absolute paths made root-relative; "
                          "map_graph reads none of them); re-capture with `python tests/typed_calls_shapes_gates.py "
                          "--capture` under this engine."),
               "engine": ver, "tree_sha256": tree_sha(name), "graph": _relative(g, root_for(name))}
        p.write_bytes((json.dumps(doc, indent=1, sort_keys=True) + "\n").encode("utf-8"))
        print(f"--capture: wrote {p.relative_to(REPO)} ({len(g.get('nodes', []))} nodes, "
              f"{len(g.get('links', []))} links)")
    return 0


def do_dump() -> int:
    srcs = sources()
    only = [t for t in os.environ.get("S4_DUMP_TREES", "").split(",") if t] or list(TREES)
    vs = [v for v in os.environ.get("S4_DUMP_VARIANTS", "").split(",") if v] or list(VARIANTS)
    for tree in only:
        for src in srcs:
            for v in vs:
                r = run(src, tree, v)
                st = r.stats or {}
                print(f"== {tree} [{src} {v}] engine={r.engine} edges={len(r.edges)} "
                      f"err={st.get('error')} files={st.get('files')} failed={st.get('files_failed')} "
                      f"disabled={st.get('langs_disabled')}")
                by = {}
                for (a, b), g in sorted(r.edges.items()):
                    by.setdefault(r.ident(a), []).append(f"{r.ident(b)}={g}")
                for c in sorted(set(by) | {r.ident(n["key"]) for n in r.nodes if n.get("call_typed")}):
                    ty = r.typed(c)
                    wk = r.weak(c)
                    print(f"   {c}: {by.get(c, [])}" + (f"  TYPED {ty}" if ty else "")
                          + (f"  WEAK {sorted(wk)}" if wk else ""))
    return 0


# ================================================================================================
# The cases (design section 5), on the FINAL graph
# ================================================================================================

def _m(f, n):
    return f"{f}::{n}"


ALL = ("0839", "0851", LIVE)
_C, _B, _TV, _P6 = "app/core.py", "app/bind.py", "app/tvar.py", "app/pep695.py"
_MOD, _MODS, _MAYBE, _TRY = "app/models.py", "app/mods.py", "app/maybe.py", "app/tryimp.py"
_FLD, _OPEN, _AUD = "app/fields.py", "app/open.py", "app/audit.py"
_TM, _JREQ = "src/m.ts", "src/req.js"
X, I, A = "EXTRACTED", "INFERRED", "AMBIGUOUS"


def none(tree, caller, m, prior=ALL, why=""):
    return {"kind": "none", "tree": tree, "caller": caller, "m": m, "prior": prior, "why": why}


def pos(tree, caller, m, typed, edges=None, why=""):
    return {"kind": "pos", "tree": tree, "caller": caller, "m": m, "typed": typed,
            "edges": typed if edges is None else edges, "why": why}


def gone(tree, caller, m, why=""):
    return {"kind": "gone", "tree": tree, "caller": caller, "m": m, "why": why}


def keep(tree, caller, m, allow, why=""):
    return {"kind": "keep", "tree": tree, "caller": caller, "m": m, "allow": allow, "why": why}


def weak(tree, caller, m, target, why=""):
    return {"kind": "weak", "tree": tree, "caller": caller, "m": m, "target": target, "why": why}


def noweak(tree, caller, m, why=""):
    return {"kind": "noweak", "tree": tree, "caller": caller, "m": m, "why": why}


def never(tree, target, from_ext=None, why=""):
    return {"kind": "never", "tree": tree, "target": target, "from_ext": from_ext, "why": why}


def noedge(tree, caller, target, why=""):
    return {"kind": "noedge", "tree": tree, "caller": caller, "target": target, "why": why}


def family(tree, caller, m, kept, dropped, why=""):
    return {"kind": "family", "tree": tree, "caller": caller, "m": m, "kept": kept, "dropped": dropped, "why": why}


def policy(tree, caller, m, typed, production, why=""):
    return {"kind": "policy", "tree": tree, "caller": caller, "m": m, "typed": typed, "production": production,
            "why": why}


def pfix(tree, caller, m, edges, why=""):
    return {"kind": "pfix", "tree": tree, "caller": caller, "m": m, "edges": edges, "why": why}


def psame(tree, caller, m, why=""):
    return {"kind": "psame", "tree": tree, "caller": caller, "m": m, "why": why}


CASES = [
    # ---- Python: rebinding and binding forms (the in-file name fan-out is the tuple a wrong removal would lose)
    *[none("py_bind", _m(_C, f), m, why=w) for f, m, w in (
        ("mn_rebind", "save", "x = Order(); x = make(); x.save()"),
        ("mn_del", "save", "del x"),
        ("mn_for", "save", "for o in orders: o.save()"),
        ("mn_with", "acquire", "with Lock() as l: l.acquire()"),
        ("mn_except", "save", "except E as e: e.save()"),
        ("mn_walrus", "save", "walrus"),
        ("mn_global", "save", "global"),
        ("mn_nonlocal", "save", "nonlocal"),
        ("mn_tuple", "save", "h, g = f()"),
        ("mn_comprehension", "save", "a comprehension target shadowing a typed outer name"),
        ("mn_any", "save", "x: Any"),
        ("mn_union_str", "save", "x: Union[Order, str]"),
        ("mn_pipe_str", "save", "x: Order | str"),
        ("mn_union_any", "save", "Union[Order, Any]"),
        ("mn_list", "append", "List[Order] -> orders.append is unbound"),
        ("mn_typevar", "save", "x: T (a TypeVar)"),
        ("mn_unparseable", "save", "an unparseable string annotation"),
        ("mn_unannotated", "save", "an unannotated parameter (`request`)"))],
    none("py_bind", _m(_TV, "mn_typevar_named_like_class"), "save", why="a TypeVar named like a class"),
    none("py_bind", _m(_P6, "mn_pep695"), "store", why="a PEP 695 parameter named like a class (Order.store exists)"),
    none("py_bind", _m(_B, "mn_cross_unannotated"), "save", prior=(), why="cross-file, unannotated"),
    pos("py_bind", _m(_C, "pos_local"), "save", {_m(_C, "Order.save"): X}, why="sibling: x = Order(); x.save()"),
    pos("py_bind", _m(_C, "pos_param"), "save", {_m(_C, "Order.save"): X}, why="sibling: x: Order"),
    pos("py_bind", _m(_C, "pos_append"), "append", {_m(_C, "Order.append"): X}, why="sibling: o: Order -> o.append"),
    pos("py_bind", _m(_C, "pos_acquire"), "acquire", {_m(_C, "Order.acquire"): X}, why="sibling: o.acquire()"),
    pos("py_bind", _m(_B, "pos_cross_param"), "save", {_m(_C, "Order.save"): I},
        why="sibling across files: a pure addition"),
    pos("py_bind", _m(_C, "cand_if_else"), "save", {_m(_C, "Order.save"): A, _m(_C, "Other.save"): A},
        why="if c: x = A() else: x = B() — a candidate set NARROWS the fan-out"),
    none("py_bind", _m(_B, "cand_cross_if_else"), "save", prior=(),
         why="the same if/else with nothing to narrow: a candidate set never creates a fan-out"),
    # ---- Python: ambiguous resolution and module receivers
    none("py_resolve", _m(_MAYBE, "mn_type_checking_else"), "save", why="if TYPE_CHECKING: import / else: = object"),
    none("py_resolve", _m(_TRY, "mn_try_two_classes"), "save", why="try/except ImportError: two classes"),
    pos("py_resolve", _m(_TRY, "pos_try_ctor_free"), "save", {_m(_TRY, "Spare2.save"): X},
        why="sibling in the same file"),
    pos("py_resolve", _m("app/pick.py", "pick_b"), "find", {_m("b/repo.py", "Repo.find"): I},
        why="Repo in a/ and b/, `from b.repo import Repo` -> only b's"),
    never("py_resolve", _m("a/repo.py", "Repo.find"), why="the other package's Repo"),
    gone("py_resolve", _m(_MODS, "ext_search"), "search", why="re.search() is external"),
    gone("py_resolve", _m(_MODS, "ext_join"), "join", why="os.path.join() is external"),
    none("py_resolve", _m(_MODS, "mn_path_loaded"), "main", prior=("0839",),
         why="a path-loaded `CG = _load('x'); CG.main()` is unknown"),
    none("py_resolve", _m(_MODS, "mn_class_alias"), "create", why="x = Order; x.create()"),
    pos("py_resolve", _m(_MODS, "pos_classref"), "create", {_m(_MOD, "Order.create"): I},
        why="sibling: Order.create() (K) — 0.8.51 alone links ClassName.m() itself; both ends converge"),
    pos("py_resolve", _m("tools/main.py", "main"), "run", {_m("tools/lr.py", "run"): I},
        why="import a.b as LR; LR.run() -> the module function (0.8.51 alone binds the class LR.run)"),
    never("py_resolve", _m("tools/legacy.py", "LR.run"), why="the capitalised alias is never the class LR"),
    pos("py_resolve", _m("src/flask/app.py", "Flask.respond"), "dumps", {_m("src/flask/json/__init__.py", "dumps"): I},
        why="sibling: `from . import json as fjson; fjson.dumps()` is flask's own json"),
    *[none("py_resolve", _m(f, c), m, prior=(), why="flask's `import json; json.%s()` (json is a project dir)" % m)
      for f, c, m in (("src/flask/json/provider.py", "DefaultJSONProvider.dumps", "dumps"),
                      ("src/flask/json/provider.py", "DefaultJSONProvider.loads", "loads"),
                      ("src/flask/json/tag.py", "TaggedJSONSerializer.dumps", "dumps"),
                      ("src/flask/json/tag.py", "TaggedJSONSerializer.loads", "loads"))],
    *[noedge("py_resolve", _m(f, c), _m("src/flask/json/__init__.py", m),
             why="no edge to flask.json.%s in ANY variant (CALLS_PLUS included)" % m)
      for f, c, m in (("src/flask/json/provider.py", "DefaultJSONProvider.dumps", "dumps"),
                      ("src/flask/json/provider.py", "DefaultJSONProvider.loads", "loads"),
                      ("src/flask/json/tag.py", "TaggedJSONSerializer.dumps", "dumps"),
                      ("src/flask/json/tag.py", "TaggedJSONSerializer.loads", "loads"))],
    # ---- Python: fields, attributes, open or unknown classes
    none("py_class", _m(_FLD, "Holder.mn_field_callable"), "cb", why="self.cb() where cb is a field"),
    none("py_class", _m(_FLD, "Holder.mn_untyped_injection"), "save", why="self.repo = repo (untyped parameter)"),
    none("py_class", _m(_FLD, "TwoClasses.mn_field_two_classes"), "save", why="self.item assigned two classes"),
    none("py_class", _m(_FLD, "mn_property_called"), "save", why="a property called as obj.prop()"),
    pos("py_class", _m(_FLD, "Holder.pos_owned"), "save", {_m(_MOD, "Order.save"): I},
        why="sibling: self.owned = Order() (removes the engine's in-file WithProp.save pick)"),
    *[none("py_class", _m(_OPEN, f), m, why=w) for f, m, w in (
        ("mn_getattr_class", "save", "a class with __getattr__"),
        ("mn_metaclass", "save", "a metaclass"),
        ("mn_decorated_class", "save", "an unknown class decorator"),
        ("mn_patched", "save", "a module-level C.m = patched"),
        ("ExtBase.mn_super_external", "save", "super().m() over an external base"),
        ("mn_dunder", "__enter__", "dunders"))],
    pos("py_class", _m(_OPEN, "pos_plain"), "save", {_m(_OPEN, "Plain.save"): X}, why="sibling: a closed class"),
    weak("py_class", _m(_AUD, "audit_untyped"), "notify", _m(_AUD, "Notifier.notify"),
         why="an untyped nt.notify() with two repo-wide notify methods IS demoted"),
    noweak("py_class", _m(_AUD, "Notifier.uses_helper"), "helper", why="self.helper(); b.helper() keeps its grade"),
    noweak("py_class", _m(_AUD, "Map.add"), "bind", why="Map.bind(self, x) via the class is not demoted"),
    # ---- TypeScript: types that must not bind (graphify's in-file pick is the last `find`: Tail.find)
    *[none("ts_shapes", _m(_TM, f), "find", why=w) for f, w in (
        ("viaInterface", "r: IRepo (an interface SqlRepo implements)"),
        ("viaUnion", "r: Repo | Cache"),
        ("viaCast", "(x as Repo).find()"),
        ("viaAny", "a: any"),
        ("viaUnknown", "unknown"),
        ("viaDestructure", "typed destructuring"),
        ("viaRebind", "let r = new Repo(); r = other"),
        ("viaFunctionThis", "this in a function(){} outside a class"),
        ("Holder.viaFnExpr", "this in a function(){} inside a method"),
        ("Holder.computed", "this['repo'].find()"))],
    none("ts_shapes", _m(_TM, "Holder.viaObjectMethod"), "find", prior=(),
         why="this in an object-literal method (graphify attributes its calls to no caller)"),
    none("ts_shapes", _m(_TM, "viaInterfaceMissing"), "extra",
         why="an interface is open: a member it does not declare is never `absent`"),
    none("ts_shapes", _m(_TM, "Box.hold"), "run", why="Box<T> while a class T exists (x: T is the type parameter)"),
    never("ts_shapes", _m(_TM, "SqlRepo.find"), why="an interface receiver never reaches an implementer by type"),
    *[keep("ts_shapes", _m(_TM, f), "find", {_m(_TM, "Repo.find")}, why=w) for f, w in (
        ("Caller.viaCall", "this.find.call(this) is no call to type (blocks the REPLACE)"),
        ("Caller.viaBind", ".bind"), ("Caller.viaApply", ".apply"))],
    pos("ts_shapes", _m(_TM, "Caller.posSelf"), "find", {_m(_TM, "Repo.find"): X},
        why="sibling: this.find() alone (S, inherited)"),
    pos("ts_shapes", _m(_TM, "posParam"), "find", {_m(_TM, "Repo.find"): X}, why="sibling: r: Repo"),
    pos("ts_shapes", _m(_TM, "posT"), "run", {_m(_TM, "T.run"): X}, why="sibling: x: T outside the generic"),
    pos("ts_shapes", _m(_TM, "Holder.posField"), "find", {_m(_TM, "Repo.find"): X}, why="sibling: this.repo.find()"),
    pos("ts_shapes", _m(_TM, "Holder.posOptional"), "find", {_m(_TM, "Repo.find"): X},
        why="optional chaining this.repo?.find()"),
    pos("ts_shapes", _m("src/pos.ts", "posCrossParam"), "find", {_m(_TM, "Repo.find"): I}, why="sibling across files"),
    pos("ts_shapes", _m("src/pos.ts", "posModule"), "helper", {_m(_TM, "helper"): I},
        why="sibling: import * as m; m.helper()"),
    pos("ts_shapes", _m("src/neg.ts", "posNew"), "Order", {_m(_TM, "Order"): I}, why="sibling: new Order()"),
    none("ts_shapes", _m("src/neg.ts", "typeOnlyValue"), "Order", prior=(), why="an `import type` used as a value"),
    none("ts_shapes", _m("src/view.tsx", "View"), "Order", prior=(), why="JSX <Order/> is no constructor call"),
    never("ts_shapes", _m("src/logcls.ts", "Log"), why="a decorator (`@Log`) is no call to type"),
    # (a method named like a builtin — map, max — gets no engine tuple at all: graphify skips those names)
    gone("ts_shapes", _m("src/pkg.ts", "viaPackage"), "crunch", why="a namespace import of a package is external"),
    gone("ts_shapes", _m("src/pkg.ts", "viaBuiltin"), "crunch", why="a member of a builtin global is external"),
    gone("ts_shapes", _m("src/lone.ts", "Lone.run"), "save",
         why="this.save() where save lives only in an unrelated in-file class: removed in .ts (absent)"),
    none("ts_shapes", _m("src/lone.js", "LoneJs.run"), "save", why="... and unknown in .js (never absent)"),
    pos("ts_shapes", _m(_JREQ, "useLib"), "find", {_m("src/lib.js", "Repo.find"): I},
        why="JS: const { Repo } = require(); new Repo().find() (the in-file Local.find pick removed)"),
    none("ts_shapes", _m(_JREQ, "jsdoc"), "find", why="JSDoc types are not read"),
    # ---- TS: duplicate class names across workspace packages
    pos("ts_ws", _m("src/use.ts", "pickB"), "find", {_m("packages/b/src/repo.ts", "Repo.find"): I},
        why="tsconfig paths pick packages/b"),
    never("ts_ws", _m("packages/a/src/repo.ts", "Repo.find"), why="never the other workspace package"),
    none("ts_ws", _m("src/use.ts", "viaUnmapped"), "find", why="an alias no config maps"),
    # ---- Python Order vs TS Order; the family filter (VF)
    pos("mix", _m("app/py_order.py", "py_ctor"), "Order", {_m("app/py_order.py", "Order"): X},
        why="a Python Order() binds the Python class (the TS Order fan-out removed)"),
    none("mix", _m("app/no_order.py", "mn_ts_order"), "Order", prior=(),
         why="a Python Order() with no Python class in reach while a TS Order exists"),
    never("mix", _m("web/order.ts", "Order"), from_ext=".py", why="a Python caller never types a TS class"),
    family("mix", _m("app/py_order.py", "py_ctor"), "Order", {_m("app/py_order.py", "Order")},
           {_m("web/order.ts", "Order")}, why="py -> ts dropped"),
    family("mix", _m("web/use.ts", "useShared"), "shared", {_m("web/util.js", "shared")},
           {_m("app/py_order.py", "shared")}, why=".ts -> .js kept, .ts -> .py dropped"),
    family("mix", _m("app/views/orders/show.html.erb", "orders/show#module"), "total_label",
           {_m("app/helpers/orders_helper.rb", "total_label")}, set(), why=".erb -> .rb is never filtered"),
    family("mix", _m("tools/gen.xyz", "gen_main"), "shared",
           {_m("web/util.js", "shared"), _m("app/py_order.py", "shared")}, set(),
           why="a caller with an unknown extension is never filtered"),
    # ---- the caller policy: non-production paths by DIRECTORY component
    *[policy("nonprod", _m(f, c), "helper", {_m("app/svc.py", "helper"): I}, True, why=w) for f, c, w in (
        ("examples.py", "run_example", "examples.py is production"),
        ("src/docs_parser.py", "parse_docs", "src/docs_parser.py is production"),
        ("mydocs/guide.py", "run_guide", "mydocs/ is production"))],
    *[policy("nonprod", _m(f, c), "helper", {_m("app/svc.py", "helper"): I}, False, why=w) for f, c, w in (
        ("examples/demo.py", "run_demo", "examples/ is non-production: corrections only"),
        ("docs/conf.py", "run_docs", "docs/ is non-production"),
        ("tests/test_svc.py", "test_helper", "a test caller: corrections only"))],
    pos("nonprod", _m("examples/fixed.py", "run_fixed"), "helper", {_m("app/svc.py", "helper"): I},
        why="a non-production caller with a prior tuple IS corrected"),
    # ---- the precise fixes (VP vs VPx = DEEPMAP_PRECISE_FIXES=0)
    pfix("precise", _m("shop/svc.py", "once"), "Order", {_m("shop/models.py", "Order"): I},
         why="P1b: one Order( site — the engine's duplicate placeholder name dropped"),
    psame("precise", _m("shop/svc.py", "twice"), "Order", why="P1b: two Order( sites keep the name"),
    pfix("precise", _m("src/result.ts", "okOne"), "OK", {_m("src/result.ts", "OK@2"): X},
         why="TS re-key: type OK<T> + const OK -> the call binds the const"),
    pfix("precise", _m("src/useremote.ts", "viaRemote"), "Remote",
         {_m("src/remote.ts", "Remote"): A, _m("src/useremote.ts", "Remote"): A},
         why="the re-key never crosses files: the value of another file -> the name"),
]


# ================================================================================================
# Case evaluation
# ================================================================================================

def _lower_ok(base: dict, got: dict, weak_set: set) -> list:
    """The differences between `base` and `got` that are NOT an audit demotion (same targets; a grade lowered to
    AMBIGUOUS only where the audit named the target)."""
    bad = []
    for t in sorted(set(base) | set(got)):
        if base.get(t) == got.get(t):
            continue
        if t in base and t in got and got[t] == A and t in weak_set:
            continue
        bad.append((t, base.get(t), got.get(t)))
    return bad


def _present(src, case) -> bool:
    return run(src, case["tree"], "V0").has(case["caller"])


def eval_case(case: dict, src: str):
    """(ok, detail) for one case on one source."""
    tree, c, m = case["tree"], case.get("caller"), case.get("m")
    k = case["kind"]
    if k == "never":
        hits = []
        for v in ("VTa", "VT", "VPT", "V9", "VTnp"):
            r = run(src, tree, v)
            for n in r.nodes:
                if case["from_ext"] and not str(n.get("source_file", "")).endswith(case["from_ext"]):
                    continue
                for t in (n.get("call_typed") or {}):
                    if r.ident(t) == case["target"]:
                        hits.append((v, r.ident(n["key"])))
        tgt_ok = run(src, tree, "V0").has(case["target"])
        return tgt_ok and not hits, {"target present": tgt_ok, "typed from": hits}
    if not _present(src, case):
        return False, f"caller {c} is no node on {src}"
    v0 = run(src, tree, "V0")
    if k == "noedge":
        hits = [v for v in VARIANTS if (run(src, tree, v).by_ident.get(c), run(src, tree, v).by_ident.get(case["target"]))
                in run(src, tree, v).edges]
        return not hits and v0.has(case["target"]), {"edge in": hits}
    e0 = v0.m_edges(c, m)
    if k == "none":
        out, bad = {}, []
        for v in ("VTa", "VT", "VPT"):
            r = run(src, tree, v)
            if r.typed(c, m):
                bad.append(f"{v} typed {r.typed(c, m)}")
        ta = run(src, tree, "VTa").m_edges(c, m)
        if ta != e0:
            bad.append(f"VTa {ta} != V0 {e0}")
        vt = run(src, tree, "VT")
        d = _lower_ok(e0, vt.m_edges(c, m), vt.weak(c))
        if d:
            bad.append(f"VT vs V0 (beyond audit demotions): {d}")
        vp, vpt = run(src, tree, "VP"), run(src, tree, "VPT")
        d = _lower_ok(vp.m_edges(c, m), vpt.m_edges(c, m), vpt.weak(c))
        if d:
            bad.append(f"VPT vs VP (beyond audit demotions): {d}")
        if src in case["prior"] and not e0:
            bad.append("V0 holds no tuple of m here (the no-removal check would have no teeth)")
        out["V0"] = e0
        return not bad, bad or out
    if k == "pos":
        bad = []
        for v in ("VTa", "VT", "VPT"):
            r = run(src, tree, v)
            if r.typed(c, m) != case["typed"]:
                bad.append(f"{v} typed {r.typed(c, m)}")
            if r.m_edges(c, m) != case["edges"]:
                bad.append(f"{v} edges {r.m_edges(c, m)}")
        if e0 == case["edges"]:
            bad.append(f"V0 already {e0}: the sibling shows nothing")
        return not bad, bad or {"V0": e0}
    if k == "gone":
        bad = []
        for v in ("VTa", "VT", "VPT"):
            r = run(src, tree, v)
            if r.typed(c, m) or r.m_edges(c, m):
                bad.append(f"{v} typed {r.typed(c, m)} edges {r.m_edges(c, m)}")
        if not e0:
            bad.append("V0 holds no m edge: the removal shows nothing")
        return not bad, bad or {"V0": e0}
    if k == "keep":
        bad = []
        for v in ("VTa", "VT"):
            r = run(src, tree, v)
            ty, got = r.typed(c, m), r.m_edges(c, m)
            if set(ty) - set(case["allow"]):
                bad.append(f"{v} typed {ty}")
            for t, g in e0.items():
                want = ty.get(t, g)
                if got.get(t) != want and not (v == "VT" and got.get(t) == A and t in r.weak(c)):
                    bad.append(f"{v} {t}: {g} -> {got.get(t)}")
        if not e0:
            bad.append("V0 holds no m edge")
        return not bad, bad or {"V0": e0}
    if k == "weak":
        bad = []
        t = case["target"]
        for v in ("VA", "VT"):
            r = run(src, tree, v)
            if t not in r.weak(c) or r.m_edges(c, m).get(t) != A:
                bad.append(f"{v} weak {sorted(r.weak(c))} edges {r.m_edges(c, m)}")
        if e0.get(t) in (None, A):
            bad.append(f"V0 {e0}: nothing to demote")
        return not bad, bad or {"V0": e0}
    if k == "noweak":
        bad = []
        va = run(src, tree, "VA")
        if va.m_edges(c, m) != e0:
            bad.append(f"VA {va.m_edges(c, m)} != V0 {e0}")
        for v in ("VA", "VT"):
            r = run(src, tree, v)
            w = {t for t in r.weak(c) if member_of(r.node.get(r.by_ident.get(t), {}).get("name")) == m}
            if w:
                bad.append(f"{v} weak {sorted(w)}")
        if not e0:
            bad.append("V0 holds no m edge")
        return not bad, bad or {"V0": e0}
    if k == "family":
        vf = run(src, tree, "VF").m_edges(c, m)
        want0 = case["kept"] | case["dropped"]
        bad = []
        if set(e0) != want0:
            bad.append(f"V0 targets {sorted(e0)} != {sorted(want0)}")
        if set(vf) != case["kept"]:
            bad.append(f"VF targets {sorted(vf)} != {sorted(case['kept'])}")
        return not bad, bad or {"V0": e0, "VF": vf}
    if k == "policy":
        bad = []
        vt, vnp = run(src, tree, "VT"), run(src, tree, "VTnp")
        if case["production"]:
            if vt.typed(c, m) != case["typed"] or vt.m_edges(c, m) != case["typed"]:
                bad.append(f"VT typed {vt.typed(c, m)} edges {vt.m_edges(c, m)}")
        else:
            if vt.typed(c, m) or vt.m_edges(c, m) != e0:
                bad.append(f"VT typed {vt.typed(c, m)} edges {vt.m_edges(c, m)} (V0 {e0})")
            if vnp.typed(c, m) != case["typed"] or vnp.m_edges(c, m) != case["typed"]:
                bad.append(f"VTnp typed {vnp.typed(c, m)} edges {vnp.m_edges(c, m)}")
        if e0:
            bad.append(f"V0 {e0}: not a pure addition")
        return not bad, bad or {"V0": e0}
    if k in ("pfix", "psame"):
        vp, vx = run(src, tree, "VP").m_edges(c, m), run(src, tree, "VPx").m_edges(c, m)
        if k == "psame":
            return vp == vx and bool(vp), {"VP": vp, "VPx": vx}
        return vp == case["edges"] and vx != vp, {"VP": vp, "VPx": vx}
    return False, f"unknown kind {k}"


_KIND_TEXT = {"none": "no typed key, no removal", "pos": "typed exactly, the fan-out replaced",
              "gone": "external / absent: every m edge removed, nothing typed", "keep": "no removal",
              "weak": "audit demotes", "noweak": "audit leaves alone", "never": "never typed",
              "noedge": "no edge in any variant", "family": "VF", "policy": "caller policy",
              "pfix": "VP fix", "psame": "VP == VPx"}


_JS_EXTS = (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs")


def ts_grammars() -> bool:
    """Are the tree-sitter TS / JS grammars importable here? (an OPTIONAL dependency, pulled in by graphify: without
    them the TS / JS reader and the TS re-key are off by design — receiver_facts_ts.grammars_available)."""
    try:
        import receiver_facts_ts as rts
        fn = getattr(rts, "grammars_available", None)
        return bool(fn()) if callable(fn) else True
    except Exception:  # noqa: BLE001
        return False


def needs_ts(case) -> bool:
    """A case whose outcome depends on reading TS / JS source (typing or the re-key) — the family filter never does."""
    ref = case.get("caller") or case.get("target") or ""
    return case["kind"] != "family" and ref.split("::", 1)[0].lower().endswith(_JS_EXTS)


def sec_cases(srcs) -> None:
    print("\n-- C: every design-5 case on the FINAL graph (the live engine + both engines' captures) --")
    if TC is None:
        check("C: build/typed_calls.py is importable", False)
    ts_ok = ts_grammars()
    skipped = 0
    for case in CASES:
        label = case.get("caller") or case.get("target")
        tail = f" {case['m']}" if case.get("m") else ""
        if needs_ts(case) and not ts_ok:
            skipped += 1
            continue
        for src in srcs:
            try:
                ok, detail = eval_case(case, src)
            except Exception as exc:  # noqa: BLE001 — a crash is a FAIL with its reason
                ok, detail = False, f"{type(exc).__name__}: {exc}"
            check(f"C [{src}] {case['tree']} {label}{tail} — {_KIND_TEXT[case['kind']]}: {case['why']}", ok, detail)
    if skipped:
        REC.skip(f"C: {skipped} TypeScript / JavaScript case(s)", "the tree-sitter TS / JS grammars are not importable "
                 "here (an optional dependency graphify pulls in): the TS / JS reader and the TS re-key are off by "
                 "design")


# ================================================================================================
# A: the pipeline is sync's
# ================================================================================================

def _dotted(fn) -> str:
    if isinstance(fn, ast.Name):
        return fn.id
    if isinstance(fn, ast.Attribute):
        base = _dotted(fn.value)
        return f"{base}.{fn.attr}" if base else fn.attr
    return ""


def pipeline_steps(src: str) -> list:
    """The STEPS `_sync_pass` calls, in source order (consecutive repeats folded)."""
    tree = ast.parse(src)
    fn = next((n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_sync_pass"), None)
    if fn is None:
        return []
    calls = sorted((c for c in ast.walk(fn) if isinstance(c, ast.Call)), key=lambda c: (c.lineno, c.col_offset))
    out = []
    for c in calls:
        name = _dotted(c.func)
        if name in STEPS and (not out or out[-1] != name):
            out.append(name)
    return out


def push_first_step(src: str) -> bool:
    """push_to_neo4j's first computation is `edges = _resolve_calls(nodes, calls)` (what this gate's chain returns)."""
    tree = ast.parse(src)
    fn = next((n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "push_to_neo4j"), None)
    if fn is None:
        return False
    for st in fn.body:
        if isinstance(st, ast.Assign) and isinstance(st.value, ast.Call):
            return (_dotted(st.value.func) == "_resolve_calls"
                    and [getattr(a, "id", None) for a in st.value.args] == ["nodes", "calls"]
                    and [getattr(t, "id", None) for t in st.targets] == ["edges"])
    return False


class _Res(list):
    def single(self):
        return self[0] if self else None

    def consume(self):
        return None

    def data(self):
        return []


class _Sess:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, *a, **k):
        return _Res()

    def begin_transaction(self, *a, **k):
        return self

    def commit(self):
        return None

    def rollback(self):
        return None

    def close(self):
        return None


class _Drv:
    def session(self, *a, **k):
        return _Sess()

    def verify_connectivity(self):
        return None

    def close(self):
        return None


def real_sync(tree: str, graph: dict, variant: str):
    """The REAL sync() pass over a fresh copy of `tree` (graphify path; the engine subprocess hands back `graph`; the
    database faked): the edges push_to_neo4j received, or None."""
    root = write_tree(tmp() / "sync" / f"{tree}_{variant}", dict(TREES[tree], **{".deepmap-root": ""}))
    pushed = {}

    def push(driver, nodes, calls, imports, typed_edges=None):
        pushed["edges"] = S._resolve_calls(nodes, calls)
        pushed["stable"] = {n["key"]: label_of(n) for n in nodes}
        return len(nodes), len(pushed["edges"])

    stubs = dict(_graph_has_nodes=lambda d: False, _graph_holds_nodes=lambda d: False,
                 _graph_node_count=lambda d: 0, _prune_blast_radius=lambda d, live: (0, 0),
                 _cleanup_stale=lambda d, live: None, _reuse_rekeyed_embeddings=lambda d, ns: 0,
                 indexes=types.SimpleNamespace(create_all_indexes=lambda d: {},
                                               wait_for_indexes=lambda d, timeout=120: True, VECTOR_INDEXES=(),
                                               vector_quantization_disabled=lambda: False),
                 embeddings=types.SimpleNamespace(generate_embeddings=lambda d: 0),
                 _count_embedded_nodes=lambda d, fallback=0: 0, _maybe_auto_tune=lambda r: None,
                 push_to_neo4j=push)
    saved_uri = os.environ.get("NEO4J_URI")
    os.environ["NEO4J_URI"] = "bolt://127.0.0.1:9"
    try:
        with flags(VARIANTS[variant]), patched(S, **stubs), patched(GX, _run_engine=fake_engine(graph)), \
                engine_present(), quiet():
            ok = S.sync(root, driver=_Drv())
    finally:
        if saved_uri is None:
            os.environ.pop("NEO4J_URI", None)
        else:
            os.environ["NEO4J_URI"] = saved_uri
    if pushed.get("edges") is None:
        return ok, None, root
    st = pushed["stable"]
    return ok, sorted([st.get(a, a), st.get(b, b), g] for (a, b), g in pushed["edges"].items()), root


PIPELINE_MODULES = ("graphify_extract", "sync", "edge_resolve", "typescript_edges", "language_recognizers",
                    "typed_calls", "receiver_facts_py", "receiver_facts_ts", "py_import_resolver")


def sec_loaded() -> None:
    """Run LAST (the readers are imported lazily, by the first typed pass): a --root run must measure that tree."""
    print("\n-- A0: the code under test --")
    wrong = {}
    for name in PIPELINE_MODULES:
        mod = sys.modules.get(name)
        f = Path(getattr(mod, "__file__", "") or "").resolve() if mod is not None else None
        if f is None or f.parent != BUILD.resolve():
            wrong[name] = str(f) if f else "not imported"
    check(f"A0: every pipeline module was loaded from the build/ under test ({BUILD})", not wrong, wrong)


def sec_pipeline(srcs) -> None:
    print("\n-- A: the pipeline is sync's own (order from build/sync.py's AST; a real sync() pushes these edges) --")
    src = (BUILD / "sync.py").read_text(encoding="utf-8")
    got = pipeline_steps(src)
    check("A1: _sync_pass runs exactly this gate's steps, in this order: " + " -> ".join(STEPS),
          got == list(STEPS), got)
    check("A2: push_to_neo4j's first computation is `edges = _resolve_calls(nodes, calls)` (the final graph judged here)",
          push_first_step(src))
    bucket = next((b for b in srcs if b != LIVE), None)
    if bucket is None:
        REC.skip("A3: a real sync() pass", "no capture present")
        return
    for tree, variant in (("py_bind", "VT"), ("ts_shapes", "VPT"), ("py_resolve", "V0")):
        ok, edges, _root = real_sync(tree, capture(bucket, tree)["graph"], variant)
        mine = run(bucket, tree, variant)
        # (the two runs materialize the tree at different roots: compared on version-stable identities)
        same = edges is not None and edges == mine.canonical()["edges"]
        check(f"A3 [{bucket}] {tree} {variant}: a real sync() pass (graphify path, database faked) pushes exactly the "
              f"edges this gate computes ({len(mine.edges)})", ok is True and same,
              (ok, None if edges is None else len(edges), len(mine.edges)))


# ================================================================================================
# B: the captures and the installed engine
# ================================================================================================

def sec_captures(srcs) -> None:
    print("\n-- B: the captures (both engines) and the installed engine --")
    for b in sorted(BUCKETS.values()):
        missing = [t for t in TREES if capture(b, t) is None]
        stale = [t for t in TREES if capture(b, t) is not None and capture(b, t).get("tree_sha256") != tree_sha(t)]
        eng = {capture(b, t).get("engine") for t in TREES if capture(b, t) is not None}
        want = next(v for v, bb in BUCKETS.items() if bb == b)
        check(f"B1 {b}: every tree captured from THIS gate's tree by graphify {want} (re-capture after editing one)",
              not missing and not stale and eng == {want}, {"missing": missing, "stale": stale, "engines": eng})
    if not _HAS_ENGINE:
        REC.skip("B2/B3: the installed engine", "graphify is not installed")
        return
    ver = GX.graphify_version()
    b = BUCKETS.get(ver)
    print(f"  (installed: graphify {ver})")
    for tree in TREES:
        if live_graph(tree) is None or _LIVE[(tree, "real")].engine != "graphify":
            check(f"B2 {tree}: the installed engine extracts the tree", False, _LIVE[(tree, "real")].err[-600:])
            continue
        real = _LIVE[(tree, "real")]
        replay = run(LIVE, tree, "VT")
        check(f"B2 {tree}: the real `_rebuild_code` run and the replay of its own graph.json give the same final "
              f"graph (VT: the subprocess stand-in is transparent)", real.canonical() == replay.canonical())
        if b is None:
            REC.skip(f"B3 {tree}", f"no capture for graphify {ver}")
            continue
        diff = [v for v in ("V0", "VT", "VPT") if run(LIVE, tree, v).canonical() != run(b, tree, v).canonical()]
        check(f"B3 {tree}: the installed engine ({ver}) gives exactly its capture's final graph (V0, VT, VPT)",
              not diff, diff)


# ================================================================================================
# D: graph-wide invariants
# ================================================================================================

_TEXT = ("key", "label", "name", "source_file", "start_line", "body_summary", "documented")


def sec_invariants(srcs) -> None:
    print("\n-- D: graph-wide invariants on every source and variant --")
    for src in srcs:
        bad = {"marked": [], "xfile": [], "grades": [], "text": [], "hidden": []}
        for tree in TREES:
            text0 = [tuple(n.get(f) for f in _TEXT) for n in run(src, tree, "V0").nodes]
            for v in VARIANTS:
                r = run(src, tree, v)
                handed = {(a, str(b)) for a, b in r.calls if GX.is_call_key(b)}
                file_of = {n["key"]: n.get("source_file") for n in r.nodes}
                for n in r.nodes:
                    for t, g in (n.get("call_typed") or {}).items():
                        want = A if t in (n.get("call_weak") or ()) else g
                        if (n["key"], t) not in handed or r.edges.get((n["key"], t)) != want:
                            bad["marked"].append((tree, v, r.ident(n["key"]), r.ident(t), g, r.edges.get((n["key"], t))))
                        if g == X and file_of.get(t) != n.get("source_file"):
                            bad["xfile"].append((tree, v, r.ident(n["key"]), r.ident(t)))
                if set(r.edges.values()) - {X, I, A}:
                    bad["grades"].append((tree, v, sorted(set(r.edges.values()))))
                if [tuple(n.get(f) for f in _TEXT) for n in r.nodes] != text0:
                    bad["text"].append((tree, v))
            if run(src, tree, "V0").canonical() != run(src, tree, "V0", hide=True).canonical():
                bad["hidden"].append(tree)
        check(f"D1 [{src}]: every typed key reaches the push marked (CallKey) and its edge carries exactly its "
              f"call_typed grade (AMBIGUOUS where the audit named it)", not bad["marked"], bad["marked"][:5])
        check(f"D2 [{src}]: no cross-file EXTRACTED typed edge (design 4)", not bad["xfile"], bad["xfile"][:5])
        check(f"D3 [{src}]: only the three grade strings reach the push", not bad["grades"], bad["grades"][:5])
        check(f"D4 [{src}]: node text (key, label, name, file, line, summary) identical across every variant "
              f"(edge-only)", not bad["text"], bad["text"][:5])
        check(f"D5 [{src}]: every flag off (V0) == build/typed_calls.py unimportable, final graph byte for byte",
              not bad["hidden"], bad["hidden"])
        no_ts = () if ts_grammars() else ("ts_shapes", "ts_ws")     # (TS / JS typing is off without the grammars)
        vac = [t for t in TREES if run(src, t, "VT").canonical()["edges"] == run(src, t, "V0").canonical()["edges"]
               and t not in ("precise",) + no_ts]
        check(f"D6 [{src}]: not vacuous — TYPED changes the final graph of every shape tree", not vac, vac)


# ================================================================================================
# E: the on/off effect diff (the artefact)
# ================================================================================================

def effect_diff(src: str, tree: str, base="V0", var="VT") -> dict:
    a, b = run(src, tree, base), run(src, tree, var)
    ea = {(a.ident(x), a.ident(y)): g for (x, y), g in a.edges.items()}
    eb = {(b.ident(x), b.ident(y)): g for (x, y), g in b.edges.items()}
    return {"added": sorted([*p, eb[p]] for p in set(eb) - set(ea)),
            "removed": sorted([*p, ea[p]] for p in set(ea) - set(eb)),
            "regraded": sorted([*p, ea[p], eb[p]] for p in set(ea) & set(eb) if ea[p] != eb[p])}


def sec_diff(srcs) -> None:
    print("\n-- E: the on/off effect diff (V0 -> VT, final graph) per tree and source --")
    out = {}
    for src in srcs:
        for tree in TREES:
            d = effect_diff(src, tree)
            out[f"{src}/{tree}"] = d
            print(f"  [{src}] {tree}: +{len(d['added'])} -{len(d['removed'])} ~{len(d['regraded'])}")
    first = next((s for s in srcs if s != LIVE), None)
    if first is not None:
        for tree in TREES:
            d = out[f"{first}/{tree}"]
            for row in d["added"]:
                print(f"     {tree} + {row[0]} -> {row[1]} {row[2]}")
            for row in d["removed"]:
                print(f"     {tree} - {row[0]} -> {row[1]} {row[2]}")
            for row in d["regraded"]:
                print(f"     {tree} ~ {row[0]} -> {row[1]} {row[2]} -> {row[3]}")
    buckets = [s for s in srcs if s != LIVE]
    if len(buckets) == 2:
        differ = [t for t in TREES if out[f"{buckets[0]}/{t}"] != out[f"{buckets[1]}/{t}"]]
        print(f"  (trees whose V0 -> VT effect differs between the engine ends: {differ or 'none'})")
    if DIFF_OUT:
        Path(DIFF_OUT).parent.mkdir(parents=True, exist_ok=True)
        Path(DIFF_OUT).write_text(json.dumps(out, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(f"  (written: {DIFF_OUT})")
    check("E: the effect diff was computed for every tree on every source", len(out) == len(TREES) * len(srcs))


# ================================================================================================

def main() -> int:
    try:
        if MODE == "capture":
            return do_capture()
        if MODE == "dump":
            return do_dump()
        ver = GX.graphify_version() if _HAS_ENGINE else "(not installed)"
        print(f"typed_calls_shapes_gates (S4): Python {sys.version.split()[0]}, graphify {ver}, build {BUILD}")
        srcs = sources()
        check("both engines' captures of every tree are present (tests/fixtures/typed_calls/negative/{0839,0851})",
              [s for s in srcs if s != LIVE] == ["0839", "0851"], srcs)
        sec_pipeline(srcs)
        sec_captures(srcs)
        sec_cases(srcs)
        sec_invariants(srcs)
        sec_diff(srcs)
        sec_loaded()
        print(f"\nRESULT: {REC.passed} passed, {REC.failed} failed, {REC.skipped} skipped")
        if REC.failed:
            print("FAILED:\n  " + "\n  ".join(REC.fails[:80]))
        print("GREEN" if not REC.failed else "RED")
        return 0 if not REC.failed else 1
    finally:
        cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
