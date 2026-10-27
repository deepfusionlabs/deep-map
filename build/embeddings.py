#!/usr/bin/env python3
"""DeepMap embedder (C4) — intent-summary embeddings.

Implements the embedder contract below, validated against a live
Neo4j 5.26 + the real model. DeepMap embeds DETERMINISTIC INTENT SUMMARIES
(body_summary / docstring / summary — synthesized by build/summarize.py with NO LLM call), NOT raw
code — "search by what the code does, not how it's spelled". The model is a GENERAL-TEXT
model; the edge is *what* is embedded.

Contract (frozen — do not change behavior):
  compose_text(record)            -> "search_document: {name}: {summary}. {rationale}"
  content_hash(text, model_name)  -> 16-char hex, model-name salted (drives re-embedding)
  generate_embeddings(driver, batch_size=100, force=False)
                                  -> re-embeds where _embed_hash != _content_hash;
                                     a SEEDED shuffle before insert (HNSW quality) — the seed and the
                                     order it permutes come from the corpus, so fresh builds of the
                                     same corpus embed bit-identically (v0.28.0)
  embed_query(text)               -> list[float] (768), "search_query: " prefix (asymmetric)
  MODEL_NAME = "nomic-ai/modernbert-embed-base"  (768-dim, Apache-2.0, GENERAL-TEXT)

For the long-lived MCP servers (v0.28.0; additive — `_get_model()` is unchanged for sync / the CLI):
  model_status()                  -> non-blocking {"state": ready|loading|failed|cold, ...}
  ensure_model(max_wait_s, retry_after_s)
                                  -> the model, or ModelUnavailable at once when the last load failed
                                     recently / a load in progress is older than the caller will wait

CPU threads (round 8 J6, round 9 K13): the model load pins torch to ONE intra-op thread — the MCP servers'
setting (encode() deadlocked in a server worker thread with the default parallelism) and every QUERY encode's, so
the CLI's in-process query vector equals the server's. Only the BULK path (generate_embeddings: sync / setup)
raises it, to bulk_embed_threads() — DEEPMAP_EMBED_THREADS, else a FIXED 8 on every machine — for each batched
encode, under the encode lock, and restores it after. The vectors depend on that thread COUNT, never on how many
CPUs the process can use, so the fixed default embeds a corpus identically on every machine (same CPU instruction
set and torch/MKL build) — and a rebuild bit-identically.
"""
from __future__ import annotations

import contextlib
import hashlib
import os
import random
import sys
import threading
import time
from typing import Any

MODEL_NAME = "nomic-ai/modernbert-embed-base"
EMBED_DIM = 768
DOC_PREFIX = "search_document: "    # asymmetric prefixing (design.md:34,37)
QUERY_PREFIX = "search_query: "

# The labels DeepMap embeds (every embeddable label, incl. Community which is embedded but
# not searched — see build/retrieval.py:174-181). The retriever searches 5 of these.
EMBEDDABLE_LABELS = ["Function", "Method", "Class", "Module", "Route", "Community"]

_model = None  # lazy-loaded (≈3s first call)
_model_lock = threading.Lock()  # guard the lazy init: the MCP server is long-lived + concurrent
# Serialise encodes: the MCP tools run in worker threads, and a HF fast tokenizer must not be driven from two
# threads at once ("Already borrowed"). torch is pinned to one thread for queries, so this costs no throughput.
# A BULK encode holds it too, while torch runs on the bulk thread count, so a query encode never runs on it.
_encode_lock = threading.Lock()
# Load bookkeeping for the long-lived MCP servers (model_status / ensure_model): when the current load
# started (None when no load is running) and when + why the last one failed. Monotonic seconds.
_load_state: dict = {"started": None, "failed_at": None, "error": None}

