#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fix round r283 (O2) — the MCP deepmap_impact tool keeps its project awake, and describes its walk truthfully.

ISS-build:021 (C8). The idle-hibernation signal is the project's registry `last_used`: provision.hibernate_idle stops
ANOTHER project's running Neo4j once that stamp is 30 min old (another project's `deepmap hibernate`, or the opt-in
SessionStart sweep). MCP deepmap_search stamped it (port_registry.touch_last_used_by_bolt, keyed on the injected
NEO4J_URI's bolt port — the server's cwd is the plugin repo, not the project), and so do CLI search and CLI impact
("impact is a use, like search") — but MCP deepmap_impact returned WITHOUT stamping, so a session that only traced
blast radius over MCP looked idle and could have its instance stopped mid-session. THE FIX: both MCP tools stamp the
same best-effort way after an answered call; the answer is unchanged and a failing stamp never fails the call.

ISS-build:018 (C6), the MCP description. The model-facing deepmap_impact description said "a graph without runtime
edges gets one-hop :CALLS". query_impact narrows the requested edge types to those present (CALLS always kept) and
still walks *1..max_hops (IMPACT_MAX_HOPS = 3); one hop happens only on the error paths (a transaction-memory re-walk,
the unsupported-feature / timeout fallback), each announced by a trailing note. THE FIX: the description says the walk
stays multi-hop over :CALLS. It is still project-label-first, identical for the Python 3.10-3.12 (raw) and 3.13+
(dedented) docstring, and the realistic-path remedy still fits the client's 2,048-char limit; the claim is also
cross-checked against the REAL query_impact over a fake Neo4j session.

