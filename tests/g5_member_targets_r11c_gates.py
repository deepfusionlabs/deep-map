#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Round-11c engine gate (WE, C1): a G5 member fall-back (`call_member`, R11b/G1) excludes ONLY :Module and :Class
targets — it keeps :Method AND :Function — and removing an excluded target never promotes a kept edge's grade.

The defect (the two R11b G1 skeptics, real engine): 11b's F2 kept only :Method targets for a G5 name fall-back. That
dropped TRUE edges into project module-level :Function nodes reached through an imported-module receiver when the
caller's file has 2+ classes defining a method of that name (graphify binds the call to one of them, G5 turns it into
the bare name): this repo's `import language_recognizers as LR` / `LR.run(root)`
(tests/typed_edges_r7_gates.py -> build/language_recognizers.py::run, 3 true edges, in-degree 21 -> 7),
`helpers.render(v)`, `storage.close(self)`. And "graded by the count left" PROMOTED the false sibling that remained
AMBIGUOUS -> INFERRED (fastapi: _FrontendRoute.handle's `self.app(...)` -> APIRouter.app once the app.py :Module was
removed; probe2: Order.close's `storage.close(self)` -> Invoice.close).
The fix (sync._resolve_calls): a member name keeps every candidate except :Module (a file is not callable) and :Class
nodes, and it is graded on the candidate count BEFORE that exclusion — the grade the name fan-out always gave — so
the exclusion only ever removes edges. F1 (the dunder rule) is unchanged; flask keeps Environment.__init__ at 0
out-edges and json/tag.py at in-degree 0 (tests/g5_fallback_r11b_gates.py).

Sections (hermetic unless noted — temp dirs, no Neo4j / network):
  A  map_graph on a frozen graphify 0.8.39 graph of the probe tree: the 6 G5 member callers carry call_member
     (unchanged)
  B  the push in every flag mode: the true :Function edges (LR.run, helpers.render, h2.slugify, storage.close) are kept;
     no member edge lands on a :Module / :Class; no false promotion (Proxy.__call__ -> Router.app, Order.close ->
     Invoice.close stay AMBIGUOUS); the result equals the pre-11b fan-out minus :Module/:Class targets, grades identical
  C  _resolve_calls unit cases (the grade is the pre-exclusion count; a non-member caller is untouched; F8b / Go filters
     still count first)
  D  the REAL engine on the probe tree, ENGINE-VERSION-AWARE (skipped when graphify is absent): the installed release
     picks its measured engine line (see ENGINE LINES below; an unmeasured version takes the NEAREST measured release's
     line and says so), and the real push must equal that line's frozen-graph push exactly — a wrong line goes RED
  E  the graphify 0.8.47+ graph shape (hermetic, every host): 5 member callers, no default-mode edge for gate_d, only
     CALLS_PLUS recovers `LR.run` -> lib/recognizers.py::run (INFERRED); every engine-independent assertion unchanged
  Z  DEEPMAP_SELF_CALL_REBIND=0: the push byte-identical to the frozen pre-fix output in every flag mode
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build"
sys.path.insert(0, str(BUILD))

import graphify_extract as GX  # noqa: E402

_PASS = _FAIL = 0
_TMP = Path(tempfile.mkdtemp(prefix="dm_r11c_g5m_")).resolve()   # created by THIS gate, removed by exact path
_FLAGS = ("DEEPMAP_PRECISE_CALLS", "DEEPMAP_GRAPHIFY_CALLS_PLUS", "DEEPMAP_GRAPHIFY_CALLS_PLUS_TESTS",
          "DEEPMAP_LOCAL_DEF_CALLS", "DEEPMAP_FALSE_CALL_FILTER", "DEEPMAP_DROP_APP_TO_TEST_CALLS",
          "DEEPMAP_SELF_CALL_REBIND", "DEEPMAP_EXTRACTOR", "DEEPMAP_EXCLUDE_TESTS", "DEEPMAP_INCLUDE_VENDOR",
          "DEEPMAP_RESOLVED_IMPORTS", "DEEPMAP_SUPPLEMENTAL_TS_RUST", "DEEPMAP_MODULE_DOCSTRINGS",
          "DEEPMAP_SUMMARY_NORMALIZE", "DEEPMAP_CASE_INSENSITIVE_EXT", "DEEPMAP_RESCUE_SKIPPED_DIRS",
          "DEEPMAP_CLEAN_OWNER_NAMES")
MODES = {"off": {}, "plus": {"DEEPMAP_GRAPHIFY_CALLS_PLUS": "1"}, "precise": {"DEEPMAP_PRECISE_CALLS": "1"},
         "both": {"DEEPMAP_PRECISE_CALLS": "1", "DEEPMAP_GRAPHIFY_CALLS_PLUS": "1"}}


def check(label: str, cond: bool, detail="") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {label}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}{(' — ' + str(detail)[:900]) if detail != '' else ''}")


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


def _mode_env(mode: str, **extra) -> dict:
    env = {f: None for f in _FLAGS}
    env.update(MODES[mode])
    env.update(extra)
    return env


