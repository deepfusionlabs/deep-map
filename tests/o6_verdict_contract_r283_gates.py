#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ISS-build:026 + observation D25 (fix round r283, owner O6) — build/verdict.py tells the TRUE exit contract.

ISS-build:026 (validated C1). verdict.py advertised an opt-in `--strict` exit mode — the module docstring's P5 row
("now a verdict (+ opt-in --strict)") and exit_code()'s docstring ("becomes 3 only under --strict, so scripts can opt
in to treating degraded as not-ready") — but no DeepMap command, hook or installer has a --strict flag and nothing
calls exit_code() (only the verdict gates do). Wired as written, its 3 would also have collided with `deepmap sync`'s
3 = "Neo4j unreachable". Decision (no new CLI surface): the promise is removed. The docstrings state the real
contracts (deepmap sync 0 / 1 / 3 / 4, provision setup 0 / 1 / 4; a DEGRADED verdict exits 0) and that exit_code()
is an unwired helper. Its strict mapping is kept (tests/delphi_pascal_gates.py and tests/verdict_gates.py call it),
but it is now the named STRICT_DEGRADED_RC, documented as a code `deepmap sync` already uses. Section D locks it
unwired: a build/, hooks/ or benchmark/ module that calls exit_code(), or any --strict option on the product surface,
turns this gate RED and names the collision.

D25 (the verdict part). The module docstring said "build/retrieval.py is byte-frozen", which has been false since
v0.28.0: the file changed, and only its 3-arm ranking functions are hash-locked (tests/fts_r7_gates.py). It now says
what is true: this module never imports or modifies retrieval.py.

