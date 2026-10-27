#!/usr/bin/env python3
"""DeepMap sync (C7) — rebuild the code graph and push it to Neo4j.

The pipeline:
    sync(workspace_root, *, cleanup_stale=False) -> bool
    _rebuild_code() -> indexes (created) -> push_to_neo4j() [MERGE nodes; every edge type rebuilt in
    bounded write transactions] -> indexes (ONLINE) -> embeddings

The Neo4j instance is THIS project's, by the one routing rule (port_registry.resolve_neo4j_route): never
a guessed bolt 7687 (see sync() and verify_legacy_owner).

Composes the validated sibling modules build/indexes.py (create_all_indexes/wait_for_indexes)
and build/embeddings.py (generate_embeddings). End-to-end-validated against live Neo4j 5.26.

EXTRACTION (DEEPMAP_EXTRACTOR=auto|graphify|ast, DEFAULT auto): production DeepMap uses the
upstream `graphify` multi-language engine (design mandate). `_rebuild_code` runs
`graphify update <path> --no-cluster` (NO LLM, NO clustering, NO network) and maps graphify's
loosely-typed graph.json to the typed DeepMap schema via build/graphify_extract.py. The default
`auto` prefers graphify (installed bare by both installers) and falls back to the stdlib-`ast`
extractor (`_rebuild_code_ast`, Python-only) only when graphify is absent, fails, or yields nothing —
so multi-language works out of the box while a graph is never empty. The fallback is never silent: its
cause is logged, recorded in the graph-build manifest, and named by the verdict. `ast` forces the
Python-only path (the retriever's original oracle corpus). The push/index/embed stages are
engine-agnostic and final.
"""
from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import indexes          # noqa: E402  (sibling module)
import embeddings       # noqa: E402
import summarize        # noqa: E402  (Phase 3a: richer intent-summaries for undocumented code)
import graphify_extract  # noqa: E402  (Phase 3b: graphify-proper extraction; ast is the fallback)
import extract_cache    # noqa: E402  (opt-in per-file content-hash cache: skip re-parsing unchanged files)
import enrich           # noqa: E402  (opt-in LLM enrichment: extractor-agnostic body_summary override; no-op without a sidecar)
import framework_edges  # noqa: E402  (Track C: extractor-independent framework/runtime edge recognizers)
import language_recognizers  # noqa: E402  (the per-language recognizer registry; Python entry delegates to framework_edges)
import edge_resolve     # noqa: E402  (Track C: name resolution + typed-edge/:IMPORTS persistence)
import sync_status      # noqa: E402  (auto-sync: holder/last records, preemption, build recipe — stdlib only)

# ---- sync concurrency lock (T10) -------------------------------------------
# Two syncs at once (e.g. a post-commit and a post-checkout git hook firing together) would race
# the same MERGE/DELETE. A single-writer **OS-level** lock (flock on POSIX, an msvcrt byte-lock on
# Windows) serialises them: a concurrent run finds the lock held and skips. Using the OS lock —
# not a pid file — means the kernel releases it automatically if the holder crashes, so there is
# no stale-lock, pid-liveness, or steal-race logic to get wrong (those are notoriously unsafe).
# The primitive lives in build/sync_status.py (os_lock — the auto-sync runner's own locks use it too).
_os_lock = sync_status.os_lock


def _acquire_sync_lock(root: Path):
    """Acquire the single-writer sync lock. Returns an OPEN FILE HANDLE holding the OS lock, or
    None if another holder has it: another sync, or `deepmap enrich`, which takes this same lock while
    it extracts (build/enrich.py _take_sync_lock). Release by passing the handle to _release_sync_lock."""
    lockdir = root / ".deepmap"
    lockdir.mkdir(parents=True, exist_ok=True)
    handle = open(lockdir / "sync.lock", "a+")
    if not _os_lock(handle):
        handle.close()
        return None
    try:   # best-effort, informational metadata only (no one reads it for correctness now)
        handle.seek(0)
        handle.truncate()   # 'a+' writes at EOF; truncate first so the file doesn't grow each acquire
        handle.write(json.dumps({"pid": os.getpid(), "ts": time.time()}) + " \n")
        handle.flush()
    except OSError:
        pass
    return handle


def _release_sync_lock(handle) -> None:
    """Release the OS lock and close the handle. Safe to call with None."""
    if handle is None:
        return
    try:
        if os.name == "nt":
            import msvcrt
            try:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        else:
            import fcntl
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
    finally:
        try:
            handle.close()
        except OSError:
            pass


# ---- cross-instance write lock ---------------------------------------------
# The per-root sync.lock above only serialises SAME-project syncs. Different projects have different
# .deepmap/sync.lock files, so two of them could race the ONE shared Neo4j (the switch-safety guard's
# read->wipe->push is not atomic on its own). A second OS lock, keyed on the Neo4j instance (NEO4J_URI)
# and held under DEEPMAP_HOME (~/.deepmap), serialises ALL writers to that instance: the loser skips.
def _bolt_uri(port) -> str:
    """THE bolt URI DeepMap generates for a local port (ROUND-8 J4): port_registry.bolt_uri — the
    instances bind 127.0.0.1 only, and `localhost` resolves to ::1 first on Windows (a refused IPv6
    connect, ~2 s, on every new connection). The literal is the fallback only when port_registry itself
    cannot be imported."""
    try:
        import port_registry
        return port_registry.bolt_uri(port)
    except ImportError:
        return f"bolt://127.0.0.1:{int(port)}"


def _git_longpaths() -> list:
    """`-c core.longpaths=true` on Windows, [] elsewhere — the prefix of every git probe here (ROUND-8 J6):
    without it Git for Windows cannot lstat a committed file past MAX_PATH (260), so `status` reported it
    as an uncommitted change forever. port_registry.git_longpaths_args, with the same rule as fallback."""
    try:
        import port_registry
        return list(port_registry.git_longpaths_args())
    except Exception:
        return ["-c", "core.longpaths=true"] if os.name == "nt" else []


def _git_env() -> dict:
    """The environment of every git subprocess sync starts: this process's, with GIT_OPTIONAL_LOCKS=0 (auto-sync
    H5) — `git status` otherwise takes .git/index.lock to refresh the index, and a sync running in the background
    (DeepMap's own refresh, the git hook) made the user's own `git commit` / `git add` / rebase step fail with
    "Unable to create index.lock". Read-only probes never need that lock."""
    return dict(os.environ, GIT_OPTIONAL_LOCKS="0")


_LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1", "0:0:0:0:0:0:0:1")


def _instance_lock_key(uri) -> str:
    """The string an instance lock is keyed on. ONE key per endpoint whatever its loopback spelling
    (ROUND-8 J4): this build dials bolt://127.0.0.1:<port>, builds <= v0.27.x dialled (and hashed)
    bolt://localhost:<port>, and a sync of each version — a git post-commit hook baked with an older
    plugin path runs the older code — must still exclude the other. So a LOOPBACK URI is keyed in the
    spelling those builds hashed (`<scheme>://localhost:<port>`, the default port made explicit); any
    other URI (an operator's remote NEO4J_URI) is keyed verbatim, exactly as before. Never raises."""
    s = str(uri)
    try:
        from urllib.parse import urlsplit
        u = urlsplit(s.strip())
        if (u.hostname or "").lower() not in _LOOPBACK_HOSTS:
            return s
        path = u.path if u.path not in ("", "/") else ""
        return f"{(u.scheme or 'bolt').lower()}://localhost:{u.port or 7687}{path}"
    except (ValueError, TypeError):
        return s


def _instance_lock_path(uri: str | None = None) -> Path:
    """Path of the per-Neo4j-instance sync lock (one per endpoint — see _instance_lock_key), under
    DEEPMAP_HOME (~/.deepmap). With per-project instances each project has a DISTINCT bolt URI, so this
    lock naturally becomes per-project (its old cross-project serialization only ever mattered for the
    shared instance)."""
    import hashlib
    home = Path(os.environ.get("DEEPMAP_HOME", Path.home() / ".deepmap"))
    uri = uri or os.environ.get("NEO4J_URI") or _bolt_uri(7687)
    digest = hashlib.sha1(_instance_lock_key(uri).encode("utf-8")).hexdigest()[:16]
    return home / f"instance-{digest}.lock"


def _acquire_instance_lock(uri: str | None = None):
    """Acquire the single-writer lock for THIS Neo4j instance. Returns an OPEN FILE HANDLE holding the
    OS lock, or None if another sync (any project on the SAME instance) already holds it. Release via
    _release_sync_lock."""
    path = _instance_lock_path(uri)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+")
    if not _os_lock(handle):
        handle.close()
        return None
    return handle


# ---- _rebuild_code: extract a typed DeepMap-schema graph --------------------
def _summarize(node, name, kind):
    # docstring first line if present, else a structure-derived intent summary (build/summarize.py)
    return summarize.summarize(node, name, kind)


# Route classification: match a decorator's trailing IDENTIFIER against these exact HTTP-router
# method names — NOT a substring of ast.dump (which flips @x.deleter -> 'delete', @target_getter
# -> 'get', @cache_forget -> 'get'). Set kept identical to the original to minimize graph change.
_HTTP_DECOS = {"route", "get", "post", "put", "delete", "endpoint"}


def _deco_name(d):
    """The trailing identifier of a decorator expression: @app.get(...) -> 'get', @route -> 'route',
    @x.deleter -> 'deleter'. Used to classify Route by exact method NAME, not a substring."""
    t = d.func if isinstance(d, ast.Call) else d
    return getattr(t, "attr", None) or getattr(t, "id", None)


# ---- @overload stubs (round 7, T4#4 / T4d-F7) ------------------------------------------------------
# typing.overload stubs (`@overload def f(x: int) -> int: ...`) are declarations, not code: the ast engine
# emitted one node per stub, so click's Context.invoke became THREE nodes (@846, @851, @853) and every call
# to it an AMBIGUOUS fan-out onto bodies that do nothing. A stub-bodied def decorated @overload /
# @typing.overload / @t.overload that is FOLLOWED in the same scope by a same-named implementation is
# skipped; the implementation keeps its own line and key. A lone stub (no implementation after it — a
# protocol, a stub-only module) and an @overload def with a REAL body (a dispatch library's overload, which
# IS code) stay nodes. No STAGE-0 corpus file uses @overload.
_DEF_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef)


def _is_overload_decorated(fn) -> bool:
    return any((_deco_name(d) or "") == "overload" for d in fn.decorator_list)


def _is_stub_body(body) -> bool:
    """True iff a def body declares nothing but a stub: `...`, `pass`, a docstring, or a bare raise."""
    for st in body:
        if isinstance(st, (ast.Pass, ast.Raise)):
            continue
        if isinstance(st, ast.Expr) and isinstance(st.value, ast.Constant) and (
                st.value.value is Ellipsis or isinstance(st.value.value, str)):
            continue
        return False
    return True


def _overload_stub_ids(tree) -> set:
    """id() of every @overload stub in `tree` that a same-named implementation follows in the same body."""
    stubs: set = set()
    for parent in ast.walk(tree):
        for field in ("body", "orelse", "finalbody"):
            stmts = getattr(parent, field, None)
            if not isinstance(stmts, list):
                continue
            for i, st in enumerate(stmts):
                if not (isinstance(st, _DEF_TYPES) and _is_overload_decorated(st) and _is_stub_body(st.body)):
                    continue
                if any(isinstance(later, _DEF_TYPES) and later.name == st.name
                       and not _is_overload_decorated(later) for later in stmts[i + 1:]):
                    stubs.add(id(st))
    return stubs


# ---- receiver kinds (round 7, T3#5) ------------------------------------------------------------------
# A call `x.m()` used to be recorded as plain `m`, so name resolution bound `re.search(...)` to the project's
# DeepMapRetriever.search and `subprocess.run(...)` to the project's run() — INFERRED, the grade that reads
# "trust this". The (caller, callee) tuples stay exactly as they were (enrich, the incremental cache and the
# graphify path read them), and each Function/Method/Route node records, per callee, HOW it was reached, as
# private metadata `call_recv` {callee: sorted kinds} that _resolve_calls reads (it is never pushed):
#   "bare"          m()                       a module/closure/builtin name
#   "self"          self.m() / cls.m()
#   "mod:<dotted>"  re.m(), os.path.m(), Tool.m() — the receiver is a name this FILE imports (import X /
#                   import X as Y / from P import X; a leading '.' = a relative, i.e. project, import)
#   "name:<N>"      N.m() — any other bare name (a local, a parameter, a module-level variable, a class)
#   "expr"          anything else: self.x.m(), f().m(), super().m(), a[0].m()
# A name the function assigns or binds locally (a parameter, `re = ...`, for/with/except targets) is never
# taken for the module it shadows.
def _import_aliases(tree, nodes=None) -> dict:
    """{bound name: dotted import target} for every import in `tree` (or in `nodes`, an iterable of its ast
    nodes — R11/R2's module-level ones); a name bound to two different targets maps to None (unknown).
    `import a.b` binds `a` -> 'a'; `from . import x` binds x -> '.x'."""
    out: dict = {}

    def bind(name, target):
        out[name] = target if out.get(name, target) == target else None

    for node in (ast.walk(tree) if nodes is None else nodes):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.asname:
                    bind(a.asname, a.name)
                else:
                    top = a.name.split(".")[0]
                    bind(top, top)
        elif isinstance(node, ast.ImportFrom):
            mod = "." * int(node.level or 0) + (node.module or "")
            for a in node.names:
                if a.name == "*":
                    continue
                target = (mod + a.name) if (not mod or mod.endswith(".")) else f"{mod}.{a.name}"
                bind(a.asname or a.name, target)
    return out


def _locally_bound(fn) -> set:
    """Names a function (or anything nested in it) binds itself: parameters and Store targets."""
    names: set = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
    return names


def _dotted(expr):
    """'a.b.c' for a Name/Attribute chain rooted at a Name, else None."""
    parts = []
    while isinstance(expr, ast.Attribute):
        parts.append(expr.attr)
        expr = expr.value
    if not isinstance(expr, ast.Name):
        return None
    parts.append(expr.id)
    return ".".join(reversed(parts))


def _receiver_kind(func, aliases: dict, local: set) -> str:
    """How the callee of `func` (an ast.Call's .func) is reached — see the table above."""
    if isinstance(func, ast.Name):
        return "bare"
    recv = func.value
    chain = _dotted(recv)
    if chain is None:
        return "expr"
    root, _, rest = chain.partition(".")
    if root in ("self", "cls"):
        return "self" if not rest else "expr"
    if root not in local and aliases.get(root):
        return "mod:" + aliases[root] + (f".{rest}" if rest else "")
    return f"name:{root}" if not rest else "expr"


class _Extractor(ast.NodeVisitor):
    def __init__(self, rel):
        self.rel = rel
        self.nodes, self.calls, self.imports = [], [], []
        self.class_stack = []
        self._stubs: set = set()      # @overload stubs to skip (filled by visit_Module)
        self._aliases: dict = {}      # the file's import bindings (filled by visit_Module)

    def visit_Module(self, node):
        self._stubs = _overload_stub_ids(node)
        self._aliases = _import_aliases(node)
        self.generic_visit(node)

    def _key(self, name, line):
        return f"{self.rel}::{name}:{line}"

    def visit_ClassDef(self, node):
        self.nodes.append(dict(key=self._key(node.name, node.lineno), label="Class",
                               name=node.name, source_file=self.rel, start_line=node.lineno,
                               documented=bool(ast.get_docstring(node)),
                               body_summary=_summarize(node, node.name, "class")))
        self.class_stack.append(node.name)
        self.generic_visit(node)
        self.class_stack.pop()

    def _func(self, node):
        if id(node) in self._stubs:
            return                    # an @overload stub its implementation follows: not a node (T4#4)
        kind = "Method" if self.class_stack else "Function"
        name = node.name if not self.class_stack else f"{self.class_stack[-1]}.{node.name}"
        if any((_deco_name(d) or "").lower() in _HTTP_DECOS for d in node.decorator_list):
            kind = "Route"
        key = self._key(name, node.lineno)
        entry = dict(key=key, label=kind, name=name, source_file=self.rel,
                     start_line=node.lineno, documented=bool(ast.get_docstring(node)),
                     body_summary=_summarize(node, name, kind.lower()))
        self.nodes.append(entry)
        local = _locally_bound(node)
        recv: dict = {}
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                f = child.func
                callee = getattr(f, "id", None) or getattr(f, "attr", None)
                if callee:
                    self.calls.append((key, callee))
                    recv.setdefault(callee, set()).add(_receiver_kind(f, self._aliases, local))
        if recv:                      # private resolution metadata (never pushed): see _receiver_kind
            entry["call_recv"] = {c: sorted(k) for c, k in recv.items()}
        self.generic_visit(node)

    visit_FunctionDef = _func
    visit_AsyncFunctionDef = _func

    def visit_Import(self, node):
        for a in node.names:
            self.imports.append((self.rel, a.name))

    def visit_ImportFrom(self, node):
        if node.module:
            self.imports.append((self.rel, node.module))


class _Extraction(tuple):
    """What _rebuild_code returns: unpacks exactly like the plain (nodes, calls, imports, engine) tuple
    every caller and test stub uses, plus `.info` — how the engine was chosen (G4):
    {"requested", "resolved", "fallback_reason", "engine_error", "failed_files", "failed_examples",
    "maxpath", "rescued_dirs", "rescue_dropped", "graphify_out", "self_call_rebind",
    "self_calls_rebound", "graphify_version", "rescue_blocked", "resolved_imports", "imports_resolved",
    "imports_by_name", "calls_filtered", "imports_bare_resolved", "supplemental_nodes",
    "overloads_moved", "file_ids_split", "calls_plus", "timed_out", "timeout_s"} (keys absent when empty —
    "calls_filtered" .. "calls_plus" are the round-7 engine-output corrections, see
    graphify_extract._ROUND7_COUNTERS; `timed_out` + `timeout_s` (round 11) when graphify was stopped by its
    time budget, so the downgrade refusal names the budget, not a broken install; `engine_error` only for a
    failed forced-graphify run,
    where there is no fallback; `rescue_blocked` = the engine-skipped dirs NOT re-included because the
    installed graphify is outside graphify_extract.SUPPORTED_GRAPHIFY_SPEC; the three import keys only
    when the graphify graph is the one kept — `imports_resolved` imports bind by the engine-resolved node
    key, `imports_by_name` by name). sync() hands it to the verdict and the manifest, so an automatic
    auto->ast fallback is named with its cause instead of passing as a READY (or "forced") ast run.

    `.typed_note` (slice S2; None unless the typed-call pass will run) — what this extraction hands that pass (the
    resolved engine, the shared file reader): see _TypedNote. Never part of `.info`."""

    def __new__(cls, nodes, calls, imports, engine, info, typed_note=None):
        self = super().__new__(cls, (nodes, calls, imports, engine))
        self.info = info
        self.typed_note = typed_note
        return self


def _rebuild_code(workspace_root: Path):
    """Extract a typed DeepMap-schema graph. Returns (nodes, calls, imports, engine) where `engine`
    is the RESOLVED extractor actually used ('graphify' or 'ast') — as an `_Extraction`, whose `.info`
    records the requested mode, any fallback reason and graphify's per-file failures. Engine via
    DEEPMAP_EXTRACTOR:

      auto (DEFAULT) — prefer the multi-language graphify engine; fall back to the stdlib-ast
                       extractor when graphify is absent or yields nothing. This is the default so
                       multi-language works out of the box while never producing an empty graph.
      graphify       — force the upstream graphify engine (build/graphify_extract.py); NO ast
                       fallback (an empty extraction stays empty — for callers that must know
                       graphify ran).
      ast            — force the stdlib-ast extractor (Python-only; the path the retriever's
                       original no-regression oracle was captured on).

    Default is `auto` (graphify-preferred): graphify's Python retrieval is validated non-regressing
    vs ast on the STAGE-0 corpus (local ast-vs-graphify comparison + a frozen graphify oracle,
    tests/oracles/self-graphify-build.json), and it is the only path that extracts non-Python
    languages. graphify is installed by both installers (bare, no LLM extras) so `auto` resolves to
    graphify on a normal install; where graphify is unavailable, ast keeps Python working. The
    G3 Opus benchmark remains the gate for any PUBLISHED quality number, not for the default engine.
    """
    mode = os.environ.get("DEEPMAP_EXTRACTOR", "auto").strip().lower() or "auto"
    info = {"requested": mode, "resolved": None, "fallback_reason": None}
    if mode in ("auto", "graphify"):
        # S2: while the typed-call pass will run, the extraction reads the files through a reader that pass then
        # reuses (_typed_extraction_source; None otherwise — the call is then exactly as before)
        shared = _typed_extraction_source(workspace_root)
        res, report = (graphify_extract.extract_with_report(workspace_root) if shared is None
                       else graphify_extract.extract_with_report(workspace_root, source=shared))
        report = report if isinstance(report, dict) else {}
        info.update({k: report.get(k) for k in ("failed_files", "failed_examples", "maxpath",
                                                "rescued_dirs", "rescue_dropped", "graphify_out",
                                                "self_calls_rebound", "graphify_version",
                                                "rescue_blocked",
                                                # round 7: the engine-output corrections (counts > 0 only)
                                                "calls_filtered", "imports_bare_resolved",
                                                "supplemental_nodes", "overloads_moved", "file_ids_split",
                                                "calls_plus",
                                                # round 11 (M1): the engine ran out of its time budget
                                                "timed_out", "timeout_s") if report.get(k)})
        if "self_call_rebind" in report:            # G5: whether the self-call correction ran (opt-out)
            info["self_call_rebind"] = bool(report["self_call_rebind"])
        if res is not None:
            # before the empty check: an engine result that is ALL metadata is an empty one (auto falls back)
            res = _drop_macos_metadata(workspace_root, *res)
        if res is not None and res[0]:
            nodes, calls, imports = _maybe_exclude_vendor(       # vendor default-on; tests opt-in
                workspace_root, *_maybe_exclude_tests(*res))
            if report.get("resolved_imports"):
                # The resolved-import-key contract: a key-form target always names a node of THIS
                # graph — an import into a node the vendor/test filters just removed goes with it.
                imports = graphify_extract.drop_dangling_import_keys(imports, res[0], nodes)
                info["resolved_imports"] = True
                info["imports_resolved"], info["imports_by_name"] = graphify_extract.import_key_counts(
                    imports, nodes)
            elif "resolved_imports" in report:
                info.update(resolved_imports=False, imports_resolved=0, imports_by_name=len(imports))
            print(f"[deepmap sync] extractor: graphify ({len(nodes)} nodes, {len(calls)} calls)")
            _note_engine_nodes(workspace_root, nodes)
            note = _note_extraction(workspace_root, "graphify", nodes, shared)   # S2: for _typed_calls_link
            return _Extraction(nodes, calls, imports, "graphify", dict(info, resolved="graphify"), typed_note=note)
        cause = report.get("cause") or "graphify produced no code nodes"
        if mode == "graphify":
            print(f"[deepmap sync] graphify extractor produced no nodes (mode=graphify; no ast "
                  f"fallback): {cause}", file=sys.stderr)
            # No fallback happened, so this is not a fallback_reason — but the verdict (whose reasons are
            # persisted) must still name what went wrong instead of a bare "0 code nodes".
            _note_engine_nodes(workspace_root, [])
            _note_extraction(workspace_root, "graphify", [])     # forgets an older note of this root
            return _Extraction([], [], [], "graphify", dict(info, resolved="graphify", engine_error=cause))
        # Say WHY, and that the result is Python-only: this used to read "graphify unavailable/empty"
        # while graphify was installed and had crashed, and the verdict then blamed a "forced" ast.
        info["fallback_reason"] = cause
        for k in ("self_call_rebind", "self_calls_rebound", "calls_filtered", "imports_bare_resolved",
                  "supplemental_nodes", "overloads_moved", "file_ids_split", "calls_plus"):
            info.pop(k, None)                                  # graphify's graph is not the one kept
        print(f"[deepmap sync] graphify failed ({cause}) — falling back automatically to the "
              f"Python-only ast extractor; non-Python source will be missing from the graph",
              file=sys.stderr)
    # Incremental sync is an ast-path-only optimization (DEEPMAP_INCREMENTAL_SYNC): the graphify
    # engine is one whole-tree subprocess we cannot decompose per-file, so it always runs a full
    # extraction. The incremental variant returns a tuple IDENTICAL to the full walk — it just skips
    # re-parsing unchanged files — so `_maybe_exclude_tests` and everything downstream is unchanged.
    ast_extract = _rebuild_code_ast_incremental if _incremental_enabled() else _rebuild_code_ast
    nodes, calls, imports = _maybe_exclude_vendor(
        workspace_root, *_maybe_exclude_tests(*_drop_macos_metadata(workspace_root, *ast_extract(workspace_root))))
    _note_engine_nodes(workspace_root, nodes)
    note = _note_extraction(workspace_root, "ast", nodes)   # S2: the ast attribution for _typed_calls_link
    return _Extraction(nodes, calls, imports, "ast", dict(info, resolved="ast"), typed_note=note)


def _note_engine_nodes(root, nodes) -> None:
    """Hand the verdict the exact set of nodes the extraction ENGINE produced (before the per-language
    recognizers merge their supplemental :Class/:Method shells in), so coverage counts engine output
    only (round 7, T8d-B1 — verdict.note_engine_nodes). Best-effort; never affects extraction."""
    try:
        import verdict as _verdict
        _verdict.note_engine_nodes(root, nodes)
    except Exception:  # noqa: BLE001
        pass


# ---- long paths (round 7, T10#14 / T10d-8) -----------------------------------------------------------
# Windows without LongPathsEnabled cannot list or open a path of 260+ characters through the normal API,
# and pathlib's rglob skipped such a directory SILENTLY: its files were missing from the graph while the
# verdict said READY (a 358-char path under a 150-char root). DeepMap's own walker now goes through the
# extended-length form (\\?\C:\... / \\?\UNC\server\share\...), which the Win32 API reads at any length,
# hands out that form only for a file that needs it (so every other path, key and message is unchanged),
# and reports — once — any directory it still could not list. (graphify's own walker is upstream; the
# verdict census flags what it drops.)
_WIN_MAX_PATH = 260           # a file path of this many characters or more needs the extended form


def _extended_path(path: str) -> str:
    """The Windows extended-length form of an ABSOLUTE path (unchanged when it already is one)."""
    if path.startswith("\\\\?\\"):
        return path
    if path.startswith("\\\\"):
        return "\\\\?\\UNC\\" + path[2:]
    return "\\\\?\\" + path


def _walk_files(root: Path, unreadable: list):
    """Yield (rel_parts, filename, os_path) for every file under `root` — os.walk through the
    extended-length form on Windows, so a deep directory is listed instead of skipped; a directory that
    still can't be listed is appended to `unreadable` (os.walk's onerror) instead of vanishing."""
    base = os.path.abspath(str(root))
    top = _extended_path(base) if os.name == "nt" else base
    for dirpath, _dirnames, filenames in os.walk(top, onerror=unreadable.append):
        sub = dirpath[len(top):]
        parts = [p for p in re.split(r"[\\/]+", sub) if p]
        for fn in filenames:
            yield parts, fn, os.path.join(dirpath, fn)


def _report_unreadable(root, unreadable: list, what: str) -> None:
    if not unreadable:
        return
    first = unreadable[0]
    where = str(getattr(first, "filename", "") or "").replace("\\\\?\\", "")
    print(f"[deepmap sync] could not list {len(unreadable)} director{'y' if len(unreadable) == 1 else 'ies'} "
          f"under {root} (first: {where} — {type(first).__name__}: {getattr(first, 'strerror', '') or first}); "
          f"the {what} under {'it' if len(unreadable) == 1 else 'them'} are not in the graph", file=sys.stderr)


def _iter_ast_files(workspace_root: Path):
    """Yield (py_path, rel) for every extractable .py file, in the SAME sorted order the full walk
    uses. Shared by the full extractor AND the incremental cache so both iterate an identical file
    set in an identical order — the property that makes an incrementally-reconstructed graph
    byte-identical to a full rebuild. `rel` is OS-portable (`as_posix()`).

    Long-path safe (round 7): the walk lists directories past Windows' MAX_PATH, a file whose path is
    260+ characters is yielded in its extended-length form (readable; `rel` and the node keys stay the
    normal relative path), and a directory that cannot be listed is reported once on stderr.

    macOS metadata is never yielded (framework_edges.is_macos_metadata): an AppleDouble `._x.py` twin
    (binary, NUL bytes — it was a "skipped ._x.py" line per file) and anything under a `__MACOSX/` or a
    Netatalk `.AppleDouble/` directory, judged on the path below the root."""
    root = Path(workspace_root)
    unreadable: list = []
    found = []
    for parts, fn, os_path in _walk_files(root, unreadable):
        # A literal-extension match is case-sensitive on POSIX, so `APP.PY` / `Setup.Py` were unextracted
        # there (not on Windows) — compare the lowercased suffix.
        if Path(fn).suffix.lower() == ".py" and not framework_edges.is_macos_metadata("/".join((*parts, fn))):
            found.append((root.joinpath(*parts, fn), os_path))
    base = os.path.abspath(str(root))
    for py, os_path in sorted(found, key=lambda t: t[0]):   # the order the old sorted(rglob) walk had
        # Match path COMPONENTS, not a substring of the whole path, so a legitimately-named file
        # like `my.venv-tools/x.py` or `.venv-docs/y.py` is not silently dropped.
        if any(part in {"__pycache__", ".venv"} for part in py.parts):
            continue
        # Normalize the path separator so node keys + source_file are OS-portable: a graph built
        # on Windows ('\\') and one on POSIX ('/') must yield identical keys (audit #2). Use
        # Path.as_posix() (not str.replace) — it converts the OS separator WITHOUT mangling a
        # literal backslash that is a valid character in a POSIX filename. key + source_file
        # both derive from `rel`.
        try:
            rel = py.relative_to(root).as_posix()
        except ValueError:
            rel = py.as_posix()
        if os.name == "nt" and len(os.path.join(base, *rel.split("/"))) >= _WIN_MAX_PATH:
            py = Path(os_path)        # the extended-length form: the only one Windows can open
        yield py, rel
    _report_unreadable(root, unreadable, "Python files")


def _extract_ast_file(py: Path, rel: str):
    """Extract ONE Python file -> (file_nodes, file_calls, file_imports). A pure function of (rel,
    file content): the caller-visible unit that BOTH the full walk and the incremental cache use, so
    a cache MISS re-extracts byte-identically to a full rebuild. On any parse/visit/summarize error
    the file is SKIPPED (logged to stderr) and ([], [], []) is returned — the exact (empty)
    contribution the full walk makes for a bad file.

    Per-file isolation must cover the WHOLE file body, not just ast.parse: a POST-PARSE error in the
    visitor/summarizer (e.g. a RecursionError on pathological nesting that survived parsing) would
    otherwise propagate and abort extraction of every remaining file. Build the file's nodes into
    LOCALS and only return them if the whole file succeeds — a failure skips exactly the one problem
    file, ATOMICALLY (no half-added module node), matching the parse-error behavior."""
    try:
        # utf-8-sig strips a leading BOM (written by many Windows editors) — without it,
        # ast.parse raises on the U+FEFF and the whole (valid, runnable) module is dropped.
        tree = ast.parse(py.read_text(encoding="utf-8-sig", errors="replace"))
        file_nodes = [dict(key=f"{rel}::module", label="Module", name=py.stem,
                           source_file=rel, start_line=1,
                           documented=bool(ast.get_docstring(tree)),
                           body_summary=summarize.summarize(tree, py.stem, "Module"))]
        ex = _Extractor(rel)
        ex.visit(tree)
        file_nodes.extend(ex.nodes)
        return file_nodes, list(ex.calls), list(ex.imports)
    except Exception as e:
        print(f"[deepmap sync] skipped {rel} ({type(e).__name__})", file=sys.stderr)
        return [], [], []


def _rebuild_code_ast(workspace_root: Path):
    """Full stdlib-ast extraction: walk every .py file and concatenate its per-file extraction."""
    nodes, calls, imports = [], [], []
    for py, rel in _iter_ast_files(workspace_root):
        fn, fc, fi = _extract_ast_file(py, rel)
        nodes.extend(fn)
        calls.extend(fc)
        imports.extend(fi)
    return nodes, calls, imports


# The version of the per-file ast extraction (_extract_ast_file's output), stored as a reserved entry of
# the incremental cache: bump it whenever that output changes, so an entry an older build wrote from the
# SAME file bytes is not reused (the round-7 extraction skips @overload stubs and records receiver kinds).
# The entry's key holds a NUL, which no file's relative path can, and its string `hash` passes
# extract_cache.load_cache's sanity filter; the cache keeps the plain extractor="ast" tag.
_AST_EXTRACT_SEMANTICS = "2026-09-r7"
_AST_SEMANTICS_KEY = "\x00deepmap:ast-extract-semantics"


def _incremental_enabled() -> bool:
    """True iff opt-in incremental sync is on (DEEPMAP_INCREMENTAL_SYNC). OFF by default — the full
    rebuild is the safe default/fallback until incremental is proven and the flag is flipped."""
    return os.environ.get("DEEPMAP_INCREMENTAL_SYNC", "").strip().lower() in {"1", "true", "yes", "on"}


def _rebuild_code_ast_incremental(workspace_root: Path):
    """Incremental stdlib-ast extraction (behind DEEPMAP_INCREMENTAL_SYNC). Reuse the cached per-file
    extraction for files whose content hash is unchanged; re-extract ADDED/CHANGED files; DELETED/
    MOVED-away files simply fall out of the walk. Returns a (nodes, calls, imports) tuple IDENTICAL
    to `_rebuild_code_ast` for the same tree — the only difference is that unchanged files skip the
    ast.parse. See build/extract_cache.py for the correctness invariant.

    A cold/stale/corrupt cache only causes extra MISSES (never a wrong result), so this is always a
    safe substitute for the full walk on the ast path. A cache written by a DIFFERENT version of the
    per-file extraction (_AST_EXTRACT_SEMANTICS) is treated as cold: a content-hash hit only proves the
    file is unchanged, not that the extractor is."""
    root = Path(workspace_root)
    prev = extract_cache.load_cache(root, extractor="ast")
    prev_files = prev.get("files", {})
    if (prev_files.get(_AST_SEMANTICS_KEY) or {}).get("hash") != _AST_EXTRACT_SEMANTICS:
        prev_files = {}                     # entries from another extractor version: re-extract once
    files = []
    for py, rel in _iter_ast_files(root):
        try:
            data = py.read_bytes()          # raw bytes for the content hash (cheap vs ast.parse)
        except OSError:
            data = None                     # unreadable now -> forced miss + (empty) re-extract
        files.append((py, rel, data))
    nodes, calls, imports, new_files, stats = extract_cache.incremental_extract(
        files, prev_files, _extract_ast_file)
    new_files[_AST_SEMANTICS_KEY] = {"hash": _AST_EXTRACT_SEMANTICS}
    extract_cache.save_cache(root, extractor="ast", files=new_files)
    print(f"[deepmap sync] incremental extract: {stats['hits']}/{stats['total']} files reused from "
          f"cache, {stats['misses']} (re)parsed", file=sys.stderr)
    return nodes, calls, imports


# ---- opt-in test-directory exclusion (DEEPMAP_EXCLUDE_TESTS; default OFF) ----
# A cross-language mitigation for "search returns tests, not lib" pollution on big repos (e.g. the
# CommonJS express G0 finding). It is EXTRACTOR-AGNOSTIC (applied at the _rebuild_code convergence
# point, so it covers both graphify and the ast fallback) and, when OFF, a strict no-op that returns
# the SAME objects — so a default build (incl. the Python STAGE-0 oracle) is byte-identical and the
# frozen retriever is untouched. This is an extraction-SCOPE knob only; ranking is unchanged.
_TEST_DIR_PARTS = {"test", "tests", "__tests__", "spec", "specs", "testing", "e2e", "__mocks__"}


def _exclude_tests_enabled() -> bool:
    return os.environ.get("DEEPMAP_EXCLUDE_TESTS", "").strip().lower() in {"1", "true", "yes", "on"}


def _is_test_path(source_file: str) -> bool:
    """True iff `source_file` is test code — matched by DIRECTORY COMPONENT (never a substring, so
    `src/contest.py` / `lib/latest.js` / `pkg/attest.rb` are NOT tests) or by filename convention:
    test_*.py, *_test.{py,go,rs}, *.test.{ts,tsx,js,jsx,mjs,cjs}, *.spec.{...}, *_spec.rb,
    *Test.java / *Tests.java."""
    raw = str(source_file or "").replace("\\", "/")
    if not raw:
        return False
    parts = raw.split("/")
    if any(p.lower() in _TEST_DIR_PARTS for p in parts[:-1]):   # a DIRECTORY component is a test dir
        return True
    name = parts[-1]
    lname = name.lower()
    if lname.startswith("test_") and lname.endswith(".py"):
        return True
    if lname.endswith(("_test.py", "_test.go", "_test.rs", "_spec.rb")):
        return True
    for e in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"):
        if lname.endswith(".test" + e) or lname.endswith(".spec" + e):
            return True
    # DELIBERATELY case-SENSITIVE, unlike every rule above (which uses `lname`). This is the one rule
    # matching a BARE `Test`/`Tests` with no separator anchor, so the capital T IS the discriminator:
    # lowercasing it would match Latest.java / Contest.java / Protest.java / Attest.java /
    # Greatest.java and silently DROP those production classes from the graph under
    # DEEPMAP_EXCLUDE_TESTS. No anchored case-insensitive rule can separate USERTEST.JAVA from
    # LATEST.JAVA, so this is the maximal correct rule for the available signal. Uppercase-.JAVA trees
    # are already covered by the src/test/ DIRECTORY rule above, which does lowercase its components.
    if name.endswith("Test.java") or name.endswith("Tests.java"):   # JUnit CamelCase (capital T)
        return True
    return False


def _apply_recognizers(root, nodes):
    """The 1c fan-out seam: the language-recognizer registry over `root`, its supplemental nodes
    filtered through the DEEPMAP_EXCLUDE_TESTS contract (which ran inside _rebuild_code, BEFORE
    this point — recognizer nodes must not re-admit excluded test files, whose typed edges would
    otherwise re-anchor via edge_resolve) and merged into `nodes` (deduped; base extractor wins;
    the ORIGINAL list object comes back when nothing was added — Python-only byte-identity).
    The default-ON vendor exclusion holds here too (ISS-build:022): the walk never reads a file
    _recognizer_exclusion rules out (a vendored/gitignored file unless DEEPMAP_INCLUDE_VENDOR, and an
    in-tree virtualenv, dist/, caches and another checkout under .worktrees/ or .claude/worktrees/
    always), and supplemental nodes / typed edges attributed to one are dropped like the test ones —
    graphify never extracted them, so a relationship declared there resolved onto same-named PROJECT
    classes (edge_resolve's global-src fallback).
    Returns (merged_nodes, typed_edges). Never raises — degrades to (nodes, [])."""
    try:
        excluded = _recognizer_exclusion(root)
        supp, typed = language_recognizers.run(root, exclude=excluded)
        if _exclude_tests_enabled():
            if supp:
                kept = [n for n in supp if not _is_test_path(n.get("source_file", ""))]
                if len(kept) != len(supp):
                    print(f"[deepmap sync] DEEPMAP_EXCLUDE_TESTS: excluded {len(supp) - len(kept)} "
                          f"supplemental test node(s)", file=sys.stderr)
                supp = kept
            if typed:
                # Typed edges from excluded test files must be filtered HERE too: their nodes are
                # gone, but a GLOBAL_SRC_FALLBACK type (a test-declared EF cascade) would otherwise
                # re-anchor onto same-named PRODUCTION nodes — re-admitting the excluded wiring.
                typed = [e for e in typed if not _is_test_path(getattr(e, "src_file", ""))]
        # The same rule on the OUTPUT, mirroring the test filter above: the walk already skipped those
        # files, so this only drops what a recognizer attributes to an excluded file from another one.
        kept_supp = [n for n in supp if not excluded(n.get("source_file", ""))] if supp else supp
        kept_typed = [e for e in typed if not excluded(getattr(e, "src_file", ""))] if typed else typed
        if len(kept_supp) != len(supp) or len(kept_typed) != len(typed):
            print(f"[deepmap sync] vendor-ignore: excluded {len(supp) - len(kept_supp)} supplemental node(s) "
                  f"and {len(typed) - len(kept_typed)} typed edge(s) declared in vendored or non-project "
                  f"files", file=sys.stderr)
            supp, typed = kept_supp, kept_typed
        return language_recognizers.merge_supplemental(nodes, supp), typed
    except Exception as e:  # noqa: BLE001 — the recognizer layer must never break a sync
        print(f"[deepmap sync] recognizer layer degraded ({type(e).__name__})", file=sys.stderr)
        return nodes, []


def _maybe_exclude_tests(nodes, calls, imports):
    """Drop test nodes + their outgoing calls + test-authored imports when DEEPMAP_EXCLUDE_TESTS is
    set. Disabled (or no test files) -> returns the SAME objects (byte-identity: the STAGE-0 oracle
    and every default build are unchanged). Callee NAMES resolve later against build_name_index(kept),
    so a call INTO a test-only symbol auto-drops with no corruption of surviving edges."""
    if not _exclude_tests_enabled():
        return nodes, calls, imports
    excluded = {n["key"] for n in nodes if _is_test_path(n.get("source_file", ""))}
    if not excluded:
        return nodes, calls, imports
    kept_nodes = [n for n in nodes if n["key"] not in excluded]
    kept_calls = [(ck, callee) for (ck, callee) in calls if ck not in excluded]
    kept_imports = [(src, name) for (src, name) in imports
                    if src not in excluded and not _is_test_path(str(src))]
    print(f"[deepmap sync] DEEPMAP_EXCLUDE_TESTS: excluded {len(excluded)} test node(s), "
          f"kept {len(kept_nodes)}", file=sys.stderr)
    return kept_nodes, kept_calls, kept_imports


# --- vendor / generated-asset exclusion (P0-3, Rails field-review) ---------------------------------
# Vendored libraries + generated bundles (jQuery/ActiveScaffold under public/, node_modules, minified
# *.min.js) are code the user never wrote — on the review's Rails app 84% of Function nodes lived under
# public/. They dilute the vector arm and inflate graph-weighted centrality. DEFAULT-ON: dropped by
# DIRECTORY COMPONENT (never a substring, so src/publication.py / libvendor/x.js survive) + a few
# minified-file suffixes, honoring the repo .gitignore's bare-dir entries additively. Escape hatch:
# DEEPMAP_INCLUDE_VENDOR=1. The Python STAGE-0 oracle has no vendored dirs, and build/dist/target are
# deliberately NOT in the static set (they are SOURCE in some repos — this one included; a genuinely
# generated build/ is caught via .gitignore instead), so a Python build is byte-identical; ranking is
# untouched — this is an extraction-SCOPE knob only.
_VENDOR_DIR_PARTS = {"node_modules", "bower_components", "vendor", "public", "coverage",
                     ".yarn", ".pnp", ".next", ".nuxt", ".turbo", ".svelte-kit"}
_VENDOR_FILE_SUFFIXES = (".min.js", ".min.css", ".bundle.js", "-min.js", ".min.mjs")


def _include_vendor_enabled() -> bool:
    return os.environ.get("DEEPMAP_INCLUDE_VENDOR", "").strip().lower() in {"1", "true", "yes", "on"}


def _gitignore_dir_parts(root) -> set:
    """Best-effort UNANCHORED bare-directory names from the repo-root .gitignore, matched as dir
    components (mirroring gitignore's 'a bare dir matches at any depth' rule). Anchored (/foo), negated
    (!foo) and glob (*?[]) patterns are SKIPPED — matching those correctly needs a full gitignore
    engine; the curated set + explicit env cover them. Never raises."""
    parts = set()
    try:
        gi = Path(root) / ".gitignore"
        if not gi.is_file():
            return parts
        for line in gi.read_text(encoding="utf-8", errors="replace").splitlines():
            s = line.strip()
            if not s or s[0] in "#!/":
                continue
            if any(c in s for c in "*?[]"):
                continue
            seg = s.rstrip("/")
            if seg and "/" not in seg:
                parts.add(seg.lower())
    except Exception:
        pass
    return parts


def _is_vendor_path(source_file, extra_dir_parts=()) -> bool:
    """True iff `source_file` is a vendored/generated asset — by DIRECTORY COMPONENT (never substring)
    or a minified-file suffix. `extra_dir_parts` folds in the .gitignore bare-dir names."""
    raw = str(source_file or "").replace("\\", "/")
    if not raw:
        return False
    parts = raw.split("/")
    dir_parts = _VENDOR_DIR_PARTS | set(extra_dir_parts)
    if any(p.lower() in dir_parts for p in parts[:-1]):
        return True
    return parts[-1].lower().endswith(_VENDOR_FILE_SUFFIXES)


def _maybe_exclude_vendor(root, nodes, calls, imports):
    """Drop vendored/generated nodes + their outgoing calls + vendor-authored imports, DEFAULT-ON.
    DEEPMAP_INCLUDE_VENDOR=1 (or no vendor files) -> returns the SAME objects (byte-identity: a Python
    build / the STAGE-0 oracle are unchanged). Callee NAMES resolve later against build_name_index(kept),
    so a call INTO a dropped vendored symbol auto-drops with no corruption of surviving edges."""
    if _include_vendor_enabled():
        return nodes, calls, imports
    extra = _gitignore_dir_parts(root)
    excluded = {n["key"] for n in nodes if _is_vendor_path(n.get("source_file", ""), extra)}
    if not excluded:
        return nodes, calls, imports
    kept_nodes = [n for n in nodes if n["key"] not in excluded]
    kept_calls = [(ck, callee) for (ck, callee) in calls if ck not in excluded]
    kept_imports = [(src, name) for (src, name) in imports
                    if src not in excluded and not _is_vendor_path(str(src), extra)]
    print(f"[deepmap sync] vendor-ignore: dropped {len(excluded)} vendored/generated node(s) "
          f"(node_modules/vendor/public/minified/gitignored), kept {len(kept_nodes)} — set "
          f"DEEPMAP_INCLUDE_VENDOR=1 to keep them", file=sys.stderr)
    return kept_nodes, kept_calls, kept_imports


def _drop_macos_metadata(root, nodes, calls, imports):
    """The node-level backstop for macOS metadata (framework_edges.is_macos_metadata: an AppleDouble `._*` twin,
    anything under `__MACOSX/` or `.AppleDouble/`), applied to every engine's output in _rebuild_code: a node whose
    source_file (relative to `root`; an absolute one is made relative, else judged by its name) is metadata is
    dropped with its outgoing calls and imports, and — as graphify_extract._keep_rescued_only does — a call keyed to
    it (CallKey, under precise CALLS / CALLS_PLUS) and an import resolved to its key. The walks never yield such a
    file and graphify is told to leave it out (graphify_case_shim.skip_macos_metadata for detect()'s file list,
    hide_macos_metadata_listings for the engine's own directory listings), so this fires only when an engine still
    parsed some (one the shim could not patch). Always on: metadata is never source. Nothing to drop ->
    the SAME objects (byte-identity downstream). Never raises (the input back)."""
    try:
        base = Path(root).resolve()
        dropped = set()
        for n in nodes:
            sf = str(n.get("source_file") or "").replace("\\", "/")
            if not sf:
                continue
            if os.path.isabs(sf):
                try:
                    sf = Path(sf).resolve().relative_to(base).as_posix()
                except (ValueError, OSError):
                    sf = sf.rsplit("/", 1)[-1]
            if framework_edges.is_macos_metadata(sf):
                dropped.add(n.get("key"))
        if not dropped:
            return nodes, calls, imports
        keyed = graphify_extract.keyed_calls_enabled()           # only then can a callee be a node KEY
        resolved = graphify_extract.resolved_imports_enabled()   # only then can an import target be a node KEY
        kept_nodes = [n for n in nodes if n.get("key") not in dropped]
        kept_calls = [c for c in calls if c[0] not in dropped
                      and not (keyed and graphify_extract.is_call_key(c[1]) and c[1] in dropped)]
        kept_imports = [i for i in imports if i[0] not in dropped and not (resolved and i[1] in dropped)]
        print(f"[deepmap sync] macOS metadata: dropped {len(dropped)} node(s) parsed from AppleDouble ._* / "
              f"__MACOSX/ / .AppleDouble/ files, kept {len(kept_nodes)}", file=sys.stderr)
        return kept_nodes, kept_calls, kept_imports
    except Exception:  # noqa: BLE001 — a backstop must never cost the extraction
        return nodes, calls, imports


def _recognizer_exclusion(root):
    """The recognizer seam's path rule (ISS-build:022): a predicate over a root-relative posix path, True
    for a file whose declarations must never reach a recognizer. _apply_recognizers hands it to the registry
    (its walk never reads such a file; a post-pass that reads the tree itself gets it when it declares an
    `exclude` keyword) and applies it to the recognizers' output. Two parts:
      * sync's own vendor rule — exactly what _maybe_exclude_vendor drops from the ENGINE's output
        (_VENDOR_DIR_PARTS, the minified suffixes, the repo .gitignore's bare dirs) — unless
        DEEPMAP_INCLUDE_VENDOR, which keeps those files in the graph and so in the recognizers' view;
      * the directories DeepMap never treats as project source whatever the flags:
        graphify_extract._never_rescue (the virtualenv/env family incl. site-packages, dist/, caches, VCS
        and engine-output dirs, .worktrees/) and a `worktrees/` directory directly inside a dotted one —
        Claude Code's .claude/worktrees/<name>/, another checkout of the repo (graphify's one parent-aware
        skip; r283 round 3). graphify never extracts them (bar .hg/.svn/.deepmap, which hold no source), so
        a relationship declared there has no node of its own, and a GLOBAL_SRC_FALLBACK type resolved onto
        a same-named PROJECT class (a virtualenv's django.contrib.auth `Group.permissions` -> the repo's
        Group -[:HAS_MANY]-> Permission, INFERRED); a worktree's router mount also rewrote the project's
        route paths. The Python-only ast fallback does walk them (all but .venv/ and __pycache__/): in ast
        mode their .py files that the vendor rule keeps are graph nodes, yet still never recognizer input.
        The vendor dirs among them follow the flag, as above.
    Directory COMPONENTS only, relative to the root (never the path above it, not even the root's own
    name, which graphify consults for a top-level worktrees/), case-insensitive (graphify matches exact
    names, so it extracts an odd-case Env/ that is excluded here). Never raises (a failing check excludes
    nothing)."""
    vendor_parts = None if _include_vendor_enabled() else _gitignore_dir_parts(root)
    never = getattr(graphify_extract, "_never_rescue", None)

    def excluded(rel) -> bool:
        try:
            raw = str(rel or "").replace("\\", "/")
            if not raw:
                return False
            if vendor_parts is not None and _is_vendor_path(raw, vendor_parts):
                return True
            dirs = raw.split("/")[:-1]
            if callable(never):
                for part in dirs:
                    if part and part.lower() not in _VENDOR_DIR_PARTS and never(part):
                        return True
            # graphify's parent-aware skip (detect._is_noise_dir): a worktrees/ whose direct parent is a
            # dotted directory — never a bare "." / ".." path component
            for parent, part in zip(dirs, dirs[1:]):
                if part.lower() == "worktrees" and parent.startswith(".") and parent not in (".", ".."):
                    return True
            return False
        except Exception:  # noqa: BLE001
            return False

    return excluded


# ---- confidence model (the audit wedge — FD-8.5) ----------------------------
def _precise_calls_enabled() -> bool:
    """Whether a CALLS tuple's callee can be a node KEY — the push then binds exactly that node. P0-1: the
    mirror of graphify_extract.precise_calls_enabled (DEEPMAP_PRECISE_CALLS, read at push time); R10/P5: of
    graphify_extract.keyed_calls_enabled, which is also true under DEEPMAP_GRAPHIFY_CALLS_PLUS (its additions
    are keyed to the one node they resolve to, with or without precise CALLS — they used to arrive as the
    bare name and fan out: flask 44 calls -> 84 edges). Both flags OFF (default), or a callee that is a bare
    NAME (ast path / an unresolved graphify target): the keyed branch never fires and CALLS resolution is
    byte-identical to today."""
    fn = getattr(graphify_extract, "keyed_calls_enabled", None)
    if callable(fn):
        try:
            return bool(fn())
        except Exception:  # noqa: BLE001 — fall back to the precise flag alone
            pass
    return os.environ.get("DEEPMAP_PRECISE_CALLS", "").strip().lower() in {"1", "true", "yes", "on"}


def _keyed_mode_for():
    """S1 review finding 5 (slice S2): a predicate rel -> bool — can a CALLS tuple whose CALLER sits in `rel` carry a
    node KEY? map_graph keys an engine call only for a caller of a family DEEPMAP_PRECISE_CALLS selects
    (graphify_extract.precise_for: `1` = every file, a CSV = those families) and a DEEPMAP_GRAPHIFY_CALLS_PLUS addition
    for any caller. So a family CSV (`PRECISE=js`) switches the keyed reading on for its own callers only: a Python
    caller then keeps the flag-off rules (a NAME equal to some node's key — a root `cli.py`'s Module `cli` for a
    `cli()` call — reads as that key, exactly as with the flag off). Both flags off: False everywhere (byte-identical
    to before); `1` or CALLS_PLUS: True everywhere (as before). Falls back to the graph-wide
    _precise_calls_enabled() when graphify_extract cannot answer. Never raises."""
    if not _precise_calls_enabled():
        return lambda rel: False
    try:
        if graphify_extract.graphify_calls_plus_enabled():
            return lambda rel: True
        fams = graphify_extract.precise_calls_families()
        pf = graphify_extract.precise_for
        memo: dict = {}

        def keyed(rel) -> bool:
            r = str(rel or "")
            if r not in memo:
                try:
                    memo[r] = bool(pf(r, fams))
                except Exception:  # noqa: BLE001
                    memo[r] = True
            return memo[r]
        return keyed
    except Exception:  # noqa: BLE001 — no per-family answer: the graph-wide switch, as before
        return lambda rel: True


# ---- S2 (receiver-calls design 6.1 / 7.3, decision D0): the cross-family filter on the NAME branch ------------------
# graphify never resolves a call across languages, and _resolve_calls' keyed branch already drops a key into another
# KNOWN language family (edge_resolve.lang_family). The name fan-out did not: a Python `Order()` reached a TypeScript
# `Order` class, a Python `save()` a Ruby `Order.save`. Under DEEPMAP_CALLS_FAMILY_FILTER a name-resolved caller's
# targets lose every target whose known family differs from the caller's known family, BEFORE the grade is counted
# (one Python and one TypeScript `Order` -> the Python one, INFERRED: the language allows only it). A caller or target
# without a known family (an unknown extension) is never filtered, and `.ts` / `.js` (js) and `.erb` / `.rb` (ruby)
# share a family. _attach_local_defs counts the same kept targets. Default OFF until its own decision (D0 — slice S8
# flips _CALLS_FAMILY_FILTER_DEFAULT); off, every pass is byte-identical. sync_status classifies the knob.
_CALLS_FAMILY_FILTER_DEFAULT = False


def _calls_family_filter_enabled() -> bool:
    """DEEPMAP_CALLS_FAMILY_FILTER — unset / blank -> _CALLS_FAMILY_FILTER_DEFAULT; while that is off, on iff an ON-word
    (1/true/yes/on), else off iff an OFF-word (sync_status: a default-off knob — _DEFAULT_OFF_KNOBS)."""
    raw = str(os.environ.get("DEEPMAP_CALLS_FAMILY_FILTER", "") or "").strip().lower()
    if not raw:
        return _CALLS_FAMILY_FILTER_DEFAULT
    if _CALLS_FAMILY_FILTER_DEFAULT:
        return raw not in {"0", "false", "no", "off"}
    return raw in {"1", "true", "yes", "on"}


def _family_memo():
    """A memoised edge_resolve.lang_family for one pass (None = no known family). Never raises."""
    memo: dict = {}

    def fam(path):
        p = str(path or "")
        if p not in memo:
            try:
                memo[p] = edge_resolve.lang_family(p)
            except Exception:  # noqa: BLE001
                memo[p] = None
        return memo[p]
    return fam


def _same_family(caller_file, targets, file_of: dict, fam) -> list:
    """The targets the cross-family filter keeps for a caller in `caller_file` (see the block comment above): all of
    them when the caller has no known family, else those whose file has the caller's family or none."""
    fa = fam(caller_file)
    if fa is None:
        return targets
    return [t for t in targets if fam(file_of.get(t, "")) in (None, fa)]


def _is_call_key(callee) -> bool:
    """R11/R1: True iff the producer MARKED `callee` as a node key (graphify_extract.CallKey — map_graph's
    precise engine calls, precise supplemental TS/Rust calls and DEEPMAP_GRAPHIFY_CALLS_PLUS additions).
    Key-ness is no longer inferred from `callee in <node keys>`: a NAME that equals some node's key (a root
    `cli.py` defining `cli()`, `build/insights.py` defining `build_insights()`, `from main import main`) is
    a name and fans out by name. Never raises; a plain str (every ast / flag-off callee) is never a key."""
    try:
        return isinstance(callee, str) and getattr(callee, "_DEEPMAP_CALL_KEY", False) is True
    except Exception:  # noqa: BLE001
        return False


def _drop_app_to_test_enabled() -> bool:
    """Opt-in (default OFF): drop a CALLS edge from a NON-test caller to a callee resolved under a test
    dir. On a graphify graph an app association-reader call (activity/teacher/…) can resolve BY NAME to
    the only defined method of that name — a memoized fixture in test/ — producing app->test edges that
    pollute deepmap_impact (2026-07 field audit, F8). FINER than DEEPMAP_EXCLUDE_TESTS (which drops the
    test NODES entirely): this keeps test nodes searchable but strips the misleading cross-boundary call.
    Default OFF so the default graph — including the byte-frozen STAGE-0 Python oracle — is unchanged."""
    return os.environ.get("DEEPMAP_DROP_APP_TO_TEST_CALLS", "").strip().lower() in {"1", "true", "yes", "on"}


def _edge_confidence(n_targets: int) -> str:
    """Confidence for a name-resolved CALLS edge. A single candidate is INFERRED (a name match,
    not a guaranteed binding); >1 candidate is AMBIGUOUS (the callee name maps to several
    symbols, so the true target is uncertain). This is the signal that makes the graph
    auditable — callers can see which edges are firm vs. guessed."""
    return "AMBIGUOUS" if n_targets > 1 else "INFERRED"


# ---- push_to_neo4j: MERGE nodes + CALLS + typed/runtime edges + IMPORTS ----------------------
# Batched, and the edge rebuild is ATOMIC (2026-09 live validation, #29 + T10b). The push used to send one
# autocommit statement per node and per edge, and each edge statement MATCHed both endpoints with no label
# — two full node scans per edge, ~12 edges/s, hours on a real repo. It also deleted every live node's
# :CALLS in one autocommit before re-creating them one by one, so every refresh showed deepmap_impact and
# the graph arm a near-empty edge set for minutes (918 -> 8), and a killed sync left it partial.
# Now nodes are upserted per label in UNWIND batches, and the edges are pruned and re-created INSIDE write
# transactions whose UNWIND batches MATCH each endpoint by label (an index seek on deepmap_key_<label>):
# a reader sees the old edges until the commit and the new ones after it, and a sync killed mid-rebuild
# rolls back to the old set. Every edge is ANCHORED at one endpoint (a :CALLS edge at its caller, a typed
# edge at its emitted src — for a reversed CASCADES that is its target, the principal, which is not always
# the declaring node — and an :IMPORTS edge at the importing module), and each write transaction rebuilds
# ALL edge types of one group of anchors. Up to _EDGE_TX_MAX_ROWS edge rows the whole rebuild is ONE
# transaction; a bigger repo commits one group at a time — each anchor's edges still swap atomically — so
# the transaction state stays bounded. A row is an
# edge the transaction WRITES or an existing edge its prunes DELETE (every type; round 3 — round 2 counted
# only the writes, so a re-sync that shrank the edge set deleted it all in one transaction: live, 203k
# stale :IMPORTS hit MemoryPoolOutOfMemoryError at the 358 MiB cap, rolled back, and would every time).
# Measured on Neo4j 5.26 with the 512m per-instance heap (transaction cap ~358 MB = 70%): ~2 KB of
# transaction state per counted row; a re-sync deleting and re-writing 203k edges peaked at 42 MB per
# transaction at 20k rows and at 77 MB at 40k rows, with the SAME wall time (~29 s) — so 20k it is. An
# anchor that alone exceeds the budget is rewritten in bounded pieces (_rewrite_oversized_anchor).
# (Round 1 bounded only :CALLS and wrote every typed edge and :IMPORTS in the LAST transaction.)
_PUSH_BATCH = 1000            # rows per UNWIND statement
_EDGE_TX_MAX_ROWS = 20_000    # edge rows (written + pruned, all types) per write transaction


def _batches(rows):
    for i in range(0, len(rows), _PUSH_BATCH):
        yield rows[i:i + _PUSH_BATCH]


def _fts_short_name_enabled() -> bool:
    """DEEPMAP_FTS_SHORT_NAME (round 7, I2 — default OFF): write `short_name` (the bare `method` of a
    dotted `Class.method` name) on every node, which indexes.create_all_indexes adds to deepmap_code_fts.
    The standard analyzer keeps `Class.method` as ONE token, so BM25 could not match a method by its bare
    name (T4#2). An index-content change — measured on the benchmark before any default flip; OFF leaves
    the upsert statements and the index definition byte-identical. Same switch as indexes.py reads."""
    return os.environ.get("DEEPMAP_FTS_SHORT_NAME", "").strip().lower() in {"1", "true", "yes", "on"}


def _short_name(name) -> "str | None":
    """'run' for 'Svc.run'; None for an undotted name (its `name` token already matches) or a trailing dot."""
    s = str(name or "")
    if "." not in s:
        return None
    tail = s.rsplit(".", 1)[1]
    return tail or None


def _upsert_nodes(session, nodes) -> None:
    """MERGE every node by (:Label {key}), one UNWIND statement per label and batch. Parsed nodes are
    EXTRACTED (directly from source); confidence is per node so the retriever can surface it on every
    result. The label is interpolated exactly as the per-node MERGE always did (Cypher cannot bind it).
    Under DEEPMAP_FTS_SHORT_NAME=1 each row also sets `short_name` (null — i.e. removed — when the name
    has no dotted tail)."""
    short = _fts_short_name_enabled()
    by_label: dict = {}
    for n in nodes:
        row = {"key": n["key"], "name": n["name"], "sf": n["source_file"], "line": n["start_line"],
               "summ": n["body_summary"], "conf": n.get("confidence", "EXTRACTED")}
        if short:
            row["short"] = _short_name(n["name"])
        by_label.setdefault(n["label"], []).append(row)
    extra = ", x.short_name=row.short" if short else ""
    for label, rows in by_label.items():
        for chunk in _batches(rows):
            session.run(f"UNWIND $rows AS row MERGE (x:{label} {{key:row.key}}) "
                        "SET x.name=row.name, x.source_file=row.sf, x.start_line=row.line, "
                        "x.body_summary=row.summ, x.confidence=row.conf" + extra, rows=chunk)


# ---- round 10 (P2 / P3): the full-text index ends a sync exactly as a fresh build leaves it ---------------
# Lucene keeps an UPDATED or DELETED document in the BM25 statistics (docCount / docFreq) until its segment
# merges, so after a sync that rewrote summaries (a summary flag, an edit, enrich) or deleted nodes (the
# stale prune, a project switch) the same graph ranked differently from a fresh build (measured on benchmark
# corpora: answers moved, and a clean rebuild restored the fresh ranking). A fresh build only
# ever ADDS documents. So:
#   * before the push, _fts_changed_docs counts the EXISTING code nodes whose indexed text (name,
#     body_summary; short_name while it is indexed) the push will rewrite — server side, by the upsert's own
#     key seeks; only the count comes back;
#   * a stale prune that will delete nodes, a stale short_name removal (P3) and an interrupted earlier
#     rebuild (the `fts_rebuild_pending` stamp on the :_DeepMapMeta singleton, set BEFORE the push and
#     cleared once the index is re-created) count too;
#   * then, after the push and the prune, deepmap_code_fts is DROPPED and re-CREATED (indexes.
#     rebuild_fts_index): populated from the graph as it is, additions only — a fresh build's state;
#   * a project switch re-creates it right after the wipe, on the empty graph, before anything is pushed.
# A sync that changes no indexed text and deletes nothing (the common no-op re-sync — Neo4j writes no
# index update for a property SET to its current value) keeps the index as it is. The cost is one index
# population, O(nodes), only on a sync that changed indexed text (measured: see the round-10 notes); the FTS
# arm sits out the few seconds the index is re-populating.
_FTS_PENDING = "fts_rebuild_pending"
_FTS_NUL = "\x00"                 # the coalesce sentinel: tells a missing property from ''


def _fts_code_labels() -> list:
    """The labels deepmap_code_fts covers (indexes.FTS_INDEXES) — only these rewrite its documents."""
    for name, labels, _props in getattr(indexes, "FTS_INDEXES", ()) or ():
        if name == getattr(indexes, "CODE_FTS_INDEX", "deepmap_code_fts"):
            return [lb for lb in str(labels).split("|") if lb]
    return ["Function", "Class", "Module", "Method", "Route"]


def _first_count(res, field: str = "c") -> int:
    """The integer `field` of a result's first row; 0 when there is none (never raises)."""
    try:
        rows = list(res) if res is not None else []
        return int(rows[0][field] or 0) if rows else 0
    except (KeyError, IndexError, TypeError, ValueError):
        return 0


def _fts_changed_docs(driver, nodes) -> int:
    """How many EXISTING nodes this push rewrites a full-text-indexed property of — `name` and
    `body_summary`, plus `short_name` while DEEPMAP_FTS_SHORT_NAME puts it in the index (see the block
    comment above). Read before the push; a database error propagates (fail loud, like the push)."""
    short = _fts_short_name_enabled()
    covered = set(_fts_code_labels())
    by_label: dict = {}
    for n in nodes:
        if n.get("label") not in covered:
            continue
        row = {"key": n["key"], "name": n["name"], "summ": n["body_summary"]}
        if short:
            row["short"] = _short_name(n["name"])
        by_label.setdefault(n["label"], []).append(row)
    cond = ("coalesce(x.name, $nul) <> coalesce(row.name, $nul) "
            "OR coalesce(x.body_summary, $nul) <> coalesce(row.summ, $nul)"
            + (" OR coalesce(x.short_name, $nul) <> coalesce(row.short, $nul)" if short else ""))
    changed = 0
    with driver.session() as s:
        for label, rows in by_label.items():
            if not _SAFE_LABEL.match(str(label)):
                continue
            for chunk in _batches(rows):
                changed += _first_count(s.run(f"UNWIND $rows AS row MATCH (x:{label} {{key:row.key}}) "
                                              f"WHERE {cond} RETURN count(x) AS c", rows=chunk, nul=_FTS_NUL))
    return changed


def _fts_rebuild_pending(driver) -> bool:
    """True iff an earlier sync changed indexed text and did not get to re-create the index."""
    with driver.session() as s:
        res = s.run(f"MATCH (m:_DeepMapMeta) RETURN m.{_FTS_PENDING} AS p LIMIT 1")
        try:
            return any(bool(r["p"]) for r in (list(res) if res is not None else []))
        except (KeyError, IndexError, TypeError):
            return False


def _mark_fts_rebuild(driver, pending: bool) -> None:
    """Set (before the push) or clear (once the index is re-created) the pending stamp on the meta
    singleton. MERGE: a pre-owner graph gets the singleton now; the owner stamp follows the push."""
    with driver.session() as s:
        if pending:
            s.run(f"MERGE (m:_DeepMapMeta {{id:'singleton'}}) SET m.{_FTS_PENDING} = true")
        else:
            s.run(f"MATCH (m:_DeepMapMeta) REMOVE m.{_FTS_PENDING}")


def _rebuild_code_fts(driver) -> bool:
    """indexes.rebuild_fts_index for deepmap_code_fts; False when the index module offers none."""
    fn = getattr(indexes, "rebuild_fts_index", None)
    if not callable(fn):
        return False
    fn(driver)
    return True


def _remove_stale_short_names(driver) -> int:
    """R10/P3: with DEEPMAP_FTS_SHORT_NAME off, remove the `short_name` an earlier flag-on sync wrote — on
    every node, in bounded batches (_run_batched) — so a later flag-on sync never serves a stale one (the
    upsert only writes short_name while the flag is on; 475 stale ones on requests). Returns how many nodes
    carried one; a graph that never had the flag on costs one read and no write. Flag on: 0, nothing read.
    A database error propagates."""
    if _fts_short_name_enabled():
        return 0
    with driver.session() as s:
        n = _first_count(s.run("MATCH (n) WHERE n.short_name IS NOT NULL RETURN count(n) AS c"))
    if n:
        _run_batched(driver, "MATCH (n) WHERE n.short_name IS NOT NULL", "n", "REMOVE n.short_name",
                     _DELETE_NODE_BATCH)
    return n


_VERDICT_RANK = {"drop": 0, "amb": 1, "base": 2}


def _project_import_names(nodes) -> set:
    """Lowercased names a PROJECT import can start with: every directory component and file stem of a
    node's source_file, plus Module node names. Conservative by design — an external module that happens
    to share a name with any project file or directory is treated as the project's (no edge is dropped)."""
    names: set = set()
    for n in nodes:
        parts = [p for p in str(n.get("source_file") or "").replace("\\", "/").split("/") if p]
        if parts:
            parts[-1] = parts[-1].rsplit(".", 1)[0]
            names.update(p.lower() for p in parts)
        if n.get("label") == "Module" and n.get("name"):
            names.add(str(n["name"]).lower())
    return names


def _receiver_verdict(kind: str, callee: str, target_name: str, same_file: bool, project_names) -> str:
    """How a receiver kind (see _receiver_kind) supports one name-resolved target: 'base' (graded as
    before), 'amb' (plausible but unverified -> AMBIGUOUS) or 'drop' (Python's name rules exclude it).
      bare       — a local/closure/module/builtin name: never another FILE's method (a qualified target
                   reached only through its last segment). Same-file targets keep their grade: the ast
                   engine names a function nested in a method `Class.inner`.
      self       — as before.
      mod:<X>    — X from an import: a relative or project module keeps the grade; an EXTERNAL module
                   (re, subprocess, os.path, numpy, pathlib.Path, ...) is never a project def -> drop.
      name:<N>   — `N.m()`: exactly `N.m` keeps the grade (a class N); anything else is unverified.
      expr       — the receiver's type is unknown -> AMBIGUOUS."""
    if kind == "self":
        return "base"
    if kind == "bare":
        return "base" if same_file or target_name == callee else "drop"
    if kind.startswith("mod:"):
        mod = kind[4:]
        if mod.startswith("."):
            return "base"
        return "base" if mod.split(".")[0].lower() in project_names else "drop"
    if kind.startswith("name:"):
        return "base" if target_name == f"{kind[5:]}.{callee}" else "amb"
    return "amb"


# ---- round 8 (J3): calls the language rules out, at PUSH time ------------------------------------------
# graphify_extract.map_graph drops a call the ENGINE bound to something the language cannot reach (a bare
# call bound to another file's method, a Go production file bound to a _test.go symbol). But on the default
# path a caller reaches the push as (caller, NAME), and the name fan-out below re-created exactly those
# edges: every node whose last dotted segment is the name. Live on this repo AuthBackoff.check collected
# 1,173 AMBIGUOUS edges from bare `check(...)` calls in tests/ (which define their own module-level check())
# and topped the graph arm for every query. Two rules, both guaranteed by the language, so default ON under
# the map-time filter's own opt-out (DEEPMAP_FALSE_CALL_FILTER=0 restores the round-7 resolution):
#   * bare -> method: in Python / JS / TS / Go / Rust a call WITHOUT a receiver never reaches a method
#     (Python needs self./obj., JS/TS this./obj., Go/Rust a receiver). When EVERY call of the name in the
#     caller's body is bare (_attach_bare_calls reads it from source; the ast path already records it), a
#     :Method target in ANOTHER file, or of ANOTHER class in the caller's file, is dropped. One `obj.name(`
#     anywhere in the body keeps every target — the same "impossible only if every call site is" rule the
#     map-time filter uses. Java/C#/Kotlin/Ruby/C++/Swift have an implicit this, so they are never judged.
#   * Go: a file not ending in _test.go never calls into one (go build compiles _test.go only for go test).
_BARE_NO_METHOD_EXTS = tuple(getattr(graphify_extract, "_BARE_CALL_NO_METHOD_EXTS", ()) or (
    ".py", ".pyi", ".go", ".rs", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"))
_BARE_SCAN_MAX_LINES = 5000   # a caller span (or a module) longer than this is not judged: its edges stay


def _false_call_filter_on() -> bool:
    """graphify_extract.false_call_filter_enabled — default ON; DEEPMAP_FALSE_CALL_FILTER=0 turns the
    map-time AND the push-time rules off (the graph is then exactly the round-7 one). Never raises."""
    fn = getattr(graphify_extract, "false_call_filter_enabled", None)
    if callable(fn):
        try:
            return bool(fn())
        except Exception:
            pass
    return str(os.environ.get("DEEPMAP_FALSE_CALL_FILTER", "1")).strip().lower() not in {"0", "false", "no", "off"}


def _bare_call_lang(source_file) -> bool:
    """A language where a call without a receiver can never be a method call (case-insensitive ext)."""
    return str(source_file or "").lower().endswith(_BARE_NO_METHOD_EXTS)


def _go_production_file(source_file) -> bool:
    """A Go file the plain build compiles — `.go` and not `_test.go` (case-sensitive, as the go tool)."""
    s = str(source_file or "")
    return s.endswith(".go") and not s.endswith("_test.go")


def _class_ctx(node) -> "str | None":
    """The class a node's body belongs to: a Class node's own name; the owner of a `Owner.member`;
    None for a module, a top-level function, or anything unnamed."""
    if not node or node.get("label") == "Module":
        return None
    name = str(node.get("name") or "")
    if node.get("label") == "Class":
        return name or None
    return name.rsplit(".", 1)[0] if "." in name else None


def _bare_unreachable(caller, target) -> bool:
    """True iff a BARE call written in `caller` can never reach `target`: a :Method in another file, or
    in the caller's own file but of a class other than the one the caller belongs to (a class body can
    call its own earlier defs by bare name, and the ast engine names a def nested in a method
    `Owner.inner` — both stay reachable). An owner-less Method in the caller's file is kept."""
    if not caller or not target or target.get("label") != "Method":
        return False
    if str(target.get("source_file") or "") != str(caller.get("source_file") or ""):
        return True
    tname = str(target.get("name") or "")
    if "." not in tname:
        return False
    return _class_ctx(caller) != tname.rsplit(".", 1)[0]


def _resolve_calls(nodes, calls) -> dict:
    """PURE: the :CALLS edges to persist, as {(caller_key, callee_key): confidence} in first-seen order.

    name_to_keys is edge_resolve.build_name_index (full name + last dotted segment -> keys), the index
    the typed-edge push shares. A repeated (caller, callee) pair is ONE edge carrying the last grade —
    what the old per-edge MERGE ... SET left behind. A caller that is not a node of this sync gets no
    edge: only this sync's nodes have their outgoing edges pruned, so it would keep a phantom forever.

    Round 7 — two node attributes refine the grades, and a caller carrying neither resolves exactly as
    before (the graphify path, synthetic tuples):
      * `call_recv` (ast callers — see _receiver_kind): how each callee was reached. A target Python's
        name rules exclude is DROPPED (an external-module receiver: `re.search(...)` never binds the
        project's DeepMapRetriever.search; a bare call never binds another file's method); a target
        reached through a receiver of unknown type is AMBIGUOUS; the rest keep the count-based grade,
        counted over the targets that remain (T3#5).
      * `local_defs` (_attach_local_defs): names the caller's own scope chain defines. A bare-name call
        to such a name binds the caller's own def, so a target in ANOTHER file is AMBIGUOUS — a grade
        change only, the edge and CALLS in-degree stay (T3#6).

    Round 8 (J3, default ON; DEEPMAP_FALSE_CALL_FILTER=0 restores the above exactly): a target the
    language rules out is dropped before grading, so the confidence counts only what remains — a Go
    production file never binds a _test.go symbol (name fan-out and precise keys), and a name the caller
    calls ONLY without a receiver (`call_bare`, from _attach_bare_calls; `call_recv` "bare" on the ast
    path) never binds a :Method of another file or of another class (_bare_unreachable). Round 9 (K11): an
    ast caller's `call_alias` (from _attach_bare_calls) names the bare-called names that MAY hold a bound
    method (an imported value alias `check = _default.check`, a local / parameter / closure value); for
    those the "bare" receiver kind rules nothing out and the targets keep the count-based grade — what
    the graphify path does for the same source. Only attached (and only read) while the filter is on.

    DEEPMAP_LOCAL_DEF_CALLS (default OFF; see _attach_local_def_calls): a Python caller's `call_local`
    {name: [keys]} restricts that name's targets to the caller's own definition(s) BEFORE every rule
    above, which then grade what remains (same-file targets, so the J3 / T3#6 / F8b / Go rules find
    nothing to change); under precise CALLS a key the language rules out is re-bound to them with F3b's
    same-file grade. Flag unset: the metadata is ignored and this function is byte-identical.

    Round 11b (G1) + 11c (C1): a graphify caller's `call_member` (map_graph, from the G5 self-call rebinder)
    names the bare names it reaches ONLY through a member call `x.m()` whose class the engine's file could
    not pin. Those names never resolve to a :Module (a file is not callable: flask's `self.serializer.tag(v)`
    landed on json/tag.py) or a :Class; :Method AND module-level :Function targets stay (`LR.run(root)` /
    `helpers.render(v)` through an imported module is a call of that function). The grade is the name
    fan-out's own, counted BEFORE that exclusion, so removing an impossible target never promotes a kept
    edge (fastapi: `self.app(...)` -> APIRouter.app stays AMBIGUOUS). The T3#6 local-def rule (a BARE call
    binds the caller's own def) never grades them. It rides DEEPMAP_SELF_CALL_REBIND: with G5 off no node
    carries the attribute.

    Slice S2 (typed-receiver call linking, receiver-calls design 6.1 / 4): a callee its producer MARKED as a node
    key (_is_call_key) binds exactly that node whatever DEEPMAP_PRECISE_CALLS says — with every flag off nothing
    is marked, so this is byte-identical. Two private attributes of typed_calls.link (DEEPMAP_TYPED_CALLS /
    DEEPMAP_CALLS_AUDIT; never pushed):
      * `call_typed` {target key: grade}: the caller's (caller, key) tuple to such a target takes EXACTLY that
        grade (a same-file typed call can be INFERRED) — the DEEPMAP_LOCAL_DEF_CALLS re-bind and the T3#6
        demotion are bare-call rules and never apply to it; the cross-family, F8b and Go `_test.go` drops do;
      * `call_weak` [target key, ...]: applied LAST — an existing (caller, target) edge becomes AMBIGUOUS
        (it only lowers; it never adds an edge).
    DEEPMAP_CALLS_FAMILY_FILTER (default OFF; D0): the name branch drops targets of another known language family
    before grading (see _calls_family_filter_enabled)."""
    name_to_keys = edge_resolve.build_name_index(nodes)
    drop_att = _drop_app_to_test_enabled()   # F8b: opt-in NON-test-caller -> test-callee demotion
    fcf = _false_call_filter_on()            # J3: the language rules below (default ON)
    # S2: the typed pass's grades (call_typed) and its audit's demotions (call_weak) — absent unless it ran
    typed_of = {n["key"]: n["call_typed"] for n in nodes if isinstance(n.get("call_typed"), dict)}
    weak_of = {n["key"]: n["call_weak"] for n in nodes
               if n.get("call_weak") and isinstance(n.get("call_weak"), (list, tuple, set, frozenset))}
    fam = _family_memo() if _calls_family_filter_enabled() else None   # D0: the cross-family filter (default OFF)
    # DEEPMAP_LOCAL_DEF_CALLS (default OFF): the definition(s) a bare Python call binds in its own file
    bind_of = ({n["key"]: n["call_local"] for n in nodes if isinstance(n.get("call_local"), dict)}
               if _local_def_calls_enabled() else {})
    bind_name = {n["key"]: str(n.get("name") or "") for n in nodes} if bind_of else {}
    key_to_file = {n["key"]: str(n.get("source_file", "")) for n in nodes}
    recv_of = {n["key"]: n["call_recv"] for n in nodes if isinstance(n.get("call_recv"), dict)}
    local_of = {n["key"]: set(n["local_defs"]) for n in nodes if n.get("local_defs")}
    bare_of = ({n["key"]: set(n["call_bare"]) for n in nodes if n.get("call_bare")} if fcf else {})
    alias_of = ({n["key"]: set(n["call_alias"]) for n in nodes if n.get("call_alias")} if fcf else {})
    key_to_name = ({n["key"]: str(n.get("name") or "") for n in nodes} if (recv_of or local_of) else {})
    node_of = ({n["key"]: n for n in nodes} if (fcf and (recv_of or bare_of)) else {})
    member_of = _member_names(nodes)          # R11b/G1 + R11c/C1: G5 member-call fall-backs -> never a :Module/:Class
    unfit_keys = {n["key"] for n in nodes if n.get("label") in _MEMBER_UNFIT_LABELS} if member_of else set()
    project_names = None
    edges: dict = {}

    def recv_verdict(kind, ck, caller_key, callee, caller_file):
        if alias_of and str(kind) == "bare" and callee in alias_of.get(caller_key, ()):
            return "base"        # R9/K11: the bare name may hold a bound method — no bare rule applies
        v = _receiver_verdict(str(kind), callee, key_to_name.get(ck, ""), key_to_file.get(ck) == caller_file,
                              project_names or ())
        if (v == "base" and fcf and str(kind) == "bare" and _bare_call_lang(caller_file)
                and _bare_unreachable(node_of.get(caller_key), node_of.get(ck))):
            return "drop"        # J3: a same-file method of ANOTHER class is out of a bare call's reach
        return v

    for caller_key, callee in calls:
        if caller_key not in key_to_file:
            continue
        caller_file = key_to_file[caller_key]
        go_prod = fcf and _go_production_file(caller_file)   # J3: never into a _test.go file
        local = local_of.get(caller_key)
        # P0-1 (opt-in): a graphify-RESOLVED call carries the target's KEY (a live node), so make
        # ONE firm edge — no name re-resolution / fan-out. Flag OFF (or ast/name tuples) -> this branch
        # never fires and the fan-out below is byte-identical to today. R11/R1: only a callee the
        # producer MARKED as a key (_is_call_key) takes it — a NAME that equals some node's key (a root
        # cli.py's Module `cli` for a `cli()` call) fans out by name like every other name. A resolved
        # callee that got test/vendor-excluded is no longer a live key, so it drops cleanly (falls
        # through -> name_to_keys miss -> 0 targets). S2: a MARKED key is honoured per tuple, whatever the flags
        # (with every flag off nothing is marked); a key the typed pass made (call_typed) takes its grade exactly.
        if _is_call_key(callee) and callee in key_to_file:
            tg = (typed_of.get(caller_key) or {}).get(callee) if typed_of else None
            if tg not in _TYPED_GRADES:
                tg = None
            bound = (bind_of.get(caller_key) or {}).get(bind_name.get(callee, "").rsplit(".", 1)[-1]) \
                if (bind_of and tg is None) else None   # a typed key is never re-bound (a bare-call rule)
            if bound is not None and callee not in bound:
                # DEEPMAP_LOCAL_DEF_CALLS: the engine bound a bare call the caller's own file defines to
                # another file (or to a method a bare call cannot reach) -> the local definition(s) instead,
                # graded by F3b's same-file rule (EXTRACTED; AMBIGUOUS when there are several, e.g. an
                # if/else pair of defs). A nested def with no node binds nothing: the edge is dropped.
                keep = [ck for ck in bound if ck != caller_key and ck in key_to_file]
                conf = "EXTRACTED" if len(keep) == 1 else "AMBIGUOUS"
                for ck in keep:
                    edges[(caller_key, ck)] = conf
                continue
            callee_file = key_to_file[callee]
            fam_a = edge_resolve.lang_family(caller_file)
            fam_b = edge_resolve.lang_family(callee_file)
            if fam_a is not None and fam_b is not None and fam_a != fam_b:
                # graphify never resolves a call across languages; a key-form callee in another KNOWN
                # family is its id COLLISION (an unresolved `format` gets the same id as a root-level
                # helpers.rb's `format`) — drop it, exactly as edge_resolve.resolve_imports drops a
                # cross-family import key. Without the colliding file the call is a dangling external
                # anyway, so the caller's edges no longer depend on an unrelated file of another
                # language. Precise mode only: the default name path below is unchanged.
                continue
            if drop_att and _is_test_path(callee_file) and not _is_test_path(caller_file):
                continue   # F8b: strip the app->test-fixture cross-boundary edge (opt-in)
            if go_prod and callee_file.endswith("_test.go"):
                continue   # J3: go build never compiles a _test.go file into a production caller
            if callee != caller_key:
                if tg is not None:
                    edges[(caller_key, callee)] = tg    # S2: the typed pass's grade, exactly (never T3#6)
                    continue
                # F3b: EXTRACTED is reserved for a LITERAL same-file relationship (edge_resolve's
                # cross-file-never-EXTRACTED principle). A graphify-resolved CROSS-FILE call is a
                # firm binding but NOT a same-file literal -> INFERRED (was mis-stamped EXTRACTED).
                same = bool(caller_file and caller_file == callee_file)
                conf = "EXTRACTED" if same else "INFERRED"
                if not same and local and key_to_name.get(callee, "").rsplit(".", 1)[-1] in local:
                    conf = "AMBIGUOUS"          # the caller's own scope defines that name (T3#6)
                edges[(caller_key, callee)] = conf
            continue
        targets = [ck for ck in name_to_keys.get(callee, []) if ck != caller_key]
        if fam is not None and targets:
            targets = _same_family(caller_file, targets, key_to_file, fam)   # D0 (opt-in): never another language
        member = bool(member_of) and callee in member_of.get(caller_key, ())
        if bind_of:
            bound = (bind_of.get(caller_key) or {}).get(callee)
            if bound is not None:              # DEEPMAP_LOCAL_DEF_CALLS: only the caller's own definition(s)
                bound = set(bound)
                targets = [ck for ck in targets if ck in bound]
        if drop_att and not _is_test_path(caller_file):
            # F8b: a NON-test caller's name-resolved CALLS must not land on test-dir nodes (the
            # graphify app->test-fixture misbind); keeps test nodes searchable, drops the edge only.
            targets = [ck for ck in targets if not _is_test_path(key_to_file.get(ck, ""))]
        if go_prod:
            targets = [ck for ck in targets if not key_to_file.get(ck, "").endswith("_test.go")]
        kinds = (recv_of.get(caller_key) or {}).get(callee)
        if kinds:
            if project_names is None and any(str(k).startswith("mod:") for k in kinds):
                project_names = _project_import_names(nodes)
            best = {ck: max((recv_verdict(k, ck, caller_key, callee, caller_file) for k in kinds),
                            key=_VERDICT_RANK.get)
                    for ck in targets}
            targets = [ck for ck in targets if best[ck] != "drop"]
            econf = _edge_confidence(len(targets))
            graded = [(ck, econf if best[ck] == "base" else "AMBIGUOUS") for ck in targets]
        else:
            if callee in bare_of.get(caller_key, ()):
                # J3: every call of this name in the caller's body is bare -> no method it can't reach
                caller = node_of.get(caller_key)
                targets = [ck for ck in targets if not _bare_unreachable(caller, node_of.get(ck))]
            econf = _edge_confidence(len(targets))
            graded = [(ck, econf) for ck in targets]
        if local and callee in local and not member:
            graded = [(ck, conf if key_to_file.get(ck) == caller_file else "AMBIGUOUS") for ck, conf in graded]
        if member:
            # R11c/C1: a member call `x.m()` never reaches a :Module (a file is not callable) or a :Class, so those
            # edges go — AFTER the grade, which counted them: removing an impossible target is no evidence for the
            # ones left, so it never promotes a kept edge (fastapi's `self.app(...)` -> APIRouter.app stays
            # AMBIGUOUS). A module-level :Function stays: `LR.run(root)` / `helpers.render(v)` through an imported
            # module IS a call of it (R11b's :Method-only rule lost those true edges).
            graded = [(ck, conf) for ck, conf in graded if ck not in unfit_keys]
        for ck, conf in graded:
            edges[(caller_key, ck)] = conf
    if weak_of:
        # S2: the untyped-receiver audit (typed_calls, DEEPMAP_CALLS_AUDIT) — AFTER every other rule, an existing
        # (caller, target) edge it names becomes AMBIGUOUS. Grade-only: no edge is added or removed, and the
        # first-seen order of `edges` is kept.
        for caller_key, weak in weak_of.items():
            for t in weak:
                if (caller_key, t) in edges:
                    edges[(caller_key, t)] = "AMBIGUOUS"
    return edges


# S2: the only grades a typed key may carry (any other string would read as EXTRACTED in impact's worstOrd —
# retrieval.py); a `call_typed` value outside them is ignored (the key then gets the keyed branch's own grade)
_TYPED_GRADES = frozenset({"EXTRACTED", "INFERRED", "AMBIGUOUS"})

# R11c/C1: the labels a G5 member fall-back (`call_member`) never resolves to — see _resolve_calls
_MEMBER_UNFIT_LABELS = frozenset({"Module", "Class"})


def _member_names(nodes) -> dict:
    """{caller key: set of bare names} from map_graph's `call_member` (R11b/G1) — the names a graphify caller
    reaches only through a member call G5 could not pin to one class (resolved to anything but a :Module / :Class,
    R11c/C1). Empty when G5 is off (no attribute)."""
    out: dict = {}
    for n in nodes:
        names = n.get("call_member")
        if names and isinstance(names, (list, tuple, set, frozenset)):
            out.setdefault(n["key"], set()).update(str(x) for x in names)
    return out


# ---- nested-def shadowing on the graphify path (round 7, T3#6) ---------------------------------------
# graphify makes no node for a def nested in a function, so a bare call to one (`_rec(1)` inside the
# gate_setup that defines `_rec`) resolved by name to ANOTHER file's `_rec` — a single candidate, INFERRED.
# Python binds that call to the caller's own def. _attach_local_defs reads the scope chain of such callers
# from the source (only the files that need it) and _resolve_calls grades those cross-file targets
# AMBIGUOUS. ast callers are skipped: their nested defs ARE nodes, so the fan-out already says AMBIGUOUS.
_DEF_OR_CLASS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _read_source_text(root, rel):
    """The text of `rel` under `root` (long-path safe on Windows), or None. Never raises."""
    try:
        path = os.path.abspath(str(Path(root) / rel))
        if os.name == "nt" and len(path) >= _WIN_MAX_PATH:
            path = _extended_path(path)
        return Path(path).read_text(encoding="utf-8-sig", errors="replace")
    except Exception:
        return None


def _python_scope_names(root, rel):
    """(module_names, {def line: names visible from that function's scope chain}) for a Python source
    file, or None when it can't be read/parsed. A function's scope chain = the defs/classes bound in its
    own body and in every ENCLOSING function's body (a class body is not part of its methods' scope)."""
    text = _read_source_text(root, rel)
    if text is None:
        return None
    try:
        tree = ast.parse(text)
    except Exception:
        return None

    def bound_here(fn) -> set:
        out, todo = set(), list(ast.iter_child_nodes(fn))
        while todo:
            x = todo.pop()
            if isinstance(x, _DEF_OR_CLASS):
                out.add(x.name)                  # bound in fn's scope; its own body is a new scope
            elif not isinstance(x, ast.Lambda):
                todo.extend(ast.iter_child_nodes(x))
        return out

    module_names = {st.name for st in tree.body if isinstance(st, _DEF_OR_CLASS)}
    by_line: dict = {}

    def visit(node, inherited: frozenset):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, _DEF_TYPES):
                mine = inherited | bound_here(child)
                for line in {child.lineno, *(d.lineno for d in child.decorator_list)}:
                    by_line[line] = by_line.get(line, frozenset()) | mine
                visit(child, frozenset(mine))
            else:
                visit(child, inherited)

    visit(tree, frozenset())
    return module_names, by_line


def _attach_local_defs(root, nodes, calls) -> int:
    """Attach `local_defs` (sorted names) to each Python caller WITHOUT ast `call_recv` metadata whose
    bare-name call resolves to exactly ONE node, in another file (the only case graded INFERRED — two or
    more candidates are AMBIGUOUS already), or, under precise CALLS, whose graphify-resolved target is in
    another file: the names its scope chain defines, per _python_scope_names — module-level defs/classes
    plus defs nested in the caller and its enclosing functions. _resolve_calls then grades such cross-file
    targets AMBIGUOUS. Parses only the files that need it. Returns how many nodes got the attribute; never
    raises (no attribute -> the old grades).

    Slice S2: a key the typed-call pass made (the caller's `call_typed`) is never a reason to attach — T3#6 is a
    bare-call rule and _resolve_calls never grades a typed key with it. Whether a callee that equals a node key
    reads as that key is decided per CALLER (_keyed_mode_for: a DEEPMAP_PRECISE_CALLS family CSV no longer flips
    it for callers of other families). Under DEEPMAP_CALLS_FAMILY_FILTER the single-candidate count is over the
    targets _resolve_calls keeps (the same family)."""
    try:
        by_key = {n["key"]: n for n in nodes}
        file_of = {k: str(n.get("source_file") or "") for k, n in by_key.items()}
        name_idx = None
        fcf = None
        need: dict = {}
        # R11/R1: with keyed callees possible only a MARKED key is one; both flags off, the old test is kept
        # byte for byte (the default graph does not change). S1 finding 5: decided per caller's family.
        keyed_for = _keyed_mode_for()
        fam = _family_memo() if _calls_family_filter_enabled() else None
        for ck, callee in calls:
            n = by_key.get(ck)
            if n is None or "call_recv" in n or not isinstance(callee, str):
                continue
            rel = file_of[ck]
            if not rel.lower().endswith(".py"):
                continue
            if callee in (n.get("call_typed") or ()):
                continue        # S2: a typed key — the T3#6 local-def rule never grades it (_resolve_calls)
            if callee in by_key and (not keyed_for(rel) or _is_call_key(callee)):   # precise CALLS: a resolved key
                if file_of[callee] != rel:
                    need.setdefault(rel, set()).add(ck)
                continue
            if "." in callee or "::" in callee:
                continue
            if callee in (n.get("call_member") or ()):
                continue        # R11b/G1: a G5 member call — the local-def rule never grades it (_resolve_calls)
            if name_idx is None:
                name_idx = edge_resolve.build_name_index(nodes)
            # only a SINGLE cross-file candidate is graded INFERRED; 2+ are AMBIGUOUS already — counted
            # over the targets _resolve_calls keeps (J3: a bare-only call's unreachable methods are gone,
            # so run _attach_bare_calls first)
            targets = [t for t in name_idx.get(callee, ()) if t != ck]
            if fam is not None and targets:
                targets = _same_family(rel, targets, file_of, fam)   # D0: what _resolve_calls keeps
            if callee in (n.get("call_bare") or ()):
                if fcf is None:
                    fcf = _false_call_filter_on()
                if fcf:
                    targets = [t for t in targets if not _bare_unreachable(n, by_key.get(t))]
            if len(targets) == 1 and file_of.get(targets[0], rel) != rel:
                need.setdefault(rel, set()).add(ck)
        attached = 0
        for rel, keys in need.items():
            scopes = _python_scope_names(root, rel)
            if scopes is None:
                continue
            module_names, by_line = scopes
            for ck in keys:
                try:
                    line = int(by_key[ck].get("start_line") or 0)
                except (TypeError, ValueError):
                    line = 0
                names = set(module_names) | set(by_line.get(line, ()))
                if names:
                    by_key[ck]["local_defs"] = sorted(names)
                    attached += 1
        return attached
    except Exception:  # noqa: BLE001 — a grade refinement must never break a sync
        return 0


# ---- round 8 (J3): which names a graphify caller calls ONLY without a receiver ------------------------
# The graphify path hands the push (caller, NAME) tuples with no receiver information, so the source is
# read — only for the (caller, name) pairs where it matters: a name whose fan-out holds a :Method the
# caller could not reach with a bare call (_bare_unreachable), in a language without an implicit this.
# A pair is marked bare-only when the caller's body shows at least one call of the name and EVERY one is
# bare. Anything that cannot be decided keeps every target: an unreadable file, a span that cannot be
# bounded, no visible call, or a name that MAY be a VALUE holding a bound method / a method value — a bare
# call through such a name really does reach a method (R9/K11; `random.seed = _inst.seed` is the stdlib
# idiom). A value is: a Python parameter / assignment in the caller, in a def around it (a closure) or at
# module level / a `global`; a from-import (at any level, star imports too — and on the graphify path one
# bound under ANOTHER identifier, `import check as base_check`, since the engine names a resolved callee by
# its target) of a name the imported module
# binds as a value, followed through relative imports and re-exports (_PyImportValues — an import from
# outside the project counts only when the method's own module binds the name as a value); a Go
# package-level `var` of the caller's package (its directory; a _test.go var only for a test caller); a
# JS/TS value declared beside the method (`var check = new Api().check`); elsewhere any bare mention not
# followed by `(` in the caller's file (an import line, a `const`, a parameter) — a local alias
# `check = obj.check; check()` is a method call written bare. The ast path (receiver kinds) applies the
# same Python rule to its "bare" verdict (`call_alias`). A call that opens its line after one ending in
# `.` / `?.` / `::` / `->` is a RECEIVER call, never bare (R9/K10: gofmt's only multi-line chain form,
# `return q.` / `\t\tFirst()`; also JS/TS/Rust, and Python inside brackets or after `\`) — the same line
# rule as graphify_extract._call_sites, so the map-time and the push-time rules agree.
_PY_DEF_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
_PY_TEXT_EXTS = (".py", ".pyi")
_TSJS_TEXT_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
_DEF_WORD_BEFORE = re.compile(r"(?:^|[^\w$])(?:def|fn|func|function|class|new|async\s+def)\s*$")
_GO_METHOD_DEF_BEFORE = re.compile(r"(?:^|[^\w$])func\s*\([^()]*\)\s*$")
_MODIFIERS_ONLY = re.compile(r"^\s*(?:(?:export|default|public|private|protected|internal|static|async|override|"
                             r"readonly|abstract|declare|get|set|pub(?:\([^)]*\))?|unsafe|extern)\b\s*)*\*?\s*$")
_COMMENT_LINE = ("//", "/*", "*")
# R9/K10: the operators a receiver chain may END a line with (a float literal `1.` ends a statement instead;
# `..` / `...` are ranges / spreads / ellipses) — graphify_extract._MEMBER_TAIL / _NUM_DOT_TAIL
_MEMBER_TAIL = ("?.", ".", "::", "->")
_NUM_DOT_TAIL = re.compile(r"(?:^|[^\w$.])\d[\d_]*\.$")
_PREV_LINE_LIMIT = 50            # at most this many blank / comment-only lines are looked through
# R9/K11: a Go package-level var (a `var (...)` block's specs too) / a JS-TS value declared beside a method
_GO_PKG_VAR_LINE = re.compile(r"^var\s+([A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)*)")
_GO_PKG_VAR_BLOCK = re.compile(r"^var\s*\(\s*(?://.*)?$")
_GO_PKG_VAR_SPEC = re.compile(r"^[ \t]+([A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)*)")
_WORD_RE = "(?<![\\w$]){}(?![\\w$])"


def _strip_trailing_comment(line: str, py: bool) -> str:
    """`line` without a trailing `#` (Python) or `//` comment that sits outside a string literal (K10)."""
    q = None
    i, n = 0, len(line)
    while i < n:
        ch = line[i]
        if q:
            if ch == "\\":
                i += 2
                continue
            if ch == q:
                q = None
        elif ch in "'\"`":
            q = ch
        elif (py and ch == "#") or (not py and ch == "/" and line[i + 1:i + 2] == "/"):
            return line[:i]
        i += 1
    return line


def _is_comment_only(stripped: str, py: bool) -> bool:
    """A stripped line that holds only a comment (`#`; `//`, `/*`, `*/`, a ` * ` doc-comment line)."""
    if py:
        return stripped.startswith("#")
    return stripped.startswith(("//", "/*", "*/")) or (stripped[:1] == "*" and stripped[1:2] in ("", " ", "\t", "/"))


def _prev_code_line(lines, idx: int, py: bool, limit: int = _PREV_LINE_LIMIT):
    """The code line before 0-based line `idx` of `lines` — blank and comment-only lines skipped (at most
    `limit` back), a trailing comment cut — or None when there is none (R9/K10)."""
    j = idx - 1
    while j >= 0 and idx - j <= limit:
        s = lines[j].strip()
        if s and not _is_comment_only(s, py):
            return _strip_trailing_comment(lines[j], py).rstrip()
        j -= 1
    return None


def _py_string_lines(lines) -> set:
    """1-based numbers of the lines of a Python file that lie INSIDE a multi-line string literal (a
    docstring's second line onwards) — prose there is not code. Empty when the file does not tokenize."""
    import io
    import tokenize
    out: set = set()
    kinds = {tokenize.STRING, getattr(tokenize, "FSTRING_MIDDLE", tokenize.STRING)}
    try:
        for tok in tokenize.generate_tokens(io.StringIO("\n".join(lines) + "\n").readline):
            if tok.type in kinds and tok.end[0] > tok.start[0]:
                out.update(range(tok.start[0] + 1, tok.end[0] + 1))
    except (tokenize.TokenError, SyntaxError, ValueError, IndentationError):
        pass
    return out


def _continues_member(prev) -> bool:
    """True iff `prev` — the previous code line, or a zero-argument callable returning it (read only now) —
    ends in a member operator, so a name that opens the next line is a receiver call / member (R9/K10). An
    explicit `\\` continuation is looked through."""
    if callable(prev):
        try:
            prev = prev()
        except Exception:  # noqa: BLE001 — unreadable context: no continuation
            return False
    if not prev:
        return False
    p = str(prev).rstrip()
    if p.endswith("\\"):
        p = p[:-1].rstrip()
    return p.endswith(_MEMBER_TAIL) and not p.endswith("..") and not _NUM_DOT_TAIL.search(p)


def _read_source_lines(root, rel):
    """`rel`'s lines split on '\\n' only (so line numbers match the engine's), long-path safe; None when
    unreadable. Never raises."""
    try:
        path = os.path.abspath(str(Path(root) / rel))
        if os.name == "nt" and len(path) >= _WIN_MAX_PATH:
            path = _extended_path(path)
        data = Path(path).read_bytes()
    except (OSError, ValueError):
        return None
    return [s.rstrip("\r") for s in data.decode("utf-8-sig", errors="replace").split("\n")]


def _is_ident_char(ch: str) -> bool:
    return bool(ch) and (ch.isalnum() or ch in "_$")


def _text_call_forms(text: str, name: str, prev=None) -> list:
    """'bare' / 'qualified' for each call `name(` on one source line (non-Python callers). A definition
    (`function name(`, `func (r T) name(`, a class member `name(...) {`) and `new name(` are skipped.

    `prev` (R9/K10) is the previous code line (_prev_code_line), or a zero-argument callable returning it,
    read only when a call opens its line: after a line ending in `.` / `?.` / `::` / `->` such a call is
    the continuation of a receiver chain (`return q.` / `\\t\\tFirst()`), so it is 'qualified'."""
    return ["bare" if k == "bare" else "qualified" for k in _text_call_kinds(text, name, prev)]


def _member_tail(s) -> "str | None":
    """The member operator `s` ends with (`::` / `->` / `?.` / `.`), else None."""
    s = str(s or "").rstrip()
    for tail in _MEMBER_TAIL:
        if s.endswith(tail):
            return tail
    return None


def _text_call_kinds(text: str, name: str, prev=None) -> list:
    """_text_call_forms with the qualified form split by its operator (R10/P1): 'bare', 'path' (`a::name(` —
    a scope path, which can reach a free function: `self::helper()`, `crate::util::helper()`, `ns::f()`)
    or 'member' (`x.name(` / `x?.name(` / `p->name(` — a receiver). A call that opens its line takes the
    operator the previous code line ends with (R9/K10)."""
    out: list = []
    i = text.find(name)
    while i >= 0:
        k = i + len(name)
        j = k
        while j < len(text) and text[j] in " \t":
            j += 1
        if not _is_ident_char(text[i - 1] if i else "") and not _is_ident_char(text[k] if k < len(text) else "") \
                and j < len(text) and text[j] == "(":
            before = text[:i].rstrip()
            tail = _member_tail(before)
            if tail is None and not before and prev is not None:
                try:                                  # a lazy `prev` is read once, only here
                    p = prev() if callable(prev) else prev
                except Exception:  # noqa: BLE001 — unreadable context: no continuation
                    p = None
                if _continues_member(p):
                    p = str(p).rstrip()
                    tail = _member_tail(p[:-1] if p.endswith("\\") else p) or "."
            if tail is not None:
                out.append("path" if tail == "::" else "member")
            elif not (_DEF_WORD_BEFORE.search(before) or _GO_METHOD_DEF_BEFORE.search(before)):
                depth, close = 0, None
                for p in range(j, len(text)):
                    if text[p] == "(":
                        depth += 1
                    elif text[p] == ")":
                        depth -= 1
                        if depth == 0:
                            close = p
                            break
                rest = text[close + 1:].lstrip() if close is not None else ""
                member_def = ((_MODIFIERS_ONLY.match(before) and rest[:1] in ("{", ":"))
                              or (before.endswith(("{", ",")) and rest[:1] == "{"))
                if not member_def:
                    out.append("bare")               # (a member definition `name(...) {` is not a call)
        i = text.find(name, i + 1)
    return out


def _text_value_mention(text: str, name: str, prev=None) -> bool:
    """True iff `name` appears on the line as a whole word that is neither called (`name(`) nor a member
    (`x.name`, or — R9/K10 — the name opening its line after a `recv.` line break; `prev` as in
    _text_call_forms): a declaration, an assignment, a parameter, an argument, an import — a VALUE."""
    i = text.find(name)
    while i >= 0:
        k = i + len(name)
        if not _is_ident_char(text[i - 1] if i else "") and not _is_ident_char(text[k] if k < len(text) else ""):
            j = k
            while j < len(text) and text[j] in " \t":
                j += 1
            before = text[:i].rstrip()
            member = before.endswith(_MEMBER_TAIL) or (not before and prev is not None and _continues_member(prev))
            if not (j < len(text) and text[j] == "(") and not member:
                return True
        i = text.find(name, i + 1)
    return False


def _py_value_bindings(tree) -> set:
    """Names a Python module binds as VALUES at module level — any assignment / for / with / walrus /
    except target outside defs, classes and lambdas (a comprehension variable counts too: over-counting
    only keeps edges) — plus every name declared `global` anywhere in it. Imports are judged separately
    (_PyImportValues, R9/K11): an imported name is a value exactly when the imported module binds it as one."""
    names: set = set()
    stack = list(ast.iter_child_nodes(tree))
    while stack:
        x = stack.pop()
        if isinstance(x, _PY_DEF_NODES + (ast.Lambda, ast.Import, ast.ImportFrom)):
            continue
        if isinstance(x, ast.Name) and isinstance(x.ctx, (ast.Store, ast.Del)):
            names.add(x.id)
        elif isinstance(x, ast.ExceptHandler) and x.name:
            names.add(x.name)
        stack.extend(ast.iter_child_nodes(x))
    for x in ast.walk(tree):
        if isinstance(x, ast.Global):
            names.update(x.names)
    return names


def _py_call_forms(scope, name: str) -> list:
    """'bare' / 'qualified' for every ast Call of `name` inside `scope` (a def, a class or a module)."""
    out: list = []
    for x in ast.walk(scope):
        if isinstance(x, ast.Call):
            f = x.func
            if isinstance(f, ast.Name) and f.id == name:
                out.append("bare")
            elif isinstance(f, ast.Attribute) and f.attr == name:
                out.append("qualified")
    return out


def _span_end(lines, start: int, sibling_starts) -> int:
    """Where a non-Python node starting at `start` ends: the line before the next node of the file that is
    NOT nested in it (indented no deeper than its own first line), else the end of the file."""
    def indent(s: str) -> int:
        return len(s) - len(s.lstrip(" \t"))

    own = indent(lines[start - 1])
    for s in sibling_starts:
        if s > start and s <= len(lines) and lines[s - 1].strip() and indent(lines[s - 1]) <= own:
            return s - 1
    return len(lines)


class _PyImportValues:
    """R9/K11 (push time): whether a name a Python file imports may be a VALUE — and so may hold a bound
    method (`check = _default.check` in the imported module). The push-time port of the map-time rule
    (graphify_extract._bare_alias_checker's `import_kind`, W5), over this sync's files and the file system:
      'value'    the imported module binds the name as a value, or re-exports one (followed through
                 relative imports, re-export chains and star imports; past 4 hops, or when a module on the
                 way cannot be read / parsed / has a module-level __getattr__, the answer is 'value' too)
      'def'      a def / class / module there — or not bound there at all (an import cycle binds nothing)
      'external' the module is outside the project (stdlib / third-party)
    The module-level binding table and the absolute-module resolution are graphify_extract's own helpers
    (the same answers the map-time rule gets); when they are unavailable every import is a 'value' — the
    call is kept, never wrongly dropped. Never raises."""

    _HOPS = 4

    def __init__(self, root, nodes):
        self.root = root
        files = set()
        for n in nodes:
            sf = str(n.get("source_file") or "").replace("\\", "/")
            if sf.lower().endswith(_PY_TEXT_EXTS):
                files.add(sf)
        self.files = files
        self._tabs: dict = {}
        self._disk: dict = {}
        self._idx = None
        self._bindings = getattr(graphify_extract, "_py_bindings", None)
        helpers = [getattr(graphify_extract, nm, None) for nm in ("_py_module_index", "_resolve_py_module", "_engine_id")]
        self._resolver = helpers if all(callable(h) for h in helpers) else None

    def _exists(self, rel: str) -> bool:
        if rel in self.files:
            return True
        if rel not in self._disk:
            try:
                path = os.path.abspath(str(Path(self.root) / rel))
                if os.name == "nt" and len(path) >= _WIN_MAX_PATH:
                    path = _extended_path(path)
                self._disk[rel] = os.path.isfile(path)
            except (OSError, ValueError):
                self._disk[rel] = False
        return self._disk[rel]

    @staticmethod
    def collect(imports: dict, node) -> None:
        """Add one ast.ImportFrom `node` to `imports` ({"from", "orig", "stars"} as in table())."""
        for a in node.names:
            if a.name == "*":
                imports["stars"].append((node.module, node.level or 0))
            else:
                imports["from"].setdefault(a.asname or a.name, []).append((node.module, a.name, node.level or 0))
                if a.asname and a.asname != a.name:
                    imports["orig"].setdefault(a.name, []).append((node.module, a.name, node.level or 0, a.asname))

    @staticmethod
    def empty_imports() -> dict:
        return {"from": {}, "orig": {}, "stars": []}

    def seed(self, rel: str, tree, imports: dict) -> None:
        """Register a file the caller already parsed and collected (one walk per file)."""
        if rel not in self._tabs and tree is not None:
            self._tabs[rel] = dict(imports, tree=tree)

    def table(self, rel: str, tree=None):
        """{"from": {bound name: [(module, original name, level)]}, "orig": {ORIGINAL name: [(module, original
        name, level, bound name)]} of the imports bound under another identifier (`import check as
        base_check` — K12's reverse alias), "stars": [(module, level)] — the file's from-imports at ANY level
        (an import inside a def binds there) — and its "tree"} — or None when the file cannot be read or
        parsed. The module-level binding table is `mod(rel)`, built only for a module imported FROM."""
        if rel not in self._tabs:
            tab = None
            try:
                if tree is None:
                    text = _read_source_text(self.root, rel)
                    tree = ast.parse(text) if text is not None else None
                if tree is not None:
                    tab = self.empty_imports()
                    for d in ast.walk(tree):
                        if isinstance(d, ast.ImportFrom):
                            self.collect(tab, d)
                    tab["tree"] = tree
            except Exception:  # noqa: BLE001 — unparseable / unreadable: the caller treats it as a value
                tab = None
            self._tabs[rel] = tab
        return self._tabs[rel]

    def mod(self, rel: str):
        """The module-level binding table of `rel` (graphify_extract._py_bindings), or None when the file
        cannot be read / parsed or the helper is unavailable."""
        tab = self.table(rel)
        if tab is None:
            return None
        if "mod" not in tab:
            try:
                got = self._bindings(tab["tree"]) if callable(self._bindings) else None
            except Exception:  # noqa: BLE001 — the caller treats an unknown table as a value
                got = None
            tab["mod"] = got if isinstance(got, dict) else None
        return tab["mod"]

    def resolve(self, importer: str, module, level: int):
        """The project files a Python import names — [] = outside the project, None = cannot be told."""
        if level:
            parts = [p for p in importer.split("/")[:-1] if p]
            up = level - 1
            if up > len(parts):
                return []
            base = parts[:len(parts) - up]
            stem = "/".join(base + ([p for p in module.split(".") if p] if module else []))
            pre = stem + "/" if stem else ""
            cands = ([stem + ".py", stem + ".pyi"] if module else []) + [pre + "__init__.py", pre + "__init__.pyi"]
            return [c for c in cands if self._exists(c)]
        if not module:
            return []
        if self._resolver is None:
            return None
        index, resolve_mod, engine_id = self._resolver
        if self._idx is None:
            # every package __init__ on the way to a known file counts too (an empty one may have no node)
            dirs: set = set()
            for f in self.files:
                parts = f.split("/")[:-1]
                dirs.update("/".join(parts[:i]) for i in range(1, len(parts) + 1))
            known = set(self.files) | {f"{d}/{init}" for d in dirs for init in ("__init__.py", "__init__.pyi")
                                       if self._exists(f"{d}/{init}")}
            self._idx = index([(f, f) for f in sorted(known)])
        got = resolve_mod(engine_id(module), self._idx, importer, None, lambda k: k)
        if got is None:
            return []
        return [got[1]] if got[0] == "key" else [str(x) for x in got[1]]

    def kind(self, importer: str, module, orig: str, level: int, depth: int = 0, seen: tuple = ()) -> str:
        files = self.resolve(importer, module, level)
        if files is None:
            return "value"
        if not files:
            return "external"
        found: set = set()
        for f in files:
            if (f, orig) in seen:
                found.add("def")
                continue
            seen_f = seen + ((f, orig),)
            b = self.mod(f)
            if not b:
                return "value"
            if orig in b.get("values", ()):
                return "value"
            imports = b.get("imports") or {}
            if orig in imports:
                if depth >= self._HOPS:
                    return "value"
                for m2, o2, l2 in imports[orig]:
                    k = self.kind(f, m2, o2, l2, depth + 1, seen_f)
                    if k == "value":
                        return "value"
                    found.add(k)
                continue
            if orig in b.get("defs", ()) or orig in b.get("mods", ()):
                found.add("def")
                continue
            if b.get("getattr"):
                return "value"
            for m2, l2 in b.get("stars") or ():
                if depth >= self._HOPS or self.kind(f, m2, orig, l2, depth + 1, seen_f) == "value":
                    return "value"
            found.add("def")
        return "external" if found == {"external"} else "def"

    def module_value(self, rel: str, *names) -> bool:
        """True iff Python file `rel` binds one of `names` as a module-level value."""
        if not str(rel).lower().endswith(_PY_TEXT_EXTS):
            return False
        b = self.mod(rel)
        return bool(b) and any(nm in b.get("values", ()) for nm in names)

    def imports_value(self, rel: str, name: str, target_files=(), tree=None, called=None) -> bool:
        """True iff `rel` imports `name` (a from-import at any level, or a star import that may bring it) from
        a module where it may be a value — or from outside the project while a target method's own module
        binds that name as a value (the alias may be what was imported). `called` (graphify-shaped callers:
        the names the caller calls bare) adds the imports of `name` bound under ANOTHER identifier the caller
        calls: the engine names a resolved callee by its target, so `other()` after `from m import name as
        other` reaches the push as `name` (R9/K12's reverse alias). The ast engine records the written name,
        so it passes None."""
        tab = self.table(rel, tree)
        if tab is None:
            return False
        rev = [(m, o, lvl) for m, o, lvl, bound in tab["orig"].get(name, ()) if called and bound in called]
        entries = list(tab["from"].get(name, ())) + rev + [(m, name, lvl) for m, lvl in tab["stars"]]
        for module, orig, lvl in entries:
            k = self.kind(rel, module, orig, lvl)
            if k == "value" or (k == "external" and any(self.module_value(tf, orig, name) for tf in target_files)):
                return True
        return False


_PY_STAR_IMPORT = re.compile(r"\bimport\s*\*")


def _py_name_only_called(text: str, name: str) -> bool:
    """True iff Python source `text` has no star import and every whole-word occurrence of `name` is a call
    (`name(`, also `def name(` / `class name(`) or a member (`.name`). Every way Python BINDS a name — an
    assignment / annotation / for / with / except / walrus / match target, a parameter, an import, `global`
    / `nonlocal`, `del` — writes it NOT followed by `(`, so such a file never binds `name` as a value (K11's
    cheap exact-negative prefilter; anything else — a string, a comment, a keyword argument — only sends the
    pair on to the full parse)."""
    if _PY_STAR_IMPORT.search(text):
        return False
    i = text.find(name)
    n = len(name)
    while i >= 0:
        a = text[i - 1] if i else ""
        k = i + n
        b = text[k] if k < len(text) else ""
        if not (a and (a.isalnum() or a == "_")) and not (b and (b.isalnum() or b == "_")):
            if a != ".":
                while k < len(text) and text[k] in " \t":
                    k += 1
                if not (k < len(text) and text[k] == "("):
                    return False
        i = text.find(name, i + 1)
    return True


def _py_file_facts(tree):
    """ONE traversal of a Python module: ({(line, name): def/class} for every def/class line and decorator
    line, _py_function_spans, the from-imports {"from", "orig", "stars"} (_PyImportValues), and the module
    VALUE names exactly as _py_value_bindings gives them)."""
    from collections import deque
    defs: dict = {}
    spans: list = []
    imps = _PyImportValues.empty_imports()
    values: set = set()
    todo = deque((c, False) for c in ast.iter_child_nodes(tree))   # breadth-first, in ast.walk's order
    while todo:
        x, inner = todo.popleft()                  # inner: inside a def / class / lambda (not module-level)
        if isinstance(x, _PY_DEF_NODES):
            for ln in {x.lineno, *(d.lineno for d in x.decorator_list)}:
                defs.setdefault((ln, x.name), x)
            if not isinstance(x, ast.ClassDef):
                spans.append((x.lineno, getattr(x, "end_lineno", None) or 0, x))
            todo.extend((c, True) for c in ast.iter_child_nodes(x))
            continue
        if isinstance(x, ast.ImportFrom):
            _PyImportValues.collect(imps, x)
            continue
        if isinstance(x, ast.Import):
            continue
        if isinstance(x, ast.Global):
            values.update(x.names)
        elif not inner:
            if isinstance(x, ast.Name) and isinstance(x.ctx, (ast.Store, ast.Del)):
                values.add(x.id)
            elif isinstance(x, ast.ExceptHandler) and x.name:
                values.add(x.name)
        todo.extend((c, inner or isinstance(x, ast.Lambda)) for c in ast.iter_child_nodes(x))
    return defs, spans, imps, values


def _py_function_spans(tree) -> list:
    """[(first line, last line, def)] of every function def in a Python `ast` (one walk per file)."""
    return [(d.lineno, getattr(d, "end_lineno", None) or 0, d) for d in ast.walk(tree)
            if isinstance(d, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _py_enclosing_values(tree, fn, spans=None, bound_cache=None) -> set:
    """The value names (_locally_bound) of every FUNCTION def around `fn` — a closure reads them. `spans`
    (_py_function_spans of `tree`) and `bound_cache` ({id(def): names}) let a caller judging many defs
    of one file walk it once."""
    out: set = set()
    start = getattr(fn, "lineno", 0) or 0
    end = getattr(fn, "end_lineno", None) or start
    for lo, hi, d in (spans if spans is not None else _py_function_spans(tree)):
        if d is not fn and lo <= start and hi >= end:
            if bound_cache is None:
                out |= _locally_bound(d)
            else:
                got = bound_cache.get(id(d))
                if got is None:
                    got = bound_cache[id(d)] = _locally_bound(d)
                out |= got
    return out


def _go_file_vars(lines) -> set:
    """The package-level `var` names of one Go file (`var X`, `var X, Y`, the specs of a `var ( ... )` block)."""
    names: set = set()
    block = False
    for raw in lines or ():
        if block:
            if raw.startswith(")"):
                block = False
                continue
            m = _GO_PKG_VAR_SPEC.match(raw)
        elif _GO_PKG_VAR_BLOCK.match(raw):
            block = True
            continue
        else:
            m = _GO_PKG_VAR_LINE.match(raw)
        if m:
            names.update(x.strip() for x in m.group(1).split(","))
    return names


def _go_package_vars(root, rel: str, cache: dict):
    """The package-level var names of the Go package `rel` belongs to — every `.go` file of its directory; a
    _test.go file only for a _test.go caller (case-sensitive, as the go tool) — or None when the directory
    cannot be listed."""
    d = rel.replace("\\", "/").rsplit("/", 1)[0] if "/" in rel.replace("\\", "/") else ""
    test = rel.endswith("_test.go")
    key = (d, test)
    if key not in cache:
        try:
            path = os.path.abspath(str(Path(root) / d)) if d else os.path.abspath(str(root))
            if os.name == "nt" and len(path) >= _WIN_MAX_PATH:
                path = _extended_path(path)
            entries = sorted(os.listdir(path))
        except (OSError, ValueError):
            cache[key] = None
            return None
        names: set = set()
        for fn in entries:
            if fn.lower().endswith(".go") and (test or not fn.endswith("_test.go")):
                names |= _go_file_vars(_read_source_lines(root, f"{d}/{fn}" if d else fn))
        cache[key] = names
    return cache[key]


def _js_declares_value(lines, name: str) -> bool:
    """True iff a JS/TS file declares a VALUE `name` at the start of a statement (`const` / `let` / `var`,
    a destructuring, `export`/`declare`) or assigns `exports.name` / `module.exports.name` — graphify_extract's
    rule for the alias beside the method (`var check = new Api().check`)."""
    decl = re.compile(r"^\s*(?:export\s+)?(?:declare\s+)?(?:const|let|var)\s+(?:[\[{][^\]}]*)?"
                      + _WORD_RE.format(re.escape(name))
                      + "|(?:^|[^\\w$.])(?:module\\.)?exports\\." + re.escape(name) + r"\s*=")
    return any(decl.search(x) for x in (lines or ()) if name in x)


def _ast_bare_drops(caller, callee: str, target) -> bool:
    """True iff the ast path's "bare" receiver verdict drops `target` for a bare `callee(` in `caller` — the
    round-7 rule (another file's method) or J3's (a same-file method of another class); see recv_verdict."""
    if not caller or not target:
        return False
    same = str(target.get("source_file") or "") == str(caller.get("source_file") or "")
    v = _receiver_verdict("bare", callee, str(target.get("name") or ""), same, ())
    return v == "drop" or (v == "base" and _bare_unreachable(caller, target))


def _attach_bare_calls(root, nodes, calls) -> int:
    """Attach `call_bare` (sorted callee names) to each caller WITHOUT ast `call_recv` metadata, in a
    language without an implicit this (_bare_call_lang), for every name whose fan-out holds a :Method the
    caller could not reach bare (_bare_unreachable) and which its body calls ONLY without a receiver and
    cannot hold as a value — see the block comment above. _resolve_calls then drops those methods;
    _attach_local_defs (run after this) counts the targets that remain. An ast caller (`call_recv`) whose
    "bare" call of a name would lose a method that way gets `call_alias` instead when the name may hold a
    bound method (R9/K11): _resolve_calls then keeps those targets. Returns the number of (caller, name)
    pairs marked bare-only; a no-op under DEEPMAP_FALSE_CALL_FILTER=0. Reads only the files that need it.
    Never raises (nothing attached -> the round-7 resolution)."""
    try:
        if not _false_call_filter_on():
            return 0
        by_key = {n["key"]: n for n in nodes}
        name_idx = None
        need: dict = {}                                   # caller key -> {name: files of the methods at stake}
        alias_need: dict = {}                             # ast caller key -> {name: files of the methods at stake}
        ast_seen: set = set()                             # (ast caller, name) pairs already judged
        droppable: dict = {}                              # name -> the targets a "bare" verdict can ever drop
        for ck, callee in calls:
            n = by_key.get(ck)
            if n is None or not isinstance(callee, str) or not callee:
                continue
            if "call_recv" in n:                          # the ast path: its receiver kinds say how it was reached
                if (ck, callee) in ast_seen:
                    continue
                ast_seen.add((ck, callee))
                kinds = (n.get("call_recv") or {}).get(callee)
                if not kinds or "bare" not in kinds or not str(n.get("source_file") or "").lower().endswith(_PY_TEXT_EXTS):
                    continue
                if name_idx is None:
                    name_idx = edge_resolve.build_name_index(nodes)
                if callee not in droppable:
                    # _ast_bare_drops is True only for a target reached through its LAST `.` / `::` segment
                    # (another file's `Owner.m`) or for a :Method — an exact prefilter over the name's fan-out
                    droppable[callee] = [t for t in name_idx.get(callee, ()) if t in by_key and (
                        by_key[t].get("label") == "Method" or "." in str(by_key[t].get("name") or "")
                        or "::" in str(by_key[t].get("name") or ""))]
                at_stake = {str(by_key[t].get("source_file") or "") for t in droppable[callee]
                            if t != ck and _ast_bare_drops(n, callee, by_key[t])}
                if at_stake:
                    alias_need.setdefault(ck, {}).setdefault(callee, set()).update(at_stake)
                continue
            if (_is_call_key(callee) and callee in by_key) or "." in callee or "::" in callee:
                continue                                  # a marked key (R11/R1; any flags, S2) / a qualified name
            if not _bare_call_lang(n.get("source_file")):
                continue
            if name_idx is None:
                name_idx = edge_resolve.build_name_index(nodes)
            at_stake = {str(by_key[t].get("source_file") or "") for t in name_idx.get(callee, ())
                        if t != ck and t in by_key and _bare_unreachable(n, by_key[t])}
            if at_stake:
                need.setdefault(ck, {}).setdefault(callee, set()).update(at_stake)
        if not need and not alias_need:
            return 0
        files: dict = {}                                  # rel -> lines | None
        py: dict = {}                                     # rel -> (tree, {(line, name): def}, bindings) | None
        py_strings: dict = {}                             # rel -> lines inside a multi-line string (text path)
        go_vars: dict = {}                                # (package dir, test caller) -> var names | None
        imp_box: list = [None]                            # the _PyImportValues, built on first need
        spans_of: dict = {}                               # rel -> _py_function_spans (enclosing-def lookups)
        bound_cache: dict = {}                            # id(def) -> _locally_bound(def)
        starts: dict = {}                                 # rel -> sorted non-Module node start lines
        for n in nodes:
            if n.get("label") != "Module":
                try:
                    starts.setdefault(str(n.get("source_file") or ""), set()).add(int(n.get("start_line") or 0))
                except (TypeError, ValueError):
                    pass
        starts = {k: sorted(v) for k, v in starts.items()}

        def lines_of(rel):
            if rel not in files:
                files[rel] = _read_source_lines(root, rel) if rel else None
            return files[rel]

        def imports_ctx():
            if imp_box[0] is None:
                imp_box[0] = _PyImportValues(root, nodes)
            return imp_box[0]

        def py_of(rel):
            """(tree, {(line, name): def}, module values) of a Python file — ONE walk also collects its
            function spans and from-imports (K11) — or None."""
            if rel not in py:
                py[rel] = None
                lines = lines_of(rel)
                if lines and len(lines) <= 200_000:
                    try:
                        tree = ast.parse("\n".join(lines))
                        defs, spans, imps, module_values = _py_file_facts(tree)
                        py[rel] = (tree, defs, module_values)
                        spans_of[rel] = spans
                        imports_ctx().seed(rel, tree, imps)
                    except (SyntaxError, ValueError, RecursionError, MemoryError):
                        py[rel] = None
            return py[rel]

        def py_scope(n, rel):
            """(tree, scope, module values) of a Python caller — scope the module or its def — or None."""
            got = py_of(rel)
            if got is None:
                return None
            tree, defs, module_values = got
            if n.get("label") == "Module":
                return tree, tree, module_values
            try:
                start = int(n.get("start_line") or 0)
            except (TypeError, ValueError):
                return None
            scope = defs.get((start, str(n.get("name") or "").rsplit(".", 1)[-1]))
            return (tree, scope, module_values) if scope is not None else None

        def py_may_hold_value(rel, tree, scope, module_values, name, at_stake, scope_values, reverse) -> bool:
            """K11: the bare `name` may be a value holding a bound method in this Python caller's scope.
            `reverse` (graphify-shaped callers): also through an import alias of `name` the scope calls."""
            if name in module_values:
                return True
            if scope is not tree:
                if scope_values[0] is None:
                    if rel not in spans_of:
                        spans_of[rel] = _py_function_spans(tree)
                    scope_values[0] = _locally_bound(scope) | _py_enclosing_values(tree, scope, spans_of[rel],
                                                                                   bound_cache)
                if name in scope_values[0]:
                    return True
            called = None
            if reverse:
                if scope_values[1] is None:
                    scope_values[1] = {x.func.id for x in ast.walk(scope)
                                       if isinstance(x, ast.Call) and isinstance(x.func, ast.Name)}
                called = scope_values[1]
            return imports_ctx().imports_value(rel, name, at_stake, tree, called)

        marked = 0
        for ck, names in need.items():
            n = by_key[ck]
            rel = str(n.get("source_file") or "")
            lines = lines_of(rel)
            if not lines or len(lines) > 200_000:
                continue
            try:
                start = int(n.get("start_line") or 0)
            except (TypeError, ValueError):
                continue
            is_module = n.get("label") == "Module"
            judged: dict = {}                             # name -> list of call forms, or None (undecidable)
            got = py_scope(n, rel) if rel.lower().endswith(_PY_TEXT_EXTS) else None
            if got is not None:
                tree, scope, module_values = got
                scope_values: list = [None, None]   # (scope values, names it calls bare)
                for name, at_stake in names.items():
                    forms = _py_call_forms(scope, name)
                    if forms and all(f == "bare" for f in forms) and py_may_hold_value(
                            rel, tree, scope, module_values, name, at_stake, scope_values, True):
                        forms = None                      # K11: may hold a bound method -> undecidable
                    judged[name] = forms
            else:                                         # not Python, or its def was not found: the text
                if is_module:
                    lo, hi = 0, len(lines) if len(lines) <= _BARE_SCAN_MAX_LINES else -1
                elif 1 <= start <= len(lines):
                    end = _span_end(lines, start, starts.get(rel, ()))
                    lo, hi = (start - 1, end) if end - start + 1 <= _BARE_SCAN_MAX_LINES else (0, -1)
                else:
                    lo, hi = 0, -1
                if hi < 0:
                    continue
                py_text = rel.lower().endswith(_PY_TEXT_EXTS)

                def prev_at(i, rel=rel, lines=lines, py_text=py_text):
                    """K10: the code line before 0-based line i (None inside a Python multi-line string)."""
                    if py_text:
                        if rel not in py_strings:
                            py_strings[rel] = _py_string_lines(lines)
                        if i + 1 in py_strings[rel]:
                            return None
                    return _prev_code_line(lines, i, py_text)

                low = rel.lower()
                for name, at_stake in names.items():
                    if any(_text_value_mention(t, name, prev=lambda i=i: prev_at(i)) for i, t in enumerate(lines)
                           if name in t and not t.lstrip().startswith(_COMMENT_LINE)):
                        judged[name] = None               # used as a value somewhere in the file
                        continue
                    forms = [f for i in range(lo, hi) if name in lines[i]
                             for f in _text_call_forms(lines[i], name, prev=lambda i=i: prev_at(i))]
                    if forms and all(f == "bare" for f in forms):
                        if low.endswith(".go"):           # K11: a package-level var of the caller's package
                            pkg = _go_package_vars(root, rel, go_vars)
                            if pkg is None or name in pkg:
                                forms = None
                        elif low.endswith(_TSJS_TEXT_EXTS):   # K11: a value declared beside the method
                            if any(_js_declares_value(lines_of(tf), name) for tf in sorted(at_stake)):
                                forms = None
                    judged[name] = forms
            bare_only = sorted(nm for nm, forms in judged.items() if forms and all(f == "bare" for f in forms))
            if bare_only:
                n["call_bare"] = bare_only
                marked += len(bare_only)
        only_called: dict = {}                            # (rel, name) -> _py_name_only_called
        texts: dict = {}                                  # rel -> the file's text (joined lines)
        for ck, names in alias_need.items():              # the ast path (K11): its "bare" verdict
            n = by_key[ck]
            rel = str(n.get("source_file") or "")
            lines = lines_of(rel)
            if lines:                                     # a name the file only ever calls is no value: no parse
                for nm in names:
                    if (rel, nm) not in only_called:
                        if rel not in texts:
                            texts[rel] = "\n".join(lines)
                        only_called[(rel, nm)] = _py_name_only_called(texts[rel], nm)
                names = {nm: st for nm, st in names.items() if not only_called[(rel, nm)]}
                if not names:
                    continue
            got = py_scope(n, rel)
            if got is None:
                alias = sorted(names)                     # the source cannot be read again: keep the methods
            else:
                tree, scope, module_values = got
                scope_values = [None, None]
                alias = sorted(nm for nm, at_stake in names.items()
                               if py_may_hold_value(rel, tree, scope, module_values, nm, at_stake, scope_values,
                                                    False))
            if alias:
                n["call_alias"] = alias
        return marked
    except Exception:  # noqa: BLE001 — a correction must never break a sync
        return 0


# ---- round 10 (P1): under DEEPMAP_PRECISE_CALLS a RECEIVER call is never keyed to a module-level function --
# graphify binds a call by NAME, file-first: `obj.shell_complete(ctx, incomplete)` in click's
# ShellComplete.get_completions became a call to the module-level FUNCTION shell_completion.shell_complete of
# the same file, and `self._stream.isatty()` one to _compat.isatty. Precise CALLS turned those keys into
# firm EXTRACTED edges and dropped the real, polymorphic targets (Command/Group/Parameter.shell_complete, …)
# that the name fan-out keeps with the flag off (Phase M, click: 2 of 153 EXTRACTED edges into module-level
# functions). A receiver never reaches a module-level def — except through the module itself (`mod.f()`)
# or a scope path (`self::f()` / `ns::f()`) — so a keyed call to a :Function with no class scope (an
# undotted name) whose every call site in the caller is a RECEIVER call falls back to exactly what the flag
# off does: the (caller, name) tuple, fanned out by name and graded AMBIGUOUS / INFERRED. A caller with BOTH
# a bare (or module / path) site and a receiver site keeps the key (the bare site binds that def) AND gets
# the name fan-out for the receiver site. Decided from the source:
#   * Python (ast; same file or another): a site is `bare` (`f(`), `module` (`mod.f(` with `mod` an import
#     of the target's module or one of its packages — click's `shell_completion.f(`) or a receiver (`self.`,
#     `obj.`, `a.b.`, `f().`, `super().`, and an import that names ANOTHER module: `os.isatty(`);
#   * any other language (text): same-file targets only — `x.f(` / `x?.f(` / `p->f(` is a receiver, `a::f(`
#     a path, and JS/TS `exports.f(` / `module.exports.f(` the file's own f. Another file's function is
#     reached through a package / namespace receiver (`pkg.F()`, `ns.f()`), which text cannot tell from an
#     object, so those keys stay.
# Undecidable keeps the key (today's precise result): an unreadable / unparseable file, a def not found at
# the node's line, or no call site of the name in the caller (graphify merges some callers' calls). The
# fallback tuples are placed FIRST, so any keyed edge the caller also has keeps its own grade. Flag off:
# nothing is read and the calls are returned unchanged.
# The MAP-time half (graphify_extract._receiver_unkeyer, W5) already hands a receiver-only call over as its
# name, so on a graphify graph this pass mostly adds what the map time cannot: the receiver site's fan-out
# for a caller that also calls the name bare (the map time keeps that key and nothing else), and the
# stricter module test above. Both halves only ever un-key; neither re-keys what the other left as a name.
_P1_SCAN_MAX_LINES = _BARE_SCAN_MAX_LINES


def _module_level_function(node) -> bool:
    """A :Function with no class scope — an undotted name (a method is `Owner.m`, a Rust item `a::b`)."""
    name = str((node or {}).get("name") or "")
    return (node or {}).get("label") == "Function" and bool(name) and "." not in name and "::" not in name


def _module_names_of(source_file) -> set:
    """The names a module receiver can use for `source_file`: its stem (the package's name for an
    `__init__`) and every directory of its path (a package that re-exports it)."""
    parts = [p for p in str(source_file or "").replace("\\", "/").split("/") if p]
    if not parts:
        return set()
    stem = parts[-1].rsplit(".", 1)[0]
    names = set(parts[:-1])
    if stem != "__init__":
        names.add(stem)
    return names


def _py_module_level_nodes(tree, has_node=None) -> list:
    """R11/R2: the ast nodes a Python MODULE evaluates in its own scope — every module-level statement and
    expression, nested if / try / with / for / while blocks and lambdas included, plus the decorators,
    default values, annotations, bases and keywords of a def / class (they run where the statement is,
    _scope_parts) — but never the BODY of a def or a class: those calls are that def's / class's, not the
    module's. A :Module node's CALLS are exactly these (DEEPMAP_GRAPHIFY_CALLS_PLUS gives it its
    module-level statements; a lambda is no node, so its calls are the module's too). R11c/C2: with
    `has_node` (_py_def_keys), also the body of every def / class the graph has NO node for — CALLS_PLUS
    gives those calls to the :Module as well. ONE rule for both halves: graphify_extract._py_module_level_nodes."""
    return graphify_extract._py_module_level_nodes(tree, has_node)


def _py_module_scope_facts(tree) -> tuple:
    """(nodes, aliases, local) of a Python module's OWN scope for P1's site kinds (R11/R2): its module-level
    nodes (_py_module_level_nodes), the imports among them (an import inside a def binds a name of that def,
    not the module's), and the names those nodes bind as values. At module level an import and its FALL-BACK
    bind ONE variable — `try: import x as y` / `except ImportError: y = None` (or `y = Stub()` in that except,
    or a `y = None` anywhere) is the import, so it is never taken for a shadowing value — but any other
    rebinding (`import helpers as h` / `h = Wrapper()`) is a value again (R11c/C3). The first of
    _py_module_call_scopes."""
    return _py_module_call_scopes(tree)[0]


def _py_module_call_scopes(tree, has_node=None) -> list:
    """[(nodes, aliases, local)] — every scope whose calls a Python :Module owns: its own (_py_module_scope_facts)
    and, with `has_node` (_py_def_keys), one per def / class the graph has NO node for (R11c/C2: graphify's
    merged ids — if/else twin defs, `def config` beside `class Config`, `_main` beside `main`, a duplicate def —
    whose calls DEEPMAP_GRAPHIFY_CALLS_PLUS attributes to the :Module), each with its own imports and values over
    the module's. ONE rule for map and push time: graphify_extract._py_module_scopes."""
    return graphify_extract._py_module_scopes(tree, has_node)


def _py_def_keys(nodes) -> dict:
    """R11c/C2: {source file: {(start line, bare name)}} of the Function / Method / Class nodes — the keys of
    _calls_plus's own has-a-node test (graphify_extract._py_def_has_node): a def / class statement has a graph
    node when one of its file's nodes starts at its def line under its name."""
    out: dict = {}
    for n in nodes:
        if n.get("label") in ("Function", "Method", "Class"):
            try:
                out.setdefault(str(n.get("source_file") or ""), set()).add(
                    (int(n.get("start_line") or 0), str(n.get("name") or "").rsplit(".", 1)[-1]))
            except (TypeError, ValueError):
                continue
    return out


def _py_site_kinds(scope, wanted, aliases: dict, local: set, nodes=None) -> dict:
    """{name: [(kind, module dotted or None)]} for every Call of a `wanted` name in `scope` (or in `nodes`,
    an iterable of ast nodes — a :Module's own, R11/R2): 'bare', 'mod' (the receiver is an import-bound
    name — the dotted import target is given) or 'member'."""
    out: dict = {}
    for x in (ast.walk(scope) if nodes is None else nodes):
        if not isinstance(x, ast.Call):
            continue
        f = x.func
        if isinstance(f, ast.Name):
            if f.id in wanted:
                out.setdefault(f.id, []).append(("bare", None))
        elif isinstance(f, ast.Attribute) and f.attr in wanted:
            kind = _receiver_kind(f, aliases, local)
            if kind.startswith("mod:"):
                out.setdefault(f.attr, []).append(("mod", kind[4:]))
            else:
                out.setdefault(f.attr, []).append(("member", None))
    return out


def _unkey_receiver_calls(root, nodes, calls):
    """(calls, n): when callees can be keys (_precise_calls_enabled: DEEPMAP_PRECISE_CALLS, or R10/P5's keyed
    DEEPMAP_GRAPHIFY_CALLS_PLUS additions), the (caller, key) tuples bound to a module-level :Function that
    the caller only reaches through a receiver become (caller, name) — the flag-off tuple — and a caller
    that reaches it both ways also gets the (caller, name) tuple; see the block comment above. `n` = the
    tuples changed. Both flags off (or nothing to judge): the SAME list and 0. Never raises (on any error
    the calls are returned unchanged)."""
    if not _precise_calls_enabled():
        return calls, 0
    try:
        by_key = {n["key"]: n for n in nodes}
        want: dict = {}                                   # caller key -> {target key: target name}
        for ck, callee in calls:
            # R11/R1: only a MARKED key is judged — a name equal to some node's key is already a name
            if not _is_call_key(callee) or callee == ck or callee not in by_key or ck not in by_key:
                continue
            caller, target = by_key[ck], by_key[callee]
            if "call_recv" in caller or not _module_level_function(target):
                continue
            want.setdefault(ck, {})[callee] = str(target["name"])
        if not want:
            return calls, 0
        files: dict = {}                                  # rel -> lines | None
        pyfacts: dict = {}                                # rel -> (tree, defs, aliases) | None
        starts: dict = {}                                 # rel -> sorted non-Module node start lines
        for n in nodes:
            if n.get("label") != "Module":
                try:
                    starts.setdefault(str(n.get("source_file") or ""), set()).add(int(n.get("start_line") or 0))
                except (TypeError, ValueError):
                    pass
        starts = {k: sorted(v) for k, v in starts.items()}

        def lines_of(rel):
            if rel not in files:
                files[rel] = _read_source_lines(root, rel) if rel else None
            return files[rel]

        def py_facts(rel):
            if rel not in pyfacts:
                pyfacts[rel] = None
                lines = lines_of(rel)
                if lines and len(lines) <= 200_000:
                    try:
                        tree = ast.parse("\n".join(lines))
                        pyfacts[rel] = (tree, _py_file_facts(tree)[0], _import_aliases(tree))
                    except (SyntaxError, ValueError, RecursionError, MemoryError):
                        pyfacts[rel] = None
            return pyfacts[rel]

        mod_scopes: dict = {}                             # rel -> _py_module_call_scopes
        def_keys: list = []                               # [rel -> the def keys of its nodes] (R11c/C2)
        rebound_of: dict = {}                             # rel -> imported names the module rebinds (R11c/C3)

        def py_sites(n, rel, wanted):
            got = py_facts(rel)
            if got is None:
                return None
            tree, defs, aliases = got
            if n.get("label") == "Module":
                # R11/R2: a :Module's calls are its MODULE-LEVEL statements only — a receiver call inside
                # one of its defs (`engine_cli.main()` in def main) is that def's, and must not turn the
                # module's own bare `main()` into 'both' (a fan-out to every `main` in the repo)
                # R11c/C2: plus the bodies of its defs / classes that have no graph node (their calls are the
                # module's — _calls_plus), each read with its own imports and values (R11c/C3)
                if rel not in mod_scopes:
                    if not def_keys:
                        def_keys.append(_py_def_keys(nodes))
                    mod_scopes[rel] = _py_module_call_scopes(
                        tree, graphify_extract._py_def_has_node(def_keys[0].get(rel, set())))
                out: dict = {}
                for m_nodes, m_aliases, m_local in mod_scopes[rel]:
                    for nm, got in _py_site_kinds(tree, wanted, m_aliases, m_local, nodes=m_nodes).items():
                        out.setdefault(nm, []).extend(got)
                return out
            else:
                try:
                    start = int(n.get("start_line") or 0)
                except (TypeError, ValueError):
                    return None
                scope = defs.get((start, str(n.get("name") or "").rsplit(".", 1)[-1]))
                if scope is None:
                    return None
            local = _locally_bound(scope)
            # R11c/C3: a global the module rebinds as a value (`h = Wrapper()` after `import helpers as h`) is a
            # value in this def too, unless the def imports the name itself (graphify_extract._py_rebound_globals)
            if rel not in rebound_of:
                rebound_of[rel] = graphify_extract._py_rebound_globals(tree, aliases)
            if rebound_of[rel]:
                local = local | (rebound_of[rel] - set(_import_aliases(scope)))
            return _py_site_kinds(scope, wanted, aliases, local)

        def text_sites(n, rel, wanted):
            lines = lines_of(rel)
            if not lines or len(lines) > 200_000:
                return None
            if n.get("label") == "Module":
                lo, hi = (0, len(lines)) if len(lines) <= _P1_SCAN_MAX_LINES else (0, -1)
            else:
                try:
                    start = int(n.get("start_line") or 0)
                except (TypeError, ValueError):
                    return None
                if not 1 <= start <= len(lines):
                    return None
                end = _span_end(lines, start, starts.get(rel, ()))
                lo, hi = (start - 1, end) if end - start + 1 <= _P1_SCAN_MAX_LINES else (0, -1)
            if hi < 0:
                return None
            js = rel.lower().endswith(_TSJS_TEXT_EXTS)
            out: dict = {}
            for i in range(lo, hi):
                line = lines[i]
                if line.lstrip().startswith(_COMMENT_LINE):
                    continue
                for name in wanted:
                    if name in line:
                        kinds = _text_call_kinds(line, name, prev=lambda i=i: _prev_code_line(lines, i, False))
                        # CommonJS `exports.f(` / `module.exports.f(` names the file's own f (as the map-time
                        # rule, graphify_extract._receiver_unkeyer, reads it): a direct reference, not a receiver
                        n_self = len(re.findall(r"(?<![\w$.])(?:module\s*\.\s*)?exports\s*\.\s*"
                                                + re.escape(name) + r"\s*\(", line)) if js else 0
                        for kind in kinds:
                            if kind == "member" and n_self > 0:
                                kind, n_self = "path", n_self - 1
                            out.setdefault(name, []).append((kind, None))
            return out

        member_in: dict = {}                              # (rel, name) -> the file has a `.name(` / `->name(`

        def may_be_member(rel, name) -> bool:
            """A cheap prefilter: a receiver site is written `.name(` / `?.name(` / `->name(` (whitespace and line
            breaks allowed), so a file without that text has none — and needs no parse. (A comment or a `\\`
            continuation between the dot and the name is not looked through: such a rare site keeps the key,
            today's result.)"""
            if (rel, name) not in member_in:
                lines = lines_of(rel)
                text = "\n".join(lines) if lines else ""
                member_in[(rel, name)] = (not lines) or bool(re.search(
                    r"(?:\.|->)\s*" + re.escape(name) + r"\s*\(", text))
            return member_in[(rel, name)]

        fallback: list = []                               # (caller, name) tuples, placed first
        unkeyed: set = set()                              # (caller, key) tuples replaced by their name
        seen_fb: set = set()
        for ck, targets in want.items():
            n = by_key[ck]
            rel = str(n.get("source_file") or "")
            py = rel.lower().endswith(_PY_TEXT_EXTS)
            if not py:
                targets = {t: nm for t, nm in targets.items() if str(by_key[t].get("source_file") or "") == rel}
            targets = {t: nm for t, nm in targets.items() if may_be_member(rel, nm)}
            if not targets:
                continue                                  # nothing a receiver could have written
            wanted = set(targets.values())
            sites = py_sites(n, rel, wanted) if py else text_sites(n, rel, wanted)
            if sites is None:
                continue
            for t, name in targets.items():
                kinds = sites.get(name) or []
                if not kinds:
                    continue                              # no visible site: keep graphify's key
                tfile = str(by_key[t].get("source_file") or "")
                if not py and tfile != rel:
                    continue
                mods = _module_names_of(tfile) if py else set()
                direct = receiver = False
                for kind, dotted in kinds:
                    if kind in ("bare", "path"):
                        direct = True
                    elif kind == "mod" and tfile != rel and str(dotted or "").lstrip(".").split(".")[-1] in mods:
                        direct = True                     # `mod.f(` through the target's module / package
                    else:
                        receiver = True
                if not receiver:
                    continue
                if not direct:
                    unkeyed.add((ck, t))
                if (ck, name) not in seen_fb:
                    seen_fb.add((ck, name))
                    fallback.append((ck, name))
        if not fallback:
            return calls, 0
        # a plain name tuple that merely equals an un-keyed (caller, key) pair is a different call: it stays
        rest = [c for c in calls if not (_is_call_key(c[1]) and (c[0], c[1]) in unkeyed)]
        return fallback + rest, len(fallback)
    except Exception:  # noqa: BLE001 — a precision refinement must never break a sync
        return calls, 0


# ---- S2: typed-receiver call linking at push time (build/typed_calls.py; receiver-calls design 2.8 / 6.1) -----
# Step 2 of the push passes, between _unkey_receiver_calls (P1 never sees a typed key) and _attach_bare_calls:
# typed_calls.link types the receiver of each member call / constructor from the caller's own SOURCE and keys the
# call to the exact node (a CallKey) with a grade in the caller's private `call_typed`; its untyped-receiver audit
# names engine picks to demote in `call_weak`. The passes after it honour both per tuple (_resolve_calls,
# _attach_local_defs, _attach_bare_calls, _attach_local_def_calls). DEEPMAP_TYPED_CALLS / DEEPMAP_CALLS_AUDIT off
# (today's default): the SAME list comes back, no node is touched, nothing is printed.
#
# What the extraction hands the pass. While the pass will run, _rebuild_code attaches a _TypedNote to the _Extraction
# it returns (`.typed_note`, never `.info`): the RESOLVED engine (typed_calls attributes nested-scope call sites the
# way that engine does) and, on the graphify path, the graphify_extract._Source the extraction read the files
# through, so no file is read or parsed twice (design 2.1; one open costs ~10 ms under Windows' scanner). The source
# is shared only when graphify_extract.extract_with_report accepts `source=` (else the pass reads the files itself —
# the same result, slower). _EXTRACTION_NOTES maps the root to a WEAK reference to that note, so the note lives
# exactly as long as its extraction: _sync_pass (and the offline measurement mirror, slice S0's offline_calls, which
# calls _rebuild_code and then _typed_calls_link(root, nodes, calls) the same way) keeps its extraction until the
# step, while a caller that only unpacks it (enrich's enumeration) lets it go at once — a parsed reader never stays
# alive for a pass that does not come. _typed_calls_link takes the note ONCE and releases its reader when the pass is
# done; _sync_pass drops the registry entry when it ends. A note is used only for the nodes it was taken from (all
# its node keys still present — the recognizers only add); without one the pass falls back to typed_calls.link's own
# defaults (a fresh reader, graphify's attribution — safe on the ast path too).
_EXTRACTION_NOTES: dict = {}      # note key (the root) -> weakref.ref(_TypedNote)


class _TypedNote:
    """What one extraction hands the typed-call pass (see the block comment above). Owned by its _Extraction."""
    __slots__ = ("engine", "source", "keys", "__weakref__")

    def __init__(self, engine, source, keys):
        self.engine, self.source, self.keys = engine, source, keys


def _typed_calls_module():
    """build/typed_calls.py, or None when it cannot be imported (the step is then a no-op). Never raises."""
    try:
        import typed_calls
        return typed_calls
    except Exception:  # noqa: BLE001 — a missing / broken module never breaks a sync
        return None


def _typed_pass_enabled(tc=None) -> bool:
    """Will typed_calls.link do anything for this environment (typed_calls.enabled)? Never raises."""
    tc = tc if tc is not None else _typed_calls_module()
    if tc is None or not callable(getattr(tc, "link", None)):
        return False
    fn = getattr(tc, "enabled", None)
    if not callable(fn):
        return True               # no cheap check: link decides (and returns its input when off)
    try:
        return bool(fn())
    except Exception:  # noqa: BLE001
        return False


def _note_key(root) -> str:
    try:
        return os.path.normcase(os.path.abspath(str(root)))
    except Exception:  # noqa: BLE001
        return str(root)


def _typed_extraction_source(workspace_root):
    """The graphify_extract._Source to hand graphify_extract.extract_with_report (and then the typed pass), or None:
    only while the typed pass will run AND extract_with_report takes `source=` (graphify_extract shares map_graph's
    reader through it). Never raises."""
    try:
        if not _typed_pass_enabled():
            return None
        fn = getattr(graphify_extract, "extract_with_report", None)
        cls = getattr(graphify_extract, "_Source", None)
        if not callable(fn) or cls is None or "source" not in _callable_params(fn)[0]:
            return None
        return cls(Path(workspace_root).resolve())
    except Exception:  # noqa: BLE001
        return None


def _note_extraction(workspace_root, engine, nodes, source=None) -> "_TypedNote | None":
    """_rebuild_code's note for the typed pass (see the block comment above) — returned for the _Extraction to own,
    registered weakly under the root; None (and no entry) while the pass is off. Never raises."""
    try:
        key = _note_key(workspace_root)
        _EXTRACTION_NOTES.pop(key, None)
        if not _typed_pass_enabled():
            return None
        import weakref
        note = _TypedNote(engine, source, frozenset(n.get("key") for n in (nodes or ()) if isinstance(n, dict)))
        _EXTRACTION_NOTES[key] = weakref.ref(note)
        return note
    except Exception:  # noqa: BLE001
        return None


def _take_extraction_note(root, nodes) -> "_TypedNote | None":
    """The note of `root`'s latest extraction (its registry entry removed: one-shot), while that extraction is alive
    and every node key it recorded is still in `nodes`; else None. Never raises."""
    ref = _EXTRACTION_NOTES.pop(_note_key(root), None)
    note = ref() if callable(ref) else None
    if not isinstance(note, _TypedNote):
        return None
    try:
        live = {n.get("key") for n in nodes if isinstance(n, dict)}
        return note if note.keys <= live else None
    except Exception:  # noqa: BLE001
        return None


def _drop_extraction_note(root) -> None:
    """Forget any note left for `root` (the end of a sync pass). Never raises."""
    try:
        _EXTRACTION_NOTES.pop(_note_key(root), None)
    except Exception:  # noqa: BLE001
        pass


def _callable_params(fn) -> tuple:
    """(set of parameter names, takes **kwargs) of `fn`; (set(), False) when unreadable. Never raises."""
    try:
        import inspect
        params = inspect.signature(fn).parameters
        return set(params), any(p.kind is p.VAR_KEYWORD for p in params.values())
    except Exception:  # noqa: BLE001
        return set(), False


def _typed_calls_link(root, nodes, calls, *, engine=None, source=None):
    """(calls, stats) — design 2.8 step 2: `typed_calls.link(root, nodes, calls, ...)` with the test-caller rule
    (sync._is_test_path), the RESOLVED engine and the extraction's own reader (the note _rebuild_code left —
    `engine` / `source` override it; handed over as typed_calls' `sources` {rel: reader} map for every graph file
    plus `source=` for any other, each only when link takes it), plus ONE stderr line (typed_calls.describe). The
    pass off, or typed_calls not
    importable: the SAME list and stats None (nothing read, nothing printed). Never raises: on any error the input
    list comes back with stats {"error": ...} (typed_calls.link itself rolls back every node attribute it set)."""
    note = _take_extraction_note(root, nodes)
    eng = engine if engine is not None else (note.engine if note is not None else None)
    src = source if source is not None else (note.source if note is not None else None)
    if note is not None:
        note.source = None        # the extraction no longer pins the reader: it dies with this step
    tc = _typed_calls_module()
    if not _typed_pass_enabled(tc):
        return calls, None
    try:
        names, var_kw = _callable_params(tc.link)
        kwargs = {}
        if "is_test" in names or var_kw:
            kwargs["is_test"] = _is_test_path
        if eng in ("graphify", "ast") and ("engine" in names or var_kw):
            kwargs["engine"] = eng
        if src is not None:
            if "sources" in names:
                # the core's contract: {rel: the reader the extraction read (and parsed) that file with} — one shared
                # graphify_extract._Source holds every graph file it read (stats sources_shared counts them)
                kwargs["sources"] = {str(n["source_file"]): src for n in nodes
                                     if isinstance(n, dict) and n.get("source_file")}
            if "source" in names or var_kw:
                kwargs["source"] = src              # ... and the same reader for any file outside that map
        result = tc.link(root, nodes, calls, **kwargs)
        out, stats = result if isinstance(result, tuple) and len(result) == 2 else (None, None)
        if not isinstance(out, list):
            raise TypeError(f"typed_calls.link returned {type(result).__name__}, not (calls, stats)")
        stats = stats if isinstance(stats, dict) else {}
    except Exception as exc:  # noqa: BLE001 — a correction pass must never break a sync
        out, stats = calls, {"error": f"{type(exc).__name__}: {exc}"[:500]}
    try:
        line = tc.describe(stats) if callable(getattr(tc, "describe", None)) else None
    except Exception:  # noqa: BLE001
        line = None
    if not line:
        line = (f"typed call linking skipped ({stats['error']}) — the CALLS are unchanged" if stats.get("error")
                else "typed call linking ran")
    print(f"[deepmap sync] {line}", file=sys.stderr)
    return out, stats


def _call_linking_block(stats) -> "dict | None":
    """The graph-build manifest's `call_linking` block from typed_calls.link's stats (design 6.1): JSON values only,
    `notes` capped; for a run that failed (`error`) only version / engine / error / notes / seconds — its counters
    are the skeleton's (typed_calls: "stamp nothing else from such a run"). None when there are no stats."""
    if not isinstance(stats, dict) or not stats:
        return None
    try:
        keep = stats
        if stats.get("error"):
            keep = {k: stats[k] for k in ("version", "engine", "error", "notes", "seconds") if k in stats}
        block = json.loads(json.dumps(keep, sort_keys=True, default=str))
        if isinstance(block.get("notes"), list) and len(block["notes"]) > 20:
            block["notes"] = block["notes"][:20] + [f"... {len(stats['notes']) - 20} more"]
        return block
    except (TypeError, ValueError):
        return None


# ---- DEEPMAP_LOCAL_DEF_CALLS (default OFF): a bare Python call binds the caller's OWN definition -------
# Python resolves a bare name lexically: the innermost enclosing FUNCTION scope that binds it (a class body
# is visible only to code written directly in it, never to its methods), then the module's globals, then
# builtins. So a bare `check(...)` in a module that defines `def check` at module level — and neither imports
# nor rebinds that name — can only ever call THAT check. The name fan-out in _resolve_calls ignores this:
# live on this repo (round 8) 252,048 of 262,024 CALLS edges (96%) bound such a call to ANOTHER file's
# same-name Function/Class, and every tests/*_gates.py `check` had in-degree ~1,249 in the graph-weighted
# arm. Under the flag, _attach_local_def_calls reads each Python caller's source and records, per callee
# name, the definition(s) the language binds (`call_local` {name: [keys]} — private metadata, never pushed);
# _resolve_calls then keeps only those targets. A (caller, name) pair is decided only when EVERY call site of
# the name in the caller is bare and binds a definition in the caller's own file:
#   * a def/class in an enclosing FUNCTION scope (a nested def, a closure); else, for a site written directly
#     in a class body, the class's own def when it precedes the site (a class body looks names up as it
#     runs); else the module-level def/class (for code that runs at import time, only when it precedes);
#   * a nested def the engine made no node for (graphify makes none) binds NOTHING in the graph: the edge
#     is dropped, never moved to another file (T3#6 only graded that cross-file edge AMBIGUOUS).
# Anything undecidable keeps today's resolution: the file imports the name anywhere (from-import, alias,
# a try/except fallback) or has a star import; the file imports the name under ANOTHER identifier
# (`from pkg.base import helper as base_helper` — R9/K12: the engine names a resolved callee by its TARGET,
# so `base_helper(2)` reaches the push as `helper`, one tuple with a bare `helper(1)`; `import a.b` counts
# `b` too); the name is a VALUE in a scope on the way (an assignment,
# parameter, for/with/except/match target, walrus, del) or is declared global/nonlocal anywhere in the
# file; a qualified call `x.name(` in the same caller; no visible call site; an unreadable or unparseable
# file; a module-level definition the graph has no node for; a caller the engine credits with a callee its
# source never calls (graphify gives `_main` and `main` ONE id and merges their calls — the source of that
# node is then not the def found at its line). A module that does not define the name at all keeps
# today's behaviour.
# Python only (.py/.pyi). JS/TS are deliberately NOT judged: a classic (non-module) browser script's
# top-level `function check()` is a GLOBAL shared by every script on the page — a later file's declaration
# replaces it — so "the caller's own file defines it" does not guarantee the binding there, and sync has no
# JS/TS parser to tell a module from a script (only line heuristics).
# Grades: only the edge SET shrinks; the targets that remain are graded by the existing rules — on the
# name path one same-file target is INFERRED and several (conditional defs) AMBIGUOUS; under
# DEEPMAP_PRECISE_CALLS a graphify key the language rules out (another file, or a :Method a bare call cannot
# reach) is re-bound to the local def with F3b's same-file grade (EXTRACTED for one, AMBIGUOUS for several).
# Interplay: DEEPMAP_FALSE_CALL_FILTER (J3, default ON) still runs, on the targets that remain; =0 does NOT
# veto this separate opt-in (it restores the round-7 J3 rules only). With this flag unset nothing is
# attached and _resolve_calls ignores the metadata: byte-identical under every other flag
# (tests/local_def_calls_gates.py).
_LOCAL_DEF_EXTS = (".py", ".pyi")
_PY_FUNC_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
_PY_SCOPES = _PY_FUNC_SCOPES + (ast.ClassDef,)
_PY_COMPS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
_PY_LEAVES = (ast.expr_context, ast.boolop, ast.operator, ast.unaryop, ast.cmpop)   # no children, no names
_MATCH_NAMED = tuple(t for t in (getattr(ast, "MatchAs", None), getattr(ast, "MatchStar", None)) if t)
_MATCH_MAPPING = getattr(ast, "MatchMapping", None)
_UNDECIDED = "undecided"


def _local_def_calls_enabled() -> bool:
    """DEEPMAP_LOCAL_DEF_CALLS (default OFF) — see the block comment above."""
    return os.environ.get("DEEPMAP_LOCAL_DEF_CALLS", "").strip().lower() in {"1", "true", "yes", "on"}


def _scope_parts(node):
    """([children evaluated in the ENCLOSING scope], [children evaluated in the node's own scope]) of a
    def / async def / class / lambda: decorators, defaults, annotations and bases run where the statement is."""
    if isinstance(node, ast.Lambda):
        a = node.args
        return [*a.defaults, *(d for d in a.kw_defaults if d is not None)], [node.body]
    if isinstance(node, ast.ClassDef):
        return [*node.decorator_list, *node.bases, *node.keywords], list(node.body)
    a = node.args
    outer = [*node.decorator_list, *a.defaults, *(d for d in a.kw_defaults if d is not None)]
    outer += [x.annotation for x in (*a.posonlyargs, *a.args, *a.kwonlyargs, a.vararg, a.kwarg)
              if x is not None and x.annotation is not None]
    if node.returns is not None:
        outer.append(node.returns)
    return outer, list(node.body)


def _first_line(d) -> int:
    """The first line of a def/class statement (its first decorator's, when decorated)."""
    return min([d.lineno, *(x.lineno for x in getattr(d, "decorator_list", ()))])


class _PyModuleScopes:
    """The lexical scopes of ONE parsed Python module, for _attach_local_def_calls: where each call site's
    callee name is looked up from, which definitions and values each scope binds, and which names the file
    imports / declares global or nonlocal anywhere. Iterative (no recursion on deep files)."""

    def __init__(self, tree):
        self.tree = tree
        self.parent: dict = {}      # id(def/class/lambda) -> the scope its statement runs in
        self.site_scope: dict = {}  # id(ast.Call) -> the scope its callee name is looked up from
        self.in_comp: set = set()   # id(ast.Call) of a site inside a comprehension (never sees a class body)
        self.calls: list = []       # every ast.Call, in walk order
        self.calls_in: dict = {}    # id(def/class) -> the ast.Calls in its subtree (what ast.walk(def) yields)
        self.defs_at: dict = {}     # (line, name) -> def/class node, for its `def`/`class` and decorator lines
        self.imported: set = set()  # names ANY import statement of the file binds
        self.alias_of: dict = {}    # R9/K12: ORIGINAL name -> the OTHER identifiers imports bind it under
        self.star = False           # the file has a `from x import *`
        self.declared: set = set()  # names declared `global` or `nonlocal` anywhere in the file
        self._binds: dict = {}
        stack = [(tree, tree, False, ())]         # (node, lookup scope, in a comprehension, owning defs)
        push = stack.append
        while stack:
            node, scope, comp, owners = stack.pop()
            if isinstance(node, ast.Call):
                self.site_scope[id(node)] = scope
                self.calls.append(node)
                for o in owners:
                    self.calls_in.setdefault(id(o), []).append(node)
                if comp:
                    self.in_comp.add(id(node))
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for a in node.names:
                    if a.name == "*":
                        self.star = True
                    else:
                        bound = a.asname or a.name.split(".")[0]
                        self.imported.add(bound)
                        orig = a.name.rsplit(".", 1)[-1]
                        if orig != bound:           # `from m import helper as base_helper`, `import a.b`
                            self.alias_of.setdefault(orig, set()).add(bound)
            elif isinstance(node, (ast.Global, ast.Nonlocal)):
                self.declared.update(node.names)
            if isinstance(node, _PY_SCOPES):
                self.parent[id(node)] = scope
                inner_owners = owners
                if not isinstance(node, ast.Lambda):
                    inner_owners = owners + (node,)
                    for ln in {node.lineno, *(d.lineno for d in node.decorator_list)}:
                        self.defs_at.setdefault((ln, node.name), node)
                outer, inner = _scope_parts(node)
                for x in outer:
                    push((x, scope, comp, inner_owners))
                for x in inner:
                    push((x, node, False, inner_owners))
            elif isinstance(node, _PY_COMPS):
                gens = node.generators
                if gens:                        # the first iterable runs in the enclosing scope itself
                    push((gens[0].iter, scope, comp, owners))
                rest = [x for x in ast.iter_child_nodes(node) if not (gens and x is gens[0])]
                if gens:
                    rest += [x for x in ast.iter_child_nodes(gens[0]) if x is not gens[0].iter]
                for x in rest:
                    push((x, scope, True, owners))
            else:
                for x in ast.iter_child_nodes(node):
                    if not isinstance(x, _PY_LEAVES):
                        push((x, scope, comp, owners))

    def binds(self, scope):
        """(defs {name: [def/class nodes]}, value names) bound IN `scope` itself — a nested scope's body is
        not walked, but its decorators/defaults/bases are (they run here). Parameters are values; so is
        every assignment / for / with / except / match target, walrus, del and import. Over-counting a
        value only keeps today's edges."""
        got = self._binds.get(id(scope))
        if got is not None:
            return got
        defs: dict = {}
        values: set = set()
        if scope is self.tree:
            todo = list(scope.body)
        else:
            todo = list(_scope_parts(scope)[1])
            if isinstance(scope, _PY_FUNC_SCOPES):
                a = scope.args
                values.update(x.arg for x in (*a.posonlyargs, *a.args, *a.kwonlyargs, a.vararg, a.kwarg)
                              if x is not None)
        while todo:
            x = todo.pop()
            if isinstance(x, _PY_SCOPES):
                if not isinstance(x, ast.Lambda):
                    defs.setdefault(x.name, []).append(x)
                todo.extend(_scope_parts(x)[0])
                continue
            if isinstance(x, ast.Name) and isinstance(x.ctx, (ast.Store, ast.Del)):
                values.add(x.id)
            elif isinstance(x, ast.ExceptHandler) and x.name:
                values.add(x.name)
            elif isinstance(x, (ast.Import, ast.ImportFrom)):
                values.update(a.asname or a.name.split(".")[0] for a in x.names if a.name != "*")
            elif _MATCH_NAMED and isinstance(x, _MATCH_NAMED) and getattr(x, "name", None):
                values.add(x.name)
            elif _MATCH_MAPPING is not None and isinstance(x, _MATCH_MAPPING) and getattr(x, "rest", None):
                values.add(x.rest)
            todo.extend(c for c in ast.iter_child_nodes(x) if not isinstance(c, _PY_LEAVES))
        self._binds[id(scope)] = (defs, values)
        return defs, values

    def _chain(self, call):
        """The scopes a site's name lookup passes, innermost first, WITHOUT the module; None if unknown."""
        chain, s = [], self.site_scope.get(id(call))
        while s is not None and s is not self.tree:
            chain.append(s)
            s = self.parent.get(id(s))
        return chain if s is self.tree else None

    def resolve(self, name, call):
        """What the bare call `name(...)` at `call` binds in THIS file: ("module" | "nested", [def/class
        nodes]); None when no scope of the file binds the name (a builtin, another module's name); or
        _UNDECIDED (see the block comment)."""
        chain = self._chain(call)
        if chain is None or name in self.declared:
            return _UNDECIDED
        line = int(getattr(call, "lineno", 0) or 0)
        for i, s in enumerate(chain):
            if isinstance(s, ast.ClassDef) and (i > 0 or id(call) in self.in_comp):
                continue                        # a class body is visible only to code written directly in it
            defs, values = self.binds(s)
            if name in values:
                return _UNDECIDED
            if name in defs:
                if isinstance(s, ast.ClassDef) and not all(_first_line(d) < line for d in defs[name]):
                    return _UNDECIDED           # a class body looks the name up while it runs
                return ("nested", defs[name])
        if self.star or name in self.imported:
            return _UNDECIDED
        defs, values = self.binds(self.tree)
        if name in values:
            return _UNDECIDED
        if name not in defs:
            return None
        at_import = not any(isinstance(s, _PY_FUNC_SCOPES) for s in chain)
        if at_import and not all(_first_line(d) < line for d in defs[name]):
            return _UNDECIDED                   # module code runs top to bottom: the def is not bound yet
        return ("module", defs[name])

    def _runs_at_import(self, call) -> bool:
        """True iff `call` is module code: no function or lambda encloses it (a class body may)."""
        chain = self._chain(call)
        return chain is not None and not any(isinstance(s, _PY_FUNC_SCOPES) for s in chain)

    def _class_body_site(self, call, class_node) -> bool:
        """True iff `call` runs while `class_node`'s statement runs: in its body (not inside a function or
        lambda nested in it) or in its decorators/bases."""
        s, in_fn = self.site_scope.get(id(call)), False
        while s is not None:
            if s is class_node:
                return not in_fn
            if isinstance(s, _PY_FUNC_SCOPES):
                in_fn = True
            if s is self.tree:
                break
            s = self.parent.get(id(s))
        return True

    def sites_of(self, node):
        """{callee name: ["bare" | "qualified", ...] as (form, ast.Call) pairs} for the call sites caller
        `node` owns — a def's whole body (the ast engine's attribution; graphify's is a subset), a class's
        own body, a module's import-time code — or None when its definition is not found at its line."""
        label = node.get("label")
        if label == "Module":
            calls = [c for c in self.calls if self._runs_at_import(c)]
        else:
            try:
                line = int(node.get("start_line") or 0)
            except (TypeError, ValueError):
                return None
            d = self.defs_at.get((line, str(node.get("name") or "").rsplit(".", 1)[-1]))
            if d is None:
                return None
            calls = self.calls_in.get(id(d), [])
            if isinstance(d, ast.ClassDef):
                calls = [c for c in calls if self._class_body_site(c, d)]
        by: dict = {}
        for c in calls:
            f = c.func
            if isinstance(f, ast.Name):
                by.setdefault(f.id, []).append(("bare", c))
            elif isinstance(f, ast.Attribute):
                by.setdefault(f.attr, []).append(("qualified", c))
        return by

    def decide(self, name, by, cands):
        """The keys (of `cands`, [(start_line, key)] same-file nodes named `name`) that the caller's bare
        calls of `name` bind, or None to keep today's resolution. A caller that also calls a name some
        import of the file binds `name` under (R9/K12: `base_helper(2)` after `from pkg.base import helper
        as base_helper`) is undecided: the engine names a resolved callee by its TARGET, so that call
        reaches the push as `helper` too — one tuple with the bare `helper(1)` the source cannot split."""
        forms = by.get(name)
        if not forms or any(f != "bare" for f, _c in forms):
            return None
        if any(a in by for a in self.alias_of.get(name, ())):
            return None
        mod_lines: set = set()
        nested_lines: set = set()
        for _f, c in forms:
            got = self.resolve(name, c)
            if got is None or got == _UNDECIDED:
                return None
            kind, defs = got
            lines = {ln for d in defs for ln in (d.lineno, *(x.lineno for x in getattr(d, "decorator_list", ())))}
            (mod_lines if kind == "module" else nested_lines).update(lines)
        mod_keys = {k for ln, k in cands if ln in mod_lines}
        if mod_lines and not mod_keys:
            return None                         # the graph has no node for the module-level def
        keep = mod_keys | {k for ln, k in cands if ln in nested_lines}
        return [k for _ln, k in cands if k in keep]


def _py_module_scopes(root, rel):
    """_PyModuleScopes for `rel` under `root`, or None when it can't be read or parsed. Never raises."""
    text = _read_source_text(root, rel)
    if text is None:
        return None
    try:
        return _PyModuleScopes(ast.parse(text))
    except (SyntaxError, ValueError, RecursionError, MemoryError, TypeError):
        return None


def _attach_local_def_calls(root, nodes, calls) -> int:
    """Under DEEPMAP_LOCAL_DEF_CALLS=1: attach `call_local` {callee name: [node keys]} to each Python caller
    for every name the language binds to a definition in the caller's own file (see the block comment
    above); _resolve_calls then keeps only those targets. Judges only the (caller, name) pairs where it can
    change something — a name fan-out with a target in another file or with several targets, or (precise
    CALLS) a graphify key in another file or on a :Method — and reads only the files those callers are in.
    Returns the number of (caller, name) pairs decided. Flag off: returns 0 and attaches nothing. Never
    raises (nothing attached -> today's resolution)."""
    try:
        if not _local_def_calls_enabled():
            return 0
        by_key = {n["key"]: n for n in nodes}
        file_of = {k: str(n.get("source_file") or "") for k, n in by_key.items()}
        name_idx = None
        need: dict = {}                                   # caller key -> names to judge
        called: dict = {}                                 # caller key -> last segment of EVERY callee it has
        for ck, callee in calls:
            n = by_key.get(ck)
            if n is None or not isinstance(callee, str) or not callee:
                continue
            rel = file_of[ck]
            if not rel.lower().endswith(_LOCAL_DEF_EXTS):
                continue
            if callee in (n.get("call_typed") or ()):
                continue        # S2: a typed key is never re-bound (_resolve_calls) — nothing to judge or count
            if _is_call_key(callee) and callee in by_key:   # precise CALLS: a resolved key (marked — R11/R1)
                t = by_key[callee]
                nm = str(t.get("name") or "").rsplit(".", 1)[-1]
                called.setdefault(ck, set()).add(nm)
                if nm and t.get("label") != "Module" and (file_of[callee] != rel or t.get("label") == "Method"):
                    need.setdefault(ck, set()).add(nm)
                continue
            called.setdefault(ck, set()).add(callee.rsplit(".", 1)[-1])
            if "." in callee or "::" in callee:
                continue
            if name_idx is None:
                name_idx = edge_resolve.build_name_index(nodes)
            targets = [t for t in name_idx.get(callee, ()) if t != ck]
            if len(targets) > 1 or any(file_of.get(t) != rel for t in targets):
                need.setdefault(ck, set()).add(callee)
        if not need:
            return 0
        cands: dict = {}                                  # (file, last name segment) -> [(line, key)]
        for n in nodes:
            if n.get("label") == "Module":
                continue
            try:
                line = int(n.get("start_line") or 0)
            except (TypeError, ValueError):
                continue
            nm = str(n.get("name") or "").rsplit(".", 1)[-1]
            cands.setdefault((file_of[n["key"]], nm), []).append((line, n["key"]))
        parsed: dict = {}
        decided = 0
        for ck, names in need.items():
            n = by_key[ck]
            rel = file_of[ck]
            if rel not in parsed:
                parsed[rel] = _py_module_scopes(root, rel)
            scopes = parsed[rel]
            if scopes is None:
                continue
            by = scopes.sites_of(n)
            if not by or any(nm not in by for nm in called.get(ck, ())):
                # a callee with no call site in this def's source: the engine's node is not (only) this
                # def — graphify gives `_main` and `main` ONE id and merges their calls — so judge nothing
                continue
            out: dict = {}
            for name in sorted(names):
                keys = scopes.decide(name, by, cands.get((rel, name), ()))
                if keys is not None:
                    out[name] = keys
            if out:
                n["call_local"] = out
                decided += len(out)
        return decided
    except Exception:  # noqa: BLE001 — an opt-in refinement must never break a sync
        return 0


def _write_calls(tx, anchors, edges_by_anchor, labels_of, *, prune: bool = True) -> None:
    """Inside `tx`: prune the prior outgoing :CALLS of `anchors`, then re-create theirs.

    The prune is what makes a call removed from source disappear on re-sync (MERGE-only left phantom
    edges that corrupted deepmap_impact / the graph-weighted arm — bug-hunt HIGH); on a fresh build it
    deletes nothing. It is scoped to this sync's nodes, never the whole graph, and matches them by key
    alone (one scan per transaction) so a same-key node under another label is pruned too.
    `prune=False` only writes (an oversized anchor, already pruned in batches — see push_to_neo4j)."""
    if prune:
        tx.run("MATCH (a)-[r:CALLS]->() WHERE a.key IN $keys DELETE r", keys=anchors)
    by_labels: dict = {}
    for a in anchors:
        for b, conf in edges_by_anchor.get(a, ()):
            for la in labels_of[a]:
                for lb in labels_of[b]:
                    by_labels.setdefault((la, lb), []).append({"a": a, "b": b, "c": conf})
    for (la, lb), rows in by_labels.items():
        for chunk in _batches(rows):
            tx.run(f"UNWIND $rows AS row MATCH (a:{la} {{key:row.a}}) MATCH (b:{lb} {{key:row.b}}) "
                   "MERGE (a)-[r:CALLS]->(b) SET r.confidence=row.c", rows=chunk)


def _edge_groups(keys, rows_of, budget) -> list:
    """Split `keys` (in order) into the anchor groups written one transaction each: a group is closed
    BEFORE the anchor that would take it past `budget` edge rows, so no group exceeds the budget unless
    a single anchor alone does — and such an anchor is a group of its own (never merged into a
    neighbour's; push_to_neo4j writes it in bounded pieces). Always at least one group (a sync whose
    nodes have no edges still prunes their old ones)."""
    groups, group, rows = [], [], 0
    for k in keys:
        n = rows_of(k)
        if group and rows + n > budget:
            groups.append(group)
            group, rows = [], 0
        group.append(k)
        rows += n
    if group or not groups:
        groups.append(group)
    return groups


# What a group's transaction DELETES counts against its budget too (round 3, review finding #1). Round 2
# sized a group by the rows it would create, but its prunes delete every edge its anchors already have —
# so the first re-sync after the edge set SHRANK (v0.27.4's cross-language `index` :IMPORTS fan-out,
# 100k+ rows, re-resolved by W5 to a few hundred; a recognizer switched off with DEEPMAP_RECOGNIZERS; a
# precise-CALLS switch) put all those deletes (~2.2 KB of transaction state each, measured) into one
# transaction: a MemoryPoolOutOfMemoryError that rolls back, so every later sync repeated it. The
# existing anchored degree is read once up front, matched exactly as the prunes match (by key alone,
# every label; CALLS + forward types at the source, reversed types at the target).
_SAFE_EDGE_TYPE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


def _anchored_edge_types():
    """(forward, reversed) — the edge types a group prunes, split as they are ANCHORED: :CALLS and every
    forward typed type at the source, edge_resolve.REVERSED_EDGE_TYPES (CASCADES) at the target. Only
    whitelisted identifiers (they are interpolated as relationship types)."""
    vocab = sorted(t for t in edge_resolve.ALLOWED_EDGE_TYPES if _SAFE_EDGE_TYPE.match(str(t)))
    rev = [t for t in vocab if t in edge_resolve.REVERSED_EDGE_TYPES]
    fwd = ["CALLS"] + [t for t in vocab if t not in edge_resolve.REVERSED_EDGE_TYPES and t != "CALLS"]
    return fwd, rev


def _existing_edge_counts(driver, keys) -> dict:
    """{anchor key: its EXISTING anchored edges} for `keys` (anchors with none are absent) — exactly the
    edges the rebuild's prunes will delete. One read over the whole key set; COUNT { } subqueries (Neo4j
    5.3+ / Cypher 25), else pattern comprehensions (4.4-5.x) — only a SYNTAX error moves to the fallback
    (in a fresh session); any other database error propagates (fail loud: a guessed 0 would bring the
    unbounded delete back)."""
    keys = list(dict.fromkeys(keys or ()))
    if not keys:
        return {}
    fwd, rev = _anchored_edge_types()
    f, r = "|".join(fwd), "|".join(rev)
    head, tail = "MATCH (a) WHERE a.key IN $keys WITH a.key AS k, ", " AS d WHERE d > 0 RETURN k, d"
    forms = (head + f"COUNT {{ (a)-[:{f}]->() }}" + (f" + COUNT {{ ()-[:{r}]->(a) }}" if r else "") + tail,
             head + f"size([(a)-[x:{f}]->() | x])" + (f" + size([(a)<-[y:{r}]-() | y])" if r else "") + tail)
    rows = []
    for i, cypher in enumerate(forms):
        try:
            with driver.session() as s:
                res = s.run(cypher, keys=keys)
                try:
                    rows = list(res) if res is not None else []
                except TypeError:            # a result object that is not iterable: nothing to count
                    rows = []
            break
        except Exception as exc:  # noqa: BLE001 — re-raised unless it is a syntax error with a fallback left
            if i == len(forms) - 1 or not _is_syntax_error(exc):
                raise
    counts: dict = {}
    for row in rows:
        try:
            k, d = row["k"], int(row["d"] or 0)
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        if d > 0:
            counts[k] = counts.get(k, 0) + d     # one key under two labels: both nodes are pruned
    return counts


def _rewrite_oversized_anchor(driver, session, key, calls_rows, typed_rows, labels_of, resolver,
                              budget) -> None:
    """An anchor whose existing + new edges alone exceed the budget: prune its old edges in batched
    inner transactions of at most `budget` rows (CALL { } IN TRANSACTIONS), then write its new rows in
    transactions of at most `budget` rows each. Not one atomic swap — a reader may briefly see this ONE
    anchor with part of its edges, and a sync killed in between leaves it partial until the next sync —
    but bounded: one oversized transaction would fail on a memory-pool error and roll back, and every
    later sync would repeat it."""
    fwd, rev = _anchored_edge_types()
    step = max(1, min(_DELETE_REL_BATCH, budget))
    _run_batched(driver, f"MATCH (a)-[r:{'|'.join(fwd)}]->() WHERE a.key IN $keys", "r", "DELETE r",
                 step, keys=[key])
    if rev:
        _run_batched(driver, f"MATCH ()-[r:{'|'.join(rev)}]->(a) WHERE a.key IN $keys", "r", "DELETE r",
                     step, keys=[key])
    items = [(True, row) for row in calls_rows] + [(False, row) for row in typed_rows]
    for i in range(0, len(items), max(1, budget)):
        chunk = items[i:i + max(1, budget)]
        with session.begin_transaction() as tx:
            calls = [row for is_call, row in chunk if is_call]
            if calls:
                _write_calls(tx, [key], {key: calls}, labels_of, prune=False)
            typed = [row for is_call, row in chunk if not is_call]
            if typed:
                edge_resolve.write_edge_rows(tx, typed, resolver)
            tx.commit()


def push_to_neo4j(driver, nodes, calls, imports, typed_edges=None):
    """Upsert `nodes`, then atomically rebuild their outgoing :CALLS, typed/runtime edges and :IMPORTS.
    Returns (nodes pushed, DISTINCT :CALLS edges) — CALLS-only, a SUBSET of `deepmap status`'s
    all-relationship count. With no nodes there is nothing to upsert and nothing to prune.

    Track C's framework/runtime edges and :IMPORTS are resolved up front by edge_resolve (name
    resolution over the live nodes, extractor-independent) into rows anchored at their emitted src
    (normally the declaring node; a reversed CASCADES row at its principal), and every transaction
    prunes the WHOLE edge vocabulary of its anchors before re-creating theirs (anti-phantom: a relation
    removed from source — or a type whose recognizer was disabled — leaves no stale edge behind), so the
    groups together rebuild exactly what one transaction would.

    A group's budget counts BOTH sides of its swap: the rows it writes and the existing edges its
    prunes delete (_existing_edge_counts, read once before any write). An anchor that alone exceeds the
    budget is rewritten in bounded pieces (_rewrite_oversized_anchor) instead of one transaction."""
    if not nodes:
        return 0, 0
    edges = _resolve_calls(nodes, calls)
    labels_of: dict = {}                     # key -> its label(s); normally exactly one
    for n in nodes:
        labels = labels_of.setdefault(n["key"], [])
        if n["label"] not in labels:
            labels.append(n["label"])
    edges_by_anchor: dict = {}
    for (a, b), conf in edges.items():
        edges_by_anchor.setdefault(a, []).append((b, conf))
    live_keys = list(labels_of)
    resolver = edge_resolve.Resolver(nodes)
    typed_by_anchor = edge_resolve.rows_by_anchor(edge_resolve.edge_rows(
        typed_edges or [], imports or [], resolver, live_keys, set(live_keys)))
    existing = _existing_edge_counts(driver, live_keys)   # before ANY write: a failed read changes nothing
    budget = max(1, int(_EDGE_TX_MAX_ROWS))

    def rows_of(k):
        return existing.get(k, 0) + len(edges_by_anchor.get(k, ())) + len(typed_by_anchor.get(k, ()))

    groups = _edge_groups(live_keys, rows_of, budget)
    with driver.session() as s:
        _upsert_nodes(s, nodes)
        for anchors in groups:
            if len(anchors) == 1 and rows_of(anchors[0]) > budget:
                k = anchors[0]
                _rewrite_oversized_anchor(driver, s, k, edges_by_anchor.get(k, []),
                                          typed_by_anchor.get(k, []), labels_of, resolver, budget)
                continue
            with s.begin_transaction() as tx:
                _write_calls(tx, anchors, edges_by_anchor, labels_of)
                edge_resolve.rewrite_anchored_edges(
                    tx, anchors, [r for k in anchors for r in typed_by_anchor.get(k, ())], resolver)
                tx.commit()
    return len(nodes), len(edges)


# ---- embedding reuse across a re-key (round 7, T7d-7) ------------------------------------------------
# ast keys carry the line (`rel::name:line`), so a line shift re-keys a node: the new key has no embedding
# and generate_embeddings re-embedded it — every function below an added import, on every edit. (Name-based
# ast keys would change graph identity and the STAGE-0 key, so the key stays.) Instead, a node whose key is
# new takes the embedding of an existing node with the same label, file and name whose `_embed_hash` equals
# the hash generate_embeddings will compute for the new node (embeddings.content_hash of its composed text —
# same text, same vector). generate_embeddings re-checks every hash from the node's actual properties, so a
# copy can only ever skip work, never keep a wrong vector. Runs after the push and BEFORE the stale prune
# deletes the old node. A graph with name-based keys (graphify) rarely re-keys, and then only by a move.
_SAFE_LABEL = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


def _reuse_rekeyed_embeddings(driver, nodes) -> int:
    """Copy embeddings onto re-keyed nodes (see above). Returns how many were copied. Best-effort: any
    error returns 0 and generate_embeddings embeds them as before; never raises."""
    try:
        want: dict = {}
        for n in nodes or ():
            summ = n.get("body_summary")
            if not summ or not isinstance(n.get("key"), str):
                continue
            text = embeddings.compose_text({"name": n.get("name"), "body_summary": summ})
            want[n["key"]] = (str(n.get("label") or ""), str(n.get("source_file") or ""),
                              str(n.get("name") or ""), embeddings.content_hash(text))
        if not want:
            return 0
        files = sorted({sf for _l, sf, _n, _h in want.values() if sf})
        with driver.session() as s:
            rows = list(s.run(
                "MATCH (n) WHERE n.key IS NOT NULL AND n.embedding IS NOT NULL AND n._embed_hash IS NOT NULL "
                "AND n.source_file IN $files "
                "RETURN n.key AS k, labels(n) AS ls, n.source_file AS sf, n.name AS nm, n._embed_hash AS h",
                files=files) or [])
        have: dict = {}
        donors: dict = {}
        for r in rows:
            k, h = r["k"], r["h"]
            have[k] = h
            for lb in (r["ls"] or ()):
                donors.setdefault((str(lb), str(r["sf"] or ""), str(r["nm"] or ""), h), k)
        pairs: dict = {}
        for k, (lb, sf, nm, h) in want.items():
            if have.get(k) == h:
                continue                              # this key is already embedded with this text
            d = donors.get((lb, sf, nm, h))
            if d is not None and d != k and _SAFE_LABEL.match(lb):
                pairs.setdefault(lb, []).append({"new": k, "old": d})
        copied = 0
        if pairs:
            with driver.session() as s:
                for lb, prs in pairs.items():
                    for chunk in _batches(prs):
                        s.run(f"UNWIND $rows AS row MATCH (x:{lb} {{key:row.new}}) MATCH (o:{lb} {{key:row.old}}) "
                              "SET x.embedding=o.embedding, x._embed_hash=o._embed_hash, "
                              "x._content_hash=o._embed_hash", rows=chunk)
                        copied += len(chunk)
        if copied:
            print(f"[deepmap sync] reused {copied} embedding(s) of re-keyed node(s) (same file, name and "
                  f"summary — e.g. a line shift); they are not re-embedded", file=sys.stderr)
        return copied
    except Exception:  # noqa: BLE001 — an optimisation: re-embedding is always the safe fallback
        return 0


# ---- graph-wide deletes, bounded -----------------------------------------------------------------------
# The stale prune and the --switch-project wipe were ONE `... DETACH DELETE n` statement each, i.e. one
# transaction holding every deleted node AND relationship. Measured on Neo4j 5.26: one graph-wide prune of
# a 1.2k-node / 90k-edge graph peaked at 201 MB of transaction state (~2.2 KB per relationship) against
# the ~358 MB cap of the 512m per-instance heap, so on a bigger graph the wipe or the prune could never
# complete. They now delete relationships first, then nodes, in CALL { } IN TRANSACTIONS batches (so a hub
# node never puts its whole degree into one transaction). Not atomic any more, and deliberately so: a
# prune interrupted half-way just leaves orphans the next prune removes, and a wipe deletes the owner
# stamp LAST, so a half-finished wipe still names the old owner and the next sync refuses to adopt it.
_DELETE_REL_BATCH = 10_000
_DELETE_NODE_BATCH = 1_000


def _is_syntax_error(exc) -> bool:
    return "SyntaxError" in str(getattr(exc, "code", "") or "")


def _run_batched(driver, match: str, var: str, action: str, batch: int, **params) -> None:
    """Run `<match> <action on var>` in inner transactions of `batch` rows. Three spellings, newest
    first: the `CALL (var) { }` scope clause (Neo4j 5.23+ and Cypher 25, where the importing WITH is
    gone), the importing-WITH `CALL { WITH var }` (Neo4j 4.4-5.x), and — only for a server that knows
    neither — the plain unbatched statement. Only a SYNTAX error moves on to the next spelling (in a
    fresh session); any other database error propagates (fail loud). Runs as an auto-commit query
    (session.run): CALL IN TRANSACTIONS cannot run inside an explicit transaction."""
    forms = (f"{match} CALL ({var}) {{ {action} }} IN TRANSACTIONS OF {int(batch)} ROWS",
             f"{match} CALL {{ WITH {var} {action} }} IN TRANSACTIONS OF {int(batch)} ROWS",
             f"{match} {action}")
    for i, cypher in enumerate(forms):
        try:
            with driver.session() as s:
                res = s.run(cypher, **params)
                consume = getattr(res, "consume", None)
                if callable(consume):
                    consume()                 # run it NOW, so a syntax error surfaces here
            return
        except Exception as exc:  # noqa: BLE001 — re-raised unless it is a syntax error with a fallback left
            if i == len(forms) - 1 or not _is_syntax_error(exc):
                raise


def _cleanup_stale(driver, live_keys):
    """Graph-wide stale prune: DETACH-DELETE every keyed node not in `live_keys` — its relationships
    first, then the nodes, in bounded batches. `n.key IS NOT NULL` keeps the keyless :_DeepMapMeta owner
    singleton out of it."""
    stale = "n.key IS NOT NULL AND NOT n.key IN $keys"
    keys = list(live_keys)
    _run_batched(driver, f"MATCH (n)-[r]->() WHERE {stale}", "r", "DELETE r", _DELETE_REL_BATCH, keys=keys)
    _run_batched(driver, f"MATCH ()-[r]->(n) WHERE {stale}", "r", "DELETE r", _DELETE_REL_BATCH, keys=keys)
    _run_batched(driver, f"MATCH (n) WHERE {stale}", "n", "DETACH DELETE n", _DELETE_NODE_BATCH, keys=keys)


# Repo-ROOT markers that make a graph-wide stale-prune safe (this sync covers the WHOLE graph). `.git`
# is canonical; other VCS roots (.hg/.svn) and common project-root files let NON-GIT repos prune too
# (P7 — legacy/enterprise TFS/SVN/plain-folder codebases). A subdir won't carry these at the subdir
# level, and the 0-node refusal in sync() is the catastrophic-wipe backstop either way. Drop a
# `.deepmap-root` file to force-opt-in a bare folder.
_ROOT_MARKERS = (".git", ".hg", ".svn", "pyproject.toml", "setup.py", "package.json", "go.mod",
                 "Cargo.toml", "pom.xml", "build.gradle", ".deepmap-root")


def _cleanup_is_safe(root: Path) -> bool:
    """A graph-wide stale-prune (`_cleanup_stale` DETACH-DELETEs every node whose key is not in the
    freshly-extracted set) is only safe when this sync covers the WHOLE graph — i.e. the repo root.
    Node keys are workspace-root-relative (`relpath::name:line`), so a SUBDIR sync produces keys
    (`module.py::f:1`) that never match a prior full-repo sync's keys (`src/module.py::f:1`) — a
    graph-wide prune would then DETACH-DELETE the entire out-of-subtree graph (bug-hunt #3 HIGH).
    Gate on a repo-ROOT marker (git OR another recognizable project/VCS root, so non-git repos work);
    a bare folder with no marker refuses. The 0-node refusal in sync() is the backstop either way."""
    return any((root / m).exists() for m in _ROOT_MARKERS)


# ---- F3d: stale-cleanup WIPE GUARD (the P0 of the 2026-07 field audit) --------------------------------
# The three shipped guards (0-node refusal, repo-root marker, owner-singleton) all MISS an engine
# DOWNGRADE: a polyglot Ruby+Python repo whose graphify install broke falls back to `ast` (Python-only),
# yields a NON-ZERO partial extraction (passes the 0-node guard), and the graph-wide prune then
# DETACH-DELETEs the graphify-built Ruby graph that ast can't recreate — and /deep-map:refresh passes
# --stale-cleanup automatically. Two pure-additive guards close it (a normal graphify->graphify re-sync
# still prunes true orphans); --force-prune is the explicit operator override for both.
_PRUNE_MAX_FRACTION = 0.5   # refuse a prune that would delete more than this share of the keyed graph


def _read_prior_manifest(root) -> dict:
    """A PRIOR run's <root>/.deepmap/graph_build.json as a dict; {} when absent/unreadable. Never raises."""
    try:
        p = Path(root) / ".deepmap" / "graph_build.json"
        if not p.is_file():
            return {}
        data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _read_prior_engines(root) -> list:
    """The engines whose nodes the CURRENT graph holds, per the prior manifest: its `graph_engines`, or
    for a manifest written before that field existed its `extractor`. [] when unknown. Never raises."""
    data = _read_prior_manifest(root)
    engines = data.get("graph_engines")
    if isinstance(engines, list):
        return sorted({str(e) for e in engines if e})
    ex = data.get("extractor")
    return [str(ex)] if ex else []


def _read_prior_extractor(root):
    """The engine a PRIOR run left in the graph ('graphify'|'ast'), or None if absent/unreadable. Read
    BEFORE sync rewrites the manifest (end of sync), so it reflects what the CURRENT graph was built
    with — used to refuse an engine-DOWNGRADE prune. 'graphify' whenever the graph still holds graphify
    nodes (`graph_engines`), even if the last run was ast: a refused downgrade prune leaves them in
    place, so the guard must keep protecting them on every later run, not just the first. Never raises."""
    engines = _read_prior_engines(root)
    if "graphify" in engines:
        return "graphify"
    return engines[0] if engines else None


def _prune_blast_radius(driver, live_keys):
    """Count (would_delete, existing_total) over the keyed graph: how many nodes a graph-wide stale
    prune would DETACH-DELETE (n.key set, not in the fresh live set) and the total keyed population.
    Best-effort; any query error fails OPEN -> (0, 0) so a legitimate prune is never blocked."""
    try:
        with driver.session() as s:
            rec = s.run(
                "MATCH (n) WHERE n.key IS NOT NULL "
                "RETURN count(n) AS total, "
                "count(CASE WHEN NOT n.key IN $keys THEN 1 END) AS deleted",
                keys=list(live_keys)).single()
            if rec is None:
                return 0, 0
            return int(rec["deleted"] or 0), int(rec["total"] or 0)
    except Exception:
        return 0, 0


def _prune_decision(engine, prior_engine, would_delete, existing_total, force_prune):
    """Pure: whether a graph-wide stale-prune is safe. Returns ('prune', None) to proceed or
    ('skip', reason). --force-prune overrides both guards. An engine DOWNGRADE (a prior graphify graph,
    this run fell back to ast — ast cannot recreate graphify-only nodes) or a BLAST RADIUS over
    _PRUNE_MAX_FRACTION of the keyed graph (almost always a partial/mis-scoped extraction, not a real
    orphan set) refuses."""
    if force_prune:
        return "prune", None
    if engine == "ast" and prior_engine == "graphify":
        return "skip", "engine-downgrade"
    if existing_total > 0 and would_delete > existing_total * _PRUNE_MAX_FRACTION:
        return "skip", "blast-radius"
    return "prune", None


# ---- which stale nodes are suspicious (round 7: T7d-1 lead, T5#7) ------------------------------------
# The >50% guard exists to catch a PARTIAL or mis-scoped extraction. It counted every stale node, so it
# also refused the prunes it should allow — a full re-key (every function's line moved, every file
# re-extracted), a big deletion (the files are gone), DEEPMAP_EXCLUDE_TESTS dropping the test nodes it was
# set to drop (so it "had no effect"), the vendor filter dropping pre-v0.24 vendored nodes — and it was
# measured AFTER the push, so a full re-key read as exactly 50% and slipped through by luck. The census now
# runs BEFORE the push and, when the plain count exceeds the fraction, only stale nodes whose file STILL
# EXISTS, was NOT extracted this run and is NOT deliberately excluded count toward the blast radius.
def _source_file_gone(root, rel) -> bool:
    """True iff a node's source_file no longer exists under `root` (long-path safe); False when unknown."""
    try:
        p = Path(rel)
        path = os.path.abspath(str(p if p.is_absolute() else Path(root) / rel))
        if os.name == "nt":
            path = _extended_path(path)
        return not os.path.lexists(path)
    except Exception:
        return False


def _stale_census(driver, root, live_keys, live_files):
    """(suspicious, exempt) over the keyed nodes a graph-wide prune would delete: `suspicious` = those in
    a file that still exists, was not extracted this run and is not excluded on purpose (the test filter
    when DEEPMAP_EXCLUDE_TESTS is on, the vendor filter unless DEEPMAP_INCLUDE_VENDOR); `exempt` counts the
    rest by why ({"re-extracted", "deleted", "excluded"}). None when the census can't be read (the caller
    keeps the plain count — the guard fails safe). One grouped read; never raises."""
    try:
        with driver.session() as s:
            rows = list(s.run("MATCH (n) WHERE n.key IS NOT NULL AND NOT n.key IN $keys "
                              "RETURN n.source_file AS sf, count(n) AS c", keys=list(live_keys)) or [])
        per_file = [(r["sf"], int(r["c"] or 0)) for r in rows]
    except Exception:
        return None
    try:
        excl_tests, keep_vendor = _exclude_tests_enabled(), _include_vendor_enabled()
        vendor_parts = _gitignore_dir_parts(root)
        suspicious, exempt = 0, {"re-extracted": 0, "deleted": 0, "excluded": 0}
        for sf, c in per_file:
            rel = str(sf or "")
            if rel and rel in live_files:
                exempt["re-extracted"] += c
            elif rel and ((excl_tests and _is_test_path(rel))
                          or (not keep_vendor and _is_vendor_path(rel, vendor_parts))):
                exempt["excluded"] += c
            elif rel and _source_file_gone(root, rel):
                exempt["deleted"] += c
            else:
                suspicious += c
        return suspicious, exempt
    except Exception:
        return None


def _plan_prune(driver, root, nodes, engine, prior_engine, force_prune) -> dict:
    """The stale-prune decision for this pass, taken BEFORE the push: {"decision": "prune"|"skip",
    "reason", "would_delete", "existing_total", "suspicious", "exempt"}."""
    live = {n["key"] for n in nodes}
    would_delete, existing_total = _prune_blast_radius(driver, live)
    decision, reason = _prune_decision(engine, prior_engine, would_delete, existing_total, force_prune)
    plan = {"live": live, "decision": decision, "reason": reason, "would_delete": would_delete,
            "existing_total": existing_total, "suspicious": None, "exempt": None}
    if decision == "skip" and reason == "blast-radius":
        census = _stale_census(driver, root, live, {str(n.get("source_file") or "") for n in nodes})
        if census is not None:
            suspicious, exempt = census
            decision, reason = _prune_decision(engine, prior_engine, suspicious, existing_total, force_prune)
            plan.update(decision=decision, reason=reason, suspicious=suspicious, exempt=exempt)
    return plan


# ---- evidence-only prune (auto-sync: DeepMap's own background refresh) -------------------------------------------------
# An unattended refresh never runs the full prune (it is not the user's decision to delete what the graph holds).
# Without any prune, though, every ast line shift leaves duplicates (one inserted line re-keys every function below
# it). So its prune is EVIDENCE-ONLY: it deletes the stale nodes only when every one of them is provably stale — in
# a file this run re-read (the symbol is gone or re-keyed) or in a file that no longer exists — and refuses (keeps
# them all) on anything less: a stale node in a file that still exists but was not extracted, a deliberately
# excluded one, a re-read file that lost more than half of its symbols (a half-written or conflicted file), an
# engine failure, fallback or timeout, or an engine change. Evidence prune ⊆ full prune by construction: the same
# census, plus extra refusals, and the full guard itself (_prune_decision) as its last condition. A refused evidence
# prune keeps the verdict as it is (auto-sync H7): the nodes are reported as `stale_kept`, with a caveat, and
# /deep-map:refresh (the full prune) removes them.
_POLICY_RANK = {p: i for i, p in enumerate(sync_status.PRUNE_POLICIES)}   # none < evidence < full


def _effective_policy(holder, request) -> str:
    """The prune policy of a pass: the max (none < evidence < full) of the holder's and a claimed re-run request's.
    A request without "prune_policy" (an older DeepMap's) contributes "full" when it asked for cleanup_stale, else
    "none"."""
    h = holder if holder in _POLICY_RANK else "none"
    if not isinstance(request, dict) or not request:
        return h
    r = request.get("prune_policy")
    if r not in _POLICY_RANK:
        r = "full" if request.get("cleanup_stale") else "none"
    return h if _POLICY_RANK[h] >= _POLICY_RANK[r] else r


_EVIDENCE_WHY = {
    "evidence:census-unreadable": "the graph's stale nodes could not be counted",
    "evidence:unextracted-files": "some are in files that still exist but were not re-read",
    "evidence:excluded": "some are in files a test / vendor filter now excludes",
    "evidence:file-shrank": "a re-read file lost more than half of its symbols",
    "evidence:engine-failures": "the extraction engine failed on some files",
    "evidence:fallback": "the extraction fell back or ran out of time",
    "evidence:engine-change": "the extraction engine changed",
    "evidence:guard": "the full prune's own guard refused",
}


def _evidence_census(driver, root, live_keys, nodes):
    """The evidence-prune census over the keyed nodes a prune would delete: {"suspicious", "exempt"} exactly as
    _stale_census counts them, plus "shrunk" = [(rel, prior_names, lost)] for every RE-EXTRACTED file holding stale
    nodes whose prior distinct symbol names lost more than half in this run (lost = |prior names − this run's names
    of that file|; a re-keyed symbol — same name, new line — or an old duplicate is not lost). Read before the push,
    so "prior" is the graph as it was. None when the graph cannot be read. Never raises."""
    live_files = {str(n.get("source_file") or "") for n in nodes}
    census = _stale_census(driver, root, live_keys, live_files)
    if census is None:
        return None
    suspicious, exempt = census
    try:
        with driver.session() as s:
            stale_rows = list(s.run("MATCH (n) WHERE n.key IS NOT NULL AND NOT n.key IN $keys "
                                    "RETURN DISTINCT n.source_file AS sf", keys=list(live_keys)) or [])
        files = sorted({str(r["sf"]) for r in stale_rows if r["sf"] and str(r["sf"]) in live_files})
        prior = {}
        if files:
            with driver.session() as s:
                for r in s.run("MATCH (n) WHERE n.key IS NOT NULL AND n.source_file IN $files "
                               "RETURN n.source_file AS sf, collect(DISTINCT n.name) AS names", files=files) or []:
                    prior[str(r["sf"])] = {str(x) for x in (r["names"] or []) if x is not None}
    except Exception:
        return None
    live_names: dict = {}
    for n in nodes:
        sf = str(n.get("source_file") or "")
        if sf in prior and n.get("name") is not None:
            live_names.setdefault(sf, set()).add(str(n.get("name")))
    shrunk = []
    for rel in sorted(prior):
        names = prior[rel]
        lost = len(names - live_names.get(rel, set()))
        if names and 2 * lost > len(names):
            shrunk.append((rel, len(names), lost))
    return {"suspicious": suspicious, "exempt": dict(exempt), "shrunk": shrunk}


def _evidence_decision(census, *, engine, prior_engine, extraction, existing_total, would_delete):
    """PURE: ("prune", None) only when every stale node is provably stale and the run is trustworthy, else
    ("skip", "evidence:<why>") — the first failing condition, in this order: census-unreadable, unextracted-files,
    excluded, file-shrank, engine-failures, fallback, engine-change, guard (the full prune's own _prune_decision)."""
    if not isinstance(census, dict):
        return "skip", "evidence:census-unreadable"
    try:
        suspicious = int(census.get("suspicious") or 0)
        excluded = int((census.get("exempt") or {}).get("excluded") or 0)
    except (TypeError, ValueError):
        return "skip", "evidence:census-unreadable"
    if suspicious != 0:
        return "skip", "evidence:unextracted-files"
    if excluded != 0:
        return "skip", "evidence:excluded"
    if census.get("shrunk"):
        return "skip", "evidence:file-shrank"
    info = extraction if isinstance(extraction, dict) else {}
    if info.get("failed_files"):
        return "skip", "evidence:engine-failures"
    if info.get("fallback_reason") or info.get("timed_out"):
        return "skip", "evidence:fallback"
    if prior_engine not in (None, engine):
        return "skip", "evidence:engine-change"
    if _prune_decision(engine, prior_engine, suspicious, existing_total, False) != ("prune", None):
        return "skip", "evidence:guard"
    return "prune", None


def _plan_prune_evidence(driver, root, nodes, engine, prior_engine, extraction) -> dict:
    """The evidence-only prune decision for this pass, taken BEFORE the push (as _plan_prune is): the same plan
    shape ({"live", "decision", "reason", "would_delete", "existing_total", "suspicious", "exempt"}) plus "policy":
    "evidence" and "shrunk"."""
    live = {n["key"] for n in nodes}
    would_delete, existing_total = _prune_blast_radius(driver, live)
    census = _evidence_census(driver, root, live, nodes)
    decision, reason = _evidence_decision(census, engine=engine, prior_engine=prior_engine, extraction=extraction,
                                          existing_total=existing_total, would_delete=would_delete)
    return {"live": live, "decision": decision, "reason": reason, "would_delete": would_delete,
            "existing_total": existing_total, "policy": "evidence",
            "suspicious": (census or {}).get("suspicious"), "exempt": (census or {}).get("exempt"),
            "shrunk": (census or {}).get("shrunk")}


# ---- project-switch safety (one Neo4j instance holds ONE project's graph) ---
# DeepMap pushes every project into ONE shared Neo4j; node keys are root-relative and un-namespaced, so
# syncing project B into a graph that holds project A would silently MERGE the two (cross-project query
# pollution) — and a --stale-cleanup at B's root would DETACH DELETE all of A's nodes. Neo4j Community
# can't do a DB-per-project (Enterprise only), so switching is made SAFE, not isolated: a keyless
# singleton :_DeepMapMeta node records which project OWNS the graph, and sync() refuses to change owners
# unless authorised (--switch-project only, P1-6), in which case it wipes + rebuilds fresh. Whether a
# stamp names THIS project is port_registry.owner_is_this_project, the ONE owner predicate (F1). The
# meta node has NO `key` property (so _cleanup_stale, which only deletes n.key IS NOT NULL, never
# touches it) and no code label (so search/embeddings/indexes never see it).
def _project_identity(root) -> str:
    """The canonical owner key for a sync root: the git toplevel if `root` is in a git repo, else the
    resolved path. All subdirs of one repo map to the SAME identity, so a subdir sync is never mistaken
    for a different project."""
    git = shutil.which("git")
    if git:
        try:
            # git prints the path as UTF-8; decoding it in the locale code page (cp1255, cp1252, ...)
            # garbled or failed on a non-ASCII root, so the owner identity silently diverged (G6).
            p = subprocess.run([git, *_git_longpaths(), "-C", str(root), "rev-parse", "--show-toplevel"],
                               capture_output=True, encoding="utf-8", errors="surrogateescape",
                               timeout=30, env=_git_env())
            top = (p.stdout or "").strip()
            if p.returncode == 0 and top:
                return str(Path(top).resolve())
        except (OSError, subprocess.TimeoutExpired):
            pass
    return str(Path(root).resolve())


def _is_subdir(child, parent) -> bool:
    """True iff `child` is strictly under `parent` — lexical (via relative_to on case-normalised
    resolved paths), so C:\\proj\\A is NOT a subdir of C:\\proj\\AB despite the string prefix, and it is
    case-insensitive on Windows. child == parent is False (not a strict subdir)."""
    c = os.path.normcase(str(Path(child).resolve()))
    p = os.path.normcase(str(Path(parent).resolve()))
    if c == p:
        return False
    try:
        Path(c).relative_to(Path(p))
        return True
    except ValueError:
        return False


def _abs_on_this_os(owner) -> bool:
    """ROUND-5 G3: is the owner stamp `owner` an absolute path ON THIS OS? pathlib's rule: on POSIX it
    starts with '/'; on Windows it has a drive AND a root ('C:\\x', 'C:/x') or is a UNC share. Every stamp
    DeepMap writes is one (_project_identity resolves it), so a stamp that is not — 'C:/Users/a/X' read on
    Linux/macOS, '/Users/a/X' (macOS), '\\x' or 'C:rel' read on Windows, any relative stamp — was written
    on ANOTHER machine/OS (the NEO4J_URI escape hatch shared across machines, WSL reaching the Windows
    7687), and Path(stamp).resolve() rebased it onto the working directory: run from the project root it
    landed AT or INSIDE the project, a "vanished narrower stamp" of THIS project, so sync merged into and
    re-stamped the other machine's graph. Deliberately pathlib, not os.path.isabs: before Python 3.13
    os.path.isabs('/x') is True on Windows. Never raises (-> False)."""
    try:
        return bool(owner) and Path(str(owner)).is_absolute()
    except Exception:
        return False


def _same_project(cur_id, owner) -> bool:
    """Does the graph owner stamp `owner` (:_DeepMapMeta owning_root) name the project whose identity is
    `cur_id`? ROUND-4 F1: this is port_registry.owner_is_this_project — the ONE owner predicate the CLI,
    heal, provision and the legacy-route verdict use too — so it is alias/legacy-aware (a stamp written
    with the identity a pre-UTF-8-fix build computed, e.g. 'C:/Users/JosÃ©/proj', is this project's own
    graph: E1), and containment counts only between paths of the SAME project identity. The old rule
    here ("one path contains the other") made a repository nested in another DeepMap project (work/ and
    work/api) the same project — api's sync then merged api-relative keys into work's graph — and it
    refused the project's own pre-fix stamp while the CLI accepted it. `cur_id` is a project identity
    (_project_identity), so it is also a valid root for the predicate. Only a missing (or failing)
    port_registry predicate falls back to _same_project_local (the same rule without the legacy
    identities). ROUND-5 G3: a stamp that is not an absolute path on THIS OS (_abs_on_this_os) is a
    DIFFERENT project — decided here, before the predicate, so sync's foreign-owner check never depends on
    how a predicate resolves it. Never raises."""
    if not _abs_on_this_os(owner):
        return False
    try:
        import port_registry
    except ImportError:
        port_registry = None
    if port_registry is not None and hasattr(port_registry, "owner_is_this_project"):
        try:
            return bool(port_registry.owner_is_this_project(cur_id, owner))
        except Exception:
            pass
    return _same_project_local(cur_id, owner)


def _same_project_local(cur_id, owner) -> bool:
    """F1 without port_registry (mirrors port_registry._same_project_path): the same path -> True;
    disjoint paths -> False; one contains the other -> True only when both map to the same project
    identity — a subdirectory of the same repository, never a nested repository or a folder around the
    project. An owner strictly inside `cur_id` whose path no longer exists (an old, NARROWER stamp) is
    judged at its nearest EXISTING ancestor: `cur_id` itself (nothing of it is left) -> True; a folder
    inside `cur_id` -> that folder's identity decides (a vanished stamp under a nested repository is that
    repository's). ROUND-5 G3: an owner that is not an absolute path on THIS OS (_abs_on_this_os — e.g.
    'C:/Users/a/X' on POSIX) is a DIFFERENT project; it is never resolved against the working directory
    (where the project root would make it a "vanished narrower stamp" of this project). Never raises."""
    try:
        if not cur_id or not owner or not _abs_on_this_os(owner):
            return False

        def _n(p):
            return os.path.normcase(str(Path(p).resolve()))

        cur, own = _n(cur_id), _n(owner)
        if cur == own:
            return True
        probe = owner
        if _is_subdir(owner, cur_id):
            if not Path(owner).exists():       # an old narrower stamp whose directory is gone
                anc = next((a for a in Path(owner).resolve().parents if a.exists()), None)
                if anc is None or _n(anc) == cur or not _is_subdir(anc, cur_id):
                    return True
                probe = anc
        elif not _is_subdir(cur_id, owner):
            return False                       # disjoint
        return _n(_project_identity(probe)) == _n(_project_identity(cur_id))
    except Exception:
        return False


def _read_owning_root(driver):
    """The owning project root recorded on the shared graph, or None if unrecorded. Consumes the result
    as a list and tolerates zero rows (never .single()[...] — a fresh/empty graph must return None, not
    raise); a real DatabaseError propagates (fail-loud) via the caller's connected driver."""
    with driver.session() as s:
        rows = list(s.run("MATCH (m:_DeepMapMeta) RETURN m.owning_root AS owning_root LIMIT 1"))
    for r in rows:
        val = r["owning_root"]
        if val:
            return val
    return None


def _write_owning_root(driver, identity: str) -> None:
    """Stamp/refresh the singleton owner node. The fixed id means MERGE never duplicates it; the absence
    of a `key` property means the stale-prune never deletes it."""
    with driver.session() as s:
        s.run("MERGE (m:_DeepMapMeta {id:'singleton'}) SET m.owning_root=$root, m.built_at=$ts",
              root=str(identity),
              ts=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))


def _graph_has_nodes(driver) -> bool:
    """True iff the graph holds any real (non-meta) node. Cheap; used only for the legacy-adoption
    notice when no owner has been recorded yet (a pre-feature graph)."""
    with driver.session() as s:
        rows = list(s.run("MATCH (n) WHERE NOT n:_DeepMapMeta RETURN count(n) AS c"))
    return bool(rows) and bool(rows[0]["c"])


def _count_embedded_nodes(driver, fallback: int = 0) -> int:
    """The TRUE number of nodes carrying an embedding — NOT the per-run re-embed delta.

    The sync-time health verdict + the graph-build manifest must reflect how many nodes ARE
    embedded, not how many were re-embedded THIS run: `embeddings.generate_embeddings` returns only
    the delta, which is legitimately 0 on a re-sync of an already-fully-embedded graph — and that 0
    used to trip `verdict.classify` into a FALSE DEGRADED "VECTOR SEARCH DISABLED" on a perfectly
    healthy graph (Rails field-review P1-5). This is the same count status/doctor already report.
    Best-effort: any query failure (or an empty result) returns `fallback` (the per-run delta), so
    the outcome is never worse than the old behaviour and this never raises."""
    try:
        with driver.session() as s:
            rec = s.run("MATCH (n) WHERE n.embedding IS NOT NULL RETURN count(n) AS c").single()
        return int(rec["c"]) if rec is not None and rec["c"] is not None else fallback
    except Exception:
        return fallback


def _wipe_all(driver) -> None:
    """Delete the ENTIRE graph (incl. the meta node) for a clean project switch, in bounded batches
    (see _run_batched): every relationship, then every node but the :_DeepMapMeta owner stamp, then the
    stamp itself — LAST, so a wipe that dies half-way still names the old owner and the next sync
    refuses to adopt the leftovers. The new owner is re-stamped after the fresh push."""
    _run_batched(driver, "MATCH ()-[r]->()", "r", "DELETE r", _DELETE_REL_BATCH)
    _run_batched(driver, "MATCH (n) WHERE NOT n:_DeepMapMeta", "n", "DETACH DELETE n", _DELETE_NODE_BATCH)
    with driver.session() as s:
        s.run("MATCH (n:_DeepMapMeta) DETACH DELETE n")


# ---- which Neo4j this project uses (ROUND-2 D1: ONE rule for CLI, sync, indexes, provision, hooks) ----
def _resolve_route(root):
    """(uri, legacy, problem) for `root` — port_registry.resolve_neo4j_route, the single routing rule:
    an explicit NEO4J_URI (the escape hatch), else the project's own registered instance, else its own
    instance re-found from its conf, else bolt 7687 as a LEGACY route that must be verified before use
    (`legacy` True — see verify_legacy_owner). `problem` (uri None) when there is no safe route: the
    registry exists but can't be read, or this project has no instance while another project owns
    7687. It used to be a bare `except Exception: 7687`, so /deep-map:refresh and the git hook wrote
    into another project's instance. Only a MISSING port_registry module falls back to NEO4J_URI or
    7687 (and 7687 is then a legacy route too). Never raises.

    The answer unpacks as that 3-tuple and also carries `.source` — the resolver's route source ("registry",
    "instance-conf", "env", "legacy-unverified", "none" or None) — which the manifest's build_recipe records."""
    try:
        import port_registry
    except ImportError:
        env_uri = os.environ.get("NEO4J_URI")
        return _Route(env_uri or _bolt_uri(7687), not env_uri, None,
                      "env" if env_uri else "legacy-unverified")
    try:
        route = port_registry.resolve_neo4j_route(root)
    except Exception as exc:  # noqa: BLE001 — the resolver never raises; be safe anyway
        return _Route(None, False, f"could not resolve this project's Neo4j instance ({type(exc).__name__}: {exc})")
    if route.problem or not route.uri:
        return _Route(None, False, route.problem or "could not resolve this project's Neo4j instance",
                      getattr(route, "source", None))
    return _Route(route.uri, route.source == "legacy-unverified", None, getattr(route, "source", None))


class _Route(tuple):
    """_resolve_route's answer: unpacks as (uri, legacy, problem) like the plain tuple every caller and gate stub
    uses, plus `.source` (the resolver's route source; None when unknown)."""

    def __new__(cls, uri, legacy, problem, source=None):
        self = super().__new__(cls, (uri, legacy, problem))
        self.source = source
        return self


def _legacy_verdict(root, owner, graph_has_nodes, uri=None):
    """(ok, problem): may `root` use the graph behind a legacy route, given its recorded owner and
    whether it holds nodes? port_registry.legacy_owner_verdict is the policy (ok for this project's own
    graph, or an unowned EMPTY one; refused otherwise with a remedy that names /deep-map:setup); a
    local mirror stands in only when that module is missing. ROUND-5 G3: a recorded owner that is not an
    absolute path on THIS OS (_abs_on_this_os — another machine's stamp) is another project's graph:
    refused here, in the policy's own wording, before the policy resolves it against the working
    directory (where the project root made it this project's own)."""
    try:
        import port_registry
    except ImportError:
        port_registry = None
    fix = ("this project has no DeepMap Neo4j instance of its own — run /deep-map:setup in this "
           "project to give it one")
    where = uri or _bolt_uri(7687)
    if owner and not _abs_on_this_os(owner):
        try:
            where = uri or port_registry.bolt_uri(port_registry.BASE_BOLT)
            fix = port_registry.no_instance_problem()
        except Exception:
            pass
        return False, f"{where} holds project {owner}'s DeepMap graph — {fix}"
    if port_registry is not None and hasattr(port_registry, "legacy_owner_verdict"):
        return port_registry.legacy_owner_verdict(root, owner, graph_has_nodes, uri=uri)
    if owner:
        if _same_project(_project_identity(root), owner):
            return True, None
        return False, f"{where} holds project {owner}'s DeepMap graph — {fix}"
    if graph_has_nodes is False:
        return True, None
    return False, f"{where} holds a DeepMap graph with no recorded owner, so it can't be shown to be this project's — {fix}"


def verify_legacy_owner(driver, root, *, uri=None):
    """(ok, reason) — may the project at `root` read or write the graph behind a LEGACY route
    (port_registry.resolve_neo4j_route source "legacy-unverified": no instance of its own, so bolt 7687,
    which may hold another project's graph)? ok when the graph's :_DeepMapMeta owner is this project
    (port_registry.owner_is_this_project — the ONE owner predicate, F1), or when the graph has no owner
    and is EMPTY. Otherwise
    (another project's graph, or an unowned graph with nodes) not ok, and `reason` is the user-facing
    refusal naming /deep-map:setup. `driver` must be connected to that route's URI (`uri` only words
    the message). The CLI, provision and sync itself call this before using the route.

    Read-only. A database error (instance down, bad credentials) PROPAGATES: an outage stays an
    outage (fail loud), never a made-up verdict."""
    owner = _read_owning_root(driver)
    has_nodes = None if owner else bool(_graph_has_nodes(driver))
    return _legacy_verdict(root, owner, has_nodes, uri)


def _is_local_base_bolt(uri) -> bool:
    """True iff `uri` is the LOCAL base bolt port (7687 on localhost / 127.0.0.1 / ::1) — DeepMap's
    legacy / first-project store. Never raises."""
    try:
        from urllib.parse import urlparse
        try:
            import port_registry
            base = int(port_registry.BASE_BOLT)
        except Exception:
            base = 7687
        u = urlparse(str(uri))
        return (u.hostname or "").lower() in _LOOPBACK_HOSTS and (u.port or 7687) == base
    except Exception:
        return False


def _operator_neo4j_uri() -> bool:
    """True iff NEO4J_URI in this process is the OPERATOR's escape hatch (a BYO / shared instance they
    chose) — not provision's per-project injection: provision SETS NEO4J_URI to the project's own bolt
    URI before its in-process sync (setup), and its _USER_SET_NEO4J_URI records whether the operator had
    set one when it was imported. No provision module in the process -> any NEO4J_URI is the operator's
    (only provision injects). Never raises."""
    try:
        if not os.environ.get("NEO4J_URI"):
            return False
        prov = sys.modules.get("provision")
        flag = getattr(prov, "_USER_SET_NEO4J_URI", None) if prov is not None else None
        return True if flag is None else bool(flag)
    except Exception:
        return True


def _is_base_claim_route(root, neo4j_uri) -> bool:
    """True iff sync is about to use DeepMap's shared base store (local bolt 7687) only because THIS
    project's own registry entry claims the base (7687, 7474) pair — not an operator NEO4J_URI (the
    escape hatch; provision's injection of the claimed pair for setup's own sync does not count as one).
    Such a claim may be one a <= v0.27.4 build wrote by mistake (ROUND-4 F2: its _base_adopt_ok counted
    the adopter's own new MCP entry as proof it owned a DOWN legacy store). Never raises."""
    try:
        if _operator_neo4j_uri() or not _is_local_base_bolt(neo4j_uri):
            return False
        import port_registry
        return port_registry.lookup_neo4j_ports(root) == (port_registry.BASE_BOLT, port_registry.BASE_HTTP)
    except Exception:
        return False


def _record_base_store_owner(root, neo4j_uri, *, identity=None) -> None:
    """Refresh DeepMap's base store owner record (provision._record_base_owner — what
    provision._base_adopt_ok reads while the base is DOWN, and what setup re-verifies a base claim
    against, F2) from what THIS sync just proved over the driver it opened itself from `neo4j_uri` (so
    the graph it read or wrote is provably the one there):
      * identity None — after a REAL push: the graph's :_DeepMapMeta owner was just stamped with this
        project, so the record names this project. Setup already writes it after its own sync; this
        covers the syncs setup never sees — /deep-map:refresh, the git post-commit hook, `deepmap sync`;
      * identity = a live owner stamp that names ANOTHER project (sync refuses, or replaces it only on
        an authorised switch, which re-records after its push): the store is provably that project's —
        the same live-owner proof provision's _foreign_base_owner records.
    Only for the LOCAL base bolt; provision guards the write itself: only while DeepMap's OWN legacy
    instance is the one running (a foreign Neo4j on 7687 says nothing about the store on disk), only into
    an existing data dir, and an unchanged record is not rewritten. A per-project instance, a remote host
    or an injected driver never gets here. Best-effort: never raises, never affects the sync."""
    try:
        if not _is_local_base_bolt(neo4j_uri):
            return
        import provision
        if identity:
            provision._record_base_owner(root, identity=str(identity))
        else:
            provision._record_base_owner(root)
    except Exception:
        pass


# ---- graph-build provenance manifest ---------------------------------------
# After a successful rebuild, sync stamps <root>/.deepmap/graph_build.json with the git SHA the graph
# was built at (+ counts). The benchmark's assert_graph_at_pinned_sha reads it to verify the ON-arm
# graph and the OFF-arm grep are the SAME pinned SHA — automatically, with no operator honor-
# declaration. When `root` is NOT a git repo, a deterministic filesystem CONTENT-hash is stamped
# instead (under `built_at_content_hash`/`provenance:"content-hash"`) so the sidecar still exists for
# non-git workspaces; only an empty/unreadable tree or a write failure stays silent. The benchmark's
# SHA-pin path is unaffected — it reads `built_at_sha` (git only) and still fails closed otherwise.
# The manifest also names the DeepMap version that built the graph (`deepmap_version`, fix-all r283), so
# the benchmark's resume check can tell a graph an older DeepMap built from this one's.
def _read_code_version(build_dir=None):
    """The DeepMap version of the code in `build_dir` (default: this module's own build/) — the "version"
    in <plugin root>/.claude-plugin/plugin.json, read exactly as port_registry.code_identity reads it for a
    server's owner sidecar — or None when it cannot be read. Never raises."""
    try:
        import port_registry
        build = Path(build_dir) if build_dir else Path(__file__).resolve().parent
        v = port_registry.code_identity(build).get(port_registry.CODE_VERSION_FIELD)
    except Exception:  # noqa: BLE001 — provenance is best-effort, never a build precondition
        return None
    v = str(v).strip() if v is not None else ""
    return v or None


# Read ONCE, when this module is imported: the version of the code this process runs. A plugin checkout
# updated in place while a long sync runs must not stamp its NEWER version on a graph the older code built.
_CODE_VERSION = _read_code_version()


def _git_head_sha(root: Path):
    """Best-effort: the 40-hex HEAD SHA of `root` if it is a git repo, else None. Never raises."""
    git = shutil.which("git")
    if not git:
        return None
    try:
        p = subprocess.run([git, *_git_longpaths(), "-C", str(root), "rev-parse", "HEAD"],
                           capture_output=True, text=True, timeout=30, env=_git_env())
    except (OSError, subprocess.TimeoutExpired):
        return None
    sha = (p.stdout or "").strip().lower()
    if p.returncode == 0 and len(sha) == 40 and all(c in "0123456789abcdef" for c in sha):
        return sha
    return None


# "Dirty" (D4) = an uncommitted change that can make the graph differ from the commit: a file the
# extractors read (a source extension graphify maps or a recognizer consumes) or a .gitignore (it feeds
# the vendor filter) — modified, staged, deleted, renamed or untracked. NOT DeepMap's own artifacts
# (.deepmap/, graphify-out/, the .mcp.json setup writes, .deepmap-root, anything under DEEPMAP_HOME), and
# not files the graph never holds (notes, docs, editor settings, a harness's sidecars, vendored or
# minified code the vendor filter drops). Round 1 counted every untracked file, so the .mcp.json that
# setup/heal/mcp-start leave in the project root made every later build "dirty" forever.
_ARTIFACT_DIR_NAMES = {".deepmap", "graphify-out"}   # DeepMap's own regenerated dirs — never "changes"
_ARTIFACT_FILE_NAMES = {".mcp.json", ".deepmap-root"}  # DeepMap's own files in the project tree
_DIRTY_SAMPLE_MAX = 10                                 # paths kept in the manifest's dirty_paths_sample


def _graph_input_exts() -> set:
    """Lowercased extensions whose files can change the graph: what graphify maps into it plus what the
    per-language recognizers read (.erb views, …) plus .py (the ast path). Never raises."""
    exts = {".py"}
    try:
        exts |= {str(e).lower() for e in graphify_extract.SOURCE_CODE_EXTS}
    except Exception:
        pass
    try:
        for entry in language_recognizers.REGISTRY:
            exts |= {str(e).lower() for e in entry.extensions}
    except Exception:
        pass
    return exts


# Files that are graph inputs although they are not source code: what the engine and the recognizers read to decide
# what is extracted and how it is wired. graphify's walk honours .gitignore / .graphifyignore at every level; the
# TypeScript / JavaScript recognizer places imports and routes from tsconfig.json / jsconfig.json (`paths`, `baseUrl`
# and an `extends` chain, usually tsconfig.<name>.json), package.json (`imports`, workspace packages, the next
# dependency), nest-cli.json / angular.json / workspace.json / an Nx project.json (project roots) and next.config.*.
# A change to one changes the typed edges, so it is "dirty" and part of the source fingerprint. The names the
# recognizers read are drift-locked by tests/sync_status_gates.py section L (every *.json literal in a recognizer
# module must be a graph input).
_GRAPH_CONFIG_NAMES = frozenset({".gitignore", ".graphifyignore", "package.json", "tsconfig.json", "jsconfig.json",
                                 "nest-cli.json", "angular.json", "workspace.json", "project.json"})
_GRAPH_CONFIG_JSON_PREFIXES = ("tsconfig.", "jsconfig.")     # an `extends` target: tsconfig.base.json, ...
_GRAPH_CONFIG_PREFIXES = ("next.config.",)


def _is_graph_config(name: str) -> bool:
    """True iff a file NAMED `name` is one of the non-source graph inputs above (case-insensitive)."""
    n = str(name).lower()
    return (n in _GRAPH_CONFIG_NAMES or (n.startswith(_GRAPH_CONFIG_JSON_PREFIXES) and n.endswith(".json"))
            or n.startswith(_GRAPH_CONFIG_PREFIXES))


def _affects_graph(rel: str, vendor_parts, exts) -> bool:
    """True iff an uncommitted change to `rel` (POSIX, relative to the sync root) can change the graph: a source file
    the extractors read (by extension) or a non-source graph input (_is_graph_config), outside DeepMap's own
    artifacts and the vendor filter."""
    parts = [p for p in rel.split("/") if p]
    if not parts or any(p in _ARTIFACT_DIR_NAMES for p in parts) or parts[-1] in _ARTIFACT_FILE_NAMES:
        return False
    if not _include_vendor_enabled() and _is_vendor_path(rel, vendor_parts):
        return False
    name = parts[-1]
    if _is_graph_config(name):
        return True
    dot = name.rfind(".")
    return dot > 0 and name[dot:].lower() in exts


def _dir_affects_graph(rel: str, vendor_parts) -> bool:
    """True iff the DIRECTORY `rel` — a submodule (gitlink) or a nested repository inside the tree, whose files git
    lists only as that one entry — holds files the extraction reads: not one of DeepMap's artifact dirs, not dropped
    by the vendor filter. (Both extraction walks descend into such a directory.)"""
    parts = [p for p in str(rel).strip("/").split("/") if p]
    if not parts or any(p in _ARTIFACT_DIR_NAMES for p in parts):
        return False
    return _include_vendor_enabled() or not _is_vendor_path("/".join(parts) + "/x", vendor_parts)


# The root markers that are FILES (_cleanup_is_safe looks for them in the sync root itself): adding or removing one
# there changes whether a sync may prune at all, so the graph a sync leaves — part of the fingerprint, though the
# dirty check ignores .deepmap-root (DeepMap's own marker file).
_ROOT_MARKER_FILES = frozenset(m for m in _ROOT_MARKERS if m not in (".git", ".hg", ".svn"))


def _fingerprint_input(rel: str, vendor_parts, exts) -> bool:
    """The source fingerprint's file rule: _affects_graph, plus a root marker file in the sync root itself."""
    return ("/" not in rel and rel in _ROOT_MARKER_FILES) or _affects_graph(rel, vendor_parts, exts)


def _git_worktree_changes(root: Path):
    """Best-effort (dirty, sample, count) for the git work tree under `root`: `dirty` True iff there is
    an uncommitted change that can affect the graph (see _affects_graph) — tracked or untracked, every
    untracked file listed individually (-uall), limited to `root`'s own subtree; False when there is
    none; None when unknown (no git, not a repo, error — `sample` [] and `count` 0 then). `sample` holds
    at most _DIRTY_SAMPLE_MAX such paths relative to `root` (POSIX), `count` their total. git's -z
    output is parsed exactly: paths are UTF-8 and unquoted, and a rename/copy record carries the new and
    the old path. Never raises."""
    git = shutil.which("git")
    if not git:
        return None, [], 0
    try:
        lp = _git_longpaths()      # J6: a committed file past MAX_PATH is not "modified"
        p = subprocess.run([git, *lp, "-C", str(root), "status", "--porcelain", "-z", "--untracked-files=all",
                            "--", "."], capture_output=True, timeout=60, env=_git_env())
        pre = subprocess.run([git, *lp, "-C", str(root), "rev-parse", "--show-prefix"],
                             capture_output=True, timeout=30, env=_git_env())
    except (OSError, subprocess.TimeoutExpired):
        return None, [], 0
    if p.returncode != 0 or pre.returncode != 0:
        return None, [], 0
    try:
        # porcelain paths are relative to the repository top; the graph's are relative to `root`
        prefix = pre.stdout.decode("utf-8", "surrogateescape").strip().replace("\\", "/")
        home_rel = None
        try:
            home = Path(os.environ.get("DEEPMAP_HOME", Path.home() / ".deepmap")).resolve()
            home_rel = home.relative_to(Path(root).resolve()).as_posix() + "/"
        except (ValueError, OSError):
            pass
        vendor_parts, exts = _gitignore_dir_parts(root), _graph_input_exts()
        items = (p.stdout or b"").decode("utf-8", "surrogateescape").split("\0")
        changed: list = []
        i = 0
        while i < len(items):
            entry = items[i]
            i += 1
            if len(entry) < 4 or entry[2] != " ":
                continue
            xy, paths = entry[:2], [entry[3:]]
            if ("R" in xy or "C" in xy) and i < len(items):
                paths.append(items[i])                  # -z rename/copy: "XY new\0old\0"
                i += 1
            for path in paths:
                rel = path.replace("\\", "/")
                if prefix and rel.startswith(prefix):
                    rel = rel[len(prefix):]
                if home_rel and (rel.rstrip("/") + "/").startswith(home_rel):
                    continue                            # DeepMap's own home (gout, instances) inside the tree
                if rel.endswith("/") or (rel and (Path(root) / rel).is_dir()):
                    # a submodule with new commits / changed content, or an untracked nested repository: git lists
                    # it as ONE entry, and the extraction reads the files inside it
                    rel = rel.rstrip("/")
                    if rel and rel not in changed and _dir_affects_graph(rel, vendor_parts):
                        changed.append(rel)
                    continue
                if rel not in changed and _affects_graph(rel, vendor_parts, exts):
                    changed.append(rel)
        return bool(changed), changed[:_DIRTY_SAMPLE_MAX], len(changed)
    except Exception:  # noqa: BLE001 — provenance is best-effort
        return None, [], 0


def _git_worktree_dirty(root: Path):
    """True / False / None (unknown) — the `dirty` of _git_worktree_changes. Never raises."""
    return _git_worktree_changes(root)[0]


# ---- is a git operation in progress? / the source fingerprint (auto-sync H3, M2) ------------------------------------
# A background refresh must never read a tree in the middle of a rebase, merge, cherry-pick, revert or bisect (the
# files hold conflict markers or a half-applied series), and "is the graph current?" must not mean "did HEAD move":
# a docs-only commit, a commit to another subtree, or committing work an earlier sync already read from the dirty
# tree all move HEAD without changing a single graph input. The source fingerprint is the set of graph-input files
# (the _affects_graph rule: extensions the extractors read + .gitignore, minus DeepMap's own artifacts, the vendor
# filter and an in-tree DEEPMAP_HOME) under the sync root with their content ids — the index blob of a clean file,
# `git hash-object` (clean filters / autocrlf applied, so a committed file hashes to its blob) of a changed or
# untracked one — captured BEFORE extraction, like the rest of _source_state. Equal fingerprints = the same graph
# inputs. Every git call runs with GIT_OPTIONAL_LOCKS=0 (_git_env).
_GIT_BUSY_PATHS = (("rebase-merge", "rebase"), ("rebase-apply", "rebase"), ("MERGE_HEAD", "merge"),
                   ("CHERRY_PICK_HEAD", "cherry-pick"), ("REVERT_HEAD", "revert"), ("BISECT_LOG", "bisect"),
                   ("sequencer", "sequence"))
_FINGERPRINT_MAX_HASH = 20_000      # changed/untracked files hashed at most; beyond it the fingerprint is unknown
_FINGERPRINT_MAX_DEPTH = 3          # nested repositories (submodules, an untracked repo in the tree) followed this deep


def git_busy(root) -> "str | None":
    """The git operation in progress in `root`'s repository — "rebase" | "merge" | "cherry-pick" | "revert" |
    "bisect" | "sequence" — by the marker paths git keeps while it runs (one `rev-parse --git-path` call), or None
    (none, not a git repo, or unknown). Unmerged index entries ("unmerged") are source_fingerprint's to report.
    Never raises."""
    git = shutil.which("git")
    if not git:
        return None
    try:
        args = []
        for name, _op in _GIT_BUSY_PATHS:
            args += ["--git-path", name]
        p = subprocess.run([git, *_git_longpaths(), "-C", str(root), "rev-parse", *args],
                           capture_output=True, timeout=30, env=_git_env())
        if p.returncode != 0:
            return None
        lines = (p.stdout or b"").decode("utf-8", "surrogateescape").splitlines()
        if len(lines) != len(_GIT_BUSY_PATHS):
            return None
        for line, (_name, op) in zip(lines, _GIT_BUSY_PATHS):
            path = Path(line.strip())
            if not path.is_absolute():
                path = Path(root) / path
            if os.path.lexists(str(path)):
                return op
        return None
    except Exception:  # noqa: BLE001
        return None


def _home_rel(root):
    """DEEPMAP_HOME relative to `root` (POSIX, with a trailing '/') when it lies inside it, else None."""
    try:
        home = Path(os.environ.get("DEEPMAP_HOME", Path.home() / ".deepmap")).resolve()
        return home.relative_to(Path(root).resolve()).as_posix() + "/"
    except (ValueError, OSError):
        return None


def _fingerprint_value(index: dict) -> str:
    import hashlib
    return hashlib.sha256("\n".join(f"{rel}\x00{sha}" for rel, sha in sorted(index.items()))
                          .encode("utf-8", "surrogateescape")).hexdigest()[:32]


def _fs_fingerprint(root: Path) -> dict:
    """The non-git fingerprint: _fs_content_hash's walk restricted to graph-input files (at most _PROVENANCE_MAX_FILES,
    a sorted prefix beyond — truncated). Never raises."""
    out = {"value": None, "kind": "fs", "head": None, "files": 0, "dirty": 0, "unmerged": 0, "git_busy": None,
           "truncated": False}
    try:
        vendor_parts, exts = _gitignore_dir_parts(root), _graph_input_exts()
        home_rel = _home_rel(root)
        files = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in _PROVENANCE_SKIP_DIRS]
            for fn in filenames:
                fp = Path(dirpath) / fn
                try:
                    if fp.is_symlink() or not fp.is_file():
                        continue
                except OSError:
                    continue
                rel = fp.relative_to(root).as_posix()
                if home_rel and (rel + "/").startswith(home_rel):
                    continue
                if _fingerprint_input(rel, vendor_parts, exts):
                    files.append((rel, fp))
        files.sort(key=lambda t: t[0])
        out["truncated"] = len(files) > _PROVENANCE_MAX_FILES
        index = {}
        for rel, fp in files[:_PROVENANCE_MAX_FILES]:
            try:
                index[rel] = extract_cache.hash_bytes(fp.read_bytes())
            except OSError:
                continue
        out["files"] = len(index)
        out["value"] = _fingerprint_value(index)
        return out
    except Exception:  # noqa: BLE001
        return out


def _nested_fingerprint(path: Path, depth: int) -> dict:
    """The fingerprint of a directory git lists as ONE entry — a submodule, an untracked nested repository — that
    the extraction walks into: its own source_fingerprint when it is a git work tree (it has a .git), else the
    plain-files one."""
    if depth > _FINGERPRINT_MAX_DEPTH:
        return {"value": None, "kind": None, "git_busy": None, "truncated": True}
    if os.path.lexists(str(path / ".git")):
        return source_fingerprint(path, _depth=depth)
    return _fs_fingerprint(path)


def source_fingerprint(root, *, _depth: int = 0) -> dict:
    """The graph inputs under `root` as a fingerprint (see the block comment above): {"value": 32 hex | None,
    "kind": "git" | "fs" | None, "head": HEAD sha | None, "files", "dirty" (changed/untracked/deleted inputs),
    "unmerged", "git_busy": None | "rebase" | "merge" | "cherry-pick" | "revert" | "bisect" | "sequence" |
    "unmerged", "truncated"}. `value` is None when it cannot be known (a git operation in progress, unmerged
    entries, git failed, more than _FINGERPRINT_MAX_HASH files to hash). A non-git tree gets the "fs" kind. Never
    raises.

    Its input set is at least what extraction reads: source files and the non-source graph inputs (_affects_graph),
    a root marker in `root` itself (_fingerprint_input), and every submodule / nested repository (both extraction
    walks descend into it): a gitlink counts with the commit the index records for it, and one git status lists as
    changed (new commits checked out, modified or untracked content) — or an untracked nested repository — with
    its own fingerprint (_nested_fingerprint, at most _FINGERPRINT_MAX_DEPTH deep; unknown there = unknown here)."""
    root = Path(root)
    out = {"value": None, "kind": None, "head": None, "files": 0, "dirty": 0, "unmerged": 0, "git_busy": None,
           "truncated": False}
    try:
        head = _git_head_sha(root)
        if head is None:
            return _fs_fingerprint(root)
        out.update(kind="git", head=head)
        busy = git_busy(root)
        if busy:
            out["git_busy"] = busy
            return out
        git = shutil.which("git")
        if not git:
            return out
        lp = _git_longpaths()
        ls = subprocess.run([git, *lp, "-C", str(root), "ls-files", "-s", "-z", "--", "."],
                            capture_output=True, timeout=60, env=_git_env())
        if ls.returncode != 0:
            return out
        vendor_parts, exts = _gitignore_dir_parts(root), _graph_input_exts()
        home_rel = _home_rel(root)

        def wanted(rel):
            return not (home_rel and (rel + "/").startswith(home_rel)) and _fingerprint_input(rel, vendor_parts, exts)

        def dir_wanted(rel):
            return not (home_rel and (rel + "/").startswith(home_rel)) and _dir_affects_graph(rel, vendor_parts)

        index, unmerged, links = {}, 0, set()
        for entry in (ls.stdout or b"").decode("utf-8", "surrogateescape").split("\0"):
            if not entry or "\t" not in entry:
                continue
            meta, rel = entry.split("\t", 1)
            parts = meta.split()
            if len(parts) != 3:
                continue
            rel = rel.replace("\\", "/")
            if parts[2] != "0":
                unmerged += 1
                continue
            if parts[0] == "160000":
                # a gitlink (submodule): the commit the index records pins what is checked out there — a submodule
                # bump changes it. The trailing "/" keeps the entry apart from any file key.
                if dir_wanted(rel):
                    links.add(rel)
                    index[rel + "/"] = "gitlink:" + parts[1]
                continue
            if wanted(rel):
                index[rel] = parts[1]
        if unmerged:
            out.update(git_busy="unmerged", unmerged=unmerged)
            return out
        st = subprocess.run([git, *lp, "-C", str(root), "status", "--porcelain=v1", "-z", "--untracked-files=all",
                             "--no-renames", "--", "."], capture_output=True, timeout=60, env=_git_env())
        pre = subprocess.run([git, *lp, "-C", str(root), "rev-parse", "--show-prefix"],
                             capture_output=True, timeout=30, env=_git_env())
        if st.returncode != 0 or pre.returncode != 0:
            return out
        prefix = pre.stdout.decode("utf-8", "surrogateescape").strip().replace("\\", "/")
        to_hash, dirty, nested = [], 0, []
        for entry in (st.stdout or b"").decode("utf-8", "surrogateescape").split("\0"):
            if len(entry) < 4 or entry[2] != " ":
                continue
            rel = entry[3:].replace("\\", "/")
            if prefix and rel.startswith(prefix):
                rel = rel[len(prefix):]
            path = root / rel.rstrip("/")
            if rel.endswith("/") or rel in links or path.is_dir():
                # one entry for a whole directory: a submodule git reports changed, or an untracked nested repository
                rel = rel.rstrip("/")
                if not rel or not dir_wanted(rel):
                    continue
                dirty += 1
                if not os.path.lexists(str(path)):
                    index.pop(rel + "/", None)    # a submodule removed from the work tree
                else:
                    nested.append(rel)
                continue
            if not wanted(rel):
                continue
            dirty += 1
            if not os.path.lexists(str(path)):
                index.pop(rel, None)              # deleted (staged or not)
            else:
                to_hash.append(rel)
        for rel in sorted(set(nested)):
            sub = _nested_fingerprint(root / rel, _depth + 1)
            if sub.get("git_busy"):
                out.update(git_busy=sub["git_busy"], dirty=dirty)
                return out                        # a git operation in progress inside it: the tree is mid-change
            if sub.get("value") is None:
                out.update(truncated=bool(sub.get("truncated")), dirty=dirty)
                return out                        # what it holds cannot be known: neither can this tree's
            index[rel + "/"] = "tree:" + str(sub["value"])
        to_hash = sorted(set(to_hash))
        if len(to_hash) > _FINGERPRINT_MAX_HASH:
            out.update(truncated=True, dirty=dirty)
            return out
        if to_hash:
            ho = subprocess.run([git, *lp, "-C", str(root), "hash-object", "--stdin-paths"],
                                input="\n".join(to_hash).encode("utf-8", "surrogateescape") + b"\n",
                                capture_output=True, timeout=120, env=_git_env())
            shas = (ho.stdout or b"").decode("ascii", "replace").split()
            if ho.returncode != 0 or len(shas) != len(to_hash):
                return out
            index.update(zip(to_hash, shas))
        out.update(value=_fingerprint_value(index), files=len(index), dirty=dirty)
        return out
    except Exception:  # noqa: BLE001 — provenance is best-effort
        return out


def _source_state(root: Path) -> dict:
    """The provenance of the tree an extraction is ABOUT to read, captured BEFORE extracting (Y4): the
    HEAD SHA + whether (and where) the work tree differs from it in a way that can change the graph, or
    for a non-git tree its content hash. The manifest used to read HEAD when it was written, at the END
    of a sync (after push, indexes and embeddings — minutes on a real repo), so a commit landing in
    between was reported as the graph's own ("fresh as of the last commit") while its code was missing
    from the graph. Auto-sync H3: also the source fingerprint ("fingerprint", source_fingerprint) and the time
    the capture started ("captured_utc" / "captured_epoch", read BEFORE the first git call) — the manifest's
    edits_seen_through: an edit after it is not in the graph, whenever the sync ends."""
    captured = time.time()
    state = {"captured_epoch": captured,
             "captured_utc": datetime.fromtimestamp(captured, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    sha = _git_head_sha(root)
    if sha is not None:
        dirty, sample, count = _git_worktree_changes(root)
        state.update({"sha": sha, "dirty": dirty, "dirty_paths_sample": sample, "dirty_paths_count": count,
                      "content_hash": None})
    else:
        state.update({"sha": None, "dirty": None, "dirty_paths_sample": [], "dirty_paths_count": 0,
                      "content_hash": _fs_content_hash(root)})
    state["fingerprint"] = source_fingerprint(root)
    return state


# A non-git tree still deserves provenance. These bound the fallback walk so a huge/binary tree can't
# blow up a one-time, non-git sync path (git repos never reach this — _git_head_sha short-circuits).
_PROVENANCE_SKIP_DIRS = {".git", ".hg", ".svn", ".deepmap", "node_modules", "__pycache__",
                         ".venv", "venv", "env", "dist", "build", "target", ".idea", ".vscode",
                         ".mypy_cache", ".pytest_cache", ".tox", ".gradle", "bin", "obj"}
_PROVENANCE_MAX_FILE_BYTES = 5_000_000     # skip a single over-sized/binary file (hash its size instead)
_PROVENANCE_MAX_FILES = 20_000             # bound total work; if exceeded we hash a sorted prefix + note it


def _fs_content_hash(root: Path):
    """A deterministic filesystem CONTENT-hash of the tree at `root`, for provenance when it is NOT a
    git repo. Folds `posix_relpath \\x00 <hash>` lines (sorted, POSIX-normalized so a tree hashes
    identically on Windows and Linux) into one digest via `extract_cache.hash_bytes`. Skips VCS/build/
    cache dirs; over-sized files contribute their size, not their bytes. Returns a 16-hex digest, or
    None if nothing hashable exists (an empty tree — stay silent, like the git-absent path). Never
    raises — provenance is best-effort, not a build precondition."""
    try:
        files = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in _PROVENANCE_SKIP_DIRS]
            for fn in filenames:
                fp = Path(dirpath) / fn
                try:
                    if fp.is_symlink() or not fp.is_file():
                        continue
                except OSError:
                    continue
                files.append(fp)
        # Sort by relpath BEFORE any cap so the digest is deterministic even when truncated.
        files.sort(key=lambda p: p.relative_to(root).as_posix())
        truncated = len(files) > _PROVENANCE_MAX_FILES
        entries = []
        for fp in files[:_PROVENANCE_MAX_FILES]:
            rel = fp.relative_to(root).as_posix()
            try:
                if fp.stat().st_size > _PROVENANCE_MAX_FILE_BYTES:
                    entries.append(f"{rel}\x00oversize:{fp.stat().st_size}")
                    continue
                data = fp.read_bytes()
            except OSError:
                continue
            entries.append(f"{rel}\x00{extract_cache.hash_bytes(data)}")
        if not entries:
            return None
        if truncated:
            print(f"[sync] provenance content-hash covers the first {_PROVENANCE_MAX_FILES} of "
                  f"{len(files)} files (non-git tree, bounded).", file=sys.stderr)
        return extract_cache.hash_bytes("\n".join(entries).encode("utf-8"))
    except Exception:  # noqa: BLE001 — provenance must never break a sync
        return None


def write_graph_build_manifest(root, *, n_nodes: int, n_edges: int, n_embedded: int,
                               extractor: str, verdict=None, reasons=None, coverage=None,
                               extraction=None, source_state=None, graph_engines=None,
                               graph_nodes=None, stale_cleanup_skipped=None, stale_kept=None,
                               build_recipe=None, sync_wall_s=None, embeddings_pending=None, call_linking=None):
    """Stamp <root>/.deepmap/graph_build.json with provenance (the git SHA the graph was built at, or
    a filesystem content-hash when `root` is not a git repo) + counts + extractor + a UTC timestamp.
    Returns the path on success, or None only if there is nothing to stamp (an empty/unreadable
    non-git tree) or the write failed. NEVER raises — a missing manifest just means the benchmark must
    fall back to DEEPMAP_GRAPH_SHA; it is provenance, not a build precondition.

    Provenance keys: a git repo records `built_at_sha` (40-hex) + `provenance:"git"`; a non-git tree
    records `built_at_content_hash` (16-hex) + `provenance:"content-hash"`. Consumers read
    `built_at_sha` via `.get()`, so the git-only SHA-pin/staleness paths are unaffected (they simply
    see no SHA for a content-hash manifest and fall back / fail closed as before).

    `verdict`/`reasons`/`coverage` (all optional, ADDITIVE — omitted when None) persist the sync-time
    honesty verdict so session-start surfaces (hooks, doctor/status) can read coverage health without
    Neo4j: the classifier's inputs exist only transiently at sync time, and Neo4j may be down at
    session start. A classify() failure upstream passes None and the manifest still writes.

    `extraction` (optional, ADDITIVE — `_rebuild_code`'s `.info`) records how the engine was chosen as
    top-level fields: `extractor_requested` (the DEEPMAP_EXTRACTOR mode), `extractor_fallback_reason`
    (only when auto fell back to ast, with graphify's cause), `graphify_version` (the engine version
    that ran), `graphify_failed_files`, `graphify_rescued_dirs`, `graphify_rescue_dropped` and
    `graphify_rescue_blocked` (engine-skipped dirs NOT re-included because that graphify version is
    outside graphify_extract.SUPPORTED_GRAPHIFY_SPEC) — each only when non-empty — whether graphify's
    self-calls were re-bound (`graphify_self_call_rebind`, + `graphify_self_calls_rebound` when any),
    whether imports bound by the engine-resolved target key (`graphify_resolved_imports`, with
    `graphify_imports_resolved` / `graphify_imports_by_name`; only when graphify's graph is the one
    kept) and a forced-graphify failure's `extractor_engine_error`. `extractor` stays the RESOLVED
    engine. A malformed value is left out, never an exception.

    `source_state` (`_source_state(root)` captured BEFORE extraction; sync() always passes it) is the
    provenance recorded (Y4 + ROUND-2 D4): a git tree ALWAYS records `built_at_sha` (HEAD when the
    extraction started) plus `dirty` (true / false / null = unknown) — did the work tree differ from that
    commit in a way that can change the graph — with `dirty_paths_sample` (at most _DIRTY_SAMPLE_MAX
    paths relative to `root`) and `dirty_paths_count`; `built_at_dirty` mirrors `dirty` for readers of
    the round-1 name. Staleness readers compare built_at_sha with HEAD (HEAD moved -> stale) and treat
    dirty as "possibly newer than the commit"; the benchmark SHA pin fails CLOSED on dirty (or unknown).
    Omitted -> computed now (direct callers).

    `graph_engines` (optional) lists the engines whose nodes the live graph holds after this sync —
    the memory the engine-downgrade prune guard reads back (see _read_prior_extractor).

    `stale_cleanup_skipped` (optional; round 7, T5#7) — the reason a requested stale prune was refused
    ("blast-radius", "engine-downgrade") — with `graph_nodes`, the graph's real node total then: `nodes`
    stays what THIS run pushed, so after a refused prune the manifest no longer reads as the whole graph
    (670 pushed while the graph held 1589).

    `deepmap_version` (ADDITIVE, fix-all r283) — the DeepMap version (.claude-plugin/plugin.json) of the
    code that wrote this manifest, i.e. that built the graph: _CODE_VERSION, read once when this module was
    imported. Omitted when unreadable. A manifest written before it existed has none, so a reader must
    treat an absent value as unknown, never as a match.

    Auto-sync additions (all ADDITIVE; absent = unknown): `source_fingerprint` + `source_fingerprint_kind` (the
    graph inputs when the extraction started — source_fingerprint, from source_state["fingerprint"]; only when
    known), `edits_seen_through_utc` / `edits_seen_through_epoch` (when that capture started: an edit after it is
    not in this graph), `build_recipe` (sync_status.make_build_recipe — how the graph was built; never a password),
    `sync_wall_s` (this pass: lock acquired -> manifest write), `stale_kept` (only when given: the stale nodes an
    evidence-only prune kept) and `embeddings_pending` (only when true: the embeddings were postponed). The file
    is written atomically (sync_status.atomic_write_text: temp + os.replace, Windows sharing-violation retry), its
    content (indent=2, sort_keys=True) as before.

    `call_linking` (slice S2, ADDITIVE; only when the typed-call pass ran) — typed_calls.link's stats as a
    `call_linking` block (_call_linking_block: the languages, shapes, per-language / per-shape counts, grades,
    replaced tuples, audit demotions, seconds; a failed run records only its error). Never in `_rebuild_code`'s
    `.info`, so the verdict and the golden gates never see it."""
    root = Path(root)
    if not isinstance(source_state, dict):
        source_state = _source_state(root)
    sha, dirty = source_state.get("sha"), source_state.get("dirty")
    content_hash = None if sha is not None else source_state.get("content_hash")
    if sha is None and content_hash is None:
        return None                              # nothing to stamp: a non-git tree with no hashable file
    try:
        d = root / ".deepmap"
        d.mkdir(parents=True, exist_ok=True)
        manifest = {
            "built_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "nodes": int(n_nodes),
            "calls_edges": int(n_edges),
            "embedded": int(n_embedded),
            "extractor": extractor,
            "provenance": "git" if sha is not None else "content-hash",
            "note": ("DeepMap graph-build provenance, written by build/sync.py after a successful "
                     "rebuild so the benchmark can verify the ON-arm graph SHA == the pinned SHA the "
                     "OFF arm greps (git repos); a filesystem content-hash stands in for non-git "
                     "trees. Safe to delete; regenerated on the next sync."),
        }
        if _CODE_VERSION:
            manifest["deepmap_version"] = str(_CODE_VERSION)[:100]   # which DeepMap built this graph
        if sha is not None:
            manifest["built_at_sha"] = sha
            state = dirty if isinstance(dirty, bool) else None
            manifest["dirty"] = manifest["built_at_dirty"] = state
            manifest["dirty_paths_sample"] = [str(x) for x in
                                              (source_state.get("dirty_paths_sample") or [])][:_DIRTY_SAMPLE_MAX]
            manifest["dirty_paths_count"] = int(source_state.get("dirty_paths_count") or 0)
        else:
            manifest["built_at_content_hash"] = content_hash
        if graph_engines is not None:
            manifest["graph_engines"] = sorted({str(e) for e in graph_engines if e})
        if stale_cleanup_skipped:
            manifest["stale_cleanup_skipped"] = str(stale_cleanup_skipped)
            gn = _as_count(graph_nodes)
            if gn is not None:
                manifest["graph_nodes"] = gn
        if verdict is not None:
            manifest["verdict"] = str(verdict)
        if reasons is not None:
            manifest["reasons"] = [str(r) for r in reasons]
        if coverage is not None:
            manifest["coverage"] = coverage
        if isinstance(extraction, dict):
            if extraction.get("requested"):
                manifest["extractor_requested"] = str(extraction["requested"])
            if extraction.get("fallback_reason"):
                manifest["extractor_fallback_reason"] = str(extraction["fallback_reason"])[:500]
            if extraction.get("graphify_version"):   # the engine version this build ran (E3 range guard)
                manifest["graphify_version"] = str(extraction["graphify_version"])[:100]
            for src, dst in (("failed_files", "graphify_failed_files"),
                             ("rescue_dropped", "graphify_rescue_dropped"),
                             ("self_calls_rebound", "graphify_self_calls_rebound")):
                n = _as_count(extraction.get(src))
                if n:                                # recorded only when non-zero (as before)
                    manifest[dst] = n
            # round 7: the engine-output corrections graphify_extract counts (graphify_<counter>, non-zero)
            for src in getattr(graphify_extract, "_ROUND7_COUNTERS", ()) or (
                    "calls_filtered", "imports_bare_resolved", "supplemental_nodes", "overloads_moved",
                    "file_ids_split", "calls_plus"):
                n = _as_count(extraction.get(src))
                if n:
                    manifest[f"graphify_{src}"] = n
            for src, dst in (("rescued_dirs", "graphify_rescued_dirs"),
                             ("rescue_blocked", "graphify_rescue_blocked")):   # blocked: unsupported engine
                dirs = _as_str_list(extraction.get(src))
                if dirs:
                    manifest[dst] = dirs
            if "self_call_rebind" in extraction:     # G5: whether graphify's self-calls were re-bound
                manifest["graphify_self_call_rebind"] = bool(extraction["self_call_rebind"])
            if "resolved_imports" in extraction:     # present only when graphify's graph is the one kept
                manifest["graphify_resolved_imports"] = bool(extraction["resolved_imports"])
                for src, dst in (("imports_resolved", "graphify_imports_resolved"),
                                 ("imports_by_name", "graphify_imports_by_name")):
                    n = _as_count(extraction.get(src), default=0)
                    if n is not None:
                        manifest[dst] = n
            if extraction.get("engine_error"):       # a forced-graphify run that failed (no fallback)
                manifest["extractor_engine_error"] = str(extraction["engine_error"])[:500]
        # auto-sync (H3): what the graph was built FROM, and when that was captured
        fp = source_state.get("fingerprint")
        if isinstance(fp, dict) and isinstance(fp.get("value"), str) and fp.get("value"):
            manifest["source_fingerprint"] = fp["value"]
            if fp.get("kind"):
                manifest["source_fingerprint_kind"] = str(fp["kind"])
        if isinstance(source_state.get("captured_utc"), str):
            manifest["edits_seen_through_utc"] = source_state["captured_utc"]
        try:
            if source_state.get("captured_epoch") is not None:
                manifest["edits_seen_through_epoch"] = float(source_state["captured_epoch"])
        except (TypeError, ValueError):
            pass
        if isinstance(build_recipe, dict):
            manifest["build_recipe"] = build_recipe
        try:
            if sync_wall_s is not None and not isinstance(sync_wall_s, bool):
                manifest["sync_wall_s"] = round(max(0.0, float(sync_wall_s)), 1)
        except (TypeError, ValueError):
            pass
        sk = _as_count(stale_kept)
        if stale_kept is not None and sk is not None:
            manifest["stale_kept"] = sk
        if embeddings_pending is True:
            manifest["embeddings_pending"] = True
        block = _call_linking_block(call_linking)
        if block is not None:
            manifest["call_linking"] = block      # S2: the typed-call pass's stats (only when it ran)
        path = d / "graph_build.json"
        # M10: atomic (temp + os.replace, a Windows sharing-violation retry) — hooks and the background refresh read
        # this file while a sync rewrites it; a reader must never see half of it.
        if not sync_status.atomic_write_text(path, json.dumps(manifest, indent=2, sort_keys=True)):
            return None
        return path
    except (OSError, TypeError, ValueError):     # never raises: provenance, not a build precondition
        return None


def _as_count(value, default=None):
    """A non-negative int from an extraction count; `default` when it is absent (None); None when it is
    not a count (a bool, a non-number, negative). Never raises — the manifest must always write."""
    if value is None:
        return default
    if isinstance(value, bool):
        return None
    try:
        n = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return n if n >= 0 else None


def _as_str_list(value) -> list:
    """A list of strings from an extraction dir list (a bare string is one dir, not its characters)."""
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, (list, tuple, set, frozenset)):
        return [str(v) for v in value if v is not None and str(v)]
    return []


# ---- auto-gitignore DeepMap's regenerated artifact directories --------------
# `deepmap sync` drops two artifact trees into the USER's project: <root>/graphify-out/ (the graphify
# subprocess's cache — graph.json + hundreds of cache/ast/v*/*.json) and <root>/.deepmap/ (DeepMap's
# own runtime state). Neither is source; both are fully regenerated on every sync. Without a .gitignore
# the user gets hundreds of untracked files cluttering `git status` (a real field report: 799 files).
# DeepMap's OWN repo gitignores both, so we do the same for the user — with a SELF-IGNORING nested
# .gitignore (body `*`) inside each dir, so the whole dir (including the .gitignore itself) is invisible
# to git with ZERO new entries. Nested (not a root-.gitignore append) so we never touch the user's own
# tracked file, and it works identically for a repo-root sync and a subdirectory sync.
_ARTIFACT_GI_SENTINEL = "# DeepMap: regenerated artifact directory — safe to delete, do not commit."
_ARTIFACT_GI_BODY = (_ARTIFACT_GI_SENTINEL + "\n"
                     "# Auto-created by `deepmap sync` so these files don't clutter `git status`.\n"
                     "*\n")


def _auto_gitignore_enabled() -> bool:
    """Default ON. Opt out with DEEPMAP_AUTO_GITIGNORE=0/false/no/off (a user who wants to manage their
    own .gitignore, or commit the artifacts). Inverse of the default-OFF flags (_include_vendor_enabled)
    because unset/empty means ENABLED here."""
    return os.environ.get("DEEPMAP_AUTO_GITIGNORE", "").strip().lower() not in {"0", "false", "no", "off"}


def _dir_has_tracked_files(d: Path) -> bool:
    """Best-effort: True iff git reports any TRACKED file under `d`, so we never drop a `*` .gitignore
    over a directory the user intentionally tracks deliverables in (F3c — e.g. a project that commits
    graph_enriched.json under graphify-out/; a `*` ignore would silently hide any NEW file added there).
    git absent / not a repo / any error -> False (proceed exactly as before). Never raises.

    The output is read as BYTES — only its emptiness matters. Decoding it in the locale code page
    (text=True) raised on a non-ASCII tracked file name on Windows, which the except below turned into
    "nothing tracked" — so the guard let a `*` .gitignore cover the user's tracked files."""
    try:
        r = subprocess.run(["git", *_git_longpaths(), "ls-files", "--", d.name],
                           cwd=str(d.parent), capture_output=True, timeout=10, env=_git_env())
        return r.returncode == 0 and bool((r.stdout or b"").strip())
    except Exception:
        return False


def _ensure_artifact_gitignore(dir_path) -> bool:
    """Best-effort: drop a self-ignoring `.gitignore` (`*`) into a DeepMap artifact directory so its
    regenerated contents never pollute the user's `git status`. Writes only when the file is ABSENT or
    already DeepMap-owned (first line == our sentinel) — NEVER clobbers a user/third-party `.gitignore`,
    and NEVER when the dir already holds git-tracked files (F3c). LF newlines (deterministic,
    Windows-safe). NEVER raises. Returns True iff a DeepMap-owned ignore file is present afterward, else
    False (a non-dir, a foreign .gitignore, a user-tracked dir, or a write error)."""
    try:
        d = Path(dir_path)
        if not d.is_dir():
            return False
        if _dir_has_tracked_files(d):     # F3c: the user tracks deliverables here -> never `*`-ignore it
            return False
        gi = d / ".gitignore"
        if gi.exists():
            existing = gi.read_text(encoding="utf-8", errors="replace")
            first = existing.splitlines()[:1]
            if not first or first[0].strip() != _ARTIFACT_GI_SENTINEL:
                return False                      # a foreign .gitignore -> leave it untouched
            if existing == _ARTIFACT_GI_BODY:
                return True                       # already ours and current -> no rewrite
        with open(gi, "w", encoding="utf-8", newline="\n") as fh:   # explicit LF (works on 3.6+)
            fh.write(_ARTIFACT_GI_BODY)
        return True
    except OSError:
        return False


# ---- which directory a sync extracts (Y2) -----------------------------------
def _sync_root(target: Path) -> Path:
    """The directory a sync of `target` extracts and keeps its .deepmap/ state in: when `target` is a
    subdirectory inside a git work tree and a directory above it (up to the git toplevel) has already
    been synced — it holds .deepmap/graph_build.json — the OUTERMOST such directory; else `target`.

    Node keys and source_file are relative to the extracted directory, and one repository is one Neo4j
    graph (its instance, owner and .mcp.json are keyed on the git toplevel). A subdirectory sync of a
    repository synced at its root therefore re-pushed every node under it with subdirectory-relative
    keys — duplicates carrying source_file paths that do not exist from the root — and, seeing only the
    subtree, could not rebuild the edges leaving it (2026-09 live validation #51). The outermost synced
    directory wins, so a manifest a past subdirectory sync left behind does not pin the subdirectory.
    A subdirectory nothing above has synced (a sub-project set up on its own) and a non-git directory
    (its own project for routing) are synced as given."""
    top = Path(_project_identity(target))
    if not _is_subdir(target, top):
        return target
    root = target
    for d in target.parents:
        if not (_is_subdir(d, top) or os.path.normcase(str(d)) == os.path.normcase(str(top))):
            break                                # never look above the repository
        if (d / ".deepmap" / "graph_build.json").is_file():
            root = d
    return root


# ---- a sync that finds the lock held is queued, not lost (Y3) ----------------
# The per-root lock serialises same-project syncs, and a sync that found it held used to skip, promising
# that "the running sync will complete the update". It could not: the holder extracted once, at its
# start, so a commit made after that (back-to-back commits fire the detached post-commit hook twice) was
# missing from the graph until someone synced again — while the manifest called the graph fresh. Now the
# skipper leaves a re-run request in .deepmap/ and the holder honours it with another pass before it
# returns. Lost wake-ups are closed on both sides: the skipper retries the lock once after writing the
# request (the holder may have released it in between), and the holder looks for a request only AFTER
# releasing the lock, re-acquiring it to run again (if another sync grabbed it first, that one extracts
# after the request and consumes it). `deepmap enrich` holds this lock too, briefly, while it extracts
# (build/enrich.py) — it never syncs, so a request queued behind it waits for the NEXT sync (enrich names
# it). The skipper cannot tell the two holders apart, so its message names both.
_RERUN_REQUEST = "sync.rerun"
_MAX_SYNC_PASSES = 3        # passes per sync() call; a request still pending then waits for the next sync


def _rerun_request_path(root: Path) -> Path:
    return Path(root) / ".deepmap" / _RERUN_REQUEST


def _request_rerun(root: Path, *, cleanup_stale: bool, force_prune: bool, prune_policy=None,
                   origin: str = "manual", trigger=None, ts=None) -> bool:
    """Queue a re-run behind whoever holds this project's lock: a sync performs it before it returns;
    `deepmap enrich` (which holds the lock only while it extracts) never syncs, so the next sync does. The
    flags OR-merge with a request already queued (a skipped /deep-map:refresh keeps its --stale-cleanup).
    Written atomically. False when it could not be written. Never raises.

    Auto-sync (H4): the request also carries "prune_policy" (none < evidence < full; a manual request writes
    "full" when it asked for --stale-cleanup, else "none"), "origin" (manual | unattended) and "trigger". Merged
    with a queued request: prune_policy = the max of both, origin "manual" if either is (a request an older
    DeepMap wrote counts as manual), trigger of the winning origin. A request DeepMap's background refresh queues
    (origin "unattended") ALWAYS writes cleanup_stale false — an older DeepMap holder ORs only cleanup_stale into
    its own run and would turn an evidence-only request into a full prune. `ts`: the time the request was first
    queued (a request put back keeps its own; default now)."""
    path = _rerun_request_path(root)
    try:
        prev = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, ValueError):
        prev = {}
    prev = prev if isinstance(prev, dict) else {}
    origin = "unattended" if origin == "unattended" else "manual"
    if origin == "unattended":
        cleanup_stale = False
    own = prune_policy if prune_policy in _POLICY_RANK else ("full" if cleanup_stale else "none")
    policy = _effective_policy(own, prev) if prev else own
    prev_origin = ("unattended" if prev.get("origin") == "unattended" else "manual") if prev else None
    merged_origin = "manual" if "manual" in (origin, prev_origin) else "unattended"
    try:
        when = float(ts) if ts is not None and not isinstance(ts, bool) else time.time()
    except (TypeError, ValueError):
        when = time.time()
    request = {"ts": when, "pid": os.getpid(),
               "cleanup_stale": bool(cleanup_stale or prev.get("cleanup_stale")),
               "force_prune": bool(force_prune or prev.get("force_prune")),
               "prune_policy": policy, "origin": merged_origin,
               "trigger": (trigger if merged_origin == origin else prev.get("trigger"))}
    tmp = path.with_name(f"{_RERUN_REQUEST}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(request), encoding="utf-8")
        os.replace(tmp, path)
        return True
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        return False


def _peek_rerun_request(root: Path):
    """The queued re-run request (a dict) WITHOUT claiming it — None when nothing is queued or it cannot be read.
    Never raises."""
    try:
        path = _rerun_request_path(root)
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _is_user_request(request) -> bool:
    """True iff `request` (a queued re-run, claimed or peeked) is one a user's sync queued — origin "manual", or no
    origin at all (an older DeepMap's). DeepMap's background refresh never runs such a request (H1/H4)."""
    return isinstance(request, dict) and bool(request) and request.get("origin") != "unattended"


def _take_rerun_request(root: Path):
    """Claim and remove the queued request. Called while holding the lock and BEFORE extracting, so the
    extraction that follows covers whatever the request was about. Returns its flags (a dict), or None
    when nothing is queued. Claimed by rename, so a request written after the claim stays queued."""
    path = _rerun_request_path(root)
    if not path.is_file():
        return None
    claimed = path.with_name(f"{_RERUN_REQUEST}.{os.getpid()}.claimed")
    try:
        os.replace(path, claimed)
    except OSError:
        return None
    try:
        data = json.loads(claimed.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    try:
        claimed.unlink()
    except OSError:
        pass
    return data if isinstance(data, dict) else {}


def _graph_holds_nodes(driver):
    """_graph_has_nodes, best-effort: True / False when the count was READ, None when it could not be (a
    dropped connection, a transient database error). None is UNKNOWN, never "empty": _sync_pass fails safe on
    it — it keeps the engine memory (so the engine-downgrade refusal still applies) and a 0-node run keeps the
    graph-build manifest (and the graph_engines in it) exactly as for a kept graph. It used to map an error to
    False, which _sync_pass read as an EMPTY graph: the memory was cleared, an ast fallback was pushed next to
    graphify's nodes and graph_engines was rewritten without graphify (ISS-build:017). Never raises."""
    try:
        return bool(_graph_has_nodes(driver))
    except Exception:
        return None


def _graph_node_count(driver):
    """How many real (non-meta) nodes the graph holds, or None when that can't be read. Best-effort,
    for wording only (the size of a graph a 0-node run left in place); never raises."""
    try:
        with driver.session() as s:
            rows = list(s.run("MATCH (n) WHERE NOT n:_DeepMapMeta RETURN count(n) AS c"))
        return int(rows[0]["c"]) if rows and rows[0]["c"] is not None else None
    except Exception:
        return None


def _downgrade_message(extraction, *, cleanup_stale=False, force_prune=False, root_ok=True) -> str:
    """The engine-DOWNGRADE refusal (round 7: the PUSH is refused, not only the prune — pushing ast
    nodes next to graphify's put a second copy of every Python symbol in the graph, since the two engines
    key nodes differently), worded for how this run came to be 'ast': FORCED by DEEPMAP_EXTRACTOR=ast
    (graphify works; the user chose ast — --stale-cleanup --force-prune is the deliberate engine switch),
    or an automatic FALLBACK (graphify broke — fix it). The flags explain a switch that was asked for
    but can't happen here."""
    info = extraction if isinstance(extraction, dict) else {}
    requested = str(info.get("requested") or os.environ.get("DEEPMAP_EXTRACTOR", "")).strip().lower()
    kept = ("Nothing was pushed: the existing graph and its graph-build manifest were kept unchanged "
            "(the graph may now be stale).")
    if force_prune and not cleanup_stale:
        note = (" (--force-prune alone does not switch engines: without --stale-cleanup the graph would "
                "hold both engines' nodes.)")
    elif force_prune and not root_ok:
        note = (" (this directory has no repo-root marker, so the switch's graph-wide prune cannot run "
                "here — run it from the repository root, or drop a .deepmap-root file there.)")
    else:
        note = ""
    if requested == "ast":
        return ("[deepmap sync] SKIPPED — engine DOWNGRADE: the graph holds nodes built by 'graphify', and "
                "this run was forced to the Python-only 'ast' extractor (DEEPMAP_EXTRACTOR=ast). Pushing its "
                "nodes would add a second copy of every Python symbol (the engines key nodes differently), "
                "and 'ast' cannot recreate the non-Python code. " + kept + " To switch engines "
                "deliberately, re-run with --stale-cleanup --force-prune (the graph then holds only what "
                "'ast' extracts); to keep graphify's graph, unset DEEPMAP_EXTRACTOR and re-sync." + note)
    cause = info.get("fallback_reason")
    if info.get("timed_out"):
        # Round 11 (M1): graphify is not broken — it ran out of its time budget (a large repo, a loaded machine).
        # The remedy is more time, with a concrete value: twice the budget that ran out.
        try:
            used = int(info.get("timeout_s") or 0)
        except (TypeError, ValueError):
            used = 0
        more = (f"{graphify_extract.ENV_GRAPHIFY_TIMEOUT}="
                f"{min(graphify_extract.GRAPHIFY_TIMEOUT_MAX_S, 2 * used) if used else 3600}")
        fix = (f" graphify ran out of time — it is not broken: re-sync with a larger budget ({more}; seconds, up "
               f"to {graphify_extract.GRAPHIFY_TIMEOUT_MAX_S}), ideally when the machine is less busy;")
    else:
        fix = " Fix the graphify install (or set DEEPMAP_EXTRACTOR=graphify) and re-sync;"
    return ("[deepmap sync] SKIPPED — engine DOWNGRADE: the graph was built with 'graphify' but this run "
            "fell back automatically to 'ast'"
            + (f" ({str(cause)[:200]})" if cause else "")
            + ", which cannot recreate the graphify-extracted nodes, and pushing its nodes would add a "
            "second copy of every Python symbol. " + kept + fix + " to switch to 'ast' deliberately, re-run "
            "with --stale-cleanup --force-prune." + note)


last_verdict = None   # this process's most recent sync() verdict (READY/DEGRADED/FAILED) — the CLI exits
                      # non-zero on FAILED even when the run kept the previous manifest (Y5)

# Round 11 (M1): a sync that RAN but pushed nothing — the previous graph stays in place, unchanged and possibly
# stale — used to exit 0 like a real refresh, so /deep-map:refresh, scripts and the git hook read success over a
# stale graph (the field case: graphify timed out on a loaded box, sync fell back to ast, and the engine-downgrade
# guard refused the push). `last_not_pushed` names why (None when this run pushed, or needed no push of its own);
# `deepmap_cli.py sync` and this module's own __main__ then exit SYNC_NOT_PUSHED_RC — distinct from 1 (refused /
# failed / verdict FAILED: no usable graph from this run) and 3 (Neo4j unreachable): the kept graph still answers.
SYNC_NOT_PUSHED_RC = 4
last_not_pushed = None


# Auto-sync: the record of how this process's last sync() call ended — the sync_last.json record of its last pass
# when a pass ran (or the background refresh yielded), else a small dict naming why none did: {"outcome": "queued"},
# {"outcome": "busy-unqueued" | "busy-enrich" | "route-problem" | "preempt-timeout", "reason": ...}. DeepMap's
# background refresh (build/auto_sync.py) maps it to its own outcome; nobody else needs it.
last_outcome = None

# A refusal because the database holds a graph this project cannot claim (sync_last.json outcome "refused"): another
# project's (foreign / legacy / base claim), or — for DeepMap's background refresh only — a graph with no recorded
# owner ("unowned": it never adopts a database it cannot prove is this project's).
_REFUSAL_KINDS_FOREIGN = ("foreign-owner", "legacy-owner", "base-claim", "unowned")


def _one_line(text, limit: int = 300):
    """`text` on one line, at most `limit` characters (None stays None)."""
    if text is None:
        return None
    s = " ".join(str(text).split())
    return s if len(s) <= limit else s[:limit - 1] + "…"


def _is_db_error(exc) -> bool:
    """A Neo4j / driver / OS error (the CLI's exit 3 class), as opposed to a code bug."""
    if isinstance(exc, OSError):
        return True
    try:
        from neo4j.exceptions import DriverError, Neo4jError
        return isinstance(exc, (Neo4jError, DriverError))
    except Exception:  # noqa: BLE001 — no driver installed: not a driver error
        return False


def _utc_now(epoch=None) -> str:
    t = time.time() if epoch is None else epoch
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _preempt_background(root: Path, holder: dict):
    """A user's sync found DeepMap's background refresh holding this project's lock (auto-sync H1): ask it to stop
    at its next safe point and wait for the lock (sync_status.preempt_wait, at most PREEMPT_WAIT_S), so this sync
    runs in its own process — its own environment, flags, full prune and verdict — instead of being queued into
    the background refresh's run. Returns (lock, status): "acquired" (run now), "fallback" (another user sync took
    the lock first: the usual queue path applies) or "timeout" (last_not_pushed / last_outcome set: nothing
    synced, the CLI exits 4)."""
    global last_not_pushed, last_outcome
    wait_min = int(sync_status.PREEMPT_WAIT_S // 60)
    try:
        ago = sync_status.duration_words(time.time() - float(holder.get("started_epoch")))
    except (TypeError, ValueError):
        ago = "a while"
    print(sync_status.PREEMPT_START.format(ago=ago, phase=sync_status.phase_words(holder.get("phase")),
                                           wait=wait_min), file=sys.stderr)

    def say(_kind, **fmt):
        print(sync_status.PREEMPT_PROGRESS.format(**fmt), file=sys.stderr)

    lock, status, phase = sync_status.preempt_wait(root, acquire=lambda: _acquire_sync_lock(root), by="sync",
                                                   holder=holder, say=say)
    if status == "acquired":
        print(sync_status.PREEMPT_ACQUIRED, file=sys.stderr)
    elif status == "timeout":
        last_not_pushed = sync_status.PREEMPT_TIMEOUT.format(wait=wait_min,
                                                             phase=sync_status.phase_words(phase))
        last_outcome = {"outcome": "preempt-timeout", "reason": last_not_pushed}
    return lock, status


def _recipe_route_source(root, source, uri=None):
    """The route source the manifest's build_recipe records. An "env" route (NEO4J_URI) whose URI IS this project's
    own managed instance — port_registry.resolve_neo4j_route(honor_env=False) says "registry" / "instance-conf", and
    both name the same endpoint after _instance_lock_key's normalisation — is recorded as that managed source,
    whoever set NEO4J_URI: setup's in-process sync (provision injects the registered pair; provision runs as
    __main__, so no module named "provision" can say the URI is its injection) or an operator pointing it at the
    project's own instance. Both write the database the background refresh resolves without NEO4J_URI, so it sees
    the same route. Any other "env" URI (a BYO / shared instance) stays "env". Decided by URI only, never by which
    modules are loaded. Never raises."""
    try:
        if source == "env" and uri:
            import port_registry
            managed = port_registry.resolve_neo4j_route(root, honor_env=False)
            if (not managed.problem and managed.uri and managed.source in ("registry", "instance-conf")
                    and _instance_lock_key(sync_status.strip_userinfo(managed.uri))
                    == _instance_lock_key(sync_status.strip_userinfo(uri))):
                return managed.source
    except Exception:  # noqa: BLE001
        pass
    return source


def _holder_start(root: Path, run: dict, pass_no: int, unattended) -> None:
    """Right after this sync took the project's lock: write its holder record (sync_running.json) and start the
    background refresh's watcher. Best-effort: a record that cannot be written changes nothing."""
    try:
        sync_status.write_running(root, {
            "schema": sync_status.SCHEMA, "id": run["id"], "origin": run["origin"], "trigger": run["trigger"],
            "session": run["session"], "started_utc": run["started_utc"], "started_epoch": run["started_epoch"],
            "pass": pass_no, "phase": "start", "head_at_start": None, "source_fingerprint": None,
            "deepmap_version": _CODE_VERSION})
    except Exception:  # noqa: BLE001
        pass
    if unattended is not None:
        unattended.begin()


def _pass_record(run: dict, passes: int, info: dict, ok, exc) -> dict:
    """The sync_last.json record of the pass that just ended (spec §2.6.3). `rc` is what sync._main /
    deepmap_cli.cmd_sync would exit with; an exception escaping the pass is outcome "error", rc 3 for a database /
    OS error, else 1."""
    now = time.time()
    info = info if isinstance(info, dict) else {}
    extra = {}
    if exc is not None:
        outcome, rc = "error", (3 if _is_db_error(exc) else 1)
        reason = f"{type(exc).__name__}: {exc}"
    elif info.get("yielded"):
        # sync_status.yield_fields: "preempted" rc 0 (a user's sync runs next) or "busy-enrich" rc 4 (only enrich
        # waited: nothing will refresh the graph)
        outcome = info.get("yield_outcome") or "preempted"
        rc = info.get("yield_rc") if isinstance(info.get("yield_rc"), int) else 0
        reason = info.get("reason") or "a sync the user started was waiting"
        extra["preempted_by"] = list(info.get("preempted_by") or [])
    elif info.get("user_request_pending"):
        # a re-run a user's sync queued, and no user's sync waiting to run it: nothing synced, the graph kept
        outcome, rc = "user-request-pending", SYNC_NOT_PUSHED_RC
        reason = info.get("reason") or sync_status.USER_REQUEST_REASON
    else:
        rc = 1 if (not ok or last_verdict == "FAILED") else (SYNC_NOT_PUSHED_RC if last_not_pushed else 0)
        reason = info.get("reason")
        if not ok:
            outcome = "refused" if info.get("refusal_kind") in _REFUSAL_KINDS_FOREIGN else "failed"
        elif last_not_pushed:
            outcome = "not-pushed"
            reason = last_not_pushed
        elif last_verdict == "FAILED":
            outcome = "failed"
            reason = reason or ((info.get("reasons") or [None])[0])
        else:
            outcome = "synced"
    return sync_status.last_record(
        id=run["id"], origin=run["origin"], trigger=run["trigger"], pid=os.getpid(),
        started_utc=run["started_utc"], finished_utc=_utc_now(now), finished_epoch=now,
        wall_s=round(now - run["started_epoch"], 3), passes=passes, rc=rc, outcome=outcome,
        not_pushed_kind=(info.get("not_pushed_kind") if outcome == "not-pushed" else None),
        veto=info.get("veto"), engine_timed_out=bool(info.get("engine_timed_out")),
        refusal_kind=info.get("refusal_kind"), reason=_one_line(reason),
        verdict=(last_verdict if outcome != "error" else None),
        reasons=[_one_line(r) for r in (info.get("reasons") or [])][:3],
        built_at_sha=info.get("built_at_sha"), dirty=info.get("dirty"),
        source_fingerprint=info.get("source_fingerprint"), pushed_nodes=int(info.get("pushed_nodes") or 0),
        pruned=int(info.get("pruned") or 0), prune_policy=info.get("prune_policy") or run.get("policy"),
        stale_cleanup_skipped=info.get("stale_cleanup_skipped"), stale_kept=int(info.get("stale_kept") or 0),
        embeddings_deferred=bool(info.get("embeddings_deferred")), deepmap_version=_CODE_VERSION, **extra)


def _holder_end(root: Path, run: dict, passes: int, info: dict, ok, exc, unattended, wrote: bool) -> None:
    """Before this sync releases the project's lock: stop the watcher, record how the pass ended
    (sync_last.json, last_outcome — only when a pass ran, the background refresh yielded or it left a user's request
    for a user's sync) and remove the holder
    record. Never raises."""
    global last_outcome
    try:
        if unattended is not None:
            unattended.end()
    except Exception:  # noqa: BLE001
        pass
    if wrote:
        try:
            rec = _pass_record(run, passes, info, ok, exc)
            last_outcome = rec
            sync_status.write_last(root, rec)
        except Exception:  # noqa: BLE001 — a record never changes a sync's result
            pass
    try:
        sync_status.clear_running(root)
    except Exception:  # noqa: BLE001
        pass


def sync(workspace_root, *, cleanup_stale: bool = False, switch_project: bool = False,
         force_prune: bool = False, driver=None, prune_policy=None, unattended=None) -> bool:
    """Rebuild the graph from `workspace_root` and push -> index -> embed into Neo4j.

    Returns True on success. Mirrors the frozen pipeline order. Pass an existing `driver` or
    set NEO4J_URI/NEO4J_PASSWORD (auth user 'neo4j'). `switch_project` authorises REPLACING another
    project's graph on the shared instance (a foreign owner is otherwise refused); `cleanup_stale`
    only prunes orphaned nodes WITHIN the current owner — it no longer authorises a cross-owner wipe
    (P1-6: the two concerns were decoupled so `/deep-map:refresh` can never silently switch owners).

    The Neo4j instance is THIS project's (ROUND-2 D1, _resolve_route): an explicit NEO4J_URI, else
    its own registered instance. With no safe route (an unreadable registry, or no instance of its own
    while another project owns bolt 7687) it returns False before touching anything. On the LEGACY 7687
    route (no instance of its own, nothing else registered there) the graph is used only when it is
    provably this project's or empty (verify_legacy_owner) — --switch-project does not override that.

    A subdirectory of a git repository already synced higher up syncs that directory instead (see
    _sync_root, Y2). A sync that finds this project's lock held — by another sync, or by `deepmap enrich`
    while it extracts — queues a re-run and returns True: a running sync honours the request before it
    returns (Y3); enrich never syncs, so after it the next sync does. `last_verdict` is set to this run's
    verdict, and `last_not_pushed` to why this run pushed nothing over the graph it left in place (the
    engine-downgrade refusal; another sync or enrich run holding this project's lock with no re-run
    queued; another sync holding the shared instance's) — None otherwise (round 11, M1).

    Auto-sync (DeepMap's background refresh, build/auto_sync.py):
      * `prune_policy` — None (today's rule: "full" with cleanup_stale, else "none") | "none" | "evidence" (the
        evidence-only prune, _plan_prune_evidence) | "full"; another value -> ValueError. A claimed re-run request
        raises it to the max of both (none < evidence < full), monotonic across the passes of one call.
      * `unattended` — a sync_status.UnattendedControl: this is the background refresh. It never switches
        projects, forces or fully prunes (-> ValueError; cleanup_stale is ignored; a claimed request's
        cleanup_stale / force_prune / "full" are capped to the evidence prune), yields to any sync a user starts
        (preemption: it stops at its next safe point and pushes nothing — recorded "preempted", or "busy-enrich"
        rc 4 when only a deepmap enrich run waited: sync_status.yield_fields), queues an "unattended" re-run request
        when it finds the lock held (none behind a deepmap enrich run: last_outcome "busy-enrich"), never adopts a
        graph with no recorded owner (refusal_kind "unowned"), and never runs — or claims — a request a user's
        sync queued: with no user's sync waiting for the lock it syncs nothing and records "user-request-pending"
        (rc 4; /deep-map:refresh runs the request), and after a pass of its own it stops, keeping that pass's
        record.
      * A user's sync (unattended None) that finds the background refresh holding the lock no longer queues behind
        it: it asks it to stop and waits (at most sync_status.PREEMPT_WAIT_S), then runs itself; when it does not
        stop in time, nothing is synced and last_not_pushed says so (the CLI exits 4). Any other holder: as before.
      * Every pass is recorded: .deepmap/sync_running.json while it holds the lock, .deepmap/sync_last.json when
        it ends; `last_outcome` (module global) is that record, or why no pass ran. A record that cannot be
        written never changes the result."""
    global last_verdict, last_not_pushed, last_outcome
    last_verdict = None
    last_not_pushed = None
    last_outcome = None
    if prune_policy is not None and prune_policy not in _POLICY_RANK:
        raise ValueError(f"unknown prune_policy {prune_policy!r} (expected none | evidence | full)")
    if unattended is not None:
        if switch_project or force_prune:
            raise ValueError("an unattended (background) sync never switches projects or forces a prune")
        if prune_policy is None:
            prune_policy = getattr(unattended, "prune_policy", None) or "evidence"
        if prune_policy == "full":
            raise ValueError("an unattended (background) sync never runs the full prune")
        cleanup_stale = False
    policy = prune_policy if prune_policy is not None else ("full" if cleanup_stale else "none")
    requested = Path(workspace_root).resolve()
    root = _sync_root(requested)
    if root != requested:
        print(f"[deepmap sync] {requested} is inside {root}, which DeepMap syncs as a whole — syncing "
              f"that instead (the graph keys every node relative to it; syncing only the subdirectory "
              f"would duplicate every node under it)", file=sys.stderr)
    # Resolve THIS project's Neo4j instance (D1, the one routing rule — see _resolve_route): used for
    # the driver AND the per-instance write lock, so the git-hook sync path (which calls sync directly,
    # without provision's env injection) still reaches the right instance. No safe route -> fail loud
    # before anything is locked, read or written (never a guessed 7687).
    route = _resolve_route(root)
    neo4j_uri, legacy_route, route_problem = route
    route_source = _recipe_route_source(root, getattr(route, "source", None), neo4j_uri)
    if route_problem:
        print(f"[deepmap sync] cannot sync {root}: {route_problem}", file=sys.stderr)
        last_outcome = {"outcome": "route-problem", "reason": _one_line(route_problem)}
        return False
    lock = _acquire_sync_lock(root)
    if lock is None and unattended is not None:
        # DeepMap's background refresh missed the lock: a deepmap enrich run never syncs, so nothing is queued
        # behind it; behind a sync, an "unattended" re-run request (evidence-only, never cleanup_stale) is.
        holder = sync_status.read_holder(root)
        if isinstance(holder, dict) and holder.get("origin") == "enrich" and holder.get("live") is not False:
            print("[deepmap sync] a deepmap enrich run holds this project's lock — this background refresh syncs "
                  "nothing now and queues nothing (enrich never syncs)", file=sys.stderr)
            last_not_pushed = ("busy: a deepmap enrich run held this project's sync lock, so this background refresh "
                               "synced nothing")
            last_outcome = {"outcome": "busy-enrich", "reason": last_not_pushed}
            return True
        queued = _request_rerun(root, cleanup_stale=False, force_prune=False, prune_policy=policy,
                                origin="unattended", trigger=getattr(unattended, "trigger", None))
        lock = _acquire_sync_lock(root)          # the holder may have released it in between
        if lock is None:
            if queued:
                print("[deepmap sync] another sync of this project is in progress — queued a background re-run "
                      "(it is included before that sync finishes)", file=sys.stderr)
                last_outcome = {"outcome": "queued"}
            else:
                last_not_pushed = ("busy: another sync of this project was in progress and a re-run could not be "
                                   "queued, so this background refresh synced nothing")
                print(f"[deepmap sync] {last_not_pushed}", file=sys.stderr)
                last_outcome = {"outcome": "busy-unqueued", "reason": last_not_pushed}
            return True
    if lock is None:
        # A user's sync (or setup's): when the holder is DeepMap's background refresh — provably alive — ask it to
        # stop and wait, so this sync runs on its own (H1). Any other holder (another user sync, enrich, no record
        # yet, an unverifiable one): the queue path below, unchanged.
        holder = sync_status.read_holder(root)
        if isinstance(holder, dict) and holder.get("origin") == "unattended" and holder.get("live") is True:
            lock, status = _preempt_background(root, holder)
            if status == "timeout":
                return True   # nothing synced: last_not_pushed says why (the CLI exits 4)
    if lock is None:
        queued = _request_rerun(root, cleanup_stale=cleanup_stale, force_prune=force_prune,
                                **({"prune_policy": prune_policy} if prune_policy is not None else {}))
        lock = _acquire_sync_lock(root)          # the holder may have released it in between
        if lock is None:
            # The holder is another sync or `deepmap enrich` (its extraction takes this lock too) — the OS lock
            # does not say which, so name both, and what each does with the queued request.
            if queued:
                print("[deepmap sync] another sync of this project, or a deepmap enrich run, is in progress — "
                      "queued a re-run: a running sync performs it before it finishes, so changes made after it "
                      "started are included; deepmap enrich never syncs, so if enrich holds the lock, run "
                      "/deep-map:refresh once it finishes",
                      file=sys.stderr)
                last_outcome = {"outcome": "queued"}
            else:
                print("[deepmap sync] another sync of this project, or a deepmap enrich run, is in progress and a "
                      "re-run could not be queued — this run was SKIPPED; run /deep-map:refresh once it finishes",
                      file=sys.stderr)
                # nothing guarantees the holder saw this run's changes (M1: a distinct exit, not 0)
                last_not_pushed = ("busy: another sync of this project, or a deepmap enrich run, was in progress "
                                   "and a re-run could not be queued, so this run's changes may not be in the graph")
                last_outcome = {"outcome": "busy-unqueued", "reason": last_not_pushed}
            return True   # not an error: the queued request covers this run (an unqueued skip: last_not_pushed)
    started = time.time()
    run = {"id": f"{_utc_now(started)}-{os.getpid()}", "origin": "unattended" if unattended is not None else "manual",
           "trigger": getattr(unattended, "trigger", None), "session": getattr(unattended, "session", None),
           "started_utc": _utc_now(started), "started_epoch": started, "policy": policy}
    ok = False
    passes = 0
    while lock is not None:
        info: dict = {"lock_t0": time.monotonic()}
        exc_seen = None
        wrote = False
        stop = False
        try:
            _holder_start(root, run, passes + 1, unattended)
            yielded = unattended.checkpoint(to_phase="start") if unattended is not None else None
            request = None
            user_request = False
            if not yielded:
                if unattended is not None and _is_user_request(_peek_rerun_request(root)):
                    # A request a user's sync (or an older DeepMap) queued: never run it under the background
                    # refresh's environment, and never claim it — it stays exactly as that sync queued it.
                    user_request = True
                else:
                    request = _take_rerun_request(root)      # queued before this pass: its extraction covers it
                    if unattended is not None and _is_user_request(request):
                        # it became a user's request between the look and the claim: put it back as it was
                        _request_rerun(root, cleanup_stale=bool(request.get("cleanup_stale")),
                                       force_prune=bool(request.get("force_prune")),
                                       prune_policy=_effective_policy("none", request), origin="manual",
                                       trigger=request.get("trigger"), ts=request.get("ts"))
                        request, user_request = None, True
                if user_request:
                    # A user's sync waiting for this lock right now is a real hand-over (it runs the request);
                    # otherwise nobody is about to run it, and this run says so instead of claiming a preemption.
                    yielded = unattended.checkpoint(to_phase="start")
            if yielded:
                y = unattended.yield_record()
                print(y["log"], file=sys.stderr)
                info.update(yielded=True, yield_outcome=y["outcome"], yield_rc=y["rc"], reason=y["reason"],
                            preempted_by=y["preempted_by"], prune_policy=policy)
                if y["rc"] == SYNC_NOT_PUSHED_RC:
                    last_not_pushed = y["reason"]
                wrote = True
                ok = True
            elif user_request:
                stop = True
                if passes:
                    # an earlier pass of this call synced: its record stays this call's outcome
                    print(sync_status.USER_REQUEST_AFTER_PASS, file=sys.stderr)
                else:
                    print(sync_status.USER_REQUEST_PENDING, file=sys.stderr)
                    last_not_pushed = sync_status.USER_REQUEST_REASON
                    info.update(user_request_pending=True, reason=sync_status.USER_REQUEST_REASON,
                                prune_policy=policy)
                    wrote = True
                    ok = True
            else:
                if request:
                    cleanup_stale = cleanup_stale or bool(request.get("cleanup_stale"))
                    force_prune = force_prune or bool(request.get("force_prune"))
                    policy = _effective_policy(policy, request)
                if unattended is not None:
                    # H1 / H4: whatever a queued request says (a hand-edited file, another DeepMap version), the
                    # background refresh never forces a prune and never runs the full one.
                    cleanup_stale = force_prune = False
                    if policy == "full":
                        policy = "evidence"
                info["prune_policy"] = run["policy"] = policy
                passes += 1
                wrote = True
                ok = _sync_pass(root, neo4j_uri, cleanup_stale=cleanup_stale,
                                switch_project=switch_project, force_prune=force_prune, driver=driver,
                                legacy_route=legacy_route, prune_policy=policy, unattended=unattended,
                                route_source=route_source, pass_info=info)
        except BaseException as exc:
            exc_seen = exc
            raise
        finally:
            _holder_end(root, run, passes, info, ok, exc_seen, unattended, wrote)
            _release_sync_lock(lock)
            lock = None
        if stop or (unattended is not None and unattended.yielded()):
            break                             # a user's sync is waiting / a user's request: no further pass
        if not ok or not _rerun_request_path(root).is_file():
            break
        if unattended is not None and _is_user_request(_peek_rerun_request(root)):
            # Only a sync the user starts runs a user's request: this background refresh stops after the pass it
            # finished, and that pass's record stays this call's outcome.
            print(sync_status.USER_REQUEST_AFTER_PASS, file=sys.stderr)
            break
        if passes >= _MAX_SYNC_PASSES:
            print(f"[deepmap sync] changes kept arriving during {passes} passes — stopping here; the "
                  f"queued re-run is left for the next sync (run /deep-map:refresh)", file=sys.stderr)
            break
        lock = _acquire_sync_lock(root)   # None: a newer sync holds it and will consume the request
        if lock is not None:
            print("[deepmap sync] changes arrived while this sync ran — syncing again to include them",
                  file=sys.stderr)
    return ok


def _phase(root: Path, ctl, phase: str, **fields) -> None:
    """Record the pass's phase (and `fields`) in this holder's sync_running.json — through the background
    refresh's control when there is one (its watcher reads the phase). Best-effort; never raises."""
    try:
        if ctl is not None:
            ctl.enter(phase)
            if fields:
                sync_status.update_running(root, **fields)
        else:
            sync_status.update_running(root, phase=phase, **fields)
    except Exception:  # noqa: BLE001
        pass


def _sync_pass(root: Path, neo4j_uri: str, *, cleanup_stale: bool, switch_project: bool,
               force_prune: bool, driver=None, legacy_route: bool = False, prune_policy=None,
               unattended=None, route_source=None, pass_info=None) -> bool:
    """One extract -> push -> index -> embed -> manifest pass over `root`, run while holding its sync
    lock. False on a deliberate refusal or an index failure; a database error propagates (fail loud).
    `legacy_route`: `neo4j_uri` is the unverified legacy 7687 (D1) — the graph's owner is checked
    before anything is extracted or written.

    Auto-sync: `prune_policy` (None = "full" with cleanup_stale, else "none"; "evidence" = _plan_prune_evidence);
    `unattended` (sync_status.UnattendedControl — its write checkpoint: pre_write's veto, then a yield to a
    waiting user sync, both before anything is written; its before_embed may postpone the embeddings);
    `route_source` (recorded in the manifest's build_recipe); `pass_info` (a dict this pass fills for the
    sync_last.json record: refusal_kind, not_pushed_kind, veto, yielded, built_at_sha, ...)."""
    global last_verdict, last_not_pushed
    last_not_pushed = None      # per pass: a queued re-run that pushes clears an earlier pass's skip
    pinfo = pass_info if isinstance(pass_info, dict) else {}
    policy = prune_policy if prune_policy in _POLICY_RANK else ("full" if cleanup_stale else "none")
    full = policy == "full"     # today's --stale-cleanup prune (byte for byte); "evidence" never runs it
    pinfo["prune_policy"] = policy
    ctl = unattended
    lock_t0 = pinfo.get("lock_t0") or time.monotonic()
    # Auto-gitignore .deepmap/ as early as we hold the per-root lock (the dir exists — the lock created
    # it) so even an instance-lock skip below still leaves it ignored. Best-effort; never affects sync.
    if _auto_gitignore_enabled():
        _ensure_artifact_gitignore(root / ".deepmap")
    inst_lock = _acquire_instance_lock(neo4j_uri)
    if inst_lock is None:
        # With per-project instances this lock is only contended on an instance several projects share
        # (the NEO4J_URI escape hatch), where the holder is ANOTHER project's sync: it will not pick up
        # this project's changes, so say so instead of promising it.
        print("[deepmap sync] another sync is writing this Neo4j instance (instance lock held — a "
              "project sharing it) — this run was SKIPPED; run /deep-map:refresh once it finishes",
              file=sys.stderr)
        last_not_pushed = ("busy: another sync was writing this Neo4j instance (a project sharing it), so this "
                           "run synced nothing")
        pinfo["not_pushed_kind"] = "busy-instance"
        return True   # not an error, but nothing was synced
    own_driver = driver is None
    if own_driver:
        from neo4j import GraphDatabase
        driver = GraphDatabase.driver(neo4j_uri,
                                      auth=("neo4j", os.environ.get("NEO4J_PASSWORD", "deepmap")),
                                      notifications_min_severity="OFF")
    try:
        driver.verify_connectivity()
        # Project-switch safety: one Neo4j instance holds ONE project's graph. Read the recorded owner
        # BEFORE extracting; refuse a foreign owner unless a switch was authorised. A refuse costs no
        # extraction and mutates nothing.
        cur_id = _project_identity(root)
        owner = _read_owning_root(driver)
        # F1: the ONE owner predicate (port_registry.owner_is_this_project, via _same_project) — the same
        # one the legacy verdict below uses, so a pre-UTF-8-fix stamp of THIS project is never refused
        # here after that verdict accepted it (E1), and a nested/parent project is never "the same".
        foreign = owner is not None and not _same_project(cur_id, owner)
        if foreign and own_driver:
            # The live graph on this URI provably is ANOTHER project's: when that is DeepMap's own base
            # store, note its owner, so a later setup can tell it while the base is down (F2). Self-guarded.
            _record_base_store_owner(root, neo4j_uri, identity=owner)
        if legacy_route:
            # D1: this project has no instance of its own, so the graph on bolt 7687 may be another
            # project's (or a pre-owner graph nobody can vouch for). Use it only when it provably is
            # this project's — or is empty. --switch-project does NOT override this: it replaces the
            # graph on an instance the operator chose (NEO4J_URI); here it would destroy another
            # project's graph that has its own setup, and /deep-map:setup gives this one its own.
            ok_owner, problem = _legacy_verdict(
                root, owner, None if owner else _graph_has_nodes(driver), neo4j_uri)
            if not ok_owner:
                note = (" (--switch-project only replaces a graph on an instance chosen with NEO4J_URI;"
                        " it is not applied to the default instance)" if switch_project else "")
                print(f"[deepmap sync] REFUSING: {problem}{note}", file=sys.stderr)
                pinfo.update(refusal_kind="legacy-owner", reason=f"REFUSING: {problem}{note}")
                return False   # deliberate refusal: no extraction, no graph mutation
        # P1-6: ONLY --switch-project authorises replacing a foreign project's graph. --stale-cleanup
        # is decoupled — it prunes orphans within the SAME owner (below) but must NOT silently wipe
        # another project (so /deep-map:refresh, which passes --stale-cleanup, can never switch owners).
        switch_authorized = switch_project
        if foreign and _is_base_claim_route(root, neo4j_uri):
            # This project's OWN registry claim is the shared base store (7687) and that store holds
            # another project's graph: a base claim a <=v0.27.4 build wrote by mistake (or a base whose
            # graph an operator replaced). The claim loses to the live owner (F2): /deep-map:setup releases
            # it and gives this project its own instance, and the real owner's graph stays. So the wipe
            # hint is never printed and --switch-project is NOT applied here — it would DELETE that
            # project's graph — exactly as on the legacy route. Also inside setup's own sync, where
            # provision injected NEO4J_URI (not an operator's choice). An operator NEO4J_URI (a shared
            # instance chosen on purpose) and a per-project instance keep the --switch-project path below.
            note = (" --switch-project is not applied to the shared base store: it would delete that "
                    "project's graph (to replace a graph deliberately, point NEO4J_URI at that instance)."
                    if switch_project else
                    " Do NOT use --switch-project here: it would delete that project's graph.")
            print(f"[deepmap sync] REFUSING: this project is registered on the shared base Neo4j "
                  f"({neo4j_uri}), which holds project '{owner}'s graph, not this project's — run "
                  f"/deep-map:setup in this project to give it its own instance (that graph stays with "
                  f"its owner).{note}", file=sys.stderr)
            pinfo.update(refusal_kind="base-claim",
                         reason=f"REFUSING: this project is registered on the shared base Neo4j ({neo4j_uri}), "
                                f"which holds project '{owner}'s graph — run /deep-map:setup in this project")
            return False   # deliberate refusal: no extraction, no graph mutation
        if foreign and not switch_authorized:
            print(f"[deepmap sync] REFUSING: this Neo4j holds project '{owner}'. Re-run with "
                  f"--switch-project to replace it with this project (--stale-cleanup no longer "
                  f"authorises a cross-project wipe).",
                  file=sys.stderr)
            pinfo.update(refusal_kind="foreign-owner",
                         reason=f"REFUSING: this project's Neo4j holds project '{owner}'s graph")
            return False   # deliberate refusal: no extraction, no graph mutation
        if owner is None and ctl is not None and _graph_has_nodes(driver):
            # Auto-sync: DeepMap's background refresh never adopts a database it cannot prove is this project's —
            # an operator NEO4J_URI exported for another app since the last sync would otherwise get DeepMap's
            # owner marker, indexes and graph written into it with no command run. A sync the user starts adopts.
            why = ("REFUSING: this project's Neo4j holds a graph with no recorded owner, and a background refresh "
                   "never adopts a database it cannot prove is this project's — if it is this project's graph, "
                   "/deep-map:refresh adopts it; if NEO4J_URI points at another database, unset it")
            print(f"[deepmap sync] {why}", file=sys.stderr)
            pinfo.update(refusal_kind="unowned", reason=why)
            return False   # deliberate refusal: no extraction, no graph mutation
        if owner is None and _graph_has_nodes(driver):
            # Once adopted, the owner IS this project, so --switch-project is a no-op on the next run (it
            # used to be the advice here); a forced stale-prune is what drops another project's leftovers.
            print(f"[deepmap sync] no owner recorded on a non-empty graph — adopting '{cur_id}'. If "
                  f"this Neo4j held a different project, re-run the sync from this repository's root with "
                  f"--stale-cleanup --force-prune to drop every node this project's extraction does not "
                  f"produce.", file=sys.stderr)
        prior_engines = _read_prior_engines(root)           # what the graph holds before this pass
        source_state = _source_state(root)                   # Y4: the tree as extraction is about to read it
        _fp = source_state.get("fingerprint") if isinstance(source_state, dict) else None
        _fp_value = _fp.get("value") if isinstance(_fp, dict) else None
        pinfo.update(built_at_sha=source_state.get("sha") if isinstance(source_state, dict) else None,
                     dirty=source_state.get("dirty") if isinstance(source_state, dict) else None,
                     source_fingerprint=_fp_value)
        _phase(root, ctl, "extract", head_at_start=pinfo.get("built_at_sha"), source_fingerprint=_fp_value)
        extracted = _rebuild_code(root)                     # 1. extract (line numbers inline)
        nodes, calls, imports, engine = extracted
        extraction = getattr(extracted, "info", None)       # G4: requested vs resolved + fallback reason
        pinfo["engine_timed_out"] = bool(isinstance(extraction, dict) and extraction.get("timed_out"))
        # Auto-gitignore graphify-out/ AFTER extraction (so writing into graphify's dir never races the
        # subprocess); the .is_dir() guard makes it a no-op in ast-mode (no graphify -> no dir).
        if _auto_gitignore_enabled():
            _ensure_artifact_gitignore(root / "graphify-out")
        enrich.apply_sidecar(root, nodes)   # 1b. OPT-IN: override body_summary from the enrichment sidecar (no-op if none)
        # 1c. the per-language recognizer registry (Track C generalized): the Python framework/
        # runtime edges (byte-identical to the old extract_framework_edges call) + any per-language
        # supplemental nodes (e.g. Rails model classes graphify misses). NOTE: supplemental nodes
        # land AFTER the enrichment sidecar (1b) by design — enrich is Python-exact and enumerates
        # via _rebuild_code, so it can never have a sidecar entry for a recognizer node.
        nodes, typed_edges = _apply_recognizers(root, nodes)
        # Authorised switch: wipe only when we actually have a replacement graph (preserve the 0-node
        # backstop — a crash/empty extraction must never leave the instance wiped-and-empty).
        replaced = False                                    # did this pass leave ONLY its own engine's nodes?
        if foreign and nodes:
            print(f"[deepmap sync] switching project: replacing the graph for '{owner}' with "
                  f"'{cur_id}'", file=sys.stderr)
            _wipe_all(driver)
            # R10/P2: the wiped project's documents would stay in the full-text statistics — re-create the
            # index now, on the empty graph, so this push only ADDS (a fresh build's state; crash-safe).
            _rebuild_code_fts(driver)
            replaced = True
        elif foreign and not nodes:
            print("[deepmap sync] switch SKIPPED: extraction produced 0 nodes (refusing to wipe the "
                  "graph) — the existing project is preserved", file=sys.stderr)
        # Round 7 — the engine memory (the prior manifest's graph_engines) against the LIVE graph: an
        # EMPTY graph (a store wiped or re-created under a kept .deepmap/) holds no engine's nodes, whatever
        # the manifest says, so the memory must neither refuse this push nor survive it. Only a graph READ as
        # empty resets it: a count that could not be read (None) keeps the memory, so the downgrade refusal
        # below still applies (ISS-build:017 — an unreadable count used to read as "empty").
        if nodes and not foreign and prior_engines and set(prior_engines) != {engine}:
            holds = _graph_holds_nodes(driver)
            if holds is None:
                guard = (", so the engine-downgrade guard still applies"
                         if engine == "ast" and "graphify" in prior_engines else "")
                print(f"[deepmap sync] could not read whether the graph still holds nodes — keeping the "
                      f"engine memory of the previous build ({', '.join(prior_engines)}) instead of "
                      f"resetting it{guard}", file=sys.stderr)
            elif not holds:
                prior_engines = []
        prior_engine = (None if (replaced or not prior_engines) else _read_prior_extractor(root))
        root_ok = _cleanup_is_safe(root)
        if nodes and engine == "ast" and prior_engine == "graphify" \
                and not (force_prune and full and root_ok):
            # Round 7 (T7#11): an engine DOWNGRADE refuses the PUSH, not only the prune. The ast engine
            # keys nodes differently from graphify, so pushing them put a second copy of every Python
            # symbol next to graphify's (search and impact answered everything twice) while the refused
            # prune kept the graphify nodes. The graph and its manifest stay as they are; a deliberate
            # switch is --stale-cleanup --force-prune (push, then prune to what 'ast' extracts).
            print(_downgrade_message(extraction, cleanup_stale=full, force_prune=force_prune,
                                     root_ok=root_ok), file=sys.stderr)
            why = ("ENGINE DOWNGRADE REFUSED — this run resolved to 'ast' over a graph built by 'graphify'; "
                   "nothing was pushed, so the graph is still the previous build (see the message above)")
            last_verdict = "DEGRADED"
            info = extraction if isinstance(extraction, dict) else {}
            forced = str(info.get("requested") or os.environ.get("DEEPMAP_EXTRACTOR", "")).strip().lower() == "ast"
            last_not_pushed = ("engine-downgrade: this run "
                               + ("was forced to 'ast' (DEEPMAP_EXTRACTOR=ast)" if forced else "fell back to 'ast'")
                               + " over a graph built by 'graphify'"
                               + (" because graphify ran out of its time budget (raise "
                                  f"{graphify_extract.ENV_GRAPHIFY_TIMEOUT})" if info.get("timed_out") else "")
                               + ", so the push was refused")
            pinfo.update(not_pushed_kind="engine-downgrade", reasons=[why])
            try:
                import verdict as _verdict
                print(_verdict.format_block(_verdict.DEGRADED, [why]), file=sys.stderr)
            except Exception:
                print(f"[deepmap] verdict: DEGRADED\n          - {why}", file=sys.stderr)
            try:
                import port_registry as _pr
                _pr.touch_last_used(root)
            except Exception:
                pass
            return True   # non-fatal: the graph is preserved (the verdict says DEGRADED)
        # The push-side call passes below are PURE: they read the extraction and the source files, never Neo4j. So
        # they run HERE, before the write checkpoint: a background refresh is still in its extract phase while they
        # run (the typed pass alone takes tens of seconds on a large repository), so a sync the user starts meanwhile
        # stops it at once (the watcher's hard exit) instead of waiting them out, and the checkpoint below polls for
        # it once they are done, right before anything is written (typed_calls_push_gates C8 locks the order).
        # R10/P1 (DEEPMAP_PRECISE_CALLS only): a receiver call graphify bound to a same-named module-level
        # function goes back to the flag-off name tuple — BEFORE the passes below, which then judge it
        # exactly as they judge a flag-off tuple. Flag off: `calls` is returned unchanged.
        calls, n_unkey = _unkey_receiver_calls(root, nodes, calls)
        if n_unkey:
            print(f"[deepmap sync] {n_unkey} receiver call(s) (`obj.f()`) the engine bound to a same-named "
                  f"module-level function now fan out by name, as with DEEPMAP_PRECISE_CALLS off (a receiver "
                  f"never reaches a module-level def)", file=sys.stderr)
        # S2 (design 2.8 step 2): typed-receiver call linking + the untyped-receiver audit — DEEPMAP_TYPED_CALLS /
        # DEEPMAP_CALLS_AUDIT (default OFF: the same list, stats None). One stderr line when it runs.
        calls, call_stats = _typed_calls_link(root, nodes, calls)
        n_bare = _attach_bare_calls(root, nodes, calls)   # round 8, J3: bare-only calls (before T3#6's pass)
        if n_bare:
            print(f"[deepmap sync] {n_bare} bare call(s) no longer fan out to methods they cannot reach "
                  f"(no receiver in Python/JS/TS/Go/Rust) — DEEPMAP_FALSE_CALL_FILTER=0 keeps them",
                  file=sys.stderr)
        _attach_local_defs(root, nodes, calls)   # round 7, T3#6: the scope data _resolve_calls grades with
        n_local = _attach_local_def_calls(root, nodes, calls)   # DEEPMAP_LOCAL_DEF_CALLS (default OFF)
        if n_local:
            print(f"[deepmap sync] {n_local} bare call(s) bound to the caller's own definition only — Python "
                  f"binds a bare name to its own scope/module first (DEEPMAP_LOCAL_DEF_CALLS=1)",
                  file=sys.stderr)
        if ctl is not None:
            # Auto-sync — the background refresh's WRITE CHECKPOINT: nothing has been written to Neo4j yet. First its
            # own veto (a git operation started meanwhile, the extractor or its version changed: the refresh would
            # not rebuild the graph the user built), then a sync the user started that waits for the lock: stop here
            # and push nothing. Past it the pass can no longer be stopped (it finishes, then yields).
            try:
                veto = ctl.pre_write(engine, extraction if isinstance(extraction, dict) else {}) \
                    if callable(getattr(ctl, "pre_write", None)) else None
            except Exception as exc:  # noqa: BLE001 — a check that cannot answer never lets the push through
                veto = f"pre-write-error: {type(exc).__name__}"
            if veto:
                veto = _one_line(veto, 200)
                print(f"[deepmap sync] this background refresh pushed nothing ({veto}) — the graph and its "
                      f"graph-build manifest are unchanged", file=sys.stderr)
                last_not_pushed = f"vetoed: {veto}"
                pinfo.update(not_pushed_kind="veto", veto=veto)
                return True
            if ctl.checkpoint(to_phase="write"):
                y = ctl.yield_record()
                print(y["log"], file=sys.stderr)
                pinfo.update(yielded=True, yield_outcome=y["outcome"], yield_rc=y["rc"], reason=y["reason"],
                             preempted_by=y["preempted_by"])
                if y["rc"] == SYNC_NOT_PUSHED_RC:
                    last_not_pushed = y["reason"]
                return True
        else:
            _phase(root, None, "write")
        # Round 7 (T7d-1 lead / T5#7) — the stale-prune decision is taken BEFORE the push: measured after
        # it, a full re-key read as exactly 50% of a doubled graph. See _plan_prune / _stale_census.
        plan = None
        if full and nodes and root_ok:
            plan = _plan_prune(driver, root, nodes, engine, prior_engine, force_prune)
        elif policy == "evidence" and nodes and root_ok:
            # auto-sync: the evidence-only prune (never the full one) — see _plan_prune_evidence
            plan = _plan_prune_evidence(driver, root, nodes, engine, prior_engine, extraction)
        # 2. indexes — CREATED before the push: every node MERGE and edge MATCH seeks the node-key
        # indexes, which a first sync used to create only afterwards. Waited on after the push.
        idx_status = indexes.create_all_indexes(driver)
        _vec_names = {n for _l, n in getattr(indexes, "VECTOR_INDEXES", ())}
        vec_redone = sorted(k for k, v in (idx_status if isinstance(idx_status, dict) else {}).items()
                            if k in _vec_names and str(v).startswith("recreated"))
        if vec_redone:   # R10/P4: DEEPMAP_VECTOR_QUANTIZATION changed since the indexes were built
            mode = ("OFF (exact scores, DEEPMAP_VECTOR_QUANTIZATION=0)" if indexes.vector_quantization_disabled()
                    else "ON (the Neo4j default)")
            print(f"[deepmap sync] re-created {len(vec_redone)} vector index(es) with vector quantization {mode}"
                  f" — the stored embeddings re-populate them; nothing is re-embedded", file=sys.stderr)
        # R10/P3 + P2: drop stale short_names (flag off), then decide whether this push leaves the full-text
        # index with documents a fresh build would not have (see _fts_changed_docs) — stamped BEFORE the push
        # so a sync killed before the rebuild still gets it done by the next one.
        n_short = _remove_stale_short_names(driver)
        if n_short:
            print(f"[deepmap sync] removed a stale short_name from {n_short} node(s) (DEEPMAP_FTS_SHORT_NAME "
                  f"is off)", file=sys.stderr)
        fts_pending = _fts_rebuild_pending(driver)
        fts_changed = _fts_changed_docs(driver, nodes) if nodes else 0
        fts_deleted = int((plan or {}).get("would_delete") or 0) if (plan or {}).get("decision") == "prune" else 0
        fts_dirty = bool(fts_pending or fts_changed or fts_deleted or n_short)
        if fts_dirty and not fts_pending:
            _mark_fts_rebuild(driver, True)
        n_pushed, n_edges = push_to_neo4j(driver, nodes, calls, imports, typed_edges)  # 3. MERGE
        if n_pushed:
            # Stamp ownership right after a real push (before index/embed) so a later failure still
            # leaves ownership correct. Always THIS project's current identity: it re-stamps an old
            # narrower or pre-UTF-8-fix stamp of the same project (E1) in canonical form, and after an
            # authorised switch it names the NEW project — the old "keep a broader owner" rule kept a
            # FOREIGN parent's stamp over the replaced graph (F1: a broader owner is never this project).
            _write_owning_root(driver, cur_id)
            if own_driver:
                _record_base_store_owner(root, neo4j_uri)
            # Round 7 (T7d-7): a node re-keyed by a line shift takes its old node's embedding — BEFORE the
            # stale prune below deletes that node.
            _reuse_rekeyed_embeddings(driver, nodes)
        prune_skipped = None
        evidence_kept = None
        if plan is not None and plan.get("policy") == "evidence":
            # auto-sync: the evidence-only prune. A refusal keeps the stale nodes and the verdict (H7): they are
            # reported as stale_kept, and /deep-map:refresh (the full prune) removes them.
            if plan["decision"] == "prune":
                _cleanup_stale(driver, plan["live"])
                replaced = True
                pinfo["pruned"] = int(plan.get("would_delete") or 0)
            elif int(plan.get("would_delete") or 0) > 0:
                prune_skipped = "evidence"
                evidence_kept = int(plan.get("would_delete") or 0)
                why = _EVIDENCE_WHY.get(plan.get("reason"), str(plan.get("reason") or "evidence"))
                print(f"[deepmap sync] background refresh kept {evidence_kept} stale node(s) it could not prove "
                      f"removed ({why}) — /deep-map:refresh prunes them", file=sys.stderr)
        elif plan is not None:                                # (optional) prune orphans
            # F3d wipe guard: refuse a >50% blast-radius prune (a partial/mis-scoped extraction), unless
            # --force-prune. A normal re-sync with a small orphan set still prunes, and so does one whose
            # stale nodes are all in re-extracted, deleted or deliberately excluded files.
            if plan["decision"] == "prune":
                _cleanup_stale(driver, plan["live"])
                replaced = True
                pinfo["pruned"] = int(plan.get("would_delete") or 0)
            elif plan["reason"] == "engine-downgrade":        # unreachable: the push was refused above
                prune_skipped = "engine-downgrade"
                print(_downgrade_message(extraction, cleanup_stale=full, force_prune=force_prune,
                                         root_ok=root_ok), file=sys.stderr)
            else:  # blast-radius
                prune_skipped = "blast-radius"
                sus = plan.get("suspicious")
                detail = (f"; {sus} of them are in files that still exist but were not extracted this run"
                          if sus is not None else "")
                print(f"[deepmap sync] stale-cleanup SKIPPED: a graph-wide prune would DETACH-DELETE "
                      f"{plan['would_delete']}/{plan['existing_total']} keyed nodes{detail} "
                      f"(> {int(_PRUNE_MAX_FRACTION * 100)}% of the graph) — that is almost always a partial "
                      f"or mis-scoped extraction, not a real orphan set. Verify the extraction, then pass "
                      f"--force-prune to override.", file=sys.stderr)
        elif full and nodes and not root_ok:
            # Keys are root-relative, so a subdir sync's live set never covers the full graph — a
            # graph-wide DETACH DELETE would wipe out-of-scope nodes. Refuse it loudly.
            print(f"[deepmap sync] stale-cleanup SKIPPED: {root} has no repo-root marker "
                  "(.git/.hg/.svn/pyproject.toml/package.json/go.mod/Cargo.toml/…) — a subdir sync's "
                  "keys are subtree-scoped, so a graph-wide prune would wipe out-of-scope nodes. Run "
                  "--stale-cleanup from the repo root (or drop a .deepmap-root file there).",
                  file=sys.stderr)
        elif full and not nodes:
            # An extraction that yields 0 nodes (extractor crash, empty repo, graphify miss) must
            # NOT trigger DETACH DELETE of everything — that would wipe a good graph. Refuse it.
            print("[deepmap sync] stale-cleanup SKIPPED: extraction produced 0 nodes "
                  "(refusing to wipe the graph)", file=sys.stderr)
        if fts_dirty:
            # R10/P2: re-create deepmap_code_fts from the graph as it now is (additions only) — its BM25
            # statistics then equal a fresh build's. wait_for_indexes below waits for it to be ONLINE.
            t_fts = time.monotonic()
            if _rebuild_code_fts(driver):
                _mark_fts_rebuild(driver, False)
                why = ", ".join(w for w in (
                    f"{fts_changed} node(s) with rewritten indexed text" if fts_changed else "",
                    f"{fts_deleted} pruned node(s)" if fts_deleted else "",
                    f"{n_short} stale short_name(s) removed" if n_short else "",
                    "an interrupted earlier rebuild" if fts_pending else "") if w)
                print(f"[deepmap sync] full-text index re-created ({why}) in {time.monotonic() - t_fts:.1f}s so its "
                      f"BM25 statistics equal a fresh build's", file=sys.stderr)
        if not indexes.wait_for_indexes(driver, timeout=120):
            print("[deepmap sync] indexes did not all come ONLINE", file=sys.stderr)
            pinfo.update(refusal_kind="index-failure", reason="indexes did not all come ONLINE")
            return False
        _phase(root, ctl, "embed")
        deferred = False
        if ctl is not None and callable(getattr(ctl, "before_embed", None)):
            # auto-sync (M4): the background refresh checks memory again right before the model loads; a machine
            # that stays low postpones the embeddings (the graph and full-text index are written; the next refresh
            # adds them — the manifest says embeddings_pending).
            try:
                deferred = not ctl.before_embed()
            except Exception:  # noqa: BLE001 — a check that cannot answer does not block the embeddings
                deferred = False
        if deferred:
            print("[deepmap sync] embeddings postponed — the machine is low on memory; the graph and its full-text "
                  "index are written, and the next refresh computes the embeddings", file=sys.stderr)
            n_emb = 0
            pinfo["embeddings_deferred"] = True
        else:
            try:
                n_emb = embeddings.generate_embeddings(driver)   # 4. embeddings
            except (ImportError, OSError) as exc:
                # ONLY a genuine model-unavailable signal degrades: torch/sentence-transformers not
                # importable (ImportError) or the model files missing (OSError). A DB outage or a real
                # code bug is NOT caught here — it propagates and fails loud, so we never report a
                # half-built or silently-broken graph as success (T10). Graph + indexes are already
                # built, so search runs FTS-only until embeddings are (re)generated.
                print(f"[deepmap sync] embeddings skipped — model unavailable "
                      f"({type(exc).__name__}: {exc}); graph + indexes built, search runs FTS-only",
                      file=sys.stderr)
                n_emb = 0
        _phase(root, ctl, "manifest")
        # P1-5 (Rails field-review): the verdict + manifest must reflect the TRUE embedded count
        # (nodes with an embedding), NOT `n_emb` — which is only the count re-embedded THIS run and
        # is legitimately 0 on a re-sync of an already-fully-embedded graph, which used to fire a
        # FALSE DEGRADED "VECTOR SEARCH DISABLED" on a healthy graph. `n_emb` stays the human
        # "embedded this run" figure; `n_emb_total` drives health/coverage + the persisted manifest.
        n_emb_total = _count_embedded_nodes(driver, fallback=n_emb)
        # Y5: a pass that pushed NOTHING left the graph as it was — so when that graph still holds a
        # previous build, its manifest (extractor, verdict, SHA) still describes it (see below), and the
        # graph counts printed must be the KEPT graph's, named as such: printing this run's "0 nodes,
        # 0 CALLS edges" beside the kept graph's embedded total described a graph that cannot exist.
        holds = _graph_holds_nodes(driver) if not n_pushed else False
        graph_kept = not n_pushed and bool(holds)
        # ISS-build:017: a node count that could not be read is UNKNOWN, not "empty" — the manifest (and the
        # engine memory in its graph_engines) is kept exactly as for a kept graph; only a graph READ as empty
        # gets this run's 0-node FAILED manifest below. This run's FAILED verdict still reaches the CLI.
        graph_unknown = not n_pushed and holds is None
        keep_manifest = graph_kept or graph_unknown
        # Round 7 (T5#7): a refused prune leaves nodes this run did not extract in the graph — report the
        # graph's real total next to this run's count (and in the manifest + verdict below).
        graph_total = _graph_node_count(driver) if (prune_skipped and n_pushed) else None
        if graph_kept:
            kept_n = _graph_node_count(driver)
            kept = (f"{kept_n} nodes, {n_emb_total} embedded" if kept_n is not None
                    else f"{n_emb_total} embedded nodes")
            print(f"[deepmap sync] this run extracted 0 nodes from {root} — nothing was pushed; the existing "
                  f"graph ({kept}) and its graph-build manifest were left unchanged", file=sys.stderr)
        elif graph_unknown:
            print(f"[deepmap sync] this run extracted 0 nodes from {root} — nothing was pushed; whether the "
                  f"graph still holds a previous build could not be read, so its graph-build manifest was left "
                  f"unchanged", file=sys.stderr)
        else:
            print(f"[deepmap sync] {n_pushed} nodes, {n_edges} CALLS edges, {n_emb} embedded this run "
                  f"({n_emb_total} embedded total) from {root}"
                  + (f"; the graph holds {graph_total} nodes — the stale-cleanup was skipped, so nodes this "
                     f"run did not extract are still in it" if graph_total is not None else ""))
        # Honest verdict (P1/P4/P5 + the Rails coverage layer): a degraded/empty outcome must be
        # LOUD, not a silent success — 0 nodes on a non-Python repo, 0 embeddings (vector search
        # off), or a dead conventional layer (app/models/ with files but ~no nodes). Advisory +
        # best-effort: prints only when NOT fully healthy; a failure here never breaks a sync and
        # never blocks the manifest write below (the fields just stay absent).
        _v = _rs = _cov = None
        try:
            import verdict as _verdict
            _census = _verdict.extension_census(root)
            _cov = _verdict.build_coverage(root, nodes, census=_census)
            _v, _rs = _verdict.classify(
                neo4j_ok=True, n_nodes=n_pushed, n_embedded=n_emb_total,
                census=_census,
                extractor=engine,   # the RESOLVED engine actually used (graphify or ast fallback)
                ml_ok=_verdict.ml_stack_ok(),
                coverage=_cov, extraction=extraction)
            if prune_skipped and prune_skipped != "evidence" and n_pushed:
                # Round 7 (T5#7): the graph is not what this run extracted — say so, and never call it READY.
                # (Not for the background refresh's evidence-only prune, auto-sync H7: a DEGRADED verdict would
                # silence DeepMap's guidance over a few kept nodes — they are reported as stale_kept instead.)
                stale_n = plan.get("would_delete") if isinstance(plan, dict) else None
                _rs = list(_rs or []) + [
                    f"STALE NODES KEPT — the requested stale-cleanup was skipped ({prune_skipped}), so the "
                    f"graph still holds {stale_n if stale_n is not None else 'the'} node(s) this run did not "
                    f"extract" + (f" ({graph_total} nodes in all)" if graph_total is not None else "")
                    + "; search and impact can return them. Fix the extraction, or re-run with --force-prune "
                    "if those deletions are expected"]
                if _v == _verdict.READY:
                    _v = _verdict.DEGRADED
            if _v != _verdict.READY:
                # The block's "graph:" line is the graph's own state — omitted when this run kept an
                # existing graph (the line above already gave that graph's counts, named as kept) or could
                # not read it (a "graph: 0 nodes" line would describe a graph nobody saw).
                counts = ({} if keep_manifest else
                          {"nodes": graph_total if graph_total is not None else n_pushed,
                           "edges": n_edges, "embedded": n_emb_total})
                print(_verdict.format_block(_v, _rs, **counts), file=sys.stderr)
        except Exception:
            _v = _rs = _cov = None   # classify failure must not block the manifest write
        last_verdict = str(_v) if _v else None
        # Y5 (graph_kept, above): overwriting a kept graph's manifest with "0 nodes / FAILED / ast" made
        # status report FAILED over an intact graph and erased the engine memory the downgrade guard
        # reads. The FAILED verdict still reaches the CLI through `last_verdict`. An EMPTY graph does get
        # the FAILED manifest (that is the truth); a graph that could not be read keeps its manifest
        # (keep_manifest — never erase graph_engines on an unknown, ISS-build:017).
        if replaced:
            engines_after = [engine]
        elif n_pushed:
            engines_after = sorted(set(prior_engines) | {engine})
        else:
            engines_after = prior_engines if keep_manifest else []
        pinfo.update(reasons=list(_rs or []), pushed_nodes=int(n_pushed or 0),
                     stale_cleanup_skipped=(prune_skipped if n_pushed else None),
                     stale_kept=(int(evidence_kept or 0) if prune_skipped == "evidence" else
                                 int((plan or {}).get("would_delete") or 0) if prune_skipped else 0))
        if not keep_manifest:
            # Provenance sidecar (best-effort) — records the git SHA the graph was built at so the
            # benchmark can verify the ON-arm graph and the OFF-arm grep are the SAME pinned SHA, plus
            # the sync-time verdict/coverage so hooks + doctor can read health without Neo4j.
            try:
                recipe = sync_status.make_build_recipe(
                    engine=engine, extraction=extraction, route_source=route_source, neo4j_uri=neo4j_uri,
                    code_version=_CODE_VERSION)
            except Exception:  # noqa: BLE001 — provenance never blocks the manifest
                recipe = None
            manifest = write_graph_build_manifest(
                root, n_nodes=n_pushed, n_edges=n_edges, n_embedded=n_emb_total,
                extractor=engine,   # the RESOLVED engine (graphify or ast fallback), not just the mode
                verdict=_v, reasons=_rs, coverage=_cov, extraction=extraction,
                source_state=source_state, graph_engines=engines_after,
                stale_cleanup_skipped=prune_skipped if n_pushed else None, graph_nodes=graph_total,
                stale_kept=(evidence_kept if (prune_skipped == "evidence" and n_pushed) else None),
                build_recipe=recipe, sync_wall_s=time.monotonic() - lock_t0,
                embeddings_pending=(True if deferred else None), call_linking=call_stats)
            if manifest is not None:
                print(f"[deepmap sync] graph-build provenance -> {manifest}")
        _maybe_auto_tune(root)
        # v0.27: stamp last_used (the idle-hibernation signal). ONE site covers CLI sync, the git-hook
        # sync, and provision setup's sync. Best-effort — never affects the sync result.
        try:
            import port_registry as _pr
            _pr.touch_last_used(root)
        except Exception:
            pass
        return True
    finally:
        _drop_extraction_note(root)   # S2: a typed-pass note this pass never used (a refusal) dies with it
        if own_driver:
            driver.close()
        _release_sync_lock(inst_lock)


def _maybe_auto_tune(root) -> None:
    """RM-1 (opt-in): after a successful sync, re-tune learned fusion weights offline IFF
    DEEPMAP_AUTO_TUNE is set OR the learning-loop master switch is on (DEEPMAP_LEARNING /
    instance learned_fusion.learning_enabled; an explicit DEEPMAP_AUTO_TUNE=0 vetoes the tune leg
    while leaving logging on). OFF by default. Writes .deepmap/instance.json with applied:false
    (never live). A no-op / failure here must NEVER break sync — every leg is wrapped and
    best-effort. Also rotates the retrieval log (unconditionally) so it can't grow unbounded."""
    try:
        import retrieval_log as _rl
        _rl.rotate_log(root)
    except Exception:
        pass
    try:
        auto = str(os.environ.get("DEEPMAP_AUTO_TUNE", "")).strip().lower()
        if auto in {"0", "false", "no", "off"}:
            return
        if auto not in {"1", "true", "yes", "on"}:
            import retrieval_log as _rl
            if not _rl.learning_enabled(root):
                return
        import tune_weights as _tw
        try:
            # Drift guard FIRST — judge the weights that were STANDING before re-tuning replaces
            # them. A significant newest-slice degradation reverts to equal, loudly.
            d = _tw.apply_drift_guard(root)
            if d.get("reverted"):
                print(f"[deepmap sync] learning drift guard -> REVERTED to equal "
                      f"(newest-slice mean_delta={d.get('mean_delta') or 0.0:.4f}, "
                      f"95% CI [{d.get('ci_lo') or 0.0:.4f}, {d.get('ci_hi') or 0.0:.4f}])")
        except Exception:
            pass
        pooled = None
        try:
            # Cold-start seeding (opt-in via DEEPMAP_SEED_PRIOR): warm-start the tuner from the
            # local user prior. Offline recommendation input only — never read by live ranking.
            import learned_prior as _lp
            pooled = _lp.seed_for_tuner(root)
        except Exception:
            pooled = None
        res = _tw.tune_profiles(root=root, seed_prior=(pooled or {}).get("arm_weights"))
        out = _tw.write_instance_weights(root, res)
        if out is not None:
            print(f"[deepmap sync] learning auto-tune -> {out} (applied:false; reason={res.get('reason')})")
        try:
            import learned_prior as _lp
            if pooled and res.get("reason") == "below_threshold":
                # A cold project records the pooled prior as a RECOMMENDATION block (never live).
                _lp.write_seed_recommendation(root, pooled)
            if (res.get("beats_equal") is True and res.get("significant") is True
                    and _lp.seed_env_enabled()):
                # A significant WIN feeds the local user prior (only wins are pooled).
                _lp.export_prior(root, result=res)
        except Exception:
            pass
    except Exception:
        # Offline tuning must never fail a sync.
        pass


def _main(argv) -> int:
    """`python build/sync.py [dir] [--stale-cleanup] [--switch-project] [--force-prune]` -> exit code.
    A sync that ran but produced no usable graph (verdict FAILED) is not a success either (1), and one that
    pushed nothing over the graph it left in place exits SYNC_NOT_PUSHED_RC (4) — the same rules as
    `deepmap sync` (deepmap_cli.cmd_sync)."""
    target = argv[1] if len(argv) > 1 and not argv[1].startswith("--") else "."
    ok = sync(target, cleanup_stale="--stale-cleanup" in argv,
              switch_project="--switch-project" in argv,
              force_prune="--force-prune" in argv)
    if not ok or last_verdict == "FAILED":
        return 1
    return SYNC_NOT_PUSHED_RC if last_not_pushed else 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