# Bulk-embedding threads (round 8 J6; round 9 K13). A batched encode's vectors depend on the torch intra-op thread
# COUNT (it decides how the kernels split their reductions), NOT on how many CPUs the process can use: measured on
# the real model (64 real docstrings, torch 2.12 + MKL, CPU-affinity masks), a fixed count was bit-identical with
# 12, 4 or 2 usable CPUs (64/64 at 8, 4 and 1 threads), while 4 vs 8 threads left 0/64 bit-equal — and on the
# pinned 571-node STAGE-0 corpus 435/571, with ranks 5/6 of a frozen oracle query swapped. Round 8's default,
# torch's own count (the physical cores, or OMP_NUM_THREADS) capped at 8, so embedded one corpus differently on a
# 4-core and an 8-core machine. The default is now ONE FIXED count on every machine: 8 = round 8's cap (a machine
# that already ran 8 embeds exactly as before; 8 kept the 1-thread STAGE-0 freeze's arm order on all 35 measured
# queries; 1 -> 6-8 threads ~doubles the rate: 3.7 -> 7.9 nodes/s). DEEPMAP_EMBED_THREADS overrides it: a positive
# integer (capped at _BULK_THREADS_LIMIT; 1 = the single-thread vectors of builds before round 8, bit for bit), or
# `auto` = round 8's machine-dependent count (torch's own default, read at the first load BEFORE the load pins it
# to 1, capped at _BULK_THREADS_AUTO_MAX), whose vectors then depend on the machine. 8 threads on a SMALL machine
# is not a slowdown: pinned to 2 CPUs (a loaded i7-1355U, 32 docs) 8 threads took 105 s vs 120-142 s at 2 and
# 132-165 s at 1 (torch turns MKL's dynamic thread adjustment off, so it runs exactly the count it is given; the
# oversubscription cost nothing measurable there). A different CPU instruction set (AVX-512 / AVX2 / ARM) or
# torch/MKL build can still move a vector at float rounding; no thread count pins that.
_BULK_THREADS_DEFAULT = 8
_BULK_THREADS_AUTO_MAX = 8
_BULK_THREADS_LIMIT = 64
_torch_default_threads: "int | None" = None
_threads_env_noted: set = set()


class ModelUnavailable(RuntimeError):
    """The embedding model can't serve this call right now — the last load failed recently, or a load
    has been running for longer than the caller is willing to wait. Search degrades to keyword-only on
    it; the embed tool reports it."""


def short_error(exc: BaseException, limit: int = 200) -> str:
    """'Type: first line of the message', capped — HF load errors run to several lines of advice."""
    first = (str(exc).strip().splitlines() or [""])[0].strip()
    if len(first) > limit:
        first = first[:limit].rstrip() + "…"
    return f"{type(exc).__name__}: {first}" if first else type(exc).__name__


def _load_locked() -> None:
    """Load the model (caller holds _model_lock), recording the attempt for model_status()."""
    global _model, _torch_default_threads
    _load_state["started"] = time.monotonic()
    try:
        # Pin torch to a single thread: model.encode() deadlocks when run inside an MCP
        # server's worker thread with the default multi-thread intra-op parallelism.
        # Kept inside the lock so it runs exactly once, with the load. torch's own default is
        # remembered first: the bulk path (generate_embeddings) raises the count to it for its encodes.
        try:
            import torch
            if _torch_default_threads is None:
                _torch_default_threads = int(torch.get_num_threads())
            torch.set_num_threads(1)
        except Exception:
            pass
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(MODEL_NAME)
        _load_state["failed_at"] = None
        _load_state["error"] = None
    except BaseException as exc:
        _load_state["failed_at"] = time.monotonic()
        _load_state["error"] = short_error(exc)
        raise
    finally:
        _load_state["started"] = None


def _get_model():
    # Double-checked locking: two concurrent first-requests must not BOTH load the model
    # (wasteful) — the long-lived MCP server can field overlapping first calls.
    if _model is None:
        with _model_lock:
            if _model is None:
                _load_locked()
    return _model


def model_status() -> dict:
    """A NON-blocking snapshot of the model for the long-lived servers (never waits on a load):
    {"state": "ready"} | {"state": "loading", "for_s"} | {"state": "failed", "ago_s", "error"} |
    {"state": "cold"} (never attempted in this process)."""
    if _model is not None:
        return {"state": "ready"}
    now = time.monotonic()
    started, failed_at = _load_state["started"], _load_state["failed_at"]
    if started is not None:
        return {"state": "loading", "for_s": max(0.0, now - started)}
    if failed_at is not None:
        return {"state": "failed", "ago_s": max(0.0, now - failed_at), "error": _load_state["error"]}
    return {"state": "cold"}


