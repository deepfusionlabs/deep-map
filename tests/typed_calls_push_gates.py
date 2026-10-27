#!/usr/bin/env python3
"""Typed-receiver call linking, slice S2 — the PUSH integration in build/sync.py (receiver-calls design 6.1, 9 S2).

What it proves (no Neo4j, no network; the real engine is never run — captured graphs of graphify 0.8.39 AND 0.8.51):

  A  flag-off byte identity: the push-side passes (_unkey_receiver_calls -> _typed_calls_link -> _attach_bare_calls
     -> _attach_local_defs -> _attach_local_def_calls -> _resolve_calls) give exactly what the pre-S2 sync.py gave on a
     seeded 40-project synthetic corpus (Python / TS / JS / Go / Ruby / unknown files written to disk, graphify- and
     ast-shaped tuples, CallKeys where a precise / CALLS_PLUS run makes them) under 9 flag modes — every flag off,
     DEEPMAP_PRECISE_CALLS=1 / =python, + LOCAL_DEF_CALLS, LOCAL_DEF_CALLS alone, FALSE_CALL_FILTER=0, F8b,
     CALLS_PLUS, the ast shape. The digests were frozen from the pre-S2 module (FROZEN_FROM); regenerate with
     `--freeze <dir holding that sync.py>` only for a deliberate default-graph change.
  B  the per-tuple rules: a lone CallKey binds exactly its node in default mode (RED on the pre-S2 tree); a
     `call_typed` grade is honoured exactly (a same-file typed call can be INFERRED); typed keys skip T3#6 and the
     DEEPMAP_LOCAL_DEF_CALLS re-bind but obey the cross-family drop, F8b and the Go `_test.go` rule; `call_weak` is
     applied last and only lowers; a typed key appended after a fan-out wins; the anti-confound (TYPED alone leaves
     a NAME equal to a node key exactly as V0 does); S1 review finding 5 (a PRECISE family CSV no longer flips
     _attach_local_defs' keyed reading for callers of other families); B10 (review r1 finding 1): TYPED with
     DEEPMAP_LOCAL_DEF_CALLS on a real tree — a typed key never makes _attach_local_def_calls skip the caller, so its
     bare call stays local; _upsert_nodes rows never carry the private attributes.
  C  the typed step's wiring: pipeline order read from sync.py's AST; a missing typed_calls module / the flags off
     -> the SAME list and no stats; the link call (is_test = sync._is_test_path, the RESOLVED engine, the
     extraction's own reader — `source=` or the core's `sources=` — adapted to link's signature); the one-shot
     extraction note; the shared graphify_extract._Source when extract_with_report takes `source=`; one stderr
     line; the real typed_calls on a project; C8 (review r1 finding 4): the pure passes sit BEFORE the background
     refresh's write checkpoint (AST) — a real unattended sync() runs the typed pass in a hard-exit phase and a user's
     sync that starts during it is seen at the checkpoint (nothing pushed, outcome preempted).
  D  the manifest's `call_linking` block (and only there — never `_rebuild_code`'s `.info`), through a real sync()
     pass with a fake driver.
  E  the captured graphs of the S3b (Python) and S3c (TS / JS) fixture trees, both engines: TYPED=0 == the module
     hidden; node text identical across V0 / VT / VP / VPT / VTL (TYPED + DEEPMAP_LOCAL_DEF_CALLS); every typed key
     reaches the push marked with its exact grade (VT, VPT, VTL); no cross-file EXTRACTED typed edge; VT changes edges
     (not vacuous).
  F  sync_edge_tx_bound E0-E3 with typed tuples: the budget-split push leaves the identical graph (its own
     checks re-run on a typed fixture).
  G  incremental sync with TYPED on: incremental == full (cold, warm, after an edit); the extract cache never holds
     a CallKey or a typed attribute.
  H  mutation teeth: 20 mutants of build/sync.py (one a MOVE: the pure passes back after the write checkpoint), each
     must turn a check RED.

usage: python tests/typed_calls_push_gates.py [--root <tree holding build/>] [--freeze <dir holding a sync.py>]
"""
from __future__ import annotations

import ast
import contextlib
import copy
import hashlib
import importlib
import importlib.util
import io
import json
import os
import random
import shutil
import sys
import tempfile
import traceback
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FX = REPO / "tests" / "fixtures" / "typed_calls"


def _parse_args(argv):
    root, freeze, it = None, None, iter(argv)
    for arg in it:
        if arg == "--root":
            root = next(it, "")
        elif arg.startswith("--root="):
            root = arg[len("--root="):]
        elif arg == "--freeze":
            freeze = next(it, "")
        elif arg.startswith("--freeze="):
            freeze = arg[len("--freeze="):]
        else:
            raise SystemExit(f"usage: python tests/typed_calls_push_gates.py [--root <tree>] [--freeze <dir>] "
                             f"(unknown argument {arg!r})")
    if root is not None and not (Path(root) / "build").is_dir():
        raise SystemExit(f"--root needs a directory holding build/ (got {root!r})")
    if freeze is not None and not (Path(freeze) / "sync.py").is_file():
        raise SystemExit(f"--freeze needs a directory holding sync.py (got {freeze!r})")
    return (Path(root).resolve() if root else REPO), (Path(freeze).resolve() if freeze else None)


ROOT, FREEZE = _parse_args(sys.argv[1:]) if __name__ == "__main__" else (REPO, None)
BUILD = ROOT / "build"
sys.path.insert(0, str(BUILD))

import edge_resolve as ER      # noqa: E402
import graphify_extract as GX  # noqa: E402
import sync as S               # noqa: E402

CK = GX.call_key
PASSES: list = []
FAILS: list = []
SKIPS: list = []
_TMP: list = []

# Every graph flag a check touches is PINNED here ("0" where off — never "unset = off"), so a later default flip
# (slice S8) cannot move these expectations.
OFF = {"DEEPMAP_TYPED_CALLS": "0", "DEEPMAP_CALLS_AUDIT": "0", "DEEPMAP_PRECISE_CALLS": "0",
       "DEEPMAP_CALLS_FAMILY_FILTER": "0", "DEEPMAP_GRAPHIFY_CALLS_PLUS": "0", "DEEPMAP_LOCAL_DEF_CALLS": "0",
       "DEEPMAP_DROP_APP_TO_TEST_CALLS": "0", "DEEPMAP_FALSE_CALL_FILTER": "1", "DEEPMAP_EXCLUDE_TESTS": "0",
       "DEEPMAP_PRECISE_FIXES": "1", "DEEPMAP_SELF_CALL_REBIND": "1", "DEEPMAP_INCREMENTAL_SYNC": "0"}
WAVE1 = "python,typescript,javascript"


class Rec:
    """Check recorder: loud (the real run) or silent (a mutant run only counts)."""

    def __init__(self, loud=True):
        self.loud, self.failed, self.labels = loud, 0, []

    def check(self, name, ok, detail=""):
        if self.loud:
            (PASSES if ok else FAILS).append(name)
            print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok or detail == "" else f" -- {str(detail)[:900]}"))
        if not ok:
            self.failed += 1
            self.labels.append(name)
        return bool(ok)


LOUD = Rec()


def check(name, ok, detail=""):
    return LOUD.check(name, ok, detail)


def skip(name, why):
    SKIPS.append(name)
    print(f"  [SKIP] {name} -- {why}")


def tmp() -> Path:
    if not _TMP:
        _TMP.append(Path(tempfile.mkdtemp(prefix="dm_s2push_")).resolve())
    return _TMP[0]


@contextlib.contextmanager
def flags(**kv):
    """Every DEEPMAP_* variable removed (the shell must not leak in), then `kv` set; all restored after."""
    saved = {k: v for k, v in os.environ.items() if k.startswith("DEEPMAP_")}
    try:
        for k in saved:
            os.environ.pop(k, None)
        kv.setdefault("DEEPMAP_HOME", str(tmp() / "home"))     # never the operator's ~/.deepmap
        for k, v in kv.items():
            if v is not None:
                os.environ[k] = str(v)
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
def captured_stderr():
    buf = io.StringIO()
    old = sys.stderr
    sys.stderr = buf
    try:
        yield buf
    finally:
        sys.stderr = old


@contextlib.contextmanager
def quiet():
    with contextlib.redirect_stdout(io.StringIO()), captured_stderr():
        yield


def node(key, label, name, sf, line=1, **extra):
    d = {"key": key, "label": label, "name": name, "source_file": sf, "start_line": line, "body_summary": f"{name}.",
         "documented": False}
    d.update(extra)
    return d


def write_tree(root: Path, files: dict) -> Path:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode("utf-8"))
    return root


def norm(v):
    """A JSON-stable form of a node attribute value (sets sorted)."""
    if isinstance(v, (set, frozenset)):
        return sorted(norm(x) for x in v)
    if isinstance(v, (list, tuple)):
        return [norm(x) for x in v]
    if isinstance(v, dict):
        return {str(k): norm(x) for k, x in sorted(v.items(), key=lambda kv: str(kv[0]))}
    return v


ATTRS = ("local_defs", "call_bare", "call_alias", "call_local", "call_member", "call_recv", "call_typed", "call_weak")


def chain(Sm, root, nodes, calls, *, typed=True):
    """The push-side passes exactly as sync's pass runs them, from the calls the extraction + recognizers hand over.
    Returns (nodes, calls handed to the push, edges, counts)."""
    nodes = copy.deepcopy(nodes)
    calls = list(calls)
    calls, n_unkey = Sm._unkey_receiver_calls(root, nodes, calls)
    stats = None
    step = getattr(Sm, "_typed_calls_link", None)
    if typed and callable(step):
        calls, stats = step(root, nodes, calls)
    n_bare = Sm._attach_bare_calls(root, nodes, calls)
    Sm._attach_local_defs(root, nodes, calls)
    n_local = Sm._attach_local_def_calls(root, nodes, calls)
    edges = Sm._resolve_calls(nodes, calls)
    return nodes, calls, edges, {"unkey": n_unkey, "bare": n_bare, "local": n_local, "stats": stats}


def canonical(nodes, calls, edges, counts) -> str:
    out = {"calls": [[a, str(b), bool(GX.is_call_key(b))] for a, b in calls],
           "attrs": {n["key"]: {a: norm(n[a]) for a in ATTRS if a in n} for n in nodes if any(a in n for a in ATTRS)},
           "edges": [[a, b, c] for (a, b), c in edges.items()],
           "n": [counts["unkey"], counts["bare"], counts["local"]]}
    return json.dumps(out, sort_keys=True)


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def load_module(path: Path, name: str, source: "str | None" = None):
    """`path` (or `source` standing for it) as a fresh module `name` (sys.path already holds its siblings)."""
    mod = types.ModuleType(name)
    mod.__file__ = str(path)
    code = compile(source if source is not None else path.read_text(encoding="utf-8"), str(path), "exec")
    sys.modules[name] = mod
    try:
        exec(code, mod.__dict__)  # noqa: S102 — this repository's own module (or a mutant of it)
    finally:
        sys.modules.pop(name, None)
    return mod


# =====================================================================================================
# A — the synthetic corpus and the frozen flag-off digests
# =====================================================================================================
NAMES = ("save", "run", "helper", "load", "check", "send", "build", "cli", "Order")
CLASSES = ("Order", "Session", "Repo", "Runner")
PY_FORMS = ("bare", "bare", "self", "member", "member", "module", "classref", "alias", "nested")
TS_FORMS = ("bare", "this", "member", "member")
GO_FORMS = ("bare", "member", "member")
RB_FORMS = ("bare", "member")
_RECV_KIND = {"bare": "bare", "self": "self", "module": "mod:os", "classref": "name:Order", "member": "expr",
              "alias": "bare", "nested": "bare"}


def _file_plan(rng, i, used_cli):
    kind = rng.choice(("py", "py", "py", "py", "ts", "js", "go", "go_test", "rb", "zzz"))
    if kind == "py":
        if not used_cli[0] and rng.random() < 0.3:
            used_cli[0] = True
            return "cli.py", "py"
        return rng.choice((f"pkg/a{i}.py", f"pkg/a{i}.py", f"tests/test_a{i}.py", f"examples/e{i}.py")), "py"
    return {"ts": (f"web/w{i}.ts", "ts"), "js": (f"web/j{i}.js", "js"), "go": (f"svc/g{i}.go", "go"),
            "go_test": (f"svc/g{i}_test.go", "go"), "rb": (f"app/models/r{i}.rb", "rb"),
            "zzz": (f"misc/x{i}.zzz", "zzz")}[kind]


