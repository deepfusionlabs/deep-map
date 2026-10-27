#!/usr/bin/env python3
"""DeepMap index builder (C3).

Implements the index contract below: the 2 full-text
+ 6 vector indexes DeepMap's hybrid search depends on. Validated against live Neo4j 5.26
(HNSW options m=24 / ef_construction=400 / 768-dim cosine accepted; all reach
ONLINE).

  create_all_indexes(driver) -> dict[name, "created"|"exists"]   # 2 FTS + 6 vector + 5 node-key
  wait_for_indexes(driver, timeout=300) -> bool                  # True only when all ONLINE

Note: the schema CREATES 6 vector indexes (incl. Community); the retriever SEARCHES 5
(Community excluded — build/retrieval.py:174-181). "6 created/ONLINE" != "5 searched".

It also creates 5 RANGE indexes on node `key` (one per code label) — a write-path speedup only:
`push_to_neo4j` MERGEs every node by `(:Label {key})` and MATCHes both endpoints of every edge (all
types) the same way, so a `key` index turns each of those from a label scan into an index seek (and
silences Neo4j's "MERGE without a supporting index" warning). sync creates them BEFORE its push, so even
the first sync of a project writes through them.
They are NOT search/embedding indexes — the retriever's arms and `EMBEDDABLE_LABELS` are untouched,
so the search-path count asymmetry (6 vector created / 5 searched) is unchanged.

Under DEEPMAP_FTS_SHORT_NAME=1 (default OFF) deepmap_code_fts also indexes `short_name` (the bare method of a
`Class.method` name); flipping the switch re-creates that index (see fts_properties / create_all_indexes).

  rebuild_fts_index(driver) -> "rebuilt"                         # DROP + CREATE deepmap_code_fts (round 10, P2)
  vector_quantization_report(session) -> (level, text)           # the doctor line (round 10, P4)

sync re-creates deepmap_code_fts after a push that rewrote indexed text or deleted nodes, so its BM25 statistics
equal a fresh build's (Lucene keeps updated/deleted documents in them until a merge). Under
DEEPMAP_VECTOR_QUANTIZATION=0 (opt-in; the default DDL is unchanged) the vector indexes are created with
`vector.quantization.enabled: false` — exact vector scores — and an index whose setting differs is re-created.

`python build/indexes.py` (run from the project) targets THIS project's instance by the one routing
rule (sync._resolve_route / port_registry.resolve_neo4j_route) and refuses — exit 1, nothing created —
when there is no safe route or the legacy 7687 graph is not provably this project's.
"""
from __future__ import annotations

import time

VECTOR_DIM = 768
HNSW_M = 24
HNSW_EF_CONSTRUCTION = 400

# (label, index_name) — 6 vector indexes (Community is embedded but not a retrieval arm).
VECTOR_INDEXES = [
    ("Function", "deepmap_fn_embeddings"),
    ("Method", "deepmap_method_embeddings"),
    ("Class", "deepmap_class_embeddings"),
    ("Module", "deepmap_module_embeddings"),
    ("Route", "deepmap_route_embeddings"),
    ("Community", "deepmap_community_embeddings"),
]

# (name, labels, properties) — 2 full-text indexes.
FTS_INDEXES = [
    ("deepmap_code_fts", "Function|Class|Module|Method|Route",
     ["name", "body_summary", "docstring", "rationale"]),
    ("deepmap_community_fts", "Community", ["name", "summary"]),
]

# DEEPMAP_FTS_SHORT_NAME=1 (round 7, I2 — default OFF): deepmap_code_fts also indexes `short_name`, the bare
# `method` of a dotted `Class.method` name that sync writes (build/sync.py _upsert_nodes). The standard
# analyzer keeps `Class.method` as ONE token, so BM25 could not match a method by its bare name (T4#2). An
# index-CONTENT change, so it is measured on the benchmark before any default flip; OFF, the index definition
# is byte-identical. FTS_INDEXES stays the base definition (the search-coherence contract reads it); the
# effective property list is fts_properties(). A 5th populated field changes the BM25 clause bound the
# retriever's query builder models (retrieval.py) — it must read fts_code_properties() when the flag is on.
CODE_FTS_INDEX = "deepmap_code_fts"
SHORT_NAME_PROPERTY = "short_name"


def fts_short_name_enabled() -> bool:
    """DEEPMAP_FTS_SHORT_NAME truthy (1/true/yes/on) — the same switch sync._fts_short_name_enabled reads."""
    import os
    return os.environ.get("DEEPMAP_FTS_SHORT_NAME", "").strip().lower() in {"1", "true", "yes", "on"}