def ensure_model(max_wait_s: "float | None" = None, retry_after_s: "float | None" = None):
    """The model for a long-lived server, WITHOUT queueing behind a hopeless load:
      * loaded                                   -> the model;
      * the last load failed < `retry_after_s` ago -> ModelUnavailable at once (None: always retry);
      * another thread is loading                -> wait for it, but only until that load is `max_wait_s`
                                                    old (0: don't wait; None: wait indefinitely), then
                                                    ModelUnavailable;
      * otherwise                                -> load it now in this thread (the caller bounds the wait).
    A load that fails while this caller waited is not immediately re-attempted by it (ModelUnavailable).
    `_get_model()` is unchanged for one-shot callers (sync, the CLI)."""
    if _model is not None:
        return _model
    now = time.monotonic()
    st = model_status()
    if st["state"] == "failed" and retry_after_s is not None and st["ago_s"] < retry_after_s:
        raise ModelUnavailable(f"the embedding model {MODEL_NAME} could not be loaded ({st['error']})")
    if st["state"] == "loading":
        wait = None if max_wait_s is None else max_wait_s - st["for_s"]
        if wait is not None and wait <= 0:
            raise ModelUnavailable(f"the embedding model {MODEL_NAME} is still loading after "
                                   f"{st['for_s']:.0f}s")
    else:
        wait = max_wait_s
    got = _model_lock.acquire() if wait is None else (
        _model_lock.acquire(timeout=wait) if wait > 0 else _model_lock.acquire(blocking=False))
    if not got:
        st = model_status()
        raise ModelUnavailable(f"the embedding model {MODEL_NAME} is still loading after "
                               f"{st.get('for_s', 0.0):.0f}s")
    try:
        if _model is None:
            failed_at = _load_state["failed_at"]
            if retry_after_s is not None and failed_at is not None and failed_at >= now:
                raise ModelUnavailable(f"the embedding model {MODEL_NAME} could not be loaded "
                                       f"({_load_state['error']})")
            _load_locked()
        return _model
    finally:
        _model_lock.release()


def compose_text(record: dict[str, Any]) -> str:
    """Build the document text to embed from a node record: the INTENT SUMMARY, not raw code.

    Prefers body_summary, then docstring, then summary; appends a rationale clause if present.
    """
    name = record.get("name") or ""
    summary = (record.get("body_summary") or record.get("docstring")
               or record.get("summary") or "")
    rationale = record.get("rationale") or ""
    text = f"{DOC_PREFIX}{name}: {summary}".rstrip(": ").rstrip()
    if rationale:
        text = f"{text}. {rationale}"
    return text


def content_hash(text: str, model_name: str = MODEL_NAME) -> str:
    """16-char hex, SALTED by model name so a model swap invalidates every cached embedding."""
    return hashlib.sha256(f"{model_name}:{text}".encode("utf-8")).hexdigest()[:16]


def embed_query(text: str) -> list[float]:
    """Embed a query with the asymmetric 'search_query: ' prefix. -> 768-d list[float]."""
    model = _get_model()
    with _encode_lock:
        vec = model.encode(QUERY_PREFIX + text, show_progress_bar=False)
    return [float(x) for x in vec]


def _embed_doc(text: str) -> list[float]:
    return [float(x) for x in _get_model().encode(text, show_progress_bar=False)]


