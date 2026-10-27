#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Round-11b engine gate (WE, G1; revised by R11c/C1): the G5 self-call rebinder's NAME fall-back never reaches a
:Module or a :Class, and never fans a dunder out to every `__init__` in the repo.

The defect (found on flask at 36e4a824, where G5 alone explained a search-quality drop): G5
(DEEPMAP_SELF_CALL_REBIND, default ON) turns an in-file member call it cannot pin to one class into the bare
NAME, and sync._resolve_calls fans that name out over EVERY node label:
  * `self.serializer.tag(value)` (flask json/tag.py) also landed on the tag.py :Module — a file is not callable;
  * `BaseEnvironment.__init__(self, **options)` (flask templating.py, jinja2's Environment) fanned out to all 41
    `__init__`s — 28 methods and 13 package `__init__.py` :Modules — although G5 already DROPS the same dunder
    for self/super (its own docstring: "a name fan-out would hang it on every `__init__` in the repo").
The extra in-degree pushed JSONProvider.response out of flask's top-15 search results.
The fix:
  F1 a dunder reached through any other receiver is dropped — or, when the receiver is an IN-FILE class
     (`SessionMixin.__init__(self, ...)`), bound through that class's in-file MRO exactly as self/super are;
  F2 the fall-back pairs are recorded on the caller (`call_member`, private push metadata like call_bare /
     call_alias — never pushed) and _resolve_calls keeps them off :Module and :Class nodes. R11c/C1: 11b kept
     only :Method targets, which lost TRUE edges into module-level :Functions called through an imported module
     (`LR.run(root)`, `helpers.render(v)`) and, grading by the count left, promoted the false sibling that
     remained; now :Method AND :Function targets stay, graded on the count BEFORE the exclusion (the name
     fan-out's own grade — the exclusion never promotes; tests/g5_member_targets_r11c_gates.py). A name the
     caller ALSO calls another way (a bare call, a qualified call) is not restricted. The fall-back tuples stay
     plain NAME tuples (never a CallKey — R11/R1). No new flag: it follows DEEPMAP_SELF_CALL_REBIND; with that
     off, map_graph and the push are byte-identical to before.

Sections (hermetic unless noted — temp dirs, no Neo4j / network):
  A  map_graph on a frozen graphify 0.8.39 graph of a flask-shaped tree: F1 drops / binds, F2's call_member,
     the fall-back tuples are plain names in every flag mode
  B  the push (sync's passes in _sync_pass order + _resolve_calls): exact edges; Environment.__init__ has no
     CALLS into any __init__ or :Module; the tag.py :Module and every __init__ :Module have in-degree 0; a G5
     fall-back never targets :Module / :Class; the polymorphic fan-out stays (AMBIGUOUS)
  C  a name the caller also calls BARE keeps its whole fan-out (no call_member for it)
  D  the consumers: _resolve_calls' member exclusion + its grade (the count before the exclusion), the local-def
     rule never grades a member name, _attach_local_defs skips it, a member fan-out of only :Module / :Class
     candidates yields no edge, a module-level :Function reachable through an import alias IS kept
  E  the REAL engine on the tree (skipped when graphify is absent)
  Z  DEEPMAP_SELF_CALL_REBIND=0: map_graph output and the push byte-identical to the pre-fix build (frozen)
"""
from __future__ import annotations

import contextlib
import hashlib
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
_TMP = Path(tempfile.mkdtemp(prefix="dm_r11b_g5_")).resolve()   # created by THIS gate, removed by exact path
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


S = _load("sync", "dm_r11b_g5_sync")


# ---- the flask-shaped fixture (its graphify 0.8.39 graph is frozen below) --------------------------------------
TREE = {
    "src/flask/__init__.py": 'from .app import Flask\n\n__version__ = "3.0"\n',
    "src/flask/json/__init__.py": "from .provider import JSONProvider\n",
    "src/flask/app.py": (
        "class Flask:\n"
        "    def __init__(self, import_name):\n"
        "        self.import_name = import_name\n"
        "\n"
        "    def create_global_jinja_loader(self):\n"
        "        return None\n"
        "\n"
        "    def wsgi_app(self, environ):\n"
        "        return environ\n"
        "\n"
        "\n"
        "class FlaskClient:\n"
        "    def __init__(self, application):\n"
        "        self.application = application\n"
        "\n"
        "    def open(self, environ):\n"
        "        return self.wsgi_app(environ)\n"
    ),
    "src/flask/helpers.py": (
        "def wsgi_app(environ):\n"
        "    return environ\n"
    ),
    "src/flask/templating.py": (
        "from jinja2 import Environment as BaseEnvironment\n"
        "\n"
        "\n"
        "class Environment(BaseEnvironment):\n"
        "    def __init__(self, app, **options):\n"
        '        if "loader" not in options:\n'
        '            options["loader"] = app.create_global_jinja_loader()\n'
        "        BaseEnvironment.__init__(self, **options)\n"
        "        self.app = app\n"
        "\n"
        "\n"
        "class DispatchingJinjaLoader:\n"
        "    def __init__(self, app):\n"
        "        self.app = app\n"
    ),
    "src/flask/json/tag.py": (
        "class JSONTag:\n"
        '    key = ""\n'
        "\n"
        "    def __init__(self, serializer):\n"
        "        self.serializer = serializer\n"
        "\n"
        "    def check(self, value):\n"
        "        raise NotImplementedError\n"
        "\n"
        "    def to_json(self, value):\n"
        "        raise NotImplementedError\n"
        "\n"
        "    def tag(self, value):\n"
        "        return {self.key: self.to_json(value)}\n"
        "\n"
        "\n"
        "class TagDict(JSONTag):\n"
        '    key = " di"\n'
        "\n"
        "    def check(self, value):\n"
        "        return isinstance(value, dict) and len(value) == 1\n"
        "\n"
        "    def to_json(self, value):\n"
        "        key = next(iter(value))\n"
        '        return {f"{key}__": self.serializer.tag(value[key])}\n'
        "\n"
        "\n"
        "class PassList(JSONTag):\n"
        "    def check(self, value):\n"
        "        return isinstance(value, list)\n"
        "\n"
        "    def to_json(self, value):\n"
        "        return [self.serializer.tag(item) for item in value]\n"
        "\n"
        "\n"
        "class TaggedJSONSerializer:\n"
        "    def __init__(self):\n"
        "        self.order = []\n"
        "\n"
        "    def tag(self, value):\n"
        "        for tag in self.order:\n"
        "            if tag.check(value):\n"
        "                return tag.tag(value)\n"
        "        return value\n"
    ),
    "src/flask/json/provider.py": (
        "class JSONProvider:\n"
        "    def __init__(self, app):\n"
        "        self._app = app\n"
        "\n"
        "    def dumps(self, obj, **kwargs):\n"
        "        raise NotImplementedError\n"
        "\n"
        "    def response(self, *args, **kwargs):\n"
        "        obj = self._prepare_response_obj(args, kwargs)\n"
        "        return self.dumps(obj)\n"
        "\n"
        "    def _prepare_response_obj(self, args, kwargs):\n"
        "        return args or kwargs\n"
    ),
    "src/flask/sessions.py": (
        "class SessionMixin(dict):\n"
        "    def __init__(self, initial=None):\n"
        "        dict.__init__(self, initial or {})\n"
        "\n"
        "\n"
        "class SecureCookieSession(SessionMixin):\n"
        "    def __init__(self, initial=None):\n"
        "        SessionMixin.__init__(self, initial)\n"
        "        self.modified = False\n"
        "\n"
        "\n"
        "class NullSession(SecureCookieSession):\n"
        "    def __init__(self):\n"
        "        SecureCookieSession.__init__(self)\n"
    ),
    "tests/test_apps/blueprintapp/__init__.py": "from flask import Flask\n\napp = Flask(__name__)\n",
}


def _write_tree(root: Path, tree: dict) -> Path:
    for rel, text in tree.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode("utf-8"))
    return root


# graphify 0.8.39's graph.json for TREE (captured from the real engine; nodes' _origin and links' weight dropped)
GRAPH_JSON = r"""
{"nodes": [
{"file_type": "code", "id": "flask_init", "label": "__init__.py", "source_file": "src/flask/__init__.py", "source_location": "L1"},
{"file_type": "code", "id": "flask_app", "label": "app.py", "source_file": "src/flask/app.py", "source_location": "L1"},
{"file_type": "code", "id": "flask_app_flask", "label": "Flask", "source_file": "src/flask/app.py", "source_location": "L1"},
{"file_type": "code", "id": "flask_app_flask_init", "label": ".__init__()", "source_file": "src/flask/app.py", "source_location": "L2"},
{"file_type": "code", "id": "flask_app_flask_create_global_jinja_loader", "label": ".create_global_jinja_loader()", "source_file": "src/flask/app.py", "source_location": "L5"},
{"file_type": "code", "id": "flask_app_flask_wsgi_app", "label": ".wsgi_app()", "source_file": "src/flask/app.py", "source_location": "L8"},
{"file_type": "code", "id": "flask_app_flaskclient", "label": "FlaskClient", "source_file": "src/flask/app.py", "source_location": "L12"},
{"file_type": "code", "id": "flask_app_flaskclient_init", "label": ".__init__()", "source_file": "src/flask/app.py", "source_location": "L13"},
{"file_type": "code", "id": "flask_app_flaskclient_open", "label": ".open()", "source_file": "src/flask/app.py", "source_location": "L16"},
{"file_type": "code", "id": "flask_helpers", "label": "helpers.py", "source_file": "src/flask/helpers.py", "source_location": "L1"},
{"file_type": "code", "id": "flask_helpers_wsgi_app", "label": "wsgi_app()", "source_file": "src/flask/helpers.py", "source_location": "L1"},
{"file_type": "code", "id": "json_init", "label": "__init__.py", "source_file": "src/flask/json/__init__.py", "source_location": "L1"},
{"file_type": "code", "id": "json_provider", "label": "provider.py", "source_file": "src/flask/json/provider.py", "source_location": "L1"},
{"file_type": "code", "id": "json_provider_jsonprovider", "label": "JSONProvider", "source_file": "src/flask/json/provider.py", "source_location": "L1"},
{"file_type": "code", "id": "json_provider_jsonprovider_init", "label": ".__init__()", "source_file": "src/flask/json/provider.py", "source_location": "L2"},
{"file_type": "code", "id": "json_provider_jsonprovider_dumps", "label": ".dumps()", "source_file": "src/flask/json/provider.py", "source_location": "L5"},
{"file_type": "code", "id": "json_provider_jsonprovider_response", "label": ".response()", "source_file": "src/flask/json/provider.py", "source_location": "L8"},
{"file_type": "code", "id": "json_provider_jsonprovider_prepare_response_obj", "label": "._prepare_response_obj()", "source_file": "src/flask/json/provider.py", "source_location": "L12"},
{"file_type": "code", "id": "json_tag", "label": "tag.py", "source_file": "src/flask/json/tag.py", "source_location": "L1"},
{"file_type": "code", "id": "json_tag_jsontag", "label": "JSONTag", "source_file": "src/flask/json/tag.py", "source_location": "L1"},
{"file_type": "code", "id": "json_tag_jsontag_init", "label": ".__init__()", "source_file": "src/flask/json/tag.py", "source_location": "L4"},
{"file_type": "code", "id": "json_tag_jsontag_check", "label": ".check()", "source_file": "src/flask/json/tag.py", "source_location": "L7"},
{"file_type": "code", "id": "json_tag_jsontag_to_json", "label": ".to_json()", "source_file": "src/flask/json/tag.py", "source_location": "L10"},
{"file_type": "code", "id": "json_tag_jsontag_tag", "label": ".tag()", "source_file": "src/flask/json/tag.py", "source_location": "L13"},
{"file_type": "code", "id": "json_tag_tagdict", "label": "TagDict", "source_file": "src/flask/json/tag.py", "source_location": "L17"},
{"file_type": "code", "id": "json_tag_tagdict_check", "label": ".check()", "source_file": "src/flask/json/tag.py", "source_location": "L20"},
{"file_type": "code", "id": "json_tag_tagdict_to_json", "label": ".to_json()", "source_file": "src/flask/json/tag.py", "source_location": "L23"},
{"file_type": "code", "id": "json_tag_passlist", "label": "PassList", "source_file": "src/flask/json/tag.py", "source_location": "L28"},
{"file_type": "code", "id": "json_tag_passlist_check", "label": ".check()", "source_file": "src/flask/json/tag.py", "source_location": "L29"},
{"file_type": "code", "id": "json_tag_passlist_to_json", "label": ".to_json()", "source_file": "src/flask/json/tag.py", "source_location": "L32"},
{"file_type": "code", "id": "json_tag_taggedjsonserializer", "label": "TaggedJSONSerializer", "source_file": "src/flask/json/tag.py", "source_location": "L36"},
{"file_type": "code", "id": "json_tag_taggedjsonserializer_init", "label": ".__init__()", "source_file": "src/flask/json/tag.py", "source_location": "L37"},
{"file_type": "code", "id": "json_tag_taggedjsonserializer_tag", "label": ".tag()", "source_file": "src/flask/json/tag.py", "source_location": "L40"},
{"file_type": "code", "id": "flask_sessions", "label": "sessions.py", "source_file": "src/flask/sessions.py", "source_location": "L1"},
{"file_type": "code", "id": "flask_sessions_sessionmixin", "label": "SessionMixin", "source_file": "src/flask/sessions.py", "source_location": "L1"},
{"file_type": "code", "id": "dict", "label": "dict", "source_file": "", "source_location": ""},
{"file_type": "code", "id": "flask_sessions_sessionmixin_init", "label": ".__init__()", "source_file": "src/flask/sessions.py", "source_location": "L2"},
{"file_type": "code", "id": "flask_sessions_securecookiesession", "label": "SecureCookieSession", "source_file": "src/flask/sessions.py", "source_location": "L6"},
{"file_type": "code", "id": "flask_sessions_securecookiesession_init", "label": ".__init__()", "source_file": "src/flask/sessions.py", "source_location": "L7"},
{"file_type": "code", "id": "flask_sessions_nullsession", "label": "NullSession", "source_file": "src/flask/sessions.py", "source_location": "L12"},
{"file_type": "code", "id": "flask_sessions_nullsession_init", "label": ".__init__()", "source_file": "src/flask/sessions.py", "source_location": "L13"},
{"file_type": "code", "id": "flask_templating", "label": "templating.py", "source_file": "src/flask/templating.py", "source_location": "L1"},
{"file_type": "code", "id": "flask_templating_environment", "label": "Environment", "source_file": "src/flask/templating.py", "source_location": "L4"},
{"file_type": "code", "id": "baseenvironment", "label": "BaseEnvironment", "source_file": "", "source_location": ""},
{"file_type": "code", "id": "flask_templating_environment_init", "label": ".__init__()", "source_file": "src/flask/templating.py", "source_location": "L5"},
{"file_type": "code", "id": "flask_templating_dispatchingjinjaloader", "label": "DispatchingJinjaLoader", "source_file": "src/flask/templating.py", "source_location": "L12"},
{"file_type": "code", "id": "flask_templating_dispatchingjinjaloader_init", "label": ".__init__()", "source_file": "src/flask/templating.py", "source_location": "L13"},
{"file_type": "code", "id": "blueprintapp_init", "label": "__init__.py", "source_file": "tests/test_apps/blueprintapp/__init__.py", "source_location": "L1"}
], "links": [
{"confidence": "EXTRACTED", "context": "import", "relation": "imports_from", "source": "flask_init", "source_file": "src/flask/__init__.py", "source_location": "L1", "target": "flask_app"},
{"confidence": "EXTRACTED", "relation": "contains", "source": "flask_app", "source_file": "src/flask/app.py", "source_location": "L1", "target": "flask_app_flask"},
{"confidence": "EXTRACTED", "relation": "method", "source": "flask_app_flask", "source_file": "src/flask/app.py", "source_location": "L2", "target": "flask_app_flask_init"},
{"confidence": "EXTRACTED", "relation": "method", "source": "flask_app_flask", "source_file": "src/flask/app.py", "source_location": "L5", "target": "flask_app_flask_create_global_jinja_loader"},
{"confidence": "EXTRACTED", "relation": "method", "source": "flask_app_flask", "source_file": "src/flask/app.py", "source_location": "L8", "target": "flask_app_flask_wsgi_app"},
{"confidence": "EXTRACTED", "relation": "contains", "source": "flask_app", "source_file": "src/flask/app.py", "source_location": "L12", "target": "flask_app_flaskclient"},
{"confidence": "EXTRACTED", "relation": "method", "source": "flask_app_flaskclient", "source_file": "src/flask/app.py", "source_location": "L13", "target": "flask_app_flaskclient_init"},
{"confidence": "EXTRACTED", "relation": "method", "source": "flask_app_flaskclient", "source_file": "src/flask/app.py", "source_location": "L16", "target": "flask_app_flaskclient_open"},
{"confidence": "EXTRACTED", "context": "call", "relation": "calls", "source": "flask_app_flaskclient_open", "source_file": "src/flask/app.py", "source_location": "L17", "target": "flask_app_flask_wsgi_app"},
{"confidence": "EXTRACTED", "relation": "contains", "source": "flask_helpers", "source_file": "src/flask/helpers.py", "source_location": "L1", "target": "flask_helpers_wsgi_app"},
{"confidence": "EXTRACTED", "context": "import", "relation": "imports_from", "source": "json_init", "source_file": "src/flask/json/__init__.py", "source_location": "L1", "target": "json_provider"},
{"confidence": "EXTRACTED", "relation": "contains", "source": "json_provider", "source_file": "src/flask/json/provider.py", "source_location": "L1", "target": "json_provider_jsonprovider"},
{"confidence": "EXTRACTED", "relation": "method", "source": "json_provider_jsonprovider", "source_file": "src/flask/json/provider.py", "source_location": "L2", "target": "json_provider_jsonprovider_init"},
{"confidence": "EXTRACTED", "relation": "method", "source": "json_provider_jsonprovider", "source_file": "src/flask/json/provider.py", "source_location": "L5", "target": "json_provider_jsonprovider_dumps"},
{"confidence": "EXTRACTED", "relation": "method", "source": "json_provider_jsonprovider", "source_file": "src/flask/json/provider.py", "source_location": "L8", "target": "json_provider_jsonprovider_response"},
{"confidence": "EXTRACTED", "relation": "method", "source": "json_provider_jsonprovider", "source_file": "src/flask/json/provider.py", "source_location": "L12", "target": "json_provider_jsonprovider_prepare_response_obj"},
{"confidence": "EXTRACTED", "context": "call", "relation": "calls", "source": "json_provider_jsonprovider_response", "source_file": "src/flask/json/provider.py", "source_location": "L9", "target": "json_provider_jsonprovider_prepare_response_obj"},
{"confidence": "EXTRACTED", "context": "call", "relation": "calls", "source": "json_provider_jsonprovider_response", "source_file": "src/flask/json/provider.py", "source_location": "L10", "target": "json_provider_jsonprovider_dumps"},
{"confidence": "EXTRACTED", "relation": "contains", "source": "json_tag", "source_file": "src/flask/json/tag.py", "source_location": "L1", "target": "json_tag_jsontag"},
{"confidence": "EXTRACTED", "relation": "method", "source": "json_tag_jsontag", "source_file": "src/flask/json/tag.py", "source_location": "L4", "target": "json_tag_jsontag_init"},
{"confidence": "EXTRACTED", "relation": "method", "source": "json_tag_jsontag", "source_file": "src/flask/json/tag.py", "source_location": "L7", "target": "json_tag_jsontag_check"},
{"confidence": "EXTRACTED", "relation": "method", "source": "json_tag_jsontag", "source_file": "src/flask/json/tag.py", "source_location": "L10", "target": "json_tag_jsontag_to_json"},
{"confidence": "EXTRACTED", "relation": "method", "source": "json_tag_jsontag", "source_file": "src/flask/json/tag.py", "source_location": "L13", "target": "json_tag_jsontag_tag"},
{"confidence": "EXTRACTED", "relation": "contains", "source": "json_tag", "source_file": "src/flask/json/tag.py", "source_location": "L17", "target": "json_tag_tagdict"},
{"confidence": "EXTRACTED", "relation": "inherits", "source": "json_tag_tagdict", "source_file": "src/flask/json/tag.py", "source_location": "L17", "target": "json_tag_jsontag"},
{"confidence": "EXTRACTED", "relation": "method", "source": "json_tag_tagdict", "source_file": "src/flask/json/tag.py", "source_location": "L20", "target": "json_tag_tagdict_check"},
{"confidence": "EXTRACTED", "relation": "method", "source": "json_tag_tagdict", "source_file": "src/flask/json/tag.py", "source_location": "L23", "target": "json_tag_tagdict_to_json"},
{"confidence": "EXTRACTED", "relation": "contains", "source": "json_tag", "source_file": "src/flask/json/tag.py", "source_location": "L28", "target": "json_tag_passlist"},
{"confidence": "EXTRACTED", "relation": "inherits", "source": "json_tag_passlist", "source_file": "src/flask/json/tag.py", "source_location": "L28", "target": "json_tag_jsontag"},
{"confidence": "EXTRACTED", "relation": "method", "source": "json_tag_passlist", "source_file": "src/flask/json/tag.py", "source_location": "L29", "target": "json_tag_passlist_check"},
{"confidence": "EXTRACTED", "relation": "method", "source": "json_tag_passlist", "source_file": "src/flask/json/tag.py", "source_location": "L32", "target": "json_tag_passlist_to_json"},
{"confidence": "EXTRACTED", "relation": "contains", "source": "json_tag", "source_file": "src/flask/json/tag.py", "source_location": "L36", "target": "json_tag_taggedjsonserializer"},
{"confidence": "EXTRACTED", "relation": "method", "source": "json_tag_taggedjsonserializer", "source_file": "src/flask/json/tag.py", "source_location": "L37", "target": "json_tag_taggedjsonserializer_init"},
{"confidence": "EXTRACTED", "relation": "method", "source": "json_tag_taggedjsonserializer", "source_file": "src/flask/json/tag.py", "source_location": "L40", "target": "json_tag_taggedjsonserializer_tag"},
{"confidence": "EXTRACTED", "context": "call", "relation": "calls", "source": "json_tag_jsontag_tag", "source_file": "src/flask/json/tag.py", "source_location": "L14", "target": "json_tag_passlist_to_json"},
{"confidence": "EXTRACTED", "context": "call", "relation": "calls", "source": "json_tag_tagdict_to_json", "source_file": "src/flask/json/tag.py", "source_location": "L25", "target": "json_tag_taggedjsonserializer_tag"},
{"confidence": "EXTRACTED", "context": "call", "relation": "calls", "source": "json_tag_passlist_to_json", "source_file": "src/flask/json/tag.py", "source_location": "L33", "target": "json_tag_taggedjsonserializer_tag"},
{"confidence": "EXTRACTED", "context": "call", "relation": "calls", "source": "json_tag_taggedjsonserializer_tag", "source_file": "src/flask/json/tag.py", "source_location": "L42", "target": "json_tag_passlist_check"},
{"confidence": "EXTRACTED", "relation": "contains", "source": "flask_sessions", "source_file": "src/flask/sessions.py", "source_location": "L1", "target": "flask_sessions_sessionmixin"},
{"confidence": "EXTRACTED", "relation": "inherits", "source": "flask_sessions_sessionmixin", "source_file": "src/flask/sessions.py", "source_location": "L1", "target": "dict"},
{"confidence": "EXTRACTED", "relation": "method", "source": "flask_sessions_sessionmixin", "source_file": "src/flask/sessions.py", "source_location": "L2", "target": "flask_sessions_sessionmixin_init"},
{"confidence": "EXTRACTED", "relation": "contains", "source": "flask_sessions", "source_file": "src/flask/sessions.py", "source_location": "L6", "target": "flask_sessions_securecookiesession"},
{"confidence": "EXTRACTED", "relation": "inherits", "source": "flask_sessions_securecookiesession", "source_file": "src/flask/sessions.py", "source_location": "L6", "target": "flask_sessions_sessionmixin"},
{"confidence": "EXTRACTED", "relation": "method", "source": "flask_sessions_securecookiesession", "source_file": "src/flask/sessions.py", "source_location": "L7", "target": "flask_sessions_securecookiesession_init"},
{"confidence": "EXTRACTED", "relation": "contains", "source": "flask_sessions", "source_file": "src/flask/sessions.py", "source_location": "L12", "target": "flask_sessions_nullsession"},
{"confidence": "EXTRACTED", "relation": "inherits", "source": "flask_sessions_nullsession", "source_file": "src/flask/sessions.py", "source_location": "L12", "target": "flask_sessions_securecookiesession"},
{"confidence": "EXTRACTED", "relation": "method", "source": "flask_sessions_nullsession", "source_file": "src/flask/sessions.py", "source_location": "L13", "target": "flask_sessions_nullsession_init"},
{"confidence": "EXTRACTED", "context": "call", "relation": "calls", "source": "flask_sessions_sessionmixin_init", "source_file": "src/flask/sessions.py", "source_location": "L3", "target": "flask_sessions_nullsession_init"},
{"confidence": "EXTRACTED", "context": "call", "relation": "calls", "source": "flask_sessions_securecookiesession_init", "source_file": "src/flask/sessions.py", "source_location": "L8", "target": "flask_sessions_nullsession_init"},
{"confidence": "EXTRACTED", "context": "import", "relation": "imports_from", "source": "flask_templating", "source_file": "src/flask/templating.py", "source_location": "L1", "target": "jinja2"},
{"confidence": "EXTRACTED", "relation": "contains", "source": "flask_templating", "source_file": "src/flask/templating.py", "source_location": "L4", "target": "flask_templating_environment"},
{"confidence": "EXTRACTED", "relation": "inherits", "source": "flask_templating_environment", "source_file": "src/flask/templating.py", "source_location": "L4", "target": "baseenvironment"},
{"confidence": "EXTRACTED", "relation": "method", "source": "flask_templating_environment", "source_file": "src/flask/templating.py", "source_location": "L5", "target": "flask_templating_environment_init"},
{"confidence": "EXTRACTED", "relation": "contains", "source": "flask_templating", "source_file": "src/flask/templating.py", "source_location": "L12", "target": "flask_templating_dispatchingjinjaloader"},
{"confidence": "EXTRACTED", "relation": "method", "source": "flask_templating_dispatchingjinjaloader", "source_file": "src/flask/templating.py", "source_location": "L13", "target": "flask_templating_dispatchingjinjaloader_init"},
{"confidence": "EXTRACTED", "context": "call", "relation": "calls", "source": "flask_templating_environment_init", "source_file": "src/flask/templating.py", "source_location": "L8", "target": "flask_templating_dispatchingjinjaloader_init"},
{"confidence": "EXTRACTED", "context": "import", "relation": "imports_from", "source": "blueprintapp_init", "source_file": "tests/test_apps/blueprintapp/__init__.py", "source_location": "L1", "target": "flask"},
{"confidence": "EXTRACTED", "context": "export", "relation": "re_exports", "source": "flask_init", "source_file": "src/flask/__init__.py", "source_location": "L1", "target": "flask_app"},
{"confidence": "EXTRACTED", "context": "export", "relation": "re_exports", "source": "json_init", "source_file": "src/flask/json/__init__.py", "source_location": "L1", "target": "json_provider"},
{"confidence": "EXTRACTED", "context": "import", "relation": "imports", "source": "flask_init", "source_file": "src/flask/__init__.py", "source_location": "L1", "target": "flask_app_flask"},
{"confidence": "EXTRACTED", "context": "import", "relation": "imports", "source": "json_init", "source_file": "src/flask/json/__init__.py", "source_location": "L1", "target": "json_provider_jsonprovider"}
]}
"""

# the pre-fix build's output with DEEPMAP_SELF_CALL_REBIND=0 (frozen): the fix must not move it by a byte
OFF_CALLS = [
    ('flask_app_flaskclient_open', 'Flask.wsgi_app'),
    ('json_provider_jsonprovider_response', 'JSONProvider._prepare_response_obj'),
    ('json_provider_jsonprovider_response', 'JSONProvider.dumps'),
    ('json_tag_jsontag_tag', 'PassList.to_json'),
    ('json_tag_tagdict_to_json', 'TaggedJSONSerializer.tag'),
    ('json_tag_passlist_to_json', 'TaggedJSONSerializer.tag'),
    ('json_tag_taggedjsonserializer_tag', 'PassList.check'),
    ('flask_sessions_sessionmixin_init', 'NullSession.__init__'),
    ('flask_sessions_securecookiesession_init', 'NullSession.__init__'),
    ('flask_templating_environment_init', 'DispatchingJinjaLoader.__init__'),
]
OFF_EDGES = [
    ('flask_app_flaskclient_open', 'flask_app_flask_wsgi_app', 'INFERRED'),
    ('json_provider_jsonprovider_response', 'json_provider_jsonprovider_prepare_response_obj', 'INFERRED'),
    ('json_provider_jsonprovider_response', 'json_provider_jsonprovider_dumps', 'INFERRED'),
    ('json_tag_jsontag_tag', 'json_tag_passlist_to_json', 'INFERRED'),
    ('json_tag_tagdict_to_json', 'json_tag_taggedjsonserializer_tag', 'INFERRED'),
    ('json_tag_passlist_to_json', 'json_tag_taggedjsonserializer_tag', 'INFERRED'),
    ('json_tag_taggedjsonserializer_tag', 'json_tag_passlist_check', 'INFERRED'),
    ('flask_sessions_sessionmixin_init', 'flask_sessions_nullsession_init', 'INFERRED'),
    ('flask_sessions_securecookiesession_init', 'flask_sessions_nullsession_init', 'INFERRED'),
    ('flask_templating_environment_init', 'flask_templating_dispatchingjinjaloader_init', 'INFERRED'),
]
# sha256 of json.dumps([nodes, calls, imports], sort_keys=True) from the pre-fix map_graph, REBIND=0
OFF_DIGESTS = {"off": "1389784453d3f45e98a3b7bb7bbd768a936ab738ee9994b65455a4f139f9dcf3",
               "plus": "1389784453d3f45e98a3b7bb7bbd768a936ab738ee9994b65455a4f139f9dcf3",
               "precise": "9ff33d38e335a4af2a859cb9ac2ff058d120dad1f6f29f32b902df4feba4cf62"}

INIT_MODULES = ("flask_init", "json_init", "blueprintapp_init")
ENV_INIT = "flask_templating_environment_init"
MEMBER = {"flask_app_flaskclient_open": ["wsgi_app"], "json_tag_tagdict_to_json": ["tag"],
          "json_tag_passlist_to_json": ["tag"], "json_tag_taggedjsonserializer_tag": ["check"]}
DEFAULT_EDGES = {   # the fixed push, flags off (DEEPMAP_SELF_CALL_REBIND on — the default)
    # R11c/C1: `self.wsgi_app(environ)` keeps the name fan-out's own two candidates and grade (11b: the method alone,
    # INFERRED — a promotion by the exclusion)
    ("flask_app_flaskclient_open", "flask_app_flask_wsgi_app"): "AMBIGUOUS",
    ("flask_app_flaskclient_open", "flask_helpers_wsgi_app"): "AMBIGUOUS",
    ("json_provider_jsonprovider_response", "json_provider_jsonprovider_prepare_response_obj"): "INFERRED",
    ("json_provider_jsonprovider_response", "json_provider_jsonprovider_dumps"): "INFERRED",
    ("json_tag_jsontag_tag", "json_tag_jsontag_to_json"): "INFERRED",
    ("json_tag_tagdict_to_json", "json_tag_jsontag_tag"): "AMBIGUOUS",
    ("json_tag_tagdict_to_json", "json_tag_taggedjsonserializer_tag"): "AMBIGUOUS",
    ("json_tag_passlist_to_json", "json_tag_jsontag_tag"): "AMBIGUOUS",
    ("json_tag_passlist_to_json", "json_tag_taggedjsonserializer_tag"): "AMBIGUOUS",
    ("json_tag_taggedjsonserializer_tag", "json_tag_jsontag_check"): "AMBIGUOUS",
    ("json_tag_taggedjsonserializer_tag", "json_tag_tagdict_check"): "AMBIGUOUS",
    ("json_tag_taggedjsonserializer_tag", "json_tag_passlist_check"): "AMBIGUOUS",
    ("flask_sessions_securecookiesession_init", "flask_sessions_sessionmixin_init"): "INFERRED",
}


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


def _mapped(mode: str, root: Path, graph=None, **extra):
    stats: dict = {}
    with _env(**_mode_env(mode, **extra)), contextlib.redirect_stderr(io.StringIO()):
        nodes, calls, imports = GX.map_graph(graph or _graph(), root=root, stats=stats)
    return nodes, calls, imports, stats


def _run(mode: str, root: Path, graph=None, **extra):
    nodes, calls, imports, stats = _mapped(mode, root, graph, **extra)
    with _env(**_mode_env(mode, **extra)):
        edges = _passes(root, nodes, calls)
    return nodes, calls, edges, stats


def _of(edges: dict, caller: str) -> dict:
    return {b: c for (a, b), c in edges.items() if a == caller}


def _calls_of(calls, caller: str) -> list:
    return [b for a, b in calls if a == caller]


def _indeg(edges: dict, key: str) -> int:
    return sum(1 for (_a, b) in edges if b == key)


def _labels(nodes) -> dict:
    return {n["key"]: n["label"] for n in nodes}


def _digest(nodes, calls, imports) -> str:
    return hashlib.sha256(json.dumps([nodes, [list(c) for c in calls], [list(i) for i in imports]],
                                     sort_keys=True).encode()).hexdigest()


# ---- A ---------------------------------------------------------------------------------------------------------
def gate_a_map(root: Path) -> None:
    print("\n-- A: map_graph — F1 (dunder via another receiver) and F2 (call_member) --")
    nodes, calls, _i, stats = _mapped("off", root)
    check("F1: `BaseEnvironment.__init__(self, **options)` (an external base) is DROPPED — no call tuple at all",
          _calls_of(calls, ENV_INIT) == [], _calls_of(calls, ENV_INIT))
    check("F1: `dict.__init__(self, ...)` (a builtin base) is dropped", _calls_of(calls, "flask_sessions_sessionmixin_init")
          == [], _calls_of(calls, "flask_sessions_sessionmixin_init"))
    check("F1: `SessionMixin.__init__(self, initial)` (an IN-FILE class receiver) binds SessionMixin.__init__ — its "
          "MRO, as for self/super (never the fan-out, never dropped)",
          _calls_of(calls, "flask_sessions_securecookiesession_init") == ["SessionMixin.__init__"],
          _calls_of(calls, "flask_sessions_securecookiesession_init"))
    check("stats: 2 moved (JSONTag.tag's self.to_json + the in-file class receiver), 4 left AMBIGUOUS, 2 dropped",
          (stats.get("self_calls_moved"), stats.get("self_calls_ambiguous"), stats.get("self_calls_dropped"))
          == (2, 4, 2), stats)
    got = {n["key"]: n.get("call_member") for n in nodes if "call_member" in n}
    check("F2: the fall-back pairs are recorded on exactly their callers (`call_member`, sorted names)", got == MEMBER,
          got)
    check("F2: the fall-back tuples themselves are unchanged plain NAME tuples",
          [(a, b) for a, b in calls if a in MEMBER] == [(k, v[0]) for k, v in MEMBER.items()]
          and all(type(b) is str for a, b in calls if a in MEMBER),
          [(a, b, type(b).__name__) for a, b in calls if a in MEMBER])
    for mode in ("plus", "precise", "both"):
        mn, mc, _mi, _ms = _mapped(mode, root)
        fb = [(a, b) for a, b in mc if a in MEMBER]
        check(f"{mode}: every G5 fall-back is a plain NAME (never a CallKey — R11/R1) and call_member is the same",
              fb == [(k, v[0]) for k, v in MEMBER.items()] and not any(GX.is_call_key(b) for _a, b in fb)
              and {n["key"]: n.get("call_member") for n in mn if "call_member" in n} == MEMBER, fb)
    _pn, pc, _pi, _ps = _mapped("precise", root)
    check("precise: the in-file class receiver's dunder is the MARKED key of SessionMixin.__init__",
          _calls_of(pc, "flask_sessions_securecookiesession_init") == ["flask_sessions_sessionmixin_init"]
          and GX.is_call_key(_calls_of(pc, "flask_sessions_securecookiesession_init")[0]),
          _calls_of(pc, "flask_sessions_securecookiesession_init"))
    _n0, legacy, _i0 = GX.map_graph(_graph())
    check("pure mode (no root=): no rebind, no call_member — unchanged",
          not any("call_member" in n for n in _n0) and _calls_of(legacy, ENV_INIT) == ["DispatchingJinjaLoader.__init__"],
          _calls_of(legacy, ENV_INIT))


# ---- B ---------------------------------------------------------------------------------------------------------
def gate_b_push(root: Path) -> None:
    print("\n-- B: the push — no :Module / __init__ fan-out, the polymorphic fan-out kept --")
    for mode in ("off", "plus"):
        nodes, _calls, edges, _st = _run(mode, root)
        lab = _labels(nodes)
        check(f"{mode}: the pushed CALLS are exactly the fixed set", dict(edges) == DEFAULT_EDGES,
              sorted(set(edges.items()) ^ set(DEFAULT_EDGES.items())))
        check(f"{mode}: Environment.__init__ has NO CALLS into any __init__ or :Module (was 12: 9 methods + 3 modules)",
              not [b for b in _of(edges, ENV_INIT) if lab.get(b) == "Module" or b.endswith("_init")],
              _of(edges, ENV_INIT))
        check(f"{mode}: the json/tag.py :Module has in-degree 0 (was 2)", _indeg(edges, "json_tag") == 0,
              [a for (a, b) in edges if b == "json_tag"])
        check(f"{mode}: no package __init__ :Module is a CALLS target",
              all(_indeg(edges, k) == 0 for k in INIT_MODULES), {k: _indeg(edges, k) for k in INIT_MODULES})
        bad = [(a, b, lab.get(b)) for (a, b) in edges if a in MEMBER and lab.get(b) in ("Module", "Class")]
        check(f"{mode}: a G5 fall-back never targets a :Module / :Class", not bad, bad)
        check(f"{mode}: no CALLS edge lands on a :Module at all in this tree",
              not [(a, b) for (a, b) in edges if lab.get(b) == "Module"], [(a, b) for (a, b) in edges if lab.get(b) == "Module"])
    nodes, _c, edges, _s = _run("off", root)
    check("the polymorphic fan-out stays: TaggedJSONSerializer.tag -> 3 `check` methods, AMBIGUOUS",
          _of(edges, "json_tag_taggedjsonserializer_tag") == {"json_tag_jsontag_check": "AMBIGUOUS",
                                                              "json_tag_tagdict_check": "AMBIGUOUS",
                                                              "json_tag_passlist_check": "AMBIGUOUS"},
          _of(edges, "json_tag_taggedjsonserializer_tag"))
    check("R11c/C1: FlaskClient.open's self.wsgi_app() keeps the :Method AND the module-level :Function, graded as "
          "the name fan-out always was (AMBIGUOUS — no exclusion here, and none would promote)",
          _of(edges, "flask_app_flaskclient_open") == {"flask_app_flask_wsgi_app": "AMBIGUOUS",
                                                       "flask_helpers_wsgi_app": "AMBIGUOUS"},
          _of(edges, "flask_app_flaskclient_open"))
    check("JSONProvider.response keeps both of its edges (untouched by G5)",
          _of(edges, "json_provider_jsonprovider_response") == {
              "json_provider_jsonprovider_prepare_response_obj": "INFERRED",
              "json_provider_jsonprovider_dumps": "INFERRED"}, _of(edges, "json_provider_jsonprovider_response"))
    for mode in ("precise", "both"):
        _n, _c, pe, _s = _run(mode, root)
        check(f"{mode}: SecureCookieSession.__init__ -> SessionMixin.__init__ is one EXTRACTED edge (same file)",
              _of(pe, "flask_sessions_securecookiesession_init") == {"flask_sessions_sessionmixin_init": "EXTRACTED"},
              _of(pe, "flask_sessions_securecookiesession_init"))
        check(f"{mode}: Environment.__init__ still has no CALLS; tag.py still in-degree 0",
              _of(pe, ENV_INIT) == {} and _indeg(pe, "json_tag") == 0, (_of(pe, ENV_INIT), _indeg(pe, "json_tag")))
    # the real push entry point: push_to_neo4j persists exactly _resolve_calls' edges
    rows: list = []

    class _Tx:
        def run(self, q, **kw):
            if "[r:CALLS]" in q and "MERGE" in q:
                rows.extend((r["a"], r["b"], r["c"]) for r in (kw.get("rows") or []))
            return []

        def commit(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _Sess(_Tx):
        def begin_transaction(self):
            return _Tx()

    class _Drv:
        def session(self, **_k):
            return _Sess()

    saved = (S._existing_edge_counts, S.edge_resolve)
    try:
        S._existing_edge_counts = lambda *a, **k: {}
        S.edge_resolve = _EdgeResolveStub(S.edge_resolve)
        with _env(**_mode_env("off")), contextlib.redirect_stderr(io.StringIO()):
            nodes, calls, _i, _s = _mapped("off", root)
            c2, _n = S._unkey_receiver_calls(root, nodes, list(calls))
            S._attach_bare_calls(root, nodes, c2)
            S._attach_local_defs(root, nodes, c2)
            S._attach_local_def_calls(root, nodes, c2)
            S.push_to_neo4j(_Drv(), nodes, c2, [], [])
    finally:
        S._existing_edge_counts, S.edge_resolve = saved
    check("push_to_neo4j writes exactly those CALLS (none into tag.py / an __init__ :Module)",
          {(a, b): c for a, b, c in rows} == DEFAULT_EDGES, sorted(rows))


class _EdgeResolveStub:
    """edge_resolve with the typed-edge / import writers stubbed out (the CALLS path is what is under test)."""
    def __init__(self, real):
        self._real = real

    def __getattr__(self, name):
        return getattr(self._real, name)

    @staticmethod
    def edge_rows(*a, **k):
        return []

    @staticmethod
    def rows_by_anchor(rows):
        return {}

    @staticmethod
    def rewrite_anchored_edges(*a, **k):
        return {}

    @staticmethod
    def Resolver(nodes):  # noqa: N802 — mirrors the class it stands in for
        return None


# ---- C ---------------------------------------------------------------------------------------------------------
def gate_c_also_bare(base: Path) -> None:
    print("\n-- C: a name the caller ALSO calls bare is not restricted --")
    tree = dict(TREE)
    tree["src/flask/app.py"] = TREE["src/flask/app.py"].replace(
        "        return self.wsgi_app(environ)\n", "        return self.wsgi_app(environ) or wsgi_app(environ)\n")
    root = _write_tree(base / "c_tree", tree)
    g = _graph()
    # the bare `wsgi_app(environ)` on the same line, as the engine links a call it resolves to another file's def
    g["links"].append({"confidence": "INFERRED", "context": "call", "relation": "calls",
                       "source": "flask_app_flaskclient_open", "source_file": "src/flask/app.py",
                       "source_location": "L17", "target": "flask_helpers_wsgi_app"})
    nodes, calls, edges, _s = _run("off", root, g)
    node = next(n for n in nodes if n["key"] == "flask_app_flaskclient_open")
    check("no call_member for a name that is also called bare", "call_member" not in node, node.get("call_member"))
    check("... and the push keeps its whole name fan-out (the method AND the module-level def), AMBIGUOUS",
          _of(edges, "flask_app_flaskclient_open") == {"flask_app_flask_wsgi_app": "AMBIGUOUS",
                                                       "flask_helpers_wsgi_app": "AMBIGUOUS"},
          _of(edges, "flask_app_flaskclient_open"))


# ---- D ---------------------------------------------------------------------------------------------------------
def _nd(key, name, sf, label="Method", line=1, **extra):
    return dict(key=key, label=label, name=name, source_file=sf, start_line=line, documented=False,
                body_summary=name, **extra)


def gate_d_consumers(base: Path) -> None:
    print("\n-- D: the consumers of call_member --")
    root = base / "d_tree"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "a.py").write_text("def ping():\n    return 0\n\n\nclass A:\n    def go(self, x):\n"
                                       "        return x.ping()\n", encoding="utf-8")
    (root / "pkg" / "b.py").write_text("class B:\n    def ping(self):\n        return 1\n", encoding="utf-8")
    (root / "pkg" / "ping.py").write_text("X = 1\n", encoding="utf-8")

    def world(member=True):
        return [_nd("a_mod", "a", "pkg/a.py", "Module"), _nd("a_ping", "ping", "pkg/a.py", "Function", 1),
                _nd("a_A", "A", "pkg/a.py", "Class", 5),
                _nd("a_go", "A.go", "pkg/a.py", "Method", 6, **({"call_member": ["ping"]} if member else {})),
                _nd("b_ping", "B.ping", "pkg/b.py", "Method", 2), _nd("ping_mod", "ping", "pkg/ping.py", "Module")]

    calls = [("a_go", "ping")]
    with _env(**_mode_env("off")):
        edges = _passes(root, world(), calls)
        plain = _passes(root, world(member=False), calls)
    check("member name (R11c/C1): the :Method AND the module-level :Function stay, ping.py (a :Module) does not — "
          "graded on all 3 candidates (AMBIGUOUS: the exclusion never promotes)",
          dict(edges) == {("a_go", "a_ping"): "AMBIGUOUS", ("a_go", "b_ping"): "AMBIGUOUS"}, dict(edges))
    check("without call_member the name fans out exactly as before (3 targets, AMBIGUOUS)",
          dict(plain) == {("a_go", "a_ping"): "AMBIGUOUS", ("a_go", "b_ping"): "AMBIGUOUS",
                          ("a_go", "ping_mod"): "AMBIGUOUS"}, dict(plain))
    # the caller ALSO makes a bare call with ONE cross-file candidate -> _attach_local_defs gives it its scope
    # names ({N, pong} of n.py — pong is a def NESTED in the caller): the rule then grades that bare call, never the
    # member call `x.pong()`, whose single cross-file candidate stays INFERRED
    (root / "pkg" / "n.py").write_text("class N:\n    def go(self, x):\n        def pong():\n            return 0\n"
                                       "        return x.pong() or helper()\n", encoding="utf-8")

    def nworld(member=True):
        return [_nd("n_go", "N.go", "pkg/n.py", "Method", 2, **({"call_member": ["pong"]} if member else {})),
                _nd("b_pong", "B.pong", "pkg/b.py", "Method", 4), _nd("c_helper", "helper", "pkg/c.py", "Function")]

    wl, wl_plain = nworld(), nworld(member=False)
    with _env(**_mode_env("off")):
        el = _passes(root, wl, [("n_go", "pong"), ("n_go", "helper")])
        el_plain = _passes(root, wl_plain, [("n_go", "pong"), ("n_go", "helper")])
    go = next(n for n in wl if n["key"] == "n_go")
    check("the local-def rule never grades a member call (`x.pong()` does not bind the caller's nested def pong)",
          "pong" in (go.get("local_defs") or ()) and dict(el) == {("n_go", "b_pong"): "INFERRED",
                                                                  ("n_go", "c_helper"): "INFERRED"},
          (go.get("local_defs"), dict(el)))
    check("... while the same call WITHOUT call_member is graded by it (AMBIGUOUS — the rule is live for bare names)",
          dict(el_plain) == {("n_go", "b_pong"): "AMBIGUOUS", ("n_go", "c_helper"): "INFERRED"}, dict(el_plain))
    # ONE cross-file candidate is the case _attach_local_defs would parse the caller's file for — not for a member name
    ws = [n for n in world() if n["key"] in ("a_go", "b_ping")]
    with _env(**_mode_env("off")), contextlib.redirect_stderr(io.StringIO()):
        S._attach_local_defs(root, ws, calls)
    check("_attach_local_defs attaches no scope data for a member name (it never grades one)",
          not any("local_defs" in n for n in ws), [n["key"] for n in ws if "local_defs" in n])
    ws_plain = [n for n in world(member=False) if n["key"] in ("a_go", "b_ping")]
    with _env(**_mode_env("off")), contextlib.redirect_stderr(io.StringIO()):
        S._attach_local_defs(root, ws_plain, calls)
    check("... while the same caller WITHOUT call_member gets it (the rule is live for bare names)",
          "ping" in (next(n for n in ws_plain if n["key"] == "a_go").get("local_defs") or ()), ws_plain)
    only_mod = [_nd("c_go", "C.go", "pkg/a.py", "Method", 6, call_member=["thing"]),
                _nd("thing_mod", "thing", "pkg/thing.py", "Module"), _nd("thing_fn", "thing", "pkg/t2.py", "Function")]
    with _env(**_mode_env("off")):
        e2 = S._resolve_calls(only_mod, [("c_go", "thing")])
    check("R11c/C1: a module-level :Function reachable through an import alias IS kept (never the :Module) — graded "
          "on both candidates (AMBIGUOUS)", e2 == {("c_go", "thing_fn"): "AMBIGUOUS"}, e2)
    no_fit = [only_mod[0], only_mod[1], _nd("thing_cls", "thing", "pkg/t3.py", "Class")]
    with _env(**_mode_env("off")):
        e2b = S._resolve_calls(no_fit, [("c_go", "thing")])
    check("a member name whose only candidates are a :Module and a :Class yields no edge", e2b == {}, e2b)
    both = [_nd("c_go", "C.go", "pkg/a.py", "Method", 6, call_member=["run"]), _nd("r1", "X.run", "pkg/x.py"),
            _nd("r2", "Y.run", "pkg/y.py"), _nd("r3", "Z.run", "pkg/z.py"), _nd("rc", "run", "pkg/run.py", "Class")]
    with _env(**_mode_env("off")):
        e3 = S._resolve_calls(both, [("c_go", "run")])
    check("several :Method candidates stay AMBIGUOUS (a :Class of that name is dropped)",
          e3 == {("c_go", "r1"): "AMBIGUOUS", ("c_go", "r2"): "AMBIGUOUS", ("c_go", "r3"): "AMBIGUOUS"}, e3)
    with _env(**_mode_env("off")):
        e4 = S._resolve_calls(both, [("c_go", "X.run")])
    check("a QUALIFIED name from the same caller is not restricted by call_member (only the bare fall-back is)",
          e4 == {("c_go", "r1"): "INFERRED"}, e4)


# ---- E ---------------------------------------------------------------------------------------------------------
def gate_e_real_engine() -> None:
    print("\n-- E: the REAL engine on the flask-shaped tree --")
    try:
        import graphify  # noqa: F401
    except Exception:
        print("  [SKIP] graphify is not installed in this interpreter — the real-engine section is not run")
        return
    root = _write_tree(_TMP / "real", TREE)
    for mode in ("off", "precise"):
        with _env(**_mode_env(mode, DEEPMAP_HOME=str(_TMP / "home"))), contextlib.redirect_stderr(io.StringIO()), \
                contextlib.redirect_stdout(io.StringIO()):
            res, rep = GX.extract_with_report(root)
            if not res:
                check(f"real engine ({mode}) produced a graph", False, rep.get("cause"))
                return
            nodes, calls, _i = res
            edges = _passes(root, nodes, calls)
        lab = _labels(nodes)
        env_init = next((n["key"] for n in nodes if n["name"] == "Environment.__init__"), None)
        tag_mod = next((n["key"] for n in nodes if n["label"] == "Module" and n["source_file"].endswith("json/tag.py")),
                       None)
        members = {n["key"] for n in nodes if n.get("call_member")}
        check(f"real ({mode}): Environment.__init__ has no CALLS into any __init__ / :Module",
              env_init is not None and not [b for b in _of(edges, env_init) if lab.get(b) == "Module"
                                            or b.endswith("_init")], _of(edges, env_init))
        check(f"real ({mode}): the json/tag.py :Module has in-degree 0", tag_mod is not None and _indeg(edges, tag_mod) == 0,
              [a for (a, b) in edges if b == tag_mod])
        check(f"real ({mode}): no CALLS edge lands on a :Module",
              not [(a, b) for (a, b) in edges if lab.get(b) == "Module"],
              [(a, b) for (a, b) in edges if lab.get(b) == "Module"])
        unfit = [(a, b, lab.get(b)) for (a, b) in edges if a in members and lab.get(b) in ("Module", "Class")]
        check(f"real ({mode}): the G5 fall-back callers are the 4 of the frozen graph and never target a :Module / "
              ":Class", len(members) == 4 and not unfit, (sorted(members), unfit))


# ---- Z ---------------------------------------------------------------------------------------------------------
def gate_z_rebind_off(root: Path) -> None:
    print("\n-- Z: DEEPMAP_SELF_CALL_REBIND=0 — byte-identical to the pre-fix build --")
    for mode in ("off", "plus", "precise"):
        nodes, calls, imports, _s = _mapped(mode, root, DEEPMAP_SELF_CALL_REBIND="0")
        check(f"{mode}, REBIND=0: map_graph's nodes / calls / imports hash to the pre-fix digest",
              _digest(nodes, calls, imports) == OFF_DIGESTS[mode], _digest(nodes, calls, imports))
        check(f"{mode}, REBIND=0: no node carries call_member", not any("call_member" in n for n in nodes))
    nodes, calls, edges, _s = _run("off", root, DEEPMAP_SELF_CALL_REBIND="0")
    check("REBIND=0: the CALLS tuples, in order, equal the pre-fix build's", calls == OFF_CALLS, calls)
    check("REBIND=0: the resolved edges, in order, equal the pre-fix build's",
          [(a, b, c) for (a, b), c in edges.items()] == OFF_EDGES, list(edges.items()))


def main() -> int:
    print("g5_fallback_r11b_gates — the G5 name fall-back: F1 (dunder) + F2 (never a :Module / :Class — R11c/C1)")
    try:
        root = _write_tree(_TMP / "fixture", TREE)
        for gate, args in ((gate_a_map, (root,)), (gate_b_push, (root,)), (gate_c_also_bare, (_TMP,)),
                           (gate_d_consumers, (_TMP,)), (gate_e_real_engine, ()), (gate_z_rebind_off, (root,))):
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
