#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j gates for ROUND-8 J6 (W4): bulk embedding (sync/setup) uses several CPU threads; queries stay on one.

THE DEFECT (round-7 live validation, L1): build/embeddings.py:_load_locked pinned torch to ONE intra-op thread
for EVERY process — the workaround for model.encode() deadlocking inside an MCP server's worker thread — so
sync/setup's first build embedded single-threaded too: 3,170 nodes at 0.8-1.8 nodes/s, ~25.5 of setup's 30.3
minutes. Standalone: 3.7 nodes/s at 1 thread vs 7.9 nodes/s at 6 threads (~2.1x).

THE FIX (binding J6 — only the bulk path, NOT the MCP servers; deterministic for a fixed thread count):
  * the model load still pins torch to 1 thread (the servers' and every query encode's setting — unchanged);
  * generate_embeddings raises the count to bulk_embed_threads() for its batched encodes and restores it after
    each one (also on an error), holding the encode lock meanwhile, so a query encode never runs on the bulk count;
  * bulk_embed_threads() = DEEPMAP_EMBED_THREADS (1..64) when set, else a FIXED 8 on every machine (round 9 K13;
    round 8 used torch's own default capped at 8, which made the vectors machine-dependent — that count is now the
    explicit DEEPMAP_EMBED_THREADS=auto, see tests/embed_threads_r9b_gates.py) — ONE fixed count per run, so a
    rebuild is bit-identical
    (measured on the real model, 300 real docstring texts: two separate processes at 8 threads bit-identical;
    1 vs 8 threads differ only at float rounding, max |diff| 4.3e-06, top-15 neighbour order identical for 300/300
    probes; 0.76 -> 2.3 nodes/s on a heavily loaded box). DEEPMAP_EMBED_THREADS=1 reproduces a direct
    single-thread encode of the same chunks bit for bit;
  * a fake model with no torch loaded is not affected (nothing imports torch just to set threads).

Hermetic: torch and sentence_transformers are faked in sys.modules; no model, no Neo4j.
"""
from __future__ import annotations

import ast
import contextlib
import importlib.util
import io
import os
import sys
import threading
import time
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build"

_PASS = 0
_FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {label}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}{(' — ' + str(detail)) if detail else ''}")


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


class _FakeTorch(types.ModuleType):
    """torch's thread API only: get_num_threads / set_num_threads (a process-global, like the real one)."""

    def __init__(self, default: int = 10):
        super().__init__("torch")
        self.n = default
        self.sets: list = []

    def get_num_threads(self):
        return self.n

    def set_num_threads(self, n):
        self.sets.append(int(n))
        self.n = int(n)


class _World:
    def __init__(self, torch_default=10):
        self.torch = _FakeTorch(torch_default)
        self.encodes: list = []          # (kind, n_texts, torch threads at encode time)
        self.block: "threading.Event | None" = None
        self.entered = threading.Event()
        self.fail_on: "int | None" = None


def _fake_st(w: _World) -> types.ModuleType:
    m = types.ModuleType("sentence_transformers")

    class SentenceTransformer:
        def __init__(self, name):
            self.name = name

        def encode(self, texts, show_progress_bar=False):
            single = isinstance(texts, str)
            batch = [texts] if single else list(texts)
            kind = "query" if single else "bulk"
            w.encodes.append((kind, len(batch), w.torch.n))
            if kind == "bulk":
                w.entered.set()
                if w.block is not None:
                    w.block.wait(10)
                if w.fail_on is not None and sum(1 for k, *_ in w.encodes if k == "bulk") == w.fail_on:
                    raise RuntimeError("fake encode failure")
            vecs = [[float(len(t) % 7), float(sum(map(ord, t)) % 11)] + [0.0] * 766 for t in batch]
            return vecs[0] if single else vecs

    m.SentenceTransformer = SentenceTransformer
    return m


@contextlib.contextmanager
def _embeddings(w: _World):
    """A fresh build/embeddings.py whose `import torch` / `sentence_transformers` are the fakes of `w`."""
    saved = {k: sys.modules.get(k) for k in ("torch", "sentence_transformers")}
    sys.modules["torch"] = w.torch
    sys.modules["sentence_transformers"] = _fake_st(w)
    try:
        spec = importlib.util.spec_from_file_location(f"dm_emb_thr_r8_{time.monotonic_ns()}", BUILD / "embeddings.py")
        E = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(E)
        yield E
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


