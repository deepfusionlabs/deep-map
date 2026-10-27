#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j gates for ROUND-9 K13 (W4): the bulk-embedding thread count is a FIXED default, not the machine's.

THE DEFECT (review_r8, full-suite lens, confirmed x2): round 8 (J6) made bulk embedding (sync / setup)
multi-threaded with `bulk_embed_threads()` defaulting to torch's OWN intra-op count — the physical cores (or
OMP_NUM_THREADS) — capped at 8. The vectors depend on that COUNT: on the real model and the pinned 571-node
STAGE-0 corpus, 4 vs 8 threads left only 435/571 vectors bit-identical and swapped ranks 5/6 of the frozen
oracle query 'acquire a single-writer lock before rebuilding'. So the same corpus embedded differently on a
4-core and an 8-core machine, `tests/stage0_refreeze.py --check` could fail with no code change (its
child_env strips every DEEPMAP_* knob, so DEEPMAP_EMBED_THREADS could not pin it), and benchmark ON-arm graphs
depended on the CPU.

THE FIX (binding K13): the default is ONE fixed count on every machine (8 — round 8's cap, so a machine that
already ran 8 embeds exactly as before; the reviewer measured that 8 keeps the 1-thread oracle's arm order on
all 35 queries, and 1 -> 6-8 threads is ~2x faster); DEEPMAP_EMBED_THREADS overrides it (a positive integer,
capped at 64; `auto` = round 8's machine-dependent count, an explicit opt-in). Neither the core count, the
usable-CPU count nor OMP_NUM_THREADS moves the default. Measured for this round on the real model (64 real
docstrings, torch 2.12 / MKL, CPU-affinity masks): a FIXED count is bit-identical with 12, 4 or 2 usable
CPUs (64/64 at 8, at 4 and at 1 thread) — the vectors follow the thread COUNT, never the CPUs. The query /
MCP-server path stays on ONE thread (unchanged). A large first build on a machine with fewer usable CPUs than
the fixed count says so once, naming the override (output only).

Hermetic: torch and sentence_transformers are faked in sys.modules; the machine (torch default, os.cpu_count,
os.process_cpu_count, os.sched_getaffinity) is simulated; no model, no Neo4j, no network.
"""
from __future__ import annotations

import ast
import contextlib
import importlib.util
import io
import os
import sys
import time
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build"
TESTS = REPO / "tests"

_PASS = 0
_FAIL = 0
_MISSING = object()


def check(label: str, cond: bool, detail: object = "") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {label}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}{(' — ' + str(detail)) if detail != '' else ''}")


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


@contextlib.contextmanager
def _machine(cpus: int):
    """Simulate a machine with `cpus` usable CPUs for every stdlib probe an implementation might use."""
    names = ("cpu_count", "process_cpu_count", "sched_getaffinity")
    saved = {n: getattr(os, n, _MISSING) for n in names}
    try:
        os.cpu_count = lambda: cpus
        os.process_cpu_count = lambda: cpus
        os.sched_getaffinity = lambda pid=0: set(range(cpus))
        yield
    finally:
        for n, v in saved.items():
            if v is _MISSING:
                with contextlib.suppress(AttributeError):
                    delattr(os, n)
            else:
                setattr(os, n, v)


class _FakeTorch(types.ModuleType):
    """torch's thread API only (a process-global, like the real one). `default` = what the real torch reports
    before anyone sets it: the machine's physical cores, or OMP_NUM_THREADS."""

    def __init__(self, default: int):
        super().__init__("torch")
        self.n = default
        self.sets: list = []

    def get_num_threads(self):
        return self.n

    def set_num_threads(self, n):
        self.sets.append(int(n))
        self.n = int(n)


class _World:
    def __init__(self, torch_default: int):
        self.torch = _FakeTorch(torch_default)
        self.encodes: list = []     # (kind, n_texts, torch threads at encode time)


def _fake_st(w: _World) -> types.ModuleType:
    """A model whose vectors depend on the torch thread count at encode time — exactly the real model's
    behaviour (a different count moves a vector at float rounding; a fixed count is bit-identical)."""
    m = types.ModuleType("sentence_transformers")

    class SentenceTransformer:
        def __init__(self, name):
            self.name = name

        def encode(self, texts, show_progress_bar=False):
            single = isinstance(texts, str)
            batch = [texts] if single else list(texts)
            w.encodes.append(("query" if single else "bulk", len(batch), w.torch.n))
            rounding = w.torch.n * 1e-7
            vecs = [[float(len(t) % 7) + rounding, float(sum(map(ord, t)) % 11)] + [0.0] * 766 for t in batch]
            return vecs[0] if single else vecs

    m.SentenceTransformer = SentenceTransformer
    return m


@contextlib.contextmanager
def _embeddings(w: "_World | None"):
    """A fresh build/embeddings.py; with `w`, its `import torch` / `sentence_transformers` are the fakes."""
    saved = {k: sys.modules.get(k) for k in ("torch", "sentence_transformers")}
    if w is not None:
        sys.modules["torch"] = w.torch
        sys.modules["sentence_transformers"] = _fake_st(w)
    else:
        sys.modules.pop("torch", None)
    try:
        spec = importlib.util.spec_from_file_location(f"dm_emb_thr_r9b_{time.monotonic_ns()}", BUILD / "embeddings.py")
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
                drv.sets.append((p.get("id"), tuple(p.get("emb") or ())[:3], p.get("h")))
                return []
        return _S()


def _bulk_threads(w: _World) -> list:
    return [n for kind, _c, n in w.encodes if kind == "bulk"]


def _build(torch_default: int, cpus: int, env_threads=None, n_nodes=250, progress_s="0", omp=None):
    """One simulated machine's sync embed: -> (world, driver, stderr, torch count afterwards)."""
    w = _World(torch_default)
    with _machine(cpus), _env(DEEPMAP_EMBED_THREADS=env_threads, DEEPMAP_EMBED_PROGRESS_S=progress_s,
                              OMP_NUM_THREADS=omp, MKL_NUM_THREADS=omp):
        with _embeddings(w) as E:
            drv = _Drv(n_nodes)
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                E.generate_embeddings(drv, batch_size=100)
            after = w.torch.n
            E.embed_query("where is auth handled")
    return w, drv, err.getvalue(), after


# Simulated machines: (torch's own default, usable CPUs) — a 2-vCPU CI runner, a 4-core laptop, an 8-core
# desktop, a 10-core/12-thread dev box (this repo's), a 32-core workstation, and one with OMP_NUM_THREADS=3.
MACHINES = [(1, 2), (2, 4), (4, 8), (6, 12), (8, 16), (10, 12), (16, 32), (32, 64)]


def gate_default_is_fixed() -> None:
    print("\n-- the default bulk count is ONE fixed value on every machine (was: torch's own count, capped at 8) --")
    got = {}
    for td, cpus in MACHINES:
        w, _d, _e, _a = _build(td, cpus)
        got[(td, cpus)] = _bulk_threads(w)
    values = {tuple(v) for v in got.values()}
    check("every simulated machine runs its bulk encodes on the SAME thread count", len(values) == 1, got)
    check("…and that count is 8 on each of them (round 8's cap: a >=8-core machine embeds exactly as before)",
          all(v == [8, 8, 8] for v in got.values()), got)
    w, _d, _e, _a = _build(3, 4, omp="3")
    check("OMP_NUM_THREADS / MKL_NUM_THREADS=3 (torch default 3) do not move the default either",
          _bulk_threads(w) == [8, 8, 8], _bulk_threads(w))


def gate_default_without_torch_default() -> None:
    print("\n-- bulk_embed_threads() before any model load (torch default unknown) ignores os.cpu_count too --")
    vals = {}
    for cpus in (1, 2, 4, 12, 64):
        with _machine(cpus), _env(DEEPMAP_EMBED_THREADS=None):
            with _embeddings(None) as E:
                vals[cpus] = E.bulk_embed_threads()
    check("the same value for 1, 2, 4, 12 and 64 CPUs (was cpu_count // 2)", len(set(vals.values())) == 1, vals)
    check("…the fixed default, 8", set(vals.values()) == {8}, vals)
    with _embeddings(None) as E:
        check("the fixed default is a named module constant (_BULK_THREADS_DEFAULT == 8)",
              getattr(E, "_BULK_THREADS_DEFAULT", None) == 8, getattr(E, "_BULK_THREADS_DEFAULT", "missing"))


def gate_same_vectors_across_machines() -> None:
    print("\n-- THE property: the same corpus embeds to the same vectors, chunks and insert order on every machine --")
    payloads = {}
    for td, cpus in MACHINES:
        _w, drv, _e, _a = _build(td, cpus)
        payloads[(td, cpus)] = drv.sets
    ref = payloads[MACHINES[0]]
    diff = [m for m, p in payloads.items() if p != ref]
    check("identical (id, vector, hash) writes, in the same order, on all 8 simulated machines "
          "(the fake model moves a vector with the thread count, like the real one)", not diff and len(ref) == 250,
          f"differ: {diff}")
    _w, drv3, _e, _a = _build(4, 8, env_threads="3")
    _w, drv3b, _e, _a = _build(16, 32, env_threads="3")
    check("an explicit DEEPMAP_EMBED_THREADS=3 is machine-independent too", drv3.sets == drv3b.sets)
    check("…and differs from the default's vectors only through the thread count (same ids / hashes / order)",
          [(i, h) for i, _v, h in drv3.sets] == [(i, h) for i, _v, h in ref] and drv3.sets != ref)


def gate_override() -> None:
    print("\n-- DEEPMAP_EMBED_THREADS overrides the fixed default --")
    for val, want in (("1", 1), ("3", 3), ("16", 16), (" 4 ", 4), ("08", 8), ("999", 64), ("9" * 5000, 64)):
        label = (f"DEEPMAP_EMBED_THREADS={val[:12]!r} -> {want} on a small and a large machine" if len(val) <= 12
                 else f"a {len(val)}-digit DEEPMAP_EMBED_THREADS -> {want}, no int() crash, on either machine")
        try:
            a, _d, _e, _x = _build(2, 4, env_threads=val)
            b, _d, _e, _x = _build(32, 64, env_threads=val)
        except Exception as exc:  # noqa: BLE001 — a crash is a FAIL, not an aborted gate
            check(label, False, f"raised {type(exc).__name__}: {str(exc)[:120]}")
            continue
        check(label, _bulk_threads(a) == _bulk_threads(b) == [want] * 3, (_bulk_threads(a), _bulk_threads(b)))
    for bad in ("abc", "0", "0000", "-2", "2.5", "٣", "auto-ish"):
        w, _d, err, _x = _build(4, 8, env_threads=bad)
        check(f"DEEPMAP_EMBED_THREADS={bad!r} -> the fixed default (8), not the machine's 4",
              _bulk_threads(w) == [8, 8, 8], _bulk_threads(w))
        check(f"…with a one-line 'ignored' note ({bad!r})", "DEEPMAP_EMBED_THREADS" in err and "ignored" in err,
              err[:200])
    w, _d, err, _x = _build(4, 8, env_threads="   ")
    check("a blank DEEPMAP_EMBED_THREADS is 'unset': the fixed default, no note",
          _bulk_threads(w) == [8, 8, 8] and "DEEPMAP_EMBED_THREADS" not in err, (_bulk_threads(w), err[:200]))


def gate_auto_opt_in() -> None:
    print("\n-- DEEPMAP_EMBED_THREADS=auto: round 8's machine-dependent count, as an explicit opt-in --")
    for td, cpus, want in ((4, 8, 4), (2, 4, 2), (10, 12, 8), (32, 64, 8)):
        w, _d, err, _x = _build(td, cpus, env_threads="auto")
        check(f"auto on a machine whose torch default is {td} -> {want} (torch's own count, capped at 8)",
              _bulk_threads(w) == [want] * 3, _bulk_threads(w))
        check(f"…and it is not reported as an ignored value (torch default {td})", "ignored" not in err, err[:200])
    w, _d, _e, _x = _build(4, 8, env_threads="AUTO")
    check("'AUTO' is accepted case-insensitively", _bulk_threads(w) == [4, 4, 4], _bulk_threads(w))


def gate_query_path_single_thread() -> None:
    print("\n-- the query / MCP-server path stays on ONE thread (unchanged) --")
    for td, cpus in ((2, 4), (32, 64)):
        w, _d, _e, after = _build(td, cpus)
        check(f"torch is back on 1 thread after the bulk run (torch default {td})", after == 1, after)
        check(f"…and the query encode ran on 1 thread (torch default {td})", w.encodes[-1] == ("query", 1, 1),
              w.encodes[-1:])
    w = _World(32)
    with _env(DEEPMAP_EMBED_THREADS=None):
        with _embeddings(w) as E:
            E.ensure_model(max_wait_s=0, retry_after_s=300)
            check("the servers' ensure_model load pins torch to 1 thread", w.torch.n == 1, w.torch.n)
            E.embed_query("x")
            check("…and a server query encode runs on 1 thread", w.encodes[-1] == ("query", 1, 1), w.encodes[-1:])
    for name in ("deepmap_search_server.py", "deepmap_embed_server.py"):
        tree = ast.parse((BUILD / name).read_text(encoding="utf-8"))
        names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)} | {
            n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        check(f"{name} never reaches the bulk path or its thread count",
              not ({"generate_embeddings", "bulk_embed_threads", "_embed_docs"} & names))


def gate_small_machine_note() -> None:
    print("\n-- a large first build on a machine with fewer CPUs than the fixed count says so once (output only) --")
    w, drv_small, err, _x = _build(2, 2, n_nodes=1200, progress_s=None)
    lines = [ln for ln in err.splitlines() if "[deepmap embed]" in ln]
    start = [ln for ln in lines if "embedding 1,200 nodes" in ln]
    check("the start line says 8 CPU threads", bool(start) and "8 CPU threads" in start[0], lines[:2])
    check("…and, on a 2-CPU machine, names the DEEPMAP_EMBED_THREADS override and the CPU count",
          bool(start) and "DEEPMAP_EMBED_THREADS" in start[0] and "2 CPUs" in start[0], lines[:2])
    check("…exactly once", sum("DEEPMAP_EMBED_THREADS" in ln for ln in lines) == 1, lines)
    check("…and it makes no speed claim (pinned to 2 CPUs, 8 threads measured no slower than 2 or 1)",
          bool(start) and not any(w in start[0].lower() for w in ("faster", "slower", "speed")), lines[:1])
    check("…the encodes still ran on the fixed 8", set(_bulk_threads(w)) == {8}, _bulk_threads(w))
    _w, drv_big, err_big, _x = _build(10, 12, n_nodes=1200, progress_s=None)
    check("a 12-CPU machine gets no override note", "DEEPMAP_EMBED_THREADS" not in err_big, err_big[:300])
    check("the note changes nothing embedded (same writes as the 12-CPU machine)", drv_small.sets == drv_big.sets)
    _w, _d, err_set, _x = _build(2, 2, env_threads="2", n_nodes=1200, progress_s=None)
    check("no override note once DEEPMAP_EMBED_THREADS is set", "DEEPMAP_EMBED_THREADS" not in err_set, err_set[:300])
    _w, _d, err_q, _x = _build(2, 2, n_nodes=300, progress_s=None)
    check("a small job on a small machine stays quiet", "[deepmap embed]" not in err_q, err_q[:300])
    _w, _d, err_off, _x = _build(2, 2, n_nodes=1200, progress_s="0")
    check("DEEPMAP_EMBED_PROGRESS_S=0 keeps it quiet too", "[deepmap embed]" not in err_off, err_off[:300])


def gate_stage0_harness() -> None:
    print("\n-- the STAGE-0 re-freeze harness (child_env strips DEEPMAP_*) now gets the same count everywhere --")
    sys.path.insert(0, str(TESTS))
    try:
        import stage0_refreeze as R
    finally:
        sys.path.remove(str(TESTS))
    child = R.child_env({**os.environ, "DEEPMAP_EMBED_THREADS": "3"})
    check("child_env drops an operator's DEEPMAP_EMBED_THREADS (the harness runs the DEFAULT pipeline)",
          "DEEPMAP_EMBED_THREADS" not in child)
    counts = {}
    for td, cpus in MACHINES:
        w, _d, _e, _a = _build(td, cpus, env_threads=child.get("DEEPMAP_EMBED_THREADS"))
        counts[(td, cpus)] = _bulk_threads(w)
    check("…so every machine's --check / --freeze embeds on one count (8) — the oracle's fused order is "
          "reproducible across machines", {tuple(v) for v in counts.values()} == {(8, 8, 8)}, counts)


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            s.reconfigure(encoding="utf-8")
    print("embed_threads_r9b_gates — ROUND-9 K13: a fixed, machine-independent bulk-embedding thread count")
    gate_default_is_fixed()
    gate_default_without_torch_default()
    gate_same_vectors_across_machines()
    gate_override()
    gate_auto_opt_in()
    gate_query_path_single_thread()
    gate_small_machine_note()
    gate_stage0_harness()
    total = _PASS + _FAIL
    print(f"\nRESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
