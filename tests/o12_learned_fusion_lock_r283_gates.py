#!/usr/bin/env python3
"""Observation D21 — build/learned_fusion.py's byte-freeze gets a lock that a committed change cannot pass.

The freeze (CLAUDE.md: `build/learned_fusion.py` is byte-unchanged; the G4 activation AND-gate lives in it) was
byte-checked in one place only: tests/sync_r10_gates.py gate_h_frozen, which compares the working-tree file with
`git show HEAD:build/learned_fusion.py`. Once a change is committed — and always in CI's fresh checkout — the file IS
HEAD, so that check is green whatever the file says (CLAUDE.md lesson 5: a gate must not read `git show HEAD:`).
retrieval.py's ranking symbols have an absolute hash lock (tests/fts_r7_gates.py `_FROZEN`); learned_fusion.py had
none — the other gates lock its behaviour or ban source tokens, not its bytes.

This gate pins the sha256 of the whole file, CRLF-normalised the way tests/fts_r7_gates.py normalises retrieval.py (a
Windows autocrlf checkout is not a change), to the content the file has had since it was added in v0.9.0 (1d1fd5752,
its only commit; identical at v0.28.1 37230dd85). It reads the file only — no git, no network, no Neo4j. Changing the
file on purpose is a G4 decision: update PINNED_SHA256 in that same change and say why.

Usage: python tests/o12_learned_fusion_lock_r283_gates.py [--root <tree>]
"""
from __future__ import annotations

import hashlib
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
REL = Path("build") / "learned_fusion.py"
# sha256 of build/learned_fusion.py with CRLF -> LF: its v0.9.0 content (1d1fd5752; 5,592 bytes, 130 lines), unchanged
# through v0.28.1 (37230dd85).
PINNED_SHA256 = "7458d4926f75c2ccc48d3de1d70edce2c7f859ac769e720cc19901a2bbdd7b17"
PASSES: list[str] = []
FAILS: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSES if ok else FAILS).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok or not detail else f" — {str(detail)[:500]}"))


def digest(data: bytes) -> str:
    """sha256 of `data` with CRLF normalised to LF (the form git stores)."""
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def frozen_problem(root: Path) -> str:
    """'' when <root>/build/learned_fusion.py matches the pin, else why not. Reads the file only — never git."""
    path = root / REL
    if not path.is_file():
        return f"{path} is missing"
    got = digest(path.read_bytes())
    return "" if got == PINNED_SHA256 else f"sha256 {got} != pinned {PINNED_SHA256} — the frozen file changed"


def _tree(base: Path, name: str, data: "bytes | None") -> Path:
    """A throwaway tree under `base` holding only build/learned_fusion.py = `data` (None: no file). Not a git repo."""
    root = base / name
    (root / "build").mkdir(parents=True)
    if data is not None:
        (root / REL).write_bytes(data)
    return root


def gate_lock(root: Path) -> None:
    print("\n-- the lock: build/learned_fusion.py is the pinned content (CRLF-normalised sha256; no git) --")
    problem = frozen_problem(root)
    check("build/learned_fusion.py is byte-identical to its frozen v0.9.0 content (pinned sha256)", not problem, problem)


def gate_teeth(root: Path) -> None:
    print("\n-- teeth: a change is caught, line endings are not a change, and the verdict needs no git --")
    path = root / REL
    src = path.read_bytes() if path.is_file() else b""
    check("precondition: the tree's build/learned_fusion.py is readable and non-empty", bool(src), str(path))
    if not src:
        return
    verdict = frozen_problem(root)
    lf = src.replace(b"\r\n", b"\n")
    mid = len(lf) // 2
    swapped = lf[:mid] + (b"x" if lf[mid:mid + 1] != b"x" else b"y") + lf[mid + 1:]
    with tempfile.TemporaryDirectory(prefix="o12_lf_lock_") as td:
        base = Path(td)
        same = _tree(base, "copy", src)
        check("the same bytes in a temp tree that is not a git checkout get the same verdict (the lock reads no git)",
              frozen_problem(same) == verdict, frozen_problem(same))
        crlf = _tree(base, "crlf", lf.replace(b"\n", b"\r\n"))
        check("a CRLF checkout of the same content gets the same verdict (Windows autocrlf is not a change)",
              frozen_problem(crlf) == verdict, frozen_problem(crlf))
        appended = _tree(base, "appended", src + b"\n# drift\n")
        check("a copy with one appended comment line is caught (RED)", frozen_problem(appended) != "")
        edited = _tree(base, "edited", swapped)
        check("a same-length copy with one byte changed mid-file is caught (RED) — not a size check",
              len(swapped) == len(lf) and frozen_problem(edited) != "")
        missing = _tree(base, "missing", None)
        check("a tree without the file is caught (RED), never skipped", "missing" in frozen_problem(missing))
    check("digest() ignores CRLF vs LF and nothing else",
          digest(b"a\r\nb\n") == digest(b"a\nb\n") and digest(b"a\nb\n") != digest(b"a\nb \n"))


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
    print(f"DeepMap — build/learned_fusion.py byte-freeze lock (content hash, not git HEAD) (root: {root})")
    print("=" * 72)
    gate_lock(root)
    gate_teeth(root)
    total = len(PASSES) + len(FAILS)
    print("\n" + "=" * 72)
    if FAILS:
        print(f"RESULT: RED — {len(PASSES)}/{total} gates passed.")
        return 1
    print(f"RESULT: GREEN — {len(PASSES)}/{total} gates passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