def _load(name: str, alias: str):
    spec = importlib.util.spec_from_file_location(alias, BUILD / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


S = _load("sync", "dm_r11c_g5m_sync")


# ---- the probe tree (the two R11b G1 skeptics' probes + this repo's LR.run shape) ------------------------------
TREE = {
    "pkg/__init__.py": "",
    "pkg/helpers.py": (
        "def render(x):\n"
        "    return str(x)\n"
        "\n"
        "\n"
        "def slugify(text):\n"
        "    return text.lower()\n"
        "\n"
        "\n"
        "def close(obj):\n"
        "    return obj\n"
    ),
    "pkg/views.py": (
        "from pkg import helpers\n"
        "from . import helpers as h2\n"
        "\n"
        "\n"
        "class ListView:\n"
        "    def render(self):\n"
        "        return 'list'\n"
        "\n"
        "    def slugify(self, t):\n"
        "        return t\n"
        "\n"
        "\n"
        "class DetailView:\n"
        "    def render(self):\n"
        "        return 'detail'\n"
        "\n"
        "    def slugify(self, t):\n"
        "        return t\n"
        "\n"
        "\n"
        "def page(value):\n"
        "    return helpers.render(value)\n"
        "\n"
        "\n"
        "def page2(value):\n"
        "    return h2.slugify(value)\n"
        "\n"
        "\n"
        "class Controller:\n"
        "    def show(self, value):\n"
        "        return helpers.render(value)\n"
    ),
    "pkg/models.py": (
        "from pkg import helpers as storage\n"
        "\n"
        "\n"
        "class Order:\n"
        "    def close(self):\n"
        "        return storage.close(self)\n"
        "\n"
        "\n"
        "class Invoice:\n"
        "    def close(self):\n"
        "        return 2\n"
    ),
    "pkg/proxy.py": (
        "class Proxy:\n"
        "    def __init__(self, app):\n"
        "        self.app = app\n"
        "\n"
        "    def __call__(self, scope):\n"
        "        return self.app(scope)\n"
        "\n"
        "\n"
        "class Router:\n"
        "    def app(self, scope):\n"
        "        return scope\n"
    ),
    "examples/app.py": "APP = 1\n",
    "lib/__init__.py": "",
    "lib/recognizers.py": (
        "def run(root):\n"
        "    return [root]\n"
    ),
    "checks/gates.py": (
        "from lib import recognizers as LR\n"
        "\n"
        "\n"
        "class _Tx:\n"
        "    def run(self, q):\n"
        "        return q\n"
        "\n"
        "\n"
        "class _Sess:\n"
        "    def run(self, q):\n"
        "        return q\n"
        "\n"
        "\n"
        "def gate_d(root):\n"
        "    return LR.run(root)\n"
    ),
}
TREE["pkg/kinds.py"] = "class slugify:\n    pass\n"

# graphify 0.8.39's graph.json for TREE (captured from the real engine; nodes' _origin and links' weight dropped) —
# identical on 0.8.39-0.8.46; 0.8.47+ emit it minus one link (_graph_nolink(), see ENGINE LINES)
GRAPH_JSON = r"""
{"nodes": [
{"file_type": "code", "id": "checks_gates", "label": "gates.py", "source_file": "checks/gates.py", "source_location": "L1"},
{"file_type": "code", "id": "checks_gates_tx", "label": "_Tx", "source_file": "checks/gates.py", "source_location": "L4"},
{"file_type": "code", "id": "checks_gates_tx_run", "label": ".run()", "source_file": "checks/gates.py", "source_location": "L5"},
{"file_type": "code", "id": "checks_gates_sess", "label": "_Sess", "source_file": "checks/gates.py", "source_location": "L9"},
{"file_type": "code", "id": "checks_gates_sess_run", "label": ".run()", "source_file": "checks/gates.py", "source_location": "L10"},
{"file_type": "code", "id": "checks_gates_gate_d", "label": "gate_d()", "source_file": "checks/gates.py", "source_location": "L14"},
{"file_type": "code", "id": "examples_app", "label": "app.py", "source_file": "examples/app.py", "source_location": "L1"},
{"file_type": "code", "id": "lib_init", "label": "__init__.py", "source_file": "lib/__init__.py", "source_location": "L1"},
{"file_type": "code", "id": "lib_recognizers", "label": "recognizers.py", "source_file": "lib/recognizers.py", "source_location": "L1"},
{"file_type": "code", "id": "lib_recognizers_run", "label": "run()", "source_file": "lib/recognizers.py", "source_location": "L1"},
{"file_type": "code", "id": "pkg_init", "label": "__init__.py", "source_file": "pkg/__init__.py", "source_location": "L1"},
{"file_type": "code", "id": "pkg_helpers", "label": "helpers.py", "source_file": "pkg/helpers.py", "source_location": "L1"},
{"file_type": "code", "id": "pkg_helpers_render", "label": "render()", "source_file": "pkg/helpers.py", "source_location": "L1"},
{"file_type": "code", "id": "pkg_helpers_slugify", "label": "slugify()", "source_file": "pkg/helpers.py", "source_location": "L5"},
{"file_type": "code", "id": "pkg_helpers_close", "label": "close()", "source_file": "pkg/helpers.py", "source_location": "L9"},
{"file_type": "code", "id": "pkg_kinds", "label": "kinds.py", "source_file": "pkg/kinds.py", "source_location": "L1"},
{"file_type": "code", "id": "pkg_kinds_slugify", "label": "slugify", "source_file": "pkg/kinds.py", "source_location": "L1"},
{"file_type": "code", "id": "pkg_models", "label": "models.py", "source_file": "pkg/models.py", "source_location": "L1"},
{"file_type": "code", "id": "pkg_models_order", "label": "Order", "source_file": "pkg/models.py", "source_location": "L4"},
{"file_type": "code", "id": "pkg_models_order_close", "label": ".close()", "source_file": "pkg/models.py", "source_location": "L5"},
{"file_type": "code", "id": "pkg_models_invoice", "label": "Invoice", "source_file": "pkg/models.py", "source_location": "L9"},
{"file_type": "code", "id": "pkg_models_invoice_close", "label": ".close()", "source_file": "pkg/models.py", "source_location": "L10"},
{"file_type": "code", "id": "pkg_proxy", "label": "proxy.py", "source_file": "pkg/proxy.py", "source_location": "L1"},
{"file_type": "code", "id": "pkg_proxy_proxy", "label": "Proxy", "source_file": "pkg/proxy.py", "source_location": "L1"},
{"file_type": "code", "id": "pkg_proxy_proxy_init", "label": ".__init__()", "source_file": "pkg/proxy.py", "source_location": "L2"},
{"file_type": "code", "id": "pkg_proxy_proxy_call", "label": ".__call__()", "source_file": "pkg/proxy.py", "source_location": "L5"},
{"file_type": "code", "id": "pkg_proxy_router", "label": "Router", "source_file": "pkg/proxy.py", "source_location": "L9"},
{"file_type": "code", "id": "pkg_proxy_router_app", "label": ".app()", "source_file": "pkg/proxy.py", "source_location": "L10"},
{"file_type": "code", "id": "pkg_views", "label": "views.py", "source_file": "pkg/views.py", "source_location": "L1"},
{"file_type": "code", "id": "pkg_views_listview", "label": "ListView", "source_file": "pkg/views.py", "source_location": "L5"},
{"file_type": "code", "id": "pkg_views_listview_render", "label": ".render()", "source_file": "pkg/views.py", "source_location": "L6"},
{"file_type": "code", "id": "pkg_views_listview_slugify", "label": ".slugify()", "source_file": "pkg/views.py", "source_location": "L9"},
{"file_type": "code", "id": "pkg_views_detailview", "label": "DetailView", "source_file": "pkg/views.py", "source_location": "L13"},
{"file_type": "code", "id": "pkg_views_detailview_render", "label": ".render()", "source_file": "pkg/views.py", "source_location": "L14"},
{"file_type": "code", "id": "pkg_views_detailview_slugify", "label": ".slugify()", "source_file": "pkg/views.py", "source_location": "L17"},
{"file_type": "code", "id": "pkg_views_page", "label": "page()", "source_file": "pkg/views.py", "source_location": "L21"},
{"file_type": "code", "id": "pkg_views_page2", "label": "page2()", "source_file": "pkg/views.py", "source_location": "L25"},
{"file_type": "code", "id": "pkg_views_controller", "label": "Controller", "source_file": "pkg/views.py", "source_location": "L29"},
{"file_type": "code", "id": "pkg_views_controller_show", "label": ".show()", "source_file": "pkg/views.py", "source_location": "L30"}
], "links": [
{"confidence": "EXTRACTED", "context": "import", "relation": "imports_from", "source": "checks_gates", "source_file": "checks/gates.py", "source_location": "L1", "target": "lib"},
{"confidence": "EXTRACTED", "relation": "contains", "source": "checks_gates", "source_file": "checks/gates.py", "source_location": "L4", "target": "checks_gates_tx"},
{"confidence": "EXTRACTED", "relation": "method", "source": "checks_gates_tx", "source_file": "checks/gates.py", "source_location": "L5", "target": "checks_gates_tx_run"},
{"confidence": "EXTRACTED", "relation": "contains", "source": "checks_gates", "source_file": "checks/gates.py", "source_location": "L9", "target": "checks_gates_sess"},
{"confidence": "EXTRACTED", "relation": "method", "source": "checks_gates_sess", "source_file": "checks/gates.py", "source_location": "L10", "target": "checks_gates_sess_run"},
{"confidence": "EXTRACTED", "relation": "contains", "source": "checks_gates", "source_file": "checks/gates.py", "source_location": "L14", "target": "checks_gates_gate_d"},
{"confidence": "EXTRACTED", "context": "call", "relation": "calls", "source": "checks_gates_gate_d", "source_file": "checks/gates.py", "source_location": "L15", "target": "checks_gates_sess_run"},
{"confidence": "EXTRACTED", "relation": "contains", "source": "lib_recognizers", "source_file": "lib/recognizers.py", "source_location": "L1", "target": "lib_recognizers_run"},
{"confidence": "EXTRACTED", "relation": "contains", "source": "pkg_helpers", "source_file": "pkg/helpers.py", "source_location": "L1", "target": "pkg_helpers_render"},
{"confidence": "EXTRACTED", "relation": "contains", "source": "pkg_helpers", "source_file": "pkg/helpers.py", "source_location": "L5", "target": "pkg_helpers_slugify"},
{"confidence": "EXTRACTED", "relation": "contains", "source": "pkg_helpers", "source_file": "pkg/helpers.py", "source_location": "L9", "target": "pkg_helpers_close"},
{"confidence": "EXTRACTED", "relation": "contains", "source": "pkg_kinds", "source_file": "pkg/kinds.py", "source_location": "L1", "target": "pkg_kinds_slugify"},
{"confidence": "EXTRACTED", "context": "import", "relation": "imports_from", "source": "pkg_models", "source_file": "pkg/models.py", "source_location": "L1", "target": "pkg"},
{"confidence": "EXTRACTED", "relation": "contains", "source": "pkg_models", "source_file": "pkg/models.py", "source_location": "L4", "target": "pkg_models_order"},
{"confidence": "EXTRACTED", "relation": "method", "source": "pkg_models_order", "source_file": "pkg/models.py", "source_location": "L5", "target": "pkg_models_order_close"},
{"confidence": "EXTRACTED", "relation": "contains", "source": "pkg_models", "source_file": "pkg/models.py", "source_location": "L9", "target": "pkg_models_invoice"},
{"confidence": "EXTRACTED", "relation": "method", "source": "pkg_models_invoice", "source_file": "pkg/models.py", "source_location": "L10", "target": "pkg_models_invoice_close"},
{"confidence": "EXTRACTED", "context": "call", "relation": "calls", "source": "pkg_models_order_close", "source_file": "pkg/models.py", "source_location": "L6", "target": "pkg_models_invoice_close"},
{"confidence": "EXTRACTED", "relation": "contains", "source": "pkg_proxy", "source_file": "pkg/proxy.py", "source_location": "L1", "target": "pkg_proxy_proxy"},
{"confidence": "EXTRACTED", "relation": "method", "source": "pkg_proxy_proxy", "source_file": "pkg/proxy.py", "source_location": "L2", "target": "pkg_proxy_proxy_init"},
{"confidence": "EXTRACTED", "relation": "method", "source": "pkg_proxy_proxy", "source_file": "pkg/proxy.py", "source_location": "L5", "target": "pkg_proxy_proxy_call"},
{"confidence": "EXTRACTED", "relation": "contains", "source": "pkg_proxy", "source_file": "pkg/proxy.py", "source_location": "L9", "target": "pkg_proxy_router"},
{"confidence": "EXTRACTED", "relation": "method", "source": "pkg_proxy_router", "source_file": "pkg/proxy.py", "source_location": "L10", "target": "pkg_proxy_router_app"},
{"confidence": "EXTRACTED", "context": "call", "relation": "calls", "source": "pkg_proxy_proxy_call", "source_file": "pkg/proxy.py", "source_location": "L6", "target": "pkg_proxy_router_app"},
{"confidence": "EXTRACTED", "context": "import", "relation": "imports_from", "source": "pkg_views", "source_file": "pkg/views.py", "source_location": "L1", "target": "pkg"},
{"confidence": "EXTRACTED", "context": "import", "relation": "imports_from", "source": "pkg_views", "source_file": "pkg/views.py", "source_location": "L2", "target": "pkg_init"},
{"confidence": "EXTRACTED", "relation": "contains", "source": "pkg_views", "source_file": "pkg/views.py", "source_location": "L5", "target": "pkg_views_listview"},
{"confidence": "EXTRACTED", "relation": "method", "source": "pkg_views_listview", "source_file": "pkg/views.py", "source_location": "L6", "target": "pkg_views_listview_render"},
{"confidence": "EXTRACTED", "relation": "method", "source": "pkg_views_listview", "source_file": "pkg/views.py", "source_location": "L9", "target": "pkg_views_listview_slugify"},
{"confidence": "EXTRACTED", "relation": "contains", "source": "pkg_views", "source_file": "pkg/views.py", "source_location": "L13", "target": "pkg_views_detailview"},
{"confidence": "EXTRACTED", "relation": "method", "source": "pkg_views_detailview", "source_file": "pkg/views.py", "source_location": "L14", "target": "pkg_views_detailview_render"},
{"confidence": "EXTRACTED", "relation": "method", "source": "pkg_views_detailview", "source_file": "pkg/views.py", "source_location": "L17", "target": "pkg_views_detailview_slugify"},
{"confidence": "EXTRACTED", "relation": "contains", "source": "pkg_views", "source_file": "pkg/views.py", "source_location": "L21", "target": "pkg_views_page"},
{"confidence": "EXTRACTED", "relation": "contains", "source": "pkg_views", "source_file": "pkg/views.py", "source_location": "L25", "target": "pkg_views_page2"},
{"confidence": "EXTRACTED", "relation": "contains", "source": "pkg_views", "source_file": "pkg/views.py", "source_location": "L29", "target": "pkg_views_controller"},
{"confidence": "EXTRACTED", "relation": "method", "source": "pkg_views_controller", "source_file": "pkg/views.py", "source_location": "L30", "target": "pkg_views_controller_show"},
{"confidence": "EXTRACTED", "context": "call", "relation": "calls", "source": "pkg_views_page", "source_file": "pkg/views.py", "source_location": "L22", "target": "pkg_views_detailview_render"},
{"confidence": "EXTRACTED", "context": "call", "relation": "calls", "source": "pkg_views_page2", "source_file": "pkg/views.py", "source_location": "L26", "target": "pkg_views_detailview_slugify"},
{"confidence": "EXTRACTED", "context": "call", "relation": "calls", "source": "pkg_views_controller_show", "source_file": "pkg/views.py", "source_location": "L31", "target": "pkg_views_detailview_render"},
{"confidence": "EXTRACTED", "context": "submodule_import", "relation": "imports_from", "source": "checks_gates", "source_file": "checks/gates.py", "source_location": "L1", "target": "lib_recognizers"},
{"confidence": "EXTRACTED", "context": "submodule_import", "relation": "imports_from", "source": "pkg_models", "source_file": "pkg/models.py", "source_location": "L1", "target": "pkg_helpers"},
{"confidence": "EXTRACTED", "context": "submodule_import", "relation": "imports_from", "source": "pkg_views", "source_file": "pkg/views.py", "source_location": "L1", "target": "pkg_helpers"}
]}
"""


def _write_tree(root: Path, tree: dict) -> Path:
    for rel, text in tree.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode("utf-8"))
    return root