def gen_project(seed: int, mode: dict):
    """One synthetic project: {rel: text}, nodes, calls. `mode` decides where CallKeys appear (only where a real
    run makes them: precise callers, CALLS_PLUS additions) and whether callers carry ast receiver kinds."""
    rng = random.Random(seed * 7919 + 17)
    used_cli = [False]
    plans = [_file_plan(rng, i, used_cli) for i in range(rng.randint(3, 7))]
    files, nodes, sites = {}, [], []          # sites: (caller key, rel, name, form)
    for rel, kind in plans:
        if rel in files:
            continue
        modkey = rel.rsplit(".", 1)[0].replace("/", "_")
        nodes.append(node(modkey, "Module", modkey.rsplit("_", 1)[-1], rel, 1))
        lines = []
        defs = []                                  # (name, label, cls)
        if rel == "cli.py":
            defs.append(("cli", "Function", None))
        for _ in range(rng.randint(1, 3)):
            defs.append((rng.choice(NAMES[:-1]), rng.choice(("Function", "Function", "Function", "Route")), None))
        if rng.random() < 0.7:
            c = rng.choice(CLASSES)
            for _ in range(rng.randint(1, 3)):
                defs.append((rng.choice(NAMES[:-2]), "Method", c))
        seen_names = set()
        if kind == "py":
            lines += ["import os", "from pkg.a0 import helper", ""]
        elif kind in ("ts", "js"):
            lines += ["import { helper } from './w0';", ""]
        elif kind == "go":
            lines += ["package svc", ""]
        cur_cls = None
        for name, label, cls in defs:
            qual = f"{cls}.{name}" if cls else name
            if qual in seen_names:
                continue
            seen_names.add(qual)
            forms = {"py": PY_FORMS, "ts": TS_FORMS, "js": TS_FORMS, "go": GO_FORMS,
                     "rb": RB_FORMS}.get(kind, ("bare",))
            body = [(rng.choice(NAMES), rng.choice(forms)) for _ in range(rng.randint(1, 4))]
            if cls is None or label != "Method":
                body = [(n, f if f not in ("self", "this") else "member") for n, f in body]
            ind = "    "
            if kind == "py":
                if cls and cls != cur_cls:
                    lines += [f"class {cls}:"]
                    nodes.append(node(f"{modkey}_{cls.lower()}", "Class", cls, rel, len(lines)))
                    cur_cls = cls
                elif not cls and cur_cls:
                    cur_cls = None
                pre = ind if cls else ""
                lines.append(f"{pre}def {name}(self, a=None):" if cls else f"def {name}(a=None):")
                start = len(lines)
                for cn, form in body:
                    b = pre + ind
                    if form == "bare":
                        lines.append(f"{b}{cn}()")
                    elif form == "self":
                        lines.append(f"{b}self.{cn}()")
                    elif form == "member":
                        lines.append(f"{b}obj.{cn}()")
                    elif form == "module":
                        lines.append(f"{b}os.{cn}()")
                    elif form == "classref":
                        lines.append(f"{b}Order.{cn}()")
                    elif form == "alias":
                        lines += [f"{b}{cn} = obj.{cn}", f"{b}{cn}()"]
                    elif form == "nested":
                        lines += [f"{b}def {cn}():", f"{b}    return 1", f"{b}{cn}()"]
                lines += [f"{pre}{ind}return None", ""]
            elif kind in ("ts", "js"):
                if cls and cls != cur_cls:
                    if cur_cls:
                        lines.append("}")
                    lines.append(f"export class {cls} {{")
                    nodes.append(node(f"{modkey}_{cls.lower()}", "Class", cls, rel, len(lines)))
                    cur_cls = cls
                elif not cls and cur_cls:
                    lines.append("}")
                    cur_cls = None
                pre = "  " if cls else ""
                lines.append(f"{pre}{name}(a) {{" if cls else f"export function {name}(a) {{")
                start = len(lines)
                for cn, form in body:
                    b = pre + "  "
                    lines.append(f"{b}{cn}();" if form == "bare" else f"{b}this.{cn}();" if form == "this"
                                 else f"{b}obj.{cn}();")
                lines.append(f"{pre}}}")
            elif kind == "go":
                if cls:
                    lines.append(f"func (s *{cls}) {name}() {{")
                else:
                    lines.append(f"func {name}() {{")
                start = len(lines)
                for cn, form in body:
                    lines.append(f"\t{cn}()" if form == "bare" else f"\ts.{cn}()")
                lines += ["}", ""]
            elif kind == "rb":
                if cls and cls != cur_cls:
                    if cur_cls:
                        lines.append("end")
                    lines.append(f"class {cls}")
                    nodes.append(node(f"{modkey}_{cls.lower()}", "Class", cls, rel, len(lines)))
                    cur_cls = cls
                lines.append(f"  def {name}")
                start = len(lines)
                for cn, form in body:
                    lines.append(f"    {cn}" if form == "bare" else f"    obj.{cn}")
                lines.append("  end")
            else:
                lines.append(f"proc {name}")
                start = len(lines)
                for cn, _f in body:
                    lines.append(f"  {cn}()")
                lines.append("end")
            key = f"{modkey}_{qual.lower().replace('.', '_')}"
            nodes.append(node(key, label if not (cls and label == "Route") else "Method", qual, rel, start))
            for cn, form in body:
                sites.append((key, rel, cn, form))
        if kind in ("ts", "js") and cur_cls:
            lines.append("}")
        if kind == "rb" and cur_cls:
            lines.append("end")
        files[rel] = "\n".join(lines) + "\n"
    by_last: dict = {}
    for n in nodes:
        by_last.setdefault(str(n["name"]).rsplit(".", 1)[-1], []).append(n["key"])
    fam = ER.lang_family
    calls = []
    keyed = mode.get("keyed", lambda rel: False)
    for ck, rel, cn, form in sites:
        if form == "classref" and rng.random() < 0.5:
            calls.append((ck, f"Order.{cn}"))
            continue
        cands = [k for k in by_last.get(cn, ()) if k != ck
                 and fam(next(n["source_file"] for n in nodes if n["key"] == k)) == fam(rel)]
        if keyed(rel) and cands and rng.random() < 0.6:
            calls.append((ck, CK(rng.choice(cands))))
        else:
            calls.append((ck, cn))
    for _ in range(rng.randint(0, 2)):          # engine artefacts: a name the source never calls
        ck = rng.choice([s[0] for s in sites]) if sites else None
        if ck:
            calls.append((ck, rng.choice(NAMES)))
    if mode.get("plus"):                         # CALLS_PLUS additions: keyed, Python callers only
        for ck, rel, cn, form in sites:
            if rel.endswith(".py") and form == "module" and rng.random() < 0.7:
                cands = [k for k in by_last.get(cn, ()) if k != ck]
                if cands:
                    calls.append((ck, CK(rng.choice(cands))))
    if mode.get("ast"):                          # the ast extractor's receiver kinds on its Python callers
        kinds: dict = {}
        for ck, rel, cn, form in sites:
            if rel.endswith(".py"):
                kinds.setdefault(ck, {}).setdefault(cn, set()).add(_RECV_KIND.get(form, "expr"))
        for n in nodes:
            if n["key"] in kinds:
                n["call_recv"] = {c: sorted(k) for c, k in kinds[n["key"]].items()}
    else:                                        # graphify-shaped: G5's member fall-backs on some callers
        for n in nodes:
            mem = sorted({cn for ck, _r, cn, f in sites if ck == n["key"] and f in ("member", "this")})
            if mem and rng.random() < 0.4:
                n["call_member"] = mem
    return files, nodes, calls


_ALL = lambda rel: True                                     # noqa: E731
_PY = lambda rel: str(rel).endswith(".py")                  # noqa: E731
MODES = {
    "off": (dict(OFF), {}),
    "precise": (dict(OFF, DEEPMAP_PRECISE_CALLS="1"), {"keyed": _ALL}),
    "precise_python": (dict(OFF, DEEPMAP_PRECISE_CALLS="python"), {"keyed": _PY}),
    "precise_local": (dict(OFF, DEEPMAP_PRECISE_CALLS="1", DEEPMAP_LOCAL_DEF_CALLS="1"), {"keyed": _ALL}),
    "local": (dict(OFF, DEEPMAP_LOCAL_DEF_CALLS="1"), {}),
    "fcf_off": (dict(OFF, DEEPMAP_FALSE_CALL_FILTER="0"), {}),
    "f8b": (dict(OFF, DEEPMAP_DROP_APP_TO_TEST_CALLS="1", DEEPMAP_PRECISE_CALLS="1"), {"keyed": _ALL}),
    "calls_plus": (dict(OFF, DEEPMAP_GRAPHIFY_CALLS_PLUS="1"), {"plus": True}),
    "ast": (dict(OFF), {"ast": True}),
}
SEEDS = range(40)