Hermetic: a temp DEEPMAP_HOME (the real port_registry writes only there), a fake `embeddings` module, a fake retriever /
fake Neo4j session; no sockets, no Neo4j, no model; never touches ~/.deepmap, the committed .mcp.json or a retrieval
log (DEEPMAP_RETRIEVAL_LOG=0 and DEEPMAP_LEARNING=0 veto every log switch).
Usage: python tests/o2_mcp_impact_r283_gates.py [--root <tree>]
"""
from __future__ import annotations

import ast
import contextlib
import importlib.util
import inspect
import json
import os
import re
import sys
import tempfile
import time
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePath

REPO = Path(__file__).resolve().parent.parent
PASSES: list[str] = []
FAILS: list[str] = []

BOLT = 7811                                 # the per-project bolt port provision would inject
URI = f"bolt://127.0.0.1:{BOLT}"
ROW = {"caller": "checkout", "caller_type": "Function", "file": "shop/cart.py", "line": 12,
       "confidence": "INFERRED", "callee": "save", "hop": 1, "via": "CALLS", "via_path": ["CALLS"]}
IDLE_WINDOW_S = 30 * 60                     # provision.DEFAULT_IDLE_MIN — the hibernation window

# Realistic project roots (as in tests/mcp_label_r9_gates.py): an 80-char Linux / Windows path, a 31-char name.
NAME = "payments-reconciliation-service"
REALISTIC = tuple(r for r in (
    ("Linux", "/home/alexandra.johnson/src/github.com/acme-corp/" + NAME),
    ("Windows", "C:\\Users\\alexandra.johnson\\source\\repos\\AcmeCorp\\" + NAME),
) if os.name == "nt" or r[0] != "Windows")

# A statement about a graph that has no typed (framework/runtime) edges, and a one-hop claim.
_NO_TYPED_EDGES = re.compile(r"\b(?:without|no)\b[^.;]*?\b(?:runtime|typed|framework)\b[^.;]*?\bedges?\b"
                             r"|pre-Track-C", re.IGNORECASE)
_ONE_HOP = re.compile(r"\b(?:one|1|single)[- ]hop\b", re.IGNORECASE)


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSES if ok else FAILS).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok or not detail else f" — {detail}"))


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


def _load_server(build: Path):
    """A fresh copy of the REAL search server module (its `embeddings` import is the fake one)."""
    spec = importlib.util.spec_from_file_location(f"dm_o2_r283_srv_{time.monotonic_ns()}",
                                                  build / "deepmap_search_server.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _load_retrieval(build: Path):
    spec = importlib.util.spec_from_file_location(f"dm_o2_r283_ret_{time.monotonic_ns()}", build / "retrieval.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ---------------------------------------------------------------------------------- fakes: retriever + touch
class _Rows(list):
    """What DeepMapRetriever.impact returns: the dependent rows plus the honesty `notes`."""

    def __init__(self, rows=(), notes=()):
        super().__init__(rows)
        self.notes = list(notes)


class _Retriever:
    def __init__(self):
        self.notes: list = []
        self.error = None
        self.calls: list = []

    def impact(self, symbol, limit=30, edge_types=None):
        self.calls.append(("impact", symbol, limit, edge_types))
        if self.error is not None:
            raise self.error
        return _Rows([dict(ROW)], self.notes)

    def search(self, query, top_k=15):
        self.calls.append(("search", query, top_k))
        return [{"name": "save", "score": 1.0}]


@contextlib.contextmanager
def _recorded_touch(PR, fail: bool = False):
    """port_registry.touch_last_used_by_bolt replaced by a recorder (the server imports port_registry at call time,
    so it resolves to this same module object)."""
    seen: list = []
    saved = PR.touch_last_used_by_bolt

    def fake(port, env=None):
        seen.append(port)
        if fail:
            raise RuntimeError("registry unwritable (fake)")

    PR.touch_last_used_by_bolt = fake
    try:
        yield seen
    finally:
        PR.touch_last_used_by_bolt = saved


def _call(fn, *a, **kw):
    try:
        return fn(*a, **kw), None
    except Exception as exc:  # noqa: BLE001
        return None, exc


# ------------------------------------------------------------------------ A. ISS-build:021 — the last_used stamp
def gate_impact_stamps(srv, PR) -> None:
    print("\n-- A1 (ISS-build:021): an answered deepmap_impact stamps this project's last_used, like search --")
    ret = _Retriever()
    srv._get_retriever = lambda: ret
    with _recorded_touch(PR) as seen:
        out, err = _call(srv.deepmap_impact, "save")
    check(f"deepmap_impact stamps last_used once, keyed on the injected NEO4J_URI's bolt port ({URI} -> {BOLT})",
          err is None and seen == [BOLT], f"touch_last_used_by_bolt calls {seen}, error {err!r}")
    check("...and the answer is unchanged (the stamp adds no row and no note)", out == [ROW], repr(out))

    with _recorded_touch(PR) as seen:
        _out, err = _call(srv.deepmap_search, "where is the cart saved")
    check("deepmap_search still stamps the same port (its stamp is unchanged)", err is None and seen == [BOLT],
          f"{seen} / {err!r}")

    import anyio

    async def via_mcp():
        return await srv.mcp._tool_manager.call_tool("deepmap_impact", {"symbol": "save"}, convert_result=False)
    with _recorded_touch(PR) as seen:
        out, err = _call(anyio.run, via_mcp)
    check("...over the real MCP tool path too (bounded_tool's worker thread): the call answers and stamps once",
          err is None and out == [ROW] and seen == [BOLT], f"{seen} / {out!r} / {err!r}")

    ret.notes = ["'save' has 1 dependent (fake retriever note)."]
    with _recorded_touch(PR) as seen:
        out, err = _call(srv.deepmap_impact, "save", limit=500)
    notes = [r["note"] for r in (out or []) if isinstance(r, dict) and set(r) == {"note"}]
    check("an answer carrying retriever notes and a clamped limit is stamped too — and still ends with both notes",
          err is None and seen == [BOLT] and (out or [None])[0] == ROW and len(notes) == 2
          and notes[0] == ret.notes[0] and "clamped" in notes[1], f"{seen} / {out!r} / {err!r}")
    ret.notes = []

    with _recorded_touch(PR, fail=True) as seen:
        out, err = _call(srv.deepmap_impact, "save")
    check("a stamp that RAISES never fails the call (best-effort): the answer comes back unchanged",
          seen == [BOLT] and err is None and out == [ROW], f"{seen} / {out!r} / {err!r}")

    with _env(NEO4J_URI=None), _recorded_touch(PR):
        out, err = _call(srv.deepmap_impact, "save")
    check("without NEO4J_URI (a hand-started server) the call still answers — the stamp never raises into it",
          err is None and out == [ROW], f"{out!r} / {err!r}")

    with _recorded_touch(PR) as seen:
        _out, err = _call(srv.deepmap_impact, "   ")
    check("an empty symbol is refused (ValueError) before any Neo4j use, and stamps nothing",
          isinstance(err, ValueError) and seen == [], f"{err!r} / {seen}")

    boom = RuntimeError("fake impact failure")
    ret.error = boom
    with _recorded_touch(PR) as seen:
        _out, err = _call(srv.deepmap_impact, "save")
    ret.error = None
    check("a FAILED impact raises its own error unchanged and stamps nothing (only an answered call is a use — as "
          "for MCP search and CLI impact)", err is boom and seen == [], f"{err!r} / {seen}")


def gate_real_registry(srv, PR) -> None:
    print("\n-- A2 (ISS-build:021): end to end — the REAL port_registry refreshes the entry (temp DEEPMAP_HOME) --")
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    reg = {"version": PR.REGISTRY_VERSION, "kind": PR.REGISTRY_KIND, "updated_utc": old, "projects": {
        "mine-key": {"root": "C:/work/mine", "search_port": 8789, "embed_port": 8790,
                     "bolt_port": BOLT, "http_port": 7598, "last_used": old},
        "other-key": {"root": "C:/work/other", "search_port": 8791, "embed_port": 8792,
                      "bolt_port": BOLT + 2, "http_port": 7600, "last_used": old}}}
    path = PR.registry_path()
    home = Path(os.environ["DEEPMAP_HOME"]).resolve()
    check("fixture: the registry lives in the TEMP DEEPMAP_HOME (never ~/.deepmap)",
          path.resolve().parent == home and "deepmap_o2_r283_" in str(home), str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(reg, indent=2), encoding="utf-8")
    check("fixture: the project starts IDLE by hibernate's rule (last_used 2 h old >= the 30-min window)",
          (PR._age_seconds(old) or 0) >= IDLE_WINDOW_S, str(PR._age_seconds(old)))

    ret = _Retriever()
    srv._get_retriever = lambda: ret
    out, err = _call(srv.deepmap_impact, "save")
    after = json.loads(path.read_text(encoding="utf-8")).get("projects", {})
    mine, other = after.get("mine-key", {}), after.get("other-key", {})
    age = PR._age_seconds(mine.get("last_used"))
    check("after ONE impact-only call the project's last_used is fresh (< 60 s), so hibernate_idle keeps it",
          err is None and age is not None and age < 60, f"age {age}, error {err!r}")
    check("...only this server's project is stamped (the other project's entry is untouched)",
          other.get("last_used") == old, repr(other))
    check("...its port pools are preserved and no entry is added",
          len(after) == 2 and (mine.get("search_port"), mine.get("embed_port"), mine.get("bolt_port"),
                               mine.get("http_port")) == (8789, 8790, BOLT, 7598), repr(after))
    check("...and the answer is unchanged", out == [ROW], repr(out))


# ---------------------------------------------------------- B. ISS-build:018 (C6) — the MCP tool description
def _impact_doc(build: Path) -> tuple[str, str, str]:
    """(the RAW docstring as Python 3.10-3.12 store it, its normalised form, the describer's lead) — read from the
    server SOURCE, so the gate follows the real text and decorator arguments."""
    tree = ast.parse((build / "deepmap_search_server.py").read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "deepmap_impact")
    raw = ast.get_docstring(fn, clean=False) or ""
    lead = ""
    for dec in fn.decorator_list:
        for kw in getattr(dec, "keywords", []):
            if kw.arg == "describe" and isinstance(kw.value, ast.Call) and kw.value.args:
                lead = kw.value.args[0].value
    return raw, inspect.cleandoc(raw), lead


def _statements(text: str) -> list[str]:
    flat = " ".join((text or "").split())
    return [s for s in re.split(r"(?<=[.;])\s+", flat) if s]


def _walk_claims(label: str, text: str) -> None:
    """The description's claims about the walk depth, checked statement by statement."""
    st = _statements(text)
    about = [s for s in st if _NO_TYPED_EDGES.search(s)]
    check(f"{label}: it still tells the agent what a graph WITHOUT runtime edges gets", bool(about), text[-900:])
    stale = [s for s in about if _ONE_HOP.search(s)]
    check(f"{label}: ...and no longer says such a graph gets ONE hop (the stale claim)", not stale, repr(stale))
    check(f"{label}: ...it says the walk there stays MULTI-HOP over :CALLS",
          any("multi-hop" in s.lower() and "CALLS" in s for s in about), repr(about))
    loose = [s for s in st if _ONE_HOP.search(s) and "note" not in s.lower()]
    check(f"{label}: one hop is claimed nowhere except in the trailing-note list (it is an error path)",
          not loose, repr(loose))
    noted = [s for s in st if "note" in s.lower() and re.search(r"reduced in depth|cut to 1 hop|\b(?:1|one)[- ]hop",
                                                                   s, re.IGNORECASE)]
    check(f"{label}: a walk cut short in depth is still listed as a trailing {{\"note\"}}", bool(noted),
          text[-600:])


def gate_description(build: Path, B, base: Path) -> None:
    print("\n-- B1 (ISS-build:018): the deepmap_impact description no longer claims one hop without runtime edges --")
    raw, clean, lead = _impact_doc(build)
    _walk_claims("the docstring (source)", clean)

    X = base / "projX"
    X.mkdir(exist_ok=True)
    root = str(X.resolve())
    with _env(DEEPMAP_PROJECT_ROOT=root, DEEPMAP_PROJECT_KEY="projx-key"):
        srv = _load_server(build)
    tool = srv.mcp._tool_manager.get_tool("deepmap_impact")
    desc = tool.description if tool is not None else ""
    _walk_claims("the REGISTERED description (project env)", desc)
    check(f"project label FIRST: it starts '{lead} of project {X.name} ({root}) only.'",
          bool(lead) and desc.startswith(f"{lead} of project {X.name} ({root}) only."), repr(desc[:200]))
    check(f"...ends with the normalised docstring and fits {B.CLIENT_DESCRIPTION_MAX} chars ({len(desc)})",
          desc.endswith(clean) and len(desc) <= B.CLIENT_DESCRIPTION_MAX, str(len(desc)))

    with _env(DEEPMAP_PROJECT_ROOT=None, DEEPMAP_PROJECT_KEY=None):
        bare = _load_server(build)
    tool = bare.mcp._tool_manager.get_tool("deepmap_impact")
    _walk_claims("the description WITHOUT the project env (the docstring itself)", tool.description if tool else "")

    print("\n-- B2: identical for the 3.10-3.12 (raw) and 3.13+ (dedented) docstring; the remedy still fits --")
    check("fixture: the raw docstring really carries source indentation (the comparison is not vacuous)",
          raw != clean and len(raw) > len(clean), f"raw {len(raw)} vs clean {len(clean)}")

    class _Cap:
        def __init__(self):
            self.desc = {}

        def tool(self, description=None):
            def reg(fn):
                self.desc[fn.__name__] = description
                return fn
            return reg

    def registered(doc: str, describe) -> "str | None":
        def deepmap_impact(**kwargs):
            return None
        deepmap_impact.__doc__ = doc
        cap = _Cap()
        B.bounded_tool(cap, describe=describe)(deepmap_impact)
        return cap.desc.get("deepmap_impact")

    def remedy(text: str) -> bool:
        head = (text or "").split("\n\n", 1)[0]
        return "different project" in head and "/deep-map:setup" in head and ".mcp.json" in head

    for what, r in [("a temp project", root)] + list(REALISTIC):
        describe = B.project_tool_describer(lead, env={"DEEPMAP_PROJECT_ROOT": r})
        d_raw, d_clean = registered(raw, describe), registered(clean, describe)
        check(f"{what}: raw and dedented docstrings register the SAME description",
              d_raw is not None and d_raw == d_clean, f"{len(d_raw or '')} vs {len(d_clean or '')}")
        check(f"{what} ({len(r)} chars): project-label first, the 'different project' remedy kept, within "
              f"{B.CLIENT_DESCRIPTION_MAX} chars ({len(d_raw or '')})",
              (d_raw or "").startswith(f"{lead} of project {PurePath(r).name} ({r}) only.") and remedy(d_raw)
              and len(d_raw or "") <= B.CLIENT_DESCRIPTION_MAX and (d_raw or "").endswith(clean),
              repr((d_raw or "")[:240]))


# -------------------------------------------------- B3. the claim is TRUE: the real walk (fake Neo4j session)
class _Session:
    def __init__(self, drv):
        self.drv = drv

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, cypher, **params):
        text = getattr(cypher, "text", cypher)
        self.drv.log.append(text)
        if "count(callee) AS matched" in text:
            return [{"matched": 1}]
        if "db.relationshipTypes" in text:
            return [{"types": list(self.drv.present)}]
        if "MATCH path = (caller)" in text:                  # the multi-hop impact walk
            if self.drv.walk_errors:
                raise self.drv.walk_errors.pop(0)
            return [dict(r) for r in self.drv.walk_rows]
        if "MATCH (caller)-[r:CALLS]->(callee)" in text:     # query_callers (the one-hop fallback)
            return [dict(r) for r in self.drv.callers_rows]
        return []


