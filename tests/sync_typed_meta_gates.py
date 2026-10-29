#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j gates: every successful push records WHICH typed-call languages linked this graph.

deepmap_impact words its C# note by whether the graph's calls through a receiver were linked (opt-in
DEEPMAP_TYPED_CALLS=csharp) or not. The graph itself has to say so: after every push that wrote nodes, sync sets
`typed_call_langs` on the :_DeepMapMeta {id:'singleton'} node to the sorted list of the typed-call languages that
actually ran (enabled and not disabled by the pass; [] when the pass did not run or failed). A graph without the
property was built by an older DeepMap and reads as "unknown" (not linked).

  M1  sync._typed_call_langs (pure): None / {} / a failed run -> []; disabled languages dropped; the audit's
      languages never count (the audit only demotes); sorted, de-duplicated, str only; never raises.
  M2  a real sync() pass (ast engine, faked database): every flag off -> one write of [] on the singleton, after
      the owner stamp, matched by id; a fake that reads every `MERGE (m:_DeepMapMeta` as the owner stamp (as
      switch_guard / sync_owner_predicate do) still reads this project as the owner.
  M3  the typed pass stubbed with stats -> the enabled languages minus the disabled ones; a failed pass -> [].
  M4  the REAL typed pass (DEEPMAP_TYPED_CALLS=python, when build/typed_calls.py is importable) -> ["python"].
  M5  nothing pushed (an empty tree) -> no write at all.
  M6  the write fails -> the sync still succeeds (the push and the owner stamp stand), and the stale value is
      removed so an older ["csharp"] can never outlive the sync that stopped linking.
  M7  mutation teeth: each mutant of build/sync.py must turn a check RED.