class _Drv:
    def __init__(self, n):
        self.rows = [{"id": f"e{i}", "name": f"fn_{i}", "body_summary": f"does thing {i}", "docstring": None,
                      "summary": None, "rationale": None, "embed_hash": None, "key": f"k{i:05d}"} for i in range(n)]
        self.sets: list = []

    def session(self, **kw):
        drv = self

        class _S:
            def __enter__(s):
                return s

            def __exit__(s, *a):
                return False

            def run(s, cypher, **p):
                if "RETURN elementId(n) AS id" in cypher:
                    return list(drv.rows)
                drv.sets.append((p.get("id"), tuple(p.get("emb") or ())[:2], p.get("h")))
                return []
        return _S()


def _bulk_threads(w):
    return [n for kind, _c, n in w.encodes if kind == "bulk"]


def _run_bulk(env_threads=None, torch_default=10, n_nodes=250, preload=True):
    w = _World(torch_default)
    with _env(DEEPMAP_EMBED_THREADS=env_threads, DEEPMAP_EMBED_PROGRESS_S="0"):
        with _embeddings(w) as E:
            if preload:
                E._get_model()
            drv = _Drv(n_nodes)
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                count = E.generate_embeddings(drv, batch_size=100)
            after = w.torch.n
            E.embed_query("where is auth handled")
    return w, count, after, drv, err.getvalue()


def gate_query_pin_kept() -> None:
    print("\n-- the model load still pins torch to ONE thread (MCP servers + every query encode) --")
    w = _World(10)
    with _env(DEEPMAP_EMBED_THREADS=None):
        with _embeddings(w) as E:
            E._get_model()
            check("after the load torch runs on 1 thread", w.torch.n == 1, w.torch.n)
            E.embed_query("x")
            check("a query encode runs on 1 thread", w.encodes[-1] == ("query", 1, 1), w.encodes[-1:])
            E.ensure_model(max_wait_s=0, retry_after_s=300)
            check("ensure_model (the servers' path) leaves torch on 1 thread", w.torch.n == 1, w.torch.n)


def gate_bulk_default() -> None:
    print("\n-- generate_embeddings encodes on several threads (default: a fixed 8, round 9 K13) --")
    w, count, after, _drv, _err = _run_bulk(None, torch_default=10)
    bulk = _bulk_threads(w)
    check("250 nodes embedded in 3 batched encodes", count == 250 and len(bulk) == 3, f"{count} {bulk}")
    check("every bulk encode ran on 8 threads (the fixed default; torch default 10) — was 1", bulk == [8, 8, 8], bulk)
    check("torch is back on 1 thread when generate_embeddings returns", after == 1, after)
    check("a query encode after the bulk run is on 1 thread", w.encodes[-1] == ("query", 1, 1), w.encodes[-1:])
    w4, _c, _a, _d, _e = _run_bulk(None, torch_default=4)
    check("a 4-thread torch default still gives the fixed 8 (K13: never the machine's count)",
          _bulk_threads(w4) == [8, 8, 8], _bulk_threads(w4))


def gate_bulk_cold_model() -> None:
    print("\n-- a cold model (first build) still encodes on the bulk count, not the load's pin --")
    w, count, after, _drv, _err = _run_bulk(None, torch_default=6, preload=False)
    check("the load happened inside generate_embeddings and every bulk encode ran on the fixed 8 (torch default 6)",
          _bulk_threads(w) == [8, 8, 8], _bulk_threads(w))
    check("…and torch is on 1 thread afterwards", after == 1, after)


def gate_env_override() -> None:
    print("\n-- DEEPMAP_EMBED_THREADS fixes the count (1 = the old single-thread vectors exactly) --")
    w3, *_ = _run_bulk("3")
    check("DEEPMAP_EMBED_THREADS=3 -> 3 threads", _bulk_threads(w3) == [3, 3, 3], _bulk_threads(w3))
    w1, *_ = _run_bulk("1")
    check("DEEPMAP_EMBED_THREADS=1 -> every bulk encode on 1 thread", _bulk_threads(w1) == [1, 1, 1], _bulk_threads(w1))
    check("…and torch is never raised above 1 in that run", max(w1.torch.sets or [1]) == 1, w1.torch.sets)
    w99, *_ = _run_bulk("999")
    check("DEEPMAP_EMBED_THREADS=999 is capped at 64", _bulk_threads(w99) == [64, 64, 64], _bulk_threads(w99))
    for bad in ("abc", "0", "-2", "2.5"):
        wb, _c, _a, _d, err = _run_bulk(bad)
        check(f"DEEPMAP_EMBED_THREADS={bad!r} -> the default (8)", _bulk_threads(wb) == [8, 8, 8], _bulk_threads(wb))
        check(f"…and it says the value was ignored ({bad!r})", "DEEPMAP_EMBED_THREADS" in err, err[:200])


