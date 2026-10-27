#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j, no-model gates: fresh builds of the SAME corpus embed identically (v0.28.0 round 4, F5).

THE BUG (W8, measured on the STAGE-0 re-freeze): `embeddings.generate_embeddings` shuffled the pending nodes
with the UNSEEDED module-level `random.shuffle` (HNSW insert-order quality). The shuffle decides which nodes
share an encode CHUNK, and a batched encode is not bit-identical to the same text encoded in another batch
(the padded length / kernel accumulation order changes: 0/50 bit-equal batch vs single). So two fresh builds
of the same corpus differed in 119/571 embeddings, and near-tied search ranks 4-10 reordered between
rebuilds — the product was not reproducible, and the STAGE-0 harness had to seed Python's global RNG around
the UNCHANGED sync to get a stable oracle.

THE FIX: the pending list is put in a CANONICAL order (by node key, then content hash — never by elementId or
by the order Neo4j happens to return rows in, both of which differ between instances), then shuffled by a
PRIVATE `random.Random` seeded from that canonical list. Same corpus -> same chunks -> bit-identical vectors
and the same vector-index insert order; the insert order is still a pseudo-random permutation (HNSW quality),
and the process-global `random` state is left untouched.

The fake model below reproduces the real property that makes this matter: a text's vector depends (slightly)
on the other texts in its encode call.
"""
from __future__ import annotations

import hashlib
import importlib.util
import random
import struct
import sys
from pathlib import Path

BUILD = Path(__file__).resolve().parent.parent / "build"

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


def _load():
    spec = importlib.util.spec_from_file_location("dm_embeddings_det", BUILD / "embeddings.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _h(s: str) -> int:
    return int.from_bytes(hashlib.sha256(s.encode("utf-8")).digest()[:8], "big")


class _BatchSensitiveModel:
    """vector(text) = a text-determined base + a tiny term that depends on the WHOLE encode call — exactly the
    real batched-encode behaviour (equal to ~1e-7, never bit-equal across different chunk compositions)."""

    def __init__(self, dim: int):
        self.dim = dim
        self.calls = []

    def encode(self, texts, show_progress_bar=False, **k):
        if isinstance(texts, str):
            texts = [texts]
        self.calls.append(list(texts))
        batch_term = (_h("\x1f".join(texts)) % 1000) * 1e-7
        out = []
        for t in texts:
            b = _h(t)
            out.append([((b >> (i % 48)) & 0xFF) / 255.0 + batch_term for i in range(self.dim)])
        return out


def _corpus(n: int = 250, dup_texts: int = 6):
    """(key, name, summary) rows; a few nodes share the exact same text (same name + summary, other file)."""
    rows = []
    for i in range(n):
        rows.append({"key": f"pkg/mod{i % 17}.py::f{i}:{i + 1}", "name": f"f{i}",
                     "body_summary": f"does thing number {i} for the {('cart', 'user', 'order')[i % 3]}",
                     "docstring": None, "summary": None, "rationale": None})
    for j in range(dup_texts):   # identical composed text, distinct nodes
        rows.append({"key": f"pkg/copy{j}.py::helper:3", "name": "helper",
                     "body_summary": "returns the shared helper value",
                     "docstring": None, "summary": None, "rationale": None})
    return rows


class _Sess:
    def __init__(self, drv):
        self.drv = drv

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, cypher, **p):
        if "RETURN elementId" in cypher:
            self.drv.read_cypher = cypher
            return [{k: v for k, v in r.items() if k != "_ident"} for r in self.drv.rows]
        if "SET n.embedding" in cypher:
            self.drv.sets.append((self.drv.key_of[p["id"]], p["emb"], p["h"]))
        return []


class _Drv:
    """A 'fresh instance': its own elementIds, and rows returned in its own storage order."""

    def __init__(self, corpus, instance: str, order_seed: int, embed_hash=None):
        rows = []
        for i, r in enumerate(corpus):
            row = dict(r)
            row["id"] = f"4:{instance}:{(i * 7919 + order_seed) % 100003}"
            row["embed_hash"] = (embed_hash or {}).get(r["key"])
            rows.append(row)
        random.Random(order_seed).shuffle(rows)
        self.rows = rows
        self.key_of = {r["id"]: (r.get("_ident") or r["key"]) for r in rows}
        self.sets = []
        self.read_cypher = ""

    def session(self):
        return _Sess(self)


def _bits(vec):
    return b"".join(struct.pack("<d", float(x)) for x in vec)


def _build(E, corpus, instance, order_seed, global_seed, batch_size=100, embed_hash=None, force=False):
    E._get_model = lambda m=_BatchSensitiveModel(E.EMBED_DIM): m
    random.seed(global_seed)   # a different process-global RNG state per "build"
    drv = _Drv(corpus, instance, order_seed, embed_hash)
    n = E.generate_embeddings(drv, batch_size=batch_size, force=force)
    return n, drv


def gate_fresh_builds_identical(E) -> None:
    print("\n-- two fresh builds of the same corpus: bit-identical vectors + the same insert order (F5) --")
    corpus = _corpus()
    n1, a = _build(E, corpus, "aaaa-1111", order_seed=1, global_seed=11)
    n2, b = _build(E, corpus, "bbbb-2222", order_seed=2, global_seed=22)
    check("both builds embed every summarizable node", n1 == n2 == len(corpus), f"{n1} / {n2} / {len(corpus)}")
    va = {k: _bits(e) for k, e, _h2 in a.sets}
    vb = {k: _bits(e) for k, e, _h2 in b.sets}
    diff = [k for k in va if va[k] != vb.get(k)]
    check("every node's vector is BIT-identical across the two builds (other elementIds, other row order, "
          "other global RNG state)", not diff and len(va) == len(corpus), f"{len(diff)}/{len(va)} differ")
    ka = [k for k, _e, _h2 in a.sets]
    kb = [k for k, _e, _h2 in b.sets]
    check("the vector-index INSERT order (node keys) is identical", ka == kb,
          f"first divergence at {next((i for i, (x, y) in enumerate(zip(ka, kb)) if x != y), None)}")
    n3, c = _build(E, corpus, "cccc-3333", order_seed=3, global_seed=33, batch_size=100)
    check("a third build agrees too", [(k, _bits(e)) for k, e, _ in c.sets] == [(k, _bits(e)) for k, e, _ in a.sets])


def gate_still_shuffled(E) -> None:
    print("\n-- the insert order is still a pseudo-random permutation (HNSW insert-order quality) --")
    corpus = _corpus()
    _n, a = _build(E, corpus, "aaaa-1111", order_seed=1, global_seed=11)
    ka = [k for k, _e, _h2 in a.sets]
    canon = sorted(ka)
    check("insert order is NOT the sorted key order", ka != canon)
    check("insert order is NOT the order Neo4j returned the rows in", ka != [r["key"] for r in a.rows])
    # Not a trivially rotated/reversed canonical order: count adjacent pairs that stay adjacent-in-sorted-order.
    pos = {k: i for i, k in enumerate(canon)}
    adjacent = sum(1 for x, y in zip(ka, ka[1:]) if abs(pos[x] - pos[y]) == 1)
    check("the permutation is well mixed (few neighbours stay neighbours)", adjacent < len(ka) // 5,
          f"{adjacent} adjacent pairs of {len(ka) - 1}")
    other = _corpus(n=251)
    _n, o = _build(E, other, "aaaa-1111", order_seed=1, global_seed=11)
    ko = [k for k, _e, _h2 in o.sets if k in pos]
    check("a DIFFERENT corpus gets a different permutation (the seed is derived from the corpus)",
          ko != ka, "same order for a different corpus")


def gate_global_rng_untouched(E) -> None:
    print("\n-- the process-global `random` state is neither consumed nor reseeded --")
    corpus = _corpus(n=40)
    E._get_model = lambda m=_BatchSensitiveModel(E.EMBED_DIM): m
    random.seed(12345)
    before = random.getstate()
    E.generate_embeddings(_Drv(corpus, "dddd", 4), batch_size=16, force=True)
    check("random.getstate() is unchanged by generate_embeddings", random.getstate() == before)


def gate_incremental(E) -> None:
    print("\n-- an incremental re-embed (only changed nodes pending) is deterministic too --")
    corpus = _corpus(n=120)
    # First, a full build to learn each node's content hash.
    _n, full = _build(E, corpus, "eeee", order_seed=5, global_seed=5)
    hashes = {k: h for k, _e, h in full.sets}
    changed = [dict(r) for r in corpus]
    for r in changed[:: 7]:
        r["body_summary"] = r["body_summary"] + " (edited)"
    runs = []
    for inst, osd, gsd in (("ffff", 6, 60), ("gggg", 7, 70)):
        n, d = _build(E, changed, inst, order_seed=osd, global_seed=gsd, batch_size=8, embed_hash=hashes)
        runs.append((n, [(k, _bits(e)) for k, e, _ in d.sets]))
    want = len(changed[:: 7])
    check("only the edited nodes are re-embedded", runs[0][0] == runs[1][0] == want,
          f"{runs[0][0]} / {runs[1][0]} / {want}")
    check("same pending set -> same chunks, same vectors, same order", runs[0][1] == runs[1][1])


def gate_edge_rows(E) -> None:
    print("\n-- rows without a key / with identical text never break the canonical order --")
    corpus = _corpus(n=30)
    for i, r in enumerate(corpus[:5]):
        r["_ident"] = f"<nokey-{i}>"      # the gate's own bookkeeping identity; the product sees key=None
        r["key"] = None
    E._get_model = lambda m=_BatchSensitiveModel(E.EMBED_DIM): m
    random.seed(1)
    d1 = _Drv(corpus, "hhhh", 8)
    n1 = E.generate_embeddings(d1, batch_size=7, force=True)
    random.seed(2)
    d2 = _Drv(corpus, "iiii", 9)
    n2 = E.generate_embeddings(d2, batch_size=7, force=True)
    check("keyless rows are embedded (no crash on a None key)", n1 == n2 == len(corpus), f"{n1}/{n2}")
    s1 = [(k, _bits(e)) for k, e, _ in d1.sets]
    s2 = [(k, _bits(e)) for k, e, _ in d2.sets]
    check("keyless rows still get identical vectors + insert order across builds (content-hash order)",
          s1 == s2)
    check("the read query also returns the node key (the canonical order needs it)",
          "n.key" in d1.read_cypher, d1.read_cypher[:160])


def gate_contract_kept(E) -> None:
    print("\n-- the existing contract holds (one encode per chunk, hash-gated re-embed, count) --")
    corpus = _corpus(n=25, dup_texts=0)
    model = _BatchSensitiveModel(E.EMBED_DIM)
    E._get_model = lambda: model
    drv = _Drv(corpus, "jjjj", 10)
    n = E.generate_embeddings(drv, batch_size=10, force=True)
    check("returns the count embedded", n == 25, str(n))
    check("ONE encode call per chunk (25 nodes / 10 -> 3 calls)", len(model.calls) == 3, str(len(model.calls)))
    check("every SET carries the node's own content hash",
          all(h == E.content_hash(E.compose_text(next(r for r in corpus if r["key"] == k))) for k, _e, h in drv.sets))
    hashes = {k: h for k, _e, h in drv.sets}
    model2 = _BatchSensitiveModel(E.EMBED_DIM)
    E._get_model = lambda: model2
    n = E.generate_embeddings(_Drv(corpus, "kkkk", 11, embed_hash=hashes), batch_size=10, force=False)
    check("an unchanged corpus re-embeds nothing", n == 0 and not model2.calls, f"{n} / {len(model2.calls)}")
    doc = (E._embed_docs.__doc__ or "")
    check("_embed_docs no longer claims a batched encode is identical to encoding each text alone",
          "identical to encoding each alone" not in doc, doc[:160])


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 72)
    print("DeepMap — embeddings: fresh builds of the same corpus are reproducible (F5; no model, no Neo4j)")
    print("=" * 72)
    E = _load()
    gate_fresh_builds_identical(E)
    gate_still_shuffled(E)
    gate_global_rng_untouched(E)
    gate_incremental(E)
    gate_edge_rows(E)
    gate_contract_kept(E)
    print("\n" + "=" * 72)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