Beside it, `typed_call_narrowed` (review F4): True when typed linking ran for a language with
DEEPMAP_TYPED_CALLS_SHAPES selecting fewer than all typed_calls.SHAPES, or, for a language whose
LanguageSpec.lookup_rules holds iface_dispatch (C#), DEEPMAP_TYPED_CALLS_IFACE selecting fewer than all
typed_calls.IFACE_MODES. Read only through typed_calls' own readers (_config(...).shapes, _iface_modes(...)), so a
change to what a value MEANS there (an ON word, an unknown token) follows automatically; written in the SAME
statement as typed_call_langs (null, i.e. absent = unknown, when it cannot be told) and removed with it on failure.
  N1  sync._typed_call_narrowed (pure, the real readers): nothing ran -> False; every shape / every mode -> False;
      fewer shapes -> True for any language; fewer interface modes -> True only when a language with the
      iface_dispatch rule ran; any other IFACE value agrees with typed_calls._iface_modes, whatever it decides.
      Teeth through a stand-in typed_calls: the READER decides, not the environment; a reader that fails, or no
      typed_calls at all, -> None.
  N2  sync._write_typed_call_langs: one SET of both properties on the singleton; unknown -> SET to null; a failed
      write REMOVEs both.
  N3  a real sync() pass: every flag off -> False; the typed pass stubbed (csharp ran) under
      DEEPMAP_TYPED_CALLS_IFACE=static -> True, and under DEEPMAP_TYPED_CALLS_SHAPES=R1 -> True.
  N4  mutation teeth for N1 / N2.

Hermetic: temp trees, a temp DEEPMAP_HOME, a recording fake driver; no Neo4j, no network.
usage: python tests/sync_typed_meta_gates.py
"""
from __future__ import annotations

import contextlib
import io
import os
import shutil
import sys
import tempfile
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build"
sys.path.insert(0, str(BUILD))

_PASS = _FAIL = 0
_TMP: list = []

OFF = {"DEEPMAP_TYPED_CALLS": "0", "DEEPMAP_CALLS_AUDIT": "0", "DEEPMAP_PRECISE_CALLS": "0",
       "DEEPMAP_CALLS_FAMILY_FILTER": "0", "DEEPMAP_GRAPHIFY_CALLS_PLUS": "0", "DEEPMAP_LOCAL_DEF_CALLS": "0",
       "DEEPMAP_DROP_APP_TO_TEST_CALLS": "0", "DEEPMAP_FALSE_CALL_FILTER": "1", "DEEPMAP_EXCLUDE_TESTS": "0",
       "DEEPMAP_INCREMENTAL_SYNC": "0", "DEEPMAP_TYPED_CALLS_SHAPES": None, "DEEPMAP_TYPED_CALLS_IFACE": None}
SHAPES_ENV, IFACE_ENV = "DEEPMAP_TYPED_CALLS_SHAPES", "DEEPMAP_TYPED_CALLS_IFACE"

TREE = {".deepmap-root": "",
        "pkg/__init__.py": "",
        "pkg/models.py": "class Order:\n    def save(self):\n        return 1\n",
        "pkg/service.py": "from pkg.models import Order\n\n\ndef place():\n    o = Order()\n    return o.save()\n"}


class Rec:
    def __init__(self, loud=True):
        self.loud, self.failed, self.labels = loud, 0, []

    def check(self, label, ok, detail=""):
        global _PASS, _FAIL
        if self.loud:
            if ok:
                _PASS += 1
                print(f"  [PASS] {label}")
            else:
                _FAIL += 1
                print(f"  [FAIL] {label}{(' — ' + str(detail)[:600]) if detail != '' else ''}")
        if not ok:
            self.failed += 1
            self.labels.append(label)
        return bool(ok)


LOUD = Rec()


def tmp() -> Path:
    d = Path(tempfile.mkdtemp(prefix="dm_typedmeta_"))
    _TMP.append(d)
    return d


@contextlib.contextmanager
def flags(**kv):
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


def load_module(path: Path, name: str, source: "str | None" = None):
    mod = types.ModuleType(name)
    mod.__file__ = str(path)
    code = compile(source if source is not None else path.read_text(encoding="utf-8"), str(path), "exec")
    sys.modules[name] = mod
    try:
        exec(code, mod.__dict__)  # noqa: S102 — this repository's own module (or a mutant of it)
    finally:
        sys.modules.pop(name, None)
    return mod


def write_tree(root: Path, files: dict) -> Path:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode("utf-8"))
    return root


# ---- a recording fake driver; reads every `MERGE (m:_DeepMapMeta` as the owner stamp, like the owner gates --
class _Res(list):
    def single(self):
        return self[0] if self else None

    def consume(self):
        return None

    def data(self):
        return []


class _Sess:
    def __init__(self, drv):
        self.drv = drv

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, cypher, **p):
        d = self.drv
        d.stmts.append((cypher, p))
        if d.fail_on and d.fail_on in cypher:
            raise RuntimeError("simulated failure of the meta write")
        if "_DeepMapMeta" in cypher and "owning_root" in cypher and "RETURN" in cypher:
            return _Res([{"owning_root": d.owner}] if d.owner else [])
        if "MERGE (m:_DeepMapMeta" in cypher:            # what switch_guard / sync_owner_predicate read
            d.owner = p.get("root")
            return _Res()
        if "count(n)" in cypher:
            return _Res([{"c": 0}])
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
    def __init__(self, fail_on=None):
        self.stmts, self.owner, self.fail_on = [], None, fail_on

    def session(self, *a, **k):
        return _Sess(self)

    def verify_connectivity(self):
        return None

    def close(self):
        return None


def meta_writes(drv) -> list:
    """[(index, cypher, params)] of every statement that touches typed_call_langs."""
    return [(i, c, p) for i, (c, p) in enumerate(drv.stmts) if "typed_call_langs" in c]


def owner_index(drv) -> int:
    idx = [i for i, (c, p) in enumerate(drv.stmts) if "MERGE (m:_DeepMapMeta" in c and "owning_root" in c]
    return idx[-1] if idx else -1


def real_sync(Sm, root: Path, env: dict, *, drv=None, typed_stub=None):
    """The REAL sync() pass on `root` (ast engine) with the database faked (push_to_neo4j stubbed to report what it
    was handed). Returns (ok, driver, stderr)."""
    Sm._graph_has_nodes = lambda d: False
    Sm._graph_holds_nodes = lambda d: False
    Sm._graph_node_count = lambda d: 0
    Sm._prune_blast_radius = lambda d, live: (0, 0)
    Sm._cleanup_stale = lambda d, live: None
    Sm._reuse_rekeyed_embeddings = lambda d, ns: 0
    Sm.indexes = types.SimpleNamespace(create_all_indexes=lambda d: {}, wait_for_indexes=lambda d, timeout=120: True,
                                       VECTOR_INDEXES=(), vector_quantization_disabled=lambda: False)
    Sm.embeddings = types.SimpleNamespace(generate_embeddings=lambda d: 0)
    Sm._count_embedded_nodes = lambda d, fallback=0: 0
    Sm._maybe_auto_tune = lambda r: None
    Sm.push_to_neo4j = lambda driver, nodes, calls, imports, typed_edges=None: (len(nodes), len(calls))
    if typed_stub is not None:
        Sm._typed_calls_link = typed_stub
    drv = drv if drv is not None else _Drv()
    home = tmp() / "home"
    full = dict(env, DEEPMAP_EXTRACTOR="ast", DEEPMAP_HOME=str(home), DEEPMAP_AUTO_GITIGNORE="0",
                NEO4J_URI="bolt://127.0.0.1:9")
    err = io.StringIO()
    with flags(**full), contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
        ok = Sm.sync(root, driver=drv)
    return ok, drv, err.getvalue()


def _have_typed():
    try:
        import importlib
        tc = importlib.import_module("typed_calls")
        importlib.import_module("receiver_facts_py")
        return tc if callable(getattr(tc, "link", None)) else None
    except Exception:  # noqa: BLE001
        return None


# =====================================================================================================
def m1_pure(Sm, rec):
    fn = getattr(Sm, "_typed_call_langs", None)
    if not rec.check("M1: sync._typed_call_langs exists", callable(fn)):
        return
    cases = [
        ("None (the pass off)", None, []),
        ("{}", {}, []),
        ("the skeleton of a run with nothing enabled", {"typed_langs": [], "audit_langs": [], "error": None}, []),
        ("one language", {"typed_langs": ["csharp"], "langs_disabled": {}, "error": None}, ["csharp"]),
        ("disabled languages dropped", {"typed_langs": ["python", "csharp", "typescript"],
                                        "langs_disabled": {"csharp": "no reader", "typescript": "canary"}},
         ["python"]),
        ("the audit's languages never count", {"typed_langs": [], "audit_langs": ["python"]}, []),
        ("sorted + de-duplicated", {"typed_langs": ["typescript", "csharp", "csharp", "javascript"]},
         ["csharp", "javascript", "typescript"]),
        ("a failed run -> []", {"typed_langs": ["csharp"], "error": "ValueError: x"}, []),
        ("non-str entries ignored", {"typed_langs": ["csharp", 3, None, ["x"]]}, ["csharp"]),
        ("langs_disabled as a list", {"typed_langs": ["csharp", "python"], "langs_disabled": ["python"]},
         ["csharp"]),
        ("malformed typed_langs", {"typed_langs": 7}, []),
        ("not a dict", ["csharp"], []),
    ]
    for label, stats, want in cases:
        try:
            got = fn(stats)
        except Exception as exc:  # noqa: BLE001
            got = f"raised {exc!r}"
        rec.check(f"M1: {label} -> {want}", got == want and (not isinstance(got, list) or
                                                             all(type(x) is str for x in got)), got)


def m2_default(Sm, rec):
    root = write_tree(tmp() / "m2", TREE)
    ok, drv, err = real_sync(Sm, root, dict(OFF))
    w = meta_writes(drv)
    rec.check("M2: every flag off -> sync() succeeds and writes typed_call_langs exactly once", ok is True and len(w) == 1,
              (ok, [c for _i, c, _p in w], err[-400:]))
    if not w:
        return
    i, c, p = w[0]
    langs = [v for v in p.values() if isinstance(v, list)]
    rec.check("M2b: ... the value is [] (no typed-call language linked this graph)", langs == [[]], p)
    rec.check("M2c: ... on the singleton (matched by id:'singleton'), set as a property",
              "_DeepMapMeta {id:'singleton'}" in c and "SET" in c and "typed_call_langs" in c, c)
    rec.check("M2d: ... right after the owner stamp (the singleton exists when it is written)",
              owner_index(drv) >= 0 and i > owner_index(drv), (owner_index(drv), i))
    rec.check("M2e: a fake that reads every `MERGE (m:_DeepMapMeta` as the owner stamp still reads this project as "
              "the owner (the meta write is not such a statement and carries no root)",
              drv.owner is not None and "root" not in p and "MERGE (m:_DeepMapMeta" not in c, (drv.owner, c, p))


def m3_stub(Sm, rec):
    stats = {"version": 2, "typed_langs": ["typescript", "csharp", "python"], "audit_langs": ["javascript"],
             "langs_disabled": {"typescript": "canary"}, "error": None, "emitted": 0, "notes": [], "seconds": 0.0}

    def stub(root, nodes, calls, **k):
        return calls, dict(stats)
    root = write_tree(tmp() / "m3", TREE)
    ok, drv, err = real_sync(Sm, root, dict(OFF), typed_stub=stub)
    w = meta_writes(drv)
    langs = [v for _i, _c, p in w for v in p.values() if isinstance(v, list)]
    rec.check("M3: the typed pass ran for csharp + python (typescript disabled; javascript audit only) -> "
              "typed_call_langs = [\"csharp\", \"python\"]", ok is True and langs == [["csharp", "python"]],
              (ok, langs, err[-300:]))

    def failed(root, nodes, calls, **k):
        return calls, {"typed_langs": ["csharp"], "error": "RuntimeError: boom", "notes": [], "seconds": 0.0}
    root = write_tree(tmp() / "m3b", TREE)
    ok, drv, err = real_sync(Sm, root, dict(OFF), typed_stub=failed)
    langs = [v for _i, _c, p in meta_writes(drv) for v in p.values() if isinstance(v, list)]
    rec.check("M3b: a typed pass that failed (stats error) -> []", ok is True and langs == [[]], (ok, langs))


def m4_real(Sm, rec):
    if _have_typed() is None:
        print("  [SKIP] M4: build/typed_calls.py / receiver_facts_py.py not importable here")
        return
    root = write_tree(tmp() / "m4", TREE)
    ok, drv, err = real_sync(Sm, root, dict(OFF, DEEPMAP_TYPED_CALLS="python"))
    langs = [v for _i, _c, p in meta_writes(drv) for v in p.values() if isinstance(v, list)]
    rec.check("M4: the REAL typed pass (DEEPMAP_TYPED_CALLS=python) -> typed_call_langs = [\"python\"]",
              ok is True and langs == [["python"]] and "typed call linking" in err, (ok, langs, err[-300:]))


def m5_nothing(Sm, rec):
    root = write_tree(tmp() / "m5", {".deepmap-root": "", "README.md": "no code here\n"})
    ok, drv, err = real_sync(Sm, root, dict(OFF))
    rec.check("M5: nothing pushed (no code) -> typed_call_langs is not written", not meta_writes(drv),
              (ok, [c for _i, c, _p in meta_writes(drv)]))


def m6_failure(Sm, rec):
    root = write_tree(tmp() / "m6", TREE)
    drv = _Drv(fail_on="SET m.typed_call_langs")
    ok, drv, err = real_sync(Sm, root, dict(OFF), drv=drv)
    w = meta_writes(drv)
    rec.check("M6: the typed_call_langs write fails -> the sync still succeeds (the push and the owner stamp "
              "stand)", ok is True and owner_index(drv) >= 0, (ok, err[-400:]))
    rec.check("M6b: ... and the stale property is removed (REMOVE m.typed_call_langs) so no older value outlives "
              "this sync", any("REMOVE" in c and "typed_call_langs" in c for _i, c, _p in w),
              [c for _i, c, _p in w])


# ---- N: typed_call_narrowed ------------------------------------------------------------------------------
def _real_tc():
    try:
        import importlib
        tc = importlib.import_module("typed_calls")
        ok = all(hasattr(tc, a) for a in ("SHAPES", "IFACE_MODES", "LANGUAGES", "_config", "_iface_modes"))
        return tc if ok else None
    except Exception:  # noqa: BLE001
        return None


def _stand_in(real, *, shapes=None, iface=None, raise_on=None):
    """typed_calls with its constants and its two readers replaced: the readers answer `shapes` / `iface` whatever
    the environment says (None = every one), or raise."""
    ns = types.SimpleNamespace(SHAPES=real.SHAPES, IFACE_MODES=real.IFACE_MODES, LANGUAGES=real.LANGUAGES)

    def _config(env, registry):
        if raise_on == "config":
            raise RuntimeError("the shapes reader failed")
        return types.SimpleNamespace(shapes=frozenset(real.SHAPES if shapes is None else shapes))

    def _iface_modes(env, langs, registry):
        if raise_on == "iface":
            raise RuntimeError("the interface reader failed")
        return frozenset(real.IFACE_MODES if iface is None else iface)
    ns._config, ns._iface_modes = _config, _iface_modes
    return ns


def n1_narrowed(Sm, rec):
    fn = getattr(Sm, "_typed_call_narrowed", None)
    if not rec.check("N1: sync._typed_call_narrowed exists", callable(fn)):
        return
    tc = _real_tc()
    if tc is None:
        print("  [SKIP] N1: build/typed_calls.py (its readers) not importable here")
        return
    every = ",".join(tc.SHAPES)
    cases = [
        ("nothing ran ([]), every setting default", [], {}, False),
        ("nothing ran ([]), shapes narrowed: nothing was narrowed", [], {SHAPES_ENV: "R1"}, False),
        ("python ran, every default", ["python"], {}, False),
        ("python ran, one shape selected", ["python"], {SHAPES_ENV: "R1"}, True),
        ("python ran, every shape named", ["python"], {SHAPES_ENV: every}, False),
        ("python ran, shapes OFF (none selected)", ["python"], {SHAPES_ENV: "0"}, True),
        ("csharp ran, every default", ["csharp"], {}, False),
        ("csharp ran, interface dispatch static only", ["csharp"], {IFACE_ENV: "static"}, True),
        ("csharp ran, implementers only", ["csharp"], {IFACE_ENV: "impl"}, True),
        ("csharp ran, both modes named", ["csharp"], {IFACE_ENV: "static,impl"}, False),
        ("csharp ran, interface dispatch OFF", ["csharp"], {IFACE_ENV: "0"}, True),
        ("csharp ran, one shape selected", ["csharp"], {SHAPES_ENV: "K"}, True),
        ("python ran, IFACE narrowed: python has no iface_dispatch rule", ["python"], {IFACE_ENV: "static"}, False),
        ("typescript + javascript ran, IFACE OFF: neither has the rule", ["javascript", "typescript"],
         {IFACE_ENV: "0"}, False),
        ("csharp + python ran, IFACE static", ["csharp", "python"], {IFACE_ENV: "static"}, True),
    ]
    for label, langs, env, want in cases:
        with flags(**{SHAPES_ENV: None, IFACE_ENV: None, **env}):
            try:
                got = fn(list(langs))
            except Exception as exc:  # noqa: BLE001
                got = f"raised {exc!r}"
        rec.check(f"N1: {label} -> {want}", got is want, got)
    # whatever typed_calls decides an ON word or an unknown token means, the stamp says what its reader selected
    for raw in ("1", "on", "yes", "statc", "static,bogus", " Static ", "IMPL"):
        with flags(**{SHAPES_ENV: None, IFACE_ENV: raw}):
            try:
                modes = tc._iface_modes(None, {"csharp"}, tc.LANGUAGES)
                want = bool(set(tc.IFACE_MODES) - set(modes))
                got = fn(["csharp"])
            except Exception as exc:  # noqa: BLE001
                want, got = None, f"raised {exc!r}"
        rec.check(f"N1: csharp ran, DEEPMAP_TYPED_CALLS_IFACE={raw!r} -> what typed_calls._iface_modes selected says "
                  f"({want})", want is not None and got is want, (got, want))
    for raw in ("r1,r2", "R1,bogus", "bogus"):
        with flags(**{SHAPES_ENV: raw, IFACE_ENV: None}):
            try:
                want = bool(set(tc.SHAPES) - set(tc._config(None, tc.LANGUAGES).shapes))
                got = fn(["python"])
            except Exception as exc:  # noqa: BLE001
                want, got = None, f"raised {exc!r}"
        rec.check(f"N1: python ran, DEEPMAP_TYPED_CALLS_SHAPES={raw!r} -> what typed_calls._config selected says "
                  f"({want})", want is not None and got is want, (got, want))
    # teeth: the READERS decide (a stand-in reader that narrows under a default environment)
    saved = Sm._typed_calls_module
    try:
        stand = [
            ("a shapes reader selecting R1 under a DEFAULT environment -> True (the reader decides)", ["python"],
             dict(shapes={"R1"}), True),
            ("an interface reader selecting static (csharp ran) -> True", ["csharp"], dict(iface={"static"}), True),
            ("... the same reader when only python ran -> False (no iface_dispatch rule)", ["python"],
             dict(iface={"static"}), False),
            ("readers selecting everything -> False", ["csharp"], {}, False),
            ("the shapes reader raising -> None (unknown, never a guess)", ["python"], dict(raise_on="config"), None),
            ("the interface reader raising (csharp ran) -> None", ["csharp"], dict(raise_on="iface"), None),
        ]
        for label, langs, kw, want in stand:
            st = _stand_in(tc, **kw)
            Sm._typed_calls_module = lambda st=st: st
            with flags(**{SHAPES_ENV: None, IFACE_ENV: None}):
                try:
                    got = fn(list(langs))
                except Exception as exc:  # noqa: BLE001
                    got = f"raised {exc!r}"
            rec.check(f"N1: {label}", got is want, got)
        Sm._typed_calls_module = lambda: None
        with flags(**{SHAPES_ENV: None, IFACE_ENV: None}):
            got_none, got_empty = fn(["csharp"]), fn([])
        rec.check("N1: typed_calls not importable while a language ran -> None; nothing ran -> False",
                  got_none is None and got_empty is False, (got_none, got_empty))
    finally:
        Sm._typed_calls_module = saved


def n2_write(Sm, rec):
    fn = getattr(Sm, "_write_typed_call_langs", None)
    if not rec.check("N2: sync._write_typed_call_langs exists", callable(fn)):
        return
    tc = _real_tc()
    if tc is None:
        print("  [SKIP] N2: build/typed_calls.py not importable here")
        return
    for label, langs, env, want in (("csharp ran under IFACE=static", ["csharp"], {IFACE_ENV: "static"}, True),
                                    ("python ran, every default", ["python"], {}, False),
                                    ("nothing ran", [], {SHAPES_ENV: "R1"}, False)):
        drv = _Drv()
        with flags(**{SHAPES_ENV: None, IFACE_ENV: None, **env}):
            fn(drv, list(langs))
        w = meta_writes(drv)
        c, p = (w[0][1], w[0][2]) if len(w) == 1 else ("", {})
        rec.check(f"N2: {label} -> ONE statement on the singleton SETs typed_call_langs={langs} and "
                  f"typed_call_narrowed={want}",
                  len(w) == 1 and "_DeepMapMeta {id:'singleton'}" in c and "SET" in c and "m.typed_call_narrowed=" in c
                  and "MERGE" not in c and [v for v in p.values() if isinstance(v, list)] == [langs]
                  and [v for v in p.values() if isinstance(v, bool)] == [want], (c, p))
    saved = Sm._typed_calls_module
    try:
        Sm._typed_calls_module = lambda: None
        drv = _Drv()
        fn(drv, ["csharp"])
        w = meta_writes(drv)
        p = w[0][2] if len(w) == 1 else {}
        rec.check("N2: unknown (typed_calls not importable) -> typed_call_narrowed SET to null (absent = unknown), "
                  "typed_call_langs still written",
                  len(w) == 1 and None in p.values() and [v for v in p.values() if isinstance(v, list)] == [["csharp"]],
                  [c for _i, c, _p in w])
    finally:
        Sm._typed_calls_module = saved
    drv = _Drv(fail_on="SET m.typed_call_langs")
    fn(drv, ["csharp"])
    rem = [c for _i, c, _p in meta_writes(drv) if "REMOVE" in c]
    rec.check("N2: the write fails -> one REMOVE of BOTH properties (no stale narrowed flag outlives it)",
              len(rem) == 1 and "m.typed_call_langs" in rem[0] and "m.typed_call_narrowed" in rem[0], rem)


def n3_sync(Sm, rec):
    if _real_tc() is None:
        print("  [SKIP] N3: build/typed_calls.py not importable here")
        return
    stats = {"version": 2, "typed_langs": ["csharp"], "langs_disabled": {}, "error": None, "notes": [], "seconds": 0.0}

    def stub(root, nodes, calls, **k):
        return calls, dict(stats)

    def narrowed_of(drv):
        return [v for _i, _c, p in meta_writes(drv) for k, v in p.items() if k != "langs"]
    root = write_tree(tmp() / "n3a", TREE)
    ok, drv, err = real_sync(Sm, root, dict(OFF))
    rec.check("N3: every flag off -> the sync writes typed_call_narrowed=False beside typed_call_langs=[]",
              ok is True and narrowed_of(drv) == [False], (ok, narrowed_of(drv), err[-300:]))
    for label, env in (("DEEPMAP_TYPED_CALLS_IFACE=static", {IFACE_ENV: "static"}),
                       ("DEEPMAP_TYPED_CALLS_SHAPES=R1", {SHAPES_ENV: "R1"})):
        Sm2 = load_module(BUILD / "sync.py", f"dm_typedmeta_n3_{abs(hash(label))}_{os.getpid()}")
        root = write_tree(tmp() / f"n3_{abs(hash(label))}", TREE)
        ok, drv, err = real_sync(Sm2, root, dict(OFF, **env), typed_stub=stub)
        rec.check(f"N3: the typed pass ran for csharp under {label} -> typed_call_narrowed=True",
                  ok is True and narrowed_of(drv) == [True], (ok, narrowed_of(drv), err[-300:]))


def run_narrowed(Sm, rec):
    n1_narrowed(Sm, rec)
    n2_write(Sm, rec)


def run_all(Sm, rec):
    m1_pure(Sm, rec)
    m2_default(Sm, rec)
    m3_stub(Sm, rec)
    m5_nothing(Sm, rec)
    m6_failure(Sm, rec)


MUTANTS = [
    ("the meta write skipped", "            _write_typed_call_langs(driver, _typed_call_langs(call_stats))\n",
     "            pass\n"),
    ("disabled languages kept", "x not in off", "x is not None"),
    ("a failed run counted", '        if not isinstance(stats, dict) or stats.get("error"):\n',
     "        if not isinstance(stats, dict):\n"),
    ("the audit's languages counted", 'stats.get("typed_langs")', '(list(stats.get("typed_langs") or []) + '
                                                                  'list(stats.get("audit_langs") or []))'),
    ("a failed write propagates", "    except Exception:  # noqa: BLE001 — typed_call_langs is advisory",
     "    except ZeroDivisionError:  # noqa: BLE001 — typed_call_langs is advisory"),
]


MUTANTS_N = [
    ("the narrowed flag never computed", "        narrowed = _typed_call_narrowed(langs)\n", "        narrowed = None\n"),
    ("the shapes reader ignored", "        if set(tc.SHAPES) - set(tc._config(None, reg).shapes):\n",
     "        if False:\n"),
    ("the interface reader ignored",
     "            if set(tc.IFACE_MODES) - set(tc._iface_modes(None, set(ran), reg)):\n", "            if False:\n"),
    ("the interface modes judged for every language",
     '        if any("iface_dispatch" in (getattr(reg[n], "lookup_rules", None) or ()) for n in ran):\n',
     "        if ran:\n"),
    ("a reader failure read as not narrowed",
     "    except Exception:  # noqa: BLE001 — advisory: an unreadable setting is unknown, never a guess\n"
     "        return None\n",
     "    except Exception:  # noqa: BLE001 — advisory: an unreadable setting is unknown, never a guess\n"
     "        return False\n"),
    ("a failed write keeps the narrowed flag",
     '"MATCH (m:_DeepMapMeta {id:\'singleton\'}) REMOVE m.typed_call_langs, m.typed_call_narrowed"',
     '"MATCH (m:_DeepMapMeta {id:\'singleton\'}) REMOVE m.typed_call_langs"'),
    ("nothing ran reads as unknown", "    if not langs:\n        return False\n", "    if not langs:\n        return None\n"),
]


def _mutants(tag, mutants, runner):
    src = (BUILD / "sync.py").read_text(encoding="utf-8")
    for i, (label, old, new) in enumerate(mutants):
        n = src.count(old)
        if not LOUD.check(f"{tag}: mutant '{label}' — its anchor is found exactly once in sync.py", n == 1,
                          f"found {n} times"):
            continue
        rec = Rec(loud=False)
        try:
            Sm = load_module(BUILD / "sync.py", f"dm_typedmeta_mut_{tag}_{i}_{os.getpid()}", src.replace(old, new))
            with contextlib.redirect_stdout(io.StringIO()):
                runner(Sm, rec)
        except Exception as exc:  # noqa: BLE001 — a crash is a caught mutant too
            rec.failed += 1
            rec.labels.append(f"crash: {exc!r}")
        LOUD.check(f"{tag}: mutant '{label}' is caught ({rec.failed} check(s) RED)", rec.failed > 0, rec.labels[:2])


def sec_mutants():
    print("\n-- M7: mutation teeth (each mutant of build/sync.py must turn a check RED) --")
    _mutants("M7", MUTANTS, run_all)
    if _real_tc() is None:
        print("  [SKIP] N4: build/typed_calls.py not importable here")
        return
    print("\n-- N4: mutation teeth for typed_call_narrowed --")
    _mutants("N4", MUTANTS_N, run_narrowed)


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 64)
    print("DeepMap — typed_call_langs on the :_DeepMapMeta singleton (no Neo4j)")
    print("=" * 64)
    os.environ.setdefault("GIT_CONFIG_NOSYSTEM", "1")
    saved_uri = os.environ.get("NEO4J_URI")
    try:
        print("\n-- M1-M6, N1-N3 --")
        for fn in (m1_pure, m2_default, m3_stub, m4_real, m5_nothing, m6_failure, n1_narrowed, n2_write, n3_sync):
            try:
                Sm = load_module(BUILD / "sync.py", f"dm_typedmeta_{fn.__name__}_{os.getpid()}")
                fn(Sm, LOUD)
            except Exception as exc:  # noqa: BLE001
                LOUD.check(f"{fn.__name__}: runs without raising", False, repr(exc))
        try:
            sec_mutants()
        except Exception as exc:  # noqa: BLE001
            LOUD.check("M7: runs without raising", False, repr(exc))
    finally:
        if saved_uri is None:
            os.environ.pop("NEO4J_URI", None)
        else:
            os.environ["NEO4J_URI"] = saved_uri
        for d in _TMP:
            shutil.rmtree(d, ignore_errors=True)
    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
