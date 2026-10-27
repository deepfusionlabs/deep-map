#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DeepMap incremental-extraction cache (opt-in speed win — DEEPMAP_INCREMENTAL_SYNC).

A per-file content-hash cache so an unchanged source file is NOT re-parsed on the next `sync`.
It is the extraction-time analog of the embedder's per-node content hash (`build/embeddings.py`
`content_hash` / `_embed_hash`): a file whose bytes are unchanged reuses its previously-extracted
(nodes, calls, imports); an ADDED / CHANGED file is re-extracted; a DELETED / MOVED-away file is
simply absent from the new walk and drops out.

CORRECTNESS INVARIANT (the whole point): the (nodes, calls, imports) this reconstructs for a tree
is **IDENTICAL** to a full re-extraction of that same tree — the win is only that unchanged files
skip the (expensive) `ast.parse` + visit + summarize. That holds because:
  * the per-file ast extraction is a PURE function of (rel, file bytes) — no cross-file state, no
    env dependence (name-resolution across files happens LATER, in push_to_neo4j, over the full
    node set, which is unchanged);
  * `incremental_extract` iterates the caller's file list in the SAME deterministic order the full
    walk uses and, per file, either re-extracts it (byte-identical to the full path, which calls
    the SAME `extract_one`) or reuses a cache entry that was produced from THIS EXACT content
    (guarded by the content hash — a hit means "same rel + same bytes" ⇒ same extraction).

Because a cache HIT requires an exact content-hash match, a stale / cold / corrupt / version-
mismatched cache can only ever cause spurious MISSES (extra re-extraction) — NEVER a wrong result.
`load_cache` is deliberately tolerant (any problem ⇒ an empty cache ⇒ a full extraction). So the
full rebuild is always the safe fallback, and this file never changes WHAT graph is produced.

Scope: this covers the file-by-file `ast` extractor path only. The default `graphify` engine runs
as one subprocess over the whole tree (a black box we cannot decompose per-file safely), so
incremental sync is inert there and a full extraction runs — see build/sync.py `_rebuild_code`.

Pure + dependency-free (hashlib/json/pathlib): unit-testable with no Neo4j, no model, no network.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

# Bump when the PER-FILE cached shape (nodes/calls/imports encoding) changes — an older cache then
# fails the version check in load_cache and is discarded (⇒ a one-time full extraction). Cheap
# insurance against ever reusing a differently-shaped entry.
CACHE_VERSION = 1
CACHE_FILENAME = "extract_cache.json"

# A sentinel "hash" for a file we could not read at hashing time. It can never equal a real 16-hex
# digest, so such a file ALWAYS misses (is re-attempted every sync) and can never collide with a
# genuinely-empty file's real hash (which would otherwise poison the cache — an unreadable file and
# a zero-byte file must not share a hash). See incremental_extract.
_UNREADABLE = "\x00unreadable"


def cache_path(root) -> Path:
    """Path of the per-root extraction cache (under the synced root's `.deepmap/`, gitignored)."""
    return Path(root) / ".deepmap" / CACHE_FILENAME


def hash_bytes(data: bytes) -> str:
    """16-char hex content hash of raw file bytes. Raw bytes (not decoded text) so the hash is
    CONSERVATIVE: two byte-strings that decode to the same text (BOM vs none, replaced invalid
    bytes) differ here ⇒ a spurious miss + re-extract (correct), never a false hit."""
    return hashlib.sha256(data).hexdigest()[:16]


def load_cache(root, *, extractor: str) -> dict:
    """Return the previous cache as {"files": {rel: {"hash", "nodes", "calls", "imports"}}}, or an
    EMPTY {"files": {}} on anything unexpected — a missing/unreadable/corrupt file, a CACHE_VERSION
    mismatch, or an `extractor` mismatch (an ast cache must not be reused for a different engine).
    Never raises: a bad cache degrades to a full extraction, it never fails a sync."""
    empty = {"files": {}}
    p = cache_path(root)
    try:
        if not p.exists():
            return empty
        obj = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return empty
    if not isinstance(obj, dict):
        return empty
    if obj.get("version") != CACHE_VERSION or obj.get("extractor") != extractor:
        return empty
    files = obj.get("files")
    if not isinstance(files, dict):
        return empty
    # Shallow structural sanity — a malformed per-file entry is dropped (⇒ that file just misses),
    # never trusted. Belt-and-braces vs a hand-edited / partially-written cache.
    clean: dict = {}
    for rel, ent in files.items():
        if isinstance(rel, str) and isinstance(ent, dict) and isinstance(ent.get("hash"), str):
            clean[rel] = ent
    return {"files": clean}


def save_cache(root, *, extractor: str, files: dict):
    """Write the new per-file cache atomically-ish (temp + replace). Best-effort — returns the path
    on success or None on any OSError (the cache is an optimization, never a build precondition)."""
    p = cache_path(root)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": CACHE_VERSION, "extractor": extractor, "files": files}
        tmp = p.with_suffix(p.suffix + ".part")
        tmp.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        os.replace(tmp, p)
        return p
    except OSError:
        return None


def _entry(h: str, fn: list, fc: list, fi: list) -> dict:
    """A cache entry for one file: its content hash + its extracted nodes/calls/imports. Calls and
    imports are stored as JSON lists (tuples don't survive JSON); they are rehydrated to tuples on
    read so the reconstructed tuple is `==` a fresh extraction's."""
    return {"hash": h, "nodes": fn, "calls": [list(x) for x in fc], "imports": [list(x) for x in fi]}


def incremental_extract(files, prev_files, extract_one):
    """Reconstruct (nodes, calls, imports) for a tree, reusing unchanged files from the cache.

    Args:
      files:       iterable of (path, rel, data) in the SAME deterministic order the full walk uses,
                   where `data` is the file's raw bytes, or None if it could not be read.
      prev_files:  the previous cache's {rel: entry} map (from load_cache(...)["files"]).
      extract_one: (path, rel) -> (file_nodes, file_calls, file_imports); the SAME per-file
                   extractor the full path calls (so a miss is byte-identical to a full re-extract).
                   Must itself be a pure function of (rel, file content) and handle its own errors
                   (return empty lists for a file it skips) so a hit and a miss agree.

    Returns (nodes, calls, imports, new_files, stats):
      * nodes/calls/imports — concatenated in file order, `==` a full re-extraction of `files`;
      * new_files — the cache map to persist via save_cache;
      * stats — {"hits", "misses", "total"} for a one-line sync log.
    """
    nodes: list = []
    calls: list = []
    imports: list = []
    new_files: dict = {}
    hits = misses = 0
    for path, rel, data in files:
        h = _UNREADABLE if data is None else hash_bytes(data)
        cached = prev_files.get(rel)
        hit = (cached is not None and h != _UNREADABLE and cached.get("hash") == h)
        if hit:
            fn = list(cached.get("nodes") or [])
            fc = [tuple(x) for x in (cached.get("calls") or [])]
            fi = [tuple(x) for x in (cached.get("imports") or [])]
            hits += 1
        else:
            raw_n, raw_c, raw_i = extract_one(path, rel)
            fn = list(raw_n)
            fc = [tuple(x) for x in raw_c]
            fi = [tuple(x) for x in raw_i]
            misses += 1
        new_files[rel] = _entry(h, fn, fc, fi)
        nodes.extend(fn)
        calls.extend(fc)
        imports.extend(fi)
    return nodes, calls, imports, new_files, {"hits": hits, "misses": misses, "total": hits + misses}
