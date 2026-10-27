#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j gates: the MCP tool descriptions name the project AND keep the "working in a different project?"
remedy on EVERY supported Python, for realistic project paths (v0.28.0 round 9, K9).

THE PROBLEM (round-8 review, confirmed on a python:3.11 container): mcp_bounds.bounded_tool handed the RAW
`fn.__doc__` to project_tool_describer, which drops the remedy sentence once lead + label + remedy + docstring
passes the client's 2,048-char description limit. Python 3.13+ strips docstring indentation at COMPILE time;
3.10-3.12 keep it — so deepmap_impact's docstring is 1,774 chars on 3.13 and 1,846 on 3.11, and on 3.10-3.12
the impact description lost its remedy for EVERY project path (the budget for name + path was -4 chars), while
the dev box (3.13) and CI ("3.x" = the newest release) stayed green. Even on 3.13 the remedy only fit a
name + path of <= 68 chars — a typical `C:\\Users\\<name>\\source\\repos\\<Org>\\<Project>` already lost it.

THE FIX (locked here):
  * the docstring is NORMALISED (inspect.cleandoc) before it is measured and embedded, so the description is
    byte-identical on 3.10, 3.11, 3.12 and 3.13+ (bounded_tool and the describer both normalise; idempotent);
  * when the full remedy would not fit, a COMPACT remedy (still: different project -> /deep-map:setup there, or
    reload for its own .mcp.json) is used before the remedy is dropped at all, so realistic paths keep it;
  * a server started WITHOUT the project env still advertises its docstring byte-for-byte (no regression for a
    hand-started server), and the MCP instructions still carry the full remedy.