def _auto_bulk_threads() -> int:
    """DEEPMAP_EMBED_THREADS=auto: round 8's machine-dependent count — torch's own intra-op default (read at the
    first model load, before the load pins it to 1; half the logical CPUs when that is unknown), capped at
    _BULK_THREADS_AUTO_MAX. An explicit opt-in: its vectors differ between machines at float rounding."""
    base = _torch_default_threads or max(1, (os.cpu_count() or 2) // 2)
    return max(1, min(int(base), _BULK_THREADS_AUTO_MAX))


def bulk_embed_threads() -> int:
    """The torch intra-op thread count for generate_embeddings' batched encodes (sync / setup — never the MCP
    servers or a query). DEEPMAP_EMBED_THREADS when it is a positive integer (capped at 64; 1 = the single-thread
    vectors of builds before round 8, bit for bit) or `auto` (round 8's count: torch's own default capped at 8);
    else the FIXED _BULK_THREADS_DEFAULT (8) on every machine — never the core count (round 9 K13), because the
    vectors follow the thread count: encodes at one fixed count are bit-identical whatever the CPUs, a different
    count moves a vector at float rounding (enough to swap near-tied ranks). An unusable value is ignored, with a
    one-line note, for the fixed default. A pure function of DEEPMAP_EMBED_THREADS except for `auto`."""
    raw = os.environ.get("DEEPMAP_EMBED_THREADS")
    text = (raw or "").strip()
    if not text:
        return _BULK_THREADS_DEFAULT
    if text.isascii() and text.isdigit():
        digits = text.lstrip("0")
        if digits:   # int() of a very long digit string raises (the 4300-digit limit): anything this long is > 64
            return _BULK_THREADS_LIMIT if len(digits) > 3 else min(int(digits), _BULK_THREADS_LIMIT)
    elif text.lower() == "auto":
        return _auto_bulk_threads()
    if text not in _threads_env_noted:
        _threads_env_noted.add(text)
        try:
            print(f"[deepmap embed] DEEPMAP_EMBED_THREADS={raw!r} is not a positive integer or 'auto'; ignored "
                  f"(using the fixed default, {_BULK_THREADS_DEFAULT} threads).", file=sys.stderr, flush=True)
        except Exception:  # noqa: BLE001 — a note must never break an embed
            pass
    return _BULK_THREADS_DEFAULT


def _usable_cpus() -> "int | None":
    """How many CPUs this process may run on (affinity-aware where the platform tells), or None. Used ONLY for the
    one-line note of _fewer_cpus_note — it never changes the thread count (that would make vectors machine-bound)."""
    try:
        probe = getattr(os, "process_cpu_count", None)          # 3.13+: the affinity mask on Linux (Windows: all)
        n = probe() if probe is not None else None
        if not n and hasattr(os, "sched_getaffinity"):          # Linux on older Pythons
            n = len(os.sched_getaffinity(0))
        n = n or os.cpu_count()
        return int(n) if n else None
    except Exception:  # noqa: BLE001 — a note must never break an embed
        return None


def _fewer_cpus_note(threads: "int | None") -> str:
    """'' unless the fixed default applies (DEEPMAP_EMBED_THREADS unset) and this process may use fewer CPUs than
    it; then one clause for a large job's start line that explains the count and names the override. It makes no
    speed claim: on 2 CPUs, 8 threads measured no slower than 2 or 1 (see _BULK_THREADS_DEFAULT). Output only."""
    if not threads or (os.environ.get("DEEPMAP_EMBED_THREADS") or "").strip():
        return ""
    cpus = _usable_cpus()
    if not cpus or cpus >= threads:
        return ""
    return (f" (a fixed count, the same on every machine so a corpus embeds alike everywhere; this machine has "
            f"{cpus} CPU{'' if cpus == 1 else 's'} — DEEPMAP_EMBED_THREADS sets another count, whose vectors then "
            f"differ at float rounding)")


@contextlib.contextmanager
def _torch_threads(n: "int | None"):
    """Run the block with torch on `n` intra-op threads and put the previous count back afterwards (also on
    an error). A no-op for n None/0, or when torch is not loaded — a fake model never makes this import torch."""
    torch = sys.modules.get("torch") if n else None
    before = None
    if torch is not None:
        try:
            current = int(torch.get_num_threads())
            if current != int(n):
                torch.set_num_threads(int(n))
                before = current
        except Exception:  # noqa: BLE001 — a thread-count problem must never fail the encode
            before = None
    try:
        yield
    finally:
        if before is not None:
            try:
                torch.set_num_threads(before)
            except Exception:  # noqa: BLE001
                pass


def _embed_docs(texts: list[str], threads: "int | None" = None) -> list[list[float]]:
    """Embed a LIST of doc texts in ONE vectorized model call (SentenceTransformer.encode sub-batches
    internally). Padding is masked, so a text's vector matches encoding it alone to float rounding —
    but NOT bit-for-bit: the padded length of its sub-batch changes the kernels' accumulation order
    (measured: 0/50 bit-equal batch vs single). Which texts share a call therefore matters for exact
    reproducibility; generate_embeddings makes the chunks a pure function of the corpus. Pays the fixed
    per-call overhead (tokenizer/tensor/forward-launch) ONCE per chunk instead of once per node (the
    prior per-item loop was ~an order of magnitude slower on large repos). Returns 768-d float lists
    aligned with `texts`. `threads` (the bulk path: bulk_embed_threads()) runs this ONE encode on that many
    torch threads, under the encode lock, then restores the previous count; None keeps the current count."""
    if not texts:
        return []
    model = _get_model()      # load FIRST: the load pins torch to 1 thread, the bulk count is applied after it
    with _encode_lock, _torch_threads(threads):
        arr = model.encode(texts, show_progress_bar=False)
    return [[float(x) for x in vec] for vec in arr]


def _insert_order(pending: list[tuple]) -> list[tuple]:
    """The order to embed + insert `pending` [(id, text, chash, key)] in: a pseudo-random permutation (HNSW
    insert-order quality, as before) that is a PURE FUNCTION OF THE CORPUS (v0.28.0). The unseeded
    module-level `random.shuffle` made fresh builds of the same corpus differ — it decides which nodes share
    an encode chunk, and a batched encode is not bit-identical across chunk compositions (119/571 vectors
    differed between two fresh builds; near-tied ranks 4-10 reordered). So: sort CANONICALLY first — by node
    key, then content hash, never by elementId or by the order Neo4j returned the rows (both differ between
    instances; the elementId is only the last tie-break, between nodes with the same key and text, whose
    vectors are identical anyway) — then shuffle with a PRIVATE RNG seeded from that canonical list. The
    process-global `random` state is neither consumed nor reseeded."""
    canon = sorted(pending, key=lambda p: (p[3] or "", p[2], p[1], p[0] or ""))
    digest = hashlib.sha256()
    for _id, _text, chash, key in canon:
        digest.update(f"{key or ''}{chash}".encode("utf-8", "surrogatepass"))
    random.Random(int.from_bytes(digest.digest()[:8], "big")).shuffle(canon)
    return canon


# Progress for a long embed (round 7, T4d-I): a large repo's first build embedded for 15-68 minutes without a
# word. Every DEEPMAP_EMBED_PROGRESS_S seconds (30; 0 = off) generate_embeddings prints one stderr line — done/
# total, rate, time left — and a job of _PROGRESS_ANNOUNCE_MIN+ nodes announces itself up front. A run that
# finishes within the interval prints nothing. Output only: what is embedded, and in what order, is unchanged.
_PROGRESS_EVERY_S = 30.0
_PROGRESS_ANNOUNCE_MIN = 1000
_progress_clock = time.monotonic   # injectable for tests


def _progress_every_s() -> float:
    import os
    try:
        return float(os.environ.get("DEEPMAP_EMBED_PROGRESS_S", _PROGRESS_EVERY_S))
    except (TypeError, ValueError):
        return _PROGRESS_EVERY_S


def _plural_threads(n: int) -> str:
    return f"{n} CPU thread" + ("" if n == 1 else "s")


def _duration(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    return f"{minutes}m{seconds:02d}s" if minutes < 60 else f"{minutes // 60}h{minutes % 60:02d}m"


class _EmbedProgress:
    """Time-based progress lines for generate_embeddings (stderr, flushed). Never raises."""

    def __init__(self, total: int, threads: "int | None" = None, note: str = ""):
        self.out = sys.stderr
        self.total = total
        self.every = _progress_every_s()
        self.start = self.last = _progress_clock()
        self.printed = False
        on = f" on {_plural_threads(threads)}{note}" if threads else ""
        if self.every > 0 and total >= _PROGRESS_ANNOUNCE_MIN:
            self._say(f"embedding {total:,} nodes{on} — a large first build can take a while; progress every "
                      f"{self.every:g}s")

    def _say(self, text: str) -> None:
        try:
            print(f"[deepmap embed] {text}", file=self.out, flush=True)
            self.printed = True
        except Exception:  # noqa: BLE001 — progress output must never break an embed
            pass

    def update(self, done: int) -> None:
        if self.every <= 0 or not self.total:
            return
        now = _progress_clock()
        if now - self.last < self.every:
            return
        self.last = now
        elapsed = max(now - self.start, 1e-9)
        rate = done / elapsed
        left = f"about {_duration((self.total - done) / rate)} left" if rate > 0 else "time left unknown"
        self._say(f"{done:,}/{self.total:,} nodes embedded ({100 * done // self.total}%), {rate:.1f} nodes/s, {left}")

    def finish(self, done: int) -> None:
        if self.printed:
            self._say(f"done: {done:,} nodes embedded in {_duration(_progress_clock() - self.start)}")


def generate_embeddings(driver, batch_size: int = 100, force: bool = False) -> int:
    """Embed every summarizable node whose content changed, across all embeddable labels.

    Re-embeds a node only when its `_embed_hash` != the freshly-computed `_content_hash`
    (model-name salted) — unless `force`. A seeded shuffle before insert improves HNSW graph
    quality; its seed and the order it permutes are derived from the corpus (`_insert_order`), so
    fresh builds of the same corpus get the same encode chunks, bit-identical vectors and the same
    insert order. Bare-name nodes (no summary text) are skipped. Returns the count embedded.

    The batched encodes run on bulk_embed_threads() torch threads (round 8, J6: the load's one-thread pin is
    for the MCP servers and queries; single-threaded, a 3,170-node first build spent ~25 of its 30 minutes
    here, and 1 -> 6 threads measured 3.7 -> 7.9 nodes/s) — one count for the whole run, restored after every
    encode. What is embedded, the chunks and the insert order do not depend on it; the vectors do only at float
    rounding, and not at all for a fixed count — which is why the default is a FIXED 8, not the machine's core
    count (round 9, K13): the same corpus embeds to the same vectors on every machine.
    """
    labels = "|".join(EMBEDDABLE_LABELS)
    with driver.session() as s:
        rows = list(s.run(
            f"MATCH (n) WHERE any(l IN labels(n) WHERE l IN $labels) "
            "AND (n.body_summary IS NOT NULL OR n.docstring IS NOT NULL OR n.summary IS NOT NULL) "
            "RETURN elementId(n) AS id, n.name AS name, n.body_summary AS body_summary, "
            "n.docstring AS docstring, n.summary AS summary, n.rationale AS rationale, "
            "n._embed_hash AS embed_hash, n.key AS key",
            labels=EMBEDDABLE_LABELS))

    pending = []
    for r in rows:
        text = compose_text(dict(r))
        chash = content_hash(text)
        if force or r.get("embed_hash") != chash:
            key = r.get("key")
            pending.append((r["id"], text, chash, key if isinstance(key, str) else None))

    pending = _insert_order(pending)  # HNSW insert-order quality, reproducible per corpus
    embedded = 0
    threads = None
    if pending:
        _get_model()                      # load first: the load pins torch to 1 thread (servers / queries) and records
        threads = bulk_embed_threads()    # torch's default (only DEEPMAP_EMBED_THREADS=auto reads it) — fixed for the run
    shown = threads if "torch" in sys.modules else None
    # stderr lines on a long run only (+ the fewer-CPUs note on its start line); changes nothing embedded
    progress = _EmbedProgress(len(pending), shown, _fewer_cpus_note(shown))
    for i in range(0, len(pending), batch_size):
        batch = [(node_id, text, chash) for node_id, text, chash, _key in pending[i:i + batch_size]]
        vectors = _embed_docs([text for _id, text, _h in batch], threads=threads)  # ONE vectorized encode per chunk
        with driver.session() as s:
            for (node_id, text, chash), emb in zip(batch, vectors):
                s.run("MATCH (n) WHERE elementId(n) = $id "
                      "SET n.embedding = $emb, n._embed_hash = $h, n._content_hash = $h",
                      id=node_id, emb=emb, h=chash)
                embedded += 1
        progress.update(embedded)
    progress.finish(embedded)
    return embedded
