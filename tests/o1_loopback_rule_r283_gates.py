#!/usr/bin/env python3
"""Observation C11 — provision uses THE loopback-host rule (port_registry.is_loopback_host), not three private copies.

Finding: provision's _is_local_base_uri, _escape_hatch_instance and _neo4j_endpoint each carried an inline
("localhost", "127.0.0.1", "::1") tuple, while port_registry.is_loopback_host — and sync, the hooks and the CLI —
also treat the expanded IPv6 loopback 0:0:0:0:0:0:0:1 as this machine. An operator NEO4J_URI spelled
bolt://[0:0:0:0:0:0:0:1]:7687 was "not a DeepMap-managed local instance — nothing to stop" for `provision.py stop`,
and _stop_rc skipped its port wait, although the [::1] spelling of the same endpoint was stopped.

The fix: one helper (provision._is_loopback -> port_registry.is_loopback_host) behind all three. This gate checks each
function against port_registry.is_loopback_host over a matrix of host spellings, and that provision.py keeps no inline
loopback-host collection of its own.

Hermetic: temp DEEPMAP_HOME; no Neo4j, no sockets, no network.
Usage: python tests/o1_loopback_rule_r283_gates.py [--root <tree>]
"""
from __future__ import annotations

import ast
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PASSES: list[str] = []
FAILS: list[str] = []

# (the host as it appears in a bolt URI, the URI netloc form)
_HOSTS = (("localhost", "localhost"), ("LocalHost", "LocalHost"), ("127.0.0.1", "127.0.0.1"), ("::1", "[::1]"),
          ("0:0:0:0:0:0:0:1", "[0:0:0:0:0:0:0:1]"), ("0:0:0:0:0:0:0:1", "[0:0:0:0:0:0:0:1]".upper()),
          ("example.com", "example.com"), ("10.0.0.7", "10.0.0.7"), ("::2", "[::2]"), ("127.0.0.2", "127.0.0.2"),
          ("my-neo4j.internal", "my-neo4j.internal"))


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSES if ok else FAILS).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok or not detail else f" — {str(detail)[:500]}"))


def _load(root: Path):
    sys.path.insert(0, str(root / "build"))
    import provision as P  # noqa: E402
    import port_registry as PR  # noqa: E402
    return P, PR


def gate_a_one_rule(P, PR) -> None:
    print("\n-- A: every provision loopback test agrees with port_registry.is_loopback_host --")
    for host, net in _HOSTS:
        want = PR.is_loopback_host(host)
        uri = f"bolt://{net}:7687"
        got = P._is_local_base_uri(uri)
        check(f"_is_local_base_uri({uri}) == {want}", got is want, repr(got))
        got = P._escape_hatch_instance(uri)
        check(f"_escape_hatch_instance({uri}) managed == {want}", got == ((True, None) if want else (False, None)),
              repr(got))
        uri2 = f"neo4j://{net}:7689"
        got = P._neo4j_endpoint(uri2)
        exp = ("localhost", 7689) if want else (host.lower(), 7689)
        check(f"_neo4j_endpoint({uri2}) == {exp}", got == exp, repr(got))
    print("\n-- A2: the expanded IPv6 loopback in particular (the reported divergence) --")
    exp = "bolt://[0:0:0:0:0:0:0:1]:7687"
    check("the expanded IPv6 loopback base URI is the local base store", P._is_local_base_uri(exp) is True)
    check("... and `stop` would act on the base store it names (managed, legacy layout)",
          P._escape_hatch_instance(exp) == (True, None), repr(P._escape_hatch_instance(exp)))
    check("... and it is the SAME endpoint as bolt://localhost:7687 / bolt://[::1]:7687",
          P._neo4j_endpoint(exp) == P._neo4j_endpoint("bolt://localhost:7687") == P._neo4j_endpoint("bolt://[::1]:7687"),
          repr((P._neo4j_endpoint(exp), P._neo4j_endpoint("bolt://[::1]:7687"))))
    check("a non-base port on the loopback is not the base store", P._is_local_base_uri("bolt://127.0.0.1:7689") is False)
    check("an unparseable URI is not the base store (no raise)", P._is_local_base_uri("bolt://[::1:7687") is False)


def gate_b_no_private_copy(root: Path) -> None:
    print("\n-- B: provision.py holds no inline loopback-host collection of its own --")
    tree = ast.parse((root / "build" / "provision.py").read_text(encoding="utf-8"))
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            vals = {e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)}
            if {"127.0.0.1", "::1"} <= vals or {"localhost", "::1"} <= vals:
                hits.append(f"line {node.lineno}: {sorted(vals)}")
    check("no tuple / list / set literal of loopback host names in provision.py (one rule: port_registry)",
          not hits, "; ".join(hits))


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
    print(f"DeepMap — one loopback-host rule in provision (root: {root})")
    print("=" * 72)
    for k in ("NEO4J_URI", "DEEPMAP_SEARCH_PORT", "DEEPMAP_EMBED_PORT"):
        os.environ.pop(k, None)
    with tempfile.TemporaryDirectory(prefix="o1lb") as td:
        os.environ["DEEPMAP_HOME"] = str(Path(td) / "home")
        P, PR = _load(root)
        gate_a_one_rule(P, PR)
        gate_b_no_private_copy(root)
    total = len(PASSES) + len(FAILS)
    print("\n" + "=" * 72)
    if FAILS:
        print(f"RESULT: RED — {len(PASSES)}/{total} gates passed.")
        return 1
    print(f"RESULT: GREEN — {len(PASSES)}/{total} gates passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
