#!/usr/bin/env python3
"""0.28.3 (O10 / D5) — every .deepmap/instance.json writer is ONE locked, atomic read-modify-write.

Finding (DeepInit refresh 2026-09-29, validated D5): every writer of <root>/.deepmap/instance.json —
tune_weights.write_instance_weights / apply_learned, instance_state._write (behind merge_learned_fusion /
write_experiment: the drift guard, `deepmap learning --enable/--disable`, the seed recommendation, the
offline experiments), weight_ab.write_benchmark_evidence and enrich_ab.write_enrich_evidence — did an
unlocked, non-atomic whole-file `write_text` of a read-modify-write copy. A background post-commit sync's
auto-tune could therefore lose an interactive `deepmap learning` write, and a torn/garbage file read as {}
made the next write drop EVERY other key (learning_enabled, benchmark_evidence, experiments.*).

Locks (behavioral, hermetic — temp dirs only, never ~/.deepmap):
  - a file that is not a JSON object (a torn write, a hand edit, a list) is REFUSED by every writer and
    left byte-identical — never wiped; an EMPTY file holds nothing and is written normally,
  - every write is atomic: a temp file in .deepmap/ + os.replace onto instance.json; no writer calls a
    truncating Path.write_text on instance.json; a failed replace leaves the old file intact and no temp,
  - every writer honours the cross-process lock (<root>/.deepmap/instance.json.lock): while another holder
    has it, the writer does NOT write and gives up within the bounded wait (never hangs); after release
    the same write lands,
  - 4 processes x 25 concurrent merges lose NO update (the old unlocked read-modify-write did),
  - zero-egress: only instance.json + its .lock sidecar under <root>/.deepmap/, no temp leftovers, and the
    frozen live reader (learned_fusion) reads what the new writer wrote.

Usage: python tests/o10_instance_state_lock_r283_gates.py [--root <tree>]
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.dont_write_bytecode = True     # never drop __pycache__ into a --root snapshot

REPO = Path(__file__).resolve().parent.parent
PASSES: list[str] = []
FAILS: list[str] = []

TORN = b'{\n  "learned_fusion": {\n    "learning_enabled": true,\n    "benchmark_evid'   # a truncated write
NOT_OBJECT = b"[1, 2, 3]\n"
GOOD = {"project": "deepmap", "learned_fusion": {"learning_enabled": True,
                                                  "benchmark_evidence": {"present": True}},
        "experiments": {"access_prior": {"applied": False}}}
WIN = {"vector": 0.5, "bm25": 1.5, "graph_weighted": 1.0}


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSES if ok else FAILS).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok or not detail else f" — {detail}"))


def _load(root: Path):
    for sub in ("benchmark", "build"):
        p = str(root / sub)
        if p not in sys.path:
            sys.path.insert(0, p)
    import instance_state as S           # noqa: E402
    import tune_weights as T             # noqa: E402
    import learned_fusion as LF          # noqa: E402
    import learned_prior as LP           # noqa: E402
    import weight_ab as W                # noqa: E402
    import enrich_ab as E                # noqa: E402
    return S, T, LF, LP, W, E


def _inst(root: Path) -> Path:
    return root / ".deepmap" / "instance.json"


def _put(root: Path, raw: bytes) -> Path:
    p = _inst(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(raw)
    return p


def _writers(S, T, LP, W, E):
    """(label, call(root) -> result, succeeded(result) -> bool) for EVERY instance.json writer."""
    tuned = {"arm_weights": dict(WIN), "beats_equal": True, "significant": True, "reason": "learned_beats_equal",
             "objective": "ndcg", "rrf_k": 60, "profiles": {}, "generated_utc": "2026-09-29T00:00:00Z"}
    pooled = {"arm_weights": dict(WIN), "profiles": {}, "n_projects": 2, "n_labeled_total": 300}
    return [
        ("tune_weights.write_instance_weights", lambda r: T.write_instance_weights(r, dict(tuned)),
         lambda x: x is not None),
        ("instance_state.merge_learned_fusion (learning --enable)",
         lambda r: S.merge_learned_fusion(r, {"learning_enabled": False}), lambda x: x is not None),
        ("instance_state.write_experiment", lambda r: S.write_experiment(r, "graph_signal", {"verdict": "ok"}),
         lambda x: x is not None),
        ("learned_prior.write_seed_recommendation", lambda r: LP.write_seed_recommendation(r, dict(pooled)),
         lambda x: bool(x)),
        ("weight_ab.write_benchmark_evidence", lambda r: W.write_benchmark_evidence(r, {"present": True}),
         lambda x: x is not None),
        ("enrich_ab.write_enrich_evidence", lambda r: E.write_enrich_evidence(r, {"present": True}),
         lambda x: x is not None),
        ("tune_weights.apply_drift_guard (drift block)", lambda r: T.apply_drift_guard(r),
         lambda x: isinstance(x, dict) and x.get("written") is True),
    ]


def gate_never_wipes(S, T, LP, W, E) -> None:
    print("\n-- a file that is NOT a JSON object is refused by every writer, never wiped --")
    for label, call, ok in _writers(S, T, LP, W, E):
        for kind, raw in (("torn write", TORN), ("non-object JSON", NOT_OBJECT)):
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                p = _put(root, raw)
                res = call(root)
                check(f"{label}: {kind} left byte-identical (never wiped)", p.read_bytes() == raw,
                      repr(p.read_bytes()[:80]))
                check(f"{label}: {kind} -> reports NOT written", not ok(res), repr(res)[:120])
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        p = _put(root, TORN)
        ok, reason = T.apply_learned(root)
        check("apply_learned on a torn file -> refused, file untouched", ok is False and p.read_bytes() == TORN,
              reason)
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        p = _put(root, b"")
        out = S.merge_learned_fusion(root, {"learning_enabled": True})
        data = json.loads(p.read_text(encoding="utf-8"))
        check("an EMPTY file holds nothing -> the write lands normally",
              out is not None and data == {"learned_fusion": {"learning_enabled": True}}, str(data))


class _Spy:
    def __init__(self):
        self.replaced: list[tuple[str, str]] = []
        self.write_text_targets: list[str] = []


def _with_spies(fn):
    """Run fn() with os.replace + pathlib.Path.write_text wrapped (call-through) and return the spy."""
    spy = _Spy()
    real_replace, real_wt = os.replace, pathlib.Path.write_text

    def replace(src, dst, *a, **k):
        spy.replaced.append((str(src), str(dst)))
        return real_replace(src, dst, *a, **k)

    def write_text(self, *a, **k):
        spy.write_text_targets.append(str(self))
        return real_wt(self, *a, **k)

    os.replace, pathlib.Path.write_text = replace, write_text
    try:
        fn()
    finally:
        os.replace, pathlib.Path.write_text = real_replace, real_wt
    return spy


def gate_atomic(S, T, LP, W, E) -> None:
    print("\n-- every write is temp + os.replace onto instance.json (never a truncating write_text) --")
    for label, call, ok in _writers(S, T, LP, W, E) + [
            ("tune_weights.apply_learned", lambda r: T.apply_learned(r, require_benchmark=False),
             lambda x: isinstance(x, tuple) and x[0] is True)]:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            seed = dict(GOOD)
            seed["learned_fusion"] = dict(GOOD["learned_fusion"], arm_weights=dict(WIN), beats_equal=True,
                                          significant=True)
            _put(root, json.dumps(seed).encode("utf-8"))
            box = {}
            spy = _with_spies(lambda: box.setdefault("res", call(root)))
            inst = str(_inst(root))
            check(f"{label}: wrote", ok(box.get("res")), repr(box.get("res"))[:120])
            check(f"{label}: committed by os.replace onto instance.json",
                  any(Path(d) == Path(inst) for _s, d in spy.replaced), str(spy.replaced))
            check(f"{label}: no truncating write_text on instance.json",
                  not any(Path(t) == Path(inst) for t in spy.write_text_targets), str(spy.write_text_targets))
            data = json.loads(_inst(root).read_text(encoding="utf-8"))
            check(f"{label}: unrelated keys survive", data.get("project") == "deepmap"
                  and "access_prior" in (data.get("experiments") or {}), str(sorted(data)))
            left = sorted(p.name for p in (root / ".deepmap").iterdir())
            check(f"{label}: no temp file left behind", all(n in ("instance.json", "instance.json.lock")
                                                           for n in left), str(left))

    print("\n-- a failed commit (os.replace keeps failing) leaves the old file intact + no temp --")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        raw = json.dumps(GOOD).encode("utf-8")
        p = _put(root, raw)
        real_replace = os.replace
        saved = getattr(S, "_IO_RETRY_S", None)

        def boom(src, dst, *a, **k):
            raise PermissionError(13, "simulated sharing violation", str(dst))

        os.replace = boom
        try:
            if saved is not None:
                S._IO_RETRY_S = 0.05
            out = S.merge_learned_fusion(root, {"learning_enabled": False})
        finally:
            os.replace = real_replace
            if saved is not None:
                S._IO_RETRY_S = saved
        check("merge reports failure", out is None, repr(out))
        check("the old file is byte-identical", p.read_bytes() == raw)
        left = sorted(x.name for x in p.parent.iterdir())
        check("no temp file left behind", all(n in ("instance.json", "instance.json.lock") for n in left), str(left))


def _hold_lock(root: Path):
    lock = _inst(root).with_name("instance.json.lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    h = open(lock, "a+")
    if os.name == "nt":
        import msvcrt
        h.seek(0)
        msvcrt.locking(h.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(h.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    return h


def _drop_lock(h) -> None:
    try:
        if os.name == "nt":
            import msvcrt
            h.seek(0)
            msvcrt.locking(h.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(h.fileno(), fcntl.LOCK_UN)
    finally:
        h.close()


def gate_lock(S, T, LP, W, E) -> None:
    print("\n-- every writer honours the cross-process lock, with a BOUNDED wait --")
    saved = getattr(S, "LOCK_WAIT_S", None)
    S.LOCK_WAIT_S = 0.3
    try:
        for label, call, ok in _writers(S, T, LP, W, E):
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                raw = json.dumps(GOOD).encode("utf-8")
                p = _put(root, raw)
                h = _hold_lock(root)
                try:
                    t0 = time.monotonic()
                    res = call(root)
                    dt = time.monotonic() - t0
                finally:
                    _drop_lock(h)
                check(f"{label}: while another holder has the lock -> NOT written", p.read_bytes() == raw
                      and not ok(res), repr(res)[:120])
                check(f"{label}: gave up within the bounded wait (never hangs)", dt < 3.0, f"{dt:.2f}s")
                res2 = call(root)
                check(f"{label}: after release the same write lands", ok(res2) and p.read_bytes() != raw,
                      repr(res2)[:120])
    finally:
        if saved is None:
            try:
                delattr(S, "LOCK_WAIT_S")
            except AttributeError:
                pass
        else:
            S.LOCK_WAIT_S = saved


_CHILD = r"""
import sys
sys.dont_write_bytecode = True
sys.path.insert(0, {build!r})
import instance_state as S
missed = 0
for j in range({n}):
    if S.merge_learned_fusion({root!r}, {{"w{i}_%d" % j: j}}) is None:
        missed += 1