def _graph() -> dict:
    return json.loads(GRAPH_JSON)


def _passes(root: Path, nodes: list, calls: list):
    """sync's push-time passes in _sync_pass order, then _resolve_calls (what push_to_neo4j persists)."""
    with contextlib.redirect_stderr(io.StringIO()):
        c2, _n = S._unkey_receiver_calls(root, nodes, list(calls))
        S._attach_bare_calls(root, nodes, c2)
        S._attach_local_defs(root, nodes, c2)
        S._attach_local_def_calls(root, nodes, c2)
        return S._resolve_calls(nodes, c2)


def _run(mode: str, root: Path, graph=None, **extra):
    with _env(**_mode_env(mode, **extra)), contextlib.redirect_stderr(io.StringIO()):
        nodes, calls, _imports = GX.map_graph(graph or _graph(), root=root)
        edges = _passes(root, nodes, calls)
    return nodes, calls, edges


def _of(edges: dict, caller: str) -> dict:
    return {b: c for (a, b), c in edges.items() if a == caller}


def _labels(nodes) -> dict:
    return {n["key"]: n["label"] for n in nodes}


MEMBER = {"checks_gates_gate_d": ["run"], "pkg_models_order_close": ["close"], "pkg_proxy_proxy_call": ["app"],
          "pkg_views_page": ["render"], "pkg_views_page2": ["slugify"], "pkg_views_controller_show": ["render"]}