# Frozen from the PRE-S2 build/sync.py (the working tree before slice S2, sha256 FROZEN_FROM) with
# `python tests/typed_calls_push_gates.py --freeze <dir holding that sync.py>`: per mode, the 12-hex digest of
# every seed's canonical (calls handed to the push with key marks, private attributes, edges in order, counts).
FROZEN_FROM = "cbb852e684be932c157f9dc7c2f6c8c945c59ca9f936843c4243aea2c2376ed2"
FROZEN: dict = {
    "off": (
        "08b3615bb694 168d38468779 cab6c78ebc69 e283f0ec4d06 54fb0336a06a 641e9e5f35a1 b4523926c433 b2a344dad002 "
        "1e94d70a551e b105d902bd49 e7f474b08bcb e20c33a7e415 a3da111b2dab b00e29d5ba9c 054f1c782dd3 c9fa031fd8d8 "
        "c29a511f985b f5335d5dc955 edbe82e8b70e 8e7a47ce9bd0 74d392853bec 63e0bd890905 af133154f5ab 2478436aae33 "
        "3987e12d1987 01cc7751e7db df72a68ad139 89f3ee00f1f7 31d09987cd03 823af356403e a919f924e9d9 8d4376bfdc84 "
        "f3e94cc16d77 92ec25e16668 ea2d20c8e743 9d4755082634 715604929bfa 1d3dbb38b842 73011b11edea f34eecc726f4"
    ).split(),
    "precise": (
        "c079b5e2b28d bd87b3ab488e cdf272b7a4d3 be536bcd54b9 ec86376a6e96 5f9434e31ee4 1b841b07be1a f35e55075ecc "
        "d595e91ce50f dd9e96118629 27b8c458743f 863c4aa70ad4 77f97239ef59 d515293230c4 56ff1bc2dd18 187eb5cac7b8 "
        "15f54a137069 0025e9aacd92 483fa04430c9 0acde82f486b 6d6f39a0d995 374c6eaa1f41 56e05f41f034 f0684d35a459 "
        "7496b7f7e005 d805990b2d56 23c193aabef8 6068810111aa 03942b2cda11 31303160ebdf 64104883c3bc 3decd519c1b1 "
        "cfc14e53c20e 71fd0890dbab 4f72df294229 730bf48db468 e3307ff08662 2c6d039b3058 8d434ea1710d 679a9a3505e4"
    ).split(),
    "precise_python": (
        "08b3615bb694 b147228f7bc6 6996965f33f7 e283f0ec4d06 e8959bfcc06c b5a734449952 e4f40194eea9 ac657e75a188 "
        "d595e91ce50f dd9e96118629 e7f474b08bcb 05281a2f452e a3da111b2dab abb5102cbf25 3407159e1d70 187eb5cac7b8 "
        "270dcbaf0ecc 2572faa8cae5 a306b772f227 f66c54b2f4d2 e15735050131 4d2e05cb2b62 4d5b752ee9b0 ab1f8133acd2 "
        "e9885ba2c1da f7d03f5ca4b9 ef6ad91a23b3 6068810111aa 31d09987cd03 823af356403e c1d5499d8ae3 0388fa35bb53 "
        "ed4a0e6c9b51 343849fb74a2 ea2d20c8e743 9d4755082634 0a07dba85abf c7e2618fd920 211c1cd66e81 a56a5f5ca6ed"
    ).split(),
    "precise_local": (
        "c079b5e2b28d 29d269b25aa8 cdf272b7a4d3 be536bcd54b9 754ee870b9bf 5f9434e31ee4 8c62bca7bbfb c01856f9db14 "
        "312d009f4225 5a34b1bf2192 27b8c458743f 863c4aa70ad4 77f97239ef59 d6f78ea52ad5 10c8a21142bb 187eb5cac7b8 "
        "85d3f056eab2 be04e3faa3a8 34189917f9d4 32c8d29e3965 b1ba027ee1c8 374c6eaa1f41 dbc6517d2501 f0684d35a459 "
        "7496b7f7e005 aa5394e04149 23c193aabef8 9af96c6fbb71 03942b2cda11 31303160ebdf 4514f449d571 87fb72ba7d40 "
        "ef3859339abf a9230427ed4a 4f72df294229 730bf48db468 b2d58182435b 44d2a5f8d337 6baf9ea6ca64 f88b93827baf"
    ).split(),
    "local": (
        "08b3615bb694 ad106af1674a 8c6be4a65e5c e283f0ec4d06 ff04a07d15f0 5063a7c9b1ad 6a67cd4f10e5 bf495750037c "
        "2aeb7772809b 171a59c97145 e7f474b08bcb 4a938aa0059b a3da111b2dab 48ebd46c5787 6d5844f1414b c9fa031fd8d8 "
        "247c0ceb99b7 113e7b796aa1 4b0e99beadf1 67f1c7f0337e bc7f03871b56 63e0bd890905 910de901591c 2478436aae33 "
        "3987e12d1987 9b5daa5270d7 df72a68ad139 a9c03cce9a9a 31d09987cd03 823af356403e e9757b0d86d5 fd2176d9fa4f "
        "3e881cf7d572 f845b2a5aa66 ea2d20c8e743 9d4755082634 703b3d213578 d29a29c72a82 6b7fa37bedfd 02255ba64d35"
    ).split(),
    "fcf_off": (
        "8a8e5aec1467 b4f91dc7ca69 a3aba9c1f66b e283f0ec4d06 f0447932466d 9237c382dc54 48b5b45b7fb1 4d92ac43ccb8 "
        "339cb599d4e2 b105d902bd49 71b720a38034 de976b66f4d5 4fd4b1997208 33209919c1b9 69b3d189c60b 3ff79b22afff "
        "a85f605a609d 1930cbd38667 d2e9cd267c85 91299749d824 1166e262260f 92a530929157 9d7f47bc961f ccd97285ff63 "
        "2651af80faee b1a105c43d32 64b9bf945e61 91f02f4372c6 b7bfc6eb1c15 839a7f24cdd2 a919f924e9d9 4dd6f776722d "
        "cec1add196ec afeb130ee98d 106c44ba7703 c049b4271abb 7cb897f51b76 bc87b10f1c0c b13eef36e489 3b346894f834"
    ).split(),
    "f8b": (
        "d5ac389fbdc7 886cff86417c ed5a8bc185a1 4fda921706f2 82c7784702de 32f6a2e8108b 1b841b07be1a f35e55075ecc "
        "8fe483561303 dd9e96118629 27b8c458743f d2efa231e39e c772c6d0908d b58bffcceab7 5c1eadb99f6d 187eb5cac7b8 "
        "4aabbabd20db 0025e9aacd92 10867bceea74 a8ca05ea8edf 6d6f39a0d995 2bf6faa9c937 56e05f41f034 193a27752d8d "
        "7496b7f7e005 107466b87e64 23c193aabef8 713686d9b422 7f1c0a274e8d 31303160ebdf a0d86128e914 3decd519c1b1 "
        "8e636bffe05a ab48dd3f0b73 4f72df294229 730bf48db468 e3307ff08662 5e44de240753 0d48ce666588 5fdfac05b5c4"
    ).split(),
    "calls_plus": (
        "08b3615bb694 3e4509f8c39b afd0fce348cb e283f0ec4d06 c2c43c8f985e 59b142f14a86 398f7b22549c b0a128c2d63a "
        "830423dd245a a3fa79dba274 e7f474b08bcb ef1a95975725 a3da111b2dab 054427a698c2 9873c8dbb503 43bc49266a3d "
        "43b0d9520c6c 4f652eca3b06 2106eab0b2a7 9031301f40f3 b20eac12a65a 63e0bd890905 af133154f5ab e6058777b126 "
        "3987e12d1987 9f0fb7655591 df72a68ad139 806cef19ea94 31d09987cd03 823af356403e 88eba244f0b1 1f8a345925ad "
        "e19edc24fb03 098dfa28c048 ea2d20c8e743 9d4755082634 715604929bfa e3ab6d79bee1 3f6d74393796 5d7f1be79ce8"
    ).split(),
    "ast": (
        "476ede6bbb87 df90645ddfe2 1e9c1aa0a40e 8bf1717a1e68 e8bcc3cc4c03 d797508a4457 084e9174158a bb84217090c3 "
        "9dc352a95911 060c9ad04876 52032196fa1f bf4df7aad21f 141bf6c1e6fa 5c0b83c9261f a002689fc27d 788380ffe18d "
        "9066c217b92a baf1e0aeb473 3c0f11eb1923 985310005381 e77a7b7b20d8 e2a0c346e6b3 d3a5ac9db900 1bf941a20ab0 "
        "932cb7b481a9 5f2538125c56 e1c72da16379 721a2a204921 7e8cb71460d6 7c6ba7388406 62c5e6132ddf 77d3126ff211 "
        "7e3c9562e77c a091cdf39fa9 beacfeb39d98 4f50f6e33e71 1a4604dfe541 1aaca2da5aac a9f1bd53ad0d 2a3c43e3aaac"
    ).split(),
}


def corpus_digests(Sm, mode_names=None) -> dict:
    """{mode: [digest per seed]} of the push-side passes of module `Sm` over the synthetic corpus."""
    out = {}
    base = tmp() / "corpus"
    for mode in (mode_names or MODES):
        env, gen = MODES[mode]
        ds = []
        for seed in SEEDS:
            files, nodes, calls = gen_project(seed, gen)
            root = write_tree(base / mode / f"s{seed}", files)
            with flags(**env), quiet():
                res = chain(Sm, root, nodes, calls)
            ds.append(digest(canonical(*res)))
        out[mode] = ds
    return out


def sec_a():
    print("\n-- A: flag-off byte identity — the push passes vs the frozen pre-S2 module, 9 modes x 40 projects --")
    if not FROZEN:
        check("A: the frozen digests are present (run --freeze against the pre-S2 sync.py)", False)
        return
    got = corpus_digests(S)
    for mode, want in FROZEN.items():
        have = got.get(mode, [])
        bad = [i for i, (a, b) in enumerate(zip(have, want)) if a != b]
        check(f"A [{mode}]: all {len(want)} projects give the pre-S2 calls / attributes / edges byte for byte",
              len(have) == len(want) and not bad, f"seeds differing: {bad[:10]}")
    # the corpus is not vacuous: it reaches every branch the frozen modes care about
    stats = {"keys": 0, "fanout": 0, "local_defs": 0, "call_bare": 0, "call_alias": 0, "call_local": 0,
             "multi_family": 0}
    for mode in ("off", "precise", "local", "ast", "precise_local"):
        env, gen = MODES[mode]
        for seed in list(SEEDS)[:12]:
            files, nodes, calls = gen_project(seed, gen)
            root = write_tree(tmp() / "corpus" / mode / f"s{seed}", files)
            with flags(**env), quiet():
                ns, cs, edges, _c = chain(S, root, nodes, calls)
            stats["keys"] += sum(1 for _a, b in cs if GX.is_call_key(b))
            stats["fanout"] += sum(1 for c in edges.values() if c == "AMBIGUOUS")
            for a in ("local_defs", "call_bare", "call_alias", "call_local"):
                stats[a] += sum(1 for n in ns if a in n)
            fams = {ER.lang_family(n["source_file"]) for n in ns}
            stats["multi_family"] += len(fams) > 2
    check("A: the corpus reaches every branch (CallKeys, AMBIGUOUS fan-outs, local_defs, call_bare, call_alias, "
          "call_local, multi-language projects)", all(v > 0 for v in stats.values()), stats)


def freeze(path: Path) -> int:
    Sp = load_module(path / "sync.py", "dm_s2_frozen_sync")
    got = corpus_digests(Sp)
    sha = hashlib.sha256((path / "sync.py").read_bytes()).hexdigest()
    print(f'FROZEN_FROM = "{sha}"')
    print("FROZEN: dict = {")
    for mode, ds in got.items():
        print(f'    "{mode}": "{" ".join(ds)}".split(),')
    print("}")
    return 0


# =====================================================================================================
# B — the per-tuple rules (pure, hand-made inputs)
# =====================================================================================================
def b_nodes():
    return [node("a.py::module", "Module", "a", "a.py"),
            node("a_caller", "Function", "caller", "a.py", 3),
            node("a_save_local", "Function", "save", "a.py", 8),
            node("b_order", "Class", "Order", "b.py", 1),
            node("b_order_save", "Method", "Order.save", "b.py", 2),
            node("c_invoice_save", "Method", "Invoice.save", "c.py", 2),
            node("w_order_save", "Method", "Order.save", "web/w.ts", 2),
            node("tests_fx", "Function", "fixture", "tests/test_x.py", 1),
            node("g_run", "Function", "Run", "svc/g.go", 3),
            node("g_helper_test", "Function", "Helper", "svc/g_test.go", 3),
            node("h_build", "Function", "Build", "svc/h.go", 3)]


def with_attr(nodes, key, **attrs):
    for n in nodes:
        if n["key"] == key:
            n.update(attrs)
    return nodes


def resolve(Sm, nodes, calls, **env):
    with flags(**dict(OFF, **env)):
        return Sm._resolve_calls(nodes, calls)