How 3.10-3.12 are simulated on any interpreter: the RAW docstring is read from the server source with
`ast.get_docstring(node, clean=False)` (the AST keeps the source text; only 3.13's code generator dedents), and
the real server module is loaded with that raw text installed as `fn.__doc__` at decoration time — exactly what
a 3.11 interpreter does. Optional real-interpreter leg: DEEPMAP_R9_ALT_PYTHON=<python 3.10-3.12 with mcp
installed> runs tests/mcp_project_label_gates.py under it (SKIP when unset).

Hermetic: temp DEEPMAP_HOME, fake embeddings + fake retriever, no sockets, no Neo4j, never touches ~/.deepmap or
the committed .mcp.json.
"""
from __future__ import annotations

import ast
import contextlib
import importlib.util
import inspect
import io
import os
import subprocess
import sys
import tempfile
import time
import types
from pathlib import Path, PurePath

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build"
sys.path.insert(0, str(BUILD))

import mcp_bounds as B  # noqa: E402

_PASS = 0
_FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {label}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}{(' — ' + detail) if detail else ''}")


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


# ------------------------------------------------------------------------------------------ the fixtures
TOOLS = (("deepmap_search_server.py", "deepmap_search"), ("deepmap_search_server.py", "deepmap_impact"),
         ("deepmap_embed_server.py", "deepmap_embed"))

# Realistic project roots: an 80-char Linux / Windows and a 76-char macOS path with a 31-char project name.
# DEEPMAP_PROJECT_ROOT is always a NATIVE path (provision injects the resolved root), so the Windows spelling is
# only exercised on Windows — on POSIX `C:\...` has no separator PurePath splits, so it has no short name.
NAME = "payments-reconciliation-service"
REALISTIC = tuple(r for r in (
    ("Linux", "/home/alexandra.johnson/src/github.com/acme-corp/" + NAME),
    ("Windows", "C:\\Users\\alexandra.johnson\\source\\repos\\AcmeCorp\\" + NAME),
    ("macOS", "/Users/alexandra.johnson/Developer/acme-corp/" + NAME),
) if os.name == "nt" or r[0] != "Windows")


def _server_cfg(fname: str, func: str) -> tuple[str, str, bool]:
    """(the RAW docstring as Python 3.10-3.12 store it, the describer's lead, answers_about_project) — read
    from the server SOURCE, so the gate follows the real decorator arguments."""
    tree = ast.parse((BUILD / fname).read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == func)
    raw = ast.get_docstring(fn, clean=False)
    for dec in fn.decorator_list:
        if not isinstance(dec, ast.Call):
            continue
        for kw in dec.keywords:
            if kw.arg == "describe" and isinstance(kw.value, ast.Call):
                lead = kw.value.args[0].value
                about = next((k.value.value for k in kw.value.keywords if k.arg == "answers_about_project"), True)
                return raw, lead, about
    raise AssertionError(f"{func}: no describe=project_tool_describer(...) in its decorator")


class _CapServer:
    """A FastMCP stand-in that records the description bounded_tool registers each tool with."""

    def __init__(self):
        self.desc = {}

    def tool(self, description=None):
        def reg(fn):
            self.desc[fn.__name__] = description
            return fn
        return reg


def _registered(func: str, doc: str | None, describe) -> str | None:
    """The description bounded_tool registers for a tool named `func` whose __doc__ is `doc`."""
    def body(**kwargs):
        return None
    body.__name__ = func
    body.__doc__ = doc
    srv = _CapServer()
    B.bounded_tool(srv, describe=describe)(body)
    return srv.desc.get(func)


def _remedy_ok(text: str) -> bool:
    """The description's project paragraph tells an agent in ANOTHER project what to do."""
    head = (text or "").split("\n\n", 1)[0]
    return "different project" in head and "/deep-map:setup" in head and ".mcp.json" in head


def _fake_embeddings() -> types.ModuleType:
    m = types.ModuleType("embeddings")

    class ModelUnavailable(RuntimeError):
        pass

    m.ModelUnavailable = ModelUnavailable
    m.ensure_model = lambda max_wait_s=None, retry_after_s=None: object()
    m.embed_query = lambda text: [0.0] * 768
    m._get_model = lambda: object()
    m.model_status = lambda: {"state": "ready"}
    return m


def _load(fname: str, raw_docs: dict | None = None):
    """Load a REAL server module. With `raw_docs` ({tool: raw docstring}) the tools are decorated with that raw
    text as their __doc__ — what a Python 3.10-3.12 interpreter hands bounded_tool."""
    sys.modules["embeddings"] = _fake_embeddings()
    real = B.bounded_tool
    if raw_docs:
        def shim(server, *a, **kw):
            register = real(server, *a, **kw)

            def reg(fn):
                if fn.__name__ in raw_docs:
                    fn.__doc__ = raw_docs[fn.__name__]
                return register(fn)
            return reg
        B.bounded_tool = shim
    try:
        spec = importlib.util.spec_from_file_location(f"dm_r9_{fname[:-3]}_{time.monotonic_ns()}", BUILD / fname)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
    finally:
        B.bounded_tool = real
    return m


def _desc(srv, tool: str) -> str:
    t = srv.mcp._tool_manager.get_tool(tool)
    return t.description if t is not None else ""


# ------------------------------------------------------------------------------------------------ gates
def gate_fixture_is_real() -> dict:
    print("\n-- fixture: the RAW (3.10-3.12) docstrings really differ from the dedented (3.13+) form --")
    raws = {}
    for fname, func in TOOLS:
        raw, lead, _about = _server_cfg(fname, func)
        raws[func] = raw
        check(f"{func}: its raw docstring carries the source indentation (so the simulation is not vacuous)",
              raw is not None and raw != inspect.cleandoc(raw) and len(raw) > len(inspect.cleandoc(raw)),
              f"raw {len(raw or '')} vs clean {len(inspect.cleandoc(raw or ''))}")
        check(f"{func}: the lead read from its decorator is non-empty ({lead!r})", bool(lead))
    impact_raw = raws["deepmap_impact"]
    check("deepmap_impact: the raw form is the long one the review measured (>= 60 chars longer than dedented)",
          len(impact_raw) - len(inspect.cleandoc(impact_raw)) >= 60,
          f"{len(impact_raw)} vs {len(inspect.cleandoc(impact_raw))}")
    return raws


def gate_version_parity(base: Path) -> None:
    print("\n-- the registered description is IDENTICAL for the 3.10-3.12 (raw) and 3.13+ (dedented) docstring --")
    roots = [("a temp project", str((base / "projX").resolve()))] + list(REALISTIC) + [
        ("a 150-char path", str(base.resolve()) + "/" + "d" * max(1, 150 - len(str(base.resolve())) - 6) + "/projL"),
        ("an extreme path", str(base.resolve()) + "/" + "x" * 360 + "/proj")]
    for fname, func in TOOLS:
        raw, lead, about = _server_cfg(fname, func)
        clean = inspect.cleandoc(raw)
        for what, root in roots:
            describe = B.project_tool_describer(lead, answers_about_project=about,
                                                env={"DEEPMAP_PROJECT_ROOT": root})
            d_raw, d_clean = _registered(func, raw, describe), _registered(func, clean, describe)
            check(f"{func}, {what}: raw and dedented docstrings register the SAME description",
                  d_raw is not None and d_raw == d_clean,
                  f"len {len(d_raw or '')} vs {len(d_clean or '')}")
            check(f"{func}, {what}: it ends with the NORMALISED docstring and fits "
                  f"{B.CLIENT_DESCRIPTION_MAX} chars ({len(d_raw or '')})",
                  (d_raw or "").endswith(clean) and len(d_raw or "") <= B.CLIENT_DESCRIPTION_MAX)
        describe = B.project_tool_describer(lead, answers_about_project=about, env={})
        check(f"{func}: WITHOUT the project env nothing is described (the docstring stays the description)",
              _registered(func, raw, describe) is None and _registered(func, clean, describe) is None)
    # A describer called directly (not through bounded_tool) normalises too.
    describe = B.project_tool_describer("Searches the DeepMap graph", env={"DEEPMAP_PROJECT_ROOT": "/p/q"})
    check("describe() itself normalises: a blank-first-line, indented docstring == its dedented form",
          describe("\n    Line one.\n\n    Line two.\n    ") == describe("Line one.\n\nLine two."),
          repr(describe("\n    Line one.\n\n    Line two.\n    ")))


def gate_simulated_py311_server(base: Path, raws: dict) -> None:
    print("\n-- the REAL servers, decorated with the raw docstrings (what Python 3.10-3.12 hand them) --")
    X = base / "projX"
    root = str(X.resolve())
    with _env(DEEPMAP_PROJECT_ROOT=root, DEEPMAP_PROJECT_KEY="projx-key"):
        old = _load("deepmap_search_server.py", raw_docs=raws)
        new = _load("deepmap_search_server.py")
        eold = _load("deepmap_embed_server.py", raw_docs=raws)
        enew = _load("deepmap_embed_server.py")
    check("the simulation took: deepmap_impact.__doc__ is the RAW text in the simulated module",
          old.deepmap_impact.__doc__ == raws["deepmap_impact"])
    for tool in ("deepmap_search", "deepmap_impact"):
        d_old, d_new = _desc(old, tool), _desc(new, tool)
        check(f"{tool}: the 3.10-3.12 description KEEPS the 'different project' remedy",
              _remedy_ok(d_old), repr(d_old.split("\n\n", 1)[0]))
        check(f"{tool}: the 3.10-3.12 description == this interpreter's description (byte-identical)",
              d_old == d_new, f"{len(d_old)} vs {len(d_new)}")
        lead = _server_cfg("deepmap_search_server.py", tool)[1]
        check(f"{tool}: it starts '{lead} of project {X.name} ({root})'",
              d_old.startswith(f"{lead} of project {X.name} ({root}) only."), repr(d_old[:200]))
    check("deepmap_embed: the 3.10-3.12 description == this interpreter's", _desc(eold, "deepmap_embed") ==
          _desc(enew, "deepmap_embed"))
    check("the search server's instructions still carry the FULL remedy",
          B._ELSEWHERE in (old.mcp.instructions or ""), repr(old.mcp.instructions))
    with _env(DEEPMAP_PROJECT_ROOT=None, DEEPMAP_PROJECT_KEY=None):
        bare = _load("deepmap_search_server.py", raw_docs=raws)
    check("WITHOUT the project env the 3.10-3.12 server still advertises its docstring byte-for-byte",
          all(_desc(bare, t) == getattr(bare, t).__doc__ == raws[t] for t in ("deepmap_search", "deepmap_impact")))


def gate_realistic_paths(raws: dict) -> None:
    print("\n-- REALISTIC project paths (~80 chars, a 31-char name) keep the remedy on every Python --")
    for os_name, root in REALISTIC:
        with _env(DEEPMAP_PROJECT_ROOT=root, DEEPMAP_PROJECT_KEY="k"):
            for label, mod in (("3.10-3.12", _load("deepmap_search_server.py", raw_docs=raws)),
                               (sys.version.split()[0], _load("deepmap_search_server.py"))):
                for tool in ("deepmap_search", "deepmap_impact"):
                    d = _desc(mod, tool)
                    head = d.split("\n\n", 1)[0]
                    check(f"{os_name} ({len(root)} chars), Python {label}: {tool} keeps the remedy, the whole "
                          f"path, and fits ({len(d)} chars)",
                          _remedy_ok(d) and f"{NAME} ({root})" in head and len(d) <= B.CLIENT_DESCRIPTION_MAX
                          and d.endswith(inspect.cleandoc(raws[tool])), repr(head[:260]))


def gate_remedy_tiers() -> None:
    print("\n-- the remedy degrades in steps: full -> compact -> none -> the path shortened in the middle --")
    short = getattr(B, "_ELSEWHERE_SHORT", None)
    check("a compact remedy exists (mcp_bounds._ELSEWHERE_SHORT)", isinstance(short, str) and bool(short))
    if not isinstance(short, str):
        return
    check("the compact remedy says the same thing (different project -> /deep-map:setup there, or reload for "
          "its own .mcp.json)", _remedy_ok(short), repr(short))
    check(f"...and is materially shorter than the full one ({len(short)} vs {len(B._ELSEWHERE)} chars)",
          len(short) <= len(B._ELSEWHERE) - 50)
    raw, lead, about = _server_cfg("deepmap_search_server.py", "deepmap_impact")
    doc = inspect.cleandoc(raw)
    fixed = len(f"{lead} of project  () only. \n\n{doc}")        # everything but label text + remedy
    full_budget = B.CLIENT_DESCRIPTION_MAX - fixed - len(B._ELSEWHERE)
    short_budget = B.CLIENT_DESCRIPTION_MAX - fixed - len(short)

    def root_for(total: int) -> str:
        # a root whose name ("p") + path together take exactly `total` chars
        return "/" + "r" * max(1, total - 4) + "/p"

    def run(total: int) -> str:
        root = root_for(total)
        return B.project_tool_describer(lead, answers_about_project=about,
                                        env={"DEEPMAP_PROJECT_ROOT": root})(raw)

    d = run(full_budget)
    check(f"name + path = {full_budget} chars (the full budget): the FULL remedy", B._ELSEWHERE in d, str(len(d)))
    d = run(full_budget + 1)
    check(f"one char more: the COMPACT remedy ({len(d)} chars)", short in d and B._ELSEWHERE not in d
          and len(d) <= B.CLIENT_DESCRIPTION_MAX, str(len(d)))
    d = run(short_budget)
    check(f"name + path = {short_budget} chars (the compact budget): still the compact remedy",
          short in d and len(d) <= B.CLIENT_DESCRIPTION_MAX, str(len(d)))
    d = run(short_budget + 1)
    root = root_for(short_budget + 1)
    check("one char more: no remedy, but the WHOLE path is still shown (the instructions carry the remedy)",
          short not in d and B._ELSEWHERE not in d and f"({root})" in d and len(d) <= B.CLIENT_DESCRIPTION_MAX,
          str(len(d)))
    d = run(900)
    check("an extreme path: shortened in the MIDDLE, the docstring intact, within the limit",
          "…" in d.split("\n\n", 1)[0] and d.endswith(doc) and len(d) <= B.CLIENT_DESCRIPTION_MAX, str(len(d)))
    need = max(len(PurePath(r).name) + len(r) for _os, r in REALISTIC)
    check(f"DRIFT LOCK: the compact budget ({short_budget} chars of name + path; full remedy: {full_budget}) covers "
          f"the realistic paths ({need}) — headroom {short_budget - need} chars for deepmap_impact's docstring to grow",
          short_budget >= need)


def gate_alt_interpreter() -> None:
    print("\n-- optional: tests/mcp_project_label_gates.py under a REAL Python 3.10-3.12 (DEEPMAP_R9_ALT_PYTHON) --")
    alt = os.environ.get("DEEPMAP_R9_ALT_PYTHON", "").strip()
    if not alt:
        print("  [SKIP] DEEPMAP_R9_ALT_PYTHON is not set (the in-process simulation above covers the logic)")
        return
    try:
        ver = subprocess.run([alt, "-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
                             capture_output=True, text=True, timeout=60).stdout.strip()
        p = subprocess.run([alt, str(REPO / "tests" / "mcp_project_label_gates.py")], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=600,
                           env={**os.environ, "PYTHONUTF8": "1"})
    except Exception as exc:  # noqa: BLE001
        check("the alternate interpreter ran", False, f"{type(exc).__name__}: {exc}")
        return
    tail = (p.stdout or "").strip().splitlines()[-1:] or [""]
    check(f"mcp_project_label_gates is GREEN under Python {ver}", p.returncode == 0 and "GREEN" in tail[0],
          f"rc={p.returncode} {tail[0]!r} {(p.stderr or '')[-300:]!r}")


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 72)
    print("DeepMap — K9: project label + remedy survive on every supported Python (no Neo4j)")
    print(f"(running on Python {sys.version.split()[0]})")
    print("=" * 72)
    saved_emb = sys.modules.get("embeddings")
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        (base / "projX").mkdir()
        (base / "home").mkdir()
        with _env(DEEPMAP_HOME=str(base / "home"), NEO4J_URI=None, DEEPMAP_MCP_TRANSPORT=None,
                  DEEPMAP_RETRIEVAL_LOG=None, DEEPMAP_APPLY_LEARNED=None, DEEPMAP_PROJECT_GUARD=None,
                  DEEPMAP_PROJECT_ROOT=None, DEEPMAP_PROJECT_KEY=None, DEEPMAP_MCP_WARMUP="0"), \
                contextlib.redirect_stderr(io.StringIO()):
            try:
                raws = gate_fixture_is_real()
            except Exception as exc:  # noqa: BLE001
                check("gate_fixture_is_real ran without crashing", False, f"{type(exc).__name__}: {exc}")
                raws = None
            if raws:
                for gate, args in ((gate_version_parity, (base,)), (gate_simulated_py311_server, (base, raws)),
                                   (gate_realistic_paths, (raws,)), (gate_remedy_tiers, ())):
                    try:
                        gate(*args)
                    except Exception as exc:  # noqa: BLE001 — a crashing gate is RED, not a crashed suite
                        check(f"{gate.__name__} ran without crashing", False, f"{type(exc).__name__}: {exc}")
        gate_alt_interpreter()
    if saved_emb is not None:
        sys.modules["embeddings"] = saved_emb
    else:
        sys.modules.pop("embeddings", None)
    print("\n" + "=" * 72)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