# the pre-11b push (the merged copy's build, flags off): the member names fanned out over EVERY label
PRE_11B = {
    ("checks_gates_gate_d", "checks_gates_sess_run"): "AMBIGUOUS",
    ("checks_gates_gate_d", "checks_gates_tx_run"): "AMBIGUOUS",
    ("checks_gates_gate_d", "lib_recognizers_run"): "AMBIGUOUS",
    ("pkg_models_order_close", "pkg_helpers_close"): "AMBIGUOUS",
    ("pkg_models_order_close", "pkg_models_invoice_close"): "AMBIGUOUS",
    ("pkg_proxy_proxy_call", "examples_app"): "AMBIGUOUS",
    ("pkg_proxy_proxy_call", "pkg_proxy_router_app"): "AMBIGUOUS",
    ("pkg_views_controller_show", "pkg_helpers_render"): "AMBIGUOUS",
    ("pkg_views_controller_show", "pkg_views_detailview_render"): "AMBIGUOUS",
    ("pkg_views_controller_show", "pkg_views_listview_render"): "AMBIGUOUS",
    ("pkg_views_page", "pkg_helpers_render"): "AMBIGUOUS",
    ("pkg_views_page", "pkg_views_detailview_render"): "AMBIGUOUS",
    ("pkg_views_page", "pkg_views_listview_render"): "AMBIGUOUS",
    ("pkg_views_page2", "pkg_helpers_slugify"): "AMBIGUOUS",
    ("pkg_views_page2", "pkg_kinds_slugify"): "AMBIGUOUS",
    ("pkg_views_page2", "pkg_views_detailview_slugify"): "AMBIGUOUS",
    ("pkg_views_page2", "pkg_views_listview_slugify"): "AMBIGUOUS",
}
UNFIT = {"examples_app", "pkg_kinds_slugify"}          # the :Module and the :Class candidates
EXPECTED = {k: v for k, v in PRE_11B.items() if k[1] not in UNFIT}
TRUE_FN = {("checks_gates_gate_d", "lib_recognizers_run"): "LR.run(root) (import ... as LR)",
           ("pkg_views_page", "pkg_helpers_render"): "helpers.render(value)",
           ("pkg_views_controller_show", "pkg_helpers_render"): "helpers.render(value) in a method",
           ("pkg_views_page2", "pkg_helpers_slugify"): "h2.slugify(value) (from . import helpers as h2)",
           ("pkg_models_order_close", "pkg_helpers_close"): "storage.close(self) (import ... as storage)"}
