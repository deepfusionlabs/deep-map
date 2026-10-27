#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Round-8 gates for the install-time ML-stack self-check (owner W8: scripts/ml_stack_selfcheck.py).
RED-first, hermetic.

Locks the J6 self-check speed item (live L2f-5: one run of `scripts/ml_stack_selfcheck.py` took ~33 min
with the model already in the local Hugging Face cache; ~20% of one core, no child process, no socket when
sampled — a stall inside the self-check's own process, with nothing telling the user what it was doing):

  O  OFFLINE FIRST. The model check loads the model from the local cache only (SentenceTransformer
     local_files_only=True — measured: zero network attempts with the sockets denied, cached load ~2-4 s)
     and never goes online when that works. Only a CACHE MISS (an OSError: nothing cached, or a partial
     snapshot missing the weights — "does not appear to have a file named ... model.safetensors") falls back
     to an online load. Any other offline failure (a runtime error, a wrong dimension) is reported as is —
     going online can't fix it and would only spend time.
  D  Downloads can be switched off: `--offline`, or HF_HUB_OFFLINE / TRANSFORMERS_OFFLINE set by the user ->
     a cache miss is a clear failure naming a download command (one that fetches only the files the model
     needs, not the whole 2.4 GB repo with its ONNX variants), never an online attempt.
  T  EVERY PHASE IS BOUNDED. A watchdog wraps the imports, the offline load and the download; past its
     limit it prints which phase stalled (and which import it was on), how long it waited, how to re-run /
     raise the limit, and exits 1 so an installer never hangs silently. Limits come from
     DEEPMAP_SELFCHECK_IMPORT_TIMEOUT / _MODEL_TIMEOUT / _DOWNLOAD_TIMEOUT (seconds; 0 = no limit; junk ->
     the default). A finished phase cancels its watchdog; a long phase prints a heartbeat.
  W  The real loader passes local_files_only=True on the offline attempt and nothing on the online one;
     a sentence-transformers too old to know local_files_only turns into a cache miss (-> the bounded online
     load), not a TypeError crash.

Hermetic: the model loader, the imports and the process exit are all stubbed (a fake sentence_transformers
module; `_EXIT` replaced by a recorder), so no torch import, no model, no network, no real exit. Budgets are
fractions of a second.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import sys
import threading
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "ml_stack_selfcheck.py"

_PASS = 0
_FAIL = 0


def check(label: str, cond: bool, detail="") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {label}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}{(' — ' + str(detail)[:600]) if detail != '' else ''}")


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


