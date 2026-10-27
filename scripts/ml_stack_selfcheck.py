#!/usr/bin/env python3
"""DeepMap ML-stack self-check.

Run AFTER installing the CPU ML stack to prove the Windows/macOS/Linux install
actually works — imports each required library and (optionally) loads the embedding
model and embeds a probe, asserting a 768-dim vector. FAILS LOUDLY (non-zero exit +
a clear message) on any missing import or wrong dimensionality, so a silently-degraded
install (the failure mode this guards) can never pass quietly.

What it checks: torch (CPU) / sentence-transformers / transformers import, and the
embedding model nomic-ai/modernbert-embed-base (768-dim, Apache-2.0, general-text;
build/embeddings.py MODEL_NAME) loads and embeds.

ranx is intentionally NOT checked: the retriever (build/retrieval.py) is ranx-free
(pure-Python _weighted_rrf). Re-add a ranx import check ONLY if ranx is used again.

It also FAILS when an installed neo4j / mcp / graphifyy sits outside the range requirements.txt pins
(ROUND-4 F3): pip keeps an older copy that satisfies a spec as "already satisfied", and a copy outside the
validated range breaks the servers (an old mcp dies at import next to today's pydantic; a 5.x neo4j driver
lacks an exception class the CLI uses) or the extraction (graphify outside the validated range). Each
failure prints the runnable fix: DeepMap's own interpreter, quoted, `-m pip install --upgrade "<spec>"`.

The model check is OFFLINE FIRST (ROUND-8 J6; live: one run took ~33 min with the model already cached):
it loads the model from the local Hugging Face cache only (local_files_only - no network at all; a cached
load takes seconds) and goes online ONLY on a cache miss (nothing cached, or a partial snapshot). Every
phase - the imports, the offline load, a download - runs under a watchdog: a long phase prints a heartbeat,
and one past its limit is named (with the import it was on), explained, and exits 1, so an installer never
hangs silently. Limits, in seconds (0 = no limit):
    DEEPMAP_SELFCHECK_IMPORT_TIMEOUT    default 900   (the first torch import can be slow under antivirus)
    DEEPMAP_SELFCHECK_MODEL_TIMEOUT     default 300   (loading the cached model; normally a few seconds)
    DEEPMAP_SELFCHECK_DOWNLOAD_TIMEOUT  default 1800  (a first-time ~600 MB download, only on a cache miss)

Usage:
    python ml_stack_selfcheck.py               # imports + pins + model load + 768-dim probe
    python ml_stack_selfcheck.py --imports-only # imports + pins only (no ~600MB model download)
    python ml_stack_selfcheck.py --offline      # never download: a model missing from the cache FAILS
                                                # (also when HF_HUB_OFFLINE / TRANSFORMERS_OFFLINE is set)
Exit: 0 = all good; 1 = a check failed or a phase ran past its limit (the message names exactly what).
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
REQUIREMENTS = REPO / "requirements.txt"
PINNED = ("neo4j", "mcp", "graphifyy")     # the packages requirements.txt pins to a validated range (F3)

MODEL_NAME = "nomic-ai/modernbert-embed-base"   # == build/embeddings.py MODEL_NAME
EXPECT_DIM = 768
# The ML stack (embeddings) PLUS the import-time runtime deps the provisioner + the two MCP servers
# load at module top (`from neo4j import GraphDatabase`, `from mcp.server.fastmcp import FastMCP`).
# Without neo4j/mcp the install "succeeds" then crashes at `provision.py setup` — exactly the
# silently-degraded-install failure mode this self-check exists to FAIL LOUD on (bug-hunt #3 HIGH).
REQUIRED = ["torch", "sentence_transformers", "transformers", "neo4j", "mcp"]

# Phase limits (seconds; env-overridable, 0 = no limit - see the module docstring). Generous on purpose: a
# limit is a stall detector, not a performance target (measured on a Windows box: the imports ~50 s, the
# cached model load 2-4 s, a bare online load 5.5 s).
ENV_IMPORT_TIMEOUT = "DEEPMAP_SELFCHECK_IMPORT_TIMEOUT"
ENV_MODEL_TIMEOUT = "DEEPMAP_SELFCHECK_MODEL_TIMEOUT"
ENV_DOWNLOAD_TIMEOUT = "DEEPMAP_SELFCHECK_DOWNLOAD_TIMEOUT"
IMPORT_TIMEOUT_DEFAULT_S = 900
MODEL_TIMEOUT_DEFAULT_S = 300
DOWNLOAD_TIMEOUT_DEFAULT_S = 1800
HEARTBEAT_S = 60          # a "still ..." line this often while a phase runs, so a slow step is never silent

_EXIT = os._exit          # the watchdog's hard exit (the stalled work holds the main thread); a gate stubs it
_STATE = {"doing": ""}    # what the current phase is on (the watchdog names it: "importing torch")
_UNSET = object()


def budget_from_env(name: str, default: float) -> "float | None":
    """A phase limit from the environment: unset/blank -> `default`; a number > 0 -> it; 0 or less -> None
    (no limit); anything else -> `default` with a note (junk never disables the watchdog)."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        val = float(raw.strip())
    except ValueError:
        print(f"  [note] ignoring {name}={raw!r} (not a number of seconds); using {default:g}s", file=sys.stderr)
        return default
    if val != val or val == float("inf"):     # NaN / inf: not a usable limit
        return default
    return None if val <= 0 else val