# CALLS_PLUS adds each import-alias call as a KEYED edge (R10/P5): the key wins for that def (INFERRED, cross-file)
PLUS_KEYED = {("checks_gates_gate_d", "lib_recognizers_run"), ("pkg_views_page", "pkg_helpers_render"),
              ("pkg_views_controller_show", "pkg_helpers_render"), ("pkg_views_page2", "pkg_helpers_slugify"),
              ("pkg_models_order_close", "pkg_helpers_close")}
GATE_D = "checks_gates_gate_d"

# ---- ENGINE LINES (bisected on the REAL engines: every release 0.8.39..0.8.51 on Windows, PYTHONHASHSEED 0-3 on ------
# 0.8.39/0.8.46/0.8.47/0.8.51; 0.8.39 and 0.8.51 also on Linux py3.10/3.11) ------------------------------------------
# bound  : graphify 0.8.39-0.8.46 bind gate_d's `LR.run(root)` to the same-file method _Sess.run (the gate_d link in
#          GRAPH_JSON), so the G5 member fall-back fans it out: gate_d is the 6th member caller, `run` -> 3 targets.
# nolink : graphify 0.8.47+ (0.8.47 / 0.8.49 / 0.8.50 / 0.8.51 measured; 0.8.48 is the yanked wheel that cannot import
#          `graphify.extractors`, so it builds no graph at all) emits NO call link for `LR.run(root)`: the raw graph.json
#          is GRAPH_JSON minus exactly that one link, nodes identical. gate_d is then no member caller and has NO
#          default-mode edge; CALLS_PLUS's keyed import-alias edge recovers `LR.run` -> lib_recognizers_run (INFERRED).
# Everything else — the other 5 member callers, their edges and grades, no :Module/:Class target, no promotion — is
# identical on both lines. requirements.txt (>=0.8.39,<0.8.52) resolves to 0.8.51 on a fresh install / CI.
LINE_DESC = {"bound": "0.8.39-0.8.46 — `LR.run(root)` bound to the same-file _Sess.run: 6 member callers",
             "nolink": "0.8.47+ — no call link for `LR.run(root)`: 5 member callers, gate_d -> lib_recognizers_run "
                       "only via CALLS_PLUS (INFERRED)"}
_MEASURED = {(0, 8, v): "bound" for v in range(39, 47)}
_MEASURED.update({(0, 8, v): "nolink" for v in (47, 49, 50, 51)})
_NOLINK_DROPS = (GATE_D, "checks_gates_sess_run")      # the one `calls` link graphify 0.8.47+ no longer emits


def _vstr(t: tuple) -> str:
    return ".".join(map(str, t))


def _engine_line(ver) -> tuple:
    """(line, how) for an installed graphify version string. A measured release maps to its own line; an unmeasured
    one (a future 0.8.52+/0.9.x, or a pre-0.8.39) to the NEAREST measured release's line; an unreadable version to
    the newest measured release (what requirements.txt resolves to). Never silent: `how` names the choice, and the
    real push must then match that line EXACTLY, so an engine that behaves otherwise goes RED."""
    t = GX._version_tuple(ver) if ver else None
    newest = max(_MEASURED)
    if t is None:
        return _MEASURED[newest], (f"version unreadable ({ver!r}) — assuming the newest measured release "
                                   f"{_vstr(newest)}, the one requirements.txt resolves to")
    if t in _MEASURED:
        return _MEASURED[t], f"measured release {_vstr(t)}"

    def flat(v):
        return v[0] * 1_000_000 + v[1] * 1_000 + v[2]
    near = min(_MEASURED, key=lambda m: (abs(flat(m) - flat(t)), -flat(m)))
    return _MEASURED[near], (f"{ver} is NOT a measured release — expecting the NEAREST measured release "
                             f"{_vstr(near)}'s behaviour; if it differs this section goes RED (re-measure the engine "
                             "and extend _MEASURED)")


def _graph_nolink() -> dict:
    g = _graph()
    g["links"] = [lk for lk in g["links"]
                  if not (lk.get("relation") == "calls" and (lk.get("source"), lk.get("target")) == _NOLINK_DROPS)]
    return g


def _members(line: str) -> dict:
    return dict(MEMBER) if line == "bound" else {k: v for k, v in MEMBER.items() if k != GATE_D}


def _true_fn(line: str, mode: str) -> dict:
    """The TRUE module-level :Function edges the engine line lets the push see in this mode."""
    if line == "bound" or mode in ("plus", "both"):
        return dict(TRUE_FN)
    return {k: v for k, v in TRUE_FN.items() if k[0] != GATE_D}


def _expected_push(line: str, mode: str) -> dict:
    """The exact push (edge -> grade) over the probe tree for an engine line and a flag mode."""
    plus = mode in ("plus", "both")
    want = {k: ("INFERRED" if plus and k in PLUS_KEYED else v) for k, v in EXPECTED.items()}
    if line == "nolink":
        want = {k: v for k, v in want.items() if k[0] != GATE_D}
        if plus:
            want[(GATE_D, "lib_recognizers_run")] = "INFERRED"
    return want


