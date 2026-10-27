#!/usr/bin/env python3
"""ISS-build:027 (C10) + Observation C14 — docstrings that say what the code does.

C10: port_registry's module docstring said the base pairs (8787/8788, 7687/7474) belong to the first project "so no
failure path hands them out any more", and registry_status's said a present-but-unusable registry "must never be
answered with a base port". But an UNRECOGNIZED registry (garbage JSON, an unknown version, a bad shape — possibly a
newer DeepMap's schema) makes the allocators return the base pair WITHOUT writing it: the documented unknown-schema
contract (never clobber other projects' allocations), gate-locked in tests/registry_robustness_gates.py. Every current
caller re-checks (lookup_ports / registry_status); a future caller trusting the docstrings would not. The behaviour
stays; the docstrings now state the exception and what a caller must check.

C14: provision._base_adopt_ok said DEEPMAP_NO_BASE_ADOPT=1 is set by "the benchmark's per-clone builds". It is not:
benchmark/clone_graph.py pre-claims a NON-base pair with port_registry.allocate_neo4j_ports(clone,
base_adopt_ok=lambda: False) before setup. The docstring now names that mechanism.

Hermetic: temp DEEPMAP_HOME; no Neo4j, no sockets, no network.
Usage: python tests/o1_docstring_truth_r283_gates.py [--root <tree>]
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PASSES: list[str] = []
FAILS: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSES if ok else FAILS).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok or not detail else f" — {str(detail)[:500]}"))


def _norm(doc) -> str:
    return " ".join(str(doc or "").split())


def _load(root: Path):
    sys.path.insert(0, str(root / "build"))
    import provision as P  # noqa: E402
    import port_registry as PR  # noqa: E402
    return P, PR


def gate_c10_docstrings(PR) -> None:
    print("\n-- C10: port_registry's docstrings state the unrecognized-registry exception --")
    mod = _norm(PR.__doc__)
    low = mod.lower()
    check("module docstring: the 'no failure path hands them out' claim is gone or qualified",
          "no failure path hands them out any more" not in mod or "unrecognized" in low, mod[1100:1700])
    check("module docstring: names the UNRECOGNIZED-registry exception (base pair returned WITHOUT writing it)",
          "unrecognized" in low and "without writing" in low, mod[1100:1900])
    rs = _norm(PR.registry_status.__doc__)
    check("registry_status: no unqualified 'must never be answered with a base port'",
          "must never be answered with a base port" not in rs, rs)
    check("registry_status: says the allocators return the base pair, unwritten, for 'unrecognized'",
          "unrecognized" in rs.lower() and "base pair" in rs.lower() and "without writing" in rs.lower(), rs)
    for fn in (PR.allocate_ports, PR.allocate_neo4j_ports):
        d = _norm(fn.__doc__).lower()
        check(f"{fn.__name__}: documents the unrecognized-registry base pair (and what to check)",
              "unrecognized" in d and "base" in d and ("lookup_" in d or "registry_status" in d), d[:600])


def gate_c10_behaviour_unchanged(PR, home: Path) -> None:
    print("\n-- C10: ... and the contract itself is unchanged (garbage registry -> base pair, not written) --")
    work = home.parent / "c10work"
    work.mkdir(exist_ok=True)
    proj = work / "a"
    proj.mkdir(exist_ok=True)
    reg = home / PR.REGISTRY_FILENAME
    home.mkdir(parents=True, exist_ok=True)
    reg.write_text("}{ not json", encoding="utf-8")
    check("registry_status of a garbage registry is 'unrecognized'", PR.registry_status() == "unrecognized")
    pa = PR.allocate_ports(proj)
    check("allocate_ports on it returns the base (8787, 8788)", pa == (PR.BASE_SEARCH, PR.BASE_EMBED), repr(pa))
    pn = PR.allocate_neo4j_ports(proj, base_adopt_ok=lambda: True)
    check("allocate_neo4j_ports on it returns the base (7687, 7474)", pn == (PR.BASE_BOLT, PR.BASE_HTTP), repr(pn))
    check("... UNRECORDED: lookup_ports / lookup_neo4j_ports say None (what callers re-check)",
          PR.lookup_ports(proj) is None and PR.lookup_neo4j_ports(proj) is None)
    check("... and the garbage registry was NOT overwritten", reg.read_text(encoding="utf-8") == "}{ not json")


def gate_c14(P, root: Path) -> None:
    print("\n-- C14: _base_adopt_ok names the benchmark's REAL mechanism --")
    d = _norm(P._base_adopt_ok.__doc__)
    check("no claim that the benchmark's per-clone builds set DEEPMAP_NO_BASE_ADOPT",
          "per-clone builds set it" not in d and "benchmark's per-clone builds set" not in d, d[-900:])
    check("names the pre-allocation: allocate_neo4j_ports(clone, base_adopt_ok=lambda: False)",
          "allocate_neo4j_ports" in d and "base_adopt_ok=lambda: False" in d, d[-900:])
    bench = sorted((root / "benchmark").glob("*.py"))
    users = [p.name for p in bench if "NO_BASE_ADOPT" in p.read_text(encoding="utf-8", errors="replace")]
    check("(truth) no benchmark module sets DEEPMAP_NO_BASE_ADOPT", bench != [] and users == [], repr(users))
    cg = (root / "benchmark" / "clone_graph.py").read_text(encoding="utf-8", errors="replace")
    check("(truth) benchmark/clone_graph.py pre-claims with allocate_neo4j_ports(..., base_adopt_ok=lambda: False)",
          "allocate_neo4j_ports(" in cg and "base_adopt_ok=lambda: False" in cg)


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
    print(f"DeepMap — port_registry / _base_adopt_ok docstrings match the code (root: {root})")
    print("=" * 72)
    for k in ("NEO4J_URI", "DEEPMAP_SEARCH_PORT", "DEEPMAP_EMBED_PORT"):
        os.environ.pop(k, None)
    with tempfile.TemporaryDirectory(prefix="o1doc") as td:
        home = Path(td) / "home"
        os.environ["DEEPMAP_HOME"] = str(home)
        P, PR = _load(root)
        gate_c10_docstrings(PR)
        gate_c10_behaviour_unchanged(PR, home)
        gate_c14(P, root)
    total = len(PASSES) + len(FAILS)
    print("\n" + "=" * 72)
    if FAILS:
        print(f"RESULT: RED — {len(PASSES)}/{total} gates passed.")
        return 1
    print(f"RESULT: GREEN — {len(PASSES)}/{total} gates passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