def fts_properties(name: str, props=None) -> list:
    """The properties full-text index `name` is created ON — its FTS_INDEXES list, plus `short_name` for
    deepmap_code_fts when DEEPMAP_FTS_SHORT_NAME is on."""
    base = list(props if props is not None else next((p for n, _l, p in FTS_INDEXES if n == name), []))
    if name == CODE_FTS_INDEX and fts_short_name_enabled() and SHORT_NAME_PROPERTY not in base:
        base.append(SHORT_NAME_PROPERTY)
    return base


def fts_code_properties() -> list:
    """The effective property list of deepmap_code_fts (4 by default, 5 under DEEPMAP_FTS_SHORT_NAME=1)."""
    return fts_properties(CODE_FTS_INDEX)


def _index_properties(session, name):
    """The property list of the existing index `name` (one of this module's constants — inlined like the
    STARTS WITH filter above), or None when it can't be read. Never raises."""
    try:
        if not str(name).replace("_", "").isalnum():
            return None
        rows = list(session.run(f"SHOW INDEXES YIELD name, properties WHERE name = '{name}' "
                                "RETURN properties") or [])
        if not rows:
            return None
        props = rows[0]["properties"]
        return [str(p) for p in props] if isinstance(props, (list, tuple)) else None
    except Exception:
        return None

# (label, index_name) — RANGE indexes on node `key`, one per code label. Write-path ONLY: they make
# `push_to_neo4j`'s `MERGE (x:Label {key:row.key})` and its edge-endpoint `MATCH (a:Label {key:row.a})`
# index seeks instead of label scans (and suppress Neo4j's MERGE-without-index warning). Kept SEPARATE
# from VECTOR_INDEXES/FTS_INDEXES so the search-coherence contract (index_coherence_gates) is untouched.
# The keyless `:_DeepMapMeta` owner singleton has no `key` property, so it needs no index here.
KEY_INDEX_LABELS = ["Function", "Method", "Class", "Module", "Route"]
KEY_INDEXES = [(label, f"deepmap_key_{label.lower()}") for label in KEY_INDEX_LABELS]


# DEEPMAP_VECTOR_QUANTIZATION=0 (round 10, P4 — opt-in; default unchanged): Neo4j 5.23+ stores vector-index
# entries QUANTIZED by default (`vector.quantization.enabled: true`), so a vector-index score deviates from the
# exact (1+cos)/2 by ~1e-3 — the size of the gap between neighbours: the vector arm's order is approximate and
# a cosine-identical re-embed moves the fused top-15 (measured: adjacent inversions in a label's top-45).
# The switch creates the vector indexes with `vector.quantization.enabled: false` (exact scores) and
# re-creates an existing index whose setting differs; unset (or any other value) keeps the default DDL byte for
# byte — the ranking is benchmark-gated — and an index an opt-in run left unquantized is re-created quantized,
# so the default graph is exactly a never-flagged one. A server that cannot disable it (older than 5.23)
# rejects the option: the index is then created with the default DDL and the status says so.
QUANTIZATION_OPTION = "vector.quantization.enabled"


def vector_quantization_disabled() -> bool:
    """DEEPMAP_VECTOR_QUANTIZATION set to 0/false/no/off — the opt-in exact-score vector indexes."""
    import os
    return os.environ.get("DEEPMAP_VECTOR_QUANTIZATION", "").strip().lower() in {"0", "false", "no", "off"}


def _vector_ddl(label: str, name: str, exact: bool) -> str:
    """The CREATE statement of one vector index — byte-identical to the pre-round-10 one unless `exact`."""
    return (f"CREATE VECTOR INDEX {name} IF NOT EXISTS FOR (n:{label}) ON (n.embedding) "
            "OPTIONS {indexConfig: {"
            f"`vector.dimensions`: {VECTOR_DIM}, "
            "`vector.similarity_function`: 'cosine', "
            f"`vector.hnsw.m`: {HNSW_M}, `vector.hnsw.ef_construction`: {HNSW_EF_CONSTRUCTION}"
            + (f", `{QUANTIZATION_OPTION}`: false" if exact else "")
            + "}}")


def _run_now(session, cypher: str):
    """session.run + consume, so a statement error surfaces HERE (the driver may defer it to the next
    call on the session)."""
    res = session.run(cypher)
    consume = getattr(res, "consume", None)
    if callable(consume):
        consume()
    return res


def _index_config(session, name):
    """The indexConfig map of the existing index `name` (a module constant), or None when it can't be
    read. Never raises."""
    try:
        if not str(name).replace("_", "").isalnum():
            return None
        rows = list(session.run(f"SHOW INDEXES YIELD name, options WHERE name = '{name}' RETURN options") or [])
        if not rows:
            return None
        opts = rows[0]["options"]
        cfg = opts.get("indexConfig") if isinstance(opts, dict) else None
        return dict(cfg) if isinstance(cfg, dict) else None
    except Exception:
        return None