# the pre-fix push with DEEPMAP_SELF_CALL_REBIND=0 (frozen from the 11b build): the fix must not move it by a byte
OFF_ROWS = {
    "off": [["checks_gates_gate_d", "checks_gates_sess_run", "INFERRED"],
            ["pkg_models_order_close", "pkg_models_invoice_close", "INFERRED"],
            ["pkg_proxy_proxy_call", "pkg_proxy_router_app", "INFERRED"],
            ["pkg_views_page", "pkg_views_detailview_render", "INFERRED"],
            ["pkg_views_page2", "pkg_views_detailview_slugify", "INFERRED"],
            ["pkg_views_controller_show", "pkg_views_detailview_render", "INFERRED"]],
    "precise": [["checks_gates_gate_d", "checks_gates_sess_run", "EXTRACTED"],
                ["pkg_models_order_close", "pkg_models_invoice_close", "EXTRACTED"],
                ["pkg_proxy_proxy_call", "pkg_proxy_router_app", "EXTRACTED"],
                ["pkg_views_page", "pkg_views_detailview_render", "EXTRACTED"],
                ["pkg_views_page2", "pkg_views_detailview_slugify", "EXTRACTED"],
                ["pkg_views_controller_show", "pkg_views_detailview_render", "EXTRACTED"]],
    "plus": [["checks_gates_gate_d", "checks_gates_sess_run", "INFERRED"],
             ["pkg_models_order_close", "pkg_models_invoice_close", "INFERRED"],
             ["pkg_proxy_proxy_call", "pkg_proxy_router_app", "INFERRED"],
             ["pkg_views_page", "pkg_views_detailview_render", "INFERRED"],
             ["pkg_views_page2", "pkg_views_detailview_slugify", "INFERRED"],
             ["pkg_views_controller_show", "pkg_views_detailview_render", "INFERRED"],
             ["checks_gates_gate_d", "lib_recognizers_run", "INFERRED"],
             ["pkg_models_order_close", "pkg_helpers_close", "INFERRED"],
             ["pkg_views_page", "pkg_helpers_render", "INFERRED"],
             ["pkg_views_page2", "pkg_helpers_slugify", "INFERRED"],
             ["pkg_views_controller_show", "pkg_helpers_render", "INFERRED"]],
    "both": [["checks_gates_gate_d", "checks_gates_sess_run", "EXTRACTED"],
             ["pkg_models_order_close", "pkg_models_invoice_close", "EXTRACTED"],
             ["pkg_proxy_proxy_call", "pkg_proxy_router_app", "EXTRACTED"],
             ["pkg_views_page", "pkg_views_detailview_render", "EXTRACTED"],
             ["pkg_views_page2", "pkg_views_detailview_slugify", "EXTRACTED"],
             ["pkg_views_controller_show", "pkg_views_detailview_render", "EXTRACTED"],
             ["checks_gates_gate_d", "lib_recognizers_run", "INFERRED"],
             ["pkg_models_order_close", "pkg_helpers_close", "INFERRED"],
             ["pkg_views_page", "pkg_helpers_render", "INFERRED"],
             ["pkg_views_page2", "pkg_helpers_slugify", "INFERRED"],
             ["pkg_views_controller_show", "pkg_helpers_render", "INFERRED"]],
}


# ---- A ---------------------------------------------------------------------------------------------------------
def gate_a_map(root: Path) -> None:
    print("\n-- A: map_graph — the G5 member callers (unchanged by C1) --")
    for mode in MODES:
        with _env(**_mode_env(mode)), contextlib.redirect_stderr(io.StringIO()):
            nodes, calls, _i = GX.map_graph(_graph(), root=root)
        got = {n["key"]: n.get("call_member") for n in nodes if "call_member" in n}
        check(f"{mode}: call_member is recorded on exactly the 6 G5 member callers", got == MEMBER, got)
        fb = [(a, b) for a, b in calls if a in MEMBER and not GX.is_call_key(b)]
        check(f"{mode}: their fall-backs are plain NAME tuples (never a CallKey — R11/R1)",
              sorted(fb) == sorted((k, v[0]) for k, v in MEMBER.items()), fb)


# ---- B ---------------------------------------------------------------------------------------------------------
def gate_b_push(root: Path) -> None:
    print("\n-- B: the push — :Function kept, :Module / :Class excluded, no promotion --")
    for mode in MODES:
        nodes, _calls, edges = _run(mode, root)
        lab = _labels(nodes)
        e = dict(edges)
        for pair, why in TRUE_FN.items():
            check(f"{mode}: the TRUE module-level :Function edge is kept — {why}", pair in e, _of(e, pair[0]))
        bad = [(a, b, lab.get(b)) for (a, b) in e if a in MEMBER and lab.get(b) in ("Module", "Class")]
        check(f"{mode}: no G5 member edge lands on a :Module or a :Class", not bad, bad)
        check(f"{mode}: no false promotion — Proxy.__call__'s `self.app(scope)` -> Router.app stays AMBIGUOUS "
              "(fastapi-shaped: only the examples/app.py :Module was excluded)",
              e.get(("pkg_proxy_proxy_call", "pkg_proxy_router_app")) == "AMBIGUOUS",
              _of(e, "pkg_proxy_proxy_call"))
        check(f"{mode}: no false promotion — Order.close's `storage.close(self)` -> Invoice.close stays AMBIGUOUS",
              e.get(("pkg_models_order_close", "pkg_models_invoice_close")) == "AMBIGUOUS",
              _of(e, "pkg_models_order_close"))
        check(f"{mode}: LR.run's caller keeps its whole fan-out (the 2 sibling `run` methods too)",
              set(_of(e, "checks_gates_gate_d")) == {"checks_gates_sess_run", "checks_gates_tx_run",
                                                     "lib_recognizers_run"}, _of(e, "checks_gates_gate_d"))
        table = _expected_push("bound", mode)   # the exact push section D holds a 0.8.39-0.8.46 engine to
        if mode in ("off", "precise"):
            check(f"{mode}: the push equals the pre-11b fan-out minus its :Module / :Class targets, grades identical",
                  e == EXPECTED and table == EXPECTED, sorted(set(e.items()) ^ set(EXPECTED.items())))
        else:
            want = {k: ("INFERRED" if k in PLUS_KEYED else v) for k, v in EXPECTED.items()}
            check(f"{mode}: the same edges; CALLS_PLUS's keyed import-alias calls keep their key grade (INFERRED)",
                  e == want and table == want, sorted(set(e.items()) ^ set(want.items())))