Sections: A the exit-contract docstrings · B the retrieval.py claim · C exit_code() behaviour (unchanged — the
compatibility the two existing gates rely on) · D drift locks: exit_code() stays unwired, no --strict flag anywhere on
the product surface, the collision's premise (deepmap sync's 3 = Neo4j unreachable), each scanner mutation-tested.
Pure: reads files and calls pure functions; no Neo4j, no network, no writes.
Usage: python tests/o6_verdict_contract_r283_gates.py [--root <tree>]
"""
from __future__ import annotations

import ast
import importlib.util
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PASSES: list[str] = []
FAILS: list[str] = []

_STRICT = re.compile(r"--strict(?![\w-])")          # the flag itself — never --strict-mcp-config
# A mention that says the flag does NOT exist: a negation just before it ("there is no `--strict` flag") or right
# after it in the same clause ("the `--strict` exit is unwired").
_NEGATED = re.compile(r"\b(?:no|not|never|without|nor)\b[^.;:]{0,30}$", re.I)
_NEGATED_AFTER = re.compile(r"^[^.;]{0,60}?\b(?:unwired|not wired|never wired|is not|isn't|does not exist|"
                            r"doesn't exist|was removed|no longer)\b", re.I)


def check(name: str, ok: bool, detail="") -> None:
    (PASSES if ok else FAILS).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok or detail == "" else f" — {str(detail)[:700]}"))


def _load(root: Path):
    build = root / "build"
    sys.path.insert(0, str(build))
    spec = importlib.util.spec_from_file_location("_o6_verdict_r283", build / "verdict.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _norm(text) -> str:
    return " ".join(str(text or "").split())


def _promises_strict(text: str) -> list:
    """Every `--strict` mention in `text` that is NOT a negation ("there is no `--strict` flag") — i.e. a promise."""
    out = []
    for m in _STRICT.finditer(text):
        before, after = text[max(0, m.start() - 40):m.start()], text[m.end():m.end() + 80]
        if not (_NEGATED.search(before) or _NEGATED_AFTER.search(after)):
            out.append(text[max(0, m.start() - 60):m.end() + 20])
    return out


# ---- A: the exit-contract docstrings --------------------------------------------------------------------------------
def gate_a_docstrings(V) -> None:
    print("\n-- A: the docstrings state the real exit contract, and promise no --strict mode (ISS-build:026) --")
    doc, edoc = _norm(V.__doc__), _norm(V.exit_code.__doc__)
    check("A the module docstring promises no --strict mode (a mention may only say there is none)",
          not _promises_strict(doc), _promises_strict(doc))
    check("A the module docstring gives `deepmap sync`'s real codes (cmd_sync: 3 Neo4j unreachable, 4 NOT PUSHED)",
          "cmd_sync" in doc and re.search(r"\b3 Neo4j unreachable", doc) is not None
          and re.search(r"\b4 NOT PUSHED", doc) is not None, doc)
    check("A the module docstring says a DEGRADED verdict never sets the exit status (printed + persisted; the run "
          "exits 0 unless it pushed nothing)",
          re.search(r"DEGRADED verdict[^.]*never sets the exit status[^.]*exits 0", doc) is not None, doc)
    check("A exit_code() says it is UNWIRED (no DeepMap command calls it)",
          re.search(r"\bunwired\b", edoc, re.I) is not None, edoc)
    check("A exit_code() promises no --strict flag and no longer tells scripts they can opt in",
          not _promises_strict(edoc) and not re.search(r"scripts can opt in", edoc, re.I),
          _promises_strict(edoc) or edoc)
    check("A exit_code() names the collision: its strict 3 is `deepmap sync`'s \"Neo4j unreachable\"",
          "STRICT_DEGRADED_RC" in edoc and "Neo4j unreachable" in edoc, edoc)


# ---- B: the retrieval.py claim (D25) ----------------------------------------------------------------------------------
def gate_b_retrieval_claim(V, root: Path) -> None:
    print("\n-- B: no false 'retrieval.py is byte-frozen' claim (D25) --")
    doc = _norm(V.__doc__)
    check("B the module docstring no longer says build/retrieval.py is byte-frozen (only its ranking functions are "
          "hash-locked since v0.28.0)",
          not re.search(r"retrieval\.py\W+(?:is\s+)?byte[- ]?(?:frozen|unchanged|identical)", doc, re.I),
          re.findall(r"[^.]*retrieval\.py[^.]*", doc))
    check("B ... and says what is true: this module never imports or modifies retrieval.py",
          re.search(r"never imports[^.]*retrieval\.py", doc) is not None, doc)
    tree = ast.parse((root / "build" / "verdict.py").read_text(encoding="utf-8"))
    imported = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names} | \
               {str(n.module or "").split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    check("B the claim holds: verdict.py imports neither retrieval nor learned_fusion",
          not ({"retrieval", "learned_fusion"} & imported), sorted(imported))


# ---- C: behaviour (unchanged) -----------------------------------------------------------------------------------------
def gate_c_behaviour(V) -> None:
    print("\n-- C: exit_code() behaviour is unchanged (tests/delphi_pascal_gates.py + tests/verdict_gates.py call it) --")
    check("C READY -> 0", V.exit_code(V.READY) == 0 and V.exit_code(V.READY, strict=True) == 0)
    check("C FAILED -> 1, strict or not", V.exit_code(V.FAILED) == 1 and V.exit_code(V.FAILED, strict=True) == 1)
    check("C DEGRADED -> 0 by default — what `deepmap sync` and `provision.py setup` exit on DEGRADED",
          V.exit_code(V.DEGRADED) == 0)
    check("C DEGRADED, strict=True -> 3, the named STRICT_DEGRADED_RC (the value is documented as taken)",
          getattr(V, "STRICT_DEGRADED_RC", None) == 3 and V.exit_code(V.DEGRADED, strict=True) == V.STRICT_DEGRADED_RC,
          getattr(V, "STRICT_DEGRADED_RC", "(no STRICT_DEGRADED_RC)"))


# ---- D: drift locks ---------------------------------------------------------------------------------------------------
def _exit_code_wiring(src: str) -> list:
    """Where Python source `src` wires verdict.exit_code: `from verdict import exit_code`, any `<x>.exit_code(...)`
    call, or `getattr(<x>, "exit_code")`. Reading a record's `["exit_code"]` / an object's `.exit_code` is not a call."""
    hits = []
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.ImportFrom) and str(n.module or "").split(".")[-1] == "verdict" \
                and any(a.name in ("exit_code", "*") for a in n.names):
            hits.append(f"L{n.lineno}: from {n.module} import exit_code")
        elif isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "exit_code":
            hits.append(f"L{n.lineno}: .exit_code(...) call")
        elif isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "getattr" \
                and len(n.args) >= 2 and isinstance(n.args[1], ast.Constant) and n.args[1].value == "exit_code":
            hits.append(f"L{n.lineno}: getattr(..., 'exit_code')")
    return hits


def _py_strict_options(src: str) -> list:
    """String literals in Python source that ARE a --strict option token (`"--strict"`, `"--strict=1"`) — what an
    argparse option or an argv test is written with; prose that merely mentions the flag does not count."""
    return [f"L{n.lineno}: {n.value!r}" for n in ast.walk(ast.parse(src))
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and re.fullmatch(r"--strict(?:=.*)?", n.value)]


def _product_files(root: Path) -> dict:
    """{kind: [paths]} — the product surface a --strict flag or an exit_code() wiring would live on."""
    py = [p for d in ("build", "hooks", "benchmark") for p in sorted((root / d).glob("*.py"))]
    docs = sorted((root / "commands").glob("*.md")) + sorted((root / "skills").rglob("*.md")) + \
        [p for p in (root / "README.md",) if p.is_file()] + sorted((root / "docs").glob("*.md"))
    scripts = [p for p in (root / "install.sh", root / "install.ps1", root / "hooks" / "hooks.json") if p.is_file()]
    return {"py": py, "docs": docs, "scripts": scripts}


def gate_d_drift_locks(root: Path) -> None:
    print("\n-- D: drift locks — exit_code() stays unwired; no --strict flag on the product surface --")
    # The scanners must be able to fail (mutation self-test), or a GREEN below means nothing.
    wired = ("import sys\nimport verdict as V\nsys.exit(V.exit_code(v, strict=True))\n",
             "from verdict import exit_code\nraise SystemExit(exit_code(v))\n",
             "from build.verdict import exit_code as rc\n",
             "import verdict\nrc = getattr(verdict, 'exit_code')(v)\n")
    benign = "rec = {'exit_code': 0}\nx = rec['exit_code']\ny = proc.exit_code\ndef exit_code():\n    return 0\n"
    check("D (self-test) the wiring scanner catches every wiring form and ignores record keys / attribute reads",
          all(_exit_code_wiring(s) for s in wired) and not _exit_code_wiring(benign),
          [(s, _exit_code_wiring(s)) for s in (*wired, benign)])
    opt = 'p.add_argument("--strict", action="store_true")\nif "--strict=1" in argv:\n    pass\n'
    prose = '"""There is no --strict flag."""\nargs = ["--strict-mcp-config", cfg]\n'
    check("D (self-test) the option scanner catches a --strict option, not prose or --strict-mcp-config",
          len(_py_strict_options(opt)) == 2 and not _py_strict_options(prose),
          (_py_strict_options(opt), _py_strict_options(prose)))
    check("D (self-test) the docs scanner flags a --strict promise, not a negation or --strict-mcp-config",
          bool(_promises_strict("Run `deepmap sync --strict` to fail on DEGRADED."))
          and bool(_promises_strict("Use --strict; it is not the default."))
          and not _promises_strict("There is no `--strict` flag. Pass --strict-mcp-config to claude.")
          and not _promises_strict("The verdict's `--strict` exit is unwired (exit_code has no caller)."))

    files = _product_files(root)
    rel = lambda p: p.relative_to(root).as_posix()  # noqa: E731
    wiring = {rel(p): h for p in files["py"] if p.name != "verdict.py"
              for h in [_exit_code_wiring(p.read_text(encoding="utf-8"))] if h}
    check(f"D no build/ hooks/ benchmark/ module calls verdict.exit_code() ({len(files['py'])} files) — its strict "
          f"3 is `deepmap sync`'s \"Neo4j unreachable\"; give a strict mode its own code before wiring it",
          not wiring, wiring)
    options = {rel(p): h for p in files["py"] for h in [_py_strict_options(p.read_text(encoding="utf-8"))] if h}
    options.update({rel(p): _STRICT.findall(p.read_text(encoding="utf-8", errors="replace"))
                    for p in files["scripts"] if _STRICT.search(p.read_text(encoding="utf-8", errors="replace"))})
    check("D no --strict option in build/ hooks/ benchmark/ Python, the installers or hooks.json",
          not options, options)
    promises = {rel(p): h for p in files["docs"]
                for h in [_promises_strict(_norm(p.read_text(encoding="utf-8", errors="replace")))] if h}
    check(f"D no command, skill, README or docs/ page promises a --strict flag ({len(files['docs'])} files)",
          not promises, promises)
    cli = (root / "build" / "deepmap_cli.py").read_text(encoding="utf-8")
    check("D the collision's premise: `deepmap sync`'s exit-code help lists 3 as \"Neo4j unreachable\"",
          re.search(r'"\s*3\s+Neo4j unreachable', cli) is not None)


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    root = REPO
    if "--root" in sys.argv:
        root = Path(sys.argv[sys.argv.index("--root") + 1]).resolve()
    print("=" * 76)
    print(f"DeepMap — O6 r283: verdict.py tells the true exit contract; no false frozen-file claim (root: {root})")
    print("=" * 76)
    V = _load(root)
    gate_a_docstrings(V)
    gate_b_retrieval_claim(V, root)
    gate_c_behaviour(V)
    gate_d_drift_locks(root)
    total = len(PASSES) + len(FAILS)
    print("\n" + "=" * 76)
    if FAILS:
        print(f"RESULT: RED — {len(PASSES)}/{total} gates passed.")
        return 1
    print(f"RESULT: GREEN — {len(PASSES)}/{total} gates passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