print("MISSED", missed)
"""


def gate_no_lost_update(root_tree: Path) -> None:
    print("\n-- 4 processes x 25 concurrent merges: no update is lost --")
    k, n = 4, 25
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _put(root, json.dumps({"project": "deepmap", "learned_fusion": {"learning_enabled": True}}).encode("utf-8"))
        procs = [subprocess.Popen([sys.executable, "-B", "-c",
                                   _CHILD.format(build=str(root_tree / "build"), root=str(root), n=n, i=i)],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                 for i in range(k)]
        outs = [p.communicate(timeout=180) for p in procs]
        missed = sum(int(o.split("MISSED")[-1].strip() or 0) for o, _e in outs if "MISSED" in o)
        errs = [e.strip()[-300:] for _o, e in outs if e.strip()]
        check("every child ran cleanly", not errs and all(p.returncode == 0 for p in procs), str(errs)[:400])
        try:
            data = json.loads(_inst(root).read_text(encoding="utf-8"))
            parsed = True
        except Exception as exc:
            data, parsed = {}, False
            errs.append(repr(exc))
        check("the file is a valid JSON object afterwards", parsed, str(errs)[-200:])
        lf = data.get("learned_fusion") or {}
        want = {f"w{i}_{j}" for i in range(k) for j in range(n)}
        lost = sorted(want - set(lf))
        check(f"no merge was skipped on the lock (bounded wait never hit)", missed == 0, f"missed={missed}")
        check(f"all {k * n} concurrent updates survive (no lost update)", not lost,
              f"{len(lost)} lost, e.g. {lost[:6]}")
        check("unrelated keys survive the storm", data.get("project") == "deepmap"
              and lf.get("learning_enabled") is True, str({x: data.get(x) for x in ("project",)}))


def gate_zero_egress_and_reader(S, T, LF) -> None:
    print("\n-- zero-egress + the frozen live reader reads what the writer wrote --")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        T.write_instance_weights(root, {"arm_weights": dict(WIN), "beats_equal": True, "significant": True})
        S.merge_learned_fusion(root, {"applied": True})
        S.write_experiment(root, "x", {"b": 2})
        files = sorted(str(p.relative_to(root)).replace("\\", "/") for p in root.rglob("*") if p.is_file())
        check("only .deepmap/instance.json (+ its .lock sidecar) under the root",
              set(files) <= {".deepmap/instance.json", ".deepmap/instance.json.lock"}
              and ".deepmap/instance.json" in files, str(files))
        got = LF.load_applied_weights(root, env={"DEEPMAP_APPLY_LEARNED": "1"})
        check("learned_fusion (frozen reader) resolves the written weights", got == WIN, str(got))


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    root = REPO
    if "--root" in sys.argv:
        root = Path(sys.argv[sys.argv.index("--root") + 1]).resolve()
    print("=" * 72)
    print(f"DeepMap — O10/D5: instance.json writers are locked + atomic + never wipe (root: {root})")
    print("=" * 72)
    S, T, LF, LP, W, E = _load(root)
    gate_never_wipes(S, T, LP, W, E)
    gate_atomic(S, T, LP, W, E)
    gate_lock(S, T, LP, W, E)
    gate_no_lost_update(root)
    gate_zero_egress_and_reader(S, T, LF)
    total = len(PASSES) + len(FAILS)
    print("\n" + "=" * 72)
    if FAILS:
        print(f"RESULT: RED — {len(PASSES)}/{total} gates passed.")
        return 1
    print(f"RESULT: GREEN — {len(PASSES)}/{total} gates passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
