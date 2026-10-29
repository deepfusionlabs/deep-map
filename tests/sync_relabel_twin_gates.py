#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j gates: a push removes every node whose key this extraction gives a DIFFERENT label (review F1).

_upsert_nodes MERGEs by (:Label {key}) and the stale prune deletes by key alone, so when an extraction gives a live
key a new label the old node stayed beside the new one for good: a twin with the old name, summary and embedding,
in the vector and full-text indexes and in impact's name matches, which no prune ever removed (its key is live).
The case that found it: graphify 0.8.39 with the default-on C# owner step keeps a record / struct / enum method's
key and moves its label :Function -> :Method; DEEPMAP_CSHARP_MEMBERS=0 moves it back. push_to_neo4j now reads the
twins (before any write), stamps the full-text rebuild, deletes them (relationships first, bounded) and reports how
many on its result (`.relabeled`); sync() re-creates deepmap_code_fts, logs it and records `relabeled` in
sync_last.json. It runs on every push, whatever the stale prune decides.
The same pass removes a CLASS SHELL twin (2026-10 live verification): a stale :Class whose (source_file, name,
start_line) equals a :Class this push writes under another key. On 0.8.39 the csharp_edges recognizer's record shell
(`<file>::OrderLine:27`, "OrderLine: C# record") stayed beside the owner (`<id>@OrderLine:L27`) after a sync that does
not prune. The census is now one key-only scan per label, diffed client side, plus a confirming seek for the
candidates only (it used to send about 4N keys on every push).

  A  sync._relabel_twins / _delete_relabel_twins over a model graph: a node under a label its key no longer has is
     a twin; a node under its live label, a stale key (the prune's business), a node that also carries a live
     label, and a key this extraction gives two labels are not; a twin under a label this extraction writes for no
     key at all (one of indexes.KEY_INDEX_LABELS) is found; every census statement is a read that names its label
     (never a label-less MATCH (n)): one key-only scan per label, then a batched confirming seek over the
     candidates only; an unsafe label is never interpolated; the delete is relationships first (out, in), then the
     nodes, each batched IN TRANSACTIONS and guarded by label and key and the live labels. Class shells: exactly a
     stale :Class with the file, name and line of a pushed :Class; never another partial part (another line), a
     same-named class of another file, another name on that line, a node under another label, a match on a pushed
     :Method, a node with no line (or line 0), another letter case of the file; a push that writes no class reads
     no stale class key; the shell delete re-checks file, name and line.
  B  push_to_neo4j through the model graph: the 0.8.39 -> members shape (three :Function twins of three :Method
     nodes, with an embedding and edges) leaves only the :Method nodes, their CALLS rebuilt, no embedding carried
     over (the normal path embeds them); the reverse direction too; the result is still the plain pair and
     `.relabeled` counts the twins; order: census before any write, the pending stamp before the first delete,
     the deletes before the upsert; no twin -> no stamp and no delete; a second push removes nothing; a stale key
     is left to the prune. With a record shell: it goes with its edges, the must-nots stay, `.shells` and `.moves`
     report it, both directions.
  C  sync() end to end (the real push over the model graph): the twin is removed when the >50% guard skips the
     prune, in the background refresh's evidence-only pass and with no stale-cleanup at all; the full-text index is
     re-created after the push and the pending stamp cleared; the log names the removal and the label moves it saw
     (both directions, never a fixed C# example); sync_last.json records `relabeled`; the manifest's embedded total
     and node total no longer count the twin; a pass with no twin re-creates nothing and records no `relabeled`.
     Shells: their own log line and full-text reason, counted in `relabeled`, never again in `pruned`,
     `stale_kept` or a blast-radius skip.
  D  the installed graphify (0.8.39 or 0.8.51) on a C# record / struct tree, mapped with DEEPMAP_CSHARP_MEMBERS=0
     and with the default, pushed in both orders: the graph ends with exactly the second extraction's nodes. On
     0.8.39 the keys really change label (the teeth: a key-only prune leaves twins); on 0.8.51 none does. With the
     recognizers too and two files gone from the second extraction: on 0.8.39 exactly the record shells (or, the
     other way, their owners) are removed, and a same-named class of a gone file, a partial part of a gone file and
     an owner with no shell stay; on 0.8.51 nothing is removed.
  E  mutation teeth: each mutant of build/sync.py must turn a check RED.
  F  the census cost, counted by a recording driver over a 6,000- and a 50,000-node synthetic graph: one key-only
     scan per label, N rows read in all, no key sent when nothing can be a twin; with a few twins and stale classes,
     only those keys are sent.

Hermetic: temp trees, a temp DEEPMAP_HOME, a model graph driver; no Neo4j, no network, no model. D runs the
installed engine once (about 5 s on 0.8.39, 10 s on 0.8.51) and skips when graphify is not installed.
usage: python tests/sync_relabel_twin_gates.py
"""
from __future__ import annotations

import contextlib
import copy
import importlib.util
import inspect
import io
import json
import os
import re
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
_HAS_ENGINE = importlib.util.find_spec("graphify") is not None

OFF = {"DEEPMAP_TYPED_CALLS": "0", "DEEPMAP_CALLS_AUDIT": "0", "DEEPMAP_PRECISE_CALLS": "0",
       "DEEPMAP_CALLS_FAMILY_FILTER": "0", "DEEPMAP_GRAPHIFY_CALLS_PLUS": "0", "DEEPMAP_LOCAL_DEF_CALLS": "0",
       "DEEPMAP_DROP_APP_TO_TEST_CALLS": "0", "DEEPMAP_FALSE_CALL_FILTER": "1", "DEEPMAP_EXCLUDE_TESTS": "0",
       "DEEPMAP_INCREMENTAL_SYNC": "0", "DEEPMAP_FTS_SHORT_NAME": None, "DEEPMAP_AUTO_GITIGNORE": "0"}


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
                print(f"  [FAIL] {label}{(' — ' + str(detail)[:700]) if detail != '' else ''}")
        if not ok:
            self.failed += 1
            self.labels.append(label)
        return bool(ok)


LOUD = Rec()


def tmp() -> Path:
    d = Path(tempfile.mkdtemp(prefix="dm_relabel_"))
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


@contextlib.contextmanager
def clean_env(**kv):
    """Every DEEPMAP_* variable except DEEPMAP_HOME removed, then `kv` applied (None = unset); restored after."""
    saved = {k: v for k, v in os.environ.items() if k.startswith("DEEPMAP_")}
    try:
        for k in list(saved):
            if k != "DEEPMAP_HOME":
                os.environ.pop(k, None)
        for k, v in kv.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = str(v)
        yield
    finally:
        for k in [k for k in os.environ if k.startswith("DEEPMAP_")]:
            os.environ.pop(k, None)
        os.environ.update(saved)


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


def node(key, label, name, sf="src/Types.cs", line=1, summ=None):
    return {"key": key, "label": label, "name": name, "source_file": sf, "start_line": line,
            "body_summary": summ if summ is not None else f"{name}: method"}


# =====================================================================================================
# A model graph that answers exactly the statements the push, the stale prune and sync() make
# =====================================================================================================
_WRITE_WORDS = ("DELETE", "MERGE", "CREATE", "SET ", "REMOVE")
# the census: a key-only scan per label, then a confirming seek over the candidates (the columns after `ls` are the
# shell rule's; the model also answers the earlier two-column seek, so an older sync.py is measured, not crashed)
_SCAN = re.compile(r"MATCH \(n:(\w+)\) WHERE n\.key IS NOT NULL RETURN n\.key AS k\Z")
_CENSUS = re.compile(r"UNWIND \$keys AS twin_key MATCH \(n:(\w+) \{key: twin_key\}\) "
                     r"RETURN n\.key AS k, labels\(n\) AS ls"
                     r"(?:, n\.source_file AS sf, n\.name AS name, n\.start_line AS line)?\Z")
_TWIN_DEL = re.compile(r"UNWIND \$cand AS twin MATCH \(n:(\w+) \{key: twin\.k\}\) "
                       r"WHERE NONE\(l IN labels\(n\) WHERE l IN twin\.ls\)(.*)\Z", re.S)
_SHELL_DEL = re.compile(r"UNWIND \$cand AS twin MATCH \(n:Class \{key: twin\.k\}\) WHERE n\.source_file = twin\.sf "
                        r"AND n\.name = twin\.name AND n\.start_line = twin\.line(.*)\Z", re.S)
_UPSERT = re.compile(r"UNWIND \$rows AS row MERGE \(x:(\w+) \{key:row\.key\}\) SET ")
_CALL_WRITE = re.compile(r"UNWIND \$rows AS row MATCH \(a:(\w+) \{key:row\.a\}\) MATCH \(b:(\w+) \{key:row\.b\}\) "
                         r"MERGE \(a\)-\[r:CALLS\]->\(b\)")
_STALE = "n.key IS NOT NULL AND NOT n.key IN $keys"


class _Res(list):
    def single(self):
        return self[0] if self else None

    def consume(self):
        return None


class Graph:
    """Nodes {id: {"labels": set, **props}}, relationships {id: (src id, type, dst id)}, the meta singleton, and a
    log of every statement (cypher, params) in order."""

    def __init__(self):
        self.nodes: dict = {}
        self.rels: dict = {}
        self.meta: dict = {}
        self.log: list = []
        self._next = 1
        self.fts_rebuilds: list = []         # log index of each deepmap_code_fts re-create

    # ---- building / reading -------------------------------------------------------------------------------
    def add(self, labels, key, **props) -> int:
        i, self._next = self._next, self._next + 1
        self.nodes[i] = dict(props, key=key, labels=set([labels] if isinstance(labels, str) else labels))
        return i

    def rel(self, a: int, typ: str, b: int) -> int:
        i, self._next = self._next, self._next + 1
        self.rels[i] = (a, typ, b)
        return i

    def ids(self, label, key) -> list:
        return [i for i, n in self.nodes.items() if label in n["labels"] and n.get("key") == key]

    def pairs(self) -> set:
        return {(lb, n.get("key")) for n in self.nodes.values() for lb in n["labels"]}

    def by_key(self, key) -> list:
        return [n for n in self.nodes.values() if n.get("key") == key]

    def calls(self) -> set:
        out = set()
        for a, typ, b in self.rels.values():
            if typ == "CALLS":
                out.add((tuple(sorted(self.nodes[a]["labels"])), self.nodes[a]["key"],
                         tuple(sorted(self.nodes[b]["labels"])), self.nodes[b]["key"]))
        return out

    def _del_node(self, i) -> None:
        for r in [r for r, (a, _t, b) in self.rels.items() if a == i or b == i]:
            self.rels.pop(r, None)
        self.nodes.pop(i, None)

    def session(self, *a, **k):
        return _Sess(self)

    def verify_connectivity(self):
        return None

    def close(self):
        return None

    # ---- the statements -----------------------------------------------------------------------------------
    def run(self, cypher, p):
        self.log.append((cypher, p))
        m = _SCAN.match(cypher)
        if m:
            lb = m.group(1)
            return _Res({"k": n["key"]} for n in self.nodes.values() if lb in n["labels"] and n.get("key") is not None)
        m = _CENSUS.match(cypher)
        if m:
            lb, keys = m.group(1), set(p.get("keys") or ())
            return _Res({"k": n["key"], "ls": sorted(n["labels"]), "sf": n.get("source_file"), "name": n.get("name"),
                         "line": n.get("start_line")} for n in self.nodes.values()
                        if lb in n["labels"] and n.get("key") in keys)
        m, ms = _TWIN_DEL.match(cypher), _SHELL_DEL.match(cypher)
        if m or ms:
            if m:
                lb, rest = m.group(1), m.group(2)
                hit = [i for c in (p.get("cand") or ()) for i in self.ids(lb, c["k"])
                       if not self.nodes[i]["labels"] & set(c["ls"])]
            else:
                rest = ms.group(1)
                hit = [i for c in (p.get("cand") or ()) for i in self.ids("Class", c["k"])
                       if (self.nodes[i].get("source_file"), self.nodes[i].get("name"),
                           self.nodes[i].get("start_line")) == (c["sf"], c["name"], c["line"])]
            if re.fullmatch(r" MATCH \(n\)-\[r\]->\(\) CALL \(r\) \{ DELETE r \} IN TRANSACTIONS OF \d+ ROWS", rest):
                for r in [r for r, (a, _t, _b) in self.rels.items() if a in hit]:
                    self.rels.pop(r, None)
            elif re.fullmatch(r" MATCH \(\)-\[r\]->\(n\) CALL \(r\) \{ DELETE r \} IN TRANSACTIONS OF \d+ ROWS", rest):
                for r in [r for r, (_a, _t, b) in self.rels.items() if b in hit]:
                    self.rels.pop(r, None)
            elif re.fullmatch(r" CALL \(n\) \{ DETACH DELETE n \} IN TRANSACTIONS OF \d+ ROWS", rest):
                for i in hit:
                    self._del_node(i)
            else:
                raise AssertionError(f"unexpected twin-delete form: {cypher}")
            return _Res()
        m = _UPSERT.match(cypher)
        if m:
            lb = m.group(1)
            for row in p.get("rows") or ():
                ids = self.ids(lb, row["key"])
                i = ids[0] if ids else self.add(lb, row["key"])
                self.nodes[i].update(name=row["name"], source_file=row["sf"], start_line=row["line"],
                                     body_summary=row["summ"], confidence=row["conf"])
            return _Res()
        if cypher == "MATCH (a)-[r:CALLS]->() WHERE a.key IN $keys DELETE r":
            keys = set(p.get("keys") or ())
            for r in [r for r, (a, t, _b) in self.rels.items() if t == "CALLS" and self.nodes[a].get("key") in keys]:
                self.rels.pop(r, None)
            return _Res()
        m = _CALL_WRITE.match(cypher)
        if m:
            la, lb = m.group(1), m.group(2)
            for row in p.get("rows") or ():
                for a in self.ids(la, row["a"]):
                    for b in self.ids(lb, row["b"]):
                        if not any(x == (a, "CALLS", b) for x in self.rels.values()):
                            self.rel(a, "CALLS", b)
            return _Res()
        if _STALE in cypher:                                   # sync._cleanup_stale (key-only, as it is)
            keys = set(p.get("keys") or ())
            stale = [i for i, n in self.nodes.items() if n.get("key") is not None and n["key"] not in keys]
            if "DETACH DELETE n" in cypher:
                for i in stale:
                    self._del_node(i)
            elif "MATCH (n)-[r]->()" in cypher:
                for r in [r for r, (a, _t, _b) in self.rels.items() if a in stale]:
                    self.rels.pop(r, None)
            elif "MATCH ()-[r]->(n)" in cypher:
                for r in [r for r, (_a, _t, b) in self.rels.items() if b in stale]:
                    self.rels.pop(r, None)
            return _Res()
        if "SET m.fts_rebuild_pending = true" in cypher:
            self.meta["fts_rebuild_pending"] = True
            return _Res()
        if "REMOVE m.fts_rebuild_pending" in cypher:
            self.meta.pop("fts_rebuild_pending", None)
            return _Res()
        if "fts_rebuild_pending AS p" in cypher:
            return _Res([{"p": True}] if self.meta.get("fts_rebuild_pending") else [])
        if "m.owning_root AS owning_root" in cypher:
            return _Res([{"owning_root": self.meta["owning_root"]}] if self.meta.get("owning_root") else [])
        if "MERGE (m:_DeepMapMeta {id:'singleton'}) SET m.owning_root" in cypher:
            self.meta["owning_root"] = p.get("root")
            return _Res()
        if cypher == "MATCH (n) WHERE n.embedding IS NOT NULL RETURN count(n) AS c":
            return _Res([{"c": sum(1 for n in self.nodes.values() if n.get("embedding") is not None)}])
        if cypher == "MATCH (n) WHERE NOT n:_DeepMapMeta RETURN count(n) AS c":
            return _Res([{"c": len(self.nodes)}])
        if cypher.startswith("UNWIND $cand AS twin"):
            raise AssertionError(f"a twin delete the model does not know: {cypher}")
        return _Res()                                          # every other read / write: nothing to model


class _Sess:
    def __init__(self, g):
        self.g = g

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, cypher, **p):
        return self.g.run(cypher, p)

    def begin_transaction(self, *a, **k):
        return self

    def commit(self):
        return None

    def rollback(self):
        return None

    def close(self):
        return None


def _first(log, pred) -> int:
    for i, (c, p) in enumerate(log):
        if pred(c, p):
            return i
    return -1


def _is_census(c, _p=None) -> bool:
    return bool(_SCAN.match(c) or _CENSUS.match(c))


def _is_twin_delete(c, _p=None) -> bool:
    return bool(_TWIN_DEL.match(c) or _SHELL_DEL.match(c))


def _is_write(c, _p=None) -> bool:
    return any(w in c for w in _WRITE_WORDS)


# ---- the 0.8.39 C# shapes: same keys, :Function without the owner step, :Method with it -----------------------------
OLD = [node("src_types", "Module", "Types", line=1, summ="Types: module"),
       node("src_types_user", "Class", "User", line=33, summ="User: class"),
       node("src_types_user_check", "Method", "User.Check", line=35),
       node("src_types_matches", "Function", "Matches", line=5),
       node("src_types_tostring", "Function", "ToString", line=10),
       node("src_types_area", "Function", "Area", line=25)]
NEW = [node("src_types", "Module", "Types", line=1, summ="Types: module"),
       node("src_types_user", "Class", "User", line=33, summ="User: class"),
       node("src_types_user_check", "Method", "User.Check", line=35),
       node("src_types@SearchResult:L3", "Class", "SearchResult", line=3, summ="SearchResult: class"),
       node("src_types@Dim:L16", "Class", "Dim", line=16, summ="Dim: class"),
       node("src_types_matches", "Method", "SearchResult.Matches", line=5),
       node("src_types_tostring", "Method", "SearchResult.ToString", line=10),
       node("src_types@Dim.ToString:L20", "Method", "Dim.ToString", line=20),
       node("src_types_area", "Method", "Dim.Area", line=25)]
TWIN_KEYS = ["src_types_area", "src_types_matches", "src_types_tostring"]
CALLS = [("src_types_user_check", "Matches")]
# the class shell: the 0.8.39 csharp_edges record shell of the owner NEW pushes as src_types@SearchResult:L3
SHELL = node("src/Types.cs::SearchResult:3", "Class", "SearchResult", line=3, summ="SearchResult: C# record")
# stale nodes that are NOT duplicates of a class NEW pushes: each must stay (the prune's business)
NOT_SHELLS = [
    node("src/Types.cs::SearchResult:40", "Class", "SearchResult", line=40),       # another partial part, another line
    node("src/Other.cs::SearchResult:3", "Class", "SearchResult", sf="src/Other.cs", line=3),   # another file
    node("src/Types.cs::Dimension:16", "Class", "Dimension", line=16),             # another name on Dim's line
    node("src/Types.cs::SearchResult.fn:3", "Function", "SearchResult", line=3),   # not a :Class
    node("src/Types.cs::Dim.Area:25", "Class", "Dim.Area", line=25),               # the triple of a pushed :Method
    node("src/types.cs::Dim:16", "Class", "Dim", sf="src/types.cs", line=16),      # another letter case of the file
    node("src/Types.cs::Dim:none", "Class", "Dim", line=None),                     # no line
    node("src/Types.cs::Dim:zero", "Class", "Dim", line=0),                        # line 0
]
NOT_SHELL_KEYS = [n["key"] for n in NOT_SHELLS]


def graph_of(nodes, calls=(), *, embedded=True) -> Graph:
    """A model graph holding `nodes` (each embedded) and `calls` resolved by bare name."""
    g = Graph()
    ids = {}
    for n in nodes:
        ids[(n["label"], n["key"])] = g.add(n["label"], n["key"], name=n["name"], source_file=n["source_file"],
                                            start_line=n["start_line"], body_summary=n["body_summary"],
                                            **({"embedding": [0.5, 0.25], "_embed_hash": "h-" + n["key"]}
                                               if embedded else {}))
    for a, callee in calls:
        src = [i for (lb, k), i in ids.items() if k == a]
        dst = [i for (lb, k), i in ids.items()
               if g.nodes[i]["name"] == callee or g.nodes[i]["name"].endswith("." + callee)]
        for x in src:
            for y in dst:
                g.rel(x, "CALLS", y)
    return g


def labels_of(nodes) -> dict:
    out: dict = {}
    for n in nodes:
        ls = out.setdefault(n["key"], [])
        if n["label"] not in ls:
            ls.append(n["label"])
    return out


def push(Sm, g, nodes, calls=(), *, env=None):
    with flags(**dict(OFF, **(env or {}))):
        return Sm.push_to_neo4j(g, copy.deepcopy(nodes), list(calls), [], None)


# =====================================================================================================
# A — the census and the delete
# =====================================================================================================
def a_units(Sm, rec):
    fn, dl = getattr(Sm, "_relabel_twins", None), getattr(Sm, "_delete_relabel_twins", None)
    if not rec.check("A: sync._relabel_twins and sync._delete_relabel_twins exist", callable(fn) and callable(dl)):
        return
    g = graph_of(OLD)
    got = fn(g, labels_of(NEW))
    rec.check("A1: three :Function nodes whose keys this extraction gives :Method -> {Function: those keys}",
              {lb: sorted(ks) for lb, ks in got.items()} == {"Function": TWIN_KEYS}, got)
    rec.check("A2: a node under its live label (User, User.Check, the module) is never a twin",
              not any(k in ("src_types", "src_types_user", "src_types_user_check") for ks in got.values() for k in ks),
              got)
    g = graph_of(OLD + [node("src_gone", "Function", "Gone", line=99)])
    got = fn(g, labels_of(NEW))
    rec.check("A3: a stale key (not in this extraction) is the prune's business, never a twin",
              not any("src_gone" in ks for ks in got.values()), got)
    g = Graph()
    g.add({"Function", "Method"}, "src_types_matches", name="x")
    g.add("Method", "src_types_tostring", name="y")
    got = fn(g, labels_of(NEW))
    rec.check("A4: a node that also carries its key's live label (:Function:Method) is not a twin", got == {}, got)
    both = NEW + [node("src_types_matches", "Function", "Matches", line=5)]
    got = fn(graph_of(OLD), labels_of(both))
    rec.check("A5: a key this extraction itself gives two labels: neither existing node is a twin",
              not any("src_types_matches" in ks for ks in got.values()), got)
    g = graph_of(NEW)
    g.add("Route", "src_types_area", name="GET /area")
    got = fn(g, labels_of(NEW))
    rec.check("A6: a twin under a label this extraction writes for NO key (Route, a node-key index label) is found",
              got == {"Route": ["src_types_area"]}, got)
    g = graph_of(OLD)
    saved = Sm._TWIN_READ_BATCH
    try:
        Sm._TWIN_READ_BATCH = 2
        fn(g, labels_of(NEW))
    finally:
        Sm._TWIN_READ_BATCH = saved
    stmts = [(c, p) for c, p in g.log]
    census = [(c, p) for c, p in stmts if _is_census(c)]
    rec.check("A7: the census only READS (no statement writes) and every statement names its label, never a "
              "label-less MATCH (n)",
              census and len(census) == len(stmts) and not any(_is_write(c) for c, _p in stmts)
              and not any("MATCH (n)" in c or "MATCH (n {" in c for c, _p in stmts),
              [c for c, _p in stmts][:6])
    scans = [_SCAN.match(c).group(1) for c, _p in census if _SCAN.match(c)]
    per = {}
    for c, p in census:
        if _CENSUS.match(c):
            per.setdefault(_CENSUS.match(c).group(1), []).append(len(p.get("keys") or ()))
    want_labels = list(getattr(Sm.indexes, "KEY_INDEX_LABELS", None) or Sm._TWIN_LABELS_FALLBACK)
    rec.check("A7b: exactly ONE key-only scan per node-key index label (KEY_INDEX_LABELS), sending no key",
              sorted(scans) == sorted(want_labels) and len(scans) == len(set(scans))
              and all(not p.get("keys") for c, p in census if _SCAN.match(c)), scans)
    rec.check("A7c: a confirming seek ONLY over the candidates (the three :Function keys this extraction gives "
              ":Method), batched (_TWIN_READ_BATCH=2: no statement holds more than 2 keys); no seek under a label "
              "with no candidate", set(per) == {"Function"} and max(per["Function"]) <= 2
              and sum(per["Function"]) == len(TWIN_KEYS), per)
    tl = Sm._twin_labels({"k1": ["Function"], "k2": ["Weird Label"], "k3": ["X`) DETACH DELETE (y"],
                          "k4": ["Extra"]})
    rec.check("A8: _twin_labels = the node-key index labels, then this extraction's other labels; a label that is "
              "not an identifier is never interpolated", tl[:len(want_labels)] == want_labels and "Extra" in tl
              and not any(" " in x or "`" in x for x in tl), tl)
    try:
        import indexes as IX
        import language_recognizers as LR
        key_labels, valid = list(IX.KEY_INDEX_LABELS), set(LR.VALID_NODE_LABELS)
    except Exception as exc:  # noqa: BLE001
        key_labels, valid = repr(exc), set()
    rec.check("A8b: (drift lock) sync._TWIN_LABELS_FALLBACK == indexes.KEY_INDEX_LABELS, and every label DeepMap "
              "writes (language_recognizers.VALID_NODE_LABELS, map_graph's Module / Class / Method / Function) is "
              "one of them, so a twin under any of them is always read",
              list(Sm._TWIN_LABELS_FALLBACK) == key_labels and isinstance(key_labels, list)
              and (valid | {"Module", "Class", "Method", "Function"}) <= set(key_labels),
              (Sm._TWIN_LABELS_FALLBACK, key_labels, sorted(valid)))
    # the delete
    g = graph_of(OLD, CALLS)
    lo = labels_of(NEW)
    twins = fn(g, lo)
    g.log.clear()
    n = dl(g, twins, lo)
    stmts = [c for c, _p in g.log]
    kinds = [("out" if "MATCH (n)-[r]->()" in c else "in" if "MATCH ()-[r]->(n)" in c else
              "node" if "DETACH DELETE n" in c else "?") for c in stmts]
    rec.check("A9: the delete returns the twins it was handed (3) and leaves none behind",
              n == 3 and not any(("Function", k) in g.pairs() for k in TWIN_KEYS), (n, sorted(g.pairs())))
    rec.check("A9b: per label: relationships out, then in, then the nodes; each batched IN TRANSACTIONS and matched "
              "by label + key with the live-label guard",
              kinds == ["out", "in", "node"] and all(_is_twin_delete(c) and "IN TRANSACTIONS OF" in c for c in stmts),
              stmts)
    rec.check("A9c: ... and the twins' relationships are gone with them (no dangling CALLS)",
              all(a in g.nodes and b in g.nodes for a, _t, b in g.rels.values()), g.rels)
    g = Graph()
    g.add({"Function", "Method"}, "src_types_matches", name="x")
    dl(g, {"Function": ["src_types_matches"]}, lo)
    rec.check("A10: the delete itself re-checks the live labels: a :Function:Method node handed to it stays",
              ("Method", "src_types_matches") in g.pairs(), sorted(g.pairs()))
    g = Graph()
    dl(g, {"Bad Label": ["k"]}, {"k": ["Function"]})
    rec.check("A11: an unsafe label handed to the delete is never interpolated (no statement)", not g.log,
              [c for c, _p in g.log])
    a_shells(Sm, rec)


def _classes(Sm, nodes) -> dict:
    fn = getattr(Sm, "_class_triples", None)
    return fn(nodes) if callable(fn) else {}


def _census(Sm, g, nodes):
    """sync._relabel_twins over `g` for an extraction of `nodes`, with the class triples when this sync.py has them."""
    fn = Sm._relabel_twins
    if len(inspect.signature(fn).parameters) < 3:        # an older two-argument census (measured, not crashed)
        return fn(g, labels_of(nodes))
    return fn(g, labels_of(nodes), _classes(Sm, nodes))


def _shell_keys(got) -> list:
    return sorted(t.get("k") for t in (getattr(got, "shells", None) or ()) if isinstance(t, dict))


def a_shells(Sm, rec):
    """A12-A15: the class shell rule (a stale :Class with the file, name and line of a class this push writes)."""
    g = graph_of(NEW + [SHELL] + NOT_SHELLS)
    got = _census(Sm, g, NEW)
    rec.check("A12: a stale :Class with the file, name and start line of a pushed :Class (the 0.8.39 record shell of "
              "src_types@SearchResult:L3) is a shell twin", _shell_keys(got) == [SHELL["key"]],
              (_shell_keys(got), dict(got)))
    rec.check("A12b: ... and only it: another partial part (another line), a same-named class of another file, another "
              "name on a pushed class's line, a :Function, the triple of a pushed :Method, another letter case of the "
              "file, no line and line 0 are never shells, and none of them is a relabel twin either",
              not set(NOT_SHELL_KEYS) & set(_shell_keys(got)) and not any(k in NOT_SHELL_KEYS for ks in got.values()
                                                                          for k in ks), (_shell_keys(got), dict(got)))
    sh = [t for t in (getattr(got, "shells", None) or ()) if isinstance(t, dict)]
    rec.check("A12c: a shell carries its file, name and line for the delete's re-check",
              sh == [{"k": SHELL["key"], "sf": "src/Types.cs", "name": "SearchResult", "line": 3}], sh)
    odd = NEW + [node("src_types@Dim:none", "Class", "Dim", line=None), node("src_types@Dim:zero", "Class", "Dim",
                                                                             line=0)]
    got = _census(Sm, graph_of(NEW + NOT_SHELLS), odd)
    rec.check("A12d: a pushed :Class with no line (or line 0) never matches a stale one with no line (or line 0)",
              _shell_keys(got) == [], _shell_keys(got))
    fn_only = [n for n in NEW if n["label"] != "Class"]
    g = graph_of(OLD + [SHELL] + NOT_SHELLS)
    got = _census(Sm, g, fn_only)
    seeks = [c for c, p in g.log if _CENSUS.match(c) and _CENSUS.match(c).group(1) == "Class"]
    rec.check("A13: a push that writes no :Class finds no shell and sends no stale :Class key at all", _shell_keys(got)
              == [] and not seeks, (_shell_keys(got), seeks[:2]))
    g = graph_of(NEW + [SHELL] + NOT_SHELLS)
    got = _census(Sm, g, NEW + [SHELL])
    rec.check("A13b: a class the extraction itself still writes (its key is live) is never a shell, even with a "
              "same-triple class beside it", _shell_keys(got) == [], _shell_keys(got))
    # the delete
    dl = Sm._delete_relabel_twins
    g = graph_of(NEW + [SHELL] + NOT_SHELLS)
    s_id = g.ids("Class", SHELL["key"])[0]
    caller = g.ids("Method", "src_types_user_check")[0]
    g.rel(caller, "CALLS", s_id)
    g.rel(s_id, "INHERITS", g.ids("Class", "src_types_user")[0])
    got = _census(Sm, g, NEW)
    g.log.clear()
    n = dl(g, got, labels_of(NEW))
    stmts = [c for c, _p in g.log]
    kinds = [("out" if "MATCH (n)-[r]->()" in c else "in" if "MATCH ()-[r]->(n)" in c else
              "node" if "DETACH DELETE n" in c else "?") for c in stmts]
    rec.check("A14: the delete removes the shell with its relationships (in and out) and counts it",
              n == 1 and not g.ids("Class", SHELL["key"]) and all(a in g.nodes and b in g.nodes
                                                                    for a, _t, b in g.rels.values()),
              (n, [k for k in (SHELL["key"],) if g.ids("Class", k)], g.rels))
    rec.check("A14b: ... relationships out, then in, then the node, each batched IN TRANSACTIONS and matched by "
              ":Class, key AND the same file, name and start line", kinds == ["out", "in", "node"]
              and all(_SHELL_DEL.match(c) and "IN TRANSACTIONS OF" in c for c in stmts), stmts)
    rec.check("A14c: ... and every must-not stays", all(g.by_key(k) for k in NOT_SHELL_KEYS),
              [k for k in NOT_SHELL_KEYS if not g.by_key(k)])
    g = graph_of(NEW + [SHELL])
    twins = Sm._Twins() if hasattr(Sm, "_Twins") else {}
    if hasattr(twins, "shells"):
        twins.shells = [{"k": SHELL["key"], "sf": "src/Types.cs", "name": "SearchResult", "line": 4}]
    dl(g, twins, labels_of(NEW))
    rec.check("A14d: the delete itself re-checks file, name and line: a candidate whose line no longer matches stays",
              hasattr(Sm, "_Twins") and bool(g.ids("Class", SHELL["key"])), [c for c, _p in g.log])
    # A15: nothing to find -> nothing sent
    big = [node(f"src/m{i}.cs::F{i}:{i + 1}", ("Function", "Method", "Class", "Module")[i % 4], f"F{i}",
                sf=f"src/m{i // 7}.cs", line=i + 1) for i in range(600)]
    g = graph_of(big)
    _census(Sm, g, big)
    sent = sum(len(p.get("keys") or ()) for c, p in g.log)
    rec.check("A15: a 600-node graph equal to its extraction: the census sends no key (one scan per label, no seek)",
              sent == 0 and all(_SCAN.match(c) for c, _p in g.log), (sent, len(g.log)))


# =====================================================================================================
# B — push_to_neo4j
# =====================================================================================================
def b_push(Sm, rec):
    g = graph_of(OLD, CALLS)
    before = len(g.nodes)
    res = push(Sm, g, NEW, CALLS)
    rec.check("B1: members=0 -> default (0.8.39 shapes): the graph holds exactly the pushed (label, key) pairs, "
              "no :Function twin", g.pairs() == {(n["label"], n["key"]) for n in NEW},
              {"extra": sorted(g.pairs() - {(n["label"], n["key"]) for n in NEW})})
    rec.check("B1b: ... one node per key (it held %d nodes, three of them twins)" % before,
              all(len(g.by_key(n["key"])) == 1 for n in NEW), [(k, len(g.by_key(k))) for k in TWIN_KEYS])
    rec.check("B1c: ... the caller's CALLS edge now reaches the :Method node only",
              g.calls() == {(("Method",), "src_types_user_check", ("Method",), "src_types_matches")}, g.calls())
    rec.check("B1d: ... and no embedding was carried over to the surviving :Method nodes (the normal path embeds "
              "them: generate_embeddings picks a node without one)",
              all(g.by_key(k)[0].get("embedding") is None and g.by_key(k)[0].get("_embed_hash") is None
                  for k in TWIN_KEYS), [g.by_key(k)[0] for k in TWIN_KEYS])
    rec.check("B2: the result is still the plain pair (nodes pushed, CALLS edges) and a tuple",
              res == (len(NEW), 1) and isinstance(res, tuple) and tuple(res) == (len(NEW), 1), res)
    rec.check("B2b: ... and `.relabeled` counts the twins removed (3)", getattr(res, "relabeled", None) == 3,
              getattr(res, "relabeled", None))
    log = g.log
    first_write = _first(log, _is_write)
    last_census = max([i for i, (c, _p) in enumerate(log) if _is_census(c)] or [-1])
    stamp = _first(log, lambda c, p: "SET m.fts_rebuild_pending = true" in c)
    first_del = _first(log, _is_twin_delete)
    upsert = _first(log, lambda c, p: bool(_UPSERT.match(c)))
    rec.check("B3: order: every census read before the first write; the full-text pending stamp before the first "
              "twin delete; the deletes before the node upsert",
              0 <= last_census < first_write and first_write == stamp and stamp < first_del < upsert,
              (last_census, first_write, stamp, first_del, upsert))
    rec.check("B3b: ... and the stamp is still set when the push returns (sync() re-creates the index, then clears "
              "it)", g.meta.get("fts_rebuild_pending") is True, g.meta)
    # the reverse direction
    g = graph_of(NEW, CALLS)
    res = push(Sm, g, OLD, CALLS)
    rec.check("B4: default -> DEEPMAP_CSHARP_MEMBERS=0 (Method -> Function): no :Method twin, and the two synthetic "
              "owners are left to the stale prune", g.pairs() == {(n["label"], n["key"]) for n in OLD}
              | {("Class", "src_types@SearchResult:L3"), ("Class", "src_types@Dim:L16"),
                 ("Method", "src_types@Dim.ToString:L20")} and getattr(res, "relabeled", None) == 3,
              (sorted(g.pairs()), getattr(res, "relabeled", None)))
    # no twin
    g = graph_of(NEW, CALLS)
    res = push(Sm, g, NEW, CALLS)
    rec.check("B5: no twin -> no pending stamp, no twin delete, `.relabeled` == 0",
              getattr(res, "relabeled", None) == 0 and _first(g.log, _is_twin_delete) < 0
              and _first(g.log, lambda c, p: "fts_rebuild_pending" in c) < 0 and "fts_rebuild_pending" not in g.meta,
              [c for c, _p in g.log if _is_twin_delete(c) or "pending" in c])
    g = graph_of(OLD, CALLS)
    push(Sm, g, NEW, CALLS)
    g.log.clear()
    res = push(Sm, g, NEW, CALLS)
    rec.check("B6: a second push of the same extraction removes nothing more", getattr(res, "relabeled", None) == 0
              and _first(g.log, _is_twin_delete) < 0, getattr(res, "relabeled", None))
    g = graph_of(OLD + [node("src_gone", "Function", "Gone", line=99)], CALLS)
    push(Sm, g, NEW, CALLS)
    rec.check("B7: a stale key stays (the twin step never prunes: that is the stale prune's decision)",
              ("Function", "src_gone") in g.pairs(), sorted(g.pairs()))
    g = Graph()
    res = push(Sm, g, [], [])
    rec.check("B8: an empty push still returns the old (0, 0) and reads nothing", res == (0, 0) and not g.log,
              (res, g.log))
    b_shells(Sm, rec)


def b_shells(Sm, rec):
    """B9/B10: the class shell, in a push."""
    g = graph_of(OLD + [SHELL] + NOT_SHELLS, CALLS)
    s_id = g.ids("Class", SHELL["key"])[0]
    g.rel(g.ids("Method", "src_types_user_check")[0], "CALLS", s_id)
    res = push(Sm, g, NEW, CALLS)
    rec.check("B9: members=0 -> default with the recognizer's record shell: the shell is gone with its edges, the "
              "owner stays", not g.ids("Class", SHELL["key"]) and g.ids("Class", "src_types@SearchResult:L3")
              and all(a in g.nodes and b in g.nodes for a, _t, b in g.rels.values()),
              sorted(k for _lb, k in g.pairs() if "SearchResult" in k))
    rec.check("B9b: ... every must-not stays (another part, another file, another name, a :Function, a pushed "
              ":Method's triple, another letter case, no line, line 0)", all(g.by_key(k) for k in NOT_SHELL_KEYS),
              [k for k in NOT_SHELL_KEYS if not g.by_key(k)])
    rec.check("B9c: ... `.relabeled` counts the three relabel twins and the shell (4), `.shells` the shell (1), "
              "`.moves` the label move it saw ((Function -> Method) x3)",
              getattr(res, "relabeled", None) == 4 and getattr(res, "shells", None) == 1
              and tuple(getattr(res, "moves", ()) or ()) == (("Function", ("Method",), 3),),
              (getattr(res, "relabeled", None), getattr(res, "shells", None), getattr(res, "moves", None)))
    g.log.clear()
    res = push(Sm, g, NEW, CALLS)
    rec.check("B9d: a second push finds no shell and deletes nothing", getattr(res, "relabeled", None) == 0
              and getattr(res, "shells", None) == 0 and _first(g.log, _is_twin_delete) < 0,
              (getattr(res, "relabeled", None), getattr(res, "shells", None)))
    g = graph_of(NEW, CALLS)
    res = push(Sm, g, OLD + [SHELL], CALLS)
    rec.check("B10: default -> members=0 (the recognizer writes the shell again): the OWNER is the stale duplicate "
              "now and goes; the shell stays; the other synthetic owner (Dim, no shell) is left to the prune",
              g.by_key(SHELL["key"]) and not g.by_key("src_types@SearchResult:L3")
              and g.ids("Class", "src_types@Dim:L16") and getattr(res, "shells", None) == 1
              and getattr(res, "relabeled", None) == 4
              and tuple(getattr(res, "moves", ()) or ()) == (("Method", ("Function",), 3),),
              (sorted(g.pairs()), getattr(res, "relabeled", None), getattr(res, "shells", None),
               getattr(res, "moves", None)))


# =====================================================================================================
# C — sync() end to end, the real push over the model graph
# =====================================================================================================
_TREE = {".deepmap-root": "", "src/Types.cs": "namespace Lib { public class User { } }\n"}
STALE = node("src_gone", "Function", "Gone", sf="src/Gone.cs", line=3)


def run_sync(Sm, g, nodes, *, plan=None, evidence=None, cleanup=False, policy=None):
    """sync() on a temp root whose extraction is `nodes` (+CALLS), the database the model graph `g`, owned by this
    project, built by graphify. Returns (ok, stderr, root)."""
    root = write_tree(tmp() / "p", _TREE)
    (root / ".deepmap").mkdir(exist_ok=True)
    (root / ".deepmap" / "graph_build.json").write_bytes(json.dumps(
        {"graph_engines": ["graphify"], "extractor": "graphify", "nodes": len(g.nodes)}).encode("utf-8"))
    g.meta["owning_root"] = Sm._project_identity(root)
    Sm._rebuild_code = lambda r: Sm._Extraction(copy.deepcopy(nodes), list(CALLS), [], "graphify",
                                                {"requested": "auto", "resolved": "graphify", "fallback_reason": None})
    Sm._apply_recognizers = lambda r, ns: (ns, [])
    Sm._graph_has_nodes = lambda d: bool(g.nodes)
    Sm._graph_holds_nodes = lambda d: bool(g.nodes)
    Sm._record_base_store_owner = lambda *a, **k: None
    Sm._reuse_rekeyed_embeddings = lambda d, ns: 0
    Sm._fts_changed_docs = lambda d, ns: 0
    Sm._remove_stale_short_names = lambda d: 0
    Sm._unkey_receiver_calls = lambda r, n, c: (c, 0)
    Sm._attach_bare_calls = lambda r, n, c: 0
    Sm._attach_local_defs = lambda r, n, c: 0
    Sm._attach_local_def_calls = lambda r, n, c: 0
    Sm._maybe_auto_tune = lambda r: None
    if plan is not None:
        Sm._plan_prune = lambda *a, **k: dict(plan)
    if evidence is not None:
        Sm._plan_prune_evidence = lambda *a, **k: dict(evidence)

    def rebuild_fts(d, name="deepmap_code_fts"):
        g.fts_rebuilds.append(len(g.log))
        g.log.append(("REBUILD FTS", {}))
        return "rebuilt"
    real_idx = Sm.indexes
    Sm.indexes = types.SimpleNamespace(
        create_all_indexes=lambda d: {}, wait_for_indexes=lambda d, timeout=120: True, VECTOR_INDEXES=(),
        vector_quantization_disabled=lambda: False, rebuild_fts_index=rebuild_fts,
        KEY_INDEX_LABELS=list(getattr(real_idx, "KEY_INDEX_LABELS", None) or Sm._TWIN_LABELS_FALLBACK),
        FTS_INDEXES=getattr(real_idx, "FTS_INDEXES", ()), CODE_FTS_INDEX="deepmap_code_fts")
    Sm.embeddings = types.SimpleNamespace(generate_embeddings=lambda d: 0)
    err = io.StringIO()
    env = dict(OFF, DEEPMAP_EXTRACTOR="graphify", DEEPMAP_HOME=str(tmp() / "home"), NEO4J_URI="bolt://127.0.0.1:9")
    try:
        with flags(**env), contextlib.redirect_stderr(err), contextlib.redirect_stdout(err):
            ok = Sm.sync(root, driver=g, cleanup_stale=cleanup, prune_policy=policy)
    finally:
        Sm.indexes = real_idx
    return ok, err.getvalue(), root


def _read_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def c_sync(Sm, rec, only=None, extras=True):
    """C1-C4 (`only`: the case tags to run, e.g. {"C3"}), then C9 and C6-C8 unless `extras` is False."""
    live = {(n["label"], n["key"]) for n in NEW}
    skip = {"live": {n["key"] for n in NEW}, "decision": "skip", "reason": "blast-radius", "would_delete": 1,
            "existing_total": 2, "suspicious": 1, "exempt": None}
    cases = [
        ("C1: a full refresh whose >50% guard SKIPS the prune", dict(plan=skip, cleanup=True)),
        ("C2: the background refresh's evidence-only pass (nothing proven stale)",
         dict(evidence=dict(skip, decision="skip", reason="evidence:unextracted-files", policy="evidence"),
              policy="evidence")),
        ("C3: a plain sync with no stale-cleanup at all", dict()),
    ]
    for label, kw in cases:
        if only is not None and label.split(":")[0] not in only:
            continue
        g = graph_of(OLD + [STALE], CALLS)
        ok, err, root = run_sync(Sm, g, NEW, **kw)
        rec.check(f"{label}: the sync succeeds and the twins are gone (only live pairs + the kept stale node)",
                  ok is True and g.pairs() == live | {("Function", "src_gone")},
                  (ok, sorted(g.pairs() - live), err[-600:]))
        push_at = _first(g.log, lambda c, p: bool(_UPSERT.match(c)))
        stamp = _first(g.log, lambda c, p: "SET m.fts_rebuild_pending = true" in c)
        cleared = [i for i, (c, _p) in enumerate(g.log) if "REMOVE m.fts_rebuild_pending" in c]
        rec.check(f"{label}: deepmap_code_fts is re-created AFTER the push and the pending stamp cleared after that",
                  0 <= stamp < push_at < (g.fts_rebuilds or [-1])[0] < max(cleared or [-1])
                  and "fts_rebuild_pending" not in g.meta, (stamp, push_at, g.fts_rebuilds, cleared, g.meta))
        rec.check(f"{label}: the log names the removal and the full-text reason",
                  "removed 3 node(s) the graph held under a label this extraction no longer gives their key" in err
                  and "3 node(s) removed under an old label" in err, err[-800:])
        rec.check(f"{label}: ... and the label move it saw, never a fixed example",
                  "their key (3 from :Function to :Method); each duplicated a node this push wrote" in err
                  and "C# record or struct method" not in err and "under an old key" not in err, err[-800:])
        last = _read_json(root / ".deepmap" / "sync_last.json") or {}
        rec.check(f"{label}: sync_last.json records relabeled=3", last.get("relabeled") == 3,
                  {k: last.get(k) for k in ("outcome", "relabeled", "pruned", "stale_kept")})
        man = _read_json(root / ".deepmap" / "graph_build.json") or {}
        rec.check(f"{label}: the manifest's embedded total no longer counts the twins' embeddings (3 live nodes + "
                  f"the kept stale one = 4, not 7)", man.get("embedded") == 4,
                  {k: man.get(k) for k in ("nodes", "embedded", "graph_nodes")})
        if "plan" in kw:
            rec.check(f"{label}: ... and the graph total the verdict reports is the live nodes + the kept stale one "
                      f"(no twin)", man.get("graph_nodes") == len(NEW) + 1, man.get("graph_nodes"))
    # no twin: nothing re-created, nothing recorded
    if only is None or "C4" in only:
        g = graph_of(NEW, CALLS)
        ok, err, root = run_sync(Sm, g, NEW)
        last = _read_json(root / ".deepmap" / "sync_last.json") or {}
        rec.check("C4: a pass with no twin: no full-text re-create, no removal line, no `relabeled` in sync_last.json",
                  ok is True and not g.fts_rebuilds and "under a label this extraction" not in err
                  and "relabeled" not in last and last.get("outcome") == "synced", (g.fts_rebuilds, last, err[-400:]))
    if extras:
        c_neutral(Sm, rec)
        c_shells(Sm, rec)


def c_neutral(Sm, rec, only=None):
    """C9: the removal line names the moves it saw, whichever way they go (the line used to say 'a C# record or
    struct method that is now a :Method, not a :Function' on every engine and label pair)."""
    if only is None or "C9" in only:
        g = graph_of(NEW, CALLS)
        ok, err, _root = run_sync(Sm, g, OLD)
        rec.check("C9: default -> DEEPMAP_CSHARP_MEMBERS=0 (Method -> Function): the line says '3 from :Method to "
                  ":Function', never the fixed C# example", ok is True
                  and "no longer gives their key (3 from :Method to :Function); each duplicated" in err
                  and "C# record or struct method" not in err, err[-800:])
    if only is None or "C9b" in only:
        g = Graph()
        for lb, k in (("Class", "a.cs::A"), ("Class", "b.cs::B"), ("Function", "c.cs::c")):
            g.add(lb, k, name=k, source_file=k.split("::")[0], start_line=1, body_summary=k)
        two = [node("a.cs::A", "Module", "A", sf="a.cs"), node("b.cs::B", "Module", "B", sf="b.cs"),
               node("c.cs::c", "Method", "c", sf="c.cs")]
        ok, err, _root = run_sync(Sm, g, two)
        rec.check("C9b: two different moves are both named, in a stable order ('2 from :Class to :Module, 1 from "
                  ":Function to :Method', sorted by the old label)", ok is True
                  and "their key (2 from :Class to :Module, 1 from :Function to :Method); each" in err, err[-800:])
    if only is None or "C9c" in only:
        # the moves on the result are re-checked: a result whose moves do not add up names no labels rather than guess
        class _R(tuple):
            relabeled, shells, moves = 3, 0, (("Function", ("Method",), 2),)
        saved = Sm.push_to_neo4j
        Sm.push_to_neo4j = lambda d, n, c, i, t=None: _R((len(n), 0))
        try:
            ok, err, _root = run_sync(Sm, graph_of(NEW, CALLS), NEW)
        finally:
            Sm.push_to_neo4j = saved
        rec.check("C9c: moves that do not account for the count -> the line names no labels (and no fixed example)",
                  ok is True and "no longer gives their key; each duplicated" in err and "C# record" not in err,
                  err[-600:])


def c_shells(Sm, rec, only=None):
    """C6-C8: the class shell through sync(): reported, re-indexed, recorded, and never counted again by the prune."""
    live_keys = {n["key"] for n in NEW}
    if only is None or "C6" in only:
        # C6: a full refresh that prunes: the shell and the stale node were both in the plan; the push took the shell
        plan = {"live": live_keys, "decision": "prune", "reason": None, "would_delete": 2, "existing_total": 12,
                "suspicious": None, "exempt": None}
        g = graph_of(OLD + [SHELL, STALE], CALLS)
        ok, err, root = run_sync(Sm, g, NEW, plan=plan, cleanup=True)
        last = _read_json(root / ".deepmap" / "sync_last.json") or {}
        rec.check("C6: a pruning refresh with a record shell: the shell and the stale node are gone, the live nodes "
                  "stay", ok is True and g.pairs() == {(n["label"], n["key"]) for n in NEW},
                  (ok, sorted(g.pairs()), err[-500:]))
        rec.check("C6b: ... the log has the shell's own line, the full-text reason names it, and the relabel line "
                  "counts only the relabel twins", "removed 1 :Class node(s) under an old key: each had the file, name "
                  "and start line of a class this push wrote under another key" in err
                  and "1 class node(s) removed under an old key" in err
                  and "removed 3 node(s) the graph held under a label" in err
                  and "3 node(s) removed under an old label" in err and "1 pruned node(s)" in err, err[-900:])
        rec.check("C6c: ... sync_last.json: relabeled 4 (3 relabel twins + the shell), pruned 1 (the stale node only: "
                  "the shell is not counted twice)", last.get("relabeled") == 4 and last.get("pruned") == 1,
                  {k: last.get(k) for k in ("relabeled", "pruned", "stale_kept")})
    if only is None or "C7" in only:
        # C7: the background refresh keeps what it cannot prove; the shell is no longer among the kept
        ev = {"live": live_keys, "decision": "skip", "reason": "evidence:unextracted-files", "would_delete": 2,
              "existing_total": 12, "suspicious": 1, "exempt": {"re-extracted": 1, "deleted": 0, "excluded": 0},
              "policy": "evidence"}
        g = graph_of(OLD + [SHELL, STALE], CALLS)
        ok, err, root = run_sync(Sm, g, NEW, evidence=ev, policy="evidence")
        last = _read_json(root / ".deepmap" / "sync_last.json") or {}
        rec.check("C7: the evidence-only refresh: the shell is removed, the unproven stale node kept and reported as "
                  "the ONE kept node (stale_kept 1, 'kept 1 stale node(s)')", ok is True and not g.by_key(SHELL["key"])
                  and g.by_key(STALE["key"]) and last.get("stale_kept") == 1 and "kept 1 stale node(s)" in err
                  and last.get("relabeled") == 4, ({k: last.get(k) for k in ("relabeled", "pruned", "stale_kept")},
                                                   err[-600:]))
    if only is None or "C8" in only:
        # C8: a blast-radius skip whose only stale node was the shell: nothing is left to skip
        skip = {"live": live_keys, "decision": "skip", "reason": "blast-radius", "would_delete": 1, "existing_total": 2,
                "suspicious": None, "exempt": None}
        g = graph_of(OLD + [SHELL], CALLS)
        ok, err, root = run_sync(Sm, g, NEW, plan=skip, cleanup=True)
        last = _read_json(root / ".deepmap" / "sync_last.json") or {}
        rec.check("C8: a blast-radius skip whose one stale node was the shell the push removed: no 'stale-cleanup "
                  "SKIPPED' line, no STALE NODES KEPT, nothing recorded as skipped", ok is True
                  and "stale-cleanup SKIPPED" not in err and "STALE NODES KEPT" not in err
                  and not last.get("stale_cleanup_skipped") and not last.get("stale_kept")
                  and g.pairs() == {(n["label"], n["key"]) for n in NEW},
                  ({k: last.get(k) for k in ("stale_cleanup_skipped", "stale_kept", "relabeled")}, err[-600:]))
    if only is None or "C8b" in only:
        # C8b: the same skip with a real stale node beside the shell still skips, now counting the stale node only
        skip2 = {"live": live_keys, "decision": "skip", "reason": "blast-radius", "would_delete": 2,
                 "existing_total": 3, "suspicious": None, "exempt": None}
        g = graph_of(OLD + [SHELL, STALE], CALLS)
        ok, err, root = run_sync(Sm, g, NEW, plan=skip2, cleanup=True)
        last = _read_json(root / ".deepmap" / "sync_last.json") or {}
        rec.check("C8b: ... with one more (real) stale node the skip stands and names 1, not 2", ok is True
                  and "DETACH-DELETE 1/2 keyed nodes" in err and last.get("stale_kept") == 1
                  and last.get("stale_cleanup_skipped") == "blast-radius", ({k: last.get(k) for k in (
                      "stale_cleanup_skipped", "stale_kept")}, err[-600:]))


# =====================================================================================================
# D — the installed engine
# =====================================================================================================
CS_TREE = {".deepmap-root": "", "src/Types.cs": (
    "namespace Lib\n"
    "{\n"
    "    public record SearchResult(string Name)\n"
    "    {\n"
    "        public bool Matches(string q)\n"
    "        {\n"
    "            return Name == q;\n"
    "        }\n"
    "\n"
    "        public override string ToString()\n"
    "        {\n"
    "            return Name;\n"
    "        }\n"
    "    }\n"
    "\n"
    "    public struct Dim\n"
    "    {\n"
    "        public int W;\n"
    "\n"
    "        public override string ToString()\n"
    "        {\n"
    "            return W.ToString();\n"
    "        }\n"
    "\n"
    "        public int Area()\n"
    "        {\n"
    "            return W * W;\n"
    "        }\n"
    "    }\n"
    "\n"
    "    public class User\n"
    "    {\n"
    "        public bool Check(SearchResult r)\n"
    "        {\n"
    "            return r.Matches(\"x\");\n"
    "        }\n"
    "    }\n"
    "}\n"),
    # the live verification's shapes (records, a record struct, a struct with a method), two partial parts of one
    # class (one of them in a second file) and a class named like one of the records, in another file
    "src/Money.cs": (
    "using System;\n"
    "\n"
    "namespace Shop.Models\n"
    "{\n"
    "    public readonly struct Money\n"
    "    {\n"
    "        public decimal Amount { get; }\n"
    "\n"
    "        public Money Add(Money other)\n"
    "        {\n"
    "            return new Money();\n"
    "        }\n"
    "    }\n"
    "\n"
    "    public record OrderLine(int ProductId, int Quantity, decimal UnitPrice)\n"
    "    {\n"
    "        public decimal LineTotal() => Quantity * UnitPrice;\n"
    "\n"
    "        public string Describe()\n"
    "        {\n"
    "            return Quantity + \" x product \" + ProductId;\n"
    "        }\n"
    "    }\n"
    "\n"
    "    public record InvoiceLine(string Sku, decimal Amount)\n"
    "    {\n"
    "        public string Describe()\n"
    "        {\n"
    "            return Sku + \": \" + Amount;\n"
    "        }\n"
    "    }\n"
    "\n"
    "    public record struct GeoPoint(double Lat, double Lon)\n"
    "    {\n"
    "        public double DistanceTo(GeoPoint other)\n"
    "        {\n"
    "            return Math.Sqrt(Lat * other.Lat + Lon * other.Lon);\n"
    "        }\n"
    "    }\n"
    "}\n"),
    "src/Cart.cs": (
    "namespace Shop.Models\n"
    "{\n"
    "    public partial class Cart\n"
    "    {\n"
    "        public int Count() => 1;\n"
    "    }\n"
    "\n"
    "    public partial class Cart\n"
    "    {\n"
    "        public int Twice() => Count() * 2;\n"
    "    }\n"
    "}\n"),
    "src/CartTotals.cs": (
    "namespace Shop.Models\n"
    "{\n"
    "    public partial class Cart\n"
    "    {\n"
    "        public decimal Total() => Count() * 3m;\n"
    "    }\n"
    "}\n"),
    "src/Legacy.cs": (
    "namespace Shop.Legacy\n"
    "{\n"
    "    public class OrderLine\n"
    "    {\n"
    "        public string Describe() => \"legacy\";\n"
    "    }\n"
    "}\n")}
GONE_FILES = ("src/Legacy.cs", "src/CartTotals.cs")    # dropped from D3's second extraction, as if deleted
_ENGINE: dict = {}


def engine_extractions():
    """{"0": (nodes, calls), "1": (nodes, calls), "0r": ..., "1r": ...}: the installed engine run ONCE on CS_TREE,
    mapped with DEEPMAP_CSHARP_MEMBERS=0 and with the default; the "r" variants with the language recognizers merged
    in (what sync pushes). None when the engine fails."""
    if "x" in _ENGINE:
        return _ENGINE["x"]
    import graphify_extract as GX
    home, root = tmp() / "h", write_tree(tmp() / "cs", CS_TREE)
    out = None
    with clean_env(DEEPMAP_HOME=str(home)), contextlib.redirect_stderr(io.StringIO()), \
            contextlib.redirect_stdout(io.StringIO()):
        res, rep = GX.extract_with_report(root)
        gp = GX.graphify_out_dir(root) / "graph.json"
        if res is not None and gp.is_file():
            graph = json.loads(gp.read_text(encoding="utf-8"))
            Sr = load_module(BUILD / "sync.py", f"dm_relabel_recog_{os.getpid()}")
            out = {}
            for flag in ("0", None):
                with clean_env(DEEPMAP_HOME=str(home), DEEPMAP_CSHARP_MEMBERS=flag):
                    ns, cs, _im = GX.map_graph(graph, root=root)
                    rs, _typed = Sr._apply_recognizers(root, copy.deepcopy(ns))
                out["0" if flag else "1"] = (ns, cs)
                out["0r" if flag else "1r"] = (rs, cs)
    _ENGINE["x"] = (out, GX.graphify_version(), (rep or {}).get("cause"))
    return _ENGINE["x"]


def _without(ext, files):
    """An extraction (nodes, calls) with every node of `files` gone (and the calls those nodes made)."""
    nodes = [n for n in ext[0] if n.get("source_file") not in files]
    keys = {n["key"] for n in nodes}
    return nodes, [c for c in ext[1] if c[0] in keys]


def _triple(n):
    t = (n.get("source_file"), n.get("name"), n.get("start_line"))
    return t if isinstance(t[0], str) and t[0] and isinstance(t[1], str) and type(t[2]) is int and t[2] >= 1 else None


def d_engine(Sm, rec):
    if not _HAS_ENGINE:
        print("  [SKIP] D: graphify is not installed")
        return
    out, ver, cause = engine_extractions()
    if not rec.check(f"D: graphify {ver} extracted the C# tree and map_graph mapped it both ways", out is not None,
                     cause):
        return
    a, b = out["0"], out["1"]
    lab = {"0": {n["key"]: n["label"] for n in a[0]}, "1": {n["key"]: n["label"] for n in b[0]}}
    moved = sorted(k for k in lab["0"] if k in lab["1"] and lab["0"][k] != lab["1"][k])
    if ver == "0.8.39":
        rec.check("D1: (the teeth) on 0.8.39 keys really change label between members=0 and the default "
                  "(Function -> Method)", len(moved) >= 3 and all(lab["0"][k] == "Function" and lab["1"][k] == "Method"
                                                                 for k in moved), moved)
    else:
        rec.check(f"D1: on {ver} no key changes label (the engine owns record / struct methods itself)", not moved,
                  moved)
    for first, second, name in ((a, b, "members=0 -> default"), (b, a, "default -> members=0")):
        g = Graph()
        push(Sm, g, first[0], first[1])
        res = push(Sm, g, second[0], second[1])
        want = {(n["label"], n["key"]) for n in second[0]}
        extra = g.pairs() - want
        keys2 = {n["key"] for n in second[0]}
        twins = {(lb, k) for lb, k in extra if k in keys2}
        rec.check(f"D2: {ver}, {name}: no node of the second extraction's keys survives under another label",
                  not twins, sorted(twins))
        rec.check(f"D2b: {ver}, {name}: `.relabeled` == the keys that changed label ({len(moved)}), no class shell "
                  f"(map_graph alone writes none)", getattr(res, "relabeled", None) == len(moved)
                  and getattr(res, "shells", None) == 0,
                  (getattr(res, "relabeled", None), getattr(res, "shells", None), moved))
        teeth = {(n["label"], n["key"]) for n in first[0] if n["key"] in keys2} - want
        if ver == "0.8.39":
            rec.check(f"D2c: {ver}, {name}: (the teeth) a key-only prune would have left {len(moved)} twin(s) here",
                      len(teeth) == len(moved) >= 3, sorted(teeth))
    d_shells(Sm, rec, out, ver)


def d_shells(Sm, rec, out, ver):
    """D3: what sync pushes (map_graph + the recognizers), the second extraction without two files (as if deleted):
    on 0.8.39 the record shells <file>::X:<line> and the owners <id>@X:L<line> replace each other, and nothing that is
    not a same-triple :Class of the second extraction is removed; on 0.8.51 nothing is removed."""
    a, b = out["0r"], out["1r"]
    for first, second, name in ((a, _without(b, GONE_FILES), "members=0 -> default"),
                                (b, _without(a, GONE_FILES), "default -> members=0")):
        g = Graph()
        push(Sm, g, first[0], first[1])
        res = push(Sm, g, second[0], second[1])
        want = {(n["label"], n["key"]) for n in second[0]}
        keys2 = {n["key"] for n in second[0]}
        cls2 = {_triple(n) for n in second[0] if n["label"] == "Class"} - {None}
        gone = {(n["label"], n["key"]) for n in first[0]} - g.pairs()
        shells = sorted((lb, k) for lb, k in gone if k not in keys2)
        stale_kept = sorted((n["label"], n["key"]) for n in first[0] if n["key"] not in keys2
                            and (n["label"], n["key"]) in g.pairs())
        rec.check(f"D3: {ver}, {name}: every node of the second extraction is in the graph", want <= g.pairs(),
                  sorted(want - g.pairs())[:4])
        firsts = {(n["label"], n["key"]): n for n in first[0]}
        rec.check(f"D3b: {ver}, {name}: every stale node removed is a :Class with the file, name and line of a :Class "
                  f"the second extraction writes (a shell twin), and `.shells` counts them ({len(shells)})",
                  all(lb == "Class" and _triple(firsts[(lb, k)]) in cls2 for lb, k in shells)
                  and getattr(res, "shells", None) == len(shells), (shells, getattr(res, "shells", None)))
        gone_files_kept = [p for p in stale_kept if firsts[p].get("source_file") in GONE_FILES]
        rec.check(f"D3c: {ver}, {name}: the classes of the gone files stay (a same-named OrderLine of another file, "
                  f"a partial Cart part of another file at the same line as the one pushed): the prune's business",
                  any(firsts[p]["name"] == "OrderLine" and p[0] == "Class" for p in gone_files_kept)
                  and any(firsts[p]["name"] == "Cart" and p[0] == "Class" for p in gone_files_kept), gone_files_kept)
        dup = {}
        for n in g.nodes.values():
            if "Class" in n["labels"] and _triple(n) is not None:
                dup.setdefault(_triple(n), []).append(n["key"])
        own = {}
        for n in second[0]:
            if n["label"] == "Class" and _triple(n) is not None:
                own.setdefault(_triple(n), []).append(n["key"])
        twice = {t: ks for t, ks in dup.items() if len(ks) > 1 and len(own.get(t, ())) < len(ks)}
        rec.check(f"D3d: {ver}, {name}: no :Class (file, name, line) is held twice unless the extraction itself writes "
                  f"it twice", not twice, twice)
        if ver == "0.8.39":
            plain = {(n["label"], n["key"]) for n in (out["0"] if name.startswith("members") else out["1"])[0]}
            if name.startswith("members"):      # the shells are what the recognizers added to the members=0 graph
                want_gone = {p for p in firsts if p not in plain and p[0] == "Class" and _triple(firsts[p]) in cls2}
            else:                               # the owners are what the owner step added to map_graph's own output
                want_gone = {p for p in firsts if p[1] not in keys2 and p[0] == "Class" and _triple(firsts[p]) in cls2}
            rec.check(f"D3e: {ver}, {name}: (the teeth) exactly the "
                      + ("recognizer's record / struct shells" if name.startswith("members") else "owners those shells "
                         "duplicate") + " are removed, the live verification's three among them (OrderLine, "
                      "InvoiceLine, GeoPoint)", set(shells) == want_gone
                      and {"OrderLine", "InvoiceLine", "GeoPoint"} <= {firsts[p]["name"] for p in shells},
                      (shells, sorted(want_gone)))
            if name.startswith("default"):
                rec.check(f"D3f: {ver}, {name}: the struct's owner (Money, no shell to replace it) stays",
                          any(firsts[p]["name"] == "Money" and p[0] == "Class" for p in stale_kept), stale_kept)
        else:
            rec.check(f"D3e: {ver}, {name}: no class shell (the engine owns records itself)", not shells, shells)


# =====================================================================================================
# F — the census cost (a recording driver)
# =====================================================================================================
class _CountDrv:
    """A recording stand-in for Neo4j that answers only the census: per label {key: (labels, sf, name, line)}.
    Each statement is logged as (kind, keys sent, rows returned); kind is "scan" (the key-only scan), "seek" (an
    UNWIND $keys key seek, the census this replaced or the confirming read) or "other"."""

    def __init__(self, nodes):
        self.by_label: dict = {}
        for n in nodes:
            self.by_label.setdefault(n["label"], {})[n["key"]] = (
                [n["label"]], n["source_file"], n["name"], n["start_line"])
        self.log: list = []

    def session(self, *a, **k):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, cypher, **p):
        m, c = _SCAN.match(cypher), _CENSUS.match(cypher)
        if m:
            rows = [{"k": k} for k in self.by_label.get(m.group(1), {})]
            self.log.append(("scan", len(p.get("keys") or ()), len(rows)))
        elif c:
            have = self.by_label.get(c.group(1), {})
            rows = [{"k": k, "ls": have[k][0], "sf": have[k][1], "name": have[k][2], "line": have[k][3]}
                    for k in (p.get("keys") or ()) if k in have]
            self.log.append(("seek", len(p.get("keys") or ()), len(rows)))
        else:
            rows = []
            self.log.append(("other", len(p.get("keys") or ()), 0))
        return _Res(rows)


_COST_LABELS = ("Function",) * 8 + ("Method",) * 6 + ("Class",) * 3 + ("Module",) * 3   # 40/30/15/15 %, no Route


def _synthetic(n: int) -> list:
    return [node(f"src/p{i // 50}/f{i // 10}.cs::S{i}:{i % 10 + 1}", _COST_LABELS[i % 20], f"S{i}",
                 sf=f"src/p{i // 50}/f{i // 10}.cs", line=i % 10 + 1) for i in range(n)]


def _cost(Sm, graph_nodes, ext_nodes) -> dict:
    drv = _CountDrv(graph_nodes)
    got = _census(Sm, drv, ext_nodes)
    kinds = [k for k, _s, _r in drv.log]
    return {"statements": len(drv.log), "scans": kinds.count("scan"), "seeks": kinds.count("seek"),
            "keys_sent": sum(s for _k, s, _r in drv.log), "rows_read": sum(r for _k, _s, r in drv.log),
            "twins": sum(len(v) for v in got.values()), "shells": len(_shell_keys(got))}


def f_cost(Sm, rec, sizes=(6_000, 50_000), quiet=False):
    """F: the census on a graph equal to its extraction (the common push: nothing can be a twin) and with a few
    twins and stale classes; prints the counts (statements, keys sent, rows read)."""
    for n in sizes:
        nodes = _synthetic(n)
        labels = Sm._twin_labels(labels_of(nodes))
        c = _cost(Sm, nodes, nodes)
        old_seeks = sum(1 for lb in labels for ls in labels_of(nodes).values() if lb not in ls)
        if not quiet:
            print(f"  [INFO] F: {n:,} nodes, no twin: {c['statements']} statement(s) ({c['scans']} scan, {c['seeks']} "
                  f"seek), {c['keys_sent']:,} key(s) sent, {c['rows_read']:,} row(s) read (the census this replaced "
                  f"sent {old_seeks:,} keys, one index seek each, in "
                  f"{sum(-(-sum(1 for ls in labels_of(nodes).values() if lb not in ls) // 10_000) for lb in labels)} "
                  f"statement(s))")
        rec.check(f"F1: {n:,} nodes equal to the extraction: one key-only scan per label ({len(labels)}), no seek, "
                  f"no key sent, each node's key read once ({n:,} rows)",
                  c["scans"] == len(labels) == c["statements"] and c["seeks"] == 0 and c["keys_sent"] == 0
                  and c["rows_read"] == n and c["twins"] == 0 and c["shells"] == 0, c)
        # a few twins (3 keys moved Function -> Method) and stale classes (2 shells, 10 that duplicate nothing)
        graph = list(nodes)
        moved = [x for x in nodes if x["label"] == "Function"][:3]
        ext = [dict(x, label="Method") if x in moved else x for x in nodes]
        cls = [x for x in nodes if x["label"] == "Class"][:12]
        graph += [dict(x, key=x["key"] + "#shell") for x in cls[:2]]
        graph += [dict(x, key=x["key"] + "#old", start_line=x["start_line"] + 100) for x in cls[2:12]]
        c = _cost(Sm, graph, ext)
        if not quiet:
            print(f"  [INFO] F: {n:,} nodes + 3 relabel twins + 2 shells + 10 stale classes: {c['statements']} "
                  f"statement(s) ({c['scans']} scan, {c['seeks']} seek), {c['keys_sent']:,} key(s) sent, "
                  f"{c['rows_read']:,} row(s) read")
        rec.check(f"F2: {n:,} nodes with 3 relabel twins, 2 shells and 10 stale non-duplicate classes: found exactly "
                  f"3 + 2; only the 3 + 12 candidates are sent (one seek per label with candidates)",
                  c["twins"] == 3 and c["shells"] == 2 and c["keys_sent"] == 15 and c["seeks"] == 2
                  and c["scans"] == len(labels), c)


# =====================================================================================================
# E — mutation teeth
# =====================================================================================================
def _shell_checks(Sm, rec):
    """A12-A15 and B9/B10 alone: what a mutant of the class shell rule breaks."""
    a_shells(Sm, rec)
    b_shells(Sm, rec)


MUTANTS_FAST = [
    ("the twin step skipped", "    if n_twins:\n        # (the counts above", "    if False:\n        # (the counts above"),
    ("the census reads only this extraction's labels",
     '    known = list(getattr(indexes, "KEY_INDEX_LABELS", None) or _TWIN_LABELS_FALLBACK)\n', "    known = []\n"),
    ("the census ignores a node's live labels", "                        if not have & set(live):\n",
     "                        if True:\n"),
    ("the census reads a label-less MATCH (n)", 's.run(f"MATCH (n:{lb}) WHERE n.key IS NOT NULL RETURN n.key AS k")',
     's.run(f"MATCH (n) WHERE n.key IS NOT NULL RETURN n.key AS k")'),
    ("the scan sends every key it reads (the old census's cost)",
     "                if (live is not None and lb not in live) or (live is None and shells_here):\n",
     "                if True:\n"),
    ("the pending stamp skipped", "        _mark_fts_rebuild(driver, True)                   # stamped BEFORE",
     "        pass                   # stamped BEFORE"),
    ("the count not reported", "    result.relabeled = n_twins\n", "    result.relabeled = 0\n"),
    ("the delete drops nodes only (no relationships first)",
     '        _run_batched(driver, f"{m} MATCH (n)-[r]->()", "r", "DELETE r", _DELETE_REL_BATCH, cand=cand)\n'
     '        _run_batched(driver, f"{m} MATCH ()-[r]->(n)", "r", "DELETE r", _DELETE_REL_BATCH, cand=cand)\n', ""),
    ("the delete without the live-label guard", ' WHERE NONE(l IN labels(n) WHERE l IN twin.ls)"', '"'),
    ("the stale prune runs inside the push",
     "    twins = _relabel_twins(driver, labels_of, _class_triples(nodes))   # before",
     "    _cleanup_stale(driver, list(labels_of))\n"
     "    twins = _relabel_twins(driver, labels_of, _class_triples(nodes))   # before"),
    # the class shell rule
    ("the shell rule ignores the line", "                        if t is not None and t in classes:\n",
     "                        if t is not None and any(t[:2] == c[:2] for c in classes):\n", _shell_checks),
    ("the shell rule ignores the file", "                        if t is not None and t in classes:\n",
     "                        if t is not None and any(t[1:] == c[1:] for c in classes):\n", _shell_checks),
    ("the shell rule ignores the name", "                        if t is not None and t in classes:\n",
     "                        if t is not None and any((t[0], t[2]) == (c[0], c[2]) for c in classes):\n",
     _shell_checks),
    ("shells read under every label", "            shells_here = bool(classes) and lb == _SHELL_LABEL\n",
     "            shells_here = bool(classes)\n", _shell_checks),
    ("a missing line matches",
     "    if isinstance(sf, str) and sf and isinstance(name, str) and name and type(line) is int and line >= 1:\n",
     "    if isinstance(sf, str) and sf and isinstance(name, str) and name:\n", _shell_checks),
    ("the shell delete without its file / name / line re-check",
     '             "WHERE n.source_file = twin.sf AND n.name = twin.name AND n.start_line = twin.line")',
     '             "WHERE n.source_file = twin.sf AND n.name = twin.name AND n.start_line = twin.line '
     'OR twin.k IS NOT NULL")', _shell_checks),
    ("the push ignores the shells", '    n_shells = len(getattr(twins, "shells", None) or ())\n',
     "    n_shells = 0\n", _shell_checks),
    ("the shells are never deleted", "    if shells:\n        m = (f\"UNWIND $cand AS twin MATCH (n:{_SHELL_LABEL}",
     "    if False:\n        m = (f\"UNWIND $cand AS twin MATCH (n:{_SHELL_LABEL}", _shell_checks),
    ("the push writes no class triples",
     "    twins = _relabel_twins(driver, labels_of, _class_triples(nodes))   # before",
     "    twins = _relabel_twins(driver, labels_of, {})   # before", _shell_checks),
]
def _c_plain(Sm, rec):
    """C3 alone (one sync() run: a plain sync with the three relabel twins)."""
    c_sync(Sm, rec, only={"C3"}, extras=False)


# each sync() mutant runs only the C checks that read what it breaks (a full C pass is 13 sync() runs)
MUTANTS_SYNC = [
    ("sync() ignores the count for the full-text index",
     "        if n_twins:\n            fts_dirty = True\n", "        if n_twins:\n            fts_dirty = fts_dirty\n",
     _c_plain),
    ("sync() records nothing", "    extra.update(_relabeled_field(info))\n", "", _c_plain),
    ("sync() reads a non-int count as removed twins", "    n = n if type(n) is int and n > 0 else 0\n",
     "    n = n if n else 0\n", lambda Sm, rec: _non_int_count(Sm, rec)),
    ("sync() names the fixed C# example again", "{_moves_text(moves, n_moved)}",
     " (for example a C# record or struct method that is now a :Method, not a :Function)", _c_plain),
    ("sync() trusts moves that do not add up", "    if not moves or sum(k for _o, _n, k in moves) != total:\n",
     "    if not moves:\n", lambda Sm, rec: c_neutral(Sm, rec, only={"C9c"})),
    ("sync() counts the shells in the prune again", "                _discount_plan(plan, n_shells)\n", "",
     lambda Sm, rec: c_shells(Sm, rec, only={"C6"})),
    ("sync() reports a blast-radius skip with nothing left",
     "            elif int(plan.get(\"would_delete\") or 0) > 0:"
     "  # blast-radius", "            else:  # blast-radius", lambda Sm, rec: c_shells(Sm, rec, only={"C8"})),
]


def run_fast(Sm, rec):
    a_units(Sm, rec)
    b_push(Sm, rec)


def run_sync_checks(Sm, rec):
    c_sync(Sm, rec)


def e_mutants():
    print("\n-- E: mutation teeth (each mutant of build/sync.py must turn a check RED) --")
    src = (BUILD / "sync.py").read_text(encoding="utf-8")
    for tag, mutants, runner in (("E-push", MUTANTS_FAST, run_fast), ("E-sync", MUTANTS_SYNC, run_sync_checks)):
        for i, (label, old, new, *own) in enumerate(mutants):
            n = src.count(old)
            if not LOUD.check(f"{tag}: mutant '{label}': its anchor is found exactly once in sync.py", n == 1,
                              f"found {n} times"):
                continue
            rec = Rec(loud=False)
            try:
                Sm = load_module(BUILD / "sync.py", f"dm_relabel_mut_{tag}_{i}_{os.getpid()}", src.replace(old, new))
                with contextlib.redirect_stdout(io.StringIO()):
                    (own[0] if own else runner)(Sm, rec)
            except Exception as exc:  # noqa: BLE001 — a crash is a caught mutant too
                rec.failed += 1
                rec.labels.append(f"crash: {exc!r}")
            LOUD.check(f"{tag}: mutant '{label}' is caught ({rec.failed} check(s) RED)", rec.failed > 0,
                       rec.labels[:2])


class _Truthy:
    """A push result attribute that is neither an int nor absent (a test double's auto-attribute)."""

    def __bool__(self):
        return True

    def __int__(self):
        return 1


def _non_int_count(Sm, rec):
    """C5: a push stand-in whose result carries a non-int `relabeled` (a mock's auto-attribute) counts as no twin."""
    class _R(tuple):
        relabeled = _Truthy()
    Sm.push_to_neo4j = lambda d, n, c, i, t=None: _R((len(n), 0))
    g = graph_of(NEW, CALLS)
    ok, err, root = run_sync(Sm, g, NEW)
    last = _read_json(root / ".deepmap" / "sync_last.json") or {}
    rec.check("C5: a push result whose `relabeled` is not an int counts as no twin (no re-create, nothing recorded)",
              ok is True and not g.fts_rebuilds and "relabeled" not in last, (g.fts_rebuilds, last.get("relabeled")))


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 64)
    print("DeepMap — a push removes a live key's node under another label (no Neo4j)")
    print("=" * 64)
    os.environ.setdefault("GIT_CONFIG_NOSYSTEM", "1")
    saved_uri = os.environ.get("NEO4J_URI")
    try:
        for title, fn in (("A: the census and the delete", a_units), ("B: push_to_neo4j", b_push),
                          ("C: sync() end to end", c_sync), ("C5: a non-int count", _non_int_count),
                          ("D: the installed engine", d_engine), ("F: the census cost", f_cost)):
            print(f"\n-- {title} --")
            try:
                Sm = load_module(BUILD / "sync.py", f"dm_relabel_{fn.__name__}_{os.getpid()}")
                fn(Sm, LOUD)
            except Exception as exc:  # noqa: BLE001
                LOUD.check(f"{fn.__name__}: runs without raising", False, repr(exc))
        try:
            e_mutants()
        except Exception as exc:  # noqa: BLE001
            LOUD.check("E: runs without raising", False, repr(exc))
    finally:
        if saved_uri is None:
            os.environ.pop("NEO4J_URI", None)
        else:
            os.environ["NEO4J_URI"] = saved_uri
        for d in _TMP:
            shutil.rmtree(d, ignore_errors=True)        # exactly the temp dirs this gate created
    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