def gate_same_vectors_and_order() -> None:
    print("\n-- only the thread count changes: what is embedded, the chunks and the insert order are identical --")
    _w8, c8, _a8, d8, _e8 = _run_bulk(None)
    _w1, c1, _a1, d1, _e1 = _run_bulk("1")
    check("the same node ids, vectors (fake model) and hashes, in the same insert order", d8.sets == d1.sets and c8 == c1,
          f"{len(d8.sets)} vs {len(d1.sets)}")


def gate_restore_on_error() -> None:
    print("\n-- an encode that raises still restores torch to 1 thread --")
    w = _World(10)
    w.fail_on = 2
    with _env(DEEPMAP_EMBED_THREADS=None, DEEPMAP_EMBED_PROGRESS_S="0"):
        with _embeddings(w) as E:
            E._get_model()
            raised = False
            try:
                E.generate_embeddings(_Drv(250), batch_size=100)
            except RuntimeError:
                raised = True
            check("the encode error propagates (sync decides; nothing swallowed)", raised)
            check("torch is back on 1 thread after the failure", w.torch.n == 1, w.torch.n)


def gate_query_never_on_bulk_count() -> None:
    print("\n-- a query encode that arrives DURING a bulk encode waits, then runs on 1 thread --")
    w = _World(10)
    w.block = threading.Event()
    with _env(DEEPMAP_EMBED_THREADS=None, DEEPMAP_EMBED_PROGRESS_S="0"):
        with _embeddings(w) as E:
            E._get_model()
            bulk = threading.Thread(target=E.generate_embeddings, args=(_Drv(150),), kwargs={"batch_size": 100})
            bulk.start()
            w.entered.wait(5)
            q = threading.Thread(target=E.embed_query, args=("concurrent query",))
            q.start()
            time.sleep(0.3)
            early = [e for e in w.encodes if e[0] == "query"]
            check("the query did not encode while the bulk encode held the lock", not early, early)
            w.block.set()
            bulk.join(10)
            q.join(10)
            queries = [e for e in w.encodes if e[0] == "query"]
            check("…it ran afterwards, on 1 thread", queries == [("query", 1, 1)], queries)
            check("the bulk encodes ran on 8 threads", _bulk_threads(w) == [8, 8], _bulk_threads(w))


def gate_no_torch_import_for_fakes() -> None:
    print("\n-- a fake model with no torch loaded: nothing imports torch just to set threads --")
    saved = sys.modules.pop("torch", None)
    blocker = None

    class _Block:
        def find_spec(self, name, path=None, target=None):
            if name == "torch":
                raise ImportError("torch import blocked by the gate")
            return None
    try:
        blocker = _Block()
        sys.meta_path.insert(0, blocker)
        spec = importlib.util.spec_from_file_location(f"dm_emb_thr_r8_nt_{time.monotonic_ns()}", BUILD / "embeddings.py")
        E = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(E)

        class _M:
            def encode(self, texts, show_progress_bar=False):
                return [[0.5] * 768 for _ in texts] if not isinstance(texts, str) else [0.5] * 768

        E._get_model = lambda m=_M(): m
        d = _Drv(120)
        with _env(DEEPMAP_EMBED_THREADS=None, DEEPMAP_EMBED_PROGRESS_S="0"):
            n = E.generate_embeddings(d, batch_size=100)
        check("120 nodes embedded with the fake model", n == 120, n)
        check("torch was never imported", "torch" not in sys.modules)
    finally:
        if blocker is not None:
            sys.meta_path.remove(blocker)
        if saved is not None:
            sys.modules["torch"] = saved


def gate_servers_never_bulk() -> None:
    print("\n-- the MCP servers never reach the bulk path (their encodes stay single-threaded) --")
    for name in ("deepmap_search_server.py", "deepmap_embed_server.py"):
        tree = ast.parse((BUILD / name).read_text(encoding="utf-8"))
        names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)} | {
            n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        check(f"{name} references neither generate_embeddings nor the bulk thread count",
              not ({"generate_embeddings", "bulk_embed_threads", "_embed_docs"} & names))


def main() -> int:
    print("embed_threads_r8_gates — ROUND-8 J6: multi-threaded bulk embedding, single-threaded queries")
    gate_query_pin_kept()
    gate_bulk_default()
    gate_bulk_cold_model()
    gate_env_override()
    gate_same_vectors_and_order()
    gate_restore_on_error()
    gate_query_never_on_bulk_count()
    gate_no_torch_import_for_fakes()
    gate_servers_never_bulk()
    total = _PASS + _FAIL
    print(f"\nRESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