def _vector_quantization(session, name):
    """True / False as the existing vector index `name` reports `vector.quantization.enabled`, or None when
    it does not report it (a server older than 5.23) or it can't be read. Never raises."""
    cfg = _index_config(session, name)
    if not cfg or QUANTIZATION_OPTION not in cfg:
        return None
    val = cfg[QUANTIZATION_OPTION]
    if isinstance(val, bool):
        return val
    text = str(val).strip().lower()
    return True if text == "true" else (False if text == "false" else None)


def create_all_indexes(driver) -> dict:
    """Create the 2 FTS + 6 vector indexes (idempotent via IF NOT EXISTS). Returns a status map.

    deepmap_code_fts is created on fts_properties(): when the existing index was built on a different
    property set (DEEPMAP_FTS_SHORT_NAME switched on or off since), it is dropped and re-created —
    IF NOT EXISTS alone would keep the old definition forever — and reported "recreated". When the
    existing properties can't be read, the index is left as it is.

    The vector indexes follow DEEPMAP_VECTOR_QUANTIZATION the same way (P4): an existing index whose
    reported `vector.quantization.enabled` differs from the wanted setting is dropped and re-created
    ("recreated" — the stored embeddings re-populate it; nothing is re-embedded); an index that does not
    report the setting is left as it is. Opt-in on a server that rejects the option: the default DDL,
    status "created (quantization not supported)"."""
    status: dict[str, str] = {}
    exact = vector_quantization_disabled()
    with driver.session() as s:
        existing = {r["name"] for r in s.run(
            "SHOW INDEXES YIELD name WHERE name STARTS WITH 'deepmap_' RETURN name")}

        for label, name in VECTOR_INDEXES:
            recreated = False
            if name in existing:
                quantized = _vector_quantization(s, name)
                if quantized is not None and quantized == exact:   # exact wanted & quantized, or vice versa
                    s.run(f"DROP INDEX {name} IF EXISTS")
                    recreated = True
            state = "recreated" if recreated else ("exists" if name in existing else "created")
            if exact:
                try:
                    _run_now(s, _vector_ddl(label, name, True))
                except Exception:  # noqa: BLE001 — an old server rejects the option: keep the default index
                    s.run(_vector_ddl(label, name, False))
                    state += " (quantization not supported)"
            else:
                s.run(_vector_ddl(label, name, False))
            status[name] = state

        for name, labels, props in FTS_INDEXES:
            props = fts_properties(name, props)
            recreated = False
            if name == CODE_FTS_INDEX and name in existing:
                current = _index_properties(s, name)
                if current is not None and set(current) != set(props):
                    s.run(f"DROP INDEX {name} IF EXISTS")
                    recreated = True
            on_each = ", ".join(f"n.{p}" for p in props)
            s.run(f"CREATE FULLTEXT INDEX {name} IF NOT EXISTS "
                  f"FOR (n:{labels}) ON EACH [{on_each}]")
            status[name] = "recreated" if recreated else ("exists" if name in existing else "created")

        # Node-key RANGE indexes (write-path speedup for the per-node MERGE). Idempotent via
        # IF NOT EXISTS; the label is a literal from KEY_INDEX_LABELS (no user input) so it is safe
        # in the index-definition position, matching how the vector/FTS labels are interpolated.
        for label, name in KEY_INDEXES:
            s.run(f"CREATE INDEX {name} IF NOT EXISTS FOR (n:{label}) ON (n.key)")
            status[name] = "exists" if name in existing else "created"
    return status


def rebuild_fts_index(driver, name: str = CODE_FTS_INDEX) -> str:
    """DROP and re-CREATE the full-text index `name` on its current definition (fts_properties) — round 10,
    P2. Lucene keeps an updated or deleted document in the BM25 statistics (docCount / docFreq) until its
    segment merges, so after a sync that rewrote summaries or deleted nodes the same graph ranked
    differently from a fresh build (measured on benchmark corpora: answers moved until a clean rebuild). A re-created
    index is populated from the graph as it is — additions only — which is exactly a fresh build's state.
    The index is missing until it is re-created and POPULATING until wait_for_indexes sees it ONLINE (the
    retriever skips a missing index — its FTS arm sits out that window). Returns "rebuilt"; a database
    error propagates."""
    labels, props = next(((lb, p) for n, lb, p in FTS_INDEXES if n == name), (None, None))
    if labels is None or not str(name).replace("_", "").isalnum():
        raise ValueError(f"not a DeepMap full-text index: {name!r}")
    on_each = ", ".join(f"n.{p}" for p in fts_properties(name, props))
    with driver.session() as s:
        _run_now(s, f"DROP INDEX {name} IF EXISTS")
        _run_now(s, f"CREATE FULLTEXT INDEX {name} IF NOT EXISTS FOR (n:{labels}) ON EACH [{on_each}]")
    return "rebuilt"


