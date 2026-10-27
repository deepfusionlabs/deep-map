#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Per-language recognizer registry — the language seam of the extraction pipeline (flat, stdlib-only).

DeepMap's pipeline is language-neutral below the (nodes, calls, imports, typed_edges) tuple: name
resolution + confidence (edge_resolve), push/prune, impact traversal and the census all work for any
language. What was NOT language-neutral is the semantic recognizer layer: framework_edges.py walks
`*.py` and reads the Python AST, so every other language got graphify's generic nodes+calls and NO
framework/DSL relationships (the 2026-07 Rails field report: app/models/ invisible, cascades
untraceable by deepmap_impact).

This module generalizes that seam WITHOUT changing the recognizer LOGIC in framework_edges.py (only
ALLOWED_EDGE_TYPES gained CASCADES in v0.18; the logic is unchanged — its gates staying green is the
no-regression proof): a plain-list REGISTRY of RecognizerEntry, each contributing a PURE per-file core
    recognize(source_text, rel_path) -> (supplemental_nodes, typed_edges)
plus ONE shared impure walker with per-entry ignore discipline, a supplemental-node validator, and
the fan-out `run(root)` that build/sync.py calls where it used to call extract_framework_edges.

Design rules (review-locked):
  * The Python entry delegates to framework_edges.edges_from_module (+ its compose_route_prefixes
    post-pass) and contributes ZERO supplemental nodes — so its output equals the legacy
    extract_framework_edges shell edge for edge, and the Python-only node list is byte-identical
    (merge_supplemental returns the ORIGINAL list object when nothing is added).
  * An entry may declare a whole-run `finalize` post-pass (round 7) for cross-file facts; it only
    rewrites the edges its own per-file core produced, and a crash in it keeps those edges.
  * The `*.py` walk replicates extract_framework_edges' EXACT discipline (sorted rglob, skip
    __pycache__/.venv path components, utf-8-sig + errors="replace", posix relpath, per-file
    isolation). NEW extensions get a BROADER ignore set (vendor/, node_modules/, tmp/, log/, …) —
    a Rails checkout's vendored gems must never enter a recognizer.
  * On top of those static sets the caller may pass `run(root, exclude=pred)`; sync does, with its
    exclusion rule (its vendor/.gitignore filter + the dirs DeepMap never treats as project source), so no
    per-file recognizer reads a vendored, third-party or other-checkout file — on the graphify path none of
    those is in the graph (graphify skips them or sync's vendor filter drops them; the Python-only ast
    fallback does extract the non-vendored ones, bar .venv/), and a relationship declared there (a
    virtualenv's Django models) resolved onto same-named PROJECT classes through edge_resolve's global-src
    fallback (ISS-build:022). A file the predicate excludes is never read.
  * Supplemental nodes must carry the FULL push contract {key, label, name, source_file,
    start_line, documented, body_summary}: push_to_neo4j hard-subscripts these (a missing key is a
    sync-crashing KeyError), and a label outside the indexed set pushes fine but is silently
    invisible to every search index — so the validator DROPS nonconforming nodes (degrade + one
    stderr line, never crash, never push an invisible node).
  * DEEPMAP_RECOGNIZERS: unset/empty -> every applicable entry runs (default-on, self-gating by
    file presence — a Python-only repo pays nothing for the Ruby entry); "0"/"false"/"no"/"off" ->
    the whole layer is off; a CSV of names ("python,ruby") -> only those entries run.
  * Never raises: a recognizer crash on one file skips that file; a broken entry contributes
    nothing; run() always returns two lists.

Adding a language = one new flat module exposing `recognize(source, rel)` + one RecognizerEntry
here (declare its extensions, its edge types — which MUST be ⊆ framework_edges.ALLOWED_EDGE_TYPES,
the persist-time injection whitelist — and which of those are impact-traversable; the registry
gates lock that discipline).
"""
from __future__ import annotations

import inspect
import os
import sys
from pathlib import Path
from typing import Callable, NamedTuple

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import framework_edges  # noqa: E402  (the Python entry delegates into it: per-file core + route-prefix post-pass)
import ruby_rails_edges  # noqa: E402  (plugin #2: Rails/ActiveRecord DSL -> typed edges + ensured nodes)
import csharp_edges  # noqa: E402  (plugin #3: EF Core/ASP.NET declarations -> typed edges + ensured nodes)
import java_edges  # noqa: E402  (plugin #4: Spring/JPA annotations -> typed edges + ensured nodes)
import go_edges  # noqa: E402  (plugin #5: Gin/Echo/Chi routers + GORM models -> typed edges + nodes)
import typescript_edges  # noqa: E402  (plugin #6: NestJS/Express-style/Next.js/TypeORM/Angular -> typed edges)

# Labels the schema actually indexes/embeds (build/indexes.py) — anything else is invisible to search.
VALID_NODE_LABELS = {"Function", "Method", "Class", "Module", "Route"}

# Each entry's STATIC ignore set — the floor every caller of iter_source/run gets. The *.py walk keeps
# framework_edges' EXACT skip set (output-parity with the pre-registry extract_framework_edges shell).
# _BROAD_IGNORE, for the other languages, is VCS/tool dirs, build output (build/bin/obj/target/dist),
# vendor/ + node_modules/, the venv names and Rails' tmp/ + log/. It is not a copy of the verdict census's
# verdict._IGNORE_PARTS, which has since grown public/, coverage/, bower_components/ and the JS bundler dirs.
# In production both floors are topped up by the caller: sync._apply_recognizers passes run(..., exclude=)
# its own rule (sync._recognizer_exclusion) — the vendor/.gitignore filter it applies to the engine's output
# (honouring DEEPMAP_INCLUDE_VENDOR) plus the dirs DeepMap never treats as project source (the virtualenv/env
# family incl. site-packages, dist/, caches, .worktrees/ and .claude/worktrees/ checkouts) — so a sync's
# registry walk never reads a vendored or third-party file, and the default walk skips at least every
# directory the verdict census ignores (drift-locked by tests/o5_recognizer_vendor_seam_r283_gates.py) — the
# TypeScript entry's build/ bin/ obj/ target/ only in the root or a package root (_TS_IGNORE).
_PY_IGNORE = frozenset({"__pycache__", ".venv"})
_BROAD_IGNORE = frozenset({"__pycache__", ".venv", "venv", ".git", "node_modules", ".deepmap",
                           "dist", "build", ".mypy_cache", ".pytest_cache", ".tox", "vendor",
                           "target", ".idea", ".vs", "bin", "obj", "tmp", "log"})
# The TypeScript / JavaScript entry's own floor. build/ bin/ obj/ target/ tmp/ log/ (and out/) are skipped only
# directly in the root or in a package root — a "/name" entry, see walk_files —, where they are build output or
# runtime dirs: deeper down they are NestJS feature modules as often as not (src/log/, src/build/), which the
# engine extracts. The JS tool and output dirs are skipped at any depth.
_TS_IGNORE = frozenset({"__pycache__", ".venv", "venv", ".git", "node_modules", ".deepmap", "dist",
                        ".mypy_cache", ".pytest_cache", ".tox", "vendor", ".idea", ".vs", "bower_components",
                        "coverage", ".next", ".nuxt", ".turbo", ".svelte-kit", ".yarn", ".pnp", ".angular",
                        ".output", "jspm_packages",
                        "/build", "/out", "/bin", "/obj", "/target", "/tmp", "/log"})

_OFF = {"0", "false", "no", "off"}
_REQUIRED_NODE_KEYS = ("key", "label", "name", "source_file", "start_line", "body_summary")


class RecognizerEntry(NamedTuple):
    """One language's semantic recognizer.

    name              : registry id ("python", "ruby") — also the DEEPMAP_RECOGNIZERS CSV token.
    extensions        : file extensions this entry consumes ({".py"}); dispatch is by presence.
    edge_types        : the TypedEdge types this entry may emit — MUST be a subset of
                        framework_edges.ALLOWED_EDGE_TYPES (the persist whitelist stays a static
                        literal there; the registry gates lock the subset).
    impact_edge_types : the ORDERED subset that is blast-radius-traversable — must mirror
                        retrieval.DEFAULT_IMPACT_EDGE_TYPES membership for this entry's types
                        (drift is a gate failure, not a silent bug).
    recognize         : PURE per-file core: (source_text, rel_posix) ->
                        (supplemental_nodes, typed_edges). No fs, no network; exceptions are
                        caught per-file by the walker.
    ignore_parts      : directory names the shared walker never descends into for this entry: a
                        name at any depth, or "/name" — that directory only directly in the walk's
                        root or in a package root (a directory holding a package.json).
    finalize          : OPTIONAL whole-run post-pass (round 7): (workspace_root, this entry's
                        typed_edges in walk order) -> typed_edges, for facts no single file can see
                        (a route prefix mounted in another module; an EF navigation property's type
                        declared in the entity's own file). It may only rewrite edges it was handed;
                        a crash keeps the per-file edges. None (the default) = no post-pass, so a
                        6-field entry behaves exactly as before. A post-pass that reads the tree
                        itself should declare an `exclude=None` keyword: run() then hands it the
                        caller's exclusion predicate (the same one the walk honours); one that
                        declares `files=None` also gets the walk's file list (no second walk).
    """
    name: str
    extensions: frozenset
    edge_types: frozenset
    impact_edge_types: tuple
    recognize: Callable
    ignore_parts: frozenset
    finalize: "Callable | None" = None


def _python_recognize(source: str, rel: str) -> tuple:
    """The Python entry: framework_edges' pure core, verbatim; NO supplemental nodes (framework
    edges are an edges-only contract — the byte-identity property of the Python-only path)."""
    import ast
    return [], framework_edges.edges_from_module(ast.parse(source), rel)


PYTHON = RecognizerEntry(
    name="python",
    extensions=frozenset({".py"}),
    # The exact Track-C Python vocabulary (explicit, NOT derived from ALLOWED — ALLOWED also holds
    # RENDERS, which the Ruby recognizer emits, and IMPORTS, which comes from extraction).
    # + CASCADES (2026-10): SQLAlchemy relationship(cascade=...) / ForeignKey(ondelete=...) and Django
    # on_delete=CASCADE / SET_NULL — already in retrieval.DEFAULT_IMPACT_EDGE_TYPES, so the union is unchanged.
    edge_types=frozenset({"INHERITS", "IMPLEMENTS", "ROUTES_TO", "REFERENCES", "HAS_MANY",
                          "DEPENDS_ON", "HOOKS", "SCHEDULED_AS", "TRIGGERS", "DISPATCHES", "CASCADES"}),
    impact_edge_types=("ROUTES_TO", "DEPENDS_ON", "TRIGGERS", "SCHEDULED_AS", "HOOKS", "INHERITS", "CASCADES"),
    recognize=_python_recognize,
    ignore_parts=_PY_IGNORE,
    # Cross-module route prefixes (include_router / register_blueprint in another file) — the SAME
    # post-pass the legacy extract_framework_edges shell runs, so the two stay byte-equal.
    finalize=framework_edges.compose_route_prefixes,
)

RUBY = RecognizerEntry(
    name="ruby",
    # .erb (P1-3): Rails view templates -> :Module view nodes + RENDERS edges (view -> partial).
    extensions=frozenset({".rb", ".erb"}),
    # + ROUTES_TO (v0.21): config/routes.rb route -> controller#action (cross-file, NAME-hinted).
    # + RENDERS (P1-3): a view renders a partial/template — blast-radius for shared partials.
    edge_types=frozenset({"INHERITS", "HAS_MANY", "REFERENCES", "HOOKS", "CASCADES", "ROUTES_TO",
                          "RENDERS"}),
    # HAS_MANY/REFERENCES stay NON-traversable by default (the settled benchmark-comparability
    # invariant; opt-in via DEEPMAP_IMPACT_PROFILE=rails). CASCADES/ROUTES_TO are already default;
    # RENDERS joins the default (a NEW benchmark-neutral reachability edge, absent from benchmark graphs),
    # so the lockstep union grows by RENDERS in lockstep with retrieval.DEFAULT_IMPACT_EDGE_TYPES.
    impact_edge_types=("HOOKS", "INHERITS", "CASCADES", "ROUTES_TO", "RENDERS"),
    recognize=ruby_rails_edges.recognize,
    ignore_parts=_BROAD_IGNORE,
)

CSHARP = RecognizerEntry(
    name="csharp",
    extensions=frozenset({".cs"}),
    edge_types=frozenset({"INHERITS", "IMPLEMENTS", "ROUTES_TO", "REFERENCES", "HAS_MANY",
                          "DEPENDS_ON", "CASCADES"}),
    # HAS_MANY/REFERENCES/IMPLEMENTS stay NON-traversable by default (the settled benchmark-
    # comparability invariant; opt-in via DEEPMAP_IMPACT_EDGES). Every traversable type below is
    # already in retrieval.DEFAULT_IMPACT_EDGE_TYPES, so the lockstep union is unchanged.
    impact_edge_types=("ROUTES_TO", "DEPENDS_ON", "INHERITS", "CASCADES"),
    recognize=csharp_edges.recognize,
    ignore_parts=_BROAD_IGNORE,
    # Fluent HasMany(i => i.Lines) / HasOne(o => o.Buyer): the target is the navigation property's
    # declared TYPE (List<InvoiceLine> Lines, in the entity's own file) instead of a singularized guess.
    finalize=csharp_edges.finalize,
)

JAVA = RecognizerEntry(
    name="java",
    extensions=frozenset({".java"}),
    edge_types=frozenset({"INHERITS", "IMPLEMENTS", "ROUTES_TO", "HAS_MANY", "REFERENCES",
                          "DEPENDS_ON", "CASCADES", "SCHEDULED_AS", "TRIGGERS"}),
    # HAS_MANY/REFERENCES/IMPLEMENTS stay NON-traversable by default (benchmark-comparability
    # invariant). Every traversable type below is already in retrieval.DEFAULT_IMPACT_EDGE_TYPES
    # (Python/C# contribute them), so the lockstep union is unchanged — retrieval.py stays frozen.
    impact_edge_types=("ROUTES_TO", "DEPENDS_ON", "INHERITS", "CASCADES", "SCHEDULED_AS", "TRIGGERS"),
    recognize=java_edges.recognize,
    ignore_parts=_BROAD_IGNORE,
)

GO = RecognizerEntry(
    name="go",
    extensions=frozenset({".go"}),
    # No IMPLEMENTS: Go interface satisfaction is implicit and not statically knowable -> never emitted.
    edge_types=frozenset({"ROUTES_TO", "HAS_MANY", "REFERENCES", "CASCADES", "DEPENDS_ON", "INHERITS"}),
    # HAS_MANY/REFERENCES stay NON-traversable by default (benchmark-comparability invariant). The
    # traversable set below is already in retrieval.DEFAULT_IMPACT_EDGE_TYPES -> union unchanged.
    impact_edge_types=("ROUTES_TO", "DEPENDS_ON", "INHERITS", "CASCADES"),
    recognize=go_edges.recognize,
    ignore_parts=_BROAD_IGNORE,
)

TYPESCRIPT = RecognizerEntry(
    name="typescript",
    # JavaScript too: the same frameworks run on both (.d.ts and minified files are skipped by the core).
    extensions=frozenset({".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}),
    edge_types=frozenset({"INHERITS", "IMPLEMENTS", "ROUTES_TO", "HAS_MANY", "REFERENCES", "DEPENDS_ON",
                          "CASCADES", "SCHEDULED_AS", "TRIGGERS", "HOOKS"}),
    # HAS_MANY/REFERENCES/IMPLEMENTS stay NON-traversable by default (benchmark-comparability invariant).
    # Every traversable type below is already in retrieval.DEFAULT_IMPACT_EDGE_TYPES -> union unchanged.
    impact_edge_types=("ROUTES_TO", "DEPENDS_ON", "TRIGGERS", "SCHEDULED_AS", "HOOKS", "INHERITS",
                       "CASCADES"),
    recognize=typescript_edges.recognize,
    ignore_parts=_TS_IGNORE,
    # Router mounts in another module, each NestJS app's global prefix, Next.js project roots, imports placed
    # in the tree (tsconfig paths / baseUrl, workspace packages, barrels), one handler routed twice.
    finalize=typescript_edges.finalize,
)

REGISTRY: tuple = (PYTHON, RUBY, CSHARP, JAVA, GO, TYPESCRIPT)


def _enabled_entries() -> list:
    """The entries DEEPMAP_RECOGNIZERS allows: unset/on -> all; off-value -> none; CSV -> named."""
    try:
        raw = (os.environ.get("DEEPMAP_RECOGNIZERS") or "").strip().lower()
    except Exception:
        raw = ""
    if raw in _OFF:
        return []
    if not raw or raw in {"1", "true", "yes", "on", "all"}:
        return list(REGISTRY)
    wanted = {t.strip() for t in raw.split(",") if t.strip()}
    return [r for r in REGISTRY if r.name in wanted]


def disabled_entries() -> list:
    """The registry entries DEEPMAP_RECOGNIZERS switches OFF, in registry order ([] by default). Their
    typed edges from an earlier sync are stale by definition — edge_resolve.prune_disabled_recognizer_edges
    removes them, including the ones anchored at the orphan nodes the disabled recognizer no longer
    emits (the live-scoped prune cannot reach those). Never raises."""
    try:
        on = {r.name for r in _enabled_entries()}
        return [r for r in REGISTRY if r.name not in on]
    except Exception:  # noqa: BLE001
        return []


def _excluded(exclude, rel: str) -> bool:
    """The caller's exclusion predicate for one root-relative posix path; a predicate that raises excludes
    nothing (the walk never raises)."""
    try:
        return bool(exclude(rel))
    except Exception:  # noqa: BLE001
        return False


def walk_files(root, extensions, ignore_parts):
    """(rel_posix, path) of every file under `root` with one of `extensions` (CASE-INSENSITIVE suffix), in
    the order `sorted(root.rglob("*"))` gives them, never descending into an ignored directory: a name of
    `ignore_parts` at any depth, a "/name" entry only directly in the root or in a package root (a
    directory holding a package.json). Directories are pruned while walking, so node_modules/ costs one
    directory entry, not a stat per file under it. Matched relative to `root`, never against the path above
    it (a repo under /tmp or build/ keeps every file: the v0.18.0 Linux-CI lesson). Symlinked directories are
    not followed (nor were they by rglob). Unreadable directories are skipped. macOS metadata is never
    listed, whatever the caller's ignore set (framework_edges.is_macos_metadata): a `__MACOSX/` or `.AppleDouble/`
    directory is pruned, an AppleDouble `._<name>` twin skipped. Never raises."""
    root = Path(root)
    top = os.fspath(root)
    wanted = {str(e).lower() for e in extensions}
    names = {str(x) for x in ignore_parts if not str(x).startswith("/")}
    anchored = {str(x)[1:] for x in ignore_parts if str(x).startswith("/")}
    meta = framework_edges.is_macos_metadata
    found: list = []
    try:
        for dirpath, dirnames, filenames in os.walk(top):
            pkg_root = dirpath == top or "package.json" in filenames
            dirnames[:] = [d for d in dirnames if d not in names and not (pkg_root and d in anchored)
                           and not meta(d, is_dir=True)]
            for fn in filenames:
                if meta(fn):
                    continue
                p = Path(dirpath) / fn
                if p.suffix.lower() not in wanted:
                    continue
                found.append(p)
    except Exception:  # noqa: BLE001 — a walk that cannot finish keeps what it found
        pass
    for p in sorted(found):
        try:
            yield p.relative_to(root).as_posix(), p
        except ValueError:
            continue


def iter_source(root, entry: RecognizerEntry, exclude=None):
    """Shared impure walker: yield (rel_posix, source_text) for `entry`'s files under `root` —
    walk_files (one pruned walk, sorted, a CASE-INSENSITIVE suffix test, the entry's ignore set), utf-8-sig +
    errors="replace" (BOM-safe), per-file isolation (an unreadable file is skipped).

    `exclude` (optional): a predicate over the root-relative posix path; a file it returns True for is
    skipped BEFORE it is read, on top of `entry.ignore_parts`. build/sync.py passes its exclusion rule
    here (sync._recognizer_exclusion: its vendor/.gitignore filter + the directories DeepMap never treats
    as project source), so no recognizer reads — or parses — a vendored or third-party file.

    The suffix test is case-insensitive on purpose. This used to be one rglob per extension keyed on
    the literal extension, and rglob is case-sensitive on POSIX (it is NOT on Windows) — so on Linux/macOS a legacy
    `MODEL.RB` / `ORDER.CS` / `UNIT1.PAS` file was skipped by EVERY recognizer and its whole typed-edge
    layer vanished silently. That is the same defect class as the 2026-08 Delphi allowlist report, one
    layer down, and it was invisible from a Windows dev box. One walk also does strictly less
    filesystem work than N globs, and pruning ignored directories keeps a node_modules/ out of it.
    """
    root = Path(root)
    for rel, p in walk_files(root, entry.extensions, entry.ignore_parts):
        try:
            if not p.is_file():
                continue
        except OSError:
            continue
        if exclude is not None and _excluded(exclude, rel):
            continue                                   # never read, let alone parsed
        try:
            yield rel, p.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue


def validate_supplemental(nodes, entry_name: str) -> list:
    """Drop/normalize nonconforming supplemental nodes. A bad node must DEGRADE (dropped + one
    stderr line), never crash the push (missing keys are KeyErrors there) and never push an
    out-of-schema label (silently invisible to every index). Never raises."""
    out: list = []
    for n in (nodes or []):
        try:
            if not isinstance(n, dict):
                raise ValueError("not a dict")
            if n.get("label") not in VALID_NODE_LABELS:
                raise ValueError(f"label {n.get('label')!r} outside the indexed set")
            for k in _REQUIRED_NODE_KEYS:
                v = n.get(k)
                if v is None or (isinstance(v, str) and not v.strip()):
                    raise ValueError(f"missing/empty {k!r}")
            nn = dict(n)
            nn["source_file"] = str(nn["source_file"]).replace("\\", "/")
            nn["start_line"] = int(nn["start_line"])
            nn.setdefault("documented", False)
            out.append(nn)
        except Exception as exc:
            print(f"[deepmap sync] {entry_name}-recognizer dropped a nonconforming node ({exc})",
                  file=sys.stderr)
            continue
    return out


def _finalize_kwargs(fin, exclude, files=None) -> dict:
    """{"exclude": exclude} when the post-pass `fin` declares an `exclude` parameter (or **kwargs), plus
    {"files": files} when it declares `files` (the root-relative files the walk yielded for its entry, so a
    post-pass that needs the tree's file list does not walk it again); {} otherwise — every other post-pass
    is called exactly as before. Never raises."""
    if exclude is None and files is None:
        return {}
    try:
        params = inspect.signature(fin).parameters
    except (TypeError, ValueError):
        return {}
    out: dict = {}
    if exclude is not None and ("exclude" in params
                                or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())):
        out["exclude"] = exclude
    if files is not None and "files" in params:
        out["files"] = files
    return out


def run(workspace_root, exclude=None) -> tuple:
    """The fan-out build/sync.py calls at the old extract_framework_edges call site: every enabled +
    applicable recognizer over its files. Returns (supplemental_nodes, typed_edges); ([], []) when
    the layer is off. `exclude` (optional) is handed to every entry's walk (iter_source) — sync passes
    its exclusion rule, so a vendored or third-party file never reaches a per-file recognizer and no edge
    from one reaches a `finalize` post-pass; without it the walk is the entries' own ignore sets only.
    A post-pass that reads the tree itself (e.g. to find router mounts in other modules) receives the
    predicate too when it declares an `exclude` keyword (RecognizerEntry.finalize). Never raises."""
    supp: list = []
    edges: list = []
    try:
        root = Path(workspace_root)
        for entry in _enabled_entries():
            entry_edges: list = []
            walked: list = []                          # the entry's files, handed to a post-pass that asks
            for rel, source in iter_source(root, entry, exclude):
                walked.append(rel)
                try:
                    nodes_i, edges_i = entry.recognize(source, rel)
                except Exception as e:  # noqa: BLE001 — skip the one bad file, never abort the sweep
                    print(f"[deepmap sync] {entry.name}-recognizer skipped {rel} "
                          f"({type(e).__name__})", file=sys.stderr)
                    continue
                if nodes_i:
                    supp.extend(validate_supplemental(nodes_i, entry.name))
                entry_edges.extend(edges_i or [])
            fin = getattr(entry, "finalize", None)
            if fin is not None and entry_edges:
                try:
                    entry_edges = list(fin(root, entry_edges, **_finalize_kwargs(fin, exclude, walked)))
                except Exception as e:  # noqa: BLE001 — a post-pass crash keeps the per-file edges
                    print(f"[deepmap sync] {entry.name}-recognizer post-pass skipped "
                          f"({type(e).__name__})", file=sys.stderr)
            edges.extend(entry_edges)
    except Exception as e:  # noqa: BLE001 — the recognizer layer must never break a sync
        print(f"[deepmap sync] recognizer layer degraded ({type(e).__name__})", file=sys.stderr)
    return supp, edges


_MERGE_SUMMARY_CAP = 600  # a merged body_summary is capped so a fat model can't unboundedly grow one


def _merge_summaries_enabled() -> bool:
    """Opt-in (default OFF): on a (name, source_file) collision, APPEND the recognizer summary's fact
    tail into the base node's body_summary in place instead of dropping it. Default OFF because
    body_summary is EMBEDDED — it is a ranking/benchmark input, and the default graph must stay
    byte-identical until a benchmark A/B blesses the change (the same reason the graph arm is frozen)."""
    return os.environ.get("DEEPMAP_MERGE_SUMMARIES", "").strip().lower() in {"1", "true", "yes", "on"}


def _fact_tail(summary) -> str:
    """The DSL fact tail of a recognizer summary 'Name: kind; fact; fact' -> 'fact; fact' (or '').

    We keep only what follows the first ';' so the recognizer's name-echo prefix ('Order: ActiveRecord
    model') is not duplicated into the base summary — only the association facts carry new signal."""
    if not isinstance(summary, str) or ";" not in summary:
        return ""
    return summary.split(";", 1)[1].strip()


def merge_supplemental(nodes, supplemental) -> list:
    """Base-extractor nodes + supplemental recognizer nodes, deduped by (name, source_file) with
    the BASE node winning — a duplicate would make the resolver return two keys for one symbol and
    _grade would demote every edge on it to AMBIGUOUS (self-inflicted). Returns the ORIGINAL list
    object when nothing is added (byte-identity for the Python-only path). Never raises.

    Opt-in (DEEPMAP_MERGE_SUMMARIES=1): a collision APPENDS the recognizer summary's fact tail into
    the base node's body_summary IN PLACE (deduped, capped, idempotent) rather than dropping it, so
    the association signal reaches the vector arm on the graphify happy path too. The list-identity
    contract holds in BOTH modes — the merge mutates a base string, it never appends a node."""
    if not supplemental:
        return nodes
    try:
        merge_sum = _merge_summaries_enabled()
        seen: dict = {}
        for n in nodes:
            if isinstance(n, dict):
                seen[(n.get("name"), str(n.get("source_file", "")).replace("\\", "/"))] = n
        added: list = []
        for n in supplemental:
            k = (n.get("name"), str(n.get("source_file", "")).replace("\\", "/"))
            if k in seen:
                if merge_sum:
                    base = seen[k]
                    tail = _fact_tail(n.get("body_summary", "")) if isinstance(base, dict) else ""
                    cur = base.get("body_summary") if isinstance(base, dict) else None
                    if tail and isinstance(cur, str) and tail not in cur:
                        base["body_summary"] = (cur.rstrip() + "; " + tail)[:_MERGE_SUMMARY_CAP]
                continue
            seen[k] = n
            added.append(n)
        return (list(nodes) + added) if added else nodes
    except Exception:
        return nodes