def b_cases(Sm, rec):
    # B1: a lone CallKey in default mode binds exactly its node (F3b grades it)
    e = resolve(Sm, b_nodes(), [("a_caller", CK("b_order_save"))])
    rec.check("B1: default mode (every flag off) — a lone CallKey binds exactly its node, cross-file INFERRED",
              e == {("a_caller", "b_order_save"): "INFERRED"}, e)
    e = resolve(Sm, b_nodes(), [("a_caller", CK("a_save_local"))])
    rec.check("B1: ... a same-file CallKey in default mode -> EXTRACTED (F3b)",
              e == {("a_caller", "a_save_local"): "EXTRACTED"}, e)
    e = resolve(Sm, b_nodes(), [("a_caller", "b_order_save")])
    rec.check("B1: ... an UNMARKED name equal to a node key is still a name (no node is named that: no edge)",
              e == {}, e)
    # B3: call_typed grades exactly
    ns = with_attr(b_nodes(), "a_caller", call_typed={"a_save_local": "INFERRED"})
    e = resolve(Sm, ns, [("a_caller", CK("a_save_local"))])
    rec.check("B3: a call_typed grade is honoured exactly — a same-file typed call stays INFERRED (not F3b's "
              "EXTRACTED)", e == {("a_caller", "a_save_local"): "INFERRED"}, e)
    ns = with_attr(b_nodes(), "a_caller", call_typed={"b_order_save": "AMBIGUOUS", "c_invoice_save": "AMBIGUOUS"})
    e = resolve(Sm, ns, [("a_caller", CK("b_order_save")), ("a_caller", CK("c_invoice_save"))])
    rec.check("B3: ... a candidate set: each member AMBIGUOUS",
              e == {("a_caller", "b_order_save"): "AMBIGUOUS", ("a_caller", "c_invoice_save"): "AMBIGUOUS"}, e)
    ns = with_attr(b_nodes(), "a_caller", call_typed={"b_order_save": "BOGUS"})
    e = resolve(Sm, ns, [("a_caller", CK("b_order_save"))])
    rec.check("B3: ... a grade outside EXTRACTED / INFERRED / AMBIGUOUS is ignored (the keyed branch's own grade)",
              e == {("a_caller", "b_order_save"): "INFERRED"}, e)
    # B4: T3#6 and the LOCAL_DEF re-bind are skipped; the family / F8b / Go drops apply
    ns = with_attr(b_nodes(), "a_caller", local_defs=["save"], call_typed={"b_order_save": "INFERRED"})
    e = resolve(Sm, ns, [("a_caller", CK("b_order_save"))], DEEPMAP_PRECISE_CALLS="1")
    ctl = resolve(Sm, with_attr(b_nodes(), "a_caller", local_defs=["save"]), [("a_caller", CK("b_order_save"))],
                  DEEPMAP_PRECISE_CALLS="1")
    rec.check("B4: a typed key skips T3#6 (the caller's scope defines `save`): INFERRED, where the untyped precise "
              "key is AMBIGUOUS", e == {("a_caller", "b_order_save"): "INFERRED"}
              and ctl == {("a_caller", "b_order_save"): "AMBIGUOUS"}, (e, ctl))
    ns = with_attr(b_nodes(), "a_caller", call_local={"save": ["a_save_local"]},
                   call_typed={"b_order_save": "INFERRED"})
    e = resolve(Sm, ns, [("a_caller", CK("b_order_save"))], DEEPMAP_PRECISE_CALLS="1", DEEPMAP_LOCAL_DEF_CALLS="1")
    ctl = resolve(Sm, with_attr(b_nodes(), "a_caller", call_local={"save": ["a_save_local"]}),
                  [("a_caller", CK("b_order_save"))], DEEPMAP_PRECISE_CALLS="1", DEEPMAP_LOCAL_DEF_CALLS="1")
    rec.check("B4: a typed key is never re-bound by DEEPMAP_LOCAL_DEF_CALLS (the untyped precise key is)",
              e == {("a_caller", "b_order_save"): "INFERRED"} and ctl == {("a_caller", "a_save_local"): "EXTRACTED"},
              (e, ctl))
    ns = with_attr(b_nodes(), "a_caller", call_typed={"w_order_save": "INFERRED", "b_order_save": "INFERRED"})
    e = resolve(Sm, ns, [("a_caller", CK("w_order_save")), ("a_caller", CK("b_order_save"))])
    rec.check("B4: a typed key into another language family is dropped (the keyed branch's family rule); a same-"
              "family one is kept", e == {("a_caller", "b_order_save"): "INFERRED"}, e)
    ns = with_attr(b_nodes(), "a_caller", call_typed={"tests_fx": "INFERRED"})
    e = resolve(Sm, ns, [("a_caller", CK("tests_fx"))], DEEPMAP_DROP_APP_TO_TEST_CALLS="1")
    e2 = resolve(Sm, ns, [("a_caller", CK("tests_fx"))])
    rec.check("B4: F8b drops a typed app -> test edge (opt-in), and keeps it with F8b off",
              e == {} and e2 == {("a_caller", "tests_fx"): "INFERRED"}, (e, e2))
    ns = with_attr(b_nodes(), "g_run", call_typed={"g_helper_test": "INFERRED", "h_build": "INFERRED"})
    e = resolve(Sm, ns, [("g_run", CK("g_helper_test")), ("g_run", CK("h_build"))])
    rec.check("B4: a Go production caller never gets a typed edge into a _test.go file; a production target stays",
              e == {("g_run", "h_build"): "INFERRED"}, e)
    # B5: call_weak — last, only lowers
    ns = with_attr(b_nodes(), "a_caller", call_weak=["a_save_local"])
    e = resolve(Sm, ns, [("a_caller", CK("a_save_local"))], DEEPMAP_PRECISE_CALLS="1")
    rec.check("B5: call_weak turns an existing (same-file EXTRACTED) edge AMBIGUOUS",
              e == {("a_caller", "a_save_local"): "AMBIGUOUS"}, e)
    ns = with_attr(b_nodes(), "a_caller", call_weak=["c_invoice_save"])
    e = resolve(Sm, ns, [("a_caller", CK("a_save_local"))], DEEPMAP_PRECISE_CALLS="1")
    rec.check("B5: call_weak never ADDS an edge (a target with no edge stays without one)",
              e == {("a_caller", "a_save_local"): "EXTRACTED"}, e)
    one = [node("a_caller", "Function", "caller", "a.py", 3), node("b_order_send", "Method", "Order.send", "b.py", 2)]
    e0 = resolve(Sm, copy.deepcopy(one), [("a_caller", "send")])
    e = resolve(Sm, with_attr(copy.deepcopy(one), "a_caller", call_weak=["b_order_send"]), [("a_caller", "send")])
    rec.check("B5: call_weak applies AFTER the name branch: a single-candidate INFERRED fan-out edge -> AMBIGUOUS",
              e0 == {("a_caller", "b_order_send"): "INFERRED"} and e == {("a_caller", "b_order_send"): "AMBIGUOUS"},
              (e0, e))
    calls = [("a_caller", "save"), ("a_caller", CK("a_save_local"))]
    base = resolve(Sm, b_nodes(), calls, DEEPMAP_PRECISE_CALLS="1")
    weak = resolve(Sm, with_attr(b_nodes(), "a_caller", call_weak=["a_save_local", "b_order_save", "zz"]), calls,
                   DEEPMAP_PRECISE_CALLS="1")
    rank = {"AMBIGUOUS": 0, "INFERRED": 1, "EXTRACTED": 2}
    rec.check("B5: only lowers — the same edges in the same order, no grade raised, the named ones AMBIGUOUS",
              list(base) == list(weak) and all(rank[weak[k]] <= rank[base[k]] for k in base)
              and weak[("a_caller", "a_save_local")] == weak[("a_caller", "b_order_save")] == "AMBIGUOUS", (base, weak))
    # B6: a typed key appended after a fan-out wins
    ns = with_attr(b_nodes(), "a_caller", call_typed={"b_order_save": "INFERRED"})
    e = resolve(Sm, ns, [("a_caller", "save"), ("a_caller", CK("b_order_save"))])
    rec.check("B6: a typed key appended after a name fan-out wins its pair (INFERRED); the kept fan-out edges stay "
              "AMBIGUOUS, first-seen order kept",
              e.get(("a_caller", "b_order_save")) == "INFERRED"
              and e.get(("a_caller", "c_invoice_save")) == "AMBIGUOUS"
              and list(e)[:2] == [("a_caller", "a_save_local"), ("a_caller", "b_order_save")], e)


def b_files_cases(Sm, rec):
    base = tmp() / f"b_{id(Sm)}"
    # B2: _attach_bare_calls never reads a CallKey as a name (any flags)
    root = write_tree(base / "b2", {"a.py": "import os\n\n\ndef caller():\n    save()\n",
                                     "s.py": "def save():\n    return 1\n",
                                     "b.py": "class Order:\n    def save(self):\n        return 2\n"})
    ns = [node("a.py::m", "Module", "a", "a.py"), node("a_caller", "Function", "caller", "a.py", 4),
          node("save", "Function", "save", "s.py", 1), node("b_order_save", "Method", "Order.save", "b.py", 2)]
    with flags(**OFF):
        Sm._attach_bare_calls(root, ns, [("a_caller", CK("save"))])
    rec.check("B2: _attach_bare_calls skips a CallKey whatever the flags (it is a resolved key, not a bare name)",
              "call_bare" not in ns[1], ns[1].get("call_bare"))
    ns2 = copy.deepcopy(ns)
    with flags(**OFF):
        Sm._attach_bare_calls(root, ns2, [("a_caller", "save")])
    rec.check("B2: ... while the NAME `save` (every site bare) is judged bare-only (not vacuous)",
              ns2[1].get("call_bare") == ["save"], ns2[1].get("call_bare"))
    # B4b: _attach_local_defs never attaches for a typed key
    root = write_tree(base / "b4", {"a.py": "def helper():\n    return 0\n\n\ndef caller():\n    return o.save()\n",
                                     "b.py": "class Order:\n    def save(self):\n        return 2\n"})
    mk = lambda **a: [node("a.py::m", "Module", "a", "a.py"), node("a_caller", "Function", "caller", "a.py", 5, **a),
                      node("a_helper", "Function", "helper", "a.py", 1),
                      node("b_order_save", "Method", "Order.save", "b.py", 2)]  # noqa: E731
    typed = mk(call_typed={"b_order_save": "INFERRED"})
    plain = mk()
    with flags(**OFF):
        Sm._attach_local_defs(root, typed, [("a_caller", CK("b_order_save"))])
        Sm._attach_local_defs(root, plain, [("a_caller", CK("b_order_save"))])
    rec.check("B4: _attach_local_defs attaches nothing for a typed key (T3#6 never grades it) ...",
              "local_defs" not in typed[1], typed[1].get("local_defs"))
    rec.check("B4: ... while the same key without call_typed (default mode) does (not vacuous)",
              plain[1].get("local_defs") == ["caller", "helper"], plain[1].get("local_defs"))
    # B7 + B8: a NAME equal to a node key (a root cli.py's Module `cli`) — the anti-confound and S1 finding 5
    root = write_tree(base / "b7", {"cli.py": "def cli():\n    return 1\n",
                                     "main.py": "import os\n\n\ndef run():\n    return cli()\n"})
    shape = ([node("cli", "Module", "cli", "cli.py"), node("cli_cli", "Function", "cli", "cli.py", 1),
              node("main", "Module", "main", "main.py"), node("main_run", "Function", "run", "main.py", 4)],
             [("main_run", "cli")])

    def run_shape(**env):
        with flags(**dict(OFF, **env)), quiet():
            ns_, cs_, e_, _c = chain(Sm, root, *shape)
        return {n["key"]: {a: norm(n[a]) for a in ATTRS if a in n} for n in ns_}, e_, [(a, str(b)) for a, b in cs_]

    v0 = run_shape()
    rec.check("B7: the shape — with every flag off `cli` reads as the Module key: main_run gets local_defs",
              v0[0]["main_run"].get("local_defs") == ["run"], v0)
    vt = run_shape(DEEPMAP_TYPED_CALLS="python", DEEPMAP_CALLS_AUDIT="python")
    rec.check("B7: anti-confound — TYPED alone handles the unmarked NAME exactly as V0 (keyed_mode stays off; the "
              "name branch; same attributes, edges and tuples)", vt == v0, (v0, vt))
    for csv in ("js", "go,ruby", "rust"):
        vc = run_shape(DEEPMAP_PRECISE_CALLS=csv)
        rec.check(f"B8: S1 finding 5 — DEEPMAP_PRECISE_CALLS={csv} (no Python caller precise) gives the Python "
                  f"callers exactly the unset run's attributes and edges", vc == v0, (v0, vc))
    vp1, vpy = run_shape(DEEPMAP_PRECISE_CALLS="1"), run_shape(DEEPMAP_PRECISE_CALLS="python")
    rec.check("B8: ... and PRECISE=python keys Python callers exactly as PRECISE=1 does here (keyed reading on)",
              vpy == vp1 and "local_defs" not in vp1[0]["main_run"], (vp1, vpy))


# B10 (S2 review r1 finding 1): a typed key must not change how DEEPMAP_LOCAL_DEF_CALLS judges the caller's OTHER call
# names. `O()` is typed to b.py's Order (a CallKey whose node's name, `Order`, is no call site of `caller`); were it
# counted, _attach_local_def_calls would find a callee without a site and skip the whole caller, so the bare
# `helper()` would fan out to b.py's helper too.
B10_TREE = {"a.py": "from b import Order as O\n\n\ndef helper():\n    return 1\n\n\ndef caller():\n    o = O()\n"
                    "    return helper()\n",
            "b.py": "class Order:\n    def save(self):\n        return 1\n\n\ndef helper():\n    return 2\n"}


def b_local_typed(Sm, rec):
    if _have_typed() is None:
        rec.check("B10: build/typed_calls.py and receiver_facts_py.py are importable", False)
        return
    root = write_tree(tmp() / f"b10_{id(Sm)}", B10_TREE)
    out = {}
    for label, typed in (("typed", "python"), ("untyped", "0")):
        env = dict(OFF, DEEPMAP_EXTRACTOR="ast", DEEPMAP_LOCAL_DEF_CALLS="1", DEEPMAP_TYPED_CALLS=typed)
        with flags(**env), quiet():
            ex = Sm._rebuild_code(root)
            ns, cs, edges, _c = chain(Sm, root, ex[0], ex[1])
        keys = {(n["source_file"], n["name"]): n["key"] for n in ns}
        caller = next((n for n in ns if n["name"] == "caller"), {})
        out[label] = (edges, caller.get("call_local"), caller.get("call_typed"), keys)
    edges, local, typed_attr, keys = out["typed"]
    ck, a_helper, b_helper, order = (keys.get(("a.py", "caller")), keys.get(("a.py", "helper")),
                                     keys.get(("b.py", "helper")), keys.get(("b.py", "Order")))
    rec.check("B10: not vacuous — TYPED types `O()` to b.py's Order (a typed key whose name is no call site of caller)",
              isinstance(typed_attr, dict) and order in typed_attr and edges.get((ck, order)) == typed_attr.get(order),
              (typed_attr, edges))
    rec.check("B10: TYPED + DEEPMAP_LOCAL_DEF_CALLS — the caller's bare `helper()` is still decided local "
              "(call_local {'helper': [a.py's helper]}), exactly as with TYPED off",
              local == {"helper": [a_helper]} and out["untyped"][1] == {"helper": [a_helper]}, (local, out["untyped"][1]))
    rec.check("B10: ... so no caller -> b.py's helper edge, and caller -> a.py's helper keeps the TYPED-off grade",
              (ck, b_helper) not in edges and a_helper is not None
              and edges.get((ck, a_helper)) == out["untyped"][0].get((ck, a_helper)) not in (None, "AMBIGUOUS"),
              (edges, out["untyped"][0]))