def offline_forced(env=None) -> bool:
    """True when the user switched Hugging Face downloads off (HF_HUB_OFFLINE / TRANSFORMERS_OFFLINE, the
    same truthy values huggingface_hub accepts) - the self-check then never goes online."""
    env = os.environ if env is None else env
    return any(str(env.get(k, "")).strip().upper() in {"1", "ON", "YES", "TRUE"}
               for k in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"))


def _flush() -> None:
    for s in (sys.stdout, sys.stderr):
        try:
            s.flush()
        except Exception:  # noqa: BLE001
            pass


class Watchdog:
    """Bound one phase of the self-check. The work stays on the MAIN thread (torch/transformers behave
    exactly as they do without the self-check); a daemon thread prints a heartbeat every `heartbeat_s` and,
    if the phase is still running at `budget_s`, prints what stalled (+ `detail()`, e.g. the import it was
    on) and the remedy, then exits the process with 1 - a stall inside a C extension can't be interrupted
    any other way. Leaving the `with` block cancels it. `budget_s=None` -> no limit, no thread."""

    def __init__(self, budget_s, phase: str, remedy: str, heartbeat_s=None, detail=None):
        self.budget_s = budget_s
        self.phase = phase
        self.remedy = remedy
        self.heartbeat_s = HEARTBEAT_S if heartbeat_s is None else heartbeat_s
        self.detail = detail
        self._done = threading.Event()
        self._thread = None

    def __enter__(self):
        if self.budget_s is not None:
            self._thread = threading.Thread(target=self._watch, name="selfcheck-watchdog", daemon=True)
            self._thread.start()
        return self

    def __exit__(self, *exc):
        self._done.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        return False

    def _watch(self) -> None:
        start = time.monotonic()
        deadline = start + self.budget_s
        while True:
            now = time.monotonic()
            if now >= deadline:
                break
            if self._done.wait(min(self.heartbeat_s, deadline - now)):
                return
            elapsed = time.monotonic() - start
            if elapsed < self.budget_s:
                try:        # a closed / broken stdout must never kill the watchdog before its deadline
                    print(f"  ... still {self.phase} after {elapsed:.0f}s (limit {self.budget_s:g}s)", flush=True)
                except Exception:  # noqa: BLE001
                    pass
        if self._done.is_set():
            return
        detail = ""
        try:
            detail = (self.detail() if callable(self.detail) else self.detail) or ""
        except Exception:  # noqa: BLE001
            pass
        try:
            print(f"\nFAIL: {self.phase} did not finish within {self.budget_s:g}s"
                  + (f" (stuck {detail})" if detail else "")
                  + f" - the self-check stopped here instead of hanging. {self.remedy}", file=sys.stderr)
        except Exception:  # noqa: BLE001 - the exit below must happen whatever the streams do
            pass
        _flush()
        _EXIT(1)


def _utf8():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass


def check_imports() -> list[str]:
    """Import each required lib; return the list of failures (empty == all OK)."""
    failures = []
    for mod in REQUIRED:
        _STATE["doing"] = f"importing {mod}"
        t0 = time.monotonic()
        try:
            m = __import__(mod)
            ver = getattr(m, "__version__", None) or _installed_version(mod) or "?"   # mcp has no __version__
            took = time.monotonic() - t0
            print(f"  [ok] import {mod} ({ver})" + (f" in {took:.1f}s" if took >= 1 else ""), flush=True)
        except Exception as exc:  # noqa: BLE001 - report any import failure verbatim
            failures.append(f"{mod}: {exc}")
            print(f"  [FAIL] import {mod}: {exc}", file=sys.stderr)
    _STATE["doing"] = ""
    return failures


def check_extractor(runnable: bool = True) -> str | None:
    """Prove the multi-language extraction engine (import graphify) is importable — and, unless
    imports-only, actually RUNNABLE: run `graphify update --no-cluster` (NO LLM, NO clustering,
    NO network — the only path DeepMap invokes) over a tiny throwaway repo and assert it emits a
    graph.json with >=1 node. Returns an error string or None. This is what makes the fresh-install
    'multi-language works out of the box' claim honest — a bare graphifyy install with NO LLM extras
    still parses code (zero-egress by construction)."""
    import importlib.util
    if importlib.util.find_spec("graphify") is None:
        return ("graphify (the multi-language engine) is not importable — install it (bare, no extras) into "
                f"DeepMap's Python: {_graphify_install_command()} . DEEPMAP_EXTRACTOR=graphify/auto needs it.")
    print("  [ok] import graphify (multi-language extraction engine)")
    if not runnable:
        return None
    import json
    import subprocess
    import tempfile
    from pathlib import Path
    try:
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            (tdp / "probe.py").write_text("def hello(name):\n    return greet(name)\n"
                                          "def greet(n):\n    return 'hi ' + n\n", encoding="utf-8")
            env = dict(__import__("os").environ, PYTHONUTF8="1")
            proc = subprocess.run([sys.executable, "-m", "graphify", "update", str(tdp), "--no-cluster"],
                                  cwd=str(tdp), capture_output=True, text=True, timeout=180, env=env,
                                  encoding="utf-8", errors="replace")
            gpath = tdp / "graphify-out" / "graph.json"
            if not gpath.exists():
                return (f"`graphify update --no-cluster` produced no graph.json (rc={proc.returncode}): "
                        f"{(proc.stderr or '').strip()[:200]}")
            g = json.loads(gpath.read_text(encoding="utf-8"))
            n = len(g.get("nodes", []) or [])
            if n < 1:
                return "`graphify update` produced an empty graph (0 nodes)"
            print(f"  [ok] graphify update --no-cluster runnable; probe graph {n} nodes (no LLM, no network)")
            return None
    except Exception as exc:  # noqa: BLE001
        return f"graphify runnable-smoke failed: {type(exc).__name__}: {exc}"


def _build_module(name: str):
    """Import a DeepMap build/ module (graphify_extract) for its hint helpers, or None."""
    try:
        build = str(REPO / "build")
        if build not in sys.path:
            sys.path.insert(0, build)
        return __import__(name)
    except Exception:  # noqa: BLE001
        return None


def _hint_python() -> str:
    """This interpreter as it must appear in a pasted command: double-quoted, forward slashes (Git Bash
    eats the backslashes of an unquoted Windows path) — graphify_extract.hint_python() when importable."""
    gx = _build_module("graphify_extract")
    try:
        if gx is not None:
            return gx.hint_python()
    except Exception:  # noqa: BLE001
        pass
    return f'"{Path(sys.executable).as_posix()}"' if sys.executable else "python"


def _graphify_install_command() -> str:
    """The pinned, quoted, --upgrade graphify install command — graphify_extract's own (the range its
    runtime guard validates), else built from requirements.txt. Never a bare `pip install graphifyy`,
    which resolves to an unvalidated 0.9.x."""
    gx = _build_module("graphify_extract")
    try:
        if gx is not None:
            return gx.graphify_install_command()
    except Exception:  # noqa: BLE001
        pass
    return f'{_hint_python()} -m pip install --upgrade "graphifyy{pinned_specs().get("graphifyy", "")}"'


def pinned_specs(path: Path = REQUIREMENTS) -> dict:
    """{package: specifier} for the PINNED packages, read from requirements.txt (the reference the
    installers and CI mirror — tests/compliance_gates.py). Missing file / line -> that package absent."""
    out: dict = {}
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.split("#", 1)[0].strip()
            m = re.match(r"([A-Za-z0-9][A-Za-z0-9._-]*)\s*((?:[<>=!~]=?\s*[0-9][^,\s]*\s*,?\s*)+)$", line)
            if m and m.group(1).lower() in PINNED:
                out[m.group(1).lower()] = re.sub(r"\s+", "", m.group(2)).rstrip(",")
    except Exception:  # noqa: BLE001
        pass
    return out


def _release(v: str) -> tuple:
    nums = []
    for part in str(v).strip().split("."):
        m = re.match(r"\d+", part)
        if not m:
            break
        nums.append(int(m.group(0)))
    while len(nums) > 1 and nums[-1] == 0:
        nums.pop()
    return tuple(nums)


def spec_contains(spec: str, version: str) -> bool:
    """Does `version` satisfy `spec`? packaging's SpecifierSet when importable, else a comparator for the
    >= / > / <= / < / == / != clauses requirements.txt uses."""
    try:
        from packaging.specifiers import SpecifierSet
        from packaging.version import Version
        return Version(version) in SpecifierSet(spec)
    except ImportError:
        pass
    except Exception:  # noqa: BLE001 - an unparsable version is not "in range"
        return False
    v = _release(version)
    for clause in [c for c in spec.split(",") if c.strip()]:
        m = re.match(r"\s*(==|>=|<=|!=|>|<)\s*(.+)", clause)
        if not m:
            return False
        op, b = m.group(1), _release(m.group(2))
        if not {">=": v >= b, ">": v > b, "<=": v <= b, "<": v < b, "==": v == b, "!=": v != b}[op]:
            return False
    return True


def _installed_version(pkg: str):
    try:
        from importlib import metadata
        return metadata.version(pkg)
    except Exception:  # noqa: BLE001
        return None


def check_pins(version_of=None, specs=None) -> list[str]:
    """One failure per PINNED package installed OUTSIDE its requirements.txt range, each with the runnable
    fix. A package that is not installed is skipped here (the import checks report that)."""
    version_of = version_of or _installed_version
    specs = pinned_specs() if specs is None else specs
    failures = []
    for pkg in PINNED:
        spec = specs.get(pkg)
        ver = version_of(pkg)
        if not spec or not ver:
            continue
        if spec_contains(spec, ver):
            print(f"  [ok] {pkg} {ver} is inside the validated range {pkg}{spec}")
            continue
        fix = f'{_hint_python()} -m pip install --upgrade "{pkg}{spec}"'
        failures.append(f"{pkg} {ver} is outside the validated range {pkg}{spec} — fix: {fix}")
        print(f"  [FAIL] {failures[-1]}", file=sys.stderr)
    return failures


def model_download_command() -> str:
    """A runnable command that downloads exactly the files the embedding model needs (~600 MB) into the
    local cache - NOT `snapshot_download`, which pulls the whole 2.4 GB repo with its unused ONNX variants."""
    return (f'{_hint_python()} -c "from sentence_transformers import SentenceTransformer; '
            f"SentenceTransformer('{MODEL_NAME}')\"")


def load_model(local_files_only: bool):
    """SentenceTransformer(MODEL_NAME); with local_files_only=True it reads the local Hugging Face cache
    ONLY (measured: zero network attempts with the sockets denied) and raises an OSError when the model is
    not (fully) there. A sentence-transformers too old to know the argument can't do an offline-only load:
    that becomes an OSError too, i.e. a cache miss -> the bounded online load."""
    from sentence_transformers import SentenceTransformer
    if not local_files_only:
        return SentenceTransformer(MODEL_NAME)
    try:
        return SentenceTransformer(MODEL_NAME, local_files_only=True)
    except TypeError as exc:
        if "local_files_only" in str(exc):
            raise OSError(f"this sentence-transformers cannot load offline-only ({exc})") from exc
        raise


def _short(exc: BaseException, limit: int = 200) -> str:
    first = (str(exc).strip().splitlines() or [""])[0].strip()
    if len(first) > limit:
        first = first[:limit].rstrip() + "..."
    return f"{type(exc).__name__}: {first}" if first else type(exc).__name__


def _probe(model) -> "str | None":
    vec = model.encode("search_query: does the install work")
    dim = len(vec)
    return None if dim == EXPECT_DIM else f"model returned dim {dim}, expected {EXPECT_DIM}"


def check_model(allow_download: bool = True, offline_budget=_UNSET, download_budget=_UNSET, loader=None,
                heartbeat_s=None) -> "str | None":
    """Load the model and embed a probe; return an error string or None.

    OFFLINE FIRST: the local Hugging Face cache only (no network - the installer pre-downloads the model, so
    this is the normal path and takes seconds). Only a cache MISS (an OSError: nothing cached, or a partial
    snapshot without the weights) goes online, and only when downloads are allowed (not `--offline`, not
    HF_HUB_OFFLINE). Any other offline failure is reported as is - a download can't fix a broken torch or a
    corrupt blob. Each attempt runs under its own Watchdog (DEEPMAP_SELFCHECK_MODEL_TIMEOUT /
    DEEPMAP_SELFCHECK_DOWNLOAD_TIMEOUT)."""
    loader = loader or load_model
    if offline_budget is _UNSET:
        offline_budget = budget_from_env(ENV_MODEL_TIMEOUT, MODEL_TIMEOUT_DEFAULT_S)
    if download_budget is _UNSET:
        download_budget = budget_from_env(ENV_DOWNLOAD_TIMEOUT, DOWNLOAD_TIMEOUT_DEFAULT_S)
    dl_cmd = model_download_command()

    t0 = time.monotonic()
    try:
        with Watchdog(offline_budget, f"loading {MODEL_NAME} from the local cache (offline)",
                      "The model is cached but loading it stalled (the machine may be overloaded): re-run, or "
                      f"allow more time with {ENV_MODEL_TIMEOUT}=<seconds> (0 = no limit).",
                      heartbeat_s=heartbeat_s):
            model = loader(True)
            err = _probe(model)
    except OSError as exc:            # not (fully) in the local cache
        miss = exc
    except Exception as exc:  # noqa: BLE001 - a non-cache failure: report it, don't go online
        return (f"model load/encode failed (loading the local cache copy of {MODEL_NAME}, offline): "
                f"{_short(exc)}")
    else:
        if err:
            return err
        print(f"  [ok] {MODEL_NAME} loaded from the local cache (offline, no network); probe embedding "
              f"dim={EXPECT_DIM} in {time.monotonic() - t0:.1f}s", flush=True)
        return None

    if not allow_download:
        why = ("HF_HUB_OFFLINE / TRANSFORMERS_OFFLINE is set - unset it for the download" if offline_forced()
               else "--offline")
        return (f"{MODEL_NAME} is not (fully) in the local Hugging Face cache ({_short(miss)}), and downloads "
                f"are off ({why}). Download it once with network access: {dl_cmd}")
    budget_txt = "no time limit" if download_budget is None else f"up to {download_budget:g}s"
    print(f"  [..] {MODEL_NAME} is not (fully) in the local cache ({_short(miss, 120)}) - downloading it "
          f"(online, ~600 MB, {budget_txt}) ...", flush=True)
    t0 = time.monotonic()
    try:
        with Watchdog(download_budget, f"downloading {MODEL_NAME} (online)",
                      "Check the network / proxy and re-run, or download it once yourself: "
                      f"{dl_cmd} ; allow more time with {ENV_DOWNLOAD_TIMEOUT}=<seconds> (0 = no limit).",
                      heartbeat_s=heartbeat_s):
            model = loader(False)
            err = _probe(model)
    except Exception as exc:  # noqa: BLE001
        return (f"model download/load failed: {_short(exc)} (the local cache had no complete copy: "
                f"{_short(miss)}). Check the network, or download it once yourself: {dl_cmd}")
    if err:
        return err
    print(f"  [ok] {MODEL_NAME} downloaded and loaded; probe embedding dim={EXPECT_DIM} in "
          f"{time.monotonic() - t0:.1f}s", flush=True)
    return None


def main() -> int:
    _utf8()
    ap = argparse.ArgumentParser()
    ap.add_argument("--imports-only", action="store_true",
                    help="check imports only; skip the ~600MB model download")
    ap.add_argument("--offline", action="store_true",
                    help="never download: verify the model from the local cache only (also implied by "
                         "HF_HUB_OFFLINE / TRANSFORMERS_OFFLINE)")
    args = ap.parse_args()
    started = time.monotonic()
    allow_download = not (args.offline or offline_forced())

    print("DeepMap ML-stack self-check")
    print(f"  python: {sys.version.split()[0]}", flush=True)

    with Watchdog(budget_from_env(ENV_IMPORT_TIMEOUT, IMPORT_TIMEOUT_DEFAULT_S), "importing the ML stack",
                  "The first import of torch / sentence-transformers can be slow (antivirus scanning, a busy "
                  f"disk): re-run the installer, or allow more time with {ENV_IMPORT_TIMEOUT}=<seconds> "
                  "(0 = no limit).", detail=lambda: _STATE.get("doing", "")):
        failures = check_imports()
    if failures:
        print(f"\nFAIL: {len(failures)} import(s) missing -> the ML stack is not"
              f" correctly installed. Re-run the installer.", file=sys.stderr)
        return 1

    # The installed neo4j / mcp / graphifyy must be inside the ranges requirements.txt pins (F3).
    pin_failures = check_pins()
    if pin_failures:
        print(f"\nFAIL: {len(pin_failures)} package(s) outside the validated range:\n  "
              + "\n  ".join(pin_failures), file=sys.stderr)
        return 1

    # The multi-language extraction engine (import graphify). Importability is always checked; the
    # runnable smoke (an actual `graphify update --no-cluster`) runs unless --imports-only.
    ext_err = check_extractor(runnable=not args.imports_only)
    if ext_err:
        print(f"\nFAIL: {ext_err}", file=sys.stderr)
        return 1

    if not args.imports_only:
        err = check_model(allow_download=allow_download)
        if err:
            print(f"\nFAIL: {err}", file=sys.stderr)
            return 1

    print("\nOK: ML stack is functional"
          + ("" if args.imports_only else f" and {MODEL_NAME} embeds at {EXPECT_DIM}-dim")
          + "; graphify multi-language engine "
          + ("importable" if args.imports_only else "importable + runnable")
          + f". (The self-check took {time.monotonic() - started:.1f}s.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
