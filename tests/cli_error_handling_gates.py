#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-Neo4j, no-spend gates for the CLI's friendly-error handling when Neo4j drops mid-query.

`deepmap search`/`impact`/`status` open a driver and `verify_connectivity()` inside a `try/finally`
that only CLOSES the driver — so a `neo4j.exceptions` connectivity error used to propagate out of
`main()` and print a raw Python traceback (exit 1). The fix guards the dispatch in `main()` and turns
a driver/DB/OS error into a clear one-line message + a non-zero exit, without the exception escaping.

These gates lock: for each of `search`/`impact`/`status`, a `ServiceUnavailable` from
`verify_connectivity()` -> `main()` returns a non-zero code WITHOUT the exception escaping, and a
friendly message is printed. The guard is scoped (Neo4jError/DriverError/OSError), so it must NOT
swallow a plain programming error (a KeyError still escapes).

Pure + hermetic: stubs `_load` (so no heavy embeddings/retrieval import) and `_driver` (a fake driver
that raises on connect). No Neo4j, no network.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import sys
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
        print(f"  [FAIL] {label}{(' — ' + detail) if detail else ''}")


def _load_cli(base: Path):
    spec = importlib.util.spec_from_file_location("dm_deepmap_cli", base / "deepmap_cli.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _run_main(CLI, argv):
    """Invoke CLI.main() with a crafted argv; return (rc, raised_exc, stderr_text)."""
    from neo4j.exceptions import ServiceUnavailable

    class _FakeDriver:
        def verify_connectivity(self):
            raise ServiceUnavailable("Unable to connect to bolt://localhost:7687 (connection refused)")
        def session(self):
            raise ServiceUnavailable("Unable to connect")
        def close(self):
            pass

    saved_argv = sys.argv[:]
    saved = {"_load": CLI._load, "_driver": CLI._driver}
    err = io.StringIO()
    rc, raised = None, None
    try:
        CLI._load = lambda name: types.SimpleNamespace(
            maybe_log_search=lambda *a, **k: None,
            resolve_arm_weights_for_live=lambda **k: None,
            embed_query=lambda q: [0.0],
            DeepMapRetriever=lambda *a, **k: types.SimpleNamespace(search=lambda *a, **k: []),
            query_impact=lambda *a, **k: [],
        )
        CLI._driver = lambda: _FakeDriver()
        sys.argv = argv
        with contextlib.redirect_stderr(err):
            rc = CLI.main()
    except BaseException as exc:  # noqa: BLE001 — we WANT to observe an escaping exception
        raised = exc
    finally:
        sys.argv = saved_argv
        CLI._load = saved["_load"]
        CLI._driver = saved["_driver"]
    return rc, raised, err.getvalue()


def gate_friendly_errors(CLI) -> None:
    for cmd, argv in (("search", ["deepmap", "search", "x"]),
                      ("impact", ["deepmap", "impact", "x"]),
                      ("status", ["deepmap", "status"])):
        print(f"\n-- {cmd}: Neo4j down mid-query -> friendly message, non-zero, no traceback --")
        rc, raised, err = _run_main(CLI, argv)
        check(f"{cmd}: no exception escapes main()", raised is None,
              f"raised {type(raised).__name__}" if raised else "")
        check(f"{cmd}: returns a non-zero exit code", isinstance(rc, int) and rc != 0, repr(rc))
        check(f"{cmd}: prints a friendly (non-traceback) message",
              "Traceback" not in err and ("neo4j" in err.lower() or "unreachable" in err.lower()),
              err.strip()[:100])


def gate_does_not_swallow_bugs(CLI) -> None:
    print("\n-- the guard is scoped: a plain programming error still escapes --")
    saved_argv = sys.argv[:]
    saved_fn = None
    raised = None
    try:
        # Register a fake subcommand handler that raises a non-DB error via monkeypatching dispatch.
        parser = CLI.build_parser()
        # Reach into the parsed namespace path by patching _driver to raise KeyError from search.
        saved_fn = CLI._driver
        CLI._driver = lambda: (_ for _ in ()).throw(KeyError("boom"))
        sys.argv = ["deepmap", "status"]
        with contextlib.redirect_stderr(io.StringIO()):
            try:
                CLI.main()
            except KeyError:
                raised = "KeyError"
            except BaseException as exc:  # noqa: BLE001
                raised = type(exc).__name__
    finally:
        sys.argv = saved_argv
        if saved_fn is not None:
            CLI._driver = saved_fn
    check("a KeyError (programming bug) is NOT masked as a friendly DB error", raised == "KeyError",
          f"got {raised}")


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("=" * 64)
    print("DeepMap — CLI friendly DB-error handling gates (no Neo4j)")
    print("=" * 64)
    CLI = _load_cli(BUILD)
    gate_friendly_errors(CLI)
    gate_does_not_swallow_bugs(CLI)
    print("\n" + "=" * 64)
    total = _PASS + _FAIL
    print(f"RESULT: {'GREEN' if _FAIL == 0 else 'RED'} — {_PASS}/{total} gates passed.")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