def b_upsert(Sm, rec):
    rows = []

    class Sess:
        def run(self, cypher, **p):
            rows.extend(p.get("rows") or [])

    ns = with_attr(b_nodes(), "a_caller", call_typed={"b_order_save": "INFERRED"}, call_weak=["c_invoice_save"],
                   call_member=["save"], local_defs=["save"])
    with flags(**OFF):
        Sm._upsert_nodes(Sess(), ns)
    keys = {k for r in rows for k in r}
    rec.check("B9: _upsert_nodes rows never carry call_typed / call_weak (a fixed field list)",
              rows and keys == {"key", "name", "sf", "line", "summ", "conf"}, sorted(keys))


def sec_b():
    print("\n-- B: the per-tuple rules: CallKey, call_typed, call_weak, T3#6, LOCAL_DEF, keyed mode per family --")
    b_cases(S, LOUD)
    b_files_cases(S, LOUD)
    b_local_typed(S, LOUD)
    b_upsert(S, LOUD)


# =====================================================================================================
# C — the typed step's wiring
# =====================================================================================================
def pipeline_steps(src: str):
    """(ordered step calls in the function that runs extraction -> push, the target of the typed call, helper
    params) read from sync.py's AST."""
    tree = ast.parse(src)
    top = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    pipe = None
    for fn in top.values():
        names = {getattr(c.func, "id", None) for c in ast.walk(fn) if isinstance(c, ast.Call)}
        if "_rebuild_code" in names and "push_to_neo4j" in names:
            pipe = fn
            break
    if pipe is None:
        return None, None, None
    want = ("_rebuild_code", "_unkey_receiver_calls", "_typed_calls_link", "_attach_bare_calls", "_attach_local_defs",
            "_attach_local_def_calls", "push_to_neo4j")
    calls = sorted(((c.lineno, c.col_offset, c.func.id) for c in ast.walk(pipe)
                    if isinstance(c, ast.Call) and isinstance(c.func, ast.Name) and c.func.id in want))
    target = None
    for n in ast.walk(pipe):
        if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call) \
                and getattr(n.value.func, "id", None) == "_typed_calls_link":
            target = ast.unparse(n.targets[0]) if hasattr(ast, "unparse") else "?"
            args = [ast.unparse(a) for a in n.value.args] if hasattr(ast, "unparse") else []
            target = (target, args, [k.arg for k in n.value.keywords])
    helper = top.get("_typed_calls_link")
    params = [a.arg for a in helper.args.args] if helper else None
    return [c[2] for c in calls], target, params


SIG_WORDS = {"bare": "none", "kw": "**kw", "sources": "the core contract: sources = {rel: reader} + source="}


class FakeTC(types.ModuleType):
    """A stand-in typed_calls: records link's kwargs, appends one typed tuple, says one line."""

    def __init__(self, sig="full", on=True, raise_exc=None):
        super().__init__("fake_typed_calls")
        self.seen, self.on, self.raise_exc = [], on, raise_exc
        mk = {
            "full": lambda: (lambda root, nodes, calls, *, is_test=None, engine=None, source=None:
                             self._link(root, nodes, calls, is_test=is_test, engine=engine, source=source)),
            "bare": lambda: (lambda root, nodes, calls: self._link(root, nodes, calls)),
            "kw": lambda: (lambda root, nodes, calls, **kw: self._link(root, nodes, calls, **kw)),
            "sources": lambda: (lambda root, nodes, calls, *, is_test=None, engine=None, source=None, sources=None:
                                self._link(root, nodes, calls, is_test=is_test, engine=engine, source=source,
                                           sources=sources)),
        }
        self.link = mk[sig]()

    def enabled(self, env=None):
        return self.on

    def _link(self, root, nodes, calls, **kw):
        self.seen.append(kw)
        if self.raise_exc:
            raise self.raise_exc
        return list(calls) + [(nodes[0]["key"], CK(nodes[-1]["key"]))], {"emitted": 1, "version": 2}

    def describe(self, stats):
        if stats.get("error"):
            return f"FAKE skipped ({stats['error']})"
        return f"FAKE LINE emitted={stats.get('emitted')}"


def c_wiring(Sm, rec, src_path: Path):
    steps, target, params = pipeline_steps(src_path.read_text(encoding="utf-8"))
    order_ok = bool(steps) and "_typed_calls_link" in steps and steps.index("_unkey_receiver_calls") \
        < steps.index("_typed_calls_link") < steps.index("_attach_bare_calls")
    rec.check("C1: sync.py's pipeline calls _typed_calls_link after _unkey_receiver_calls and before "
              "_attach_bare_calls (read from its AST), exactly once",
              order_ok and steps.count("_typed_calls_link") == 1,
              steps)
    rec.check("C1: ... as `calls, call_stats = _typed_calls_link(root, nodes, calls)` (the call offline_calls "
              "mirrors), defined at module level with (root, nodes, calls)",
              target is not None and target[0].replace(" ", "").strip("()") == "calls,call_stats"
              and target[1] == ["root", "nodes", "calls"] and not target[2] and params == ["root", "nodes", "calls"],
              (target, params))
    root = tmp() / f"c_{id(Sm)}"
    root.mkdir(parents=True, exist_ok=True)
    ns = [node("m_f", "Function", "f", "m.py", 1), node("m_g", "Function", "g", "m.py", 4)]
    calls = [("m_f", "g")]
    step = getattr(Sm, "_typed_calls_link", None)
    if not callable(step):
        rec.check("C2: sync defines _typed_calls_link", False)
        return
    with flags(**OFF), patched(Sm, _typed_calls_module=lambda: None), captured_stderr() as err:
        out = step(root, ns, calls)
    rec.check("C2: typed_calls not importable -> the SAME list object, stats None, nothing printed",
              out[0] is calls and out[1] is None and not err.getvalue(), (out, err.getvalue()))
    with flags(**OFF), captured_stderr() as err:
        out = step(root, ns, calls)
    rec.check("C3: DEEPMAP_TYPED_CALLS=0 and DEEPMAP_CALLS_AUDIT=0 -> the SAME list object, stats None, no line",
              out[0] is calls and out[1] is None and not err.getvalue(), (out, err.getvalue()))
    fake = FakeTC()
    with flags(**OFF), patched(Sm, _typed_calls_module=lambda: fake), captured_stderr() as err:
        Sm._drop_extraction_note(root)
        out = step(root, ns, calls)
    kw = fake.seen[0] if fake.seen else {}
    rec.check("C4: the pass on -> typed_calls.link called once with is_test = sync._is_test_path, its output "
              "returned, exactly one stderr line (typed_calls.describe)",
              len(fake.seen) == 1 and kw.get("is_test") is Sm._is_test_path and out[0][-1] == ("m_f", "m_g")
              and GX.is_call_key(out[0][-1][1]) and out[1] == {"emitted": 1, "version": 2}
              and err.getvalue().count("\n") == 1 and "[deepmap sync] FAKE LINE emitted=1" in err.getvalue(),
              (fake.seen, out, err.getvalue()))
    rec.check("C4: ... no note -> no engine / source passed (typed_calls' own defaults)",
              kw.get("engine") is None and kw.get("source") is None, kw)
    for sig in ("bare", "kw", "sources"):
        fk = FakeTC(sig)
        src_obj = GX._Source(root)
        with flags(**OFF), patched(Sm, _typed_calls_module=lambda fk=fk: fk), quiet():
            step(root, ns, calls, engine="ast", source=src_obj)
        got = fk.seen[0] if fk.seen else None
        want = {"bare": {}, "kw": {"is_test": Sm._is_test_path, "engine": "ast", "source": src_obj},
                "sources": {"is_test": Sm._is_test_path, "engine": "ast", "source": src_obj,
                            "sources": {"m.py": src_obj}}}[sig]
        rec.check(f"C4: link's signature is honoured ({sig}): exactly the keywords it takes "
                  f"({SIG_WORDS[sig]})",
                  got == want, (got, want))
    fk = FakeTC(raise_exc=RuntimeError("boom"))
    with flags(**OFF), patched(Sm, _typed_calls_module=lambda: fk), captured_stderr() as err:
        out = step(root, ns, calls)
    rec.check("C4: link raising -> the input list, stats {'error': ...}, a 'skipped' line (never breaks a sync)",
              out[0] is calls and "boom" in str((out[1] or {}).get("error"))
              and err.getvalue().strip() == "[deepmap sync] FAKE skipped (RuntimeError: boom)",
              (out, err.getvalue()))


def c_note(Sm, rec):
    root = write_tree(tmp() / f"cn_{id(Sm)}", {"pkg/__init__.py": "", "pkg/m.py": "def f():\n    return g()\n\n\n"
                                                                                  "def g():\n    return 1\n"})
    fake = FakeTC()
    with flags(**dict(OFF, DEEPMAP_EXTRACTOR="ast")), patched(Sm, _typed_calls_module=lambda: fake), quiet():
        ex = Sm._rebuild_code(root)
        nodes, calls = ex[0], ex[1]
        Sm._typed_calls_link(root, nodes, list(calls))
        Sm._typed_calls_link(root, nodes, list(calls))
    rec.check("C5: the extraction note — _rebuild_code (ast) hands the RESOLVED engine to the next "
              "_typed_calls_link only (one-shot)", [s.get("engine") for s in fake.seen] == ["ast", None], fake.seen)
    fake = FakeTC()
    with flags(**dict(OFF, DEEPMAP_EXTRACTOR="ast")), patched(Sm, _typed_calls_module=lambda: fake), quiet():
        ex = Sm._rebuild_code(root)
        Sm._typed_calls_link(root, [n for n in ex[0] if n["label"] == "Module"], list(ex[1]))
    rec.check("C5: ... a note whose node keys are not all in the nodes handed over is ignored (never another "
              "extraction's)", [s.get("engine") for s in fake.seen] == [None], fake.seen)
    fake = FakeTC()
    with flags(**dict(OFF, DEEPMAP_EXTRACTOR="ast")), patched(Sm, _typed_calls_module=lambda: fake), quiet():
        ex = Sm._rebuild_code(root)
        Sm._drop_extraction_note(root)
        Sm._typed_calls_link(root, ex[0], list(ex[1]))
    rec.check("C5: ... _drop_extraction_note forgets it (sync's pass drops a note it never used)",
              [s.get("engine") for s in fake.seen] == [None], fake.seen)
    with flags(**dict(OFF, DEEPMAP_EXTRACTOR="ast")), quiet():
        ex_off = Sm._rebuild_code(root)
    rec.check("C5: ... with the pass OFF _rebuild_code leaves no note (nothing kept alive)",
              Sm._note_key(root) not in Sm._EXTRACTION_NOTES and getattr(ex_off, "typed_note", 0) is None,
              sorted(Sm._EXTRACTION_NOTES))
    fake = FakeTC()
    with flags(**dict(OFF, DEEPMAP_EXTRACTOR="ast")), patched(Sm, _typed_calls_module=lambda: fake), quiet():
        nodes, calls, _imp, _eng = Sm._rebuild_code(root)      # unpacked only — enrich's enumeration does this
        import gc
        gc.collect()
        Sm._typed_calls_link(root, nodes, list(calls))
    rec.check("C5: ... the note lives exactly as long as its extraction: a caller that only unpacks it (enrich) "
              "keeps nothing alive for a pass that never comes", [s.get("engine") for s in fake.seen] == [None],
              fake.seen)
    fake = FakeTC()
    with flags(**dict(OFF, DEEPMAP_EXTRACTOR="ast")), patched(Sm, _typed_calls_module=lambda: fake), quiet():
        ex = Sm._rebuild_code(root)
    info = getattr(ex, "info", {}) or {}
    rec.check("C5: ... the note rides on the extraction (.typed_note: engine ast), never in its .info",
              getattr(getattr(ex, "typed_note", None), "engine", None) == "ast"
              and not any("note" in k or "typed" in k for k in info), (getattr(ex, "typed_note", None), sorted(info)))
    # C6: the shared reader through extract_with_report(source=)
    seen = []
    graph_nodes = [node("m", "Module", "m", "m.py"), node("m_f", "Function", "f", "m.py", 1),
                   node("m_g", "Function", "g", "m.py", 4)]

    def ewr_src(workspace_root, *, timeout=None, source=None):
        seen.append(source)
        return (copy.deepcopy(graph_nodes), [("m_f", "g")], []), {}

    def ewr_plain(workspace_root, *, timeout=None):
        seen.append("plain")
        return (copy.deepcopy(graph_nodes), [("m_f", "g")], []), {}
    fake = FakeTC()
    with flags(**dict(OFF, DEEPMAP_EXTRACTOR="graphify")), patched(Sm, _typed_calls_module=lambda: fake), \
            patched(GX, extract_with_report=ewr_src), quiet():
        ex = Sm._rebuild_code(root)
        Sm._typed_calls_link(root, ex[0], list(ex[1]))
    shared = seen[0] if seen else None
    rec.check("C6: the pass on and extract_with_report takes source= -> it gets a graphify_extract._Source of the "
              "resolved root, and that SAME object reaches typed_calls.link (engine graphify)",
              isinstance(shared, GX._Source) and Path(shared.root) == Path(root).resolve()
              and fake.seen and fake.seen[0].get("source") is shared and fake.seen[0].get("engine") == "graphify",
              (seen, fake.seen))
    rec.check("C6: ... and once the pass has run the extraction no longer pins that reader (its note's source is "
              "released: the parsed trees die with the step)",
              getattr(getattr(ex, "typed_note", None), "source", 0) is None, getattr(ex, "typed_note", None))
    seen.clear()
    with flags(**dict(OFF, DEEPMAP_EXTRACTOR="graphify")), patched(GX, extract_with_report=ewr_src), quiet():
        Sm._rebuild_code(root)
    rec.check("C6: ... the pass OFF -> extract_with_report is called exactly as before (no source=)",
              seen == [None], seen)
    seen.clear()
    fake = FakeTC()
    with flags(**dict(OFF, DEEPMAP_EXTRACTOR="graphify")), patched(Sm, _typed_calls_module=lambda: fake), \
            patched(GX, extract_with_report=ewr_plain), quiet():
        ex = Sm._rebuild_code(root)
        Sm._typed_calls_link(root, ex[0], list(ex[1]))
    rec.check("C6: ... an extract_with_report without source= is called with the root only; link then gets no "
              "source (it reads the files itself) but the engine", seen == ["plain"] and fake.seen
              and fake.seen[0].get("source") is None and fake.seen[0].get("engine") == "graphify", (seen, fake.seen))