class _Driver:
    def __init__(self, present=("CALLS",), walk_rows=(), walk_errors=(), callers_rows=()):
        self.present, self.walk_rows = tuple(present), list(walk_rows)
        self.walk_errors, self.callers_rows = list(walk_errors), list(callers_rows)
        self.log: list = []

    def session(self, **kw):
        return _Session(self)


def _walks(drv) -> list:
    return [m.groups() for c in drv.log for m in [re.search(r"\[rels:([A-Z_|]+)\*1\.\.(\d+)\]", c)] if m]


def gate_walk_truth(build: Path) -> None:
    print("\n-- B3: the description is TRUE — the real query_impact on a graph whose only edge type is CALLS --")
    R = _load_retrieval(build)
    hop2 = dict(ROW, caller="place_order", file="shop/orders.py", line=40, hop=2, via_path=["CALLS", "CALLS"])
    drv = _Driver(present=("CALLS",), walk_rows=[ROW, hop2])
    rows, err = _call(R.query_impact, drv, "save")
    walks = _walks(drv)
    check("the walk follows :CALLS alone (the graph has no runtime edges)",
          err is None and len(walks) == 1 and walks[0][0] == "CALLS", f"{walks} / {err!r}")
    check(f"...and is still MULTI-hop: *1..{getattr(R, 'IMPACT_MAX_HOPS', '?')} (IMPACT_MAX_HOPS), not one hop",
          bool(walks) and int(walks[0][1]) == getattr(R, "IMPACT_MAX_HOPS", -1) and int(walks[0][1]) >= 2, str(walks))
    check("...a 2-hop (transitive) dependent comes back, the answer reports its full depth, and no note says it "
          "was reduced", rows is not None and any(r.get("hop") == 2 for r in rows)
          and getattr(rows, "max_hops", None) == getattr(R, "IMPACT_MAX_HOPS", -2)
          and not getattr(rows, "notes", ["?"]), f"{rows!r} notes={getattr(rows, 'notes', None)}")

    mem = RuntimeError("The allocation of an extra 2.0 MiB would use more than the limit 1.0 GiB "
                       "(dbms.memory.transaction.total.max) — fake transaction-memory refusal")
    drv = _Driver(present=("CALLS",), walk_rows=[ROW], walk_errors=[mem])
    rows, err = _call(R.query_impact, drv, "save")
    notes = " ".join(getattr(rows, "notes", []) or [])
    check("one hop is an ERROR path: a transaction-memory refusal re-walks at 1 hop — and a note says so",
          err is None and [w[1] for w in _walks(drv)] == [str(R.IMPACT_MAX_HOPS), "1"]
          and getattr(rows, "max_hops", None) == 1 and bool(_ONE_HOP.search(notes)), f"{_walks(drv)} / {notes!r}")

    from neo4j.exceptions import ClientError
    drv = _Driver(present=("CALLS",), walk_errors=[ClientError("variable-length pattern not supported (fake)")],
                  callers_rows=[{"caller": "checkout", "caller_type": "Function", "file": "shop/cart.py",
                                 "line": 12, "confidence": "INFERRED", "callee": "save"}])
    rows, err = _call(R.query_impact, drv, "save")
    notes = " ".join(getattr(rows, "notes", []) or [])
    check("...and an unsupported-feature error falls back to one-hop query_callers — with a note saying so",
          err is None and bool(rows) and bool(re.search(r"\(1 hop\)|1[- ]hop|one[- ]hop", notes, re.IGNORECASE)),
          f"{rows!r} / {notes!r} / {err!r}")


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    root = REPO
    if "--root" in sys.argv:
        root = Path(sys.argv[sys.argv.index("--root") + 1]).resolve()
    build = root / "build"
    sys.path.insert(0, str(build))
    print("=" * 72)
    print(f"DeepMap — MCP deepmap_impact: stamps last_used; describes the no-runtime-edge walk truthfully (root: {root})")
    print("=" * 72)
    saved_emb = sys.modules.get("embeddings")
    sys.modules["embeddings"] = _fake_embeddings()
    try:
        with tempfile.TemporaryDirectory(prefix="deepmap_o2_r283_") as td:
            base = Path(td)
            (base / "home").mkdir()
            with _env(DEEPMAP_HOME=str(base / "home"), NEO4J_URI=URI, DEEPMAP_PROJECT_ROOT=None,
                      DEEPMAP_PROJECT_KEY=None, DEEPMAP_PROJECT_GUARD=None, DEEPMAP_MCP_TRANSPORT=None,
                      DEEPMAP_MCP_WARMUP="0", DEEPMAP_RETRIEVAL_LOG="0", DEEPMAP_LEARNING="0",
                      DEEPMAP_APPLY_LEARNED=None, DEEPMAP_IMPACT_EDGES=None, DEEPMAP_IMPACT_PROFILE=None,
                      DEEPMAP_IMPACT_MAX_HOPS=None, DEEPMAP_SESSION_ID=None):
                import mcp_bounds as B
                import port_registry as PR
                srv = _load_server(build)
                for gate, args in ((gate_impact_stamps, (srv, PR)), (gate_real_registry, (srv, PR)),
                                   (gate_description, (build, B, base)), (gate_walk_truth, (build,))):
                    try:
                        gate(*args)
                    except Exception as exc:  # noqa: BLE001 — a crashing gate is RED, not a crashed suite
                        check(f"{gate.__name__} ran without crashing", False, f"{type(exc).__name__}: {exc}")
    finally:
        if saved_emb is not None:
            sys.modules["embeddings"] = saved_emb
        else:
            sys.modules.pop("embeddings", None)

    total = len(PASSES) + len(FAILS)
    print("\n" + "=" * 72)
    if FAILS:
        print(f"RESULT: RED — {len(PASSES)}/{total} gates passed.")
        return 1
    print(f"RESULT: GREEN — {len(PASSES)}/{total} gates passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