# ---- C ---------------------------------------------------------------------------------------------------------
def _nd(key, name, sf, label="Method", line=1, **extra):
    return dict(key=key, label=label, name=name, source_file=sf, start_line=line, documented=False,
                body_summary=name, **extra)


def _caller(member=True):
    return _nd("c_go", "C.go", "pkg/c.py", "Method", 6, **({"call_member": ["run"]} if member else {}))


def gate_c_units() -> None:
    print("\n-- C: _resolve_calls — the exclusion and the grade --")
    world = [_nd("m1", "X.run", "pkg/x.py"), _nd("f1", "run", "lib/rec.py", "Function"),
             _nd("mod", "run", "tools/run.py", "Module"), _nd("cls", "run", "pkg/k.py", "Class")]
    with _env(**_mode_env("off")):
        e1 = S._resolve_calls([_caller()] + world, [("c_go", "run")])
        e0 = S._resolve_calls([_caller(False)] + world, [("c_go", "run")])
    check("member: the :Method and the :Function are kept, the :Module and the :Class are not — graded on all 4 "
          "candidates (AMBIGUOUS)", e1 == {("c_go", "m1"): "AMBIGUOUS", ("c_go", "f1"): "AMBIGUOUS"}, e1)
    check("a caller WITHOUT call_member fans out over every label exactly as before",
          e0 == {("c_go", "m1"): "AMBIGUOUS", ("c_go", "f1"): "AMBIGUOUS", ("c_go", "mod"): "AMBIGUOUS",
                 ("c_go", "cls"): "AMBIGUOUS"}, e0)
    with _env(**_mode_env("off")):
        e2 = S._resolve_calls([_caller(), world[0], world[2]], [("c_go", "run")])
        e3 = S._resolve_calls([_caller(), world[0]], [("c_go", "run")])
        e4 = S._resolve_calls([_caller(), world[1], world[3]], [("c_go", "run")])
        e5 = S._resolve_calls([_caller(), world[2], world[3]], [("c_go", "run")])
        e6 = S._resolve_calls([_caller(), world[1]], [("c_go", "run")])
    check("fastapi-shaped: ONE :Method beside an excluded :Module stays AMBIGUOUS (the exclusion never promotes)",
          e2 == {("c_go", "m1"): "AMBIGUOUS"}, e2)
    check("... while ONE :Method with nothing excluded keeps the count grade (INFERRED, as always)",
          e3 == {("c_go", "m1"): "INFERRED"}, e3)
    check("ONE module-level :Function beside an excluded :Class is kept, AMBIGUOUS",
          e4 == {("c_go", "f1"): "AMBIGUOUS"}, e4)
    check("only :Module / :Class candidates -> no edge at all", e5 == {}, e5)
    check("ONE :Function alone (`helpers.render(v)` with no sibling in the project) -> INFERRED",
          e6 == {("c_go", "f1"): "INFERRED"}, e6)
    # a filter that runs before grading still counts first: F8b (opt-in) drops a test-dir target before the grade
    tw = [_caller(), _nd("m1", "X.run", "pkg/x.py"), _nd("tmod", "run", "tests/run.py", "Module")]
    with _env(**_mode_env("off", DEEPMAP_DROP_APP_TO_TEST_CALLS="1")):
        e7 = S._resolve_calls(tw, [("c_go", "run")])
    check("F8b on: a test-dir :Module candidate is dropped BEFORE the grade (as before) -> the :Method is INFERRED",
          e7 == {("c_go", "m1"): "INFERRED"}, e7)
    with _env(**_mode_env("off")):
        e8 = S._resolve_calls(tw, [("c_go", "run")])
    check("F8b off: that :Module is excluded as a :Module and still counts for the grade (AMBIGUOUS)",
          e8 == {("c_go", "m1"): "AMBIGUOUS"}, e8)
    with _env(**_mode_env("off")):
        e9 = S._resolve_calls([_caller()] + world, [("c_go", "X.run")])
    check("a QUALIFIED name from the same caller is untouched by call_member", e9 == {("c_go", "m1"): "INFERRED"}, e9)


# ---- D ---------------------------------------------------------------------------------------------------------
def _gate_d_why(line: str, mode: str) -> str:
    if line == "bound":
        return "the 3-way `run` fan-out (LR.run bound to _Sess.run, G5 name fall-back)"
    if mode in ("plus", "both"):
        return "ONLY lib/recognizers.py::run, INFERRED — CALLS_PLUS's keyed import-alias edge"
    return "NONE — graphify 0.8.47+ emits no call link for `LR.run(root)` and nothing else invents one"