def vector_quantization_report(session) -> tuple:
    """(level, detail) for `deepmap doctor` (P4): whether the vector indexes store quantized entries, as the
    server reports it, against the DEEPMAP_VECTOR_QUANTIZATION wish. level is "OK" or "WARN" (the indexes
    disagree with the setting — the next sync re-creates them). Never raises."""
    exact = vector_quantization_disabled()
    wish = "DEEPMAP_VECTOR_QUANTIZATION=0 (exact)" if exact else "default (quantized)"
    try:
        names = {r["name"] for r in session.run(
            "SHOW INDEXES YIELD name WHERE name STARTS WITH 'deepmap_' RETURN name")}
    except Exception:
        return "WARN", "vector quantization: could not read the vector indexes"
    got = {name: _vector_quantization(session, name) for _l, name in VECTOR_INDEXES if name in names}
    if not got:
        return "WARN", f"vector quantization: no vector index yet (setting: {wish})"
    known = {v for v in got.values() if v is not None}
    if not known:
        return "OK", f"vector quantization: not reported by this Neo4j (older than 5.23?); setting: {wish}"
    if known == {True} and not exact:
        return "OK", ("vector quantization ON (Neo4j default) — vector scores are approximate (~1e-3); "
                      "DEEPMAP_VECTOR_QUANTIZATION=0 + a sync gives exact scores")
    if known == {False} and exact:
        return "OK", "vector quantization OFF — exact vector scores (DEEPMAP_VECTOR_QUANTIZATION=0)"
    on = sorted(n for n, v in got.items() if v is True)
    off = sorted(n for n, v in got.items() if v is False)
    return "WARN", (f"vector quantization: {len(on)} index(es) quantized, {len(off)} exact — setting: {wish}; "
                    f"the next sync re-creates the ones that differ")


def wait_for_indexes(driver, timeout: int = 300) -> bool:
    """Poll until every deepmap_* index is ONLINE. Returns False on timeout (never a silent
    premature success — a half-built index would otherwise yield empty arms, FD-1.9)."""
    deadline = time.time() + timeout
    expected = ({name for _, name in VECTOR_INDEXES}
                | {name for name, _, _ in FTS_INDEXES}
                | {name for _, name in KEY_INDEXES})
    while time.time() < deadline:
        with driver.session() as s:
            states = {r["name"]: r["state"] for r in s.run(
                "SHOW INDEXES YIELD name, state WHERE name STARTS WITH 'deepmap_' "
                "RETURN name, state")}
        if expected <= set(states) and all(states[n] == "ONLINE" for n in expected):
            return True
        if any(states.get(n) == "FAILED" for n in expected):
            return False  # an HNSW-option rejection or similar — surface it, don't spin
        time.sleep(1)
    return False


if __name__ == "__main__":
    import os
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import sync as _sync                                  # the ONE routing rule + owner check (D1)
    _root = Path.cwd()
    # This project's instance (port_registry.resolve_neo4j_route via sync): env NEO4J_URI wins, else
    # the project's own instance. No safe route (an unreadable registry, or no instance of its own
    # while another project owns 7687) -> refuse; it used to fall back to 7687 — another project's.
    _uri, _legacy, _problem = _sync._resolve_route(_root)
    if _problem:
        print(f"[deepmap indexes] cannot create indexes for {_root}: {_problem}", file=sys.stderr)
        raise SystemExit(1)
    from neo4j import GraphDatabase
    drv = GraphDatabase.driver(_uri,
                               auth=("neo4j", os.environ.get("NEO4J_PASSWORD", "deepmap")))
    try:
        if _legacy:                                       # bolt 7687 without an instance of its own:
            _ok, _why = _sync.verify_legacy_owner(drv, _root, uri=_uri)   # only this project's graph
            if not _ok:
                print(f"[deepmap indexes] REFUSING: {_why}", file=sys.stderr)
                raise SystemExit(1)
        print("create_all_indexes:", create_all_indexes(drv))
        _online = wait_for_indexes(drv, timeout=60)
        print("wait_for_indexes:", _online)
        with drv.session() as _s:
            print(vector_quantization_report(_s)[1])
    finally:
        drv.close()
    raise SystemExit(0 if _online else 1)