C7_TREE = {"pkg/__init__.py": "",
           "pkg/models.py": "class Order:\n    def save(self):\n        return 1\n\n\nclass Invoice:\n"
                            "    def save(self):\n        return 2\n",
           "pkg/service.py": "from pkg.models import Order\n\n\ndef place():\n    o = Order()\n    return o.save()\n"}


def _have_typed():
    try:
        tc = importlib.import_module("typed_calls")
        importlib.import_module("receiver_facts_py")
        return tc if callable(getattr(tc, "link", None)) else None
    except Exception:  # noqa: BLE001
        return None


def c_real(Sm, rec):
    if _have_typed() is None:
        rec.check("C7: build/typed_calls.py and receiver_facts_py.py are importable", False)
        return
    root = write_tree(tmp() / f"c7_{id(Sm)}", C7_TREE)
    out = {}
    for label, env in (("off", OFF), ("on", dict(OFF, DEEPMAP_TYPED_CALLS="python"))):
        with flags(**dict(env, DEEPMAP_EXTRACTOR="ast")), captured_stderr() as err, \
                contextlib.redirect_stdout(io.StringIO()):
            ex = Sm._rebuild_code(root)
            ns, cs, edges, counts = chain(Sm, root, ex[0], ex[1])
        keys = {n["name"]: n["key"] for n in ns}
        out[label] = (edges, counts["stats"], err.getvalue(), keys, ex)
    e_on, st, err_on, keys, ex = out["on"]
    place, osave, isave = keys.get("place"), keys.get("Order.save"), keys.get("Invoice.save")
    rec.check("C7: the real typed_calls (ast engine): `o = Order(); o.save()` -> place -> Order.save INFERRED "
              "(cross-file typed); the Invoice.save fan-out is replaced",
              e_on.get((place, osave)) == "INFERRED" and (place, isave) not in e_on, e_on)
    e_off = out["off"][0]
    rec.check("C7: ... with the flags off the fan-out is today's (both save methods)",
              (place, osave) in e_off and (place, isave) in e_off, e_off)
    rec.check("C7: ... stats returned (emitted >= 1, engine ast) and ONE '[deepmap sync] typed call linking' line",
              isinstance(st, dict) and st.get("emitted", 0) >= 1 and st.get("engine") == "ast"
              and err_on.count("[deepmap sync] typed call linking") == 1, (st, err_on[-400:]))
    info = getattr(ex, "info", {}) or {}
    rec.check("D4: _rebuild_code's .info never carries the typed pass's stats (the verdict never sees them)",
              not any("typed" in k or "call_link" in k for k in info), sorted(info))
    import inspect
    if importlib.util.find_spec("graphify") is None \
            or "source" not in inspect.signature(GX.extract_with_report).parameters:
        rec.check("C7: the real engine + graphify_extract's source= seam are present for the shared-reader run", False,
                  "graphify not importable or extract_with_report has no source= keyword")
        return
    root = write_tree(tmp() / f"c7g_{id(Sm)}", C7_TREE)
    with flags(**dict(OFF, DEEPMAP_EXTRACTOR="graphify", DEEPMAP_TYPED_CALLS="python")), captured_stderr() as err, \
            contextlib.redirect_stdout(io.StringIO()):
        ex = Sm._rebuild_code(root)
        ns, cs, edges, counts = chain(Sm, root, ex[0], ex[1])
    st = counts["stats"] or {}
    names = {n["name"]: n["key"] for n in ns}
    pl, osv = names.get("place"), names.get("Order.save")
    rec.check("C7: the REAL engine (graphify) — the extraction's reader is shared with the typed pass "
              "(stats sources_shared >= the 2 Python files it typed from; engine graphify) and place -> Order.save "
              "is typed INFERRED",
              ex[3] == "graphify" and st.get("engine") == "graphify" and int(st.get("sources_shared") or 0) >= 2
              and edges.get((pl, osv)) == "INFERRED",
              (ex[3], {k: st.get(k) for k in ("engine", "sources_shared", "emitted", "error")}, err.getvalue()[-300:]))


PURE_PASSES = ("_unkey_receiver_calls", "_typed_calls_link", "_attach_bare_calls", "_attach_local_defs",
               "_attach_local_def_calls")


def pass_positions(src: str):
    """From sync.py's AST, in the function that runs extraction -> push: {pure pass: [lines]}, and the lines of the
    background refresh's write checkpoint (`<ctl>.checkpoint(to_phase="write")`), its pre_write veto call, the manual
    path's `_phase(root, None, "write")` and push_to_neo4j. None when no such function."""
    tree = ast.parse(src)
    pipe = None
    for fn in tree.body:
        if isinstance(fn, ast.FunctionDef):
            names = {getattr(c.func, "id", None) for c in ast.walk(fn) if isinstance(c, ast.Call)}
            if "_rebuild_code" in names and "push_to_neo4j" in names:
                pipe = fn
                break
    if pipe is None:
        return None
    out = {"passes": {}, "checkpoint": [], "pre_write": [], "phase_write": [], "push": []}
    for c in ast.walk(pipe):
        if not isinstance(c, ast.Call):
            continue
        f = c.func
        if isinstance(f, ast.Name) and f.id in PURE_PASSES:
            out["passes"].setdefault(f.id, []).append(c.lineno)
        elif isinstance(f, ast.Name) and f.id == "push_to_neo4j":
            out["push"].append(c.lineno)
        elif isinstance(f, ast.Name) and f.id == "_phase" \
                and any(isinstance(a, ast.Constant) and a.value == "write" for a in c.args):
            out["phase_write"].append(c.lineno)
        elif isinstance(f, ast.Attribute) and f.attr == "checkpoint" and any(
                k.arg == "to_phase" and isinstance(k.value, ast.Constant) and k.value.value == "write"
                for k in c.keywords):
            out["checkpoint"].append(c.lineno)
        elif isinstance(f, ast.Attribute) and f.attr == "pre_write":
            out["pre_write"].append(c.lineno)
    return out