def gate_d_real_engine() -> None:
    print("\n-- D: the REAL engine on the probe tree (engine-version-aware) --")
    try:
        import graphify  # noqa: F401
    except Exception:
        print("  [SKIP] graphify is not installed in this interpreter — the real-engine section is not run")
        return
    ver = GX.graphify_version()
    line, how = _engine_line(ver)
    print(f"  [INFO] installed graphify {ver or '(unknown)'}: {how}")
    print(f"  [INFO] -> expecting engine line '{line}': {LINE_DESC[line]}")
    root = _write_tree(_TMP / "real", TREE)
    for mode in ("off", "precise", "plus"):
        with _env(**_mode_env(mode, DEEPMAP_HOME=str(_TMP / "home"))), contextlib.redirect_stderr(io.StringIO()), \
                contextlib.redirect_stdout(io.StringIO()):
            res, rep = GX.extract_with_report(root)
            if not res:
                check(f"real engine ({mode}) produced a graph", False, rep.get("cause"))
                return
            nodes, calls, _i = res
            edges = _passes(root, nodes, calls)
        lab = _labels(nodes)
        members = {n["key"] for n in nodes if n.get("call_member")}
        e = dict(edges)
        want_members, need = _members(line), _true_fn(line, mode)
        check(f"real ({mode}): the {len(want_members)} G5 member callers of the frozen graph for engine line '{line}'",
              members == set(want_members), sorted(members))
        check(f"real ({mode}): every TRUE module-level :Function edge the '{line}' line exposes is kept "
              f"({len(need)} of {len(TRUE_FN)})", all(p in e for p in need), [p for p in need if p not in e])
        check(f"real ({mode}): no member edge lands on a :Module / :Class",
              not [(a, b) for (a, b) in e if a in members and lab.get(b) in ("Module", "Class")],
              [(a, b, lab.get(b)) for (a, b) in e if a in members and lab.get(b) in ("Module", "Class")])
        check(f"real ({mode}): Proxy.__call__ -> Router.app and Order.close -> Invoice.close stay AMBIGUOUS",
              e.get(("pkg_proxy_proxy_call", "pkg_proxy_router_app")) == "AMBIGUOUS"
              and e.get(("pkg_models_order_close", "pkg_models_invoice_close")) == "AMBIGUOUS",
              (_of(e, "pkg_proxy_proxy_call"), _of(e, "pkg_models_order_close")))
        want_d = _of(_expected_push(line, mode), GATE_D)
        check(f"real ({mode}): gate_d's `LR.run(root)` out-edges are exactly the '{line}' line's — "
              f"{_gate_d_why(line, mode)}", _of(e, GATE_D) == want_d, (_of(e, GATE_D), want_d))
        want = _expected_push(line, mode)
        check(f"real ({mode}): the whole push equals the '{line}' line's frozen-graph push, grades identical",
              e == want, sorted(set(e.items()) ^ set(want.items())))


# ---- E ---------------------------------------------------------------------------------------------------------
def gate_e_nolink_shape(root: Path) -> None:
    print("\n-- E: the graphify 0.8.47+ graph shape (hermetic) — no call link for `LR.run(root)` --")
    g0, g1 = _graph()["links"], _graph_nolink()["links"]
    gone = [lk for lk in g0 if lk not in g1]
    check("the 0.8.47+ graph is the 0.8.39 one minus exactly gate_d's `LR.run` call link (nodes identical)",
          len(g1) == len(g0) - 1 and _graph_nolink()["nodes"] == _graph()["nodes"]
          and [(lk["source"], lk["target"], lk["relation"]) for lk in gone] == [_NOLINK_DROPS + ("calls",)], gone)
    for mode in MODES:
        nodes, _c, edges = _run(mode, root, graph=_graph_nolink())
        lab = _labels(nodes)
        e = dict(edges)
        got = {n["key"]: n.get("call_member") for n in nodes if "call_member" in n}
        check(f"{mode}: call_member on exactly the 5 other member callers (gate_d has no call to fall back from)",
              got == _members("nolink"), got)
        need = _true_fn("nolink", mode)
        check(f"{mode}: every TRUE module-level :Function edge this graph exposes is kept ({len(need)} of "
              f"{len(TRUE_FN)})", all(p in e for p in need), [p for p in need if p not in e])
        bad = [(a, b, lab.get(b)) for (a, b) in e if a in got and lab.get(b) in ("Module", "Class")]
        check(f"{mode}: no G5 member edge lands on a :Module or a :Class", not bad, bad)
        check(f"{mode}: no false promotion — Proxy.__call__ -> Router.app and Order.close -> Invoice.close stay "
              "AMBIGUOUS", e.get(("pkg_proxy_proxy_call", "pkg_proxy_router_app")) == "AMBIGUOUS"
              and e.get(("pkg_models_order_close", "pkg_models_invoice_close")) == "AMBIGUOUS",
              (_of(e, "pkg_proxy_proxy_call"), _of(e, "pkg_models_order_close")))
        want_d = {"lib_recognizers_run": "INFERRED"} if mode in ("plus", "both") else {}
        check(f"{mode}: gate_d's out-edges — {_gate_d_why('nolink', mode)}",
              _of(e, GATE_D) == want_d and _of(_expected_push("nolink", mode), GATE_D) == want_d, _of(e, GATE_D))
        want = _expected_push("nolink", mode)
        check(f"{mode}: the push equals the 0.8.39 push minus gate_d's rows (+ its CALLS_PLUS edge), grades identical",
              e == want, sorted(set(e.items()) ^ set(want.items())))


# ---- Z ---------------------------------------------------------------------------------------------------------
def gate_z_rebind_off(root: Path) -> None:
    print("\n-- Z: DEEPMAP_SELF_CALL_REBIND=0 — byte-identical to the pre-fix build --")
    for mode in MODES:
        nodes, _c, edges = _run(mode, root, DEEPMAP_SELF_CALL_REBIND="0")
        check(f"{mode}, REBIND=0: no node carries call_member", not any("call_member" in n for n in nodes))
        rows = [[a, b, c] for (a, b), c in edges.items()]
        check(f"{mode}, REBIND=0: the pushed CALLS, in order, equal the pre-fix build's", rows == OFF_ROWS[mode], rows)


def main() -> int:
    print("g5_member_targets_r11c_gates — a G5 member fall-back keeps :Method + :Function, never promotes")
    try:
        root = _write_tree(_TMP / "fixture", TREE)
        for gate, args in ((gate_a_map, (root,)), (gate_b_push, (root,)), (gate_c_units, ()),
                           (gate_d_real_engine, ()), (gate_e_nolink_shape, (root,)), (gate_z_rebind_off, (root,))):
            try:
                gate(*args)
            except Exception as e:  # noqa: BLE001
                import traceback
                traceback.print_exc()
                check(f"{gate.__name__} ran without raising", False, f"{type(e).__name__}: {e}")
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
    total = _PASS + _FAIL
    if _FAIL:
        print(f"\nRESULT: RED — {_FAIL} of {total} checks failed.")
        return 1
    print(f"\nRESULT: GREEN — {total}/{total} checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