def _load():
    spec = importlib.util.spec_from_file_location("dm_selfcheck_r8_gate", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


M = _load()


class _Model:
    def __init__(self, dim=768):
        self.dim = dim

    def encode(self, text):
        return [0.0] * self.dim


class _Loader:
    """A scripted model loader: `plan` maps local_files_only -> a _Model or an exception to raise."""

    def __init__(self, offline, online=None):
        self.plan = {True: offline, False: online}
        self.calls: list = []

    def __call__(self, local_files_only):
        self.calls.append(local_files_only)
        out = self.plan[local_files_only]
        if isinstance(out, BaseException):
            raise out
        if out is None:
            raise AssertionError(f"unexpected load(local_files_only={local_files_only})")
        return out


def _quiet(fn, *a, **k):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rv = fn(*a, **k)
        except TypeError as exc:     # the pre-fix API has no such parameters -> RED, not a crash
            rv = f"<TypeError: {exc}>"
        except SystemExit as exc:    # e.g. argparse rejecting a flag the pre-fix script lacks
            rv = f"<SystemExit {exc.code}>"
    return rv, out.getvalue(), err.getvalue()


def _check_model(**k):
    if not hasattr(M, "check_model"):
        return "<no check_model>", "", ""
    k.setdefault("offline_budget", 30)
    k.setdefault("download_budget", 30)
    return _quiet(M.check_model, **k)


MISS = OSError("We couldn't connect to 'https://huggingface.co' to load the files, and couldn't find them in "
               "the cached files.")
PARTIAL = OSError("nomic-ai/modernbert-embed-base does not appear to have a file named pytorch_model.bin or "
                  "model.safetensors.")


# ================================================================================================
def gate_o_offline_first() -> None:
    print("\n-- O: a cached model is verified OFFLINE; only a cache miss goes online --")
    ld = _Loader(offline=_Model())
    rv, out, _ = _check_model(loader=ld)
    check("cached model -> check_model passes", rv is None, rv)
    check("...with exactly ONE load, from the local cache only (local_files_only=True) — never online",
          ld.calls == [True], ld.calls)
    check("...and says it was verified offline, from the local cache",
          "local cache" in out and "offline" in out.lower(), out)

    ld = _Loader(offline=MISS, online=_Model())
    rv, out, _ = _check_model(loader=ld)
    check("nothing cached (offline OSError) -> falls back to ONE online load, which passes",
          rv is None and ld.calls == [True, False], (rv, ld.calls))
    check("...and says it is downloading because the model is not in the local cache",
          "not" in out and "local cache" in out and "download" in out.lower(), out)

    ld = _Loader(offline=PARTIAL, online=_Model())
    rv, _o, _e = _check_model(loader=ld)
    check("a PARTIAL cache (weights missing — the real transformers message) is a cache miss -> online",
          rv is None and ld.calls == [True, False], (rv, ld.calls))

    ld = _Loader(offline=RuntimeError("Error while deserializing header: invalid JSON"), online=_Model())
    rv, _o, _e = _check_model(loader=ld)
    check("a NON-cache offline failure is reported as is — no online attempt (it can't fix it)",
          isinstance(rv, str) and "deserializing" in rv and ld.calls == [True], (rv, ld.calls))

    ld = _Loader(offline=_Model(dim=384), online=_Model())
    rv, _o, _e = _check_model(loader=ld)
    check("a cached model with the WRONG dimension fails (768 expected) — no online attempt",
          isinstance(rv, str) and "384" in rv and "768" in rv and ld.calls == [True], (rv, ld.calls))

    ld = _Loader(offline=MISS, online=OSError("Connection error: huggingface.co unreachable"))
    rv, _o, _e = _check_model(loader=ld)
    check("miss + failed download -> one failure naming BOTH the online error and the cache miss",
          isinstance(rv, str) and "unreachable" in rv and "cached files" in rv, rv)
    check("...with a runnable download command that fetches only what the model needs (not the ONNX repo)",
          isinstance(rv, str) and "SentenceTransformer(" in rv and "modernbert-embed-base" in rv
          and "snapshot_download" not in rv, rv)


def gate_d_downloads_off() -> None:
    print("\n-- D: --offline / HF_HUB_OFFLINE -> a cache miss fails clearly, never an online attempt --")
    ld = _Loader(offline=MISS, online=_Model())
    rv, _o, _e = _check_model(loader=ld, allow_download=False)
    check("allow_download=False + cache miss -> a failure, with NO online load",
          isinstance(rv, str) and ld.calls == [True], (rv, ld.calls))
    check("...naming the local cache, why it did not download, and the download command",
          isinstance(rv, str) and "local" in rv and "cache" in rv and "offline" in rv.lower()
          and "SentenceTransformer(" in rv, rv)
    with _env(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE=None):
        rv, _o, _e = _check_model(loader=_Loader(offline=MISS, online=_Model()), allow_download=False)
    check("...and when HF_HUB_OFFLINE is what switched downloads off, it says to unset it for the download "
          "(the printed command would fail in that same shell otherwise)",
          isinstance(rv, str) and "HF_HUB_OFFLINE" in rv and "unset" in rv, rv)
    ld = _Loader(offline=_Model())
    rv, _o, _e = _check_model(loader=ld, allow_download=False)
    check("allow_download=False + cached model -> passes (offline needs no network)",
          rv is None and ld.calls == [True], (rv, ld.calls))

    if not hasattr(M, "offline_forced"):
        check("offline_forced() exists", False)
    else:
        with _env(HF_HUB_OFFLINE=None, TRANSFORMERS_OFFLINE=None):
            check("no offline env -> downloads allowed", M.offline_forced() is False)
        with _env(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE=None):
            check("HF_HUB_OFFLINE=1 -> downloads off (the user's choice is honoured)", M.offline_forced() is True)
        with _env(HF_HUB_OFFLINE=None, TRANSFORMERS_OFFLINE="true"):
            check("TRANSFORMERS_OFFLINE=true -> downloads off", M.offline_forced() is True)
        with _env(HF_HUB_OFFLINE="0", TRANSFORMERS_OFFLINE=None):
            check("HF_HUB_OFFLINE=0 -> downloads allowed", M.offline_forced() is False)

    # main() wiring: --offline and HF_HUB_OFFLINE both reach check_model as allow_download=False.
    seen: list = []
    saved = {n: getattr(M, n, None) for n in ("check_imports", "check_pins", "check_extractor", "check_model")}
    saved_argv = sys.argv
    try:
        M.check_imports = lambda: []
        M.check_pins = lambda **k: []
        M.check_extractor = lambda runnable=True: None

        def _cm(allow_download=True, **k):
            seen.append(allow_download)
            return None
        M.check_model = _cm
        for argv, env in ((["x"], None), (["x", "--offline"], None), (["x"], "1")):
            sys.argv = argv
            with _env(HF_HUB_OFFLINE=env, TRANSFORMERS_OFFLINE=None):
                rc, _o, _e = _quiet(M.main)
            if rc != 0:
                seen.append(("rc", rc, _e[-300:]))
    finally:
        for n, v in saved.items():
            setattr(M, n, v)
        sys.argv = saved_argv
    check("main(): plain run -> downloads allowed; --offline -> off; HF_HUB_OFFLINE=1 -> off",
          seen == [True, False, False], seen)


def _fake_exit():
    rec = {"codes": [], "ev": threading.Event()}

    def _exit(code):
        rec["codes"].append(code)
        rec["ev"].set()
    return rec, _exit


def gate_t_bounded() -> None:
    print("\n-- T: every phase is bounded by a watchdog (clear message + exit 1), heartbeats while waiting --")
    if not hasattr(M, "Watchdog") or not hasattr(M, "_EXIT"):
        check("Watchdog + _EXIT (the injectable process exit) exist", False)
        return
    real_exit = M._EXIT
    try:
        # (1) the offline load stalls past its limit
        rec, M._EXIT = _fake_exit()

        def stall(local_files_only):
            rec["ev"].wait(5)
            raise RuntimeError("aborted by the gate after the watchdog fired")
        t0 = time.monotonic()
        rv, out, err = _check_model(loader=stall, offline_budget=0.3, heartbeat_s=60)
        took = time.monotonic() - t0
        check("a stalled OFFLINE load -> the watchdog exits 1 (the installer fails instead of hanging)",
              rec["codes"] == [1], rec["codes"])
        check("...about when the limit is reached (not immediately, not much later)",
              0.25 <= took < 3.0, f"{took:.2f}s")
        check("...naming the phase (loading from the local cache), the limit and the env var to raise it",
              "local cache" in err and "0.3" in err and "DEEPMAP_SELFCHECK_MODEL_TIMEOUT" in err, err)

        # (2) the download stalls past its limit
        rec, M._EXIT = _fake_exit()

        def dl(local_files_only):
            if local_files_only:
                raise MISS
            rec["ev"].wait(5)
            raise RuntimeError("aborted by the gate after the watchdog fired")
        rv, out, err = _check_model(loader=dl, offline_budget=30, download_budget=0.3, heartbeat_s=60)
        check("a stalled DOWNLOAD -> the watchdog exits 1, naming the download + DEEPMAP_SELFCHECK_DOWNLOAD_TIMEOUT"
              " + the manual download command",
              rec["codes"] == [1] and "download" in err.lower() and "DEEPMAP_SELFCHECK_DOWNLOAD_TIMEOUT" in err
              and "SentenceTransformer(" in err, (rec["codes"], err))

        # (3) a finished phase cancels its watchdog
        rec, M._EXIT = _fake_exit()
        rv, out, err = _check_model(loader=_Loader(offline=_Model()), offline_budget=0.3, heartbeat_s=60)
        time.sleep(0.6)
        check("a phase that finishes in time never fires its watchdog (cancelled on exit)",
              rv is None and rec["codes"] == [], (rv, rec["codes"]))

        # (4) heartbeat while a phase runs long (but within its limit)
        rec, M._EXIT = _fake_exit()

        def slow(local_files_only):
            time.sleep(0.45)
            return _Model()
        rv, out, err = _check_model(loader=slow, offline_budget=10, heartbeat_s=0.1)
        check("a long (in-limit) phase prints 'still ...' heartbeats so the user sees it is alive",
              rv is None and "still" in out and rec["codes"] == [], (rv, out, rec["codes"]))

        # (4b) a broken stdout/stderr (a closed installer pipe) never stops the watchdog from exiting
        rec, M._EXIT = _fake_exit()

        class _Broken(io.TextIOBase):
            def write(self, s):
                raise OSError("broken pipe")

        def stall2(local_files_only):
            rec["ev"].wait(5)
            raise RuntimeError("aborted by the gate after the watchdog fired")
        with contextlib.redirect_stdout(_Broken()), contextlib.redirect_stderr(_Broken()):
            try:
                M.check_model(loader=stall2, offline_budget=0.35, download_budget=30, heartbeat_s=0.1)
            except Exception:  # noqa: BLE001
                pass
        check("a broken stdout/stderr (heartbeats + the FAIL line raise) -> the watchdog still exits 1",
              rec["codes"] == [1], rec["codes"])

        # (5) Watchdog(None) == no limit: no thread, never fires
        rec, M._EXIT = _fake_exit()
        n0 = threading.active_count()
        with M.Watchdog(None, "a phase with no limit", "remedy"):
            n1 = threading.active_count()
            time.sleep(0.05)
        check("Watchdog(None) starts no thread and never exits", n1 == n0 and rec["codes"] == [], (n0, n1))

        # (6) main(): the IMPORT phase is bounded too, and names the import it was stuck on
        rec, M._EXIT = _fake_exit()
        saved = {n: getattr(M, n) for n in ("check_imports", "check_pins", "check_extractor", "check_model")}
        saved_argv = sys.argv
        try:
            def hang_imports():
                st = getattr(M, "_STATE", None)
                if isinstance(st, dict):
                    st["doing"] = "importing torch"
                rec["ev"].wait(5)
                return ["torch: aborted by the gate"]
            M.check_imports = hang_imports
            M.check_pins = lambda **k: []
            M.check_extractor = lambda runnable=True: None
            M.check_model = lambda **k: None
            sys.argv = ["x", "--imports-only"]
            with _env(DEEPMAP_SELFCHECK_IMPORT_TIMEOUT="0.3"):
                rc, out, err = _quiet(M.main)
        finally:
            for n, v in saved.items():
                setattr(M, n, v)
            sys.argv = saved_argv
        check("main(): a stalled IMPORT phase -> watchdog exit 1 naming the import it was on + the env var",
              rec["codes"] == [1] and "torch" in err and "DEEPMAP_SELFCHECK_IMPORT_TIMEOUT" in err,
              (rec["codes"], err))
    finally:
        M._EXIT = real_exit

    if not hasattr(M, "budget_from_env"):
        check("budget_from_env() exists", False)
        return
    with _env(DEEPMAP_SELFCHECK_MODEL_TIMEOUT=None):
        check("unset -> the default", M.budget_from_env("DEEPMAP_SELFCHECK_MODEL_TIMEOUT", 300) == 300)
    with _env(DEEPMAP_SELFCHECK_MODEL_TIMEOUT="45"):
        check("'45' -> 45 s", M.budget_from_env("DEEPMAP_SELFCHECK_MODEL_TIMEOUT", 300) == 45)
    with _env(DEEPMAP_SELFCHECK_MODEL_TIMEOUT="0"):
        check("'0' -> no limit (None)", M.budget_from_env("DEEPMAP_SELFCHECK_MODEL_TIMEOUT", 300) is None)
    with _env(DEEPMAP_SELFCHECK_MODEL_TIMEOUT="soon"):
        v, _o, _e = _quiet(M.budget_from_env, "DEEPMAP_SELFCHECK_MODEL_TIMEOUT", 300)
        check("junk -> the default (never a crash, never 'no limit')", v == 300, v)
    defaults = (getattr(M, "IMPORT_TIMEOUT_DEFAULT_S", None), getattr(M, "MODEL_TIMEOUT_DEFAULT_S", None),
                getattr(M, "DOWNLOAD_TIMEOUT_DEFAULT_S", None))
    check("defaults are finite and ordered sensibly: offline load < imports <= download, offline load <= 10 min",
          all(isinstance(d, (int, float)) and d > 0 for d in defaults)
          and defaults[1] <= 600 and defaults[1] < defaults[0] <= defaults[2], defaults)


def gate_w_real_loader() -> None:
    print("\n-- W: the real loader passes local_files_only=True offline, nothing online; old ST -> a miss --")
    if not hasattr(M, "load_model"):
        check("load_model() exists", False)
        return
    seen: list = []

    class ST:
        def __init__(self, name, **kw):
            seen.append((name, kw))

        def encode(self, text):
            return [0.0] * 768

    class OldST:
        def __init__(self, name, device=None, cache_folder=None):
            seen.append((name, {}))

    fake = types.ModuleType("sentence_transformers")
    saved = sys.modules.get("sentence_transformers")
    try:
        fake.SentenceTransformer = ST
        sys.modules["sentence_transformers"] = fake
        M.load_model(True)
        M.load_model(False)
        check("offline load -> SentenceTransformer(MODEL_NAME, local_files_only=True)",
              seen[0] == (M.MODEL_NAME, {"local_files_only": True}), seen)
        check("online load -> SentenceTransformer(MODEL_NAME) with NO local_files_only",
              len(seen) > 1 and seen[1][0] == M.MODEL_NAME and "local_files_only" not in seen[1][1], seen)
        fake.SentenceTransformer = OldST
        try:
            M.load_model(True)
            got = "no exception"
        except OSError as exc:
            got = exc
        except Exception as exc:  # noqa: BLE001
            got = f"{type(exc).__name__}: {exc}"
        check("a sentence-transformers without local_files_only -> OSError (a cache miss -> bounded online), "
              "not a TypeError crash", isinstance(got, OSError), got)
    finally:
        if saved is None:
            sys.modules.pop("sentence_transformers", None)
        else:
            sys.modules["sentence_transformers"] = saved


def gate_x_whole_run() -> None:
    print("\n-- X: the full (stubbed) run: offline-first model check, elapsed time reported --")
    if not hasattr(M, "load_model"):      # never fall through to a REAL torch import + model load here
        check("load_model() exists (check_model's default loader)", False)
        return
    saved = {n: getattr(M, n, None) for n in ("check_imports", "check_pins", "check_extractor", "load_model")}
    saved_argv = sys.argv
    calls: list = []
    try:
        M.check_imports = lambda: []
        M.check_pins = lambda **k: []
        M.check_extractor = lambda runnable=True: None

        def lm(local_files_only):
            calls.append(local_files_only)
            return _Model()
        M.load_model = lm
        sys.argv = ["x"]
        with _env(HF_HUB_OFFLINE=None, TRANSFORMERS_OFFLINE=None):
            rc, out, err = _quiet(M.main)
    finally:
        for n, v in saved.items():
            if v is not None:
                setattr(M, n, v)
        sys.argv = saved_argv
    check("main() with a cached model -> exit 0 after ONE offline load (the default loader is load_model)",
          rc == 0 and calls == [True], (rc, calls, err[-300:]))
    check("...and the OK line reports how long the self-check took", "OK:" in out and "took" in out, out[-400:])


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 64)
    print("DeepMap — round-8 self-check gates (offline-first model check, bounded phases)")
    print("=" * 64)
    gate_o_offline_first()
    gate_d_downloads_off()
    gate_t_bounded()
    gate_w_real_loader()
    gate_x_whole_run()
    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