def c_unattended(src: str, rec, tag: str):
    """C8 (S2 review r1 finding 4): the pure push-side passes — the typed pass above all, tens of seconds on a large
    repository — run BEFORE the background refresh's write checkpoint, while it can still be hard-exited, and the
    checkpoint polls once they are done. `src`: the sync.py text under test (a mutant's in H)."""
    pos = pass_positions(src)
    if pos is None:
        rec.check("C8: sync.py has a function that runs extraction -> push", False)
        return
    lines = [ln for v in pos["passes"].values() for ln in v]
    gates = pos["checkpoint"] + pos["pre_write"] + pos["phase_write"]
    rec.check("C8: each pure push-side pass is called once, BEFORE the write checkpoint, its pre_write veto and the "
              "manual path's 'write' phase (read from sync.py's AST); the checkpoint comes before the push",
              sorted(pos["passes"]) == sorted(PURE_PASSES) and all(len(v) == 1 for v in pos["passes"].values())
              and len(pos["checkpoint"]) == 1 and pos["pre_write"] and pos["phase_write"] and pos["push"]
              and max(lines) < min(gates) and max(pos["checkpoint"]) < min(pos["push"]), pos)
    if _have_typed() is None:
        rec.check("C8: build/typed_calls.py is importable", False)
        return
    import sync_status as ST
    root = write_tree(tmp() / f"c8_{tag}", dict(C7_TREE, **{".deepmap-root": ""}))
    exits, seen = [], {}
    ctl = ST.UnattendedControl(root, trigger="session-start", poll_s=60, hard_exit=lambda rc=0: exits.append(rc))

    def patch(Sm):
        real = Sm._typed_calls_link

        def typed_step(root_, nodes, calls, **kw):
            # a user's sync starts while the typed pass runs: it writes its preempt record and waits for the lock
            seen["phase"] = ctl.phase
            seen["preempt"] = ST.write_preempt(root_, by="sync")
            return real(root_, nodes, calls, **kw)
        Sm._typed_calls_link = typed_step
    try:
        ok, m, err, edges = real_sync(root, dict(OFF, DEEPMAP_TYPED_CALLS="python"), source=src, ctl=ctl,
                                      patch=patch)
    finally:
        if seen.get("preempt"):
            ST.clear_preempt(seen["preempt"])
    last = {}
    try:
        last = json.loads((root / ".deepmap" / "sync_last.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    rec.check("C8: under the background refresh the typed pass runs in a hard-exit phase (nothing written yet: the "
              "watcher may stop the process at once)", seen.get("phase") in ST.HARD_EXIT_PHASES,
              (seen.get("phase"), ST.HARD_EXIT_PHASES))
    rec.check("C8: ... and a user's sync that starts during it is seen at the write checkpoint right after the passes: "
              "nothing pushed, no manifest, outcome preempted (said 'before writing the graph')",
              ok is True and edges is None and not m and last.get("outcome") == "preempted" and not exits
              and "stops here (before writing the graph)" in err and err.count("typed call linking") == 1,
              (ok, edges is None, sorted(m), last.get("outcome"), exits, err[-400:]))


def sec_c():
    print("\n-- C: the typed step's wiring in sync.py --")
    c_wiring(S, LOUD, BUILD / "sync.py")
    c_note(S, LOUD)
    c_real(S, LOUD)
    c_unattended((BUILD / "sync.py").read_text(encoding="utf-8"), LOUD, "loud")


# =====================================================================================================
# D — the manifest's call_linking block, through a real sync() pass
# =====================================================================================================
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


def real_sync(root: Path, env: dict, *, source=None, ctl=None, patch=None):
    """The REAL sync() pass on `root` (ast engine) with the database faked; returns (ok, manifest, stderr, the edges
    pushed — None when nothing was pushed). `source`: the sync.py text to load (a mutant's); `ctl`: a
    sync_status.UnattendedControl (the background refresh: evidence prune); `patch(Sm)`: a last edit of the module."""
    Sm = load_module(BUILD / "sync.py", f"dm_s2_sync_{len(PASSES) + len(FAILS)}_{os.getpid()}_{id(ctl)}", source)
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
    pushed = {}

    def push(driver, nodes, calls, imports, typed_edges=None):
        pushed["edges"] = Sm._resolve_calls(nodes, calls)
        return len(nodes), len(pushed["edges"])
    Sm.push_to_neo4j = push
    if ctl is not None:     # the evidence prune's planning reads the graph: nothing to prune in a faked one
        Sm._plan_prune_evidence = lambda *a, **k: None
    if patch is not None:
        patch(Sm)
    home = tmp() / "d_home"
    full = dict(env, DEEPMAP_EXTRACTOR="ast", DEEPMAP_HOME=str(home), DEEPMAP_AUTO_GITIGNORE="0")
    saved_uri = os.environ.get("NEO4J_URI")
    os.environ["NEO4J_URI"] = "bolt://127.0.0.1:9"
    try:
        with flags(**full), captured_stderr() as err, contextlib.redirect_stdout(io.StringIO()):
            ok = Sm.sync(root, driver=_Drv()) if ctl is None else \
                Sm.sync(root, driver=_Drv(), prune_policy="evidence", unattended=ctl)
    finally:
        if saved_uri is None:
            os.environ.pop("NEO4J_URI", None)
        else:
            os.environ["NEO4J_URI"] = saved_uri
    mpath = root / ".deepmap" / "graph_build.json"
    manifest = json.loads(mpath.read_text(encoding="utf-8")) if mpath.is_file() else {}
    return ok, manifest, err.getvalue(), pushed.get("edges")


def d_manifest(Sm, rec):
    root = tmp() / f"d_manifest_{id(Sm)}"
    root.mkdir(parents=True, exist_ok=True)
    (root / "x.py").write_text("x = 1\n", encoding="utf-8")
    state = {"sha": None, "content_hash": "c" * 16, "dirty": None}
    stats = {"version": 2, "engine": "graphify", "typed_langs": ["python"], "audit_langs": [], "emitted": 3,
             "grades": {"EXTRACTED": 1, "INFERRED": 2, "AMBIGUOUS": 0}, "by_lang": {"python": {"sites": 9}},
             "by_shape": {"R2": {"sites": 2, "emitted": 1}}, "removed": 1, "audit_weak": 0, "notes": [],
             "error": None, "seconds": 1.25}
    try:
        p = Sm.write_graph_build_manifest(root, n_nodes=1, n_edges=1, n_embedded=0, extractor="graphify",
                                         source_state=state, call_linking=stats)
        m = json.loads(p.read_text(encoding="utf-8")) if p else {}
    except TypeError as exc:
        m = {"error": repr(exc)}
    cl = m.get("call_linking")
    rec_ok = isinstance(cl, dict) and cl.get("emitted") == 3 and cl.get("typed_langs") == ["python"] \
        and cl.get("grades", {}).get("INFERRED") == 2 and cl.get("by_shape", {}).get("R2", {}).get("emitted") == 1
    rec.check("D1: write_graph_build_manifest(call_linking=stats) writes the call_linking block (languages, grades, "
          "per-language / per-shape counts)", rec_ok, m)
    p = Sm.write_graph_build_manifest(root, n_nodes=1, n_edges=1, n_embedded=0, extractor="graphify",
                                     source_state=state)
    m = json.loads(p.read_text(encoding="utf-8")) if p else {}
    rec.check("D2: call_linking omitted / None -> no block (the default manifest is unchanged)",
          p is not None and "call_linking" not in m, sorted(m))
    try:
        err_stats = {"version": 2, "engine": "ast", "error": "ValueError: x", "emitted": 7, "removed": 4,
                     "notes": [f"n{i}" for i in range(30)], "seconds": 0.5}
        p = Sm.write_graph_build_manifest(root, n_nodes=1, n_edges=1, n_embedded=0, extractor="ast",
                                         source_state=state, call_linking=err_stats)
        cl = json.loads(p.read_text(encoding="utf-8")).get("call_linking") if p else None
    except TypeError as exc:
        cl = repr(exc)
    rec.check("D2: a failed run (error) -> only version / engine / error / notes / seconds (its counters are the "
          "skeleton's); notes capped at 20 + a count",
          isinstance(cl, dict) and set(cl) == {"version", "engine", "error", "notes", "seconds"}
          and len(cl["notes"]) == 21 and cl["notes"][-1] == "... 10 more", cl)


def sec_d():
    print("\n-- D: the graph-build manifest's call_linking block --")
    d_manifest(S, LOUD)
    if _have_typed() is None:
        check("D3: build/typed_calls.py is importable", False)
        return
    for label, env, want in (("TYPED=python", dict(OFF, DEEPMAP_TYPED_CALLS="python"), True),
                             ("every flag off", dict(OFF), False)):
        root = write_tree(tmp() / f"d_sync_{label.split('=')[0]}", dict(C7_TREE, **{".deepmap-root": ""}))
        ok, m, err, edges = real_sync(root, env)
        cl = m.get("call_linking")
        if want:
            check(f"D3: a real sync() pass ({label}, ast engine, faked database) stamps call_linking in "
                  f"graph_build.json (emitted >= 1) and prints the typed line once",
                  ok is True and isinstance(cl, dict) and cl.get("emitted", 0) >= 1
                  and err.count("[deepmap sync] typed call linking") == 1, (ok, cl, err[-500:]))
            check("D3: ... and the pushed edges carry the typed key (place -> Order.save INFERRED)",
                  any(c == "INFERRED" and a.endswith("place:4") and b.endswith("Order.save:2")
                      for (a, b), c in (edges or {}).items()), edges)
        else:
            check(f"D3: a real sync() pass ({label}) writes no call_linking block and no typed line",
                  ok is True and "call_linking" not in m and "typed call linking" not in err, (ok, sorted(m)))


# =====================================================================================================
# E — the captured graphs of the S3b / S3c fixture trees (both engines)
# =====================================================================================================
def _fixture_trees():
    """{(bucket, lang, tree): (graph, files)} from tests/fixtures/typed_calls/<bucket>/{py,ts} and the TREES of the
    gates that captured them (their tree_sha256 must match: a stale capture is not used)."""
    out, notes = {}, []
    for lang, gate in (("py", "typed_calls_py_gates.py"), ("ts", "typed_calls_ts_gates.py")):
        try:
            mod = load_module(REPO / "tests" / gate, f"dm_s2_trees_{lang}")
            trees = getattr(mod, "TREES")
        except Exception as exc:  # noqa: BLE001
            notes.append(f"{gate}: {exc!r}")
            continue
        for bucket in ("0839", "0851"):
            for name, files in sorted(trees.items()):
                p = FX / bucket / lang / f"{name}.json"
                if not p.is_file():
                    continue
                cap = json.loads(p.read_text(encoding="utf-8"))
                sha = hashlib.sha256(json.dumps(files, sort_keys=True).encode("utf-8")).hexdigest()
                if cap.get("tree_sha256") not in (None, sha):
                    notes.append(f"{bucket}/{lang}/{name}: stale capture")
                    continue
                out[(bucket, lang, name)] = (cap["graph"], files)
    return out, notes


VARIANTS = {"V0": dict(OFF), "VT": dict(OFF, DEEPMAP_TYPED_CALLS=WAVE1, DEEPMAP_CALLS_AUDIT=WAVE1),
            "VP": dict(OFF, DEEPMAP_PRECISE_CALLS="1"),
            "VPT": dict(OFF, DEEPMAP_PRECISE_CALLS="1", DEEPMAP_TYPED_CALLS=WAVE1, DEEPMAP_CALLS_AUDIT=WAVE1),
            # S2 review r1 finding 1: TYPED with DEEPMAP_LOCAL_DEF_CALLS (typed keys skip its re-bind and its judging)
            "VTL": dict(OFF, DEEPMAP_TYPED_CALLS=WAVE1, DEEPMAP_CALLS_AUDIT=WAVE1, DEEPMAP_LOCAL_DEF_CALLS="1")}
TYPED_VARIANTS = ("VT", "VPT", "VTL")
NODE_TEXT = ("key", "label", "name", "source_file", "start_line", "body_summary", "documented", "confidence")


def replay(Sm, graph, root, env, *, hide=False):
    with flags(**env), quiet():
        nodes, calls, imports = GX.map_graph(copy.deepcopy(graph), root=root)
        nodes, calls, imports = Sm._maybe_exclude_vendor(root, *Sm._maybe_exclude_tests(nodes, calls, imports))
        Sm.enrich.apply_sidecar(root, nodes)
        nodes, _typed = Sm._apply_recognizers(root, nodes)
        if hide:
            with patched(Sm, _typed_calls_module=lambda: None):
                return chain(Sm, root, nodes, calls)
        return chain(Sm, root, nodes, calls)


def sec_e():
    print("\n-- E: captured graphs (graphify 0.8.39 + 0.8.51) of the S3b / S3c trees through every push pass --")
    trees, notes = _fixture_trees()
    if notes:
        print(f"  (notes: {notes[:6]})")
    buckets = sorted({b for b, _l, _n in trees})
    check("E: captured graphs of both engines are present (tests/fixtures/typed_calls/{0839,0851}/{py,ts})",
          buckets == ["0839", "0851"] and len(trees) >= 20, (buckets, len(trees)))
    if _have_typed() is None:
        check("E: build/typed_calls.py is importable", False)
        return
    bad_hide, bad_text, bad_grade, bad_extracted, changed = [], [], [], [], {}
    for (bucket, lang, name), (graph, files) in sorted(trees.items()):
        root = write_tree(tmp() / "e" / bucket / lang / name, files)
        runs = {v: replay(S, graph, root, env) for v, env in VARIANTS.items()}
        hidden = replay(S, graph, root, VARIANTS["V0"], hide=True)
        if canonical(*hidden) != canonical(*runs["V0"]):
            bad_hide.append((bucket, lang, name))
        texts = {v: [tuple(n.get(f) for f in NODE_TEXT) for n in r[0]] for v, r in runs.items()}
        if any(texts[v] != texts["V0"] for v in texts):
            bad_text.append((bucket, lang, name))
        for v in TYPED_VARIANTS:
            ns, cs, edges, _c = runs[v]
            file_of = {n["key"]: n["source_file"] for n in ns}
            handed = {(a, str(b)) for a, b in cs if GX.is_call_key(b)}
            for n in ns:
                for t, g in (n.get("call_typed") or {}).items():
                    want = "AMBIGUOUS" if t in (n.get("call_weak") or ()) else g
                    if (n["key"], t) not in handed or edges.get((n["key"], t)) != want:
                        bad_grade.append((bucket, name, v, n["key"], t, g, edges.get((n["key"], t))))
                    if g == "EXTRACTED" and file_of.get(t) != n["source_file"]:
                        bad_extracted.append((bucket, name, v, n["key"], t))
            if v != "VTL" and runs[v][2] != runs["V0" if v == "VT" else "VP"][2]:
                changed.setdefault((bucket, lang), 0)
                changed[(bucket, lang)] += 1
    check("E1: DEEPMAP_TYPED_CALLS=0 / AUDIT=0 == typed_calls hidden: calls, attributes and edges byte for byte "
          f"({len(trees)} captured graphs)", not bad_hide, bad_hide[:5])
    check("E2: node text (key, name, body_summary, line, label, ...) identical across V0 / VT / VP / VPT / VTL",
          not bad_text, bad_text[:5])
    check("E3: every typed key reaches the push marked (CallKey) and its edge carries exactly the call_typed grade "
          "(VT, VPT and VTL = TYPED + DEEPMAP_LOCAL_DEF_CALLS) "
          "(AMBIGUOUS where the audit named it)", not bad_grade, bad_grade[:5])
    check("E4: no cross-file EXTRACTED typed edge (design 4)", not bad_extracted, bad_extracted[:5])
    py_both = all(changed.get((b, "py"), 0) > 0 for b in ("0839", "0851"))
    print(f"  (graphs whose pushed edges TYPED changed, per engine / reader: {dict(sorted(changed.items()))})")
    check("E5: not vacuous — TYPED changes the pushed edges on Python trees of both engines (and TS/JS where the "
          "grammar loads)", py_both, changed)


# =====================================================================================================
# F — sync_edge_tx_bound E0-E3 with typed tuples
# =====================================================================================================
def sec_f():
    print("\n-- F: the budget-split push with typed tuples leaves the identical graph (sync_edge_tx_bound E0-E3) --")
    try:
        tx = load_module(REPO / "tests" / "sync_edge_tx_bound_gates.py", "dm_s2_txbound")
    except Exception as exc:  # noqa: BLE001
        check("F: tests/sync_edge_tx_bound_gates.py loads", False, repr(exc))
        return
    tx.BUILD = BUILD                                  # its _load() then loads THIS tree's sync.py
    fx = tx._fixture()
    nodes = copy.deepcopy(fx[0])
    calls = list(fx[1])
    helper = "base.py::helper:5"
    want = {}
    for i in range(40):
        ck = f"m{i}.py::C{i}.run:2"
        n = next(x for x in nodes if x["key"] == ck)
        if i % 2 == 0:
            n["call_typed"] = {helper: "EXTRACTED"}
            calls.append((ck, CK(helper)))
        if i % 3 == 0:
            n["call_weak"] = [helper]
        want[ck] = "AMBIGUOUS" if i % 3 == 0 else "EXTRACTED" if i % 2 == 0 else "INFERRED"
    typed_fx = (nodes, calls, fx[2], fx[3])
    views = {}
    with flags(**OFF):
        for budget in (10 ** 9, 25, 3, 1):
            g = tx._prior(typed_fx)
            Sm = tx._load()
            Sm._EDGE_TX_MAX_ROWS = budget
            drv = tx._GDrv(g)
            Sm.push_to_neo4j(drv, *typed_fx)
            views[budget] = (dict(g.rels), len(drv.log), list(g.unknown))
    ref = views[10 ** 9][0]
    check("F: the interpreter understood every statement", all(not v[2] for v in views.values()),
          [v[2][:1] for v in views.values()])
    for budget in (25, 3, 1):
        rels, ntx, _u = views[budget]
        check(f"F (E0/E1): budget {budget}: {ntx} transactions, the graph IDENTICAL to one transaction's",
              ntx > 1 and rels == ref, f"diff={set(rels) ^ set(ref)}"[:300])
    got = {}
    for (et, a, b), props in ref.items():
        if et == "CALLS" and b[1] == helper:
            got[a[1]] = props.get("confidence")
    check("F: each typed caller's CALLS edge carries its typed grade, the audited ones AMBIGUOUS, the rest the "
          "name fan-out's INFERRED (40 callers)", got == want, {k: (got.get(k), v) for k, v in want.items()
                                                               if got.get(k) != v})
    # the tx gate's OWN E0-E3 checks, re-run unchanged on the typed fixture (its fixture function swapped)
    buf = io.StringIO()
    p0, f0 = tx._PASS, tx._FAIL
    with flags(**OFF), patched(tx, _fixture=lambda n=40: copy.deepcopy(typed_fx)), contextlib.redirect_stdout(buf):
        for fn in (tx.gate_semantic_equivalence, tx.gate_equivalence_and_bounds, tx.gate_atomic_per_anchor):
            try:
                fn()
            except Exception as exc:  # noqa: BLE001
                tx._FAIL += 1
                print(f"  [FAIL] {fn.__name__} raised {exc!r}")
    ran, failed = (tx._PASS - p0) + (tx._FAIL - f0), tx._FAIL - f0
    check(f"F: sync_edge_tx_bound's own E0-E3 checks ({ran}) all GREEN on the typed fixture (one-transaction graph "
          f"== every budget's; rows within the budget; each anchor's prune + re-create in one transaction)",
          ran >= 12 and failed == 0, [ln for ln in buf.getvalue().splitlines() if "[FAIL]" in ln][:3])


# =====================================================================================================
# G — incremental sync with TYPED on
# =====================================================================================================
def sec_g():
    print("\n-- G: incremental sync with DEEPMAP_TYPED_CALLS on: incremental == full; no CallKey in the cache --")
    if _have_typed() is None:
        check("G: build/typed_calls.py is importable", False)
        return
    files = dict(C7_TREE)
    files["pkg/more.py"] = ("from pkg.models import Invoice\n\n\ndef bill(x):\n    i = Invoice()\n    i.save()\n"
                            "    return x.save()\n\n\nclass Clerk:\n    def work(self):\n        return self.file()\n\n"
                            "    def file(self):\n        return bill(1)\n")
    root = write_tree(tmp() / "g_inc", files)
    env = dict(OFF, DEEPMAP_EXTRACTOR="ast", DEEPMAP_TYPED_CALLS="python", DEEPMAP_CALLS_AUDIT="python")

    def run(incremental):
        with flags(**dict(env, DEEPMAP_INCREMENTAL_SYNC="1" if incremental else "0")), quiet():
            ex = S._rebuild_code(root)
            fresh = not any(("call_typed" in n or "call_weak" in n) for n in ex[0])
            return canonical(*chain(S, root, ex[0], ex[1])), fresh

    full, f0 = run(False)
    cold, f1 = run(True)
    warm, f2 = run(True)
    check("G: incremental (cold, then warm) == full, with TYPED + AUDIT on", full == cold == warm,
          (digest(full), digest(cold), digest(warm)))
    typed_keys = [c for c in json.loads(full)["calls"] if c[2]]
    check("G: ... and typed keys reach the push (not vacuous: TYPED keyed calls here)", len(typed_keys) >= 2,
          typed_keys)
    (root / "pkg" / "service.py").write_text(C7_TREE["pkg/service.py"] + "\n\ndef again():\n    o = Order()\n"
                                             "    return o.save()\n", encoding="utf-8")
    full2, _ = run(False)
    inc2, f3 = run(True)
    check("G: after an edit: incremental == full again (and differs from before)", full2 == inc2 and full2 != full,
          (digest(full2), digest(inc2)))
    check("G: the nodes an extraction hands over never carry a typed attribute from an earlier pass (no leak "
          "through the cache)", f0 and f1 and f2 and f3)
    cache = S.extract_cache.load_cache(root, extractor="ast")
    raw = S.extract_cache.cache_path(root).read_text(encoding="utf-8") if S.extract_cache.cache_path(root).is_file() \
        else ""
    entries = [e for k, e in (cache.get("files") or {}).items() if not k.startswith("\x00")]
    callees = [c[1] for e in entries for c in (e.get("calls") or [])]
    check("G: the extract cache holds plain callee names only (never a CallKey, never call_typed / call_weak)",
          entries and callees and all(type(c) is str and not GX.is_call_key(c) for c in callees)
          and "call_typed" not in raw and "call_weak" not in raw, (len(entries), len(callees)))


# =====================================================================================================
# H — mutation teeth
# =====================================================================================================
MUTANTS = [
    ("the keyed branch needs PRECISE again", "        if _is_call_key(callee) and callee in key_to_file:\n",
     "        if _precise_calls_enabled() and _is_call_key(callee) and callee in key_to_file:\n", "b"),
    ("call_typed's grade ignored",
     "                if tg is not None:\n                    edges[(caller_key, callee)] = tg",
     "                if False:\n                    edges[(caller_key, callee)] = tg", "b"),
    ("call_weak never applied",
     "                if (caller_key, t) in edges:\n                    edges[(caller_key, t)] = \"AMBIGUOUS\"",
     "                if False:\n                    edges[(caller_key, t)] = \"AMBIGUOUS\"", "b"),
    ("call_weak RAISES to INFERRED", "edges[(caller_key, t)] = \"AMBIGUOUS\"", "edges[(caller_key, t)] = \"INFERRED\"",
     "b"),
    ("call_weak adds edges", "                if (caller_key, t) in edges:\n", "                if True:\n", "b"),
    ("a typed key re-bound by LOCAL_DEF_CALLS", "if (bind_of and tg is None) else None", "if bind_of else None", "b"),
    ("typed keys skip the family drop",
     "            if fam_a is not None and fam_b is not None and fam_a != fam_b:\n",
     "            if tg is None and fam_a is not None and fam_b is not None and fam_a != fam_b:\n", "b"),
    ("_attach_local_defs reads typed keys", "            if callee in (n.get(\"call_typed\") or ()):\n"
     "                continue        # S2: a typed key — the T3#6", "            if False:\n"
     "                continue        # S2: a typed key — the T3#6", "bf"),
    ("keyed mode graph-wide again", "(not keyed_for(rel) or _is_call_key(callee))",
     "(not _precise_calls_enabled() or _is_call_key(callee))", "bf"),
    ("_attach_bare_calls judges keys as names without PRECISE",
     "            if (_is_call_key(callee) and callee in by_key) or \".\" in callee or \"::\" in callee:\n",
     "            if (_precise_calls_enabled() and _is_call_key(callee) and callee in by_key) or \".\" in callee "
     "or \"::\" in callee:\n", "bf"),
    ("the typed step never calls link", "        result = tc.link(root, nodes, calls, **kwargs)\n",
     "        result = (calls, {})\n", "c"),
    ("a stale note is used for any nodes", "        return note if note.keys <= live else None\n",
     "        return note\n", "n"),
    ("_drop_extraction_note forgets nothing", "        _EXTRACTION_NOTES.pop(_note_key(root), None)\n",
     "        pass\n", "n"),
    ("a note is kept with the pass off", "        if not _typed_pass_enabled():\n            return None\n"
     "        import weakref\n", "        import weakref\n", "n"),
    ("the manifest drops the call_linking block", "            manifest[\"call_linking\"] = block",
     "            pass", "d"),
    ("the registry holds the note strongly", "        _EXTRACTION_NOTES[key] = weakref.ref(note)\n",
     "        _EXTRACTION_NOTES[key] = (lambda n=note: n)\n", "n"),
    ("the extraction keeps pinning the reader after the pass",
     "        note.source = None        # the extraction no longer pins the reader", "        pass  #", "n"),
    ("sources= gets the bare reader, not the {rel: reader} map",
     "                kwargs[\"sources\"] = {str(n[\"source_file\"]): src for n in nodes\n"
     "                                     if isinstance(n, dict) and n.get(\"source_file\")}",
     "                kwargs[\"sources\"] = src", "w"),
    # S2 review r1 finding 1 (mutant m1): typed keys counted among the caller's callees by LOCAL_DEF_CALLS
    ("_attach_local_def_calls reads typed keys", "            if callee in (n.get(\"call_typed\") or ()):\n"
     "                continue        # S2: a typed key is never re-bound", "            if False:\n"
     "                continue        # S2: a typed key is never re-bound", "l"),
]


def _passes_after_checkpoint(src: str) -> str:
    """S2 review r1 finding 4's mutant: the pure push-side passes moved back after the write checkpoint (right before
    the R10/P3 short_name step, where they were). `src` unchanged when an anchor is missing."""
    start = src.find("        # The push-side call passes below are PURE")
    end = src.find("        if ctl is not None:\n            # Auto-sync")
    anchor = "        # R10/P3 + P2: drop stale short_names"
    if start < 0 or end < start or src.count(anchor) != 1:
        return src
    block = src[start:end]
    i = block.find("        # R10/P1 (DEEPMAP_PRECISE_CALLS only)")
    if i < 0:
        return src
    return (src[:start] + src[end:]).replace(anchor, block[i:] + anchor)


MUTANTS.append(("the pure passes run after the write checkpoint again", _passes_after_checkpoint, None, "u"))


def sec_h():
    print("\n-- H: mutation teeth (each mutant of build/sync.py must turn a check RED) --")
    src = (BUILD / "sync.py").read_text(encoding="utf-8")
    for i, (label, old, new, which) in enumerate(MUTANTS):
        if callable(old):                     # a transform (a move), not one replacement
            msrc = old(src)
            n = 1 if msrc != src else 0
        else:
            n = src.count(old)
            msrc = src.replace(old, new) if n == 1 else src
        if n != 1:
            check(f"H: mutant '{label}' — its anchor is found exactly once in sync.py", False, f"found {n} times")
            continue
        try:
            Sm = load_module(BUILD / "sync.py", f"dm_s2_mutant_{i}", msrc)
        except Exception as exc:  # noqa: BLE001
            check(f"H: mutant '{label}' loads", False, repr(exc))
            continue
        rec = Rec(loud=False)
        try:
            if "b" in which:
                b_cases(Sm, rec)
            if "f" in which:
                b_files_cases(Sm, rec)
            if "l" in which:
                b_local_typed(Sm, rec)
            if "u" in which:
                with quiet():
                    c_unattended(msrc, rec, f"h{i}")
            if "c" in which:
                with quiet():
                    c_wiring(Sm, rec, BUILD / "sync.py")
                    c_real(Sm, rec)
            if "w" in which:
                with quiet():
                    c_wiring(Sm, rec, BUILD / "sync.py")
            if "n" in which:
                with quiet():
                    c_note(Sm, rec)
            if "d" in which:
                with quiet():
                    d_manifest(Sm, rec)
        except Exception as exc:  # noqa: BLE001 — a crash is a caught mutant too
            rec.failed += 1
            rec.labels.append(f"crash: {exc!r}")
        check(f"H: mutant '{label}' is caught ({rec.failed} check(s) RED)", rec.failed > 0, rec.labels[:2])


# =====================================================================================================
def main() -> int:
    if FREEZE is not None:
        try:
            return freeze(FREEZE)
        finally:
            shutil.rmtree(str(_TMP[0]), ignore_errors=True) if _TMP else None
    print(f"typed_calls_push_gates — build: {BUILD}")
    try:
        for sec in (sec_a, sec_b, sec_c, sec_d, sec_e, sec_f, sec_g, sec_h):
            try:
                sec()
            except Exception as exc:  # noqa: BLE001 — a crashing section is a RED section
                check(f"{sec.__name__} ran without crashing", False, f"{exc!r}\n{traceback.format_exc()[-1500:]}")
    finally:
        while _TMP:
            shutil.rmtree(str(_TMP.pop()), ignore_errors=True)   # exactly the temp dir this gate created
    print(f"\ntyped_calls_push_gates: {len(PASSES)} passed, {len(FAILS)} failed, {len(SKIPS)} skipped")
    if FAILS:
        print("FAILED:\n  - " + "\n  - ".join(FAILS[:40]))
        print("typed_calls_push_gates: RED")
        return 1
    print("typed_calls_push_gates: GREEN")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
